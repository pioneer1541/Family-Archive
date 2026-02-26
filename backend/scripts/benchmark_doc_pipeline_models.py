import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal  # noqa: E402
from app.models import Chunk, Document  # noqa: E402
from app.services import llm_summary as llm_mod  # noqa: E402
from app.services.llm_summary import (  # noqa: E402
    classify_category_from_summary,
    detect_summary_quality_flags,
    prompt_snapshot,
    regenerate_friendly_name_from_summary,
    summarize_document_with_model,
)
from app.services.source_tags import infer_source_type, is_leaf_category_path  # noqa: E402


DATE_PATTERNS = [
    re.compile(r"\b20\d{2}[/-](?:0?[1-9]|1[0-2])(?:[/-](?:0?[1-9]|[12]\d|3[01]))?\b"),
    re.compile(r"\b(?:0?[1-9]|1[0-2])[/-]20\d{2}\b"),
    re.compile(r"20\d{2}\s*年\s*(?:0?[1-9]|1[0-2])\s*月"),
]
AMOUNT_PATTERN = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")

PROCESS_BAD_TERMS = [
    "ingestion",
    "pipeline",
    "chunk",
    "map-reduce",
    "section-level",
    "source_type",
    "已完成文档入库",
    "分块",
    "语义分块",
]

TOPIC_RULES: dict[str, list[str]] = {
    "electricity": ["electricity", "energy", "kwh", "电费", "用电"],
    "water": ["water bill", "yarra valley water", "water usage", "水费", "用水"],
    "gas": ["gas", "燃气", "煤气"],
    "internet": ["internet", "broadband", "nbn", "网络", "宽带"],
    "meeting": ["meeting", "agm", "会议", "通知"],
    "warranty": ["warranty", "保修", "质保"],
}

TOPIC_CATEGORY_MAP = {
    "electricity": "finance/bills/electricity",
    "water": "finance/bills/water",
    "gas": "finance/bills/gas",
    "internet": "finance/bills/internet",
}

TOPIC_NAME_TOKENS = {
    "electricity": {"zh": ["电费", "电力"], "en": ["Electricity"]},
    "water": {"zh": ["水费"], "en": ["Water"]},
    "gas": {"zh": ["燃气", "煤气"], "en": ["Gas"]},
    "internet": {"zh": ["网络", "宽带"], "en": ["Internet", "Broadband"]},
    "meeting": {"zh": ["会议", "通知"], "en": ["Meeting", "Notice", "AGM"]},
    "warranty": {"zh": ["保修", "质保"], "en": ["Warranty"]},
}


@dataclass
class DocPayload:
    doc_id: str
    file_name: str
    source_path: str
    title_en: str
    title_zh: str
    category_path: str
    category_label_en: str
    category_label_zh: str
    source_text: str
    content_excerpt: str
    source_type: str


def _extract_dates(text: str) -> list[str]:
    out: list[str] = []
    raw = str(text or "")
    for pat in DATE_PATTERNS:
        for m in pat.findall(raw):
            value = "".join(m) if isinstance(m, tuple) else str(m)
            value = value.strip()
            if value and value not in out:
                out.append(value)
            if len(out) >= 8:
                return out
    return out


def _extract_amounts(text: str) -> list[str]:
    out: list[str] = []
    for m in AMOUNT_PATTERN.findall(str(text or "")):
        value = " ".join(str(m).split()).strip()
        if value and value not in out:
            out.append(value)
        if len(out) >= 8:
            break
    return out


def _detect_topic(text: str) -> str:
    raw = str(text or "").lower()
    best_topic = ""
    best_score = 0
    for topic, keys in TOPIC_RULES.items():
        score = 0
        for key in keys:
            if str(key).lower() in raw:
                score += 1
        if score > best_score:
            best_score = score
            best_topic = topic
    return best_topic if best_score > 0 else ""


def _contains_any(text: str, keywords: list[str]) -> bool:
    raw = str(text or "").lower()
    for kw in keywords:
        if str(kw).lower() in raw:
            return True
    return False


