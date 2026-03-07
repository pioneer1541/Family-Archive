from app.schemas import (
    AgentExecuteRequest,
    BilingualText,
    DetailCoverageStats,
    PlannerDecision,
    ResultCard,
)
from app.services.planner import RouterDecision

# Maps RouterDecision.sub_intent → PlannerDecision.task_kind
_SUB_INTENT_TO_TASK_KIND: dict[str, str] = {
    "detail_extract": "detail_extract",
    "entity_fact_lookup": "fact_lookup",
    "search_semantic": "search",
    "bill_attention": "aggregate_lookup",
    "period_aggregate": "aggregate_lookup",
    "bill_monthly_total": "aggregate_lookup",
    "queue_view": "queue",
    "reprocess_doc": "mutate",
    "tag_update": "mutate",
    "chitchat": "search",
}

# Maps RouterDecision.sub_intent → PlannerDecision.intent (for _execute_plan compat)
_SUB_INTENT_TO_PLANNER_INTENT: dict[str, str] = {
    # bill_attention route in _execute_plan checks: planner.intent == "list_recent"
    "bill_attention": "list_recent",
    # bill_monthly_total: pass intent directly so _execute_plan can route without query regex
    "bill_monthly_total": "bill_monthly_total",
    "chitchat": "search_semantic",
}


def _router_to_planner(router: RouterDecision, req: AgentExecuteRequest) -> PlannerDecision:
    """Convert RouterDecision → PlannerDecision for bundle builder compatibility."""
    intent = _SUB_INTENT_TO_PLANNER_INTENT.get(router.sub_intent, router.sub_intent)
    return PlannerDecision(
        intent=intent,
        confidence=0.90,
        doc_scope=req.doc_scope if isinstance(req.doc_scope, dict) else {},
        actions=[],
        fallback="search_semantic",
        ui_lang=router.ui_lang,
        query_lang=router.query_lang,
        route_reason=f"v2_{router.route_reason}",
        subject_domain=router.domain,
        task_kind=_SUB_INTENT_TO_TASK_KIND.get(router.sub_intent, "search"),
        target_slots=[],
        refusal_candidate=False,
    )


def _chitchat_title(query: str, ui_lang: str) -> str:
    q = (query or "").lower().strip()
    zh = ui_lang == "zh"
    if any(t in q for t in ("谢谢", "感谢", "多谢", "thanks", "thank you", "thank")):
        return "不客气" if zh else "You're Welcome"
    if any(t in q for t in ("再见", "拜拜", "bye", "goodbye", "farewell")):
        return "再见" if zh else "Goodbye"
    return "你好" if zh else "Hello"


def _build_chitchat_card(req: AgentExecuteRequest) -> ResultCard:
    """Template response for chitchat/off-topic queries — no retrieval, no LLM synthesis."""
    zh = "您好！我专注于家庭知识库问题，例如账单、保险、家居设备、宠物或家庭文件。请问有什么可以帮您？"
    en = "Hello! I'm focused on family vault topics such as bills, insurance, home appliances, pets, or family documents. How can I help?"
    return ResultCard(
        title=_chitchat_title(req.query, req.ui_lang),
        short_summary=BilingualText(zh=zh, en=en),
        key_points=[],
        sources=[],
        actions=[],
        detail_sections=[],
        missing_fields=[],
        coverage_stats=DetailCoverageStats(docs_scanned=0, docs_matched=0, fields_filled=0),
        evidence_summary=[],
        insufficient_evidence=False,
    )
