"""Agent V2 - LangGraph Definition

Unified graph architecture for the Family Vault agent.
"""

import time
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.schemas import AgentExecuteRequest, AgentExecuteResponse
from app.services.agent_v2.state import AgentGraphState
from app.services.agent_v2.nodes import router, retriever, synthesizer, chitchat
from app.services.agent_v2.edges.conditions import should_chitchat, should_retry

# Build the graph
builder = StateGraph(AgentGraphState)

# Add nodes
builder.add_node("router", router.node)
builder.add_node("chitchat", chitchat.node)
builder.add_node("retrieve", retriever.node)
builder.add_node("synthesize", synthesizer.node)

# Add edges
builder.add_edge(START, "router")

# Conditional: chitchat short-circuit
builder.add_conditional_edges(
    "router",
    should_chitchat,
    {
        True: "chitchat",
        False: "retrieve"
    }
)

# Retrieve -> Synthesize (with potential retry loop)
builder.add_edge("retrieve", "synthesize")

# Synthesize -> END or retry
# TODO: Add recovery/retry logic in Phase 2
builder.add_edge("synthesize", END)

# Chitchat -> END
builder.add_edge("chitchat", END)

# Compile the graph
graph = builder.compile()


async def execute(req: AgentExecuteRequest) -> AgentExecuteResponse:
    """Execute agent with the new LangGraph architecture.
    
    This is the main entry point for Agent V2.
    """
    # Initialize state
    initial_state: AgentGraphState = {
        "req": req.model_dump(),
        "trace_id": f"agt-{uuid.uuid4().hex[:12]}",
        "timing": {"start_ms": int(time.time() * 1000)},
        "loop_budget": 3,  # Max recovery loops
        "loop_count": 0,
    }
    
    # Execute graph
    result = await graph.ainvoke(initial_state)
    
    # Construct response
    return AgentExecuteResponse(
        card=result.get("final_card_payload", {}),
        planner=result.get("router", {}),
        executor_stats=result.get("executor_stats_payload", {}),
        related_docs=result.get("related_docs_payload", []),
        trace_id=result.get("trace_id", "")
    )
