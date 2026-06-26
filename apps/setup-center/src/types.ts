// ─── Shared types for Setup Center ───

export type PlatformInfo = {
  os: string;
  arch: string;
  homeDir: string;
  openakitaRootDir: string;
};

export type WorkspaceSummary = {
  id: string;
  name: string;
  path: string;
  isCurrent: boolean;
};

export type ProviderInfo = {
  name: string;
  slug: string;
  api_type: "openai" | "anthropic" | string;
  default_base_url: string;
  api_key_env_suggestion: string;
  supports_model_list: boolean;
  supports_capability_api: boolean;
  requires_api_key?: boolean;  // default true; false for local providers like Ollama
  is_local?: boolean;          // true for local providers (Ollama, LM Studio, etc.)
  coding_plan_base_url?: string;   // Coding Plan 专用 API 地址（存在则支持 coding plan）
  coding_plan_api_type?: string;   // Coding Plan 模式下的协议类型（不存在则与 api_type 相同）
  default_context_window?: number;
  default_max_tokens?: number;
  note?: string;                   // i18n key — 显示在服务商选择下方的提示信息
};

export type ListedModel = {
  id: string;
  name: string;
  capabilities: Record<string, boolean>;
};

export type EndpointDraft = {
  name: string;
  provider: string;
  api_type: string;
  base_url: string;
  api_key_env: string;
  model: string;
  priority: number;
  max_tokens: number;
  context_window: number;
  timeout: number;
  capabilities: string[];
  rpm_limit?: number;
  note?: string | null;
  pricing_tiers?: { max_input: number; input_price: number; output_price: number }[];
  enabled?: boolean;
  // Relay capability discovery (filled by POST /api/config/sync-endpoint-models).
  // When set, the UI can grey out unavailable models and the LLMClient
  // skips this endpoint when its `model` is not in the catalog. Absence
  // means "never probed" — the endpoint is still considered usable.
  supported_models?: string[];
  models_synced_at?: number | null;
  models_sync_error?: string | null;
};

export type PythonCandidate = {
  command: string[];
  versionText: string;
  isUsable: boolean;
};

export type BundledPythonInstallResult = {
  pythonCommand: string[];
  pythonPath: string;
  installDir: string;
  assetName: string;
  tag: string;
};

export type InstallSource = "pypi" | "github" | "local";

export type EnvMap = Record<string, string>;

export type StepId =
  | "welcome"
  | "workspace"
  | "python"
  | "install"
  | "llm"
  | "im"
  | "tools"
  | "agent"
  | "advanced"
  | "finish"
  | "quick-form"
  | "quick-setup"
  | "quick-finish";

export type Step = {
  id: StepId;
  title: string;
  desc: string;
};

export type ViewId = "wizard" | "status" | "chat" | "skills" | "im" | "onboarding" | "token_stats" | "skill_usage" | "mcp" | "scheduler" | "memory" | "dashboard" | "agent_manager" | "agent_store" | "skill_store" | "org_editor" | "pixel_office" | "identity" | "docs" | "security" | "pending_approvals" | "plugins" | "my_feedback" | `plugin_app:${string}`;

export type PluginUIApp = {
  id: string;
  title: string;
  title_i18n?: Record<string, string>;
  icon_url?: string;
  sidebar_group: string;
  sandbox?: string;
  enabled: boolean;
  status?: string;
};

// ─── Health check types ───

export type HealthStatus = "healthy" | "degraded" | "unhealthy" | "unknown" | "disabled";

export type EndpointHealthResult = {
  name: string;
  status: HealthStatus;
  latencyMs: number | null;
  error: string | null;
  errorCategory: string | null;
  consecutiveFailures: number;
  cooldownRemaining: number;
  isExtendedCooldown: boolean;
  lastCheckedAt: string | null;
};

export type IMHealthResult = {
  channel: string;
  name: string;
  status: HealthStatus;
  error: string | null;
  lastCheckedAt: string | null;
};

export type EndpointSummary = {
  name: string;
  provider: string;
  apiType: string;
  baseUrl: string;
  model: string;
  keyEnv: string;
  keyPresent: boolean;
  enabled?: boolean;
  health?: EndpointHealthResult | null;
};

export type IMStatus = {
  k: string;
  name: string;
  enabled: boolean;
  ok: boolean;
  missing: string[];
  health?: IMHealthResult | null;
};

// ─── Chat types ───

export type ChatArtifact = {
  artifact_type: string;  // "image" | "file" | "voice" etc.
  file_url: string;       // relative URL for /api/files/...
  path: string;           // absolute local path
  name: string;
  caption: string;
  size?: number;
};

