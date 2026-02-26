'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {Link} from '@/i18n/navigation';
import {pathFromCatId} from '@src/lib/category';
import {getKbClient} from '@src/lib/api/kb-client';
import type {KbCategory, KbDoc, UiLocale} from '@src/lib/api/types';
import {pickBilingualText} from '@src/lib/i18n/bilingual';
import {readCategoryAliasMap} from '@src/lib/ui-state/category-alias';
import {subscribeDocUpdated} from '@src/lib/ui-state/doc-events';
import {useOverlay} from '@src/lib/ui-state/overlay';
import {useTopbar} from '@src/lib/ui-state/topbar';
import {DocList} from '@src/components/docs/DocList';

function categoryName(category: KbCategory | null, locale: UiLocale, aliases: Record<string, string>): string {
  if (!category) return '';
  const alias = String(aliases[category.path] || '').trim();
  if (alias) return alias;
  const selected = pickBilingualText(category.label, locale).text;
  return selected || category.path;
}

function filterDocs(docs: KbDoc[], query: string): KbDoc[] {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return docs;
  return docs.filter((doc) =>
    [doc.title.zh, doc.title.en, doc.summary.zh, doc.summary.en, doc.tags.join(' ')].join(' ').toLowerCase().includes(q)
  );
}

function CatDocsView({catId}: {catId: string}) {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const client = useMemo(() => getKbClient(), []);
  const {openOverlay} = useOverlay();
  const {setTopbar} = useTopbar();

  const [category, setCategory] = useState<KbCategory | null>(null);
  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [query, setQuery] = useState('');
  const [aliases, setAliases] = useState<Record<string, string>>({});

  useEffect(() => {
    setAliases(readCategoryAliasMap());
  }, []);

  useEffect(() => {
    let alive = true;
    const resolvedPath = pathFromCatId(catId);

    Promise.all([client.getCategories(), client.getDocs({categoryPath: resolvedPath})])
      .then(([categories, rows]) => {
        if (!alive) return;
        const matched = categories.find((item) => item.id === catId || item.path === resolvedPath) || null;
        setCategory(matched);
        setDocs(rows);
      })
      .catch(() => {
        if (!alive) return;
        setCategory(null);
        setDocs([]);
      });

    return () => {
      alive = false;
    };
  }, [catId, client]);

  useEffect(() => {
    return subscribeDocUpdated((doc) => {
      setDocs((prev) => prev.map((row) => (row.id === doc.id ? doc : row)));
    });
  }, []);

  const label = categoryName(category, locale, aliases);

  useEffect(() => {
    setTopbar({
      title: label || t('cats.unknownCategory'),
      metaMode: 'count',
      count: docs.length,
      metaText: ''
    });
  }, [docs.length, label, setTopbar, t]);

  const filtered = useMemo(() => filterDocs(docs, query), [docs, query]);

  return (
    <div className="view active" id="view-cat-docs">
      <div className="breadcrumb">
        <Link href="/cats" className="breadcrumb-back">
          ← {t('catDocs.back')}
        </Link>
        <span className="breadcrumb-sep">›</span>
        <span className="breadcrumb-current" id="cat-docs-breadcrumb">
          {label || t('cats.unknownCategory')}
        </span>
      </div>

      <div className="cat-doc-header">
        <div className="cat-doc-icon" id="cat-docs-icon">
          {category?.icon || '📂'}
        </div>
        <div className="cat-doc-info">
          <div className="cat-doc-name" id="cat-docs-name">
            {label || t('cats.unknownCategory')}
          </div>
          <div className="cat-doc-count" id="cat-docs-count">
            {t('catDocs.count', {count: docs.length})}
          </div>
        </div>
      </div>

      <div className="search-bar">
        <input
          className="search-input"
          id="cat-search-input"
          type="text"
          value={query}
          placeholder={t('catDocs.searchPlaceholder')}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button className="search-btn" type="button" aria-label={t('docs.searchButton')}>
          🔍
        </button>
      </div>

      <DocList
        id="cat-doc-list"
        docs={filtered}
        locale={locale}
        aliases={aliases}
        emptyText={t('catDocs.empty')}
        onOpen={openOverlay}
      />
    </div>
  );
}

export {CatDocsView};
export default CatDocsView;
