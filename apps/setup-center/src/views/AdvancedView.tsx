/**
 * Advanced settings view — extracted from App.tsx renderAdvanced().
 *
 * Owns its own local state for system info, backup, migration, factory reset,
 * and hub API URL.  Shared state (envDraft, service status, etc.) is injected
 * via props.
 */

import { Fragment, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke, IS_TAURI } from "../platform";
import { safeFetch } from "../providers";
import { joinPath, envGet, envSet } from "../utils";
import { notifySuccess, notifyError, notifyLoading, dismissLoading } from "../utils/notify";
import { FieldText, FieldBool, FieldSelect } from "../components/EnvFields";
import { Section } from "../components/Section";
import { WebPasswordManager } from "../components/WebPasswordManager";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction,
} from "@/components/ui/alert-dialog";
import type { EnvMap, PlatformInfo, WorkspaceSummary, ViewId } from "../types";

export interface AdvancedViewProps {
  envDraft: EnvMap;
  setEnvDraft: React.Dispatch<React.SetStateAction<EnvMap>>;
  busy: string | null;
  workspaces: WorkspaceSummary[];
  currentWorkspaceId: string | null;
  serviceStatus: { running: boolean; pid: number | null; pidFile: string; port?: number } | null;
  dataMode: "local" | "remote";
  info: PlatformInfo | null;
  storeVisible: boolean;
  setStoreVisible: (v: boolean) => void;
  desktopVersion: string;
  shouldUseHttpApi: () => boolean;
  httpApiBase: () => string;
  askConfirm: (msg: string, onConfirm: () => void) => void;
  refreshAll: () => Promise<void>;
  restartService: () => Promise<void>;
  setView: React.Dispatch<React.SetStateAction<ViewId>>;
}

