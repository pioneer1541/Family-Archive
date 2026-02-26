#!/usr/bin/env python3
"""
50-query bilingual comprehensive test for the family-vault agent.

Usage:
    cd backend
    python evaluation/run_50_bilingual_test.py
    python evaluation/run_50_bilingual_test.py --api http://localhost:18180
"""
import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import requests

API_BASE = "http://localhost:18180"
REPORT_PATH = Path(__file__).parent / "bilingual_50_report.json"
TIMEOUT_SEC = 60

# ---------------------------------------------------------------------------
# 50 bilingual test cases
# ---------------------------------------------------------------------------
CASES: list[dict[str, Any]] = [
    # ── Bills (10) ─────────────────────────────────────────────────────────
    {
        "id": "B01", "domain": "bills", "lang": "zh", "ui_lang": "zh",
        "query": "上个月的电费是多少？",
        "expected_route": "calculate", "expected_sub": "bill_monthly_total",
        "note": "电费-上月",
    },
    {
        "id": "B02", "domain": "bills", "lang": "en", "ui_lang": "en",
        "query": "What was the water bill for January 2026?",
        "expected_route": "calculate", "expected_sub": "bill_monthly_total",
        "note": "water-jan2026",
    },
    {
        "id": "B03", "domain": "bills", "lang": "zh", "ui_lang": "zh",
        "query": "网络费用每月固定是多少？",
        "expected_route": "calculate", "expected_sub": "bill_attention",
        "note": "internet-monthly",
    },
    {
        "id": "B04", "domain": "bills", "lang": "zh", "ui_lang": "zh",
        "query": "过去3个月的燃气费总计是多少？",
        "expected_route": "calculate", "expected_sub": "period_aggregate",
        "note": "gas-3months",
    },
    {
        "id": "B05", "domain": "bills", "lang": "en", "ui_lang": "en",
        "query": "How much did we spend on all utilities last quarter?",
        "expected_route": "calculate", "expected_sub": "period_aggregate",
        "note": "all-utilities-quarter",
    },
    {
        "id": "B06", "domain": "bills", "lang": "zh", "ui_lang": "zh",
        "query": "有没有未付的账单？",
        "expected_route": "calculate", "expected_sub": "bill_attention",
        "note": "unpaid-bills",
    },
    {
        "id": "B07", "domain": "bills", "lang": "zh", "ui_lang": "zh",
        "query": "2026年2月份的账单情况",
        "expected_route": "calculate", "expected_sub": "bill_monthly_total",
        "note": "feb2026-all-bills",
    },
    {
        "id": "B08", "domain": "bills", "lang": "en", "ui_lang": "en",
        "query": "What bills are coming due soon?",
        "expected_route": "calculate", "expected_sub": "bill_attention",
        "note": "upcoming-bills",
    },
    {
        "id": "B09", "domain": "bills", "lang": "zh", "ui_lang": "zh",
        "query": "物业费的缴费日期是什么时候？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "strata-due-date",
    },
    {
        "id": "B10", "domain": "bills", "lang": "en", "ui_lang": "en",
        "query": "February 2026 electricity bill total amount",
        "expected_route": "calculate", "expected_sub": "bill_monthly_total",
        "note": "electricity-feb2026-en",
    },
    # ── Insurance (8) ──────────────────────────────────────────────────────
    {
        "id": "I01", "domain": "insurance", "lang": "zh", "ui_lang": "zh",
        "query": "特斯拉的车险保单到期日是什么时候？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "vehicle-expiry",
    },
    {
        "id": "I02", "domain": "insurance", "lang": "en", "ui_lang": "en",
        "query": "What is our vehicle insurance policy number?",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "vehicle-policy-no",
    },
    {
        "id": "I03", "domain": "insurance", "lang": "zh", "ui_lang": "zh",
        "query": "宠物保险的保障范围包括哪些？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "pet-insurance-coverage",
    },
    {
        "id": "I04", "domain": "insurance", "lang": "zh", "ui_lang": "zh",
        "query": "Lucky的宠物保险每年的保费是多少？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "pet-premium",
    },
    {
        "id": "I05", "domain": "insurance", "lang": "zh", "ui_lang": "zh",
        "query": "健康保险的客服联系电话是多少？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "health-ins-phone",
    },
    {
        "id": "I06", "domain": "insurance", "lang": "en", "ui_lang": "en",
        "query": "Which insurer provides our home and contents insurance?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "home-insurer",
    },
    {
        "id": "I07", "domain": "insurance", "lang": "en", "ui_lang": "en",
        "query": "What is the excess amount on our pet insurance?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "pet-excess",
    },
    {
        "id": "I08", "domain": "insurance", "lang": "mixed", "ui_lang": "zh",
        "query": "家庭 health insurance 的等待期是多久？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "health-waiting-period",
    },
    # ── Home/Property (5) ──────────────────────────────────────────────────
    {
        "id": "H01", "domain": "home", "lang": "zh", "ui_lang": "zh",
        "query": "房贷的月供金额是多少？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "mortgage-monthly",
    },
    {
        "id": "H02", "domain": "home", "lang": "zh", "ui_lang": "zh",
        "query": "我们的贷款是哪家银行的？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "loan-bank",
    },
    {
        "id": "H03", "domain": "home", "lang": "en", "ui_lang": "en",
        "query": "What is the remaining loan term in years?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "loan-term",
    },
    {
        "id": "H04", "domain": "home", "lang": "zh", "ui_lang": "zh",
        "query": "屋顶上次维修是什么时候做的？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "roof-maintenance-date",
    },
    {
        "id": "H05", "domain": "home", "lang": "en", "ui_lang": "en",
        "query": "What is the total floor area of our property?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "property-area",
    },
    # ── Appliances (5) ─────────────────────────────────────────────────────
    {
        "id": "A01", "domain": "appliances", "lang": "zh", "ui_lang": "zh",
        "query": "洗碗机是什么品牌型号？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "dishwasher-brand-model",
    },
    {
        "id": "A02", "domain": "appliances", "lang": "en", "ui_lang": "en",
        "query": "What brand and model is our air conditioner?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "ac-brand-model",
    },
    {
        "id": "A03", "domain": "appliances", "lang": "zh", "ui_lang": "zh",
        "query": "热水器是什么时候购买的？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "hot-water-purchase-date",
    },
    {
        "id": "A04", "domain": "appliances", "lang": "zh", "ui_lang": "zh",
        "query": "冰箱的购买发票号是多少？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "fridge-invoice",
    },
    {
        "id": "A05", "domain": "appliances", "lang": "en", "ui_lang": "en",
        "query": "When was the last service for our air conditioner?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "ac-last-service",
    },
    # ── Warranty (5) ───────────────────────────────────────────────────────
    {
        "id": "W01", "domain": "warranty", "lang": "zh", "ui_lang": "zh",
        "query": "水箱的保修期是多少年？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "water-tank-warranty",
    },
    {
        "id": "W02", "domain": "warranty", "lang": "en", "ui_lang": "en",
        "query": "What is the hot water system serial number?",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "hot-water-serial",
    },
    {
        "id": "W03", "domain": "warranty", "lang": "zh", "ui_lang": "zh",
        "query": "洗碗机的保修截止日期是什么时候？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "dishwasher-warranty-end",
    },
    {
        "id": "W04", "domain": "warranty", "lang": "zh", "ui_lang": "zh",
        "query": "空调的保修服务商是谁？联系方式是什么？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "ac-warranty-contact",
    },
    {
        "id": "W05", "domain": "warranty", "lang": "en", "ui_lang": "en",
        "query": "What does the dishwasher warranty cover?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "dishwasher-warranty-coverage",
    },
    # ── Pets (6) ───────────────────────────────────────────────────────────
    {
        "id": "P01", "domain": "pets", "lang": "zh", "ui_lang": "zh",
        "query": "Lucky的出生日期是什么时候？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "lucky-birthday",
    },
    {
        "id": "P02", "domain": "pets", "lang": "zh", "ui_lang": "zh",
        "query": "米饭下次需要打什么疫苗？什么时候打？",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "mifan-next-vaccine",
    },
    {
        "id": "P03", "domain": "pets", "lang": "en", "ui_lang": "en",
        "query": "What vaccines has Lucky received so far?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "lucky-vaccine-history",
    },
    {
        "id": "P04", "domain": "pets", "lang": "zh", "ui_lang": "zh",
        "query": "宠物医院的联系方式是什么？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "vet-contact",
    },
    {
        "id": "P05", "domain": "pets", "lang": "zh", "ui_lang": "zh",
        "query": "Lucky的宠物登记证号是多少？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "lucky-registration",
    },
    {
        "id": "P06", "domain": "pets", "lang": "mixed", "ui_lang": "zh",
        "query": "When was 米饭's last vet checkup?",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "mifan-last-vet",
    },
    # ── Legal/Contract (4) ─────────────────────────────────────────────────
    {
        "id": "L01", "domain": "legal", "lang": "zh", "ui_lang": "zh",
        "query": "我们目前有哪些有效合同？",
        "expected_route": "lookup", "expected_sub": "search_semantic",
        "note": "active-contracts",
    },
    {
        "id": "L02", "domain": "legal", "lang": "zh", "ui_lang": "zh",
        "query": "网络服务合同的到期日是什么时候？",
        "expected_route": "lookup", "expected_sub": "entity_fact_lookup",
        "note": "internet-contract-expiry",
    },
    {
        "id": "L03", "domain": "legal", "lang": "en", "ui_lang": "en",
        "query": "What are our obligations under the strata or body corporate agreement?",
        "expected_route": "lookup", "expected_sub": "search_semantic",
        "note": "strata-obligations",
    },
    {
        "id": "L04", "domain": "legal", "lang": "en", "ui_lang": "en",
        "query": "What is the notice period in our internet service contract?",
        "expected_route": "lookup", "expected_sub": "detail_extract",
        "note": "internet-notice-period",
    },
    # ── How-to / Semantic (4) ──────────────────────────────────────────────
    {
        "id": "S01", "domain": "home", "lang": "zh", "ui_lang": "zh",
        "query": "水箱应该怎么维护保养？",
        "expected_route": "lookup", "expected_sub": "search_semantic",
        "note": "water-tank-howto",
    },
    {
        "id": "S02", "domain": "appliances", "lang": "en", "ui_lang": "en",
        "query": "How do I reset the air conditioner filter indicator?",
        "expected_route": "lookup", "expected_sub": "search_semantic",
        "note": "ac-filter-reset-howto",
    },
    {
        "id": "S03", "domain": "bills", "lang": "zh", "ui_lang": "zh",
        "query": "有什么节约用电的实用方法？",
        "expected_route": "lookup", "expected_sub": "search_semantic",
        "note": "save-electricity-howto",
    },
    {
        "id": "S04", "domain": "legal", "lang": "en", "ui_lang": "en",
        "query": "What documents do I need for vehicle registration renewal?",
        "expected_route": "lookup", "expected_sub": "search_semantic",
        "note": "vehicle-registration-docs",
    },
    # ── Chitchat / Edge (3) ────────────────────────────────────────────────
    {
        "id": "C01", "domain": "chitchat", "lang": "zh", "ui_lang": "zh",
        "query": "你好，你能做什么？",
        "expected_route": "chitchat", "expected_sub": "chitchat",
        "note": "greeting-capabilities",
    },
    {
        "id": "C02", "domain": "chitchat", "lang": "en", "ui_lang": "en",
        "query": "Can you calculate 15% of my last electricity bill?",
        "expected_route": "calculate", "expected_sub": "bill_monthly_total",
        "note": "math-with-bill-context",
    },
    {
        "id": "C03", "domain": "generic", "lang": "zh", "ui_lang": "zh",
        "query": "家里有没有任何文件快要过期了？",
        "expected_route": "lookup", "expected_sub": "search_semantic",
        "note": "expiring-docs-summary",
    },
]


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def call_agent(*, api: str, query: str, ui_lang: str) -> tuple[dict[str, Any], float]:
    url = api.rstrip("/") + "/v1/agent/execute"
    payload = {
        "query": query,
        "ui_lang": ui_lang,
        "query_lang": "auto",
        "doc_scope": {},
        "conversation": [],
        "client_context": {"context_policy": "fresh_turn"},
    }
    t0 = time.perf_counter()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT_SEC)
            resp.raise_for_status()
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return resp.json(), elapsed_ms
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    raise last_exc or RuntimeError("call_failed")


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def extract_result(resp: dict[str, Any], measured_ms: int) -> dict[str, Any]:
    card = resp.get("card") or {}
    stats = resp.get("executor_stats") or {}
    planner = resp.get("planner") or {}

    route = str(stats.get("route") or planner.get("intent") or "").strip()
    sub_intent = str(stats.get("sub_intent") or "").strip()
    total_ms = int(stats.get("total_latency_ms") or 0) or measured_ms

    ui_lang = str(planner.get("ui_lang") or "zh")
    summary = card.get("short_summary") or {}
    if ui_lang == "en":
        answer_text = str(summary.get("en") or summary.get("zh") or "")
    else:
        answer_text = str(summary.get("zh") or summary.get("en") or "")
    answer_snippet = answer_text[:90].replace("\n", " ").strip()

    related_docs = resp.get("related_docs") or []
    doc_count = len(related_docs) if isinstance(related_docs, list) else 0

    insufficient = bool(card.get("insufficient_evidence", False))

    detail_sections = card.get("detail_sections") or []
    filled_fields = 0
    for section in detail_sections:
        rows = (section.get("rows") or []) if isinstance(section, dict) else []
        filled_fields += sum(1 for r in rows if isinstance(r, dict) and str(r.get("value_zh") or r.get("value_en") or "").strip())

    return {
        "route": route,
        "sub_intent": sub_intent,
        "latency_ms": total_ms,
        "answer_snippet": answer_snippet,
        "doc_count": doc_count,
        "insufficient_evidence": insufficient,
        "filled_fields": filled_fields,
    }


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def _bar(n: int, total: int, width: int = 20) -> str:
    filled = round(n / max(1, total) * width)
    return "█" * filled + "░" * (width - filled)


