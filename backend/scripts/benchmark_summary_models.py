import argparse
import json
import re
import statistics
import time
from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, Document
from app.services import llm_summary as llm_mod
from app.services.llm_summary import summarize_document_with_model


DEFAULT_FILES = [
    "Owners Handover Guide - Anderson Creek Townhomes Stage 2 .pdf",
    "19bf7f8fd32c7bc7_Lot_41_FeeNotice202602andAttachment.pdf",
]

DATE_PATTERNS = [
    re.compile(r"\b20\d{2}[/-](?:0?[1-9]|1[0-2])(?:[/-](?:0?[1-9]|[12]\d|3[01]))?\b"),
    re.compile(r"\b(?:0?[1-9]|1[0-2])[/-]20\d{2}\b"),
    re.compile(r"20\d{2}\s*年\s*(?:0?[1-9]|1[0-2])\s*月"),
]
AMOUNT_PATTERN = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
TOPIC_TERMS = [
    "warranty",
    "handover",
    "guide",
    "fee notice",
    "invoice",
    "bill",
    "lot",
    "strata",
    "maintenance",
    "保修",
    "交接",
    "指南",
    "费用",
    "账单",
    "发票",
    "物业",
    "维护",
]
BAD_TERMS = ["已完成文档入库", "分块", "source_type", "ingestion", "queue", "处理状态"]


def extract_dates(text: str) -> list[str]:
    out: list[str] = []
    for pat in DATE_PATTERNS:
        for m in pat.findall(text or ""):
            v = "".join(m) if isinstance(m, tuple) else str(m)
            v = v.strip()
            if v and v not in out:
                out.append(v)
            if len(out) >= 8:
                return out
    return out


def extract_amounts(text: str) -> list[str]:
    out: list[str] = []
    for m in AMOUNT_PATTERN.findall(text or ""):
        v = " ".join(str(m).split())
        if v and v not in out:
            out.append(v)
        if len(out) >= 8:
            break
    return out


def zh_char_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def quality_score(summary_en: str, summary_zh: str, source_text: str) -> dict[str, float | int]:
    src = source_text or ""
    out = (summary_en or "") + " " + (summary_zh or "")
    dates = extract_dates(src)
    amounts = extract_amounts(src)

    date_hits = sum(1 for d in dates if d in out)
    amount_hits = sum(1 for a in amounts if a in out)

    out_lower = out.lower()
    topic_hits = 0
    for term in TOPIC_TERMS:
        if term in out or term in out_lower:
            topic_hits += 1

    bad_hits = sum(1 for b in BAD_TERMS if b in out)

    zh_len = zh_char_count(summary_zh or "")
    en_len = len(re.findall(r"[A-Za-z]", summary_en or ""))

    score = 0.0
    score += min(3.0, date_hits * 1.0)
    score += min(3.0, amount_hits * 1.5)
    score += min(2.0, topic_hits * 0.5)
    if zh_len >= en_len * 0.5:
        score += 1.0
    score -= bad_hits * 1.0
    score = max(0.0, min(10.0, score))

    return {
        "score": round(score, 2),
        "date_hits": int(date_hits),
        "date_total": int(len(dates)),
        "amount_hits": int(amount_hits),
        "amount_total": int(len(amounts)),
        "topic_hits": int(topic_hits),
        "bad_hits": int(bad_hits),
        "zh_chars": int(zh_len),
        "en_alpha_chars": int(en_len),
    }


