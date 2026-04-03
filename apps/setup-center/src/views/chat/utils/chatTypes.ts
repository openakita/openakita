// ─── ChatView 本地类型定义 ───
// 核心共享类型（ChatMessage, ChatConversation 等）位于 @/types.ts，此处仅定义 ChatView 内部使用的类型。

import type {
  ChatMessage,
  ChatToolCall,
  ChatTodo,
  ChatTodoStep,
  ChatAskUser,
  ChatAskQuestion,
  ChatAttachment,
  ChatArtifact,
  ChatErrorInfo,
  ChatConversation,
  ChatDisplayMode,
  ConversationStatus,
  EndpointSummary,
  SlashCommand,
  ChainGroup,
  ChainToolCall,
  ChainEntry,
  ChainSummaryItem,
} from "../../../types";

export type {
  ChatMessage,
  ChatToolCall,
  ChatTodo,
  ChatTodoStep,
  ChatAskUser,
  ChatAskQuestion,
  ChatAttachment,
  ChatArtifact,
  ChatErrorInfo,
  ChatConversation,
  ChatDisplayMode,
  ConversationStatus,
  EndpointSummary,
  SlashCommand,
  ChainGroup,
  ChainToolCall,
  ChainEntry,
  ChainSummaryItem,
};

/** Lazy-loaded markdown rendering modules */
export type MdModules = {
  ReactMarkdown: typeof import("react-markdown").default;
  remarkPlugins: import("react-markdown").Options["remarkPlugins"];
  rehypePlugins: import("react-markdown").Options["rehypePlugins"];
};

/** Message queued for sequential sending */
export type QueuedMessage = {
  id: string;
  text: string;
  timestamp: number;
  convId: string;
};

/** SSE stream event union — synced with Python openakita.events / src/streamEvents.ts */
export type StreamEvent =
  | { type: "heartbeat" }
  | { type: "iteration_start"; iteration: number }
  | { type: "context_compressed"; before_tokens: number; after_tokens: number }
  | { type: "thinking_start" }
  | { type: "thinking_delta"; content: string }
  | { type: "thinking_end"; duration_ms?: number; has_thinking?: boolean }
  | { type: "chain_text"; content: string }
  | { type: "text_delta"; content: string }
  | { type: "text_replace"; content: string }
  | { type: "tool_call_start"; tool: string; tool_name?: string; args: Record<string, unknown>; id?: string; call_id?: string; protocol_version?: number }
  | { type: "tool_call_end"; tool: string; tool_name?: string; result: string; id?: string; call_id?: string; is_error?: boolean; skipped?: boolean; protocol_version?: number }
  | { type: "todo_created"; plan: ChatTodo; restored?: boolean }
  | { type: "todo_step_updated"; stepId?: string; step_id?: string; stepIdx?: number; status: string; protocol_version?: number }
  | { type: "todo_completed" }
  | { type: "todo_cancelled" }
  | { type: "plan_ready_for_approval"; data: { conversation_id: string; summary: string; plan_id: string; plan_file: string }; conversation_id?: string; plan_id?: string; plan_file?: string; protocol_version?: number }
  | { type: "ask_user"; question: string; options?: { id: string; label: string }[]; allow_multiple?: boolean; questions?: { id: string; prompt: string; options?: { id: string; label: string }[]; allow_multiple?: boolean }[] }
  | { type: "user_insert"; content: string }
  | { type: "agent_switch"; agentName: string; reason: string }
  | { type: "agent_handoff"; from_agent: string; to_agent: string; reason?: string }
  | { type: "sub_agent_state"; agent_id?: string; agentId?: string; session_id?: string; sessionId?: string; status?: string; reason?: string; protocol_version?: number }
  | { type: "artifact"; artifact_type: string; file_url: string; path: string; name: string; caption: string; size?: number }
  | { type: "security_confirm"; tool: string; tool_name?: string; args: Record<string, unknown>; id?: string; call_id?: string; confirm_id?: string; reason: string; risk_level: string; needs_sandbox: boolean; protocol_version?: number }
  | { type: "ui_preference"; theme?: string; language?: string }
  | { type: "error"; message: string }
  | { type: "done"; reason?: string; usage?: { input_tokens: number; output_tokens: number; total_tokens?: number; context_tokens?: number; context_limit?: number } };

/** Sub-agent delegation entry for handoff display */
export type SubAgentEntry = {
  agentId: string;
  status: "delegating" | "done" | "error";
  reason?: string;
  startTime: number;
};

/** Sub-agent task progress card data */
export type SubAgentTask = {
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
  tokens_used?: number;
  current_tool_summary?: string;
  queue_count?: number;
};

/** Per-session streaming context (supports concurrent streams across conversations) */
export type StreamContext = {
  abort: AbortController;
  reader: ReadableStreamDefaultReader<Uint8Array> | null;
  isStreaming: boolean;
  userStopped: boolean;
  messages: ChatMessage[];
  activeSubAgents: SubAgentEntry[];
  subAgentTasks: SubAgentTask[];
  isDelegating: boolean;
  pollingTimer: ReturnType<typeof setInterval> | null;
};

/** Agent profile for agent selector */
export type AgentProfile = {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  name_i18n?: Record<string, string>;
  description_i18n?: Record<string, string>;
  preferred_endpoint?: string | null;
};
