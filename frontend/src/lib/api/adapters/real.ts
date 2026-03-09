import {catIdFromPath, colorIndexForCategory, iconForCategory} from '@src/lib/category';
import type {
  AdminCreateUserPayload,
  AgentAction,
  AgentRequestErrorKind,
  AgentRequestErrorShape,
  AgentRunPayload,
  AgentRunResult,
  AgentStreamEvent,
  AppSettingItem,
  AuthStatus,
  ConnectivityStatus,
  DocumentContentAvailability,
  DocumentContentAvailabilityDetail,
  GetDocParams,
  GetDocsParams,
  KbApiClient,
  KbCategory,
  KbDoc,
  KeywordLists,
  MailHealthResponse,
  OllamaModel,
  PatchDocPayload,
  RegenSummaryResult,
  SyncLastResult,
  SyncRunDetail,
  SyncRunStartResult,
  SyncSourceSummary,
  UiLocale,
  UserListResult,
  UserResponse,
  GmailCredential,
  GmailCredentialCreate,
  GmailCredentialUpdate
} from '../types';

const API_BASE = process.env.NEXT_PUBLIC_FKV_API_BASE || '/api';
const GMAIL_V1_BASE = `${API_BASE}/v1/gmail/credentials`;
const GMAIL_LEGACY_BASE = `${API_BASE}/gmail/credentials`;
const DOC_CACHE_MAX = 200;
const DOC_CACHE_TTL_MS = 10 * 60 * 1000;
const docCache = new Map<string, {doc: KbDoc; ts: number}>();
const pendingDocRequests = new Map<string, Promise<KbDoc | null>>();

function docCacheGet(id: string): KbDoc | null {
  const key = String(id || '').trim();
  if (!key) return null;
  const hit = docCache.get(key);
  if (!hit) return null;
  if (Date.now() - hit.ts > DOC_CACHE_TTL_MS) {
    docCache.delete(key);
    return null;
  }
  // LRU touch
  docCache.delete(key);
  docCache.set(key, {doc: hit.doc, ts: Date.now()});
  return {...hit.doc};
}

function docCacheSet(doc: KbDoc | null): void {
  if (!doc) return;
  const key = String(doc.id || '').trim();
  if (!key) return;
  if (docCache.has(key)) docCache.delete(key);
  docCache.set(key, {doc: {...doc}, ts: Date.now()});
  while (docCache.size > DOC_CACHE_MAX) {
    const oldestKey = docCache.keys().next().value;
    if (!oldestKey) break;
    docCache.delete(oldestKey);
  }
}

interface RawDocItem {
  doc_id: string;
  file_name: string;
  file_ext?: string;
  source_path?: string;
  source_path_included?: boolean;
  preview_url?: string;
  status: string;
  doc_lang?: string;
  title_zh: string;
  title_en: string;
  summary_zh?: string;
  summary_en?: string;
  category_path: string;
  category_label_zh?: string;
  category_label_en?: string;
  source_available?: boolean;
  source_missing_reason?: string;
  summary_quality_state?: string;
  summary_last_error?: string;
  tags?: string[];
  updated_at: string;
  chunks_included?: boolean;
  chunks?: Array<{content?: string}>;
  ocr_pages_total?: number | null;
  ocr_pages_processed?: number | null;
  longdoc_mode?: string | null;
  longdoc_pages_total?: number | null;
  longdoc_pages_used?: number | null;
}

interface RawDocListResponse {
  total?: number;
  limit?: number;
  offset?: number;
  items?: RawDocItem[];
}

interface RawCategoryItem {
  category_path: string;
  label_zh?: string;
  label_en?: string;
  doc_count: number;
}

interface RawContentAvailability {
  source_available?: boolean;
  inline_supported?: boolean;
  detail?: string;
}

interface RawRegenResponse {
  quality_state?: string;
  quality_flags?: string[];
  applied?: boolean;
  apply_reason?: string;
  category_recomputed?: boolean;
  tags_recomputed?: boolean;
  qdrant_synced?: boolean;
  cascade_applied?: boolean;
  cascade_reason?: string;
}

interface RawAgentAction {
  key?: string;
  label_en?: string;
  label_zh?: string;
  action_type?: string;
  payload?: Record<string, unknown>;
  requires_confirm?: boolean;
  confirm_text_en?: string;
  confirm_text_zh?: string;
}

interface RawAgentResponse {
  card?: {
    title?: string;
    short_summary?: {zh?: string; en?: string};
    key_points?: Array<{zh?: string; en?: string}>;
    sources?: Array<{doc_id?: string; chunk_id?: string; label?: string}>;
    actions?: RawAgentAction[];
    detail_sections?: Array<{
      section_name?: string;
      rows?: Array<{
        field?: string;
        label_en?: string;
        label_zh?: string;
        value_en?: string;
        value_zh?: string;
        evidence_refs?: Array<{doc_id?: string; chunk_id?: string; evidence_text?: string}>;
      }>;
    }>;
    missing_fields?: string[];
    coverage_stats?: {
      docs_scanned?: number;
      docs_matched?: number;
      fields_filled?: number;
    };
  };
  related_docs?: Array<{
    doc_id?: string;
    file_name?: string;
    title_zh?: string;
    title_en?: string;
    summary_zh?: string;
    summary_en?: string;
    category_path?: string;
    category_label_zh?: string;
    category_label_en?: string;
    tags?: string[];
    source_available?: boolean;
    source_missing_reason?: string;
    updated_at?: string;
  }>;
  trace_id?: string;
  executor_stats?: {
    hit_count?: number;
    doc_count?: number;
    used_chunk_count?: number;
    route?: string;
    bilingual_search?: boolean;
    qdrant_used?: boolean;
    retrieval_mode?: string;
    vector_hit_count?: number;
    lexical_hit_count?: number;
    fallback_reason?: string;
    facet_mode?: string;
    facet_keys?: string[];
    context_policy?: string;
    fact_route?: string;
    fact_month?: string;
    synth_fallback_used?: boolean;
    synth_error_code?: string;
    detail_topic?: string;
    detail_mode?: string;
    detail_rows_count?: number;
    answerability?: string;
    coverage_ratio?: number;
    field_coverage_ratio?: number;
    coverage_missing_fields?: string[];
    query_required_terms?: string[];
    subject_anchor_terms?: string[];
    subject_coverage_ok?: boolean;
    infra_guard_applied?: boolean;
    route_reason?: string;
  };
}

