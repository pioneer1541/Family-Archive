"""Agent V2 - LangGraph Definition (Phase 2)

Unified graph architecture with single-LLM optimization.
"""

import time
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.schemas import AgentExecuteRequest, AgentExecuteResponse
from app.services.agent_v2.state import AgentGraphState
from app.services.agent_v2.nodes.router import router_node
from app.services.agent_v2.nodes.chitchat import chitchat_node
from app.services.agent_v2.nodes.retriever import retriever_node
from app.services.agent_v2.nodes.synthesizer import synthesizer_node
from app.services.agent_v2.nodes.recovery import recovery_node
from app.services.agent_v2.nodes.query_classifier import query_classifier_node
from app.services.agent_v2.nodes.unified_synthesizer import unified_synthesizer_node
from app.services.agent_v2.edges.conditions import (
    should_chitchat,
    should_retry,
    is_answerability_insufficient,
    is_simple_query,
)
from app.services.agent_v2.config import AgentV2Config
from app.services.agent_v2.metrics import AgentV2Metrics, record_metrics
from app.services.agent_v2.ab_test_metrics import get_ab_test_collector

# Build the graph
builder = StateGraph(AgentGraphState)

# Add nodes
builder.add_node("query_classifier_node", query_classifier_node)
builder.add_node("unified_synthesize_node", unified_synthesizer_node)
builder.add_node("router_node", router_node)
builder.add_node("chitchat_node", chitchat_node)
builder.add_node("retrieve_node", retriever_node)
builder.add_node("synthesize_node", synthesizer_node)
builder.add_node("recovery_node", recovery_node)

# Phase 2: Start with query classifier
builder.add_edge(START, "query_classifier_node")

# Conditional: Simple query -> unified synthesizer (1 LLM call)
#            Complex query -> router (2 LLM calls)
builder.add_conditional_edges(
    "query_classifier_node",
    is_simple_query,
    {
        True: "unified_synthesize_node",   # Single-LLM mode
        False: "router_node",               # Dual-LLM mode
    }
)

# Unified synthesizer -> END (complete in 1 call)
builder.add_edge("unified_synthesize_node", END)

# Dual-LLM path: router -> conditional (chitchat or retrieve)
builder.add_conditional_edges(
    "router_node",
    should_chitchat,
    {
        True: "chitchat_node",
        False: "retrieve_node"
    }
)

# Retrieve -> Recovery check (if insufficient) or Synthesize
builder.add_conditional_edges(
    "retrieve_node",
    is_answerability_insufficient,
    {
        True: "recovery_node",
        False: "synthesize_node"
    }
)

# Recovery -> Retrieve (retry loop)
builder.add_conditional_edges(
    "recovery_node",
    should_retry,
    {
        True: "retrieve_node",      # Retry with relaxed constraints
        False: "synthesize_node"    # Give up and synthesize with what we have
    }
)

# Synthesize -> END
builder.add_edge("synthesize_node", END)

# Chitchat -> END
builder.add_edge("chitchat_node", END)

# Compile the graph
graph = builder.compile()


