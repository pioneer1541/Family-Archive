'use client';

import {useEffect, useState} from 'react';
import {useTranslations} from 'next-intl';
import type {KbCategory} from '@src/lib/api/types';

interface CatCardProps {
  category: KbCategory;
  displayName: string;
  onOpen: (catId: string) => void;
  onRename: (categoryPath: string, nextName: string) => void;
}

export function CatCard({category, displayName, onOpen, onRename}: CatCardProps) {
  const t = useTranslations();
  const [draft, setDraft] = useState(displayName);

  useEffect(() => {
    setDraft(displayName);
  }, [displayName]);

  return (
    <div className="cat-card cat-card-clickable" onClick={() => onOpen(category.id)}>
      <div className="cat-card-header">
        <span className="cat-card-icon">{category.icon}</span>
        <div className="cat-count-box">
          <div className="cat-count-big">{category.count}</div>
          <div className="cat-count-label">{t('cats.docsUnit')}</div>
        </div>
      </div>

      <div className="cat-card-name" id={`cat-name-${category.id}`}>
        {displayName}
      </div>

      <div
        className="rename-row"
        onClick={(event) => {
          event.stopPropagation();
        }}
      >
        <input
          id={`rename-${category.id}`}
          className="rename-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t('cats.renamePlaceholder')}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            const value = String(draft || '').trim();
            if (!value) return;
            onRename(category.path, value);
          }}
        />
        <button
          className="rename-btn"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            const value = String(draft || '').trim();
            if (!value) return;
            onRename(category.path, value);
          }}
        >
          {t('cats.rename')}
        </button>
      </div>
    </div>
  );
}