interface RawSyncSourceSummary {
  candidate_files?: number;
  changed_files?: number;
  queued?: boolean;
  job_id?: string;
  polled_messages?: number;
  processed_messages?: number;
  downloaded_attachments?: number;
}

interface RawSyncStartResponse {
  run_id?: string;
  status?: string;
  started_at?: string;
  last_sync_at?: string | null;
  dispatch_status?: string;
  dispatch_error?: string;
  nas?: RawSyncSourceSummary;
  mail?: RawSyncSourceSummary;
}

interface RawSyncRunItem {
  item_id?: string;
  source_type?: string;
  file_name?: string;
  file_size?: number;
  stage?: string;
  doc_id?: string | null;
  updated_at?: string;
  detail?: string;
}

interface RawSyncDetailResponse {
  run_id?: string;
  status?: string;
  started_at?: string;
  finished_at?: string | null;
  summary?: Record<string, number>;
  items?: RawSyncRunItem[];
}

interface RawSyncLastResponse {
  last_sync_at?: string | null;
  last_run_status?: string;
  last_run_id?: string | null;
}

class ApiHttpError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`api_error:${status}`);
    this.name = 'ApiHttpError';
    this.status = Number(status || 0);
    this.detail = String(detail || '').trim();
  }
}

export class AgentRequestError extends Error implements AgentRequestErrorShape {
  kind: AgentRequestErrorKind;
  status?: number;
  detail?: string;
  traceId?: string;

  constructor(kind: AgentRequestErrorKind, message: string, opts?: {status?: number; detail?: string; traceId?: string}) {
    super(message);
    this.name = 'AgentRequestError';
    this.kind = kind;
    this.status = opts?.status;
    this.detail = opts?.detail;
    this.traceId = opts?.traceId;
  }
}

export function isAgentRequestError(error: unknown): error is AgentRequestError {
  return error instanceof AgentRequestError;
}

function toErrorDetail(input: unknown): string {
  if (!input || typeof input !== 'object') return '';
  const row = input as Record<string, unknown>;
  if (typeof row.detail === 'string') return String(row.detail || '').trim();
  return '';
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = '';
    try {
      const body = (await res.json()) as unknown;
      detail = toErrorDetail(body);
    } catch {
      detail = '';
    }
    throw new ApiHttpError(res.status, detail || res.statusText || '');
  }
  return (await res.json()) as T;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function mapLimit<T, R>(items: T[], limit: number, worker: (item: T, index: number) => Promise<R>): Promise<PromiseSettledResult<R>[]> {
  const safeLimit = Math.max(1, Math.floor(limit || 1));
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let cursor = 0;
  async function runOne(): Promise<void> {
    while (cursor < items.length) {
      const idx = cursor;
      cursor += 1;
      try {
        const value = await worker(items[idx], idx);
        results[idx] = {status: 'fulfilled', value};
      } catch (error) {
        results[idx] = {status: 'rejected', reason: error};
      }
    }
  }
  await Promise.all(Array.from({length: Math.min(safeLimit, items.length)}, () => runOne()));
  return results;
}

async function requestJsonWithTimeout<T>(url: string, init: RequestInit | undefined, timeoutMs: number): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), Math.max(1000, timeoutMs));
  try {
    const mergedInit: RequestInit = {
      ...(init || {}),
      signal: ctrl.signal
    };
    return await requestJson<T>(url, mergedInit);
  } catch (error) {
    if (error instanceof ApiHttpError) {
      const status = Number(error.status || 0);
      if (status === 502 || status === 503 || status === 504) {
        throw new AgentRequestError('gateway', 'gateway_error', {status, detail: error.detail});
      }
      if (status >= 500) {
        throw new AgentRequestError('server', 'server_error', {status, detail: error.detail});
      }
      throw new AgentRequestError('unknown', 'api_error', {status, detail: error.detail});
    }
    if (error && typeof error === 'object' && (error as {name?: string}).name === 'AbortError') {
      throw new AgentRequestError('timeout', 'timeout');
    }
    if (error instanceof TypeError) {
      throw new AgentRequestError('network', 'network_error');
    }
    if (error instanceof AgentRequestError) throw error;
    throw new AgentRequestError('unknown', 'unknown_error');
  } finally {
    clearTimeout(timer);
  }
}

function shouldRetryAgentError(error: unknown): boolean {
  if (!(error instanceof AgentRequestError)) return false;
  return error.kind === 'timeout' || error.kind === 'network' || error.kind === 'gateway';
}

async function requestJsonWithRetry<T>(url: string, init: RequestInit | undefined, opts?: {timeoutMs?: number; retries?: number}): Promise<T> {
  const retries = Math.max(0, Number(opts?.retries ?? 1));
  const timeoutMs = Math.max(5000, Number(opts?.timeoutMs ?? 35000));
  let lastError: unknown = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await requestJsonWithTimeout<T>(url, init, timeoutMs);
    } catch (error) {
      lastError = error;
      if (attempt >= retries || !shouldRetryAgentError(error)) break;
      await sleep(300 * (attempt + 1));
    }
  }
  throw (lastError instanceof Error ? lastError : new AgentRequestError('unknown', 'unknown_error'));
}