export type ChatSource = {
  tool_name?: string;
  tool_use_id?: string;
  requested_url: string;
  final_url: string;
  hostname?: string;
  redirected?: boolean;
  from_cache?: boolean;
  status?: string;
  hint?: string;
};

export type ChatMcpCall = {
  tool_use_id?: string;
  server: string;
  tool: string;
  status?: "ok" | "error" | string;
  auto_connected?: boolean;
  reconnected?: boolean;
  error?: string;
};

export type ChatErrorInfo = {
  message: string;
  category: "auth" | "quota" | "timeout" | "content_filter" | "network" | "server" | "unknown";
  raw?: string;
};

/** Single row in the organization-command live timeline. */
export type OrgTimelineEntry = {
  /** "started" – command accepted; "progress" – sub-agent emitted a summary;
   *  "done" – command finished (success or error). */
  status: "started" | "progress" | "done";
  /** Plain-text summary (already user-facing, no internal payload). */
  summary: string;
  /** Optional category – e.g. node id, role label, mailbox type. */
  category?: string | null;
  /** Optional originating node id from org runtime. */
  nodeId?: string | null;
  /** Epoch millis when the event arrived in the browser. */
  timestamp: number;
};

export type ChatMessage = {
  id: string;
  /** Stable backend history index used for paged history loading. */
  historyIndex?: number;
  role: "user" | "assistant" | "system";
  content: string;
  thinking?: string | null;
  agentName?: string | null;
  toolCalls?: ChatToolCall[] | null;
  todo?: ChatTodo | null;
  progressEvents?: ChatProgressEvent[] | null;
  askUser?: ChatAskUser | null;
  attachments?: ChatAttachment[] | null;
  artifacts?: ChatArtifact[] | null;
  sources?: ChatSource[] | null;
  mcpCalls?: ChatMcpCall[] | null;
  thinkingChain?: ChainGroup[] | null;
  /** Live timeline of organization command progress (org-mode only).
   *
   * Populated by `org_command_started` / `org_progress` / `org_command_done`
   * SSE events. Rendered as a collapsible card above the final answer so
   * users can see which sub-agents fired without the progress text leaking
   * into the assistant's textual reply.
   */
  orgTimeline?: OrgTimelineEntry[] | null;
  errorInfo?: ChatErrorInfo | null;
  usage?: {
    input_tokens: number;
    output_tokens: number;
    total_tokens?: number;
    usage_estimated?: boolean;
    usage_source?: string;
  } | null;
  timestamp: number;
  streaming?: boolean;
  /** Ephemeral UI-only status while an SSE stream is alive; never persisted as message content. */
  streamStatus?: string | null;
  /**
   * Set when this assistant bubble was finalized from an interrupted /
   * recovering stream, so its content may be partial or polluted. Backend
   * reconciliation (`patchMessagesWithBackendDetailed`) then replaces the text
   * with the authoritative persisted answer even when the backend copy is
   * *shorter* (e.g. trace markers stripped), and clears this flag. Transient —
   * not persisted to localStorage.
   */
  streamFallback?: boolean;
  /**
   * Ordered, structured render model for an assistant message.
   *
   * This is the single source of truth for how the rich cards (reasoning,
   * plan, text, tools, attachments, answered ask_user, …) are laid out and
   * re-displayed after a reload / window switch. It is normally a
   * deterministic projection of the flat fields above (see
   * `views/chat/utils/messageParts.ts#deriveMessageParts`), and may also be
   * supplied authoritatively by the backend history projection
   * (`/api/sessions/{id}/history` → `parts`). When absent it is derived on
   * the fly, so old sessions / localStorage payloads keep rendering.
   *
   * Kept out of localStorage and out of the LLM transcript on purpose: it is
   * a view concern, not stored message content.
   */
  parts?: MessagePart[] | null;
};

/**
 * One ordered block inside an assistant message.
 *
 * Heavy text blocks (`text`, `reasoning`, `thinking`) are markers — the
 * renderer pulls their payload from the corresponding flat field on the
 * message — so the projection stays small when it travels over the wire from
 * the backend history endpoint. The remaining (small) blocks inline their
 * data so a single part is self-describing. The client-side
 * `deriveMessageParts` builds the same shape from flat fields.
 */
export type MessagePart =
  | { kind: "reasoning"; id: string }
  | { kind: "thinking"; id: string }
  | { kind: "org_timeline"; id: string }
  | { kind: "sources"; id: string }
  | { kind: "mcp"; id: string }
  | { kind: "plan"; id: string; todo?: ChatTodo; progressEvents?: ChatProgressEvent[] }
  | { kind: "text"; id: string }
  | { kind: "tools"; id: string }
  | { kind: "attachment"; id: string; artifact?: ChatArtifact }
  | { kind: "ask_user"; id: string; ask?: ChatAskUser }
  | { kind: "error"; id: string };

