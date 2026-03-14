"""Agent V2 Nodes - Unified Synthesizer

Single LLM call that combines routing and synthesis for simple queries.
Reduces latency from 2 LLM calls to 1.
"""

from typing import Any

from app.logging_utils import get_logger
from app.services.agent_v2.state import AgentGraphState

logger = get_logger(__name__)


UNIFIED_PROMPT_TEMPLATE = """You are an intelligent assistant for a family knowledge vault. Your task is to:
1. Understand the user's intent
2. Search the provided document context
3. Generate a helpful answer

## Document Context
{context}

## User Query
{query}

## Task
Based on the context above:
- If the answer is found in the context, provide it directly
- If the context is insufficient, indicate this clearly
- Respond in the user's language ({lang})

## Output Format
Return JSON:
{{
    "intent": "lookup|chitchat|calculate|detail_extract|system",
    "confidence": 0.0-1.0,
    "answer_found": true|false,
    "answer": {{
        "title": "Short title (10-30 chars)",
        "short_summary": {{
            "en": "Brief answer in English (50-150 chars)",
            "zh": "Brief answer in Chinese (50-150 chars)"
        }},
        "key_points": [
            {{"en": "Key point 1", "zh": "要点1"}},
            {{"en": "Key point 2", "zh": "要点2"}}
        ]
    }},
    "sources": ["doc_id_1", "doc_id_2"]
}}

If answer_found is false, set answer to:
{{
    "title": "未找到相关信息 / Not Found",
    "short_summary": {{
        "en": "No relevant information found in the documents.",
        "zh": "在文档中未找到相关信息。"
    }},
    "key_points": []
}}
"""


def _build_unified_prompt(state: AgentGraphState) -> str:
    """Build the unified prompt with context."""
    query = state["req"]["query"]
    ui_lang = state["req"].get("ui_lang", "zh")
    lang = "Chinese" if ui_lang == "zh" else "English"

    # Get context chunks
    chunks = state.get("context_chunks", [])

    if chunks:
        context_parts = []
        for i, chunk in enumerate(chunks[:10]):  # Limit to 10 chunks
            content = chunk.get("content", "")[:500]  # Limit content length
            source = chunk.get("source", f"doc_{i}")
            context_parts.append(f"[{i+1}] Source: {source}\n{content}")
        context = "\n\n".join(context_parts)
    else:
        context = "No relevant documents found."

    return UNIFIED_PROMPT_TEMPLATE.format(
        query=query,
        context=context,
        lang=lang,
    )


def _parse_unified_response(response_text: str) -> dict[str, Any] | None:
    """Parse and validate the unified LLM response."""
    import json

    try:
        result = json.loads(response_text)

        # Extract intent
        intent = result.get("intent", "lookup")
        confidence = float(result.get("confidence", 0.8))
        answer_found = result.get("answer_found", True)

        # Extract answer structure
        answer = result.get("answer", {})

        # Ensure required fields
        if "title" not in answer:
            answer["title"] = "Family Vault"

        if "short_summary" not in answer:
            answer["short_summary"] = {
                "en": answer.get("content", "No answer provided"),
                "zh": answer.get("content", "未提供答案"),
            }

        if "key_points" not in answer:
            answer["key_points"] = []

        return {
            "intent": intent,
            "confidence": confidence,
            "answer_found": answer_found,
            "answer": answer,
            "sources": result.get("sources", []),
        }

    except json.JSONDecodeError as e:
        logger.warning("unified_response_parse_error", extra={"error": str(e)})
        return None
    except Exception as e:
        logger.warning("unified_response_validation_error", extra={"error": str(e)})
        return None


async def unified_synthesizer_node(state: AgentGraphState) -> dict[str, Any]:
    """
    Unified node: performs routing + synthesis in a single LLM call.

    This is the core of Phase 2 single-LLM mode. For simple queries:
    - No separate router call
    - No separate synthesizer call
    - One LLM call that does both

    State updates:
    - router: {route, confidence, route_reason}  # For compatibility
    - final_card_payload: The answer card
    - terminal: True
    - terminal_reason: "answer_complete" | "insufficient_context"

    Returns state updates dict.
    """
    from app.services.agent_v2.tools.llm import call_unified_llm

    query = state["req"]["query"]
    trace_id = state.get("trace_id", "unknown")

    logger.info(
        "unified_synthesizer_start",
        extra={"trace_id": trace_id, "query": query[:100]}
    )

    # Build unified prompt
    prompt = _build_unified_prompt(state)

    # Single LLM call
    try:
        response = await call_unified_llm(prompt, state)
        result = _parse_unified_response(response)

        if result is None:
            # Parsing failed - fallback to insufficient context
            logger.warning(
                "unified_synthesizer_parse_failed",
                extra={"trace_id": trace_id}
            )
            return {
                "router": {
                    "route": "lookup",
                    "confidence": 0.5,
                    "route_reason": "unified_parse_failed",
                },
                "final_card_payload": {
                    "title": "未找到相关信息 / Not Found",
                    "short_summary": {
                        "en": "Unable to parse the response. Please try again.",
                        "zh": "无法解析响应，请重试。",
                    },
                    "key_points": [],
                },
                "terminal": True,
                "terminal_reason": "answer_complete",
            }

        # Build sources
        sources = []
        for src in result.get("sources", []):
            sources.append({
                "doc_id": src,
                "chunk_indices": [0],
                "excerpts": [],
            })

        # Map intent to route for compatibility
        intent_to_route = {
            "chitchat": "chitchat",
            "lookup": "lookup",
            "calculate": "calculate",
            "detail_extract": "detail_extract",
            "system": "lookup",
        }

        route = intent_to_route.get(result["intent"], "lookup")

        logger.info(
            "unified_synthesizer_complete",
            extra={
                "trace_id": trace_id,
                "route": route,
                "answer_found": result["answer_found"],
                "confidence": result["confidence"],
            }
        )

        return {
            "router": {
                "route": route,
                "confidence": result["confidence"],
                "route_reason": "unified_single_call",
            },
            "final_card_payload": result["answer"],
            "terminal": True,
            "terminal_reason": "answer_complete" if result["answer_found"] else "insufficient_context",
            "sources": sources,
        }

    except Exception as exc:
        logger.error(
            "unified_synthesizer_error",
            extra={"trace_id": trace_id, "error": str(exc)}
        )

        # Fallback response
        return {
            "router": {
                "route": "lookup",
                "confidence": 0.3,
                "route_reason": f"unified_error: {str(exc)[:50]}",
            },
            "final_card_payload": {
                "title": "处理出错 / Error",
                "short_summary": {
                    "en": "An error occurred while processing your request.",
                    "zh": "处理请求时发生错误。",
                },
                "key_points": [],
            },
            "terminal": True,
            "terminal_reason": "answer_complete",
        }
