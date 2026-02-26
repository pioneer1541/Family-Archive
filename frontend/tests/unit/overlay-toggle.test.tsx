import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {Toast} from '@src/components/common/Toast';
import {DetailOverlay} from '@src/components/overlay/DetailOverlay';
import {DocumentContentOverlay} from '@src/components/overlay/DocumentContentOverlay';
import {ContentViewerProvider, useContentViewer} from '@src/lib/ui-state/content-viewer';
import {OverlayProvider, useOverlay} from '@src/lib/ui-state/overlay';
import {ToastProvider} from '@src/lib/ui-state/toast';

const baseDoc = {
  id: 'doc-1',
  fileName: 'demo.pdf',
  fileExt: 'pdf',
  sourcePath: '',
  status: 'completed',
  title: {zh: '示例文档', en: 'Demo Document'},
  summary: {zh: '示例摘要', en: 'Demo summary'},
  categoryPath: 'archive/misc',
  categoryLabel: {zh: '归档', en: 'Archive'},
  tags: ['status:review'],
  sourceAvailable: true,
  sourceMissingReason: '',
  summaryQualityState: 'ok',
  summaryLastError: '',
  updatedAt: '2026-02-20T00:00:00Z',
  previewUrl: '/api/v1/documents/doc-1/content?disposition=inline',
  inlineUrl: '/api/v1/documents/doc-1/content?disposition=inline',
  downloadUrl: '/api/v1/documents/doc-1/content?disposition=attachment',
  extractedText: '这是提取文本。'
};

const getDocMock = vi.fn().mockResolvedValue(baseDoc);
const patchDocMock = vi.fn();
const regenSummaryMock = vi.fn().mockResolvedValue({
  doc: null,
  applied: false,
  applyReason: 'quality_not_ok',
  qualityState: 'needs_regen',
  qualityFlags: [],
  categoryRecomputed: false,
  tagsRecomputed: false,
  qdrantSynced: false,
  cascadeApplied: false,
  cascadeReason: 'summary_not_ok'
});
const getContentAvailabilityMock = vi.fn().mockResolvedValue({
  sourceAvailable: true,
  inlineSupported: true,
  detail: 'ok'
});

vi.mock('next-intl', () => ({
  useLocale: () => 'zh-CN',
  useTranslations: () => (key: string) => {
    const dict: Record<string, string> = {
      'overlay.friendlyName': '友好名称',
      'overlay.langTabs': '语言切换',
      'overlay.friendlyInputPlaceholder': '输入友好名称…',
      'overlay.save': '保存',
      'overlay.category': '分类',
      'overlay.summary': '摘要',
      'overlay.regen': '重新生成',
      'overlay.loading': '加载中…',
      'overlay.tags': '标签',
      'overlay.viewDoc': '浏览文档内容',
      'overlay.toastFriendlySaved': '友好名称已更新',
      'overlay.toastSummaryRegenerated': '摘要已重新生成',
      'overlay.toastSummaryCascadeSuccess': '摘要、分类与标签已更新',
      'overlay.toastSummaryCascadePartial': '摘要已更新，但联动更新不完整',
      'overlay.toastSummaryRegenFailed': '摘要重生成失败',
      'overlay.toastSummaryRegenFailedKeep': '摘要重生成失败，分类与标签保持不变',
      'overlay.qualityState': '质量状态',
      'overlay.qualityOk': '正常',
      'overlay.qualityNeedsRegen': '需重试',
      'overlay.qualityLlmFailed': '模型失败',
      'overlay.qualityUnknown': '未知',
      'overlay.lastError': '错误详情',
      'overlay.regenFailLlm': '模型调用失败',
      'overlay.regenFailQuality': '摘要质量未达标',
      'overlay.regenFailNotFound': '文档不存在',
      'overlay.regenFailUnknown': '未知失败',
      'overlay.cascadeReasonOk': '联动更新完成',
      'overlay.cascadeReasonSummaryNotOk': '摘要质量未通过',
      'overlay.cascadeReasonCategory': '分类未更新',
      'overlay.cascadeReasonTags': '标签未更新',
      'overlay.cascadeReasonQdrant': '向量同步失败',
      'overlay.cascadeReasonNotFound': '文档不存在',
      'overlay.cascadeReasonUnknown': '联动原因未知',
      'contentOverlay.download': '下载原文',
      'contentOverlay.loading': '正在加载文档内容…',
      'contentOverlay.fallbackTitle': '提取文本',
      'contentOverlay.reasonSourceMissing': '源文件缺失，已切换为提取文本浏览。',
      'contentOverlay.reasonNotReady': '文档尚未完成处理，暂无法预览原文。',
      'contentOverlay.reasonUnsupported': '当前格式不支持内联预览，已切换为提取文本。',
      'contentOverlay.reasonAvailabilityEndpointMissing': '预览探测接口暂不可用，当前已切换为提取文本浏览。',
      'contentOverlay.reasonAvailabilityUnavailable': '预览状态探测失败，当前已切换为提取文本浏览。',
      'contentOverlay.reasonGeneric': '当前无法内联预览，已切换为提取文本。',
      'contentOverlay.noExtractedText': '暂无可显示的提取文本，请使用下载按钮查看原文件。',
      'contentOverlay.iframeTitle': '文档内容'
    };
    return dict[key] || key;
  }
}));