// ─── 思维链 (Thinking Chain) 类型 ───

/**
 * Backend ``config_hint`` SSE event payload (see
 * src/openakita/core/reasoning_engine.py:_build_tool_end_events).
 *
 * Single source of truth for both ChainEntry's ``config_hint`` kind and
 * ChatToolCall.configHints[]; keeping it in one place prevents the two
 * sites from drifting (the previous inline duplication was a known smell).
 */
export type ConfigHintPayload = {
  scope: string;
  error_code:
    | "missing_credential"
    | "auth_failed"
    | "rate_limited"
    | "network_unreachable"
    | "content_filter"
    | "unknown";
  title: string;
  message?: string;
  actions?: Array<{
    id?: string;
    label?: string;
    view?: string;
    section?: string;
    anchor?: string;
    url?: string;
    [k: string]: unknown;
  }>;
};

/** 叙事流条目类型 */
export type ChainEntry =
  | { kind: "thinking"; content: string }       // LLM extended thinking 内容
  | { kind: "text"; content: string; icon?: string }  // LLM 推理意图 / chain_text / 状态通知
  | { kind: "tool_start"; toolId: string; tool: string; args: Record<string, unknown>; description: string; status?: "running" | "done" | "error" }
  | { kind: "tool_end"; toolId: string; tool: string; result: string; status: "done" | "error" }
  // Structured config hint inlined into the ReAct chain timeline so the user
  // sees the actionable card *in the iteration that produced it*, instead of
  // relying on the legacy ToolCallsGroup which is hidden whenever a
  // thinkingChain exists (see MessageBubble.tsx / FlatMessageItem.tsx
  // guards). ``toolId`` is forwarded so we can later cross-link the card
  // back to the offending tool_start row if needed.
  | { kind: "config_hint"; toolId: string; hint: ConfigHintPayload }
  | { kind: "compressed"; beforeTokens: number; afterTokens: number };

/** 一个 ReAct 迭代组 = 按时间顺序的叙事流 */
export type ChainGroup = {
  iteration: number;
  entries: ChainEntry[];             // 按时间顺序的叙事片段
  durationMs?: number;               // 本轮耗时 ms
  hasThinking: boolean;              // 模型是否返回了 extended thinking
  collapsed: boolean;                // 当前折叠状态
  // 向后兼容（用于 IM 视图等）
  toolCalls: ChainToolCall[];
};

export type ChainToolCall = {
  toolId: string;
  tool: string;
  args: Record<string, unknown>;
  result?: string;
  status: "running" | "done" | "error";
  description: string;
};

/**
 * Persisted causal reasoning-chain timeline (the server mirrors the browser's
 * ``ChainGroup.entries`` assembly and stores it as ``chain_timeline``). The
 * client restores it with ``buildChainFromTimeline`` so the reasoning chain
 * re-displays faithfully after reload / multi-window switch, instead of the
 * lossy ``chain_summary`` rebuild. Entries reuse the live ``ChainEntry`` shape,
 * including ``config_hint`` action cards.
 */
export type ChainTimelineGroup = {
  iteration: number;
  entries: ChainEntry[];
  durationMs?: number;
};

/** IM 消息中的思维链摘要项 */
export type ChainSummaryItem = {
  iteration: number;
  thinking_preview: string;
  thinking_duration_ms: number;
  tools: { name: string; input_preview: string; result_preview?: string }[];
  context_compressed?: { before_tokens: number; after_tokens: number };
};

/** 聊天显示模式 */
export type ChatDisplayMode = "bubble" | "flat";

export type ChatToolCall = {
  id?: string;
  tool: string;
  args: Record<string, unknown>;
  result?: string | null;
  status: "pending" | "running" | "done" | "error";
  // Optional structured config hints attached when the backend emitted a
  // ``config_hint`` SSE event for this tool call. Used by the legacy
  // ToolCallsGroup path (no thinkingChain). The thinkingChain path renders
  // hints via ChainEntry "config_hint" kind instead — both reference the
  // same ConfigHintPayload type to keep the two render sites in sync.
  configHints?: ConfigHintPayload[];
};

export type ChatTodo = {
  id: string;
  taskSummary: string;
  steps: ChatTodoStep[];
  status: "in_progress" | "completed" | "failed" | "cancelled";
};

/** @deprecated Use ChatTodo instead */
export type ChatPlan = ChatTodo;

export type ChatTodoStep = {
  id?: string;
  description: string;
  status: "pending" | "in_progress" | "completed" | "skipped" | "failed" | "cancelled";
  result?: string | null;
};

