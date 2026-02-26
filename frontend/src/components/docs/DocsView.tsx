'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import type {KbDoc, UiLocale} from '@src/lib/api/types';
import {getKbClient} from '@src/lib/api/kb-client';
import {readCategoryAliasMap} from '@src/lib/ui-state/category-alias';
import {subscribeDocUpdated} from '@src/lib/ui-state/doc-events';
import {useOverlay} from '@src/lib/ui-state/overlay';
import {useTopbar} from '@src/lib/ui-state/topbar';
import {DocList} from './DocList';

function DocsView() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const client = useMemo(() => getKbClient(), []);
  const {openOverlay} = useOverlay();
  const {setTopbar} = useTopbar();

  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [aliases, setAliases] = useState<Record<string, string>>({});
  const [includeMissing, setIncludeMissing] = useState(false);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const PAGE_SIZE = 50;

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const qs = new URLSearchParams(window.location.search);
    const raw = String(qs.get('include_missing') || '').trim().toLowerCase();
    setIncludeMissing(raw === '1' || raw === 'true' || raw === 'yes');
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(String(query || '').trim());
      setOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    client
      .getDocsPage?.({includeMissing, q: debouncedQuery, limit: PAGE_SIZE, offset: 0})
      .then((page) => {
        if (!alive || !page) return;
        setDocs(page.items);
        setTotal(page.total);
        setOffset(page.items.length);
      })
      .catch(() => {
        if (!alive) return;
        setDocs([]);
        setTotal(0);
        setOffset(0);
      })
      .finally(() => {
        if (!alive) return;
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [client, includeMissing, debouncedQuery]);

  useEffect(() => {
    setAliases(readCategoryAliasMap());
  }, []);

  useEffect(() => {
    return subscribeDocUpdated((doc) => {
      setDocs((prev) => prev.map((row) => (row.id === doc.id ? doc : row)));
    });
  }, []);

  useEffect(() => {
    setTopbar({
      title: t('nav.docs'),
      metaMode: 'count',
      count: total || docs.length,
      metaText: ''
    });
  }, [docs.length, total, setTopbar, t]);

  const filteredDocs = useMemo(() => docs, [docs]);

  const hasMore = offset < total;

  const handleLoadMore = async () => {
    if (!client.getDocsPage || loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const page = await client.getDocsPage({includeMissing, q: debouncedQuery, limit: PAGE_SIZE, offset});
      setDocs((prev) => {
        const existing = new Set(prev.map((row) => row.id));
        return [...prev, ...page.items.filter((row) => !existing.has(row.id))];
      });
      setTotal(page.total);
      setOffset((prev) => prev + page.items.length);
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="view active" id="view-docs">
      <div className="search-bar">
        <input
          className="search-input"
          id="search-input"
          type="text"
          value={query}
          placeholder={t('docs.searchPlaceholder')}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button className="search-btn" type="button" aria-label={t('docs.searchButton')}>
          🔍
        </button>
      </div>

      <DocList
        docs={filteredDocs}
        locale={locale}
        aliases={aliases}
        id="doc-list"
        emptyText={loading ? t('docs.loading') : t('docs.empty')}
        onOpen={openOverlay}
      />
      {hasMore ? (
        <div className="docs-pagination-row">
          <button className="search-btn" type="button" disabled={loadingMore} onClick={() => void handleLoadMore()}>
            {loadingMore ? t('docs.loadingMore') : t('docs.loadMore')}
          </button>
          <span className="empty-text">
            {t('docs.count', {shown: docs.length, total})}
          </span>
        </div>
      ) : total > 0 ? (
        <div className="docs-pagination-row">
          <span className="empty-text">{t('docs.count', {shown: docs.length, total})}</span>
        </div>
      ) : null}
    </div>
  );
}

export {DocsView};
export default DocsView;
