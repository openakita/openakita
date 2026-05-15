import {
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  useLayoutEffect,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import { useMdModules } from "./chat/hooks/useMdModules";
import {
  ReactFlow,
  Background,
  useNodesState,
  useEdgesState,
  addEdge,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
  type EdgeTypes,
  type ReactFlowInstance,
  type NodeChange,
  type EdgeChange,
  Handle,
  Position,
  MarkerType,
  Panel,
  ReactFlowProvider,
  type OnConnect,
  type ConnectionLineComponentProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  IconPlus,
  IconTrash,
  IconRefresh,
  IconPlay,
  IconStop,
  IconCheck,
  IconX,
  IconUsers,
  IconChevronDown,
  IconChevronRight,
  IconRadar,
  IconSnowflake,
  IconGear,
  IconBuilding,
  IconClipboard,
  IconMenu,
  IconSitemap,
  IconAlertCircle,
  IconUpload,
  IconMessageCircle,
  IconUnlock,
  IconPin,
  IconShuffle,
  IconBot,
  IconPlug,
} from "../icons";
import { safeFetch } from "../providers";
import { IS_CAPACITOR, saveFileDialog, IS_TAURI, writeTextFile, openFileDialog, onWsEvent, saveAttachment } from "../platform";
import { OrgInboxSidebar } from "../components/OrgInboxSidebar";
import { WorkbenchNodePicker, type WorkbenchTemplate } from "../components/WorkbenchNodePicker";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { OrgAvatar, AVATAR_PRESETS, AVATAR_MAP } from "../components/OrgAvatars";
import { OrgChatPanel } from "../components/OrgChatPanel";
import { OrgBlackboardPanel, type OrgBlackboardPanelHandle } from "../components/OrgBlackboardPanel";
import { OrgMonitorPanel } from "../components/OrgMonitorPanel";
import { OrgDashboard } from "../components/OrgDashboard";
import { OrgProjectBoard } from "../components/OrgProjectBoard";
import { ZoomIn, ZoomOut, Maximize, X as XIcon } from "lucide-react";
import { Button } from "../components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Checkbox } from "../components/ui/checkbox";
import { Input as ShadInput } from "../components/ui/input";
import { Label as ShadLabel } from "../components/ui/label";
import { Slider } from "../components/ui/slider";
import { Switch } from "../components/ui/switch";
import { Badge } from "../components/ui/badge";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "../components/ui/tooltip";
import { ToggleGroup, ToggleGroupItem } from "../components/ui/toggle-group";
import {
  fmtTime, fmtDateTime, fmtShortDate, stripMd,
  STATUS_LABELS, STATUS_COLORS, ORG_STATUS_LABELS,
  EDGE_COLORS, DEPT_COLORS, getDeptColor,
  BB_TYPE_COLORS, BB_TYPE_LABELS,
  type OrgNodeData, type OrgEdgeData, type OrgSummary,
  type OrgFull, type TemplateSummary,
} from "./orgEditorConstants";
import agentOrgImg from "../assets/agent_org.png";

// ── Task text helpers ──

/** Replace node IDs with human-readable role titles in task display text. */
function humanizeTask(text: string, nodes: { id: string; data: any }[]): string {
  if (!text) return "";
  const nameOf = (id: string): string => {
    const nd = nodes.find((n) => n.id === id);
    return nd?.data?.role_title || id;
  };
  let s = text
    .replace(/来自\s+([a-zA-Z][a-zA-Z0-9_-]*)/g, (_, id) => i18n.t("org.editor.fromNode", { name: nameOf(id) }))
    .replace(/to_node\s*=\s*([a-zA-Z][a-zA-Z0-9_-]*)/g, (_, id) => `to_node=${nameOf(id)}`);
  const _tcPfx = i18n.t("org.editor.taskChain").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  s = s.replace(new RegExp(_tcPfx + "[^\\]]*\\]", "g"), "");
  s = s.replace(/,?\s*task_chain_id=[^\s,)}\]]+/g, "");
  return s.trim();
}

// ── Custom Canvas Controls (shadcn UI) ──

function OrgCanvasControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { t } = useTranslation();

  return (
    <Panel position="top-right" style={{ marginTop: 12, marginRight: 12 }}>
      <TooltipProvider>
        <div className="flex flex-col gap-1 rounded-lg border border-border/50 bg-card/90 p-1 shadow-md backdrop-blur-sm">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => zoomIn()}>
                <ZoomIn className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">{t("org.editor.zoomIn")}</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => zoomOut()}>
                <ZoomOut className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">{t("org.editor.zoomOut")}</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => fitView({ padding: 0.2 })}>
                <Maximize className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">{t("org.editor.fitView")}</TooltipContent>
          </Tooltip>
        </div>
      </TooltipProvider>
    </Panel>
  );
}

// Types and helpers imported from ./orgEditorConstants

function orgNodeToFlowNode(n: OrgNodeData, extra?: Record<string, any>): Node {
  return {
    id: n.id,
    type: "orgNode",
    position: n.position,
    data: extra ? { ...n, ...extra } : { ...n },
  };
}

function orgEdgeToFlowEdge(e: OrgEdgeData): Edge {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    type: "default",
    label: e.label || undefined,
    style: { stroke: EDGE_COLORS[e.edge_type] || "var(--muted)", strokeWidth: e.edge_type === "hierarchy" ? 2 : 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS[e.edge_type] || "var(--muted)" },
    animated: e.edge_type === "collaborate",
    data: { ...e },
  };
}

// ── Auto-layout: tree hierarchy ──

function computeTreeLayout(nodes: Node[], edges: Edge[]): Node[] {
  if (nodes.length === 0) return nodes;

  const NODE_W = 240;
  const NODE_H = 100;
  const GAP_X = 40;
  const GAP_Y = 80;

  const childrenMap: Record<string, string[]> = {};
  const parentSet = new Set<string>();
  for (const e of edges) {
    const src = e.source;
    const tgt = e.target;
    if (!childrenMap[src]) childrenMap[src] = [];
    childrenMap[src].push(tgt);
    parentSet.add(tgt);
  }

  const roots = nodes.filter((n) => !parentSet.has(n.id));
  if (roots.length === 0) return nodes;

  const levels: string[][] = [];
  const visited = new Set<string>();

  function bfs() {
    let queue = roots.map((r) => r.id);
    while (queue.length > 0) {
      const level: string[] = [];
      const next: string[] = [];
      for (const id of queue) {
        if (visited.has(id)) continue;
        visited.add(id);
        level.push(id);
        for (const c of childrenMap[id] || []) {
          if (!visited.has(c)) next.push(c);
        }
      }
      if (level.length > 0) levels.push(level);
      queue = next;
    }
  }
  bfs();

  for (const n of nodes) {
    if (!visited.has(n.id)) {
      if (levels.length === 0) levels.push([]);
      levels[levels.length - 1].push(n.id);
    }
  }

  const posMap: Record<string, { x: number; y: number }> = {};
  const maxLevelWidth = Math.max(...levels.map((l) => l.length));
  const totalW = maxLevelWidth * (NODE_W + GAP_X) - GAP_X;

  for (let li = 0; li < levels.length; li++) {
    const level = levels[li];
    const levelW = level.length * (NODE_W + GAP_X) - GAP_X;
    const offsetX = (totalW - levelW) / 2;
    for (let ni = 0; ni < level.length; ni++) {
      posMap[level[ni]] = {
        x: offsetX + ni * (NODE_W + GAP_X),
        y: li * (NODE_H + GAP_Y),
      };
    }
  }

  return nodes.map((n) => {
    const pos = posMap[n.id];
    if (!pos) return n;
    return { ...n, position: { x: pos.x, y: pos.y } };
  });
}

const NODE_COLLISION_W = 200;
const NODE_COLLISION_H = 80;
const NEW_NODE_ANCHOR = { x: 250, y: 200 };
const NEW_NODE_STEP_X = 240;
const NEW_NODE_STEP_Y = 140;

function isPositionOccupied(nodes: Node[], candidate: { x: number; y: number }): boolean {
  return nodes.some((n) => {
    const p = n.position || { x: 0, y: 0 };
    return Math.abs(p.x - candidate.x) < NODE_COLLISION_W && Math.abs(p.y - candidate.y) < NODE_COLLISION_H;
  });
}

function getNextNodePosition(nodes: Node[]): { x: number; y: number } {
  if (nodes.length === 0) return { ...NEW_NODE_ANCHOR };
  if (!isPositionOccupied(nodes, NEW_NODE_ANCHOR)) return { ...NEW_NODE_ANCHOR };

  const columns = Math.max(3, Math.ceil(Math.sqrt(nodes.length + 1)));
  const maxAttempts = (nodes.length + 16) * 2;

  for (let i = 0; i < maxAttempts; i++) {
    const row = Math.floor(i / columns);
    const col = i % columns;
    const candidate = {
      x: NEW_NODE_ANCHOR.x + col * NEW_NODE_STEP_X,
      y: NEW_NODE_ANCHOR.y + row * NEW_NODE_STEP_Y,
    };
    if (!isPositionOccupied(nodes, candidate)) return candidate;
  }

  const maxX = nodes.reduce((m, n) => Math.max(m, n.position?.x ?? 0), 0);
  const maxY = nodes.reduce((m, n) => Math.max(m, n.position?.y ?? 0), 0);
  return { x: maxX + NEW_NODE_STEP_X, y: maxY + NEW_NODE_STEP_Y };
}

// ── Custom Connection Line (visible dashed line from handle to cursor) ──

function OrgConnectionLine({ fromX, fromY, toX, toY }: ConnectionLineComponentProps) {
  return (
    <g>
      <circle cx={fromX} cy={fromY} r={5} fill="var(--primary, #6366f1)" />
      <path
        d={`M${fromX},${fromY} C ${fromX},${(fromY + toY) / 2} ${toX},${(fromY + toY) / 2} ${toX},${toY}`}
        fill="none"
        stroke="var(--primary, #6366f1)"
        strokeWidth={2.5}
        strokeDasharray="8 4"
        strokeLinecap="round"
      />
      <circle cx={toX} cy={toY} r={4} fill="var(--primary, #6366f1)" opacity={0.6} />
    </g>
  );
}

// ── Custom Node Component ──

function OrgNodeComponent({ data, selected }: { data: OrgNodeData; selected: boolean }) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);
  const nodeRef = useRef<HTMLDivElement>(null);
  const deptColor = getDeptColor(data.department);
  const isLive = (data as any)._liveMode === true;
  const rawStatusColor = STATUS_COLORS[data.status] || "var(--muted)";
  const statusColor = (!isLive && data.status === "idle") ? "var(--muted)" : rawStatusColor;
  const isFrozen = data.status === "frozen";
  const isBusy = data.status === "busy";
  const isError = data.status === "error";
  const isWaiting = data.status === "waiting";
  const isClone = data.is_clone;
  const isEphemeral = data.ephemeral;

  const rt = (data as any)._runtime;
  const idleSecs = rt?.idle_seconds;
  const pendingMsgs = rt?.pending_messages;
  const isAnomaly = rt?.anomaly;

  return (
    <div
      ref={nodeRef}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: "var(--card-bg, #fff)",
        border: `2px solid ${selected ? "var(--primary)" : isAnomaly ? "#f59e0b" : isError ? "var(--danger)" : isBusy ? statusColor : "var(--line)"}`,
        borderRadius: "var(--radius)",
        padding: 0,
        minWidth: 180,
        maxWidth: 220,
        boxShadow: selected
          ? "0 0 0 2px var(--primary)"
          : isAnomaly
          ? "0 0 12px rgba(245,158,11,0.35)"
          : isBusy
          ? `0 0 16px ${statusColor}50`
          : isError
          ? `0 0 12px var(--danger, #ef4444)30`
          : "0 1px 4px rgba(0,0,0,0.08)",
        opacity: isFrozen ? 0.5 : 1,
        filter: isFrozen ? "grayscale(0.6)" : "none",
        transition: "all 0.3s ease",
        animation: isBusy
          ? "orgNodePulse 2s ease-in-out infinite"
          : isError
          ? "orgNodeError 1s ease-in-out infinite"
          : isWaiting
          ? "orgNodeWait 3s ease-in-out infinite"
          : "none",
        position: "relative",
        zIndex: hovered ? 10000 : "auto",
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="org-handle"
        isConnectable
        title={t("org.editor.targetHandle")}
        aria-label={t("org.editor.targetHandleLabel")}
      />

      {/* Department color strip */}
      <div style={{
        height: 4,
        borderRadius: "var(--radius) var(--radius) 0 0",
        background: isBusy
          ? `linear-gradient(90deg, ${deptColor}, ${statusColor}, ${deptColor})`
          : isAnomaly
          ? "linear-gradient(90deg, #f59e0b, #fbbf24, #f59e0b)"
          : deptColor,
        backgroundSize: isBusy || isAnomaly ? "200% 100%" : undefined,
        animation: isBusy ? "orgStripFlow 2s linear infinite" : isAnomaly ? "orgStripFlow 3s linear infinite" : undefined,
      }} />

      <div style={{ padding: "8px 10px", display: "flex", gap: 8, alignItems: "flex-start" }}>
        {/* Avatar */}
        <OrgAvatar
          avatarId={data.avatar}
          size={30}
          statusColor={statusColor}
          statusGlow={isBusy}
          statusTitle={
            !isLive && data.status === "idle" ? t("org.editor.notActivated")
            : data.status === "idle" && idleSecs != null && idleSecs > 60
              ? `${t("org.editor.idleLabel")} ${idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h${Math.floor((idleSecs % 3600) / 60)}m` : `${Math.floor(idleSecs / 60)}m`}`
            : STATUS_LABELS[data.status] ? t(STATUS_LABELS[data.status]) : data.status
          }
          style={isBusy ? { border: `2px solid ${statusColor}` } : isError ? { border: "2px solid var(--danger)" } : undefined}
        />
        {/* Title area */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
            <span style={{
              fontSize: 13,
              fontWeight: 600,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: 1,
            }}>
              {data.role_title}
            </span>
            {(isClone || isEphemeral) && (
              <span style={{
                fontSize: 9,
                padding: "0 4px",
                borderRadius: 3,
                background: isEphemeral ? "#fef3c7" : "#e0f2fe",
                color: isEphemeral ? "#b45309" : "#0369a1",
                fontWeight: 500,
              }}>
                {isEphemeral ? t("org.editor.ephemeral") : t("org.editor.clone")}
              </span>
            )}
            {data.plugin_origin && (
              <span
                title={t("org.editor.workbenchBadge", "工作台") + "：" + (data.plugin_origin.plugin_id || "")}
                style={{
                  fontSize: 9,
                  padding: "0 5px",
                  borderRadius: 3,
                  background: "#ecfeff",
                  color: "#0e7490",
                  fontWeight: 600,
                  border: "1px solid #a5f3fc",
                  whiteSpace: "nowrap",
                }}
              >
                {t("org.editor.workbenchBadge", "工作台")}
              </span>
            )}
          </div>

        {/* Goal preview */}
        {data.role_goal && (
          <div style={{
            fontSize: 10,
            color: "var(--muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            marginBottom: 4,
            maxWidth: 180,
          }}>
            {data.role_goal}
          </div>
        )}

        {/* Department + status tags + runtime metrics */}
        <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
          {data.department && (
            <span style={{
              fontSize: 10,
              padding: "1px 6px",
              borderRadius: 4,
              background: `${deptColor}15`,
              color: deptColor,
              fontWeight: 500,
            }}>
              {data.department}
            </span>
          )}
          {(!isLive && data.status === "idle") ? (
            <span style={{
              fontSize: 10, padding: "1px 6px", borderRadius: 4,
              background: "var(--bg-subtle, #f3f4f6)", color: "var(--muted)",
              fontWeight: 500,
            }}>
              {t("org.editor.notActivated")}
            </span>
          ) : data.status !== "idle" ? (
            <span style={{
              fontSize: 10, padding: "1px 6px", borderRadius: 4,
              background: `${statusColor}15`, color: statusColor,
              fontWeight: 500,
            }}>
              {i18n.t(STATUS_LABELS[data.status]) || data.status}
            </span>
          ) : (
            <span style={{
              fontSize: 10, padding: "1px 6px", borderRadius: 4,
              background: `${statusColor}15`, color: statusColor,
              fontWeight: 500,
            }}
              title={idleSecs != null && idleSecs > 60
                ? `${t("org.editor.idleLabel")} ${idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h${Math.floor((idleSecs % 3600) / 60)}m` : `${Math.floor(idleSecs / 60)}m`}`
                : t("org.editor.onlineIdle")}
            >
              {t("org.editor.nodeIdle")}
            </span>
          )}
          {pendingMsgs > 0 && (
            <span style={{
              fontSize: 9, padding: "1px 5px", borderRadius: 10,
              background: "#fef2f2", color: "#dc2626", fontWeight: 600,
            }}>
              {pendingMsgs}
            </span>
          )}
          {idleSecs != null && idleSecs > 60 && data.status === "idle" && isLive && (
            <span style={{
              fontSize: 9, padding: "1px 5px", borderRadius: 3,
              background: "#f3f4f6", color: "#9ca3af",
            }}>
              {idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h` : `${Math.floor(idleSecs / 60)}m`}
            </span>
          )}
        </div>

        {/* Current task indicator */}
        {isBusy && data.current_task && (
          <div style={{
            fontSize: 9, color: statusColor, marginTop: 3,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            maxWidth: 180, fontStyle: "italic", opacity: 0.85,
          }}>
            {stripMd(data.current_task)}
          </div>
        )}

        {/* Anomaly warning */}
        {isAnomaly && (
          <div style={{ fontSize: 9, color: "#f59e0b", marginTop: 3, display: "flex", alignItems: "center", gap: 3 }}>
            <IconAlertCircle size={10} color="#f59e0b" />
            <span>{typeof isAnomaly === "string" ? isAnomaly : t("org.editor.needsAttention")}</span>
          </div>
        )}

        {/* Frozen indicator */}
        {isFrozen && (
          <div style={{ fontSize: 10, color: "#93c5fd", marginTop: 4, display: "flex", alignItems: "center", gap: 3 }}>
            <IconSnowflake size={11} color="#93c5fd" />
            <span>{data.frozen_reason || t("org.editor.frozen")}</span>
          </div>
        )}
        </div>{/* close title area */}
      </div>

      {/* Hover tooltip via Portal to escape ReactFlow stacking context */}
      {hovered && rt && nodeRef.current && createPortal(
        (() => {
          const rect = nodeRef.current!.getBoundingClientRect();
          const pp = rt.plan_progress as { completed?: number; total?: number } | undefined;
          const ds = rt.delegated_summary as { in_progress?: number; completed?: number; total?: number } | undefined;
          const extTools = (rt.external_tools as string[] | undefined) || [];
          const runningSince = rt.running_since as string | number | undefined;
          const recentTs = rt.recent_activity_ts as string | number | undefined;
          const watchdog = rt.last_watchdog_action as string | undefined;
          const Sep = () => <div style={{ height: 1, background: "var(--line)", margin: "6px 0" }} />;
          return (
            <div style={{
              position: "fixed",
              left: rect.right + 8,
              top: rect.top,
              zIndex: 99999,
              background: "var(--card-bg, #fff)", border: "1px solid var(--line)",
              borderRadius: 6, padding: "10px 12px", minWidth: 240,
              pointerEvents: "none",
              boxShadow: "0 4px 12px rgba(0,0,0,0.15)", fontSize: 10,
            }}>
              <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 11 }}>{data.role_title}</div>
              <div style={{ color: "#6b7280", lineHeight: 1.6 }}>
                <div>{t("org.editor.department")}: {data.department || "—"} · {t("org.editor.levelField")} L{data.level ?? "?"}</div>
                <div>{t("org.editor.statusLabel")}: <span style={{ color: statusColor, fontWeight: 500 }}>{!isLive && data.status === "idle" ? t("org.editor.notActivated") : isLive && data.status === "idle" ? t("org.editor.nodeIdle") : STATUS_LABELS[data.status] ? t(STATUS_LABELS[data.status]) : data.status}</span></div>
                {idleSecs != null && <div>{t("org.editor.idleLabel")}: {idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h${Math.floor((idleSecs % 3600) / 60)}m` : idleSecs >= 60 ? `${Math.floor(idleSecs / 60)}m` : `${idleSecs}s`}</div>}
                {pendingMsgs != null && pendingMsgs > 0 && <div>{t("org.editor.pendingLabel")}: {t("org.editor.messagesLabel", { count: pendingMsgs })}</div>}
                {((pp && pp.total != null && pp.total > 0) || (ds && (ds.total ?? 0) > 0)) && <Sep />}
                {pp && pp.total != null && pp.total > 0 && (
                  <div>
                    {t("org.editor.planProgress")}: {pp.completed ?? 0}/{pp.total}
                    <div style={{ marginTop: 2, height: 4, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
                      <div style={{ height: "100%", width: `${Math.min(100, ((pp.completed ?? 0) / pp.total) * 100)}%`, background: "var(--primary)", borderRadius: 2 }} />
                    </div>
                  </div>
                )}
                {ds && (ds.total ?? 0) > 0 && (
                  <div>{t("org.editor.delegatedTasks")}: {ds.in_progress ?? 0} / {ds.completed ?? 0} / {ds.total}</div>
                )}
                {(runningSince != null || extTools.length > 0 || recentTs != null || watchdog) && <Sep />}
                {runningSince != null && (
                  <div>{t("org.editor.running")}: {typeof runningSince === "number" ? fmtTime(runningSince) : fmtShortDate(runningSince)}</div>
                )}
                {extTools.length > 0 && <div>{t("org.editor.externalTools")}: {extTools.slice(0, 3).join(", ")}{extTools.length > 3 ? ` +${extTools.length - 3}` : ""}</div>}
                {recentTs != null && <div>{t("org.editor.recentActivity")}: {fmtShortDate(recentTs)}</div>}
                {watchdog && <div>{t("org.editor.watchdog")}: {watchdog}</div>}
                {(data.current_task || isAnomaly) && <Sep />}
                {data.current_task && (
                  <div style={{ marginTop: 2, color: "#b45309", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 200, overflow: "auto", lineHeight: 1.5 }}>
                    {data.current_task}
                  </div>
                )}
                {isAnomaly && <div style={{ marginTop: 2, color: "#f59e0b", fontWeight: 500 }}>{typeof isAnomaly === "string" ? isAnomaly : t("org.editor.errorLabel")}</div>}
              </div>
            </div>
          );
        })(),
        document.body,
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        className="org-handle"
        isConnectable
        title={t("org.editor.sourceHandle")}
        aria-label={t("org.editor.sourceHandleLabel")}
      />
    </div>
  );
}

const nodeTypes: NodeTypes = {
  orgNode: OrgNodeComponent as any,
};

// ── Main Component ──

