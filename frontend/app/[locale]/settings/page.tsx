'use client';

import {useEffect, useState, useCallback, useRef} from 'react';
import {useTranslations, useLocale} from 'next-intl';
import {useRouter, useSearchParams, usePathname} from 'next/navigation';
import {getKbClient} from '@src/lib/api/kb-client';
import type {
  AppSettingItem,
  ConnectivityStatus,
  KeywordLists,
  OllamaModel,
  GmailCredential,
  GmailCredentialCreate,
  GmailCredentialUpdate,
  UserResponse,
} from '@src/lib/api/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type TabKey = 'llm' | 'storage' | 'mail' | 'keywords' | 'account' | 'users';
const RESTART_REQUIRED_KEYS = new Set([
  'planner_model',
  'synthesizer_model',
  'embed_model',
  'summary_model',
  'category_model',
  'friendly_name_model',
  'vl_extract_model',
  'summary_timeout_page_sec',
  'summary_timeout_section_sec',
  'summary_timeout_final_sec',
  'agent_synth_timeout_sec',
  'ollama_base_url',
]);

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

// 脱敏显示 client_id (只显示前8位和后4位)
function maskClientId(clientId: string): string {
  if (!clientId || clientId.length <= 12) return '****';
  return clientId.substring(0, 8) + '****' + clientId.substring(clientId.length - 4);
}

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
        <button type="button" className="btn-secondary" onClick={handleAdd}>+</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export default function SettingsPage() {
  const t = useTranslations('settings');
  const tg = useTranslations('gmail');
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
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
  const [me, setMe] = useState<UserResponse | null>(null);
  // Account tab
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [pwError, setPwError] = useState('');
  // Gmail credentials
  const [gmailCreds, setGmailCreds] = useState<GmailCredential[]>([]);
  const [gmailLoading, setGmailLoading] = useState(false);
  const [gmailFormOpen, setGmailFormOpen] = useState(false);
  const [gmailGuideOpen, setGmailGuideOpen] = useState(false);
  const [gmailForm, setGmailForm] = useState<GmailCredentialCreate>({name: "", client_id: "", client_secret: ""});
  const [gmailEditId, setGmailEditId] = useState<string | null>(null);
  const [gmailError, setGmailError] = useState("");
  const [gmailSaving, setGmailSaving] = useState(false);
  const [gmailAuthorizingId, setGmailAuthorizingId] = useState<string | null>(null);
  // Gmail 删除确认
  const [gmailDeleteId, setGmailDeleteId] = useState<string | null>(null);
  const authPollingRef = useRef<number | null>(null);
  const oauthNoticeHandledRef = useRef(false);
  const [originUrl, setOriginUrl] = useState('');
  // Admin users tab
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [userCreateName, setUserCreateName] = useState('');
  const [userCreatePassword, setUserCreatePassword] = useState('');
  const [userCreateError, setUserCreateError] = useState('');
  const localDirPickerRef = useRef<HTMLInputElement | null>(null);
  const isAdmin = me?.role === 'admin';

  useEffect(() => {
    client.getSettings?.().then(setItems).catch(() => {});
    client.getOllamaModels?.().then(setModels).catch(() => {});
    client.getKeywords?.().then(setKeywords).catch(() => {});
    client.getMe?.().then((u) => setMe(u ?? null)).catch(() => setMe(null));
    loadGmailCredentials();
  }, []);

  useEffect(() => {
    const connected = searchParams.get('gmail_connected');
    const oauthError = searchParams.get('gmail_error');
    if (oauthNoticeHandledRef.current) return;
    if (connected === '1') {
      oauthNoticeHandledRef.current = true;
      setToast(tg('gmail_connected'));
      setTimeout(() => setToast(''), 3000);
      router.replace(pathname);
      return;
    }
    if (oauthError) {
      oauthNoticeHandledRef.current = true;
      setToast(`${tg('gmail_error')}: ${oauthError}`);
      setTimeout(() => setToast(''), 5000);
      router.replace(pathname);
    }
  }, [pathname, router, searchParams, tg]);

  useEffect(() => {
    setOriginUrl(window.location.origin);
  }, []);

  // 加载 Gmail 凭证列表

  // 滚动锁定：弹出框打开时锁定 body 滚动
  useEffect(() => {
    if (restartRequired) {
      const scrollY = window.scrollY;
      document.body.classList.add("overlay-open");
      document.body.style.top = `-${scrollY}px`;
      return () => {
        document.body.classList.remove("overlay-open");
        document.body.style.top = "";
        window.scrollTo(0, scrollY);
      };
    }
  }, [restartRequired]);
  async function loadGmailCredentials() {
    setGmailLoading(true);
    try {
      const creds = await client.getGmailCredentials?.() ?? [];
      setGmailCreds(creds);
    } catch {
      setGmailCreds([]);
    } finally {
      setGmailLoading(false);
    }
  }

  const loadUsers = useCallback(async () => {
    if (!isAdmin) return;
    setUsersLoading(true);
    try {
      const result = await client.listUsers?.();
      setUsers(result?.items ?? []);
    } catch {
      setUsers([]);
    } finally {
      setUsersLoading(false);
    }
  }, [isAdmin, client]);

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
        const r = await fetch('/api/v1/settings', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(patch),
        });
        const result = await r.json().catch(() => ({}));
        if (!r.ok) {
          throw new Error((result as {detail?: string})?.detail || 'Settings update failed');
        }
        const hasRestartKey = Object.keys(patch).some((key) => RESTART_REQUIRED_KEYS.has(key));
        if (result?.restart_required || hasRestartKey) {
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
      const fallbackSuccess = isZh ? '服务已重启' : 'Services restarted';
      const fallbackFail = isZh ? '重启失败，请手动重启容器' : 'Restart failed, please restart container manually';
      const toastMessage = (result?.message || result?.error || '').trim();
      if (result?.ok) {
        setRestartRequired(false);
        setToast(toastMessage || fallbackSuccess);
      } else {
        setToast(toastMessage || fallbackFail);
      }
      setTimeout(() => setToast(''), toastMessage.length > 120 ? 12000 : 6000);
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

  async function handleCreateUser(e: React.FormEvent) {
    e.preventDefault();
    setUserCreateError('');
    if (userCreateName.trim().length < 3) {
      setUserCreateError(t('userUsernameMin'));
      return;
    }
    if (userCreatePassword.length < 8) {
      setUserCreateError(t('userPasswordMin'));
      return;
    }
    try {
      await client.createUser?.({username: userCreateName.trim(), password: userCreatePassword, role: 'user'});
      setUserCreateName('');
      setUserCreatePassword('');
      setToast(t('userCreated'));
      setTimeout(() => setToast(''), 3000);
      await loadUsers();
    } catch (err: unknown) {
      setUserCreateError(err instanceof Error ? err.message : t('userCreateError'));
    }
  }

  async function handleDeleteUser(id: string) {
    try {
      await client.deleteUser?.(id);
      setToast(t('userDeleted'));
      setTimeout(() => setToast(''), 3000);
      await loadUsers();
    } catch (err: unknown) {
      setToast(err instanceof Error ? err.message : t('userDeleteError'));
      setTimeout(() => setToast(''), 4000);
    }
  }

  // Gmail 凭证操作
  async function handleGmailSubmit(e: React.FormEvent) {
    e.preventDefault();
    setGmailError("");
    
    // 验证表单
    if (!gmailForm.name.trim()) {
      setGmailError(tg('validationName'));
      return;
    }
    if (!gmailForm.client_id.trim()) {
      setGmailError(tg('validationClientId'));
      return;
    }
    if (!gmailEditId && !gmailForm.client_secret.trim()) {
      setGmailError(tg('validationClientSecret'));
      return;
    }

    try {
      setGmailSaving(true);
      if (gmailEditId) {
        const updateData: GmailCredentialUpdate = {
          name: gmailForm.name,
          client_id: gmailForm.client_id,
        };
        if (gmailForm.client_secret.trim()) {
          updateData.client_secret = gmailForm.client_secret.trim();
        }
        await client.updateGmailCredential?.(gmailEditId, updateData);
        setToast(tg('toastUpdated'));
      } else {
        await client.createGmailCredential?.(gmailForm);
        setToast(tg('toastAdded'));
      }
      setGmailFormOpen(false);
      setGmailEditId(null);
      setGmailForm({name: "", client_id: "", client_secret: ""});
      loadGmailCredentials();
      setTimeout(() => setToast(''), 3000);
    } catch (err) {
      setGmailError(err instanceof Error ? err.message : tg('toastFailed'));
    } finally {
      setGmailSaving(false);
    }
  }

  // 打开编辑表单
  function handleGmailEdit(cred: GmailCredential) {
    setGmailEditId(cred.id);
    setGmailForm({
      name: cred.name,
      client_id: cred.client_id,
      client_secret: "",
    });
    setGmailFormOpen(true);
    setGmailError("");
  }

  // 确认删除
  async function handleGmailDelete() {
    if (!gmailDeleteId) return;
    try {
      await client.deleteGmailCredential?.(gmailDeleteId);
      setToast(tg('toastDeleted'));
      setGmailDeleteId(null);
      loadGmailCredentials();
      setTimeout(() => setToast(''), 3000);
    } catch (err) {
      setToast(err instanceof Error ? err.message : tg('deleteFailed'));
      setTimeout(() => setToast(''), 4000);
    }
  }

  // 取消表单
  function handleGmailCancel() {
    setGmailFormOpen(false);
    setGmailEditId(null);
    setGmailForm({name: "", client_id: "", client_secret: ""});
    setGmailError("");
  }

  function handleGmailCreate() {
    setGmailEditId(null);
    setGmailForm({name: "", client_id: "", client_secret: ""});
    setGmailError("");
    setGmailFormOpen(true);
  }

  function handleGmailGuideClose() {
    setGmailGuideOpen(false);
  }

  async function handleGmailAuthorize(credId: string) {
    if (!client.getGmailAuthUrl) return;
    setGmailAuthorizingId(credId);
    try {
      const result = await client.getGmailAuthUrl(credId, window.location.origin);
      const authWindow = window.open(result.auth_url, '_blank', 'noopener,noreferrer');
      if (!authWindow) {
        throw new Error(tg('toastFailed'));
      }
      if (authPollingRef.current !== null) {
        window.clearInterval(authPollingRef.current);
      }
      authPollingRef.current = window.setInterval(() => {
        if (!authWindow.closed) return;
        if (authPollingRef.current !== null) {
          window.clearInterval(authPollingRef.current);
          authPollingRef.current = null;
        }
        setGmailAuthorizingId(null);
        loadGmailCredentials();
      }, 800);
    } catch (err) {
      setGmailAuthorizingId(null);
      setToast(err instanceof Error ? err.message : tg('toastFailed'));
      setTimeout(() => setToast(''), 4000);
    }
  }

  useEffect(() => {
    return () => {
      if (authPollingRef.current !== null) {
        window.clearInterval(authPollingRef.current);
      }
    };
  }, []);

  const gmailDeleteTarget = gmailCreds.find((cred) => cred.id === gmailDeleteId) ?? null;

  const MODEL_KEYS = ['summary_model', 'planner_model', 'synthesizer_model', 'embed_model', 'category_model', 'friendly_name_model', 'vl_extract_model'];
  const TIMEOUT_KEYS = ['summary_timeout_page_sec', 'summary_timeout_section_sec', 'summary_timeout_final_sec', 'agent_synth_timeout_sec'];

  const tabs: {key: TabKey; label: string}[] = [
    {key: 'llm', label: t('tabLlm')},
    {key: 'storage', label: t('tabStorage')},
    {key: 'mail', label: t('tabMail')},
    {key: 'keywords', label: t('tabKeywords')},
    {key: 'account', label: t('tabAccount')},
    ...(isAdmin ? [{key: 'users' as TabKey, label: t('tabUsers')}] : []),
  ];

  function handleBrowseLocalFolder() {
    localDirPickerRef.current?.click();
  }

  function handleLocalFolderSelected(event: React.ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    const first = files && files.length > 0 ? files[0] : null;
    const relative = String((first as File & {webkitRelativePath?: string} | null)?.webkitRelativePath || '');
    const folderName = relative.split('/').filter(Boolean)[0] || '';
    if (folderName) {
      const current = String(getVal('local_source_dir') || '').trim();
      const guessed = current
        ? `${current.replace(/[\\/]+$/, '')}/${folderName}`
        : folderName;
      setVal('local_source_dir', guessed);
      setToast(t('localBrowseHint'));
      setTimeout(() => setToast(''), 3500);
    } else {
      setToast(t('localBrowseUnavailable'));
      setTimeout(() => setToast(''), 3500);
    }
    event.target.value = '';
  }

  useEffect(() => {
    if (tab === 'users' && isAdmin) {
      loadUsers();
    }
  }, [tab, isAdmin, loadUsers]);

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
              <hr className="settings-divider" />
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
              <hr className="settings-divider" />
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

          {/* Storage Tab */}
          {tab === 'storage' && (
            <div className="settings-section">
              <div className="settings-field">
                <label>{t('sourceType')}</label>
                <select
                  value={(getVal('source_type') || 'local').toLowerCase() === 'nas' ? 'nas' : 'local'}
                  onChange={(e) => setVal('source_type', e.target.value)}
                >
                  <option value="local">{t('sourceTypeLocal')}</option>
                  <option value="nas">{t('sourceTypeNas')}</option>
                </select>
              </div>
              {(getVal('source_type') || 'local').toLowerCase() === 'local' ? (
                <div className="settings-field">
                  <label>{t('localSourceDir')}</label>
                  <div className="settings-input-row">
                    <input
                      type="text"
                      value={getVal('local_source_dir')}
                      onChange={(e) => setVal('local_source_dir', e.target.value)}
                    />
                    <button type="button" className="btn-secondary" onClick={handleBrowseLocalFolder}>
                      {t('browse')}
                    </button>
                    <input
                      ref={localDirPickerRef}
                      type="file"
                      style={{display: 'none'}}
                      onChange={handleLocalFolderSelected}
                      multiple
                      {...({'webkitdirectory': ''} as React.InputHTMLAttributes<HTMLInputElement>)}
                    />
                  </div>
                  <p className="settings-hint">{t('localBrowseHint')}</p>
                </div>
              ) : (
                <>
                  <div className="settings-field">
                    <label>{t('nasHost')}</label>
                    <input
                      type="text"
                      value={getVal('nas_host')}
                      onChange={(e) => setVal('nas_host', e.target.value)}
                    />
                  </div>
                  <div className="settings-field">
                    <label>{t('nasSharePath')}</label>
                    <input
                      type="text"
                      value={getVal('nas_path')}
                      onChange={(e) => setVal('nas_path', e.target.value)}
                    />
                  </div>
                </>
              )}
              <div className="settings-field">
                <label>{t('nasAutoScan')}</label>
                <input
                  type="checkbox"
                  checked={getVal('nas_auto_scan_enabled') === '1' || getVal('nas_auto_scan_enabled') === 'true'}
                  onChange={(e) => setVal('nas_auto_scan_enabled', e.target.checked ? '1' : '0')}
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
              <h3>{t('mailPollingSection')}</h3>
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
              <hr className="settings-divider" />
              <div className="settings-section-header">
                <h3>{tg('credentials')}</h3>
                <div className="settings-section-header-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setGmailGuideOpen(true)}
                  >
                    {tg('gmail_config_guide')}
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={handleGmailCreate}
                    disabled={gmailFormOpen}
                  >
                    {tg('add')}
                  </button>
                </div>
              </div>
              <p className="settings-hint">{tg('hint')}</p>

              {gmailLoading ? (
                <p className="settings-hint">{t('loading')}</p>
              ) : gmailCreds.length === 0 ? (
                <p className="settings-hint">{tg('empty')}</p>
              ) : (
                <div className="gmail-cred-table-wrap">
                  <table className="gmail-cred-table">
                    <thead>
                      <tr>
                        <th>{tg('tableName')}</th>
                        <th>{tg('tableClientId')}</th>
                        <th>{tg('tableCreatedAt')}</th>
                        <th>{tg('tableUpdatedAt')}</th>
                        <th>{tg('tableActions')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {gmailCreds.map((cred) => (
                        <tr key={cred.id}>
                          <td>{cred.name}</td>
                          <td className="gmail-cred-mono">{maskClientId(cred.client_id)}</td>
                          <td>{new Date(cred.created_at).toLocaleDateString(locale)}</td>
                          <td>{new Date(cred.updated_at).toLocaleDateString(locale)}</td>
                          <td className="gmail-cred-actions">
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => handleGmailAuthorize(cred.id)}
                              disabled={gmailAuthorizingId === cred.id}
                            >
                              {gmailAuthorizingId === cred.id ? t('loading') : tg('authorize')}
                            </button>
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => handleGmailEdit(cred)}
                            >
                              {t('edit')}
                            </button>
                            <button
                              type="button"
                              className="btn-secondary btn-danger"
                              onClick={() => setGmailDeleteId(cred.id)}
                            >
                              {t('delete')}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <hr className="settings-divider" />
              <h3>{t('mailConnectionSection')}</h3>
              {connectivity && (
                <div className="connectivity-status">
                  <StatusBadge ok={connectivity.gmail.ok} label={connectivity.gmail.ok ? t('connOk') : t('connFail')} />
                </div>
              )}
              <button type="button" className="btn-secondary" onClick={handleTestConn}>{t('testConn')}</button>
            </div>
          )}

          {/* Gmail Form Modal */}
          {gmailGuideOpen && (
            <div className="settings-restart-dialog" onClick={handleGmailGuideClose}>
              <div className="settings-restart-content gmail-modal-content" onClick={(e) => e.stopPropagation()}>
                <h3>{tg('gmail_config_guide')}</h3>
                <ol className="gmail-guide-steps">
                  <li>
                    <h4>{tg('gmail_step1_title')}</h4>
                    <p className="settings-hint">{tg('gmail_step1')}</p>
                    <a
                      href="https://console.cloud.google.com/"
                      target="_blank"
                      rel="noreferrer"
                      className="gmail-guide-link"
                    >
                      https://console.cloud.google.com/
                    </a>
                  </li>
                  <li>
                    <h4>{tg('gmail_step2_title')}</h4>
                    <p className="settings-hint">{tg('gmail_step2')}</p>
                    <a
                      href="https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                      target="_blank"
                      rel="noreferrer"
                      className="gmail-guide-link"
                    >
                      https://console.cloud.google.com/apis/library/gmail.googleapis.com
                    </a>
                  </li>
                  <li>
                    <h4>{tg('gmail_step3_title')}</h4>
                    <p className="settings-hint">{tg('gmail_step3')}</p>
                    <ul className="gmail-guide-substeps">
                      <li>{tg('gmail_step3_sub1')}</li>
                      <li>{tg('gmail_step3_sub2')}</li>
                      <li>
                        {tg('gmail_step3_sub3')}
                        <code className="gmail-guide-code">{`${originUrl || 'https://your-domain.com'}/gmail/callback`}</code>
                      </li>
                    </ul>
                    <a
                      href="https://console.cloud.google.com/apis/credentials"
                      target="_blank"
                      rel="noreferrer"
                      className="gmail-guide-link"
                    >
                      https://console.cloud.google.com/apis/credentials
                    </a>
                  </li>
                  <li>
                    <h4>{tg('gmail_step4_title')}</h4>
                    <p className="settings-hint">{tg('gmail_step4')}</p>
                  </li>
                </ol>
                <div className="settings-restart-actions">
                  <button type="button" className="btn-secondary" onClick={handleGmailGuideClose}>
                    {t('cancel')}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Gmail Form Modal */}
          {gmailFormOpen && (
            <div className="settings-restart-dialog" onClick={handleGmailCancel}>
              <div className="settings-restart-content gmail-modal-content" onClick={(e) => e.stopPropagation()}>
                <h3>{gmailEditId ? tg('modalEditTitle') : tg('modalCreateTitle')}</h3>
                <form onSubmit={handleGmailSubmit} className="settings-form gmail-cred-form">
                  <div className="settings-field">
                    <label>{tg('name')}</label>
                    <input
                      type="text"
                      value={gmailForm.name}
                      onChange={(e) => setGmailForm({...gmailForm, name: e.target.value})}
                      placeholder={tg('namePlaceholder')}
                      required
                    />
                  </div>
                  <div className="settings-field">
                    <label>{tg('clientId')}</label>
                    <input
                      type="text"
                      value={gmailForm.client_id}
                      onChange={(e) => setGmailForm({...gmailForm, client_id: e.target.value})}
                      placeholder={tg('clientIdPlaceholder')}
                      required
                    />
                  </div>
                  <div className="settings-field">
                    <label>{tg('clientSecret')}</label>
                    <input
                      type="password"
                      value={gmailForm.client_secret}
                      onChange={(e) => setGmailForm({...gmailForm, client_secret: e.target.value})}
                      placeholder={tg('clientSecretPlaceholder')}
                      required={!gmailEditId}
                    />
                    {gmailEditId && (
                      <p className="settings-hint">{tg('secretHint')}</p>
                    )}
                  </div>
                  {gmailError && <p className="setup-error">{gmailError}</p>}
                  <div className="settings-form-actions">
                    <button type="button" className="btn-secondary" onClick={handleGmailCancel}>
                      {t('cancel')}
                    </button>
                    <button type="submit" className="btn-primary" disabled={gmailSaving}>
                      {gmailSaving
                        ? t('loading')
                        : (gmailEditId ? tg('update') : tg('create'))}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

          {/* Delete Confirmation */}
          {gmailDeleteId && (
            <div className="settings-restart-dialog">
              <div className="settings-restart-content">
                <h3>{tg('deleteConfirm')}</h3>
                <p>{tg('deleteHint')}</p>
                {gmailDeleteTarget && (
                  <p className="settings-hint">{`${tg('name')}: ${gmailDeleteTarget.name}`}</p>
                )}
                <div className="settings-restart-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setGmailDeleteId(null)}
                  >
                    {t('cancel')}
                  </button>
                  <button
                    type="button"
                    className="btn-primary btn-danger"
                    onClick={handleGmailDelete}
                  >
                    {tg('delete')}
                  </button>
                </div>
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

          {tab === 'users' && (
            <div className="settings-section">
              <h3>{t('userManagement')}</h3>
              <form onSubmit={handleCreateUser} className="settings-form">
                <div className="settings-field">
                  <label>{t('username')}</label>
                  <input
                    type="text"
                    value={userCreateName}
                    onChange={(e) => setUserCreateName(e.target.value)}
                    autoComplete="username"
                    required
                  />
                </div>
                <div className="settings-field">
                  <label>{t('newPassword')}</label>
                  <input
                    type="password"
                    value={userCreatePassword}
                    onChange={(e) => setUserCreatePassword(e.target.value)}
                    autoComplete="new-password"
                    required
                  />
                </div>
                {userCreateError && <p className="setup-error">{userCreateError}</p>}
                <button type="submit" className="btn-primary">{t('createUser')}</button>
              </form>
              <hr className="settings-divider" />
              {usersLoading ? (
                <p className="settings-hint">{t('loading')}</p>
              ) : users.length === 0 ? (
                <p className="settings-hint">{t('usersEmpty')}</p>
              ) : (
                <div className="users-table-wrap">
                  <table className="users-table">
                    <thead>
                      <tr>
                        <th>{t('username')}</th>
                        <th>{t('role')}</th>
                        <th>{t('createdAt')}</th>
                        <th>{t('actions')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.id}>
                          <td>{u.username}</td>
                          <td>{u.role || 'user'}</td>
                          <td>{new Date(u.created_at).toLocaleDateString(locale)}</td>
                          <td>
                            <button
                              type="button"
                              className="btn-secondary btn-danger"
                              onClick={() => handleDeleteUser(u.id)}
                              disabled={u.id === me?.id}
                            >
                              {t('delete')}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Save button (except account tab) */}
          {tab !== 'account' && tab !== 'users' && (
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
