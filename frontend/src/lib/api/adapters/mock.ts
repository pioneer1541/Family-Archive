import {catIdFromPath, colorIndexForCategory, iconForCategory} from '@src/lib/category';
import type {
  AgentRunPayload,
  AgentRunResult,
  DocumentContentAvailability,
  GetDocParams,
  GetDocsParams,
  KbApiClient,
  KbCategory,
  KbDoc,
  PatchDocPayload,
  RegenSummaryResult,
  SyncLastResult,
  SyncRunDetail,
  SyncRunStartResult,
  UiLocale
} from '../types';

interface SeedDoc {
  id: string;
  fileName: string;
  categoryPath: string;
  titleZh: string;
  titleEn: string;
  summaryZh: string;
  summaryEn: string;
  tags: string[];
}

const seedDocs: SeedDoc[] = [
  {
    id: 'd-1',
    fileName: 'water_bill_2024_12.pdf',
    categoryPath: 'finance/bills/water',
    titleZh: '2024年12月水费账单',
    titleEn: 'Water Bill Dec 2024',
    summaryZh: '2024年12月家庭自来水用量及费用汇总，本月用水15吨，应缴52.50。',
    summaryEn: 'Water consumption and charges for Dec 2024. Amount due 52.50.',
    tags: ['账单', '水费', '2024']
  },
  {
    id: 'd-2',
    fileName: 'electric_bill_2024_12.pdf',
    categoryPath: 'finance/bills/electricity',
    titleZh: '2024年12月电费账单',
    titleEn: 'Electricity Bill Dec 2024',
    summaryZh: '国家电网电费通知，用电量348度，金额289.90，截止日期2025年1月15日。',
    summaryEn: 'Electricity bill, usage 348 kWh, amount due 289.90.',
    tags: ['账单', '电费', '2024']
  },
  {
    id: 'd-7',
    fileName: 'internet_bill_2026_02.pdf',
    categoryPath: 'finance/bills/internet',
    titleZh: '2026年2月互联网账单',
    titleEn: 'Internet Bill Feb 2026',
    summaryZh: 'Superloop 宽带账单，账单周期 2026-02-08 至 2026-03-07，金额 109.00 澳币。',
    summaryEn: 'Superloop broadband invoice from 2026-02-08 to 2026-03-07. Amount due AUD 109.00.',
    tags: ['账单', '网络', '宽带', 'superloop']
  },
  {
    id: 'd-3',
    fileName: 'checkup_zhangwei_2024.pdf',
    categoryPath: 'health/reports',
    titleZh: '体检报告 - 张伟',
    titleEn: 'Health Check Report - Zhang Wei',
    summaryZh: '2024年度健康体检，血糖偏高建议复查。',
    summaryEn: 'Annual health check, elevated glucose requires follow-up.',
    tags: ['体检', '张伟', '2024']
  },
  {
    id: 'd-4',
    fileName: 'rental_contract_2024.docx',
    categoryPath: 'legal/contracts',
    titleZh: '房屋租赁合同',
    titleEn: 'Rental Contract',
    summaryZh: '租期2024-03至2025-02，月租5800。',
    summaryEn: 'Lease period from 2024-03 to 2025-02, monthly rent 5800.',
    tags: ['合同', '租房']
  },
  {
    id: 'd-5',
    fileName: 'Daikin_Warranty_Lot_41.pdf',
    categoryPath: 'home/manuals',
    titleZh: '大金空调保修说明',
    titleEn: 'Daikin Aircon Warranty Guide',
    summaryZh: '大金空调保修条款，含压缩机保修期限与售后联系方式。',
    summaryEn: 'Daikin air conditioner warranty terms with compressor coverage and service contacts.',
    tags: ['空调', '保修', '家电']
  },
  {
    id: 'd-6',
    fileName: 'Owners_Corporation_Notice_2026_Q1.pdf',
    categoryPath: 'home/property',
    titleZh: '2026年第一季度物业费通知',
    titleEn: 'Owners Corporation Fee Notice Q1 2026',
    summaryZh: '物业费与公共区域维护费用通知，包含缴费周期与截止日期。',
    summaryEn: 'Property and strata fee notice with billing period and due date.',
    tags: ['物业', '费用', '维护']
  }
];