def _summary_quality(summary_en: str, summary_zh: str, source_text: str) -> dict[str, Any]:
    flags = detect_summary_quality_flags(summary_en, summary_zh)
    src_dates = _extract_dates(source_text)
    src_amounts = _extract_amounts(source_text)
    out = (str(summary_en or "") + " " + str(summary_zh or "")).strip()
    date_hits = sum(1 for d in src_dates if d in out)
    amount_hits = sum(1 for a in src_amounts if a in out)
    zh_chars = sum(1 for ch in str(summary_zh or "") if "\u4e00" <= ch <= "\u9fff")

    score = 10.0
    if "empty_summary" in flags:
        score -= 6.0
    if "contains_process_terms" in flags:
        score -= 4.0
    if "missing_entity_signals" in flags:
        score -= 2.0
    if "too_short" in flags:
        score -= 1.0
    score += min(1.5, float(date_hits) * 0.8)
    score += min(1.5, float(amount_hits) * 1.0)
    if zh_chars >= 60:
        score += 1.0
    score = max(0.0, min(10.0, score))

    return {
        "score": round(score, 2),
        "flags": flags,
        "date_hits": int(date_hits),
        "date_total": int(len(src_dates)),
        "amount_hits": int(amount_hits),
        "amount_total": int(len(src_amounts)),
        "zh_chars": int(zh_chars),
    }


def _category_quality(category_path: str, summary_en: str, summary_zh: str, source_text: str) -> dict[str, Any]:
    path = str(category_path or "").strip().lower()
    merged = f"{summary_en}\n{summary_zh}\n{source_text[:1600]}"
    topic = _detect_topic(merged)
    expected = TOPIC_CATEGORY_MAP.get(topic, "")
    is_leaf = bool(path and is_leaf_category_path(path))

    score = 0.0
    if path:
        score += 2.0
    if is_leaf:
        score += 3.0
    if path and path != "archive/misc":
        score += 1.0
    if expected:
        if path == expected:
            score += 4.0
        elif path.startswith(expected.rsplit("/", 1)[0] + "/"):
            score += 2.0
        else:
            score -= 1.0
    else:
        score += 2.0
    score = max(0.0, min(10.0, score))

    return {
        "score": round(score, 2),
        "is_leaf": bool(is_leaf),
        "topic_detected": topic,
        "expected_category": expected,
        "category_path": path,
    }


def _name_quality(name_en: str, name_zh: str, category_path: str, summary_en: str, summary_zh: str, source_text: str) -> dict[str, Any]:
    en = str(name_en or "").strip()
    zh = str(name_zh or "").strip()
    merged_name = f"{en} {zh}"
    merged_text = f"{summary_en}\n{summary_zh}\n{source_text[:1600]}"
    topic = _detect_topic(merged_text)
    tokens = TOPIC_NAME_TOKENS.get(topic, {"zh": [], "en": []})

    has_bad_terms = _contains_any(merged_name, PROCESS_BAD_TERMS)
    dates = _extract_dates(source_text)
    has_year = any((d[:4] in merged_name) for d in dates if len(d) >= 4)
    has_topic_token = _contains_any(zh, list(tokens.get("zh", []))) or _contains_any(en, list(tokens.get("en", [])))
    category_ok = True
    expected = TOPIC_CATEGORY_MAP.get(topic, "")
    if expected:
        category_ok = str(category_path or "").strip().lower().startswith(expected.rsplit("/", 1)[0] + "/")

    score = 0.0
    if en:
        score += 2.0
    if zh:
        score += 2.0
    if len(en) <= 80 and len(zh) <= 80:
        score += 1.0
    if has_year:
        score += 2.0
    if has_topic_token:
        score += 2.0
    if category_ok:
        score += 1.0
    if has_bad_terms:
        score -= 3.0
    score = max(0.0, min(10.0, score))

    return {
        "score": round(score, 2),
        "has_bad_terms": bool(has_bad_terms),
        "has_year": bool(has_year),
        "topic_detected": topic,
        "has_topic_token": bool(has_topic_token),
    }


def _overall_quality(summary_score: float, category_score: float, name_score: float) -> float:
    score = (float(summary_score) * 0.5) + (float(category_score) * 0.25) + (float(name_score) * 0.25)
    return round(max(0.0, min(10.0, score)), 2)


