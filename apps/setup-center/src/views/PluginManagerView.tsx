import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeFetch } from "../providers";
import { showInFolder, downloadFile } from "../platform";
import { IconCode, IconPlug, IconFileText2, IconPackage, IconBook, IconGear, IconShield, IconFolderOpen, IconDownload, IconTerminal, IconHeartPulse, IconRefresh } from "../icons";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../components/ui/card";
import { Checkbox } from "../components/ui/checkbox";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { cn } from "../lib/utils";

interface PluginInfo {
  id: string;
  name: string;
  version: string;
  type: string;
  category: string;
  permissions?: string[];
  permission_level?: string;
  enabled?: boolean;
  status?: string;
  error?: string;
  description?: string;
  author?: string;
  homepage?: string;
  tags?: string[];
  has_readme?: boolean;
  has_config_schema?: boolean;
  has_icon?: boolean;
  pending_permissions?: string[];
  granted_permissions?: string[];
  // i18n: optional per-language overrides surfaced by the backend.
  // Falls back to `name` / `description` when missing or empty.
  display_name_i18n?: Record<string, string>;
  description_i18n?: Record<string, string>;
  ui_title?: string;
  ui_title_i18n?: Record<string, string>;
}

interface PluginListResponse {
  plugins: PluginInfo[];
  failed: Record<string, string>;
}

interface ConfigProp {
  type?: string;
  title?: string;
  description?: string;
  default?: any;
  enum?: string[];
  items?: { type?: string };
  "x-visible-when"?: Record<string, string | string[]>;
}

interface ConfigSchema {
  type?: string;
  properties?: Record<string, ConfigProp>;
  required?: string[];
}

const PERM_LABELS: Record<string, { zh: string; en: string }> = {
  "tools.register":      { zh: "注册工具",     en: "Register Tools" },
  "hooks.basic":         { zh: "基础钩子",     en: "Basic Hooks" },
  "hooks.message":       { zh: "消息钩子",     en: "Message Hooks" },
  "hooks.retrieve":      { zh: "检索钩子",     en: "Retrieval Hooks" },
  "hooks.all":           { zh: "所有钩子",     en: "All Hooks" },
  "config.read":         { zh: "读取配置",     en: "Read Config" },
  "config.write":        { zh: "写入配置",     en: "Write Config" },
  "data.own":            { zh: "数据存储",     en: "Data Storage" },
  "log":                 { zh: "日志",         en: "Logging" },
  "skill":               { zh: "技能",         en: "Skill" },
  "memory.read":         { zh: "读取记忆",     en: "Read Memory" },
  "memory.write":        { zh: "写入记忆",     en: "Write Memory" },
  "memory.replace":      { zh: "替换记忆",     en: "Replace Memory" },
  "channel.register":    { zh: "注册通道",     en: "Register Channel" },
  "channel.send":        { zh: "发送消息",     en: "Send Messages" },
  "retrieval.register":  { zh: "注册检索源",   en: "Register Retrieval" },
  "search.register":     { zh: "注册搜索后端", en: "Register Search" },
  "routes.register":     { zh: "注册 API 路由", en: "Register API Routes" },
  "brain.access":        { zh: "访问 Brain",   en: "Access Brain" },
  "vector.access":       { zh: "访问向量库",   en: "Access Vector Store" },
  "settings.read":       { zh: "读取设置",     en: "Read Settings" },
  "llm.register":        { zh: "注册 LLM 服务", en: "Register LLM" },
  "system.config.write": { zh: "系统配置写入", en: "System Config Write" },
};

const LEVEL_LABELS: Record<string, { zh: string; en: string }> = {
  basic:    { zh: "基础", en: "basic" },
  advanced: { zh: "高级", en: "advanced" },
  system:   { zh: "系统", en: "system" },
};

function permLabel(perm: string, lang: string): string {
  const entry = PERM_LABELS[perm];
  if (!entry) return perm;
  return lang.startsWith("zh") ? entry.zh : entry.en;
}

function levelLabel(level: string, lang: string): string {
  const entry = LEVEL_LABELS[level];
  if (!entry) return level;
  return lang.startsWith("zh") ? entry.zh : entry.en;
}

const CATEGORY_LABELS: Record<string, { zh: string; en: string }> = {
  all:       { zh: "全部",       en: "All" },
  channel:   { zh: "IM 通道",    en: "Channels" },
  llm:       { zh: "AI 模型",    en: "AI Models" },
  knowledge: { zh: "知识库",     en: "Knowledge" },
  tool:      { zh: "工具",       en: "Tools" },
  memory:    { zh: "记忆",       en: "Memory" },
  hook:      { zh: "钩子",       en: "Hooks" },
  skill:     { zh: "技能",       en: "Skills" },
  mcp:       { zh: "MCP 服务",   en: "MCP Servers" },
};

function categoryLabel(cat: string, lang: string): string {
  const entry = CATEGORY_LABELS[cat];
  if (!entry) return cat;
  return lang.startsWith("zh") ? entry.zh : entry.en;
}

/**
 * Pick a localized string from a plugin's i18n dict, with a string fallback.
 *
 * Resolution order (so the UI never shows an empty cell):
 *   1) exact lang match (e.g. "zh-CN" → dict["zh-CN"])
 *   2) language base match (e.g. "zh-CN" → dict["zh"])
 *   3) "en" if present (most-likely-understood default)
 *   4) any first value in the dict
 *   5) the fallback string (manifest's plain `name` / `description`)
 *
 * Used for plugin display name and description in the manager list and the
 * sidebar; both fields are optional in the manifest.
 */
function pickI18n(
  dict: Record<string, string> | undefined | null,
  lang: string,
  fallback: string,
): string {
  if (dict && typeof dict === "object") {
    if (dict[lang]) return dict[lang];
    const base = lang.split("-")[0];
    if (base && dict[base]) return dict[base];
    if (dict.en) return dict.en;
    const first = Object.values(dict).find((v) => typeof v === "string" && v);
    if (first) return first;
  }
  return fallback;
}

const LEVEL_BADGE_STYLES: Record<string, { color: string; backgroundColor: string }> = {
  basic: { color: "var(--ok, #22c55e)", backgroundColor: "rgba(34, 197, 94, 0.12)" },
  advanced: { color: "var(--warning, #f59e0b)", backgroundColor: "rgba(245, 158, 11, 0.14)" },
  system: { color: "var(--danger, #ef4444)", backgroundColor: "rgba(239, 68, 68, 0.12)" },
};

