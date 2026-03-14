"""Agent V2 Edges - Conditions

Conditional routing logic for the agent graph.
"""

from app.services.agent_v2.state import AgentGraphState


def should_chitchat(state: AgentGraphState) -> bool:
    """Determine if the route is chitchat."""
    return state.get("route") == "chitchat"


def is_answerability_insufficient(state: AgentGraphState) -> bool:
    """Check if answerability is insufficient and we need recovery."""
    answerability = state.get("answerability", "sufficient")
    return answerability in ("insufficient", "none")


def should_retry(state: AgentGraphState) -> bool:
    """Determine if we should retry/recover.
    
    Returns True if we have budget left and haven't exhausted retries.
    """
    loop_count = state.get("loop_count", 0)
    loop_budget = state.get("loop_budget", 3)
    
    # Don't retry if we've exhausted budget
    if loop_count >= loop_budget:
        return False
    
    # Check if recovery plan exists (indicates we attempted recovery)
    if state.get("recovery_plan"):
        return True
    
    return False
