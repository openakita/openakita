// ─── ChatView 纯函数工具 & 常量 ───

import type {
  ChatMessage,
  ChatAskUser,
  ChatAskQuestion,
  ChatErrorInfo,
  ChatArtifact,
  ChatSource,
  ChatMcpCall,
  ChatTodo,
  ChatAttachment,
  MessagePart,
  ChainGroup,
  ChainEntry,
  ChainToolCall,
  ChainSummaryItem,
  ChainTimelineGroup,
} from "./chatTypes";
import { IS_TAURI, logger, saveFileDialog, writeTextFile } from "../../../platform";
import { getAccessToken } from "../../../platform/auth";

// ── 持久化 Key 常量 ──
// Legacy (pre-workspace-isolation) global keys — kept as defaults for hooks that
// don't yet thread workspaceId through, and as the source for one-time migration
// in ChatView's workspace-change effect.

export const STORAGE_KEY_CONVS = "chat_conversations";
export const STORAGE_KEY_ACTIVE = "chat_activeConvId";
export const STORAGE_KEY_MSGS_PREFIX = "chat_msgs_";

/** Workspace-scoped storage keys. Pass `null` to get the legacy global keys. */
export function getWorkspaceStorageKeys(workspaceId: string | null | undefined): {
  CONVS: string;
  ACTIVE: string;
  MSGS_PREFIX: string;
} {
  if (!workspaceId) {
    return {
      CONVS: STORAGE_KEY_CONVS,
      ACTIVE: STORAGE_KEY_ACTIVE,
      MSGS_PREFIX: STORAGE_KEY_MSGS_PREFIX,
    };
  }
  return {
    CONVS: `chat_conversations_${workspaceId}`,
    ACTIVE: `chat_activeConvId_${workspaceId}`,
    MSGS_PREFIX: `chat_msgs_${workspaceId}_`,
  };
}

// ── 行为阈值常量 ──

export const IDLE_THRESHOLD_MS = 75 * 60 * 1000; // 75 minutes
export const IDLE_TOKEN_THRESHOLD = 50_000;
export const PASTE_CHAR_THRESHOLD = 800;
export const UNDO_MAX_STEPS = 50;

// ── 加载状态轮播提示 ──

const _spinnerTips = [
  "Tip: 按 Ctrl+/ 查看所有快捷键",
  "Tip: 输入 / 可以使用斜杠命令",
  "Tip: 拖拽文件到输入框可以上传附件",
  "Tip: Ctrl+F 搜索聊天记录",
  "Tip: 输入 @agent名 快速切换 Agent",
  "Tip: 使用 /clear 清空当前会话上下文",
  "Tip: 使用 /memory 管理 AI 记忆",
  "Tip: 长按 Shift+Enter 可以换行输入",
];
let _tipShowCounts: number[] = new Array(_spinnerTips.length).fill(0);

export function getNextSpinnerTip(): string {
  const minCount = Math.min(..._tipShowCounts);
  const candidates = _tipShowCounts
    .map((c, i) => (c === minCount ? i : -1))
    .filter((i) => i >= 0);
  const idx = candidates[Math.floor(Math.random() * candidates.length)];
  _tipShowCounts[idx]++;
  return _spinnerTips[idx];
}

// ── Error Card 元数据 ──

export const ERROR_META: Record<string, { icon: string; color: string; hint: string }> = {
  auth: { icon: "key", color: "#ef4444", hint: "请检查 API Key 配置" },
  quota: { icon: "chart", color: "#f59e0b", hint: "请稍后重试或升级配额" },
  timeout: { icon: "clock", color: "#f59e0b", hint: "可尝试简化问题后重试" },
  content_filter: {
    icon: "shield",
    color: "#8b5cf6",
    hint: "云端模型的内容安全审核未通过。可尝试：① 输入 /clear 清空当前对话后重新开始；② 换一种表述；③ 切换到对内容审核更宽松的模型端点。",
  },
  network: { icon: "globe", color: "#f59e0b", hint: "请检查网络连接" },
  server: { icon: "warn", color: "#ef4444", hint: "服务暂时不可用，请稍后重试" },
  unknown: { icon: "error", color: "#ef4444", hint: "" },
};

