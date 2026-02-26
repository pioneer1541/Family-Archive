#!/usr/bin/env python3
import argparse
import glob
import json
from pathlib import Path
from typing import Any


def _load_reports(pattern: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(pattern)):
        p = Path(path)
        if not p.exists() or not p.is_file():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        rows.append(obj)
    return rows


def _top_failures(reports: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    bag: dict[str, dict[str, Any]] = {}
    for rep in reports:
        for row in list(rep.get("rows") or []):
            if not isinstance(row, dict):
                continue
            case_id = str(row.get("id") or "").strip()
            if not case_id:
                continue
            score = float((((row.get("scores") or {}).get("mixed") or {}).get("overall") or 0.0))
            item = bag.setdefault(case_id, {"case_id": case_id, "count": 0, "score_sum": 0.0, "domain": str(row.get("domain") or "")})
            item["count"] += 1
            item["score_sum"] += score
    out = []
    for item in bag.values():
        count = int(item["count"] or 0)
        out.append(
            {
                "case_id": item["case_id"],
                "domain": item["domain"],
                "count": count,
                "avg_score": round(float(item["score_sum"] or 0.0) / max(1, count), 4),
            }
        )
    out.sort(key=lambda x: (float(x.get("avg_score") or 0.0), -int(x.get("count") or 0)))
    return out[:limit]


def build_trend(reports: list[dict[str, Any]]) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for rep in reports:
        summary = rep.get("summary") if isinstance(rep, dict) else {}
        runs.append(
            {
                "run_id": str(rep.get("run_id") or ""),
                "generated_at": str(rep.get("generated_at") or ""),
                "seed": rep.get("seed"),
                "sample_size": int(rep.get("sample_size") or 0),
                "avg_total_score": float(summary.get("avg_total_score") or 0.0),
                "context_relevance_avg": float(summary.get("context_relevance_avg") or 0.0),
                "answer_faithfulness_avg": float(summary.get("answer_faithfulness_avg") or 0.0),
                "answer_relevance_avg": float(summary.get("answer_relevance_avg") or 0.0),
                "boundary_refusal_pass_rate": float(summary.get("boundary_refusal_pass_rate") or 0.0),
            }
        )
    runs.sort(key=lambda x: str(x.get("generated_at") or ""))
    return {
        "ok": True,
        "snapshot_count": len(runs),
        "runs": runs,
        "top_failures": _top_failures(reports, limit=10),
    }


def run() -> int:
    ap = argparse.ArgumentParser(description="Build trend report from agent eval reports.")
    ap.add_argument("--glob", default="evaluation/agent_eval_report*.json")
    ap.add_argument("--out", default="evaluation/agent_eval_trend.json")
    args = ap.parse_args()

    reports = _load_reports(str(args.glob))
    trend = build_trend(reports)
    out = Path(args.out)
    out.write_text(json.dumps(trend, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "snapshot_count": int(trend.get("snapshot_count") or 0)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

