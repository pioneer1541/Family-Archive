"use client";

import {useState} from "react";
import {useRouter} from "next/navigation";
import {useLocale, useTranslations} from "next-intl";
import {getKbClient} from "@src/lib/api/kb-client";

export default function RegisterPage() {
  const locale = useLocale();
  const router = useRouter();
  const t = useTranslations("register");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const client = getKbClient();

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError(t("errorPasswordLength"));
      return;
    }

    if (password !== confirmPassword) {
      setError(t("errorPasswordMismatch"));
      return;
    }

    setLoading(true);
    try {
      await client.authRegister?.(email, password);
      router.replace(`/${locale}/login?registered=1`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.includes("already") || msg.includes("exists")) {
        setError(t("errorEmailExists"));
      } else {
        setError(t("errorRegisterFailed"));
      }
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
        <form onSubmit={handleRegister} className="setup-form">
          <label>
            {t("email")}
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={t("emailPlaceholder")}
              autoFocus
              autoComplete="email"
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
              autoComplete="new-password"
              required
              minLength={8}
            />
          </label>
          <label>
            {t("confirmPassword")}
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder={t("confirmPasswordPlaceholder")}
              autoComplete="new-password"
              required
            />
          </label>
          {error && <p className="setup-error">{error}</p>}
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? t("signingUp") : t("signUp")}
          </button>
        </form>
        <p className="setup-hint" style={{marginTop: "1rem", textAlign: "center"}}>
          {t("haveAccount")}{" "}
          <a href={`/${locale}/login`} style={{color: "var(--accent)"}}>
            {t("login")}
          </a>
        </p>
      </div>
    </div>
  );
}
