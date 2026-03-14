"""Agent V2 Edges - Conditions

Conditional routing logic for the agent graph.
"""

from app.services.agent_v2.state import AgentGraphState


def should_chitchat(state: AgentGraphState) -> bool:
    """Determine if the route is chitchat."""
    return state.get("route") == "chitchat"


def should_retry(state: AgentGraphState) -> bool:
    """Determine if we should retry/recover.
    
    TODO: Implement full retry logic with loop budget check.
    """
    loop_count = state.get("loop_count", 0)
    loop_budget = state.get("loop_budget", 3)
    
    # Don't retry if we've exhausted budget
    if loop_count >= loop_budget:
        return False
    
    # TODO: Add actual retry conditions based on answerability
    return False
