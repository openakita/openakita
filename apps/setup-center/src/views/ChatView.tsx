// ─── ChatView: 完整 AI 聊天页面 ───
// 支持流式 MD 渲染、思考内容折叠、Plan/Todo、斜杠命令、多模态、多 Agent、端点选择

import { useEffect, useMemo, useRef, useState, useCallback, memo } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { setThemePref } from "../theme";
import type { Theme } from "../theme";
import { invoke, downloadFile, openFileWithDefault, showInFolder, readFileBase64, onDragDrop, IS_TAURI, IS_WEB, onWsEvent, logger } from "../platform";
import { getAccessToken } from "../platform/auth";
import { safeFetch } from "../providers";
import type {
  ChatMessage,
  ChatConversation,
  ConversationStatus,
  ChatToolCall,
  ChatPlan,
  ChatTodoStep,
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
import { genId, formatTime, formatDate, timeAgo } from "../utils";
import {
  IconSend, IconPaperclip, IconMic, IconStopCircle,
  IconPlan, IconPlus, IconMenu, IconStop, IconX,
  IconCheck, IconLoader, IconCircle, IconPlay, IconMinus,
  IconChevronDown, IconChevronUp, IconMessageCircle, IconChevronRight,
  IconImage, IconRefresh, IconClipboard, IconTrash, IconZap,
  IconMask, IconBot, IconUsers, IconHelp, IconEdit, IconDownload,
  IconPin, IconSearch, IconCircleDot, IconXCircle,
  IconBuilding, IconShield, IconAlertCircle,
  getFileTypeIcon,
} from "../icons";

// ─── Markdown 渲染库延迟加载 ───
// react-markdown v10 及其依赖使用 \p{ID_Start} Unicode 属性转义和 (?<=...) 后行断言，
// 旧版 WebKit (macOS < 13.3) 不支持这些正则语法，会导致 JS 引擎 SyntaxError。
// 通过运行时特性检测 + 动态 import() 实现：
//   - 支持的浏览器：加载完整 markdown 渲染
//   - 不支持的浏览器：回退为纯文本，app 正常运行
type MdModules = {
  ReactMarkdown: typeof import("react-markdown").default;
  remarkGfm: typeof import("remark-gfm").default;
  rehypeHighlight: typeof import("rehype-highlight").default;
};
let _mdModules: MdModules | null = null;
let _mdLoadAttempted = false;

function useMdModules(): MdModules | null {
  const [mods, setMods] = useState<MdModules | null>(() => _mdModules);
  useEffect(() => {
    if (_mdModules) { setMods(_mdModules); return; }
    if (_mdLoadAttempted) return;
    _mdLoadAttempted = true;
    try {
      new RegExp("\\p{ID_Start}", "u");
      new RegExp("(?<=a)b");
    } catch { return; }
    Promise.all([
      import("react-markdown"),
      import("remark-gfm"),
      import("rehype-highlight"),
    ]).then(([md, gfm, hl]) => {
      _mdModules = {
        ReactMarkdown: md.default,
        remarkGfm: gfm.default,
        rehypeHighlight: hl.default,
      };
      setMods(_mdModules);
    }).catch((err) => {
      console.warn("[ChatView] markdown modules unavailable:", err);
    });
  }, []);
  return mods;
}

let _artifactClickTimer: ReturnType<typeof setTimeout> | null = null;

function appendAuthToken(url: string): string {
  if (IS_TAURI) return url;
  const token = getAccessToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

/** Strip legacy inline execution summaries from assistant message content */
function stripLegacySummary(content: string): string {
  if (!content) return content;
  const markers = ["\n\n[子Agent工作总结]", "\n\n[执行摘要]"];
  for (const m of markers) {
    const idx = content.indexOf(m);
    if (idx !== -1) content = content.substring(0, idx);
  }
  if (content.startsWith("[执行摘要]") || content.startsWith("[子Agent工作总结]")) return "";
  return content;
}

// ─── 排队消息类型 ───
type QueuedMessage = {
  id: string;
  text: string;
  timestamp: number;
  convId: string;
};

/**
 * 将消息数组安全写入 localStorage。
 * 策略：先尝试完整保存；若配额溢出则剥离 thinkingChain 后重试
 * （thinkingChain 可由后端 chain_summary 重建）。
 */
function saveMessagesToStorage(key: string, msgs: ChatMessage[]): boolean {
  const base = msgs.map(({ streaming, ...rest }) => rest);
  try {
    localStorage.setItem(key, JSON.stringify(base));
    return true;
  } catch {
    // 配额溢出 → 剥离最大数据块后重试
    const slim = msgs.map(({ streaming, thinkingChain, ...rest }) => rest);
    try {
      localStorage.setItem(key, JSON.stringify(slim));
      return true;
    } catch {
      return false;
    }
  }
}

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
        result: t.result_preview || t.input_preview,
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
        result: t.result_preview || t.input_preview,
        status: "done" as const,
        description: t.input_preview,
      })),
    };
  });
}

/** 将 ask_user 的结构化回答（JSON / option ID）转为人类可读文本 */
function formatAskUserAnswer(answer: string, askUser: ChatAskUser): string {
  const questions: ChatAskQuestion[] = askUser.questions?.length
    ? askUser.questions
    : [{ id: "__single__", prompt: askUser.question, options: askUser.options }];
  try {
    const parsed = JSON.parse(answer);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const formatted = questions.map((q) => {
        const val = parsed[q.id];
        if (!val) return null;
        const vals = Array.isArray(val) ? val : [val];
        const labels = vals.map((v: string) => {
          if (v.startsWith("OTHER:")) return v.slice(6);
          return q.options?.find((o) => o.id === v)?.label ?? v;
        });
        return `${q.prompt}: ${labels.join(", ")}`;
      }).filter(Boolean).join(" | ");
      if (formatted) return formatted;
    }
  } catch { /* not JSON */ }
  const options = askUser.options || questions[0]?.options;
  const opt = options?.find((o) => o.id === answer);
  if (opt) return opt.label;
  if (answer.includes(",") && options) {
    const ids = answer.split(",");
    if (ids.every((id) => id.startsWith("OTHER:") || options.some((o) => o.id === id))) {
      return ids.map((id) => {
        if (id.startsWith("OTHER:")) return id.slice(6);
        return options.find((o) => o.id === id)?.label ?? id;
      }).join(", ");
    }
  }
  return answer;
}

/** 用后端数据补全本地消息中缺失的 content / thinkingChain */
function patchMessagesWithBackend(
  localMsgs: ChatMessage[],
  backendMsgs: { role: string; content: string; chain_summary?: ChainSummaryItem[]; artifacts?: ChatArtifact[] }[],
): ChatMessage[] {
  const backendAssistant = backendMsgs.filter((m) => m.role === "assistant");
  let aIdx = 0;
  let changed = false;
  const patched = localMsgs.map((m) => {
    if (m.role !== "assistant") return m;
    const backend = backendAssistant[aIdx++];
    if (!backend) return m;

    const patches: Partial<ChatMessage> = {};

    if (backend.content && !m.askUser && (!m.content || m.content.length < backend.content.length)) {
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

    if (!m.artifacts?.length && backend.artifacts?.length) {
      patches.artifacts = backend.artifacts;
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
  | { type: "text"; content?: string; text?: string }
  | { type: "tool_call_start"; tool: string; args: Record<string, unknown>; id?: string }
  | { type: "tool_call_end"; tool: string; result: string; id?: string; is_error?: boolean }
  | { type: "todo_created"; plan: ChatPlan }
  | { type: "todo_step_updated"; stepId?: string; stepIdx?: number; status: string }
  | { type: "todo_completed" }
  | { type: "todo_cancelled" }
  | { type: "ask_user"; question: string; options?: { id: string; label: string }[]; allow_multiple?: boolean; questions?: { id: string; prompt: string; options?: { id: string; label: string }[]; allow_multiple?: boolean }[] }
  | { type: "user_insert"; content: string }
  | { type: "agent_switch"; agentName: string; reason: string }
  | { type: "agent_handoff"; from_agent: string; to_agent: string; reason?: string }
  | { type: "artifact"; artifact_type: string; file_url: string; path: string; name: string; caption: string; size?: number }
  | { type: "security_confirm"; tool: string; args: Record<string, unknown>; id?: string; reason: string; risk_level: string; needs_sandbox: boolean }
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
    case "create_todo":
      return `Created todo: ${String(args.task_summary || "").slice(0, 40)}`;
    case "update_todo_step":
      return `Updated todo step ${args.step_index ?? ""}`;
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
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", background: "rgba(37,99,235,0.03)", userSelect: "none" }}
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
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", background: "rgba(37,99,235,0.04)", userSelect: "none" }}
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
              data-slot="skip"
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
    <div className="floatingTodoBar">
      {/* 折叠头部：可点击展开 */}
      <div className="floatingTodoHeader" onClick={() => setExpanded((v) => !v)}>
        <div className="floatingTodoHeaderLeft">
          <IconClipboard size={14} style={{ opacity: 0.6 }} />
          <span className="floatingTodoTitle">
            {typeof plan.taskSummary === "string" ? plan.taskSummary : JSON.stringify(plan.taskSummary)}
          </span>
        </div>
        <div className="floatingTodoHeaderRight">
          <span className="floatingTodoProgress">{completed}/{total}</span>
          <span className="floatingTodoChevron" style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}>
            <IconChevronDown size={14} />
          </span>
        </div>
      </div>

      {/* 进度条 */}
      <div className="floatingTodoProgressBar">
        <div className="floatingTodoProgressFill" style={{ width: `${pct}%` }} />
      </div>

      {/* 折叠态：只显示当前活跃步骤 */}
      {!expanded && activeStep && !allDone && (
        <div className="floatingTodoActive">
          <span className="floatingTodoActiveIcon"><IconPlay size={11} /></span>
          <span className="floatingTodoActiveText">{activeIdx + 1}/{total} {activeDesc}</span>
        </div>
      )}
      {!expanded && allDone && (
        <div className="floatingTodoActive floatingTodoDone">
          <span className="floatingTodoActiveIcon"><IconCheck size={12} /></span>
          <span className="floatingTodoActiveText">全部完成</span>
        </div>
      )}

      {/* 展开态：完整步骤列表 */}
      {expanded && (
        <div className="floatingTodoSteps">
          {plan.steps.map((step, idx) => (
            <FloatingTodoStepItem key={step.id || idx} step={step} idx={idx} />
          ))}
        </div>
      )}
    </div>
  );
}

function FloatingTodoStepItem({ step, idx }: { step: ChatTodoStep; idx: number }) {
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
    <div className={`floatingTodoStepRow ${step.status === "in_progress" ? "floatingTodoStepActive" : ""}`}>
      <span className="floatingTodoStepIcon" style={{ color }}>{icon}</span>
      <div className="floatingTodoStepContent">
        <span style={{ opacity: step.status === "skipped" || step.status === "cancelled" ? 0.5 : 1 }}>{idx + 1}. {descText}</span>
        {resultText && <div className="floatingTodoStepResult">{resultText}</div>}
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
  onSubmit,
}: {
  question: ChatAskQuestion;
  selected: Set<string>;
  onSelect: (optId: string) => void;
  otherText: string;
  onOtherText: (v: string) => void;
  showOther: boolean;
  onToggleOther: () => void;
  letterOffset?: number;
  onSubmit?: () => void;
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
              onKeyDown={(e) => { if (e.key === "Escape") onToggleOther(); if (e.key === "Enter" && otherText.trim()) onSubmit?.(); }}
            />
          )}
        </div>
      ) : (
        <input
          autoFocus
          value={otherText}
          onChange={(e) => onOtherText(e.target.value)}
          placeholder={t("chat.askPlaceholder")}
          style={{ width: "100%", fontSize: 13, padding: "7px 12px", borderRadius: 8, border: "1px solid rgba(124,58,237,0.25)", outline: "none", boxSizing: "border-box" }}
          onKeyDown={(e) => { if (e.key === "Enter" && otherText.trim()) onSubmit?.(); }}
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
    const result: Record<string, string | string[]> = {};
    normalizedQuestions.forEach((q) => {
      const sel = selections[q.id];
      const other = otherTexts[q.id]?.trim();
      const arr = sel ? Array.from(sel) : [];
      if (other) arr.push(`OTHER:${other}`);
      if (arr.length === 0 && !other) return;
      result[q.id] = q.allow_multiple ? arr : (arr[0] || other || "");
    });
    if (Object.keys(result).length === 0) return;
    onAnswer(JSON.stringify(result));
  }, [isSingle, normalizedQuestions, selections, otherTexts, onAnswer]);

  // ─── 已回答状态 ───
  if (ask.answered) {
    const displayAnswer = formatAskUserAnswer(ask.answer || "", ask);
    return (
      <div style={{ margin: "8px 0", padding: "10px 14px", borderRadius: 10, background: "rgba(37,99,235,0.06)", border: "1px solid rgba(37,99,235,0.15)" }}>
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
            onSubmit={isSingle ? handleSubmit : undefined}
          />
        ))}
      </div>
      {(!isSingle || normalizedQuestions.some((q) => q.allow_multiple || !q.options?.length)) && (
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
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
        boxShadow: "0 -12px 48px rgba(17,24,39,0.18)",
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
            background: idx === selectedIdx ? "rgba(37,99,235,0.14)" : "transparent",
            borderTop: idx === 0 ? "none" : "1px solid rgba(17,24,39,0.1)",
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
             cmd.id === "org" ? <IconUsers size={16} /> :
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

const MessageBubble = memo(function MessageBubble({
  msg,
  onAskAnswer,
  apiBaseUrl,
  showChain = true,
  onSkipStep,
  onImagePreview,
  mdModules,
}: {
  msg: ChatMessage;
  onAskAnswer?: (msgId: string, answer: string) => void;
  apiBaseUrl?: string;
  showChain?: boolean;
  onSkipStep?: () => void;
  onImagePreview?: (url: string, name: string) => void;
  mdModules?: MdModules | null;
}) {
  const { t } = useTranslation();
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
        {msg.content && (isUser ? msg.content : stripLegacySummary(msg.content)) && (
          <div className={isUser ? "chatMdContent chatMdContentUser" : "chatMdContent"}>
            {mdModules ? (
              <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]} rehypePlugins={[mdModules.rehypeHighlight]}>
                {isUser ? msg.content : stripLegacySummary(msg.content)}
              </mdModules.ReactMarkdown>
            ) : (
              <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{isUser ? msg.content : stripLegacySummary(msg.content)}</pre>
            )}
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
              const rawUrl = art.file_url.startsWith("http")
                ? art.file_url
                : `${apiBaseUrl || ""}${art.file_url}`;
              const fullUrl = appendAuthToken(rawUrl);
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
                        cursor: "pointer",
                      }}
                      onClick={() => {
                        if (_artifactClickTimer) clearTimeout(_artifactClickTimer);
                        _artifactClickTimer = setTimeout(() => {
                          onImagePreview?.(fullUrl, art.name || "image");
                        }, 250);
                      }}
                      onDoubleClick={() => {
                        if (_artifactClickTimer) { clearTimeout(_artifactClickTimer); _artifactClickTimer = null; }
                        (async () => {
                          try {
                            const savedPath = await downloadFile(fullUrl, art.name || `image-${Date.now()}.png`);
                            await openFileWithDefault(savedPath);
                          } catch (err) {
                            logger.error("Chat", "图片打开失败", { error: String(err) });
                          }
                        })();
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
                          const savedPath = await downloadFile(fullUrl, art.name || `image-${Date.now()}.png`);
                          await showInFolder(savedPath);
                        } catch (err) {
                          logger.error("Chat", "图片下载失败", { error: String(err) });
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
              return (() => {
                const FileIcon = getFileTypeIcon(art.name || "");
                const sizeStr = art.size != null
                  ? art.size > 1048576 ? `${(art.size / 1048576).toFixed(1)} MB` : `${(art.size / 1024).toFixed(1)} KB`
                  : "";
                return (
                  <div key={i} style={{
                    display: "inline-flex", alignItems: "center", gap: 10,
                    padding: "10px 14px", borderRadius: 10, border: "1px solid var(--line)",
                    fontSize: 13, marginBottom: 4, cursor: "pointer",
                    background: "var(--panel)",
                    transition: "background 0.15s",
                  }}
                    onClick={() => {
                      if (_artifactClickTimer) clearTimeout(_artifactClickTimer);
                      _artifactClickTimer = setTimeout(async () => {
                        try {
                          const savedPath = await downloadFile(fullUrl, art.name || "file");
                          await showInFolder(savedPath);
                        } catch (err) {
                          logger.error("Chat", "文件下载失败", { error: String(err) });
                        }
                      }, 250);
                    }}
                    onDoubleClick={() => {
                      if (_artifactClickTimer) { clearTimeout(_artifactClickTimer); _artifactClickTimer = null; }
                      (async () => {
                        try {
                          const savedPath = await downloadFile(fullUrl, art.name || "file");
                          await openFileWithDefault(savedPath);
                        } catch (err) {
                          logger.error("Chat", "文件打开失败", { error: String(err) });
                        }
                      })();
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(37,99,235,0.08)"; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--panel)"; }}
                  >
                    <FileIcon size={28} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                      <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{art.name}</span>
                      <span style={{ fontSize: 11, opacity: 0.5 }}>
                        {sizeStr}{sizeStr && art.caption ? " · " : ""}{art.caption || ""}
                      </span>
                    </div>
                    <IconDownload size={14} style={{ opacity: 0.4, flexShrink: 0 }} />
                  </div>
                );
              })();
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
});

// ─── Flat Mode (Cursor 风格无气泡模式) ───

const FlatMessageItem = memo(function FlatMessageItem({
  msg,
  onAskAnswer,
  apiBaseUrl,
  showChain = true,
  onSkipStep,
  onImagePreview,
  mdModules,
}: {
  msg: ChatMessage;
  onAskAnswer?: (msgId: string, answer: string) => void;
  apiBaseUrl?: string;
  showChain?: boolean;
  onSkipStep?: () => void;
  onImagePreview?: (url: string, name: string) => void;
  mdModules?: MdModules | null;
}) {
  const { t } = useTranslation();
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
          {msg.content && stripLegacySummary(msg.content) && (
            <div className="chatMdContent">
              {mdModules ? (
                <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]} rehypePlugins={[mdModules.rehypeHighlight]}>
                  {stripLegacySummary(msg.content)}
                </mdModules.ReactMarkdown>
              ) : (
                <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{stripLegacySummary(msg.content)}</pre>
              )}
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
                const rawUrl = art.file_url.startsWith("http")
                  ? art.file_url
                  : `${apiBaseUrl || ""}${art.file_url}`;
                const fullUrl = appendAuthToken(rawUrl);
                if (art.artifact_type === "image") {
                  return (
                    <div key={i} style={{ marginBottom: 8, position: "relative", display: "inline-block" }}>
                      <img
                        src={fullUrl}
                        alt={art.caption || art.name}
                        style={{ maxWidth: "100%", maxHeight: 400, borderRadius: 8, border: "1px solid var(--line)", display: "block", cursor: "pointer" }}
                        onClick={() => {
                          if (_artifactClickTimer) clearTimeout(_artifactClickTimer);
                          _artifactClickTimer = setTimeout(() => {
                            onImagePreview?.(fullUrl, art.name || "image");
                          }, 250);
                        }}
                        onDoubleClick={() => {
                          if (_artifactClickTimer) { clearTimeout(_artifactClickTimer); _artifactClickTimer = null; }
                          (async () => {
                            try {
                              const savedPath = await downloadFile(fullUrl, art.name || `image-${Date.now()}.png`);
                              await openFileWithDefault(savedPath);
                            } catch (err) {
                              logger.error("Chat", "图片打开失败", { error: String(err) });
                            }
                          })();
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
                            const savedPath = await downloadFile(fullUrl, art.name || `image-${Date.now()}.png`);
                            await showInFolder(savedPath);
                          } catch (err) {
                            logger.error("Chat", "图片下载失败", { error: String(err) });
                          }
                        }}
                      >
                        <IconDownload size={16} />
                      </button>
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
                return (() => {
                  const FileIcon = getFileTypeIcon(art.name || "");
                  const sizeStr = art.size != null
                    ? art.size > 1048576 ? `${(art.size / 1048576).toFixed(1)} MB` : `${(art.size / 1024).toFixed(1)} KB`
                    : "";
                  return (
                    <div key={i} style={{
                      display: "inline-flex", alignItems: "center", gap: 10,
                      padding: "10px 14px", borderRadius: 10, border: "1px solid var(--line)",
                      fontSize: 13, marginBottom: 4, cursor: "pointer",
                      background: "var(--panel)",
                      transition: "background 0.15s",
                    }}
                      onClick={() => {
                        if (_artifactClickTimer) clearTimeout(_artifactClickTimer);
                        _artifactClickTimer = setTimeout(async () => {
                          try {
                            const savedPath = await downloadFile(fullUrl, art.name || "file");
                            await showInFolder(savedPath);
                          } catch (err) {
                            logger.error("Chat", "文件下载失败", { error: String(err) });
                          }
                        }, 250);
                      }}
                      onDoubleClick={() => {
                        if (_artifactClickTimer) { clearTimeout(_artifactClickTimer); _artifactClickTimer = null; }
                        (async () => {
                          try {
                            const savedPath = await downloadFile(fullUrl, art.name || "file");
                            await openFileWithDefault(savedPath);
                          } catch (err) {
                            logger.error("Chat", "文件打开失败", { error: String(err) });
                          }
                        })();
                      }}
                      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(37,99,235,0.08)"; }}
                      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "var(--panel)"; }}
                    >
                      <FileIcon size={28} />
                      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                        <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{art.name}</span>
                        <span style={{ fontSize: 11, opacity: 0.5 }}>
                          {sizeStr}{sizeStr && art.caption ? " · " : ""}{art.caption || ""}
                        </span>
                      </div>
                      <IconDownload size={14} style={{ opacity: 0.4, flexShrink: 0 }} />
                    </div>
                  );
                })()
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
});

// ─── SVG icon helper ───
const _SVG_PATHS: Record<string, string> = {
  terminal:"M4 17l6-5-6-5M12 19h8",code:"M16 18l6-6-6-6M8 6l-6 6 6 6",
  globe:"M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10A15.3 15.3 0 0112 2z",
  shield:"M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",database:"M12 2C6.48 2 2 3.79 2 6v12c0 2.21 4.48 4 10 4s10-1.79 10-4V6c0-2.21-4.48-4-10-4zM2 12c0 2.21 4.48 4 10 4s10-1.79 10-4M2 6c0 2.21 4.48 4 10 4s10-1.79 10-4",
  cpu:"M6 6h12v12H6zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",cloud:"M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z",
  lock:"M19 11H5a2 2 0 00-2 2v7a2 2 0 002 2h14a2 2 0 002-2v-7a2 2 0 00-2-2zM7 11V7a5 5 0 0110 0v4",zap:"M13 2L3 14h9l-1 8 10-12h-9l1-8z",
  eye:"M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 100 6 3 3 0 000-6z",message:"M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z",
  mail:"M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6",chart:"M18 20V10M12 20V4M6 20v-6",
  network:"M5.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM18.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM12 24a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM5.5 5.5L12 19M18.5 5.5L12 19",
  target:"M12 2a10 10 0 100 20 10 10 0 000-20zM12 6a6 6 0 100 12 6 6 0 000-12zM12 10a2 2 0 100 4 2 2 0 000-4z",
  compass:"M12 2a10 10 0 100 20 10 10 0 000-20zM16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36z",
  layers:"M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  workflow:"M6 3a3 3 0 100 6 3 3 0 000-6zM18 15a3 3 0 100 6 3 3 0 000-6zM8.59 13.51l6.83 3.98M6 9v4M18 9v6",
  flask:"M9 3h6M10 3v6.5l-5 8.5h14l-5-8.5V3",pen:"M12 20h9M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4L16.5 3.5z",
  mic:"M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8",
  bot:"M12 2a2 2 0 012 2v1h3a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2h3V4a2 2 0 012-2zM9 13h0M15 13h0M9 17h6",
  puzzle:"M19.439 12.956l-1.5 0a2 2 0 010-4l1.5 0a.5.5 0 00.5-.5l0-2.5a2 2 0 00-2-2l-2.5 0a.5.5 0 01-.5-.5l0-1.5a2 2 0 00-4 0l0 1.5a.5.5 0 01-.5.5L7.939 3.956a2 2 0 00-2 2l0 2.5a.5.5 0 00.5.5l1.5 0a2 2 0 010 4l-1.5 0a.5.5 0 00-.5.5l0 2.5a2 2 0 002 2l2.5 0a.5.5 0 01.5.5l0 1.5a2 2 0 004 0l0-1.5a.5.5 0 01.5-.5l2.5 0a2 2 0 002-2l0-2.5a.5.5 0 00-.5-.5z",
  heart:"M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 000-7.78z",
};
function RenderIcon({ icon, size = 14 }: { icon: string; size?: number }) {
  if (icon.startsWith("svg:")) {
    const d = _SVG_PATHS[icon.slice(4)];
    if (!d) return <span>{icon}</span>;
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        <path d={d} />
      </svg>
    );
  }
  return <>{icon}</>;
}

