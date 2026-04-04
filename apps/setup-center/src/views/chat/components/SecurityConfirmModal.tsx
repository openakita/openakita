import { useRef, useEffect, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../../../providers";
import { getAccessToken } from "../../../platform/auth";
import { IS_TAURI } from "../../../platform";
import { IconShield, IconAlertCircle } from "../../../icons";

const RISK_LABELS: Record<string, string> = {
  critical: "极高风险",
  high: "高风险",
  medium: "中风险",
  low: "低风险",
};

function humanizeArgs(tool: string, args: Record<string, unknown>): string {
  if (tool === "run_shell" && args.command) return `即将执行命令：${args.command}`;
  if ((tool === "write_file" || tool === "edit_file") && args.path) return `即将修改文件：${args.path}`;
  if (tool === "delete_file" && args.path) return `即将删除文件：${args.path}`;
  return JSON.stringify(args, null, 2);
}

export function SecurityConfirmModal({
  data, apiBase, onClose, timerRef, setData,
}: {
  data: { tool: string; args: Record<string, unknown>; reason: string; riskLevel: string; needsSandbox: boolean; toolId?: string; countdown: number };
  apiBase: string;
  onClose: () => void;
  timerRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  setData: React.Dispatch<React.SetStateAction<typeof data | null>>;
}) {
  const { t } = useTranslation();
  const pausedRef = useRef(false);
  const [postError, setPostError] = useState<string | null>(null);

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      if (pausedRef.current) return;
      setData((prev) => {
        if (!prev) return prev;
        if (prev.countdown <= 1) {
          clearInterval(timerRef.current!);
          timerRef.current = null;
          handleDecision("deny");
          return null;
        }
        return { ...prev, countdown: prev.countdown - 1 };
      });
    }, 1000);
    return () => {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDecision = useCallback(async (decision: "allow" | "deny" | "sandbox") => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    setPostError(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (!IS_TAURI) {
        const token = getAccessToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;
      }
      await safeFetch(`${apiBase}/api/chat/security-confirm`, {
        method: "POST",
        headers,
        body: JSON.stringify({ confirm_id: data.toolId, decision }),
      });
      onClose();
    } catch (err) {
      console.error("[SecurityConfirm] decision failed:", err);
      setPostError("网络请求失败，请重试");
    }
  }, [apiBase, data.toolId, onClose, timerRef]);

  const riskColors: Record<string, string> = {
    critical: "#ef4444",
    high: "#f59e0b",
    medium: "#3b82f6",
    low: "#10b981",
  };
  const riskColor = riskColors[data.riskLevel] || riskColors.medium;

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 99999,
        background: "rgba(0,0,0,0.55)", backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) { pausedRef.current = !pausedRef.current; } }}
    >
      <div style={{
        background: "var(--panel)", borderRadius: 16, padding: "24px 28px",
        maxWidth: 480, width: "90%",
        border: `2px solid ${riskColor}`,
        boxShadow: `0 8px 32px rgba(0,0,0,0.25), 0 0 0 1px ${riskColor}33`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <IconShield size={24} style={{ color: riskColor }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>{t("chat.securityConfirmTitle", "安全确认")}</div>
            <div style={{ fontSize: 12, opacity: 0.6 }}>
              {t("chat.securityRiskLevel", "风险等级")}: <span style={{ color: riskColor, fontWeight: 700 }}>{RISK_LABELS[data.riskLevel] || data.riskLevel}</span>
            </div>
          </div>
        </div>

        <div style={{ padding: "12px 14px", background: `${riskColor}08`, border: `1px solid ${riskColor}22`, borderRadius: 10, marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <IconAlertCircle size={16} style={{ color: riskColor, marginTop: 2, flexShrink: 0 }} />
            <div style={{ fontSize: 13, lineHeight: 1.5 }}>{data.reason}</div>
          </div>
        </div>

        <div style={{ fontSize: 13, marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{t("chat.securityTool", "工具")}: <code>{data.tool}</code></div>
          <pre style={{ margin: 0, fontSize: 11, maxHeight: 120, overflow: "auto", padding: "8px 10px", borderRadius: 8, background: "var(--panel2)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {humanizeArgs(data.tool, data.args)}
          </pre>
        </div>

        {postError && (
          <div style={{ fontSize: 12, color: "#ef4444", marginBottom: 8, padding: "6px 10px", background: "#ef444411", borderRadius: 6 }}>
            {postError}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            onClick={() => handleDecision("deny")}
            style={{
              padding: "8px 20px", borderRadius: 8, cursor: "pointer",
              background: "transparent", border: "1px solid var(--line)", color: "var(--text)",
              fontSize: 13, fontWeight: 600,
            }}
          >
            {t("chat.securityDeny", "拒绝")} ({data.countdown}s)
          </button>
          {data.needsSandbox && (
            <button
              onClick={() => handleDecision("sandbox")}
              style={{
                padding: "8px 20px", borderRadius: 8, cursor: "pointer",
                background: "#3b82f622", border: "1px solid #3b82f644", color: "#3b82f6",
                fontSize: 13, fontWeight: 600,
              }}
            >
              {t("chat.securitySandbox", "沙箱运行")}
            </button>
          )}
          <button
            onClick={() => handleDecision("allow")}
            style={{
              padding: "8px 20px", borderRadius: 8, cursor: "pointer",
              background: riskColor, border: "none", color: "#fff",
              fontSize: 13, fontWeight: 700,
            }}
          >
            {t("chat.securityAllow", "允许")}
          </button>
        </div>
      </div>
    </div>
  );
}
