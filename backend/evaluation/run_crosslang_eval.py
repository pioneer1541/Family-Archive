#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

import requests


def run() -> int:
    ap = argparse.ArgumentParser(description="Cross-language retrieval baseline evaluation")
    ap.add_argument("--api", default="http://127.0.0.1:18180")
    ap.add_argument("--cases", default="evaluation/crosslang_cases.json")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--pass-target", type=float, default=0.85)
    ap.add_argument("--out", default="evaluation/crosslang_eval_report.json")
    args = ap.parse_args()

    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise SystemExit("invalid_cases_file")

    api = args.api.rstrip("/")
    session = requests.Session()
    rows = []
    hit_pass = 0
    translation_ready = 0
    cross_pass = 0

    for case in cases:
        query = str((case or {}).get("query") or "").strip()
        if not query:
            continue
        expected_min_hits = max(1, int((case or {}).get("expected_min_hits") or 1))

        payload = {
            "query": query,
            "top_k": int(args.top_k),
            "score_threshold": 0.0,
            "ui_lang": "zh",
            "query_lang": "zh",
        }

        t0 = time.time()
        status_code = 0
        hit_count = 0
        query_en = ""
        error = ""
        try:
            r = session.post(api + "/v1/search", json=payload, timeout=30)
            status_code = int(r.status_code)
            if status_code == 200:
                out = r.json()
                hits = out.get("hits") if isinstance(out, dict) else []
                hit_count = len(hits) if isinstance(hits, list) else 0
                query_en = str((out or {}).get("query_en") or "").strip()
            else:
                error = f"http_{status_code}"
        except Exception as exc:
            error = str(exc)[:120]
        latency_ms = int((time.time() - t0) * 1000)

        hit_ok = status_code == 200 and hit_count >= expected_min_hits
        translated_ok = bool(query_en)
        cross_ok = hit_ok and translated_ok
        if hit_ok:
            hit_pass += 1
        if translated_ok:
            translation_ready += 1
        if cross_ok:
            cross_pass += 1

        rows.append(
            {
                "query": query,
                "expected_min_hits": expected_min_hits,
                "status_code": status_code,
                "hit_count": hit_count,
                "query_en": query_en,
                "translated": translated_ok,
                "hit_ok": hit_ok,
                "cross_ok": cross_ok,
                "latency_ms": latency_ms,
                "error": error,
            }
        )

    total = len(rows)
    hit_pass_rate = (hit_pass / total) if total else 0.0
    translation_ready_rate = (translation_ready / total) if total else 0.0
    pass_rate = (cross_pass / total) if total else 0.0
    target = float(args.pass_target)

    summary = {
        "total": total,
        "hit_pass": hit_pass,
        "hit_pass_rate": round(hit_pass_rate, 4),
        "translation_ready": translation_ready,
        "translation_ready_rate": round(translation_ready_rate, 4),
        "cross_pass": cross_pass,
        "pass_rate": round(pass_rate, 4),
        "pass_target": target,
        "target_85_pass": pass_rate >= target,
    }

    report = {
        "ok": True,
        "api": args.api,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": summary,
        "rows": rows,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("crosslang_eval")
    print("out:", args.out)
    print("summary:", json.dumps(summary, ensure_ascii=False))
    return 0 if summary["target_85_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(run())
