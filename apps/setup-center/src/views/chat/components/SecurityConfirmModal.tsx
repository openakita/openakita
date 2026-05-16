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

// C9a §2: ApprovalClass → 中文 + 颜色（必须与 src/openakita/core/policy_v2/enums.py
// 的 ApprovalClass StrEnum 字面量逐字对齐——任何漂移都会导致 badge 静默不渲染）。
const APPROVAL_CLASS_LABELS: Record<string, { label: string; color: string }> = {
  // 只读类
  readonly_scoped:  { label: "局部只读",     color: "#10b981" },
  readonly_global:  { label: "全局只读",     color: "#22c55e" },
  readonly_search:  { label: "搜索",         color: "#06b6d4" },
  // 修改类
  mutating_scoped:  { label: "局部副作用",   color: "#f59e0b" },
  mutating_global:  { label: "全局副作用",   color: "#ea580c" },
  destructive:      { label: "破坏性操作",   color: "#dc2626" },
  // 执行类
  exec_low_risk:    { label: "低危执行",     color: "#3b82f6" },
  exec_capable:     { label: "高权执行",     color: "#dc2626" },
  // 控制 / 交互 / 网络
  control_plane:    { label: "控制面",       color: "#9333ea" },
  interactive:      { label: "交互式",       color: "#3b82f6" },
  network_out:      { label: "网络出站",     color: "#0891b2" },
  // 兜底
  unknown:          { label: "未分类",       color: "#6b7280" },
};

const TOOL_LABELS: Record<string, string> = {
  run_shell: "Shell 命令",
  run_powershell: "PowerShell 命令",
  write_file: "写入文件",
  edit_file: "编辑文件",
  delete_file: "删除文件",
  delegate_to_agent: "委托给 Agent",
  switch_mode: "切换模式",
};

const SOURCE_LABELS: Record<string, string> = {
  explicit_register_param: "显式注册参数",
  explicit_handler_attr: "工具处理器声明",
  skill_metadata: "Skill 元数据",
  mcp_annotation: "MCP 注解",
  plugin_prefix: "插件声明",
  heuristic_prefix: "工具名前缀推断",
  fallback_unknown: "兜底未知",
};

const ROLE_LABELS: Record<string, string> = {
  plan: "计划模式",
  ask: "问答模式",
  agent: "Agent 模式",
  coordinator: "协作编排模式",
};

const MODE_LABELS: Record<string, string> = {
  trust: "信任确认",
  default: "默认确认",
  accept_edits: "接受编辑",
  strict: "严格确认",
  dont_ask: "不询问",
};

function formatToolName(tool: string): string {
  const label = TOOL_LABELS[tool];
  return label ? `${label} (${tool})` : tool;
}

function humanizeArgs(tool: string, args: Record<string, unknown>): string {
  if ((tool === "run_shell" || tool === "run_powershell") && args.command) {
    return `即将执行命令：${args.command}`;
  }
  if ((tool === "write_file" || tool === "edit_file") && args.path) return `即将修改文件：${args.path}`;
  if (tool === "delete_file" && args.path) return `即将删除文件：${args.path}`;
  return JSON.stringify(args, null, 2);
}

type Decision = "allow_once" | "allow_session" | "allow_always" | "deny" | "sandbox";

export interface SecurityCloseInfo {
  decision: string;
  tool: string;
  command: string;
}

// C23 P2-2: ApprovalClass DecisionAction → 中文短标签 + 颜色（必须与
// src/openakita/core/policy_v2/enums.py 的 DecisionAction StrEnum 对齐）。
const ACTION_LABELS: Record<string, { label: string; color: string }> = {
  allow:   { label: "允许",  color: "#10b981" },
  confirm: { label: "确认",  color: "#f59e0b" },
  deny:    { label: "拒绝",  color: "#ef4444" },
  defer:   { label: "延期",  color: "#9333ea" },
};

