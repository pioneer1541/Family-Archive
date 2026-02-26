'use client';

import type {ReactNode} from 'react';
import {useEffect, useMemo, useState} from 'react';
import {useTranslations} from 'next-intl';
import {Link, usePathname} from '@/i18n/navigation';
import {Sidebar} from './Sidebar';
import {Topbar} from './Topbar';
import {DetailOverlay} from '@src/components/overlay/DetailOverlay';
import {DocumentContentOverlay} from '@src/components/overlay/DocumentContentOverlay';
import {SyncTaskOverlay} from '@src/components/overlay/SyncTaskOverlay';
import {Toast} from '@src/components/common/Toast';
import {ContentViewerProvider} from '@src/lib/ui-state/content-viewer';
import {SyncViewerProvider} from '@src/lib/ui-state/sync-viewer';
import {TopbarProvider} from '@src/lib/ui-state/topbar';
import {OverlayProvider} from '@src/lib/ui-state/overlay';
import {ToastProvider} from '@src/lib/ui-state/toast';

interface MobileNavItem {
  href: '/dashboard' | '/docs' | '/cats' | '/agent';
  key: 'dashboard' | 'docs' | 'cats' | 'agent';
  icon: string;
}

const MOBILE_NAV_ITEMS: MobileNavItem[] = [
  {href: '/dashboard', key: 'dashboard', icon: '⊞'},
  {href: '/docs', key: 'docs', icon: '◫'},
  {href: '/cats', key: 'cats', icon: '⊟'},
  {href: '/agent', key: 'agent', icon: '✦'}
];

function normalizePath(pathname: string): string {
  return String(pathname || '').replace(/^\/(zh-CN|en-AU)(?=\/|$)/, '') || '/';
}

function isActive(pathname: string, href: string): boolean {
  const normalized = normalizePath(pathname);
  if (href === '/cats') return normalized === '/cats' || normalized.startsWith('/cats/');
  return normalized === href;
}

function AppShell({children}: {children: ReactNode}) {
  const pathname = usePathname();
  const t = useTranslations();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(false);

  useEffect(() => {
    setMobileSidebarOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const root = document.documentElement;
    const body = document.body;

    const updateViewportState = () => {
      const vv = window.visualViewport;
      const viewportHeight = Math.round(vv?.height ?? window.innerHeight);
      root.style.setProperty('--app-height', `${viewportHeight}px`);
      const keyboardDelta = Math.max(0, Math.round(window.innerHeight - (vv?.height ?? window.innerHeight)));
      const isKeyboardOpen = keyboardDelta >= 140;
      setKeyboardOpen(isKeyboardOpen);
      body.classList.toggle('keyboard-open', isKeyboardOpen);
      root.style.setProperty('--bottom-tab-visible', isKeyboardOpen ? '0px' : 'calc(var(--bottom-tab-height) + env(safe-area-inset-bottom))');
    };

    updateViewportState();
    window.addEventListener('resize', updateViewportState, {passive: true});
    window.visualViewport?.addEventListener('resize', updateViewportState, {passive: true});

    return () => {
      window.removeEventListener('resize', updateViewportState);
      window.visualViewport?.removeEventListener('resize', updateViewportState);
      body.classList.remove('keyboard-open');
    };
  }, []);

  const activeTabKey = useMemo(() => {
    const active = MOBILE_NAV_ITEMS.find((item) => isActive(pathname, item.href));
    return active?.key || 'dashboard';
  }, [pathname]);

  const normalizedPath = useMemo(() => normalizePath(pathname), [pathname]);
  const isAgentRoute = normalizedPath.startsWith('/agent');

  return (
    <ToastProvider>
      <OverlayProvider>
        <ContentViewerProvider>
          <SyncViewerProvider>
            <TopbarProvider>
              <Sidebar open={mobileSidebarOpen} onNavigate={() => setMobileSidebarOpen(false)} />
              <div
                className={`sidebar-backdrop${mobileSidebarOpen ? ' open' : ''}`}
                id="sidebar-backdrop"
                onClick={() => setMobileSidebarOpen(false)}
              />
              <div className={`main${keyboardOpen ? ' keyboard-open' : ''}`}>
                <Topbar onToggleMobileSidebar={() => setMobileSidebarOpen((prev) => !prev)} />
                <div className={`content${isAgentRoute ? ' content-agent' : ''}`}>{children}</div>
              </div>
              <nav className={`bottom-tab-bar${keyboardOpen ? ' hidden-by-keyboard' : ''}`}>
                {MOBILE_NAV_ITEMS.map((item) => (
                  <Link
                    key={item.key}
                    id={`tab-${item.key}`}
                    href={item.href}
                    className={`tab-item${activeTabKey === item.key ? ' active' : ''}`}
                  >
                    <span className="tab-icon">{item.icon}</span>
                    <span className="tab-label">{t(`nav.${item.key}`)}</span>
                  </Link>
                ))}
              </nav>
              <DetailOverlay />
              <DocumentContentOverlay />
              <SyncTaskOverlay />
              <Toast />
            </TopbarProvider>
          </SyncViewerProvider>
        </ContentViewerProvider>
      </OverlayProvider>
    </ToastProvider>
  );
}

export {AppShell};
export default AppShell;
