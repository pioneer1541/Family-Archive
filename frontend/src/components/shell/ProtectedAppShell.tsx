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
import {isEditableField, parsePxValue, shouldScrollFocusedField} from '@src/lib/mobile/focusVisibility';

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
    let focusTimer: number | null = null;

    const ensureFocusedFieldVisible = () => {
      const active = document.activeElement;
      if (!isEditableField(active)) return;

      const vv = window.visualViewport;
      const viewportTop = Math.round(vv?.offsetTop ?? 0);
      const viewportHeight = Math.round(vv?.height ?? window.innerHeight);
      const rect = active.getBoundingClientRect();
      const topbarHeight = parsePxValue(
        root.style.getPropertyValue('--topbar-height') || getComputedStyle(root).getPropertyValue('--topbar-height'),
        57
      );
      const isKeyboardOpen = root.dataset.keyboardOpen === 'true';
      const bottomTabBar = document.querySelector<HTMLElement>('.bottom-tab-bar');
      const fixedBottomHeight = isKeyboardOpen ? 0 : Math.round(bottomTabBar?.offsetHeight ?? 0);

      if (
        shouldScrollFocusedField({
          windowWidth: window.innerWidth,
          viewportTop,
          viewportHeight,
          topbarHeight,
          fixedBottomHeight,
          rectTop: rect.top,
          rectBottom: rect.bottom
        })
      ) {
        active.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'auto'});
      }
    };

    const scheduleFocusCorrection = () => {
      if (focusTimer) window.clearTimeout(focusTimer);
      focusTimer = window.setTimeout(() => {
        ensureFocusedFieldVisible();
      }, 120);
    };

    const updateViewportState = () => {
      const vv = window.visualViewport;
      const viewportTop = Math.round(vv?.offsetTop ?? 0);
      const viewportHeight = Math.round(vv?.height ?? window.innerHeight);
      const viewportBottom = Math.min(window.innerHeight, viewportTop + viewportHeight);
      root.style.setProperty('--app-height', `${viewportBottom}px`);
      const keyboardDelta = Math.max(0, Math.round(window.innerHeight - viewportBottom));
      const isKeyboardOpen = keyboardDelta >= 140;
      root.style.setProperty('--keyboard-open', isKeyboardOpen ? '1' : '0');
      root.style.setProperty('--keyboard-offset', `${keyboardDelta}px`);
      root.dataset.keyboardOpen = isKeyboardOpen ? 'true' : 'false';
      if (isKeyboardOpen) scheduleFocusCorrection();
    };

    updateViewportState();
    document.addEventListener('focusin', scheduleFocusCorrection, true);
    window.addEventListener('resize', updateViewportState, {passive: true});
    window.visualViewport?.addEventListener('resize', updateViewportState, {passive: true});
    window.visualViewport?.addEventListener('scroll', scheduleFocusCorrection, {passive: true});

    return () => {
      if (focusTimer) window.clearTimeout(focusTimer);
      document.removeEventListener('focusin', scheduleFocusCorrection, true);
      window.removeEventListener('resize', updateViewportState);
      window.visualViewport?.removeEventListener('resize', updateViewportState);
      window.visualViewport?.removeEventListener('scroll', scheduleFocusCorrection);
      root.style.setProperty('--keyboard-open', '0');
      root.style.setProperty('--keyboard-offset', '0px');
      root.dataset.keyboardOpen = 'false';
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
