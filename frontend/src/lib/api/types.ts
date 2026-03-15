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
  // V1 compatible fields
  stage: string;
  label: AgentStageLabel;
  done: boolean;
  result?: AgentRunResult;
  error?: boolean;
  detail?: string;
  // V2 extended fields
  eventType?: 'start' | 'progress' | 'chunk' | 'end' | 'error';
  node?: string;
  data?: {
    complexity?: 'simple' | 'complex';
    confidence?: number;
    method?: 'rule' | 'llm' | 'ab_test' | 'chitchat';
    hitCount?: number;
    docCount?: number;
    answerability?: string;
    content?: BilingualText;
    llmCalls?: number;
    costSaving?: number;
  };
  traceId?: string;
  timestamp?: number;
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
  nas: {ok: boolean; path: string; readable: boolean; writable: boolean; error?: string | null};
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

// ---------------------------------------------------------------------------
// User & Auth
// ---------------------------------------------------------------------------

export interface UserResponse {
  id: string;
  username: string;
  email?: string | null;
  role?: string;
  created_at: string;
}

export interface AdminCreateUserPayload {
  username: string;
  password: string;
  email?: string;
  role?: 'admin' | 'user';
}

export interface UserListResult {
  total: number;
  items: UserResponse[];
}

// Gmail credential types
export interface GmailCredential {
  id: string;
  name: string;
  client_id: string;
  has_token: boolean;
  created_at: string;
  updated_at: string;
}

export interface GmailCredentialCreate {
  name: string;
  client_id: string;
  client_secret: string;
}

export interface GmailCredentialUpdate {
  name?: string;
  client_id?: string;
  client_secret?: string;
}

export interface GmailDeviceAuthStart {
  device_code: string;
  user_code: string;
  verification_url: string;
  expires_in: number;
  interval: number;
}

export interface GmailDeviceAuthComplete {
  status: 'pending' | 'slow_down' | 'completed';
  credential_id?: string | null;
}

export type LLMProviderType = 'ollama' | 'openai' | 'kimi' | 'glm' | 'custom';

export interface LLMProvider {
  id: string;
  name: string;
  provider_type: LLMProviderType;
  base_url: string;
  has_api_key: boolean;
  model_name: string;
  is_active: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface LLMProviderCreate {
  name: string;
  provider_type: LLMProviderType;
  base_url: string;
  api_key?: string;
  model_name: string;
  is_active: boolean;
  is_default: boolean;
}

export interface LLMProviderUpdate {
  name?: string;
  provider_type?: LLMProviderType;
  base_url?: string;
  api_key?: string;
  model_name?: string;
  is_active?: boolean;
  is_default?: boolean;
}

export interface LLMProviderTestResult {
  ok: boolean;
  latency_ms: number;
  models: string[];
  error?: string | null;
}

export interface LLMProviderValidateRequest {
  provider_id?: string;
  name?: string;
  provider_type: LLMProviderType;
  base_url: string;
  api_key?: string;
  model_name: string;
  is_active: boolean;
}

export interface LLMProviderValidateResult {
  ok: boolean;
  latency_ms: number;
  models: string[];
  normalized_base_url: string;
  error?: string | null;
}

// ---------------------------------------------------------------------------
// API Client Interface
// ---------------------------------------------------------------------------

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
  authLogin?(username: string, password: string): Promise<void>;
  authRegister?(username: string, password: string, email?: string): Promise<void>;
  authLogout?(): Promise<void>;
  changePassword?(oldPassword: string, newPassword: string): Promise<void>;
  getMe?(): Promise<UserResponse | null>;
  listUsers?(): Promise<UserListResult>;
  createUser?(payload: AdminCreateUserPayload): Promise<UserResponse>;
  deleteUser?(userId: string): Promise<void>;
  // Settings
  getSettings?(): Promise<AppSettingItem[]>;
  updateSettings?(patch: Record<string, string>): Promise<void>;
  getOllamaModels?(): Promise<OllamaModel[]>;
  getConnectivity?(): Promise<ConnectivityStatus>;
  // Keywords
  getKeywords?(): Promise<KeywordLists>;
  updateKeywords?(patch: Partial<KeywordLists>): Promise<void>;
  // Restart
  restartServices?(): Promise<{ok: boolean; message?: string; error?: string; manual?: boolean}>;
  // Gmail Credentials
  getGmailCredentials?(): Promise<GmailCredential[]>;
  getGmailAuthUrl?(credId: string, redirectUri?: string): Promise<{auth_url: string}>;
  startGmailDeviceAuth?(): Promise<GmailDeviceAuthStart>;
  completeGmailDeviceAuth?(deviceCode: string): Promise<GmailDeviceAuthComplete>;
  createGmailCredential?(data: GmailCredentialCreate): Promise<GmailCredential>;
  updateGmailCredential?(id: string, data: GmailCredentialUpdate): Promise<GmailCredential>;
  deleteGmailCredential?(id: string): Promise<void>;
  // LLM Providers
  getLLMProviders?(): Promise<LLMProvider[]>;
  createLLMProvider?(data: LLMProviderCreate): Promise<LLMProvider>;
  updateLLMProvider?(id: string, data: LLMProviderUpdate): Promise<LLMProvider>;
  deleteLLMProvider?(id: string): Promise<void>;
  testLLMProvider?(id: string): Promise<LLMProviderTestResult>;
  validateLLMProvider?(data: LLMProviderValidateRequest): Promise<LLMProviderValidateResult>;
  getLLMProviderModels?(id: string): Promise<string[]>;
}
