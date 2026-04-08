/**
 * Organization Operations Dashboard — 酷炫数据大屏风格
 * 渐变边框 + 发光效果 + 动画数字 + 粒子网格背景
 */
import { useEffect, useState, useCallback, useRef } from "react";
import { safeFetch } from "../providers";
import { OrgAvatar } from "./OrgAvatars";
import { useMdModules } from "../views/chat/hooks/useMdModules";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "./ui/tooltip";

/* ─── Inline SVG mini-icons (replace emoji) ─── */
const SvgNodes = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3"/><path d="M12 3v6m0 6v6m9-9h-6m-6 0H3"/>
  </svg>
);
const SvgZap = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
  </svg>
);
const SvgCheck = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
  </svg>
);
const SvgMsg = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
  </svg>
);
const SvgInbox = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>
  </svg>
);
const SvgShield = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
  </svg>
);
const SvgList = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>
    <line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>
  </svg>
);
const SvgAlert = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
  </svg>
);
const SvgClipboard = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>
  </svg>
);
const SvgGrid = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
  </svg>
);

interface OrgDashboardProps {
  orgId: string;
  apiBaseUrl: string;
  orgName?: string;
  onNodeClick?: (nodeId: string) => void;
}

const STATUS_COLORS: Record<string, string> = {
  idle: "#22c55e",
  busy: "#3b82f6",
  error: "#ef4444",
  frozen: "#94a3b8",
  waiting: "#f59e0b",
};

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲", busy: "忙碌", error: "异常", frozen: "冻结", waiting: "等待",
};

const HEALTH_MAP: Record<string, [string, string, string]> = {
  healthy: ["运行良好", "#22c55e", "#22c55e30"],
  attention: ["需关注", "#3b82f6", "#3b82f630"],
  warning: ["有隐患", "#f59e0b", "#f59e0b30"],
  critical: ["异常", "#ef4444", "#ef444430"],
};

const DB_TYPE_META: Record<string, { icon: string; label: string; tip: string; cls: string }> = {
  task_delegated:  { icon: "↗", label: "分配", tip: "分配任务给下级节点",     cls: "db-ev-delegated" },
  task_delivered:  { icon: "↙", label: "交付", tip: "向上级交付任务成果",     cls: "db-ev-delivered" },
  task_accepted:   { icon: "✓", label: "通过", tip: "上级验收通过",           cls: "db-ev-accepted" },
  task_rejected:   { icon: "✗", label: "打回", tip: "上级打回，需要重新处理", cls: "db-ev-rejected" },
  task_timeout:    { icon: "⏱", label: "超时", tip: "任务执行超时",           cls: "db-ev-timeout" },
  task_completed:  { icon: "✓", label: "完成", tip: "节点执行完成",           cls: "db-ev-completed" },
  node_activated:  { icon: "▶", label: "执行", tip: "节点开始执行任务",       cls: "db-ev-activated" },
  _default:        { icon: "•", label: "事件", tip: "",                       cls: "" },
};

function stripMd(s: string): string {
  return s
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/_(.+?)_/g, "$1")
    .replace(/~~(.+?)~~/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\n+/g, " ")
    .trim();
}

function fmtDuration(s: number | null | undefined): string {
  if (!s || s <= 0) return "--";
  if (s >= 86400) return `${Math.floor(s / 86400)}天 ${Math.floor((s % 86400) / 3600)}时`;
  if (s >= 3600) return `${Math.floor(s / 3600)}时 ${Math.floor((s % 3600) / 60)}分`;
  return `${Math.floor(s / 60)}分`;
}

function fmtTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtIdle(s: number | null | undefined): string {
  if (s == null) return "--";
  if (s < 60) return "刚刚活动";
  if (s < 3600) return `${Math.floor(s / 60)}分钟前`;
  return `${Math.floor(s / 3600)}小时前`;
}

