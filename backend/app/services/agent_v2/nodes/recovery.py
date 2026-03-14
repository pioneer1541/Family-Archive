"""Agent V2 Nodes - Recovery

Recovery and retry logic for failed or insufficient retrievals.
"""

from typing import Any

from app.logging_utils import get_logger
from app.services.agent_v2.state import AgentGraphState

logger = get_logger(__name__)


async def recovery_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Recovery node: attempt to recover from insufficient retrieval.
    
    Strategies:
    1. Relax search constraints (category, tags)
    2. Try alternative query formulations
    3. Increase result limit
    
    Returns updated state for retry.
    """
    req = state.get("req", {})
    trace_id = state.get("trace_id", "")
    loop_count = state.get("loop_count", 0)
    loop_budget = state.get("loop_budget", 3)
    
    logger.info("recovery_attempt: trace_id=%s loop=%d/%d", trace_id, loop_count, loop_budget)
    
    # Check if we have budget left
    if loop_count >= loop_budget:
        logger.warning("recovery_budget_exhausted: trace_id=%s", trace_id)
        return {
            "terminal": True,
            "terminal_reason": "recovery_budget_exhausted",
            "final_card_payload": {
                "title": "Family Vault",
                "short_summary": {
                    "en": "I couldn't find enough information after multiple attempts. Please try rephrasing your question.",
                    "zh": "经过多次尝试，我仍无法找到足够的信息。请尝试用不同方式描述您的问题。"
                },
                "type": "answer",
            },
        }
    
    # Increment loop counter
    new_loop_count = loop_count + 1
    
    # Build recovery plan
    recovery_plan = {
        "strategy": "relax_constraints",
        "original_top_k": req.get("top_k", 10),
        "new_top_k": min(req.get("top_k", 10) * 2, 20),  # Double top_k, max 20
        "relaxed_filters": True,
    }
    
    # Update request for retry
    updated_req = dict(req)
    updated_req["top_k"] = recovery_plan["new_top_k"]
    # Clear category filters to broaden search
    if updated_req.get("category_path"):
        recovery_plan["cleared_category"] = updated_req.pop("category_path")
    if updated_req.get("tags_all"):
        recovery_plan["cleared_tags_all"] = updated_req.pop("tags_all")
    
    logger.info("recovery_plan: trace_id=%s strategy=%s new_top_k=%d", 
                trace_id, recovery_plan["strategy"], recovery_plan["new_top_k"])
    
    return {
        "req": updated_req,
        "loop_count": new_loop_count,
        "recovery_plan": recovery_plan,
        "terminal": False,
    }
