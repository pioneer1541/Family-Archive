"""Agent V2 Tools - LLM

LLM call abstractions.
"""

import asyncio
import json
from typing import Any

from app.config import get_settings
from app.runtime_config import get_runtime_setting
from app.schemas import AgentExecuteRequest, PlannerDecision
from app.services.llm_provider import create_provider, LLMConfig, ProviderType

settings = get_settings()


def _get_provider_for_model(model_name: str):
    """Create provider instance for a model."""
    # Determine provider type from model name or settings
    base_url = settings.ollama_base_url
    
    config = LLMConfig(
        provider_type=ProviderType.OLLAMA,
        base_url=base_url,
        model_name=model_name,
        timeout=30.0,
    )
    return create_provider(config)


def _call_router_sync(query: str, model: str) -> dict[str, Any]:
    """Synchronous router LLM call."""
    # Create provider
    provider = _get_provider_for_model(model)
    
    # Build router prompt
    messages = [
        {
            "role": "system",
            "content": (
                "You are a query router for a family knowledge vault. "
                "Classify the query into one of: chitchat, lookup, calculate, system, detail_extract. "
                "Return JSON with: route, confidence (0-1), rewritten_query, route_reason"
            )
        },
        {"role": "user", "content": query}
    ]
    
    # Call LLM
    response = provider.chat_completion(
        messages=messages,
        temperature=0.0,
    )
    
    # Parse response
    result = json.loads(response.content)

    return {
        "route": result.get("route", "lookup"),
        "confidence": result.get("confidence", 0.8),
        "rewritten_query": result.get("rewritten_query", query),
        "route_reason": result.get("route_reason", "llm"),
    }


async def call_router_llm(query: str, db=None) -> dict[str, Any]:
    """Call LLM for routing decision.
    
    Integrates with existing llm_provider.
    Runs sync call in thread pool to avoid blocking event loop.
    """
    try:
        # Get router model from settings (before entering thread)
        model = get_runtime_setting("planner_model", db)
        
        # Run sync call in thread pool
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _call_router_sync, query, model)
        return result
        
    except Exception as e:
        # Fallback
        return {
            "route": "lookup",
            "confidence": 0.5,
            "rewritten_query": query,
            "route_reason": f"error_fallback: {str(e)}",
        }


def _call_synthesizer_sync(
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    bundle: dict[str, Any],
    trace_id: str,
    model: str
) -> dict[str, Any] | None:
    """Synchronous synthesizer LLM call."""
    # Create provider (model passed in, no db access)
    provider = _get_provider_for_model(model)
    
    # Build synthesis prompt (simplified version of _synth_prompt)
    chunks = bundle.get("context_chunks", [])[:10]  # Limit to 10 chunks
    
    context_text = "\n\n".join([
        f"[{i+1}] {c.get('content', '')[:500]}"
        for i, c in enumerate(chunks)
    ])
    
    target_lang = "Chinese" if req.ui_lang == "zh" else "English"
    
    messages = [
        {
            "role": "system",
            "content": (
                f"You are a helpful assistant for a family knowledge vault. "
                f"Answer the user's question using ONLY the provided context. "
                f"Respond in {target_lang}. "
                f"Return JSON with: title, short_summary (with en and zh), key_points (array with en and zh)"
            )
        },
        {
            "role": "user",
            "content": f"Question: {req.query}\n\nContext:\n{context_text}"
        }
    ]
    
    # Call LLM
    response = provider.chat_completion(
        messages=messages,
        temperature=0.1,
    )
    
    # Parse response
    result = json.loads(response.content)

    # Ensure required fields
    if "short_summary" not in result:
        result["short_summary"] = {"en": result.get("content", ""), "zh": result.get("content", "")}
    
    return result


async def call_synthesizer_llm(
    req: AgentExecuteRequest,
    planner: PlannerDecision,
    bundle: dict[str, Any],
    trace_id: str,
    db=None
) -> dict[str, Any] | None:
    """Call LLM for answer synthesis.
    
    Integrates with existing llm_provider.
    Runs sync call in thread pool to avoid blocking event loop.
    """
    try:
        # Get synthesizer model from settings (before entering thread)
        model = get_runtime_setting("synthesizer_model", db)
        
        # Run sync call in thread pool (pass model string, not db session)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, _call_synthesizer_sync, req, planner, bundle, trace_id, model
        )
        return result
        
    except Exception as e:
        # Return error info
        return {
            "title": "Family Vault",
            "short_summary": {
                "en": f"Error in synthesis: {str(e)}",
                "zh": f"合成错误：{str(e)}"
            },
            "key_points": [],
        }
