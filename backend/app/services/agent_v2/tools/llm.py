"""Agent V2 Tools - LLM

LLM call abstractions.
"""

from typing import Any


async def call_router_llm(query: str) -> dict[str, Any]:
    """Call LLM for routing decision.
    
    TODO: Integrate with existing llm_provider.py
    For now, returns mock response.
    """
    # Placeholder - will integrate with actual LLM provider
    return {
        "route": "lookup",
        "confidence": 0.8,
        "rewritten_query": query,
        "route_reason": "placeholder",
    }
