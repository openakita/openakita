import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Activity, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { invoke } from "../platform";
import { joinPath } from "../utils";
import { dismissLoading, notifyError, notifyLoading, notifySuccess } from "../utils/notify";
import type { PlatformInfo } from "../types";

type ToolProbe = { available?: boolean; path?: string; version?: string };

export type RuntimeDiagnostics = {
  summary?: string;
  environment?: {
    runtime?: {
      mode?: string;
      seed_dirs?: string[];
      workspace_dependency_cache?: string;
      python_abi?: string;
      wheel_tag?: string;
      bootstrap_python_seed_packaged?: boolean;
      bootstrap_python_seed?: { packaged?: boolean; version?: string; path?: string };
      bootstrap_node_seed_packaged?: boolean;
    };
    toolchain?: {
      python?: { abi?: string; wheelTag?: string; managed?: string; agent?: string; seedPackaged?: boolean };
      node?: {
        managed_node?: string;
        managed_bin?: string;
        seedPackaged?: boolean;
        node?: ToolProbe;
        npm?: ToolProbe;
        corepack?: ToolProbe;
        pnpm?: ToolProbe;
        yarn?: ToolProbe;
        workspace_cache?: string;
        npm_cache?: string;
        npm_prefix?: string;
        corepack_home?: string;
      };
    };
  };
};

export type RuntimeEnvironmentPanelProps = {
  serviceStatus: {
    running: boolean;
    pid: number | null;
    pidFile: string;
    port?: number;
    heartbeatPhase?: string;
    heartbeatReady?: boolean;
  } | null;
  backendBootPhase: "unknown" | "starting" | "running" | "stopped" | "error";
  installProgress: { stage: string; percent: number } | null;
  info: PlatformInfo | null;
  runtimeDiag: RuntimeDiagnostics | null;
  runtimeDiagChecking: boolean;
  venvStatus: string;
  indexUrl: string;
  installLiveLog: string;
  busy: string | null;
  currentWorkspaceId: string | null;
  refreshRuntimeDiagnostics: () => Promise<void>;
  doStopService: (wsId?: string | null) => Promise<void>;
  doStartLocalService: (wsId: string) => Promise<void>;
};

