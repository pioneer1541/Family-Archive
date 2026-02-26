import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi} from 'vitest';
import {DocsView} from '@src/components/docs/DocsView';
import {OverlayProvider} from '@src/lib/ui-state/overlay';
import {TopbarProvider} from '@src/lib/ui-state/topbar';

const docs = [
  {
    id: 'doc-1',
    fileName: 'water_bill.pdf',
    fileExt: 'pdf',
    sourcePath: '',
    status: 'completed',
    title: {zh: '2024年12月水费账单', en: 'Water Bill'},
    summary: {zh: '本月用水费用明细。', en: 'Water usage summary.'},
    categoryPath: 'finance/bills/water',
    categoryLabel: {zh: '水费账单', en: 'Water Bills'},
    tags: ['vendor:agl'],
    sourceAvailable: true,
    sourceMissingReason: '',
    summaryQualityState: 'ok',
    summaryLastError: '',
    updatedAt: '2026-02-20T00:00:00Z',
    previewUrl: '',
    inlineUrl: '/api/v1/documents/doc-1/content?disposition=inline',
    downloadUrl: '/api/v1/documents/doc-1/content?disposition=attachment',
    extractedText: 'water text'
  },
  {
    id: 'doc-2',
    fileName: 'electric_bill.pdf',
    fileExt: 'pdf',
    sourcePath: '',
    status: 'completed',
    title: {zh: '2024年12月电费账单', en: 'Electricity Bill'},
    summary: {zh: '本月电费金额。', en: 'Electricity amount due.'},
    categoryPath: 'finance/bills/electricity',
    categoryLabel: {zh: '电费账单', en: 'Electricity Bills'},
    tags: ['vendor:agl', 'status:important'],
    sourceAvailable: true,
    sourceMissingReason: '',
    summaryQualityState: 'ok',
    summaryLastError: '',
    updatedAt: '2026-02-19T00:00:00Z',
    previewUrl: '',
    inlineUrl: '/api/v1/documents/doc-2/content?disposition=inline',
    downloadUrl: '/api/v1/documents/doc-2/content?disposition=attachment',
    extractedText: 'electric text'
  }
];

vi.mock('next-intl', () => ({
  useLocale: () => 'zh-CN',
  useTranslations: () => translate
}));

function translate(key: string) {
  const dict: Record<string, string> = {
    'nav.docs': '文档',
    'docs.searchPlaceholder': '搜索文档名称、摘要、标签…',
    'docs.searchButton': '搜索',
    'docs.empty': '未找到相关文档',
    'docs.loading': '加载中',
    'docs.loadMore': '加载更多',
    'docs.loadingMore': '加载中',
    'docs.count': '{shown}/{total}'
  };
  return dict[key] || key;
}

vi.mock('@src/lib/api/kb-client', () => ({
  getKbClient: () => ({
    getDocs: vi.fn().mockResolvedValue(docs),
    getDocsPage: vi.fn().mockImplementation((params?: {q?: string; limit?: number; offset?: number}) => {
      const q = String(params?.q || '').toLowerCase().trim();
      let rows = docs;
      if (q) {
        rows = docs.filter((doc) =>
          [
            doc.fileName,
            doc.title.zh,
            doc.title.en,
            doc.summary.zh,
            doc.summary.en,
            doc.categoryPath,
            doc.tags.join(' ')
          ]
            .join(' ')
            .toLowerCase()
            .includes(q)
        );
      }
      const limit = Number(params?.limit || 50);
      const offset = Number(params?.offset || 0);
      return Promise.resolve({
        items: rows.slice(offset, offset + limit),
        total: rows.length,
        limit,
        offset
      });
    })
  })
}));

describe('Docs search filter', () => {
  it('filters docs by title/summary/tags/category on input', async () => {
    const user = userEvent.setup();
    render(
      <OverlayProvider>
        <TopbarProvider>
          <DocsView />
        </TopbarProvider>
      </OverlayProvider>
    );

    await waitFor(() => {
      expect(screen.getByText(/2024年12月水费账单/)).toBeInTheDocument();
      expect(screen.getByText(/2024年12月电费账单/)).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText('搜索文档名称、摘要、标签…');
    await user.clear(input);
    await user.type(input, 'electricity');

    await waitFor(() => {
      expect(screen.getByText(/2024年12月电费账单/)).toBeInTheDocument();
      expect(screen.queryByText(/2024年12月水费账单/)).not.toBeInTheDocument();
    });
  });
});
