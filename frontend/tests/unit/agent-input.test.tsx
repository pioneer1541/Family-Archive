import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {AgentView} from '@src/components/agent/AgentView';
import {OverlayProvider} from '@src/lib/ui-state/overlay';
import {TopbarProvider} from '@src/lib/ui-state/topbar';

const runAgent = vi.fn().mockResolvedValue({answer: '已处理', relatedDocs: []});
const replaceMock = vi.fn();
const mockSearch = {ask: '', autostart: ''};

vi.mock('next-intl', () => ({
  useLocale: () => 'zh-CN',
  useTranslations: () => translate
}));

function translate(key: string, values?: Record<string, string>) {
  const dict: Record<string, string> = {
    'nav.agent': 'Agent',
    'topbar.aiAssistant': 'AI 智能助手',
    'agent.welcomeTitle': '家庭资料 Agent',
    'agent.welcomeSub': '我可以帮你查找文档、解答问题、整理资料信息。',
    'agent.suggestion1': '建议1',
    'agent.suggestion2': '建议2',
    'agent.suggestion3': '建议3',
    'agent.suggestion4': '建议4',
    'agent.inputPlaceholder': '输入问题或指令…',
    'agent.inputHint': '按 Enter 发送，Shift+Enter 换行',
    'agent.relatedDocs': '相关文档',
    'agent.fallbackAnswer': `抱歉，我暂时无法处理“${values?.query || ''}”，请稍后重试。`,
    'agent.me': '我'
  };
  return dict[key] || key;
}

vi.mock('@src/lib/api/kb-client', () => ({
  getKbClient: () => ({
    runAgent
  })
}));

vi.mock('@/i18n/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: replaceMock
  })
}));

vi.mock('next/navigation', () => ({
  useSearchParams: () => ({
    get: (key: string) => {
      if (key === 'ask') return mockSearch.ask || null;
      if (key === 'autostart') return mockSearch.autostart || null;
      return null;
    }
  })
}));

describe('Agent input Enter/Shift+Enter', () => {
  beforeEach(() => {
    runAgent.mockClear();
    replaceMock.mockClear();
    mockSearch.ask = '';
    mockSearch.autostart = '';
  });

  it('uses Shift+Enter for newline and Enter for send', async () => {
    const user = userEvent.setup();

    render(
      <OverlayProvider>
        <TopbarProvider>
          <AgentView />
        </TopbarProvider>
      </OverlayProvider>
    );

    const input = screen.getByPlaceholderText('输入问题或指令…');

    await user.type(input, '测试问题');
    await user.keyboard('{Shift>}{Enter}{/Shift}');

    expect((input as HTMLTextAreaElement).value).toContain('\n');
    expect(document.querySelectorAll('.msg.user').length).toBe(0);

    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(screen.getByText('测试问题')).toBeInTheDocument();
    });
    expect(runAgent).toHaveBeenCalledTimes(1);
    const payload = runAgent.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(payload).toBeTruthy();
    expect(payload.clientContext).toEqual({context_policy: 'fresh_turn'});
    expect(payload.conversation).toEqual([]);
  });

  it('auto sends when ask/autostart query exists and then clears query', async () => {
    mockSearch.ask = '最近有哪些账单需要关注？';
    mockSearch.autostart = '1';

    render(
      <OverlayProvider>
        <TopbarProvider>
          <AgentView />
        </TopbarProvider>
      </OverlayProvider>
    );

    await waitFor(() => {
      expect(runAgent).toHaveBeenCalledTimes(1);
    });
    expect(runAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        query: '最近有哪些账单需要关注？'
      })
    );
    await waitFor(() => {
      expect(replaceMock).toHaveBeenCalledWith('/agent');
    });
  });

  it('sends recent conversation only for followup query', async () => {
    const user = userEvent.setup();

    render(
      <OverlayProvider>
        <TopbarProvider>
          <AgentView />
        </TopbarProvider>
      </OverlayProvider>
    );

    const input = screen.getByPlaceholderText('输入问题或指令…');
    await user.type(input, '先查网络账单');
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(runAgent).toHaveBeenCalledTimes(1);
    });

    await user.type(input, '继续看它的联系方式');
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(runAgent).toHaveBeenCalledTimes(2);
    });
    const payload = runAgent.mock.calls[1]?.[0] as Record<string, any>;
    expect(payload?.clientContext).toEqual({context_policy: 'followup_turn'});
    expect(Array.isArray(payload?.conversation)).toBe(true);
    expect((payload?.conversation || []).length).toBeGreaterThan(0);
  });
});
