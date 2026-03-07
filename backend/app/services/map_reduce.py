import json
import re
import time
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.models import Chunk, Document, DocumentStatus
from app.schemas import (
    BilingualText,
    MapReduceSummaryResponse,
    MapReduceSummarySection,
    ResultCardSource,
)
from app.services.llm_summary import (
    detect_summary_quality_flags,
    summarize_final_with_model,
    summarize_page_with_model,
    summarize_section_with_model,
)
from app.services.parsing import chunk_text, extract_page_chunks_from_path

settings = get_settings()
logger = get_logger(__name__)

_LONGDOC_SIGNAL_PATTERNS = [
    re.compile(r"\$\s?\d"),
    re.compile(
        r"\b(?:aud|amount|total|due|invoice|bill|kwh|usage)\b", flags=re.IGNORECASE
    ),
    re.compile(r"\b20\d{2}[/-](?:0?[1-9]|1[0-2])(?:[/-](?:0?[1-9]|[12]\d|3[01]))?\b"),
    re.compile(r"20\d{2}\s*年\s*(?:0?[1-9]|1[0-2])\s*月"),
    re.compile(
        r"\b(?:must|required|obligation|risk|deadline|action)\b", flags=re.IGNORECASE
    ),
    re.compile(r"(?:到期|截止|义务|风险|建议|行动项)"),
]


def _compact_text(text: str, limit: int = 220) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    return t[:limit].rstrip() + "..."


def _normalize_token(token: str) -> str:
    return (
        "".join(
            ch
            for ch in str(token or "")
            if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff")
        )
        .strip()
        .lower()
    )


def _extract_keywords(text: str, *, top_n: int = 6) -> list[str]:
    words: list[str] = []
    for token in str(text or "").replace("|", " ").split():
        s = _normalize_token(token)
        if len(s) < 2:
            continue
        if all(ch.isdigit() for ch in s):
            continue
        words.append(s)
    if not words:
        return []
    cnt = Counter(words)
    return [w for w, _ in cnt.most_common(max(1, int(top_n)))]


def _fallback_page_summary(page_text: str, page_index: int) -> tuple[str, str]:
    keywords = _extract_keywords(page_text, top_n=4)
    key_txt = ", ".join(keywords) if keywords else "document details"
    core = _compact_text(page_text, 260)
    en = f"Page {page_index}: Key topics {key_txt}. {core}"
    zh_keywords = "、".join(keywords[:4]) if keywords else "关键信息"
    zh = f"第{page_index}页关键信息：{zh_keywords}。{_compact_text(page_text, 220)}"
    return (en, zh)


def _fallback_section_summary(
    *,
    section_index: int,
    page_start: int,
    page_end: int,
    page_summaries_en: list[str],
    page_summaries_zh: list[str],
) -> tuple[str, str]:
    en_preview = " ".join(str(x or "") for x in page_summaries_en[:3])
    zh_preview = "；".join(str(x or "") for x in page_summaries_zh[:3])
    en = f"Section {section_index} (pages {page_start}-{page_end}): {_compact_text(en_preview, 360)}"
    zh = f"第{section_index}节（第{page_start}-{page_end}页）：{_compact_text(zh_preview, 360)}"
    return (en, zh)


def _build_semantic_chunks(
    page_chunks: list[str], *, min_tokens: int = 200, max_tokens: int = 500
) -> list[str]:
    merged = "\n\n".join(
        str(x or "").strip() for x in page_chunks if str(x or "").strip()
    )
    if not merged:
        return []

    target = max(220, min(420, int((int(min_tokens) + int(max_tokens)) / 2)))
    overlap = max(20, int(target * 0.15))
    raw = chunk_text(merged, target_tokens=target, overlap_tokens=overlap)
    if not raw:
        return []

    out: list[str] = []
    for item in raw:
        tokens = str(item or "").split()
        if not tokens:
            continue
        if len(tokens) < int(min_tokens) and out:
            out[-1] = (out[-1] + " " + " ".join(tokens)).strip()
            continue
        out.append(" ".join(tokens[: int(max_tokens)]).strip())
    return [x for x in out if x]


def _page_signal_score(text: str) -> int:
    raw = str(text or "")
    if not raw.strip():
        return 0
    score = 0
    for pattern in _LONGDOC_SIGNAL_PATTERNS:
        if pattern.search(raw):
            score += 2
    token_count = len(raw.split())
    if token_count >= 180:
        score += 1
    if token_count >= 320:
        score += 1
    return score


def _select_page_indices(page_chunks: list[str], *, hard_limit: int) -> list[int]:
    total = len(page_chunks)
    if total <= hard_limit:
        return list(range(total))
    safe_limit = max(4, int(hard_limit))

    selected: set[int] = {0, total - 1}
    scored = [(idx, _page_signal_score(page_chunks[idx])) for idx in range(total)]
    scored.sort(key=lambda item: (-int(item[1]), int(item[0])))

    for idx, _score in scored:
        if len(selected) >= safe_limit:
            break
        selected.add(int(idx))

    if len(selected) < safe_limit:
        step = max(1.0, float(total - 1) / float(max(1, safe_limit - 1)))
        for i in range(safe_limit):
            idx = min(total - 1, max(0, int(round(i * step))))
            selected.add(idx)
            if len(selected) >= safe_limit:
                break

    return sorted(selected)[:safe_limit]