// C23 F7: engine.py 决策链 step.name → 中文短标签。chain step name 是
// engine 内部英文常量（preflight / classify / matrix / safety_immune / ...）
// 直接渲染会让中文用户面对一堆英文标识符。这里给出对照表，
// 命中则展示中文，未命中（例如未来新加的 step）则保留原 name 不破坏渲染。
// 后端真相来源: src/openakita/core/policy_v2/engine.py 各 DecisionStep
// 的 name= 参数。
const STEP_LABELS: Record<string, string> = {
  engine_crash:              "引擎异常",
  preflight:                 "预检",
  classify:                  "分类",
  safety_immune:             "永不放行检查",
  owner_only:                "Owner 唯一性",
  channel_compat:            "信道兼容性",
  matrix:                    "矩阵决策",
  matrix_deny:               "矩阵拒绝",
  matrix_allow:              "矩阵放行",
  replay:                    "重放检查",
  trusted_path:              "可信路径",
  user_allowlist:            "用户白名单",
  death_switch:              "死亡开关",
  unattended:                "无人值守模式",
  finalize:                  "终决",
  intent_preflight:          "意图预检",
  intent_role_block:         "角色意图阻断",
  intent_trust_bypass:       "信任旁路",
  intent_clean:              "意图清扫",
  intent_risk:               "意图风险",
  approval_override_ignored: "覆盖未应用",
  approval_override_applied: "覆盖已应用",
};

function approvalClassLabel(value: string): string {
  return APPROVAL_CLASS_LABELS[value]?.label ?? value;
}

function formatPolicyReason(reason: string): string {
  if (!reason) return "";
  if (reason === "matrix says CONFIRM (no relax matched)") {
    return "策略矩阵要求确认（未命中重放授权、可信路径或用户白名单）";
  }
  if (reason === "matrix says ALLOW") return "策略矩阵允许执行";
  if (reason === "matrix says DENY") return "策略矩阵拒绝执行";
  if (reason === "security profile is off") return "安全方案已关闭，允许执行";
  if (reason === "death_switch active") return "只读保护已触发，阻止继续执行";
  if (reason.startsWith("safety_immune match:")) {
    return `命中绝对保护规则：${reason.slice("safety_immune match:".length).trim()}`;
  }
  if (reason.startsWith("engine_crash:")) {
    return `策略引擎异常：${reason.slice("engine_crash:".length).trim()}`;
  }
  return reason;
}

function formatDecisionNote(note: string): string {
  if (!note) return "";

  const classSource = note.match(/^class=([a-z_]+)\s+source=([a-z_]+)$/);
  if (classSource) {
    return `工具分类：${approvalClassLabel(classSource[1])}；来源：${SOURCE_LABELS[classSource[2]] ?? classSource[2]}`;
  }

  const roleMode = note.match(/^role=([a-z_]+)\s+mode=([a-z_]+)$/);
  if (roleMode) {
    return `会话角色：${ROLE_LABELS[roleMode[1]] ?? roleMode[1]}；确认模式：${MODE_LABELS[roleMode[2]] ?? roleMode[2]}`;
  }

  const tool = note.match(/^tool=([a-z_]+)$/);
  if (tool) return `工具：${formatToolName(tool[1])}`;

  const approvalClass = note.match(/^approval_class=([a-z_]+)$/);
  if (approvalClass) return `工具分类：${approvalClassLabel(approvalClass[1])}`;

  return formatPolicyReason(note);
}

