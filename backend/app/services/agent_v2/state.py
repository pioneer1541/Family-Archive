"""Agent V2 - LangGraph State Definition

TypedDict state for the unified LangGraph agent architecture.
"""

from typing import Any, Literal, TypedDict

Answerability = Literal["sufficient", "partial", "insufficient", "none"]


class AgentGraphState(TypedDict, total=False):
    """Unified state for the agent graph.
    
    All fields are optional (total=False) to allow incremental updates
    from each node.
    """
    
    # Input / Request
    req: dict[str, Any]

    # Phase 2: Query classification (single-LLM vs dual-LLM mode)
    classifier: dict[str, Any]  # {complexity: "simple"|"complex", confidence: float, method: "rule"|"llm"}

    # Routing
    route: str  # "chitchat", "lookup", "calculate", "system", "detail_extract"
    route_reason: str
    router: dict[str, Any]  # Full router decision payload
    
    # Query understanding
    query_spec: dict[str, Any]
    subtasks: list[dict[str, Any]]
    
    # Trace / Observability
    trace_id: str
    context_policy: str
    
    # Retrieval
    query_variants: list[str]
    candidate_hits: list[dict[str, Any]]
    candidate_docs: list[dict[str, Any]]
    ranked_docs: list[dict[str, Any]]
    context_chunks: list[dict[str, Any]]
    
    # Slot extraction / Evidence
    slot_results: list[dict[str, Any]]
    derivations: list[dict[str, Any]]
    required_slots: list[str]
    critical_slots: list[str]
    slot_coverage_ratio: float
    critical_slot_coverage_ratio: float
    coverage_missing_slots: list[str]
    critical_missing_slots: list[str]
    subject_coverage_ok: bool
    target_field_coverage_ok: bool
    
    # Answer quality
    answerability: Answerability
    partial_evidence_signals: list[str]
    refusal_blockers: list[str]
    answer_posture: str  # "answer", "refuse", "clarify"
    force_refusal_reason: str
    
    # Recovery / Loop control
    recovery_plan: dict[str, Any]
    loop_budget: int
    loop_count: int
    loop_progress_history: list[dict[str, Any]]
    
    # Output
    final_card_payload: dict[str, Any]
    executor_stats_payload: dict[str, Any]
    related_docs_payload: list[dict[str, Any]]
    
    # Timing / Metrics
    timing: dict[str, Any]
    
    # Termination
    terminal: bool
    terminal_reason: str
