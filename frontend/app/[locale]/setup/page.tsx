'use client';

import {useState} from 'react';
import {useRouter} from 'next/navigation';
import {useLocale} from 'next-intl';
import {getKbClient} from '@src/lib/api/kb-client';

export default function SetupPage() {
  const locale = useLocale();
  const router = useRouter();
  const isZh = locale === 'zh-CN';

  const [step, setStep] = useState<'password' | 'ollama'>('password');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [ollamaUrl, setOllamaUrl] = useState('http://host.docker.internal:11434');
  const [testStatus, setTestStatus] = useState<'idle' | 'ok' | 'fail'>('idle');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const client = getKbClient();

  async function handleSetPassword(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (password.length < 8) {
      setError(isZh ? '密码至少需要8个字符' : 'Password must be at least 8 characters');
      return;
    }
    if (password !== confirm) {
      setError(isZh ? '两次输入的密码不一致' : 'Passwords do not match');
      return;
    }
    setSaving(true);
    try {
      await client.authSetup?.(password);
      setStep('ollama');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : (isZh ? '设置失败，请重试' : 'Setup failed, please try again'));
    } finally {
      setSaving(false);
    }
  }

  async function handleTestOllama() {
    setTestStatus('idle');
    try {
      const result = await client.getOllamaModels?.();
      setTestStatus(result && result.length >= 0 ? 'ok' : 'fail');
    } catch {
      setTestStatus('fail');
    }
  }

  async function handleFinish() {
    setSaving(true);
    try {
      if (ollamaUrl.trim()) {
        await client.updateSettings?.({ollama_base_url: ollamaUrl.trim()});
      }
      // Log in automatically after setup
      await client.authLogin?.(password);
      router.replace(`/${locale}/dashboard`);
    } catch {
      router.replace(`/${locale}/login`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="setup-page">
      <div className="setup-card">
        <div className="setup-header">
          <h1>{isZh ? '欢迎使用 Family Vault' : 'Welcome to Family Vault'}</h1>
          <p>{isZh ? '请完成初始设置' : 'Complete the initial setup to get started'}</p>
        </div>

        {step === 'password' && (
          <form onSubmit={handleSetPassword} className="setup-form">
            <h2>{isZh ? '第一步：设置访问密码' : 'Step 1: Set Access Password'}</h2>
            <p className="setup-hint">
              {isZh
                ? '密码将加密存储在本地数据库中，用于保护您的家庭档案'
                : 'Your password is stored encrypted in the local database to protect your family archive'}
            </p>
            <label>
              {isZh ? '密码' : 'Password'}
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isZh ? '至少8个字符' : 'At least 8 characters'}
                autoComplete="new-password"
                required
              />
            </label>
            <label>
              {isZh ? '确认密码' : 'Confirm Password'}
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder={isZh ? '再次输入密码' : 'Re-enter password'}
                autoComplete="new-password"
                required
              />
            </label>
            {error && <p className="setup-error">{error}</p>}
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? (isZh ? '保存中…' : 'Saving…') : (isZh ? '继续' : 'Continue')}
            </button>
          </form>
        )}

        {step === 'ollama' && (
          <div className="setup-form">
            <h2>{isZh ? '第二步：配置 Ollama（可跳过）' : 'Step 2: Configure Ollama (Optional)'}</h2>
            <p className="setup-hint">
              {isZh
                ? 'Ollama 是本地 AI 推理引擎，用于文档摘要和 AI 问答。'
                : 'Ollama is the local AI inference engine for document summarization and Q&A.'}
            </p>
            <label>
              {isZh ? 'Ollama 地址' : 'Ollama Base URL'}
              <input
                type="url"
                value={ollamaUrl}
                onChange={(e) => setOllamaUrl(e.target.value)}
                placeholder="http://host.docker.internal:11434"
              />
            </label>
            <div className="setup-row">
              <button type="button" className="btn-secondary" onClick={handleTestOllama}>
                {isZh ? '测试连接' : 'Test Connection'}
              </button>
              {testStatus === 'ok' && (
                <span className="badge-green">{isZh ? '连接成功' : 'Connected'}</span>
              )}
              {testStatus === 'fail' && (
                <span className="badge-red">{isZh ? '连接失败' : 'Connection failed'}</span>
              )}
            </div>
            <div className="setup-actions">
              <button type="button" className="btn-secondary" onClick={() => router.replace(`/${locale}/login`)}>
                {isZh ? '跳过' : 'Skip'}
              </button>
              <button type="button" className="btn-primary" onClick={handleFinish} disabled={saving}>
                {saving ? (isZh ? '完成中…' : 'Finishing…') : (isZh ? '完成设置' : 'Complete Setup')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
