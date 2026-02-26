'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {getKbClient} from '@src/lib/api/kb-client';
import type {SyncRunDetail, UiLocale} from '@src/lib/api/types';
import {useSyncViewer} from '@src/lib/ui-state/sync-viewer';

function humanSize(value: number, locale: UiLocale): string {
  const bytes = Number(value || 0);
  if (bytes <= 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB'];
  let val = bytes;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return `${new Intl.NumberFormat(locale === 'zh-CN' ? 'zh-CN' : 'en-AU', {maximumFractionDigits: 1}).format(val)} ${units[idx]}`;
}

function stageLabel(stage: string, t: ReturnType<typeof useTranslations>): string {
  const value = String(stage || '').trim().toLowerCase();
  if (value === 'discovered') return t('sync.stageDiscovered');
  if (value === 'queued') return t('sync.stageQueued');
  if (value === 'pending') return t('sync.stagePending');
  if (value === 'processing') return t('sync.stageProcessing');
  if (value === 'completed') return t('sync.stageCompleted');
  if (value === 'failed') return t('sync.stageFailed');
  if (value === 'duplicate') return t('sync.stageDuplicate');
  if (value === 'skipped') return t('sync.stageSkipped');
  return value || '-';
}

export function SyncTaskOverlay() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const {open, runId, closeSyncViewer} = useSyncViewer();
  const client = useMemo(() => getKbClient(), []);
  const [detail, setDetail] = useState<SyncRunDetail | null>(null);

  useEffect(() => {
    if (!open || !runId) return;
    let alive = true;
    let timer: number | null = null;

    const fetchRun = async () => {
      const out = await client.getSyncRun(runId);
      if (!alive) return;
      if (out) setDetail(out);
      if (!out || (out.status !== 'completed' && out.status !== 'failed')) {
        timer = window.setTimeout(fetchRun, 2000);
      }
    };

    setDetail(null);
    fetchRun();

    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [client, open, runId]);

  return (
    <div id="sync-overlay" className={`sync-overlay${open ? ' open' : ''}`} onClick={closeSyncViewer}>
      <div className="sync-panel" onClick={(event) => event.stopPropagation()}>
        <div className="sync-header">
          <h3>{t('sync.title')}</h3>
          <button type="button" className="sync-close" onClick={closeSyncViewer}>
            ×
          </button>
        </div>
        <div className="sync-body">
          {!detail ? (
            <div className="sync-loading">{t('sync.loading')}</div>
          ) : (
            <>
              <div className="sync-kpi-grid">
                <div className="sync-kpi-card">
                  <div className="sync-kpi-label">{t('sync.runStatus')}</div>
                  <div className={`sync-kpi-value state-${String(detail.status || '').toLowerCase()}`}>{detail.status}</div>
                </div>
                <div className="sync-kpi-card">
                  <div className="sync-kpi-label">{t('sync.totalFiles')}</div>
                  <div className="sync-kpi-value">{detail.summary.total}</div>
                </div>
                <div className="sync-kpi-card">
                  <div className="sync-kpi-label">{t('sync.stageProcessing')}</div>
                  <div className="sync-kpi-value">{detail.summary.processing}</div>
                </div>
                <div className="sync-kpi-card">
                  <div className="sync-kpi-label">{t('sync.stageCompleted')}</div>
                  <div className="sync-kpi-value">{detail.summary.completed}</div>
                </div>
              </div>
              <div className="sync-progress-wrap">
                <div className="sync-progress-head">
                  <span>{t('sync.progress')}</span>
                  <span>
                    {detail.summary.progressPct}% ({detail.summary.terminalCount}/{detail.summary.total || 0})
                  </span>
                </div>
                <div className="sync-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={detail.summary.progressPct}>
                  <div className="sync-progress-fill" style={{width: `${detail.summary.progressPct}%`}} />
                </div>
              </div>
              <div className="sync-summary-grid">
                <span>{t('sync.stageCompleted')}: {detail.summary.completed}</span>
                <span>{t('sync.stageProcessing')}: {detail.summary.processing}</span>
                <span>{t('sync.stagePending')}: {detail.summary.pending}</span>
                <span>{t('sync.stageFailed')}: {detail.summary.failed}</span>
                <span>{t('sync.stageDuplicate')}: {detail.summary.duplicate}</span>
                <span>{t('sync.stageSkipped')}: {detail.summary.skipped}</span>
              </div>
              <div className="sync-items-head">
                <span>{t('sync.file')}</span>
                <span>{t('sync.size')}</span>
                <span>{t('sync.stage')}</span>
                <span>{t('sync.updatedAt')}</span>
              </div>
              <ul className="sync-items-list">
                {detail.items.map((item) => (
                  <li key={item.itemId} className="sync-item-row">
                    <div className="sync-item-name">{item.fileName || '-'}</div>
                    <div className="sync-item-size">{humanSize(item.fileSize, locale)}</div>
                    <div className={`sync-item-stage stage-${String(item.stage || '').toLowerCase()}`}>{stageLabel(item.stage, t)}</div>
                    <div className="sync-item-updated">
                      {item.updatedAt
                        ? new Intl.DateTimeFormat(locale === 'zh-CN' ? 'zh-CN' : 'en-AU', {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit'
                          }).format(new Date(item.updatedAt))
                        : '-'}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default SyncTaskOverlay;