// ─── Sub-Agent Progress Cards ───

type SubAgentTaskDisplay = {
  agent_id: string;
  profile_id: string;
  session_id: string;
  name: string;
  icon: string;
  status: "starting" | "running" | "completed" | "error" | "timeout" | "cancelled";
  iteration: number;
  tools_executed: string[];
  tools_total: number;
  elapsed_s: number;
  last_progress_s: number;
  started_at: number;
};

function SubAgentCards({ tasks }: { tasks: SubAgentTaskDisplay[] }) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 4;
  const totalPages = Math.ceil(tasks.length / PAGE_SIZE);
  const visible = tasks.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const statusLabel = (s: string) => {
    switch (s) {
      case "starting": return t("chat.subAgentStarting", "启动中");
      case "running": return t("chat.subAgentRunning", "执行中");
      case "completed": return t("chat.subAgentDone", "已完成");
      case "error": return t("chat.subAgentError", "出错");
      case "timeout": return t("chat.subAgentTimeout", "超时");
      case "cancelled": return t("chat.subAgentCancelled", "已取消");
      default: return s;
    }
  };

  const statusClass = (s: string) => {
    switch (s) {
      case "starting":
      case "running": return "sacBadgeRunning";
      case "completed": return "sacBadgeDone";
      case "error": return "sacBadgeError";
      case "timeout": return "sacBadgeTimeout";
      default: return "";
    }
  };

  const formatElapsed = (s: number) => {
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}m${sec > 0 ? sec + "s" : ""}`;
  };

  return (
    <div className="sacContainer">
      <div className="sacHeader">
        <span className="sacTitle">{t("chat.subAgentPanel", "子 Agent 进度")}</span>
        {totalPages > 1 && (
          <div className="sacPager">
            <button className="sacPageBtn" disabled={page <= 0} onClick={() => setPage(p => p - 1)}>‹</button>
            <span className="sacPageInfo">{page + 1}/{totalPages}</span>
            <button className="sacPageBtn" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>›</button>
          </div>
        )}
      </div>
      <div className="sacGrid" ref={scrollRef}>
        {visible.map((task) => (
          <div key={task.agent_id} className={`sacCard ${task.status === "running" || task.status === "starting" ? "sacCardActive" : ""}`}>
            <div className="sacCardTop">
              <span className="sacIcon"><RenderIcon icon={task.icon} size={16} /></span>
              <span className="sacName">{task.name}</span>
              <span className={`sacBadge ${statusClass(task.status)}`}>
                {(task.status === "running" || task.status === "starting") && <span className="sacPulse" />}
                {statusLabel(task.status)}
              </span>
            </div>
            <div className="sacCardMeta">
              <span>{t("chat.subAgentIter", "迭代")} {task.iteration}</span>
              <span className="sacDot">·</span>
              <span>{formatElapsed(task.elapsed_s)}</span>
              <span className="sacDot">·</span>
              <span>{t("chat.subAgentTools", "工具")} ×{task.tools_total}</span>
            </div>
            <div className="sacToolList">
              {task.tools_executed.length === 0 && (
                <div className="sacToolItem sacToolWaiting">…</div>
              )}
              {task.tools_executed.map((tool, idx) => {
                const isCurrent = idx === task.tools_executed.length - 1 && (task.status === "running" || task.status === "starting");
                return (
                  <div key={`${tool}-${idx}`} className={`sacToolItem ${isCurrent ? "sacToolCurrent" : ""}`}>
                    <span className="sacToolArrow">{isCurrent ? "▸" : "▹"}</span>
                    <span className="sacToolName">{tool}</span>
                    {isCurrent && <span className="sacToolBlink" />}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── SecurityConfirmModal: 安全确认弹窗 ───

function SecurityConfirmModal({
  data, apiBase, onClose, timerRef, setData,
}: {
  data: { tool: string; args: Record<string, unknown>; reason: string; riskLevel: string; needsSandbox: boolean; toolId?: string; countdown: number };
  apiBase: string;
  onClose: () => void;
  timerRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  setData: React.Dispatch<React.SetStateAction<typeof data | null>>;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    timerRef.current = setInterval(() => {
      setData((prev) => {
        if (!prev) return null;
        const next = prev.countdown - 1;
        if (next <= 0) {
          safeFetch(`${apiBase}/api/chat/security-confirm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm_id: prev.toolId || "", decision: "deny" }),
          }).catch(() => {});
          onClose();
          return null;
        }
        return { ...prev, countdown: next };
      });
    }, 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [apiBase, onClose, timerRef, setData]);

  const respond = (decision: "allow" | "deny" | "sandbox") => {
    safeFetch(`${apiBase}/api/chat/security-confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_id: data.toolId || "", decision }),
    }).catch(() => {});
    onClose();
  };

  const riskColors: Record<string, string> = {
    CRITICAL: "text-red-600 bg-red-100 dark:bg-red-900/30",
    HIGH: "text-orange-600 bg-orange-100 dark:bg-orange-900/30",
    MEDIUM: "text-yellow-600 bg-yellow-100 dark:bg-yellow-900/30",
    LOW: "text-green-600 bg-green-100 dark:bg-green-900/30",
  };
  const riskCls = riskColors[data.riskLevel] || "text-muted-foreground bg-muted";

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-[440px] rounded-xl bg-background shadow-2xl border p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="size-10 rounded-full bg-orange-100 dark:bg-orange-900/30 flex items-center justify-center">
            <IconAlertCircle size={20} className="text-orange-500" />
          </div>
          <div>
            <h3 className="text-lg font-semibold">{t("security.confirmTitle")}</h3>
            <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${riskCls}`}>{data.riskLevel}</span>
          </div>
          <span className="ml-auto tabular-nums text-sm text-muted-foreground">{data.countdown}s</span>
        </div>
        <div className="mb-4 space-y-2 text-sm">
          <div><span className="font-medium">{t("security.tool")}:</span> <code className="px-1.5 py-0.5 bg-muted rounded text-xs">{data.tool}</code></div>
          <div><span className="font-medium">{t("security.reason")}:</span> <span className="text-muted-foreground">{data.reason}</span></div>
          {data.args.command ? (
            <div className="mt-2 p-2.5 bg-muted rounded-md font-mono text-xs break-all max-h-24 overflow-y-auto">
              {String(data.args.command)}
            </div>
          ) : null}
        </div>
        <div className="flex gap-2">
          <Button onClick={() => respond("allow")} className="flex-1 bg-green-600 hover:bg-green-700 text-white">
            <IconCheck size={14} /> {t("security.allow")}
          </Button>
          <Button onClick={() => respond("deny")} variant="destructive" className="flex-1">
            <IconX size={14} /> {t("security.deny")}
          </Button>
          {data.needsSandbox && (
            <Button onClick={() => respond("sandbox")} className="flex-1 bg-blue-600 hover:bg-blue-700 text-white">
              <IconShield size={14} /> {t("security.sandboxExec")}
            </Button>
          )}
        </div>
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
  multiAgentEnabled = false,
}: {
  serviceRunning: boolean;
  endpoints: EndpointSummary[];
  onStartService: () => void;
  apiBaseUrl?: string;
  visible?: boolean;
  multiAgentEnabled?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const mdModules = useMdModules();

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
  const inputTextRef = useRef("");
  const [hasInputText, setHasInputText] = useState(false);
  const [selectedEndpoint, setSelectedEndpoint] = useState("auto");
  const [chatMode, setChatMode] = useState<"agent" | "plan" | "ask">("agent");
  const planMode = chatMode === "plan";
  const [streamingTick, setStreamingTick] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(() => typeof window !== "undefined" && window.innerWidth > 768);
  const [sidebarPinned, setSidebarPinned] = useState(() => {
    try { return localStorage.getItem("openakita_convSidebarPinned") === "true"; } catch { return false; }
  });
  const [convSearchQuery, setConvSearchQuery] = useState("");
  const [orbitTip, setOrbitTip] = useState<{ x: number; y: number; name: string; title: string } | null>(null);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashFilter, setSlashFilter] = useState("");
  const [slashSelectedIdx, setSlashSelectedIdx] = useState(0);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);
  const [lightbox, setLightbox] = useState<{ url: string; name: string } | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  const [securityConfirm, setSecurityConfirm] = useState<{
    tool: string; args: Record<string, unknown>; reason: string;
    riskLevel: string; needsSandbox: boolean; toolId?: string;
    countdown: number;
  } | null>(null);
  const securityTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [winSize, setWinSize] = useState({ w: window.innerWidth, h: window.innerHeight });
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setLightbox(null); };
    const onResize = () => setWinSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("resize", onResize); };
  }, [lightbox]);

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

  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const modeMenuRef = useRef<HTMLDivElement | null>(null);

  const [agentProfiles, setAgentProfiles] = useState<{id:string;name:string;description:string;icon:string;color:string;name_i18n?:Record<string,string>;description_i18n?:Record<string,string>;preferred_endpoint?:string|null}[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("default");
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const agentMenuRef = useRef<HTMLDivElement | null>(null);

  // ── Org mode state ──
  const [orgMode, setOrgMode] = useState(false);
  const [orgList, setOrgList] = useState<{id: string; name: string; icon: string; status: string}[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [selectedOrgNodeId, setSelectedOrgNodeId] = useState<string | null>(null);
  const [orgMenuOpen, setOrgMenuOpen] = useState(false);
  const orgMenuRef = useRef<HTMLDivElement | null>(null);
  const [orgCommandPending, setOrgCommandPending] = useState(false);
  const orgCommandPendingRef = useRef(false);

  useEffect(() => {
    if (!orgMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (orgMenuRef.current && !orgMenuRef.current.contains(e.target as HTMLElement)) {
        setOrgMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [orgMenuOpen]);

  useEffect(() => {
    const handler = (e: Event) => {
      const { orgId, nodeId } = (e as CustomEvent).detail ?? {};
      if (!orgId) return;
      setOrgMode(true);
      setSelectedOrgId(orgId);
      setSelectedOrgNodeId(nodeId ?? null);
    };
    window.addEventListener("openakita_activate_org", handler);
    return () => window.removeEventListener("openakita_activate_org", handler);
  }, []);

  type SubAgentEntry = { agentId: string; status: "delegating" | "done" | "error"; reason?: string; startTime: number };
  const [displayActiveSubAgents, setDisplayActiveSubAgents] = useState<SubAgentEntry[]>([]);

  // Sub-agent progress cards (populated via polling /api/agents/sub-tasks)
  type SubAgentTask = {
    agent_id: string;
    profile_id: string;
    session_id: string;
    name: string;
    icon: string;
    status: "starting" | "running" | "completed" | "error" | "timeout" | "cancelled";
    iteration: number;
    tools_executed: string[];
    tools_total: number;
    elapsed_s: number;
    last_progress_s: number;
    started_at: number;
  };
  const [displaySubAgentTasks, setDisplaySubAgentTasks] = useState<SubAgentTask[]>([]);

  // ── Per-session streaming context (supports concurrent streams) ──
  type StreamContext = {
    abort: AbortController;
    reader: ReadableStreamDefaultReader<Uint8Array> | null;
    isStreaming: boolean;
    messages: ChatMessage[];
    activeSubAgents: SubAgentEntry[];
    subAgentTasks: SubAgentTask[];
    isDelegating: boolean;
    pollingTimer: ReturnType<typeof setInterval> | null;
  };
  const streamContexts = useRef<Map<string, StreamContext>>(new Map());
  const activeConvIdRef = useRef(activeConvId);
  const isCurrentConvStreaming = streamContexts.current.get(activeConvId ?? "")?.isStreaming ?? false;

  // ── Multi-device busy lock ──
  const clientIdRef = useRef(() => {
    let id = sessionStorage.getItem("openakita_client_id");
    if (!id) {
      id = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : genId();
      sessionStorage.setItem("openakita_client_id", id);
    }
    return id;
  });
  const getClientId = useCallback(() => clientIdRef.current(), []);
  const [busyConversations, setBusyConversations] = useState<Map<string, string>>(new Map());
  const busyConvRef = useRef(busyConversations);
  busyConvRef.current = busyConversations;

  const isConvBusyOnOtherDevice = useCallback((convId: string) => {
    const busyClientId = busyConvRef.current.get(convId);
    return !!busyClientId && busyClientId !== getClientId();
  }, [getClientId]);

  const updateConvStatus = useCallback((convId: string, status: ConversationStatus) => {
    setConversations((prev) =>
      prev.map((c) => c.id === convId ? { ...c, status, timestamp: Date.now() } : c)
    );
  }, []);

  // 会话右键菜单 & 重命名
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; convId: string } | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  useEffect(() => {
    if (!ctxMenu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setCtxMenu(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ctxMenu]);

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
    activeConvIdRef.current = activeConvId;
    try {
      if (activeConvId) localStorage.setItem(STORAGE_KEY_ACTIVE, activeConvId);
      else localStorage.removeItem(STORAGE_KEY_ACTIVE);
    } catch {}
  }, [activeConvId]);

  // Force re-render every 30s to refresh relative timestamps
  const [, setTimeTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTimeTick((t) => t + 1), 30_000);
    return () => clearInterval(iv);
  }, []);

  // ── 持久化消息（流式中由 StreamContext 管理，finally 一次性写入） ──
  const saveMessagesTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestMessagesRef = useRef<ChatMessage[]>(messages);
  const latestActiveConvIdRef = useRef<string | null>(activeConvId);
  useEffect(() => { latestMessagesRef.current = messages; }, [messages]);
  useEffect(() => { latestActiveConvIdRef.current = activeConvId; }, [activeConvId]);

  const flushCurrentConversationToStorage = useCallback(() => {
    const convId = latestActiveConvIdRef.current;
    if (!convId) return;
    saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, latestMessagesRef.current);
  }, [STORAGE_KEY_MSGS_PREFIX]);

  useEffect(() => {
    if (!activeConvId) return;
    if (streamContexts.current.get(activeConvId)?.isStreaming) return;
    if (saveMessagesTimerRef.current) clearTimeout(saveMessagesTimerRef.current);
    saveMessagesTimerRef.current = setTimeout(() => {
      if (!saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + activeConvId, messages)) {
        try {
          const convs: ChatConversation[] = JSON.parse(localStorage.getItem(STORAGE_KEY_CONVS) || "[]");
          const toEvict = [...convs].reverse().find(c => c.id !== activeConvId);
          if (toEvict) {
            localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + toEvict.id);
            saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + activeConvId, messages);
          }
        } catch { /* give up */ }
      }
    }, 300);
    return () => { if (saveMessagesTimerRef.current) clearTimeout(saveMessagesTimerRef.current); };
  }, [messages, activeConvId, streamingTick]);

  // (messagesSnapshotRef / liveMessagesCache removed — StreamContext manages live messages)

  // 页面隐藏/关闭时立即落盘，降低"当天消息未及时写入 localStorage"的概率
  useEffect(() => {
    const flushNow = () => {
      if (saveMessagesTimerRef.current) {
        clearTimeout(saveMessagesTimerRef.current);
        saveMessagesTimerRef.current = null;
      }
      flushCurrentConversationToStorage();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flushNow();
    };
    window.addEventListener("beforeunload", flushNow);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("beforeunload", flushNow);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [flushCurrentConversationToStorage]);

  // ── APP 后台恢复：中断已断开的 SSE 流 ──
  // Tauri WebView (macOS WKWebView / Windows WebView2) kills HTTP streams
  // when the window is in the background, so we need to proactively abort and
  // let the error handler clean up.  Regular browsers keep fetch streams alive
  // across tab switches / minimize, so aborting would unnecessarily terminate
  // a perfectly healthy response — skip for IS_WEB.
  useEffect(() => {
    if (IS_WEB) return;
    const handler = () => {
      for (const [convId, ctx] of streamContexts.current) {
        if (!ctx.isStreaming) continue;
        ctx.abort.abort();
        logger.warn("Chat", "SSE stream aborted after app resume", { convId });
      }
    };
    window.addEventListener("openakita_app_resumed", handler);
    return () => window.removeEventListener("openakita_app_resumed", handler);
  }, []);

  // ── 切换对话时加载对应消息 ──
  const skipConvLoadRef = useRef(false);
  const hydrateSeqRef = useRef(0);

  const mapBackendHistoryToMessages = useCallback(
    (rows: { id: string; role: string; content: string; timestamp: number; chain_summary?: ChainSummaryItem[]; artifacts?: ChatArtifact[]; ask_user?: { question: string; options?: { id: string; label: string }[]; questions?: ChatAskQuestion[] } }[]): ChatMessage[] => {
      return rows.map((m) => ({
        id: m.id,
        role: m.role as "user" | "assistant" | "system",
        content: m.content,
        timestamp: m.timestamp,
        ...(m.chain_summary?.length ? { thinkingChain: buildChainFromSummary(m.chain_summary) } : {}),
        ...(m.artifacts?.length ? { artifacts: m.artifacts } : {}),
        ...(m.ask_user ? { askUser: m.ask_user, content: "" } : {}),
      }));
    },
    [],
  );

  const hydrateConversationMessages = useCallback(async (convId: string, expectedCount = 0) => {
    const seq = ++hydrateSeqRef.current;
    let localMsgs: ChatMessage[] = [];
    try {
      const raw = localStorage.getItem(STORAGE_KEY_MSGS_PREFIX + convId);
      localMsgs = raw ? JSON.parse(raw) : [];
    } catch {
      localMsgs = [];
    }

    const localCount = Array.isArray(localMsgs) ? localMsgs.length : 0;
    const shouldSyncBackend = serviceRunning && (localCount === 0 || (expectedCount > 0 && localCount < expectedCount));

    if (!shouldSyncBackend) {
      if (seq === hydrateSeqRef.current) setMessages(localMsgs);
      return;
    }

    try {
      const res = await safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`);
      const data = await res.json();
      const backendMsgs = Array.isArray(data?.messages) ? mapBackendHistoryToMessages(data.messages) : [];

      const chosen = backendMsgs.length >= localCount ? backendMsgs : localMsgs;
      if (seq === hydrateSeqRef.current) setMessages(chosen);

      if (backendMsgs.length >= localCount) {
        saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, backendMsgs);
      }
    } catch {
      if (seq === hydrateSeqRef.current) setMessages(localMsgs);
    }
  }, [serviceRunning, apiBaseUrl, mapBackendHistoryToMessages, STORAGE_KEY_MSGS_PREFIX]);

  useEffect(() => {
    if (!activeConvId) {
      setMessages([]);
      return;
    }
    if (skipConvLoadRef.current) {
      skipConvLoadRef.current = false;
      return;
    }

    // If a StreamContext is actively streaming for this conv, restore its state directly
    const ctx = streamContexts.current.get(activeConvId);
    if (ctx?.isStreaming) {
      setMessages(ctx.messages);
      setDisplayActiveSubAgents(ctx.activeSubAgents);
      setDisplaySubAgentTasks(ctx.subAgentTasks);
    } else {
      const activeMeta = conversations.find((c) => c.id === activeConvId);
      const expectedCount = activeMeta?.messageCount || 0;
      void hydrateConversationMessages(activeConvId, expectedCount);
      setDisplayActiveSubAgents([]);
      setDisplaySubAgentTasks([]);
    }

    isInitialScrollRef.current = true;
    const conv = conversations.find((c) => c.id === activeConvId);
    if (multiAgentEnabled) {
      const agentId = conv?.agentProfileId || "default";
      setSelectedAgent(agentId);
    }
    setSelectedEndpoint(conv?.endpointId || "auto");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- conversations 故意排除：
    // 此 effect 语义是"切换对话时加载消息"，不应因 messageCount/title 等元数据变更而重新 hydrate，
    // 否则流结束后 setConversations 更新 messageCount 会触发竞态覆盖。
  }, [activeConvId, hydrateConversationMessages, multiAgentEnabled]);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const isNearBottomRef = useRef(true);
  const lastScrollTimeRef = useRef(0); // throttle scroll during streaming to reduce flicker when thinking is long
  const isInitialScrollRef = useRef(true); // first scroll should be instant, not smooth
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // abortRef/readerRef removed — now per-session in StreamContext
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Track scroll position to avoid auto-scrolling when user is reading earlier messages
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const onScroll = () => {
      isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const setInputValue = useCallback((val: string) => {
    inputTextRef.current = val;
    setHasInputText(val.trim().length > 0);
    if (inputRef.current) {
      inputRef.current.value = val;
      inputRef.current.style.height = "auto";
      if (val) {
        inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 120) + "px";
      }
    }
  }, []);

  // Fetch initial context size on mount / when service starts
  useEffect(() => {
    if (!serviceRunning) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/stats/tokens/context`);
        const data = await res.json();
        if (cancelled) return;
        if (typeof data.context_tokens === "number") setContextTokens(data.context_tokens);
        if (typeof data.context_limit === "number") setContextLimit(data.context_limit);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [serviceRunning, apiBaseUrl]);

  useEffect(() => {
    if (!multiAgentEnabled) {
      setAgentProfiles([]);
      return;
    }
    if (!visible) return;
    const fetchProfiles = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/agents/profiles`);
        const data = await res.json();
        setAgentProfiles(data.profiles || []);
      } catch (e) {
        logger.warn("Chat", "Failed to fetch agent profiles", { error: String(e) });
      }
    };
    fetchProfiles();
  }, [multiAgentEnabled, apiBaseUrl, serviceRunning, visible]);

  useEffect(() => {
    if (!multiAgentEnabled || !visible || !serviceRunning) return;
    const fetchOrgs = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/orgs`);
        const data = await res.json();
        setOrgList(data.map((o: any) => ({ id: o.id, name: o.name, icon: o.icon || "", status: o.status })));
      } catch { /* ignore */ }
    };
    fetchOrgs();
  }, [multiAgentEnabled, apiBaseUrl, serviceRunning, visible]);

  // Sync selectedAgent → current conversation's agentProfileId
  // Only react to selectedAgent changes (not activeConvId) to avoid overwriting
  // a newly-switched conversation with the previous conversation's agent.
  const prevSelectedAgentRef = useRef(selectedAgent);
  useEffect(() => {
    if (!multiAgentEnabled) return;
    if (selectedAgent === prevSelectedAgentRef.current) return;
    prevSelectedAgentRef.current = selectedAgent;
    const convId = activeConvIdRef.current;
    if (!convId) return;
    setConversations((prev) => {
      const current = prev.find((c) => c.id === convId);
      if (current?.agentProfileId === selectedAgent) return prev;
      return prev.map((c) => c.id === convId ? { ...c, agentProfileId: selectedAgent } : c);
    });
  }, [selectedAgent, multiAgentEnabled]);

  // Sync selectedEndpoint → current conversation's endpointId
  const prevSelectedEndpointRef = useRef(selectedEndpoint);
  useEffect(() => {
    if (selectedEndpoint === prevSelectedEndpointRef.current) return;
    prevSelectedEndpointRef.current = selectedEndpoint;
    const convId = activeConvIdRef.current;
    if (!convId) return;
    const epVal = selectedEndpoint === "auto" ? undefined : selectedEndpoint;
    setConversations((prev) => {
      const current = prev.find((c) => c.id === convId);
      if ((current?.endpointId || undefined) === epVal) return prev;
      return prev.map((c) => c.id === convId ? { ...c, endpointId: epVal } : c);
    });
  }, [selectedEndpoint]);

  // Validate selectedEndpoint against current endpoints list
  useEffect(() => {
    if (selectedEndpoint === "auto") return;
    if (endpoints.length === 0) return;
    if (!endpoints.some((ep) => ep.name === selectedEndpoint)) {
      setSelectedEndpoint("auto");
    }
  }, [endpoints, selectedEndpoint]);

  useEffect(() => {
    if (!agentMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (agentMenuRef.current && !agentMenuRef.current.contains(e.target as Node)) {
        setAgentMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [agentMenuOpen]);

  // Restore conversations from backend when localStorage is empty (e.g. after Tauri restart)

  // 启动后后台对账会话列表：本地先展示，后端异步增量合并，避免"今天新会话缺失"
  const sessionRestoreAttempted = useRef(false);
  useEffect(() => {
    if (!serviceRunning || sessionRestoreAttempted.current) return;
    sessionRestoreAttempted.current = true;

    let cancelled = false;
    (async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/sessions?channel=desktop`);
        if (cancelled) return;
        const data = await res.json();
        const backendSessions: { id: string; title: string; lastMessage: string; timestamp: number; messageCount: number; agentProfileId?: string }[] = data.sessions || [];
        if (cancelled) return;

        // ── Factory reset / data wipe detection ──
        // Two complementary checks:
        // 1) data_epoch mismatch: backend's data/ was recreated (epoch changed)
        // 2) ready + 0 sessions: backend is fully initialized but has nothing —
        //    local conversations are orphaned (handles bootstrap: first time the
        //    epoch code runs, cached is null so #1 cannot fire).
        const backendReady = data.ready !== false;
        const epoch = data.data_epoch as string | undefined;
        const EPOCH_KEY = "openakita_data_epoch";

        if (epoch) {
          const cached = localStorage.getItem(EPOCH_KEY);
          localStorage.setItem(EPOCH_KEY, epoch);
          if (cached && cached !== epoch) {
            setConversations((prev) => {
              for (const c of prev) {
                try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + c.id); } catch {}
              }
              return [];
            });
            setActiveConvId(null);
            setMessages([]);
            return;
          }
        }

        if (backendReady && backendSessions.length === 0) {
          setConversations((prev) => {
            if (prev.length === 0) return prev;
            for (const c of prev) {
              try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + c.id); } catch {}
            }
            return [];
          });
          setActiveConvId(null);
          setMessages([]);
          return;
        }
        if (backendSessions.length === 0) return;

        const restoredConvs: ChatConversation[] = backendSessions.map((s) => ({
          id: s.id,
          title: s.title || "对话",
          lastMessage: s.lastMessage || "",
          timestamp: s.timestamp,
          messageCount: s.messageCount || 0,
          agentProfileId: s.agentProfileId,
        }));

        setConversations((prev) => {
          const prevMap = new Map(prev.map((c) => [c.id, c]));
          const mergedFromBackend: ChatConversation[] = restoredConvs.map((b) => {
            const local = prevMap.get(b.id);
            if (!local) return b;
            return {
              ...local,
              title: local.titleGenerated ? local.title : (b.title || local.title || "对话"),
              lastMessage: b.lastMessage || local.lastMessage,
              timestamp: Math.max(local.timestamp || 0, b.timestamp || 0),
              messageCount: Math.max(local.messageCount || 0, b.messageCount || 0),
              agentProfileId: local.agentProfileId || b.agentProfileId,
            };
          });
          const backendIds = new Set(restoredConvs.map((c) => c.id));
          const localOnly = prev.filter((c) => !backendIds.has(c.id));
          return [...mergedFromBackend, ...localOnly];
        });

        // 没有活跃会话时，默认打开后端最新会话
        if (!activeConvId) {
          setActiveConvId(restoredConvs[0].id);
        }
      } catch { /* backend not available yet, ignore */ }
    })();
    return () => { cancelled = true; };
  }, [serviceRunning, apiBaseUrl, activeConvId]);

  // ── Multi-device busy state: poll + WS events ──
  useEffect(() => {
    if (!serviceRunning) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/chat/busy`);
        if (cancelled) return;
        const data = await res.json();
        const items: { conversation_id: string; client_id: string }[] = data.busy_conversations || [];
        const myId = getClientId();
        const m = new Map<string, string>();
        for (const it of items) {
          if (it.client_id !== myId) m.set(it.conversation_id, it.client_id);
        }
        setBusyConversations(m);
      } catch { /* ignore */ }
    };
    poll();
    const timer = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [serviceRunning, apiBaseUrl, getClientId]);

  useEffect(() => {
    if (!IS_WEB) return;
    const myId = getClientId();
    return onWsEvent((event, data) => {
      const d = data as Record<string, unknown> | null;
      if (!d) return;
      const convId = d.conversation_id as string | undefined;
      if (!convId) return;
      if (event === "chat:busy") {
        const clientId = d.client_id as string;
        if (clientId !== myId) {
          setBusyConversations((prev) => { const m = new Map(prev); m.set(convId, clientId); return m; });
        }
      } else if (event === "chat:idle") {
        setBusyConversations((prev) => { const m = new Map(prev); m.delete(convId); return m; });
      } else if (event === "chat:message_update") {
        if (convId === activeConvIdRef.current) {
          safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`)
            .then((r) => r.json())
            .then((d2) => { if (d2?.messages?.length) setMessages((prev) => patchMessagesWithBackend(prev, d2.messages)); })
            .catch(() => {});
        }
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBaseUrl, getClientId]);

  // ── 消息补全：用后端数据修复 localStorage 中不完整的消息（中断的流式传输等）──
  const patchedConvsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!serviceRunning || !activeConvId || isCurrentConvStreaming) return;
    if (patchedConvsRef.current.has(activeConvId)) return;

    patchedConvsRef.current.add(activeConvId);
    const convId = activeConvId;

    safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`)
      .then((r) => r.json())
      .then((data) => {
        if (!data?.messages?.length) return;
        setMessages((prev) => patchMessagesWithBackend(prev, data.messages));
      })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceRunning, activeConvId, streamingTick, apiBaseUrl, messages.length]);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // ── API base URL ──
  const apiBase = apiBaseUrl;

  // ── 文件上传辅助函数：上传文件到 /api/upload 并返回访问 URL ──
  const uploadFile = useCallback(async (file: Blob, filename: string): Promise<string> => {
    const form = new FormData();
    form.append("file", file, filename);
    const res = await safeFetch(`${apiBase}/api/upload`, { method: "POST", body: form });
    const data = await res.json();
    return data.url as string;  // 后端返回 { url: "/api/uploads/<filename>" }
  }, [apiBase]);

  // ── 组件卸载清理：abort 所有流式请求 + 停止麦克风 ──
  useEffect(() => {
    return () => {
      for (const [, ctx] of streamContexts.current) {
        try { ctx.abort.abort(); } catch {}
        try { ctx.reader?.cancel().catch(() => {}); } catch {}
        if (ctx.pollingTimer) clearInterval(ctx.pollingTimer);
      }
      streamContexts.current.clear();
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
      needsScrollOnVisible.current = true;
      return;
    }
    if (isInitialScrollRef.current) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
      });
      isInitialScrollRef.current = false;
    } else if (isNearBottomRef.current) {
      // During streaming: throttle scroll to ~8Hz to reduce flicker when thinking content is long.
      // Non-streaming: scroll every time with smooth behavior.
      const now = Date.now();
      const scrollThrottleMs = 120;
      const shouldScroll = !isCurrentConvStreaming || (now - lastScrollTimeRef.current >= scrollThrottleMs);
      if (shouldScroll) {
        lastScrollTimeRef.current = now;
        messagesEndRef.current.scrollIntoView({
          behavior: isCurrentConvStreaming ? "auto" : "smooth",
        });
      }
    }
    needsScrollOnVisible.current = false;
  }, [messages, visible, isCurrentConvStreaming]);

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
    if (!isCurrentConvStreaming && messages.some(m => m.thinkingChain?.length)) {
      const timer = setTimeout(() => {
        setMessages(prev => prev.map(m => ({
          ...m,
          thinkingChain: m.thinkingChain?.map(g => ({ ...g, collapsed: true })) ?? null,
        })));
      }, 1500);
      return () => clearTimeout(timer);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCurrentConvStreaming, streamingTick]);

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

  // ── 点击外部关闭模式菜单 ──
  useEffect(() => {
    if (!modeMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (modeMenuRef.current && !modeMenuRef.current.contains(e.target as Node)) {
        setModeMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modeMenuOpen]);

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
        setInputValue(`请使用技能「${args}」来帮我：`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /skill <技能名>，如 /skill web-search。在消息中提及技能名即可触发。", timestamp: Date.now() }]);
      }
    }},
    { id: "persona", label: "切换角色", description: "切换 Agent 的人格预设", action: (args) => {
      if (args) {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `角色切换请在「设置 → 灵魂与意志」中修改 PERSONA_NAME 为 "${args}"`, timestamp: Date.now() }]);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "可用角色: default, business, tech_expert, butler, girlfriend, boyfriend, family, jarvis\n用法: /persona <角色ID>", timestamp: Date.now() }]);
      }
    }},
    { id: "agent", label: "切换 Agent", description: "在多 Agent 间切换（handoff 模式）", action: (args) => {
      if (args) {
        setInputValue(`请切换到 Agent「${args}」来处理接下来的任务。`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /agent <Agent名称>。在 handoff 模式下，AI 会自动在 Agent 间切换。", timestamp: Date.now() }]);
      }
    }},
    { id: "agents", label: "查看 Agent 列表", description: "显示可用的 Agent 列表", action: () => {
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: "Agent 列表取决于 handoff 配置。当前可通过 /agent <名称> 手动请求切换。", timestamp: Date.now() }]);
    }},
    { id: "org", label: "组织模式", description: "切换到组织编排模式，向组织下命令", action: (args) => {
      if (args === "off" || args === "关闭") {
        setOrgMode(false);
        setSelectedOrgId(null);
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "已退出组织模式", timestamp: Date.now() }]);
      } else if (args) {
        const match = orgList.find(o => o.name.includes(args) || o.id === args);
        if (match) {
          setOrgMode(true);
          setSelectedOrgId(match.id);
          setMessages((prev) => [...prev, { id: genId(), role: "system", content: `已切换到组织: ${match.icon} ${match.name}`, timestamp: Date.now() }]);
        } else {
          setMessages((prev) => [...prev, { id: genId(), role: "system", content: `未找到组织「${args}」。可用组织: ${orgList.map(o => o.name).join(", ") || "无"}`, timestamp: Date.now() }]);
        }
      } else {
        const names = orgList.map(o => `${o.icon} ${o.name}`).join("\n") || "（暂无组织）";
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `组织模式 ${orgMode ? "已开启" : "已关闭"}\n可用组织:\n${names}\n\n用法: /org <组织名> 或 /org off`, timestamp: Date.now() }]);
      }
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
    if (activeConvId) {
      const ctx = streamContexts.current.get(activeConvId);
      const msgsToSave = ctx?.isStreaming ? ctx.messages : messages;
      if (msgsToSave.length > 0) {
        saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + activeConvId, msgsToSave);
      }
    }
    setActiveConvId(id);
    setMessages([]);
    setPendingAttachments([]);
    setDisplayActiveSubAgents([]);
    setDisplaySubAgentTasks([]);
    setSelectedEndpoint("auto");
    setConversations((prev) => [{
      id,
      title: "新对话",
      lastMessage: "",
      timestamp: Date.now(),
      messageCount: 0,
      agentProfileId: multiAgentEnabled ? selectedAgent : undefined,
    }, ...prev]);
  }, [activeConvId, messages, multiAgentEnabled, selectedAgent]);

  // ── 删除对话（实际执行） ──
  const doDeleteConversation = useCallback((convId: string) => {
    try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + convId); } catch {}
    setMessageQueue(prev => prev.filter(m => m.convId !== convId));
    const ctx = streamContexts.current.get(convId);
    if (ctx) {
      try { ctx.abort.abort(); } catch {}
      try { ctx.reader?.cancel().catch(() => {}); } catch {}
      if (ctx.pollingTimer) clearInterval(ctx.pollingTimer);
      streamContexts.current.delete(convId);
      setStreamingTick(t => t + 1);
    }

    if (serviceRunning) {
      safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}`, {
        method: "DELETE",
      }).catch(() => {});
    }

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
  }, [activeConvId, serviceRunning, apiBaseUrl]);

  // ── 删除对话（弹窗确认） ──
  const deleteConversation = useCallback((convId: string, e?: React.MouseEvent) => {
    if (e) { e.stopPropagation(); e.preventDefault(); }
    const conv = conversations.find((c) => c.id === convId);
    const title = conv?.title || t("chat.defaultTitle");
    setConfirmDialog({
      message: t("chat.confirmDeleteConversation", { title }),
      onConfirm: () => doDeleteConversation(convId),
    });
  }, [conversations, t, doDeleteConversation]);

  // ── 置顶/取消置顶 ──
  const togglePinConversation = useCallback((convId: string) => {
    setConversations((prev) => prev.map((c) =>
      c.id === convId ? { ...c, pinned: !c.pinned } : c
    ));
    setCtxMenu(null);
  }, []);

  // ── 重命名确认 ──
  const confirmRename = useCallback((convId: string, newTitle: string) => {
    const title = newTitle.trim();
    if (title) {
      setConversations((prev) => prev.map((c) =>
        c.id === convId ? { ...c, title, titleGenerated: true } : c
      ));
    }
    setRenamingId(null);
    setRenameText("");
  }, []);

  // ── 发送消息（overrideText 用于 ask_user 回复等场景，绕过 inputText；targetConvId 用于自动出队等需要指定目标会话的场景） ──
  // displayContent: 当发送给 API 的原文（如 JSON）不适合直接展示时，可指定用户气泡中的显示文本
  const sendMessage = useCallback(async (overrideText?: string, targetConvId?: string, displayContent?: string, modeOverride?: "agent" | "plan" | "ask") => {
    const text = (overrideText ?? inputTextRef.current).trim();
    if (!text && pendingAttachments.length === 0) return;
    if (orgCommandPendingRef.current) return;

    const resolvedConvId = targetConvId || activeConvId;
    const targetIsStreaming = resolvedConvId ? !!streamContexts.current.get(resolvedConvId)?.isStreaming : false;
    if (targetIsStreaming) return;

    if (resolvedConvId && isConvBusyOnOtherDevice(resolvedConvId)) return;

    // 斜杠命令处理
    if (text.startsWith("/")) {
      const parts = text.slice(1).split(/\s+/);
      const cmdId = parts[0].toLowerCase();
      const cmd = slashCommands.find((c) => c.id === cmdId);
      if (cmd) {
        cmd.action(parts.slice(1).join(" "));
        setInputValue("");
        setSlashOpen(false);
        return;
      }
    }

    // @org: 前缀或组织模式 — 路由到组织 API
    const orgPrefixMatch = text.match(/^@org:(\S+?)(?:\/(\S+?))?\s+([\s\S]+)/);
    if (orgPrefixMatch || (orgMode && selectedOrgId)) {
      let targetOrgId = selectedOrgId;
      let targetNodeId = selectedOrgNodeId;
      let msgContent = text;
      if (orgPrefixMatch) {
        const orgRef = orgPrefixMatch[1];
        targetNodeId = orgPrefixMatch[2] || null;
        msgContent = orgPrefixMatch[3];
        const match = orgList.find(o => o.name.includes(orgRef) || o.id === orgRef);
        if (match) targetOrgId = match.id;
      }
      if (targetOrgId) {
        const orgUserMsg: ChatMessage = { id: genId(), role: "user", content: text, timestamp: Date.now() };
        const placeholderId = genId();
        const orgOrgName = orgList.find(o => o.id === targetOrgId)?.name || targetOrgId;
        const orgConvId = activeConvId;
        const orgMsgsSnapshot: ChatMessage[] = [...messages, orgUserMsg, {
          id: placeholderId, role: "assistant" as const,
          content: "", streaming: true, timestamp: Date.now(),
        }];
        let orgMsgsLive = orgMsgsSnapshot;

        const updateOrgMessages = (updater: (msgs: ChatMessage[]) => ChatMessage[]) => {
          orgMsgsLive = updater(orgMsgsLive);
          if (activeConvIdRef.current === orgConvId) {
            setMessages(orgMsgsLive);
          }
        };

        setMessages(orgMsgsSnapshot);
        setInputValue("");
        orgCommandPendingRef.current = true;
        setOrgCommandPending(true);

        const progressLines: string[] = [];
        const pushProgress = (line: string) => {
          progressLines.push(line);
          const preview = progressLines.slice(-8).map(l => `> ${l}`).join("\n");
          updateOrgMessages((prev) => prev.map(m =>
            m.id === placeholderId ? { ...m, content: preview } : m
          ));
        };

        const unsub = onWsEvent((event, raw) => {
          const d = raw as Record<string, unknown> | null;
          if (!d || d.org_id !== targetOrgId) return;
          const nodeId = (d.node_id || d.from_node || "") as string;
          const toNode = (d.to_node || "") as string;
          if (event === "org:node_status") {
            const st = d.status as string;
            if (st === "busy") {
              const task = (d.current_task || "") as string;
              pushProgress(`🟢 **${nodeId}** 开始处理${task ? `：${task.slice(0, 60)}` : ""}`);
            } else if (st === "idle") {
              pushProgress(`✅ **${nodeId}** 完成`);
            } else if (st === "error") {
              pushProgress(`❌ **${nodeId}** 出错`);
            }
          } else if (event === "org:task_delegated") {
            const task = (d.task || "") as string;
            pushProgress(`📋 **${nodeId}** → **${toNode}** 分配任务：${(task as string).slice(0, 50)}`);
          } else if (event === "org:message") {
            const msgType = d.msg_type as string || "消息";
            pushProgress(`💬 **${nodeId}** → **${toNode}** ${msgType}`);
          } else if (event === "org:escalation") {
            pushProgress(`⬆️ **${nodeId}** 向上汇报`);
          } else if (event === "org:blackboard_update") {
            pushProgress(`📝 **${nodeId}** 更新黑板`);
          } else if (event === "org:task_complete") {
            pushProgress(`🎯 **${nodeId}** 任务完成`);
          } else if (event === "org:task_timeout") {
            pushProgress(`⏰ **${nodeId}** 任务超时`);
          }
        });

        try {
          const submitRes = await safeFetch(`${apiBaseUrl}/api/orgs/${targetOrgId}/command`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: msgContent, target_node_id: targetNodeId }),
          });
          const submitData = await submitRes.json();
          const commandId = submitData.command_id as string | undefined;

          if (!commandId) {
            const resultText = submitData.result || submitData.error || JSON.stringify(submitData);
            const progressSummary = progressLines.length > 0
              ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
              : "";
            updateOrgMessages((prev) => prev.map(m =>
              m.id === placeholderId
                ? { ...m, content: `${progressSummary}**[${orgOrgName}]** ${resultText}`, streaming: false }
                : m
            ));
          } else {
            let resolved = false;
            const onDone = onWsEvent((evt, raw) => {
              const d = raw as Record<string, unknown> | null;
              if (evt !== "org:command_done" || !d || d.command_id !== commandId) return;
              resolved = true;
              const result = d.result as Record<string, unknown> | null;
              const error = d.error as string | undefined;
              const resultText = (result && (result.result || result.error)) || error || JSON.stringify(d);
              const progressSummary = progressLines.length > 0
                ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
                : "";
              updateOrgMessages((prev) => prev.map(m =>
                m.id === placeholderId
                  ? { ...m, content: `${progressSummary}**[${orgOrgName}]** ${resultText}`, streaming: false }
                  : m
              ));
            });

            const pollInterval = 5_000;
            const stallThreshold = 60_000;
            let lastProgressAt = Date.now();
            const origPushProgress = pushProgress;
            const wrappedPush = (line: string) => { lastProgressAt = Date.now(); origPushProgress(line); };
            // Replace the outer pushProgress's timestamp tracking
            void wrappedPush;

            while (!resolved) {
              await new Promise(r => setTimeout(r, pollInterval));
              if (resolved) break;
              try {
                const pollRes = await safeFetch(
                  `${apiBaseUrl}/api/orgs/${targetOrgId}/commands/${commandId}`
                );
                const pollData = await pollRes.json();
                if (pollData.status === "done" || pollData.status === "error") {
                  if (!resolved) {
                    resolved = true;
                    const resultText = pollData.result?.result || pollData.result?.error || pollData.error || JSON.stringify(pollData);
                    const progressSummary = progressLines.length > 0
                      ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
                      : "";
                    updateOrgMessages((prev) => prev.map(m =>
                      m.id === placeholderId
                        ? { ...m, content: `${progressSummary}**[${orgOrgName}]** ${resultText}`, streaming: false }
                        : m
                    ));
                  }
                }
              } catch { /* poll failed, retry next cycle */ }

              if (!resolved && Date.now() - lastProgressAt > stallThreshold) {
                pushProgress("⏳ 执行时间较长，组织仍在处理中...");
                lastProgressAt = Date.now();
              }
            }

            onDone();
          }
        } catch (e: any) {
          updateOrgMessages((prev) => prev.map(m =>
            m.id === placeholderId
              ? { ...m, content: `组织命令失败: ${e.message || e}`, streaming: false, role: "system" as const }
              : m
          ));
        } finally {
          unsub();
          orgCommandPendingRef.current = false;
          setOrgCommandPending(false);
          if (orgConvId) {
            saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + orgConvId, orgMsgsLive);
          }
        }
        return;
      }
    }

    // 创建用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: "user",
      content: displayContent || text,
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

    let convId = resolvedConvId;

    setInputValue("");
    setPendingAttachments([]);
    setSlashOpen(false);
    if (!convId) {
      convId = genId();
      skipConvLoadRef.current = true;
      setActiveConvId(convId);
      setConversations((prev) => [{
        id: convId!,
        title: text.slice(0, 30) || "新对话",
        lastMessage: text,
        timestamp: Date.now(),
        messageCount: 1,
        status: "running",
        agentProfileId: multiAgentEnabled ? selectedAgent : undefined,
        endpointId: selectedEndpoint !== "auto" ? selectedEndpoint : undefined,
      }, ...prev]);
    } else {
      updateConvStatus(convId, "running");
    }

    const thisConvId = convId!;

    // SSE 流式请求
    const abort = new AbortController();

    // Build per-session StreamContext with initial messages
    const fallbackMessages = thisConvId === activeConvId ? [...messages] : (() => {
      try {
        const raw = localStorage.getItem(STORAGE_KEY_MSGS_PREFIX + thisConvId);
        return raw ? JSON.parse(raw) as ChatMessage[] : [];
      } catch { return []; }
    })();
    const sctx: StreamContext = {
      abort,
      reader: null,
      isStreaming: true,
      messages: [...fallbackMessages, userMsg, assistantMsg],
      activeSubAgents: [],
      subAgentTasks: [],
      isDelegating: false,
      pollingTimer: null,
    };
    streamContexts.current.set(thisConvId, sctx);
    // User just sent a message — always scroll to bottom
    isNearBottomRef.current = true;
    // Functional updater chains with any pending setMessages (e.g. handleAskAnswer's answered flag)
    if (thisConvId === activeConvIdRef.current) {
      setMessages((prev) => {
        const updated = [...prev, userMsg, assistantMsg];
        sctx.messages = updated;
        return updated;
      });
    } else {
      setMessages(sctx.messages);
    }
    setStreamingTick(t => t + 1);

    // ── Per-session helpers: write to StreamContext, sync to screen only if active ──
    // rAF throttle: StreamContext always gets the latest data immediately,
    // but React state (setMessages) is flushed at most once per animation frame.
    // This reduces O(N) reconciliation from ~30-60/s to ≤60fps, critical for long histories.
    let screenFlushRaf = 0;
    const flushToScreen = () => {
      screenFlushRaf = 0;
      const c = streamContexts.current.get(thisConvId);
      if (c && activeConvIdRef.current === thisConvId) setMessages(c.messages);
    };
    const updateMessages = (updater: (msgs: ChatMessage[]) => ChatMessage[]) => {
      const c = streamContexts.current.get(thisConvId);
      if (!c) return;
      c.messages = updater(c.messages);
      if (activeConvIdRef.current === thisConvId && !screenFlushRaf) {
        screenFlushRaf = requestAnimationFrame(flushToScreen);
      }
    };
    const updateSubAgents = (
      agentsUpdater?: (prev: SubAgentEntry[]) => SubAgentEntry[],
      tasksUpdater?: (prev: SubAgentTask[]) => SubAgentTask[],
    ) => {
      const c = streamContexts.current.get(thisConvId);
      if (!c) return;
      if (agentsUpdater) c.activeSubAgents = agentsUpdater(c.activeSubAgents);
      if (tasksUpdater) c.subAgentTasks = tasksUpdater(c.subAgentTasks);
      if (activeConvIdRef.current === thisConvId) {
        if (agentsUpdater) setDisplayActiveSubAgents(c.activeSubAgents);
        if (tasksUpdater) setDisplaySubAgentTasks(c.subAgentTasks);
      }
    };

    const IDLE_TIMEOUT_MS = 300_000;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        abort.abort();
        const c = streamContexts.current.get(thisConvId);
        c?.reader?.cancel().catch(() => {});
      }, IDLE_TIMEOUT_MS);
    };

    try {
      const effectiveMode = modeOverride ?? chatMode;
      const body: Record<string, unknown> = {
        message: text,
        conversation_id: convId,
        mode: effectiveMode,
        plan_mode: effectiveMode === "plan",
        endpoint: selectedEndpoint === "auto" ? null : selectedEndpoint,
        thinking_mode: thinkingMode !== "auto" ? thinkingMode : null,
        thinking_depth: thinkingMode !== "off" ? thinkingDepth : null,
        agent_profile_id: multiAgentEnabled ? selectedAgent : undefined,
        client_id: getClientId(),
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
        if (response.status === 409) {
          try {
            const busyData = await response.json();
            if (busyData?.error === "conversation_busy") {
              const busyCid = busyData.busy_client_id as string;
              setBusyConversations((prev) => { const m = new Map(prev); m.set(thisConvId, busyCid); return m; });
              updateMessages((prev) => prev.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, content: t("chat.busyOnOtherDevice"), streaming: false }
                  : m
              ));
              if (thisConvId) updateConvStatus(thisConvId, "idle");
              return;
            }
          } catch { /* fall through to generic error */ }
        }
        const errText = await response.text().catch(() => "请求失败");
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: `错误：${response.status} ${errText}`, streaming: false } : m
        ));
        if (thisConvId) updateConvStatus(thisConvId, "error");
        return;
      }

      // 收到响应头，重置空闲计时
      resetIdleTimer();

      // 处理 SSE 流
      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");
      sctx.reader = reader;

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

        // 拆行：done 时 flush 全部 buffer，否则保留不完整的末行
        let lines: string[];
        if (done) {
          lines = buffer.split("\n");
          buffer = "";
        } else {
          lines = buffer.split("\n");
          buffer = lines.pop() || "";
        }

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
                  updateMessages((prev) => {
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

                    const uMsg = { id: genId(), role: "user" as const, content: insertContent, timestamp: Date.now() };
                    if (assistantIdx >= 0) {
                      const newArr = [...prev];
                      newArr.splice(assistantIdx, 0, uMsg);
                      return newArr;
                    }
                    return [...prev, uMsg];
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
              case "text":
                currentContent += event.content ?? event.text ?? "";
                break;
              case "tool_call_start": {
                if (event.tool === "delegate_to_agent" && event.args?.agent_id) {
                  const targetId = String(event.args.agent_id);
                  updateSubAgents((prev) => {
                    const exists = prev.find((s) => s.agentId === targetId);
                    if (exists) return prev.map((s) => s.agentId === targetId ? { ...s, status: "delegating", startTime: Date.now() } : s);
                    return [...prev, { agentId: targetId, status: "delegating" as const, reason: String(event.args.reason || ""), startTime: Date.now() }];
                  }, undefined);
                }
                if (event.tool === "delegate_parallel" && Array.isArray(event.args?.tasks)) {
                  updateSubAgents((prev) => {
                    let updated = [...prev];
                    for (const task of event.args.tasks as Array<{ agent_id?: string; reason?: string }>) {
                      if (!task.agent_id) continue;
                      const targetId = String(task.agent_id);
                      const exists = updated.find((s) => s.agentId === targetId);
                      if (exists) {
                        updated = updated.map((s) => s.agentId === targetId ? { ...s, status: "delegating" as const, startTime: Date.now() } : s);
                      } else {
                        updated.push({ agentId: targetId, status: "delegating" as const, reason: String(task.reason || ""), startTime: Date.now() });
                      }
                    }
                    return updated;
                  }, undefined);
                }
                if (event.tool === "spawn_agent") {
                  const targetId = String(event.args?.inherit_from || event.args?.agent_id || `spawn_${Date.now()}`);
                  updateSubAgents((prev) => {
                    const exists = prev.find((s) => s.agentId === targetId);
                    if (exists) return prev.map((s) => s.agentId === targetId ? { ...s, status: "delegating" as const, startTime: Date.now() } : s);
                    return [...prev, { agentId: targetId, status: "delegating" as const, reason: String(event.args?.task || event.args?.reason || ""), startTime: Date.now() }];
                  }, undefined);
                }
                if (event.tool === "create_agent" && event.args?.name) {
                  const targetId = String(event.args.name);
                  updateSubAgents((prev) => {
                    const exists = prev.find((s) => s.agentId === targetId);
                    if (exists) return prev.map((s) => s.agentId === targetId ? { ...s, status: "delegating" as const, startTime: Date.now() } : s);
                    return [...prev, { agentId: targetId, status: "delegating" as const, reason: String(event.args.description || ""), startTime: Date.now() }];
                  }, undefined);
                }

                // Per-session polling for sub-agent progress
                const _isAgentTool = event.tool === "delegate_to_agent" || event.tool === "delegate_parallel" || event.tool === "spawn_agent" || event.tool === "create_agent";
                if (_isAgentTool) {
                  logger.info("Chat", "Agent tool detected in SSE", {
                    tool: event.tool, args: JSON.stringify(event.args || {}).slice(0, 200),
                    multiAgentEnabled: String(multiAgentEnabled),
                    activeConv: activeConvIdRef.current, thisConv: thisConvId,
                    subAgentsCount: sctx.activeSubAgents.length,
                  });
                }
                if (_isAgentTool && !sctx.isDelegating) {
                  sctx.isDelegating = true;
                  if (sctx.pollingTimer) clearInterval(sctx.pollingTimer);
                  const doFetch = () => {
                    safeFetch(`${apiBase}/api/agents/sub-tasks?conversation_id=${encodeURIComponent(thisConvId)}`)
                      .then((r) => r.json())
                      .then((data: SubAgentTask[]) => {
                        if (!Array.isArray(data)) return;
                        const c = streamContexts.current.get(thisConvId);
                        if (c) c.subAgentTasks = data;
                        if (activeConvIdRef.current === thisConvId) setDisplaySubAgentTasks(data);
                        logger.debug("Chat", "Sub-tasks poll result", {
                          count: data.length,
                          activeConvMatch: String(activeConvIdRef.current === thisConvId),
                        });
                        const allDone = data.length > 0 && data.every(
                          (t) => t.status === "completed" || t.status === "error" || t.status === "timeout" || t.status === "cancelled"
                        );
                        if (allDone && c?.pollingTimer) {
                          clearInterval(c.pollingTimer);
                          c.pollingTimer = null;
                          c.isDelegating = false;
                        }
                      })
                      .catch((e) => {
                        logger.warn("Chat", "Sub-tasks poll failed", { error: String(e) });
                      });
                  };
                  setTimeout(doFetch, 500);
                  sctx.pollingTimer = setInterval(doFetch, 2000);
                }

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
                const _isAgentToolEnd = event.tool === "delegate_to_agent" || event.tool === "delegate_parallel" || event.tool === "spawn_agent" || event.tool === "create_agent";
                if (_isAgentToolEnd) {
                  const isErr = event.is_error === true || (event.result || "").startsWith("❌");
                  updateSubAgents((prev) => prev.map((s) =>
                    s.status === "delegating" ? { ...s, status: isErr ? "error" : "done" } : s
                  ), undefined);
                  sctx.isDelegating = false;
                  if (sctx.pollingTimer) { clearInterval(sctx.pollingTimer); sctx.pollingTimer = null; }
                  safeFetch(`${apiBase}/api/agents/sub-tasks?conversation_id=${encodeURIComponent(thisConvId)}`)
                    .then((r) => r.json())
                    .then((data: SubAgentTask[]) => {
                      if (!Array.isArray(data)) return;
                      const c = streamContexts.current.get(thisConvId);
                      if (c) c.subAgentTasks = data;
                      if (activeConvIdRef.current === thisConvId) setDisplaySubAgentTasks(data);
                      const allDone = data.length > 0 && data.every(
                        (t) => t.status === "completed" || t.status === "error" || t.status === "timeout" || t.status === "cancelled"
                      );
                      if (allDone) {
                        setTimeout(() => {
                          const c2 = streamContexts.current.get(thisConvId);
                          if (c2) { c2.subAgentTasks = []; c2.activeSubAgents = []; }
                          if (activeConvIdRef.current === thisConvId) {
                            setDisplaySubAgentTasks([]);
                            setDisplayActiveSubAgents([]);
                          }
                        }, 5000);
                      }
                    })
                    .catch(() => {});
                }
                // Refresh profiles when a new agent is created
                if (event.tool === "create_agent" && !(event.is_error || (event.result || "").startsWith("❌"))) {
                  safeFetch(`${apiBase}/api/agents/profiles`)
                    .then((r) => r.json())
                    .then((data) => { if (data?.profiles) setAgentProfiles(data.profiles); })
                    .catch(() => {});
                }
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
              case "todo_created":
                currentPlan = event.plan;
                updateMessages((prev) => prev.map((m) =>
                  m.plan && m.plan.status !== "completed" && m.plan.status !== "failed" && m.plan.status !== "cancelled"
                    ? { ...m, plan: { ...m.plan, status: "completed" as const } }
                    : m
                ));
                break;
              case "todo_step_updated":
                if (currentPlan) {
                  const newSteps: ChatTodoStep[] = currentPlan.steps.map((s) => {
                    const matched = event.stepId
                      ? s.id === event.stepId
                      : event.stepIdx != null && currentPlan!.steps.indexOf(s) === event.stepIdx;
                    return matched ? { ...s, status: event.status as ChatTodoStep["status"] } : s;
                  });
                  const allDone = newSteps.every((s) => s.status === "completed" || s.status === "skipped" || s.status === "failed");
                  currentPlan = { ...currentPlan, steps: newSteps, ...(allDone ? { status: "completed" as const } : {}) } as ChatPlan;
                }
                break;
              case "todo_completed":
                if (currentPlan) {
                  currentPlan = { ...currentPlan, status: "completed" } as ChatPlan;
                }
                break;
              case "todo_cancelled":
                if (currentPlan) {
                  currentPlan = { ...currentPlan, status: "cancelled" } as ChatPlan;
                }
                break;
              case "security_confirm": {
                setSecurityConfirm({
                  tool: event.tool,
                  args: event.args,
                  reason: event.reason,
                  riskLevel: event.risk_level,
                  needsSandbox: event.needs_sandbox,
                  toolId: event.id,
                  countdown: 60,
                });
                break;
              }
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
                logger.debug("Chat", "Artifact SSE received", { name: event.name, file_url: event.file_url, artifact_type: event.artifact_type });
                currentArtifacts = [...currentArtifacts, {
                  artifact_type: event.artifact_type,
                  file_url: event.file_url,
                  path: event.path,
                  name: event.name,
                  caption: event.caption,
                  size: event.size,
                }];
                break;
              case "agent_handoff": {
                updateSubAgents((prev) => {
                  const exists = prev.find((s) => s.agentId === event.to_agent);
                  if (exists) return prev.map((s) => s.agentId === event.to_agent ? { ...s, status: "delegating", startTime: Date.now() } : s);
                  return [...prev, { agentId: event.to_agent, status: "delegating" as const, reason: event.reason, startTime: Date.now() }];
                }, undefined);
                break;
              }
              case "agent_switch":
                currentAgent = event.agentName;
                updateMessages((prev) => {
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
                    artifacts: currentArtifacts.length > 0 ? [...currentArtifacts] : null,
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
                updateMessages((prev) => {
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
            updateMessages((prev) => prev.map((m) =>
              m.id === assistantMsg.id
                ? {
                    ...m,
                    content: currentContent,
                    thinking: currentThinking || null,
                    agentName: currentAgent,
                    toolCalls: currentToolCalls.length > 0 ? [...currentToolCalls] : null,
                    plan: currentPlan ? { ...currentPlan } : null,
                    askUser: currentAsk,
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

        if (done) break;
      }

      // ── 循环结束后：判断是正常完成还是被用户中止 ──
      if (abort.signal.aborted) {
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, content: m.content || "（已中止）", streaming: false }
            : m
        ));
      } else {
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id
            ? {
                ...m,
                content: m.content || (m.askUser ? "" : "⚠️ 未收到有效回复，请重试。"),
                streaming: false,
              }
            : m
        ));
        // 兜底对账：若 SSE 流正常完成却未交付任何有效响应，从 session history 回填。
        // 注意：ask_user / 纯工具执行等结构化响应设计上不产生 text_delta，
        // 需同时检查所有响应载体，避免将"无文本的正常响应"误判为"流失败"。
        const streamDeliveredPayload = !!(
          currentContent.trim() || currentAsk || currentToolCalls.length > 0
        );
        if (gracefulDone && !streamDeliveredPayload && convId) {
          safeFetch(`${apiBase}/api/sessions/${encodeURIComponent(convId)}/history`)
            .then((r) => r.json())
            .then((data) => {
              const rows = Array.isArray(data?.messages) ? data.messages : [];
              // Prefer assistant replies generated after this user turn; fallback to latest assistant.
              const candidates = rows.filter((m: { role?: string; content?: string }) => m?.role === "assistant" && typeof m?.content === "string");
              const newerThanUser = candidates.filter((m: { timestamp?: number }) => typeof m?.timestamp === "number" && m.timestamp >= userMsg.timestamp);
              const lastAssistant = (newerThanUser.length > 0 ? newerThanUser : candidates).slice(-1)[0];
              if (!lastAssistant?.content) return;
              setMessages((prev) => prev.map((m) => {
                if (m.id !== assistantMsg.id) return m;
                const patched: ChatMessage = { ...m, content: m.content || lastAssistant.content };
                if ((!m.thinkingChain || m.thinkingChain.length === 0) && Array.isArray(lastAssistant.chain_summary) && lastAssistant.chain_summary.length > 0) {
                  patched.thinkingChain = buildChainFromSummary(lastAssistant.chain_summary);
                }
                return patched;
              }));
            })
            .catch(() => {});
        }
      }
    } catch (e: unknown) {
      // Only treat as user-initiated abort when OUR AbortController was triggered.
      // DOMException "AbortError" can also originate from the browser itself
      // (page lifecycle freeze, tab discard, memory pressure) and must NOT be
      // conflated with explicit user cancellation.
      if (abort.signal.aborted) {
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: m.content || "（已中止）", streaming: false } : m
        ));
      } else {
        const isBrowserAbort =
          (e instanceof DOMException && e.name === "AbortError") ||
          (e instanceof Error && e.name === "AbortError");

        if (isBrowserAbort) {
          // Browser killed the connection (tab frozen / discarded, etc.)
          // Preserve whatever content we already received — don't overwrite it.
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, streaming: false } : m
          ));
        } else {
          const errMsg = e instanceof Error ? e.message : String(e);
          let guidance = t("chat.backendServiceHint");
          try {
            const healthRes = await fetch(`${apiBase}/api/health`, { signal: AbortSignal.timeout(2000) });
            if (healthRes.ok) {
              guidance = t("chat.backendOnlineUpstreamHint");
            }
          } catch { /* health probe failed -> keep backend guidance */ }
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content || `连接失败：${errMsg}\n\n${guidance}`, streaming: false } : m
          ));
        }

        // Fire-and-forget: try to recover the (possibly completed) response
        // from backend session history.  The backend may have finished the
        // LLM call even though the frontend stream was interrupted.
        if (convId) {
          const _recoverMsgId = assistantMsg.id;
          const _recoverUserTs = userMsg.timestamp;
          const _recoverKey = STORAGE_KEY_MSGS_PREFIX + thisConvId;
          setTimeout(() => {
            safeFetch(`${apiBase}/api/sessions/${encodeURIComponent(convId)}/history`)
              .then((r) => r.ok ? r.json() : null)
              .then((data) => {
                if (!data) return;
                const rows = Array.isArray(data?.messages) ? data.messages : [];
                const candidates = rows.filter(
                  (m: { role?: string; content?: string }) =>
                    m?.role === "assistant" && typeof m?.content === "string",
                );
                const newerThanUser = candidates.filter(
                  (m: { timestamp?: number }) =>
                    typeof m?.timestamp === "number" && m.timestamp >= _recoverUserTs,
                );
                const lastAssistant = (newerThanUser.length > 0 ? newerThanUser : candidates).slice(-1)[0];
                if (!lastAssistant?.content) return;
                setMessages((prev) => {
                  const updated = prev.map((m) => {
                    if (m.id !== _recoverMsgId) return m;
                    if (m.content && m.content.length >= (lastAssistant.content as string).length) return m;
                    const patched: ChatMessage = { ...m, content: lastAssistant.content };
                    if (
                      (!m.thinkingChain || m.thinkingChain.length === 0) &&
                      Array.isArray(lastAssistant.chain_summary) &&
                      lastAssistant.chain_summary.length > 0
                    ) {
                      patched.thinkingChain = buildChainFromSummary(lastAssistant.chain_summary);
                    }
                    return patched;
                  });
                  try { saveMessagesToStorage(_recoverKey, updated); } catch { /* quota */ }
                  return updated;
                });
              })
              .catch(() => {});
          }, 3000);
        }
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      if (screenFlushRaf) { cancelAnimationFrame(screenFlushRaf); screenFlushRaf = 0; }
      const ctx = streamContexts.current.get(thisConvId);
      if (ctx) {
        ctx.isStreaming = false;
        try { ctx.reader?.cancel().catch(() => {}); } catch {}
        ctx.reader = null;
        const hasRunning = ctx.subAgentTasks.some(
          (t) => t.status === "running" || t.status === "starting"
        );
        if (hasRunning && !ctx.pollingTimer) {
          const doFetch = () => {
            safeFetch(`${apiBase}/api/agents/sub-tasks?conversation_id=${encodeURIComponent(thisConvId)}`)
              .then((r) => r.json())
              .then((data: SubAgentTask[]) => {
                if (!Array.isArray(data)) return;
                if (activeConvIdRef.current === thisConvId) setDisplaySubAgentTasks(data);
                const allDone = data.length > 0 && data.every(
                  (t) => t.status === "completed" || t.status === "error" || t.status === "timeout" || t.status === "cancelled"
                );
                if (allDone) {
                  if (finalPollingTimer) { clearInterval(finalPollingTimer); finalPollingTimer = null; }
                  setTimeout(() => {
                    if (activeConvIdRef.current === thisConvId) {
                      setDisplaySubAgentTasks([]);
                      setDisplayActiveSubAgents([]);
                    }
                  }, 5000);
                }
              })
              .catch(() => {});
          };
          let finalPollingTimer: ReturnType<typeof setInterval> | null = setInterval(doFetch, 2000);
          doFetch();
          setTimeout(() => {
            if (finalPollingTimer) { clearInterval(finalPollingTimer); finalPollingTimer = null; }
          }, 600_000);
        } else if (!hasRunning) {
          if (ctx.pollingTimer) { clearInterval(ctx.pollingTimer); ctx.pollingTimer = null; }
          if (activeConvIdRef.current === thisConvId) {
            setTimeout(() => {
              setDisplayActiveSubAgents([]);
              setDisplaySubAgentTasks([]);
            }, 5000);
          }
        } else {
          if (ctx.pollingTimer) { clearInterval(ctx.pollingTimer); ctx.pollingTimer = null; }
        }
        saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId, ctx.messages);
        if (activeConvIdRef.current === thisConvId) {
          setMessages(ctx.messages);
        }
        streamContexts.current.delete(thisConvId);
      }
      setStreamingTick(t => t + 1);

      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === thisConvId
            ? { ...c, lastMessage: text.slice(0, 60), timestamp: Date.now(), messageCount: (c.messageCount || 0) + 2, status: "completed" as ConversationStatus }
            : c
        );
        const conv = updated.find((c) => c.id === thisConvId);
        if (conv && !conv.titleGenerated && (conv.messageCount || 0) <= 2) {
          (async () => {
            try {
              const res = await safeFetch(`${apiBase}/api/sessions/generate-title`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text }),
                signal: AbortSignal.timeout(15000),
              });
              const data = await res.json();
              if (data.title) {
                setConversations((p) => p.map((c) =>
                  c.id === thisConvId ? { ...c, title: data.title, titleGenerated: true } : c
                ));
              }
            } catch { /* fallback: keep truncated title */ }
          })();
        }
        return updated;
      });
    }
  }, [pendingAttachments, isCurrentConvStreaming, activeConvId, chatMode, selectedEndpoint, apiBase, slashCommands, thinkingMode, thinkingDepth, t, setInputValue]);

  // ── 处理用户回答 (ask_user) ──
  const handleAskAnswer = useCallback((msgId: string, answer: string) => {
    const target = latestMessagesRef.current.find((m) => m.id === msgId);
    const displayText = target?.askUser
      ? formatAskUserAnswer(answer, target.askUser)
      : undefined;

    const isPlanSwitch = answer === "plan" && target?.askUser?.options?.some((o: { id: string }) => o.id === "plan");
    if (isPlanSwitch) {
      setChatMode("plan");
    }

    setMessages((prev) => prev.map((m) =>
      m.id === msgId && m.askUser
        ? { ...m, askUser: { ...m.askUser, answered: true, answer } }
        : m
    ));
    // reason_stream 在 ask_user 后中断流，用户回复通过新 /api/chat 请求继续处理
    sendMessage(answer, undefined, displayText !== answer ? displayText : undefined, isPlanSwitch ? "plan" : undefined);
  }, [sendMessage]);

  // ── 停止生成 ──
  const stopStreaming = useCallback((targetConvId?: string) => {
    const id = targetConvId ?? activeConvId;
    if (!id) return;
    const ctx = streamContexts.current.get(id);
    if (ctx) {
      ctx.abort.abort();
      try { ctx.reader?.cancel().catch(() => {}); } catch {}
      ctx.reader = null;
    }
  }, [activeConvId]);

  // ── 消息排队系统 ──
  const [messageQueue, setMessageQueue] = useState<QueuedMessage[]>([]);
  const [queueExpanded, setQueueExpanded] = useState(true);

  const handleSkipStep = useCallback(() => {
    safeFetch(`${apiBase}/api/chat/skip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, reason: "用户从界面跳过步骤" }),
    }).catch(() => {});
  }, [apiBase, activeConvId]);

  const handleImagePreview = useCallback((url: string, name: string) => {
    setLightbox({ url, name });
  }, []);

  const handleCancelTask = useCallback(() => {
    safeFetch(`${apiBase}/api/chat/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, reason: "用户从界面取消任务" }),
    }).then(() => {
      const cid = activeConvId;
      setTimeout(() => {
        if (cid && streamContexts.current.get(cid)?.reader) stopStreaming(cid);
      }, 2000);
    }).catch(() => {
      stopStreaming();
    });
  }, [apiBase, activeConvId, stopStreaming]);

  const handleInsertMessage = useCallback((text: string) => {
    if (!text.trim()) return;
    const inserter = (prev: ChatMessage[]) => {
      const uMsg = { id: genId(), role: "user" as const, content: text.trim(), timestamp: Date.now() };
      const streamingIdx = prev.findIndex((m) => m.role === "assistant" && m.streaming);
      if (streamingIdx >= 0) {
        const newArr = [...prev];
        newArr.splice(streamingIdx, 0, uMsg);
        return newArr;
      }
      return [...prev, uMsg];
    };
    const ctx = activeConvId ? streamContexts.current.get(activeConvId) : null;
    if (ctx) ctx.messages = inserter(ctx.messages);
    setMessages(inserter);
    safeFetch(`${apiBase}/api/chat/insert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, message: text }),
    }).catch(() => {});
  }, [apiBase, activeConvId]);

  const handleQueueMessage = useCallback(() => {
    const text = inputTextRef.current.trim();
    if (!text || !activeConvId) return;
    setMessageQueue(prev => [...prev, { id: genId(), text, timestamp: Date.now(), convId: activeConvId }]);
    setInputValue("");
  }, [activeConvId, setInputValue]);

  const handleRemoveQueued = useCallback((id: string) => {
    setMessageQueue(prev => prev.filter(m => m.id !== id));
  }, []);

  const handleEditQueued = useCallback((id: string) => {
    const item = messageQueue.find(m => m.id === id);
    if (item) {
      setInputValue(item.text);
      setMessageQueue(prev => prev.filter(m => m.id !== id));
      inputRef.current?.focus();
    }
  }, [messageQueue, setInputValue]);

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

  // ── 排队消息自动出队 ──
  // 后端支持并发流式 — 每会话独立 Agent 实例。
  // 排队仅限同会话：某会话流结束时，出队该会话排队的下一条消息。
  const prevStreamingSetRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const currentStreamingSet = new Set(
      [...streamContexts.current.entries()].filter(([, c]) => c.isStreaming).map(([id]) => id),
    );
    if (messageQueue.length === 0) {
      prevStreamingSetRef.current = currentStreamingSet;
      return;
    }
    for (const finishedId of prevStreamingSetRef.current) {
      if (!currentStreamingSet.has(finishedId)) {
        const nextIdx = messageQueue.findIndex(m => m.convId === finishedId);
        if (nextIdx >= 0) {
          const next = messageQueue[nextIdx];
          setMessageQueue(prev => prev.filter((_, i) => i !== nextIdx));
          const targetId = next.convId;
          setTimeout(() => {
            sendMessage(next.text, targetId);
          }, 100);
          break;
        }
      }
    }
    prevStreamingSetRef.current = currentStreamingSet;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamingTick, messageQueue, sendMessage]);

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

  // ── 拖拽图片/文件 (Tauri native or HTML5 drag-drop) ──
  const [dragOver, setDragOver] = useState(false);
  useEffect(() => {
    if (!IS_TAURI) return; // Web uses HTML5 drag-drop via onDrop on the container
    let cancelled = false;
    let unlisten: (() => void) | null = null;

    const mimeMap: Record<string, string> = {
      png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
      gif: "image/gif", webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
      mp4: "video/mp4", webm: "video/webm", avi: "video/x-msvideo",
      mov: "video/quicktime", mkv: "video/x-matroska",
      pdf: "application/pdf", txt: "text/plain", md: "text/plain",
      json: "application/json", csv: "text/csv",
    };

    const handleDroppedPaths = (paths: string[]) => {
      for (const filePath of paths) {
        const name = filePath.split(/[\\/]/).pop() || "file";
        const ext = (name.split(".").pop() || "").toLowerCase();
        const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
        const isVideo = ["mp4", "webm", "avi", "mov", "mkv"].includes(ext);
        const mimeType = mimeMap[ext] || "application/octet-stream";
        readFileBase64(filePath)
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
            setPendingAttachments((prev) => [...prev, {
              type: isImage ? "image" : isVideo ? "video" : "file",
              name,
              previewUrl: isImage ? dataUrl : undefined,
              url: dataUrl,
              mimeType,
            }]);
          })
          .catch((err) => logger.error("Chat", "DragDrop read_file_base64 failed", { name, error: String(err) }));
      }
    };

    onDragDrop({
      onEnter: () => { if (!cancelled) setDragOver(true); },
      onOver: () => { if (!cancelled) setDragOver(true); },
      onLeave: () => { if (!cancelled) setDragOver(false); },
      onDrop: (paths) => {
        if (cancelled) return;
        setDragOver(false);
        handleDroppedPaths(paths);
      },
    }).then((unsub) => { unlisten = unsub; });

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
    // macOS 中文输入法按回车选字时 isComposing=true，此时不应触发发送
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;

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
          setInputValue("");
          setSlashOpen(false);
        }
      } else if (e.key === "Escape") {
        setSlashOpen(false);
      }
      return;
    }

    if (isCurrentConvStreaming) {
      // 当前会话正在流式传输:
      //   有文本 + Ctrl+Enter = 立即插入（仅当前会话流式时可用）
      //   有文本 + Enter     = 排队
      //   空文本 + Enter     = 取队列第一条立即插入
      const domText = (e.target as HTMLTextAreaElement).value.trim();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (domText) {
          handleInsertMessage(domText);
          setInputValue("");
        }
      } else if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (domText) {
          handleQueueMessage();
        } else {
          const myFirst = messageQueue.find(m => m.convId === activeConvId);
          if (myFirst) {
            setMessageQueue(prev => prev.filter(m => m.id !== myFirst.id));
            handleInsertMessage(myFirst.text);
          }
        }
      }
    } else {
      // 非当前会话流式中: Enter / Ctrl+Enter 直接发送（后端支持并发）
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        sendMessage();
      } else if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        sendMessage();
      }
    }
  }, [slashOpen, slashFilter, slashCommands, slashSelectedIdx, sendMessage, isCurrentConvStreaming, handleInsertMessage, handleQueueMessage, messageQueue, activeConvId, setInputValue]);

  // ── 输入变化处理（非受控模式：仅更新 ref，不触发全局重渲染） ──
  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    inputTextRef.current = val;
    const has = val.trim().length > 0;
    setHasInputText(prev => prev !== has ? has : prev);

    // @org: 前缀检测 — 自动切换到组织模式
    const orgMatch = val.match(/^@org:(\S+)\s/);
    if (orgMatch && !orgMode) {
      const target = orgMatch[1];
      const match = orgList.find(o => o.name.includes(target) || o.id === target);
      if (match) {
        setOrgMode(true);
        setSelectedOrgId(match.id);
      }
    }

    if (val.startsWith("/") && !val.includes(" ")) {
      setSlashOpen(true);
      setSlashFilter(val.slice(1));
      setSlashSelectedIdx(0);
    } else {
      setSlashOpen(false);
    }
  }, [orgMode, orgList]);

  // ── Filtered + grouped conversations for Cursor-style sidebar ──
  const filteredConversations = useMemo(() => {
    const q = convSearchQuery.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) =>
      c.title.toLowerCase().includes(q) ||
      (c.lastMessage || "").toLowerCase().includes(q)
    );
  }, [conversations, convSearchQuery]);

  const pinnedConvs = useMemo(() =>
    filteredConversations.filter((c) => c.pinned).sort((a, b) => b.timestamp - a.timestamp),
    [filteredConversations]
  );
  const agentConvs = useMemo(() =>
    filteredConversations.filter((c) => !c.pinned).sort((a, b) => b.timestamp - a.timestamp),
    [filteredConversations]
  );

  // ── 未启动服务提示 ──
  if (!serviceRunning) {
    return (
      <div className="imViewEmpty">
        <IconMessageCircle size={48} />
        <div style={{ marginTop: 12, fontWeight: 600 }}>{t("chat.title")}</div>
        <div style={{ marginTop: 4, opacity: 0.5, fontSize: 13 }}>后端服务未启动，请启动后再进行使用</div>
      </div>
    );
  }

  const statusIcon = (status?: ConversationStatus) => {
    switch (status) {
      case "running":
        return <span className="convStatusDot convStatusRunning"><IconLoader size={12} /></span>;
      case "completed":
        return <span className="convStatusDot convStatusCompleted"><IconCheck size={12} /></span>;
      case "error":
        return <span className="convStatusDot convStatusError"><IconXCircle size={12} /></span>;
      default:
        return <span className="convStatusDot convStatusIdle"><IconCircleDot size={12} /></span>;
    }
  };

  const renderConvItem = (conv: ChatConversation) => {
    const isActive = conv.id === activeConvId;
    const profileId = conv.agentProfileId || "default";
    const agentProfile = agentProfiles.find((p) => p.id === profileId) ?? null;
    return (
      <div
        key={conv.id}
        className={`convItem ${isActive ? "convItemActive" : ""}`}
        onClick={() => { if (renamingId !== conv.id) setActiveConvId(conv.id); }}
        onContextMenu={(e) => { e.preventDefault(); (e.nativeEvent as any)._handled = true; setCtxMenu({ x: e.clientX, y: e.clientY, convId: conv.id }); }}
      >
        <div className="convItemIcon">
          <span title={agentProfile?.name || ""} style={{ fontSize: 16 }}>{agentProfile?.icon || "💬"}</span>
        </div>
        <div className="convItemBody">
          {renamingId === conv.id ? (
            <input
              autoFocus
              value={renameText}
              onChange={(e) => setRenameText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") confirmRename(conv.id, renameText);
                if (e.key === "Escape") { setRenamingId(null); setRenameText(""); }
              }}
              onBlur={() => confirmRename(conv.id, renameText)}
              onClick={(e) => e.stopPropagation()}
              className="convRenameInput"
            />
          ) : (
            <>
              <div className="convItemTitle">{conv.title}</div>
              <div className="convItemMeta">
                {agentProfile && <span className="convItemAgent">{agentProfile.name}</span>}
                {conv.lastMessage && <span className="convItemDesc">{conv.lastMessage.slice(0, 40)}</span>}
              </div>
            </>
          )}
        </div>
        <div className="convItemRight">
          <span className="convItemTime">{timeAgo(conv.timestamp)}</span>
          {isConvBusyOnOtherDevice(conv.id)
            ? <span className="convStatusDot" style={{ color: "var(--warning, #eab308)", fontSize: 10, whiteSpace: "nowrap" }} title={t("chat.busyOnOtherDevice")}>⏳</span>
            : statusIcon(conv.status)}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0 }}>

      {/* 会话右键菜单 — portal 到 body 避免父级 backdrop-filter 影响 fixed 定位 */}
      {ctxMenu && createPortal(
        <div
          style={{ position: "fixed", inset: 0, zIndex: 9999 }}
          onClick={() => setCtxMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setCtxMenu(null); }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              left: ctxMenu.x,
              top: ctxMenu.y,
              background: "var(--panel)",
              backdropFilter: "blur(16px)",
              WebkitBackdropFilter: "blur(16px)",
              border: "1px solid var(--line)",
              borderRadius: 10,
              boxShadow: "0 8px 24px rgba(0,0,0,0.22)",
              padding: "4px 0",
              minWidth: 140,
              fontSize: 13,
              zIndex: 10000,
            }}
          >
            {([
              {
                label: conversations.find((c) => c.id === ctxMenu.convId)?.pinned
                  ? t("chat.unpinConversation") : t("chat.pinConversation"),
                icon: <IconPin size={13} />,
                danger: false,
                action: () => togglePinConversation(ctxMenu.convId),
              },
              {
                label: t("chat.renameConversation"),
                icon: <IconEdit size={13} />,
                danger: false,
                action: () => {
                  const conv = conversations.find((c) => c.id === ctxMenu.convId);
                  if (conv) { setRenamingId(conv.id); setRenameText(conv.title); }
                  setCtxMenu(null);
                },
              },
              {
                label: t("chat.deleteConversation"),
                icon: <IconTrash size={13} />,
                danger: true,
                action: () => { deleteConversation(ctxMenu.convId); setCtxMenu(null); },
              },
            ]).map((item, i) => (
              <div
                key={i}
                onClick={item.action}
                style={{
                  padding: "8px 14px",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  color: item.danger ? "#ef4444" : "inherit",
                  transition: "background 0.1s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = item.danger ? "rgba(239,68,68,0.08)" : "rgba(37,99,235,0.08)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                <span style={{ opacity: 0.6, display: "flex" }}>{item.icon}</span>
                {item.label}
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}

      {/* 主聊天区 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }} onMouseDown={() => { if (sidebarOpen && !sidebarPinned) setSidebarOpen(false); }}>
        {/* Chat top bar */}
        <div className="chatTopBar">
          <button onClick={newConversation} className="chatTopBarBtn">
            <IconPlus size={14} />
          </button>

          {/* Active agent orbits — shown when sidebar is closed */}
          {!sidebarOpen && conversations.length > 0 && (
            <div className="agentOrbitStrip">
              {conversations
                .slice()
                .sort((a, b) => b.timestamp - a.timestamp)
                .slice(0, 8)
                .map((conv) => {
                  const pid = conv.agentProfileId || "default";
                  const ap = agentProfiles.find((p) => p.id === pid) ?? null;
                  const isActive = conv.id === activeConvId;
                  const isRunning = conv.status === "running" || streamContexts.current.has(conv.id);
                  return (
                    <button
                      key={conv.id}
                      className={`agentOrbitNode ${isActive ? "agentOrbitActive" : ""} ${isRunning ? "agentOrbitRunning" : ""}`}
                      onClick={() => setActiveConvId(conv.id)}
                      onMouseEnter={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setOrbitTip({ x: rect.left + rect.width / 2, y: rect.bottom + 6, name: ap?.name || "Default", title: conv.title });
                      }}
                      onMouseLeave={() => setOrbitTip(null)}
                    >
                      <span className="agentOrbitIcon">
                        {ap?.icon || "💬"}
                      </span>
                      {isRunning && <span className="agentOrbitPulse" />}
                    </button>
                  );
                })}
            </div>
          )}

          {/* Active sub-agents in current conversation */}
          {displayActiveSubAgents.length > 0 && (
            <div className="subAgentStrip">
              <span className="subAgentLabel">协作中</span>
              {displayActiveSubAgents.map((sub) => {
                const sp = agentProfiles.find((p) => p.id === sub.agentId);
                return (
                  <div
                    key={sub.agentId}
                    className={`subAgentChip ${sub.status === "delegating" ? "subAgentActive" : sub.status === "error" ? "subAgentError" : "subAgentDone"}`}
                    title={sp?.name || sub.agentId}
                  >
                    <span className="subAgentChipIcon"><RenderIcon icon={sp?.icon || "🤖"} size={14} /></span>
                    <span className="subAgentChipName">{sp?.name || sub.agentId}</span>
                    {sub.status === "delegating" && <span className="subAgentSpinner" />}
                    {sub.status === "done" && <span className="subAgentCheck">✓</span>}
                    {sub.status === "error" && <span className="subAgentCross">✗</span>}
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ flex: 1 }} />

          <button
            onClick={() => setShowChain(v => !v)}
            className="chatTopBarBtn chainToggleBtn"
            title={showChain ? t("chat.hideChain") : t("chat.showChain")}
            style={{ opacity: showChain ? 1 : 0.4 }}
          >
            <IconZap size={14} />
          </button>

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

          <button
            onClick={() => setSidebarOpen((v) => !v)}
            className="chatTopBarBtn"
            style={{ background: sidebarOpen ? "rgba(37,99,235,0.08)" : "transparent" }}
            title={t("chat.toggleHistory") || "会话列表"}
          >
            <IconMenu size={16} />
          </button>
        </div>

        {/* 消息列表 */}
        <div ref={scrollContainerRef} style={{ flex: 1, overflow: "auto", padding: "16px 20px", minHeight: 0 }}>
          {messages.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", opacity: 0.4 }}>
              <div style={{ marginBottom: 12 }}><IconMessageCircle size={48} /></div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>{t("chat.emptyTitle")}</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>{t("chat.emptyDesc")}</div>
            </div>
          )}
          {messages.map((msg) =>
            displayMode === "flat" ? (
              <FlatMessageItem key={msg.id} msg={msg} onAskAnswer={handleAskAnswer} apiBaseUrl={apiBaseUrl} showChain={showChain} onSkipStep={handleSkipStep} onImagePreview={handleImagePreview} mdModules={mdModules} />
            ) : (
              <MessageBubble key={msg.id} msg={msg} onAskAnswer={handleAskAnswer} apiBaseUrl={apiBaseUrl} showChain={showChain} onSkipStep={handleSkipStep} onImagePreview={handleImagePreview} mdModules={mdModules} />
            )
          )}

          {/* Sub-agent progress cards */}
          {displaySubAgentTasks.length > 0 && (
            <SubAgentCards tasks={displaySubAgentTasks} />
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
          <div style={{ padding: "12px 16px 8px", borderTop: "1px solid var(--line)", display: "flex", flexWrap: "wrap", gap: 12, background: "var(--panel)", maxHeight: 140, overflowY: "auto" }}>
            {pendingAttachments.map((att, idx) => (
              <AttachmentPreview
                key={`${att.name}-${att.type}-${idx}`}
                att={att}
                onRemove={() => setPendingAttachments((prev) => prev.filter((_, i) => i !== idx))}
              />
            ))}
          </div>
        )}

        {/* Busy-on-other-device banner */}
        {activeConvId && isConvBusyOnOtherDevice(activeConvId) && (
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "8px 16px", margin: "0 16px 6px",
            borderRadius: 10, fontSize: 13,
            background: "rgba(234,179,8,0.12)", color: "var(--text)",
            border: "1px solid rgba(234,179,8,0.25)",
          }}>
            <span style={{ fontSize: 16 }}>⏳</span>
            <span style={{ flex: 1 }}>{t("chat.busyOnOtherDevice")}</span>
            <button
              onClick={newConversation}
              style={{
                padding: "4px 12px", borderRadius: 6, border: "none",
                background: "var(--primary, #3b82f6)", color: "#fff",
                cursor: "pointer", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap",
              }}
            >{t("chat.busyNewConversation")}</button>
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
                setInputValue("");
                setSlashOpen(false);
              }}
              selectedIdx={slashSelectedIdx}
            />
          )}

          {/* Queued messages list — Cursor style, per-session */}
          {(() => {
            const currentQueue = messageQueue.filter(m => m.convId === activeConvId);
            if (currentQueue.length === 0) return null;
            return (
              <div className="queuedContainer">
                <button
                  className="queuedHeader"
                  onClick={() => setQueueExpanded(v => !v)}
                >
                  <span className="queuedHeaderChevron">
                    {queueExpanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
                  </span>
                  <span className="queuedHeaderLabel">
                    {currentQueue.length} {t("chat.queuedCount")}
                  </span>
                </button>
                {queueExpanded && (
                  <div className="queuedList">
                    {currentQueue.map((qm, idx) => (
                      <div key={qm.id} className="queuedItem">
                        <span className="queuedItemIndicator">
                          <IconCircle size={10} />
                        </span>
                        <span className="queuedItemText" title={qm.text}>
                          {qm.text.length > 80 ? qm.text.slice(0, 80) + "..." : qm.text}
                        </span>
                        <div className="queuedItemActions">
                          <button
                            data-slot="queued"
                            className="queuedItemBtn queuedItemSendBtn"
                            onClick={() => handleSendQueuedNow(qm.id)}
                            title={t("chat.sendNow")}
                          >
                            <IconSend size={12} />
                          </button>
                          <button
                            data-slot="queued"
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
            );
          })()}

          <div className={`chatInputBox ${chatMode !== "agent" ? "chatInputBoxPlan" : ""}`}>
            {/* Top row: compact model picker */}
            <div className="chatInputTop" ref={modelMenuRef} style={{ position: "relative" }}>
              <button
                className="chatModelPickerBtn"
                onClick={() => setModelMenuOpen((v) => !v)}
              >
                <span className="chatModelPickerLabel">
                  {selectedEndpoint === "auto"
                    ? (() => {
                        const ap = multiAgentEnabled ? agentProfiles.find(p => p.id === selectedAgent) : null;
                        const pe = ap?.preferred_endpoint;
                        if (pe) {
                          const ep = endpoints.find(e => e.name === pe);
                          return `${t("chat.selectModel")} → ${ep ? ep.model : pe}`;
                        }
                        return t("chat.selectModel");
                      })()
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
                  {endpoints.map((ep) => {
                    const hs = ep.health?.status;
                    const dot = hs === "healthy" ? "🟢" : hs === "degraded" ? "🟡" : hs === "unhealthy" ? "🔴" : "⚪";
                    return (
                      <div
                        key={ep.name}
                        className={`chatModelMenuItem ${selectedEndpoint === ep.name ? "chatModelMenuItemActive" : ""}`}
                        onClick={() => { setSelectedEndpoint(ep.name); setModelMenuOpen(false); }}
                      >
                        <span style={{ fontSize: 8, marginRight: 6, lineHeight: 1 }}>{dot}</span>
                        <span style={{ fontWeight: 600 }}>{ep.model}</span>
                        <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{ep.name}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {multiAgentEnabled && agentProfiles.length > 0 && !orgMode && (
                <div ref={agentMenuRef} style={{ position: "relative", marginLeft: 8 }}>
                  <button
                    className="chatModelPickerBtn"
                    onClick={() => setAgentMenuOpen((v) => !v)}
                    style={{ gap: 4 }}
                  >
                    <span style={{ fontSize: 13 }}>
                      {(() => {
                        const ap = agentProfiles.find(p => p.id === selectedAgent);
                        return ap ? `${ap.icon} ${ap.name}` : t("chat.agentDefault");
                      })()}
                    </span>
                    <IconChevronDown size={12} />
                  </button>
                  {agentMenuOpen && (
                    <div className="chatModelMenu" style={{ minWidth: 220 }}>
                      {!agentProfiles.some(p => p.id === "default") && (
                        <div
                          key="__default__"
                          className={`chatModelMenuItem ${selectedAgent === "default" ? "chatModelMenuItemActive" : ""}`}
                          onClick={() => { setSelectedAgent("default"); setAgentMenuOpen(false); }}
                        >
                          <span style={{ marginRight: 6 }}>🎯</span>
                          <span style={{ fontWeight: 600 }}>{t("chat.agentDefault")}</span>
                        </div>
                      )}
                      {agentProfiles.map((ap) => (
                        <div
                          key={ap.id}
                          className={`chatModelMenuItem ${selectedAgent === ap.id ? "chatModelMenuItemActive" : ""}`}
                          onClick={() => { setSelectedAgent(ap.id); setAgentMenuOpen(false); }}
                        >
                          <span style={{ marginRight: 6 }}>{ap.icon}</span>
                          <span style={{ fontWeight: 600 }}>{ap.name}</span>
                          <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{ap.description}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {/* Org mode selector */}
              {multiAgentEnabled && orgList.length > 0 && (
                <div ref={orgMenuRef} style={{ position: "relative", marginLeft: 8 }}>
                  <button
                    className="chatModelPickerBtn"
                    onClick={() => {
                      if (orgMode) {
                        setOrgMode(false);
                        setSelectedOrgId(null);
                        setOrgMenuOpen(false);
                      } else {
                        setOrgMenuOpen((v) => !v);
                      }
                    }}
                    style={{
                      gap: 4,
                      background: orgMode ? "rgba(14,165,233,0.15)" : undefined,
                      borderColor: orgMode ? "var(--primary)" : undefined,
                    }}
                  >
                    <span style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 4 }}>
                      <IconBuilding size={13} />
                      {orgMode && selectedOrgId
                        ? (() => { const o = orgList.find(x => x.id === selectedOrgId); return o ? o.name : "组织"; })()
                        : "组织"}
                    </span>
                    {orgMode ? <IconX size={10} /> : <IconChevronDown size={12} />}
                  </button>
                  {orgMenuOpen && (
                    <div className="chatModelMenu" style={{ minWidth: 200 }}>
                      {orgList.map((o) => (
                        <div
                          key={o.id}
                          className={`chatModelMenuItem ${selectedOrgId === o.id ? "chatModelMenuItemActive" : ""}`}
                          onClick={() => {
                            setOrgMode(true);
                            setSelectedOrgId(o.id);
                            setOrgMenuOpen(false);
                          }}
                        >
                          <IconBuilding size={13} style={{ marginRight: 4, flexShrink: 0 }} />
                          <span style={{ fontWeight: 600 }}>{o.name}</span>
                          <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{o.status}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Org mode hint bar */}
            {orgMode && selectedOrgId && (
              <div style={{
                fontSize: 11, color: "var(--primary)", padding: "4px 8px",
                background: "rgba(14,165,233,0.08)", borderRadius: 6, marginBottom: 4,
                display: "flex", alignItems: "center", gap: 6,
              }}>
                <IconBuilding size={12} />
                正在与「{orgList.find(o => o.id === selectedOrgId)?.name}」{selectedOrgNodeId ? ` / ${selectedOrgNodeId}` : ""}对话
                {selectedOrgNodeId && (
                  <button
                    onClick={() => setSelectedOrgNodeId(null)}
                    style={{
                      background: "none", border: "none", cursor: "pointer",
                      color: "var(--muted)", fontSize: 10, padding: "0 2px",
                      display: "flex", alignItems: "center",
                    }}
                    title="取消节点指定，改为与整个组织对话"
                  >
                    <IconX size={10} />
                  </button>
                )}
                {orgCommandPending && <span style={{ opacity: 0.6 }}> — 组织协调中，进度实时显示 ↓</span>}
              </div>
            )}

            {/* Textarea */}
            <textarea
              ref={inputRef}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              onPaste={handlePaste}
              placeholder={orgCommandPending ? "组织正在处理中..." : orgMode ? (selectedOrgNodeId ? `输入指令发送给 ${selectedOrgNodeId}...` : "输入指令发送给组织...") : isCurrentConvStreaming ? t("chat.queueHint") : chatMode === "plan" ? `Plan ${t("chat.planMode")}` : chatMode === "ask" ? "Ask Mode - read only" : t("chat.placeholder")}
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
                <button data-slot="toolbar" onClick={() => fileInputRef.current?.click()} className="chatInputIconBtn" title={t("chat.attach")}>
                  <IconPaperclip size={16} />
                </button>
                <input ref={fileInputRef} type="file" multiple accept="image/*,video/*,audio/*,.pdf,.txt,.md,.py,.js,.ts,.json,.csv" style={{ display: "none" }} onChange={handleFileSelect} />

                <button data-slot="toolbar" onClick={toggleRecording} className={`chatInputIconBtn ${isRecording ? "chatInputIconBtnDanger" : ""}`} title={isRecording ? t("chat.stopRecording") : t("chat.voice")}>
                  {isRecording ? <IconStopCircle size={16} /> : <IconMic size={16} />}
                </button>

                <div ref={modeMenuRef} style={{ position: "relative", display: "inline-flex" }}>
                  <button
                    data-slot="toolbar"
                    onClick={() => setModeMenuOpen((v) => !v)}
                    className={`chatInputIconBtn ${chatMode !== "agent" ? "chatInputIconBtnActive" : ""}`}
                    title={chatMode === "agent" ? "Agent Mode" : chatMode === "plan" ? "Plan Mode" : "Ask Mode"}
                  >
                    {{ agent: <IconBot size={16} />, plan: <IconPlan size={16} />, ask: <IconSearch size={16} /> }[chatMode]}
                    <span style={{ fontSize: 11, marginLeft: 2 }}>
                      {chatMode === "agent" ? "Agent" : chatMode === "plan" ? "Plan" : "Ask"}
                    </span>
                    <IconChevronDown size={10} style={{ marginLeft: 2, opacity: 0.5 }} />
                  </button>
                  {modeMenuOpen && (
                    <div className="chatModeMenu">
                      <div className="chatModeMenuSection">执行模式</div>
                      {([
                        { key: "agent" as const, icon: <IconBot size={14} />, label: "Agent", desc: "直接执行任务" },
                        { key: "plan" as const, icon: <IconPlan size={14} />, label: "Plan", desc: "先规划再执行" },
                        { key: "ask" as const, icon: <IconSearch size={14} />, label: "Ask", desc: "只读分析模式" },
                      ]).map((m) => (
                        <div
                          key={m.key}
                          className={`chatModeMenuItem ${chatMode === m.key ? "chatModeMenuItemActive" : ""}`}
                          onClick={() => { setChatMode(m.key); setModeMenuOpen(false); }}
                        >
                          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            {m.icon}
                            <span style={{ fontWeight: 600 }}>{m.label}</span>
                          </span>
                          <span style={{ fontSize: 10, opacity: 0.5 }}>{m.desc}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* 深度思考按钮 + 下拉菜单 */}
                <div ref={thinkingMenuRef} style={{ position: "relative", display: "inline-flex" }}>
                  <button
                    data-slot="toolbar"
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
                {isCurrentConvStreaming || orgCommandPending ? (
                  hasInputText && !orgCommandPending ? (
                    <button
                      data-slot="queue"
                      onClick={handleQueueMessage}
                      className="chatInputSendBtn"
                      title={t("chat.queueHint")}
                    >
                      <IconSend size={14} />
                    </button>
                  ) : (
                    <button
                      data-slot="stop"
                      onClick={orgCommandPending ? undefined : handleCancelTask}
                      className={`chatInputSendBtn ${orgCommandPending ? "" : "chatInputStopBtn"}`}
                      title={orgCommandPending ? "组织处理中..." : t("chat.stopGeneration")}
                      disabled={orgCommandPending}
                      style={orgCommandPending ? { opacity: 0.5, cursor: "wait" } : undefined}
                    >
                      {orgCommandPending ? <IconSend size={14} /> : <IconStop size={14} />}
                    </button>
                  )
                ) : (
                  <button
                    data-slot="send"
                    onClick={() => sendMessage()}
                    className="chatInputSendBtn"
                    disabled={!hasInputText && pendingAttachments.length === 0}
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

      {/* Cursor-style right sidebar — conversations */}
      {sidebarOpen && (
        <>
        {typeof window !== "undefined" && window.innerWidth <= 768 && (
          <div className="sidebarOverlay" style={{ zIndex: 1000 }} onClick={() => setSidebarOpen(false)} />
        )}
        <div className={`convSidebar${typeof window !== "undefined" && window.innerWidth <= 768 ? " convSidebarMobileOpen" : ""}`}>
          <div className="convSidebarHeader">
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div className="convSearchBox" style={{ flex: 1 }}>
                <IconSearch size={13} style={{ opacity: 0.4, flexShrink: 0 }} />
                <input
                  data-slot="search"
                  className="convSearchInput"
                  placeholder={t("chat.searchConversations") || "搜索会话..."}
                  value={convSearchQuery}
                  onChange={(e) => setConvSearchQuery(e.target.value)}
                />
                {convSearchQuery && (
                  <button data-slot="clear" className="convSearchClear" onClick={() => setConvSearchQuery("")}>
                    <IconX size={11} />
                  </button>
                )}
              </div>
              <button
                data-slot="pin"
                className="convPinBtn"
                onClick={() => {
                  const next = !sidebarPinned;
                  setSidebarPinned(next);
                  try { localStorage.setItem("openakita_convSidebarPinned", String(next)); } catch {}
                }}
                title={sidebarPinned ? (t("chat.unpinSidebar") || "取消固定") : (t("chat.pinSidebar") || "固定会话列表")}
                style={{ color: sidebarPinned ? "var(--brand, #2563eb)" : "var(--muted2, #999)" }}
              >
                <IconPin size={14} />
              </button>
            </div>
            <button data-slot="new-chat" className="convNewBtn" onClick={newConversation}>
              {t("chat.newConversation")}
            </button>
          </div>

          <div className="convSidebarList">
            {pinnedConvs.length > 0 && (
              <>
                <div className="convSectionLabel">{t("chat.pinnedSection")}</div>
                {pinnedConvs.map(renderConvItem)}
              </>
            )}

            {agentConvs.length > 0 && (
              <>
                <div className="convSectionLabel">{t("chat.conversationsLabel") || "会话"}</div>
                {agentConvs.map(renderConvItem)}
              </>
            )}

            {filteredConversations.length === 0 && (
              <div className="convEmpty">
                {convSearchQuery ? t("common.noResults") || "无结果" : t("common.noData")}
              </div>
            )}
          </div>
        </div>
        </>
      )}

      {/* Orbit tooltip — portal to body to escape overflow:hidden */}
      {orbitTip && createPortal(
        <div className="agentOrbitTooltip agentOrbitTooltipVisible" style={{ left: orbitTip.x, top: orbitTip.y }}>
          <span className="agentOrbitTooltipName">{orbitTip.name}</span>
          <span className="agentOrbitTooltipTitle">{orbitTip.title}</span>
        </div>,
        document.body,
      )}

      {/* Image lightbox overlay */}
      {lightbox && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 99999,
            background: "rgba(0,0,0,0.85)", backdropFilter: "blur(8px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            cursor: "zoom-out",
          }}
          onClick={() => setLightbox(null)}
        >
          <img
            src={lightbox.url}
            alt={lightbox.name}
            style={{
              maxWidth: Math.max(winSize.w - 80, 200),
              maxHeight: Math.max(winSize.h - 80, 200),
              borderRadius: 8, objectFit: "contain",
              boxShadow: "0 8px 48px rgba(0,0,0,0.5)",
              cursor: "default",
              transition: "max-width 0.2s, max-height 0.2s",
            }}
            onClick={(e) => e.stopPropagation()}
          />
          <div style={{
            position: "absolute", top: 16, right: 16,
            display: "flex", gap: 8,
          }}>
            <button
              title={t("chat.downloadImage") || "保存图片"}
              style={{
                background: "rgba(255,255,255,0.25)", color: "#fff",
                border: "1px solid rgba(255,255,255,0.35)", borderRadius: 8,
                backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)",
                width: 40, height: 40,
                display: "flex", alignItems: "center", justifyContent: "center",
                cursor: "pointer", transition: "background 0.15s",
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "rgba(255,255,255,0.4)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "rgba(255,255,255,0.25)"; }}
              onClick={async (e) => {
                e.stopPropagation();
                try {
                  const savedPath = await downloadFile(lightbox.url, lightbox.name || `image-${Date.now()}.png`);
                  await showInFolder(savedPath);
                } catch (err) {
                  logger.error("Chat", "图片下载失败", { error: String(err) });
                }
              }}
            >
              <IconDownload size={18} />
            </button>
            <button
              style={{
                background: "rgba(255,255,255,0.25)", color: "#fff",
                border: "1px solid rgba(255,255,255,0.35)", borderRadius: 8,
                backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)",
                width: 40, height: 40,
                display: "flex", alignItems: "center", justifyContent: "center",
                cursor: "pointer", transition: "background 0.15s",
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "rgba(255,255,255,0.4)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "rgba(255,255,255,0.25)"; }}
              onClick={(e) => { e.stopPropagation(); setLightbox(null); }}
            >
              <IconX size={18} />
            </button>
          </div>
        </div>
      )}
      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
      {securityConfirm && createPortal(
        <SecurityConfirmModal
          data={securityConfirm}
          apiBase={apiBaseUrl}
          onClose={() => {
            if (securityTimerRef.current) clearInterval(securityTimerRef.current);
            setSecurityConfirm(null);
          }}
          timerRef={securityTimerRef}
          setData={setSecurityConfirm}
        />,
        document.body,
      )}
    </div>
  );
}
