import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import crud, models  # noqa: F401
from app.config import get_settings
from app.db import Base, SessionLocal, ensure_sqlite_runtime_schema, engine
from app.models import Chunk, Document, DocumentStatus, MailIngestionEvent
from app.services.llm_summary import classify_category_from_summary, regenerate_friendly_name_from_summary
from app.services.map_reduce import build_map_reduce_summary
from app.services.qdrant import qdrant_payload, upsert_records
from app.services.source_tags import category_labels_for_path, infer_source_type
from app.services.tag_rules import infer_auto_tags


settings = get_settings()


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _ensure_schema() -> None:
    try:
        Base.metadata.create_all(bind=engine)
        ensure_sqlite_runtime_schema()
    except OperationalError:
        # Common when DB file is mounted read-only (for example, docker-owned volume from host).
        # Rebuild still proceeds and will report per-document write failures.
        pass


def _preview(text: str, limit: int = 220) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[:limit].rstrip() + "..."


def _mail_context_for_attachment(db, path: str) -> tuple[str, str]:
    row = (
        db.execute(
            select(MailIngestionEvent)
            .where(MailIngestionEvent.attachment_path == str(path or ""))
            .order_by(MailIngestionEvent.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return ("", "")
    return (str(row.subject or ""), str(row.from_addr or ""))


def _doc_snapshot(doc: Document) -> dict[str, Any]:
    return {
        "title_en": str(doc.title_en or ""),
        "title_zh": str(doc.title_zh or ""),
        "category_path": str(doc.category_path or ""),
        "summary_en_preview": _preview(str(doc.summary_en or "")),
        "summary_zh_preview": _preview(str(doc.summary_zh or "")),
        "summary_quality_state": str(doc.summary_quality_state or "unknown"),
        "summary_last_error": str(doc.summary_last_error or ""),
    }


def _refresh_one_doc(doc_id: str, *, ui_lang: str, chunk_group_size: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            return {"doc_id": doc_id, "ok": False, "error": "document_not_found"}
        if str(doc.status or "") != DocumentStatus.COMPLETED.value:
            return {"doc_id": doc_id, "ok": False, "error": "document_not_completed"}

        before = _doc_snapshot(doc)
        out = build_map_reduce_summary(db, doc_id=doc.id, ui_lang=ui_lang, chunk_group_size=chunk_group_size)

        doc.summary_quality_state = str(out.quality_state or "needs_regen")[:24]
        doc.summary_model = str(settings.summary_model or "")[:64]
        doc.summary_version = "prompt-v2"

        summary_en = str((out.short_summary.en if out.short_summary else "") or "").strip()
        summary_zh = str((out.short_summary.zh if out.short_summary else "") or "").strip()

        if out.quality_state == "ok" and (summary_en or summary_zh):
            doc.summary_en = summary_en[:2000]
            doc.summary_zh = summary_zh[:2000]
            doc.summary_last_error = ""
        else:
            detail = ",".join(str(x or "").strip() for x in out.quality_flags if str(x or "").strip())
            doc.summary_last_error = (detail or str(out.quality_state or "needs_regen"))[:240]

        chunks = (
            db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index.asc()).limit(20))
            .scalars()
            .all()
        )
        excerpt = "\n".join(str(item.content or "") for item in chunks)[:3200]
        source_type = infer_source_type(str(doc.source_path or ""))

        if out.quality_state == "ok":
            classified = classify_category_from_summary(
                file_name=doc.file_name,
                source_type=source_type,
                summary_en=doc.summary_en,
                summary_zh=doc.summary_zh,
                content_excerpt=excerpt,
            )
            if classified is not None:
                cat_en, cat_zh, cat_path = classified
                doc.category_label_en = str(cat_en or "")[:128]
                doc.category_label_zh = str(cat_zh or "")[:128]
                doc.category_path = str(cat_path or "")[:256]
                doc.category_version = "taxonomy-v1"
            elif (not str(doc.category_label_en or "").strip()) or (not str(doc.category_label_zh or "").strip()):
                default_en, default_zh = category_labels_for_path(doc.category_path)
                doc.category_label_en = str(default_en or "")[:128]
                doc.category_label_zh = str(default_zh or "")[:128]

            renamed = regenerate_friendly_name_from_summary(
                file_name=doc.file_name,
                category_path=doc.category_path,
                summary_en=doc.summary_en,
                summary_zh=doc.summary_zh,
                fallback_en=doc.title_en,
                fallback_zh=doc.title_zh,
            )
            if renamed is not None:
                title_en, title_zh = renamed
                doc.title_en = str(title_en or doc.title_en)[:512]
                doc.title_zh = str(title_zh or doc.title_zh)[:512]
                doc.name_version = "name-v2"

        if out.quality_state == "ok":
            mail_subject = ""
            mail_from = ""
            if source_type == "mail":
                mail_subject, mail_from = _mail_context_for_attachment(db, str(doc.source_path or ""))

            auto_tags = infer_auto_tags(
                file_name=doc.file_name,
                source_path=doc.source_path,
                source_type=source_type,
                summary_en=doc.summary_en,
                summary_zh=doc.summary_zh,
                content_excerpt=excerpt,
                category_path=doc.category_path,
                mail_from=mail_from,
                mail_subject=mail_subject,
            )
            crud.sync_auto_tags_for_document(db, document_id=doc.id, auto_tag_keys=auto_tags)
            doc_tags = crud.get_document_tag_keys(db, doc.id)
            doc.updated_at = dt.datetime.now(dt.UTC)

            payload_records = [
                qdrant_payload(
                    doc_id=doc.id,
                    chunk_id=chunk.id,
                    doc_lang=doc.doc_lang,
                    category_path=doc.category_path,
                    source_type=source_type,
                    updated_at=doc.updated_at,
                    title_en=doc.title_en,
                    title_zh=doc.title_zh,
                    tags=doc_tags,
                    text=chunk.content,
                )
                for chunk in chunks
            ]
            try:
                upsert_records(payload_records)
            except Exception:
                # qdrant failures should not block metadata rebuild.
                pass

        db.commit()
        db.refresh(doc)
        return {
            "doc_id": doc.id,
            "file_name": str(doc.file_name or ""),
            "ok": True,
            "quality_state": str(out.quality_state or ""),
            "quality_flags": list(out.quality_flags or []),
            "before": before,
            "after": _doc_snapshot(doc),
        }
    except OperationalError:
        db.rollback()
        return {"doc_id": doc_id, "ok": False, "error": "sqlite_operational_error"}
    except Exception as exc:
        db.rollback()
        return {"doc_id": doc_id, "ok": False, "error": f"{type(exc).__name__}:{exc}"[:240]}
    finally:
        db.close()


def run_full_rebuild(
    *,
    workers: int,
    chunk_group_size: int,
    ui_lang: str,
    include_non_completed: bool,
    limit: int,
) -> dict[str, Any]:
    _ensure_schema()
    started_at = _now_iso()
    db = SessionLocal()
    try:
        stmt = select(Document.id).order_by(Document.updated_at.desc())
        if not include_non_completed:
            stmt = stmt.where(Document.status == DocumentStatus.COMPLETED.value)
        if int(limit) > 0:
            stmt = stmt.limit(int(limit))
        doc_ids = [str(row[0]) for row in db.execute(stmt).all() if str(row[0] or "").strip()]
    finally:
        db.close()

    results: list[dict[str, Any]] = []
    failed_queue: list[str] = []
    if not doc_ids:
        return {
            "started_at": started_at,
            "finished_at": _now_iso(),
            "documents_total": 0,
            "ok_count": 0,
            "failed_count": 0,
            "quality_retry_queue": [],
            "items": [],
        }

    max_workers = max(1, int(workers))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        fut_map = {
            executor.submit(
                _refresh_one_doc,
                doc_id,
                ui_lang=ui_lang,
                chunk_group_size=chunk_group_size,
            ): doc_id
            for doc_id in doc_ids
        }
        for fut in as_completed(fut_map):
            doc_id = fut_map[fut]
            item = fut.result()
            results.append(item)
            if not bool(item.get("ok")):
                failed_queue.append(doc_id)
            if len(results) % 20 == 0:
                print(
                    json.dumps(
                        {
                            "phase": "progress",
                            "done": len(results),
                            "total": len(doc_ids),
                            "ok": sum(1 for x in results if x.get("ok")),
                            "failed": len(failed_queue),
                            "ts": _now_iso(),
                        },
                        ensure_ascii=False,
                    )
                )

    ok_count = sum(1 for item in results if bool(item.get("ok")))
    failed_count = len(results) - ok_count
    return {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "documents_total": len(doc_ids),
        "ok_count": ok_count,
        "failed_count": failed_count,
        "quality_retry_queue": failed_queue,
        "items": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild document summary/category/friendly-name quality for all documents.")
    parser.add_argument("--workers", type=int, default=2, help="Worker count (default: 2)")
    parser.add_argument("--chunk-group-size", type=int, default=6, help="Map-reduce section size in pages (default: 6)")
    parser.add_argument("--ui-lang", choices=["zh", "en"], default="zh", help="UI language for map-reduce source labels")
    parser.add_argument("--include-non-completed", action="store_true", help="Include non-completed documents")
    parser.add_argument("--limit", type=int, default=0, help="Optional max document count")
    parser.add_argument(
        "--output",
        default=str((Path(__file__).resolve().parents[2] / "data" / "before_after_quality_report.json")),
        help="Output JSON report path",
    )
    args = parser.parse_args()

    t0 = time.time()
    report = run_full_rebuild(
        workers=max(1, int(args.workers)),
        chunk_group_size=max(2, int(args.chunk_group_size)),
        ui_lang=str(args.ui_lang),
        include_non_completed=bool(args.include_non_completed),
        limit=max(0, int(args.limit)),
    )
    report["elapsed_sec"] = round(time.time() - t0, 3)

    out_path = Path(str(args.output)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out_path), "ok_count": report["ok_count"], "failed_count": report["failed_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
