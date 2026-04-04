import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { ChatErrorInfo } from "../utils/chatTypes";
import { ERROR_META } from "../utils/chatHelpers";

export function ErrorCard({ error, onRetry }: { error: ChatErrorInfo; onRetry?: () => void }) {
  const { t } = useTranslation();
  const meta = ERROR_META[error.category] || ERROR_META.unknown;
  const [copied, setCopied] = useState(false);
  const [countdown, setCountdown] = useState(() => (error.category === "quota" && onRetry) ? 30 : 0);
  const countdownRef = useRef(countdown);
  countdownRef.current = countdown;

  useEffect(() => {
    if (countdown <= 0 || error.category !== "quota") return;
    const timer = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 0) return 0;
        if (prev <= 1) {
          clearInterval(timer);
          onRetry?.();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [error.category, countdown > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCopy = useCallback(() => {
    const detail = error.raw || error.message;
    navigator.clipboard.writeText(detail).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => {});
  }, [error]);

  return (
    <div
      className="errorCard"
      style={{
        border: `1px solid ${meta.color}`,
        borderLeft: `4px solid ${meta.color}`,
        borderRadius: 8,
        padding: "10px 14px",
        margin: "8px 0",
        background: `${meta.color}08`,
        fontSize: 13,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 600, marginBottom: 4 }}>
        <span>{meta.icon}</span>
        <span style={{ color: meta.color }}>{error.message}</span>
      </div>
      {meta.hint && (
        <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 6 }}>
          {meta.hint}
          {countdown > 0 && (
            <span style={{ marginLeft: 8, fontWeight: 600 }}>
              ({countdown}s {t("chat.autoRetry", "后自动重试")})
            </span>
          )}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
        {onRetry && (
          <button
            onClick={() => { setCountdown(0); onRetry(); }}
            style={{
              fontSize: 12, padding: "3px 10px", borderRadius: 4,
              border: `1px solid ${meta.color}`, background: "transparent",
              color: meta.color, cursor: "pointer",
            }}
          >
            {t("chat.retry", "重试")}
          </button>
        )}
        <button
          onClick={handleCopy}
          style={{
            fontSize: 12, padding: "3px 10px", borderRadius: 4,
            border: "1px solid var(--line)", background: "transparent",
            color: "var(--text-secondary)", cursor: "pointer",
          }}
        >
          {copied ? t("chat.copied", "已复制") : t("chat.copyError", "复制详情")}
        </button>
        {countdown > 0 && (
          <button
            onClick={() => setCountdown(0)}
            style={{
              fontSize: 12, padding: "3px 10px", borderRadius: 4,
              border: "1px solid var(--line)", background: "transparent",
              color: "var(--text-secondary)", cursor: "pointer",
            }}
          >
            {t("chat.cancelAutoRetry", "取消自动重试")}
          </button>
        )}
      </div>
    </div>
  );
}
