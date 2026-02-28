from typing import Any, Literal, TypedDict

Answerability = Literal["sufficient", "partial", "insufficient", "none"]


class AgentGraphState(TypedDict, total=False):
    req: dict[str, Any]
    planner: dict[str, Any]
    query_spec: dict[str, Any]
    subtasks: list[dict[str, Any]]
    route: str
    route_reason: str
    trace_id: str
    context_policy: str

    query_variants: list[str]
    candidate_hits: list[dict[str, Any]]
    candidate_docs: list[dict[str, Any]]
    ranked_docs: list[dict[str, Any]]
    context_chunks: list[dict[str, Any]]
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
    answerability: Answerability
    partial_evidence_signals: list[str]
    refusal_blockers: list[str]
    answer_posture: str
    force_refusal_reason: str

    recovery_plan: dict[str, Any]
    loop_budget: int
    loop_count: int
    loop_progress_history: list[dict[str, Any]]

    final_card_payload: dict[str, Any]
    executor_stats_payload: dict[str, Any]
    related_docs_payload: list[dict[str, Any]]
    timing: dict[str, Any]

    terminal: bool
    terminal_reason: str

    response: Any