export function SecurityConfirmModal({
  data, apiBase, onClose, timerRef, setData,
}: {
  data: {
    tool: string; args: Record<string, unknown>; reason: string;
    riskLevel: string; needsSandbox: boolean; toolId?: string; countdown: number;
    defaultOnTimeout?: string;
    // C9a §2: v2 字段（缺失时不渲染对应 UI 元素，向后兼容旧 backend）
    approvalClass?: string | null; policyVersion?: number; channel?: string;
    // C23 P2-2: 决策链。缺失或空时不渲染"决策依据"折叠区。
    decisionChain?: Array<{ name: string; action: string; note: string }>;
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
  // C23 P2-2: 默认折叠，避免把 modal 撑太大；用户主动 expand 才显示。
  const [showChain, setShowChain] = useState(false);

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
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ fontWeight: 700, fontSize: 16 }}>
                {t("chat.securityConfirmTitle", "安全确认")}
              </div>
              {/* C9a §2: approval_class badge (v2 字段；旧 backend 缺失时不渲染) */}
              {data.approvalClass && APPROVAL_CLASS_LABELS[data.approvalClass] && (
                <span
                  title={`policy_v2 ApprovalClass: ${data.approvalClass}`}
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: `${APPROVAL_CLASS_LABELS[data.approvalClass].color}1a`,
                    color: APPROVAL_CLASS_LABELS[data.approvalClass].color,
                    border: `1px solid ${APPROVAL_CLASS_LABELS[data.approvalClass].color}55`,
                    letterSpacing: "0.02em",
                  }}
                >
                  {APPROVAL_CLASS_LABELS[data.approvalClass].label}
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, opacity: 0.6, display: "flex", gap: 8, alignItems: "center" }}>
              <span>
                {t("chat.securityRiskLevel", "风险等级")}:{" "}
                <span style={{ color: riskColor, fontWeight: 700 }}>
                  {RISK_LABELS[data.riskLevel] || data.riskLevel}
                </span>
              </span>
              {/* C9a §2: 渠道标识（IM 用户更需要知道是否是远端来源） */}
              {data.channel === "im" && (
                <span style={{ opacity: 0.7 }}>· {t("chat.securityChannelIm", "IM 渠道")}</span>
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
            <div style={{ fontSize: 13, lineHeight: 1.5 }} title={data.reason}>
              {formatPolicyReason(data.reason)}
            </div>
          </div>
        </div>

        <div style={{ fontSize: 13, marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            {t("chat.securityTool", "工具")}: <code title={data.tool}>{formatToolName(data.tool)}</code>
          </div>
          <pre style={{
            margin: 0, fontSize: 11, maxHeight: 120, overflow: "auto",
            padding: "8px 10px", borderRadius: 8, background: "var(--panel2)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {humanizeArgs(data.tool, data.args)}
          </pre>
        </div>

        {/* C23 P2-2: decision_chain 折叠区。plan C9 要求把"为什么会要确认"
            的引擎判断链展开给用户看。默认折叠 (showChain=false) 不打扰；
            点击 disclosure 展开后逐行渲染 name / action badge / note。
            缺失或空 chain 时整段不渲染（向后兼容旧 backend）。 */}
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
                  const actionMeta = ACTION_LABELS[step.action] || { label: step.action, color: "#6b7280" };
                  const stepLabel = STEP_LABELS[step.name] || step.name;
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
                      {step.note && (
                        <span
                          title={step.note}
                          style={{ marginLeft: 6, opacity: 0.75, wordBreak: "break-word" }}
                        >
                          {formatDecisionNote(step.note)}
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
          <button
            onClick={() => handleDecision("deny")}
            style={{
              ...btnBase,
              background: "transparent", border: "1px solid var(--line)",
              color: "var(--text)",
            }}
          >
            {t("chat.securityDeny", "拒绝")} ({data.countdown}s)
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
                {t("chat.securitySandbox", "沙箱运行")}
              </button>
            )}
            <button
              onClick={() => handleDecision("allow_once")}
              style={{ ...btnBase, background: riskColor, color: "#fff" }}
            >
              {t("chat.securityAllowOnce", "允许一次")}
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
                    {t("chat.securityAllowSession", "本次会话允许")}
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
                      {t("chat.securityAllowAlways", "始终允许")}
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
