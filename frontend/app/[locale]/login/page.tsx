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
          {isZh
            ? '忘记密码？在容器内运行 reset 命令重置。'
            : 'Forgot password? Run the reset command inside the container.'}
        </p>
      </div>
    </div>
  );
}
