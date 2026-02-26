'use client';

import {useEffect, useMemo, useState} from 'react';
import {useLocale, useTranslations} from 'next-intl';
import {getKbClient} from '@src/lib/api/kb-client';
import type {KbCategory, KbDoc, MailHealthResponse, UiLocale} from '@src/lib/api/types';
import {useRouter} from '@/i18n/navigation';
import {pickBilingualText} from '@src/lib/i18n/bilingual';
import {readCategoryAliasMap} from '@src/lib/ui-state/category-alias';
import {subscribeDocUpdated} from '@src/lib/ui-state/doc-events';
import {pushRecentAgentQuestion, readRecentAgentQuestions, subscribeRecentAgentQuestions} from '@src/lib/ui-state/recent-agent-questions';
import {useOverlay} from '@src/lib/ui-state/overlay';
import {useSyncViewer} from '@src/lib/ui-state/sync-viewer';
import {useToast} from '@src/lib/ui-state/toast';
import {useTopbar} from '@src/lib/ui-state/topbar';

function formatRecentDate(updatedAt: string, locale: UiLocale): string {
  const value = String(updatedAt || '').trim();
  if (!value) return '';
  try {
    const date = new Date(value);
    return new Intl.DateTimeFormat(locale === 'zh-CN' ? 'zh-CN' : 'en-AU', {
      month: 'numeric',
      day: 'numeric'
    }).format(date);
  } catch {
    return '';
  }
}

function categoryName(cat: KbCategory, locale: UiLocale, aliases: Record<string, string>): string {
  const alias = String(aliases[cat.path] || '').trim();
  if (alias) return alias;
  const selected = pickBilingualText(cat.label, locale).text;
  return selected || cat.path;
}

