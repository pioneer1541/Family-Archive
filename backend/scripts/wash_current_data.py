import argparse
import datetime as dt
import os
from dataclasses import dataclass

from sqlalchemy import delete, select

from app import crud
from app.config import get_settings
from app.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus
from app.services.ingestion import process_ingestion_job
from app.services.source_tags import infer_source_type


settings = get_settings()


def _real(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.realpath(raw)
    except Exception:
        return ""


def _is_within(path: str, root: str) -> bool:
    p = _real(path)
    r = _real(root)
    if (not p) or (not r):
        return False
    try:
        return os.path.commonpath([p, r]) == r
    except Exception:
        return p == r or p.startswith(r.rstrip("/\\") + os.sep)


def _ext(value: str) -> str:
    return str(value or "").strip().lower().lstrip(".")


def _set(values: list[str]) -> set[str]:
    return {_ext(x) for x in values if _ext(x)}


@dataclass
class WashResult:
    scanned: int = 0
    passed_rules: int = 0
    reprocessed_ok: int = 0
    reprocessed_failed: int = 0
    marked_failed: int = 0
    skipped_missing_source: int = 0


def _photo_too_large(file_ext: str, file_size: int, photo_exts: set[str], cap_mb: int) -> bool:
    if cap_mb <= 0:
        return False
    if _ext(file_ext) not in photo_exts:
        return False
    return int(file_size or 0) > int(cap_mb) * 1024 * 1024


def _mark_failed(doc: Document, *, code: str, db) -> None:
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    doc.status = DocumentStatus.FAILED.value
    doc.error_code = str(code or "rule_filtered")[:120]
    doc.updated_at = dt.datetime.now(dt.UTC)
    db.commit()


def _rule_error(doc: Document, *, nas_root: str, allowed_file: set[str], allowed_nas: set[str], allowed_mail: set[str], photo_exts: set[str], photo_cap_mb: int) -> str:
    src_path = str(doc.source_path or "")
    src_type = infer_source_type(src_path)
    ext = _ext(doc.file_ext)

    if _photo_too_large(ext, int(doc.file_size or 0), photo_exts, photo_cap_mb):
        return "photo_too_large"

    if src_type == "nas":
        if not _is_within(src_path, nas_root):
            return "nas_root_violation"
        if ext not in allowed_nas:
            return "nas_ext_blocked"
        return ""

    if src_type == "mail":
        if ext not in allowed_mail:
            return "mail_ext_blocked"
        return ""

    if ext not in allowed_file:
        return "file_ext_blocked"
    return ""


def run_wash(*, only_completed: bool, enforce_fail: bool, real_sources_only: bool = False) -> WashResult:
    db = SessionLocal()
    result = WashResult()
    try:
        nas_root = str(settings.nas_default_source_dir or "").strip()
        allowed_file = _set(settings.ingestion_allowed_extensions)
        allowed_nas = _set(settings.nas_allowed_extensions)
        allowed_mail = _set(settings.mail_allowed_extensions)
        photo_exts = _set(settings.photo_file_extensions)
        photo_cap_mb = int(settings.photo_max_size_mb or 0)

        stmt = select(Document).order_by(Document.updated_at.desc())
        if only_completed:
            stmt = stmt.where(Document.status == DocumentStatus.COMPLETED.value)
        docs = db.execute(stmt).scalars().all()
        result.scanned = len(docs)
        print(
            {
                "phase": "start",
                "documents": len(docs),
                "only_completed": only_completed,
                "enforce_fail": enforce_fail,
                "real_sources_only": bool(real_sources_only),
                "nas_root": nas_root,
            }
        )

        for idx, doc in enumerate(docs, start=1):
            code = _rule_error(
                doc,
                nas_root=nas_root,
                allowed_file=allowed_file,
                allowed_nas=allowed_nas,
                allowed_mail=allowed_mail,
                photo_exts=photo_exts,
                photo_cap_mb=photo_cap_mb,
            )
            if (not code) and bool(real_sources_only):
                src_type = infer_source_type(str(doc.source_path or ""))
                if src_type not in {"nas", "mail"}:
                    code = "source_not_allowed"
            if code:
                if enforce_fail:
                    _mark_failed(doc, code=code, db=db)
                    result.marked_failed += 1
                if idx % 20 == 0:
                    print({"phase": "scan", "index": idx, "marked_failed": result.marked_failed, "reprocessed_ok": result.reprocessed_ok})
                continue

            result.passed_rules += 1
            src_path = str(doc.source_path or "").strip()
            if (not src_path) or (not os.path.exists(src_path)):
                if enforce_fail:
                    _mark_failed(doc, code="source_missing", db=db)
                    result.marked_failed += 1
                else:
                    result.skipped_missing_source += 1
                continue

            # Re-run ingestion with current rules into the same document row.
            job = crud.create_ingestion_job(db, [src_path])
            out = process_ingestion_job(job.id, force_reprocess=True, reprocess_doc_id=doc.id)
            if bool(out.get("ok")) and str(out.get("status") or "") == "completed":
                result.reprocessed_ok += 1
            else:
                result.reprocessed_failed += 1

            if idx % 10 == 0:
                print(
                    {
                        "phase": "reprocess",
                        "index": idx,
                        "passed_rules": result.passed_rules,
                        "reprocessed_ok": result.reprocessed_ok,
                        "reprocessed_failed": result.reprocessed_failed,
                        "skipped_missing_source": result.skipped_missing_source,
                    }
                )

        print({"phase": "done", **result.__dict__})
        return result
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Wash current documents with latest ingestion rules and reprocess.")
    parser.add_argument("--all-status", action="store_true", help="Process all documents, not only completed.")
    parser.add_argument("--enforce-fail", action="store_true", help="Mark rule-violating documents as failed and drop chunks.")
    parser.add_argument(
        "--real-sources-only",
        action="store_true",
        help="Only keep NAS and mail source documents (mark all other source types as rule violations).",
    )
    args = parser.parse_args()

    run_wash(
        only_completed=not bool(args.all_status),
        enforce_fail=bool(args.enforce_fail),
        real_sources_only=bool(args.real_sources_only),
    )


if __name__ == "__main__":
    main()
