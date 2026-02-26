'use client';

import type {KeyboardEvent} from 'react';
import {iconForCategory} from '@src/lib/category';
import type {KbDoc, UiLocale} from '@src/lib/api/types';
import {pickBilingualText} from '@src/lib/i18n/bilingual';

interface DocCardProps {
  doc: KbDoc;
  locale: UiLocale;
  categoryLabel: string;
  badgeClassName: string;
  onOpen: (docId: string) => void;
}

export function DocCard({doc, locale, categoryLabel, badgeClassName, onOpen}: DocCardProps) {
  const title = pickBilingualText(doc.title, locale);
  const summary = pickBilingualText(doc.summary, locale);
  const icon = iconForCategory(doc.categoryPath);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onOpen(doc.id);
  };

  return (
    <div className="doc-item" role="button" tabIndex={0} onClick={() => onOpen(doc.id)} onKeyDown={onKeyDown}>
      <div className="doc-header">
        <div>
          <div className="doc-title">
            {icon} {title.text || doc.fileName}
            {title.fallbackLabel ? <span className="lang-fallback">{title.fallbackLabel}</span> : null}
          </div>
          <div className="doc-filename">
            {doc.fileName}
            {!doc.sourceAvailable ? (
              <span className="source-missing-pill">{locale === 'zh-CN' ? '源文件缺失' : 'Source Missing'}</span>
            ) : null}
          </div>
        </div>
        <span className={badgeClassName}>{categoryLabel}</span>
      </div>

      <div className="doc-summary">
        {summary.text || '-'}
        {summary.fallbackLabel ? <span className="lang-fallback">{summary.fallbackLabel}</span> : null}
      </div>

      <div className="doc-tags">
        {doc.tags.map((tag) => (
          <span key={`${doc.id}-${tag}`} className="tag">
            {tag}
          </span>
        ))}
      </div>
    </div>
  );
}
