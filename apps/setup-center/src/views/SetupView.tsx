// ─── SetupView: first-run password setup ───
//
// Shown when the backend reports ``setup_required: true`` — i.e. the user
// reached the SPA from a non-loopback address (LAN / WAN) on a fresh install
// that has no web access password yet.
//
// Mirrors the style of LoginView so the user perceives this as part of the
// same flow. Differences vs. LoginView:
//  - Two password inputs (password + confirm) with client-side mismatch
//    detection so we surface the error before round-tripping to the backend.
//  - Inline strength hints that match the backend's validation rules
//    (length ≥ 8, not all digits, not all letters). The backend is the
//    authority — this is purely UX feedback.
//  - On success, fire a verify probe immediately so a save-but-can't-login
//    edge case (e.g. a poisoned data dir that silently dropped the write)
//    is reported before the user navigates away.

import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import logoUrl from "../assets/logo.png";
import { IS_CAPACITOR } from "../platform/detect";
import { checkAuth, setupInitialPassword } from "../platform/auth";

const MIN_LEN = 8;

type StrengthCode =
  | null
  | "errorTooShort"
  | "errorAllDigits"
  | "errorAllLetters";

function localStrengthCheck(pw: string): StrengthCode {
  if (pw.length < MIN_LEN) return "errorTooShort";
  if (/^\d+$/.test(pw)) return "errorAllDigits";
  // ``isalpha``-like: every code point is a letter (basic Latin + most Unicode
  // letter ranges via \p{L}). RegExp with /u flag.
  if (/^\p{L}+$/u.test(pw)) return "errorAllLetters";
  return null;
}

function mapBackendError(detail: string | undefined): string {
  if (!detail) return "errorGeneric";
  const known = new Set([
    "password_too_short",
    "password_all_digits",
    "password_all_letters",
    "password_mismatch",
    "password_invalid",
    "already_set",
  ]);
  if (known.has(detail)) {
    switch (detail) {
      case "password_too_short":
        return "errorTooShort";
      case "password_all_digits":
        return "errorAllDigits";
      case "password_all_letters":
        return "errorAllLetters";
      case "already_set":
        return "errorAlreadySet";
      default:
        return "errorGeneric";
    }
  }
  return detail;
}


