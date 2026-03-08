"use client";

import {useState} from "react";
import {useRouter} from "next/navigation";
import {useLocale, useTranslations} from "next-intl";
import {Link} from "@/i18n/navigation";
import {getKbClient} from "@src/lib/api/kb-client";

export default function LoginPage() {
  const locale = useLocale();
  const router = useRouter();
  const t = useTranslations("login");

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const client = getKbClient();

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await client.authLogin?.(username, password);
      router.replace(`/${locale}/dashboard`);
    } catch (err: unknown) {
      setError(t("errorInvalid"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="setup-page">
      <div className="setup-card">
        <div className="setup-header">
          <h1>Family Vault</h1>
          <p>{t("subtitle")}</p>
        </div>
        <form onSubmit={handleLogin} className="setup-form">
          <label>
            {t("username")}
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={t("usernamePlaceholder")}
              autoFocus
              autoComplete="username"
              required
            />
          </label>
          <label>
            {t("password")}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t("passwordPlaceholder")}
              autoComplete="current-password"
              required
            />
          </label>
          {error && <p className="setup-error">{error}</p>}
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? t("signingIn") : t("signIn")}
          </button>
        </form>
        <p className="setup-hint" style={{marginTop: "1rem", textAlign: "center"}}>
          {t("noAccount")}{" "}
          <Link href="/register" style={{color: "var(--accent)"}}>
            {t("register")}
          </Link>
        </p>
      </div>
    </div>
  );
}
