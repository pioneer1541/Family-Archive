'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import type {KbCategory, UiLocale} from '@src/lib/api/types';
import {getKbClient} from '@src/lib/api/kb-client';
import {useRouter} from '@/i18n/navigation';
import {pickBilingualText} from '@src/lib/i18n/bilingual';
import {readCategoryAliasMap, writeCategoryAlias} from '@src/lib/ui-state/category-alias';
import {useToast} from '@src/lib/ui-state/toast';
import {useTopbar} from '@src/lib/ui-state/topbar';
import {CatCard} from './CatCard';

function categoryName(category: KbCategory, locale: UiLocale, aliases: Record<string, string>): string {
  const alias = String(aliases[category.path] || '').trim();
  if (alias) return alias;
  const selected = pickBilingualText(category.label, locale).text;
  return selected || category.path;
}

function CatsView() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const router = useRouter();
  const client = useMemo(() => getKbClient(), []);
  const {showToast} = useToast();
  const {setTopbar} = useTopbar();

  const [categories, setCategories] = useState<KbCategory[]>([]);
  const [aliases, setAliases] = useState<Record<string, string>>({});

  useEffect(() => {
    let alive = true;
    client
      .getCategories()
      .then((rows) => {
        if (!alive) return;
        setCategories(rows);
      })
      .catch(() => {
        if (!alive) return;
        setCategories([]);
      });
    return () => {
      alive = false;
    };
  }, [client]);

  useEffect(() => {
    setAliases(readCategoryAliasMap());
  }, []);

  const totalCount = useMemo(() => categories.reduce((sum, item) => sum + item.count, 0), [categories]);

  useEffect(() => {
    setTopbar({
      title: t('nav.cats'),
      metaMode: 'count',
      count: totalCount,
      metaText: ''
    });
  }, [setTopbar, t, totalCount]);

  const sorted = useMemo(() => categories.slice().sort((a, b) => b.count - a.count), [categories]);

  return (
    <div className="view active" id="view-cats">
      <div className="cat-list" id="cat-list">
        {sorted.map((category) => (
          <CatCard
            key={category.id}
            category={category}
            displayName={categoryName(category, locale, aliases)}
            onOpen={(catId) => router.push(`/cats/${catId}`)}
            onRename={(path, nextName) => {
              writeCategoryAlias(path, nextName);
              setAliases((prev) => ({...prev, [path]: nextName}));
              showToast(t('cats.renameSaved', {name: nextName}));
            }}
          />
        ))}
      </div>
    </div>
  );
}

export {CatsView};
export default CatsView;
