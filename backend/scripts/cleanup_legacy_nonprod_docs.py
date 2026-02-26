#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal
from app.models import Document, DocumentStatus
from app.services.governance import LEGACY_CATEGORY_PATHS


def _default_output() -> Path:
    return (ROOT_DIR.parent / "data" / "legacy_nonprod_cleanup_report.json").resolve()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup legacy category debt for non-production statuses only.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Dry run only (default).")
    group.add_argument("--apply", action="store_true", help="Apply cleanup actions.")
    parser.add_argument("--days", type=int, default=30, help="Only process records older than N days.")
    parser.add_argument("--output", type=str, default=str(_default_output()), help="Output report path.")
    return parser.parse_args()


def _append_error_code(current: str | None, extra: str) -> str:
    code = str(extra or "").strip()
    prev = str(current or "").strip()
    if not code:
        return prev
    if not prev:
        return code[:120]
    parts = [item.strip() for item in prev.split(",") if item.strip()]
    if code not in parts:
        parts.append(code)
    return ",".join(parts)[:120]


def run_cleanup(*, apply: bool, days: int, output: Path) -> dict[str, object]:
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=max(1, int(days)))
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(Document)
                .where(
                    Document.status.in_([DocumentStatus.FAILED.value, DocumentStatus.DUPLICATE.value]),
                    Document.updated_at < cutoff,
                )
                .order_by(Document.updated_at.asc())
            )
            .scalars()
            .all()
        )
        candidates = [doc for doc in rows if str(doc.category_path or "").strip().lower() in LEGACY_CATEGORY_PATHS]

        deleted_duplicate = 0
        flagged_failed = 0
        skipped = 0
        items: list[dict[str, object]] = []

        for doc in candidates:
            item = {
                "doc_id": str(doc.id),
                "file_name": str(doc.file_name or ""),
                "status": str(doc.status or ""),
                "category_path": str(doc.category_path or ""),
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else "",
                "action": "",
            }
            if str(doc.status) == DocumentStatus.DUPLICATE.value:
                item["action"] = "delete_duplicate"
                if apply:
                    db.delete(doc)
                deleted_duplicate += 1
                items.append(item)
                continue

            if str(doc.status) == DocumentStatus.FAILED.value:
                item["action"] = "flag_failed_cleanup_candidate"
                if apply:
                    doc.error_code = _append_error_code(doc.error_code, "legacy_cleanup_candidate")
                    doc.updated_at = now
                flagged_failed += 1
                items.append(item)
                continue

            skipped += 1
            item["action"] = "skip"
            items.append(item)

        if apply:
            db.commit()
        else:
            db.rollback()

        report = {
            "started_at": now.isoformat(),
            "finished_at": dt.datetime.now(dt.UTC).isoformat(),
            "apply": bool(apply),
            "days": int(days),
            "cutoff": cutoff.isoformat(),
            "candidate_count": len(candidates),
            "deleted_duplicate": int(deleted_duplicate),
            "flagged_failed": int(flagged_failed),
            "skipped": int(skipped),
            "items": items,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        db.close()


def main() -> int:
    args = _parse_args()
    apply = bool(args.apply)
    report = run_cleanup(
        apply=apply,
        days=max(1, int(args.days)),
        output=Path(str(args.output)).resolve(),
    )
    print(
        json.dumps(
            {
                "apply": report.get("apply"),
                "candidate_count": report.get("candidate_count"),
                "deleted_duplicate": report.get("deleted_duplicate"),
                "flagged_failed": report.get("flagged_failed"),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
