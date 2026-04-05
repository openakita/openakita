import { createContext, useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { invoke, listen, IS_TAURI, IS_WEB, IS_CAPACITOR, IS_LOCAL_WEB, getAppVersion, onWsEvent, reconnectWsNow, logger, registerGlobalShortcut } from "./platform";
import { getActiveServer, getActiveServerId } from "./platform/servers";
import { checkAuth, installFetchInterceptor, AUTH_EXPIRED_EVENT, isPasswordUserSet, clearAccessToken, setTauriRemoteMode, isTauriRemoteMode } from "./platform/auth";
import { LoginView } from "./views/LoginView";
import { ServerManagerView } from "./views/ServerManagerView";
import { ChatView } from "./views/ChatView";

// Lazy-loaded views — keeps first-screen bundle small (4.7 Code Splitting)
const SkillManager = lazy(() => import("./views/SkillManager").then(m => ({ default: m.SkillManager })));
const IMView = lazy(() => import("./views/IMView").then(m => ({ default: m.IMView })));
const TokenStatsView = lazy(() => import("./views/TokenStatsView").then(m => ({ default: m.TokenStatsView })));
const MCPView = lazy(() => import("./views/MCPView").then(m => ({ default: m.MCPView })));
const PluginManagerView = lazy(() => import("./views/PluginManagerView"));
const SchedulerView = lazy(() => import("./views/SchedulerView").then(m => ({ default: m.SchedulerView })));
const MemoryView = lazy(() => import("./views/MemoryView").then(m => ({ default: m.MemoryView })));
const IdentityView = lazy(() => import("./views/IdentityView").then(m => ({ default: m.IdentityView })));
const AgentDashboardView = lazy(() => import("./views/AgentDashboardView").then(m => ({ default: m.AgentDashboardView })));
const AgentManagerView = lazy(() => import("./views/AgentManagerView").then(m => ({ default: m.AgentManagerView })));
const OrgEditorView = lazy(() => import("./views/OrgEditorView").then(m => ({ default: m.OrgEditorView })));
const PixelOfficeView = lazy(() => import("./views/PixelOfficeView").then(m => ({ default: m.PixelOfficeView })));
const AgentStoreView = lazy(() => import("./views/AgentStoreView").then(m => ({ default: m.AgentStoreView })));
const SkillStoreView = lazy(() => import("./views/SkillStoreView").then(m => ({ default: m.SkillStoreView })));
const SecurityView = lazy(() => import("./views/SecurityView"));
const PetView = lazy(() => import("./views/PetView").then(m => ({ default: m.PetView })));

import { FeedbackModal } from "./views/FeedbackModal";
import { IMConfigView } from "./views/IMConfigView";
import { AgentSystemView } from "./views/AgentSystemView";
import { LLMView } from "./views/LLMView";
import { StatusView } from "./views/StatusView";
import type {
  EndpointSummary as EndpointSummaryType,
  PlatformInfo, WorkspaceSummary, ProviderInfo,
  EndpointDraft, PythonCandidate, BundledPythonInstallResult, InstallSource,
  EnvMap, StepId, Step, ViewId,
} from "./types";
import {
  IconCheckCircle, IconXCircle, IconInfo,
} from "./icons";
import { ChevronRight, ChevronDown, Loader2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import logoUrl from "./assets/logo.png";
import "highlight.js/styles/github.css";
import { getThemePref, setThemePref, THEME_CHANGE_EVENT, type Theme } from "./theme";
import { copyToClipboard } from "./utils/clipboard";
import { BUILTIN_PROVIDERS, PIP_INDEX_PRESETS } from "./constants";
import { safeFetch } from "./providers";
import {
  slugify, joinPath, toFileUrl,
  envGet, envSet,
} from "./utils";
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
import { CliManager } from "./components/CliManager";
import { WebPasswordManager } from "./components/WebPasswordManager";
import { FieldText, FieldBool, FieldSelect, FieldCombo, FieldSlider, TelegramPairingCodeHint } from "./components/EnvFields";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { ModalOverlay } from "./components/ModalOverlay";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { useNotifications } from "./hooks/useNotifications";
import { notifySuccess, notifyError, notifyLoading, dismissLoading } from "./utils/notify";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import { useVersionCheck } from "./hooks/useVersionCheck";
import { useEnvManager } from "./hooks/useEnvManager";
import { AdvancedView } from "./views/AdvancedView";

const THEME_I18N_KEYS: Record<Theme, string> = {
  system: "topbar.themeSystem",
  dark: "topbar.themeDark",
  light: "topbar.themeLight",
  "daltonized-light": "topbar.themeDaltonizedLight",
  "daltonized-dark": "topbar.themeDaltonizedDark",
  "high-contrast": "topbar.themeHighContrast",
};

/** Health-check timeout for recurring monitoring (heartbeat + refreshStatus).
 *  Startup/one-shot probes keep their own shorter timeouts.
 *  5s accommodates slow devices where the event loop may be busy. */
const HEALTH_POLL_TIMEOUT_MS = 5_000;

interface EnvFieldCtx {
  envDraft: EnvMap;
  setEnvDraft: React.Dispatch<React.SetStateAction<EnvMap>>;
  secretShown: Record<string, boolean>;
  setSecretShown: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  busy: string | null;
  t: (key: string, opts?: Record<string, unknown>) => string;
}

const EnvFieldContext = createContext<EnvFieldCtx | null>(null);

const _HASH_TO_VIEW: Record<string, ViewId> = {
  "chat": "chat", "im": "im", "skills": "skills", "mcp": "mcp",
  "scheduler": "scheduler", "memory": "memory", "status": "status",
  "token-stats": "token_stats", "identity": "identity",
  "dashboard": "dashboard", "org-editor": "org_editor",
  "pixel-office": "pixel_office",
  "agent-manager": "agent_manager", "agent-store": "agent_store",
  "skill-store": "skill_store", "wizard": "wizard", "docs": "docs",
  "security": "security",
};

const _VIEW_TO_HASH: Record<string, string> = Object.fromEntries(
  Object.entries(_HASH_TO_VIEW).map(([k, v]) => [v, k]),
);

const _HASH_TO_STEP: Record<string, StepId> = {
  "llm": "llm", "im": "im", "tools": "tools", "agent": "agent", "advanced": "advanced",
};

function _parseHashRoute(hash: string): { view: ViewId; stepId?: StepId } | null {
  const path = hash.replace(/^#\/?/, "");
  if (!path) return null;
  if (_HASH_TO_VIEW[path]) return { view: _HASH_TO_VIEW[path] };
  if (path.startsWith("config/")) {
    const step = path.slice(7);
    if (_HASH_TO_STEP[step]) return { view: "wizard", stepId: _HASH_TO_STEP[step] as StepId };
  }
  return null;
}

function _viewToHash(view: string, stepId?: string): string {
  if (view === "wizard" && stepId) {
    return `#/config/${stepId}`;
  }
  return _VIEW_TO_HASH[view] ? `#/${_VIEW_TO_HASH[view]}` : "";
}

export function App() {
  if (window.location.pathname === '/pet') {
    return <Suspense fallback={null}><PetView /></Suspense>;
  }

  const { t, i18n } = useTranslation();

  // ── Web / Capacitor auth gate ──
  // IS_LOCAL_WEB: hostname is 127.0.0.1/localhost/::1 — backend authenticates
  // by client IP, no tokens or round-trips needed.  This eliminates the entire
  // class of "checkAuth timeout → login page flash" bugs.
  const needsRemoteAuth = (IS_WEB || IS_CAPACITOR) && !IS_LOCAL_WEB;
  const [webAuthed, setWebAuthed] = useState(!needsRemoteAuth);
  const [authChecking, setAuthChecking] = useState(needsRemoteAuth);
  const [showPwBanner, setShowPwBanner] = useState(false);
  const [showServerManager, setShowServerManager] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);
  const [needServerConfig, setNeedServerConfig] = useState(
    () => IS_CAPACITOR && !getActiveServer(),
  );
  // Tauri remote auth: when Tauri desktop connects to a remote backend that requires login
  const [tauriRemoteLoginUrl, setTauriRemoteLoginUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!needsRemoteAuth) {
      // Local web: non-blocking fetch for password-banner check only
      if (IS_LOCAL_WEB) {
        fetch("/api/auth/check", { signal: AbortSignal.timeout(5000) })
          .then((r) => r.json())
          .then((data) => {
            if (data.password_user_set === false && !localStorage.getItem("openakita_pw_banner_dismissed")) {
              setShowPwBanner(true);
            }
          })
          .catch(() => {});
      }
      return;
    }
    if (IS_CAPACITOR && !getActiveServer()) {
      setAuthChecking(false);
      return;
    }
    checkAuth(IS_CAPACITOR ? (getActiveServer()?.url || "") : "").then((ok) => {
      if (ok) {
        installFetchInterceptor();
        if (!isPasswordUserSet() && !localStorage.getItem("openakita_pw_banner_dismissed")) {
          setShowPwBanner(true);
        }
      }
      setWebAuthed(ok);
      setAuthChecking(false);
    });
    const onExpired = () => setWebAuthed(false);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps


  // ── Mobile keyboard: track visual viewport for reliable height ──
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () => {
      document.documentElement.style.setProperty('--app-height', `${vv.height}px`);
      if (Math.abs(vv.height - window.innerHeight) < 1) {
        window.scrollTo(0, 0);
      }
    };
    update();
    vv.addEventListener('resize', update);
    vv.addEventListener('scroll', update);
    return () => {
      vv.removeEventListener('resize', update);
      vv.removeEventListener('scroll', update);
    };
  }, []);

  const [themePrefState, setThemePrefState] = useState<Theme>(getThemePref());
  useEffect(() => {
    const handler = (e: Event) => setThemePrefState((e as CustomEvent<Theme>).detail);
    window.addEventListener(THEME_CHANGE_EVENT, handler);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, handler);
  }, []);
  const [info, setInfo] = useState<PlatformInfo | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(null);
  const { confirmDialog, setConfirmDialog, askConfirm } = useNotifications();
  const busy: string | null = null;
  const [dangerAck, setDangerAck] = useState(false);

  // ── Restart overlay state ──
  const [restartOverlay, setRestartOverlay] = useState<{ phase: "saving" | "restarting" | "waiting" | "done" | "fail" | "notRunning" } | null>(null);


  // ── Service conflict & version state ──
  const [conflictDialog, setConflictDialog] = useState<{ pid: number; version: string } | null>(null);
  const [pendingStartWsId, setPendingStartWsId] = useState<string | null>(null); // workspace ID waiting for conflict resolution
  const {
    desktopVersion, backendVersion, setBackendVersion,
    versionMismatch, setVersionMismatch,
    newRelease, setNewRelease,
    updateAvailable, setUpdateAvailable, updateProgress, setUpdateProgress,
    checkVersionMismatch, checkForAppUpdate,
    doDownloadAndInstall, doRelaunchAfterUpdate,
  } = useVersionCheck();

  // ── 独立初始化 autostart 状态（不依赖 refreshStatus 的复杂前置条件，Web 跳过） ──
  useEffect(() => {
    if (IS_WEB) return;
    invoke<boolean>("autostart_is_enabled")
      .then((en) => setAutostartEnabled(en))
      .catch(() => setAutostartEnabled(null));
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

  useEffect(() => {
    const onResize = () => {
      const w = window.innerWidth;
      const mobile = w <= 768;
      setIsMobile(mobile);
      if (!mobile) setMobileSidebarOpen(false);
      if (!mobile && w <= 980) {
        if (!sidebarAutoCollapsed.current) {
          sidebarAutoCollapsed.current = true;
          setSidebarCollapsed(true);
        }
      } else if (w > 980 && sidebarAutoCollapsed.current) {
        sidebarAutoCollapsed.current = false;
        setSidebarCollapsed(false);
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const steps: Step[] = useMemo(
    () => [
      { id: "llm" as StepId, title: t("config.step.endpoints"), desc: t("config.step.endpointsDesc") },
      { id: "im" as StepId, title: t("config.imTitle"), desc: t("config.step.imDesc") },
      { id: "tools" as StepId, title: t("config.step.tools"), desc: t("config.step.toolsDesc") },
      { id: "agent" as StepId, title: t("config.step.agent"), desc: t("config.step.agentDesc") },
      { id: "advanced" as StepId, title: t("config.step.advanced"), desc: t("config.step.advancedDesc") },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t],
  );

  const [view, setView] = useState<ViewId>(() => {
    const parsed = _parseHashRoute(window.location.hash);
    if (parsed) return parsed.view;
    return (IS_WEB || IS_CAPACITOR) ? "chat" : "wizard";
  });
  const [appInitializing, setAppInitializing] = useState(!(IS_WEB || IS_CAPACITOR));
  const [configExpanded, setConfigExpanded] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const sidebarAutoCollapsed = useRef(false);
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth <= 768);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [bugReportOpen, setBugReportOpen] = useState(false);
  const [disabledViews, setDisabledViews] = useState<string[]>([]);
  const [multiAgentEnabled, setMultiAgentEnabled] = useState(false);
  const [storeVisible, setStoreVisible] = useState(() => localStorage.getItem("openakita_storeVisible") === "true");
  // ── Hash-based deep link routing ──
  useEffect(() => {
    const onHashChange = () => {
      const parsed = _parseHashRoute(window.location.hash);
      if (parsed) {
        setView(parsed.view);
        if (parsed.stepId) setStepId(parsed.stepId);
      }
    };
    // Listen for postMessage from embedded docs iframe (cross-origin safe)
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type === "openakita-navigate" && typeof e.data.hash === "string") {
        window.location.hash = e.data.hash;
      }
    };
    window.addEventListener("hashchange", onHashChange);
    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("hashchange", onHashChange);
      window.removeEventListener("message", onMessage);
    };
  }, []);

  // ── Data mode: "local" (Tauri commands) or "remote" (HTTP API) ──
  // Web mode always starts in "remote" since the backend is already running
  const [dataMode, setDataMode] = useState<"local" | "remote">((IS_WEB || IS_CAPACITOR) ? "remote" : "local");
  const [apiBaseUrl, setApiBaseUrl] = useState(() =>
    IS_CAPACITOR ? (getActiveServer()?.url || "")
    : IS_WEB ? ""
    : (localStorage.getItem("openakita_apiBaseUrl") || "http://127.0.0.1:18900"),
  );
  const [connectDialogOpen, setConnectDialogOpen] = useState(false);
  const [connectAddress, setConnectAddress] = useState("");

  // Tauri remote: listen for auth expiration and redirect to login
  useEffect(() => {
    if (!IS_TAURI) return;
    const onExpired = () => {
      if (isTauriRemoteMode()) {
        setTauriRemoteLoginUrl(apiBaseUrl);
      }
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, [apiBaseUrl]);

  const [stepId, setStepId] = useState<StepId>(() => {
    const parsed = _parseHashRoute(window.location.hash);
    return parsed?.stepId || "llm";
  });
  const currentStepIdxRaw = useMemo(() => steps.findIndex((s) => s.id === stepId), [steps, stepId]);
  const currentStepIdx = currentStepIdxRaw < 0 ? 0 : currentStepIdxRaw;

  useEffect(() => {
    if (stepId === "workspace") {
      invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>("get_root_dir_info")
        .then((info) => {
          setObCurrentRoot(info.currentRoot);
          if (info.customRoot) {
            setObCustomRootInput(info.customRoot);
            setObCustomRootApplied(true);
          }
        })
        .catch(() => {});
    }
  }, [stepId]);

  // ── Onboarding Wizard (首次安装引导) ──
  type OnboardingStep = "ob-welcome" | "ob-agreement" | "ob-llm" | "ob-im" | "ob-cli" | "ob-progress" | "ob-done";
  const [obStep, setObStep] = useState<OnboardingStep>("ob-welcome");
  const [obInstallLog, setObInstallLog] = useState<string[]>([]);
  const [obInstalling, setObInstalling] = useState(false);
  const [obEnvCheck, setObEnvCheck] = useState<{
    openakitaRoot: string;
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
  const [obAutostart, setObAutostart] = useState(true); // 开机自启，默认勾选
  const [obAgreementInput, setObAgreementInput] = useState("");

  // Custom root directory
  const [obShowCustomRoot, setObShowCustomRoot] = useState(false);
  const [obCustomRootInput, setObCustomRootInput] = useState("");
  const [obCustomRootApplied, setObCustomRootApplied] = useState(false);
  const [obCustomRootMigrate, setObCustomRootMigrate] = useState(false);
  const [obCurrentRoot, setObCurrentRoot] = useState("");
  const [obCustomRootBusy, setObCustomRootBusy] = useState(false);

  // Quick workspace switcher
  const [wsDropdownOpen, setWsDropdownOpen] = useState(false);
  const [wsQuickCreateOpen, setWsQuickCreateOpen] = useState(false);
  const [wsQuickName, setWsQuickName] = useState("");
  const [obAgreementError, setObAgreementError] = useState(false);

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
    if (!IS_TAURI) return;
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
      logger.error("App", "obConnectExistingService failed", { error: String(e) });
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
        } else {
          // 非首次启动：直接进入状态页面
          setView("status");
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
        logger.debug("App", "Force entering onboarding mode");
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
  const [indexUrl, setIndexUrl] = useState<string>("https://mirrors.aliyun.com/pypi/simple/");
  const [pipIndexPresetId, setPipIndexPresetId] = useState<"official" | "tuna" | "aliyun" | "custom">("aliyun");
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
  const [savedEndpoints, setSavedEndpoints] = useState<EndpointDraft[]>([]);
  const [savedCompilerEndpoints, setSavedCompilerEndpoints] = useState<EndpointDraft[]>([]);
  const [savedSttEndpoints, setSavedSttEndpoints] = useState<EndpointDraft[]>([]);

  // status panel data
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [endpointSummary, setEndpointSummary] = useState<
    { name: string; provider: string; apiType: string; baseUrl: string; model: string; keyEnv: string; keyPresent: boolean; enabled?: boolean }[]
  >([]);
  const [skillSummary, setSkillSummary] = useState<{ count: number; systemCount: number; externalCount: number } | null>(null);
  const [skillsDetail, setSkillsDetail] = useState<
    { skill_id: string; name: string; description: string; name_i18n?: Record<string, string> | null; description_i18n?: Record<string, string> | null; system: boolean; enabled?: boolean; tool_name?: string | null; category?: string | null; path?: string | null }[] | null
  >(null);
  const [skillsSelection, setSkillsSelection] = useState<Record<string, boolean>>({});
  const [skillsTouched, setSkillsTouched] = useState(false);
  const [autostartEnabled, setAutostartEnabled] = useState<boolean | null>(null);
  const [autoUpdateEnabled, setAutoUpdateEnabled] = useState<boolean | null>(null);
  // autoStartBackend 已合并到"开机自启"：--background 模式自动拉起后端，无需独立开关
  const [serviceStatus, setServiceStatus] = useState<{ running: boolean; pid: number | null; pidFile: string; port?: number } | null>(null);
  // 心跳状态机: "alive" | "suspect" | "degraded" | "dead"
  const [heartbeatState, setHeartbeatState] = useState<"alive" | "suspect" | "degraded" | "dead">("dead");
  const heartbeatStateRef = useRef<"alive" | "suspect" | "degraded" | "dead">("dead");
  const heartbeatFailCount = useRef(0);
  /** 连续成功次数，从 degraded/suspect 回到 alive 需至少 2 次，避免偶发超时导致绿黄反复横跳 */
  const heartbeatAliveSuccessCountRef = useRef(0);
  const wsRefreshDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pageVisible, setPageVisible] = useState(true);
  const visibilityGraceRef = useRef(false); // 休眠恢复宽限期
  const [detectedProcesses, setDetectedProcesses] = useState<Array<{ pid: number; cmd: string }>>([]);
  const [serviceLog, setServiceLog] = useState<{ path: string; content: string; truncated: boolean } | null>(null);
  const [serviceLogError, setServiceLogError] = useState<string | null>(null);
  const serviceLogRef = useRef<HTMLPreElement>(null);
  const logAtBottomRef = useRef(true);
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
  const {
    envDraft, setEnvDraft,
    secretShown, setSecretShown,
    ensureEnvLoaded, saveEnvKeys,
    resetEnvLoaded, markEnvLoaded,
  } = useEnvManager({
    currentWorkspaceId,
    shouldUseHttpApi,
    httpApiBase,
  });

  const envFieldCtx = useMemo<EnvFieldCtx>(() => ({
    envDraft, setEnvDraft, secretShown, setSecretShown, busy, t,
  }), [envDraft, secretShown, busy, t]);

  async function refreshAll() {
    if (IS_TAURI) {
      const res = await invoke<PlatformInfo>("get_platform_info");
      setInfo(res);
      const ws = await invoke<WorkspaceSummary[]>("list_workspaces");
      setWorkspaces(ws);
      const cur = await invoke<string | null>("get_current_workspace_id");
      setCurrentWorkspaceId(cur);
    } else {
      setInfo({ os: "web", arch: "", homeDir: "", openakitaRootDir: "" });
      if (!currentWorkspaceId) setCurrentWorkspaceId("default");
    }
  }

  // Web mode init: runs after auth is confirmed
  const webInitDone = useRef(false);
  useEffect(() => {
    if ((!IS_WEB && !IS_CAPACITOR) || !webAuthed || webInitDone.current) return;
    webInitDone.current = true;
    let cancelled = false;
    (async () => {
      await refreshAll();
      if (cancelled) return;
      const capBase = IS_CAPACITOR ? apiBaseUrl : "";
      if (!IS_CAPACITOR) setApiBaseUrl("");
      setServiceStatus({ running: true, pid: null, pidFile: "" });
      try {
        const hRes = await safeFetch(`${capBase}/api/health`, { signal: AbortSignal.timeout(3_000) });
        const hData = await hRes.json();
        if (hData.version) setBackendVersion(hData.version);
      } catch { /* ignore */ }
      // Explicitly fetch config that useCallback/useEffect chains may miss
      // due to auth not being ready when the initial effects fired
      try {
        const modeRes = await safeFetch(`${capBase}/api/config/agent-mode`);
        const modeData = await modeRes.json();
        if (!cancelled) setMultiAgentEnabled(modeData.multi_agent_enabled ?? false);
      } catch { /* ignore */ }
      try {
        const dvRes = await safeFetch(`${capBase}/api/config/disabled-views`);
        const dvData = await dvRes.json();
        if (!cancelled) setDisabledViews(dvData.disabled_views || []);
      } catch { /* ignore */ }
      try { await refreshStatus("local", capBase, true); } catch { /* ignore */ }
      autoCheckEndpoints(capBase);
    })();
    return () => { cancelled = true; };
  }, [webAuthed]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (IS_WEB) return;

        // ── Tauri 模式：完整初始化流程 ──
        try {
          const v = await getAppVersion();
          if (!cancelled) {
            setAppVersion(v);
            setSelectedPypiVersion(v);
          }
        } catch {
          // ignore
        }
        await refreshAll();
        if (!cancelled) {
          try {
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
            const raw = await readWorkspaceFile("data/llm_endpoints.json");
            const parsed = JSON.parse(raw);
            const eps = Array.isArray(parsed?.endpoints) ? parsed.endpoints : [];
            if (!cancelled && eps.length > 0) {
              setSavedEndpoints(eps.map((e: any) => ({
                name: String(e?.name || ""), provider: String(e?.provider || ""),
                api_type: String(e?.api_type || ""), base_url: String(e?.base_url || ""),
                model: String(e?.model || ""), api_key_env: String(e?.api_key_env || ""),
                priority: Number(e?.priority || 1),
                max_tokens: Number(e?.max_tokens ?? 0),
                context_window: Number(e?.context_window || 200000),
                timeout: Number(e?.timeout || 180),
                capabilities: Array.isArray(e?.capabilities) ? e.capabilities.map((x: any) => String(x)) : [],
                enabled: e?.enabled !== false,
              })));
            }
          } catch { /* ignore */ }

          if (!cancelled) {
            const localUrl = "http://127.0.0.1:18900";

            const connectToRunningService = async (url: string) => {
              const healthRes = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(3000) });
              if (!healthRes.ok) return false;
              if (cancelled) return true;
              const healthData = await healthRes.json();
              const svcVersion = healthData.version || "";
              setApiBaseUrl(url);
              setServiceStatus({ running: true, pid: healthData.pid || null, pidFile: "" });
              if (svcVersion) setBackendVersion(svcVersion);
              try { await refreshStatus("local", url, true); } catch { /* ignore */ }
              autoCheckEndpoints(url);
              if (svcVersion) setTimeout(() => checkVersionMismatch(svcVersion), 500);
              return true;
            };

            let alreadyConnected = false;
            try {
              alreadyConnected = await connectToRunningService(localUrl);
            } catch { /* 服务未运行 */ }

            if (!alreadyConnected && !cancelled) {
              let handled = false;
              try {
                const autoStarting = await invoke<boolean>("is_backend_auto_starting");
                if (autoStarting) {
                  handled = true;
                  const _busyAutoStart = notifyLoading(t("topbar.autoStarting"));
                  let serviceReady = false;
                  let spawnDone = false;
                  let postSpawnWait = 0;

                  for (let attempt = 0; attempt < 90 && !cancelled; attempt++) {
                    await new Promise((r) => setTimeout(r, 2000));
                    try {
                      serviceReady = await connectToRunningService(localUrl);
                      if (serviceReady) break;
                    } catch { /* still starting */ }
                    if (!spawnDone) {
                      try {
                        const still = await invoke<boolean>("is_backend_auto_starting");
                        if (!still) spawnDone = true;
                      } catch { spawnDone = true; }
                    }
                    if (spawnDone) {
                      postSpawnWait++;
                      if (postSpawnWait > 30) break;
                    }
                  }
                  if (!cancelled) {
                    if (serviceReady) {
                      visibilityGraceRef.current = true;
                      heartbeatFailCount.current = 0;
                      setTimeout(() => { visibilityGraceRef.current = false; }, 10000);
                    }
                    dismissLoading(_busyAutoStart);
                    if (serviceReady) {
                      notifySuccess(t("topbar.autoStartSuccess"));
                    } else {
                      setServiceStatus({ running: false, pid: null, pidFile: "" });
                      notifyError(t("topbar.autoStartFail"));
                    }
                  }
                }
              } catch { /* is_backend_auto_starting 不可用，忽略 */ }
              if (!handled && !cancelled) {
                setServiceStatus({ running: false, pid: null, pidFile: "" });
              }
            }
          }
        }
      } catch (e) {
        if (!cancelled) notifyError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ── 页面可见性监听（休眠/睡眠恢复感知）──
  // Capacitor 环境下 visibilitychange 和 appStateChange 可能同时触发，
  // 用 lastResumeRef 做 3 秒去重避免 WS 双重重连。
  const lastResumeRef = useRef(0);
  const handleAppResumed = useCallback(() => {
    const now = Date.now();
    if (now - lastResumeRef.current < 3000) return;
    lastResumeRef.current = now;
        visibilityGraceRef.current = true;
        heartbeatFailCount.current = 0;
        setTimeout(() => { visibilityGraceRef.current = false; }, 10000);
        reconnectWsNow();
        window.dispatchEvent(new Event("openakita_app_resumed"));
        logger.info("App", "Resumed from background");
  }, []);

  useEffect(() => {
    const handler = () => {
      const visible = !document.hidden;
      setPageVisible(visible);
      if (visible) handleAppResumed();
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [handleAppResumed]);

  // ── Capacitor: 原生 appStateChange 补充 ──
  // iOS WKWebView 进入后台时可能被系统挂起，visibilitychange 不一定触发。
  // @capacitor/app 提供原生级生命周期事件，100% 可靠。
  useEffect(() => {
    if (!IS_CAPACITOR) return;
    let cancelled = false;
    let removeListener: (() => void) | undefined;
    import("@capacitor/app").then(({ App }) => {
      if (cancelled) return;
      App.addListener("appStateChange", ({ isActive }) => {
        setPageVisible(isActive);
        if (isActive) handleAppResumed();
      }).then((handle) => {
        if (cancelled) { handle.remove(); return; }
        removeListener = () => handle.remove();
      });
    }).catch(() => {});
    return () => { cancelled = true; removeListener?.(); };
  }, [handleAppResumed]);

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
        const res = await fetch(`${effectiveBase}/api/health`, { signal: AbortSignal.timeout(HEALTH_POLL_TIMEOUT_MS) });
        if (res.ok) {
          heartbeatFailCount.current = 0;
          const wasUnhealthy = heartbeatStateRef.current === "degraded" || heartbeatStateRef.current === "suspect";
          heartbeatAliveSuccessCountRef.current = wasUnhealthy
            ? heartbeatAliveSuccessCountRef.current + 1
            : 1;
          const needTwoToRecover = wasUnhealthy && heartbeatAliveSuccessCountRef.current < 2;
          if (heartbeatStateRef.current !== "alive" && !needTwoToRecover) {
            heartbeatStateRef.current = "alive";
            setHeartbeatState("alive");
            if (IS_TAURI) try { await invoke("set_tray_backend_status", { status: "alive" }); } catch { /* ignore */ }
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

        heartbeatAliveSuccessCountRef.current = 0;
        heartbeatFailCount.current += 1;
        const suspectThreshold = 2;  // 连续失败 ≥2 才进入 suspect，单次孤立超时不变黄
        const degradeThreshold = 5;  // 连续失败 ≥5 才检查 PID 升级为 degraded/dead
        if (heartbeatFailCount.current < suspectThreshold) {
          return;
        }
        if (heartbeatFailCount.current < degradeThreshold) {
          if (heartbeatStateRef.current !== "suspect") {
            heartbeatStateRef.current = "suspect";
            setHeartbeatState("suspect");
          }
          return;
        }

        if (IS_TAURI && dataMode !== "remote") {
          try {
            const alive = await invoke<boolean>("openakita_check_pid_alive", { workspaceId: currentWorkspaceId });
            if (alive) {
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
          if (IS_TAURI) try { await invoke("set_tray_backend_status", { status: "dead" }); } catch { /* ignore */ }
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
          await refreshStatus(undefined, undefined, true);
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

  // ── Global shortcut: Ctrl+Shift+A to summon window ──
  useEffect(() => {
    if (!IS_TAURI) return;
    let unregister: (() => void) | null = null;
    (async () => {
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        const win = getCurrentWindow();
        unregister = await registerGlobalShortcut("CmdOrCtrl+Shift+A", () => {
          win.show().catch(() => {});
          win.setFocus().catch(() => {});
        });
      } catch { /* global-shortcut not available */ }
    })();
    return () => { if (unregister) unregister(); };
  }, []);

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
        notifyError(msg);
        try {
          await refreshStatus(undefined, undefined, true);
        } catch {
          // ignore
        }
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  // ── Web mode: subscribe to WebSocket events (replaces Tauri listen() for real-time updates) ──
  useEffect(() => {
    if ((!IS_WEB && !IS_CAPACITOR) || !webAuthed) return;
    const unsub = onWsEvent((event, data) => {
      const p = data as any;
      if (!p) return;
      if (event === "pip_install_event") {
        if (p.kind === "stage") {
          setInstallProgress({ stage: String(p.stage || ""), percent: Math.max(0, Math.min(100, Number(p.percent || 0))) });
        } else if (p.kind === "line") {
          const text = String(p.text || "");
          if (text) setInstallLiveLog((prev) => { const n = prev + text; return n.length > 80_000 ? n.slice(n.length - 80_000) : n; });
        }
      } else if (
        event === "service_status_changed" || event === "skills:changed" ||
        event === "im:channel_status" || event === "im:new_message"
      ) {
        if (wsRefreshDebounceRef.current) clearTimeout(wsRefreshDebounceRef.current);
        wsRefreshDebounceRef.current = setTimeout(() => {
          wsRefreshDebounceRef.current = null;
          refreshStatus().catch(() => {});
        }, 2_000);
      }
    });
    return unsub;
  }, [webAuthed]);

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


  // Keep boolean flags in sync with the visible status string (best-effort).
  useEffect(() => {
    if (!venvStatus) return;
    if (venvStatus.includes("venv 就绪")) setVenvReady(true);
    if (venvStatus.includes("安装完成")) setOpenakitaInstalled(true);
  }, [venvStatus]);

  async function doCreateWorkspace() {
    const _busyId = notifyLoading("创建工作区...");
    try {
      if (IS_WEB) {
        notifyError("工作区管理暂不支持 Web 模式，请在桌面端操作");
        return;
      } else {
        const ws = await invoke<WorkspaceSummary>("create_workspace", {
          id: newWsId,
          name: newWsName.trim(),
          setCurrent: true,
        });
        await refreshAll();
        setCurrentWorkspaceId(ws.id);
      }
      resetEnvLoaded();
      notifySuccess(`已创建工作区：${newWsName.trim()}（${newWsId}）`);
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSetCurrentWorkspace(id: string) {
    const _busyId = notifyLoading("切换工作区...");
    try {
      const wasRunning = serviceStatus?.running;
      if (IS_WEB) {
        notifyError("工作区切换暂不支持 Web 模式，请在桌面端操作");
        return;
      } else {
        await invoke("set_current_workspace", { id });
      }
      await refreshAll();
      resetEnvLoaded();
      if (wasRunning) {
        notifySuccess(t("topbar.switchWorkspaceDoneRestart", { id }));
      } else {
        notifySuccess(t("topbar.switchWorkspaceDone", { id }));
      }
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doDetectPython() {
    const _busyId = notifyLoading("检测项目 Python 环境...");
    try {
      const cands = await invoke<PythonCandidate[]>("detect_python");
      setPythonCandidates(cands);
      const firstUsable = cands.findIndex((c) => c.isUsable);
      setSelectedPythonIdx(firstUsable);
      notifySuccess(firstUsable >= 0 ? "已找到可用 Python（3.11+）" : "未找到可用内置 Python（请检查安装包完整性）");
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doInstallEmbeddedPython() {
    const _busyId = notifyLoading("检查内置 Python...");
    try {
      setVenvStatus("检查内置 Python 中...");
      const r = await invoke<BundledPythonInstallResult>("install_bundled_python", { pythonSeries: "3.11" });
      const cand: PythonCandidate = {
        command: r.pythonCommand,
        versionText: `bundled (${r.tag}): ${r.assetName}`,
        isUsable: true,
      };
      setPythonCandidates((prev) => [cand, ...prev.filter((p) => p.command.join(" ") !== cand.command.join(" "))]);
      setSelectedPythonIdx(0);
      setVenvStatus(`内置 Python 就绪：${r.pythonPath}`);
      notifySuccess("内置 Python 可用，可以继续创建 venv");
    } catch (e) {
      notifyError(String(e));
      setVenvStatus(`内置 Python 不可用：${String(e)}`);
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doCreateVenv() {
    if (!canUsePython) return;
    const _busyId = notifyLoading("创建 venv...");
    try {
      setVenvStatus("创建 venv 中...");
      const py = pythonCandidates[selectedPythonIdx].command;
      await invoke<string>("create_venv", { pythonCommand: py, venvDir });
      setVenvStatus(`venv 就绪：${venvDir}`);
      setVenvReady(true);
      setOpenakitaInstalled(false);
      notifySuccess("venv 已准备好，可以安装 openakita");
      await persistPythonEnvConfig(venvDir);
    } catch (e) {
      notifyError(String(e));
      setVenvStatus(`创建 venv 失败：${String(e)}`);
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function persistPythonEnvConfig(venvPath: string) {
    if (!currentWorkspaceId || !IS_TAURI) return;
    try {
      const entries: { key: string; value: string }[] = [
        { key: "PYTHON_VENV_PATH", value: venvPath },
      ];
      await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries });
      setEnvDraft((prev) => {
        const next = { ...prev };
        next["PYTHON_VENV_PATH"] = venvPath;
        return next;
      });
    } catch {
      // best-effort
    }
  }

  async function doCreateVenvFromPython() {
    if (!canUsePython) return;
    const _busyId = notifyLoading(t("config.pyCreatingVenv"));
    try {
      setVenvStatus(t("config.pyCreatingVenv"));
      const py = pythonCandidates[selectedPythonIdx].command;
      await invoke<string>("create_venv", { pythonCommand: py, venvDir });
      setVenvStatus(t("config.pyVenvCreated", { path: venvDir }));
      setVenvReady(true);
      setOpenakitaInstalled(false);
      await persistPythonEnvConfig(venvDir);
      notifySuccess(t("config.pyVenvReady"));
    } catch (e) {
      notifyError(String(e));
      setVenvStatus(t("config.pyVenvCreateFail") + `: ${String(e)}`);
    } finally {
      dismissLoading(_busyId);
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
      notifyError(`获取 PyPI 版本列表失败：${e}`);
    } finally {
      setPypiVersionsLoading(false);
    }
  }

  async function doSetupVenvAndInstallOpenAkita() {
    if (!canUsePython) {
      notifyError("请先在 Python 步骤安装/检测并选择一个可用 Python（3.11+）。");
      return;
    }
    setInstallLiveLog("");
    setInstallProgress({ stage: "准备开始", percent: 1 });
    const _busyId = notifyLoading("创建 venv 并安装 openakita...");
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
      await persistPythonEnvConfig(venvDir);

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
      notifySuccess("openakita 已安装，可以读取服务商列表并配置端点");

      // 3) verify by attempting to list providers (makes failures visible early)
      try {
        await doLoadProviders();
      } catch {
        // ignore; doLoadProviders already sets error
      }
    } catch (e) {
      const msg = String(e);
      notifyError(msg);
      setVenvStatus(`安装失败：${msg}`);
      setInstallLog("");
      if (msg.includes("缺少 Setup Center 所需模块") || msg.includes("No module named 'openakita.setup_center'")) {
        notifySuccess("你安装到的 openakita 不包含 Setup Center 模块。建议切换“安装来源”为 GitHub 或 本地源码，然后重新安装。");
      }
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doLoadProviders() {
    const _busyId = notifyLoading("读取服务商列表...");
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

      if (parsed.length === 0) {
        parsed = BUILTIN_PROVIDERS;
      } else {
        // 后端返回的列表可能不完整（部分 registry 加载失败），
        // 将 BUILTIN_PROVIDERS 中缺失的服务商补充进去
        const slugSet = new Set(parsed.map(p => p.slug));
        for (const bp of BUILTIN_PROVIDERS) {
          if (!slugSet.has(bp.slug)) parsed.push(bp);
        }
      }
      const bottomSlugs = new Set(["ollama", "lmstudio", "custom"]);
      const top = parsed.filter(p => !bottomSlugs.has(p.slug));
      const bottom = ["ollama", "lmstudio", "custom"]
        .map(s => parsed.find(p => p.slug === s))
        .filter(Boolean) as ProviderInfo[];
      parsed = [...top, ...bottom];
      setProviders(parsed);

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
      logger.warn("App", "doLoadProviders failed", { error: String(e) });
      if (providers.length === 0) {
        const bottomSlugs2 = new Set(["ollama", "lmstudio", "custom"]);
        const top2 = BUILTIN_PROVIDERS.filter(p => !bottomSlugs2.has(p.slug));
        const bottom2 = ["ollama", "lmstudio", "custom"]
          .map(s => BUILTIN_PROVIDERS.find(p => p.slug === s))
          .filter(Boolean) as ProviderInfo[];
        const sorted = [...top2, ...bottom2];
        setProviders(sorted);
      }
    } finally {
      dismissLoading(_busyId);
    }
  }

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
          max_tokens: Number.isFinite(Number(e?.max_tokens)) ? Number(e?.max_tokens) : 0,
          context_window: Number.isFinite(Number(e?.context_window)) ? Number(e?.context_window) : 200000,
          timeout: Number.isFinite(Number(e?.timeout)) ? Number(e?.timeout) : 180,
          capabilities: Array.isArray(e?.capabilities) ? e.capabilities.map((x: any) => String(x)) : [],
          rpm_limit: Number.isFinite(Number(e?.rpm_limit)) ? Number(e?.rpm_limit) : 0,
          note: e?.note ? String(e.note) : null,
          pricing_tiers: Array.isArray(e?.pricing_tiers) ? e.pricing_tiers.map((t: any) => ({
            max_input: Number.isFinite(Number(t?.max_input)) ? Number(t.max_input) : 0,
            input_price: Number.isFinite(Number(t?.input_price)) ? Number(t.input_price) : 0,
            output_price: Number.isFinite(Number(t?.output_price)) ? Number(t.output_price) : 0,
          })) : undefined,
          enabled: e?.enabled !== false,
        }))
        .filter((e: any) => e.name);
      list.sort((a, b) => a.priority - b.priority);
      setSavedEndpoints(list);

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
          context_window: Number.isFinite(Number(e.context_window)) ? Number(e.context_window) : 200000,
          timeout: Number.isFinite(Number(e.timeout)) ? Number(e.timeout) : 30,
          capabilities: Array.isArray(e.capabilities) ? e.capabilities.map((x: any) => String(x)) : ["text"],
          note: e.note ? String(e.note) : null,
          enabled: e?.enabled !== false,
        }))
        .sort((a: EndpointDraft, b: EndpointDraft) => a.priority - b.priority);
      setSavedCompilerEndpoints(compilerEps);

      // Load STT endpoints
      const sttEps: EndpointDraft[] = (Array.isArray(parsed?.stt_endpoints) ? parsed.stt_endpoints : [])
        .filter((e: any) => e?.name)
        .map((e: any) => ({
          name: String(e.name || ""),
          provider: String(e.provider || ""),
          api_type: String(e.api_type || "openai"),
          base_url: String(e.base_url || ""),
          api_key_env: String(e.api_key_env || ""),
          model: String(e.model || ""),
          priority: Number.isFinite(Number(e.priority)) ? Number(e.priority) : 1,
          max_tokens: Number.isFinite(Number(e.max_tokens)) ? Number(e.max_tokens) : 0,
          context_window: Number.isFinite(Number(e.context_window)) ? Number(e.context_window) : 0,
          timeout: Number.isFinite(Number(e.timeout)) ? Number(e.timeout) : 60,
          capabilities: Array.isArray(e.capabilities) ? e.capabilities.map((x: any) => String(x)) : ["text"],
          note: e.note ? String(e.note) : null,
          enabled: e?.enabled !== false,
        }))
        .sort((a: EndpointDraft, b: EndpointDraft) => a.priority - b.priority);
      setSavedSttEndpoints(sttEps);
    } catch {
      setSavedEndpoints([]);
      setSavedCompilerEndpoints([]);
      setSavedSttEndpoints([]);
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
    if (IS_WEB || IS_CAPACITOR) return apiBaseUrl || window.location.origin;
    return dataMode === "remote" ? apiBaseUrl : "http://127.0.0.1:18900";
  }

  // ── Disabled views management ──
  const fetchDisabledViews = useCallback(async () => {
    if (!shouldUseHttpApi()) return;
    try {
      const resp = await safeFetch(`${httpApiBase()}/api/config/disabled-views`);
      const data = await resp.json();
      setDisabledViews(data.disabled_views || []);
    } catch { /* ignore */ }
  }, [serviceStatus?.running, dataMode, apiBaseUrl]);

  useEffect(() => { fetchDisabledViews(); }, [fetchDisabledViews]);

  const fetchAgentMode = useCallback(async () => {
    if (!shouldUseHttpApi()) return;
    try {
      const res = await safeFetch(`${httpApiBase()}/api/config/agent-mode`);
      const data = await res.json();
      setMultiAgentEnabled(data.multi_agent_enabled ?? false);
    } catch (e) {
      logger.warn("App", "Failed to fetch agent mode", { error: String(e) });
    }
  }, [serviceStatus?.running, dataMode, apiBaseUrl]);

  useEffect(() => { fetchAgentMode(); }, [fetchAgentMode]);

  const toggleMultiAgent = useCallback(async () => {
    const next = !multiAgentEnabled;
    try {
      await safeFetch(`${httpApiBase()}/api/config/agent-mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      setMultiAgentEnabled(next);
    } catch (e) {
      logger.error("App", "Failed to toggle agent mode", { error: String(e) });
    }
  }, [multiAgentEnabled]);

  const toggleViewDisabled = useCallback(async (viewName: string) => {
    const next = disabledViews.includes(viewName)
      ? disabledViews.filter((v) => v !== viewName)
      : [...disabledViews, viewName];
    setDisabledViews(next);
    if (shouldUseHttpApi()) {
      try {
        await safeFetch(`${httpApiBase()}/api/config/disabled-views`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ views: next }),
        });
      } catch { /* ignore */ }
    }
  }, [disabledViews, serviceStatus?.running, dataMode, apiBaseUrl]);

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
        logger.warn("App", `readWorkspaceFile: HTTP failed for ${relativePath}, falling back to Tauri`);
      }
    }
    // ── 后端未运行 / HTTP 回退 → Tauri 本地读取（Web 模式无此能力） ──
    if (IS_TAURI && currentWorkspaceId) {
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
          const reloaded = await triggerConfigReload();
          if (!reloaded) {
            toast.warning("配置已保存，但热重载未生效。建议重启后端服务以应用更改。", { duration: 6000 });
          }
          return;
        }
        if (relativePath === "data/skills.json") {
          await safeFetch(`${base}/api/config/skills`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: JSON.parse(content) }),
          });
          try {
            await safeFetch(`${base}/api/skills/reload`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({}),
            });
          } catch { /* reload failure is non-blocking */ }
          return;
        }
      } catch {
        // HTTP 暂时不可用 — 回退到本地写入（比如后端正在重启）
        logger.warn("App", `writeWorkspaceFile: HTTP failed for ${relativePath}, falling back to Tauri`);
      }
    }
    // ── 后端未运行 / HTTP 回退 → Tauri 本地写入（Web 模式无此能力） ──
    if (IS_TAURI && currentWorkspaceId) {
      await invoke("workspace_write_file", { workspaceId: currentWorkspaceId, relativePath, content });
      return;
    }
    throw new Error(`写入配置失败：服务未运行且无本地工作区 (${relativePath})`);
  }

  /**
   * 通知运行中的后端热重载配置。
   * 仅在后端运行时调用有意义；后端未运行时静默跳过。
   * 返回 true 表示重载成功，false 表示失败或后端未运行。
   */
  async function triggerConfigReload(): Promise<boolean> {
    if (!shouldUseHttpApi()) return false;
    try {
      const resp = await safeFetch(`${httpApiBase()}/api/config/reload`, {
        method: "POST",
        signal: AbortSignal.timeout(3000),
      });
      const data = await resp.json();
      if (data.reloaded) return true;
      logger.warn("App", `Config reload not applied: ${data.reason || "unknown"}`);
      return false;
    } catch {
      return false;
    }
  }

  /**
   * 纯重启：安装 IM 依赖 → 检测存活 → 触发重启 → 轮询恢复。
   * 不含 env 保存逻辑，可独立调用（如 Bot 配置保存后重启）。
   */
  async function restartService(): Promise<void> {
    const base = httpApiBase();
    setRestartOverlay({ phase: "restarting" });

    try {
      // 自动安装已启用 IM 通道缺失的依赖（非阻塞，失败不影响重启）
      if (IS_TAURI && venvDir && currentWorkspaceId) {
        try {
          await invoke("openakita_ensure_channel_deps", {
            venvDir,
            workspaceId: currentWorkspaceId,
          });
        } catch { /* 非关键步骤，失败不影响流程 */ }
      }

      // 检测服务是否运行
      let alive = false;
      try {
        const ping = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(2000) });
        alive = ping.ok;
      } catch { alive = false; }

      if (!alive) {
        setRestartOverlay({ phase: "notRunning" });
        setTimeout(() => {
          setRestartOverlay(null);
          notifySuccess(t("config.restartNotRunning"));
        }, 2000);
        return;
      }

      // 触发重启
      setRestartOverlay({ phase: "restarting" });
      const wsId = currentWorkspaceId || workspaces[0]?.id;

      if (IS_TAURI && wsId && venvDir && dataMode === "local") {
        // ── Tauri 本地模式：进程级重启（杀旧进程 → 启新进程） ──
        try {
          const shutRes = await fetch(`${base}/api/shutdown`, { method: "POST", signal: AbortSignal.timeout(2000) });
          if (shutRes.ok) await new Promise((r) => setTimeout(r, 1000));
        } catch { /* 请求可能因服务关闭而失败 */ }

        try {
          await invoke("openakita_service_stop", { workspaceId: wsId });
        } catch { /* PID 文件可能不存在 */ }

        await waitForServiceDown(base, 15000);

        setRestartOverlay({ phase: "waiting" });
        try {
          const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>(
            "openakita_service_start", { venvDir, workspaceId: wsId },
          );
          setServiceStatus(ss);
        } catch (e) {
          setRestartOverlay({ phase: "fail" });
          setTimeout(() => {
            setRestartOverlay(null);
            notifyError(t("config.restartFail") + ": " + String(e));
          }, 2500);
          return;
        }
      } else {
        // ── Web / Capacitor 模式：进程内重启（唯一可用方式） ──
        try {
          await fetch(`${base}/api/config/restart`, { method: "POST", signal: AbortSignal.timeout(3000) });
        } catch { /* 请求可能因服务关闭而失败 */ }

        await waitForServiceDown(base, 15000);
      }

      // 轮询等待服务恢复
      setRestartOverlay({ phase: "waiting" });
      const maxWait = IS_TAURI ? 60_000 : 30_000;
      const pollInterval = 1000;
      const startTime = Date.now();
      let recovered = false;

      while (Date.now() - startTime < maxWait) {
        await new Promise((r) => setTimeout(r, pollInterval));
        try {
          const res = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(2000) });
          if (res.ok) {
            recovered = true;
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
        try { await refreshStatus(undefined, undefined, true); } catch { /* ignore */ }
        autoCheckEndpoints(apiBaseUrl);
        setTimeout(() => {
          setRestartOverlay(null);
          notifySuccess(t("config.restartSuccess"));
        }, 1200);
      } else {
        setRestartOverlay({ phase: "fail" });
        setTimeout(() => {
          setRestartOverlay(null);
          notifyError(t("config.restartFail"));
        }, 2500);
      }
    } catch (e) {
      setRestartOverlay(null);
      notifyError(String(e));
    }
  }

  /**
   * 保存 .env 配置后触发服务重启，并轮询等待服务恢复。
   * 如果服务未运行，仅保存不重启并提示。
   */
  async function applyAndRestart(keys: string[]): Promise<void> {
    setRestartOverlay({ phase: "saving" });
    try {
      await saveEnvKeys(keys);
    } catch (e) {
      setRestartOverlay(null);
      notifyError(String(e));
      return;
    }
    await restartService();
  }


  const step = steps[currentStepIdx] || steps[0];


  /** 根据当前步骤返回需要自动保存的 env key 列表 */
  function getAutoSaveKeysForStep(sid: StepId): string[] {
    switch (sid) {
      case "im":
        return [
          "IM_CHAIN_PUSH",
          "TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_PROXY",
          "TELEGRAM_REQUIRE_PAIRING", "TELEGRAM_PAIRING_CODE", "TELEGRAM_WEBHOOK_URL",
          "FEISHU_ENABLED", "FEISHU_APP_ID", "FEISHU_APP_SECRET",
          "WEWORK_ENABLED", "WEWORK_CORP_ID",
          "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY", "WEWORK_CALLBACK_PORT", "WEWORK_CALLBACK_HOST",
          "WEWORK_MODE", "WEWORK_WS_ENABLED", "WEWORK_WS_BOT_ID", "WEWORK_WS_SECRET",
          "DINGTALK_ENABLED", "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET",
          "ONEBOT_ENABLED", "ONEBOT_MODE", "ONEBOT_WS_URL", "ONEBOT_REVERSE_HOST", "ONEBOT_REVERSE_PORT", "ONEBOT_ACCESS_TOKEN",
          "QQBOT_ENABLED", "QQBOT_APP_ID", "QQBOT_APP_SECRET", "QQBOT_SANDBOX", "QQBOT_MODE", "QQBOT_WEBHOOK_PORT", "QQBOT_WEBHOOK_PATH",
          "WECHAT_ENABLED", "WECHAT_TOKEN",
        ];
      case "tools":
        return [
          "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FORCE_IPV4",
          "TOOL_MAX_PARALLEL", "FORCE_TOOL_CALL_MAX_RETRIES", "FORCE_TOOL_CALL_IM_FLOOR", "CONFIRMATION_TEXT_MAX_RETRIES",
          "ALLOW_PARALLEL_TOOLS_WITH_INTERRUPT_CHECKS",
          "MCP_ENABLED", "MCP_TIMEOUT",
          "DESKTOP_ENABLED", "DESKTOP_DEFAULT_MONITOR", "DESKTOP_COMPRESSION_QUALITY",
          "DESKTOP_MAX_WIDTH", "DESKTOP_MAX_HEIGHT", "DESKTOP_CACHE_TTL",
          "DESKTOP_UIA_TIMEOUT", "DESKTOP_UIA_RETRY_INTERVAL", "DESKTOP_UIA_MAX_RETRIES",
          "DESKTOP_VISION_ENABLED", "DESKTOP_VISION_MAX_RETRIES", "DESKTOP_VISION_TIMEOUT",
          "DESKTOP_CLICK_DELAY", "DESKTOP_TYPE_INTERVAL", "DESKTOP_MOVE_DURATION",
          "DESKTOP_FAILSAFE", "DESKTOP_PAUSE",
          "WHISPER_MODEL", "WHISPER_LANGUAGE", "GITHUB_TOKEN",
        ];
      case "agent":
        return [
          "AGENT_NAME", "MAX_ITERATIONS", "SELFCHECK_AUTOFIX",
          "THINKING_MODE",
          "PROGRESS_TIMEOUT_SECONDS", "HARD_TIMEOUT_SECONDS",
          "MEMORY_MODE",
          "EMBEDDING_MODEL", "EMBEDDING_DEVICE", "MODEL_DOWNLOAD_SOURCE",
          "MEMORY_HISTORY_DAYS", "MEMORY_MAX_HISTORY_FILES", "MEMORY_MAX_HISTORY_SIZE_MB",
          "PERSONA_NAME",
          "PROACTIVE_ENABLED", "PROACTIVE_MAX_DAILY_MESSAGES", "PROACTIVE_MIN_INTERVAL_MINUTES",
          "PROACTIVE_QUIET_HOURS_START", "PROACTIVE_QUIET_HOURS_END", "PROACTIVE_IDLE_THRESHOLD_HOURS",
          "STICKER_ENABLED", "STICKER_DATA_DIR",
          "SCHEDULER_TIMEZONE", "SCHEDULER_TASK_TIMEOUT",
        ];
      case "advanced":
        return [
          "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FORCE_IPV4",
          "DATABASE_PATH", "LOG_LEVEL",
          "LOG_DIR", "LOG_FILE_PREFIX", "LOG_MAX_SIZE_MB", "LOG_BACKUP_COUNT",
          "LOG_RETENTION_DAYS", "LOG_FORMAT", "LOG_TO_CONSOLE", "LOG_TO_FILE",
          "DESKTOP_NOTIFY_ENABLED", "DESKTOP_NOTIFY_SOUND",
          "SESSION_TIMEOUT_MINUTES", "SESSION_MAX_HISTORY", "SESSION_STORAGE_PATH",
          "API_HOST", "TRUST_PROXY",
          "BACKUP_ENABLED", "BACKUP_PATH", "BACKUP_CRON",
          "BACKUP_MAX_BACKUPS", "BACKUP_INCLUDE_USERDATA", "BACKUP_INCLUDE_MEDIA",
          "CONTEXT_MAX_WINDOW", "CONTEXT_COMPRESSION_RATIO", "CONTEXT_COMPRESSION_THRESHOLD",
          "CONTEXT_BOUNDARY_COMPRESSION_RATIO", "CONTEXT_MIN_RECENT_TURNS",
          "CONTEXT_ENABLE_TOOL_COMPRESSION", "CONTEXT_LARGE_TOOL_THRESHOLD",
        ];
      default:
        return [];
    }
  }

  /** 返回当前步骤对应的 footer 保存按钮配置，无需按钮时返回 null */
  function getFooterSaveConfig(): { keys: string[]; savedMsg: string } | null {
    switch (stepId) {
      case "llm":
        return null;

      case "im":
        return { keys: getAutoSaveKeysForStep("im"), savedMsg: t("config.imSaved") };
      case "tools":
        return { keys: getAutoSaveKeysForStep("tools"), savedMsg: t("config.toolsSaved") };
      case "agent":
        return { keys: getAutoSaveKeysForStep("agent"), savedMsg: t("config.agentSaved") };
      case "advanced":
        return { keys: getAutoSaveKeysForStep("advanced"), savedMsg: t("config.advancedSaved") };
      default:
        return null;
    }
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
      // ── Autostart / auto-update 状态查询（不依赖后端，放在公共路径） ──
      try {
        const en = await invoke<boolean>("autostart_is_enabled");
        setAutostartEnabled(en);
      } catch {
        setAutostartEnabled(null);
      }
      try {
        const au = await invoke<boolean>("get_auto_update");
        setAutoUpdateEnabled(au);
      } catch {
        setAutoUpdateEnabled(null);
      }

      // Verify the service is actually alive before trying HTTP API
      let serviceAlive = false;
      if (forceAliveCheck || serviceStatus?.running || effectiveDataMode === "remote") {
        try {
          const ping = await fetch(`${effectiveApiBaseUrl}/api/health`, { signal: AbortSignal.timeout(HEALTH_POLL_TIMEOUT_MS) });
          serviceAlive = ping.ok;
          if (serviceAlive) {
            try {
              const healthData = await ping.json();
              if (healthData.version) setBackendVersion(healthData.version);
            } catch { /* ignore parse error */ }
            setServiceStatus((prev) =>
              prev ? { ...prev, running: true } : { running: true, pid: null, pidFile: "" }
            );
          }
        } catch {
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
        let endpointSummaryResolved = false;
        let envAlreadyLoaded = false;
        let httpEnv: EnvMap = {};
        try {
          // Try new config API (may not exist in older service versions)
          const envRes = await safeFetch(`${effectiveApiBaseUrl}/api/config/env`);
          const envData = await envRes.json();
          httpEnv = envData.env || {};
          setEnvDraft((prev) => ({ ...prev, ...httpEnv }));
          markEnvLoaded(currentWorkspaceId || "__remote__");
          envAlreadyLoaded = true;

          const epRes = await safeFetch(`${effectiveApiBaseUrl}/api/config/endpoints`);
          const epData = await epRes.json();
          const eps = Array.isArray(epData?.endpoints) ? epData.endpoints : [];

          let statusMap: Record<string, boolean> = {};
          try {
            const statusRes = await safeFetch(`${effectiveApiBaseUrl}/api/config/endpoint-status`);
            const statusData = await statusRes.json();
            const statusList = Array.isArray(statusData?.endpoints) ? statusData.endpoints : [];
            for (const s of statusList) {
              if (s?.name) statusMap[String(s.name)] = !!s.key_present;
            }
          } catch { /* endpoint-status API not available, fall back to env */ }

          const list = eps
            .map((e: any) => {
              const keyEnv = String(e?.api_key_env || "");
              const epName = String(e?.name || "");
              const keyPresent = epName in statusMap
                ? statusMap[epName]
                : !!(keyEnv && (httpEnv[keyEnv] ?? "").trim());
              return {
                name: String(e?.name || ""),
                provider: String(e?.provider || ""),
                apiType: String(e?.api_type || ""),
                baseUrl: String(e?.base_url || ""),
                model: String(e?.model || ""),
                keyEnv,
                keyPresent,
                enabled: e?.enabled !== false,
              };
            })
            .filter((e: any) => e.name);
          if (list.length > 0) {
            setEndpointSummary(list);
            endpointSummaryResolved = true;
          }
        } catch {
          // Config API not available — will fall back below
        }

        // Fall back: try /api/models (always available in running service)
        if (!endpointSummaryResolved) {
          try {
            const modelsRes = await safeFetch(`${effectiveApiBaseUrl}/api/models`);
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
              enabled: m?.enabled !== false,
            })).filter((e: any) => e.name);
            if (list.length > 0) {
              setEndpointSummary(list);
              endpointSummaryResolved = true;
              const healthFromModels: Record<string, any> = {};
              for (const m of models) {
                const n = String(m?.name || m?.endpoint || "");
                if (!n) continue;
                const s = String(m?.status || "unknown");
                healthFromModels[n] = { status: s, latencyMs: null, error: s === "unhealthy" ? "endpoint unhealthy" : null };
              }
              setEndpointHealth((prev: any) => ({ ...healthFromModels, ...prev }));
            }
          } catch { /* ignore */ }
        }

        // Fall back to Tauri local file system if HTTP API completely failed
        if (!endpointSummaryResolved && currentWorkspaceId) {
          try {
            const env = envAlreadyLoaded ? httpEnv : await ensureEnvLoaded(currentWorkspaceId);
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
                enabled: e?.enabled !== false,
              };
            }).filter((e: any) => e.name);
            if (list.length > 0) {
              setEndpointSummary(list);
              endpointSummaryResolved = true;
            }
          } catch { /* ignore */ }
        }

        // Skills via HTTP
        try {
          const skRes = await safeFetch(`${effectiveApiBaseUrl}/api/skills`);
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
                skill_id: String(s?.skill_id || s?.name || ""),
                name: String(s?.name || ""), description: String(s?.description || ""),
                system: !!s?.system, enabled: typeof s?.enabled === "boolean" ? s.enabled : undefined,
                tool_name: s?.tool_name ?? null, category: s?.category ?? null, path: s?.path ?? null,
              })));
            } catch { /* keep existing skill data on failure */ }
          }
        }

        // Service status – enrich with PID info from Tauri, but do NOT override
        // the running flag: the HTTP health check is the source of truth for whether
        // the service is alive.  The Tauri PID file may not exist when the service
        // was started externally (not via this app).
        if (effectiveDataMode !== "remote" && currentWorkspaceId) {
          try {
            const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_status", { workspaceId: currentWorkspaceId });
            setServiceStatus((prev) => ({
              running: prev?.running ?? serviceAlive,
              pid: ss.pid ?? prev?.pid ?? null,
              pidFile: ss.pidFile ?? prev?.pidFile ?? "",
            }));
          } catch { /* keep existing status */ }
        }
        // IM channels (HTTP API mode)
        try {
          const imRes = await safeFetch(`${effectiveApiBaseUrl}/api/im/channels`, { signal: AbortSignal.timeout(5000) });
          const imData = await imRes.json();
          const channels = imData.channels || [];
          const h: Record<string, { status: string; error: string | null; lastCheckedAt: string | null }> = {};
          for (const c of channels) {
            const key = c.channel || c.name;
            const val = { status: c.status || "unknown", error: c.error || null, lastCheckedAt: c.last_checked_at || null };
            h[key] = val;
            const ctype = c.channel_type || key;
            if (ctype !== key) {
              if (!h[ctype] || (val.status === "online" && h[ctype]?.status !== "online")) {
                h[ctype] = val;
              }
            }
          }
          if (Object.keys(h).length > 0) setImHealth(h);
        } catch { /* IM status is optional */ }
        return;
      }

      // ── Local mode: use Tauri commands (original logic) ──
      if (!currentWorkspaceId) {
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
            enabled: e?.enabled !== false,
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
            skill_id: String(s?.skill_id || s?.name || ""),
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
        /* keep existing skill data on failure */
      }

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
          const imRes = await safeFetch(`${effectiveApiBaseUrl}/api/im/channels`, { signal: AbortSignal.timeout(5000) });
          const imData = await imRes.json();
          const channels = imData.channels || [];
          const h: Record<string, { status: string; error: string | null; lastCheckedAt: string | null }> = {};
          for (const c of channels) {
            const key = c.channel || c.name;
            const val = { status: c.status || "unknown", error: c.error || null, lastCheckedAt: c.last_checked_at || null };
            h[key] = val;
            const ctype = c.channel_type || key;
            if (ctype !== key) {
              if (!h[ctype] || (val.status === "online" && h[ctype]?.status !== "online")) {
                h[ctype] = val;
              }
            }
          }
          if (Object.keys(h).length > 0) setImHealth(h);
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

  // 进入聊天页时，如果端点列表为空，触发一次受控自愈刷新。
  // 这能覆盖启动竞态（服务已起但端点摘要尚未装载）的偶发场景。
  useEffect(() => {
    if (view !== "chat") return;
    if (endpointSummary.length > 0) return;
    if (dataMode !== "remote" && !serviceStatus?.running) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      void refreshStatus(undefined, undefined, true).catch(() => {});
    }, 300);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, endpointSummary.length, dataMode, serviceStatus?.running, currentWorkspaceId, apiBaseUrl]);

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
   * 轮询等待后端 HTTP 服务完全关闭（端口不可达）。
   * 用于重启场景，确保旧服务完全关闭后再启动新服务。
   * @returns true 如果在 maxWaitMs 内服务已不可达
   */
  async function waitForServiceDown(baseUrl: string, maxWaitMs = 15000): Promise<boolean> {
    const start = Date.now();
    const interval = 500;
    while (Date.now() - start < maxWaitMs) {
      try {
        await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(1000) });
        // 还能连上，继续等
      } catch {
        // 连接失败 = 服务已关闭
        return true;
      }
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

  // checkVersionMismatch, compareSemver, checkForAppUpdate, doDownloadAndInstall, doRelaunchAfterUpdate
  // -> extracted to ./hooks/useVersionCheck.ts

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
    let _busyId = notifyLoading(t("topbar.starting"));
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
        notifySuccess(t("connect.success"));
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
        dismissLoading(_busyId);
        _busyId = notifyLoading(t("topbar.starting") + "…");
        const bgReady = await waitForServiceReady("http://127.0.0.1:18900", 60000);
        if (bgReady) {
          notifySuccess(t("connect.success"));
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
          notifyError(t("topbar.startFail") + " (HTTP API not reachable)");
          await refreshStatus("local", "http://127.0.0.1:18900", true);
        }
      } else {
        notifyError(t("topbar.startFail"));
      }
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
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
    const _busyId = notifyLoading(t("connect.testing"));
    try {
      // IMPORTANT: pass forceAliveCheck=true because setServiceStatus is async
      // and refreshStatus's closure still sees the old serviceStatus value
      await refreshStatus("local", "http://127.0.0.1:18900", true);
      autoCheckEndpoints("http://127.0.0.1:18900");
      notifySuccess(t("connect.success"));
      // Check version mismatch using info from conflict detection (avoids extra request)
      if (ver && ver !== "unknown") checkVersionMismatch(ver);
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
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
    const _busyId = notifyLoading(t("status.stopping"));
    try {
      await doStopService(wsId);
      // 轮询等待旧服务完全关闭（端口释放），而非固定延时
      await waitForServiceDown("http://127.0.0.1:18900", 15000);
    } catch { /* ignore stop errors */ }
    dismissLoading(_busyId);
    await doStartLocalService(wsId);
  }

  // ── Check for app updates once desktop version is known (respects auto-update toggle) ──
  useEffect(() => {
    if (desktopVersion === "0.0.0") return; // not yet loaded
    if (autoUpdateEnabled === false) return; // user disabled auto-update
    checkForAppUpdate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktopVersion, autoUpdateEnabled]);

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
      notifyError(t("status.stopFailed"));
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

  useEffect(() => {
    const el = serviceLogRef.current;
    if (el && logAtBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [serviceLog?.content]);

  // Skills selection default sync (only when user hasn't changed it)
  useEffect(() => {
    if (!skillsDetail) return;
    if (skillsTouched) return;
    const m: Record<string, boolean> = {};
    for (const s of skillsDetail) {
      if (!s?.skill_id) continue;
      if (s.system) m[s.skill_id] = true;
      else m[s.skill_id] = typeof s.enabled === "boolean" ? s.enabled : true;
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
      notifyError("请先设置当前工作区");
      return;
    }
    const _busyId = notifyLoading("读取 skills...");
    try {
      let skillsList: any[] = [];
      // ── 后端运行中 → HTTP API ──
      if (shouldUseHttpApi()) {
        const res = await safeFetch(`${httpApiBase()}/api/skills`, { signal: AbortSignal.timeout(15_000) });
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
          logger.warn("App", "openakita_list_skills via Tauri failed", { error: String(e) });
        }
      }
      const systemCount = skillsList.filter((s: any) => !!s.system).length;
      const externalCount = skillsList.length - systemCount;
      setSkillSummary({ count: skillsList.length, systemCount, externalCount });
      setSkillsDetail(
        skillsList.map((s: any) => ({
          skill_id: String(s?.skill_id || s?.name || ""),
          name: String(s?.name || ""),
          description: String(s?.description || ""),
          system: !!s?.system,
          enabled: typeof s?.enabled === "boolean" ? s.enabled : undefined,
          tool_name: s?.tool_name ?? null,
          category: s?.category ?? null,
          path: s?.path ?? null,
        })),
      );
      notifySuccess("已刷新 skills 列表");
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveSkillsSelection() {
    if (!currentWorkspaceId) {
      notifyError("请先设置当前工作区");
      return;
    }
    if (!skillsDetail) {
      notifyError("未读取到 skills 列表（请先刷新 skills）");
      return;
    }
    const _busyId = notifyLoading("保存 skills 启用状态...");
    try {
      const externalAllowlist = skillsDetail
        .filter((s) => !s.system && !!s.skill_id)
        .filter((s) => !!skillsSelection[s.skill_id])
        .map((s) => s.skill_id);

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
      notifySuccess("已保存：data/skills.json（系统技能默认启用；外部技能按你的选择启用）");
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }



  function renderStatus() {
    return (
      <StatusView
        currentWorkspaceId={currentWorkspaceId}
        workspaces={workspaces}
        envDraft={envDraft}
        serviceStatus={serviceStatus}
        heartbeatState={heartbeatState}
        busy={busy}
        autostartEnabled={autostartEnabled}
        autoUpdateEnabled={autoUpdateEnabled}
        setAutostartEnabled={setAutostartEnabled}
        setAutoUpdateEnabled={setAutoUpdateEnabled}
        endpointSummary={endpointSummary}
        endpointHealth={endpointHealth}
        setEndpointHealth={setEndpointHealth}
        imHealth={imHealth}
        setImHealth={setImHealth}
        skillSummary={skillSummary}
        serviceLog={serviceLog}
        serviceLogRef={serviceLogRef}
        logAtBottomRef={logAtBottomRef}
        detectedProcesses={detectedProcesses}
        setDetectedProcesses={setDetectedProcesses}
        setNewRelease={setNewRelease}
        setUpdateAvailable={setUpdateAvailable}
        setUpdateProgress={setUpdateProgress}
        shouldUseHttpApi={shouldUseHttpApi}
        httpApiBase={httpApiBase}
        startLocalServiceWithConflictCheck={startLocalServiceWithConflictCheck}
        refreshStatus={refreshStatus}
        doStopService={doStopService}
        waitForServiceDown={waitForServiceDown}
        doStartLocalService={doStartLocalService}
        setView={setView}
      />
    );
  }

  function renderLLM() {
    return (
      <LLMView
        savedEndpoints={savedEndpoints}
        savedCompilerEndpoints={savedCompilerEndpoints}
        savedSttEndpoints={savedSttEndpoints}
        setSavedEndpoints={setSavedEndpoints}
        setSavedCompilerEndpoints={setSavedCompilerEndpoints}
        setSavedSttEndpoints={setSavedSttEndpoints}
        envDraft={envDraft}
        setEnvDraft={setEnvDraft}
        secretShown={secretShown}
        setSecretShown={setSecretShown}
        busy={busy}
        currentWorkspaceId={currentWorkspaceId}
        dataMode={dataMode}
        shouldUseHttpApi={shouldUseHttpApi}
        httpApiBase={httpApiBase}
        askConfirm={askConfirm}
        providers={providers}
        doLoadProviders={doLoadProviders}
        loadSavedEndpoints={loadSavedEndpoints}
        readWorkspaceFile={readWorkspaceFile}
        writeWorkspaceFile={writeWorkspaceFile}
        venvDir={venvDir}
        ensureEnvLoaded={ensureEnvLoaded}
        serviceRunning={!!serviceStatus?.running}
      />
    );
  }

  // FieldText/FieldBool/FieldSelect/FieldCombo/TelegramPairingCodeHint -> ./components/EnvFields.tsx
  // Wrapper closures that pass envDraft/onEnvChange automatically to extracted field components
  const _envBase = { envDraft, onEnvChange: setEnvDraft, busy };
  const FT = (p: { k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password" }) =>
    <FieldText key={p.k} {...p} {..._envBase} />;
  const FB = (p: { k: string; label: string; help?: string; defaultValue?: boolean }) =>
    <FieldBool key={p.k} {...p} {..._envBase} />;
  const FS = (p: { k: string; label: string; options: { value: string; label: string }[]; help?: string }) =>
    <FieldSelect key={p.k} {...p} {..._envBase} />;
  const FC = (p: { k: string; label: string; options: { value: string; label: string }[]; placeholder?: string; help?: string }) =>
    <FieldCombo key={p.k} {...p} {..._envBase} />;
  const FR = (p: { k: string; label: string; help?: string; min: number; max: number; step: number; defaultValue: number; unit?: string }) =>
    <FieldSlider key={p.k} {...p} {..._envBase} />;

  async function renderIntegrationsSave(keys: string[], successText: string) {
    if (!currentWorkspaceId) { notifyError(t("common.error")); return; }
    const _busyId = notifyLoading(t("common.loading"));
    try {
      const result = await saveEnvKeys(keys);
      if (result.restartRequired) {
        toast.warning(
          t("config.savedNeedRestart", "已保存，需要重启服务才能生效"),
          {
            duration: 8000,
            action: {
              label: t("config.restartNow", "立即重启"),
              onClick: () => restartService(),
            },
          },
        );
      } else {
        notifySuccess(successText);
      }
    } finally {
      dismissLoading(_busyId);
    }
  }

  const _configViewProps = {
    envDraft, setEnvDraft,
    currentWorkspaceId,
    disabledViews, toggleViewDisabled,
  };

  function renderIM(wizardMode?: boolean) {
    return (
      <IMConfigView
        {..._configViewProps}
        venvDir={venvDir}
        apiBaseUrl={apiBaseUrl}
        onRequestRestart={restartService}
        wizardMode={wizardMode}
        multiAgentEnabled={multiAgentEnabled}
      />
    );
  }

  function renderTools() {
    const keysTools = [
      "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FORCE_IPV4",
      "TOOL_MAX_PARALLEL", "FORCE_TOOL_CALL_MAX_RETRIES", "FORCE_TOOL_CALL_IM_FLOOR", "CONFIRMATION_TEXT_MAX_RETRIES",
      "ALLOW_PARALLEL_TOOLS_WITH_INTERRUPT_CHECKS",
      "MCP_ENABLED", "MCP_TIMEOUT",
      "DESKTOP_ENABLED", "DESKTOP_DEFAULT_MONITOR", "DESKTOP_COMPRESSION_QUALITY",
      "DESKTOP_MAX_WIDTH", "DESKTOP_MAX_HEIGHT", "DESKTOP_CACHE_TTL",
      "DESKTOP_UIA_TIMEOUT", "DESKTOP_UIA_RETRY_INTERVAL", "DESKTOP_UIA_MAX_RETRIES",
      "DESKTOP_VISION_ENABLED", "DESKTOP_VISION_MAX_RETRIES", "DESKTOP_VISION_TIMEOUT",
      "DESKTOP_CLICK_DELAY", "DESKTOP_TYPE_INTERVAL", "DESKTOP_MOVE_DURATION",
      "DESKTOP_FAILSAFE", "DESKTOP_PAUSE",
      "WHISPER_MODEL", "WHISPER_LANGUAGE", "GITHUB_TOKEN",
    ];

    const list = skillsDetail || [];
    const systemSkills = list.filter((s) => !!s.system);
    const externalSkills = list.filter((s) => !s.system);

    return (
      <>
        <div className="card">
          <h3 className="text-base font-bold tracking-tight">{t("config.toolsTitle")}</h3>
          <p className="text-sm text-muted-foreground mt-1 mb-3">{t("config.toolsHint")}</p>

          {/* ── MCP ── */}
          <details className="group rounded-lg border border-border">
            <summary className="cursor-pointer flex items-center justify-between px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-1.5">
                <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90 text-muted-foreground" />
                {t("config.toolsMCP")}
              </span>
              <label className="inline-flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none" onClick={(e) => e.stopPropagation()}>
                <span>{disabledViews.includes("mcp") ? t("config.toolsSkillsDisabled") : t("config.toolsSkillsEnabled")}</span>
                <div
                  onClick={async () => {
                    const willDisable = !disabledViews.includes("mcp");
                    toggleViewDisabled("mcp");
                    setEnvDraft((p) => ({ ...p, MCP_ENABLED: willDisable ? "false" : "true" }));
                    try {
                      const entries = { MCP_ENABLED: willDisable ? "false" : "true" };
                      if (shouldUseHttpApi()) {
                        await safeFetch(`${httpApiBase()}/api/config/env`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ entries }),
                        });
                        notifySuccess(willDisable
                          ? t("config.mcpDisabledNeedRestart", { defaultValue: "MCP 已禁用，重启后生效" })
                          : t("config.mcpEnabledNeedRestart", { defaultValue: "MCP 已启用，重启后生效" }));
                      }
                    } catch { /* ignore */ }
                  }}
                  className="relative shrink-0 transition-colors duration-200 rounded-full"
                  style={{
                    width: 40, height: 22,
                    background: disabledViews.includes("mcp") ? "var(--line, #d1d5db)" : "var(--ok, #22c55e)",
                  }}
                >
                  <div className="absolute top-0.5 rounded-full bg-white shadow-sm transition-[left] duration-200" style={{
                    width: 18, height: 18,
                    left: disabledViews.includes("mcp") ? 2 : 20,
                  }} />
              </div>
              </label>
            </summary>
            <div className="flex flex-col gap-2.5 px-4 py-3 border-t border-border">
              <div className="grid2">
                {FT({ k: "MCP_TIMEOUT", label: "Timeout (s)", placeholder: "60" })}
              </div>
            </div>
          </details>

          {/* ── Skills ── */}
          <details className="group/skills rounded-lg border border-border mt-2">
            <summary className="cursor-pointer flex items-center justify-between px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-1.5">
                <ChevronRight className="size-4 shrink-0 transition-transform group-open/skills:rotate-90 text-muted-foreground" />
                {t("config.toolsSkills")}
              </span>
              <label className="inline-flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none" onClick={(e) => e.stopPropagation()}>
                <span>{disabledViews.includes("skills") ? t("config.toolsSkillsDisabled") : t("config.toolsSkillsEnabled")}</span>
                <div
                  onClick={() => toggleViewDisabled("skills")}
                  className="relative shrink-0 transition-colors duration-200 rounded-full"
                  style={{
                    width: 40, height: 22,
                    background: disabledViews.includes("skills") ? "var(--line, #d1d5db)" : "var(--ok, #22c55e)",
                  }}
                >
                  <div className="absolute top-0.5 rounded-full bg-white shadow-sm transition-[left] duration-200" style={{
                    width: 18, height: 18,
                    left: disabledViews.includes("skills") ? 2 : 20,
                  }} />
                </div>
              </label>
            </summary>
            <div className="flex items-center gap-2 px-4 py-3 border-t border-border">
              <button
                className="px-3 py-1.5 text-xs font-medium rounded-md border border-border hover:bg-accent/50 transition-colors"
                onClick={() => {
                  if (!skillsDetail) return;
                  const m: Record<string, boolean> = {};
                  for (const s of skillsDetail) { if (s?.skill_id) m[s.skill_id] = true; }
                  setSkillsSelection(m);
                  setSkillsTouched(true);
                }}
              >
                {t("config.toolsEnableAll")}
              </button>
              <button
                className="px-3 py-1.5 text-xs font-medium rounded-md border border-border hover:bg-accent/50 transition-colors"
                onClick={() => {
                  if (!skillsDetail) return;
                  const m: Record<string, boolean> = {};
                  for (const s of skillsDetail) { if (s?.skill_id) m[s.skill_id] = false; }
                  setSkillsSelection(m);
                  setSkillsTouched(true);
                }}
              >
                {t("config.toolsDisableAll")}
              </button>
              <span className="text-xs text-muted-foreground ml-auto">
                {skillsDetail ? t("config.toolsSkillsCount", { enabled: Object.values(skillsSelection).filter(Boolean).length, total: skillsDetail.length }) : ""}
              </span>
            </div>
          </details>

          {/* ── Desktop Automation ── */}
          <details className="group/desktop rounded-lg border border-border mt-2">
            <summary className="cursor-pointer flex items-center justify-between px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-1.5">
                <ChevronRight className="size-4 shrink-0 transition-transform group-open/desktop:rotate-90 text-muted-foreground" />
                {t("config.toolsDesktop")}
              </span>
              <label className="inline-flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none" onClick={(e) => e.stopPropagation()}>
                <span>{envDraft["DESKTOP_ENABLED"] === "false" ? t("config.toolsSkillsDisabled") : t("config.toolsSkillsEnabled")}</span>
                <div
                  onClick={() => setEnvDraft((p) => ({ ...p, DESKTOP_ENABLED: p.DESKTOP_ENABLED === "false" ? "true" : "false" }))}
                  className="relative shrink-0 transition-colors duration-200 rounded-full"
                  style={{
                    width: 40, height: 22,
                    background: envDraft["DESKTOP_ENABLED"] === "false" ? "var(--line, #d1d5db)" : "var(--ok, #22c55e)",
                  }}
                >
                  <div className="absolute top-0.5 rounded-full bg-white shadow-sm transition-[left] duration-200" style={{
                    width: 18, height: 18,
                    left: envDraft["DESKTOP_ENABLED"] === "false" ? 2 : 20,
                  }} />
                </div>
              </label>
            </summary>
            <div className="flex flex-col gap-2.5 px-4 py-3 border-t border-border">
              <div className="grid3">
                {FT({ k: "DESKTOP_DEFAULT_MONITOR", label: t("config.toolsMonitor"), placeholder: "0" })}
                {FT({ k: "DESKTOP_MAX_WIDTH", label: t("config.toolsMaxW"), placeholder: "1920" })}
                {FT({ k: "DESKTOP_MAX_HEIGHT", label: t("config.toolsMaxH"), placeholder: "1080" })}
              </div>
              <details className="group/deskadv rounded-lg border border-border">
                <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors text-muted-foreground">
                  <ChevronRight className="size-4 shrink-0 transition-transform group-open/deskadv:rotate-90" />
                  {t("config.toolsDesktopAdvanced")}
                </summary>
                <div className="flex flex-col gap-2.5 px-4 py-3 border-t border-border">
                  <div className="grid3">
                    {FT({ k: "DESKTOP_COMPRESSION_QUALITY", label: t("config.toolsCompression"), placeholder: "85" })}
                    {FT({ k: "DESKTOP_CACHE_TTL", label: "Cache TTL", placeholder: "1.0" })}
                    {FB({ k: "DESKTOP_FAILSAFE", label: "Failsafe" })}
                  </div>
                  {FB({ k: "DESKTOP_VISION_ENABLED", label: t("config.toolsVision"), help: t("config.toolsVisionHelp") })}
                  <div className="grid3">
                    {FT({ k: "DESKTOP_CLICK_DELAY", label: "Click Delay", placeholder: "0.1" })}
                    {FT({ k: "DESKTOP_TYPE_INTERVAL", label: "Type Interval", placeholder: "0.03" })}
                    {FT({ k: "DESKTOP_MOVE_DURATION", label: "Move Duration", placeholder: "0.15" })}
                  </div>
                </div>
              </details>
            </div>
          </details>

          {/* ── Model Downloads & Voice Recognition — hidden (not actively used) ── */}

          {/* ── Tool Parallelism ── */}
          <details className="group/net rounded-lg border border-border mt-2">
            <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <ChevronRight className="size-4 shrink-0 transition-transform group-open/net:rotate-90 text-muted-foreground" />
              {t("config.toolsParallel")}
            </summary>
            <div className="flex flex-col gap-2.5 px-4 py-3 border-t border-border">
              <div className="grid2">
                {FT({ k: "TOOL_MAX_PARALLEL", label: t("config.toolsParallel"), placeholder: "1", help: t("config.toolsParallelHelp") })}
              </div>
            </div>
          </details>

          {/* ── Hallucination Guard ── */}
          <details className="group/hguard rounded-lg border border-border mt-2">
            <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <ChevronDown className="size-4 shrink-0 transition-transform group-open/hguard:rotate-180 text-muted-foreground" />
              {t("config.toolsHallucinationGuard")}
            </summary>
            <div className="flex flex-col gap-2.5 px-4 py-3 border-t border-border">
              <p className="text-xs text-muted-foreground">{t("config.toolsHallucinationGuardHint")}</p>
              <div className="grid2">
                {FS({ k: "FORCE_TOOL_CALL_MAX_RETRIES", label: t("config.toolsForceRetry"), options: [
                  { value: "0", label: t("config.guardOff") },
                  { value: "1", label: "1" },
                  { value: "2", label: "2" },
                  { value: "3", label: "3" },
                ] })}
                {FS({ k: "FORCE_TOOL_CALL_IM_FLOOR", label: t("config.toolsImFloor"), options: [
                  { value: "0", label: t("config.guardSameAsGlobal") },
                  { value: "1", label: "1" },
                  { value: "2", label: "2" },
                ] })}
              </div>
              <div className="grid2">
                {FS({ k: "CONFIRMATION_TEXT_MAX_RETRIES", label: t("config.toolsConfirmTextRetry"), options: [
                  { value: "0", label: t("config.guardOff") },
                  { value: "1", label: "1" },
                  { value: "2", label: "2" },
                  { value: "3", label: "3" },
                ] })}
              </div>
            </div>
          </details>

          {/* ── Skills toggle (moved below, no longer here) ── */}

        </div>

        {/* ── CLI 命令行工具管理 (desktop only) ── */}
        {IS_TAURI && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 className="text-base font-bold tracking-tight">{t("config.cliTitle")}</h3>
          <p className="text-sm text-muted-foreground mt-1 mb-3">{t("config.cliDesc")}</p>
          <CliManager />
        </div>
        )}
      </>
    );
  }

  // CliManager -> ./components/CliManager.tsx

  function renderAgentSystem() {
    return <AgentSystemView {..._configViewProps} serviceRunning={!!serviceStatus?.running} apiBaseUrl={apiBaseUrl} />;
  }

  function renderAdvanced() {
    return (
      <AdvancedView
        envDraft={envDraft}
        setEnvDraft={setEnvDraft}
        busy={busy}
        workspaces={workspaces}
        currentWorkspaceId={currentWorkspaceId}
        serviceStatus={serviceStatus}
        dataMode={dataMode}
        info={info}
        storeVisible={storeVisible}
        setStoreVisible={setStoreVisible}
        desktopVersion={desktopVersion}
        shouldUseHttpApi={shouldUseHttpApi}
        httpApiBase={httpApiBase}
        askConfirm={askConfirm}
        refreshAll={refreshAll}
        restartService={restartService}
        setView={setView}
      />
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
      "THINKING_MODE",
      "TOOL_MAX_PARALLEL",
      "FORCE_TOOL_CALL_MAX_RETRIES",
      "FORCE_TOOL_CALL_IM_FLOOR",
      "CONFIRMATION_TEXT_MAX_RETRIES",
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
      "MEMORY_MODE",
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
      "SCHEDULER_TIMEZONE",
      "SCHEDULER_TASK_TIMEOUT",
      // session
      "SESSION_TIMEOUT_MINUTES",
      "SESSION_MAX_HISTORY",
      "SESSION_STORAGE_PATH",
      // IM
      "IM_CHAIN_PUSH",
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
      "WEWORK_CALLBACK_HOST",
      "WEWORK_MODE",
      "WEWORK_WS_ENABLED",
      "WEWORK_WS_BOT_ID",
      "WEWORK_WS_SECRET",
      "DINGTALK_ENABLED",
      "DINGTALK_CLIENT_ID",
      "DINGTALK_CLIENT_SECRET",
      "ONEBOT_ENABLED",
      "ONEBOT_MODE",
      "ONEBOT_WS_URL",
      "ONEBOT_REVERSE_HOST",
      "ONEBOT_REVERSE_PORT",
      "ONEBOT_ACCESS_TOKEN",
      "QQBOT_ENABLED",
      "QQBOT_APP_ID",
      "QQBOT_APP_SECRET",
      "QQBOT_SANDBOX",
      "QQBOT_MODE",
      "QQBOT_WEBHOOK_PORT",
      "QQBOT_WEBHOOK_PATH",
      // WeChat (iLink Bot API)
      "WECHAT_ENABLED",
      "WECHAT_TOKEN",
      // MCP (docs/mcp-integration.md)
      "MCP_ENABLED",
      "MCP_TIMEOUT",
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
      "DESKTOP_VISION_MAX_RETRIES",
      "DESKTOP_VISION_TIMEOUT",
      "DESKTOP_CLICK_DELAY",
      "DESKTOP_TYPE_INTERVAL",
      "DESKTOP_MOVE_DURATION",
      "DESKTOP_FAILSAFE",
      "DESKTOP_PAUSE",
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
              {FT({ k: "HTTP_PROXY", label: "HTTP_PROXY", placeholder: "http://127.0.0.1:7890" })}
              {FT({ k: "HTTPS_PROXY", label: "HTTPS_PROXY", placeholder: "http://127.0.0.1:7890" })}
              {FT({ k: "ALL_PROXY", label: "ALL_PROXY", placeholder: "socks5://127.0.0.1:1080" })}
            </div>
            <div className="grid3" style={{ marginTop: 10 }}>
              {FB({ k: "FORCE_IPV4", label: "强制 IPv4", help: "某些 VPN/IPv6 环境下有用" })}
              {FT({ k: "TOOL_MAX_PARALLEL", label: "TOOL_MAX_PARALLEL", placeholder: "1", help: "单轮多工具并行数（默认 1=串行）" })}
              {FT({ k: "LOG_LEVEL", label: "LOG_LEVEL", placeholder: "INFO", help: "DEBUG/INFO/WARNING/ERROR" })}
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
                    {FT({ k: "TELEGRAM_BOT_TOKEN", label: "Bot Token", placeholder: "从 BotFather 获取（仅会显示一次）", type: "password" })}
                    {FT({ k: "TELEGRAM_PROXY", label: "代理（可选）", placeholder: "http://127.0.0.1:7890 / socks5://..." })}
                    {FB({ k: "TELEGRAM_REQUIRE_PAIRING", label: t("config.imPairing") })}
                    {FT({ k: "TELEGRAM_PAIRING_CODE", label: t("config.imPairingCode"), placeholder: t("config.imPairingCodeHint") })}
                    <TelegramPairingCodeHint currentWorkspaceId={currentWorkspaceId} envDraft={envDraft} onEnvChange={setEnvDraft} />
                    {FT({ k: "TELEGRAM_WEBHOOK_URL", label: "Webhook URL", placeholder: "https://..." })}
                  </>
                ),
              },
              {
                title: "飞书（需要 openakita[feishu]）",
                enabledKey: "FEISHU_ENABLED",
                apply: "https://open.feishu.cn/",
                body: (
                  <>
                    {FT({ k: "FEISHU_APP_ID", label: "App ID", placeholder: "" })}
                    {FT({ k: "FEISHU_APP_SECRET", label: "App Secret", placeholder: "", type: "password" })}
                  </>
                ),
              },
              (() => {
                const wMode = (envDraft["WEWORK_MODE"] || "websocket") as "http" | "websocket";
                const isWs = wMode === "websocket";
                return {
                  title: "企业微信（需要 openakita[wework]）",
                  enabledKey: isWs ? "WEWORK_WS_ENABLED" : "WEWORK_ENABLED",
                  apply: "https://work.weixin.qq.com/",
                  body: (
                    <>
                      <div style={{ marginBottom: 8 }}>
                        <div className="label">{t("config.imWeworkMode")}</div>
                        <ToggleGroup type="single" variant="outline" size="sm" value={wMode} onValueChange={(v) => {
                          if (!v) return;
                          const m = v as "http" | "websocket";
                                const oldKey = isWs ? "WEWORK_WS_ENABLED" : "WEWORK_ENABLED";
                                const newKey = m === "websocket" ? "WEWORK_WS_ENABLED" : "WEWORK_ENABLED";
                                setEnvDraft((d) => {
                                  const wasEnabled = (d[oldKey] || "false").toLowerCase() === "true";
                            const next: Record<string, string> = { ...d, WEWORK_MODE: m };
                            if (wasEnabled && oldKey !== newKey) { next[oldKey] = "false"; next[newKey] = "true"; }
                                  return next;
                                });
                        }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                          <ToggleGroupItem value="http">{t("config.imWeworkModeHttp")}</ToggleGroupItem>
                          <ToggleGroupItem value="websocket">{t("config.imWeworkModeWs")}</ToggleGroupItem>
                        </ToggleGroup>
                        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                          {isWs ? t("config.imWeworkModeWsHint") : t("config.imWeworkModeHttpHint")}
                        </div>
                      </div>
                      {isWs ? (
                        <>
                          {FT({ k: "WEWORK_WS_BOT_ID", label: t("config.imWeworkBotId") })}
                          {FT({ k: "WEWORK_WS_SECRET", label: t("config.imWeworkSecret"), type: "password" })}
                        </>
                      ) : (
                        <>
                          {FT({ k: "WEWORK_CORP_ID", label: "Corp ID" })}
                          {FT({ k: "WEWORK_TOKEN", label: "回调 Token", placeholder: "在企业微信后台「接收消息」设置中获取" })}
                          {FT({ k: "WEWORK_ENCODING_AES_KEY", label: "EncodingAESKey", placeholder: "在企业微信后台「接收消息」设置中获取", type: "password" })}
                          {FT({ k: "WEWORK_CALLBACK_PORT", label: "回调端口", placeholder: "9880" })}
                          <div style={{ fontSize: 12, color: "var(--muted)", margin: "4px 0 0 0", lineHeight: 1.6 }}>
                            💡 企业微信后台「接收消息服务器配置」的 URL 请填：<code style={{ background: "#f5f5f5", padding: "1px 5px", borderRadius: 4, fontSize: 11 }}>http://your-domain:9880/callback</code>
                          </div>
                        </>
                      )}
                    </>
                  ),
                };
              })(),
              {
                title: "钉钉（需要 openakita[dingtalk]）",
                enabledKey: "DINGTALK_ENABLED",
                apply: "https://open.dingtalk.com/",
                body: (
                  <>
                    {FT({ k: "DINGTALK_CLIENT_ID", label: "Client ID" })}
                    {FT({ k: "DINGTALK_CLIENT_SECRET", label: "Client Secret", type: "password" })}
                  </>
                ),
              },
              {
                title: "QQ 官方机器人（需要 openakita[qqbot]）",
                enabledKey: "QQBOT_ENABLED",
                apply: "https://bot.q.qq.com/wiki/develop/api-v2/",
                body: (
                  <>
                    {FT({ k: "QQBOT_APP_ID", label: "AppID", placeholder: "q.qq.com 开发设置" })}
                    {FT({ k: "QQBOT_APP_SECRET", label: "AppSecret", type: "password", placeholder: "q.qq.com 开发设置" })}
                    {FB({ k: "QQBOT_SANDBOX", label: t("config.imQQBotSandbox") })}
                    <div style={{ marginTop: 8 }}>
                      <div className="label">{t("config.imQQBotMode")}</div>
                      <ToggleGroup type="single" variant="outline" size="sm" value={envDraft["QQBOT_MODE"] || "websocket"} onValueChange={(v) => { if (v) setEnvDraft((d) => ({ ...d, QQBOT_MODE: v })); }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                        <ToggleGroupItem value="websocket">WebSocket</ToggleGroupItem>
                        <ToggleGroupItem value="webhook">Webhook</ToggleGroupItem>
                      </ToggleGroup>
                      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                        {(envDraft["QQBOT_MODE"] || "websocket") === "websocket"
                          ? t("config.imQQBotModeWsHint")
                          : t("config.imQQBotModeWhHint")}
                      </div>
                    </div>
                    {(envDraft["QQBOT_MODE"] === "webhook") && (
                      <>
                        {FT({ k: "QQBOT_WEBHOOK_PORT", label: t("config.imQQBotWebhookPort"), placeholder: "9890" })}
                        {FT({ k: "QQBOT_WEBHOOK_PATH", label: t("config.imQQBotWebhookPath"), placeholder: "/qqbot/callback" })}
                      </>
                    )}
                  </>
                ),
              },
              {
                title: t("config.imWechat") + "（需要 openakita[wechat]）",
                enabledKey: "WECHAT_ENABLED",
                apply: "",
                body: (
                  <>
                    {FT({ k: "WECHAT_TOKEN", label: "Token", type: "password", placeholder: t("wechat.tokenHint") })}
                    <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{t("wechat.hint")}</p>
                  </>
                ),
              },
              (() => {
                const obMode = (envDraft["ONEBOT_MODE"] || "reverse") as "reverse" | "forward";
                const isReverse = obMode === "reverse";
                return {
                title: "OneBot（需要 openakita[onebot] + NapCat/Lagrange）",
                enabledKey: "ONEBOT_ENABLED",
                apply: "https://github.com/botuniverse/onebot-11",
                body: (
                  <>
                      <div style={{ marginBottom: 8 }}>
                        <div className="label">{t("config.imOneBotMode")}</div>
                        <ToggleGroup type="single" variant="outline" size="sm" value={obMode} onValueChange={(v) => { if (v) setEnvDraft((d) => ({ ...d, ONEBOT_MODE: v })); }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                          <ToggleGroupItem value="reverse">{t("config.imOneBotModeReverse")}</ToggleGroupItem>
                          <ToggleGroupItem value="forward">{t("config.imOneBotModeForward")}</ToggleGroupItem>
                        </ToggleGroup>
                        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                          {isReverse ? t("config.imOneBotModeReverseHint") : t("config.imOneBotModeForwardHint")}
                        </div>
                      </div>
                      {isReverse ? (
                        <>
                          {FT({ k: "ONEBOT_REVERSE_HOST", label: t("config.imOneBotReverseHost"), placeholder: "0.0.0.0" })}
                          {FT({ k: "ONEBOT_REVERSE_PORT", label: t("config.imOneBotReversePort"), placeholder: "6700" })}
                        </>
                      ) : (
                        FT({ k: "ONEBOT_WS_URL", label: "WebSocket URL", placeholder: "ws://127.0.0.1:8080" })
                      )}
                      {FT({ k: "ONEBOT_ACCESS_TOKEN", label: "Access Token", type: "password", placeholder: t("config.imOneBotTokenHint") })}
                  </>
                ),
                };
              })(),
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
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <div className="label" style={{ marginBottom: 0 }}>MCP</div>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--fg2)", cursor: "pointer", userSelect: "none" }} onClick={(e) => e.stopPropagation()}>
                    <span>{envDraft["MCP_ENABLED"] === "false" ? "已禁用" : "已启用"}</span>
                    <div
                      onClick={() => setEnvDraft((p) => ({ ...p, MCP_ENABLED: p.MCP_ENABLED === "false" ? "true" : "false" }))}
                      style={{
                        position: "relative", width: 40, height: 22, borderRadius: 11,
                        background: envDraft["MCP_ENABLED"] === "false" ? "var(--line, #d1d5db)" : "var(--ok, #22c55e)",
                        transition: "background 0.2s", flexShrink: 0,
                      }}
                    >
                      <div style={{
                        position: "absolute", top: 2, width: 18, height: 18, borderRadius: 9,
                        background: "#fff", boxShadow: "0 1px 2px rgba(0,0,0,.15)",
                        left: envDraft["MCP_ENABLED"] === "false" ? 2 : 20,
                        transition: "left 0.2s",
                      }} />
                </div>
                  </label>
                </div>
                <div className="grid2">
                  {FT({ k: "MCP_TIMEOUT", label: "MCP_TIMEOUT", placeholder: "60" })}
                </div>
              </div>

              <div className="card" style={{ marginTop: 0 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <div className="label" style={{ marginBottom: 0 }}>桌面自动化（Windows）</div>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--fg2)", cursor: "pointer", userSelect: "none" }} onClick={(e) => e.stopPropagation()}>
                    <span>{envDraft["DESKTOP_ENABLED"] === "false" ? "已禁用" : "已启用"}</span>
                    <div
                      onClick={() => setEnvDraft((p) => ({ ...p, DESKTOP_ENABLED: p.DESKTOP_ENABLED === "false" ? "true" : "false" }))}
                      style={{
                        position: "relative", width: 40, height: 22, borderRadius: 11,
                        background: envDraft["DESKTOP_ENABLED"] === "false" ? "var(--line, #d1d5db)" : "var(--ok, #22c55e)",
                        transition: "background 0.2s", flexShrink: 0,
                      }}
                    >
                      <div style={{
                        position: "absolute", top: 2, width: 18, height: 18, borderRadius: 9,
                        background: "#fff", boxShadow: "0 1px 2px rgba(0,0,0,.15)",
                        left: envDraft["DESKTOP_ENABLED"] === "false" ? 2 : 20,
                        transition: "left 0.2s",
                      }} />
                </div>
                  </label>
                </div>
                <div className="divider" />
                <div className="grid3">
                  {FT({ k: "DESKTOP_DEFAULT_MONITOR", label: "默认显示器", placeholder: "0" })}
                  {FT({ k: "DESKTOP_MAX_WIDTH", label: "最大宽", placeholder: "1920" })}
                  {FT({ k: "DESKTOP_MAX_HEIGHT", label: "最大高", placeholder: "1080" })}
                </div>
                <div className="grid3" style={{ marginTop: 10 }}>
                  {FT({ k: "DESKTOP_COMPRESSION_QUALITY", label: "压缩质量", placeholder: "85" })}
                  {FT({ k: "DESKTOP_CACHE_TTL", label: "截图缓存秒", placeholder: "1.0" })}
                  {FB({ k: "DESKTOP_FAILSAFE", label: "failsafe", help: "鼠标移到角落中止（PyAutoGUI 风格）" })}
                </div>
                <div className="divider" />
                {FB({ k: "DESKTOP_VISION_ENABLED", label: "启用视觉", help: "用于屏幕理解/定位" })}
                <div className="grid3" style={{ marginTop: 10 }}>
                  {FT({ k: "DESKTOP_CLICK_DELAY", label: "click_delay", placeholder: "0.1" })}
                  {FT({ k: "DESKTOP_TYPE_INTERVAL", label: "type_interval", placeholder: "0.03" })}
                  {FT({ k: "DESKTOP_MOVE_DURATION", label: "move_duration", placeholder: "0.15" })}
                </div>
              </div>
            </div>

            <div className="divider" />
            <div className="grid3">
              {FC({ k: "WHISPER_MODEL", label: "WHISPER_MODEL", help: "tiny/base/small/medium/large", options: [
                { value: "tiny", label: "tiny (~39MB)" },
                { value: "base", label: "base (~74MB)" },
                { value: "small", label: "small (~244MB)" },
                { value: "medium", label: "medium (~769MB)" },
                { value: "large", label: "large (~1.5GB)" },
              ], placeholder: "base" })}
              {FS({ k: "WHISPER_LANGUAGE", label: "WHISPER_LANGUAGE", options: [
                { value: "zh", label: "中文 (zh)" },
                { value: "en", label: "English (en)" },
                { value: "auto", label: "Auto (自动检测)" },
              ] })}
              {FT({ k: "GITHUB_TOKEN", label: "GITHUB_TOKEN", placeholder: "", type: "password", help: "用于搜索/下载技能" })}
              {FT({ k: "DATABASE_PATH", label: "DATABASE_PATH", placeholder: "data/agent.db" })}
            </div>
          </div>

          <div className="card">
            <div className="cardTitle" style={{ fontSize: 14, marginBottom: 6 }}>
              灵魂与意志（核心配置）
            </div>
            <div className="cardHint">
              这些是系统内置能力的开关与参数。<b>内置项默认启用</b>（你随时可以关闭）。建议先用默认值跑通，再按需调优。
            </div>
            <div className="divider" />

            <details open>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>基础</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                {FT({ k: "AGENT_NAME", label: "Agent 名称", placeholder: "OpenAkita" })}
                {FT({ k: "MAX_ITERATIONS", label: "最大迭代次数", placeholder: "300" })}
                {FS({ k: "THINKING_MODE", label: t("config.agentThinking"), options: [
                  { value: "auto", label: t("config.agentThinkingAuto") },
                  { value: "always", label: t("config.agentThinkingAlways") },
                  { value: "never", label: t("config.agentThinkingNever") },
                ] })}
                {FT({ k: "DATABASE_PATH", label: "数据库路径", placeholder: "data/agent.db" })}
                {FS({ k: "LOG_LEVEL", label: "日志级别", options: [
                  { value: "DEBUG", label: "DEBUG" },
                  { value: "INFO", label: "INFO" },
                  { value: "WARNING", label: "WARNING" },
                  { value: "ERROR", label: "ERROR" },
                ] })}
              </div>
            </details>

            <div className="divider" />
            <details>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>日志高级</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                {FT({ k: "LOG_DIR", label: "日志目录", placeholder: "logs" })}
                {FT({ k: "LOG_FILE_PREFIX", label: "日志文件前缀", placeholder: "openakita" })}
                {FT({ k: "LOG_MAX_SIZE_MB", label: "单文件最大 MB", placeholder: "10" })}
                {FT({ k: "LOG_BACKUP_COUNT", label: "备份文件数", placeholder: "30" })}
                {FT({ k: "LOG_RETENTION_DAYS", label: "保留天数", placeholder: "30" })}
                {FT({ k: "LOG_FORMAT", label: "日志格式", placeholder: "%(asctime)s - %(name)s - %(levelname)s - %(message)s" })}
                {FB({ k: "LOG_TO_CONSOLE", label: "输出到控制台", help: "默认 true" })}
                {FB({ k: "LOG_TO_FILE", label: "输出到文件", help: "默认 true" })}
              </div>
            </details>

            <div className="divider" />
            <details>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>会话</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                {FT({ k: "SESSION_TIMEOUT_MINUTES", label: "会话超时（分钟）", placeholder: "30" })}
                {FT({ k: "SESSION_MAX_HISTORY", label: "会话最大历史条数", placeholder: "50" })}
                {FT({ k: "SESSION_STORAGE_PATH", label: "会话存储路径", placeholder: "data/sessions" })}
              </div>
            </details>

            <div className="divider" />
            <details open>
              <summary style={{ cursor: "pointer", fontWeight: 800, padding: "8px 0" }}>调度器</summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                {FT({ k: "SCHEDULER_TIMEZONE", label: "时区", placeholder: "Asia/Shanghai" })}
                {FT({ k: "SCHEDULER_TASK_TIMEOUT", label: "任务超时（秒）", placeholder: "600" })}
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

  // 构造端点摘要（供 ChatView 使用，仅启用的端点）
  const chatEndpoints: EndpointSummaryType[] = useMemo(() =>
    endpointSummary
      .filter((e) => e.enabled !== false)
      .map((e) => {
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


  // ── Onboarding Wizard 渲染 ──

  async function obLoadEnvCheck() {
    if (!IS_TAURI) return;
    try {
      const check = await invoke<typeof obEnvCheck>("check_environment");
      setObEnvCheck(check);
    } catch (e) {
      logger.warn("App", "check_environment failed", { error: String(e) });
    }
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
    if (!IS_TAURI) return;
    setObInstalling(true);
    setObInstallLog([]);
    setObDetailLog([]);
    setObHasErrors(false);

    const dateLabel = new Date().toISOString().slice(0, 19).replace("T", "_").replace(/:/g, "-");
    let obLogPath: string | null = null;
    try {
      obLogPath = await invoke<string>("start_onboarding_log", { dateLabel });
      if (obLogPath) {
        const configLines: string[] = [];
        configLines.push("");
        configLines.push("=== LLM 配置 ===");
        if (savedEndpoints.length === 0) {
          configLines.push("  (无)");
        } else {
          for (const e of savedEndpoints) {
            configLines.push(`  - ${e.name}: base_url=${(e as any).base_url || ""}, model=${(e as any).model || ""}, api_key_env=${(e as any).api_key_env || "(无)"}`);
          }
        }
        configLines.push("");
        configLines.push("=== IM 配置（仅键名，不记录密钥值）===");
        const imKeys = getAutoSaveKeysForStep("im");
        for (const k of imKeys) {
          const set = Object.prototype.hasOwnProperty.call(envDraft, k) && envDraft[k];
          configLines.push(`  - ${k}: ${set ? "(已设置)" : "(未设置)"}`);
        }
        configLines.push("");
        configLines.push("=== 流程日志 ===");
        invoke("append_onboarding_log_lines", { logPath: obLogPath, lines: configLines }).catch(() => {});
      }
    } catch {
    }

    const taskDefs: SetupTask[] = [
      { id: "workspace", label: "准备工作区", status: "pending" },
    ];
    taskDefs.push({ id: "backend-check", label: "检查后端环境", status: "pending" });
    const cliCommands: string[] = [];
    if (obCliOpenakita) cliCommands.push("openakita");
    if (obCliOa) cliCommands.push("oa");
    if (cliCommands.length > 0) {
      taskDefs.push({ id: "cli", label: `注册 CLI 命令 (${cliCommands.join(", ")})`, status: "pending" });
    }
    if (obAutostart) {
      taskDefs.push({ id: "autostart", label: t("onboarding.autostart.taskLabel"), status: "pending" });
    }
    taskDefs.push({ id: "service-start", label: "启动后端服务", status: "pending" });
    taskDefs.push({ id: "http-wait", label: "等待 HTTP 服务就绪", status: "pending" });
    taskDefs.push({ id: "llm-config", label: "保存 LLM 配置", status: savedEndpoints.length > 0 ? "pending" : "skipped" });
    taskDefs.push({ id: "env-save", label: "保存环境变量", status: "pending" });
    setObTasks(taskDefs);

    const log = (msg: string) => {
      setObInstallLog((prev) => [...prev, msg]);
      addDetailLog(msg);
      const now = new Date();
      const ts = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;
      const line = `[${ts}] ${msg}`;
      if (obLogPath) {
        invoke("append_onboarding_log", { logPath: obLogPath, line }).catch(() => {});
      }
    };
    const logTask = (label: string, status: string, detail?: string) => {
      const msg = detail ? `[任务] ${label}: ${status} - ${detail}` : `[任务] ${label}: ${status}`;
      const now = new Date();
      const ts = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;
      const line = `[${ts}] ${msg}`;
      if (obLogPath) {
        invoke("append_onboarding_log", { logPath: obLogPath, line }).catch(() => {});
      }
    };
    let hasErr = false;

    try {
      // ── STEP: workspace ──
      updateTask("workspace", { status: "running" });
      logTask("准备工作区", "running");
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
      logTask("准备工作区", "done");

      // ── STEP: llm-config ──
      if (savedEndpoints.length > 0) {
        updateTask("llm-config", { status: "running" });
        logTask("保存 LLM 配置", "running");
        const llmData = { endpoints: savedEndpoints, settings: {} };
        await invoke("workspace_write_file", {
          workspaceId: activeWsId,
          relativePath: "data/llm_endpoints.json",
          content: JSON.stringify(llmData, null, 2),
        });
        log(t("onboarding.progress.llmConfigSaved"));
        updateTask("llm-config", { status: "done", detail: `${savedEndpoints.length} 个端点` });
        logTask("保存 LLM 配置", "done", `${savedEndpoints.length} 个端点`);
      }

      // ── STEP: backend-check ──
      updateTask("backend-check", { status: "running" });
      logTask("检查后端环境", "running");
      try {
        const effectiveVenv = venvDir || (info ? joinPath(info.openakitaRootDir, "venv") : "");
        const backendInfo = await invoke<{
          bundled: boolean;
          venvReady: boolean;
          exePath: string;
          bundledChecked: string;
          venvChecked: string;
        }>("check_backend_availability", { venvDir: effectiveVenv });
        if (!backendInfo.bundled && !backendInfo.venvReady) {
          log("未找到可用后端，尝试自动创建 venv 并安装 openakita...");
          logTask("检查后端环境", "running", "创建 venv...");
          updateTask("backend-check", { detail: "创建 venv..." });
          const detectedPy = await invoke<Array<{ command: string[]; version: string }>>("detect_python");
          if (detectedPy.length > 0) {
            await invoke<string>("create_venv", { pythonCommand: detectedPy[0].command, venvDir: effectiveVenv });
            updateTask("backend-check", { detail: "安装 openakita..." });
            logTask("检查后端环境", "running", "安装 openakita...");
            await invoke<string>("pip_install", { venvDir: effectiveVenv, packageSpec: "openakita" });
            log("✓ 已自动安装后端环境");
          } else {
            log("⚠ 未检测到 Python 3.11+，无法自动创建后端环境");
            log(`  已检查路径: bundled=${backendInfo.bundledChecked} venv=${backendInfo.venvChecked}`);
            updateTask("backend-check", { status: "error", detail: "未找到 Python 3.11+" });
            logTask("检查后端环境", "error", "未找到 Python 3.11+");
          }
        } else {
          log(backendInfo.bundled ? "✓ 使用内置后端" : "✓ 使用 venv 后端");
        }
        if (!hasErr) {
          updateTask("backend-check", { status: "done" });
          logTask("检查后端环境", "done");
        }
      } catch (e) {
        log(`⚠ 后端环境检查失败: ${String(e)}`);
        updateTask("backend-check", { status: "error", detail: String(e).slice(0, 120) });
        logTask("检查后端环境", "error", String(e));
      }

      // ── STEP: cli ──
      if (cliCommands.length > 0) {
        updateTask("cli", { status: "running" });
        logTask(`注册 CLI 命令 (${cliCommands.join(", ")})`, "running");
        log("注册 CLI 命令...");
        try {
          const result = await invoke<string>("register_cli", {
            commands: cliCommands,
            addToPath: obCliAddToPath,
          });
          log(`✓ ${result}`);
          updateTask("cli", { status: "done" });
          logTask(`注册 CLI 命令 (${cliCommands.join(", ")})`, "done", result);
        } catch (e) {
          log(`⚠ CLI 命令注册失败: ${String(e)}`);
          updateTask("cli", { status: "error", detail: String(e) });
          logTask(`注册 CLI 命令 (${cliCommands.join(", ")})`, "error", String(e));
        }
      }

      // ── STEP: autostart ──
      if (obAutostart) {
        updateTask("autostart", { status: "running" });
        logTask(t("onboarding.autostart.taskLabel"), "running");
        try {
          await invoke("autostart_set_enabled", { enabled: true });
          setAutostartEnabled(true);
          log(t("onboarding.autostart.success"));
          updateTask("autostart", { status: "done" });
          logTask(t("onboarding.autostart.taskLabel"), "done");
        } catch (e) {
          log(t("onboarding.autostart.fail") + ": " + String(e));
          updateTask("autostart", { status: "error", detail: String(e).slice(0, 120) });
          logTask(t("onboarding.autostart.taskLabel"), "error", String(e));
        }
      }

      // ── STEP: service-start ──
      // The early-start in ob-welcome may have already launched the backend.
      // Probe first to avoid a redundant start (which is harmless but slow).
      updateTask("service-start", { status: "running" });
      logTask("启动后端服务", "running");
      const effectiveVenv = venvDir || (info ? joinPath(info.openakitaRootDir, "venv") : "");
      let httpReady = false;
      try {
        const earlyProbe = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(3000) }).then(r => r.ok).catch(() => false);
        if (earlyProbe) {
          log("✓ 后端已在运行（由 ob-welcome 提前启动）");
          setServiceStatus({ running: true, pid: null, pidFile: "" });
          setDataMode("remote");
          httpReady = true;
          updateTask("service-start", { status: "done", detail: "已在运行" });
          logTask("启动后端服务", "done", "已在运行");
          updateTask("http-wait", { status: "done", detail: "已就绪" });
          logTask("等待 HTTP 服务就绪", "done", "已就绪");
        } else {
          log(t("onboarding.progress.startingService"));
        await invoke("openakita_service_start", { venvDir: effectiveVenv, workspaceId: activeWsId });
        log(t("onboarding.progress.serviceStarted"));
        updateTask("service-start", { status: "done" });
        logTask("启动后端服务", "done");

        // ── STEP: http-wait ──
        let httpReady = false;
        updateTask("http-wait", { status: "running" });
        logTask("等待 HTTP 服务就绪", "running");
        log("等待 HTTP 服务就绪...");
        for (let i = 0; i < 20; i++) {
          await new Promise(r => setTimeout(r, 2000));
          updateTask("http-wait", { detail: `已等待 ${(i + 1) * 2}s...` });
          if (i > 0 && obLogPath) {
            const now = new Date();
            const ts = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;
            invoke("append_onboarding_log", { logPath: obLogPath, line: `[${ts}] [任务] 等待 HTTP 服务就绪: 已等待 ${(i + 1) * 2}s...` }).catch(() => {});
          }
          try {
            const res = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(3000) });
            if (res.ok) {
              log("✓ HTTP 服务已就绪");
              setServiceStatus({ running: true, pid: null, pidFile: "" });
              httpReady = true;
              updateTask("http-wait", { status: "done", detail: `${(i + 1) * 2}s` });
              logTask("等待 HTTP 服务就绪", "done", `${(i + 1) * 2}s`);
              break;
            }
          } catch { /* not ready yet */ }
          if (i % 5 === 4) log(`仍在等待 HTTP 服务启动... (${(i + 1) * 2}s)`);
        }
        if (!httpReady) {
          log("⚠ HTTP 服务尚未就绪，可进入主页面后手动刷新");
          updateTask("http-wait", { status: "error", detail: "超时" });
          logTask("等待 HTTP 服务就绪", "error", "超时");
          }
        }
      } catch (e) {
        const errStr = String(e);
        log(t("onboarding.progress.serviceStartFailed", { error: errStr }));
        updateTask("service-start", { status: "error", detail: errStr.slice(0, 120) });
        logTask("启动后端服务", "error", errStr.slice(0, 200));
        updateTask("http-wait", { status: "skipped" });
        logTask("等待 HTTP 服务就绪", "skipped", "服务启动失败");
        if (errStr.length > 200) {
          log('--- 详细错误信息 ---');
          log(errStr);
        }
        hasErr = true;
      }

      // ── STEP: llm-config (via HTTP API, after backend is ready) ──
      if (savedEndpoints.length > 0) {
        updateTask("llm-config", { status: "running" });
        logTask("保存 LLM 配置", "running");
        if (httpReady) {
          try {
            const base = httpApiBase();
            for (const ep of savedEndpoints) {
              const apiKey = envDraft[(ep as any).api_key_env] || "";
              await safeFetch(`${base}/api/config/save-endpoint`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  endpoint: ep,
                  api_key: apiKey || null,
                  endpoint_type: "endpoints",
                }),
              });
            }
            for (const ep of savedCompilerEndpoints) {
              const apiKey = envDraft[(ep as any).api_key_env] || "";
              await safeFetch(`${base}/api/config/save-endpoint`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  endpoint: ep,
                  api_key: apiKey || null,
                  endpoint_type: "compiler_endpoints",
                }),
              });
            }
            for (const ep of savedSttEndpoints) {
              const apiKey = envDraft[(ep as any).api_key_env] || "";
              await safeFetch(`${base}/api/config/save-endpoint`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  endpoint: ep,
                  api_key: apiKey || null,
                  endpoint_type: "stt_endpoints",
                }),
              });
            }
            log(t("onboarding.progress.llmConfigSaved"));
            updateTask("llm-config", { status: "done", detail: `${savedEndpoints.length + savedCompilerEndpoints.length + savedSttEndpoints.length} 个端点` });
            logTask("保存 LLM 配置", "done", `${savedEndpoints.length + savedCompilerEndpoints.length + savedSttEndpoints.length} 个端点`);
          } catch (e) {
            log(`⚠ LLM 配置保存失败: ${String(e)}`);
            updateTask("llm-config", { status: "error", detail: String(e).slice(0, 120) });
            logTask("保存 LLM 配置", "error", String(e));
            hasErr = true;
          }
        } else {
          log("⚠ HTTP 服务未就绪，使用 Tauri 直接写入 LLM 配置");
          try {
            const llmData = { endpoints: savedEndpoints, compiler_endpoints: savedCompilerEndpoints, stt_endpoints: savedSttEndpoints, settings: {} };
            await invoke("workspace_write_file", {
              workspaceId: activeWsId,
              relativePath: "data/llm_endpoints.json",
              content: JSON.stringify(llmData, null, 2),
            });
            log(t("onboarding.progress.llmConfigSaved"));
            updateTask("llm-config", { status: "done", detail: `${savedEndpoints.length} 个端点 (Tauri)` });
            logTask("保存 LLM 配置", "done", `${savedEndpoints.length} 个端点 (Tauri 回退)`);
          } catch (e) {
            log(`⚠ LLM 配置保存失败: ${String(e)}`);
            updateTask("llm-config", { status: "error", detail: String(e).slice(0, 120) });
            logTask("保存 LLM 配置", "error", String(e));
            hasErr = true;
          }
        }
      }

      // ── STEP: env-save (IM and other non-LLM env vars) ──
      updateTask("env-save", { status: "running" });
      logTask("保存环境变量", "running");
      try {
        const imKeys = getAutoSaveKeysForStep("im");
        const entries: Record<string, string> = {};
        for (const k of imKeys) {
          if (Object.prototype.hasOwnProperty.call(envDraft, k) && envDraft[k]) {
            entries[k] = envDraft[k];
          }
        }
        if (!httpReady) {
          for (const ep of [...savedEndpoints, ...savedCompilerEndpoints, ...savedSttEndpoints]) {
            const keyName = (ep as any).api_key_env;
            if (keyName && Object.prototype.hasOwnProperty.call(envDraft, keyName) && envDraft[keyName]) {
              entries[keyName] = envDraft[keyName];
            }
          }
        }
        if (Object.keys(entries).length > 0) {
          if (httpReady) {
            await safeFetch(`${httpApiBase()}/api/config/env`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ entries }),
            });
          } else if (IS_TAURI && activeWsId) {
            const tauriEntries = Object.entries(entries).map(([key, value]) => ({ key, value }));
            await invoke("workspace_update_env", { workspaceId: activeWsId, entries: tauriEntries });
          }
          log(t("onboarding.progress.envSaved") || "✓ 环境变量已保存");
        }
        updateTask("env-save", { status: "done", detail: `${Object.keys(entries).length} 项` });
        logTask("保存环境变量", "done", `${Object.keys(entries).length} 项`);
      } catch (e) {
        log(`⚠ 保存环境变量失败: ${String(e)}`);
        updateTask("env-save", { status: "error", detail: String(e) });
        logTask("保存环境变量", "error", String(e));
        hasErr = true;
      }

      log(t("onboarding.progress.done"));
    } catch (e) {
      log(t("onboarding.progress.error", { error: String(e) }));
      hasErr = true;
    } finally {
      if (obLogPath) {
        log(t("onboarding.installLogSaved", { path: obLogPath }) || `安装日志已保存至: ${obLogPath}`);
      }
      setObHasErrors(hasErr);
      setObInstalling(false);
      setObStep("ob-done");
    }
  }

  function renderOnboarding() {
    // Progress/done are transitional states and should not create extra indicator dots.
    const obStepDots = ["ob-welcome", "ob-agreement", "ob-llm", "ob-im", "ob-cli"] as OnboardingStep[];
    const obCurrentIdxRaw = obStepDots.indexOf(obStep);
    const obCurrentIdx = obCurrentIdxRaw >= 0 ? obCurrentIdxRaw : obStepDots.length - 1;

    const obStepLabels: Record<string, string> = {
      "ob-welcome": t("onboarding.step.welcome", "欢迎"),
      "ob-agreement": t("onboarding.step.agreement", "协议"),
      "ob-llm": t("onboarding.step.llm", "模型"),
      "ob-im": t("onboarding.step.im", "通讯"),
      "ob-cli": t("onboarding.step.cli", "完成"),
    };

    const stepIndicator = (
      <div className="flex flex-col items-center gap-1 py-4">
        <div className="flex items-center gap-3">
          {obCurrentIdx > 0 && (
            <button
              onClick={() => setObStep(obStepDots[obCurrentIdx - 1])}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-0.5 rounded"
              style={{ cursor: "pointer", background: "transparent", border: "none" }}
            >
              ← {t("common.back", "返回")}
            </button>
          )}
        {obStepDots.map((s, i) => (
            <div key={s} className="flex flex-col items-center gap-1" style={{ minWidth: 40 }}>
              <div
                className={`size-2 rounded-full transition-all duration-200 ${
                  i === obCurrentIdx
                    ? "bg-primary scale-[1.3]"
                    : i < obCurrentIdx
                      ? "bg-emerald-500"
                      : "bg-muted-foreground/25"
                }`}
              />
              <span className={`text-[10px] transition-opacity ${i === obCurrentIdx ? "text-foreground font-medium" : "text-muted-foreground/50"}`}>
                {obStepLabels[s] || ""}
              </span>
            </div>
          ))}
        </div>
      </div>
    );

    switch (obStep) {
      case "ob-welcome":
        return (
          <div className="obPage">
            <div className="flex flex-col items-center text-center max-w-[520px] gap-5">
              <img src={logoUrl} alt="OpenAkita" className="w-20 h-20 rounded-2xl shadow-lg mb-1" />
              <div className="space-y-2">
                <h1 className="text-[28px] font-bold tracking-tight text-foreground">{t("onboarding.welcome.title")}</h1>
                <p className="text-sm text-muted-foreground leading-relaxed">{t("onboarding.welcome.desc")}</p>
              </div>

              {obEnvCheck && (
                <>
                  {obEnvCheck.conflicts.length > 0 && (
                    <Card className={`w-full border text-left text-[13px] ${
                      obEnvCheck.conflicts.some(c => c.includes("失败") || c.includes("进程"))
                        ? "border-amber-300 bg-amber-50/60 dark:border-amber-500/40 dark:bg-amber-950/30"
                        : "border-emerald-300 bg-emerald-50/60 dark:border-emerald-500/40 dark:bg-emerald-950/30"
                    }`}>
                      <CardContent className="py-3 px-4 space-y-2">
                        <div className="flex items-center gap-2 font-semibold">
                          {obEnvCheck.conflicts.some(c => c.includes("失败") || c.includes("进程"))
                            ? <AlertTriangle className="size-4 text-amber-500 shrink-0" />
                            : <CheckCircle2 className="size-4 text-emerald-500 shrink-0" />}
                        {obEnvCheck.conflicts.some(c => c.includes("失败") || c.includes("进程"))
                          ? t("onboarding.welcome.envWarning")
                          : t("onboarding.welcome.envCleaned")}
                        </div>
                        <ul className="ml-5 list-disc space-y-0.5">
                        {obEnvCheck.conflicts.map((c, i) => <li key={i}>{c}</li>)}
                      </ul>
                        <p className="text-xs text-muted-foreground">
                        检查路径: {obEnvCheck.openakitaRoot ?? "(未知)"}
                      </p>
                        <Button variant="secondary" size="sm" onClick={() => obLoadEnvCheck()}>
                        重新检测环境
                        </Button>
                      </CardContent>
                    </Card>
                  )}
                  {obEnvCheck.conflicts.length === 0 && (
                    <p className="text-xs text-muted-foreground/75">
                      检查路径: {obEnvCheck.openakitaRoot ?? "(未知)"}
                    </p>
                  )}
                </>
              )}

              {obDetectedService && (
                <Card className="w-full border border-emerald-300 bg-emerald-50/60 dark:border-emerald-500/40 dark:bg-emerald-950/30 text-left text-[13px]">
                  <CardContent className="py-3 px-4 space-y-2">
                    <div className="flex items-center gap-2 font-semibold">
                      <CheckCircle2 className="size-4 text-emerald-500 shrink-0" />
                      {t("onboarding.welcome.serviceDetected")}
                    </div>
                    <p className="text-muted-foreground">
                    {t("onboarding.welcome.serviceDetectedDesc", { version: obDetectedService.version })}
                  </p>
                    <Button size="sm" onClick={() => obConnectExistingService()}>
                    {t("onboarding.welcome.connectExisting")}
                    </Button>
                  </CardContent>
                </Card>
              )}

              <div className="w-full max-w-[460px] mt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="gap-1.5 text-xs text-muted-foreground px-2 h-7"
                  onClick={async () => {
                    if (!obShowCustomRoot) {
                      try {
                        const info = await invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>("get_root_dir_info");
                        setObCurrentRoot(info.currentRoot);
                        if (info.customRoot) {
                          setObCustomRootInput(info.customRoot);
                          setObCustomRootApplied(true);
                        }
                      } catch {}
                    }
                    setObShowCustomRoot((v) => !v);
                  }}
                >
                  <ChevronRight className={`size-3.5 transition-transform duration-200 ${obShowCustomRoot ? "rotate-90" : ""}`} />
                  {t("onboarding.welcome.customRootToggle")}
                </Button>

                {obShowCustomRoot && (
                  <Card className="mt-2 shadow-sm">
                    <CardContent className="py-4 px-4 space-y-3">
                      <p className="text-xs text-muted-foreground leading-relaxed">{t("onboarding.welcome.customRootHint")}</p>
                    {obCurrentRoot && (
                        <p className="text-[11px] text-muted-foreground/60 break-all">
                        {t("onboarding.welcome.customRootCurrent", { path: obCurrentRoot })}
                      </p>
                    )}
                      <div className="flex gap-2 items-center">
                        <Input
                          className="flex-1 h-8 text-[13px]"
                        value={obCustomRootInput}
                        onChange={(e) => { setObCustomRootInput(e.target.value); setObCustomRootApplied(false); }}
                        placeholder={t("onboarding.welcome.customRootPlaceholder")}
                      />
                        <Button
                          size="sm"
                          className="h-8 shrink-0"
                        disabled={!obCustomRootInput.trim() || obCustomRootApplied || obCustomRootBusy}
                        onClick={async () => {
                          if (obCustomRootBusy) return;
                          setObCustomRootBusy(true);
                          try {
                            const info = await invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>(
                              "set_custom_root_dir", { path: obCustomRootInput.trim(), migrate: obCustomRootMigrate }
                            );
                            setObCurrentRoot(info.currentRoot);
                            setObCustomRootApplied(true);
                              notifySuccess(t("onboarding.welcome.customRootApplied", { path: info.currentRoot }));
                            obLoadEnvCheck();
                          } catch (e: any) {
                              notifyError(String(e));
                          } finally {
                            setObCustomRootBusy(false);
                          }
                        }}
                      >
                          {obCustomRootBusy ? <Loader2 className="size-3.5 animate-spin" /> : t("onboarding.welcome.customRootApply")}
                        </Button>
                    </div>
                      <div className="flex items-center gap-2">
                        <Checkbox
                          id="ob-migrate"
                          checked={obCustomRootMigrate}
                          onCheckedChange={(v) => setObCustomRootMigrate(!!v)}
                        />
                        <Label htmlFor="ob-migrate" className="text-xs cursor-pointer font-normal">
                        {t("onboarding.welcome.customRootMigrate")}
                        </Label>
                    </div>
                    {obCustomRootApplied && obCustomRootInput.trim() && (
                        <Button
                          variant="link"
                          className="h-auto p-0 text-[11px] text-muted-foreground"
                        onClick={async () => {
                          try {
                            const info = await invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>(
                              "set_custom_root_dir", { path: null, migrate: false }
                            );
                            setObCurrentRoot(info.currentRoot);
                            setObCustomRootInput("");
                            setObCustomRootApplied(false);
                              notifySuccess(t("onboarding.welcome.customRootDefault") + ": " + info.currentRoot);
                            obLoadEnvCheck();
                          } catch (e: any) {
                              notifyError(String(e));
                          }
                        }}
                      >
                        {t("onboarding.welcome.customRootDefault")}
                        </Button>
                    )}
                    </CardContent>
                  </Card>
                )}
              </div>

              <Button
                size="lg"
                className="mt-2 px-10 rounded-xl text-[15px]"
                onClick={async () => {
                  let earlyStartWsId = currentWorkspaceId || "";
                  try {
                    const wsList = await invoke<WorkspaceSummary[]>("list_workspaces");
                    if (!wsList.length) {
                      const wsId = "default";
                      await invoke("create_workspace", { name: t("onboarding.defaultWorkspace"), id: wsId, setCurrent: true });
                      await invoke("set_current_workspace", { id: wsId });
                      setCurrentWorkspaceId(wsId);
                      setWorkspaces([{ id: wsId, name: t("onboarding.defaultWorkspace"), path: "", isCurrent: true }]);
                      earlyStartWsId = wsId;
                    } else {
                      setWorkspaces(wsList);
                      if (!currentWorkspaceId && wsList.length > 0) {
                        setCurrentWorkspaceId(wsList[0].id);
                      }
                      earlyStartWsId = currentWorkspaceId || wsList[0]?.id || "";
                    }
                  } catch (e) {
                    logger.warn("App", "ob: create default workspace failed", { error: String(e) });
                  }

                  // Kick off backend startup in background so HTTP API is
                  // likely ready by the time the user reaches ob-llm.
                  if (IS_TAURI && earlyStartWsId) {
                    const wsId = earlyStartWsId;
                    const effectiveVenv = venvDir || (info ? joinPath(info.openakitaRootDir, "venv") : "");
                    (async () => {
                      try {
                        const backendInfo = await invoke<{
                          bundled: boolean; venvReady: boolean; exePath: string;
                          bundledChecked: string; venvChecked: string;
                        }>("check_backend_availability", { venvDir: effectiveVenv });
                        if (!backendInfo.bundled && !backendInfo.venvReady) return;
                        await invoke("openakita_service_start", { venvDir: effectiveVenv, workspaceId: wsId });
                        for (let i = 0; i < 15; i++) {
                          await new Promise(r => setTimeout(r, 2000));
                          try {
                            const res = await fetch("http://127.0.0.1:18900/api/health", { signal: AbortSignal.timeout(3000) });
                            if (res.ok) {
                              setServiceStatus({ running: true, pid: null, pidFile: "" });
                              setDataMode("remote");
                              break;
                            }
                          } catch { /* not ready yet */ }
                        }
                      } catch (e) {
                        logger.warn("App", "ob: early backend start failed, will retry in ob-progress", { error: String(e) });
                      }
                    })();
                  }

                  setObStep("ob-agreement");
                }}
              >
                {t("onboarding.welcome.start")}
              </Button>
            </div>
            {stepIndicator}
          </div>
        );

      case "ob-agreement":
        return (
          <div className="obPage">
            <div className="obContent">
              <h2 className="obStepTitle">{t("onboarding.agreement.title")}</h2>
              <p className="obStepDesc">{t("onboarding.agreement.subtitle")}</p>
              <Card className="text-left">
                <CardContent className="py-5 px-5 space-y-4">
                  <div className="whitespace-pre-wrap text-[13px] leading-[1.7] max-h-[240px] overflow-y-auto rounded-lg border bg-muted/40 p-4 text-foreground">
                  {t("onboarding.agreement.content")}
                </div>
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold">{t("onboarding.agreement.confirmLabel")}</Label>
                    <Input
                  value={obAgreementInput}
                  onChange={(e) => { setObAgreementInput(e.target.value); setObAgreementError(false); }}
                  placeholder={t("onboarding.agreement.confirmPlaceholder")}
                      aria-invalid={obAgreementError || undefined}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      if (obAgreementInput.trim() === t("onboarding.agreement.confirmText")) {
                        setObAgreementError(false);
                        setObStep("ob-llm");
                      } else {
                        setObAgreementError(true);
                      }
                    }
                  }}
                />
                {obAgreementError && (
                      <p className="text-[13px] text-destructive">{t("onboarding.agreement.errorMismatch")}</p>
                )}
              </div>
                </CardContent>
              </Card>
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <Button variant="outline" onClick={() => setObStep("ob-welcome")}>{t("config.prev")}</Button>
                <Button
                  onClick={() => {
                    if (obAgreementInput.trim() === t("onboarding.agreement.confirmText")) {
                      setObAgreementError(false);
                      setObStep("ob-llm");
                    } else {
                      setObAgreementError(true);
                    }
                  }}
                >
                  {t("onboarding.agreement.proceed")}
                </Button>
              </div>
            </div>
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
                <Button variant="outline" onClick={() => setObStep("ob-agreement")}>{t("config.prev")}</Button>
                {savedEndpoints.length > 0 ? (
                  <Button onClick={() => setObStep("ob-im")}>{t("config.next")}</Button>
                ) : (
                  <Button variant="secondary" onClick={() => setObStep("ob-im")}>{t("onboarding.llm.skip")}</Button>
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
              <div className="obFormArea">{renderIM(true)}</div>
              <p className="obSkipHint">{t("onboarding.skipHint")}</p>
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <Button variant="outline" onClick={() => setObStep("ob-llm")}>{t("config.prev")}</Button>
                <Button onClick={() => setObStep("ob-cli")}>{t("config.next")}</Button>
              </div>
            </div>
          </div>
        );

      case "ob-cli":
        return (
          <div className="obPage">
            <div className="obContent">
              <h2 className="obStepTitle">{t("onboarding.system.title")}</h2>
              <p className="obStepDesc">
                {t("onboarding.system.desc")}
              </p>

              <div className="flex flex-col gap-2">
                <label className="obModuleItem" data-checked={obCliOpenakita || undefined}>
                  <Checkbox checked={obCliOpenakita} onCheckedChange={() => setObCliOpenakita(!obCliOpenakita)} />
                  <div className="obModuleInfo">
                    <strong style={{ fontFamily: "monospace", fontSize: 15 }}>openakita</strong>
                    <span className="obModuleDesc">{t("onboarding.system.cmdFull")}</span>
                  </div>
                </label>

                <label className="obModuleItem" data-checked={obCliOa || undefined}>
                  <Checkbox checked={obCliOa} onCheckedChange={() => setObCliOa(!obCliOa)} />
                  <div className="obModuleInfo">
                    <strong style={{ fontFamily: "monospace", fontSize: 15 }}>oa</strong>
                    <span className="obModuleDesc">{t("onboarding.system.cmdShort")}</span>
                  </div>
                  <Badge variant="secondary" className="obModuleBadge obModuleBadgeRec">{t("onboarding.system.recommended")}</Badge>
                </label>

                <label className="obModuleItem" data-checked={obCliAddToPath || undefined}>
                  <Checkbox checked={obCliAddToPath} onCheckedChange={() => setObCliAddToPath(!obCliAddToPath)} />
                  <div className="obModuleInfo">
                    <strong>{t("onboarding.system.addToPath")}</strong>
                    <span className="obModuleDesc">{t("onboarding.system.addToPathDesc")}</span>
                  </div>
                </label>

                <div style={{ borderTop: "1px solid var(--line)", margin: "8px 0" }} />

                <label className="obModuleItem" data-checked={obAutostart || undefined}>
                  <Checkbox checked={obAutostart} onCheckedChange={() => setObAutostart(!obAutostart)} />
                  <div className="obModuleInfo">
                    <strong>{t("onboarding.autostart.label")}</strong>
                    <span className="obModuleDesc">{t("onboarding.autostart.desc")}</span>
                  </div>
                  <Badge variant="secondary" className="obModuleBadge obModuleBadgeRec">{t("onboarding.autostart.recommended")}</Badge>
                </label>
              </div>

              {(obCliOpenakita || obCliOa) && (
                <Card className="mt-4">
                  <CardContent className="py-4 px-5 space-y-2.5">
                    <p className="text-[13px] font-semibold text-muted-foreground">{t("onboarding.system.cmdExamples")}</p>
                    <div className="bg-slate-900 rounded-lg px-4 py-3.5 font-mono text-[13px] leading-[1.9] text-slate-200 overflow-x-auto">
                    {obCliOa && <>
                        <div><span className="text-slate-400">$</span> <span className="text-blue-300">oa</span> serve <span className="text-slate-400 ml-6">{t("onboarding.system.commentServe")}</span></div>
                        <div><span className="text-slate-400">$</span> <span className="text-blue-300">oa</span> status <span className="text-slate-400 ml-4">{t("onboarding.system.commentStatus")}</span></div>
                        <div><span className="text-slate-400">$</span> <span className="text-blue-300">oa</span> run <span className="text-slate-400 ml-9">{t("onboarding.system.commentRun")}</span></div>
                      </>}
                      {obCliOa && obCliOpenakita && <div className="h-1" />}
                    {obCliOpenakita && <>
                        <div><span className="text-slate-400">$</span> <span className="text-indigo-300">openakita</span> init <span className="text-slate-400 ml-2">{t("onboarding.system.commentInit")}</span></div>
                        <div><span className="text-slate-400">$</span> <span className="text-indigo-300">openakita</span> serve <span className="text-slate-400">{t("onboarding.system.commentServe")}</span></div>
                    </>}
                  </div>
                  </CardContent>
                </Card>
              )}
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <Button variant="outline" onClick={() => setObStep("ob-im")}>{t("config.prev")}</Button>
                <Button onClick={() => { setObStep("ob-progress"); obRunSetup(); }}>
                  {t("onboarding.system.startInstall")}
                </Button>
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
              <p style={{ fontSize: 12, color: "var(--muted)", margin: "0 0 12px", lineHeight: 1.5 }}>
                {t("onboarding.progress.patience")}
              </p>

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
                      <span style={{ fontSize: 12, color: "#3b82f6", flexShrink: 0, fontWeight: 500 }}>{t("onboarding.progress.inProgress")}</span>
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
                  <div style={{ color: "#64748b" }}>{t("onboarding.progress.waitingStart")}</div>
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
            <div className="flex flex-col items-center text-center max-w-[520px] gap-5">
              <div className="flex items-center justify-center size-16 rounded-full bg-emerald-500 text-white text-[32px] shadow-lg shadow-emerald-500/30">✓</div>
              <h1 className="text-[28px] font-bold tracking-tight text-foreground">{t("onboarding.done.title")}</h1>
              <p className="text-sm text-muted-foreground leading-relaxed">{t("onboarding.done.desc")}</p>
              {obHasErrors && (
                <Card className="w-full border border-amber-300 bg-amber-50/60 dark:border-amber-500/40 dark:bg-amber-950/30 text-left text-[13px]">
                  <CardContent className="py-3 px-4 space-y-1">
                    <div className="flex items-center gap-2 font-semibold">
                      <AlertTriangle className="size-4 text-amber-500 shrink-0" />
                      {t("onboarding.done.someErrors")}
                </div>
                    <p className="text-muted-foreground">{t("onboarding.done.errorsHint")}</p>
                  </CardContent>
                </Card>
              )}
              <Button
                size="lg"
                className="mt-2 px-10 rounded-xl text-[15px]"
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
              </Button>
            </div>
            {stepIndicator}
          </div>
        );

      default:
        return null;
    }
  }

  function renderStepContent() {
    if (!info) return <div className="card">{t("common.loading")}</div>;
    if (view === "status") return renderStatus();
    if (view === "chat") return null;  // ChatView 始终挂载，不在此渲染

    const _disableToggle = (viewKey: string, label: string) => (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", marginBottom: 12 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--muted)", cursor: "pointer" }}>
          <span>{disabledViews.includes(viewKey) ? t("common.disabled", { label }) : t("common.enabled", { label })}</span>
          <div
            onClick={() => toggleViewDisabled(viewKey)}
            style={{
              width: 40, height: 22, borderRadius: 11, cursor: "pointer",
              background: disabledViews.includes(viewKey) ? "var(--line)" : "var(--ok)",
              position: "relative", transition: "background 0.2s",
            }}
          >
            <div style={{
              width: 18, height: 18, borderRadius: 9, background: "#fff",
              position: "absolute", top: 2,
              left: disabledViews.includes(viewKey) ? 2 : 20,
              transition: "left 0.2s", boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
            }} />
          </div>
        </label>
      </div>
    );

    if (view === "skills") {
      return disabledViews.includes("skills") ? (
            <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
          <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，请在「工具与技能」配置中启用</p>
            </div>
          ) : (
            <SkillManager
              venvDir={venvDir}
              currentWorkspaceId={currentWorkspaceId}
              envDraft={envDraft}
              onEnvChange={setEnvDraft}
              onSaveEnvKeys={async (keys) => {
                await saveEnvKeys(keys);
              }}
              apiBaseUrl={apiBaseUrl}
              serviceRunning={!!serviceStatus?.running}
              dataMode={dataMode}
            />
      );
    }
    if (view === "im") {
      return disabledViews.includes("im") ? (
            <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
          <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，请在「配置 → IM 通道」中启用</p>
            </div>
          ) : (
        <IMView serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl} />
      );
    }
    if (view === "token_stats") {
      return (
        <TokenStatsView
          serviceRunning={serviceStatus?.running ?? false}
          apiBaseUrl={apiBaseUrl}
          disabled={disabledViews.includes("token_stats")}
          onToggleDisabled={() => toggleViewDisabled("token_stats")}
        />
      );
    }
    if (view === "mcp") {
      return disabledViews.includes("mcp") ? (
            <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
          <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，请在「工具与技能」配置中启用</p>
            </div>
          ) : (
            <MCPView
              serviceRunning={serviceStatus?.running ?? false}
              apiBaseUrl={apiBaseUrl}
              envDraft={envDraft}
              onEnvChange={setEnvDraft}
              onSaveEnvKeys={async (keys) => { await saveEnvKeys(keys); }}
            />
      );
    }
    if (view === "plugins") {
      return <PluginManagerView visible={true} httpApiBase={httpApiBase} />;
    }
    if (view === "scheduler") {
      return disabledViews.includes("scheduler") ? (
            <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
          <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，请在「灵魂与意志」配置中启用</p>
            </div>
          ) : (
            <SchedulerView serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl} />
      );
    }
    if (view === "memory") {
      return disabledViews.includes("memory") ? (
            <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
          <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，请在「灵魂与意志」配置中启用</p>
            </div>
          ) : (
            <MemoryView serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl} />
      );
    }
    if (view === "identity") {
      return (
        <IdentityView serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl} />
      );
    }
    if (view === "dashboard") {
      return (
        <AgentDashboardView
          apiBaseUrl={apiBaseUrl}
          visible={view === "dashboard"}
          multiAgentEnabled={multiAgentEnabled}
        />
      );
    }
    if (view === "org_editor") {
      return (
        <OrgEditorView
          apiBaseUrl={apiBaseUrl}
          visible={view === "org_editor"}
        />
      );
    }
    if (view === "pixel_office") {
      return (
        <PixelOfficeView
          apiBaseUrl={apiBaseUrl}
          visible={view === "pixel_office"}
        />
      );
    }
    if (view === "agent_manager") {
      return (
        <AgentManagerView
          apiBaseUrl={apiBaseUrl}
          visible={view === "agent_manager"}
          multiAgentEnabled={multiAgentEnabled}
        />
      );
    }
    if (view === "agent_store") {
      return (
        <AgentStoreView
          apiBaseUrl={apiBaseUrl}
          visible={view === "agent_store"}
        />
      );
    }
    if (view === "skill_store") {
      return (
        <SkillStoreView
          apiBaseUrl={apiBaseUrl}
          visible={view === "skill_store"}
        />
      );
    }
    if (view === "security") {
      return (
        <SecurityView
          apiBaseUrl={apiBaseUrl}
          serviceRunning={serviceStatus?.running ?? false}
        />
      );
    }
    if (view === "docs") {
      const docsBase = httpApiBase();
      return (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
          <iframe
            src={`${docsBase}/user-docs/`}
            style={{ flex: 1, border: "none", width: "100%", height: "100%", borderRadius: 8, background: "var(--bg, #fff)" }}
            title={t("sidebar.docs")}
          />
        </div>
      );
    }
    switch (stepId) {
      case "llm":
        return renderLLM();
      case "im":
        return renderIM();
      case "tools":
        return renderTools();
      case "agent":
        return renderAgentSystem();
      case "advanced":
        return renderAdvanced();
      default:
        return renderLLM();
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
      <EnvFieldContext.Provider value={envFieldCtx}>
      <div className="onboardingShell">
        {renderOnboarding()}

        <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
        <Toaster position="top-right" richColors closeButton />
      </div>
      </EnvFieldContext.Provider>
    );
  }

  // ── Capacitor: server config gate ──
  if (IS_CAPACITOR && (needServerConfig || showServerManager)) {
    return <ServerManagerView
      activeServerId={getActiveServerId()}
      manageModeInit={showServerManager && !needServerConfig}
      onConnect={(url) => {
        clearAccessToken();
        setApiBaseUrl(url);
        setNeedServerConfig(false);
        setShowServerManager(false);
        setWebAuthed(false);
        setAuthChecking(true);
        checkAuth(url).then((ok) => {
          if (ok) {
            installFetchInterceptor();
            if (!isPasswordUserSet() && !localStorage.getItem("openakita_pw_banner_dismissed")) setShowPwBanner(true);
          }
          setWebAuthed(ok);
          setAuthChecking(false);
          webInitDone.current = false;
        });
      }}
      onDone={needServerConfig ? undefined : () => setShowServerManager(false)}
    />;
  }

  // ── Web / Capacitor auth gate: show login page if not authenticated ──
  if (needsRemoteAuth && !webAuthed) {
    if (authChecking) {
      return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", color: "var(--text3, #94a3b8)" }}>Loading...</div>;
    }
    return <LoginView
      apiBaseUrl={IS_CAPACITOR ? apiBaseUrl : ""}
      onLoginSuccess={() => {
        installFetchInterceptor();
        webInitDone.current = false;
        setWebAuthed(true);
      }}
      onSwitchServer={IS_CAPACITOR ? () => setShowServerManager(true) : undefined}
      onPreview={() => {
        setPreviewMode(true);
        setWebAuthed(true);
      }}
    />;
  }

  // ── Tauri remote auth gate: remote backend requires login ──
  if (IS_TAURI && tauriRemoteLoginUrl) {
    return <LoginView
      apiBaseUrl={tauriRemoteLoginUrl}
      onLoginSuccess={() => {
        installFetchInterceptor();
        setTauriRemoteLoginUrl(null);
        setDataMode("remote");
        setServiceStatus({ running: true, pid: null, pidFile: "" });
        notifySuccess(t("connect.success"));
        void refreshStatus("remote", tauriRemoteLoginUrl, true).then(() => {
          autoCheckEndpoints(tauriRemoteLoginUrl);
        });
      }}
      onSwitchServer={() => {
        setTauriRemoteMode(false);
        setTauriRemoteLoginUrl(null);
      }}
    />;
  }

  return (
    <EnvFieldContext.Provider value={envFieldCtx}>
    <div className={`appShell ${sidebarCollapsed ? "appShellCollapsed" : ""}${isMobile ? " appShellMobile" : ""}`} style={previewMode ? { paddingTop: IS_CAPACITOR ? "calc(32px + env(safe-area-inset-top))" : 32 } : undefined}>
      {previewMode && (
        <div style={{
          position: "fixed", top: 0, left: 0, right: 0, zIndex: 9999,
          background: "linear-gradient(135deg, #2563eb, #6366f1)",
          color: "#fff", textAlign: "center",
          padding: "6px 16px",
          paddingTop: IS_CAPACITOR ? "max(6px, env(safe-area-inset-top))" : "6px",
          fontSize: 13, fontWeight: 600,
          display: "flex", alignItems: "center", justifyContent: "center", gap: 12,
        }}>
          <span>{t("preview.banner", { defaultValue: "预览模式 — 连接服务器后可使用完整功能" })}</span>
          <button
            onClick={() => { setPreviewMode(false); setWebAuthed(false); }}
            style={{
              background: "rgba(255,255,255,0.2)", border: "1px solid rgba(255,255,255,0.4)",
              color: "#fff", borderRadius: 6, padding: "2px 10px", fontSize: 12,
              fontWeight: 600, cursor: "pointer",
            }}
          >
            {t("preview.connect", { defaultValue: "去连接" })}
          </button>
        </div>
      )}
      {isMobile && mobileSidebarOpen && (
        <div className="sidebarOverlay" onClick={() => setMobileSidebarOpen(false)} />
      )}
      <Sidebar
        collapsed={isMobile ? false : sidebarCollapsed}
        onToggleCollapsed={() => { if (!isMobile) setSidebarCollapsed((v) => !v); }}
        view={view}
        onViewChange={(v) => {
          setView(v);
          setMobileSidebarOpen(false);
          const newHash = _viewToHash(v);
          if (newHash) {
            window.location.hash = newHash;
          } else if (window.location.hash) {
            history.replaceState(null, "", window.location.pathname + window.location.search);
          }
        }}
        mobileOpen={mobileSidebarOpen}
        configExpanded={configExpanded}
        onToggleConfig={() => {
          if (sidebarCollapsed) { setSidebarCollapsed(false); setConfigExpanded(true); }
          else { setConfigExpanded((v) => !v); }
        }}
        steps={steps}
        stepId={stepId}
        onStepChange={(s: StepId) => {
          setStepId(s);
          if (view === "wizard") window.location.hash = _viewToHash("wizard", s);
        }}
        disabledViews={disabledViews}
        multiAgentEnabled={multiAgentEnabled}
        onToggleMultiAgent={toggleMultiAgent}
        storeVisible={storeVisible}
        desktopVersion={desktopVersion}
        backendVersion={backendVersion}
        serviceRunning={serviceStatus?.running ?? false}
        onBugReport={() => setBugReportOpen(true)}
        onRefreshStatus={async () => { await refreshStatus(undefined, undefined, true); }}
        isWeb={IS_WEB}
      />

      <main className="main">
        <Topbar
          wsDropdownOpen={wsDropdownOpen}
          setWsDropdownOpen={setWsDropdownOpen}
          currentWorkspaceId={currentWorkspaceId}
          workspaces={workspaces}
          onSwitchWorkspace={doSetCurrentWorkspace}
          wsQuickCreateOpen={wsQuickCreateOpen}
          setWsQuickCreateOpen={setWsQuickCreateOpen}
          wsQuickName={wsQuickName}
          setWsQuickName={setWsQuickName}
          onCreateWorkspace={async (id, name) => {
            try {
              if (IS_WEB) {
                notifyError("工作区管理暂不支持 Web 模式，请在桌面端操作");
                return;
              }
              await invoke("create_workspace", { id, name, setCurrent: true });
              await refreshAll();
              setCurrentWorkspaceId(id);
              resetEnvLoaded();
              notifySuccess(`${name} (${id})`);
            } catch (err: any) { notifyError(String(err)); }
          }}
          serviceRunning={serviceStatus?.running ?? false}
          endpointCount={endpointSummary.length}
          dataMode={dataMode}
          busy={busy}
          onDisconnect={() => {
            setTauriRemoteMode(false);
            setDataMode("local");
            setServiceStatus({ running: false, pid: null, pidFile: "" });
            resetEnvLoaded();
            notifySuccess(t("topbar.disconnected"));
          }}
          onConnect={() => {
            setConnectAddress(apiBaseUrl.replace(/^https?:\/\//, ""));
            setConnectDialogOpen(true);
          }}
          onStart={async () => {
            const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
            if (!effectiveWsId) { notifyError(t("common.error")); return; }
            await startLocalServiceWithConflictCheck(effectiveWsId);
          }}
          onRefreshAll={async () => { await refreshAll(); try { await refreshStatus(undefined, undefined, true); } catch {} }}
          onSetTheme={(theme) => setThemePref(theme)}
          themePrefState={themePrefState}
          isWeb={IS_WEB || IS_CAPACITOR}
          onLogout={(IS_WEB || IS_CAPACITOR) ? async () => {
            const { logout } = await import("./platform/auth");
            await logout(IS_CAPACITOR ? apiBaseUrl : "");
            setWebAuthed(false);
          } : undefined}
          webAccessUrl={IS_TAURI && (serviceStatus?.running ?? false) ? `${apiBaseUrl || "http://127.0.0.1:18900"}/web` : undefined}
          apiBaseUrl={apiBaseUrl || "http://127.0.0.1:18900"}
          onToggleMobileSidebar={isMobile ? () => setMobileSidebarOpen((v) => !v) : undefined}
          serverName={IS_CAPACITOR ? (getActiveServer()?.name || undefined) : undefined}
          onServerManager={IS_CAPACITOR ? () => setShowServerManager(true) : undefined}
        />

        {showPwBanner && (
          <div style={{
            display: "flex", alignItems: "center", gap: isMobile ? 6 : 10,
            padding: isMobile ? "6px 10px" : "8px 16px",
            background: "var(--warning-bg, #fef3c7)", borderBottom: "1px solid var(--warning-border, #f59e0b)",
            color: "var(--warning-text, #92400e)", fontSize: isMobile ? 12 : 13,
          }}>
            <span style={{ flex: 1 }}>
              {isMobile
                ? t("web.passwordBannerShort", { defaultValue: "访问密码为自动生成，建议设置自定义密码。" })
                : t("web.passwordBanner", { defaultValue: "当前 Web 访问密码为系统自动生成，建议前往设置页面配置自定义密码以保障远程访问安全。" })}
            </span>
            <button className="btnSmall" style={{ whiteSpace: "nowrap", fontWeight: 500, fontSize: isMobile ? 11 : undefined, padding: isMobile ? "2px 8px" : undefined }} onClick={() => {
              setView("wizard");
              setStepId("advanced");
              setShowPwBanner(false);
              localStorage.setItem("openakita_pw_banner_dismissed", "1");
            }}>{t("web.passwordBannerAction", { defaultValue: "去设置" })}</button>
            <button style={{
              background: "none", border: "none", cursor: "pointer", padding: 2,
              color: "var(--warning-text, #92400e)", fontSize: 16, lineHeight: 1, opacity: 0.6,
            }} onClick={() => {
              setShowPwBanner(false);
              localStorage.setItem("openakita_pw_banner_dismissed", "1");
            }} title={t("common.close", { defaultValue: "关闭" })}>×</button>
          </div>
        )}

        <div style={{ gridRow: 3, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
          {/* ChatView 始终挂载，切走时隐藏以保留聊天记录 */}
          <div className="contentChat" style={{ display: view === "chat" ? undefined : "none", flex: 1, minHeight: 0 }}>
            <ChatView
              serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl}
              endpoints={chatEndpoints}
              visible={view === "chat"}
              multiAgentEnabled={multiAgentEnabled}
              onStartService={async () => {
                const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
                if (!effectiveWsId) {
                  notifyError("未找到工作区（请先创建/选择一个工作区）");
                  return;
                }
                await startLocalServiceWithConflictCheck(effectiveWsId);
              }}
            />
          </div>
          <div className="content" style={{ display: view !== "chat" ? undefined : "none", flex: 1, minHeight: 0 }}>
            <Suspense fallback={<div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", opacity: 0.5 }}><div className="spinner" style={{ width: 24, height: 24 }} /></div>}>
            {renderStepContent()}
            </Suspense>
          </div>
        </div>

        {/* ── Connect Dialog ── */}
        {connectDialogOpen && (
          <ModalOverlay onClose={() => setConnectDialogOpen(false)}>
            <div className="modalContent" style={{ maxWidth: 420 }}>
              <div className="dialogHeader">
                <span className="cardTitle">{t("connect.title")}</span>
                <button className="dialogCloseBtn" onClick={() => setConnectDialogOpen(false)}>&times;</button>
              </div>
              <div className="dialogSection">
                <p style={{ color: "var(--muted)", fontSize: 13, margin: "0 0 16px" }}>{t("connect.hint")}</p>
                <div className="dialogLabel">{t("connect.address")}</div>
                <input
                  value={connectAddress}
                  onChange={(e) => setConnectAddress(e.target.value)}
                  placeholder="127.0.0.1:18900"
                  autoFocus
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 8, border: "1px solid var(--line)", fontSize: 14, background: "var(--panel2)", color: "var(--text)" }}
                />
              </div>
              <div className="dialogFooter">
                <button className="btnSmall" onClick={() => setConnectDialogOpen(false)}>{t("common.cancel")}</button>
                <button className="btnPrimary" disabled={!!busy} onClick={async () => {
                  const addr = connectAddress.trim();
                  if (!addr) return;
                  const url = addr.startsWith("http") ? addr : `http://${addr}`;
                  const _b = notifyLoading(t("connect.testing"));
                  let connected = false;
                  try {
                    const res = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(5000) });
                    const data = await res.json();
                    if (data.status === "ok") {
                      if (IS_TAURI) setTauriRemoteMode(true);
                      const authOk = IS_TAURI ? await checkAuth(url) : true;
                      if (!authOk) {
                        setApiBaseUrl(url);
                        localStorage.setItem("openakita_apiBaseUrl", url);
                        setConnectDialogOpen(false);
                        setTauriRemoteLoginUrl(url);
                        if (data.version) checkVersionMismatch(data.version);
                        return;
                      }
                      setApiBaseUrl(url);
                      localStorage.setItem("openakita_apiBaseUrl", url);
                      setDataMode("remote");
                      setServiceStatus({ running: true, pid: null, pidFile: "" });
                      setConnectDialogOpen(false);
                      connected = true;
                      notifySuccess(t("connect.success"));
                      if (data.version) checkVersionMismatch(data.version);
                      await refreshStatus("remote", url, true);
                      autoCheckEndpoints(url);
                    } else {
                      notifyError(t("connect.fail"));
                    }
                  } catch {
                    if (IS_TAURI && !connected) setTauriRemoteMode(false);
                    notifyError(t("connect.fail"));
                  } finally { dismissLoading(_b); }
                }}>{t("connect.confirm")}</button>
              </div>
            </div>
          </ModalOverlay>
        )}

        {/* ── Restart overlay ── */}
        {restartOverlay && (
          <div className="modalOverlay" style={{ zIndex: 10000, background: "rgba(0,0,0,0.5)" }}>
            <div className="modalContent" style={{ maxWidth: 360, padding: "32px 28px", textAlign: "center", borderRadius: 16 }}>
              {(restartOverlay.phase === "saving" || restartOverlay.phase === "restarting" || restartOverlay.phase === "waiting") && (
                <>
                  <div style={{ marginBottom: 16, display: "flex", justifyContent: "center", paddingLeft: 0, paddingRight: 0 }}>
                    <svg width="40" height="40" viewBox="0 0 40 40" style={{ animation: "spin 1s linear infinite" }}>
                      <circle cx="20" cy="20" r="16" fill="none" stroke="#2563eb" strokeWidth="3" strokeDasharray="80" strokeDashoffset="20" strokeLinecap="round" />
                    </svg>
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: "#0e7490" }}>
                    {restartOverlay.phase === "saving" && t("common.loading")}
                    {restartOverlay.phase === "restarting" && t("config.restarting")}
                    {restartOverlay.phase === "waiting" && t("config.restartWaiting")}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
                    {t("config.applyRestartHint")}
                  </div>
                </>
              )}
              {restartOverlay.phase === "done" && (
                <>
                  <div style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}><IconCheckCircle size={40} /></div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: "#059669" }}>{t("config.restartSuccess")}</div>
                </>
              )}
              {restartOverlay.phase === "fail" && (
                <>
                  <div style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}><IconXCircle size={40} /></div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: "#dc2626" }}>{t("config.restartFail")}</div>
                </>
              )}
              {restartOverlay.phase === "notRunning" && (
                <>
                  <div style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}><IconInfo size={40} /></div>
                  <div style={{ fontSize: 14, fontWeight: 500, color: "#64748b" }}>{t("config.restartNotRunning")}</div>
                </>
              )}
            </div>
          </div>
        )}
        <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>


        {/* ── Service conflict dialog ── */}
        {conflictDialog && (
          <ModalOverlay onClose={() => { setConflictDialog(null); setPendingStartWsId(null); }}>
            <div className="modalContent" style={{ maxWidth: 440, padding: 24 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <span style={{ fontSize: 20 }}>⚠️</span>
                <span style={{ fontWeight: 600, fontSize: 15 }}>{t("conflict.title")}</span>
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.7, marginBottom: 8 }}>{t("conflict.message")}</div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20 }}>
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
          </ModalOverlay>
        )}

        {/* ── Version mismatch banner ── */}
        {versionMismatch && (
          <div style={{ position: "fixed", top: 48, left: "50%", transform: "translateX(-50%)", zIndex: 9999, background: "var(--panel2)", backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)", border: "1px solid var(--warning)", borderRadius: 10, padding: "12px 20px", maxWidth: 500, boxShadow: "var(--shadow)", display: "flex", flexDirection: "column", gap: 8, color: "var(--warning)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 16 }}>⚠️</span>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{t("version.mismatch")}</span>
              <button style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", fontSize: 16, color: "var(--muted)" }} onClick={() => setVersionMismatch(null)}>&times;</button>
            </div>
            <div style={{ fontSize: 12, lineHeight: 1.6 }}>
              {t("version.mismatchDetail", { backend: versionMismatch.backend, desktop: versionMismatch.desktop })}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button className="btnSmall" style={{ fontSize: 11 }} onClick={async () => { const ok = await copyToClipboard(t("version.pipCommand")); if (ok) notifySuccess(t("version.copied")); }}>{t("version.updatePip")}</button>
              <code style={{ fontSize: 11, background: "var(--nav-hover)", padding: "2px 8px", borderRadius: 4, color: "var(--text)" }}>{t("version.pipCommand")}</code>
            </div>
          </div>
        )}

        {/* ── Update notification with download/install support ── */}
        {newRelease && (
          <div style={{ position: "fixed", bottom: 20, right: 20, zIndex: 9998, background: "var(--panel2)", backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)", border: "1px solid var(--brand)", borderRadius: 10, padding: "12px 20px", maxWidth: 400, boxShadow: "var(--shadow)", display: "flex", flexDirection: "column", gap: 8, color: "var(--brand)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 16 }}>{updateProgress.status === "done" ? "✅" : updateProgress.status === "error" ? "❌" : "🎉"}</span>
              <span style={{ fontWeight: 600, fontSize: 13 }}>
                {updateProgress.status === "done" ? t("version.updateReady") : updateProgress.status === "error" ? t("version.updateFailed") : t("version.newRelease")}
              </span>
              {updateProgress.status === "idle" && (
                <button style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", fontSize: 16, color: "var(--muted)" }} onClick={() => {
                  setNewRelease(null);
                  localStorage.setItem("openakita_release_dismissed", newRelease.latest);
                }}>&times;</button>
              )}
            </div>

            {/* Version info */}
            <div style={{ fontSize: 12, lineHeight: 1.6 }}>
              {t("version.newReleaseDetail", { latest: newRelease.latest, current: newRelease.current })}
            </div>

            {/* Download progress bar */}
            {updateProgress.status === "downloading" && (
              <div style={{ width: "100%", background: "#bbdefb", borderRadius: 4, height: 6, overflow: "hidden" }}>
                <div style={{ width: `${updateProgress.percent || 0}%`, background: "#1976d2", height: "100%", borderRadius: 4, transition: "width 0.3s" }} />
              </div>
            )}
            {updateProgress.status === "downloading" && (
              <div style={{ fontSize: 11, color: "#1565c0" }}>{t("version.downloading")} {updateProgress.percent || 0}%</div>
            )}
            {updateProgress.status === "installing" && (
              <div style={{ fontSize: 11, color: "#1565c0" }}>{t("version.installing")}</div>
            )}
            {updateProgress.status === "error" && (
              <div style={{ fontSize: 11, color: "#c62828" }}>{updateProgress.error}</div>
            )}

            {/* Action buttons */}
            <div style={{ display: "flex", gap: 8 }}>
              {updateProgress.status === "idle" && updateAvailable && (
                <button className="btnSmall btnSmallPrimary" style={{ fontSize: 11 }} onClick={doDownloadAndInstall}>
                  {t("version.updateNow")}
                </button>
              )}
              {updateProgress.status === "idle" && !updateAvailable && (
                <a href={newRelease.url} target="_blank" rel="noreferrer" className="btnSmall btnSmallPrimary" style={{ fontSize: 11, textDecoration: "none" }}>{t("version.viewRelease")}</a>
              )}
              {updateProgress.status === "done" && (
                <button className="btnSmall btnSmallPrimary" style={{ fontSize: 11 }} onClick={doRelaunchAfterUpdate}>
                  {t("version.restartNow")}
                </button>
              )}
              {updateProgress.status === "idle" && (
                <button className="btnSmall" style={{ fontSize: 11 }} onClick={() => {
                  setNewRelease(null);
                  localStorage.setItem("openakita_release_dismissed", newRelease.latest);
                }}>{t("version.dismiss")}</button>
              )}
              {updateProgress.status === "error" && (
                <button className="btnSmall" style={{ fontSize: 11 }} onClick={() => {
                  setUpdateProgress({ status: "idle" });
                }}>{t("version.retry")}</button>
              )}
            </div>
          </div>
        )}

        <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
        <Toaster position="top-right" richColors closeButton />

        {view === "wizard" ? (() => {
          const saveConfig = getFooterSaveConfig();
          return saveConfig ? (
            <div className="footer" style={{ gridRow: 4, justifyContent: "flex-end" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Button variant="secondary"
                  onClick={() => renderIntegrationsSave(saveConfig.keys, saveConfig.savedMsg)}
                  disabled={!currentWorkspaceId || !!busy}>
                  {t("config.saveEnv")}
                </Button>
                <Button
                  onClick={() => applyAndRestart(saveConfig.keys)}
                  disabled={!currentWorkspaceId || !!busy || !!restartOverlay}
                  title={t("config.applyRestartHint")}>
                  {t("config.applyRestart")}
                </Button>
              </div>
            </div>
          ) : null;
        })() : null}
      </main>

      {/* Feedback Modal (Bug Report + Feature Request) */}
      <FeedbackModal
        open={bugReportOpen}
        onClose={() => setBugReportOpen(false)}
        apiBase={httpApiBase()}
      />
    </div>
    </EnvFieldContext.Provider>
  );
}

