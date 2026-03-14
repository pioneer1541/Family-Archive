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


def is_simple_query(state: AgentGraphState) -> bool:
    """Check if query was classified as simple (use single-LLM mode).

    Returns True for simple queries -> go to unified_synthesizer
    Returns False for complex queries -> go to router (dual-LLM mode)
    """
    classifier = state.get("classifier", {})
    complexity = classifier.get("complexity", "complex")
    return complexity == "simple"


def is_complex_query(state: AgentGraphState) -> bool:
    """Check if query was classified as complex (use dual-LLM mode).

    Returns True for complex queries -> go to router
    Returns False for simple queries -> go to unified_synthesizer
    """
    return not is_simple_query(state)


def is_chitchat_shortcircuit(state: AgentGraphState) -> bool:
    """Check if query is chitchat (short-circuit with 0 LLM calls).

    Phase 3.2: Chitchat detected at classifier level, route directly to END.
    """
    return state.get("terminal") is True and state.get("terminal_reason") == "chitchat_complete"


def get_classifier_next_node(state: AgentGraphState) -> str:
    """Determine next node after query_classifier.

    Returns one of: "end", "unified_synthesize", "router"
    """
    # Phase 3.2: Chitchat short-circuit - 0 LLM calls
    if state.get("terminal") is True and state.get("terminal_reason") == "chitchat_complete":
        return "end"

    # Phase 2: Check complexity for single vs dual LLM mode
    classifier = state.get("classifier", {})
    complexity = classifier.get("complexity", "complex")

    if complexity == "simple":
        return "unified_synthesize"  # 1 LLM call
    else:
        return "router"  # 2 LLM calls
