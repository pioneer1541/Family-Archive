#!/usr/bin/env python3
import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

import requests

try:
    from evaluation.agent_eval_scoring import score_case_mixed
except ModuleNotFoundError:  # pragma: no cover
    from agent_eval_scoring import score_case_mixed


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load_cases(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("invalid_cases_file")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("id") or "").strip()
        question_zh = str(row.get("question_zh") or "").strip()
        if not case_id or not question_zh:
            continue
        out.append(row)
    if not out:
        raise ValueError("empty_cases")
    return out


def sample_cases(cases: list[dict[str, Any]], *, sample_size: int, seed: int) -> list[dict[str, Any]]:
    if sample_size <= 0:
        raise ValueError("invalid_sample_size")
    if sample_size > len(cases):
        raise ValueError("sample_size_exceeds_case_count")
    rng = random.Random(int(seed))
    picked = list(cases)
    rng.shuffle(picked)
    return picked[:sample_size]


def sample_fixed_cases(cases: list[dict[str, Any]], *, sample_size: int) -> list[dict[str, Any]]:
    if sample_size <= 0:
        return []
    if sample_size > len(cases):
        raise ValueError("sample_size_exceeds_case_count")
    return list(cases)[:sample_size]


def call_agent_execute(*, api: str, question: str, ui_lang: str, timeout_sec: int = 60) -> dict[str, Any]:
    url = str(api).rstrip("/") + "/v1/agent/execute"
    payload = {
        "query": str(question or "").strip(),
        "ui_lang": str(ui_lang or "zh"),
        "query_lang": "auto",
        "doc_scope": {},
        "conversation": [],
        "client_context": {"context_policy": "fresh_turn"},
    }
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=max(5, int(timeout_sec)),
            )
            resp.raise_for_status()
            return resp.json() if hasattr(resp, "json") else {}
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as exc:
            last_exc = exc
            if attempt >= 2:
                raise
            time.sleep(0.5 * (2**attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("agent_eval_call_failed")


def _answer_from_agent(out: dict[str, Any], ui_lang: str) -> str:
    card = out.get("card") if isinstance(out, dict) else {}
    summary = (card.get("short_summary") or {}) if isinstance(card, dict) else {}
    key_points = card.get("key_points") if isinstance(card, dict) else []
    lines: list[str] = []
    if str(ui_lang) == "en":
        lines.append(str(summary.get("en") or "").strip())
        if isinstance(key_points, list):
            for row in key_points[:6]:
                if isinstance(row, dict):
                    txt = str(row.get("en") or row.get("zh") or "").strip()
                    if txt:
                        lines.append(f"- {txt}")
    else:
        lines.append(str(summary.get("zh") or summary.get("en") or "").strip())
        if isinstance(key_points, list):
            for row in key_points[:6]:
                if isinstance(row, dict):
                    txt = str(row.get("zh") or row.get("en") or "").strip()
                    if txt:
                        lines.append(f"- {txt}")
    return "\n".join([x for x in lines if str(x or "").strip()]).strip()


def _safe_related_docs(out: dict[str, Any]) -> list[dict[str, Any]]:
    rows = out.get("related_docs") if isinstance(out, dict) else None
    if not isinstance(rows, list):
        return []
    docs: list[dict[str, Any]] = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        docs.append(
            {
                "doc_id": str(row.get("doc_id") or ""),
                "file_name": str(row.get("file_name") or ""),
                "title": str(row.get("title_zh") or row.get("title_en") or row.get("file_name") or ""),
                "category_path": str(row.get("category_path") or ""),
            }
        )
    return docs


def _domain_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = {}
    for row in rows:
        domain = str(row.get("domain") or "").strip().lower()
        score = float(((row.get("scores") or {}).get("mixed") or {}).get("overall") or 0.0)
        buckets.setdefault(domain, []).append(score)
    return {
        domain: {
            "count": len(scores),
            "avg_overall": round(sum(scores) / max(1, len(scores)), 4),
        }
        for domain, scores in sorted(buckets.items())
    }


def _route_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        route = str(((row.get("executor_stats") or {}).get("route") or "")).strip() or "unknown"
        out[route] = int(out.get(route, 0)) + 1
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def _coverage_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {"lt_0_4": 0, "0_4_to_0_7": 0, "gte_0_7": 0}
    for row in rows:
        cov = float(((row.get("executor_stats") or {}).get("coverage_ratio") or 0.0))
        if cov < 0.4:
            out["lt_0_4"] += 1
        elif cov < 0.7:
            out["0_4_to_0_7"] += 1
        else:
            out["gte_0_7"] += 1
    return out


def _graph_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "graph_enabled_rate": 0.0,
            "avg_graph_loops_used": 0.0,
            "recovery_success_rate": 0.0,
            "terminal_reason_distribution": {},
        }
    graph_rows = [row for row in rows if bool(((row.get("executor_stats") or {}).get("graph_enabled")))]
    loops = [int(((row.get("executor_stats") or {}).get("graph_loops_used") or 0)) for row in graph_rows]
    terminal_reason_distribution: dict[str, int] = {}
    recover_attempted = 0
    recover_success = 0
    for row in graph_rows:
        stats = row.get("executor_stats") or {}
        reason = str(stats.get("graph_terminal_reason") or "").strip() or "unknown"
        terminal_reason_distribution[reason] = int(terminal_reason_distribution.get(reason) or 0) + 1
        loops_used = int(stats.get("graph_loops_used") or 0)
        if loops_used > 0:
            recover_attempted += 1
            if str(stats.get("answerability") or "") in {"sufficient", "partial"}:
                recover_success += 1
    return {
        "graph_enabled_rate": round(len(graph_rows) / max(1, len(rows)), 4),
        "avg_graph_loops_used": round(sum(loops) / max(1, len(loops)), 4) if loops else 0.0,
        "recovery_success_rate": round(recover_success / max(1, recover_attempted), 4) if recover_attempted else 0.0,
        "terminal_reason_distribution": dict(sorted(terminal_reason_distribution.items(), key=lambda kv: kv[0])),
    }


def _infra_error_type(row: dict[str, Any]) -> str:
    return str(row.get("error_type") or row.get("error") or "").strip()


def _is_infra_error(row: dict[str, Any]) -> bool:
    if bool(row.get("infra_error")):
        return True
    return _infra_error_type(row) in {"ConnectionError", "ReadTimeout", "Timeout", "ConnectTimeout"}


def _refusal_policy_violations(rows: list[dict[str, Any]]) -> list[str]:
    bad: list[str] = []
    for row in rows:
        if not bool(row.get("should_refuse")):
            continue
        boundary_ok = bool((((row.get("scores") or {}).get("rule") or {}).get("boundary_ok")))
        if not boundary_ok:
            bad.append(str(row.get("id") or ""))
    return [x for x in bad if x]


def _boundary_pass_rate(rows: list[dict[str, Any]]) -> float:
    boundary = [r for r in rows if bool(r.get("should_refuse"))]
    if not boundary:
        return 1.0
    passed = 0
    for row in boundary:
        boundary_ok = bool((((row.get("scores") or {}).get("rule") or {}).get("boundary_ok")))
        if boundary_ok:
            passed += 1
    return round(passed / len(boundary), 4)


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "avg_total_score": 0.0,
            "context_relevance_avg": 0.0,
            "answer_faithfulness_avg": 0.0,
            "answer_relevance_avg": 0.0,
            "boundary_refusal_pass_rate": 0.0,
            "domain_breakdown": {},
        }
    ctx = [float(((r.get("scores") or {}).get("mixed") or {}).get("context_relevance") or 0.0) for r in rows]
    fai = [float(((r.get("scores") or {}).get("mixed") or {}).get("answer_faithfulness") or 0.0) for r in rows]
    rel = [float(((r.get("scores") or {}).get("mixed") or {}).get("answer_relevance") or 0.0) for r in rows]
    overall = [float(((r.get("scores") or {}).get("mixed") or {}).get("overall") or 0.0) for r in rows]
    route_distribution = _route_distribution(rows)
    structured_routes = {"bill_attention", "bill_monthly_total", "queue_snapshot", "reprocess_exec", "tag_update_exec", "detail_extract", "entity_fact_lookup", "period_aggregate"}
    structured_hits = sum(int(route_distribution.get(route) or 0) for route in structured_routes)
    return {
        "avg_total_score": round(sum(overall) / len(overall), 4),
        "context_relevance_avg": round(sum(ctx) / len(ctx), 4),
        "answer_faithfulness_avg": round(sum(fai) / len(fai), 4),
        "answer_relevance_avg": round(sum(rel) / len(rel), 4),
        "boundary_refusal_pass_rate": _boundary_pass_rate(rows),
        "domain_breakdown": _domain_metrics(rows),
        "route_distribution": route_distribution,
        "structured_route_hit_rate": round(structured_hits / len(rows), 4) if rows else 0.0,
        "coverage_distribution": _coverage_distribution(rows),
        "refusal_policy_violations": _refusal_policy_violations(rows),
        **_graph_metrics(rows),
    }


