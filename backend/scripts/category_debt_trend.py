#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.governance import compute_debt_trend, load_snapshots_from_dir


def _default_data_dir() -> Path:
    return (ROOT_DIR.parent / "data").resolve()


def _default_output() -> Path:
    return (_default_data_dir() / "category_debt_trend_latest.json").resolve()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build category debt trend report from snapshot files.")
    parser.add_argument("--days", type=int, default=30, help="Lookback days.")
    parser.add_argument("--data-dir", type=str, default=str(_default_data_dir()), help="Snapshot directory.")
    parser.add_argument("--output", type=str, default=str(_default_output()), help="Trend output path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    data_dir = Path(str(args.data_dir)).resolve()
    output = Path(str(args.output)).resolve()
    snapshots = load_snapshots_from_dir(data_dir=data_dir, days=max(1, min(365, int(args.days))))
    trend = compute_debt_trend(snapshots)
    report = {
        "days": int(args.days),
        "snapshot_count": len(snapshots),
        "week_over_week_change": int(trend.get("week_over_week_change") or 0),
        "points": list(trend.get("points") or []),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "snapshot_count": report["snapshot_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
