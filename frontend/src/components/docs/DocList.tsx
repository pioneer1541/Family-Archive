'use client';

import {colorIndexForCategory} from '@src/lib/category';
import type {KbDoc, UiLocale} from '@src/lib/api/types';
import {pickBilingualText} from '@src/lib/i18n/bilingual';
import {DocCard} from './DocCard';

interface DocListProps {
  docs: KbDoc[];
  locale: UiLocale;
  id: string;
  emptyText: string;
  aliases?: Record<string, string>;
  onOpen: (docId: string) => void;
}

function categoryLabel(doc: KbDoc, locale: UiLocale, aliases?: Record<string, string>): string {
  const alias = String(aliases?.[doc.categoryPath] || '').trim();
  if (alias) return alias;
  const selected = pickBilingualText(doc.categoryLabel, locale).text;
  return selected || doc.categoryPath;
}

export function DocList({docs, locale, id, emptyText, aliases, onOpen}: DocListProps) {
  return (
    <div className="doc-list" id={id}>
      {!docs.length ? (
        <div className="empty-text">{emptyText}</div>
      ) : (
        docs.map((doc) => (
          <DocCard
            key={doc.id}
            doc={doc}
            locale={locale}
            categoryLabel={categoryLabel(doc, locale, aliases)}
            badgeClassName={`cat-badge cat-color-${colorIndexForCategory(doc.categoryPath)}`}
            onOpen={onOpen}
          />
        ))
      )}
    </div>
  );
}
