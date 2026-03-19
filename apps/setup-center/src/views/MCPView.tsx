import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  IconLink,
  IconChevronDown, IconChevronRight,
  DotGreen, DotGray, DotYellow,
} from "../icons";
import { safeFetch } from "../providers";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2, RefreshCw, Plus, Trash2, Plug, Unplug } from "lucide-react";
import { toast } from "sonner";

type MCPTool = {
  name: string;
  description: string;
};

type MCPServer = {
  name: string;
  description: string;
  transport: string;
  url: string;
  command: string;
  connected: boolean;
  tools: MCPTool[];
  tool_count: number;
  has_instructions: boolean;
  catalog_tool_count: number;
  source: "builtin" | "workspace";
  removable: boolean;
};

type AddServerForm = {
  name: string;
  transport: "stdio" | "streamable_http" | "sse";
  command: string;
  args: string;
  env: string;
  url: string;
  description: string;
  auto_connect: boolean;
};

const emptyForm: AddServerForm = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  env: "",
  url: "",
  description: "",
  auto_connect: false,
};

/**
 * Parse args string into an array, respecting quoted strings for paths with spaces.
 * Examples:
 *   '-m my_module'           -> ['-m', 'my_module']
 *   '"C:\\Program Files\\s.py"' -> ['C:\\Program Files\\s.py']
 *   '-y @scope/pkg'         -> ['-y', '@scope/pkg']
 *   (one arg per line)      -> each line is one arg
 */
function parseArgs(raw: string): string[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  if (trimmed.includes("\n")) {
    return trimmed.split("\n").map(l => l.trim()).filter(Boolean);
  }
  const args: string[] = [];
  let current = "";
  let inQuote: string | null = null;
  for (const ch of trimmed) {
    if (inQuote) {
      if (ch === inQuote) { inQuote = null; }
      else { current += ch; }
    } else if (ch === '"' || ch === "'") {
      inQuote = ch;
    } else if (ch === " " || ch === "\t") {
      if (current) { args.push(current); current = ""; }
    } else {
      current += ch;
    }
  }
  if (current) args.push(current);
  return args;
}

