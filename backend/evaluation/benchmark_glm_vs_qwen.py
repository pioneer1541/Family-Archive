#!/usr/bin/env python3
"""
Benchmark: qwen3:4b-instruct vs hf.co/unsloth/GLM-4.7-Flash-GGUF:Q4_K_M

Tests three dimensions:
  1. Router intent recognition  — accuracy + latency (V2 route_and_rewrite prompt)
  2. Summary generation quality — quality score (0-10) + latency
  3. Raw inference speed        — baseline latency with a simple fixed prompt

GLM is a thinking model. Both think-disable mechanisms are applied:
  - Top-level payload field:  "think": False
  - System message prefix:    "/no_think\\n"  (same as agent_graph_nodes.py)

Run inside fkv-api container:
    docker exec fkv-api python3 evaluation/benchmark_glm_vs_qwen.py \\
        --models "qwen3:4b-instruct" "hf.co/unsloth/GLM-4.7-Flash-GGUF:Q4_K_M" \\
        --measure-rounds 2 \\
        --out /tmp/benchmark_glm_result.json
"""

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

# Allow running from repo root or inside container
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import requests
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import get_settings
settings = get_settings()
from app.db import engine
from app.models import Chunk, Document
from app.services.llm_summary import CATEGORY_PROMPT, DOCUMENT_SUMMARY_PROMPT, FRIENDLY_NAME_PROMPT
from app.services.source_tags import leaf_category_paths, normalize_category_path

# Reuse quality-scoring from existing benchmark script
try:
    from benchmark_summary_models import quality_score, extract_dates, extract_amounts
except ImportError:
    # Inline fallback if script path differs
    _DATE_PATS = [
        re.compile(r"\b20\d{2}[/-](?:0?[1-9]|1[0-2])(?:[/-](?:0?[1-9]|[12]\d|3[01]))?\b"),
        re.compile(r"\b(?:0?[1-9]|1[0-2])[/-]20\d{2}\b"),
        re.compile(r"20\d{2}\s*年\s*(?:0?[1-9]|1[0-2])\s*月"),
    ]
    _AMT_PAT = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
    _TOPIC = ["warranty", "handover", "guide", "fee notice", "invoice", "bill", "lot",
              "strata", "maintenance", "保修", "交接", "指南", "费用", "账单", "发票", "物业", "维护"]
    _BAD = ["已完成文档入库", "分块", "source_type", "ingestion", "queue", "处理状态"]

    def extract_dates(text):
        out = []
        for pat in _DATE_PATS:
            for m in pat.findall(text or ""):
                v = "".join(m) if isinstance(m, tuple) else str(m)
                if v.strip() and v not in out:
                    out.append(v.strip())
                if len(out) >= 8:
                    return out
        return out

    def extract_amounts(text):
        out = []
        for m in _AMT_PAT.findall(text or ""):
            v = " ".join(str(m).split())
            if v and v not in out:
                out.append(v)
            if len(out) >= 8:
                break
        return out

    def quality_score(summary_en, summary_zh, source_text):
        src = source_text or ""
        out = (summary_en or "") + " " + (summary_zh or "")
        dates = extract_dates(src)
        amounts = extract_amounts(src)
        date_hits = sum(1 for d in dates if d in out)
        amount_hits = sum(1 for a in amounts if a in out)
        out_lower = out.lower()
        topic_hits = sum(1 for t in _TOPIC if t in out or t in out_lower)
        bad_hits = sum(1 for b in _BAD if b in out)
        zh_len = sum(1 for c in (summary_zh or "") if "\u4e00" <= c <= "\u9fff")
        en_len = len(re.findall(r"[A-Za-z]", summary_en or ""))
        score = min(3.0, date_hits * 1.0) + min(3.0, amount_hits * 1.5) + min(2.0, topic_hits * 0.5)
        if zh_len >= en_len * 0.5:
            score += 1.0
        score -= bad_hits * 1.0
        score = max(0.0, min(10.0, score))
        return {"score": round(score, 2), "date_hits": date_hits, "date_total": len(dates),
                "amount_hits": amount_hits, "amount_total": len(amounts),
                "topic_hits": topic_hits, "bad_hits": bad_hits,
                "zh_chars": zh_len, "en_alpha_chars": en_len}


# ── V2 Router test cases ───────────────────────────────────────────────────────
# Each tuple: (query, ui_lang, expected_sub_intent)
# Sub-intents from _VALID_SUB_INTENTS in planner.py
ROUTER_TEST_CASES = [
    ("Fluffy的出生日期是什么时候？",        "zh", "entity_fact_lookup"),
    ("2月份电费账单多少钱？",              "zh", "bill_monthly_total"),
    ("帮我查一下网络账单的详细信息",        "zh", "detail_extract"),
    ("今年总共花了多少水费？",              "zh", "period_aggregate"),
    ("谢谢你的帮助",                       "zh", "chitchat"),
    ("What is Fluffy's birth date?",        "en", "entity_fact_lookup"),
    ("January electricity bill amount",    "en", "bill_monthly_total"),
    ("How to maintain the water tank?",    "en", "search_semantic"),
    ("How many pets do I have?",           "en", "entity_fact_lookup"),
    ("你好！",                             "zh", "chitchat"),
    ("重新处理上传失败的文件",              "zh", "reprocess_doc"),
    ("过去6个月的保险费用汇总",            "zh", "period_aggregate"),
    ("家庭医保的联系电话是多少？",          "zh", "entity_fact_lookup"),
    ("查看文档队列状态",                   "zh", "queue_view"),
    ("Show me all insurance coverage details", "en", "detail_extract"),
]