export function AdvancedView(props: AdvancedViewProps) {
  const {
    envDraft, setEnvDraft, busy,
    workspaces, currentWorkspaceId, serviceStatus, dataMode, info,
    storeVisible, setStoreVisible, desktopVersion,
    shouldUseHttpApi, httpApiBase, askConfirm,
    refreshAll, restartService, setView,
  } = props;

  const { t } = useTranslation();

  // ── Field helpers (same pattern as App.tsx) ──
  const _envBase = { envDraft, onEnvChange: setEnvDraft, busy };
  const FT = (p: { k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password" }) =>
    <FieldText key={p.k} {...p} {..._envBase} />;
  const FB = (p: { k: string; label: string; help?: string; defaultValue?: boolean }) =>
    <FieldBool key={p.k} {...p} {..._envBase} />;
  const FS = (p: { k: string; label: string; options: { value: string; label: string }[]; help?: string }) =>
    <FieldSelect key={p.k} {...p} {..._envBase} />;

  // ── Local state (previously in App.tsx top-level, only used here) ──
  const [advSysInfo, setAdvSysInfo] = useState<Record<string, string> | null>(null);
  const [advLoading, setAdvLoading] = useState<Record<string, boolean>>({});
  const [hubApiUrl, setHubApiUrl] = useState<string>("");

  const [backupHistory, setBackupHistory] = useState<Array<{ filename: string; path: string; size_bytes: number; created_at: string; manifest?: any }>>([]);
  const [backupShowHistory, setBackupShowHistory] = useState(false);

  const [factoryResetOpen, setFactoryResetOpen] = useState(false);
  const [factoryResetConfirmText, setFactoryResetConfirmText] = useState("");

  const [migrateTargetPath, setMigrateTargetPath] = useState("");
  const [migratePreflight, setMigratePreflight] = useState<{
    sourcePath: string; targetPath: string; sourceSizeMb: number; targetFreeMb: number;
    canMigrate: boolean; reason: string;
    entries: Array<{ name: string; isDir: boolean; sizeMb: number; existsAtTarget: boolean }>;
  } | null>(null);
  const [migrateBusy, setMigrateBusy] = useState(false);
  const [migrateCurrentRoot, setMigrateCurrentRoot] = useState("");
  const [migrateCustomRoot, setMigrateCustomRoot] = useState<string | null>(null);

  // ── Auto-load on mount ──
  const loadedRef = useRef(false);
  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;

    const apiUrl = shouldUseHttpApi() ? httpApiBase() : null;
    if (apiUrl) {
      setAdvLoading((p) => ({ ...p, sysinfo: true }));
      safeFetch(`${apiUrl}/api/system-info`, { signal: AbortSignal.timeout(8_000) })
        .then((r) => r.json())
        .then((data) => {
          const sysInfo: Record<string, string> = {};
          if (data.os) sysInfo["OS"] = data.os;
          if (data.openakita_version) sysInfo["Backend"] = data.openakita_version;
          setAdvSysInfo(sysInfo);
        })
        .catch(() => {})
        .finally(() => setAdvLoading((p) => ({ ...p, sysinfo: false })));

      safeFetch(`${apiUrl}/api/config/env`, { signal: AbortSignal.timeout(5_000) })
        .then((r) => r.json())
        .then((data) => {
          if (data.env?.HUB_API_URL) setHubApiUrl(data.env.HUB_API_URL);
          if (data.env?.HUB_ENABLED != null) {
            const enabled = data.env.HUB_ENABLED === "true" || data.env.HUB_ENABLED === "True";
            setStoreVisible(enabled);
            localStorage.setItem("openakita_storeVisible", String(enabled));
          }
        })
        .catch(() => {});
    }

    if (IS_TAURI) {
      invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>("get_root_dir_info")
        .then((rootInfo) => {
          setMigrateCurrentRoot(rootInfo.currentRoot);
          setMigrateCustomRoot(rootInfo.customRoot);
        })
        .catch(() => {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Async actions ──

  async function fetchSystemInfo() {
    const url = shouldUseHttpApi() ? httpApiBase() : null;
    if (!url) { notifyError(t("adv.needService")); return; }
    setAdvLoading((p) => ({ ...p, sysinfo: true }));
    try {
      const res = await safeFetch(`${url}/api/system-info`, { signal: AbortSignal.timeout(8_000) });
      const data = await res.json();
      const sysInfo: Record<string, string> = {};
      if (data.os) sysInfo[t("adv.sysOs")] = data.os;
      if (data.openakita_version) sysInfo[t("adv.sysVersion")] = data.openakita_version;
      setAdvSysInfo(sysInfo);
    } catch (e) { notifyError(String(e)); }
    finally { setAdvLoading((p) => ({ ...p, sysinfo: false })); }
  }

  // ── Workspace paths ──
  const opsWs = workspaces.find(w => w.id === (currentWorkspaceId || "default"));
  const opsWsPath = opsWs?.path || "";
  const opsLogsPath = opsWsPath ? joinPath(opsWsPath, "logs") : "";
  const opsIdentityPath = opsWsPath ? joinPath(opsWsPath, "identity") : "";
  const opsPathRows = [
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

  // ── Backup ──

  async function runBackupNow() {
    if (!currentWorkspaceId) return;
    let outputDir = envGet(envDraft, "BACKUP_PATH");
    if (!outputDir) {
      try {
        const { openFileDialog } = await import("../platform");
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
      const { openFileDialog } = await import("../platform");
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
      const { openFileDialog } = await import("../platform");
      const selected = await openFileDialog({ directory: true, title: t("adv.backupPath") });
      if (selected) {
        setEnvDraft((prev) => envSet(prev, "BACKUP_PATH", selected));
      }
    } catch (e) { notifyError(String(e)); }
  }

  // ── Migration ──

  async function runMigratePreflight() {
    if (!migrateTargetPath.trim()) { notifyError(t("adv.migrateTargetPlaceholder")); return; }
    setMigrateBusy(true);
    try {
      const res = await invoke<NonNullable<typeof migratePreflight>>("preflight_migrate_root", { targetPath: migrateTargetPath.trim() });
      setMigratePreflight(res);
    } catch (e: any) {
      notifyError(String(e));
      setMigratePreflight(null);
    } finally {
      setMigrateBusy(false);
    }
  }

  async function browseMigratePath() {
    try {
      const { openFileDialog } = await import("../platform");
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
      const res = await invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>(
        "set_custom_root_dir", { path: migrateTargetPath.trim(), migrate: true }
      );
      setMigrateCurrentRoot(res.currentRoot);
      setMigrateCustomRoot(res.customRoot);
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
      const res = await invoke<{ defaultRoot: string; currentRoot: string; customRoot: string | null }>(
        "set_custom_root_dir", { path: null, migrate: true }
      );
      setMigrateCurrentRoot(res.currentRoot);
      setMigrateCustomRoot(res.customRoot);
      setMigratePreflight(null);
      setMigrateTargetPath("");
      dismissLoading(_busyId);
      setMigrateBusy(false);
      notifySuccess(t("adv.migrateResetDone", { path: res.currentRoot }));
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

  // ── Render ──

  return (
    <>
      {/* ── Card 1: 系统配置（桌面通知 / 会话 / 日志） ── */}
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

        <Section title={t("config.toolsNetwork")} className="mt-2">
          <div className="grid3">
            {FT({ k: "HTTP_PROXY", label: "HTTP_PROXY", placeholder: "http://127.0.0.1:7890" })}
            {FT({ k: "HTTPS_PROXY", label: "HTTPS_PROXY", placeholder: "http://127.0.0.1:7890" })}
            {FT({ k: "ALL_PROXY", label: "ALL_PROXY", placeholder: "socks5://..." })}
          </div>
          <div className="grid2 mt-2">
            {FB({ k: "FORCE_IPV4", label: t("config.toolsForceIPv4"), help: t("config.toolsForceIPv4Help") })}
          </div>
        </Section>
      </div>

      {/* ── Card 3: 平台与云服务 ── */}
      <div className="card" style={{ marginTop: 12 }}>
        <h3 style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>{t("adv.platformTitle")}</h3>

        <Section title={t("adv.hubTitle")}
          toggle={
            <Switch
              checked={storeVisible}
              onCheckedChange={(v) => {
                setStoreVisible(v);
                localStorage.setItem("openakita_storeVisible", String(v));
                if (shouldUseHttpApi()) {
                  safeFetch(`${httpApiBase()}/api/config/env`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ entries: { HUB_ENABLED: String(v) } }),
                  }).catch(() => {});
                } else {
                  setEnvDraft((prev) => envSet(prev, "HUB_ENABLED", String(v)));
                }
              }}
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