export function MCPView({ serviceRunning, apiBaseUrl = "http://127.0.0.1:18900" }: { serviceRunning: boolean; apiBaseUrl?: string }) {
  const { t } = useTranslation();
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [mcpEnabled, setMcpEnabled] = useState(true);

  const [loading, setLoading] = useState(false);
  const [expandedServer, setExpandedServer] = useState<string | null>(null);
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<AddServerForm>({ ...emptyForm });
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);

  const fetchServers = useCallback(async () => {
    if (!serviceRunning) return;
    setLoading(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/servers`);
      const data = await res.json();
      setServers(data.servers || []);
      if (typeof data.mcp_enabled === "boolean") setMcpEnabled(data.mcp_enabled);
    } catch { /* ignore */ }
    setLoading(false);
  }, [serviceRunning, apiBaseUrl]);

  useEffect(() => { fetchServers(); }, [fetchServers]);

  const showMsg = (text: string, ok: boolean) => {
    if (ok) toast.success(text);
    else toast.error(text);
  };

  const connectServer = async (name: string) => {
    setBusy(name);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/connect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_name: name }),
      });
      const data = await res.json();
      if (data.status === "connected" || data.status === "already_connected") {
        showMsg(`${t("mcp.connected")} ${name}`, true);
        await fetchServers();
      } else {
        showMsg(`${t("mcp.connectFailed")}: ${data.error || t("mcp.unknownError")}`, false);
      }
    } catch (e) {
      showMsg(`${t("mcp.connectError")}: ${e}`, false);
    }
    setBusy(null);
  };

  const disconnectServer = async (name: string) => {
    setBusy(name);
    try {
      await safeFetch(`${apiBaseUrl}/api/mcp/disconnect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_name: name }),
      });
      showMsg(`${t("mcp.disconnected")} ${name}`, true);
      await fetchServers();
    } catch (e) {
      showMsg(`${t("mcp.disconnectError")}: ${e}`, false);
    }
    setBusy(null);
  };

  const doRemoveServer = useCallback(async (name: string) => {
    setBusy(name);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      const data = await res.json();
      if (data.status === "ok") {
        showMsg(`${t("mcp.deleted")} ${name}`, true);
        await fetchServers();
      } else {
        showMsg(`${t("mcp.deleteFailed")}: ${data.message || t("mcp.unknownError")}`, false);
      }
    } catch (e) {
      showMsg(`${t("mcp.deleteFailed")}: ${e}`, false);
    }
    setBusy(null);
  }, [apiBaseUrl, t, fetchServers]);

  const removeServer = (name: string) => {
    setConfirmDialog({
      message: t("mcp.confirmDelete", { name }),
      onConfirm: () => doRemoveServer(name),
    });
  };

  const addServer = async () => {
    const name = form.name.trim();
    if (!name) { showMsg(t("mcp.nameRequired"), false); return; }
    if (!/^[a-zA-Z0-9_-]+$/.test(name)) { showMsg(t("mcp.nameInvalid"), false); return; }
    if (form.transport === "stdio" && !form.command.trim()) { showMsg(t("mcp.commandRequired"), false); return; }
    if ((form.transport === "streamable_http" || form.transport === "sse") && !form.url.trim()) { showMsg(t("mcp.urlRequired", { transport: form.transport === "sse" ? "SSE" : "HTTP" }), false); return; }
    setBusy("add");
    try {
      const envObj: Record<string, string> = {};
      if (form.env.trim()) {
        for (const line of form.env.trim().split("\n")) {
          const idx = line.indexOf("=");
          if (idx > 0) envObj[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        }
      }
      const parsedArgs = parseArgs(form.args);
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/servers/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          transport: form.transport,
          command: form.command.trim(),
          args: parsedArgs,
          env: envObj,
          url: form.url.trim(),
          description: form.description.trim(),
          auto_connect: form.auto_connect,
        }),
      });
      const data = await res.json();
      if (data.status === "ok") {
        const cr = data.connect_result;
        let connMsg = "";
        if (cr) {
          if (cr.connected) {
            connMsg = `, ${t("mcp.autoConnected", { count: cr.tool_count ?? 0 })}`;
          } else {
            connMsg = `\n⚠️ ${t("mcp.autoConnectFailed")}: ${cr.error || t("mcp.unknownError")}`;
          }
        }
        showMsg(`✅ 已添加 ${name}${connMsg}`, !cr || cr.connected !== false);
        setForm({ ...emptyForm });
        setShowAdd(false);
        await fetchServers();
      } else {
        showMsg(`${t("mcp.addFailed")}: ${data.message || data.error || t("mcp.unknownError")}`, false);
      }
    } catch (e) {
      showMsg(`${t("mcp.addError")}: ${e}`, false);
    }
    setBusy(null);
  };

  const loadInstructions = async (name: string) => {
    if (instructions[name]) return;
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/instructions/${encodeURIComponent(name)}`);
      const data = await res.json();
      setInstructions(prev => ({ ...prev, [name]: data.instructions || t("mcp.noInstructions") }));
    } catch { /* ignore */ }
  };

  const toggleExpand = (name: string) => {
    if (expandedServer === name) {
      setExpandedServer(null);
    } else {
      setExpandedServer(name);
      loadInstructions(name);
    }
  };

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconLink size={48} />
        <div className="mt-3 font-semibold">MCP</div>
        <div className="mt-1 text-xs opacity-50">后端服务未启动，请启动后再进行使用</div>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <IconLink size={20} />
          <span style={{ fontSize: 16, fontWeight: 600 }}>{t("mcp.title")}</span>
          {!mcpEnabled && (
            <span style={{
              fontSize: 11, fontWeight: 500, color: "#b45309",
              background: "#fef3c7", padding: "2px 8px", borderRadius: 4,
            }}>
              {t("mcp.disabled") || "MCP 已禁用"}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Button variant="outline" size="sm" onClick={() => setShowAdd(!showAdd)}>
            <Plus size={14} /> {t("mcp.addServer")}
          </Button>
          <Button variant="outline" size="sm" onClick={fetchServers} disabled={loading}>
            {loading ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={14} />}
            {t("topbar.refresh")}
          </Button>
        </div>
      </div>

      {/* Add server form */}
      {showAdd && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>{t("mcp.addServerTitle")}</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 16px" }}>
            <div className="space-y-1.5">
              <Label>{t("mcp.serverName")} *</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder={t("mcp.serverNamePlaceholder")} />
            </div>
            <div className="space-y-1.5">
              <Label>{t("mcp.description")}</Label>
              <Input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder={t("mcp.descriptionPlaceholder")} />
            </div>
            <div className="space-y-1.5">
              <Label>{t("mcp.transport")}</Label>
              <Select value={form.transport} onValueChange={v => setForm({ ...form, transport: v as "stdio" | "streamable_http" | "sse" })}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="stdio">stdio ({t("mcp.stdioDesc")})</SelectItem>
                  <SelectItem value="streamable_http">Streamable HTTP</SelectItem>
                  <SelectItem value="sse">SSE (Server-Sent Events)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.transport === "stdio" ? (
              <>
                <div className="space-y-1.5">
                  <Label>{t("mcp.command")} *</Label>
                  <Input value={form.command} onChange={e => setForm({ ...form, command: e.target.value })} placeholder={t("mcp.commandPlaceholder")} />
                </div>
                <div className="space-y-1.5" style={{ gridColumn: "1 / -1" }}>
                  <Label>{t("mcp.argsLabel")}</Label>
                  <Textarea
                    value={form.args}
                    onChange={e => setForm({ ...form, args: e.target.value })}
                    placeholder={'如: -m openakita.mcp_servers.web_search\n或每行一个参数:\n-y\n@anthropic/mcp-server-filesystem\n"C:\\My Path\\dir"'}
                    rows={2}
                    className="resize-y font-mono text-xs"
                  />
                </div>
              </>
            ) : (
              <div className="space-y-1.5">
                <Label>URL *</Label>
                <Input value={form.url} onChange={e => setForm({ ...form, url: e.target.value })}
                  placeholder={form.transport === "sse" ? "如: http://127.0.0.1:8080/sse" : "如: http://127.0.0.1:12306/mcp"} />
              </div>
            )}
            <div className="space-y-1.5" style={{ gridColumn: "1 / -1" }}>
              <Label>{t("mcp.envLabel")}</Label>
              <Textarea
                value={form.env}
                onChange={e => setForm({ ...form, env: e.target.value })}
                placeholder={"API_KEY=sk-xxx\nMY_VAR=hello"}
                rows={3}
                className="resize-y font-mono text-xs"
              />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 14, justifyContent: "space-between", alignItems: "center" }}>
            <Label className="flex items-center gap-2 cursor-pointer text-sm font-normal text-muted-foreground">
              <Checkbox checked={form.auto_connect} onCheckedChange={(v) => setForm({ ...form, auto_connect: !!v })} />
              {t("mcp.autoConnect")}
            </Label>
            <div style={{ display: "flex", gap: 8 }}>
              <Button variant="outline" size="sm" onClick={() => { setShowAdd(false); setForm({ ...emptyForm }); }}>
                {t("common.cancel")}
              </Button>
              <Button size="sm" onClick={addServer} disabled={busy === "add"}>
                {busy === "add" && <Loader2 className="animate-spin" size={14} />}
                {t("mcp.add")}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Server list */}
      {loading && servers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 30, color: "var(--muted)" }}>
          {t("common.loading")}
        </div>
      ) : servers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
          <p style={{ fontSize: 15, marginBottom: 8 }}>{t("mcp.noServers")}</p>
          <p style={{ fontSize: 13 }}>{t("mcp.noServersHint")}</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {servers.map(s => (
            <div key={s.name} className="card" style={{ padding: 0 }}>
              {/* Server header */}
              <div
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "12px 16px", cursor: "pointer",
                }}
                onClick={() => toggleExpand(s.name)}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  {expandedServer === s.name ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                  {s.connected ? <DotGreen /> : <DotGray />}
                  <span style={{ fontWeight: 600, fontSize: 14 }}>{s.name}</span>
                  <span style={{ fontSize: 12, color: "var(--muted)", background: "var(--bg-subtle, #f1f5f9)", padding: "1px 6px", borderRadius: 3 }}>
                    {s.transport === "streamable_http" ? "HTTP" : s.transport === "sse" ? "SSE" : "stdio"}
                  </span>
                  <span style={{
                    fontSize: 11, padding: "1px 6px", borderRadius: 3,
                    background: s.source === "workspace" ? "var(--ok-bg, #dcfce7)" : "var(--bg-subtle, #f1f5f9)",
                    color: s.source === "workspace" ? "var(--ok, #16a34a)" : "var(--muted)",
                  }}>
                    {s.source === "workspace" ? t("mcp.sourceWorkspace") : t("mcp.sourceBuiltin")}
                  </span>
                  {s.description && (
                    <span style={{ fontSize: 12, color: "var(--muted)" }}>— {s.description}</span>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }} onClick={e => e.stopPropagation()}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>
                    {s.connected ? t("mcp.toolCount", { count: s.tool_count }) : t("mcp.toolCountCatalog", { count: s.catalog_tool_count })}
                  </span>
                  {s.connected ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => disconnectServer(s.name)}
                      disabled={busy === s.name}
                      className="text-amber-600 border-amber-300 hover:bg-amber-50 hover:text-amber-700 dark:text-amber-400 dark:border-amber-700 dark:hover:bg-amber-950"
                    >
                      {busy === s.name ? <Loader2 className="animate-spin" size={14} /> : <Unplug size={14} />}
                      {t("mcp.disconnect")}
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      onClick={() => connectServer(s.name)}
                      disabled={busy === s.name}
                    >
                      {busy === s.name ? <Loader2 className="animate-spin" size={14} /> : <Plug size={14} />}
                      {t("mcp.connect")}
                    </Button>
                  )}
                  {s.removable && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => removeServer(s.name)}
                      disabled={busy === s.name}
                      title={t("mcp.deleteServer")}
                      className="text-muted-foreground hover:text-destructive"
                    >
                      <Trash2 size={14} />
                    </Button>
                  )}
                </div>
              </div>

              {/* Expanded details */}
              {expandedServer === s.name && (
                <div style={{ borderTop: "1px solid var(--line, #e5e7eb)", padding: "12px 16px" }}>
                  {/* Connection info */}
                  <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>
                    {s.transport === "streamable_http" || s.transport === "sse" ? (
                      <span>{s.transport === "sse" ? "SSE" : "HTTP"} URL: <code>{s.url}</code></span>
                    ) : (
                      <span>{t("mcp.commandLabel")}: <code>{s.command}</code></span>
                    )}
                  </div>

                  {/* Tools */}
                  {s.tools.length > 0 ? (
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                        {t("mcp.availableTools")} ({s.tools.length})
                      </div>
                      <div style={{ display: "grid", gap: 6 }}>
                        {s.tools.map(t => (
                          <div key={t.name} style={{
                            background: "var(--bg-subtle, #f8fafc)", borderRadius: 6, padding: "8px 12px",
                          }}>
                            <div style={{ fontWeight: 500, fontSize: 13 }}>{t.name}</div>
                            {t.description && (
                              <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
                                {t.description}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : !s.connected ? (
                    <div style={{ fontSize: 13, color: "var(--muted)" }}>
                      <DotYellow /> {t("mcp.connectToSeeTools")}
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, color: "var(--muted)" }}>{t("mcp.noTools")}</div>
                  )}

                  {/* Instructions */}
                  {s.has_instructions && instructions[s.name] && (
                    <details style={{ marginTop: 12 }}>
                      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600, color: "var(--primary, #3b82f6)", lineHeight: "20px" }}>
                        {t("mcp.instructions")}
                      </summary>
                      <pre style={{
                        marginTop: 8, padding: 12, background: "var(--bg-subtle, #f8fafc)",
                        borderRadius: 6, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word",
                        maxHeight: 300, overflow: "auto",
                      }}>
                        {instructions[s.name]}
                      </pre>
                    </details>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Help text */}
      <div style={{ marginTop: 16, fontSize: 12, color: "var(--muted)", lineHeight: 1.8 }}>
        <strong>MCP (Model Context Protocol)</strong> {t("mcp.helpLine1")}
        <br />
        {t("mcp.helpLine2")}
        <br />
        {t("mcp.helpLine3")}
      </div>
      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
    </div>
  );
}
