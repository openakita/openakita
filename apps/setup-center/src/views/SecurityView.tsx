import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  IconShield, IconRefresh, IconPlus, IconX, IconTrash,
  IconChevronDown, IconChevronRight, IconClock, IconSave, IconAlertCircle,
} from "../icons";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { toast } from "sonner";
import { Loader2, RotateCw, Save, ShieldAlert } from "lucide-react";

type SecurityViewProps = {
  apiBaseUrl: string;
  serviceRunning: boolean;
};

type ZoneConfig = {
  workspace: string[];
  controlled: string[];
  protected: string[];
  forbidden: string[];
  default_zone?: string;
};

type CommandConfig = {
  custom_critical: string[];
  custom_high: string[];
  excluded_patterns: string[];
  blocked_commands: string[];
};

type SandboxConfig = {
  enabled: boolean;
  backend: string;
  sandbox_risk_levels: string[];
  exempt_commands: string[];
};

type AuditEntry = {
  ts: number;
  tool: string;
  decision: string;
  reason: string;
  policy: string;
};

type CheckpointEntry = {
  checkpoint_id: string;
  timestamp: number;
  tool_name: string;
  description: string;
  file_count: number;
};

const ZONE_META: Record<string, { color: string; tw: string }> = {
  workspace: { color: "#22c55e", tw: "bg-emerald-500" },
  controlled: { color: "#3b82f6", tw: "bg-blue-500" },
  protected: { color: "#f59e0b", tw: "bg-amber-500" },
  forbidden: { color: "#ef4444", tw: "bg-red-500" },
};

const BACKEND_OPTIONS = [
  { value: "auto", label: "Auto" },
  { value: "low_integrity", label: "Low Integrity (Windows)" },
  { value: "bubblewrap", label: "Bubblewrap (Linux)" },
  { value: "seatbelt", label: "Seatbelt (macOS)" },
  { value: "docker", label: "Docker" },
  { value: "none", label: "None (Disabled)" },
];

type TabId = "zones" | "commands" | "sandbox" | "audit" | "checkpoints";

