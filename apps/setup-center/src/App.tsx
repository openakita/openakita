import { Fragment, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke, listen, IS_TAURI, IS_WEB, IS_CAPACITOR, IS_LOCAL_WEB, getAppVersion, onWsEvent, reconnectWsNow, logger, openExternalUrl } from "./platform";
import { getActiveServer, getActiveServerId } from "./platform/servers";
import { checkAuth, installFetchInterceptor, AUTH_EXPIRED_EVENT, isPasswordUserSet, logout, clearAccessToken, setTauriRemoteMode, isTauriRemoteMode } from "./platform/auth";
import { LoginView } from "./views/LoginView";
import { ServerManagerView } from "./views/ServerManagerView";
import { ChatView } from "./views/ChatView";
import { SkillManager } from "./views/SkillManager";
import { IMView } from "./views/IMView";
import { TokenStatsView } from "./views/TokenStatsView";
import { MCPView } from "./views/MCPView";
import { SchedulerView } from "./views/SchedulerView";
import { MemoryView } from "./views/MemoryView";
import { IdentityView } from "./views/IdentityView";
import { AgentDashboardView } from "./views/AgentDashboardView";
import { AgentManagerView } from "./views/AgentManagerView";
import { OrgEditorView } from "./views/OrgEditorView";
import { FeedbackModal } from "./views/FeedbackModal";
import { IMConfigView } from "./views/IMConfigView";
import type { IMBot } from "./views/im-shared";
import { TYPE_TO_ENABLED_KEY } from "./views/im-shared";
import { AgentSystemView } from "./views/AgentSystemView";
import { AgentStoreView } from "./views/AgentStoreView";
import { SkillStoreView } from "./views/SkillStoreView";
import type {
  EndpointSummary as EndpointSummaryType,
  PlatformInfo, WorkspaceSummary, ProviderInfo, ListedModel,
  EndpointDraft, PythonCandidate, BundledPythonInstallResult, InstallSource,
  EnvMap, StepId, Step,
} from "./types";
import {
  IconRefresh, IconCheck, IconCheckCircle, IconX, IconXCircle,
  IconChevronDown, IconChevronRight, IconChevronUp, IconGlobe,
  IconEdit, IconTrash, IconEye, IconEyeOff, IconInfo, IconClipboard, IconPower, IconCircle,
  DotGreen, DotGray, DotYellow, DotRed,
  LogoTelegram, LogoFeishu, LogoWework, LogoDingtalk, LogoQQ,
} from "./icons";
import { ChevronDownIcon, ChevronRight, XIcon, Loader2, RefreshCw, Play, Square, RotateCcw, Power, PowerOff, FolderOpen, Activity, ArrowRight, Server, Download, Zap, Inbox, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction } from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import logoUrl from "./assets/logo.png";
import "highlight.js/styles/github.css";
import { getThemePref, setThemePref, THEME_CHANGE_EVENT, type Theme } from "./theme";
import { copyToClipboard } from "./utils/clipboard";
import { BUILTIN_PROVIDERS, STT_RECOMMENDED_MODELS, PIP_INDEX_PRESETS } from "./constants";
import {
  isLocalProvider, localProviderPlaceholderKey, friendlyFetchError,
  inferCapabilities, fetchModelsDirectly, safeFetch, proxyFetch,
  isMiniMaxProvider, isVolcCodingPlanProvider, isDashScopeCodingPlanProvider,
  isLongCatProvider, miniMaxFallbackModels, volcCodingPlanFallbackModels,
  dashScopeCodingPlanFallbackModels, longCatFallbackModels,
} from "./providers";
import {
  slugify, joinPath, toFileUrl, envKeyFromSlug, nextEnvKeyName,
  suggestEndpointName, parseEnv, envGet, envSet,
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
import { SearchSelect } from "./components/SearchSelect";
import { ProviderSearchSelect } from "./components/ProviderSearchSelect";
import { TroubleshootPanel } from "./components/TroubleshootPanel";
import { CliManager } from "./components/CliManager";
import { WebPasswordManager } from "./components/WebPasswordManager";
import { FieldText, FieldBool, FieldSelect, FieldCombo, FieldSlider, TelegramPairingCodeHint } from "./components/EnvFields";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { ModalOverlay } from "./components/ModalOverlay";
import { Section } from "./components/Section";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { useNotifications } from "./hooks/useNotifications";
import { notifySuccess, notifyError, notifyLoading, dismissLoading } from "./utils/notify";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import { useVersionCheck, compareSemver } from "./hooks/useVersionCheck";

const THEME_I18N_KEYS: Record<Theme, string> = { system: "topbar.themeSystem", dark: "topbar.themeDark", light: "topbar.themeLight" };

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

type ViewId = "wizard" | "status" | "chat" | "skills" | "im" | "onboarding" | "modules" | "token_stats" | "mcp" | "scheduler" | "memory" | "identity" | "dashboard" | "org_editor" | "agent_manager" | "agent_store" | "skill_store" | "docs";

const _HASH_TO_VIEW: Record<string, ViewId> = {
  "chat": "chat", "im": "im", "skills": "skills", "mcp": "mcp",
  "scheduler": "scheduler", "memory": "memory", "status": "status",
  "token-stats": "token_stats", "identity": "identity",
  "dashboard": "dashboard", "org-editor": "org_editor",
  "agent-manager": "agent_manager", "agent-store": "agent_store",
  "skill-store": "skill_store", "wizard": "wizard", "docs": "docs",
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
  const toggleTheme = useCallback(() => {
    let next: Theme = "system";
    if (themePrefState === "system") next = "dark";
    else if (themePrefState === "dark") next = "light";
    else next = "system";
    setThemePref(next);
    notifySuccess(t(THEME_I18N_KEYS[next]));
  }, [themePrefState, t]);
  const [info, setInfo] = useState<PlatformInfo | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState<string | null>(null);
  const { confirmDialog, setConfirmDialog, askConfirm } = useNotifications();
  const busy: string | null = null;
  const [dangerAck, setDangerAck] = useState(false);

  // ── Restart overlay state ──
  const [restartOverlay, setRestartOverlay] = useState<{ phase: "saving" | "restarting" | "waiting" | "done" | "fail" | "notRunning" } | null>(null);

  // ── Module restart prompt ──
  const [moduleRestartPrompt, setModuleRestartPrompt] = useState<string | null>(null);

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
  type ModuleInfo = { id: string; name: string; description: string; installed: boolean; bundled: boolean; sizeMb: number; category: string };
  const [obStep, setObStep] = useState<OnboardingStep>("ob-welcome");
  const [obModules, setObModules] = useState<ModuleInfo[]>([]);
  /** 卸载因“拒绝访问”失败时，可先停止后端再卸载的待处理模块 */
  const [moduleUninstallPending, setModuleUninstallPending] = useState<{ id: string; name: string } | null>(null);
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
  const [obPendingBots, setObPendingBots] = useState<IMBot[]>([]);

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
  // advanced panel state
  const [advSysInfo, setAdvSysInfo] = useState<Record<string, string> | null>(null);
  const [advLoading, setAdvLoading] = useState<Record<string, boolean>>({});
  const [hubApiUrl, setHubApiUrl] = useState<string>("");
  const advLoadedRef = useRef(false);

  // backup state
  const [backupHistory, setBackupHistory] = useState<Array<{ filename: string; path: string; size_bytes: number; created_at: string; manifest?: any }>>([]);
  const [backupShowHistory, setBackupShowHistory] = useState(false);
  const [factoryResetOpen, setFactoryResetOpen] = useState(false);
  const [factoryResetConfirmText, setFactoryResetConfirmText] = useState("");

  // workspace migration state
  const [migrateTargetPath, setMigrateTargetPath] = useState("");
  const [migratePreflight, setMigratePreflight] = useState<{
    sourcePath: string; sourceSizeMb: number; targetPath: string; targetFreeMb: number;
    entries: Array<{ name: string; sizeMb: number; existsAtTarget: boolean; isDir: boolean }>;
    canMigrate: boolean; reason: string;
  } | null>(null);
  const [migrateBusy, setMigrateBusy] = useState(false);
  const [migrateCurrentRoot, setMigrateCurrentRoot] = useState("");
  const [migrateCustomRoot, setMigrateCustomRoot] = useState<string | null>(null);

  // providers & models
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providerSlug, setProviderSlug] = useState<string>("");
  const selectedProvider = useMemo(
    () => providers.find((p) => p.slug === providerSlug) || null,
    [providers, providerSlug],
  );
  const [apiType, setApiType] = useState<"openai" | "openai_responses" | "anthropic">("openai");
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
  const [savedSttEndpoints, setSavedSttEndpoints] = useState<EndpointDraft[]>([]);
  const [apiKeyEnvTouched, setApiKeyEnvTouched] = useState(false);
  const [endpointNameTouched, setEndpointNameTouched] = useState(false);
  const [baseUrlTouched, setBaseUrlTouched] = useState(false);
  const [llmAdvancedOpen, setLlmAdvancedOpen] = useState(false);
  const [baseUrlExpanded, setBaseUrlExpanded] = useState(false);
  const [editBaseUrlExpanded, setEditBaseUrlExpanded] = useState(false);
  const [compBaseUrlExpanded, setCompBaseUrlExpanded] = useState(false);
  const [sttBaseUrlExpanded, setSttBaseUrlExpanded] = useState(false);
  const [addEpMaxTokens, setAddEpMaxTokens] = useState(0);
  const [addEpContextWindow, setAddEpContextWindow] = useState(200000);
  const [addEpTimeout, setAddEpTimeout] = useState(180);
  const [addEpRpmLimit, setAddEpRpmLimit] = useState(0);
  const [codingPlanMode, setCodingPlanMode] = useState(false);

  // Compiler endpoint form state
  const [compilerProviderSlug, setCompilerProviderSlug] = useState("");
  const [compilerApiType, setCompilerApiType] = useState<"openai" | "anthropic">("openai");
  const [compilerBaseUrl, setCompilerBaseUrl] = useState("");
  const [compilerApiKeyEnv, setCompilerApiKeyEnv] = useState("");
  const [compilerApiKeyValue, setCompilerApiKeyValue] = useState("");
  const [compilerModel, setCompilerModel] = useState("");
  const [compilerEndpointName, setCompilerEndpointName] = useState("");
  const [compilerCodingPlan, setCompilerCodingPlan] = useState(false);
  const [compilerModels, setCompilerModels] = useState<ListedModel[]>([]); // models fetched for compiler section

  // STT endpoint form state（与 LLM/Compiler 完全独立，避免互相影响）
  const [sttProviderSlug, setSttProviderSlug] = useState("");
  const [sttApiType, setSttApiType] = useState<"openai" | "anthropic">("openai");
  const [sttBaseUrl, setSttBaseUrl] = useState("");
  const [sttApiKeyEnv, setSttApiKeyEnv] = useState("");
  const [sttApiKeyValue, setSttApiKeyValue] = useState("");
  const [sttModel, setSttModel] = useState("");
  const [sttEndpointName, setSttEndpointName] = useState("");
  const [sttModels, setSttModels] = useState<ListedModel[]>([]);

  // Edit endpoint modal (do not reuse the "add" form)
  const [editingOriginalName, setEditingOriginalName] = useState<string | null>(null);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const isEditingEndpoint = editModalOpen && editingOriginalName !== null;
  const [llmNextModalOpen, setLlmNextModalOpen] = useState(false);
  const [editDraft, setEditDraft] = useState<{
    name: string;
    priority: number;
    providerSlug: string;
    apiType: "openai" | "openai_responses" | "anthropic";
    baseUrl: string;
    apiKeyEnv: string;
    apiKeyValue: string; // optional; blank means don't change
    modelId: string;
    caps: string[];
    maxTokens: number;
    contextWindow: number;
    timeout: number;
    rpmLimit: number;
    pricingTiers: { max_input: number; input_price: number; output_price: number }[];
  } | null>(null);
  const dragNameRef = useRef<string | null>(null);
  const [editModels, setEditModels] = useState<ListedModel[]>([]); // models fetched inside the edit modal

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
  const [secretShown, setSecretShown] = useState<Record<string, boolean>>({});
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
  const [logLevelFilter, setLogLevelFilter] = useState<Set<string>>(new Set(["INFO", "WARN", "ERROR", "DEBUG"]));
  const logAtBottomRef = useRef(true);
  const [logAtBottom, setLogAtBottom] = useState(true);
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
  useEffect(() => {
    const handler = () => {
      const visible = !document.hidden;
      setPageVisible(visible);
      if (visible) {
        // 从 hidden 恢复：给 10 秒宽限期，前 2 次心跳失败不计
        visibilityGraceRef.current = true;
        heartbeatFailCount.current = 0;
        setTimeout(() => { visibilityGraceRef.current = false; }, 10000);
        // 立即重连 WebSocket（后台期间连接可能已断开）
        reconnectWsNow();
        // 通知 ChatView 等组件检查进行中的 SSE 流
        window.dispatchEvent(new Event("openakita_app_resumed"));
        logger.info("App", "Resumed from background");
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
          const prefix = status === "retrying" ? "🔄" : status === "error" ? "❌" : status === "done" ? "✅" : status === "warning" ? "⚠️" : status === "restart-hint" ? "🔁" : "📦";
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
      } else if (event === "module-install-progress") {
        const msg = String(p.message || "");
        const status = String(p.status || "");
        const moduleId = String(p.moduleId || "");
        if (msg) {
          const prefix = status === "retrying" ? "🔄" : status === "error" ? "❌" : status === "done" ? "✅" : status === "warning" ? "⚠️" : status === "restart-hint" ? "🔁" : "📦";
          setObDetailLog(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${prefix} [${moduleId}] ${msg}`]);
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

  async function ensureEnvLoaded(workspaceId: string): Promise<EnvMap> {
    if (envLoadedForWs.current === workspaceId) return envDraft;
    let parsed: EnvMap = {};

    if (shouldUseHttpApi()) {
      try {
        const res = await safeFetch(`${httpApiBase()}/api/config/env`);
        const data = await res.json();
        parsed = data.env || {};
      } catch {
        if (IS_TAURI && workspaceId) {
          try {
            const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
            parsed = parseEnv(content);
          } catch { parsed = {}; }
        }
      }
    } else if (IS_TAURI && workspaceId) {
      try {
        const content = await invoke<string>("workspace_read_file", { workspaceId, relativePath: ".env" });
        parsed = parseEnv(content);
      } catch { parsed = {}; }
    }
    // Set sensible defaults for first-time setup
    const defaults: Record<string, string> = {
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
      envLoadedForWs.current = null;
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
      envLoadedForWs.current = null;
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
      const defaultSlug = parsed.find(p => p.slug === "openai")?.slug ?? parsed[0]?.slug ?? "";
      setProviderSlug((prev) => prev || defaultSlug);

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
        const defaultSlug2 = sorted.find(p => p.slug === "openai")?.slug ?? sorted[0]?.slug ?? "";
        setProviderSlug((prev) => prev || defaultSlug2);
      }
    } finally {
      dismissLoading(_busyId);
    }
  }

  useEffect(() => {
    if (!selectedProvider) return;
    // Coding Plan：根据 provider 的 coding_plan_api_type 切换协议与 URL
    if (codingPlanMode && selectedProvider.coding_plan_base_url) {
      setApiType((selectedProvider.coding_plan_api_type as "openai" | "anthropic") || "anthropic");
      if (!baseUrlTouched) setBaseUrl(selectedProvider.coding_plan_base_url);
      setAddEpContextWindow(200000);
      setAddEpMaxTokens((selectedProvider as ProviderInfo).default_max_tokens ?? 8192);
    } else {
      const t = (selectedProvider.api_type as "openai" | "anthropic") || "openai";
      setApiType(t);
      if (!baseUrlTouched) setBaseUrl(selectedProvider.default_base_url || "");
      setAddEpContextWindow((selectedProvider as ProviderInfo).default_context_window ?? 200000);
      setAddEpMaxTokens((selectedProvider as ProviderInfo).default_max_tokens ?? 0);
    }
    const suggested = selectedProvider.api_key_env_suggestion || envKeyFromSlug(selectedProvider.slug);
    const used = new Set(Object.keys(envDraft || {}));
    for (const ep of savedEndpoints) {
      if (ep.api_key_env) used.add(ep.api_key_env);
    }
    if (!apiKeyEnvTouched) {
      setApiKeyEnv(nextEnvKeyName(suggested, used));
    }
    const autoName = suggestEndpointName(selectedProvider.slug, selectedModelId);
    if (!endpointNameTouched) {
      setEndpointName(autoName);
    }
    if (isLocalProvider(selectedProvider) && !apiKeyValue.trim()) {
      setApiKeyValue(localProviderPlaceholderKey(selectedProvider));
    }
  }, [selectedProvider, selectedModelId, envDraft, savedEndpoints, apiKeyEnvTouched, endpointNameTouched, baseUrlTouched, codingPlanMode]);

  // When user switches provider via dropdown, reset auto-naming to follow the new provider.
  useEffect(() => {
    if (!providerSlug) return;
    if (editModalOpen) return;
    setApiKeyEnvTouched(false);
    setEndpointNameTouched(false);
    setBaseUrlTouched(false);
    setCodingPlanMode(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerSlug]);

  // MiniMax / 火山 Coding Plan / DashScope Coding Plan / LongCat 不可靠提供 /models：进入时直接提供内置候选，并允许继续手填。
  useEffect(() => {
    if (!selectedProvider) return;
    const effectiveBaseUrl = (codingPlanMode ? selectedProvider.coding_plan_base_url : selectedProvider.default_base_url) || "";
    if (isVolcCodingPlanProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(volcCodingPlanFallbackModels(selectedProvider.slug));
      return;
    }
    if (isDashScopeCodingPlanProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(dashScopeCodingPlanFallbackModels(selectedProvider.slug));
      return;
    }
    if (isLongCatProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(longCatFallbackModels(selectedProvider.slug));
      return;
    }
    if (isMiniMaxProvider(selectedProvider.slug, effectiveBaseUrl)) {
      setModels(miniMaxFallbackModels(selectedProvider.slug));
      return;
    }
  }, [selectedProvider, codingPlanMode]);

  async function doFetchModels() {
    setModels([]);
    setSelectedModelId(""); // clear search / selection
    const _busyId = notifyLoading("拉取模型列表...");
    try {
      // 本地服务商自动使用 placeholder key
      const effectiveKey = apiKeyValue.trim() || (isLocalProvider(selectedProvider) ? localProviderPlaceholderKey(selectedProvider) : "");
      logger.debug("App", "doFetchModels", { apiType, baseUrl, slug: selectedProvider?.slug, keyLen: effectiveKey?.length, httpApi: shouldUseHttpApi(), isLocal: isLocalProvider(selectedProvider) });
      const parsed = await fetchModelListUnified({
        apiType,
        baseUrl,
        providerSlug: selectedProvider?.slug ?? null,
        apiKey: effectiveKey,
      });
      setModels(parsed);
      setSelectedModelId("");
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
      setCapTouched(false);
    } catch (e: any) {
      logger.error("App", "doFetchModels error", { error: String(e) });
      const raw = String(e?.message || e);
      notifyError(friendlyFetchError(raw, t, selectedProvider?.name));
    } finally {
      dismissLoading(_busyId);
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
      let httpApiFailed = false;
      if (shouldUseHttpApi()) {
        // ── 后端运行中 → 走后端 API（验证后端兼容性 + 热加载）──
        try {
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
        } catch (httpErr) {
          const msg = String(httpErr);
          if (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("AbortError")) {
            logger.warn("App", "doTestConnection: HTTP API unreachable, falling back to direct", { error: String(httpErr) });
            httpApiFailed = true;
          } else {
            throw httpErr;
          }
        }
      }
      if (!shouldUseHttpApi() || httpApiFailed) {
        // ── 后端未运行 / 不可达 → 前端直连服务商 API ──
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
      const raw = String(e);
      // 使用通用友好化函数，testProviderSlug 可用于定位本地服务名称
      const provName = providers.find((p) => p.slug === params.testProviderSlug)?.name;
      const errMsg = friendlyFetchError(raw, t, provName);
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
    logger.debug("App", "fetchModelListUnified", { shouldUseHttpApi: shouldUseHttpApi(), httpApiBase: httpApiBase() });
    if (shouldUseHttpApi()) {
      logger.debug("App", "fetchModelListUnified: using HTTP API");
      try {
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
      } catch (httpErr) {
        // 后端 API 不可达（端口冲突、未完全启动等），回退到 Tauri/直连
        const msg = String(httpErr);
        if (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("AbortError")) {
          logger.warn("App", "fetchModelListUnified: HTTP API unreachable, falling back", { error: String(httpErr) });
        } else {
          // 非网络错误（如后端返回业务错误），直接抛出
          throw httpErr;
        }
      }
    }
    // ── 后端未运行 / 后端不可达 → 本地回退 ──
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
      logger.warn("App", "openakita_list_models via Python bridge failed, using direct fetch", { error: String(e) });
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

  async function loadSavedEndpoints(skipAutoFix = false) {
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

      // Auto-fix: detect endpoints sharing the same api_key_env.
      // When two endpoints share one env var, editing either one overwrites the
      // other's API key.  Rename duplicates to unique names and persist.
      // IMPORTANT: operate on the RAW parsed JSON arrays to preserve all fields
      // (extra_params, pricing_tiers, etc.) that EndpointDraft doesn't carry.
      // skipAutoFix prevents infinite recursion when the recursive reload follows a fix.
      if (skipAutoFix) return;
      const rawLists = [
        Array.isArray(parsed?.endpoints) ? parsed.endpoints : [],
        Array.isArray(parsed?.compiler_endpoints) ? parsed.compiler_endpoints : [],
        Array.isArray(parsed?.stt_endpoints) ? parsed.stt_endpoints : [],
      ];
      const usedKeys = new Set<string>();
      let envFixNeeded = false;
      const envCopies: Record<string, string> = {};
      for (const rawArr of rawLists) {
        for (const rawEp of rawArr) {
          const key = String(rawEp?.api_key_env || "");
          if (!key) continue;
          if (usedKeys.has(key)) {
            const newKey = nextEnvKeyName(key, usedKeys);
            envCopies[newKey] = key;
            rawEp.api_key_env = newKey;
            envFixNeeded = true;
          }
          usedKeys.add(rawEp.api_key_env);
        }
      }

      if (envFixNeeded) {
        // Persist the renamed config — writes raw JSON so all fields are preserved
        try {
          await writeWorkspaceFile("data/llm_endpoints.json", JSON.stringify(parsed, null, 2) + "\n");
        } catch { /* best-effort */ }

        // Copy env values from the original key to the new key so the endpoint
        // keeps working with whichever value was last written.
        const freshEnv = await ensureEnvLoaded(currentWorkspaceId || "__remote__");
        const copyEntries: Record<string, string> = {};
        for (const [newKey, origKey] of Object.entries(envCopies)) {
          const val = envGet(freshEnv, origKey) || "";
          if (val) {
            copyEntries[newKey] = val;
            setEnvDraft((e) => envSet(e, newKey, val));
          }
        }
        if (Object.keys(copyEntries).length > 0) {
          try {
            if (shouldUseHttpApi()) {
              await safeFetch(`${httpApiBase()}/api/config/env`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ entries: copyEntries }),
              });
            } else if (IS_TAURI && currentWorkspaceId) {
              await invoke("workspace_update_env", {
                workspaceId: currentWorkspaceId,
                entries: Object.entries(copyEntries).map(([key, value]) => ({ key, value })),
              });
            }
          } catch { /* best-effort */ }
        }

        // Reload to reflect the fixed env key names in EndpointDraft state
        await loadSavedEndpoints(/* skipAutoFix */ true);

        notifyError(
          "检测到多个端点共用相同的 API Key 环境变量名，已自动修复。"
          + "如果两个端点使用不同的 API Key，请编辑端点重新填写。",
        );
      }
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

  function normalizePriority(n: any, fallback: number) {
    const x = Number(n);
    if (!Number.isFinite(x) || x <= 0) return fallback;
    return Math.floor(x);
  }

  async function doFetchCompilerModels() {
    const compilerSelectedProvider = providers.find((p) => p.slug === compilerProviderSlug) || null;
    const isCompilerLocal = isLocalProvider(compilerSelectedProvider);
    if (!compilerApiKeyValue.trim() && !isCompilerLocal) {
      notifyError("请先填写编译端点的 API Key 值");
      return;
    }
    if (!compilerBaseUrl.trim()) {
      notifyError("请先填写编译端点的 Base URL");
      return;
    }
    setCompilerModels([]);
    const _busyId = notifyLoading("拉取编译端点模型列表...");
    try {
      const effectiveCompilerKey = compilerApiKeyValue.trim() || (isCompilerLocal ? localProviderPlaceholderKey(compilerSelectedProvider) : "");
      const parsed = await fetchModelListUnified({
        apiType: compilerApiType,
        baseUrl: compilerBaseUrl,
        providerSlug: compilerProviderSlug || null,
        apiKey: effectiveCompilerKey,
      });
      setCompilerModels(parsed);
      setCompilerModel("");
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
    } catch (e: any) {
      const raw = String(e?.message || e);
      const cprov = providers.find((p) => p.slug === compilerProviderSlug);
      notifyError(friendlyFetchError(raw, t, cprov?.name));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doFetchSttModels() {
    const sttSelectedProvider = providers.find((p) => p.slug === sttProviderSlug) || null;
    const isSttLocal = isLocalProvider(sttSelectedProvider);
    if (!sttApiKeyValue.trim() && !isSttLocal) {
      notifyError("请先填写 STT 端点的 API Key 值");
      return;
    }
    if (!sttBaseUrl.trim()) {
      notifyError("请先填写 STT 端点的 Base URL");
      return;
    }
    setSttModels([]);
    const _busyId = notifyLoading("拉取 STT 端点模型列表...");
    try {
      const effectiveKey = sttApiKeyValue.trim() || (isSttLocal ? localProviderPlaceholderKey(sttSelectedProvider) : "");
      const parsed = await fetchModelListUnified({
        apiType: sttApiType,
        baseUrl: sttBaseUrl,
        providerSlug: sttProviderSlug || null,
        apiKey: effectiveKey,
      });
      setSttModels(parsed);
      setSttModel("");
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
    } catch (e: any) {
      const raw = String(e?.message || e);
      const sprov = providers.find((p) => p.slug === sttProviderSlug);
      notifyError(friendlyFetchError(raw, t, sprov?.name));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveCompilerEndpoint(): Promise<boolean> {
    if (!currentWorkspaceId && dataMode !== "remote") {
      notifyError("请先创建/选择一个当前工作区");
      return false;
    }
    if (!compilerModel.trim()) {
      notifyError("请填写编译模型名称");
      return false;
    }
    if (!compilerBaseUrl.trim()) {
      notifyError("请填写编译端点的 Base URL");
      return false;
    }
    if (!/^https?:\/\//i.test(compilerBaseUrl.trim())) {
      notifyError("编译端点 Base URL 必须以 http:// 或 https:// 开头");
      return false;
    }
    const compilerSelectedProvider = providers.find((p) => p.slug === compilerProviderSlug) || null;
    const isCompilerLocal = isLocalProvider(compilerSelectedProvider);
    const rawCompApiKeyEnv = compilerApiKeyEnv.trim()
      || compilerSelectedProvider?.api_key_env_suggestion
      || envKeyFromSlug(compilerProviderSlug || "custom");
    const effectiveCompApiKeyEnv = nextEnvKeyName(
      rawCompApiKeyEnv,
      new Set([...savedEndpoints, ...savedCompilerEndpoints].map((e) => e.api_key_env).filter(Boolean)),
    );
    const effectiveCompApiKeyValue = compilerApiKeyValue.trim() || (isCompilerLocal ? localProviderPlaceholderKey(compilerSelectedProvider) : "");
    if (!isCompilerLocal && !effectiveCompApiKeyValue) {
      notifyError("请填写编译端点的 API Key 值");
      return false;
    }
    const _busyId = notifyLoading("写入编译端点...");
    try {
      // Write API key to .env — 遵循路由原则
      const compilerEnvPayload = { entries: { [effectiveCompApiKeyEnv]: effectiveCompApiKeyValue } };
      if (shouldUseHttpApi()) {
        try {
          await safeFetch(`${httpApiBase()}/api/config/env`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(compilerEnvPayload),
          });
        } catch {
          if (IS_TAURI && currentWorkspaceId) {
            await invoke("workspace_update_env", {
              workspaceId: currentWorkspaceId,
              entries: [{ key: effectiveCompApiKeyEnv, value: effectiveCompApiKeyValue }],
            });
          }
        }
      } else if (IS_TAURI && currentWorkspaceId) {
        await invoke("workspace_update_env", {
          workspaceId: currentWorkspaceId,
          entries: [{ key: effectiveCompApiKeyEnv, value: effectiveCompApiKeyValue }],
        });
      }
      setEnvDraft((e) => envSet(e, effectiveCompApiKeyEnv, effectiveCompApiKeyValue));

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
        base_url: compilerBaseUrl.trim(),
        api_key_env: effectiveCompApiKeyEnv,
        model: compilerModel.trim(),
        priority: base.compiler_endpoints.length + 1,
        max_tokens: 2048,
        context_window: 200000,
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
      notifySuccess(`编译端点 ${name} 已保存`);
      await loadSavedEndpoints();
      return true;
    } catch (e) {
      notifyError(String(e));
      return false;
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doDeleteCompilerEndpoint(epName: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    const _busyId = notifyLoading("删除编译端点...");
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
      notifySuccess(`编译端点 ${epName} 已删除`);

      // Also re-read to sync fully (background)
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveSttEndpoint(): Promise<boolean> {
    if (!currentWorkspaceId && dataMode !== "remote") {
      notifyError("请先创建/选择一个当前工作区");
      return false;
    }
    if (!sttModel.trim()) {
      notifyError("请填写 STT 模型名称");
      return false;
    }
    if (!sttBaseUrl.trim()) {
      notifyError("请填写 STT 端点的 Base URL");
      return false;
    }
    if (!/^https?:\/\//i.test(sttBaseUrl.trim())) {
      notifyError("STT 端点 Base URL 必须以 http:// 或 https:// 开头");
      return false;
    }
    const sttSelectedProvider = providers.find((p) => p.slug === sttProviderSlug) || null;
    const isSttLocal = isLocalProvider(sttSelectedProvider);
    const rawSttApiKeyEnv = sttApiKeyEnv.trim()
      || sttSelectedProvider?.api_key_env_suggestion
      || envKeyFromSlug(sttProviderSlug || "custom");
    const effectiveSttApiKeyEnv = nextEnvKeyName(
      rawSttApiKeyEnv,
      new Set([...savedEndpoints, ...savedCompilerEndpoints, ...savedSttEndpoints].map((e) => e.api_key_env).filter(Boolean)),
    );
    const effectiveSttApiKeyValue = sttApiKeyValue.trim() || (isSttLocal ? localProviderPlaceholderKey(sttSelectedProvider) : "");
    if (!isSttLocal && !effectiveSttApiKeyValue) {
      notifyError("请填写 STT 端点的 API Key 值");
      return false;
    }
    const _busyId = notifyLoading("保存 STT 端点...");
    try {
      const sttEnvPayload = { entries: { [effectiveSttApiKeyEnv]: effectiveSttApiKeyValue } };
      if (shouldUseHttpApi()) {
        try {
          await safeFetch(`${httpApiBase()}/api/config/env`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(sttEnvPayload),
          });
        } catch {
          if (IS_TAURI && currentWorkspaceId) {
            await invoke("workspace_update_env", {
              workspaceId: currentWorkspaceId,
              entries: [{ key: effectiveSttApiKeyEnv, value: effectiveSttApiKeyValue }],
            });
          }
        }
      } else if (IS_TAURI && currentWorkspaceId) {
        await invoke("workspace_update_env", {
          workspaceId: currentWorkspaceId,
          entries: [{ key: effectiveSttApiKeyEnv, value: effectiveSttApiKeyValue }],
        });
      }
      setEnvDraft((e) => envSet(e, effectiveSttApiKeyEnv, effectiveSttApiKeyValue));

      let currentJson = "";
      try {
        currentJson = await readWorkspaceFile("data/llm_endpoints.json");
      } catch { currentJson = ""; }
      const base = currentJson ? JSON.parse(currentJson) : { endpoints: [], settings: {} };
      base.stt_endpoints = Array.isArray(base.stt_endpoints) ? base.stt_endpoints : [];

      const baseName = (sttEndpointName.trim() || `stt-${sttProviderSlug || "provider"}-${sttModel.trim()}`).slice(0, 64);
      const usedNames = new Set(base.stt_endpoints.map((e: any) => String(e?.name || "")).filter(Boolean));
      let name = baseName;
      if (usedNames.has(name)) {
        for (let i = 2; i < 10; i++) {
          const candidate = `${baseName}-${i}`.slice(0, 64);
          if (!usedNames.has(candidate)) { name = candidate; break; }
        }
      }

      const endpoint = {
        name,
        provider: sttProviderSlug || "custom",
        api_type: sttApiType,
        base_url: sttBaseUrl.trim(),
        api_key_env: effectiveSttApiKeyEnv,
        model: sttModel.trim(),
        priority: base.stt_endpoints.length + 1,
        max_tokens: 0,
        context_window: 0,
        timeout: 60,
        capabilities: ["text"],
      };
      base.stt_endpoints.push(endpoint);
      base.stt_endpoints.sort((a: any, b: any) => (Number(a?.priority) || 999) - (Number(b?.priority) || 999));

      await writeWorkspaceFile("data/llm_endpoints.json", JSON.stringify(base, null, 2) + "\n");

      setSttModel("");
      setSttApiKeyValue("");
      setSttEndpointName("");
      setSttBaseUrl("");
      setSttModels([]);
      notifySuccess(`STT 端点 ${name} 已保存`);
      await loadSavedEndpoints();
      return true;
    } catch (e) {
      notifyError(String(e));
      return false;
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doDeleteSttEndpoint(epName: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    const _busyId = notifyLoading("删除 STT 端点...");
    try {
      let currentJson = "";
      try {
        currentJson = await readWorkspaceFile("data/llm_endpoints.json");
      } catch { currentJson = ""; }
      const base = currentJson ? JSON.parse(currentJson) : { endpoints: [], settings: {} };
      base.stt_endpoints = Array.isArray(base.stt_endpoints) ? base.stt_endpoints : [];
      base.stt_endpoints = base.stt_endpoints
        .filter((e: any) => String(e?.name || "") !== epName)
        .map((e: any, i: number) => ({ ...e, priority: i + 1 }));

      await writeWorkspaceFile("data/llm_endpoints.json", JSON.stringify(base, null, 2) + "\n");

      setSavedSttEndpoints((prev) => prev.filter((e) => e.name !== epName));
      notifySuccess(`STT 端点 ${epName} 已删除`);

      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doReorderByNames(orderedNames: string[]) {
    if (!currentWorkspaceId) return;
    const _busyId = notifyLoading("保存排序...");
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
      notifySuccess("已保存端点顺序（priority 已更新）");
      await loadSavedEndpoints();
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
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
      apiKeyValue: envDraft[ep.api_key_env || ""] || "",
      modelId: ep.model || "",
      caps: Array.isArray(ep.capabilities) && ep.capabilities.length ? ep.capabilities : ["text"],
      maxTokens: typeof ep.max_tokens === "number" ? ep.max_tokens : 0,
      contextWindow: typeof ep.context_window === "number" ? ep.context_window : 200000,
      timeout: typeof ep.timeout === "number" ? ep.timeout : 180,
      rpmLimit: typeof ep.rpm_limit === "number" ? ep.rpm_limit : 0,
      pricingTiers: Array.isArray(ep.pricing_tiers) ? ep.pricing_tiers.map((t: any) => ({
        max_input: Number.isFinite(Number(t?.max_input)) ? Number(t.max_input) : 0,
        input_price: Number.isFinite(Number(t?.input_price)) ? Number(t.input_price) : 0,
        output_price: Number.isFinite(Number(t?.output_price)) ? Number(t.output_price) : 0,
      })) : [],
    });
    setEditModalOpen(true);
    setConnTestResult(null);
  }

  function resetEndpointEditor() {
    setEditingOriginalName(null);
    setEditDraft(null);
    setEditModalOpen(false);
    setEditModels([]);
    setSecretShown((m) => ({ ...m, __EDIT_EP_KEY: false }));
    setCodingPlanMode(false);
  }

  async function doFetchEditModels() {
    if (!editDraft) return;
    const editProvider = providers.find((p) => p.slug === editDraft.providerSlug);
    const isEditLocal = isLocalProvider(editProvider);
    const key = editDraft.apiKeyValue.trim() || envGet(envDraft, editDraft.apiKeyEnv) || (isEditLocal ? localProviderPlaceholderKey(editProvider) : "");
    if (!isEditLocal && !key) {
      notifyError("请先填写 API Key 值（或确保对应环境变量已有值）");
      return;
    }
    if (!editDraft.baseUrl.trim()) {
      notifyError("请先填写 Base URL");
      return;
    }
    const _busyId = notifyLoading("拉取模型列表...");
    try {
      const parsed = await fetchModelListUnified({
        apiType: editDraft.apiType,
        baseUrl: editDraft.baseUrl,
        providerSlug: editDraft.providerSlug || null,
        apiKey: key || "local",
      });
      setEditModels(parsed);
      if (parsed.length > 0) {
        notifySuccess(t("llm.fetchSuccess", { count: parsed.length }));
      } else {
        notifyError(t("llm.fetchErrorEmpty"));
      }
    } catch (e: any) {
      const raw = String(e?.message || e);
      const eprov = providers.find((p) => p.slug === (editDraft?.providerSlug || ""));
      notifyError(friendlyFetchError(raw, t, eprov?.name));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doSaveEditedEndpoint() {
    if (!currentWorkspaceId) {
      notifyError("请先创建/选择一个当前工作区");
      return;
    }
    if (!editDraft || !editingOriginalName) return;
    if (!editDraft.name.trim()) {
      notifyError("端点名称不能为空");
      return;
    }
    if (!editDraft.modelId.trim()) {
      notifyError("模型不能为空");
      return;
    }
    if (!editDraft.apiKeyEnv.trim()) {
      notifyError("API Key 环境变量名不能为空");
      return;
    }
    if (!editDraft.baseUrl.trim()) {
      notifyError("请填写 Base URL");
      return;
    }
    if (!/^https?:\/\//i.test(editDraft.baseUrl.trim())) {
      notifyError("Base URL 必须以 http:// 或 https:// 开头");
      return;
    }
    const _busyId = notifyLoading("保存修改...");
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
            if (IS_TAURI && currentWorkspaceId) {
              await invoke("workspace_update_env", {
                workspaceId: currentWorkspaceId,
                entries: [{ key: editDraft.apiKeyEnv.trim(), value: editDraft.apiKeyValue.trim() }],
              });
            }
          }
        } else if (IS_TAURI && currentWorkspaceId) {
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
      const validTiers = (editDraft.pricingTiers || []).filter(
        (t) => t.input_price > 0 || t.output_price > 0
      );
      const next: Record<string, any> = {
        name: editDraft.name.trim().slice(0, 64),
        provider: editDraft.providerSlug || "custom",
        api_type: editDraft.apiType,
        base_url: editDraft.baseUrl.trim(),
        api_key_env: editDraft.apiKeyEnv.trim(),
        model: editDraft.modelId.trim(),
        priority: normalizePriority(editDraft.priority, 1),
        max_tokens: editDraft.maxTokens ?? 0,
        context_window: editDraft.contextWindow ?? 200000,
        timeout: editDraft.timeout ?? 180,
        rpm_limit: editDraft.rpmLimit ?? 0,
        capabilities: editDraft.caps?.length ? editDraft.caps : ["text"],
        extra_params:
          (editDraft.caps || []).includes("thinking") && editDraft.providerSlug === "dashscope"
            ? { enable_thinking: true }
            : undefined,
      };
      next.pricing_tiers = validTiers.length > 0 ? validTiers : undefined;
      if (idx >= 0) {
        const prev = endpoints[idx] || {};
        const merged = { ...prev, ...next };
        if (!next.pricing_tiers) delete merged.pricing_tiers;
        endpoints[idx] = merged;
      } else {
        endpoints.push(next);
      }
      endpoints.sort((a: any, b: any) => (Number(a?.priority) || 999) - (Number(b?.priority) || 999));
      await writeEndpointsJson(endpoints, settings);
      notifySuccess("端点已更新");
      setEditModalOpen(false);
      await loadSavedEndpoints();
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  useEffect(() => {
    if (stepId !== "llm") return;
    loadSavedEndpoints().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepId, currentWorkspaceId, dataMode]);

  async function doSaveEndpoint(): Promise<boolean> {
    if (!currentWorkspaceId) {
      notifyError("请先创建/选择一个当前工作区");
      return false;
    }
    if (!selectedModelId) {
      notifyError("请先选择模型");
      return false;
    }
    if (!baseUrl.trim()) {
      notifyError("请填写 Base URL");
      return false;
    }
    if (!/^https?:\/\//i.test(baseUrl.trim())) {
      notifyError("Base URL 必须以 http:// 或 https:// 开头");
      return false;
    }
    const isLocal = isLocalProvider(selectedProvider);
    // 本地服务商允许空 API Key（自动填入 placeholder）
    const effectiveApiKeyValue = apiKeyValue.trim() || (isLocal ? localProviderPlaceholderKey(selectedProvider) : "");
    // apiKeyEnv 兜底：即使 useEffect 未触发也能生成合理的环境变量名
    // When creating (not editing), deduplicate against existing endpoints to avoid
    // two endpoints sharing the same env var (which causes key overwrite on save).
    const rawApiKeyEnv = apiKeyEnv.trim()
      || selectedProvider?.api_key_env_suggestion
      || envKeyFromSlug(selectedProvider?.slug || providerSlug || "custom");
    const effectiveApiKeyEnv = isEditingEndpoint
      ? rawApiKeyEnv
      : nextEnvKeyName(rawApiKeyEnv, new Set(savedEndpoints.map((e) => e.api_key_env).filter(Boolean)));
    if (!isLocal && !effectiveApiKeyValue) {
      notifyError("请填写 API Key 值（会写入工作区 .env）");
      return false;
    }
    const _busyId = notifyLoading(isEditingEndpoint ? "更新端点配置..." : "写入端点配置...");

    try {
      await ensureEnvLoaded(currentWorkspaceId);
      setEnvDraft((e) => envSet(e, effectiveApiKeyEnv, effectiveApiKeyValue));
      const envPayload = { entries: { [effectiveApiKeyEnv]: effectiveApiKeyValue } };

      if (shouldUseHttpApi()) {
        try {
          await safeFetch(`${httpApiBase()}/api/config/env`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(envPayload),
          });
        } catch {
          if (IS_TAURI && currentWorkspaceId) {
            await invoke("workspace_update_env", {
              workspaceId: currentWorkspaceId,
              entries: [{ key: effectiveApiKeyEnv, value: effectiveApiKeyValue }],
            });
          }
        }
      } else if (IS_TAURI && currentWorkspaceId) {
        await invoke("workspace_update_env", {
          workspaceId: currentWorkspaceId,
          entries: [{ key: effectiveApiKeyEnv, value: effectiveApiKeyValue }],
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
          base_url: baseUrl.trim(),
          api_key_env: effectiveApiKeyEnv,
          model: selectedModelId,
          priority: normalizePriority(endpointPriority, 1),
          max_tokens: addEpMaxTokens,
          context_window: addEpContextWindow,
          timeout: addEpTimeout,
          rpm_limit: addEpRpmLimit,
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
            base.endpoints.push(endpoint);
          } else {
            const prev = base.endpoints[idx] || {};
            base.endpoints[idx] = { ...prev, ...endpoint };
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

      notifySuccess(
        isEditingEndpoint
          ? "端点已更新：data/llm_endpoints.json（同时已写入 API Key 到 .env）。"
          : "端点已追加写入：data/llm_endpoints.json（同时已写入 API Key 到 .env）。你可以继续添加备份端点。",
      );
      if (isEditingEndpoint) resetEndpointEditor();
      await loadSavedEndpoints();
      return true;
    } catch (e) {
      notifyError(String(e));
      return false;
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doDeleteEndpoint(name: string) {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    const _busyId = notifyLoading("删除端点...");
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      const base = raw ? JSON.parse(raw) : { endpoints: [], settings: {} };
      const eps = Array.isArray(base.endpoints) ? base.endpoints : [];
      base.endpoints = eps.filter((e: any) => String(e?.name || "") !== name);
      const next = JSON.stringify(base, null, 2) + "\n";
      await writeWorkspaceFile("data/llm_endpoints.json", next);

      // Immediately update local state
      setSavedEndpoints((prev) => prev.filter((e) => e.name !== name));
      notifySuccess(`已删除端点：${name}`);

      // Background re-read to fully sync
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    } finally {
      dismissLoading(_busyId);
    }
  }

  async function doToggleEndpointEnabled(name: string, endpointType: "endpoints" | "compiler_endpoints" | "stt_endpoints" = "endpoints") {
    if (!currentWorkspaceId && dataMode !== "remote") return;
    try {
      const raw = await readWorkspaceFile("data/llm_endpoints.json");
      const base = raw ? JSON.parse(raw) : { endpoints: [], settings: {} };
      const eps = Array.isArray(base[endpointType]) ? base[endpointType] : [];
      for (const ep of eps) {
        if (String(ep?.name || "") === name) {
          ep.enabled = ep.enabled === false ? true : false;
          break;
        }
      }
      base[endpointType] = eps;
      await writeWorkspaceFile("data/llm_endpoints.json", JSON.stringify(base, null, 2) + "\n");
      loadSavedEndpoints().catch(() => {});
    } catch (e) {
      notifyError(String(e));
    }
  }

  async function saveEnvKeys(keys: string[]) {
    const entries: Record<string, string> = {};
    for (const k of keys) {
      if (Object.prototype.hasOwnProperty.call(envDraft, k)) {
        const v = envDraft[k];
        if (typeof v === "string" && v.length > 0) {
          entries[k] = v;
        }
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
        logger.warn("App", "saveEnvKeys: HTTP failed, falling back to Tauri");
      }
    }
    if (IS_TAURI && currentWorkspaceId) {
      await ensureEnvLoaded(currentWorkspaceId);
      const tauriEntries = Object.entries(entries).map(([key, value]) => ({ key, value }));
      await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries: tauriEntries });
    }
  }

  const PROVIDER_APPLY_URLS: Record<string, string> = {
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
    ollama: "https://ollama.com/library",
    lmstudio: "https://lmstudio.ai/",
  };
  function getProviderApplyUrl(slug: string): string {
    return PROVIDER_APPLY_URLS[slug.toLowerCase()] || "";
  }
  async function openApplyUrl(url: string) {
    try { await openExternalUrl(url); } catch {
      const ok = await copyToClipboard(url);
      if (ok) notifySuccess("链接已复制到剪贴板：" + url);
      else notifyError("无法打开链接，请手动访问：" + url);
    }
  }
  const providerApplyUrl = useMemo(() => getProviderApplyUrl(selectedProvider?.slug || ""), [selectedProvider?.slug]);

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
        // API keys are already written individually by each endpoint save;
        // bulk-writing them here risks overwriting valid keys with stale envDraft values.
        return { keys: [], savedMsg: t("config.llmSaved") };

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


  // auto-load all advanced panel data when entering the page
  useEffect(() => {
    if (stepId !== "advanced") { advLoadedRef.current = false; return; }
    if (advLoadedRef.current) return;
    advLoadedRef.current = true;

    const apiUrl = shouldUseHttpApi() ? httpApiBase() : null;

    if (apiUrl) {
      // System info
      setAdvLoading((p) => ({ ...p, sysinfo: true }));
      safeFetch(`${apiUrl}/api/system-info`, { signal: AbortSignal.timeout(8_000) })
        .then((r) => r.json())
        .then((data) => {
          const info: Record<string, string> = {};
          if (data.os) info["OS"] = data.os;
          if (data.openakita_version) info["Backend"] = data.openakita_version;
          setAdvSysInfo(info);
        })
        .catch(() => {})
        .finally(() => setAdvLoading((p) => ({ ...p, sysinfo: false })));

      // Load current HUB_API_URL from .env
      safeFetch(`${apiUrl}/api/config/env`, { signal: AbortSignal.timeout(5_000) })
        .then((r) => r.json())
        .then((data) => {
          if (data.env?.HUB_API_URL) setHubApiUrl(data.env.HUB_API_URL);
        })
        .catch(() => {});
    }

    // Load migration info (Tauri only)
    if (IS_TAURI) {
      invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>("get_root_dir_info")
        .then((info) => {
          setMigrateCurrentRoot(info.currentRoot);
          setMigrateCustomRoot(info.customRoot);
        })
        .catch(() => {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepId]);

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
        try {
          // Try new config API (may not exist in older service versions)
          const envRes = await safeFetch(`${effectiveApiBaseUrl}/api/config/env`);
          const envData = await envRes.json();
          const env = envData.env || {};
          setEnvDraft((prev) => ({ ...prev, ...env }));
          envLoadedForWs.current = "__remote__";

          const epRes = await safeFetch(`${effectiveApiBaseUrl}/api/config/endpoints`);
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
            h[c.channel || c.name] = { status: c.status || "unknown", error: c.error || null, lastCheckedAt: c.last_checked_at || null };
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
        setSkillSummary(null);
        setSkillsDetail(null);
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
            h[c.channel || c.name] = { status: c.status || "unknown", error: c.error || null, lastCheckedAt: c.last_checked_at || null };
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
    const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
    const ws = workspaces.find((w) => w.id === effectiveWsId) || workspaces[0] || null;
    const im = [
      { k: "TELEGRAM_ENABLED", name: "Telegram", required: ["TELEGRAM_BOT_TOKEN"] },
      { k: "FEISHU_ENABLED", name: t("status.feishu"), required: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"] },
      { k: "WEWORK_ENABLED", name: t("status.wework"), required: ["WEWORK_CORP_ID", "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY"] },
      { k: "WEWORK_WS_ENABLED", name: t("status.weworkWs"), required: ["WEWORK_WS_BOT_ID", "WEWORK_WS_SECRET"] },
      { k: "DINGTALK_ENABLED", name: t("status.dingtalk"), required: ["DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"] },
      { k: "ONEBOT_ENABLED", name: "OneBot", required: [] },
      { k: "QQBOT_ENABLED", name: "QQ 机器人", required: ["QQBOT_APP_ID", "QQBOT_APP_SECRET"] },
    ];
    const imStatus = im.map((c) => {
      const enabled = envGet(envDraft, c.k, "false").toLowerCase() === "true";
      const missing = c.required.filter((rk) => !(envGet(envDraft, rk) || "").trim());
      return { ...c, enabled, ok: enabled ? missing.length === 0 : true, missing };
    });

    return (
      <>
        {/* Banner: backend not running (hide during initial probe; hide in web mode — backend is always running) */}
        {IS_TAURI && !serviceStatus?.running && serviceStatus !== null && effectiveWsId && (
          <div style={{
            marginBottom: 16, padding: "16px 20px", borderRadius: 10,
            background: "rgba(245, 158, 11, 0.15)",
            border: "1px solid rgba(245, 158, 11, 0.4)",
            display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
          }}>
            <div style={{ fontSize: 28, lineHeight: 1, color: "var(--warning)" }}>&#9888;</div>
            <div style={{ flex: 1, minWidth: 180 }}>
              <div style={{ fontWeight: 700, fontSize: 15, color: "var(--warning)", marginBottom: 4 }}>
                {t("status.backendNotRunning")}
              </div>
              <div style={{ fontSize: 13, color: "var(--warning)", opacity: 0.85 }}>
                {t("status.backendNotRunningHint")}
              </div>
            </div>
            <Button
              size="sm"
              onClick={async () => { await startLocalServiceWithConflictCheck(effectiveWsId); }}
              disabled={!!busy}
            >
              {busy ? <><Loader2 className="animate-spin mr-1" size={14} />{busy}</> : <><Play size={14} className="mr-1" />{t("topbar.start")}</>}
            </Button>
          </div>
        )}
        {/* Banner: auto-starting backend (shown while serviceStatus is null and busy with auto-start) */}
        {IS_TAURI && serviceStatus === null && !!busy && effectiveWsId && (
          <div style={{
            marginBottom: 16, padding: "16px 20px", borderRadius: 10,
            background: "rgba(37, 99, 235, 0.15)",
            border: "1px solid rgba(37, 99, 235, 0.4)",
            display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
          }}>
            <div className="spinner" style={{ width: 22, height: 22, flexShrink: 0, color: "var(--brand)" }} />
            <div style={{ flex: 1, minWidth: 180 }}>
              <div style={{ fontWeight: 700, fontSize: 15, color: "var(--brand)", marginBottom: 4 }}>
                {busy}
              </div>
              <div style={{ fontSize: 13, color: "var(--brand)", opacity: 0.85 }}>
                {t("status.backendNotRunningHint")}
              </div>
            </div>
          </div>
        )}

        {/* Top: Unified status panel */}
        <div className="statusPanel">
          {/* Service row */}
          <div className="statusPanelRow statusPanelRowService">
            <div className="statusPanelIcon">
              <Server size={18} />
            </div>
            <div className="statusPanelInfo">
              <div className="statusPanelTitle">
                {t("status.service")}
                <Badge variant={
                  serviceStatus === null ? "secondary"
                  : heartbeatState === "alive" ? "default"
                  : heartbeatState === "degraded" || heartbeatState === "suspect" ? "secondary"
                  : serviceStatus?.running ? "default"
                  : "outline"
                } className={`statusBadgeInline ${
                  serviceStatus === null ? "statusBadgeWarn"
                  : heartbeatState === "alive" ? "statusBadgeOk"
                  : heartbeatState === "degraded" || heartbeatState === "suspect" ? "statusBadgeWarn"
                  : serviceStatus?.running ? "statusBadgeOk"
                  : "statusBadgeOff"
                }`}>
                  {serviceStatus === null ? (busy || t("topbar.starting"))
                  : heartbeatState === "degraded" ? t("status.unresponsive")
                  : serviceStatus?.running ? t("topbar.running")
                  : t("topbar.stopped")}
                </Badge>
              </div>
              <div className="statusPanelDesc">
                {serviceStatus?.pid ? `PID ${serviceStatus.pid}` : ""}
              </div>
            </div>
            {IS_TAURI && (
            <div className="statusPanelActions">
              {!serviceStatus?.running && serviceStatus !== null && effectiveWsId && (
                <Button size="sm" className="statusBtn" onClick={async () => {
                  await startLocalServiceWithConflictCheck(effectiveWsId);
                }} disabled={!!busy}>{busy ? <><Loader2 className="animate-spin" size={13} />{busy}</> : <><Play size={13} />{t("topbar.start")}</>}</Button>
              )}
              {serviceStatus?.running && effectiveWsId && (<>
                <Button size="sm" variant="destructive" className="statusBtn" onClick={async () => {
                  const _b = notifyLoading(t("status.stopping"));
                  try {
                    await doStopService(effectiveWsId);
                  } catch (e) { notifyError(String(e)); } finally { dismissLoading(_b); }
                }} disabled={!!busy}><Square size={13} />{t("status.stop")}</Button>
                <Button size="sm" variant="outline" className="statusBtn" onClick={async () => {
                  const _b = notifyLoading(t("status.restarting"));
                  try {
                    await doStopService(effectiveWsId);
                    await waitForServiceDown("http://127.0.0.1:18900", 15000);
                    dismissLoading(_b);
                    await doStartLocalService(effectiveWsId);
                  } catch (e) { notifyError(String(e)); dismissLoading(_b); }
                }} disabled={!!busy}><RotateCcw size={13} />{t("status.restart")}</Button>
              </>)}
            </div>
            )}
          </div>
          {/* Multi-process warning */}
          {IS_TAURI && detectedProcesses.length > 1 && (
            <div className="statusPanelAlert">
              <span style={{ fontWeight: 600 }}>⚠ 检测到 {detectedProcesses.length} 个 OpenAkita 进程正在运行</span>
              <span style={{ fontSize: 11, opacity: 0.8 }}>
                ({detectedProcesses.map(p => `PID ${p.pid}`).join(", ")})
              </span>
              <Button size="sm" variant="destructive" style={{ marginLeft: "auto" }} onClick={async () => {
                const _b = notifyLoading("正在停止所有进程...");
                try {
                  const stopped = await invoke<number[]>("openakita_stop_all_processes");
                  setDetectedProcesses([]);
                  notifySuccess(`已停止 ${stopped.length} 个进程`);
                  await refreshStatus();
                } catch (e) { notifyError(String(e)); } finally { dismissLoading(_b); }
              }} disabled={!!busy}><Square size={12} className="mr-1" />全部停止</Button>
            </div>
          )}
          {/* Degraded hint */}
          {heartbeatState === "degraded" && (
            <div className="statusPanelAlert">
              <DotYellow size={8} />
              <span>
                {t("status.degradedHint")}
                <br />
                <span style={{ fontSize: 11, opacity: 0.8 }}>{t("status.degradedAutoClean")}</span>
              </span>
            </div>
          )}
          {/* Troubleshooting panel */}
          {(heartbeatState === "dead" && !serviceStatus?.running) && (
            <TroubleshootPanel t={t} />
          )}

          {/* Auto-update row — desktop only */}
          {IS_TAURI && (
          <div className="statusPanelRow">
            <div className="statusPanelIcon">
              <Download size={18} />
            </div>
            <div className="statusPanelInfo">
              <div className="statusPanelTitle">
                {t("status.autoUpdate")}
                <Badge variant={autoUpdateEnabled ? "default" : "outline"} className={`statusBadgeInline ${autoUpdateEnabled ? "statusBadgeOk" : "statusBadgeOff"}`}>
                  {autoUpdateEnabled ? t("status.on") : t("status.off")}
                </Badge>
              </div>
              <div className="statusPanelDesc">{t("status.autoUpdateHint")}</div>
            </div>
            <div className="statusPanelActions">
              <Button size="sm" variant="outline" className={cn(
                "h-7 text-xs px-2.5",
                autoUpdateEnabled
                  ? "bg-amber-50 text-amber-600 border-amber-200 hover:bg-amber-100 hover:text-amber-700 dark:bg-amber-950 dark:text-amber-400 dark:border-amber-800 dark:hover:bg-amber-900"
                  : "bg-emerald-50 text-emerald-600 border-emerald-200 hover:bg-emerald-100 hover:text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400 dark:border-emerald-800 dark:hover:bg-emerald-900",
              )} onClick={async () => {
                const _b = notifyLoading(t("common.loading"));
                try {
                  const next = !autoUpdateEnabled;
                  await invoke("set_auto_update", { enabled: next });
                  setAutoUpdateEnabled(next);
                  if (!next) { setNewRelease(null); setUpdateAvailable(null); setUpdateProgress({ status: "idle" }); }
                } catch (e) { notifyError(String(e)); } finally { dismissLoading(_b); }
              }} disabled={autoUpdateEnabled === null || !!busy}>{autoUpdateEnabled ? <PowerOff size={12} /> : <Power size={12} />}{autoUpdateEnabled ? t("status.off") : t("status.on")}</Button>
            </div>
          </div>
          )}

          {/* Autostart row — desktop only */}
          {IS_TAURI && (
          <div className="statusPanelRow">
            <div className="statusPanelIcon">
              <Zap size={18} />
            </div>
            <div className="statusPanelInfo">
              <div className="statusPanelTitle">
                {t("status.autostart")}
                <Badge variant={autostartEnabled ? "default" : "outline"} className={`statusBadgeInline ${autostartEnabled ? "statusBadgeOk" : "statusBadgeOff"}`}>
                  {autostartEnabled ? t("status.on") : t("status.off")}
                </Badge>
              </div>
              <div className="statusPanelDesc">{t("status.autostartHint")}</div>
            </div>
            <div className="statusPanelActions">
              <Button size="sm" variant="outline" className={cn(
                "h-7 text-xs px-2.5",
                autostartEnabled
                  ? "bg-amber-50 text-amber-600 border-amber-200 hover:bg-amber-100 hover:text-amber-700 dark:bg-amber-950 dark:text-amber-400 dark:border-amber-800 dark:hover:bg-amber-900"
                  : "bg-emerald-50 text-emerald-600 border-emerald-200 hover:bg-emerald-100 hover:text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400 dark:border-emerald-800 dark:hover:bg-emerald-900",
              )} onClick={async () => {
                const _b = notifyLoading(t("common.loading"));
                try { const next = !autostartEnabled; await invoke("autostart_set_enabled", { enabled: next }); setAutostartEnabled(next); } catch (e) { notifyError(String(e)); } finally { dismissLoading(_b); }
              }} disabled={autostartEnabled === null || !!busy}>{autostartEnabled ? <PowerOff size={12} /> : <Power size={12} />}{autostartEnabled ? t("status.off") : t("status.on")}</Button>
            </div>
          </div>
          )}

          {/* Workspace row */}
          <div className="statusPanelRow statusPanelRowWs">
            <div className="statusPanelIcon">
              <FolderOpen size={18} />
            </div>
            <div className="statusPanelInfo" style={{ flex: 1, minWidth: 0 }}>
              <div className="statusPanelTitle">{t("config.step.workspace")}</div>
              <div className="statusPanelDesc" style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ fontWeight: 600, color: "var(--fg)" }}>{currentWorkspaceId || "—"}</span>
                <span style={{ opacity: 0.5 }}>·</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>{ws?.path || ""}</span>
              </div>
            </div>
            {ws?.path && (
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                title={t("status.openFolder")}
                onClick={async () => {
                  const { openFileWithDefault } = await import("./platform");
                  try { await openFileWithDefault(ws.path); } catch (e) { logger.error("App", "openFileWithDefault failed", { error: String(e) }); }
                }}
              >
                <FolderOpen size={14} />
              </Button>
            )}
          </div>
        </div>

        {/* LLM Endpoints compact table */}
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span className="statusCardLabel">{t("status.llmEndpoints")} ({endpointSummary.length})</span>
            <Button size="sm" variant="outline" onClick={async () => {
              setHealthChecking("all");
              try {
                let results: Array<{ name: string; status: string; latency_ms: number | null; error: string | null; error_category: string | null; consecutive_failures: number; cooldown_remaining: number; is_extended_cooldown: boolean; last_checked_at: string | null }>;
                const healthUrl = shouldUseHttpApi() ? httpApiBase() : null;
                if (healthUrl) {
                  const res = await safeFetch(`${healthUrl}/api/health/check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}), signal: AbortSignal.timeout(60_000) });
                  const data = await res.json();
                  results = data.results || [];
                } else {
                  notifyError(t("status.needServiceRunning"));
                  setHealthChecking(null);
                  return;
                }
                const h: typeof endpointHealth = {};
                for (const r of results) { h[r.name] = { status: r.status, latencyMs: r.latency_ms, error: r.error, errorCategory: r.error_category, consecutiveFailures: r.consecutive_failures, cooldownRemaining: r.cooldown_remaining, isExtendedCooldown: r.is_extended_cooldown, lastCheckedAt: r.last_checked_at }; }
                setEndpointHealth(h);
              } catch (e) { notifyError(String(e)); } finally { setHealthChecking(null); }
            }} disabled={!!healthChecking || !!busy}>
              {healthChecking === "all" ? <><Loader2 className="animate-spin mr-1" size={14} />{t("status.checking")}</> : <><Activity size={14} className="mr-1" />{t("status.checkAll")}</>}
            </Button>
          </div>
          {endpointSummary.length === 0 ? (
            <div className="cardHint">{t("status.noEndpoints")}</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="h-9 text-xs">{t("status.endpoint")}</TableHead>
                  <TableHead className="h-9 text-xs">{t("status.model")}</TableHead>
                  <TableHead className="h-9 text-xs w-[50px]">Key</TableHead>
                  <TableHead className="h-9 text-xs">{t("sidebar.status")}</TableHead>
                  <TableHead className="h-9 text-xs w-[70px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
              {endpointSummary.map((e) => {
                const h = endpointHealth[e.name];
                const dotClass = h ? (h.status === "healthy" ? "healthy" : h.status === "degraded" ? "degraded" : "unhealthy") : e.keyPresent ? "unknown" : "unhealthy";
                const fullError = h && h.status !== "healthy" ? (h.error || "") : "";
                const label = h
                  ? h.status === "healthy" ? (h.latencyMs != null ? h.latencyMs + "ms" : "OK") : fullError.slice(0, 30) + (fullError.length > 30 ? "…" : "")
                  : e.keyPresent ? "—" : t("status.keyMissing");
                return (
                  <TableRow key={e.name} className={e.enabled === false ? "opacity-45" : ""}>
                    <TableCell className="py-2.5 font-semibold">
                      {e.name}
                      {e.enabled === false && <span className="ml-1.5 text-muted-foreground text-[10px] font-bold">{t("llm.disabled")}</span>}
                    </TableCell>
                    <TableCell className="py-2.5 text-muted-foreground text-xs">{e.model}</TableCell>
                    <TableCell className="py-2.5">{e.keyPresent ? <DotGreen /> : <DotGray />}</TableCell>
                    <TableCell className="py-2.5">
                      <span
                        className="inline-flex items-center gap-1 text-xs"
                        title={fullError ? (t("status.clickToCopy", "点击复制") + ": " + fullError) : undefined}
                      >
                        <span className={"healthDot " + dotClass} />
                        <span
                          className={fullError ? "cursor-pointer" : ""}
                          onClick={fullError ? async (ev) => { ev.stopPropagation(); const ok = await copyToClipboard(fullError); if (ok) notifySuccess(t("version.copied")); } : undefined}
                          role={fullError ? "button" : undefined}
                        >
                          {label}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell className="py-2.5 text-right">
                      <Button size="sm" variant="outline" className="h-7 text-xs px-2.5" onClick={async () => {
                        setHealthChecking(e.name);
                        try {
                          let r: any[];
                          const healthUrl = shouldUseHttpApi() ? httpApiBase() : null;
                          if (healthUrl) {
                            const res = await safeFetch(`${healthUrl}/api/health/check`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint_name: e.name }), signal: AbortSignal.timeout(60_000) });
                            const data = await res.json();
                            r = data.results || [];
                          } else {
                            notifyError(t("status.needServiceRunning"));
                            setHealthChecking(null);
                            return;
                          }
                          if (r[0]) setEndpointHealth((prev: any) => ({ ...prev, [r[0].name]: { status: r[0].status, latencyMs: r[0].latency_ms, error: r[0].error, errorCategory: r[0].error_category, consecutiveFailures: r[0].consecutive_failures, cooldownRemaining: r[0].cooldown_remaining, isExtendedCooldown: r[0].is_extended_cooldown, lastCheckedAt: r[0].last_checked_at } }));
                        } catch (err) { notifyError(String(err)); } finally { setHealthChecking(null); }
                      }} disabled={!!healthChecking || !!busy}>{healthChecking === e.name ? <Loader2 className="animate-spin" size={14} /> : t("status.check")}</Button>
                    </TableCell>
                  </TableRow>
                );
              })}
              </TableBody>
            </Table>
          )}
        </div>

        {/* IM Channels + Skills side by side */}
        <div className="statusGrid2" style={{ marginTop: 12 }}>
          <div className="card" style={{ marginTop: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span className="statusCardLabel">{t("status.imChannels")}</span>
              <Button size="sm" variant="outline" onClick={async () => {
                setImChecking(true);
                try {
                  const healthUrl = shouldUseHttpApi() ? httpApiBase() : null;
                  if (healthUrl) {
                    const res = await safeFetch(`${healthUrl}/api/im/channels`);
                    const data = await res.json();
                    const channels = data.channels || [];
                    const h: typeof imHealth = {};
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
                    setImHealth(h);
                  } else {
                    notifyError(t("status.needServiceRunning"));
                  }
                } catch (err) { notifyError(String(err)); } finally { setImChecking(false); }
              }} disabled={imChecking || !!busy}>
                {imChecking ? <><Loader2 className="animate-spin mr-1" size={14} />{t("status.checking")}</> : <><Activity size={14} className="mr-1" />{t("status.checkAll")}</>}
              </Button>
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
            <Button size="sm" variant="outline" className="w-full mt-2.5" onClick={() => setView("skills")}>{t("status.manageSkills")} <ArrowRight size={14} className="ml-1" /></Button>
          </div>
        </div>

        {/* Service log */}
        {serviceStatus?.running && (
          <div className="card" style={{ marginTop: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <span className="statusCardLabel">{t("status.log")}</span>
              <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                {(["ERROR", "WARN", "INFO", "DEBUG"] as const).map((level) => {
                  const active = logLevelFilter.has(level);
                  return (
                    <span
                      key={level}
                      className={`logFilterBadge logFilterBadge--${level}${active ? " logFilterBadge--active" : ""}`}
                      onClick={() => setLogLevelFilter((prev) => {
                        const next = new Set(prev);
                        if (next.has(level)) next.delete(level); else next.add(level);
                        return next;
                      })}
                    >{level}</span>
                  );
                })}
              </div>
            </div>
            <div style={{ position: "relative" }}>
              <div ref={serviceLogRef as any} className="logPre" onScroll={(e) => {
                const el = e.currentTarget;
                const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
                logAtBottomRef.current = atBottom;
                setLogAtBottom(atBottom);
              }}>{(() => {
                const raw = (serviceLog?.content || "").trim();
                if (!raw) return <span className="logMuted">{t("status.noLog")}</span>;
                return raw.split("\n").filter((line) => {
                  if (/\b(ERROR|CRITICAL|FATAL)\b/.test(line)) return logLevelFilter.has("ERROR");
                  if (/\bWARN(ING)?\b/.test(line)) return logLevelFilter.has("WARN");
                  if (/\bDEBUG\b/.test(line)) return logLevelFilter.has("DEBUG");
                  return logLevelFilter.has("INFO");
                }).map((line, i) => {
                  const isError = /\b(ERROR|CRITICAL|FATAL)\b/.test(line);
                  const isWarn = /\bWARN(ING)?\b/.test(line);
                  const isDebug = /\bDEBUG\b/.test(line);
                  const cls = isError ? "logLineError" : isWarn ? "logLineWarn" : isDebug ? "logLineDebug" : "logLineInfo";
                  const highlighted = line
                    .replace(/^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.]\d+)/, '<span class="logTimestamp">$1</span>')
                    .replace(/\b(INFO|ERROR|WARN(?:ING)?|DEBUG|CRITICAL|FATAL)\b/, '<span class="logLevel logLevel--$1">$1</span>')
                    .replace(/([\w.]+(?:\.[\w]+)+)\s+-\s+/, '<span class="logModule">$1</span> - ')
                    .replace(/\[([^\]]+)\]/, '[<span class="logTag">$1</span>]');
                  return <div key={i} className={`logLine ${cls}`} dangerouslySetInnerHTML={{ __html: highlighted }} />;
                });
              })()}</div>
              {!logAtBottom && (
                <button className="logScrollBtn" onClick={() => {
                  const el = serviceLogRef.current;
                  if (el) { el.scrollTop = el.scrollHeight; logAtBottomRef.current = true; setLogAtBottom(true); }
                }}>↓</button>
              )}
            </div>
          </div>
        )}
      </>
    );
  }

  // ── Add endpoint dialog state ──
  const [addEpDialogOpen, setAddEpDialogOpen] = useState(false);
  const [addCompDialogOpen, setAddCompDialogOpen] = useState(false);
  const [addSttDialogOpen, setAddSttDialogOpen] = useState(false);

  function openAddEpDialog() {
    resetEndpointEditor();
    setConnTestResult(null);
    setProviderSlug(providers.find(p => p.slug === "openai")?.slug ?? providers[0]?.slug ?? "");
    setApiType("openai");
    setBaseUrl("");
    setBaseUrlTouched(false);
    setApiKeyEnv("");
    setApiKeyEnvTouched(false);
    setApiKeyValue("");
    setModels([]);
    setSelectedModelId("");
    setEndpointName("");
    setEndpointNameTouched(false);
    setCapSelected([]);
    setCapTouched(false);
    setEndpointPriority(1);
    setCodingPlanMode(false);
    setAddEpMaxTokens(0);
    setAddEpContextWindow(200000);
    setAddEpTimeout(180);
    setAddEpRpmLimit(0);
    if (providers.length === 0) doLoadProviders();
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
            <Button size="sm" onClick={openAddEpDialog} disabled={!!busy}>
              + {t("llm.addEndpoint")}
            </Button>
          </div>

          {savedEndpoints.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
              <Inbox size={32} strokeWidth={1.5} className="mb-2 opacity-40" />
              <p className="text-sm">{t("llm.noEndpoints")}</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("status.endpoint")}</TableHead>
                  <TableHead>{t("status.model")}</TableHead>
                  <TableHead className="w-[50px]">Key</TableHead>
                  <TableHead className="w-[80px]">Priority</TableHead>
                  <TableHead className="w-[140px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {savedEndpoints.map((e) => (
                  <TableRow key={e.name} className={e.enabled === false ? "opacity-45" : undefined}>
                    <TableCell className="font-semibold">
                      {e.name}
                      {savedEndpoints[0]?.name === e.name && e.enabled !== false && <span className="ml-1.5 text-[10px] font-extrabold text-primary">{t("llm.primary")}</span>}
                      {e.enabled === false && <span className="ml-1.5 text-[10px] font-bold text-muted-foreground">{t("llm.disabled")}</span>}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{e.model}</TableCell>
                    <TableCell>{(envDraft[e.api_key_env] || "").trim() ? <DotGreen /> : <DotGray />}</TableCell>
                    <TableCell>{e.priority}</TableCell>
                    <TableCell>
                      <div className="flex gap-1 justify-end">
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" style={savedEndpoints[0]?.name === e.name ? { visibility: "hidden" } : undefined} onClick={() => doSetPrimaryEndpoint(e.name)} disabled={!!busy} title={t("llm.setPrimary")}><IconChevronUp size={14} /></Button>
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doToggleEndpointEnabled(e.name)} disabled={!!busy} title={e.enabled === false ? t("llm.enable") : t("llm.disable")}>{e.enabled !== false ? <IconPower size={14} /> : <IconCircle size={14} />}</Button>
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doStartEditEndpoint(e.name)} disabled={!!busy} title={t("llm.edit")}><IconEdit size={14} /></Button>
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>

        {/* ── Compiler endpoints ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div>
              <div className="cardTitle" style={{ marginBottom: 2 }}>{t("llm.compiler")}</div>
              <div className="cardHint">{t("llm.compilerHint")}</div>
            </div>
            <Button variant="outline" size="sm" className="bg-primary/5 border-primary/30 text-primary hover:bg-primary/10 hover:text-primary" onClick={() => { if (providers.length === 0) doLoadProviders(); setCompilerProviderSlug(""); setCompilerApiType("openai"); setCompilerBaseUrl(""); setCompilerApiKeyEnv(""); setCompilerApiKeyValue(""); setCompilerModel(""); setCompilerEndpointName(""); setCompilerCodingPlan(false); setCompilerModels([]); setAddCompDialogOpen(true); }} disabled={!!busy}>
              + {t("llm.addEndpoint")}
            </Button>
          </div>
          {savedCompilerEndpoints.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
              <Inbox size={32} strokeWidth={1.5} className="mb-2 opacity-40" />
              <p className="text-sm">{t("llm.noEndpoints")}</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("status.endpoint")}</TableHead>
                  <TableHead>{t("status.model")}</TableHead>
                  <TableHead className="w-[80px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {savedCompilerEndpoints.map((e) => (
                  <TableRow key={e.name} className={e.enabled === false ? "opacity-45" : undefined}>
                    <TableCell className="font-semibold">
                      {e.name}
                      {e.enabled === false && <span className="ml-1.5 text-[10px] font-bold text-muted-foreground">{t("llm.disabled")}</span>}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{e.model}</TableCell>
                    <TableCell>
                      <div className="flex gap-1 justify-end">
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doToggleEndpointEnabled(e.name, "compiler_endpoints")} disabled={!!busy} title={e.enabled === false ? t("llm.enable") : t("llm.disable")}>{e.enabled !== false ? <IconPower size={14} /> : <IconCircle size={14} />}</Button>
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteCompilerEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>

        {/* ── STT endpoints ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div>
              <div className="cardTitle" style={{ marginBottom: 2 }}>{t("llm.stt")}</div>
              <div className="cardHint">{t("llm.sttHint")}</div>
            </div>
            <Button variant="outline" size="sm" className="bg-primary/5 border-primary/30 text-primary hover:bg-primary/10 hover:text-primary" onClick={() => { if (providers.length === 0) doLoadProviders(); setSttProviderSlug(""); setSttApiType("openai"); setSttBaseUrl(""); setSttApiKeyEnv(""); setSttApiKeyValue(""); setSttModel(""); setSttEndpointName(""); setSttModels([]); setAddSttDialogOpen(true); }} disabled={!!busy}>
              + {t("llm.addEndpoint")}
            </Button>
          </div>
          {savedSttEndpoints.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
              <Inbox size={32} strokeWidth={1.5} className="mb-2 opacity-40" />
              <p className="text-sm">{t("llm.noEndpoints")}</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("status.endpoint")}</TableHead>
                  <TableHead>{t("status.model")}</TableHead>
                  <TableHead className="w-[80px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {savedSttEndpoints.map((e) => (
                  <TableRow key={e.name} className={e.enabled === false ? "opacity-45" : undefined}>
                    <TableCell className="font-semibold">
                      {e.name}
                      {e.enabled === false && <span className="ml-1.5 text-[10px] font-bold text-muted-foreground">{t("llm.disabled")}</span>}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{e.model}</TableCell>
                    <TableCell>
                      <div className="flex gap-1 justify-end">
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-foreground" onClick={() => doToggleEndpointEnabled(e.name, "stt_endpoints")} disabled={!!busy} title={e.enabled === false ? t("llm.enable") : t("llm.disable")}>{e.enabled !== false ? <IconPower size={14} /> : <IconCircle size={14} />}</Button>
                        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive hover:bg-destructive/10" onClick={() => askConfirm(`${t("common.confirmDeleteMsg")} "${e.name}"?`, () => doDeleteSttEndpoint(e.name))} disabled={!!busy} title={t("common.delete")}><IconTrash size={14} /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>

        {/* ── Add endpoint dialog ── */}
        <Dialog open={addEpDialogOpen} onOpenChange={(open) => { if (!open) setAddEpDialogOpen(false); }}>
          <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { resetEndpointEditor(); setConnTestResult(null); }}>
            <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
              <DialogTitle>{isEditingEndpoint ? t("llm.editEndpoint") : t("llm.addEndpoint")}</DialogTitle>
              <DialogDescription className="sr-only">{t("llm.addEndpoint")}</DialogDescription>
            </DialogHeader>

            <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
              {/* Provider */}
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1">{t("llm.provider")} {!["custom", "ollama", "lmstudio"].includes(providerSlug) && <span className="inline-flex items-center gap-0.5 text-[11px] font-normal text-muted-foreground/70 min-w-0"><span className="shrink-0">API 地址：</span><span className="inline-block max-w-[200px] overflow-x-auto whitespace-nowrap align-middle" style={{ scrollbarWidth: "thin" }}>{baseUrl || selectedProvider?.default_base_url || "—"}</span> <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] shrink-0" onClick={() => setBaseUrlExpanded(v => !v)}>{baseUrlExpanded ? "收起" : "配置"}</Button></span>}</Label>
                <ProviderSearchSelect
                  value={providerSlug}
                  onChange={(v) => { setProviderSlug(v); setBaseUrlExpanded(false); }}
                  options={providers.map((p) => ({ value: p.slug, label: p.name }))}
                  placeholder={providers.length === 0 ? t("common.loading") : undefined}
                  disabled={providers.length === 0}
                />
              </div>

              {/* Coding Plan toggle */}
              {selectedProvider?.coding_plan_base_url && (
                <label htmlFor="coding-plan-add" className="flex items-center justify-between gap-3 rounded-lg border border-border px-4 py-3 cursor-pointer select-none hover:bg-accent/50 transition-colors">
                  <div className="space-y-0.5">
                    <div className="text-sm font-medium">{t("llm.codingPlan")}</div>
                    <div className="text-xs text-muted-foreground">{t("llm.codingPlanHint")}</div>
                  </div>
                  <Switch id="coding-plan-add" checked={codingPlanMode} onCheckedChange={(v) => { setCodingPlanMode(v); setBaseUrlTouched(false); }} />
                </label>
              )}

              {/* Base URL */}
              {["custom", "ollama", "lmstudio"].includes(providerSlug) ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setBaseUrlTouched(true); }} placeholder={selectedProvider?.default_base_url || "https://api.example.com/v1"} />
              </div>
              ) : baseUrlExpanded ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); setBaseUrlTouched(true); }} placeholder={selectedProvider?.default_base_url || "https://api.example.com/v1"} />
              </div>
              ) : null}

              {/* API Key */}
              <div className="space-y-1.5">
                <Label className="inline-flex items-center gap-2">
                  API Key {isLocalProvider(selectedProvider) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                  {providerApplyUrl && !isLocalProvider(selectedProvider) && (
                    <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(providerApplyUrl)}>获取 API Key</Button>
                  )}
                </Label>
                <Input value={apiKeyValue} onChange={(e) => setApiKeyValue(e.target.value)} placeholder={isLocalProvider(selectedProvider) ? t("llm.localKeyPlaceholder") : "输入调用大模型的 API Key"} type={(secretShown.__LLM_API_KEY && !IS_WEB) ? "text" : "password"} />
                {isLocalProvider(selectedProvider) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
              </div>

              {/* Model */}
              <div className="space-y-1.5">
                <Label>{t("llm.selectModel")} <span className="text-[11px] font-normal text-muted-foreground/70">自行输入或<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchModels} disabled={(!apiKeyValue.trim() && !isLocalProvider(selectedProvider)) || !baseUrl.trim() || !!busy}>拉取模型列表</Button>并选择{models.length > 0 && <span className="text-muted-foreground/50">（已拉取 {models.length} 个）</span>}</span></Label>
                <SearchSelect
                  value={selectedModelId}
                  onChange={(v) => setSelectedModelId(v)}
                  options={models.map((m) => m.id)}
                  placeholder={models.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")}
                  disabled={!!busy}
                />
              </div>

              {/* Endpoint Name */}
              <div className="space-y-1.5">
                <Label>{t("llm.endpointName")}</Label>
                <Input value={endpointName} onChange={(e) => { setEndpointNameTouched(true); setEndpointName(e.target.value); }} placeholder="dashscope-qwen3-max" />
              </div>

              {/* Capabilities */}
              <div className="space-y-1.5">
                <Label>{t("llm.capabilities")}</Label>
                <div className="flex flex-wrap gap-2">
                  {[
                    { k: "text", name: t("llm.capText") },
                    { k: "thinking", name: t("llm.capThinking") },
                    { k: "vision", name: t("llm.capVision") },
                    { k: "video", name: t("llm.capVideo") },
                    { k: "tools", name: t("llm.capTools") },
                  ].map((c) => {
                    const on = capSelected.includes(c.k);
                    return (
                      <button key={c.k} data-slot="cap-chip" type="button"
                        className={cn(
                          "inline-flex items-center justify-center h-8 px-3.5 rounded-md border text-sm font-medium cursor-pointer transition-colors",
                          on
                            ? "border-primary bg-primary text-primary-foreground shadow-sm hover:bg-primary/90"
                            : "border-input bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                        )}
                        onClick={() => { setCapTouched(true); setCapSelected((prev) => { const set = new Set(prev); if (set.has(c.k)) set.delete(c.k); else set.add(c.k); const out = Array.from(set); return out.length ? out : ["text"]; }); }}
                      >{c.name}</button>
                    );
                  })}
                </div>
              </div>

              {/* Advanced (collapsed) */}
              <details className="group rounded-lg border border-border">
                <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium text-muted-foreground select-none list-none [&::-webkit-details-marker]:hidden hover:text-foreground transition-colors">
                  <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open:rotate-180" />
                  {t("llm.advancedParams") || t("llm.advanced") || "高级参数"}
                </summary>
                <div className="border-t border-border px-4 py-3 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label>{t("llm.advApiType")}</Label>
                      <Select value={apiType} onValueChange={(v) => setApiType(v as any)}>
                        <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="openai">openai</SelectItem>
                          <SelectItem value="openai_responses">openai_responses</SelectItem>
                          <SelectItem value="anthropic">anthropic</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label>{t("llm.advPriority")}</Label>
                      <Input type="number" value={String(endpointPriority)} onChange={(e) => setEndpointPriority(Number(e.target.value))} />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advKeyEnv")}</Label>
                    <Input value={apiKeyEnv} onChange={(e) => { setApiKeyEnvTouched(true); setApiKeyEnv(e.target.value); }} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advMaxTokens")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advMaxTokensHint")}</span></Label>
                    <Input type="number" min={0} value={addEpMaxTokens} onChange={(e) => setAddEpMaxTokens(Math.max(0, parseInt(e.target.value) || 0))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advContextWindow")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advContextWindowHint")}</span></Label>
                    <Input type="number" min={1024} value={addEpContextWindow} onChange={(e) => setAddEpContextWindow(Math.max(1024, parseInt(e.target.value) || 200000))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advTimeout")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advTimeoutHint")}</span></Label>
                    <Input type="number" min={10} value={addEpTimeout} onChange={(e) => setAddEpTimeout(Math.max(10, parseInt(e.target.value) || 180))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advRpmLimit")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advRpmLimitHint")}</span></Label>
                    <Input type="number" min={0} value={addEpRpmLimit} onChange={(e) => setAddEpRpmLimit(Math.max(0, parseInt(e.target.value) || 0))} />
                  </div>
                </div>
              </details>
            </div>

            {connTestResult && (
              <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
                connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
              )}>
                {connTestResult.ok
                  ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                  : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
              </div>
            )}

            <DialogFooter className="px-6 py-2.5 shrink-0 flex-col sm:flex-col gap-1.5">
              <div className="flex items-center justify-between w-full">
                <Button variant="ghost" onClick={() => setAddEpDialogOpen(false)}>{t("common.cancel")}</Button>
                <div className="flex gap-2 items-center">
                  <Button variant="secondary"
                    disabled={(!apiKeyValue.trim() && !isLocalProvider(selectedProvider)) || !baseUrl.trim() || connTesting}
                    onClick={() => doTestConnection({ testApiType: apiType, testBaseUrl: baseUrl, testApiKey: apiKeyValue.trim() || (isLocalProvider(selectedProvider) ? localProviderPlaceholderKey(selectedProvider) : ""), testProviderSlug: selectedProvider?.slug })}
                  >
                    {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                  </Button>
                  {(() => {
                    const _isLocal = isLocalProvider(selectedProvider);
                    const missing: string[] = [];
                    if (!baseUrl.trim()) missing.push("Base URL");
                    if (!_isLocal && !apiKeyValue.trim()) missing.push("API Key");
                    if (!selectedModelId.trim()) missing.push(t("status.model"));
                    if (!currentWorkspaceId && dataMode !== "remote") missing.push(t("workspace.title") || "工作区");
                    const btnDisabled = missing.length > 0 || !!busy;
                    return (
                      <Button onClick={async () => { const ok = await doSaveEndpoint(); if (ok) { setAddEpDialogOpen(false); setConnTestResult(null); } }} disabled={btnDisabled}>
                        {isEditingEndpoint ? t("common.save") : t("llm.addEndpoint")}
                      </Button>
                    );
                  })()}
                </div>
              </div>
              {(() => {
                const _isLocal = isLocalProvider(selectedProvider);
                const missing: string[] = [];
                if (!baseUrl.trim()) missing.push("Base URL");
                if (!_isLocal && !apiKeyValue.trim()) missing.push("API Key");
                if (!selectedModelId.trim()) missing.push(t("status.model"));
                if (!currentWorkspaceId && dataMode !== "remote") missing.push(t("workspace.title") || "工作区");
                const show = missing.length > 0 && !busy;
                return (
                  <div className={cn("text-[10px] text-muted-foreground text-right w-full", !show && "invisible")}>{t("common.missingFields") || "缺少"}: {missing.join(", ") || "—"}</div>
                );
              })()}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* ── Edit endpoint modal ── */}
        <Dialog open={editModalOpen && !!editDraft} onOpenChange={(open) => { if (!open) setEditModalOpen(false); }}>
          <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { resetEndpointEditor(); setConnTestResult(null); }}>
            <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
              <DialogTitle>{t("llm.editEndpoint")}: {editDraft?.name}</DialogTitle>
              <DialogDescription className="sr-only">{t("llm.editEndpoint")}</DialogDescription>
            </DialogHeader>

            {editDraft && <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
              {/* Provider (read-only) */}
              <div className="space-y-1.5">
                <Label className="flex items-center gap-1 flex-wrap">{t("llm.provider")} <span className="text-[11px] font-normal text-muted-foreground/70">服务商在创建时确定，不可更改</span> {!["custom", "ollama", "lmstudio"].includes(editDraft.providerSlug) && <span className="inline-flex items-center gap-0.5 text-[11px] font-normal text-muted-foreground/70 min-w-0"><span className="shrink-0">API 地址：</span><span className="inline-block max-w-[200px] overflow-x-auto whitespace-nowrap align-middle" style={{ scrollbarWidth: "thin" }}>{editDraft.baseUrl || "—"}</span> <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] shrink-0" onClick={() => setEditBaseUrlExpanded(v => !v)}>{editBaseUrlExpanded ? "收起" : "配置"}</Button></span>}</Label>
                <Input value={(() => { const p = providers.find((x) => x.slug === editDraft.providerSlug); return p ? p.name : (editDraft.providerSlug || "custom"); })()} disabled className="opacity-70" />
              </div>

              {/* Base URL */}
              {["custom", "ollama", "lmstudio"].includes(editDraft.providerSlug) ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={editDraft.baseUrl || ""} onChange={(e) => setEditDraft({ ...editDraft, baseUrl: e.target.value })} placeholder="请输入" />
              </div>
              ) : editBaseUrlExpanded ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={editDraft.baseUrl || ""} onChange={(e) => setEditDraft({ ...editDraft, baseUrl: e.target.value })} placeholder="请输入" />
              </div>
              ) : null}

              {/* API Key */}
              <div className="space-y-1.5">
                <Label className="inline-flex items-center gap-2">
                  API Key {isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                  {(() => { const url = getProviderApplyUrl(editDraft.providerSlug); const ep = providers.find((p) => p.slug === editDraft.providerSlug); return url && !isLocalProvider(ep) ? <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(url)}>获取 API Key</Button> : null; })()}
                </Label>
                <div className="relative">
                  <Input value={editDraft.apiKeyValue} onChange={(e) => { setEditDraft((d) => d ? { ...d, apiKeyValue: e.target.value } : d); }} type={(secretShown.__EDIT_EP_KEY && !IS_WEB) ? "text" : "password"} className="pr-11" placeholder={isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) ? t("llm.localKeyPlaceholder") : "输入调用大模型的 API Key"} />
                  {!IS_WEB && <Button type="button" variant="ghost" size="icon-xs" className="absolute right-1.5 top-1/2 -translate-y-1/2" onClick={() => setSecretShown((m) => ({ ...m, __EDIT_EP_KEY: !m.__EDIT_EP_KEY }))} title={secretShown.__EDIT_EP_KEY ? "隐藏" : "显示"}>
                    {secretShown.__EDIT_EP_KEY ? <IconEyeOff size={14} /> : <IconEye size={14} />}
                  </Button>}
                </div>
                {isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
              </div>

              {/* Model */}
              <div className="space-y-1.5">
                <Label>{t("status.model")} <span className="text-[11px] font-normal text-muted-foreground/70">自行输入或<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchEditModels} disabled={(!isLocalProvider(providers.find((p) => p.slug === editDraft.providerSlug)) && !(editDraft.apiKeyValue || "").trim()) || !(editDraft.baseUrl || "").trim() || !!busy}>拉取模型列表</Button>并选择{editModels.length > 0 && <span className="text-muted-foreground/50">（已拉取 {editModels.length} 个）</span>}</span></Label>
                <SearchSelect
                  value={editDraft.modelId || ""}
                  onChange={(v) => setEditDraft({ ...editDraft, modelId: v })}
                  options={editModels.length > 0 ? editModels.map(m => m.id) : [editDraft.modelId || ""].filter(Boolean)}
                  placeholder={editModels.length > 0 ? t("llm.searchModel") : (editDraft.modelId || t("llm.modelPlaceholder"))}
                  disabled={!!busy}
                />
              </div>

              {/* Capabilities */}
              <div className="space-y-1.5">
                <Label>{t("llm.capabilities")}</Label>
                <div className="flex flex-wrap gap-2">
                  {[
                    { k: "text", name: t("llm.capText") },
                    { k: "thinking", name: t("llm.capThinking") },
                    { k: "vision", name: t("llm.capVision") },
                    { k: "video", name: t("llm.capVideo") },
                    { k: "tools", name: t("llm.capTools") },
                  ].map((c) => {
                    const on = (editDraft.caps || []).includes(c.k);
                    return (
                      <button key={c.k} data-slot="cap-chip" type="button"
                        className={cn(
                          "inline-flex items-center justify-center h-8 px-3.5 rounded-md border text-sm font-medium cursor-pointer transition-colors",
                          on
                            ? "border-primary bg-primary text-primary-foreground shadow-sm hover:bg-primary/90"
                            : "border-input bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                        )}
                        onClick={() => setEditDraft((d) => {
                          if (!d) return d;
                          const set = new Set(d.caps || []);
                          if (set.has(c.k)) set.delete(c.k); else set.add(c.k);
                          const out = Array.from(set);
                          return { ...d, caps: out.length ? out : ["text"] };
                        })}
                      >{c.name}</button>
                    );
                  })}
                </div>
              </div>

              {/* Advanced (collapsed) */}
              <details className="group rounded-lg border border-border">
                <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium text-muted-foreground select-none list-none [&::-webkit-details-marker]:hidden hover:text-foreground transition-colors">
                  <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open:rotate-180" />
                  {t("llm.advancedParams") || t("llm.advanced") || "高级参数"}
                </summary>
                <div className="border-t border-border px-4 py-3 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label>{t("llm.advApiType")}</Label>
                      <Select value={editDraft.apiType} onValueChange={(v) => setEditDraft({ ...editDraft, apiType: v as any })}>
                        <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="openai">openai</SelectItem>
                          <SelectItem value="openai_responses">openai_responses</SelectItem>
                          <SelectItem value="anthropic">anthropic</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label>{t("llm.advPriority")}</Label>
                      <Input type="number" value={editDraft.priority} onChange={(e) => setEditDraft({ ...editDraft, priority: Number(e.target.value) || 1 })} />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advKeyEnv")}</Label>
                    <Input value={editDraft.apiKeyEnv} onChange={(e) => setEditDraft({ ...editDraft, apiKeyEnv: e.target.value })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advMaxTokens")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advMaxTokensHint")}</span></Label>
                    <Input type="number" min={0} value={editDraft.maxTokens} onChange={(e) => setEditDraft({ ...editDraft, maxTokens: Math.max(0, parseInt(e.target.value) || 0) })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advContextWindow")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advContextWindowHint")}</span></Label>
                    <Input type="number" min={1024} value={editDraft.contextWindow} onChange={(e) => setEditDraft({ ...editDraft, contextWindow: Math.max(1024, parseInt(e.target.value) || 200000) })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advTimeout")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advTimeoutHint")}</span></Label>
                    <Input type="number" min={10} value={editDraft.timeout} onChange={(e) => setEditDraft({ ...editDraft, timeout: Math.max(10, parseInt(e.target.value) || 180) })} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>{t("llm.advRpmLimit")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.advRpmLimitHint")}</span></Label>
                    <Input type="number" min={0} value={editDraft.rpmLimit} onChange={(e) => setEditDraft({ ...editDraft, rpmLimit: Math.max(0, parseInt(e.target.value) || 0) })} />
                  </div>
                </div>
              </details>

              {/* 阶梯定价配置 */}
              <details className="group rounded-lg border border-border">
                <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium text-muted-foreground select-none list-none [&::-webkit-details-marker]:hidden hover:text-foreground transition-colors">
                  <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open:rotate-180" />
                  定价配置 <span className="text-[11px] font-normal text-muted-foreground/70">（可选，用于费用估算）</span>
                </summary>
                <div className="border-t border-border px-4 py-3 space-y-2.5">
                  {(editDraft.pricingTiers || []).length > 0 && (
                    <div className="grid grid-cols-[1fr_1fr_1fr_28px] gap-1.5 text-[11px] text-muted-foreground">
                      <span>最大输入 tokens</span>
                      <span>输入价格/M</span>
                      <span>输出价格/M</span>
                      <span />
                    </div>
                  )}
                  {(editDraft.pricingTiers || []).map((tier, idx) => (
                    <div key={idx} className="grid grid-cols-[1fr_1fr_1fr_28px] gap-1.5 items-center">
                      <Input type="number" min={0} placeholder="128000" value={tier.max_input || ""} onChange={(e) => {
                        const tiers = [...(editDraft.pricingTiers || [])];
                        tiers[idx] = { ...tiers[idx], max_input: parseInt(e.target.value) || 0 };
                        setEditDraft({ ...editDraft, pricingTiers: tiers });
                      }} className="h-8 text-xs" />
                      <Input type="number" min={0} step={0.01} placeholder="1.2" value={tier.input_price || ""} onChange={(e) => {
                        const tiers = [...(editDraft.pricingTiers || [])];
                        tiers[idx] = { ...tiers[idx], input_price: parseFloat(e.target.value) || 0 };
                        setEditDraft({ ...editDraft, pricingTiers: tiers });
                      }} className="h-8 text-xs" />
                      <Input type="number" min={0} step={0.01} placeholder="7.2" value={tier.output_price || ""} onChange={(e) => {
                        const tiers = [...(editDraft.pricingTiers || [])];
                        tiers[idx] = { ...tiers[idx], output_price: parseFloat(e.target.value) || 0 };
                        setEditDraft({ ...editDraft, pricingTiers: tiers });
                      }} className="h-8 text-xs" />
                      <Button data-slot="pricing-btn" variant="ghost" size="icon-xs" className="text-muted-foreground/50 hover:text-destructive" onClick={() => {
                        const tiers = (editDraft.pricingTiers || []).filter((_, i) => i !== idx);
                        setEditDraft({ ...editDraft, pricingTiers: tiers });
                      }}><XIcon className="size-3.5" /></Button>
                    </div>
                  ))}
                  <Button data-slot="pricing-btn" variant="outline" size="sm" className="w-full border-dashed text-muted-foreground text-xs" onClick={() => {
                    const tiers = [...(editDraft.pricingTiers || []), { max_input: 0, input_price: 0, output_price: 0 }];
                    setEditDraft({ ...editDraft, pricingTiers: tiers });
                  }}>
                    + 添加档位
                  </Button>
                </div>
              </details>
            </div>}

            {connTestResult && (
              <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
                connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
              )}>
                {connTestResult.ok
                  ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                  : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
              </div>
            )}

            <DialogFooter className="px-6 py-2.5 shrink-0 flex-row justify-between sm:justify-between">
              <Button variant="ghost" onClick={() => setEditModalOpen(false)}>{t("common.cancel")}</Button>
              <div className="flex gap-2 items-center">
                <Button variant="secondary"
                  disabled={(!isLocalProvider(providers.find((p) => p.slug === editDraft?.providerSlug)) && !(editDraft?.apiKeyValue || "").trim()) || !(editDraft?.baseUrl || "").trim() || connTesting}
                  onClick={() => { const _ep = providers.find((p) => p.slug === editDraft?.providerSlug); doTestConnection({
                    testApiType: editDraft?.apiType || "openai",
                    testBaseUrl: editDraft?.baseUrl || "",
                    testApiKey: (editDraft?.apiKeyValue || "").trim() || (isLocalProvider(_ep) ? localProviderPlaceholderKey(_ep) : ""),
                    testProviderSlug: editDraft?.providerSlug,
                  }); }}
                >
                  {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                </Button>
                <Button onClick={async () => { await doSaveEditedEndpoint(); }} disabled={!!busy}>{t("common.save")}</Button>
              </div>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* ── Add compiler dialog ── */}
        <Dialog open={addCompDialogOpen} onOpenChange={(open) => { if (!open) setAddCompDialogOpen(false); }}>
          <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { setConnTestResult(null); }}>
            <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
              <DialogTitle>{t("llm.addCompiler")}</DialogTitle>
              <DialogDescription className="sr-only">{t("llm.addCompiler")}</DialogDescription>
            </DialogHeader>

            <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
              {/* Provider */}
              <div className="space-y-1.5">
                <Label>{t("llm.provider")} {!["custom", "ollama", "lmstudio"].includes(compilerProviderSlug) && <span className="text-[11px] font-normal text-muted-foreground/70">API 地址：{compilerBaseUrl || "—"} <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => setCompBaseUrlExpanded(v => !v)}>{compBaseUrlExpanded ? "收起" : "配置"}</Button></span>}</Label>
                <ProviderSearchSelect
                  value={compilerProviderSlug}
                  onChange={(slug) => {
                    setCompilerProviderSlug(slug);
                    setCompBaseUrlExpanded(false);
                    setCompilerCodingPlan(false);
                    if (slug === "custom") {
                      setCompilerApiType("openai");
                      setCompilerBaseUrl("");
                      setCompilerApiKeyEnv("CUSTOM_COMPILER_API_KEY");
                      setCompilerApiKeyValue("");
                    } else {
                      const p = providers.find((x) => x.slug === slug);
                      if (p) {
                        setCompilerApiType((p.api_type as any) || "openai");
                        setCompilerBaseUrl(p.default_base_url || "");
                        const suggested = p.api_key_env_suggestion || envKeyFromSlug(p.slug);
                        const used = new Set(Object.keys(envDraft || {}));
                        for (const ep of [...savedEndpoints, ...savedCompilerEndpoints]) { if (ep.api_key_env) used.add(ep.api_key_env); }
                        setCompilerApiKeyEnv(nextEnvKeyName(suggested, used));
                        if (isLocalProvider(p)) {
                          setCompilerApiKeyValue(localProviderPlaceholderKey(p));
                        } else {
                          setCompilerApiKeyValue("");
                        }
                      }
                    }
                  }}
                  options={providers.map((p) => ({ value: p.slug, label: p.name }))}
                />
              </div>

              {/* Coding Plan toggle */}
              {(() => { const cp = providers.find((x) => x.slug === compilerProviderSlug); return cp?.coding_plan_base_url ? (
                <label htmlFor="coding-plan-comp" className="flex items-center justify-between gap-3 rounded-lg border border-border px-4 py-3 cursor-pointer select-none hover:bg-accent/50 transition-colors">
                  <div className="space-y-0.5">
                    <div className="text-sm font-medium">{t("llm.codingPlan")}</div>
                    <div className="text-xs text-muted-foreground">{t("llm.codingPlanHint")}</div>
                  </div>
                  <Switch id="coding-plan-comp" checked={compilerCodingPlan} onCheckedChange={(v) => {
                    setCompilerCodingPlan(v);
                    if (cp) {
                      if (v && cp.coding_plan_base_url) {
                        setCompilerBaseUrl(cp.coding_plan_base_url);
                        setCompilerApiType("anthropic");
                      } else {
                        setCompilerBaseUrl(cp.default_base_url || "");
                        setCompilerApiType((cp.api_type as "openai" | "anthropic") || "openai");
                      }
                    }
                  }} />
                </label>
              ) : null; })()}

              {/* Base URL */}
              {["custom", "ollama", "lmstudio"].includes(compilerProviderSlug) ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={compilerBaseUrl} onChange={(e) => setCompilerBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
              </div>
              ) : compBaseUrlExpanded ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={compilerBaseUrl} onChange={(e) => setCompilerBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
              </div>
              ) : null}

              {/* API Key Env */}
              <div className="space-y-1.5">
                <Label>{t("llm.apiKeyEnv")}</Label>
                <Input value={compilerApiKeyEnv} onChange={(e) => setCompilerApiKeyEnv(e.target.value)} placeholder="MY_API_KEY" />
              </div>

              {/* API Key */}
              <div className="space-y-1.5">
                <Label className="inline-flex items-center gap-2">
                  API Key {isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug)) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                  {(() => { const url = getProviderApplyUrl(compilerProviderSlug); const cp = providers.find((p) => p.slug === compilerProviderSlug); return url && !isLocalProvider(cp) ? <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(url)}>获取 API Key</Button> : null; })()}
                </Label>
                <Input value={compilerApiKeyValue} onChange={(e) => setCompilerApiKeyValue(e.target.value)} placeholder={isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug)) ? t("llm.localKeyPlaceholder") : "输入调用大模型的 API Key"} type="password" />
                {isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug)) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
              </div>

              {/* Model */}
              <div className="space-y-1.5">
                <Label>{t("status.model")} <span className="text-[11px] font-normal text-muted-foreground/70">自行输入或<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchCompilerModels} disabled={(!compilerApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug))) || !compilerBaseUrl.trim() || !!busy}>拉取模型列表</Button>并选择{compilerModels.length > 0 && <span className="text-muted-foreground/50">（已拉取 {compilerModels.length} 个）</span>}</span></Label>
                <SearchSelect value={compilerModel} onChange={(v) => setCompilerModel(v)} options={compilerModels.map((m) => m.id)} placeholder={compilerModels.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")} disabled={!!busy} />
              </div>

              {/* Endpoint Name */}
              <div className="space-y-1.5">
                <Label>{t("llm.endpointName")} <span className="text-[11px] font-normal text-muted-foreground/70">({t("common.optional")})</span></Label>
                <Input value={compilerEndpointName} onChange={(e) => setCompilerEndpointName(e.target.value)} placeholder={`compiler-${compilerProviderSlug || "custom"}-${compilerModel || "model"}`} />
              </div>
            </div>

            {connTestResult && (
              <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
                connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
              )}>
                {connTestResult.ok
                  ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                  : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
              </div>
            )}

            <DialogFooter className="px-6 py-2.5 shrink-0 flex-col sm:flex-col gap-1.5">
              <div className="flex items-center justify-between w-full">
                <Button variant="ghost" onClick={() => setAddCompDialogOpen(false)}>{t("common.cancel")}</Button>
                <div className="flex gap-2 items-center">
                  <Button variant="secondary"
                    disabled={(!compilerApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug))) || !compilerBaseUrl.trim() || connTesting}
                    onClick={() => { const _cp = providers.find((p) => p.slug === compilerProviderSlug); doTestConnection({
                      testApiType: compilerApiType,
                      testBaseUrl: compilerBaseUrl,
                      testApiKey: compilerApiKeyValue.trim() || (isLocalProvider(_cp) ? localProviderPlaceholderKey(_cp) : ""),
                      testProviderSlug: compilerProviderSlug || null,
                    }); }}
                  >
                    {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                  </Button>
                  {(() => {
                    const _isCompLocal = isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug));
                    const cMissing: string[] = [];
                    if (!compilerModel.trim()) cMissing.push(t("status.model"));
                    if (!_isCompLocal && !compilerApiKeyEnv.trim()) cMissing.push("Key Env Name");
                    if (!_isCompLocal && !compilerApiKeyValue.trim()) cMissing.push("API Key");
                    if (!currentWorkspaceId && dataMode !== "remote") cMissing.push(t("workspace.title") || "工作区");
                    const cBtnDisabled = cMissing.length > 0 || !!busy;
                    return (
                      <Button onClick={async () => { const ok = await doSaveCompilerEndpoint(); if (ok) { setAddCompDialogOpen(false); setConnTestResult(null); } }} disabled={cBtnDisabled}>
                        {t("llm.addEndpoint")}
                      </Button>
                    );
                  })()}
                </div>
              </div>
              {(() => {
                const _isCompLocal = isLocalProvider(providers.find((p) => p.slug === compilerProviderSlug));
                const cMissing: string[] = [];
                if (!compilerModel.trim()) cMissing.push(t("status.model"));
                if (!_isCompLocal && !compilerApiKeyEnv.trim()) cMissing.push("Key Env Name");
                if (!_isCompLocal && !compilerApiKeyValue.trim()) cMissing.push("API Key");
                if (!currentWorkspaceId && dataMode !== "remote") cMissing.push(t("workspace.title") || "工作区");
                const cShow = cMissing.length > 0 && !busy;
                return (
                  <div className={cn("text-[10px] text-muted-foreground text-right w-full", !cShow && "invisible")}>{t("common.missingFields") || "缺少"}: {cMissing.join(", ") || "—"}</div>
                );
              })()}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* ── Add STT dialog ── */}
        <Dialog open={addSttDialogOpen} onOpenChange={(open) => { if (!open) setAddSttDialogOpen(false); }}>
          <DialogContent className="sm:max-w-[480px] max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()} onCloseAnimationEnd={() => { setConnTestResult(null); }}>
            <DialogHeader className="px-6 pt-5 pb-3 shrink-0">
              <DialogTitle>{t("llm.addStt")}</DialogTitle>
              <DialogDescription className="sr-only">{t("llm.addStt")}</DialogDescription>
            </DialogHeader>

            <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4 space-y-4" style={{ scrollbarGutter: "stable" }}>
              {/* Provider */}
              <div className="space-y-1.5">
                <Label>{t("llm.provider")} {!["custom", "ollama", "lmstudio"].includes(sttProviderSlug) && <span className="text-[11px] font-normal text-muted-foreground/70">API 地址：{sttBaseUrl || "—"} <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => setSttBaseUrlExpanded(v => !v)}>{sttBaseUrlExpanded ? "收起" : "配置"}</Button></span>}</Label>
                <ProviderSearchSelect
                  value={sttProviderSlug}
                  onChange={(slug) => {
                    setSttBaseUrlExpanded(false);
                    setSttProviderSlug(slug);
                    if (slug === "custom") {
                      setSttApiType("openai");
                      setSttBaseUrl("");
                      setSttApiKeyEnv("CUSTOM_STT_API_KEY");
                      setSttApiKeyValue("");
                      setSttModels([]);
                      setSttModel("");
                    } else {
                      const p = providers.find((x) => x.slug === slug);
                      if (p) {
                        setSttApiType((p.api_type as any) || "openai");
                        setSttBaseUrl(p.default_base_url || "");
                        const suggested = p.api_key_env_suggestion || envKeyFromSlug(p.slug);
                        const used = new Set(Object.keys(envDraft || {}));
                        for (const ep of [...savedEndpoints, ...savedCompilerEndpoints, ...savedSttEndpoints]) { if (ep.api_key_env) used.add(ep.api_key_env); }
                        setSttApiKeyEnv(nextEnvKeyName(suggested, used));
                        if (isLocalProvider(p)) {
                          setSttApiKeyValue(localProviderPlaceholderKey(p));
                        } else {
                          setSttApiKeyValue("");
                        }
                      }
                      const rec = STT_RECOMMENDED_MODELS[slug];
                      if (rec?.length) {
                        setSttModels(rec.map((m) => ({ id: m.id, name: m.id, capabilities: {} })));
                        setSttModel(rec[0].id);
                      } else {
                        setSttModels([]);
                        setSttModel("");
                      }
                    }
                  }}
                  options={providers.map((p) => ({ value: p.slug, label: p.name }))}
                />
              </div>

              {/* Base URL */}
              {["custom", "ollama", "lmstudio"].includes(sttProviderSlug) ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={sttBaseUrl} onChange={(e) => setSttBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
              </div>
              ) : sttBaseUrlExpanded ? (
              <div className="space-y-1.5">
                <Label>{t("llm.baseUrl")} <span className="text-[11px] font-normal text-muted-foreground/70">{t("llm.baseUrlHint")}</span></Label>
                <Input value={sttBaseUrl} onChange={(e) => setSttBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
              </div>
              ) : null}

              {/* API Key Env */}
              <div className="space-y-1.5">
                <Label>{t("llm.apiKeyEnv")}</Label>
                <Input value={sttApiKeyEnv} onChange={(e) => setSttApiKeyEnv(e.target.value)} placeholder="MY_API_KEY" />
              </div>

              {/* API Key */}
              <div className="space-y-1.5">
                <Label className="inline-flex items-center gap-2">
                  API Key {isLocalProvider(providers.find((p) => p.slug === sttProviderSlug)) && <span className="text-muted-foreground text-[11px] font-normal">({t("llm.localNoKey")})</span>}
                  {(() => { const url = getProviderApplyUrl(sttProviderSlug); const sp = providers.find((p) => p.slug === sttProviderSlug); return url && !isLocalProvider(sp) ? <Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px]" onClick={() => openApplyUrl(url)}>获取 API Key</Button> : null; })()}
                </Label>
                <Input value={sttApiKeyValue} onChange={(e) => setSttApiKeyValue(e.target.value)} placeholder={isLocalProvider(providers.find((p) => p.slug === sttProviderSlug)) ? t("llm.localKeyPlaceholder") : "输入调用大模型的 API Key"} type="password" />
                {isLocalProvider(providers.find((p) => p.slug === sttProviderSlug)) && <p className="text-xs text-primary">{t("llm.localHint")}</p>}
              </div>

              {/* Model */}
              <div className="space-y-1.5">
                <Label>{t("status.model")} <span className="text-[11px] font-normal text-muted-foreground/70">自行输入或<Button type="button" variant="link" size="xs" className="h-auto p-0 text-[11px] disabled:opacity-100 disabled:pointer-events-auto disabled:cursor-default" onClick={doFetchSttModels} disabled={(!sttApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === sttProviderSlug))) || !sttBaseUrl.trim() || !!busy}>拉取模型列表</Button>并选择{sttModels.length > 0 && <span className="text-muted-foreground/50">（已拉取 {sttModels.length} 个）</span>}</span></Label>
                <SearchSelect value={sttModel} onChange={(v) => setSttModel(v)} options={sttModels.map((m) => m.id)} placeholder={sttModels.length > 0 ? t("llm.searchModel") : t("llm.modelPlaceholder")} disabled={!!busy} />
                {(() => {
                  const rec = STT_RECOMMENDED_MODELS[sttProviderSlug];
                  if (!rec?.length) return null;
                  return (
                    <div className="mt-1 text-xs text-muted-foreground/70 leading-relaxed">
                      {rec.map((m) => (
                        <span key={m.id} className="mr-3">
                          <code className="bg-muted/50 px-1.5 py-0.5 rounded cursor-pointer hover:bg-muted transition-colors" onClick={() => setSttModel(m.id)}>{m.id}</code>
                          {m.note && <span className="ml-1 text-primary">{m.note}</span>}
                        </span>
                      ))}
                    </div>
                  );
                })()}
              </div>

              {/* Endpoint Name */}
              <div className="space-y-1.5">
                <Label>{t("llm.endpointName")} <span className="text-[11px] font-normal text-muted-foreground/70">({t("common.optional")})</span></Label>
                <Input value={sttEndpointName} onChange={(e) => setSttEndpointName(e.target.value)} placeholder={`stt-${sttProviderSlug || "custom"}-${sttModel || "model"}`} />
              </div>
            </div>

            {connTestResult && (
              <div className={cn("mx-6 px-3 py-2 rounded-lg text-xs leading-relaxed shrink-0",
                connTestResult.ok ? "bg-emerald-500/8 border border-emerald-500/25 text-emerald-600" : "bg-red-500/6 border border-red-500/20 text-red-600"
              )}>
                {connTestResult.ok
                  ? `${t("llm.testSuccess")} · ${connTestResult.latencyMs}ms · ${t("llm.testModelCount", { count: connTestResult.modelCount ?? 0 })}`
                  : `${t("llm.testFailed")}：${connTestResult.error} (${connTestResult.latencyMs}ms)`}
              </div>
            )}

            <DialogFooter className="px-6 py-2.5 shrink-0 flex-col sm:flex-col gap-1.5">
              <div className="flex items-center justify-between w-full">
                <Button variant="ghost" onClick={() => setAddSttDialogOpen(false)}>{t("common.cancel")}</Button>
                <div className="flex gap-2 items-center">
                  <Button variant="secondary"
                    disabled={(!sttApiKeyValue.trim() && !isLocalProvider(providers.find((p) => p.slug === sttProviderSlug))) || !sttBaseUrl.trim() || connTesting}
                    onClick={() => { const _sp = providers.find((p) => p.slug === sttProviderSlug); doTestConnection({
                      testApiType: sttApiType,
                      testBaseUrl: sttBaseUrl,
                      testApiKey: sttApiKeyValue.trim() || (isLocalProvider(_sp) ? localProviderPlaceholderKey(_sp) : ""),
                      testProviderSlug: sttProviderSlug || null,
                    }); }}
                  >
                    {connTesting ? t("llm.testTesting") : t("llm.testConnection")}
                  </Button>
                  {(() => {
                    const _isSttLocal = isLocalProvider(providers.find((p) => p.slug === sttProviderSlug));
                    const sMissing: string[] = [];
                    if (!sttModel.trim()) sMissing.push(t("status.model"));
                    if (!_isSttLocal && !sttApiKeyValue.trim()) sMissing.push("API Key");
                    if (!currentWorkspaceId && dataMode !== "remote") sMissing.push(t("workspace.title") || "工作区");
                    const sBtnDisabled = sMissing.length > 0 || !!busy;
                    return (
                      <Button onClick={async () => { const ok = await doSaveSttEndpoint(); if (ok) { setAddSttDialogOpen(false); setConnTestResult(null); } }} disabled={sBtnDisabled}>
                        {t("llm.addStt")}
                      </Button>
                    );
                  })()}
                </div>
              </div>
              {(() => {
                const _isSttLocal = isLocalProvider(providers.find((p) => p.slug === sttProviderSlug));
                const sMissing: string[] = [];
                if (!sttModel.trim()) sMissing.push(t("status.model"));
                if (!_isSttLocal && !sttApiKeyValue.trim()) sMissing.push("API Key");
                if (!currentWorkspaceId && dataMode !== "remote") sMissing.push(t("workspace.title") || "工作区");
                const sShow = sMissing.length > 0 && !busy;
                return (
                  <div className={cn("text-[10px] text-muted-foreground text-right w-full", !sShow && "invisible")}>{t("common.missingFields") || "缺少"}: {sMissing.join(", ") || "—"}</div>
                );
              })()}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
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
      await saveEnvKeys(keys);
      notifySuccess(successText);
    } finally {
      dismissLoading(_busyId);
    }
  }

  const _configViewProps = {
    envDraft, setEnvDraft,
    currentWorkspaceId,
    disabledViews, toggleViewDisabled,
  };

  function renderIM(opts?: { onboarding?: boolean }) {
    const imDisabled = disabledViews.includes("im");
    return (
      <IMConfigView
        {..._configViewProps}
        venvDir={venvDir}
        imDisabled={imDisabled}
        onToggleIM={opts?.onboarding ? undefined : () => toggleViewDisabled("im")}
        apiBaseUrl={httpApiBase()}
        onNavigateToBotConfig={opts?.onboarding ? undefined : (presetType) => { setView("im"); }}
        {...(opts?.onboarding ? { pendingBots: obPendingBots, onPendingBotsChange: setObPendingBots } : {})}
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
                <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open:rotate-180 text-muted-foreground" />
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
                <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open/skills:rotate-180 text-muted-foreground" />
                Skills 技能集成
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
                启用全部
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
                禁用全部
              </button>
              <span className="text-xs text-muted-foreground ml-auto">
                {skillsDetail ? `${Object.values(skillsSelection).filter(Boolean).length} / ${skillsDetail.length} 已启用` : ""}
              </span>
            </div>
          </details>

          {/* ── Desktop Automation ── */}
          <details className="group/desktop rounded-lg border border-border mt-2">
            <summary className="cursor-pointer flex items-center justify-between px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <span className="flex items-center gap-1.5">
                <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open/desktop:rotate-180 text-muted-foreground" />
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
                  <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open/deskadv:rotate-180" />
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

          {/* ── Network & Proxy ── */}
          <details className="group/net rounded-lg border border-border mt-2">
            <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open/net:rotate-180 text-muted-foreground" />
              {t("config.toolsNetwork")}
            </summary>
            <div className="flex flex-col gap-2.5 px-4 py-3 border-t border-border">
              <div className="grid3">
                {FT({ k: "HTTP_PROXY", label: "HTTP_PROXY", placeholder: "http://127.0.0.1:7890" })}
                {FT({ k: "HTTPS_PROXY", label: "HTTPS_PROXY", placeholder: "http://127.0.0.1:7890" })}
                {FT({ k: "ALL_PROXY", label: "ALL_PROXY", placeholder: "socks5://..." })}
              </div>
              <div className="grid2">
                {FB({ k: "FORCE_IPV4", label: t("config.toolsForceIPv4"), help: t("config.toolsForceIPv4Help") })}
                {FT({ k: "TOOL_MAX_PARALLEL", label: t("config.toolsParallel"), placeholder: "1", help: t("config.toolsParallelHelp") })}
              </div>
            </div>
          </details>

          {/* ── Hallucination Guard ── */}
          <details className="group/hguard rounded-lg border border-border mt-2">
            <summary className="cursor-pointer flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium select-none list-none [&::-webkit-details-marker]:hidden hover:bg-accent/50 transition-colors">
              <ChevronDownIcon className="size-4 shrink-0 transition-transform group-open/hguard:rotate-180 text-muted-foreground" />
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
          <h3 className="text-base font-bold tracking-tight">CLI 命令行工具</h3>
          <p className="text-sm text-muted-foreground mt-1 mb-3">管理终端命令注册，注册后可在 CMD / PowerShell / 终端中直接使用 oa 或 openakita 命令。</p>
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

    async function fetchSystemInfo() {
      const url = shouldUseHttpApi() ? httpApiBase() : null;
      if (!url) { notifyError(t("adv.needService")); return; }
      try {
        const res = await safeFetch(`${url}/api/system-info`, { signal: AbortSignal.timeout(8_000) });
        const data = await res.json();
        const info: Record<string, string> = {};
        if (data.os) info[t("adv.sysOs")] = data.os;
        if (data.openakita_version) info[t("adv.sysVersion")] = data.openakita_version;
        setAdvSysInfo(info);
      } catch (e) { notifyError(String(e)); }
    }

    // ── 系统运维区域 ──

    const opsWs = workspaces.find(w => w.id === (currentWorkspaceId || "default"));
    const opsWsPath = opsWs?.path || "";
    const opsLogsPath = opsWsPath ? joinPath(opsWsPath, "logs") : "";
    const opsIdentityPath = opsWsPath ? joinPath(opsWsPath, "identity") : "";

    const opsPathRows: { label: string; path: string }[] = [
      { label: t("adv.opsWorkspacePath"), path: opsWsPath },
      { label: t("adv.opsLogsPath"), path: opsLogsPath },
      { label: t("adv.opsIdentityPath"), path: opsIdentityPath },
    ];

    async function opsOpenFolder(p: string) {
      if (!p) return;
      try {
        await invoke("show_item_in_folder", { path: p });
      } catch {
        try {
          await invoke("open_file_with_default", { path: p });
        } catch {
          if (opsWsPath && opsWsPath !== p) {
            try { await invoke("open_file_with_default", { path: opsWsPath }); } catch (e) { notifyError(String(e)); }
          }
        }
      }
    }

    async function opsHandleBundleExport() {
      if (!currentWorkspaceId) return;
      let _b: string | number | undefined;
      try {
        const ts = Math.floor(Date.now() / 1000);
        const filename = `openakita-diagnostic-${ts}.zip`;
        const { save } = await import("@tauri-apps/plugin-dialog");
        const defaultDir = info?.homeDir ? joinPath(info.homeDir, "Downloads") : undefined;
        const chosen = await save({
          defaultPath: defaultDir ? joinPath(defaultDir, filename) : filename,
          filters: [{ name: "ZIP Archive", extensions: ["zip"] }],
        });
        if (!chosen) return;
        _b = notifyLoading(t("adv.opsLogExporting"));
        let sysInfoJson: string | undefined;
        if (shouldUseHttpApi()) {
          try {
            const res = await safeFetch(`${httpApiBase()}/api/system-info`, { signal: AbortSignal.timeout(5_000) });
            const data = await res.json();
            sysInfoJson = JSON.stringify(data, null, 2);
          } catch { /* best-effort */ }
        }
        const dest = await invoke<string>("export_diagnostic_bundle", {
          workspaceId: currentWorkspaceId,
          systemInfoJson: sysInfoJson ?? null,
          destPath: chosen,
        });
        notifySuccess(t("adv.opsLogExportSuccess", { path: dest }));
        await invoke("show_item_in_folder", { path: dest });
      } catch (e) { notifyError(String(e)); } finally { if (_b !== undefined) dismissLoading(_b); }
    }

    // ── Backup functions ──

    async function runBackupNow() {
      if (!currentWorkspaceId) return;
      let outputDir = envGet(envDraft, "BACKUP_PATH");
      if (!outputDir) {
        try {
          const { openFileDialog } = await import("./platform");
          const selected = await openFileDialog({ directory: true, title: t("adv.backupPath") });
          if (!selected) return;
          outputDir = selected;
          setEnvDraft((prev) => envSet(prev, "BACKUP_PATH", outputDir));
        } catch (e) { notifyError(String(e)); return; }
      }
      const _b = notifyLoading(t("adv.backupExporting"));
      try {
        const apiPort = (serviceStatus && "port" in serviceStatus ? serviceStatus.port : undefined) || 18900;
        const result = await invoke<{ status: string; path?: string; filename?: string; size_bytes?: number }>(
          "export_workspace_backup",
          {
            workspaceId: currentWorkspaceId,
            outputDir,
            includeUserdata: envGet(envDraft, "BACKUP_INCLUDE_USERDATA", "true") === "true",
            includeMedia: envGet(envDraft, "BACKUP_INCLUDE_MEDIA", "false") === "true",
            apiPort,
          }
        );
        notifySuccess(t("adv.backupDone", { path: result.filename || result.path || "" }));
        loadBackupHistory();
      } catch (e) { notifyError(String(e)); } finally { dismissLoading(_b); }
    }

    async function executeBackupImport(zipPath: string) {
      if (!currentWorkspaceId) return;
      const _b = notifyLoading(t("adv.backupExporting"));
      try {
        const apiPort = (serviceStatus && "port" in serviceStatus ? serviceStatus.port : undefined) || 18900;
        const result = await invoke<{ status: string; restored_count?: number }>(
          "import_workspace_backup",
          { workspaceId: currentWorkspaceId, zipPath, apiPort }
        );
        notifySuccess(t("adv.backupImportDone", { count: result.restored_count ?? 0 }));
      } catch (e) { notifyError(String(e)); } finally { dismissLoading(_b); }
    }

    async function runBackupImport() {
      if (!currentWorkspaceId) return;
      try {
        const { openFileDialog } = await import("./platform");
        const zipPath = await openFileDialog({ title: t("adv.backupImport"), filters: [{ name: "Backup", extensions: ["zip"] }] });
        if (!zipPath) return;
        askConfirm(t("adv.backupImportConfirm"), () => executeBackupImport(zipPath));
      } catch (e) { notifyError(String(e)); }
    }

    async function loadBackupHistory() {
      const url = shouldUseHttpApi() ? httpApiBase() : null;
      if (!url || !envGet(envDraft, "BACKUP_PATH")) { setBackupHistory([]); return; }
      try {
        const res = await safeFetch(`${url}/api/workspace/backups`, { signal: AbortSignal.timeout(5_000) });
        const data = await res.json();
        setBackupHistory(data.backups || []);
      } catch { setBackupHistory([]); }
    }

    async function browseBackupPath() {
      try {
        const { openFileDialog } = await import("./platform");
        const selected = await openFileDialog({ directory: true, title: t("adv.backupPath") });
        if (selected) {
          setEnvDraft((prev) => envSet(prev, "BACKUP_PATH", selected));
        }
      } catch (e) { notifyError(String(e)); }
    }

    // ── Workspace migration ──

    async function runMigratePreflight() {
      if (!migrateTargetPath.trim()) { notifyError(t("adv.migrateTargetPlaceholder")); return; }
      setMigrateBusy(true);
      try {
        const info = await invoke<NonNullable<typeof migratePreflight>>("preflight_migrate_root", { targetPath: migrateTargetPath.trim() });
        setMigratePreflight(info);
      } catch (e: any) {
        notifyError(String(e));
        setMigratePreflight(null);
      } finally {
        setMigrateBusy(false);
      }
    }

    async function browseMigratePath() {
      try {
        const { openFileDialog } = await import("./platform");
        const selected = await openFileDialog({ directory: true, title: t("adv.migrateTargetPath") });
        if (selected) {
          setMigrateTargetPath(selected);
          setMigratePreflight(null);
        }
      } catch (e) { notifyError(String(e)); }
    }

    async function executeMigrate() {
      if (!migratePreflight?.canMigrate) return;
      setMigrateBusy(true);
      const _busyId = notifyLoading(t("adv.migrateBusy"));
      try {
        const info = await invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>(
          "set_custom_root_dir", { path: migrateTargetPath.trim(), migrate: true }
        );
        setMigrateCurrentRoot(info.currentRoot);
        setMigrateCustomRoot(info.customRoot);
        setMigratePreflight(null);
        dismissLoading(_busyId);
        setMigrateBusy(false);
        notifySuccess(t("adv.migrateSuccess"));
        await refreshAll();
        await restartService();
      } catch (e: any) {
        notifyError(t("adv.migrateFailed", { error: String(e) }));
        setMigrateBusy(false);
        dismissLoading(_busyId);
      }
    }

    function runMigrate() {
      if (!migratePreflight?.canMigrate) return;
      askConfirm(
        t("adv.migrateConfirm", { from: migratePreflight.sourcePath, to: migratePreflight.targetPath }),
        () => executeMigrate()
      );
    }

    async function executeMigrateReset() {
      setMigrateBusy(true);
      const _busyId = notifyLoading(t("adv.migrateBusy"));
      try {
        const info = await invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>(
          "set_custom_root_dir", { path: null, migrate: true }
        );
        setMigrateCurrentRoot(info.currentRoot);
        setMigrateCustomRoot(info.customRoot);
        setMigratePreflight(null);
        setMigrateTargetPath("");
        dismissLoading(_busyId);
        setMigrateBusy(false);
        notifySuccess(t("adv.migrateResetDone", { path: info.currentRoot }));
        await refreshAll();
        await restartService();
      } catch (e: any) {
        notifyError(String(e));
        setMigrateBusy(false);
        dismissLoading(_busyId);
      }
    }

    function runMigrateResetDefault() {
      askConfirm(t("adv.migrateResetConfirm"), () => executeMigrateReset());
    }

    return (
      <>
        {/* ── 系统配置（桌面通知 / 会话 / 日志） ── */}
        <div className="card">
          <h3 style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>{t("config.agentAdvanced")}</h3>

          <Section title={t("config.agentDesktopNotify")}>
            <div className="grid2">
              {FB({ k: "DESKTOP_NOTIFY_ENABLED", label: t("config.agentDesktopNotifyEnable"), help: t("config.agentDesktopNotifyEnableHelp") })}
              {FB({ k: "DESKTOP_NOTIFY_SOUND", label: t("config.agentDesktopNotifySound"), help: t("config.agentDesktopNotifySoundHelp") })}
            </div>
          </Section>

          <Section title={t("config.agentSessionSection")} className="mt-2">
            <div className="grid3">
              {FT({ k: "SESSION_TIMEOUT_MINUTES", label: t("config.agentSessionTimeout"), placeholder: "30" })}
              {FT({ k: "SESSION_MAX_HISTORY", label: t("config.agentSessionMax"), placeholder: "50" })}
              {FT({ k: "SESSION_STORAGE_PATH", label: t("config.agentSessionPath"), placeholder: "data/sessions" })}
            </div>
          </Section>

          <Section title={t("config.agentLogSection")} className="mt-2">
            <div className="grid3">
              {FS({ k: "LOG_LEVEL", label: t("config.agentLogLevel"), options: [
                { value: "DEBUG", label: "DEBUG" },
                { value: "INFO", label: "INFO" },
                { value: "WARNING", label: "WARNING" },
                { value: "ERROR", label: "ERROR" },
              ] })}
              {FT({ k: "LOG_DIR", label: t("config.agentLogDir"), placeholder: "logs" })}
              {FT({ k: "DATABASE_PATH", label: t("config.agentDbPath"), placeholder: "data/agent.db" })}
            </div>
            <div className="grid3">
              {FT({ k: "LOG_MAX_SIZE_MB", label: t("config.agentLogMaxMB"), placeholder: "10" })}
              {FT({ k: "LOG_BACKUP_COUNT", label: t("config.agentLogBackup"), placeholder: "30" })}
              {FT({ k: "LOG_RETENTION_DAYS", label: t("config.agentLogRetention"), placeholder: "30" })}
            </div>
            <div className="grid3">
              {FB({ k: "LOG_TO_CONSOLE", label: t("config.agentLogConsole") })}
              {FB({ k: "LOG_TO_FILE", label: t("config.agentLogFile") })}
            </div>
          </Section>
        </div>

        {/* ── 上下文管理 ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <Section title={t("config.ctxMgmtTitle")}>
            <div className="grid2">
              {FT({ k: "CONTEXT_MAX_WINDOW", label: t("config.ctxMaxWindow") + " (tokens)", help: t("config.ctxMaxWindowHelp"), placeholder: t("config.ctxMaxWindowPlaceholder") })}
              {FR({ k: "CONTEXT_MIN_RECENT_TURNS", label: t("config.ctxMinRecentTurns"), help: t("config.ctxMinRecentTurnsHelp"), min: 2, max: 30, step: 1, defaultValue: 8, unit: t("config.ctxUnitTurns") })}
            </div>
            <div className="grid2">
              {FR({ k: "CONTEXT_COMPRESSION_RATIO", label: t("config.ctxCompressionRatio"), help: t("config.ctxCompressionRatioHelp"), min: 0.05, max: 0.5, step: 0.01, defaultValue: 0.15, unit: "×" })}
              {FR({ k: "CONTEXT_COMPRESSION_THRESHOLD", label: t("config.ctxCompressionThreshold"), help: t("config.ctxCompressionThresholdHelp"), min: 0.5, max: 0.95, step: 0.01, defaultValue: 0.85, unit: "×" })}
            </div>
            <div className="grid2">
              {FR({ k: "CONTEXT_BOUNDARY_COMPRESSION_RATIO", label: t("config.ctxBoundaryRatio"), help: t("config.ctxBoundaryRatioHelp"), min: 0.05, max: 0.5, step: 0.01, defaultValue: 0.18, unit: "×" })}
              {FR({ k: "CONTEXT_LARGE_TOOL_THRESHOLD", label: t("config.ctxLargeToolThreshold"), help: t("config.ctxLargeToolThresholdHelp"), min: 500, max: 20000, step: 500, defaultValue: 5000, unit: "tokens" })}
            </div>
            <div className="grid2">
              {FB({ k: "CONTEXT_ENABLE_TOOL_COMPRESSION", label: t("config.ctxEnableToolCompression"), help: t("config.ctxEnableToolCompressionHelp") })}
            </div>
          </Section>
        </div>

        {/* ── Card 2: 网络与安全 ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <h3 style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>{t("adv.networkSecurityTitle")}</h3>

          <Section title={t("adv.webNetworkTitle", { defaultValue: "Web 访问" })}>
            <div className="cardHint" style={{ marginBottom: 4 }}>
              {t("adv.webNetworkHint", { defaultValue: "控制 HTTP API 服务的监听范围和代理设置。修改后需重启后端生效。" })}
            </div>
            <FieldBool k="API_HOST" label={t("adv.apiHostLabel", { defaultValue: "允许外部访问（局域网/公网）" })}
              help={t("adv.apiHostHelp", { defaultValue: "开启后监听 0.0.0.0，允许其他设备通过 IP 访问 Web 端。关闭则仅本机可访问。" })}
              envDraft={{ ...envDraft, API_HOST: (envDraft.API_HOST === "0.0.0.0") ? "true" : "false" }}
              onEnvChange={(fn) => {
                const next = fn({ API_HOST: (envDraft.API_HOST === "0.0.0.0") ? "true" : "false" });
                if (next.API_HOST === "true") {
                  askConfirm(
                    t("adv.apiHostWarn"),
                    () => setEnvDraft((prev) => ({ ...prev, API_HOST: "0.0.0.0" })),
                  );
                } else {
                  setEnvDraft((prev) => ({ ...prev, API_HOST: "127.0.0.1" }));
                }
              }}
            />
            <FieldBool k="TRUST_PROXY" label={t("adv.trustProxyLabel", { defaultValue: "反向代理模式（Nginx/Caddy）" })}
              help={t("adv.trustProxyHelp", { defaultValue: "通过反向代理部署时必须开启。开启后读取 X-Forwarded-For 获取真实 IP，并关闭本地免密。" })}
              envDraft={envDraft} onEnvChange={(fn) => setEnvDraft((prev) => fn(prev))}
            />
            <p className="mt-1.5 text-xs text-muted-foreground/70">
              {t("adv.webNetworkRestartHint", { defaultValue: "保存后需在状态面板重启后端生效" })}
            </p>
          </Section>

          {IS_TAURI && !!serviceStatus?.running && dataMode !== "remote" && (
            <Section title={t("adv.webPasswordTitle")} className="mt-2">
              <div className="cardHint" style={{ marginBottom: 4 }}>{t("adv.webPasswordHint")}</div>
              <WebPasswordManager apiBase={httpApiBase()} />
            </Section>
          )}
        </div>

        {/* ── Card 3: 平台与云服务 ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <h3 style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>{t("adv.platformTitle")}</h3>

          <Section title={t("adv.hubTitle")}
            toggle={
              <Switch
                checked={storeVisible}
                onCheckedChange={(v) => { setStoreVisible(v); localStorage.setItem("openakita_storeVisible", String(v)); }}
              />
            }
          >
            <p className="text-xs text-muted-foreground mb-1">{t("adv.hubHint")}</p>
            <div className="flex items-center gap-1.5">
              <Input
                value={hubApiUrl}
                onChange={(e) => setHubApiUrl(e.target.value)}
                placeholder={t("adv.hubUrlPlaceholder")}
                className="flex-1 max-w-[380px]"
              />
              <Button
                size="sm"
                disabled={!!busy}
                onClick={async () => {
                  const val = hubApiUrl.trim() || "https://openakita.ai/api";
                  if (shouldUseHttpApi()) {
                    try {
                      await safeFetch(`${httpApiBase()}/api/config/env`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ entries: { HUB_API_URL: val } }),
                      });
                      notifySuccess(t("adv.hubSaved"));
                    } catch (e) { notifyError(String(e)); }
                  } else {
                    setEnvDraft((prev) => envSet(prev, "HUB_API_URL", val));
                    notifySuccess(t("adv.hubSaved"));
                  }
                }}
              >
                {t("common.save") || "Save"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!!busy}
                onClick={async () => {
                  const url = (hubApiUrl.trim() || "https://openakita.ai/api").replace(/\/$/, "");
                  try {
                    const res = await fetch(`${url}/health`, { signal: AbortSignal.timeout(6000) });
                    if (res.ok) notifySuccess(t("adv.hubTestOk"));
                    else notifyError(t("adv.hubTestFail"));
                  } catch { notifyError(t("adv.hubTestFail")); }
                }}
              >
                {t("adv.hubTest")}
              </Button>
            </div>
          </Section>
        </div>

        {/* ── Card 4: 数据与备份 ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <h3 style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>{t("adv.dataBackupTitle")}</h3>

          <Section title={t("adv.backupAutoTitle")} subtitle={t("adv.backupAutoHint")}
            toggle={
              <Switch
                checked={envGet(envDraft, "BACKUP_ENABLED", "false") === "true"}
                onCheckedChange={(v) => setEnvDraft((prev) => envSet(prev, "BACKUP_ENABLED", String(v)))}
              />
            }
          >
            <div className="space-y-3">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">{t("adv.backupPath")}</Label>
                <div className="flex gap-1.5 items-center">
                  <Input
                    value={envGet(envDraft, "BACKUP_PATH")}
                    onChange={(e) => setEnvDraft((prev) => envSet(prev, "BACKUP_PATH", e.target.value))}
                    placeholder={t("adv.backupPathPlaceholder")}
                    className="flex-1"
                  />
                  <Button variant="outline" onClick={browseBackupPath} disabled={!!busy}>{t("adv.backupBrowse")}</Button>
                </div>
              </div>

              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">{t("adv.backupMaxKeep")}</Label>
                <Input
                  type="number"
                  min={1} max={100}
                  value={envGet(envDraft, "BACKUP_MAX_BACKUPS", "5")}
                  onChange={(e) => setEnvDraft((prev) => envSet(prev, "BACKUP_MAX_BACKUPS", String(Math.max(1, parseInt(e.target.value) || 5))))}
                  className="w-20"
                />
              </div>

              {envGet(envDraft, "BACKUP_ENABLED", "false") === "true" && (
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">{t("adv.backupSchedule")}</Label>
                  <div className="flex items-center gap-2">
                    {(() => {
                      const cron = envGet(envDraft, "BACKUP_CRON", "0 2 * * *");
                      const schedVal = cron === "0 2 * * *" ? "daily" : cron === "0 2 * * 0" ? "weekly" : "custom";
                      return (
                        <>
                          <Select
                            value={schedVal}
                            onValueChange={(v) => {
                              if (v === "daily") setEnvDraft((prev) => envSet(prev, "BACKUP_CRON", "0 2 * * *"));
                              else if (v === "weekly") setEnvDraft((prev) => envSet(prev, "BACKUP_CRON", "0 2 * * 0"));
                            }}
                          >
                            <SelectTrigger size="sm"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="daily">{t("adv.backupScheduleDaily")}</SelectItem>
                              <SelectItem value="weekly">{t("adv.backupScheduleWeekly")}</SelectItem>
                              <SelectItem value="custom">{t("adv.backupScheduleCustom")}</SelectItem>
                            </SelectContent>
                          </Select>
                          {schedVal === "custom" && (
                            <Input
                              value={cron}
                              onChange={(e) => setEnvDraft((prev) => envSet(prev, "BACKUP_CRON", e.target.value))}
                              className="w-[140px]"
                            />
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>
              )}

              <div className="flex gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="backup-userdata"
                    checked={envGet(envDraft, "BACKUP_INCLUDE_USERDATA", "true") === "true"}
                    onCheckedChange={(v) => setEnvDraft((prev) => envSet(prev, "BACKUP_INCLUDE_USERDATA", String(!!v)))}
                  />
                  <Label htmlFor="backup-userdata" className="cursor-pointer">{t("adv.backupIncludeUserdata")}</Label>
                </div>
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="backup-media"
                    checked={envGet(envDraft, "BACKUP_INCLUDE_MEDIA", "false") === "true"}
                    onCheckedChange={(v) => setEnvDraft((prev) => envSet(prev, "BACKUP_INCLUDE_MEDIA", String(!!v)))}
                  />
                  <Label htmlFor="backup-media" className="cursor-pointer">{t("adv.backupIncludeMedia")}</Label>
                </div>
              </div>
            </div>
          </Section>

          {IS_TAURI && (
            <Section title={t("adv.backupManualTitle")} subtitle={t("adv.backupManualHint")} className="mt-2">
              <div className="space-y-3">
                <div className="flex gap-2 flex-wrap">
                  <Button variant="outline" size="sm" onClick={runBackupNow} disabled={!currentWorkspaceId || !!busy}>
                    {t("adv.backupNow")}
                  </Button>
                  <Button variant="outline" size="sm" onClick={runBackupImport} disabled={!currentWorkspaceId || !!busy}>
                    {t("adv.backupRestore")}
                  </Button>
                </div>

                {envGet(envDraft, "BACKUP_PATH") && (
                  <div>
                    <div
                      className="text-sm font-medium cursor-pointer flex items-center gap-1"
                      onClick={() => { setBackupShowHistory((p) => !p); if (!backupShowHistory) loadBackupHistory(); }}
                    >
                      <span className="inline-block transition-transform duration-150" style={{ transform: backupShowHistory ? "rotate(90deg)" : "rotate(0)" }}>▸</span>
                      {t("adv.backupHistory")}
                    </div>
                    {backupShowHistory && (
                      <div className="mt-1.5">
                        {backupHistory.length === 0 ? (
                          <p className="text-xs text-muted-foreground">{t("adv.backupNoHistory")}</p>
                        ) : (
                          <div className="flex flex-col gap-1">
                            {backupHistory.map((b) => (
                              <div key={b.filename} className="flex justify-between items-center text-xs py-1 px-2 rounded-md bg-muted/30">
                                <span className="font-mono">{b.filename}</span>
                                <span className="text-muted-foreground whitespace-nowrap ml-3">
                                  {(b.size_bytes / 1024 / 1024).toFixed(1)} MB
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </Section>
          )}

          {IS_TAURI && (
            <Section title={t("adv.migrateTitle")} subtitle={t("adv.migrateHint")} className="mt-2">
              <div className="space-y-3">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">{t("adv.migrateCurrentPath")}</Label>
                  <p className="text-xs font-mono text-muted-foreground break-all">{migrateCurrentRoot || "—"}</p>
                </div>

                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">{t("adv.migrateTargetPath")}</Label>
                  <div className="flex gap-1.5 items-center">
                    <Input
                      value={migrateTargetPath}
                      onChange={(e) => { setMigrateTargetPath(e.target.value); setMigratePreflight(null); }}
                      placeholder={t("adv.migrateTargetPlaceholder")}
                      className="flex-1"
                      disabled={migrateBusy}
                    />
                    <Button variant="outline" size="sm" onClick={browseMigratePath} disabled={migrateBusy}>
                      {t("adv.migrateBrowse")}
                    </Button>
                    <Button variant="outline" size="sm" onClick={runMigratePreflight} disabled={migrateBusy || !migrateTargetPath.trim()}>
                      {migrateBusy ? t("adv.migrateChecking") : t("adv.migrateCheck")}
                    </Button>
                  </div>
                </div>

                {migratePreflight && (
                  <div className="rounded-md border p-3 space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">{t("adv.migrateSourceSize")}</span>
                      <span className="font-mono">{migratePreflight.sourceSizeMb.toFixed(1)} MB</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">{t("adv.migrateTargetFree")}</span>
                      <span className="font-mono">{migratePreflight.targetFreeMb >= 1024 ? (migratePreflight.targetFreeMb / 1024).toFixed(1) + " GB" : migratePreflight.targetFreeMb.toFixed(0) + " MB"}</span>
                    </div>
                    {migratePreflight.entries.length > 0 && (
                      <div>
                        <span className="text-muted-foreground text-xs">{t("adv.migrateEntries")}</span>
                        <div className="flex flex-col gap-0.5 mt-1">
                          {migratePreflight.entries.map((e) => (
                            <div key={e.name} className="flex justify-between text-xs py-0.5 px-2 rounded bg-muted/30">
                              <span className="font-mono">{e.isDir ? "📁" : "📄"} {e.name}{e.existsAtTarget ? ` (${t("adv.migrateConflictHint")})` : ""}</span>
                              <span className="text-muted-foreground">{e.sizeMb.toFixed(1)} MB</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <p className={`text-xs ${migratePreflight.canMigrate ? "text-muted-foreground" : "text-destructive"}`}>
                      {migratePreflight.reason}
                    </p>
                  </div>
                )}

                <div className="flex gap-2 flex-wrap">
                  <Button
                    size="sm"
                    onClick={runMigrate}
                    disabled={migrateBusy || !migratePreflight?.canMigrate}
                  >
                    {migrateBusy ? t("adv.migrateBusy") : t("adv.migrateStart")}
                  </Button>
                  {migrateCustomRoot && (
                    <Button variant="outline" size="sm" onClick={runMigrateResetDefault} disabled={migrateBusy}>
                      {t("adv.migrateResetDefault")}
                    </Button>
                  )}
                </div>

              </div>
            </Section>
          )}
        </div>

        {/* ── Card 5: 系统信息与运维 ── */}
        <div className="card" style={{ marginTop: 12 }}>
          <h3 style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>{t("adv.sysOpsTitle")}</h3>

          <Section title={t("adv.sysTitle")}
            toggle={IS_TAURI ? (
              <Button variant="outline" size="xs" onClick={(e) => { e.preventDefault(); opsHandleBundleExport(); }} disabled={!!busy || !currentWorkspaceId}>
                {busy === t("adv.opsLogExporting") ? t("adv.opsLogExporting") : t("adv.exportDiagBtn")}
              </Button>
            ) : undefined}
          >
            {!advSysInfo ? (
              advLoading.sysinfo ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <span className="spinner size-3.5" />
                  {t("common.loading")}
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={fetchSystemInfo} disabled={!!busy || !serviceStatus?.running}>{t("adv.sysLoad")}</Button>
                  {!serviceStatus?.running && <span className="text-xs text-muted-foreground">{t("adv.needService")}</span>}
                </div>
              )
            ) : (
              <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {Object.entries(advSysInfo).map(([k, v]) => (
                  <Fragment key={k}>
                    <span className="font-medium text-muted-foreground">{k}</span>
                    <span>{v}</span>
                  </Fragment>
                ))}
                <span className="font-medium text-muted-foreground">Desktop</span>
                <span>{desktopVersion}</span>
              </div>
            )}
          </Section>

          {IS_TAURI && (
            <Section title={t("adv.opsPaths")} className="mt-2">
              <div className="grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-1.5 items-center text-sm">
                {opsPathRows.map((row) => (
                  <Fragment key={row.label}>
                    <span className="font-medium whitespace-nowrap">{row.label}</span>
                    <span className="break-all text-muted-foreground text-xs font-mono">{row.path || "—"}</span>
                    <Button variant="outline" size="xs" onClick={() => opsOpenFolder(row.path)} disabled={!row.path}>{t("adv.opsOpenFolder")}</Button>
                  </Fragment>
                ))}
              </div>
            </Section>
          )}

          {IS_TAURI && (
            <Section title={t("adv.factoryResetTitle")} subtitle={t("adv.factoryResetSubtitle")} className="mt-2">
              <p className="text-xs text-muted-foreground mb-2">{t("adv.factoryResetDesc")}</p>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => { setFactoryResetConfirmText(""); setFactoryResetOpen(true); }}
                disabled={!!busy}
              >
                {t("adv.factoryResetBtn")}
              </Button>
            </Section>
          )}

          <AlertDialog open={factoryResetOpen} onOpenChange={(open) => { if (!open) setFactoryResetOpen(false); }}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("adv.factoryResetConfirmTitle")}</AlertDialogTitle>
                <AlertDialogDescription className="space-y-2" asChild>
                  <div>
                    <p>{t("adv.factoryResetConfirmDesc")}</p>
                    <ul className="list-disc pl-5 text-sm space-y-0.5">
                      <li>{t("adv.factoryResetItem1")}</li>
                      <li>{t("adv.factoryResetItem2")}</li>
                      <li>{t("adv.factoryResetItem3")}</li>
                      <li>{t("adv.factoryResetItem4")}</li>
                    </ul>
                    <p className="font-medium mt-2">{t("adv.factoryResetTypeHint")}</p>
                    <Input
                      value={factoryResetConfirmText}
                      onChange={(e) => setFactoryResetConfirmText(e.target.value)}
                      placeholder="RESET"
                      className="mt-1"
                      autoFocus
                    />
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  disabled={factoryResetConfirmText !== "RESET"}
                  onClick={async () => {
                    setFactoryResetOpen(false);
                    const _b = notifyLoading(t("adv.factoryResetInProgress"));
                    try {
                      const result = await invoke<string>("factory_reset");
                      dismissLoading(_b);
                      notifySuccess(result);
                      try { localStorage.clear(); } catch {}
                      setTimeout(() => { setView("onboarding"); window.location.reload(); }, 1500);
                    } catch (e) {
                      dismissLoading(_b);
                      notifyError(String(e));
                    }
                  }}
                >
                  {t("adv.factoryResetConfirmBtn")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
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
                    <TelegramPairingCodeHint currentWorkspaceId={currentWorkspaceId} />
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
                {FS({ k: "THINKING_MODE", label: "Thinking 模式", options: [
                  { value: "auto", label: "auto (自动判断)" },
                  { value: "always", label: "always (始终思考)" },
                  { value: "never", label: "never (从不思考)" },
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

  // 保存 env keys 的辅助函数（供 SkillManager 使用，路由逻辑与 saveEnvKeys 一致）
  async function saveEnvKeysExternal(keys: string[]) {
    const entries: Record<string, string> = {};
    for (const k of keys) {
      if (Object.prototype.hasOwnProperty.call(envDraft, k)) {
        const v = (envDraft[k] ?? "").trim();
        if (v.length > 0) {
          entries[k] = v;
        }
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
        logger.warn("App", "saveEnvKeysExternal: HTTP failed, falling back to Tauri");
      }
    }
    if (IS_TAURI && currentWorkspaceId) {
      const tauriEntries = Object.entries(entries).map(([key, value]) => ({ key, value }));
      await invoke("workspace_update_env", { workspaceId: currentWorkspaceId, entries: tauriEntries });
    }
  }

  // ── Onboarding Wizard 渲染 ──
  async function obLoadModules() {
    if (!IS_TAURI) return;
    try {
      const modules = await invoke<ModuleInfo[]>("detect_modules");
      setObModules(modules);
      
    } catch (e) {
      logger.warn("App", "detect_modules failed", { error: String(e) });
    }
  }

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

    // 安装配置日志：单独写入 {data_root}/logs/onboarding-日期.log，便于排查
    const dateLabel = new Date().toISOString().slice(0, 19).replace("T", "_").replace(/:/g, "-");
    let obLogPath: string | null = null;
    try {
      obLogPath = await invoke<string>("start_onboarding_log", { dateLabel });
      // 写入配置快照（不记录密钥明文）
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
      // 日志文件创建失败不影响主流程
    }

    // 初始化任务列表
    const taskDefs: SetupTask[] = [
      { id: "workspace", label: "准备工作区", status: "pending" },
      { id: "llm-config", label: "保存 LLM 配置", status: savedEndpoints.length > 0 ? "pending" : "skipped" },
      { id: "env-save", label: "保存环境变量", status: "pending" },
    ];
    
    taskDefs.push({ id: "backend-check", label: "检查后端环境", status: "pending" });
    // CLI 注册
    const cliCommands: string[] = [];
    if (obCliOpenakita) cliCommands.push("openakita");
    if (obCliOa) cliCommands.push("oa");
    if (cliCommands.length > 0) {
      taskDefs.push({ id: "cli", label: `注册 CLI 命令 (${cliCommands.join(", ")})`, status: "pending" });
    }
    // 开机自启
    if (obAutostart) {
      taskDefs.push({ id: "autostart", label: t("onboarding.autostart.taskLabel"), status: "pending" });
    }
    if (obPendingBots.length > 0) {
      taskDefs.push({ id: "register-bots", label: t("onboarding.registerBots", { count: obPendingBots.length }), status: "pending" });
    }
    taskDefs.push({ id: "service-start", label: "启动后端服务", status: "pending" });
    taskDefs.push({ id: "http-wait", label: "等待 HTTP 服务就绪", status: "pending" });
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
    /** 将任务状态写入日志，便于排查 */
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

      // Derive .env enabled flags from pending bots (ensures channel deps get installed)
      if (obPendingBots.length > 0) {
        const enabledTypes = new Set(obPendingBots.map((b) => b.type));
        for (const bType of enabledTypes) {
          const ek = TYPE_TO_ENABLED_KEY[bType];
          if (ek) {
            setEnvDraft((m: EnvMap) => ({ ...m, [ek]: "true" }));
            envDraft[ek] = "true";
          }
        }
      }

      // ── STEP: env-save ──
      updateTask("env-save", { status: "running" });
      logTask("保存环境变量", "running");
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
        logTask("保存环境变量", "done", `${envEntries.length} 项`);
      } catch (e) {
        log(`⚠ 保存环境变量失败: ${String(e)}`);
        updateTask("env-save", { status: "error", detail: String(e) });
        logTask("保存环境变量", "error", String(e));
        hasErr = true;
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

      // ── STEP: register-bots (write to runtime_state.json via Tauri, before backend starts) ──
      if (obPendingBots.length > 0) {
        updateTask("register-bots", { status: "running" });
        logTask("注册 IM Bot", "running");
        try {
          let runtimeState: Record<string, unknown> = {};
          try {
            const content = await invoke<string>("workspace_read_file", {
              workspaceId: activeWsId,
              relativePath: "data/runtime_state.json",
            });
            runtimeState = JSON.parse(content);
          } catch { /* file doesn't exist yet, start fresh */ }

          const existingBots: Record<string, unknown>[] = Array.isArray(runtimeState.im_bots)
            ? (runtimeState.im_bots as Record<string, unknown>[])
            : [];
          const existingIds = new Set(existingBots.map((b) => b.id));

          let added = 0;
          for (const bot of obPendingBots) {
            if (!existingIds.has(bot.id)) {
              existingBots.push(bot);
              existingIds.add(bot.id);
              added++;
              log(`✓ Bot ${bot.name || bot.id} 已写入配置`);
            } else {
              log(`⏭ Bot ${bot.id} 已存在，跳过`);
            }
          }
          runtimeState.im_bots = existingBots;

          await invoke("workspace_write_file", {
            workspaceId: activeWsId,
            relativePath: "data/runtime_state.json",
            content: JSON.stringify(runtimeState, null, 2),
          });

          updateTask("register-bots", { status: "done", detail: `${added} Bot${added > 1 ? "s" : ""}` });
          logTask("注册 IM Bot", "done", `${added} Bot(s) → runtime_state.json`);
        } catch (e) {
          log(`⚠ Bot 配置写入失败: ${String(e)}`);
          updateTask("register-bots", { status: "error", detail: String(e).slice(0, 120) });
          logTask("注册 IM Bot", "error", String(e));
          hasErr = true;
        }
      }

      // ── STEP: service-start ──
      updateTask("service-start", { status: "running" });
      logTask("启动后端服务", "running");
      log(t("onboarding.progress.startingService"));
      const effectiveVenv = venvDir || (info ? joinPath(info.openakitaRootDir, "venv") : "");
      try {
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

    const stepIndicator = (
      <div className="flex gap-2 py-4">
        {obStepDots.map((s, i) => (
          <div
            key={s}
            className={`size-2 rounded-full transition-all duration-200 ${
              i === obCurrentIdx
                ? "bg-primary scale-[1.3]"
                : i < obCurrentIdx
                  ? "bg-emerald-500"
                  : "bg-muted-foreground/25"
            }`}
          />
        ))}
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
                    logger.warn("App", "ob: create default workspace failed", { error: String(e) });
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
              <div className="obFormArea">{renderIM({ onboarding: true })}</div>
              <p className="obSkipHint">{t("onboarding.skipHint")}</p>
            </div>
            <div className="obFooter">
              {stepIndicator}
              <div className="obFooterBtns">
                <Button variant="outline" onClick={() => setObStep("ob-llm")}>{t("config.prev")}</Button>
                <Button onClick={() => setObStep("ob-cli")}>{t("config.next")}</Button>
                <Button variant="secondary" onClick={() => setObStep("ob-cli")} title={t("onboarding.im.skip")}>
                  {t("onboarding.im.skipShort") || t("onboarding.im.skip")}
                </Button>
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
                    <span className="obModuleDesc">完整命令名称</span>
                  </div>
                </label>

                <label className="obModuleItem" data-checked={obCliOa || undefined}>
                  <Checkbox checked={obCliOa} onCheckedChange={() => setObCliOa(!obCliOa)} />
                  <div className="obModuleInfo">
                    <strong style={{ fontFamily: "monospace", fontSize: 15 }}>oa</strong>
                    <span className="obModuleDesc">简短别名，推荐日常使用</span>
                  </div>
                  <Badge variant="secondary" className="obModuleBadge obModuleBadgeRec">推荐</Badge>
                </label>

                <label className="obModuleItem" data-checked={obCliAddToPath || undefined}>
                  <Checkbox checked={obCliAddToPath} onCheckedChange={() => setObCliAddToPath(!obCliAddToPath)} />
                  <div className="obModuleInfo">
                    <strong>添加到系统 PATH</strong>
                    <span className="obModuleDesc">新打开的终端中可直接输入命令名运行，无需完整路径</span>
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
                    <p className="text-[13px] font-semibold text-muted-foreground">安装后可使用的命令示例</p>
                    <div className="bg-slate-900 rounded-lg px-4 py-3.5 font-mono text-[13px] leading-[1.9] text-slate-200 overflow-x-auto">
                      {obCliOa && <>
                        <div><span className="text-slate-400">$</span> <span className="text-blue-300">oa</span> serve <span className="text-slate-400 ml-6"># 启动后端服务</span></div>
                        <div><span className="text-slate-400">$</span> <span className="text-blue-300">oa</span> status <span className="text-slate-400 ml-4"># 查看运行状态</span></div>
                        <div><span className="text-slate-400">$</span> <span className="text-blue-300">oa</span> run <span className="text-slate-400 ml-9"># 单次对话</span></div>
                      </>}
                      {obCliOa && obCliOpenakita && <div className="h-1" />}
                      {obCliOpenakita && <>
                        <div><span className="text-slate-400">$</span> <span className="text-indigo-300">openakita</span> init <span className="text-slate-400 ml-2"># 初始化工作区</span></div>
                        <div><span className="text-slate-400">$</span> <span className="text-indigo-300">openakita</span> serve <span className="text-slate-400"># 启动后端服务</span></div>
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
                  {t("onboarding.modules.startInstall")}
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
                模块与运行环境体积较大，安装过程中请耐心等待，请勿关闭本窗口。
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
    if (!info) return <div className="card">加载中...</div>;
    if (view === "status") return renderStatus();
    if (view === "chat") return null;  // ChatView 始终挂载，不在此渲染

    const _disableToggle = (viewKey: string, label: string) => (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", marginBottom: 12 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--muted)", cursor: "pointer" }}>
          <span>{disabledViews.includes(viewKey) ? `${label} 已禁用` : `${label} 已启用`}</span>
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
          onSaveEnvKeys={saveEnvKeysExternal}
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
        <IMView serviceRunning={serviceStatus?.running ?? false} multiAgentEnabled={multiAgentEnabled} apiBaseUrl={apiBaseUrl} onRequestRestart={restartService} venvDir={venvDir} />
      );
    }
    if (view === "token_stats") {
      return (
        <div>
          {_disableToggle("token_stats", "Token 统计")}
          {disabledViews.includes("token_stats") ? (
            <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
              <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，点击上方开关启用</p>
            </div>
          ) : (
            <TokenStatsView serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl} />
          )}
        </div>
      );
    }
    if (view === "mcp") {
      return disabledViews.includes("mcp") ? (
        <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
          <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，请在「工具与技能」配置中启用</p>
        </div>
      ) : (
        <MCPView serviceRunning={serviceStatus?.running ?? false} apiBaseUrl={apiBaseUrl} />
      );
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
    if (view === "modules") {
      return (
        <div>
          {_disableToggle("modules", "模块管理")}
          {disabledViews.includes("modules") ? (
            <div className="card" style={{ opacity: 0.5, textAlign: "center", padding: 40 }}>
              <p style={{ color: "#94a3b8", fontSize: 15 }}>此模块已禁用，点击上方开关启用</p>
            </div>
          ) : (
        <div className="card">
          <h2 className="cardTitle">{t("modules.title")}</h2>
          <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 16 }}>{t("modules.desc")}</p>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 16, padding: "10px 14px", background: "var(--warn-bg, #fffbeb)", borderRadius: 8, border: "1px solid var(--warn-border, #fde68a)", fontSize: 13, color: "var(--warn, #92400e)", lineHeight: 1.6 }}>
            <span style={{ fontSize: 16, flexShrink: 0, marginTop: 1 }}>⚠️</span>
            <span>{t("modules.legacyNotice")}</span>
          </div>
          {moduleUninstallPending && currentWorkspaceId && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, padding: "10px 12px", background: "#fef2f2", borderRadius: 8, border: "1px solid #fecaca" }}>
              <span style={{ flex: 1, fontSize: 13 }}>{t("modules.uninstallFailInUse")}</span>
              <button
                type="button"
                className="btnPrimary btnSmall"
                disabled={!!busy}
                onClick={async () => {
                  const { id, name } = moduleUninstallPending;
                  if (!IS_TAURI) { notifyError("模块管理仅限桌面端"); return; }
                  const _b = notifyLoading(t("status.stopping"));
                  try {
                    const ss = await invoke<{ running: boolean; pid: number | null; pidFile: string }>("openakita_service_stop", { workspaceId: currentWorkspaceId });
                    setServiceStatus(ss);
                    await new Promise((r) => setTimeout(r, 1500));
                    await invoke("uninstall_module", { moduleId: id });
                    notifySuccess(t("modules.uninstalled", { name }));
                    setModuleUninstallPending(null);
                    obLoadModules();
                  } catch (e) {
                    notifyError(String(e));
                  } finally {
                    dismissLoading(_b);
                  }
                }}
              >
                {t("modules.stopAndUninstall")}
              </button>
              <button type="button" className="btnSmall" onClick={() => { setModuleUninstallPending(null); }}>{t("common.cancel")}</button>
            </div>
          )}
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
                          if (!IS_TAURI) return;
                          const doUninstall = async () => {
                            await invoke("uninstall_module", { moduleId: m.id });
                            notifySuccess(t("modules.uninstalled", { name: m.name }));
                            obLoadModules();
                            if (serviceStatus?.running) {
                              setModuleRestartPrompt(m.name);
                            }
                          };
                          const _b = notifyLoading(t("modules.uninstalling", { name: m.name }));
                          try {
                            await doUninstall();
                          } catch (e) {
                            const msg = String(e);
                            const isAccessDenied = /拒绝访问|Access denied|os error 5/i.test(msg);
                            if (isAccessDenied && serviceStatus?.running && currentWorkspaceId) {
                              notifyError(t("modules.uninstallFailInUse"));
                              setModuleUninstallPending({ id: m.id, name: m.name });
                              return;
                            }
                            notifyError(msg);
                          } finally {
                            dismissLoading(_b);
                          }
                        }}
                        disabled={m.bundled || !!busy}
                        title={m.bundled ? t("modules.bundledCannotUninstall") : t("modules.uninstall")}
                      >
                        {t("modules.uninstall")}
                      </button>
                    </>
                  ) : (
                    <button
                      className="btnPrimary btnSmall"
                      onClick={async () => {
                        if (!IS_TAURI) return;
                        const _b = notifyLoading(t("modules.installing", { name: m.name }));
                        try {
                          await invoke("install_module", { moduleId: m.id, mirror: null });
                          notifySuccess(t("modules.installSuccess", { name: m.name }));
                          obLoadModules();
                          if (serviceStatus?.running) {
                            setModuleRestartPrompt(m.name);
                          }
                        } catch (e) {
                          notifyError(String(e));
                        } finally {
                          dismissLoading(_b);
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
          )}
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
              envLoadedForWs.current = null;
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
            envLoadedForWs.current = null;
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
          toggleTheme={toggleTheme}
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
            {renderStepContent()}
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

        {/* ── Module restart prompt ── */}
        {moduleRestartPrompt && (
          <ModalOverlay onClose={() => setModuleRestartPrompt(null)}>
            <div className="modalContent" style={{ maxWidth: 400, padding: "28px 24px", borderRadius: 16 }}>
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>{t("modules.restartTitle")}</div>
              <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 20, lineHeight: 1.6 }}>
                {t("modules.restartDesc", { name: moduleRestartPrompt })}
              </div>
              <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
                <button className="btnSmall" onClick={() => setModuleRestartPrompt(null)}>{t("modules.restartLater")}</button>
                <button className="btnPrimary btnSmall" onClick={async () => {
                  setModuleRestartPrompt(null);
                  await applyAndRestart([]);
                }}>{t("modules.restartNow")}</button>
              </div>
            </div>
          </ModalOverlay>
        )}

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

