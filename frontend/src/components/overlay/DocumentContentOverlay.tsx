'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {getKbClient} from '@src/lib/api/kb-client';
import type {DocumentContentAvailability, KbDoc, UiLocale} from '@src/lib/api/types';
import {pickBilingualText} from '@src/lib/i18n/bilingual';
import {useContentViewer} from '@src/lib/ui-state/content-viewer';

const PREVIEWABLE_EXTENSIONS = new Set(['pdf', 'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff', 'heic']);

function canInlinePreview(doc: KbDoc | null): boolean {
  if (!doc) return false;
  const ext = String(doc.fileExt || '').trim().toLowerCase();
  if (!ext) return false;
  if (!PREVIEWABLE_EXTENSIONS.has(ext)) return false;
  return Boolean(String(doc.inlineUrl || '').trim());
}

function fallbackReasonText(t: (key: string) => string, detail: string): string {
  const code = String(detail || '').trim();
  if (code === 'source_file_missing') return t('contentOverlay.reasonSourceMissing');
  if (code === 'document_not_ready') return t('contentOverlay.reasonNotReady');
  if (code === 'unsupported_media_type') return t('contentOverlay.reasonUnsupported');
  if (code === 'availability_endpoint_missing') return t('contentOverlay.reasonAvailabilityEndpointMissing');
  if (code === 'availability_unreachable') return t('contentOverlay.reasonAvailabilityUnavailable');
  return t('contentOverlay.reasonGeneric');
}

function isAvailabilityProbeFailure(detail: string): boolean {
  const code = String(detail || '').trim();
  return code === 'availability_endpoint_missing' || code === 'availability_unreachable';
}

export function DocumentContentOverlay() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const client = useMemo(() => getKbClient(), []);
  const {open, docId, closeViewer} = useContentViewer();

  const [doc, setDoc] = useState<KbDoc | null>(null);
  const [loading, setLoading] = useState(false);
  const [frameFailed, setFrameFailed] = useState(false);
  const [availability, setAvailability] = useState<DocumentContentAvailability | null>(null);

  useEffect(() => {
    if (!open || !docId) return;
    let alive = true;
    setLoading(true);
    setDoc(null);
    setFrameFailed(false);
    setAvailability(null);

    Promise.allSettled([client.getDoc(docId, {includeChunks: true, chunkLimit: 50}), client.getContentAvailability(docId)])
      .then((results) => {
        if (!alive) return;

        const docResult = results[0];
        if (docResult && docResult.status === 'fulfilled') {
          setDoc(docResult.value);
        } else {
          setDoc(null);
        }

        const availabilityResult = results[1];
        if (availabilityResult && availabilityResult.status === 'fulfilled') {
          setAvailability(availabilityResult.value);
        } else {
          setAvailability({sourceAvailable: false, inlineSupported: false, detail: 'availability_unreachable'});
        }
      })
      .finally(() => {
        if (!alive) return;
        setLoading(false);
      });

    return () => {
      alive = false;
    };
  }, [client, docId, open]);

  const title = pickBilingualText(doc?.title || {zh: '', en: ''}, locale).text || doc?.fileName || '-';
  const availabilityDetail = String(availability?.detail || '').trim();
  const canShowInline = Boolean(availability?.sourceAvailable && availability?.inlineSupported);
  const allowInlineWhenProbeFails = isAvailabilityProbeFailure(availabilityDetail);
  const showFrame = canInlinePreview(doc) && !frameFailed && (canShowInline || allowInlineWhenProbeFails);
  const extracted = String(doc?.extractedText || '').trim();
  const fallbackReason = fallbackReasonText(t, availabilityDetail);

  return (
    <div
      id="content-overlay"
      className={`content-overlay${open ? ' open' : ''}`}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        closeViewer();
      }}
    >
      <div className="content-panel" onClick={(event) => event.stopPropagation()}>
        <div className="content-toolbar">
          <div className="content-title-wrap">
            <div className="content-title">{title}</div>
            <div className="content-filename mono">{doc?.fileName || '-'}</div>
          </div>
          <div className="content-actions">
            <a
              className="content-download-btn"
              href={doc?.downloadUrl || '#'}
              target="_blank"
              rel="noreferrer"
              onClick={(event) => {
                if (doc?.downloadUrl) return;
                event.preventDefault();
              }}
            >
              ⬇ {t('contentOverlay.download')}
            </a>
            <button className="content-close" type="button" onClick={closeViewer}>
              ✕
            </button>
          </div>
        </div>

        <div className="content-body">
          {loading ? (
            <div className="content-loading">{t('contentOverlay.loading')}</div>
          ) : showFrame ? (
            <iframe
              className="content-frame"
              src={doc?.inlineUrl || ''}
              title={t('contentOverlay.iframeTitle', {name: doc?.fileName || ''})}
              onError={() => setFrameFailed(true)}
            />
          ) : (
            <div className="text-fallback">
              <div className="text-fallback-title">{t('contentOverlay.fallbackTitle')}</div>
              <div className="text-fallback-reason">{fallbackReason}</div>
              {extracted ? (
                <pre className="text-fallback-pre">{extracted}</pre>
              ) : (
                <div className="text-fallback-empty">{t('contentOverlay.noExtractedText')}</div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
