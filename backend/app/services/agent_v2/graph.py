"""Agent V2 - LangGraph Definition

Unified graph architecture for the Family Vault agent.
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
from app.services.agent_v2.edges.conditions import should_chitchat, should_retry, is_answerability_insufficient

# Build the graph
builder = StateGraph(AgentGraphState)

# Add nodes
builder.add_node("router_node", router_node)
builder.add_node("chitchat_node", chitchat_node)
builder.add_node("retrieve_node", retriever_node)
builder.add_node("synthesize_node", synthesizer_node)
builder.add_node("recovery_node", recovery_node)

# Add edges
builder.add_edge(START, "router_node")

# Conditional: chitchat short-circuit
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
        True: "retrieve_node",  # Retry with relaxed constraints
        False: "synthesize_node"  # Give up and synthesize with what we have
    }
)

# Synthesize -> END
builder.add_edge("synthesize_node", END)

# Chitchat -> END
builder.add_edge("chitchat_node", END)

# Compile the graph
graph = builder.compile()


async def execute(req: AgentExecuteRequest, db=None) -> AgentExecuteResponse:
    """Execute agent with the new LangGraph architecture.
    
    This is the main entry point for Agent V2.
    
    Args:
        req: The execution request
        db: Database session (required for retrieval)
    """
    # Initialize state
    initial_state: AgentGraphState = {
        "req": req.model_dump(),
        "trace_id": f"agt-{uuid.uuid4().hex[:12]}",
        "timing": {"start_ms": int(time.time() * 1000)},
        "loop_budget": 3,  # Max recovery loops
        "loop_count": 0,
    }
    
    # Execute graph with config (passes db to nodes)
    config = {"configurable": {"db": db}} if db else None
    result = await graph.ainvoke(initial_state, config=config)
    
    # Construct response
    return AgentExecuteResponse(
        card=result.get("final_card_payload", {}),
        planner=result.get("router", {}),
        executor_stats=result.get("executor_stats_payload", {}),
        related_docs=result.get("related_docs_payload", []),
        trace_id=result.get("trace_id", "")
    )