export function OrgEditorView({
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
}) {
  const { t } = useTranslation();
  const mdModules = useMdModules();

  // State
  const [orgList, setOrgList] = useState<OrgSummary[]>([]);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [currentOrg, setCurrentOrg] = useState<OrgFull | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const lastSavedRef = useRef<string>("");
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);
  const [showNewNodeForm, setShowNewNodeForm] = useState(false);
  const [showWorkbenchPicker, setShowWorkbenchPicker] = useState(false);
  const [propsTab, setPropsTab] = useState<"overview" | "identity" | "capabilities">("overview");
  const [fullPromptPreview, setFullPromptPreview] = useState<string | null>(null);
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false);
  const [layoutLocked, setLayoutLocked] = useState(false);
  const liveMode = currentOrg?.status === "active" || currentOrg?.status === "running";
  const isCanvasLocked = layoutLocked || liveMode;
  const [activeDrawer, setActiveDrawer] = useState<"chat" | "inbox" | null>(null);
  const [showNodeChat, setShowNodeChat] = useState(false);
  const [orgStats, setOrgStats] = useState<any>(null);
  const [editingName, setEditingName] = useState(false);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const [toast, setToast] = useState<{ message: string; type: "ok" | "error" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  type AgentProfileEntry = { id: string; name: string; description: string; icon: string };
  const [agentProfiles, setAgentProfiles] = useState<AgentProfileEntry[]>([]);
  const [agentProfileSearch, setAgentProfileSearch] = useState("");
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);

  const [viewMode, setViewMode] = useState<"canvas" | "projects" | "dashboard">("canvas");
  const chatPanelOpen = activeDrawer === "chat";
  const inboxOpen = activeDrawer === "inbox";
  const reactFlowRef = useRef<ReactFlowInstance<Node, Edge> | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    type: "node" | "edge" | "pane";
    id: string | null;
    flowX?: number;
    flowY?: number;
  } | null>(null);
  const [clipboardNode, setClipboardNode] = useState<any>(null);
  useEffect(() => {
    if (!contextMenu) return;
    const dismiss = () => setContextMenu(null);
    window.addEventListener("click", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => { window.removeEventListener("click", dismiss); window.removeEventListener("scroll", dismiss, true); };
  }, [contextMenu]);

  const [edgeAnimations, setEdgeAnimations] = useState<Record<string, { color: string; ts: number }>>({});
  const [edgeFlowCounts, setEdgeFlowCounts] = useState<Record<string, number>>({});

  // React Flow state
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([] as Edge[]);

  const showToast = useCallback((message: string, type: "ok" | "error" = "ok") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  // MCP/Skill lists for selection
  const [availableMcpServers, setAvailableMcpServers] = useState<{ name: string; status: string }[]>([]);
  const [availableSkills, setAvailableSkills] = useState<{ name: string; description?: string; name_i18n?: string; description_i18n?: string; category?: string | null }[]>([]);

  // Blackboard panel ref (data managed by OrgBlackboardPanel)
  const bbPanelRef = useRef<OrgBlackboardPanelHandle>(null);

  // Capabilities search
  const [mcpSearch, setMcpSearch] = useState("");
  const [skillSearch, setSkillSearch] = useState("");

  // Org settings panel collapse
  const [personaCollapsed, setPersonaCollapsed] = useState(false);
  const [bizCollapsed, setBizCollapsed] = useState(false);
  const [orgWatchdogCollapsed, setOrgWatchdogCollapsed] = useState(true);
  const [watchdogCollapsed, setWatchdogCollapsed] = useState(true);
  // 看门狗本地草稿：留空 = 用后端默认；填 0 = 显式禁用；填正数 = 自定义。
  const [watchdogDraft, setWatchdogDraft] = useState<{
    warn: string;
    autostop: string;
    timeout: string;
    deadlock: string;
  }>({
    warn: "",
    autostop: "",
    timeout: "",
    deadlock: "",
  });
  const [watchdogLoaded, setWatchdogLoaded] = useState(false);
  const [watchdogSaving, setWatchdogSaving] = useState(false);

  const loadWatchdogConfig = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/config/env`);
      const data = await res.json();
      const env = (data?.env || {}) as Record<string, string>;
      setWatchdogDraft({
        warn: env.ORG_COMMAND_STUCK_WARN_SECS || "",
        autostop: env.ORG_COMMAND_STUCK_AUTOSTOP_SECS || "",
        timeout: env.ORG_COMMAND_TIMEOUT_SECS || "",
        deadlock: env.ORG_COMMAND_DEADLOCK_GRACE_SECS || "",
      });
      setWatchdogLoaded(true);
    } catch {
      setWatchdogLoaded(true);
    }
  }, [apiBaseUrl]);

  const saveWatchdogConfig = useCallback(async () => {
    setWatchdogSaving(true);
    try {
      // 保存策略：用户清空 = 显式删除 .env 行 → 后端 fallback 到 default；
      // 用户填了值（含 "0"） = 写入 .env 覆盖默认。/api/config/env 的
      // entries 空串语义是"保留原行"，真删除必须走 delete_keys。
      const draftMap: Record<string, string> = {
        ORG_COMMAND_STUCK_WARN_SECS: watchdogDraft.warn.trim(),
        ORG_COMMAND_STUCK_AUTOSTOP_SECS: watchdogDraft.autostop.trim(),
        ORG_COMMAND_TIMEOUT_SECS: watchdogDraft.timeout.trim(),
        ORG_COMMAND_DEADLOCK_GRACE_SECS: watchdogDraft.deadlock.trim(),
      };
      const entries: Record<string, string> = {};
      const delete_keys: string[] = [];
      for (const [k, v] of Object.entries(draftMap)) {
        if (v === "") delete_keys.push(k);
        else entries[k] = v;
      }
      await safeFetch(`${apiBaseUrl}/api/config/env`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries, delete_keys }),
      });
      showToast(t("org.editor.watchdogSaved"), "ok");
    } catch {
      showToast(t("org.editor.watchdogSaveFailed"), "error");
    } finally {
      setWatchdogSaving(false);
    }
  }, [apiBaseUrl, watchdogDraft, showToast, t]);

  // New node form
  const [newNodeTitle, setNewNodeTitle] = useState("");
  const [newNodeDept, setNewNodeDept] = useState("");
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768 || IS_CAPACITOR);
  const [showLeftPanel, setShowLeftPanel] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(false);
  const [showBlackboardPanel, setShowBlackboardPanel] = useState(false);
  const [creatingOrg, setCreatingOrg] = useState(false);
  const orgCreateBusyRef = useRef(false);

  useLayoutEffect(() => {
    let prev = window.innerWidth < 768 || IS_CAPACITOR;
    const onResize = () => {
      const mobile = window.innerWidth < 768 || IS_CAPACITOR;
      setIsMobile(mobile);
      if (mobile && !prev) setShowLeftPanel(false);
      if (!mobile && prev) setShowLeftPanel(true);
      prev = mobile;
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    setNodes((prev) =>
      prev.map((n) =>
        (n.data as any)._liveMode === liveMode
          ? n
          : { ...n, data: { ...n.data, _liveMode: liveMode } },
      ),
    );
  }, [liveMode, setNodes]);

  // ── Data fetching ──

  const fetchOrgList = useCallback(async (): Promise<OrgSummary[]> => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs`);
      const data = await res.json();
      const list = Array.isArray(data) ? (data as OrgSummary[]) : [];
      list.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
      setOrgList(list);
      return list;
    } catch (e) {
      console.error("Failed to fetch orgs:", e);
      return [];
    }
  }, [apiBaseUrl]);

  const fetchTemplates = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/templates`);
      const data = await res.json();
      setTemplates(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error("Failed to fetch templates:", e);
    }
  }, [apiBaseUrl]);

  const fetchOrg = useCallback(async (orgId: string) => {
    setLoading(true);
    setActiveDrawer(null);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}`);
      const data: OrgFull = await res.json();
      if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
      setSaveStatus("idle");
      setCurrentOrg(data);
      lastSavedRef.current = "";
      const running = data.status === "active" || data.status === "running";
      const liveExtra = { _liveMode: running };
      const flowNodes = data.nodes.map((n) => {
        const fn = orgNodeToFlowNode(n, liveExtra);
        if ((fn.data as any).current_task) {
          (fn.data as any).current_task = humanizeTask((fn.data as any).current_task, data.nodes.map((nd) => ({ id: nd.id, data: nd })));
        }
        return fn;
      });
      const flowEdges = data.edges.map(orgEdgeToFlowEdge);
      // Preserve the user's saved canvas. Auto-layout is only run from explicit user actions.
      setNodes(flowNodes);
      setEdges(flowEdges);
      setSelectedNodeId(null);
      setEditingName(false);
      setLayoutLocked(Boolean(data.layout_locked));
    } catch (e) {
      console.error("Failed to fetch org:", e);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, setNodes, setEdges]);

  const fetchMcpServers = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/servers`);
      const data = await res.json();
      setAvailableMcpServers(data.servers || []);
    } catch { /* MCP endpoint may not be available */ }
  }, [apiBaseUrl]);

  const fetchAvailableSkills = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/skills`);
      const data = await res.json();
      setAvailableSkills(data.skills || []);
    } catch { /* skills endpoint may not be available */ }
  }, [apiBaseUrl]);

  // 实时刷新：监听 App.tsx 桥接的 'openakita:skills-changed' 事件，
  // 与 SkillManager.tsx 共享同一事件源，避免后端技能变更后该面板看到旧列表
  useEffect(() => {
    const onChange = () => { fetchAvailableSkills().catch(() => {}); };
    window.addEventListener("openakita:skills-changed", onChange);
    return () => window.removeEventListener("openakita:skills-changed", onChange);
  }, [fetchAvailableSkills]);


  const fetchAgentProfiles = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/profiles`);
      const data = await res.json();
      setAgentProfiles(data.profiles || []);
    } catch { /* ignore */ }
  }, [apiBaseUrl]);

  useEffect(() => {
    if (visible) {
      fetchOrgList();
      fetchTemplates();
      fetchMcpServers();
      fetchAvailableSkills();
      fetchAgentProfiles();
    }
  }, [visible, fetchOrgList, fetchTemplates, fetchMcpServers, fetchAvailableSkills, fetchAgentProfiles]);

  useEffect(() => {
    if (selectedOrgId && visible) {
      fetchOrg(selectedOrgId);
    }
  }, [selectedOrgId, visible, fetchOrg]);


  // ── WebSocket for real-time org events ──

  const triggerEdgeAnimation = useCallback((fromNode: string, toNode: string, color: string) => {
    const edgeKey = edges.find(
      (e) => (e.source === fromNode && e.target === toNode) || (e.source === toNode && e.target === fromNode),
    )?.id;
    if (!edgeKey) return;
    setEdgeAnimations((prev) => ({ ...prev, [edgeKey]: { color, ts: Date.now() } }));
    setEdgeFlowCounts((prev) => ({ ...prev, [edgeKey]: (prev[edgeKey] || 0) + 1 }));
    setTimeout(() => {
      setEdgeAnimations((prev) => {
        const copy = { ...prev };
        if (copy[edgeKey]?.ts && Date.now() - copy[edgeKey].ts >= 4500) delete copy[edgeKey];
        return copy;
      });
    }, 5000);
  }, [edges]);

  const currentOrgId = currentOrg?.id;
  useEffect(() => {
    if (!visible || !currentOrgId) return;
    const orgId = currentOrgId;

    return onWsEvent((ev, raw) => {
      const d = raw as Record<string, unknown> | null;
      if (!d || d.org_id !== orgId) return;

      if (ev === "org:node_status") {
        const { node_id, status, current_task } = d as any;
        setNodes((prev) => {
          const display = current_task ? humanizeTask(current_task, prev) : "";
          return prev.map((n) =>
            n.id === node_id
              ? { ...n, data: { ...n.data, status, current_task: display || n.data.current_task } }
              : n,
          );
        });
      } else if (ev === "org:task_delegated") {
        triggerEdgeAnimation((d as any).from_node, (d as any).to_node, "var(--primary)");
      } else if (ev === "org:task_delivered") {
        triggerEdgeAnimation((d as any).from_node, (d as any).to_node, "var(--ok)");
      } else if (ev === "org:task_accepted") {
        triggerEdgeAnimation((d as any).accepted_by, (d as any).from_node, "#22c55e");
      } else if (ev === "org:task_rejected") {
        triggerEdgeAnimation((d as any).rejected_by, (d as any).from_node, "var(--danger)");
      } else if (ev === "org:escalation") {
        triggerEdgeAnimation((d as any).from_node, (d as any).to_node, "var(--danger)");
      } else if (ev === "org:message") {
        triggerEdgeAnimation((d as any).from_node, (d as any).to_node, "#a78bfa");
      } else if (ev === "org:blackboard_update") {
        bbPanelRef.current?.refresh();
      } else if (ev === "org:status_change") {
        const newStatus = (d as any).status as string;
        setCurrentOrg((prev) => prev ? { ...prev, status: newStatus } : prev);
        setOrgList((prev) => prev.map((o) => o.id === orgId ? { ...o, status: newStatus } : o));
        if (newStatus === "dormant" || newStatus === "paused") {
          if (newStatus === "dormant") {
            setNodes((prev) => prev.map((n) => ({
              ...n,
              data: { ...n.data, status: "idle", current_task: null, _runtime: null },
            })));
          }
        }
      } else if (ev === "org:task_complete") {
        triggerEdgeAnimation((d as any).node_id, (d as any).node_id, "#22c55e");
      } else if (
        ev === "org:command_done" ||
        ev === "org:command_stopped_no_progress"
      ) {
        void fetchOrg(orgId);
        bbPanelRef.current?.refresh();
      } else if (ev === "org:command_phase") {
        // command_phase is emitted by polling/diagnostics and can arrive every few seconds.
        // Avoid reloading the whole canvas here; node_status/task events already update live state.
        bbPanelRef.current?.refresh();
      } else if (ev === "org:task_cancelled") {
        bbPanelRef.current?.refresh();
      } else if (ev === "org:quota_exhausted") {
        showToast(t("org.editor.quotaExhausted"), "error");
      } else if (ev === "org:watchdog_recovery") {
        showToast(t("org.editor.watchdogRecovery", { name: (d as any).node_id }), "error");
      } else if (ev === "org:broadcast") {
        triggerEdgeAnimation((d as any).from_node, (d as any).from_node, "#a78bfa");
      } else if (ev === "org:meeting_started" || ev === "org:meeting_completed") {
        bbPanelRef.current?.refresh();
      }
    });
  }, [visible, currentOrgId, setNodes, triggerEdgeAnimation, showToast, fetchOrg]);

  // ── Start/Stop org ──
  const handleStartOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/start`, { method: "POST" });
      setCurrentOrg({ ...currentOrg, status: "active" });
      setOrgList((prev) => prev.map((o) => o.id === currentOrg.id ? { ...o, status: "active" } : o));
      const mode = (currentOrg as any).operation_mode || "command";
      showToast(
        mode === "autonomous"
          ? t("org.editor.orgStartedAutonomous")
          : t("org.editor.orgStartedCommand"),
        "ok",
      );
    } catch (e: any) {
      console.error("Failed to start org:", e);
      showToast(t("org.editor.startFailed", { error: e?.message || e }), "error");
    }
  }, [currentOrg, apiBaseUrl, showToast]);

  // 防抖：避免双击/连点导致同一组织连续两次 /stop（旧版本会让后端抛
  // "无效状态转换: dormant -> dormant"）。
  const stoppingRef = useRef(false);
  const handleStopOrg = useCallback(async () => {
    if (!currentOrg || stoppingRef.current) return;
    stoppingRef.current = true;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/stop`, { method: "POST" });
      setCurrentOrg((prev) => prev ? { ...prev, status: "dormant" } : prev);
      setOrgList((prev) => prev.map((o) => o.id === currentOrg.id ? { ...o, status: "dormant" } : o));
      // 不再依赖 WS：HTTP 成功立即把所有节点视觉状态清零，避免装机版 WS 被
      // CSP 拦掉时残留 CPO/PM 等"执行中"状态。
      setNodes((prev) => prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          status: "idle",
          current_task: null,
          _runtime: null,
        },
      })));
    } catch (e: any) {
      console.error("Failed to stop org:", e);
      showToast(t("org.editor.stopFailed", { error: e?.message || e }), "error");
    } finally {
      // 800ms 内拒绝二次触发；正常 stop 链路远小于此阈值。
      setTimeout(() => { stoppingRef.current = false; }, 800);
    }
  }, [currentOrg, apiBaseUrl, showToast, setNodes]);

  // ── Org export/import ──
  const orgImportRef = useRef<HTMLInputElement>(null);

  const handleExportOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      const safeName = currentOrg.name.replace(/\s+/g, "_").replace(/[/\\]/g, "_").slice(0, 30);
      const defaultName = `${safeName}.json`;

      if (IS_TAURI) {
        const savePath = await saveFileDialog({
          title: t("org.editor.exportConfig"),
          defaultPath: defaultName,
          filters: [{ name: "JSON", extensions: ["json"] }],
        });
        if (!savePath) return;
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/export`, { method: "POST" });
        const data = await res.json();
        await writeTextFile(savePath, JSON.stringify(data, null, 2));
        showToast(t("org.editor.exportedTo", { path: savePath }));
      } else {
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/export`, { method: "POST" });
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = defaultName;
        a.click();
        URL.revokeObjectURL(url);
        showToast(t("org.editor.exportedAs", { name: currentOrg.name, file: defaultName }));
      }
    } catch (e) { showToast(String(e), "error"); }
  }, [currentOrg, apiBaseUrl, showToast]);

  const handleImportOrg = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/import`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      showToast(data.message || t("org.editor.importSuccess", { name: data.organization?.name || "" }));
      fetchOrgList();
      if (data.organization?.id) {
        setSelectedOrgId(data.organization.id);
      }
    } catch (err) { showToast(String(err), "error"); }
    if (orgImportRef.current) orgImportRef.current.value = "";
  }, [apiBaseUrl, showToast, fetchOrgList]);

  const [confirmReset, setConfirmReset] = useState(false);
  const handleResetOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/reset`, { method: "POST" });
      const data = await res.json();
      setCurrentOrg(data);
      setLayoutLocked(false);
      bbPanelRef.current?.refresh();
      setOrgStats(null);
      showToast(t("org.editor.orgReset"));
    } catch (e) { console.error("Failed to reset org:", e); }
    setConfirmReset(false);
  }, [currentOrg, apiBaseUrl]);

  // ── Save ──

  const buildSavePayload = useCallback(() => {
    if (!currentOrg) return null;
    const updatedNodes = nodes.map((n) => {
      const { status, _runtime, _liveMode, current_task, ...configData } = n.data as any;
      return { ...configData, position: n.position };
    });
    const updatedEdges = edges.map((e) => ({
      ...(e.data || {}),
      id: e.id,
      source: e.source,
      target: e.target,
      edge_type: (e.data as any)?.edge_type || "hierarchy",
      label: (e.data as any)?.label || (e.label as string) || "",
      bidirectional: (e.data as any)?.bidirectional ?? true,
      priority: (e.data as any)?.priority ?? 0,
      bandwidth_limit: (e.data as any)?.bandwidth_limit ?? 60,
    }));
    return {
      name: currentOrg.name,
      description: currentOrg.description,
      user_persona: currentOrg.user_persona || { title: t("org.editor.defaultPersonaTitle"), display_name: "", description: "" },
      operation_mode: (currentOrg as any).operation_mode || "command",
      core_business: currentOrg.core_business || "",
      layout_locked: layoutLocked,
      workspace_dir: (currentOrg as any).workspace_dir || "",
      auto_persist_final_answer:
        (currentOrg as any).auto_persist_final_answer !== false,
      watchdog_enabled: (currentOrg as any).watchdog_enabled === true,
      watchdog_interval_s: Number((currentOrg as any).watchdog_interval_s || 30),
      watchdog_stuck_threshold_s: Number((currentOrg as any).watchdog_stuck_threshold_s || 1800),
      watchdog_silence_threshold_s: Number((currentOrg as any).watchdog_silence_threshold_s || 1800),
      heartbeat_enabled: currentOrg.heartbeat_enabled,
      heartbeat_interval_s: currentOrg.heartbeat_interval_s,
      standup_enabled: currentOrg.standup_enabled,
      nodes: updatedNodes,
      edges: updatedEdges,
    };
  }, [currentOrg, nodes, edges, layoutLocked]);

  const doSave = useCallback(async (): Promise<boolean> => {
    if (!currentOrg) return false;
    const payload = buildSavePayload();
    if (!payload) return false;

    // 工作台节点必须是叶子节点：与后端 OrgManager.update 的校验同步，
    // 在 PUT 之前先做一次前端校验，避免 400 才提示。
    try {
      const nodes: Array<{ id: string; role_title?: string; plugin_origin?: unknown }> =
        (payload as any).nodes || [];
      const edges: Array<{ source: string; target: string; edge_type?: string }> =
        (payload as any).edges || [];
      const workbenchIds = new Set(
        nodes.filter((n) => !!n.plugin_origin).map((n) => n.id),
      );
      const offending: string[] = [];
      for (const e of edges) {
        if ((e.edge_type || "hierarchy") === "hierarchy" && workbenchIds.has(e.source)) {
          const n = nodes.find((x) => x.id === e.source);
          offending.push((n?.role_title || e.source) as string);
        }
      }
      if (offending.length) {
        const msg =
          t("org.editor.workbenchMustBeLeaf", "工作台节点必须是叶子节点") +
          "：" +
          Array.from(new Set(offending)).join("、");
        showToast(msg, "error");
        setSaveStatus("error");
        return false;
      }
    } catch (validationErr) {
      console.debug("workbench leaf validation skipped", validationErr);
    }

    const snapshot = JSON.stringify(payload);
    if (snapshot === lastSavedRef.current) return true;
    setSaveStatus("saving");
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: snapshot,
      });
      if (!resp.ok) throw new Error(t("org.editor.saveFailed2", { status: resp.status }));
      lastSavedRef.current = snapshot;
      setSaveStatus("saved");
      fetchOrgList();
      return true;
    } catch (e: any) {
      console.error("Failed to save org:", e);
      setSaveStatus("error");
      showToast(e.message || t("org.editor.autoSaveFailed"), "error");
      return false;
    }
  }, [currentOrg, buildSavePayload, apiBaseUrl, fetchOrgList, showToast, t]);

  const doSaveRef = useRef(doSave);
  doSaveRef.current = doSave;

  const flushSave = useCallback(() => {
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    doSaveRef.current();
  }, []);

  useEffect(() => {
    if (!currentOrg) return;
    const payload = buildSavePayload();
    if (!payload) return;
    const snap = JSON.stringify(payload);
    if (!lastSavedRef.current) { lastSavedRef.current = snap; return; }
    if (snap === lastSavedRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => { doSaveRef.current(); }, 1500);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [currentOrg, buildSavePayload]);

  useEffect(() => {
    if (saveStatus !== "saved") return;
    const t = setTimeout(() => setSaveStatus("idle"), 2000);
    return () => clearTimeout(t);
  }, [saveStatus]);

  // ── Create org ──

  const handleCreateOrg = useCallback(async () => {
    if (orgCreateBusyRef.current) return;
    orgCreateBusyRef.current = true;
    setCreatingOrg(true);
    const defaultName = t("org.editor.newOrg");
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: defaultName, description: "" }),
      });
      const data = (await res.json()) as { id?: string };
      const list = await fetchOrgList();
      let newId = typeof data?.id === "string" && data.id ? data.id : "";
      if (!newId && list.length > 0) {
        const byName = list.filter((o) => o.name === defaultName);
        const pool = byName.length > 0 ? byName : list;
        pool.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
        newId = pool[0]?.id ?? "";
      }
      if (newId) setSelectedOrgId(newId);
      showToast(newId ? t("org.editor.createdOrg") : t("org.editor.createdButNotFound"), newId ? "ok" : "error");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("Failed to create org:", e);
      showToast(msg.includes("401") || msg.includes("Authentication") ? t("org.editor.createNeedAuth") : t("org.editor.createFailed", { error: msg }), "error");
    } finally {
      orgCreateBusyRef.current = false;
      setCreatingOrg(false);
    }
  }, [apiBaseUrl, fetchOrgList, showToast]);

  const handleCreateFromTemplate = useCallback(async (templateId: string) => {
    if (orgCreateBusyRef.current) return;
    orgCreateBusyRef.current = true;
    setCreatingOrg(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/from-template`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: templateId }),
      });
      const data = (await res.json()) as { id?: string; name?: string };
      const list = await fetchOrgList();
      let newId = typeof data?.id === "string" && data.id ? data.id : "";
      if (!newId && list.length > 0) {
        const sorted = [...list].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
        newId = sorted[0]?.id ?? "";
      }
      if (newId) setSelectedOrgId(newId);
      setShowTemplates(false);
      showToast(newId ? t("org.editor.createdFromTemplate") : t("org.editor.createdButNotFound"), newId ? "ok" : "error");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("Failed to create from template:", e);
      showToast(t("org.editor.createFromTemplateFailed", { error: msg }), "error");
    } finally {
      orgCreateBusyRef.current = false;
      setCreatingOrg(false);
    }
  }, [apiBaseUrl, fetchOrgList, showToast, t]);

  const [confirmDeleteOrgId, setConfirmDeleteOrgId] = useState<string | null>(null);

  const handleDeleteOrg = useCallback(async (orgId: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}`, { method: "DELETE" });
      if (selectedOrgId === orgId) {
        if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
        setActiveDrawer(null);
        setSelectedOrgId(null);
        setCurrentOrg(null);
        setNodes([]);
        setEdges([]);
      }
      fetchOrgList();
    } catch (e) {
      console.error("Failed to delete org:", e);
    } finally {
      setConfirmDeleteOrgId(null);
    }
  }, [apiBaseUrl, selectedOrgId, fetchOrgList, setNodes, setEdges]);

  // ── Node management ──

  const handleAddNode = useCallback(() => {
    if (!currentOrg || !newNodeTitle.trim()) return;
    const newId = `node_${Date.now().toString(36)}`;
    setNodes((prev) => {
      const newNode: OrgNodeData = {
        id: newId,
        role_title: newNodeTitle.trim(),
        role_goal: "",
        role_backstory: "",
        agent_source: "local",
        agent_profile_id: null,
        position: getNextNodePosition(prev),
        level: 0,
        department: newNodeDept.trim(),
        custom_prompt: "",
        identity_dir: null,
        mcp_servers: [],
        skills: [],
        skills_mode: "all",
        preferred_endpoint: null,
        endpoint_policy: "prefer",
        max_concurrent_tasks: 1,
        timeout_s: 300,
        can_delegate: true,
        can_escalate: true,
        can_request_scaling: true,
        is_clone: false,
        clone_source: null,
        external_tools: [],
        enable_file_tools: true,
        plugin_origin: null,
        ephemeral: false,
        frozen_by: null,
        frozen_reason: null,
        frozen_at: null,
        avatar: null,
        status: "idle",
      };
      return [...prev, orgNodeToFlowNode(newNode, { _liveMode: liveMode })];
    });
    setSelectedNodeId(newId);
    setNewNodeTitle("");
    setNewNodeDept("");
    setShowNewNodeForm(false);
  }, [currentOrg, newNodeTitle, newNodeDept, setNodes]);

  const handleDeleteNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((prev) => prev.filter((n) => n.id !== selectedNodeId));
    setEdges((prev) => prev.filter((e) => e.source !== selectedNodeId && e.target !== selectedNodeId));
    setSelectedNodeId(null);
  }, [selectedNodeId, setNodes, setEdges]);

  /**
   * Add a workbench (plugin-backed) node to the canvas.
   *
   * Builds an OrgNodeData from the picked template's ``suggested_node``,
   * keeping it as a leaf node (the only valid topology — see backend
   * OrgManager.update + OrgRuntime._create_node_agent). The node's
   * external_tools is pre-bound to the workbench's tool names; the
   * plugin_origin field flags it for UI badges, read-only capability tab,
   * and runtime prompt injection.
   */
  const handleAddWorkbenchNode = useCallback((tpl: WorkbenchTemplate) => {
    if (!currentOrg) return;
    const suggested = tpl.suggested_node || {};
    const newId = `wb_${tpl.plugin_id.replace(/[^a-zA-Z0-9_]/g, "_")}_${Date.now().toString(36)}`;
    setNodes((prev) => {
      const newNode: OrgNodeData = {
        id: newId,
        role_title: suggested.role_title || tpl.name || tpl.plugin_id,
        role_goal: suggested.role_goal || "",
        role_backstory: "",
        agent_source: "local",
        agent_profile_id: suggested.agent_profile_id ?? null,
        position: getNextNodePosition(prev),
        level: 0,
        department: tpl.category || "",
        custom_prompt: suggested.custom_prompt || "",
        identity_dir: null,
        mcp_servers: suggested.mcp_servers || [],
        skills: suggested.skills || [],
        skills_mode: suggested.skills_mode || "all",
        preferred_endpoint: null,
        endpoint_policy: "prefer",
        max_concurrent_tasks: suggested.max_concurrent_tasks ?? 1,
        timeout_s: 0,
        can_delegate: suggested.can_delegate ?? false,
        can_escalate: suggested.can_escalate ?? true,
        can_request_scaling: false,
        is_clone: false,
        clone_source: null,
        external_tools: [...(suggested.external_tools || [])],
        enable_file_tools: suggested.enable_file_tools ?? false,
        plugin_origin: suggested.plugin_origin || {
          plugin_id: tpl.plugin_id,
          template_id: tpl.id,
          version: tpl.version,
        },
        ephemeral: false,
        frozen_by: null,
        frozen_reason: null,
        frozen_at: null,
        avatar: null,
        status: "idle",
      };
      return [...prev, orgNodeToFlowNode(newNode, { _liveMode: liveMode })];
    });
    setSelectedNodeId(newId);
    setPropsTab("capabilities");
    setShowRightPanel(true);
  }, [currentOrg, liveMode, setNodes]);

  const toggleLayoutLock = useCallback(() => {
    if (liveMode) {
      showToast(t("org.editor.runningLocked"), "error");
      return;
    }
    const nextLocked = !layoutLocked;
    setLayoutLocked(nextLocked);
    showToast(
      nextLocked
        ? t("org.editor.layoutLockedToast", "布局已锁定，节点位置不会被拖动修改")
        : t("org.editor.layoutUnlockedToast", "布局已解锁，可以继续拖动节点"),
    );
  }, [layoutLocked, liveMode, showToast, t]);

  const applyAutoLayout = useCallback(() => {
    if (isCanvasLocked) {
      showToast(
        liveMode
          ? t("org.editor.runningLocked")
          : t("org.editor.layoutLockedAutoLayoutHint", "布局已锁定，请先解锁后再自动布局"),
        "error",
      );
      return;
    }
    setNodes((prev) => computeTreeLayout(prev, edges));
  }, [edges, isCanvasLocked, liveMode, setNodes, showToast, t]);

  // ── Edge connection ──

  const onConnect: OnConnect = useCallback(
    (params: Connection) => {
      const edgeId = `edge_${Date.now().toString(36)}`;
      const newEdge: Edge = {
        id: edgeId,
        source: params.source!,
        target: params.target!,
        type: "default",
        style: { stroke: EDGE_COLORS.hierarchy, strokeWidth: 2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS.hierarchy },
        data: {
          id: edgeId,
          source: params.source,
          target: params.target,
          edge_type: "hierarchy",
          label: "",
          bidirectional: true,
          priority: 0,
          bandwidth_limit: 60,
        },
      };
      setEdges((prev) => addEdge(newEdge, prev));
    },
    [setEdges],
  );

  // ── Node click ──

  const onNodeClick = useCallback((_: any, node: Node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    setPropsTab("overview");
    setFullPromptPreview(null);
    setShowRightPanel(true);
    setShowNodeChat(false);
  }, []);

  const onEdgeClick = useCallback((_: any, edge: Edge) => {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
    setShowRightPanel(true);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setShowRightPanel(false);
    setContextMenu(null);
  }, []);

  const onNodeDragStop = useCallback(() => {}, []);

  // ── Fetch org stats in live mode ──
  useEffect(() => {
    if (!visible || !currentOrg || !liveMode) {
      if (!currentOrg || !liveMode) setOrgStats(null);
      return;
    }
    const fetchStats = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/stats`);
        if (res.ok) setOrgStats(await res.json());
      } catch (e) { /* ignore */ }
    };
    fetchStats();
    const interval = setInterval(fetchStats, 8000);
    return () => clearInterval(interval);
  }, [visible, currentOrg, liveMode, apiBaseUrl]);


  // ── Inject runtime metrics into nodes from orgStats ──
  useEffect(() => {
    if (!orgStats?.per_node || !orgStats?.anomalies) return;
    const nodeMap = new Map<string, any>();
    for (const nd of orgStats.per_node) nodeMap.set(nd.id, nd);
    const anomalyMap = new Map<string, string>();
    for (const a of orgStats.anomalies) anomalyMap.set(a.node_id, a.message);
    setNodes((prev) =>
      prev.map((n) => {
        const rt = nodeMap.get(n.id);
        if (!rt) return n;
        const patch: Record<string, any> = {
          _runtime: {
            idle_seconds: rt.idle_seconds,
            pending_messages: rt.pending_messages,
            anomaly: anomalyMap.get(n.id) || null,
            plan_progress: rt.plan_progress,
            delegated_summary: rt.delegated_summary,
            external_tools: rt.external_tools,
            running_since: rt.running_since,
            recent_activity_ts: rt.recent_activity_ts,
            last_watchdog_action: rt.last_watchdog_action,
          },
        };
        if (rt.status && rt.status !== n.data.status) {
          patch.status = rt.status;
        }
        return { ...n, data: { ...n.data, ...patch } };
      }),
    );
  }, [orgStats, setNodes]);

  // ── Selected node data ──

  const nodeNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const n of nodes) {
      const title = (n.data as any)?.role_title;
      if (title) map[n.id] = title;
    }
    return map;
  }, [nodes]);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) return null;
    const n = nodes.find((n) => n.id === selectedNodeId);
    return n ? (n.data as unknown as OrgNodeData) : null;
  }, [selectedNodeId, nodes]);

  const updateNodeData = useCallback((field: string, value: any) => {
    if (!selectedNodeId) return;
    setNodes((prev) =>
      prev.map((n) =>
        n.id === selectedNodeId ? { ...n, data: { ...n.data, [field]: value } } : n,
      ),
    );
  }, [selectedNodeId, setNodes]);

  // ── Selected edge data ──

  const selectedEdge = useMemo(() => {
    if (!selectedEdgeId) return null;
    const e = edges.find((e) => e.id === selectedEdgeId);
    if (!e) return null;
    return { ...((e.data as any) || {}), source: e.source, target: e.target, _id: e.id };
  }, [selectedEdgeId, edges]);

  // 渲染用 edges：把 edge 动画 / 流量计数合并到 edge 对象。
  // 必须 useMemo —— 否则拖拽时 useNodesState 每帧刷新 nodes 会让父组件重渲，
  // 内联 edges.map(...) 会重建所有 edge 引用，ReactFlow 按引用 diff 时
  // 会判定"全部边都变了"并重画所有 SVG / marker，导致拖拽和动画一顿一顿。
  // 拖拽只改 nodes，不在本 memo 依赖里，所以拖拽期间 ReactFlow 收到的是同一引用。
  const flowEdges = useMemo(() => {
    return edges.map((e) => {
      const anim = edgeAnimations[e.id];
      const flowCount = liveMode ? edgeFlowCounts[e.id] : undefined;
      const base = flowCount && flowCount > 0
        ? { ...e, label: `${(e.data as any)?.label || ""} ${flowCount > 0 ? `(${flowCount})` : ""}`.trim() || undefined }
        : e;
      if (!anim) return base;
      return {
        ...base,
        animated: true,
        style: { ...base.style, stroke: anim.color, strokeWidth: 3, filter: `drop-shadow(0 0 4px ${anim.color})` },
        markerEnd: { ...(base.markerEnd as any), color: anim.color },
      };
    });
  }, [edges, edgeAnimations, edgeFlowCounts, liveMode]);

  const updateEdgeData = useCallback((field: string, value: any) => {
    if (!selectedEdgeId) return;
    setEdges((prev) =>
      prev.map((e) => {
        if (e.id !== selectedEdgeId) return e;
        const newData = { ...e.data, [field]: value };
        const edgeType = field === "edge_type" ? value : (e.data as any)?.edge_type;
        return {
          ...e,
          data: newData,
          style: { stroke: EDGE_COLORS[edgeType] || "var(--muted)", strokeWidth: edgeType === "hierarchy" ? 2 : 1.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS[edgeType] || "var(--muted)" },
          animated: edgeType === "collaborate",
          label: field === "label" ? value : (e.data as any)?.label || undefined,
        };
      }),
    );
  }, [selectedEdgeId, setEdges]);

  const handleDeleteEdge = useCallback(() => {
    if (!selectedEdgeId) return;
    setEdges((prev) => prev.filter((e) => e.id !== selectedEdgeId));
    setSelectedEdgeId(null);
  }, [selectedEdgeId, setEdges]);

  const ctxCopyNode = useCallback((nodeId: string) => {
    const n = nodes.find((n) => n.id === nodeId);
    if (n) setClipboardNode(structuredClone(n));
    setContextMenu(null);
  }, [nodes]);

  const ctxDeleteNode = useCallback((nodeId: string) => {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId));
    setEdges((prev) => prev.filter((e) => e.source !== nodeId && e.target !== nodeId));
    if (selectedNodeId === nodeId) setSelectedNodeId(null);
    setContextMenu(null);
  }, [selectedNodeId, setNodes, setEdges]);

  const ctxUnfreezeNode = useCallback(async (nodeId: string) => {
    setContextMenu(null);
    if (!selectedOrgId) return;
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${selectedOrgId}/nodes/${nodeId}/unfreeze`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      setNodes((prev) => prev.map((n) => {
        if (n.id !== nodeId) return n;
        return { ...n, data: { ...n.data, status: "idle", frozen_by: null, frozen_reason: null, frozen_at: null } };
      }));
      showToast(t("org.editor.nodeUnfrozen"));
    } catch (e) {
      showToast(t("org.editor.unfreezeFailed", { error: String(e) }), "error");
    }
  }, [selectedOrgId, apiBaseUrl, setNodes, showToast]);

  const ctxDeleteEdge = useCallback((edgeId: string) => {
    setEdges((prev) => prev.filter((e) => e.id !== edgeId));
    if (selectedEdgeId === edgeId) setSelectedEdgeId(null);
    setContextMenu(null);
  }, [selectedEdgeId, setEdges]);

  const ctxReverseEdge = useCallback((edgeId: string) => {
    setEdges((prev) => prev.map((e) => {
      if (e.id !== edgeId) return e;
      return { ...e, source: e.target, target: e.source };
    }));
    setContextMenu(null);
  }, [setEdges]);

  const ctxPasteNode = useCallback(() => {
    if (!clipboardNode) return;
    const offset = 60;
    const newId = `node_${Date.now().toString(36)}`;
    const pasted = {
      ...structuredClone(clipboardNode),
      id: newId,
      position: { x: (clipboardNode.position?.x ?? 200) + offset, y: (clipboardNode.position?.y ?? 200) + offset },
      data: { ...clipboardNode.data, id: newId, role_title: `${clipboardNode.data?.role_title || t("org.editor.newNode")} (${t("org.editor.clone")})`, _liveMode: liveMode },
      selected: false,
    };
    setNodes((prev) => [...prev, pasted]);
    setContextMenu(null);
  }, [clipboardNode, setNodes, liveMode]);

  const ctxAddNodeAt = useCallback(() => {
    const newId = `node_${Date.now().toString(36)}`;
    const hasPanePosition = contextMenu?.type === "pane"
      && typeof contextMenu.flowX === "number"
      && typeof contextMenu.flowY === "number";
    const pos = hasPanePosition
      ? { x: contextMenu.flowX!, y: contextMenu.flowY! }
      : getNextNodePosition(nodes);
    const newNode: OrgNodeData = {
      id: newId, role_title: t("org.editor.newNode"), role_goal: "", role_backstory: "",
      agent_source: "local", agent_profile_id: null, position: pos, level: 0,
      department: "", custom_prompt: "", identity_dir: null, mcp_servers: [], skills: [],
      skills_mode: "all", preferred_endpoint: null, endpoint_policy: "prefer", max_concurrent_tasks: 1, timeout_s: 0,
      can_delegate: true, can_escalate: true, can_request_scaling: true, is_clone: false,
      clone_source: null, external_tools: [], enable_file_tools: true, plugin_origin: null,
      ephemeral: false, frozen_by: null,
      frozen_reason: null, frozen_at: null, avatar: null, status: "idle",
    };
    setNodes((prev) => [...prev, orgNodeToFlowNode(newNode, { _liveMode: liveMode })]);
    setSelectedNodeId(newId);
    setContextMenu(null);
  }, [nodes, contextMenu, setNodes, liveMode]);

  // ── Render ──

  return (
    <div style={{ display: visible ? "flex" : "none", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* ── Toolbar - 3-section layout ── */}
      {currentOrg && (
        <div className="org-topbar">
          {/* ── Left: Org info ── */}
          <div className="org-topbar-left">
            <button
              className="org-tb-btn"
              onClick={() => setShowLeftPanel(!showLeftPanel)}
              title={t("org.editor.orgList")}
            >
              <IconMenu size={14} />
            </button>
            {!isMobile && (
              editingName ? (
                <input
                  ref={nameInputRef}
                  className="org-topbar-name org-topbar-name--editing"
                  value={currentOrg.name}
                  onChange={(e) => setCurrentOrg({ ...currentOrg, name: e.target.value })}
                  onBlur={() => setEditingName(false)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === "Escape") { setEditingName(false); e.currentTarget.blur(); } }}
                  autoFocus
                />
              ) : (
                <span
                  className="org-topbar-name"
                  onClick={() => setEditingName(true)}
                  title={t("org.editor.clickToRename")}
                >
                  {currentOrg.name || t("org.editor.unnamedOrg")}
                </span>
              )
            )}
            <TooltipProvider>
              <div
                className="org-topbar-status"
                style={{
                  borderColor: `${STATUS_COLORS[currentOrg.status] || "var(--muted)"}40`,
                  color: STATUS_COLORS[currentOrg.status] || "var(--muted)",
                }}
              >
                <span className="org-status-dot" style={{
                  background: STATUS_COLORS[currentOrg.status] || "var(--muted)",
                  animation: liveMode ? "orgDotPulse 1.5s ease-in-out infinite" : undefined,
                }} />
                <span className="org-status-label">{ORG_STATUS_LABELS[currentOrg.status] ? t(ORG_STATUS_LABELS[currentOrg.status]) : currentOrg.status}</span>
                {liveMode && orgStats && !isMobile && (
                  <>
                    <span className="org-status-sep" />
                    <Tooltip><TooltipTrigger asChild>
                      <span className="org-status-stat"><IconClipboard size={11} /> {orgStats.total_tasks_completed ?? 0}</span>
                    </TooltipTrigger><TooltipContent>{t("org.editor.completedTasks")}</TooltipContent></Tooltip>
                    <Tooltip><TooltipTrigger asChild>
                      <span className="org-status-stat"><IconMessageCircle size={11} /> {orgStats.total_messages_exchanged ?? 0}</span>
                    </TooltipTrigger><TooltipContent>{t("org.editor.messageExchanges")}</TooltipContent></Tooltip>
                    {orgStats.pending_messages > 0 && (
                      <Tooltip><TooltipTrigger asChild>
                        <span
                          className="org-status-stat"
                          style={{ color: "#f59e0b", cursor: "pointer" }}
                          onClick={() => { setSelectedNodeId(null); setSelectedEdgeId(null); setShowRightPanel(true); setPropsTab("overview"); }}
                        >▪ {orgStats.pending_messages}</span>
                      </TooltipTrigger><TooltipContent>{t("org.editor.pendingMessages")}</TooltipContent></Tooltip>
                    )}
                    {orgStats.anomalies?.length > 0 && (
                      <Tooltip><TooltipTrigger asChild>
                        <span
                          className="org-status-stat"
                          style={{ color: "#ef4444", fontWeight: 600, cursor: "pointer" }}
                          onClick={() => { setSelectedNodeId(null); setSelectedEdgeId(null); setShowRightPanel(true); setPropsTab("overview"); }}
                        >! {orgStats.anomalies.length}</span>
                      </TooltipTrigger><TooltipContent>{t("org.editor.viewErrors")}</TooltipContent></Tooltip>
                    )}
                  </>
                )}
              </div>
            </TooltipProvider>
          </div>

          {/* ── Center: View tabs ── */}
          <div className="org-topbar-center">
            <ToggleGroup
              type="single"
              value={viewMode}
              onValueChange={(v) => { if (v && v !== viewMode) { flushSave(); setViewMode(v as typeof viewMode); } }}
              variant="outline"
              className="org-topbar-tabs flex-shrink-0"
            >
              <ToggleGroupItem value="canvas" className="text-xs h-7 px-3 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">{t("org.editor.tabOrchestration")}</ToggleGroupItem>
              <ToggleGroupItem value="projects" className="text-xs h-7 px-3 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">{t("org.editor.tabProjects")}</ToggleGroupItem>
              <ToggleGroupItem value="dashboard" className="text-xs h-7 px-3 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">{t("org.editor.tabKanban")}</ToggleGroupItem>
            </ToggleGroup>
          </div>

          {/* ── Right: Actions ── */}
          <div className="org-topbar-right">
            {currentOrg.status === "archived" ? (
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{t("org.editor.archived")}</span>
            ) : currentOrg.status === "dormant" ? (
              <button className="org-tb-btn org-tb-btn--ok" onClick={handleStartOrg} title={t("org.editor.startOrg")}>
                <IconPlay size={13} /> {!isMobile && t("org.editor.start")}
              </button>
            ) : (<>
              <button className="org-tb-btn org-tb-btn--danger" onClick={handleStopOrg} title={t("org.editor.stopOrg")}>
                <IconStop size={13} /> {!isMobile && t("org.editor.stop")}
              </button>
            </>)}
            <button
              className={`org-tb-btn${(showRightPanel && !selectedNode && !selectedEdge) ? " org-tb-btn--active" : ""}`}
              onClick={() => { setShowRightPanel(!showRightPanel); setSelectedNodeId(null); setSelectedEdgeId(null); }}
              title={t("org.editor.orgSettings")}
            >
              <IconGear size={13} />
            </button>
            <button
              className={`org-tb-btn${inboxOpen ? " org-tb-btn--active" : ""}`}
              onClick={() => setActiveDrawer(inboxOpen ? null : "inbox")}
              style={{ position: "relative" }}
            >
              <IconMessageCircle size={13} />
              {(orgStats?.unread_inbox > 0 || orgStats?.pending_approvals > 0) && (
                <span className="org-notif-dot" />
              )}
            </button>
          </div>
        </div>
      )}

      {/* ── Content area: Left + Canvas + Right ── */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden", position: "relative" }}>

      {/* ── Welcome: two-card layout when no org selected ── */}
      {!currentOrg && (
        <div style={{ flex: 1, display: "flex", padding: 16, gap: 16, overflow: "hidden" }}>
          {/* Left card: org list */}
          <div style={{
            width: 280, flexShrink: 0, display: "flex", flexDirection: "column",
            background: "var(--card-bg, #fff)", border: "1px solid var(--line)",
            borderRadius: 12, overflow: "hidden",
            boxShadow: "0 2px 12px rgba(0,0,0,0.06)",
          }}>
            <div style={{ padding: "12px 12px 8px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontWeight: 600, fontSize: 14 }}>{t("orgEditor.title")}</span>
              <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                <TooltipProvider delayDuration={300}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="link" size="sm" onClick={() => setShowTemplates(!showTemplates)} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">{t("org.editor.template")}</Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">{t("org.editor.createFromTemplate")}</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="link" size="sm" onClick={() => void handleCreateOrg()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">{t("org.editor.create")}</Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">{t("org.editor.createBlank")}</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="link" size="sm" onClick={() => orgImportRef.current?.click()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">{t("org.editor.import")}</Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">{t("org.editor.importFromFile")}</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            </div>
            {showTemplates && (
              <div style={{ padding: "0 8px 8px" }}>
                <div className="card" style={{ padding: 8, fontSize: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>{t("org.editor.createFromTemplateLong")}</div>
                  {templates.length === 0 && (
                    <div style={{ color: "var(--muted)", padding: "6px 8px" }}>{t("org.editor.emptyOrgHint")}</div>
                  )}
                  {templates.map((tpl) => (
                    <div key={tpl.id} onClick={() => handleCreateFromTemplate(tpl.id)}
                      style={{ padding: "6px 8px", borderRadius: "var(--radius-sm)", cursor: "pointer", display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}
                      className="navItem"
                    >
                      <span><IconBuilding size={14} /></span>
                      <div>
                        <div style={{ fontWeight: 500 }}>{tpl.name}</div>
                        <div style={{ fontSize: 10, color: "var(--muted)" }}>{t("org.editor.nodeCount", { count: tpl.node_count })}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div style={{ flex: 1, overflowY: "auto", padding: "0 8px 8px" }}>
              {orgList.length === 0 && (
                <div style={{ textAlign: "center", color: "var(--muted)", fontSize: 12, padding: 20 }}>
                  {t("org.editor.emptyOrgHint")}
                </div>
              )}
              {orgList.map((org) => (
                <div key={org.id}
                  onClick={() => { setSelectedOrgId(org.id); }}
                  className={`navItem ${selectedOrgId === org.id ? "navItemActive" : ""}`}
                  style={{ padding: "8px 10px", marginBottom: 4, borderRadius: "var(--radius-sm)", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between", position: "relative" }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, overflow: "hidden" }}>
                    <IconBuilding size={16} />
                    <div style={{ overflow: "hidden" }}>
                      <div style={{ fontWeight: 500, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{org.name}</div>
                      <div style={{ fontSize: 10, color: "var(--muted)" }}>{t("org.editor.nodeCount", { count: org.node_count })} · {ORG_STATUS_LABELS[org.status] ? t(ORG_STATUS_LABELS[org.status]) : org.status}</div>
                    </div>
                  </div>
                  <button className="btnSmall" onClick={(e) => { e.stopPropagation(); setConfirmDeleteOrgId(org.id); }} style={{ opacity: 0.5, fontSize: 10 }} title={t("org.editor.deleteOrg")}>
                    <IconTrash size={10} />
                  </button>
                  {confirmDeleteOrgId === org.id && (
                    <div style={{ position: "absolute", right: 0, top: "100%", zIndex: 10, background: "var(--card-bg, #fff)", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px", boxShadow: "0 4px 12px rgba(0,0,0,0.12)", display: "flex", gap: 6, alignItems: "center", fontSize: 11 }} onClick={(e) => e.stopPropagation()}>
                      <span>{t("org.editor.confirmDelete")}</span>
                      <button className="btnSmall" onClick={() => handleDeleteOrg(org.id)} style={{ color: "var(--danger)", fontSize: 11 }}>{t("org.editor.deleteBtn")}</button>
                      <button className="btnSmall" onClick={() => setConfirmDeleteOrgId(null)} style={{ fontSize: 11 }}>{t("org.editor.cancelBtn")}</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
          {/* Right card: tutorial / guide */}
          <div style={{
            flex: 1, minWidth: 0, minHeight: 0,
            display: "flex", flexDirection: "column",
            background: "var(--card-bg, #fff)", border: "1px solid var(--line)",
            borderRadius: 12, overflow: "hidden",
            boxShadow: "0 2px 12px rgba(0,0,0,0.06)",
          }}>
            <div style={{
              flex: 1, minHeight: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              padding: 24,
            }}>
              <img
                src={agentOrgImg}
                alt={t("orgEditor.title")}
                style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", borderRadius: 8 }}
              />
            </div>
            <div style={{ padding: "16px 20px", borderTop: "1px solid var(--line)", flexShrink: 0 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>{t("orgEditor.welcomeTitle")}</h3>
              <p style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.8 }}>
                {t("orgEditor.welcomeDesc")}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Left Panel: Org List (floating, only when org selected) ── */}
      {currentOrg && showLeftPanel && (
        <div
          onClick={() => setShowLeftPanel(false)}
          style={{
            position: "absolute", inset: 0, zIndex: 49,
          }}
        />
      )}
      {currentOrg && showLeftPanel && (
      <div
        style={{
          width: isMobile ? "80%" : 260,
          maxWidth: 320,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "var(--card-bg, #fff)",
          position: "absolute",
          zIndex: 50,
          top: 8,
          left: 8,
          bottom: 8,
          borderRadius: 12,
          border: "1px solid var(--line)",
          boxShadow: "0 8px 24px rgba(0,0,0,0.12), 0 2px 8px rgba(0,0,0,0.08)",
        }}
      >
        <div style={{ padding: "12px 12px 8px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>{t("orgEditor.title")}</span>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="link" size="sm" onClick={() => setShowTemplates(!showTemplates)} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">
                    {t("org.editor.template")}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t("org.editor.createFromTemplate")}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="link" size="sm" onClick={() => void handleCreateOrg()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">
                    {t("org.editor.create")}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t("org.editor.createBlank")}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="link" size="sm" onClick={() => orgImportRef.current?.click()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">
                    {t("org.editor.import")}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t("org.editor.importFromFile")}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <input
              ref={orgImportRef}
              type="file"
              accept=".json,.akita-org"
              style={{ display: "none" }}
              onChange={handleImportOrg}
            />
            <button className="btnSmall" onClick={() => setShowLeftPanel(false)} title={t("org.editor.close")} style={{ minWidth: 28, minHeight: 28, opacity: 0.5 }}>
              <IconX size={14} />
            </button>
          </div>
        </div>

        {/* Templates dropdown */}
        {showTemplates && (
          <div style={{ padding: "0 8px 8px" }}>
            <div className="card" style={{ padding: 8, fontSize: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>{t("org.editor.createFromTemplateLong")}</div>
              {templates.length === 0 && (
                <div style={{ color: "var(--muted)", padding: "6px 8px" }}>{t("org.editor.emptyOrgHint")}</div>
              )}
              {templates.map((tpl) => (
                <div
                  key={tpl.id}
                  onClick={() => handleCreateFromTemplate(tpl.id)}
                  style={{
                    padding: "6px 8px",
                    borderRadius: "var(--radius-sm)",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 2,
                  }}
                  className="navItem"
                >
                  <span><IconBuilding size={14} /></span>
                  <div>
                    <div style={{ fontWeight: 500 }}>{tpl.name}</div>
                    <div style={{ fontSize: 10, color: "var(--muted)" }}>{t("org.editor.nodeCount", { count: tpl.node_count })}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Org list */}
        <div style={{ flex: 1, overflowY: "auto", padding: "0 8px" }}>
          {orgList.length === 0 && (
            <div style={{ textAlign: "center", color: "var(--muted)", fontSize: 12, padding: 20 }}>
              {t("org.editor.emptyOrgHint")}
            </div>
          )}
          {orgList.map((org) => (
            <div
              key={org.id}
              onClick={() => { if (selectedOrgId && selectedOrgId !== org.id) flushSave(); setSelectedOrgId(org.id); setShowLeftPanel(false); }}
              className={`navItem ${selectedOrgId === org.id ? "navItemActive" : ""}`}
              style={{
                padding: "8px 10px",
                marginBottom: 4,
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                position: "relative",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, overflow: "hidden" }}>
                <IconBuilding size={16} />
                <div style={{ overflow: "hidden" }}>
                  <div style={{ fontWeight: 500, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {org.name}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--muted)" }}>
                    {t("org.editor.nodeCount", { count: org.node_count })} · {ORG_STATUS_LABELS[org.status] ? t(ORG_STATUS_LABELS[org.status]) : org.status}
                  </div>
                </div>
              </div>
              <button
                className="btnSmall"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmDeleteOrgId(org.id);
                }}
                style={{ opacity: 0.5, fontSize: 10 }}
                title={t("org.editor.deleteOrg")}
              >
                <IconTrash size={10} />
              </button>
              {confirmDeleteOrgId === org.id && (
                <div
                  style={{
                    position: "absolute", right: 0, top: "100%", zIndex: 10,
                    background: "var(--card-bg, #fff)", border: "1px solid var(--line)",
                    borderRadius: 8, padding: "8px 10px", boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
                    display: "flex", gap: 6, alignItems: "center", fontSize: 11,
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <span>{t("org.editor.confirmDelete")}</span>
                  <button className="btnSmall" onClick={() => handleDeleteOrg(org.id)} style={{ color: "var(--danger)", fontSize: 11 }}>{t("org.editor.deleteBtn")}</button>
                  <button className="btnSmall" onClick={() => setConfirmDeleteOrgId(null)} style={{ fontSize: 11 }}>{t("org.editor.cancelBtn")}</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      )}

      {/* ── Center: Canvas ── */}
      {currentOrg && (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Add node dialog */}
        {showNewNodeForm && createPortal(
          <div className="org-modal-overlay" onClick={() => setShowNewNodeForm(false)}>
            <div className="org-modal" onClick={e => e.stopPropagation()} style={{ width: 360 }}>
              <div className="org-modal-header">
                <span>{t("org.editor.addNodeTitle")}</span>
                <button className="org-modal-close" onClick={() => setShowNewNodeForm(false)}><IconX size={14} /></button>
              </div>
              <div className="org-modal-body">
                <label className="org-modal-label">{t("org.editor.roleName")}</label>
                <input
                  className="input"
                  placeholder={t("org.editor.roleNamePlaceholder")}
                  value={newNodeTitle}
                  onChange={(e) => setNewNodeTitle(e.target.value)}
                  style={{ width: "100%", fontSize: 13, marginBottom: 12 }}
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
                />
                <label className="org-modal-label">{t("org.editor.deptOptional")}</label>
                <input
                  className="input"
                  placeholder={t("org.editor.deptPlaceholder")}
                  value={newNodeDept}
                  onChange={(e) => setNewNodeDept(e.target.value)}
                  style={{ width: "100%", fontSize: 13 }}
                  onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
                />
              </div>
              <div className="org-modal-footer">
                <button className="org-modal-btn" onClick={() => setShowNewNodeForm(false)}>{t("org.editor.cancel")}</button>
                <button className="org-modal-btn org-modal-btn--primary" onClick={handleAddNode}>{t("org.editor.add")}</button>
              </div>
            </div>
          </div>,
          document.body
        )}

        {/* Workbench node picker — adds a leaf node pre-bound to a plugin's tools */}
        <WorkbenchNodePicker
          apiBaseUrl={apiBaseUrl}
          open={showWorkbenchPicker}
          onClose={() => setShowWorkbenchPicker(false)}
          onPick={handleAddWorkbenchNode}
        />

        {/* Main content: Canvas / Projects / Dashboard */}
        {currentOrg ? (
          <>
          {viewMode === "dashboard" ? (
            <div style={{ flex: 1, overflow: "hidden" }}>
              <OrgDashboard
                orgId={currentOrg.id}
                apiBaseUrl={apiBaseUrl}
                orgName={currentOrg.name}
                onNodeClick={(nodeId) => {
                  setViewMode("canvas");
                  const n = nodes.find(nd => nd.id === nodeId);
                  if (n) {
                    setSelectedNodeId(nodeId);
                    setSelectedEdgeId(null);
                    setShowRightPanel(true);
                    setPropsTab("overview");
                  }
                }}
              />
            </div>
          ) : viewMode === "projects" ? (
            <div style={{ flex: 1, overflow: "hidden" }}>
              {selectedOrgId ? (
                <OrgProjectBoard
                  orgId={selectedOrgId}
                  apiBaseUrl={apiBaseUrl}
                  nodes={nodes.map(n => ({ id: n.id, role_title: (n.data as any)?.role_title, avatar: (n.data as any)?.avatar }))}
                />
              ) : (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", height: "100%" }}>
                  {t("org.editor.selectOrgFirst")}
                </div>
              )}
            </div>
          ) : (
          <div style={{ flex: 1, position: "relative" }} onContextMenu={(e) => e.preventDefault()}>
            <ReactFlowProvider>
            <ReactFlow
              onInit={(instance) => {
                reactFlowRef.current = instance;
              }}
              nodes={nodes}
              edges={flowEdges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={onNodeClick}
              onEdgeClick={onEdgeClick}
              onPaneClick={onPaneClick}
              onNodeDragStop={onNodeDragStop}
              onNodeContextMenu={(e, node) => { e.preventDefault(); e.stopPropagation(); setSelectedNodeId(node.id); setSelectedEdgeId(null); setContextMenu({ x: e.clientX, y: e.clientY, type: "node", id: node.id }); }}
              onEdgeContextMenu={(e, edge) => { e.preventDefault(); e.stopPropagation(); setSelectedEdgeId(edge.id); setSelectedNodeId(null); setContextMenu({ x: e.clientX, y: e.clientY, type: "edge", id: edge.id }); }}
              onPaneContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                const flow = reactFlowRef.current?.screenToFlowPosition({ x: e.clientX, y: e.clientY });
                setContextMenu({
                  x: e.clientX,
                  y: e.clientY,
                  type: "pane",
                  id: null,
                  flowX: flow?.x,
                  flowY: flow?.y,
                });
              }}
              nodeTypes={nodeTypes}
              connectOnClick
              connectionLineComponent={OrgConnectionLine}
              connectionLineContainerStyle={{ zIndex: 20000, overflow: "visible" }}
              connectionLineStyle={{ stroke: "var(--primary)", strokeWidth: 2, strokeDasharray: "6 3" }}
              fitView
              snapToGrid
              snapGrid={[20, 20]}
              nodesDraggable={!isCanvasLocked}
              nodesConnectable
              defaultEdgeOptions={{
                type: "default",
                style: { strokeWidth: 2 },
              }}
              style={{ background: "var(--bg-app)" }}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={20} size={1} color="var(--line)" />
              <OrgCanvasControls />
              {/* Canvas-specific toolbar */}
              <Panel position="top-left">
                <div className="org-canvas-toolbar">
                  <button className="org-cvs-btn" onClick={() => setShowNewNodeForm(true)} title={t("org.editor.addNode")}>
                    <IconPlus size={13} /> {t("org.editor.addNode")}
                  </button>
                  <button
                    className="org-cvs-btn"
                    onClick={() => setShowWorkbenchPicker(true)}
                    title={t("org.editor.addWorkbenchNode", "添加工作台节点")}
                    disabled={liveMode}
                  >
                    <IconPlug size={13} /> {t("org.editor.addWorkbenchNode", "添加工作台节点")}
                  </button>
                  <button
                    className="org-cvs-btn"
                    title={isCanvasLocked ? t("org.editor.layoutLockedAutoLayoutHint", "布局已锁定，请先解锁后再自动布局") : t("org.editor.autoLayout")}
                    onClick={applyAutoLayout}
                    disabled={isCanvasLocked}
                  >
                    <IconSitemap size={13} /> {t("org.editor.autoLayout")}
                  </button>
                  <button
                    className={`org-cvs-btn${isCanvasLocked ? " org-cvs-btn--active" : ""}`}
                    onClick={toggleLayoutLock}
                    disabled={liveMode}
                    title={liveMode ? t("org.editor.runningLocked") : layoutLocked ? t("org.editor.unlockDrag") : t("org.editor.lockLayout")}
                  >
                    {layoutLocked ? <IconUnlock size={13} /> : <IconPin size={13} />}
                    {layoutLocked ? t("org.editor.unlockDrag") : t("org.editor.lockLayout")}
                  </button>
                  {selectedNodeId && (
                    <button className="org-cvs-btn org-cvs-btn--danger" onClick={handleDeleteNode} title={t("org.editor.deleteSelected")}>
                      <IconTrash size={13} />
                    </button>
                  )}
                </div>
                <div className="org-edge-legend">
                  {([
                    { type: "hierarchy", label: t("org.editor.hierarchyLegend"), dash: false },
                    { type: "collaborate", label: t("org.editor.collaborateLegend"), dash: true },
                    { type: "escalate", label: t("org.editor.escalateLegend"), dash: false },
                    { type: "consult", label: t("org.editor.consultLegend"), dash: false },
                  ] as const).map((e) => (
                    <span key={e.type} className="org-edge-legend-item">
                      <span
                        className="org-edge-legend-line"
                        style={{
                          background: e.dash ? "transparent" : EDGE_COLORS[e.type],
                          borderBottom: e.dash ? `2px dashed ${EDGE_COLORS[e.type]}` : undefined,
                        }}
                      />
                      {e.label}
                    </span>
                  ))}
                </div>
                <div className="org-connect-hint">
                  {t("org.editor.edgeHint")}
                </div>
              </Panel>
              {saveStatus !== "idle" && (
                <Panel position="bottom-center">
                  <div className={`org-save-indicator org-save-indicator--${saveStatus}`}>
                    {saveStatus === "saving" ? t("org.editor.saving") : saveStatus === "saved" ? <><IconCheck size={12} /> {t("org.editor.autoSaved")}</> : <span onClick={() => doSaveRef.current()} style={{ cursor: "pointer" }}>{t("org.editor.saveFailed")}</span>}
                  </div>
                </Panel>
              )}
            </ReactFlow>
            </ReactFlowProvider>
            {/* ── Context menu (portal to body to avoid clipping) ── */}
            {contextMenu && createPortal(
              <div
                className="org-ctx-menu"
                style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y, zIndex: 99999 }}
                onClick={() => setContextMenu(null)}
                onContextMenu={(e) => e.preventDefault()}
              >
                {contextMenu.type === "node" && contextMenu.id && (<>
                  {liveMode && selectedOrgId && (
                    <button onClick={() => { setSelectedNodeId(contextMenu.id!); setSelectedEdgeId(null); setShowRightPanel(true); setShowNodeChat(true); setContextMenu(null); }}>
                      <span className="org-ctx-icon"><IconMessageCircle size={14} /></span>{t("org.editor.chatWithNode")}
                    </button>
                  )}
                  {liveMode && selectedOrgId && (nodes.find(n => n.id === contextMenu.id)?.data as any)?.status === "frozen" && (
                    <button onClick={() => ctxUnfreezeNode(contextMenu.id!)}>
                      <span className="org-ctx-icon"><IconUnlock size={14} /></span>{t("org.editor.unfreezeNode")}
                    </button>
                  )}
                  <button onClick={() => ctxCopyNode(contextMenu.id!)}>
                    <span className="org-ctx-icon"><IconClipboard size={14} /></span>{t("org.editor.copyNode")}
                  </button>
                  <button onClick={() => ctxDeleteNode(contextMenu.id!)}>
                    <span className="org-ctx-icon" style={{ color: "#e74c3c" }}><IconTrash size={14} /></span>{t("org.editor.deleteNode")}
                  </button>
                </>)}
                {contextMenu.type === "edge" && contextMenu.id && (<>
                  <button onClick={() => ctxReverseEdge(contextMenu.id!)}>
                    <span className="org-ctx-icon"><IconRefresh size={14} /></span>{t("org.editor.reverseEdge")}
                  </button>
                  <button onClick={() => ctxDeleteEdge(contextMenu.id!)}>
                    <span className="org-ctx-icon" style={{ color: "#e74c3c" }}><IconTrash size={14} /></span>{t("org.editor.deleteEdge")}
                  </button>
                </>)}
                {contextMenu.type === "pane" && (<>
                  <button onClick={() => ctxAddNodeAt()}>
                    <span className="org-ctx-icon"><IconPlus size={14} /></span>{t("org.editor.addNodeMenu")}
                  </button>
                  {clipboardNode && (
                    <button onClick={() => ctxPasteNode()}>
                      <span className="org-ctx-icon"><IconPin size={14} /></span>{t("org.editor.pasteNode")}
                    </button>
                  )}
                  <button
                    onClick={() => {
                      applyAutoLayout();
                      setContextMenu(null);
                    }}
                    disabled={isCanvasLocked}
                    title={isCanvasLocked ? t("org.editor.layoutLockedAutoLayoutHint", "布局已锁定，请先解锁后再自动布局") : t("org.editor.autoLayoutMenu")}
                  >
                    <span className="org-ctx-icon"><IconShuffle size={14} /></span>{t("org.editor.autoLayoutMenu")}
                  </button>
                  <button
                    onClick={() => {
                      toggleLayoutLock();
                      setContextMenu(null);
                    }}
                    disabled={liveMode}
                    title={liveMode ? t("org.editor.runningLocked") : layoutLocked ? t("org.editor.unlockDrag") : t("org.editor.lockLayout")}
                  >
                    <span className="org-ctx-icon">{layoutLocked ? <IconUnlock size={14} /> : <IconPin size={14} />}</span>
                    {layoutLocked ? t("org.editor.unlockDrag") : t("org.editor.lockLayout")}
                  </button>
                  <button
                    onClick={() => {
                      void reactFlowRef.current?.fitView({ padding: 0.2 });
                      setContextMenu(null);
                    }}
                  >
                    <span className="org-ctx-icon" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <Maximize className="size-3.5" strokeWidth={2} />
                    </span>
                    {t("org.editor.fitViewMenu")}
                  </button>
                </>)}
              </div>,
              document.body
            )}
            {/* ── Canvas bottom: live activity feed ── */}
            {liveMode && isCanvasLocked && orgStats && (() => {
              const perNode: any[] = orgStats.per_node || [];
              const recentTasks: any[] = orgStats.recent_tasks || [];
              const anomalies: any[] = orgStats.anomalies || [];
              const nodeLabel = (id: string) => {
                if (!id) return "";
                const nd = nodes.find(n => n.id === id);
                return (nd?.data as any)?.role_title || id?.slice(0, 6) || "";
              };
              const typeMeta: Record<string, { icon: string; label: string; tip: string; cls: string }> = {
                task_delegated:  { icon: "↗", label: t("org.dashboard.feedAssign"),   tip: t("org.dashboard.feedAssignTip"),   cls: "feed-delegated" },
                task_delivered:  { icon: "↙", label: t("org.dashboard.feedDeliver"),  tip: t("org.dashboard.feedDeliverTip"),  cls: "feed-delivered" },
                task_accepted:   { icon: "✓", label: t("org.dashboard.feedAccept"),   tip: t("org.dashboard.feedAcceptTip"),   cls: "feed-accepted" },
                task_rejected:   { icon: "✗", label: t("org.dashboard.feedReject"),   tip: t("org.dashboard.feedRejectTip"),   cls: "feed-rejected" },
                task_timeout:    { icon: "⏱", label: t("org.dashboard.feedTimeout"),  tip: t("org.dashboard.feedTimeoutTip"),  cls: "feed-timeout" },
                task_completed:  { icon: "✓", label: t("org.dashboard.feedComplete"), tip: t("org.dashboard.feedCompleteTip"), cls: "feed-completed" },
                node_activated:  { icon: "▶", label: t("org.dashboard.feedExecute"),  tip: t("org.dashboard.feedExecuteTip"),  cls: "feed-activated" },
              };
              const defaultMeta = { icon: "•", label: "", tip: "", cls: "" };

              const busyLines: { key: string; node: string; text: string; pct: number }[] = [];
              for (const n of perNode) {
                if (n.status !== "busy" && !n.current_task_title) continue;
                const pp = n.plan_progress || {};
                const pct = pp.total > 0 ? Math.round((pp.completed / pp.total) * 100) : -1;
                const rawTask = n.current_task_title || (n.current_task ? String(n.current_task) : t("org.editor.executing"));
                const taskDesc = humanizeTask(rawTask, nodes);
                busyLines.push({ key: n.id, node: n.role_title || nodeLabel(n.id), text: taskDesc, pct });
              }

              if (busyLines.length === 0 && recentTasks.length === 0 && anomalies.length === 0) return null;

              const FeedTip = ({ text }: { text: string }) => (
                <div className="org-feed-tip-content">
                  {mdModules ? (
                    <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>
                      {text ?? ""}
                    </mdModules.ReactMarkdown>
                  ) : (
                    <div style={{ whiteSpace: "pre-wrap" }}>{text ?? ""}</div>
                  )}
                </div>
              );

              const tipCls = "org-feed-tip-wrap bg-popover text-popover-foreground border border-border shadow-lg";

              return (
                <div className="org-live-feed">
                  {busyLines.map(b => (
                    <div key={b.key} className="org-feed-item org-feed-busy"
                      onClick={() => { setSelectedNodeId(b.key); setSelectedEdgeId(null); setShowRightPanel(true); setPropsTab("overview"); }}
                    >
                      <span className="org-feed-dot" />
                      <span className="org-feed-who">{b.node}</span>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="org-feed-text">{stripMd(b.text)}</span>
                        </TooltipTrigger>
                        <TooltipContent side="top" align="start" className={tipCls}>
                          <FeedTip text={b.text} />
                        </TooltipContent>
                      </Tooltip>
                      {b.pct >= 0 && (
                        <span className="org-feed-progress">
                          <span className="org-feed-bar"><span className="org-feed-bar-fill" style={{ width: `${b.pct}%` }} /></span>
                          <span className="org-feed-pct">{b.pct}%</span>
                        </span>
                      )}
                    </div>
                  ))}
                  {recentTasks.slice(0, 6).map((t: any, i: number) => {
                    const ts = t.t ? new Date(typeof t.t === "number" && t.t < 1e12 ? t.t * 1000 : t.t) : null;
                    const timeStr = ts ? fmtTime(ts.getTime()) : "";
                    const fullTimeStr = ts ? fmtDateTime(ts.getTime()) : "";
                    const meta = typeMeta[t.type] || defaultMeta;
                    const fromLabel = nodeLabel(t.from);
                    const toLabel = nodeLabel(t.to);
                    return (
                      <div key={`rt-${i}`} className={`org-feed-item ${meta.cls}`}>
                        <span className="org-feed-time" title={fullTimeStr}>{timeStr}</span>
                        <span className={`org-feed-badge ${meta.cls}`} title={meta.tip}>
                          <span className="org-feed-badge-icon">{meta.icon}</span>
                          {meta.label || t.type}
                        </span>
                        <span className="org-feed-who">{fromLabel}</span>
                        {toLabel && <>
                          <span className="org-feed-arrow">→</span>
                          <span className="org-feed-who">{toLabel}</span>
                        </>}
                        {t.task && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="org-feed-text">{stripMd(t.task)}</span>
                            </TooltipTrigger>
                            <TooltipContent side="top" align="start" className={tipCls}>
                              <FeedTip text={t.task} />
                            </TooltipContent>
                          </Tooltip>
                        )}
                      </div>
                    );
                  })}
                  {anomalies.map((a: any, i: number) => (
                    <div key={`an-${i}`} className="org-feed-item feed-warn">
                      <span className="org-feed-badge feed-warn">
                        <span className="org-feed-badge-icon">!</span>
                        {t("org.editor.errorBadge")}
                      </span>
                      <span className="org-feed-who">{a.role_title || nodeLabel(a.node_id)}</span>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="org-feed-text">{stripMd(String(a.message))}</span>
                        </TooltipTrigger>
                        <TooltipContent side="top" align="start" className={tipCls}>
                          <FeedTip text={String(a.message)} />
                        </TooltipContent>
                      </Tooltip>
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
          )}

          {/* ═══ Floating FAB group (bottom-right) ═══ */}
          {selectedOrgId && !chatPanelOpen && (
            <div className="org-fab-group">
              <button
                onClick={() => setShowBlackboardPanel(v => !v)}
                className={`org-bb-fab${showBlackboardPanel ? " org-bb-fab--active" : ""}`}
                title={t("org.editor.orgBlackboard")}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <line x1="8" y1="8" x2="16" y2="8" />
                  <line x1="8" y1="12" x2="14" y2="12" />
                  <line x1="8" y1="16" x2="12" y2="16" />
                </svg>
                <span className="org-bb-fab-label">{t("org.editor.blackboard")}</span>
              </button>
              <button
                onClick={() => setActiveDrawer("chat")}
                className="org-chat-fab"
                title={t("org.editor.openCommandCenter")}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <span className="org-chat-fab-label">{t("org.editor.commandCenter")}</span>
              </button>
            </div>
          )}

          {/* ═══ Slide-out Drawers (Chat / Inbox) — shared overlay, mutually exclusive ═══ */}
          {selectedOrgId && (
            <>
              <div
                className="org-drawer-overlay"
                onClick={() => setActiveDrawer(null)}
                style={{ display: activeDrawer ? undefined : "none" }}
              />
              <div className="org-drawer-slide" style={{ display: chatPanelOpen ? undefined : "none" }}>
                <OrgChatPanel
                  orgId={selectedOrgId}
                  nodeId={null}
                  apiBaseUrl={apiBaseUrl}
                  showHeader
                  title={t("org.editor.orgCommandCenter", { name: currentOrg?.name || t("org.editor.orgDefault") })}
                  onClose={() => setActiveDrawer(null)}
                  nodeNames={nodeNameMap}
                />
              </div>
              <div className="org-drawer-slide" style={{ display: inboxOpen ? undefined : "none" }}>
                <OrgInboxSidebar
                  apiBaseUrl={apiBaseUrl}
                  orgId={selectedOrgId}
                  visible={inboxOpen}
                  onClose={() => setActiveDrawer(null)}
                  embedded
                />
              </div>
            </>
          )}

          <style>{`
            .org-fab-group {
              position: absolute; bottom: 20px; right: 20px; z-index: 40;
              display: flex; flex-direction: column; align-items: stretch; gap: 10px;
              animation: org-fab-in 0.4s cubic-bezier(0.34,1.56,0.64,1);
            }
            .org-chat-fab {
              display: flex; align-items: center; justify-content: center; gap: 8px;
              padding: 12px 20px; border: none; border-radius: 16px;
              background: linear-gradient(135deg, #3b82f6, #6366f1) !important;
              color: #ffffff !important; cursor: pointer; font-size: 13px; font-weight: 600;
              box-shadow: 0 4px 20px rgba(99,102,241,0.4), 0 0 40px rgba(99,102,241,0.15);
              transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
              -webkit-text-fill-color: #ffffff !important;
            }
            @keyframes org-fab-in {
              from { transform: scale(0.5) translateY(20px); opacity: 0; }
              to { transform: scale(1) translateY(0); opacity: 1; }
            }
            .org-chat-fab:hover {
              transform: translateY(-2px) scale(1.02);
              background: linear-gradient(135deg, #2563eb, #4f46e5) !important;
              color: #ffffff !important;
              -webkit-text-fill-color: #ffffff !important;
              box-shadow: 0 6px 28px rgba(99,102,241,0.6), 0 0 60px rgba(99,102,241,0.25);
            }
            .org-chat-fab:active { transform: scale(0.97); }
            .org-chat-fab svg { stroke: #ffffff !important; }
            .org-chat-fab-label { letter-spacing: 0.5px; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
            .org-bb-fab {
              display: flex; align-items: center; justify-content: center; gap: 8px;
              padding: 12px 20px; border: none; border-radius: 16px;
              background: linear-gradient(135deg, #10b981, #059669) !important;
              color: #ffffff !important; cursor: pointer; font-size: 13px; font-weight: 600;
              box-shadow: 0 4px 20px rgba(16,185,129,0.4), 0 0 40px rgba(16,185,129,0.15);
              transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
              -webkit-text-fill-color: #ffffff !important;
            }
            .org-bb-fab:hover {
              transform: translateY(-2px) scale(1.02);
              background: linear-gradient(135deg, #059669, #047857) !important;
              box-shadow: 0 6px 28px rgba(16,185,129,0.6), 0 0 60px rgba(16,185,129,0.25);
            }
            .org-bb-fab--active {
              background: linear-gradient(135deg, #047857, #065f46) !important;
              box-shadow: 0 2px 10px rgba(16,185,129,0.3), inset 0 1px 2px rgba(0,0,0,0.1);
            }
            .org-bb-fab:active { transform: scale(0.97); }
            .org-bb-fab svg { stroke: #ffffff !important; }
            .org-bb-fab-label { letter-spacing: 0.5px; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }

            .org-drawer-overlay {
              position: absolute; inset: 0; z-index: 80;
              background: rgba(0,0,0,0.3);
              backdrop-filter: blur(2px);
              animation: org-overlay-in 0.2s ease;
            }
            @keyframes org-overlay-in { from { opacity: 0; } to { opacity: 1; } }

            .org-drawer-slide {
              position: absolute; top: 0; right: 0; bottom: 0; z-index: 90;
              width: min(560px, 85%);
              background: var(--bg-app);
              border-left: 1px solid var(--line, rgba(51,65,85,0.5));
              box-shadow: -8px 0 30px rgba(0,0,0,0.3);
              animation: org-slide-in 0.3s cubic-bezier(0.4,0,0.2,1);
              display: flex; flex-direction: column; overflow: hidden;
            }
            @keyframes org-slide-in { from { transform: translateX(100%); } to { transform: translateX(0); } }
            @keyframes org-panel-in {
              from { opacity: 0; transform: translateX(40px); }
              to { opacity: 1; transform: translateX(0); }
            }

            .org-ctx-menu {
              min-width: 160px;
              background: var(--card-bg);
              border: 1px solid var(--line, rgba(51,65,85,0.6));
              border-radius: 10px;
              padding: 4px;
              box-shadow: 0 8px 30px rgba(0,0,0,0.35), 0 0 1px rgba(255,255,255,0.1);
              backdrop-filter: blur(12px);
              animation: org-ctx-in 0.15s ease;
            }
            @keyframes org-ctx-in { from { opacity: 0; transform: scale(0.92); } to { opacity: 1; transform: scale(1); } }
            .org-ctx-menu button {
              display: flex; align-items: center; gap: 8px; width: 100%;
              padding: 8px 12px; border: none; border-radius: 7px;
              background: transparent; color: var(--text);
              font-size: 13px; cursor: pointer; text-align: left;
              transition: background 0.15s;
            }
            .org-ctx-menu button:hover { background: var(--hover-bg, rgba(99,102,241,0.15)); }
            .org-ctx-menu button:disabled {
              opacity: 0.45;
              cursor: not-allowed;
            }
            .org-ctx-menu button:disabled:hover { background: transparent; }
            .org-ctx-icon { width: 18px; text-align: center; flex-shrink: 0; font-size: 14px; }

            /* ── Top bar layout ── */
            .org-topbar {
              min-height: 36px;
              padding: 2px 0;
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 8px;

              background: var(--card-bg, #fff);
              border-bottom: 1px solid color-mix(in srgb, var(--line, rgba(15,23,42,0.08)) 82%, transparent);
              flex-shrink: 0;
              container-type: inline-size;
              container-name: org-topbar;
            }
            :root[data-theme="light"] .org-topbar {
              background:
                linear-gradient(
                  180deg,
                  color-mix(in srgb, var(--card-bg, #fff) 80%, var(--bg-app, #f4f5f7) 20%),
                  color-mix(in srgb, var(--card-bg, #fff) 94%, var(--bg-subtle, #f1f5f9) 6%)
                );
            }
            .org-topbar-left {
              display: flex; align-items: center; gap: 6px;
              flex-shrink: 1; min-width: 0; overflow: hidden;
            }
            .org-topbar-center {
              display: flex; align-items: center; justify-content: center;
              align-self: stretch; flex: 0 0 auto;
            }
            .org-topbar-tabs {
              align-self: center;
            }
            :root[data-theme="dark"] .org-topbar {
              background: color-mix(in srgb, var(--card-bg, #27272a) 94%, var(--bg-app, #09090b) 6%);
              min-height: 38px;
              padding-block: 3px;
            }
            .org-topbar-name {
              font-weight: 600; font-size: 14px;
              color: var(--text);
              max-width: 180px;
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
              cursor: pointer; border-radius: 4px;
              padding: 2px 6px;
              transition: background 0.15s;
            }
            .org-topbar-name:hover { background: var(--hover-bg, rgba(99,102,241,0.08)); }
            .org-topbar-name--editing {
              border: 1px solid var(--primary, #6366f1);
              background: var(--card-bg, #fff);
              outline: none; width: 160px;
              cursor: text;
            }
            .org-topbar-name--editing:hover { background: var(--card-bg, #fff); }
            .org-topbar-status {
              display: inline-flex; align-items: center; gap: 5px;
              font-size: 11px; padding: 3px 10px; border-radius: 20px;
              border: 1px solid; font-weight: 500;
              white-space: nowrap; flex-shrink: 0;
              user-select: none;
            }
            .org-status-dot {
              width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
            }
            .org-status-sep {
              width: 1px; height: 10px; background: currentColor; opacity: 0.25; flex-shrink: 0;
            }
            .org-status-stat {
              display: inline-flex; align-items: center; gap: 2px;
              font-weight: 400; opacity: 0.85; cursor: default;
            }
            @container org-topbar (max-width: 700px) {
              .org-status-label { display: none; }
            }
            @container org-topbar (max-width: 880px) {
              .org-topbar {
                padding-block: 1px;
              }
              .org-topbar-center {
                flex: 1 1 auto;
                min-width: 0;
              }
              .org-topbar-tabs {
                max-width: 100%;
              }
            }

            /* ── Right actions ── */
            .org-topbar-right {
              display: flex; align-items: center; gap: 3px; flex-shrink: 0;
            }
            .org-tb-btn {
              display: inline-flex; align-items: center; gap: 4px;
              height: 28px; padding: 0 8px; border-radius: 6px;
              border: 1px solid var(--line, rgba(51,65,85,0.5));
              background: transparent;
              color: var(--text);
              font-size: 12px; cursor: pointer; white-space: nowrap;
              transition: background 0.15s, color 0.15s, border-color 0.15s;
              position: relative;
            }
            .org-tb-btn:hover {
              background: var(--hover-bg, rgba(99,102,241,0.12));
              border-color: rgba(99,102,241,0.3);
            }
            .org-tb-btn:active { background: rgba(99,102,241,0.2); }
            .org-tb-btn:disabled { opacity: 0.4; cursor: not-allowed; }
            .org-tb-btn--active {
              color: var(--primary, #6366f1); font-weight: 600;
              background: rgba(99,102,241,0.12);
              border-color: rgba(99,102,241,0.35);
            }
            .org-save-indicator {
              display: inline-flex; align-items: center; gap: 4px;
              font-size: 11px; padding: 4px 12px;
              border-radius: 16px; white-space: nowrap;
              background: var(--card-bg, rgba(30,41,59,0.9));
              border: 1px solid var(--line, rgba(51,65,85,0.5));
              box-shadow: 0 2px 8px rgba(0,0,0,0.15);
              backdrop-filter: blur(8px);
              animation: orgSaveIn 0.2s ease;
              user-select: none;
            }
            @keyframes orgSaveIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
            .org-save-indicator--saving { color: var(--muted, #94a3b8); }
            .org-save-indicator--saved { color: #22c55e; }
            .org-save-indicator--error { color: #ef4444; font-weight: 500; }
            .org-tb-btn--ok { color: #22c55e; border-color: rgba(34,197,94,0.3); }
            .org-tb-btn--ok:hover { background: rgba(34,197,94,0.12); }
            .org-tb-btn--danger { color: #ef4444; border-color: rgba(239,68,68,0.3); }
            .org-tb-btn--danger:hover { background: rgba(239,68,68,0.12); }
            .org-notif-dot {
              position: absolute; top: 3px; right: 3px;
              width: 5px; height: 5px; border-radius: 50%;
              background: var(--ok, #22c55e);
              animation: orgDotPulse 1.5s ease-in-out infinite;
            }

            /* ── Canvas toolbar (inside ReactFlow) ── */
            .org-canvas-toolbar {
              display: flex; align-items: center; gap: 4px;
              background: var(--card-bg, rgba(30,41,59,0.9));
              border: 1px solid var(--line, rgba(51,65,85,0.5));
              border-radius: 8px; padding: 3px 4px;
              box-shadow: 0 2px 8px rgba(0,0,0,0.2);
              backdrop-filter: blur(8px);
            }
            .org-cvs-btn {
              display: inline-flex; align-items: center; gap: 4px;
              height: 26px; padding: 0 10px; border-radius: 5px;
              border: none; background: transparent;
              color: var(--text); font-size: 11px;
              cursor: pointer; white-space: nowrap;
              transition: background 0.15s;
            }
            .org-cvs-btn:hover { background: rgba(99,102,241,0.15); }
            .org-cvs-btn:disabled {
              opacity: 0.45;
              cursor: not-allowed;
            }
            .org-cvs-btn:disabled:hover { background: transparent; }
            .org-cvs-btn--active { color: var(--primary, #6366f1); font-weight: 600; }
            .org-cvs-btn--danger { color: #ef4444; }
            .org-cvs-btn--danger:hover { background: rgba(239,68,68,0.15); }

            /* ── Enhanced connection handles ── */
            .org-handle.react-flow__handle {
              pointer-events: all !important;
              width: 11px !important;
              height: 11px !important;
              min-width: 11px !important;
              min-height: 11px !important;
              background: var(--primary, #6366f1) !important;
              border: 2px solid var(--card-bg, #fff) !important;
              border-radius: 50% !important;
              opacity: 0.72;
              z-index: 10 !important;
              box-shadow: 0 0 0 2px rgba(99,102,241,0.14), 0 0 6px rgba(99,102,241,0.18);
              transition: width 0.2s, height 0.2s, opacity 0.2s, box-shadow 0.2s, background 0.2s;
              cursor: crosshair !important;
            }
            .react-flow__node:hover .org-handle,
            .react-flow__node.selected .org-handle {
              width: 14px !important;
              height: 14px !important;
              min-width: 14px !important;
              min-height: 14px !important;
              opacity: 1;
              border-color: transparent !important;
              box-shadow: 0 0 0 3px rgba(99,102,241,0.3), 0 0 10px rgba(99,102,241,0.42);
              animation: org-handle-pulse 2s ease-in-out infinite;
            }
            .react-flow__node:hover .org-handle:hover,
            .react-flow__node.selected .org-handle:hover {
              width: 16px !important;
              height: 16px !important;
              min-width: 16px !important;
              min-height: 16px !important;
              background: #818cf8 !important;
              box-shadow: 0 0 0 4px rgba(99,102,241,0.35), 0 0 16px rgba(99,102,241,0.6);
              animation: none;
            }
            @keyframes org-handle-pulse {
              0%, 100% { box-shadow: 0 0 0 3px rgba(99,102,241,0.3); }
              50% { box-shadow: 0 0 0 6px rgba(99,102,241,0.15), 0 0 12px rgba(99,102,241,0.3); }
            }
            .react-flow__handle.connecting,
            .react-flow__handle.connectingfrom {
              background: #818cf8 !important;
              width: 14px !important;
              height: 14px !important;
              pointer-events: all !important;
            }

            .org-edge-legend {
              display: flex; align-items: center; gap: 8px;
              margin-top: 4px; padding: 3px 8px;
              background: var(--card-bg, rgba(30,41,59,0.85));
              border: 1px solid var(--line, rgba(51,65,85,0.5));
              border-radius: 6px;
              backdrop-filter: blur(8px);
            }
            .org-edge-legend-item {
              display: inline-flex; align-items: center; gap: 4px;
              font-size: 10px; color: var(--muted, #94a3b8);
              white-space: nowrap;
            }
            .org-edge-legend-line {
              display: inline-block; width: 16px; height: 2px;
              border-radius: 1px; flex-shrink: 0;
            }
            .org-connect-hint {
              margin-top: 4px;
              padding: 4px 8px;
              border-radius: 6px;
              border: 1px dashed color-mix(in srgb, var(--primary, #6366f1) 30%, transparent);
              background: color-mix(in srgb, var(--primary, #6366f1) 7%, transparent);
              color: var(--muted, #94a3b8);
              font-size: 10px;
              line-height: 1.4;
              width: fit-content;
            }

            .org-tb-stats {
              display: flex; gap: 6px; align-items: center;
              font-size: 10px; color: var(--muted, #6b7280);
              padding: 0 4px;
            }

            /* ── Canvas bottom live activity feed ── */
            .org-live-feed {
              position: absolute; bottom: 0; left: 0; right: 0;
              z-index: 5; max-height: 160px; overflow-y: auto;
              background: linear-gradient(to top, var(--bg-app, rgba(15,23,42,0.97)) 80%, transparent);
              padding: 12px 16px 8px;
              scrollbar-width: thin;
            }
            .org-feed-item {
              display: flex; align-items: center; gap: 7px;
              padding: 4px 0; font-size: 12px; color: var(--text, #cbd5e1);
              line-height: 1.5; white-space: nowrap; cursor: pointer;
              border-bottom: 1px solid rgba(51,65,85,0.12);
            }
            .org-feed-item:last-child { border-bottom: none; }
            .org-feed-item:hover .org-feed-text { color: var(--text, #e2e8f0); }
            .org-feed-busy:hover .org-feed-who { color: var(--primary, #6366f1); }

            .org-feed-dot {
              width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
              background: #3b82f6;
              animation: orgDotPulse 1.5s ease-in-out infinite;
            }

            .org-feed-time {
              font-size: 11px; color: var(--muted, #64748b);
              font-family: "SF Mono", "Cascadia Code", "Consolas", ui-monospace, monospace;
              font-variant-numeric: tabular-nums;
              flex-shrink: 0; min-width: 38px; letter-spacing: 0.2px;
              opacity: 0.75;
            }

            .org-feed-badge {
              display: inline-flex; align-items: center; gap: 3px;
              flex-shrink: 0; padding: 1px 7px 1px 5px; border-radius: 4px;
              font-size: 11px; font-weight: 500; white-space: nowrap;
              background: rgba(99,102,241,0.10); color: var(--primary, #818cf8);
            }
            .org-feed-badge-icon {
              font-weight: 700; font-size: 11px; line-height: 1;
              font-family: system-ui, sans-serif;
            }
            .org-feed-badge.feed-completed,
            .org-feed-badge.feed-accepted {
              background: rgba(34,197,94,0.10); color: #22c55e;
            }
            .org-feed-badge.feed-activated {
              background: rgba(59,130,246,0.10); color: #3b82f6;
            }
            .org-feed-badge.feed-rejected {
              background: rgba(239,68,68,0.10); color: #ef4444;
            }
            .org-feed-badge.feed-timeout {
              background: rgba(245,158,11,0.10); color: #f59e0b;
            }
            .org-feed-badge.feed-delegated {
              background: rgba(99,102,241,0.10); color: #818cf8;
            }
            .org-feed-badge.feed-delivered {
              background: rgba(6,182,212,0.10); color: #06b6d4;
            }
            .org-feed-badge.feed-warn {
              background: rgba(245,158,11,0.12); color: #f59e0b;
            }

            .org-feed-who {
              font-weight: 600; color: var(--text); flex-shrink: 0;
              max-width: 100px; overflow: hidden; text-overflow: ellipsis;
              transition: color 0.15s; font-size: 12px;
            }

            .org-feed-arrow {
              color: var(--muted, #64748b); flex-shrink: 0;
              font-size: 11px; opacity: 0.6;
            }

            .feed-warn .org-feed-who { color: #f59e0b; }
            .feed-warn .org-feed-text { color: #f59e0b; }

            .org-feed-text {
              color: var(--muted, #94a3b8); font-size: 11px;
              overflow: hidden; text-overflow: ellipsis; min-width: 0;
              flex: 1;
            }

            .org-feed-progress {
              display: inline-flex; align-items: center; gap: 4px;
              flex-shrink: 0;
            }
            .org-feed-bar {
              width: 48px; height: 4px; border-radius: 2px;
              background: rgba(51,65,85,0.3); overflow: hidden;
            }
            .org-feed-bar-fill {
              height: 100%; border-radius: 2px;
              background: #3b82f6; transition: width 0.3s ease;
            }
            .org-feed-pct {
              font-size: 10px; color: var(--muted); font-weight: 600;
            }

            /* ── Feed tooltip (shadcn + markdown) ── */
            .org-feed-tip-wrap {
              max-width: 640px !important; min-width: 200px;
              padding: 10px 14px !important; text-align: left !important;
              white-space: normal !important; border-radius: 8px !important;
            }
            .org-feed-tip-wrap [data-slot="tooltip-arrow"] {
              display: none;
            }
            .org-feed-tip-content {
              font-size: 12px; line-height: 1.7;
              max-height: 300px; overflow-y: auto;
              scrollbar-width: thin;
              color: inherit;
            }
            .org-feed-tip-content p { margin: 0 0 6px; }
            .org-feed-tip-content p:last-child { margin-bottom: 0; }
            .org-feed-tip-content ul, .org-feed-tip-content ol {
              margin: 4px 0; padding-left: 1.4em;
            }
            .org-feed-tip-content li { margin: 2px 0; }
            .org-feed-tip-content strong { font-weight: 600; }
            .org-feed-tip-content code {
              font-size: 11px; padding: 1px 5px; border-radius: 3px;
              background: hsl(var(--muted));
            }
            .org-feed-tip-content pre {
              margin: 4px 0; padding: 8px 10px; border-radius: 6px;
              background: hsl(var(--muted)); overflow-x: auto;
              font-size: 11px;
            }
            .org-feed-tip-content pre code {
              padding: 0; background: none;
            }

            /* ── Blackboard entry markdown ── */
            .bb-entry-content {
              font-size: 12px; line-height: 1.7; word-break: break-word;
              color: var(--text, #e2e8f0);
            }
            .bb-entry-content p { margin: 0 0 4px; }
            .bb-entry-content p:last-child { margin-bottom: 0; }
            .bb-entry-content h1, .bb-entry-content h2, .bb-entry-content h3,
            .bb-entry-content h4, .bb-entry-content h5, .bb-entry-content h6 {
              margin: 6px 0 3px; font-size: 12px; font-weight: 700;
              color: var(--text, #f1f5f9);
            }
            .bb-entry-content h1 { font-size: 14px; }
            .bb-entry-content h2 { font-size: 13px; }
            .bb-entry-content ul, .bb-entry-content ol {
              margin: 2px 0; padding-left: 1.4em;
            }
            .bb-entry-content li { margin: 1px 0; }
            .bb-entry-content li::marker { color: var(--muted, #64748b); }
            .bb-entry-content strong { font-weight: 600; }
            .bb-entry-content em { font-style: italic; }
            .bb-entry-content code {
              font-size: 11px; padding: 1px 4px; border-radius: 3px;
              background: var(--hover-bg, rgba(100,100,100,0.15));
              font-family: "SF Mono", "Cascadia Code", "Consolas", ui-monospace, monospace;
            }
            .bb-entry-content pre {
              margin: 4px 0; padding: 8px 10px; border-radius: 6px;
              background: var(--hover-bg, rgba(0,0,0,0.2)); overflow-x: auto;
              font-size: 11px;
            }
            .bb-entry-content pre code { padding: 0; background: none; }
            .bb-entry-content blockquote {
              margin: 4px 0; padding: 2px 0 2px 10px;
              border-left: 3px solid var(--primary, #6366f1);
              color: var(--muted, #94a3b8);
            }
            .bb-entry-content table { border-collapse: collapse; margin: 4px 0; font-size: 11px; width: 100%; }
            .bb-entry-content th, .bb-entry-content td {
              padding: 3px 8px; border: 1px solid var(--line, rgba(51,65,85,0.5));
              text-align: left;
            }
            .bb-entry-content th { font-weight: 600; background: var(--hover-bg, rgba(100,100,100,0.1)); }
            .bb-entry-content hr { border: none; border-top: 1px solid var(--line, rgba(51,65,85,0.5)); margin: 6px 0; }
            .bb-entry-content a { color: var(--primary, #6366f1); text-decoration: underline; }

            /* ── Modal dialog ── */
            .org-modal-overlay {
              position: fixed; inset: 0; z-index: 10000;
              background: rgba(0,0,0,0.45);
              backdrop-filter: blur(3px);
              display: flex; align-items: center; justify-content: center;
              animation: org-overlay-in 0.15s ease;
            }
            .org-modal {
              background: var(--bg-app);
              border: 1px solid var(--line, rgba(51,65,85,0.6));
              border-radius: 12px;
              box-shadow: 0 12px 40px rgba(0,0,0,0.4);
              min-width: 300px; max-width: 90vw;
              animation: org-ctx-in 0.2s ease;
            }
            .org-modal-header {
              display: flex; justify-content: space-between; align-items: center;
              padding: 14px 16px 10px;
              font-weight: 600; font-size: 14px; color: var(--text);
            }
            .org-modal-close {
              background: none; border: none; color: var(--muted, #94a3b8);
              cursor: pointer; padding: 4px; border-radius: 4px;
              transition: color 0.15s;
            }
            .org-modal-close:hover { color: var(--text); }
            .org-modal-body { padding: 0 16px 12px; }
            .org-modal-label {
              display: block; font-size: 11px; font-weight: 500;
              color: var(--muted, #94a3b8); margin-bottom: 4px;
            }
            .org-modal-footer {
              display: flex; justify-content: flex-end; gap: 8px;
              padding: 10px 16px 14px;
              border-top: 1px solid var(--line, rgba(51,65,85,0.4));
            }
            .org-modal-btn {
              height: 32px; padding: 0 16px; border-radius: 6px;
              border: 1px solid var(--line, rgba(51,65,85,0.5));
              background: transparent; color: var(--text);
              font-size: 12px; cursor: pointer;
              transition: background 0.15s;
            }
            .org-modal-btn:hover { background: rgba(99,102,241,0.1); }
            .org-modal-btn--primary {
              background: var(--primary, #6366f1); color: #fff;
              border-color: var(--primary, #6366f1);
            }
            .org-modal-btn--primary:hover { background: #4f46e5; }
          `}</style>
          </>
        ) : null}
      </div>
      )}

      {/* ── Right Panel: Node Properties ── */}
      {isMobile && selectedNode && showRightPanel && (
        <div
          onClick={() => { setSelectedNodeId(null); }}
          style={{
            position: "absolute", inset: 0, zIndex: 49,
            background: "rgba(0,0,0,0.3)",
          }}
        />
      )}

      {/* ── Chat Panel (liveMode, desktop only, toggled by button) ── */}
      {liveMode && selectedNode && showRightPanel && !isMobile && selectedOrgId && showNodeChat && (
        <div
          style={{
            width: 300, flexShrink: 0,
            borderLeft: "1px solid var(--line)",
            background: "var(--bg-app)",
            display: "flex", flexDirection: "column",
            animation: "org-panel-in 0.3s cubic-bezier(0.4,0,0.2,1)",
          }}
        >
          <div style={{
            padding: "12px 12px 8px", borderBottom: "1px solid var(--line)",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>
              {t("org.editor.chatTitle", { name: selectedNode.role_title })}
            </div>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <OrgChatPanel
              orgId={selectedOrgId}
              nodeId={selectedNodeId}
              apiBaseUrl={apiBaseUrl}
              compact
              nodeNames={nodeNameMap}
            />
          </div>
        </div>
      )}

      {/* ── Monitor Panel (liveMode, desktop only) ── */}
      {liveMode && selectedNode && showRightPanel && !isMobile && selectedOrgId && (
        <OrgMonitorPanel
          orgId={selectedOrgId}
          nodeId={selectedNode.id}
          apiBaseUrl={apiBaseUrl}
          nodes={nodes}
          visible={visible}
        />
      )}

      {selectedNode && showRightPanel && (
        <div
          style={{
            width: isMobile ? "85%" : 300,
            maxWidth: isMobile ? 360 : 300,
            borderLeft: isMobile ? "none" : "1px solid var(--line)",
            overflowY: "auto",
            scrollbarGutter: "stable",
            background: "var(--bg-app)",
            position: isMobile ? "absolute" : "relative",
            right: 0,
            top: 0,
            bottom: 0,
            zIndex: isMobile ? 50 : "auto",
            boxShadow: isMobile ? "-4px 0 12px rgba(0,0,0,0.15)" : "none",
            flexShrink: 0,
            animation: isMobile ? undefined : "org-panel-in 0.3s cubic-bezier(0.4,0,0.2,1) 0.1s both",
          }}
        >
          <div style={{ padding: "12px 12px 8px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{selectedNode.role_title}</div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>
                {selectedNode.department || t("org.editor.unassignedDept")}
              </div>
            </div>
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
              {liveMode && selectedOrgId && (
                <button
                  className="btnSmall"
                  onClick={() => setShowNodeChat(prev => !prev)}
                  style={{
                    minWidth: 36, minHeight: 36, fontSize: 12,
                    background: showNodeChat
                      ? "linear-gradient(135deg, #2563eb, #4338ca)"
                      : "linear-gradient(135deg, #3b82f6, #6366f1)",
                    color: "#fff", border: "none", borderRadius: 8,
                    boxShadow: showNodeChat ? "inset 0 1px 3px rgba(0,0,0,0.3)" : undefined,
                  }}
                  title={showNodeChat ? t("org.editor.collapseChat") : t("org.editor.expandChat")}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                  </svg>
                </button>
              )}
              {isMobile && (
                <button className="btnSmall" onClick={() => { setSelectedNodeId(null); }} style={{ minWidth: 36, minHeight: 36 }}><IconX size={14} /></button>
              )}
            </div>
          </div>

          {/* Tabs */}
          <div style={{ padding: "10px 12px 0", borderBottom: "1px solid var(--line)" }}>
            <ToggleGroup
              type="single"
              value={propsTab}
              onValueChange={(v) => { if (v) setPropsTab(v as typeof propsTab); }}
              variant="outline"
              size="sm"
              spacing={0}
              className="grid w-full grid-cols-3"
            >
              <ToggleGroupItem value="overview" className="h-8 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                {t("org.editor.tabOverview")}
              </ToggleGroupItem>
              <ToggleGroupItem value="identity" className="h-8 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                {t("org.editor.tabIdentity")}
              </ToggleGroupItem>
              <ToggleGroupItem value="capabilities" className="h-8 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                {t("org.editor.tabCapabilities")}
              </ToggleGroupItem>
            </ToggleGroup>
          </div>

          {liveMode && selectedNodeId && (
            <div style={{
              margin: "8px 12px 0", padding: "6px 10px",
              background: "rgba(234,179,8,0.12)",
              border: "1px solid rgba(234,179,8,0.3)",
              borderRadius: 6,
              display: "flex", alignItems: "center", gap: 6,
              fontSize: 11, color: "#a16207",
            }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
              <span>{t("org.editor.runningLocked")}</span>
            </div>
          )}

          <div style={{ padding: 12 }}>

            {/* ── Org-level stats dashboard (live mode, no node selected) ── */}
            {propsTab === "overview" && liveMode && !selectedNodeId && orgStats && (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

                {/* Health indicator */}
                <div className="card" style={{
                  padding: "10px 12px", display: "flex", alignItems: "center", gap: 10,
                  borderLeft: `4px solid ${orgStats.health === "critical" ? "#ef4444" : orgStats.health === "warning" ? "#f59e0b" : orgStats.health === "attention" ? "#3b82f6" : "#22c55e"}`,
                }}>
                  <div style={{
                    width: 12, height: 12, borderRadius: "50%",
                    background: orgStats.health === "critical" ? "#ef4444" : orgStats.health === "warning" ? "#f59e0b" : orgStats.health === "attention" ? "#3b82f6" : "#22c55e",
                    animation: orgStats.health !== "healthy" ? "orgDotPulse 1.5s ease-in-out infinite" : undefined,
                    boxShadow: `0 0 8px ${orgStats.health === "critical" ? "#ef4444" : orgStats.health === "warning" ? "#f59e0b" : orgStats.health === "attention" ? "#3b82f6" : "#22c55e"}60`,
                  }} />
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>
                      {orgStats.health === "healthy" ? t("org.dashboard.healthGood") : orgStats.health === "critical" ? t("org.dashboard.healthCritical") : orgStats.health === "warning" ? t("org.dashboard.healthWarning") : t("org.dashboard.healthAttention")}
                    </div>
                    <div style={{ fontSize: 10, color: "#9ca3af" }}>
                      {orgStats.anomalies?.length > 0 ? t("org.editor.alertsTitle", { count: orgStats.anomalies.length }) : t("org.dashboard.allClear")}
                    </div>
                  </div>
                </div>

                {/* KPI grid */}
                <div className="card" style={{ padding: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{t("org.editor.kpiTitle")}</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                    {[
                      { label: t("org.editor.uptime"), value: orgStats.uptime_s ? (orgStats.uptime_s >= 3600 ? `${Math.floor(orgStats.uptime_s / 3600)}h${Math.floor((orgStats.uptime_s % 3600) / 60)}m` : `${Math.round(orgStats.uptime_s / 60)}m`) : "-", color: "var(--primary)" },
                      { label: t("org.editor.tasksCompleted"), value: orgStats.total_tasks_completed ?? 0, color: "#22c55e" },
                      { label: t("org.editor.messagesExchanged"), value: orgStats.total_messages_exchanged ?? 0, color: "#3b82f6" },
                      { label: t("org.editor.pendingCount"), value: orgStats.pending_messages ?? 0, color: orgStats.pending_messages > 5 ? "#f59e0b" : "#6b7280" },
                      { label: t("org.editor.unreadMessages"), value: orgStats.unread_inbox ?? 0, color: orgStats.unread_inbox > 0 ? "#dc2626" : "#6b7280" },
                      { label: t("org.editor.pendingApprovals"), value: orgStats.pending_approvals ?? 0, color: orgStats.pending_approvals > 0 ? "#7c3aed" : "#6b7280" },
                    ].map((item) => (
                      <div key={item.label} style={{
                        padding: 6, background: "var(--bg-secondary)",
                        borderRadius: 6, textAlign: "center",
                      }}>
                        <div style={{ fontSize: 16, fontWeight: 700, color: item.color }}>{item.value}</div>
                        <div style={{ fontSize: 9, color: "#9ca3af", marginTop: 1 }}>{item.label}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Anomaly alerts */}
                {orgStats.anomalies?.length > 0 && (
                  <div className="card" style={{ padding: 12 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8, color: "#f59e0b" }}>
                      {t("org.editor.alertsSection", { count: orgStats.anomalies.length })}
                    </div>
                    <div style={{ maxHeight: 150, overflowY: "auto" }}>
                      {orgStats.anomalies.map((a: any, i: number) => (
                        <div key={i} style={{
                          display: "flex", gap: 6, alignItems: "flex-start",
                          padding: "4px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                        }}>
                          <span style={{
                            fontSize: 9, padding: "1px 5px", borderRadius: 3, flexShrink: 0,
                            background: a.type === "error" ? "#fef2f2" : a.type === "stuck" ? "#fffbeb" : "#f0f9ff",
                            color: a.type === "error" ? "#dc2626" : a.type === "stuck" ? "#b45309" : "#2563eb",
                            fontWeight: 500,
                          }}>
                            {a.type === "error" ? t("org.editor.alertError") : a.type === "stuck" ? t("org.editor.alertStuck") : t("org.editor.alertBacklog")}
                          </span>
                          <div>
                            <span style={{ fontWeight: 500 }}>{a.role_title}</span>
                            <div style={{ fontSize: 10, color: "#6b7280" }}>{a.message}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Node load table */}
                <div className="card" style={{ padding: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{t("org.editor.nodeLoadTitle")}</div>
                  <div style={{ maxHeight: 200, overflowY: "auto" }}>
                    {(orgStats.per_node || []).map((nd: any) => {
                      const st = nd.status || "idle";
                      const hasAnomaly = orgStats.anomalies?.some((a: any) => a.node_id === nd.id);
                      return (
                        <div key={nd.id} style={{
                          display: "flex", alignItems: "center", gap: 6,
                          padding: "5px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                          background: hasAnomaly ? "#fffbeb08" : undefined,
                        }}
                          onClick={() => { setSelectedNodeId(nd.id); }}
                        >
                          <span style={{
                            width: 8, height: 8, borderRadius: "50%",
                            background: STATUS_COLORS[st] || "#9ca3af", flexShrink: 0,
                            boxShadow: hasAnomaly ? "0 0 6px #f59e0b" : undefined,
                          }} />
                          <span style={{ fontWeight: 500, flex: 1, cursor: "pointer" }}>{nd.role_title}</span>
                          <span style={{
                            fontSize: 9, padding: "1px 4px", borderRadius: 3,
                            background: `${STATUS_COLORS[st] || "#9ca3af"}20`,
                            color: STATUS_COLORS[st] || "#9ca3af",
                          }}>
                            {STATUS_LABELS[st] || st}
                          </span>
                          {nd.idle_seconds != null && nd.idle_seconds > 60 && st === "idle" && (
                            <span style={{ fontSize: 9, color: "#9ca3af" }}>
                              {nd.idle_seconds >= 3600 ? `${Math.floor(nd.idle_seconds / 3600)}h` : `${Math.floor(nd.idle_seconds / 60)}m`}
                            </span>
                          )}
                          {nd.pending_messages > 0 && (
                            <span style={{ fontSize: 9, color: "#dc2626", fontWeight: 600 }}>
                              {nd.pending_messages}
                            </span>
                          )}
                          {nd.current_task && (
                            <span style={{ fontSize: 9, color: "#6b7280", maxWidth: 60, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {stripMd(humanizeTask(nd.current_task, nodes))}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Recent blackboard changes */}
                {orgStats.recent_blackboard?.length > 0 && (
                  <div className="card" style={{ padding: 12 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{t("org.editor.recentBlackboard")}</div>
                    <div style={{ maxHeight: 160, overflowY: "auto" }}>
                      {orgStats.recent_blackboard.map((bb: any, i: number) => {
                        const tc = BB_TYPE_COLORS[bb.memory_type] || "#6b7280";
                        return (
                          <div key={i} style={{
                            padding: "5px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                          }}>
                            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <span style={{
                                fontSize: 11, padding: "1px 5px", borderRadius: 3,
                                background: tc + "20", color: tc, fontWeight: 600,
                              }}>
                                {BB_TYPE_LABELS[bb.memory_type] || bb.memory_type}
                              </span>
                              <span style={{ fontSize: 11, color: "var(--muted)" }}>{(() => { const nd = nodes.find(n => n.id === bb.source_node); return (nd?.data as any)?.role_title || bb.source_node; })()}</span>
                              <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
                                {fmtTime(bb.timestamp)}
                              </span>
                            </div>
                            <div className="bb-entry-content" style={{ marginTop: 3 }}>
                              {mdModules ? (
                                <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>
                                  {bb.content ?? ""}
                                </mdModules.ReactMarkdown>
                              ) : (
                                <div style={{ whiteSpace: "pre-wrap" }}>{bb.content ?? ""}</div>
                              )}
                            </div>
                            {Array.isArray(bb.attachments) && bb.attachments.length > 0 && (
                              <div style={{ marginTop: 3, display: "flex", flexDirection: "column", gap: 2 }}>
                                {bb.attachments.map((att: any, ai: number) => (
                                  <button
                                    key={att.path || ai}
                                    className="btnSmall"
                                    style={{
                                      display: "inline-flex", alignItems: "center", gap: 4,
                                      padding: "2px 8px", borderRadius: 4, fontSize: 11,
                                      background: "rgba(8,145,178,0.08)",
                                      border: "1px solid rgba(8,145,178,0.2)",
                                      color: "#0891b2", cursor: "pointer",
                                    }}
                                    onClick={async () => {
                                      try {
                                        await saveAttachment({
                                          apiUrl: `${apiBaseUrl}/api/files?path=${encodeURIComponent(att.path)}`,
                                          filename: att.filename,
                                        });
                                      } catch (e) {
                                        console.error("File save failed:", e);
                                      }
                                    }}
                                  >
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                                    </svg>
                                    {att.filename}
                                  </button>
                                ))}
                              </div>
                            )}
                            {Array.isArray(bb.tags) && bb.tags.length > 0 && (
                              <div style={{ display: "flex", gap: 3, marginTop: 3, flexWrap: "wrap" }}>
                                {bb.tags.map((t: string) => (
                                  <span key={t} style={{
                                    fontSize: 11, padding: "0 5px", borderRadius: 3,
                                    background: "var(--hover-bg, rgba(100,100,100,0.1))", color: "var(--muted)",
                                  }}>#{t}</span>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {propsTab === "overview" && (
              <fieldset disabled={!!liveMode} style={{ border: "none", margin: 0, padding: 0, minWidth: 0, opacity: liveMode ? 0.5 : 1, display: "flex", flexDirection: "column", gap: 10 }}>
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>{t("org.editor.avatarLabel")}</label>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  {AVATAR_PRESETS.map((av) => {
                    const isSel = selectedNode.avatar === av.id;
                    return (
                      <OrgAvatar
                        key={av.id}
                        avatarId={av.id}
                        size={36}
                        onClick={liveMode ? undefined : () => updateNodeData("avatar", av.id)}
                        style={{
                          cursor: liveMode ? "not-allowed" : "pointer",
                          border: isSel ? "2.5px solid var(--primary)" : "2.5px solid transparent",
                          boxShadow: isSel ? "0 0 0 2px var(--primary)" : "none",
                          opacity: isSel ? 1 : 0.75,
                          transition: "all 0.15s",
                          pointerEvents: liveMode ? "none" : undefined,
                        }}
                      />
                    );
                  })}
                  {/* Upload custom avatar */}
                  <label
                    title={liveMode ? t("org.editor.runningLocked") : t("org.editor.uploadAvatar")}
                    style={{
                      width: 36, height: 36, borderRadius: 8,
                      border: "2px dashed var(--muted, #9ca3af)",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      cursor: liveMode ? "not-allowed" : "pointer",
                      opacity: liveMode ? 0.3 : 0.6,
                      transition: "opacity .15s",
                      fontSize: 18, color: "var(--muted, #9ca3af)",
                      pointerEvents: liveMode ? "none" : undefined,
                    }}
                    onMouseEnter={(e) => { if (!liveMode) e.currentTarget.style.opacity = "1"; }}
                    onMouseLeave={(e) => { if (!liveMode) e.currentTarget.style.opacity = "0.6"; }}
                  >
                    +
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/svg+xml"
                      style={{ display: "none" }}
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        if (file.size > 2 * 1024 * 1024) {
                          alert(t("org.editor.imageTooLarge"));
                          return;
                        }
                        const form = new FormData();
                        form.append("file", file);
                        try {
                          const res = await safeFetch(`${apiBaseUrl}/api/orgs/avatars/upload`, {
                            method: "POST",
                            body: form,
                          });
                          if (res.ok) {
                            const data = await res.json();
                            updateNodeData("avatar", data.url);
                          } else {
                            const err = await res.text();
                            alert(t("org.editor.uploadFailed", { error: String(err) }));
                          }
                        } catch (err) {
                          alert(t("org.editor.uploadFailed", { error: String(err) }));
                        }
                        e.target.value = "";
                      }}
                    />
                  </label>
                </div>
                {/* Show custom avatar preview if currently using uploaded image */}
                {selectedNode.avatar && (selectedNode.avatar.startsWith("/") || selectedNode.avatar.startsWith("http")) && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <OrgAvatar avatarId={selectedNode.avatar} size={48} />
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>{t("org.editor.customAvatar")}</span>
                    <button
                      className="btn btn-sm"
                      style={{ fontSize: 11, padding: "2px 6px" }}
                      onClick={() => updateNodeData("avatar", null)}
                    >
                      {t("org.editor.remove")}
                    </button>
                  </div>
                )}
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>{t("org.editor.roleTitleLabel")}</label>
                <input
                  className="input"
                  value={selectedNode.role_title}
                  onChange={(e) => updateNodeData("role_title", e.target.value)}
                  placeholder={t("org.editor.roleNameFieldPlaceholder")}
                  style={{ fontSize: 13 }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>{t("org.editor.agentSourceLabel")}
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— {t("org.editor.agentSourceHint")}</span>
                </label>
                <div style={{ display: "flex", gap: 6 }}>
                  <select
                    className="input"
                    value={selectedNode.agent_source.startsWith("ref:") ? "ref" : "local"}
                    onChange={(e) => updateNodeData("agent_source", e.target.value === "local" ? "local" : `ref:${selectedNode.agent_profile_id || ""}`)}
                    style={{ fontSize: 13, flex: 1 }}
                  >
                    <option value="local">{t("org.editor.agentSourceLocal")}</option>
                    <option value="ref">{t("org.editor.agentSourceRef")}</option>
                  </select>
                </div>
                {selectedNode.agent_source.startsWith("ref:") && (
                  <>
                    <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginTop: -4 }}>{t("org.editor.selectAgentLabel")}</label>
                    <div style={{ position: "relative" }}>
                      <div
                        className="input"
                        onClick={() => { setAgentDropdownOpen(!agentDropdownOpen); setAgentProfileSearch(""); }}
                        style={{
                          fontSize: 13, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between",
                          background: selectedNode.agent_profile_id ? undefined : "var(--bg-app)",
                        }}
                      >
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                          {(() => {
                            const ap = agentProfiles.find(p => p.id === selectedNode.agent_profile_id);
                            if (!ap) return t("org.editor.selectAgentPlaceholder");
                            return (
                              <>
                                {ap.icon ? <span style={{ fontSize: 16, flexShrink: 0 }}>{ap.icon}</span> : <IconBot size={16} style={{ flexShrink: 0 }} />}
                                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>{ap.name}</span>
                              </>
                            );
                          })()}
                        </span>
                        <IconChevronDown size={12} style={{ flexShrink: 0, opacity: 0.5 }} />
                      </div>
                      {agentDropdownOpen && (
                        <>
                        <div style={{ position: "fixed", inset: 0, zIndex: 99 }} onClick={() => setAgentDropdownOpen(false)} />
                        <div style={{
                          position: "absolute", top: "100%", left: 0, right: 0, zIndex: 100,
                          background: "var(--card-bg, #fff)", border: "1px solid var(--line)",
                          borderRadius: 6, boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                          maxHeight: 240, display: "flex", flexDirection: "column",
                        }}>
                          <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--line)" }}>
                            <input
                              className="input"
                              value={agentProfileSearch}
                              onChange={(e) => setAgentProfileSearch(e.target.value)}
                              placeholder={t("org.editor.searchAgent")}
                              autoFocus
                              style={{ fontSize: 12, width: "100%" }}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </div>
                          <div style={{ overflowY: "auto", flex: 1 }}>
                            {agentProfiles.length === 0 ? (
                              <div style={{ padding: 12, color: "var(--muted)", textAlign: "center", fontSize: 11 }}>
                                {t("org.editor.noAgentsAvailable")}
                              </div>
                            ) : (
                              agentProfiles
                                .filter(ap => {
                                  if (!agentProfileSearch) return true;
                                  const q = agentProfileSearch.toLowerCase();
                                  return ap.name.toLowerCase().includes(q) || ap.id.toLowerCase().includes(q) || (ap.description || "").toLowerCase().includes(q);
                                })
                                .map(ap => (
                                  <div
                                    key={ap.id}
                                    onClick={() => {
                                      updateNodeData("agent_profile_id", ap.id);
                                      updateNodeData("agent_source", `ref:${ap.id}`);
                                      setAgentDropdownOpen(false);
                                    }}
                                    style={{
                                      padding: "6px 10px", cursor: "pointer", fontSize: 12,
                                      display: "flex", alignItems: "center", gap: 8,
                                      background: selectedNode.agent_profile_id === ap.id ? "rgba(14,165,233,0.08)" : undefined,
                                    }}
                                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover, rgba(0,0,0,0.04))")}
                                    onMouseLeave={(e) => (e.currentTarget.style.background = selectedNode.agent_profile_id === ap.id ? "rgba(14,165,233,0.08)" : "")}
                                  >
                                    {ap.icon ? <span style={{ fontSize: 16, flexShrink: 0 }}>{ap.icon}</span> : <IconBot size={16} style={{ flexShrink: 0 }} />}
                                    <div style={{ minWidth: 0, flex: 1 }}>
                                      <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ap.name}</div>
                                      {ap.description && <div style={{ fontSize: 10, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ap.description}</div>}
                                    </div>
                                    {selectedNode.agent_profile_id === ap.id && <IconCheck size={14} style={{ color: "var(--primary)", flexShrink: 0 }} />}
                                  </div>
                                ))
                            )}
                          </div>
                        </div>
                        </>
                      )}
                    </div>
                  </>
                )}
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                  {t("org.editor.roleGoalLabel")}
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— {t("org.editor.roleGoalHint")}</span>
                </label>
                <textarea
                  className="input"
                  value={selectedNode.role_goal}
                  onChange={(e) => updateNodeData("role_goal", e.target.value)}
                  rows={2}
                  placeholder={t("org.editor.roleGoalPlaceholder")}
                  style={{ fontSize: 13, resize: "vertical" }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                  {t("org.editor.roleBackstoryLabel")}
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— {t("org.editor.roleBackstoryHint")}</span>
                </label>
                <textarea
                  className="input"
                  value={selectedNode.role_backstory}
                  onChange={(e) => updateNodeData("role_backstory", e.target.value)}
                  rows={3}
                  placeholder={t("org.editor.roleBackstoryPlaceholder")}
                  style={{ fontSize: 13, resize: "vertical" }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>{t("org.editor.departmentLabel")}</label>
                <input
                  className="input"
                  value={selectedNode.department}
                  onChange={(e) => updateNodeData("department", e.target.value)}
                  style={{ fontSize: 13 }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>{t("org.editor.levelLabel")}</label>
                <input
                  className="input"
                  type="number"
                  min={0}
                  value={selectedNode.level}
                  onChange={(e) => updateNodeData("level", parseInt(e.target.value) || 0)}
                  style={{ fontSize: 13, width: 80 }}
                />
              </fieldset>
            )}

            {propsTab === "identity" && (
              <fieldset disabled={!!liveMode} style={{ border: "none", margin: 0, padding: 0, minWidth: 0, opacity: liveMode ? 0.5 : 1, display: "flex", flexDirection: "column", gap: 10 }}>
                {/* Section 1: Field relationship */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--card-bg, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 6 }}>
                    {t("org.editor.promptStructureTitle")}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.7 }}>
                    <div>{t("org.editor.promptStructureIntro")}</div>
                    <div style={{ marginTop: 4, paddingLeft: 8 }}>
                      <div>1. <b>{t("org.editor.promptPart1Title")}</b> — {t("org.editor.promptPart1Desc")}</div>
                      <div>2. <b>{t("org.editor.promptPart2Title")}</b> — {t("org.editor.promptPart2Desc")}</div>
                      <div>3. <b>{t("org.editor.promptPart3Title")}</b> — {t("org.editor.promptPart3Desc")}</div>
                      <div>4. <b>{t("org.editor.promptPart4Title")}</b> — {t("org.editor.promptPart4Desc")}</div>
                      <div>5. <b>{t("org.editor.promptPart5Title")}</b> — {t("org.editor.promptPart5Desc")}</div>
                      <div>6. <b>{t("org.editor.promptPart6Title")}</b> — {t("org.editor.promptPart6Desc")}</div>
                    </div>
                    <div style={{ marginTop: 6 }}>
                      {t("org.editor.promptPriority")}
                    </div>
                  </div>
                </div>

                {/* Section 2: Custom prompt */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--card-bg, #fff)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                      {t("org.editor.customPromptTitle")}
                    </div>
                    <button
                      className="btnSmall"
                      style={{ fontSize: 10, padding: "2px 8px" }}
                      onClick={() => {
                        if (selectedNode.custom_prompt && !confirm(t("org.editor.confirmOverwritePrompt"))) return;
                        const tpl = `You are an experienced ${selectedNode.role_title || "professional"}.\n\n## Core Responsibilities\n- ${selectedNode.role_goal || "To be defined"}\n\n## Work Style\n- Communicate concisely, lead with conclusions\n- Record important decisions on the org blackboard\n- Proactively report progress to superiors\n\n## Professional Background\n${selectedNode.role_backstory || "Describe the role's professional background, experience, and expertise here"}`;
                        updateNodeData("custom_prompt", tpl);
                      }}
                    >
                      {t("org.editor.fillTemplate")}
                    </button>
                  </div>
                  <textarea
                    className="input"
                    value={selectedNode.custom_prompt}
                    onChange={(e) => updateNodeData("custom_prompt", e.target.value)}
                    rows={10}
                    placeholder={t("org.editor.customPromptPlaceholder")}
                    style={{ fontSize: 12, resize: "vertical", fontFamily: "monospace", lineHeight: 1.5, minHeight: 120 }}
                  />
                  <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                    {selectedNode.custom_prompt
                      ? t("org.editor.hasCustomPrompt", { count: selectedNode.custom_prompt.length })
                      : t("org.editor.noCustomPrompt")}
                  </div>
                </div>

                {/* Section 3: Prompt preview */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--card-bg, #fff)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                      {t("org.editor.promptPreviewTitle")}
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                      {fullPromptPreview !== null && (
                        <button
                          className="btnSmall"
                          style={{ fontSize: 10, padding: "2px 8px" }}
                          onClick={() => setFullPromptPreview(null)}
                        >
                          {t("org.editor.previewBrief")}
                        </button>
                      )}
                      <button
                        className="btnSmall"
                        style={{ fontSize: 10, padding: "2px 8px" }}
                        disabled={promptPreviewLoading}
                        onClick={async () => {
                          if (!currentOrg) return;
                          setPromptPreviewLoading(true);
                          try {
                            const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNode.id}/prompt-preview`);
                            if (resp.ok) {
                              const data = await resp.json();
                              setFullPromptPreview(data.full_prompt);
                            } else {
                              setFullPromptPreview(t("org.editor.previewFetchFailed"));
                            }
                          } catch {
                            setFullPromptPreview(t("org.editor.previewFetchError"));
                          }
                          setPromptPreviewLoading(false);
                        }}
                      >
                        {promptPreviewLoading ? "..." : t("org.editor.previewFull")}
                      </button>
                    </div>
                  </div>
                  <div style={{
                    fontSize: 11, color: "var(--fg)", lineHeight: 1.6,
                    background: "var(--bg-code, #f5f5f5)", borderRadius: 6,
                    padding: "8px 10px", maxHeight: 300, overflowY: "auto",
                    fontFamily: "monospace", whiteSpace: "pre-wrap",
                  }}>
                    {fullPromptPreview !== null
                      ? fullPromptPreview
                      : selectedNode.custom_prompt
                        ? selectedNode.custom_prompt
                        : `You are ${selectedNode.role_title || "(unnamed role)"}. ${selectedNode.role_goal ? `Goal: ${selectedNode.role_goal}. ` : ""}${selectedNode.role_backstory ? `Background: ${selectedNode.role_backstory}.` : ""}`}
                  </div>
                  {fullPromptPreview === null && (
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                      {t("org.editor.previewRoleDescHint")}
                    </div>
                  )}
                  {fullPromptPreview !== null && (
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                      {t("org.editor.previewFullHint", { chars: fullPromptPreview.length })}
                    </div>
                  )}
                </div>

                {/* Section 4: Identity files info */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--card-bg, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 4 }}>
                    {t("org.editor.identityFilesTitle")}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
                    {t("org.editor.identityFilesDesc")}
                    <div style={{ fontFamily: "monospace", fontSize: 10, marginTop: 4, paddingLeft: 8 }}>
                      <div>nodes/{selectedNode.id}/identity/ROLE.md — {t("org.editor.identityFileRole")}</div>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--warning, #b8860b)", marginTop: 6 }}>
                      {t("org.editor.identityFilesWarning")}
                    </div>
                  </div>
                </div>
                </fieldset>
            )}

            {propsTab === "capabilities" && (
              <fieldset disabled={!!liveMode} style={{ border: "none", margin: 0, padding: 0, minWidth: 0, opacity: liveMode ? 0.5 : 1, display: "flex", flexDirection: "column", gap: 10 }}>
                {/* ── Workbench banner: appears only for plugin-backed leaf nodes ── */}
                {selectedNode.plugin_origin && (
                  <div
                    style={{
                      border: "1px solid #a5f3fc",
                      background: "#ecfeff",
                      color: "#0e7490",
                      borderRadius: 6,
                      padding: "10px 12px",
                      fontSize: 12,
                      lineHeight: 1.6,
                      display: "flex",
                      flexDirection: "column",
                      gap: 6,
                    }}
                    role="note"
                    aria-label={t("org.editor.workbenchBadge", "工作台")}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 600 }}>
                      <IconPlug size={13} />
                      {t("org.editor.workbenchBadge", "工作台")}：
                      {selectedNode.plugin_origin.plugin_id}
                      {selectedNode.plugin_origin.version
                        ? ` · v${selectedNode.plugin_origin.version}`
                        : ""}
                    </div>
                    <div style={{ fontSize: 11 }}>
                      {t(
                        "org.editor.workbenchBannerDesc",
                        "这是工作台节点。工具清单由工作台决定，请勿手动调整；工作台节点必须保持为叶子节点，否则保存时将被拒绝。",
                      )}
                    </div>
                    {(selectedNode.external_tools || []).length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                          {t("org.editor.workbenchTools", "工作台工具（只读）")}
                        </div>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                          {selectedNode.external_tools.map((name: string) => (
                            <span
                              key={name}
                              style={{
                                fontSize: 10,
                                padding: "1px 6px",
                                borderRadius: 3,
                                background: "#fff",
                                color: "#0e7490",
                                border: "1px solid #a5f3fc",
                                fontFamily: "var(--font-mono, monospace)",
                              }}
                            >
                              {name}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                {/* ── Section 1: 执行工具类目 ── */}
                <Card
                  className="gap-0 overflow-hidden py-0"
                  style={selectedNode.plugin_origin ? { opacity: 0.55, pointerEvents: "none" } : undefined}
                  aria-disabled={selectedNode.plugin_origin ? true : undefined}
                >
                  <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
                    <div className="min-w-0">
                      <CardTitle className="text-sm">{t("org.editor.executionTools")}</CardTitle>
                      <CardDescription className="mt-1 text-[11px]">
                        {t("org.editor.executionToolsDesc")}
                      </CardDescription>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 shrink-0 px-2 text-[11px]"
                      onClick={() => {
                        const title = (selectedNode.role_title || "").toLowerCase();
                        let preset: string[] = ["research", "memory"];
                        if (title.includes("ceo") || title.includes("执行官")) preset = ["research", "planning", "memory"];
                        else if (title.includes("cto") || title.includes("技术总监")) preset = ["research", "planning", "filesystem", "memory"];
                        else if (title.includes("cmo") || title.includes("市场")) preset = ["research", "planning", "memory"];
                        else if (title.includes("cpo") || title.includes("产品总监")) preset = ["research", "planning", "memory"];
                        else if (title.includes("工程师") || title.includes("开发") || title.includes("dev")) preset = ["filesystem", "memory"];
                        else if (title.includes("运营") || title.includes("content")) preset = ["research", "filesystem", "memory"];
                        else if (title.includes("设计") || title.includes("design")) preset = ["browser", "filesystem"];
                        else if (title.includes("产品经理") || title.includes("pm")) preset = ["research", "planning", "memory"];
                        else if (title.includes("seo")) preset = ["research", "memory"];
                        else if (title.includes("devops")) preset = ["filesystem", "memory"];
                        updateNodeData("external_tools", preset);
                      }}
                      title={t("org.editor.autoRecommend")}
                    >
                      {t("org.editor.autoRecommendBtn")}
                    </Button>
                  </div>
                  <CardContent className="grid grid-cols-2 gap-2 px-3 py-3">
                    {[
                      { key: "research", label: t("org.editor.toolSearch") },
                      { key: "planning", label: t("org.editor.toolPlan") },
                      { key: "filesystem", label: t("org.editor.toolFile") },
                      { key: "memory", label: t("org.editor.toolMemory") },
                      { key: "browser", label: t("org.editor.toolBrowser") },
                      { key: "communication", label: t("org.editor.toolComm") },
                    ].map((cat) => {
                      const checked = (selectedNode.external_tools || []).includes(cat.key);
                      return (
                        <label
                          key={cat.key}
                          className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-xs transition-colors"
                          style={{
                            borderColor: checked ? "color-mix(in srgb, var(--primary) 45%, var(--line))" : "var(--line)",
                            background: checked ? "color-mix(in srgb, var(--primary) 10%, transparent)" : "var(--card-bg)",
                          }}
                        >
                          <Checkbox
                            checked={checked}
                            onCheckedChange={() => {
                              const cur = selectedNode.external_tools || [];
                              const next = checked
                                ? cur.filter((s: string) => s !== cat.key)
                                : [...cur, cat.key];
                              updateNodeData("external_tools", next);
                            }}
                          />
                          <span className="truncate">{cat.label}</span>
                        </label>
                      );
                    })}
                  </CardContent>
                  {/* 基础文件工具开关：与上面 6 个类目正交 —— 即便没勾选
                      "文件/命令"，只要打开这个开关就给节点放行 write_file /
                      read_file / edit_file / list_directory，让需要交付文件
                      的角色可以把交付物落盘后走 file_attachments 提交。
                      run_shell / 删除等高风险工具仍由"文件/命令"类目控制。 */}
                  <div className="border-t px-3 py-3" style={{ borderColor: "var(--line)" }}>
                    {(() => {
                      const checked = selectedNode.enable_file_tools !== false;
                      return (
                        <label
                          className="flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-xs transition-colors"
                          style={{
                            borderColor: checked ? "color-mix(in srgb, var(--primary) 45%, var(--line))" : "var(--line)",
                            background: checked ? "color-mix(in srgb, var(--primary) 10%, transparent)" : "var(--card-bg)",
                          }}
                        >
                          <Checkbox
                            checked={checked}
                            onCheckedChange={(value) => updateNodeData("enable_file_tools", value === true)}
                            className="mt-[2px]"
                          />
                          <div className="flex flex-col gap-1">
                            <span className="font-medium">{t("org.editor.basicFileTools")}</span>
                            <span className="text-[11px] leading-4 text-muted-foreground">
                              {t("org.editor.basicFileToolsDesc")}
                            </span>
                          </div>
                        </label>
                      );
                    })()}
                  </div>
                </Card>

                {/* ── Section 2: MCP 服务器 ── */}
                <Card className="gap-0 overflow-hidden py-0">
                  <CardHeader className="px-4 py-3" style={{ borderBottom: "1px solid var(--line)" }}>
                    <CardTitle className="text-sm">{t("org.editor.mcpServersTitle")}</CardTitle>
                    <CardDescription className="text-[11px]">
                      {t("org.editor.mcpServersDesc")}
                    </CardDescription>
                  </CardHeader>
                  {availableMcpServers.length > 3 && (
                    <CardContent className="px-3 pt-3 pb-0">
                      <ShadInput
                        placeholder={t("org.editor.mcpServers")}
                        value={mcpSearch}
                        onChange={(e) => setMcpSearch(e.target.value)}
                        className="h-8 text-xs"
                      />
                    </CardContent>
                  )}
                  {availableMcpServers.length > 0 ? (
                    <CardContent className="max-h-[150px] space-y-2 overflow-y-auto px-3 py-3">
                      {availableMcpServers
                        .filter((srv) => !mcpSearch || srv.name.toLowerCase().includes(mcpSearch.toLowerCase()))
                        .map((srv) => {
                        const checked = selectedNode.mcp_servers.includes(srv.name);
                        return (
                          <label
                            key={srv.name}
                            className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-xs transition-colors"
                            style={{
                              borderColor: checked ? "color-mix(in srgb, var(--primary) 45%, var(--line))" : "var(--line)",
                              background: checked ? "color-mix(in srgb, var(--primary) 10%, transparent)" : "var(--card-bg)",
                            }}
                          >
                            <Checkbox
                              checked={checked}
                              onCheckedChange={() => {
                                const next = checked
                                  ? selectedNode.mcp_servers.filter((s: string) => s !== srv.name)
                                  : [...selectedNode.mcp_servers, srv.name];
                                updateNodeData("mcp_servers", next);
                              }}
                            />
                            <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
                              {srv.name}
                            </span>
                            <Badge style={{
                              fontSize: 9, padding: "1px 5px", borderRadius: 3, flexShrink: 0,
                              background: srv.status === "connected" ? "#dcfce7" : "#f3f4f6",
                              color: srv.status === "connected" ? "#166534" : "#9ca3af",
                            }}>
                              {srv.status === "connected" ? t("org.editor.mcpOnline") : t("org.editor.mcpOffline")}
                            </Badge>
                          </label>
                        );
                      })}
                    </CardContent>
                  ) : (
                    <CardContent className="px-4 py-3 text-[11px] text-muted-foreground">
                      {t("org.editor.noMcpServers")}
                    </CardContent>
                  )}
                  {selectedNode.mcp_servers.length > 0 && (
                    <div className="border-t px-4 py-2 text-[10px] text-muted-foreground">
                      {t("org.editor.selectedCount", { count: selectedNode.mcp_servers.length })}
                    </div>
                  )}
                </Card>

                {/* ── Section 3: Skills ── */}
                <Card className="gap-0 overflow-hidden py-0">
                  <CardHeader className="px-4 py-3" style={{ borderBottom: "1px solid var(--line)" }}>
                    <CardTitle className="text-sm">{t("org.editor.skillsTitle")}</CardTitle>
                    <CardDescription className="text-[11px]">
                      {t("org.editor.skillsDesc")}
                    </CardDescription>
                  </CardHeader>
                  {availableSkills.length > 3 && (
                    <CardContent className="px-3 pt-3 pb-0">
                      <ShadInput
                        placeholder={t("org.editor.skills")}
                        value={skillSearch}
                        onChange={(e) => setSkillSearch(e.target.value)}
                        className="h-8 text-xs"
                      />
                    </CardContent>
                  )}
                  {availableSkills.length > 0 ? (
                    <CardContent className="max-h-[260px] space-y-3 overflow-y-auto px-3 py-3">
                      {(() => {
                        const filtered = availableSkills.filter((skill) => {
                          if (!skillSearch) return true;
                          const q = skillSearch.toLowerCase();
                          const ni = skill.name_i18n;
                          const di = skill.description_i18n;
                          const nameStr = typeof ni === "object" && ni ? ((ni as any).zh || (ni as any).en || "") : (ni || "");
                          const descStr = typeof di === "object" && di ? ((di as any).zh || (di as any).en || "") : (di || "");
                          return nameStr.toLowerCase().includes(q)
                            || skill.name.toLowerCase().includes(q)
                            || descStr.toLowerCase().includes(q)
                            || (skill.description || "").toLowerCase().includes(q)
                            || ((skill.category || "").toLowerCase().includes(q));
                        });
                        // 按 category 分组（参考 hermes 范式：分类字典序，组内按 name 字典序）
                        const grouped: Record<string, typeof filtered> = {};
                        for (const s of filtered) {
                          const k = s.category || "Uncategorized";
                          (grouped[k] ||= [] as typeof filtered).push(s);
                        }
                        const sortedNames = Object.keys(grouped).sort((a, b) => a.localeCompare(b));
                        for (const k of sortedNames) grouped[k].sort((a, b) => a.name.localeCompare(b.name));

                        const renderSkillRow = (skill: typeof filtered[number]) => {
                          const checked = selectedNode.skills.includes(skill.name);
                          const rawName = skill.name_i18n;
                          const displayName = (typeof rawName === "object" && rawName !== null)
                            ? (rawName as any).zh || (rawName as any).en || skill.name
                            : rawName || skill.name;
                          const rawDesc = skill.description_i18n;
                          const displayDesc = (typeof rawDesc === "object" && rawDesc !== null)
                            ? (rawDesc as any).zh || (rawDesc as any).en || skill.description || ""
                            : rawDesc || skill.description || "";
                          return (
                            <label
                              key={skill.name}
                              className="flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-xs transition-colors"
                              style={{
                                borderColor: checked ? "color-mix(in srgb, var(--primary) 45%, var(--line))" : "var(--line)",
                                background: checked ? "color-mix(in srgb, var(--primary) 10%, transparent)" : "var(--card-bg)",
                              }}
                            >
                              <Checkbox
                                checked={checked}
                                onCheckedChange={() => {
                                  const next = checked
                                    ? selectedNode.skills.filter((s: string) => s !== skill.name)
                                    : [...selectedNode.skills, skill.name];
                                  updateNodeData("skills", next);
                                }}
                                className="mt-0.5"
                              />
                              <div className="min-w-0 flex-1 overflow-hidden">
                                <div className="overflow-hidden text-ellipsis whitespace-nowrap">
                                  {displayName}
                                </div>
                                {displayDesc && (
                                  <div className="overflow-hidden text-ellipsis whitespace-nowrap text-[10px] text-muted-foreground">
                                    {displayDesc}
                                  </div>
                                )}
                              </div>
                            </label>
                          );
                        };

                        return sortedNames.map((catName) => {
                          const items = grouped[catName];
                          const groupNames = items.map(s => s.name);
                          const allChecked = groupNames.every(n => selectedNode.skills.includes(n));
                          return (
                            <div key={catName} className="flex flex-col gap-1.5">
                              <div className="flex items-center gap-2">
                                <div className="text-[11px] font-semibold text-foreground/80">
                                  {catName}
                                </div>
                                <span className="text-[10px] text-muted-foreground">{items.length}</span>
                                <div className="flex-1" />
                                <button
                                  type="button"
                                  className="text-[10px] text-muted-foreground hover:text-foreground"
                                  onClick={() => {
                                    if (allChecked) {
                                      // 反选：仅作用于 agent profile 的 enabled_skills，
                                      // **不**下发到全局 allowlist（这是 OrgEditor 的语义）
                                      const next = selectedNode.skills.filter(
                                        (s: string) => !groupNames.includes(s)
                                      );
                                      updateNodeData("skills", next);
                                    } else {
                                      const next = Array.from(
                                        new Set([...selectedNode.skills, ...groupNames])
                                      );
                                      updateNodeData("skills", next);
                                    }
                                  }}
                                >
                                  {allChecked ? t("org.editor.deselectAll") : t("org.editor.selectAll")}
                                </button>
                              </div>
                              <div className="flex flex-col gap-1.5">
                                {items.map(renderSkillRow)}
                              </div>
                            </div>
                          );
                        });
                      })()}
                    </CardContent>
                  ) : (
                    <CardContent className="px-4 py-3 text-[11px] text-muted-foreground">
                      {t("org.editor.noSkills")}
                    </CardContent>
                  )}
                  {selectedNode.skills.length > 0 && (
                    <div className="border-t px-4 py-2 text-[10px] text-muted-foreground">
                      {t("org.editor.selectedCount", { count: selectedNode.skills.length })}
                    </div>
                  )}
                </Card>

                {/* ── 需要启用 MCP 工具类目提示 ── */}
                {selectedNode.mcp_servers.length > 0 && !(selectedNode.external_tools || []).includes("mcp") && (
                  <div style={{
                    fontSize: 10, color: "#b45309", background: "#fffbeb",
                    padding: "6px 10px", borderRadius: 6, border: "1px solid #fde68a",
                    lineHeight: 1.5,
                  }}>
                    {t("org.editor.mcpNotEnabled")}
                    <Button
                      variant="outline"
                      size="sm"
                      className="ml-1 h-6 px-2 text-[10px] align-middle"
                      onClick={() => {
                        const cur = selectedNode.external_tools || [];
                        if (!cur.includes("mcp")) updateNodeData("external_tools", [...cur, "mcp"]);
                      }}
                    >
                      {t("org.editor.enableNow")}
                    </Button>
                  </div>
                )}
                </fieldset>
            )}

            {propsTab === "capabilities" && (
              <fieldset disabled={!!liveMode} style={{ border: "none", margin: 0, padding: 0, minWidth: 0, opacity: liveMode ? 0.5 : 1, display: "flex", flexDirection: "column", gap: 14 }}>
                {/* Performance section */}
                <Card className="gap-0 py-0">
                  <CardHeader className="px-4 py-3">
                    <CardTitle className="text-sm">{t("org.editor.performanceLimits")}</CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-3 px-4 pb-4">
                    <div>
                      <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">{t("org.editor.concurrentTasks")}</ShadLabel>
                      <ShadInput
                        type="number"
                        min={1}
                        value={selectedNode.max_concurrent_tasks}
                        onChange={(e) => updateNodeData("max_concurrent_tasks", parseInt(e.target.value) || 1)}
                        className="h-8 text-xs"
                      />
                    </div>
                    <div>
                      <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">{t("org.editor.timeoutSeconds")}</ShadLabel>
                      <ShadInput
                        type="number"
                        min={30}
                        value={selectedNode.timeout_s}
                        onChange={(e) => updateNodeData("timeout_s", parseInt(e.target.value) || 300)}
                        className="h-8 text-xs"
                      />
                    </div>
                  </CardContent>
                </Card>

                {/* Auto-clone section */}
                <Card className="gap-0 py-0">
                  <div className="flex items-start justify-between gap-3 px-4 py-3">
                    <div className="min-w-0">
                      <CardTitle className="text-sm">{t("org.editor.autoClone")}</CardTitle>
                      <CardDescription className="mt-1 text-[11px] leading-5">
                        {t("org.editor.autoCloneDesc")}
                      </CardDescription>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 pt-0.5">
                      <ShadLabel className="cursor-pointer text-[11px] text-muted-foreground" htmlFor="auto-clone-enabled">{t("org.editor.enable")}</ShadLabel>
                      <Switch
                        id="auto-clone-enabled"
                        checked={selectedNode.auto_clone_enabled || false}
                        onCheckedChange={(checked) => updateNodeData("auto_clone_enabled", checked)}
                      />
                    </div>
                  </div>
                  <CardContent className="space-y-3 px-4 pb-4">
                  {selectedNode.auto_clone_enabled && (
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <div>
                        <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">{t("org.editor.cloneThreshold")}</ShadLabel>
                        <ShadInput
                          type="number"
                          min={2}
                          value={selectedNode.auto_clone_threshold || 3}
                          onChange={(e) => updateNodeData("auto_clone_threshold", parseInt(e.target.value) || 3)}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div>
                        <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">{t("org.editor.maxClones")}</ShadLabel>
                        <ShadInput
                          type="number"
                          min={1}
                          max={5}
                          value={selectedNode.auto_clone_max || 3}
                          onChange={(e) => updateNodeData("auto_clone_max", parseInt(e.target.value) || 3)}
                          className="h-8 text-xs"
                        />
                      </div>
                    </div>
                  )}
                  <div className="text-[11px] leading-5 text-muted-foreground">
                    {t("org.editor.autoCloneDetailDesc")}
                  </div>
                  </CardContent>
                </Card>

                {/* Permissions section */}
                <Card className="gap-0 overflow-hidden py-0">
                  <CardHeader className="px-4 py-3" style={{ borderBottom: "1px solid var(--line)" }}>
                    <CardTitle className="text-sm">{t("org.editor.permissionsTitle")}</CardTitle>
                    <CardDescription className="text-[11px]">
                      {t("org.editor.permissionsDesc")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-2 px-3 py-3">
                    {([
                      { key: "can_delegate", label: t("org.editor.canDelegate") },
                      { key: "can_escalate", label: t("org.editor.canEscalate") },
                      { key: "can_request_scaling", label: t("org.editor.canRequestScaling") },
                      { key: "ephemeral", label: t("org.editor.isEphemeral") },
                    ] as const).map(({ key, label }) => {
                      const checked = !!selectedNode[key];
                      return (
                        <label
                          key={key}
                          className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-xs transition-colors"
                          style={{
                            borderColor: checked ? "color-mix(in srgb, var(--primary) 45%, var(--line))" : "var(--line)",
                            background: checked ? "color-mix(in srgb, var(--primary) 10%, transparent)" : "var(--card-bg)",
                          }}
                        >
                          <Checkbox
                            checked={checked}
                            onCheckedChange={(value) => updateNodeData(key, value === true)}
                          />
                          <span>{label}</span>
                        </label>
                      );
                    })}
                  </CardContent>
                </Card>

                {/* LLM endpoint */}
                <Card className="gap-0 py-0">
                  <CardHeader className="px-4 py-3">
                    <CardTitle className="text-sm">{t("org.editor.llmEndpointTitle")}</CardTitle>
                    <CardDescription className="text-[11px]">
                      {t("org.editor.llmEndpointDesc")}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3 px-4 pb-4">
                    <ShadInput
                      value={selectedNode.preferred_endpoint || ""}
                      onChange={(e) => {
                        const value = e.target.value || null;
                        updateNodeData("preferred_endpoint", value);
                        if (!value) updateNodeData("endpoint_policy", "prefer");
                      }}
                      placeholder={t("org.editor.llmEndpointPlaceholder")}
                      className="h-8 text-xs"
                    />
                    <div className="space-y-1.5">
                      <ShadLabel className="text-xs opacity-70">{t("org.editor.llmEndpointPolicy")}</ShadLabel>
                      <select
                        value={selectedNode.endpoint_policy || "prefer"}
                        onChange={(e) => updateNodeData("endpoint_policy", e.target.value)}
                        className="h-8 w-full rounded-md border border-input bg-background px-3 text-xs"
                        disabled={!selectedNode.preferred_endpoint}
                      >
                        <option value="prefer">{t("org.editor.llmEndpointPolicyPrefer")}</option>
                        <option value="require">{t("org.editor.llmEndpointPolicyRequire")}</option>
                      </select>
                      <p className="text-[11px] text-muted-foreground">
                        {t("org.editor.llmEndpointPolicyHint")}
                      </p>
                    </div>
                  </CardContent>
                </Card>
                </fieldset>
            )}

          </div>
        </div>
      )}

      {/* ── Right Panel: Edge Properties ── */}
      {selectedEdge && !selectedNode && showRightPanel && (
        <div
          className="flex flex-col gap-4 border-l border-border bg-background overflow-y-auto"
          style={{
            width: isMobile ? "85%" : 280,
            maxWidth: isMobile ? 360 : 280,
            flexShrink: 0,
            scrollbarGutter: "stable",
            padding: 16,
            position: isMobile ? "absolute" : "relative",
            zIndex: isMobile ? 50 : "auto",
            right: 0, top: 0, bottom: 0,
            boxShadow: isMobile ? "-4px 0 12px rgba(0,0,0,0.15)" : "none",
            borderLeft: isMobile ? "none" : undefined,
          }}
        >
          {/* Header */}
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">{t("org.editor.edgeProperties")}</span>
            <Button variant="ghost" size="icon-xs" onClick={() => setSelectedEdgeId(null)}>
              <XIcon className="size-3.5" />
            </Button>
          </div>

          {/* Source / Target */}
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs leading-relaxed text-muted-foreground">
            <div>{t("org.editor.edgeSource")}: <strong className="text-foreground">{(() => { const n = nodes.find(n => n.id === selectedEdge.source); return (n?.data as any)?.role_title || selectedEdge.source; })()}</strong></div>
            <div>{t("org.editor.edgeTarget")}: <strong className="text-foreground">{(() => { const n = nodes.find(n => n.id === selectedEdge.target); return (n?.data as any)?.role_title || selectedEdge.target; })()}</strong></div>
          </div>

          {/* Edge type */}
          <div className="space-y-2">
            <ShadLabel className="text-xs">{t("org.editor.edgeTypeLabel")}</ShadLabel>
            <ToggleGroup
              type="single"
              value={selectedEdge.edge_type || "hierarchy"}
              onValueChange={(v) => { if (v) updateEdgeData("edge_type", v); }}
              className="flex flex-wrap gap-1.5"
            >
              {([
                { key: "hierarchy", label: t("org.editor.edgeTypeHierarchy"), color: EDGE_COLORS.hierarchy },
                { key: "collaborate", label: t("org.editor.edgeTypeCollaborate"), color: EDGE_COLORS.collaborate },
                { key: "escalate", label: t("org.editor.edgeTypeEscalate"), color: EDGE_COLORS.escalate },
                { key: "consult", label: t("org.editor.edgeTypeConsult"), color: EDGE_COLORS.consult || "var(--muted)" },
              ] as const).map((et) => (
                <ToggleGroupItem
                  key={et.key}
                  value={et.key}
                  size="sm"
                  className="h-7 gap-1.5 px-2.5 text-xs data-[state=on]:font-semibold"
                  style={selectedEdge.edge_type === et.key ? { color: et.color, borderColor: et.color } : undefined}
                >
                  <span className="inline-block h-0.5 w-2.5 rounded-full" style={{ background: et.color }} />
                  {et.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          {/* Label */}
          <div className="space-y-2">
            <ShadLabel className="text-xs" htmlFor="edge-label">{t("org.editor.edgeLabelField")}</ShadLabel>
            <ShadInput
              id="edge-label"
              className="h-8 text-xs"
              placeholder={t("org.editor.edgeLabelPlaceholder")}
              value={selectedEdge.label || ""}
              onChange={(e) => updateEdgeData("label", e.target.value)}
            />
          </div>

          {/* Bidirectional */}
          <div className="flex items-center justify-between rounded-md border border-border px-3 py-2.5">
            <div className="space-y-0.5">
              <ShadLabel className="text-xs cursor-pointer" htmlFor="edge-bidir">{t("org.editor.bidirectional")}</ShadLabel>
              <p className="text-[11px] text-muted-foreground leading-tight">{t("org.editor.bidirectionalHint")}</p>
            </div>
            <Switch
              id="edge-bidir"
              checked={selectedEdge.bidirectional ?? true}
              onCheckedChange={(v) => updateEdgeData("bidirectional", v)}
            />
          </div>

          {/* Priority */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <ShadLabel className="text-xs">{t("org.editor.priority")}</ShadLabel>
              <Badge variant="secondary" className="h-5 text-[10px] tabular-nums">{selectedEdge.priority ?? 0}</Badge>
            </div>
            <Slider
              min={0} max={10} step={1}
              value={[selectedEdge.priority ?? 0]}
              onValueChange={([v]) => updateEdgeData("priority", v)}
            />
          </div>

          {/* Bandwidth limit */}
          <div className="space-y-2">
            <ShadLabel className="text-xs" htmlFor="edge-bw">{t("org.editor.bandwidthLimit")}</ShadLabel>
            <ShadInput
              id="edge-bw"
              type="number" min={1} max={999}
              className="h-8 w-24 text-xs tabular-nums"
              value={selectedEdge.bandwidth_limit ?? 60}
              onChange={(e) => updateEdgeData("bandwidth_limit", Number(e.target.value))}
            />
          </div>

          {/* Delete */}
          <div className="mt-2 border-t border-border pt-3">
            <Button
              variant="destructive"
              size="sm"
              className="w-full gap-1.5 text-xs"
              onClick={handleDeleteEdge}
            >
              <IconTrash size={12} /> {t("org.editor.deleteEdgeBtn")}
            </Button>
          </div>
        </div>
      )}

      {/* ── Right Panel: Org Blackboard (standalone, triggered by FAB) ── */}
      {currentOrg && !isMobile && showBlackboardPanel && (
        <>
          <div
            onClick={() => setShowBlackboardPanel(false)}
            style={{ position: "absolute", inset: 0, zIndex: 35, cursor: "default" }}
          />
          <div style={{ width: 520, flexShrink: 0, borderLeft: "1px solid var(--line)", display: "flex", flexDirection: "column", background: "var(--bg-app)", animation: "org-panel-in 0.3s cubic-bezier(0.4,0,0.2,1) 0s both", zIndex: 36 }}>
            <OrgBlackboardPanel
              ref={bbPanelRef}
              orgId={currentOrg.id}
              apiBaseUrl={apiBaseUrl}
              nodes={nodes}
              fullWidth
              onClose={() => setShowBlackboardPanel(false)}
            />
          </div>
        </>
      )}

      {/* ── Right Panel: Org Blackboard (second-layer drawer, alongside settings) ── */}
      {currentOrg && !selectedNode && !selectedEdge && !isMobile && showRightPanel && !showBlackboardPanel && (
        <OrgBlackboardPanel
          ref={bbPanelRef}
          orgId={currentOrg.id}
          apiBaseUrl={apiBaseUrl}
          nodes={nodes}
        />
      )}

      {/* ── Right Panel: Org Settings (when no node/edge selected) ── */}
      {currentOrg && !selectedNode && !selectedEdge && !isMobile && showRightPanel && (
        <div
          style={{
            width: 300,
            borderLeft: "1px solid var(--line)",
            overflowY: "auto",
            scrollbarGutter: "stable",
            background: "var(--bg-app)",
            flexShrink: 0,
            padding: 12,
            animation: "org-panel-in 0.3s cubic-bezier(0.4,0,0.2,1) 0.05s both",
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>{t("org.editor.orgSettingsTitle")}</div>

          {/* ── 运行模式 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{t("org.editor.runMode")}</div>
            <div style={{ display: "flex", gap: 4 }}>
              <button
                className="btnSmall"
                style={{
                  flex: 1, fontSize: 12, padding: "6px 10px",
                  background: ((currentOrg as any).operation_mode || "command") === "command" ? "var(--primary)" : "var(--bg-subtle, var(--bg-card))",
                  color: ((currentOrg as any).operation_mode || "command") === "command" ? "#fff" : "var(--text)",
                  border: "1px solid var(--line)",
                  borderRadius: 4,
                }}
                onClick={() => setCurrentOrg({ ...currentOrg, operation_mode: "command" } as any)}
              >
                {t("org.editor.commandMode")}
              </button>
              <button
                className="btnSmall"
                style={{
                  flex: 1, fontSize: 12, padding: "6px 10px",
                  background: ((currentOrg as any).operation_mode || "command") === "autonomous" ? "var(--primary)" : "var(--bg-subtle, var(--bg-card))",
                  color: ((currentOrg as any).operation_mode || "command") === "autonomous" ? "#fff" : "var(--text)",
                  border: "1px solid var(--line)",
                  borderRadius: 4,
                }}
                onClick={() => setCurrentOrg({ ...currentOrg, operation_mode: "autonomous" } as any)}
              >
                {t("org.editor.autonomousMode")}
              </button>
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.5 }}>
              {((currentOrg as any).operation_mode || "command") === "command"
                ? t("org.editor.commandModeDesc")
                : t("org.editor.autonomousModeDesc")}
            </div>
          </div>

          {/* ── 工作目录 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{t("org.editor.workDir")}</div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                className="input"
                style={{ flex: 1, fontSize: 12 }}
                placeholder={t("org.editor.workDirDefault")}
                value={(currentOrg as any).workspace_dir || ""}
                onChange={(e) => setCurrentOrg({ ...currentOrg, workspace_dir: e.target.value } as any)}
              />
              {IS_TAURI && (
                <button
                  className="btnSmall"
                  style={{ fontSize: 12, padding: "5px 10px", whiteSpace: "nowrap" }}
                  onClick={async () => {
                    const selected = await openFileDialog({ directory: true, title: t("org.editor.workDir") });
                    if (selected) setCurrentOrg({ ...currentOrg, workspace_dir: selected } as any);
                  }}
                >
                  {t("org.editor.browse")}
                </button>
              )}
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.5 }}>
              {t("orgEditor.outputPathHint")}
            </div>
          </div>

          {/* ── 交付兜底 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
              {t("org.editor.deliveryFallback")}
            </div>
            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer" }}>
              <input
                type="checkbox"
                style={{ marginTop: 3 }}
                checked={(currentOrg as any).auto_persist_final_answer !== false}
                onChange={(e) =>
                  setCurrentOrg({
                    ...currentOrg,
                    auto_persist_final_answer: e.target.checked,
                  } as any)
                }
              />
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontSize: 12 }}>
                  {t("org.editor.autoPersistLabel")}
                </span>
                <span style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
                  {t("org.editor.autoPersistHint")}
                </span>
              </div>
            </label>
          </div>

          {/* ── 核心业务 (仅自主模式) ── */}
          {((currentOrg as any).operation_mode || "command") === "autonomous" && (
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setBizCollapsed(!bizCollapsed)}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                {t("org.editor.coreBusiness")}
                {bizCollapsed && (currentOrg.core_business || "").trim() && (
                  <span style={{ fontWeight: 400, fontSize: 11, color: "var(--ok)", marginLeft: 6 }}>{t("org.editor.configured")}</span>
                )}
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{bizCollapsed ? "▸" : "▾"}</span>
            </div>
            {!bizCollapsed && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6, lineHeight: 1.5 }}>
                  {t("org.editor.coreBusinessHint")}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
                  {[
                    { label: t("org.editor.templateStartup"), tpl: "## 业务定位\n我们是一家___公司，核心产品/服务是___。\n\n## 当前阶段目标\n- 完成产品 MVP 并上线\n- 获取首批 100 个种子用户\n- 验证产品-市场匹配度\n\n## 工作策略\n- 产品优先：先打磨核心功能，再扩展\n- 精益运营：小规模验证后再投入推广资源\n- 数据驱动：关注用户留存率和活跃度\n\n## 主动运营要求\n负责人需持续推进：产品开发进度跟踪、市场调研执行、用户反馈收集与分析、团队任务协调。每个复盘周期应有可交付成果。" },
                    { label: t("org.editor.templateContentOps"), tpl: "## 业务定位\n面向___领域的内容创作与分发平台/账号。\n\n## 当前阶段目标\n- 建立稳定的内容生产流程（每周___篇）\n- 核心平台粉丝/订阅达到___\n- 形成可复制的爆款内容方法论\n\n## 工作策略\n- 选题驱动：每周策划会确定选题方向\n- 数据复盘：分析每篇内容的阅读/互动数据\n- 持续迭代：根据数据调整内容策略\n\n## 主动运营要求\n负责人需持续推进：选题策划与分配、内容质量把控、发布排期管理、数据复盘与策略调整。确保内容产出不中断。" },
                    { label: t("org.editor.templateSoftware"), tpl: "## 项目定位\n为___开发的___系统/应用。\n\n## 当前阶段目标\n- 完成___模块的开发与测试\n- 交付可演示的版本给___\n- 技术文档同步更新\n\n## 工作策略\n- 迭代开发：按优先级排列功能，每轮迭代2周\n- 质量保障：代码审查 + 自动化测试覆盖\n- 文档先行：关键架构决策必须文档化\n\n## 主动运营要求\n负责人需持续推进：任务拆解与分配、代码审查、进度跟踪、阻塞问题排除、与需求方沟通确认。" },
                    { label: t("org.editor.templateResearch"), tpl: "## 课题方向\n研究___领域的___问题。\n\n## 当前阶段目标\n- 完成文献调研，形成研究综述\n- 确定研究方案和实验设计\n- 产出阶段性研究报告\n\n## 工作策略\n- 文献先行：系统梳理相关领域进展\n- 实验验证：设计对照实验验证假设\n- 定期交流：团队内部周会分享进展\n\n## 主动运营要求\n负责人需持续推进：文献调研分配、研究方案讨论、实验进度追踪、成果整理与汇报。" },
                    { label: t("org.editor.templateEcommerce"), tpl: "## 业务定位\n面向___的___品类电商。\n\n## 当前阶段目标\n- 完成店铺搭建和首批___个 SKU 上架\n- 月销售额达到___\n- 建立稳定的供应链和客服流程\n\n## 工作策略\n- 选品驱动：通过市场分析确定主推品类\n- 流量获取：___平台引流 + 内容营销\n- 复购优先：客户满意度和复购率是核心指标\n\n## 主动运营要求\n负责人需持续推进：选品调研、供应链管理、营销活动策划执行、客户反馈处理、数据分析与策略调整。确保日常运营不中断。" },
                  ].map((tpl) => (
                    <button
                      key={tpl.label}
                      className="btnSmall"
                      style={{ fontSize: 11, padding: "3px 8px" }}
                      onClick={() => {
                        if ((currentOrg.core_business || "").trim() && !confirm(t("org.editor.confirmOverwriteCoreBusiness"))) return;
                        setCurrentOrg({ ...currentOrg, core_business: tpl.tpl });
                      }}
                    >
                      {tpl.label}
                    </button>
                  ))}
                </div>
                <textarea
                  className="input"
                  style={{ width: "100%", fontSize: 12, minHeight: 120, resize: "vertical", lineHeight: 1.6, fontFamily: "inherit" }}
                  placeholder={t("org.editor.coreBusinessPlaceholder")}
                  value={currentOrg.core_business || ""}
                  onChange={(e) => setCurrentOrg({ ...currentOrg, core_business: e.target.value })}
                />
                {(currentOrg.core_business || "").trim() && (
                  <div style={{ fontSize: 11, color: "var(--ok)", marginTop: 4 }}>
                    {t("org.editor.coreBusinessSuccess")}
                  </div>
                )}
              </div>
            )}
          </div>
          )}

          {/* ── 组织运行看门狗（per-org，默认关闭） ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setOrgWatchdogCollapsed(!orgWatchdogCollapsed)}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                {t("org.editor.orgWatchdogTitle")}
                {orgWatchdogCollapsed && (currentOrg as any).watchdog_enabled === true && (
                  <span style={{ fontWeight: 400, fontSize: 11, color: "var(--ok)", marginLeft: 6 }}>
                    {t("org.editor.watchdogActive")}
                  </span>
                )}
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{orgWatchdogCollapsed ? "▸" : "▾"}</span>
            </div>
            {!orgWatchdogCollapsed && (
              <div style={{ marginTop: 8 }}>
                <label style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer", marginBottom: 8 }}>
                  <input
                    type="checkbox"
                    style={{ marginTop: 3 }}
                    checked={(currentOrg as any).watchdog_enabled === true}
                    onChange={(e) => setCurrentOrg({ ...currentOrg, watchdog_enabled: e.target.checked } as any)}
                  />
                  <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <span style={{ fontSize: 12 }}>{t("org.editor.orgWatchdogEnable")}</span>
                    <span style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
                      {t("org.editor.orgWatchdogHint")}
                    </span>
                  </div>
                </label>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, opacity: (currentOrg as any).watchdog_enabled === true ? 1 : 0.65 }}>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>
                      {t("org.editor.orgWatchdogIntervalLabel")}
                    </label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder="30"
                      value={(currentOrg as any).watchdog_interval_s ?? 30}
                      onChange={(e) => setCurrentOrg({ ...currentOrg, watchdog_interval_s: Number(e.target.value || 30) } as any)}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>
                      {t("org.editor.orgWatchdogStuckLabel")}
                    </label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder="1800"
                      value={(currentOrg as any).watchdog_stuck_threshold_s ?? 1800}
                      onChange={(e) => setCurrentOrg({ ...currentOrg, watchdog_stuck_threshold_s: Number(e.target.value || 1800) } as any)}
                    />
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {t("org.editor.orgWatchdogStuckHelp")}
                    </div>
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>
                      {t("org.editor.orgWatchdogSilenceLabel")}
                    </label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder="1800"
                      value={(currentOrg as any).watchdog_silence_threshold_s ?? 1800}
                      onChange={(e) => setCurrentOrg({ ...currentOrg, watchdog_silence_threshold_s: Number(e.target.value || 1800) } as any)}
                    />
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {t("org.editor.orgWatchdogSilenceHelp")}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ── 命令生命周期看门狗（高级，全局配置；默认全部关闭） ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => {
                const next = !watchdogCollapsed;
                setWatchdogCollapsed(next);
                if (!next && !watchdogLoaded) void loadWatchdogConfig();
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                {t("org.editor.watchdogTitle")}
                {watchdogCollapsed && (
                  watchdogDraft.warn.trim() !== "" ||
                  watchdogDraft.autostop.trim() !== "" ||
                  watchdogDraft.timeout.trim() !== "" ||
                  watchdogDraft.deadlock.trim() !== ""
                ) && (
                  <span style={{ fontWeight: 400, fontSize: 11, color: "var(--ok)", marginLeft: 6 }}>
                    {t("org.editor.watchdogActive")}
                  </span>
                )}
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{watchdogCollapsed ? "▸" : "▾"}</span>
            </div>
            {!watchdogCollapsed && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8, lineHeight: 1.5 }}>
                  {t("org.editor.watchdogHint")}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>
                      {t("org.editor.watchdogWarnLabel")}
                    </label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder=""
                      value={watchdogDraft.warn}
                      onChange={(e) => setWatchdogDraft({ ...watchdogDraft, warn: e.target.value })}
                    />
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {t("org.editor.watchdogWarnHelp")}
                    </div>
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>
                      {t("org.editor.watchdogAutostopLabel")}
                    </label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder=""
                      value={watchdogDraft.autostop}
                      onChange={(e) => setWatchdogDraft({ ...watchdogDraft, autostop: e.target.value })}
                    />
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {t("org.editor.watchdogAutostopHelp")}
                    </div>
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>
                      {t("org.editor.watchdogTimeoutLabel")}
                    </label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder=""
                      value={watchdogDraft.timeout}
                      onChange={(e) => setWatchdogDraft({ ...watchdogDraft, timeout: e.target.value })}
                    />
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {t("org.editor.watchdogTimeoutHelp")}
                    </div>
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>
                      {t("org.editor.watchdogDeadlockLabel")}
                    </label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder=""
                      value={watchdogDraft.deadlock}
                      onChange={(e) => setWatchdogDraft({ ...watchdogDraft, deadlock: e.target.value })}
                    />
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {t("org.editor.watchdogDeadlockHelp")}
                    </div>
                  </div>
                </div>
                <div style={{ marginTop: 10, display: "flex", gap: 6 }}>
                  <button
                    className="btnSmall"
                    style={{ fontSize: 11, padding: "4px 10px" }}
                    disabled={watchdogSaving}
                    onClick={() => void saveWatchdogConfig()}
                  >
                    {watchdogSaving ? t("org.editor.watchdogSaving") : t("org.editor.watchdogSave")}
                  </button>
                  <button
                    className="btnSmall"
                    style={{ fontSize: 11, padding: "4px 10px" }}
                    disabled={watchdogSaving}
                    onClick={() => setWatchdogDraft({ warn: "", autostop: "", timeout: "", deadlock: "" })}
                  >
                    {t("org.editor.watchdogClear")}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* ── 用户身份 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setPersonaCollapsed(!personaCollapsed)}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                {t("org.editor.userPersona")}
                {currentOrg.user_persona?.title && (
                  <span style={{ fontWeight: 400, fontSize: 11, color: "var(--muted)", marginLeft: 6 }}>
                    {currentOrg.user_persona.display_name || currentOrg.user_persona.title}
                  </span>
                )}
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{personaCollapsed ? "▸" : "▾"}</span>
            </div>
            {!personaCollapsed && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6, lineHeight: 1.5 }}>
                  {t("org.editor.userPersonaHint")}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
                  {[
                    { title: t("org.editor.presetChairman"), desc: t("org.editor.personaDescPlaceholder") },
                    { title: t("org.editor.presetProductLead"), desc: "" },
                    { title: t("org.editor.presetProducer"), desc: "" },
                    { title: t("org.editor.presetInvestor"), desc: "" },
                    { title: t("org.editor.presetClient"), desc: "" },
                    { title: t("org.editor.presetResearchLead"), desc: "" },
                  ].map((preset) => (
                    <button
                      key={preset.title}
                      className="btnSmall"
                      style={{
                        fontSize: 11, padding: "3px 8px",
                        background: currentOrg.user_persona?.title === preset.title ? "var(--primary)" : undefined,
                        color: currentOrg.user_persona?.title === preset.title ? "#fff" : undefined,
                      }}
                      onClick={() => setCurrentOrg({
                        ...currentOrg,
                        user_persona: { title: preset.title, display_name: preset.title, description: preset.desc },
                      })}
                    >
                      {preset.title}
                    </button>
                  ))}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  <div style={{ display: "flex", gap: 6 }}>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>{t("org.editor.personaTitleField")}</label>
                      <input
                        className="input"
                        style={{ width: "100%", fontSize: 12 }}
                        placeholder={t("org.editor.personaTitlePlaceholder")}
                        value={currentOrg.user_persona?.title || ""}
                        onChange={(e) => setCurrentOrg({
                          ...currentOrg,
                          user_persona: { ...currentOrg.user_persona, title: e.target.value, display_name: currentOrg.user_persona?.display_name || "", description: currentOrg.user_persona?.description || "" },
                        })}
                      />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>{t("org.editor.personaDisplayName")}</label>
                      <input
                        className="input"
                        style={{ width: "100%", fontSize: 12 }}
                        placeholder={t("org.editor.personaDisplayNameHint")}
                        value={currentOrg.user_persona?.display_name || ""}
                        onChange={(e) => setCurrentOrg({
                          ...currentOrg,
                          user_persona: { ...currentOrg.user_persona, title: currentOrg.user_persona?.title || t("org.editor.defaultPersonaTitle"), display_name: e.target.value, description: currentOrg.user_persona?.description || "" },
                        })}
                      />
                    </div>
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>{t("org.editor.personaDescription")}</label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder={t("org.editor.personaDescPlaceholder")}
                      value={currentOrg.user_persona?.description || ""}
                      onChange={(e) => setCurrentOrg({
                        ...currentOrg,
                        user_persona: { ...currentOrg.user_persona, title: currentOrg.user_persona?.title || t("org.editor.defaultPersonaTitle"), display_name: currentOrg.user_persona?.display_name || "", description: e.target.value },
                      })}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ── Quick actions ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{t("org.editor.quickActions")}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={() => setConfirmReset(true)}>{t("org.editor.resetOrg")}</button>
              <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={handleExportOrg}>{t("org.editor.exportConfig")}</button>
              <label className="btnSmall" style={{ fontSize: 11, padding: "4px 8px", cursor: "pointer" }}>
                {t("org.editor.importConfig")}
                <input type="file" accept=".json" style={{ display: "none" }} onChange={handleImportOrg} />
              </label>
              {liveMode && (<>
                <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={async () => {
                  try { await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/heartbeat/trigger`, { method: "POST" }); } catch {}
                }}>{t("org.editor.triggerHeartbeat")}</button>
                <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={async () => {
                  try { await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/standup/trigger`, { method: "POST" }); } catch {}
                }}>{t("org.editor.triggerStandup")}</button>
              </>)}
            </div>
          </div>
        </div>
      )}

      </div>{/* close content area */}

      {/* Toast notification */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          zIndex: 9999, display: "flex", alignItems: "center", gap: 6,
          padding: "8px 16px", borderRadius: 8, fontSize: 13, fontWeight: 500,
          color: "#fff", boxShadow: "0 4px 16px rgba(0,0,0,0.2)",
          background: toast.type === "ok" ? "var(--ok, #22c55e)" : "var(--danger, #ef4444)",
          animation: "toast-in 0.2s ease",
        }}>
          {toast.type === "ok" ? <IconCheck size={14} /> : <IconAlertCircle size={14} />}
          {toast.message}
        </div>
      )}
      <ConfirmDialog
        dialog={confirmReset ? { message: t("org.editor.confirmResetOrg"), onConfirm: handleResetOrg } : null}
        onClose={() => setConfirmReset(false)}
      />
    </div>
  );
}
