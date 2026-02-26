import pytest

from app.schemas import AgentExecuteRequest, AgentExecuteResponse, AgentExecutorStats, BilingualText, PlannerDecision, ResultCard
from app.services import agent as agent_service

pytestmark = pytest.mark.no_db_reset


def _dummy_response() -> AgentExecuteResponse:
    return AgentExecuteResponse(
        planner=PlannerDecision(
            intent="search_semantic",
            confidence=0.5,
            doc_scope={},
            actions=["search_documents"],
            fallback="search_semantic",
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
        executor_stats=AgentExecutorStats(route="search_bundle"),
    )


def test_execute_agent_graph_fail_open_falls_back_to_legacy(monkeypatch):
    monkeypatch.setattr(agent_service.settings, "agent_graph_enabled", True)
    monkeypatch.setattr(agent_service.settings, "agent_graph_fail_open", True)
    monkeypatch.setattr(agent_service.settings, "agent_graph_shadow_enabled", False)

    dummy = _dummy_response()
    monkeypatch.setattr(agent_service, "execute_agent_v2", lambda db, req: dummy)

    def _boom(*args, **kwargs):
        raise RuntimeError("graph boom")

    monkeypatch.setattr("app.services.agent_graph.execute_agent_graph", _boom)

    out = agent_service.execute_agent(db=None, req=AgentExecuteRequest(query="test", ui_lang="zh", query_lang="zh"))
    assert out.trace_id == "t1"
    assert out.executor_stats.route == "search_bundle"
