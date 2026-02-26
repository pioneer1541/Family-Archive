#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.db import SessionLocal
from app.models import Chunk, Document, DocumentStatus
from app.services.qdrant import delete_records_by_point_ids, stable_point_id


settings = get_settings()


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _report_path_from_repo_root() -> Path:
    return (ROOT_DIR.parent / "data" / "inline_mail_cleanup_report.json").resolve()


def _mail_root() -> str:
    raw = str(settings.mail_attachment_root or "").strip()
    return os.path.realpath(raw) if raw else ""


def _name_pattern() -> re.Pattern[str]:
    raw = str(settings.mail_inline_name_patterns or "").strip() or r"image\d{3,4}|logo|signature|smime"
    try:
        return re.compile(raw, flags=re.IGNORECASE)
    except Exception:
        return re.compile(r"image\d{3,4}|logo|signature|smime", flags=re.IGNORECASE)


def _doc_is_mail_image(doc: Document) -> bool:
    root = _mail_root()
    source = os.path.realpath(str(doc.source_path or "").strip()) if str(doc.source_path or "").strip() else ""
    if (not root) or (not source):
        return False
    try:
        if os.path.commonpath([source, root]) != root:
            return False
    except Exception:
        if not source.startswith(root):
            return False

    ext = str(doc.file_ext or "").strip().lower().lstrip(".")
    if ext not in {str(x).strip().lower().lstrip(".") for x in settings.photo_file_extensions}:
        return False

    file_name = str(doc.file_name or "").strip()
    pattern = _name_pattern()
    return bool(pattern.search(file_name))


def cleanup_inline_mail_images(*, apply: bool, days: int, output: Path) -> dict[str, Any]:
    started = _now_iso()
    db = SessionLocal()
    try:
        stmt = select(Document).where(Document.status == DocumentStatus.COMPLETED.value)
        if int(days) > 0:
            threshold = dt.datetime.now(dt.UTC) - dt.timedelta(days=int(days))
            stmt = stmt.where(Document.updated_at >= threshold)
        docs = db.execute(stmt.order_by(Document.updated_at.desc())).scalars().all()
        candidates = [doc for doc in docs if _doc_is_mail_image(doc)]

        items: list[dict[str, Any]] = []
        deleted_docs = 0
        deleted_files = 0
        deleted_points = 0

        for doc in candidates:
            rows = db.execute(select(Chunk.id).where(Chunk.document_id == doc.id)).scalars().all()
            chunk_ids = [str(item or "").strip() for item in rows if str(item or "").strip()]
            point_ids = [stable_point_id(str(doc.id), chunk_id) for chunk_id in chunk_ids]
            source_path = str(doc.source_path or "").strip()

            item = {
                "doc_id": str(doc.id),
                "file_name": str(doc.file_name or ""),
                "source_path": source_path,
                "chunk_count": len(chunk_ids),
                "point_count": len(point_ids),
                "applied": bool(apply),
                "db_deleted": False,
                "file_deleted": False,
                "points_deleted": 0,
                "errors": [],
            }
            if apply:
                try:
                    out = delete_records_by_point_ids(point_ids, wait=True)
                    item["points_deleted"] = int(out.get("deleted") or 0)
                    deleted_points += int(item["points_deleted"])
                except Exception as exc:  # pragma: no cover - defensive
                    item["errors"].append(f"qdrant:{type(exc).__name__}")
                try:
                    db.delete(doc)
                    item["db_deleted"] = True
                    deleted_docs += 1
                except Exception as exc:  # pragma: no cover - defensive
                    item["errors"].append(f"db:{type(exc).__name__}")
                if source_path:
                    try:
                        if os.path.isfile(source_path):
                            os.remove(source_path)
                            item["file_deleted"] = True
                            deleted_files += 1
                    except Exception as exc:  # pragma: no cover - defensive
                        item["errors"].append(f"file:{type(exc).__name__}")
            items.append(item)

        if apply:
            db.commit()
        else:
            db.rollback()

        report = {
            "started_at": started,
            "finished_at": _now_iso(),
            "apply": bool(apply),
            "days": int(days),
            "mail_attachment_root": _mail_root(),
            "candidate_count": len(candidates),
            "deleted_docs": int(deleted_docs),
            "deleted_files": int(deleted_files),
            "deleted_points": int(deleted_points),
            "items": items,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        db.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup inline image attachments imported from mail.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Dry run only (default).")
    group.add_argument("--apply", action="store_true", help="Apply deletion.")
    parser.add_argument("--days", type=int, default=0, help="Only process docs updated within N days; 0 means all.")
    parser.add_argument("--output", type=str, default=str(_report_path_from_repo_root()), help="Output report path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    apply = bool(args.apply)
    report = cleanup_inline_mail_images(
        apply=apply,
        days=max(0, int(args.days)),
        output=Path(str(args.output)).resolve(),
    )
    print(
        json.dumps(
            {
                "apply": report.get("apply"),
                "candidate_count": report.get("candidate_count"),
                "deleted_docs": report.get("deleted_docs"),
                "deleted_files": report.get("deleted_files"),
                "deleted_points": report.get("deleted_points"),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