def load_doc_payload(db: SessionLocal, file_name: str) -> dict | None:
    doc = (
        db.execute(
            select(Document)
            .where(Document.file_name == file_name, Document.status == "completed")
            .order_by(Document.updated_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if doc is None:
        return None
    chunks = (
        db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc()))
        .scalars()
        .all()
    )
    text = "\n".join(str(c.content or "") for c in chunks)
    return {
        "doc_id": doc.id,
        "file_name": doc.file_name,
        "title_en": doc.title_en,
        "title_zh": doc.title_zh,
        "category_label_en": doc.category_label_en,
        "category_label_zh": doc.category_label_zh,
        "text": text,
    }


def run_benchmark(files: list[str], models: list[str], warmup_rounds: int, measure_rounds: int, timeout_sec: int) -> dict:
    report: dict = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "test_plan": {
            "models": models,
            "warmup_rounds": int(warmup_rounds),
            "measure_rounds": int(measure_rounds),
            "summary_timeout_sec": int(timeout_sec),
            "notes": "same prompt/pipeline as summarize_document_with_model; text source is concatenated chunk content capped at 9000 chars",
        },
        "results": [],
    }

    db = SessionLocal()
    orig_model = llm_mod.settings.summary_model
    orig_timeout = llm_mod.settings.summary_timeout_sec
    llm_mod.settings.summary_timeout_sec = int(timeout_sec)
    try:
        for file_name in files:
            payload = load_doc_payload(db, file_name)
            if not payload:
                report["results"].append({"file_name": file_name, "error": "not_found_or_not_completed"})
                continue

            source_text = str(payload["text"] or "")[:9000]
            file_result: dict = {
                "file_name": file_name,
                "doc_id": payload["doc_id"],
                "source_text_chars_used": len(source_text),
                "source_dates": extract_dates(source_text),
                "source_amounts": extract_amounts(source_text),
                "models": {},
            }

            for model_name in models:
                llm_mod.settings.summary_model = model_name
                for _ in range(int(warmup_rounds)):
                    summarize_document_with_model(
                        text=source_text,
                        title_en=payload["title_en"],
                        title_zh=payload["title_zh"],
                        category_label_en=payload["category_label_en"],
                        category_label_zh=payload["category_label_zh"],
                    )

                runs: list[dict] = []
                latencies_ms: list[float] = []
                for _ in range(int(measure_rounds)):
                    t0 = time.perf_counter()
                    out = summarize_document_with_model(
                        text=source_text,
                        title_en=payload["title_en"],
                        title_zh=payload["title_zh"],
                        category_label_en=payload["category_label_en"],
                        category_label_zh=payload["category_label_zh"],
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    latencies_ms.append(elapsed_ms)
                    if out is None:
                        summary_en, summary_zh = "", ""
                    else:
                        summary_en, summary_zh = out
                    runs.append(
                        {
                            "latency_ms": round(elapsed_ms, 1),
                            "summary_en": summary_en,
                            "summary_zh": summary_zh,
                            "quality": quality_score(summary_en, summary_zh, source_text),
                        }
                    )

                avg_latency = statistics.mean(latencies_ms) if latencies_ms else 0.0
                p95_latency = max(latencies_ms) if len(latencies_ms) < 20 else statistics.quantiles(latencies_ms, n=20)[18]
                avg_quality = statistics.mean([float(r["quality"]["score"]) for r in runs]) if runs else 0.0
                best_run = (
                    sorted(runs, key=lambda r: (float(r["quality"]["score"]), -float(r["latency_ms"])), reverse=True)[0]
                    if runs
                    else {"summary_en": "", "summary_zh": "", "quality": {"score": 0.0}}
                )

                file_result["models"][model_name] = {
                    "avg_latency_ms": round(avg_latency, 1),
                    "p95_latency_ms": round(p95_latency, 1),
                    "avg_quality_score": round(avg_quality, 2),
                    "runs": runs,
                    "representative_summary": {
                        "summary_en": best_run.get("summary_en", ""),
                        "summary_zh": best_run.get("summary_zh", ""),
                        "quality": best_run.get("quality", {}),
                    },
                }

            report["results"].append(file_result)
    finally:
        llm_mod.settings.summary_model = orig_model
        llm_mod.settings.summary_timeout_sec = orig_timeout
        db.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark summary quality/latency across models for selected documents.")
    parser.add_argument("--files", nargs="+", default=DEFAULT_FILES, help="Target file names in documents table.")
    parser.add_argument("--models", nargs="+", default=["qwen3:1.7b", "qwen3:4b-instruct"], help="Summary models to compare.")
    parser.add_argument("--warmup-rounds", type=int, default=1)
    parser.add_argument("--measure-rounds", type=int, default=2)
    parser.add_argument("--timeout-sec", type=int, default=90)
    parser.add_argument("--out", default="", help="Optional output json path.")
    args = parser.parse_args()

    report = run_benchmark(
        files=list(args.files),
        models=list(args.models),
        warmup_rounds=int(args.warmup_rounds),
        measure_rounds=int(args.measure_rounds),
        timeout_sec=int(args.timeout_sec),
    )

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if str(args.out or "").strip():
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
    print(payload)


if __name__ == "__main__":
    main()
