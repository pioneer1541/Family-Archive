#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def _default_output() -> Path:
    return (ROOT_DIR.parent / "data" / "source_availability_cache_backfill_report.json").resolve()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill documents.source_available_cached from filesystem checks.")
    parser.add_argument("--apply", action="store_true", help="Apply updates. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of documents to scan (0 = all).")
    parser.add_argument("--status", type=str, default="", help="Optional status filter (e.g. completed).")
    parser.add_argument("--output", type=str, default=str(_default_output()), help="Path to report JSON.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_path = Path(str(args.output)).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        query = db.query(models.Document)
        status = str(args.status or "").strip()
        if status:
            query = query.filter(models.Document.status == status)
        query = query.order_by(models.Document.updated_at.desc())
        if int(args.limit or 0) > 0:
            query = query.limit(int(args.limit))
        rows = query.all()

        changed = 0
        unchanged = 0
        missing_path = 0
        samples: list[dict] = []
        for doc in rows:
            source_path = str(doc.source_path or "").strip()
            if not source_path:
                missing_path += 1
                actual = False
            else:
                actual = os.path.isfile(source_path)
            before = bool(getattr(doc, "source_available_cached", True))
            if before != actual:
                changed += 1
                if args.apply:
                    doc.source_available_cached = bool(actual)
                    # source_checked_at is optional in older DBs; set only when column exists on mapped model.
                    if hasattr(doc, "source_checked_at"):
                        import datetime as dt

                        doc.source_checked_at = dt.datetime.now(dt.UTC)
                if len(samples) < 50:
                    samples.append(
                        {
                            "doc_id": str(doc.id),
                            "file_name": str(doc.file_name or ""),
                            "status": str(doc.status or ""),
                            "before": before,
                            "after": bool(actual),
                        }
                    )
            else:
                unchanged += 1

        if args.apply and changed:
            db.commit()

        report = {
            "mode": "apply" if args.apply else "dry_run",
            "status_filter": status,
            "scanned": len(rows),
            "changed": changed,
            "unchanged": unchanged,
            "missing_source_path": missing_path,
            "sample_changes": samples,
        }
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output_path), **{k: report[k] for k in ("mode", "scanned", "changed")}}, ensure_ascii=False))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

