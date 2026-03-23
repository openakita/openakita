import { useState, useEffect, useCallback } from "react";

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
  basic: "#22c55e",
  advanced: "#f59e0b",
  system: "#ef4444",
};

const TYPE_ICONS: Record<string, string> = {
  python: "🐍",
  mcp: "🔌",
  skill: "📝",
};

export default function PluginManagerView({ visible }: { visible: boolean }) {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [failed, setFailed] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [installUrl, setInstallUrl] = useState("");
  const [installing, setInstalling] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchPlugins();
      setPlugins(data.plugins || []);
      setFailed(data.failed || {});
    } catch (e: any) {
      setError(e.message || "Failed to load plugins");
    } finally {
      setLoading(false);
    }
  }, []);

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
      <h2 style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}>
        Plugins
        <span style={{ fontSize: 12, color: "#888", fontWeight: 400 }}>
          {plugins.length} installed
        </span>
      </h2>
      <p style={{ color: "#888", fontSize: 13, marginBottom: 20 }}>
        Manage installed plugins, install from URL, or enable/disable modules.
      </p>

      {/* Install bar */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
        <input
          type="text"
          placeholder="URL or path to install plugin..."
          value={installUrl}
          onChange={(e) => setInstallUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleInstall()}
          style={{
            flex: 1,
            padding: "8px 12px",
            border: "1px solid #333",
            borderRadius: 6,
            background: "#1a1a1a",
            color: "#eee",
            fontSize: 13,
          }}
        />
        <button
          onClick={handleInstall}
          disabled={installing || !installUrl.trim()}
          style={{
            padding: "8px 16px",
            borderRadius: 6,
            border: "none",
            background: "#2563eb",
            color: "#fff",
            cursor: "pointer",
            fontSize: 13,
            opacity: installing ? 0.6 : 1,
          }}
        >
          {installing ? "Installing..." : "Install"}
        </button>
        <button
          onClick={refresh}
          style={{
            padding: "8px 12px",
            borderRadius: 6,
            border: "1px solid #333",
            background: "transparent",
            color: "#aaa",
            cursor: "pointer",
            fontSize: 13,
          }}
        >
          Refresh
        </button>
      </div>

      {error && (
        <div style={{ padding: "10px 14px", background: "#2d1515", border: "1px solid #5a2020", borderRadius: 6, color: "#f87171", marginBottom: 16, fontSize: 13 }}>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ color: "#888", padding: 40, textAlign: "center" }}>Loading plugins...</div>
      ) : plugins.length === 0 && Object.keys(failed).length === 0 ? (
        <div style={{ color: "#888", padding: 40, textAlign: "center" }}>
          No plugins installed. Install plugins from URL or place them in <code>data/plugins/</code>.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {plugins.map((p) => (
            <div
              key={p.id}
              style={{
                border: "1px solid #2a2a2a",
                borderRadius: 8,
                padding: "14px 18px",
                background: "#111",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 18 }}>{TYPE_ICONS[p.type] || "📦"}</span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{p.name}</div>
                    <div style={{ color: "#888", fontSize: 12 }}>
                      v{p.version} · {p.category || p.type}
                    </div>
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {p.status === "failed" && (
                    <span style={{ color: "#f87171", fontSize: 11 }}>failed</span>
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
                        background: LEVEL_COLORS[p.permission_level] || "#666",
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
                      border: "1px solid #333",
                      background: "transparent",
                      color: p.enabled === false ? "#22c55e" : "#aaa",
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    {p.enabled === false ? "Enable" : "Disable"}
                  </button>
                  <button
                    onClick={() => handleAction(p.id, "delete")}
                    style={{
                      padding: "4px 10px",
                      borderRadius: 4,
                      border: "1px solid #5a2020",
                      background: "transparent",
                      color: "#f87171",
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    Remove
                  </button>
                </div>
              </div>
              {p.error && (
                <div style={{ marginTop: 6, color: "#f87171", fontSize: 12 }}>{p.error}</div>
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
                        background: "#222",
                        color: "#999",
                        border: "1px solid #333",
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
              <h3 style={{ marginTop: 16, color: "#f87171", fontSize: 14 }}>Failed to Load</h3>
              {Object.entries(failed).map(([id, reason]) => (
                <div
                  key={id}
                  style={{
                    border: "1px solid #5a2020",
                    borderRadius: 8,
                    padding: "10px 14px",
                    background: "#1a0f0f",
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{id}</div>
                  <div style={{ color: "#f87171", fontSize: 12, marginTop: 4 }}>{reason}</div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