# V2 router system prompt (copied from planner.py to avoid importing private vars)
_V2_ROUTER_SYSTEM_PROMPT = (
    "You are a routing assistant for a private family knowledge vault (家庭知识库).\n"
    "In ONE JSON response: (1) classify the route, (2) rewrite the query for vector search.\n\n"
    "Routes:\n"
    '- "lookup": Searching for facts, policies, how-to, coverage, contact info, documents\n'
    '- "calculate": Bill totals, pending payments, spending aggregation over time\n'
    '- "chitchat": Greetings, off-topic, unrelated to family documents or finances\n'
    '- "system": Queue status, document reprocessing, tag management\n\n'
    "Sub-intents:\n"
    '  lookup  → "detail_extract"     (multiple structured fields from a document)\n'
    '          → "entity_fact_lookup" (ONE specific fact: phone, email, reference number, a single date)\n'
    '          → "search_semantic"    (how-to, general search, anything else)\n'
    '  calculate → "bill_attention"     (list pending/upcoming bills, no specific month)\n'
    '            → "period_aggregate"   (total spending over past N months)\n'
    '            → "bill_monthly_total" (bills for a specific month)\n'
    '  system  → "queue_view" | "reprocess_doc" | "tag_update"\n'
    '  chitchat → "chitchat"\n\n'
    "Disambiguation rules:\n"
    "- bill + specific month → calculate / bill_monthly_total\n"
    "- maintenance / how-to / steps → lookup / search_semantic\n"
    "- contact / phone / email for ONE entity → lookup / entity_fact_lookup\n"
    "- greetings, small talk → chitchat\n"
    "- pet names (e.g. Fluffy, Buddy) + 生日/birthday → lookup / entity_fact_lookup\n\n"
    "Return strictly valid JSON: "
    '{"route":"lookup","rewritten_query":"","domain":"generic","sub_intent":"search_semantic",'
    '"time_window_months":0,"target_month":""}'
)

_VALID_ROUTES = {"lookup", "calculate", "chitchat", "system"}
_VALID_SUB_INTENTS = {
    "detail_extract", "entity_fact_lookup", "search_semantic",
    "bill_attention", "period_aggregate", "bill_monthly_total",
    "queue_view", "reprocess_doc", "tag_update", "chitchat",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_glm(model_name: str) -> bool:
    """Return True if this model requires think-disable."""
    low = model_name.lower()
    return "glm" in low or "unsloth" in low


def _build_payload(model: str, messages: list[dict], extra_options: dict | None = None,
                   fmt: str | None = None, glm_no_format: bool = False) -> dict:
    """Build Ollama /api/chat payload, injecting think=False for GLM models.

    glm_no_format: when True, skip the format constraint for GLM (relies on /no_think
    to produce valid JSON without the overhead of the format sampler).
    """
    payload: dict = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": {"temperature": 0.0, **(extra_options or {})},
    }
    if fmt:
        # In fair mode, skip format for GLM — format constraint can cause long stalls
        # on Chinese-language queries; _extract_json() handles free-form output.
        if not (glm_no_format and _is_glm(model)):
            payload["format"] = fmt
    if _is_glm(model):
        payload["think"] = False
        # Prepend /no_think to the first system message
        for msg in payload["messages"]:
            if msg.get("role") == "system":
                if not str(msg.get("content", "")).startswith("/no_think"):
                    msg["content"] = "/no_think\n" + str(msg.get("content", ""))
                break
    return payload


def _timeout_for(model: str, base: int, mult: float) -> int:
    """Return timeout for model: GLM gets base*mult, others get base."""
    return int(base * mult) if (_is_glm(model) and mult > 1.0) else base


def _prefilter_paths(allowed: list[str], doc: dict, top_n: int = 52) -> list[str]:
    """Return the top_n most keyword-relevant paths for this document."""
    if top_n >= len(allowed):
        return allowed
    text = " ".join([
        doc.get("file_name", "").replace("_", " ").replace("-", " "),
        doc.get("summary_en", ""),
        doc.get("summary_zh", ""),
        doc.get("content_excerpt", "")[:500],
    ]).lower()

    def _score(path: str) -> int:
        return sum(1 for part in path.replace("/", " ").split() if part in text)

    return sorted(allowed, key=_score, reverse=True)[:top_n]


def _extract_json(text: str) -> dict | None:
    """Extract first JSON object from text (handles think-mode <think>...</think> leakage)."""
    # Strip think blocks if any leaked through
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _chat(model: str, messages: list[dict], timeout: int = 30,
          fmt: str | None = None, temp: float = 0.0,
          glm_no_format: bool = False) -> tuple[str, float]:
    """Call Ollama /api/chat. Returns (response_text, latency_ms)."""
    url = settings.ollama_base_url.rstrip("/") + "/api/chat"
    payload = _build_payload(model, messages, extra_options={"temperature": temp},
                             fmt=fmt, glm_no_format=glm_no_format)
    t0 = time.perf_counter()
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        text = str((body.get("message") or {}).get("content") or "")
        latency_ms = (time.perf_counter() - t0) * 1000
        return text, latency_ms
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        return f"ERROR:{exc}", latency_ms


def _p(msg: str) -> None:
    print(msg, flush=True)


# ── Suite 1: Router Intent Recognition ────────────────────────────────────────

