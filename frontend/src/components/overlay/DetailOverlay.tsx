'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {colorIndexForCategory, iconForCategory} from '@src/lib/category';
import {getKbClient} from '@src/lib/api/kb-client';
import type {BilingualText, KbDoc, RegenSummaryResult, UiLocale} from '@src/lib/api/types';
import {pickBilingualText} from '@src/lib/i18n/bilingual';
import {emitDocUpdated} from '@src/lib/ui-state/doc-events';
import {useContentViewer} from '@src/lib/ui-state/content-viewer';
import {useOverlay} from '@src/lib/ui-state/overlay';
import {useToast} from '@src/lib/ui-state/toast';

type ContentLang = 'zh' | 'en';

const EMPTY_BILINGUAL: BilingualText = {zh: '', en: ''};

function toUiLocale(lang: ContentLang): UiLocale {
  return lang === 'zh' ? 'zh-CN' : 'en-AU';
}

function pickByLang(value: BilingualText, lang: ContentLang) {
  return pickBilingualText(value || EMPTY_BILINGUAL, toUiLocale(lang));
}

function regenFailReasonText(t: (key: string) => string, result: RegenSummaryResult): string {
  const reason = String(result.applyReason || '').trim();
  if (reason === 'llm_failed') return t('overlay.regenFailLlm');
  if (reason === 'needs_regen' || reason === 'quality_not_ok') return t('overlay.regenFailQuality');
  if (reason === 'document_not_found') return t('overlay.regenFailNotFound');
  if (reason) return reason;
  if (result.qualityFlags.length) return result.qualityFlags.join(', ');
  return t('overlay.regenFailUnknown');
}

function qualityStateText(t: (key: string) => string, qualityState: string): string {
  const state = String(qualityState || '').trim();
  if (state === 'ok') return t('overlay.qualityOk');
  if (state === 'needs_regen') return t('overlay.qualityNeedsRegen');
  if (state === 'llm_failed') return t('overlay.qualityLlmFailed');
  return t('overlay.qualityUnknown');
}

function cascadeReasonText(t: (key: string) => string, reason: string): string {
  const value = String(reason || '').trim();
  if (value === 'ok') return t('overlay.cascadeReasonOk');
  if (value === 'summary_not_ok') return t('overlay.cascadeReasonSummaryNotOk');
  if (value === 'category_not_recomputed') return t('overlay.cascadeReasonCategory');
  if (value === 'tags_not_recomputed') return t('overlay.cascadeReasonTags');
  if (value === 'qdrant_error') return t('overlay.cascadeReasonQdrant');
  if (value === 'document_not_found') return t('overlay.cascadeReasonNotFound');
  if (!value) return t('overlay.cascadeReasonUnknown');
  return value;
}

