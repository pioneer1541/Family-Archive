"""Agent V2 Nodes - Synthesizer

Answer generation from retrieved context.
"""

from typing import Any

from app.logging_utils import get_logger
from app.schemas import AgentExecuteRequest, PlannerDecision
from app.services.agent_v2.state import AgentGraphState
from app.services.agent_v2.tools.llm import call_synthesizer_llm

logger = get_logger(__name__)


async def synthesizer_node(state: AgentGraphState, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synthesizer node: generate final answer from context.
    
    Integrates with existing synthesis logic.
    """
    req_data = state.get("req", {})
    trace_id = state.get("trace_id", "")
    context_chunks = state.get("context_chunks", [])
    router_data = state.get("router", {})
    
    # Get DB session from config
    db = config.get("configurable", {}).get("db") if config else None
    
    query = req_data.get("query", "")
    ui_lang = req_data.get("ui_lang", "zh")
    
    # Handle insufficient context
    if not context_chunks:
        logger.warning("synthesizer_no_context", extra={"trace_id": trace_id, "query": query})
        return {
            "final_card_payload": {
                "title": "Family Vault",
                "short_summary": {
                    "en": "I couldn't find relevant information in your documents.",
                    "zh": "我在您的文档中没有找到相关信息。"
                },
                "type": "answer",
            },
            "terminal": True,
            "terminal_reason": "insufficient_context",
        }
    
    try:
        # Build synthesis request
        # Convert state format to legacy bundle format for compatibility
        bundle = {
            "context_chunks": context_chunks,
            "route": router_data.get("route", "lookup"),
            "hit_count": len(context_chunks),
            "doc_count": len(set(c.get("doc_id") for c in context_chunks)),
            "answerability": state.get("answerability", "sufficient"),
            "missing_fields": state.get("coverage_missing_slots", []),
            "coverage_ratio": state.get("slot_coverage_ratio", 1.0),
        }
        
        # Create planner decision from router data
        planner = PlannerDecision(
            intent=router_data.get("route", "lookup"),
            confidence=router_data.get("confidence", 0.8),
            ui_lang=ui_lang,
            query_lang=req_data.get("query_lang", ui_lang),
        )
        
        # Create request object
        req = AgentExecuteRequest(
            query=query,
            ui_lang=ui_lang,
            query_lang=req_data.get("query_lang", ui_lang),
        )
        
        # Call synthesizer
        result = await call_synthesizer_llm(
            req=req,
            planner=planner,
            bundle=bundle,
            trace_id=trace_id,
            db=db,
        )
        
        logger.info(
            "synthesizer_success",
            extra={"trace_id": trace_id, "query": query, "has_result": bool(result)}
        )
        
        return {
            "final_card_payload": result or {
                "title": "Family Vault",
                "content": "[Error in synthesis]",
                "type": "answer",
            },
            "terminal": True,
            "terminal_reason": "answer_complete",
        }
        
    except Exception as e:
        logger.error("synthesizer_failed", extra={"trace_id": trace_id, "query": query, "error": str(e)})
        
        # Fallback response
        return {
            "final_card_payload": {
                "title": "Family Vault",
                "short_summary": {
                    "en": f"An error occurred while generating the answer: {str(e)}",
                    "zh": f"生成回答时发生错误：{str(e)}"
                },
                "type": "answer",
            },
            "terminal": True,
            "terminal_reason": "synthesis_error",
        }
