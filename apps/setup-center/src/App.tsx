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
  DotGreen, DotGray,
  IconBook, LogoTelegram, LogoFeishu, LogoWework, LogoDingtalk, LogoQQ,
} from "./icons";
import logoUrl from "./assets/logo.png";
import "highlight.js/styles/github.css";

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

type ListedModel = {
  id: string;
  name: string;
  capabilities: Record<string, boolean>;
};

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
  | "finish";

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
  const rootRef = useRef<HTMLDivElement | null>(null);
  const filtered = useMemo(() => {
    const q = (value || "").trim().toLowerCase();
    const list = q ? options.filter((x) => x.toLowerCase().includes(q)) : options;
    return list.slice(0, 200);
  }, [options, value]);

  useEffect(() => {
    if (hoverIdx >= filtered.length) setHoverIdx(0);
  }, [filtered.length, hoverIdx]);

  return (
    <div ref={rootRef} style={{ position: "relative", flex: "1 1 auto", minWidth: 520 }}>
      <div style={{ position: "relative" }}>
        <input
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
          }}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
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
                setOpen(false);
              }
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          disabled={disabled}
          style={{ paddingRight: 44 }}
        />
        <button
          type="button"
          className="btnSmall"
          onClick={() => setOpen((v) => !v)}
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
      </div>
      {open && !disabled ? (
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
                  setOpen(false);
                }}
                style={{
                  padding: "10px 12px",
                  cursor: "pointer",
                  fontWeight: 650,
                  background: idx === hoverIdx ? "rgba(14, 165, 233, 0.10)" : "transparent",
                  borderTop: idx === 0 ? "none" : "1px solid rgba(17,24,39,0.06)",
                }}
              >
                {opt}
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

  // Ensure boot overlay is removed once React actually mounts.
  useEffect(() => {
    try {
      document.getElementById("boot")?.remove();
      window.dispatchEvent(new Event("openakita_app_ready"));
    } catch {
      // ignore
    }
  }, []);

  const steps: Step[] = useMemo(
    () => [
      { id: "welcome", title: t("config.step.welcome"), desc: t("config.step.welcomeDesc") },
      { id: "workspace", title: t("config.step.workspace"), desc: t("config.step.workspaceDesc") },
      { id: "python", title: "Python", desc: t("config.step.pythonDesc") },
      { id: "install", title: t("config.step.install"), desc: t("config.step.installDesc") },
      { id: "llm", title: t("config.step.endpoints"), desc: t("config.step.endpointsDesc") },
      { id: "im", title: t("config.imTitle"), desc: t("config.step.imDesc") },
      { id: "tools", title: t("config.step.tools"), desc: t("config.step.toolsDesc") },
      { id: "agent", title: t("config.step.agent"), desc: t("config.step.agentDesc") },
      { id: "finish", title: t("config.step.finish"), desc: t("config.step.finishDesc") },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t],
  );

  const [view, setView] = useState<"wizard" | "status" | "chat" | "skills" | "im">("wizard");
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
  const [serviceStatus, setServiceStatus] = useState<{ running: boolean; pid: number | null; pidFile: string } | null>(null);
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
            // Check if endpoints exist
            const ws = await invoke<string | null>("get_current_workspace_id");
            if (ws) {
              const raw = await invoke<string>("workspace_read_file", { workspaceId: ws, relativePath: "data/llm_endpoints.json" });
              const parsed = JSON.parse(raw);
              const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
              if (!cancelled && eps.length > 0) {
                setSavedEndpoints(eps.map((e: any) => ({
                  name: String(e?.name || ""), provider: String(e?.provider || ""),
                  apiType: String(e?.api_type || ""), baseUrl: String(e?.base_url || ""),
                  model: String(e?.model || ""), apiKeyEnv: String(e?.api_key_env || ""),
                  priority: Number(e?.priority || 1),
                })));
              }
            }
          } catch { /* ignore */ }
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
    // integrations/finish are completion-oriented; keep manual.
    return d;
  }, [info, currentWorkspaceId, canUsePython, openakitaInstalled, savedEndpoints.length]);

  // 当 done 集合更新时，自动推进 maxReachedStepIdx
  // 核心步骤（welcome ~ llm）全完成后，解锁所有后续步骤（IM/工具/Agent/完成都是可选的）
  useEffect(() => {
    const coreSteps: StepId[] = ["welcome", "workspace", "python", "install", "llm"];
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
    let parsed: EnvMap;
    if (dataMode === "remote") {
      // Remote mode: fetch from HTTP API
      try {
        const res = await fetch(`${apiBaseUrl}/api/config/env`);
        const data = await res.json();
        parsed = data.env || {};
      } catch {
        parsed = {};
      }
    } else {
      // Local mode: read from workspace file
      const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
      parsed = parseEnv(content);
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
      const raw = await invoke<string>("openakita_list_providers", { venvDir });
      const parsed = JSON.parse(raw) as ProviderInfo[];
      setProviders(parsed);
      const first = parsed[0]?.slug ?? "";
      setProviderSlug((prev) => prev || first);
      setNotice(`已加载服务商：${parsed.length} 个`);
      try {
        const v = await invoke<string>("openakita_version", { venvDir });
        setOpenakitaVersion(v || "");
      } catch {
        setOpenakitaVersion("");
      }
      const slugs = new Set(parsed.map((p) => (p.slug || "").toLowerCase()));
      if (!slugs.has("kimi-cn") || !slugs.has("minimax-cn") || !slugs.has("deepseek")) {
        setNotice(
          `已加载服务商：${parsed.length} 个（但看起来 openakita 版本偏旧，缺少部分内置供应商）。建议回到“安装”用“本地源码/GitHub”重新安装 openakita，然后再回来刷新服务商列表。`,
        );
      }
    } catch (e) {
      setError(String(e));
      throw e;
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
      const raw = await invoke<string>("openakita_list_models", {
        venvDir,
        apiType,
        baseUrl,
        providerSlug: selectedProvider?.slug ?? null,
        apiKey: apiKeyValue,
      });
      const parsed = JSON.parse(raw) as ListedModel[];
      setModels(parsed);
      // 不要默认选中/填入任何模型，避免“自动出现一个搜索结果”造成误导
      setSelectedModelId("");
      setNotice(`拉取到模型：${parsed.length} 个`);
      setCapTouched(false);
    } finally {
      setBusy(null);
    }
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
    if (!currentWorkspaceId) {
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
    if (dataMode === "remote") {
      try {
        const res = await fetch(`${apiBaseUrl}/api/config/endpoints`);
        const data = await res.json();
        const eps = Array.isArray(data?.endpoints) ? data.endpoints : [];
        return { endpoints: eps, settings: data?.raw?.settings || {} };
      } catch {
        return { endpoints: [], settings: {} };
      }
    }
    if (!currentWorkspaceId) return { endpoints: [], settings: {} };
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
    if (dataMode === "remote") {
      // Read existing content from remote to preserve extra fields
      let existing: any = {};
      try {
        const res = await fetch(`${apiBaseUrl}/api/config/endpoints`);
        const data = await res.json();
        existing = data?.raw || {};
      } catch { /* ignore */ }
      const base = { ...existing, endpoints, settings: settings || {} };
      await fetch(`${apiBaseUrl}/api/config/endpoints`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: base }),
      });
      return;
    }
    if (!currentWorkspaceId) throw new Error("未设置当前工作区");
    let existing: any = {};
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      existing = raw ? JSON.parse(raw) : {};
    } catch { /* ignore */ }
    const base = { ...existing, endpoints, settings: settings || {} };
    const next = JSON.stringify(base, null, 2) + "\n";
    await writeWorkspaceFile("data/llm_endpoints.json", next);
  }

  // ── Generic file read/write with remote mode support ──
  async function readWorkspaceFile(relativePath: string): Promise<string> {
    if (dataMode === "remote") {
      // For known paths, use dedicated APIs
      if (relativePath === "data/llm_endpoints.json") {
        const res = await fetch(`${apiBaseUrl}/api/config/endpoints`);
        const data = await res.json();
        return JSON.stringify(data.raw || { endpoints: data.endpoints || [] });
      }
      if (relativePath === "data/skills.json") {
        const res = await fetch(`${apiBaseUrl}/api/config/skills`);
        const data = await res.json();
        return JSON.stringify(data.skills || {});
      }
      if (relativePath === ".env") {
        const res = await fetch(`${apiBaseUrl}/api/config/env`);
        const data = await res.json();
        return data.raw || "";
      }
      throw new Error(`Remote read not supported for: ${relativePath}`);
    }
    return invoke<string>("workspace_read_file", { workspaceId: currentWorkspaceId, relativePath });
  }

  async function writeWorkspaceFile(relativePath: string, content: string): Promise<void> {
    if (dataMode === "remote") {
      if (relativePath === "data/llm_endpoints.json") {
        await fetch(`${apiBaseUrl}/api/config/endpoints`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: JSON.parse(content) }),
        });
        return;
      }
      if (relativePath === "data/skills.json") {
        await fetch(`${apiBaseUrl}/api/config/skills`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: JSON.parse(content) }),
        });
        return;
      }
      throw new Error(`Remote write not supported for: ${relativePath}`);
    }
    await invoke("workspace_write_file", { workspaceId: currentWorkspaceId, relativePath, content });
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
      const raw = await invoke<string>("openakita_list_models", {
        venvDir,
        apiType: compilerApiType,
        baseUrl: compilerBaseUrl,
        providerSlug: compilerProviderSlug || null,
        apiKey: compilerApiKeyValue,
      });
      const parsed = JSON.parse(raw) as ListedModel[];
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
    if (!currentWorkspaceId) {
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
      // Write API key to .env
      await invoke("workspace_update_env", {
        workspaceId: currentWorkspaceId,
        entries: [{ key: compilerApiKeyEnv.trim(), value: compilerApiKeyValue.trim() }],
      });
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

      await invoke("workspace_write_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
        content: JSON.stringify(base, null, 2) + "\n",
      });

      // Reset form
      setCompilerModel("");
      setCompilerApiKeyValue("");
      setCompilerEndpointName("");
      setNotice(`编译端点 ${name} 已保存`);
      await loadSavedEndpoints();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function doDeleteCompilerEndpoint(epName: string) {
    if (!currentWorkspaceId) return;
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

      await invoke("workspace_write_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
        content: JSON.stringify(base, null, 2) + "\n",
      });
      setNotice(`编译端点 ${epName} 已删除`);
      await loadSavedEndpoints();
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
    setNotice(null);
  }

  function resetEndpointEditor() {
    setEditingOriginalName(null);
    setEditDraft(null);
    setEditModalOpen(false);
    setEditModels([]);
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
      const raw = await invoke<string>("openakita_list_models", {
        venvDir,
        apiType: editDraft.apiType,
        baseUrl: editDraft.baseUrl,
        providerSlug: editDraft.providerSlug || null,
        apiKey: key,
      });
      const parsed = JSON.parse(raw) as ListedModel[];
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
        await invoke("workspace_update_env", {
          workspaceId: currentWorkspaceId,
          entries: [{ key: editDraft.apiKeyEnv.trim(), value: editDraft.apiKeyValue.trim() }],
        });
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
  }, [stepId, currentWorkspaceId]);

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
      await invoke("workspace_update_env", {
        workspaceId: currentWorkspaceId,
        entries: [{ key: apiKeyEnv.trim(), value: apiKeyValue.trim() }],
      });

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
    if (!currentWorkspaceId) return;
    setError(null);
    setBusy("删除端点...");
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      const base = raw ? JSON.parse(raw) : { endpoints: [], settings: {} };
      const eps = Array.isArray(base.endpoints) ? base.endpoints : [];
      base.endpoints = eps.filter((e: any) => String(e?.name || "") !== name);
      const next = JSON.stringify(base, null, 2) + "\n";
      await writeWorkspaceFile("data/llm_endpoints.json", next);
      setNotice(`已删除端点：${name}`);
      await loadSavedEndpoints();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function saveEnvKeys(keys: string[]) {
    if (dataMode === "remote") {
      const entries: Record<string, string> = {};
      for (const k of keys) {
        if (Object.prototype.hasOwnProperty.call(envDraft, k)) {
          entries[k] = envDraft[k] ?? "";
        }
      }
      await fetch(`${apiBaseUrl}/api/config/env`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries }),
      });
      return;
    }
    if (!currentWorkspaceId) throw new Error("未设置当前工作区");
    await ensureEnvLoaded(currentWorkspaceId);
    const entries = keys
      .filter((k) => Object.prototype.hasOwnProperty.call(envDraft, k))
      .map((k) => ({ key: k, value: envDraft[k] ?? "" }));
    await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries });
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
          "QQ_ENABLED", "QQ_ONEBOT_URL",
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
          "WHISPER_MODEL", "GITHUB_TOKEN",
        ];
      case "agent":
        return [
          "AGENT_NAME", "MAX_ITERATIONS", "AUTO_CONFIRM",
          "THINKING_MODE", "FAST_MODEL",
          "PROGRESS_TIMEOUT_SECONDS", "HARD_TIMEOUT_SECONDS",
          "DATABASE_PATH", "LOG_LEVEL",
          "LOG_DIR", "LOG_FILE_PREFIX", "LOG_MAX_SIZE_MB", "LOG_BACKUP_COUNT",
          "LOG_RETENTION_DAYS", "LOG_FORMAT", "LOG_TO_CONSOLE", "LOG_TO_FILE",
          "EMBEDDING_MODEL", "EMBEDDING_DEVICE",
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

  function goPrev() {
    setNotice(null);
    setError(null);
    setStepId(steps[Math.max(currentStepIdx - 1, 0)].id);
  }

  // keep env draft in sync when workspace changes
  useEffect(() => {
    if (!currentWorkspaceId) return;
    ensureEnvLoaded(currentWorkspaceId).catch(() => {});
  }, [currentWorkspaceId]);

  async function refreshStatus(overrideDataMode?: "local" | "remote", overrideApiBaseUrl?: string) {
    const effectiveDataMode = overrideDataMode || dataMode;
    const effectiveApiBaseUrl = overrideApiBaseUrl || apiBaseUrl;
    if (!info && !serviceStatus?.running && effectiveDataMode !== "remote") return;
    setStatusLoading(true);
    setStatusError(null);
    try {
      // Verify the service is actually alive before trying HTTP API
      let serviceAlive = false;
      if (serviceStatus?.running || effectiveDataMode === "remote") {
        try {
          const ping = await fetch(`${effectiveApiBaseUrl}/api/health`, { signal: AbortSignal.timeout(3000) });
          serviceAlive = ping.ok;
          if (serviceAlive && effectiveDataMode === "remote") {
            // Ensure running state is set for remote mode
            setServiceStatus((prev) =>
              prev ? { ...prev, running: true } : { running: true, pid: null, pidFile: "" }
            );
          }
        } catch {
          // Service is not reachable
          serviceAlive = false;
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
          // Fall back to Tauri for skills
          if (currentWorkspaceId) {
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

        // Service status – only check local PID if NOT in remote mode
        if (effectiveDataMode !== "remote" && currentWorkspaceId) {
          try {
            const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", { workspaceId: currentWorkspaceId });
            setServiceStatus(ss);
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

      // Local mode: check PID-based service status
      // Remote mode: skip PID check — status is determined by HTTP health check
      if (effectiveDataMode !== "remote") {
        try {
          const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", {
            workspaceId: currentWorkspaceId,
          });
          setServiceStatus(ss);
        } catch {
          setServiceStatus(null);
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
    } catch (e) {
      setStatusError(String(e));
    } finally {
      setStatusLoading(false);
    }
  }

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
      const chunk = await invoke<{ path: string; content: string; truncated: boolean }>("openakita_service_log", {
        workspaceId,
        tailBytes: 60000,
      });
      setServiceLog(chunk);
      setServiceLogError(null);
    } catch (e) {
      setServiceLog(null);
      setServiceLogError(String(e));
    }
  }

  // 状态面板：服务运行时自动刷新日志
  useEffect(() => {
    if (view !== "status") return;
    if (!currentWorkspaceId) return;
    if (!serviceStatus?.running) return;
    let cancelled = false;
    void (async () => {
      if (!cancelled) await refreshServiceLog(currentWorkspaceId);
    })();
    const t = window.setInterval(() => {
      if (cancelled) return;
      void refreshServiceLog(currentWorkspaceId);
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [view, currentWorkspaceId, serviceStatus?.running]);

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
    if (!currentWorkspaceId) return;
    if (!!busy) return;
    if (skillsDetail) return;
    if (!openakitaInstalled) return;
    void doRefreshSkills();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, stepId, currentWorkspaceId, openakitaInstalled, skillsDetail]);

  async function doRefreshSkills() {
    if (!currentWorkspaceId) {
      setError("请先设置当前工作区");
      return;
    }
    setError(null);
    setBusy("读取 skills...");
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

  const doneCount = done.size;
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
      { k: "QQ_ENABLED", name: "QQ(OneBot)", required: ["QQ_ONEBOT_URL"] },
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
              {serviceStatus?.running ? <DotGreen /> : <DotGray />}
            </div>
            <div className="statusCardValue">
              {serviceStatus?.running ? t("topbar.running") : t("topbar.stopped")}
              {serviceStatus?.pid ? <span className="statusCardSub"> PID {serviceStatus.pid}</span> : null}
            </div>
            <div className="statusCardActions">
              {!serviceStatus?.running && effectiveWsId && (
                <button className="btnSmall btnSmallPrimary" onClick={async () => {
                  setBusy(t("topbar.starting")); setError(null);
                  try {
                    const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", { venvDir, workspaceId: effectiveWsId });
                    setServiceStatus(ss);
                    await new Promise((r) => setTimeout(r, 600));
                    const real = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", { workspaceId: effectiveWsId });
                    setServiceStatus(real);
                    if (real.running) await refreshStatus();
                  } catch (e) { setError(String(e)); } finally { setBusy(null); }
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
                    // Start
                    const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", { venvDir, workspaceId: effectiveWsId });
                    setServiceStatus(ss);
                    await new Promise((r) => setTimeout(r, 600));
                    const real = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", { workspaceId: effectiveWsId });
                    setServiceStatus(real);
                    if (real.running) await refreshStatus();
                  } catch (e) { setError(String(e)); } finally { setBusy(null); }
                }} disabled={!!busy}>{t("status.restart")}</button>
              </>)}
            </div>
          </div>

          {/* Workspace */}
          <div className="statusCard">
            <div className="statusCardHead">
              <span className="statusCardLabel">{t("config.step.workspace")}</span>
            </div>
            <div className="statusCardValue">{currentWorkspaceId || "—"}</div>
            <div className="statusCardSub">{ws?.path || ""}</div>
          </div>

          {/* Autostart */}
          <div className="statusCard">
            <div className="statusCardHead">
              <span className="statusCardLabel">{t("status.autostart")}</span>
              {autostartEnabled ? <DotGreen /> : <DotGray />}
            </div>
            <div className="statusCardValue">{autostartEnabled ? t("status.on") : t("status.off")}</div>
            <div className="statusCardActions">
              <button className="btnSmall" onClick={async () => {
                setBusy(t("common.loading")); setError(null);
                try { const next = !autostartEnabled; await invoke("autostart_set_enabled", { enabled: next }); setAutostartEnabled(next); } catch (e) { setError(String(e)); } finally { setBusy(null); }
              }} disabled={autostartEnabled === null || !!busy}>{autostartEnabled ? t("status.off") : t("status.on")}</button>
            </div>
          </div>
        </div>

        {/* LLM Endpoints compact table */}
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span className="statusCardLabel">{t("status.llmEndpoints")} ({endpointSummary.length})</span>
            <button className="btnSmall" onClick={async () => {
              setHealthChecking("all");
              try {
                let results: Array<{ name: string; status: string; latency_ms: number | null; error: string | null; error_category: string | null; consecutive_failures: number; cooldown_remaining: number; is_extended_cooldown: boolean; last_checked_at: string | null }>;
                // Always use HTTP API when service is running (bridge has no health-check command)
                const healthUrl = serviceStatus?.running ? apiBaseUrl : null;
                if (healthUrl) {
                  const res = await fetch(`${healthUrl}/api/health/check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
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
                        const healthUrl = serviceStatus?.running ? apiBaseUrl : null;
                        if (healthUrl) {
                          const res = await fetch(`${healthUrl}/api/health/check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint_name: e.name }) });
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
                  const healthUrl = serviceStatus?.running ? apiBaseUrl : null;
                  if (healthUrl) {
                    const res = await fetch(`${healthUrl}/api/im/channels`);
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
              <button className="btnSmall" onClick={() => { if (effectiveWsId) refreshServiceLog(effectiveWsId); }}>{t("topbar.refresh")}</button>
            </div>
            <pre className="logPre">{(serviceLog?.content || "").trim() || t("status.noLog")}</pre>
          </div>
        )}
      </>
    );
  }

  function renderWelcome() {
    const guideSteps = [
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

        {/* Step guide */}
        <div className="card" style={{ marginTop: 12 }}>
          <div className="cardTitle">{t("welcome.title")}</div>
          <div className="cardHint" style={{ marginBottom: 16 }}>{t("welcome.subtitle")}</div>
          <div className="welcomeSteps">
            {guideSteps.map((s, i) => (
              <div key={i} className="welcomeStepRow">
                <div className="welcomeStepNum">{s.icon}</div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{s.title}</div>
                  <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }}>{s.desc}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 20, textAlign: "center" }}>
            <button className="btnPrimary" onClick={goNext}>{t("welcome.begin")}</button>
          </div>
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
              {["all", "windows", "browser", "whisper", "feishu", "dingtalk", "wework", "qq"].map((x) => (
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
                    <button className="btnIcon btnIconDanger" onClick={() => doDeleteEndpoint(e.name)} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></button>
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
            {savedCompilerEndpoints.length < 2 && (
              <button className="btnSmall btnSmallPrimary" onClick={() => { doLoadProviders(); setAddCompDialogOpen(true); }} disabled={!!busy}>
                + {t("llm.addEndpoint")}
              </button>
            )}
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
                  <button className="btnIcon btnIconDanger" onClick={() => doDeleteCompilerEndpoint(e.name)} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></button>
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

              {/* Fetch models */}
              <div className="dialogSection">
                <button onClick={doFetchModels} className="btnPrimary" disabled={!apiKeyValue.trim() || !baseUrl.trim() || !!busy}
                  style={{ width: "100%", padding: "10px 16px", borderRadius: 8 }}>
                  {models.length > 0 ? t("llm.refetch") + ` (${models.length})` : t("llm.fetchModels")}
                </button>
              </div>

              {/* Select model (shown after fetch) */}
              {models.length > 0 && (
                <>
                  <div className="dialogSection">
                    <div className="dialogLabel">{t("llm.selectModel")}</div>
                    <SearchSelect
                      value={selectedModelId}
                      onChange={(v) => setSelectedModelId(v)}
                      options={models.map((m) => m.id)}
                      placeholder={t("llm.searchModel")}
                      disabled={!!busy}
                    />
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

                  {/* Footer */}
                  <div className="dialogFooter">
                    <button className="btnSmall" onClick={() => { setAddEpDialogOpen(false); resetEndpointEditor(); }}>{t("common.cancel")}</button>
                    <button className="btnPrimary" style={{ padding: "8px 20px", borderRadius: 8 }} onClick={async () => { await doSaveEndpoint(); setAddEpDialogOpen(false); }} disabled={!selectedModelId || !currentWorkspaceId || !!busy}>
                      {isEditingEndpoint ? t("common.save") : t("llm.addEndpoint")}
                    </button>
                  </div>
                </>
              )}
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
              <div className="dialogSection">
                <div className="dialogLabel">{t("status.model")}</div>
                <input value={editDraft.modelId || ""} onChange={(e) => setEditDraft({ ...editDraft, modelId: e.target.value })} />
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">Base URL</div>
                <input value={editDraft.baseUrl || ""} onChange={(e) => setEditDraft({ ...editDraft, baseUrl: e.target.value })} />
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">API Key</div>
                <input value={envDraft[editDraft.apiKeyEnv || ""] || ""} onChange={(e) => { const k = editDraft.apiKeyEnv || ""; setEnvDraft((m) => ({ ...m, [k]: e.target.value })); }} type="password" />
              </div>
              <div className="dialogFooter">
                <button className="btnSmall" onClick={() => resetEndpointEditor()}>{t("common.cancel")}</button>
                <button className="btnPrimary" style={{ padding: "8px 20px", borderRadius: 8 }} onClick={async () => { await doSaveEditedEndpoint(); }} disabled={!!busy}>{t("common.save")}</button>
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
              <div className="dialogSection">
                <div className="dialogLabel">{t("llm.provider")}</div>
                <select value={compilerProviderSlug} onChange={(e) => {
                  const slug = e.target.value;
                  setCompilerProviderSlug(slug);
                  const p = providers.find((x) => x.slug === slug);
                  if (p) {
                    setCompilerApiType((p.api_type as any) || "openai");
                    setCompilerBaseUrl(p.default_base_url || "");
                    const suggested = p.api_key_env_suggestion || envKeyFromSlug(p.slug);
                    const used = new Set(Object.keys(envDraft || {}));
                    for (const ep of [...savedEndpoints, ...savedCompilerEndpoints]) { if (ep.api_key_env) used.add(ep.api_key_env); }
                    setCompilerApiKeyEnv(nextEnvKeyName(suggested, used));
                  }
                }}>
                  <option value="">--</option>
                  {providers.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
                </select>
              </div>
              <div className="dialogSection">
                <div className="dialogLabel">API Key</div>
                <input value={compilerApiKeyValue} onChange={(e) => setCompilerApiKeyValue(e.target.value)} placeholder="sk-..." type="password" />
              </div>
              <div className="dialogSection">
                <button onClick={doFetchCompilerModels} className="btnPrimary" disabled={!compilerApiKeyValue.trim() || !compilerBaseUrl.trim() || !!busy}
                  style={{ width: "100%", padding: "10px 16px", borderRadius: 8 }}>
                  {compilerModels.length > 0 ? t("llm.refetch") + ` (${compilerModels.length})` : t("llm.fetchModels")}
                </button>
              </div>
              {compilerModels.length > 0 && (
                <>
                  <div className="dialogSection">
                    <div className="dialogLabel">{t("status.model")}</div>
                    <SearchSelect value={compilerModel} onChange={(v) => setCompilerModel(v)} options={compilerModels.map((m) => m.id)} placeholder="qwen-turbo / gpt-4o-mini" disabled={!!busy} />
                  </div>
                  <div className="dialogFooter">
                    <button className="btnSmall" onClick={() => setAddCompDialogOpen(false)}>{t("common.cancel")}</button>
                    <button className="btnPrimary" style={{ padding: "8px 20px", borderRadius: 8 }} onClick={async () => { await doSaveCompilerEndpoint(); setAddCompDialogOpen(false); }} disabled={!compilerModel.trim() || !compilerApiKeyEnv.trim() || !compilerApiKeyValue.trim() || !currentWorkspaceId || !!busy}>
                      {t("llm.addEndpoint")}
                    </button>
                  </div>
                </>
              )}
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

  function FieldBool({ k, label, help }: { k: string; label: string; help?: string }) {
    const v = envGet(envDraft, k, "false").toLowerCase() === "true";
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
      "QQ_ENABLED", "QQ_ONEBOT_URL",
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
        title: "QQ (OneBot)",
        appType: t("config.imTypeOneBot"),
        logo: <LogoQQ size={22} />,
        enabledKey: "QQ_ENABLED",
        docUrl: "https://github.com/botuniverse/onebot-11",
        needPublicIp: false,
        body: <FieldText k="QQ_ONEBOT_URL" label="OneBot WebSocket URL" placeholder="ws://127.0.0.1:8080" />,
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

          <div className="btnRow" style={{ marginTop: 14 }}>
            <button className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysIM, t("config.imSaved"))}
              disabled={!currentWorkspaceId || !!busy}>
              {t("config.imSave")}
            </button>
          </div>
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
      "WHISPER_MODEL", "GITHUB_TOKEN",
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

          {/* ── Network & Proxy ── */}
          <details className="configDetails">
            <summary>{t("config.toolsNetwork")}</summary>
            <div className="configDetailsBody">
              <div className="grid3">
                <FieldText k="HTTP_PROXY" label="HTTP_PROXY" placeholder="http://127.0.0.1:7890" />
                <FieldText k="HTTPS_PROXY" label="HTTPS_PROXY" placeholder="http://127.0.0.1:7890" />
                <FieldText k="ALL_PROXY" label="ALL_PROXY" placeholder="socks5://..." />
              </div>
              <div className="grid3">
                <FieldBool k="FORCE_IPV4" label={t("config.toolsForceIPv4")} help={t("config.toolsForceIPv4Help")} />
                <FieldText k="TOOL_MAX_PARALLEL" label={t("config.toolsParallel")} placeholder="1" help={t("config.toolsParallelHelp")} />
                <FieldText k="WHISPER_MODEL" label="Whisper" placeholder="base" help={t("config.toolsWhisperHelp")} />
              </div>
            </div>
          </details>

          {/* ── Other ── */}
          <details className="configDetails">
            <summary>{t("config.toolsOther")}</summary>
            <div className="configDetailsBody">
              <div className="grid2">
                <FieldText k="GITHUB_TOKEN" label="GITHUB_TOKEN" type="password" help={t("config.toolsGithubHelp")} />
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

          <div className="divider" />
          <div className="btnRow">
            <button className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysTools, t("config.toolsSaved"))}
              disabled={!currentWorkspaceId || !!busy}>
              {t("config.saveEnv")}
            </button>
          </div>
        </div>
      </>
    );
  }

  function renderAgentSystem() {
    const keysAgent = [
      "AGENT_NAME", "MAX_ITERATIONS", "AUTO_CONFIRM",
      "THINKING_MODE", "FAST_MODEL",
      "PROGRESS_TIMEOUT_SECONDS", "HARD_TIMEOUT_SECONDS",
      "DATABASE_PATH", "LOG_LEVEL", "LOG_DIR", "LOG_FILE_PREFIX",
      "LOG_MAX_SIZE_MB", "LOG_BACKUP_COUNT", "LOG_RETENTION_DAYS",
      "LOG_FORMAT", "LOG_TO_CONSOLE", "LOG_TO_FILE",
      "EMBEDDING_MODEL", "EMBEDDING_DEVICE",
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
            <FieldText k="THINKING_MODE" label={t("config.agentThinking")} placeholder="auto" help={t("config.agentThinkingHelp")} />
          </div>
          <div className="grid2" style={{ marginTop: 8 }}>
            <FieldText k="FAST_MODEL" label={t("config.agentFastModel")} placeholder="claude-sonnet-4-20250514" help={t("config.agentFastModelHelp")} />
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
            <FieldBool k="SCHEDULER_ENABLED" label={t("config.agentSchedulerEnable")} help={t("config.agentSchedulerEnableHelp")} />
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
                <FieldText k="LOG_LEVEL" label={t("config.agentLogLevel")} placeholder="INFO" />
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
              <div className="grid2">
                <FieldText k="EMBEDDING_MODEL" label={t("config.agentEmbedModel")} placeholder="shibing624/text2vec-base-chinese" />
                <FieldText k="EMBEDDING_DEVICE" label={t("config.agentEmbedDevice")} placeholder="cpu" />
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

          <div className="divider" />
          <div className="btnRow">
            <button className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysAgent, t("config.agentSaved"))}
              disabled={!currentWorkspaceId || !!busy}>
              {t("config.saveEnv")}
            </button>
          </div>
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
      "FAST_MODEL",
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
      // memory / embedding
      "EMBEDDING_MODEL",
      "EMBEDDING_DEVICE",
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
      "QQ_ENABLED",
      "QQ_ONEBOT_URL",
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
                title: "QQ（需要 openakita[qq] + NapCat/Lagrange）",
                enabledKey: "QQ_ENABLED",
                apply: "https://github.com/botuniverse/onebot-11",
                body: <FieldText k="QQ_ONEBOT_URL" label="OneBot WebSocket URL" placeholder="ws://127.0.0.1:8080" />,
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
              <FieldText k="WHISPER_MODEL" label="WHISPER_MODEL" placeholder="base" help="tiny/base/small/medium/large" />
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
                <FieldText k="THINKING_MODE" label="Thinking 模式" placeholder="auto" help="auto=自动判断 / always=始终思考 / never=从不思考" />
                <FieldText k="FAST_MODEL" label="快速模型（Thinking auto 时用）" placeholder="claude-sonnet-4-20250514" help="THINKING_MODE=auto 时，简单任务会切到此模型" />
                <FieldText k="DATABASE_PATH" label="数据库路径" placeholder="data/agent.db" />
                <FieldText k="LOG_LEVEL" label="日志级别" placeholder="INFO" help="DEBUG/INFO/WARNING/ERROR" />
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

          <div className="btnRow">
            <button
              className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysCore, "已写入工作区 .env（工具/IM/MCP/桌面/高级配置）")}
              disabled={!currentWorkspaceId || !!busy}
            >
              一键写入工作区 .env（全覆盖）
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

  // 保存 env keys 的辅助函数（供 SkillManager 使用）
  async function saveEnvKeysExternal(keys: string[]) {
    if (!currentWorkspaceId) return;
    const entries = keys
      .filter((k) => Object.prototype.hasOwnProperty.call(envDraft, k))
      .map((k) => ({ key: k, value: (envDraft[k] ?? "").trim() }));
    if (entries.length > 0) {
      await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries });
    }
  }

  function renderStepContent() {
    if (!info) return <div className="card">加载中...</div>;
    if (view === "status") return renderStatus();
    if (view === "chat") {
      return (
        <ChatView
          serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl}
          endpoints={chatEndpoints}
          onStartService={async () => {
            const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
            if (!effectiveWsId) {
              setError("未找到工作区（请先创建/选择一个工作区）");
              return;
            }
            setBusy("启动后台服务...");
            setError(null);
            try {
              const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", {
                venvDir,
                workspaceId: effectiveWsId,
              });
              setServiceStatus(ss);
              await new Promise((r) => setTimeout(r, 600));
              const real = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", {
                workspaceId: effectiveWsId,
              });
              setServiceStatus(real);
              if (!real.running) {
                setError("后台服务未能保持运行。请先完成安装向导。");
              } else {
                await refreshStatus();
              }
            } catch (e) {
              setError(String(e));
            } finally {
              setBusy(null);
            }
          }}
        />
      );
    }
    if (view === "skills") {
      return (
        <SkillManager
          venvDir={venvDir}
          currentWorkspaceId={currentWorkspaceId}
          envDraft={envDraft}
          onEnvChange={setEnvDraft}
          onSaveEnvKeys={saveEnvKeysExternal}
        />
      );
    }
    if (view === "im") {
      return <IMView serviceRunning={serviceStatus?.running ?? false} />;
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
      default:
        return renderWelcome();
    }
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
          <div className={`navItem ${view === "status" ? "navItemActive" : ""}`} onClick={async () => { setView("status"); try { await refreshStatus(); } catch { /* ignore */ } }} role="button" tabIndex={0} title={t("sidebar.status")}>
            <IconStatus size={16} /> {!sidebarCollapsed && <span>{t("sidebar.status")}</span>}
          </div>
        </div>

        {/* Collapsible Config section */}
        <div className="configSection">
          <div className="configHeader" onClick={() => { if (sidebarCollapsed) { setView("wizard"); } else { setConfigExpanded((v) => !v); } }} role="button" tabIndex={0} title={t("sidebar.config")}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <IconConfig size={16} />
              {!sidebarCollapsed && <span>{t("sidebar.config")}</span>}
            </div>
            {!sidebarCollapsed && (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className="configProgress">{t("sidebar.configProgress", { done: doneCount, total: totalSteps })}</span>
                {configExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
              </div>
            )}
          </div>
          {!sidebarCollapsed && configExpanded && (
            <div className="stepList">
              {steps.map((s, idx) => {
                const isActive = view === "wizard" && s.id === stepId;
                const isDone = done.has(s.id);
                const canJump = idx <= maxReachedStepIdx || isDone;
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
                    setBusy(t("topbar.starting"));
                    setError(null);
                    try {
                      setDataMode("local");
                      setApiBaseUrl("http://127.0.0.1:18900");
                      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", { venvDir, workspaceId: effectiveWsId });
                      setServiceStatus(ss);
                      await new Promise((r) => setTimeout(r, 600));
                      const real = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", { workspaceId: effectiveWsId });
                      setServiceStatus(real);
                      if (real.running) { await refreshStatus(); }
                      else { setError(t("topbar.startFail")); }
                    } catch (e) { setError(String(e)); } finally { setBusy(null); }
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

        <div className={view === "chat" ? "contentChat" : "content"}>
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
                      await refreshStatus("remote", url);
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

        {/* Fixed Toast Notifications */}
        {(busy || notice || error) && (
          <div className="toastContainer">
            {busy && <div className="toast toastInfo">{busy}</div>}
            {notice && <div className="toast toastOk" onClick={() => setNotice(null)}>{notice}</div>}
            {error && <div className="toast toastError" onClick={() => setError(null)}>{error}</div>}
          </div>
        )}

        {view === "wizard" ? (
          <div className="footer">
            <div className="statusLine">{t("config.configuring")}</div>
            <div className="btnRow">
              <button onClick={goPrev} disabled={isFirst || !!busy}>{t("config.prev")}</button>
              {stepId === "finish" ? (
                <button
                  className="btnPrimary"
                  onClick={async () => {
                    const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
                    if (!effectiveWsId) { setError(t("common.error")); return; }
                    setBusy(t("common.loading"));
                    setError(null);
                    setView("status");
                    try {
                      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", {
                        venvDir,
                        workspaceId: effectiveWsId,
                      });
                      setServiceStatus(ss);
                      await new Promise((r) => setTimeout(r, 600));
                      const real = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", {
                        workspaceId: effectiveWsId,
                      });
                      setServiceStatus(real);
                      await refreshStatus();
                      await refreshServiceLog(effectiveWsId);
                    } catch (e) {
                      setError(String(e));
                      try { await refreshStatus(); await refreshServiceLog(effectiveWsId); } catch { /* ignore */ }
                    } finally { setBusy(null); }
                  }}
                  disabled={!!busy}
                >{t("config.finish")}</button>
              ) : (
                <button className="btnPrimary" onClick={goNext} disabled={isLast || !!busy}>{t("config.next")}</button>
              )}
            </div>
          </div>
        ) : null}
      </main>
    </div>
  );
}