async def execute(req: AgentExecuteRequest, db=None, external_trace_id: str | None = None, _force: bool = False) -> AgentExecuteResponse:
    """Execute agent with the new LangGraph architecture.

    This is the main entry point for Agent V2.

    Args:
        req: The execution request
        db: Database session (required for retrieval)
        external_trace_id: Optional trace ID from caller (for correlation)
        _force: Internal flag to bypass config check (for testing)
    """
    # Check if V2 is enabled (unless forced)
    if not _force and not AgentV2Config.should_use_v2(external_trace_id):
        raise RuntimeError("Agent V2 is disabled")

    # Use external trace_id if provided, otherwise generate new one
    trace_id = external_trace_id or f"agt-{uuid.uuid4().hex[:12]}"
    initial_state: AgentGraphState = {
        "req": req.model_dump(),
        "trace_id": trace_id,
        "timing": {"start_ms": int(time.time() * 1000)},
        "loop_budget": 3,  # Max recovery loops
        "loop_count": 0,
    }

    # Initialize metrics
    metrics = AgentV2Metrics(trace_id) if AgentV2Config.is_metrics_enabled() else None
    ab_metrics = None
    success = False

    try:
        # Execute graph with config (passes db to nodes)
        config = {"configurable": {"db": db, "metrics": metrics}} if db else None
        if metrics:
            metrics.start_node("graph_execution")
        result = await graph.ainvoke(initial_state, config=config)
        if metrics:
            metrics.end_node("graph_execution")

        success = True

        # Phase 2: Collect A/B test metrics
        if AgentV2Config.is_single_llm_mode_enabled():
            classifier = result.get("classifier", {})
            ab_metrics = get_ab_test_collector().start(
                trace_id=trace_id,
                complexity=classifier.get("complexity", "unknown"),
                method=classifier.get("method", "unknown"),
            )

    except Exception as e:
        # Log error and return fallback response
        from app.logging_utils import get_logger
        logger = get_logger(__name__)
        logger.error("agent_v2_execution_failed: trace_id=%s error=%s", trace_id, str(e))

        # Return minimal error response
        return AgentExecuteResponse(
            card={
                "title": "Family Vault",
                "short_summary": {"en": "An error occurred", "zh": "发生错误"},
                "key_points": [],
                "detail_sections": [],
                "sources": [],
                "actions": [],
            },
            planner={
                "intent": "error",
                "confidence": 0.0,
                "doc_scope": {},
                "actions": [],
                "fallback": "error",
                "ui_lang": req.ui_lang,
                "query_lang": req.query_lang or req.ui_lang,
                "route_reason": f"execution_error: {str(e)}",
            },
            executor_stats={
                "route": "error",
                "retrieval_mode": "none",
                "answer_mode": "error",
                "route_reason": "execution_error",
                "graph_enabled": True,
            },
            related_docs=[],
            trace_id=trace_id,
        )
    finally:
        if metrics:
            metrics_summary = metrics.finish(success=success)
            record_metrics(metrics_summary)

        # Phase 2: Finish A/B test metrics
        if ab_metrics:
            classifier = result.get("classifier", {}) if result else {}
            # Count LLM calls based on path taken
            llm_calls = 1 if classifier.get("complexity") == "simple" else 2
            get_ab_test_collector().finish(
                ab_metrics,
                llm_calls=llm_calls,
                success=success,
            )

    # Construct response with complete fields
    req_data = result.get("req", {})
    ui_lang = req_data.get("ui_lang", "zh")
    query_lang = req_data.get("query_lang", ui_lang)

    # Build complete planner
    router_data = result.get("router", {})
    planner = {
        "intent": router_data.get("route", "lookup"),
        "confidence": router_data.get("confidence", 0.8),
        "doc_scope": req_data.get("doc_scope") or {},
        "actions": router_data.get("actions", ["search_documents"]),
        "fallback": router_data.get("fallback", "search_semantic"),
        "ui_lang": ui_lang,
        "query_lang": query_lang,
        "route_reason": router_data.get("route_reason", "default"),
    }

    # Build complete card with defaults
    card_payload = result.get("final_card_payload", {})
    card = {
        "title": card_payload.get("title", "Family Vault"),
        "short_summary": card_payload.get("short_summary", {"en": "", "zh": ""}),
        "key_points": card_payload.get("key_points", []),
        "detail_sections": card_payload.get("detail_sections", []),
        "sources": card_payload.get("sources", []),
        "actions": card_payload.get("actions", []),
        "type": card_payload.get("type", "answer"),
    }

    # Determine graph path for metrics
    classifier = result.get("classifier", {})
    complexity = classifier.get("complexity", "unknown")
    graph_path = f"classifier({complexity})->"

    if complexity == "simple":
        graph_path += "unified_synthesize"
    elif router_data.get("route") == "chitchat":
        graph_path += "router->chitchat"
    else:
        graph_path += "router->retrieve->synthesize"
        if result.get("recovery_plan"):
            graph_path += "(with recovery)"

    # Build executor stats with graph tracking
    executor_stats = {
        "route": router_data.get("route", "lookup"),
        "retrieval_mode": "vector" if result.get("context_chunks") else "none",
        "answer_mode": "synthesis" if router_data.get("route") != "chitchat" else "chitchat",
        "route_reason": router_data.get("route_reason", "default"),
        "answerability": result.get("answerability", "sufficient"),
        "hit_count": len(result.get("context_chunks", [])),
        "doc_count": len(set(c.get("doc_id") for c in result.get("context_chunks", []))),
        "graph_enabled": True,
        "graph_complexity": complexity,
        "graph_path": graph_path,
        "graph_loop_budget": result.get("loop_budget", 3),
        "graph_loops_used": result.get("loop_count", 0),
    }

    return AgentExecuteResponse(
        card=card,
        planner=planner,
        executor_stats=executor_stats,
        related_docs=result.get("related_docs_payload", []),
        trace_id=result.get("trace_id", "")
    )