const categoryLabelMap: Record<string, {zh: string; en: string}> = {
  'finance/bills/water': {zh: '水费账单', en: 'Water Bills'},
  'finance/bills/electricity': {zh: '电费账单', en: 'Electricity Bills'},
  'finance/bills/internet': {zh: '网络账单', en: 'Internet Bills'},
  'health/reports': {zh: '健康报告', en: 'Health Reports'},
  'legal/contracts': {zh: '合同文件', en: 'Contracts'},
  'home/manuals': {zh: '家电手册', en: 'Home Manuals'},
  'home/property': {zh: '物业资料', en: 'Property Docs'}
};

let docsStore: KbDoc[] = seedDocs.map((doc, idx) => ({
  id: doc.id,
  fileName: doc.fileName,
  fileExt: doc.fileName.split('.').pop()?.toLowerCase() || '',
  sourcePath: `/mock/${doc.fileName}`,
  status: 'completed',
  title: {zh: doc.titleZh, en: doc.titleEn},
  summary: {zh: doc.summaryZh, en: doc.summaryEn},
  categoryPath: doc.categoryPath,
  categoryLabel: categoryLabelMap[doc.categoryPath] || {zh: '未分类', en: 'Uncategorized'},
  tags: doc.tags,
  sourceAvailable: true,
  sourceMissingReason: '',
  summaryQualityState: 'ok',
  summaryLastError: '',
  updatedAt: new Date(Date.now() - idx * 3600_000).toISOString(),
  previewUrl: `/api/v1/documents/${doc.id}/content?disposition=inline`,
  inlineUrl: `/api/v1/documents/${doc.id}/content?disposition=inline`,
  downloadUrl: `/api/v1/documents/${doc.id}/content?disposition=attachment`,
  extractedText: `${doc.summaryZh}\n\n${doc.summaryEn}`,
  ocrPagesTotal: null,
  ocrPagesProcessed: null,
  longdocMode: null,
  longdocPagesTotal: null,
  longdocPagesUsed: null
}));
let mockSyncCounter = 0;

function wait<T>(value: T, ms = 120): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(value), ms);
  });
}

function localeText(zh: string, en: string, locale: UiLocale): string {
  return locale === 'zh-CN' ? zh : en;
}

function toCategories(): KbCategory[] {
  const map = new Map<string, KbCategory>();
  for (const doc of docsStore) {
    if (!map.has(doc.categoryPath)) {
      const label = categoryLabelMap[doc.categoryPath] || {zh: doc.categoryPath, en: doc.categoryPath};
      map.set(doc.categoryPath, {
        id: catIdFromPath(doc.categoryPath),
        path: doc.categoryPath,
        label,
        icon: iconForCategory(doc.categoryPath),
        colorIndex: colorIndexForCategory(doc.categoryPath),
        count: 0
      });
    }
    const found = map.get(doc.categoryPath);
    if (found) {
      found.count += 1;
    }
  }
  return Array.from(map.values()).sort((a, b) => b.count - a.count);
}

function localSearch(query: string): KbDoc[] {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return docsStore.slice(0, 4);
  const tokens = q
    .split(/[\s,，。！？!?:：;；/\\|]+/g)
    .map((item) => item.trim())
    .filter(Boolean);
  return docsStore.filter((doc) => {
    const source = [
      doc.title.zh,
      doc.title.en,
      doc.summary.zh,
      doc.summary.en,
      doc.tags.join(' '),
      doc.categoryLabel.zh,
      doc.categoryLabel.en
    ]
      .join(' ')
      .toLowerCase();
    if (source.includes(q)) return true;
    if (!tokens.length) return false;
    return tokens.some((token) => source.includes(token));
  });
}