def run_router_suite(models: list[str], timeout: int = 15,
                     glm_timeout_mult: float = 1.0, glm_no_format: bool = False) -> dict:
    """Test V2 router intent + sub_intent classification for all test cases."""
    _p("\n[1] ROUTER INTENT RECOGNITION")
    _p(f"    {len(ROUTER_TEST_CASES)} test cases | models: {models}")
    _p("    " + "-" * 70)

    results: dict = {"test_cases": ROUTER_TEST_CASES, "models": {}}

    for model in models:
        _p(f"\n  Model: {model}")
        model_results: list[dict] = []
        latencies: list[float] = []
        _to = _timeout_for(model, timeout, glm_timeout_mult)

        for query, ui_lang, expected_sub_intent in ROUTER_TEST_CASES:
            messages = [
                {"role": "system", "content": _V2_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(
                    {"query": query, "ui_lang": ui_lang, "query_lang": ui_lang},
                    ensure_ascii=False
                )},
            ]
            raw_text, latency_ms = _chat(model, messages, timeout=_to, fmt="json",
                                         temp=0.0, glm_no_format=glm_no_format)
            latencies.append(latency_ms)

            parsed = _extract_json(raw_text)
            got_route = str((parsed or {}).get("route") or "").strip()
            got_sub = str((parsed or {}).get("sub_intent") or "").strip()
            is_route_valid = got_route in _VALID_ROUTES
            is_sub_correct = got_sub == expected_sub_intent

            status = "✓" if is_sub_correct else "✗"
            _p(f"    {status}  [{latency_ms:6.0f}ms]  {query[:40]:<42}"
               f" expected={expected_sub_intent:<20} got={got_sub or '?'}")

            model_results.append({
                "query": query,
                "ui_lang": ui_lang,
                "expected_sub_intent": expected_sub_intent,
                "got_route": got_route,
                "got_sub_intent": got_sub,
                "route_valid": is_route_valid,
                "sub_intent_correct": is_sub_correct,
                "latency_ms": round(latency_ms, 1),
                "raw_response": raw_text[:400],
            })

        correct = sum(1 for r in model_results if r["sub_intent_correct"])
        total = len(model_results)
        avg_lat = statistics.mean(latencies) if latencies else 0
        _p(f"\n    → Accuracy: {correct}/{total}  |  avg latency: {avg_lat:.0f}ms")

        results["models"][model] = {
            "accuracy": f"{correct}/{total}",
            "accuracy_pct": round(correct / total * 100, 1) if total else 0.0,
            "avg_latency_ms": round(avg_lat, 1),
            "p95_latency_ms": round(max(latencies) if len(latencies) < 20
                                    else statistics.quantiles(latencies, n=20)[18], 1),
            "cases": model_results,
        }

    return results


# ── Suite 2: Summary Generation Quality ───────────────────────────────────────

def _load_docs(n: int = 2) -> list[dict]:
    """Fetch n real completed documents with their chunk text from DB."""
    with Session(engine) as sess:
        docs = sess.execute(
            sa.select(Document)
            .where(Document.status == "completed")
            .order_by(Document.updated_at.desc())
            .limit(n)
        ).scalars().all()
        payloads = []
        for doc in docs:
            chunks = sess.execute(
                sa.select(Chunk.content)
                .where(Chunk.document_id == doc.id)
                .order_by(Chunk.chunk_index)
            ).scalars().all()
            text = "\n".join(str(c) for c in chunks if c)
            payloads.append({
                "file_name": doc.file_name,
                "title_en": doc.title_en or "",
                "title_zh": doc.title_zh or "",
                "category_label_en": doc.category_label_en or "",
                "category_label_zh": doc.category_label_zh or "",
                "text": text[:9000],
            })
    return payloads


def _load_docs_enriched(n: int = 10) -> list[dict]:
    """Load n completed docs with summaries + first chunk excerpt (for category/naming suites)."""
    with Session(engine) as sess:
        docs = sess.execute(
            sa.select(Document)
            .where(
                Document.status == "completed",
                Document.summary_en != "",
                Document.summary_zh != "",
                Document.category_path != "archive/misc",
            )
            .order_by(Document.updated_at.desc())
            .limit(n)
        ).scalars().all()
        payloads = []
        for doc in docs:
            first_chunk = sess.execute(
                sa.select(Chunk.content)
                .where(Chunk.document_id == doc.id)
                .order_by(Chunk.chunk_index)
                .limit(1)
            ).scalar_one_or_none()
            payloads.append({
                "file_name": doc.file_name,
                "title_en": doc.title_en or "",
                "title_zh": doc.title_zh or "",
                "summary_en": doc.summary_en or "",
                "summary_zh": doc.summary_zh or "",
                "category_path": doc.category_path or "archive/misc",
                "category_label_en": doc.category_label_en or "",
                "category_label_zh": doc.category_label_zh or "",
                "content_excerpt": str(first_chunk or "")[:2200],
            })
    return payloads


