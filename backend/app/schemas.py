from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class BilingualText(BaseModel):
    en: str = ""
    zh: str = ""


class IngestionJobCreateRequest(BaseModel):
    file_paths: list[str] = Field(min_length=1)


class IngestionJobResponse(BaseModel):
    job_id: str
    status: str
    input_paths: list[str]
    success_count: int
    failed_count: int
    duplicate_count: int
    error_code: str | None = None
    retry_count: int = 0
    max_retries: int = 0
    queue_mode: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IngestionJobDeleteResponse(BaseModel):
    job_id: str
    deleted: bool
    ignored_paths: int = 0
    detail: str = ""


class NasScanRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    recursive: bool = True
    max_files: int = Field(default=2000, ge=1, le=50000)


class NasScanResponse(BaseModel):
    paths: list[str]
    candidate_files: int
    changed_files: int
    missing_paths: int = 0
    queued: bool
    queue_mode: str
    job_id: str = ""


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    ui_lang: Literal["zh", "en"] = "zh"
    query_lang: Literal["zh", "en", "auto"] = "auto"
    category_path: str | None = None
    tags_all: list[str] = Field(default_factory=list, max_length=20)
    tags_any: list[str] = Field(default_factory=list, max_length=20)
    include_missing: bool = False


class SearchHit(BaseModel):
    doc_id: str
    chunk_id: str
    score: float
    text_snippet: str
    matched_query: str
    doc_lang: str
    title_en: str
    title_zh: str
    category_path: str
    source_type: str
    updated_at: datetime
    tags: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    query_en: str = ""
    bilingual: bool = False
    hits: list[SearchHit]
    qdrant_used: bool = False
    retrieval_mode: str = "none"
    vector_hit_count: int = 0
    lexical_hit_count: int = 0


class DocumentChunk(BaseModel):
    chunk_id: str
    chunk_index: int
    token_count: int
    content: str


class DocumentResponse(BaseModel):
    doc_id: str
    source_path: str
    source_path_included: bool = False
    file_name: str
    file_ext: str
    file_size: int
    sha256: str
    status: str
    duplicate_of: str | None = None
    error_code: str | None = None
    doc_lang: str
    title_en: str
    title_zh: str
    summary_en: str
    summary_zh: str
    category_label_en: str
    category_label_zh: str
    category_path: str
    summary_quality_state: str = "unknown"
    summary_last_error: str = ""
    summary_model: str = ""
    summary_version: str = "prompt-v2"
    category_version: str = "taxonomy-v1"
    name_version: str = "name-v2"
    source_available: bool = True
    source_missing_reason: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    chunks_included: bool = False
    chunks: list[DocumentChunk]
    # OCR truncation info (PDFs only; None means not applicable or not yet computed)
    ocr_pages_total: int | None = None
    ocr_pages_processed: int | None = None
    # Long-document sampling info (populated after map-reduce summary)
    longdoc_mode: str | None = None
    longdoc_pages_total: int | None = None
    longdoc_pages_used: int | None = None


class DocumentListItem(BaseModel):
    doc_id: str
    file_name: str
    status: str
    doc_lang: str
    title_en: str
    title_zh: str
    summary_en: str = ""
    summary_zh: str = ""
    category_path: str
    category_label_en: str = ""
    category_label_zh: str = ""
    source_available: bool = True
    source_missing_reason: str = ""
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime


class DocumentTagItem(BaseModel):
    key: str
    family: str
    value: str
    origin: str
    label_en: str
    label_zh: str


class DocumentTagsPatchRequest(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=30)
    remove: list[str] = Field(default_factory=list, max_length=30)


class DocumentTagsResponse(BaseModel):
    doc_id: str
    tags: list[DocumentTagItem]


class TagCatalogItem(BaseModel):
    key: str
    family: str
    value: str
    label_en: str
    label_zh: str
    doc_count: int


class TagCatalogResponse(BaseModel):
    total: int
    items: list[TagCatalogItem]


class DocumentListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DocumentListItem]


class DocumentContentAvailabilityResponse(BaseModel):
    doc_id: str
    source_available: bool
    inline_supported: bool
    detail: str


class FriendlyNameUpdateRequest(BaseModel):
    friendly_name_zh: str | None = Field(default=None, max_length=512)
    friendly_name_en: str | None = Field(default=None, max_length=512)


class FriendlyNameResponse(BaseModel):
    doc_id: str
    friendly_name_zh: str
    friendly_name_en: str
    updated_at: datetime


class CategoryItem(BaseModel):
    category_path: str
    label_en: str
    label_zh: str
    doc_count: int


class CategoriesResponse(BaseModel):
    total_categories: int
    items: list[CategoryItem]


class GovernanceScopeMetrics(BaseModel):
    status_filter: str
    total_docs: int = 0
    legacy_docs: int = 0
    legacy_ratio: float = 0.0


