"""Agent V2 Nodes - Router

Intent classification and query routing.
"""

import hashlib
from typing import Any

from app.logging_utils import get_logger
from app.services.agent_v2.state import AgentGraphState

logger = get_logger(__name__)
from app.services.agent_v2.tools.cache import get_cache, set_cache
from app.services.agent_v2.tools.llm import call_router_llm

# Chitchat detection - rule-based pre-filter
_CHITCHAT_TOKENS = {
    "你好", "早安", "晚安", "谢谢", "再见",
    "hello", "hi", "thanks", "bye", "ok", "好的", "嗯",
}


def _is_chitchat_rule_based(query: str) -> bool:
    """Rule-based chitchat detection to short-circuit LLM call."""
    q = query.lower().strip()
    return len(q) <= 15 and any(p in q for p in _CHITCHAT_TOKENS)


async def router_node(state: AgentGraphState) -> dict[str, Any]:
    """Router node: classify intent and determine route.
    
    Input: state["req"]["query"]
    Output: {router: {...}, route: str, route_reason: str}
    """
    query = state.get("req", {}).get("query", "")
    trace_id = state.get("trace_id", "")
    
    # Rule-based short-circuit for chitchat
    if _is_chitchat_rule_based(query):
        logger.info("router_chitchat_rule: query=%s trace_id=%s", query, trace_id)
        return {
            "router": {
                "route": "chitchat",
                "route_reason": "rule_based",
                "confidence": 1.0,
                "rewritten_query": "",
            },
            "route": "chitchat",
            "route_reason": "rule_based",
        }
    
    # Check cache (use stable hash)
    cache_key = f"router:v1:{hashlib.md5(query.encode()).hexdigest()}"
    if cached := await get_cache(cache_key):
        logger.info("router_cache_hit: query=%s trace_id=%s", query, trace_id)
        return {
            "router": cached,
            "route": cached.get("route", "lookup"),
            "route_reason": "cache_hit",
        }
    
    # Call LLM for routing decision
    try:
        result = await call_router_llm(query)
        
        # Validate result
        route = result.get("route", "lookup")
        if route not in ("chitchat", "lookup", "calculate", "system", "detail_extract"):
            route = "lookup"
        
        # Enrich result
        result["route"] = route
        result["route_reason"] = result.get("route_reason", "llm")
        
        # Cache result (5 minutes TTL for router)
        await set_cache(cache_key, result, ttl=300)
        
        logger.info(
            "router_llm_success: query=%s route=%s confidence=%s trace_id=%s",
            query,
            route,
            result.get("confidence"),
            trace_id,
        )
        
        return {
            "router": result,
            "route": route,
            "route_reason": "llm",
        }
        
    except Exception as e:
        logger.error("router_llm_failed: query=%s error=%s trace_id=%s", query, str(e), trace_id)
        
        # Fallback to safe default
        fallback = {
            "route": "lookup",
            "route_reason": "llm_error_fallback",
            "confidence": 0.5,
            "rewritten_query": query,
        }
        return {
            "router": fallback,
            "route": "lookup",
            "route_reason": "llm_error_fallback",
        }
