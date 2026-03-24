import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";

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
}

interface PluginListResponse {
  plugins: PluginInfo[];
  failed: Record<string, string>;
}

function httpApiBase(): string {
  const w = window as any;
  return w.__API_BASE__ || "http://127.0.0.1:19980";
}

async function fetchPlugins(): Promise<PluginListResponse> {
  const resp = await fetch(`${httpApiBase()}/api/plugins/list`, {
    headers: { Authorization: `Bearer ${(window as any).__API_TOKEN__ || ""}` },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function pluginAction(
  pluginId: string,
  action: "enable" | "disable" | "delete",
): Promise<void> {
  const method = action === "delete" ? "DELETE" : "POST";
  const url =
    action === "delete"
      ? `${httpApiBase()}/api/plugins/${pluginId}`
      : `${httpApiBase()}/api/plugins/${pluginId}/${action}`;
  const resp = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${(window as any).__API_TOKEN__ || ""}` },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
}

async function installPlugin(source: string): Promise<any> {
  const resp = await fetch(`${httpApiBase()}/api/plugins/install`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${(window as any).__API_TOKEN__ || ""}`,
    },
    body: JSON.stringify({ source }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

const LEVEL_COLORS: Record<string, string> = {
  basic: "var(--ok, #22c55e)",
  advanced: "var(--warning, #f59e0b)",
  system: "var(--danger, #ef4444)",
};

const TYPE_ICONS: Record<string, string> = {
  python: "🐍",
  mcp: "🔌",
  skill: "📝",
};

export default function PluginManagerView({ visible }: { visible: boolean }) {
  const { t } = useTranslation();
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notAvailable, setNotAvailable] = useState(false);
  const [installUrl, setInstallUrl] = useState("");
  const [installing, setInstalling] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    setNotAvailable(false);
    try {
      const data = await fetchPlugins();
      setPlugins(data.plugins || []);
      setFailed(data.failed || {});
    } catch (e: any) {
      const msg = e.message || "";
      if (msg.includes("404") || msg.includes("Not Found")) {
        setNotAvailable(true);
      } else {
        setError(msg || t("plugins.failedToLoad"));
      }
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (visible) refresh();
  }, [visible, refresh]);

  const handleAction = async (id: string, action: "enable" | "disable" | "delete") => {
    try {
      await pluginAction(id, action);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleInstall = async () => {
    if (!installUrl.trim()) return;
    setInstalling(true);
    setError("");
    try {
      await installPlugin(installUrl.trim());
      setInstallUrl("");
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setInstalling(false);
    }
  };

  if (!visible) return null;

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
          onKeyDown={(e) => e.key === "Enter" && handleInstall()}
          disabled={notAvailable}
          style={{
            flex: 1,
            padding: "8px 12px",
            border: "1px solid var(--line)",
            borderRadius: 6,
            background: "var(--bg-subtle, var(--panel))",
            color: "var(--fg)",
            fontSize: 13,
            outline: "none",
          }}
        />
        <button
          onClick={handleInstall}
          disabled={installing || !installUrl.trim() || notAvailable}
          style={{
            padding: "8px 16px",
            borderRadius: 6,
            border: "none",
            background: "var(--primary, #2563eb)",
            color: "#fff",
            cursor: notAvailable ? "not-allowed" : "pointer",
            fontSize: 13,
            opacity: installing || notAvailable ? 0.6 : 1,
          }}
        >
          {installing ? t("plugins.installing") : t("plugins.install")}
        </button>
        <button
          onClick={refresh}
          style={{
            padding: "8px 12px",
            borderRadius: 6,
            border: "1px solid var(--line)",
            background: "transparent",
            color: "var(--muted)",
            cursor: "pointer",
            fontSize: 13,
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
          borderRadius: 6,
          color: "var(--fg)",
          marginBottom: 16,
          fontSize: 13,
          lineHeight: 1.5,
        }}>
          {t("plugins.notAvailable")}
        </div>
      )}

      {error && (
        <div style={{
          padding: "10px 14px",
          background: "var(--err-bg, rgba(239, 68, 68, 0.15))",
          border: "1px solid var(--danger, #ef4444)",
          borderRadius: 6,
          color: "var(--error, #f87171)",
          marginBottom: 16,
          fontSize: 13,
        }}>
          {error}
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
          {plugins.map((p) => (
            <div
              key={p.id}
              style={{
                border: "1px solid var(--line)",
                borderRadius: 8,
                padding: "14px 18px",
                background: "var(--card-bg, var(--panel))",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 18 }}>{TYPE_ICONS[p.type] || "📦"}</span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 14, color: "var(--fg)" }}>{p.name}</div>
                    <div style={{ color: "var(--muted)", fontSize: 12 }}>
                      v{p.version} · {p.category || p.type}
                    </div>
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {p.status === "failed" && (
                    <span style={{ color: "var(--error, #f87171)", fontSize: 11 }}>{t("plugins.failed")}</span>
                  )}
                  {p.permission_level && (
                    <span
                      style={{
                        display: "inline-block",
                        padding: "2px 8px",
                        borderRadius: 10,
                        fontSize: 11,
                        fontWeight: 600,
                        color: "#fff",
                        background: LEVEL_COLORS[p.permission_level] || "var(--muted)",
                      }}
                    >
                      {p.permission_level}
                    </span>
                  )}
                  <button
                    onClick={() => handleAction(p.id, p.enabled === false ? "enable" : "disable")}
                    style={{
                      padding: "4px 10px",
                      borderRadius: 4,
                      border: "1px solid var(--line)",
                      background: "transparent",
                      color: p.enabled === false ? "var(--ok, #22c55e)" : "var(--muted)",
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    {p.enabled === false ? t("plugins.enable") : t("plugins.disable")}
                  </button>
                  <button
                    onClick={() => handleAction(p.id, "delete")}
                    style={{
                      padding: "4px 10px",
                      borderRadius: 4,
                      border: "1px solid var(--danger, #ef4444)",
                      background: "transparent",
                      color: "var(--error, #f87171)",
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    {t("plugins.remove")}
                  </button>
                </div>
              </div>
              {p.error && (
                <div style={{ marginTop: 6, color: "var(--error, #f87171)", fontSize: 12 }}>{p.error}</div>
              )}
              {(p.permissions?.length ?? 0) > 0 && (
                <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {(p.permissions || []).map((perm) => (
                    <span
                      key={perm}
                      style={{
                        padding: "1px 6px",
                        borderRadius: 4,
                        fontSize: 10,
                        background: "var(--bg-subtle, var(--panel2))",
                        color: "var(--muted)",
                        border: "1px solid var(--line)",
                      }}
                    >
                      {perm}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}

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
                    borderRadius: 8,
                    padding: "10px 14px",
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