export function SetupView({
  apiBaseUrl,
  onSetupSuccess,
}: {
  apiBaseUrl: string;
  onSetupSuccess: () => void;
}) {
  const { t } = useTranslation();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showFaq, setShowFaq] = useState(false);

  const localStrength = useMemo(() => localStrengthCheck(password), [password]);
  const passwordsMatch = confirm.length > 0 && password === confirm;
  const canSubmit =
    !loading &&
    password.length > 0 &&
    confirm.length > 0 &&
    localStrength === null &&
    passwordsMatch;

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      setError(null);

      const local = localStrengthCheck(password);
      if (local) {
        setError(t(`setup.${local}`));
        return;
      }
      if (password !== confirm) {
        setError(t("setup.mismatch"));
        return;
      }

      setLoading(true);
      const result = await setupInitialPassword(password, apiBaseUrl);
      if (!result.success) {
        setLoading(false);
        const key = mapBackendError(result.error);
        const fallback = result.error || t("setup.errorGeneric");
        const i18nKey = `setup.${key}`;
        const localised = t(i18nKey);
        // If the key is an unknown i18n key, ``t()`` echoes it back unchanged.
        // In that case fall back to the raw backend message.
        setError(localised === i18nKey ? fallback : localised);
        return;
      }

      // Verify probe: confirm the auth handshake works end-to-end before
      // handing control back to the parent. If this fails we still believe
      // the password was saved (the 200 came from the server), but something
      // is off — surface a clear instruction rather than dropping the user
      // into a half-broken app.
      try {
        const ok = await checkAuth(apiBaseUrl);
        setLoading(false);
        if (!ok) {
          setError(t("setup.verifyFailed"));
          return;
        }
        onSetupSuccess();
      } catch {
        setLoading(false);
        setError(t("setup.verifyFailed"));
      }
    },
    [apiBaseUrl, confirm, onSetupSuccess, password, t],
  );

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        width: "100vw",
        background:
          "linear-gradient(135deg, var(--bg, #f8fafc) 0%, var(--panel, #e2e8f0) 100%)",
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        color: "var(--text, #334155)",
        padding: 32,
        paddingTop: IS_CAPACITOR ? "max(32px, env(safe-area-inset-top))" : 32,
        boxSizing: "border-box",
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          background: "var(--panel2, #fff)",
          borderRadius: 16,
          boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
          padding: "40px 48px",
          maxWidth: 460,
          width: "100%",
          textAlign: "center",
        }}
      >
        <img
          src={logoUrl}
          alt="OpenAkita"
          style={{ width: 56, height: 56, marginBottom: 12, borderRadius: 12 }}
        />
        <h2
          style={{
            margin: "0 0 8px",
            fontSize: 20,
            fontWeight: 600,
            color: "var(--text, #1e293b)",
          }}
        >
          {t("setup.title")}
        </h2>
        <p
          style={{
            margin: "0 0 20px",
            fontSize: 13,
            color: "var(--text3, #64748b)",
            lineHeight: 1.6,
            textAlign: "left",
          }}
        >
          {t("setup.intro")}
        </p>

        {error && (
          <div
            style={{
              background: "var(--error-bg, #fef2f2)",
              color: "var(--error, #dc2626)",
              borderRadius: 8,
              padding: "8px 12px",
              fontSize: 13,
              marginBottom: 16,
              textAlign: "left",
              whiteSpace: "pre-line",
              lineHeight: 1.6,
            }}
          >
            {error}
          </div>
        )}

        <label
          style={{
            display: "block",
            textAlign: "left",
            fontSize: 13,
            fontWeight: 500,
            marginBottom: 6,
            color: "var(--text2, #475569)",
          }}
        >
          {t("setup.password")}
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={t("setup.passwordPlaceholder")}
          autoFocus
          disabled={loading}
          style={inputStyle}
          onFocus={(e) => {
            e.target.style.borderColor = "var(--primary, #2563eb)";
          }}
          onBlur={(e) => {
            e.target.style.borderColor = "var(--line, #e2e8f0)";
          }}
        />
        {password.length > 0 && localStrength && (
          <div
            style={{
              textAlign: "left",
              fontSize: 12,
              color: "var(--text3, #94a3b8)",
              marginTop: 4,
              marginBottom: 12,
            }}
          >
            {t(`setup.${localStrength}`)}
          </div>
        )}

        <label
          style={{
            display: "block",
            textAlign: "left",
            fontSize: 13,
            fontWeight: 500,
            marginTop: 16,
            marginBottom: 6,
            color: "var(--text2, #475569)",
          }}
        >
          {t("setup.confirm")}
        </label>
        <input
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={t("setup.confirmPlaceholder")}
          disabled={loading}
          style={inputStyle}
          onFocus={(e) => {
            e.target.style.borderColor = "var(--primary, #2563eb)";
          }}
          onBlur={(e) => {
            e.target.style.borderColor = "var(--line, #e2e8f0)";
          }}
        />
        {confirm.length > 0 && !passwordsMatch && (
          <div
            style={{
              textAlign: "left",
              fontSize: 12,
              color: "var(--error, #dc2626)",
              marginTop: 4,
              marginBottom: 12,
            }}
          >
            {t("setup.mismatch")}
          </div>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          style={{
            width: "100%",
            marginTop: 20,
            background: !canSubmit
              ? "var(--text3, #94a3b8)"
              : "linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%)",
            color: "#fff",
            border: "none",
            borderRadius: 10,
            padding: "10px 0",
            fontSize: 15,
            fontWeight: 600,
            cursor: !canSubmit ? "not-allowed" : "pointer",
            boxShadow: "0 2px 8px rgba(37,99,235,0.3)",
            transition: "transform 0.1s, opacity 0.15s",
            opacity: !canSubmit ? 0.7 : 1,
          }}
        >
          {loading ? t("setup.submitting") : t("setup.submit")}
        </button>

        <div style={{ marginTop: 24, borderTop: "1px solid var(--line, #e2e8f0)", paddingTop: 16, textAlign: "left" }}>
          <button
            type="button"
            onClick={() => setShowFaq((s) => !s)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text3, #64748b)",
              fontSize: 13,
              cursor: "pointer",
              padding: 0,
            }}
          >
            {showFaq ? "▾ " : "▸ "}
            {t("setup.faqTitle")}
          </button>
          {showFaq && (
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--text3, #64748b)", lineHeight: 1.7 }}>
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontWeight: 600, color: "var(--text2, #475569)", marginBottom: 2 }}>
                  {t("setup.faqWhy")}
                </div>
                <div>{t("setup.faqWhyAns")}</div>
              </div>
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontWeight: 600, color: "var(--text2, #475569)", marginBottom: 2 }}>
                  {t("setup.faqForgot")}
                </div>
                <div>{t("setup.faqForgotAns")}</div>
              </div>
              <div>
                <div style={{ fontWeight: 600, color: "var(--text2, #475569)", marginBottom: 2 }}>
                  {t("setup.faqLocal")}
                </div>
                <div>{t("setup.faqLocalAns")}</div>
              </div>
            </div>
          )}
        </div>
      </form>
    </div>
  );
}


const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 14px",
  fontSize: 15,
  borderRadius: 10,
  border: "1px solid var(--line, #e2e8f0)",
  background: "var(--bg, #f8fafc)",
  color: "var(--text, #1e293b)",
  outline: "none",
  boxSizing: "border-box",
  transition: "border-color 0.15s",
};
