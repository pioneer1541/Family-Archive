import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from app.config import get_settings
from app.schemas import PlannerDecision, PlannerRequest
from app.services.agent_queryspec import apply_query_spec_to_planner_fields, build_query_spec_from_query


settings = get_settings()

_INTENT_RULES = [
    (
        "detail_extract",
        [
            "列出",
            "明细",
            "详细",
            "所有",
            "细节",
            "policy details",
            "line items",
            "breakdown",
            "list all",
            "what's covered",
            "coverage",
            "maintenance steps",
            "how to maintain",
        ],
    ),
    (
        "period_aggregate",
        [
            "过去",
            "近",
            "平均",
            "总共",
            "合计",
            "上季度",
            "上个月",
            "本月",
            "每月",
            "近月",
            "trend",
            "average",
            "total",
            "last quarter",
            "last month",
            "this month",
            "past six months",
            "past 6 months",
            "current energy bills",
        ],
    ),
    (
        "entity_fact_lookup",
        [
            "是什么",
            "哪家",
            "哪个",
            "联系方式",
            "电话",
            "邮箱",
            "品牌",
            "型号",
            "保单号",
            "发票号",
            "工单号",
            "证号",
            "when",
            "contact",
            "phone",
            "email",
            "model",
            "serial",
            "birthday",
            "birth date",
            "dob",
            "which company",
            "provider",
            "contact details",
        ],
    ),
    ("summarize_docs", ["总结", "摘要", "summarize", "summary"]),
    ("compare_docs", ["比较", "对比", "compare"]),
    ("extract_fields", ["提取", "字段", "extract"]),
    ("timeline_build", ["时间线", "timeline"]),
    ("list_recent", ["最近", "latest", "recent", "current bills", "current gas bills", "current bill", "outstanding bills"]),
    ("list_by_category", ["分类", "category"]),
    ("open_document", ["打开", "open document"]),
    ("queue_view", ["队列", "queue"]),
    ("tag_update", ["标签", "tag"]),
    ("reprocess_doc", ["重处理", "reprocess"]),
    ("search_keyword", ["关键字", "keyword"]),
]

_INTENT_ACTIONS = {
    "detail_extract": ["extract_details", "retrieve_docs"],
    "period_aggregate": ["search_documents", "extract_fields"],
    "entity_fact_lookup": ["search_documents", "extract_fields"],
    "summarize_docs": ["retrieve_docs", "summarize_docs"],
    "compare_docs": ["retrieve_docs", "compare_docs"],
    "extract_fields": ["retrieve_docs", "extract_fields"],
    "timeline_build": ["retrieve_docs", "timeline_extract"],
    "list_by_category": ["list_by_category"],
    "list_recent": ["list_recent"],
    "open_document": ["retrieve_docs"],
    "reprocess_doc": ["queue_ops"],
    "queue_view": ["queue_ops"],
    "tag_update": ["tag_update"],
    "search_keyword": ["search_documents"],
    "search_semantic": ["search_documents"],
}

_ALLOWED_INTENTS = set(_INTENT_ACTIONS.keys())
_JSON_BLOCK = re.compile(r"\{.*\}", flags=re.S)