def _load_doc(file_name: str) -> DocPayload:
    db = SessionLocal()
    try:
        doc = (
            db.execute(
                select(Document)
                .where(Document.file_name == str(file_name or "").strip(), Document.status == "completed")
                .order_by(Document.updated_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if doc is None:
            raise RuntimeError(f"document_not_found: {file_name}")

        chunks = db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc())).scalars().all()
        text = "\n".join(str(c.content or "") for c in chunks)
        excerpt = "\n".join(str(c.content or "") for c in chunks[:20])[:3200]
        return DocPayload(
            doc_id=str(doc.id),
            file_name=str(doc.file_name),
            source_path=str(doc.source_path or ""),
            title_en=str(doc.title_en or ""),
            title_zh=str(doc.title_zh or ""),
            category_path=str(doc.category_path or ""),
            category_label_en=str(doc.category_label_en or ""),
            category_label_zh=str(doc.category_label_zh or ""),
            source_text=text[:9000],
            content_excerpt=excerpt,
            source_type=infer_source_type(str(doc.source_path or "")),
        )
    finally:
        db.close()


def _latency_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg_ms": 0.0, "p95_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        p95 = sorted_vals[0]
    else:
        idx = int(round((len(sorted_vals) - 1) * 0.95))
        p95 = sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]
    return {
        "avg_ms": round(statistics.mean(sorted_vals), 1),
        "p95_ms": round(float(p95), 1),
        "min_ms": round(float(sorted_vals[0]), 1),
        "max_ms": round(float(sorted_vals[-1]), 1),
    }