def print_report(results: list[dict[str, Any]]) -> None:
    sep = "═" * 70
    thin = "─" * 70

    total = len(results)
    errors = [r for r in results if r.get("error")]
    insufficient = [r for r in results if r.get("result", {}).get("insufficient_evidence")]
    answered = [r for r in results if not r.get("error") and not r.get("result", {}).get("insufficient_evidence")]
    latencies = [r["result"]["latency_ms"] for r in results if not r.get("error") and r.get("result")]

    print()
    print(sep)
    print("  BILINGUAL 50-QUERY TEST REPORT  双语50题综合测试报告")
    print(sep)
    print(f"  总计: {total}  ✓已回答: {len(answered)}  ⚠证据不足: {len(insufficient)}  ✗错误: {len(errors)}")
    print()

    # ── Domain breakdown ──
    print("── 按领域分布 (By Domain) " + "─" * 44)
    domains: dict[str, list[dict]] = {}
    for r in results:
        d = r["case"]["domain"]
        domains.setdefault(d, []).append(r)

    print(f"  {'Domain':<14} {'Count':>5}  {'AvgLatency':>10}  {'NoEvidence':>10}  {'Errors':>6}")
    print(f"  {'─'*14} {'─'*5}  {'─'*10}  {'─'*10}  {'─'*6}")
    for domain, rows in sorted(domains.items()):
        cnt = len(rows)
        lats = [r["result"]["latency_ms"] for r in rows if not r.get("error") and r.get("result")]
        avg_lat = f"{statistics.mean(lats)/1000:.1f}s" if lats else "—"
        insuf = sum(1 for r in rows if r.get("result", {}).get("insufficient_evidence"))
        errs = sum(1 for r in rows if r.get("error"))
        insuf_pct = f"{insuf} ({insuf*100//cnt}%)" if insuf else "0"
        print(f"  {domain:<14} {cnt:>5}  {avg_lat:>10}  {insuf_pct:>10}  {errs:>6}")
    print()

    # ── Route distribution ──
    print("── 路由分布 (Route Distribution) " + "─" * 36)
    route_counts: dict[str, int] = {}
    for r in results:
        if r.get("result"):
            route = r["result"]["route"]
            sub = r["result"]["sub_intent"]
            key = f"{route}/{sub}" if sub else route or "unknown"
            route_counts[key] = route_counts.get(key, 0) + 1
        elif r.get("error"):
            route_counts["ERROR"] = route_counts.get("ERROR", 0) + 1
    for k, v in sorted(route_counts.items(), key=lambda x: -x[1]):
        bar = _bar(v, total, width=15)
        print(f"  {k:<38} {bar} {v:>3}")
    print()

    # ── Route accuracy ──
    print("── 路由准确率 (Route Accuracy) " + "─" * 39)
    correct_route = 0
    for r in results:
        if not r.get("error") and r.get("result"):
            expected = r["case"]["expected_route"]
            actual = r["result"]["route"]
            if expected and actual and expected.lower() == actual.lower():
                correct_route += 1
    route_acc = correct_route / max(1, total - len(errors))
    print(f"  路由匹配: {correct_route}/{total - len(errors)} ({route_acc:.0%})")
    print()

    # ── Latency stats ──
    print("── 延迟统计 (Latency Stats) " + "─" * 42)
    if latencies:
        sorted_lats = sorted(latencies)
        p50 = sorted_lats[int(len(sorted_lats) * 0.50)]
        p90 = sorted_lats[int(len(sorted_lats) * 0.90)]
        print(f"  p50: {p50/1000:.1f}s  p90: {p90/1000:.1f}s  max: {max(latencies)/1000:.1f}s  min: {min(latencies)/1000:.1f}s")
        slow = [r for r in results if r.get("result") and r["result"]["latency_ms"] > 15000]
        if slow:
            print(f"  ⚠ 超过15秒的查询: {len(slow)} 个")
    else:
        print("  无延迟数据")
    print()

    # ── Issues list ──
    issues = []
    for r in results:
        case = r["case"]
        if r.get("error"):
            issues.append((case["id"], case["domain"], "ERROR", str(r["error"])[:60], case["query"]))
        elif r.get("result", {}).get("insufficient_evidence"):
            issues.append((case["id"], case["domain"], "NO_EVIDENCE", "", case["query"]))
        elif r.get("result") and r["result"]["latency_ms"] > 15000:
            issues.append((case["id"], case["domain"], f"SLOW {r['result']['latency_ms']//1000}s", "", case["query"]))

    if issues:
        print("── 问题清单 (Issues) " + "─" * 49)
        for cid, domain, issue_type, detail, query in issues:
            q_short = query[:50] + ("…" if len(query) > 50 else "")
            detail_str = f"  {detail}" if detail else ""
            print(f"  [{cid}] {domain:<12} {issue_type:<20} {q_short}{detail_str}")
        print()

    # ── Full detail table ──
    print("── 明细表 (Full Result Table) " + "─" * 40)
    print(f"  {'#':<3} {'ID':<4} {'Domain':<12} {'L':2} {'Route/SubIntent':<34} {'ms':>6} {'D':>2} {'F':>2}  {'Status'}")
    print(f"  {'─'*3} {'─'*4} {'─'*12} {'─'*2} {'─'*34} {'─'*6} {'─'*2} {'─'*2}  {'─'*30}")

    for i, r in enumerate(results, 1):
        case = r["case"]
        result = r.get("result") or {}
        err = r.get("error")
        route = result.get("route", "")
        sub = result.get("sub_intent", "")
        route_str = f"{route}/{sub}" if sub else (route or "—")
        lat = result.get("latency_ms", 0)
        docs = result.get("doc_count", 0)
        fields = result.get("filled_fields", 0)
        insuf = result.get("insufficient_evidence", False)

        if err:
            status = f"✗ {str(err)[:28]}"
        elif insuf:
            status = "⚠ no evidence"
        else:
            snippet = result.get("answer_snippet", "")[:28]
            status = f"✓ {snippet}"

        lang_flag = {"zh": "中", "en": "E", "mixed": "M"}.get(case["lang"], "?")
        lat_str = f"{lat}" if lat else "—"

        print(
            f"  {i:<3} {case['id']:<4} {case['domain']:<12} {lang_flag:2} "
            f"{route_str:<34} {lat_str:>6} {docs:>2} {fields:>2}  {status}"
        )

    print()
    print(sep)
    print(f"  报告已保存至: {REPORT_PATH}")
    print(sep)
    print()


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_all(api: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    print(f"\n开始测试 / Starting test: {len(CASES)} queries → {api}")
    print("─" * 70)

    for i, case in enumerate(CASES, 1):
        query = case["query"]
        ui_lang = case["ui_lang"]
        q_short = query[:55] + ("…" if len(query) > 55 else "")
        print(f"[{i:02d}/{len(CASES)}] {case['id']:<4} {case['domain']:<12} {q_short}", end="", flush=True)

        err_str = ""
        result: dict[str, Any] = {}
        try:
            resp, measured_ms = call_agent(api=api, query=query, ui_lang=ui_lang)
            result = extract_result(resp, measured_ms)
            insuf = result["insufficient_evidence"]
            lat_s = result["latency_ms"] / 1000
            route_str = f"{result['route']}/{result['sub_intent']}" if result["sub_intent"] else result["route"]
            flag = "⚠" if insuf else "✓"
            print(f"  {flag} {route_str:<30} {lat_s:.1f}s")
        except Exception as exc:
            err_str = type(exc).__name__ + ": " + str(exc)[:60]
            print(f"  ✗ {err_str}")

        results.append({"case": case, "result": result, "error": err_str})

    return results


def save_report(results: list[dict[str, Any]]) -> None:
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_cases": len(results),
        "results": [
            {
                "id": r["case"]["id"],
                "domain": r["case"]["domain"],
                "lang": r["case"]["lang"],
                "query": r["case"]["query"],
                "expected_route": r["case"]["expected_route"],
                "expected_sub": r["case"]["expected_sub"],
                "note": r["case"]["note"],
                **r.get("result", {}),
                "error": r.get("error", ""),
            }
            for r in results
        ],
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run() -> int:
    ap = argparse.ArgumentParser(description="Run 50-query bilingual comprehensive test.")
    ap.add_argument("--api", default=API_BASE, help="API base URL")
    args = ap.parse_args()

    results = run_all(api=args.api)
    save_report(results)
    print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