def _is_zh(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _pick_intent_rule(query: str) -> tuple[str, float]:
    text = str(query or "").strip().lower()
    if not text:
        return ("search_semantic", 0.0)
    for intent, keys in _INTENT_RULES:
        if any(key.lower() in text for key in keys):
            if intent == "detail_extract":
                topic_hits = any(
                    token in text
                    for token in (
                        "insurance",
                        "policy",
                        "保单",
                        "保险",
                        "账单",
                        "bill",
                        "warranty",
                        "保修",
                        "合同",
                        "contract",
                        "coverage",
                        "covered",
                        "maintain",
                        "maintenance",
                        "birthday",
                        "birth date",
                    )
                )
                return (intent, 0.81 if topic_hits else 0.7)
            return (intent, 0.74)
    if len(text) <= 4:
        return ("search_keyword", 0.58)
    return ("search_semantic", 0.52)


def _safe_query_lang(req: PlannerRequest) -> str:
    if req.query_lang in {"zh", "en"}:
        return req.query_lang
    return "zh" if _is_zh(req.query) else "en"


def _clamp_confidence(value: Any) -> float:
    try:
        num = float(value)
    except Exception:
        num = 0.0
    return max(0.0, min(1.0, num))


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    matched = _JSON_BLOCK.search(raw)
    if not matched:
        return {}
    try:
        parsed = json.loads(matched.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _build_fallback_plan(req: PlannerRequest) -> PlannerDecision:
    intent, confidence = _pick_intent_rule(req.query)
    query_lang = _safe_query_lang(req)
    fallback = "search_semantic" if confidence < 0.55 else intent
    text = str(req.query or "").strip().lower()
    required_evidence_fields: list[str] = []
    if any(token in text for token in ("多少钱", "金额", "total", "sum", "费用")):
        required_evidence_fields.append("amount")
    if any(token in text for token in ("什么时候", "到期", "日期", "date", "when")):
        required_evidence_fields.append("date")
    if any(token in text for token in ("联系方式", "电话", "邮箱", "contact", "phone", "email")):
        required_evidence_fields.append("contact")
    if any(token in text for token in ("有没有", "是否", "有无", "do we have", "did we", "have we")):
        required_evidence_fields.append("explicit_presence_evidence")
    spec = build_query_spec_from_query(req.query, planner_intent=intent, doc_scope=req.doc_scope)
    planner_payload = {
        "intent": intent,
        "confidence": round(confidence, 2),
        "doc_scope": req.doc_scope,
        "actions": _INTENT_ACTIONS.get(intent, ["search_documents"]),
        "fallback": fallback,
        "ui_lang": req.ui_lang,
        "query_lang": query_lang,
        "route_reason": "fallback_rule",
        "required_evidence_fields": required_evidence_fields,
        "refusal_candidate": "explicit_presence_evidence" in required_evidence_fields,
    }
    planner_payload = apply_query_spec_to_planner_fields(spec, planner_payload)
    return PlannerDecision(
        **planner_payload
    )


def _apply_intent_override(req: PlannerRequest, plan: PlannerDecision) -> PlannerDecision:
    heuristic_intent, heuristic_conf = _pick_intent_rule(req.query)
    if heuristic_intent not in {"detail_extract", "period_aggregate", "entity_fact_lookup", "list_recent"}:
        return plan
    current_intent = str(plan.intent or "").strip()
    if current_intent in {heuristic_intent, "detail_extract", "period_aggregate", "entity_fact_lookup", "list_recent"}:
        return plan
    # Override broad semantic routes, and also override low-confidence off-target
    # intents from the planner model (e.g. compare_docs for a clear fact/how-to query).
    low_conf_non_structured = (float(plan.confidence) < 0.65) and (
        current_intent not in {"detail_extract", "period_aggregate", "entity_fact_lookup", "list_recent"}
    )
    if current_intent not in {"search_semantic", "search_keyword"} and not low_conf_non_structured:
        return plan
    if heuristic_conf < 0.72:
        return plan
    plan.intent = heuristic_intent
    plan.actions = _INTENT_ACTIONS.get(heuristic_intent, ["search_documents"])
    plan.fallback = "search_semantic"
    plan.route_reason = "heuristic_intent_override"
    spec = build_query_spec_from_query(req.query, planner_intent=heuristic_intent, doc_scope=plan.doc_scope or req.doc_scope)
    for key, value in apply_query_spec_to_planner_fields(spec, {}).items():
        setattr(plan, key, value)
    return plan


def _planner_prompt(req: PlannerRequest) -> list[dict[str, str]]:
    intents = ", ".join(sorted(_ALLOWED_INTENTS))
    schema = {
        "intent": "one_of_allowed_intents",
        "confidence": "0_to_1_float",
        "doc_scope": "echo_input_scope_or_refined_scope",
        "actions": ["tool_action_1", "tool_action_2"],
        "fallback": "search_semantic_or_intent",
        "ui_lang": "zh_or_en",
        "query_lang": "zh_or_en",
        "route_reason": "brief_reason",
        "required_evidence_fields": ["amount", "date", "contact", "explicit_presence_evidence"],
        "refusal_candidate": "bool",
        "query_spec": {
            "version": "v2",
            "task_kind": "fact_lookup|howto_lookup|status_check|aggregate_lookup|detail_extract|summarize|compare|list|timeline|queue|mutate",
            "subject_domain": "home|insurance|appliances|bills|pets|generic",
            "subject_aliases": ["..."],
            "target_slots": ["..."],
            "time_scope": {"kind": "none_or_relative", "start": "", "end": "", "relative_window_months": 0, "reference": ""},
            "derivations": ["..."],
            "needs_presence_evidence": False,
            "needs_status_evidence": False,
            "strict_domain_filter": False,
            "preferred_categories": ["..."],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "You are a Planner model. Return JSON only. "
                "Do not summarize documents. Do not invent tools. "
                f"Allowed intents: {intents}. "
                f"Allowed actions map: {json.dumps(_INTENT_ACTIONS, ensure_ascii=False)}. "
                "If uncertain set confidence < 0.55 and fallback='search_semantic'."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "query": req.query,
                    "ui_lang": req.ui_lang,
                    "query_lang": _safe_query_lang(req),
                    "doc_scope": req.doc_scope,
                    "output_schema": schema,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _planner_from_llm(req: PlannerRequest) -> PlannerDecision | None:
    url = settings.ollama_base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": settings.planner_model,
        "stream": False,
        "messages": _planner_prompt(req),
        "options": {"temperature": 0.0},
    }
    try:
        resp = requests.post(url, json=payload, timeout=8)
        resp.raise_for_status()
        body = resp.json() if hasattr(resp, "json") else {}
        text = str((body.get("message") or {}).get("content") or "")
        parsed = _extract_json_object(text)
        if not parsed:
            return None

        intent = str(parsed.get("intent") or "").strip()
        if intent not in _ALLOWED_INTENTS:
            return None

        confidence = _clamp_confidence(parsed.get("confidence"))
        actions = parsed.get("actions")
        if not isinstance(actions, list):
            actions = _INTENT_ACTIONS.get(intent, ["search_documents"])
        actions = [str(item).strip() for item in actions if str(item).strip()]
        if not actions:
            actions = _INTENT_ACTIONS.get(intent, ["search_documents"])

        fallback = str(parsed.get("fallback") or "").strip() or intent
        if confidence < 0.55:
            fallback = "search_semantic"
        elif fallback not in _ALLOWED_INTENTS:
            fallback = intent

        ui_lang = str(parsed.get("ui_lang") or req.ui_lang).strip().lower()
        if ui_lang not in {"zh", "en"}:
            ui_lang = "zh" if req.ui_lang == "zh" else "en"

        query_lang = str(parsed.get("query_lang") or _safe_query_lang(req)).strip().lower()
        if query_lang not in {"zh", "en"}:
            query_lang = _safe_query_lang(req)

        scope = parsed.get("doc_scope")
        if not isinstance(scope, dict):
            scope = req.doc_scope

        raw_required = [str(x or "") for x in (parsed.get("required_evidence_fields") or []) if str(x or "").strip()][:8]
        refusal_candidate = bool(parsed.get("refusal_candidate", False))
        query_spec = parsed.get("query_spec") if isinstance(parsed.get("query_spec"), dict) else {}
        if not query_spec:
            query_spec = build_query_spec_from_query(req.query, planner_intent=intent, doc_scope=scope)
        planner_payload = {
            "intent": intent,
            "confidence": round(confidence, 2),
            "doc_scope": scope,
            "actions": actions,
            "fallback": fallback,
            "ui_lang": ui_lang,
            "query_lang": query_lang,
            "route_reason": "llm_plan",
            "required_evidence_fields": raw_required,
            "refusal_candidate": refusal_candidate,
        }
        planner_payload = apply_query_spec_to_planner_fields(query_spec, planner_payload)
        # Backward-compatible pass-through if LLM returned convenience fields explicitly.
        if str(parsed.get("task_kind") or "").strip():
            planner_payload["task_kind"] = str(parsed.get("task_kind") or "").strip()
        if str(parsed.get("subject_domain") or "").strip():
            planner_payload["subject_domain"] = str(parsed.get("subject_domain") or "").strip()
        if isinstance(parsed.get("target_slots"), list):
            planner_payload["target_slots"] = [str(x or "").strip() for x in parsed.get("target_slots") if str(x or "").strip()][:12]
        if str(parsed.get("query_spec_version") or "").strip():
            planner_payload["query_spec_version"] = str(parsed.get("query_spec_version") or "").strip()
        return PlannerDecision(**planner_payload)
    except Exception:
        return None


def plan_from_request(req: PlannerRequest) -> PlannerDecision:
    from_llm = _planner_from_llm(req)
    plan = _apply_intent_override(req, from_llm) if from_llm is not None else _apply_intent_override(req, _build_fallback_plan(req))
    if not isinstance(getattr(plan, "query_spec", None), dict) or not dict(getattr(plan, "query_spec", {}) or {}):
        spec = build_query_spec_from_query(req.query, planner_intent=str(plan.intent or ""), doc_scope=plan.doc_scope or req.doc_scope)
        for key, value in apply_query_spec_to_planner_fields(spec, {}).items():
            setattr(plan, key, value)
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# V2 Router: single LLM call — classify route + rewrite query
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RouterDecision:
    """Simplified routing decision from the unified V2 router.

    Routes:
        lookup    — vector search (uses rewritten_query)
        calculate — structured DB query (bill facts)
        chitchat  — short-circuit, no retrieval
        system    — queue / reprocess / tag ops
    """
    route: str            # "lookup" | "calculate" | "chitchat" | "system"
    rewritten_query: str  # search-optimized query (empty for non-lookup)
    domain: str           # "insurance"|"bills"|"home"|"appliances"|"pets"|"warranty"|"legal"|"generic"
    sub_intent: str       # maps to _execute_plan() routing keys
    time_window_months: int = 1   # for period_aggregate
    target_month: str = ""        # "YYYY-MM" for bill_monthly_total
    route_reason: str = "llm"     # "llm" | "heuristic"
    ui_lang: str = "zh"
    query_lang: str = "auto"


_ROUTER_SYSTEM_PROMPT = (
    "You are a routing assistant for a private family knowledge vault (家庭知识库).\n"
    "In ONE JSON response: (1) classify the route, (2) rewrite the query for vector search.\n\n"
    "Routes:\n"
    '- "lookup": Searching for facts, policies, how-to, coverage, contact info, documents\n'
    '- "calculate": Bill totals, pending payments, spending aggregation over time\n'
    '- "chitchat": Greetings, off-topic, unrelated to family documents or finances\n'
    '- "system": Queue status, document reprocessing, tag management\n\n'
    "Sub-intents:\n"
    '  lookup  → "detail_extract"     (multiple structured fields from a document: insurance policy, appliance specs, pet records)\n'
    '          → "entity_fact_lookup" (ONE specific fact: phone number, email, reference number, a single date)\n'
    '          → "search_semantic"    (how-to, general search, maintenance steps, anything else)\n'
    '  calculate → "bill_attention"     (list pending/upcoming bills, no specific month)\n'
    '            → "period_aggregate"   (total spending over past N months, e.g. "过去6个月")\n'
    '            → "bill_monthly_total" (bills for a specific month, e.g. "2月的账单")\n'
    '  system  → "queue_view" | "reprocess_doc" | "tag_update"\n'
    '  chitchat → "chitchat"\n\n'
    "Disambiguation rules:\n"
    "- bill + specific month (e.g. 2月, February, 2026-02) → calculate / bill_monthly_total, even without total/how-much language\n"
    "- maintenance / how-to / steps → lookup / search_semantic (NOT detail_extract)\n"
    "- contact / phone / email for ONE entity → lookup / entity_fact_lookup (NOT detail_extract)\n"
    "- greetings, small talk, math, off-topic → chitchat\n"
    "- pet names (e.g. 米饭, Lucky) + 生日/birthday/疫苗/health term → lookup / entity_fact_lookup (NOT chitchat)\n\n"
    "Domains: insurance | bills | home | appliances | pets | warranty | legal | generic\n\n"
    "For rewritten_query (lookup ONLY — empty string for all other routes):\n"
    "- Preserve original language (zh/en/mixed)\n"
    "- Expand abbreviations; add cross-language synonyms for bilingual search\n"
    '- Remove conversational filler ("帮我看看", "可以告诉我")\n\n'
    "Examples:\n"
    'Q: "你好" → {"route":"chitchat","rewritten_query":"","domain":"generic","sub_intent":"chitchat","time_window_months":0,"target_month":""}\n'
    'Q: "2月份的账单" → {"route":"calculate","rewritten_query":"","domain":"bills","sub_intent":"bill_monthly_total","time_window_months":0,"target_month":"2026-02"}\n'
    'Q: "过去3个月总共花了多少" → {"route":"calculate","rewritten_query":"","domain":"bills","sub_intent":"period_aggregate","time_window_months":3,"target_month":""}\n'
    'Q: "水箱怎么维护" → {"route":"lookup","rewritten_query":"水箱 维护保养步骤 water tank maintenance steps care","domain":"home","sub_intent":"search_semantic","time_window_months":0,"target_month":""}\n'
    'Q: "家庭医保的联系电话" → {"route":"lookup","rewritten_query":"家庭医疗保险 联系电话 contact phone number","domain":"insurance","sub_intent":"entity_fact_lookup","time_window_months":0,"target_month":""}\n'
    'Q: "保险保障哪些项目" → {"route":"lookup","rewritten_query":"保险保障范围 coverage items what is covered","domain":"insurance","sub_intent":"detail_extract","time_window_months":0,"target_month":""}\n'
    'Q: "米饭的生日是什么时候" → {"route":"lookup","rewritten_query":"米饭 宠物生日 pet birthday date of birth","domain":"pets","sub_intent":"entity_fact_lookup","time_window_months":0,"target_month":""}\n\n'
    "Return strictly valid JSON (no markdown, no extra keys):\n"
    '{"route":"lookup","rewritten_query":"","domain":"generic","sub_intent":"search_semantic",'
    '"time_window_months":0,"target_month":""}'
)

_VALID_ROUTES = {"lookup", "calculate", "chitchat", "system"}
_VALID_SUB_INTENTS = {
    "detail_extract", "entity_fact_lookup", "search_semantic",
    "bill_attention", "period_aggregate", "bill_monthly_total",
    "queue_view", "reprocess_doc", "tag_update", "chitchat",
}


def _router_heuristic(req: PlannerRequest) -> RouterDecision:
    """Rule-based fallback when the LLM router fails."""
    q = req.query.lower().strip()
    base: dict[str, Any] = {"ui_lang": req.ui_lang, "query_lang": req.query_lang, "route_reason": "heuristic"}

    # Chitchat — very short greetings
    _chitchat_tokens = {"你好", "早安", "晚安", "谢谢", "再见", "hello", "hi", "thanks", "bye", "ok", "好的", "嗯"}
    if len(q) <= 15 and any(p in q for p in _chitchat_tokens):
        return RouterDecision(route="chitchat", rewritten_query="", domain="generic", sub_intent="chitchat", **base)

    # System ops
    if any(p in q for p in ("队列", "queue", "重处理", "reprocess", "reindex", "邮件处理", "附件处理", "mail queue", "ingest queue")):
        return RouterDecision(route="system", rewritten_query="", domain="generic", sub_intent="queue_view", **base)
    if any(p in q for p in ("标签", "tag update")):
        return RouterDecision(route="system", rewritten_query="", domain="generic", sub_intent="tag_update", **base)

    # Calculate — bill routes
    if re.search(r"过去|past\s+month|近\d+个?月", q):
        m = re.search(r"(\d+)\s*个?月", q)
        window = int(m.group(1)) if m else 3
        return RouterDecision(route="calculate", rewritten_query="", domain="bills",
                              sub_intent="period_aggregate", time_window_months=window, **base)
    if re.search(r"\d{4}年?\d{1,2}月|\d{1,2}月份?", q) and any(p in q for p in ("账单", "bill", "缴费")):
        return RouterDecision(route="calculate", rewritten_query="", domain="bills",
                              sub_intent="bill_monthly_total", **base)
    if any(p in q for p in ("账单", "bill", "待付", "未付", "缴费", "payment due", "due date")):
        return RouterDecision(route="calculate", rewritten_query="", domain="bills",
                              sub_intent="bill_attention", **base)

    # Lookup — domain detection
    domain, sub = "generic", "search_semantic"
    if any(p in q for p in ("保险", "insurance", "理赔", "claim", "保单", "policy")):
        domain, sub = "insurance", "detail_extract"
    elif any(p in q for p in ("warranty", "保修", "质保", "保固")):
        domain, sub = "warranty", "detail_extract"
    elif any(p in q for p in ("家电", "appliance", "空调", "冰箱", "洗碗机", "品牌", "型号")):
        domain, sub = "appliances", "detail_extract"
    elif any(p in q for p in ("宠物", "pet", "疫苗", "vaccine", "兽医", "vet", "猫", "狗", "lucky", "米饭")):
        domain, sub = "pets", "detail_extract"
    elif any(p in q for p in ("水箱", "hvac", "电气", "hydraulic", "维护", "维修", "maintenance")):
        domain, sub = "home", "search_semantic"
    return RouterDecision(route="lookup", rewritten_query=req.query, domain=domain, sub_intent=sub, **base)


def route_and_rewrite(req: PlannerRequest) -> RouterDecision:
    """Single LLM call: classify the user's intent + rewrite the query for vector search.

    Returns a RouterDecision with route in {lookup, calculate, chitchat, system}.
    Falls back to _router_heuristic() if the LLM call fails or returns invalid JSON.
    """
    try:
        resp = requests.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.planner_model,
                "messages": [
                    {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": req.query},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw: dict[str, Any] = json.loads(resp.json()["message"]["content"])
        route = str(raw.get("route") or "lookup").strip().lower()
        if route not in _VALID_ROUTES:
            route = "lookup"
        sub_intent = str(raw.get("sub_intent") or "search_semantic").strip()
        if sub_intent not in _VALID_SUB_INTENTS:
            sub_intent = "search_semantic"
        return RouterDecision(
            route=route,
            rewritten_query=str(raw.get("rewritten_query") or "").strip(),
            domain=str(raw.get("domain") or "generic").strip(),
            sub_intent=sub_intent,
            time_window_months=max(1, int(raw.get("time_window_months") or 1)),
            target_month=str(raw.get("target_month") or "").strip(),
            route_reason="llm",
            ui_lang=req.ui_lang,
            query_lang=req.query_lang,
        )
    except Exception:
        return _router_heuristic(req)