vi.mock('@src/lib/api/kb-client', () => ({
  getKbClient: () => ({
    getDoc: getDocMock,
    patchDoc: patchDocMock,
    regenSummary: regenSummaryMock,
    getContentAvailability: getContentAvailabilityMock
  })
}));

beforeEach(() => {
  getDocMock.mockReset();
  getDocMock.mockResolvedValue(baseDoc);

  patchDocMock.mockReset();

  regenSummaryMock.mockReset();
  regenSummaryMock.mockResolvedValue({
    doc: null,
    applied: false,
    applyReason: 'quality_not_ok',
    qualityState: 'needs_regen',
    qualityFlags: [],
    categoryRecomputed: false,
    tagsRecomputed: false,
    qdrantSynced: false,
    cascadeApplied: false,
    cascadeReason: 'summary_not_ok'
  });

  getContentAvailabilityMock.mockReset();
  getContentAvailabilityMock.mockResolvedValue({
    sourceAvailable: true,
    inlineSupported: true,
    detail: 'ok'
  });
});

function Trigger() {
  const {openOverlay} = useOverlay();
  return (
    <button type="button" onClick={() => openOverlay('doc-1')}>
      open
    </button>
  );
}

function ContentTrigger() {
  const {openViewer} = useContentViewer();
  return (
    <button type="button" onClick={() => openViewer('doc-1')}>
      open-content
    </button>
  );
}