function safePreviewUrl(previewUrl: string, sourcePath: string): string {
  const rawPreview = String(previewUrl || '').trim();
  if (/^https?:\/\//i.test(rawPreview)) return rawPreview;
  if (rawPreview.startsWith('/api/') || rawPreview.startsWith('/preview/') || rawPreview.startsWith('/download/')) return rawPreview;

  const rawSource = String(sourcePath || '').trim();
  if (/^https?:\/\//i.test(rawSource)) return rawSource;
  return '';
}

function extFromName(fileName: string): string {
  const raw = String(fileName || '').trim();
  const idx = raw.lastIndexOf('.');
  if (idx <= 0 || idx >= raw.length - 1) return '';
  return raw.slice(idx + 1).toLowerCase();
}

function contentUrls(docId: string): {inlineUrl: string; downloadUrl: string} {
  const safeId = encodeURIComponent(String(docId || '').trim());
  return {
    inlineUrl: `${API_BASE}/v1/documents/${safeId}/content?disposition=inline`,
    downloadUrl: `${API_BASE}/v1/documents/${safeId}/content?disposition=attachment`
  };
}

function extractedText(rawChunks: RawDocItem['chunks']): string {
  const rows = Array.isArray(rawChunks) ? rawChunks : [];
  if (!rows.length) return '';
  return rows
    .map((row) => String(row?.content || '').trim())
    .filter(Boolean)
    .join('\n\n')
    .slice(0, 120_000);
}

function minimalDocFromAgentRelated(raw: NonNullable<RawAgentResponse['related_docs']>[number]): KbDoc | null {
  const id = String(raw?.doc_id || '').trim();
  if (!id) return null;
  const urls = contentUrls(id);
  return {
    id,
    fileName: String(raw?.file_name || ''),
    fileExt: extFromName(String(raw?.file_name || '')),
    sourcePath: '',
    status: 'completed',
    title: {
      zh: String(raw?.title_zh || ''),
      en: String(raw?.title_en || '')
    },
    summary: {
      zh: String(raw?.summary_zh || ''),
      en: String(raw?.summary_en || '')
    },
    categoryPath: String(raw?.category_path || ''),
    categoryLabel: {
      zh: String(raw?.category_label_zh || ''),
      en: String(raw?.category_label_en || '')
    },
    tags: Array.isArray(raw?.tags) ? raw.tags.map((item) => String(item || '').trim()).filter(Boolean) : [],
    sourceAvailable: raw?.source_available !== false,
    sourceMissingReason: String(raw?.source_missing_reason || ''),
    summaryQualityState: '',
    summaryLastError: '',
    updatedAt: String(raw?.updated_at || ''),
    previewUrl: '',
    inlineUrl: urls.inlineUrl,
    downloadUrl: urls.downloadUrl,
    extractedText: '',
    ocrPagesTotal: null,
    ocrPagesProcessed: null,
    longdocMode: null,
    longdocPagesTotal: null,
    longdocPagesUsed: null
  };
}

function toDoc(raw: RawDocItem): KbDoc {
  const sourcePath = String(raw.source_path || '');
  const docId = String(raw.doc_id || '');
  const urls = contentUrls(docId);
  const preview = safePreviewUrl(String(raw.preview_url || ''), sourcePath);
  return {
    id: docId,
    fileName: String(raw.file_name || ''),
    fileExt: String(raw.file_ext || '').trim().toLowerCase() || extFromName(String(raw.file_name || '')),
    sourcePath,
    status: String(raw.status || ''),
    title: {
      zh: String(raw.title_zh || ''),
      en: String(raw.title_en || '')
    },
    summary: {
      zh: String(raw.summary_zh || ''),
      en: String(raw.summary_en || '')
    },
    categoryPath: String(raw.category_path || 'archive/misc'),
    categoryLabel: {
      zh: String(raw.category_label_zh || ''),
      en: String(raw.category_label_en || '')
    },
    sourceAvailable: Boolean(raw.source_available ?? true),
    sourceMissingReason: String(raw.source_missing_reason || ''),
    summaryQualityState: String(raw.summary_quality_state || 'unknown'),
    summaryLastError: String(raw.summary_last_error || ''),
    tags: Array.isArray(raw.tags) ? raw.tags.map((tag) => String(tag || '').trim()).filter(Boolean) : [],
    updatedAt: String(raw.updated_at || ''),
    previewUrl: preview || urls.inlineUrl,
    inlineUrl: urls.inlineUrl,
    downloadUrl: urls.downloadUrl,
    extractedText: extractedText(raw.chunks),
    ocrPagesTotal: raw.ocr_pages_total != null ? Number(raw.ocr_pages_total) : null,
    ocrPagesProcessed: raw.ocr_pages_processed != null ? Number(raw.ocr_pages_processed) : null,
    longdocMode: raw.longdoc_mode != null ? String(raw.longdoc_mode) : null,
    longdocPagesTotal: raw.longdoc_pages_total != null ? Number(raw.longdoc_pages_total) : null,
    longdocPagesUsed: raw.longdoc_pages_used != null ? Number(raw.longdoc_pages_used) : null
  };
}

function toCategory(raw: RawCategoryItem): KbCategory {
  const path = String(raw.category_path || 'archive/misc');
  return {
    id: catIdFromPath(path),
    path,
    label: {
      zh: String(raw.label_zh || ''),
      en: String(raw.label_en || '')
    },
    icon: iconForCategory(path),
    count: Number(raw.doc_count || 0),
    colorIndex: colorIndexForCategory(path)
  };
}

function docsQueryString(params?: GetDocsParams): string {
  const qs = new URLSearchParams();
  qs.set('status', 'completed');
  qs.set('limit', String(Math.max(1, Math.min(500, Number(params?.limit ?? 200)))));
  qs.set('offset', String(Math.max(0, Number(params?.offset ?? 0))));
  if (params?.categoryPath) {
    qs.set('category_path', params.categoryPath);
  }
  if (params?.q) {
    qs.set('q', params.q);
  }
  if (params?.sourceState) {
    qs.set('source_state', params.sourceState);
  }
  if (params?.includeMissing) {
    qs.set('include_missing', 'true');
  }
  return qs.toString();
}

async function getDocsPage(params?: GetDocsParams): Promise<{items: KbDoc[]; total: number; limit: number; offset: number}> {
  const data = await requestJson<RawDocListResponse>(`${API_BASE}/v1/documents?${docsQueryString(params)}`);
  const rows = Array.isArray(data.items) ? data.items : [];
  const docs = rows.map((row) => {
    const doc = toDoc(row);
    const cached = docCacheGet(doc.id);
    if (cached && cached.summary.zh === doc.summary.zh && cached.summary.en === doc.summary.en) {
      return cached;
    }
    docCacheSet(doc);
    return doc;
  });
  return {
    items: docs,
    total: Number(data.total || 0),
    limit: Number(data.limit || params?.limit || 0),
    offset: Number(data.offset || params?.offset || 0)
  };
}

async function getDocs(params?: GetDocsParams): Promise<KbDoc[]> {
  const page = await getDocsPage(params);
  const baseDocs = page.items;

  const missingDetails = baseDocs.filter((item) => !item.summary.zh && !item.summary.en).map((item) => item.id);
  if (missingDetails.length) {
    const detailRows = await Promise.allSettled(
      missingDetails.map(async (docId) => {
        return getDoc(docId);
      })
    );
    const hydratedMap = new Map<string, KbDoc>();
    for (const row of detailRows) {
      if (row.status !== 'fulfilled' || !row.value) continue;
      hydratedMap.set(row.value.id, row.value);
    }
    return baseDocs.map((item) => hydratedMap.get(item.id) || item);
  }

  return baseDocs;
}

function docRequestKey(id: string, params?: GetDocParams): string {
  const safeId = String(id || '').trim();
  const includeChunks = Boolean(params?.includeChunks);
  const chunkLimit = Number(params?.chunkLimit || 0);
  const includeSourcePath = Boolean(params?.includeSourcePath);
  return `${safeId}|c:${includeChunks ? 1 : 0}|l:${chunkLimit}|s:${includeSourcePath ? 1 : 0}`;
}

async function getDoc(id: string, params?: GetDocParams): Promise<KbDoc | null> {
  const safeId = String(id || '').trim();
  if (!safeId) return null;
  const useCache = !params?.includeChunks && !params?.includeSourcePath;
  if (useCache) {
    const cached = docCacheGet(safeId);
    if (cached) return cached;
  }
  const key = docRequestKey(safeId, params);
  const pending = pendingDocRequests.get(key);
  if (pending) return pending;
  const promise = fetchDocFresh(safeId, params).finally(() => {
    pendingDocRequests.delete(key);
  });
  pendingDocRequests.set(key, promise);
  const fresh = await promise;
  if (fresh) return fresh;
  const cached = docCacheGet(safeId);
  return cached ? {...cached} : null;
}

async function fetchDocFresh(docId: string, params?: GetDocParams): Promise<KbDoc | null> {
  try {
    const qs = new URLSearchParams();
    if (params?.includeChunks) qs.set('include_chunks', 'true');
    if (params?.chunkLimit && Number(params.chunkLimit) > 0) qs.set('chunk_limit', String(Number(params.chunkLimit)));
    if (params?.includeSourcePath) qs.set('include_source_path', 'true');
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    const raw = await requestJson<RawDocItem>(`${API_BASE}/v1/documents/${encodeURIComponent(docId)}${suffix}`);
    const doc = toDoc(raw);
    if (!params?.includeChunks && !params?.includeSourcePath) {
      docCacheSet(doc);
    } else {
      const cached = docCacheGet(doc.id);
      if (cached) {
        const merged: KbDoc = {
          ...cached,
          ...doc,
          extractedText: doc.extractedText || cached.extractedText,
          sourcePath: doc.sourcePath || cached.sourcePath
        };
        docCacheSet(merged);
        return merged;
      }
    }
    return doc;
  } catch {
    return null;
  }
}

async function patchDoc(id: string, payload: PatchDocPayload): Promise<KbDoc | null> {
  const safeId = String(id || '').trim();
  if (!safeId) return null;

  const body: Record<string, string> = {};
  if (payload.friendlyNameZh) body.friendly_name_zh = payload.friendlyNameZh;
  if (payload.friendlyNameEn) body.friendly_name_en = payload.friendlyNameEn;
  if (!Object.keys(body).length) return getDoc(safeId);

  await requestJson(`${API_BASE}/v1/documents/${encodeURIComponent(safeId)}/friendly-name`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const updated = await fetchDocFresh(safeId);
  if (updated) docCacheSet(updated);
  return updated;
}

async function regenSummary(id: string, locale: UiLocale): Promise<RegenSummaryResult> {
  const safeId = String(id || '').trim();
  if (!safeId) {
    return {
      doc: null,
      applied: false,
      applyReason: 'invalid_doc_id',
      qualityState: 'llm_failed',
      qualityFlags: [],
      categoryRecomputed: false,
      tagsRecomputed: false,
      qdrantSynced: false,
      cascadeApplied: false,
      cascadeReason: 'invalid_doc_id'
    };
  }
  const out = await requestJson<RawRegenResponse>(`${API_BASE}/v1/summaries/map-reduce`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({doc_id: safeId, ui_lang: locale === 'zh-CN' ? 'zh' : 'en', chunk_group_size: 6})
  });
  const updated = await fetchDocFresh(safeId);
  return {
    doc: updated,
    applied: Boolean(out.applied),
    applyReason: String(out.apply_reason || ''),
    qualityState: String(out.quality_state || 'needs_regen'),
    qualityFlags: Array.isArray(out.quality_flags) ? out.quality_flags.map((item) => String(item || '').trim()).filter(Boolean) : [],
    categoryRecomputed: Boolean(out.category_recomputed),
    tagsRecomputed: Boolean(out.tags_recomputed),
    qdrantSynced: Boolean(out.qdrant_synced),
    cascadeApplied: Boolean(out.cascade_applied),
    cascadeReason: String(out.cascade_reason || '')
  };
}

async function getContentAvailability(id: string): Promise<DocumentContentAvailability> {
  const safeId = String(id || '').trim();
  if (!safeId) {
    return {sourceAvailable: false, inlineSupported: false, detail: 'document_not_found'};
  }
  try {
    const out = await requestJson<RawContentAvailability>(`${API_BASE}/v1/documents/${encodeURIComponent(safeId)}/content/availability`);
    const detail = String(out.detail || '').trim() as DocumentContentAvailabilityDetail;
    return {
      sourceAvailable: Boolean(out.source_available),
      inlineSupported: Boolean(out.inline_supported),
      detail
    };
  } catch (error) {
    if (error instanceof ApiHttpError) {
      const detail = error.detail || '';
      if (error.status === 404 && (!detail || detail === 'Not Found')) {
        return {sourceAvailable: false, inlineSupported: false, detail: 'availability_endpoint_missing'};
      }
      if (
        detail === 'source_file_missing' ||
        detail === 'document_not_ready' ||
        detail === 'unsupported_media_type' ||
        detail === 'document_not_found'
      ) {
        return {sourceAvailable: false, inlineSupported: false, detail};
      }
    }
    return {sourceAvailable: false, inlineSupported: false, detail: 'availability_unreachable'};
  }
}

async function getCategories(): Promise<KbCategory[]> {
  const data = await requestJson<{items?: RawCategoryItem[]}>(`${API_BASE}/v1/categories`);
  const rows = Array.isArray(data.items) ? data.items : [];
  return rows.map(toCategory);
}

function buildAgentAnswer(out: any, locale: UiLocale): string {
  const shortZh = String(out?.card?.short_summary?.zh || '').trim();
  const shortEn = String(out?.card?.short_summary?.en || '').trim();
  const points = Array.isArray(out?.card?.key_points) ? out.card.key_points : [];
  const pointLines = points
    .slice(0, 4)
    .map((item: any) => (locale === 'zh-CN' ? String(item?.zh || item?.en || '').trim() : String(item?.en || item?.zh || '').trim()))
    .filter(Boolean)
    .map((line: string) => `- ${line}`)
    .join('\n');

  const lead = locale === 'zh-CN' ? shortZh || shortEn : shortEn || shortZh;
  return [lead, pointLines].filter(Boolean).join('\n');
}

function toAgentActions(out: RawAgentResponse): AgentAction[] {
  const rows = Array.isArray(out?.card?.actions) ? out.card.actions : [];
  const actions: AgentAction[] = [];
  for (const item of rows.slice(0, 6)) {
    const key = String(item?.key || '').trim();
    if (!key) continue;
    actions.push({
      key,
      labelEn: String(item?.label_en || key),
      labelZh: String(item?.label_zh || key),
      actionType: String(item?.action_type || 'suggestion'),
      payload: (item?.payload && typeof item.payload === 'object' ? item.payload : {}) as Record<string, unknown>,
      requiresConfirm: Boolean(item?.requires_confirm),
      confirmTextEn: String(item?.confirm_text_en || ''),
      confirmTextZh: String(item?.confirm_text_zh || '')
    });
  }
  return actions;
}

function toSyncSourceSummary(raw: RawSyncSourceSummary | undefined): SyncSourceSummary {
  return {
    candidateFiles: Number(raw?.candidate_files || 0),
    changedFiles: Number(raw?.changed_files || 0),
    queued: Boolean(raw?.queued),
    jobId: String(raw?.job_id || ''),
    polledMessages: Number(raw?.polled_messages || 0),
    processedMessages: Number(raw?.processed_messages || 0),
    downloadedAttachments: Number(raw?.downloaded_attachments || 0)
  };
}

async function _parseAgentRaw(out: RawAgentResponse, locale: UiLocale): Promise<AgentRunResult> {
  const relatedRows = Array.isArray(out?.related_docs) ? out.related_docs : [];
  const relatedDocIds = relatedRows.map((row) => String(row?.doc_id || '').trim()).filter(Boolean);
  const sourceRows = Array.isArray(out?.card?.sources) ? out.card.sources : [];
  const docIds: string[] = Array.from(
    new Set<string>(
      [...relatedDocIds, ...sourceRows.map((row): string => String(row?.doc_id || '').trim())].filter((docId) => docId.length > 0)
    )
  ).slice(0, 4);
  const hydrated = await mapLimit(docIds, 3, (docId) => getDoc(docId));
  const docs: KbDoc[] = [];
  let partialRelatedDocs = false;
  const hydratedById = new Map<string, KbDoc>();
  for (const item of hydrated) {
    if (item.status !== 'fulfilled') {
      partialRelatedDocs = true;
      continue;
    }
    if (!item.value) {
      partialRelatedDocs = true;
      continue;
    }
    hydratedById.set(item.value.id, item.value);
    docs.push(item.value);
  }
  if (docs.length < docIds.length) {
    for (const raw of relatedRows) {
      const docId = String(raw?.doc_id || '').trim();
      if (!docId || hydratedById.has(docId)) continue;
      const fallback = minimalDocFromAgentRelated(raw);
      if (!fallback) continue;
      docs.push(fallback);
      partialRelatedDocs = true;
    }
  }
  const actions = toAgentActions(out);
  const title = String(out?.card?.title || '').trim();
  const shortSummaryZh = String(out?.card?.short_summary?.zh || '').trim();
  const shortSummaryEn = String(out?.card?.short_summary?.en || '').trim();
  const keyPoints = Array.isArray(out?.card?.key_points)
    ? out.card.key_points
        .map((item) => ({
          zh: String(item?.zh || '').trim(),
          en: String(item?.en || '').trim()
        }))
        .filter((item) => item.zh || item.en)
    : [];

  return {
    answer: buildAgentAnswer(out, locale),
    relatedDocs: docs,
    card: title || shortSummaryZh || shortSummaryEn || keyPoints.length
      ? {
          title: title || 'Knowledge Task Result',
          shortSummary: {zh: shortSummaryZh, en: shortSummaryEn},
          keyPoints,
          actions,
          detailSections: Array.isArray(out?.card?.detail_sections)
            ? out.card.detail_sections.map((section) => ({
                sectionName: String(section?.section_name || 'details'),
                rows: Array.isArray(section?.rows)
                  ? section.rows.map((row) => ({
                      field: String(row?.field || ''),
                      labelEn: String(row?.label_en || ''),
                      labelZh: String(row?.label_zh || ''),
                      valueEn: String(row?.value_en || ''),
                      valueZh: String(row?.value_zh || ''),
                      evidenceRefs: Array.isArray(row?.evidence_refs)
                        ? row.evidence_refs.map((ev) => ({
                            docId: String(ev?.doc_id || ''),
                            chunkId: String(ev?.chunk_id || ''),
                            evidenceText: String(ev?.evidence_text || '')
                          }))
                        : []
                    }))
                  : []
              }))
            : [],
          missingFields: Array.isArray(out?.card?.missing_fields)
            ? out.card.missing_fields.map((item) => String(item || '').trim()).filter(Boolean)
            : [],
          coverageStats: {
            docsScanned: Number(out?.card?.coverage_stats?.docs_scanned || 0),
            docsMatched: Number(out?.card?.coverage_stats?.docs_matched || 0),
            fieldsFilled: Number(out?.card?.coverage_stats?.fields_filled || 0)
          }
        }
      : null,
    traceId: String(out?.trace_id || ''),
    executorStats: {
      hitCount: Number(out?.executor_stats?.hit_count || 0),
      docCount: Number(out?.executor_stats?.doc_count || 0),
      usedChunkCount: Number(out?.executor_stats?.used_chunk_count || 0),
      route: String(out?.executor_stats?.route || ''),
      bilingualSearch: Boolean(out?.executor_stats?.bilingual_search),
      qdrantUsed: Boolean(out?.executor_stats?.qdrant_used),
      retrievalMode: String(out?.executor_stats?.retrieval_mode || 'none'),
      vectorHitCount: Number(out?.executor_stats?.vector_hit_count || 0),
      lexicalHitCount: Number(out?.executor_stats?.lexical_hit_count || 0),
      fallbackReason: String(out?.executor_stats?.fallback_reason || ''),
      facetMode: String(out?.executor_stats?.facet_mode || 'none'),
      facetKeys: Array.isArray(out?.executor_stats?.facet_keys)
        ? out.executor_stats.facet_keys.map((item) => String(item || '').trim()).filter(Boolean)
        : [],
      contextPolicy: String(out?.executor_stats?.context_policy || 'fresh_turn'),
      factRoute: String(out?.executor_stats?.fact_route || 'none'),
      factMonth: String(out?.executor_stats?.fact_month || ''),
      synthFallbackUsed: Boolean(out?.executor_stats?.synth_fallback_used),
      synthErrorCode: String(out?.executor_stats?.synth_error_code || ''),
      detailTopic: String(out?.executor_stats?.detail_topic || ''),
      detailMode: String(out?.executor_stats?.detail_mode || ''),
      detailRowsCount: Number(out?.executor_stats?.detail_rows_count || 0)
    },
    partialRelatedDocs
  };
}

async function runAgent(payload: AgentRunPayload): Promise<AgentRunResult> {
  const out = await requestJsonWithRetry<RawAgentResponse>(`${API_BASE}/v1/agent/execute`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      query: payload.query,
      ui_lang: payload.locale === 'zh-CN' ? 'zh' : 'en',
      query_lang: 'auto',
      conversation: Array.isArray(payload.conversation)
        ? payload.conversation
            .slice(-10)
            .map((item) => ({
              role: String(item?.role || ''),
              content: String(item?.content || '')
            }))
            .filter((item) => (item.role === 'user' || item.role === 'assistant') && item.content.trim().length > 0)
        : [],
      client_context: payload.clientContext && typeof payload.clientContext === 'object' ? payload.clientContext : {}
    })
  }, {timeoutMs: 35000, retries: 1});
  return _parseAgentRaw(out, payload.locale);
}