export function DetailOverlay() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const client = useMemo(() => getKbClient(), []);
  const {open, docId, closeOverlay} = useOverlay();
  const {openViewer} = useContentViewer();
  const {showToast} = useToast();

  const [doc, setDoc] = useState<KbDoc | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [regening, setRegening] = useState(false);
  const [lang, setLang] = useState<ContentLang>('zh');
  const [nameInput, setNameInput] = useState('');

  useEffect(() => {
    if (!open || !docId) return;
    const nextLang: ContentLang = locale === 'zh-CN' ? 'zh' : 'en';
    setLang(nextLang);
    setDoc(null);

    let alive = true;
    setLoading(true);
    client
      .getDoc(docId)
      .then((row) => {
        if (!alive) return;
        setDoc(row);
      })
      .catch(() => {
        if (!alive) return;
        setDoc(null);
      })
      .finally(() => {
        if (!alive) return;
        setLoading(false);
      });

    return () => {
      alive = false;
    };
  }, [client, docId, locale, open]);

  useEffect(() => {
    if (!doc) {
      setNameInput('');
      return;
    }
    setNameInput(lang === 'zh' ? String(doc.title.zh || '') : String(doc.title.en || ''));
  }, [doc, lang]);

  const titleValue = pickByLang(doc?.title || EMPTY_BILINGUAL, lang);
  const summaryValue = pickByLang(doc?.summary || EMPTY_BILINGUAL, lang);
  const categoryValue = pickBilingualText(doc?.categoryLabel || EMPTY_BILINGUAL, locale);

  const handleSaveName = async () => {
    if (!doc) return;
    const value = String(nameInput || '').trim();
    if (!value) return;

    setSaving(true);
    try {
      const updated = await client.patchDoc(doc.id, lang === 'zh' ? {friendlyNameZh: value} : {friendlyNameEn: value});
      if (!updated) return;
      setDoc(updated);
      emitDocUpdated(updated);
      showToast(t('overlay.toastFriendlySaved'));
    } finally {
      setSaving(false);
    }
  };

  const handleRegen = async () => {
    if (!doc || regening) return;
    setRegening(true);
    try {
      const result = await client.regenSummary(doc.id, locale);
      if (result.doc) {
        setDoc(result.doc);
        emitDocUpdated(result.doc);
      }
      if (result.applied && result.cascadeApplied && result.categoryRecomputed && result.tagsRecomputed) {
        showToast(t('overlay.toastSummaryCascadeSuccess'));
      } else if (result.applied) {
        const reasonText = cascadeReasonText(t, result.cascadeReason);
        showToast(`${t('overlay.toastSummaryCascadePartial')}: ${reasonText}`);
      } else {
        const reasonText = regenFailReasonText(t, result);
        showToast(`${t('overlay.toastSummaryRegenFailedKeep')}: ${reasonText}`);
      }
    } finally {
      setRegening(false);
    }
  };

  return (
    <div
      className={`detail-overlay${open ? ' open' : ''}`}
      id="detail-overlay"
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        closeOverlay();
      }}
    >
      <div className="detail-panel" onClick={(event) => event.stopPropagation()}>
        <div className="detail-header">
          <button className="detail-close" type="button" onClick={closeOverlay}>
            ✕
          </button>
          <div className="detail-title" id="d-title">
            {titleValue.text || '-'}
            {titleValue.fallbackLabel ? <span className="lang-fallback">{titleValue.fallbackLabel}</span> : null}
          </div>
          <div className="detail-value mono" id="d-filename">
            {doc?.fileName || '-'}
          </div>
        </div>

        <div className="detail-body">
          <div className="detail-field">
            <div className="detail-label-row">
              <span className="detail-label">{t('overlay.friendlyName')}</span>
              <div className="lang-tabs" role="tablist" aria-label={t('overlay.langTabs')}>
                <button
                  type="button"
                  className={`lang-tab${lang === 'zh' ? ' active' : ''}`}
                  onClick={() => setLang('zh')}
                >
                  中文
                </button>
                <button
                  type="button"
                  className={`lang-tab${lang === 'en' ? ' active' : ''}`}
                  onClick={() => setLang('en')}
                >
                  EN
                </button>
              </div>
            </div>
            <div className="editable-name-row">
              <input
                className="editable-input"
                id="d-name-input"
                value={nameInput}
                onChange={(event) => setNameInput(event.target.value)}
                placeholder={t('overlay.friendlyInputPlaceholder')}
              />
              <button className="save-inline-btn" type="button" disabled={saving} onClick={() => void handleSaveName()}>
                {t('overlay.save')}
              </button>
            </div>
          </div>

          <div className="detail-field">
            <div className="detail-label">{t('overlay.category')}</div>
            <div id="d-cat">
              <span className={`cat-badge cat-color-${colorIndexForCategory(doc?.categoryPath || '')}`}>
                {iconForCategory(doc?.categoryPath || '')} {categoryValue.text || doc?.categoryPath || '-'}
              </span>
            </div>
          </div>

          <div className="detail-field">
            <div className="detail-label-row">
              <span className="detail-label">{t('overlay.summary')}</span>
              <button
                className={`regen-btn${regening ? ' spinning' : ''}`}
                id="regen-btn"
                type="button"
                disabled={regening || loading}
                onClick={() => void handleRegen()}
              >
                <span className="regen-icon">↺</span> {t('overlay.regen')}
              </button>
            </div>
            <div className={`summary-box${loading || regening ? ' loading' : ''}`} id="d-summary">
              {summaryValue.text || (loading ? t('overlay.loading') : '-')}
              {summaryValue.fallbackLabel ? <span className="lang-fallback">{summaryValue.fallbackLabel}</span> : null}
            </div>
            <div className="summary-status-row">
              <span className={`summary-quality state-${String(doc?.summaryQualityState || 'unknown')}`}>
                {t('overlay.qualityState')}: {qualityStateText(t, String(doc?.summaryQualityState || 'unknown'))}
              </span>
              {String(doc?.summaryLastError || '').trim() ? (
                <details className="summary-error-details">
                  <summary>{t('overlay.lastError')}</summary>
                  <div className="summary-error-text">{String(doc?.summaryLastError || '')}</div>
                </details>
              ) : null}
              {doc?.longdocMode === 'sampled' && doc.longdocPagesTotal != null && doc.longdocPagesUsed != null ? (
                <div className="doc-tracking-info longdoc-sampled">
                  {locale === 'zh-CN'
                    ? `长文档采样：使用了 ${doc.longdocPagesUsed} / ${doc.longdocPagesTotal} 页`
                    : `Long doc sampled: ${doc.longdocPagesUsed} / ${doc.longdocPagesTotal} pages used`}
                </div>
              ) : null}
              {doc?.ocrPagesTotal != null &&
              doc.ocrPagesProcessed != null &&
              doc.ocrPagesProcessed < doc.ocrPagesTotal ? (
                <div className="doc-tracking-info ocr-truncated">
                  {locale === 'zh-CN'
                    ? `⚠ OCR 仅处理了前 ${doc.ocrPagesProcessed} / ${doc.ocrPagesTotal} 页`
                    : `⚠ OCR limited to first ${doc.ocrPagesProcessed} of ${doc.ocrPagesTotal} pages`}
                </div>
              ) : null}
            </div>
          </div>

          <div className="detail-field">
            <div className="detail-label">{t('overlay.tags')}</div>
            <div className="detail-tags" id="d-tags">
              {(doc?.tags || []).map((tag) => (
                <span key={`${doc?.id}-${tag}`} className="tag">
                  {tag}
                </span>
              ))}
            </div>
          </div>

          <button
            className="view-btn"
            type="button"
            onClick={(event) => {
              event.preventDefault();
              if (!doc?.id) return;
              openViewer(doc.id);
            }}
          >
            📄 {t('overlay.viewDoc')}
          </button>
        </div>
      </div>
    </div>
  );
}
