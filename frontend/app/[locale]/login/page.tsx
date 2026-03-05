'use client';

import {useState} from 'react';
import {useRouter} from 'next/navigation';
import {useLocale} from 'next-intl';
import {getKbClient} from '@src/lib/api/kb-client';

export default function LoginPage() {
  const locale = useLocale();
  const router = useRouter();
  const isZh = locale === 'zh-CN';

  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const client = getKbClient();

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await client.authLogin?.(password);
      router.replace(`/${locale}/dashboard`);
    } catch (err: unknown) {
      setError(isZh ? '密码错误，请重试' : 'Incorrect password, please try again');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="setup-page">
      <div className="setup-card">
        <div className="setup-header">
          <h1>{isZh ? 'Family Vault' : 'Family Vault'}</h1>
          <p>{isZh ? '请输入密码以继续' : 'Enter your password to continue'}</p>
        </div>
        <form onSubmit={handleLogin} className="setup-form">
          <label>
            {isZh ? '密码' : 'Password'}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isZh ? '输入密码' : 'Enter password'}
              autoFocus
              autoComplete="current-password"
              required
            />
          </label>
          {error && <p className="setup-error">{error}</p>}
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? (isZh ? '验证中…' : 'Verifying…') : (isZh ? '登录' : 'Login')}
          </button>
        </form>
        <p className="setup-hint" style={{marginTop: '1rem', textAlign: 'center'}}>
          {isZh ? (
            <>
              忘记密码？请通过以下命令重置：<br/>
              <code style={{display: 'block', marginTop: '0.5rem', padding: '0.5rem', background: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '0.85rem'}}>
                docker exec -it fkv-api python -c "from app.auth import set_admin_password; from app.db import SessionLocal; db = SessionLocal(); set_admin_password('新密码', db); print('密码已重置')"
              </code>
            </>
          ) : (
            <>
              Forgot password? Reset with:<br/>
              <code style={{display: 'block', marginTop: '0.5rem', padding: '0.5rem', background: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '0.85rem'}}>
                docker exec -it fkv-api python -c "from app.auth import set_admin_password; from app.db import SessionLocal; db = SessionLocal(); set_admin_password('new_password', db); print('Password reset')"
              </code>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
