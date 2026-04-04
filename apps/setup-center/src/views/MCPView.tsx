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
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2, RefreshCw, Plus, Trash2, Plug, Unplug, Info, Server, Wrench } from "lucide-react";
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

function transportLabel(transport: string): string {
  if (transport === "streamable_http") return "HTTP";
  if (transport === "sse") return "SSE";
  return "stdio";
}

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

  const connectedCount = servers.filter((server) => server.connected).length;
  const totalTools = servers.reduce((sum, server) => sum + (server.connected ? server.tool_count : server.catalog_tool_count), 0);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      <Card className="gap-0 overflow-hidden border-border/80 bg-gradient-to-br from-primary/5 via-background to-background py-0 shadow-sm">
        <CardHeader className="gap-3 px-6 py-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="flex min-w-0 items-start gap-4">
              <div className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <IconLink size={22} />
              </div>
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-3">
                  <CardTitle className="text-xl tracking-tight">{t("mcp.title")}</CardTitle>
                  {!mcpEnabled && (
                    <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400">
                      {t("mcp.disabled") || "MCP 已禁用"}
                    </Badge>
                  )}
                </div>
                <CardDescription className="max-w-3xl text-sm leading-6">
                  <strong className="font-semibold text-foreground">MCP (Model Context Protocol)</strong> {t("mcp.helpLine1")}
                  <br />
                  {t("mcp.helpLine2")}
                  <br />
                  {t("mcp.helpLine3")}
                </CardDescription>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button variant={showAdd ? "secondary" : "outline"} onClick={() => setShowAdd(!showAdd)}>
                <Plus size={14} />
                {t("mcp.addServer")}
              </Button>
              <Button variant="outline" onClick={fetchServers} disabled={loading}>
                {loading ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={14} />}
                {t("topbar.refresh")}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 border-t px-6 py-4 sm:grid-cols-3">
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="text-xs text-muted-foreground">MCP Servers</div>
            <div className="mt-2 text-2xl font-semibold">{servers.length}</div>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="text-xs text-muted-foreground">{t("mcp.connected")}</div>
            <div className="mt-2 text-2xl font-semibold text-emerald-600">{connectedCount}</div>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="text-xs text-muted-foreground">{t("mcp.availableTools")}</div>
            <div className="mt-2 text-2xl font-semibold">{totalTools}</div>
          </div>
        </CardContent>
      </Card>

      {showAdd && (
        <Card className="gap-0 border-border/80 py-0 shadow-sm">
          <CardHeader className="gap-2 px-6 py-4">
            <CardTitle className="text-base">{t("mcp.addServerTitle")}</CardTitle>
            <CardDescription>
              {form.transport === "stdio"
                ? t("mcp.stdioDesc")
                : form.transport === "sse"
                  ? "使用 SSE 端点接入远程 MCP 服务。"
                  : "使用 Streamable HTTP 端点接入远程 MCP 服务。"}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 px-6 py-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>{t("mcp.serverName")} *</Label>
              <Input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder={t("mcp.serverNamePlaceholder")} />
            </div>
            <div className="space-y-2">
              <Label>{t("mcp.description")}</Label>
              <Input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder={t("mcp.descriptionPlaceholder")} />
            </div>
            <div className="space-y-2">
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
              <div className="space-y-2">
                <Label>{t("mcp.command")} *</Label>
                <Input value={form.command} onChange={e => setForm({ ...form, command: e.target.value })} placeholder={t("mcp.commandPlaceholder")} />
              </div>
            ) : (
              <div className="space-y-2">
                <Label>URL *</Label>
                <Input
                  value={form.url}
                  onChange={e => setForm({ ...form, url: e.target.value })}
                  placeholder={form.transport === "sse" ? "如: http://127.0.0.1:8080/sse" : "如: http://127.0.0.1:12306/mcp"}
                />
              </div>
            )}
            {form.transport === "stdio" && (
              <div className="space-y-2 md:col-span-2">
                <Label>{t("mcp.argsLabel")}</Label>
                <Textarea
                  value={form.args}
                  onChange={e => setForm({ ...form, args: e.target.value })}
                  placeholder={'如: -m openakita.mcp_servers.web_search\n或每行一个参数:\n-y\n@anthropic/mcp-server-filesystem\n"C:\\My Path\\dir"'}
                  rows={3}
                  className="resize-y font-mono text-xs"
                />
              </div>
            )}
            <div className="space-y-2 md:col-span-2">
              <Label>{t("mcp.envLabel")}</Label>
              <Textarea
                value={form.env}
                onChange={e => setForm({ ...form, env: e.target.value })}
                placeholder={"API_KEY=sk-xxx\nMY_VAR=hello"}
                rows={3}
                className="resize-y font-mono text-xs"
              />
            </div>
          </CardContent>
          <CardFooter className="flex flex-col gap-3 border-t px-6 py-4 md:flex-row md:items-center md:justify-between">
            <Label className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
              <Checkbox checked={form.auto_connect} onCheckedChange={(v) => setForm({ ...form, auto_connect: !!v })} />
              {t("mcp.autoConnect")}
            </Label>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => { setShowAdd(false); setForm({ ...emptyForm }); }}>
                {t("common.cancel")}
              </Button>
              <Button onClick={addServer} disabled={busy === "add"}>
                {busy === "add" && <Loader2 className="animate-spin" size={14} />}
                {t("mcp.add")}
              </Button>
            </div>
          </CardFooter>
        </Card>
      )}

      {loading && servers.length === 0 ? (
        <Card className="shadow-sm">
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            {t("common.loading")}
          </CardContent>
        </Card>
      ) : servers.length === 0 ? (
        <Card className="shadow-sm">
          <CardContent className="py-12 text-center text-muted-foreground">
            <p className="text-base font-medium text-foreground">{t("mcp.noServers")}</p>
            <p className="mt-2 text-sm">{t("mcp.noServersHint")}</p>
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          {servers.map((s) => (
            <Card key={s.name} className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm transition-shadow hover:shadow-md">
              <CardHeader className="gap-3 px-6 py-4">
                <div
                  className="flex cursor-pointer flex-col gap-4 xl:flex-row xl:items-start xl:justify-between"
                  onClick={() => toggleExpand(s.name)}
                >
                  <div className="flex min-w-0 gap-4">
                    <div className="flex size-11 shrink-0 items-center justify-center rounded-2xl border bg-muted/40 text-muted-foreground">
                      {s.connected ? <DotGreen /> : <DotGray />}
                    </div>
                    <div className="min-w-0 space-y-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="pointer-events-none -ml-2"
                        >
                          {expandedServer === s.name ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                        </Button>
                        <CardTitle className="text-base">{s.name}</CardTitle>
                        <Badge variant="secondary">{transportLabel(s.transport)}</Badge>
                        <Badge
                          variant="outline"
                          className={s.source === "workspace" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : undefined}
                        >
                          {s.source === "workspace" ? t("mcp.sourceWorkspace") : t("mcp.sourceBuiltin")}
                        </Badge>
                        {s.connected ? (
                          <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
                            {t("mcp.connected")}
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-muted-foreground">
                            {t("mcp.disconnected")}
                          </Badge>
                        )}
                      </div>

                      {s.description && (
                        <CardDescription className="max-w-3xl text-sm leading-6">
                          {s.description}
                        </CardDescription>
                      )}

                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge variant="outline" className="gap-1">
                          <Wrench size={12} />
                          {s.connected ? t("mcp.toolCount", { count: s.tool_count }) : t("mcp.toolCountCatalog", { count: s.catalog_tool_count })}
                        </Badge>
                        {s.has_instructions && (
                          <Badge variant="outline" className="gap-1">
                            <Info size={12} />
                            {t("mcp.instructions")}
                          </Badge>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    {s.connected ? (
                      <Button
                        variant="outline"
                        onClick={() => disconnectServer(s.name)}
                        disabled={busy === s.name}
                        className="text-amber-600 border-amber-300 hover:bg-amber-50 hover:text-amber-700 dark:text-amber-400 dark:border-amber-700 dark:hover:bg-amber-950"
                      >
                        {busy === s.name ? <Loader2 className="animate-spin" size={14} /> : <Unplug size={14} />}
                        {t("mcp.disconnect")}
                      </Button>
                    ) : (
                      <Button onClick={() => connectServer(s.name)} disabled={busy === s.name}>
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
              </CardHeader>

              {expandedServer === s.name && (
                <CardContent className="space-y-4 border-t px-6 py-4">
                  <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">
                    <div className="mb-1 flex items-center gap-2 font-medium text-foreground">
                      <Server size={14} />
                      {t("mcp.transport")}
                    </div>
                    {s.transport === "streamable_http" || s.transport === "sse" ? (
                      <span>{transportLabel(s.transport)} URL: <code>{s.url}</code></span>
                    ) : (
                      <span>{t("mcp.commandLabel")}: <code>{s.command}</code></span>
                    )}
                  </div>

                  {s.tools.length > 0 ? (
                    <div className="space-y-3">
                      <div className="text-sm font-semibold text-foreground">
                        {t("mcp.availableTools")} ({s.tools.length})
                      </div>
                      <div className="grid gap-3 md:grid-cols-2">
                        {s.tools.map((tool) => (
                          <div key={tool.name} className="rounded-xl border bg-background/80 p-4">
                            <div className="text-sm font-medium text-foreground">{tool.name}</div>
                            {tool.description && (
                              <div className="mt-2 text-sm leading-6 text-muted-foreground">
                                {tool.description}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : !s.connected ? (
                    <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm text-muted-foreground">
                      <span className="inline-flex items-center gap-2">
                        <DotYellow />
                        {t("mcp.connectToSeeTools")}
                      </span>
                    </div>
                  ) : (
                    <div className="rounded-xl border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                      {t("mcp.noTools")}
                    </div>
                  )}

                  {s.has_instructions && instructions[s.name] && (
                    <Card className="gap-0 border-border/70 bg-muted/20 py-0 shadow-none">
                      <CardHeader className="gap-2 px-4 py-3">
                        <CardTitle className="text-sm">{t("mcp.instructions")}</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <pre className="max-h-[300px] overflow-auto rounded-lg border bg-background p-3 text-xs leading-6 text-foreground whitespace-pre-wrap break-words">
                          {instructions[s.name]}
                        </pre>
                      </CardContent>
                    </Card>
                  )}
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      )}

      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
    </div>
  );
}
