import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  IconLink,
  IconChevronDown, IconChevronRight,
  DotYellow,
} from "../icons";
import { safeFetch } from "../providers";
import type { MCPConfigField, EnvMap } from "../types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Loader2, RefreshCw, Plus, Trash2, Plug, Unplug, Info, Server, Wrench, Eye, EyeOff, Save, AlertTriangle } from "lucide-react";
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
  config_schema: MCPConfigField[];
  config_status: Record<string, boolean>;
  config_complete: boolean;
};

type AddServerForm = {
  name: string;
  transport: "stdio" | "streamable_http" | "sse";
  command: string;
  args: string;
  env: string;
  url: string;
  headers: string;
  description: string;
  auto_connect: boolean;
};

const emptyForm: AddServerForm = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  headers: "",
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

function ConnectionIndicator({ connected, busy }: { connected: boolean; busy: boolean }) {
  const shellClassName = "relative flex size-11 shrink-0 items-center justify-center rounded-2xl border";

  if (busy) {
    return (
      <div className={`${shellClassName} border-primary/20 bg-primary/5 text-primary`}>
        <Loader2 className="animate-spin" size={16} />
      </div>
    );
  }

  if (connected) {
    return (
      <div className={`${shellClassName} border-emerald-500/25 bg-emerald-500/10`}>
        <span className="absolute inline-flex size-5 rounded-full bg-emerald-400/20 animate-ping" />
        <span className="absolute inline-flex size-3.5 rounded-full bg-emerald-500/30 animate-pulse" />
        <span className="relative inline-flex size-2.5 rounded-full bg-emerald-500 shadow-[0_0_12px_rgba(34,197,94,0.45)]" />
      </div>
    );
  }

  return (
    <div className={`${shellClassName} border-border bg-muted/40`}>
      <span className="inline-flex size-2.5 rounded-full bg-muted-foreground/70" />
    </div>
  );
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

function renderHelpText(help: string, helpUrl?: string) {
  const linkRe = /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g;
  const parts: (string | { text: string; url: string })[] = [];
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  while ((match = linkRe.exec(help)) !== null) {
    if (match.index > lastIdx) parts.push(help.slice(lastIdx, match.index));
    parts.push({ text: match[1], url: match[2] });
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < help.length) parts.push(help.slice(lastIdx));

  return (
    <p className="text-xs text-muted-foreground">
      {parts.map((p, i) =>
        typeof p === "string" ? (
          <span key={i}>{p}</span>
        ) : (
          <a key={i} href={p.url} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 hover:text-primary/80">{p.text}</a>
        )
      )}
      {helpUrl && (
        <>
          {" "}
          <a href={helpUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-primary underline underline-offset-2 hover:text-primary/80">
            <Info size={11} />
          </a>
        </>
      )}
    </p>
  );
}

function shouldShowField(f: MCPConfigField, serverProps: Record<string, string>): boolean {
  if (!f.when || Object.keys(f.when).length === 0) return true;
  return Object.entries(f.when).every(([k, v]) => serverProps[k] === v);
}

function MCPConfigForm({
  schema,
  configStatus,
  envDraft,
  onEnvChange,
  onSave,
  serverName,
  serverTransport,
  apiBaseUrl,
  onRefresh,
  t,
}: {
  schema: MCPConfigField[];
  configStatus: Record<string, boolean>;
  envDraft: EnvMap;
  onEnvChange: (update: (prev: EnvMap) => EnvMap) => void;
  onSave: (keys: string[]) => Promise<void>;
  serverName: string;
  serverTransport: string;
  apiBaseUrl: string;
  onRefresh: () => Promise<void>;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  const [secretVisible, setSecretVisible] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const serverProps: Record<string, string> = { transport: serverTransport };
  const visibleSchema = schema.filter(f => shouldShowField(f, serverProps));
  const missingCount = visibleSchema.filter(f => f.required && !configStatus[f.key]).length;

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(schema.map(f => f.key));
      toast.success(t("mcp.configSaved"));
    } catch {
      toast.error(t("mcp.configSaveFailed") || "保存失败");
    }
    setSaving(false);
  };

  const handleTestConnection = async () => {
    setTesting(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/connect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_name: serverName }),
      });
      const data = await res.json();
      if (data.status === "connected" || data.status === "already_connected") {
        toast.success(t("mcp.testConnectSuccess") || `${serverName} 连接成功`);
        if (data.status === "connected") {
          try {
            await safeFetch(`${apiBaseUrl}/api/mcp/disconnect`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ server_name: serverName }),
            });
          } catch { /* ignore disconnect error during test */ }
        }
        await onRefresh();
      } else if (data.status === "config_incomplete") {
        toast.error(data.message || t("mcp.configRequired"));
      } else {
        toast.error(`${t("mcp.testConnectFailed") || "测试连接失败"}: ${data.error || ""}`);
      }
    } catch (e) {
      toast.error(`${t("mcp.testConnectFailed") || "测试连接失败"}: ${e}`);
    }
    setTesting(false);
  };

  return (
    <div className="rounded-xl border border-primary/20 bg-primary/[0.02] p-4 space-y-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Wrench size={14} className="text-primary" />
        {t("mcp.configTitle")}
      </div>
      <p className="text-xs text-muted-foreground">{t("mcp.configHint")}</p>

      <div className="grid gap-4 md:grid-cols-2">
        {visibleSchema.map(f => {
          const val = envDraft[f.key] ?? (f.default != null ? String(f.default) : "");

          if (f.type === "bool") {
            return (
              <div key={f.key} className="flex items-center justify-between gap-3 md:col-span-2">
                <div className="space-y-0.5">
                  <Label className="text-sm">
                    {f.label || f.key}
                    {f.required && <span className="ml-1 text-destructive">*</span>}
                  </Label>
                  {f.help && renderHelpText(f.help, f.helpUrl)}
                </div>
                <Switch
                  checked={val === "true" || val === "1"}
                  onCheckedChange={(v) => onEnvChange(prev => ({ ...prev, [f.key]: v ? "true" : "false" }))}
                />
              </div>
            );
          }

          if (f.type === "select" && f.options?.length) {
            return (
              <div key={f.key} className="space-y-2">
                <Label className="text-sm">
                  {f.label || f.key}
                  {f.required && <span className="ml-1 text-destructive">*</span>}
                </Label>
                <Select value={val} onValueChange={(v) => onEnvChange(prev => ({ ...prev, [f.key]: v }))}>
                  <SelectTrigger className="w-full"><SelectValue placeholder={f.placeholder} /></SelectTrigger>
                  <SelectContent>
                    {f.options.map(opt => (
                      <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {f.help && renderHelpText(f.help, f.helpUrl)}
              </div>
            );
          }

          const isSecret = f.type === "secret";
          const visible = secretVisible[f.key] ?? false;

          return (
            <div key={f.key} className={`space-y-2 ${f.type === "url" || f.type === "path" ? "md:col-span-2" : ""}`}>
              <Label className="text-sm">
                {f.label || f.key}
                {f.required && <span className="ml-1 text-destructive">*</span>}
              </Label>
              <div className="relative">
                <Input
                  type={isSecret && !visible ? "password" : "text"}
                  value={val}
                  onChange={e => onEnvChange(prev => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder || `${f.label || f.key}`}
                  className={isSecret ? "pr-10 font-mono text-xs" : "font-mono text-xs"}
                />
                {isSecret && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    onClick={() => setSecretVisible(prev => ({ ...prev, [f.key]: !visible }))}
                    title={visible ? t("mcp.secretHide") : t("mcp.secretShow")}
                  >
                    {visible ? <EyeOff size={14} /> : <Eye size={14} />}
                  </Button>
                )}
              </div>
              {f.help && renderHelpText(f.help, f.helpUrl)}
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-between border-t pt-3">
        <div className="text-xs text-muted-foreground">
          {missingCount > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
              <AlertTriangle size={12} />
              {t("mcp.configMissing", { count: missingCount })}
            </span>
          ) : (
            <span className="text-emerald-600 dark:text-emerald-400">{t("mcp.configComplete")}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={handleTestConnection} disabled={testing || saving}>
            {testing ? <Loader2 className="animate-spin" size={14} /> : <Plug size={14} />}
            {t("mcp.testConnect") || "测试连接"}
          </Button>
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="animate-spin" size={14} /> : <Save size={14} />}
            {t("mcp.configSave")}
          </Button>
        </div>
      </div>
    </div>
  );
}

type QuickConfigDialogState = {
  serverName: string;
  schema: MCPConfigField[];
  missingFields: { key: string; label: string }[];
} | null;

function QuickConfigDialog({
  state,
  onClose,
  envDraft,
  onEnvChange,
  onSaveAndConnect,
  t,
}: {
  state: QuickConfigDialogState;
  onClose: () => void;
  envDraft: EnvMap;
  onEnvChange: (update: (prev: EnvMap) => EnvMap) => void;
  onSaveAndConnect: (serverName: string, keys: string[]) => Promise<void>;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  const [secretVisible, setSecretVisible] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  if (!state) return null;

  const relevantFields = state.schema.filter(f =>
    state.missingFields.some(m => m.key === f.key)
  );

  const handleSaveAndConnect = async () => {
    setSaving(true);
    try {
      await onSaveAndConnect(state.serverName, state.schema.map(f => f.key));
    } finally {
      setSaving(false);
      onClose();
    }
  };

  return (
    <Dialog open={!!state} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle size={16} className="text-amber-500" />
            {t("mcp.configRequired")}
          </DialogTitle>
          <DialogDescription>
            {t("mcp.configMissingFields", { fields: state.missingFields.map(f => f.label).join(", ") })}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          {relevantFields.map(f => {
            const val = envDraft[f.key] ?? "";
            const isSecret = f.type === "secret";
            const visible = secretVisible[f.key] ?? false;

            return (
              <div key={f.key} className="space-y-2">
                <Label className="text-sm">
                  {f.label || f.key}
                  {f.required && <span className="ml-1 text-destructive">*</span>}
                </Label>
                <div className="relative">
                  <Input
                    type={isSecret && !visible ? "password" : "text"}
                    value={val}
                    onChange={e => onEnvChange(prev => ({ ...prev, [f.key]: e.target.value }))}
                    placeholder={f.placeholder || `${f.label || f.key}`}
                    className={isSecret ? "pr-10 font-mono text-xs" : "font-mono text-xs"}
                  />
                  {isSecret && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                      onClick={() => setSecretVisible(prev => ({ ...prev, [f.key]: !visible }))}
                    >
                      {visible ? <EyeOff size={14} /> : <Eye size={14} />}
                    </Button>
                  )}
                </div>
                {f.help && <p className="text-xs text-muted-foreground">{f.help}</p>}
              </div>
            );
          })}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t("common.cancel") || "取消"}</Button>
          <Button onClick={handleSaveAndConnect} disabled={saving}>
            {saving ? <Loader2 className="animate-spin" size={14} /> : <Plug size={14} />}
            {t("mcp.saveAndConnect") || "保存并连接"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function MCPView({
  serviceRunning,
  apiBaseUrl = "http://127.0.0.1:18900",
  envDraft,
  onEnvChange,
  onSaveEnvKeys,
}: {
  serviceRunning: boolean;
  apiBaseUrl?: string;
  envDraft: EnvMap;
  onEnvChange: React.Dispatch<React.SetStateAction<EnvMap>>;
  onSaveEnvKeys: (keys: string[]) => Promise<void>;
}) {
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
  const [quickConfigDialog, setQuickConfigDialog] = useState<QuickConfigDialogState>(null);

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
      } else if (data.status === "config_incomplete") {
        const server = servers.find(s => s.name === name);
        if (server?.config_schema?.length) {
          setQuickConfigDialog({
            serverName: name,
            schema: server.config_schema,
            missingFields: data.missing_fields || [],
          });
        } else {
          const fields = (data.missing_fields || []).map((f: { label: string }) => f.label).join(", ");
          toast.error(t("mcp.configMissingFields", { fields }) || data.message);
          setExpandedServer(name);
        }
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
      const headersObj: Record<string, string> = {};
      if (form.headers.trim()) {
        for (const line of form.headers.trim().split("\n")) {
          const idx = line.indexOf("=");
          if (idx > 0) headersObj[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
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
          headers: Object.keys(headersObj).length > 0 ? headersObj : undefined,
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
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 items-start gap-4">
              <div className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <IconLink size={22} />
              </div>
              <div className="min-w-0 space-y-2">
                <div className="flex min-w-0 items-center gap-3">
                  <CardTitle className="truncate text-xl tracking-tight" title={t("mcp.title")}>
                    {t("mcp.title")}
                  </CardTitle>
                  {!mcpEnabled && (
                    <Badge
                      variant="outline"
                      className="max-w-full shrink overflow-hidden text-ellipsis whitespace-nowrap border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
                      title={t("mcp.disabled") || "MCP 已禁用"}
                    >
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

            <div className="flex shrink-0 items-center gap-2">
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
            {(form.transport === "streamable_http" || form.transport === "sse") && (
              <div className="space-y-2 md:col-span-2">
                <Label>{t("mcp.headersLabel") || "请求头 (Headers)"}</Label>
                <Textarea
                  value={form.headers}
                  onChange={e => setForm({ ...form, headers: e.target.value })}
                  placeholder={"Authorization=${MY_TOKEN}\nX-Custom-Header=value"}
                  rows={3}
                  className="resize-y font-mono text-xs"
                />
                <p className="text-xs text-muted-foreground">
                  {t("mcp.headersHint") || "每行一个，格式 KEY=VALUE。支持 ${VAR} 变量替换（从 .env 文件读取）。"}
                </p>
              </div>
            )}
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
          {servers.map((s) => {
            const isBusy = busy === s.name;

            return (
            <Card key={s.name} className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm transition-shadow hover:shadow-md">
              <CardHeader className="gap-3 px-6 py-4">
                <div
                  className="flex cursor-pointer items-center justify-between gap-4"
                  onClick={() => toggleExpand(s.name)}
                >
                  <div className="flex min-w-0 flex-1 items-center gap-4">
                    <ConnectionIndicator connected={s.connected} busy={isBusy} />
                    <div className="min-w-0 flex-1 space-y-3">
                      <div className="flex min-w-0 items-center gap-2">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="pointer-events-none -ml-2"
                        >
                          {expandedServer === s.name ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                        </Button>
                        <CardTitle className="min-w-0 truncate text-base" title={s.name}>
                          {s.name}
                        </CardTitle>
                        <Badge
                          variant="secondary"
                          className="max-w-[96px] shrink overflow-hidden text-ellipsis whitespace-nowrap"
                          title={transportLabel(s.transport)}
                        >
                          {transportLabel(s.transport)}
                        </Badge>
                        <Badge
                          variant="outline"
                          className={`max-w-[120px] shrink overflow-hidden text-ellipsis whitespace-nowrap ${
                            s.source === "workspace" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : ""
                          }`}
                          title={s.source === "workspace" ? t("mcp.sourceWorkspace") : t("mcp.sourceBuiltin")}
                        >
                          {s.source === "workspace" ? t("mcp.sourceWorkspace") : t("mcp.sourceBuiltin")}
                        </Badge>
                        {s.connected ? (
                          <Badge
                            variant="outline"
                            className="max-w-[110px] shrink overflow-hidden text-ellipsis whitespace-nowrap border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                            title={t("mcp.connected")}
                          >
                            {t("mcp.connected")}
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="max-w-[110px] shrink overflow-hidden text-ellipsis whitespace-nowrap text-muted-foreground"
                            title={t("mcp.disconnected")}
                          >
                            {t("mcp.disconnected")}
                          </Badge>
                        )}
                        {s.config_schema?.length > 0 && !s.config_complete && (
                          <Badge
                            variant="outline"
                            className="max-w-[120px] shrink overflow-hidden text-ellipsis whitespace-nowrap border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
                            title={t("mcp.configIncomplete")}
                          >
                            <AlertTriangle size={11} className="mr-0.5" />
                            {t("mcp.configIncomplete")}
                          </Badge>
                        )}
                      </div>

                      {s.description && (
                        <CardDescription className="max-w-3xl text-sm leading-6">
                          <span className="block truncate" title={s.description}>
                            {s.description}
                          </span>
                        </CardDescription>
                      )}

                      <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                        <Badge
                          variant="outline"
                          className="max-w-[170px] shrink gap-1 overflow-hidden text-ellipsis whitespace-nowrap"
                          title={s.connected ? t("mcp.toolCount", { count: s.tool_count }) : t("mcp.toolCountCatalog", { count: s.catalog_tool_count })}
                        >
                          <Wrench size={12} />
                          {s.connected ? t("mcp.toolCount", { count: s.tool_count }) : t("mcp.toolCountCatalog", { count: s.catalog_tool_count })}
                        </Badge>
                        {s.has_instructions && (
                          <Badge
                            variant="outline"
                            className="max-w-[120px] shrink gap-1 overflow-hidden text-ellipsis whitespace-nowrap"
                            title={t("mcp.instructions")}
                          >
                            <Info size={12} />
                            {t("mcp.instructions")}
                          </Badge>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center gap-2 self-center" onClick={(e) => e.stopPropagation()}>
                    {s.connected ? (
                      <Button
                        variant="outline"
                        onClick={() => disconnectServer(s.name)}
                        disabled={isBusy}
                        className="text-amber-600 border-amber-300 hover:bg-amber-50 hover:text-amber-700 dark:text-amber-400 dark:border-amber-700 dark:hover:bg-amber-950"
                      >
                        {isBusy ? <Loader2 className="animate-spin" size={14} /> : <Unplug size={14} />}
                        {t("mcp.disconnect")}
                      </Button>
                    ) : (
                      <Button onClick={() => connectServer(s.name)} disabled={isBusy} className="self-center">
                        {isBusy ? <Loader2 className="animate-spin" size={14} /> : <Plug size={14} />}
                        {t("mcp.connect")}
                      </Button>
                    )}
                    {s.removable && (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => removeServer(s.name)}
                        disabled={isBusy}
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
                  {s.config_schema && s.config_schema.length > 0 && (
                    <MCPConfigForm
                      schema={s.config_schema}
                      configStatus={s.config_status}
                      envDraft={envDraft}
                      onEnvChange={onEnvChange}
                      onSave={async (keys) => {
                        await onSaveEnvKeys(keys);
                        await fetchServers();
                      }}
                      serverName={s.name}
                      serverTransport={s.transport}
                      apiBaseUrl={apiBaseUrl}
                      onRefresh={fetchServers}
                      t={t}
                    />
                  )}
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
                            <div className="truncate text-sm font-medium text-foreground" title={tool.name}>{tool.name}</div>
                            {tool.description && (
                              <div className="mt-2 truncate text-sm leading-6 text-muted-foreground" title={tool.description}>
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
          );
          })}
        </div>
      )}

      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
      <QuickConfigDialog
        state={quickConfigDialog}
        onClose={() => setQuickConfigDialog(null)}
        envDraft={envDraft}
        onEnvChange={onEnvChange}
        onSaveAndConnect={async (serverName, keys) => {
          await onSaveEnvKeys(keys);
          await fetchServers();
          await connectServer(serverName);
        }}
        t={t}
      />
    </div>
  );
}