function _buildAgentRequestBody(payload: AgentRunPayload): string {
  return JSON.stringify({
    query: payload.query,
    ui_lang: payload.locale === 'zh-CN' ? 'zh' : 'en',
    query_lang: 'auto',
    conversation: Array.isArray(payload.conversation)
      ? payload.conversation
          .slice(-10)
          .map((item) => ({role: String(item?.role || ''), content: String(item?.content || '')}))
          .filter((item) => (item.role === 'user' || item.role === 'assistant') && item.content.trim().length > 0)
      : [],
    client_context: payload.clientContext && typeof payload.clientContext === 'object' ? payload.clientContext : {}
  });
}

async function streamAgent(
  payload: AgentRunPayload,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${API_BASE}/v1/agent/execute/stream`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: _buildAgentRequestBody(payload),
    signal
  });

  if (!res.ok || !res.body) {
    const detail = `agent_stream_failed: ${res.status}`;
    onEvent({stage: '__error__', label: {zh: '请求失败', en: 'Request failed'}, done: true, error: true, detail});
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (!data) continue;
        let evt: Record<string, unknown>;
        try {
          evt = JSON.parse(data) as Record<string, unknown>;
        } catch {
          continue;
        }
        if (evt.error) {
          onEvent({stage: '__error__', label: {zh: '请求失败', en: 'Request failed'}, done: true, error: true, detail: String(evt.detail || 'agent_stream_error')});
          return;
        }
        const stage = String(evt.stage || '');
        const label = (evt.label as AgentStreamEvent['label']) || {zh: stage, en: stage};
        if (evt.result) {
          const result = await _parseAgentRaw(evt.result as RawAgentResponse, payload.locale);
          onEvent({stage, label, done: true, result});
        } else {
          onEvent({stage, label, done: true});
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

async function getLastSync(): Promise<SyncLastResult> {
  try {
    const out = await requestJson<RawSyncLastResponse>(`${API_BASE}/v1/sync/last`);
    return {
      lastSyncAt: String(out?.last_sync_at || ''),
      lastRunStatus: String(out?.last_run_status || ''),
      lastRunId: String(out?.last_run_id || '')
    };
  } catch {
    return {lastSyncAt: '', lastRunStatus: '', lastRunId: ''};
  }
}

async function startSync(): Promise<SyncRunStartResult> {
  const out = await requestJson<RawSyncStartResponse>(`${API_BASE}/v1/sync/runs`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  });
  return {
    runId: String(out?.run_id || ''),
    status: String(out?.status || ''),
    startedAt: String(out?.started_at || ''),
    lastSyncAt: String(out?.last_sync_at || ''),
    dispatchStatus: String(out?.dispatch_status || 'queued'),
    dispatchError: String(out?.dispatch_error || ''),
    nas: toSyncSourceSummary(out?.nas),
    mail: toSyncSourceSummary(out?.mail)
  };
}

async function getMailHealth(): Promise<MailHealthResponse> {
  try {
    const out = await requestJson<{enabled?: boolean; status?: string; detail?: string}>(`${API_BASE}/v1/mail/health`);
    return {
      enabled: Boolean(out?.enabled),
      status: String(out?.status || 'unknown'),
      detail: String(out?.detail || '')
    };
  } catch {
    return {enabled: false, status: 'unreachable', detail: 'Mail health endpoint unreachable'};
  }
}

async function getSyncRun(runId: string): Promise<SyncRunDetail | null> {
  const safeId = String(runId || '').trim();
  if (!safeId) return null;
  try {
    const out = await requestJson<RawSyncDetailResponse>(`${API_BASE}/v1/sync/runs/${encodeURIComponent(safeId)}`);
    const summary = out?.summary || {};
    return {
      runId: String(out?.run_id || safeId),
      status: String(out?.status || ''),
      startedAt: String(out?.started_at || ''),
      finishedAt: String(out?.finished_at || ''),
      summary: {
        total: Number(summary.total || 0),
        discovered: Number(summary.discovered || 0),
        queued: Number(summary.queued || 0),
        pending: Number(summary.pending || 0),
        processing: Number(summary.processing || 0),
        completed: Number(summary.completed || 0),
        failed: Number(summary.failed || 0),
        duplicate: Number(summary.duplicate || 0),
        skipped: Number(summary.skipped || 0),
        activeCount: Number(summary.active_count ?? (Number(summary.discovered || 0) + Number(summary.queued || 0) + Number(summary.pending || 0) + Number(summary.processing || 0))),
        terminalCount: Number(summary.terminal_count ?? (Number(summary.completed || 0) + Number(summary.failed || 0) + Number(summary.duplicate || 0) + Number(summary.skipped || 0))),
        progressPct: Number(summary.progress_pct ?? (Number(summary.total || 0) <= 0 ? 100 : Math.round(((Number(summary.completed || 0) + Number(summary.failed || 0) + Number(summary.duplicate || 0) + Number(summary.skipped || 0)) / Number(summary.total || 1)) * 100))),
        isActive: Boolean(
          summary.is_active ??
            ((Number(summary.discovered || 0) + Number(summary.queued || 0) + Number(summary.pending || 0) + Number(summary.processing || 0)) > 0)
        )
      },
      items: (Array.isArray(out?.items) ? out.items : []).map((item) => ({
        itemId: String(item?.item_id || ''),
        sourceType: String(item?.source_type || ''),
        fileName: String(item?.file_name || ''),
        fileSize: Number(item?.file_size || 0),
        stage: String(item?.stage || ''),
        docId: String(item?.doc_id || ''),
        updatedAt: String(item?.updated_at || ''),
        detail: String(item?.detail || '')
      }))
    };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

async function getAuthStatus(): Promise<AuthStatus> {
  const r = await fetch(`${API_BASE}/v1/auth/status`);
  const data = await r.json();
  return {setup_complete: Boolean(data?.setup_complete)};
}

async function authSetup(password: string): Promise<void> {
  const r = await fetch(`${API_BASE}/v1/auth/setup`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({password}),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || 'Setup failed');
  }
}

async function authLogin(username: string, password: string): Promise<void> {
  const r = await fetch(`${API_BASE}/v1/auth/login`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password}),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || 'Login failed');
  }
}

async function authLogout(): Promise<void> {
  await fetch(`${API_BASE}/v1/auth/logout`, {method: 'POST'});
}

async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const r = await fetch(`${API_BASE}/v1/auth/password`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({old_password: oldPassword, new_password: newPassword}),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || 'Password change failed');
  }
}

async function getMe(): Promise<UserResponse | null> {
  try {
    const r = await fetch(`${API_BASE}/v1/auth/me`);
    if (!r.ok) return null;
    const raw = (await r.json()) as Record<string, unknown>;
    return {
      id: String(raw.user_id || ''),
      username: String(raw.username || ''),
      email: raw.email ? String(raw.email) : null,
      role: raw.role ? String(raw.role) : undefined,
      created_at: String(raw.created_at || ''),
    };
  } catch {
    return null;
  }
}

async function authRegister(username: string, password: string, email?: string): Promise<void> {
  const r = await fetch(`${API_BASE}/v1/auth/register`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password, email}),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || 'Registration failed');
  }
}

async function listUsers(): Promise<UserListResult> {
  const r = await fetch(`${API_BASE}/v1/auth/users`);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || 'Failed to list users');
  }
  const raw = (await r.json()) as {total?: number; items?: Array<Record<string, unknown>>};
  const items = Array.isArray(raw.items) ? raw.items : [];
  return {
    total: Number(raw.total || items.length),
    items: items.map((item) => ({
      id: String(item.user_id || ''),
      username: String(item.username || ''),
      email: item.email ? String(item.email) : null,
      role: item.role ? String(item.role) : undefined,
      created_at: String(item.created_at || ''),
    })),
  };
}

async function createUser(payload: AdminCreateUserPayload): Promise<UserResponse> {
  const r = await fetch(`${API_BASE}/v1/auth/users`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || 'Failed to create user');
  }
  const item = (await r.json()) as Record<string, unknown>;
  return {
    id: String(item.user_id || ''),
    username: String(item.username || ''),
    email: item.email ? String(item.email) : null,
    role: item.role ? String(item.role) : undefined,
    created_at: String(item.created_at || ''),
  };
}

async function deleteUser(userId: string): Promise<void> {
  const r = await fetch(`${API_BASE}/v1/auth/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || 'Failed to delete user');
  }
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

