import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

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
  | "status"
  | "welcome"
  | "workspace"
  | "python"
  | "install"
  | "llm"
  | "integrations"
  | "finish";

type Step = {
  id: StepId;
  title: string;
  desc: string;
};

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
        id: "status",
        title: "状态面板",
        desc: "运行状态与监控入口",
      },
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
        id: "integrations",
        title: "工具与集成",
        desc: "IM / MCP / 桌面 / 代理等全覆盖",
      },
      {
        id: "finish",
        title: "完成",
        desc: "下一步引导与检查清单",
      },
    ],
    [],
  );

  const [stepId, setStepId] = useState<StepId>("status");
  const currentStepIdx = useMemo(() => steps.findIndex((s) => s.id === stepId), [steps, stepId]);
  const isFirst = currentStepIdx <= 0;
  const isLast = currentStepIdx >= steps.length - 1;

  // workspace create
  const [newWsName, setNewWsName] = useState("默认工作区");
  const newWsId = useMemo(() => slugify(newWsName) || "default", [newWsName]);

  // python / venv / install
  const [pythonCandidates, setPythonCandidates] = useState<PythonCandidate[]>([]);
  const [selectedPythonIdx, setSelectedPythonIdx] = useState<number>(-1);
  const [venvStatus, setVenvStatus] = useState<string>("");
  const [extras, setExtras] = useState<string>("all");
  const [indexUrl, setIndexUrl] = useState<string>("");
  const [venvReady, setVenvReady] = useState(false);
  const [openakitaInstalled, setOpenakitaInstalled] = useState(false);
  const [installSource, setInstallSource] = useState<InstallSource>("pypi");
  const [githubRepo, setGithubRepo] = useState<string>("openakita/openakita");
  const [githubRefType, setGithubRefType] = useState<"branch" | "tag">("branch");
  const [githubRef, setGithubRef] = useState<string>("main");
  const [localSourcePath, setLocalSourcePath] = useState<string>("");

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

  // status panel data
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [endpointSummary, setEndpointSummary] = useState<
    { name: string; provider: string; apiType: string; baseUrl: string; model: string; keyEnv: string; keyPresent: boolean }[]
  >([]);
  const [skillSummary, setSkillSummary] = useState<{ count: number; systemCount: number; externalCount: number } | null>(null);
  const [autostartEnabled, setAutostartEnabled] = useState<boolean | null>(null);
  const [serviceStatus, setServiceStatus] = useState<{ running: boolean; pid: number | null; pidFile: string } | null>(null);

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
        setStepId("status");
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

  const canUsePython = useMemo(() => {
    if (selectedPythonIdx < 0) return false;
    return pythonCandidates[selectedPythonIdx]?.isUsable ?? false;
  }, [pythonCandidates, selectedPythonIdx]);

  const pipIndexPresetId = useMemo<"official" | "tuna" | "aliyun" | "custom">(() => {
    const t = indexUrl.trim();
    if (!t) return "official";
    const hit = PIP_INDEX_PRESETS.find((p) => p.url && p.url === t);
    return hit ? hit.id : "custom";
  }, [indexUrl]);

  const done = useMemo(() => {
    const d = new Set<StepId>();
    if (info) d.add("welcome");
    if (currentWorkspaceId) d.add("workspace");
    if (canUsePython) d.add("python");
    if (openakitaInstalled) d.add("install");
    if (models.length > 0 && selectedModelId) d.add("llm");
    // integrations/finish are completion-oriented; keep manual.
    return d;
  }, [info, currentWorkspaceId, canUsePython, openakitaInstalled, models.length, selectedModelId]);

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

  async function doSetupVenvAndInstallOpenAkita() {
    if (!canUsePython) {
      setError("请先在 Python 步骤安装/检测并选择一个可用 Python（3.11+）。");
      return;
    }
    setError(null);
    setNotice(null);
    setBusy("创建 venv 并安装 openakita...");
    try {
      // 1) create venv (idempotent)
      setVenvStatus("创建 venv 中...");
      const py = pythonCandidates[selectedPythonIdx].command;
      await invoke<string>("create_venv", { pythonCommand: py, venvDir });
      setVenvReady(true);
      setOpenakitaInstalled(false);
      setVenvStatus(`venv 就绪：${venvDir}`);

      // 2) pip install
      setVenvStatus("安装 openakita 中（pip）...");
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
        return `openakita${extrasPart}`;
      })();
      await invoke("pip_install", {
        venvDir,
        packageSpec: spec,
        indexUrl: indexUrl.trim() ? indexUrl.trim() : null,
      });
      setOpenakitaInstalled(true);
      setVenvStatus(`安装完成：${spec}`);
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
    setApiKeyEnv(selectedProvider.api_key_env_suggestion || envKeyFromSlug(selectedProvider.slug));
  }, [selectedProvider]);

  async function doFetchModels() {
    setError(null);
    setModels([]);
    setSelectedModelId("");
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
      setSelectedModelId(parsed[0]?.id ?? "");
      setNotice(`拉取到模型：${parsed.length} 个`);
    } finally {
      setBusy(null);
    }
  }

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
    setBusy("写入端点配置...");
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
        const name = `${providerSlug || "provider"}-${selectedModelId}`.slice(0, 64);
        const caps = models.find((m) => m.id === selectedModelId)?.capabilities ?? {};
        const capList = Object.entries(caps)
          .filter(([, v]) => v)
          .map(([k]) => k);

        const endpoint = {
          name,
          provider: providerSlug || (selectedProvider?.slug ?? "custom"),
          api_type: apiType,
          base_url: baseUrl,
          api_key_env: apiKeyEnv.trim(),
          model: selectedModelId,
          priority: 0,
          max_tokens: 8192,
          timeout: 180,
          capabilities: capList,
        };

        // 若同名则替换，否则追加
        const idx = base.endpoints.findIndex((e: any) => e?.name === name);
        if (idx >= 0) base.endpoints[idx] = endpoint;
        else base.endpoints.unshift(endpoint);

        return JSON.stringify(base, null, 2) + "\n";
      })();

      await invoke("workspace_write_file", {
        workspaceId: currentWorkspaceId,
        relativePath: "data/llm_endpoints.json",
        content: next,
      });

      setNotice("端点已写入：data/llm_endpoints.json（同时已写入 API Key 到 .env）");
    } finally {
      setBusy(null);
    }
  }

  async function saveEnvKeys(keys: string[]) {
    if (!currentWorkspaceId) throw new Error("未设置当前工作区");
    await ensureEnvLoaded(currentWorkspaceId);
    const entries = keys.map((k) => ({ key: k, value: envDraft[k] ?? "" }));
    await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries });
  }

  const providerApplyUrl = useMemo(() => {
    const slug = (selectedProvider?.slug || "").toLowerCase();
    const map: Record<string, string> = {
      openai: "https://platform.openai.com/api-keys",
      anthropic: "https://console.anthropic.com/settings/keys",
      moonshot: "https://platform.moonshot.cn/",
      kimi: "https://platform.moonshot.cn/",
      dashscope: "https://dashscope.console.aliyun.com/",
      minimax: "https://www.minimaxi.com/",
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
    if (stepId === "status") {
      setStepId("welcome");
      return;
    }
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
    if (stepId === "llm" && (!selectedModelId || models.length === 0)) {
      setError("请先拉取模型列表并选择一个模型，然后写入端点配置。");
      return;
    }
    setStepId(steps[Math.min(currentStepIdx + 1, steps.length - 1)].id);
  }

  function goPrev() {
    setNotice(null);
    setError(null);
    if (stepId === "welcome") {
      setStepId("status");
      return;
    }
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
      } catch {
        setSkillSummary(null);
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

  const headerRight = (
    <div className="row">
      <span className="pill">
        当前工作区：<b>{currentWorkspaceId || "未设置"}</b>
      </span>
      <span className="pill">
        venv：<span>{venvDir || "—"}</span>
      </span>
      <button onClick={() => refreshAll()} disabled={!!busy}>
        刷新
      </button>
    </div>
  );

  const StepDot = ({ idx, isDone }: { idx: number; isDone: boolean }) => (
    <div className={`stepDot ${isDone ? "stepDotDone" : ""}`}>{isDone ? "✓" : idx + 1}</div>
  );

  function renderStatus() {
    const ws = workspaces.find((w) => w.id === currentWorkspaceId) || null;
    const im = [
      { k: "TELEGRAM_ENABLED", name: "Telegram", required: ["TELEGRAM_BOT_TOKEN"] },
      { k: "FEISHU_ENABLED", name: "飞书", required: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"] },
      { k: "WEWORK_ENABLED", name: "企业微信", required: ["WEWORK_CORP_ID", "WEWORK_AGENT_ID", "WEWORK_SECRET"] },
      { k: "DINGTALK_ENABLED", name: "钉钉", required: ["DINGTALK_APP_KEY", "DINGTALK_APP_SECRET"] },
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
              CLI 用户也可使用：`openakita daemon start --workspace-dir &lt;工作区&gt;`
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
                  disabled={!currentWorkspaceId || !!busy}
                  onClick={async () => {
                    if (!currentWorkspaceId) return;
                    setBusy("启动后台服务...");
                    setError(null);
                    try {
                      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_start", {
                        venvDir,
                        workspaceId: currentWorkspaceId,
                      });
                      setServiceStatus(ss);
                      setNotice("后台服务已启动（openakita serve）");
                    } catch (e) {
                      setError(String(e));
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  启动服务
                </button>
                <button
                  className="btnDanger"
                  disabled={!currentWorkspaceId || !!busy}
                  onClick={async () => {
                    if (!currentWorkspaceId) return;
                    setBusy("停止后台服务...");
                    setError(null);
                    try {
                      const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_stop", {
                        workspaceId: currentWorkspaceId,
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
                      <br />
                      api_key_env: {e.keyEnv || "—"}
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
    return (
      <>
        <div className="card">
          <div className="cardTitle">venv + 安装 openakita</div>
          <div className="cardHint">这一步会在固定目录创建 venv：`~/.openakita/venv`，并安装 `openakita[extras]`。</div>
          <div className="divider" />
          <div className="field">
            <div className="labelRow">
              <div className="label">安装来源</div>
              <div className="help">如果 PyPI 版本不包含 Setup Center 模块，可切到 GitHub / 本地源码</div>
            </div>
            <div className="btnRow">
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
          </div>

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

          <div className="grid2">
            <div className="field">
              <div className="labelRow">
                <div className="label">extras</div>
                <div className="help">建议 `all`（跨平台安全）</div>
              </div>
              <input value={extras} onChange={(e) => setExtras(e.target.value)} placeholder="all / windows / whisper / browser / feishu ..." />
            </div>
            <div className="field">
              <div className="labelRow">
                <div className="label">pip 源（可切换）</div>
                <div className="help">选择国内镜像会自动填充 index-url</div>
              </div>
              <select
                value={pipIndexPresetId}
                onChange={(e) => {
                  const id = e.target.value as "official" | "tuna" | "aliyun" | "custom";
                  const preset = PIP_INDEX_PRESETS.find((p) => p.id === id);
                  if (!preset) return;
                  if (id === "custom") return;
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
                value={indexUrl}
                onChange={(e) => setIndexUrl(e.target.value)}
                placeholder="自定义 index-url（可选）"
                disabled={pipIndexPresetId !== "custom"}
              />
            </div>
          </div>
          <div className="btnRow" style={{ marginTop: 12 }}>
            <button className="btnPrimary" onClick={doSetupVenvAndInstallOpenAkita} disabled={!canUsePython || !!busy}>
              一键创建 venv 并安装 openakita
            </button>
          </div>
          {venvStatus ? <div className="okBox">{venvStatus}</div> : null}
          <div className="okBox">下一步：进入“LLM 端点”，读取服务商列表并拉取模型。</div>
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
          <div className="btnRow">
            <button className="btnPrimary" onClick={doLoadProviders} disabled={!!busy}>
              读取服务商列表
            </button>
            <span className="statusLine">（需要先在 venv 安装 openakita）</span>
          </div>

          {providers.length > 0 ? (
            <div style={{ marginTop: 12 }}>
              <div className="grid2">
                <div className="field">
                  <div className="labelRow">
                    <div className="label">服务商</div>
                    <div className="help">支持 OpenAI / Anthropic 协议</div>
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

                <div className="field">
                  <div className="labelRow">
                    <div className="label">协议与 Base URL</div>
                    <div className="help">可手动改（如中转/私有网关）</div>
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

              <div className="grid2" style={{ marginTop: 12 }}>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">API Key 环境变量名（写入 .env）</div>
                    <div className="help">端点会引用它（api_key_env）</div>
                  </div>
                  <input value={apiKeyEnv} onChange={(e) => setApiKeyEnv(e.target.value)} placeholder="例如：OPENAI_API_KEY" />
                </div>
                <div className="field">
                  <div className="labelRow">
                    <div className="label">API Key 值</div>
                    <div className="help">仅用于当前拉取/写入本地工作区</div>
                  </div>
                  <input value={apiKeyValue} onChange={(e) => setApiKeyValue(e.target.value)} placeholder="sk-..." type="password" />
                </div>
              </div>

              <div className="btnRow" style={{ marginTop: 12 }}>
                <button onClick={doFetchModels} className="btnPrimary" disabled={!apiKeyValue.trim() || !baseUrl.trim() || !!busy}>
                  拉取模型列表
                </button>
              </div>

              {models.length > 0 ? (
                <div className="card" style={{ marginTop: 12 }}>
                  <div className="labelRow">
                    <div className="label">选择模型</div>
                    <div className="help">会写入 data/llm_endpoints.json</div>
                  </div>
                  <div className="row" style={{ marginTop: 8 }}>
                    <select value={selectedModelId} onChange={(e) => setSelectedModelId(e.target.value)} style={{ minWidth: 520 }}>
                      {models.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.id}
                        </option>
                      ))}
                    </select>
                    <button className="btnPrimary" onClick={doSaveEndpoint} disabled={!currentWorkspaceId || !!busy}>
                      写入端点配置
                    </button>
                  </div>
                  <div className="help" style={{ marginTop: 8 }}>
                    capabilities：
                    {Object.entries(models.find((m) => m.id === selectedModelId)?.capabilities ?? {})
                      .filter(([, v]) => v)
                      .map(([k]) => k)
                      .join(", ") || "（未知）"}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="okBox">下一步：进入“工具与集成”，把 IM/MCP/桌面/代理等配置一次性写入工作区 .env。</div>
        </div>
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
    return (
      <div className="field">
        <div className="labelRow">
          <div className="label">{label}</div>
          {k ? <div className="help">{k}</div> : null}
        </div>
        <input
          value={envGet(envDraft, k)}
          onChange={(e) => setEnvDraft((m) => envSet(m, k, e.target.value))}
          placeholder={placeholder}
          type={type || "text"}
        />
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

  function renderIntegrations() {
    const keysCore = [
      // LLM envs (optional, referenced by endpoints)
      "ANTHROPIC_API_KEY",
      "ANTHROPIC_BASE_URL",
      "YUNWU_API_KEY",
      "KIMI_API_KEY",
      "KIMI_BASE_URL",
      "KIMI_MODEL",
      "DASHSCOPE_API_KEY",
      "DASHSCOPE_BASE_URL",
      "DASHSCOPE_MODEL",
      "DASHSCOPE_IMAGE_API_URL",
      "MINIMAX_API_KEY",
      "MINIMAX_BASE_URL",
      "MINIMAX_MODEL",
      "DEEPSEEK_API_KEY",
      "OPENROUTER_API_KEY",
      "SILICONFLOW_API_KEY",
      "LLM_ENDPOINTS_CONFIG",
      // network/proxy
      "HTTP_PROXY",
      "HTTPS_PROXY",
      "ALL_PROXY",
      "FORCE_IPV4",
      // basic model defaults
      "DEFAULT_MODEL",
      "MAX_TOKENS",
      // agent
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
      // memory/embedding
      "EMBEDDING_MODEL",
      "EMBEDDING_DEVICE",
      "MEMORY_HISTORY_DAYS",
      "MEMORY_MAX_HISTORY_FILES",
      "MEMORY_MAX_HISTORY_SIZE_MB",
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
      "DINGTALK_ENABLED",
      "DINGTALK_APP_KEY",
      "DINGTALK_APP_SECRET",
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
            这一页会把项目里常用的开关与参数全部集中起来（覆盖 `.env.example` + MCP 文档 + 桌面自动化配置）。你可以分块填写，最后统一写入工作区 `.env`。
          </div>
          <div className="divider" />

          <div className="card" style={{ marginTop: 0 }}>
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              LLM 环境变量（可选，但推荐在这里集中管理）
            </div>
            <div className="cardHint">
              这些 Key 通常会被 `data/llm_endpoints.json` 的 `api_key_env` 引用。你也可以只在“LLM 端点”步骤里写入单个 Key。
            </div>
            <div className="divider" />
            <div className="grid2">
              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  Anthropic / 云雾中转
                </div>
                <FieldText k="ANTHROPIC_API_KEY" label="ANTHROPIC_API_KEY" type="password" />
                <FieldText k="ANTHROPIC_BASE_URL" label="ANTHROPIC_BASE_URL" placeholder="https://api.anthropic.com" />
                <div className="divider" />
                <FieldText k="YUNWU_API_KEY" label="YUNWU_API_KEY" type="password" />
              </div>
              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  Kimi / DashScope / 其它
                </div>
                <FieldText k="KIMI_API_KEY" label="KIMI_API_KEY" type="password" />
                <div className="grid2" style={{ marginTop: 10 }}>
                  <FieldText k="KIMI_BASE_URL" label="KIMI_BASE_URL" placeholder="https://api.moonshot.cn/v1" />
                  <FieldText k="KIMI_MODEL" label="KIMI_MODEL" placeholder="kimi-k2.5" />
                </div>
                <div className="divider" />
                <FieldText k="DASHSCOPE_API_KEY" label="DASHSCOPE_API_KEY" type="password" />
                <div className="grid2" style={{ marginTop: 10 }}>
                  <FieldText k="DASHSCOPE_BASE_URL" label="DASHSCOPE_BASE_URL" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
                  <FieldText k="DASHSCOPE_MODEL" label="DASHSCOPE_MODEL" placeholder="qwen3-max" />
                </div>
                <FieldText
                  k="DASHSCOPE_IMAGE_API_URL"
                  label="DASHSCOPE_IMAGE_API_URL"
                  placeholder="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
                />
              </div>
            </div>

            <div className="grid2" style={{ marginTop: 12 }}>
              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  MiniMax / DeepSeek
                </div>
                <FieldText k="MINIMAX_API_KEY" label="MINIMAX_API_KEY" type="password" />
                <div className="grid2" style={{ marginTop: 10 }}>
                  <FieldText k="MINIMAX_BASE_URL" label="MINIMAX_BASE_URL" placeholder="https://api.minimaxi.com/anthropic" />
                  <FieldText k="MINIMAX_MODEL" label="MINIMAX_MODEL" placeholder="MiniMax-M2.1" />
                </div>
                <div className="divider" />
                <FieldText k="DEEPSEEK_API_KEY" label="DEEPSEEK_API_KEY" type="password" />
              </div>
              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  OpenRouter / SiliconFlow / 自定义端点文件
                </div>
                <FieldText k="OPENROUTER_API_KEY" label="OPENROUTER_API_KEY" type="password" />
                <FieldText k="SILICONFLOW_API_KEY" label="SILICONFLOW_API_KEY" type="password" />
                <div className="divider" />
                <FieldText k="LLM_ENDPOINTS_CONFIG" label="LLM_ENDPOINTS_CONFIG" placeholder="data/llm_endpoints.json" help="默认无需改；用于自定义端点配置文件位置" />
              </div>
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
            <div className="grid2">
              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  Telegram
                </div>
                <FieldBool k="TELEGRAM_ENABLED" label="启用 Telegram" />
                <div className="divider" />
                <FieldText k="TELEGRAM_BOT_TOKEN" label="Bot Token" placeholder="your-telegram-bot-token" type="password" />
                <FieldText k="TELEGRAM_PROXY" label="Proxy（可选）" placeholder="http://127.0.0.1:7890 / socks5://..." />
                <div className="help">
                  申请指南：<a href="https://t.me/BotFather">https://t.me/BotFather</a>
                </div>
              </div>

              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  飞书（extra: openakita[feishu]）
                </div>
                <FieldBool k="FEISHU_ENABLED" label="启用飞书" />
                <div className="divider" />
                <FieldText k="FEISHU_APP_ID" label="APP_ID" placeholder="" />
                <FieldText k="FEISHU_APP_SECRET" label="APP_SECRET" placeholder="" type="password" />
              </div>
            </div>

            <div className="grid2" style={{ marginTop: 12 }}>
              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  企业微信
                </div>
                <FieldBool k="WEWORK_ENABLED" label="启用企业微信" />
                <div className="divider" />
                <div className="grid3">
                  <FieldText k="WEWORK_CORP_ID" label="CORP_ID" />
                  <FieldText k="WEWORK_AGENT_ID" label="AGENT_ID" />
                  <FieldText k="WEWORK_SECRET" label="SECRET" type="password" />
                </div>
              </div>

              <div className="card" style={{ marginTop: 0 }}>
                <div className="label" style={{ marginBottom: 8 }}>
                  钉钉 / QQ(OneBot)
                </div>
                <FieldBool k="DINGTALK_ENABLED" label="启用钉钉" />
                <div className="grid2" style={{ marginTop: 10 }}>
                  <FieldText k="DINGTALK_APP_KEY" label="DINGTALK_APP_KEY" />
                  <FieldText k="DINGTALK_APP_SECRET" label="DINGTALK_APP_SECRET" type="password" />
                </div>
                <div className="divider" />
                <FieldBool k="QQ_ENABLED" label="启用 QQ（OneBot）" />
                <FieldText k="QQ_ONEBOT_URL" label="QQ_ONEBOT_URL" placeholder="ws://127.0.0.1:8080" />
              </div>
            </div>
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
              Agent / 记忆 / 调度 / 多 Agent
            </div>
            <div className="grid3">
              <FieldText k="AGENT_NAME" label="AGENT_NAME" placeholder="OpenAkita" />
              <FieldText k="MAX_ITERATIONS" label="MAX_ITERATIONS" placeholder="300" />
              <FieldBool k="AUTO_CONFIRM" label="AUTO_CONFIRM" help="自动确认（慎用）" />
            </div>
            <div className="divider" />
            <div className="grid3">
              <FieldText k="EMBEDDING_MODEL" label="EMBEDDING_MODEL" placeholder="shibing624/text2vec-base-chinese" />
              <FieldText k="EMBEDDING_DEVICE" label="EMBEDDING_DEVICE" placeholder="cpu / cuda" />
              <FieldText k="MEMORY_HISTORY_DAYS" label="MEMORY_HISTORY_DAYS" placeholder="30" />
            </div>
            <div className="grid3" style={{ marginTop: 10 }}>
              <FieldText k="MEMORY_MAX_HISTORY_FILES" label="MEMORY_MAX_HISTORY_FILES" placeholder="1000" />
              <FieldText k="MEMORY_MAX_HISTORY_SIZE_MB" label="MEMORY_MAX_HISTORY_SIZE_MB" placeholder="500" />
              <FieldText k="SESSION_MAX_HISTORY" label="SESSION_MAX_HISTORY" placeholder="50" />
            </div>
            <div className="divider" />
            <div className="grid3">
              <FieldBool k="SCHEDULER_ENABLED" label="SCHEDULER_ENABLED" />
              <FieldText k="SCHEDULER_TIMEZONE" label="SCHEDULER_TIMEZONE" placeholder="Asia/Shanghai" />
              <FieldText k="SCHEDULER_MAX_CONCURRENT" label="SCHEDULER_MAX_CONCURRENT" placeholder="5" />
            </div>
            <div className="grid3" style={{ marginTop: 10 }}>
              <FieldBool k="ORCHESTRATION_ENABLED" label="ORCHESTRATION_ENABLED" help="Master/Worker 架构" />
              <FieldText k="ORCHESTRATION_BUS_ADDRESS" label="BUS_ADDRESS" placeholder="tcp://127.0.0.1:5555" />
              <FieldText k="ORCHESTRATION_PUB_ADDRESS" label="PUB_ADDRESS" placeholder="tcp://127.0.0.1:5556" />
            </div>
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
          <div className="cardTitle">完成：下一步引导</div>
          <div className="cardHint">你已经把基础链路跑通。下面是“下一步该怎么做”的清单，按优先级从上到下。</div>
          <div className="divider" />
          <div className="grid2">
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">1) 检查生成文件</div>
              <div className="cardHint" style={{ marginTop: 8 }}>
                工作区目录：<b>{ws?.path || "（未选择）"}</b>
                <br />
                - `.env`（已写入你的 key/开关）
                <br />
                - `data/llm_endpoints.json`（端点列表）
                <br />- `identity/SOUL.md`（Agent 设定）
              </div>
            </div>
            <div className="card" style={{ marginTop: 0 }}>
              <div className="label">2) 运行/验证建议</div>
              <div className="cardHint" style={{ marginTop: 8 }}>
                - 用该工作区运行一次 CLI（验证端点/工具）
                <br />
                - 如果启用 MCP Browser：记得安装 Playwright 浏览器
                <br />- Windows 桌面工具：确保安装 `openakita[windows]`
              </div>
            </div>
          </div>
          <div className="divider" />
          <div className="card">
            <div className="label">3) 打包发布（你要的“下一步”）</div>
            <div className="cardHint" style={{ marginTop: 8 }}>
              - Windows：`npm run tauri build` 生成 `.exe`
              <br />
              - macOS：同样 `npm run tauri build` 生成 `.app`（后续可做签名/公证）
              <br />- Linux：生成对应包（取决于系统打包工具）
            </div>
            <div className="okBox">
              我下一步会把“打包脚本 + 产物路径说明 + 发布 checklist”补齐，并把内置 Python 做成可固定版本/可回滚的安装策略。
            </div>
          </div>

          <div className="divider" />
          <div className="card">
            <div className="label">4) 卸载 / 清理（可选）</div>
            <div className="cardHint" style={{ marginTop: 8 }}>
              - “卸载 openakita”只影响 venv 内的 Python 包
              <br />
              - “删除运行环境”会删除 `~/.openakita/venv` 与 `~/.openakita/runtime`（会丢失已安装依赖与内置 Python），但**保留 workspaces 配置**
            </div>
            <div className="divider" />
            <label className="pill" style={{ cursor: "pointer" }}>
              <input
                style={{ width: 16, height: 16 }}
                type="checkbox"
                checked={dangerAck}
                onChange={(e) => setDangerAck(e.target.checked)}
              />
              我已了解：删除运行环境是不可逆操作
            </label>
            <div className="btnRow" style={{ marginTop: 10 }}>
              <button onClick={uninstallOpenAkita} disabled={!!busy}>
                卸载 openakita（venv）
              </button>
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
    switch (stepId) {
      case "status":
        return renderStatus();
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
      case "integrations":
        return renderIntegrations();
      case "finish":
        return renderFinish();
      default:
        return renderStatus();
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
              第 {currentStepIdx + 1} 步 / {steps.length} 步：{step.title}
            </div>
            <div className="statusLine">{step.desc}</div>
          </div>
          {headerRight}
        </div>

        <div className="content">
          {busy ? <div className="okBox">正在处理：{busy}</div> : null}
          {notice ? <div className="okBox">{notice}</div> : null}
          {error ? <div className="errorBox">{error}</div> : null}
          {renderStepContent()}
        </div>

        <div className="footer">
          <div className="statusLine">
            提示：先按顺序走完，再回头微调参数会更快。{stepId === "integrations" ? "（这一页是“全覆盖”的总开关面板）" : ""}
          </div>
          <div className="btnRow">
            <button onClick={goPrev} disabled={isFirst || !!busy}>
              上一步
            </button>
            <button className="btnPrimary" onClick={goNext} disabled={isLast || !!busy}>
              下一步
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

