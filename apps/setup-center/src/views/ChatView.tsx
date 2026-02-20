// ─── ChatView: 完整 AI 聊天页面 ───
// 支持流式 MD 渲染、思考内容折叠、Plan/Todo、斜杠命令、多模态、多 Agent、端点选择

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { setThemePref } from "../theme";
import type { Theme } from "../theme";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type {
  ChatMessage,
  ChatConversation,
  ChatToolCall,
  ChatPlan,
  ChatPlanStep,
  ChatAskUser,
  ChatAskQuestion,
  ChatAttachment,
  ChatArtifact,
  SlashCommand,
  EndpointSummary,
  ChainGroup,
  ChainToolCall,
  ChainEntry,
  ChainSummaryItem,
  ChatDisplayMode,
} from "../types";
import { genId, formatTime, formatDate } from "../utils";
import {
  IconSend, IconPaperclip, IconMic, IconStopCircle,
  IconPlan, IconPlus, IconMenu, IconStop, IconX,
  IconCheck, IconLoader, IconCircle, IconPlay, IconMinus,
  IconChevronDown, IconChevronUp, IconMessageCircle, IconChevronRight,
  IconImage, IconRefresh, IconClipboard, IconTrash, IconZap,
  IconMask, IconBot, IconUsers, IconHelp, IconEdit, IconDownload,
} from "../icons";

// ─── 排队消息类型 ───
type QueuedMessage = {
  id: string;
  text: string;
  timestamp: number;
};

// ─── 从后端 chain_summary 重建前端 ChainGroup ───
function buildChainFromSummary(summary: ChainSummaryItem[]): ChainGroup[] {
  return summary.map((s) => {
    const entries: ChainEntry[] = [];
    if (s.thinking_preview) {
      entries.push({ kind: "thinking", content: s.thinking_preview });
    }
    for (const t of s.tools) {
      entries.push({
        kind: "tool_end",
        toolId: `restored-${s.iteration}-${t.name}`,
        tool: t.name,
        result: t.input_preview,
        status: "done",
      });
    }
    if (s.context_compressed) {
      entries.push({
        kind: "compressed",
        beforeTokens: s.context_compressed.before_tokens,
        afterTokens: s.context_compressed.after_tokens,
      });
    }
    return {
      iteration: s.iteration,
      entries,
      durationMs: s.thinking_duration_ms,
      hasThinking: !!s.thinking_preview,
      collapsed: true,
      toolCalls: s.tools.map((t) => ({
        toolId: `restored-${s.iteration}-${t.name}`,
        tool: t.name,
        args: {},
        result: t.input_preview,
        status: "done" as const,
        description: t.input_preview,
      })),
    };
  });
}

/** 用后端数据补全本地消息中缺失的 content / thinkingChain */
function patchMessagesWithBackend(
  localMsgs: ChatMessage[],
  backendMsgs: { role: string; content: string; chain_summary?: ChainSummaryItem[] }[],
): ChatMessage[] {
  const backendAssistant = backendMsgs.filter((m) => m.role === "assistant");
  let aIdx = 0;
  let changed = false;
  const patched = localMsgs.map((m) => {
    if (m.role !== "assistant") return m;
    const backend = backendAssistant[aIdx++];
    if (!backend) return m;

    const patches: Partial<ChatMessage> = {};

    if (!m.content && backend.content) {
      patches.content = backend.content;
    }

    const hasBrokenChain = m.thinkingChain?.some((g) => !g.entries.length && !g.durationMs);
    if (backend.chain_summary?.length && (!m.thinkingChain?.length || hasBrokenChain)) {
      patches.thinkingChain = buildChainFromSummary(backend.chain_summary);
    }

    if (m.thinkingChain && !patches.thinkingChain) {
      const cleaned = m.thinkingChain.filter((g) => g.entries.length > 0 || g.durationMs);
      if (cleaned.length !== m.thinkingChain.length) {
        patches.thinkingChain = cleaned.length > 0 ? cleaned : undefined;
      }
    }

    if (Object.keys(patches).length > 0) {
      changed = true;
      return { ...m, ...patches };
    }
    return m;
  });
  return changed ? patched : localMsgs;
}

// ─── SSE 事件处理 ───

type StreamEvent =
  | { type: "heartbeat" }
  | { type: "iteration_start"; iteration: number }
  | { type: "context_compressed"; before_tokens: number; after_tokens: number }
  | { type: "thinking_start" }
  | { type: "thinking_delta"; content: string }
  | { type: "thinking_end"; duration_ms?: number; has_thinking?: boolean }
  | { type: "chain_text"; content: string }
  | { type: "text_delta"; content: string }
  | { type: "tool_call_start"; tool: string; args: Record<string, unknown>; id?: string }
  | { type: "tool_call_end"; tool: string; result: string; id?: string; is_error?: boolean }
  | { type: "plan_created"; plan: ChatPlan }
  | { type: "plan_step_updated"; stepId?: string; stepIdx?: number; status: string }
  | { type: "plan_completed" }
  | { type: "plan_cancelled" }
  | { type: "ask_user"; question: string; options?: { id: string; label: string }[]; allow_multiple?: boolean; questions?: { id: string; prompt: string; options?: { id: string; label: string }[]; allow_multiple?: boolean }[] }
  | { type: "user_insert"; content: string }
  | { type: "agent_switch"; agentName: string; reason: string }
  | { type: "artifact"; artifact_type: string; file_url: string; path: string; name: string; caption: string; size?: number }
  | { type: "ui_preference"; theme?: string; language?: string }
  | { type: "error"; message: string }
  | { type: "done"; usage?: { input_tokens: number; output_tokens: number; total_tokens?: number; context_tokens?: number; context_limit?: number } };

// ─── 思维链工具函数 ───

/** 提取文件名 */
function basename(path: string): string {
  if (!path) return "";
  return path.replace(/\\/g, "/").split("/").pop() || path;
}

/** 将原始工具调用转为人类可读描述 */
function formatToolDescription(tool: string, args: Record<string, unknown>): string {
  switch (tool) {
    case "read_file":
      return `Read ${basename(String(args.path || args.file || ""))}`;
    case "grep": case "search": case "ripgrep": case "search_files":
      return `Grepped ${String(args.pattern || args.query || "").slice(0, 60)}${args.path ? ` in ${basename(String(args.path))}` : ""}`;
    case "web_search":
      return `Searched: "${String(args.query || "").slice(0, 50)}"`;
    case "execute_code": case "run_code":
      return "Executed code";
    case "create_plan":
      return `Created plan: ${String(args.task_summary || "").slice(0, 40)}`;
    case "update_plan_step":
      return `Updated plan step ${args.step_index ?? ""}`;
    case "write_file":
      return `Wrote ${basename(String(args.path || ""))}`;
    case "edit_file":
      return `Edited ${basename(String(args.path || ""))}`;
    case "list_files": case "list_dir":
      return `Listed ${basename(String(args.path || args.directory || "."))}`;
    case "browser_navigate":
      return `Navigated to ${String(args.url || "").slice(0, 50)}`;
    case "browser_screenshot":
      return "Took screenshot";
    case "ask_user":
      return `Asked: "${String(args.question || "").slice(0, 40)}"`;
    default:
      return `${tool}(${Object.keys(args).slice(0, 3).join(", ")})`;
  }
}

/** 自动生成组摘要 */
function generateGroupSummary(tools: ChainToolCall[]): string {
  const reads = tools.filter(t => ["read_file"].includes(t.tool)).length;
  const searches = tools.filter(t => ["grep", "search", "ripgrep", "search_files", "web_search"].includes(t.tool)).length;
  const writes = tools.filter(t => ["write_file", "edit_file"].includes(t.tool)).length;
  const others = tools.length - reads - searches - writes;
  const parts: string[] = [];
  if (reads) parts.push(`${reads} file${reads > 1 ? "s" : ""}`);
  if (searches) parts.push(`${searches} search${searches > 1 ? "es" : ""}`);
  if (writes) parts.push(`${writes} write${writes > 1 ? "s" : ""}`);
  if (others) parts.push(`${others} other${others > 1 ? "s" : ""}`);
  return parts.length > 0 ? `Explored ${parts.join(", ")}` : "";
}

// ─── 子组件 ───