export default function SecurityView({ apiBaseUrl, serviceRunning }: SecurityViewProps) {
  const { t } = useTranslation();

  const [tab, setTab] = useState<TabId>("zones");
  const [zones, setZones] = useState<ZoneConfig>({ workspace: [], controlled: [], protected: [], forbidden: [] });
  const [commands, setCommands] = useState<CommandConfig>({ custom_critical: [], custom_high: [], excluded_patterns: [], blocked_commands: [] });
  const [sandbox, setSandbox] = useState<SandboxConfig>({ enabled: true, backend: "auto", sandbox_risk_levels: ["HIGH"], exempt_commands: [] });
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [checkpoints, setCheckpoints] = useState<CheckpointEntry[]>([]);
  const [saving, setSaving] = useState(false);

  const api = useCallback(async (path: string, method = "GET", body?: unknown) => {
    const opts: RequestInit = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(`${apiBaseUrl}${path}`, opts);
    return res.json();
  }, [apiBaseUrl]);

  const load = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const [zRes, cRes, sRes] = await Promise.all([
        api("/api/config/security/zones"),
        api("/api/config/security/commands"),
        api("/api/config/security/sandbox"),
      ]);
      setZones(zRes);
      setCommands(cRes);
      setSandbox(sRes);
    } catch { /* ignore */ }
  }, [api, serviceRunning]);

  useEffect(() => { load(); }, [load]);

  const loadAudit = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await api("/api/config/security/audit");
      setAudit(res.entries || []);
    } catch { /* ignore */ }
  }, [api, serviceRunning]);

  const loadCheckpoints = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await api("/api/config/security/checkpoints");
      setCheckpoints(res.checkpoints || []);
    } catch { /* ignore */ }
  }, [api, serviceRunning]);

  useEffect(() => {
    if (tab === "audit") loadAudit();
    if (tab === "checkpoints") loadCheckpoints();
  }, [tab, loadAudit, loadCheckpoints]);

  const doSave = async (endpoint: string, body: unknown, successKey: string) => {
    setSaving(true);
    try {
      await api(endpoint, "POST", body);
      toast.success(t(`security.${successKey}`));
    } catch {
      toast.error(t("security.saveFailed"));
    }
    setSaving(false);
  };

  const rewindCheckpoint = async (id: string) => {
    if (!confirm(t("security.rewindConfirm", { id }))) return;
    try {
      await api("/api/config/security/checkpoint/rewind", "POST", { checkpoint_id: id });
      toast.success(t("security.rewound"));
      loadCheckpoints();
    } catch { /* ignore */ }
  };

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
        <ShieldAlert size={32} className="mb-3 opacity-50" />
        <p className="text-sm">{t("security.backendOff")}</p>
      </div>
    );
  }

  const TABS: { id: TabId; labelKey: string }[] = [
    { id: "zones", labelKey: "security.zones" },
    { id: "commands", labelKey: "security.commands" },
    { id: "sandbox", labelKey: "security.sandbox" },
    { id: "audit", labelKey: "security.audit" },
    { id: "checkpoints", labelKey: "security.checkpoints" },
  ];

  return (
    <div className="mx-auto max-w-[1080px] space-y-5 px-6 py-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5 min-w-0">
          <h2 className="text-lg font-bold tracking-tight">
            {t("security.title", "安全控制")}
          </h2>
          <p className="text-xs text-muted-foreground leading-relaxed">
            {t("security.desc", "配置系统安全策略，包括文件访问区域、命令拦截和沙箱环境。")}
          </p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center justify-between flex-shrink-0">
        <ToggleGroup
          type="single"
          value={tab}
          onValueChange={(v) => { if (v) setTab(v as TabId); }}
          variant="outline"
        >
          {TABS.map((tb) => (
            <ToggleGroupItem
              key={tb.id}
              value={tb.id}
              className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
            >
              {t(tb.labelKey)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      {/* Zones */}
      {tab === "zones" && (
        <Card className="p-0 gap-0 border-border/50 shadow-sm">
          <CardHeader className="px-5 py-3 pb-3 border-b border-border/50">
            <CardTitle className="text-sm font-semibold">{t("security.zones", "安全区域")}</CardTitle>
          </CardHeader>
          <CardContent className="px-5 pt-4 pb-5 space-y-4">
            <p className="text-sm text-muted-foreground">{t("security.zonesDesc")}</p>
            <div className="grid grid-cols-1 gap-3">
              {(["workspace", "controlled", "protected", "forbidden"] as const).map((zone) => (
                <ZonePanel
                  key={zone}
                  zone={zone}
                  paths={zones[zone] || []}
                  onChange={(paths) => setZones((prev) => ({ ...prev, [zone]: paths }))}
                />
              ))}
            </div>
            <div className="flex justify-end pt-2">
              <Button onClick={() => doSave("/api/config/security/zones", zones, "zonesSaved")} disabled={saving}>
                {saving ? <Loader2 className="size-4 animate-spin mr-2" /> : <Save size={14} className="mr-2" />}
                {t("security.save")}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Commands */}
      {tab === "commands" && (
        <Card className="p-0 gap-0 border-border/50 shadow-sm">
          <CardHeader className="px-5 py-3 pb-3 border-b border-border/50">
            <CardTitle className="text-sm font-semibold">{t("security.commands", "命令拦截")}</CardTitle>
          </CardHeader>
          <CardContent className="px-5 pt-4 pb-5 space-y-5">
            <p className="text-sm text-muted-foreground">{t("security.commandsDesc")}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <TagEditor
                label={t("security.criticalPatterns")}
                items={commands.custom_critical}
                onChange={(v) => setCommands((p) => ({ ...p, custom_critical: v }))}
                placeholder={`e.g. rm\\s+-rf\\s+/`}
              />
              <TagEditor
                label={t("security.highPatterns")}
                items={commands.custom_high}
                onChange={(v) => setCommands((p) => ({ ...p, custom_high: v }))}
                placeholder="e.g. Remove-Item.*-Recurse"
              />
              <TagEditor
                label={t("security.excludedPatterns")}
                items={commands.excluded_patterns}
                onChange={(v) => setCommands((p) => ({ ...p, excluded_patterns: v }))}
                placeholder={t("security.excludedPh")}
              />
              <TagEditor
                label={t("security.blockedCommands")}
                items={commands.blocked_commands}
                onChange={(v) => setCommands((p) => ({ ...p, blocked_commands: v }))}
                placeholder="e.g. diskpart"
              />
            </div>
            <div className="flex justify-end pt-2 border-t border-border/50 mt-6 pt-4">
              <Button onClick={() => doSave("/api/config/security/commands", commands, "commandsSaved")} disabled={saving}>
                {saving ? <Loader2 className="size-4 animate-spin mr-2" /> : <Save size={14} className="mr-2" />}
                {t("security.save")}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Sandbox */}
      {tab === "sandbox" && (
        <Card className="p-0 gap-0 border-border/50 shadow-sm">
          <CardHeader className="px-5 py-3 pb-3 border-b border-border/50">
            <CardTitle className="text-sm font-semibold">{t("security.sandbox", "沙箱配置")}</CardTitle>
          </CardHeader>
          <CardContent className="px-5 pt-4 pb-5 space-y-5">
            <p className="text-sm text-muted-foreground">{t("security.sandboxDesc")}</p>
            <div className="space-y-4 max-w-md">
              <div className="flex items-center justify-between border border-border/50 p-4 rounded-lg bg-muted/20">
                <div className="space-y-0.5">
                  <Label className="text-sm font-medium">{t("security.sandboxEnabled")}</Label>
                  <p className="text-xs text-muted-foreground">启用或禁用命令执行沙箱</p>
                </div>
                <Switch
                  checked={sandbox.enabled}
                  onCheckedChange={(v) => setSandbox((p) => ({ ...p, enabled: v }))}
                />
              </div>
              <div className="space-y-2">
                <Label className="text-sm font-medium">{t("security.sandboxBackend")}</Label>
                <Select
                  value={sandbox.backend}
                  onValueChange={(v) => setSandbox((p) => ({ ...p, backend: v }))}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BACKEND_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground pt-1">选择用于隔离执行环境的后端技术</p>
              </div>
            </div>
            <div className="flex justify-end pt-2 border-t border-border/50 mt-6 pt-4">
              <Button onClick={() => doSave("/api/config/security/sandbox", sandbox, "sandboxSaved")} disabled={saving}>
                {saving ? <Loader2 className="size-4 animate-spin mr-2" /> : <Save size={14} className="mr-2" />}
                {t("security.save")}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Audit */}
      {tab === "audit" && (
        <Card className="p-0 gap-0 border-border/50 shadow-sm overflow-hidden">
          <CardHeader className="px-5 py-3 pb-3 border-b border-border/50 flex flex-row items-center justify-between space-y-0">
            <div className="space-y-1">
              <CardTitle className="text-sm font-semibold">{t("security.audit", "审计日志")}</CardTitle>
              <p className="text-xs text-muted-foreground">
                {t("security.auditCount", { count: audit.length })}
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={loadAudit} className="h-8">
              <RotateCw size={14} className="mr-1.5" /> {t("security.refresh")}
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            {audit.length === 0 ? (
              <div className="py-16 text-center text-muted-foreground text-sm flex flex-col items-center">
                <IconShield size={32} className="mb-3 opacity-20" />
                {t("security.noAudit")}
              </div>
            ) : (
              <Table>
                <TableHeader className="bg-muted/30">
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="w-[100px] text-xs h-10 px-5 font-medium">{t("security.auditDecision")}</TableHead>
                    <TableHead className="text-xs h-10 px-4 font-medium">{t("security.auditTool")}</TableHead>
                    <TableHead className="hidden sm:table-cell text-xs h-10 px-4 font-medium">{t("security.auditReason")}</TableHead>
                    <TableHead className="w-[120px] text-right text-xs h-10 px-5 font-medium">{t("security.auditTime")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[...audit].reverse().map((e, i) => (
                    <TableRow key={i} className="border-b-border/50 transition-colors hover:bg-muted/20">
                      <TableCell className="px-5 py-3"><DecisionBadge decision={e.decision} /></TableCell>
                      <TableCell className="px-4 py-3 font-medium text-sm">{e.tool}</TableCell>
                      <TableCell className="hidden sm:table-cell px-4 py-3 text-muted-foreground text-xs max-w-[300px] truncate" title={e.reason}>{e.reason}</TableCell>
                      <TableCell className="px-5 py-3 text-right text-xs text-muted-foreground whitespace-nowrap font-mono">
                        {new Date(e.ts * 1000).toLocaleTimeString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Checkpoints */}
      {tab === "checkpoints" && (
        <Card className="p-0 gap-0 border-border/50 shadow-sm overflow-hidden">
          <CardHeader className="px-5 py-3 pb-3 border-b border-border/50 flex flex-row items-center justify-between space-y-0">
            <div className="space-y-1">
              <CardTitle className="text-sm font-semibold">{t("security.checkpoints", "安全检查点")}</CardTitle>
              <p className="text-xs text-muted-foreground">
                {t("security.checkpointCount", { count: checkpoints.length })}
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={loadCheckpoints} className="h-8">
              <RotateCw size={14} className="mr-1.5" /> {t("security.refresh")}
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            {checkpoints.length === 0 ? (
              <div className="py-16 text-center text-muted-foreground text-sm flex flex-col items-center">
                <IconClock size={32} className="mb-3 opacity-20" />
                {t("security.noCheckpoints")}
              </div>
            ) : (
              <Table>
                <TableHeader className="bg-muted/30">
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="text-xs h-10 px-5 font-medium">ID</TableHead>
                    <TableHead className="text-xs h-10 px-4 font-medium">{t("security.checkpointTool")}</TableHead>
                    <TableHead className="hidden sm:table-cell text-xs h-10 px-4 font-medium">{t("security.checkpointFiles")}</TableHead>
                    <TableHead className="hidden sm:table-cell text-xs h-10 px-4 font-medium">{t("security.checkpointTime")}</TableHead>
                    <TableHead className="w-[100px] text-right text-xs h-10 px-5 font-medium" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {checkpoints.map((cp) => (
                    <TableRow key={cp.checkpoint_id} className="border-b-border/50 transition-colors hover:bg-muted/20">
                      <TableCell className="px-5 py-3 font-mono text-xs truncate max-w-[180px]" title={cp.checkpoint_id}>{cp.checkpoint_id}</TableCell>
                      <TableCell className="px-4 py-3 text-sm">{cp.tool_name}</TableCell>
                      <TableCell className="hidden sm:table-cell px-4 py-3 text-xs text-muted-foreground">
                        <Badge variant="outline" className="font-mono">{cp.file_count}</Badge> {t("security.files")}
                      </TableCell>
                      <TableCell className="hidden sm:table-cell px-4 py-3 text-xs text-muted-foreground whitespace-nowrap font-mono">
                        {new Date(cp.timestamp * 1000).toLocaleString()}
                      </TableCell>
                      <TableCell className="px-5 py-3 text-right">
                        <Button variant="outline" size="sm" onClick={() => rewindCheckpoint(cp.checkpoint_id)} className="h-7 text-xs">
                          {t("security.rewind")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/* ─── Sub-components ─── */

function DecisionBadge({ decision }: { decision: string }) {
  const variant = decision === "deny" ? "destructive" : decision === "confirm" ? "outline" : "secondary";
  return (
    <Badge variant={variant} className="text-[11px] uppercase shrink-0">
      {decision}
    </Badge>
  );
}

function ZonePanel({ zone, paths, onChange }: {
  zone: string;
  paths: string[]; onChange: (v: string[]) => void;
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [expanded, setExpanded] = useState(zone === "workspace" || zone === "controlled");
  const meta = ZONE_META[zone];

  const add = () => {
    const v = input.trim();
    if (v && !paths.includes(v)) onChange([...paths, v]);
    setInput("");
  };

  return (
    <Card className="p-0 gap-0 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-3 px-4 py-3.5 text-left hover:bg-accent/50 transition-colors"
      >
        <span className={cn("size-3 rounded-full shrink-0", meta.tw)} />
        <span className="flex-1 text-sm font-semibold">{t(`security.zone_${zone}`)}</span>
        <Badge variant="secondary" className="text-xs font-mono">{paths.length}</Badge>
        {expanded ? <IconChevronDown size={16} className="text-muted-foreground" /> : <IconChevronRight size={16} className="text-muted-foreground" />}
      </button>
      {expanded && (
        <CardContent className="pt-0 pb-4 space-y-2">
          {paths.map((p, i) => (
            <div key={i} className="flex items-center gap-2 group bg-muted/30 rounded-md border border-transparent hover:border-border transition-colors px-2 py-1">
              <code className="flex-1 text-xs font-mono">{p}</code>
              <Button
                variant="ghost" size="icon"
                className="size-7 opacity-0 group-hover:opacity-100 text-destructive hover:text-destructive hover:bg-destructive/10"
                onClick={() => onChange(paths.filter((_, j) => j !== i))}
              >
                <IconX size={14} />
              </Button>
            </div>
          ))}
          <div className="flex gap-2 mt-3">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
              placeholder="D:/path/to/dir/**"
              className="h-9 text-sm font-mono"
            />
            <Button variant="secondary" size="sm" onClick={add} className="h-9 px-4">
              <IconPlus size={14} className="mr-1.5" />
              {t("common.add", "添加")}
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

function TagEditor({ label, items, onChange, placeholder }: {
  label: string; items: string[]; onChange: (v: string[]) => void; placeholder?: string;
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");

  const add = () => {
    const v = input.trim();
    if (v && !items.includes(v)) onChange([...items, v]);
    setInput("");
  };

  return (
    <div className="space-y-3">
      <Label className="text-sm font-medium">{label}</Label>
      {items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {items.map((item, i) => (
            <Badge key={i} variant="secondary" className="gap-1.5 pl-2.5 pr-1 py-1 font-mono text-xs border-transparent hover:border-border transition-colors">
              {item}
              <button
                onClick={() => onChange(items.filter((_, j) => j !== i))}
                className="ml-0.5 rounded-sm hover:bg-destructive/20 transition-colors p-0.5"
              >
                <IconX size={12} className="text-muted-foreground hover:text-destructive" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder={placeholder}
          className="h-9 text-sm font-mono"
        />
        <Button variant="secondary" size="sm" onClick={add} className="h-9 px-4">
          <IconPlus size={14} className="mr-1.5" />
          {t("common.add", "添加")}
        </Button>
      </div>
    </div>
  );
}