// ── SVG icon paths ──

export const SVG_PATHS: Record<string, string> = {
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

// ── 对话导出 ──

export async function exportConversation(
  msgs: ChatMessage[],
  title: string,
  format: "md" | "json",
): Promise<boolean> {
  let content: string;
  let mimeType: string;
  let ext: string;
  if (format === "json") {
    content = JSON.stringify(msgs.map(({ streaming, streamStatus, streamFallback, ...rest }) => rest), null, 2);
    mimeType = "application/json";
    ext = "json";
  } else {
    const lines: string[] = [`# ${title}`, "", `> 导出时间: ${new Date().toLocaleString()}`, ""];
    for (const msg of msgs) {
      const role = msg.role === "user" ? "[User] 用户" : msg.role === "assistant" ? "[AI] 助手" : "[Sys] 系统";
      lines.push(`## ${role}`, "");
      if (msg.content) lines.push(msg.content, "");
      if (msg.toolCalls?.length) {
        lines.push("**工具调用:**", "");
        for (const tc of msg.toolCalls) {
          lines.push(`- \`${tc.tool}\`: ${JSON.stringify(tc.args).slice(0, 200)}`);
        }
        lines.push("");
      }
      lines.push("---", "");
    }
    content = lines.join("\n");
    mimeType = "text/markdown";
    ext = "md";
  }
  const filename = `${title.replace(/[/\\?%*:|"<>]/g, "_").slice(0, 50)}.${ext}`;
  logger.info("Chat.Export", "start", { format: ext, msgCount: msgs.length });

  try {
    if (IS_TAURI) {
      const savePath = await saveFileDialog({
        title: "导出会话",
        defaultPath: filename,
        filters: [{ name: format === "json" ? "JSON" : "Markdown", extensions: [ext] }],
      });
      if (!savePath) {
        logger.info("Chat.Export", "cancelled", { format: ext });
        return false;
      }
      await writeTextFile(savePath, content);
      logger.info("Chat.Export", "success", { format: ext, path: savePath });
      return true;
    }

    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    logger.info("Chat.Export", "success", { format: ext });
    return true;
  } catch (error) {
    logger.error("Chat.Export", "error", { format: ext, error: String(error) });
    throw error;
  }
}

// ── Auth token helper ──

export function appendAuthToken(url: string): string {
  if (IS_TAURI) return url;
  const token = getAccessToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

// ── 消息内容处理 ──

export function stripLegacySummary(content: string): string {
  if (!content) return content;
  const markers = ["\n\n[子Agent工作总结]", "\n\n[执行摘要]"];
  for (const m of markers) {
    const idx = content.indexOf(m);
    if (idx !== -1) content = content.substring(0, idx);
  }
  if (content.startsWith("[执行摘要]") || content.startsWith("[子Agent工作总结]")) return "";
  return content;
}

// ── 持久化：消息序列化 / 反序列化 ──

export const STORED_MESSAGE_WINDOW = 120;

export function sanitizeStoredMessages(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((m): m is ChatMessage => {
    if (!m || typeof m !== "object") return false;
    if (typeof m.id !== "string" || !m.id) return false;
    if (m.role !== "user" && m.role !== "assistant" && m.role !== "system") return false;
    if (typeof m.content !== "string") return false;
    if (typeof m.timestamp !== "number") return false;
    return true;
  }).map((m) => {
    // NOTE: streamFallback is intentionally preserved here (unlike streaming /
    // streamStatus). It marks a finalized-but-provisional bubble whose text came
    // from an interrupted stream; it must survive reload / window switch so the
    // next backend reconciliation can replace the text and clear it. It is a
    // tiny boolean and self-clears on the first successful patch.
    const cleaned = { ...m, streaming: undefined, streamStatus: undefined };
    if (
      m.role === "assistant" &&
      (!m.content || m.content.trim() === "") &&
      !m.toolCalls?.length &&
      !m.todo &&
      !m.askUser &&
      !m.artifacts?.length &&
      !m.parts?.length
    ) {
      return null;
    }
    return cleaned;
  }).filter(Boolean) as ChatMessage[];
}

export function loadMessagesFromStorage(key: string): ChatMessage[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return sanitizeStoredMessages(parsed);
  } catch {
    return [];
  }
}

export function saveMessagesToStorage(key: string, msgs: ChatMessage[], maxMessages = STORED_MESSAGE_WINDOW): boolean {
  const windowed = maxMessages > 0 && msgs.length > maxMessages ? msgs.slice(-maxMessages) : msgs;
  // streamFallback is kept on purpose (see sanitizeStoredMessages) so an
  // interrupted bubble stays flagged across reloads until reconciled.
  const base = windowed.map(({
    streaming: _streaming,
    streamStatus: _streamStatus,
    parts: _parts,
    ...rest
  }) => rest);
  try {
    localStorage.setItem(key, JSON.stringify(base));
    return true;
  } catch {
    const slim = windowed.map(({
      streaming: _streaming,
      streamStatus: _streamStatus,
      thinkingChain: _thinkingChain,
      parts: _parts,
      ...rest
    }) => rest);
    try {
      localStorage.setItem(key, JSON.stringify(slim));
      return true;
    } catch {
      return false;
    }
  }
}

export function shouldRenderConversationMessages(
  conversationId: string | null | undefined,
  activeConversationId: string | null | undefined,
): boolean {
  return Boolean(conversationId) && conversationId === activeConversationId;
}

function latestMessageTimestamp(msgs: ChatMessage[]): number {
  return msgs.reduce((max, msg) => Math.max(max, Number.isFinite(msg.timestamp) ? msg.timestamp : 0), 0);
}

function attachmentSignature(attachments: ChatAttachment[] | null | undefined): string {
  if (!attachments?.length) return "";
  return attachments.map((att) => [
    att.type,
    att.name,
    att.url,
    att.localPath,
    att.uploadId,
    att.previewUrl,
    att.size,
    att.mimeType,
  ].map((value) => String(value ?? "")).join("\u001f")).join("\u001e");
}

function messageSignature(msg: ChatMessage | undefined): string {
  if (!msg) return "";
  return `${msg.role}\n${msg.timestamp}\n${msg.content}\n${attachmentSignature(msg.attachments)}`;
}

function firstUserContent(msgs: ChatMessage[]): string {
  return msgs.find((msg) => msg.role === "user")?.content.trim() ?? "";
}

function removeAdjacentDuplicateUserMessages(msgs: ChatMessage[]): ChatMessage[] {
  let changed = false;
  const deduped: ChatMessage[] = [];
  for (const msg of msgs) {
    const prev = deduped[deduped.length - 1];
    if (
      msg.role === "user" &&
      prev?.role === "user" &&
      prev.content === msg.content &&
      attachmentSignature(prev.attachments) === attachmentSignature(msg.attachments)
    ) {
      changed = true;
      continue;
    }
    deduped.push(msg);
  }
  return changed ? deduped : msgs;
}

function messageMatchKey(msg: Pick<ChatMessage, "role" | "content">): string {
  return `${msg.role}\n${msg.content}`;
}

function backendMessageMatchKey(msg: Pick<BackendHistoryMessage, "role" | "content">): string {
  return `${msg.role}\n${msg.content}`;
}

function mergeMissingAttachments(primary: ChatMessage[], secondary: ChatMessage[]): ChatMessage[] {
  const withAttachments = new Map<string, ChatMessage[]>();
  for (const msg of secondary) {
    if (!msg.attachments?.length) continue;
    const key = messageMatchKey(msg);
    const queue = withAttachments.get(key);
    if (queue) queue.push(msg);
    else withAttachments.set(key, [msg]);
  }

  let changed = false;
  const merged = primary.map((msg) => {
    if (msg.attachments?.length) return msg;
    const queue = withAttachments.get(messageMatchKey(msg));
    const source = queue?.shift();
    if (!source?.attachments?.length) return msg;
    changed = true;
    return { ...msg, attachments: source.attachments };
  });
  return changed ? merged : primary;
}

/**
 * Choose which message history should hydrate the UI.
 *
 * Backend history is the source of truth after SSE disconnect recovery. Local
 * messages can still contain structured fields that the backend has not seen
 * yet during an in-flight turn, so hydration merges missing attachments before
 * choosing the history to render.
 */
export function chooseHydratedMessages(localMsgs: ChatMessage[], backendMsgs: ChatMessage[]): ChatMessage[] {
  const cleanBackend = removeAdjacentDuplicateUserMessages(backendMsgs);
  const cleanLocal = removeAdjacentDuplicateUserMessages(localMsgs);
  const backendWithLocalAttachments = mergeMissingAttachments(cleanBackend, cleanLocal);
  if (backendWithLocalAttachments.length === 0) return cleanLocal;
  if (cleanLocal.length === 0) return cleanBackend;

  const localFirstUser = firstUserContent(cleanLocal);
  const backendFirstUser = firstUserContent(backendWithLocalAttachments);
  if (localFirstUser && backendFirstUser && localFirstUser !== backendFirstUser) {
    return backendWithLocalAttachments;
  }

  const patchedLocal = removeAdjacentDuplicateUserMessages(
    mergeMissingAttachments(patchMessagesWithBackend(cleanLocal, backendWithLocalAttachments), backendWithLocalAttachments),
  );

  if (backendWithLocalAttachments.length > cleanLocal.length) return backendWithLocalAttachments;
  if (cleanLocal.length > cleanBackend.length) return patchedLocal;
  if (patchedLocal !== cleanLocal) return patchedLocal;

  const localLatest = latestMessageTimestamp(cleanLocal);
  const backendLatest = latestMessageTimestamp(backendWithLocalAttachments);
  if (backendLatest > localLatest) return backendWithLocalAttachments;
  if (localLatest > backendLatest) return cleanLocal;

  const localLast = messageSignature(cleanLocal[cleanLocal.length - 1]);
  const backendLast = messageSignature(backendWithLocalAttachments[backendWithLocalAttachments.length - 1]);
  return backendLast && backendLast !== localLast ? backendWithLocalAttachments : cleanLocal;
}

// ── 思维链 ──

export function buildChainFromSummary(summary: ChainSummaryItem[]): ChainGroup[] {
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
      toolCalls: s.tools.map((t: { name: string; input_preview: string; result_preview?: string }) => ({
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

/**
 * Restore the causal reasoning chain from the backend's persisted
 * ``chain_timeline`` (the server mirror of the live ``ChainGroup.entries``
 * assembly). Preferred over ``buildChainFromSummary`` because it preserves
 * narration text, tool arguments, and the true text/tool ordering — the
 * detail the lossy summary drops. Entries are coerced defensively since they
 * arrive as untyped JSON.
 */
export function buildChainFromTimeline(timeline: ChainTimelineGroup[]): ChainGroup[] {
  return timeline.map((g, gi) => {
    const entries = coerceTimelineEntries((g as { entries?: unknown }).entries);
    return {
      iteration: typeof g.iteration === "number" ? g.iteration : gi,
      entries,
      ...(typeof g.durationMs === "number" ? { durationMs: g.durationMs } : {}),
      hasThinking: entries.some((e) => e.kind === "thinking"),
      collapsed: true,
      toolCalls: toolCallsFromChainEntries(entries),
    };
  });
}

function coerceTimelineEntries(raw: unknown): ChainEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: ChainEntry[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const kind = (item as { kind?: unknown }).kind;
    const r = item as Record<string, unknown>;
    switch (kind) {
      case "thinking":
        out.push({ kind: "thinking", content: String(r.content ?? "") });
        break;
      case "text":
        out.push({ kind: "text", content: String(r.content ?? ""), ...(r.icon ? { icon: String(r.icon) } : {}) });
        break;
      case "tool_start":
        out.push({
          kind: "tool_start",
          toolId: String(r.toolId ?? ""),
          tool: String(r.tool ?? ""),
          args: r.args && typeof r.args === "object" ? (r.args as Record<string, unknown>) : {},
          description: String(r.description ?? ""),
          status: r.status === "done" || r.status === "error" ? r.status : "running",
        });
        break;
      case "tool_end":
        out.push({
          kind: "tool_end",
          toolId: String(r.toolId ?? ""),
          tool: String(r.tool ?? ""),
          result: String(r.result ?? ""),
          status: r.status === "error" ? "error" : "done",
        });
        break;
      case "config_hint": {
        const rawHint = r.hint && typeof r.hint === "object" ? r.hint as Record<string, unknown> : {};
        const rawActions = rawHint.actions;
        const actions = Array.isArray(rawActions)
          ? rawActions
              .filter((action): action is Record<string, unknown> => !!action && typeof action === "object")
              .map((action) => ({ ...action }))
          : undefined;
        out.push({
          kind: "config_hint",
          toolId: String(r.toolId ?? ""),
          hint: {
            scope: String(rawHint.scope ?? ""),
            error_code: (
              rawHint.error_code === "missing_credential" ||
              rawHint.error_code === "auth_failed" ||
              rawHint.error_code === "rate_limited" ||
              rawHint.error_code === "network_unreachable" ||
              rawHint.error_code === "content_filter" ||
              rawHint.error_code === "unknown"
                ? rawHint.error_code
                : "unknown"
            ),
            title: String(rawHint.title ?? ""),
            ...(rawHint.message != null ? { message: String(rawHint.message) } : {}),
            ...(actions ? { actions } : {}),
          },
        });
        break;
      }
      case "compressed":
        out.push({
          kind: "compressed",
          beforeTokens: Number(r.beforeTokens ?? 0),
          afterTokens: Number(r.afterTokens ?? 0),
        });
        break;
      default:
        break;
    }
  }
  return out;
}

/** Derive the backward-compat ChainToolCall[] (IM views etc.) from chain entries. */
function toolCallsFromChainEntries(entries: ChainEntry[]): ChainToolCall[] {
  const byId = new Map<string, ChainToolCall>();
  const order: string[] = [];
  for (const e of entries) {
    if (e.kind === "tool_start") {
      const key = e.toolId || `${e.tool}-${order.length}`;
      if (!byId.has(key)) order.push(key);
      byId.set(key, {
        toolId: e.toolId,
        tool: e.tool,
        args: e.args,
        status: e.status === "done" || e.status === "error" ? e.status : "running",
        description: e.description,
      });
    } else if (e.kind === "tool_end") {
      const key = e.toolId || order[order.length - 1];
      const prev = key ? byId.get(key) : undefined;
      if (prev) {
        prev.result = e.result;
        prev.status = e.status;
      } else {
        const k = e.toolId || `${e.tool}-${order.length}`;
        order.push(k);
        byId.set(k, { toolId: e.toolId, tool: e.tool, args: {}, result: e.result, status: e.status, description: "" });
      }
    }
  }
  return order.map((k) => byId.get(k)).filter((x): x is ChainToolCall => !!x);
}

export function basename(path: string): string {
  if (!path) return "";
  return path.replace(/\\/g, "/").split("/").pop() || path;
}

export function formatToolDescription(tool: string, args: Record<string, unknown>): string {
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

export function generateGroupSummary(tools: ChainToolCall[]): string {
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

// ── ask_user 回答格式化 ──

export function formatAskUserAnswer(answer: string, askUser: ChatAskUser): string {
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
          return q.options?.find((o: { id: string; label: string }) => o.id === v)?.label ?? v;
        });
        return `${q.prompt}: ${labels.join(", ")}`;
      }).filter(Boolean).join(" | ");
      if (formatted) return formatted;
    }
  } catch { /* not JSON */ }
  const options = askUser.options || questions[0]?.options;
  const opt = options?.find((o: { id: string; label: string }) => o.id === answer);
  if (opt) return opt.label;
  if (answer.includes(",") && options) {
    const ids = answer.split(",");
    if (ids.every((id: string) => id.startsWith("OTHER:") || options.some((o: { id: string; label: string }) => o.id === id))) {
      return ids.map((id: string) => {
        if (id.startsWith("OTHER:")) return id.slice(6);
        return options.find((o: { id: string; label: string }) => o.id === id)?.label ?? id;
      }).join(", ");
    }
  }
  return answer;
}