function hasToken(text: string, tokens: string[]): boolean {
  const lowered = String(text || '').toLowerCase();
  return tokens.some((token) => lowered.includes(token));
}

export function createMockAdapter(): KbApiClient {
  return {
    async getDocs(params?: GetDocsParams): Promise<KbDoc[]> {
      const path = String(params?.categoryPath || '').trim().toLowerCase();
      let rows = path ? docsStore.filter((doc) => doc.categoryPath === path) : docsStore;
      const q = String(params?.q || '').trim().toLowerCase();
      if (q) {
        rows = rows.filter((doc) =>
          [doc.fileName, doc.title.zh, doc.title.en, doc.summary.zh, doc.summary.en, doc.categoryPath, doc.tags.join(' ')]
            .join(' ')
            .toLowerCase()
            .includes(q)
        );
      }
      const offset = Math.max(0, Number(params?.offset || 0));
      const limit = Math.max(1, Number(params?.limit || rows.length || 1));
      return wait(rows.slice(offset, offset + limit).map((item) => ({...item})));
    },

    async getDocsPage(params?: GetDocsParams) {
      const path = String(params?.categoryPath || '').trim().toLowerCase();
      let rows = path ? docsStore.filter((doc) => doc.categoryPath === path) : docsStore;
      const q = String(params?.q || '').trim().toLowerCase();
      if (q) {
        rows = rows.filter((doc) =>
          [doc.fileName, doc.title.zh, doc.title.en, doc.summary.zh, doc.summary.en, doc.categoryPath, doc.tags.join(' ')]
            .join(' ')
            .toLowerCase()
            .includes(q)
        );
      }
      const total = rows.length;
      const offset = Math.max(0, Number(params?.offset || 0));
      const limit = Math.max(1, Number(params?.limit || 50));
      const items = rows.slice(offset, offset + limit).map((item) => ({...item}));
      return wait({items, total, limit, offset});
    },

    async getDoc(id: string, _params?: GetDocParams): Promise<KbDoc | null> {
      const found = docsStore.find((doc) => doc.id === String(id || ''));
      return wait(found ? {...found} : null);
    },

    async patchDoc(id: string, payload: PatchDocPayload): Promise<KbDoc | null> {
      const idx = docsStore.findIndex((doc) => doc.id === String(id || ''));
      if (idx < 0) return wait(null);
      const current = docsStore[idx];
      docsStore[idx] = {
        ...current,
        title: {
          zh: String(payload.friendlyNameZh || current.title.zh),
          en: String(payload.friendlyNameEn || current.title.en)
        },
        updatedAt: new Date().toISOString()
      };
      return wait({...docsStore[idx]});
    },

    async regenSummary(id: string, locale: UiLocale): Promise<RegenSummaryResult> {
      const idx = docsStore.findIndex((doc) => doc.id === String(id || ''));
      if (idx < 0) {
        return wait(
          {
            doc: null,
            applied: false,
            applyReason: 'document_not_found',
            qualityState: 'llm_failed',
            qualityFlags: ['document_not_found'],
            categoryRecomputed: false,
            tagsRecomputed: false,
            qdrantSynced: false,
            cascadeApplied: false,
            cascadeReason: 'document_not_found'
          },
          400
        );
      }
      const current = docsStore[idx];
      docsStore[idx] = {
        ...current,
        summary: {
          zh: `${current.summary.zh}（已重新生成）`,
          en: `${current.summary.en} (Regenerated)`
        },
        summaryQualityState: 'ok',
        summaryLastError: '',
        updatedAt: new Date().toISOString()
      };
      return wait(
        {
          doc: {...docsStore[idx]},
          applied: true,
          applyReason: 'ok',
          qualityState: 'ok',
          qualityFlags: [],
          categoryRecomputed: true,
          tagsRecomputed: true,
          qdrantSynced: true,
          cascadeApplied: true,
          cascadeReason: 'ok'
        },
        locale === 'zh-CN' ? 900 : 700
      );
    },

    async getContentAvailability(id: string): Promise<DocumentContentAvailability> {
      const found = docsStore.find((doc) => doc.id === String(id || '').trim());
      if (!found) {
        return wait({sourceAvailable: false, inlineSupported: false, detail: 'document_not_found'});
      }
      const ext = String(found.fileExt || '').toLowerCase();
      const inlineSupported = ['pdf', 'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff', 'heic'].includes(ext);
      return wait({
        sourceAvailable: Boolean(found.sourceAvailable),
        inlineSupported,
        detail: found.sourceAvailable ? (inlineSupported ? 'ok' : 'unsupported_media_type') : 'source_file_missing'
      });
    },

    async getCategories(): Promise<KbCategory[]> {
      return wait(toCategories());
    },

    async runAgent(payload: AgentRunPayload): Promise<AgentRunResult> {
      const query = String(payload.query || '').trim();
      const lowered = query.toLowerCase();
      const networkTokens = ['网络', '互联网', '宽带', 'nbn', 'broadband', 'internet', 'superloop'];
      const propertyTokens = ['物业', 'property', 'strata', 'owners corporation', 'body corporate'];
      const contactTokens = ['联系方式', 'contact', 'phone', '电话', 'email', '邮箱', 'manager', '负责人'];

      let matched = localSearch(query).slice(0, 6);
      let facetMode = 'none';
      let facetKeys: string[] = [];
      let fallbackReason = '';

      if (hasToken(lowered, networkTokens)) {
        facetMode = 'strict_topic';
        facetKeys = ['network_bill'];
        matched = matched.filter((doc) => doc.categoryPath === 'finance/bills/internet');
      } else if (hasToken(lowered, propertyTokens) && hasToken(lowered, contactTokens)) {
        facetMode = 'strict_topic';
        facetKeys = ['property_contact'];
        const allowed = new Set(['home/maintenance', 'home/property', 'legal/property', 'finance/bills/other']);
        matched = matched.filter((doc) => allowed.has(doc.categoryPath));
      }

      matched = matched.slice(0, 4);
      if (facetMode === 'strict_topic' && !matched.length) {
        fallbackReason = 'strict_filter_zero_hit';
      }

      const bills = matched.filter((doc) => doc.categoryPath.startsWith('finance/bills'));
      const answer = matched.length
        ? localeText(
            bills.length
              ? [
                  '根据资料库记录，最近有以下账单需要关注：',
                  '',
                  '需要缴费的账单：',
                  ...bills.slice(0, 2).map((doc) => `- ${doc.title.zh}：金额信息见文档摘要。`),
                  '',
                  '已缴费的账单：',
                  ...bills.slice(2, 4).map((doc) => `- ${doc.title.zh}`)
                ].join('\n')
              : `根据你的问题“${payload.query}”，我找到 ${matched.length} 份相关文档，已附在下方。`,
            bills.length
              ? [
                  'Recent bills from your library:',
                  '',
                  'Bills to pay:',
                  ...bills.slice(0, 2).map((doc) => `- ${doc.title.en}`),
                  '',
                  'Paid bills:',
                  ...bills.slice(2, 4).map((doc) => `- ${doc.title.en}`)
                ].join('\n')
              : `For “${payload.query}”, I found ${matched.length} related documents below.`,
            payload.locale
          )
        : localeText(
            `我暂时没找到与“${payload.query}”直接匹配的文档，建议尝试更具体关键词。`,
            `No direct matches for “${payload.query}”. Try a more specific keyword.`,
            payload.locale
          );
      return wait({
        answer,
        relatedDocs: matched,
        card: {
          title: payload.locale === 'zh-CN' ? '任务结果' : 'Task Result',
          shortSummary: {
            zh: answer,
            en: answer
          },
          keyPoints: [],
          detailSections: [],
          missingFields: [],
          coverageStats: {
            docsScanned: matched.length,
            docsMatched: matched.length,
            fieldsFilled: 0
          },
          actions: [
            {
              key: 'open_docs',
              labelEn: 'Open Docs',
              labelZh: '打开文档',
              actionType: 'navigate',
              payload: {target: 'docs'},
              requiresConfirm: false,
              confirmTextEn: '',
              confirmTextZh: ''
            }
          ]
        },
        traceId: `mock-${Date.now()}`,
        executorStats: {
          hitCount: matched.length,
          docCount: matched.length,
          usedChunkCount: matched.length,
          route: 'mock',
          bilingualSearch: false,
          qdrantUsed: false,
          retrievalMode: facetMode === 'strict_topic' ? 'hybrid' : 'structured',
          vectorHitCount: 0,
          lexicalHitCount: 0,
          fallbackReason,
          facetMode,
          facetKeys,
          contextPolicy: 'fresh_turn',
          factRoute: 'none',
          factMonth: '',
          synthFallbackUsed: false,
          synthErrorCode: '',
          detailTopic: 'generic',
          detailMode: 'structured',
          detailRowsCount: 0
        }
      });
    },

    async getLastSync(): Promise<SyncLastResult> {
      return wait({
        lastSyncAt: '',
        lastRunStatus: '',
        lastRunId: ''
      });
    },

    async startSync(): Promise<SyncRunStartResult> {
      mockSyncCounter += 1;
      const now = new Date().toISOString();
      return wait({
        runId: `mock-sync-${mockSyncCounter}`,
        status: 'running',
        startedAt: now,
        lastSyncAt: '',
        dispatchStatus: 'queued',
        dispatchError: '',
        nas: {
          candidateFiles: 2,
          changedFiles: 1,
          queued: true,
          jobId: `mock-nas-${mockSyncCounter}`,
          polledMessages: 0,
          processedMessages: 0,
          downloadedAttachments: 0
        },
        mail: {
          candidateFiles: 0,
          changedFiles: 0,
          queued: true,
          jobId: `mock-mail-${mockSyncCounter}`,
          polledMessages: 2,
          processedMessages: 2,
          downloadedAttachments: 1
        }
      });
    },

    async getSyncRun(runId: string): Promise<SyncRunDetail | null> {
      if (!String(runId || '').trim()) return wait(null);
      return wait({
        runId: String(runId),
        status: 'completed',
        startedAt: new Date(Date.now() - 45_000).toISOString(),
        finishedAt: new Date().toISOString(),
        summary: {
          total: 3,
          discovered: 0,
          queued: 0,
          pending: 0,
          processing: 0,
          completed: 2,
          failed: 0,
          duplicate: 1,
          skipped: 0,
          activeCount: 0,
          terminalCount: 3,
          progressPct: 100,
          isActive: false
        },
        items: [
          {
            itemId: 'mock-item-1',
            sourceType: 'nas',
            fileName: 'Owners Handover Guide.pdf',
            fileSize: 2_340_018,
            stage: 'completed',
            docId: 'd-1',
            updatedAt: new Date(Date.now() - 20_000).toISOString(),
            detail: 'ok'
          },
          {
            itemId: 'mock-item-2',
            sourceType: 'mail',
            fileName: 'internet_bill_2026_02.pdf',
            fileSize: 923_442,
            stage: 'completed',
            docId: 'd-7',
            updatedAt: new Date(Date.now() - 15_000).toISOString(),
            detail: 'ok'
          },
          {
            itemId: 'mock-item-3',
            sourceType: 'nas',
            fileName: 'duplicate_warranty.pdf',
            fileSize: 128_120,
            stage: 'duplicate',
            docId: '',
            updatedAt: new Date(Date.now() - 12_000).toISOString(),
            detail: 'sha256_duplicate'
          }
        ]
      });
    }
  };
}
