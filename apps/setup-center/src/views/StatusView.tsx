import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_TAURI, logger } from "../platform";
import { safeFetch } from "../providers";
import { envGet } from "../utils";
import { notifyLoading, notifyError, notifySuccess, dismissLoading } from "../utils/notify";
import { copyToClipboard } from "../utils/clipboard";
import {
  DotGreen, DotGray, DotYellow,
  IM_LOGO_MAP,
  IconAlertCircle,
} from "../icons";
import { Activity, ArrowRight, Loader2, Play, Square, RotateCcw, Power, PowerOff, FolderOpen, Server, Download, Zap, Wrench, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import { TroubleshootPanel } from "../components/TroubleshootPanel";
import { LinkDiagnosticsPanel, type LinkDiagnostic } from "../components/LinkDiagnosticsPanel";
import { SkillConflictsPanel } from "../components/SkillConflictsPanel";
import { ProviderIcon } from "../components/ProviderIcon";
import type { EnvMap, ViewId, WorkspaceSummary } from "../types";
import type { UpdateInfo } from "../platform";

export interface StatusViewProps {
  currentWorkspaceId: string | null;
  workspaces: WorkspaceSummary[];
  envDraft: EnvMap;
  serviceStatus: {
    running: boolean;
    pid: number | null;
    pidFile: string;
    port?: number;
    heartbeatPhase?: string;
    heartbeatHttpReady?: boolean;
    heartbeatImReady?: boolean;
    heartbeatReady?: boolean;
    lastLinkDiagnostic?: LinkDiagnostic | null;
  } | null;
  /**
   * 后端启动阶段。区分 "starting"（自动启动中 / 用户刚点启动）与 "stopped"（确认未启动）
   * 是为了避免老 UI 那种"启动中→未启动→运行中"的红色误报闪烁：
   * starting 期间显示蓝色"正在启动" banner，只有 stopped/error 才显示红色"未启动"。
   */
  backendBootPhase?: "unknown" | "starting" | "running" | "stopped" | "error";
  heartbeatState: "alive" | "suspect" | "degraded" | "dead";
  busy: string | null;
  autostartEnabled: boolean | null;
  autoUpdateEnabled: boolean | null;
  setAutostartEnabled: React.Dispatch<React.SetStateAction<boolean | null>>;
  setAutoUpdateEnabled: React.Dispatch<React.SetStateAction<boolean | null>>;
  endpointSummary: { name: string; provider: string; apiType: string; baseUrl: string; model: string; keyEnv: string; keyPresent: boolean; enabled?: boolean }[];
  endpointHealth: Record<string, { status: string; latencyMs: number | null; error: string | null; errorCategory: string | null; consecutiveFailures: number; cooldownRemaining: number; isExtendedCooldown: boolean; lastCheckedAt: string | null }>;
  setEndpointHealth: React.Dispatch<React.SetStateAction<Record<string, {
    status: string; latencyMs: number | null; error: string | null; errorCategory: string | null;
    consecutiveFailures: number; cooldownRemaining: number; isExtendedCooldown: boolean; lastCheckedAt: string | null;
  }>>>;
  imHealth: Record<string, { status: string; error: string | null; lastCheckedAt: string | null }>;
  setImHealth: React.Dispatch<React.SetStateAction<Record<string, {
    status: string; error: string | null; lastCheckedAt: string | null;
  }>>>;
  skillSummary: { count: number; systemCount: number; externalCount: number } | null;
  serviceLog: { path: string; content: string; truncated: boolean } | null;
  serviceLogRef: React.RefObject<HTMLPreElement | null>;
  logAtBottomRef: React.MutableRefObject<boolean>;
  detectedProcesses: Array<{ pid: number; cmd: string }>;
  setDetectedProcesses: React.Dispatch<React.SetStateAction<Array<{ pid: number; cmd: string }>>>;
  setNewRelease: React.Dispatch<React.SetStateAction<{ latest: string; current: string; url: string } | null>>;
  setUpdateAvailable: React.Dispatch<React.SetStateAction<UpdateInfo | null>>;
  setUpdateProgress: React.Dispatch<React.SetStateAction<{
    status: "idle" | "downloading" | "installing" | "done" | "error";
    percent?: number;
    error?: string;
  }>>;
  shouldUseHttpApi: () => boolean;
  httpApiBase: () => string;
  startLocalServiceWithConflictCheck: (wsId: string) => Promise<boolean>;
  refreshStatus: (overrideDataMode?: "local" | "remote", overrideApiBaseUrl?: string, forceAliveCheck?: boolean) => Promise<void>;
  doStopService: (wsId?: string | null) => Promise<void>;
  waitForServiceDown: (base: string, maxMs?: number) => Promise<boolean>;
  doStartLocalService: (wsId: string) => Promise<void>;
  onOpenRuntimeEnvironment: () => void;
  setView: (view: ViewId) => void;
}

export function StatusView(props: StatusViewProps) {
  const { t } = useTranslation();
  const {
    currentWorkspaceId, workspaces, envDraft,
    serviceStatus, backendBootPhase = "unknown", heartbeatState, busy,
    autostartEnabled, autoUpdateEnabled, setAutostartEnabled, setAutoUpdateEnabled,
    endpointSummary, endpointHealth, setEndpointHealth,
    imHealth, setImHealth,
    skillSummary, serviceLog, serviceLogRef, logAtBottomRef,
    detectedProcesses, setDetectedProcesses,
    setNewRelease, setUpdateAvailable, setUpdateProgress,
    shouldUseHttpApi, httpApiBase,
    startLocalServiceWithConflictCheck, refreshStatus,
    doStopService, waitForServiceDown, doStartLocalService,
    onOpenRuntimeEnvironment,
    setView,
  } = props;

  const [healthChecking, setHealthChecking] = useState<string | null>(null);
  const [imChecking, setImChecking] = useState(false);
  // Structured runtime error surface (e.g. RUNTIME_PERMISSION_DENIED|...)
  // —— Rust 端 `ensure_runtime_layout` 等核心 IO 失败时会把带前缀的错误写到
  // runtime manifest.last_error，本组件读出后渲染指引 banner，让企业 AD /
  // 杀软误杀场景下的用户知道怎么修，而不是看着空白的"已停止"发呆。
  const [runtimeLastError, setRuntimeLastError] = useState<{
    lastError: string | null;
    legacyMode: boolean;
    runtimeRoot: string;
    manifestPath: string;
  } | null>(null);
  const [logLevelFilter, setLogLevelFilter] = useState<Set<string>>(new Set(["INFO", "WARN", "ERROR", "DEBUG"]));
  const [logAtBottom, setLogAtBottom] = useState(true);
  // Local guard for the "Start backend" button. The parent App.tsx exposes a
  // `busy` prop, but it is currently hard-coded to null upstream, so without
  // a local in-flight flag a rapid double-click fires startLocalServiceWithConflictCheck
  // multiple times in parallel and each one queues its own loading toast,
  // producing the "toast spam" the user complained about.
  const [startingService, setStartingService] = useState(false);
  const [memorySubsystem, setMemorySubsystem] = useState<{
    status: string;
    reason?: string | null;
    details?: string | null;
    repair_available?: boolean;
  } | null>(null);
  const [repairOpen, setRepairOpen] = useState(false);

  const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
  const ws = workspaces.find((w) => w.id === effectiveWsId) || workspaces[0] || null;
  const startBackend = async () => {
    if (startingService || !!busy || !effectiveWsId) return;
    setStartingService(true);
    try {
      await startLocalServiceWithConflictCheck(effectiveWsId);
    } finally {
      setStartingService(false);
    }
  };
  // Poll structured runtime error on every backend stop / error. Cheap: just
  // reads ~1KB from disk via Tauri.
  useEffect(() => {
    if (!IS_TAURI) return;
    if (serviceStatus?.running && backendBootPhase !== "error") {
      // 后端已起来且不在错误态：清掉上次残留的提示。
      setRuntimeLastError(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const r = await invoke<{
          lastError: string | null;
          legacyMode: boolean;
          runtimeRoot: string;
          manifestPath: string;
        }>("openakita_runtime_last_error");
        if (!cancelled) setRuntimeLastError(r);
      } catch (e) {
        logger.warn("openakita_runtime_last_error failed", String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [serviceStatus?.running, backendBootPhase]);

  useEffect(() => {
    if (!serviceStatus?.running) {
      setMemorySubsystem(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const res = await safeFetch(`${httpApiBase()}/api/health`, { signal: AbortSignal.timeout(3000) });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setMemorySubsystem(data.memory_subsystem || null);
      } catch {
        // health polling is best-effort; main heartbeat owns service state
      }
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [serviceStatus?.running, httpApiBase]);

  const permissionDenied =
    !!runtimeLastError?.lastError &&
    runtimeLastError.lastError.startsWith("RUNTIME_PERMISSION_DENIED|");
  const memoryDegraded = memorySubsystem?.status === "degraded";
  const memoryRepairRestartRequired =
    memorySubsystem?.status === "repair_completed_restart_required";
  const im = [
    { k: "TELEGRAM_ENABLED", name: "Telegram", required: ["TELEGRAM_BOT_TOKEN"] },
    { k: "FEISHU_ENABLED", name: t("status.feishu"), required: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"] },
    { k: "WEWORK_ENABLED", name: t("status.wework"), required: ["WEWORK_CORP_ID", "WEWORK_TOKEN", "WEWORK_ENCODING_AES_KEY"] },
    { k: "WEWORK_WS_ENABLED", name: t("status.weworkWs"), required: ["WEWORK_WS_BOT_ID", "WEWORK_WS_SECRET"] },
    { k: "DINGTALK_ENABLED", name: t("status.dingtalk"), required: ["DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"] },
    { k: "ONEBOT_ENABLED", name: "OneBot", required: [] },
    { k: "QQBOT_ENABLED", name: "QQ", required: ["QQBOT_APP_ID", "QQBOT_APP_SECRET"] },
    { k: "WECHAT_ENABLED", name: t("status.wechat"), required: ["WECHAT_TOKEN"] },
  ];
  const imStatus = im.map((c) => {
    const enabled = envGet(envDraft, c.k, "false").toLowerCase() === "true";
    const missing = c.required.filter((rk) => !(envGet(envDraft, rk) || "").trim());
    return { ...c, enabled, ok: enabled ? missing.length === 0 : true, missing };
  });

  // ── 启动阶段与"未启动"严格区分 ──
  // showStartingBanner: 蓝色 spinner banner，表达"正在启动 / 初始化中"。
  //   - 后端进程还没起来，但 phase 是 starting
  //   - 或者 HTTP API 已可访问，但 heartbeat/readiness 仍显示 starting/http_ready/starting_im
  //   - 或者 phase 是 unknown 且 serviceStatus 还没探到（首次 mount 的极早期）
  // showNotRunningBanner: 红色"未启动"banner，仅当：
  //   - phase 已经明确转为 stopped 或 error
  //   - 且后端确实没运行
  // 这样就避免了老逻辑里"自动启动到一半 invoke 失败 → setServiceStatus(false)
  // → 红条闪一下 → 后端真起来后又变绿"的诡异闪烁。
  const isRunning = !!serviceStatus?.running;
  const heartbeatPhase = serviceStatus?.heartbeatPhase || "";
  const phaseStarting =
    backendBootPhase === "starting" ||
    (isRunning && serviceStatus?.heartbeatReady === false) ||
    ["starting", "initializing", "http_ready", "starting_im"].includes(heartbeatPhase) ||
    (backendBootPhase === "unknown" && serviceStatus === null);
  const showStartingBanner = IS_TAURI && phaseStarting && effectiveWsId;
  const showNotRunningBanner =
    IS_TAURI &&
    !isRunning &&
    !phaseStarting &&
    (backendBootPhase === "stopped" || backendBootPhase === "error" || (serviceStatus !== null && backendBootPhase === "unknown")) &&
    effectiveWsId;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      {/* Banner: starting / auto-starting backend */}
      {showStartingBanner && (
        <Card className="gap-0 border-primary/30 bg-primary/10 py-0 shadow-sm">
          <CardContent className="flex flex-wrap items-center gap-4 px-5 py-4">
            <div className="spinner" style={{ width: 22, height: 22, flexShrink: 0, color: "var(--brand)" }} />
            <div className="min-w-[180px] flex-1">
              <div className="mb-1 text-sm font-semibold text-primary">
                {busy || (isRunning ? "后端正在完成初始化" : t("status.backendStarting"))}
              </div>
              <div className="text-xs text-primary/80">
                {heartbeatPhase === "starting_im"
                  ? "HTTP API 已就绪，正在启动 IM 通道和后台连接。"
                  : heartbeatPhase === "http_ready"
                    ? "HTTP API 已就绪，后台服务仍在继续初始化。"
                    : t("status.backendStartingHint")}
              </div>
            </div>
          </CardContent>
        </Card>
      )}
      {(memoryDegraded || memoryRepairRestartRequired) && (
        <Card className="gap-0 border-amber-500/40 bg-amber-500/10 py-0 shadow-sm">
          <CardContent className="flex flex-wrap items-center gap-4 px-5 py-4">
            <Wrench className="h-5 w-5 shrink-0 text-amber-600" />
            <div className="min-w-[200px] flex-1">
              <div className="mb-1 text-sm font-semibold text-amber-700 dark:text-amber-400">
                {memoryRepairRestartRequired
                  ? t("status.memoryRepairRestartTitle")
                  : t("status.memoryDegradedTitle")}
              </div>
              <div className="text-xs text-amber-700/80 dark:text-amber-400/80">
                {memoryRepairRestartRequired
                  ? t("status.memoryRepairRestartDesc")
                  : t("status.memoryDegradedDesc", {
                      reason: memorySubsystem?.reason || t("status.unknown"),
                    })}
              </div>
            </div>
            {memoryRepairRestartRequired ? (
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  if (effectiveWsId) {
                    await doStopService(effectiveWsId);
                    await doStartLocalService(effectiveWsId);
                  }
                }}
              >
                {t("status.restart")}
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => setRepairOpen(true)}>
                {t("status.memoryRepairButton")}
              </Button>
            )}
          </CardContent>
        </Card>
      )}
      {repairOpen && (
        <MemoryRepairDialog
          apiBase={httpApiBase()}
          onClose={() => setRepairOpen(false)}
          onDone={async () => {
            setRepairOpen(false);
            await refreshStatus();
          }}
          t={t}
        />
      )}
      {/* Banner: RUNTIME_PERMISSION_DENIED — 企业 AD / 杀软"勒索软件防护"拦截
          runtime 目录创建时，给用户一条可操作的指引。在"未启动" banner 前
          展示，因为这是导致"未启动"的根因，应优先看到。

          走 i18n 的 status.runtimePermissionDenied* 字段；按钮使用专门的
          openakita_open_runtime_root 命令——它在目录尚未创建时会自动回退到
          最近一级存在的祖先，避免通用 show_item_in_folder 抛 "Path does not
          exist"。 */}
      {IS_TAURI && permissionDenied && runtimeLastError && (
        <Card className="gap-0 border-rose-500/40 bg-rose-500/10 py-0 shadow-sm">
          <CardContent className="flex flex-wrap items-center gap-4 px-5 py-4">
            <div className="text-2xl leading-none text-rose-600">&#9940;</div>
            <div className="min-w-[200px] flex-1">
              <div className="mb-1 text-sm font-semibold text-rose-700 dark:text-rose-400">
                {t("status.runtimePermissionDeniedTitle")}
              </div>
              <div className="text-xs text-rose-700/80 dark:text-rose-400/80">
                {runtimeLastError.lastError?.split("|").slice(1).join("|") ||
                  t("status.runtimePermissionDeniedHint")}
              </div>
              <div className="mt-1 text-[11px] text-rose-700/70 dark:text-rose-400/70 break-all">
                {runtimeLastError.runtimeRoot}
              </div>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                try {
                  const r = await invoke<{ opened: string; fellBack: boolean }>(
                    "openakita_open_runtime_root"
                  );
                  if (r.fellBack) {
                    notifySuccess(
                      t("status.runtimePermissionDeniedFallbackToParent", {
                        path: r.opened,
                      })
                    );
                  }
                } catch (e) {
                  notifyError(String(e));
                }
              }}
            >
              <FolderOpen size={14} className="mr-1" />
              {t("status.runtimePermissionDeniedOpen")}
            </Button>
          </CardContent>
        </Card>
      )}
      {/* Banner: backend confirmed not running (hide in web mode — backend is always running) */}
      {showNotRunningBanner && (
        <Card className="gap-0 border-amber-500/40 bg-amber-500/10 py-0 shadow-sm">
          <CardContent className="flex flex-wrap items-center gap-4 px-5 py-4">
            <div className="text-2xl leading-none text-amber-600">&#9888;</div>
            <div className="min-w-[180px] flex-1">
              <div className="mb-1 text-sm font-semibold text-amber-700 dark:text-amber-400">
                {backendBootPhase === "error" ? t("status.backendStartFailed") : t("status.backendNotRunning")}
              </div>
              <div className="text-xs text-amber-700/80 dark:text-amber-400/80">
                {t("status.backendNotRunningHint")}
              </div>
            </div>
          <Button
            size="sm"
            onClick={startBackend}
            disabled={!!busy || startingService}
          >
            {startingService || busy
              ? <><Loader2 className="animate-spin mr-1" size={14} />{busy || t("topbar.starting")}</>
              : <><Play size={14} className="mr-1" />{t("topbar.start")}</>}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onOpenRuntimeEnvironment}
          >
            <ArrowRight size={14} className="mr-1" />
            查看运行环境
          </Button>
          </CardContent>
        </Card>
      )}

      {/* Top: Unified status panel */}
      <Card className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm">
        <div className="statusPanel !border-0 !rounded-none !bg-transparent">
        {/* Service row */}
        <div className="statusPanelRow statusPanelRowService">
          <div className="statusPanelIcon">
            <Server size={18} />
          </div>
          <div className="statusPanelInfo">
            <div className="statusPanelTitle">
              {t("status.service")}
              <Badge variant={
                phaseStarting ? "secondary"
                : heartbeatState === "alive" ? "default"
                : heartbeatState === "degraded" || heartbeatState === "suspect" ? "secondary"
                : isRunning ? "default"
                : "outline"
              } className={`statusBadgeInline ${
                phaseStarting ? "statusBadgeWarn"
                : heartbeatState === "alive" ? "statusBadgeOk"
                : heartbeatState === "degraded" || heartbeatState === "suspect" ? "statusBadgeWarn"
                : isRunning ? "statusBadgeOk"
                : "statusBadgeOff"
              }`}>
                {phaseStarting ? (busy || (isRunning ? "初始化中" : t("topbar.autoStarting")))
                : heartbeatState === "degraded" ? t("status.unresponsive")
                : isRunning ? t("topbar.running")
                : t("topbar.stopped")}
              </Badge>
              {isRunning && !phaseStarting && (
                <Badge variant="default" className="statusBadgeInline statusBadgeOk">
                  环境正常
                </Badge>
              )}
            </div>
            <div className="statusPanelDesc">
              {serviceStatus?.pid ? `PID ${serviceStatus.pid}` : ""}
            </div>
          </div>
          {IS_TAURI && (
          <div className="statusPanelActions">
            {!isRunning && !phaseStarting && effectiveWsId && (
              <Button size="sm" className="statusBtn" onClick={startBackend} disabled={!!busy || startingService}>
                {startingService || busy
                  ? <><Loader2 className="animate-spin" size={13} />{busy || t("topbar.starting")}</>
                  : <><Play size={13} />{t("topbar.start")}</>}
              </Button>
            )}
            {phaseStarting && effectiveWsId && (
              <Badge variant="secondary" className="statusBadgeInline statusBadgeWarn">
                <Loader2 className="animate-spin mr-1" size={12} />
                {t("topbar.autoStarting")}
              </Badge>
            )}
            {serviceStatus?.running && !phaseStarting && effectiveWsId && (<>
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
                  await waitForServiceDown(httpApiBase(), 15000);
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
            <span style={{ fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 4 }}><IconAlertCircle size={13} /> {t("statusExtra.multiProcessWarning", { count: detectedProcesses.length })}</span>
            <span style={{ fontSize: 11, opacity: 0.8 }}>
              ({detectedProcesses.map(p => `PID ${p.pid}`).join(", ")})
            </span>
            <Button size="sm" variant="destructive" style={{ marginLeft: "auto" }} onClick={async () => {
              const _b = notifyLoading(t("statusExtra.stoppingAll"));
              try {
                const stopped = await invoke<number[]>("openakita_stop_all_processes");
                setDetectedProcesses([]);
                notifySuccess(t("statusExtra.stoppedCount", { count: stopped.length }));
                await refreshStatus();
              } catch (e) { notifyError(String(e)); } finally { dismissLoading(_b); }
            }} disabled={!!busy}><Square size={12} className="mr-1" />{t("statusExtra.stopAll")}</Button>
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

        {/* Link diagnostics + per-session cache reset */}
        <LinkDiagnosticsPanel
          httpApiBase={httpApiBase}
          initialDiagnostic={serviceStatus?.lastLinkDiagnostic ?? null}
        />

        {/* Skill registration conflicts (multi-source same name detection) */}
        <SkillConflictsPanel httpApiBase={httpApiBase} />

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
                const { openFileWithDefault } = await import("../platform");
                try { await openFileWithDefault(ws.path); } catch (e) { logger.error("App", "openFileWithDefault failed", { error: String(e) }); }
              }}
            >
              <FolderOpen size={14} />
            </Button>
          )}
        </div>
        </div>
      </Card>

      {/* LLM Endpoints compact table */}
      <Card className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between gap-3 px-5 py-4">
          <div className="min-w-0">
            <CardTitle className="truncate text-sm" title={`${t("status.llmEndpoints")} (${endpointSummary.length})`}>
              {t("status.llmEndpoints")} ({endpointSummary.length})
            </CardTitle>
            <CardDescription className="mt-1 truncate text-xs" title={t("statusExtra.llmEndpointsDesc")}>{t("statusExtra.llmEndpointsDesc")}</CardDescription>
          </div>
          <Button size="sm" variant="outline" className="shrink-0" title={t("status.checkAll")} onClick={async () => {
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
            {healthChecking === "all"
              ? <><Loader2 className="animate-spin mr-1" size={14} /><span className="hidden xl:inline">{t("status.checking")}</span></>
              : <><Activity size={14} className="mr-1" /><span className="hidden xl:inline">{t("status.checkAll")}</span></>}
          </Button>
        </CardHeader>
        <CardContent className="px-0 pb-0">
        {endpointSummary.length === 0 ? (
          <div className="px-5 pb-4 text-sm text-muted-foreground">
            {!serviceStatus?.running
              ? <><Loader2 className="inline animate-spin mr-1" size={13} />{t("status.waitingForBackend")}</>
              : t("status.noEndpoints")}
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="h-9 text-xs">{t("status.endpoint")}</TableHead>
                <TableHead className="h-9 text-xs">{t("status.model")}</TableHead>
                <TableHead className="h-9 w-[64px] text-center text-xs">Key</TableHead>
                <TableHead className="h-9 w-[110px] text-center text-xs">{t("sidebar.status")}</TableHead>
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
                    <span className="inline-flex items-center gap-2 align-middle">
                      <ProviderIcon slug={e.provider} size={16} title={e.provider} />
                      <span>{e.name}</span>
                    </span>
                    {e.enabled === false && <span className="ml-1.5 text-muted-foreground text-[10px] font-bold">{t("llm.disabled")}</span>}
                  </TableCell>
                  <TableCell className="py-2.5 text-muted-foreground text-xs">{e.model}</TableCell>
                  <TableCell className="py-2.5 text-center">
                    <span className="inline-flex items-center justify-center">
                      {e.keyPresent ? <DotGreen /> : <DotGray />}
                    </span>
                  </TableCell>
                  <TableCell className="py-2.5 text-center">
                    <span
                      className="inline-flex items-center justify-center gap-1 text-xs"
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
        </CardContent>
      </Card>

      {/* IM Channels + Skills side by side */}
      <div className="statusGrid2">
        <Card className="gap-0 border-border/80 py-0 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between gap-3 px-5 py-4">
            <CardTitle className="min-w-0 truncate text-sm" title={t("status.imChannels")}>{t("status.imChannels")}</CardTitle>
            <Button size="sm" variant="outline" className="shrink-0" title={t("status.checkAll")} onClick={async () => {
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
              {imChecking
                ? <><Loader2 className="animate-spin mr-1" size={14} /><span className="hidden xl:inline">{t("status.checking")}</span></>
                : <><Activity size={14} className="mr-1" /><span className="hidden xl:inline">{t("status.checkAll")}</span></>}
            </Button>
          </CardHeader>
          <CardContent className="space-y-2 px-5 pb-4 pt-0">
          {imStatus.map((c) => {
            const channelId = c.k.replace("_ENABLED", "").toLowerCase();
            const ih = imHealth[channelId];
            const isOnline = ih && (ih.status === "healthy" || ih.status === "online");
            const isConfigured = ih && ih.status === "configured";
            const effectiveEnabled = ih ? true : c.enabled;
            const serviceRunning = serviceStatus?.running;
            const dot = !effectiveEnabled
              ? "disabled"
              : ih
                ? (isOnline ? "healthy" : isConfigured ? "unknown" : "unhealthy")
                : c.ok ? "unknown" : serviceRunning ? "unknown" : "degraded";
            const LogoComp = IM_LOGO_MAP[channelId];
            const label = !effectiveEnabled
              ? t("status.disabled")
              : ih
                ? (isOnline ? t("status.online") : isConfigured ? t("status.configured") : t("status.offline"))
                : c.ok
                  ? t("status.configured")
                  : serviceRunning ? "—" : t("status.keyMissing");
            return (
              <div key={c.k} className="imStatusRow rounded-lg border border-border/50 bg-muted/20 px-3 py-2">
                <span className="inline-flex h-4 w-4 items-center justify-center">
                  <span className={"healthDot " + dot} />
                </span>
                <span className="inline-flex h-4 w-4 items-center justify-center">
                  {LogoComp && <span style={{ display: "inline-flex", flexShrink: 0 }}><LogoComp size={16} /></span>}
                </span>
                <span style={{ fontWeight: 600, fontSize: 13, minWidth: 0 }}>{c.name}</span>
                <span className="imStatusLabel text-right">{label}</span>
              </div>
            );
          })}
          </CardContent>
        </Card>
        <Card className="gap-0 border-border/80 py-0 shadow-sm">
          <CardHeader className="px-5 py-4">
            <CardTitle className="text-sm">{t("sidebar.skills")}</CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-4 pt-0">
          {!skillSummary && !serviceStatus?.running ? (
            <div className="text-sm text-muted-foreground">
              <Loader2 className="inline animate-spin mr-1" size={13} />{t("status.waitingForBackend")}
            </div>
          ) : skillSummary ? (
            <div className="space-y-2">
              <div className="statusMetric"><span>{t("status.total")}</span><b>{skillSummary.count}</b></div>
              <div className="statusMetric"><span>{t("skills.system")}</span><b>{skillSummary.systemCount}</b></div>
              <div className="statusMetric"><span>{t("skills.external")}</span><b>{skillSummary.externalCount}</b></div>
            </div>
          ) : <div className="text-sm text-muted-foreground">{t("status.skillsNA")}</div>}
          <Button size="sm" variant="outline" className="w-full mt-2.5" onClick={() => setView("skills")}>{t("status.manageSkills")} <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
      </div>

      {/* Service log */}
      {serviceStatus?.running && (
        <Card className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm">
          <CardHeader className="flex flex-row items-center justify-between gap-3 overflow-x-auto px-5 py-4">
            <CardTitle className="min-w-0 shrink-0 truncate text-sm" title={t("status.log")}>{t("status.log")}</CardTitle>
            <div style={{ display: "flex", alignItems: "center", gap: 3, flexShrink: 0, whiteSpace: "nowrap" }}>
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
          </CardHeader>
          <CardContent className="px-5 pb-5 pt-0">
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
                // eslint-disable-next-line no-control-regex
                const sanitized = line.replace(/\x1b\[[\d;?]*[A-Za-z]/g, "").replace(/\r/g, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
                const highlighted = sanitized
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
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function MemoryRepairDialog({
  apiBase,
  onClose,
  onDone,
  t,
}: {
  apiBase: string;
  onClose: () => void;
  onDone: () => Promise<void> | void;
  t: (key: string, vars?: Record<string, unknown>) => string;
}) {
  const [status, setStatus] = useState<any>(null);
  const [selected, setSelected] = useState<string>("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<
    "restore" | "recreate" | "run-recover" | null
  >(null);

  const loadStatus = async () => {
    const res = await safeFetch(`${apiBase}/api/memory/repair/status`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setStatus(data);
    const first = [...(data.backups || []), ...(data.snapshots || [])].find(
      (x: any) => x.integrity === "ok",
    );
    if (first && !selected) setSelected(first.filename);
  };

  useEffect(() => {
    loadStatus().catch((e) => notifyError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  const runAction = (action: "restore" | "recreate" | "run-recover") => {
    if (!status?.confirmation_token) return;
    if (action === "restore" && !selected) {
      notifyError(t("status.memoryRepairSelectBackup"));
      return;
    }
    setPendingAction(action);
  };

  const executeAction = async () => {
    const action = pendingAction;
    if (!action) return;
    if (!status?.confirmation_token) {
      setPendingAction(null);
      return;
    }
    setPendingAction(null);
    setBusyAction(action);
    const toastId = notifyLoading(t("status.memoryRepairRunning"));
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (IS_TAURI) {
        headers["X-OpenAkita-Desktop-Token"] =
          await invoke<string>("openakita_desktop_session_token");
      }
      const body =
        action === "restore"
          ? { source: selected, confirmation_token: status.confirmation_token }
          : { confirmation_token: status.confirmation_token };
      const res = await safeFetch(`${apiBase}/api/memory/repair/${action}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      notifySuccess(t("status.memoryRepairDone"));
      await onDone();
    } catch (e) {
      notifyError(t("status.memoryRepairFailed", { err: String(e) }));
      await loadStatus().catch(() => undefined);
    } finally {
      dismissLoading(toastId);
      setBusyAction(null);
    }
  };

  const candidates = [...(status?.backups || []), ...(status?.snapshots || [])];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <Card className="w-full max-w-2xl border-border/80 shadow-xl">
        <CardHeader>
          <CardTitle>{t("status.memoryRepairTitle")}</CardTitle>
          <CardDescription>{t("status.memoryRepairDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!status ? (
            <div className="text-sm text-muted-foreground">
              <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
              {t("status.checking")}
            </div>
          ) : (
            <>
              <div className="rounded border border-border/60 bg-muted/30 p-3 text-xs">
                <div>{t("status.memoryRepairReason")}: {status.reason || "-"}</div>
                <div className="break-all">DB: {status.db_path}</div>
              </div>
              <div className="max-h-56 overflow-auto rounded border border-border/60">
                {candidates.length === 0 ? (
                  <div className="p-3 text-sm text-muted-foreground">
                    {t("status.memoryRepairNoBackups")}
                  </div>
                ) : (
                  candidates.map((b: any) => (
                    <label key={b.filename} className="flex cursor-pointer items-center gap-3 border-b border-border/40 px-3 py-2 text-sm last:border-b-0">
                      <input
                        type="radio"
                        name="memory-backup"
                        checked={selected === b.filename}
                        onChange={() => setSelected(b.filename)}
                      />
                      <span className="min-w-0 flex-1 truncate">{b.filename}</span>
                      <Badge variant={b.integrity === "ok" ? "default" : "destructive"}>
                        {b.integrity}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {Math.round((b.size_bytes || 0) / 1024)} KB
                      </span>
                    </label>
                  ))
                )}
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <Button variant="outline" onClick={onClose} disabled={!!busyAction}>
                  {t("config.cancel")}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => runAction("restore")}
                  disabled={!!busyAction || !selected}
                >
                  {busyAction === "restore" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("status.memoryRepairRestore")}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => runAction("run-recover")}
                  disabled={!!busyAction || status.recover_method === "unavailable"}
                >
                  {busyAction === "run-recover" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("status.memoryRepairRecover")}
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => runAction("recreate")}
                  disabled={!!busyAction}
                >
                  {busyAction === "recreate" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {t("status.memoryRepairRecreate")}
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <AlertDialog
        open={pendingAction !== null}
        onOpenChange={(open) => {
          if (busyAction) return;
          if (!open) setPendingAction(null);
        }}
      >
        <AlertDialogContent className="sm:max-w-[480px]">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <span className="grid size-8 place-items-center rounded-lg border border-red-500/20 bg-red-500/10 text-red-600">
                <ShieldAlert size={16} />
              </span>
              {pendingAction === "recreate"
                ? t("status.memoryRepairRecreate")
                : pendingAction === "restore"
                  ? t("status.memoryRepairRestore")
                  : t("status.memoryRepairRecover")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingAction === "recreate"
                ? t("status.memoryRepairConfirmRecreate")
                : t("status.memoryRepairConfirm")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={!!busyAction}>
              {t("config.cancel")}
            </AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              disabled={!!busyAction}
              onClick={() => void executeAction()}
            >
              {busyAction && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("status.memoryRepairConfirmAction")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
