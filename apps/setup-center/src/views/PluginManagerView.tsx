import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeFetch } from "../providers";
import { showInFolder, downloadFile } from "../platform";
import { IconCode, IconPlug, IconFileText2, IconPackage, IconBook, IconGear, IconShield, IconFolderOpen, IconDownload, IconTerminal } from "../icons";

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
}

interface PluginListResponse {
  plugins: PluginInfo[];
  failed: Record<string, string>;
}

interface ConfigSchema {
  type?: string;
  properties?: Record<string, {
    type?: string;
    title?: string;
    description?: string;
    default?: any;
    enum?: string[];
    items?: { type?: string };
  }>;
  required?: string[];
}

const LEVEL_COLORS: Record<string, string> = {
  basic: "var(--ok, #22c55e)",
  advanced: "var(--warning, #f59e0b)",
  system: "var(--danger, #ef4444)",
};

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

function TypeIcon({ type }: { type: string }) {
  const style = { flexShrink: 0, color: "var(--muted)" } as const;
  switch (type) {
    case "python": return <IconCode size={18} style={style} />;
    case "mcp":    return <IconPlug size={18} style={style} />;
    case "skill":  return <IconFileText2 size={18} style={style} />;
    default:       return <IconPackage size={18} style={style} />;
  }
}

