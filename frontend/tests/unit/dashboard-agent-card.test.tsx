import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {DashboardView} from '@src/components/dashboard/DashboardView';
import {OverlayProvider} from '@src/lib/ui-state/overlay';
import {SyncViewerProvider} from '@src/lib/ui-state/sync-viewer';
import {TopbarProvider} from '@src/lib/ui-state/topbar';
import {ToastProvider} from '@src/lib/ui-state/toast';

const pushMock = vi.fn();

const docs = [
  {
    id: 'doc-1',
    fileName: 'internet_bill.pdf',
    fileExt: 'pdf',
    sourcePath: '',
    status: 'completed',
    title: {zh: '2026年2月互联网账单', en: 'Internet Bill Feb 2026'},
    summary: {zh: '宽带费用汇总', en: 'Broadband bill summary'},
    categoryPath: 'finance/bills/internet',
    categoryLabel: {zh: '网络账单', en: 'Internet Bills'},
    tags: ['账单'],
    sourceAvailable: true,
    sourceMissingReason: '',
    summaryQualityState: 'ok',
    summaryLastError: '',
    updatedAt: '2026-02-20T00:00:00Z',
    previewUrl: '',
    inlineUrl: '',
    downloadUrl: '',
    extractedText: ''
  }
];

const categories = [
  {
    id: 'finance__bills__internet',
    path: 'finance/bills/internet',
    label: {zh: '网络账单', en: 'Internet Bills'},
    icon: '🧾',
    count: 6,
    colorIndex: 0
  },
  {
    id: 'home__property',
    path: 'home/property',
    label: {zh: '物业资料', en: 'Property Docs'},
    icon: '🏠',
    count: 2,
    colorIndex: 1
  }
];

function translate(key: string): string {
  const dict: Record<string, string> = {
    'nav.dashboard': '总览',
    'dashboard.recent': '最近添加',
    'dashboard.stats': '文档统计',
    'dashboard.docsUnit': '份',
    'dashboard.agentCardTitle': '家庭资料 Agent',
    'dashboard.agentCardSub': '向 AI 提问，快速查找和分析您的家庭文档',
    'dashboard.agentInputPlaceholder': '例如：我们家有哪些保险？',
    'dashboard.agentAskBtn': '提问 →',
    'dashboard.quickRecentBills': '最近账单',
    'dashboard.quickInsurance': '家庭保险',
    'dashboard.quickHealth': '健康档案',
    'dashboard.quickContractExpiry': '合同到期'
  };
  return dict[key] || key;
}

vi.mock('next-intl', () => ({
  useLocale: () => 'zh-CN',
  useTranslations: () => translate
}));

vi.mock('@/i18n/navigation', () => ({
  useRouter: () => ({
    push: pushMock
  })
}));

vi.mock('@src/lib/api/kb-client', () => ({
  getKbClient: () => ({
    getDocs: vi.fn().mockResolvedValue(docs),
    getCategories: vi.fn().mockResolvedValue(categories),
    getLastSync: vi.fn().mockResolvedValue({lastSyncAt: '', lastRunStatus: '', lastRunId: ''}),
    startSync: vi.fn().mockResolvedValue({runId: 'sync-1', status: 'running', startedAt: '', lastSyncAt: '', nas: {}, mail: {}}),
    getSyncRun: vi.fn().mockResolvedValue(null)
  })
}));

describe('Dashboard Agent card and clickable category bars', () => {
  beforeEach(() => {
    pushMock.mockClear();
  });

  it('jumps to agent with ask query when press Enter on dashboard input', async () => {
    render(
      <OverlayProvider>
        <SyncViewerProvider>
          <TopbarProvider>
            <ToastProvider>
              <DashboardView />
            </ToastProvider>
          </TopbarProvider>
        </SyncViewerProvider>
      </OverlayProvider>
    );
    await waitFor(() => {
      expect(screen.getByRole('button', {name: /网络账单/})).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText('例如：我们家有哪些保险？');
    fireEvent.change(input, {target: {value: '最近有哪些账单需要关注？'}});
    fireEvent.keyDown(input, {key: 'Enter'});

    expect(pushMock).toHaveBeenCalledTimes(1);
    const path = String(pushMock.mock.calls[0]?.[0] || '');
    expect(path.startsWith('/agent?')).toBe(true);
    const query = new URLSearchParams(path.split('?')[1] || '');
    expect(query.get('ask')).toBe('最近有哪些账单需要关注？');
    expect(query.get('autostart')).toBe('1');
    expect(query.get('src')).toBe('dashboard');
  });

  it('jumps to agent with quick prompt button', async () => {
    render(
      <OverlayProvider>
        <SyncViewerProvider>
          <TopbarProvider>
            <ToastProvider>
              <DashboardView />
            </ToastProvider>
          </TopbarProvider>
        </SyncViewerProvider>
      </OverlayProvider>
    );
    await waitFor(() => {
      expect(screen.getByRole('button', {name: /网络账单/})).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', {name: '最近账单'}));

    expect(pushMock).toHaveBeenCalledTimes(1);
    const path = String(pushMock.mock.calls[0]?.[0] || '');
    const query = new URLSearchParams(path.split('?')[1] || '');
    expect(query.get('ask')).toBe('最近账单');
    expect(query.get('autostart')).toBe('1');
  });

  it('opens category detail route when clicking stat bar', async () => {
    render(
      <OverlayProvider>
        <SyncViewerProvider>
          <TopbarProvider>
            <ToastProvider>
              <DashboardView />
            </ToastProvider>
          </TopbarProvider>
        </SyncViewerProvider>
      </OverlayProvider>
    );

    await waitFor(() => {
      expect(screen.getByRole('button', {name: /网络账单/})).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', {name: /网络账单/}));

    expect(pushMock).toHaveBeenCalledWith('/cats/finance__bills__internet');
  });
});
