#!/usr/bin/env python3
import argparse
import datetime as dt
import json
from pathlib import Path

from sqlalchemy import func, select

from app import crud
from app.api.routes import map_reduce_summary
from app.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus
from app.schemas import MapReduceSummaryRequest


TARGET_STATES = {"unknown", "llm_failed", "needs_regen"}


def _report_path_from_repo_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "quality_backfill_report.json"


def _preview(text: str, limit: int = 220) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "..."


def _doc_snapshot(doc: Document) -> dict[str, str]:
    return {
        "summary_quality_state": str(doc.summary_quality_state or "unknown"),
        "summary_last_error": str(doc.summary_last_error or ""),
        "title_zh": str(doc.title_zh or ""),
        "title_en": str(doc.title_en or ""),
        "category_path": str(doc.category_path or ""),
        "summary_zh_preview": _preview(str(doc.summary_zh or "")),
    }


def run_backfill(
    *,
    include_missing: bool,
    ui_lang: str,
    chunk_group_size: int,
    limit: int,
    max_chunks: int,
    output: Path,
) -> dict:
    db = SessionLocal()
    try:
        stmt = (
            select(Document)
            .where(
                Document.status == DocumentStatus.COMPLETED.value,
                Document.summary_quality_state.in_(sorted(TARGET_STATES)),
            )
            .order_by(Document.updated_at.desc())
        )
        rows = db.execute(stmt).scalars().all()
        chunk_rows = db.execute(
            select(Chunk.document_id, func.count().label("chunk_count")).group_by(Chunk.document_id)
        ).all()
        chunk_count_map = {str(doc_id): int(count or 0) for doc_id, count in chunk_rows}

        candidates: list[Document] = []
        for row in rows:
            if include_missing:
                candidates.append(row)
                continue
            if crud.source_path_available(row.source_path):
                candidates.append(row)
        candidates.sort(key=lambda item: int(chunk_count_map.get(str(item.id), 0)))
        if max_chunks > 0:
            candidates = [item for item in candidates if int(chunk_count_map.get(str(item.id), 0)) <= int(max_chunks)]
        if limit > 0:
            candidates = candidates[:limit]

        report_items: list[dict] = []
        applied_count = 0
        failed_count = 0
        for doc in candidates:
            before = _doc_snapshot(doc)
            source_available = crud.source_path_available(doc.source_path)
            item = {
                "doc_id": str(doc.id),
                "file_name": str(doc.file_name or ""),
                "source_available": bool(source_available),
                "chunk_count": int(chunk_count_map.get(str(doc.id), 0)),
                "before": before,
                "after": {},
                "applied": False,
                "reason": "",
                "quality_state": "",
                "quality_flags": [],
            }
            try:
                out = map_reduce_summary(
                    MapReduceSummaryRequest(
                        doc_id=str(doc.id),
                        ui_lang=ui_lang,
                        chunk_group_size=int(chunk_group_size),
                    ),
                    db=db,
                )
                db.refresh(doc)
                item["after"] = _doc_snapshot(doc)
                item["applied"] = bool(out.applied)
                item["reason"] = str(out.apply_reason or out.quality_state or "unknown")
                item["quality_state"] = str(out.quality_state or "")
                item["quality_flags"] = [str(x or "") for x in list(out.quality_flags or []) if str(x or "").strip()]
                if item["applied"]:
                    applied_count += 1
                else:
                    failed_count += 1
            except Exception as exc:  # pragma: no cover - defensive script branch
                db.rollback()
                item["after"] = before
                item["applied"] = False
                item["reason"] = f"exception:{type(exc).__name__}"
                item["quality_state"] = "exception"
                item["quality_flags"] = []
                failed_count += 1

            report_items.append(item)

        report = {
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "model": "qwen3:4b-instruct",
            "include_missing": bool(include_missing),
            "target_states": sorted(TARGET_STATES),
            "total_candidates": len(candidates),
            "processed": len(report_items),
            "applied": int(applied_count),
            "failed_or_skipped": int(failed_count),
            "items": report_items,
        }

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill summary quality states for unknown/failed docs.")
    parser.add_argument("--include-missing", action="store_true", help="Include source-missing docs (chunk fallback only).")
    parser.add_argument("--ui-lang", choices=["zh", "en"], default="zh")
    parser.add_argument("--chunk-group-size", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="Max documents to process; 0 means no limit.")
    parser.add_argument("--max-chunks", type=int, default=0, help="Skip docs whose chunk count exceeds this value; 0 means no cap.")
    parser.add_argument("--output", type=str, default=str(_report_path_from_repo_root()))
    args = parser.parse_args()

    report = run_backfill(
        include_missing=bool(args.include_missing),
        ui_lang=str(args.ui_lang),
        chunk_group_size=max(2, min(20, int(args.chunk_group_size))),
        limit=max(0, int(args.limit)),
        max_chunks=max(0, int(args.max_chunks)),
        output=Path(str(args.output)),
    )
    print(
        "quality_backfill_done",
        {
            "processed": report.get("processed"),
            "applied": report.get("applied"),
            "failed_or_skipped": report.get("failed_or_skipped"),
            "output": str(args.output),
        },
    )


if __name__ == "__main__":
    main()
