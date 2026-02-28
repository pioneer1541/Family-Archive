export type UiLocale = 'zh-CN' | 'en-AU';

export interface BilingualText {
  zh: string;
  en: string;
}

export interface KbDoc {
  id: string;
  fileName: string;
  fileExt: string;
  sourcePath: string;
  status: string;
  title: BilingualText;
  summary: BilingualText;
  categoryPath: string;
  categoryLabel: BilingualText;
  tags: string[];
  sourceAvailable: boolean;
  sourceMissingReason: string;
  summaryQualityState: string;
  summaryLastError: string;
  updatedAt: string;
  previewUrl: string;
  inlineUrl: string;
  downloadUrl: string;
  extractedText: string;
  // OCR truncation (PDFs only; null means not applicable or not yet computed)
  ocrPagesTotal: number | null;
  ocrPagesProcessed: number | null;
  // Long-document sampling (populated after map-reduce summary)
  longdocMode: string | null;
  longdocPagesTotal: number | null;
  longdocPagesUsed: number | null;
}

export interface KbCategory {
  id: string;
  path: string;
  label: BilingualText;
  icon: string;
  count: number;
  colorIndex: number;
}

export interface GetDocsParams {
  categoryPath?: string;
  includeMissing?: boolean;
  sourceState?: 'available' | 'missing' | 'all';
  q?: string;
  limit?: number;
  offset?: number;
}

export interface GetDocParams {
  includeChunks?: boolean;
  chunkLimit?: number;
  includeSourcePath?: boolean;
}

export interface PatchDocPayload {
  friendlyNameZh?: string;
  friendlyNameEn?: string;
}

export interface AgentRunPayload {
  query: string;
  locale: UiLocale;
  conversation?: Array<{role: 'user' | 'assistant'; content: string}>;
  clientContext?: Record<string, unknown>;
}

export interface AgentStageLabel {
  zh: string;
  en: string;
}

export interface AgentStreamEvent {
  stage: string;
  label: AgentStageLabel;
  done: boolean;
  result?: AgentRunResult;
  error?: boolean;
  detail?: string;
}

export type AgentRequestErrorKind = 'timeout' | 'network' | 'gateway' | 'server' | 'unknown';

export interface AgentRequestErrorShape {
  kind: AgentRequestErrorKind;
  status?: number;
  detail?: string;
  traceId?: string;
}

export interface AgentAction {
  key: string;
  labelEn: string;
  labelZh: string;
  actionType: string;
  payload: Record<string, unknown>;
  requiresConfirm: boolean;
  confirmTextEn: string;
  confirmTextZh: string;
}

export interface AgentDetailEvidenceRef {
  docId: string;
  chunkId: string;
  evidenceText: string;
}

export interface AgentDetailRow {
  field: string;
  labelEn: string;
  labelZh: string;
  valueEn: string;
  valueZh: string;
  evidenceRefs: AgentDetailEvidenceRef[];
}

export interface AgentDetailSection {
  sectionName: string;
  rows: AgentDetailRow[];
}

export interface AgentCoverageStats {
  docsScanned: number;
  docsMatched: number;
  fieldsFilled: number;
}

export interface AgentCard {
  title: string;
  shortSummary: BilingualText;
  keyPoints: BilingualText[];
  actions: AgentAction[];
  detailSections: AgentDetailSection[];
  missingFields: string[];
  coverageStats: AgentCoverageStats;
}

export interface AgentExecutorStats {
  hitCount: number;
  docCount: number;
  usedChunkCount: number;
  route: string;
  bilingualSearch: boolean;
  qdrantUsed: boolean;
  retrievalMode: string;
  vectorHitCount: number;
  lexicalHitCount: number;
  fallbackReason: string;
  facetMode: string;
  facetKeys: string[];
  contextPolicy: string;
  factRoute: string;
  factMonth: string;
  synthFallbackUsed: boolean;
  synthErrorCode: string;
  detailTopic: string;
  detailMode: string;
  detailRowsCount: number;
}

export interface AgentRunResult {
  answer: string;
  relatedDocs: KbDoc[];
  card: AgentCard | null;
  traceId: string;
  executorStats: AgentExecutorStats;
  partialRelatedDocs?: boolean;
}

export type DocumentContentAvailabilityDetail =
  | 'ok'
  | 'source_file_missing'
  | 'document_not_ready'
  | 'unsupported_media_type'
  | 'document_not_found'
  | 'availability_endpoint_missing'
  | 'availability_unreachable'
  | string;

export interface DocumentContentAvailability {
  sourceAvailable: boolean;
  inlineSupported: boolean;
  detail: DocumentContentAvailabilityDetail;
}

