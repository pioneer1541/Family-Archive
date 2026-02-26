'use client';

import {useTranslations} from 'next-intl';
import {Link, usePathname} from '@/i18n/navigation';

interface NavItem {
  href: '/dashboard' | '/docs' | '/cats' | '/agent';
  icon: string;
  key: 'dashboard' | 'docs' | 'cats' | 'agent';
}

interface SidebarProps {
  open?: boolean;
  onNavigate?: () => void;
}

const NAV_ITEMS: NavItem[] = [
  {href: '/dashboard', icon: '⊞', key: 'dashboard'},
  {href: '/docs', icon: '◫', key: 'docs'},
  {href: '/cats', icon: '⊟', key: 'cats'},
  {href: '/agent', icon: '✦', key: 'agent'}
];

function normalizePath(pathname: string): string {
  return String(pathname || '').replace(/^\/(zh-CN|en-AU)(?=\/|$)/, '') || '/';
}

function isActive(pathname: string, href: string): boolean {
  const normalized = normalizePath(pathname);
  if (href === '/cats') return normalized === '/cats' || normalized.startsWith('/cats/');
  return normalized === href;
}

export function Sidebar({open = false, onNavigate}: SidebarProps) {
  const pathname = usePathname();
  const t = useTranslations();

  return (
    <nav className={`sidebar${open ? ' open' : ''}`}>
      <div className="sidebar-brand">
        <h1>
          {t('app.brandLine1')}
          <br />
          {t('app.brandLine2')}
        </h1>
        <p>{t('app.brandSub')}</p>
      </div>
      <div className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`nav-item${isActive(pathname, item.href) ? ' active' : ''}`}
            onClick={onNavigate}
          >
            <span className="icon">{item.icon}</span>
            {t(`nav.${item.key}`)}
          </Link>
        ))}
      </div>
    </nav>
  );
}

export default Sidebar;
