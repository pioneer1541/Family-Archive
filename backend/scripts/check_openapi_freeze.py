#!/usr/bin/env python3
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app


def _canonical_json_text(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def run() -> int:
    spec_path = Path("openapi.json")
    if not spec_path.exists():
        print("openapi_frozen_spec_missing: openapi.json")
        return 1

    committed = json.loads(spec_path.read_text(encoding="utf-8"))
    generated = app.openapi()

    if committed == generated:
        print("openapi_freeze_ok")
        return 0

    committed_text = _canonical_json_text(committed).splitlines()
    generated_text = _canonical_json_text(generated).splitlines()
    diff = list(
        difflib.unified_diff(
            committed_text,
            generated_text,
            fromfile="openapi.json (committed)",
            tofile="openapi.json (generated)",
            lineterm="",
        )
    )

    print("openapi_freeze_mismatch")
    print("Run: docker exec -i fkv-api python /app/scripts/export_openapi.py")
    for line in diff[:240]:
        print(line)
    if len(diff) > 240:
        print(f"... diff_truncated: {len(diff) - 240} more lines")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