class GovernanceLegacyFile(BaseModel):
    doc_id: str
    file_name: str
    status: str
    category_path: str
    updated_at: str


class GovernanceCategoryDebtResponse(BaseModel):
    snapshot_at: str
    scope_prod: GovernanceScopeMetrics
    scope_audit: GovernanceScopeMetrics
    legacy_counts_by_status: dict[str, int] = Field(default_factory=dict)
    legacy_ratio_by_status: dict[str, float] = Field(default_factory=dict)
    top_legacy_files: list[GovernanceLegacyFile] = Field(default_factory=list)


class GovernanceCategoryDebtTrendPoint(BaseModel):
    snapshot_at: str
    prod_legacy_docs: int = 0
    prod_total_docs: int = 0
    audit_legacy_docs: int = 0
    audit_total_docs: int = 0


class GovernanceCategoryDebtTrendResponse(BaseModel):
    days: int = 30
    snapshot_count: int = 0
    week_over_week_change: int = 0
    points: list[GovernanceCategoryDebtTrendPoint] = Field(default_factory=list)


class QueueJobItem(BaseModel):
    job_id: str
    status: str
    success_count: int
    failed_count: int
    duplicate_count: int
    error_code: str | None = None
    created_at: datetime


class QueueDocumentItem(BaseModel):
    doc_id: str
    file_name: str
    status: str
    updated_at: datetime


class QueueResponse(BaseModel):
    jobs: list[QueueJobItem]
    documents: list[QueueDocumentItem]
    totals: dict[str, int]


class SyncRunStartRequest(BaseModel):
    nas_paths: list[str] = Field(default_factory=list)
    recursive: bool = True
    mail_max_results: int | None = Field(default=None, ge=1, le=100)


class SyncSourceSummary(BaseModel):
    candidate_files: int = 0
    changed_files: int = 0
    queued: bool = False
    job_id: str = ""
    polled_messages: int = 0
    processed_messages: int = 0
    downloaded_attachments: int = 0


class SyncRunStartResponse(BaseModel):
    run_id: str
    status: str
    started_at: datetime
    last_sync_at: datetime | None = None
    dispatch_status: str = "queued"
    dispatch_error: str = ""
    nas: SyncSourceSummary = Field(default_factory=SyncSourceSummary)
    mail: SyncSourceSummary = Field(default_factory=SyncSourceSummary)


class SyncRunItemResponse(BaseModel):
    item_id: str
    source_type: str
    file_name: str
    file_size: int
    stage: str
    doc_id: str | None = None
    updated_at: datetime
    detail: str = ""


class SyncRunSummary(BaseModel):
    total: int = 0
    discovered: int = 0
    queued: int = 0
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    duplicate: int = 0
    skipped: int = 0
    active_count: int = 0
    terminal_count: int = 0
    progress_pct: int = 100
    is_active: bool = False


class SyncRunDetailResponse(BaseModel):
    run_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    summary: SyncRunSummary = Field(default_factory=SyncRunSummary)
    items: list[SyncRunItemResponse] = Field(default_factory=list)


class SyncLastResponse(BaseModel):
    last_sync_at: datetime | None = None
    last_run_status: str = ""
    last_run_id: str | None = None


class ReprocessResponse(BaseModel):
    doc_id: str
    job_id: str
    status: str


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    doc_set: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    task_id: str
    title: str
    task_type: str
    doc_set: list[str]
    filters: dict[str, Any]
    summary: BilingualText
    status: str
    created_time: datetime
    updated_time: datetime


class TaskListItem(BaseModel):
    task_id: str
    title: str
    task_type: str
    status: str
    updated_time: datetime


class TaskListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TaskListItem]


class PlannerRequest(BaseModel):
    query: str = Field(min_length=1)
    ui_lang: Literal["zh", "en"] = "zh"
    query_lang: Literal["zh", "en", "auto"] = "auto"
    doc_scope: dict[str, Any] = Field(default_factory=dict)


class PlannerDecision(BaseModel):
    intent: str
    confidence: float
    doc_scope: dict[str, Any]
    actions: list[str]
    fallback: str
    ui_lang: str
    query_lang: str
    route_reason: str = ""
    required_evidence_fields: list[str] = Field(default_factory=list)
    refusal_candidate: bool = False
    task_kind: str = ""
    subject_domain: str = ""
    target_slots: list[str] = Field(default_factory=list)
    query_spec: dict[str, Any] = Field(default_factory=dict)
    query_spec_version: str = ""


class AgentExecuteRequest(BaseModel):
    query: str = Field(min_length=1)
    ui_lang: Literal["zh", "en"] = "zh"
    query_lang: Literal["zh", "en", "auto"] = "auto"
    doc_scope: dict[str, Any] = Field(default_factory=dict)
    planner: PlannerDecision | None = None
    conversation: list[dict[str, str]] = Field(default_factory=list, max_length=12)
    client_context: dict[str, Any] = Field(default_factory=dict)