export type ChatProgressEvent =
  | { type: "todo_created"; seq?: number; plan: ChatTodo }
  | {
      type: "todo_step_updated";
      seq?: number;
      stepId?: string;
      stepIdx?: number;
      status?: ChatTodoStep["status"];
      result?: string | null;
    }
  | { type: "todo_completed"; seq?: number }
  | { type: "todo_cancelled"; seq?: number };

export type PlanApprovalEvent = {
  conversation_id: string;
  summary: string;
  plan_id: string;
  plan_file: string;
};

export type ChatAskQuestion = {
  id: string;
  prompt: string;
  options?: { id: string; label: string }[];
  allow_multiple?: boolean; // true = multi-select, false = single-select (default)
};

export type ChatAskUser = {
  /** Simple single question (backward compat, used when questions is empty) */
  question: string;
  options?: { id: string; label: string }[];
  /** Structured multi-question support */
  questions?: ChatAskQuestion[];
  answered?: boolean;
  answer?: string;
};

export type ChatAttachment = {
  type: "image" | "file" | "voice" | "video" | "document";
  name: string;
  url?: string;
  localPath?: string;
  uploadId?: string;
  previewUrl?: string;
  size?: number;
  mimeType?: string;
  uploadStatus?: "uploading" | "uploaded" | "failed";
  uploadError?: string;
  /** Transient upload tracking ID — not persisted to backend */
  _uploadId?: string;
};

export type ConversationStatus = "idle" | "running" | "completed" | "error";

export type ChatConversation = {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: number;
  messageCount: number;
  pinned?: boolean;
  titleGenerated?: boolean;
  titleManuallySet?: boolean;
  agentProfileId?: string;
  endpointId?: string;
  endpointPolicy?: "prefer" | "require";
  orgMode?: boolean;
  orgId?: string;
  orgNodeId?: string;
  status?: ConversationStatus;
};

// ─── Slash commands ───

export type SlashCommand = {
  id: string;
  label: string;
  description: string;
  icon?: string;
  action: (args: string) => void;
};

// ─── MCP config types ───

export type MCPConfigField = {
  key: string;
  label: string;
  type: "text" | "secret" | "number" | "select" | "bool" | "url" | "path";
  required?: boolean;
  help?: string;
  helpUrl?: string;
  default?: string | number | boolean;
  placeholder?: string;
  options?: string[];
  when?: Record<string, string> | null;
};

// ─── Skill types ───

export type SkillConfigField = {
  key: string;
  label: string;
  type: "text" | "secret" | "number" | "select" | "bool";
  required?: boolean;
  help?: string;
  default?: string | number | boolean;
  options?: string[];
  min?: number;
  max?: number;
};

export type SkillInfo = {
  skillId: string;
  name: string;
  description: string;
  name_i18n?: Record<string, string> | null;
  description_i18n?: Record<string, string> | null;
  system: boolean;
  enabled?: boolean;
  toolName?: string | null;
  category?: string | null;
  path?: string | null;
  sourceUrl?: string | null;
  config?: SkillConfigField[] | null;
  configComplete?: boolean;
};

export type MarketplaceSkill = {
  id: string;         // e.g. "vercel-labs/agent-skills/vercel-react-best-practices"
  skillId: string;    // e.g. "vercel-react-best-practices"
  name: string;
  description: string;
  author: string;     // source repo owner
  url: string;        // install URL: "owner/repo@skill"
  installs?: number;
  stars?: number;
  tags?: string[];
  installed?: boolean;
};

// ─── Persona presets ───

export const PERSONA_PRESETS = [
  { id: "default", name: "默认助手", desc: "专业友好、平衡得体", style: "适合日常使用，万能型角色" },
  { id: "business", name: "商务顾问", desc: "正式专业、数据驱动", style: "适合工作场景，正式汇报、数据分析" },
  { id: "tech_expert", name: "技术专家", desc: "简洁精准、代码导向", style: "适合编程开发，技术问答" },
  { id: "butler", name: "私人管家", desc: "周到细致、礼貌正式", style: "适合生活服务，日程安排、出行规划" },
  { id: "girlfriend", name: "虚拟女友", desc: "温柔体贴、情感丰富", style: "适合情感陪伴，倾听与关怀" },
  { id: "boyfriend", name: "虚拟男友", desc: "阳光开朗、幽默风趣", style: "适合情感陪伴，轻松有趣" },
  { id: "family", name: "家人", desc: "亲切关怀、唠叨温暖", style: "适合家庭场景，长辈式温暖关怀" },
  { id: "jarvis", name: "贾维斯", desc: "冷静睿智、英式幽默", style: "适合科技极客，像钢铁侠的 AI 管家" },
] as const;
