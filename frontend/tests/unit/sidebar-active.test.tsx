import type {ReactNode} from 'react';
import {render, screen} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import {Sidebar} from '@src/components/shell/Sidebar';

let mockPathname = '/dashboard';

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => {
    const dict: Record<string, string> = {
      'app.brandLine1': 'Family',
      'app.brandLine2': 'Archive',
      'app.brandSub': 'FAMILY ARCHIVE',
      'nav.dashboard': 'Dashboard',
      'nav.docs': 'Documents',
      'nav.cats': 'Categories',
      'nav.agent': 'Agent'
    };
    return dict[key] || key;
  }
}));

vi.mock('@/i18n/navigation', () => ({
  Link: ({children, href, className}: {children: ReactNode; href: string; className: string}) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
  usePathname: () => mockPathname
}));

describe('Sidebar active state', () => {
  it('keeps cats nav active on /cats/[catId]', () => {
    mockPathname = '/cats/finance__bills';
    render(<Sidebar />);

    const cats = screen.getByText('Categories').closest('a');
    const dashboard = screen.getByText('Dashboard').closest('a');

    expect(cats).toHaveClass('nav-item active');
    expect(dashboard).toHaveClass('nav-item');
    expect(dashboard).not.toHaveClass('active');
  });
});