export function RuntimeEnvironmentPanel({
  serviceStatus,
  backendBootPhase,
  installProgress,
  info,
  runtimeDiag,
  runtimeDiagChecking,
  venvStatus,
  indexUrl,
  installLiveLog,
  busy,
  currentWorkspaceId,
  refreshRuntimeDiagnostics,
  doStopService,
  doStartLocalService,
}: RuntimeEnvironmentPanelProps) {
  const { t } = useTranslation();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const backendReady = serviceStatus?.running && backendBootPhase === "running" && serviceStatus?.heartbeatReady !== false;
  const heartbeatPhase = serviceStatus?.heartbeatPhase || "";
  const stage = installProgress?.stage
    || (heartbeatPhase === "starting_im" ? "HTTP API 已就绪，正在启动 IM 通道和后台连接"
      : heartbeatPhase === "http_ready" ? "HTTP API 已就绪，后台服务仍在继续初始化"
      : backendBootPhase === "starting" ? t("status.backendStarting")
      : backendReady ? "运行环境已就绪"
      : serviceStatus?.running ? "后端正在完成初始化"
      : backendBootPhase === "error" ? t("status.backendStartFailed")
      : "等待启动后端");
  const percent = installProgress?.percent
    ?? (backendBootPhase === "starting" ? 60
      : backendReady ? 100
      : serviceStatus?.running ? 85
      : 0);
  const runtimeRoot = info?.openakitaRootDir
    ? joinPath(info.openakitaRootDir, "runtime")
    : "~/.openakita/runtime";
  const appVenvHint = joinPath(runtimeRoot, "app-venv");
  const agentVenvHint = joinPath(runtimeRoot, "agent-venv");
  const runtimeLogHint = joinPath(joinPath(runtimeRoot, "logs"), "bootstrap.log");
  const nodeInfo = runtimeDiag?.environment?.toolchain?.node;
  const pythonInfo = runtimeDiag?.environment?.toolchain?.python;
  const runtimeInfo = runtimeDiag?.environment?.runtime;
  const pythonAbi = pythonInfo?.abi || runtimeInfo?.python_abi || runtimeInfo?.bootstrap_python_seed?.version;
  const pythonSeedSignals = [
    pythonInfo?.seedPackaged,
    runtimeInfo?.bootstrap_python_seed_packaged,
    runtimeInfo?.bootstrap_python_seed?.packaged,
  ];
  const pythonSeedPackaged = pythonSeedSignals.includes(true)
    ? true
    : pythonSeedSignals.includes(false)
      ? false
      : undefined;
  const nodeSeedPackaged = nodeInfo?.seedPackaged ?? runtimeInfo?.bootstrap_node_seed_packaged;
  const dependencySummary = [
    `Python ${pythonAbi || t("status.unknown")}`,
    `Node ${nodeInfo?.node?.version || t("status.notChecked")}`,
    `npm ${nodeInfo?.npm?.version || t("status.notChecked")}`,
  ];
  if (nodeInfo?.managed_node) dependencySummary.push(t("status.managedNodeAvailable"));
  if (nodeSeedPackaged === false) dependencySummary.push(t("status.nodeSeedNotPackaged"));
  if (pythonSeedPackaged === false) dependencySummary.push(t("status.pythonSeedNotPackaged"));
  if (runtimeInfo?.seed_dirs?.length) dependencySummary.push(t("status.readonlySeedEnabled"));
  const runtimeDetailItems = [
    ["App venv", appVenvHint],
    ["Agent venv", agentVenvHint],
    ["默认镜像", indexUrl || "https://mirrors.aliyun.com/pypi/simple/"],
    ["日志路径", runtimeLogHint],
    ...(nodeInfo?.workspace_cache ? [[t("status.workspaceCache"), nodeInfo.workspace_cache]] : []),
  ];

  const onRepair = async () => {
    if (!currentWorkspaceId) return;
    const _b = notifyLoading(t("status.runtimeRepairing"));
    try {
      try { await doStopService(currentWorkspaceId); } catch { /* ignore */ }
      const report = await invoke<string>("repair_runtime_env");
      notifySuccess(report.split("\n").slice(0, 3).join("\n"));
      await doStartLocalService(currentWorkspaceId);
    } catch (e) {
      notifyError(t("status.runtimeRepairFailed", { err: String(e) }));
    } finally {
      dismissLoading(_b);
    }
  };

  return (
    <div className="rounded-lg border border-border/70 bg-muted/20 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-[220px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="border-blue-300/70 bg-blue-500/10 text-blue-700 dark:text-blue-300">
              {stage}
            </Badge>
            <span className="text-sm font-medium">
              {venvStatus || (backendReady ? "环境正常" : serviceStatus?.running ? "后端初始化中" : "尚未启动")}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            桌面端会优先创建 app runtime venv 和 agent tools venv；失败时回退到 legacy PyInstaller 兼容模式。
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={refreshRuntimeDiagnostics}
            disabled={runtimeDiagChecking || !serviceStatus?.running}
            title={t("status.workspaceDependencies")}
          >
            {runtimeDiagChecking ? <Loader2 className="mr-1 animate-spin" size={14} /> : <Activity className="mr-1" size={14} />}
            {t("status.check")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!!busy || !currentWorkspaceId || backendBootPhase === "starting"}
            onClick={() => currentWorkspaceId && doStartLocalService(currentWorkspaceId)}
          >
            重试启动
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={!!busy || !currentWorkspaceId}
            onClick={onRepair}
            title={t("status.runtimeRepairHint")}
          >
            {t("status.runtimeRepairTitle")}
          </Button>
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-blue-500 transition-all"
          style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
        />
      </div>
      <div className="mt-2.5 rounded-lg border border-border/70 bg-background/70 px-3 py-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="min-w-0 flex-1 truncate text-muted-foreground" title={dependencySummary.join(" · ")}>
            {dependencySummary.join(" · ")}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-xs"
            onClick={() => setDetailsOpen((v) => !v)}
          >
            {detailsOpen ? "隐藏详情" : "查看详情"}
            {detailsOpen ? <ChevronDown className="ml-1" size={13} /> : <ChevronRight className="ml-1" size={13} />}
          </Button>
        </div>
        {detailsOpen && (
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            {runtimeDetailItems.map(([label, value]) => (
              <div
                key={label}
                className="min-w-0 rounded-lg border border-border/60 bg-background/70 px-3 py-2"
                title={value}
              >
                <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {label}
                </div>
                <div className="mt-1 truncate font-mono text-[11px] text-foreground/80">
                  {value}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {installLiveLog && (
        <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-200">
          {installLiveLog.slice(-4000)}
        </pre>
      )}
    </div>
  );
}

export function RuntimeEnvironmentDialog({
  open,
  onOpenChange,
  ...panelProps
}: RuntimeEnvironmentPanelProps & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl max-h-[85vh] overflow-y-auto" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>OpenAkita 运行环境</DialogTitle>
          <DialogDescription>
            查看 runtime venv、agent tools venv、Node/npm 与种子包状态，并在异常时修复运行时。
          </DialogDescription>
        </DialogHeader>
        <RuntimeEnvironmentPanel {...panelProps} />
      </DialogContent>
    </Dialog>
  );
}
