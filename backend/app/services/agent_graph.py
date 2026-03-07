import time
from functools import lru_cache
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context
from app.schemas import AgentExecuteRequest, AgentExecuteResponse
from app.services.agent_graph_nodes import (
    answer_build_node,
    derive_facts_node,
    expand_context_node,
    extract_slots_node,
    planner_node,
    query_variant_node,
    recovery_apply_node,
    recovery_decision,
    recovery_plan_node,
    rerank_candidates_node,
    response_finalize_node,
    retrieve_candidates_node,
    route_decision,
    route_node,
    structured_fastpath_node,
    sufficiency_judge_node,
)

logger = get_logger(__name__)
settings = get_settings()


def _require_langgraph():
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # pragma: no cover - runtime dependency path
        raise RuntimeError("langgraph_not_installed") from exc
    return StateGraph, START, END


def _apply_graph_timing_fields_to_stats(stats_obj: Any, timing: dict[str, Any]) -> None:
    if stats_obj is None:
        return
    node_lat = dict(timing.get("graph_node_latencies_ms") or {})
    planner_latency_ms = int(timing.get("planner_latency_ms") or node_lat.get("planner") or 0)
    synth_latency_ms = int(timing.get("synth_latency_ms") or 0)
    graph_search_calls = int(timing.get("graph_search_calls") or 0)
    graph_router_assist_latency_ms = int(timing.get("graph_router_assist_latency_ms") or 0)
    graph_retrieval_latency_ms = int(node_lat.get("retrieve") or 0)
    graph_rerank_latency_ms = int(node_lat.get("rerank") or 0)
    graph_expand_latency_ms = int(node_lat.get("expand") or 0)
    graph_extract_latency_ms = int(node_lat.get("extract") or 0)
    graph_judge_latency_ms = int(node_lat.get("judge") or 0)
    graph_recovery_latency_ms = int(node_lat.get("recovery_plan") or 0) + int(node_lat.get("recovery_apply") or 0)
    executor_latency_ms = int(timing.get("total_latency_ms") or sum(int(v or 0) for v in node_lat.values()))

    def _set(name: str, value: Any) -> None:
        if isinstance(stats_obj, dict):
            stats_obj[name] = value
            return
        try:
            setattr(stats_obj, name, value)
        except Exception:
            pass

    _set("planner_latency_ms", planner_latency_ms)
    _set("executor_latency_ms", executor_latency_ms)
    _set("synth_latency_ms", synth_latency_ms)
    _set("graph_node_latencies_ms", node_lat)
    _set("graph_search_calls", graph_search_calls)
    _set("graph_router_assist_latency_ms", graph_router_assist_latency_ms)
    _set("graph_retrieval_latency_ms", graph_retrieval_latency_ms)
    _set("graph_rerank_latency_ms", graph_rerank_latency_ms)
    _set("graph_expand_latency_ms", graph_expand_latency_ms)
    _set("graph_extract_latency_ms", graph_extract_latency_ms)
    _set("graph_judge_latency_ms", graph_judge_latency_ms)
    _set("graph_recovery_latency_ms", graph_recovery_latency_ms)
    try:
        if isinstance(stats_obj, dict):
            planner_calls = int(stats_obj.get("graph_llm_calls_planner") or 0)
            synth_calls = int(stats_obj.get("graph_llm_calls_synth") or 0)
        else:
            planner_calls = int(getattr(stats_obj, "graph_llm_calls_planner", 0) or 0)
            synth_calls = int(getattr(stats_obj, "graph_llm_calls_synth", 0) or 0)
        _set("graph_llm_calls_total", planner_calls + synth_calls)
    except Exception:
        pass


def _timed_node(node_key: str, fn: Callable[[Any, dict[str, Any] | None], dict[str, Any]]):
    def _wrapped(state: dict[str, Any], config: dict[str, Any] | None = None):
        started = time.perf_counter()
        out = fn(state, config)
        ms = int((time.perf_counter() - started) * 1000)
        payload = dict(out or {})
        base_timing = dict((state or {}).get("timing") or {})
        out_timing = dict(payload.get("timing") or {})
        merged_timing = {**base_timing, **out_timing}
        node_lat = dict(merged_timing.get("graph_node_latencies_ms") or {})
        node_lat[node_key] = int(node_lat.get(node_key) or 0) + ms
        merged_timing["graph_node_latencies_ms"] = node_lat
        payload["timing"] = merged_timing

        stats_payload = payload.get("executor_stats_payload")
        if isinstance(stats_payload, dict):
            payload["executor_stats_payload"] = {**stats_payload}
            _apply_graph_timing_fields_to_stats(payload["executor_stats_payload"], merged_timing)

        resp = payload.get("response")
        if resp is not None and hasattr(resp, "executor_stats"):
            _apply_graph_timing_fields_to_stats(getattr(resp, "executor_stats", None), merged_timing)

        return payload

    return _wrapped


