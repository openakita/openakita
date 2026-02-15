import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getVersion } from "@tauri-apps/api/app";
// Window controls are handled by native title bar
import { ChatView } from "./views/ChatView";
import { SkillManager } from "./views/SkillManager";
import { IMView } from "./views/IMView";
import type { EndpointSummary as EndpointSummaryType } from "./types";
import {
  IconChat, IconIM, IconSkills, IconStatus, IconConfig,
  IconRefresh, IconCheck, IconCheckCircle, IconX, IconXCircle,
  IconChevronDown, IconChevronRight, IconChevronUp, IconGlobe, IconLink, IconPower,
  IconEdit, IconTrash, IconEye, IconEyeOff, IconInfo, IconClipboard,
  DotGreen, DotGray, DotYellow, DotRed,
  IconBook, IconZap, IconGear,
  LogoTelegram, LogoFeishu, LogoWework, LogoDingtalk, LogoQQ,
} from "./icons";
import logoUrl from "./assets/logo.png";
import "highlight.js/styles/github.css";
// ═══════════════════════════════════════════════════════════════════════
// 前后端交互路由原则（全局适用）：
//   后端运行中 → 所有配置读写、模型列表、连接测试 **优先走后端 HTTP API**
//                后端负责持久化、热加载、配置兼容性验证
//   后端未运行（onboarding / 首次配置 / wizard full 模式 finish 步骤前）
//                → 走本地 Tauri Rust 操作或前端直连服务商 API
//   判断函数：shouldUseHttpApi()  /  httpApiBase()
//   容错机制：HTTP API 调用失败时自动回退到 Tauri 本地操作（应对后端重启等瞬态异常）
//
// 两种使用模式均完整支持：
//   1. Onboarding（打包模式）：NSIS → onboarding wizard → 写本地 → 启动服务 → HTTP API
//   2. Wizard Full（开发者模式）：选工作区 → 装 venv → 配置端点(本地) → 启动服务 → HTTP API
// ═══════════════════════════════════════════════════════════════════════
// ── 唯一数据源：与 Python 后端共享 providers.json ──
// 路径通过 vite.config.ts alias 映射到 src/openakita/llm/registries/providers.json
// 新增/修改服务商只需编辑该 JSON 文件，前后端自动同步
import SHARED_PROVIDERS from "@shared/providers.json";

type PlatformInfo = {
  os: string;
  arch: string;
  homeDir: string;
  openakitaRootDir: string;
};

type WorkspaceSummary = {
  id: string;
  name: string;
  path: string;
  isCurrent: boolean;
};

type ProviderInfo = {
  name: string;
  slug: string;
  api_type: "openai" | "anthropic" | string;
  default_base_url: string;
  api_key_env_suggestion: string;
  supports_model_list: boolean;
  supports_capability_api: boolean;
};

// 内置 Provider 列表（打包模式下 venv 不可用时作为回退）
// 数据来源：@shared/providers.json（与 Python 后端共享同一份文件）
// registry_class 字段仅 Python 使用，前端忽略
const BUILTIN_PROVIDERS: ProviderInfo[] = SHARED_PROVIDERS as ProviderInfo[];

type ListedModel = {
  id: string;
  name: string;
  capabilities: Record<string, boolean>;
};

// ── 前端直连模型列表 API（不依赖 Python 后端）──
// 当 Python venv 和本地服务都不可用时（如打包模式 onboarding），
// 前端可以直接用用户的 API Key 请求服务商的 /models 接口。
// 这与 Python bridge 的 list_models 逻辑完全等价。

/**
 * 前端版 infer_capabilities：根据模型名推断能力。
 * 与 Python 端 openakita.llm.capabilities.infer_capabilities 的关键词规则保持一致。
 *
 * ⚠ 维护提示：如果 Python 端的推断规则有修改，需要同步更新此函数。
 * 参见: src/openakita/llm/capabilities.py → infer_capabilities()
 */
function inferCapabilities(modelName: string, _providerSlug?: string | null): Record<string, boolean> {
  const m = modelName.toLowerCase();
  const caps: Record<string, boolean> = { text: true, vision: false, video: false, tools: false, thinking: false };

  // Vision
  if (["vl", "vision", "visual", "image", "-v-", "4v"].some(kw => m.includes(kw))) caps.vision = true;
  // Video
  if (["kimi", "gemini"].some(kw => m.includes(kw))) caps.video = true;
  // Thinking
  if (["thinking", "r1", "qwq", "qvq", "o1"].some(kw => m.includes(kw))) caps.thinking = true;
  // Tools
  if (["qwen", "gpt", "claude", "deepseek", "kimi", "glm", "gemini", "moonshot"].some(kw => m.includes(kw))) caps.tools = true;

  return caps;
}

/**
 * 前端直连服务商 API 拉取模型列表。
 * 通过 Rust http_proxy_request 命令代理发送，绕过 WebView CORS 限制。
 */
async function fetchModelsDirectly(params: {
  apiType: string; baseUrl: string; providerSlug: string | null; apiKey: string;
}): Promise<ListedModel[]> {
  const { apiType, baseUrl, providerSlug, apiKey } = params;
  const base = baseUrl.replace(/\/+$/, "");

  if (apiType === "anthropic") {
    // Anthropic: GET /v1/models
    const url = base.endsWith("/v1") ? `${base}/models` : `${base}/v1/models`;
    const resp = await proxyFetch(url, {
      headers: { "x-api-key": apiKey, "anthropic-version": "2023-06-01" },
      timeoutSecs: 30,
    });
    if (resp.status >= 400) throw new Error(`Anthropic API ${resp.status}: ${resp.body.slice(0, 200)}`);
    const data = JSON.parse(resp.body);
    return (data.data ?? [])
      .map((m: any) => ({
        id: String(m.id ?? "").trim(),
        name: String(m.display_name ?? m.id ?? ""),
        capabilities: inferCapabilities(String(m.id ?? ""), providerSlug),
      }))
      .filter((m: ListedModel) => m.id);
  }

  // OpenAI-compatible: GET /models
  const url = `${base}/models`;
  const resp = await proxyFetch(url, {
    headers: { Authorization: `Bearer ${apiKey}` },
    timeoutSecs: 30,
  });
  if (resp.status >= 400) throw new Error(`API ${resp.status}: ${resp.body.slice(0, 200)}`);
  const data = JSON.parse(resp.body);
  return (data.data ?? [])
    .map((m: any) => ({
      id: String(m.id ?? "").trim(),
      name: String(m.id ?? ""),
      capabilities: inferCapabilities(String(m.id ?? ""), providerSlug),
    }))
    .filter((m: ListedModel) => m.id)
    .sort((a: ListedModel, b: ListedModel) => a.id.localeCompare(b.id));
}

type EndpointDraft = {
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

type PythonCandidate = {
  command: string[];
  versionText: string;
  isUsable: boolean;
};

type EmbeddedPythonInstallResult = {
  pythonCommand: string[];
  pythonPath: string;
  installDir: string;
  assetName: string;
  tag: string;
};

type InstallSource = "pypi" | "github" | "local";

function slugify(input: string) {
  return input
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9-_]/g, "")
    .slice(0, 32);
}

function joinPath(a: string, b: string) {
  if (!a) return b;
  const sep = a.includes("\\") ? "\\" : "/";
  return a.replace(/[\\/]+$/, "") + sep + b.replace(/^[\\/]+/, "");
}

function toFileUrl(p: string) {
  const t = p.trim();
  if (!t) return "";
  // Windows: D:\path\to\repo -> file:///D:/path/to/repo
  if (/^[a-zA-Z]:[\\/]/.test(t)) {
    const s = t.replace(/\\/g, "/");
    return `file:///${s}`;
  }
  // POSIX: /Users/... -> file:///Users/...
  if (t.startsWith("/")) {
    return `file://${t}`;
  }
  // Fallback (best-effort)
  return `file://${t}`;
}

function envKeyFromSlug(slug: string) {
  const up = slug.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
  return `${up}_API_KEY`;
};

function nextEnvKeyName(base: string, used: Set<string>) {
  const b = base.trim();
  if (!b) return base;
  if (!used.has(b)) return b;
  for (let i = 2; i < 100; i++) {
    const k = `${b}_${i}`;
    if (!used.has(k)) return k;
  }
  return `${b}_${Date.now()}`;
}

function suggestEndpointName(providerSlug: string, modelId: string) {
  const p = (providerSlug || "provider").trim() || "provider";
  const m = (modelId || "").trim();
  if (!m) return `${p}-primary`.slice(0, 64);
  // keep readable, avoid path separators in names
  const clean = m.replace(/[\\/]+/g, "-");
  return `${p}-${clean}`.slice(0, 64);
}

type EnvMap = Record<string, string>;

function parseEnv(content: string): EnvMap {
  const out: EnvMap = {};
  for (const raw of content.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1);
    out[k] = v;
  }
  return out;
}

function envGet(env: EnvMap, key: string, fallback = "") {
  return env[key] ?? fallback;
}

function envSet(env: EnvMap, key: string, value: string): EnvMap {
  return { ...env, [key]: value };
}

type StepId =
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

type Step = {
  id: StepId;
  title: string;
  desc: string;
};

