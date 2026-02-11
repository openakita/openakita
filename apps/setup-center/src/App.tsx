import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getVersion } from "@tauri-apps/api/app";

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
  const [info, setInfo] = useState<PlatformInfo | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
      {
        id: "welcome",
        title: "开始",
        desc: "确认环境与整体流程",
      },
      {
        id: "workspace",
        title: "工作区",
        desc: "创建/选择配置隔离空间",
      },
      {
        id: "python",
        title: "Python",
        desc: "内置 Python 或系统 Python",
      },
      {
        id: "install",
        title: "安装",
        desc: "venv + pip 安装 openakita",
      },
      {
        id: "llm",
        title: "LLM 端点",
        desc: "拉取模型列表并写入端点",
      },
      {
        id: "im",
        title: "IM 通道",
        desc: "启用并配置 Telegram/飞书/企业微信/钉钉/QQ",
      },
      {
        id: "tools",
        title: "工具与技能",
        desc: "Skills / MCP / 桌面自动化 / 代理等",
      },
      {
        id: "agent",
        title: "Agent 与系统",
        desc: "记忆 / 会话 / 调度 / 多 Agent",
      },
      {
        id: "finish",
        title: "完成",
        desc: "下一步引导与检查清单",
      },
    ],
    [],
  );

  const [view, setView] = useState<"wizard" | "status">("wizard");
  const [stepId, setStepId] = useState<StepId>("welcome");
  const currentStepIdxRaw = useMemo(() => steps.findIndex((s) => s.id === stepId), [steps, stepId]);
  const currentStepIdx = currentStepIdxRaw < 0 ? 0 : currentStepIdxRaw;
  const isFirst = currentStepIdx <= 0;
  const isLast = currentStepIdx >= steps.length - 1;

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

  // unified env draft (full coverage)
  const [envDraft, setEnvDraft] = useState<EnvMap>({});
  const envLoadedForWs = useRef<string | null>(null);

  const pretty = useMemo(() => {
    if (!info) return "";
    return [
      `OS: ${info.os}`,
      `Arch: ${info.arch}`,
      `Home: ${info.homeDir}`,
      `OpenAkita Root: ${info.openakitaRootDir}`,
    ].join("\n");
  }, [info]);

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
            // Default PyPI version to match Setup Center version
            setSelectedPypiVersion(v);
          }
        } catch {
          // ignore
        }
        await refreshAll();
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

  // Keep boolean flags in sync with the visible status string (best-effort).
  useEffect(() => {
    if (!venvStatus) return;
    if (venvStatus.includes("venv 就绪")) setVenvReady(true);
    if (venvStatus.includes("安装完成")) setOpenakitaInstalled(true);
  }, [venvStatus]);

  async function ensureEnvLoaded(workspaceId: string) {
    if (envLoadedForWs.current === workspaceId) return;
    const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
    const parsed = parseEnv(content);
    setEnvDraft(parsed);
    envLoadedForWs.current = workspaceId;
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
      const raw = await invoke<string>("workspace_read_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
      });
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
    if (!currentWorkspaceId) return { endpoints: [], settings: {} };
    try {
      const raw = await invoke<string>("workspace_read_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
      });
      const parsed = raw ? JSON.parse(raw) : { endpoints: [], settings: {} };
      const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
      const settings = parsed?.settings && typeof parsed.settings === "object" ? parsed.settings : {};
      return { endpoints: eps, settings };
    } catch {
      return { endpoints: [], settings: {} };
    }
  }

  async function writeEndpointsJson(endpoints: any[], settings: any) {
    if (!currentWorkspaceId) throw new Error("未设置当前工作区");
    // Read existing JSON to preserve extra top-level fields (e.g. compiler_endpoints)
    let existing: any = {};
    try {
      const raw = await invoke<string>("workspace_read_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
      });
      existing = raw ? JSON.parse(raw) : {};
    } catch { /* ignore */ }
    const base = { ...existing, endpoints, settings: settings || {} };
    const next = JSON.stringify(base, null, 2) + "\n";
    await invoke("workspace_write_file", {
      workspaceId: currentWorkspaceId,
      relativePath: "data/llm_endpoints.json",
      content: next,
    });
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
        currentJson = await invoke<string>("workspace_read_file", {
          workspaceId: currentWorkspaceId,
          relativePath: "data/llm_endpoints.json",
        });
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
        currentJson = await invoke<string>("workspace_read_file", {
          workspaceId: currentWorkspaceId,
          relativePath: "data/llm_endpoints.json",
        });
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
        currentJson = await invoke<string>("workspace_read_file", {
          workspaceId: currentWorkspaceId,
          relativePath: "data/llm_endpoints.json",
        });
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

      await invoke("workspace_write_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
        content: next,
      });

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
      const raw = await invoke<string>("workspace_read_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
      });
      const base = raw ? JSON.parse(raw) : { endpoints: [], settings: {} };
      const eps = Array.isArray(base.endpoints) ? base.endpoints : [];
      base.endpoints = eps.filter((e: any) => String(e?.name || "") !== name);
      const next = JSON.stringify(base, null, 2) + "\n";
      await invoke("workspace_write_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
        content: next,
      });
      setNotice(`已删除端点：${name}`);
      await loadSavedEndpoints();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function saveEnvKeys(keys: string[]) {
    if (!currentWorkspaceId) throw new Error("未设置当前工作区");
    await ensureEnvLoaded(currentWorkspaceId);
    // 只写入“已存在于 envDraft 的键”（即：用户实际填写/修改过，或工作区 .env 里原本就有）。
    // 这样可以避免把大量未填写的可选字段写成 KEY= 空值，污染 .env 并导致类型解析报错。
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
    if (stepId === "llm" && (!selectedModelId || models.length === 0)) {
      // 已有端点时，不硬拦截：改为弹窗提醒用户选择“新增端点”或“继续下一步”
      if (savedEndpoints.length > 0) {
        setLlmNextModalOpen(true);
        return;
      }
    }
    setStepId(steps[Math.min(currentStepIdx + 1, steps.length - 1)].id);
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

  async function refreshStatus() {
    if (!info) return;
    setStatusLoading(true);
    setStatusError(null);
    try {
      if (!currentWorkspaceId) {
        setEndpointSummary([]);
        setSkillSummary(null);
        setSkillsDetail(null);
        return;
      }
      await ensureEnvLoaded(currentWorkspaceId);

      // endpoints
      const raw = await invoke<string>("workspace_read_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
      });
      const parsed = JSON.parse(raw);
      const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
      const env = envDraft;
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

      try {
        const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", {
          workspaceId: currentWorkspaceId,
        });
        setServiceStatus(ss);
      } catch {
        setServiceStatus(null);
      }
    } catch (e) {
      setStatusError(String(e));
    } finally {
      setStatusLoading(false);
    }
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

      await invoke("workspace_write_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/skills.json",
        content,
      });
      setSkillsTouched(false);
      setNotice("已保存：data/skills.json（系统技能默认启用；外部技能按你的选择启用）");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  const headerRight = (
    <div className="row">
      {appVersion ? <span className="pill">Setup Center：<b>v{appVersion}</b></span> : null}
      {openakitaVersion ? <span className="pill">openakita：<b>v{openakitaVersion}</b></span> : null}
      <span className="pill">
        当前工作区：<b>{currentWorkspaceId || "未设置"}</b>
      </span>
      <span className="pill">
        venv：<span>{venvDir || "—"}</span>
      </span>
      <button
        onClick={async () => {
          setView("wizard");
          setStepId("welcome");
        }}
        disabled={!!busy}
      >
        安装向导
      </button>
      <button
        onClick={async () => {
          setView("status");
          try {
            await refreshStatus();
          } catch {
            // ignore
          }
        }}
        disabled={!!busy}
      >
        状态面板
      </button>
      <button onClick={() => refreshAll()} disabled={!!busy}>
        刷新
      </button>
    </div>
  );

  const StepDot = ({ idx, isDone }: { idx: number; isDone: boolean }) => (
    <div className={`stepDot ${isDone ? "stepDotDone" : ""}`}>{isDone ? "✓" : idx + 1}</div>
  );

  function renderStatus() {
    const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
    const ws = workspaces.find((w) => w.id === effectiveWsId) || workspaces[0] || null;
    const im = [
      { k: "TELEGRAM_ENABLED", name: "Telegram", required: ["TELEGRAM_BOT_TOKEN"] },
      { k: "FEISHU_ENABLED", name: "飞书", required: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"] },
      { k: "WEWORK_ENABLED", name: "企业微信", required: ["WEWORK_CORP_ID", "WEWORK_AGENT_ID", "WEWORK_SECRET", "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY"] },
      { k: "DINGTALK_ENABLED", name: "钉钉", required: ["DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"] },
      { k: "QQ_ENABLED", name: "QQ(OneBot)", required: ["QQ_ONEBOT_URL"] },
    ];
    const imStatus = im.map((c) => {
      const enabled = envGet(envDraft, c.k, "false").toLowerCase() === "true";
      const missing = c.required.filter((rk) => !(envGet(envDraft, rk) || "").trim());
      return { ...c, enabled, ok: enabled ? missing.length === 0 : true, missing };
    });

    const openakitaLooksInstalled = !!skillSummary; // best-effort signal

    return (
      <>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <div className="cardTitle">运行状态面板</div>
              <div className="cardHint">
                从托盘/菜单栏点击图标，会默认打开这里。后续会补齐：进程心跳、日志、端点连通性测试与告警。
              </div>
            </div>
            <div className="btnRow">
              <button className="btnPrimary" onClick={refreshStatus} disabled={statusLoading || !!busy}>
                刷新状态
              </button>
              <button onClick={() => setStepId("welcome")} disabled={!!busy}>
                继续向导
              </button>
            </div>
          </div>

          {statusError ? <div className="errorBox">{statusError}</div> : null}
          {statusLoading ? <div className="okBox">正在刷新状态...</div> : null}

          <div className="divider" />
          <div className="card">
            <div className="label">常驻与自启动</div>
            <div className="cardHint" style={{ marginTop: 8 }}>
              - 关闭窗口默认隐藏到托盘/菜单栏（从托盘菜单“退出”才会真正退出）
              <br />
              - 自启动用于“开机自动运行 Setup Center（托盘常驻）”，适合作为运行监控面板
            </div>
            <div className="divider" />
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div>
                <div style={{ fontWeight: 800 }}>开机自启动</div>
                <div className="help">Windows: 启动项；macOS: LaunchAgent</div>
              </div>
              <button
                className="btnPrimary"
                disabled={autostartEnabled === null || !!busy}
                onClick={async () => {
                  setBusy("更新自启动配置...");
                  setError(null);
                  try {
                    const next = !(autostartEnabled ?? false);
                    await invoke("autostart_set_enabled", { enabled: next });
                    setAutostartEnabled(next);
                    setNotice(next ? "已启用开机自启动" : "已关闭开机自启动");
                  } catch (e) {
                    setError(String(e));
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                {autostartEnabled ? "关闭自启动" : "开启自启动"}
              </button>
            </div>
            {autostartEnabled === null ? <div className="cardHint">自启动状态未知（可能是权限/平台限制或尚未初始化）。</div> : null}
          </div>

          <div className="divider" />
          <div className="card">
            <div className="label">后台服务（OpenAkita Serve）</div>
            <div className="cardHint" style={{ marginTop: 8 }}>
              这是“关闭终端仍常驻”的关键能力之一：由 Setup Center 在后台启动 `openakita serve`，用于长期跑 IM 通道/后台处理。
              <br />
              CLI 用户也可使用：`openakita daemon start --workspace-dir "${ws?.path || ""}"`
            </div>
            <div className="divider" />
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div className="cardHint">
                状态：
                <b>
                  {" "}
                  {serviceStatus
                    ? serviceStatus.running
                      ? `运行中 PID=${serviceStatus.pid ?? "?"}`
                      : "未运行"
                    : "未知"}
                </b>
                <br />
                <span className="help">pid 文件：{serviceStatus?.pidFile || "—"}</span>
              </div>
              <div className="btnRow">
                <button
                  className="btnPrimary"
                  disabled={!effectiveWsId || !!busy || !!serviceStatus?.running}
                  onClick={async () => {
                    if (!effectiveWsId) return;
                    setBusy("启动后台服务...");
                    setError(null);
                    try {
                      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", {
                        venvDir,
                        workspaceId: effectiveWsId,
                      });
                      setServiceStatus(ss);
                      setNotice("后台服务已启动（openakita serve）");
                      // 立即刷新一次全量状态，避免“已启动但状态没更新/按钮还可点”
                      try {
                        await refreshStatus();
                      } catch {
                        // ignore
                      }
                      void refreshServiceLog(effectiveWsId);
                    } catch (e) {
                      setError(String(e));
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  {serviceStatus?.running ? "已启动" : "启动服务"}
                </button>
                <button
                  className="btnDanger"
                  disabled={!effectiveWsId || !!busy || !serviceStatus?.running}
                  onClick={async () => {
                    if (!effectiveWsId) return;
                    setBusy("停止后台服务...");
                    setError(null);
                    try {
                      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_stop", {
                        workspaceId: effectiveWsId,
                      });
                      setServiceStatus(ss);
                      setNotice("已请求停止后台服务");
                    } catch (e) {
                      setError(String(e));
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  停止服务
                </button>
              </div>
            </div>

            <div className="divider" />
            <div className="label">服务日志（openakita-serve.log）</div>
            <div className="cardHint" style={{ marginTop: 8 }}>
              自动更新（每 2 秒）。仅展示末尾内容，避免日志过大导致卡顿。
            </div>
            <div className="btnRow" style={{ justifyContent: "flex-start", marginTop: 10 }}>
              <button
                onClick={() => {
                  if (!effectiveWsId) return;
                  void refreshServiceLog(effectiveWsId);
                }}
                disabled={!effectiveWsId || !!busy}
              >
                手动刷新日志
              </button>
            </div>
            {serviceLogError ? <div className="errorBox">{serviceLogError}</div> : null}
            <pre
              style={{
                marginTop: 10,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 12,
                padding: 12,
                border: "1px solid var(--line)",
                borderRadius: 12,
                background: "rgba(255,255,255,0.7)",
                maxHeight: 260,
                overflow: "auto",
              }}
            >
              {(serviceLog?.content || "").trim() || "（暂无日志）"}
            </pre>
            {serviceLog?.path ? <div className="help">路径：{serviceLog.path}{serviceLog.truncated ? "（已截断）" : ""}</div> : null}
          </div>

          <div className="divider" />
          <div className="grid2">
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">工作区</div>
              <div className="cardHint" style={{ marginTop: 8 }}>
                当前：<b>{currentWorkspaceId || "未设置"}</b>
                <br />
                路径：<b>{ws?.path || "—"}</b>
              </div>
            </div>
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">运行环境</div>
              <div className="cardHint" style={{ marginTop: 8 }}>
                venv：<b>{venvDir || "—"}</b>
                <br />
                openakita：<b>{openakitaLooksInstalled ? "已安装（可读取 skills）" : "未确认（先完成安装）"}</b>
              </div>
            </div>
          </div>

          <div className="divider" />
          <div className="card">
            <div className="label">LLM 端点</div>
            <div className="cardHint" style={{ marginTop: 8 }}>
              共 <b>{endpointSummary.length}</b> 个端点（keyPresent 仅检查工作区 `.env` 是否填了对应 `api_key_env`）
            </div>
            <div className="divider" />
            {endpointSummary.length === 0 ? (
              <div className="cardHint">未读取到端点。请先在“LLM 端点”步骤写入端点配置。</div>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                {endpointSummary.slice(0, 8).map((e) => (
                  <div key={e.name} className="card" style={{ marginTop: 0 }}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <div style={{ fontWeight: 800 }}>{e.name}</div>
                      <div
                        className="pill"
                        style={{
                          borderColor: e.keyPresent ? "rgba(16,185,129,0.25)" : "rgba(255,77,109,0.22)",
                        }}
                      >
                        {e.keyPresent ? "Key 已配置" : "Key 缺失"}
                      </div>
                    </div>
                    <div className="help" style={{ marginTop: 6 }}>
                      {e.provider} / {e.apiType} / {e.model}
                      <br />
                      {e.baseUrl}
                    </div>
                  </div>
                ))}
                {endpointSummary.length > 8 ? <div className="help">… 还有 {endpointSummary.length - 8} 个端点</div> : null}
              </div>
            )}
          </div>

          <div className="divider" />
          <div className="grid2">
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">IM 通道</div>
              <div className="divider" />
              <div style={{ display: "grid", gap: 8 }}>
                {imStatus.map((c) => (
                  <div key={c.k} className="row" style={{ justifyContent: "space-between" }}>
                    <div style={{ fontWeight: 700 }}>{c.name}</div>
                    <div className="help">
                      {c.enabled ? (c.ok ? "✅ 已配置" : `⚠ 缺少：${c.missing.join(", ")}`) : "— 未启用"}
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">Skills</div>
              <div className="divider" />
              {skillSummary ? (
                <div className="cardHint">
                  共 <b>{skillSummary.count}</b> 个技能
                  <br />
                  系统技能：<b>{skillSummary.systemCount}</b>
                  <br />
                  外部技能：<b>{skillSummary.externalCount}</b>
                </div>
              ) : (
                <div className="cardHint">未能读取 skills（通常是 venv 未安装 openakita 或环境未就绪）。</div>
              )}
            </div>
          </div>
        </div>
      </>
    );
  }

  function renderWelcome() {
    return (
      <>
        <div className="card">
          <div className="cardTitle">OpenAkita Setup Center</div>
          <div className="cardHint">
            这是一个“逐步向导”。左侧是步骤列表，右侧是当前步骤。每一步都会告诉你下一步该做什么，并在必要时阻止你跳过关键环节。
          </div>
          <div className="divider" />
          <div className="grid2">
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">平台信息</div>
              <pre style={{ margin: "8px 0 0 0", color: "var(--text)" }}>{pretty}</pre>
            </div>
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">你将完成什么</div>
              <div className="cardHint" style={{ marginTop: 8 }}>
                - 创建工作区（配置隔离）<br />
                - 准备 Python（内置/系统）→ 创建 venv → 安装 openakita
                <br />
                - 选择服务商/端点 → 自动拉取模型列表 → 写入端点配置
                <br />- 外部工具/IM/MCP/桌面自动化等开关与配置（全覆盖写入 .env）
              </div>
            </div>
          </div>
          <div className="okBox">
            建议从左侧第 2 步“工作区”开始。每个工作区都会在 `~/.openakita/workspaces/&lt;id&gt;` 下生成独立配置文件。
          </div>
        </div>
      </>
    );
  }

  function renderWorkspace() {
    return (
      <>
        <div className="card">
          <div className="cardTitle">工作区（配置隔离）</div>
          <div className="cardHint">
            工作区会生成并维护：`.env`、`data/llm_endpoints.json`、`identity/SOUL.md`。你可以为“生产/测试/不同客户”分别建立工作区。
          </div>
          <div className="divider" />
          <div className="row">
            <div className="field" style={{ minWidth: 320, flex: "1 1 auto" }}>
              <div className="labelRow">
                <div className="label">工作区名称</div>
                <div className="help">会自动生成 id（可作为文件夹名）</div>
              </div>
              <input value={newWsName} onChange={(e) => setNewWsName(e.target.value)} placeholder="例如：生产 / 测试 / 客户A" />
              <div className="help">
                生成的 id：<b>{newWsId}</b>
              </div>
            </div>
            <button className="btnPrimary" onClick={doCreateWorkspace} disabled={!!busy || !newWsName.trim()}>
              新建并设为当前
            </button>
          </div>
        </div>

        <div className="card">
          <div className="cardTitle">已有工作区</div>
          {workspaces.length === 0 ? (
            <div className="cardHint">当前还没有工作区。建议先创建一个。</div>
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
                        {w.isCurrent ? <span style={{ marginLeft: 8, color: "var(--brand)" }}>当前</span> : null}
                      </div>
                      <div className="help" style={{ marginTop: 6 }}>
                        {w.path}
                      </div>
                    </div>
                    <div className="btnRow">
                      <button onClick={() => doSetCurrentWorkspace(w.id)} disabled={!!busy || w.isCurrent}>
                        设为当前
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
          <div className="cardTitle">Python（选择一种即可）</div>
          <div className="cardHint">
            推荐使用内置 Python：不依赖系统环境，便于后续打包与“一键安装”。如果你已经有 Python 3.11+，也可以直接检测系统 Python。
          </div>
          <div className="divider" />
          <div className="btnRow">
            <button className="btnPrimary" onClick={doInstallEmbeddedPython} disabled={!!busy}>
              安装内置 Python（推荐）
            </button>
            <button onClick={doDetectPython} disabled={!!busy}>
              检测系统 Python（3.11+）
            </button>
          </div>
          {pythonCandidates.length > 0 ? (
            <div style={{ marginTop: 12 }}>
              <div className="field">
                <div className="labelRow">
                  <div className="label">选择 Python</div>
                  <div className="help">后续将用这个 Python 创建 venv</div>
                </div>
                <select value={selectedPythonIdx} onChange={(e) => setSelectedPythonIdx(Number(e.target.value))}>
                  <option value={-1}>（未选择）</option>
                  {pythonCandidates.map((c, idx) => (
                    <option key={idx} value={idx}>
                      {c.isUsable ? "✅" : "❌"} {c.command.join(" ")} — {c.versionText}
                    </option>
                  ))}
                </select>
              </div>
              {venvStatus ? <div className="okBox">{venvStatus}</div> : null}
            </div>
          ) : null}
          <div className="okBox">下一步：进入“安装”，创建 venv 并安装 openakita。</div>
        </div>
      </>
    );
  }

  function renderInstall() {
    const venvPath = venvDir;
    const installReadyText = openakitaInstalled
      ? "已安装完成：可以进入下一步（LLM 端点）"
      : venvReady
        ? "venv 就绪：可以开始安装 openakita"
        : "准备创建 venv 并安装 openakita";
    return (
      <>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <div>
              <div className="cardTitle">安装 openakita（venv + pip）</div>
              <div className="cardHint">
                这一步会在固定目录创建 venv：`~/.openakita/venv`，并安装 `openakita[extras]`。
                <br />
                <span className="help">提示：尽量只改你需要的参数，其他保持默认即可。</span>
              </div>
            </div>
            <div className="pill" style={{ alignItems: "center", gap: 8 }}>
              <span className="help">venv</span>
              <span style={{ fontWeight: 800, color: "var(--text)" }}>{venvPath}</span>
            </div>
          </div>
          <div className="divider" />

          <div className="card" style={{ marginTop: 0 }}>
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              1) 安装来源
            </div>
            <div className="cardHint">
              说明：默认从 PyPI 安装；如果提示“缺少 Setup Center 所需模块”，再切到 GitHub 或 本地源码。
            </div>
            <div className="btnRow" style={{ marginTop: 10, flexWrap: "wrap" }}>
              <button className={installSource === "pypi" ? "btnPrimary" : ""} onClick={() => setInstallSource("pypi")} disabled={!!busy}>
                PyPI / 镜像
              </button>
              <button className={installSource === "github" ? "btnPrimary" : ""} onClick={() => setInstallSource("github")} disabled={!!busy}>
                GitHub
              </button>
              <button className={installSource === "local" ? "btnPrimary" : ""} onClick={() => setInstallSource("local")} disabled={!!busy}>
                本地源码
              </button>
            </div>

          {installSource === "pypi" ? (
            <div style={{ marginTop: 10 }}>
              <div className="field">
                <div className="labelRow">
                  <div className="label">指定版本</div>
                  <div className="help">
                    建议选择与 Setup Center 同版本（<b>v{appVersion || "?"}</b>），以保证兼容性
                  </div>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <button
                    className="btnSmall"
                    onClick={doFetchPypiVersions}
                    disabled={!!busy || pypiVersionsLoading}
                    style={{ whiteSpace: "nowrap", borderRadius: 999 }}
                  >
                    {pypiVersionsLoading ? "获取中..." : "获取版本列表"}
                  </button>
                  {pypiVersions.length > 0 ? (
                    <select
                      value={selectedPypiVersion}
                      onChange={(e) => setSelectedPypiVersion(e.target.value)}
                      disabled={!!busy}
                      style={{ flex: "1 1 auto", minWidth: 180 }}
                    >
                      {pypiVersions.map((v) => (
                        <option key={v} value={v}>
                          {v}{v === appVersion ? "（推荐 · 与 Setup Center 同版本）" : v === pypiVersions[0] ? "（最新）" : ""}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={selectedPypiVersion}
                      onChange={(e) => setSelectedPypiVersion(e.target.value)}
                      placeholder={appVersion ? `默认 ${appVersion}（同 Setup Center 版本），留空安装最新` : "留空安装最新版本，或输入版本号如 1.2.13"}
                      disabled={!!busy}
                      style={{ flex: "1 1 auto" }}
                    />
                  )}
                </div>
                {selectedPypiVersion && appVersion && selectedPypiVersion !== appVersion ? (
                  <div className="help" style={{ marginTop: 6, color: "#e67e22", fontWeight: 700 }}>
                    注意：当前选择 v{selectedPypiVersion}，与 Setup Center（v{appVersion}）版本不一致，可能存在兼容性差异
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {installSource === "github" ? (
            <div className="grid2" style={{ marginTop: 10 }}>
              <div className="field">
                <div className="labelRow">
                  <div className="label">GitHub 仓库</div>
                  <div className="help">格式：owner/repo</div>
                </div>
                <input value={githubRepo} onChange={(e) => setGithubRepo(e.target.value)} placeholder="openakita/openakita" />
              </div>
              <div className="field">
                <div className="labelRow">
                  <div className="label">分支/Tag</div>
                  <div className="help">默认 main</div>
                </div>
                <div className="row">
                  <select value={githubRefType} onChange={(e) => setGithubRefType(e.target.value as any)} style={{ width: 140 }}>
                    <option value="branch">branch</option>
                    <option value="tag">tag</option>
                  </select>
                  <input value={githubRef} onChange={(e) => setGithubRef(e.target.value)} placeholder="main / v1.2.7 ..." />
                </div>
              </div>
            </div>
          ) : null}

          {installSource === "local" ? (
            <div className="field" style={{ marginTop: 10 }}>
              <div className="labelRow">
                <div className="label">本地源码路径</div>
                <div className="help">例如：本仓库根目录 `D:\\coder\\myagent`</div>
              </div>
              <input value={localSourcePath} onChange={(e) => setLocalSourcePath(e.target.value)} placeholder="D:\\coder\\myagent" />
            </div>
          ) : null}
          </div>

          <div className="card">
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              2) 安装参数
            </div>
            <div className="grid2">
              <div className="field">
                <div className="labelRow">
                  <div className="label">extras</div>
                  <div className="help">建议 `all`（跨平台安全）</div>
                </div>
                <input value={extras} onChange={(e) => setExtras(e.target.value)} placeholder="all / windows / whisper / browser / feishu ..." />
                <div className="btnRow" style={{ marginTop: 8, justifyContent: "flex-start", flexWrap: "wrap" }}>
                  {["all", "windows", "browser", "whisper", "feishu", "dingtalk", "wework", "qq"].map((x) => (
                    <button
                      key={x}
                      className="btnSmall"
                      type="button"
                      onClick={() => setExtras(x)}
                      disabled={!!busy}
                      style={{ borderRadius: 999 }}
                    >
                      {x}
                    </button>
                  ))}
                </div>
              </div>
              <div className="field">
                <div className="labelRow">
                  <div className="label">pip 源（镜像）</div>
                  <div className="help">用于下载 openakita 及其依赖</div>
                </div>
                <select
                  value={pipIndexPresetId}
                  onChange={(e) => {
                    const id = e.target.value as "official" | "tuna" | "aliyun" | "custom";
                    setPipIndexPresetId(id);
                    const preset = PIP_INDEX_PRESETS.find((p) => p.id === id);
                    if (!preset) return;
                    if (id === "custom") {
                      setIndexUrl(customIndexUrl);
                      return;
                    }
                    setIndexUrl(preset.url);
                  }}
                >
                  {PIP_INDEX_PRESETS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
                <input
                  style={{ marginTop: 10 }}
                  value={pipIndexPresetId === "custom" ? customIndexUrl : indexUrl}
                  onChange={(e) => {
                    const v = e.target.value;
                    setCustomIndexUrl(v);
                    if (pipIndexPresetId === "custom") setIndexUrl(v);
                  }}
                  placeholder="自定义 index-url（仅在“自定义…”时生效）"
                  disabled={pipIndexPresetId !== "custom"}
                />
              </div>
            </div>
          </div>

          <div className="divider" />
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div className="cardHint" style={{ marginTop: 0 }}>
              <b>{installReadyText}</b>
              <br />
              <span className="help">安装过程中不建议频繁切换页面；如遇失败请展开查看 pip 输出。</span>
            </div>
            <button className="btnPrimary" onClick={doSetupVenvAndInstallOpenAkita} disabled={!canUsePython || !!busy}>
              {openakitaInstalled ? "升级/重装 openakita" : "创建 venv 并安装 openakita"}
            </button>
          </div>
          {venvStatus ? <div className="okBox">{venvStatus}</div> : null}

          {!!busy && (busy || "").includes("venv") ? (
            <div className="card" style={{ marginTop: 10 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div className="label">实时进度</div>
                  <div className="help">{installProgress ? `${installProgress.stage}（约 ${installProgress.percent}%）` : "执行中..."}</div>
                </div>
                <button
                  type="button"
                  className="btnSmall"
                  onClick={() => setInstallLiveLog("")}
                  disabled={!!busy}
                  style={{ borderRadius: 999 }}
                >
                  清空实时输出
                </button>
              </div>
              <div
                style={{
                  marginTop: 10,
                  height: 10,
                  borderRadius: 999,
                  background: "rgba(17,24,39,0.08)",
                  overflow: "hidden",
                  border: "1px solid rgba(17,24,39,0.08)",
                }}
              >
                <div
                  style={{
                    width: `${installProgress?.percent ?? 5}%`,
                    height: "100%",
                    background: "linear-gradient(90deg, rgba(99,102,241,0.9), rgba(14,165,233,0.9))",
                    transition: "width 180ms ease",
                  }}
                />
              </div>
              <div className="divider" />
              <div className="label">实时输出（pip/验证）</div>
              <pre
                style={{
                  marginTop: 10,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontSize: 12,
                  padding: 12,
                  border: "1px solid var(--line)",
                  borderRadius: 12,
                  background: "rgba(255,255,255,0.7)",
                  maxHeight: 220,
                  overflow: "auto",
                }}
              >
                {installLiveLog || "（等待输出...）"}
              </pre>
              <div className="cardHint" style={{ marginTop: 8 }}>
                说明：这里是<strong>实时</strong>输出；完整输出会在安装结束后出现在“查看安装日志（pip 输出）”。
              </div>
            </div>
          ) : null}

          {installLog ? (
            <details style={{ marginTop: 10 }}>
              <summary style={{ cursor: "pointer", fontWeight: 800 }}>查看安装日志（pip 输出）</summary>
              <pre
                style={{
                  marginTop: 10,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontSize: 12,
                  padding: 12,
                  border: "1px solid var(--line)",
                  borderRadius: 12,
                  background: "rgba(255,255,255,0.7)",
                  maxHeight: 260,
                  overflow: "auto",
                }}
              >
                {installLog}
              </pre>
            </details>
          ) : null}
          {openakitaInstalled ? (
            <div className="okBox">已完成：下一步进入“LLM 端点”，读取服务商列表并拉取模型。</div>
          ) : (
            <div className="cardHint" style={{ marginTop: 10 }}>
              完成安装后，底部“下一步”才可以进入“LLM 端点”。
            </div>
          )}
        </div>
      </>
    );
  }

  function renderLLM() {
    return (
      <>
        <div className="card">
          <div className="cardTitle">LLM 端点（自动拉模型列表）</div>
          <div className="cardHint">
            这一页会做两件事：1) 用 API Key 拉取模型列表 2) 把端点写入工作区 `data/llm_endpoints.json`，并把 Key 写入工作区 `.env`。
          </div>
          <div className="divider" />

          {savedEndpoints.length > 0 ? (
            <div className="card" style={{ marginTop: 0 }}>
              <div className="cardTitle" style={{ fontSize: 14 }}>
                已配置端点（可一直增加，用于备份/容灾）
              </div>
              <div className="cardHint" style={{ marginTop: 6 }}>
                支持拖拽排序（会自动更新 priority）。也可以“一键设为主”（放到第一位）。
              </div>
              <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                {savedEndpoints.map((e) => (
                  <div
                    key={e.name}
                    draggable
                    onDragStart={() => {
                      dragNameRef.current = e.name;
                    }}
                    onDragOver={(ev) => {
                      ev.preventDefault();
                    }}
                    onDrop={(ev) => {
                      ev.preventDefault();
                      const src = dragNameRef.current;
                      const dst = e.name;
                      dragNameRef.current = null;
                      if (!src || src === dst) return;
                      const names = savedEndpoints.map((x) => x.name);
                      const s = names.indexOf(src);
                      const d = names.indexOf(dst);
                      if (s < 0 || d < 0) return;
                      const next = [...names];
                      next.splice(s, 1);
                      next.splice(d, 0, src);
                      void doReorderByNames(next);
                    }}
                    className="row"
                    style={{
                      justifyContent: "space-between",
                      padding: "10px 12px",
                      border: "1px solid var(--line)",
                      borderRadius: 14,
                      background: "rgba(255, 255, 255, 0.75)",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 800 }}>
                        {e.name}{" "}
                        <span style={{ color: "var(--muted)", fontWeight: 600 }}>
                          （priority {e.priority}）
                        </span>
                        {savedEndpoints[0]?.name === e.name ? (
                          <span style={{ marginLeft: 8, color: "var(--brand)", fontWeight: 800 }}>主</span>
                        ) : null}
                      </div>
                      <div className="help" style={{ marginTop: 4 }}>
                      {e.provider}/{e.model} · {e.api_type}
                      <br />
                      {e.base_url}
                      </div>
                    </div>
                    <div className="btnRow">
                      {savedEndpoints[0]?.name !== e.name ? (
                        <button onClick={() => doSetPrimaryEndpoint(e.name)} disabled={!!busy}>
                          设为主
                        </button>
                      ) : null}
                      <button onClick={() => doStartEditEndpoint(e.name)} disabled={!!busy}>
                        编辑
                      </button>
                      <button className="btnDanger" onClick={() => doDeleteEndpoint(e.name)} disabled={!!busy}>
                        删除
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="okBox" style={{ marginTop: 10 }}>
                说明：OpenAkita 会按 priority 从小到大优先使用；主端点挂了会自动切到备份端点。
              </div>
            </div>
          ) : (
            <div className="okBox">当前还没有端点。你可以先拉取模型列表，然后“追加写入端点配置”。</div>
          )}

          <div className="btnRow">
            <button className="btnPrimary" onClick={doLoadProviders} disabled={!!busy}>
              读取服务商列表
            </button>
            <span className="statusLine">（需要先在 venv 安装 openakita）</span>
          </div>

          {providers.length > 0 ? (
            <div style={{ marginTop: 12 }}>
              <div className="card" style={{ marginTop: 0 }}>
                <div className="cardTitle" style={{ fontSize: 14 }}>1) 选择服务商</div>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">服务商</div>
                    <div className="help">选了服务商会自动填 Base URL</div>
                  </div>
                  <select value={providerSlug} onChange={(e) => setProviderSlug(e.target.value)}>
                    {providers.map((p) => (
                      <option key={p.slug} value={p.slug}>
                        {p.name} ({p.slug})
                      </option>
                    ))}
                  </select>
                  {providerApplyUrl ? (
                    <div className="help" style={{ marginTop: 6 }}>
                      申请 Key：<a href={providerApplyUrl}>{providerApplyUrl}</a>
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="card">
                <div className="cardTitle" style={{ fontSize: 14 }}>2) 填写 API Key</div>
                <div className="grid2" style={{ marginTop: 10 }}>
                  <div className="field">
                    <div className="labelRow">
                      <div className="label">API Key 值</div>
                      <div className="help">仅用于当前拉取/写入本地工作区</div>
                    </div>
                    <div style={{ position: "relative" }}>
                      <input
                        value={apiKeyValue}
                        onChange={(e) => setApiKeyValue(e.target.value)}
                        placeholder="sk-..."
                        type={secretShown.__LLM_API_KEY ? "text" : "password"}
                        style={{ paddingRight: 78 }}
                      />
                      <button
                        type="button"
                        className="btnSmall"
                        onClick={() => setSecretShown((m) => ({ ...m, __LLM_API_KEY: !m.__LLM_API_KEY }))}
                        disabled={!!busy}
                        style={{
                          position: "absolute",
                          right: 8,
                          top: "50%",
                          transform: "translateY(-50%)",
                          height: 30,
                          padding: "0 10px",
                          borderRadius: 10,
                        }}
                      >
                        {secretShown.__LLM_API_KEY ? "隐藏" : "显示"}
                      </button>
                    </div>
                  </div>
                  <div className="field">
                    <div className="labelRow">
                      <div className="label">将写入 .env 的变量名</div>
                      <div className="help">端点会引用它（api_key_env）</div>
                    </div>
                    <div className="pill" style={{ justifyContent: "space-between", width: "100%" }}>
                      <span style={{ color: "var(--text)", fontWeight: 800 }}>{apiKeyEnv || "（未生成）"}</span>
                      <button className="btnSmall" onClick={() => setLlmAdvancedOpen((v) => !v)} disabled={!!busy}>
                        {llmAdvancedOpen ? "收起高级" : "高级"}
                      </button>
                    </div>
                  </div>
                </div>

                {llmAdvancedOpen ? (
                  <div style={{ marginTop: 10 }}>
                    <div className="grid2">
                      <div className="field">
                        <div className="labelRow">
                          <div className="label">API Key 环境变量名（可改）</div>
                          <div className="help">避免多端点冲突，可用 _2/_3</div>
                        </div>
                        <input
                          value={apiKeyEnv}
                          onChange={(e) => {
                            setApiKeyEnvTouched(true);
                            setApiKeyEnv(e.target.value);
                          }}
                          placeholder="例如：DASHSCOPE_API_KEY / DASHSCOPE_API_KEY_2"
                        />
                        <div className="btnRow" style={{ marginTop: 8 }}>
                          <button
                            className="btnSmall"
                            onClick={() => {
                              const base = (selectedProvider?.api_key_env_suggestion || envKeyFromSlug(selectedProvider?.slug || "provider")).trim();
                              const used = new Set(Object.keys(envDraft || {}));
                              for (const ep of savedEndpoints) {
                                if (ep.api_key_env) used.add(ep.api_key_env);
                              }
                              setApiKeyEnvTouched(false);
                              setApiKeyEnv(nextEnvKeyName(base, used));
                            }}
                            disabled={!!busy || !selectedProvider}
                          >
                            生成新变量名
                          </button>
                        </div>
                      </div>
                      <div className="field">
                        <div className="labelRow">
                          <div className="label">协议与 Base URL（高级）</div>
                          <div className="help">中转/私有网关可在这里改</div>
                        </div>
                        <div className="row">
                          <select value={apiType} onChange={(e) => setApiType(e.target.value as any)} style={{ width: 160 }}>
                            <option value="openai">openai</option>
                            <option value="anthropic">anthropic</option>
                          </select>
                          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://.../v1" />
                        </div>
                      </div>
                    </div>
                  </div>
                ) : null}

                <div className="btnRow" style={{ marginTop: 12, justifyContent: "flex-start" }}>
                  <button onClick={doFetchModels} className="btnPrimary" disabled={!apiKeyValue.trim() || !baseUrl.trim() || !!busy}>
                    3) 拉取模型列表
                  </button>
                </div>
              </div>

              {models.length > 0 ? (
                <div className="card" style={{ marginTop: 12 }}>
                  <div className="cardTitle" style={{ fontSize: 14 }}>4) 选择模型并保存端点</div>
                  <div className="cardHint">这里会把端点写入 `data/llm_endpoints.json`，并把 Key 写入 `.env`。</div>
                  <div className="grid2" style={{ marginTop: 10 }}>
                    <div className="field">
                      <div className="labelRow">
                        <div className="label">端点名称</div>
                        <div className="help">必须唯一；用于主/备份区分</div>
                      </div>
                      <input
                        value={endpointName}
                        onChange={(e) => {
                          setEndpointNameTouched(true);
                          setEndpointName(e.target.value);
                        }}
                        placeholder="例如：dashscope-qwen3-max / openai-primary"
                      />
                    </div>
                    <div className="field">
                      <div className="labelRow">
                        <div className="label">优先级（越小越优先）</div>
                        <div className="help">例如 1=主端点，2=备份</div>
                      </div>
                      <input
                        value={String(endpointPriority)}
                        onChange={(e) => setEndpointPriority(Number(e.target.value))}
                        placeholder="1 / 2 / 3 ..."
                      />
                    </div>
                  </div>
                  <div className="row" style={{ marginTop: 8, alignItems: "stretch" }}>
                    <SearchSelect
                      value={selectedModelId}
                      onChange={(v) => setSelectedModelId(v)}
                      options={models.map((m) => m.id)}
                      placeholder="搜索/选择模型（可下拉、可输入、可粘贴）"
                      disabled={!!busy}
                    />
                    <button className="btnPrimary" onClick={doSaveEndpoint} disabled={!currentWorkspaceId || !!busy}>
                      {isEditingEndpoint ? "保存修改" : "追加写入端点配置"}
                    </button>
                    {isEditingEndpoint ? (
                      <button onClick={resetEndpointEditor} disabled={!!busy}>
                        取消编辑
                      </button>
                    ) : null}
                  </div>
                  <div className="divider" />
                  <div className="labelRow">
                    <div className="label">模型能力（可手工调整）</div>
                    <div className="help">文本/思考/图片/视频/原生工具</div>
                  </div>
                  <div className="btnRow" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                    {[
                      { k: "text", name: "文本" },
                      { k: "thinking", name: "思考" },
                      { k: "vision", name: "图片" },
                      { k: "video", name: "视频" },
                      { k: "tools", name: "原生工具" },
                    ].map((c) => {
                      const on = capSelected.includes(c.k);
                      return (
                        <button
                          key={c.k}
                          className={on ? "btnPrimary" : ""}
                          onClick={() => {
                            setCapTouched(true);
                            setCapSelected((prev) => {
                              const set = new Set(prev);
                              if (set.has(c.k)) set.delete(c.k);
                              else set.add(c.k);
                              const out = Array.from(set);
                              return out.length ? out : ["text"];
                            });
                          }}
                          disabled={!!busy}
                        >
                          {on ? "✓ " : ""}
                          {c.name}
                        </button>
                      );
                    })}
                    <button
                      onClick={() => {
                        setCapTouched(false);
                        const caps = models.find((m) => m.id === selectedModelId)?.capabilities ?? {};
                        const list = Object.entries(caps)
                          .filter(([, v]) => v)
                          .map(([k]) => k);
                        setCapSelected(list.length ? list : ["text"]);
                      }}
                      disabled={!!busy}
                    >
                      重置为自动识别
                    </button>
                  </div>
                  <div className="help" style={{ marginTop: 8 }}>
                    capabilities：
                    {capSelected.join(", ") || "（未知）"}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="okBox">下一步：进入“IM 通道”，按需启用 Telegram/飞书/企业微信等。</div>
        </div>

        {/* ── Compiler Endpoints Card ── */}
        <div className="card" style={{ marginTop: 24 }}>
          <div className="cardTitle">提示词编译模型（Prompt Compiler）</div>
          <div className="cardHint">
            用于预处理用户指令的轻量模型，建议使用响应速度快的小模型（如 qwen-turbo、gpt-4o-mini）。
            支持主备 2 个端点，失败自动回退主模型。不启用思考模式。
          </div>
          <div className="divider" />

          {savedCompilerEndpoints.length > 0 && (
            <div style={{ display: "grid", gap: 8, marginBottom: 16 }}>
              {savedCompilerEndpoints.map((e) => (
                <div
                  key={e.name}
                  className="row"
                  style={{
                    justifyContent: "space-between",
                    padding: "10px 12px",
                    border: "1px solid var(--line)",
                    borderRadius: 14,
                    background: "rgba(255, 255, 255, 0.75)",
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 800 }}>
                      {e.name}{" "}
                      <span style={{ color: "var(--muted)", fontWeight: 600 }}>
                        （priority {e.priority}）
                      </span>
                      {savedCompilerEndpoints[0]?.name === e.name ? (
                        <span style={{ marginLeft: 8, color: "var(--brand)", fontWeight: 800 }}>主</span>
                      ) : null}
                    </div>
                    <div className="help" style={{ marginTop: 4 }}>
                      {e.provider}/{e.model} · {e.api_type}
                      <br />
                      {e.base_url} · timeout {e.timeout}s
                    </div>
                  </div>
                  <div className="btnRow">
                    <button className="btnDanger" onClick={() => doDeleteCompilerEndpoint(e.name)} disabled={!!busy}>
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {savedCompilerEndpoints.length < 2 ? (
            <div>
              <div style={{ fontSize: 13, opacity: 0.7, marginBottom: 12, fontWeight: 600 }}>
                {savedCompilerEndpoints.length === 0 ? "添加主编译端点" : "添加备用编译端点"}
              </div>
              {providers.length > 0 ? (
                <div className="grid2">
                  <div className="field">
                    <div className="labelRow">
                      <div className="label">服务商</div>
                      <div className="help">选了会自动填 URL 和建议 Key 名</div>
                    </div>
                    <select
                      value={compilerProviderSlug}
                      onChange={(e) => {
                        const slug = e.target.value;
                        setCompilerProviderSlug(slug);
                        const p = providers.find((x) => x.slug === slug);
                        if (p) {
                          setCompilerApiType((p.api_type as any) || "openai");
                          setCompilerBaseUrl(p.default_base_url || "");
                          const suggested = p.api_key_env_suggestion || envKeyFromSlug(p.slug);
                          const used = new Set(Object.keys(envDraft || {}));
                          for (const ep of [...savedEndpoints, ...savedCompilerEndpoints]) {
                            if (ep.api_key_env) used.add(ep.api_key_env);
                          }
                          setCompilerApiKeyEnv(nextEnvKeyName(suggested, used));
                        }
                      }}
                    >
                      <option value="">（请选择）</option>
                      {providers.map((p) => (
                        <option key={p.slug} value={p.slug}>
                          {p.name} ({p.slug})
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <div className="labelRow">
                      <div className="label">协议与 Base URL</div>
                    </div>
                    <div className="row">
                      <select value={compilerApiType} onChange={(e) => setCompilerApiType(e.target.value as any)} style={{ width: 160 }}>
                        <option value="openai">openai</option>
                        <option value="anthropic">anthropic</option>
                      </select>
                      <input value={compilerBaseUrl} onChange={(e) => setCompilerBaseUrl(e.target.value)} placeholder="https://.../v1" />
                    </div>
                  </div>
                </div>
              ) : (
                <div className="grid2">
                  <div className="field">
                    <div className="labelRow">
                      <div className="label">协议与 Base URL</div>
                      <div className="help">请先在上方"读取服务商列表"以启用服务商选择</div>
                    </div>
                    <div className="row">
                      <select value={compilerApiType} onChange={(e) => setCompilerApiType(e.target.value as any)} style={{ width: 160 }}>
                        <option value="openai">openai</option>
                        <option value="anthropic">anthropic</option>
                      </select>
                      <input value={compilerBaseUrl} onChange={(e) => setCompilerBaseUrl(e.target.value)} placeholder="https://.../v1" />
                    </div>
                  </div>
                  <div className="field">
                    <div className="labelRow">
                      <div className="label">服务商 slug（手动）</div>
                    </div>
                    <input value={compilerProviderSlug} onChange={(e) => setCompilerProviderSlug(e.target.value)} placeholder="dashscope / openai" />
                  </div>
                </div>
              )}
              <div className="grid2" style={{ marginTop: 10 }}>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">API Key 值</div>
                    <div className="help">写入工作区 .env</div>
                  </div>
                  <div style={{ position: "relative" }}>
                    <input
                      value={compilerApiKeyValue}
                      onChange={(e) => setCompilerApiKeyValue(e.target.value)}
                      placeholder="sk-..."
                      type={secretShown.__COMPILER_API_KEY ? "text" : "password"}
                      style={{ paddingRight: 78 }}
                    />
                    <button
                      type="button"
                      className="btnSmall"
                      onClick={() => setSecretShown((m) => ({ ...m, __COMPILER_API_KEY: !m.__COMPILER_API_KEY }))}
                      disabled={!!busy}
                      style={{
                        position: "absolute",
                        right: 8,
                        top: "50%",
                        transform: "translateY(-50%)",
                        height: 30,
                        padding: "0 10px",
                        borderRadius: 10,
                      }}
                    >
                      {secretShown.__COMPILER_API_KEY ? "隐藏" : "显示"}
                    </button>
                  </div>
                </div>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">API Key 环境变量名</div>
                    <div className="help">端点引用的 api_key_env</div>
                  </div>
                  <input value={compilerApiKeyEnv} onChange={(e) => setCompilerApiKeyEnv(e.target.value)} placeholder="DASHSCOPE_API_KEY" />
                </div>
              </div>
              <div style={{ marginTop: 10 }}>
                <div className="labelRow" style={{ alignItems: "center" }}>
                  <div className="label">模型</div>
                  <div className="help">可先拉取列表再选，也可直接搜索/粘贴</div>
                </div>
                <div className="btnRow" style={{ marginBottom: 8 }}>
                  <button
                    onClick={doFetchCompilerModels}
                    className="btnPrimary"
                    disabled={!compilerApiKeyValue.trim() || !compilerBaseUrl.trim() || !!busy}
                    style={{ whiteSpace: "nowrap" }}
                  >
                    拉取模型列表
                  </button>
                  {compilerModels.length > 0 ? (
                    <span className="help" style={{ fontSize: 12 }}>已拉取 {compilerModels.length} 个模型</span>
                  ) : null}
                </div>
                <SearchSelect
                  value={compilerModel}
                  onChange={(v) => setCompilerModel(v)}
                  options={compilerModels.length > 0 ? compilerModels.map((m) => m.id) : []}
                  placeholder="搜索/选择模型（也可手动输入，如 qwen-turbo / gpt-4o-mini）"
                  disabled={!!busy}
                />
              </div>
              <div className="grid2" style={{ marginTop: 10 }}>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">端点名称（可选）</div>
                    <div className="help">留空自动生成</div>
                  </div>
                  <input value={compilerEndpointName} onChange={(e) => setCompilerEndpointName(e.target.value)} placeholder="compiler-primary" />
                </div>
              </div>
              <div className="btnRow" style={{ marginTop: 14 }}>
                <button
                  className="btnPrimary"
                  onClick={doSaveCompilerEndpoint}
                  disabled={!currentWorkspaceId || !compilerModel.trim() || !compilerApiKeyEnv.trim() || !compilerApiKeyValue.trim() || !!busy}
                >
                  保存编译端点
                </button>
              </div>
            </div>
          ) : (
            <div className="okBox">已配置 2 个编译端点（最多 2 个）。</div>
          )}
        </div>

        {llmNextModalOpen ? (
          <div
            onClick={() => setLlmNextModalOpen(false)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.24)",
              zIndex: 9999,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 24,
            }}
          >
            <div className="card" onClick={(e) => e.stopPropagation()} style={{ width: 720, maxWidth: "100%" }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <div className="cardTitle">已存在端点，是否继续？</div>
                  <div className="cardHint">
                    当前工作区已配置 <b>{savedEndpoints.length}</b> 个 LLM 端点。
                    <br />
                    你可以继续下一步（不新增端点），也可以留在本页继续新增/调整端点。
                  </div>
                </div>
                <div className="btnRow">
                  <button onClick={() => setLlmNextModalOpen(false)} disabled={!!busy}>
                    关闭
                  </button>
                </div>
              </div>
              <div className="divider" />
              <div className="btnRow" style={{ justifyContent: "flex-end" }}>
                <button
                  onClick={() => {
                    setLlmNextModalOpen(false);
                  }}
                  disabled={!!busy}
                >
                  留在本页新增端点
                </button>
                <button
                  className="btnPrimary"
                  onClick={() => {
                    setLlmNextModalOpen(false);
                    setStepId(steps[Math.min(currentStepIdx + 1, steps.length - 1)].id);
                  }}
                  disabled={!!busy}
                >
                  继续下一步
                </button>
              </div>
            </div>
          </div>
        ) : null}
        {editModalOpen && editDraft ? (
          <div
            onClick={() => resetEndpointEditor()}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.24)",
              zIndex: 9999,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 24,
            }}
          >
            <div
              className="card"
              onClick={(e) => e.stopPropagation()}
              style={{ width: 920, maxWidth: "100%", maxHeight: "90vh", overflow: "auto" }}
            >
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <div className="cardTitle">编辑端点</div>
                  <div className="cardHint">编辑不会自动改动 API Key 值；如需更新请在下方填写。</div>
                </div>
                <div className="btnRow">
                  <button onClick={resetEndpointEditor} disabled={!!busy}>关闭</button>
                </div>
              </div>
              <div className="divider" />
              <div className="grid2">
                <div className="field">
                  <div className="labelRow">
                    <div className="label">端点名称</div>
                    <div className="help">必须唯一</div>
                  </div>
                  <input value={editDraft.name} onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })} />
                </div>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">优先级</div>
                    <div className="help">越小越优先</div>
                  </div>
                  <input value={String(editDraft.priority)} onChange={(e) => setEditDraft({ ...editDraft, priority: Number(e.target.value) })} />
                </div>
              </div>
              <div className="grid2" style={{ marginTop: 10 }}>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">服务商</div>
                    <div className="help">会影响默认 URL/建议 env</div>
                  </div>
                  <select
                    value={editDraft.providerSlug}
                    onChange={(e) => {
                      const slug = e.target.value;
                      const p = providers.find((x) => x.slug === slug);
                      const suggested = p?.api_key_env_suggestion || envKeyFromSlug(slug);
                      const used = new Set(Object.keys(envDraft || {}));
                      for (const ep of savedEndpoints) if (ep.api_key_env) used.add(ep.api_key_env);
                      setEditDraft({
                        ...editDraft,
                        providerSlug: slug,
                        apiType: ((p?.api_type as any) || editDraft.apiType) as any,
                        baseUrl: p?.default_base_url || editDraft.baseUrl,
                        apiKeyEnv: nextEnvKeyName(suggested, used),
                      });
                    }}
                  >
                    {providers.map((p) => (
                      <option key={p.slug} value={p.slug}>
                        {p.name} ({p.slug})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">协议与 Base URL</div>
                    <div className="help">可手工改</div>
                  </div>
                  <div className="row">
                    <select value={editDraft.apiType} onChange={(e) => setEditDraft({ ...editDraft, apiType: e.target.value as any })} style={{ width: 160 }}>
                      <option value="openai">openai</option>
                      <option value="anthropic">anthropic</option>
                    </select>
                    <input value={editDraft.baseUrl} onChange={(e) => setEditDraft({ ...editDraft, baseUrl: e.target.value })} />
                  </div>
                </div>
              </div>
              <div className="grid2" style={{ marginTop: 10 }}>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">API Key 环境变量名</div>
                    <div className="help">写入端点的 api_key_env</div>
                  </div>
                  <input value={editDraft.apiKeyEnv} onChange={(e) => setEditDraft({ ...editDraft, apiKeyEnv: e.target.value })} />
                </div>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">API Key 值（可选）</div>
                    <div className="help">留空则不修改 .env</div>
                  </div>
                  <div style={{ position: "relative" }}>
                    <input
                      value={editDraft.apiKeyValue}
                      onChange={(e) => setEditDraft({ ...editDraft, apiKeyValue: e.target.value })}
                      type={secretShown.__EDIT_API_KEY ? "text" : "password"}
                      style={{ paddingRight: 78 }}
                    />
                    <button
                      type="button"
                      className="btnSmall"
                      onClick={() => setSecretShown((m) => ({ ...m, __EDIT_API_KEY: !m.__EDIT_API_KEY }))}
                      disabled={!!busy}
                      style={{
                        position: "absolute",
                        right: 8,
                        top: "50%",
                        transform: "translateY(-50%)",
                        height: 30,
                        padding: "0 10px",
                        borderRadius: 10,
                      }}
                    >
                      {secretShown.__EDIT_API_KEY ? "隐藏" : "显示"}
                    </button>
                  </div>
                </div>
              </div>
              <div className="divider" />
              <div className="labelRow" style={{ alignItems: "center" }}>
                <div className="label">模型</div>
                <div className="help">可搜索/下拉/粘贴；也可先拉取列表再选</div>
              </div>
              <div className="btnRow" style={{ marginBottom: 8 }}>
                <button
                  onClick={doFetchEditModels}
                  className="btnPrimary"
                  disabled={!editDraft.baseUrl.trim() || !!busy}
                  style={{ whiteSpace: "nowrap" }}
                >
                  拉取模型列表
                </button>
                {editModels.length > 0 ? (
                  <span className="help" style={{ fontSize: 12 }}>已拉取 {editModels.length} 个模型</span>
                ) : null}
              </div>
              <SearchSelect
                value={editDraft.modelId}
                onChange={(v) => {
                  setEditDraft({ ...editDraft, modelId: v });
                  // auto-update capabilities from fetched model if user hasn't manually edited
                  const src = editModels.length > 0 ? editModels : models;
                  const m = src.find((x) => x.id === v);
                  if (m?.capabilities) {
                    const list = Object.entries(m.capabilities)
                      .filter(([, val]) => val)
                      .map(([k]) => k);
                    if (list.length) setEditDraft((d) => d ? { ...d, modelId: v, caps: list } : d);
                  }
                }}
                options={(editModels.length > 0 ? editModels : models).map((m) => m.id)}
                placeholder="输入或选择模型 ID"
                disabled={!!busy}
              />
              <div className="divider" />
              <div className="labelRow">
                <div className="label">模型能力</div>
                <div className="help">可手工调整</div>
              </div>
              <div className="btnRow" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                {[
                  { k: "text", name: "文本" },
                  { k: "thinking", name: "思考" },
                  { k: "vision", name: "图片" },
                  { k: "video", name: "视频" },
                  { k: "tools", name: "原生工具" },
                ].map((c) => {
                  const on = editDraft.caps.includes(c.k);
                  return (
                    <button
                      key={c.k}
                      className={on ? "btnPrimary" : ""}
                      onClick={() => {
                        const set = new Set(editDraft.caps);
                        if (set.has(c.k)) set.delete(c.k);
                        else set.add(c.k);
                        const out = Array.from(set);
                        setEditDraft({ ...editDraft, caps: out.length ? out : ["text"] });
                      }}
                      disabled={!!busy}
                    >
                      {on ? "✓ " : ""}
                      {c.name}
                    </button>
                  );
                })}
              </div>
              <div className="divider" />
              <div className="btnRow" style={{ justifyContent: "flex-end" }}>
                <button onClick={resetEndpointEditor} disabled={!!busy}>取消</button>
                <button className="btnPrimary" onClick={doSaveEditedEndpoint} disabled={!!busy}>保存</button>
              </div>
            </div>
          </div>
        ) : null}
      </>
    );
  }

  function FieldText({
    k,
    label,
    placeholder,
    help,
    type,
  }: {
    k: string;
    label: string;
    placeholder?: string;
    help?: string;
    type?: "text" | "password";
  }) {
    const isSecret = (type || "text") === "password";
    const shown = !!secretShown[k];
    return (
      <div className="field">
        <div className="labelRow">
          <div className="label">{label}</div>
          {k ? <div className="help">{k}</div> : null}
        </div>
        <div style={{ position: "relative" }}>
          <input
            value={envGet(envDraft, k)}
            onChange={(e) => setEnvDraft((m) => envSet(m, k, e.target.value))}
            placeholder={placeholder}
            type={isSecret ? (shown ? "text" : "password") : "text"}
            style={isSecret ? { paddingRight: 78 } : undefined}
          />
          {isSecret ? (
            <button
              type="button"
              className="btnSmall"
              onClick={() => setSecretShown((m) => ({ ...m, [k]: !m[k] }))}
              disabled={!!busy}
              style={{
                position: "absolute",
                right: 8,
                top: "50%",
                transform: "translateY(-50%)",
                height: 30,
                padding: "0 10px",
                borderRadius: 10,
              }}
            >
              {shown ? "隐藏" : "显示"}
            </button>
          ) : null}
        </div>
        {help ? <div className="help">{help}</div> : null}
      </div>
    );
  }

  function FieldBool({ k, label, help }: { k: string; label: string; help?: string }) {
    const v = envGet(envDraft, k, "false").toLowerCase() === "true";
    return (
      <div className="field">
        <div className="labelRow">
          <div className="label">{label}</div>
          <div className="help">{k}</div>
        </div>
        <div className="row">
          <label className="pill" style={{ cursor: "pointer" }}>
            <input
              style={{ width: 16, height: 16 }}
              type="checkbox"
              checked={v}
              onChange={(e) => setEnvDraft((m) => envSet(m, k, String(e.target.checked)))}
            />
            启用
          </label>
          {help ? <span className="help">{help}</span> : null}
        </div>
      </div>
    );
  }

  async function renderIntegrationsSave(keys: string[], successText: string) {
    if (!currentWorkspaceId) {
      setError("请先设置当前工作区");
      return;
    }
    setBusy("写入 .env...");
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
      "TELEGRAM_ENABLED",
      "TELEGRAM_BOT_TOKEN",
      "TELEGRAM_PROXY",
      "FEISHU_ENABLED",
      "FEISHU_APP_ID",
      "FEISHU_APP_SECRET",
      "WEWORK_ENABLED",
      "WEWORK_CORP_ID",
      "WEWORK_AGENT_ID",
      "WEWORK_SECRET",
      "WEWORK_TOKEN",
      "WEWORK_ENCODING_AES_KEY",
      "WEWORK_CALLBACK_PORT",
      "DINGTALK_ENABLED",
      "DINGTALK_CLIENT_ID",
      "DINGTALK_CLIENT_SECRET",
      "QQ_ENABLED",
      "QQ_ONEBOT_URL",
    ];

    return (
      <>
        <div className="card">
          <div className="cardTitle">IM 通道</div>
          <div className="cardHint">默认折叠显示。选择“启用”后展开填写信息（上下排列）。</div>
          <div className="divider" />

          {[
            {
              title: "Telegram",
              enabledKey: "TELEGRAM_ENABLED",
              apply: "https://t.me/BotFather",
              body: (
                <>
                  <FieldText k="TELEGRAM_BOT_TOKEN" label="Bot Token（必填）" placeholder="从 BotFather 获取（仅会显示一次）" type="password" />
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
                  <FieldText k="FEISHU_APP_ID" label="App ID（必填）" placeholder="" />
                  <FieldText k="FEISHU_APP_SECRET" label="App Secret（必填）" placeholder="" type="password" />
                </>
              ),
            },
            {
              title: "企业微信（需要 openakita[wework]）",
              enabledKey: "WEWORK_ENABLED",
              apply: "https://work.weixin.qq.com/",
              body: (
                <>
                  <FieldText k="WEWORK_CORP_ID" label="Corp ID（必填）" />
                  <FieldText k="WEWORK_AGENT_ID" label="Agent ID（必填）" />
                  <FieldText k="WEWORK_SECRET" label="Secret（必填）" type="password" />
                  <FieldText k="WEWORK_TOKEN" label="回调 Token（接收消息必填）" placeholder="在企业微信后台「接收消息」设置中获取" />
                  <FieldText k="WEWORK_ENCODING_AES_KEY" label="EncodingAESKey（接收消息必填）" placeholder="在企业微信后台「接收消息」设置中获取" type="password" />
                  <FieldText k="WEWORK_CALLBACK_PORT" label="回调端口（默认 9880）" placeholder="9880" />
                </>
              ),
            },
            {
              title: "钉钉（需要 openakita[dingtalk]）",
              enabledKey: "DINGTALK_ENABLED",
              apply: "https://open.dingtalk.com/",
              body: (
                <>
                  <FieldText k="DINGTALK_CLIENT_ID" label="Client ID（必填）" />
                  <FieldText k="DINGTALK_CLIENT_SECRET" label="Client Secret（必填）" type="password" />
                </>
              ),
            },
            {
              title: "QQ（需要 openakita[qq] + NapCat/Lagrange）",
              enabledKey: "QQ_ENABLED",
              apply: "https://github.com/botuniverse/onebot-11",
              body: <FieldText k="QQ_ONEBOT_URL" label="OneBot WebSocket URL（必填）" placeholder="ws://127.0.0.1:8080" />,
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
                  申请/文档：<a href={c.apply}>{c.apply}</a>
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

          <div className="btnRow" style={{ marginTop: 14 }}>
            <button
              className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysIM, "已写入工作区 .env（IM 通道）")}
              disabled={!currentWorkspaceId || !!busy}
            >
              保存 IM 配置到工作区 .env
            </button>
          </div>
          <div className="cardHint" style={{ marginTop: 8 }}>
            只会写入你实际填写/修改过的键；清空后保存会从 `.env` 删除该键（可选项不填就不会写入）。
          </div>
          <div className="okBox">下一步：进入“工具与技能”，配置 Skills / MCP / 桌面自动化。</div>
        </div>
      </>
    );
  }

  function renderTools() {
    const keysTools = [
      // network/proxy
      "HTTP_PROXY",
      "HTTPS_PROXY",
      "ALL_PROXY",
      "FORCE_IPV4",
      "TOOL_MAX_PARALLEL",
      // MCP
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
      // voice / github
      "WHISPER_MODEL",
      "GITHUB_TOKEN",
    ];

    const list = skillsDetail || [];
    const systemSkills = list.filter((s) => !!s.system);
    const externalSkills = list.filter((s) => !s.system);

    return (
      <>
        <div className="card">
          <div className="cardTitle">工具与技能</div>
          <div className="cardHint">这里配置 Skills（可启用/禁用）以及 MCP / 桌面自动化 / 代理等。</div>
          <div className="divider" />

          <div className="card" style={{ marginTop: 0 }}>
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              Skills（系统技能默认启用；外部技能可选）
            </div>
            <div className="cardHint">
              系统技能（system）默认启用且不可关闭；外部技能由你选择是否启用。
              <br />
              保存后会写入工作区 `data/skills.json`，并在运行时生效。
            </div>
            <div className="divider" />
            <div className="btnRow" style={{ justifyContent: "flex-start" }}>
              <button
                onClick={() => {
                  if (!skillsDetail) return;
                  setSkillsTouched(true);
                  const m: Record<string, boolean> = {};
                  for (const s of skillsDetail) m[s.name] = true;
                  setSkillsSelection(m);
                }}
                disabled={!skillsDetail || !!busy}
              >
                启用全部外部技能
              </button>
              <button
                onClick={() => {
                  if (!skillsDetail) return;
                  setSkillsTouched(true);
                  const m: Record<string, boolean> = {};
                  for (const s of skillsDetail) m[s.name] = !!s.system;
                  setSkillsSelection(m);
                }}
                disabled={!skillsDetail || !!busy}
              >
                仅启用系统技能
              </button>
              <button onClick={doRefreshSkills} disabled={!currentWorkspaceId || !!busy}>
                刷新 skills 列表
              </button>
              <button className="btnPrimary" onClick={doSaveSkillsSelection} disabled={!currentWorkspaceId || !skillsDetail || !!busy}>
                保存 skills 启用状态
              </button>
            </div>

            {!skillsDetail ? (
              <div className="cardHint" style={{ marginTop: 10 }}>
                未读取到 skills（通常是 venv 未安装 openakita 或尚未完成“安装”步骤）。完成安装后点击“刷新 skills 列表”。
              </div>
            ) : (
              <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
                <div className="label">系统技能（{systemSkills.length}）</div>
                {systemSkills.length === 0 ? (
                  <div className="cardHint">无</div>
                ) : (
                  systemSkills.map((s) => (
                    <div key={s.name} className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <b>{s.name}</b> <span className="pill">system</span>
                        <div className="help">{s.description}</div>
                      </div>
                      <label className="pill" style={{ opacity: 0.75 }}>
                        已启用
                      </label>
                    </div>
                  ))
                )}

                <div className="divider" />
                <div className="label">外部技能（{externalSkills.length}）</div>
                {externalSkills.length === 0 ? (
                  <div className="cardHint">无（把外部技能放在工作区 `skills/` 或 `.cursor/skills/` 等目录里即可被扫描到）</div>
                ) : (
                  externalSkills.map((s) => {
                    const on = !!skillsSelection[s.name];
                    return (
                      <div key={s.name} className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                        <div style={{ flex: "1 1 auto", paddingRight: 12 }}>
                          <b>{s.name}</b>
                          <div className="help">{s.description}</div>
                        </div>
                        <label className="pill" style={{ cursor: "pointer", userSelect: "none" }}>
                          <input
                            style={{ width: 16, height: 16 }}
                            type="checkbox"
                            checked={on}
                            onChange={(e) => {
                              setSkillsTouched(true);
                              const v = e.target.checked;
                              setSkillsSelection((m) => ({ ...m, [s.name]: v }));
                            }}
                          />
                          启用
                        </label>
                      </div>
                    );
                  })
                )}
              </div>
            )}
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
              <FieldText k="WHISPER_MODEL" label="WHISPER_MODEL" placeholder="base" help="tiny/base/small/medium/large" />
            </div>
          </div>

          <div className="card">
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              MCP / 桌面自动化 / GitHub
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="card" style={{ marginTop: 0, width: "100%" }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  MCP
                </div>
                <FieldBool k="MCP_ENABLED" label="启用 MCP" help="连接外部 MCP 服务/工具" />
                <div className="grid2" style={{ marginTop: 10, maxWidth: 900 }}>
                  <FieldBool k="MCP_BROWSER_ENABLED" label="Browser MCP" help="Playwright 浏览器自动化" />
                  <FieldText k="MCP_TIMEOUT" label="MCP_TIMEOUT" placeholder="60" />
                </div>
                <div className="divider" />
                <FieldBool k="MCP_MYSQL_ENABLED" label="MySQL MCP" />
                <div className="grid2" style={{ marginTop: 10, maxWidth: 900 }}>
                  <FieldText k="MCP_MYSQL_HOST" label="MCP_MYSQL_HOST" placeholder="localhost" />
                  <FieldText k="MCP_MYSQL_USER" label="MCP_MYSQL_USER" placeholder="root" />
                  <FieldText k="MCP_MYSQL_PASSWORD" label="MCP_MYSQL_PASSWORD" type="password" />
                  <FieldText k="MCP_MYSQL_DATABASE" label="MCP_MYSQL_DATABASE" placeholder="mydb" />
                </div>
                <div className="divider" />
                <FieldBool k="MCP_POSTGRES_ENABLED" label="Postgres MCP" />
                <FieldText k="MCP_POSTGRES_URL" label="MCP_POSTGRES_URL" placeholder="postgresql://user:pass@localhost/db" />
              </div>

              <div className="card" style={{ marginTop: 0, width: "100%" }}>
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
                <div className="grid2" style={{ marginTop: 10, maxWidth: 900 }}>
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
              <FieldText k="GITHUB_TOKEN" label="GITHUB_TOKEN" placeholder="" type="password" help="用于搜索/下载技能" />
            </div>
          </div>

          <div className="btnRow">
            <button
              className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysTools, "已写入工作区 .env（工具 / MCP / 桌面 / 代理）")}
              disabled={!currentWorkspaceId || !!busy}
            >
              保存工具配置到工作区 .env
            </button>
          </div>
          <div className="cardHint" style={{ marginTop: 8 }}>
            只会写入你实际填写/修改过的键；清空后保存会从 `.env` 删除该键（可选项不填就不会写入）。
          </div>
          <div className="okBox">下一步：进入“Agent 与系统”，把调度/记忆/会话等跑起来。</div>
        </div>
      </>
    );
  }

  function renderAgentSystem() {
    const keysAgent = [
      // agent
      "AGENT_NAME",
      "MAX_ITERATIONS",
      "AUTO_CONFIRM",
      // timeouts
      "PROGRESS_TIMEOUT_SECONDS",
      "HARD_TIMEOUT_SECONDS",
      // logging/db
      "DATABASE_PATH",
      "LOG_LEVEL",
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
      // orchestration
      "ORCHESTRATION_ENABLED",
      "ORCHESTRATION_BUS_ADDRESS",
      "ORCHESTRATION_PUB_ADDRESS",
      "ORCHESTRATION_MIN_WORKERS",
      "ORCHESTRATION_MAX_WORKERS",
      "ORCHESTRATION_HEARTBEAT_INTERVAL",
      "ORCHESTRATION_HEALTH_CHECK_INTERVAL",
    ];

    return (
      <>
        <div className="card">
          <div className="cardTitle">Agent 与系统（核心配置）</div>
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
              <FieldText k="DATABASE_PATH" label="数据库路径" placeholder="data/agent.db" />
              <FieldText k="LOG_LEVEL" label="日志级别" placeholder="INFO" help="DEBUG/INFO/WARNING/ERROR" />
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
            <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>人格系统</summary>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
              <FieldText k="PERSONA_NAME" label="角色预设" placeholder="default" help="可选: default / business / tech_expert / butler / girlfriend / boyfriend / family / jarvis" />
              <div className="cardHint">8 种预设角色，每种有不同的沟通风格。用户可在对话中随时切换。偏好会通过 LLM 从对话中自动学习。</div>
            </div>
          </details>

          <div className="divider" />
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>活人感模式</summary>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
              <FieldBool k="PROACTIVE_ENABLED" label="启用活人感模式" help="开启后 Agent 会主动发问候、任务跟进、记忆回忆等消息" />
              <FieldText k="PROACTIVE_MAX_DAILY_MESSAGES" label="每日最大主动消息数" placeholder="3" />
              <FieldText k="PROACTIVE_MIN_INTERVAL_MINUTES" label="消息最短间隔（分钟）" placeholder="120" />
              <FieldText k="PROACTIVE_QUIET_HOURS_START" label="安静时段开始（小时）" placeholder="23" help="0-23，安静时段内不发送主动消息" />
              <FieldText k="PROACTIVE_QUIET_HOURS_END" label="安静时段结束（小时）" placeholder="7" />
              <FieldText k="PROACTIVE_IDLE_THRESHOLD_HOURS" label="空闲触发阈值（小时）" placeholder="24" help="用户多久没互动后触发闲聊问候" />
            </div>
          </details>

          <div className="divider" />
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>表情包</summary>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
              <FieldBool k="STICKER_ENABLED" label="启用表情包" help="开启后 Agent 可在 IM 中发送表情包（数据源：ChineseBQB）" />
              <FieldText k="STICKER_DATA_DIR" label="表情包数据目录" placeholder="data/sticker" />
            </div>
          </details>

          <div className="divider" />
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>会话</summary>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
              <FieldText k="SESSION_TIMEOUT_MINUTES" label="会话超时（分钟）" placeholder="30" />
              <FieldText k="SESSION_MAX_HISTORY" label="会话最大历史条数" placeholder="50" />
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
              <FieldText k="ORCHESTRATION_BUS_ADDRESS" label="总线地址" placeholder="tcp://127.0.0.1:5555" />
              <FieldText k="ORCHESTRATION_PUB_ADDRESS" label="广播地址" placeholder="tcp://127.0.0.1:5556" />
              <FieldText k="ORCHESTRATION_MIN_WORKERS" label="最小 Worker 数" placeholder="1" />
              <FieldText k="ORCHESTRATION_MAX_WORKERS" label="最大 Worker 数" placeholder="4" />
            </div>
          </details>

          <div className="btnRow" style={{ marginTop: 14 }}>
            <button
              className="btnPrimary"
              onClick={() => renderIntegrationsSave(keysAgent, "已写入工作区 .env（Agent 与系统）")}
              disabled={!currentWorkspaceId || !!busy}
            >
              保存 Agent 配置到工作区 .env
            </button>
          </div>
          <div className="cardHint" style={{ marginTop: 8 }}>
            只会写入你实际填写/修改过的键；清空后保存会从 `.env` 删除该键（可选项不填就不会写入）。
          </div>
          <div className="okBox">下一步：进入“完成”，查看运行/发布建议。</div>
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
      "TOOL_MAX_PARALLEL",
      // timeouts
      "PROGRESS_TIMEOUT_SECONDS",
      "HARD_TIMEOUT_SECONDS",
      // logging/db
      "DATABASE_PATH",
      "LOG_LEVEL",
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
      // orchestration
      "ORCHESTRATION_ENABLED",
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
      "FEISHU_ENABLED",
      "FEISHU_APP_ID",
      "FEISHU_APP_SECRET",
      "WEWORK_ENABLED",
      "WEWORK_CORP_ID",
      "WEWORK_AGENT_ID",
      "WEWORK_SECRET",
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
                    <FieldText k="WEWORK_AGENT_ID" label="Agent ID" />
                    <FieldText k="WEWORK_SECRET" label="Secret" type="password" />
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
                    申请/文档：<a href={c.apply}>{c.apply}</a>
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
                <FieldText k="DATABASE_PATH" label="数据库路径" placeholder="data/agent.db" />
                <FieldText k="LOG_LEVEL" label="日志级别" placeholder="INFO" help="DEBUG/INFO/WARNING/ERROR" />
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
          <div className="okBox">下一步：进入“完成”，查看“下一步建议（打包/测试/发布）”。</div>
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

  function renderStepContent() {
    if (!info) return <div className="card">加载中...</div>;
    if (view === "status") return renderStatus();
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
    <div className="appShell">
      <aside className="sidebar">
        <div className="sidebarHeader">
          <div className="brandTitle">OpenAkita Setup Center</div>
          <div className="brandSub">
            一键安装与配置向导
            <br />
            跨平台：Windows / macOS / Linux
          </div>
        </div>
        <div className="stepList">
          {steps.map((s, idx) => {
            const isActive = s.id === stepId;
            const isDone = done.has(s.id);
            const canJump = idx <= currentStepIdx; // 一步一步来：只能回到已到达的步骤
            return (
              <div
                key={s.id}
                className={`stepItem ${isActive ? "stepItemActive" : ""} ${canJump ? "" : "stepItemDisabled"}`}
                onClick={() => {
                  if (!canJump) return;
                  setView("wizard");
                  setStepId(s.id);
                }}
                role="button"
                tabIndex={0}
                aria-disabled={!canJump}
              >
                <StepDot idx={idx} isDone={isDone} />
                <div className="stepMeta">
                  <div className="stepTitle">{s.title}</div>
                  <div className="stepDesc">{s.desc}</div>
                </div>
              </div>
            );
          })}
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div>
            <div className="topbarTitle">
              {view === "status" ? "状态面板（独立）" : `第 ${currentStepIdx + 1} 步 / ${steps.length} 步：${step.title}`}
            </div>
            <div className="statusLine">{view === "status" ? "运行状态与监控入口（不属于安装流程）" : step.desc}</div>
          </div>
          {headerRight}
        </div>

        <div className="content">
          {busy ? <div className="okBox">正在处理：{busy}</div> : null}
          {notice ? <div className="okBox">{notice}</div> : null}
          {error ? <div className="errorBox">{error}</div> : null}
          {renderStepContent()}
        </div>

        {view === "wizard" ? (
          <div className="footer">
            <div className="statusLine">提示：先按顺序走完，再回头微调参数会更快。</div>
            <div className="btnRow">
              <button onClick={goPrev} disabled={isFirst || !!busy}>
                上一步
              </button>
              {stepId === "finish" ? (
                <button
                  className="btnPrimary"
                  onClick={async () => {
                    // 完成并启动：启动 openakita serve，然后切到状态面板（托盘常驻）
                    const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
                    if (!effectiveWsId) {
                      setError("未找到工作区（请先创建/选择一个工作区）");
                      return;
                    }
                    setBusy("启动后台服务...");
                    setError(null);
                    // 无论启动是否成功，都进入状态面板，方便用户看日志/重试（面向非技术用户）
                    setView("status");
                    try {
                      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", {
                        venvDir,
                        workspaceId: effectiveWsId,
                      });
                      setServiceStatus(ss);
                      // 轻量确认：避免“瞬间启动又退出”导致 UI 误以为已启动
                      await new Promise((r) => setTimeout(r, 600));
                      const real = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", {
                        workspaceId: effectiveWsId,
                      });
                      setServiceStatus(real);
                      await refreshStatus();
                      await refreshServiceLog(effectiveWsId);
                      if (!real.running) {
                        setError("后台服务未能保持运行（可能是工作区 .env 配置为空值导致启动失败）。请查看下方服务日志。");
                      } else {
                        setNotice("已启动后台服务（openakita serve）。窗口可关闭并常驻托盘。");
                      }
                    } catch (e) {
                      setError(String(e));
                      try {
                        await refreshStatus();
                        await refreshServiceLog(effectiveWsId);
                      } catch {
                        // ignore
                      }
                    } finally {
                      setBusy(null);
                    }
                  }}
                  disabled={!!busy}
                >
                  完成并启动
                </button>
              ) : (
                <button className="btnPrimary" onClick={goNext} disabled={isLast || !!busy}>
                  下一步
                </button>
              )}
            </div>
          </div>
        ) : null}
      </main>
    </div>
  );
}

