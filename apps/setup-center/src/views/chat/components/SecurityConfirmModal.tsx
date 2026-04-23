import { useRef, useEffect, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../../../providers";
import { getAccessToken } from "../../../platform/auth";
import { IS_TAURI } from "../../../platform";
import { IconShield, IconAlertCircle } from "../../../icons";

const RISK_LABELS: Record<string, string> = {
  critical: "Critical risk",
  high: "High risk",
  medium: "Medium risk",
  low: "Low risk",
};

function humanizeArgs(tool: string, args: Record<string, unknown>): string {
  if (tool === "run_shell" && args.command) return `About to run command: ${args.command}`;
  if ((tool === "write_file" || tool === "edit_file") && args.path) return `About to modify file: ${args.path}`;
  if (tool === "delete_file" && args.path) return `About to delete file: ${args.path}`;
  return JSON.stringify(args, null, 2);
}

type Decision = "allow_once" | "allow_session" | "allow_always" | "deny" | "sandbox";

export interface SecurityCloseInfo {
  decision: string;
  tool: string;
  command: string;
}

export function SecurityConfirmModal({
  data, apiBase, onClose, timerRef, setData,
}: {
  data: {
    tool: string; args: Record<string, unknown>; reason: string;
    riskLevel: string; needsSandbox: boolean; toolId?: string; countdown: number;
    defaultOnTimeout?: string;
  };
  apiBase: string;
  onClose: (info?: SecurityCloseInfo) => void;
  timerRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  setData: React.Dispatch<React.SetStateAction<typeof data | null>>;
}) {
  const { t } = useTranslation();
  const pausedRef = useRef(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(false);

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    const timeoutDecision = data.defaultOnTimeout === "allow" ? "allow_once" : "deny";
    timerRef.current = setInterval(() => {
      if (pausedRef.current) return;
      setData((prev) => {
        if (!prev) return prev;
        if (prev.countdown <= 1) {
          clearInterval(timerRef.current!);
          timerRef.current = null;
          handleDecision(timeoutDecision as Decision);
          return null;
        }
        return { ...prev, countdown: prev.countdown - 1 };
      });
    }, 1000);
    return () => {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDecision = useCallback(async (decision: Decision) => {
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
      onClose({ decision, tool: data.tool, command: String(data.args.command ?? "") });
    } catch (err) {
      console.error("[SecurityConfirm] decision failed:", err);
      setPostError("Network request failed, please retry");
    }
  }, [apiBase, data.toolId, onClose, timerRef]);

  const riskColors: Record<string, string> = {
    critical: "#ef4444",
    high: "#f59e0b",
    medium: "#3b82f6",
    low: "#10b981",
  };
  const riskColor = riskColors[data.riskLevel] || riskColors.medium;
  const isHigh = data.riskLevel === "high" || data.riskLevel === "critical";

  const btnBase: React.CSSProperties = {
    padding: "8px 16px", borderRadius: 8, cursor: "pointer",
    fontSize: 13, fontWeight: 600, border: "none",
  };

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
        maxWidth: 520, width: "90%",
        border: `2px solid ${riskColor}`,
        boxShadow: `0 8px 32px rgba(0,0,0,0.25), 0 0 0 1px ${riskColor}33`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <IconShield size={24} style={{ color: riskColor }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>
              {t("chat.securityConfirmTitle", "Security Confirmation")}
            </div>
            <div style={{ fontSize: 12, opacity: 0.6 }}>
              {t("chat.securityRiskLevel", "Risk Level")}:{" "}
              <span style={{ color: riskColor, fontWeight: 700 }}>
                {RISK_LABELS[data.riskLevel] || data.riskLevel}
              </span>
            </div>
          </div>
        </div>

        <div style={{
          padding: "12px 14px", background: `${riskColor}08`,
          border: `1px solid ${riskColor}22`, borderRadius: 10, marginBottom: 12,
        }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <IconAlertCircle size={16} style={{ color: riskColor, marginTop: 2, flexShrink: 0 }} />
            <div style={{ fontSize: 13, lineHeight: 1.5 }}>{data.reason}</div>
          </div>
        </div>

        <div style={{ fontSize: 13, marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            {t("chat.securityTool", "Tool")}: <code>{data.tool}</code>
          </div>
          <pre style={{
            margin: 0, fontSize: 11, maxHeight: 120, overflow: "auto",
            padding: "8px 10px", borderRadius: 8, background: "var(--panel2)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {humanizeArgs(data.tool, data.args)}
          </pre>
        </div>

        {postError && (
          <div style={{
            fontSize: 12, color: "#ef4444", marginBottom: 8,
            padding: "6px 10px", background: "#ef444411", borderRadius: 6,
          }}>
            {postError}
          </div>
        )}

        {/* Button row */}
        <div style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "center", gap: 8, flexWrap: "wrap",
        }}>
          {/* Left: deny */}
          <button
            onClick={() => handleDecision("deny")}
            style={{
              ...btnBase,
              background: "transparent", border: "1px solid var(--line)",
              color: "var(--text)",
            }}
          >
            {t("chat.securityDeny", "Deny")} ({data.countdown}s)
          </button>

          {/* Right: allow actions */}
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            {data.needsSandbox && (
              <button
                onClick={() => handleDecision("sandbox")}
                style={{
                  ...btnBase,
                  background: "#3b82f622", color: "#3b82f6",
                  border: "1px solid #3b82f644",
                }}
              >
                {t("chat.securitySandbox", "Run in Sandbox")}
              </button>
            )}
            <button
              onClick={() => handleDecision("allow_once")}
              style={{ ...btnBase, background: riskColor, color: "#fff" }}
            >
              {t("chat.securityAllowOnce", "Allow Once")}
            </button>
            {/* More options toggle */}
            <div style={{ position: "relative" }}>
              <button
                onClick={() => setShowMore(!showMore)}
                style={{
                  ...btnBase, background: "var(--panel2)", color: "var(--text)",
                  padding: "8px 10px", fontSize: 16, lineHeight: 1,
                  border: "1px solid var(--line)",
                }}
                title={t("chat.securityMoreOptions", "More Options")}
              >
                ▾
              </button>
              {showMore && (
                <div style={{
                  position: "absolute", right: 0, bottom: "calc(100% + 4px)",
                  background: "var(--panel)", border: "1px solid var(--line)",
                  borderRadius: 10, padding: 4, minWidth: 160,
                  boxShadow: "0 4px 16px rgba(0,0,0,0.2)", zIndex: 10,
                }}>
                  <button
                    onClick={() => { setShowMore(false); handleDecision("allow_session"); }}
                    style={{
                      ...btnBase, width: "100%", textAlign: "left",
                      background: "transparent", color: "var(--text)",
                      padding: "8px 12px",
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                  >
                    {t("chat.securityAllowSession", "Allow for This Session")}
                  </button>
                  {!isHigh && (
                    <button
                      onClick={() => { setShowMore(false); handleDecision("allow_always"); }}
                      style={{
                        ...btnBase, width: "100%", textAlign: "left",
                        background: "transparent", color: "var(--text)",
                        padding: "8px 12px",
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                    >
                      {t("chat.securityAllowAlways", "Always Allow")}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
