// ─── LoginView: Web access password login page ───

import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { login } from "../platform/auth";
import { IS_CAPACITOR } from "../platform/detect";
import logoUrl from "../assets/logo.png";

export function LoginView({
  apiBaseUrl,
  onLoginSuccess,
  onSwitchServer,
  onPreview,
}: {
  apiBaseUrl: string;
  onLoginSuccess: () => void;
  onSwitchServer?: () => void;
  onPreview?: () => void;
}) {
  const { t } = useTranslation();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = useCallback(async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!password.trim()) return;
    setLoading(true);
    setError(null);

    const result = await login(password, apiBaseUrl);
    setLoading(false);

    if (result.success) {
      onLoginSuccess();
    } else {
      const raw = (result.error || "").toLowerCase();
      if (raw.includes("too many")) {
        setError(t("login.tooManyAttempts"));
      } else if (raw.includes("invalid password")) {
        setError(t("login.invalidPassword"));
      } else if (raw.includes("abort") || raw.includes("timeout")) {
        setError(t("login.timeout"));
      } else if (raw.includes("failed to fetch") || raw.includes("networkerror") || raw.includes("fetch failed") || raw.includes("network") || raw.includes("load failed")) {
        setError(IS_CAPACITOR ? t("login.networkErrorMobile") : t("login.networkError"));
      } else {
        setError(result.error || t("login.failed"));
      }
    }
  }, [password, apiBaseUrl, onLoginSuccess, t]);

  const serverDisplay = apiBaseUrl ? apiBaseUrl.replace(/^https?:\/\//, "") : "";

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      height: "100vh",
      width: "100vw",
      background: "linear-gradient(135deg, var(--bg, #f8fafc) 0%, var(--panel, #e2e8f0) 100%)",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      color: "var(--text, #334155)",
      padding: 32,
      paddingTop: IS_CAPACITOR ? "max(32px, env(safe-area-inset-top))" : 32,
      boxSizing: "border-box",
    }}>
      <form
        onSubmit={handleSubmit}
        style={{
          background: "var(--panel2, #fff)",
          borderRadius: 16,
          boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
          padding: "40px 48px",
          maxWidth: 400,
          width: "100%",
          textAlign: "center",
        }}
      >
        <img
          src={logoUrl}
          alt="OpenAkita"
          style={{ width: 56, height: 56, marginBottom: 12, borderRadius: 12 }}
        />
        <h2 style={{
          margin: "0 0 8px",
          fontSize: 20,
          fontWeight: 600,
          color: "var(--text, #1e293b)",
        }}>
          OpenAkita Web
        </h2>
        <p style={{
          margin: "0 0 20px",
          fontSize: 14,
          color: "var(--text3, #64748b)",
          lineHeight: 1.6,
        }}>
          {t("login.prompt")}
        </p>

        {/* Server address display for Capacitor */}
        {IS_CAPACITOR && serverDisplay && (
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
            marginBottom: 16, padding: "6px 12px", borderRadius: 8,
            background: "var(--bg, #f1f5f9)", fontSize: 12, color: "var(--text3, #64748b)",
          }}>
            <span style={{ opacity: 0.6 }}>🔗</span>
            <span style={{ fontFamily: "monospace", wordBreak: "break-all" }}>{serverDisplay}</span>
          </div>
        )}

        {error && (
          <div style={{
            background: "var(--error-bg, #fef2f2)",
            color: "var(--error, #dc2626)",
            borderRadius: 8,
            padding: "8px 12px",
            fontSize: 13,
            marginBottom: 16,
            textAlign: "left",
            whiteSpace: "pre-line",
            lineHeight: 1.6,
          }}>
            {error}
          </div>
        )}

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={t("login.passwordPlaceholder")}
          autoFocus
          disabled={loading}
          style={{
            width: "100%",
            padding: "10px 14px",
            fontSize: 15,
            borderRadius: 10,
            border: "1px solid var(--line, #e2e8f0)",
            background: "var(--bg, #f8fafc)",
            color: "var(--text, #1e293b)",
            outline: "none",
            boxSizing: "border-box",
            marginBottom: 16,
            transition: "border-color 0.15s",
          }}
          onFocus={(e) => { e.target.style.borderColor = "var(--primary, #0ea5e9)"; }}
          onBlur={(e) => { e.target.style.borderColor = "var(--line, #e2e8f0)"; }}
        />

        <button
          type="submit"
          disabled={loading || !password.trim()}
          style={{
            width: "100%",
            background: loading
              ? "var(--text3, #94a3b8)"
              : "linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)",
            color: "#fff",
            border: "none",
            borderRadius: 10,
            padding: "10px 0",
            fontSize: 15,
            fontWeight: 600,
            cursor: loading ? "wait" : "pointer",
            boxShadow: "0 2px 8px rgba(14,165,233,0.3)",
            transition: "transform 0.1s, opacity 0.15s",
            opacity: loading || !password.trim() ? 0.7 : 1,
          }}
          onMouseDown={(e) => { if (!loading) (e.target as HTMLButtonElement).style.transform = "scale(0.97)"; }}
          onMouseUp={(e) => { (e.target as HTMLButtonElement).style.transform = ""; }}
        >
          {loading ? t("login.loggingIn") : t("login.submit")}
        </button>

        {/* Switch server button for Capacitor */}
        {onSwitchServer && (
          <button
            type="button"
            onClick={onSwitchServer}
            style={{
              width: "100%",
              marginTop: 12,
              background: "none",
              border: "1px solid var(--line, #e2e8f0)",
              borderRadius: 10,
              padding: "9px 0",
              fontSize: 14,
              color: "var(--text3, #64748b)",
              cursor: "pointer",
              transition: "border-color 0.15s, color 0.15s",
            }}
            onMouseEnter={(e) => {
              (e.target as HTMLButtonElement).style.borderColor = "var(--primary, #0ea5e9)";
              (e.target as HTMLButtonElement).style.color = "var(--primary, #0ea5e9)";
            }}
            onMouseLeave={(e) => {
              (e.target as HTMLButtonElement).style.borderColor = "var(--line, #e2e8f0)";
              (e.target as HTMLButtonElement).style.color = "var(--text3, #64748b)";
            }}
          >
            {t("login.switchServer", { defaultValue: "切换 / 添加服务器" })}
          </button>
        )}

        {/* Preview mode button */}
        {onPreview && (
          <button
            type="button"
            onClick={onPreview}
            style={{
              width: "100%",
              marginTop: 10,
              background: "none",
              border: "none",
              padding: "8px 0",
              fontSize: 13,
              color: "var(--text3, #94a3b8)",
              cursor: "pointer",
              textDecoration: "underline",
              textUnderlineOffset: 3,
            }}
          >
            {t("login.preview", { defaultValue: "跳过连接，预览界面" })}
          </button>
        )}
      </form>

      <div style={{
        marginTop: 16,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
      }}>
        <p style={{
          margin: 0,
          fontSize: 12,
          color: "var(--text3, #94a3b8)",
        }}>
          {t("login.hint")}
        </p>
        <a
          href="https://openakita.ai"
          target="_blank"
          rel="noopener noreferrer"
          style={{
            fontSize: 12,
            color: "var(--brand, #0ea5e9)",
            textDecoration: "none",
            opacity: 0.8,
          }}
        >
          openakita.ai - {t("login.downloadDesktop", { defaultValue: "下载桌面端" })}
        </a>
      </div>
    </div>
  );
}
