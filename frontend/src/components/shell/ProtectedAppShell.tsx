'use client';

import type {ReactNode} from 'react';
import {useEffect, useMemo, useState} from 'react';
import dynamic from 'next/dynamic';
import {useTranslations} from 'next-intl';
import {Link, usePathname} from '@/i18n/navigation';
import {Sidebar} from './Sidebar';
import {Topbar} from './Topbar';
import {ToastProvider} from '@src/lib/ui-state/toast';
import {OverlayProvider} from '@src/lib/ui-state/overlay';
import {ContentViewerProvider} from '@src/lib/ui-state/content-viewer';
import {SyncViewerProvider} from '@src/lib/ui-state/sync-viewer';
import {TopbarProvider} from '@src/lib/ui-state/topbar';
import {AuthGuard} from '@src/components/auth/AuthGuard';
import {normalizePath} from '@src/lib/utils/paths';

const DetailOverlay = dynamic(() => import('@src/components/overlay/DetailOverlay').then((mod) => mod.DetailOverlay), {ssr: false});
const DocumentContentOverlay = dynamic(() => import('@src/components/overlay/DocumentContentOverlay').then((mod) => mod.DocumentContentOverlay), {ssr: false});
const SyncTaskOverlay = dynamic(() => import('@src/components/overlay/SyncTaskOverlay').then((mod) => mod.SyncTaskOverlay), {ssr: false});
const Toast = dynamic(() => import('@src/components/common/Toast').then((mod) => mod.Toast), {ssr: false});

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

function isActive(pathname: string, href: string): boolean {
  const normalized = normalizePath(pathname);
  if (href === '/cats') return normalized === '/cats' || normalized.startsWith('/cats/');
  return normalized === href;
}

export function ProtectedAppShell({children}: {children: ReactNode}) {
  const pathname = usePathname();
  const t = useTranslations();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  useEffect(() => {
    setMobileSidebarOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const root = document.documentElement;

    const updateViewportState = () => {
      const vv = window.visualViewport;
      const viewportHeight = Math.round(vv?.height ?? window.innerHeight);
      root.style.setProperty('--app-height', `${viewportHeight}px`);
      const keyboardDelta = Math.max(0, Math.round(window.innerHeight - (vv?.height ?? window.innerHeight)));
      const isKeyboardOpen = keyboardDelta >= 140;
      root.style.setProperty('--keyboard-open', isKeyboardOpen ? '1' : '0');
    };

    updateViewportState();
    window.addEventListener('resize', updateViewportState, {passive: true});
    window.visualViewport?.addEventListener('resize', updateViewportState, {passive: true});

    return () => {
      window.removeEventListener('resize', updateViewportState);
      window.visualViewport?.removeEventListener('resize', updateViewportState);
      root.style.setProperty('--keyboard-open', '0');
    };
  }, []);

  const activeTabKey = useMemo(() => {
    const active = MOBILE_NAV_ITEMS.find((item) => isActive(pathname, item.href));
    return active?.key || 'dashboard';
  }, [pathname]);

  const normalizedPath = useMemo(() => normalizePath(pathname), [pathname]);
  const isAgentRoute = normalizedPath.startsWith('/agent');

  return (
    <AuthGuard>
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
                <div className="main">
                  <Topbar onToggleMobileSidebar={() => setMobileSidebarOpen((prev) => !prev)} />
                  <div className={`content${isAgentRoute ? ' content-agent' : ''}`}>{children}</div>
                </div>
                <nav className="bottom-tab-bar">
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
    </AuthGuard>
  );
}

export default ProtectedAppShell;
