import { useEffect, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../../../providers";
import { getAccessToken } from "../../../platform/auth";
import { IS_TAURI } from "../../../platform";
import { IconShield, IconAlertCircle } from "../../../icons";

export type SecurityDecision = "allow_once" | "allow_session" | "allow_always" | "deny" | "sandbox";
export type SecurityTimeoutDefault = "allow_once" | "deny";
type CloseReason = SecurityDecision | "timeout";

export type SecurityDisplayToken = {
  value: string;
  label: string;
  color?: string;
  description?: string;
};

export type SecurityConfirmDisplay = {
  title: string;
  reason: { text: string; raw?: string };
  risk: SecurityDisplayToken & { color: string };
  tool: SecurityDisplayToken;
  channel?: SecurityDisplayToken;
  approval_class?: SecurityDisplayToken;
  arguments: { text: string; format?: string };
};

export type SecurityDecisionChainStep = {
  name: string;
  action: string;
  note: string;
  metadata?: Record<string, unknown>;
  display: {
    label: string;
    action: SecurityDisplayToken & { color: string };
    note?: string;
  };
};

export type SecurityConfirmModalData = {
  tool: string;
  args: Record<string, unknown>;
  reason: string;
  riskLevel: string;
  needsSandbox: boolean;
  toolId: string;
  countdown: number;
  defaultOnTimeout: SecurityTimeoutDefault;
  decisionChain?: SecurityDecisionChainStep[];
  options: SecurityDecision[];
  display: SecurityConfirmDisplay;
  source?: "risk_gate" | "policy_v2";
  conversationId?: string;
  originalMessage?: string;
  riskIntent?: Record<string, unknown>;
};

export interface SecurityCloseInfo {
  decision: CloseReason;
  tool: string;
  command: string;
  source?: "risk_gate" | "policy_v2";
  conversationId?: string;
  uiMessage?: string;
  originalMessage?: string;
  queuedCount?: number;
  nextConfirm?: Record<string, unknown>;
  execution?: {
    state?: string;
    backend_owned?: boolean;
    client_action?: "connect_resume" | "none" | string;
    conversation_id?: string;
    confirmation_id?: string;
    resume_url?: string;
    request_id?: string;
    since_seq?: number;
    wait_ms?: number;
  };
}

export function SecurityConfirmModal({
  data, apiBase, onClose,
}: {
  data: SecurityConfirmModalData;
  apiBase: string;
  onClose: (info?: SecurityCloseInfo) => void;
}) {
  const { t } = useTranslation();
  const initialCountdown = Math.max(0, Math.floor(Number(data.countdown) || 0));
  const [countdown, setCountdown] = useState(initialCountdown);
  const [paused, setPaused] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [timeoutAttempted, setTimeoutAttempted] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(false);
  // C23 P2-2: 默认折叠，避免把 modal 撑太大；用户主动 expand 才显示。
  const [showChain, setShowChain] = useState(false);

  useEffect(() => {
    setCountdown(initialCountdown);
    setPaused(false);
    setSubmitting(false);
    setTimeoutAttempted(false);
    setPostError(null);
    setShowMore(false);
    setShowChain(false);
  }, [data, initialCountdown]);

  useEffect(() => {
    if (paused || submitting || countdown <= 0) return;
    const timeoutId = window.setTimeout(() => {
      setCountdown((current) => Math.max(current - 1, 0));
    }, 1000);
    return () => window.clearTimeout(timeoutId);
  }, [countdown, paused, submitting]);

  const handleDecision = useCallback(async (decision: SecurityDecision, closeReason: CloseReason = decision) => {
    if (submitting) return;
    setSubmitting(true);
    setPostError(null);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (!IS_TAURI) {
        const token = getAccessToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;
      }
      const res = await safeFetch(`${apiBase}/api/chat/security-confirm`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          confirm_id: data.toolId,
          decision: data.source === "risk_gate" ? closeReason : decision,
        }),
      });
      const payload = await res.json().catch(() => null) as {
        kind?: string;
        conversation_id?: string;
        ui_message?: string;
        original_message?: string;
        queued_count?: number;
        next_confirm?: Record<string, unknown>;
        execution?: SecurityCloseInfo["execution"];
      } | null;
      const resolvedSource =
        payload?.kind === "risk_gate" || data.source === "risk_gate" ? "risk_gate" : "policy_v2";
      onClose({
        decision: closeReason,
        tool: data.tool,
        command: String(data.args.command ?? ""),
        source: resolvedSource,
        conversationId: payload?.conversation_id || data.conversationId,
        uiMessage: payload?.ui_message,
        originalMessage: payload?.original_message || data.originalMessage,
        queuedCount: payload?.queued_count,
        nextConfirm: payload?.next_confirm,
        execution: payload?.execution,
      });
    } catch (err) {
      console.error("[SecurityConfirm] decision failed:", err);
      setSubmitting(false);
      setPostError("网络请求失败，请重试");
    }
  }, [apiBase, data, onClose, submitting]);

  useEffect(() => {
    if (countdown > 0 || submitting || timeoutAttempted) return;
    setTimeoutAttempted(true);
    const timeoutDecision: SecurityDecision =
      data.defaultOnTimeout === "allow_once" ? "allow_once" : "deny";
    void handleDecision(timeoutDecision, "timeout");
  }, [countdown, data.defaultOnTimeout, handleDecision, submitting, timeoutAttempted]);

  const riskColor = data.display.risk.color;
  const approvalClass = data.display.approval_class;
  const showDecision = (decision: SecurityDecision) => data.options.includes(decision);

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
      onClick={(e) => { if (e.target === e.currentTarget) setPaused((value) => !value); }}
    >
      <div style={{
        background: "var(--panel)", borderRadius: 16, padding: "24px 28px",
        maxWidth: 520, width: "90%",
        border: `2px solid ${riskColor}`,
        boxShadow: `0 8px 32px rgba(0,0,0,0.25), 0 0 0 1px ${riskColor}33`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <IconShield size={24} style={{ color: riskColor }} />
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ fontWeight: 700, fontSize: 16 }}>
                {data.display.title}
              </div>
              {approvalClass && (
                <span
                  title={approvalClass.value}
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: `${approvalClass.color || riskColor}1a`,
                    color: approvalClass.color || riskColor,
                    border: `1px solid ${approvalClass.color || riskColor}55`,
                    letterSpacing: "0.02em",
                  }}
                >
                  {approvalClass.label}
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, opacity: 0.6, display: "flex", gap: 8, alignItems: "center" }}>
              <span>
                {t("chat.securityRiskLevel", "风险等级")}:{" "}
                <span style={{ color: riskColor, fontWeight: 700 }}>
                  {data.display.risk.label}
                </span>
              </span>
              {data.display.channel?.value === "im" && (
                <span style={{ opacity: 0.7 }}>· {data.display.channel.label}</span>
              )}
            </div>
          </div>
        </div>

        <div style={{
          padding: "12px 14px", background: `${riskColor}08`,
          border: `1px solid ${riskColor}22`, borderRadius: 10, marginBottom: 12,
        }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <IconAlertCircle size={16} style={{ color: riskColor, marginTop: 2, flexShrink: 0 }} />
            <div style={{ fontSize: 13, lineHeight: 1.5 }} title={data.display.reason.raw || data.reason}>
              {data.display.reason.text}
            </div>
          </div>
        </div>

        <div style={{ fontSize: 13, marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            {t("chat.securityTool", "工具")}: <code title={data.display.tool.value}>{data.display.tool.label}</code>
          </div>
          <pre style={{
            margin: 0, fontSize: 11, maxHeight: 120, overflow: "auto",
            padding: "8px 10px", borderRadius: 8, background: "var(--panel2)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {data.display.arguments.text}
          </pre>
        </div>

        {data.decisionChain && data.decisionChain.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <button
              onClick={() => setShowChain((s) => !s)}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--text)",
                opacity: 0.7,
                fontSize: 11,
                cursor: "pointer",
                padding: "2px 0",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
              title={t("chat.securityChainHint", "查看 policy_v2 引擎逐步判定记录")}
            >
              <span style={{ display: "inline-block", transform: showChain ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>▸</span>
              {t("chat.securityDecisionChain", "决策依据")} ({data.decisionChain.length})
            </button>
            {showChain && (
              <ol style={{
                margin: "6px 0 0",
                padding: "8px 10px 8px 28px",
                fontSize: 11,
                lineHeight: 1.5,
                background: "var(--panel2)",
                borderRadius: 8,
                border: "1px solid var(--line)",
                maxHeight: 180,
                overflow: "auto",
                listStyle: "decimal",
              }}>
                {data.decisionChain.map((step, idx) => {
                  const actionMeta = step.display.action;
                  const stepLabel = step.display.label;
                  const stepNote = step.display.note || "";
                  return (
                    <li key={idx} style={{ marginBottom: 4 }}>
                      <span style={{ fontWeight: 600 }} title={step.name}>{stepLabel}</span>
                      <span
                        style={{
                          marginLeft: 6,
                          padding: "1px 6px",
                          fontSize: 10,
                          fontWeight: 700,
                          borderRadius: 999,
                          background: `${actionMeta.color}1a`,
                          color: actionMeta.color,
                          border: `1px solid ${actionMeta.color}55`,
                        }}
                      >
                        {actionMeta.label}
                      </span>
                      {stepNote && (
                        <span
                          title={step.note}
                          style={{ marginLeft: 6, opacity: 0.75, wordBreak: "break-word" }}
                        >
                          {stepNote}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        )}

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
          {showDecision("deny") && (
            <button
              onClick={() => handleDecision("deny")}
              disabled={submitting}
              style={{
                ...btnBase,
                background: "transparent", border: "1px solid var(--line)",
                color: "var(--text)",
                opacity: submitting ? 0.55 : 1,
              }}
            >
              {t("chat.securityDeny", "拒绝")} ({countdown}s)
            </button>
          )}

          {/* Right: allow actions */}
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            {data.source !== "risk_gate" && showDecision("sandbox") && (
              <button
                onClick={() => handleDecision("sandbox")}
                disabled={submitting}
                style={{
                  ...btnBase,
                  background: "#3b82f622", color: "#3b82f6",
                  border: "1px solid #3b82f644",
                  opacity: submitting ? 0.55 : 1,
                }}
              >
                {t("chat.securitySandbox", "沙箱运行")}
              </button>
            )}
            {showDecision("allow_once") && (
              <button
                onClick={() => handleDecision("allow_once")}
                disabled={submitting}
                style={{ ...btnBase, background: riskColor, color: "#fff", opacity: submitting ? 0.55 : 1 }}
              >
                {t("chat.securityAllowOnce", "允许一次")}
              </button>
            )}
            {/* More options toggle */}
            {data.source !== "risk_gate" && (showDecision("allow_session") || showDecision("allow_always")) && (
            <div style={{ position: "relative" }}>
              <button
                onClick={() => setShowMore(!showMore)}
                disabled={submitting}
                style={{
                  ...btnBase, background: "var(--panel2)", color: "var(--text)",
                  padding: "8px 10px", fontSize: 16, lineHeight: 1,
                  border: "1px solid var(--line)",
                  opacity: submitting ? 0.55 : 1,
                }}
                title={t("chat.securityMoreOptions", "更多选项")}
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
                  {showDecision("allow_session") && (
                    <button
                      onClick={() => { setShowMore(false); handleDecision("allow_session"); }}
                      disabled={submitting}
                      style={{
                        ...btnBase, width: "100%", textAlign: "left",
                        background: "transparent", color: "var(--text)",
                        padding: "8px 12px",
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                    >
                      {t("chat.securityAllowSession", "本次会话允许")}
                    </button>
                  )}
                  {showDecision("allow_always") && (
                    <button
                      onClick={() => { setShowMore(false); handleDecision("allow_always"); }}
                      disabled={submitting}
                      style={{
                        ...btnBase, width: "100%", textAlign: "left",
                        background: "transparent", color: "var(--text)",
                        padding: "8px 12px",
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                    >
                      {t("chat.securityAllowAlways", "始终允许")}
                    </button>
                  )}
                </div>
              )}
            </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
