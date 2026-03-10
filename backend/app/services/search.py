import datetime as dt
import re
import time
from typing import Any

import requests
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import crud
from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.models import Chunk, Document, DocumentStatus
from app.runtime_config import get_runtime_setting
from app.schemas import SearchHit, SearchRequest, SearchResponse
from app.services.qdrant import search_records
from app.services.source_tags import infer_source_type

settings = get_settings()
logger = get_logger(__name__)
_translation_cache: dict[str, tuple[float, str]] = {}
_TRANSLATION_CACHE_TTL_SEC = 3600.0
_TRANSLATION_CACHE_MAX = 512


def _is_zh(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _translate_query_to_en(query: str, db: Session | None = None) -> str:
    if not query or (not _is_zh(query)):
        return ""

    cache_key = str(query).strip()
    now = time.time()
    hit = _translation_cache.get(cache_key)
    if hit and (now - float(hit[0]) <= _TRANSLATION_CACHE_TTL_SEC):
        logger.debug(
            "search_translate_cache_hit",
            extra=sanitize_log_context({"query_len": len(cache_key)}),
        )
        return str(hit[1] or "")

    try:
        url = settings.ollama_base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": get_runtime_setting("planner_model", db),
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": "Translate Chinese search query into concise English keywords only.",
                },
                {"role": "user", "content": query},
            ],
            "options": {"temperature": 0.0},
        }
        r = requests.post(url, json=payload, timeout=6)
        r.raise_for_status()
        data = r.json() if hasattr(r, "json") else {}
        msg = data.get("message") if isinstance(data, dict) else {}
        out = str((msg or {}).get("content") or "").strip()
        translated = out[:120]
        _translation_cache[cache_key] = (now, translated)
        if len(_translation_cache) > _TRANSLATION_CACHE_MAX:
            oldest_key = min(_translation_cache, key=lambda k: _translation_cache[k][0])
            _translation_cache.pop(oldest_key, None)
        logger.debug(
            "search_translate_cache_store",
            extra=sanitize_log_context({"query_len": len(cache_key), "translated_len": len(translated)}),
        )
        return translated
    except Exception as exc:
        logger.warning(
            "search_translate_failed",
            extra=sanitize_log_context(
                {
                    "status": "warn",
                    "error_code": "search_translate_failed",
                    "detail": str(exc),
                    "query_len": len(cache_key),
                }
            ),
        )
        return ""


def _simple_score(text: str, query: str) -> tuple[float, str]:
    if not query:
        return (0.0, "")
    t = text.lower()
    q = query.lower()
    idx = t.find(q)
    if idx < 0:
        tokens = [token for token in q.split() if token.strip()]
        matches = sum(1 for token in tokens if token in t)
        if matches <= 0:
            return (0.0, "")
        score = min(0.95, 0.2 + 0.15 * matches)
        return (score, text[:220])

    count = t.count(q)
    score = min(0.99, 0.3 + 0.1 * count)
    left = max(0, idx - 90)
    right = min(len(text), idx + len(query) + 130)
    snippet = text[left:right].strip()
    return (score, snippet)


def _lexical_candidate_terms(query: str) -> list[str]:
    raw = str(query or "").strip()
    if not raw:
        return []
    if _is_zh(raw):
        return [raw[:120]]
    tokens = [t.strip() for t in re.split(r"[^a-zA-Z0-9]+", raw.lower()) if t.strip()]
    tokens = [t for t in tokens if len(t) >= 3]
    # Prefer longer tokens to reduce broad LIKE scans.
    seen: set[str] = set()
    ordered: list[str] = []
    for token in sorted(tokens, key=len, reverse=True):
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
        if len(ordered) >= 5:
            break
    if not ordered:
        return [raw[:120]]
    return ordered


def _parse_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        return dt.datetime.now(dt.UTC)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.UTC)
        return parsed
    except Exception:
        return dt.datetime.now(dt.UTC)


def _search_vector_query(
    query: str,
    category_path: str | None,
    score_threshold: float,
    top_k: int,
    db: Session | None = None,
) -> list[dict[str, Any]]:
    try:
        hits = search_records(
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            category_path=category_path,
            db=db,
        )
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for hit in hits:
        row = dict(hit)
        row["matched_query"] = query
        row["updated_at"] = _parse_datetime(hit.get("updated_at"))
        out.append(row)
    return out


def _filter_hits_by_allowed_docs(hits: list[dict[str, Any]], allowed_doc_ids: set[str] | None) -> list[dict[str, Any]]:
    if not hits:
        return []
    if allowed_doc_ids is None:
        return hits
    if not allowed_doc_ids:
        return []
    return [item for item in hits if str(item.get("doc_id") or "").strip() in allowed_doc_ids]