function SearchSelect({
  value,
  onChange,
  options,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(0);
  const [search, setSearch] = useState(""); // 独立搜索词，与选中值分离
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const hasOptions = options.length > 0;

  // 当有下拉选项时：显示文本 = 搜索词（正在搜索）或已选值
  // 当无下拉选项时：直接使用 value 作为手动输入
  const displayValue = hasOptions ? (search || value) : value;

  const filtered = useMemo(() => {
    if (!hasOptions) return [];
    const q = search.trim().toLowerCase();
    const list = q ? options.filter((x) => x.toLowerCase().includes(q)) : options;
    return list.slice(0, 200);
  }, [options, search, hasOptions]);

  useEffect(() => {
    if (hoverIdx >= filtered.length) setHoverIdx(0);
  }, [filtered.length, hoverIdx]);

  return (
    <div ref={rootRef} style={{ position: "relative", flex: "1 1 auto", minWidth: 0 }}>
      <div style={{ position: "relative" }}>
        <input
          ref={inputRef}
          value={displayValue}
          onChange={(e) => {
            if (hasOptions) {
              setSearch(e.target.value);
              setOpen(true);
            } else {
              onChange(e.target.value);
            }
          }}
          placeholder={placeholder}
          onFocus={() => { if (hasOptions) setOpen(true); }}
          onBlur={() => {
            // 延迟关闭，让 click 事件先触发
            setTimeout(() => setOpen(false), 150);
          }}
          onKeyDown={(e) => {
            if (!hasOptions) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setHoverIdx((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setHoverIdx((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter") {
              if (open && filtered[hoverIdx]) {
                e.preventDefault();
                onChange(filtered[hoverIdx]);
                setSearch("");
                setOpen(false);
              }
            } else if (e.key === "Escape") {
              setSearch("");
              setOpen(false);
            }
          }}
          disabled={disabled}
          style={{ paddingRight: hasOptions ? (value ? 72 : 44) : 12 }}
        />
        {/* × 清空按钮：有选中值或搜索词时显示 */}
        {hasOptions && (value || search) && !disabled && (
          <button
            type="button"
            className="btnSmall"
            onClick={() => {
              setSearch("");
              onChange("");
              setOpen(true);
              inputRef.current?.focus();
            }}
            style={{
              position: "absolute",
              right: 42,
              top: "50%",
              transform: "translateY(-50%)",
              width: 26,
              height: 26,
              padding: 0,
              borderRadius: 8,
              display: "grid",
              placeItems: "center",
              fontSize: 14,
              color: "var(--muted)",
              opacity: 0.7,
            }}
            title="清空"
          >
            ✕
          </button>
        )}
        {/* ▾ 下拉按钮：仅在有选项时显示 */}
        {hasOptions && (
          <button
            type="button"
            className="btnSmall"
            onClick={() => {
              if (!open) { setSearch(""); }
              setOpen((v) => !v);
              inputRef.current?.focus();
            }}
            disabled={disabled}
            style={{
              position: "absolute",
              right: 8,
              top: "50%",
              transform: "translateY(-50%)",
              width: 34,
              height: 30,
              padding: 0,
              borderRadius: 10,
              display: "grid",
              placeItems: "center",
            }}
          >
            ▾
          </button>
        )}
      </div>
      {open && hasOptions && !disabled ? (
        <div
          style={{
            position: "absolute",
            zIndex: 50,
            left: 0,
            right: 0,
            marginTop: 6,
            maxHeight: 280,
            overflow: "auto",
            border: "1px solid var(--line)",
            borderRadius: 14,
            background: "rgba(255,255,255,0.98)",
            boxShadow: "0 18px 60px rgba(17, 24, 39, 0.14)",
          }}
          onMouseDown={(e) => {
            // prevent input blur before click
            e.preventDefault();
          }}
        >
          {filtered.length === 0 ? (
            <div style={{ padding: 12, color: "var(--muted)", fontWeight: 650 }}>没有匹配项</div>
          ) : (
            filtered.map((opt, idx) => (
              <div
                key={opt}
                onMouseEnter={() => setHoverIdx(idx)}
                onClick={() => {
                  onChange(opt);
                  setSearch("");
                  setOpen(false);
                }}
                style={{
                  padding: "10px 12px",
                  cursor: "pointer",
                  fontWeight: 650,
                  background: opt === value
                    ? "rgba(14, 165, 233, 0.16)"
                    : idx === hoverIdx
                      ? "rgba(14, 165, 233, 0.06)"
                      : "transparent",
                  borderTop: idx === 0 ? "none" : "1px solid rgba(17,24,39,0.06)",
                }}
              >
                {opt === value ? `✓ ${opt}` : opt}
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

const PIP_INDEX_PRESETS: { id: "official" | "tuna" | "aliyun" | "custom"; label: string; url: string }[] = [
  { id: "official", label: "官方 PyPI（默认）", url: "" },
  { id: "tuna", label: "清华 TUNA", url: "https://pypi.tuna.tsinghua.edu.cn/simple" },
  { id: "aliyun", label: "阿里云", url: "https://mirrors.aliyun.com/pypi/simple/" },
  { id: "custom", label: "自定义…", url: "" },
];

/**
 * fetch wrapper: 在 HTTP 4xx/5xx 时自动抛异常（原生 fetch 只在网络错误时才抛）。
 * 所有对后端 API 的调用都应使用此函数，以确保错误被正确捕获。
 */
async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  // Apply a default timeout (10s) if the caller didn't supply an AbortSignal
  const effectiveInit = init?.signal ? init : { ...init, signal: AbortSignal.timeout(10_000) };
  const res = await fetch(url, effectiveInit);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.text();
      if (body) detail = body.slice(0, 200);
    } catch { /* ignore */ }
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }
  return res;
}

/**
 * 通过 Rust http_proxy_request 命令发送 HTTP 请求，绕过 WebView 的 CORS 限制。
 * 当前端需要直连外部 API（如 LLM 服务商）但 Python 后端未运行时使用。
 */
async function proxyFetch(url: string, options?: {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  timeoutSecs?: number;
}): Promise<{ status: number; body: string }> {
  const raw = await invoke<string>("http_proxy_request", {
    url,
    method: options?.method ?? "GET",
    headers: options?.headers ?? null,
    body: options?.body ?? null,
    timeoutSecs: options?.timeoutSecs ?? 30,
  });
  return JSON.parse(raw) as { status: number; body: string };
}

// ── 故障排除面板组件 ──
function TroubleshootPanel({ t }: { t: (k: string) => string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const isWin = navigator.platform?.toLowerCase().includes("win");
  const listCmd = isWin ? 'tasklist | findstr python' : 'ps aux | grep openakita';
  const killCmd = isWin ? 'taskkill /F /PID <PID>' : 'kill -9 <PID>';

  const copyText = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div style={{ marginTop: 8, padding: "8px 12px", background: "#f5f5f5", borderRadius: 6, fontSize: 12, color: "#555" }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>{t("status.troubleshootTitle")}</div>
      <div style={{ marginBottom: 4 }}>{t("status.troubleshootTip")}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: "#888", minWidth: 60 }}>{t("status.troubleshootListProcess")}:</span>
          <code style={{ background: "#e8e8e8", padding: "1px 6px", borderRadius: 3, fontSize: 11, flex: 1 }}>{listCmd}</code>
          <button className="btnSmall" style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => copyText(listCmd, "list")}>
            {copied === "list" ? t("status.troubleshootCopied") : t("status.troubleshootCopy")}
          </button>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: "#888", minWidth: 60 }}>{t("status.troubleshootKillProcess")}:</span>
          <code style={{ background: "#e8e8e8", padding: "1px 6px", borderRadius: 3, fontSize: 11, flex: 1 }}>{killCmd}</code>
          <button className="btnSmall" style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => copyText(killCmd, "kill")}>
            {copied === "kill" ? t("status.troubleshootCopied") : t("status.troubleshootCopy")}
          </button>
        </div>
      </div>
      <div style={{ marginTop: 6, color: "#999", fontSize: 11 }}>{t("status.troubleshootRestart")}</div>
    </div>
  );
}

export function App() {
  const { t, i18n } = useTranslation();
  const [info, setInfo] = useState<PlatformInfo | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Auto-dismiss notice after 4s
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(t);
  }, [notice]);
  const [busy, setBusy] = useState<string | null>(null);
  const [dangerAck, setDangerAck] = useState(false);

  // ── Generic confirm dialog ──
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  function askConfirm(message: string, onConfirm: () => void) {
    setConfirmDialog({ message, onConfirm });
  }

  // ── Restart overlay state ──
  const [restartOverlay, setRestartOverlay] = useState<{ phase: "saving" | "restarting" | "waiting" | "done" | "fail" | "notRunning" } | null>(null);

  // ── Service conflict & version state ──
  const [conflictDialog, setConflictDialog] = useState<{ pid: number; version: string } | null>(null);
  const [pendingStartWsId, setPendingStartWsId] = useState<string | null>(null); // workspace ID waiting for conflict resolution
  const [versionMismatch, setVersionMismatch] = useState<{ backend: string; desktop: string } | null>(null);
  const [newRelease, setNewRelease] = useState<{ latest: string; current: string; url: string } | null>(null);
  const [desktopVersion, setDesktopVersion] = useState("0.0.0");
  const [backendVersion, setBackendVersion] = useState<string | null>(null);
  const GITHUB_REPO = "openakita/openakita";

  // Read desktop app version from Tauri on mount
  useEffect(() => {
    getVersion().then((v) => setDesktopVersion(v)).catch(() => setDesktopVersion("1.10.5")); // fallback
  }, []);

  // Ensure boot overlay is removed once React actually mounts.
  useEffect(() => {
    try {
      document.getElementById("boot")?.remove();
      window.dispatchEvent(new Event("openakita_app_ready"));
    } catch {
      // ignore
    }
  }, []);

  // ── Quick / Full config mode ──
  const [configMode, setConfigMode] = useState<"quick" | "full" | null>(null);

  const steps: Step[] = useMemo(
    () => {
      if (configMode === "quick") {
        return [
          { id: "welcome", title: t("welcome.quickTitle"), desc: t("welcome.modeTitle") },
          { id: "quick-form", title: t("config.step.quickForm"), desc: t("config.step.quickFormDesc") },
          { id: "quick-setup", title: t("config.step.quickSetup"), desc: t("config.step.quickSetupDesc") },
          { id: "quick-finish", title: t("config.step.quickFinish"), desc: t("config.step.quickFinishDesc") },
        ];
      }
      if (configMode === "full") {
        return [
          { id: "workspace", title: t("config.step.workspace"), desc: t("config.step.workspaceDesc") },
          { id: "python", title: "Python", desc: t("config.step.pythonDesc") },
          { id: "install", title: t("config.step.install"), desc: t("config.step.installDesc") },
          { id: "llm", title: t("config.step.endpoints"), desc: t("config.step.endpointsDesc") },
          { id: "im", title: t("config.imTitle"), desc: t("config.step.imDesc") },
          { id: "tools", title: t("config.step.tools"), desc: t("config.step.toolsDesc") },
          { id: "agent", title: t("config.step.agent"), desc: t("config.step.agentDesc") },
          { id: "finish", title: t("config.step.finish"), desc: t("config.step.finishDesc") },
        ];
      }
      // configMode === null: show welcome only
      return [
        { id: "welcome", title: t("config.step.welcome"), desc: t("config.step.welcomeDesc") },
      ];
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [configMode, t],
  );

  const [view, setView] = useState<"wizard" | "status" | "chat" | "skills" | "im" | "onboarding" | "modules">("wizard");
  const [appInitializing, setAppInitializing] = useState(true); // 首次加载检测中，防止闪烁
  const [configExpanded, setConfigExpanded] = useState(true);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // ── Data mode: "local" (Tauri commands) or "remote" (HTTP API) ──
  const [dataMode, setDataMode] = useState<"local" | "remote">("local");
  const [apiBaseUrl, setApiBaseUrl] = useState(() => localStorage.getItem("openakita_apiBaseUrl") || "http://127.0.0.1:18900");
  const [connectDialogOpen, setConnectDialogOpen] = useState(false);
  const [connectAddress, setConnectAddress] = useState("");
  const [stepId, setStepId] = useState<StepId>("welcome");
  const currentStepIdxRaw = useMemo(() => steps.findIndex((s) => s.id === stepId), [steps, stepId]);
  const currentStepIdx = currentStepIdxRaw < 0 ? 0 : currentStepIdxRaw;
  const isFirst = currentStepIdx <= 0;
  const isLast = currentStepIdx >= steps.length - 1;

  // 记录用户历史最远到达的步骤索引，回退后依然允许点击已到达的步骤
  // 使用 localStorage 持久化，重启后恢复
  const [maxReachedStepIdx, setMaxReachedStepIdx] = useState(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("openakita_maxStep");
      return saved ? parseInt(saved, 10) || 0 : 0;
    }
    return 0;
  });
  useEffect(() => {
    setMaxReachedStepIdx((prev) => {
      const next = Math.max(prev, currentStepIdx);
      localStorage.setItem("openakita_maxStep", String(next));
      return next;
    });
  }, [currentStepIdx]);

  // 切换工作区时重置最远步骤记录
  useEffect(() => {
    const saved = localStorage.getItem("openakita_maxStep");
    setMaxReachedStepIdx(saved ? parseInt(saved, 10) || 0 : 0);
  }, [currentWorkspaceId]);

  // ── Onboarding Wizard (首次安装引导) ──
  type OnboardingStep = "ob-welcome" | "ob-llm" | "ob-im" | "ob-modules" | "ob-cli" | "ob-progress" | "ob-done";
  type ModuleInfo = { id: string; name: string; description: string; installed: boolean; bundled: boolean; sizeMb: number };
  const [obStep, setObStep] = useState<OnboardingStep>("ob-welcome");
  const [obModules, setObModules] = useState<ModuleInfo[]>([]);
  const [obSelectedModules, setObSelectedModules] = useState<Set<string>>(new Set(["vector-memory", "browser", "whisper"]));
  const obModulesDefaultsApplied = useRef(false);
  const [obInstallLog, setObInstallLog] = useState<string[]>([]);
  const [obInstalling, setObInstalling] = useState(false);
  const [obEnvCheck, setObEnvCheck] = useState<{
    hasOldVenv: boolean; hasOldRuntime: boolean; hasOldWorkspaces: boolean;
    oldVersion: string | null; currentVersion: string; conflicts: string[];
    diskUsageMb: number; runningProcesses: string[];
  } | null>(null);
  /** onboarding 启动时检测到已运行的本地后端服务（用户可选择跳过 onboarding 直接连接） */
  const [obDetectedService, setObDetectedService] = useState<{
    version: string; pid: number | null;
  } | null>(null);

  // CLI 命令注册状态
  const [obCliOpenakita, setObCliOpenakita] = useState(true);
  const [obCliOa, setObCliOa] = useState(true);
  const [obCliAddToPath, setObCliAddToPath] = useState(true);

  /** 探测本地是否有后端服务在运行（用于 onboarding 前提示用户） */
  async function obProbeRunningService() {
    try {
      const res = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(2000) });
      if (res.ok) {
        const data = await res.json();
        setObDetectedService({ version: data.version || "unknown", pid: data.pid ?? null });
      }
    } catch {
      // 无服务运行，正常进入 onboarding
      setObDetectedService(null);
    }
  }

  /** 连接已检测到的本地服务，跳过 onboarding */
  async function obConnectExistingService() {
    try {
      // 1. 确保有默认工作区
      const wsList = await invoke<WorkspaceSummary[]>("list_workspaces");
      if (!wsList.length) {
        const wsId = "default";
        await invoke("create_workspace", { name: t("onboarding.defaultWorkspace"), id: wsId, setCurrent: true });
        await invoke("set_current_workspace", { id: wsId });
        setCurrentWorkspaceId(wsId);
        setWorkspaces([{ id: wsId, name: t("onboarding.defaultWorkspace"), path: "", isCurrent: true }]);
      } else {
        setWorkspaces(wsList);
        if (!currentWorkspaceId && wsList.length > 0) {
          setCurrentWorkspaceId(wsList[0].id);
        }
      }
      // 2. 设置服务状态为已运行
      const baseUrl = "http://127.0.0.1:18900";
      setApiBaseUrl(baseUrl);
      setServiceStatus({ running: true, pid: obDetectedService?.pid ?? null, pidFile: "" });
      // 3. 刷新状态 & 自动检查端点
      refreshStatus("local", baseUrl, true);
      autoCheckEndpoints(baseUrl);
      // 4. 跳过 onboarding，进入主界面
      setView("status");
    } catch (e) {
      console.error("obConnectExistingService failed:", e);
    }
  }

  // 首次运行检测（在此完成前不渲染主界面，防止先闪主页再跳 onboarding）
  useEffect(() => {
    (async () => {
      try {
        const firstRun = await invoke<boolean>("is_first_run");
        if (firstRun) {
          await obProbeRunningService();
          setView("onboarding");
          obLoadEnvCheck();
        }
      } catch {
        // is_first_run 命令不可用（开发模式），忽略
      } finally {
        setAppInitializing(false);
      }
    })();
    const unlisten = listen<string>("app-launch-mode", async (e) => {
      if (e.payload === "first-run") {
        await obProbeRunningService();
        setView("onboarding");
        obLoadEnvCheck();
      }
    });
    // ── DEV: Ctrl+Shift+O 强制进入 onboarding 测试模式 ──
    const devKeyHandler = (ev: KeyboardEvent) => {
      if (ev.ctrlKey && ev.shiftKey && ev.key === "O") {
        ev.preventDefault();
        console.log("[DEV] Force entering onboarding mode");
        setObStep("ob-welcome");
        setObDetectedService(null);
        obProbeRunningService();
        setView("onboarding");
        obLoadEnvCheck();
      }
    };
    window.addEventListener("keydown", devKeyHandler);
    return () => {
      unlisten.then((u) => u());
      window.removeEventListener("keydown", devKeyHandler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // workspace create
  const [newWsName, setNewWsName] = useState("默认工作区");
  const newWsId = useMemo(() => slugify(newWsName) || "default", [newWsName]);

  // python / venv / install
  const [pythonCandidates, setPythonCandidates] = useState<PythonCandidate[]>([]);
  const [selectedPythonIdx, setSelectedPythonIdx] = useState<number>(-1);
  const [venvStatus, setVenvStatus] = useState<string>("");
  const [installLog, setInstallLog] = useState<string>("");
  const [installLiveLog, setInstallLiveLog] = useState<string>("");
  const [installProgress, setInstallProgress] = useState<{ stage: string; percent: number } | null>(null);
  const [extras, setExtras] = useState<string>("all");
  const [indexUrl, setIndexUrl] = useState<string>("");
  const [pipIndexPresetId, setPipIndexPresetId] = useState<"official" | "tuna" | "aliyun" | "custom">("official");
  const [customIndexUrl, setCustomIndexUrl] = useState<string>("");
  const [venvReady, setVenvReady] = useState(false);
  const [openakitaInstalled, setOpenakitaInstalled] = useState(false);
  const [installSource, setInstallSource] = useState<InstallSource>("pypi");
  const [githubRepo, setGithubRepo] = useState<string>("openakita/openakita");
  const [githubRefType, setGithubRefType] = useState<"branch" | "tag">("branch");
  const [githubRef, setGithubRef] = useState<string>("main");
  const [localSourcePath, setLocalSourcePath] = useState<string>("");
  const [pypiVersions, setPypiVersions] = useState<string[]>([]);
  const [pypiVersionsLoading, setPypiVersionsLoading] = useState(false);
  const [selectedPypiVersion, setSelectedPypiVersion] = useState<string>(""); // "" = 推荐同版本

  // providers & models
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providerSlug, setProviderSlug] = useState<string>("");
  const selectedProvider = useMemo(
    () => providers.find((p) => p.slug === providerSlug) || null,
    [providers, providerSlug],
  );
  const [apiType, setApiType] = useState<"openai" | "anthropic">("openai");
  const [baseUrl, setBaseUrl] = useState<string>("");
  const [apiKeyEnv, setApiKeyEnv] = useState<string>("");
  const [apiKeyValue, setApiKeyValue] = useState<string>("");
  const [models, setModels] = useState<ListedModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>("");
  const [capSelected, setCapSelected] = useState<string[]>([]);
  const [capTouched, setCapTouched] = useState(false);
  const [endpointName, setEndpointName] = useState<string>("");
  const [endpointPriority, setEndpointPriority] = useState<number>(1);
  const [savedEndpoints, setSavedEndpoints] = useState<EndpointDraft[]>([]);
  const [savedCompilerEndpoints, setSavedCompilerEndpoints] = useState<EndpointDraft[]>([]);
  const [apiKeyEnvTouched, setApiKeyEnvTouched] = useState(false);
  const [endpointNameTouched, setEndpointNameTouched] = useState(false);
  const [llmAdvancedOpen, setLlmAdvancedOpen] = useState(false);

  // Compiler endpoint form state
  const [compilerProviderSlug, setCompilerProviderSlug] = useState("");
  const [compilerApiType, setCompilerApiType] = useState<"openai" | "anthropic">("openai");
  const [compilerBaseUrl, setCompilerBaseUrl] = useState("");
  const [compilerApiKeyEnv, setCompilerApiKeyEnv] = useState("");
  const [compilerApiKeyValue, setCompilerApiKeyValue] = useState("");
  const [compilerModel, setCompilerModel] = useState("");
  const [compilerEndpointName, setCompilerEndpointName] = useState("");
  const [compilerModels, setCompilerModels] = useState<ListedModel[]>([]); // models fetched for compiler section

  // Edit endpoint modal (do not reuse the "add" form)
  const [editingOriginalName, setEditingOriginalName] = useState<string | null>(null);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const isEditingEndpoint = editModalOpen && editingOriginalName !== null;
  const [llmNextModalOpen, setLlmNextModalOpen] = useState(false);
  const [editDraft, setEditDraft] = useState<{
    name: string;
    priority: number;
    providerSlug: string;
    apiType: "openai" | "anthropic";
    baseUrl: string;
    apiKeyEnv: string;
    apiKeyValue: string; // optional; blank means don't change
    modelId: string;
    caps: string[];
  } | null>(null);
  const dragNameRef = useRef<string | null>(null);
  const [editModels, setEditModels] = useState<ListedModel[]>([]); // models fetched inside the edit modal

  // status panel data
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [endpointSummary, setEndpointSummary] = useState<
    { name: string; provider: string; apiType: string; baseUrl: string; model: string; keyEnv: string; keyPresent: boolean }[]
  >([]);
  const [skillSummary, setSkillSummary] = useState<{ count: number; systemCount: number; externalCount: number } | null>(null);
  const [skillsDetail, setSkillsDetail] = useState<
    { name: string; description: string; system: boolean; enabled?: boolean; tool_name?: string | null; category?: string | null; path?: string | null }[] | null
  >(null);
  const [skillsSelection, setSkillsSelection] = useState<Record<string, boolean>>({});
  const [skillsTouched, setSkillsTouched] = useState(false);
  const [secretShown, setSecretShown] = useState<Record<string, boolean>>({});
  const [autostartEnabled, setAutostartEnabled] = useState<boolean | null>(null);
  // autoStartBackend 已合并到"开机自启"：--background 模式自动拉起后端，无需独立开关
  const [serviceStatus, setServiceStatus] = useState<{ running: boolean; pid: number | null; pidFile: string } | null>(null);
  // 心跳状态机: "alive" | "suspect" | "degraded" | "dead"
  const [heartbeatState, setHeartbeatState] = useState<"alive" | "suspect" | "degraded" | "dead">("dead");
  const heartbeatStateRef = useRef<"alive" | "suspect" | "degraded" | "dead">("dead");
  const heartbeatFailCount = useRef(0);
  const [pageVisible, setPageVisible] = useState(true);
  const visibilityGraceRef = useRef(false); // 休眠恢复宽限期
  const [detectedProcesses, setDetectedProcesses] = useState<Array<{ pid: number; cmd: string }>>([]);
  const [serviceLog, setServiceLog] = useState<{ path: string; content: string; truncated: boolean } | null>(null);
  const [serviceLogError, setServiceLogError] = useState<string | null>(null);
  const [appVersion, setAppVersion] = useState<string>("");
  const [openakitaVersion, setOpenakitaVersion] = useState<string>("");

  // Health check state
  const [endpointHealth, setEndpointHealth] = useState<Record<string, {
    status: string; latencyMs: number | null; error: string | null; errorCategory: string | null;
    consecutiveFailures: number; cooldownRemaining: number; isExtendedCooldown: boolean; lastCheckedAt: string | null;
  }>>({});
  const [imHealth, setImHealth] = useState<Record<string, {
    status: string; error: string | null; lastCheckedAt: string | null;
  }>>({});
  const [healthChecking, setHealthChecking] = useState<string | null>(null); // "all" | endpoint name
  const [imChecking, setImChecking] = useState(false);

  // ── 端点连接测试（弹窗内，前端直连服务商 API，不依赖后端） ──
  const [connTesting, setConnTesting] = useState(false);
  const [connTestResult, setConnTestResult] = useState<{
    ok: boolean; latencyMs: number; error?: string; modelCount?: number;
  } | null>(null);

  // unified env draft (full coverage)
  const [envDraft, setEnvDraft] = useState<EnvMap>({});
  const envLoadedForWs = useRef<string | null>(null);

  async function refreshAll() {
    setError(null);
    const res = await invoke<PlatformInfo>("get_platform_info");
    setInfo(res);
    const ws = await invoke<WorkspaceSummary[]>("list_workspaces");
    setWorkspaces(ws);
    const cur = await invoke<string | null>("get_current_workspace_id");
    setCurrentWorkspaceId(cur);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        try {
          const v = await getVersion();
          if (!cancelled) {
            setAppVersion(v);
            setSelectedPypiVersion(v);
          }
        } catch {
          // ignore
        }
        await refreshAll();
        // ── Auto-detect step completion on startup ──
        if (!cancelled) {
          try {
            // Detect Python
            const cands = await invoke<PythonCandidate[]>("detect_python");
            if (!cancelled) {
              setPythonCandidates(cands);
              const firstUsable = cands.findIndex((c: PythonCandidate) => c.isUsable);
              setSelectedPythonIdx(firstUsable);
            }
          } catch { /* ignore */ }

          try {
            // Check if openakita is installed in venv
            const plat = await invoke<PlatformInfo>("get_platform_info");
            const vd = joinPath(plat.openakitaRootDir, "venv");
            const v = await invoke<string>("openakita_version", { venvDir: vd });
            if (!cancelled && v) {
              setOpenakitaInstalled(true);
              setOpenakitaVersion(v);
              setVenvStatus(`安装完成 (v${v})`);
              setVenvReady(true);
            }
          } catch { /* venv not found or openakita not installed */ }

          try {
            // Check if endpoints exist (use readWorkspaceFile which respects dataMode)
            const raw = await readWorkspaceFile("data/llm_endpoints.json");
            const parsed = JSON.parse(raw);
            const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
            if (!cancelled && eps.length > 0) {
              setSavedEndpoints(eps.map((e: any) => ({
                name: String(e?.name || ""), provider: String(e?.provider || ""),
                api_type: String(e?.api_type || ""), base_url: String(e?.base_url || ""),
                model: String(e?.model || ""), api_key_env: String(e?.api_key_env || ""),
                priority: Number(e?.priority || 1),
                max_tokens: Number(e?.max_tokens || 8192),
                context_window: Number(e?.context_window || 150000),
                timeout: Number(e?.timeout || 180),
                capabilities: Array.isArray(e?.capabilities) ? e.capabilities.map((x: any) => String(x)) : [],
              })));
            }
          } catch { /* ignore */ }

          // ── Auto-connect to local running service ──
          // 如果本地有 OpenAkita 服务在运行，自动连接并同步状态。
          // 版本不一致时仍然连接，由 checkVersionMismatch 负责提示用户。
          if (!cancelled) {
            try {
              const localUrl = "http://127.0.0.1:18900";
              const healthRes = await fetch(`${localUrl}/api/health`, { signal: AbortSignal.timeout(2000) });
              if (healthRes.ok && !cancelled) {
                const healthData = await healthRes.json();
                const svcVersion = healthData.version || "";
                setApiBaseUrl(localUrl);
                setServiceStatus({ running: true, pid: healthData.pid || null, pidFile: "" });
                if (svcVersion) setBackendVersion(svcVersion);
                // 加载完整状态（env + 端点列表 + 模型列表）
                try {
                  await refreshStatus("local", localUrl, true);
                } catch { /* ignore */ }
                // 自动执行一次 LLM 端点健康检测（后台、不阻塞）
                autoCheckEndpoints(localUrl);
                // 版本不一致时触发已有的 mismatch 警告（延迟执行，等 desktopVersion 加载完）
                if (svcVersion) {
                  setTimeout(() => checkVersionMismatch(svcVersion), 500);
                }
              }
            } catch { /* 服务未运行，正常情况 */ }
          }
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ── 页面可见性监听（休眠/睡眠恢复感知）──
  useEffect(() => {
    const handler = () => {
      const visible = !document.hidden;
      setPageVisible(visible);
      if (visible) {
        // 从 hidden 恢复：给 10 秒宽限期，前 2 次心跳失败不计
        visibilityGraceRef.current = true;
        heartbeatFailCount.current = 0;
        setTimeout(() => { visibilityGraceRef.current = false; }, 10000);
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, []);

  // ── 心跳轮询：三级状态机 + 防误判 ──
  useEffect(() => {
    // 只在有 workspace 且非配置向导中时启动心跳
    if (!currentWorkspaceId) return;

    const interval = pageVisible ? 5000 : 30000; // visible 5s, hidden 30s
    const timer = setInterval(async () => {
      // 自重启互锁：restartOverlay 期间暂停心跳
      if (restartOverlay) return;

      const effectiveBase = httpApiBase();
      try {
        const res = await fetch(`${effectiveBase}/api/health`, { signal: AbortSignal.timeout(3000) });
        if (res.ok) {
          heartbeatFailCount.current = 0;
          if (heartbeatStateRef.current !== "alive") {
            heartbeatStateRef.current = "alive";
            setHeartbeatState("alive");
            // 恢复时更新托盘状态
            try { await invoke("set_tray_backend_status", { status: "alive" }); } catch { /* ignore */ }
          }
          setServiceStatus(prev => prev ? { ...prev, running: true } : { running: true, pid: null, pidFile: "" });
          // 提取后端版本
          try {
            const data = await res.json();
            if (data.version) setBackendVersion(data.version);
          } catch { /* ignore */ }
        } else {
          throw new Error("non-ok");
        }
      } catch {
        // 宽限期内不计入
        if (visibilityGraceRef.current) return;

        heartbeatFailCount.current += 1;
        if (heartbeatFailCount.current < 3) {
          if (heartbeatStateRef.current !== "suspect") {
            heartbeatStateRef.current = "suspect";
            setHeartbeatState("suspect");
          }
          return;
        }

        // ── 二次确认：通过 Tauri 检查进程是否存活 ──
        if (dataMode !== "remote") {
          try {
            const alive = await invoke<boolean>("openakita_check_pid_alive", { workspaceId: currentWorkspaceId });
            if (alive) {
              // HTTP 不可达但进程存活 → DEGRADED（黄灯）
              if (heartbeatStateRef.current !== "degraded") {
                heartbeatStateRef.current = "degraded";
                setHeartbeatState("degraded");
                try { await invoke("set_tray_backend_status", { status: "degraded" }); } catch { /* ignore */ }
              }
              setServiceStatus(prev => prev ? { ...prev, running: true } : { running: true, pid: null, pidFile: "" });
              return;
            }
          } catch { /* invoke 失败，视为不可用 */ }
        }

        // 进程确认已死 → DEAD
        if (heartbeatStateRef.current !== "dead") {
          heartbeatStateRef.current = "dead";
          setHeartbeatState("dead");
          // 仅在状态实际变化时通知 Rust（避免重复系统通知）
          try { await invoke("set_tray_backend_status", { status: "dead" }); } catch { /* ignore */ }
        }
        setServiceStatus(prev => prev ? { ...prev, running: false } : { running: false, pid: null, pidFile: "" });
        setBackendVersion(null);
        // 注意：不要在 dead 状态下重置 heartbeatFailCount！
        // 否则下轮心跳 failCount 从 0 开始 → 进入 suspect → 再次变为 dead → 重复发送系统通知。
        // failCount 会在服务恢复 (alive) 时自动重置为 0（见上方 res.ok 分支）。
      }
    }, interval);

    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId, dataMode, apiBaseUrl, pageVisible, restartOverlay]);

  const venvDir = useMemo(() => {
    if (!info) return "";
    return joinPath(info.openakitaRootDir, "venv");
  }, [info]);

  // tray/menu bar -> open status panel
  useEffect(() => {
    let unlisten: null | (() => void) = null;
    (async () => {
      unlisten = await listen("open_status", async () => {
        setView("status");
        try {
          await refreshStatus();
        } catch {
          // ignore
        }
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWorkspaceId, venvDir]);

  // streaming pip logs (install step)
  useEffect(() => {
    let unlisten: null | (() => void) = null;
    (async () => {
      unlisten = await listen("pip_install_event", (ev) => {
        const p = ev.payload as any;
        if (!p || typeof p !== "object") return;
        if (p.kind === "stage") {
          const stage = String(p.stage || "");
          const percent = Number(p.percent || 0);
          if (stage) setInstallProgress({ stage, percent: Math.max(0, Math.min(100, percent)) });
          return;
        }
        if (p.kind === "line") {
          const text = String(p.text || "");
          if (!text) return;
          setInstallLiveLog((prev) => {
            const next = prev + text;
            // keep tail to avoid huge memory usage
            const max = 80_000;
            return next.length > max ? next.slice(next.length - max) : next;
          });
        }
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  // module install progress events → feed into detail log
  useEffect(() => {
    let unlisten: null | (() => void) = null;
    (async () => {
      unlisten = await listen("module-install-progress", (ev) => {
        const p = ev.payload as any;
        if (!p || typeof p !== "object") return;
        const msg = String(p.message || "");
        const status = String(p.status || "");
        const moduleId = String(p.moduleId || "");
        if (msg) {
          const prefix = status === "retrying" ? "🔄" : status === "error" ? "❌" : status === "done" ? "✅" : "📦";
          setObDetailLog(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${prefix} [${moduleId}] ${msg}`]);
        }
      });
    })();
    return () => { if (unlisten) unlisten(); };
  }, []);

  // tray quit failed: service still running
  useEffect(() => {
    let unlisten: null | (() => void) = null;
    (async () => {
      unlisten = await listen("quit_failed", async (ev) => {
        const p = ev.payload as any;
        const msg = String(p?.message || "退出失败：后台服务仍在运行。请先停止服务。");
        setView("status");
        setError(msg);
        try {
          await refreshStatus();
        } catch {
          // ignore
        }
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  const canUsePython = useMemo(() => {
    if (selectedPythonIdx < 0) return false;
    return pythonCandidates[selectedPythonIdx]?.isUsable ?? false;
  }, [pythonCandidates, selectedPythonIdx]);

  // Keep preset <-> index-url consistent
  useEffect(() => {
    const t = indexUrl.trim();
    if (pipIndexPresetId === "custom") {
      if (customIndexUrl !== indexUrl) setCustomIndexUrl(indexUrl);
      return;
    }
    const preset = PIP_INDEX_PRESETS.find((p) => p.id === pipIndexPresetId);
    const target = (preset?.url || "").trim();
    if (target !== t) setIndexUrl(preset?.url || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipIndexPresetId]);

  const done = useMemo(() => {
    const d = new Set<StepId>();
    if (info) d.add("welcome");
    if (currentWorkspaceId) d.add("workspace");
    if (canUsePython) d.add("python");
    if (openakitaInstalled) d.add("install");
    // LLM 步骤：只要工作区已有端点，就视为完成（允许用户跳过“拉模型/选模型/新增端点”）
    if (savedEndpoints.length > 0) d.add("llm");
    // Quick mode steps
    if (configMode === "quick") {
      if (savedEndpoints.length > 0) d.add("quick-form");
      if (currentWorkspaceId && canUsePython && openakitaInstalled) d.add("quick-setup");
    }
    // integrations/finish are completion-oriented; keep manual.
    return d;
  }, [info, currentWorkspaceId, canUsePython, openakitaInstalled, savedEndpoints.length, configMode]);

  // 当 done 集合更新时，自动推进 maxReachedStepIdx
  // 核心步骤（welcome ~ llm）全完成后，解锁所有后续步骤（IM/工具/Agent/完成都是可选的）
  useEffect(() => {
    const coreSteps: StepId[] = configMode === "quick"
      ? ["welcome", "quick-form", "quick-setup"]
      : ["welcome", "workspace", "python", "install", "llm"];
    const allCoreDone = coreSteps.every((id) => done.has(id));
    if (allCoreDone) {
      // 所有核心步骤完成 -> 解锁全部步骤
      setMaxReachedStepIdx((prev) => {
        const next = Math.max(prev, steps.length - 1);
        localStorage.setItem("openakita_maxStep", String(next));
        return next;
      });
    } else {
      // 否则，推进到最后一个连续完成步骤的下一步
      let maxDoneIdx = -1;
      for (let i = 0; i < steps.length; i++) {
        if (done.has(steps[i].id)) {
          maxDoneIdx = i;
        } else {
          break;
        }
      }
      if (maxDoneIdx >= 0) {
        const target = Math.min(maxDoneIdx + 1, steps.length - 1);
        setMaxReachedStepIdx((prev) => {
          const next = Math.max(prev, target);
          localStorage.setItem("openakita_maxStep", String(next));
          return next;
        });
      }
    }
  }, [done, steps]);

  // Keep boolean flags in sync with the visible status string (best-effort).
  useEffect(() => {
    if (!venvStatus) return;
    if (venvStatus.includes("venv 就绪")) setVenvReady(true);
    if (venvStatus.includes("安装完成")) setOpenakitaInstalled(true);
  }, [venvStatus]);

  async function ensureEnvLoaded(workspaceId: string): Promise<EnvMap> {
    if (envLoadedForWs.current === workspaceId) return envDraft;
    let parsed: EnvMap = {};

    if (shouldUseHttpApi()) {
      // ── 后端运行中 → HTTP API（读取后端实时 env）──
      try {
        const res = await safeFetch(`${httpApiBase()}/api/config/env`);
        const data = await res.json();
        parsed = data.env || {};
      } catch {
        // HTTP 暂时不可用（后端刚启动未就绪等），回退到本地读取
        if (workspaceId) {
          try {
            const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
            parsed = parseEnv(content);
          } catch { parsed = {}; }
        }
      }
    } else if (workspaceId) {
      // ── 后端未运行 → Tauri 本地读取 .env ──
      try {
        const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
        parsed = parseEnv(content);
      } catch { parsed = {}; }
    }
    // Set sensible defaults for first-time setup
    const defaults: Record<string, string> = {
      MCP_BROWSER_ENABLED: "true",
      DESKTOP_ENABLED: "true",
      MCP_ENABLED: "true",
    };
    for (const [dk, dv] of Object.entries(defaults)) {
      if (!(dk in parsed)) parsed[dk] = dv;
    }
    setEnvDraft(parsed);
    envLoadedForWs.current = workspaceId;
    return parsed;
  }

  async function doCreateWorkspace() {
    setBusy("创建工作区...");
    setError(null);
    try {
      const ws = await invoke<WorkspaceSummary>("create_workspace", {
        id: newWsId,
        name: newWsName.trim(),
        setCurrent: true,
      });
      await refreshAll();
      setCurrentWorkspaceId(ws.id);
      envLoadedForWs.current = null;
      setNotice(`已创建工作区：${ws.name}（${ws.id}）`);
    } finally {
      setBusy(null);
    }
  }

  async function doSetCurrentWorkspace(id: string) {
    setBusy("切换工作区...");
    setError(null);
    try {
      await invoke("set_current_workspace", { id });
      await refreshAll();
      envLoadedForWs.current = null;
      setNotice(`已切换当前工作区：${id}`);
    } finally {
      setBusy(null);
    }
  }

  async function doDetectPython() {
    setError(null);
    setBusy("检测系统 Python...");
    try {
      const cands = await invoke<PythonCandidate[]>("detect_python");
      setPythonCandidates(cands);
      const firstUsable = cands.findIndex((c) => c.isUsable);
      setSelectedPythonIdx(firstUsable);
      setNotice(firstUsable >= 0 ? "已找到可用 Python（3.11+）" : "未找到可用 Python（建议安装内置 Python）");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doInstallEmbeddedPython() {
    setError(null);
    setBusy("下载/安装内置 Python...");
    try {
      setVenvStatus("下载/安装内置 Python 中...");
      const r = await invoke<EmbeddedPythonInstallResult>("install_embedded_python", { pythonSeries: "3.11" });
      const cand: PythonCandidate = {
        command: r.pythonCommand,
        versionText: `embedded (${r.tag}): ${r.assetName}`,
        isUsable: true,
      };
      setPythonCandidates((prev) => [cand, ...prev.filter((p) => p.command.join(" ") !== cand.command.join(" "))]);
      setSelectedPythonIdx(0);
      setVenvStatus(`内置 Python 就绪：${r.pythonPath}`);
      setNotice("内置 Python 安装完成，可以继续创建 venv");
    } catch (e) {
      setError(String(e));
      setVenvStatus(`内置 Python 安装失败：${String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function doCreateVenv() {
    if (!canUsePython) return;
    setError(null);
    setBusy("创建 venv...");
    try {
      setVenvStatus("创建 venv 中...");
      const py = pythonCandidates[selectedPythonIdx].command;
      await invoke<string>("create_venv", { pythonCommand: py, venvDir });
      setVenvStatus(`venv 就绪：${venvDir}`);
      setVenvReady(true);
      setOpenakitaInstalled(false);
      setNotice("venv 已准备好，可以安装 openakita");
    } catch (e) {
      setError(String(e));
      setVenvStatus(`创建 venv 失败：${String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function doFetchPypiVersions() {
    setPypiVersionsLoading(true);
    setPypiVersions([]);
    try {
      const raw = await invoke<string>("fetch_pypi_versions", {
        package: "openakita",
        indexUrl: indexUrl.trim() ? indexUrl.trim() : null,
      });
      const list = JSON.parse(raw) as string[];
      setPypiVersions(list);
      // Auto-select: match Setup Center version if available
      if (appVersion && list.includes(appVersion)) {
        setSelectedPypiVersion(appVersion);
      } else if (list.length > 0) {
        setSelectedPypiVersion(list[0]); // latest
      }
    } catch (e: any) {
      setError(`获取 PyPI 版本列表失败：${e}`);
    } finally {
      setPypiVersionsLoading(false);
    }
  }

  async function doSetupVenvAndInstallOpenAkita() {
    if (!canUsePython) {
      setError("请先在 Python 步骤安装/检测并选择一个可用 Python（3.11+）。");
      return;
    }
    setError(null);
    setNotice(null);
    setInstallLiveLog("");
    setInstallProgress({ stage: "准备开始", percent: 1 });
    setBusy("创建 venv 并安装 openakita...");
    try {
      // 1) create venv (idempotent)
      setInstallProgress({ stage: "创建 venv", percent: 10 });
      setVenvStatus("创建 venv 中...");
      const py = pythonCandidates[selectedPythonIdx].command;
      await invoke<string>("create_venv", { pythonCommand: py, venvDir });
      setVenvReady(true);
      setOpenakitaInstalled(false);
      setVenvStatus(`venv 就绪：${venvDir}`);
      setInstallProgress({ stage: "venv 就绪", percent: 30 });

      // 2) pip install
      setInstallProgress({ stage: "pip 安装", percent: 35 });
      setVenvStatus("安装 openakita 中（pip）...");
      setInstallLog("");
      const ex = extras.trim();
      const extrasPart = ex ? `[${ex}]` : "";
      const spec = (() => {
        if (installSource === "github") {
          const repo = githubRepo.trim() || "openakita/openakita";
          const ref = githubRef.trim() || "main";
          const kind = githubRefType;
          const url =
            kind === "tag"
              ? `https://github.com/${repo}/archive/refs/tags/${ref}.zip`
              : `https://github.com/${repo}/archive/refs/heads/${ref}.zip`;
          return `openakita${extrasPart} @ ${url}`;
        }
        if (installSource === "local") {
          const p = localSourcePath.trim();
          if (!p) {
            throw new Error("请选择/填写本地源码路径（例如本仓库根目录）");
          }
          const url = toFileUrl(p);
          if (!url) {
            throw new Error("本地路径无效");
          }
          return `openakita${extrasPart} @ ${url}`;
        }
        // PyPI mode: append ==version if a specific version is selected
        const ver = selectedPypiVersion.trim();
        if (ver) {
          return `openakita${extrasPart}==${ver}`;
        }
        return `openakita${extrasPart}`;
      })();
      const log = await invoke<string>("pip_install", {
        venvDir,
        packageSpec: spec,
        indexUrl: indexUrl.trim() ? indexUrl.trim() : null,
      });
      setInstallLog(String(log || ""));
      setOpenakitaInstalled(true);
      setVenvStatus(`安装完成：${spec}`);
      setInstallProgress({ stage: "安装完成", percent: 100 });
      setNotice("openakita 已安装，可以读取服务商列表并配置端点");

      // 3) verify by attempting to list providers (makes failures visible early)
      try {
        await doLoadProviders();
      } catch {
        // ignore; doLoadProviders already sets error
      }
    } catch (e) {
      const msg = String(e);
      setError(msg);
      setVenvStatus(`安装失败：${msg}`);
      setInstallLog("");
      if (msg.includes("缺少 Setup Center 所需模块") || msg.includes("No module named 'openakita.setup_center'")) {
        setNotice("你安装到的 openakita 不包含 Setup Center 模块。建议切换“安装来源”为 GitHub 或 本地源码，然后重新安装。");
      }
    } finally {
      setBusy(null);
    }
  }

  async function doLoadProviders() {
    setError(null);
    setBusy("读取服务商列表...");
    try {
      let parsed: ProviderInfo[] = [];

      if (shouldUseHttpApi()) {
        // ── 后端运行中 → HTTP API（获取后端实时的 provider 列表）──
        try {
          const res = await safeFetch(`${httpApiBase()}/api/config/providers`, { signal: AbortSignal.timeout(5000) });
          const data = await res.json();
          parsed = Array.isArray(data.providers) ? data.providers : Array.isArray(data) ? data : [];
        } catch {
          parsed = BUILTIN_PROVIDERS; // 后端旧版本不支持此 API，回退
        }
      } else {
        // ── 后端未运行 → Tauri invoke，失败则用内置列表 ──
        try {
          const raw = await invoke<string>("openakita_list_providers", { venvDir });
          parsed = JSON.parse(raw) as ProviderInfo[];
        } catch {
          parsed = BUILTIN_PROVIDERS;
        }
      }

      if (parsed.length === 0) parsed = BUILTIN_PROVIDERS;
      setProviders(parsed);
      const first = parsed[0]?.slug ?? "";
      setProviderSlug((prev) => prev || first);
      setError(null);

      // 非关键：获取版本号（仅后端未运行时尝试 venv 方式）
      if (!shouldUseHttpApi()) {
        try {
          const v = await invoke<string>("openakita_version", { venvDir });
          setOpenakitaVersion(v || "");
        } catch {
          setOpenakitaVersion("");
        }
      }
    } catch (e) {
      console.warn("doLoadProviders failed:", e);
      if (providers.length === 0) {
        setProviders(BUILTIN_PROVIDERS);
        const first = BUILTIN_PROVIDERS[0]?.slug ?? "";
        setProviderSlug((prev) => prev || first);
        setError(null);
      }
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (!selectedProvider) return;
    const t = (selectedProvider.api_type as "openai" | "anthropic") || "openai";
    setApiType(t);
    setBaseUrl(selectedProvider.default_base_url || "");
    const suggested = selectedProvider.api_key_env_suggestion || envKeyFromSlug(selectedProvider.slug);
    const used = new Set(Object.keys(envDraft || {}));
    for (const ep of savedEndpoints) {
      if (ep.api_key_env) used.add(ep.api_key_env);
    }
    // When provider changes, auto-switch env var name to match provider (unless user manually edited it).
    if (!apiKeyEnvTouched) {
      setApiKeyEnv(nextEnvKeyName(suggested, used));
    }
    // Endpoint name should follow provider+model by default (unless user manually edited it).
    const autoName = suggestEndpointName(selectedProvider.slug, selectedModelId);
    if (!endpointNameTouched) {
      setEndpointName(autoName);
    }
  }, [selectedProvider, selectedModelId, envDraft, savedEndpoints, apiKeyEnvTouched, endpointNameTouched]);

  // When user switches provider via dropdown, reset auto-naming to follow the new provider.
  useEffect(() => {
    if (!providerSlug) return;
    if (editModalOpen) return;
    setApiKeyEnvTouched(false);
    setEndpointNameTouched(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerSlug]);

  async function doFetchModels() {
    setError(null);
    setModels([]);
    setSelectedModelId(""); // clear search / selection
    setBusy("拉取模型列表...");
    try {
      console.log('[doFetchModels] apiType:', apiType, 'baseUrl:', baseUrl, 'slug:', selectedProvider?.slug, 'keyLen:', apiKeyValue?.length, 'httpApi:', shouldUseHttpApi());
      const parsed = await fetchModelListUnified({
        apiType,
        baseUrl,
        providerSlug: selectedProvider?.slug ?? null,
        apiKey: apiKeyValue,
      });
      setModels(parsed);
      // 不要默认选中/填入任何模型，避免“自动出现一个搜索结果”造成误导
      setSelectedModelId("");
      setNotice(`拉取到模型：${parsed.length} 个`);
      setCapTouched(false);
    } catch (e: any) {
      console.error('[doFetchModels] error:', e);
      const msg = String(e?.message || e);
      setError(msg);
    } finally {
      setBusy(null);
    }
  }

  /**
   * 测试端点连接（路由原则同上）：
   *   后端运行中 → 走后端 /api/config/list-models，验证后端与配置参数的兼容性
   *   后端未运行 → 前端直连服务商 /models API，仅验证 API Key 和地址有效性
   */
  async function doTestConnection(params: {
    testApiType: string; testBaseUrl: string; testApiKey: string; testProviderSlug?: string | null;
  }) {
    setConnTesting(true);
    setConnTestResult(null);
    const t0 = performance.now();
    try {
      let modelCount = 0;
      if (shouldUseHttpApi()) {
        // ── 后端运行中 → 走后端 API（验证后端兼容性 + 热加载）──
        const base = httpApiBase();
        const res = await safeFetch(`${base}/api/config/list-models`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            api_type: params.testApiType,
            base_url: params.testBaseUrl,
            provider_slug: params.testProviderSlug || null,
            api_key: params.testApiKey,
          }),
          signal: AbortSignal.timeout(30_000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        const models = Array.isArray(data.models) ? data.models : (Array.isArray(data) ? data : []);
        modelCount = models.length;
      } else {
        // ── 后端未运行 → 前端直连服务商 API ──
        const result = await fetchModelsDirectly({
          apiType: params.testApiType,
          baseUrl: params.testBaseUrl,
          providerSlug: params.testProviderSlug ?? null,
          apiKey: params.testApiKey,
        });
        modelCount = result.length;
      }
      const latency = Math.round(performance.now() - t0);
      setConnTestResult({ ok: true, latencyMs: latency, modelCount });
    } catch (e) {
      const latency = Math.round(performance.now() - t0);
      let errMsg = String(e);
      if (errMsg.includes("Failed to fetch") || errMsg.includes("NetworkError")) {
        errMsg = t("llm.testNetworkError");
      } else if (errMsg.includes("401") || errMsg.includes("Unauthorized")) {
        errMsg = t("llm.testAuthError");
      } else if (errMsg.includes("403") || errMsg.includes("Forbidden")) {
        errMsg = t("llm.testForbidden");
      } else if (errMsg.includes("404")) {
        errMsg = t("llm.testNotFound");
      } else if (errMsg.includes("timeout") || errMsg.includes("AbortError")) {
        errMsg = t("llm.testTimeout");
      }
      setConnTestResult({ ok: false, latencyMs: latency, error: errMsg });
    } finally {
      setConnTesting(false);
    }
  }

  /**
   * 通用模型列表拉取（路由原则同上）：
   *   后端运行中 → 必须走后端 HTTP API（验证后端兼容性，capability 推断更精确）
   *   后端未运行 → 本地回退链：Tauri invoke → 前端直连服务商 API
   *
   * ⚠ 维护提示：前端直连 fallback 使用 fetchModelsDirectly()，
   *   其 capability 推断是 Python 端 infer_capabilities() 的简化版。
   *   如需更精确的推断，服务启动后会自动走后端路径。
   */
  async function fetchModelListUnified(params: {
    apiType: string; baseUrl: string; providerSlug: string | null; apiKey: string;
  }): Promise<ListedModel[]> {
    // ── 后端运行中 → HTTP API ──
    console.log('[fetchModelListUnified] shouldUseHttpApi:', shouldUseHttpApi(), 'httpApiBase:', httpApiBase());
    if (shouldUseHttpApi()) {
      console.log('[fetchModelListUnified] using HTTP API');
      const res = await safeFetch(`${httpApiBase()}/api/config/list-models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_type: params.apiType,
          base_url: params.baseUrl,
          provider_slug: params.providerSlug || null,
          api_key: params.apiKey,
        }),
        signal: AbortSignal.timeout(30_000),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      return Array.isArray(data.models) ? data.models : data;
    }
    // ── 后端未运行 → 本地回退 ──
    // 回退 1：Tauri invoke → Python bridge（开发模式 / 有 venv 时）
    try {
      const raw = await invoke<string>("openakita_list_models", {
        venvDir,
        apiType: params.apiType,
        baseUrl: params.baseUrl,
        providerSlug: params.providerSlug,
        apiKey: params.apiKey,
      });
      return JSON.parse(raw) as ListedModel[];
    } catch (e) {
      console.warn("openakita_list_models via Python bridge failed, using direct fetch:", e);
    }
    // 回退 2：前端直连服务商 API（打包模式，无 venv，onboarding 阶段）
    return fetchModelsDirectly(params);
  }

  // When selected model changes, default capabilities from fetched model unless user manually edited.
  useEffect(() => {
    if (capTouched) return;
    const caps = models.find((m) => m.id === selectedModelId)?.capabilities ?? {};
    const list = Object.entries(caps)
      .filter(([, v]) => v)
      .map(([k]) => k);
    setCapSelected(list.length ? list : ["text"]);
  }, [selectedModelId, models, capTouched]);

  async function loadSavedEndpoints() {
    if (!currentWorkspaceId && dataMode !== "remote") {
      setSavedEndpoints([]);
      setSavedCompilerEndpoints([]);
      return;
    }
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      const parsed = raw ? JSON.parse(raw) : { endpoints: [] };
      const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
      const list: EndpointDraft[] = eps
        .map((e: any) => ({
          name: String(e?.name || ""),
          provider: String(e?.provider || ""),
          api_type: String(e?.api_type || ""),
          base_url: String(e?.base_url || ""),
          api_key_env: String(e?.api_key_env || ""),
          model: String(e?.model || ""),
          priority: Number.isFinite(Number(e?.priority)) ? Number(e?.priority) : 999,
          max_tokens: Number.isFinite(Number(e?.max_tokens)) ? Number(e?.max_tokens) : 8192,
          context_window: Number.isFinite(Number(e?.context_window)) ? Number(e?.context_window) : 150000,
          timeout: Number.isFinite(Number(e?.timeout)) ? Number(e?.timeout) : 180,
          capabilities: Array.isArray(e?.capabilities) ? e.capabilities.map((x: any) => String(x)) : [],
          note: e?.note ? String(e.note) : null,
        }))
        .filter((e: any) => e.name);
      list.sort((a, b) => a.priority - b.priority);
      setSavedEndpoints(list);

      const maxP = list.reduce((m, e) => Math.max(m, Number.isFinite(e.priority) ? e.priority : 0), 0);
      // 用户希望“从主模型开始”：当没有端点时默认 priority=1；否则默认填最后一个+1。
      // 并且删除端点后应立刻回收/重算，不要沿用删除前的累加值。
      if (!isEditingEndpoint) {
        setEndpointPriority(list.length === 0 ? 1 : maxP + 1);
      }

      // Load compiler endpoints
      const compilerEps: EndpointDraft[] = (Array.isArray(parsed?.compiler_endpoints) ? parsed.compiler_endpoints : [])
        .filter((e: any) => e?.name)
        .map((e: any) => ({
          name: String(e.name || ""),
          provider: String(e.provider || ""),
          api_type: String(e.api_type || "openai"),
          base_url: String(e.base_url || ""),
          api_key_env: String(e.api_key_env || ""),
          model: String(e.model || ""),
          priority: Number.isFinite(Number(e.priority)) ? Number(e.priority) : 1,
          max_tokens: Number.isFinite(Number(e.max_tokens)) ? Number(e.max_tokens) : 2048,
          context_window: Number.isFinite(Number(e.context_window)) ? Number(e.context_window) : 150000,
          timeout: Number.isFinite(Number(e.timeout)) ? Number(e.timeout) : 30,
          capabilities: Array.isArray(e.capabilities) ? e.capabilities.map((x: any) => String(x)) : ["text"],
          note: e.note ? String(e.note) : null,
        }))
        .sort((a: EndpointDraft, b: EndpointDraft) => a.priority - b.priority);
      setSavedCompilerEndpoints(compilerEps);
    } catch {
      setSavedEndpoints([]);
      setSavedCompilerEndpoints([]);
    }
  }

  async function readEndpointsJson(): Promise<{ endpoints: any[]; settings: any }> {
    if (!currentWorkspaceId && !shouldUseHttpApi()) return { endpoints: [], settings: {} };
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      const parsed = raw ? JSON.parse(raw) : { endpoints: [], settings: {} };
      const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
      const settings = parsed?.settings && typeof parsed.settings === "object" ? parsed.settings : {};
      return { endpoints: eps, settings };
    } catch {
      return { endpoints: [], settings: {} };
    }
  }

  async function writeEndpointsJson(endpoints: any[], settings: any) {
    // readWorkspaceFile and writeWorkspaceFile already do HTTP-first internally
    let existing: any = {};
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      existing = raw ? JSON.parse(raw) : {};
    } catch { /* ignore */ }
    const base = { ...existing, endpoints, settings: settings || {} };
    const next = JSON.stringify(base, null, 2) + "\n";
    await writeWorkspaceFile("data/llm_endpoints.json", next);
  }

  // ── 配置读写路由 ──
  // 路由原则：
  //   后端运行中 (serviceStatus?.running) 或远程模式 → 必须走 HTTP API（后端负责持久化 + 热加载）
  //   后端未运行 → 走本地 Tauri Rust 操作（直接读写工作区文件）
  // 这样保证：
  //   1. 后端运行时，所有读写经过后端，确保配置兼容性和即时生效
  //   2. 后端未运行时（onboarding / 首次配置），直接操作本地文件，服务启动后自动加载

  /** 判断当前是否应走后端 HTTP API */
  function shouldUseHttpApi(): boolean {
    return dataMode === "remote" || !!serviceStatus?.running;
  }

  function httpApiBase(): string {
    return dataMode === "remote" ? apiBaseUrl : "http://127.0.0.1:18900";
  }

  async function readWorkspaceFile(relativePath: string): Promise<string> {
    // ── 后端运行中 → 优先 HTTP API（读取后端内存中的实时状态）──
    if (shouldUseHttpApi()) {
      try {
        const base = httpApiBase();
        if (relativePath === "data/llm_endpoints.json") {
          const res = await safeFetch(`${base}/api/config/endpoints`);
          const data = await res.json();
          return JSON.stringify(data.raw || { endpoints: data.endpoints || [] });
        }
        if (relativePath === "data/skills.json") {
          const res = await safeFetch(`${base}/api/config/skills`);
          const data = await res.json();
          return JSON.stringify(data.skills || {});
        }
        if (relativePath === ".env") {
          const res = await safeFetch(`${base}/api/config/env`);
          const data = await res.json();
          return data.raw || "";
        }
      } catch {
        // HTTP 暂时不可用 — 回退到本地读取（比如后端正在重启、状态延迟）
        console.warn(`readWorkspaceFile: HTTP failed for ${relativePath}, falling back to Tauri`);
      }
    }
    // ── 后端未运行 / HTTP 回退 → Tauri 本地读取 ──
    if (currentWorkspaceId) {
      return invoke<string>("workspace_read_file", { workspaceId: currentWorkspaceId, relativePath });
    }
    throw new Error(`读取配置失败：服务未运行且无本地工作区 (${relativePath})`);
  }

  async function writeWorkspaceFile(relativePath: string, content: string): Promise<void> {
    // ── 后端运行中 → 优先 HTTP API（后端负责持久化 + 热加载）──
    if (shouldUseHttpApi()) {
      try {
        const base = httpApiBase();
        if (relativePath === "data/llm_endpoints.json") {
          await safeFetch(`${base}/api/config/endpoints`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: JSON.parse(content) }),
          });
          triggerConfigReload().catch(() => {});
          return;
        }
        if (relativePath === "data/skills.json") {
          await safeFetch(`${base}/api/config/skills`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: JSON.parse(content) }),
          });
          return;
        }
      } catch {
        // HTTP 暂时不可用 — 回退到本地写入（比如后端正在重启）
        console.warn(`writeWorkspaceFile: HTTP failed for ${relativePath}, falling back to Tauri`);
      }
    }
    // ── 后端未运行 / HTTP 回退 → Tauri 本地写入 ──
    if (currentWorkspaceId) {
      await invoke("workspace_write_file", { workspaceId: currentWorkspaceId, relativePath, content });
      return;
    }
    throw new Error(`写入配置失败：服务未运行且无本地工作区 (${relativePath})`);
  }

  /**
   * 通知运行中的后端热重载配置。
   * 仅在后端运行时调用有意义；后端未运行时静默跳过。
   */
  async function triggerConfigReload(): Promise<void> {
    if (!shouldUseHttpApi()) return; // 后端未运行，无需热加载
    try {
      await safeFetch(`${httpApiBase()}/api/config/reload`, { method: "POST", signal: AbortSignal.timeout(3000) });
    } catch { /* reload not supported or transient error — that's ok */ }
  }

  /**
   * 保存 .env 配置后触发服务重启，并轮询等待服务恢复。
   * 如果服务未运行，仅保存不重启并提示。
   */
  async function applyAndRestart(keys: string[]): Promise<void> {
    const base = httpApiBase();
    setError(null);
    setRestartOverlay({ phase: "saving" });

    try {
      // Step 1: 保存配置
      await saveEnvKeys(keys);

      // Step 1.5: 自动安装已启用 IM 通道缺失的依赖（非阻塞，失败不影响重启）
      if (venvDir && currentWorkspaceId) {
        try {
          await invoke("openakita_ensure_channel_deps", {
            venvDir,
            workspaceId: currentWorkspaceId,
          });
        } catch { /* 非关键步骤，失败不影响流程 */ }
      }

      // Step 2: 检测服务是否运行
      let alive = false;
      try {
        const ping = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(2000) });
        alive = ping.ok;
      } catch { alive = false; }

      if (!alive) {
        // 服务未运行，仅保存
        setRestartOverlay({ phase: "notRunning" });
        setTimeout(() => {
          setRestartOverlay(null);
          setNotice(t("config.restartNotRunning"));
        }, 2000);
        return;
      }

      // Step 3: 触发重启
      setRestartOverlay({ phase: "restarting" });
      try {
        await fetch(`${base}/api/config/restart`, { method: "POST", signal: AbortSignal.timeout(3000) });
      } catch { /* 请求可能因服务关闭而失败，这是预期的 */ }

      // Step 4: 等待服务关闭 (短暂延迟)
      await new Promise((r) => setTimeout(r, 1500));

      // Step 5: 轮询等待服务恢复
      setRestartOverlay({ phase: "waiting" });
      const maxWait = 30_000; // 最多等 30 秒
      const pollInterval = 1000;
      const startTime = Date.now();
      let recovered = false;

      while (Date.now() - startTime < maxWait) {
        await new Promise((r) => setTimeout(r, pollInterval));
        try {
          const res = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(2000) });
          if (res.ok) {
            recovered = true;
            // 更新后端版本
            try {
              const data = await res.json();
              if (data.version) setBackendVersion(data.version);
            } catch { /* ignore */ }
            break;
          }
        } catch { /* 还没恢复，继续等 */ }
      }

      if (recovered) {
        setRestartOverlay({ phase: "done" });
        setServiceStatus((prev) =>
          prev ? { ...prev, running: true } : { running: true, pid: null, pidFile: "" }
        );
        // 刷新配置数据
        try { await refreshStatus(undefined, undefined, true); } catch { /* ignore */ }
        // 重启后重新检测端点健康状态
        autoCheckEndpoints(apiBaseUrl);
        setTimeout(() => {
          setRestartOverlay(null);
          setNotice(t("config.restartSuccess"));
        }, 1200);
      } else {
        setRestartOverlay({ phase: "fail" });
        setTimeout(() => {
          setRestartOverlay(null);
          setError(t("config.restartFail"));
        }, 2500);
      }
    } catch (e) {
      setRestartOverlay(null);
      setError(String(e));
    }
  }

  function normalizePriority(n: any, fallback: number) {
    const x = Number(n);
    if (!Number.isFinite(x) || x <= 0) return fallback;
    return Math.floor(x);
  }

  async function doFetchCompilerModels() {
    if (!compilerApiKeyValue.trim()) {
      setError("请先填写编译端点的 API Key 值");
      return;
    }
    if (!compilerBaseUrl.trim()) {
      setError("请先填写编译端点的 Base URL");
      return;
    }
    setError(null);
    setCompilerModels([]);
    setBusy("拉取编译端点模型列表...");
    try {
      const parsed = await fetchModelListUnified({
        apiType: compilerApiType,
        baseUrl: compilerBaseUrl,
        providerSlug: compilerProviderSlug || null,
        apiKey: compilerApiKeyValue,
      });
      setCompilerModels(parsed);
      setCompilerModel("");
      setNotice(`编译端点拉取到模型：${parsed.length} 个`);
    } catch (e: any) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doSaveCompilerEndpoint() {
    if (!currentWorkspaceId && dataMode !== "remote") {
      setError("请先创建/选择一个当前工作区");
      return;
    }
    if (!compilerModel.trim()) {
      setError("请填写编译模型名称");
      return;
    }
    if (!compilerApiKeyEnv.trim()) {
      setError("请填写编译端点的 API Key 环境变量名");
      return;
    }
    if (!compilerApiKeyValue.trim()) {
      setError("请填写编译端点的 API Key 值");
      return;
    }
    setBusy("写入编译端点...");
    setError(null);
    try {
      // Write API key to .env — 遵循路由原则
      const compilerEnvPayload = { entries: { [compilerApiKeyEnv.trim()]: compilerApiKeyValue.trim() } };
      if (shouldUseHttpApi()) {
        try {
          await safeFetch(`${httpApiBase()}/api/config/env`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(compilerEnvPayload),
          });
        } catch {
          if (currentWorkspaceId) {
            await invoke("workspace_update_env", {
              workspaceId: currentWorkspaceId,
              entries: [{ key: compilerApiKeyEnv.trim(), value: compilerApiKeyValue.trim() }],
            });
          }
        }
      } else if (currentWorkspaceId) {
        await invoke("workspace_update_env", {
          workspaceId: currentWorkspaceId,
          entries: [{ key: compilerApiKeyEnv.trim(), value: compilerApiKeyValue.trim() }],
        });
      }
      setEnvDraft((e) => envSet(e, compilerApiKeyEnv.trim(), compilerApiKeyValue.trim()));

      // Read existing JSON
      let currentJson = "";
      try {
        currentJson = await readWorkspaceFile("data/llm_endpoints.json");
      } catch { currentJson = ""; }
      const base = currentJson ? JSON.parse(currentJson) : { endpoints: [], settings: {} };
      base.compiler_endpoints = Array.isArray(base.compiler_endpoints) ? base.compiler_endpoints : [];

      const baseName = (compilerEndpointName.trim() || `compiler-${compilerProviderSlug || "provider"}-${compilerModel.trim()}`).slice(0, 64);
      const usedNames = new Set(base.compiler_endpoints.map((e: any) => String(e?.name || "")).filter(Boolean));
      let name = baseName;
      if (usedNames.has(name)) {
        for (let i = 2; i < 10; i++) {
          const n = `${baseName}-${i}`.slice(0, 64);
          if (!usedNames.has(n)) { name = n; break; }
        }
      }

      const endpoint = {
        name,
        provider: compilerProviderSlug || "custom",
        api_type: compilerApiType,
        base_url: compilerBaseUrl,
        api_key_env: compilerApiKeyEnv.trim(),
        model: compilerModel.trim(),
        priority: base.compiler_endpoints.length + 1,
        max_tokens: 2048,
        context_window: 150000,
        timeout: 30,
        capabilities: ["text"],
      };
      base.compiler_endpoints.push(endpoint);
      base.compiler_endpoints.sort((a: any, b: any) => (Number(a?.priority) || 999) - (Number(b?.priority) || 999));

      await writeWorkspaceFile("data/llm_endpoints.json", JSON.stringify(base, null, 2) + "\n");

      // Reset form
      setCompilerModel("");
      setCompilerApiKeyValue("");
      setCompilerEndpointName("");
      setCompilerBaseUrl("");
      setNotice(`编译端点 ${name} 已保存`);
      await loadSavedEndpoints();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doDeleteCompilerEndpoint(epName: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    setBusy("删除编译端点...");
    setError(null);
    try {
      let currentJson = "";
      try {
        currentJson = await readWorkspaceFile("data/llm_endpoints.json");
      } catch { currentJson = ""; }
      const base = currentJson ? JSON.parse(currentJson) : { endpoints: [], settings: {} };
      base.compiler_endpoints = Array.isArray(base.compiler_endpoints) ? base.compiler_endpoints : [];
      base.compiler_endpoints = base.compiler_endpoints
        .filter((e: any) => String(e?.name || "") !== epName)
        .map((e: any, i: number) => ({ ...e, priority: i + 1 }));

      await writeWorkspaceFile("data/llm_endpoints.json", JSON.stringify(base, null, 2) + "\n");

      // Immediately update local state (don't rely solely on re-read which may be stale in remote mode)
      setSavedCompilerEndpoints((prev) => prev.filter((e) => e.name !== epName));
      setNotice(`编译端点 ${epName} 已删除`);

      // Also re-read to sync fully (background)
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doReorderByNames(orderedNames: string[]) {
    if (!currentWorkspaceId) return;
    setError(null);
    setBusy("保存排序...");
    try {
      const { endpoints, settings } = await readEndpointsJson();
      const map = new Map<string, any>();
      for (const e of endpoints) {
        const name = String(e?.name || "");
        if (name) map.set(name, e);
      }
      const nextEndpoints: any[] = [];
      let p = 1;
      for (const name of orderedNames) {
        const e = map.get(name);
        if (!e) continue;
        e.priority = p++;
        nextEndpoints.push(e);
        map.delete(name);
      }
      // append leftovers (if any) preserving original order, after the explicit list
      for (const e of endpoints) {
        const name = String(e?.name || "");
        if (!name) continue;
        if (map.has(name)) {
          const ee = map.get(name);
          ee.priority = p++;
          nextEndpoints.push(ee);
          map.delete(name);
        }
      }
      await writeEndpointsJson(nextEndpoints, settings);
      setNotice("已保存端点顺序（priority 已更新）");
      await loadSavedEndpoints();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doSetPrimaryEndpoint(name: string) {
    const names = savedEndpoints.map((e) => e.name);
    const idx = names.indexOf(name);
    if (idx < 0) return;
    const next = [name, ...names.filter((n) => n !== name)];
    await doReorderByNames(next);
  }

  async function doStartEditEndpoint(name: string) {
    const ep = savedEndpoints.find((e) => e.name === name);
    if (!ep) return;
    // Ensure env variables are loaded so API Key values are available in the edit modal
    if (currentWorkspaceId) {
      await ensureEnvLoaded(currentWorkspaceId);
    } else if (dataMode === "remote") {
      await ensureEnvLoaded("__remote__");
    }
    setEditingOriginalName(name);
    setEditDraft({
      name: ep.name,
      priority: normalizePriority(ep.priority, 1),
      providerSlug: ep.provider || "",
      apiType: (ep.api_type as any) || "openai",
      baseUrl: ep.base_url || "",
      apiKeyEnv: ep.api_key_env || "",
      apiKeyValue: "",
      modelId: ep.model || "",
      caps: Array.isArray(ep.capabilities) && ep.capabilities.length ? ep.capabilities : ["text"],
    });
    setEditModalOpen(true);
    setConnTestResult(null);
    setNotice(null);
  }

  function resetEndpointEditor() {
    setEditingOriginalName(null);
    setEditDraft(null);
    setEditModalOpen(false);
    setEditModels([]);
    setSecretShown((m) => ({ ...m, __EDIT_EP_KEY: false }));
  }

  async function doFetchEditModels() {
    if (!editDraft) return;
    const key = editDraft.apiKeyValue.trim() || envGet(envDraft, editDraft.apiKeyEnv);
    if (!key) {
      setError("请先填写 API Key 值（或确保对应环境变量已有值）");
      return;
    }
    if (!editDraft.baseUrl.trim()) {
      setError("请先填写 Base URL");
      return;
    }
    setError(null);
    setBusy("拉取模型列表...");
    try {
      const parsed = await fetchModelListUnified({
        apiType: editDraft.apiType,
        baseUrl: editDraft.baseUrl,
        providerSlug: editDraft.providerSlug || null,
        apiKey: key,
      });
      setEditModels(parsed);
      setNotice(`拉取到模型：${parsed.length} 个`);
    } catch (e: any) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doSaveEditedEndpoint() {
    if (!currentWorkspaceId) {
      setError("请先创建/选择一个当前工作区");
      return;
    }
    if (!editDraft || !editingOriginalName) return;
    if (!editDraft.name.trim()) {
      setError("端点名称不能为空");
      return;
    }
    if (!editDraft.modelId.trim()) {
      setError("模型不能为空");
      return;
    }
    if (!editDraft.apiKeyEnv.trim()) {
      setError("API Key 环境变量名不能为空");
      return;
    }
    setBusy("保存修改...");
    setError(null);
    try {
      // Update env only if user provided a value (avoid accidental overwrite)
      if (editDraft.apiKeyValue.trim()) {
        await ensureEnvLoaded(currentWorkspaceId);
        setEnvDraft((e) => envSet(e, editDraft.apiKeyEnv.trim(), editDraft.apiKeyValue.trim()));
        const envPayload = { entries: { [editDraft.apiKeyEnv.trim()]: editDraft.apiKeyValue.trim() } };
        if (shouldUseHttpApi()) {
          try {
            await safeFetch(`${httpApiBase()}/api/config/env`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(envPayload),
            });
          } catch {
            // HTTP 回退
            if (currentWorkspaceId) {
              await invoke("workspace_update_env", {
                workspaceId: currentWorkspaceId,
                entries: [{ key: editDraft.apiKeyEnv.trim(), value: editDraft.apiKeyValue.trim() }],
              });
            }
          }
        } else if (currentWorkspaceId) {
          await invoke("workspace_update_env", {
            workspaceId: currentWorkspaceId,
            entries: [{ key: editDraft.apiKeyEnv.trim(), value: editDraft.apiKeyValue.trim() }],
          });
        }
      }

      const { endpoints, settings } = await readEndpointsJson();
      const used = new Set(endpoints.map((e: any) => String(e?.name || "")).filter(Boolean));
      if (editDraft.name.trim() !== editingOriginalName && used.has(editDraft.name.trim())) {
        throw new Error(`端点名称已存在：${editDraft.name.trim()}（请换一个）`);
      }
      const idx = endpoints.findIndex((e: any) => String(e?.name || "") === editingOriginalName);
      // 编辑时保留原端点的 max_tokens/context_window/timeout（UI 不暴露这些高级字段）
      const existing = idx >= 0 ? endpoints[idx] : null;
      const next = {
        name: editDraft.name.trim().slice(0, 64),
        provider: editDraft.providerSlug || "custom",
        api_type: editDraft.apiType,
        base_url: editDraft.baseUrl.trim(),
        api_key_env: editDraft.apiKeyEnv.trim(),
        model: editDraft.modelId.trim(),
        priority: normalizePriority(editDraft.priority, 1),
        max_tokens: existing?.max_tokens ?? 8192,
        context_window: existing?.context_window ?? 150000,
        timeout: existing?.timeout ?? 180,
        capabilities: editDraft.caps?.length ? editDraft.caps : ["text"],
        extra_params:
          (editDraft.caps || []).includes("thinking") && editDraft.providerSlug === "dashscope"
            ? { enable_thinking: true }
            : undefined,
      };
      if (idx >= 0) endpoints[idx] = next;
      else endpoints.push(next);
      endpoints.sort((a: any, b: any) => (Number(a?.priority) || 999) - (Number(b?.priority) || 999));
      await writeEndpointsJson(endpoints, settings);
      setNotice("端点已更新");
      resetEndpointEditor();
      await loadSavedEndpoints();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (stepId !== "llm") return;
    loadSavedEndpoints().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepId, currentWorkspaceId, dataMode]);

  async function doSaveEndpoint() {
    if (!currentWorkspaceId) {
      setError("请先创建/选择一个当前工作区");
      return;
    }
    if (!selectedModelId) {
      setError("请先选择模型");
      return;
    }
    if (!apiKeyEnv.trim() || !apiKeyValue.trim()) {
      setError("请填写 API Key 环境变量名和值（会写入工作区 .env）");
      return;
    }
    setBusy(isEditingEndpoint ? "更新端点配置..." : "写入端点配置...");
    setError(null);

    try {
      await ensureEnvLoaded(currentWorkspaceId);
      setEnvDraft((e) => envSet(e, apiKeyEnv.trim(), apiKeyValue.trim()));
      const envPayload = { entries: { [apiKeyEnv.trim()]: apiKeyValue.trim() } };

      if (shouldUseHttpApi()) {
        try {
          await safeFetch(`${httpApiBase()}/api/config/env`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(envPayload),
          });
        } catch {
          // HTTP 回退到本地写入
          if (currentWorkspaceId) {
            await invoke("workspace_update_env", {
              workspaceId: currentWorkspaceId,
              entries: [{ key: apiKeyEnv.trim(), value: apiKeyValue.trim() }],
            });
          }
        }
      } else if (currentWorkspaceId) {
        await invoke("workspace_update_env", {
          workspaceId: currentWorkspaceId,
          entries: [{ key: apiKeyEnv.trim(), value: apiKeyValue.trim() }],
        });
      }

      // 读取现有 llm_endpoints.json
      let currentJson = "";
      try {
        currentJson = await readWorkspaceFile("data/llm_endpoints.json");
      } catch {
        currentJson = "";
      }

      const next = (() => {
        const base = currentJson ? JSON.parse(currentJson) : { endpoints: [], settings: {} };
        base.endpoints = Array.isArray(base.endpoints) ? base.endpoints : [];
        const usedNames = new Set(base.endpoints.map((e: any) => String(e?.name || "")).filter(Boolean));
        const baseName = (endpointName.trim() || `${providerSlug || selectedProvider?.slug || "provider"}-${selectedModelId}`).slice(0, 64);
        const name = (() => {
          if (isEditingEndpoint) {
            // allow keeping the same name; prevent collision with other endpoints
            const original = editingOriginalName || "";
            if (baseName !== original && usedNames.has(baseName)) {
              throw new Error(`端点名称已存在：${baseName}（请换一个）`);
            }
            return baseName || original;
          }
          if (!usedNames.has(baseName)) return baseName;
          for (let i = 2; i < 100; i++) {
            const n = `${baseName}-${i}`.slice(0, 64);
            if (!usedNames.has(n)) return n;
          }
          return `${baseName}-${Date.now()}`.slice(0, 64);
        })();
        const capList = Array.isArray(capSelected) && capSelected.length ? capSelected : ["text"];

        const endpoint = {
          name,
          provider: providerSlug || (selectedProvider?.slug ?? "custom"),
          api_type: apiType,
          base_url: baseUrl,
          api_key_env: apiKeyEnv.trim(),
          model: selectedModelId,
          priority: normalizePriority(endpointPriority, 1),
          max_tokens: 8192,
          context_window: 150000,
          timeout: 180,
          capabilities: capList,
          // DashScope 思考模式：OpenAkita 的 OpenAI provider 会识别 enable_thinking
          extra_params:
            capList.includes("thinking") && (providerSlug || selectedProvider?.slug) === "dashscope"
              ? { enable_thinking: true }
              : undefined,
        };

        if (isEditingEndpoint) {
          const original = editingOriginalName || name;
          const idx = base.endpoints.findIndex((e: any) => String(e?.name || "") === original);
          if (idx < 0) {
            // if missing, fall back to append
            base.endpoints.push(endpoint);
          } else {
            base.endpoints[idx] = endpoint;
          }
        } else {
          // 默认行为：不覆盖同名端点；自动改名后直接追加，实现“主端点 + 备份端点”
          base.endpoints.push(endpoint);
        }
        // 重新按 priority 排序（越小越优先）
        base.endpoints.sort((a: any, b: any) => (Number(a?.priority) || 999) - (Number(b?.priority) || 999));

        return JSON.stringify(base, null, 2) + "\n";
      })();

      await writeWorkspaceFile("data/llm_endpoints.json", next);

      setNotice(
        isEditingEndpoint
          ? "端点已更新：data/llm_endpoints.json（同时已写入 API Key 到 .env）。"
          : "端点已追加写入：data/llm_endpoints.json（同时已写入 API Key 到 .env）。你可以继续添加备份端点。",
      );
      if (isEditingEndpoint) resetEndpointEditor();
      await loadSavedEndpoints();
    } finally {
      setBusy(null);
    }
  }

  async function doDeleteEndpoint(name: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    setError(null);
    setBusy("删除端点...");
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      const base = raw ? JSON.parse(raw) : { endpoints: [], settings: {} };
      const eps = Array.isArray(base.endpoints) ? base.endpoints : [];
      base.endpoints = eps.filter((e: any) => String(e?.name || "") !== name);
      const next = JSON.stringify(base, null, 2) + "\n";
      await writeWorkspaceFile("data/llm_endpoints.json", next);

      // Immediately update local state
      setSavedEndpoints((prev) => prev.filter((e) => e.name !== name));
      setNotice(`已删除端点：${name}`);

      // Background re-read to fully sync
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function saveEnvKeys(keys: string[]) {
    const entries: Record<string, string> = {};
    for (const k of keys) {
      if (Object.prototype.hasOwnProperty.call(envDraft, k)) {
        entries[k] = envDraft[k] ?? "";
      }
    }
    if (!Object.keys(entries).length) return;

    if (shouldUseHttpApi()) {
      // ── 后端运行中 → 优先 HTTP API（后端写入 .env 并热加载）──
      try {
        await safeFetch(`${httpApiBase()}/api/config/env`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entries }),
        });
        return; // HTTP 成功，无需本地写入
      } catch {
        // HTTP 暂时不可用，回退到本地写入
        console.warn("saveEnvKeys: HTTP failed, falling back to Tauri");
      }
    }
    // ── 后端未运行 / HTTP 回退 → Tauri 本地写入 ──
    if (currentWorkspaceId) {
      await ensureEnvLoaded(currentWorkspaceId);
      const tauriEntries = Object.entries(entries).map(([key, value]) => ({ key, value }));
      await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries: tauriEntries });
    }
  }

  const providerApplyUrl = useMemo(() => {
    const slug = (selectedProvider?.slug || "").toLowerCase();
    const map: Record<string, string> = {
      openai: "https://platform.openai.com/api-keys",
      anthropic: "https://console.anthropic.com/settings/keys",
      moonshot: "https://platform.moonshot.cn/console",
      kimi: "https://platform.moonshot.cn/console",
      "kimi-cn": "https://platform.moonshot.cn/console",
      "kimi-int": "https://platform.moonshot.ai/console/api-keys",
      dashscope: "https://dashscope.console.aliyun.com/",
      minimax: "https://platform.minimaxi.com/user-center/basic-information/interface-key",
      "minimax-cn": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
      "minimax-int": "https://platform.minimax.io/user-center/basic-information/interface-key",
      deepseek: "https://platform.deepseek.com/",
      openrouter: "https://openrouter.ai/",
      siliconflow: "https://siliconflow.cn/",
      volcengine: "https://console.volcengine.com/ark/",
      zhipu: "https://open.bigmodel.cn/",
      "zhipu-cn": "https://open.bigmodel.cn/usercenter/apikeys",
      "zhipu-int": "https://z.ai/manage-apikey/apikey-list",
      yunwu: "https://yunwu.zeabur.app/",
    };
    return map[slug] || "";
  }, [selectedProvider?.slug]);

  const step = steps[currentStepIdx] || steps[0];

  async function goNext() {
    setNotice(null);
    setError(null);
    // lightweight guardrails
    if (stepId === "workspace" && !currentWorkspaceId) {
      setError("请先创建或选择一个当前工作区。");
      return;
    }
    if (stepId === "python" && !canUsePython) {
      setError("请先安装/检测到 Python，并在下拉框选择一个可用 Python（3.11+）。");
      return;
    }
    if (stepId === "install" && !openakitaInstalled) {
      setError("请先创建 venv 并完成 pip 安装 openakita。");
      return;
    }
    if (stepId === "llm" && savedEndpoints.length === 0) {
      // 只有“没有任何端点”才硬拦截
      setError("当前工作区还没有任何 LLM 端点。请先新增至少 1 个端点，再进入下一步。");
      return;
    }
    // If endpoints already exist, allow proceeding regardless of add-dialog state

    // 自动保存当前页面填写的配置到 .env（避免用户忘记点"保存"导致配置丢失）
    if (currentWorkspaceId) {
      try {
        const autoSaveKeys = getAutoSaveKeysForStep(stepId);
        if (autoSaveKeys.length > 0) {
          setBusy("自动保存配置...");
          await saveEnvKeys(autoSaveKeys);
          setBusy(null);
        }
      } catch {
        // 自动保存失败不阻塞跳转
        setBusy(null);
      }
    }

    setStepId(steps[Math.min(currentStepIdx + 1, steps.length - 1)].id);
  }

  /** 根据当前步骤返回需要自动保存的 env key 列表 */
  function getAutoSaveKeysForStep(sid: StepId): string[] {
    switch (sid) {
      case "im":
        return [
          "TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_PROXY",
          "TELEGRAM_REQUIRE_PAIRING", "TELEGRAM_PAIRING_CODE", "TELEGRAM_WEBHOOK_URL",
          "FEISHU_ENABLED", "FEISHU_APP_ID", "FEISHU_APP_SECRET",
          "WEWORK_ENABLED", "WEWORK_CORP_ID",
          "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY", "WEWORK_CALLBACK_PORT",
          "DINGTALK_ENABLED", "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET",
          "ONEBOT_ENABLED", "ONEBOT_WS_URL", "ONEBOT_ACCESS_TOKEN",
          "QQBOT_ENABLED", "QQBOT_APP_ID", "QQBOT_APP_SECRET", "QQBOT_SANDBOX", "QQBOT_MODE", "QQBOT_WEBHOOK_PORT", "QQBOT_WEBHOOK_PATH",
        ];
      case "tools":
        return [
          "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FORCE_IPV4",
          "TOOL_MAX_PARALLEL", "FORCE_TOOL_CALL_MAX_RETRIES",
          "ALLOW_PARALLEL_TOOLS_WITH_INTERRUPT_CHECKS",
          "MCP_ENABLED", "MCP_TIMEOUT", "MCP_BROWSER_ENABLED",
          "MCP_MYSQL_ENABLED", "MCP_MYSQL_HOST", "MCP_MYSQL_USER", "MCP_MYSQL_PASSWORD", "MCP_MYSQL_DATABASE",
          "MCP_POSTGRES_ENABLED", "MCP_POSTGRES_URL",
          "DESKTOP_ENABLED", "DESKTOP_DEFAULT_MONITOR", "DESKTOP_COMPRESSION_QUALITY",
          "DESKTOP_MAX_WIDTH", "DESKTOP_MAX_HEIGHT", "DESKTOP_CACHE_TTL",
          "DESKTOP_UIA_TIMEOUT", "DESKTOP_UIA_RETRY_INTERVAL", "DESKTOP_UIA_MAX_RETRIES",
          "DESKTOP_VISION_ENABLED", "DESKTOP_VISION_MODEL", "DESKTOP_VISION_FALLBACK_MODEL",
          "DESKTOP_VISION_OCR_MODEL", "DESKTOP_VISION_MAX_RETRIES", "DESKTOP_VISION_TIMEOUT",
          "DESKTOP_CLICK_DELAY", "DESKTOP_TYPE_INTERVAL", "DESKTOP_MOVE_DURATION",
          "DESKTOP_FAILSAFE", "DESKTOP_PAUSE", "DESKTOP_LOG_ACTIONS", "DESKTOP_LOG_SCREENSHOTS", "DESKTOP_LOG_DIR",
          "WHISPER_MODEL", "WHISPER_LANGUAGE", "GITHUB_TOKEN",
        ];
      case "agent":
        return [
          "AGENT_NAME", "MAX_ITERATIONS", "AUTO_CONFIRM",
          "THINKING_MODE",
          "PROGRESS_TIMEOUT_SECONDS", "HARD_TIMEOUT_SECONDS",
          "DATABASE_PATH", "LOG_LEVEL",
          "LOG_DIR", "LOG_FILE_PREFIX", "LOG_MAX_SIZE_MB", "LOG_BACKUP_COUNT",
          "LOG_RETENTION_DAYS", "LOG_FORMAT", "LOG_TO_CONSOLE", "LOG_TO_FILE",
          "EMBEDDING_MODEL", "EMBEDDING_DEVICE", "MODEL_DOWNLOAD_SOURCE",
          "MEMORY_HISTORY_DAYS", "MEMORY_MAX_HISTORY_FILES", "MEMORY_MAX_HISTORY_SIZE_MB",
          "PERSONA_NAME",
          "PROACTIVE_ENABLED", "PROACTIVE_MAX_DAILY_MESSAGES", "PROACTIVE_MIN_INTERVAL_MINUTES",
          "PROACTIVE_QUIET_HOURS_START", "PROACTIVE_QUIET_HOURS_END", "PROACTIVE_IDLE_THRESHOLD_HOURS",
          "STICKER_ENABLED", "STICKER_DATA_DIR",
          "SCHEDULER_ENABLED", "SCHEDULER_TIMEZONE", "SCHEDULER_MAX_CONCURRENT", "SCHEDULER_TASK_TIMEOUT",
          "SESSION_TIMEOUT_MINUTES", "SESSION_MAX_HISTORY", "SESSION_STORAGE_PATH",
          "ORCHESTRATION_ENABLED", "ORCHESTRATION_MODE",
          "ORCHESTRATION_BUS_ADDRESS", "ORCHESTRATION_PUB_ADDRESS",
          "ORCHESTRATION_MIN_WORKERS", "ORCHESTRATION_MAX_WORKERS",
          "ORCHESTRATION_HEARTBEAT_INTERVAL", "ORCHESTRATION_HEALTH_CHECK_INTERVAL",
        ];
      default:
        return [];
    }
  }

  /** 返回当前步骤对应的 footer 保存按钮配置，无需按钮时返回 null */
  function getFooterSaveConfig(): { keys: string[]; savedMsg: string } | null {
    switch (stepId) {
      case "llm": {
        const keysLLM = [
          ...savedEndpoints.map((e) => e.api_key_env),
          ...savedCompilerEndpoints.map((e) => e.api_key_env),
        ].filter(Boolean);
        return { keys: keysLLM, savedMsg: t("config.llmSaved") };
      }
      case "im":
        return { keys: getAutoSaveKeysForStep("im"), savedMsg: t("config.imSaved") };
      case "tools":
        return { keys: getAutoSaveKeysForStep("tools"), savedMsg: t("config.toolsSaved") };
      case "agent":
        return { keys: getAutoSaveKeysForStep("agent"), savedMsg: t("config.agentSaved") };
      default:
        return null;
    }
  }

  function goPrev() {
    setNotice(null);
    setError(null);
    // In quick mode, going back from quick-form should return to welcome (mode selection)
    if (stepId === "quick-form") {
      quickSetupStarted.current = false;
      setQuickSetupPhase(0);
      setQuickSetupError(null);
      setConfigMode(null);
      setStepId("welcome");
      setMaxReachedStepIdx(0);
      localStorage.setItem("openakita_maxStep", "0");
      return;
    }
    setStepId(steps[Math.max(currentStepIdx - 1, 0)].id);
  }

  // keep env draft in sync when workspace changes
  useEffect(() => {
    if (!currentWorkspaceId) return;
    ensureEnvLoaded(currentWorkspaceId).catch(() => {});
  }, [currentWorkspaceId]);

  /**
   * 后台自动检测所有 LLM 端点健康状态（fire-and-forget）。
   * 连接成功后调用一次，不阻塞 UI。
   */
  function autoCheckEndpoints(baseUrl: string) {
    (async () => {
      try {
        const res = await fetch(`${baseUrl}/api/health/check`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
          signal: AbortSignal.timeout(60_000),
        });
        if (!res.ok) return;
        const data = await res.json();
        const results: Array<{
          name: string; status: string; latency_ms: number | null;
          error: string | null; error_category: string | null;
          consecutive_failures: number; cooldown_remaining: number;
          is_extended_cooldown: boolean; last_checked_at: string | null;
        }> = data.results || [];
        const h: Record<string, {
          status: string; latencyMs: number | null; error: string | null;
          errorCategory: string | null; consecutiveFailures: number;
          cooldownRemaining: number; isExtendedCooldown: boolean; lastCheckedAt: string | null;
        }> = {};
        for (const r of results) {
          h[r.name] = {
            status: r.status, latencyMs: r.latency_ms, error: r.error,
            errorCategory: r.error_category, consecutiveFailures: r.consecutive_failures,
            cooldownRemaining: r.cooldown_remaining, isExtendedCooldown: r.is_extended_cooldown,
            lastCheckedAt: r.last_checked_at,
          };
        }
        setEndpointHealth(h);
      } catch { /* 后台检测失败不影响用户 */ }
    })();
  }

  async function refreshStatus(overrideDataMode?: "local" | "remote", overrideApiBaseUrl?: string, forceAliveCheck?: boolean) {
    const effectiveDataMode = overrideDataMode || dataMode;
    const effectiveApiBaseUrl = overrideApiBaseUrl || apiBaseUrl;
    // forceAliveCheck bypasses the guard (used after connecting to a known-alive service)
    if (!forceAliveCheck && !info && !serviceStatus?.running && effectiveDataMode !== "remote") return;
    setStatusLoading(true);
    setStatusError(null);
    try {
      // Verify the service is actually alive before trying HTTP API
      let serviceAlive = false;
      if (forceAliveCheck || serviceStatus?.running || effectiveDataMode === "remote") {
        try {
          const ping = await fetch(`${effectiveApiBaseUrl}/api/health`, { signal: AbortSignal.timeout(3000) });
          serviceAlive = ping.ok;
          if (serviceAlive) {
            // Extract backend version from health response
            try {
              const healthData = await ping.json();
              if (healthData.version) setBackendVersion(healthData.version);
            } catch { /* ignore parse error */ }
            // Ensure running state is set whenever health check succeeds
            // (fixes stale-closure issues where setServiceStatus({running:true})
            //  from the caller may not have been applied yet)
            setServiceStatus((prev) =>
              prev ? { ...prev, running: true } : { running: true, pid: null, pidFile: "" }
            );
          }
        } catch {
          // Service is not reachable
          serviceAlive = false;
          setBackendVersion(null);
          if (effectiveDataMode !== "remote") {
            setServiceStatus((prev) =>
              prev ? { ...prev, running: false } : { running: false, pid: null, pidFile: "" }
            );
          }
        }
      }
      const useHttpApi = serviceAlive;
      if (useHttpApi) {
        // ── Try HTTP API, fall back to Tauri on failure ──
        let httpOk = false;
        try {
          // Try new config API (may not exist in older service versions)
          const envRes = await fetch(`${effectiveApiBaseUrl}/api/config/env`);
          if (envRes.ok) {
            const envData = await envRes.json();
            const env = envData.env || {};
            setEnvDraft((prev) => ({ ...prev, ...env }));
            envLoadedForWs.current = "__remote__";

            const epRes = await fetch(`${effectiveApiBaseUrl}/api/config/endpoints`);
            if (epRes.ok) {
              const epData = await epRes.json();
              const eps = Array.isArray(epData?.endpoints) ? epData.endpoints : [];
              const list = eps
                .map((e: any) => {
                  const keyEnv = String(e?.api_key_env || "");
                  const keyPresent = !!(keyEnv && (env[keyEnv] ?? "").trim());
                  return {
                    name: String(e?.name || ""),
                    provider: String(e?.provider || ""),
                    apiType: String(e?.api_type || ""),
                    baseUrl: String(e?.base_url || ""),
                    model: String(e?.model || ""),
                    keyEnv,
                    keyPresent,
                  };
                })
                .filter((e: any) => e.name);
              setEndpointSummary(list);
              httpOk = true;
            }
          }
        } catch {
          // Config API not available — will fall back below
        }

        // Fall back: try /api/models (always available in running service)
        if (!httpOk) {
          try {
            const modelsRes = await fetch(`${effectiveApiBaseUrl}/api/models`);
            if (modelsRes.ok) {
              const modelsData = await modelsRes.json();
              const models = Array.isArray(modelsData?.models) ? modelsData.models : [];
              const list = models.map((m: any) => ({
                name: String(m?.name || m?.endpoint || ""),
                provider: String(m?.provider || ""),
                apiType: "",
                baseUrl: "",
                model: String(m?.model || ""),
                keyEnv: "",
                keyPresent: m?.has_api_key === true,
              })).filter((e: any) => e.name);
              if (list.length > 0) {
                setEndpointSummary(list);
                // Also populate endpointHealth from /api/models status
                const healthFromModels: Record<string, any> = {};
                for (const m of models) {
                  const n = String(m?.name || m?.endpoint || "");
                  if (!n) continue;
                  const s = String(m?.status || "unknown");
                  healthFromModels[n] = { status: s, latencyMs: null, error: s === "unhealthy" ? "endpoint unhealthy" : null };
                }
                setEndpointHealth((prev: any) => ({ ...healthFromModels, ...prev }));
              }
              httpOk = true;
            }
          } catch { /* ignore */ }
        }

        // Fall back to Tauri local file system if HTTP API completely failed
        if (!httpOk && currentWorkspaceId) {
          try {
            const env = await ensureEnvLoaded(currentWorkspaceId);
            const raw = await readWorkspaceFile("data/llm_endpoints.json");
            const parsed = JSON.parse(raw);
            const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
            const list = eps.map((e: any) => {
              const keyEnv = String(e?.api_key_env || "");
              const keyPresent = !!(keyEnv && (env[keyEnv] ?? "").trim());
              return {
                name: String(e?.name || ""), provider: String(e?.provider || ""),
                apiType: String(e?.api_type || ""), baseUrl: String(e?.base_url || ""),
                model: String(e?.model || ""), keyEnv, keyPresent,
              };
            }).filter((e: any) => e.name);
            setEndpointSummary(list);
          } catch { /* ignore */ }
        }

        // Skills via HTTP
        try {
          const skRes = await fetch(`${effectiveApiBaseUrl}/api/skills`);
          if (skRes.ok) {
            const skData = await skRes.json();
            const skills = Array.isArray(skData?.skills) ? skData.skills : [];
            const systemCount = skills.filter((s: any) => !!s.system).length;
            const externalCount = skills.length - systemCount;
            setSkillSummary({ count: skills.length, systemCount, externalCount });
            setSkillsDetail(
              skills.map((s: any) => ({
                name: String(s?.name || ""), description: String(s?.description || ""),
                system: !!s?.system, enabled: typeof s?.enabled === "boolean" ? s.enabled : undefined,
                tool_name: s?.tool_name ?? null, category: s?.category ?? null, path: s?.path ?? null,
              })),
            );
          }
        } catch {
          // Fall back to Tauri for skills (local mode only)
          if (effectiveDataMode !== "remote" && currentWorkspaceId) {
            try {
              const skillsRaw = await invoke<string>("openakita_list_skills", { venvDir, workspaceId: currentWorkspaceId });
              const skillsParsed = JSON.parse(skillsRaw) as { count: number; skills: any[] };
              const skills = Array.isArray(skillsParsed.skills) ? skillsParsed.skills : [];
              const systemCount = skills.filter((s) => !!s.system).length;
              setSkillSummary({ count: skills.length, systemCount, externalCount: skills.length - systemCount });
              setSkillsDetail(skills.map((s) => ({
                name: String(s?.name || ""), description: String(s?.description || ""),
                system: !!s?.system, enabled: typeof s?.enabled === "boolean" ? s.enabled : undefined,
                tool_name: s?.tool_name ?? null, category: s?.category ?? null, path: s?.path ?? null,
              })));
            } catch { setSkillSummary(null); setSkillsDetail(null); }
          }
        }

        // Service status – enrich with PID info from Tauri, but do NOT override
        // the running flag: the HTTP health check is the source of truth for whether
        // the service is alive.  The Tauri PID file may not exist when the service
        // was started externally (not via this app).
        if (effectiveDataMode !== "remote" && currentWorkspaceId) {
          try {
            const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", { workspaceId: currentWorkspaceId });
            // Merge PID info but keep running=true since health check passed
            setServiceStatus((prev) => ({
              running: prev?.running ?? serviceAlive, // health check wins
              pid: ss.pid ?? prev?.pid ?? null,
              pidFile: ss.pidFile ?? prev?.pidFile ?? "",
            }));
          } catch { /* keep existing status */ }
        }
        return;
      }

      // ── Local mode: use Tauri commands (original logic) ──
      if (!currentWorkspaceId) {
        setEndpointSummary([]);
        setSkillSummary(null);
        setSkillsDetail(null);
        return;
      }
      const env = await ensureEnvLoaded(currentWorkspaceId);

      // endpoints
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      const parsed = JSON.parse(raw);
      const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
      const list = eps
        .map((e: any) => {
          const keyEnv = String(e?.api_key_env || "");
          const keyPresent = !!(keyEnv && (env[keyEnv] ?? "").trim());
          return {
            name: String(e?.name || ""),
            provider: String(e?.provider || ""),
            apiType: String(e?.api_type || ""),
            baseUrl: String(e?.base_url || ""),
            model: String(e?.model || ""),
            keyEnv,
            keyPresent,
          };
        })
        .filter((e: any) => e.name);
      setEndpointSummary(list);

      // skills (requires openakita installed in venv)
      try {
        const skillsRaw = await invoke<string>("openakita_list_skills", { venvDir, workspaceId: currentWorkspaceId });
        const skillsParsed = JSON.parse(skillsRaw) as { count: number; skills: any[] };
        const skills = Array.isArray(skillsParsed.skills) ? skillsParsed.skills : [];
        const systemCount = skills.filter((s) => !!s.system).length;
        const externalCount = skills.length - systemCount;
        setSkillSummary({ count: skills.length, systemCount, externalCount });
        setSkillsDetail(
          skills.map((s) => ({
            name: String(s?.name || ""),
            description: String(s?.description || ""),
            system: !!s?.system,
            enabled: typeof s?.enabled === "boolean" ? s.enabled : undefined,
            tool_name: s?.tool_name ?? null,
            category: s?.category ?? null,
            path: s?.path ?? null,
          })),
        );
      } catch {
        setSkillSummary(null);
        setSkillsDetail(null);
      }

      try {
        const en = await invoke<boolean>("autostart_is_enabled");
        setAutostartEnabled(en);
      } catch {
        setAutostartEnabled(null);
      }
      // autoStartBackend 已合并到开机自启，不再单独获取

      // Local mode (HTTP not reachable): check PID-based service status
      // This is the fallback when the HTTP API is not alive.
      if (effectiveDataMode !== "remote") {
        try {
          const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", {
            workspaceId: currentWorkspaceId,
          });
          setServiceStatus(ss);
        } catch {
          // keep existing status rather than wiping it
        }
      }
      // Auto-fetch IM channel status from running service
      if (useHttpApi) {
        try {
          const imRes = await fetch(`${effectiveApiBaseUrl}/api/im/channels`, { signal: AbortSignal.timeout(5000) });
          if (imRes.ok) {
            const imData = await imRes.json();
            const channels = imData.channels || [];
            const h: Record<string, { status: string; error: string | null; lastCheckedAt: string | null }> = {};
            for (const c of channels) {
              h[c.channel || c.name] = { status: c.status || "unknown", error: c.error || null, lastCheckedAt: c.last_checked_at || null };
            }
            if (Object.keys(h).length > 0) setImHealth(h);
          }
        } catch { /* ignore - IM status is optional */ }
      }
      // ── Multi-process detection (local mode only) ──
      if (effectiveDataMode !== "remote") {
        try {
          const procs = await invoke<Array<{ pid: number; cmd: string }>>("openakita_list_processes");
          setDetectedProcesses(procs);
        } catch {
          setDetectedProcesses([]);
        }
      } else {
        setDetectedProcesses([]);
      }
    } catch (e) {
      setStatusError(String(e));
    } finally {
      setStatusLoading(false);
    }
  }

  /**
   * 轮询等待后端 HTTP 服务就绪。
   * 启动进程（PID 存活）不代表 HTTP 可达，FastAPI+uvicorn 需要额外几秒初始化。
   * @returns true 如果在 maxWaitMs 内服务响应了 /api/health
   */
  async function waitForServiceReady(baseUrl: string, maxWaitMs = 60000): Promise<boolean> {
    const start = Date.now();
    const interval = 1000;
    while (Date.now() - start < maxWaitMs) {
      try {
        const res = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(3000) });
        if (res.ok) return true;
      } catch { /* not ready yet */ }
      await new Promise((r) => setTimeout(r, interval));
    }
    return false;
  }

  /**
   * 启动本地服务前，检测端口 18900 是否已有服务运行。
   * @returns null = 没有冲突可以启动，否则返回现有服务信息
   */
  async function detectLocalServiceConflict(): Promise<{ pid: number; version: string; service: string } | null> {
    try {
      const res = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(2000) });
      if (!res.ok) return null;
      const data = await res.json();
      if (data.status === "ok") {
        return {
          pid: data.pid || 0,
          version: data.version || "unknown",
          service: data.service || "openakita",
        };
      }
    } catch { /* service not running */ }
    return null;
  }

  /**
   * 检查后端服务版本与桌面端版本是否一致。
   * 在成功连接到服务后调用。
   */
  function checkVersionMismatch(backendVersion: string) {
    if (!backendVersion || backendVersion === "0.0.0-dev") return;
    if (!desktopVersion || desktopVersion === "0.0.0") return; // not yet loaded from Tauri
    // Normalize: strip leading 'v'
    const bv = backendVersion.replace(/^v/, "");
    const dv = desktopVersion.replace(/^v/, "");
    if (bv !== dv) {
      setVersionMismatch({ backend: bv, desktop: dv });
    } else {
      setVersionMismatch(null);
    }
  }

  /**
   * 比较两个语义化版本号，返回：
   *  1  — a > b
   *  0  — a == b
   * -1  — a < b
   * 仅比较 major.minor.patch 数字部分，忽略预发布后缀。
   */
  function compareSemver(a: string, b: string): number {
    const parse = (v: string) => v.replace(/^v/, "").split(".").map((s) => parseInt(s, 10) || 0);
    const pa = parse(a);
    const pb = parse(b);
    for (let i = 0; i < 3; i++) {
      if ((pa[i] ?? 0) > (pb[i] ?? 0)) return 1;
      if ((pa[i] ?? 0) < (pb[i] ?? 0)) return -1;
    }
    return 0;
  }

  /**
   * 检查 GitHub 是否有新版本发布。
   *
   * 缓存策略（localStorage）：
   * - 成功：缓存 24 小时，期间不再请求
   * - 失败：指数退避 — 1h → 4h → 12h → 48h → 72h（上限），
   *   适应国内网络环境下 GitHub API 不可达的情况
   */
  async function checkGitHubRelease() {
    const cacheKey = "openakita_release_check";
    const failKey = "openakita_release_fail";
    const dismissKey = "openakita_release_dismissed";
    const SUCCESS_TTL = 24 * 60 * 60 * 1000;   // 24h
    const BACKOFF_BASE = 60 * 60 * 1000;        // 1h
    const BACKOFF_MAX = 72 * 60 * 60 * 1000;    // 72h
    const FETCH_TIMEOUT = 4000;                  // 4s (shorter — don't block UX)

    try {
      // ── 1. Check success cache ──
      const cached = localStorage.getItem(cacheKey);
      if (cached) {
        try {
          const { ts, tag } = JSON.parse(cached);
          if (Date.now() - ts < SUCCESS_TTL) {
            // Still within cache window — show notification if newer
            const dismissed = localStorage.getItem(dismissKey);
            if (tag && compareSemver(tag, desktopVersion) > 0 && dismissed !== tag) {
              setNewRelease({
                latest: tag,
                current: desktopVersion,
                url: `https://github.com/${GITHUB_REPO}/releases/tag/v${tag}`,
              });
            }
            return;
          }
        } catch { /* corrupted cache, proceed to fetch */ }
      }

      // ── 2. Check failure backoff ──
      const failRaw = localStorage.getItem(failKey);
      if (failRaw) {
        try {
          const { ts, count } = JSON.parse(failRaw);
          const backoff = Math.min(BACKOFF_BASE * Math.pow(2, count - 1), BACKOFF_MAX);
          if (Date.now() - ts < backoff) {
            return; // Still in cooldown after previous failure
          }
        } catch { /* corrupted, proceed */ }
      }

      // ── 3. Fetch from GitHub API ──
      const res = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`, {
        signal: AbortSignal.timeout(FETCH_TIMEOUT),
        headers: { Accept: "application/vnd.github.v3+json" },
      });

      if (!res.ok) {
        // HTTP error (403 rate limit, 404, etc.) — record failure for backoff
        const prev = failRaw ? JSON.parse(failRaw) : { count: 0 };
        localStorage.setItem(failKey, JSON.stringify({ ts: Date.now(), count: (prev.count || 0) + 1 }));
        return;
      }

      // ── 4. Parse response ──
      const data = await res.json();
      const tagName = (data.tag_name || "").replace(/^v/, "");

      // Success — cache result and clear failure counter
      localStorage.setItem(cacheKey, JSON.stringify({ ts: Date.now(), tag: tagName }));
      localStorage.removeItem(failKey);

      if (tagName && compareSemver(tagName, desktopVersion) > 0) {
        const dismissed = localStorage.getItem(dismissKey);
        if (dismissed !== tagName) {
          setNewRelease({
            latest: tagName,
            current: desktopVersion,
            url: data.html_url || `https://github.com/${GITHUB_REPO}/releases`,
          });
        }
      }
    } catch {
      // Network error / timeout — record failure for exponential backoff
      try {
        const prevRaw = localStorage.getItem(failKey);
        const prevCount = prevRaw ? (JSON.parse(prevRaw).count || 0) : 0;
        localStorage.setItem(failKey, JSON.stringify({ ts: Date.now(), count: prevCount + 1 }));
      } catch { /* localStorage full or unavailable */ }
    }
  }

  /**
   * 包装本地服务启动流程：检测冲突 → 处理冲突 → 启动。
   * 返回 true = 已处理（连接已有或启动新服务），false = 用户取消。
   */
  async function startLocalServiceWithConflictCheck(effectiveWsId: string): Promise<boolean> {
    // Step 1: Detect existing service
    const existing = await detectLocalServiceConflict();
    if (existing) {
      // Show conflict dialog and let user choose
      setPendingStartWsId(effectiveWsId);
      setConflictDialog({ pid: existing.pid, version: existing.version });
      return false; // Will be resolved by dialog callbacks
    }
    // Step 2: No conflict — start normally
    await doStartLocalService(effectiveWsId);
    return true;
  }

  /**
   * 实际启动本地服务（跳过冲突检测）。
   */
  async function doStartLocalService(effectiveWsId: string) {
    setBusy(t("topbar.starting"));
    setError(null);
    try {
      setDataMode("local");
      setApiBaseUrl("http://127.0.0.1:18900");
      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", {
        venvDir,
        workspaceId: effectiveWsId,
      });
      setServiceStatus(ss);
      const ready = await waitForServiceReady("http://127.0.0.1:18900");
      const real = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", {
        workspaceId: effectiveWsId,
      });
      setServiceStatus(real);
      if (ready && real.running) {
        setNotice(t("connect.success"));
        // forceAliveCheck=true to bypass stale serviceStatus closure
        await refreshStatus("local", "http://127.0.0.1:18900", true);
        // 自动检测 LLM 端点健康状态
        autoCheckEndpoints("http://127.0.0.1:18900");
        // Check version after successful start
        try {
          const hRes = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(2000) });
          if (hRes.ok) {
            const hData = await hRes.json();
            checkVersionMismatch(hData.version || "");
          }
        } catch { /* ignore */ }
      } else if (real.running) {
        // Process is alive but HTTP API not yet reachable — keep waiting in background
        setBusy(t("topbar.starting") + "…");
        const bgReady = await waitForServiceReady("http://127.0.0.1:18900", 60000);
        if (bgReady) {
          setNotice(t("connect.success"));
          await refreshStatus("local", "http://127.0.0.1:18900", true);
          autoCheckEndpoints("http://127.0.0.1:18900");
          try {
            const hRes = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(2000) });
            if (hRes.ok) {
              const hData = await hRes.json();
              checkVersionMismatch(hData.version || "");
            }
          } catch { /* ignore */ }
        } else {
          setError(t("topbar.startFail") + " (HTTP API not reachable)");
          await refreshStatus("local", "http://127.0.0.1:18900", true);
        }
      } else {
        setError(t("topbar.startFail"));
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  /**
   * 连接到已有本地服务（冲突对话框的"连接已有"选项）。
   */
  async function connectToExistingLocalService() {
    const ver = conflictDialog?.version || "";
    setDataMode("local");
    setApiBaseUrl("http://127.0.0.1:18900");
    setServiceStatus({ running: true, pid: null, pidFile: "" });
    setConflictDialog(null);
    setPendingStartWsId(null);
    setBusy(t("connect.testing"));
    try {
      // IMPORTANT: pass forceAliveCheck=true because setServiceStatus is async
      // and refreshStatus's closure still sees the old serviceStatus value
      await refreshStatus("local", "http://127.0.0.1:18900", true);
      autoCheckEndpoints("http://127.0.0.1:18900");
      setNotice(t("connect.success"));
      // Check version mismatch using info from conflict detection (avoids extra request)
      if (ver && ver !== "unknown") checkVersionMismatch(ver);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  /**
   * 停止已有服务再启动新的（冲突对话框的"停止并重启"选项）。
   */
  async function stopAndRestartService() {
    const wsId = pendingStartWsId;
    setConflictDialog(null);
    setPendingStartWsId(null);
    if (!wsId) return;
    setBusy(t("status.stopping"));
    try {
      await doStopService(wsId);
      await new Promise((r) => setTimeout(r, 500));
    } catch { /* ignore stop errors */ }
    await doStartLocalService(wsId);
  }

  // ── Check GitHub release once desktop version is known ──
  useEffect(() => {
    if (desktopVersion === "0.0.0") return; // not yet loaded
    checkGitHubRelease();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktopVersion]);

  /** Stop the running service: try API shutdown first, then PID kill, then verify. */
  async function doStopService(wsId?: string | null) {
    const id = wsId || currentWorkspaceId || workspaces[0]?.id;
    if (!id) throw new Error("No workspace");
    // 1. Try graceful shutdown via HTTP API (works even for externally started services)
    let apiShutdownOk = false;
    try {
      const res = await fetch(`${apiBaseUrl}/api/shutdown`, { method: "POST", signal: AbortSignal.timeout(2000) });
      apiShutdownOk = res.ok; // true if endpoint exists and responded 200
    } catch { /* network error or timeout — service might already be down */ }
    if (apiShutdownOk) {
      // Wait for the process to exit after graceful shutdown
      await new Promise((r) => setTimeout(r, 1000));
    }
    // 2. PID-based kill as fallback (handles locally started services)
    try {
      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_stop", { workspaceId: id });
      setServiceStatus(ss);
    } catch { /* PID file might not exist for externally started services */ }
    // 3. Quick verify — is the port freed?
    await new Promise((r) => setTimeout(r, 300));
    let stillAlive = false;
    try {
      await fetch(`${apiBaseUrl}/api/health`, { signal: AbortSignal.timeout(1500) });
      stillAlive = true;
    } catch { /* Good — service is down */ }
    if (stillAlive) {
      // Service stubbornly alive — show warning
      setError(t("status.stopFailed"));
    }
    // Final status
    try {
      const final_ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", { workspaceId: id });
      setServiceStatus(final_ss);
    } catch { /* ignore */ }
  }

  async function refreshServiceLog(workspaceId: string) {
    try {
      let chunk: { path: string; content: string; truncated: boolean };
      if (shouldUseHttpApi()) {
        // ── 后端运行中 → HTTP API 获取日志 ──
        const res = await safeFetch(`${httpApiBase()}/api/logs/service?tail_bytes=60000`);
        chunk = await res.json();
      } else {
        // 本地模式且服务未运行：直接读本地日志文件
        chunk = await invoke<{ path: string; content: string; truncated: boolean }>("openakita_service_log", {
          workspaceId,
          tailBytes: 60000,
        });
      }
      setServiceLog(chunk);
      setServiceLogError(null);
    } catch (e) {
      setServiceLog(null);
      setServiceLogError(String(e));
    }
  }

  // 状态面板：服务运行时自动刷新日志（远程模式下用 "__remote__" 作为 workspaceId 占位）
  useEffect(() => {
    if (view !== "status") return;
    if (!serviceStatus?.running) return;
    const wsId = currentWorkspaceId || (dataMode === "remote" ? "__remote__" : null);
    if (!wsId) return;
    let cancelled = false;
    void (async () => {
      if (!cancelled) await refreshServiceLog(wsId);
    })();
    const t = window.setInterval(() => {
      if (cancelled) return;
      void refreshServiceLog(wsId);
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [view, currentWorkspaceId, serviceStatus?.running, dataMode]);

  // Skills selection default sync (only when user hasn't changed it)
  useEffect(() => {
    if (!skillsDetail) return;
    if (skillsTouched) return;
    const m: Record<string, boolean> = {};
    for (const s of skillsDetail) {
      if (!s?.name) continue;
      if (s.system) m[s.name] = true;
      else m[s.name] = typeof s.enabled === "boolean" ? s.enabled : true;
    }
    setSkillsSelection(m);
  }, [skillsDetail, skillsTouched]);

  // 自动获取 skills：进入“工具与技能”页就拉一次（且仅在尚未拿到 skillsDetail 时）
  useEffect(() => {
    if (view !== "wizard") return;
    if (stepId !== "tools") return;
    if (!currentWorkspaceId && dataMode !== "remote") return;
    if (!!busy) return;
    if (skillsDetail) return;
    if (!openakitaInstalled && dataMode !== "remote") return;
    void doRefreshSkills();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, stepId, currentWorkspaceId, openakitaInstalled, skillsDetail, dataMode]);

  async function doRefreshSkills() {
    if (!currentWorkspaceId && dataMode !== "remote") {
      setError("请先设置当前工作区");
      return;
    }
    setError(null);
    setBusy("读取 skills...");
    try {
      let skillsList: any[] = [];
      // ── 后端运行中 → HTTP API ──
      if (shouldUseHttpApi()) {
        const res = await safeFetch(`${httpApiBase()}/api/skills`, { signal: AbortSignal.timeout(5000) });
        const data = await res.json();
        skillsList = Array.isArray(data?.skills) ? data.skills : [];
      }
      // ── 后端未运行 → Tauri invoke（需要 venv）──
      if (!shouldUseHttpApi() && skillsList.length === 0 && currentWorkspaceId) {
        try {
          const skillsRaw = await invoke<string>("openakita_list_skills", { venvDir, workspaceId: currentWorkspaceId });
          const skillsParsed = JSON.parse(skillsRaw) as { count: number; skills: any[] };
          skillsList = Array.isArray(skillsParsed.skills) ? skillsParsed.skills : [];
        } catch (e) {
          // 打包模式下无 venv，Tauri invoke 会失败，降级为空列表（服务启动后可通过 HTTP API 获取）
          console.warn("openakita_list_skills via Tauri failed:", e);
        }
      }
      const systemCount = skillsList.filter((s: any) => !!s.system).length;
      const externalCount = skillsList.length - systemCount;
      setSkillSummary({ count: skillsList.length, systemCount, externalCount });
      setSkillsDetail(
        skillsList.map((s: any) => ({
          name: String(s?.name || ""),
          description: String(s?.description || ""),
          system: !!s?.system,
          enabled: typeof s?.enabled === "boolean" ? s.enabled : undefined,
          tool_name: s?.tool_name ?? null,
          category: s?.category ?? null,
          path: s?.path ?? null,
        })),
      );
      setNotice("已刷新 skills 列表");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doSaveSkillsSelection() {
    if (!currentWorkspaceId) {
      setError("请先设置当前工作区");
      return;
    }
    if (!skillsDetail) {
      setError("未读取到 skills 列表（请先刷新 skills）");
      return;
    }
    setError(null);
    setBusy("保存 skills 启用状态...");
    try {
      const externalAllowlist = skillsDetail
        .filter((s) => !s.system && !!s.name)
        .filter((s) => !!skillsSelection[s.name])
        .map((s) => s.name);

      const content =
        JSON.stringify(
          {
            version: 1,
            external_allowlist: externalAllowlist,
            updated_at: new Date().toISOString(),
          },
          null,
          2,
        ) + "\n";

      await writeWorkspaceFile("data/skills.json", content);
      setSkillsTouched(false);
      setNotice("已保存：data/skills.json（系统技能默认启用；外部技能按你的选择启用）");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  // Only count done items that are actually in the current steps list
  const doneCount = steps.filter((s) => done.has(s.id)).length;
  const totalSteps = steps.length;

  // Auto-collapse config section when all steps done
  useEffect(() => {
    if (doneCount >= totalSteps) setConfigExpanded(false);
  }, [doneCount, totalSteps]);

  const StepDot = ({ idx, isDone }: { idx: number; isDone: boolean }) => (
    <div className={`stepDot ${isDone ? "stepDotDone" : ""}`}>
      {isDone ? <IconCheck size={14} /> : idx + 1}
    </div>
  );

  function renderStatus() {
    const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
    const ws = workspaces.find((w) => w.id === effectiveWsId) || workspaces[0] || null;
    const im = [
      { k: "TELEGRAM_ENABLED", name: "Telegram", required: ["TELEGRAM_BOT_TOKEN"] },
      { k: "FEISHU_ENABLED", name: t("status.feishu"), required: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"] },
      { k: "WEWORK_ENABLED", name: t("status.wework"), required: ["WEWORK_CORP_ID", "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY"] },
      { k: "DINGTALK_ENABLED", name: t("status.dingtalk"), required: ["DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"] },
      { k: "ONEBOT_ENABLED", name: "OneBot", required: ["ONEBOT_WS_URL"] },
      { k: "QQBOT_ENABLED", name: "QQ 机器人", required: ["QQBOT_APP_ID", "QQBOT_APP_SECRET"] },
    ];
    const imStatus = im.map((c) => {
      const enabled = envGet(envDraft, c.k, "false").toLowerCase() === "true";
      const missing = c.required.filter((rk) => !(envGet(envDraft, rk) || "").trim());
      return { ...c, enabled, ok: enabled ? missing.length === 0 : true, missing };
    });

    return (
      <>
        {/* Top row: service + system info */}
        <div className="statusGrid3">
          {/* Service */}
          <div className="statusCard">
            <div className="statusCardHead">
              <span className="statusCardLabel">{t("status.service")}</span>
              {heartbeatState === "alive" ? <DotGreen /> : heartbeatState === "degraded" ? <DotYellow /> : heartbeatState === "suspect" ? <DotYellow /> : serviceStatus?.running ? <DotGreen /> : <DotGray />}
            </div>
            <div className="statusCardValue">
              {heartbeatState === "degraded" ? t("status.unresponsive") : serviceStatus?.running ? t("topbar.running") : t("topbar.stopped")}
              {serviceStatus?.pid ? <span className="statusCardSub"> PID {serviceStatus.pid}</span> : null}
            </div>
            <div className="statusCardActions">
              {!serviceStatus?.running && effectiveWsId && (
                <button className="btnSmall btnSmallPrimary" onClick={async () => {
                  await startLocalServiceWithConflictCheck(effectiveWsId);
                }} disabled={!!busy}>{t("topbar.start")}</button>
              )}
              {serviceStatus?.running && effectiveWsId && (<>
                <button className="btnSmall btnSmallDanger" onClick={async () => {
                  setBusy(t("status.stopping")); setError(null);
                  try {
                    await doStopService(effectiveWsId);
                  } catch (e) { setError(String(e)); } finally { setBusy(null); }
                }} disabled={!!busy}>{t("status.stop")}</button>
                <button className="btnSmall" onClick={async () => {
                  setBusy(t("status.restarting")); setError(null);
                  try {
                    await doStopService(effectiveWsId);
                    await new Promise((r) => setTimeout(r, 500));
                    await doStartLocalService(effectiveWsId);
                  } catch (e) { setError(String(e)); } finally { setBusy(null); }
                }} disabled={!!busy}>{t("status.restart")}</button>
              </>)}
            </div>
            {/* Multi-process warning */}
            {detectedProcesses.length > 1 && (
              <div style={{ marginTop: 8, padding: "6px 10px", background: "#fff3e0", borderRadius: 6, fontSize: 12, color: "#e65100", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600 }}>⚠ 检测到 {detectedProcesses.length} 个 OpenAkita 进程正在运行</span>
                <span style={{ color: "#bf360c", fontSize: 11 }}>
                  ({detectedProcesses.map(p => `PID ${p.pid}`).join(", ")})
                </span>
                <button className="btnSmall btnSmallDanger" style={{ marginLeft: "auto", fontSize: 11 }} onClick={async () => {
                  setBusy("正在停止所有进程..."); setError(null);
                  try {
                    const stopped = await invoke<number[]>("openakita_stop_all_processes");
                    setDetectedProcesses([]);
                    setNotice(`已停止 ${stopped.length} 个进程`);
                    // Refresh status after stopping
                    await refreshStatus();
                  } catch (e) { setError(String(e)); } finally { setBusy(null); }
                }} disabled={!!busy}>全部停止</button>
              </div>
            )}
            {/* Degraded hint */}
            {heartbeatState === "degraded" && (
              <div style={{ marginTop: 8, padding: "6px 10px", background: "#fffde7", borderRadius: 6, fontSize: 12, color: "#f57f17", display: "flex", alignItems: "flex-start", gap: 8, flexWrap: "wrap" }}>
                <DotYellow size={8} />
                <span>{t("status.degradedHint")}</span>
              </div>
            )}
            {/* Troubleshooting panel */}
            {(heartbeatState === "dead" && !serviceStatus?.running) && (
              <TroubleshootPanel t={t} />
            )}
          </div>

          {/* Workspace */}
          <div className="statusCard">
            <div className="statusCardHead">
              <span className="statusCardLabel">{t("config.step.workspace")}</span>
            </div>
            <div className="statusCardValue">{currentWorkspaceId || "—"}</div>
            <div className="statusCardSub">{ws?.path || ""}</div>
          </div>

          {/* Autostart (= desktop autostart + backend auto-launch) */}
          <div className="statusCard">
            <div className="statusCardHead">
              <span className="statusCardLabel">{t("status.autostart")}</span>
              {autostartEnabled ? <DotGreen /> : <DotGray />}
            </div>
            <div className="statusCardValue">{autostartEnabled ? t("status.on") : t("status.off")}</div>
            <div className="statusCardSub">{t("status.autostartHint")}</div>
            <div className="statusCardActions">
              <button className="btnSmall" onClick={async () => {
                setBusy(t("common.loading")); setError(null);
                try { const next = !autostartEnabled; await invoke("autostart_set_enabled", { enabled: next }); setAutostartEnabled(next); } catch (e) { setError(String(e)); } finally { setBusy(null); }
              }} disabled={autostartEnabled === null || !!busy}>{autostartEnabled ? t("status.off") : t("status.on")}</button>
            </div>
          </div>

          {/* Auto-start backend 已合并到"开机自启"中，不再单独展示 */}
        </div>

        {/* LLM Endpoints compact table */}
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span className="statusCardLabel">{t("status.llmEndpoints")} ({endpointSummary.length})</span>
            <button className="btnSmall" onClick={async () => {
              setHealthChecking("all");
              try {
                let results: Array<{ name: string; status: string; latency_ms: number | null; error: string | null; error_category: string | null; consecutive_failures: number; cooldown_remaining: number; is_extended_cooldown: boolean; last_checked_at: string | null }>;
                // health-check 必须走后端 HTTP API
                const healthUrl = shouldUseHttpApi() ? httpApiBase() : null;
                if (healthUrl) {
                  const res = await safeFetch(`${healthUrl}/api/health/check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}), signal: AbortSignal.timeout(60_000) });
                  const data = await res.json();
                  results = data.results || [];
                } else {
                  setError(t("status.needServiceRunning"));
                  setHealthChecking(null);
                  return;
                }
                const h: typeof endpointHealth = {};
                for (const r of results) { h[r.name] = { status: r.status, latencyMs: r.latency_ms, error: r.error, errorCategory: r.error_category, consecutiveFailures: r.consecutive_failures, cooldownRemaining: r.cooldown_remaining, isExtendedCooldown: r.is_extended_cooldown, lastCheckedAt: r.last_checked_at }; }
                setEndpointHealth(h);
              } catch (e) { setError(String(e)); } finally { setHealthChecking(null); }
            }} disabled={!!healthChecking || !!busy}>
              {healthChecking === "all" ? t("status.checking") : t("status.checkAll")}
            </button>
          </div>
          {endpointSummary.length === 0 ? (
            <div className="cardHint">{t("status.noEndpoints")}</div>
          ) : (
            <div className="epTable">
              <div className="epTableHeader">
                <span>{t("status.endpoint")}</span>
                <span>{t("status.model")}</span>
                <span>Key</span>
                <span>{t("sidebar.status")}</span>
                <span></span>
              </div>
              {endpointSummary.map((e) => {
                const h = endpointHealth[e.name];
                const dotClass = h ? (h.status === "healthy" ? "healthy" : h.status === "degraded" ? "degraded" : "unhealthy") : e.keyPresent ? "unknown" : "unhealthy";
                const label = h
                  ? h.status === "healthy" ? (h.latencyMs != null ? h.latencyMs + "ms" : "OK") : (h.error || "").slice(0, 30)
                  : e.keyPresent ? "—" : t("status.keyMissing");
                return (
                  <div key={e.name} className="epTableRow">
                    <span className="epTableName">{e.name}</span>
                    <span className="epTableModel">{e.model}</span>
                    <span>{e.keyPresent ? <DotGreen /> : <DotGray />}</span>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <span className={"healthDot " + dotClass} />
                      <span className="epTableStatus">{label}</span>
                    </span>
                    <button className="btnSmall" onClick={async () => {
                      setHealthChecking(e.name);
                      try {
                        let r: any[];
                        const healthUrl = shouldUseHttpApi() ? httpApiBase() : null;
                        if (healthUrl) {
                          const res = await safeFetch(`${healthUrl}/api/health/check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint_name: e.name }), signal: AbortSignal.timeout(60_000) });
                          const data = await res.json();
                          r = data.results || [];
                        } else {
                          setError(t("status.needServiceRunning"));
                          setHealthChecking(null);
                          return;
                        }
                        if (r[0]) setEndpointHealth((prev: any) => ({ ...prev, [r[0].name]: { status: r[0].status, latencyMs: r[0].latency_ms, error: r[0].error, errorCategory: r[0].error_category, consecutiveFailures: r[0].consecutive_failures, cooldownRemaining: r[0].cooldown_remaining, isExtendedCooldown: r[0].is_extended_cooldown, lastCheckedAt: r[0].last_checked_at } }));
                      } catch (err) { setError(String(err)); } finally { setHealthChecking(null); }
                    }} disabled={!!healthChecking || !!busy}>{healthChecking === e.name ? "..." : t("status.check")}</button>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* IM Channels + Skills side by side */}
        <div className="statusGrid2" style={{ marginTop: 12 }}>
          <div className="card" style={{ marginTop: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span className="statusCardLabel">{t("status.imChannels")}</span>
              <button className="btnSmall" onClick={async () => {
                setImChecking(true);
                try {
                  const healthUrl = shouldUseHttpApi() ? httpApiBase() : null;
                  if (healthUrl) {
                    const res = await safeFetch(`${healthUrl}/api/im/channels`);
                    const data = await res.json();
                    const channels = data.channels || [];
                    const h: typeof imHealth = {};
                    for (const c of channels) {
                      h[c.channel || c.name] = { status: c.status || "unknown", error: c.error || null, lastCheckedAt: c.last_checked_at || null };
                    }
                    setImHealth(h);
                  } else {
                    setError(t("status.needServiceRunning"));
                  }
                } catch (err) { setError(String(err)); } finally { setImChecking(false); }
              }} disabled={imChecking || !!busy}>
                {imChecking ? "..." : t("status.checkAll")}
              </button>
            </div>
            {imStatus.map((c) => {
              const channelId = c.k.replace("_ENABLED", "").toLowerCase();
              const ih = imHealth[channelId];
              const isOnline = ih && (ih.status === "healthy" || ih.status === "online");
              // If imHealth has data for this channel, trust it over envDraft (handles remote mode)
              const effectiveEnabled = ih ? true : c.enabled;
              const dot = !effectiveEnabled ? "disabled" : ih ? (isOnline ? "healthy" : "unhealthy") : c.ok ? "unknown" : "degraded";
              return (
                <div key={c.k} className="imStatusRow">
                  <span className={"healthDot " + dot} />
                  <span style={{ fontWeight: 600, fontSize: 13, flex: 1 }}>{c.name}</span>
                  <span className="imStatusLabel">{!effectiveEnabled ? t("status.disabled") : ih ? (isOnline ? t("status.online") : t("status.offline")) : c.ok ? t("status.configured") : t("status.keyMissing")}</span>
                </div>
              );
            })}
          </div>
          <div className="card" style={{ marginTop: 0 }}>
            <span className="statusCardLabel">Skills</span>
            {skillSummary ? (
              <div style={{ marginTop: 8 }}>
                <div className="statusMetric"><span>{t("status.total")}</span><b>{skillSummary.count}</b></div>
                <div className="statusMetric"><span>{t("skills.system")}</span><b>{skillSummary.systemCount}</b></div>
                <div className="statusMetric"><span>{t("skills.external")}</span><b>{skillSummary.externalCount}</b></div>
              </div>
            ) : <div className="cardHint" style={{ marginTop: 8 }}>{t("status.skillsNA")}</div>}
            <button className="btnSmall" style={{ marginTop: 10, width: "100%" }} onClick={() => setView("skills")}>{t("status.manageSkills")}</button>
          </div>
        </div>

        {/* Service log */}
        {serviceStatus?.running && (
          <div className="card" style={{ marginTop: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <span className="statusCardLabel">{t("status.log")}</span>
              <button className="btnSmall" onClick={() => { const wsId = effectiveWsId || (dataMode === "remote" ? "__remote__" : null); if (wsId) refreshServiceLog(wsId); }}>{t("topbar.refresh")}</button>
            </div>
            <pre className="logPre">{(serviceLog?.content || "").trim() || t("status.noLog")}</pre>
          </div>
        )}
      </>
    );
  }

  function renderWelcome() {
    const quickFeatures = [
      t("welcome.quickFeature1"),
      t("welcome.quickFeature2"),
      t("welcome.quickFeature3"),
      t("welcome.quickFeature4"),
    ];
    const fullSteps = [
      { icon: "1", title: t("config.step.workspace"), desc: t("welcome.step1") },
      { icon: "2", title: "Python", desc: t("welcome.step2") },
      { icon: "3", title: t("welcome.installTitle"), desc: t("welcome.step3") },
      { icon: "4", title: t("config.step.endpoints"), desc: t("welcome.step4") },
      { icon: "5", title: t("welcome.configTitle"), desc: t("welcome.step5") },
    ];
    return (
      <>
        {/* Platform info bar */}
        <div className="card" style={{ padding: "12px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            {info && (
              <>
                <span className="welcomeInfoTag">OS: {info.os}</span>
                <span className="welcomeInfoTag">Arch: {info.arch}</span>
                <span className="welcomeInfoTag">Home: {info.homeDir}</span>
              </>
            )}
          </div>
        </div>

        {/* Mode selection */}
        <div className="card" style={{ marginTop: 12 }}>
          <div className="cardTitle">{t("welcome.modeTitle")}</div>
          <div className="cardHint" style={{ marginBottom: 20 }}>{t("welcome.modeSubtitle")}</div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            {/* Quick Setup Card */}
            <div
              className="card"
              style={{
                marginTop: 0, cursor: "pointer", border: "2px solid var(--primary, #1976d2)",
                background: "linear-gradient(135deg, #e3f2fd 0%, #f5f5f5 100%)",
                transition: "box-shadow 0.2s, transform 0.15s",
              }}
              onClick={() => {
                setConfigMode("quick");
                setStepId("quick-form");
                setMaxReachedStepIdx(1);
                localStorage.setItem("openakita_maxStep", "1");
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 20px rgba(25,118,210,0.25)"; (e.currentTarget as HTMLElement).style.transform = "translateY(-2px)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = ""; (e.currentTarget as HTMLElement).style.transform = ""; }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <div style={{
                  width: 40, height: 40, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center",
                  background: "var(--primary, #1976d2)", color: "#fff", flexShrink: 0,
                }}><IconZap size={22} /></div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 16, color: "var(--primary, #1976d2)" }}>{t("welcome.quickTitle")}</div>
                  <div style={{ fontSize: 11, opacity: 0.6 }}>{t("welcome.quickTime")}</div>
                </div>
              </div>
              <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 12 }}>{t("welcome.quickDesc")}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {quickFeatures.map((f, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, opacity: 0.7 }}>
                    <IconCheck size={14} style={{ color: "var(--primary, #1976d2)", flexShrink: 0 }} />
                    <span>{f}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Full Setup Card */}
            <div
              className="card"
              style={{ marginTop: 0, cursor: "pointer", border: "2px solid transparent", transition: "box-shadow 0.2s, transform 0.15s, border-color 0.2s" }}
              onClick={() => {
                setConfigMode("full");
                setStepId("workspace");
                setMaxReachedStepIdx(0);
                localStorage.setItem("openakita_maxStep", "0");
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 16px rgba(0,0,0,0.1)"; (e.currentTarget as HTMLElement).style.transform = "translateY(-2px)"; (e.currentTarget as HTMLElement).style.borderColor = "#bdbdbd"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = ""; (e.currentTarget as HTMLElement).style.transform = ""; (e.currentTarget as HTMLElement).style.borderColor = "transparent"; }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <div style={{
                  width: 40, height: 40, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center",
                  background: "#78909c", color: "#fff", flexShrink: 0,
                }}><IconGear size={22} /></div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>{t("welcome.fullTitle")}</div>
                  <div style={{ fontSize: 11, opacity: 0.6 }}>{t("welcome.fullTime")}</div>
                </div>
              </div>
              <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 12 }}>{t("welcome.fullDesc")}</div>
              <div className="welcomeSteps" style={{ gap: 6 }}>
                {fullSteps.map((s, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, opacity: 0.7 }}>
                    <div style={{
                      width: 20, height: 20, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                      background: "#e0e0e0", fontSize: 10, fontWeight: 700, flexShrink: 0,
                    }}>{s.icon}</div>
                    <span>{s.title}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </>
    );
  }

  // ── Quick Auto Setup: runs workspace creation, Python install, venv, pip, and default .env in one go ──
  const [quickSetupPhase, setQuickSetupPhase] = useState<number>(0);
  const [quickSetupError, setQuickSetupError] = useState<string | null>(null);
  const quickSetupStarted = useRef(false);
  const [quickImExpanded, setQuickImExpanded] = useState(false);

  const QUICK_ENV_DEFAULTS: Record<string, string> = {
    AGENT_NAME: "OpenAkita",
    MAX_ITERATIONS: "300",
    THINKING_MODE: "auto",
    AUTO_CONFIRM: "false",
    DATABASE_PATH: "data/agent.db",
    PERSONA_NAME: "default",
    PROACTIVE_ENABLED: "true",
    PROACTIVE_MAX_DAILY_MESSAGES: "3",
    PROACTIVE_QUIET_HOURS_START: "23",
    PROACTIVE_QUIET_HOURS_END: "7",
    PROACTIVE_MIN_INTERVAL_MINUTES: "120",
    PROACTIVE_IDLE_THRESHOLD_HOURS: "24",
    STICKER_ENABLED: "true",
    STICKER_DATA_DIR: "data/sticker",
    MCP_ENABLED: "true",
    MCP_BROWSER_ENABLED: "true",
    DESKTOP_ENABLED: "true",
    WHISPER_MODEL: "base",
    WHISPER_LANGUAGE: "zh",
    LOG_LEVEL: "INFO",
    LOG_DIR: "logs",
    LOG_FILE_PREFIX: "openakita",
    LOG_MAX_SIZE_MB: "10",
    LOG_BACKUP_COUNT: "30",
    LOG_RETENTION_DAYS: "30",
    LOG_TO_CONSOLE: "true",
    LOG_TO_FILE: "true",
    EMBEDDING_MODEL: "shibing624/text2vec-base-chinese",
    EMBEDDING_DEVICE: "cpu",
    MODEL_DOWNLOAD_SOURCE: "auto",
    MEMORY_HISTORY_DAYS: "30",
    MEMORY_MAX_HISTORY_FILES: "1000",
    MEMORY_MAX_HISTORY_SIZE_MB: "500",
    SCHEDULER_ENABLED: "true",
    SCHEDULER_TIMEZONE: "Asia/Shanghai",
    SCHEDULER_MAX_CONCURRENT: "5",
    SESSION_TIMEOUT_MINUTES: "30",
    SESSION_MAX_HISTORY: "50",
    SESSION_STORAGE_PATH: "data/sessions",
    ORCHESTRATION_ENABLED: "false",
    ORCHESTRATION_MODE: "single",
    TELEGRAM_ENABLED: "false",
    FEISHU_ENABLED: "false",
    WEWORK_ENABLED: "false",
    DINGTALK_ENABLED: "false",
    ONEBOT_ENABLED: "false",
    QQBOT_ENABLED: "false",
  };

  // ── Quick auto-setup effect: MUST be at component top level (not inside renderQuickAutoSetup) ──
  useEffect(() => {
    if (stepId !== "quick-setup" || configMode !== "quick") return;
    if (quickSetupStarted.current) return;
    if (!info) return; // wait for platform info
    quickSetupStarted.current = true;

    (async () => {
      try {
        // Phase 0: Create workspace
        setQuickSetupPhase(0);
        setQuickSetupError(null);
        try {
          const ws = await invoke<WorkspaceSummary>("create_workspace", {
            id: "default",
            name: "默认工作区",
            setCurrent: true,
          });
          await refreshAll();
          setCurrentWorkspaceId(ws.id);
          envLoadedForWs.current = null;
        } catch {
          // workspace may already exist, try to set it as current
          const wsList = await invoke<WorkspaceSummary[]>("list_workspaces");
          const existing = wsList.find((w) => w.id === "default");
          if (existing) {
            await invoke("set_current_workspace", { id: "default" });
            await refreshAll();
            setCurrentWorkspaceId("default");
            envLoadedForWs.current = null;
          } else {
            throw new Error("创建工作区失败");
          }
        }

        // Phase 1: Install embedded Python
        setQuickSetupPhase(1);
        const r = await invoke<EmbeddedPythonInstallResult>("install_embedded_python", { pythonSeries: "3.11" });
        const cand: PythonCandidate = {
          command: r.pythonCommand,
          versionText: `embedded (${r.tag}): ${r.assetName}`,
          isUsable: true,
        };
        setPythonCandidates((prev) => [cand, ...prev.filter((p) => p.command.join(" ") !== cand.command.join(" "))]);
        setSelectedPythonIdx(0);

        // Phase 2: Create venv + pip install
        setQuickSetupPhase(2);
        const curVenvDir = joinPath(info.openakitaRootDir, "venv");
        await invoke<string>("create_venv", { pythonCommand: r.pythonCommand, venvDir: curVenvDir });
        setVenvReady(true);
        setInstallLog("");
        const spec = `openakita[all]`;
        await invoke<string>("pip_install", {
          venvDir: curVenvDir,
          packageSpec: spec,
          indexUrl: null,
        });
        setOpenakitaInstalled(true);

        // Phase 3: Write default .env
        setQuickSetupPhase(3);
        const tauriEntries = Object.entries(QUICK_ENV_DEFAULTS).map(([key, value]) => ({ key, value }));
        await invoke("workspace_update_env", { workspaceId: "default", entries: tauriEntries });
        // Also update envDraft in memory
        setEnvDraft((prev) => {
          const next = { ...prev };
          for (const [k, v] of Object.entries(QUICK_ENV_DEFAULTS)) {
            next[k] = v;
          }
          return next;
        });

        // Phase 4: Save IM config (from envDraft filled on quick-form)
        setQuickSetupPhase(4);
        try {
          const imKeys = [
            "TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_PROXY",
            "TELEGRAM_REQUIRE_PAIRING", "TELEGRAM_PAIRING_CODE", "TELEGRAM_WEBHOOK_URL",
            "FEISHU_ENABLED", "FEISHU_APP_ID", "FEISHU_APP_SECRET",
            "WEWORK_ENABLED", "WEWORK_CORP_ID",
            "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY", "WEWORK_CALLBACK_PORT",
            "DINGTALK_ENABLED", "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET",
            "ONEBOT_ENABLED", "ONEBOT_WS_URL", "ONEBOT_ACCESS_TOKEN",
            "QQBOT_ENABLED", "QQBOT_APP_ID", "QQBOT_APP_SECRET", "QQBOT_SANDBOX", "QQBOT_MODE", "QQBOT_WEBHOOK_PORT", "QQBOT_WEBHOOK_PATH",
          ];
          const imEntries = imKeys
            .filter((k) => envDraft[k] !== undefined && envDraft[k] !== "")
            .map((k) => ({ key: k, value: envDraft[k] }));
          if (imEntries.length > 0) {
            await invoke("workspace_update_env", { workspaceId: "default", entries: imEntries });
          }
        } catch { /* ignore IM save errors */ }

        // Done — advance to quick-finish
        setQuickSetupPhase(5);

        setTimeout(() => {
          setStepId("quick-finish");
          setMaxReachedStepIdx((prev) => {
            const next = Math.max(prev, steps.length - 1);
            localStorage.setItem("openakita_maxStep", String(next));
            return next;
          });
        }, 800);
      } catch (e) {
        setQuickSetupError(String(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepId, configMode, info]);

  function renderQuickAutoSetup() {
    const phases = [
      t("welcome.quickStep1"),
      t("welcome.quickStep2"),
      t("welcome.quickStep3"),
      t("welcome.quickStep4"),
      t("welcome.quickStep5"),
    ];

    return (
      <>
        <div className="card">
          <div className="cardTitle">{t("welcome.quickSetupTitle")}</div>
          <div className="cardHint" style={{ marginBottom: 20 }}>{t("welcome.quickSetupSubtitle")}</div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {phases.map((label, idx) => {
              const isActive = quickSetupPhase === idx && !quickSetupError;
              const isDone = quickSetupPhase > idx;
              const isFailed = quickSetupPhase === idx && !!quickSetupError;
              return (
                <div key={idx} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{
                    width: 32, height: 32, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                    flexShrink: 0, fontSize: 14, fontWeight: 700,
                    background: isDone ? "var(--primary, #1976d2)" : isFailed ? "#e53935" : isActive ? "#e3f2fd" : "#f5f5f5",
                    color: isDone ? "#fff" : isFailed ? "#fff" : isActive ? "var(--primary, #1976d2)" : "#999",
                    transition: "all 0.3s",
                  }}>
                    {isDone ? <IconCheck size={16} /> : isFailed ? <IconX size={16} /> : idx + 1}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: 14, color: isDone ? "var(--primary, #1976d2)" : isFailed ? "#e53935" : undefined }}>
                      {label}
                    </div>
                    {isActive && !quickSetupError && (
                      <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }}>
                        <span className="loadingDots">...</span>
                      </div>
                    )}
                  </div>
                  {isActive && !quickSetupError && (
                    <div className="spinner" style={{ width: 18, height: 18 }} />
                  )}
                </div>
              );
            })}
          </div>

          {quickSetupPhase >= 5 && !quickSetupError && (
            <div style={{ marginTop: 16, padding: "12px 16px", background: "#e8f5e9", borderRadius: 8, fontSize: 13, color: "#2e7d32" }}>
              <IconCheckCircle size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />
              {t("welcome.quickStepDone")}
            </div>
          )}

          {quickSetupError && (
            <div style={{ marginTop: 16 }}>
              <div style={{ padding: "12px 16px", background: "#ffebee", borderRadius: 8, fontSize: 13, color: "#c62828", marginBottom: 12 }}>
                {t("welcome.quickStepFail")}
                <div style={{ marginTop: 6, fontSize: 11, opacity: 0.8, whiteSpace: "pre-wrap" }}>{quickSetupError}</div>
              </div>
              <button className="btnPrimary" onClick={() => {
                quickSetupStarted.current = false;
                setQuickSetupPhase(0);
                setQuickSetupError(null);
                setConfigMode(null);
                setStepId("welcome");
                setMaxReachedStepIdx(0);
                localStorage.setItem("openakita_maxStep", "0");
              }}>
                {t("welcome.quickSwitchFull")}
              </button>
            </div>
          )}
        </div>
      </>
    );
  }

  function renderQuickForm() {
    const canStart = savedEndpoints.length > 0;
    return (
      <>
        {/* ── Intro hint ── */}
        <div className="card">
          <div className="cardTitle">{t("welcome.quickFormTitle")}</div>
          <div className="cardHint">{t("welcome.quickFormHint")}</div>
        </div>

        {/* ── LLM Endpoint section (reuse renderLLM content) ── */}
        {renderLLM()}

        {/* ── Optional IM section (collapsible) ── */}
        <div className="card" style={{ marginTop: 16 }}>
          <div
            style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
            onClick={() => setQuickImExpanded((v) => !v)}
            role="button" tabIndex={0}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="cardTitle" style={{ marginBottom: 0 }}>{t("config.imTitle")}</div>
              <span className="pill" style={{ fontSize: 10, padding: "2px 8px", background: "#f1f5f9", color: "#64748b" }}>{t("welcome.quickFormOptional")}</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, opacity: 0.6 }}>
              <span>{quickImExpanded ? t("welcome.quickFormCollapse") : t("welcome.quickFormExpand")}</span>
              {quickImExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            </div>
          </div>
          <div className="cardHint" style={{ marginTop: 4 }}>{t("welcome.quickFormImHint")}</div>
        </div>
        {quickImExpanded && renderIM()}

        {/* ── Start button ── */}
        <div style={{ marginTop: 24, textAlign: "center" }}>
          <button
            className="btnPrimary"
            style={{ padding: "12px 48px", fontSize: 16, fontWeight: 700, borderRadius: 10 }}
            disabled={!canStart || !!busy}
            onClick={() => {
              setStepId("quick-setup");
              setMaxReachedStepIdx((prev) => {
                const next = Math.max(prev, 2);
                localStorage.setItem("openakita_maxStep", String(next));
                return next;
              });
            }}
          >
            {t("welcome.quickFormStart")}
          </button>
          {!canStart && (
            <div style={{ marginTop: 8, fontSize: 12, opacity: 0.6, color: "#e53935" }}>
              {t("welcome.quickFormNeedEndpoint")}
            </div>
          )}
        </div>
      </>
    );
  }

    function renderWorkspace() {
    return (
      <>
        <div className="card">
          <div className="cardTitle">{t("config.wsTitle")}</div>
          <div className="cardHint">
            工作区会生成并维护：`.env`、`data/llm_endpoints.json`、`identity/SOUL.md`。你可以为“生产/测试/不同客户”分别建立工作区。
          </div>
          <div className="divider" />
          <div className="row">
            <div className="field" style={{ minWidth: 320, flex: "1 1 auto" }}>
              <div className="labelRow">
                <div className="label">{t("config.wsName")}</div>
                <div className="help">{t("config.wsIdHint")}</div>
              </div>
              <input value={newWsName} onChange={(e) => setNewWsName(e.target.value)} placeholder={t("config.wsPlaceholder")} />
              <div className="help">
                {t("config.wsGenId")}: <b>{newWsId}</b>
              </div>
            </div>
            <button className="btnPrimary" onClick={doCreateWorkspace} disabled={!!busy || !newWsName.trim()}>
              {t("config.wsCreate")}
            </button>
          </div>
        </div>

        <div className="card">
          <div className="cardTitle">{t("config.wsExisting")}</div>
          {workspaces.length === 0 ? (
            <div className="cardHint">{t("config.wsEmpty")}</div>
          ) : (
            <div style={{ display: "grid", gap: 10 }}>
              {workspaces.map((w) => (
                <div
                  key={w.id}
                  className="card"
                  style={{
                    marginTop: 0,
                    borderColor: w.isCurrent ? "rgba(14, 165, 233, 0.22)" : "var(--line)",
                    background: w.isCurrent ? "rgba(14, 165, 233, 0.06)" : "rgba(255, 255, 255, 0.72)",
                  }}
                >
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <div>
                      <div style={{ fontWeight: 800 }}>
                        {w.name} <span style={{ color: "var(--muted)", fontWeight: 500 }}>({w.id})</span>
                        {w.isCurrent ? <span style={{ marginLeft: 8, color: "var(--brand)" }}>{t("config.wsCurrent")}</span> : null}
                      </div>
                      <div className="help" style={{ marginTop: 6 }}>
                        {w.path}
                      </div>
                    </div>
                    <div className="btnRow">
                      <button onClick={() => doSetCurrentWorkspace(w.id)} disabled={!!busy || w.isCurrent}>
                        {t("config.wsSetCurrent")}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="okBox">
            下一步建议：进入“Python”，优先使用“内置 Python”以实现真正的一键安装（尤其是 Windows）。
          </div>
        </div>
      </>
    );
  }

  function renderPython() {
    return (
      <>
        <div className="card">
          <div className="cardTitle">{t("config.pyTitle")}</div>
          <div className="cardHint">{t("config.pyHint")}</div>
          <div className="divider" />
          <div className="btnRow">
            <button className="btnPrimary" onClick={doInstallEmbeddedPython} disabled={!!busy}>
              {t("config.pyEmbed")}
            </button>
            <button onClick={doDetectPython} disabled={!!busy}>
              {t("config.pyDetect")}
            </button>
          </div>
          {pythonCandidates.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="row" style={{ alignItems: "center", gap: 10 }}>
                <span className="label" style={{ marginBottom: 0, whiteSpace: "nowrap" }}>{t("config.pySelect")}</span>
                <select style={{ flex: 1, maxWidth: 420, textOverflow: "ellipsis" }} value={selectedPythonIdx} onChange={(e) => setSelectedPythonIdx(Number(e.target.value))}
                  title={selectedPythonIdx >= 0 ? pythonCandidates[selectedPythonIdx]?.command.join(" ") : ""}>
                  <option value={-1}>--</option>
                  {pythonCandidates.map((c, idx) => {
                    const full = c.command.join(" ");
                    const short = full.length > 60 ? "..." + full.slice(-55) : full;
                    return (
                      <option key={idx} value={idx} title={full}>
                        {short} — {c.versionText}
                      </option>
                    );
                  })}
                </select>
              </div>
            </div>
          )}
          {venvStatus && <div className="okBox" style={{ marginTop: 10 }}>{venvStatus}</div>}
          {canUsePython && <div className="okBox" style={{ marginTop: 10 }}>{t("config.pyReady")}</div>}
        </div>
      </>
    );
  }

  function renderInstall() {
    const venvPath = venvDir;
    const installReadyText = openakitaInstalled
      ? t("config.installDone")
      : venvReady
        ? t("config.installVenvReady")
        : t("config.installReady");
    return (
      <>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <div className="cardTitle" style={{ marginBottom: 0 }}>{t("config.installTitle")}</div>
            <div className="pill" style={{ gap: 6 }}>
              <span className="help">venv</span>
              <span style={{ fontWeight: 700 }}>{venvPath}</span>
            </div>
          </div>
          <div className="divider" />

          {/* Source / Version / Mirror in one row */}
          <div className="grid3" style={{ alignItems: "flex-start" }}>
            <div className="field">
              <div className="label">{t("config.installSource")}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {(["pypi", "github", "local"] as const).map((s) => (
                  <button key={s} className={installSource === s ? "capChipActive" : "capChip"}
                    onClick={() => setInstallSource(s)} disabled={!!busy}>
                    {s === "pypi" ? t("config.installPypi") : s === "github" ? "GitHub" : t("config.installLocal")}
                  </button>
                ))}
              </div>
            </div>

            {installSource === "pypi" && (
              <div className="field">
                <div className="label">{t("config.installVersion")}</div>
                <div className="row" style={{ gap: 6 }}>
                  <button className="btnSmall" onClick={doFetchPypiVersions}
                    disabled={!!busy || pypiVersionsLoading} style={{ whiteSpace: "nowrap" }}>
                    {pypiVersionsLoading ? "..." : t("config.installFetchVer")}
                  </button>
                  {pypiVersions.length > 0 ? (
                    <select value={selectedPypiVersion} onChange={(e) => setSelectedPypiVersion(e.target.value)}
                      disabled={!!busy} style={{ flex: 1 }}>
                      {pypiVersions.map((v) => (
                        <option key={v} value={v}>
                          {v}{v === appVersion ? ` (${t("config.installRecommended")})` : v === pypiVersions[0] ? ` (${t("config.installLatest")})` : ""}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input value={selectedPypiVersion} onChange={(e) => setSelectedPypiVersion(e.target.value)}
                      placeholder={appVersion || ""} disabled={!!busy} style={{ flex: 1 }} />
                  )}
                </div>
              </div>
            )}

            {installSource === "github" && (
              <div className="field">
                <div className="label">GitHub</div>
                <input value={githubRepo} onChange={(e) => setGithubRepo(e.target.value)} placeholder="openakita/openakita" />
                <div className="row" style={{ gap: 6, marginTop: 6 }}>
                  <select value={githubRefType} onChange={(e) => setGithubRefType(e.target.value as any)} style={{ width: 100 }}>
                    <option value="branch">branch</option>
                    <option value="tag">tag</option>
                  </select>
                  <input value={githubRef} onChange={(e) => setGithubRef(e.target.value)} placeholder="main" style={{ flex: 1 }} />
                </div>
              </div>
            )}

            {installSource === "local" && (
              <div className="field">
                <div className="label">{t("config.installLocal")}</div>
                <input value={localSourcePath} onChange={(e) => setLocalSourcePath(e.target.value)} placeholder="D:\\coder\\myagent" />
              </div>
            )}

            <div className="field">
              <div className="label">{t("config.installMirror")}</div>
              <select value={pipIndexPresetId}
                onChange={(e) => {
                  const id = e.target.value as "official" | "tuna" | "aliyun" | "custom";
                  setPipIndexPresetId(id);
                  const preset = PIP_INDEX_PRESETS.find((p) => p.id === id);
                  if (!preset) return;
                  if (id === "custom") { setIndexUrl(customIndexUrl); return; }
                  setIndexUrl(preset.url);
                }}>
                {PIP_INDEX_PRESETS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Extras chips */}
          <div style={{ marginTop: 12 }}>
            <div className="label">extras</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
              {["all", "windows", "browser", "whisper", "feishu", "dingtalk", "wework", "onebot", "qqbot"].map((x) => (
                <button key={x} className={extras === x ? "capChipActive" : "capChip"}
                  onClick={() => setExtras(x)} disabled={!!busy}>{x}</button>
              ))}
            </div>
          </div>

          <div className="divider" />

          {/* Action button */}
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div className="cardHint" style={{ marginTop: 0 }}><b>{installReadyText}</b></div>
            <button className="btnPrimary" onClick={doSetupVenvAndInstallOpenAkita} disabled={!canUsePython || !!busy}>
              {openakitaInstalled ? t("config.installUpgrade") : t("config.installAction")}
            </button>
          </div>
          {venvStatus && <div className="okBox" style={{ marginTop: 8 }}>{venvStatus}</div>}

          {/* Progress bar during install */}
          {!!busy && (busy || "").includes("venv") && (
            <div style={{ marginTop: 12 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div className="help">{installProgress ? `${installProgress.stage} (${installProgress.percent}%)` : t("common.loading")}</div>
                <button className="btnSmall" onClick={() => setInstallLiveLog("")} disabled={!!busy}>{t("config.installClearLog")}</button>
              </div>
              <div style={{ marginTop: 6, height: 8, borderRadius: 999, background: "var(--bg1)", overflow: "hidden" }}>
                <div style={{ width: `${installProgress?.percent ?? 5}%`, height: "100%", background: "var(--brand)", transition: "width 180ms ease" }} />
              </div>
              <pre className="logPre" style={{ marginTop: 8, maxHeight: 180 }}>{installLiveLog || t("config.installWaiting")}</pre>
            </div>
          )}

          {installLog && (
            <details style={{ marginTop: 10 }}>
              <summary className="dialogDetails" style={{ cursor: "pointer", fontWeight: 700, fontSize: 13 }}>{t("config.installShowLog")}</summary>
              <pre className="logPre" style={{ marginTop: 6, maxHeight: 200 }}>{installLog}</pre>
            </details>
          )}

          {openakitaInstalled && <div className="okBox" style={{ marginTop: 10 }}>{t("config.installDoneNext")}</div>}
        </div>
      </>
    );
  }

  // ── Add endpoint dialog state ──
  const [addEpDialogOpen, setAddEpDialogOpen] = useState(false);
  const [addCompDialogOpen, setAddCompDialogOpen] = useState(false);

  function openAddEpDialog() {
    resetEndpointEditor();
    setConnTestResult(null);
    doLoadProviders();
    setAddEpDialogOpen(true);
  }

  function renderLLM() {
    return (
      <>
        {/* ── Main endpoint list ── */}
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div>
              <div className="cardTitle" style={{ marginBottom: 2 }}>{t("llm.title")}</div>
              <div className="cardHint">{t("llm.subtitle")}</div>
            </div>
            <button className="btnPrimary" style={{ whiteSpace: "nowrap" }} onClick={openAddEpDialog} disabled={!!busy}>
              + {t("llm.addEndpoint")}
            </button>
          </div>

          {savedEndpoints.length === 0 ? (
            <div className="cardHint" style={{ textAlign: "center", padding: "24px 0" }}>{t("llm.noEndpoints")}</div>
          ) : (
            <div className="epTable">
              <div className="epTableHeader">
                <span>{t("status.endpoint")}</span>
                <span>{t("status.model")}</span>
                <span>Key</span>
                <span>Priority</span>
                <span></span>
              </div>
              {savedEndpoints.map((e) => (
                <div key={e.name} className="epTableRow">
                  <span className="epTableName">
                    {e.name}
                    {savedEndpoints[0]?.name === e.name && <span style={{ marginLeft: 6, color: "var(--brand)", fontSize: 10, fontWeight: 800 }}>{t("llm.primary")}</span>}
                  </span>
                  <span className="epTableModel">{e.model}</span>
                  <span>{(envDraft[e.api_key_env] || "").trim() ? <DotGreen /> : <DotGray />}</span>
                  <span style={{ fontSize: 12 }}>{e.priority}</span>
                  <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                    {savedEndpoints[0]?.name !== e.name && <button className="btnIcon" onClick={() => doSetPrimaryEndpoint(e.name)} disabled={!!busy} title={t("llm.setPrimary")}><IconChevronUp size={14} /></button>}
                    <button className="btnIcon" onClick={() => doStartEditEndpoint(e.name)} disabled={!!busy} title={t("llm.edit")}><IconEdit size={14} /></button>
                    <button className="btnIcon btnIconDanger" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Compiler endpoints ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <div>
              <div className="statusCardLabel">{t("llm.compiler")}</div>
              <div className="cardHint" style={{ fontSize: 11 }}>{t("llm.compilerHint")}</div>
            </div>
            <button className="btnSmall btnSmallPrimary" onClick={() => { doLoadProviders(); setAddCompDialogOpen(true); }} disabled={!!busy}>
              + {t("llm.addEndpoint")}
            </button>
          </div>
          {savedCompilerEndpoints.length === 0 ? (
            <div className="cardHint">{t("llm.noCompiler")}</div>
          ) : (
            <div style={{ display: "grid", gap: 6 }}>
              {savedCompilerEndpoints.map((e) => (
                <div key={e.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid rgba(0,0,0,0.04)" }}>
                  <div>
                    <span style={{ fontWeight: 700, fontSize: 13 }}>{e.name}</span>
                    <span style={{ color: "var(--muted)", fontSize: 11, marginLeft: 8 }}>{e.model} · {e.provider}</span>
                  </div>
                  <button className="btnIcon btnIconDanger" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteCompilerEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Add endpoint dialog ── */}
        {addEpDialogOpen && (
          <div className="modalOverlay" onClick={() => setAddEpDialogOpen(false)}>
            <div className="modalContent" onClick={(e) => e.stopPropagation()}>
              <div className="dialogHeader">
                <div className="cardTitle">{isEditingEndpoint ? t("llm.editEndpoint") : t("llm.addEndpoint")}</div>
                <button className="dialogCloseBtn" onClick={() => { setAddEpDialogOpen(false); resetEndpointEditor(); }}><IconX size={14} /></button>
              </div>

              <div className="dialogBody">
              {/* Provider */}
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.provider")}</div>
                <select value={providerSlug} onChange={(e) => setProviderSlug(e.target.value)}>
                  {providers.length === 0 && <option value="">({t("common.loading")})</option>}
                  {providers.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
                </select>
                {providerApplyUrl && <div className="help" style={{ marginTop: 6, paddingLeft: 2 }}>Key: <a href={providerApplyUrl} target="_blank" rel="noreferrer">{providerApplyUrl}</a></div>}
              </div>

              {/* Base URL */}
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.baseUrl")}</div>
                <input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder={selectedProvider?.default_base_url || "https://api.example.com/v1"}
                  style={{ width: "100%", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--line)", fontSize: 13 }}
                />
                <div className="help" style={{ marginTop: 4, paddingLeft: 2 }}>{t("llm.baseUrlHint")}</div>
              </div>

              {/* API Key */}
              <div className="dialogSection">
                <div className="dialogLabel">API Key</div>
                <input
                  value={apiKeyValue}
                  onChange={(e) => setApiKeyValue(e.target.value)}
                  placeholder="sk-..."
                  type={secretShown.__LLM_API_KEY ? "text" : "password"}
                />
              </div>

              {/* Model name — always visible; fetch is optional */}
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.selectModel")}</div>
                <SearchSelect
                  value={selectedModelId}
                  onChange={(v) => setSelectedModelId(v)}
                  options={models.map((m) => m.id)}
                  placeholder={models.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")}
                  disabled={!!busy}
                />
                {models.length === 0 && (
                  <div className="help" style={{ marginTop: 4, paddingLeft: 2, display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ opacity: 0.7 }}>{t("llm.modelManualHint")}</span>
                    <button onClick={doFetchModels} className="btnSmall" disabled={!apiKeyValue.trim() || !baseUrl.trim() || !!busy}
                      style={{ fontSize: 11, padding: "2px 10px", borderRadius: 6 }}>
                      {t("llm.fetchModels")}
                    </button>
                  </div>
                )}
                {models.length > 0 && (
                  <div className="help" style={{ marginTop: 4, paddingLeft: 2, display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ opacity: 0.6 }}>{t("llm.modelFetched", { count: models.length })}</span>
                    <button onClick={doFetchModels} className="btnSmall" disabled={!apiKeyValue.trim() || !baseUrl.trim() || !!busy}
                      style={{ fontSize: 11, padding: "2px 10px", borderRadius: 6 }}>
                      {t("llm.refetch")}
                    </button>
                  </div>
                )}
                {error && (
                  <div style={{ marginTop: 6, padding: "6px 10px", background: "rgba(229,57,53,0.12)", border: "1px solid rgba(229,57,53,0.3)", borderRadius: 6, fontSize: 12, color: "#e53935", wordBreak: "break-all" }}>
                    ⚠ {error}
                  </div>
                )}
              </div>

              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.endpointName")}</div>
                <input
                  value={endpointName}
                  onChange={(e) => { setEndpointNameTouched(true); setEndpointName(e.target.value); }}
                  placeholder="dashscope-qwen3-max"
                />
              </div>

              {/* Capabilities as chips */}
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.capabilities")}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {[
                    { k: "text", name: t("llm.capText") },
                    { k: "thinking", name: t("llm.capThinking") },
                    { k: "vision", name: t("llm.capVision") },
                    { k: "video", name: t("llm.capVideo") },
                    { k: "tools", name: t("llm.capTools") },
                  ].map((c) => {
                    const on = capSelected.includes(c.k);
                    return (
                      <span key={c.k} className={`capChip ${on ? "capChipActive" : ""}`}
                        onClick={() => { setCapTouched(true); setCapSelected((prev) => { const set = new Set(prev); if (set.has(c.k)) set.delete(c.k); else set.add(c.k); const out = Array.from(set); return out.length ? out : ["text"]; }); }}
                      >{on ? "\u2713 " : ""}{c.name}</span>
                    );
                  })}
                </div>
              </div>

              {/* Advanced (collapsed) */}
              <details className="dialogDetails">
                <summary>{t("llm.advanced")}</summary>
                <div>
                  <div style={{ marginBottom: 10 }}>
                    <div className="dialogLabel">API Type</div>
                    <select value={apiType} onChange={(e) => setApiType(e.target.value as any)} style={{ width: 140, padding: "8px 10px", borderRadius: 8, border: "1px solid var(--line)", fontSize: 13 }}>
                      <option value="openai">openai</option>
                      <option value="anthropic">anthropic</option>
                    </select>
                  </div>
                  <div style={{ marginBottom: 10 }}>
                    <div className="dialogLabel">Key Env Name</div>
                    <input value={apiKeyEnv} onChange={(e) => { setApiKeyEnvTouched(true); setApiKeyEnv(e.target.value); }} style={{ width: "100%", padding: "8px 10px", borderRadius: 8, border: "1px solid var(--line)", fontSize: 13 }} />
                  </div>
                  <div>
                    <div className="dialogLabel">Priority</div>
                    <input type="number" value={String(endpointPriority)} onChange={(e) => setEndpointPriority(Number(e.target.value))} style={{ width: 80, padding: "8px 10px", borderRadius: 8, border: "1px solid var(--line)", fontSize: 13 }} />
                  </div>
                </div>
              </details>
              </div>

              {/* 连接测试结果 */}
              {connTestResult && (
                <div className={`connTestResult ${connTestResult.ok ? "connTestOk" : "connTestFail"}`}>
                  {connTestResult.ok
                    ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                    : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
                </div>
              )}

              {/* Footer — fixed at bottom */}
              <div className="dialogFooter">
                <button className="btnSmall" onClick={() => { setAddEpDialogOpen(false); resetEndpointEditor(); setConnTestResult(null); }}>{t("common.cancel")}</button>
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    className="btnSmall"
                    style={{ padding: "8px 16px", borderRadius: 8 }}
                    disabled={!apiKeyValue.trim() || !baseUrl.trim() || connTesting}
                    onClick={() => doTestConnection({ testApiType: apiType, testBaseUrl: baseUrl, testApiKey: apiKeyValue, testProviderSlug: selectedProvider?.slug })}
                  >
                    {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                  </button>
                  <button className="btnPrimary" style={{ padding: "8px 20px", borderRadius: 8 }} onClick={async () => { await doSaveEndpoint(); setAddEpDialogOpen(false); setConnTestResult(null); }} disabled={!selectedModelId.trim() || !apiKeyEnv.trim() || !apiKeyValue.trim() || !baseUrl.trim() || (!currentWorkspaceId && dataMode !== "remote") || !!busy}>
                    {isEditingEndpoint ? t("common.save") : t("llm.addEndpoint")}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Edit endpoint modal (existing) ── */}
        {editModalOpen && editDraft && (
          <div className="modalOverlay" onClick={() => resetEndpointEditor()}>
            <div className="modalContent" onClick={(e) => e.stopPropagation()}>
              <div className="dialogHeader">
                <div className="cardTitle">{t("llm.editEndpoint")}: {editDraft.name}</div>
                <button className="dialogCloseBtn" onClick={() => resetEndpointEditor()}><IconX size={14} /></button>
              </div>
              <div className="dialogBody">
              <div className="dialogSection">
                <div className="dialogLabel">Base URL</div>
                <input value={editDraft.baseUrl || ""} onChange={(e) => setEditDraft({ ...editDraft, baseUrl: e.target.value })} />
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">API Key</div>
                <div style={{ position: "relative" }}>
                  <input value={envDraft[editDraft.apiKeyEnv || ""] || ""} onChange={(e) => { const k = editDraft.apiKeyEnv || ""; const v = e.target.value; setEnvDraft((m) => ({ ...m, [k]: v })); setEditDraft((d) => d ? { ...d, apiKeyValue: v } : d); }} type={secretShown.__EDIT_EP_KEY ? "text" : "password"} style={{ paddingRight: 44, width: "100%" }} />
                  <button type="button" className="btnEye" onClick={() => setSecretShown((m) => ({ ...m, __EDIT_EP_KEY: !m.__EDIT_EP_KEY }))} title={secretShown.__EDIT_EP_KEY ? "隐藏" : "显示"}>
                    {secretShown.__EDIT_EP_KEY ? <IconEyeOff size={16} /> : <IconEye size={16} />}
                  </button>
                </div>
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">{t("status.model")}</div>
                <SearchSelect
                  value={editDraft.modelId || ""}
                  onChange={(v) => setEditDraft({ ...editDraft, modelId: v })}
                  options={editModels.length > 0 ? editModels.map(m => m.id) : [editDraft.modelId || ""].filter(Boolean)}
                  placeholder={editModels.length > 0 ? t("llm.searchModel") : (editDraft.modelId || t("llm.modelPlaceholder"))}
                  disabled={!!busy}
                />
                <div className="help" style={{ marginTop: 4, paddingLeft: 2, display: "flex", alignItems: "center", gap: 6 }}>
                  <button onClick={doFetchEditModels} className="btnSmall" disabled={!(envDraft[editDraft.apiKeyEnv || ""] || "").trim() || !(editDraft.baseUrl || "").trim() || !!busy}
                    style={{ fontSize: 11, padding: "2px 10px", borderRadius: 6 }}>
                    {t("llm.fetchModels")}
                  </button>
                  {editModels.length > 0 && <span style={{ opacity: 0.6 }}>{t("llm.modelFetched", { count: editModels.length })}</span>}
                </div>
                {error && (
                  <div style={{ marginTop: 6, padding: "6px 10px", background: "rgba(229,57,53,0.12)", border: "1px solid rgba(229,57,53,0.3)", borderRadius: 6, fontSize: 12, color: "#e53935", wordBreak: "break-all" }}>
                    ⚠ {error}
                  </div>
                )}
              </div>
              </div>

              {/* 连接测试结果 */}
              {connTestResult && (
                <div className={`connTestResult ${connTestResult.ok ? "connTestOk" : "connTestFail"}`}>
                  {connTestResult.ok
                    ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                    : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
                </div>
              )}

              <div className="dialogFooter">
                <button className="btnSmall" onClick={() => { resetEndpointEditor(); setConnTestResult(null); }}>{t("common.cancel")}</button>
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    className="btnSmall"
                    style={{ padding: "8px 16px", borderRadius: 8 }}
                    disabled={!(envDraft[editDraft.apiKeyEnv || ""] || "").trim() || !(editDraft.baseUrl || "").trim() || connTesting}
                    onClick={() => doTestConnection({
                      testApiType: editDraft.apiType || "openai",
                      testBaseUrl: editDraft.baseUrl || "",
                      testApiKey: envDraft[editDraft.apiKeyEnv || ""] || "",
                      testProviderSlug: editDraft.providerSlug,
                    })}
                  >
                    {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                  </button>
                  <button className="btnPrimary" style={{ padding: "8px 20px", borderRadius: 8 }} onClick={async () => { await doSaveEditedEndpoint(); setConnTestResult(null); }} disabled={!!busy}>{t("common.save")}</button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Add compiler dialog ── */}
        {addCompDialogOpen && (
          <div className="modalOverlay" onClick={() => setAddCompDialogOpen(false)}>
            <div className="modalContent" onClick={(e) => e.stopPropagation()}>
              <div className="dialogHeader">
                <div className="cardTitle">{t("llm.addCompiler")}</div>
                <button className="dialogCloseBtn" onClick={() => setAddCompDialogOpen(false)}><IconX size={14} /></button>
              </div>
              <div className="dialogBody">
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.provider")}</div>
                <select value={compilerProviderSlug} onChange={(e) => {
                  const slug = e.target.value;
                  setCompilerProviderSlug(slug);
                  if (slug === "__custom__") {
                    setCompilerApiType("openai");
                    setCompilerBaseUrl("");
                    setCompilerApiKeyEnv("CUSTOM_COMPILER_API_KEY");
                  } else {
                    const p = providers.find((x) => x.slug === slug);
                    if (p) {
                      setCompilerApiType((p.api_type as any) || "openai");
                      setCompilerBaseUrl(p.default_base_url || "");
                      const suggested = p.api_key_env_suggestion || envKeyFromSlug(p.slug);
                      const used = new Set(Object.keys(envDraft || {}));
                      for (const ep of [...savedEndpoints, ...savedCompilerEndpoints]) { if (ep.api_key_env) used.add(ep.api_key_env); }
                      setCompilerApiKeyEnv(nextEnvKeyName(suggested, used));
                    }
                  }
                }}>
                  <option value="">--</option>
                  {providers.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
                  <option value="__custom__">{t("llm.customProvider")}</option>
                </select>
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.baseUrl")}</div>
                <input value={compilerBaseUrl} onChange={(e) => setCompilerBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
                <div className="cardHint" style={{ fontSize: 11, marginTop: 2 }}>{t("llm.baseUrlHint")}</div>
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.apiKeyEnv")}</div>
                <input value={compilerApiKeyEnv} onChange={(e) => setCompilerApiKeyEnv(e.target.value)} placeholder="MY_API_KEY" />
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">API Key</div>
                <input value={compilerApiKeyValue} onChange={(e) => setCompilerApiKeyValue(e.target.value)} placeholder="sk-..." type="password" />
              </div>
              {/* Model name — always visible; fetch is optional */}
              <div className="dialogSection">
                <div className="dialogLabel">{t("status.model")}</div>
                <SearchSelect value={compilerModel} onChange={(v) => setCompilerModel(v)} options={compilerModels.map((m) => m.id)} placeholder={compilerModels.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")} disabled={!!busy} />
                {compilerModels.length === 0 && (
                  <div className="help" style={{ marginTop: 4, paddingLeft: 2, display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ opacity: 0.7 }}>{t("llm.modelManualHint")}</span>
                    <button onClick={doFetchCompilerModels} className="btnSmall" disabled={!compilerApiKeyValue.trim() || !compilerBaseUrl.trim() || !!busy}
                      style={{ fontSize: 11, padding: "2px 10px", borderRadius: 6 }}>
                      {t("llm.fetchModels")}
                    </button>
                  </div>
                )}
                {compilerModels.length > 0 && (
                  <div className="help" style={{ marginTop: 4, paddingLeft: 2, display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ opacity: 0.6 }}>{t("llm.modelFetched", { count: compilerModels.length })}</span>
                    <button onClick={doFetchCompilerModels} className="btnSmall" disabled={!compilerApiKeyValue.trim() || !compilerBaseUrl.trim() || !!busy}
                      style={{ fontSize: 11, padding: "2px 10px", borderRadius: 6 }}>
                      {t("llm.refetch")}
                    </button>
                  </div>
                )}
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.endpointName")} <span style={{ color: "var(--muted)", fontSize: 11 }}>({t("common.optional")})</span></div>
                <input value={compilerEndpointName} onChange={(e) => setCompilerEndpointName(e.target.value)} placeholder={`compiler-${compilerProviderSlug || "custom"}-${compilerModel || "model"}`} />
              </div>
              </div>
              <div className="dialogFooter">
                <button className="btnSmall" onClick={() => setAddCompDialogOpen(false)}>{t("common.cancel")}</button>
                <button className="btnPrimary" style={{ padding: "8px 20px", borderRadius: 8 }} onClick={async () => { await doSaveCompilerEndpoint(); setAddCompDialogOpen(false); }} disabled={!compilerModel.trim() || !compilerApiKeyEnv.trim() || !compilerApiKeyValue.trim() || (!currentWorkspaceId && dataMode !== "remote") || !!busy}>
                  {t("llm.addEndpoint")}
                </button>
              </div>
            </div>
          </div>
        )}
      </>
    );
  }

  // ── Helper: env field for IM / Tools / Agent config pages ──
  function FieldText({
    k, label, placeholder, help, type,
  }: { k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password"; }) {
    const isSecret = (type || "text") === "password";
    const shown = !!secretShown[k];
    return (
      <div className="field">
        <div className="labelRow">
          <div className="label">
            {label}
            {help && <span className="fieldTip" title={help}><IconInfo size={13} /></span>}
          </div>
          {k ? <div className="help">{k}</div> : null}
        </div>
        <div style={{ position: "relative" }}>
          <input
            value={envGet(envDraft, k)}
            onChange={(e) => setEnvDraft((m) => envSet(m, k, e.target.value))}
            placeholder={placeholder}
            type={isSecret ? (shown ? "text" : "password") : "text"}
            style={isSecret ? { paddingRight: 44 } : undefined}
          />
          {isSecret && (
            <button type="button" className="btnEye"
              onClick={() => setSecretShown((m) => ({ ...m, [k]: !m[k] }))}
              disabled={!!busy}
              title={shown ? t("skills.hide") : t("skills.show")}>
              {shown ? <IconEyeOff size={16} /> : <IconEye size={16} />}
            </button>
          )}
        </div>
      </div>
    );
  }

  function FieldBool({ k, label, help, defaultValue }: { k: string; label: string; help?: string; defaultValue?: boolean }) {
    const v = envGet(envDraft, k, defaultValue ? "true" : "false").toLowerCase() === "true";
    return (
      <div className="field">
        <div className="labelRow">
          <div className="label">
            {label}
            {help && <span className="fieldTip" title={help}><IconInfo size={13} /></span>}
          </div>
          <div className="help">{k}</div>
        </div>
        <label className="pill" style={{ cursor: "pointer" }}>
          <input style={{ width: 16, height: 16 }} type="checkbox" checked={v}
            onChange={(e) => setEnvDraft((m) => envSet(m, k, String(e.target.checked)))} />
          {t("skills.enabled")}
        </label>
      </div>
    );
  }

  function FieldSelect({
    k, label, options, help,
  }: { k: string; label: string; options: { value: string; label: string }[]; help?: string; }) {
    return (
      <div className="field">
        <div className="labelRow">
          <div className="label">
            {label}
            {help && <span className="fieldTip" title={help}><IconInfo size={13} /></span>}
          </div>
          {k ? <div className="help">{k}</div> : null}
        </div>
        <select
          value={envGet(envDraft, k)}
          onChange={(e) => setEnvDraft((m) => envSet(m, k, e.target.value))}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>
    );
  }

  function FieldCombo({
    k, label, options, placeholder, help,
  }: { k: string; label: string; options: { value: string; label: string }[]; placeholder?: string; help?: string; }) {
    const currentVal = envGet(envDraft, k);
    const isPreset = options.some((o) => o.value === currentVal);
    return (
      <div className="field">
        <div className="labelRow">
          <div className="label">
            {label}
            {help && <span className="fieldTip" title={help}><IconInfo size={13} /></span>}
          </div>
          {k ? <div className="help">{k}</div> : null}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <select
            style={{ flex: "0 0 auto", minWidth: 140 }}
            value={isPreset ? currentVal : "__custom__"}
            onChange={(e) => {
              if (e.target.value !== "__custom__") {
                setEnvDraft((m) => envSet(m, k, e.target.value));
              }
            }}
          >
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
            <option value="__custom__">{t("common.custom") || "自定义..."}</option>
          </select>
          {(!isPreset || currentVal === "") && (
            <input
              style={{ flex: 1 }}
              value={currentVal}
              onChange={(e) => setEnvDraft((m) => envSet(m, k, e.target.value))}
              placeholder={placeholder || t("common.custom") || "自定义输入..."}
            />
          )}
        </div>
      </div>
    );
  }

  async function renderIntegrationsSave(keys: string[], successText: string) {
    if (!currentWorkspaceId) { setError(t("common.error")); return; }
    setBusy(t("common.loading"));
    setError(null);
    try {
      await saveEnvKeys(keys);
      setNotice(successText);
    } finally {
      setBusy(null);
    }
  }

  function renderIM() {
    const keysIM = [
      "TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_PROXY",
      "TELEGRAM_REQUIRE_PAIRING", "TELEGRAM_PAIRING_CODE", "TELEGRAM_WEBHOOK_URL",
      "FEISHU_ENABLED", "FEISHU_APP_ID", "FEISHU_APP_SECRET",
      "WEWORK_ENABLED", "WEWORK_CORP_ID",
      "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY", "WEWORK_CALLBACK_PORT",
      "DINGTALK_ENABLED", "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET",
      "ONEBOT_ENABLED", "ONEBOT_WS_URL", "ONEBOT_ACCESS_TOKEN",
      "QQBOT_ENABLED", "QQBOT_APP_ID", "QQBOT_APP_SECRET", "QQBOT_SANDBOX", "QQBOT_MODE", "QQBOT_WEBHOOK_PORT", "QQBOT_WEBHOOK_PATH",
    ];

    const channels = [
      {
        title: "Telegram",
        appType: t("config.imTypeLongPolling"),
        logo: <LogoTelegram size={22} />,
        enabledKey: "TELEGRAM_ENABLED",
        docUrl: "https://t.me/BotFather",
        needPublicIp: false,
        body: (
          <>
            <FieldText k="TELEGRAM_BOT_TOKEN" label={t("config.imBotToken")} placeholder="BotFather token" type="password" />
            <FieldText k="TELEGRAM_PROXY" label={t("config.imProxy")} placeholder="http://127.0.0.1:7890" />
            <FieldBool k="TELEGRAM_REQUIRE_PAIRING" label={t("config.imPairing")} />
            <FieldText k="TELEGRAM_PAIRING_CODE" label={t("config.imPairingCode")} placeholder={t("config.imPairingCodeHint")} />
            <FieldText k="TELEGRAM_WEBHOOK_URL" label="Webhook URL" placeholder="https://..." />
          </>
        ),
      },
      {
        title: t("config.imFeishu"),
        appType: t("config.imTypeCustomApp"),
        logo: <LogoFeishu size={22} />,
        enabledKey: "FEISHU_ENABLED",
        docUrl: "https://open.feishu.cn/",
        needPublicIp: false,
        body: (
          <>
            <FieldText k="FEISHU_APP_ID" label="App ID" />
            <FieldText k="FEISHU_APP_SECRET" label="App Secret" type="password" />
          </>
        ),
      },
      {
        title: t("config.imWework"),
        appType: t("config.imTypeSmartBot"),
        logo: <LogoWework size={22} />,
        enabledKey: "WEWORK_ENABLED",
        docUrl: "https://work.weixin.qq.com/",
        needPublicIp: true,
        body: (
          <>
            <FieldText k="WEWORK_CORP_ID" label="Corp ID" help={t("config.imWeworkCorpIdHelp")} />
            <FieldText k="WEWORK_TOKEN" label="Callback Token" help={t("config.imWeworkTokenHelp")} />
            <FieldText k="WEWORK_ENCODING_AES_KEY" label="EncodingAESKey" type="password" help={t("config.imWeworkAesKeyHelp")} />
            <FieldText k="WEWORK_CALLBACK_PORT" label={t("config.imCallbackPort")} placeholder="9880" />
            <div className="fieldHint" style={{ fontSize: 12, color: "var(--text3)", margin: "4px 0 0 0", lineHeight: 1.6 }}>
              💡 {t("config.imWeworkCallbackUrlHint")}<code style={{ background: "var(--bg2)", padding: "1px 5px", borderRadius: 4, fontSize: 11 }}>http://your-domain:9880/callback</code>
            </div>
          </>
        ),
      },
      {
        title: t("config.imDingtalk"),
        appType: t("config.imTypeInternalApp"),
        logo: <LogoDingtalk size={22} />,
        enabledKey: "DINGTALK_ENABLED",
        docUrl: "https://open.dingtalk.com/",
        needPublicIp: false,
        body: (
          <>
            <FieldText k="DINGTALK_CLIENT_ID" label="Client ID" />
            <FieldText k="DINGTALK_CLIENT_SECRET" label="Client Secret" type="password" />
          </>
        ),
      },
      {
        title: "QQ 机器人",
        appType: `${t("config.imTypeQQBot")} (${(envDraft["QQBOT_MODE"] || "websocket") === "webhook" ? "Webhook" : "WebSocket"})`,
        logo: <LogoQQ size={22} />,
        enabledKey: "QQBOT_ENABLED",
        docUrl: "https://bot.q.qq.com/wiki/develop/api-v2/",
        needPublicIp: false,
        body: (
          <>
            <FieldText k="QQBOT_APP_ID" label="AppID" placeholder="q.qq.com 开发设置" />
            <FieldText k="QQBOT_APP_SECRET" label="AppSecret" type="password" placeholder="q.qq.com 开发设置" />
            <FieldBool k="QQBOT_SANDBOX" label={t("config.imQQBotSandbox")} />
            <div style={{ marginTop: 8 }}>
              <div className="label">{t("config.imQQBotMode")}</div>
              <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                {["websocket", "webhook"].map((m) => (
                  <button key={m} className={(envDraft["QQBOT_MODE"] || "websocket") === m ? "capChipActive" : "capChip"}
                    onClick={() => setEnvDraft((d) => ({ ...d, QQBOT_MODE: m }))}>{m === "websocket" ? "WebSocket" : "Webhook"}</button>
                ))}
              </div>
              <div style={{ fontSize: 11, color: "#888", marginTop: 4 }}>
                {(envDraft["QQBOT_MODE"] || "websocket") === "websocket"
                  ? t("config.imQQBotModeWsHint")
                  : t("config.imQQBotModeWhHint")}
              </div>
            </div>
            {(envDraft["QQBOT_MODE"] === "webhook") && (
              <>
                <FieldText k="QQBOT_WEBHOOK_PORT" label={t("config.imQQBotWebhookPort")} placeholder="9890" />
                <FieldText k="QQBOT_WEBHOOK_PATH" label={t("config.imQQBotWebhookPath")} placeholder="/qqbot/callback" />
              </>
            )}
          </>
        ),
      },
      {
        title: "OneBot",
        appType: t("config.imTypeOneBot"),
        logo: <LogoQQ size={22} />,
        enabledKey: "ONEBOT_ENABLED",
        docUrl: "https://github.com/botuniverse/onebot-11",
        needPublicIp: false,
        body: (
          <>
            <FieldText k="ONEBOT_WS_URL" label="WebSocket URL" placeholder="ws://127.0.0.1:8080" />
            <FieldText k="ONEBOT_ACCESS_TOKEN" label="Access Token" type="password" placeholder={t("config.imOneBotTokenHint")} />
          </>
        ),
      },
    ];

    return (
      <>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div className="cardTitle">{t("config.imTitle")}</div>
            <button className="btnSmall" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}
              onClick={() => { navigator.clipboard.writeText("https://github.com/anthropic-lab/openakita/blob/main/docs/im-channels.md"); setNotice(t("config.imGuideDocCopied")); }}
              title={t("config.imGuideDoc")}
            ><IconBook size={13} />{t("config.imGuideDoc")}</button>
          </div>
          <div className="cardHint">{t("config.imHint")}</div>
          <div className="divider" />

          {channels.map((c) => {
            const enabled = envGet(envDraft, c.enabledKey, "false").toLowerCase() === "true";
            return (
              <div key={c.enabledKey} className="card" style={{ marginTop: 10 }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                  <div className="row" style={{ alignItems: "center", gap: 10 }}>
                    {c.logo}
                    <span className="label" style={{ marginBottom: 0 }}>{c.title}</span>
                    <span className="pill" style={{ fontSize: 10, padding: "1px 6px", background: "#f1f5f9", color: "#475569" }}>{c.appType}</span>
                    {c.needPublicIp && <span className="pill" style={{ fontSize: 10, padding: "1px 6px", background: "#fef3c7", color: "#92400e" }}>{t("config.imNeedPublicIp")}</span>}
                  </div>
                  <label className="pill" style={{ cursor: "pointer", userSelect: "none" }}>
                    <input style={{ width: 16, height: 16 }} type="checkbox" checked={enabled}
                      onChange={(e) => setEnvDraft((m) => envSet(m, c.enabledKey, String(e.target.checked)))} />
                    {t("config.enable")}
                  </label>
                </div>
                <div className="row" style={{ alignItems: "center", gap: 6, marginTop: 4 }}>
                  <button className="btnSmall"
                    style={{ fontSize: 11, padding: "2px 8px", display: "inline-flex", alignItems: "center", gap: 3 }}
                    title={c.docUrl}
                    onClick={() => { navigator.clipboard.writeText(c.docUrl); setNotice(t("config.imDocCopied")); }}
                  ><IconClipboard size={12} />{t("config.imDoc")}</button>
                  <span className="help" style={{ fontSize: 11, userSelect: "all", opacity: 0.6 }}>{c.docUrl}</span>
                </div>
                {enabled && (
                  <>
                    <div className="divider" />
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>{c.body}</div>
                  </>
                )}
              </div>
            );
          })}

        </div>
      </>
    );
  }

  function renderTools() {
    const keysTools = [
      "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FORCE_IPV4",
      "TOOL_MAX_PARALLEL", "FORCE_TOOL_CALL_MAX_RETRIES", "ALLOW_PARALLEL_TOOLS_WITH_INTERRUPT_CHECKS",
      "MCP_ENABLED", "MCP_TIMEOUT", "MCP_BROWSER_ENABLED",
      "MCP_MYSQL_ENABLED", "MCP_MYSQL_HOST", "MCP_MYSQL_USER", "MCP_MYSQL_PASSWORD", "MCP_MYSQL_DATABASE",
      "MCP_POSTGRES_ENABLED", "MCP_POSTGRES_URL",
      "DESKTOP_ENABLED", "DESKTOP_DEFAULT_MONITOR", "DESKTOP_COMPRESSION_QUALITY",
      "DESKTOP_MAX_WIDTH", "DESKTOP_MAX_HEIGHT", "DESKTOP_CACHE_TTL",
      "DESKTOP_UIA_TIMEOUT", "DESKTOP_UIA_RETRY_INTERVAL", "DESKTOP_UIA_MAX_RETRIES",
      "DESKTOP_VISION_ENABLED", "DESKTOP_VISION_MODEL", "DESKTOP_VISION_FALLBACK_MODEL",
      "DESKTOP_VISION_OCR_MODEL", "DESKTOP_VISION_MAX_RETRIES", "DESKTOP_VISION_TIMEOUT",
      "DESKTOP_CLICK_DELAY", "DESKTOP_TYPE_INTERVAL", "DESKTOP_MOVE_DURATION",
      "DESKTOP_FAILSAFE", "DESKTOP_PAUSE", "DESKTOP_LOG_ACTIONS", "DESKTOP_LOG_SCREENSHOTS", "DESKTOP_LOG_DIR",
      "WHISPER_MODEL", "WHISPER_LANGUAGE", "GITHUB_TOKEN",
    ];

    const list = skillsDetail || [];
    const systemSkills = list.filter((s) => !!s.system);
    const externalSkills = list.filter((s) => !s.system);

    return (
      <>
        <div className="card">
          <div className="cardTitle">{t("config.toolsTitle")}</div>
          <div className="cardHint">{t("config.toolsHint")}</div>
          <div className="divider" />

          {/* ── MCP (open by default, browser enabled) ── */}
          <details className="configDetails" open>
            <summary>{t("config.toolsMCP")}</summary>
            <div className="configDetailsBody">
              <FieldBool k="MCP_ENABLED" label={t("config.toolsMCPEnable")} help={t("config.toolsMCPEnableHelp")} />
              <div className="grid3">
                <FieldBool k="MCP_BROWSER_ENABLED" label="Browser MCP" help={t("config.toolsMCPBrowserHelp")} />
                <FieldText k="MCP_TIMEOUT" label="Timeout (s)" placeholder="60" />
              </div>
              <div className="divider" />
              <FieldBool k="MCP_MYSQL_ENABLED" label="MySQL" />
              <div className="grid2">
                <FieldText k="MCP_MYSQL_HOST" label="Host" placeholder="localhost" />
                <FieldText k="MCP_MYSQL_USER" label="User" placeholder="root" />
                <FieldText k="MCP_MYSQL_PASSWORD" label="Password" type="password" />
                <FieldText k="MCP_MYSQL_DATABASE" label="Database" placeholder="mydb" />
              </div>
              <div className="divider" />
              <FieldBool k="MCP_POSTGRES_ENABLED" label="PostgreSQL" />
              <FieldText k="MCP_POSTGRES_URL" label="URL" placeholder="postgresql://user:pass@localhost/db" />
            </div>
          </details>

          {/* ── Desktop Automation (open by default, enabled) ── */}
          <details className="configDetails" open>
            <summary>{t("config.toolsDesktop")}</summary>
            <div className="configDetailsBody">
              <FieldBool k="DESKTOP_ENABLED" label={t("config.toolsDesktopEnable")} help={t("config.toolsDesktopHelp")} />
              <div className="grid3">
                <FieldText k="DESKTOP_DEFAULT_MONITOR" label={t("config.toolsMonitor")} placeholder="0" />
                <FieldText k="DESKTOP_MAX_WIDTH" label={t("config.toolsMaxW")} placeholder="1920" />
                <FieldText k="DESKTOP_MAX_HEIGHT" label={t("config.toolsMaxH")} placeholder="1080" />
              </div>
              <details className="configDetails">
                <summary>{t("config.toolsDesktopAdvanced")}</summary>
                <div className="configDetailsBody">
                  <div className="grid3">
                    <FieldText k="DESKTOP_COMPRESSION_QUALITY" label={t("config.toolsCompression")} placeholder="85" />
                    <FieldText k="DESKTOP_CACHE_TTL" label="Cache TTL" placeholder="1.0" />
                    <FieldBool k="DESKTOP_FAILSAFE" label="Failsafe" />
                  </div>
                  <FieldBool k="DESKTOP_VISION_ENABLED" label={t("config.toolsVision")} help={t("config.toolsVisionHelp")} />
                  <div className="grid2">
                    <FieldText k="DESKTOP_VISION_MODEL" label={t("config.toolsVisionModel")} placeholder="qwen3-vl-plus" />
                    <FieldText k="DESKTOP_VISION_OCR_MODEL" label="OCR" placeholder="qwen-vl-ocr" />
                  </div>
                  <div className="grid3">
                    <FieldText k="DESKTOP_CLICK_DELAY" label="Click Delay" placeholder="0.1" />
                    <FieldText k="DESKTOP_TYPE_INTERVAL" label="Type Interval" placeholder="0.03" />
                    <FieldText k="DESKTOP_MOVE_DURATION" label="Move Duration" placeholder="0.15" />
                  </div>
                </div>
              </details>
            </div>
          </details>

          {/* ── Model Downloads & Voice Recognition (prominent, open by default) ── */}
          <details className="configDetails" open>
            <summary>{t("config.toolsDownloadVoice")}</summary>
            <div className="configDetailsBody">
              <div className="grid2">
                <FieldSelect k="MODEL_DOWNLOAD_SOURCE" label={t("config.agentDownloadSource")} options={[
                  { value: "auto", label: "Auto (自动选择最快源)" },
                  { value: "hf-mirror", label: "hf-mirror (国内镜像 🇨🇳)" },
                  { value: "modelscope", label: "ModelScope (魔搭社区 🇨🇳)" },
                  { value: "huggingface", label: "HuggingFace (官方)" },
                ]} />
                <FieldSelect k="WHISPER_LANGUAGE" label={t("config.toolsWhisperLang")} options={[
                  { value: "zh", label: "中文 (zh)" },
                  { value: "en", label: "English (en, .en model)" },
                  { value: "auto", label: "Auto (自动检测)" },
                ]} />
              </div>
              <div className="grid2">
                <FieldCombo k="WHISPER_MODEL" label={t("config.toolsWhisperModel")} help={t("config.toolsWhisperHelp")} options={[
                  { value: "tiny", label: "tiny (~39MB)" },
                  { value: "base", label: "base (~74MB)" },
                  { value: "small", label: "small (~244MB)" },
                  { value: "medium", label: "medium (~769MB)" },
                  { value: "large", label: "large (~1.5GB)" },
                ]} placeholder="base" />
                <FieldText k="GITHUB_TOKEN" label="GitHub Token" placeholder="" type="password" help={t("config.toolsGithubHelp")} />
              </div>
            </div>
          </details>

          {/* ── Network & Proxy ── */}
          <details className="configDetails">
            <summary>{t("config.toolsNetwork")}</summary>
            <div className="configDetailsBody">
              <div className="grid3">
                <FieldText k="HTTP_PROXY" label="HTTP_PROXY" placeholder="http://127.0.0.1:7890" />
                <FieldText k="HTTPS_PROXY" label="HTTPS_PROXY" placeholder="http://127.0.0.1:7890" />
                <FieldText k="ALL_PROXY" label="ALL_PROXY" placeholder="socks5://..." />
              </div>
              <div className="grid2">
                <FieldBool k="FORCE_IPV4" label={t("config.toolsForceIPv4")} help={t("config.toolsForceIPv4Help")} />
                <FieldText k="TOOL_MAX_PARALLEL" label={t("config.toolsParallel")} placeholder="1" help={t("config.toolsParallelHelp")} />
              </div>
            </div>
          </details>

          {/* ── Other ── */}
          <details className="configDetails">
            <summary>{t("config.toolsOther")}</summary>
            <div className="configDetailsBody">
              <div className="grid2">
                <FieldText k="FORCE_TOOL_CALL_MAX_RETRIES" label={t("config.toolsForceRetry")} placeholder="1" />
              </div>
            </div>
          </details>

          <div className="divider" />

          {/* ── Skills (collapsed, at bottom) ── */}
          <details className="configDetails">
            <summary>{t("config.toolsSkills")} {skillsDetail ? `(${systemSkills.length + externalSkills.length})` : ""}</summary>
            <div className="configDetailsBody">
              <div className="row" style={{ justifyContent: "flex-end", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                <button className="btnSmall" onClick={() => {
                  if (!skillsDetail) return; setSkillsTouched(true);
                  const m: Record<string, boolean> = {};
                  for (const s of skillsDetail) m[s.name] = true;
                  setSkillsSelection(m);
                }} disabled={!skillsDetail || !!busy}>{t("config.toolsEnableAll")}</button>
                <button className="btnSmall" onClick={() => {
                  if (!skillsDetail) return; setSkillsTouched(true);
                  const m: Record<string, boolean> = {};
                  for (const s of skillsDetail) m[s.name] = !!s.system;
                  setSkillsSelection(m);
                }} disabled={!skillsDetail || !!busy}>{t("config.toolsSystemOnly")}</button>
                <button className="btnSmall" onClick={doRefreshSkills} disabled={!currentWorkspaceId || !!busy}>{t("config.toolsRefresh")}</button>
                <button className="btnSmall btnSmallPrimary" onClick={doSaveSkillsSelection}
                  disabled={!currentWorkspaceId || !skillsDetail || !!busy}>{t("config.toolsSaveSkills")}</button>
              </div>

              {!skillsDetail ? (
                <div className="cardHint">{t("config.toolsNoSkills")}</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {systemSkills.length > 0 && (
                    <div className="help">{t("config.toolsSystemLabel", { count: systemSkills.length })}</div>
                  )}
                  {systemSkills.map((s) => (
                    <div key={s.name} className="row" style={{ justifyContent: "space-between", alignItems: "center", padding: "2px 0" }}>
                      <div style={{ minWidth: 0 }}><b>{s.name}</b> <span className="pill" style={{ fontSize: 11 }}>{t("skills.system")}</span>
                        <span className="help" style={{ marginLeft: 8 }}>{s.description}</span></div>
                    </div>
                  ))}
                  {externalSkills.length > 0 && (
                    <>
                      <div className="divider" />
                      <div className="help">{t("config.toolsExternalLabel", { count: externalSkills.length })}</div>
                    </>
                  )}
                  {externalSkills.map((s) => {
                    const on = !!skillsSelection[s.name];
                    return (
                      <div key={s.name} className="row" style={{ justifyContent: "space-between", alignItems: "center", padding: "2px 0" }}>
                        <div style={{ flex: 1, minWidth: 0 }}><b>{s.name}</b>
                          <span className="help" style={{ marginLeft: 8 }}>{s.description}</span></div>
                        <label className="pill" style={{ cursor: "pointer", userSelect: "none", flexShrink: 0 }}>
                          <input style={{ width: 14, height: 14 }} type="checkbox" checked={on}
                            onChange={(e) => { setSkillsTouched(true); setSkillsSelection((m) => ({ ...m, [s.name]: e.target.checked })); }} />
                          {t("config.enable")}
                        </label>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </details>

        </div>

        {/* ── CLI 命令行工具管理 ── */}
        <div className="card" style={{ marginTop: 16 }}>
          <div className="cardTitle">CLI 命令行工具</div>
          <div className="cardHint">管理终端命令注册，注册后可在 CMD / PowerShell / 终端中直接使用 oa 或 openakita 命令。</div>
          <div className="divider" />
          <CliManager />
        </div>
      </>
    );
  }

  // ── CLI 命令行工具管理组件 ──
  function CliManager() {
    const [cliStatus, setCliStatus] = useState<{
      registeredCommands: string[];
      inPath: boolean;
      binDir: string;
    } | null>(null);
    const [cliLoading, setCliLoading] = useState(false);
    const [cliMsg, setCliMsg] = useState("");
    const [cliRegOpenakita, setCliRegOpenakita] = useState(true);
    const [cliRegOa, setCliRegOa] = useState(true);
    const [cliRegPath, setCliRegPath] = useState(true);

    useEffect(() => {
      loadCliStatus();
    }, []);

    async function loadCliStatus() {
      try {
        const status = await invoke<{ registeredCommands: string[]; inPath: boolean; binDir: string }>("get_cli_status");
        setCliStatus(status);
        setCliRegOpenakita(status.registeredCommands.includes("openakita"));
        setCliRegOa(status.registeredCommands.includes("oa"));
        setCliRegPath(status.inPath);
      } catch (e) {
        setCliMsg(`查询 CLI 状态失败: ${String(e)}`);
      }
    }

    async function doRegister() {
      const cmds: string[] = [];
      if (cliRegOpenakita) cmds.push("openakita");
      if (cliRegOa) cmds.push("oa");
      if (cmds.length === 0) {
        setCliMsg("请至少选择一个命令名称");
        return;
      }
      setCliLoading(true);
      setCliMsg("");
      try {
        const result = await invoke<string>("register_cli", { commands: cmds, addToPath: cliRegPath });
        setCliMsg(`✓ ${result}`);
        await loadCliStatus();
      } catch (e) {
        setCliMsg(`✗ 注册失败: ${String(e)}`);
      } finally {
        setCliLoading(false);
      }
    }

    async function doUnregister() {
      setCliLoading(true);
      setCliMsg("");
      try {
        const result = await invoke<string>("unregister_cli");
        setCliMsg(`✓ ${result}`);
        await loadCliStatus();
      } catch (e) {
        setCliMsg(`✗ 注销失败: ${String(e)}`);
      } finally {
        setCliLoading(false);
      }
    }

    const hasRegistered = cliStatus && cliStatus.registeredCommands.length > 0;

    return (
      <div style={{ padding: "0 0 8px" }}>
        {cliStatus && hasRegistered && (
          <div style={{ background: "rgba(34,197,94,0.08)", borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>已注册命令</div>
            <div style={{ fontSize: 13 }}>
              {cliStatus.registeredCommands.map(cmd => (
                <code key={cmd} style={{ marginRight: 8, padding: "2px 6px", background: "rgba(0,0,0,0.1)", borderRadius: 4 }}>{cmd}</code>
              ))}
              {cliStatus.inPath ? (
                <span style={{ color: "#22c55e", fontSize: 12 }}> (已在 PATH 中)</span>
              ) : (
                <span style={{ color: "#f59e0b", fontSize: 12 }}> (未在 PATH 中)</span>
              )}
            </div>
            <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>目录: {cliStatus.binDir}</div>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
            <input type="checkbox" checked={cliRegOpenakita} onChange={() => setCliRegOpenakita(!cliRegOpenakita)} />
            <span><strong>openakita</strong> — 完整命令</span>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
            <input type="checkbox" checked={cliRegOa} onChange={() => setCliRegOa(!cliRegOa)} />
            <span><strong>oa</strong> — 简短别名</span>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
            <input type="checkbox" checked={cliRegPath} onChange={() => setCliRegPath(!cliRegPath)} />
            <span>添加到系统 PATH</span>
          </label>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button className="btnPrimary" onClick={doRegister} disabled={cliLoading} style={{ fontSize: 13 }}>
            {cliLoading ? "处理中..." : hasRegistered ? "更新注册" : "注册"}
          </button>
          {hasRegistered && (
            <button onClick={doUnregister} disabled={cliLoading} style={{ fontSize: 13 }}>
              注销全部
            </button>
          )}
        </div>

        {cliMsg && (
          <div style={{
            marginTop: 8, padding: "6px 10px", borderRadius: 6, fontSize: 12,
            background: cliMsg.startsWith("✓") ? "rgba(34,197,94,0.1)" : cliMsg.startsWith("✗") ? "rgba(239,68,68,0.1)" : "rgba(245,158,11,0.1)",
            color: cliMsg.startsWith("✓") ? "#22c55e" : cliMsg.startsWith("✗") ? "#ef4444" : "#f59e0b",
          }}>
            {cliMsg}
          </div>
        )}
      </div>
    );
  }

  function renderAgentSystem() {
    const keysAgent = [
      "AGENT_NAME", "MAX_ITERATIONS", "AUTO_CONFIRM",
      "THINKING_MODE",
      "PROGRESS_TIMEOUT_SECONDS", "HARD_TIMEOUT_SECONDS",
      "DATABASE_PATH", "LOG_LEVEL", "LOG_DIR", "LOG_FILE_PREFIX",
      "LOG_MAX_SIZE_MB", "LOG_BACKUP_COUNT", "LOG_RETENTION_DAYS",
      "LOG_FORMAT", "LOG_TO_CONSOLE", "LOG_TO_FILE",
      "EMBEDDING_MODEL", "EMBEDDING_DEVICE", "MODEL_DOWNLOAD_SOURCE",
      "MEMORY_HISTORY_DAYS", "MEMORY_MAX_HISTORY_FILES", "MEMORY_MAX_HISTORY_SIZE_MB",
      "PERSONA_NAME",
      "PROACTIVE_ENABLED", "PROACTIVE_MAX_DAILY_MESSAGES", "PROACTIVE_MIN_INTERVAL_MINUTES",
      "PROACTIVE_QUIET_HOURS_START", "PROACTIVE_QUIET_HOURS_END", "PROACTIVE_IDLE_THRESHOLD_HOURS",
      "STICKER_ENABLED", "STICKER_DATA_DIR",
      "SCHEDULER_ENABLED", "SCHEDULER_TIMEZONE", "SCHEDULER_MAX_CONCURRENT", "SCHEDULER_TASK_TIMEOUT",
      "SESSION_TIMEOUT_MINUTES", "SESSION_MAX_HISTORY", "SESSION_STORAGE_PATH",
      "ORCHESTRATION_ENABLED", "ORCHESTRATION_MODE", "ORCHESTRATION_BUS_ADDRESS",
      "ORCHESTRATION_PUB_ADDRESS", "ORCHESTRATION_MIN_WORKERS", "ORCHESTRATION_MAX_WORKERS",
      "ORCHESTRATION_HEARTBEAT_INTERVAL", "ORCHESTRATION_HEALTH_CHECK_INTERVAL",
    ];

    const personas = [
      { id: "default", zh: "\u9ed8\u8ba4\u52a9\u624b", en: "Default", desc: "config.agentPersonaDefault" },
      { id: "business", zh: "\u5546\u52a1\u987e\u95ee", en: "Business", desc: "config.agentPersonaBusiness" },
      { id: "tech_expert", zh: "\u6280\u672f\u4e13\u5bb6", en: "Tech Expert", desc: "config.agentPersonaTech" },
      { id: "butler", zh: "\u79c1\u4eba\u7ba1\u5bb6", en: "Butler", desc: "config.agentPersonaButler" },
      { id: "girlfriend", zh: "\u865a\u62df\u5973\u53cb", en: "Girlfriend", desc: "config.agentPersonaGirlfriend" },
      { id: "boyfriend", zh: "\u865a\u62df\u7537\u53cb", en: "Boyfriend", desc: "config.agentPersonaBoyfriend" },
      { id: "family", zh: "\u5bb6\u4eba", en: "Family", desc: "config.agentPersonaFamily" },
      { id: "jarvis", zh: "\u8d3e\u7ef4\u65af", en: "Jarvis", desc: "config.agentPersonaJarvis" },
    ];
    const curPersona = envGet(envDraft, "PERSONA_NAME", "default");

    return (
      <>
        <div className="card">
          <div className="cardTitle">{t("config.agentTitle")}</div>
          <div className="cardHint">{t("config.agentHint")}</div>
          <div className="divider" />

          {/* ── Persona Selection ── */}
          <div style={{ marginBottom: 12 }}>
            <div className="label">{t("config.agentPersona")}</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
              {personas.map((p) => (
                <button key={p.id}
                  className={curPersona === p.id ? "capChipActive" : "capChip"}
                  onClick={() => setEnvDraft((m) => envSet(m, "PERSONA_NAME", p.id))}>
                  {t(p.desc)}
                </button>
              ))}
            </div>
            {curPersona === "custom" || !personas.find((p) => p.id === curPersona) ? (
              <input style={{ marginTop: 8, maxWidth: 300 }} type="text" placeholder={t("config.agentCustomId")}
                value={envGet(envDraft, "PERSONA_CUSTOM_ID", "")}
                onChange={(e) => {
                  setEnvDraft((m) => envSet(m, "PERSONA_CUSTOM_ID", e.target.value));
                  setEnvDraft((m) => envSet(m, "PERSONA_NAME", e.target.value || "custom"));
                }} />
            ) : null}
          </div>

          {/* ── Core Parameters ── */}
          <div className="label">{t("config.agentCore")}</div>
          <div className="grid3" style={{ marginTop: 4 }}>
            <FieldText k="AGENT_NAME" label={t("config.agentName")} placeholder="OpenAkita" />
            <FieldText k="MAX_ITERATIONS" label={t("config.agentMaxIter")} placeholder="300" help={t("config.agentMaxIterHelp")} />
            <FieldSelect k="THINKING_MODE" label={t("config.agentThinking")} options={[
              { value: "auto", label: "auto (自动判断)" },
              { value: "always", label: "always (始终思考)" },
              { value: "never", label: "never (从不思考)" },
            ]} />
          </div>
          <div style={{ marginTop: 8 }}>
            <FieldBool k="AUTO_CONFIRM" label={t("config.agentAutoConfirm")} help={t("config.agentAutoConfirmHelp")} />
          </div>

          <div className="divider" />

          {/* ── Living Presence ── */}
          <div className="label">{t("config.agentProactive")}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
            <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
              <FieldBool k="PROACTIVE_ENABLED" label={t("config.agentProactiveEnable")} help={t("config.agentProactiveEnableHelp")} />
              <FieldBool k="STICKER_ENABLED" label={t("config.agentSticker")} help={t("config.agentStickerHelp")} />
            </div>
            <div className="grid3">
              <FieldText k="PROACTIVE_MAX_DAILY_MESSAGES" label={t("config.agentMaxDaily")} placeholder="3" help={t("config.agentMaxDailyHelp")} />
              <FieldText k="PROACTIVE_QUIET_HOURS_START" label={t("config.agentQuietStart")} placeholder="23" help={t("config.agentQuietStartHelp")} />
              <FieldText k="PROACTIVE_QUIET_HOURS_END" label={t("config.agentQuietEnd")} placeholder="7" />
            </div>
          </div>

          <div className="divider" />

          {/* ── Scheduler ── */}
          <div className="label">{t("config.agentScheduler")}</div>
          <div className="grid3" style={{ marginTop: 4 }}>
            <FieldBool k="SCHEDULER_ENABLED" label={t("config.agentSchedulerEnable")} help={t("config.agentSchedulerEnableHelp")} defaultValue={true} />
            <FieldText k="SCHEDULER_TIMEZONE" label={t("config.agentTimezone")} placeholder="Asia/Shanghai" />
            <FieldText k="SCHEDULER_MAX_CONCURRENT" label={t("config.agentMaxConcurrent")} placeholder="5" help={t("config.agentMaxConcurrentHelp")} />
          </div>

          <div className="divider" />

          {/* ── Advanced (collapsed) ── */}
          <details className="configDetails">
            <summary>{t("config.agentAdvanced")}</summary>
            <div className="configDetailsBody">
              {/* Logging */}
              <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentLogSection")}</div>
              <div className="grid3">
                <FieldSelect k="LOG_LEVEL" label={t("config.agentLogLevel")} options={[
                  { value: "DEBUG", label: "DEBUG" },
                  { value: "INFO", label: "INFO" },
                  { value: "WARNING", label: "WARNING" },
                  { value: "ERROR", label: "ERROR" },
                ]} />
                <FieldText k="LOG_DIR" label={t("config.agentLogDir")} placeholder="logs" />
                <FieldText k="DATABASE_PATH" label={t("config.agentDbPath")} placeholder="data/agent.db" />
              </div>
              <div className="grid3">
                <FieldText k="LOG_MAX_SIZE_MB" label={t("config.agentLogMaxMB")} placeholder="10" />
                <FieldText k="LOG_BACKUP_COUNT" label={t("config.agentLogBackup")} placeholder="30" />
                <FieldText k="LOG_RETENTION_DAYS" label={t("config.agentLogRetention")} placeholder="30" />
              </div>
              <div className="grid2">
                <FieldBool k="LOG_TO_CONSOLE" label={t("config.agentLogConsole")} />
                <FieldBool k="LOG_TO_FILE" label={t("config.agentLogFile")} />
              </div>

              <div className="divider" />
              {/* Memory & Embedding */}
              <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentMemorySection")}</div>
              <div className="grid3">
                <FieldText k="EMBEDDING_MODEL" label={t("config.agentEmbedModel")} placeholder="shibing624/text2vec-base-chinese" />
                <FieldText k="EMBEDDING_DEVICE" label={t("config.agentEmbedDevice")} placeholder="cpu" />
                <FieldSelect k="MODEL_DOWNLOAD_SOURCE" label={t("config.agentDownloadSource")} options={[
                  { value: "auto", label: "Auto (自动选择)" },
                  { value: "hf-mirror", label: "hf-mirror (国内镜像)" },
                  { value: "modelscope", label: "ModelScope (魔搭)" },
                  { value: "huggingface", label: "HuggingFace (官方)" },
                ]} />
              </div>
              <div className="grid3">
                <FieldText k="MEMORY_HISTORY_DAYS" label={t("config.agentMemDays")} placeholder="30" />
                <FieldText k="MEMORY_MAX_HISTORY_FILES" label={t("config.agentMemFiles")} placeholder="1000" />
                <FieldText k="MEMORY_MAX_HISTORY_SIZE_MB" label={t("config.agentMemSize")} placeholder="500" />
              </div>

              <div className="divider" />
              {/* Session */}
              <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentSessionSection")}</div>
              <div className="grid3">
                <FieldText k="SESSION_TIMEOUT_MINUTES" label={t("config.agentSessionTimeout")} placeholder="30" />
                <FieldText k="SESSION_MAX_HISTORY" label={t("config.agentSessionMax")} placeholder="50" />
                <FieldText k="SESSION_STORAGE_PATH" label={t("config.agentSessionPath")} placeholder="data/sessions" />
              </div>

              <div className="divider" />
              {/* Proactive advanced */}
              <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentProactiveAdv")}</div>
              <div className="grid2">
                <FieldText k="PROACTIVE_MIN_INTERVAL_MINUTES" label={t("config.agentMinInterval")} placeholder="120" />
                <FieldText k="PROACTIVE_IDLE_THRESHOLD_HOURS" label={t("config.agentIdleThreshold")} placeholder="24" />
                <FieldText k="STICKER_DATA_DIR" label={t("config.agentStickerDir")} placeholder="data/sticker" />
              </div>

              <div className="divider" />
              {/* Orchestration */}
              <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentOrchSection")}</div>
              <FieldBool k="ORCHESTRATION_ENABLED" label={t("config.agentOrchEnable")} />
              <div className="grid2">
                <FieldText k="ORCHESTRATION_MODE" label={t("config.agentOrchMode")} placeholder="single" />
                <FieldText k="ORCHESTRATION_BUS_ADDRESS" label={t("config.agentOrchBus")} placeholder="tcp://127.0.0.1:5555" />
                <FieldText k="ORCHESTRATION_MIN_WORKERS" label={t("config.agentOrchMinW")} placeholder="1" />
                <FieldText k="ORCHESTRATION_MAX_WORKERS" label={t("config.agentOrchMaxW")} placeholder="4" />
              </div>
            </div>
          </details>

        </div>
      </>
    );
  }

  function renderIntegrations() {
    const keysCore = [
      // network/proxy
      "HTTP_PROXY",
      "HTTPS_PROXY",
      "ALL_PROXY",
      "FORCE_IPV4",
      // agent (基础)
      "AGENT_NAME",
      "MAX_ITERATIONS",
      "AUTO_CONFIRM",
      "THINKING_MODE",
      "TOOL_MAX_PARALLEL",
      "FORCE_TOOL_CALL_MAX_RETRIES",
      "ALLOW_PARALLEL_TOOLS_WITH_INTERRUPT_CHECKS",
      // timeouts
      "PROGRESS_TIMEOUT_SECONDS",
      "HARD_TIMEOUT_SECONDS",
      // logging/db
      "DATABASE_PATH",
      "LOG_LEVEL",
      "LOG_DIR",
      "LOG_FILE_PREFIX",
      "LOG_MAX_SIZE_MB",
      "LOG_BACKUP_COUNT",
      "LOG_RETENTION_DAYS",
      "LOG_FORMAT",
      "LOG_TO_CONSOLE",
      "LOG_TO_FILE",
      // github/whisper
      "GITHUB_TOKEN",
      "WHISPER_MODEL",
      "WHISPER_LANGUAGE",
      // memory / embedding
      "EMBEDDING_MODEL",
      "EMBEDDING_DEVICE",
      "MODEL_DOWNLOAD_SOURCE",
      "MEMORY_HISTORY_DAYS",
      "MEMORY_MAX_HISTORY_FILES",
      "MEMORY_MAX_HISTORY_SIZE_MB",
      // persona
      "PERSONA_NAME",
      // proactive (living presence)
      "PROACTIVE_ENABLED",
      "PROACTIVE_MAX_DAILY_MESSAGES",
      "PROACTIVE_MIN_INTERVAL_MINUTES",
      "PROACTIVE_QUIET_HOURS_START",
      "PROACTIVE_QUIET_HOURS_END",
      "PROACTIVE_IDLE_THRESHOLD_HOURS",
      // sticker
      "STICKER_ENABLED",
      "STICKER_DATA_DIR",
      // scheduler
      "SCHEDULER_ENABLED",
      "SCHEDULER_TIMEZONE",
      "SCHEDULER_MAX_CONCURRENT",
      "SCHEDULER_TASK_TIMEOUT",
      // session
      "SESSION_TIMEOUT_MINUTES",
      "SESSION_MAX_HISTORY",
      "SESSION_STORAGE_PATH",
      // orchestration
      "ORCHESTRATION_ENABLED",
      "ORCHESTRATION_MODE",
      "ORCHESTRATION_BUS_ADDRESS",
      "ORCHESTRATION_PUB_ADDRESS",
      "ORCHESTRATION_MIN_WORKERS",
      "ORCHESTRATION_MAX_WORKERS",
      "ORCHESTRATION_HEARTBEAT_INTERVAL",
      "ORCHESTRATION_HEALTH_CHECK_INTERVAL",
      // IM
      "TELEGRAM_ENABLED",
      "TELEGRAM_BOT_TOKEN",
      "TELEGRAM_PROXY",
      "TELEGRAM_REQUIRE_PAIRING",
      "TELEGRAM_PAIRING_CODE",
      "TELEGRAM_WEBHOOK_URL",
      "FEISHU_ENABLED",
      "FEISHU_APP_ID",
      "FEISHU_APP_SECRET",
      "WEWORK_ENABLED",
      "WEWORK_CORP_ID",
      "WEWORK_TOKEN",
      "WEWORK_ENCODING_AES_KEY",
      "WEWORK_CALLBACK_PORT",
      "DINGTALK_ENABLED",
      "DINGTALK_CLIENT_ID",
      "DINGTALK_CLIENT_SECRET",
      "ONEBOT_ENABLED",
      "ONEBOT_WS_URL",
      "ONEBOT_ACCESS_TOKEN",
      "QQBOT_ENABLED",
      "QQBOT_APP_ID",
      "QQBOT_APP_SECRET",
      "QQBOT_SANDBOX",
      "QQBOT_MODE",
      "QQBOT_WEBHOOK_PORT",
      "QQBOT_WEBHOOK_PATH",
      // MCP (docs/mcp-integration.md)
      "MCP_ENABLED",
      "MCP_TIMEOUT",
      "MCP_BROWSER_ENABLED",
      "MCP_MYSQL_ENABLED",
      "MCP_MYSQL_HOST",
      "MCP_MYSQL_USER",
      "MCP_MYSQL_PASSWORD",
      "MCP_MYSQL_DATABASE",
      "MCP_POSTGRES_ENABLED",
      "MCP_POSTGRES_URL",
      // Desktop automation
      "DESKTOP_ENABLED",
      "DESKTOP_DEFAULT_MONITOR",
      "DESKTOP_COMPRESSION_QUALITY",
      "DESKTOP_MAX_WIDTH",
      "DESKTOP_MAX_HEIGHT",
      "DESKTOP_CACHE_TTL",
      "DESKTOP_UIA_TIMEOUT",
      "DESKTOP_UIA_RETRY_INTERVAL",
      "DESKTOP_UIA_MAX_RETRIES",
      "DESKTOP_VISION_ENABLED",
      "DESKTOP_VISION_MODEL",
      "DESKTOP_VISION_FALLBACK_MODEL",
      "DESKTOP_VISION_OCR_MODEL",
      "DESKTOP_VISION_MAX_RETRIES",
      "DESKTOP_VISION_TIMEOUT",
      "DESKTOP_CLICK_DELAY",
      "DESKTOP_TYPE_INTERVAL",
      "DESKTOP_MOVE_DURATION",
      "DESKTOP_FAILSAFE",
      "DESKTOP_PAUSE",
      "DESKTOP_LOG_ACTIONS",
      "DESKTOP_LOG_SCREENSHOTS",
      "DESKTOP_LOG_DIR",
      // browser-use / openai compatibility (used by browser_mcp)
      "OPENAI_API_BASE",
      "OPENAI_BASE_URL",
      "OPENAI_API_KEY",
      "OPENAI_API_KEY_BASE64",
      "BROWSER_USE_API_KEY",
    ];

    return (
      <>
        <div className="card">
          <div className="cardTitle">工具与集成（全覆盖写入 .env）</div>
          <div className="cardHint">
            这一页会把项目里常用的开关与参数集中起来（参考 `examples/.env.example` + MCP 文档 + 桌面自动化配置）。
            <br />
            只会写入你实际填写/修改过的键；留空保存会从工作区 `.env` 删除该键（可选项不填就不会落盘）。
          </div>
          <div className="divider" />

          <div className="card" style={{ marginTop: 0 }}>
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              LLM（不在这里重复填）
            </div>
            <div className="cardHint">
              LLM 的 API Key / Base URL / 模型选择，统一在上一步“LLM 端点”里完成：端点会写入 `data/llm_endpoints.json`，并把对应 `api_key_env` 写入工作区 `.env`。
              <br />
              这里主要管理 IM / MCP / 桌面自动化 / Agent/调度 等“运行期开关与参数”。
            </div>
          </div>

          <div className="card" style={{ marginTop: 0 }}>
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              网络代理与并行
            </div>
            <div className="grid3">
              <FieldText k="HTTP_PROXY" label="HTTP_PROXY" placeholder="http://127.0.0.1:7890" />
              <FieldText k="HTTPS_PROXY" label="HTTPS_PROXY" placeholder="http://127.0.0.1:7890" />
              <FieldText k="ALL_PROXY" label="ALL_PROXY" placeholder="socks5://127.0.0.1:1080" />
            </div>
            <div className="grid3" style={{ marginTop: 10 }}>
              <FieldBool k="FORCE_IPV4" label="强制 IPv4" help="某些 VPN/IPv6 环境下有用" />
              <FieldText k="TOOL_MAX_PARALLEL" label="TOOL_MAX_PARALLEL" placeholder="1" help="单轮多工具并行数（默认 1=串行）" />
              <FieldText k="LOG_LEVEL" label="LOG_LEVEL" placeholder="INFO" help="DEBUG/INFO/WARNING/ERROR" />
            </div>
          </div>

          <div className="card">
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              IM 通道
            </div>
            <div className="cardHint">
              默认折叠显示。选择“启用”后展开填写信息（上下排列）。建议先把 LLM 端点配置好，再回来启用 IM。
            </div>
            <div className="divider" />

            {[
              {
                title: "Telegram",
                enabledKey: "TELEGRAM_ENABLED",
                apply: "https://t.me/BotFather",
                body: (
                  <>
                    <FieldText k="TELEGRAM_BOT_TOKEN" label="Bot Token" placeholder="从 BotFather 获取（仅会显示一次）" type="password" />
                    <FieldText k="TELEGRAM_PROXY" label="代理（可选）" placeholder="http://127.0.0.1:7890 / socks5://..." />
                  </>
                ),
              },
              {
                title: "飞书（需要 openakita[feishu]）",
                enabledKey: "FEISHU_ENABLED",
                apply: "https://open.feishu.cn/",
                body: (
                  <>
                    <FieldText k="FEISHU_APP_ID" label="App ID" placeholder="" />
                    <FieldText k="FEISHU_APP_SECRET" label="App Secret" placeholder="" type="password" />
                  </>
                ),
              },
              {
                title: "企业微信（需要 openakita[wework]）",
                enabledKey: "WEWORK_ENABLED",
                apply: "https://work.weixin.qq.com/",
                body: (
                  <>
                    <FieldText k="WEWORK_CORP_ID" label="Corp ID" />
                    <FieldText k="WEWORK_TOKEN" label="回调 Token" placeholder="在企业微信后台「接收消息」设置中获取" />
                    <FieldText k="WEWORK_ENCODING_AES_KEY" label="EncodingAESKey" placeholder="在企业微信后台「接收消息」设置中获取" type="password" />
                    <FieldText k="WEWORK_CALLBACK_PORT" label="回调端口" placeholder="9880" />
                    <div style={{ fontSize: 12, color: "#888", margin: "4px 0 0 0", lineHeight: 1.6 }}>
                      💡 企业微信后台「接收消息服务器配置」的 URL 请填：<code style={{ background: "#f5f5f5", padding: "1px 5px", borderRadius: 4, fontSize: 11 }}>http://your-domain:9880/callback</code>
                    </div>
                  </>
                ),
              },
              {
                title: "钉钉（需要 openakita[dingtalk]）",
                enabledKey: "DINGTALK_ENABLED",
                apply: "https://open.dingtalk.com/",
                body: (
                  <>
                    <FieldText k="DINGTALK_CLIENT_ID" label="Client ID" />
                    <FieldText k="DINGTALK_CLIENT_SECRET" label="Client Secret" type="password" />
                  </>
                ),
              },
              {
                title: "QQ 官方机器人（需要 openakita[qqbot]）",
                enabledKey: "QQBOT_ENABLED",
                apply: "https://bot.q.qq.com/wiki/develop/api-v2/",
                body: (
                  <>
                    <FieldText k="QQBOT_APP_ID" label="AppID" placeholder="q.qq.com 开发设置" />
                    <FieldText k="QQBOT_APP_SECRET" label="AppSecret" type="password" placeholder="q.qq.com 开发设置" />
                    <FieldBool k="QQBOT_SANDBOX" label={t("config.imQQBotSandbox")} />
                    <div style={{ marginTop: 8 }}>
                      <div className="label">{t("config.imQQBotMode")}</div>
                      <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                        {["websocket", "webhook"].map((m) => (
                          <button key={m} className={(envDraft["QQBOT_MODE"] || "websocket") === m ? "capChipActive" : "capChip"}
                            onClick={() => setEnvDraft((d) => ({ ...d, QQBOT_MODE: m }))}>{m === "websocket" ? "WebSocket" : "Webhook"}</button>
                        ))}
                      </div>
                      <div style={{ fontSize: 11, color: "#888", marginTop: 4 }}>
                        {(envDraft["QQBOT_MODE"] || "websocket") === "websocket"
                          ? t("config.imQQBotModeWsHint")
                          : t("config.imQQBotModeWhHint")}
                      </div>
                    </div>
                    {(envDraft["QQBOT_MODE"] === "webhook") && (
                      <>
                        <FieldText k="QQBOT_WEBHOOK_PORT" label={t("config.imQQBotWebhookPort")} placeholder="9890" />
                        <FieldText k="QQBOT_WEBHOOK_PATH" label={t("config.imQQBotWebhookPath")} placeholder="/qqbot/callback" />
                      </>
                    )}
                  </>
                ),
              },
              {
                title: "OneBot（需要 openakita[onebot] + NapCat/Lagrange）",
                enabledKey: "ONEBOT_ENABLED",
                apply: "https://github.com/botuniverse/onebot-11",
                body: (
                  <>
                    <FieldText k="ONEBOT_WS_URL" label="WebSocket URL" placeholder="ws://127.0.0.1:8080" />
                    <FieldText k="ONEBOT_ACCESS_TOKEN" label="Access Token" type="password" placeholder={t("config.imOneBotTokenHint")} />
                  </>
                ),
              },
            ].map((c) => {
              const enabled = envGet(envDraft, c.enabledKey, "false").toLowerCase() === "true";
              return (
                <div key={c.enabledKey} className="card" style={{ marginTop: 10 }}>
                  <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                    <div className="label" style={{ marginBottom: 0 }}>
                      {c.title}
                    </div>
                    <label className="pill" style={{ cursor: "pointer", userSelect: "none" }}>
                      <input
                        style={{ width: 16, height: 16 }}
                        type="checkbox"
                        checked={enabled}
                        onChange={(e) => setEnvDraft((m) => envSet(m, c.enabledKey, String(e.target.checked)))}
                      />
                      启用
                    </label>
                  </div>
                  <div className="help" style={{ marginTop: 8 }}>
                    申请/文档：<code style={{ userSelect: "all", fontSize: 12 }}>{c.apply}</code>
                  </div>
                  {enabled ? (
                    <>
                      <div className="divider" />
                      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>{c.body}</div>
                    </>
                  ) : (
                    <div className="cardHint" style={{ marginTop: 8 }}>
                      未启用：保持折叠。
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="card">
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              MCP / 桌面自动化 / 语音与 GitHub
            </div>
            <div className="grid2">
              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  MCP
                </div>
                <FieldBool k="MCP_ENABLED" label="启用 MCP" help="连接外部 MCP 服务/工具" />
                <div className="grid2" style={{ marginTop: 10 }}>
                  <FieldBool k="MCP_BROWSER_ENABLED" label="Browser MCP" help="Playwright 浏览器自动化" />
                  <FieldText k="MCP_TIMEOUT" label="MCP_TIMEOUT" placeholder="60" />
                </div>
                <div className="divider" />
                <FieldBool k="MCP_MYSQL_ENABLED" label="MySQL MCP" />
                <div className="grid2" style={{ marginTop: 10 }}>
                  <FieldText k="MCP_MYSQL_HOST" label="MCP_MYSQL_HOST" placeholder="localhost" />
                  <FieldText k="MCP_MYSQL_USER" label="MCP_MYSQL_USER" placeholder="root" />
                  <FieldText k="MCP_MYSQL_PASSWORD" label="MCP_MYSQL_PASSWORD" type="password" />
                  <FieldText k="MCP_MYSQL_DATABASE" label="MCP_MYSQL_DATABASE" placeholder="mydb" />
                </div>
                <div className="divider" />
                <FieldBool k="MCP_POSTGRES_ENABLED" label="Postgres MCP" />
                <FieldText k="MCP_POSTGRES_URL" label="MCP_POSTGRES_URL" placeholder="postgresql://user:pass@localhost/db" />
              </div>

              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  桌面自动化（Windows）
                </div>
                <FieldBool k="DESKTOP_ENABLED" label="启用桌面工具" help="启用/禁用桌面自动化工具集" />
                <div className="divider" />
                <div className="grid3">
                  <FieldText k="DESKTOP_DEFAULT_MONITOR" label="默认显示器" placeholder="0" />
                  <FieldText k="DESKTOP_MAX_WIDTH" label="最大宽" placeholder="1920" />
                  <FieldText k="DESKTOP_MAX_HEIGHT" label="最大高" placeholder="1080" />
                </div>
                <div className="grid3" style={{ marginTop: 10 }}>
                  <FieldText k="DESKTOP_COMPRESSION_QUALITY" label="压缩质量" placeholder="85" />
                  <FieldText k="DESKTOP_CACHE_TTL" label="截图缓存秒" placeholder="1.0" />
                  <FieldBool k="DESKTOP_FAILSAFE" label="failsafe" help="鼠标移到角落中止（PyAutoGUI 风格）" />
                </div>
                <div className="divider" />
                <FieldBool k="DESKTOP_VISION_ENABLED" label="启用视觉" help="用于屏幕理解/定位" />
                <div className="grid2" style={{ marginTop: 10 }}>
                  <FieldText k="DESKTOP_VISION_MODEL" label="视觉模型" placeholder="qwen3-vl-plus" />
                  <FieldText k="DESKTOP_VISION_OCR_MODEL" label="OCR 模型" placeholder="qwen-vl-ocr" />
                </div>
                <div className="grid3" style={{ marginTop: 10 }}>
                  <FieldText k="DESKTOP_CLICK_DELAY" label="click_delay" placeholder="0.1" />
                  <FieldText k="DESKTOP_TYPE_INTERVAL" label="type_interval" placeholder="0.03" />
                  <FieldText k="DESKTOP_MOVE_DURATION" label="move_duration" placeholder="0.15" />
                </div>
              </div>
            </div>

            <div className="divider" />
            <div className="grid3">
              <FieldCombo k="WHISPER_MODEL" label="WHISPER_MODEL" help="tiny/base/small/medium/large" options={[
                { value: "tiny", label: "tiny (~39MB)" },
                { value: "base", label: "base (~74MB)" },
                { value: "small", label: "small (~244MB)" },
                { value: "medium", label: "medium (~769MB)" },
                { value: "large", label: "large (~1.5GB)" },
              ]} placeholder="base" />
              <FieldSelect k="WHISPER_LANGUAGE" label="WHISPER_LANGUAGE" options={[
                { value: "zh", label: "中文 (zh)" },
                { value: "en", label: "English (en)" },
                { value: "auto", label: "Auto (自动检测)" },
              ]} />
              <FieldText k="GITHUB_TOKEN" label="GITHUB_TOKEN" placeholder="" type="password" help="用于搜索/下载技能" />
              <FieldText k="DATABASE_PATH" label="DATABASE_PATH" placeholder="data/agent.db" />
            </div>
          </div>

          <div className="card">
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              Agent 与系统（核心配置）
            </div>
            <div className="cardHint">
              这些是系统内置能力的开关与参数。<b>内置项默认启用</b>（你随时可以关闭）。建议先用默认值跑通，再按需调优。
            </div>
            <div className="divider" />

            <details open>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>基础</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                <FieldText k="AGENT_NAME" label="Agent 名称" placeholder="OpenAkita" />
                <FieldText k="MAX_ITERATIONS" label="最大迭代次数" placeholder="300" />
                <FieldBool k="AUTO_CONFIRM" label="自动确认（慎用）" help="打开后会减少交互确认，建议只在可信环境中使用" />
                <FieldSelect k="THINKING_MODE" label="Thinking 模式" options={[
                  { value: "auto", label: "auto (自动判断)" },
                  { value: "always", label: "always (始终思考)" },
                  { value: "never", label: "never (从不思考)" },
                ]} />
                <FieldText k="DATABASE_PATH" label="数据库路径" placeholder="data/agent.db" />
                <FieldSelect k="LOG_LEVEL" label="日志级别" options={[
                  { value: "DEBUG", label: "DEBUG" },
                  { value: "INFO", label: "INFO" },
                  { value: "WARNING", label: "WARNING" },
                  { value: "ERROR", label: "ERROR" },
                ]} />
              </div>
            </details>

            <div className="divider" />
            <details>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>日志高级</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                <FieldText k="LOG_DIR" label="日志目录" placeholder="logs" />
                <FieldText k="LOG_FILE_PREFIX" label="日志文件前缀" placeholder="openakita" />
                <FieldText k="LOG_MAX_SIZE_MB" label="单文件最大 MB" placeholder="10" />
                <FieldText k="LOG_BACKUP_COUNT" label="备份文件数" placeholder="30" />
                <FieldText k="LOG_RETENTION_DAYS" label="保留天数" placeholder="30" />
                <FieldText k="LOG_FORMAT" label="日志格式" placeholder="%(asctime)s - %(name)s - %(levelname)s - %(message)s" />
                <FieldBool k="LOG_TO_CONSOLE" label="输出到控制台" help="默认 true" />
                <FieldBool k="LOG_TO_FILE" label="输出到文件" help="默认 true" />
              </div>
            </details>

            <div className="divider" />
            <details>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>记忆与 Embedding</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                <FieldText k="EMBEDDING_MODEL" label="Embedding 模型" placeholder="shibing624/text2vec-base-chinese" />
                <FieldText k="EMBEDDING_DEVICE" label="Embedding 设备" placeholder="cpu / cuda" />
                <FieldSelect k="MODEL_DOWNLOAD_SOURCE" label="模型下载源" options={[
                  { value: "auto", label: "Auto (自动选择)" },
                  { value: "hf-mirror", label: "hf-mirror (国内镜像)" },
                  { value: "modelscope", label: "ModelScope (魔搭)" },
                  { value: "huggingface", label: "HuggingFace (官方)" },
                ]} />
                <FieldText k="MEMORY_HISTORY_DAYS" label="历史保留天数" placeholder="30" />
                <FieldText k="MEMORY_MAX_HISTORY_FILES" label="最大历史文件数" placeholder="1000" />
                <FieldText k="MEMORY_MAX_HISTORY_SIZE_MB" label="最大历史大小（MB）" placeholder="500" />
              </div>
            </details>

            <div className="divider" />
            <details>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>会话</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                <FieldText k="SESSION_TIMEOUT_MINUTES" label="会话超时（分钟）" placeholder="30" />
                <FieldText k="SESSION_MAX_HISTORY" label="会话最大历史条数" placeholder="50" />
                <FieldText k="SESSION_STORAGE_PATH" label="会话存储路径" placeholder="data/sessions" />
              </div>
            </details>

            <div className="divider" />
            <details open>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>调度器（默认启用）</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                <label className="pill" style={{ cursor: "pointer", userSelect: "none", alignSelf: "flex-start" }}>
                  <input
                    style={{ width: 16, height: 16 }}
                    type="checkbox"
                    checked={envGet(envDraft, "SCHEDULER_ENABLED", "true").toLowerCase() === "true"}
                    onChange={(e) => setEnvDraft((m) => envSet(m, "SCHEDULER_ENABLED", String(e.target.checked)))}
                  />
                  启用定时任务调度器（推荐）
                </label>
                <FieldText k="SCHEDULER_TIMEZONE" label="时区" placeholder="Asia/Shanghai" />
                <FieldText k="SCHEDULER_MAX_CONCURRENT" label="最大并发任务数" placeholder="5" />
                <FieldText k="SCHEDULER_TASK_TIMEOUT" label="任务超时（秒）" placeholder="600" />
              </div>
            </details>

            <div className="divider" />
            <details>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>多 Agent 协同（可选）</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                <FieldBool k="ORCHESTRATION_ENABLED" label="启用多 Agent（Master/Worker）" help="多数用户不需要；开启前建议先完成单 Agent 跑通" />
                <FieldText k="ORCHESTRATION_MODE" label="编排模式" placeholder="single" help="single=单 Agent / handoff=接力 / master-worker=主从" />
                <FieldText k="ORCHESTRATION_BUS_ADDRESS" label="总线地址" placeholder="tcp://127.0.0.1:5555" />
                <FieldText k="ORCHESTRATION_PUB_ADDRESS" label="广播地址" placeholder="tcp://127.0.0.1:5556" />
                <FieldText k="ORCHESTRATION_MIN_WORKERS" label="最小 Worker 数" placeholder="1" />
                <FieldText k="ORCHESTRATION_MAX_WORKERS" label="最大 Worker 数" placeholder="4" />
              </div>
            </details>
          </div>

          <div className="btnRow" style={{ gap: 8 }}>
            <button
              className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysCore, "已写入工作区 .env（工具/IM/MCP/桌面/高级配置）")}
              disabled={!currentWorkspaceId || !!busy}
            >
              一键写入工作区 .env（全覆盖）
            </button>
            <button className="btnApplyRestart"
              onClick={() => applyAndRestart(keysCore)}
              disabled={!currentWorkspaceId || !!busy || !!restartOverlay}
              title={t("config.applyRestartHint")}>
              {t("config.applyRestart")}
            </button>
          </div>
          
        </div>
      </>
    );
  }

  function renderQuickFinish() {
    const ws = workspaces.find((w) => w.id === currentWorkspaceId) || null;
    const epCount = savedEndpoints.length;
    const imEnabled = ["TELEGRAM_ENABLED", "FEISHU_ENABLED", "WEWORK_ENABLED", "DINGTALK_ENABLED", "ONEBOT_ENABLED", "QQBOT_ENABLED"]
      .filter((k) => (envDraft[k] || "").toLowerCase() === "true");
    return (
      <>
        <div className="card">
          <div className="cardTitle">{t("config.quickFinishTitle")}</div>
          <div className="cardHint" style={{ marginBottom: 16 }}>{t("config.quickFinishHint")}</div>
          <div className="divider" />
          <div className="grid2" style={{ marginTop: 12 }}>
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">{t("config.step.workspace")}</div>
              <div className="cardHint" style={{ marginTop: 4 }}>{ws?.name || "default"} ({ws?.path || "-"})</div>
            </div>
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">{t("config.step.endpoints")}</div>
              <div className="cardHint" style={{ marginTop: 4 }}>{epCount} {t("topbar.endpoints", { count: epCount })}</div>
            </div>
          </div>
          {imEnabled.length > 0 && (
            <div className="card" style={{ marginTop: 12 }}>
              <div className="label">{t("config.imTitle")}</div>
              <div className="cardHint" style={{ marginTop: 4 }}>
                {imEnabled.map((k) => k.replace("_ENABLED", "")).join(", ")}
              </div>
            </div>
          )}
          <div style={{ marginTop: 20, display: "flex", justifyContent: "center", gap: 12 }}>
            <button
              className="btnPrimary"
              style={{ padding: "10px 32px", fontSize: 15 }}
              onClick={async () => {
                const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
                if (!effectiveWsId) { setError(t("common.error")); return; }
                setError(null);
                setView("status");
                await startLocalServiceWithConflictCheck(effectiveWsId);
                try { await refreshServiceLog(effectiveWsId); } catch { /* ignore */ }
              }}
              disabled={!!busy}
            >
              {t("config.quickFinishLaunch")}
            </button>
            <button
              style={{ padding: "10px 24px", fontSize: 14 }}
              onClick={() => { setView("status"); }}
            >
              {t("config.quickFinishToStatus")}
            </button>
          </div>
        </div>
      </>
    );
  }

  function renderFinish() {
    const ws = workspaces.find((w) => w.id === currentWorkspaceId) || null;

    async function uninstallOpenAkita() {
      setError(null);
      setNotice(null);
      setBusy("卸载 openakita（venv）...");
      try {
        await invoke("pip_uninstall", { venvDir, packageName: "openakita" });
        setNotice("已卸载 openakita（venv）。你可以重新安装或删除 venv。");
      } catch (e) {
        setError(String(e));
      } finally {
        setBusy(null);
      }
    }

    async function removeRuntime() {
      setError(null);
      setNotice(null);
      setBusy("删除运行环境目录...");
      try {
        await invoke("remove_openakita_runtime", { removeVenv: true, removeEmbeddedPython: true });
        setNotice("已删除 ~/.openakita/venv 与 ~/.openakita/runtime（工作区配置保留）。");
      } catch (e) {
        setError(String(e));
      } finally {
        setBusy(null);
      }
    }

    return (
      <>
        <div className="card">
          <div className="cardTitle">完成：收尾与检查</div>
          <div className="cardHint">你已经完成安装与配置。这里是收尾步骤：检查配置、（可选）卸载与清理。</div>
          <div className="divider" />
          <div className="grid2">
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">检查配置文件</div>
              <div className="cardHint" style={{ marginTop: 8 }}>
                工作区目录：<b>{ws?.path || "（未选择）"}</b>
                <br />
                - `.env`（已写入你的 key/开关）
                <br />
                - `data/llm_endpoints.json`（端点列表）
                <br />
                - `data/skills.json`（外部技能启用状态）
                <br />- `identity/SOUL.md`（Agent 设定）
              </div>
            </div>
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">运行/验证（建议）</div>
              <div className="cardHint" style={{ marginTop: 8 }}>
                - 点击右上角“状态面板”，检查服务/端点/skills 是否正常
                <br />- 如启用 MCP Browser：确保已安装 Playwright 浏览器
                <br />- 如启用 Windows 桌面工具：确保安装 `openakita[windows]`
              </div>
            </div>
          </div>

          <div className="divider" />
          <div className="card">
            <div className="label">卸载（可选）</div>
            <div className="cardHint" style={{ marginTop: 8 }}>卸载模块是独立的：只卸载 venv 内的 `openakita` 包，不影响工作区配置文件。</div>
            <div className="btnRow" style={{ marginTop: 10 }}>
              <button onClick={uninstallOpenAkita} disabled={!!busy}>
                卸载 openakita（venv）
              </button>
            </div>
          </div>

          <div className="divider" />
          <div className="card">
            <div className="label">清理运行环境（可选）</div>
            <div className="cardHint" style={{ marginTop: 8 }}>
              删除 `~/.openakita/venv` 与 `~/.openakita/runtime`（会丢失已安装依赖与内置 Python），但**保留 workspaces 配置**。
            </div>
            <div className="divider" />
            <label className="pill" style={{ cursor: "pointer" }}>
              <input style={{ width: 16, height: 16 }} type="checkbox" checked={dangerAck} onChange={(e) => setDangerAck(e.target.checked)} />
              我已了解：删除运行环境是不可逆操作
            </label>
            <div className="btnRow" style={{ marginTop: 10 }}>
              <button className="btnDanger" onClick={removeRuntime} disabled={!dangerAck || !!busy}>
                删除运行环境（venv + runtime）
              </button>
            </div>
          </div>
        </div>
      </>
    );
  }

  // 构造端点摘要（供 ChatView 使用）
  const chatEndpoints: EndpointSummaryType[] = useMemo(() =>
    endpointSummary.map((e) => {
      const h = endpointHealth[e.name];
      return {
        name: e.name,
        provider: e.provider,
        apiType: e.apiType,
        baseUrl: e.baseUrl,
        model: e.model,
        keyEnv: e.keyEnv,
        keyPresent: e.keyPresent,
        health: h ? {
          name: e.name,
          status: h.status as "healthy" | "degraded" | "unhealthy" | "unknown",
          latencyMs: h.latencyMs,
          error: h.error,
          errorCategory: h.errorCategory,
          consecutiveFailures: h.consecutiveFailures,
          cooldownRemaining: h.cooldownRemaining,
          isExtendedCooldown: h.isExtendedCooldown,
          lastCheckedAt: h.lastCheckedAt,
        } : undefined,
      };
    }),
    [endpointSummary, endpointHealth],
  );

  // 保存 env keys 的辅助函数（供 SkillManager 使用，路由逻辑与 saveEnvKeys 一致）
  async function saveEnvKeysExternal(keys: string[]) {
    const entries: Record<string, string> = {};
    for (const k of keys) {
      if (Object.prototype.hasOwnProperty.call(envDraft, k)) {
        entries[k] = (envDraft[k] ?? "").trim();
      }
    }
    if (!Object.keys(entries).length) return;

    if (shouldUseHttpApi()) {
      try {
        await safeFetch(`${httpApiBase()}/api/config/env`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entries }),
        });
        return;
      } catch {
        console.warn("saveEnvKeysExternal: HTTP failed, falling back to Tauri");
      }
    }
    if (currentWorkspaceId) {
      const tauriEntries = Object.entries(entries).map(([key, value]) => ({ key, value }));
      await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries: tauriEntries });
    }
  }

  // ── Onboarding Wizard 渲染 ──
  async function obLoadModules() {
    try {
      const modules = await invoke<ModuleInfo[]>("detect_modules");
      setObModules(modules);
      // 首次加载时，将未安装的默认推荐模块加入选中集合
      if (!obModulesDefaultsApplied.current) {
        obModulesDefaultsApplied.current = true;
        const defaultIds = ["vector-memory", "browser", "whisper"];
        setObSelectedModules(prev => {
          const next = new Set(prev);
          for (const id of defaultIds) {
            const m = modules.find(mod => mod.id === id);
            if (m && !m.installed && !m.bundled) next.add(id);
          }
          return next;
        });
      }
    } catch (e) {
      console.warn("detect_modules failed:", e);
    }
  }

  async function obLoadEnvCheck() {
    try {
      const check = await invoke<typeof obEnvCheck>("check_environment");
      setObEnvCheck(check);
    } catch (e) {
      console.warn("check_environment failed:", e);
    }
  }

  function obToggleModule(id: string) {
    setObSelectedModules((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const [obHasErrors, setObHasErrors] = useState(false);

  // ── 结构化进度跟踪 ──
  type TaskStatus = "pending" | "running" | "done" | "error" | "skipped";
  type SetupTask = { id: string; label: string; status: TaskStatus; detail?: string };
  const [obTasks, setObTasks] = useState<SetupTask[]>([]);
  const [obDetailLog, setObDetailLog] = useState<string[]>([]);

  function updateTask(id: string, update: Partial<SetupTask>) {
    setObTasks(prev => prev.map(t => t.id === id ? { ...t, ...update } : t));
  }
  function addDetailLog(msg: string) {
    setObDetailLog(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);
  }

  async function obRunSetup() {
    setObInstalling(true);
    setObInstallLog([]);
    setObDetailLog([]);
    setObHasErrors(false);

    // 初始化任务列表
    const taskDefs: SetupTask[] = [
      { id: "workspace", label: "准备工作区", status: "pending" },
      { id: "llm-config", label: "保存 LLM 配置", status: savedEndpoints.length > 0 ? "pending" : "skipped" },
      { id: "env-save", label: "保存环境变量", status: "pending" },
    ];
    // 动态添加模块安装任务
    if (obSelectedModules.size > 0) {
      taskDefs.push({ id: "python-check", label: "检查 Python 环境", status: "pending" });
      for (const moduleId of obSelectedModules) {
        taskDefs.push({ id: `module-${moduleId}`, label: `安装模块: ${moduleId}`, status: "pending" });
      }
    }
    // CLI 注册
    const cliCommands: string[] = [];
    if (obCliOpenakita) cliCommands.push("openakita");
    if (obCliOa) cliCommands.push("oa");
    if (cliCommands.length > 0) {
      taskDefs.push({ id: "cli", label: `注册 CLI 命令 (${cliCommands.join(", ")})`, status: "pending" });
    }
    taskDefs.push({ id: "service-start", label: "启动后端服务", status: "pending" });
    taskDefs.push({ id: "http-wait", label: "等待 HTTP 服务就绪", status: "pending" });
    setObTasks(taskDefs);

    const log = (msg: string) => {
      setObInstallLog((prev) => [...prev, msg]);
      addDetailLog(msg);
    };
    let hasErr = false;

    try {
      // ── STEP: workspace ──
      updateTask("workspace", { status: "running" });
      let activeWsId = currentWorkspaceId;
      log(t("onboarding.progress.creatingWorkspace"));
      if (!activeWsId || !workspaces.length) {
        const wsList = await invoke<WorkspaceSummary[]>("list_workspaces");
        if (!wsList.length) {
          activeWsId = "default";
          await invoke("create_workspace", { name: t("onboarding.defaultWorkspace"), id: activeWsId, setCurrent: true });
          await invoke("set_current_workspace", { id: activeWsId });
          setCurrentWorkspaceId(activeWsId);
          log(t("onboarding.progress.workspaceCreated"));
        } else {
          activeWsId = wsList[0].id;
          setCurrentWorkspaceId(activeWsId);
          log(t("onboarding.progress.workspaceExists"));
        }
      } else {
        log(t("onboarding.progress.workspaceExists"));
      }
      updateTask("workspace", { status: "done" });

      // ── STEP: llm-config ──
      if (savedEndpoints.length > 0) {
        updateTask("llm-config", { status: "running" });
        const llmData = { endpoints: savedEndpoints, settings: {} };
        await invoke("workspace_write_file", {
          workspaceId: activeWsId,
          relativePath: "data/llm_endpoints.json",
          content: JSON.stringify(llmData, null, 2),
        });
        log(t("onboarding.progress.llmConfigSaved"));
        updateTask("llm-config", { status: "done", detail: `${savedEndpoints.length} 个端点` });
      }

      // ── STEP: env-save ──
      updateTask("env-save", { status: "running" });
      try {
        const imKeys = getAutoSaveKeysForStep("im");
        const envEntries: { key: string; value: string }[] = [];
        for (const k of imKeys) {
          if (Object.prototype.hasOwnProperty.call(envDraft, k) && envDraft[k]) {
            envEntries.push({ key: k, value: envDraft[k] });
          }
        }
        for (const ep of savedEndpoints) {
          const keyName = (ep as any).api_key_env;
          if (keyName && Object.prototype.hasOwnProperty.call(envDraft, keyName) && envDraft[keyName]) {
            envEntries.push({ key: keyName, value: envDraft[keyName] });
          }
        }
        if (envEntries.length > 0) {
          await invoke("workspace_update_env", { workspaceId: activeWsId, entries: envEntries });
          log(t("onboarding.progress.envSaved") || "✓ 环境变量已保存");
        }
        updateTask("env-save", { status: "done", detail: `${envEntries.length} 项` });
      } catch (e) {
        log(`⚠ 保存环境变量失败: ${String(e)}`);
        updateTask("env-save", { status: "error", detail: String(e) });
        hasErr = true;
      }

      // ── STEP: python-check + modules ──
      if (obSelectedModules.size > 0) {
        updateTask("python-check", { status: "running" });
        let pyReady = false;
        log("检查 Python 环境...");
        try {
          const pyCheck = await invoke<string>("check_python_for_pip");
          log(`✓ ${pyCheck}`);
          pyReady = true;
          updateTask("python-check", { status: "done", detail: pyCheck });
        } catch {
          log("未找到 Python 环境，正在安装嵌入式 Python...");
          updateTask("python-check", { detail: "正在安装嵌入式 Python..." });
          try {
            await invoke("install_embedded_python", { pythonSeries: "3.11" });
            log("✓ 嵌入式 Python 安装完成");
            pyReady = true;
            updateTask("python-check", { status: "done", detail: "嵌入式 Python" });
          } catch (pyErr) {
            log(`⚠ 嵌入式 Python 安装失败: ${String(pyErr)}`);
            updateTask("python-check", { status: "error", detail: String(pyErr) });
            hasErr = true;
          }
        }

        for (const moduleId of obSelectedModules) {
          const taskId = `module-${moduleId}`;
          updateTask(taskId, { status: "running" });
          log(t("onboarding.progress.installingModule", { module: moduleId }));
          if (!pyReady) {
            updateTask(taskId, { status: "error", detail: "Python 环境不可用" });
            log(`⚠ 跳过 ${moduleId}: Python 环境不可用`);
            hasErr = true;
            continue;
          }
          try {
            await invoke("install_module", { moduleId, mirror: null });
            log(t("onboarding.progress.moduleInstalled", { module: moduleId }));
            updateTask(taskId, { status: "done" });
          } catch (e) {
            log(t("onboarding.progress.moduleFailed", { module: moduleId, error: String(e) }));
            updateTask(taskId, { status: "error", detail: String(e).slice(0, 120) });
            hasErr = true;
          }
        }
      }

      // ── STEP: cli ──
      if (cliCommands.length > 0) {
        updateTask("cli", { status: "running" });
        log("注册 CLI 命令...");
        try {
          const result = await invoke<string>("register_cli", {
            commands: cliCommands,
            addToPath: obCliAddToPath,
          });
          log(`✓ ${result}`);
          updateTask("cli", { status: "done" });
        } catch (e) {
          log(`⚠ CLI 命令注册失败: ${String(e)}`);
          updateTask("cli", { status: "error", detail: String(e) });
        }
      }

      // ── STEP: service-start ──
      updateTask("service-start", { status: "running" });
      log(t("onboarding.progress.startingService"));
      const effectiveVenv = venvDir || (info ? joinPath(info.openakitaRootDir, "venv") : "");
      try {
        await invoke("openakita_service_start", { venvDir: effectiveVenv, workspaceId: activeWsId });
        log(t("onboarding.progress.serviceStarted"));
        updateTask("service-start", { status: "done" });

        // ── STEP: http-wait ──
        updateTask("http-wait", { status: "running" });
        log("等待 HTTP 服务就绪...");
        let httpReady = false;
        for (let i = 0; i < 20; i++) {
          await new Promise(r => setTimeout(r, 2000));
          updateTask("http-wait", { detail: `已等待 ${(i + 1) * 2}s...` });
          try {
            const res = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(3000) });
            if (res.ok) {
              log("✓ HTTP 服务已就绪");
              setServiceStatus({ running: true, pid: null, pidFile: "" });
              httpReady = true;
              updateTask("http-wait", { status: "done", detail: `${(i + 1) * 2}s` });
              break;
            }
          } catch { /* not ready yet */ }
          if (i % 5 === 4) log(`仍在等待 HTTP 服务启动... (${(i + 1) * 2}s)`);
        }
        if (!httpReady) {
          log("⚠ HTTP 服务尚未就绪，可进入主页面后手动刷新");
          updateTask("http-wait", { status: "error", detail: "超时" });
        }
      } catch (e) {
        const errStr = String(e);
        log(t("onboarding.progress.serviceStartFailed", { error: errStr }));
        updateTask("service-start", { status: "error", detail: errStr.slice(0, 120) });
        updateTask("http-wait", { status: "skipped" });
        if (errStr.length > 200) {
          log('--- 详细错误信息 ---');
          log(errStr);
        }
        hasErr = true;
      }

      log(t("onboarding.progress.done"));
    } catch (e) {
      log(t("onboarding.progress.error", { error: String(e) }));
      hasErr = true;
    } finally {
      setObHasErrors(hasErr);
      setObInstalling(false);
      setObStep("ob-done");
    }
  }

  function renderOnboarding() {
    const obStepDots = ["ob-welcome", "ob-llm", "ob-im", "ob-modules", "ob-cli", "ob-progress", "ob-done"] as OnboardingStep[];
    const obCurrentIdx = obStepDots.indexOf(obStep);

    const stepIndicator = (
      <div className="obStepIndicator">
        {obStepDots.map((s, i) => (
          <div
            key={s}
            className={`obDot ${i === obCurrentIdx ? "obDotActive" : i < obCurrentIdx ? "obDotDone" : ""}`}
          />
        ))}
      </div>
    );

    switch (obStep) {
      case "ob-welcome":
        return (
          <div className="obPage">
            <div className="obCenter">
              <img src={logoUrl} alt="OpenAkita" className="obLogo" />
              <h1 className="obTitle">{t("onboarding.welcome.title")}</h1>
              <p className="obDesc">{t("onboarding.welcome.desc")}</p>
              {obEnvCheck && obEnvCheck.conflicts.length > 0 && (
                <div className={
                  obEnvCheck.conflicts.some(c => c.includes("失败") || c.includes("进程"))
                    ? "obWarning"
                    : "obInfo"
                }>
                  <strong>
                    {obEnvCheck.conflicts.some(c => c.includes("失败") || c.includes("进程"))
                      ? t("onboarding.welcome.envWarning")
                      : t("onboarding.welcome.envCleaned")}
                  </strong>
                  <ul>
                    {obEnvCheck.conflicts.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}
              {obDetectedService && (
                <div className="obInfo" style={{ marginBottom: 12 }}>
                  <strong>{t("onboarding.welcome.serviceDetected")}</strong>
                  <p style={{ margin: "6px 0" }}>
                    {t("onboarding.welcome.serviceDetectedDesc", { version: obDetectedService.version })}
                  </p>
                  <button
                    className="btnPrimary"
                    style={{ marginTop: 4 }}
                    onClick={() => obConnectExistingService()}
                  >
                    {t("onboarding.welcome.connectExisting")}
                  </button>
                </div>
              )}
              <button
                className="btnPrimary obBtn"
                onClick={async () => {
                  // 首次运行：提前创建默认工作区，确保后续 LLM/IM 保存有正确的 workspaceId
                  try {
                    const wsList = await invoke<WorkspaceSummary[]>("list_workspaces");
                    if (!wsList.length) {
                      const wsId = "default";
                      await invoke("create_workspace", { name: t("onboarding.defaultWorkspace"), id: wsId, setCurrent: true });
                      await invoke("set_current_workspace", { id: wsId });
                      setCurrentWorkspaceId(wsId);
                      setWorkspaces([{ id: wsId, name: t("onboarding.defaultWorkspace"), path: "", isCurrent: true }]);
                    } else {
                      setWorkspaces(wsList);
                      if (!currentWorkspaceId && wsList.length > 0) {
                        setCurrentWorkspaceId(wsList[0].id);
                      }
                    }
                  } catch (e) {
                    console.warn("ob: create default workspace failed:", e);
                  }
                  setObStep("ob-llm");
                }}
              >
                {t("onboarding.welcome.start")}
              </button>
              <button
                className="obLinkBtn"
                onClick={() => {
                  setView("wizard");
                  setConfigMode("full");
                  setStepId("welcome");
                }}
              >
                {t("onboarding.welcome.advancedLink")}
              </button>
            </div>
            {stepIndicator}
          </div>
        );

      case "ob-llm":
        return (
          <div className="obPage">
            <div className="obContent">
              <h2 className="obStepTitle">{t("onboarding.llm.title")}</h2>
              <p className="obStepDesc">{t("onboarding.llm.desc")}</p>
              <div className="obFormArea">{renderLLM()}</div>
              <p className="obSkipHint">{t("onboarding.skipHint")}</p>
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <button onClick={() => setObStep("ob-welcome")}>{t("config.prev")}</button>
                {savedEndpoints.length > 0 ? (
                  <button className="btnPrimary" onClick={() => setObStep("ob-im")}>
                    {t("config.next")}
                  </button>
                ) : (
                  <button className="obSkipBtn" onClick={() => setObStep("ob-im")}>
                    {t("onboarding.llm.skip")}
                  </button>
                )}
              </div>
            </div>
          </div>
        );

      case "ob-im":
        return (
          <div className="obPage">
            <div className="obContent">
              <h2 className="obStepTitle">{t("onboarding.im.title")}</h2>
              <p className="obStepDesc">{t("onboarding.im.desc")}</p>
              <div className="obFormArea">{renderIM()}</div>
              <p className="obSkipHint">{t("onboarding.skipHint")}</p>
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <button onClick={() => setObStep("ob-llm")}>{t("config.prev")}</button>
                <button className="btnPrimary" onClick={() => { obLoadModules(); setObStep("ob-modules"); }}>
                  {t("config.next")}
                </button>
                <button className="obSkipBtn" onClick={() => { obLoadModules(); setObStep("ob-modules"); }} title={t("onboarding.im.skip")}>
                  {t("onboarding.im.skipShort") || t("onboarding.im.skip")}
                </button>
              </div>
            </div>
          </div>
        );

      case "ob-modules":
        return (
          <div className="obPage">
            <div className="obContent">
              <h2 className="obStepTitle">{t("onboarding.modules.title")}</h2>
              <p className="obStepDesc">{t("onboarding.modules.desc")}</p>
              <p style={{ fontSize: 13, color: "#64748b", margin: "0 0 8px", lineHeight: 1.5 }}>
                已为你推荐常用模块，如不需要可取消勾选。模块安装后也可在设置中管理。
              </p>
              <div className="obModuleList">
                {obModules.map((m) => (
                  <label key={m.id} className={`obModuleItem ${m.installed || m.bundled ? "obModuleInstalled" : ""}`}
                    style={obSelectedModules.has(m.id) && !m.installed && !m.bundled ? { borderColor: "#5B8DEF", background: "#f0f5ff" } : {}}
                  >
                    <input
                      type="checkbox"
                      checked={m.installed || m.bundled || obSelectedModules.has(m.id)}
                      disabled={m.installed || m.bundled}
                      onChange={() => obToggleModule(m.id)}
                    />
                    <div className="obModuleInfo">
                      <strong>{m.name}</strong>
                      <span className="obModuleDesc">{m.description}</span>
                      <span className="obModuleSize">~{m.sizeMb} MB</span>
                    </div>
                    {(m.installed || m.bundled) && <span className="obModuleBadge">{t("onboarding.modules.installed")}</span>}
                    {m.id === "orchestration" && !m.installed && !m.bundled && (
                      <span className="obModuleBadge" style={{ background: "#fef3c7", color: "#92400e" }}>Beta</span>
                    )}
                  </label>
                ))}
                {obModules.length === 0 && <p style={{ color: "#94a3b8" }}>{t("onboarding.modules.loading")}</p>}
              </div>
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <button onClick={() => setObStep("ob-im")}>{t("config.prev")}</button>
                <button className="btnPrimary" onClick={() => setObStep("ob-cli")}>
                  {t("config.next")}
                </button>
              </div>
            </div>
          </div>
        );

      case "ob-cli":
        return (
          <div className="obPage">
            <div className="obContent">
              <h2 className="obStepTitle">终端命令注册</h2>
              <p className="obStepDesc">
                注册命令行工具后，可在终端中直接使用命令管理服务，无需打开桌面客户端。
              </p>

              <div className="obModuleList">
                {/* openakita 命令 */}
                <label className={`obModuleItem ${obCliOpenakita ? "" : ""}`} style={obCliOpenakita ? { borderColor: "#5B8DEF", background: "#f0f5ff" } : {}}>
                  <input
                    type="checkbox"
                    checked={obCliOpenakita}
                    onChange={() => setObCliOpenakita(!obCliOpenakita)}
                  />
                  <div className="obModuleInfo">
                    <strong style={{ fontFamily: "monospace", fontSize: 15 }}>openakita</strong>
                    <span className="obModuleDesc">完整命令名称</span>
                  </div>
                </label>

                {/* oa 命令 */}
                <label className={`obModuleItem`} style={obCliOa ? { borderColor: "#5B8DEF", background: "#f0f5ff" } : {}}>
                  <input
                    type="checkbox"
                    checked={obCliOa}
                    onChange={() => setObCliOa(!obCliOa)}
                  />
                  <div className="obModuleInfo">
                    <strong style={{ fontFamily: "monospace", fontSize: 15 }}>oa</strong>
                    <span className="obModuleDesc">简短别名，推荐日常使用</span>
                  </div>
                  <span className="obModuleBadge" style={{ background: "#e0e7ff", color: "#4f46e5" }}>推荐</span>
                </label>

                {/* PATH 选项 */}
                <label className={`obModuleItem`} style={obCliAddToPath ? { borderColor: "#5B8DEF", background: "#f0f5ff" } : {}}>
                  <input
                    type="checkbox"
                    checked={obCliAddToPath}
                    onChange={() => setObCliAddToPath(!obCliAddToPath)}
                  />
                  <div className="obModuleInfo">
                    <strong>添加到系统 PATH</strong>
                    <span className="obModuleDesc">新打开的终端中可直接输入命令名运行，无需完整路径</span>
                  </div>
                </label>
              </div>

              {/* 命令预览 */}
              {(obCliOpenakita || obCliOa) && (
                <div className="obFormArea" style={{ marginTop: 16, padding: "16px 20px" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "#475569", marginBottom: 10 }}>
                    安装后可使用的命令示例
                  </div>
                  <div style={{
                    background: "#1e293b", borderRadius: 8, padding: "14px 18px",
                    fontFamily: "'Cascadia Code', 'Fira Code', 'SF Mono', Consolas, monospace",
                    fontSize: 13, lineHeight: 1.9, color: "#e2e8f0", overflowX: "auto",
                  }}>
                    {obCliOa && <>
                      <div><span style={{ color: "#94a3b8" }}>$</span> <span style={{ color: "#7dd3fc" }}>oa</span> serve <span style={{ color: "#64748b", marginLeft: 24 }}># 启动后端服务</span></div>
                      <div><span style={{ color: "#94a3b8" }}>$</span> <span style={{ color: "#7dd3fc" }}>oa</span> status <span style={{ color: "#64748b", marginLeft: 16 }}># 查看运行状态</span></div>
                      <div><span style={{ color: "#94a3b8" }}>$</span> <span style={{ color: "#7dd3fc" }}>oa</span> run <span style={{ color: "#64748b", marginLeft: 36 }}># 单次对话</span></div>
                    </>}
                    {obCliOa && obCliOpenakita && <div style={{ height: 4 }} />}
                    {obCliOpenakita && <>
                      <div><span style={{ color: "#94a3b8" }}>$</span> <span style={{ color: "#a5b4fc" }}>openakita</span> init <span style={{ color: "#64748b", marginLeft: 8 }}># 初始化工作区</span></div>
                      <div><span style={{ color: "#94a3b8" }}>$</span> <span style={{ color: "#a5b4fc" }}>openakita</span> serve <span style={{ color: "#64748b" }}># 启动后端服务</span></div>
                    </>}
                  </div>
                </div>
              )}
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <button onClick={() => setObStep("ob-modules")}>{t("config.prev")}</button>
                <button className="btnPrimary" onClick={() => { setObStep("ob-progress"); obRunSetup(); }}>
                  {obSelectedModules.size > 0
                    ? t("onboarding.modules.installAndContinue")
                    : t("onboarding.modules.skipAndContinue")}
                </button>
              </div>
            </div>
          </div>
        );

      case "ob-progress": {
        const taskStatusIcon = (status: TaskStatus) => {
          switch (status) {
            case "done": return <span style={{ color: "#22c55e", fontSize: 18 }}>&#x2714;</span>;
            case "running": return <span className="obProgressSpinnerIcon" />;
            case "error": return <span style={{ color: "#ef4444", fontSize: 18 }}>&#x2716;</span>;
            case "skipped": return <span style={{ color: "#9ca3af", fontSize: 14 }}>&#x2014;</span>;
            default: return <span style={{ color: "#d1d5db", fontSize: 14 }}>&#x25CB;</span>;
          }
        };
        const taskStatusColor: Record<TaskStatus, string> = {
          done: "#22c55e", running: "#3b82f6", error: "#ef4444", skipped: "#9ca3af", pending: "#9ca3af",
        };
        return (
          <div className="obPage">
            <div className="obContent" style={{ display: "flex", flexDirection: "column", gap: 0, flex: 1, minHeight: 0 }}>
              <h2 className="obStepTitle">{t("onboarding.progress.title")}</h2>

              {/* ── 任务进度列表 ── */}
              <div style={{
                background: "#f8fafc", borderRadius: 12, border: "1px solid #e2e8f0",
                padding: "16px 20px", marginBottom: 12,
              }}>
                {obTasks.map((task, idx) => (
                  <div key={task.id} style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "8px 0",
                    borderBottom: idx < obTasks.length - 1 ? "1px solid #f1f5f9" : "none",
                    opacity: task.status === "pending" ? 0.5 : 1,
                  }}>
                    <div style={{ width: 24, textAlign: "center", flexShrink: 0 }}>
                      {taskStatusIcon(task.status)}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 14, fontWeight: task.status === "running" ? 600 : 400,
                        color: taskStatusColor[task.status] ?? "#475569",
                      }}>
                        {task.label}
                      </div>
                      {task.detail && (
                        <div style={{
                          fontSize: 12, color: task.status === "error" ? "#ef4444" : "#94a3b8",
                          marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                        }}>
                          {task.detail}
                        </div>
                      )}
                    </div>
                    {task.status === "running" && (
                      <span style={{ fontSize: 12, color: "#3b82f6", flexShrink: 0, fontWeight: 500 }}>进行中</span>
                    )}
                  </div>
                ))}
              </div>

              {/* ── 实时日志窗口 ── */}
              <div style={{
                flex: 1, minHeight: 120, maxHeight: 200,
                background: "#1e293b", borderRadius: 10, padding: "12px 16px",
                overflowY: "auto", overflowX: "hidden",
                fontFamily: "'Cascadia Code', 'Fira Code', Consolas, monospace",
                fontSize: 12, lineHeight: 1.7, color: "#cbd5e1",
              }}
                ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}
              >
                {obDetailLog.length === 0 && (
                  <div style={{ color: "#64748b" }}>等待任务开始...</div>
                )}
                {obDetailLog.map((line, i) => (
                  <div key={i} style={{
                    color: line.includes("⚠") || line.includes("失败") ? "#fbbf24"
                         : line.includes("✓") ? "#4ade80"
                         : line.includes("---") ? "#64748b"
                         : "#cbd5e1",
                  }}>{line}</div>
                ))}
                {obInstalling && (
                  <div style={{ color: "#60a5fa" }}>
                    <span className="obProgressSpinnerIcon" style={{ display: "inline-block", marginRight: 8 }} />
                    {t("onboarding.progress.working")}
                  </div>
                )}
              </div>
            </div>
            <div className="obFooter">
              {stepIndicator}
            </div>
          </div>
        );
      }

      case "ob-done":
        return (
          <div className="obPage">
            <div className="obCenter">
              <div className="obDoneIcon">✓</div>
              <h1 className="obTitle">{t("onboarding.done.title")}</h1>
              <p className="obDesc">{t("onboarding.done.desc")}</p>
              {obHasErrors && (
                <div className="obWarning">
                  <strong>{t("onboarding.done.someErrors")}</strong>
                  <p>{t("onboarding.done.errorsHint")}</p>
                </div>
              )}
              <button
                className="btnPrimary obBtn"
                onClick={async () => {
                  // 设置短暂宽限期：onboarding 结束后 HTTP 服务可能还在启动中
                  // 避免心跳检测立刻报"不可达"导致闪烁
                  visibilityGraceRef.current = true;
                  heartbeatFailCount.current = 0;
                  setTimeout(() => { visibilityGraceRef.current = false; }, 15000);
                  setView("status");
                  await refreshAll();
                  // 关键：刷新端点列表、IM 状态等（forceAliveCheck=true 绕过 serviceStatus 闭包）
                  // 首次尝试
                  try { await refreshStatus("local", "http://127.0.0.1:18900", true); } catch { /* ignore */ }
                  autoCheckEndpoints("http://127.0.0.1:18900");
                  // 延迟重试：后端 API 可能还在初始化，3 秒后再拉一次端点列表
                  setTimeout(async () => {
                    try { await refreshStatus("local", "http://127.0.0.1:18900", true); } catch { /* ignore */ }
                  }, 3000);
                  // 8 秒后最终重试
                  setTimeout(async () => {
                    try { await refreshStatus("local", "http://127.0.0.1:18900", true); } catch { /* ignore */ }
                  }, 8000);
                }}
              >
                {t("onboarding.done.enter")}
              </button>
            </div>
            {stepIndicator}
          </div>
        );

      default:
        return null;
    }
  }

  function renderStepContent() {
    if (!info) return <div className="card">加载中...</div>;
    if (view === "status") return renderStatus();
    if (view === "chat") return null;  // ChatView 始终挂载，不在此渲染
    if (view === "skills") {
      return (
        <SkillManager
          venvDir={venvDir}
          currentWorkspaceId={currentWorkspaceId}
          envDraft={envDraft}
          onEnvChange={setEnvDraft}
          onSaveEnvKeys={saveEnvKeysExternal}
          apiBaseUrl={apiBaseUrl}
          serviceRunning={!!serviceStatus?.running}
          dataMode={dataMode}
        />
      );
    }
    if (view === "im") {
      return <IMView serviceRunning={serviceStatus?.running ?? false} />;
    }
    if (view === "modules") {
      return (
        <div className="card">
          <h2 className="cardTitle">{t("modules.title")}</h2>
          <p style={{ color: "#64748b", fontSize: 13, marginBottom: 16 }}>{t("modules.desc")}</p>
          <div className="obModuleList">
            {obModules.map((m) => (
              <div key={m.id} className={`obModuleItem ${m.installed || m.bundled ? "obModuleInstalled" : ""}`}>
                <div className="obModuleInfo" style={{ flex: 1 }}>
                  <strong>{m.name}</strong>
                  <span className="obModuleDesc">{m.description}</span>
                  <span className="obModuleSize">~{m.sizeMb} MB</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {(m.installed || m.bundled) ? (
                    <>
                      <span className="obModuleBadge">{t("modules.installed")}</span>
                      <button
                        className="btnSmall"
                        style={{ color: "#ef4444" }}
                        onClick={async () => {
                          try {
                            await invoke("uninstall_module", { moduleId: m.id });
                            setNotice(t("modules.uninstalled", { name: m.name }));
                            obLoadModules();
                          } catch (e) {
                            setError(String(e));
                          }
                        }}
                        disabled={m.bundled}
                        title={m.bundled ? t("modules.bundledCannotUninstall") : t("modules.uninstall")}
                      >
                        {t("modules.uninstall")}
                      </button>
                    </>
                  ) : (
                    <button
                      className="btnPrimary btnSmall"
                      onClick={async () => {
                        try {
                          setBusy(t("modules.installing", { name: m.name }));
                          await invoke("install_module", { moduleId: m.id, mirror: null });
                          setNotice(t("modules.installSuccess", { name: m.name }));
                          obLoadModules();
                        } catch (e) {
                          setError(String(e));
                        } finally {
                          setBusy(null);
                        }
                      }}
                      disabled={!!busy}
                    >
                      {t("modules.install")}
                    </button>
                  )}
                </div>
              </div>
            ))}
            {obModules.length === 0 && <p style={{ color: "#94a3b8" }}>{t("modules.loading")}</p>}
          </div>
          <button className="btnSmall" style={{ marginTop: 16 }} onClick={obLoadModules} disabled={!!busy}>
            {t("modules.refresh")}
          </button>
        </div>
      );
    }
    switch (stepId) {
      case "welcome":
        return renderWelcome();
      case "workspace":
        return renderWorkspace();
      case "python":
        return renderPython();
      case "install":
        return renderInstall();
      case "llm":
        return renderLLM();
      case "im":
        return renderIM();
      case "tools":
        return renderTools();
      case "agent":
        return renderAgentSystem();
      case "finish":
        return renderFinish();
      case "quick-form":
        return renderQuickForm();
      case "quick-setup":
        return renderQuickAutoSetup();
      case "quick-finish":
        return renderQuickFinish();
      default:
        return renderWelcome();
    }
  }

  // ── 初始化加载中：检测是否首次运行，防止先闪主页面再跳 onboarding ──
  if (appInitializing) {
    return (
      <div className="onboardingShell" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center", opacity: 0.6 }}>
          <div className="spinner" style={{ margin: "0 auto 16px" }} />
          <div style={{ fontSize: 14 }}>Loading...</div>
        </div>
      </div>
    );
  }

  // ── Onboarding 全屏模式 (隐藏侧边栏和顶部状态栏) ──
  if (view === "onboarding") {
    return (
      <div className="onboardingShell">
        {renderOnboarding()}
      </div>
    );
  }

  return (
    <div className={`appShell ${sidebarCollapsed ? "appShellCollapsed" : ""}`}>
      <aside className={`sidebar ${sidebarCollapsed ? "sidebarCollapsed" : ""}`}>
        <div className="sidebarHeader">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <img
              src={logoUrl}
              alt="OpenAkita"
              className="brandLogo"
              onClick={() => setSidebarCollapsed((v) => !v)}
              style={{ cursor: "pointer" }}
              title={sidebarCollapsed ? t("sidebar.expand") : t("sidebar.collapse")}
            />
            {!sidebarCollapsed && (
              <div>
                <div className="brandTitle">{t("brand.title")}</div>
                <div className="brandSub">{t("brand.sub")}</div>
              </div>
            )}
          </div>
        </div>

        {/* Primary nav */}
        <div className="sidebarNav">
          <div className={`navItem ${view === "chat" ? "navItemActive" : ""}`} onClick={() => setView("chat")} role="button" tabIndex={0} title={t("sidebar.chat")}>
            <IconChat size={16} /> {!sidebarCollapsed && <span>{t("sidebar.chat")}</span>}
          </div>
          <div className={`navItem ${view === "im" ? "navItemActive" : ""}`} onClick={() => setView("im")} role="button" tabIndex={0} title={t("sidebar.im")}>
            <IconIM size={16} /> {!sidebarCollapsed && <span>{t("sidebar.im")}</span>}
          </div>
          <div className={`navItem ${view === "skills" ? "navItemActive" : ""}`} onClick={() => setView("skills")} role="button" tabIndex={0} title={t("sidebar.skills")}>
            <IconSkills size={16} /> {!sidebarCollapsed && <span>{t("sidebar.skills")}</span>}
          </div>
          <div className={`navItem ${view === "modules" ? "navItemActive" : ""}`} onClick={() => { setView("modules"); obLoadModules(); }} role="button" tabIndex={0} title={t("sidebar.modules")}>
            <IconGear size={16} /> {!sidebarCollapsed && <span>{t("sidebar.modules")}</span>}
          </div>
          <div className={`navItem ${view === "status" ? "navItemActive" : ""}`} onClick={async () => { setView("status"); try { await refreshStatus(); } catch { /* ignore */ } }} role="button" tabIndex={0} title={t("sidebar.status")}>
            <IconStatus size={16} /> {!sidebarCollapsed && <span>{t("sidebar.status")}</span>}
          </div>
        </div>

        {/* Collapsible Config section */}
        <div className="configSection">
          <div className="configHeader" onClick={() => { if (sidebarCollapsed || configMode === null) { setView("wizard"); setStepId("welcome"); } else { setConfigExpanded((v) => !v); } }} role="button" tabIndex={0} title={t("sidebar.config")}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <IconConfig size={16} />
              {!sidebarCollapsed && <span>{t("sidebar.config")}</span>}
            </div>
            {!sidebarCollapsed && configMode !== null && (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className="configProgress">{t("sidebar.configProgress", { done: doneCount, total: totalSteps })}</span>
                {configExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
              </div>
            )}
          </div>
          {!sidebarCollapsed && configExpanded && configMode !== null && (
            <div className="stepList">
              {/* Mode selection entry - always visible when a mode has been chosen */}
              {configMode !== null && (
                <div
                  className={`stepItem ${view === "wizard" && stepId === "welcome" && configMode === null ? "stepItemActive" : ""}`}
                  style={{ opacity: 0.7, borderBottom: "1px solid var(--line, #e0e0e0)", paddingBottom: 8, marginBottom: 4 }}
                  onClick={() => {
                    quickSetupStarted.current = false;
                    setQuickSetupPhase(0);
                    setQuickSetupError(null);
                    setConfigMode(null);
                    setStepId("welcome");
                    setMaxReachedStepIdx(0);
                    localStorage.setItem("openakita_maxStep", "0");
                    setView("wizard");
                  }}
                  role="button" tabIndex={0}
                >
                  <div className="stepDot" style={{ width: 20, height: 20, fontSize: 10 }}>
                    <IconChevronRight size={12} style={{ transform: "rotate(180deg)" }} />
                  </div>
                  <div className="stepMeta"><div className="stepTitle" style={{ fontSize: 11 }}>{t("welcome.backToModeSelect")}</div></div>
                </div>
              )}
              {steps.filter((s) => s.id !== "welcome" || configMode === null).map((s, idx) => {
                const isActive = view === "wizard" && s.id === stepId;
                const isDone = done.has(s.id);
                // quick-setup is non-interactive, don't allow clicking it
                const canJump = s.id === "quick-setup" ? false : (idx <= maxReachedStepIdx || isDone);
                return (
                  <div
                    key={s.id}
                    className={`stepItem ${isActive ? "stepItemActive" : ""} ${canJump ? "" : "stepItemDisabled"}`}
                    onClick={() => { if (!canJump) return; setView("wizard"); setStepId(s.id); }}
                    role="button" tabIndex={0} aria-disabled={!canJump}
                  >
                    <StepDot idx={idx} isDone={isDone} />
                    <div className="stepMeta"><div className="stepTitle">{s.title}</div></div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Version info + website link at sidebar bottom */}
        {!sidebarCollapsed && (
          <div style={{
            padding: "10px 16px",
            borderTop: "1px solid var(--line)",
            fontSize: 11,
            opacity: 0.4,
            lineHeight: 1.6,
            flexShrink: 0,
          }}>
            <div>Desktop v{desktopVersion}</div>
            {backendVersion && <div>Backend v{backendVersion}</div>}
            {!backendVersion && serviceStatus?.running && <div>Backend: -</div>}
            <div style={{ marginTop: 4 }}>
              <a
                href="https://openakita.ai"
                style={{ color: "var(--accent, #5B8DEF)", textDecoration: "none", opacity: 1 }}
                onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
                onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
              >
                <IconGlobe size={11} style={{ verticalAlign: "-1px", marginRight: 3 }} />
                openakita.ai
              </a>
            </div>
          </div>
        )}
        {sidebarCollapsed && (
          <div style={{
            padding: "8px 0",
            borderTop: "1px solid var(--line)",
            flexShrink: 0,
            display: "flex",
            justifyContent: "center",
          }}>
            <a
              href="https://openakita.ai"
              title="openakita.ai"
              style={{ color: "var(--accent, #5B8DEF)", opacity: 0.5, display: "flex" }}
            >
              <IconGlobe size={14} />
            </a>
          </div>
        )}
      </aside>

      <main className="main">
        {/* Compact status bar */}
        <div className="topbar">
          <div className="topbarStatusRow">
            <span className="topbarWs">{currentWorkspaceId || "default"}</span>
            <span className="topbarIndicator">
              {serviceStatus?.running ? <DotGreen /> : <DotGray />}
              <span>{serviceStatus?.running ? t("topbar.running") : t("topbar.stopped")}</span>
            </span>
            <span className="topbarEpCount">{t("topbar.endpoints", { count: endpointSummary.length })}</span>
            {dataMode === "remote" && <span className="pill" style={{ fontSize: 10, marginLeft: 4, background: "#e3f2fd", color: "#1565c0" }}>{t("connect.remoteMode")}</span>}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {serviceStatus?.running ? (
              <button
                className="topbarConnectBtn"
                onClick={() => {
                  // Disconnect: reset to local idle mode
                  setDataMode("local");
                  setServiceStatus({ running: false, pid: null, pidFile: "" });
                  envLoadedForWs.current = null;
                  setNotice(t("topbar.disconnected"));
                }}
                disabled={!!busy}
                title={t("topbar.disconnect")}
              >
                <IconX size={13} />
                <span>{t("topbar.disconnect")}</span>
              </button>
            ) : (
              <>
                <button
                  className="topbarConnectBtn"
                  onClick={() => {
                    setConnectAddress(apiBaseUrl.replace(/^https?:\/\//, ""));
                    setConnectDialogOpen(true);
                  }}
                  disabled={!!busy}
                  title={t("topbar.connect")}
                >
                  <IconLink size={13} />
                  <span>{t("topbar.connect")}</span>
                </button>
                <button
                  className="topbarConnectBtn"
                  onClick={async () => {
                    const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
                    if (!effectiveWsId) { setError(t("common.error")); return; }
                    await startLocalServiceWithConflictCheck(effectiveWsId);
                  }}
                  disabled={!!busy}
                  title={t("topbar.start")}
                >
                  <IconPower size={13} />
                  <span>{t("topbar.start")}</span>
                </button>
              </>
            )}
            <button className="topbarRefreshBtn" onClick={async () => { await refreshAll(); try { await refreshStatus(); } catch {} }} disabled={!!busy} title={t("topbar.refresh")}>
              <IconRefresh size={14} />
            </button>
            <button
              className="topbarRefreshBtn"
              onClick={() => { i18n.changeLanguage(i18n.language?.startsWith("zh") ? "en" : "zh"); }}
              title="中/EN"
            >
              <IconGlobe size={14} />
            </button>
          </div>
        </div>

        {/* ChatView 始终挂载，切走时隐藏以保留聊天记录 */}
        <div className="contentChat" style={{ display: view === "chat" ? undefined : "none" }}>
          <ChatView
            serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl}
            endpoints={chatEndpoints}
            onStartService={async () => {
              const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
              if (!effectiveWsId) {
                setError("未找到工作区（请先创建/选择一个工作区）");
                return;
              }
              await startLocalServiceWithConflictCheck(effectiveWsId);
            }}
          />
        </div>
        <div className="content" style={{ display: view !== "chat" ? undefined : "none" }}>
          {renderStepContent()}
        </div>

        {/* ── Connect Dialog ── */}
        {connectDialogOpen && (
          <div className="modalOverlay" onClick={() => setConnectDialogOpen(false)}>
            <div className="modalContent" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 420 }}>
              <div className="dialogHeader">
                <span className="cardTitle">{t("connect.title")}</span>
                <button className="dialogCloseBtn" onClick={() => setConnectDialogOpen(false)}>&times;</button>
              </div>
              <div className="dialogSection">
                <p style={{ color: "#64748b", fontSize: 13, margin: "0 0 16px" }}>{t("connect.hint")}</p>
                <div className="dialogLabel">{t("connect.address")}</div>
                <input
                  value={connectAddress}
                  onChange={(e) => setConnectAddress(e.target.value)}
                  placeholder="127.0.0.1:18900"
                  autoFocus
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 14 }}
                />
              </div>
              <div className="dialogFooter">
                <button className="btnSmall" onClick={() => setConnectDialogOpen(false)}>{t("common.cancel")}</button>
                <button className="btnPrimary" disabled={!!busy} onClick={async () => {
                  const addr = connectAddress.trim();
                  if (!addr) return;
                  const url = addr.startsWith("http") ? addr : `http://${addr}`;
                  setBusy(t("connect.testing"));
                  try {
                    const res = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(5000) });
                    const data = await res.json();
                    if (data.status === "ok") {
                      setApiBaseUrl(url);
                      localStorage.setItem("openakita_apiBaseUrl", url);
                      setDataMode("remote");
                      setServiceStatus({ running: true, pid: null, pidFile: "" });
                      setConnectDialogOpen(false);
                      setNotice(t("connect.success"));
                      // Check version mismatch
                      if (data.version) checkVersionMismatch(data.version);
                      await refreshStatus("remote", url, true);
                      autoCheckEndpoints(url);
                    } else {
                      setError(t("connect.fail"));
                    }
                  } catch {
                    setError(t("connect.fail"));
                  } finally { setBusy(null); }
                }}>{t("connect.confirm")}</button>
              </div>
            </div>
          </div>
        )}

        {/* ── Restart overlay ── */}
        {restartOverlay && (
          <div className="modalOverlay" style={{ zIndex: 10000, background: "rgba(0,0,0,0.5)" }}>
            <div className="modalContent" style={{ maxWidth: 360, padding: "32px 28px", textAlign: "center", borderRadius: 16 }} onClick={(e) => e.stopPropagation()}>
              {(restartOverlay.phase === "saving" || restartOverlay.phase === "restarting" || restartOverlay.phase === "waiting") && (
                <>
                  <div style={{ marginBottom: 16 }}>
                    <svg width="40" height="40" viewBox="0 0 40 40" style={{ animation: "spin 1s linear infinite" }}>
                      <circle cx="20" cy="20" r="16" fill="none" stroke="#0ea5e9" strokeWidth="3" strokeDasharray="80" strokeDashoffset="20" strokeLinecap="round" />
                    </svg>
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: "#0e7490" }}>
                    {restartOverlay.phase === "saving" && t("common.loading")}
                    {restartOverlay.phase === "restarting" && t("config.restarting")}
                    {restartOverlay.phase === "waiting" && t("config.restartWaiting")}
                  </div>
                  <div style={{ fontSize: 12, color: "#64748b", marginTop: 8 }}>
                    {t("config.applyRestartHint")}
                  </div>
                </>
              )}
              {restartOverlay.phase === "done" && (
                <>
                  <div style={{ fontSize: 36, marginBottom: 8 }}><IconCheckCircle size={40} /></div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: "#059669" }}>{t("config.restartSuccess")}</div>
                </>
              )}
              {restartOverlay.phase === "fail" && (
                <>
                  <div style={{ fontSize: 36, marginBottom: 8 }}><IconXCircle size={40} /></div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: "#dc2626" }}>{t("config.restartFail")}</div>
                </>
              )}
              {restartOverlay.phase === "notRunning" && (
                <>
                  <div style={{ fontSize: 36, marginBottom: 8 }}><IconInfo size={40} /></div>
                  <div style={{ fontSize: 14, fontWeight: 500, color: "#64748b" }}>{t("config.restartNotRunning")}</div>
                </>
              )}
            </div>
          </div>
        )}
        <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>

        {/* ── Service conflict dialog ── */}
        {conflictDialog && (
          <div className="modalOverlay" onClick={() => { setConflictDialog(null); setPendingStartWsId(null); }}>
            <div className="modalContent" style={{ maxWidth: 440, padding: 24 }} onClick={(e) => e.stopPropagation()}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <span style={{ fontSize: 20 }}>⚠️</span>
                <span style={{ fontWeight: 600, fontSize: 15 }}>{t("conflict.title")}</span>
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.7, marginBottom: 8 }}>{t("conflict.message")}</div>
              <div style={{ fontSize: 12, color: "#64748b", marginBottom: 20 }}>
                {t("conflict.detail", { pid: conflictDialog.pid, version: conflictDialog.version })}
              </div>
              <div className="dialogFooter" style={{ justifyContent: "flex-end", gap: 8 }}>
                <button className="btnSmall" onClick={() => { setConflictDialog(null); setPendingStartWsId(null); }}>{t("conflict.cancel")}</button>
                <button className="btnSmall" style={{ background: "#e53935", color: "#fff", border: "none" }}
                  onClick={() => stopAndRestartService()} disabled={!!busy}>{t("conflict.stopAndRestart")}</button>
                <button className="btnPrimary" style={{ padding: "6px 16px", borderRadius: 8 }}
                  onClick={() => connectToExistingLocalService()}>{t("conflict.connectExisting")}</button>
              </div>
            </div>
          </div>
        )}

        {/* ── Version mismatch banner ── */}
        {versionMismatch && (
          <div style={{ position: "fixed", top: 48, left: "50%", transform: "translateX(-50%)", zIndex: 9999, background: "#fff3e0", border: "1px solid #ffb74d", borderRadius: 10, padding: "12px 20px", maxWidth: 500, boxShadow: "0 4px 20px rgba(0,0,0,0.08)", display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 16 }}>⚠️</span>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{t("version.mismatch")}</span>
              <button style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", fontSize: 16, color: "#999" }} onClick={() => setVersionMismatch(null)}>&times;</button>
            </div>
            <div style={{ fontSize: 12, color: "#6d4c00", lineHeight: 1.6 }}>
              {t("version.mismatchDetail", { backend: versionMismatch.backend, desktop: versionMismatch.desktop })}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button className="btnSmall" style={{ fontSize: 11 }} onClick={() => {
                navigator.clipboard.writeText(t("version.pipCommand")).then(() => setNotice(t("version.copied")));
              }}>{t("version.updatePip")}</button>
              <code style={{ fontSize: 11, background: "#f5f5f5", padding: "2px 8px", borderRadius: 4, color: "#333" }}>{t("version.pipCommand")}</code>
            </div>
          </div>
        )}

        {/* ── New release notification ── */}
        {newRelease && (
          <div style={{ position: "fixed", bottom: 20, right: 20, zIndex: 9998, background: "#e3f2fd", border: "1px solid #90caf9", borderRadius: 10, padding: "12px 20px", maxWidth: 380, boxShadow: "0 4px 20px rgba(0,0,0,0.08)", display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 16 }}>🎉</span>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{t("version.newRelease")}</span>
              <button style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", fontSize: 16, color: "#999" }} onClick={() => {
                setNewRelease(null);
                localStorage.setItem("openakita_release_dismissed", newRelease.latest);
              }}>&times;</button>
            </div>
            <div style={{ fontSize: 12, color: "#0d47a1", lineHeight: 1.6 }}>
              {t("version.newReleaseDetail", { latest: newRelease.latest, current: newRelease.current })}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <a href={newRelease.url} target="_blank" rel="noreferrer" className="btnSmall btnSmallPrimary" style={{ fontSize: 11, textDecoration: "none" }}>{t("version.viewRelease")}</a>
              <button className="btnSmall" style={{ fontSize: 11 }} onClick={() => {
                setNewRelease(null);
                localStorage.setItem("openakita_release_dismissed", newRelease.latest);
              }}>{t("version.dismiss")}</button>
            </div>
          </div>
        )}

        {/* ── Generic confirm dialog ── */}
        {confirmDialog && (
          <div className="modalOverlay" onClick={() => setConfirmDialog(null)}>
            <div className="modalContent" style={{ maxWidth: 380, padding: 24 }} onClick={(e) => e.stopPropagation()}>
              <div style={{ fontSize: 14, lineHeight: 1.6, marginBottom: 20 }}>{confirmDialog.message}</div>
              <div className="dialogFooter" style={{ justifyContent: "flex-end" }}>
                <button className="btnSmall" onClick={() => setConfirmDialog(null)}>{t("common.cancel")}</button>
                <button className="btnSmall" style={{ background: "var(--danger, #e53935)", color: "#fff", border: "none" }} onClick={() => { confirmDialog.onConfirm(); setConfirmDialog(null); }}>{t("common.confirm")}</button>
              </div>
            </div>
          </div>
        )}

        {/* Fixed Toast Notifications */}
        {(busy || notice || error) && (
          <div className="toastContainer">
            {busy && <div className="toast toastInfo">{busy}</div>}
            {notice && <div className="toast toastOk" onClick={() => setNotice(null)}>{notice}</div>}
            {error && <div className="toast toastError" onClick={() => setError(null)}>{error}</div>}
          </div>
        )}

        {view === "wizard" ? (() => {
          // Hide footer on welcome (mode selection), quick-form (has own start button), quick-setup (auto-running) and quick-finish (has own launch button)
          if (stepId === "welcome" || stepId === "quick-form" || stepId === "quick-setup" || stepId === "quick-finish") return null;
          const saveConfig = getFooterSaveConfig();
          return (
            <div className="footer">
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div className="statusLine">{t("config.configuring")}</div>
                {saveConfig && (
                  <>
                    <button className="btnPrimary"
                      onClick={() => renderIntegrationsSave(saveConfig.keys, saveConfig.savedMsg)}
                      disabled={!currentWorkspaceId || !!busy}>
                      {t("config.saveEnv")}
                    </button>
                    <button className="btnApplyRestart"
                      onClick={() => applyAndRestart(saveConfig.keys)}
                      disabled={!currentWorkspaceId || !!busy || !!restartOverlay}
                      title={t("config.applyRestartHint")}>
                      {t("config.applyRestart")}
                    </button>
                  </>
                )}
              </div>
              <div className="btnRow">
                <button onClick={goPrev} disabled={isFirst || !!busy}>{t("config.prev")}</button>
                {stepId === "finish" ? (
                  <button
                    className="btnPrimary"
                    onClick={async () => {
                      const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
                      if (!effectiveWsId) { setError(t("common.error")); return; }
                      setError(null);
                      setView("status");
                      await startLocalServiceWithConflictCheck(effectiveWsId);
                      try { await refreshServiceLog(effectiveWsId); } catch { /* ignore */ }
                    }}
                    disabled={!!busy}
                  >{t("config.finish")}</button>
                ) : (
                  <button className="btnPrimary" onClick={goNext} disabled={isLast || !!busy}>{t("config.next")}</button>
                )}
              </div>
            </div>
          );
        })() : null}
      </main>
    </div>
  );
}