async function getSettings(): Promise<AppSettingItem[]> {
  const r = await fetch(`${API_BASE}/v1/settings`);
  if (!r.ok) return [];
  const data = await r.json();
  return Array.isArray(data?.items) ? data.items : [];
}

async function updateSettings(patch: Record<string, string>): Promise<void> {
  const r = await fetch(`${API_BASE}/v1/settings`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error('Settings update failed');
}

async function getOllamaModels(): Promise<OllamaModel[]> {
  try {
    const r = await fetch(`${API_BASE}/v1/ollama/models`);
    if (!r.ok) return [];
    const data = await r.json();
    return Array.isArray(data?.models) ? data.models : [];
  } catch {
    return [];
  }
}

async function getConnectivity(): Promise<ConnectivityStatus> {
  const r = await fetch(`${API_BASE}/v1/health/connectivity`);
  if (!r.ok) throw new Error('Connectivity check failed');
  return r.json();
}

async function getKeywords(): Promise<KeywordLists> {
  const r = await fetch(`${API_BASE}/v1/settings/keywords`);
  if (!r.ok) return {person_keywords: {}, pet_keywords: {}, location_keywords: {}};
  return r.json();
}

async function updateKeywords(patch: Partial<KeywordLists>): Promise<void> {
  const r = await fetch(`${API_BASE}/v1/settings/keywords`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error('Keywords update failed');
}

async function restartServices(): Promise<{ok: boolean; message?: string; error?: string; manual?: boolean}> {
  try {
    const r = await fetch(`${API_BASE}/v1/restart`, {
      method: 'POST',
    });
    const data = await r.json().catch(() => ({}));
    return {
      ok: Boolean(data?.ok),
      message: String(data?.message || ''),
      error: String(data?.error || ''),
      manual: Boolean(data?.manual),
    };
  } catch {
    return {ok: false, error: 'Network error', manual: true};
  }
}

async function fetchGmailWithFallback(pathSuffix = '', init?: RequestInit): Promise<Response> {
  const urls = [`${GMAIL_V1_BASE}${pathSuffix}`, `${GMAIL_LEGACY_BASE}${pathSuffix}`];
  let fallback: Response | null = null;
  for (const url of urls) {
    try {
      const response = await fetch(url, init);
      if (response.status !== 404) return response;
      fallback = response;
    } catch {
      // Try next endpoint variant.
    }
  }
  if (fallback) return fallback;
  throw new Error('Gmail credentials endpoint unreachable');
}


// ---------------------------------------------------------------------------
// Gmail Credentials
// ---------------------------------------------------------------------------

async function getGmailCredentials(): Promise<GmailCredential[]> {
  try {
    const r = await fetchGmailWithFallback();
    if (!r.ok) return [];
    const data = await r.json();
    const rows = Array.isArray(data?.items) ? data.items : [];
    return rows.map((row: any) => ({
      id: String(row?.id || ''),
      name: String(row?.name || ''),
      client_id: String(row?.client_id || row?.client_id_masked || ''),
      created_at: String(row?.created_at || ''),
      updated_at: String(row?.updated_at || ''),
    }));
  } catch {
    return [];
  }
}

async function getGmailAuthUrl(credId: string, redirectUri?: string): Promise<{auth_url: string}> {
  const params = new URLSearchParams();
  const redirectUriText = String(redirectUri || '').trim();
  if (redirectUriText) {
    params.set('redirect_uri', redirectUriText);
  }
  const suffixBase = `/${encodeURIComponent(credId)}/auth-url`;
  const suffix = params.size > 0 ? `${suffixBase}?${params.toString()}` : suffixBase;
  const r = await fetchGmailWithFallback(suffix);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || "Failed to get Gmail auth URL");
  }
  const data = await r.json().catch(() => ({}));
  const authUrl = String(data?.auth_url || '').trim();
  if (!authUrl) throw new Error('Invalid Gmail auth URL response');
  return {auth_url: authUrl};
}