def _search_lexical_query(
    db: Session,
    query: str,
    category_path: str | None,
    score_threshold: float,
    allowed_doc_ids: set[str] | None,
) -> list[dict[str, Any]]:
    stmt = (
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.status == DocumentStatus.COMPLETED.value)
    )
    if category_path:
        stmt = stmt.where(Document.category_path == category_path)
    if allowed_doc_ids is not None:
        if not allowed_doc_ids:
            return []
        stmt = stmt.where(Document.id.in_(allowed_doc_ids))

    terms = _lexical_candidate_terms(query)
    if not terms:
        return []

    like_clauses = []
    for term in terms:
        pattern = f"%{term}%"
        like_clauses.append(Chunk.content.ilike(pattern))
        like_clauses.append(Document.title_en.ilike(pattern))
        like_clauses.append(Document.title_zh.ilike(pattern))
        like_clauses.append(Document.summary_en.ilike(pattern))
        like_clauses.append(Document.summary_zh.ilike(pattern))
        like_clauses.append(Document.file_name.ilike(pattern))
    candidate_limit = max(int(settings.lexical_candidate_limit or 1500), 50)
    stmt = (
        stmt.where(or_(*like_clauses))
        .order_by(Document.updated_at.desc(), Chunk.chunk_index.asc())
        .limit(candidate_limit)
    )

    rows = db.execute(stmt).all()

    hits: list[dict[str, Any]] = []
    for chunk, doc in rows:
        score, snippet = _simple_score(chunk.content, query)
        if score < score_threshold:
            continue
        hits.append(
            {
                "doc_id": doc.id,
                "chunk_id": chunk.id,
                "score": score,
                "text_snippet": snippet,
                "matched_query": query,
                "doc_lang": doc.doc_lang,
                "title_en": doc.title_en,
                "title_zh": doc.title_zh,
                "category_path": doc.category_path,
                "source_type": infer_source_type(doc.source_path),
                "updated_at": doc.updated_at,
            }
        )
    hits.sort(key=lambda x: float(x["score"]), reverse=True)
    return hits


def _merge_hits(hits_a: list[dict[str, Any]], hits_b: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    by_chunk: dict[str, dict[str, Any]] = {}
    for item in hits_a + hits_b:
        cid = str(item.get("chunk_id") or "")
        if not cid:
            continue
        current = by_chunk.get(cid)
        if current is None or float(item.get("score") or 0.0) > float(current.get("score") or 0.0):
            by_chunk[cid] = item

    out = list(by_chunk.values())
    out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return out[:top_k]


def search_documents(db: Session, req: SearchRequest) -> SearchResponse:
    query = req.query.strip()
    query_en = ""
    has_scope_filters = bool(str(req.category_path or "").strip() or (req.tags_all or []) or (req.tags_any or []))
    allowed_doc_ids: set[str] | None = None
    needs_allowed_filter = has_scope_filters or (not bool(req.include_missing))
    if needs_allowed_filter:
        allowed_doc_ids = crud.select_document_ids_for_filters(
            db,
            status=DocumentStatus.COMPLETED.value,
            category_path=req.category_path,
            tags_all=req.tags_all,
            tags_any=req.tags_any,
            include_missing=bool(req.include_missing),
        )
        if not allowed_doc_ids:
            return SearchResponse(query=query, query_en="", bilingual=False, hits=[])

    if req.query_lang == "zh" or (req.query_lang == "auto" and _is_zh(query)):
        query_en = _translate_query_to_en(query, db)

    vector_hits_a = _search_vector_query(
        query=query,
        category_path=req.category_path,
        score_threshold=req.score_threshold,
        top_k=req.top_k,
        db=db,
    )
    vector_hits_b = []
    if query_en and query_en.lower() != query.lower():
        vector_hits_b = _search_vector_query(
            query=query_en,
            category_path=req.category_path,
            score_threshold=req.score_threshold,
            top_k=req.top_k,
            db=db,
        )

    merged_vector = _merge_hits(vector_hits_a, vector_hits_b, req.top_k)
    merged_vector = _filter_hits_by_allowed_docs(merged_vector, allowed_doc_ids)

    lexical_hits_a: list[dict[str, Any]] = []
    lexical_hits_b: list[dict[str, Any]] = []
    if len(merged_vector) < req.top_k:
        lexical_hits_a = _search_lexical_query(
            db,
            query=query,
            category_path=req.category_path,
            score_threshold=req.score_threshold,
            allowed_doc_ids=allowed_doc_ids,
        )
        if query_en and query_en.lower() != query.lower():
            lexical_hits_b = _search_lexical_query(
                db,
                query=query_en,
                category_path=req.category_path,
                score_threshold=req.score_threshold,
                allowed_doc_ids=allowed_doc_ids,
            )

    merged_lexical = _merge_hits(lexical_hits_a, lexical_hits_b, req.top_k)
    merged = _merge_hits(merged_vector, merged_lexical, req.top_k)
    bilingual = bool(query_en and (vector_hits_b or lexical_hits_b))
    vector_hit_count = len(merged_vector)
    lexical_hit_count = len(merged_lexical)
    qdrant_used = bool(vector_hits_a or vector_hits_b)
    retrieval_mode = "none"
    if vector_hit_count > 0 and lexical_hit_count > 0:
        retrieval_mode = "hybrid"
    elif vector_hit_count > 0:
        retrieval_mode = "vector_only"
    elif lexical_hit_count > 0:
        retrieval_mode = "lexical_fallback"
    tag_map = crud.get_document_tags_map(db, [str(item.get("doc_id") or "") for item in merged])

    results = [
        SearchHit(
            doc_id=str(item["doc_id"]),
            chunk_id=str(item["chunk_id"]),
            score=float(item["score"]),
            text_snippet=str(item["text_snippet"]),
            matched_query=str(item["matched_query"]),
            doc_lang=str(item["doc_lang"]),
            title_en=str(item["title_en"]),
            title_zh=str(item["title_zh"]),
            category_path=str(item["category_path"]),
            source_type=str(item["source_type"]),
            updated_at=item["updated_at"] if isinstance(item["updated_at"], dt.datetime) else dt.datetime.now(dt.UTC),
            tags=tag_map.get(str(item["doc_id"]), []),
        )
        for item in merged
    ]

    return SearchResponse(
        query=query,
        query_en=query_en,
        bilingual=bilingual,
        hits=results,
        qdrant_used=qdrant_used,
        retrieval_mode=retrieval_mode,
        vector_hit_count=vector_hit_count,
        lexical_hit_count=lexical_hit_count,
    )
