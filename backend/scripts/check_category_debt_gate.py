#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal
from app.services.governance import build_category_debt_snapshot


def main() -> int:
    db = SessionLocal()
    try:
        snapshot = build_category_debt_snapshot(db, top_limit=20)
    finally:
        db.close()

    prod_legacy = int(((snapshot.get("scope_prod") or {}).get("legacy_docs") or 0))
    payload = {
        "prod_legacy_docs": prod_legacy,
        "audit_legacy_docs": int(((snapshot.get("scope_audit") or {}).get("legacy_docs") or 0)),
    }
    print(json.dumps(payload, ensure_ascii=False))
    if prod_legacy > 0:
        top = snapshot.get("top_legacy_files") or []
        for row in top[:10]:
            print(
                f"legacy_in_completed: doc_id={row.get('doc_id')} file_name={row.get('file_name')} category={row.get('category_path')}",
                file=sys.stderr,
            )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