function AnimatedNumber({ value, color }: { value: number; color: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const prevRef = useRef(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const from = prevRef.current;
    const to = value;
    prevRef.current = to;
    if (from === to) { el.textContent = String(to); return; }
    const duration = 800;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = String(Math.round(from + (to - from) * eased));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [value]);

  return <span ref={ref} style={{ color, fontVariantNumeric: "tabular-nums" }}>{value}</span>;
}

export function OrgDashboard({ orgId, apiBaseUrl, orgName, onNodeClick }: OrgDashboardProps) {
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const mdModules = useMdModules();

  const fetchStats = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/stats`);
      if (res.ok) setStats(await res.json());
    } catch { /* ignore */ }
    setLoading(false);
    setTick(t => t + 1);
  }, [orgId, apiBaseUrl]);

  useEffect(() => {
    fetchStats();
    const iv = setInterval(fetchStats, 8000);
    return () => clearInterval(iv);
  }, [fetchStats]);

  if (loading && !stats) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", background: "var(--bg-app)", color: "var(--muted)" }}>
        <div style={{ textAlign: "center" }}>
          <div className="db-spinner" />
          <div style={{ marginTop: 12, fontSize: 13 }}>正在连接组织...</div>
        </div>
      </div>
    );
  }
  if (!stats) return <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", background: "var(--bg-app)", color: "var(--muted)" }}>无法加载</div>;

  const hl = HEALTH_MAP[stats.health] || HEALTH_MAP.healthy;
  const nodeStats = stats.node_stats || {};
  const busyCount = nodeStats.busy || 0;
  const perNode: any[] = stats.per_node || [];
  const anomalies: any[] = stats.anomalies || [];
  const recentBB: any[] = stats.recent_blackboard || [];
  const recentTasks: any[] = stats.recent_tasks || [];
  const deptWorkload: Record<string, { total: number; busy: number }> = stats.department_workload || {};
  const nodeName = (id: string) => {
    if (!id) return "";
    const n = perNode.find((n: any) => n.id === id);
    return n?.role_title || id;
  };
  const healthPct = stats.node_count > 0 ? Math.round(((stats.node_count - (nodeStats.error || 0)) / stats.node_count) * 100) : 100;
  const now = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  const FeedTip = ({ text }: { text: string }) => (
    <div className="db-tip-content">
      {mdModules ? (
        <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>
          {text}
        </mdModules.ReactMarkdown>
      ) : (
        <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{text}</pre>
      )}
    </div>
  );
  const tipCls = "db-tip-wrap bg-popover text-popover-foreground border border-border shadow-lg";

  return (
    <TooltipProvider delayDuration={300}>
    <div className="db-root">
      <div className="db-grid-bg" />

      {/* ── Header ────────────────────────────────────── */}
      <div className="db-header">
        <div className="db-header-left">
          <div className="db-title-glow">{orgName || stats.name || "组织"}</div>
          <div className="db-subtitle">实时运营监控中心</div>
        </div>
        <div className="db-header-center">
          <div className="db-health-ring" data-pct={healthPct} style={{ "--ring-color": hl[1] } as any}>
            <svg viewBox="0 0 80 80" width={64} height={64}>
              <circle cx="40" cy="40" r="34" fill="none" stroke="var(--line)" strokeWidth="5" />
              <circle cx="40" cy="40" r="34" fill="none" stroke={hl[1]} strokeWidth="5"
                strokeDasharray={`${healthPct * 2.14} 214`} strokeLinecap="round"
                transform="rotate(-90 40 40)" className="db-ring-anim" />
            </svg>
            <span className="db-health-pct">{healthPct}%</span>
          </div>
          <div>
            <div className="db-health-label" style={{ color: hl[1] }}>
              <span className="db-pulse-dot" style={{ background: hl[1] }} />
              {hl[0]}
            </div>
            <div className="db-uptime">运行 {fmtDuration(stats.uptime_s)}</div>
          </div>
        </div>
        <div className="db-header-right">
          <div className="db-clock">{now}</div>
        </div>
      </div>

      {/* ── KPI Row ───────────────────────────────────── */}
      <div className="db-kpi-row">
        {[
          { label: "节点总数", value: stats.node_count, icon: <SvgNodes />, gradient: "linear-gradient(135deg, #3b82f6, #6366f1)" },
          { label: "活跃节点", value: busyCount, sub: `/ ${stats.node_count}`, icon: <SvgZap />, gradient: "linear-gradient(135deg, #22c55e, #10b981)" },
          { label: "已完成任务", value: stats.total_tasks_completed ?? 0, icon: <SvgCheck />, gradient: "linear-gradient(135deg, #8b5cf6, #a855f7)" },
          { label: "消息总量", value: stats.total_messages_exchanged ?? 0, icon: <SvgMsg />, gradient: "linear-gradient(135deg, #f59e0b, #f97316)" },
          { label: "待处理", value: stats.pending_messages ?? 0, icon: <SvgInbox />, gradient: stats.pending_messages > 0 ? "linear-gradient(135deg, #ef4444, #f97316)" : "linear-gradient(135deg, #475569, #64748b)" },
          { label: "待审批", value: stats.pending_approvals ?? 0, icon: <SvgShield />, gradient: stats.pending_approvals > 0 ? "linear-gradient(135deg, #f97316, #eab308)" : "linear-gradient(135deg, #475569, #64748b)" },
        ].map(kpi => (
          <div key={kpi.label} className="db-kpi-card">
            <div className="db-kpi-icon" style={{ background: kpi.gradient }}>{kpi.icon}</div>
            <div className="db-kpi-value">
              <AnimatedNumber value={typeof kpi.value === "number" ? kpi.value : 0} color="var(--text)" />
              {kpi.sub && <span className="db-kpi-sub">{kpi.sub}</span>}
            </div>
            <div className="db-kpi-label">{kpi.label}</div>
            <div className="db-kpi-glow" style={{ background: kpi.gradient }} />
          </div>
        ))}
      </div>

      {/* ── Middle Row ────────────────────────────────── */}
      <div className="db-middle-row">
        <GlassCard title="节点状态分布" className="db-col-1">
          {(["idle", "busy", "error", "frozen", "waiting"] as const).map(st => {
            const count = nodeStats[st] || 0;
            const pct = stats.node_count > 0 ? (count / stats.node_count) * 100 : 0;
            return (
              <div key={st} className="db-bar-row">
                <span className="db-bar-dot" style={{ background: STATUS_COLORS[st] }} />
                <span className="db-bar-label">{STATUS_LABELS[st]}</span>
                <div className="db-bar-track">
                  <div className="db-bar-fill" style={{ width: `${pct}%`, background: STATUS_COLORS[st], boxShadow: `0 0 8px ${STATUS_COLORS[st]}60` }} />
                </div>
                <span className="db-bar-count">{count}</span>
              </div>
            );
          })}
        </GlassCard>

        <GlassCard title="部门工作量" className="db-col-1">
          {Object.entries(deptWorkload).length === 0 ? (
            <div style={{ color: "var(--muted)", fontSize: 12 }}>暂无数据</div>
          ) : (
            Object.entries(deptWorkload).sort((a, b) => b[1].total - a[1].total).map(([dept, wl]) => {
              const pct = stats.node_count > 0 ? Math.round((wl.total / stats.node_count) * 100) : 0;
              const busyPct = wl.total > 0 ? Math.round((wl.busy / wl.total) * 100) : 0;
              return (
                <div key={dept} className="db-bar-row">
                  <span className="db-bar-label" style={{ width: 56 }}>{dept}</span>
                  <div className="db-bar-track">
                    <div className="db-bar-fill db-bar-fill-dept" style={{ width: `${pct}%` }}>
                      {busyPct > 0 && <div className="db-bar-busy" style={{ width: `${busyPct}%` }} />}
                    </div>
                  </div>
                  <span className="db-bar-count">{pct}%</span>
                  {wl.busy > 0 && <span className="db-busy-badge">{wl.busy} 忙</span>}
                </div>
              );
            })
          )}
        </GlassCard>
      </div>

      {/* ── Data Row: Tasks + Alerts + Blackboard ──── */}
      <div className="db-data-row">
        <GlassCard title="实时任务流" icon={<SvgList />} className="db-col-2">
          <div className="db-scroll-area">
            {recentTasks.length === 0 ? (
              <div className="db-empty">暂无任务</div>
            ) : recentTasks.map((t, i) => {
              const meta = DB_TYPE_META[t.type] || DB_TYPE_META._default;
              const fromName = nodeName(t.from);
              const toName = nodeName(t.to);
              return (
                <div key={i} className="db-task-item">
                  <span className="db-task-time">{fmtTime(t.t)}</span>
                  <span className={`db-task-badge ${meta.cls}`} title={meta.tip}>
                    <span className="db-task-badge-icon">{meta.icon}</span>
                    {meta.label}
                  </span>
                  <span className="db-task-who">{fromName}</span>
                  {toName && <>
                    <span className="db-task-arrow">→</span>
                    <span className="db-task-who">{toName}</span>
                  </>}
                  {t.task && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="db-task-desc">{stripMd(t.task)}</span>
                      </TooltipTrigger>
                      <TooltipContent side="top" align="start" className={tipCls}>
                        <FeedTip text={t.task} />
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
              );
            })}
          </div>
        </GlassCard>

        <GlassCard title={`异常告警${anomalies.length > 0 ? ` (${anomalies.length})` : ""}`} icon={<SvgAlert />} className="db-col-1" accent={anomalies.length > 0 ? "#ef4444" : undefined}>
          <div className="db-scroll-area">
            {anomalies.length === 0 ? (
              <div className="db-all-good">✓ 一切正常</div>
            ) : anomalies.map((a, i) => (
              <div key={i} className="db-alert-item">
                <span className="db-alert-icon" style={{ color: a.type === "error" ? "#ef4444" : a.type === "stuck" ? "#f59e0b" : "#3b82f6" }}>
                  {a.type === "error" ? "✕" : "▲"}
                </span>
                <span className="db-alert-who"><b>{a.role_title || a.node_id}</b></span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="db-alert-desc">{stripMd(String(a.message))}</span>
                  </TooltipTrigger>
                  <TooltipContent side="top" align="start" className={tipCls}>
                    <FeedTip text={String(a.message)} />
                  </TooltipContent>
                </Tooltip>
              </div>
            ))}
          </div>
        </GlassCard>

        <GlassCard title="黑板记录" icon={<SvgClipboard />} className="db-col-1">
          <div className="db-scroll-area">
            {recentBB.length === 0 ? (
              <div className="db-empty">暂无记录</div>
            ) : recentBB.map((b, i) => (
              <div key={i} className="db-bb-item">
                <span className={`db-bb-badge db-bb-${b.memory_type}`}>
                  {b.memory_type === "decision" ? "决策" : b.memory_type === "progress" ? "进度" : b.memory_type === "fact" ? "事实" : b.memory_type}
                </span>
                <span className="db-bb-source">{nodeName(b.source_node)}</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="db-bb-content">{stripMd(String(b.content))}</span>
                  </TooltipTrigger>
                  <TooltipContent side="top" align="start" className={tipCls}>
                    <FeedTip text={String(b.content)} />
                  </TooltipContent>
                </Tooltip>
              </div>
            ))}
          </div>
        </GlassCard>
      </div>

      {/* ── Node Grid ─────────────────────────────────── */}
      <GlassCard title="节点矩阵" icon={<SvgGrid />}>
        <div className="db-node-grid">
          {perNode.filter((n: any) => !n.is_clone).map((n: any) => {
            const color = STATUS_COLORS[n.status] || "#64748b";
            const isBusy = n.status === "busy";
            return (
              <div key={n.id} className={`db-node-card ${isBusy ? "db-node-busy" : ""}`}
                onClick={() => onNodeClick?.(n.id)}
                style={{ "--node-color": color } as any}>
                <div className="db-node-top">
                  <OrgAvatar avatarId={n.avatar} size={28} statusColor={color} />
                  <div className="db-node-info">
                    <div className="db-node-name">{n.role_title || n.id}</div>
                    <div className="db-node-dept">{n.department || ""}</div>
                  </div>
                  <div className="db-node-status-dot" style={{ background: color }} />
                </div>
                <div className="db-node-bottom">
                  <span className="db-node-status-text" style={{ color }}>
                    {STATUS_LABELS[n.status] || n.status}
                  </span>
                  {n.current_task ? (
                    <span className="db-node-task">{n.current_task}</span>
                  ) : (
                    <span className="db-node-idle">{fmtIdle(n.idle_seconds)}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </GlassCard>

      <style>{DASHBOARD_CSS}</style>
    </div>
    </TooltipProvider>
  );
}

function GlassCard({ title, icon, children, className, accent }: {
  title: string; icon?: React.ReactNode; children: React.ReactNode; className?: string; accent?: string;
}) {
  return (
    <div className={`db-glass ${className || ""}`} style={accent ? { "--card-accent": accent } as any : undefined}>
      <div className="db-glass-title">
        {icon && <span className="db-glass-icon">{icon}</span>}
        {title}
      </div>
      {children}
    </div>
  );
}

const DASHBOARD_CSS = `
/* ─── Root ──────────────────────────────────── */
.db-root {
  height: 100%; overflow: auto; position: relative;
  background: var(--bg-app, #040a18);
  color: var(--text, #e2e8f0);
  font-family: "Noto Sans SC", system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  padding: 16px 20px 24px;
}
.db-grid-bg {
  position: absolute; inset: 0; pointer-events: none; z-index: 0;
  background-image:
    linear-gradient(var(--primary, #3b82f6)06 1px, transparent 1px),
    linear-gradient(90deg, var(--primary, #3b82f6)06 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(ellipse 60% 50% at 50% 0%, black, transparent);
}
:root[data-theme="light"] .db-grid-bg { display: none; }
.db-root > *:not(.db-grid-bg) { position: relative; z-index: 1; }

/* ─── Spinner ───────────────────────────────── */
.db-spinner {
  width: 32px; height: 32px; margin: 0 auto;
  border: 3px solid var(--line, #1e293b); border-top-color: var(--primary, #3b82f6);
  border-radius: 50%; animation: db-spin 0.8s linear infinite;
}
@keyframes db-spin { to { transform: rotate(360deg); } }

/* ─── Header ────────────────────────────────── */
.db-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px; flex-wrap: wrap; gap: 12px;
}
.db-header-left {}
.db-title-glow {
  font-size: 22px; font-weight: 800; letter-spacing: 1px;
  background: linear-gradient(135deg, #60a5fa, #a78bfa, #f472b6);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
:root[data-theme="dark"] .db-title-glow { filter: drop-shadow(0 0 10px rgba(99,102,241,0.4)); }
:root[data-theme="light"] .db-title-glow {
  background: linear-gradient(135deg, #2563eb, #7c3aed, #db2777);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.db-subtitle {
  font-size: 11px; color: var(--muted, #64748b); margin-top: 2px;
  letter-spacing: 3px; text-transform: uppercase;
}
.db-header-center { display: flex; align-items: center; gap: 12px; }
.db-health-ring { position: relative; display: flex; align-items: center; justify-content: center; }
.db-health-pct {
  position: absolute; font-size: 14px; font-weight: 700; color: var(--text, #f1f5f9);
}
.db-ring-anim { transition: stroke-dasharray 1s ease; }
.db-health-label {
  font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px;
}
.db-pulse-dot {
  width: 8px; height: 8px; border-radius: 50%;
  animation: db-pulse 2s ease-in-out infinite;
}
@keyframes db-pulse { 0%,100% { opacity: 1; box-shadow: 0 0 4px currentColor; } 50% { opacity: 0.4; box-shadow: 0 0 12px currentColor; } }
.db-uptime { font-size: 11px; color: var(--muted, #64748b); margin-top: 2px; }
.db-header-right { text-align: right; }
.db-clock {
  font-size: 20px; font-weight: 300; color: var(--muted, #94a3b8);
  font-variant-numeric: tabular-nums; font-family: "SF Mono", "Cascadia Code", "Consolas", ui-monospace, monospace;
}

/* ─── KPI Cards ─────────────────────────────── */
.db-kpi-row {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 16px;
}
@media (max-width: 960px) { .db-kpi-row { grid-template-columns: repeat(3, 1fr); } }
.db-kpi-card {
  position: relative; overflow: hidden;
  background: var(--card-bg, rgba(15,23,42,0.7));
  border: 1px solid var(--line, rgba(51,65,85,0.5));
  border-radius: 12px; padding: 14px 16px;
  transition: transform 0.2s, border-color 0.3s, box-shadow 0.3s;
}
:root[data-theme="dark"] .db-kpi-card { backdrop-filter: blur(8px); }
:root[data-theme="light"] .db-kpi-card { box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.db-kpi-card:hover { transform: translateY(-2px); border-color: var(--primary, rgba(99,102,241,0.4)); }
:root[data-theme="light"] .db-kpi-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
.db-kpi-glow {
  position: absolute; top: -20px; right: -20px; width: 60px; height: 60px;
  border-radius: 50%; opacity: 0.12; filter: blur(20px); pointer-events: none;
}
:root[data-theme="light"] .db-kpi-glow { opacity: 0.06; }
.db-kpi-icon {
  width: 28px; height: 28px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; color: #fff; margin-bottom: 8px;
}
.db-kpi-value { font-size: 26px; font-weight: 700; line-height: 1.1; }
.db-kpi-sub { font-size: 14px; color: var(--muted, #64748b); font-weight: 400; margin-left: 2px; }
.db-kpi-label { font-size: 11px; color: var(--muted, #64748b); margin-top: 4px; }

/* ─── Glass Card ────────────────────────────── */
.db-glass {
  background: var(--card-bg, rgba(15,23,42,0.6));
  border: 1px solid var(--line, rgba(51,65,85,0.5));
  border-radius: 12px; padding: 14px 16px;
  position: relative; overflow: hidden;
}
:root[data-theme="dark"] .db-glass { backdrop-filter: blur(8px); }
:root[data-theme="light"] .db-glass { box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.db-glass::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--card-accent, rgba(99,102,241,0.3)), transparent);
}
:root[data-theme="light"] .db-glass::before { opacity: 0.5; }
.db-glass-title {
  font-size: 12px; font-weight: 600; color: var(--muted, #94a3b8);
  display: flex; align-items: center; gap: 6px; margin-bottom: 10px;
  text-transform: uppercase; letter-spacing: 1px;
}
.db-glass-icon { font-size: 11px; }

/* ─── Middle Row ────────────────────────────── */
.db-middle-row {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;
}
.db-bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.db-bar-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.db-bar-label { width: 36px; font-size: 11px; color: var(--muted, #94a3b8); flex-shrink: 0; }
.db-bar-track {
  flex: 1; height: 6px; border-radius: 3px;
  background: var(--bg-subtle, rgba(51,65,85,0.5)); overflow: hidden; position: relative;
}
.db-bar-fill {
  height: 100%; border-radius: 3px; transition: width 0.8s ease;
  position: relative;
}
.db-bar-fill-dept { background: linear-gradient(90deg, #6366f1, #8b5cf6); }
.db-bar-busy {
  position: absolute; top: 0; left: 0; height: 100%;
  background: #3b82f6; border-radius: 3px; animation: db-pulse 2s infinite;
}
.db-bar-count { width: 24px; font-size: 11px; color: var(--muted, #64748b); text-align: right; flex-shrink: 0; }
.db-busy-badge {
  font-size: 9px; padding: 1px 5px; border-radius: 4px;
  background: rgba(59,130,246,0.15); color: #60a5fa; flex-shrink: 0;
}

/* ─── Data Row ──────────────────────────────── */
.db-data-row {
  display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 10px; margin-bottom: 12px;
}
@media (max-width: 960px) { .db-data-row { grid-template-columns: 1fr; } }
.db-scroll-area { max-height: 180px; overflow-y: auto; }
.db-scroll-area::-webkit-scrollbar { width: 3px; }
.db-scroll-area::-webkit-scrollbar-thumb { background: var(--line, #334155); border-radius: 2px; }
.db-empty { color: var(--muted, #475569); font-size: 12px; padding: 8px 0; }
.db-all-good { color: #22c55e; font-size: 12px; padding: 8px 0; display: flex; align-items: center; gap: 4px; }

/* Task items — mirroring org editor feed style */
.db-task-item {
  display: flex; align-items: center; gap: 7px; padding: 4px 0;
  border-bottom: 1px solid var(--line, rgba(51,65,85,0.15)); font-size: 12px;
  white-space: nowrap; line-height: 1.5; transition: background 0.15s;
}
.db-task-item:last-child { border-bottom: none; }
.db-task-item:hover { background: var(--bg-subtle, rgba(30,41,59,0.3)); }
.db-task-time {
  font-size: 11px; color: var(--muted, #64748b);
  font-family: "SF Mono", "Cascadia Code", "Consolas", ui-monospace, monospace;
  font-variant-numeric: tabular-nums; flex-shrink: 0; min-width: 56px; opacity: 0.75;
}
.db-task-badge {
  display: inline-flex; align-items: center; gap: 3px;
  flex-shrink: 0; padding: 1px 7px 1px 5px; border-radius: 4px;
  font-size: 11px; font-weight: 500; white-space: nowrap;
  background: rgba(99,102,241,0.12); color: #818cf8;
}
.db-task-badge-icon {
  font-weight: 700; font-size: 11px; line-height: 1;
  font-family: system-ui, sans-serif;
}
.db-ev-completed, .db-ev-accepted { background: rgba(34,197,94,0.12); color: #22c55e; }
.db-ev-activated  { background: rgba(59,130,246,0.12); color: #3b82f6; }
.db-ev-rejected   { background: rgba(239,68,68,0.12); color: #ef4444; }
.db-ev-timeout    { background: rgba(245,158,11,0.12); color: #f59e0b; }
.db-ev-delegated  { background: rgba(99,102,241,0.12); color: #818cf8; }
.db-ev-delivered  { background: rgba(6,182,212,0.12); color: #06b6d4; }
.db-task-who {
  font-weight: 600; color: var(--text, #e2e8f0); flex-shrink: 0;
  max-width: 100px; overflow: hidden; text-overflow: ellipsis;
  font-size: 12px;
}
.db-task-arrow { color: var(--muted, #475569); flex-shrink: 0; }
.db-task-desc {
  color: var(--muted, #94a3b8); flex: 1; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; cursor: pointer; min-width: 0;
}

/* Alert items */
.db-alert-item {
  display: flex; align-items: center; gap: 7px; padding: 4px 0;
  border-bottom: 1px solid var(--line, rgba(51,65,85,0.15)); font-size: 12px;
  white-space: nowrap; line-height: 1.5;
}
.db-alert-item:last-child { border-bottom: none; }
.db-alert-icon { font-weight: 700; flex-shrink: 0; font-size: 10px; }
.db-alert-who { flex-shrink: 0; color: var(--text, #e2e8f0); font-size: 12px; }
.db-alert-desc {
  color: var(--muted, #94a3b8); flex: 1; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; cursor: pointer; min-width: 0;
}

/* Blackboard items */
.db-bb-item {
  display: flex; align-items: center; gap: 7px; padding: 4px 0;
  border-bottom: 1px solid var(--line, rgba(51,65,85,0.15)); font-size: 12px;
  white-space: nowrap; line-height: 1.5;
}
.db-bb-item:last-child { border-bottom: none; }
.db-bb-badge {
  display: inline-flex; align-items: center;
  font-size: 11px; padding: 1px 7px; border-radius: 4px; flex-shrink: 0; font-weight: 500;
}
.db-bb-decision { background: rgba(139,92,246,0.12); color: #a78bfa; }
.db-bb-progress { background: rgba(59,130,246,0.12); color: #60a5fa; }
.db-bb-fact { background: rgba(245,158,11,0.12); color: #fbbf24; }
.db-bb-source { color: var(--muted, #94a3b8); font-weight: 500; flex-shrink: 0; font-size: 12px; }
.db-bb-content {
  color: var(--muted, #94a3b8); flex: 1; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; cursor: pointer; min-width: 0;
}

/* ── Tooltip (markdown) ── */
.db-tip-wrap {
  max-width: 420px !important; min-width: 200px;
  padding: 10px 14px !important; text-align: left !important;
  white-space: normal !important; border-radius: 8px !important;
  z-index: 9999;
}
.db-tip-wrap [data-slot="tooltip-arrow"] { display: none; }
.db-tip-content {
  font-size: 12px; line-height: 1.7;
  max-height: 300px; overflow-y: auto;
  scrollbar-width: thin; color: inherit;
}
.db-tip-content p { margin: 0 0 6px; }
.db-tip-content p:last-child { margin-bottom: 0; }
.db-tip-content ul, .db-tip-content ol { margin: 4px 0; padding-left: 1.4em; }
.db-tip-content li { margin: 2px 0; }
.db-tip-content strong { font-weight: 600; }
.db-tip-content code {
  font-size: 11px; padding: 1px 4px; border-radius: 3px;
  background: rgba(99,102,241,0.1); font-family: "SF Mono", "Cascadia Code", "Consolas", ui-monospace, monospace;
}
.db-tip-content pre { margin: 4px 0; padding: 8px; border-radius: 6px; background: var(--bg-subtle, rgba(0,0,0,0.3)); overflow-x: auto; }
.db-tip-content pre code { padding: 0; background: none; }
.db-tip-content blockquote { margin: 4px 0; padding-left: 10px; border-left: 3px solid rgba(99,102,241,0.3); color: inherit; opacity: 0.8; }
.db-tip-content table { border-collapse: collapse; margin: 4px 0; font-size: 11px; }
.db-tip-content th, .db-tip-content td { padding: 3px 8px; border: 1px solid var(--line, rgba(100,116,139,0.3)); }

/* ─── Node Grid ─────────────────────────────── */
.db-node-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px;
}
.db-node-card {
  display: flex; flex-direction: column; gap: 6px;
  padding: 10px 12px; border-radius: 10px;
  background: var(--card-bg, rgba(15,23,42,0.5));
  border: 1px solid var(--line, rgba(51,65,85,0.4));
  cursor: pointer; transition: all 0.25s;
  position: relative; overflow: hidden;
}
:root[data-theme="light"] .db-node-card { box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.db-node-card::after {
  content: ""; position: absolute; inset: 0; border-radius: 10px;
  opacity: 0; transition: opacity 0.3s;
  background: radial-gradient(circle at 50% 50%, var(--node-color, #3b82f6)08, transparent 70%);
}
.db-node-card:hover { border-color: var(--node-color, #3b82f6); transform: translateY(-1px); }
.db-node-card:hover::after { opacity: 1; }
.db-node-busy { border-color: rgba(59,130,246,0.3); }
.db-node-busy::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, transparent, var(--node-color, #3b82f6), transparent);
  animation: db-scan 2s ease-in-out infinite;
}
@keyframes db-scan { 0% { opacity: 0.3; } 50% { opacity: 1; } 100% { opacity: 0.3; } }
.db-node-top { display: flex; align-items: center; gap: 8px; }
.db-node-info { flex: 1; min-width: 0; }
.db-node-name { font-size: 12px; font-weight: 600; color: var(--text, #f1f5f9); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.db-node-dept { font-size: 10px; color: var(--muted, #64748b); }
.db-node-status-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
:root[data-theme="dark"] .db-node-status-dot { box-shadow: 0 0 6px currentColor; }
.db-node-bottom { display: flex; justify-content: space-between; align-items: center; }
.db-node-status-text { font-size: 10px; font-weight: 600; }
.db-node-task { font-size: 10px; color: var(--muted, #64748b); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.db-node-idle { font-size: 10px; color: var(--muted2, #334155); }
`;
