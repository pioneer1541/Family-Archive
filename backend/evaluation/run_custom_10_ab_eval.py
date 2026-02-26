#!/usr/bin/env python3
import argparse
import json
import re
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests


REFUSE_PATTERNS = [
    r"没有相关信息",
    r"资料中(?:没有|未找到)",
    r"无法确认",
    r"无法找到",
    r"暂无记录",
    r"not found in (?:the )?documents",
    r"no relevant information",
    r"insufficient information",
    r"cannot determine",
    r"未找到关于",
]

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean(text: str) -> str:
    return str(text or "").strip().lower()


def _answer_from_agent(out: dict[str, Any], ui_lang: str = "zh") -> str:
    card = out.get("card") if isinstance(out, dict) else {}
    summary = (card.get("short_summary") or {}) if isinstance(card, dict) else {}
    key_points = card.get("key_points") if isinstance(card, dict) else []
    lines: list[str] = []
    if str(ui_lang) == "en":
        lines.append(str(summary.get("en") or summary.get("zh") or "").strip())
        if isinstance(key_points, list):
            for row in key_points[:8]:
                if isinstance(row, dict):
                    txt = str(row.get("en") or row.get("zh") or "").strip()
                    if txt:
                        lines.append(f"- {txt}")
    else:
        lines.append(str(summary.get("zh") or summary.get("en") or "").strip())
        if isinstance(key_points, list):
            for row in key_points[:8]:
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
    for row in rows[:10]:
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


def _is_refusal_answer(answer: str) -> bool:
    body = str(answer or "").strip().lower()
    if not body:
        return True
    return any(re.search(p, body, flags=re.I) for p in REFUSE_PATTERNS)


def _extract_numbers(answer: str) -> list[float]:
    body = str(answer or "")
    out: list[float] = []
    for m in re.finditer(r"(?<!\d)(?:\$|aud\s*)?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(?:aud|元|澳币))?", body, flags=re.I):
        raw = (m.group(1) or "").replace(",", "")
        try:
            out.append(float(raw))
        except Exception:
            continue
    return out


