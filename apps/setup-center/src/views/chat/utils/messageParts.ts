// ─── 消息 parts 投影 ───
//
// `MessagePart[]` 是助手消息的"有序渲染模型"：富卡片（思维链 / 计划 / 正文 /
// 工具 / 附件 / 已答 ask_user …）按发生顺序排列，作为渲染 + 重载回显的单一来源。
//
// 设计要点（见 docs/TODO.md 与 plan）：
//  - 默认它是扁平字段的"确定性投影"——同一条消息无论来自实时流、localStorage
//    还是后端历史，都能由 `deriveMessageParts` 得到同一份有序结构，从而消除
//    "实时流式通道 vs 扁平持久历史"两条管线漂移（即此前分析中的"旁路"）。
//  - 后端历史接口也会镜像同一 schema（`/api/sessions/{id}/history` → `parts`），
//    `coerceMessageParts` 负责把它规整为前端类型；缺失时回退到本地投影，
//    保证旧会话不白屏。
//  - 重型文本块（text / reasoning / thinking）只作为顺序"标记"，真正的内容仍
//    从消息扁平字段读取，避免 parts 跨线传输时把正文 / 思维链翻倍。

import type {
  ChatMessage,
  MessagePart,
  ChatArtifact,
  ChatTodo,
  ChatAskUser,
  ChatProgressEvent,
} from "../../../types";

const KNOWN_KINDS = new Set<MessagePart["kind"]>([
  "reasoning",
  "thinking",
  "org_timeline",
  "sources",
  "mcp",
  "plan",
  "text",
  "tools",
  "attachment",
  "ask_user",
  "error",
]);

function pid(msgId: string, kind: string, suffix?: string | number): string {
  return suffix != null ? `${msgId}:${kind}:${suffix}` : `${msgId}:${kind}`;
}

/**
 * Deterministically project an assistant message's flat fields into an ordered
 * part list, in the same visual order the legacy renderers used. User / system
 * messages return an empty list (they are rendered directly, not via parts).
 */
export function deriveMessageParts(msg: ChatMessage): MessagePart[] {
  if (msg.role !== "assistant") return [];

  const parts: MessagePart[] = [];

  if (msg.thinkingChain && msg.thinkingChain.length > 0) {
    parts.push({ kind: "reasoning", id: pid(msg.id, "reasoning") });
  }
  if (msg.orgTimeline && msg.orgTimeline.length > 0) {
    parts.push({ kind: "org_timeline", id: pid(msg.id, "org_timeline") });
  }
  // ThinkingBlock only renders when there is no structured chain.
  if (msg.thinking && (!msg.thinkingChain || msg.thinkingChain.length === 0)) {
    parts.push({ kind: "thinking", id: pid(msg.id, "thinking") });
  }
  if (msg.sources && msg.sources.length > 0) {
    parts.push({ kind: "sources", id: pid(msg.id, "sources") });
  }
  if (msg.mcpCalls && msg.mcpCalls.length > 0) {
    parts.push({ kind: "mcp", id: pid(msg.id, "mcp") });
  }
  if (msg.todo && msg.todo.steps && msg.todo.steps.length > 0) {
    parts.push({
      kind: "plan",
      id: pid(msg.id, "plan", msg.todo.id || ""),
      todo: msg.todo,
      progressEvents: msg.progressEvents || undefined,
    });
  }
  if (msg.content) {
    parts.push({ kind: "text", id: pid(msg.id, "text") });
  }
  // Legacy ToolCallsGroup only renders when there is no structured chain.
  if (msg.toolCalls && msg.toolCalls.length > 0 && (!msg.thinkingChain || msg.thinkingChain.length === 0)) {
    parts.push({ kind: "tools", id: pid(msg.id, "tools") });
  }
  if (msg.artifacts && msg.artifacts.length > 0) {
    msg.artifacts.forEach((artifact, i) => {
      parts.push({ kind: "attachment", id: pid(msg.id, "attachment", i), artifact });
    });
  }
  if (msg.askUser) {
    parts.push({ kind: "ask_user", id: pid(msg.id, "ask_user"), ask: msg.askUser });
  }
  if (msg.errorInfo) {
    parts.push({ kind: "error", id: pid(msg.id, "error") });
  }

  return parts;
}

/**
 * Normalize a backend/localStorage-provided `parts` array into well-typed
 * `MessagePart[]`, dropping anything with an unknown `kind` or missing id.
 * Returns `null` when the input is not a usable parts array so callers can
 * fall back to `deriveMessageParts`.
 */