/** ThinkingBlock: 旧版组件保留做 bubble 模式向后兼容 */
function ThinkingBlock({ content, defaultOpen }: { content: string; defaultOpen?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="thinkingBlock">
      <div
        className="thinkingHeader"
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 0", userSelect: "none" }}
      >
        <span style={{ fontSize: 12, opacity: 0.5, transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s", display: "inline-flex", alignItems: "center" }}><IconChevronRight size={12} /></span>
        <span style={{ fontWeight: 700, fontSize: 13, opacity: 0.6 }}>{t("chat.thinkingBlock")}</span>
      </div>
      {open && (
        <div style={{ padding: "8px 12px", background: "rgba(124,58,237,0.04)", borderRadius: 10, fontSize: 13, lineHeight: 1.6, opacity: 0.75, whiteSpace: "pre-wrap" }}>
          {content}
        </div>
      )}
    </div>
  );
}

/** Single tool call detail (used inside expanded group) */
function ToolCallDetail({ tc }: { tc: ChatToolCall }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const statusIcon =
    tc.status === "done" ? <IconCheck size={14} /> :
    tc.status === "error" ? <IconX size={14} /> :
    tc.status === "running" ? <IconLoader size={14} /> :
    <IconCircle size={10} />;
  const statusColor = tc.status === "done" ? "var(--ok)" : tc.status === "error" ? "var(--danger)" : "var(--brand)";
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", background: "rgba(14,165,233,0.03)", userSelect: "none" }}
      >
        <span style={{ color: statusColor, fontWeight: 800, display: "inline-flex", alignItems: "center" }}>{statusIcon}</span>
        <span style={{ fontWeight: 600, fontSize: 12 }}>{tc.tool}</span>
        <span style={{ fontSize: 10, opacity: 0.4, marginLeft: "auto" }}>{open ? t("chat.collapse") : t("chat.expand")}</span>
      </div>
      {open && (
        <div style={{ padding: "6px 10px", fontSize: 12, background: "var(--panel)" }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{t("chat.args")}</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>
            {JSON.stringify(tc.args, null, 2)}
          </pre>
          {tc.result != null && (
            <>
              <div style={{ fontWeight: 700, marginTop: 8, marginBottom: 4 }}>{t("chat.result")}</div>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11, maxHeight: 200, overflow: "auto" }}>
                {typeof tc.result === "string" ? tc.result : JSON.stringify(tc.result, null, 2)}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Grouped tool calls: collapsed into one line by default, expandable (bubble mode legacy) */
function ToolCallsGroup({ toolCalls }: { toolCalls: ChatToolCall[] }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  const doneCount = toolCalls.filter((tc) => tc.status === "done").length;
  const errorCount = toolCalls.filter((tc) => tc.status === "error").length;
  const runningCount = toolCalls.filter((tc) => tc.status === "running").length;
  const allDone = doneCount === toolCalls.length;
  const hasError = errorCount > 0;
  const summaryColor = hasError ? "var(--danger)" : runningCount > 0 ? "var(--brand)" : "var(--ok)";
  const summaryIcon = hasError ? <IconX size={14} /> : runningCount > 0 ? <IconLoader size={14} /> : <IconCheck size={14} />;
  const toolNames = toolCalls.map((tc) => tc.tool);
  // Deduplicate and show counts
  const nameCounts: Record<string, number> = {};
  for (const n of toolNames) nameCounts[n] = (nameCounts[n] || 0) + 1;
  const nameLabels = Object.entries(nameCounts).map(([n, c]) => c > 1 ? `${n} ×${c}` : n);
  const summaryText = nameLabels.join(", ");

  return (
    <div style={{ margin: "6px 0", border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", background: "rgba(14,165,233,0.04)", userSelect: "none" }}
      >
        <span style={{ color: summaryColor, fontWeight: 800, display: "inline-flex", alignItems: "center" }}>{summaryIcon}</span>
        <span style={{ fontWeight: 700, fontSize: 13 }}>
          {t("chat.toolCallLabel")}{toolCalls.length > 1 ? `${toolCalls.length} ` : ""}{toolCalls.length === 1 ? toolCalls[0].tool : ""}
        </span>
        {toolCalls.length > 1 && (
          <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
            {summaryText}
          </span>
        )}
        <span style={{ fontSize: 11, opacity: 0.5, marginLeft: "auto", flexShrink: 0 }}>{expanded ? t("chat.collapse") : t("chat.expand")}</span>
      </div>
      {expanded && (
        <div style={{ padding: "6px 8px", display: "flex", flexDirection: "column", gap: 4, background: "var(--panel)" }}>
          {toolCalls.map((tc, i) => (
            <ToolCallDetail key={i} tc={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── ThinkingChain 组件 (Cursor 风格叙事流思维链) ───

/** 工具结果折叠显示 */
function ToolResultBlock({ result }: { result: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!result) return null;
  const safeResult = typeof result === "string" ? result : JSON.stringify(result, null, 2);
  const isShort = safeResult.length < 120;
  if (isShort) return <span className="chainToolResultInline">{safeResult}</span>;
  return (
    <span className="chainToolResultCollapsible">
      <span className="chainToolResultToggle" onClick={() => setExpanded(v => !v)}>
        {expanded ? "收起" : "查看详情"} <IconChevronRight size={9} />
      </span>
      {expanded && <pre className="chainToolResult">{safeResult}</pre>}
    </span>
  );
}

/** 叙事流单条目渲染 */
function ChainEntryLine({ entry, onSkipStep }: { entry: ChainEntry; onSkipStep?: () => void }) {
  switch (entry.kind) {
    case "thinking":
      return (
        <div className="chainNarrThinking">
          <span className="chainNarrThinkingLabel">thinking</span>
          <span className="chainNarrThinkingText">{entry.content}</span>
        </div>
      );
    case "text":
      return <div className="chainNarrText">{entry.content}</div>;
    case "tool_start": {
      const isRunning = entry.status === "running";
      const tsIcon = entry.status === "error"
        ? <IconX size={11} />
        : entry.status === "done"
          ? <IconCheck size={11} />
          : <IconLoader size={11} className="chainSpinner" />;
      return (
        <div className="chainNarrToolStart">
          {tsIcon}
          <span className="chainNarrToolName">{entry.description || entry.tool}</span>
          {isRunning && onSkipStep && (
            <button
              className="chainToolSkipBtn"
              onClick={(e) => { e.stopPropagation(); onSkipStep(); }}
              title="Skip this step"
            >
              <IconX size={10} />
            </button>
          )}
        </div>
      );
    }
    case "tool_end": {
      const isError = entry.status === "error";
      const icon = isError ? <IconX size={11} /> : <IconCheck size={11} />;
      const cls = isError ? "chainNarrToolEnd chainNarrToolError" : "chainNarrToolEnd";
      return (
        <div className={cls}>
          {icon}
          <ToolResultBlock result={entry.result} />
        </div>
      );
    }
    case "compressed":
      return (
        <div className="chainNarrCompressed">
          上下文压缩: {Math.round(entry.beforeTokens / 1000)}k → {Math.round(entry.afterTokens / 1000)}k tokens
        </div>
      );
    default:
      return null;
  }
}

/** 单个迭代组: 叙事流模式 */
function ChainGroupItem({ group, onToggle, isLast, streaming, onSkipStep }: {
  group: ChainGroup;
  onToggle: () => void;
  isLast: boolean;
  streaming: boolean;
  onSkipStep?: () => void;
}) {
  const { t } = useTranslation();
  const isActive = isLast && streaming;
  const durMs = group.durationMs;
  const durationSec = durMs ? (durMs / 1000).toFixed(1) : null;
  const hasContent = group.entries.length > 0;

  // 没有任何 entries 且不活跃 —— 简洁行
  if (!hasContent && !isActive) {
    return (
      <div className="chainGroup chainGroupCompact">
        <div className="chainProcessedLine">
          <IconCheck size={11} />
          <span>{t("chat.processed", { seconds: durationSec || "0" })}</span>
        </div>
      </div>
    );
  }

  const showContent = !group.collapsed || isActive;
  const headerLabel = isActive
    ? t("chat.processing")
    : group.hasThinking
      ? t("chat.thoughtFor", { seconds: durationSec || "0" })
      : t("chat.processed", { seconds: durationSec || "0" });

  return (
    <div className={`chainGroup ${group.collapsed && !isActive ? "chainGroupCollapsed" : ""}`}>
      <div className="chainThinkingHeader" onClick={onToggle}>
        <span className="chainChevron" style={{ transform: showContent ? "rotate(90deg)" : "rotate(0deg)" }}>
          <IconChevronRight size={11} />
        </span>
        <span className="chainThinkingLabel">{headerLabel}</span>
        {isActive && <IconLoader size={11} className="chainSpinner" />}
      </div>
      {showContent && (
        <div className="chainNarrFlow">
          {group.entries.map((entry, i) => (
            <ChainEntryLine key={i} entry={entry} onSkipStep={onSkipStep} />
          ))}
          {isActive && group.entries.length > 0 && (
            <div className="chainNarrCursor" />
          )}
        </div>
      )}
    </div>
  );
}

/** 完整思维链组件 */
function ThinkingChain({ chain, streaming, showChain, onSkipStep }: {
  chain: ChainGroup[];
  streaming: boolean;
  showChain: boolean;
  onSkipStep?: () => void;
}) {
  const { t } = useTranslation();
  const [localChain, setLocalChain] = useState(chain);
  const chainEndRef = useRef<HTMLDivElement>(null);

  // 同步外部 chain 数据，但保留用户手动修改的 collapsed 状态
  useEffect(() => {
    setLocalChain(prev => {
      const prevMap = new Map(prev.map(g => [g.iteration, g.collapsed]));
      return chain.map(g => ({
        ...g,
        collapsed: prevMap.has(g.iteration) ? prevMap.get(g.iteration)! : g.collapsed,
      }));
    });
  }, [chain]);

  // 流式输出时自动滚到底部
  useEffect(() => {
    if (streaming && chainEndRef.current) {
      chainEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [chain, streaming]);

  if (!showChain || !localChain || localChain.length === 0) return null;

  // 全部折叠时显示摘要行
  const allCollapsed = localChain.every(g => g.collapsed) && !streaming;
  if (allCollapsed) {
    const totalSteps = localChain.reduce((n, g) => n + g.entries.length, 0);
    return (
      <div
        className="chainCollapsedSummary"
        onClick={() => setLocalChain(prev => prev.map(g => ({ ...g, collapsed: false })))}
      >
        <IconChevronRight size={11} />
        <span>{t("chat.chainCollapsed", { count: totalSteps })}</span>
      </div>
    );
  }

  return (
    <div className="thinkingChain">
      {localChain.map((group, idx) => (
        <ChainGroupItem
          key={group.iteration}
          group={group}
          isLast={idx === localChain.length - 1}
          streaming={streaming}
          onSkipStep={onSkipStep}
          onToggle={() => {
            setLocalChain(prev => prev.map((g, i) =>
              i === idx ? { ...g, collapsed: !g.collapsed } : g
            ));
          }}
        />
      ))}
      <div ref={chainEndRef} />
    </div>
  );
}

/** 浮动 Plan 进度条 —— 贴在输入框上方，默认折叠只显示当前步骤 */
function FloatingPlanBar({ plan }: { plan: ChatPlan }) {
  const [expanded, setExpanded] = useState(false);
  const completed = plan.steps.filter((s) => s.status === "completed").length;
  const total = plan.steps.length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  const allDone = completed === total && total > 0;

  // 当前正在进行的步骤（优先 in_progress，否则取第一个 pending）
  const activeStep = plan.steps.find((s) => s.status === "in_progress")
    || plan.steps.find((s) => s.status === "pending");
  const activeIdx = activeStep ? plan.steps.indexOf(activeStep) : -1;
  const activeDesc = activeStep
    ? (typeof activeStep.description === "string" ? activeStep.description : JSON.stringify(activeStep.description))
    : null;

  return (
    <div className="floatingPlanBar">
      {/* 折叠头部：可点击展开 */}
      <div className="floatingPlanHeader" onClick={() => setExpanded((v) => !v)}>
        <div className="floatingPlanHeaderLeft">
          <IconClipboard size={14} style={{ opacity: 0.6 }} />
          <span className="floatingPlanTitle">
            {typeof plan.taskSummary === "string" ? plan.taskSummary : JSON.stringify(plan.taskSummary)}
          </span>
        </div>
        <div className="floatingPlanHeaderRight">
          <span className="floatingPlanProgress">{completed}/{total}</span>
          <span className="floatingPlanChevron" style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}>
            <IconChevronDown size={14} />
          </span>
        </div>
      </div>

      {/* 进度条 */}
      <div className="floatingPlanProgressBar">
        <div className="floatingPlanProgressFill" style={{ width: `${pct}%` }} />
      </div>

      {/* 折叠态：只显示当前活跃步骤 */}
      {!expanded && activeStep && !allDone && (
        <div className="floatingPlanActive">
          <span className="floatingPlanActiveIcon"><IconPlay size={11} /></span>
          <span className="floatingPlanActiveText">{activeIdx + 1}/{total} {activeDesc}</span>
        </div>
      )}
      {!expanded && allDone && (
        <div className="floatingPlanActive floatingPlanDone">
          <span className="floatingPlanActiveIcon"><IconCheck size={12} /></span>
          <span className="floatingPlanActiveText">全部完成</span>
        </div>
      )}

      {/* 展开态：完整步骤列表 */}
      {expanded && (
        <div className="floatingPlanSteps">
          {plan.steps.map((step, idx) => (
            <FloatingPlanStepItem key={step.id || idx} step={step} idx={idx} />
          ))}
        </div>
      )}
    </div>
  );
}

function FloatingPlanStepItem({ step, idx }: { step: ChatPlanStep; idx: number }) {
  const icon =
    step.status === "completed" ? <IconCheck size={13} /> :
    step.status === "in_progress" ? <IconPlay size={11} /> :
    step.status === "skipped" ? <IconMinus size={13} /> :
    step.status === "cancelled" ? <IconX size={13} /> :
    step.status === "failed" ? <IconX size={13} /> :
    <IconCircle size={9} />;
  const color =
    step.status === "completed" ? "rgba(16,185,129,1)"
    : step.status === "in_progress" ? "var(--brand)"
    : step.status === "failed" ? "rgba(239,68,68,1)"
    : step.status === "cancelled" ? "var(--muted)"
    : step.status === "skipped" ? "var(--muted)" : "var(--muted)";
  const descText = typeof step.description === "string" ? step.description : JSON.stringify(step.description);
  const resultText = step.result
    ? (typeof step.result === "string" ? step.result : JSON.stringify(step.result))
    : null;
  return (
    <div className={`floatingPlanStepRow ${step.status === "in_progress" ? "floatingPlanStepActive" : ""}`}>
      <span className="floatingPlanStepIcon" style={{ color }}>{icon}</span>
      <div className="floatingPlanStepContent">
        <span style={{ opacity: step.status === "skipped" || step.status === "cancelled" ? 0.5 : 1 }}>{idx + 1}. {descText}</span>
        {resultText && <div className="floatingPlanStepResult">{resultText}</div>}
      </div>
    </div>
  );
}

/** 单个问题选择器（单选/多选/纯输入） */
function AskQuestionItem({
  question,
  selected,
  onSelect,
  otherText,
  onOtherText,
  showOther,
  onToggleOther,
  letterOffset,
}: {
  question: ChatAskQuestion;
  selected: Set<string>;
  onSelect: (optId: string) => void;
  otherText: string;
  onOtherText: (v: string) => void;
  showOther: boolean;
  onToggleOther: () => void;
  letterOffset?: number;
}) {
  const { t } = useTranslation();
  const optionLetters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const hasOptions = question.options && question.options.length > 0;
  const isMulti = question.allow_multiple === true;

  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: "var(--fg, #333)" }}>
        {question.prompt}
        {isMulti && <span style={{ fontWeight: 400, fontSize: 12, opacity: 0.55, marginLeft: 6 }}>({t("chat.multiSelect", "可多选")})</span>}
      </div>
      {hasOptions ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {question.options!.map((opt, idx) => {
            const isSelected = selected.has(opt.id);
            return (
              <button
                key={opt.id}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "7px 14px", borderRadius: 8,
                  border: isSelected ? "1.5px solid rgba(124,58,237,0.55)" : "1px solid rgba(124,58,237,0.18)",
                  background: isSelected ? "rgba(124,58,237,0.10)" : "var(--panel)",
                  cursor: "pointer", fontSize: 13, textAlign: "left",
                  transition: "all 0.15s",
                }}
                onMouseEnter={(e) => { if (!isSelected) { e.currentTarget.style.background = "rgba(124,58,237,0.06)"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.35)"; } }}
                onMouseLeave={(e) => { if (!isSelected) { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.18)"; } }}
                onClick={() => onSelect(opt.id)}
              >
                <span style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 22, height: 22, borderRadius: isMulti ? 4 : 11, flexShrink: 0,
                  background: isSelected ? "rgba(124,58,237,0.85)" : "rgba(124,58,237,0.10)",
                  color: isSelected ? "#fff" : "rgba(124,58,237,0.8)",
                  fontSize: 11, fontWeight: 700, transition: "all 0.15s",
                }}>
                  {isSelected ? (isMulti ? "✓" : "●") : (optionLetters[(letterOffset || 0) + idx] || String(idx + 1))}
                </span>
                <span>{opt.label}</span>
              </button>
            );
          })}
          {/* OTHER / 手动输入 */}
          {!showOther ? (
            <button
              style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "7px 14px", borderRadius: 8,
                border: "1px dashed rgba(124,58,237,0.18)",
                background: "transparent",
                cursor: "pointer", fontSize: 13, textAlign: "left",
                transition: "all 0.15s", opacity: 0.55,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.opacity = "1"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.4)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.opacity = "0.55"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.18)"; }}
              onClick={onToggleOther}
            >
              <span style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 22, height: 22, borderRadius: isMulti ? 4 : 11, flexShrink: 0,
                background: "rgba(0,0,0,0.04)", color: "rgba(0,0,0,0.35)",
                fontSize: 11, fontWeight: 700,
              }}>…</span>
              <span>{t("chat.otherOption", "其他（手动输入）")}</span>
            </button>
          ) : (
            <input
              autoFocus
              value={otherText}
              onChange={(e) => onOtherText(e.target.value)}
              placeholder={t("chat.askPlaceholder")}
              style={{ fontSize: 13, padding: "7px 12px", borderRadius: 8, border: "1px solid rgba(124,58,237,0.25)", outline: "none" }}
              onKeyDown={(e) => { if (e.key === "Escape") onToggleOther(); }}
            />
          )}
        </div>
      ) : (
        <input
          value={otherText}
          onChange={(e) => onOtherText(e.target.value)}
          placeholder={t("chat.askPlaceholder")}
          style={{ width: "100%", fontSize: 13, padding: "7px 12px", borderRadius: 8, border: "1px solid rgba(124,58,237,0.25)", outline: "none", boxSizing: "border-box" }}
        />
      )}
    </div>
  );
}

function AskUserBlock({ ask, onAnswer }: { ask: ChatAskUser; onAnswer: (answer: string) => void }) {
  const { t } = useTranslation();

  // 将 ask 规范化为 questions 数组（兼容旧的单问题格式）
  const normalizedQuestions: ChatAskQuestion[] = useMemo(() => {
    if (ask.questions && ask.questions.length > 0) return ask.questions;
    // 兼容旧格式：单问题 + 可选 options
    return [{
      id: "__single__",
      prompt: ask.question,
      options: ask.options,
      allow_multiple: false,
    }];
  }, [ask]);

  const isSingle = normalizedQuestions.length === 1;

  // 每个问题的选中状态 { questionId -> Set<optionId> }
  const [selections, setSelections] = useState<Record<string, Set<string>>>(() => {
    const init: Record<string, Set<string>> = {};
    normalizedQuestions.forEach((q) => { init[q.id] = new Set(); });
    return init;
  });
  // 每个问题的"其他"文本
  const [otherTexts, setOtherTexts] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    normalizedQuestions.forEach((q) => { init[q.id] = ""; });
    return init;
  });
  // 是否展开"其他"输入
  const [showOthers, setShowOthers] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    normalizedQuestions.forEach((q) => { init[q.id] = !(q.options && q.options.length > 0); });
    return init;
  });

  const handleSelect = useCallback((qId: string, optId: string, isMulti: boolean) => {
    setSelections((prev) => {
      const s = new Set(prev[qId]);
      if (isMulti) {
        if (s.has(optId)) s.delete(optId); else s.add(optId);
      } else {
        // 单选：如果已选中当前项则取消，否则替换
        if (s.has(optId)) {
          s.clear();
        } else {
          s.clear();
          s.add(optId);
        }
        // 单选 + 单问题：直接提交
        if (isSingle && s.size > 0) {
          onAnswer(optId);
          return prev;
        }
      }
      return { ...prev, [qId]: s };
    });
  }, [isSingle, onAnswer]);

  const handleSubmit = useCallback(() => {
    if (isSingle) {
      const q = normalizedQuestions[0];
      const sel = selections[q.id];
      const other = otherTexts[q.id]?.trim();
      if (sel && sel.size > 0) {
        const arr = Array.from(sel);
        if (other) arr.push(`OTHER:${other}`);
        onAnswer(q.allow_multiple ? arr.join(",") : arr[0]);
      } else if (other) {
        onAnswer(other);
      }
      return;
    }
    // 多问题：返回 JSON
    const result: Record<string, string | string[]> = {};
    normalizedQuestions.forEach((q) => {
      const sel = selections[q.id];
      const other = otherTexts[q.id]?.trim();
      const arr = sel ? Array.from(sel) : [];
      if (other) arr.push(`OTHER:${other}`);
      if (arr.length === 0 && !other) return;
      result[q.id] = q.allow_multiple ? arr : (arr[0] || other || "");
    });
    onAnswer(JSON.stringify(result));
  }, [isSingle, normalizedQuestions, selections, otherTexts, onAnswer]);

  // ─── 已回答状态 ───
  if (ask.answered) {
    const displayAnswer = (() => {
      // 尝试解析 JSON（多问题）
      try {
        const parsed = JSON.parse(ask.answer || "");
        if (typeof parsed === "object" && !Array.isArray(parsed)) {
          return normalizedQuestions.map((q) => {
            const val = parsed[q.id];
            if (!val) return null;
            const vals = Array.isArray(val) ? val : [val];
            const labels = vals.map((v: string) => {
              if (v.startsWith("OTHER:")) return v.slice(6);
              const opt = q.options?.find((o) => o.id === v);
              return opt ? opt.label : v;
            });
            return `${q.prompt}: ${labels.join(", ")}`;
          }).filter(Boolean).join(" | ");
        }
      } catch { /* not JSON, fall through */ }
      // 单问题：查找选项标签
      const answeredOpt = ask.options?.find((o) => o.id === ask.answer);
      return answeredOpt ? answeredOpt.label : ask.answer;
    })();
    return (
      <div style={{ margin: "8px 0", padding: "10px 14px", borderRadius: 10, background: "rgba(14,165,233,0.06)", border: "1px solid rgba(14,165,233,0.15)" }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>{ask.question}</div>
        <div style={{ fontSize: 13, opacity: 0.7 }}>{t("chat.answered")}{displayAnswer}</div>
      </div>
    );
  }

  // ─── 未回答状态 ───
  // 判断是否有任何内容可以提交
  const canSubmit = normalizedQuestions.some((q) => {
    const sel = selections[q.id];
    const other = otherTexts[q.id]?.trim();
    return (sel && sel.size > 0) || !!other;
  });

  return (
    <div style={{ margin: "8px 0", padding: "12px 14px", borderRadius: 12, background: "rgba(124,58,237,0.04)", border: "1px solid rgba(124,58,237,0.16)" }}>
      {/* 总标题（多问题时显示，单问题时标题在 AskQuestionItem 里） */}
      {!isSingle && (
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: "var(--fg, #333)" }}>{ask.question}</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {normalizedQuestions.map((q) => (
          <AskQuestionItem
            key={q.id}
            question={q}
            selected={selections[q.id] || new Set()}
            onSelect={(optId) => handleSelect(q.id, optId, q.allow_multiple === true)}
            otherText={otherTexts[q.id] || ""}
            onOtherText={(v) => setOtherTexts((prev) => ({ ...prev, [q.id]: v }))}
            showOther={showOthers[q.id] || false}
            onToggleOther={() => setShowOthers((prev) => ({ ...prev, [q.id]: !prev[q.id] }))}
          />
        ))}
      </div>
      {/* 多问题或多选时需要提交按钮 */}
      {(!isSingle || normalizedQuestions.some((q) => q.allow_multiple)) && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
          <button
            className="btnPrimary"
            disabled={!canSubmit}
            onClick={handleSubmit}
            style={{ fontSize: 13, padding: "7px 22px", opacity: canSubmit ? 1 : 0.4, cursor: canSubmit ? "pointer" : "not-allowed" }}
          >
            {t("chat.submitAnswer", "提交")}
          </button>
        </div>
      )}
    </div>
  );
}

function AttachmentPreview({ att, onRemove }: { att: ChatAttachment; onRemove?: () => void }) {
  if (att.type === "image" && att.previewUrl) {
    return (
      <div style={{ position: "relative", display: "inline-block" }}>
        <img src={att.previewUrl} alt={att.name} style={{ width: 80, height: 80, objectFit: "cover", display: "block", borderRadius: 10, border: "1px solid var(--line)" }} />
        {onRemove && (
          <button
            onClick={onRemove}
            style={{
              position: "absolute", top: -6, right: -6,
              width: 22, height: 22, borderRadius: 11,
              border: "2px solid #fff", background: "var(--danger)", color: "#fff",
              fontSize: 11, cursor: "pointer", display: "grid", placeItems: "center",
              boxShadow: "0 1px 4px rgba(0,0,0,0.18)", zIndex: 2, padding: 0, lineHeight: 1,
            }}
          >
            <IconX size={11} />
          </button>
        )}
      </div>
    );
  }
  const icon = att.type === "voice" ? <IconMic size={14} /> : att.type === "video" ? <IconPlay size={14} /> : att.type === "image" ? <IconImage size={14} /> : <IconPaperclip size={14} />;
  const sizeStr = att.size ? `${(att.size / 1024).toFixed(1)} KB` : "";
  return (
    <div style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 28px 6px 10px", borderRadius: 10, border: "1px solid var(--line)", fontSize: 12 }}>
      {onRemove && (
        <button
          onClick={onRemove}
          style={{
            position: "absolute", top: -6, right: -6,
            width: 22, height: 22, borderRadius: 11,
            border: "2px solid #fff", background: "var(--danger)", color: "#fff",
            fontSize: 11, cursor: "pointer", display: "grid", placeItems: "center",
            boxShadow: "0 1px 4px rgba(0,0,0,0.18)", zIndex: 2, padding: 0, lineHeight: 1,
          }}
        >
          <IconX size={11} />
        </button>
      )}
      <span style={{ display: "inline-flex", alignItems: "center" }}>{icon}</span>
      <span style={{ fontWeight: 600 }}>{att.name}</span>
      {sizeStr && <span style={{ opacity: 0.5 }}>{sizeStr}</span>}
    </div>
  );
}

// ─── Slash 命令面板 ───

function SlashCommandPanel({
  commands,
  filter,
  onSelect,
  selectedIdx,
}: {
  commands: SlashCommand[];
  filter: string;
  onSelect: (cmd: SlashCommand) => void;
  selectedIdx: number;
}) {
  const filtered = useMemo(() => {
    const q = filter.toLowerCase();
    return commands.filter((c) => c.id.includes(q) || c.label.includes(q) || c.description.includes(q));
  }, [commands, filter]);

  if (filtered.length === 0) return null;
  return (
    <div
      style={{
        position: "absolute",
        bottom: "100%",
        left: 0,
        right: 0,
        marginBottom: 6,
        maxHeight: 260,
        overflow: "auto",
        border: "1px solid var(--line)",
        borderRadius: 14,
        background: "var(--panel2)",
        boxShadow: "0 -12px 48px rgba(17,24,39,0.12)",
        zIndex: 100,
      }}
    >
      {filtered.map((cmd, idx) => (
        <div
          key={cmd.id}
          onClick={() => onSelect(cmd)}
          style={{
            padding: "10px 14px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 10,
            background: idx === selectedIdx ? "rgba(14,165,233,0.08)" : "transparent",
            borderTop: idx === 0 ? "none" : "1px solid rgba(17,24,39,0.06)",
          }}
        >
          <span style={{ fontSize: 16, opacity: 0.7, display: "inline-flex", alignItems: "center" }}>
            {cmd.id === "model" ? <IconRefresh size={16} /> :
             cmd.id === "plan" ? <IconClipboard size={16} /> :
             cmd.id === "clear" ? <IconTrash size={16} /> :
             cmd.id === "skill" ? <IconZap size={16} /> :
             cmd.id === "persona" ? <IconMask size={16} /> :
             cmd.id === "agent" ? <IconBot size={16} /> :
             cmd.id === "agents" ? <IconUsers size={16} /> :
             cmd.id === "help" ? <IconHelp size={16} /> :
             <span style={{ fontSize: 14 }}>/</span>}
          </span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 13 }}>/{cmd.id} <span style={{ fontWeight: 400, opacity: 0.6 }}>{cmd.label}</span></div>
            <div style={{ fontSize: 12, opacity: 0.5 }}>{cmd.description}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── 消息渲染 ───

function MessageBubble({
  msg,
  onAskAnswer,
  apiBaseUrl,
  showChain = true,
  onSkipStep,
}: {
  msg: ChatMessage;
  onAskAnswer?: (msgId: string, answer: string) => void;
  apiBaseUrl?: string;
  showChain?: boolean;
  onSkipStep?: () => void;
}) {
  const isUser = msg.role === "user";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : "flex-start", marginBottom: 16 }}>
      {/* Agent name label */}
      {!isUser && msg.agentName && (
        <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.5, marginBottom: 2, paddingLeft: 2 }}>
          {msg.agentName}
        </div>
      )}
      <div
        style={{
          maxWidth: "85%",
          padding: isUser ? "10px 16px" : "12px 16px",
          borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
          background: isUser ? "var(--brand)" : "var(--panel2)",
          color: isUser ? "#fff" : "var(--text)",
          border: isUser ? "none" : "1px solid var(--line)",
          boxShadow: isUser ? "var(--glow-shadow)" : "var(--shadow)",
          fontSize: 14,
          lineHeight: 1.7,
          wordBreak: "break-word",
        }}
      >
        {/* Attachments */}
        {msg.attachments && msg.attachments.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {msg.attachments.map((att, i) => (
              <AttachmentPreview key={i} att={att} />
            ))}
          </div>
        )}

        {/* Thinking chain (new, Cursor-style) */}
        {msg.thinkingChain && msg.thinkingChain.length > 0 && (
          <ThinkingChain chain={msg.thinkingChain} streaming={!!msg.streaming} showChain={showChain} onSkipStep={onSkipStep} />
        )}

        {/* Thinking content (legacy fallback when no chain data) */}
        {msg.thinking && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
          <ThinkingBlock content={msg.thinking} />
        )}

        {/* Main content (markdown) */}
        {msg.content && (
          <div className="chatMdContent">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {msg.content}
            </ReactMarkdown>
          </div>
        )}

        {/* Streaming indicator */}
        {msg.streaming && !msg.content && (
          <div style={{ display: "flex", gap: 4, padding: "4px 0" }}>
            <span className="dotBounce" style={{ animationDelay: "0s" }} />
            <span className="dotBounce" style={{ animationDelay: "0.15s" }} />
            <span className="dotBounce" style={{ animationDelay: "0.3s" }} />
          </div>
        )}

        {/* Tool calls - only show legacy group when no chain data */}
        {msg.toolCalls && msg.toolCalls.length > 0 && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
          <ToolCallsGroup toolCalls={msg.toolCalls} />
        )}

        {/* Plan 已移至输入框上方浮动显示 */}

        {/* Artifacts (images, files delivered by agent) */}
        {msg.artifacts && msg.artifacts.length > 0 && (
          <div style={{ marginTop: 8 }}>
            {msg.artifacts.map((art, i) => {
              const fullUrl = art.file_url.startsWith("http")
                ? art.file_url
                : `${apiBaseUrl || ""}${art.file_url}`;
              if (art.artifact_type === "image") {
                return (
                  <div key={i} style={{ marginBottom: 8, position: "relative", display: "inline-block" }}>
                    <img
                      src={fullUrl}
                      alt={art.caption || art.name}
                      style={{
                        maxWidth: "100%",
                        maxHeight: 400,
                        borderRadius: 8,
                        border: "1px solid var(--line)",
                        display: "block",
                      }}
                    />
                    <button
                      title={t("chat.downloadImage") || "保存图片"}
                      style={{
                        position: "absolute", top: 8, right: 8,
                        background: "rgba(0,0,0,0.55)", color: "#fff",
                        border: "none", borderRadius: 6, width: 32, height: 32,
                        display: "flex", alignItems: "center", justifyContent: "center",
                        cursor: "pointer", opacity: 0.8, transition: "opacity 0.15s",
                      }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.8"; }}
                      onClick={async (e) => {
                        e.stopPropagation();
                        try {
                          const savedPath = await invoke<string>("download_file", {
                            url: fullUrl,
                            filename: art.name || `image-${Date.now()}.png`,
                          });
                          await invoke("show_item_in_folder", { path: savedPath });
                        } catch (err) {
                          console.error("图片下载失败:", err);
                        }
                      }}
                    >
                      <IconDownload size={16} />
                    </button>
                    {art.caption && (
                      <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>{art.caption}</div>
                    )}
                  </div>
                );
              }
              if (art.artifact_type === "voice") {
                return (
                  <div key={i} style={{ marginBottom: 8 }}>
                    <audio controls src={fullUrl} style={{ maxWidth: "100%" }} />
                    {art.caption && (
                      <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>{art.caption}</div>
                    )}
                  </div>
                );
              }
              // Generic file artifact — clickable card to download/open
              const sizeStr = art.size != null
                ? art.size > 1048576 ? `${(art.size / 1048576).toFixed(1)} MB` : `${(art.size / 1024).toFixed(1)} KB`
                : "";
              return (
                <div key={i} style={{
                  display: "inline-flex", alignItems: "center", gap: 8,
                  padding: "8px 14px", borderRadius: 8, border: "1px solid var(--line)",
                  fontSize: 13, marginBottom: 4, cursor: "pointer",
                  background: "var(--panel)",
                  transition: "background 0.15s",
                }}
                  onClick={async () => {
                    try {
                      const savedPath = await invoke<string>("download_file", {
                        url: fullUrl,
                        filename: art.name || "file",
                      });
                      await invoke("show_item_in_folder", { path: savedPath });
                    } catch (err) {
                      console.error("文件下载失败:", err);
                    }
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(14,165,233,0.08)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--panel)"; }}
                >
                  <IconPaperclip size={14} />
                  <span style={{ fontWeight: 600 }}>{art.name}</span>
                  {sizeStr && <span style={{ opacity: 0.5 }}>{sizeStr}</span>}
                  {art.caption && <span style={{ opacity: 0.6, fontSize: 12 }}>{art.caption}</span>}
                </div>
              );
            })}
          </div>
        )}

        {/* Ask user */}
        {msg.askUser && (
          <AskUserBlock
            ask={msg.askUser}
            onAnswer={(ans) => onAskAnswer?.(msg.id, ans)}
          />
        )}
      </div>
      <div style={{ fontSize: 11, opacity: 0.35, marginTop: 2, paddingLeft: 2, paddingRight: 2 }}>
        {formatTime(msg.timestamp)}
      </div>
    </div>
  );
}

// ─── Flat Mode (Cursor 风格无气泡模式) ───

function FlatMessageItem({
  msg,
  onAskAnswer,
  apiBaseUrl,
  showChain = true,
  onSkipStep,
}: {
  msg: ChatMessage;
  onAskAnswer?: (msgId: string, answer: string) => void;
  apiBaseUrl?: string;
  showChain?: boolean;
  onSkipStep?: () => void;
}) {
  const isUser = msg.role === "user";
  const isSystem = msg.role === "system";

  if (isSystem) {
    return (
      <div className="flatMsgSystem">
        <span>{msg.content}</span>
      </div>
    );
  }

  return (
    <div className={`flatMessage ${isUser ? "flatMsgUser" : "flatMsgAssistant"}`}>
      {/* User message */}
      {isUser && (
        <div className="flatUserContent">
          {msg.attachments && msg.attachments.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              {msg.attachments.map((att, i) => (
                <AttachmentPreview key={i} att={att} />
              ))}
            </div>
          )}
          <span>{msg.content}</span>
        </div>
      )}

      {/* Assistant message */}
      {!isUser && (
        <>
          {/* Agent name */}
          {msg.agentName && (
            <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.4, marginBottom: 4 }}>
              {msg.agentName}
            </div>
          )}

          {/* Thinking chain (Cursor style timeline) */}
          {msg.thinkingChain && msg.thinkingChain.length > 0 && (
            <ThinkingChain chain={msg.thinkingChain} streaming={!!msg.streaming} showChain={showChain} onSkipStep={onSkipStep} />
          )}

          {/* Legacy thinking fallback */}
          {msg.thinking && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
            <ThinkingBlock content={msg.thinking} />
          )}

          {/* Streaming indicator */}
          {msg.streaming && !msg.content && (
            <div style={{ display: "flex", gap: 4, padding: "4px 0" }}>
              <span className="dotBounce" style={{ animationDelay: "0s" }} />
              <span className="dotBounce" style={{ animationDelay: "0.15s" }} />
              <span className="dotBounce" style={{ animationDelay: "0.3s" }} />
            </div>
          )}

          {/* Main content (markdown) */}
          {msg.content && (
            <div className="chatMdContent">
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                {msg.content}
              </ReactMarkdown>
            </div>
          )}

          {/* Tool calls legacy fallback */}
          {msg.toolCalls && msg.toolCalls.length > 0 && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
            <ToolCallsGroup toolCalls={msg.toolCalls} />
          )}

          {/* Plan 已移至输入框上方浮动显示 */}

          {/* Artifacts */}
          {msg.artifacts && msg.artifacts.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {msg.artifacts.map((art, i) => {
                const fullUrl = art.file_url.startsWith("http")
                  ? art.file_url
                  : `${apiBaseUrl || ""}${art.file_url}`;
                if (art.artifact_type === "image") {
                  return (
                    <div key={i} style={{ marginBottom: 8, position: "relative", display: "inline-block" }}>
                      <img
                        src={fullUrl}
                        alt={art.caption || art.name}
                        style={{ maxWidth: "100%", maxHeight: 400, borderRadius: 8, border: "1px solid var(--line)", display: "block" }}
                      />
                      <button
                        title={t("chat.downloadImage") || "保存图片"}
                        style={{
                          position: "absolute", top: 8, right: 8,
                          background: "rgba(0,0,0,0.55)", color: "#fff",
                          border: "none", borderRadius: 6, width: 32, height: 32,
                          display: "flex", alignItems: "center", justifyContent: "center",
                          cursor: "pointer", opacity: 0.8, transition: "opacity 0.15s",
                        }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.8"; }}
                        onClick={async (e) => {
                          e.stopPropagation();
                          try {
                            const savedPath = await invoke<string>("download_file", {
                              url: fullUrl,
                              filename: art.name || `image-${Date.now()}.png`,
                            });
                            await invoke("show_item_in_folder", { path: savedPath });
                          } catch (err) {
                            console.error("图片下载失败:", err);
                          }
                        }}
                      >
                        <IconDownload size={16} />
                      </button>
                    </div>
                  );
                }
                return null;
              })}
            </div>
          )}

          {/* Ask user */}
          {msg.askUser && (
            <AskUserBlock
              ask={msg.askUser}
              onAnswer={(ans) => onAskAnswer?.(msg.id, ans)}
            />
          )}
        </>
      )}

      {/* Timestamp */}
      <div style={{ fontSize: 11, opacity: 0.25, marginTop: 2 }}>
        {formatTime(msg.timestamp)}
      </div>
    </div>
  );
}

