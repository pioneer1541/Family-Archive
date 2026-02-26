import pytest

from app.schemas import AgentExecutorStats
from app.services.agent_graph import _apply_graph_timing_fields_to_stats

pytestmark = pytest.mark.no_db_reset


def test_apply_graph_timing_fields_populates_stats_dict_and_model():
    timing = {
        "planner_latency_ms": 120,
        "synth_latency_ms": 450,
        "total_latency_ms": 1900,
        "graph_search_calls": 3,
        "graph_router_assist_latency_ms": 180,
        "graph_node_latencies_ms": {
            "planner": 120,
            "route": 1,
            "query_variant": 1,
            "retrieve": 220,
            "rerank": 15,
            "expand": 35,
            "extract": 40,
            "derive": 3,
            "judge": 4,
            "recovery_plan": 2,
            "recovery_apply": 1,
            "answer_build": 500,
        },
    }

    payload = {"graph_llm_calls_planner": 1, "graph_llm_calls_synth": 1}
    _apply_graph_timing_fields_to_stats(payload, timing)
    assert payload["planner_latency_ms"] == 120
    assert payload["synth_latency_ms"] == 450
    assert payload["graph_search_calls"] == 3
    assert payload["graph_router_assist_latency_ms"] == 180
    assert payload["graph_retrieval_latency_ms"] == 220
    assert payload["graph_recovery_latency_ms"] == 3
    assert payload["graph_llm_calls_total"] == 2

    stats = AgentExecutorStats(graph_llm_calls_planner=1, graph_llm_calls_synth=0)
    _apply_graph_timing_fields_to_stats(stats, timing)
    assert stats.planner_latency_ms == 120
    assert stats.graph_node_latencies_ms.get("retrieve") == 220
    assert stats.graph_router_assist_latency_ms == 180
    assert stats.graph_llm_calls_total == 1
