// Server connection manager for Capacitor mobile app.
// Two modes: first-time setup (add first server) and management (list/edit/delete/switch).

import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import logoUrl from "../assets/logo.png";
import {
  getServers, addServer, updateServer, removeServer,
  setActiveServer, testConnection,
} from "../platform/servers";
import type { ServerEntry } from "../platform/servers";

export function ServerManagerView({
  activeServerId,
  onConnect,
  onDone,
  manageModeInit,
}: {
  activeServerId: string | null;
  onConnect: (url: string) => void;
  onDone?: () => void;
  manageModeInit?: boolean;
}) {
  const { t } = useTranslation();
  const [servers, setServers] = useState<ServerEntry[]>(getServers);
  const [mode, setMode] = useState<"list" | "add" | "edit">(
    manageModeInit && servers.length > 0 ? "list" : "add",
  );
  const [editId, setEditId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; name?: string; version?: string; error?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => setServers(getServers()), []);

  const handleTest = useCallback(async () => {
    if (!url.trim()) return;
    setTesting(true);
    setTestResult(null);
    setError(null);
    const r = await testConnection(url.trim());
    setTesting(false);
    setTestResult(r);
    if (!r.ok) setError(r.error || t("server.connectFailed", { defaultValue: "连接失败" }));
  }, [url, t]);

  const handleSave = useCallback(() => {
    if (!testResult?.ok) return;
    if (mode === "edit" && editId) {
      updateServer(editId, { name: name.trim() || url.trim(), url: url.trim() });
      setActiveServer(editId);
      refresh();
      setMode("list");
      onConnect(getServers().find((s) => s.id === editId)?.url || url.trim());
    } else {
      const entry = addServer(name.trim(), url.trim());
      refresh();
      if (servers.length > 0) setMode("list");
      onConnect(entry.url);
    }
  }, [testResult, mode, editId, name, url, refresh, onConnect, servers.length]);

  const handleSwitch = useCallback((s: ServerEntry) => {
    setActiveServer(s.id);
    refresh();
    onConnect(s.url);
  }, [refresh, onConnect]);

  const handleDelete = useCallback((id: string) => {
    removeServer(id);
    refresh();
    const remaining = getServers();
    if (remaining.length === 0) {
      setMode("add");
      setName("");
      setUrl("");
      setTestResult(null);
    }
  }, [refresh]);

  const startEdit = useCallback((s: ServerEntry) => {
    setEditId(s.id);
    setName(s.name);
    setUrl(s.url);
    setTestResult(null);
    setError(null);
    setMode("edit");
  }, []);

  const startAdd = useCallback(() => {
    setEditId(null);
    setName("");
    setUrl("");
    setTestResult(null);
    setError(null);
    setMode("add");
  }, []);

  // ── Card styles (reuse LoginView look) ──
  const containerStyle: React.CSSProperties = {
    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
    minHeight: "100vh", width: "100vw",
    background: "linear-gradient(135deg, var(--bg, #f8fafc) 0%, var(--panel, #e2e8f0) 100%)",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    color: "var(--text, #334155)", padding: 24, boxSizing: "border-box",
  };
  const cardStyle: React.CSSProperties = {
    background: "var(--panel2, #fff)", borderRadius: 16,
    boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
    padding: "32px 28px", maxWidth: 420, width: "100%",
  };
  const inputStyle: React.CSSProperties = {
    width: "100%", padding: "10px 14px", fontSize: 15, borderRadius: 10,
    border: "1px solid var(--line, #e2e8f0)", background: "var(--bg, #f8fafc)",
    color: "var(--text, #1e293b)", outline: "none", boxSizing: "border-box",
    marginBottom: 12, transition: "border-color 0.15s",
  };
  const btnPrimary: React.CSSProperties = {
    width: "100%", background: "linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)",
    color: "#fff", border: "none", borderRadius: 10, padding: "10px 0", fontSize: 15,
    fontWeight: 600, cursor: "pointer", boxShadow: "0 2px 8px rgba(14,165,233,0.3)",
    transition: "transform 0.1s, opacity 0.15s",
  };
  const btnSecondary: React.CSSProperties = {
    ...btnPrimary, background: "var(--bg, #f1f5f9)", color: "var(--text, #334155)",
    boxShadow: "none", border: "1px solid var(--line, #e2e8f0)",
  };

  // ── Add / Edit form ──
  if (mode === "add" || mode === "edit") {
    return (
      <div style={containerStyle}>
        <div style={cardStyle}>
          <div style={{ textAlign: "center", marginBottom: 20 }}>
            <img src={logoUrl} alt="OpenAkita" style={{ width: 48, height: 48, borderRadius: 10, marginBottom: 8 }} />
            <h2 style={{ margin: "0 0 4px", fontSize: 18, fontWeight: 600 }}>
              {mode === "edit"
                ? t("server.editTitle", { defaultValue: "编辑服务器" })
                : t("server.addTitle", { defaultValue: "连接服务器" })}
            </h2>
            <p style={{ margin: 0, fontSize: 13, color: "var(--text3, #64748b)" }}>
              {t("server.addHint", { defaultValue: "输入桌面端显示的远程访问地址" })}
            </p>
          </div>

          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("server.namePlaceholder", { defaultValue: "名称（可选），如「家里」" })}
            style={inputStyle}
          />
          <input
            value={url}
            onChange={(e) => { setUrl(e.target.value); setTestResult(null); setError(null); }}
            placeholder="192.168.1.100:18900"
            style={inputStyle}
            autoFocus
          />

          {error && (
            <div style={{
              background: "var(--error-bg, #fef2f2)", color: "var(--error, #dc2626)",
              borderRadius: 8, padding: "8px 12px", fontSize: 13, marginBottom: 12,
            }}>
              {error}
            </div>
          )}

          {testResult?.ok && (
            <div style={{
              background: "rgba(16, 185, 129, 0.1)", color: "var(--ok, #10b981)",
              borderRadius: 8, padding: "8px 12px", fontSize: 13, marginBottom: 12,
            }}>
              {t("server.connected", { defaultValue: "连接成功" })}
              {testResult.version && ` — v${testResult.version}`}
            </div>
          )}

          {!testResult?.ok ? (
            <button
              onClick={handleTest}
              disabled={testing || !url.trim()}
              style={{ ...btnPrimary, opacity: testing || !url.trim() ? 0.6 : 1, cursor: testing ? "wait" : "pointer" }}
            >
              {testing
                ? t("server.testing", { defaultValue: "连接中..." })
                : t("server.testBtn", { defaultValue: "测试连接" })}
            </button>
          ) : (
            <button onClick={handleSave} style={btnPrimary}>
              {t("server.saveConnect", { defaultValue: "保存并连接" })}
            </button>
          )}

          {(servers.length > 0 || mode === "edit") && (
            <button
              onClick={() => { setMode("list"); setEditId(null); }}
              style={{ ...btnSecondary, marginTop: 10 }}
            >
              {t("server.backToList", { defaultValue: "返回列表" })}
            </button>
          )}

          <p style={{ marginTop: 16, fontSize: 12, color: "var(--text3, #94a3b8)", textAlign: "center" }}>
            {t("server.copyHint", { defaultValue: "在桌面端顶栏点击「复制远程地址」获取地址" })}
          </p>
        </div>
      </div>
    );
  }

  // ── Server list ──
  return (
    <div style={containerStyle}>
      <div style={cardStyle}>
        <div style={{ textAlign: "center", marginBottom: 16 }}>
          <img src={logoUrl} alt="OpenAkita" style={{ width: 48, height: 48, borderRadius: 10, marginBottom: 8 }} />
          <h2 style={{ margin: "0 0 4px", fontSize: 18, fontWeight: 600 }}>
            {t("server.listTitle", { defaultValue: "我的服务器" })}
          </h2>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
          {servers.map((s) => {
            const isActive = s.id === activeServerId;
            return (
              <div
                key={s.id}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 12px", borderRadius: 10,
                  border: `1.5px solid ${isActive ? "var(--brand, #0ea5e9)" : "var(--line, #e2e8f0)"}`,
                  background: isActive ? "rgba(14, 165, 233, 0.06)" : "var(--bg, #f8fafc)",
                  cursor: "pointer", transition: "border-color 0.15s",
                }}
                onClick={() => !isActive && handleSwitch(s)}
              >
                <div style={{
                  width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                  background: isActive ? "var(--ok, #10b981)" : "var(--muted, #94a3b8)",
                }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {s.name}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text3, #64748b)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {s.url}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); startEdit(s); }}
                    style={{
                      background: "none", border: "none", color: "var(--text3, #64748b)",
                      cursor: "pointer", padding: 4, borderRadius: 6, fontSize: 13,
                    }}
                    title={t("server.edit", { defaultValue: "编辑" })}
                  >
                    ✏️
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(s.id); }}
                    style={{
                      background: "none", border: "none", color: "var(--danger, #ef4444)",
                      cursor: "pointer", padding: 4, borderRadius: 6, fontSize: 13,
                    }}
                    title={t("server.delete", { defaultValue: "删除" })}
                  >
                    🗑️
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        <button onClick={startAdd} style={btnPrimary}>
          + {t("server.addNew", { defaultValue: "添加服务器" })}
        </button>

        {onDone && (
          <button onClick={onDone} style={{ ...btnSecondary, marginTop: 10 }}>
            {t("server.done", { defaultValue: "完成" })}
          </button>
        )}
      </div>
    </div>
  );
}