const PANEL_CARD_CLASS = "rounded-xl border bg-muted/30 p-4";
const FIELD_CLASS_NAME = "flex h-9 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50";

// Plugin install / uninstall / enable / disable can take noticeably longer than
// a regular API call: on Windows uninstall may retry rmtree with exponential
// backoff (clearing read-only bits and waiting for SQLite/HTTP handles to be
// released by GC), and async on_unload + MCP graceful disconnect easily push
// the round-trip past the default 10s safeFetch timeout. Use a longer signal
// here so the UI does not show a misleading "signal timed out" while the
// backend is still cleaning up successfully.
const PLUGIN_OP_TIMEOUT_MS = 60_000;
const longOpSignal = () => AbortSignal.timeout(PLUGIN_OP_TIMEOUT_MS);
const isTimeoutError = (e: unknown): boolean => {
  const msg = e instanceof Error ? e.message : String(e ?? "");
  return /AbortError|signal timed out|timeout|timed out/i.test(msg);
};

function TypeIcon({ type }: { type: string }) {
  const style = { flexShrink: 0, color: "var(--muted)" } as const;
  switch (type) {
    case "python": return <IconCode size={22} style={style} />;
    case "mcp":    return <IconPlug size={22} style={style} />;
    case "skill":  return <IconFileText2 size={22} style={style} />;
    default:       return <IconPackage size={22} style={style} />;
  }
}

function PluginIcon({ plugin, apiBase }: { plugin: PluginInfo; apiBase: string }) {
  const [imgErr, setImgErr] = useState(false);
  if (plugin.has_icon && !imgErr) {
    return (
      <img
        src={`${apiBase}/api/plugins/${plugin.id}/_admin/icon`}
        alt=""
        onError={() => setImgErr(true)}
        style={{ width: 28, height: 28, borderRadius: 6, objectFit: "cover", flexShrink: 0 }}
      />
    );
  }
  return <TypeIcon type={plugin.type} />;
}

interface Props {
  visible: boolean;
  httpApiBase: () => string;
}