class ResultCardSource(BaseModel):
    doc_id: str
    chunk_id: str
    label: str


class ResultCardAction(BaseModel):
    key: str
    label_en: str
    label_zh: str
    action_type: str = "suggestion"
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_confirm: bool = False
    confirm_text_en: str = ""
    confirm_text_zh: str = ""


class DetailEvidenceRef(BaseModel):
    doc_id: str
    chunk_id: str
    evidence_text: str = ""


class DetailRow(BaseModel):
    field: str
    label_en: str = ""
    label_zh: str = ""
    value_en: str = ""
    value_zh: str = ""
    evidence_refs: list[DetailEvidenceRef] = Field(default_factory=list)


class DetailSection(BaseModel):
    section_name: str
    rows: list[DetailRow] = Field(default_factory=list)


class DetailCoverageStats(BaseModel):
    docs_scanned: int = 0
    docs_matched: int = 0
    fields_filled: int = 0


class ResultCard(BaseModel):
    title: str
    short_summary: BilingualText
    key_points: list[BilingualText]
    sources: list[ResultCardSource]
    actions: list[ResultCardAction]
    detail_sections: list[DetailSection] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    coverage_stats: DetailCoverageStats = Field(default_factory=DetailCoverageStats)
    evidence_summary: list[str] = Field(default_factory=list)
    insufficient_evidence: bool = False


class AgentRelatedDoc(BaseModel):
    doc_id: str
    file_name: str
    title_en: str
    title_zh: str
    summary_en: str = ""
    summary_zh: str = ""
    category_path: str
    category_label_en: str = ""
    category_label_zh: str = ""
    tags: list[str] = Field(default_factory=list)
    source_available: bool = True
    source_missing_reason: str = ""
    updated_at: datetime


class AgentExecutorStats(BaseModel):
    hit_count: int = 0
    doc_count: int = 0
    used_chunk_count: int = 0
    route: str = ""
    bilingual_search: bool = False
    qdrant_used: bool = False
    retrieval_mode: str = "none"
    vector_hit_count: int = 0
    lexical_hit_count: int = 0
    fallback_reason: str = ""
    facet_mode: str = "none"
    facet_keys: list[str] = Field(default_factory=list)
    context_policy: str = "fresh_turn"
    fact_route: str = "none"
    fact_month: str = ""
    synth_fallback_used: bool = False
    synth_error_code: str = ""
    detail_topic: str = ""
    detail_mode: str = ""
    detail_rows_count: int = 0
    answerability: str = "sufficient"
    coverage_ratio: float = 1.0
    field_coverage_ratio: float = 1.0
    coverage_missing_fields: list[str] = Field(default_factory=list)
    query_required_terms: list[str] = Field(default_factory=list)
    subject_anchor_terms: list[str] = Field(default_factory=list)
    subject_coverage_ok: bool = True
    target_field_terms: list[str] = Field(default_factory=list)
    target_field_coverage_ok: bool = True
    infra_guard_applied: bool = False
    locale_response_mode: str = "zh_native"
    answer_mode: str = "search_summary"
    evidence_backed_doc_count: int = 0
    related_doc_selection_mode: str = "evidence_plus_candidates"
    subject_entity: str = ""
    route_reason: str = ""
    graph_enabled: bool = False
    graph_path: str = ""
    graph_loop_budget: int = 0
    graph_loops_used: int = 0
    graph_terminal_reason: str = ""
    required_slots: list[str] = Field(default_factory=list)
    critical_missing_slots: list[str] = Field(default_factory=list)
    slot_coverage_ratio: float = 0.0
    critical_slot_coverage_ratio: float = 0.0
    query_variants: list[str] = Field(default_factory=list)
    recovery_actions_applied: list[str] = Field(default_factory=list)
    answer_posture: str = ""
    force_refusal_reason: str = ""
    slot_fallback_used: bool = False
    slot_evidence_doc_count: int = 0
    evidence_link_quality: str = ""
    partial_evidence_signals: list[str] = Field(default_factory=list)
    refusal_blockers: list[str] = Field(default_factory=list)
    planner_latency_ms: int = 0
    executor_latency_ms: int = 0
    synth_latency_ms: int = 0
    graph_node_latencies_ms: dict[str, int] = Field(default_factory=dict)
    graph_search_calls: int = 0
    graph_retrieval_latency_ms: int = 0
    graph_rerank_latency_ms: int = 0
    graph_expand_latency_ms: int = 0
    graph_extract_latency_ms: int = 0
    graph_judge_latency_ms: int = 0
    graph_recovery_latency_ms: int = 0
    graph_planner_reused_in_delegate: bool = False
    graph_llm_calls_planner: int = 0
    graph_llm_calls_synth: int = 0
    graph_llm_calls_total: int = 0
    graph_router_assist_triggered: bool = False
    graph_router_assist_reason: str = ""
    graph_router_rule_confidence: float = 0.0
    graph_router_llm_confidence: float = 0.0
    graph_router_selected_categories: list[str] = Field(default_factory=list)
    graph_router_kept_rule_categories: bool = False
    graph_router_assist_latency_ms: int = 0
    graph_router_assist_cache_hit: bool = False
    graph_router_assist_error_code: str = ""
    graph_router_assist_error_detail: str = ""
    graph_router_assist_used_url_fallback: bool = False