def run_summary_suite(models: list[str], docs: list[dict], warmup: int = 1,
                      measure: int = 2, timeout: int = 90) -> dict:
    """Test summary generation quality and latency."""
    _p("\n[2] SUMMARY GENERATION QUALITY")
    _p(f"    {len(docs)} documents | warmup={warmup} | measure={measure} | models: {models}")
    _p("    " + "-" * 70)

    results: dict = {"docs": [d["file_name"] for d in docs], "models": {}}

    for model in models:
        _p(f"\n  Model: {model}")
        all_latencies: list[float] = []
        all_scores: list[float] = []
        doc_results: list[dict] = []

        for doc in docs:
            _p(f"    Doc: {doc['file_name'][:55]}")
            user_payload = {
                "title_en": doc["title_en"],
                "title_zh": doc["title_zh"],
                "category_en": doc["category_label_en"],
                "category_zh": doc["category_label_zh"],
                "content": doc["text"],
                "constraints": {
                    "max_chars_per_lang": 650,
                    "style_zh": "中文为主，提炼重点，给出可执行下一步",
                    "style_en": "concise analytical summary",
                },
            }
            messages = [
                {"role": "system", "content": DOCUMENT_SUMMARY_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]

            # Warmup
            for _ in range(warmup):
                _chat(model, messages, timeout=timeout, fmt="json", temp=0.05)

            # Measure
            runs: list[dict] = []
            for rnd in range(measure):
                raw_text, latency_ms = _chat(model, messages, timeout=timeout, fmt="json", temp=0.05)
                parsed = _extract_json(raw_text)
                summary_en = str((parsed or {}).get("summary_en") or "")
                summary_zh = str((parsed or {}).get("summary_zh") or "")
                qs = quality_score(summary_en, summary_zh, doc["text"])
                all_latencies.append(latency_ms)
                all_scores.append(float(qs["score"]))
                runs.append({
                    "round": rnd + 1,
                    "latency_ms": round(latency_ms, 1),
                    "summary_en": summary_en[:300],
                    "summary_zh": summary_zh[:300],
                    "quality": qs,
                })
                _p(f"      round {rnd+1}: {latency_ms:6.0f}ms  score={qs['score']:.1f}  "
                   f"dates={qs['date_hits']}/{qs['date_total']}  "
                   f"amounts={qs['amount_hits']}/{qs['amount_total']}")

            best = max(runs, key=lambda r: (r["quality"]["score"], -r["latency_ms"]))
            doc_results.append({"file_name": doc["file_name"], "runs": runs, "best_run": best})

        avg_lat = statistics.mean(all_latencies) if all_latencies else 0
        avg_score = statistics.mean(all_scores) if all_scores else 0
        _p(f"\n    → avg quality score: {avg_score:.2f}/10  |  avg latency: {avg_lat/1000:.1f}s")

        results["models"][model] = {
            "avg_quality_score": round(avg_score, 2),
            "avg_latency_ms": round(avg_lat, 1),
            "p95_latency_ms": round(max(all_latencies) if len(all_latencies) < 20
                                    else statistics.quantiles(all_latencies, n=20)[18], 1),
            "doc_results": doc_results,
        }

    return results


# ── Suite 3: Raw Inference Speed ───────────────────────────────────────────────

_SPEED_PROMPT = "用一句话描述家庭文档管理系统的用途。"

def run_speed_suite(models: list[str], warmup: int = 1, measure: int = 3,
                    timeout: int = 30) -> dict:
    """Measure raw inference speed with a fixed simple prompt."""
    _p("\n[3] RAW INFERENCE SPEED")
    _p(f"    prompt: '{_SPEED_PROMPT}' | warmup={warmup} | measure={measure}")
    _p("    " + "-" * 70)

    results: dict = {"prompt": _SPEED_PROMPT, "models": {}}
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": _SPEED_PROMPT},
    ]

    for model in models:
        _p(f"\n  Model: {model}")
        for _ in range(warmup):
            _chat(model, messages, timeout=timeout, temp=0.1)

        latencies: list[float] = []
        for rnd in range(measure):
            _, lat = _chat(model, messages, timeout=timeout, temp=0.1)
            latencies.append(lat)
            _p(f"    round {rnd+1}: {lat:6.0f}ms")

        avg = statistics.mean(latencies)
        mn = min(latencies)
        mx = max(latencies)
        _p(f"    → avg={avg:.0f}ms  min={mn:.0f}ms  max={mx:.0f}ms")

        results["models"][model] = {
            "avg_ms": round(avg, 1),
            "min_ms": round(mn, 1),
            "max_ms": round(mx, 1),
            "runs_ms": [round(x, 1) for x in latencies],
        }

    return results


# ── Suite 4: Category Classification Accuracy ──────────────────────────────────