describe('DetailOverlay open/close', () => {
  it('opens and closes by backdrop click', async () => {
    render(
      <ToastProvider>
        <OverlayProvider>
          <ContentViewerProvider>
            <Trigger />
            <DetailOverlay />
            <DocumentContentOverlay />
            <Toast />
          </ContentViewerProvider>
        </OverlayProvider>
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('open'));

    await waitFor(() => {
      expect(document.getElementById('detail-overlay')).toHaveClass('open');
    });

    const overlay = document.getElementById('detail-overlay');
    expect(overlay).toBeInTheDocument();
    fireEvent.click(overlay as HTMLElement);

    await waitFor(() => {
      expect(document.getElementById('detail-overlay')).not.toHaveClass('open');
    });
  });

  it('opens content overlay when click view doc button', async () => {
    render(
      <ToastProvider>
        <OverlayProvider>
          <ContentViewerProvider>
            <Trigger />
            <DetailOverlay />
            <DocumentContentOverlay />
            <Toast />
          </ContentViewerProvider>
        </OverlayProvider>
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('open'));

    await waitFor(() => {
      expect(document.getElementById('detail-overlay')).toHaveClass('open');
    });

    fireEvent.click(screen.getByText(/浏览文档内容/));

    await waitFor(() => {
      expect(document.getElementById('content-overlay')).toHaveClass('open');
    });
  });

  it('shows failure toast when regen is not applied', async () => {
    render(
      <ToastProvider>
        <OverlayProvider>
          <ContentViewerProvider>
            <Trigger />
            <DetailOverlay />
            <DocumentContentOverlay />
            <Toast />
          </ContentViewerProvider>
        </OverlayProvider>
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('open'));
    await waitFor(() => {
      expect(document.getElementById('detail-overlay')).toHaveClass('open');
    });

    fireEvent.click(screen.getByText('重新生成'));
    await waitFor(() => {
      expect(screen.getByText(/摘要重生成失败，分类与标签保持不变/)).toBeInTheDocument();
    });
  });

  it('shows success toast when regen is applied', async () => {
    regenSummaryMock.mockResolvedValueOnce({
      doc: {
        ...baseDoc,
        summary: {zh: '更新后的摘要', en: 'Updated summary'},
        summaryQualityState: 'ok',
        summaryLastError: ''
      },
      applied: true,
      applyReason: 'ok',
      qualityState: 'ok',
      qualityFlags: [],
      categoryRecomputed: true,
      tagsRecomputed: true,
      qdrantSynced: true,
      cascadeApplied: true,
      cascadeReason: 'ok'
    });

    render(
      <ToastProvider>
        <OverlayProvider>
          <ContentViewerProvider>
            <Trigger />
            <DetailOverlay />
            <DocumentContentOverlay />
            <Toast />
          </ContentViewerProvider>
        </OverlayProvider>
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('open'));
    await waitFor(() => {
      expect(document.getElementById('detail-overlay')).toHaveClass('open');
    });

    fireEvent.click(screen.getByText('重新生成'));
    await waitFor(() => {
      expect(screen.getByText(/摘要、分类与标签已更新/)).toBeInTheDocument();
    });
  });

  it('shows source missing fallback only when detail is source_file_missing', async () => {
    getContentAvailabilityMock.mockResolvedValueOnce({
      sourceAvailable: false,
      inlineSupported: false,
      detail: 'source_file_missing'
    });

    render(
      <ToastProvider>
        <OverlayProvider>
          <ContentViewerProvider>
            <Trigger />
            <DetailOverlay />
            <DocumentContentOverlay />
            <Toast />
          </ContentViewerProvider>
        </OverlayProvider>
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('open'));
    await waitFor(() => {
      expect(document.getElementById('detail-overlay')).toHaveClass('open');
    });
    fireEvent.click(screen.getByText(/浏览文档内容/));

    await waitFor(() => {
      expect(screen.getByText(/源文件缺失/)).toBeInTheDocument();
    });
    expect(document.querySelector('.content-frame')).not.toBeInTheDocument();
  });

  it('falls back to iframe when availability probe endpoint is missing', async () => {
    getContentAvailabilityMock.mockResolvedValueOnce({
      sourceAvailable: false,
      inlineSupported: false,
      detail: 'availability_endpoint_missing'
    });

    render(
      <ToastProvider>
        <OverlayProvider>
          <ContentViewerProvider>
            <Trigger />
            <DetailOverlay />
            <DocumentContentOverlay />
            <Toast />
          </ContentViewerProvider>
        </OverlayProvider>
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('open'));
    await waitFor(() => {
      expect(document.getElementById('detail-overlay')).toHaveClass('open');
    });
    fireEvent.click(screen.getByText(/浏览文档内容/));

    await waitFor(() => {
      expect(document.querySelector('.content-frame')).toBeInTheDocument();
    });
    expect(screen.queryByText(/源文件缺失/)).not.toBeInTheDocument();
  });

  it('does not show source-missing when doc detail request fails but availability is known', async () => {
    getDocMock.mockRejectedValueOnce(new Error('detail_request_failed'));
    getContentAvailabilityMock.mockResolvedValueOnce({
      sourceAvailable: true,
      inlineSupported: false,
      detail: 'unsupported_media_type'
    });

    render(
      <ToastProvider>
        <OverlayProvider>
          <ContentViewerProvider>
            <ContentTrigger />
            <DocumentContentOverlay />
            <Toast />
          </ContentViewerProvider>
        </OverlayProvider>
      </ToastProvider>
    );

    fireEvent.click(screen.getByText('open-content'));
    await waitFor(() => {
      expect(document.getElementById('content-overlay')).toHaveClass('open');
    });
    await waitFor(() => {
      expect(screen.getByText(/当前格式不支持内联预览/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/源文件缺失/)).not.toBeInTheDocument();
  });
});