@lru_cache(maxsize=1)
def _compiled_agent_graph():
    StateGraph, START, END = _require_langgraph()
    from app.services.agent_graph_state import AgentGraphState

    graph = StateGraph(AgentGraphState)
    graph.add_node("node_planner", _timed_node("planner", planner_node))
    graph.add_node("node_route", _timed_node("route", route_node))
    graph.add_node(
        "node_structured_fastpath",
        _timed_node("structured_fastpath", structured_fastpath_node),
    )
    graph.add_node("node_query_variant", _timed_node("query_variant", query_variant_node))
    graph.add_node("node_retrieve", _timed_node("retrieve", retrieve_candidates_node))
    graph.add_node("node_rerank", _timed_node("rerank", rerank_candidates_node))
    graph.add_node("node_expand", _timed_node("expand", expand_context_node))
    graph.add_node("node_extract_slots", _timed_node("extract", extract_slots_node))
    graph.add_node("node_derive", _timed_node("derive", derive_facts_node))
    graph.add_node("node_judge", _timed_node("judge", sufficiency_judge_node))
    graph.add_node("node_recovery_plan", _timed_node("recovery_plan", recovery_plan_node))
    graph.add_node("node_recovery_apply", _timed_node("recovery_apply", recovery_apply_node))
    graph.add_node("node_answer_build", _timed_node("answer_build", answer_build_node))
    graph.add_node("node_finalize", _timed_node("response_finalize", response_finalize_node))

    graph.add_edge(START, "node_planner")
    graph.add_edge("node_planner", "node_route")
    graph.add_conditional_edges(
        "node_route",
        route_decision,
        {
            "structured_fastpath": "node_structured_fastpath",
            "query_retrieval": "node_query_variant",
        },
    )
    graph.add_edge("node_structured_fastpath", "node_finalize")

    graph.add_edge("node_query_variant", "node_retrieve")
    graph.add_edge("node_retrieve", "node_rerank")
    graph.add_edge("node_rerank", "node_expand")
    graph.add_edge("node_expand", "node_extract_slots")
    graph.add_edge("node_extract_slots", "node_derive")
    graph.add_edge("node_derive", "node_judge")
    graph.add_edge("node_judge", "node_recovery_plan")
    graph.add_conditional_edges(
        "node_recovery_plan",
        recovery_decision,
        {
            "answer": "node_answer_build",
            "recover": "node_recovery_apply",
        },
    )
    graph.add_edge("node_recovery_apply", "node_query_variant")
    graph.add_edge("node_answer_build", "node_finalize")
    graph.add_edge("node_finalize", END)

    return graph.compile()


def execute_agent_graph(db: Session, req: AgentExecuteRequest) -> AgentExecuteResponse:
    compiled = _compiled_agent_graph()
    started_at = time.perf_counter()
    out = compiled.invoke(
        {"timing": {}},
        config={
            "recursion_limit": 64,
            "configurable": {
                "db": db,
                "settings": settings,
                "logger": logger,
                "raw_req": req,
                "started_at": started_at,
            },
        },
    )
    resp = (out or {}).get("response")
    if isinstance(resp, AgentExecuteResponse):
        return resp
    raise RuntimeError("agent_graph_no_response")


def stream_agent_graph(db: Session, req: AgentExecuteRequest):
    """Yield (node_name, final_response | None) tuples as the graph executes.

    Each yielded item is either:
      - (node_name: str, None)  — a node completed, no final result yet
      - (node_name: str, AgentExecuteResponse)  — last event; response is ready
    Raises RuntimeError if no response is produced.
    """
    compiled = _compiled_agent_graph()
    started_at = time.perf_counter()
    config = {
        "recursion_limit": 64,
        "configurable": {
            "db": db,
            "settings": settings,
            "logger": logger,
            "raw_req": req,
            "started_at": started_at,
        },
    }
    last_state: dict = {}
    for node_output in compiled.stream({"timing": {}}, config=config):
        node_name = next(iter(node_output), "__unknown__")
        last_state = node_output.get(node_name) or last_state
        resp = last_state.get("response")
        if isinstance(resp, AgentExecuteResponse):
            yield node_name, resp
            return
        yield node_name, None

    # Fallback: try to extract response from last_state
    resp = last_state.get("response")
    if isinstance(resp, AgentExecuteResponse):
        yield "__finalize__", resp
        return
    raise RuntimeError("agent_graph_no_response")


def try_run_agent_graph_shadow(db: Session, req: AgentExecuteRequest) -> AgentExecuteResponse | None:
    try:
        return execute_agent_graph(db, req)
    except Exception as exc:  # pragma: no cover - shadow best effort
        logger.warning(
            "agent_graph_shadow_failed",
            extra=sanitize_log_context(
                {
                    "error_code": "agent_graph_shadow_failed",
                    "exc_type": type(exc).__name__,
                    "detail": str(exc),
                }
            ),
        )
        return None