def run_pipeline_benchmark(file_name: str, models: list[str], warmup_rounds: int, measure_rounds: int) -> dict[str, Any]:
    payload = _load_doc(file_name)
    prompt_meta = prompt_snapshot()

    report: dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "file_name": payload.file_name,
        "doc_id": payload.doc_id,
        "source_type": payload.source_type,
        "source_text_chars_used": len(payload.source_text),
        "source_dates": _extract_dates(payload.source_text),
        "source_amounts": _extract_amounts(payload.source_text),
        "prompt_version": prompt_meta.get("version"),
        "prompt_hash": prompt_meta.get("hash"),
        "models": {},
    }

    orig_summary_model = llm_mod.settings.summary_model
    orig_category_model = llm_mod.settings.category_model
    orig_name_model = llm_mod.settings.friendly_name_model
    try:
        for model_name in models:
            llm_mod.settings.summary_model = model_name
            llm_mod.settings.category_model = model_name
            llm_mod.settings.friendly_name_model = model_name

            for _ in range(max(0, int(warmup_rounds))):
                s = summarize_document_with_model(
                    text=payload.source_text,
                    title_en=payload.title_en,
                    title_zh=payload.title_zh,
                    category_label_en=payload.category_label_en,
                    category_label_zh=payload.category_label_zh,
                )
                if s is None:
                    continue
                classify_category_from_summary(
                    file_name=payload.file_name,
                    source_type=payload.source_type,
                    summary_en=str(s[0] or ""),
                    summary_zh=str(s[1] or ""),
                    content_excerpt=payload.content_excerpt,
                )
                regenerate_friendly_name_from_summary(
                    file_name=payload.file_name,
                    category_path=payload.category_path,
                    summary_en=str(s[0] or ""),
                    summary_zh=str(s[1] or ""),
                    fallback_en=payload.title_en,
                    fallback_zh=payload.title_zh,
                )

            runs: list[dict[str, Any]] = []
            summary_latencies: list[float] = []
            category_latencies: list[float] = []
            name_latencies: list[float] = []
            total_latencies: list[float] = []
            overall_scores: list[float] = []

            for idx in range(max(1, int(measure_rounds))):
                t_all0 = time.perf_counter()

                t0 = time.perf_counter()
                summary_out = summarize_document_with_model(
                    text=payload.source_text,
                    title_en=payload.title_en,
                    title_zh=payload.title_zh,
                    category_label_en=payload.category_label_en,
                    category_label_zh=payload.category_label_zh,
                )
                summary_ms = (time.perf_counter() - t0) * 1000.0
                summary_latencies.append(summary_ms)
                summary_en = str((summary_out[0] if summary_out else "") or "")
                summary_zh = str((summary_out[1] if summary_out else "") or "")

                t1 = time.perf_counter()
                classified = classify_category_from_summary(
                    file_name=payload.file_name,
                    source_type=payload.source_type,
                    summary_en=summary_en,
                    summary_zh=summary_zh,
                    content_excerpt=payload.content_excerpt,
                )
                category_ms = (time.perf_counter() - t1) * 1000.0
                category_latencies.append(category_ms)
                if classified is None:
                    category_en, category_zh, category_path = ("", "", "")
                else:
                    category_en, category_zh, category_path = classified

                t2 = time.perf_counter()
                renamed = regenerate_friendly_name_from_summary(
                    file_name=payload.file_name,
                    category_path=(category_path or payload.category_path),
                    summary_en=summary_en,
                    summary_zh=summary_zh,
                    fallback_en=payload.title_en,
                    fallback_zh=payload.title_zh,
                )
                name_ms = (time.perf_counter() - t2) * 1000.0
                name_latencies.append(name_ms)
                if renamed is None:
                    name_en, name_zh = ("", "")
                else:
                    name_en, name_zh = renamed

                total_ms = (time.perf_counter() - t_all0) * 1000.0
                total_latencies.append(total_ms)

                summary_quality = _summary_quality(summary_en, summary_zh, payload.source_text)
                category_quality = _category_quality(
                    category_path=(category_path or payload.category_path),
                    summary_en=summary_en,
                    summary_zh=summary_zh,
                    source_text=payload.source_text,
                )
                name_quality = _name_quality(
                    name_en=name_en,
                    name_zh=name_zh,
                    category_path=(category_path or payload.category_path),
                    summary_en=summary_en,
                    summary_zh=summary_zh,
                    source_text=payload.source_text,
                )
                overall = _overall_quality(summary_quality["score"], category_quality["score"], name_quality["score"])
                overall_scores.append(overall)

                runs.append(
                    {
                        "round": idx + 1,
                        "latency_ms": {
                            "summary": round(summary_ms, 1),
                            "category": round(category_ms, 1),
                            "friendly_name": round(name_ms, 1),
                            "total": round(total_ms, 1),
                        },
                        "outputs": {
                            "summary_en": summary_en,
                            "summary_zh": summary_zh,
                            "category_en": category_en,
                            "category_zh": category_zh,
                            "category_path": category_path or payload.category_path,
                            "friendly_name_en": name_en,
                            "friendly_name_zh": name_zh,
                        },
                        "quality": {
                            "summary": summary_quality,
                            "category": category_quality,
                            "friendly_name": name_quality,
                            "overall_score": overall,
                        },
                    }
                )

            representative = sorted(runs, key=lambda r: float((r["quality"] or {}).get("overall_score", 0.0)), reverse=True)[0]
            report["models"][model_name] = {
                "stage_latency_ms": {
                    "summary": _latency_stats(summary_latencies),
                    "category": _latency_stats(category_latencies),
                    "friendly_name": _latency_stats(name_latencies),
                    "total": _latency_stats(total_latencies),
                },
                "quality_scores": {
                    "overall_avg": round(statistics.mean(overall_scores), 2) if overall_scores else 0.0,
                    "summary_avg": round(statistics.mean(float(r["quality"]["summary"]["score"]) for r in runs), 2) if runs else 0.0,
                    "category_avg": round(statistics.mean(float(r["quality"]["category"]["score"]) for r in runs), 2) if runs else 0.0,
                    "friendly_name_avg": round(statistics.mean(float(r["quality"]["friendly_name"]["score"]) for r in runs), 2)
                    if runs
                    else 0.0,
                },
                "representative_run": representative,
                "runs": runs,
            }
    finally:
        llm_mod.settings.summary_model = orig_summary_model
        llm_mod.settings.category_model = orig_category_model
        llm_mod.settings.friendly_name_model = orig_name_model

    model_keys = list(report["models"].keys())
    if len(model_keys) >= 2:
        sorted_by_quality = sorted(
            model_keys,
            key=lambda k: float(report["models"][k]["quality_scores"]["overall_avg"]),
            reverse=True,
        )
        sorted_by_speed = sorted(
            model_keys,
            key=lambda k: float(report["models"][k]["stage_latency_ms"]["total"]["avg_ms"]),
        )
        report["comparison"] = {
            "best_quality_model": sorted_by_quality[0],
            "fastest_model": sorted_by_speed[0],
            "quality_ranking": sorted_by_quality,
            "speed_ranking": sorted_by_speed,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark single document generation quality/performance across summary/category/friendly-name stages."
    )
    parser.add_argument("--file", default="Owners Handover Guide - Anderson Creek Townhomes Stage 2 .pdf")
    parser.add_argument("--models", nargs="+", default=["qwen3:4b-instruct"])
    parser.add_argument("--warmup-rounds", type=int, default=1)
    parser.add_argument("--measure-rounds", type=int, default=2)
    parser.add_argument(
        "--out",
        default=str((Path(__file__).resolve().parents[2] / "data" / "single_doc_pipeline_benchmark_report.json")),
    )
    args = parser.parse_args()

    report = run_pipeline_benchmark(
        file_name=str(args.file),
        models=[str(m) for m in args.models],
        warmup_rounds=max(0, int(args.warmup_rounds)),
        measure_rounds=max(1, int(args.measure_rounds)),
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    out_path = Path(str(args.out)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
