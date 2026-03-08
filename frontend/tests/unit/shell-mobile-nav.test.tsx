import type {ReactNode} from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {ProtectedAppShell} from '@src/components/shell/ProtectedAppShell';

let mockPathname = '/dashboard';

vi.mock('next-intl', () => ({
  useLocale: () => 'zh-CN',
  useTranslations: () => (key: string) => {
    const dict: Record<string, string> = {
      'app.title': 'Family Knowledge Vault',
      'app.brandLine1': '家庭',
      'app.brandLine2': '资料库',
      'app.brandSub': 'FAMILY ARCHIVE',
      'nav.dashboard': '总览',
      'nav.docs': '文档',
      'nav.cats': '分类',
      'nav.agent': 'Agent',
      'shell.mobileMenuAria': '菜单',
      'topbar.aiAssistant': 'AI 智能助手'
    };
    return dict[key] || key;
  }
}));

vi.mock('@/i18n/navigation', () => ({
  Link: ({children, href, className, id, onClick}: {children: ReactNode; href: string; className?: string; id?: string; onClick?: () => void}) => (
    <a href={href} className={className} id={id} onClick={onClick}>
      {children}
    </a>
  ),
  usePathname: () => mockPathname,
  useRouter: () => ({
    replace: vi.fn()
  })
}));

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter:       () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  usePathname:     () => mockPathname,
}));

vi.mock('@src/lib/api/kb-client', () => ({
  getKbClient: () => ({
    getDoc: vi.fn().mockResolvedValue(null),
    getContentAvailability: vi.fn().mockResolvedValue({sourceAvailable: false, inlineSupported: false, detail: 'document_not_found'})
  })
}));

describe('App shell mobile drawer and bottom tabs', () => {
  beforeEach(() => {
    mockPathname = '/dashboard';
    document.body.classList.remove('keyboard-open');
    Object.defineProperty(window, 'visualViewport', {
      value: undefined,
      configurable: true
    });
  });

  it('toggles mobile sidebar with topbar button and backdrop', async () => {
    render(
      <ProtectedAppShell>
        <div>child</div>
      </ProtectedAppShell>
    );

    const sidebar = document.querySelector('.sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    expect(sidebar).not.toHaveClass('open');
    expect(backdrop).not.toHaveClass('open');

    fireEvent.click(screen.getByRole('button', {name: '菜单'}));
    expect(sidebar).toHaveClass('open');
    expect(backdrop).toHaveClass('open');

    fireEvent.click(backdrop as HTMLElement);
    await waitFor(() => {
      expect(sidebar).not.toHaveClass('open');
      expect(backdrop).not.toHaveClass('open');
    });
  });

  it('keeps bottom tab active state in sync and auto closes drawer on route change', async () => {
    const {rerender} = render(
      <ProtectedAppShell>
        <div>child</div>
      </ProtectedAppShell>
    );

    fireEvent.click(screen.getByRole('button', {name: '菜单'}));
    expect(document.querySelector('.sidebar')).toHaveClass('open');
    expect(document.getElementById('tab-dashboard')).toHaveClass('tab-item active');

    mockPathname = '/docs';
    rerender(
      <ProtectedAppShell>
        <div>child</div>
      </ProtectedAppShell>
    );

    await waitFor(() => {
      expect(document.querySelector('.sidebar')).not.toHaveClass('open');
    });
    expect(document.getElementById('tab-docs')).toHaveClass('tab-item active');
    expect(document.getElementById('tab-dashboard')).not.toHaveClass('active');
  });

  it('hides bottom tab when virtual keyboard is open', async () => {
    let resizeHandler: (() => void) | null = null;
    const vv = {
      height: 420,
      addEventListener: (_event: string, cb: () => void) => {
        resizeHandler = cb;
      },
      removeEventListener: () => {
        resizeHandler = null;
      }
    };
    Object.defineProperty(window, 'visualViewport', {
      value: vv,
      configurable: true
    });
    Object.defineProperty(window, 'innerHeight', {
      value: 844,
      configurable: true
    });

    render(
      <ProtectedAppShell>
        <div>child</div>
      </ProtectedAppShell>
    );

    await waitFor(() => {
      expect(document.body.classList.contains('keyboard-open')).toBe(true);
      expect(document.querySelector('.bottom-tab-bar')).toHaveClass('hidden-by-keyboard');
    });

    expect(typeof resizeHandler === 'function' || resizeHandler === null).toBe(true);
  });
});