export default function PluginManagerView({ visible, httpApiBase }: Props) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notAvailable, setNotAvailable] = useState(false);
  const [installUrl, setInstallUrl] = useState("");
  const [installing, setInstalling] = useState(false);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [readmeCache, setReadmeCache] = useState<Record<string, string>>({});
  const [configPanel, setConfigPanel] = useState<string | null>(null);
  const [configSchema, setConfigSchema] = useState<ConfigSchema | null>(null);
  const [configValues, setConfigValues] = useState<Record<string, any>>({});
  const [configSaving, setConfigSaving] = useState(false);
  const [configMsg, setConfigMsg] = useState("");

  const [permDialog, setPermDialog] = useState<string | null>(null);
  const [granting, setGranting] = useState(false);

  // Per-plugin in-flight action guard. Enable/Disable/Remove all hit the
  // backend with non-trivial latency (uninstall on Windows can take >10s
  // because of file-lock retries + on_unload teardown), so without a guard
  // a single fast double-click queues two parallel requests and we end up
  // showing two toasts (the second one usually a "plugin not found" error
  // because the first request already removed it). Track which action is
  // currently running for each plugin and disable the row's buttons until
  // the request settles.
  const [pendingAction, setPendingAction] = useState<Record<string, "enable" | "disable" | "delete" | "reload">>({});

  const [logsPanel, setLogsPanel] = useState<string | null>(null);
  const [logsContent, setLogsContent] = useState("");

  const [tasksPanel, setTasksPanel] = useState<string | null>(null);
  const [tasksData, setTasksData] = useState<{
    running: number;
    total: number;
    tasks: { name: string; done: boolean; cancelled: boolean; coro: string }[];
  } | null>(null);
  const [tasksLoading, setTasksLoading] = useState(false);

  const [devMode, setDevMode] = useState<"off" | "symlink">("off");
  const [devModeSaving, setDevModeSaving] = useState(false);

  const [toast, setToast] = useState<{ msg: string; type: "ok" | "err" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const showToast = (msg: string, type: "ok" | "err" = "ok") => {
    clearTimeout(toastTimer.current);
    setToast({ msg, type });
    toastTimer.current = setTimeout(() => setToast(null), 3500);
  };
  const [categoryFilter, setCategoryFilter] = useState("all");

  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const apiBaseRef = useRef(httpApiBase);
  apiBaseRef.current = httpApiBase;

  const closeAllPanels = () => {
    setExpandedId(null);
    setConfigPanel(null);
    setPermDialog(null);
    setLogsPanel(null);
    setTasksPanel(null);
  };

  const scrollToCard = (pluginId: string) => {
    requestAnimationFrame(() => {
      cardRefs.current[pluginId]?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const refreshRef = useRef<() => Promise<void>>();

  const fetchPlugins = useCallback(async (showSpinner: boolean) => {
    if (showSpinner) setLoading(true);
    setError("");
    setNotAvailable(false);
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/list`);
      const raw = await resp.json();
      const data: PluginListResponse = raw.data ?? raw;
      setPlugins(data.plugins || []);
      setFailed(data.failed || {});
    } catch (e: any) {
      const msg = e.message || "";
      if (msg.includes("404") || msg.includes("Not Found") || msg.includes("Failed to fetch")) {
        setNotAvailable(true);
      } else {
        setError(msg || t("plugins.failedToLoad"));
      }
    } finally {
      setLoading(false);
    }
  }, [t]);

  refreshRef.current = () => fetchPlugins(false);

  const fetchDevMode = useCallback(async () => {
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/dev-mode`);
      const raw = await resp.json();
      const data = raw.data ?? raw;
      const m = data?.mode === "symlink" ? "symlink" : "off";
      setDevMode(m);
    } catch {
      // Backend may not yet support dev-mode; leave the toggle off.
    }
  }, []);

  const mountedRef = useRef(false);
  useEffect(() => {
    if (visible && !mountedRef.current) {
      mountedRef.current = true;
      fetchPlugins(true);
      fetchDevMode();
    }
  }, [visible, fetchPlugins, fetchDevMode]);

  const toggleDevMode = async (next: boolean) => {
    const target: "off" | "symlink" = next ? "symlink" : "off";
    const prev = devMode;
    setDevMode(target);
    setDevModeSaving(true);
    try {
      await safeFetch(`${apiBaseRef.current()}/api/plugins/dev-mode`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: target }),
      });
      showToast(target === "symlink" ? t("plugins.devModeOnHint") : t("plugins.devModeOffHint"));
    } catch (e: any) {
      setDevMode(prev);
      showToast(e.message, "err");
    } finally {
      setDevModeSaving(false);
    }
  };

  const toggleTasks = async (pluginId: string) => {
    if (tasksPanel === pluginId) {
      setTasksPanel(null);
      return;
    }
    closeAllPanels();
    setTasksPanel(pluginId);
    setTasksData(null);
    scrollToCard(pluginId);
    await loadTasks(pluginId);
  };

  const loadTasks = async (pluginId: string) => {
    setTasksLoading(true);
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/spawned-tasks`);
      const raw = await resp.json();
      const data = raw.data ?? raw;
      setTasksData({
        running: data?.running ?? 0,
        total: data?.total ?? 0,
        tasks: Array.isArray(data?.tasks) ? data.tasks : [],
      });
    } catch (e: any) {
      setTasksData({ running: 0, total: 0, tasks: [] });
      showToast(e.message, "err");
    } finally {
      setTasksLoading(false);
    }
  };

  const updatePluginLocal = (id: string, patch: Partial<PluginInfo>) => {
    setPlugins((prev) => prev.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  };

  const removePluginLocal = (id: string) => {
    setPlugins((prev) => prev.filter((p) => p.id !== id));
    setExpandedId((prev) => (prev === id ? null : prev));
    setConfigPanel((prev) => (prev === id ? null : prev));
    setPermDialog((prev) => (prev === id ? null : prev));
    setLogsPanel((prev) => (prev === id ? null : prev));
    setTasksPanel((prev) => (prev === id ? null : prev));
  };

  const ACTION_LABELS: Record<string, { ok: string; err: string }> = {
    enable:  { ok: t("plugins.toastEnabled"),     err: t("plugins.toastEnableFail") },
    disable: { ok: t("plugins.toastDisabled"),    err: t("plugins.toastDisableFail") },
    delete:  { ok: t("plugins.toastUninstalled"), err: t("plugins.toastUninstallFail") },
    reload:  { ok: t("plugins.toastReloaded"),    err: t("plugins.toastReloadFail") },
  };

  // Notify the Sidebar (and any other listener) that the set of plugin UI apps
  // may have changed, so they can refetch /api/plugins/ui-apps without a restart.
  const notifyAppsChanged = () => {
    try {
      window.dispatchEvent(new CustomEvent("openakita:plugin-apps-changed"));
    } catch { /* ignore */ }
  };

  const handleAction = async (id: string, action: "enable" | "disable" | "delete" | "reload") => {
    // Block re-entry while the previous request for this plugin is still
    // in flight. The button is also visually disabled below, but this
    // belt-and-suspenders check guards against keyboard / programmatic
    // re-entry too.
    if (pendingAction[id]) return;
    setPendingAction((prev) => ({ ...prev, [id]: action }));
    try {
      const method = action === "delete" ? "DELETE" : "POST";
      const url =
        action === "delete"
          ? `${apiBaseRef.current()}/api/plugins/${id}`
          : `${apiBaseRef.current()}/api/plugins/${id}/_admin/${action}`;
      const resp = await safeFetch(url, { method, signal: longOpSignal() });

      if (action === "reload") {
        // Backend now does: unload -> resync from install_source (if known)
        // -> re-import. The response tells us which path was taken so we
        // can give an accurate toast — otherwise users see "Plugin reloaded"
        // and reasonably assume their source-code edits flowed through,
        // even when (e.g.) install_source was unknown and the resync was
        // skipped, which was the original "reload button does nothing" UX.
        let resyncBody: any = null;
        try {
          resyncBody = await resp.json();
        } catch { /* ignore */ }
        const data = resyncBody?.data ?? resyncBody;
        const resynced = Boolean(data?.resynced);
        const resyncMode: string = data?.resync_mode ?? "";

        // Reload may change loaded/failed/error fields and (with dev mode)
        // pick up source-code edits. Easiest correct UI is to refetch the
        // whole list — small payload, avoids subtle local-state drift.
        await fetchPlugins(false);
        // Tell PluginAppHost to bust its iframe cache for THIS plugin.
        // Backend reload_plugin() re-imports Python, but the browser still
        // holds the old HTML/JS/CSS against the previous ?_v= query string,
        // so without this event the UI tab keeps showing the stale build.
        try {
          window.dispatchEvent(
            new CustomEvent("openakita:plugin-reloaded", { detail: { pluginId: id } }),
          );
        } catch { /* ignore */ }

        let toastMsg: string;
        if (resynced && resyncMode === "symlink") {
          toastMsg = t("plugins.toastReloadedResyncSymlink");
        } else if (resynced && resyncMode === "copy") {
          toastMsg = t("plugins.toastReloadedResyncCopy");
        } else if (!resynced) {
          // Reload succeeded but we could not auto-resync — most likely
          // an old install with no recorded source. Tell the user what to
          // do once instead of letting them keep clicking a button that
          // (for them) still doesn't pick up edits.
          toastMsg = t("plugins.toastReloadedNoSource");
        } else {
          toastMsg = ACTION_LABELS[action]?.ok ?? "OK";
        }
        showToast(toastMsg);
        notifyAppsChanged();
        return;
      }

      if (action === "delete") {
        // Backend may answer 207 Multi-Status (partial uninstall — code dir
        // could not be fully removed but db files were cleaned). Surface
        // this as a non-blocking warning so the user understands a restart
        // may be needed for a fully clean state.
        let body: any = null;
        try { body = await resp.json(); } catch { /* ignore */ }
        if (resp.status === 207 || body?.ok === false) {
          removePluginLocal(id);
          const guidance = body?.error?.guidance ?? "";
          const message = body?.error?.message ?? t("plugins.toastUninstalledPartial");
          // Surface the concrete locked-file list so the user can see WHY
          // the directory could not be removed (usually a SQLite WAL or
          // a log file the plugin forgot to close in on_unload).
          const detail = body?.error?.detail ?? "";
          const lines = [message];
          if (guidance) lines.push(guidance);
          if (detail) lines.push(detail);
          showToast(lines.join("\n"), "err");
          notifyAppsChanged();
          return;
        }
        removePluginLocal(id);
      } else {
        updatePluginLocal(id, { enabled: action === "enable" });
      }
      showToast(ACTION_LABELS[action]?.ok ?? "OK");
      notifyAppsChanged();
    } catch (e: any) {
      // A client-side timeout does NOT mean the backend operation failed —
      // long uninstalls (Windows file locks + retries) can outlive our
      // AbortSignal. Show a hint and refresh the list so the user sees the
      // real state instead of a confusing "signal timed out" error.
      if (isTimeoutError(e)) {
        showToast(t("plugins.toastOpStillRunning"), "err");
        await fetchPlugins(false);
        notifyAppsChanged();
        return;
      }
      const msg = ACTION_LABELS[action]?.err ?? e.message;
      showToast(`${msg}: ${e.message}`, "err");
    } finally {
      setPendingAction((prev) => {
        if (!prev[id]) return prev;
        const { [id]: _, ...rest } = prev;
        return rest;
      });
    }
  };

  const handleInstall = async () => {
    if (!installUrl.trim()) return;
    if (!confirm(t("plugins.trustWarning"))) return;
    setInstalling(true);
    setError("");
    try {
      await safeFetch(`${apiBaseRef.current()}/api/plugins/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: installUrl.trim() }),
        signal: longOpSignal(),
      });
      setInstallUrl("");
      showToast(t("plugins.toastInstalled"));
      await fetchPlugins(false);
      notifyAppsChanged();
    } catch (e: any) {
      if (isTimeoutError(e)) {
        showToast(t("plugins.toastOpStillRunning"), "err");
        await fetchPlugins(false);
        notifyAppsChanged();
      } else {
        showToast(e.message, "err");
        setError(e.message);
      }
    } finally {
      setInstalling(false);
    }
  };

  const toggleReadme = async (pluginId: string) => {
    if (expandedId === pluginId) {
      setExpandedId(null);
      return;
    }
    closeAllPanels();
    setExpandedId(pluginId);
    scrollToCard(pluginId);
    if (!readmeCache[pluginId]) {
      try {
        const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/readme`);
        const raw = await resp.json();
        const data = raw.data ?? raw;
        setReadmeCache((prev) => ({ ...prev, [pluginId]: data.readme || t("plugins.noReadme") }));
      } catch {
        setReadmeCache((prev) => ({ ...prev, [pluginId]: t("plugins.readmeLoadFail") }));
      }
    }
  };

  const openConfig = async (pluginId: string) => {
    if (configPanel === pluginId) {
      setConfigPanel(null);
      return;
    }
    closeAllPanels();
    setConfigPanel(pluginId);
    setConfigSchema(null);
    setConfigValues({});
    setConfigMsg("");
    scrollToCard(pluginId);
    try {
      const [schemaResp, configResp] = await Promise.all([
        safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/schema`),
        safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/config`),
      ]);
      const schemaRaw = await schemaResp.json();
      const configRaw = await configResp.json();
      const schemaData = schemaRaw.data ?? schemaRaw;
      const configData = configRaw.data ?? configRaw;
      setConfigSchema(schemaData.schema ?? null);
      setConfigValues(configData || {});
    } catch {
      setConfigSchema(null);
      setConfigValues({});
      setConfigMsg(t("plugins.configLoadFail"));
    }
  };

  const saveConfig = async (pluginId: string) => {
    setConfigSaving(true);
    setConfigMsg("");
    try {
      await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configValues),
      });
      setConfigMsg(t("plugins.configSaved"));
    } catch (e: any) {
      setConfigMsg(e.message || t("plugins.configSaveFail"));
    } finally {
      setConfigSaving(false);
    }
  };

  const handleGrantPermissions = async (pluginId: string, perms: string[]) => {
    setGranting(true);
    try {
      await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/permissions/grant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ permissions: perms, reload: true }),
      });
      await fetchPlugins(false);
      notifyAppsChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGranting(false);
    }
  };

  const handleRevokePermission = async (pluginId: string, perm: string) => {
    setGranting(true);
    try {
      await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/permissions/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ permissions: [perm], reload: true }),
      });
      await fetchPlugins(false);
      notifyAppsChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGranting(false);
    }
  };

  const handleOpenFolder = async (pluginId: string) => {
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/open-folder`, {
        method: "POST",
      });
      const raw = await resp.json();
      const data = raw.data ?? raw;
      if (data.path) {
        await showInFolder(data.path);
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleExport = async (pluginId: string) => {
    try {
      const url = `${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/export`;
      await downloadFile(url, `${pluginId}.zip`);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const toggleLogs = async (pluginId: string) => {
    if (logsPanel === pluginId) {
      setLogsPanel(null);
      return;
    }
    closeAllPanels();
    setLogsPanel(pluginId);
    scrollToCard(pluginId);
    setLogsContent("");
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/logs?lines=200`);
      const raw = await resp.json();
      const data = raw.data ?? raw;
      setLogsContent(data.logs || t("plugins.noLogs"));
    } catch {
      setLogsContent(t("plugins.logsLoadFail"));
    }
  };

  const refreshLogs = async (pluginId: string) => {
    setLogsContent("");
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/_admin/logs?lines=200`);
      const raw = await resp.json();
      const data = raw.data ?? raw;
      setLogsContent(data.logs || t("plugins.noLogs"));
    } catch {
      setLogsContent(t("plugins.logsLoadFail"));
    }
  };

  const installBtnDisabled = installing || !installUrl.trim() || notAvailable;
  const pluginsWithPending = plugins.filter(
    (p) => (p.pending_permissions?.length ?? 0) > 0
  );
  const failedEntries = Object.entries(failed);
  const categoryTabs = ["all", ...Array.from(new Set(plugins.map((p) => p.category || p.type || "tool"))).sort()];
  const filteredPlugins = plugins.filter(
    (p) => categoryFilter === "all" || (p.category || p.type || "tool") === categoryFilter
  );

  if (!visible) return null;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      <Card className="gap-0 overflow-hidden border-border/80 bg-gradient-to-br from-primary/5 via-background to-background py-0 shadow-sm">
        <CardHeader className="gap-3 px-6 py-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 items-start gap-4">
              <div className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <IconPlug size={24} />
              </div>
              <div className="min-w-0 space-y-2">
                <div className="flex min-w-0 items-center gap-3">
                  <CardTitle className="truncate text-xl tracking-tight" title={t("plugins.title")}>
                    {t("plugins.title")}
                  </CardTitle>
                  <Badge
                    variant="secondary"
                    className="max-w-[140px] shrink overflow-hidden rounded-full px-3 py-1 text-ellipsis whitespace-nowrap text-xs"
                    title={t("plugins.installed", { count: plugins.length })}
                  >
                    {t("plugins.installed", { count: plugins.length })}
                  </Badge>
                </div>
                <CardDescription className="max-w-3xl text-sm leading-6">
                  {t("plugins.desc")}
                </CardDescription>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 border-t px-6 py-4 sm:grid-cols-3">
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="text-xs text-muted-foreground">{t("plugins.title")}</div>
            <div className="mt-2 text-2xl font-semibold">{plugins.length}</div>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="text-xs text-muted-foreground">{t("plugins.permPendingTitle")}</div>
            <div className="mt-2 text-2xl font-semibold text-amber-600">{pluginsWithPending.length}</div>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="text-xs text-muted-foreground">{t("plugins.failedToLoad")}</div>
            <div className="mt-2 text-2xl font-semibold text-destructive">{failedEntries.length}</div>
          </div>
        </CardContent>
      </Card>

      <Card className="gap-0 border-border/80 py-0 shadow-sm">
        <CardHeader className="gap-2 px-6 py-4">
          <CardTitle className="text-base">{t("plugins.install")}</CardTitle>
          <CardDescription>{t("plugins.installPlaceholder")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 px-6 py-4">
          <div className="flex flex-col gap-3 lg:flex-row">
            <Input
              type="text"
              placeholder={t("plugins.installPlaceholder")}
              value={installUrl}
              onChange={(e) => setInstallUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !installBtnDisabled && handleInstall()}
              disabled={notAvailable}
              className="flex-1"
            />
            <div className="flex flex-wrap gap-2">
              <Button onClick={handleInstall} disabled={installBtnDisabled}>
                {installing ? t("plugins.installing") : t("plugins.install")}
              </Button>
              <Button variant="outline" onClick={() => fetchPlugins(false)}>
                {t("plugins.refresh")}
              </Button>
            </div>
          </div>

          {!notAvailable && (
            <label
              className={cn(
                "flex items-start gap-3 rounded-xl border bg-muted/20 p-4 cursor-pointer transition-colors",
                devMode === "symlink" && "border-primary/40 bg-primary/5",
              )}
            >
              <Checkbox
                checked={devMode === "symlink"}
                onCheckedChange={(v) => toggleDevMode(Boolean(v))}
                disabled={devModeSaving}
                className="mt-0.5"
              />
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                  <IconCode size={14} />
                  {t("plugins.devModeTitle")}
                  {devMode === "symlink" && (
                    <Badge variant="outline" className="border-primary/30 bg-primary/10 text-primary">
                      {t("plugins.devModeOn")}
                    </Badge>
                  )}
                </div>
                <div className="text-xs leading-5 text-muted-foreground">
                  {t("plugins.devModeDesc")}
                </div>
              </div>
            </label>
          )}

          {!notAvailable && plugins.length > 0 && (
            <div className="rounded-xl border bg-muted/20 p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="text-sm font-medium text-foreground">{categoryLabel(categoryFilter, lang)}</div>
                <div className="text-xs text-muted-foreground">
                  {t("plugins.installed", { count: filteredPlugins.length })}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {categoryTabs.map((cat) => {
                  const active = categoryFilter === cat;
                  const count = cat === "all"
                    ? plugins.length
                    : plugins.filter((p) => (p.category || p.type || "tool") === cat).length;
                  return (
                    <Button
                      key={cat}
                      size="sm"
                      variant={active ? "default" : "outline"}
                      className="rounded-full px-4"
                      onClick={() => setCategoryFilter(cat)}
                    >
                      {categoryLabel(cat, lang)}
                      <span className="ml-1 opacity-70">{count}</span>
                    </Button>
                  );
                })}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {notAvailable && (
        <Card className="border-amber-500/40 bg-amber-500/5 shadow-sm">
          <CardContent className="py-5 text-sm leading-6 text-foreground">
            {t("plugins.notAvailable")}
          </CardContent>
        </Card>
      )}

      {error && (
        <Card className="border-destructive/40 bg-destructive/5 shadow-sm">
          <CardContent className="py-4 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      )}

      {pluginsWithPending.length > 0 && (
        <Card className="gap-0 border-amber-500/40 bg-amber-500/5 py-0 shadow-sm">
          <CardHeader className="gap-2 px-6 py-4">
            <div className="flex items-center gap-2 text-amber-600">
              <IconShield size={16} />
              <CardTitle className="text-base text-foreground">{t("plugins.permPendingTitle")}</CardTitle>
            </div>
            <CardDescription>{t("plugins.permPendingDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 px-6 py-4">
            {pluginsWithPending.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between gap-3 rounded-xl border border-amber-500/20 bg-background/80 p-4"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground" title={pickI18n(p.display_name_i18n, lang, p.name)}>{pickI18n(p.display_name_i18n, lang, p.name)}</div>
                  <div
                    className="mt-1 truncate text-sm leading-6 text-muted-foreground"
                    title={(p.pending_permissions || []).map((pp) => permLabel(pp, lang)).join(", ")}
                  >
                    {(p.pending_permissions || []).map((pp) => permLabel(pp, lang)).join(", ")}
                  </div>
                </div>
                <Button
                  className="shrink-0"
                  onClick={() => handleGrantPermissions(p.id, p.pending_permissions || [])}
                  disabled={granting}
                >
                  {granting ? "..." : t("plugins.grantAll")}
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {loading && !notAvailable ? (
        <Card className="shadow-sm">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {t("plugins.loading")}
          </CardContent>
        </Card>
      ) : !notAvailable && filteredPlugins.length === 0 && failedEntries.length === 0 ? (
        <Card className="shadow-sm">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {t("plugins.noPlugins")}
          </CardContent>
        </Card>
      ) : !notAvailable ? (
        <div className="flex flex-col gap-4">
          {filteredPlugins.map((p) => {
            const hasPending = (p.pending_permissions?.length ?? 0) > 0;
            const showBody =
              (!!p.error && !hasPending) ||
              permDialog === p.id ||
              expandedId === p.id ||
              configPanel === p.id ||
              logsPanel === p.id;
            const badgeStyle = p.permission_level ? LEVEL_BADGE_STYLES[p.permission_level] : null;
            // Localize the plugin's display name and description per the active
            // UI language, falling back to the plain manifest fields when the
            // plugin doesn't ship i18n overrides.
            const displayName = pickI18n(p.display_name_i18n, lang, p.name);
            const displayDesc = pickI18n(p.description_i18n, lang, p.description || "");

            return (
              <div
                key={p.id}
                ref={(el) => { cardRefs.current[p.id] = el; }}
              >
                <Card className={cn(
                  "gap-0 overflow-hidden border-border/80 py-0 shadow-sm transition-shadow hover:shadow-md",
                  hasPending && "border-amber-500/50"
                )}>
                  <CardHeader className="gap-1.5 px-5 py-2">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex min-w-0 gap-4">
                        <div className={cn(
                          "flex size-10 shrink-0 items-center justify-center rounded-xl border bg-muted/40",
                          hasPending && "border-amber-500/40 bg-amber-500/10"
                        )}>
                          <PluginIcon plugin={p} apiBase={apiBaseRef.current()} />
                        </div>
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex min-w-0 items-center gap-2">
                            <CardTitle className="min-w-0 truncate py-0.5 text-base leading-tight" title={displayName}>{displayName}</CardTitle>
                            {p.permission_level && (
                              <Badge
                                variant="outline"
                                className="max-w-[96px] shrink overflow-hidden whitespace-nowrap border-0 text-ellipsis"
                                title={levelLabel(p.permission_level, lang)}
                                style={badgeStyle ? { color: badgeStyle.color, backgroundColor: badgeStyle.backgroundColor } : undefined}
                              >
                                {levelLabel(p.permission_level, lang)}
                              </Badge>
                            )}
                            {hasPending && (
                              <Badge
                                variant="outline"
                                className="max-w-[120px] shrink overflow-hidden whitespace-nowrap border-amber-500/40 bg-amber-500/10 text-amber-600 text-ellipsis"
                                title={t("plugins.permPending")}
                              >
                                {t("plugins.permPending")}
                              </Badge>
                            )}
                            {p.status === "failed" && (
                              <Badge variant="destructive" className="max-w-[96px] shrink overflow-hidden whitespace-nowrap text-ellipsis" title={t("plugins.failed")}>
                                {t("plugins.failed")}
                              </Badge>
                            )}
                          </div>

                          <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                            <Badge variant="secondary" className="max-w-[90px] shrink overflow-hidden whitespace-nowrap font-mono text-ellipsis" title={`v${p.version}`}>v{p.version}</Badge>
                            <Badge
                              variant="outline"
                              className="max-w-[120px] shrink overflow-hidden whitespace-nowrap text-ellipsis"
                              title={categoryLabel(p.category || p.type || "tool", lang)}
                            >
                              {categoryLabel(p.category || p.type || "tool", lang)}
                            </Badge>
                            {p.author && (
                              <Badge variant="outline" className="max-w-[140px] shrink overflow-hidden whitespace-nowrap text-ellipsis" title={p.author}>
                                {p.author}
                              </Badge>
                            )}
                          </div>

                          {displayDesc && (
                            <CardDescription className="max-w-3xl text-sm leading-5">
                              <span className="block line-clamp-2" title={displayDesc}>
                                {displayDesc}
                              </span>
                            </CardDescription>
                          )}

                          {(p.tags?.length ?? 0) > 0 && (
                            <div className="flex flex-wrap gap-1.5">
                              {(p.tags || []).map((tag) => (
                                <Badge key={tag} variant="outline" className="text-xs text-muted-foreground">
                                  {tag}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="flex shrink-0 items-center gap-2">
                        {(p.permissions?.length ?? 0) > 0 && (
                          <Button
                            size="icon-sm"
                            variant={permDialog === p.id ? "secondary" : "outline"}
                            title={t("plugins.permManage")}
                            aria-label={t("plugins.permManage")}
                            className={hasPending ? "border-amber-500/40 text-amber-600" : undefined}
                            onClick={() => {
                              if (permDialog === p.id) { setPermDialog(null); return; }
                              closeAllPanels();
                              setPermDialog(p.id);
                              scrollToCard(p.id);
                            }}
                          >
                            <IconShield size={14} />
                          </Button>
                        )}
                        {p.has_readme && (
                          <Button
                            size="icon-sm"
                            variant={expandedId === p.id ? "secondary" : "outline"}
                            title={t("plugins.viewDocs")}
                            aria-label={t("plugins.viewDocs")}
                            onClick={() => toggleReadme(p.id)}
                          >
                            <IconBook size={14} />
                          </Button>
                        )}
                        {p.has_config_schema && (
                          <Button
                            size="icon-sm"
                            variant={configPanel === p.id ? "secondary" : "outline"}
                            title={t("plugins.settings")}
                            aria-label={t("plugins.settings")}
                            onClick={() => openConfig(p.id)}
                          >
                            <IconGear size={14} />
                          </Button>
                        )}
                        <Button
                          size="icon-sm"
                          variant="outline"
                          title={t("plugins.openFolder")}
                          aria-label={t("plugins.openFolder")}
                          onClick={() => handleOpenFolder(p.id)}
                        >
                          <IconFolderOpen size={14} />
                        </Button>
                        <Button
                          size="icon-sm"
                          variant="outline"
                          title={t("plugins.export")}
                          aria-label={t("plugins.export")}
                          onClick={() => handleExport(p.id)}
                        >
                          <IconDownload size={14} />
                        </Button>
                        <Button
                          size="icon-sm"
                          variant={logsPanel === p.id ? "secondary" : "outline"}
                          title={t("plugins.viewLogs")}
                          aria-label={t("plugins.viewLogs")}
                          onClick={() => toggleLogs(p.id)}
                        >
                          <IconTerminal size={14} />
                        </Button>
                        <Button
                          size="icon-sm"
                          variant={tasksPanel === p.id ? "secondary" : "outline"}
                          title={t("plugins.viewTasks")}
                          aria-label={t("plugins.viewTasks")}
                          onClick={() => toggleTasks(p.id)}
                        >
                          <IconHeartPulse size={14} />
                        </Button>
                        {p.enabled && (
                          <Button
                            size="icon-sm"
                            variant="outline"
                            title={t("plugins.reloadHint")}
                            aria-label={t("plugins.reload")}
                            disabled={!!pendingAction[p.id]}
                            onClick={() => handleAction(p.id, "reload")}
                          >
                            <IconRefresh size={14} />
                          </Button>
                        )}
                      </div>
                    </div>
                  </CardHeader>

                  {showBody && (
                    <CardContent className="space-y-4 border-t px-6 py-4">
                      {p.error && !hasPending && (
                        <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                          {p.error}
                        </div>
                      )}

                      {permDialog === p.id && (
                        <div className={cn(PANEL_CARD_CLASS, hasPending && "border-amber-500/40 bg-amber-500/5")}>
                          <div className="mb-4 flex flex-wrap items-center gap-2">
                            <IconShield size={14} style={{ color: hasPending ? "var(--warning, #f59e0b)" : "var(--ok, #22c55e)" }} />
                            <div className="text-sm font-semibold text-foreground">{t("plugins.permTitle")}</div>
                            {!hasPending && (
                              <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-600">
                                {t("plugins.permAllGranted")}
                              </Badge>
                            )}
                          </div>
                          <div className="mb-4 text-sm leading-6 text-muted-foreground">
                            {t("plugins.permDesc")}
                          </div>
                          <div className="space-y-2">
                            {(p.permissions || []).map((perm) => {
                              const isGranted = p.granted_permissions?.includes(perm) ?? false;
                              const isPending = p.pending_permissions?.includes(perm) ?? false;
                              const isBasic = ["tools.register", "hooks.basic", "config.read", "config.write", "data.own", "log", "skill"].includes(perm);
                              return (
                                <div
                                  key={perm}
                                  className="flex items-center justify-between gap-2 rounded-lg border bg-background/80 px-4 py-3"
                                >
                                  <div className="min-w-0 truncate text-sm text-foreground" title={permLabel(perm, lang)}>
                                    {permLabel(perm, lang)}
                                  </div>
                                  <div className="flex shrink-0 items-center gap-2">
                                    {isBasic ? (
                                      <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-600">
                                        {t("plugins.permAuto")}
                                      </Badge>
                                    ) : isGranted ? (
                                      <>
                                        <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-600">
                                          {t("plugins.permGranted")}
                                        </Badge>
                                        <Button
                                          size="xs"
                                          variant="outline"
                                          className="border-destructive/40 text-destructive hover:text-destructive"
                                          onClick={() => handleRevokePermission(p.id, perm)}
                                          disabled={granting}
                                        >
                                          {t("plugins.permRevoke")}
                                        </Button>
                                      </>
                                    ) : isPending ? (
                                      <>
                                        <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-600">
                                          {t("plugins.permPending")}
                                        </Badge>
                                        <Button
                                          size="xs"
                                          variant="outline"
                                          className="border-emerald-500/40 text-emerald-600 hover:text-emerald-700"
                                          onClick={() => handleGrantPermissions(p.id, [perm])}
                                          disabled={granting}
                                        >
                                          {t("plugins.permGrant")}
                                        </Button>
                                      </>
                                    ) : null}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                          <div className="mt-4 flex flex-wrap gap-2">
                            {hasPending && (
                              <Button
                                onClick={() => handleGrantPermissions(p.id, p.pending_permissions || [])}
                                disabled={granting}
                              >
                                {granting ? "..." : t("plugins.grantAllAndReload")}
                              </Button>
                            )}
                            <Button variant="outline" onClick={() => setPermDialog(null)}>
                              {t("common.close")}
                            </Button>
                          </div>
                        </div>
                      )}

                      {expandedId === p.id && (
                        <div
                          className={cn("plugin-readme-content overflow-y-auto text-sm leading-6 text-foreground", PANEL_CARD_CLASS)}
                          style={{ maxHeight: 420 }}
                        >
                          {readmeCache[p.id] ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{readmeCache[p.id]}</ReactMarkdown>
                          ) : (
                            t("plugins.loading")
                          )}
                        </div>
                      )}

                      {configPanel === p.id && (
                        <div className={PANEL_CARD_CLASS}>
                          <div className="mb-4 text-sm font-semibold text-foreground">
                            {t("plugins.settings")}
                          </div>
                          {configSchema?.properties ? (
                            <>
                              <div className="space-y-4">
                                {Object.entries(configSchema.properties).map(([key, prop]) => {
                                  const isRequired = configSchema.required?.includes(key);
                                  const visibleWhen = prop["x-visible-when"];
                                  if (visibleWhen) {
                                    const hidden = Object.entries(visibleWhen).some(([depKey, expected]) => {
                                      const cur = configValues[depKey] ?? configSchema.properties?.[depKey]?.default;
                                      if (Array.isArray(expected)) return !expected.includes(cur);
                                      return cur !== expected;
                                    });
                                    if (hidden) return null;
                                  }

                                  return (
                                    <div key={key} className="space-y-2">
                                      <Label className="flex flex-wrap items-center gap-1 text-sm text-foreground">
                                        <span>{prop.title || key}</span>
                                        {isRequired && <span className="text-destructive">*</span>}
                                        {prop.title && <span className="text-xs font-normal text-muted-foreground">({key})</span>}
                                      </Label>
                                      {prop.description && (
                                        <div className="text-xs leading-5 text-muted-foreground">
                                          {prop.description}
                                        </div>
                                      )}
                                      {prop.enum ? (
                                        <select
                                          value={configValues[key] ?? prop.default ?? ""}
                                          onChange={(e) => setConfigValues((v) => ({ ...v, [key]: e.target.value }))}
                                          className={FIELD_CLASS_NAME}
                                        >
                                          <option value="">--</option>
                                          {prop.enum.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                                        </select>
                                      ) : prop.type === "boolean" ? (
                                        <Label className="flex items-center gap-3 rounded-lg border bg-background/80 px-3 py-3">
                                          <Checkbox
                                            checked={!!configValues[key]}
                                            onCheckedChange={(checked) => setConfigValues((v) => ({ ...v, [key]: !!checked }))}
                                          />
                                          <span>{prop.title || key}</span>
                                        </Label>
                                      ) : prop.type === "integer" || prop.type === "number" ? (
                                        <Input
                                          type="number"
                                          value={configValues[key] ?? prop.default ?? ""}
                                          onChange={(e) => setConfigValues((v) => ({ ...v, [key]: Number(e.target.value) }))}
                                        />
                                      ) : prop.type === "array" ? (
                                        <Input
                                          type="text"
                                          placeholder={t("plugins.arrayHint")}
                                          value={Array.isArray(configValues[key]) ? configValues[key].join(", ") : (configValues[key] ?? "")}
                                          onChange={(e) => setConfigValues((v) => ({
                                            ...v,
                                            [key]: e.target.value.split(",").map((s: string) => s.trim()).filter(Boolean),
                                          }))}
                                        />
                                      ) : (
                                        <Input
                                          type={/password|secret|_token$|_key$|^api_key$|^access_token$/i.test(key) ? "password" : "text"}
                                          value={configValues[key] ?? prop.default ?? ""}
                                          placeholder={prop.default != null ? String(prop.default) : ""}
                                          onChange={(e) => setConfigValues((v) => ({ ...v, [key]: e.target.value }))}
                                        />
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                              <div className="mt-5 flex flex-wrap items-center gap-3">
                                <Button onClick={() => saveConfig(p.id)} disabled={configSaving}>
                                  {configSaving ? t("plugins.saving") : t("plugins.saveConfig")}
                                </Button>
                                {configMsg && (
                                  <span
                                    className="text-sm"
                                    style={{ color: configMsg === t("plugins.configSaved") ? "var(--ok, #22c55e)" : "var(--error, #f87171)" }}
                                  >
                                    {configMsg}
                                  </span>
                                )}
                              </div>
                            </>
                          ) : (
                            <div className="text-sm text-muted-foreground">
                              {t("plugins.noConfigSchema")}
                              <pre className="mt-3 rounded-lg border bg-background p-3 text-xs text-foreground">
                                {JSON.stringify(configValues, null, 2) || "{}"}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}

                      {logsPanel === p.id && (
                        <div className={PANEL_CARD_CLASS}>
                          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                              <IconTerminal size={14} style={{ color: "var(--muted)" }} />
                              {t("plugins.logsTitle")}
                            </div>
                            <Button size="sm" variant="outline" onClick={() => refreshLogs(p.id)}>
                              {t("plugins.refresh")}
                            </Button>
                          </div>
                          <pre
                            className="max-h-[360px] overflow-y-auto rounded-lg border bg-slate-950 p-3 text-xs leading-6 text-slate-100"
                            style={{ wordBreak: "break-all", whiteSpace: "pre-wrap", fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace" }}
                          >
                            {logsContent || t("plugins.loading")}
                          </pre>
                        </div>
                      )}

                      {tasksPanel === p.id && (
                        <div className={PANEL_CARD_CLASS}>
                          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                              <IconHeartPulse size={14} style={{ color: "var(--muted)" }} />
                              {t("plugins.tasksTitle")}
                              {tasksData && (
                                <Badge variant="outline" className="ml-1 text-xs">
                                  {t("plugins.tasksRunning", {
                                    running: tasksData.running,
                                    total: tasksData.total,
                                  })}
                                </Badge>
                              )}
                            </div>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => loadTasks(p.id)}
                              disabled={tasksLoading}
                            >
                              {t("plugins.refresh")}
                            </Button>
                          </div>
                          <div className="mb-3 text-xs leading-5 text-muted-foreground">
                            {t("plugins.tasksDesc")}
                          </div>
                          {tasksLoading && !tasksData ? (
                            <div className="rounded-lg border bg-background p-3 text-xs text-muted-foreground">
                              {t("plugins.loading")}
                            </div>
                          ) : tasksData && tasksData.tasks.length === 0 ? (
                            <div className="rounded-lg border bg-background p-3 text-xs text-muted-foreground">
                              {t("plugins.tasksEmpty")}
                            </div>
                          ) : tasksData ? (
                            <div className="space-y-2">
                              {tasksData.tasks.map((task, idx) => (
                                <div
                                  key={`${task.name}-${idx}`}
                                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-background/80 px-3 py-2 text-xs"
                                >
                                  <div className="min-w-0 flex-1">
                                    <div className="truncate font-medium text-foreground" title={task.name}>
                                      {task.name}
                                    </div>
                                    <div className="mt-0.5 truncate text-muted-foreground" title={task.coro}>
                                      {task.coro || "-"}
                                    </div>
                                  </div>
                                  <Badge
                                    variant="outline"
                                    className={cn(
                                      "shrink-0 text-[10px]",
                                      task.cancelled
                                        ? "border-rose-500/40 bg-rose-500/10 text-rose-600"
                                        : task.done
                                        ? "border-slate-400/40 bg-slate-400/10 text-slate-500"
                                        : "border-emerald-500/40 bg-emerald-500/10 text-emerald-600",
                                    )}
                                  >
                                    {task.cancelled
                                      ? t("plugins.tasksCancelled")
                                      : task.done
                                      ? t("plugins.tasksDone")
                                      : t("plugins.tasksAlive")}
                                  </Badge>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      )}
                    </CardContent>
                  )}

                  <CardFooter className="flex items-center justify-between gap-3 border-t px-5 !pt-2 pb-2">
                    <div className="min-w-0 truncate text-xs text-muted-foreground" title={p.id}>
                      {p.id}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      {(() => {
                        const inflight = pendingAction[p.id];
                        const isToggleEnable = p.enabled === false;
                        const toggleAction = isToggleEnable ? "enable" : "disable";
                        const toggleLabel = inflight === toggleAction
                          ? (isToggleEnable ? t("plugins.enabling") : t("plugins.disabling"))
                          : (isToggleEnable ? t("plugins.enable") : t("plugins.disable"));
                        const removeLabel = inflight === "delete" ? t("plugins.removing") : t("plugins.remove");
                        return (
                          <>
                            <Button
                              size="sm"
                              variant={isToggleEnable ? "default" : "outline"}
                              onClick={() => handleAction(p.id, toggleAction)}
                              disabled={!!inflight}
                            >
                              {toggleLabel}
                            </Button>
                            <Button
                              size="sm"
                              variant="destructive"
                              onClick={() => handleAction(p.id, "delete")}
                              disabled={!!inflight}
                            >
                              {removeLabel}
                            </Button>
                          </>
                        );
                      })()}
                    </div>
                  </CardFooter>
                </Card>
              </div>
            );
          })}

          {failedEntries.length > 0 && (
            <Card className="border-destructive/40 bg-destructive/5 shadow-sm">
              <CardHeader className="gap-2">
                <CardTitle className="text-base text-foreground">{t("plugins.failedToLoad")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {failedEntries.map(([id, reason]) => (
                  <div
                    key={id}
                    className="rounded-xl border border-destructive/20 bg-background/80 p-4"
                  >
                    <div className="font-medium text-foreground">{id}</div>
                    <div className="mt-1 text-sm leading-6 text-destructive">{reason}</div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      ) : null}

      {/* Toast notification */}
      {toast && (
        <div
          onClick={() => setToast(null)}
          style={{
            position: "fixed", bottom: 32, left: "50%", transform: "translateX(-50%)",
            padding: "10px 24px", borderRadius: 8, fontSize: 13, cursor: "pointer",
            background: toast.type === "ok" ? "var(--ok, #22c55e)" : "var(--danger, #ef4444)",
            color: "#fff", boxShadow: "0 4px 16px rgba(0,0,0,0.18)", zIndex: 9999,
            maxWidth: 420, textAlign: "center", whiteSpace: "pre-line",
            animation: "fadeIn 0.2s ease",
          }}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