async function createGmailCredential(data: GmailCredentialCreate): Promise<GmailCredential> {
  const r = await fetchGmailWithFallback('', {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || "Failed to create credential");
  }
  return await r.json();
}

async function updateGmailCredential(id: string, data: GmailCredentialUpdate): Promise<GmailCredential> {
  const suffix = `/${encodeURIComponent(id)}`;
  const urls = [`${GMAIL_V1_BASE}${suffix}`, `${GMAIL_LEGACY_BASE}${suffix}`];
  for (const url of urls) {
    for (const method of ['PATCH', 'PUT']) {
      const r = await fetch(url, {
        method,
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data),
      });
      if (r.status === 404 || r.status === 405) continue;
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err?.detail || "Failed to update credential");
      }
      return await r.json();
    }
  }
  throw new Error('Failed to update credential');
}

async function deleteGmailCredential(id: string): Promise<void> {
  const r = await fetchGmailWithFallback(`/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.detail || "Failed to delete credential");
  }
}

export function createRealAdapter(): KbApiClient {
  return {
    getDocs,
    getDocsPage,
    getDoc,
    patchDoc,
    regenSummary,
    getContentAvailability,
    getCategories,
    runAgent,
    streamAgent,
    getLastSync,
    startSync,
    getSyncRun,
    getMailHealth,
    getAuthStatus,
    authSetup,
    authLogin,
    authLogout,
    changePassword,
    getSettings,
    updateSettings,
    getOllamaModels,
    getConnectivity,
    getKeywords,
    updateKeywords,
    restartServices,
    getMe,
    authRegister,
    listUsers,
    createUser,
    deleteUser,
    getGmailCredentials,
    getGmailAuthUrl,
    createGmailCredential,
    updateGmailCredential,
    deleteGmailCredential,
  };
}
