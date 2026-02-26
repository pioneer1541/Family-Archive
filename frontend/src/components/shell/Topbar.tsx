'use client';

import {useEffect, useRef} from 'react';
import type {ReactNode} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {useSearchParams} from 'next/navigation';
import {usePathname, useRouter} from '@/i18n/navigation';
import {useTopbar} from '@src/lib/ui-state/topbar';

function defaultTitle(pathname: string, t: ReturnType<typeof useTranslations>): string {
  const normalized = String(pathname || '').replace(/^\/(zh-CN|en-AU)(?=\/|$)/, '') || '/';
  if (normalized.startsWith('/docs')) return t('nav.docs');
  if (normalized.startsWith('/cats')) return t('nav.cats');
  if (normalized.startsWith('/agent')) return t('nav.agent');
  return t('nav.dashboard');
}

function metaText(count: number, locale: string): ReactNode {
  if (locale === 'zh-CN') {
    return (
      <>
        共 <strong>{count}</strong> 份文档
      </>
    );
  }
  return (
    <>
      Total <strong>{count}</strong> docs
    </>
  );
}

export function Topbar({onToggleMobileSidebar}: {onToggleMobileSidebar?: () => void}) {
  const t = useTranslations();
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const {state} = useTopbar();
  const appTitle = t('app.title');
  const topbarRef = useRef<HTMLDivElement | null>(null);

  const title = state.title || defaultTitle(pathname, t);
  const content =
    state.metaMode === 'text'
      ? state.metaText || (locale === 'zh-CN' ? 'AI 智能助手' : 'AI Assistant')
      : state.metaMode === 'count'
      ? metaText(Number(state.count || 0), locale)
      : '';

  const switchLocale = (nextLocale: 'zh-CN' | 'en-AU') => {
    if (nextLocale === locale) return;
    const qs = searchParams?.toString() || '';
    const nextPath = qs ? `${pathname}?${qs}` : pathname;
    router.replace(nextPath, {locale: nextLocale});
  };

  useEffect(() => {
    const pageTitle = String(title || '').trim();
    document.title = pageTitle ? `${pageTitle} | ${appTitle}` : appTitle;
  }, [appTitle, title]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const root = document.documentElement;
    const update = () => {
      const height = Math.round(topbarRef.current?.offsetHeight || 57);
      root.style.setProperty('--topbar-height', `${height}px`);
    };
    update();
    window.addEventListener('resize', update, {passive: true});
    window.visualViewport?.addEventListener('resize', update, {passive: true});
    return () => {
      window.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('resize', update);
    };
  }, [title, content]);

  return (
    <div className="topbar" ref={topbarRef}>
      <div className="topbar-title-row">
        <button
          type="button"
          className="mobile-menu-btn"
          onClick={onToggleMobileSidebar}
          aria-label={t('shell.mobileMenuAria')}
        >
          ☰
        </button>
        <h2 id="page-title">{title}</h2>
      </div>
      {state.metaMode === 'locale_switch' ? (
        <div className="locale-switch" id="topbar-meta" role="group" aria-label={locale === 'zh-CN' ? '语言切换' : 'Language switch'}>
          <button
            type="button"
            className={`locale-btn${locale === 'zh-CN' ? ' active' : ''}`}
            onClick={() => switchLocale('zh-CN')}
          >
            中文
          </button>
          <span className="locale-sep">|</span>
          <button
            type="button"
            className={`locale-btn${locale === 'en-AU' ? ' active' : ''}`}
            onClick={() => switchLocale('en-AU')}
          >
            EN
          </button>
        </div>
      ) : (
        <span className="topbar-meta" id="topbar-meta">
          {content}
        </span>
      )}
    </div>
  );
}