def _rows_to_page_chunks(rows: list[Chunk]) -> list[str]:
    merged = "\n".join(
        str(r.content or "") for r in rows if str(r.content or "").strip()
    )
    if not merged:
        return []
    return chunk_text(merged, target_tokens=420, overlap_tokens=40)


def _build_sources(
    doc: Document, rows: list[Chunk], *, ui_lang: str, cap: int = 10
) -> list[ResultCardSource]:
    if not rows:
        return []
    label = (doc.title_zh if ui_lang == "zh" else doc.title_en) or doc.file_name
    idxs: list[int] = [0, len(rows) - 1]
    step = max(1, len(rows) // max(1, int(cap) - 2))
    for i in range(step, len(rows), step):
        idxs.append(i)
    seen: set[str] = set()
    out: list[ResultCardSource] = []
    for i in sorted(set(idxs)):
        row = rows[max(0, min(i, len(rows) - 1))]
        if row.id in seen:
            continue
        seen.add(row.id)
        out.append(ResultCardSource(doc_id=doc.id, chunk_id=row.id, label=label))
        if len(out) >= int(cap):
            break
    return out


def build_map_reduce_summary(
    db: Session, doc_id: str, ui_lang: str = "zh", chunk_group_size: int = 6
) -> MapReduceSummaryResponse:
    t0 = time.time()
    doc = db.get(Document, doc_id)
    if doc is None:
        raise ValueError("document_not_found")
    if doc.status != DocumentStatus.COMPLETED.value:
        raise ValueError("document_not_ready")

    rows = (
        db.execute(
            select(Chunk)
            .where(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index.asc())
        )
        .scalars()
        .all()
    )
    if not rows:
        raise ValueError("document_has_no_chunks")

    page_chunks: list[str] = []
    try:
        if str(doc.source_path or "").strip():
            page_chunks = extract_page_chunks_from_path(
                doc.source_path, max_pages=260, db=db
            )
    except Exception:
        page_chunks = []
    if not page_chunks:
        page_chunks = _rows_to_page_chunks(rows)
    if not page_chunks:
        raise ValueError("document_has_no_pages")

    pages_total = len(page_chunks)
    hard_limit = max(20, int(settings.longdoc_page_hard_limit or 180))
    sample_trigger = max(20, int(settings.longdoc_sample_trigger_pages or 120))
    longdoc_mode = "normal"
    if pages_total > hard_limit:
        longdoc_mode = "sampled"
        keep_indices = _select_page_indices(page_chunks, hard_limit=hard_limit)
        page_chunks = [page_chunks[idx] for idx in keep_indices]
    elif pages_total > sample_trigger:
        longdoc_mode = "sampled"
    pages_used = len(page_chunks)

    page_summaries_en: list[str] = []
    page_summaries_zh: list[str] = []
    # Use immutable file_name as the primary title signal to avoid self-reinforcing
    # loops from previously misclassified friendly names.
    title = str(doc.file_name or doc.title_en or doc.title_zh or "")
    total_pages = len(page_chunks)
    page_fallback_used = False
    _CHECKPOINT_INTERVAL = 10  # persist progress every N pages
    doc.mapreduce_job_status = f"pages_0/{total_pages}"
    for idx, page_text in enumerate(page_chunks, start=1):
        model_out = summarize_page_with_model(
            page_text=page_text,
            page_index=idx,
            total_pages=total_pages,
            title=title,
            db=db,
        )
        if model_out is None:
            model_out = _fallback_page_summary(page_text, idx)
            page_fallback_used = True
        page_summaries_en.append(str(model_out[0] or "").strip())
        page_summaries_zh.append(str(model_out[1] or "").strip())
        # Checkpoint: persist completed page summaries to DB every N pages so a
        # mid-flight HTTP timeout does not lose all prior work.
        if idx % _CHECKPOINT_INTERVAL == 0 or idx == total_pages:
            try:
                doc.mapreduce_page_summaries_json = json.dumps(
                    {"en": page_summaries_en, "zh": page_summaries_zh},
                    ensure_ascii=False,
                )
                doc.mapreduce_job_status = f"pages_{idx}/{total_pages}"
                db.commit()
            except Exception:
                db.rollback()

    # Section chunks: 5-10 pages per section as required by long-document spec.
    section_size = max(5, min(10, int(chunk_group_size or 6)))
    sections: list[MapReduceSummarySection] = []
    section_summaries_en: list[str] = []
    section_summaries_zh: list[str] = []
    section_fallback_used = False
    for start in range(0, total_pages, section_size):
        end = min(total_pages, start + section_size)
        sec_index = len(sections) + 1
        sec_page_start = start + 1
        sec_page_end = end
        sec_en_rows = page_summaries_en[start:end]
        sec_zh_rows = page_summaries_zh[start:end]
        sec_model = summarize_section_with_model(
            section_index=sec_index,
            page_start=sec_page_start,
            page_end=sec_page_end,
            page_summaries_en=sec_en_rows,
            page_summaries_zh=sec_zh_rows,
            title=title,
            db=db,
        )
        if sec_model is None:
            sec_model = _fallback_section_summary(
                section_index=sec_index,
                page_start=sec_page_start,
                page_end=sec_page_end,
                page_summaries_en=sec_en_rows,
                page_summaries_zh=sec_zh_rows,
            )
            section_fallback_used = True
        sec_en = str(sec_model[0] or "").strip()
        sec_zh = str(sec_model[1] or "").strip()
        section_summaries_en.append(sec_en)
        section_summaries_zh.append(sec_zh)
        # Checkpoint after each section
        try:
            doc.mapreduce_section_summaries_json = json.dumps(
                {"en": section_summaries_en, "zh": section_summaries_zh},
                ensure_ascii=False,
            )
            doc.mapreduce_job_status = f"sections_{len(section_summaries_en)}/{(total_pages + section_size - 1) // section_size}"
            db.commit()
        except Exception:
            db.rollback()
        sections.append(
            MapReduceSummarySection(
                index=sec_index,
                chunk_range=f"{sec_page_start}-{sec_page_end}",
                summary=BilingualText(en=sec_en, zh=sec_zh),
            )
        )

    # Semantic chunks: 200-500 tokens.
    semantic_chunks = _build_semantic_chunks(
        page_chunks, min_tokens=200, max_tokens=500
    )
    if not semantic_chunks:
        semantic_chunks = _build_semantic_chunks(
            _rows_to_page_chunks(rows), min_tokens=200, max_tokens=500
        )

    final_section_cap = max(4, int(settings.longdoc_final_section_max or 18))
    final_semantic_cap = max(2, int(settings.longdoc_final_semantic_max or 6))
    final_sections_en = section_summaries_en[:final_section_cap]
    final_sections_zh = section_summaries_zh[:final_section_cap]
    final_semantics = semantic_chunks[:final_semantic_cap]

    final_model = summarize_final_with_model(
        title=title,
        section_summaries_en=final_sections_en,
        section_summaries_zh=final_sections_zh,
        semantic_chunks=final_semantics,
        db=db,
    )
    quality_flags: list[str] = []
    final_fallback_used = False
    if final_model is None:
        final_fallback_used = True
        quality_flags.append("final_model_failed")
        short_en = _compact_text(" ".join(section_summaries_en[:3]), 620)
        short_zh = _compact_text("；".join(section_summaries_zh[:3]), 620)
    else:
        short_en = str(final_model[0] or "").strip()
        short_zh = str(final_model[1] or "").strip()
        quality_flags.extend(detect_summary_quality_flags(short_en, short_zh))

    sources = _build_sources(doc, rows, ui_lang=ui_lang, cap=10)
    latency_ms = int((time.time() - t0) * 1000)
    if page_fallback_used:
        quality_flags.append("page_fallback_used")
    if section_fallback_used:
        quality_flags.append("section_fallback_used")
    if final_fallback_used:
        quality_flags.append("final_fallback_used")

    seen_flags: set[str] = set()
    dedup_quality_flags: list[str] = []
    for flag in quality_flags:
        key = str(flag or "").strip()
        if (not key) or (key in seen_flags):
            continue
        seen_flags.add(key)
        dedup_quality_flags.append(key)

    if final_fallback_used:
        quality_state = "llm_failed"
    elif any(
        flag in {"empty_summary", "contains_process_terms", "missing_entity_signals"}
        for flag in dedup_quality_flags
    ):
        quality_state = "needs_regen"
    else:
        quality_state = "ok"

    # Mark job as completed in DB
    try:
        doc.mapreduce_job_status = "completed"
        db.commit()
    except Exception:
        db.rollback()

    semantic_count = len(final_semantics) if final_semantics else len(rows)
    out = MapReduceSummaryResponse(
        doc_id=doc.id,
        status="completed",
        short_summary=BilingualText(en=short_en, zh=short_zh),
        sections=sections,
        sources=sources,
        total_chunks=semantic_count,
        used_chunks=semantic_count,
        latency_ms=latency_ms,
        quality_state=quality_state,
        fallback_used=bool(
            page_fallback_used or section_fallback_used or final_fallback_used
        ),
        quality_flags=dedup_quality_flags,
        longdoc_mode=longdoc_mode,
        pages_total=pages_total,
        pages_used=pages_used,
    )
    logger.info(
        "map_reduce_longdoc_summary",
        extra=sanitize_log_context(
            {
                "doc_id": str(doc.id),
                "pages_total": int(pages_total),
                "pages_used": int(pages_used),
                "longdoc_mode": str(longdoc_mode),
                "quality_state": str(quality_state),
                "fallback_used": bool(out.fallback_used),
            }
        ),
    )
    return out
