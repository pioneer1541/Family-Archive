'use client';

import {useEffect, useState, useCallback} from 'react';
import {useTranslations, useLocale} from 'next-intl';
import {useRouter} from 'next/navigation';
import {getKbClient} from '@src/lib/api/kb-client';
import type {AppSettingItem, ConnectivityStatus, KeywordLists, OllamaModel} from '@src/lib/api/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type TabKey = 'llm' | 'storage' | 'mail' | 'timeout' | 'advanced' | 'keywords' | 'account';

// ---------------------------------------------------------------------------
// Helper components
// ---------------------------------------------------------------------------
function StatusBadge({ok, label}: {ok: boolean | null; label: string}) {
  if (ok === null) return null;
  return (
    <span className={`badge ${ok ? 'badge-green' : 'badge-red'}`}>{label}</span>
  );
}

function ModelSelect({
  label, settingKey, value, models, onChange
}: {
  label: string; settingKey: string; value: string;
  models: OllamaModel[]; onChange: (key: string, val: string) => void;
}) {
  const allOptions = models.length > 0
    ? models.map((m) => m.name)
    : (value ? [value] : []);
  return (
    <div className="settings-field">
      <label htmlFor={settingKey}>{label}</label>
      <select
        id={settingKey}
        value={value}
        onChange={(e) => onChange(settingKey, e.target.value)}
      >
        {allOptions.map((name) => (
          <option key={name} value={name}>{name}</option>
        ))}
        {value && !allOptions.includes(value) && (
          <option value={value}>{value}</option>
        )}
      </select>
    </div>
  );
}

