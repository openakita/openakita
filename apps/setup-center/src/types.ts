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
  note?: string | null;
};

export type PythonCandidate = {
  command: string[];
  versionText: string;
  isUsable: boolean;
};

export type EmbeddedPythonInstallResult = {
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
  | "finish"
  | "quick-form"
  | "quick-setup"
  | "quick-finish";

export type Step = {
  id: StepId;
  title: string;
  desc: string;
};

export type ViewId = "wizard" | "status" | "chat" | "skills";

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

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  thinking?: string | null;
  agentName?: string | null;
  toolCalls?: ChatToolCall[] | null;
  plan?: ChatPlan | null;
  askUser?: ChatAskUser | null;
  attachments?: ChatAttachment[] | null;
  artifacts?: ChatArtifact[] | null;
  thinkingChain?: ChainGroup[] | null;
  timestamp: number;
  streaming?: boolean;
};

// ─── 思维链 (Thinking Chain) 类型 ───

/** 一个 ReAct 迭代组 = 一次 thinking + 关联的 tool calls */
export type ChainGroup = {
  iteration: number;
  thinking?: {
    content: string;       // 完整 thinking 文字
    durationMs: number;    // 思考耗时 ms
    preview: string;       // 首句/首 80 字符, 用于组标题
  };
  durationMs?: number;     // 本轮耗时 ms（无 thinking 内容时也有）
  toolCalls: ChainToolCall[];
  summary?: string;        // 自动生成: "Explored 3 files 2 searches"
  collapsed: boolean;      // 当前折叠状态
  contextCompressed?: {    // 本轮迭代前是否发生了上下文压缩
    beforeTokens: number;
    afterTokens: number;
  };
};

export type ChainToolCall = {
  toolId: string;
  tool: string;                     // 原始工具名
  args: Record<string, unknown>;
  result?: string;
  status: "running" | "done" | "error";
  description: string;              // 人类可读描述 (由 formatter 生成)
};

/** IM 消息中的思维链摘要项 */
export type ChainSummaryItem = {
  iteration: number;
  thinking_preview: string;
  thinking_duration_ms: number;
  tools: { name: string; input_preview: string }[];
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
};

export type ChatPlan = {
  id: string;
  taskSummary: string;
  steps: ChatPlanStep[];
  status: "in_progress" | "completed" | "failed";
};

export type ChatPlanStep = {
  description: string;
  status: "pending" | "in_progress" | "completed" | "skipped";
  result?: string | null;
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
  type: "image" | "file" | "voice";
  name: string;
  url?: string;
  previewUrl?: string;
  size?: number;
  mimeType?: string;
};

export type ChatConversation = {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: number;
  messageCount: number;
};

// ─── Slash commands ───

export type SlashCommand = {
  id: string;
  label: string;
  description: string;
  icon?: string;
  action: (args: string) => void;
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
  name: string;
  description: string;
  system: boolean;
  enabled?: boolean;
  toolName?: string | null;
  category?: string | null;
  path?: string | null;
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
