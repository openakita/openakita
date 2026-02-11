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
  | "finish";

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
  timestamp: number;
  streaming?: boolean;
};

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

export type ChatAskUser = {
  question: string;
  options?: { id: string; label: string }[];
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
  name: string;
  description: string;
  author: string;
  url: string;
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