function KeywordEditor({
  label, hint, terms, onChange
}: {
  label: string; hint: string; terms: Record<string, string>;
  onChange: (terms: Record<string, string>) => void;
}) {
  const [input, setInput] = useState('');
  const keys = Object.keys(terms);

  function handleAdd() {
    const val = input.trim().toLowerCase();
    if (!val || val in terms) { setInput(''); return; }
    onChange({...terms, [val]: val});
    setInput('');
  }

  function handleRemove(k: string) {
    const next = {...terms};
    delete next[k];
    onChange(next);
  }

  return (
    <div className="settings-field keyword-editor">
      <label>{label}</label>
      <p className="settings-hint">{hint}</p>
      <div className="keyword-tags">
        {keys.map((k) => (
          <span key={k} className="keyword-tag">
            {k}
            <button type="button" onClick={() => handleRemove(k)} aria-label={`Remove ${k}`}>×</button>
          </span>
        ))}
      </div>
      <div className="keyword-input-row">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAdd(); } }}
          placeholder={hint}
        />
        <button type="button" className="btn-secondary btn-sm" onClick={handleAdd}>+</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function SettingsPage() {
  const t = useTranslations('settings');
  const locale = useLocale();
  const router = useRouter();
  const isZh = locale === 'zh-CN';
  const client = getKbClient();

  const [tab, setTab] = useState<TabKey>('llm');
  const [items, setItems] = useState<AppSettingItem[]>([]);
  const [patch, setPatch] = useState<Record<string, string>>({});
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [connectivity, setConnectivity] = useState<ConnectivityStatus | null>(null);
  const [keywords, setKeywords] = useState<KeywordLists>({person_keywords: {}, pet_keywords: {}, location_keywords: {}});
  const [keywordsPatch, setKeywordsPatch] = useState<Partial<KeywordLists>>({});
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState('');
  const [restartRequired, setRestartRequired] = useState(false);
  const [restarting, setRestarting] = useState(false);
  // Account tab
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [pwError, setPwError] = useState('');

  useEffect(() => {
    client.getSettings?.().then(setItems).catch(() => {});
    client.getOllamaModels?.().then(setModels).catch(() => {});
    client.getKeywords?.().then(setKeywords).catch(() => {});
  }, []);

  function getVal(key: string): string {
    if (key in patch) return patch[key];
    return items.find((i) => i.key === key)?.value ?? '';
  }

  function setVal(key: string, val: string) {
    setPatch((prev) => ({...prev, [key]: val}));
  }

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      if (Object.keys(patch).length > 0) {
        const result = await fetch('/api/v1/settings', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(patch),
        }).then(r => r.json()).catch(() => ({}));
        if (result?.restart_required) {
          setRestartRequired(true);
        }
      }
      if (Object.keys(keywordsPatch).length > 0) await client.updateKeywords?.(keywordsPatch);
      setPatch({});
      setKeywordsPatch({});
      setToast(t('saved'));
      setTimeout(() => setToast(''), 3000);
    } catch {
      setToast(t('saveError'));
      setTimeout(() => setToast(''), 4000);
    } finally {
      setSaving(false);
    }
  }, [patch, keywordsPatch, client, t]);

  const handleRestart = useCallback(async () => {
    setRestarting(true);
    try {
      const result = await client.restartServices?.();
      if (result?.ok) {
        setRestartRequired(false);
        setToast(isZh ? '服务已重启' : 'Services restarted');
      } else {
        setToast(isZh ? '重启失败，请手动重启容器' : 'Restart failed, please restart container manually');
      }
      setTimeout(() => setToast(''), 4000);
    } catch {
      setToast(isZh ? '重启失败' : 'Restart failed');
      setTimeout(() => setToast(''), 4000);
    } finally {
      setRestarting(false);
    }
  }, [client, isZh]);

  async function handleTestConn() {
    try {
      const result = await client.getConnectivity?.();
      if (result) setConnectivity(result);
    } catch {}
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwError('');
    if (newPw.length < 8) {
      setPwError(isZh ? '新密码至少8个字符' : 'New password must be at least 8 characters');
      return;
    }
    if (newPw !== confirmPw) {
      setPwError(isZh ? '两次密码不一致' : 'Passwords do not match');
      return;
    }
    try {
      await client.changePassword?.(oldPw, newPw);
      setOldPw(''); setNewPw(''); setConfirmPw('');
      setToast(t('passwordChanged'));
      setTimeout(() => setToast(''), 3000);
    } catch (err: unknown) {
      setPwError(err instanceof Error ? err.message : t('passwordError'));
    }
  }

  async function handleLogout() {
    await client.authLogout?.();
    router.replace(`/${locale}/login`);
  }

  const MODEL_KEYS = ['summary_model', 'planner_model', 'synthesizer_model', 'embed_model', 'category_model', 'friendly_name_model', 'vl_extract_model'];
  const TIMEOUT_KEYS = ['summary_timeout_page_sec', 'summary_timeout_section_sec', 'summary_timeout_final_sec', 'agent_synth_timeout_sec'];

  const tabs: {key: TabKey; label: string}[] = [
    {key: 'llm', label: t('tabLlm')},
    {key: 'storage', label: t('tabStorage')},
    {key: 'mail', label: t('tabMail')},
    {key: 'timeout', label: t('tabTimeout')},
    {key: 'advanced', label: t('tabAdvanced')},
    {key: 'keywords', label: t('tabKeywords')},
    {key: 'account', label: t('tabAccount')},
  ];

  return (
    <div className="settings-page">
      <h1 className="settings-title">{t('pageTitle')}</h1>
      <div className="settings-layout">
        {/* Tab sidebar */}
        <nav className="settings-tabs">
          {tabs.map((tb) => (
            <button
              key={tb.key}
              type="button"
              className={`settings-tab${tab === tb.key ? ' active' : ''}`}
              onClick={() => setTab(tb.key)}
            >
              {tb.label}
            </button>
          ))}
        </nav>

        {/* Content */}
        <div className="settings-content">
          {/* LLM Models Tab */}
          {tab === 'llm' && (
            <div className="settings-section">
              {models.length === 0 && (
                <p className="settings-hint">{t('modelLoadError')}</p>
              )}
              {MODEL_KEYS.map((key) => {
                const meta = items.find((i) => i.key === key);
                const label = isZh ? (meta?.label_zh ?? key) : (meta?.label_en ?? key);
                return (
                  <ModelSelect
                    key={key}
                    label={label}
                    settingKey={key}
                    value={getVal(key)}
                    models={models}
                    onChange={setVal}
                  />
                );
              })}
            </div>
          )}

          {/* Storage Tab */}
          {tab === 'storage' && (
            <div className="settings-section">
              <div className="settings-field">
                <label>{t('nasAutoScan')}</label>
                <input
                  type="checkbox"
                  checked={getVal('nas_auto_scan_enabled') === '1' || getVal('nas_auto_scan_enabled') === 'true'}
                  onChange={(e) => setVal('nas_auto_scan_enabled', e.target.checked ? '1' : '0')}
                />
              </div>
              <div className="settings-field">
                <label>{t('nasDir')}</label>
                <input
                  type="text"
                  value={getVal('nas_default_source_dir')}
                  onChange={(e) => setVal('nas_default_source_dir', e.target.value)}
                />
              </div>
              <div className="settings-field">
                <label>{t('nasScanInterval')}</label>
                <input
                  type="number"
                  value={Math.round(parseInt(getVal('nas_scan_interval_sec') || '900') / 60)}
                  onChange={(e) => setVal('nas_scan_interval_sec', String(parseInt(e.target.value || '15') * 60))}
                  min={1}
                />
              </div>
              {connectivity && (
                <div className="connectivity-status">
                  <StatusBadge ok={connectivity.nas.ok} label={connectivity.nas.ok ? t('connOk') : t('connFail')} />
                  <span className="settings-hint">{`${t('nasPath')}: ${connectivity.nas.path || '-'}`}</span>
                  <span className="settings-hint">{`${t('nasReadable')}: ${connectivity.nas.readable ? t('yes') : t('no')}`}</span>
                  <span className="settings-hint">{`${t('nasWritable')}: ${connectivity.nas.writable ? t('yes') : t('no')}`}</span>
                  {!connectivity.nas.ok && connectivity.nas.error && (
                    <span className="settings-hint">{connectivity.nas.error}</span>
                  )}
                </div>
              )}
              <button type="button" className="btn-secondary" onClick={handleTestConn}>{t('testRw')}</button>
            </div>
          )}

          {/* Mail Tab */}
          {tab === 'mail' && (
            <div className="settings-section">
              <div className="settings-field">
                <label>{t('mailPoll')}</label>
                <input
                  type="checkbox"
                  checked={getVal('mail_poll_enabled') === '1' || getVal('mail_poll_enabled') === 'true'}
                  onChange={(e) => setVal('mail_poll_enabled', e.target.checked ? '1' : '0')}
                />
              </div>
              <div className="settings-field">
                <label>{t('mailInterval')}</label>
                <input
                  type="number"
                  value={Math.round(parseInt(getVal('mail_poll_interval_sec') || '300') / 60)}
                  onChange={(e) => setVal('mail_poll_interval_sec', String(parseInt(e.target.value || '5') * 60))}
                  min={1}
                />
              </div>
              <div className="settings-field">
                <label>{t('mailQuery')}</label>
                <input
                  type="text"
                  value={getVal('mail_query')}
                  onChange={(e) => setVal('mail_query', e.target.value)}
                />
              </div>
              {connectivity && (
                <div className="connectivity-status">
                  <StatusBadge ok={connectivity.gmail.ok} label={connectivity.gmail.ok ? t('connOk') : t('connFail')} />
                </div>
              )}
              <button type="button" className="btn-secondary" onClick={handleTestConn}>{t('testConn')}</button>
            </div>
          )}

          {/* Timeout Tab */}
          {tab === 'timeout' && (
            <div className="settings-section">
              {TIMEOUT_KEYS.map((key) => {
                const meta = items.find((i) => i.key === key);
                const label = isZh ? (meta?.label_zh ?? key) : (meta?.label_en ?? key);
                return (
                  <div key={key} className="settings-field">
                    <label>{label}</label>
                    <input
                      type="number"
                      value={getVal(key)}
                      onChange={(e) => setVal(key, e.target.value)}
                      min={5}
                    />
                  </div>
                );
              })}
            </div>
          )}

          {/* Advanced Tab */}
          {tab === 'advanced' && (
            <div className="settings-section">
              <div className="settings-field">
                <label>{t('ollamaUrl')}</label>
                <input
                  type="url"
                  value={getVal('ollama_base_url')}
                  onChange={(e) => setVal('ollama_base_url', e.target.value)}
                />
              </div>
              <div className="settings-field connectivity-row">
                <button type="button" className="btn-secondary" onClick={handleTestConn}>{t('testConn')}</button>
                {connectivity && (
                  <StatusBadge
                    ok={connectivity.ollama.ok}
                    label={connectivity.ollama.ok
                      ? `${t('connOk')} (${connectivity.ollama.model_count} models)`
                      : t('connFail')}
                  />
                )}
              </div>
            </div>
          )}

          {/* Keywords Tab */}
          {tab === 'keywords' && (
            <div className="settings-section">
              <p className="settings-hint">{t('keywordHint')}</p>
              <KeywordEditor
                label={t('keywordPersons')}
                hint={t('keywordPlaceholder')}
                terms={{...keywords.person_keywords, ...(keywordsPatch.person_keywords ?? {})}}
                onChange={(terms) => {
                  setKeywords((prev) => ({...prev, person_keywords: terms}));
                  setKeywordsPatch((prev) => ({...prev, person_keywords: terms}));
                }}
              />
              <KeywordEditor
                label={t('keywordPets')}
                hint={t('keywordPlaceholder')}
                terms={{...keywords.pet_keywords, ...(keywordsPatch.pet_keywords ?? {})}}
                onChange={(terms) => {
                  setKeywords((prev) => ({...prev, pet_keywords: terms}));
                  setKeywordsPatch((prev) => ({...prev, pet_keywords: terms}));
                }}
              />
              <KeywordEditor
                label={t('keywordLocations')}
                hint={t('keywordPlaceholder')}
                terms={{...keywords.location_keywords, ...(keywordsPatch.location_keywords ?? {})}}
                onChange={(terms) => {
                  setKeywords((prev) => ({...prev, location_keywords: terms}));
                  setKeywordsPatch((prev) => ({...prev, location_keywords: terms}));
                }}
              />
            </div>
          )}

          {/* Account Tab */}
          {tab === 'account' && (
            <div className="settings-section">
              <h3>{t('changePassword')}</h3>
              <form onSubmit={handleChangePassword} className="settings-form">
                <div className="settings-field">
                  <label>{t('oldPassword')}</label>
                  <input type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoComplete="current-password" required />
                </div>
                <div className="settings-field">
                  <label>{t('newPassword')}</label>
                  <input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" required />
                </div>
                <div className="settings-field">
                  <label>{t('confirmPassword')}</label>
                  <input type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} autoComplete="new-password" required />
                </div>
                {pwError && <p className="setup-error">{pwError}</p>}
                <button type="submit" className="btn-primary">{t('changePassword')}</button>
              </form>
              <hr className="settings-divider" />
              <button type="button" className="btn-secondary btn-danger" onClick={handleLogout}>
                {t('logout')}
              </button>
            </div>
          )}

          {/* Save/Reset buttons (except account tab) */}
          {tab !== 'account' && (
            <div className="settings-actions">
              <button type="button" className="btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? '…' : t('save')}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Toast */}
      {toast && <div className="settings-toast">{toast}</div>}
      
      {/* Restart Dialog */}
      {restartRequired && (
        <div className="settings-restart-dialog">
          <div className="settings-restart-content">
            <h3>{isZh ? '需要重启' : 'Restart Required'}</h3>
            <p>{isZh ? '模型设置已更改，需要重启服务才能生效。' : 'Model settings changed. A service restart is required for changes to take effect.'}</p>
            <div className="settings-restart-actions">
              <button 
                type="button" 
                className="btn-secondary" 
                onClick={() => setRestartRequired(false)}
              >
                {isZh ? '稍后' : 'Later'}
              </button>
              <button 
                type="button" 
                className="btn-primary" 
                onClick={handleRestart}
                disabled={restarting}
              >
                {restarting ? (isZh ? '重启中...' : 'Restarting...') : (isZh ? '立即重启' : 'Restart Now')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