// ── 后端数据修补 ──

type BackendHistoryMessage = {
  id?: string;
  index?: number;
  role: string;
  content: string;
  chain_summary?: ChainSummaryItem[];
  chain_timeline?: ChainTimelineGroup[];
  artifacts?: ChatArtifact[] | null;
  attachments?: ChatAttachment[] | null;
  sources?: ChatSource[] | null;
  mcp_calls?: ChatMcpCall[] | null;
  usage?: ChatMessage["usage"];
  todo?: ChatTodo | null;
  ask_user?: ChatAskUser | null;
  parts?: MessagePart[] | null;
};

export type BackendPatchStats = {
  matchedByHistoryIndex: number;
  matchedById: number;
  matchedByFallback: number;
  patched: number;
};

export type BackendPatchResult = {
  messages: ChatMessage[];
  changed: boolean;
  stats: BackendPatchStats;
};

export function patchMessagesWithBackendDetailed(
  localMsgs: ChatMessage[],
  backendMsgs: BackendHistoryMessage[],
): BackendPatchResult {
  const backendAssistant = backendMsgs.filter((m) => m.role === "assistant");
  const backendByHistoryIndex = new Map<number, BackendHistoryMessage>();
  const backendById = new Map<string, BackendHistoryMessage>();
  backendAssistant.forEach((m) => {
    if (typeof m.index === "number") backendByHistoryIndex.set(m.index, m);
    if (m.id) backendById.set(m.id, m);
  });
  const usedBackendMessages = new Set<BackendHistoryMessage>();
  const stats: BackendPatchStats = {
    matchedByHistoryIndex: 0,
    matchedById: 0,
    matchedByFallback: 0,
    patched: 0,
  };

  const lastLocalAssistantIndex = localMsgs.reduce(
    (last, m, index) => (m.role === "assistant" ? index : last),
    -1,
  );

  const claimBackendForLocalMessage = (m: ChatMessage, localIndex: number): BackendHistoryMessage | undefined => {
    if (typeof m.historyIndex === "number") {
      const indexed = backendByHistoryIndex.get(m.historyIndex);
      if (indexed && !usedBackendMessages.has(indexed)) {
        usedBackendMessages.add(indexed);
        stats.matchedByHistoryIndex += 1;
        return indexed;
      }
    }

    const byId = backendById.get(m.id);
    if (byId && !usedBackendMessages.has(byId)) {
      usedBackendMessages.add(byId);
      stats.matchedById += 1;
      return byId;
    }

    if (localIndex !== lastLocalAssistantIndex) {
      return undefined;
    }

    for (let i = backendAssistant.length - 1; i >= 0; i -= 1) {
      const candidate = backendAssistant[i];
      if (!usedBackendMessages.has(candidate)) {
        usedBackendMessages.add(candidate);
        stats.matchedByFallback += 1;
        return candidate;
      }
    }
    return undefined;
  };

  const backendAttachmentsByMessage = new Map<string, BackendHistoryMessage[]>();
  for (const msg of backendMsgs) {
    if (!msg.attachments?.length) continue;
    const key = backendMessageMatchKey(msg);
    const queue = backendAttachmentsByMessage.get(key);
    if (queue) queue.push(msg);
    else backendAttachmentsByMessage.set(key, [msg]);
  }

  const claimBackendAttachmentsForLocalMessage = (
    m: ChatMessage,
  ): BackendHistoryMessage | undefined => {
    const queue = backendAttachmentsByMessage.get(messageMatchKey(m));
    return queue?.shift();
  };

  let changed = false;
  const patched = localMsgs.map((m, index) => {
    if (m.role !== "assistant") {
      if (!m.attachments?.length) {
        const backend = claimBackendAttachmentsForLocalMessage(m);
        if (backend?.attachments?.length) {
          changed = true;
          stats.patched += 1;
          return { ...m, attachments: backend.attachments };
        }
      }
      return m;
    }
    const backend = claimBackendForLocalMessage(m, index);
    if (!backend) return m;

    const patches: Partial<ChatMessage> = {};

    // Content replacement. Normally we only upgrade to the backend copy when it
    // is *longer* (the local stream got cut off). But a message flagged
    // `streamFallback` was finalized from an interrupted / recovering stream, so
    // its text is untrustworthy — adopt the authoritative persisted answer even
    // when the backend copy is shorter (e.g. trace markers stripped on save),
    // then clear the flag so it isn't force-replaced again.
    if (
      backend.content &&
      !m.askUser &&
      (m.streamFallback || !m.content || m.content.length < backend.content.length)
    ) {
      patches.content = backend.content;
      if (m.streamFallback) patches.streamFallback = undefined;
    }

    const hasBrokenChain = m.thinkingChain?.some((g: ChainGroup) => !g.entries.length && !g.durationMs);
    if (!m.thinkingChain?.length || hasBrokenChain) {
      // Prefer the faithful timeline; fall back to the lossy summary for
      // messages persisted before chain_timeline existed.
      if (backend.chain_timeline?.length) {
        patches.thinkingChain = buildChainFromTimeline(backend.chain_timeline);
      } else if (backend.chain_summary?.length) {
        patches.thinkingChain = buildChainFromSummary(backend.chain_summary);
      }
    }

    if (m.thinkingChain && !patches.thinkingChain) {
      const cleaned = m.thinkingChain.filter((g: ChainGroup) => g.entries.length > 0 || g.durationMs);
      if (cleaned.length !== m.thinkingChain.length) {
        patches.thinkingChain = cleaned.length > 0 ? cleaned : undefined;
      }
    }

    if (!m.artifacts?.length && backend.artifacts?.length) {
      patches.artifacts = backend.artifacts;
    }

    if (!m.attachments?.length) {
      const attachmentBackend = backend.attachments?.length
        ? backend
        : claimBackendAttachmentsForLocalMessage(m);
      if (attachmentBackend?.attachments?.length) {
        patches.attachments = attachmentBackend.attachments;
      }
    }

    if (!m.sources?.length && backend.sources?.length) {
      patches.sources = backend.sources;
    }

    if (!m.mcpCalls?.length && backend.mcp_calls?.length) {
      patches.mcpCalls = backend.mcp_calls;
    }

    if (!m.usage && backend.usage) {
      patches.usage = backend.usage;
    }

    // Persisted plan snapshot — restore or refresh the plan card when the
    // backend has a newer step state (e.g. after reconnecting to a running task).
    if (
      backend.todo?.steps?.length &&
      (!m.todo?.steps?.length || JSON.stringify(m.todo) !== JSON.stringify(backend.todo))
    ) {
      patches.todo = backend.todo;
    }

    // Persisted answered ask_user — keep a resolved prompt resolved on reload
    // instead of re-rendering it as freshly clickable.
    if (backend.ask_user?.answered && !m.askUser?.answered) {
      patches.askUser = m.askUser
        ? { ...m.askUser, ...backend.ask_user, answered: true, answer: backend.ask_user.answer }
        : backend.ask_user;
    }

    // Authoritative ordered parts projection from the backend.
    if (!m.parts?.length && backend.parts?.length) {
      patches.parts = backend.parts;
    }

    if (Object.keys(patches).length > 0) {
      changed = true;
      stats.patched += 1;
      return { ...m, ...patches };
    }
    return m;
  });
  return { messages: changed ? patched : localMsgs, changed, stats };
}