// ─── 主组件 ───

export function ChatView({
  serviceRunning,
  endpoints,
  onStartService,
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
}: {
  serviceRunning: boolean;
  endpoints: EndpointSummary[];
  onStartService: () => void;
  apiBaseUrl?: string;
  visible?: boolean;
}) {
  const { t, i18n } = useTranslation();

  // ── 持久化 Key 常量 ──
  const STORAGE_KEY_CONVS = "chat_conversations";
  const STORAGE_KEY_ACTIVE = "chat_activeConvId";
  const STORAGE_KEY_MSGS_PREFIX = "chat_msgs_";

  // ── State（从 localStorage 恢复） ──
  const [conversations, setConversations] = useState<ChatConversation[]>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_CONVS);
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  });
  const [activeConvId, setActiveConvId] = useState<string | null>(() => {
    try { return localStorage.getItem(STORAGE_KEY_ACTIVE) || null; }
    catch { return null; }
  });
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const convId = localStorage.getItem(STORAGE_KEY_ACTIVE);
      if (!convId) return [];
      const raw = localStorage.getItem(STORAGE_KEY_MSGS_PREFIX + convId);
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  });
  const [inputText, setInputText] = useState("");
  const [selectedEndpoint, setSelectedEndpoint] = useState("auto");
  const [planMode, setPlanMode] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashFilter, setSlashFilter] = useState("");
  const [slashSelectedIdx, setSlashSelectedIdx] = useState(0);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);

  // 思维链 & 显示模式（从 localStorage 恢复用户习惯）
  const [showChain, setShowChain] = useState(() => {
    try { const v = localStorage.getItem("chat_showChain"); return v !== null ? v === "true" : true; }
    catch { return true; }
  });
  const [displayMode, setDisplayMode] = useState<ChatDisplayMode>(() => {
    try { const v = localStorage.getItem("chat_displayMode"); return (v === "bubble" || v === "flat") ? v : "flat"; }
    catch { return "flat"; }
  });

  // 持久化用户偏好
  useEffect(() => { try { localStorage.setItem("chat_showChain", String(showChain)); } catch {} }, [showChain]);
  useEffect(() => { try { localStorage.setItem("chat_displayMode", displayMode); } catch {} }, [displayMode]);

  const [isRecording, setIsRecording] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);

  // 深度思考模式 & 深度（从 localStorage 恢复用户习惯）
  const [thinkingMode, setThinkingMode] = useState<"auto" | "on" | "off">(() => {
    try { const v = localStorage.getItem("chat_thinkingMode"); return (v === "on" || v === "off") ? v : "auto"; }
    catch { return "auto"; }
  });
  const [thinkingDepth, setThinkingDepth] = useState<"low" | "medium" | "high">(() => {
    try { const v = localStorage.getItem("chat_thinkingDepth"); return (v === "low" || v === "medium" || v === "high") ? v : "medium"; }
    catch { return "medium"; }
  });
  const [thinkingMenuOpen, setThinkingMenuOpen] = useState(false);
  const thinkingMenuRef = useRef<HTMLDivElement | null>(null);

  // 持久化思考偏好
  useEffect(() => { try { localStorage.setItem("chat_thinkingMode", thinkingMode); } catch {} }, [thinkingMode]);
  useEffect(() => { try { localStorage.setItem("chat_thinkingDepth", thinkingDepth); } catch {} }, [thinkingDepth]);

  // ── 上下文占用追踪 ──
  const [contextTokens, setContextTokens] = useState(0);
  const [contextLimit, setContextLimit] = useState(0);
  const [contextTooltipVisible, setContextTooltipVisible] = useState(false);

  // ── 持久化会话列表 & 当前对话 ID ──
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY_CONVS, JSON.stringify(conversations));
    } catch { /* quota exceeded or private mode */ }
  }, [conversations]);

  useEffect(() => {
    try {
      if (activeConvId) localStorage.setItem(STORAGE_KEY_ACTIVE, activeConvId);
      else localStorage.removeItem(STORAGE_KEY_ACTIVE);
    } catch {}
  }, [activeConvId]);

  // ── 持久化消息（流式结束后 debounce 写入，避免高频写入） ──
  const saveMessagesTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!activeConvId) return;
    // 流式传输中时延迟保存，减少写入频率
    if (saveMessagesTimerRef.current) clearTimeout(saveMessagesTimerRef.current);
    const delay = isStreaming ? 2000 : 300;
    saveMessagesTimerRef.current = setTimeout(() => {
      try {
        // 保存时过滤掉 streaming 状态的瞬态字段，减小体积
        const toSave = messages.map(({ streaming, ...rest }) => rest);
        localStorage.setItem(STORAGE_KEY_MSGS_PREFIX + activeConvId, JSON.stringify(toSave));
      } catch {
        // localStorage quota exceeded: 清理最旧的对话消息
        try {
          const convs: ChatConversation[] = JSON.parse(localStorage.getItem(STORAGE_KEY_CONVS) || "[]");
          if (convs.length > 1) {
            const oldest = convs[convs.length - 1];
            localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + oldest.id);
          }
        } catch { /* give up */ }
      }
    }, delay);
    return () => { if (saveMessagesTimerRef.current) clearTimeout(saveMessagesTimerRef.current); };
  }, [messages, activeConvId, isStreaming]);

  // ── 切换对话时加载对应消息 ──
  const prevConvIdRef = useRef<string | null>(activeConvId);
  const skipConvLoadRef = useRef(false); // sendMessage 创建新对话时跳过加载
  useEffect(() => {
    if (activeConvId && activeConvId !== prevConvIdRef.current) {
      if (skipConvLoadRef.current) {
        skipConvLoadRef.current = false;
      } else {
        try {
          const raw = localStorage.getItem(STORAGE_KEY_MSGS_PREFIX + activeConvId);
          if (raw) {
            setMessages(JSON.parse(raw));
          } else {
            setMessages([]);
            // localStorage empty — try to restore from backend session
            if (serviceRunning) {
              const convId = activeConvId;
              fetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`)
                .then((r) => r.ok ? r.json() : null)
                .then((data) => {
                  if (!data?.messages?.length) return;
                  const restored: ChatMessage[] = data.messages.map((m: { id: string; role: string; content: string; timestamp: number; chain_summary?: ChainSummaryItem[] }) => ({
                    id: m.id,
                    role: m.role as "user" | "assistant" | "system",
                    content: m.content,
                    timestamp: m.timestamp,
                    ...(m.chain_summary?.length ? { thinkingChain: buildChainFromSummary(m.chain_summary) } : {}),
                  }));
                  setMessages(restored);
                })
                .catch(() => {});
            }
          }
        } catch { setMessages([]); }
      }
      isInitialScrollRef.current = true;
    }
    prevConvIdRef.current = activeConvId;
  }, [activeConvId, serviceRunning, apiBaseUrl]);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const isInitialScrollRef = useRef(true); // first scroll should be instant, not smooth
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Fetch initial context size on mount / when service starts
  useEffect(() => {
    if (!serviceRunning) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${apiBaseUrl}/api/stats/tokens/context`);
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        if (typeof data.context_tokens === "number") setContextTokens(data.context_tokens);
        if (typeof data.context_limit === "number") setContextLimit(data.context_limit);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [serviceRunning, apiBaseUrl]);

  // Restore conversations from backend when localStorage is empty (e.g. after Tauri restart)
  const sessionRestoreAttempted = useRef(false);
  useEffect(() => {
    if (!serviceRunning || sessionRestoreAttempted.current) return;
    sessionRestoreAttempted.current = true;
    if (conversations.length > 0) return;

    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${apiBaseUrl}/api/sessions?channel=desktop`);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const backendSessions: { id: string; title: string; lastMessage: string; timestamp: number; messageCount: number }[] = data.sessions || [];
        if (backendSessions.length === 0 || cancelled) return;

        const restoredConvs: ChatConversation[] = backendSessions.map((s) => ({
          id: s.id,
          title: s.title || "对话",
          lastMessage: s.lastMessage || "",
          timestamp: s.timestamp,
          messageCount: s.messageCount || 0,
        }));
        setConversations(restoredConvs);

        const firstConvId = restoredConvs[0].id;
        setActiveConvId(firstConvId);

        const histRes = await fetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(firstConvId)}/history`);
        if (!histRes.ok || cancelled) return;
        const histData = await histRes.json();
        const restoredMsgs: ChatMessage[] = (histData.messages || []).map((m: { id: string; role: string; content: string; timestamp: number; chain_summary?: ChainSummaryItem[] }) => ({
          id: m.id,
          role: m.role as "user" | "assistant" | "system",
          content: m.content,
          timestamp: m.timestamp,
          ...(m.chain_summary?.length ? { thinkingChain: buildChainFromSummary(m.chain_summary) } : {}),
        }));
        if (restoredMsgs.length > 0) {
          skipConvLoadRef.current = true;
          setMessages(restoredMsgs);
        }
      } catch { /* backend not available yet, ignore */ }
    })();
    return () => { cancelled = true; };
  }, [serviceRunning, apiBaseUrl, conversations.length]);

  // ── 消息补全：用后端数据修复 localStorage 中不完整的消息（中断的流式传输等）──
  const patchedConvsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!serviceRunning || !activeConvId || isStreaming) return;
    if (patchedConvsRef.current.has(activeConvId)) return;

    const hasIncomplete = messages.some(
      (m) => m.role === "assistant" && (
        !m.content ||
        m.thinkingChain?.some((g) => !g.entries.length && !g.durationMs)
      ),
    );
    if (!hasIncomplete) return;

    patchedConvsRef.current.add(activeConvId);
    const convId = activeConvId;

    fetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data?.messages?.length) return;
        setMessages((prev) => patchMessagesWithBackend(prev, data.messages));
      })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceRunning, activeConvId, isStreaming, apiBaseUrl, messages.length]);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // ── API base URL ──
  const apiBase = apiBaseUrl;

  // ── 文件上传辅助函数：上传文件到 /api/upload 并返回访问 URL ──
  const uploadFile = useCallback(async (file: Blob, filename: string): Promise<string> => {
    const form = new FormData();
    form.append("file", file, filename);
    const res = await fetch(`${apiBase}/api/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`上传失败: ${res.status}`);
    const data = await res.json();
    return data.url as string;  // 后端返回 { url: "/api/uploads/<filename>" }
  }, [apiBase]);

  // ── 组件卸载清理：abort 流式请求 + 停止麦克风 ──
  useEffect(() => {
    return () => {
      // 终止正在进行的 SSE 流式请求，避免内存泄漏和 React 状态更新警告
      abortRef.current?.abort();
      readerRef.current?.cancel().catch(() => {});
      readerRef.current = null;
      // 停止录音并释放麦克风
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        try { mediaRecorderRef.current.stop(); } catch { /* ignore */ }
      }
      mediaRecorderRef.current = null;
    };
  }, []);

  // ── 自动滚到底部 ──
  // 当 visible=false (display:none) 时 scrollIntoView 无效，
  // 所以需要在变为可见时重新触发滚动。
  const needsScrollOnVisible = useRef(false);

  useEffect(() => {
    if (!messagesEndRef.current) return;
    if (!visible) {
      // 不可见时标记待滚动，等变为可见后再执行
      needsScrollOnVisible.current = true;
      return;
    }
    if (isInitialScrollRef.current) {
      // Initial load / conversation switch: instant scroll
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
      });
      isInitialScrollRef.current = false;
    } else {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
    needsScrollOnVisible.current = false;
  }, [messages, visible]);

  // 从隐藏变为可见时，补一次即时滚动到底部
  useEffect(() => {
    if (visible && needsScrollOnVisible.current && messagesEndRef.current) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
      });
      needsScrollOnVisible.current = false;
      isInitialScrollRef.current = false;
    }
  }, [visible]);

  // ── 思维链: 流式结束后自动折叠 ──
  useEffect(() => {
    if (!isStreaming && messages.some(m => m.thinkingChain?.length)) {
      const timer = setTimeout(() => {
        setMessages(prev => prev.map(m => ({
          ...m,
          thinkingChain: m.thinkingChain?.map(g => ({ ...g, collapsed: true })) ?? null,
        })));
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [isStreaming]);

  // ── 点击外部关闭模型菜单 ──
  useEffect(() => {
    if (!modelMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) {
        setModelMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modelMenuOpen]);

  // ── 点击外部关闭思考菜单 ──
  useEffect(() => {
    if (!thinkingMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (thinkingMenuRef.current && !thinkingMenuRef.current.contains(e.target as Node)) {
        setThinkingMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [thinkingMenuOpen]);

  // ── 斜杠命令定义 ──
  const slashCommands: SlashCommand[] = useMemo(() => [
    { id: "model", label: "切换模型", description: "选择使用的 LLM 端点", action: (args) => {
      if (args && endpoints.find((e) => e.name === args)) {
        setSelectedEndpoint(args);
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `已切换到端点: ${args}`, timestamp: Date.now() }]);
      } else {
        const names = ["auto", ...endpoints.map((e) => e.name)];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `可用端点: ${names.join(", ")}\n用法: /model <端点名>`, timestamp: Date.now() }]);
      }
    }},
    { id: "plan", label: "计划模式", description: "开启/关闭 Plan 模式，先计划再执行", action: () => {
      setPlanMode((v) => {
        const next = !v;
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: next ? "已开启 Plan 模式" : "已关闭 Plan 模式", timestamp: Date.now() }]);
        return next;
      });
    }},
    { id: "clear", label: "清空对话", description: "清除当前对话的所有消息", action: () => { setMessages([]); } },
    { id: "skill", label: "使用技能", description: "调用已安装的技能（发送 /skill:<技能名> 触发）", action: (args) => {
      if (args) {
        setInputText(`请使用技能「${args}」来帮我：`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /skill <技能名>，如 /skill web-search。在消息中提及技能名即可触发。", timestamp: Date.now() }]);
      }
    }},
    { id: "persona", label: "切换角色", description: "切换 Agent 的人格预设", action: (args) => {
      if (args) {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `角色切换请在「设置 → Agent 系统」中修改 PERSONA_NAME 为 "${args}"`, timestamp: Date.now() }]);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "可用角色: default, business, tech_expert, butler, girlfriend, boyfriend, family, jarvis\n用法: /persona <角色ID>", timestamp: Date.now() }]);
      }
    }},
    { id: "agent", label: "切换 Agent", description: "在多 Agent 间切换（handoff 模式）", action: (args) => {
      if (args) {
        setInputText(`请切换到 Agent「${args}」来处理接下来的任务。`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /agent <Agent名称>。在 handoff 模式下，AI 会自动在 Agent 间切换。", timestamp: Date.now() }]);
      }
    }},
    { id: "agents", label: "查看 Agent 列表", description: "显示可用的 Agent 列表", action: () => {
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: "Agent 列表取决于 handoff 配置。当前可通过 /agent <名称> 手动请求切换。", timestamp: Date.now() }]);
    }},
    { id: "thinking", label: "深度思考", description: "设置思考模式 (on/off/auto)", action: (args) => {
      const mode = args?.toLowerCase().trim();
      if (mode === "on" || mode === "off" || mode === "auto") {
        setThinkingMode(mode);
        const label = { on: "开启", off: "关闭", auto: "自动" }[mode];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `思考模式已设置为: ${label}`, timestamp: Date.now() }]);
      } else {
        const currentLabel = { on: "开启", off: "关闭", auto: "自动" }[thinkingMode];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `当前思考模式: ${currentLabel}\n用法: /thinking on|off|auto`, timestamp: Date.now() }]);
      }
    }},
    { id: "thinking_depth", label: "思考深度", description: "设置思考深度 (low/medium/high)", action: (args) => {
      const depth = args?.toLowerCase().trim();
      if (depth === "low" || depth === "medium" || depth === "high") {
        setThinkingDepth(depth);
        const label = { low: "低", medium: "中", high: "高" }[depth];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `思考深度已设置为: ${label}`, timestamp: Date.now() }]);
      } else {
        const currentLabel = { low: "低", medium: "中", high: "高" }[thinkingDepth];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `当前思考深度: ${currentLabel}\n用法: /thinking_depth low|medium|high`, timestamp: Date.now() }]);
      }
    }},
    { id: "help", label: "帮助", description: "显示可用命令列表", action: () => {
      setMessages((prev) => [...prev, {
        id: genId(),
        role: "system",
        content: "**可用命令：**\n- `/model [端点名]` — 切换 LLM 端点\n- `/plan` — 开启/关闭计划模式\n- `/thinking [on|off|auto]` — 深度思考模式\n- `/thinking_depth [low|medium|high]` — 思考深度\n- `/clear` — 清空对话\n- `/skill [技能名]` — 使用技能\n- `/persona [角色ID]` — 查看/切换角色\n- `/agent [Agent名]` — 切换 Agent\n- `/agents` — 查看 Agent 列表\n- `/help` — 显示此帮助",
        timestamp: Date.now(),
      }]);
    }},
  ], [endpoints, thinkingMode, thinkingDepth]);

  // ── 新建对话 ──
  const newConversation = useCallback(() => {
    const id = genId();
    // 先保存当前对话消息（如果有）
    if (activeConvId && messages.length > 0) {
      try {
        const toSave = messages.map(({ streaming, ...rest }) => rest);
        localStorage.setItem(STORAGE_KEY_MSGS_PREFIX + activeConvId, JSON.stringify(toSave));
      } catch {}
    }
    setActiveConvId(id);
    setMessages([]);
    setPendingAttachments([]);
    setConversations((prev) => [{
      id,
      title: "新对话",
      lastMessage: "",
      timestamp: Date.now(),
      messageCount: 0,
    }, ...prev]);
  }, [activeConvId, messages]);

  // ── 删除对话 ──
  const deleteConversation = useCallback((convId: string, e?: React.MouseEvent) => {
    if (e) { e.stopPropagation(); e.preventDefault(); }
    // 从 localStorage 删除该对话的消息
    try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + convId); } catch {}
    // 如果删除的是当前激活的对话，切换到下一个或清空
    if (convId === activeConvId) {
      setConversations((prev) => {
        const remaining = prev.filter((c) => c.id !== convId);
        if (remaining.length > 0) {
          setActiveConvId(remaining[0].id);
          try {
            const raw = localStorage.getItem(STORAGE_KEY_MSGS_PREFIX + remaining[0].id);
            setMessages(raw ? JSON.parse(raw) : []);
          } catch { setMessages([]); }
        } else {
          setActiveConvId(null);
          setMessages([]);
        }
        return remaining;
      });
    } else {
      setConversations((prev) => prev.filter((c) => c.id !== convId));
    }
  }, [activeConvId]);

  // ── 发送消息（overrideText 用于 ask_user 回复等场景，绕过 inputText） ──
  const sendMessage = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? inputText).trim();
    if (!text && pendingAttachments.length === 0) return;
    if (isStreaming) return;

    // 斜杠命令处理
    if (text.startsWith("/")) {
      const parts = text.slice(1).split(/\s+/);
      const cmdId = parts[0].toLowerCase();
      const cmd = slashCommands.find((c) => c.id === cmdId);
      if (cmd) {
        cmd.action(parts.slice(1).join(" "));
        setInputText("");
        setSlashOpen(false);
        return;
      }
    }

    // 创建用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: "user",
      content: text,
      attachments: pendingAttachments.length > 0 ? [...pendingAttachments] : undefined,
      timestamp: Date.now(),
    };

    // 创建流式助手消息占位
    const assistantMsg: ChatMessage = {
      id: genId(),
      role: "assistant",
      content: "",
      streaming: true,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInputText("");
    setPendingAttachments([]);
    setIsStreaming(true);
    setSlashOpen(false);

    // 确保有对话（注意 setState 是异步的，需要用局部变量保存新 id）
    let convId = activeConvId;
    if (!convId) {
      convId = genId();
      skipConvLoadRef.current = true; // 阻止 activeConvId effect 从 localStorage 加载空消息覆盖已添加的 messages
      setActiveConvId(convId);
      setConversations((prev) => [{
        id: convId!,
        title: text.slice(0, 30) || "新对话",
        lastMessage: text,
        timestamp: Date.now(),
        messageCount: 1,
      }, ...prev]);
    }

    // SSE 流式请求
    const abort = new AbortController();
    abortRef.current = abort;

    // 空闲超时：如果 IDLE_TIMEOUT_MS 内没有收到任何数据则放弃
    // （每次收到数据后重置计时器，不影响长对话）
    const IDLE_TIMEOUT_MS = 300_000; // 5 minutes idle (backend sends heartbeats every 15s)
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        abort.abort();
        readerRef.current?.cancel().catch(() => {});
      }, IDLE_TIMEOUT_MS);
    };

    try {
      const body: Record<string, unknown> = {
        message: text,
        conversation_id: convId,
        plan_mode: planMode,
        endpoint: selectedEndpoint === "auto" ? null : selectedEndpoint,
        thinking_mode: thinkingMode !== "auto" ? thinkingMode : null,
        thinking_depth: thinkingMode !== "off" ? thinkingDepth : null,
      };

      // 附件信息
      if (pendingAttachments.length > 0) {
        body.attachments = pendingAttachments.map((a) => ({
          type: a.type,
          name: a.name,
          url: a.url,
          mime_type: a.mimeType,
        }));
      }

      resetIdleTimer(); // Start idle timer before fetch

      const response = await fetch(`${apiBase}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: abort.signal,
      });

      if (!response.ok) {
        const errText = await response.text().catch(() => "请求失败");
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: `错误：${response.status} ${errText}`, streaming: false } : m
        ));
        setIsStreaming(false);
        return;
      }

      // 收到响应头，重置空闲计时
      resetIdleTimer();

      // 处理 SSE 流
      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");
      readerRef.current = reader;

      const decoder = new TextDecoder();
      let buffer = "";
      let currentContent = "";
      let currentThinking = "";
      let isThinking = false;
      let currentToolCalls: ChatToolCall[] = [];
      let currentPlan: ChatPlan | null = null;
      let currentAsk: ChatAskUser | null = null;
      let currentAgent: string | null = null;
      let currentArtifacts: ChatArtifact[] = [];
      let gracefulDone = false; // SSE 正常发送了 "done" 事件

      // 思维链: 分组数据
      let chainGroups: ChainGroup[] = [];
      let currentChainGroup: ChainGroup | null = null;
      let thinkingStartTime = 0;
      let currentThinkingContent = "";
      let pendingCompressedInfo: { beforeTokens: number; afterTokens: number } | null = null;

      while (true) {
        // ── 1. 每次循环检查 abort 状态 ──
        if (abort.signal.aborted) break;

        let done: boolean;
        let value: Uint8Array | undefined;
        try {
          ({ done, value } = await reader.read());
        } catch (readErr) {
          // reader.read() 抛异常（abort 或网络错误）→ 跳到外层 catch
          throw readErr;
        }

        if (value) {
          buffer += decoder.decode(value, { stream: true });
          resetIdleTimer(); // 收到数据，重置空闲计时
        }

        // ── 2. 再次检查 abort（read 可能返回 done:true 而非抛异常） ──
        if (abort.signal.aborted) break;

        if (done) {
          // 流结束：处理 buffer 中可能的残余内容（最后一行无换行符的情况）
          if (buffer.trim()) {
            const remaining = buffer.split("\n");
            for (const line of remaining) {
              if (!line.startsWith("data: ")) continue;
              const data = line.slice(6).trim();
              if (data === "[DONE]") continue;
              try {
                const event: StreamEvent = JSON.parse(data);
                if (event.type === "text_delta") currentContent += event.content;
                else if (event.type === "plan_created") currentPlan = event.plan;
                else if (event.type === "plan_step_updated" && currentPlan) {
                  const newSteps = currentPlan.steps.map((s) => {
                    const matched = event.stepId ? s.id === event.stepId : false;
                    return matched ? { ...s, status: event.status as ChatPlanStep["status"] } : s;
                  });
                  currentPlan = { ...currentPlan, steps: newSteps } as ChatPlan;
                } else if (event.type === "plan_completed" && currentPlan) {
                  currentPlan = { ...currentPlan, status: "completed" as const } as ChatPlan;
                } else if (event.type === "plan_cancelled" && currentPlan) {
                  currentPlan = { ...currentPlan, status: "cancelled" } as ChatPlan;
                }
                if (event.type === "done") {
                  gracefulDone = true;
                  if (currentPlan && currentPlan.status === "in_progress") {
                    currentPlan = { ...currentPlan, status: "completed" as const };
                  }
                }
              } catch { /* ignore malformed */ }
            }
          }
          break;
        }

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6).trim();
          if (data === "[DONE]") continue;

          try {
            const event: StreamEvent = JSON.parse(data);

            switch (event.type) {
              case "heartbeat":
                continue;
              case "user_insert": {
                const insertContent = (event.content || "").trim();
                if (insertContent) {
                  setMessages((prev) => {
                    const assistantIdx = prev.findIndex((m) => m.id === assistantMsg.id);
                    const existingIdx = prev.findIndex(
                      (m) => m.role === "user" && m.content === insertContent && Date.now() - m.timestamp < 10000
                    );

                    if (existingIdx >= 0 && assistantIdx >= 0 && existingIdx > assistantIdx) {
                      const newArr = [...prev];
                      const [moved] = newArr.splice(existingIdx, 1);
                      const newAIdx = newArr.findIndex((m) => m.id === assistantMsg.id);
                      if (newAIdx >= 0) newArr.splice(newAIdx, 0, moved);
                      return newArr;
                    }

                    if (existingIdx >= 0) return prev;

                    const userMsg = { id: genId(), role: "user" as const, content: insertContent, timestamp: Date.now() };
                    if (assistantIdx >= 0) {
                      const newArr = [...prev];
                      newArr.splice(assistantIdx, 0, userMsg);
                      return newArr;
                    }
                    return [...prev, userMsg];
                  });
                }
                continue;
              }
              case "context_compressed":
                pendingCompressedInfo = { beforeTokens: event.before_tokens, afterTokens: event.after_tokens };
                break;
              case "iteration_start": {
                // 新迭代 → 新 chain group
                const newGroup: ChainGroup = {
                  iteration: event.iteration,
                  entries: [],
                  toolCalls: [],
                  hasThinking: false,
                  collapsed: false,
                };
                // 附加上下文压缩条目
                if (pendingCompressedInfo) {
                  newGroup.entries.push({ kind: "compressed", beforeTokens: pendingCompressedInfo.beforeTokens, afterTokens: pendingCompressedInfo.afterTokens });
                  pendingCompressedInfo = null;
                }
                currentChainGroup = newGroup;
                chainGroups = [...chainGroups, currentChainGroup];
                break;
              }
              case "thinking_start":
                isThinking = true;
                thinkingStartTime = Date.now();
                currentThinkingContent = "";
                if (!currentChainGroup) {
                  currentChainGroup = { iteration: chainGroups.length + 1, entries: [], toolCalls: [], hasThinking: false, collapsed: false };
                  chainGroups = [...chainGroups, currentChainGroup];
                }
                break;
              case "thinking_delta":
                currentThinking += event.content;
                currentThinkingContent += event.content;
                break;
              case "thinking_end": {
                isThinking = false;
                const _thinkDuration = event.duration_ms || (Date.now() - thinkingStartTime);
                const _hasThinking = event.has_thinking ?? (currentThinkingContent.length > 0);
                if (currentChainGroup) {
                  const grp: ChainGroup = currentChainGroup;
                  if (_hasThinking && currentThinkingContent) {
                    currentChainGroup = {
                      ...grp,
                      entries: [...grp.entries, { kind: "thinking" as const, content: currentThinkingContent }],
                      hasThinking: true,
                      durationMs: _thinkDuration,
                    };
                  } else {
                    currentChainGroup = { ...grp, durationMs: _thinkDuration };
                  }
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              }
              case "chain_text":
                if (!currentChainGroup) {
                  currentChainGroup = { iteration: chainGroups.length + 1, entries: [], toolCalls: [], hasThinking: false, collapsed: false };
                  chainGroups = [...chainGroups, currentChainGroup];
                }
                if (event.content) {
                  const grp: ChainGroup = currentChainGroup;
                  currentChainGroup = {
                    ...grp,
                    entries: [...grp.entries, { kind: "text" as const, content: event.content }],
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              case "text_delta":
                currentContent += event.content;
                break;
              case "tool_call_start": {
                currentToolCalls = [...currentToolCalls, { tool: event.tool, args: event.args, status: "running", id: event.id }];
                const _tcId = event.id || genId();
                const _desc = formatToolDescription(event.tool, event.args);
                const newTc: ChainToolCall = { toolId: _tcId, tool: event.tool, args: event.args, status: "running", description: _desc };
                if (currentChainGroup) {
                  const grp: ChainGroup = currentChainGroup;
                  currentChainGroup = {
                    ...grp,
                    toolCalls: [...grp.toolCalls, newTc],
                    entries: [...grp.entries, { kind: "tool_start" as const, toolId: _tcId, tool: event.tool, args: event.args, description: _desc, status: "running" }],
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              }
              case "tool_call_end": {
                let matched = false;
                currentToolCalls = currentToolCalls.map((tc) => {
                  if (matched) return tc;
                  const idMatch = event.id && tc.id && tc.id === event.id;
                  const nameMatch = !event.id && tc.tool === event.tool && tc.status === "running";
                  if (idMatch || nameMatch) { matched = true; return { ...tc, result: event.result, status: "done" as const }; }
                  return tc;
                });
                if (currentChainGroup) {
                  const grp: ChainGroup = currentChainGroup;
                  let chainMatched = false;
                  const isError = event.is_error === true || (event.result || "").startsWith("Tool error");
                  const endStatus = isError ? "error" as const : "done" as const;
                  currentChainGroup = {
                    ...grp,
                    toolCalls: grp.toolCalls.map((tc: ChainToolCall) => {
                      if (chainMatched) return tc;
                      const idMatch = event.id && tc.toolId === event.id;
                      const nameMatch = !event.id && tc.tool === event.tool && tc.status === "running";
                      if (idMatch || nameMatch) { chainMatched = true; return { ...tc, status: endStatus as ChainToolCall["status"], result: event.result }; }
                      return tc;
                    }),
                    // 更新 tool_start 状态 + 追加 tool_end
                    entries: [
                      ...grp.entries.map(e => {
                        if (e.kind === "tool_start" && (!e.status || e.status === "running")) {
                          const eIdMatch = event.id && e.toolId === event.id;
                          const eNameMatch = !event.id && e.tool === event.tool;
                          if (eIdMatch || eNameMatch) return { ...e, status: endStatus };
                        }
                        return e;
                      }),
                      { kind: "tool_end" as const, toolId: event.id || "", tool: event.tool, result: event.result, status: endStatus },
                    ],
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              }
              case "plan_created":
                currentPlan = event.plan;
                // 新 Plan 创建时，将之前消息中的旧 Plan 标记为 completed，
                // 避免浮动进度条显示已过时的旧 Plan
                setMessages((prev) => prev.map((m) =>
                  m.plan && m.plan.status !== "completed" && m.plan.status !== "failed" && m.plan.status !== "cancelled"
                    ? { ...m, plan: { ...m.plan, status: "completed" as const } }
                    : m
                ));
                break;
              case "plan_step_updated":
                if (currentPlan) {
                  const newSteps: ChatPlanStep[] = currentPlan.steps.map((s) => {
                    // 优先按 stepId 匹配，兼容旧版 stepIdx
                    const matched = event.stepId
                      ? s.id === event.stepId
                      : event.stepIdx != null && currentPlan!.steps.indexOf(s) === event.stepIdx;
                    return matched ? { ...s, status: event.status as ChatPlanStep["status"] } : s;
                  });
                  // 如果所有步骤都结束了，自动标记 plan 为 completed
                  const allDone = newSteps.every((s) => s.status === "completed" || s.status === "skipped" || s.status === "failed");
                  currentPlan = { ...currentPlan, steps: newSteps, ...(allDone ? { status: "completed" as const } : {}) } as ChatPlan;
                }
                break;
              case "plan_completed":
                if (currentPlan) {
                  currentPlan = { ...currentPlan, status: "completed" } as ChatPlan;
                }
                break;
              case "plan_cancelled":
                if (currentPlan) {
                  currentPlan = { ...currentPlan, status: "cancelled" } as ChatPlan;
                }
                break;
              case "ask_user": {
                const askQuestions = event.questions;
                // 如果没有 questions 数组但有 allow_multiple，构造一个统一的 questions
                if (!askQuestions && event.allow_multiple && event.options?.length) {
                  currentAsk = {
                    question: event.question,
                    options: event.options,
                    questions: [{
                      id: "__single__",
                      prompt: event.question,
                      options: event.options,
                      allow_multiple: true,
                    }],
                  };
                } else {
                  currentAsk = {
                    question: event.question,
                    options: event.options,
                    questions: askQuestions,
                  };
                }
                break;
              }
              case "ui_preference":
                if (event.theme) setThemePref(event.theme as Theme);
                if (event.language) i18n.changeLanguage(event.language);
                break;
              case "artifact":
                currentArtifacts = [...currentArtifacts, {
                  artifact_type: event.artifact_type,
                  file_url: event.file_url,
                  path: event.path,
                  name: event.name,
                  caption: event.caption,
                  size: event.size,
                }];
                break;
              case "agent_switch":
                currentAgent = event.agentName;
                setMessages((prev) => {
                  const switchMsg: ChatMessage = {
                    id: genId(),
                    role: "system",
                    content: `Agent 切换到：${event.agentName}${event.reason ? ` — ${event.reason}` : ""}`,
                    timestamp: Date.now(),
                  };
                  return [...prev.filter((m) => m.id !== assistantMsg.id), switchMsg, {
                    ...assistantMsg,
                    content: currentContent,
                    thinking: currentThinking || null,
                    agentName: event.agentName,
                    toolCalls: currentToolCalls.length > 0 ? currentToolCalls : null,
                    plan: currentPlan,
                    askUser: currentAsk,
                    thinkingChain: chainGroups.length > 0 ? chainGroups.map(g => ({ ...g })) : null,
                    streaming: true,
                  }];
                });
                continue; // skip normal update below
              case "error":
                currentContent += `\n\n**错误**：${event.message}`;
                break;
              case "done":
                gracefulDone = true;
                // 更新上下文用量
                if (event.usage) {
                  if (typeof event.usage.context_tokens === "number") setContextTokens(event.usage.context_tokens);
                  if (typeof event.usage.context_limit === "number") setContextLimit(event.usage.context_limit);
                }
                // 任务结束时，如果当前 Plan 仍在进行中，自动标记为 completed
                if (currentPlan && currentPlan.status === "in_progress") {
                  currentPlan = { ...(currentPlan as ChatPlan), status: "completed" as const };
                }
                // 同时清理之前消息中遗留的旧 Plan（防止浮动进度条显示过时 Plan）
                setMessages((prev) => {
                  const hasStaleplan = prev.some((m) => m.id !== assistantMsg.id && m.plan && m.plan.status !== "completed" && m.plan.status !== "failed" && m.plan.status !== "cancelled");
                  if (!hasStaleplan) return prev;
                  return prev.map((m) =>
                    m.id !== assistantMsg.id && m.plan && m.plan.status !== "completed" && m.plan.status !== "failed" && m.plan.status !== "cancelled"
                      ? { ...m, plan: { ...m.plan, status: "completed" as const } }
                      : m
                  );
                });
                break;
            }

            // 更新助手消息
            setMessages((prev) => prev.map((m) =>
              m.id === assistantMsg.id
                ? {
                    ...m,
                    content: currentContent,
                    thinking: currentThinking || null,
                    agentName: currentAgent,
                    toolCalls: currentToolCalls.length > 0 ? [...currentToolCalls] : null,
                    plan: currentPlan ? { ...currentPlan } : null,
                    askUser: currentAsk ? { ...currentAsk } : null,
                    artifacts: currentArtifacts.length > 0 ? [...currentArtifacts] : null,
                    thinkingChain: chainGroups.length > 0 ? chainGroups.map(g => ({ ...g })) : null,
                    streaming: event.type !== "done",
                  }
                : m
            ));

            if (event.type === "done") break;
          } catch {
            // ignore malformed SSE
          }
        }
      }

      // ── 循环结束后：判断是正常完成还是被用户中止 ──
      if (abort.signal.aborted) {
        // 用户点击了停止（或空闲超时触发 abort）
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, content: m.content || "（已中止）", streaming: false }
            : m
        ));
      } else {
        // 正常完成流式
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, streaming: false } : m
        ));
      }
    } catch (e: unknown) {
      // ── 兼容多种 abort 错误形式 ──
      // Chromium/WebView2: DOMException { name: "AbortError" }
      // 某些环境: Error { name: "AbortError" }
      // 安全检查: abort.signal.aborted 为 true
      const isAbort =
        abort.signal.aborted ||
        (e instanceof DOMException && e.name === "AbortError") ||
        (e instanceof Error && e.name === "AbortError");

      if (isAbort) {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: m.content || "（已中止）", streaming: false } : m
        ));
      } else {
        const errMsg = e instanceof Error ? e.message : String(e);
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: `连接失败：${errMsg}\n\n请确认后台服务（openakita serve）已启动，且 HTTP API 端口（18900）可访问。`, streaming: false } : m
        ));
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      // 确保 reader 被释放
      try { readerRef.current?.cancel().catch(() => {}); } catch { /* ignore */ }
      readerRef.current = null;
      setIsStreaming(false);
      abortRef.current = null;

      // 流式结束后更新对话摘要（lastMessage / messageCount）
      if (convId) {
        setConversations((prev) => prev.map((c) =>
          c.id === convId
            ? { ...c, lastMessage: text.slice(0, 60), timestamp: Date.now(), messageCount: (c.messageCount || 0) + 2 }
            : c
        ));
      }
    }
  }, [inputText, pendingAttachments, isStreaming, activeConvId, planMode, selectedEndpoint, apiBase, slashCommands, thinkingMode, thinkingDepth]);

  // ── 处理用户回答 (ask_user) ──
  const handleAskAnswer = useCallback((msgId: string, answer: string) => {
    setMessages((prev) => prev.map((m) =>
      m.id === msgId && m.askUser
        ? { ...m, askUser: { ...m.askUser, answered: true, answer } }
        : m
    ));
    // reason_stream 在 ask_user 后中断流，用户回复通过新 /api/chat 请求继续处理
    // 直接通过 sendMessage(overrideText) 发送，无需等待 state 更新
    sendMessage(answer);
  }, [sendMessage]);

  // ── 停止生成 ──
  const stopStreaming = useCallback(() => {
    // 1. Abort fetch 请求（触发 AbortError）
    abortRef.current?.abort();
    // 2. 显式取消 reader（某些浏览器/WebView 下 abort 不会立即终止 reader.read()）
    try { readerRef.current?.cancel().catch(() => {}); } catch { /* ignore */ }
    readerRef.current = null;
  }, []);

  // ── 消息排队系统 ──
  const [messageQueue, setMessageQueue] = useState<QueuedMessage[]>([]);
  const [queueExpanded, setQueueExpanded] = useState(true);

  const handleSkipStep = useCallback(() => {
    fetch(`${apiBase}/api/chat/skip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, reason: "用户从界面跳过步骤" }),
    }).catch(() => {});
  }, [apiBase, activeConvId]);

  const handleCancelTask = useCallback(() => {
    // 只通知后端取消，不断开 SSE 连接。
    // 后端会通过 SSE 流发送 LLM 收尾文本 + done 事件，前端自然结束。
    fetch(`${apiBase}/api/chat/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, reason: "用户从界面取消任务" }),
    }).then(() => {
      // 兜底：如果 8 秒后 SSE 还没结束（后端卡住），强制断开
      setTimeout(() => { if (readerRef.current) stopStreaming(); }, 8000);
    }).catch(() => {
      stopStreaming();
    });
  }, [apiBase, activeConvId, stopStreaming]);

  const handleInsertMessage = useCallback((text: string) => {
    if (!text.trim()) return;
    setMessages((prev) => {
      const userMsg = { id: genId(), role: "user" as const, content: text.trim(), timestamp: Date.now() };
      const streamingIdx = prev.findIndex((m) => m.role === "assistant" && m.streaming);
      if (streamingIdx >= 0) {
        const newArr = [...prev];
        newArr.splice(streamingIdx, 0, userMsg);
        return newArr;
      }
      return [...prev, userMsg];
    });
    fetch(`${apiBase}/api/chat/insert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, message: text }),
    }).catch(() => {});
  }, [apiBase, activeConvId]);

  const handleQueueMessage = useCallback(() => {
    const text = inputText.trim();
    if (!text) return;
    setMessageQueue(prev => [...prev, { id: genId(), text, timestamp: Date.now() }]);
    setInputText("");
    if (inputRef.current) {
      inputRef.current.value = "";
      inputRef.current.style.height = "auto";
    }
  }, [inputText]);

  const handleRemoveQueued = useCallback((id: string) => {
    setMessageQueue(prev => prev.filter(m => m.id !== id));
  }, []);

  const handleEditQueued = useCallback((id: string) => {
    const item = messageQueue.find(m => m.id === id);
    if (item) {
      setInputText(item.text);
      setMessageQueue(prev => prev.filter(m => m.id !== id));
      inputRef.current?.focus();
    }
  }, [messageQueue]);

  const handleSendQueuedNow = useCallback((id: string) => {
    const item = messageQueue.find(m => m.id === id);
    if (item) {
      handleInsertMessage(item.text);
      setMessageQueue(prev => prev.filter(m => m.id !== id));
    }
  }, [messageQueue, handleInsertMessage]);

  const handleMoveQueued = useCallback((id: string, direction: "up" | "down") => {
    setMessageQueue(prev => {
      const idx = prev.findIndex(m => m.id === id);
      if (idx < 0) return prev;
      const newIdx = direction === "up" ? idx - 1 : idx + 1;
      if (newIdx < 0 || newIdx >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
      return next;
    });
  }, []);

  // ── 排队消息自动出队：isStreaming 变 false 时自动取第一条执行 ──
  const autoDequeueRef = useRef(false);
  useEffect(() => {
    if (!isStreaming && autoDequeueRef.current && messageQueue.length > 0) {
      const next = messageQueue[0];
      setMessageQueue(prev => prev.slice(1));
      // 延迟一小段时间确保 streaming 状态完全清理
      setTimeout(() => sendMessage(next.text), 100);
    }
    autoDequeueRef.current = isStreaming;
  }, [isStreaming, messageQueue, sendMessage]);

  // ── 文件/图片上传 ──
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of Array.from(files)) {
      const att: ChatAttachment = {
        type: file.type.startsWith("image/") ? "image" : file.type.startsWith("video/") ? "video" : file.type.startsWith("audio/") ? "voice" : file.type === "application/pdf" ? "document" : "file",
        name: file.name,
        size: file.size,
        mimeType: file.type,
      };
      if (att.type === "video" && file.size > 7 * 1024 * 1024) {
        alert(`视频文件过大 (${(file.size / 1024 / 1024).toFixed(1)}MB)，桌面端最大支持 7MB（base64 编码后需 < 10MB）`);
        continue;
      }
      if (att.type === "image" || att.type === "video") {
        const reader = new FileReader();
        reader.onload = () => {
          att.previewUrl = att.type === "image" ? reader.result as string : undefined;
          att.url = reader.result as string;
          setPendingAttachments((prev) => [...prev, att]);
        };
        reader.readAsDataURL(file);
      } else {
        setPendingAttachments((prev) => [...prev, att]);
        uploadFile(file, file.name)
          .then((serverUrl) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a.name === att.name && a.type === att.type && !a.url
                ? { ...a, url: `${apiBase}${serverUrl}` } : a)
            );
          })
          .catch(() => {
            setPendingAttachments((prev) =>
              prev.filter((a) => !(a.name === att.name && a.type === att.type && !a.url)));
          });
      }
    }
    e.target.value = "";
  }, []);

  // ── 粘贴图片 ──
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) continue;
        const reader = new FileReader();
        reader.onload = () => {
          setPendingAttachments((prev) => [...prev, {
            type: "image",
            name: `粘贴图片-${Date.now()}.png`,
            previewUrl: reader.result as string,
            url: reader.result as string,
            size: file.size,
            mimeType: file.type,
          }]);
        };
        reader.readAsDataURL(file);
      }
    }
  }, []);

  // ── 拖拽图片/文件 (Tauri native webview-scoped drag-drop) ──
  const [dragOver, setDragOver] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | null = null;

    (async () => {
      try {
        const webview = getCurrentWebview();
        // onDragDropEvent: official Tauri v2 webview-scoped API
        unlisten = await webview.onDragDropEvent((event) => {
          if (cancelled) return;
          const payload = event.payload as any;
          console.log("[DragDrop] event:", payload.type, payload);
          if (payload.type === "over" || payload.type === "enter") {
            setDragOver(true);
          } else if (payload.type === "leave" || payload.type === "cancel") {
            setDragOver(false);
          } else if (payload.type === "drop") {
            setDragOver(false);
            const paths: string[] = payload.paths || [];
            console.log("[DragDrop] dropped paths:", paths);
            for (const filePath of paths) {
              const name = filePath.split(/[\\/]/).pop() || "file";
              const ext = (name.split(".").pop() || "").toLowerCase();
              const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
              const isVideo = ["mp4", "webm", "avi", "mov", "mkv"].includes(ext);
              const mimeMap: Record<string, string> = {
                png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
                gif: "image/gif", webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
                mp4: "video/mp4", webm: "video/webm", avi: "video/x-msvideo",
                mov: "video/quicktime", mkv: "video/x-matroska",
                pdf: "application/pdf", txt: "text/plain", md: "text/plain",
                json: "application/json", csv: "text/csv",
              };
              const mimeType = mimeMap[ext] || "application/octet-stream";
              invoke<string>("read_file_base64", { path: filePath })
                .then((dataUrl) => {
                  if (cancelled) return;
                  if (isVideo) {
                    const commaIdx = dataUrl.indexOf(",");
                    const base64Len = commaIdx >= 0 ? dataUrl.length - commaIdx - 1 : dataUrl.length;
                    const estimatedSize = base64Len * 3 / 4;
                    const VIDEO_MAX_SIZE = 7 * 1024 * 1024;
                    if (estimatedSize > VIDEO_MAX_SIZE) {
                      alert(`视频文件过大 (${(estimatedSize / 1024 / 1024).toFixed(1)}MB)，最大支持 7MB（base64 编码后需 < 10MB）`);
                      return;
                    }
                  }
                  console.log("[DragDrop] file read OK:", name, dataUrl.length, "bytes");
                  setPendingAttachments((prev) => [...prev, {
                    type: isImage ? "image" : isVideo ? "video" : "file",
                    name,
                    previewUrl: isImage ? dataUrl : undefined,
                    url: dataUrl,
                    mimeType,
                  }]);
                })
                .catch((err) => console.error("[DragDrop] read_file_base64 failed:", name, err));
            }
          }
        });
        console.log("[DragDrop] listener registered via onDragDropEvent");
      } catch (e) {
        console.warn("[DragDrop] onDragDropEvent failed, trying fallback:", e);
        // Fallback: try webview.listen for individual events
        try {
          const webview = getCurrentWebview();
          const unlisteners: Array<() => void> = [];
          const u1 = await webview.listen<any>("tauri://drag-enter", () => { if (!cancelled) setDragOver(true); });
          const u2 = await webview.listen<any>("tauri://drag-over", () => { if (!cancelled) setDragOver(true); });
          const u3 = await webview.listen<any>("tauri://drag-leave", () => { if (!cancelled) setDragOver(false); });
          const u4 = await webview.listen<any>("tauri://drag-drop", (ev) => {
            if (cancelled) return;
            setDragOver(false);
            const paths: string[] = ev.payload?.paths || [];
            console.log("[DragDrop-fallback] dropped paths:", paths);
            for (const filePath of paths) {
              const name = filePath.split(/[\\/]/).pop() || "file";
              const ext = (name.split(".").pop() || "").toLowerCase();
              const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
              const isVideo = ["mp4", "webm", "avi", "mov", "mkv"].includes(ext);
              const videoMimeMap: Record<string, string> = {
                mp4: "video/mp4", webm: "video/webm", avi: "video/x-msvideo",
                mov: "video/quicktime", mkv: "video/x-matroska",
              };
              invoke<string>("read_file_base64", { path: filePath })
                .then((dataUrl) => {
                  if (cancelled) return;
                  if (isVideo) {
                    const commaIdx = dataUrl.indexOf(",");
                    const base64Len = commaIdx >= 0 ? dataUrl.length - commaIdx - 1 : dataUrl.length;
                    const estimatedSize = base64Len * 3 / 4;
                    const VIDEO_MAX_SIZE = 7 * 1024 * 1024;
                    if (estimatedSize > VIDEO_MAX_SIZE) {
                      alert(`视频文件过大 (${(estimatedSize / 1024 / 1024).toFixed(1)}MB)，最大支持 7MB（base64 编码后需 < 10MB）`);
                      return;
                    }
                  }
                  const mimeType = isImage ? `image/${ext === "jpg" ? "jpeg" : ext}`
                    : isVideo ? (videoMimeMap[ext] || "video/mp4")
                    : "application/octet-stream";
                  setPendingAttachments((prev) => [...prev, {
                    type: isImage ? "image" : isVideo ? "video" : "file",
                    name,
                    previewUrl: isImage ? dataUrl : undefined,
                    url: dataUrl,
                    mimeType,
                  }]);
                })
                .catch((err) => console.error("[DragDrop-fallback] read failed:", err));
            }
          });
          unlisteners.push(u1, u2, u3, u4);
          unlisten = () => unlisteners.forEach((u) => u());
          console.log("[DragDrop] fallback listeners registered");
        } catch (e2) {
          console.error("[DragDrop] all methods failed:", e2);
        }
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // ── 语音录制 ──
  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      // 停止录制
      mediaRecorderRef.current?.stop();
      setIsRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      audioChunksRef.current = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const localPreview = URL.createObjectURL(blob);
        const filename = `voice-${Date.now()}.webm`;
        // 立即添加为"上传中"状态（有预览但无 url）
        const tempAtt: ChatAttachment = {
          type: "voice",
          name: filename,
          previewUrl: localPreview,
          size: blob.size,
          mimeType: "audio/webm",
        };
        setPendingAttachments((prev) => [...prev, tempAtt]);
        // 异步上传到后端
        uploadFile(blob, filename)
          .then((serverUrl) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a.name === filename && a.type === "voice"
                ? { ...a, url: `${apiBase}${serverUrl}` } : a)
            );
          })
          .catch(() => {
            setPendingAttachments((prev) => prev.filter((a) => !(a.name === filename && a.type === "voice")));
          });
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.start();
      setIsRecording(true);
    } catch {
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: "无法访问麦克风，请检查浏览器权限设置。", timestamp: Date.now() }]);
    }
  }, [isRecording]);

  // ── 输入框键盘处理 ──
  const handleInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashOpen) {
      // 与 SlashCommandPanel 保持一致的过滤逻辑（包含 description）
      const q = slashFilter.toLowerCase();
      const filtered = slashCommands.filter((c) =>
        c.id.includes(q) || c.label.includes(q) || c.description.includes(q),
      );
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashSelectedIdx((i) => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashSelectedIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const cmd = filtered[slashSelectedIdx];
        if (cmd) {
          cmd.action("");
          setInputText("");
          setSlashOpen(false);
        }
      } else if (e.key === "Escape") {
        setSlashOpen(false);
      }
      return;
    }

    if (isStreaming) {
      // Streaming 状态:
      //   有文本 + Ctrl+Enter = 立即插入
      //   有文本 + Enter     = 排队
      //   空文本 + Enter     = 取队列第一条立即插入
      // 用 DOM 真实值做前置检查，防止 React 闭包陈旧导致重复排队/插入
      const domText = (e.target as HTMLTextAreaElement).value.trim();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (domText) {
          handleInsertMessage(domText);
          setInputText("");
          if (inputRef.current) { inputRef.current.value = ""; inputRef.current.style.height = "auto"; }
        }
      } else if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (domText) {
          handleQueueMessage();
        } else if (messageQueue.length > 0) {
          // 输入为空但队列有消息：取第一条立即插入
          const first = messageQueue[0];
          setMessageQueue(prev => prev.slice(1));
          handleInsertMessage(first.text);
        }
      }
    } else {
      // 非 Streaming 状态: Enter / Ctrl+Enter 都发送
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        sendMessage();
      } else if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        sendMessage();
      }
    }
  }, [slashOpen, slashFilter, slashCommands, slashSelectedIdx, sendMessage, isStreaming, inputText, handleInsertMessage, handleQueueMessage, messageQueue]);

  // ── 输入变化处理 ──
  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInputText(val);

    // 斜杠命令检测
    if (val.startsWith("/") && !val.includes(" ")) {
      setSlashOpen(true);
      setSlashFilter(val.slice(1));
      setSlashSelectedIdx(0);
    } else {
      setSlashOpen(false);
    }
  }, []);

  // ── 未启动服务提示 ──
  if (!serviceRunning) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "60px 40px" }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}><IconMessageCircle size={48} /></div>
        <div className="cardTitle">{t("chat.title")}</div>
        <div className="cardHint" style={{ marginTop: 8, marginBottom: 20 }}>
          {t("chat.serviceHint")}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0 }}>
      {/* 对话历史侧边栏 */}
      {sidebarOpen && (
        <div
          style={{
            width: 260,
            minWidth: 260,
            borderRight: "1px solid var(--line)",
            background: "var(--panel)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div style={{ padding: "14px 14px 10px", borderBottom: "1px solid var(--line)" }}>
            <button className="btnPrimary" onClick={newConversation} style={{ width: "100%", fontSize: 13 }}>
              <IconPlus size={12} /> {t("chat.newConversation")}
            </button>
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: "8px 6px" }}>
            {conversations.map((conv) => (
              <div
                key={conv.id}
                onClick={() => {
                  setActiveConvId(conv.id);
                }}
                style={{
                  padding: "10px 12px",
                  borderRadius: 10,
                  cursor: "pointer",
                  marginBottom: 4,
                  background: conv.id === activeConvId ? "rgba(14,165,233,0.08)" : "transparent",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 4,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {conv.title}
                  </div>
                  <div style={{ fontSize: 11, opacity: 0.5, marginTop: 2 }}>
                    {formatDate(conv.timestamp)} · {t("im.messageCount", { count: conv.messageCount })}
                  </div>
                </div>
                <button
                  onClick={(e) => deleteConversation(conv.id, e)}
                  title={t("chat.deleteConversation")}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    padding: 4,
                    borderRadius: 6,
                    opacity: 0.3,
                    flexShrink: 0,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    marginTop: 1,
                    transition: "opacity 0.15s, background 0.15s",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.opacity = "0.8"; e.currentTarget.style.background = "rgba(239,68,68,0.1)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.opacity = "0.3"; e.currentTarget.style.background = "none"; }}
                >
                  <IconTrash size={13} />
                </button>
              </div>
            ))}
            {conversations.length === 0 && (
              <div style={{ padding: 16, textAlign: "center", opacity: 0.4, fontSize: 13 }}>
                {t("common.noData")}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 主聊天区 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Chat top bar */}
        <div className="chatTopBar">
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            className="chatTopBarBtn"
            style={{ background: sidebarOpen ? "rgba(14,165,233,0.08)" : "transparent" }}
            title={t("chat.newConversation")}
          >
            <IconMenu size={16} />
          </button>
          <div style={{ flex: 1 }} />

          {/* 思维链开关 */}
          <button
            onClick={() => setShowChain(v => !v)}
            className="chatTopBarBtn chainToggleBtn"
            title={showChain ? t("chat.hideChain") : t("chat.showChain")}
            style={{ opacity: showChain ? 1 : 0.4 }}
          >
            <IconZap size={14} />
          </button>

          {/* 模式切换: bubble <-> flat */}
          <button
            onClick={() => setDisplayMode(v => v === "bubble" ? "flat" : "bubble")}
            className="chatTopBarBtn modeToggleBtn"
            title={displayMode === "bubble" ? t("chat.flatMode") : t("chat.bubbleMode")}
          >
            <IconMessageCircle size={14} />
            <span style={{ fontSize: 11, marginLeft: 2 }}>
              {displayMode === "bubble" ? t("chat.flatMode") : t("chat.bubbleMode")}
            </span>
          </button>

          <button onClick={newConversation} className="chatTopBarBtn">
            <IconPlus size={14} /> <span>{t("chat.newConversation")}</span>
          </button>
        </div>

        {/* 消息列表 */}
        <div style={{ flex: 1, overflow: "auto", padding: "16px 20px", minHeight: 0 }}>
          {messages.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", opacity: 0.4 }}>
              <div style={{ marginBottom: 12 }}><IconMessageCircle size={48} /></div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>{t("chat.emptyTitle")}</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>{t("chat.emptyDesc")}</div>
            </div>
          )}
          {messages.map((msg) =>
            displayMode === "flat" ? (
              <FlatMessageItem key={msg.id} msg={msg} onAskAnswer={handleAskAnswer} apiBaseUrl={apiBaseUrl} showChain={showChain} onSkipStep={handleSkipStep} />
            ) : (
              <MessageBubble key={msg.id} msg={msg} onAskAnswer={handleAskAnswer} apiBaseUrl={apiBaseUrl} showChain={showChain} onSkipStep={handleSkipStep} />
            )
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* 浮动 Plan 进度条 —— 贴在输入框上方，仅显示进行中的 plan */}
        {(() => {
          const activePlan = [...messages].reverse().find((m) => m.plan && m.plan.status !== "completed" && m.plan.status !== "failed" && m.plan.status !== "cancelled")?.plan;
          return activePlan ? <FloatingPlanBar plan={activePlan} /> : null;
        })()}

        {/* 附件预览栏 */}
        {pendingAttachments.length > 0 && (
          <div style={{ padding: "12px 16px 8px", borderTop: "1px solid var(--line)", display: "flex", flexWrap: "wrap", gap: 12, background: "var(--panel)" }}>
            {pendingAttachments.map((att, idx) => (
              <AttachmentPreview
                key={`${att.name}-${att.type}-${idx}`}
                att={att}
                onRemove={() => setPendingAttachments((prev) => prev.filter((_, i) => i !== idx))}
              />
            ))}
          </div>
        )}

        {/* Cursor-style unified input box */}
        <div
          className="chatInputArea"
          style={dragOver ? { outline: "2px dashed var(--brand)", outlineOffset: -2, background: "rgba(37,99,235,0.04)", borderRadius: 16 } : undefined}
        >
          {/* Slash command panel */}
          {slashOpen && (
            <SlashCommandPanel
              commands={slashCommands}
              filter={slashFilter}
              onSelect={(cmd) => {
                cmd.action("");
                setInputText("");
                setSlashOpen(false);
              }}
              selectedIdx={slashSelectedIdx}
            />
          )}

          {/* Queued messages list — Cursor style */}
          {messageQueue.length > 0 && (
            <div className="queuedContainer">
              <button
                className="queuedHeader"
                onClick={() => setQueueExpanded(v => !v)}
              >
                <span className="queuedHeaderChevron">
                  {queueExpanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
                </span>
                <span className="queuedHeaderLabel">
                  {messageQueue.length} {t("chat.queuedCount")}
                </span>
              </button>
              {queueExpanded && (
                <div className="queuedList">
                  {messageQueue.map((qm, idx) => (
                    <div key={qm.id} className="queuedItem">
                      <span className="queuedItemIndicator">
                        <IconCircle size={10} />
                      </span>
                      <span className="queuedItemText" title={qm.text}>
                        {qm.text.length > 80 ? qm.text.slice(0, 80) + "..." : qm.text}
                      </span>
                      <div className="queuedItemActions">
                        <button
                          className="queuedItemBtn queuedItemSendBtn"
                          onClick={() => handleSendQueuedNow(qm.id)}
                          title={t("chat.sendNow")}
                        >
                          <IconSend size={12} />
                        </button>
                        <button
                          className="queuedItemBtn"
                          onClick={() => handleEditQueued(qm.id)}
                          title={t("chat.editMessage")}
                        >
                          <IconEdit size={13} />
                        </button>
                        <button
                          className="queuedItemBtn"
                          onClick={() => handleMoveQueued(qm.id, "up")}
                          disabled={idx === 0}
                          title="Move up"
                        >
                          <IconChevronUp size={13} />
                        </button>
                        <button
                          className="queuedItemBtn queuedItemDeleteBtn"
                          onClick={() => handleRemoveQueued(qm.id)}
                          title={t("chat.deleteQueued")}
                        >
                          <IconTrash size={13} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className={`chatInputBox ${planMode ? "chatInputBoxPlan" : ""}`}>
            {/* Top row: compact model picker */}
            <div className="chatInputTop" ref={modelMenuRef} style={{ position: "relative" }}>
              <button
                className="chatModelPickerBtn"
                onClick={() => setModelMenuOpen((v) => !v)}
              >
                <span className="chatModelPickerLabel">
                  {selectedEndpoint === "auto"
                    ? t("chat.selectModel")
                    : (() => { const ep = endpoints.find(e => e.name === selectedEndpoint); return ep ? ep.model : selectedEndpoint; })()}
                </span>
                <IconChevronDown size={12} />
              </button>
              {modelMenuOpen && (
                <div className="chatModelMenu">
                  <div
                    className={`chatModelMenuItem ${selectedEndpoint === "auto" ? "chatModelMenuItemActive" : ""}`}
                    onClick={() => { setSelectedEndpoint("auto"); setModelMenuOpen(false); }}
                  >
                    {t("chat.selectModel")}
                  </div>
                  {endpoints.map((ep) => (
                    <div
                      key={ep.name}
                      className={`chatModelMenuItem ${selectedEndpoint === ep.name ? "chatModelMenuItemActive" : ""}`}
                      onClick={() => { setSelectedEndpoint(ep.name); setModelMenuOpen(false); }}
                    >
                      <span style={{ fontWeight: 600 }}>{ep.model}</span>
                      <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{ep.name}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Textarea */}
            <textarea
              ref={inputRef}
              value={inputText}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              onPaste={handlePaste}
              placeholder={isStreaming ? t("chat.queueHint") : planMode ? `Plan ${t("chat.planMode")}` : t("chat.placeholder")}
              rows={1}
              className="chatInputTextarea"
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = "auto";
                el.style.height = Math.min(el.scrollHeight, 120) + "px";
              }}
            />

            {/* Bottom toolbar */}
            <div className="chatInputToolbar">
              <div className="chatInputToolbarLeft">
                <button onClick={() => fileInputRef.current?.click()} className="chatInputIconBtn" title={t("chat.attach")}>
                  <IconPaperclip size={16} />
                </button>
                <input ref={fileInputRef} type="file" multiple accept="image/*,video/*,audio/*,.pdf,.txt,.md,.py,.js,.ts,.json,.csv" style={{ display: "none" }} onChange={handleFileSelect} />

                <button onClick={toggleRecording} className={`chatInputIconBtn ${isRecording ? "chatInputIconBtnDanger" : ""}`} title={isRecording ? t("chat.stopRecording") : t("chat.voice")}>
                  {isRecording ? <IconStopCircle size={16} /> : <IconMic size={16} />}
                </button>

                <button onClick={() => setPlanMode((v) => !v)} className={`chatInputIconBtn ${planMode ? "chatInputIconBtnActive" : ""}`} title={t("chat.planMode")}>
                  <IconPlan size={16} />
                  <span style={{ fontSize: 11, marginLeft: 2 }}>Plan</span>
                </button>

                {/* 深度思考按钮 + 下拉菜单 */}
                <div ref={thinkingMenuRef} style={{ position: "relative", display: "inline-flex" }}>
                  <button
                    onClick={() => {
                      if (thinkingMode === "auto") {
                        setThinkingMode("on");
                      } else if (thinkingMode === "on") {
                        setThinkingMode("off");
                      } else {
                        setThinkingMode("auto");
                      }
                    }}
                    onContextMenu={(e) => { e.preventDefault(); setThinkingMenuOpen((v) => !v); }}
                    className={`chatInputIconBtn ${thinkingMode === "on" ? "chatInputIconBtnActive" : thinkingMode === "off" ? "chatInputIconBtnOff" : ""}`}
                    title={`深度思考: ${thinkingMode === "on" ? "开启" : thinkingMode === "off" ? "关闭" : "自动"} (右键设置深度)`}
                  >
                    <IconZap size={16} />
                    <span style={{ fontSize: 11, marginLeft: 2 }}>
                      {thinkingMode === "on" ? "Think" : thinkingMode === "off" ? "NoThink" : "Auto"}
                    </span>
                  </button>
                  {thinkingMenuOpen && (
                    <div className="chatThinkingMenu">
                      <div className="chatThinkingMenuSection">思考模式</div>
                      {(["auto", "on", "off"] as const).map((mode) => (
                        <div
                          key={mode}
                          className={`chatThinkingMenuItem ${thinkingMode === mode ? "chatThinkingMenuItemActive" : ""}`}
                          onClick={() => { setThinkingMode(mode); setThinkingMenuOpen(false); }}
                        >
                          <span>{{ auto: "🤖 自动", on: "🧠 开启", off: "⚡ 关闭" }[mode]}</span>
                          <span style={{ fontSize: 10, opacity: 0.5 }}>{{ auto: "系统决定", on: "强制深度思考", off: "快速回复" }[mode]}</span>
                        </div>
                      ))}
                      <div className="chatThinkingMenuDivider" />
                      <div className="chatThinkingMenuSection">思考深度</div>
                      {(["low", "medium", "high"] as const).map((depth) => (
                        <div
                          key={depth}
                          className={`chatThinkingMenuItem ${thinkingDepth === depth ? "chatThinkingMenuItemActive" : ""}`}
                          onClick={() => { setThinkingDepth(depth); setThinkingMenuOpen(false); }}
                        >
                          <span>{{ low: "💨 低", medium: "⚖️ 中", high: "🔬 高" }[depth]}</span>
                          <span style={{ fontSize: 10, opacity: 0.5 }}>{{ low: "快速响应", medium: "平衡模式", high: "深度推理" }[depth]}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="chatInputToolbarRight">
                {/* Context usage ring */}
                {contextLimit > 0 && (() => {
                  const pct = Math.min(contextTokens / contextLimit, 1);
                  const pctLabel = (pct * 100).toFixed(1);
                  const fmtK = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
                  const r = 9; const sw = 2; const circ = 2 * Math.PI * r;
                  const offset = circ * (1 - pct);
                  const color = pct > 0.95 ? "#ef4444" : pct > 0.8 ? "#f59e0b" : pct > 0.5 ? "#3b82f6" : "#999";
                  return (
                    <div
                      style={{ position: "relative", display: "inline-flex", alignItems: "center", cursor: "default", marginRight: 4 }}
                      onMouseEnter={() => setContextTooltipVisible(true)}
                      onMouseLeave={() => setContextTooltipVisible(false)}
                    >
                      <svg width={22} height={22} viewBox="0 0 22 22">
                        <circle cx={11} cy={11} r={r} fill="none" stroke="var(--line)" strokeWidth={sw} />
                        <circle cx={11} cy={11} r={r} fill="none" stroke={color} strokeWidth={sw}
                          strokeDasharray={circ} strokeDashoffset={offset}
                          strokeLinecap="round" transform="rotate(-90 11 11)" style={{ transition: "stroke-dashoffset 0.4s ease" }} />
                      </svg>
                      {contextTooltipVisible && (
                        <div style={{
                          position: "absolute", bottom: "calc(100% + 6px)", right: 0,
                          background: "rgba(0,0,0,0.82)", color: "#fff", fontSize: 11, fontWeight: 500,
                          padding: "4px 8px", borderRadius: 6, whiteSpace: "nowrap", pointerEvents: "none",
                          zIndex: 100,
                        }}>
                          {pctLabel}% · {fmtK(contextTokens)} / {fmtK(contextLimit)} context used
                        </div>
                      )}
                    </div>
                  );
                })()}
                {isStreaming ? (
                  inputText.trim() ? (
                    <button
                      onClick={handleQueueMessage}
                      className="chatInputSendBtn"
                      title={t("chat.queueHint")}
                    >
                      <IconSend size={14} />
                    </button>
                  ) : (
                    <button onClick={handleCancelTask} className="chatInputSendBtn chatInputStopBtn" title={t("chat.stopGeneration")}>
                      <IconStop size={14} />
                    </button>
                  )
                ) : (
                  <button
                    onClick={() => sendMessage()}
                    className="chatInputSendBtn"
                    disabled={!inputText.trim() && pendingAttachments.length === 0}
                    title={t("chat.send")}
                  >
                    <IconSend size={14} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
