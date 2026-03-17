import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";

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

const ZONE_LABELS: Record<string, { zh: string; en: string; color: string }> = {
  workspace: { zh: "自由区", en: "Workspace", color: "#22c55e" },
  controlled: { zh: "可控区", en: "Controlled", color: "#3b82f6" },
  protected: { zh: "保护区", en: "Protected", color: "#f59e0b" },
  forbidden: { zh: "禁区", en: "Forbidden", color: "#ef4444" },
};

const BACKEND_OPTIONS = [
  { value: "auto", label: "Auto" },
  { value: "low_integrity", label: "Low Integrity (Windows)" },
  { value: "bubblewrap", label: "Bubblewrap (Linux)" },
  { value: "seatbelt", label: "Seatbelt (macOS)" },
  { value: "docker", label: "Docker" },
  { value: "none", label: "None" },
];

export default function SecurityView({ apiBaseUrl, serviceRunning }: SecurityViewProps) {
  const { t, i18n } = useTranslation();
  const isZh = i18n.language?.startsWith("zh");

  const [tab, setTab] = useState<"zones" | "commands" | "sandbox" | "audit" | "checkpoints">("zones");
  const [zones, setZones] = useState<ZoneConfig>({ workspace: [], controlled: [], protected: [], forbidden: [] });
  const [commands, setCommands] = useState<CommandConfig>({ custom_critical: [], custom_high: [], excluded_patterns: [], blocked_commands: [] });
  const [sandbox, setSandbox] = useState<SandboxConfig>({ enabled: true, backend: "auto", sandbox_risk_levels: ["HIGH"], exempt_commands: [] });
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [checkpoints, setCheckpoints] = useState<CheckpointEntry[]>([]);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

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

  const saveZones = async () => {
    setSaving(true);
    try {
      await api("/api/config/security/zones", "POST", zones);
      setMsg(isZh ? "区域配置已保存" : "Zone config saved");
    } catch { setMsg(isZh ? "保存失败" : "Save failed"); }
    setSaving(false);
    setTimeout(() => setMsg(""), 3000);
  };

  const saveCommands = async () => {
    setSaving(true);
    try {
      await api("/api/config/security/commands", "POST", commands);
      setMsg(isZh ? "命令配置已保存" : "Command config saved");
    } catch { setMsg(isZh ? "保存失败" : "Save failed"); }
    setSaving(false);
    setTimeout(() => setMsg(""), 3000);
  };

  const saveSandbox = async () => {
    setSaving(true);
    try {
      await api("/api/config/security/sandbox", "POST", sandbox);
      setMsg(isZh ? "沙箱配置已保存" : "Sandbox config saved");
    } catch { setMsg(isZh ? "保存失败" : "Save failed"); }
    setSaving(false);
    setTimeout(() => setMsg(""), 3000);
  };

  const rewindCheckpoint = async (id: string) => {
    if (!confirm(isZh ? `确认回滚到 ${id}？` : `Rewind to ${id}?`)) return;
    try {
      await api("/api/config/security/checkpoint/rewind", "POST", { checkpoint_id: id });
      setMsg(isZh ? "已回滚" : "Rewound");
      setTimeout(() => setMsg(""), 3000);
    } catch { /* ignore */ }
  };

  if (!serviceRunning) {
    return (
      <div className="card" style={{ textAlign: "center", padding: 40 }}>
        <p style={{ color: "var(--muted)" }}>{isZh ? "后端未运行" : "Backend not running"}</p>
      </div>
    );
  }

  const tabStyle = (id: string): React.CSSProperties => ({
    padding: "8px 16px",
    cursor: "pointer",
    borderBottom: tab === id ? "2px solid var(--accent, #3b82f6)" : "2px solid transparent",
    fontWeight: tab === id ? 600 : 400,
    color: tab === id ? "var(--accent, #3b82f6)" : "var(--fg)",
    fontSize: 13,
  });

  return (
    <div style={{ padding: 0, maxWidth: 900, margin: "0 auto" }}>
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 4 }}>
        {t("security.title")}
      </h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 16 }}>
        {t("security.desc")}
      </p>

      {msg && (
        <div style={{ padding: "8px 14px", background: "var(--ok-bg, #ecfdf5)", borderRadius: 6, marginBottom: 12, fontSize: 13, color: "var(--ok, #059669)" }}>
          {msg}
        </div>
      )}

      <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--line)", marginBottom: 16 }}>
        <div style={tabStyle("zones")} onClick={() => setTab("zones")}>
          {t("security.zones")}
        </div>
        <div style={tabStyle("commands")} onClick={() => setTab("commands")}>
          {t("security.commands")}
        </div>
        <div style={tabStyle("sandbox")} onClick={() => setTab("sandbox")}>
          {t("security.sandbox")}
        </div>
        <div style={tabStyle("audit")} onClick={() => setTab("audit")}>
          {t("security.audit")}
        </div>
        <div style={tabStyle("checkpoints")} onClick={() => setTab("checkpoints")}>
          {t("security.checkpoints")}
        </div>
      </div>

      {tab === "zones" && (
        <div className="card">
          <p style={{ fontSize: 13, color: "var(--muted)", marginBottom: 16 }}>
            {t("security.zonesDesc")}
          </p>
          {(["workspace", "controlled", "protected", "forbidden"] as const).map((zone) => (
            <ZonePanel
              key={zone}
              zone={zone}
              label={isZh ? ZONE_LABELS[zone].zh : ZONE_LABELS[zone].en}
              color={ZONE_LABELS[zone].color}
              paths={zones[zone] || []}
              onChange={(paths) => setZones((prev) => ({ ...prev, [zone]: paths }))}
            />
          ))}
          <button className="btn btnPrimary" onClick={saveZones} disabled={saving} style={{ marginTop: 12 }}>
            {saving ? "..." : t("security.save")}
          </button>
        </div>
      )}

      {tab === "commands" && (
        <div className="card">
          <p style={{ fontSize: 13, color: "var(--muted)", marginBottom: 16 }}>
            {t("security.commandsDesc")}
          </p>
          <ListEditor
            label={isZh ? "CRITICAL 模式（自定义）" : "Custom CRITICAL patterns"}
            items={commands.custom_critical}
            onChange={(v) => setCommands((p) => ({ ...p, custom_critical: v }))}
            placeholder="e.g. rm\s+-rf\s+/"
          />
          <ListEditor
            label={isZh ? "HIGH 模式（自定义）" : "Custom HIGH patterns"}
            items={commands.custom_high}
            onChange={(v) => setCommands((p) => ({ ...p, custom_high: v }))}
            placeholder="e.g. Remove-Item.*-Recurse"
          />
          <ListEditor
            label={isZh ? "排除的模式" : "Excluded patterns"}
            items={commands.excluded_patterns}
            onChange={(v) => setCommands((p) => ({ ...p, excluded_patterns: v }))}
            placeholder={isZh ? "排除误报的模式" : "Exclude false positive patterns"}
          />
          <ListEditor
            label={isZh ? "命令黑名单" : "Blocked commands"}
            items={commands.blocked_commands}
            onChange={(v) => setCommands((p) => ({ ...p, blocked_commands: v }))}
            placeholder="e.g. diskpart"
          />
          <button className="btn btnPrimary" onClick={saveCommands} disabled={saving} style={{ marginTop: 12 }}>
            {saving ? "..." : t("security.save")}
          </button>
        </div>
      )}

      {tab === "sandbox" && (
        <div className="card">
          <p style={{ fontSize: 13, color: "var(--muted)", marginBottom: 16 }}>
            {t("security.sandboxDesc")}
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <span style={{ fontSize: 13 }}>{t("security.sandboxEnabled")}</span>
            <ToggleSwitch checked={sandbox.enabled} onChange={(v) => setSandbox((p) => ({ ...p, enabled: v }))} />
          </div>
          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 13, display: "block", marginBottom: 4 }}>{t("security.sandboxBackend")}</label>
            <select
              value={sandbox.backend}
              onChange={(e) => setSandbox((p) => ({ ...p, backend: e.target.value }))}
              style={{ padding: "6px 10px", borderRadius: 6, border: "1px solid var(--line)", fontSize: 13, background: "var(--bg)" }}
            >
              {BACKEND_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <button className="btn btnPrimary" onClick={saveSandbox} disabled={saving} style={{ marginTop: 12 }}>
            {saving ? "..." : t("security.save")}
          </button>
        </div>
      )}

      {tab === "audit" && (
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: "var(--muted)" }}>
              {isZh ? `最近 ${audit.length} 条记录` : `Last ${audit.length} entries`}
            </span>
            <button className="btn" onClick={loadAudit} style={{ fontSize: 12 }}>
              {isZh ? "刷新" : "Refresh"}
            </button>
          </div>
          <div style={{ maxHeight: 400, overflow: "auto" }}>
            {audit.length === 0 && (
              <p style={{ color: "var(--muted)", textAlign: "center", padding: 20 }}>
                {isZh ? "暂无审计记录" : "No audit entries"}
              </p>
            )}
            {[...audit].reverse().map((e, i) => (
              <div key={i} style={{
                padding: "8px 12px", borderBottom: "1px solid var(--line)", fontSize: 12,
                display: "flex", gap: 8, alignItems: "flex-start",
              }}>
                <span style={{
                  padding: "2px 6px", borderRadius: 4, fontSize: 11, fontWeight: 600,
                  background: e.decision === "deny" ? "#fee2e2" : e.decision === "confirm" ? "#fef3c7" : "#ecfdf5",
                  color: e.decision === "deny" ? "#dc2626" : e.decision === "confirm" ? "#d97706" : "#059669",
                }}>
                  {e.decision.toUpperCase()}
                </span>
                <div style={{ flex: 1 }}>
                  <span style={{ fontWeight: 500 }}>{e.tool}</span>
                  <span style={{ color: "var(--muted)", marginLeft: 8 }}>{e.reason}</span>
                </div>
                <span style={{ color: "var(--muted)", whiteSpace: "nowrap" }}>
                  {new Date(e.ts * 1000).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "checkpoints" && (
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: "var(--muted)" }}>
              {isZh ? `${checkpoints.length} 个快照` : `${checkpoints.length} checkpoints`}
            </span>
            <button className="btn" onClick={loadCheckpoints} style={{ fontSize: 12 }}>
              {isZh ? "刷新" : "Refresh"}
            </button>
          </div>
          {checkpoints.length === 0 && (
            <p style={{ color: "var(--muted)", textAlign: "center", padding: 20 }}>
              {isZh ? "暂无快照" : "No checkpoints"}
            </p>
          )}
          {checkpoints.map((cp) => (
            <div key={cp.checkpoint_id} style={{
              padding: "10px 14px", borderBottom: "1px solid var(--line)", fontSize: 13,
              display: "flex", alignItems: "center", gap: 10,
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500 }}>{cp.checkpoint_id}</div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>
                  {cp.tool_name} — {cp.file_count} {isZh ? "个文件" : "file(s)"}
                  <span style={{ marginLeft: 8 }}>{new Date(cp.timestamp * 1000).toLocaleString()}</span>
                </div>
              </div>
              <button
                className="btn"
                style={{ fontSize: 11 }}
                onClick={() => rewindCheckpoint(cp.checkpoint_id)}
              >
                {isZh ? "回滚" : "Rewind"}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function ZonePanel({ zone, label, color, paths, onChange }: {
  zone: string; label: string; color: string;
  paths: string[]; onChange: (v: string[]) => void;
}) {
  const [input, setInput] = useState("");
  const [expanded, setExpanded] = useState(zone === "workspace" || zone === "controlled");

  const add = () => {
    const v = input.trim();
    if (v && !paths.includes(v)) {
      onChange([...paths, v]);
    }
    setInput("");
  };

  return (
    <div style={{ marginBottom: 12, border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: "10px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: 8,
          background: "var(--bg-hover, #f8fafc)",
        }}
      >
        <div style={{ width: 10, height: 10, borderRadius: "50%", background: color }} />
        <span style={{ fontWeight: 600, fontSize: 13, flex: 1 }}>{label}</span>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>{paths.length}</span>
        <span style={{ fontSize: 11 }}>{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div style={{ padding: "8px 14px" }}>
          {paths.map((p, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <code style={{ flex: 1, fontSize: 12, padding: "2px 6px", background: "var(--bg-code, #f1f5f9)", borderRadius: 4 }}>
                {p}
              </code>
              <button
                onClick={() => onChange(paths.filter((_, j) => j !== i))}
                style={{ background: "none", border: "none", cursor: "pointer", color: "#ef4444", fontSize: 14, padding: "0 4px" }}
              >
                x
              </button>
            </div>
          ))}
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
              placeholder="D:/path/to/dir/**"
              style={{ flex: 1, padding: "4px 8px", fontSize: 12, border: "1px solid var(--line)", borderRadius: 4 }}
            />
            <button className="btn" onClick={add} style={{ fontSize: 11, padding: "4px 10px" }}>+</button>
          </div>
        </div>
      )}
    </div>
  );
}


function ListEditor({ label, items, onChange, placeholder }: {
  label: string; items: string[]; onChange: (v: string[]) => void; placeholder?: string;
}) {
  const [input, setInput] = useState("");

  const add = () => {
    const v = input.trim();
    if (v && !items.includes(v)) {
      onChange([...items, v]);
    }
    setInput("");
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <label style={{ fontSize: 13, fontWeight: 500, display: "block", marginBottom: 6 }}>{label}</label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
        {items.map((item, i) => (
          <span key={i} style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            padding: "2px 8px", background: "var(--bg-code, #f1f5f9)", borderRadius: 4, fontSize: 12,
          }}>
            <code>{item}</code>
            <button
              onClick={() => onChange(items.filter((_, j) => j !== i))}
              style={{ background: "none", border: "none", cursor: "pointer", color: "#ef4444", fontSize: 12, padding: 0 }}
            >
              x
            </button>
          </span>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder={placeholder}
          style={{ flex: 1, padding: "4px 8px", fontSize: 12, border: "1px solid var(--line)", borderRadius: 4 }}
        />
        <button className="btn" onClick={add} style={{ fontSize: 11, padding: "4px 10px" }}>+</button>
      </div>
    </div>
  );
}


function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div
      onClick={() => onChange(!checked)}
      style={{
        width: 40, height: 22, borderRadius: 11, cursor: "pointer",
        background: checked ? "var(--ok, #22c55e)" : "var(--line)",
        position: "relative", transition: "background 0.2s",
      }}
    >
      <div style={{
        width: 18, height: 18, borderRadius: 9, background: "#fff",
        position: "absolute", top: 2,
        left: checked ? 20 : 2,
        transition: "left 0.2s", boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
      }} />
    </div>
  );
}