export interface RegenSummaryResult {
  doc: KbDoc | null;
  applied: boolean;
  applyReason: string;
  qualityState: string;
  qualityFlags: string[];
  categoryRecomputed: boolean;
  tagsRecomputed: boolean;
  qdrantSynced: boolean;
  cascadeApplied: boolean;
  cascadeReason: string;
}

export interface SyncSourceSummary {
  candidateFiles: number;
  changedFiles: number;
  queued: boolean;
  jobId: string;
  polledMessages: number;
  processedMessages: number;
  downloadedAttachments: number;
}

export interface SyncRunStartResult {
  runId: string;
  status: string;
  startedAt: string;
  lastSyncAt: string;
  dispatchStatus: string;
  dispatchError: string;
  nas: SyncSourceSummary;
  mail: SyncSourceSummary;
}

export interface SyncRunItem {
  itemId: string;
  sourceType: 'nas' | 'mail' | string;
  fileName: string;
  fileSize: number;
  stage: string;
  docId: string;
  updatedAt: string;
  detail: string;
}

export interface SyncRunDetail {
  runId: string;
  status: string;
  startedAt: string;
  finishedAt: string;
  summary: {
    total: number;
    discovered: number;
    queued: number;
    pending: number;
    processing: number;
    completed: number;
    failed: number;
    duplicate: number;
    skipped: number;
    activeCount: number;
    terminalCount: number;
    progressPct: number;
    isActive: boolean;
  };
  items: SyncRunItem[];
}

export interface SyncLastResult {
  lastSyncAt: string;
  lastRunStatus: string;
  lastRunId: string;
}

export interface MailHealthResponse {
  enabled: boolean;
  status: 'ok' | 'disabled' | string;
  detail: string;
}

// ---------------------------------------------------------------------------
// Settings & Auth
// ---------------------------------------------------------------------------

export interface AppSettingItem {
  key: string;
  value: string;
  source: 'env' | 'db' | 'default';
  type: 'model' | 'int' | 'bool' | 'string' | 'path' | 'json';
  category: 'llm' | 'nas' | 'mail' | 'ingestion' | 'timeout' | 'advanced' | 'keywords';
  label_zh: string;
  label_en: string;
}

export interface OllamaModel {
  name: string;
  size: number;
}

export interface ConnectivityStatus {
  ollama: {ok: boolean; model_count: number; latency_ms?: number; error?: string};
  qdrant: {ok: boolean; collection: string; error?: string};
  nas: {ok: boolean; path: string; error?: string};
  gmail: {ok: boolean; credentials_present: boolean; token_present: boolean};
}

export interface AuthStatus {
  setup_complete: boolean;
}

export interface KeywordLists {
  person_keywords: Record<string, string>;
  pet_keywords: Record<string, string>;
  location_keywords: Record<string, string>;
}

export interface KbApiClient {
  getDocs(params?: GetDocsParams): Promise<KbDoc[]>;
  getDocsPage?(params?: GetDocsParams): Promise<{items: KbDoc[]; total: number; limit: number; offset: number}>;
  getDoc(id: string, params?: GetDocParams): Promise<KbDoc | null>;
  patchDoc(id: string, payload: PatchDocPayload): Promise<KbDoc | null>;
  regenSummary(id: string, locale: UiLocale): Promise<RegenSummaryResult>;
  getContentAvailability(id: string): Promise<DocumentContentAvailability>;
  getCategories(): Promise<KbCategory[]>;
  runAgent(payload: AgentRunPayload): Promise<AgentRunResult>;
  streamAgent?(payload: AgentRunPayload, onEvent: (event: AgentStreamEvent) => void, signal?: AbortSignal): Promise<void>;
  getLastSync(): Promise<SyncLastResult>;
  startSync(): Promise<SyncRunStartResult>;
  getSyncRun(runId: string): Promise<SyncRunDetail | null>;
  getMailHealth?(): Promise<MailHealthResponse>;
  // Auth
  getAuthStatus?(): Promise<AuthStatus>;
  authSetup?(password: string): Promise<void>;
  authLogin?(password: string): Promise<void>;
  authLogout?(): Promise<void>;
  changePassword?(oldPassword: string, newPassword: string): Promise<void>;
  // Settings
  getSettings?(): Promise<AppSettingItem[]>;
  updateSettings?(patch: Record<string, string>): Promise<void>;
  getOllamaModels?(): Promise<OllamaModel[]>;
  getConnectivity?(): Promise<ConnectivityStatus>;
  // Keywords
  getKeywords?(): Promise<KeywordLists>;
  updateKeywords?(patch: Partial<KeywordLists>): Promise<void>;
}