def _extract_dates(answer: str) -> set[str]:
    body = str(answer or "")
    found: set[str] = set()

    def _add(y: int, m: int, d: int) -> None:
        try:
            found.add(date(int(y), int(m), int(d)).isoformat())
        except Exception:
            return

    for m in re.finditer(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", body):
        _add(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in re.finditer(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", body):
        _add(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", body):
        # assume dd/mm/yyyy
        _add(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(20\d{2})\b", body, flags=re.I):
        mon = MONTHS.get(m.group(2).lower())
        if mon:
            _add(int(m.group(3)), int(mon), int(m.group(1)))
    for m in re.finditer(r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b", body, flags=re.I):
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            _add(int(m.group(3)), int(mon), int(m.group(2)))
    return found


def _contains_any(answer: str, candidates: list[Any]) -> bool:
    body = _clean(answer)
    if not body:
        return False
    for c in candidates:
        txt = _clean(str(c))
        if txt and txt in body:
            return True
    return False


def _regex_match(answer: str, candidates: list[Any]) -> bool:
    body = str(answer or "")
    for c in candidates:
        pat = str(c or "").strip()
        if not pat:
            continue
        if re.search(pat, body, flags=re.I | re.S):
            return True
    return False


def _numeric_targets(group: dict[str, Any]) -> list[float]:
    out: list[float] = []
    if "normalized_value" in group:
        try:
            out.append(float(group["normalized_value"]))
        except Exception:
            pass
    for c in list(group.get("candidates") or []):
        try:
            out.append(float(c))
            continue
        except Exception:
            pass
        nums = _extract_numbers(str(c))
        out.extend(nums)
    dedup: list[float] = []
    for v in out:
        if all(abs(v - x) > 1e-9 for x in dedup):
            dedup.append(v)
    return dedup


def _match_group(answer: str, group: dict[str, Any]) -> tuple[bool, str]:
    match_type = str(group.get("match_type") or "").strip()
    candidates = list(group.get("candidates") or [])
    if match_type == "contains_any":
        ok = _contains_any(answer, candidates)
        return ok, "contains_any"
    if match_type == "regex":
        ok = _regex_match(answer, candidates)
        return ok, "regex"
    if match_type in {"numeric_exact", "numeric_tolerance"}:
        tol = float(group.get("tolerance") or (0.0 if match_type == "numeric_exact" else 0.1))
        nums = _extract_numbers(answer)
        targets = _numeric_targets(group)
        for n in nums:
            for t in targets:
                if abs(n - t) <= tol:
                    return True, f"numeric~{t}"
        return False, "numeric"
    if match_type == "date_normalized":
        targets: set[str] = set()
        norm = str(group.get("normalized_value") or "").strip()
        if norm:
            targets.add(norm)
        targets |= {str(c).strip() for c in candidates if re.match(r"^20\d{2}-\d{2}-\d{2}$", str(c).strip())}
        found = _extract_dates(answer)
        if targets and (found & targets):
            return True, f"dates={sorted(found & targets)}"
        if _contains_any(answer, candidates):
            return True, "date_contains"
        return False, "date"
    return False, f"unsupported:{match_type}"


def _evidence_match(related_docs: list[dict[str, Any]], hints: list[str]) -> tuple[bool, list[dict[str, str]]]:
    hits: list[dict[str, str]] = []
    lower_hints = [h.lower() for h in hints if str(h or "").strip()]
    for row in related_docs:
        hay = " | ".join(
            [
                str(row.get("title") or ""),
                str(row.get("file_name") or ""),
                str(row.get("category_path") or ""),
            ]
        ).lower()
        matched = [h for h in lower_hints if h in hay]
        if matched:
            hits.append(
                {
                    "title": str(row.get("title") or ""),
                    "file_name": str(row.get("file_name") or ""),
                    "category_path": str(row.get("category_path") or ""),
                    "matched_hints": matched[:5],
                }
            )
    return bool(hits), hits[:5]


def _count_numeric_limit_matches(answer: str, targets: list[float], tol: float) -> int:
    nums = _extract_numbers(answer)
    matched: list[float] = []
    for t in targets:
        if any(abs(n - float(t)) <= tol for n in nums):
            matched.append(float(t))
    return len(matched)


def _count_clue_hits(answer: str, clues: list[str]) -> int:
    body = _clean(answer)
    count = 0
    for clue in clues:
        c = _clean(clue)
        if c and c in body:
            count += 1
    return count


def evaluate_answer(
    *,
    expected: dict[str, Any],
    answer: str,
    related_docs: list[dict[str, Any]],
) -> dict[str, Any]:
    rules = dict(expected.get("answer_pass_rules") or {})
    groups = list(expected.get("expected_fact_groups") or [])
    group_results: list[dict[str, Any]] = []
    required_fail: list[str] = []
    optional_pass = 0
    for g in groups:
        ok, detail = _match_group(answer, g)
        row = {
            "label": str(g.get("label") or ""),
            "required": bool(g.get("required")),
            "match_type": str(g.get("match_type") or ""),
            "passed": bool(ok),
            "detail": detail,
        }
        group_results.append(row)
        if bool(g.get("required")) and not ok:
            required_fail.append(str(g.get("label") or ""))
        if (not bool(g.get("required"))) and ok:
            optional_pass += 1

    fail_reasons: list[str] = []
    if bool(rules.get("require_non_refusal_answer")) and _is_refusal_answer(answer):
        fail_reasons.append("refusal_answer")

    evidence_ok = True
    evidence_used: list[dict[str, str]] = []
    if bool(rules.get("require_evidence_doc_hint_match")):
        evidence_ok, evidence_used = _evidence_match(related_docs, list(expected.get("evidence_doc_hints") or []))
        if not evidence_ok:
            fail_reasons.append("evidence_doc_hint_miss")
    else:
        _, evidence_used = _evidence_match(related_docs, list(expected.get("evidence_doc_hints") or []))

    if bool(rules.get("all_required_fact_groups_must_pass")) and required_fail:
        fail_reasons.append(f"required_fact_missing:{','.join(required_fail)}")

    min_optional = int(rules.get("min_optional_fact_groups") or 0)
    if optional_pass < min_optional:
        fail_reasons.append(f"optional_fact_groups_lt_{min_optional}")

    if "min_numeric_limits_found" in rules:
        targets = [float(x) for x in list(rules.get("numeric_limit_candidates") or [])]
        tol = float(rules.get("numeric_limit_tolerance") or 0.1)
        cnt = _count_numeric_limit_matches(answer, targets, tol)
        if cnt < int(rules.get("min_numeric_limits_found") or 0):
            fail_reasons.append(f"numeric_limits_found_lt_{int(rules.get('min_numeric_limits_found') or 0)}")

    if "min_modes_or_programs_found" in rules:
        clues = [str(x) for x in list(rules.get("mode_or_program_clues") or [])]
        cnt = _count_clue_hits(answer, clues)
        if cnt < int(rules.get("min_modes_or_programs_found") or 0):
            fail_reasons.append(f"modes_or_programs_clues_lt_{int(rules.get('min_modes_or_programs_found') or 0)}")

    if "list_style_min_items" in rules:
        min_items = int(rules.get("list_style_min_items") or 0)
        # Heuristic: optional fact groups passed act as proxy for list item coverage.
        if optional_pass < min_items:
            fail_reasons.append(f"list_style_items_lt_{min_items}")

    return {
        "pass": not fail_reasons,
        "fail_reasons": fail_reasons,
        "group_results": group_results,
        "required_missing": required_fail,
        "optional_pass_count": optional_pass,
        "evidence_used": evidence_used,
        "evidence_ok": evidence_ok,
    }


def _executor_stats_core(stats: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "route",
        "hit_count",
        "doc_count",
        "used_chunk_count",
        "qdrant_used",
        "retrieval_mode",
        "vector_hit_count",
        "lexical_hit_count",
        "answerability",
        "coverage_ratio",
        "coverage_missing_fields",
        "graph_enabled",
        "graph_loop_budget",
        "graph_loops_used",
        "graph_terminal_reason",
        "required_slots",
        "critical_missing_slots",
        "query_variants",
    ]
    out: dict[str, Any] = {}
    for k in keys:
        if k in stats:
            out[k] = stats.get(k)
    return out


def classify_graph_failure(result: dict[str, Any]) -> str:
    if not bool(result.get("ok")):
        return "INFRA_TIMEOUT_OR_FAILOPEN"
    if not bool(result.get("graph_enabled", False)):
        return "INFRA_TIMEOUT_OR_FAILOPEN"
    hit_count = int(result.get("hit_count") or 0)
    used_chunks = int(result.get("used_chunk_count") or 0)
    answerability = str(result.get("answerability") or "")
    retrieval_mode = str(result.get("retrieval_mode") or "")
    if hit_count == 0 and answerability == "sufficient":
        return "FALSE_SUFFICIENT_ON_ZERO_HIT"
    if hit_count == 0 and retrieval_mode in {"", "none"}:
        return "PRE_RETRIEVAL_FILTER_ZERO"
    if hit_count == 0:
        return "RETRIEVAL_ZERO"
    if hit_count > 0 and used_chunks <= 0:
        return "RERANK_OR_EXPAND_DROP"
    if hit_count > 0 and answerability in {"none", "insufficient"}:
        return "EXTRACT_OR_JUDGE_FALSE_NONE"
    return "ANSWER_GENERATION_MISS"


def _truncate_head(answer: str, limit: int = 220) -> str:
    s = " ".join(str(answer or "").split())
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def call_agent_execute(*, api: str, question: str, ui_lang: str = "zh", timeout_sec: int = 180, retries: int = 1) -> tuple[bool, dict[str, Any], str, float]:
    url = str(api).rstrip("/") + "/v1/agent/execute"
    payload = {
        "query": str(question or "").strip(),
        "ui_lang": str(ui_lang or "zh"),
        "query_lang": "auto",
        "doc_scope": {},
        "conversation": [],
        "client_context": {"context_policy": "fresh_turn"},
    }
    t0 = time.time()
    last_err = ""
    for attempt in range(max(1, retries + 1)):
        try:
            resp = requests.post(url, json=payload, timeout=max(5, int(timeout_sec)))
            resp.raise_for_status()
            return True, (resp.json() if hasattr(resp, "json") else {}), "", round(time.time() - t0, 3)
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt >= retries:
                break
            time.sleep(0.8 * (attempt + 1))
    return False, {}, last_err, round(time.time() - t0, 3)


def _summarize_outcome(*, ok: bool, out: dict[str, Any], err: str, latency_sec: float) -> dict[str, Any]:
    answer = _answer_from_agent(out, "zh") if ok else ""
    related_docs = _safe_related_docs(out) if ok else []
    stats = (out.get("executor_stats") if isinstance(out, dict) else {}) or {}
    return {
        "ok": ok,
        "error": err,
        "latency_sec": latency_sec,
        "answer": answer,
        "answer_head": _truncate_head(answer),
        "related_docs": related_docs,
        "related_docs_count": len(related_docs),
        "executor_stats": stats,
        "route": str(stats.get("route") or ""),
        "hit_count": int(stats.get("hit_count") or 0),
        "doc_count": int(stats.get("doc_count") or 0),
        "used_chunk_count": int(stats.get("used_chunk_count") or 0),
        "qdrant_used": bool(stats.get("qdrant_used")),
        "retrieval_mode": str(stats.get("retrieval_mode") or ""),
        "vector_hit_count": int(stats.get("vector_hit_count") or 0),
        "lexical_hit_count": int(stats.get("lexical_hit_count") or 0),
        "answerability": str(stats.get("answerability") or ""),
        "coverage_ratio": float(stats.get("coverage_ratio") or 0.0),
        "coverage_missing_fields": list(stats.get("coverage_missing_fields") or []),
        "graph_enabled": bool(stats.get("graph_enabled")),
        "graph_terminal_reason": str(stats.get("graph_terminal_reason") or ""),
        "graph_loops_used": int(stats.get("graph_loops_used") or 0),
        "query_variants": list(stats.get("query_variants") or []),
        "required_slots": list(stats.get("required_slots") or []),
        "critical_missing_slots": list(stats.get("critical_missing_slots") or []),
        "answer_posture": str(stats.get("answer_posture") or ""),
        "force_refusal_reason": str(stats.get("force_refusal_reason") or ""),
        "slot_fallback_used": bool(stats.get("slot_fallback_used")),
        "evidence_link_quality": str(stats.get("evidence_link_quality") or ""),
        "partial_evidence_signals": list(stats.get("partial_evidence_signals") or []),
        "refusal_blockers": list(stats.get("refusal_blockers") or []),
        "planner_latency_ms": int(stats.get("planner_latency_ms") or 0),
        "executor_latency_ms": int(stats.get("executor_latency_ms") or 0),
        "synth_latency_ms": int(stats.get("synth_latency_ms") or 0),
        "graph_node_latencies_ms": dict(stats.get("graph_node_latencies_ms") or {}),
        "graph_search_calls": int(stats.get("graph_search_calls") or 0),
        "graph_llm_calls_planner": int(stats.get("graph_llm_calls_planner") or 0),
        "graph_llm_calls_synth": int(stats.get("graph_llm_calls_synth") or 0),
        "graph_llm_calls_total": int(stats.get("graph_llm_calls_total") or 0),
        "graph_planner_reused_in_delegate": bool(stats.get("graph_planner_reused_in_delegate")),
        "graph_router_assist_triggered": bool(stats.get("graph_router_assist_triggered")),
        "graph_router_assist_reason": str(stats.get("graph_router_assist_reason") or ""),
        "graph_router_rule_confidence": float(stats.get("graph_router_rule_confidence") or 0.0),
        "graph_router_llm_confidence": float(stats.get("graph_router_llm_confidence") or 0.0),
        "graph_router_selected_categories": list(stats.get("graph_router_selected_categories") or []),
        "graph_router_kept_rule_categories": bool(stats.get("graph_router_kept_rule_categories")),
        "graph_router_assist_latency_ms": int(stats.get("graph_router_assist_latency_ms") or 0),
        "graph_router_assist_cache_hit": bool(stats.get("graph_router_assist_cache_hit")),
        "graph_router_assist_error_code": str(stats.get("graph_router_assist_error_code") or ""),
        "graph_router_assist_error_detail": str(stats.get("graph_router_assist_error_detail") or ""),
        "graph_router_assist_used_url_fallback": bool(stats.get("graph_router_assist_used_url_fallback")),
    }


def _db_fingerprint(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    out = {
        "db_path": str(db_path),
        "documents": int(cur.execute("select count(*) from documents").fetchone()[0]),
        "chunks": int(cur.execute("select count(*) from chunks").fetchone()[0]),
        "documents_completed": int(cur.execute("select count(*) from documents where status='completed'").fetchone()[0]),
        "source_available_cached_true": int(cur.execute("select count(*) from documents where coalesce(source_available_cached,0)=1").fetchone()[0]),
    }
    conn.close()
    return out


def _qdrant_fingerprint(base_url: str, collection: str) -> dict[str, Any]:
    url = str(base_url).rstrip("/") + f"/collections/{collection}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json() if hasattr(resp, "json") else {}
        result = (data.get("result") if isinstance(data, dict) else {}) or {}
        return {
            "qdrant_ok": True,
            "url": url,
            "collection": collection,
            "status": result.get("status"),
            "points_count": ((result.get("points_count") if isinstance(result, dict) else None) or ((result.get("config") or {}) if isinstance(result, dict) else {}).get("params")),
            "raw_points_count": result.get("points_count") if isinstance(result, dict) else None,
        }
    except Exception as exc:
        return {"qdrant_ok": False, "url": url, "collection": collection, "error": f"{type(exc).__name__}: {exc}"}


def _health(base_url: str) -> dict[str, Any]:
    url = str(base_url).rstrip("/") + "/v1/health"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json() if hasattr(resp, "json") else {}
        return {"ok": True, "url": url, "response": data}
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("invalid_cases_json")
    return [r for r in rows if isinstance(r, dict)]


def _load_expected(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(path)
    rows = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("invalid_expected_json")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if cid:
            out[cid] = row
    return out


def _expected_summary(expected: dict[str, Any]) -> str:
    req = [str(g.get("label") or "") for g in list(expected.get("expected_fact_groups") or []) if bool(g.get("required"))]
    hints = list(expected.get("evidence_doc_hints") or [])
    return f"required={req}; hints={hints[:3]}"


def _result_view(summary: dict[str, Any], judged: dict[str, Any]) -> dict[str, Any]:
    stats = summary.get("executor_stats") or {}
    return {
        "ok": bool(summary.get("ok")),
        "error": str(summary.get("error") or ""),
        "latency_sec": float(summary.get("latency_sec") or 0.0),
        "answer": str(summary.get("answer") or ""),
        "answer_head": str(summary.get("answer_head") or ""),
        "related_docs_count": int(summary.get("related_docs_count") or 0),
        "executor_stats_core": _executor_stats_core(stats),
        "pass": bool(judged.get("pass")),
        "fail_reasons": list(judged.get("fail_reasons") or []),
        "evidence_used": list(judged.get("evidence_used") or []),
        "group_results": list(judged.get("group_results") or []),
    }


def _render_md(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Custom 10 Answerable Questions A/B Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- generated_at: {report.get('generated_at')}")
    lines.append(f"- cases_total: {report.get('cases_total')}")
    lines.append(f"- legacy_pass_count: {report.get('legacy_pass_count')}")
    lines.append(f"- graph_pass_count: {report.get('graph_pass_count')}")
    lines.append(f"- legacy_vs_graph_delta: {report.get('legacy_vs_graph_delta')}")
    lines.append("")
    lines.append("## Environment Fingerprint")
    lines.append("")
    env = report.get("environment_fingerprint") or {}
    lines.append(f"- db: `{(env.get('db') or {}).get('db_path','')}`")
    lines.append(f"- documents/chunks/completed/source_available: {(env.get('db') or {}).get('documents')} / {(env.get('db') or {}).get('chunks')} / {(env.get('db') or {}).get('documents_completed')} / {(env.get('db') or {}).get('source_available_cached_true')}")
    qd = env.get("qdrant") or {}
    lines.append(f"- qdrant_ok: {qd.get('qdrant_ok')} raw_points_count={qd.get('raw_points_count')}")
    lines.append(f"- legacy_health_ok: {bool((env.get('legacy_api_health') or {}).get('ok'))}")
    lines.append(f"- graph_health_ok: {bool((env.get('graph_api_health') or {}).get('ok'))}")
    lines.append("")
    lines.append("## Failure Classification (Graph)")
    lines.append("")
    for k, v in sorted((report.get("failure_classification_summary") or {}).items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Per-case Results")
    lines.append("")
    lines.append("| id | legacy | graph | graph_class | notes |")
    lines.append("|---|---:|---:|---|---|")
    for row in report.get("per_case_rows") or []:
        lg = row.get("legacy_result") or {}
        gg = row.get("graph_result") or {}
        notes = []
        if not lg.get("pass"):
            notes.append("legacy_fail")
        if not gg.get("pass"):
            notes.append("graph_fail")
        if row.get("root_cause_classification"):
            notes.append(str(row.get("root_cause_classification")))
        lines.append(
            f"| {row.get('id')} | {bool(lg.get('pass'))} | {bool(gg.get('pass'))} | {row.get('root_cause_classification') or ''} | {'; '.join(notes)} |"
        )
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    for line in str(report.get("conclusion") or "").splitlines():
        lines.append(f"- {line}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run custom 10 answerable questions A/B test (legacy vs graph).")
    ap.add_argument("--legacy-api", default="http://127.0.0.1:18180")
    ap.add_argument("--graph-api", default="http://127.0.0.1:19180")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:16333")
    ap.add_argument("--qdrant-collection", default="fkv_docs_v1")
    ap.add_argument("--ui-lang", default="zh")
    ap.add_argument("--timeout-sec", type=int, default=180)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--cases", default="evaluation/agent_eval_cases_custom_10_answerable.json")
    ap.add_argument("--expected", default="evaluation/agent_eval_expected_custom_10_answerable.json")
    ap.add_argument("--rows-out", default="evaluation/agent_eval_custom_10_ab_rows.json")
    ap.add_argument("--report-out", default="evaluation/agent_eval_custom_10_ab_report.json")
    ap.add_argument("--report-md-out", default="evaluation/agent_eval_custom_10_ab_report.md")
    args = ap.parse_args()

    backend_dir = Path(__file__).resolve().parents[1]
    repo_root = backend_dir.parent
    cases_path = (backend_dir / args.cases).resolve() if not Path(args.cases).is_absolute() else Path(args.cases)
    expected_path = (backend_dir / args.expected).resolve() if not Path(args.expected).is_absolute() else Path(args.expected)
    rows_out = (backend_dir / args.rows_out).resolve() if not Path(args.rows_out).is_absolute() else Path(args.rows_out)
    report_out = (backend_dir / args.report_out).resolve() if not Path(args.report_out).is_absolute() else Path(args.report_out)
    report_md_out = (backend_dir / args.report_md_out).resolve() if not Path(args.report_md_out).is_absolute() else Path(args.report_md_out)

    cases = _load_cases(cases_path)
    expected_map = _load_expected(expected_path)
    case_ids = [str(c.get("id") or "") for c in cases]
    missing_expected = [cid for cid in case_ids if cid not in expected_map]
    if missing_expected:
        raise SystemExit(f"missing expected specs: {missing_expected}")

    env_fingerprint = {
        "db": _db_fingerprint(repo_root / "data" / "family_vault.db"),
        "qdrant": _qdrant_fingerprint(args.qdrant_url, args.qdrant_collection),
        "legacy_api_health": _health(args.legacy_api),
        "graph_api_health": _health(args.graph_api),
    }

    # Graph smoke on the first case to confirm graph_enabled and loop budget fields.
    smoke_case = cases[0]
    ok_smoke, out_smoke, err_smoke, latency_smoke = call_agent_execute(
        api=args.graph_api,
        question=str(smoke_case.get("question_zh") or ""),
        ui_lang=args.ui_lang,
        timeout_sec=args.timeout_sec,
        retries=args.retries,
    )
    smoke_summary = _summarize_outcome(ok=ok_smoke, out=out_smoke, err=err_smoke, latency_sec=latency_smoke)
    smoke_record = {
        "id": str(smoke_case.get("id") or ""),
        "question_zh": str(smoke_case.get("question_zh") or ""),
        "ok": smoke_summary.get("ok"),
        "graph_enabled": smoke_summary.get("graph_enabled"),
        "graph_loops_used": smoke_summary.get("graph_loops_used"),
        "graph_terminal_reason": smoke_summary.get("graph_terminal_reason"),
        "route": smoke_summary.get("route"),
        "hit_count": smoke_summary.get("hit_count"),
        "doc_count": smoke_summary.get("doc_count"),
        "used_chunk_count": smoke_summary.get("used_chunk_count"),
        "answerability": smoke_summary.get("answerability"),
        "required_slots": smoke_summary.get("required_slots"),
        "critical_missing_slots": smoke_summary.get("critical_missing_slots"),
        "latency_sec": smoke_summary.get("latency_sec"),
        "error": smoke_summary.get("error"),
    }

    rows_payload: list[dict[str, Any]] = []
    per_case_rows: list[dict[str, Any]] = []
    graph_failure_counts: dict[str, int] = {}
    legacy_pass = 0
    graph_pass = 0

    for case in cases:
        cid = str(case.get("id") or "")
        q = str(case.get("question_zh") or "")
        expected = expected_map[cid]

        ok_l, out_l, err_l, lat_l = call_agent_execute(
            api=args.legacy_api, question=q, ui_lang=args.ui_lang, timeout_sec=args.timeout_sec, retries=args.retries
        )
        legacy_summary = _summarize_outcome(ok=ok_l, out=out_l, err=err_l, latency_sec=lat_l)
        legacy_judged = evaluate_answer(expected=expected, answer=str(legacy_summary.get("answer") or ""), related_docs=list(legacy_summary.get("related_docs") or []))

        ok_g, out_g, err_g, lat_g = call_agent_execute(
            api=args.graph_api, question=q, ui_lang=args.ui_lang, timeout_sec=args.timeout_sec, retries=args.retries
        )
        graph_summary = _summarize_outcome(ok=ok_g, out=out_g, err=err_g, latency_sec=lat_g)
        graph_judged = evaluate_answer(expected=expected, answer=str(graph_summary.get("answer") or ""), related_docs=list(graph_summary.get("related_docs") or []))

        if legacy_judged["pass"]:
            legacy_pass += 1
        if graph_judged["pass"]:
            graph_pass += 1

        graph_fail_class = ""
        if not graph_judged["pass"]:
            graph_fail_class = classify_graph_failure(graph_summary)
            graph_failure_counts[graph_fail_class] = int(graph_failure_counts.get(graph_fail_class) or 0) + 1

        diff_parts: list[str] = []
        if legacy_judged["pass"] != graph_judged["pass"]:
            diff_parts.append(f"pass:{legacy_judged['pass']}->{graph_judged['pass']}")
        if legacy_summary.get("route") != graph_summary.get("route"):
            diff_parts.append(f"route:{legacy_summary.get('route')}->{graph_summary.get('route')}")
        if legacy_summary.get("answerability") != graph_summary.get("answerability"):
            diff_parts.append(f"answerability:{legacy_summary.get('answerability')}->{graph_summary.get('answerability')}")
        if legacy_summary.get("hit_count") != graph_summary.get("hit_count"):
            diff_parts.append(f"hits:{legacy_summary.get('hit_count')}->{graph_summary.get('hit_count')}")
        if graph_summary.get("graph_terminal_reason"):
            diff_parts.append(f"graph_terminal={graph_summary.get('graph_terminal_reason')}")

        row = {
            "id": cid,
            "question_zh": q,
            "expected_summary": _expected_summary(expected),
            "legacy_result": _result_view(legacy_summary, legacy_judged),
            "graph_result": _result_view(graph_summary, graph_judged),
            "diff_summary": "; ".join(diff_parts),
            "root_cause_classification": graph_fail_class,
            "evidence_used": {
                "legacy": legacy_judged.get("evidence_used") or [],
                "graph": graph_judged.get("evidence_used") or [],
            },
        }
        per_case_rows.append(row)
        rows_payload.append(
            {
                "id": cid,
                "domain": str(case.get("domain") or ""),
                "type": str(case.get("type") or ""),
                "question_zh": q,
                "expected": expected,
                "legacy_raw": {
                    "summary": legacy_summary,
                    "judged": legacy_judged,
                },
                "graph_raw": {
                    "summary": graph_summary,
                    "judged": graph_judged,
                },
                "graph_failure_class": graph_fail_class,
            }
        )

    conclusion_lines: list[str] = []
    conclusion_lines.append(
        f"这10题在当前资料库中均有对应证据来源（标准答案已由DB/chunks先行固化），本轮测试可用于聚焦系统执行质量。"
    )
    conclusion_lines.append(f"Legacy 通过率: {legacy_pass}/10；Graph 通过率: {graph_pass}/10。")
    if graph_failure_counts:
        top_class = sorted(graph_failure_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        conclusion_lines.append(f"Graph 失败主因分类: {top_class}（详见 per_case_rows 与 failure_classification_summary）。")
    else:
        conclusion_lines.append("Graph 10题全部通过。")

    report = {
        "ok": True,
        "generated_at": _now(),
        "environment_fingerprint": env_fingerprint,
        "run_config": {
            "legacy_api": args.legacy_api,
            "graph_api": args.graph_api,
            "ui_lang": args.ui_lang,
            "timeout_sec": int(args.timeout_sec),
            "retries": int(args.retries),
            "qdrant_url": args.qdrant_url,
            "qdrant_collection": args.qdrant_collection,
        },
        "smoke": smoke_record,
        "cases_total": len(cases),
        "legacy_pass_count": legacy_pass,
        "graph_pass_count": graph_pass,
        "legacy_vs_graph_delta": graph_pass - legacy_pass,
        "per_case_rows": per_case_rows,
        "failure_classification_summary": dict(sorted(graph_failure_counts.items())),
        "conclusion": "\n".join(conclusion_lines),
    }

    _write_json(rows_out, {"generated_at": _now(), "rows": rows_payload})
    _write_json(report_out, report)
    report_md_out.write_text(_render_md(report), encoding="utf-8")

    print(json.dumps(
        {
            "ok": True,
            "rows_out": str(rows_out),
            "report_out": str(report_out),
            "report_md_out": str(report_md_out),
            "legacy_pass_count": legacy_pass,
            "graph_pass_count": graph_pass,
            "smoke": smoke_record,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
