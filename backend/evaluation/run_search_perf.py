#!/usr/bin/env python3
import argparse
import json
import math
import time
from pathlib import Path

import requests


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int(math.ceil((p / 100.0) * len(ordered)) - 1)
    idx = max(0, min(idx, len(ordered) - 1))
    return int(ordered[idx])


def run() -> int:
    ap = argparse.ArgumentParser(description="Search performance baseline evaluation")
    ap.add_argument("--api", default="http://127.0.0.1:18180")
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--ui-lang", default="en", choices=["zh", "en"])
    ap.add_argument("--query-lang", default="en", choices=["zh", "en", "auto"])
    ap.add_argument("--p95-target-ms", type=int, default=1500)
    ap.add_argument("--availability-target", type=float, default=0.99)
    ap.add_argument(
        "--queries",
        default="electricity,maintenance,milestone,queue,bill",
        help="Comma-separated query set.",
    )
    ap.add_argument("--out", default="evaluation/search_perf_report.json")
    args = ap.parse_args()

    api = args.api.rstrip("/")
    queries = [q.strip() for q in str(args.queries).split(",") if q.strip()]
    if not queries:
        raise SystemExit("no_queries")
    if args.runs <= 0:
        raise SystemExit("runs_must_be_positive")

    session = requests.Session()
    payload_base = {
        "top_k": int(args.top_k),
        "score_threshold": 0.0,
        "ui_lang": str(args.ui_lang),
        "query_lang": str(args.query_lang),
    }

    # Warmup to avoid first-call effects in p95 numbers.
    for i in range(max(0, int(args.warmup))):
        query = queries[i % len(queries)]
        payload = dict(payload_base)
        payload["query"] = query
        try:
            session.post(api + "/v1/search", json=payload, timeout=25)
        except Exception:
            pass

    rows = []
    success_count = 0
    latencies_ok: list[int] = []

    for i in range(int(args.runs)):
        query = queries[i % len(queries)]
        payload = dict(payload_base)
        payload["query"] = query

        t0 = time.time()
        status_code = 0
        hit_count = 0
        error = ""
        try:
            r = session.post(api + "/v1/search", json=payload, timeout=25)
            status_code = int(r.status_code)
            if status_code == 200:
                out = r.json()
                hits = out.get("hits") if isinstance(out, dict) else []
                hit_count = len(hits) if isinstance(hits, list) else 0
                success_count += 1
            else:
                error = f"http_{status_code}"
        except Exception as exc:
            error = str(exc)[:120]
        latency_ms = int((time.time() - t0) * 1000)

        if status_code == 200:
            latencies_ok.append(latency_ms)

        rows.append(
            {
                "index": i + 1,
                "query": query,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "hit_count": hit_count,
                "error": error,
            }
        )

    total = int(args.runs)
    availability = (success_count / total) if total else 0.0
    p50 = percentile(latencies_ok, 50.0)
    p95 = percentile(latencies_ok, 95.0)

    summary = {
        "total": total,
        "success_count": success_count,
        "availability": round(availability, 4),
        "p50_ms": p50,
        "p95_ms": p95,
        "p95_target_ms": int(args.p95_target_ms),
        "availability_target": float(args.availability_target),
        "p95_pass": p95 <= int(args.p95_target_ms),
        "availability_pass": availability >= float(args.availability_target),
    }
    summary["slo_pass"] = bool(summary["p95_pass"] and summary["availability_pass"])

    report = {
        "ok": True,
        "api": args.api,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "runs": int(args.runs),
            "warmup": int(args.warmup),
            "top_k": int(args.top_k),
            "ui_lang": str(args.ui_lang),
            "query_lang": str(args.query_lang),
            "queries": queries,
        },
        "summary": summary,
        "rows": rows,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("search_perf_eval")
    print("out:", args.out)
    print("summary:", json.dumps(summary, ensure_ascii=False))
    return 0 if summary["slo_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(run())