class AgentExecuteResponse(BaseModel):
    planner: PlannerDecision
    card: ResultCard
    related_docs: list[AgentRelatedDoc] = Field(default_factory=list)
    trace_id: str = ""
    executor_stats: AgentExecutorStats = Field(default_factory=AgentExecutorStats)


class MapReduceSummaryRequest(BaseModel):
    doc_id: str = Field(min_length=1)
    ui_lang: Literal["zh", "en"] = "zh"
    chunk_group_size: int = Field(default=6, ge=2, le=20)


class MapReduceSummarySection(BaseModel):
    index: int
    chunk_range: str
    summary: BilingualText


class MapReduceSummaryResponse(BaseModel):
    doc_id: str
    status: str
    short_summary: BilingualText
    sections: list[MapReduceSummarySection]
    sources: list[ResultCardSource]
    total_chunks: int
    used_chunks: int
    latency_ms: int
    quality_state: Literal["ok", "needs_regen", "llm_failed"] = "needs_regen"
    fallback_used: bool = False
    quality_flags: list[str] = Field(default_factory=list)
    longdoc_mode: Literal["normal", "sampled"] = "normal"
    pages_total: int = 0
    pages_used: int = 0
    applied: bool = False
    apply_reason: str = ""
    category_recomputed: bool = False
    tags_recomputed: bool = False
    qdrant_synced: bool = False
    cascade_applied: bool = False
    cascade_reason: str = ""


class SystemPromptsResponse(BaseModel):
    version: str
    hash: str
    items: dict[str, str]


class MailPollRequest(BaseModel):
    max_results: int | None = Field(default=None, ge=1, le=100)


class MailPollResponse(BaseModel):
    polled_messages: int
    processed_messages: int
    downloaded_attachments: int
    queued: bool
    queue_mode: str
    job_id: str = ""
    detail: str = ""


class MailEventItem(BaseModel):
    id: str
    message_id: str
    subject: str
    from_addr: str
    attachment_name: str
    attachment_path: str
    status: str
    detail: str
    created_at: datetime


class MailEventsResponse(BaseModel):
    total: int
    items: list[MailEventItem]


class MailHealthResponse(BaseModel):
    enabled: bool
    status: str  # "ok" | "disabled" | error code from _gmail_service()
    detail: str


class HealthResponse(BaseModel):
    service: str
    version: str
    status: str


# ---------------------------------------------------------------------------
# User authentication schemas
# ---------------------------------------------------------------------------


class UserRegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class UserLoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class UserChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    user_id: str
    email: str
    role: str
    created_at: datetime


class AuthStatusResponse(BaseModel):
    setup_complete: bool
    authenticated: bool = False
    user: UserResponse | None = None


# ---------------------------------------------------------------------------
# Gmail Credentials schemas
# ---------------------------------------------------------------------------


class GmailCredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=256)
    client_secret: str = Field(min_length=1)
    redirect_uri: str = Field(default="http://localhost", max_length=512)
    token: str | None = None
    refresh_token: str | None = None
    token_uri: str = Field(default="https://oauth2.googleapis.com/token", max_length=256)
    auth_uri: str = Field(default="https://accounts.google.com/o/oauth2/auth", max_length=256)
    scopes: str = Field(default="https://www.googleapis.com/auth/gmail.readonly")


class GmailCredentialUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    client_id: str | None = Field(default=None, max_length=256)
    client_secret: str | None = None
    redirect_uri: str | None = Field(default=None, max_length=512)
    token: str | None = None
    refresh_token: str | None = None
    token_uri: str | None = Field(default=None, max_length=256)
    auth_uri: str | None = Field(default=None, max_length=256)
    scopes: str | None = None
    is_active: bool | None = None


class GmailCredentialItem(BaseModel):
    id: str
    name: str
    client_id_masked: str
    redirect_uri: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class GmailCredentialDetail(BaseModel):
    id: str
    name: str
    client_id: str
    client_secret_masked: str
    redirect_uri: str
    token_uri: str
    auth_uri: str
    scopes: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class GmailCredentialListResponse(BaseModel):
    items: list[GmailCredentialItem]
    total: int
