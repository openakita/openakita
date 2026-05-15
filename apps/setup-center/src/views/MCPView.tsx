import { useEffect, useState, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  IconLink,
  IconChevronDown, IconChevronRight,
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
import { Loader2, RefreshCw, Plus, Trash2, Plug, Unplug, Info, Server, Wrench, Eye, EyeOff, Save, AlertTriangle, Search, CheckCircle2, CircleDashed, XCircle, Link2 } from "lucide-react";
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
  enabled: boolean;
  auto_connect: boolean;
  config_schema: MCPConfigField[];
  config_status: Record<string, boolean>;
  config_complete: boolean;
};

type ServerBusyAction = "connect" | "disconnect" | "delete" | "add" | null;
type MCPStatusFilter = "all" | "connected" | "disconnected" | "needs_config" | "error";
type MCPSourceFilter = "all" | "builtin" | "workspace";
type MCPSortBy = "recent" | "name" | "status";

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

function ConnectionStatusChip({
  connected,
  busyAction,
  error,
  t,
}: {
  connected: boolean;
  busyAction: ServerBusyAction;
  error: boolean;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  if (busyAction === "connect") {
    return (
      <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-blue-500/30 bg-blue-500/10 px-2 text-[11px] font-medium text-blue-700 dark:text-blue-300">
        <Loader2 size={11} className="animate-spin" />
        {t("mcp.connecting")}
      </span>
    );
  }

  if (error) {
    return (
      <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-red-500/30 bg-red-500/10 px-2 text-[11px] font-medium text-red-700 dark:text-red-300">
        <XCircle size={11} />
        {t("mcp.connectionFailed")}
      </span>
    );
  }

  if (connected) {
    return (
      <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
        <CheckCircle2 size={11} />
        {t("mcp.connected")}
      </span>
    );
  }

  return (
    <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2 text-[11px] font-medium text-muted-foreground">
      <CircleDashed size={11} />
      {t("mcp.disconnected")}
    </span>
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

function parseKeyValueLines(raw: string): Record<string, string> {
  const parsed: Record<string, string> = {};
  if (!raw.trim()) return parsed;
  for (const line of raw.trim().split("\n")) {
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    parsed[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return parsed;
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
        signal: AbortSignal.timeout(60_000),
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
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<MCPStatusFilter>("all");
  const [sourceFilter, setSourceFilter] = useState<MCPSourceFilter>("all");
  const [sortBy, setSortBy] = useState<MCPSortBy>("recent");
  const [expandedServer, setExpandedServer] = useState<string | null>(null);
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<AddServerForm>({ ...emptyForm });
  const [busyTarget, setBusyTarget] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<ServerBusyAction>(null);
  const [failedServers, setFailedServers] = useState<Record<string, boolean>>({});
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
      setFetchError(null);
    } catch {
      setFetchError(t("mcp.fetchFailed"));
    }
    setLoading(false);
  }, [serviceRunning, apiBaseUrl, t]);

  useEffect(() => { fetchServers(); }, [fetchServers]);

  const showMsg = (text: string, ok: boolean) => {
    if (ok) toast.success(text);
    else toast.error(text);
  };

  const connectServer = async (name: string) => {
    setBusyTarget(name);
    setBusyAction("connect");
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/connect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_name: name }),
      });
      const data = await res.json();
      if (data.status === "connected" || data.status === "already_connected") {
        setFailedServers(prev => {
          const next = { ...prev };
          delete next[name];
          return next;
        });
        showMsg(t("mcp.connectSuccess", { name }), true);
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
        setFailedServers(prev => ({ ...prev, [name]: true }));
        showMsg(`${t("mcp.connectFailed")}: ${data.error || t("mcp.unknownError")}`, false);
      }
    } catch (e) {
      setFailedServers(prev => ({ ...prev, [name]: true }));
      showMsg(`${t("mcp.connectError")}: ${e}`, false);
    }
    setBusyTarget(null);
    setBusyAction(null);
  };

  const disconnectServer = async (name: string) => {
    setBusyTarget(name);
    setBusyAction("disconnect");
    try {
      await safeFetch(`${apiBaseUrl}/api/mcp/disconnect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_name: name }),
      });
      setFailedServers(prev => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      showMsg(t("mcp.disconnectSuccess", { name }), true);
      await fetchServers();
    } catch (e) {
      showMsg(`${t("mcp.disconnectError")}: ${e}`, false);
    }
    setBusyTarget(null);
    setBusyAction(null);
  };

  const doRemoveServer = useCallback(async (name: string) => {
    setBusyTarget(name);
    setBusyAction("delete");
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      const data = await res.json();
      if (data.status === "ok") {
        showMsg(t("mcp.deleteSuccess", { name }), true);
        await fetchServers();
      } else {
        showMsg(`${t("mcp.deleteFailed")}: ${data.message || t("mcp.unknownError")}`, false);
      }
    } catch (e) {
      showMsg(`${t("mcp.deleteFailed")}: ${e}`, false);
    }
    setBusyTarget(null);
    setBusyAction(null);
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
    setBusyTarget("add");
    setBusyAction("add");
    try {
      const envObj = parseKeyValueLines(form.env);
      const headersObj = parseKeyValueLines(form.headers);
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
            connMsg = `\n[!] ${t("mcp.autoConnectFailed")}: ${cr.error || t("mcp.unknownError")}`;
          }
        }
        showMsg(`${t("mcp.addSuccess", { name })}${connMsg}`, !cr || cr.connected !== false);
        setForm({ ...emptyForm });
        setShowAdd(false);
        await fetchServers();
      } else {
        showMsg(`${t("mcp.addFailed")}: ${data.message || data.error || t("mcp.unknownError")}`, false);
      }
    } catch (e) {
      showMsg(`${t("mcp.addError")}: ${e}`, false);
    }
    setBusyTarget(null);
    setBusyAction(null);
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

  const connectedCount = servers.filter((server) => server.connected).length;
  const totalTools = servers.reduce((sum, server) => sum + (server.connected ? server.tool_count : server.catalog_tool_count), 0);

  const hasActiveFilters = searchQuery.trim().length > 0 || statusFilter !== "all" || sourceFilter !== "all" || sortBy !== "recent";
  const clearFilters = () => {
    setSearchQuery("");
    setStatusFilter("all");
    setSourceFilter("all");
    setSortBy("recent");
  };

  const filteredServers = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const list = servers.filter((server) => {
      if (query) {
        const target = `${server.name} ${server.description || ""}`.toLowerCase();
        if (!target.includes(query)) return false;
      }

      if (statusFilter === "connected" && !server.connected) return false;
      if (statusFilter === "disconnected" && server.connected) return false;
      if (statusFilter === "needs_config" && (server.config_complete || !server.config_schema?.length)) return false;
      if (statusFilter === "error" && !failedServers[server.name]) return false;

      if (sourceFilter !== "all" && server.source !== sourceFilter) return false;
      return true;
    });

    if (sortBy === "name") {
      return list.sort((a, b) => a.name.localeCompare(b.name));
    }

    if (sortBy === "status") {
      const statusWeight = (server: MCPServer): number => {
        if (failedServers[server.name]) return 0;
        if (server.connected) return 1;
        if (server.config_schema?.length > 0 && !server.config_complete) return 2;
        return 3;
      };
      return list.sort((a, b) => statusWeight(a) - statusWeight(b) || a.name.localeCompare(b.name));
    }

    return list;
  }, [servers, searchQuery, statusFilter, sourceFilter, sortBy, failedServers]);

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconLink size={48} />
        <div className="mt-3 font-semibold">MCP</div>
        <div className="mt-1 text-xs opacity-70">{t("mcp.serviceNotRunning")}</div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      <Card className="gap-0 overflow-hidden border-primary/20 bg-gradient-to-br from-primary/[0.08] via-primary/[0.03] to-background py-0 shadow-sm">
        <CardHeader className="gap-3 px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-1.5">
              <div className="flex items-center gap-2">
                <CardTitle className="truncate text-lg tracking-tight" title={t("mcp.title")}>
                  {t("mcp.title")}
                </CardTitle>
                <Badge
                  variant="outline"
                  className={mcpEnabled
                    ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                    : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"}
                >
                  {mcpEnabled ? t("mcp.enabled") : t("mcp.disabled")}
                </Badge>
              </div>
              <CardDescription className="text-sm">{t("mcp.subtitle")}</CardDescription>
              {showHelp && (
                <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs leading-6 text-muted-foreground">
                  {t("mcp.helpLine2")}
                  <br />
                  {t("mcp.helpLine3")}
                </div>
              )}
            </div>

            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => setShowHelp((v) => !v)}>
                <Info size={14} />
                {t("mcp.learnMore")}
              </Button>
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
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-background/75 px-3 py-1 text-xs">
              <span className="text-muted-foreground">{t("mcp.metricsServers")}</span>
              <strong>{servers.length}</strong>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-background/75 px-3 py-1 text-xs">
              <span className="text-muted-foreground">{t("mcp.metricsConnected")}</span>
              <strong className="text-emerald-600">{connectedCount}</strong>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-background/75 px-3 py-1 text-xs">
              <span className="text-muted-foreground">{t("mcp.metricsTools")}</span>
              <strong>{totalTools}</strong>
            </span>
          </div>
        </CardHeader>
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
                  placeholder={'如: -y\n@modelcontextprotocol/server-filesystem\n或每行一个参数:\n-m\nmcp_server_fetch\n"C:\\My Path\\dir"'}
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
              <Button onClick={addServer} disabled={busyAction === "add"}>
                {busyAction === "add" && <Loader2 className="animate-spin" size={14} />}
                {t("mcp.add")}
              </Button>
            </div>
          </CardFooter>
        </Card>
      )}

      <Card className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm">
        {mcpEnabled && (
          <CardContent className="border-b bg-muted/25 px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-[220px] flex-1">
                <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t("mcp.searchPlaceholder")}
                  className="pl-8"
                />
              </div>
              <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as MCPStatusFilter)}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("mcp.filterStatusAll")}</SelectItem>
                  <SelectItem value="connected">{t("mcp.filterStatusConnected")}</SelectItem>
                  <SelectItem value="disconnected">{t("mcp.filterStatusDisconnected")}</SelectItem>
                  <SelectItem value="needs_config">{t("mcp.filterStatusNeedsConfig")}</SelectItem>
                  <SelectItem value="error">{t("mcp.filterStatusError")}</SelectItem>
                </SelectContent>
              </Select>
              <Select value={sourceFilter} onValueChange={(v) => setSourceFilter(v as MCPSourceFilter)}>
                <SelectTrigger className="w-[130px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("mcp.filterSourceAll")}</SelectItem>
                  <SelectItem value="builtin">{t("mcp.sourceBuiltin")}</SelectItem>
                  <SelectItem value="workspace">{t("mcp.sourceWorkspace")}</SelectItem>
                </SelectContent>
              </Select>
              <Select value={sortBy} onValueChange={(v) => setSortBy(v as MCPSortBy)}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="recent">{t("mcp.sortRecent")}</SelectItem>
                  <SelectItem value="name">{t("mcp.sortName")}</SelectItem>
                  <SelectItem value="status">{t("mcp.sortStatus")}</SelectItem>
                </SelectContent>
              </Select>
              {hasActiveFilters && (
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  {t("mcp.clearFilters")}
                </Button>
              )}
              <span className="ml-auto text-xs text-muted-foreground">
                {t("mcp.resultCount", { count: filteredServers.length })}
              </span>
            </div>
          </CardContent>
        )}

        <CardContent className="px-4 py-4">
          {!mcpEnabled ? (
            <div className="space-y-2 py-6 text-center text-sm text-muted-foreground">
              <p className="text-base font-medium text-foreground">{t("mcp.disabled")}</p>
              <p>{t("mcp.disabledHint")}</p>
            </div>
          ) : fetchError ? (
            <div className="space-y-3 py-6 text-center text-sm text-muted-foreground">
              <p className="text-base font-medium text-foreground">{t("mcp.loadFailedTitle")}</p>
              <p>{fetchError}</p>
              <Button variant="outline" onClick={fetchServers}>
                <RefreshCw size={14} />
                {t("common.retry")}
              </Button>
            </div>
          ) : loading && servers.length === 0 ? (
            <div className="py-6 text-center text-sm text-muted-foreground">
              {t("common.loading")}
            </div>
          ) : servers.length === 0 ? (
            <div className="space-y-3 py-8 text-center text-muted-foreground">
              <p className="text-base font-medium text-foreground">{t("mcp.noServers")}</p>
              <p className="mt-2 text-sm">{t("mcp.noServersHint")}</p>
              <Button variant="outline" onClick={() => setShowAdd(true)}>
                <Plus size={14} />
                {t("mcp.addServer")}
              </Button>
            </div>
          ) : filteredServers.length === 0 ? (
            <div className="space-y-3 py-8 text-center text-muted-foreground">
              <p className="text-base font-medium text-foreground">{t("mcp.noMatchedServers")}</p>
              <p className="text-sm">{t("mcp.noMatchedServersHint")}</p>
              <Button variant="outline" onClick={clearFilters}>
                {t("mcp.clearFilters")}
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {filteredServers.map((s) => {
                const isBusy = busyTarget === s.name;
                const rowBusyAction: ServerBusyAction = isBusy ? busyAction : null;
                const hasConfigIssue = s.config_schema?.length > 0 && !s.config_complete;

                return (
                <Card key={s.name} className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm transition-shadow hover:shadow-md">
                  <CardHeader className="gap-3 px-3 py-3 sm:px-4">
                    <div
                      className="flex cursor-pointer items-center justify-between gap-3"
                      onClick={() => toggleExpand(s.name)}
                    >
                      <div className="flex min-w-0 flex-1 items-start gap-2">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="-ml-1 -mt-0.5 shrink-0"
                          title={expandedServer === s.name ? t("mcp.collapseDetails") : t("mcp.expandDetails")}
                          aria-label={expandedServer === s.name ? t("mcp.collapseDetails") : t("mcp.expandDetails")}
                        >
                          {expandedServer === s.name ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                        </Button>
                        <div className="min-w-0 flex-1 space-y-2">
                          <div className="flex min-w-0 flex-wrap items-center gap-2">
                            <ConnectionStatusChip connected={s.connected} busyAction={rowBusyAction} error={!!failedServers[s.name]} t={t} />
                            <CardTitle className="min-w-0 truncate text-base" title={s.name}>
                              {s.name}
                            </CardTitle>
                            {!s.enabled && (
                              <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400">
                                {t("mcp.serverDisabledTag")}
                              </Badge>
                            )}
                          </div>
                          {s.description && (
                            <CardDescription className="truncate text-sm" title={s.description}>
                              {s.description}
                            </CardDescription>
                          )}
                          <div className="flex flex-wrap items-center gap-2 text-xs">
                            <Badge variant="secondary">{transportLabel(s.transport)}</Badge>
                            <Badge variant="outline">{s.source === "workspace" ? t("mcp.sourceWorkspace") : t("mcp.sourceBuiltin")}</Badge>
                            <Badge variant="outline" className="gap-1">
                              <Wrench size={11} />
                              {s.connected ? t("mcp.toolCount", { count: s.tool_count }) : t("mcp.toolCountCatalog", { count: s.catalog_tool_count })}
                            </Badge>
                            {hasConfigIssue && (
                              <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400">
                                <AlertTriangle size={11} className="mr-1" />
                                {t("mcp.configIncomplete")}
                              </Badge>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="flex shrink-0 items-center gap-2 self-center" onClick={(e) => e.stopPropagation()}>
                        {s.connected ? (
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => disconnectServer(s.name)}
                            disabled={isBusy}
                            className="h-9 min-w-[92px] px-4"
                          >
                            {isBusy ? <Loader2 className="animate-spin" size={14} /> : <Unplug size={14} />}
                            {t("mcp.disconnect")}
                          </Button>
                        ) : hasConfigIssue ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => toggleExpand(s.name)}
                            disabled={isBusy}
                            className="h-9 min-w-[92px] px-4"
                          >
                            <Link2 size={14} />
                            {t("mcp.goConfig")}
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            onClick={() => connectServer(s.name)}
                            disabled={isBusy || !s.enabled}
                            className="h-9 min-w-[92px] self-center px-4"
                          >
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
                      <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">
                        <div className="mb-1 flex items-center gap-2 font-medium text-foreground">
                          <Server size={14} />
                          {t("mcp.basicInfo")}
                        </div>
                        {s.transport === "streamable_http" || s.transport === "sse" ? (
                          <span>{transportLabel(s.transport)} URL: <code>{s.url}</code></span>
                        ) : (
                          <span>{t("mcp.commandLabel")}: <code>{s.command}</code></span>
                        )}
                      </div>

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

                      {s.tools.length > 0 ? (
                        <div className="space-y-3">
                          <div className="text-sm font-semibold text-foreground">
                            {t("mcp.toolsAndInstructions")} ({s.tools.length})
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
                            <AlertTriangle size={14} className="text-amber-600 dark:text-amber-400" />
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
        </CardContent>
      </Card>

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
