import pytest

from app.services.agent_graph_nodes import recovery_decision, route_decision

pytestmark = pytest.mark.no_db_reset


def test_route_and_recovery_decision_helpers():
    assert route_decision({"route": "structured_fastpath"}) == "structured_fastpath"
    assert route_decision({"route": "query_retrieval"}) == "query_retrieval"
    assert recovery_decision({"terminal": True}) == "answer"
    assert recovery_decision({"terminal": False}) == "recover"


def test_agent_graph_compiles_if_langgraph_installed():
    pytest.importorskip("langgraph")
    from app.services.agent_graph import _compiled_agent_graph

    graph = _compiled_agent_graph()
    assert graph is not None
