import {useEffect} from 'react';
import {render, waitFor} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import {Topbar} from '@src/components/shell/Topbar';
import {TopbarProvider, useTopbar} from '@src/lib/ui-state/topbar';

let mockPathname = '/dashboard';

vi.mock('next-intl', () => ({
  useLocale: () => 'zh-CN',
  useTranslations: () => (key: string) => {
    const dict: Record<string, string> = {
      'app.title': 'Family Knowledge Vault',
      'nav.dashboard': '总览',
      'nav.docs': '文档',
      'nav.cats': '分类',
      'nav.agent': 'Agent',
      'topbar.aiAssistant': 'AI 智能助手'
    };
    return dict[key] || key;
  }
}));

vi.mock('@/i18n/navigation', () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({
    replace: vi.fn()
  })
}));

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams()
}));

function TopbarStateSetter({title}: {title: string}) {
  const {setTopbar} = useTopbar();

  useEffect(() => {
    setTopbar({
      title,
      metaMode: 'text',
      count: 0,
      metaText: ''
    });
  }, [setTopbar, title]);

  return null;
}

describe('Topbar document title', () => {
  it('syncs title from route default', async () => {
    mockPathname = '/docs';

    render(
      <TopbarProvider>
        <Topbar />
      </TopbarProvider>
    );

    await waitFor(() => {
      expect(document.title).toBe('文档 | Family Knowledge Vault');
    });
  });

  it('prefers explicit page title from topbar state', async () => {
    mockPathname = '/cats/finance__bills__water';

    render(
      <TopbarProvider>
        <TopbarStateSetter title="水费账单" />
        <Topbar />
      </TopbarProvider>
    );

    await waitFor(() => {
      expect(document.title).toBe('水费账单 | Family Knowledge Vault');
    });
  });
});
