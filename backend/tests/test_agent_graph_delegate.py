import pytest

from app.schemas import AgentExecuteRequest, AgentExecuteResponse, AgentExecutorStats, BilingualText, PlannerDecision, ResultCard
from app.services import agent_graph_nodes as agn

pytestmark = pytest.mark.no_db_reset


def _dummy_response() -> AgentExecuteResponse:
    return AgentExecuteResponse(
        planner=PlannerDecision(
            intent="period_aggregate",
            confidence=0.8,
            doc_scope={},
            actions=[],
            fallback="none",
            ui_lang="zh",
            query_lang="zh",
        ),
        card=ResultCard(
            title="x",
            short_summary=BilingualText(en="e", zh="z"),
            key_points=[],
            sources=[],
            actions=[],
        ),
        trace_id="t1",
        executor_stats=AgentExecutorStats(route="bill_monthly_total"),
    )


def test_structured_fastpath_node_reuses_graph_planner(monkeypatch):
    seen = {"req_planner": None, "execute_plan_called": False}

    class LegacyStub:
        @staticmethod
        def _execute_plan(_db, req, planner):
            seen["execute_plan_called"] = True
            seen["req_planner"] = getattr(req, "planner", None)
            return {
                "route": "bill_monthly_total",
                "context_chunks": [],
                "related_docs": [],
                "hit_count": 1,
                "doc_count": 1,
                "bilingual_search": False,
                "qdrant_used": False,
                "retrieval_mode": "structured",
                "vector_hit_count": 0,
                "lexical_hit_count": 0,
                "fallback_reason": "",
                "facet_mode": "none",
                "facet_keys": [],
                "fact_route": "bill_monthly_total",
                "fact_month": "2026-02",
                "route_reason": "test",
                "bill_monthly": {
                    "month": "2026-02",
                    "pending": [],
                    "paid": [],
                    "total_amount": 109.0,
                    "currency": "AUD",
                },
                "sources": [],
                "related_doc_selection_mode": "evidence_only",
                "detail_mode": "structured",
                "detail_rows_count": 0,
            }

        @staticmethod
        def _synthesize_fallback(req, planner, bundle):
            return _dummy_response().card

    monkeypatch.setattr(agn, "_legacy_agent_module", lambda: LegacyStub)

    req = AgentExecuteRequest(query="2月份的账单有哪些？一共多少钱？", ui_lang="zh", query_lang="zh")
    planner = PlannerDecision(
        intent="period_aggregate",
        confidence=0.9,
        doc_scope={},
        actions=[],
        fallback="none",
        ui_lang="zh",
        query_lang="zh",
        task_kind="aggregate_lookup",
        subject_domain="bills",
        query_spec={"version": "v2", "task_kind": "aggregate_lookup", "subject_domain": "bills"},
    )
    out = agn.structured_fastpath_node(
        {
            "planner": planner.model_dump(),
            "loop_budget": 2,
            "timing": {"planner_latency_ms": 123},
        },
        config={"configurable": {"raw_req": req, "db": None, "logger": None}},
    )

    resp = out["response"]
    assert seen["execute_plan_called"] is True
    assert seen["req_planner"] is not None
    assert getattr(seen["req_planner"], "intent", "") == "period_aggregate"
    assert resp.executor_stats.graph_planner_reused_in_delegate is True
    assert resp.executor_stats.graph_llm_calls_planner == 1
    assert resp.executor_stats.planner_latency_ms == 123
    assert resp.executor_stats.graph_terminal_reason == "structured_fastpath_native"
