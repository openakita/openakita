/**
 * Organization Monitor Panel — runtime monitoring for a selected node.
 * Manages its own data fetching (events, schedules, thinking, tasks).
 */
import { useState, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import type { Node } from "@xyflow/react";
import {
  fmtTime, fmtDateTime,
  STATUS_LABELS, STATUS_COLORS,
  TASK_STATUS_LABELS, EVENT_TYPE_LABELS, MSG_TYPE_LABELS,
  DATA_KEY_LABELS, translateDataValue,
  type OrgNodeData,
} from "../views/orgEditorConstants";
import { useMdModules } from "../views/chat/hooks/useMdModules";
import { useSourceTagFormatter } from "../views/chat/components/SourceBadge";

export interface OrgMonitorPanelProps {
  orgId: string;
  nodeId: string;
  apiBaseUrl: string;
  nodes: Node[];
  visible: boolean;
}

// ── NodeTasksTabContent (moved from OrgEditorView) ──

function NodeTasksTabContent({
  nodeTasks,
  nodeActivePlan,
  loading,
  nodes,
}: {
  nodeTasks: { assigned: any[]; delegated: any[] } | null;
  nodeActivePlan: any;
  loading: boolean;
  nodes: Node[];
}) {
  const { t } = useTranslation();
  const nodeMap = new Map(nodes.map((n) => [n.id, (n.data as any)?.role_title || n.id]));
  const getNodeLabel = (id: string | null) => (id ? nodeMap.get(id) || id : "-");

  if (loading) {
    return <div style={{ fontSize: 12, color: "var(--muted)", padding: 12 }}>{t("org.monitor.loading")}</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 12, minWidth: 0 }}>
      {nodeActivePlan && (
        <div className="card" style={{ padding: 10, minWidth: 0, overflow: "hidden" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "#b45309" }}>{t("org.monitor.currentTask")}</div>
          <div style={{ fontWeight: 500, marginBottom: 6, ...WRAP_TEXT_STYLE }}>{nodeActivePlan.title}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 10, color: "var(--muted)" }}>{t("org.monitor.progress")}</span>
            <div style={{ flex: 1, height: 4, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
              <div style={{ height: "100%", borderRadius: 2, background: "var(--accent)", width: `${nodeActivePlan.progress_pct ?? 0}%` }} />
            </div>
            <span style={{ fontSize: 10, color: "var(--muted)" }}>{nodeActivePlan.progress_pct ?? 0}%</span>
          </div>
          {(nodeActivePlan.plan_steps?.length ?? 0) > 0 && (
            <div style={{ fontSize: 11 }}>
              {(nodeActivePlan.plan_steps || []).map((s: any, i: number) => {
                const st = s.status || "pending";
                const icon = st === "completed" ? "✓" : st === "in_progress" ? "→" : "○";
                const color = st === "completed" ? "#22c55e" : st === "in_progress" ? "#3b82f6" : "var(--muted)";
                return (
                  <div key={s.id || i} style={{ display: "flex", gap: 6, alignItems: "flex-start", marginBottom: 4, minWidth: 0 }}>
                    <span style={{ color, fontWeight: 600, flexShrink: 0 }}>{icon}</span>
                    <span style={{ color: "var(--text)", ...WRAP_TEXT_STYLE }}>{s.description || s.title || t("org.monitor.step", { n: i + 1 })}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ padding: 10, minWidth: 0, overflow: "hidden" }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{t("org.monitor.assignedToMe")}</div>
        {(nodeTasks?.assigned?.length ?? 0) === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>{t("org.monitor.none")}</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
            {(nodeTasks?.assigned || []).map((task: any) => (
              <div key={task.id} style={{ padding: 8, borderRadius: 6, border: "1px solid var(--line)", background: "var(--bg-subtle, var(--bg-card))", minWidth: 0, overflow: "hidden" }}>
                <div style={{ fontWeight: 500, marginBottom: 4, ...WRAP_TEXT_STYLE }}>{task.title}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, minWidth: 0, flexWrap: "wrap" }}>
                  <span style={{ padding: "1px 5px", borderRadius: 3, background: "var(--bg-app)", color: "var(--muted)" }}>
                    {t(TASK_STATUS_LABELS[task.status] || task.status)}
                  </span>
                  <span style={{ color: "var(--muted)" }}>{(task.progress_pct ?? 0)}%</span>
                </div>
                <div style={{ marginTop: 4, height: 3, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
                  <div style={{ height: "100%", borderRadius: 2, background: "var(--accent)", width: `${Math.min(100, task.progress_pct ?? 0)}%` }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 10, minWidth: 0, overflow: "hidden" }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{t("org.monitor.delegatedByMe")}</div>
        {(nodeTasks?.delegated?.length ?? 0) === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>{t("org.monitor.none")}</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
            {(nodeTasks?.delegated || []).map((task: any) => (
              <div key={task.id} style={{ padding: 8, borderRadius: 6, border: "1px solid var(--line)", background: "var(--bg-subtle, var(--bg-card))", minWidth: 0, overflow: "hidden" }}>
                <div style={{ fontWeight: 500, marginBottom: 4, ...WRAP_TEXT_STYLE }}>{task.title}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, minWidth: 0, flexWrap: "wrap" }}>
                  <span style={{ padding: "1px 5px", borderRadius: 3, background: "var(--bg-app)", color: "var(--muted)" }}>
                    {t(TASK_STATUS_LABELS[task.status] || task.status)}
                  </span>
                  <span style={{ color: "var(--muted)" }}>{(task.progress_pct ?? 0)}%</span>
                  <span style={{ color: "var(--muted)", marginLeft: "auto", ...WRAP_TEXT_STYLE }}>{t("org.monitor.executor", { name: getNodeLabel(task.assignee_node_id) })}</span>
                </div>
                <div style={{ marginTop: 4, height: 3, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
                  <div style={{ height: "100%", borderRadius: 2, background: "var(--accent)", width: `${Math.min(100, task.progress_pct ?? 0)}%` }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Monitor Panel ──

const MSG_TYPE_COLORS: Record<string, string> = {
  task_assign: "#7c3aed", task_result: "#059669",
  question: "#2563eb", answer: "#0891b2",
  escalation: "#dc2626", deliverable: "#d97706",
};

const WRAP_TEXT_STYLE = {
  minWidth: 0,
  overflowWrap: "anywhere",
  wordBreak: "break-word",
  whiteSpace: "pre-wrap",
} as const;

export function OrgMonitorPanel({ orgId, nodeId, apiBaseUrl, nodes, visible }: OrgMonitorPanelProps) {
  const { t } = useTranslation();
  const mdModules = useMdModules();
  const formatSourceTags = useSourceTagFormatter();
  const [nodeEvents, setNodeEvents] = useState<any[]>([]);
  const [nodeSchedules, setNodeSchedules] = useState<any[]>([]);
  const [nodeThinking, setNodeThinking] = useState<any[]>([]);
  const [expandedIdx, setExpandedIdx] = useState<number | string | null>(null);
  const [nodeTasks, setNodeTasks] = useState<{ assigned: any[]; delegated: any[] } | null>(null);
  const [nodeActivePlan, setNodeActivePlan] = useState<any>(null);
  const [nodeTasksLoading, setNodeTasksLoading] = useState(false);

  const nodeNameMap = useMemo(
    () => new Map(nodes.map((n) => [n.id, (n.data as any)?.role_title || n.id])),
    [nodes],
  );

  const selectedNode = nodes.find(n => n.id === nodeId)?.data as OrgNodeData | undefined;

  // Fetch node detail (events, schedules, thinking)
  useEffect(() => {
    if (!visible || !nodeId || !orgId) {
      setNodeEvents([]);
      setNodeSchedules([]);
      setNodeThinking([]);
      return;
    }
    const fetchNodeDetail = async () => {
      try {
        const [eventsRes, schedulesRes, thinkingRes] = await Promise.all([
          safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/events?actor=${nodeId}&limit=20`),
          safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/nodes/${nodeId}/schedules`),
          safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/nodes/${nodeId}/thinking?limit=30`),
        ]);
        if (eventsRes.ok) setNodeEvents(await eventsRes.json());
        if (schedulesRes.ok) setNodeSchedules(await schedulesRes.json());
        if (thinkingRes.ok) {
          const data = await thinkingRes.json();
          setNodeThinking(data.timeline || []);
        }
      } catch (e) {
        console.error("Failed to fetch node detail:", e);
      }
    };
    fetchNodeDetail();
    const interval = setInterval(fetchNodeDetail, 8000);
    return () => clearInterval(interval);
  }, [visible, nodeId, orgId, apiBaseUrl]);

  // Fetch node tasks
  useEffect(() => {
    if (!nodeId || !orgId) {
      setNodeTasks(null);
      setNodeActivePlan(null);
      return;
    }
    setNodeTasksLoading(true);
    const fetchNodeTasks = async () => {
      try {
        const [tasksRes, planRes] = await Promise.all([
          safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/nodes/${nodeId}/tasks`),
          safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/nodes/${nodeId}/active-plan`),
        ]);
        if (tasksRes.ok) {
          const data = await tasksRes.json();
          setNodeTasks({ assigned: data.assigned || [], delegated: data.delegated || [] });
        } else {
          setNodeTasks({ assigned: [], delegated: [] });
        }
        if (planRes.ok) {
          const planData = await planRes.json();
          setNodeActivePlan(planData.task_id ? planData : null);
        } else {
          setNodeActivePlan(null);
        }
      } catch {
        setNodeTasks({ assigned: [], delegated: [] });
        setNodeActivePlan(null);
      } finally {
        setNodeTasksLoading(false);
      }
    };
    fetchNodeTasks();
    const interval = setInterval(fetchNodeTasks, 10000);
    return () => clearInterval(interval);
  }, [nodeId, orgId, apiBaseUrl]);

  if (!selectedNode) return null;

  return (
    <div
      style={{
        width: 280, flexShrink: 0, minWidth: 0,
        borderLeft: "1px solid var(--line)",
        overflowY: "auto", overflowX: "hidden", scrollbarGutter: "stable",
        background: "var(--bg-app)",
        animation: "org-panel-in 0.3s cubic-bezier(0.4,0,0.2,1) 0.05s both",
      }}
    >
      <div style={{ padding: "12px 12px 8px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 13, flexShrink: 0 }}>{t("org.monitor.title")}</div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", justifyContent: "flex-end", minWidth: 0, flexWrap: "wrap" }}>
          <span style={{
            fontSize: 10, padding: "1px 6px", borderRadius: 4,
            background: `${STATUS_COLORS[selectedNode.status] || "var(--muted)"}20`,
            color: STATUS_COLORS[selectedNode.status] || "var(--muted)",
            fontWeight: 500,
          }}>
            {t(STATUS_LABELS[selectedNode.status] || selectedNode.status)}
          </span>
          {selectedNode.is_clone && <span style={{ fontSize: 9, color: "#0369a1" }}>{t("org.monitor.clone")}</span>}
          {selectedNode.ephemeral && <span style={{ fontSize: 9, color: "#b45309" }}>{t("org.monitor.ephemeral")}</span>}
        </div>
      </div>
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10, minWidth: 0 }}>

        {/* Tasks */}
        <NodeTasksTabContent
          nodeTasks={nodeTasks}
          nodeActivePlan={nodeActivePlan}
          loading={nodeTasksLoading}
          nodes={nodes}
        />

        {/* Schedules */}
        {nodeSchedules.length > 0 && (
          <div className="card" style={{ padding: 10, minWidth: 0, overflow: "hidden" }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{t("org.monitor.scheduledTasks")}</div>
            {nodeSchedules.map((s: any) => (
              <div key={s.id} style={{ padding: "4px 0", borderBottom: "1px solid var(--line)", fontSize: 11, minWidth: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6, minWidth: 0 }}>
                  <span style={{ fontWeight: 500, ...WRAP_TEXT_STYLE }}>{s.name}</span>
                  <span style={{
                    fontSize: 10, padding: "1px 5px", borderRadius: 3,
                    background: s.enabled ? "#dcfce7" : "#f3f4f6",
                    color: s.enabled ? "#166534" : "#9ca3af",
                  }}>
                    {s.enabled ? t("org.monitor.enabled") : t("org.monitor.disabled")}
                  </span>
                </div>
                {s.last_run_at && (
                  <div style={{ fontSize: 10, color: "#9ca3af", marginTop: 2, ...WRAP_TEXT_STYLE }}>{t("org.monitor.lastRun", { time: fmtDateTime(s.last_run_at) })}</div>
                )}
                {s.last_result_summary && (
                  <div style={{ fontSize: 10, color: "#6b7280", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {s.last_result_summary}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Recent events */}
        <div className="card" style={{ padding: 10, minWidth: 0, overflow: "hidden" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            {t("org.monitor.recentActivity")}
            {nodeEvents.length > 0 && (
              <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400, marginLeft: 4 }}>({nodeEvents.length})</span>
            )}
          </div>
          {nodeEvents.length === 0 ? (
            <div style={{ fontSize: 11, color: "var(--muted)" }}>{t("org.monitor.noActivity")}</div>
          ) : (
            <div style={{ maxHeight: 300, overflowY: "auto" }}>
              {nodeEvents.slice(0, 15).map((evt: any, i: number) => {
                const dataEntries = Object.entries(evt.data || {});
                const isEvtExpanded = expandedIdx === `evt-${i}`;
                const fullText = dataEntries.map(([k, v]) => `**${t(DATA_KEY_LABELS[k] || k)}**: ${translateDataValue(k, v, nodeNameMap)}`).join("\n\n");
                return (
                  <div key={evt.event_id || i}
                    onClick={() => setExpandedIdx(isEvtExpanded ? null : `evt-${i}`)}
                    style={{
                      padding: "4px 0", borderBottom: "1px solid var(--line)",
                      fontSize: 11, cursor: "pointer",
                      background: isEvtExpanded ? "var(--bg-subtle, transparent)" : undefined,
                      minWidth: 0,
                    }}>
                    <div style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0 }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                        background: evt.event_type?.includes("fail") || evt.event_type?.includes("error")
                          ? "var(--danger)"
                          : evt.event_type?.includes("complete") ? "var(--ok)" : "var(--primary)",
                      }} />
                      <span style={{ fontWeight: 500, ...WRAP_TEXT_STYLE }}>
                        {t(EVENT_TYPE_LABELS[evt.event_type] || evt.event_type?.replace(/_/g, " "))}
                      </span>
                      <span style={{ color: "var(--muted)", fontSize: 10, marginLeft: "auto" }}>
                        {fmtTime(evt.timestamp)}
                      </span>
                    </div>
                    {fullText && (
                      <div className="bb-entry-content" style={{
                        marginTop: 2, marginLeft: 12, fontSize: 10,
                        maxHeight: isEvtExpanded ? "none" : 48,
                        overflow: isEvtExpanded ? "visible" : "hidden",
                        ...WRAP_TEXT_STYLE,
                      }}>
                        {mdModules ? (
                          <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>{formatSourceTags(fullText)}</mdModules.ReactMarkdown>
                        ) : <div style={{ whiteSpace: "pre-wrap" }}>{fullText}</div>}
                      </div>
                    )}
                    {!isEvtExpanded && fullText.length > 80 && (
                      <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2, marginLeft: 12 }}>{t("org.monitor.expandFull")}</div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Thought chain */}
        <div className="card" style={{ padding: 10, minWidth: 0, overflow: "hidden" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            {t("org.monitor.thinkingChain")}
            {nodeThinking.length > 0 && (
              <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400, marginLeft: 4 }}>({nodeThinking.length})</span>
            )}
          </div>
          {nodeThinking.length === 0 ? (
            <div style={{ fontSize: 11, color: "var(--muted)" }}>{t("org.monitor.noThinkingChain")}</div>
          ) : (
            <div style={{ maxHeight: 400, overflowY: "auto" }}>
              {nodeThinking.slice(0, 30).map((item: any, i: number) => {
                const isMsg = item.type === "message";
                const isEvent = item.type === "event";
                const tsLocal = fmtTime(item.timestamp);
                const isExpanded = expandedIdx === i;

                if (isMsg) {
                  const isOut = item.direction === "out";
                  return (
                    <div key={i}
                      onClick={() => setExpandedIdx(isExpanded ? null : i)}
                      style={{
                        padding: "6px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                        cursor: "pointer", background: isExpanded ? "var(--bg-secondary)" : undefined,
                        minWidth: 0,
                      }}
                    >
                      <div style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0, flexWrap: "wrap" }}>
                        <span style={{
                          fontSize: 10, padding: "1px 5px", borderRadius: 3,
                          background: isOut ? "rgba(59,130,246,0.12)" : "rgba(245,158,11,0.12)",
                          color: isOut ? "#3b82f6" : "#f59e0b",
                          fontWeight: 500,
                        }}>
                          {isOut ? `→ ${item.peer}` : `← ${item.peer}`}
                        </span>
                        {item.msg_type && (
                          <span style={{
                            fontSize: 9, padding: "1px 4px", borderRadius: 3,
                            background: `${MSG_TYPE_COLORS[item.msg_type] || "#6b7280"}18`,
                            color: MSG_TYPE_COLORS[item.msg_type] || "#6b7280",
                          }}>
                            {t(MSG_TYPE_LABELS[item.msg_type] || item.msg_type.replace(/_/g, " "))}
                          </span>
                        )}
                        <span style={{ color: "var(--muted)", fontSize: 10, marginLeft: "auto" }}>{tsLocal}</span>
                      </div>
                      <div className="bb-entry-content" style={{
                        marginTop: 3, fontSize: 11,
                        maxHeight: isExpanded ? "none" : 60,
                        overflow: isExpanded ? "visible" : "hidden",
                        ...WRAP_TEXT_STYLE,
                      }}>
                        {mdModules ? (
                          <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>
                            {formatSourceTags(
                              isExpanded
                                ? (item.content || "")
                                : (item.content || "").length > 150
                                  ? (item.content || "").slice(0, 150) + "…"
                                  : (item.content || ""),
                            )}
                          </mdModules.ReactMarkdown>
                        ) : (
                          <div style={{ whiteSpace: "pre-wrap" }}>
                            {isExpanded ? (item.content || "") : (item.content || "").length > 150 ? (item.content || "").slice(0, 150) + "…" : (item.content || "")}
                          </div>
                        )}
                      </div>
                      {!isExpanded && (item.content || "").length > 150 && (
                        <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2 }}>{t("org.monitor.expandFull")}</div>
                      )}
                    </div>
                  );
                }

                if (isEvent) {
                  const evtType = item.event_type || "";
                  const isToolCall = evtType.includes("tool");
                  const isComplete = evtType.includes("complete");
                  const isError = evtType.includes("fail") || evtType.includes("error");
                  return (
                    <div key={i}
                      onClick={() => setExpandedIdx(isExpanded ? null : i)}
                      style={{
                        padding: "4px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                        cursor: "pointer", background: isExpanded ? "var(--bg-secondary)" : undefined,
                        minWidth: 0,
                      }}
                    >
                      <div style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0 }}>
                        <span style={{
                          width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                          background: isError ? "var(--danger)" : isComplete ? "var(--ok)" : isToolCall ? "#7c3aed" : "var(--primary)",
                        }} />
                        <span style={{ fontWeight: 500, fontSize: 10, color: isToolCall ? "#7c3aed" : undefined, ...WRAP_TEXT_STYLE }}>
                          {isToolCall ? "[T] " : ""}{t(EVENT_TYPE_LABELS[evtType] || evtType.replace(/_/g, " "))}
                        </span>
                        <span style={{ color: "var(--muted)", fontSize: 10, marginLeft: "auto" }}>{tsLocal}</span>
                      </div>
                      {item.data && Object.keys(item.data).length > 0 && (() => {
                        const entries = Object.entries(item.data).slice(0, isExpanded ? 20 : 3);
                        const mdText = entries.map(([k, v]) => {
                          const tv = translateDataValue(k, v, nodeNameMap);
                          return `**${t(DATA_KEY_LABELS[k] || k)}**: ${isExpanded ? tv : tv.slice(0, 120)}`;
                        }).join("\n\n");
                        return (
                          <div className="bb-entry-content" style={{ fontSize: 10, marginTop: 2, marginLeft: 12, ...WRAP_TEXT_STYLE }}>
                            {mdModules ? (
                              <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>{mdText}</mdModules.ReactMarkdown>
                            ) : <span style={{ color: "var(--muted)" }}>{mdText}</span>}
                          </div>
                        );
                      })()}
                      {!isExpanded && item.data && Object.keys(item.data).length > 3 && (
                        <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2, marginLeft: 12 }}>
                          {t("org.monitor.showAllFields", { count: Object.keys(item.data).length })}
                        </div>
                      )}
                    </div>
                  );
                }

                return null;
              })}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
