import { useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_TAURI, logger, relaunchApp } from "../platform";
import { safeFetch } from "../providers";
import { envGet } from "../utils";
import { notifyLoading, notifyError, notifySuccess, dismissLoading } from "../utils/notify";
import { copyToClipboard } from "../utils/clipboard";
import {
  DotGreen, DotGray, DotYellow,
  IM_LOGO_MAP,
} from "../icons";
import { Loader2, Play, Square, RotateCcw, Power, PowerOff, FolderOpen, Activity, ArrowRight, Server, Download, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { TroubleshootPanel } from "../components/TroubleshootPanel";
import type { EnvMap, WorkspaceSummary, ViewId } from "../types";
import type { UpdateInfo } from "../platform";

export interface StatusViewProps {
  currentWorkspaceId: string | null;
  workspaces: WorkspaceSummary[];
  envDraft: EnvMap;
  serviceStatus: { running: boolean; pid: number | null; pidFile: string; port?: number } | null;
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
  setView: React.Dispatch<React.SetStateAction<ViewId>>;
}

export function StatusView(props: StatusViewProps) {
  const { t } = useTranslation();
  const {
    currentWorkspaceId, workspaces, envDraft,
    serviceStatus, heartbeatState, busy,
    autostartEnabled, autoUpdateEnabled, setAutostartEnabled, setAutoUpdateEnabled,
    endpointSummary, endpointHealth, setEndpointHealth,
    imHealth, setImHealth,
    skillSummary, serviceLog, serviceLogRef, logAtBottomRef,
    detectedProcesses, setDetectedProcesses,
    setNewRelease, setUpdateAvailable, setUpdateProgress,
    shouldUseHttpApi, httpApiBase,
    startLocalServiceWithConflictCheck, refreshStatus,
    doStopService, waitForServiceDown, doStartLocalService,
    setView,
  } = props;

  const [healthChecking, setHealthChecking] = useState<string | null>(null);
  const [imChecking, setImChecking] = useState(false);
  const [logLevelFilter, setLogLevelFilter] = useState<Set<string>>(new Set(["INFO", "WARN", "ERROR", "DEBUG"]));
  const [logAtBottom, setLogAtBottom] = useState(true);

  const effectiveWsId = currentWorkspaceId || workspaces[0]?.id || null;
  const ws = workspaces.find((w) => w.id === effectiveWsId) || workspaces[0] || null;
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

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      {/* Banner: backend not running (hide during initial probe; hide in web mode — backend is always running) */}
      {IS_TAURI && !serviceStatus?.running && serviceStatus !== null && effectiveWsId && (
        <Card className="gap-0 border-amber-500/40 bg-amber-500/10 py-0 shadow-sm">
          <CardContent className="flex flex-wrap items-center gap-4 px-5 py-4">
            <div className="text-2xl leading-none text-amber-600">&#9888;</div>
            <div className="min-w-[180px] flex-1">
              <div className="mb-1 text-sm font-semibold text-amber-700 dark:text-amber-400">
                {t("status.backendNotRunning")}
              </div>
              <div className="text-xs text-amber-700/80 dark:text-amber-400/80">
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
          </CardContent>
        </Card>
      )}
      {/* Banner: auto-starting backend (shown while serviceStatus is null and busy with auto-start) */}
      {IS_TAURI && serviceStatus === null && !!busy && effectiveWsId && (
        <Card className="gap-0 border-primary/30 bg-primary/10 py-0 shadow-sm">
          <CardContent className="flex flex-wrap items-center gap-4 px-5 py-4">
            <div className="spinner" style={{ width: 22, height: 22, flexShrink: 0, color: "var(--brand)" }} />
            <div className="min-w-[180px] flex-1">
              <div className="mb-1 text-sm font-semibold text-primary">
                {busy}
              </div>
              <div className="text-xs text-primary/80">
                {t("status.backendNotRunningHint")}
              </div>
            </div>
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
                  if (IS_TAURI) {
                    await relaunchApp();
                  } else {
                    await doStartLocalService(effectiveWsId);
                  }
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
          <div>
            <CardTitle className="text-sm">{t("status.llmEndpoints")} ({endpointSummary.length})</CardTitle>
            <CardDescription className="mt-1 text-xs">模型端点状态与健康检查</CardDescription>
          </div>
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
                    {e.name}
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
            <CardTitle className="text-sm">{t("status.imChannels")}</CardTitle>
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
          </CardHeader>
          <CardContent className="space-y-2 px-5 pb-4 pt-0">
          {imStatus.map((c) => {
            const channelId = c.k.replace("_ENABLED", "").toLowerCase();
            const ih = imHealth[channelId];
            const isOnline = ih && (ih.status === "healthy" || ih.status === "online");
            const effectiveEnabled = ih ? true : c.enabled;
            const serviceRunning = serviceStatus?.running;
            const dot = !effectiveEnabled ? "disabled" : ih ? (isOnline ? "healthy" : "unhealthy") : c.ok ? "unknown" : serviceRunning ? "unknown" : "degraded";
            const LogoComp = IM_LOGO_MAP[channelId];
            const label = !effectiveEnabled
              ? t("status.disabled")
              : ih
                ? (isOnline ? t("status.online") : t("status.offline"))
                : c.ok
                  ? t("status.configured")
                  : serviceRunning ? "—" : t("status.keyMissing");
            return (
              <div key={c.k} className="imStatusRow rounded-lg border border-border/50 bg-muted/20 px-3 py-2">
                <span className="inline-flex h-4 w-4 items-center justify-center">
                  <span className={"healthDot " + dot} />
                </span>
                <span className="inline-flex h-4 w-4 items-center justify-center">
                  {LogoComp && <span style={{ display: "inline-flex", flexShrink: 0 }}>{LogoComp({ size: 16 })}</span>}
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
            <CardTitle className="text-sm">Skills</CardTitle>
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
          <CardHeader className="flex flex-row items-center justify-between gap-3 px-5 py-4">
            <CardTitle className="text-sm">{t("status.log")}</CardTitle>
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