export function coerceMessageParts(raw: unknown, msg: ChatMessage): MessagePart[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const out: MessagePart[] = [];
  let counter = 0;
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const kind = (item as { kind?: unknown }).kind;
    if (typeof kind !== "string" || !KNOWN_KINDS.has(kind as MessagePart["kind"])) continue;
    const rawId = (item as { id?: unknown }).id;
    const id = typeof rawId === "string" && rawId ? rawId : pid(msg.id, kind, counter);
    counter += 1;
    switch (kind) {
      case "plan": {
        const todo = ((item as { todo?: ChatTodo | null }).todo ?? msg.todo) || undefined;
        const rawProgressEvents =
          (item as { progressEvents?: ChatProgressEvent[] | null }).progressEvents ??
          msg.progressEvents;
        const progressEvents = Array.isArray(rawProgressEvents) ? rawProgressEvents : undefined;
        out.push({ kind: "plan", id, todo, progressEvents });
        break;
      }
      case "attachment": {
        const artifact = (item as { artifact?: ChatArtifact | null }).artifact || undefined;
        out.push({ kind: "attachment", id, artifact });
        break;
      }
      case "ask_user": {
        const ask = ((item as { ask?: ChatAskUser | null }).ask ?? msg.askUser) || undefined;
        out.push({ kind: "ask_user", id, ask });
        break;
      }
      default:
        out.push({ kind: kind as Exclude<MessagePart["kind"], "plan" | "attachment" | "ask_user">, id });
    }
  }
  return out.length > 0 ? out : null;
}

/**
 * Resolve the parts to render for a message: prefer an explicit (backend or
 * persisted) `parts`, otherwise derive from flat fields. Always self-heals
 * attachment/plan/ask payloads from flat fields when the marker omitted them.
 */
/**
 * Does this assistant message currently have at least one part that will
 * actually render something visible, given the chosen `showChain` mode and the
 * already-stripped `bodyContent`?
 *
 * This is the single source of truth for "is the bubble empty?" — used by the
 * renderers to decide whether to keep the streaming loading indicator visible.
 * Looking only at `msg.content` is wrong: a turn can surface a plan card,
 * ask_user prompt, artifact, sources, etc. with empty answer text, and the
 * reasoning chain itself is hidden when `showChain` is off. Each branch MUST
 * mirror the render guard of the matching `case` in `MessageParts.tsx`; if a
 * new part kind is added there, add it here too (the default stays false, which
 * only over-reports emptiness and is the safe failure mode).
 */
export function hasRenderableBody(
  msg: ChatMessage,
  parts: MessagePart[],
  showChain: boolean,
  bodyContent: string,
): boolean {
  return parts.some((part) => {
    switch (part.kind) {
      case "reasoning":
        return showChain && !!msg.thinkingChain && msg.thinkingChain.length > 0;
      case "thinking":
        return !!msg.thinking;
      case "org_timeline":
        return !!msg.orgTimeline && msg.orgTimeline.length > 0;
      case "sources":
        return !!msg.sources && msg.sources.length > 0;
      case "mcp":
        return !!msg.mcpCalls && msg.mcpCalls.length > 0;
      case "plan": {
        const todo = part.todo || msg.todo;
        return !!todo && !!todo.steps && todo.steps.length > 0;
      }
      case "text":
        return !!bodyContent;
      case "tools":
        return !!msg.toolCalls && msg.toolCalls.length > 0;
      case "attachment":
        return !!part.artifact;
      case "ask_user":
        return !!(part.ask || msg.askUser);
      case "error":
        return !!msg.errorInfo;
      default:
        return false;
    }
  });
}

export function resolveMessageParts(msg: ChatMessage): MessagePart[] {
  const explicit = coerceMessageParts(msg.parts, msg);
  if (explicit) {
    // Fill any attachment markers that didn't inline their artifact, in order.
    let artIdx = 0;
    let healed = explicit.map((part) => {
      if (part.kind === "attachment" && !part.artifact) {
        const art = msg.artifacts?.[artIdx];
        artIdx += 1;
        return { ...part, artifact: art ?? undefined };
      }
      if (part.kind === "attachment") artIdx += 1;
      return part;
    });
    if (msg.todo?.steps?.length && !healed.some((part) => part.kind === "plan")) {
      const planPart: MessagePart = {
        kind: "plan",
        id: pid(msg.id, "plan", msg.todo.id || ""),
        todo: msg.todo,
        progressEvents: msg.progressEvents || undefined,
      };
      const insertBefore = healed.findIndex((part) =>
        part.kind === "text" ||
        part.kind === "tools" ||
        part.kind === "attachment" ||
        part.kind === "ask_user" ||
        part.kind === "error"
      );
      healed = insertBefore >= 0
        ? [...healed.slice(0, insertBefore), planPart, ...healed.slice(insertBefore)]
        : [...healed, planPart];
    }
    return healed;
  }
  return deriveMessageParts(msg);
}