function PluginIcon({ plugin, apiBase }: { plugin: PluginInfo; apiBase: string }) {
  const [imgErr, setImgErr] = useState(false);
  if (plugin.has_icon && !imgErr) {
    return (
      <img
        src={`${apiBase}/api/plugins/${plugin.id}/icon`}
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

  const [logsPanel, setLogsPanel] = useState<string | null>(null);
  const [logsContent, setLogsContent] = useState("");

  const apiBaseRef = useRef(httpApiBase);
  apiBaseRef.current = httpApiBase;

  const refreshRef = useRef<() => Promise<void>>();

  const doRefresh = useCallback(async () => {
    setLoading(true);
    setError("");
    setNotAvailable(false);
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/list`);
      const data: PluginListResponse = await resp.json();
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

  refreshRef.current = doRefresh;

  const mountedRef = useRef(false);
  useEffect(() => {
    if (visible && !mountedRef.current) {
      mountedRef.current = true;
      doRefresh();
    }
  }, [visible, doRefresh]);

  useEffect(() => {
    if (visible && mountedRef.current) {
      /* skip: already loaded on mount */
    }
  }, [visible]);

  const manualRefresh = useCallback(() => doRefresh(), [doRefresh]);

  const handleAction = async (id: string, action: "enable" | "disable" | "delete") => {
    try {
      const method = action === "delete" ? "DELETE" : "POST";
      const url =
        action === "delete"
          ? `${apiBaseRef.current()}/api/plugins/${id}`
          : `${apiBaseRef.current()}/api/plugins/${id}/${action}`;
      await safeFetch(url, { method });
      await doRefresh();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleInstall = async () => {
    if (!installUrl.trim()) return;
    setInstalling(true);
    setError("");
    try {
      await safeFetch(`${apiBaseRef.current()}/api/plugins/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: installUrl.trim() }),
      });
      setInstallUrl("");
      await doRefresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setInstalling(false);
    }
  };

  const toggleReadme = async (pluginId: string) => {
    if (expandedId === pluginId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(pluginId);
    if (!readmeCache[pluginId]) {
      try {
        const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/readme`);
        const data = await resp.json();
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
    setConfigPanel(pluginId);
    setConfigMsg("");
    try {
      const [schemaResp, configResp] = await Promise.all([
        safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/schema`),
        safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/config`),
      ]);
      const schemaData = await schemaResp.json();
      const configData = await configResp.json();
      setConfigSchema(schemaData.schema || null);
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
      await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/config`, {
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
      await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/permissions/grant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ permissions: perms, reload: true }),
      });
      setPermDialog(null);
      await doRefresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setGranting(false);
    }
  };

  const handleOpenFolder = async (pluginId: string) => {
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/open-folder`, {
        method: "POST",
      });
      const data = await resp.json();
      if (data.path) {
        await showInFolder(data.path);
      }
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleExport = async (pluginId: string) => {
    try {
      const url = `${apiBaseRef.current()}/api/plugins/${pluginId}/export`;
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
    setLogsPanel(pluginId);
    setLogsContent("");
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/logs?lines=200`);
      const data = await resp.json();
      setLogsContent(data.logs || t("plugins.noLogs"));
    } catch {
      setLogsContent(t("plugins.logsLoadFail"));
    }
  };

  const refreshLogs = async (pluginId: string) => {
    setLogsContent("");
    try {
      const resp = await safeFetch(`${apiBaseRef.current()}/api/plugins/${pluginId}/logs?lines=200`);
      const data = await resp.json();
      setLogsContent(data.logs || t("plugins.noLogs"));
    } catch {
      setLogsContent(t("plugins.logsLoadFail"));
    }
  };

  const installBtnDisabled = installing || !installUrl.trim() || notAvailable;

  if (!visible) return null;

  const pluginsWithPending = plugins.filter(
    (p) => (p.pending_permissions?.length ?? 0) > 0
  );

  return (
    <div style={{ padding: "24px", maxWidth: 900 }}>
      <h2 style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 8, color: "var(--fg)" }}>
        {t("plugins.title")}
        <span style={{ fontSize: 12, color: "var(--muted)", fontWeight: 400 }}>
          {t("plugins.installed", { count: plugins.length })}
        </span>
      </h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 20 }}>
        {t("plugins.desc")}
      </p>

      {/* Install bar */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
        <input
          type="text"
          placeholder={t("plugins.installPlaceholder")}
          value={installUrl}
          onChange={(e) => setInstallUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !installBtnDisabled && handleInstall()}
          disabled={notAvailable}
          style={{
            flex: 1, padding: "8px 12px",
            border: "1px solid var(--line)", borderRadius: 6,
            background: "var(--bg-subtle, var(--panel))", color: "var(--fg)",
            fontSize: 13, outline: "none",
          }}
        />
        <button
          onClick={handleInstall}
          disabled={installBtnDisabled}
          style={{
            padding: "8px 16px", borderRadius: 6, border: "none",
            background: installBtnDisabled ? "var(--muted, #9ca3af)" : "var(--primary, #2563eb)",
            color: "#fff", cursor: installBtnDisabled ? "not-allowed" : "pointer",
            fontSize: 13, opacity: installBtnDisabled ? 0.5 : 1,
            transition: "background 0.2s, opacity 0.2s",
          }}
        >
          {installing ? t("plugins.installing") : t("plugins.install")}
        </button>
        <button
          onClick={manualRefresh}
          style={{
            padding: "8px 12px", borderRadius: 6,
            border: "1px solid var(--line)", background: "transparent",
            color: "var(--muted)", cursor: "pointer", fontSize: 13,
          }}
        >
          {t("plugins.refresh")}
        </button>
      </div>

      {notAvailable && (
        <div style={{
          padding: "14px 18px",
          background: "var(--warn-bg, rgba(245, 158, 11, 0.15))",
          border: "1px solid var(--warning, #f59e0b)",
          borderRadius: 6, color: "var(--fg)", marginBottom: 16,
          fontSize: 13, lineHeight: 1.5,
        }}>
          {t("plugins.notAvailable")}
        </div>
      )}

      {error && (
        <div style={{
          padding: "10px 14px",
          background: "var(--err-bg, rgba(239, 68, 68, 0.15))",
          border: "1px solid var(--danger, #ef4444)",
          borderRadius: 6, color: "var(--error, #f87171)",
          marginBottom: 16, fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {/* Pending permissions banner */}
      {pluginsWithPending.length > 0 && (
        <div style={{
          padding: "12px 16px", marginBottom: 16, borderRadius: 6,
          background: "var(--warn-bg, rgba(245, 158, 11, 0.1))",
          border: "1px solid var(--warning, #f59e0b)",
          fontSize: 13, color: "var(--fg)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 600, marginBottom: 6 }}>
            <IconShield size={14} style={{ color: "var(--warning, #f59e0b)" }} />
            {t("plugins.permPendingTitle")}
          </div>
          <div style={{ color: "var(--muted)", lineHeight: 1.5, marginBottom: 8 }}>
            {t("plugins.permPendingDesc")}
          </div>
          {pluginsWithPending.map((p) => (
            <div key={p.id} style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "6px 0", borderTop: "1px solid var(--line)",
            }}>
              <span style={{ fontSize: 13 }}>
                <strong>{p.name}</strong>
                <span style={{ color: "var(--muted)", marginLeft: 8 }}>
                  {(p.pending_permissions || []).map((pp) => permLabel(pp, lang)).join(", ")}
                </span>
              </span>
              <button
                onClick={() => handleGrantPermissions(p.id, p.pending_permissions || [])}
                disabled={granting}
                style={{
                  padding: "3px 10px", borderRadius: 4, border: "none",
                  background: "var(--warning, #f59e0b)", color: "#fff",
                  cursor: granting ? "not-allowed" : "pointer", fontSize: 11,
                  opacity: granting ? 0.6 : 1,
                }}
              >
                {granting ? "..." : t("plugins.grantAll")}
              </button>
            </div>
          ))}
        </div>
      )}

      {loading && !notAvailable ? (
        <div style={{ color: "var(--muted)", padding: 40, textAlign: "center" }}>
          {t("plugins.loading")}
        </div>
      ) : !notAvailable && plugins.length === 0 && Object.keys(failed).length === 0 ? (
        <div style={{ color: "var(--muted)", padding: 40, textAlign: "center" }}>
          {t("plugins.noPlugins")}
        </div>
      ) : !notAvailable ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {plugins.map((p) => {
            const hasPending = (p.pending_permissions?.length ?? 0) > 0;
            return (
              <div
                key={p.id}
                style={{
                  border: `1px solid ${hasPending ? "var(--warning, #f59e0b)" : "var(--line)"}`,
                  borderRadius: 8, padding: "14px 18px",
                  background: "var(--card-bg, var(--panel))",
                }}
              >
                {/* Header row */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0 }}>
                    <PluginIcon plugin={p} apiBase={apiBaseRef.current()} />
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 14, color: "var(--fg)", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                        {p.name}
                        {p.permission_level && (
                          <span style={{
                            display: "inline-block", padding: "1px 6px", borderRadius: 10,
                            fontSize: 10, fontWeight: 600, color: "#fff",
                            background: LEVEL_COLORS[p.permission_level] || "var(--muted)",
                          }}>
                            {levelLabel(p.permission_level, lang)}
                          </span>
                        )}
                        {hasPending && (
                          <span style={{
                            display: "inline-block", padding: "1px 6px", borderRadius: 10,
                            fontSize: 10, fontWeight: 600,
                            color: "var(--warning, #f59e0b)",
                            border: "1px solid var(--warning, #f59e0b)",
                            background: "transparent",
                          }}>
                            {t("plugins.permPending")}
                          </span>
                        )}
                      </div>
                      <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>
                        v{p.version} · {p.category || p.type}
                        {p.author ? ` · ${p.author}` : ""}
                      </div>
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
                    {p.status === "failed" && (
                      <span style={{ color: "var(--error, #f87171)", fontSize: 11 }}>{t("plugins.failed")}</span>
                    )}
                    {hasPending && (
                      <button
                        onClick={() => setPermDialog(permDialog === p.id ? null : p.id)}
                        title={t("plugins.permManage")}
                        style={{
                          padding: "4px 8px", borderRadius: 4, display: "inline-flex", alignItems: "center",
                          border: "1px solid var(--warning, #f59e0b)",
                          background: permDialog === p.id ? "var(--warn-bg, rgba(245,158,11,0.1))" : "transparent",
                          color: "var(--warning, #f59e0b)", cursor: "pointer",
                        }}
                      >
                        <IconShield size={14} />
                      </button>
                    )}
                    {p.has_readme && (
                      <button
                        onClick={() => toggleReadme(p.id)}
                        title={t("plugins.viewDocs")}
                        style={{
                          padding: "4px 8px", borderRadius: 4, display: "inline-flex", alignItems: "center",
                          border: "1px solid var(--line)",
                          background: expandedId === p.id ? "var(--bg-subtle, var(--panel2))" : "transparent",
                          color: "var(--muted)", cursor: "pointer",
                        }}
                      >
                        <IconBook size={14} />
                      </button>
                    )}
                    {p.has_config_schema && (
                      <button
                        onClick={() => openConfig(p.id)}
                        title={t("plugins.settings")}
                        style={{
                          padding: "4px 8px", borderRadius: 4, display: "inline-flex", alignItems: "center",
                          border: "1px solid var(--line)",
                          background: configPanel === p.id ? "var(--bg-subtle, var(--panel2))" : "transparent",
                          color: "var(--muted)", cursor: "pointer",
                        }}
                      >
                        <IconGear size={14} />
                      </button>
                    )}
                    <button
                      onClick={() => handleOpenFolder(p.id)}
                      title={t("plugins.openFolder")}
                      style={{
                        padding: "4px 8px", borderRadius: 4, display: "inline-flex", alignItems: "center",
                        border: "1px solid var(--line)", background: "transparent",
                        color: "var(--muted)", cursor: "pointer",
                      }}
                    >
                      <IconFolderOpen size={14} />
                    </button>
                    <button
                      onClick={() => handleExport(p.id)}
                      title={t("plugins.export")}
                      style={{
                        padding: "4px 8px", borderRadius: 4, display: "inline-flex", alignItems: "center",
                        border: "1px solid var(--line)", background: "transparent",
                        color: "var(--muted)", cursor: "pointer",
                      }}
                    >
                      <IconDownload size={14} />
                    </button>
                    <button
                      onClick={() => toggleLogs(p.id)}
                      title={t("plugins.viewLogs")}
                      style={{
                        padding: "4px 8px", borderRadius: 4, display: "inline-flex", alignItems: "center",
                        border: "1px solid var(--line)",
                        background: logsPanel === p.id ? "var(--bg-subtle, var(--panel2))" : "transparent",
                        color: "var(--muted)", cursor: "pointer",
                      }}
                    >
                      <IconTerminal size={14} />
                    </button>
                    <button
                      onClick={() => handleAction(p.id, p.enabled === false ? "enable" : "disable")}
                      style={{
                        padding: "4px 10px", borderRadius: 4,
                        border: "1px solid var(--line)", background: "transparent",
                        color: p.enabled === false ? "var(--ok, #22c55e)" : "var(--muted)",
                        cursor: "pointer", fontSize: 12,
                      }}
                    >
                      {p.enabled === false ? t("plugins.enable") : t("plugins.disable")}
                    </button>
                    <button
                      onClick={() => handleAction(p.id, "delete")}
                      style={{
                        padding: "4px 10px", borderRadius: 4,
                        border: "1px solid var(--danger, #ef4444)", background: "transparent",
                        color: "var(--error, #f87171)", cursor: "pointer", fontSize: 12,
                      }}
                    >
                      {t("plugins.remove")}
                    </button>
                  </div>
                </div>

                {/* Description */}
                {p.description && (
                  <div style={{ marginTop: 6, color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>
                    {p.description}
                  </div>
                )}

                {/* Tags */}
                {(p.tags?.length ?? 0) > 0 && (
                  <div style={{ marginTop: 6, display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {(p.tags || []).map((tag) => (
                      <span key={tag} style={{
                        padding: "1px 6px", borderRadius: 4, fontSize: 10,
                        background: "var(--bg-subtle, var(--panel2))", color: "var(--muted)",
                        border: "1px solid var(--line)",
                      }}>
                        {tag}
                      </span>
                    ))}
                  </div>
                )}

                {/* Error (only show if no pending_permissions — else it's the perm issue) */}
                {p.error && !hasPending && (
                  <div style={{ marginTop: 6, color: "var(--error, #f87171)", fontSize: 12 }}>{p.error}</div>
                )}

                {/* Permission dialog */}
                {permDialog === p.id && (
                  <div style={{
                    marginTop: 10, padding: "14px 16px", borderRadius: 6,
                    background: "var(--warn-bg, rgba(245,158,11,0.08))",
                    border: "1px solid var(--warning, #f59e0b)",
                  }}>
                    <div style={{ fontWeight: 600, fontSize: 13, color: "var(--fg)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
                      <IconShield size={14} style={{ color: "var(--warning, #f59e0b)" }} />
                      {t("plugins.permTitle")}
                    </div>
                    <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>
                      {t("plugins.permDesc")}
                    </div>
                    <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                      <tbody>
                        {(p.permissions || []).map((perm) => {
                          const isGranted = p.granted_permissions?.includes(perm) ?? false;
                          const isPending = p.pending_permissions?.includes(perm) ?? false;
                          return (
                            <tr key={perm} style={{ borderBottom: "1px solid var(--line)" }}>
                              <td style={{ padding: "4px 8px", color: "var(--fg)" }}>
                                {permLabel(perm, lang)}
                                <span style={{ color: "var(--muted)", marginLeft: 4 }}>({perm})</span>
                              </td>
                              <td style={{ padding: "4px 8px", textAlign: "right" }}>
                                {isGranted ? (
                                  <span style={{ color: "var(--ok, #22c55e)", fontSize: 11 }}>{t("plugins.permGranted")}</span>
                                ) : isPending ? (
                                  <span style={{ color: "var(--warning, #f59e0b)", fontSize: 11 }}>{t("plugins.permPending")}</span>
                                ) : null}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
                      <button
                        onClick={() => handleGrantPermissions(p.id, p.pending_permissions || [])}
                        disabled={granting}
                        style={{
                          padding: "6px 16px", borderRadius: 4, border: "none",
                          background: "var(--warning, #f59e0b)", color: "#fff",
                          cursor: granting ? "not-allowed" : "pointer", fontSize: 12,
                          opacity: granting ? 0.6 : 1,
                        }}
                      >
                        {granting ? "..." : t("plugins.grantAllAndReload")}
                      </button>
                      <button
                        onClick={() => setPermDialog(null)}
                        style={{
                          padding: "6px 12px", borderRadius: 4,
                          border: "1px solid var(--line)", background: "transparent",
                          color: "var(--muted)", cursor: "pointer", fontSize: 12,
                        }}
                      >
                        {t("common.close")}
                      </button>
                    </div>
                  </div>
                )}

                {/* README panel — markdown rendered */}
                {expandedId === p.id && (
                  <div
                    className="plugin-readme-content"
                    style={{
                      marginTop: 10, padding: "12px 16px", borderRadius: 6,
                      background: "var(--bg-subtle, var(--panel2))", border: "1px solid var(--line)",
                      fontSize: 13, lineHeight: 1.6, color: "var(--fg)",
                      maxHeight: 400, overflowY: "auto",
                    }}
                  >
                    {readmeCache[p.id] ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{readmeCache[p.id]}</ReactMarkdown>
                    ) : (
                      t("plugins.loading")
                    )}
                  </div>
                )}

                {/* Config panel */}
                {configPanel === p.id && (
                  <div style={{
                    marginTop: 10, padding: "14px 16px", borderRadius: 6,
                    background: "var(--bg-subtle, var(--panel2))", border: "1px solid var(--line)",
                  }}>
                    <div style={{ fontWeight: 600, fontSize: 13, color: "var(--fg)", marginBottom: 10 }}>
                      {t("plugins.settings")}
                    </div>
                    {configSchema?.properties ? (
                      <>
                        {Object.entries(configSchema.properties).map(([key, prop]) => {
                          const isRequired = configSchema.required?.includes(key);
                          return (
                            <div key={key} style={{ marginBottom: 12 }}>
                              <label style={{ display: "block", fontSize: 12, color: "var(--fg)", marginBottom: 4, fontWeight: 500 }}>
                                {prop.title || key}
                                {isRequired && <span style={{ color: "var(--danger, #ef4444)", marginLeft: 2 }}>*</span>}
                                {prop.title && <span style={{ color: "var(--muted)", fontSize: 11, marginLeft: 4, fontWeight: 400 }}>({key})</span>}
                              </label>
                              {prop.description && (
                                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                                  {prop.description}
                                </div>
                              )}
                              {prop.enum ? (
                                <select
                                  value={configValues[key] ?? prop.default ?? ""}
                                  onChange={(e) => setConfigValues((v) => ({ ...v, [key]: e.target.value }))}
                                  style={{
                                    width: "100%", padding: "6px 10px", borderRadius: 4,
                                    border: "1px solid var(--line)", background: "var(--bg, #fff)",
                                    color: "var(--fg)", fontSize: 13,
                                  }}
                                >
                                  <option value="">--</option>
                                  {prop.enum.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                                </select>
                              ) : prop.type === "boolean" ? (
                                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--fg)" }}>
                                  <input
                                    type="checkbox"
                                    checked={!!configValues[key]}
                                    onChange={(e) => setConfigValues((v) => ({ ...v, [key]: e.target.checked }))}
                                  />
                                  {prop.title || key}
                                </label>
                              ) : prop.type === "integer" || prop.type === "number" ? (
                                <input
                                  type="number"
                                  value={configValues[key] ?? prop.default ?? ""}
                                  onChange={(e) => setConfigValues((v) => ({ ...v, [key]: Number(e.target.value) }))}
                                  style={{
                                    width: "100%", padding: "6px 10px", borderRadius: 4,
                                    border: "1px solid var(--line)", background: "var(--bg, #fff)",
                                    color: "var(--fg)", fontSize: 13,
                                  }}
                                />
                              ) : prop.type === "array" ? (
                                <input
                                  type="text"
                                  placeholder={t("plugins.arrayHint")}
                                  value={Array.isArray(configValues[key]) ? configValues[key].join(", ") : (configValues[key] ?? "")}
                                  onChange={(e) => setConfigValues((v) => ({
                                    ...v,
                                    [key]: e.target.value.split(",").map((s: string) => s.trim()).filter(Boolean),
                                  }))}
                                  style={{
                                    width: "100%", padding: "6px 10px", borderRadius: 4,
                                    border: "1px solid var(--line)", background: "var(--bg, #fff)",
                                    color: "var(--fg)", fontSize: 13,
                                  }}
                                />
                              ) : (
                                <input
                                  type={key.toLowerCase().includes("password") || key.toLowerCase().includes("secret") || key.toLowerCase().includes("key") ? "password" : "text"}
                                  value={configValues[key] ?? prop.default ?? ""}
                                  placeholder={prop.default != null ? String(prop.default) : ""}
                                  onChange={(e) => setConfigValues((v) => ({ ...v, [key]: e.target.value }))}
                                  style={{
                                    width: "100%", padding: "6px 10px", borderRadius: 4,
                                    border: "1px solid var(--line)", background: "var(--bg, #fff)",
                                    color: "var(--fg)", fontSize: 13,
                                  }}
                                />
                              )}
                            </div>
                          );
                        })}
                        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
                          <button
                            onClick={() => saveConfig(p.id)}
                            disabled={configSaving}
                            style={{
                              padding: "6px 16px", borderRadius: 4, border: "none",
                              background: "var(--primary, #2563eb)", color: "#fff",
                              cursor: configSaving ? "not-allowed" : "pointer", fontSize: 12,
                              opacity: configSaving ? 0.6 : 1,
                            }}
                          >
                            {configSaving ? t("plugins.saving") : t("plugins.saveConfig")}
                          </button>
                          {configMsg && (
                            <span style={{
                              fontSize: 12,
                              color: configMsg === t("plugins.configSaved") ? "var(--ok, #22c55e)" : "var(--error, #f87171)",
                            }}>
                              {configMsg}
                            </span>
                          )}
                        </div>
                      </>
                    ) : (
                      <div style={{ color: "var(--muted)", fontSize: 12 }}>
                        {t("plugins.noConfigSchema")}
                        <pre style={{
                          marginTop: 8, padding: 10, borderRadius: 4,
                          background: "var(--bg, #fff)", border: "1px solid var(--line)",
                          fontSize: 12, whiteSpace: "pre-wrap", color: "var(--fg)",
                        }}>
                          {JSON.stringify(configValues, null, 2) || "{}"}
                        </pre>
                      </div>
                    )}
                  </div>
                )}

                {/* Logs panel */}
                {logsPanel === p.id && (
                  <div style={{
                    marginTop: 10, padding: "14px 16px", borderRadius: 6,
                    background: "var(--bg-subtle, var(--panel2))", border: "1px solid var(--line)",
                  }}>
                    <div style={{
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                      marginBottom: 8,
                    }}>
                      <div style={{ fontWeight: 600, fontSize: 13, color: "var(--fg)", display: "flex", alignItems: "center", gap: 6 }}>
                        <IconTerminal size={14} style={{ color: "var(--muted)" }} />
                        {t("plugins.logsTitle")}
                      </div>
                      <button
                        onClick={() => refreshLogs(p.id)}
                        style={{
                          padding: "3px 10px", borderRadius: 4,
                          border: "1px solid var(--line)", background: "transparent",
                          color: "var(--muted)", cursor: "pointer", fontSize: 11,
                        }}
                      >
                        {t("plugins.refresh")}
                      </button>
                    </div>
                    <pre style={{
                      margin: 0, padding: 10, borderRadius: 4,
                      background: "var(--bg, #1a1a2e)", border: "1px solid var(--line)",
                      fontSize: 11, lineHeight: 1.5, color: "var(--fg)",
                      maxHeight: 360, overflowY: "auto", whiteSpace: "pre-wrap",
                      wordBreak: "break-all", fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
                    }}>
                      {logsContent || t("plugins.loading")}
                    </pre>
                  </div>
                )}
              </div>
            );
          })}

          {Object.keys(failed).length > 0 && (
            <>
              <h3 style={{ marginTop: 16, color: "var(--error, #f87171)", fontSize: 14 }}>
                {t("plugins.failedToLoad")}
              </h3>
              {Object.entries(failed).map(([id, reason]) => (
                <div
                  key={id}
                  style={{
                    border: "1px solid var(--danger, #ef4444)",
                    borderRadius: 8, padding: "10px 14px",
                    background: "var(--err-bg, rgba(239, 68, 68, 0.15))",
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 13, color: "var(--fg)" }}>{id}</div>
                  <div style={{ color: "var(--error, #f87171)", fontSize: 12, marginTop: 4 }}>{reason}</div>
                </div>
              ))}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