def build_summary_excluding_infra(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filtered = [row for row in rows if not _is_infra_error(row)]
    out = build_summary(filtered)
    infra_count = sum(1 for row in rows if _is_infra_error(row))
    out["infra_error_count"] = int(infra_count)
    out["infra_error_rate"] = round(infra_count / max(1, len(rows)), 4) if rows else 0.0
    out["sample_count_effective"] = len(filtered)
    return out


def write_markdown(report: dict[str, Any], path: str) -> None:
    rows = list(report.get("rows") or [])
    summary = report.get("summary") or {}
    lines: list[str] = []
    lines.append("# Agent Eval Report")
    lines.append("")
    lines.append(f"- run_id: `{report.get('run_id')}`")
    lines.append(f"- generated_at: `{report.get('generated_at')}`")
    lines.append(f"- seed: `{report.get('seed')}`")
    lines.append(f"- sample_size: `{report.get('sample_size')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- avg_total_score: **{summary.get('avg_total_score', 0.0):.4f}**")
    lines.append(f"- context_relevance_avg: {summary.get('context_relevance_avg', 0.0):.4f}")
    lines.append(f"- answer_faithfulness_avg: {summary.get('answer_faithfulness_avg', 0.0):.4f}")
    lines.append(f"- answer_relevance_avg: {summary.get('answer_relevance_avg', 0.0):.4f}")
    lines.append(f"- boundary_refusal_pass_rate: {summary.get('boundary_refusal_pass_rate', 0.0):.4f}")
    lines.append("")
    lines.append("## Rows")
    lines.append("")
    lines.append("| case | domain | score | faithfulness | boundary | notes |")
    lines.append("|---|---|---:|---:|---|---|")
    for row in rows:
        scores = row.get("scores") or {}
        mixed = scores.get("mixed") or {}
        notes = ",".join(list(((scores.get("rule") or {}).get("rule_notes") or [])))
        lines.append(
            f"| {row.get('id')} | {row.get('domain')} | {float(mixed.get('overall') or 0.0):.3f} | "
            f"{float(mixed.get('answer_faithfulness') or 0.0):.3f} | {bool(row.get('should_refuse'))} | {notes} |"
        )
    Path(path).write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _evaluate_case_rows(
    *,
    api: str,
    picked: list[dict[str, Any]],
    ui_lang: str,
    judge_model: str | None,
    judge_timeout_sec: int,
    track: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in picked:
        q = str(case.get("question_zh") or "").strip()
        t0 = time.time()
        err = ""
        err_type = ""
        out: dict[str, Any] = {}
        try:
            out = call_agent_execute(api=api, question=q, ui_lang=ui_lang)
        except Exception as exc:  # pragma: no cover
            err = type(exc).__name__
            err_type = type(exc).__name__
        latency_ms = int((time.time() - t0) * 1000)
        answer = _answer_from_agent(out, ui_lang) if out else ""
        related_docs = _safe_related_docs(out) if out else []
        scores = score_case_mixed(
            case=case,
            answer=answer,
            related_docs=related_docs,
            judge_model=judge_model,
            judge_timeout_sec=judge_timeout_sec,
        )
        infra_error = err_type in {"ConnectionError", "ReadTimeout", "Timeout", "ConnectTimeout"}
        rows.append(
            {
                "id": str(case.get("id") or ""),
                "domain": str(case.get("domain") or ""),
                "type": str(case.get("type") or ""),
                "difficulty": int(case.get("difficulty") or 1),
                "question_zh": q,
                "expected_behavior": str(case.get("expected_behavior") or ""),
                "should_refuse": bool(case.get("should_refuse")),
                "answer": answer,
                "related_docs": related_docs,
                "executor_stats": (out.get("executor_stats") if isinstance(out, dict) else {}) or {},
                "scores": scores,
                "latency_ms": latency_ms,
                "error": err,
                "error_type": err_type,
                "infra_error": infra_error,
                "manual_review_needed": bool(err) or bool(scores.get("judge_error")),
                "track": track,
            }
        )
    return rows


def run_eval(
    *,
    api: str,
    cases_path: str,
    sample_size: int,
    boundary_cases_path: str | None,
    boundary_sample_size: int,
    seed: int | None,
    ui_lang: str,
    judge_model: str | None,
    judge_timeout_sec: int,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    used_seed = int(seed if seed is not None else int(time.time()))
    picked = sample_cases(cases, sample_size=sample_size, seed=used_seed)
    rows = _evaluate_case_rows(
        api=api,
        picked=picked,
        ui_lang=ui_lang,
        judge_model=judge_model,
        judge_timeout_sec=judge_timeout_sec,
        track="random",
    )

    boundary_rows: list[dict[str, Any]] = []
    if boundary_cases_path and boundary_sample_size > 0:
        boundary_cases = load_cases(boundary_cases_path)
        boundary_picked = sample_fixed_cases(boundary_cases, sample_size=boundary_sample_size)
        boundary_rows = _evaluate_case_rows(
            api=api,
            picked=boundary_picked,
            ui_lang=ui_lang,
            judge_model=judge_model,
            judge_timeout_sec=judge_timeout_sec,
            track="boundary",
        )
        rows.extend(boundary_rows)

    report = {
        "ok": True,
        "run_id": f"agent-eval-{int(time.time())}",
        "generated_at": _now(),
        "api": str(api),
        "seed": used_seed,
        "sample_size": int(sample_size),
        "sampled_case_ids": [str(row.get("id") or "") for row in picked],
        "boundary_sample_size": int(boundary_sample_size),
        "boundary_case_ids": [str(row.get("id") or "") for row in boundary_rows],
        "summary": build_summary(rows),
        "summary_random": build_summary([row for row in rows if str(row.get("track") or "") == "random"]),
        "summary_boundary": build_summary([row for row in rows if str(row.get("track") or "") == "boundary"]),
        "summary_excluding_infra": build_summary_excluding_infra(rows),
        "rows": rows,
        "judge_trace": {
            "model": str(judge_model or ""),
            "judge_timeout_sec": int(judge_timeout_sec),
        },
    }
    return report


def _default_md_out(json_out: str) -> str:
    p = Path(json_out)
    if p.suffix.lower() == ".json":
        return str(p.with_suffix(".md"))
    return str(Path(str(json_out) + ".md"))


def run() -> int:
    ap = argparse.ArgumentParser(description="Run Agent mixed evaluation (rule + LLM judge).")
    ap.add_argument("--api", default="http://127.0.0.1:18180")
    ap.add_argument("--cases", default="evaluation/agent_eval_cases_v1.json")
    ap.add_argument("--sample-size", type=int, default=20)
    ap.add_argument("--boundary-cases", default="")
    ap.add_argument("--boundary-sample-size", type=int, default=0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--ui-lang", default="zh", choices=["zh", "en"])
    ap.add_argument("--judge-model", default="")
    ap.add_argument("--judge-timeout-sec", type=int, default=30)
    ap.add_argument("--out", default="evaluation/agent_eval_report.json")
    ap.add_argument("--md-out", default="")
    args = ap.parse_args()

    report = run_eval(
        api=args.api,
        cases_path=args.cases,
        sample_size=int(args.sample_size),
        boundary_cases_path=(str(args.boundary_cases).strip() or None),
        boundary_sample_size=max(0, int(args.boundary_sample_size)),
        seed=args.seed,
        ui_lang=args.ui_lang,
        judge_model=(str(args.judge_model).strip() or None),
        judge_timeout_sec=int(args.judge_timeout_sec),
    )
    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_out = str(args.md_out).strip() or _default_md_out(str(out_path))
    write_markdown(report, md_out)
    print(
        json.dumps(
            {
                "report_json": str(out_path),
                "report_md": md_out,
                "sample_size": report.get("sample_size"),
                "seed": report.get("seed"),
                "avg_total_score": (report.get("summary") or {}).get("avg_total_score"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