function DashboardView() {
  const t = useTranslations();
  const locale = useLocale() as UiLocale;
  const router = useRouter();
  const client = useMemo(() => getKbClient(), []);
  const {openOverlay} = useOverlay();
  const {openSyncViewer} = useSyncViewer();
  const {showToast} = useToast();
  const {setTopbar} = useTopbar();

  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [categories, setCategories] = useState<KbCategory[]>([]);
  const [aliases, setAliases] = useState<Record<string, string>>({});
  const [dashQuestion, setDashQuestion] = useState('');
  const [recentQuestions, setRecentQuestions] = useState<string[]>([]);
  const [syncState, setSyncState] = useState<'idle' | 'starting' | 'running' | 'finishing'>('idle');
  const [activeRunId, setActiveRunId] = useState('');
  const [lastSyncAt, setLastSyncAt] = useState('');
  const [mailHealth, setMailHealth] = useState<MailHealthResponse | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([client.getDocs(), client.getCategories()])
      .then(([docRows, categoryRows]) => {
        if (!alive) return;
        setDocs(docRows);
        setCategories(categoryRows);
      })
      .catch(() => {
        if (!alive) return;
        setDocs([]);
        setCategories([]);
      });

    client
      .getLastSync()
      .then((out) => {
        if (!alive) return;
        setLastSyncAt(String(out.lastSyncAt || ''));
      })
      .catch(() => {
        if (!alive) return;
        setLastSyncAt('');
      });

    if (client.getMailHealth) {
      client
        .getMailHealth()
        .then((out) => {
          if (!alive) return;
          setMailHealth(out);
        })
        .catch(() => {
          if (!alive) return;
        });
    }

    return () => {
      alive = false;
    };
  }, [client]);

  useEffect(() => {
    setAliases(readCategoryAliasMap());
  }, []);

  useEffect(() => {
    setRecentQuestions(readRecentAgentQuestions());
    return subscribeRecentAgentQuestions((rows) => {
      setRecentQuestions(rows.slice(0, 20));
    });
  }, []);

  useEffect(() => {
    return subscribeDocUpdated((doc) => {
      setDocs((prev) => prev.map((row) => (row.id === doc.id ? doc : row)));
    });
  }, []);

  const totalCount = docs.length > 0 ? docs.length : categories.reduce((sum, item) => sum + item.count, 0);

  useEffect(() => {
    setTopbar({
      title: t('nav.dashboard'),
      metaMode: 'locale_switch',
      count: 0,
      metaText: ''
    });
  }, [setTopbar, t]);

  useEffect(() => {
    if (syncState !== 'running' || !activeRunId) return;
    let timer: number | null = null;
    let alive = true;
    const poll = async () => {
      const detail = await client.getSyncRun(activeRunId);
      if (!alive) return;
      if (!detail) {
        timer = window.setTimeout(poll, 2000);
        return;
      }
      if (!detail.summary.isActive) {
        setSyncState('finishing');
        window.setTimeout(() => setSyncState('idle'), 500);
        const latest = await client.getLastSync().catch(() => null);
        if (latest) setLastSyncAt(String(latest.lastSyncAt || ''));
        showToast(detail.status === 'completed' ? t('sync.syncDone') : t('sync.syncFailed'));
        return;
      }
      timer = window.setTimeout(poll, 2000);
    };
    poll();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [activeRunId, client, showToast, syncState, t]);

  const recentDocs = useMemo(
    () => docs.slice().sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt))).slice(0, 5),
    [docs]
  );

  const topCategories = useMemo(() => categories.slice().sort((a, b) => b.count - a.count).slice(0, 6), [categories]);

  const jumpToAgentWithQuestion = (rawQuestion?: string) => {
    const question = String(rawQuestion ?? dashQuestion).trim();
    if (!question) return;
    pushRecentAgentQuestion(question, {source: 'dashboard'});
    const search = new URLSearchParams({
      ask: question,
      autostart: '1',
      src: 'dashboard'
    }).toString();
    router.push(`/agent?${search}`);
  };

  const quickPrompts = useMemo(() => {
    const defaults = [
      t('dashboard.quickRecentBills'),
      t('dashboard.quickInsurance'),
      t('dashboard.quickHealth'),
      t('dashboard.quickContractExpiry')
    ]
      .map((item) => String(item || '').trim())
      .filter(Boolean);
    const out: string[] = [];
    for (const row of recentQuestions) {
      const value = String(row || '').trim();
      if (!value) continue;
      out.push(value);
      if (out.length >= 4) break;
    }
    for (const row of defaults) {
      if (out.length >= 4) break;
      if (out.includes(row)) continue;
      out.push(row);
    }
    return out.slice(0, 4);
  }, [recentQuestions, t]);

  const formatLastSync = (value: string): string => {
    const raw = String(value || '').trim();
    if (!raw) return t('sync.neverSynced');
    try {
      return new Intl.DateTimeFormat(locale === 'zh-CN' ? 'zh-CN' : 'en-AU', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      }).format(new Date(raw));
    } catch {
      return raw;
    }
  };

  const handleStartSync = async () => {
    const busy = syncState !== 'idle';
    if (busy) {
      if (activeRunId) openSyncViewer(activeRunId);
      return;
    }
    setSyncState('starting');
    try {
      const out = await client.startSync();
      const runId = String(out.runId || '');
      if (!runId) {
        setSyncState('idle');
        showToast(t('sync.syncFailed'));
        return;
      }
      setActiveRunId(runId);
      setSyncState('running');
      const latest = await client.getLastSync().catch(() => null);
      if (latest) setLastSyncAt(String(latest.lastSyncAt || ''));
    } catch {
      setSyncState('idle');
      showToast(t('sync.syncFailed'));
    }
  };

  const syncing = syncState !== 'idle';

  return (
    <div className="view active" id="view-dashboard">
      <div className="dash-agent-card">
        <div className="dash-agent-left">
          <div className="dash-agent-icon">✦</div>
          <div>
            <div className="dash-agent-title">{t('dashboard.agentCardTitle')}</div>
            <div className="dash-agent-sub">{t('dashboard.agentCardSub')}</div>
          </div>
        </div>
        <div className="dash-agent-input-wrap">
          <input
            className="dash-agent-input"
            id="dash-agent-input"
            type="text"
            value={dashQuestion}
            placeholder={t('dashboard.agentInputPlaceholder')}
            onChange={(event) => setDashQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== 'Enter') return;
              event.preventDefault();
              jumpToAgentWithQuestion();
            }}
          />
          <button
            className="dash-agent-btn"
            type="button"
            onClick={() => jumpToAgentWithQuestion()}
            disabled={!String(dashQuestion || '').trim()}
          >
            {t('dashboard.agentAskBtn')}
          </button>
        </div>
        <div className="dash-agent-chips">
          {quickPrompts.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="dash-agent-chip"
              onClick={() => {
                setDashQuestion(prompt);
                jumpToAgentWithQuestion(prompt);
              }}
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>
      {mailHealth && mailHealth.enabled && mailHealth.status !== 'ok' ? (
        <div className="mail-health-alert" role="alert">
          <span className="mail-health-icon">⚠</span>
          <span className="mail-health-text">
            {locale === 'zh-CN' ? `Gmail 集成异常：${mailHealth.detail}` : `Gmail integration error: ${mailHealth.detail}`}
          </span>
        </div>
      ) : null}
      <div className="dash-grid">
        <div className="card">
          <div className="card-title-row">
            <div className="card-title">{t('dashboard.recent')}</div>
            <div className="sync-quick-tools">
              <span className="sync-last-time">
                {t('sync.lastSync')}: {formatLastSync(lastSyncAt)}
              </span>
              <button
                type="button"
                className={`sync-now-btn${syncing ? ' syncing' : ''}${syncState === 'starting' ? ' starting' : ''}`}
                onClick={handleStartSync}
              >
                {syncing ? t('sync.syncingNow') : t('sync.syncNow')}
              </button>
            </div>
          </div>
          <ul className="recent-list" id="recent-list">
            {recentDocs.map((doc) => {
              const matched = categories.find((item) => item.path === doc.categoryPath);
              const label = matched ? categoryName(matched, locale, aliases) : doc.categoryPath;
              const icon = matched?.icon || '📄';
              const title = pickBilingualText(doc.title, locale);
              return (
                <li key={doc.id} className="recent-item" onClick={() => openOverlay(doc.id)}>
                  <div className="file-icon">{icon}</div>
                  <div className="recent-info">
                    <div className="recent-name">
                      {title.text || doc.fileName}
                      {title.fallbackLabel ? <span className="lang-fallback">{title.fallbackLabel}</span> : null}
                    </div>
                    <div className="recent-meta">{label}</div>
                  </div>
                  <div className="recent-date">{formatRecentDate(doc.updatedAt, locale)}</div>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="card">
          <div className="card-title">{t('dashboard.stats')}</div>
          <div className="stats-summary">
            <span className="stats-total" id="total-count">
              {totalCount}
            </span>
            <span className="stats-label">{t('dashboard.docsUnit')}</span>
          </div>

          <div className="cat-bars" id="cat-bars">
            {topCategories.map((cat) => {
              const pct = totalCount <= 0 ? 0 : Math.round((cat.count / totalCount) * 100);
              return (
                <div
                  key={cat.id}
                  className="cat-bar clickable"
                  role="button"
                  tabIndex={0}
                  aria-label={`${categoryName(cat, locale, aliases)} (${cat.count})`}
                  onClick={() => router.push(`/cats/${cat.id}`)}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return;
                    event.preventDefault();
                    router.push(`/cats/${cat.id}`);
                  }}
                >
                  <div className="cat-bar-header">
                    <span className="cat-bar-name">
                      {cat.icon} {categoryName(cat, locale, aliases)}
                    </span>
                    <span className="cat-bar-count">
                      {cat.count} {t('dashboard.docsUnit')}
                    </span>
                  </div>
                  <div className="cat-bar-track">
                    <div className={`cat-bar-fill bar-color-${cat.colorIndex}`} style={{width: `${pct}%`}} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

export {DashboardView};
export default DashboardView;
