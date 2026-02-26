#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal
from app.services.governance import build_category_debt_snapshot


def _default_output_dir() -> Path:
    return (ROOT_DIR.parent / "data").resolve()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate category debt snapshot report.")
    parser.add_argument("--output-dir", type=str, default=str(_default_output_dir()), help="Output directory for snapshot files.")
    parser.add_argument("--top", type=int, default=20, help="Top legacy files to include.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = Path(str(args.output_dir)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    date_key = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
    file_daily = output_dir / f"category_debt_snapshot_{date_key}.json"
    file_latest = output_dir / "category_debt_latest.json"

    db = SessionLocal()
    try:
        snapshot = build_category_debt_snapshot(db, top_limit=max(1, min(100, int(args.top))))
    finally:
        db.close()

    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    file_daily.write_text(text, encoding="utf-8")
    file_latest.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "daily": str(file_daily),
                "latest": str(file_latest),
                "prod_legacy_docs": int(((snapshot.get("scope_prod") or {}).get("legacy_docs") or 0)),
                "audit_legacy_docs": int(((snapshot.get("scope_audit") or {}).get("legacy_docs") or 0)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