export function patchMessagesWithBackend(
  localMsgs: ChatMessage[],
  backendMsgs: BackendHistoryMessage[],
): ChatMessage[] {
  return patchMessagesWithBackendDetailed(localMsgs, backendMsgs).messages;
}

// ── 错误分类 ──

export function classifyError(msg: string): ChatErrorInfo["category"] {
  const el = msg.toLowerCase();
  if (el.includes("data_inspection") || el.includes("inappropriate content")) return "content_filter";
  if (el.includes("all endpoints failed") || el.includes("allendpointsfailederror")) {
    if (["api key", "auth", "unauthorized", "401", "forbidden", "403"].some((k) => el.includes(k))) return "auth";
    if (["quota", "rate limit", "429", "余额", "insufficient"].some((k) => el.includes(k))) return "quota";
    return "server";
  }
  if (["api key", "auth", "unauthorized", "401", "forbidden", "403"].some((k) => el.includes(k))) return "auth";
  if (["quota", "rate limit", "429", "余额", "insufficient"].some((k) => el.includes(k))) return "quota";
  if (["timeout", "timed out", "deadline"].some((k) => el.includes(k))) return "timeout";
  if (["connect", "dns", "resolve", "network", "unreachable"].some((k) => el.includes(k))) return "network";
  if (["500", "502", "503", "504", "internal server"].some((k) => el.includes(k))) return "server";
  return "unknown";
}