def run_category_suite(models: list[str], docs: list[dict], timeout: int = 30,
                       glm_timeout_mult: float = 1.0, glm_no_format: bool = False,
                       prefilter_n: int = 52) -> dict:
    """Suite 4: Category classification accuracy vs. ground truth from DB."""
    all_allowed = [p for p in leaf_category_paths(include_archive_misc=False) if p != "archive/misc"]

    _p("\n[4] CATEGORY CLASSIFICATION ACCURACY")
    _p(f"    {len(docs)} documents | {len(all_allowed)} allowed paths (prefilter_n={prefilter_n}) | models: {models}")
    _p("    " + "-" * 70)

    results: dict = {"doc_count": len(docs), "allowed_count": len(all_allowed),
                     "prefilter_n": prefilter_n, "models": {}}

    for model in models:
        _p(f"\n  Model: {model}")
        model_results: list[dict] = []
        latencies: list[float] = []
        _to = _timeout_for(model, timeout, glm_timeout_mult)

        for doc in docs:
            allowed = _prefilter_paths(all_allowed, doc, top_n=prefilter_n)
            user_payload = {
                "file_name": doc["file_name"],
                "source_type": "nas",
                "summary_en": str(doc["summary_en"])[:1600],
                "summary_zh": str(doc["summary_zh"])[:1600],
                "content_excerpt": str(doc["content_excerpt"])[:2200],
                "allowed_category_paths": allowed,
                "default_if_uncertain": "archive/misc",
                "must_return_leaf": True,
            }
            messages = [
                {"role": "system", "content": CATEGORY_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
            raw_text, latency_ms = _chat(model, messages, timeout=_to, fmt="json",
                                         temp=0.05, glm_no_format=glm_no_format)
            latencies.append(latency_ms)

            parsed = _extract_json(raw_text)
            got_path = normalize_category_path(str((parsed or {}).get("category_path") or ""))
            expected_path = str(doc["category_path"])
            is_correct = got_path == expected_path
            in_allowed = got_path in set(allowed)

            status = "✓" if is_correct else "✗"
            _p(f"    {status}  [{latency_ms:6.0f}ms]  {doc['file_name'][:38]:<40}"
               f"  expected={expected_path[:25]:<27} got={got_path or '?'}")

            model_results.append({
                "file_name": doc["file_name"],
                "expected": expected_path,
                "got": got_path,
                "correct": is_correct,
                "in_allowed": in_allowed,
                "latency_ms": round(latency_ms, 1),
            })

        correct = sum(1 for r in model_results if r["correct"])
        total = len(model_results)
        avg_lat = statistics.mean(latencies) if latencies else 0
        _p(f"\n    → Accuracy: {correct}/{total} ({correct/total*100:.0f}%)  |  avg latency: {avg_lat:.0f}ms")

        results["models"][model] = {
            "accuracy": f"{correct}/{total}",
            "accuracy_pct": round(correct / total * 100, 1) if total else 0.0,
            "avg_latency_ms": round(avg_lat, 1),
            "cases": model_results,
        }

    return results


# ── Suite 5: Friendly Name Quality ─────────────────────────────────────────────

_DIRTY_WORDS = {"ingestion", "chunk", "hash", "pipeline", "source_type"}


def run_friendly_name_suite(models: list[str], docs: list[dict], timeout: int = 30,
                            glm_timeout_mult: float = 1.0, glm_no_format: bool = False) -> dict:
    """Suite 5: Friendly name generation quality (5-dimension scoring)."""
    _p("\n[5] FRIENDLY NAME QUALITY")
    _p(f"    {len(docs)} documents | models: {models}")
    _p("    " + "-" * 70)

    results: dict = {"doc_count": len(docs), "models": {}}

    for model in models:
        _p(f"\n  Model: {model}")
        all_scores: list[float] = []
        latencies: list[float] = []
        doc_results: list[dict] = []
        _to = _timeout_for(model, timeout, glm_timeout_mult)

        for doc in docs:
            user_payload = {
                "file_name": doc["file_name"],
                "category_path": doc["category_path"],
                "summary_en": str(doc["summary_en"])[:1600],
                "summary_zh": str(doc["summary_zh"])[:1600],
                "fallback_en": doc["title_en"] or doc["file_name"],
                "fallback_zh": doc["title_zh"] or doc["file_name"],
                "constraints": {"max_chars": 80, "must_match_category": True},
            }
            messages = [
                {"role": "system", "content": FRIENDLY_NAME_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
            raw_text, latency_ms = _chat(model, messages, timeout=_to, fmt="json",
                                         temp=0.05, glm_no_format=glm_no_format)
            latencies.append(latency_ms)

            parsed = _extract_json(raw_text)
            name_en = str((parsed or {}).get("friendly_name_en") or "").strip()
            name_zh = str((parsed or {}).get("friendly_name_zh") or "").strip()

            # 5 scoring dimensions (0-1 each)
            s_length = 1 if name_en and len(name_en) <= 80 else 0
            s_has_zh = 1 if sum(1 for c in name_zh if "\u4e00" <= c <= "\u9fff") >= 2 else 0
            s_has_en = 1 if len(re.findall(r"[A-Za-z]", name_en)) >= 2 else 0
            proper_nouns = re.findall(r"\b[A-Z][A-Za-z]{1,}\b",
                                      doc["file_name"].replace("_", " ").replace("-", " "))
            s_proper = 1 if (not proper_nouns or any(pn in name_en for pn in proper_nouns[:3])) else 0
            s_no_dirty = 1 if not any(d in (name_en + " " + name_zh).lower() for d in _DIRTY_WORDS) else 0

            score = float(s_length + s_has_zh + s_has_en + s_proper + s_no_dirty)
            all_scores.append(score)

            _p(f"    [{latency_ms:6.0f}ms]  score={score:.0f}/5  en: {name_en[:35]!r:37}  zh: {name_zh[:20]!r}")
            doc_results.append({
                "file_name": doc["file_name"],
                "name_en": name_en,
                "name_zh": name_zh,
                "score": score,
                "dim": {
                    "length_ok": bool(s_length), "has_zh": bool(s_has_zh),
                    "has_en": bool(s_has_en), "proper_nouns": bool(s_proper),
                    "no_dirty_words": bool(s_no_dirty),
                },
                "latency_ms": round(latency_ms, 1),
            })

        avg_lat = statistics.mean(latencies) if latencies else 0
        avg_score = statistics.mean(all_scores) if all_scores else 0
        _p(f"\n    → avg quality: {avg_score:.2f}/5  |  avg latency: {avg_lat:.0f}ms")

        results["models"][model] = {
            "avg_score": round(avg_score, 2),
            "avg_latency_ms": round(avg_lat, 1),
            "doc_results": doc_results,
        }

    return results


# ── Suite 6: Synthesizer Quality ───────────────────────────────────────────────

_SYNTH_SCENARIOS = [
    {
        "name": "search_semantic — AAMI insurance claim",
        "req": {"query": "AAMI车险的理赔流程是什么？", "ui_lang": "zh"},
        "planner": {"intent": "search_semantic", "confidence": 0.9, "ui_lang": "zh"},
        "bundle": {
            "route": "search_semantic",
            "hit_count": 2, "doc_count": 2, "bilingual_search": True,
            "answerability": "sufficient",
            "coverage_ratio": 0.8, "field_coverage_ratio": 1.0,
            "context_chunks": [
                {
                    "doc_id": "mock-001", "chunk_id": "c001",
                    "title_en": "AAMI Vehicle Insurance Policy", "title_zh": "AAMI车险保单",
                    "category_path": "home/insurance/vehicle", "score": 0.91,
                    "text": (
                        "To make a claim with AAMI, call 13 22 44 or log in to my.AAMI.com.au. "
                        "For vehicle claims, provide: date of incident, policy number, "
                        "details of other parties, and photos of the damage."
                    ),
                },
                {
                    "doc_id": "mock-001", "chunk_id": "c002",
                    "title_en": "AAMI Vehicle Insurance Policy", "title_zh": "AAMI车险保单",
                    "category_path": "home/insurance/vehicle", "score": 0.85,
                    "text": (
                        "Claim assessment: AAMI will assess your claim within 10 business days. "
                        "Approved repairs are arranged through AAMI's approved repairer network."
                    ),
                },
            ],
        },
    },
    {
        "name": "entity_fact_lookup — pet birthday",
        "req": {"query": "Fluffy的出生日期", "ui_lang": "zh"},
        "planner": {"intent": "entity_fact_lookup", "confidence": 0.95, "ui_lang": "zh"},
        "bundle": {
            "route": "entity_fact_lookup",
            "hit_count": 1, "doc_count": 1, "bilingual_search": False,
            "answerability": "sufficient",
            "coverage_ratio": 1.0, "field_coverage_ratio": 1.0,
            "detail_topic": "pets",
            "detail_sections": [
                {
                    "section_name": "宠物信息",
                    "rows": [{
                        "field": "birth_date", "label_en": "Birth Date", "label_zh": "出生日期",
                        "value_en": "04 November 2024", "value_zh": "2024年11月4日",
                        "evidence_refs": [{"doc_id": "mock-002", "chunk_id": "c003",
                                           "evidence_text": "DOB: 04 Nov 2024"}],
                    }],
                }
            ],
            "context_chunks": [],
        },
    },
    {
        "name": "bill_monthly_total — electricity Feb 2026",
        "req": {"query": "2月份的电费是多少？", "ui_lang": "zh"},
        "planner": {"intent": "bill_monthly_total", "confidence": 0.98, "ui_lang": "zh"},
        "bundle": {
            "route": "bill_monthly_total",
            "hit_count": 1, "doc_count": 1, "bilingual_search": False,
            "answerability": "sufficient",
            "coverage_ratio": 1.0, "field_coverage_ratio": 1.0,
            "bill_monthly": {
                "month": "2026-02", "total_aud": 123.45, "item_count": 1,
                "bills": [{
                    "doc_id": "mock-003", "file_name": "AGL_Feb2026_Electricity.pdf",
                    "amount_aud": 123.45, "due_date": "2026-03-15", "paid": False,
                }],
            },
            "context_chunks": [],
        },
    },
]


def _build_synth_messages(scenario: dict) -> list[dict]:
    """Build synthesizer messages equivalent to production _synth_prompt()."""
    req_data = scenario["req"]
    planner_data = scenario["planner"]
    bundle = scenario["bundle"]
    ui_lang = req_data.get("ui_lang", "zh")

    if ui_lang == "zh":
        route_rules = (
            "ROUTE RULES:\n"
            "- search_semantic: write a narrative answer in Chinese from the chunks; skip detail_sections.\n"
            "- detail_extract / entity_fact_lookup: keep short_summary as one sentence; "
            "preserve the structured detail_sections from executor data.\n"
            "- bill_monthly_total: organize as sections 月度总额 / 待缴账单 / 已缴账单; "
            "include amounts and due dates.\n"
        )
        lang_rule = "Respond in natural Chinese. Always populate BOTH short_summary.en AND short_summary.zh.\n"
        insufficient_rule = (
            "INSUFFICIENT EVIDENCE: if answerability is 'insufficient' or 'none', "
            "write short_summary.zh stating clearly what information is missing.\n"
        )
    else:
        route_rules = (
            "ROUTE RULES:\n"
            "- search_semantic: write a narrative answer in English from the chunks; skip detail_sections.\n"
            "- detail_extract / entity_fact_lookup: keep short_summary as one sentence; "
            "preserve the structured detail_sections.\n"
            "- bill_monthly_total: organize as sections Monthly Total / Pending Bills / Paid Bills.\n"
        )
        lang_rule = "Respond in natural English. Always populate BOTH short_summary.en AND short_summary.zh.\n"
        insufficient_rule = (
            "INSUFFICIENT EVIDENCE: if answerability is 'insufficient' or 'none', "
            "state directly what is missing in short_summary.en.\n"
        )

    system_content = (
        "You are a Synthesizer model for a private family knowledge vault. Return ONLY valid JSON.\n\n"
        "EVIDENCE POLICY:\n"
        "- Use ONLY the data provided. Never invent amounts, dates, names, or policy numbers.\n"
        + insufficient_rule
        + "\nOUTPUT RULES:\n"
        + lang_rule
        + "- key_points: 2-4 bilingual bullet points with concrete facts.\n"
        "- NEVER copy raw boilerplate text into short_summary.\n"
        "- Do not mention 'chunks', 'pipeline', 'model', or internal system words.\n\n"
        + route_rules
        + "\nOutput schema (JSON only, no markdown):\n"
        '{"title":"...","short_summary":{"en":"...","zh":"..."},'
        '"key_points":[{"en":"...","zh":"..."}],'
        '"detail_sections":[],"missing_fields":[],'
        '"coverage_stats":{"docs_scanned":0,"docs_matched":0,"fields_filled":0},"actions":[]}'
    )

    chunks = bundle.get("context_chunks") or []
    chunk_payload = [
        {
            "doc_id": str(c.get("doc_id") or ""), "chunk_id": str(c.get("chunk_id") or ""),
            "title_en": str(c.get("title_en") or ""), "title_zh": str(c.get("title_zh") or ""),
            "category_path": str(c.get("category_path") or ""),
            "score": float(c.get("score") or 0.0),
            "text": str(c.get("text") or "")[:420],
        }
        for c in chunks[:10]
    ]

    user_payload = {
        "query": req_data["query"],
        "target_ui_lang": ui_lang,
        "planner": planner_data,
        "route": bundle.get("route", "search_semantic"),
        "stats": {
            "hit_count": int(bundle.get("hit_count") or 0),
            "doc_count": int(bundle.get("doc_count") or 0),
            "bilingual_search": bool(bundle.get("bilingual_search")),
        },
        "bill_attention": bundle.get("bill_attention") or {},
        "bill_monthly": bundle.get("bill_monthly") or {},
        "detail_topic": str(bundle.get("detail_topic") or ""),
        "detail_sections": bundle.get("detail_sections") or [],
        "missing_fields": bundle.get("missing_fields") or [],
        "coverage_stats": bundle.get("coverage_stats") or {},
        "answerability": str(bundle.get("answerability") or "sufficient"),
        "required_evidence_fields": bundle.get("required_evidence_fields") or [],
        "coverage_ratio": float(bundle.get("coverage_ratio") or 1.0),
        "field_coverage_ratio": float(bundle.get("field_coverage_ratio") or 1.0),
        "conversation": [],
        "chunks": chunk_payload,
    }

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _score_synth(parsed: dict | None, scenario: dict) -> dict:
    """Score synthesizer output on 5 dimensions (each 0-1, total 0-5)."""
    if parsed is None:
        return {
            "score": 0, "valid_json": False, "short_summary_ok": False,
            "key_points_ok": False, "zh_quality": False, "no_refusal": False,
            "short_summary_en": "", "short_summary_zh": "",
        }
    ss = parsed.get("short_summary") or {}
    en = str(ss.get("en") or "").strip()
    zh = str(ss.get("zh") or "").strip()
    kp = parsed.get("key_points") or []

    short_summary_ok = bool(en) and bool(zh)
    key_points_ok = isinstance(kp, list) and len(kp) > 0
    zh_quality = sum(1 for c in zh if "\u4e00" <= c <= "\u9fff") >= 15
    _refusal = ["i don't know", "i cannot", "无法确定", "没有足够信息", "不知道", "无相关"]
    no_refusal = (
        not any(p in (en + " " + zh).lower() for p in _refusal)
        if scenario["bundle"].get("answerability") == "sufficient"
        else True
    )

    score = sum([True, short_summary_ok, key_points_ok, zh_quality, no_refusal])
    return {
        "score": score, "valid_json": True,
        "short_summary_ok": short_summary_ok, "key_points_ok": key_points_ok,
        "zh_quality": zh_quality, "no_refusal": no_refusal,
        "short_summary_en": en[:150], "short_summary_zh": zh[:150],
    }


def run_synthesizer_suite(models: list[str], timeout: int = 40,
                          glm_timeout_mult: float = 1.0, glm_no_format: bool = False) -> dict:
    """Suite 6: Synthesizer quality across 3 fixed mock scenarios."""
    _p("\n[6] SYNTHESIZER QUALITY (3 mock scenarios)")
    _p(f"    models: {models}")
    _p("    " + "-" * 70)

    results: dict = {"scenarios": [s["name"] for s in _SYNTH_SCENARIOS], "models": {}}

    for model in models:
        _p(f"\n  Model: {model}")
        all_scores: list[float] = []
        latencies: list[float] = []
        scenario_results: list[dict] = []
        _to = _timeout_for(model, timeout, glm_timeout_mult)

        for scenario in _SYNTH_SCENARIOS:
            messages = _build_synth_messages(scenario)
            raw_text, latency_ms = _chat(model, messages, timeout=_to, fmt="json",
                                         temp=0.1, glm_no_format=glm_no_format)
            latencies.append(latency_ms)

            parsed = _extract_json(raw_text)
            score_info = _score_synth(parsed, scenario)
            all_scores.append(float(score_info["score"]))

            status = "✓" if score_info["score"] >= 4 else ("~" if score_info["score"] >= 2 else "✗")
            _p(f"    {status}  [{latency_ms:6.0f}ms]  {scenario['name']:<48}  score={score_info['score']}/5")
            if score_info.get("short_summary_zh"):
                _p(f"         zh: {score_info['short_summary_zh'][:80]}")

            scenario_results.append({
                "scenario": scenario["name"],
                "latency_ms": round(latency_ms, 1),
                **score_info,
            })

        avg_lat = statistics.mean(latencies) if latencies else 0
        avg_score = statistics.mean(all_scores) if all_scores else 0
        _p(f"\n    → avg quality: {avg_score:.2f}/5  |  avg latency: {avg_lat:.0f}ms")

        results["models"][model] = {
            "avg_score": round(avg_score, 2),
            "avg_latency_ms": round(avg_lat, 1),
            "scenario_results": scenario_results,
        }

    return results


# ── Summary Report ─────────────────────────────────────────────────────────────

def print_summary(
    router_r: dict, summary_r: dict, speed_r: dict, models: list[str],
    category_r: dict | None = None,
    friendly_r: dict | None = None,
    synth_r: dict | None = None,
) -> None:
    _p("\n" + "=" * 72)
    _p("  BENCHMARK SUMMARY")
    _p("=" * 72)

    _p("\n  [1] ROUTER ACCURACY")
    header = f"  {'Model':<52} {'Accuracy':>10}  {'Avg ms':>8}"
    _p(header)
    _p("  " + "-" * 72)
    for m in models:
        mr = router_r["models"].get(m, {})
        _p(f"  {m:<52} {mr.get('accuracy','?/15'):>10}  {mr.get('avg_latency_ms', 0):>8.0f}")

    _p("\n  [2] SUMMARY QUALITY")
    header2 = f"  {'Model':<52} {'Score/10':>9}  {'Avg sec':>8}"
    _p(header2)
    _p("  " + "-" * 72)
    for m in models:
        mr = summary_r["models"].get(m, {})
        avg_s = mr.get("avg_latency_ms", 0) / 1000
        _p(f"  {m:<52} {mr.get('avg_quality_score', 0):>9.2f}  {avg_s:>8.1f}")

    _p("\n  [3] RAW SPEED")
    header3 = f"  {'Model':<52} {'Avg ms':>8}  {'Min ms':>8}  {'Max ms':>8}"
    _p(header3)
    _p("  " + "-" * 72)
    for m in models:
        mr = speed_r["models"].get(m, {})
        _p(f"  {m:<52} {mr.get('avg_ms', 0):>8.0f}  {mr.get('min_ms', 0):>8.0f}  {mr.get('max_ms', 0):>8.0f}")

    if category_r:
        _p("\n  [4] CATEGORY ACCURACY")
        h4 = f"  {'Model':<52} {'Accuracy':>10}  {'Avg ms':>8}"
        _p(h4)
        _p("  " + "-" * 72)
        for m in models:
            mr = category_r["models"].get(m, {})
            _p(f"  {m:<52} {mr.get('accuracy','?'):>10}  {mr.get('avg_latency_ms', 0):>8.0f}")

    if friendly_r:
        _p("\n  [5] FRIENDLY NAME QUALITY")
        h5 = f"  {'Model':<52} {'Score/5':>8}  {'Avg ms':>8}"
        _p(h5)
        _p("  " + "-" * 72)
        for m in models:
            mr = friendly_r["models"].get(m, {})
            _p(f"  {m:<52} {mr.get('avg_score', 0):>8.2f}  {mr.get('avg_latency_ms', 0):>8.0f}")

    if synth_r:
        _p("\n  [6] SYNTHESIZER QUALITY")
        h6 = f"  {'Model':<52} {'Score/5':>8}  {'Avg ms':>8}"
        _p(h6)
        _p("  " + "-" * 72)
        for m in models:
            mr = synth_r["models"].get(m, {})
            _p(f"  {m:<52} {mr.get('avg_score', 0):>8.2f}  {mr.get('avg_latency_ms', 0):>8.0f}")

    _p("\n" + "=" * 72)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark two Ollama models across router, summary, and speed.")
    parser.add_argument(
        "--models", nargs="+",
        default=["qwen3:4b-instruct", "hf.co/unsloth/GLM-4.7-Flash-GGUF:Q4_K_M"],
        help="Models to compare"
    )
    parser.add_argument("--measure-rounds", type=int, default=2)
    parser.add_argument("--warmup-rounds", type=int, default=1)
    parser.add_argument("--summary-docs", type=int, default=2, help="Number of real docs to use for summary test")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    parser.add_argument("--skip-summary", action="store_true", help="Skip summary suite (faster run)")
    parser.add_argument("--skip-extended", action="store_true",
                        help="Skip Suites 4/5/6 (category, friendly name, synthesizer)")
    parser.add_argument("--category-docs", type=int, default=10,
                        help="Number of docs for Suite 4 category accuracy")
    parser.add_argument("--naming-docs", type=int, default=8,
                        help="Number of docs for Suite 5 friendly name quality")
    parser.add_argument("--fair", action="store_true",
                        help=(
                            "Fair-for-GLM mode: (1) skip format constraint for GLM so "
                            "Chinese queries don't stall, (2) GLM gets 3× base timeout, "
                            "(3) category paths pre-filtered to top 15 per doc."
                        ))
    args = parser.parse_args()

    # Fair-mode parameters
    glm_no_format = bool(args.fair)
    glm_timeout_mult = 3.0 if args.fair else 1.0
    prefilter_n = 15 if args.fair else 52

    models = list(args.models)
    _p(f"\n=== BENCHMARK: {' vs '.join(models)} ===")
    mode_label = " [FAIR MODE: no-format + 3× timeout + prefilter-15]" if args.fair else ""
    _p(f"    measure_rounds={args.measure_rounds}  warmup={args.warmup_rounds}{mode_label}")
    for m in models:
        _p(f"    {'[GLM think-disabled]' if _is_glm(m) else '[standard]':<25} {m}")

    router_r = run_router_suite(models, timeout=20,
                                glm_timeout_mult=glm_timeout_mult, glm_no_format=glm_no_format)

    if args.skip_summary:
        _p("\n[2] SUMMARY QUALITY  — SKIPPED (--skip-summary)")
        summary_r: dict = {"docs": [], "models": {m: {"avg_quality_score": 0, "avg_latency_ms": 0, "doc_results": []} for m in models}}
    else:
        docs = _load_docs(n=args.summary_docs)
        if not docs:
            _p("\n[2] SUMMARY QUALITY  — SKIPPED (no completed documents in DB)")
            summary_r = {"docs": [], "models": {m: {"avg_quality_score": 0, "avg_latency_ms": 0, "doc_results": []} for m in models}}
        else:
            summary_r = run_summary_suite(models, docs, warmup=args.warmup_rounds, measure=args.measure_rounds)

    speed_r = run_speed_suite(models, warmup=args.warmup_rounds, measure=args.measure_rounds + 1)

    category_r: dict | None = None
    friendly_r: dict | None = None
    synth_r: dict | None = None

    if not args.skip_extended:
        enriched_docs = _load_docs_enriched(n=max(args.category_docs, args.naming_docs))
        cat_docs = enriched_docs[:args.category_docs]
        name_docs = enriched_docs[:args.naming_docs]

        if not enriched_docs:
            _p("\n[4-5] CATEGORY / FRIENDLY NAME — SKIPPED (no completed docs with summaries)")
        else:
            category_r = run_category_suite(
                models, cat_docs, timeout=30,
                glm_timeout_mult=glm_timeout_mult, glm_no_format=glm_no_format,
                prefilter_n=prefilter_n,
            )
            friendly_r = run_friendly_name_suite(
                models, name_docs, timeout=30,
                glm_timeout_mult=glm_timeout_mult, glm_no_format=glm_no_format,
            )

        synth_r = run_synthesizer_suite(
            models, timeout=40,
            glm_timeout_mult=glm_timeout_mult, glm_no_format=glm_no_format,
        )

    print_summary(router_r, summary_r, speed_r, models,
                  category_r=category_r, friendly_r=friendly_r, synth_r=synth_r)

    report = {
        "models": models,
        "measure_rounds": args.measure_rounds,
        "router": router_r,
        "summary": summary_r,
        "speed": speed_r,
        "category": category_r,
        "friendly_name": friendly_r,
        "synthesizer": synth_r,
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if str(args.out or "").strip():
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        _p(f"\n  Results saved to: {args.out}")
    else:
        _p("\n--- Full JSON report (add --out <path> to save) ---")
        _p(payload)


if __name__ == "__main__":
    main()
