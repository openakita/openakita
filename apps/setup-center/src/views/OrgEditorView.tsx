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
import { useMdModules } from "../hooks/useMdModules";
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
} from "../icons";
import { safeFetch } from "../providers";
import { IS_CAPACITOR, saveFileDialog, IS_TAURI, writeTextFile } from "../platform";
import { OrgInboxSidebar } from "../components/OrgInboxSidebar";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { OrgAvatar, AVATAR_PRESETS, AVATAR_MAP } from "../components/OrgAvatars";
import { OrgChatPanel } from "../components/OrgChatPanel";
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
import agentOrgImg from "../assets/agent_org.png";

// ── Custom Canvas Controls (shadcn UI) ──

function OrgCanvasControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();

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
            <TooltipContent side="left">放大</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => zoomOut()}>
                <ZoomOut className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">缩小</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => fitView({ padding: 0.2 })}>
                <Maximize className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">适应视图</TooltipContent>
          </Tooltip>
        </div>
      </TooltipProvider>
    </Panel>
  );
}

// ── Time helpers (always show local timezone) ──

function fmtTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtDateTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtShortDate(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

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

const TASK_STATUS_LABELS: Record<string, string> = {
  todo: "待办",
  in_progress: "进行中",
  delivered: "已交付",
  rejected: "已打回",
  accepted: "已验收",
  blocked: "已阻塞",
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  node_status_change: "节点状态变更",
  llm_usage: "模型调用统计",
  task_completed: "任务完成",
  task_assigned: "任务分配",
  task_delivered: "任务交付",
  task_accepted: "任务验收",
  task_rejected: "任务驳回",
  task_failed: "任务失败",
  node_activated: "节点激活",
  node_deactivated: "节点停用",
  node_dismissed: "节点解散",
  node_frozen: "节点冻结",
  node_unfrozen: "节点解冻",
  org_started: "组织启动",
  org_stopped: "组织停止",
  org_paused: "组织暂停",
  org_resumed: "组织恢复",
  org_reset: "组织重置",
  schedule_assigned: "定时任务分配",
  schedule_completed: "定时任务完成",
  schedule_triggered: "定时任务触发",
  schedule_requested: "定时任务请求",
  broadcast: "广播消息",
  auto_clone_created: "自动创建副本",
  clones_reclaimed: "副本回收",
  auto_kickoff: "自动启动",
  scaling_requested: "扩容请求",
  scaling_approved: "扩容批准",
  scaling_rejected: "扩容拒绝",
  tools_granted: "工具授权",
  tools_requested: "工具请求",
  tools_revoked: "工具撤销",
  user_command: "用户指令",
  watchdog_recovery: "看门狗恢复",
  heartbeat_triggered: "心跳触发",
  heartbeat_decision: "心跳决策",
  standup_started: "站会开始",
  standup_completed: "站会结束",
  meeting_completed: "会议结束",
  conflict_detected: "冲突检测",
  policy_proposed: "策略提议",
  approval_resolved: "审批完成",
  tool_call_start: "工具调用",
  tool_call_end: "工具调用完成",
  plan_created: "计划创建",
  plan_completed: "计划完成",
  plan_cancelled: "计划取消",
  plan_step_updated: "计划步骤更新",
  iteration_start: "迭代开始",
  agent_handoff: "Agent 切换",
  ask_user: "询问用户",
  done: "完成",
  error: "错误",
};

const MSG_TYPE_LABELS: Record<string, string> = {
  task_assign: "任务分配",
  task_result: "任务结果",
  task_delivered: "任务交付",
  task_accepted: "任务验收",
  task_rejected: "任务驳回",
  report: "工作汇报",
  question: "提问",
  answer: "回答",
  escalate: "上报",
  escalation: "上报",
  broadcast: "广播",
  dept_broadcast: "部门广播",
  feedback: "反馈",
  handshake: "握手",
  deliverable: "交付物",
};

const DATA_KEY_LABELS: Record<string, string> = {
  from: "来源",
  to: "目标",
  reason: "原因",
  node_id: "节点",
  calls: "调用次数",
  tokens_in: "输入 token",
  tokens_out: "输出 token",
  model: "模型",
  result_preview: "结果预览",
  deliverable_preview: "交付物预览",
  error: "错误",
  content: "内容",
  task: "任务",
  title: "标题",
  role: "角色",
  name: "名称",
  tools: "工具",
  source: "来源",
  target: "目标",
  scope: "范围",
  prompt: "提示词",
  schedule_id: "定时任务 ID",
  chain_id: "链路 ID",
  clone_id: "副本 ID",
  approval_id: "审批 ID",
  request_id: "请求 ID",
  new_node_id: "新节点 ID",
  superior: "上级",
  participants: "参与者",
  pending_count: "待处理数",
  node_count: "节点数",
  rounds: "轮次",
  cycle: "周期",
  decision: "决策",
  stuck_secs: "阻塞时长(秒)",
  threshold: "阈值",
  dismissed: "已解散",
  type: "类型",
  topic: "议题",
  filename: "文件名",
  core_business_len: "核心业务数",
  tool: "工具",
  args: "参数",
  result: "结果",
  duration_ms: "耗时(ms)",
  status: "状态",
  question: "问题",
  message: "消息",
};

const DATA_VALUE_LABELS: Record<string, string> = {
  idle: "空闲",
  busy: "执行中",
  waiting: "等待中",
  error: "异常",
  offline: "离线",
  frozen: "已冻结",
  task_started: "任务开始",
  task_completed: "任务完成",
  task_failed: "任务失败",
  task_assigned: "任务分配",
  task_delivered: "任务交付",
  task_accepted: "任务验收",
  task_rejected: "任务驳回",
  org_stopped: "组织停止",
  org_reset: "组织重置",
  org_paused: "组织暂停",
  org_resumed: "组织恢复",
  restart_cleanup: "重启清理",
  watchdog_recovery: "看门狗恢复",
  health_check_recovery: "健康检查恢复",
  org_quota_pause: "配额暂停",
  quota_exhausted: "配额耗尽",
  auto_recover_before_activate: "激活前自动恢复",
  unfreeze: "解冻",
  stuck_busy: "持续繁忙",
  error_not_recovering: "错误未恢复",
  idle_no_progress: "空闲无进展",
  root_busy: "根节点繁忙",
  root_has_task: "根节点有任务",
  skip: "跳过",
  activate: "激活",
  do_nothing: "无操作",
  pending: "待处理",
  approved: "已批准",
  rejected: "已拒绝",
  completed: "已完成",
  in_progress: "进行中",
  delivered: "已交付",
  accepted: "已验收",
  blocked: "已阻塞",
  healthy: "健康",
  warning: "警告",
  critical: "严重",
  attention: "关注",
};

function _translateDataValue(
  key: string, value: unknown,
  nodeNameMap?: Map<string, string>,
): string {
  const s = String(value);
  if ((key === "node_id" || key === "new_node_id") && nodeNameMap?.has(s)) {
    return nodeNameMap.get(s)!;
  }
  return DATA_VALUE_LABELS[s] || s;
}

function NodeTasksTabContent({
  nodeTasks,
  nodeActivePlan,
  loading,
  nodes,
  apiBaseUrl,
  orgId,
  fmtDateTime,
}: {
  nodeTasks: { assigned: any[]; delegated: any[] } | null;
  nodeActivePlan: any;
  loading: boolean;
  nodes: Node[];
  apiBaseUrl: string;
  orgId: string;
  fmtDateTime: (v: string | number | undefined | null) => string;
}) {
  const nodeMap = new Map(nodes.map((n) => [n.id, (n.data as any)?.role_title || n.id]));
  const getNodeLabel = (id: string | null) => (id ? nodeMap.get(id) || id : "-");

  if (loading) {
    return (
      <div style={{ fontSize: 12, color: "var(--muted)", padding: 12 }}>加载中...</div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 12 }}>
      {/* Current Task + Plan */}
      {nodeActivePlan && (
        <div className="card" style={{ padding: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "#b45309" }}>
            当前任务
          </div>
          <div style={{ fontWeight: 500, marginBottom: 6 }}>{nodeActivePlan.title}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 10, color: "var(--muted)" }}>进度</span>
            <div style={{
              flex: 1,
              height: 4,
              borderRadius: 2,
              background: "var(--line)",
              overflow: "hidden",
            }}>
              <div style={{
                height: "100%",
                borderRadius: 2,
                background: "var(--accent)",
                width: `${nodeActivePlan.progress_pct ?? 0}%`,
              }} />
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
                  <div key={s.id || i} style={{ display: "flex", gap: 6, alignItems: "flex-start", marginBottom: 4 }}>
                    <span style={{ color, fontWeight: 600, flexShrink: 0 }}>{icon}</span>
                    <span style={{ color: "var(--text)" }}>{s.description || s.title || `步骤 ${i + 1}`}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Assigned Tasks */}
      <div className="card" style={{ padding: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>分配给我的任务</div>
        {(nodeTasks?.assigned?.length ?? 0) === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>暂无</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {(nodeTasks?.assigned || []).map((t: any) => (
              <div
                key={t.id}
                style={{
                  padding: 8,
                  borderRadius: 6,
                  border: "1px solid var(--line)",
                  background: "var(--bg-subtle, var(--bg-card))",
                }}
              >
                <div style={{ fontWeight: 500, marginBottom: 4 }}>{t.title}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10 }}>
                  <span style={{
                    padding: "1px 5px",
                    borderRadius: 3,
                    background: "var(--bg-app)",
                    color: "var(--muted)",
                  }}>
                    {TASK_STATUS_LABELS[t.status] || t.status}
                  </span>
                  <span style={{ color: "var(--muted)" }}>{(t.progress_pct ?? 0)}%</span>
                </div>
                <div style={{
                  marginTop: 4,
                  height: 3,
                  borderRadius: 2,
                  background: "var(--line)",
                  overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%",
                    borderRadius: 2,
                    background: "var(--accent)",
                    width: `${Math.min(100, t.progress_pct ?? 0)}%`,
                  }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Delegated Tasks */}
      <div className="card" style={{ padding: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>我委派的任务</div>
        {(nodeTasks?.delegated?.length ?? 0) === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>暂无</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {(nodeTasks?.delegated || []).map((t: any) => (
              <div
                key={t.id}
                style={{
                  padding: 8,
                  borderRadius: 6,
                  border: "1px solid var(--line)",
                  background: "var(--bg-subtle, var(--bg-card))",
                }}
              >
                <div style={{ fontWeight: 500, marginBottom: 4 }}>{t.title}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10 }}>
                  <span style={{
                    padding: "1px 5px",
                    borderRadius: 3,
                    background: "var(--bg-app)",
                    color: "var(--muted)",
                  }}>
                    {TASK_STATUS_LABELS[t.status] || t.status}
                  </span>
                  <span style={{ color: "var(--muted)" }}>{(t.progress_pct ?? 0)}%</span>
                  <span style={{ color: "var(--muted)", marginLeft: "auto" }}>
                    执行人: {getNodeLabel(t.assignee_node_id)}
                  </span>
                </div>
                <div style={{
                  marginTop: 4,
                  height: 3,
                  borderRadius: 2,
                  background: "var(--line)",
                  overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%",
                    borderRadius: 2,
                    background: "var(--accent)",
                    width: `${Math.min(100, t.progress_pct ?? 0)}%`,
                  }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Types ──

interface OrgNodeData {
  id: string;
  role_title: string;
  role_goal: string;
  role_backstory: string;
  agent_source: string;
  agent_profile_id: string | null;
  position: { x: number; y: number };
  level: number;
  department: string;
  custom_prompt: string;
  identity_dir: string | null;
  mcp_servers: string[];
  skills: string[];
  skills_mode: string;
  preferred_endpoint: string | null;
  max_concurrent_tasks: number;
  timeout_s: number;
  can_delegate: boolean;
  can_escalate: boolean;
  can_request_scaling: boolean;
  is_clone: boolean;
  clone_source: string | null;
  external_tools: string[];
  ephemeral: boolean;
  avatar: string | null;
  frozen_by: string | null;
  frozen_reason: string | null;
  frozen_at: string | null;
  status: string;
  auto_clone_enabled?: boolean;
  auto_clone_threshold?: number;
  auto_clone_max?: number;
  current_task?: string;
}

interface OrgEdgeData {
  id: string;
  source: string;
  target: string;
  edge_type: string;
  label: string;
  bidirectional: boolean;
  priority: number;
  bandwidth_limit: number;
}

interface OrgSummary {
  id: string;
  name: string;
  description: string;
  icon: string;
  status: string;
  node_count: number;
  edge_count: number;
  tags: string[];
  created_at: string;
  updated_at: string;
}

interface UserPersona {
  title: string;
  display_name: string;
  description: string;
}

interface OrgFull {
  id: string;
  name: string;
  description: string;
  icon: string;
  status: string;
  nodes: OrgNodeData[];
  edges: OrgEdgeData[];
  user_persona?: UserPersona;
  [key: string]: any;
}

interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  icon: string;
  node_count: number;
  tags: string[];
}

// ── Helpers ──

const EDGE_COLORS: Record<string, string> = {
  hierarchy: "var(--primary)",
  collaborate: "var(--ok)",
  escalate: "var(--danger)",
  consult: "#a78bfa",
};

const STATUS_COLORS: Record<string, string> = {
  idle: "var(--ok)",
  busy: "var(--primary)",
  waiting: "#f59e0b",
  error: "var(--danger)",
  offline: "var(--muted)",
  frozen: "#93c5fd",
  dormant: "var(--muted)",
  active: "var(--ok)",
  running: "var(--primary)",
  paused: "#f59e0b",
  archived: "var(--muted)",
};

const ORG_STATUS_LABELS: Record<string, string> = {
  dormant: "休眠",
  active: "运行中",
  running: "运行中",
  paused: "已暂停",
  archived: "已归档",
};

const DEPT_COLORS: Record<string, string> = {
  "管理层": "#6366f1",
  "技术部": "#0ea5e9",
  "产品部": "#8b5cf6",
  "市场部": "#f97316",
  "行政支持": "#64748b",
  "工程": "#0ea5e9",
  "前端组": "#06b6d4",
  "后端组": "#14b8a6",
  "编辑部": "#f97316",
  "创作组": "#ec4899",
  "运营组": "#84cc16",
};

function getDeptColor(dept: string): string {
  return DEPT_COLORS[dept] || "#6b7280";
}

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

function detectOverlap(nodes: Node[]): boolean {
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i].position || { x: 0, y: 0 };
      const b = nodes[j].position || { x: 0, y: 0 };
      if (Math.abs(a.x - b.x) < NODE_COLLISION_W && Math.abs(a.y - b.y) < NODE_COLLISION_H) return true;
    }
  }
  return false;
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

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  busy: "执行中",
  waiting: "等待中",
  error: "异常",
  offline: "离线",
  frozen: "已冻结",
};

function OrgNodeComponent({ data, selected }: { data: OrgNodeData; selected: boolean }) {
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
        title="目标连接点：点击或拖拽到这里完成连线"
        aria-label="目标连接点"
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
            !isLive && data.status === "idle" ? "未激活"
            : data.status === "idle" && idleSecs != null && idleSecs > 60
              ? `空闲 ${idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h${Math.floor((idleSecs % 3600) / 60)}m` : `${Math.floor(idleSecs / 60)}m`}`
            : STATUS_LABELS[data.status] || data.status
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
                {isEphemeral ? "临时" : "副本"}
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
              未激活
            </span>
          ) : data.status !== "idle" ? (
            <span style={{
              fontSize: 10, padding: "1px 6px", borderRadius: 4,
              background: `${statusColor}15`, color: statusColor,
              fontWeight: 500,
            }}>
              {STATUS_LABELS[data.status] || data.status}
            </span>
          ) : (
            <span style={{
              fontSize: 10, padding: "1px 6px", borderRadius: 4,
              background: `${statusColor}15`, color: statusColor,
              fontWeight: 500,
            }}
              title={idleSecs != null && idleSecs > 60
                ? `空闲 ${idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h${Math.floor((idleSecs % 3600) / 60)}m` : `${Math.floor(idleSecs / 60)}m`}`
                : "在线空闲"}
            >
              空闲
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
            {data.current_task}
          </div>
        )}

        {/* Anomaly warning */}
        {isAnomaly && (
          <div style={{ fontSize: 9, color: "#f59e0b", marginTop: 3, display: "flex", alignItems: "center", gap: 3 }}>
            <IconAlertCircle size={10} color="#f59e0b" />
            <span>{typeof isAnomaly === "string" ? isAnomaly : "需要关注"}</span>
          </div>
        )}

        {/* Frozen indicator */}
        {isFrozen && (
          <div style={{ fontSize: 10, color: "#93c5fd", marginTop: 4, display: "flex", alignItems: "center", gap: 3 }}>
            <IconSnowflake size={11} color="#93c5fd" />
            <span>{data.frozen_reason || "已冻结"}</span>
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
                <div>部门: {data.department || "—"} · 层级 L{data.level ?? "?"}</div>
                <div>状态: <span style={{ color: statusColor, fontWeight: 500 }}>{!isLive && data.status === "idle" ? "未激活" : isLive && data.status === "idle" ? "空闲" : STATUS_LABELS[data.status] || data.status}</span></div>
                {idleSecs != null && <div>空闲: {idleSecs >= 3600 ? `${Math.floor(idleSecs / 3600)}h${Math.floor((idleSecs % 3600) / 60)}m` : idleSecs >= 60 ? `${Math.floor(idleSecs / 60)}m` : `${idleSecs}s`}</div>}
                {pendingMsgs != null && pendingMsgs > 0 && <div>待处理: {pendingMsgs} 条消息</div>}
                {((pp && pp.total != null && pp.total > 0) || (ds && (ds.total ?? 0) > 0)) && <Sep />}
                {pp && pp.total != null && pp.total > 0 && (
                  <div>
                    计划进度: {pp.completed ?? 0}/{pp.total}
                    <div style={{ marginTop: 2, height: 4, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
                      <div style={{ height: "100%", width: `${Math.min(100, ((pp.completed ?? 0) / pp.total) * 100)}%`, background: "var(--primary)", borderRadius: 2 }} />
                    </div>
                  </div>
                )}
                {ds && (ds.total ?? 0) > 0 && (
                  <div>委派: 进行中 {ds.in_progress ?? 0} · 已完成 {ds.completed ?? 0} / {ds.total}</div>
                )}
                {(runningSince != null || extTools.length > 0 || recentTs != null || watchdog) && <Sep />}
                {runningSince != null && (
                  <div>运行中: {typeof runningSince === "number" ? fmtTime(runningSince) : fmtShortDate(runningSince)}</div>
                )}
                {extTools.length > 0 && <div>外部工具: {extTools.slice(0, 3).join(", ")}{extTools.length > 3 ? ` +${extTools.length - 3}` : ""}</div>}
                {recentTs != null && <div>最近活动: {fmtShortDate(recentTs)}</div>}
                {watchdog && <div>看门狗: {watchdog}</div>}
                {(data.current_task || isAnomaly) && <Sep />}
                {data.current_task && <div style={{ marginTop: 2, color: "#b45309" }}>任务: {data.current_task}</div>}
                {isAnomaly && <div style={{ marginTop: 2, color: "#f59e0b", fontWeight: 500 }}>{typeof isAnomaly === "string" ? isAnomaly : "异常"}</div>}
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
        title="源连接点：从这里点击或拖拽开始连线"
        aria-label="源连接点"
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
  useTranslation();
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
  const [propsTab, setPropsTab] = useState<"overview" | "identity" | "capabilities">("overview");
  const [fullPromptPreview, setFullPromptPreview] = useState<string | null>(null);
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false);
  const [layoutLocked, setLayoutLocked] = useState(false);
  const liveMode = currentOrg?.status === "active" || currentOrg?.status === "running";
  const [activeDrawer, setActiveDrawer] = useState<"chat" | "inbox" | null>(null);
  const [nodeEvents, setNodeEvents] = useState<any[]>([]);
  const [nodeSchedules, setNodeSchedules] = useState<any[]>([]);
  const [nodeMessages, setNodeMessages] = useState<any[]>([]);
  const [nodeThinking, setNodeThinking] = useState<any[]>([]);
  const [orgStats, setOrgStats] = useState<any>(null);
  const [expandedThinkingIdx, setExpandedThinkingIdx] = useState<number | string | null>(null);
  const nodeNameMap = useMemo(() => new Map(nodes.map((n) => [n.id, (n.data as any)?.role_title || n.id])), [nodes]);
  const [nodeTasks, setNodeTasks] = useState<{ assigned: any[]; delegated: any[] } | null>(null);
  const [nodeActivePlan, setNodeActivePlan] = useState<any>(null);
  const [nodeTasksLoading, setNodeTasksLoading] = useState(false);
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
  const [chatPanelNode, setChatPanelNode] = useState<string | null>(null);
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
  const [availableSkills, setAvailableSkills] = useState<{ name: string; description?: string; name_i18n?: string; description_i18n?: string }[]>([]);

  // Blackboard state
  const [bbEntries, setBbEntries] = useState<any[]>([]);
  const [bbScope, setBbScope] = useState<"all" | "org" | "department" | "node">("all");

  // Capabilities search
  const [mcpSearch, setMcpSearch] = useState("");
  const [skillSearch, setSkillSearch] = useState("");

  // Org settings panel collapse
  const [personaCollapsed, setPersonaCollapsed] = useState(false);
  const [bizCollapsed, setBizCollapsed] = useState(false);
  const [bbLoading, setBbLoading] = useState(false);

  // New node form
  const [newNodeTitle, setNewNodeTitle] = useState("");
  const [newNodeDept, setNewNodeDept] = useState("");
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768 || IS_CAPACITOR);
  const [showLeftPanel, setShowLeftPanel] = useState(true);
  const [showRightPanel, setShowRightPanel] = useState(false);
  const [creatingOrg, setCreatingOrg] = useState(false);
  const orgCreateBusyRef = useRef(false);
  const wasRunningRef = useRef(false);

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
    if (!currentOrg) {
      wasRunningRef.current = false;
      return;
    }
    const running = currentOrg.status === "active" || currentOrg.status === "running";
    if (running !== wasRunningRef.current) {
      setLayoutLocked(running);
      wasRunningRef.current = running;
    }
  }, [currentOrg?.id, currentOrg?.status]);

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
      setTemplates(data);
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
      const flowNodes = data.nodes.map((n) => orgNodeToFlowNode(n, liveExtra));
      const flowEdges = data.edges.map(orgEdgeToFlowEdge);
      const hasOverlap = detectOverlap(flowNodes);
      setNodes(hasOverlap ? computeTreeLayout(flowNodes, flowEdges) : flowNodes);
      setEdges(flowEdges);
      setSelectedNodeId(null);
      setEditingName(false);
      setLayoutLocked(running);
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

  const fetchBlackboard = useCallback(async (orgId: string, scope?: string) => {
    setBbLoading(true);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (scope && scope !== "all") params.set("scope", scope);
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/memory?${params}`);
      const data = await res.json();
      setBbEntries(data || []);
    } catch {
      setBbEntries([]);
    } finally {
      setBbLoading(false);
    }
  }, [apiBaseUrl]);

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
    if (selectedOrgId) {
      fetchOrg(selectedOrgId);
    }
  }, [selectedOrgId, fetchOrg]);

  useEffect(() => {
    if (currentOrg && !selectedNodeId) {
      fetchBlackboard(currentOrg.id, bbScope);
    }
  }, [currentOrg?.id, selectedNodeId, bbScope, fetchBlackboard]);

  // ── WebSocket for live mode ──

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

  useEffect(() => {
    if (!visible || !liveMode || !currentOrg) return;
    const wsUrl = apiBaseUrl.replace(/^http/, "ws") + "/ws";
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (evt) => {
        try {
          const parsed = JSON.parse(evt.data);
          const ev = parsed.event as string;
          const d = parsed.data;
          if (!d || d.org_id !== currentOrg.id) return;

          if (currentOrg.status !== "active" && currentOrg.status !== "running") {
            setCurrentOrg((prev) => prev ? { ...prev, status: "active" } : prev);
            setOrgList((prev) => prev.map((o) => o.id === currentOrg.id ? { ...o, status: "active" } : o));
          }

          if (ev === "org:node_status") {
            const { node_id, status, current_task } = d;
            setNodes((prev) =>
              prev.map((n) =>
                n.id === node_id
                  ? { ...n, data: { ...n.data, status, current_task: current_task || n.data.current_task } }
                  : n,
              ),
            );
          } else if (ev === "org:task_delegated") {
            triggerEdgeAnimation(d.from_node, d.to_node, "var(--primary)");
          } else if (ev === "org:task_delivered") {
            triggerEdgeAnimation(d.from_node, d.to_node, "var(--ok)");
          } else if (ev === "org:task_accepted") {
            triggerEdgeAnimation(d.accepted_by, d.from_node, "#22c55e");
          } else if (ev === "org:task_rejected") {
            triggerEdgeAnimation(d.rejected_by, d.from_node, "var(--danger)");
          } else if (ev === "org:escalation") {
            triggerEdgeAnimation(d.from_node, d.to_node, "var(--danger)");
          } else if (ev === "org:message") {
            triggerEdgeAnimation(d.from_node, d.to_node, "#a78bfa");
          } else if (ev === "org:blackboard_update") {
            if (currentOrg && !selectedNodeId) fetchBlackboard(currentOrg.id, bbScope);
          }
        } catch { /* ignore parse errors */ }
      };
    } catch { /* WebSocket not available */ }
    return () => { ws?.close(); };
  }, [visible, liveMode, currentOrg, apiBaseUrl, setNodes, triggerEdgeAnimation, selectedNodeId, bbScope, fetchBlackboard]);

  // ── Start/Stop org ──
  const handleStartOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/start`, { method: "POST" });
      setCurrentOrg({ ...currentOrg, status: "active" });
      setOrgList((prev) => prev.map((o) => o.id === currentOrg.id ? { ...o, status: "active" } : o));
      setLayoutLocked(true);
      const mode = (currentOrg as any).operation_mode || "command";
      showToast(
        mode === "autonomous"
          ? "组织已启动（自主模式）——顶层负责人将根据核心业务自动运营"
          : "组织已启动（命令模式）——可通过聊天或命令面板下达任务",
        "ok",
      );
    } catch (e) { console.error("Failed to start org:", e); }
  }, [currentOrg, apiBaseUrl, showToast]);

  const handleStopOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/stop`, { method: "POST" });
      setCurrentOrg({ ...currentOrg, status: "dormant" });
      setOrgList((prev) => prev.map((o) => o.id === currentOrg.id ? { ...o, status: "dormant" } : o));
      setLayoutLocked(false);
    } catch (e) { console.error("Failed to stop org:", e); }
  }, [currentOrg, apiBaseUrl]);

  // ── Org export/import ──
  const orgImportRef = useRef<HTMLInputElement>(null);

  const handleExportOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      const safeName = currentOrg.name.replace(/\s+/g, "_").replace(/[/\\]/g, "_").slice(0, 30);
      const defaultName = `${safeName}.json`;

      if (IS_TAURI) {
        const savePath = await saveFileDialog({
          title: "导出组织配置",
          defaultPath: defaultName,
          filters: [{ name: "JSON", extensions: ["json"] }],
        });
        if (!savePath) return;
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/export`, { method: "POST" });
        const data = await res.json();
        await writeTextFile(savePath, JSON.stringify(data, null, 2));
        showToast(`组织已导出到: ${savePath}`);
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
        showToast(`组织「${currentOrg.name}」已导出为 ${defaultName}`);
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
      showToast(data.message || `组织「${data.organization?.name || ""}」导入成功`);
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
      setBbEntries([]);
      setNodeEvents([]);
      setNodeThinking([]);
      setNodeSchedules([]);
      setOrgStats(null);
      showToast("组织已重置");
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
      user_persona: currentOrg.user_persona || { title: "负责人", display_name: "", description: "" },
      operation_mode: (currentOrg as any).operation_mode || "command",
      core_business: currentOrg.core_business || "",
      heartbeat_enabled: currentOrg.heartbeat_enabled,
      heartbeat_interval_s: currentOrg.heartbeat_interval_s,
      standup_enabled: currentOrg.standup_enabled,
      nodes: updatedNodes,
      edges: updatedEdges,
    };
  }, [currentOrg, nodes, edges]);

  const doSave = useCallback(async (): Promise<boolean> => {
    if (!currentOrg) return false;
    const payload = buildSavePayload();
    if (!payload) return false;
    const snapshot = JSON.stringify(payload);
    if (snapshot === lastSavedRef.current) return true;
    setSaveStatus("saving");
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: snapshot,
      });
      if (!resp.ok) throw new Error(`保存失败 (${resp.status})`);
      lastSavedRef.current = snapshot;
      setSaveStatus("saved");
      fetchOrgList();
      return true;
    } catch (e: any) {
      console.error("Failed to save org:", e);
      setSaveStatus("error");
      showToast(e.message || "自动保存失败", "error");
      return false;
    }
  }, [currentOrg, buildSavePayload, apiBaseUrl, fetchOrgList, showToast]);

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
    const defaultName = "新组织";
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
      showToast(newId ? `已创建「${defaultName}」` : "已创建组织，但未在列表中定位到条目，请刷新或检查后端日志", newId ? "ok" : "error");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("Failed to create org:", e);
      showToast(msg.includes("401") || msg.includes("Authentication") ? "创建失败：需要登录或访问令牌（远程访问请检查 Web 访问密码）" : `创建组织失败：${msg}`, "error");
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
      showToast(newId ? `已从模板创建组织${data?.name ? `「${data.name}」` : ""}` : "已从模板创建，但未定位到新组织", newId ? "ok" : "error");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("Failed to create from template:", e);
      showToast(`从模板创建失败：${msg}`, "error");
    } finally {
      orgCreateBusyRef.current = false;
      setCreatingOrg(false);
    }
  }, [apiBaseUrl, fetchOrgList, showToast]);

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
        max_concurrent_tasks: 1,
        timeout_s: 300,
        can_delegate: true,
        can_escalate: true,
        can_request_scaling: true,
        is_clone: false,
        clone_source: null,
        external_tools: [],
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
  }, []);

  const onEdgeClick = useCallback((_: any, edge: Edge) => {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
    setShowRightPanel(true);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setContextMenu(null);
  }, []);

  const onNodeDragStop = useCallback(() => {}, []);

  // ── Fetch node detail when selected in live mode ──
  useEffect(() => {
    if (!visible || !selectedNodeId || !currentOrg || !liveMode) {
      if (!selectedNodeId || !currentOrg || !liveMode) {
        setNodeEvents([]);
        setNodeSchedules([]);
        setNodeMessages([]);
        setNodeThinking([]);
      }
      return;
    }
    const fetchNodeDetail = async () => {
      try {
        const [eventsRes, schedulesRes, msgsRes, thinkingRes] = await Promise.all([
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/events?actor=${selectedNodeId}&limit=20`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/schedules`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/messages?from_node=${selectedNodeId}&limit=20`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/thinking?limit=30`),
        ]);
        if (eventsRes.ok) setNodeEvents(await eventsRes.json());
        if (schedulesRes.ok) setNodeSchedules(await schedulesRes.json());
        if (msgsRes.ok) {
          const data = await msgsRes.json();
          setNodeMessages(data.messages || data || []);
        }
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
  }, [visible, selectedNodeId, currentOrg, liveMode, apiBaseUrl]);

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

  // ── Fetch node tasks when monitor panel is visible (liveMode) ──
  useEffect(() => {
    if (!selectedNodeId || !currentOrg || !liveMode) {
      setNodeTasks(null);
      setNodeActivePlan(null);
      return;
    }
    setNodeTasksLoading(true);
    const fetchNodeTasks = async () => {
      try {
        const [tasksRes, planRes] = await Promise.all([
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/tasks`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/active-plan`),
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
      } catch (e) {
        setNodeTasks({ assigned: [], delegated: [] });
        setNodeActivePlan(null);
      } finally {
        setNodeTasksLoading(false);
      }
    };
    fetchNodeTasks();
  }, [selectedNodeId, currentOrg, liveMode, apiBaseUrl]);

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
      showToast("节点已解除冻结");
    } catch (e) {
      showToast(`解除冻结失败: ${e}`, "error");
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
      data: { ...clipboardNode.data, id: newId, role_title: `${clipboardNode.data?.role_title || "节点"} (副本)`, _liveMode: liveMode },
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
      id: newId, role_title: "新节点", role_goal: "", role_backstory: "",
      agent_source: "local", agent_profile_id: null, position: pos, level: 0,
      department: "", custom_prompt: "", identity_dir: null, mcp_servers: [], skills: [],
      skills_mode: "all", preferred_endpoint: null, max_concurrent_tasks: 1, timeout_s: 0,
      can_delegate: true, can_escalate: true, can_request_scaling: true, is_clone: false,
      clone_source: null, external_tools: [], ephemeral: false, frozen_by: null,
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
              title="组织列表"
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
                  title="点击重命名"
                >
                  {currentOrg.name || "未命名组织"}
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
                <span className="org-status-label">{ORG_STATUS_LABELS[currentOrg.status] || currentOrg.status}</span>
                {liveMode && orgStats && !isMobile && (
                  <>
                    <span className="org-status-sep" />
                    <Tooltip><TooltipTrigger asChild>
                      <span className="org-status-stat"><IconClipboard size={11} /> {orgStats.total_tasks_completed ?? 0}</span>
                    </TooltipTrigger><TooltipContent>已完成任务数</TooltipContent></Tooltip>
                    <Tooltip><TooltipTrigger asChild>
                      <span className="org-status-stat"><IconMessageCircle size={11} /> {orgStats.total_messages_exchanged ?? 0}</span>
                    </TooltipTrigger><TooltipContent>消息交换总数</TooltipContent></Tooltip>
                    {orgStats.pending_messages > 0 && (
                      <Tooltip><TooltipTrigger asChild>
                        <span
                          className="org-status-stat"
                          style={{ color: "#f59e0b", cursor: "pointer" }}
                          onClick={() => { setSelectedNodeId(null); setSelectedEdgeId(null); setShowRightPanel(true); setPropsTab("overview"); }}
                        >▪ {orgStats.pending_messages}</span>
                      </TooltipTrigger><TooltipContent>节点间待处理内部消息 — 点击查看节点负荷</TooltipContent></Tooltip>
                    )}
                    {orgStats.anomalies?.length > 0 && (
                      <Tooltip><TooltipTrigger asChild>
                        <span
                          className="org-status-stat"
                          style={{ color: "#ef4444", fontWeight: 600, cursor: "pointer" }}
                          onClick={() => { setSelectedNodeId(null); setSelectedEdgeId(null); setShowRightPanel(true); setPropsTab("overview"); }}
                        >! {orgStats.anomalies.length}</span>
                      </TooltipTrigger><TooltipContent>点击查看异常详情</TooltipContent></Tooltip>
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
              <ToggleGroupItem value="canvas" className="text-xs h-7 px-3 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">编排</ToggleGroupItem>
              <ToggleGroupItem value="projects" className="text-xs h-7 px-3 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">项目</ToggleGroupItem>
              <ToggleGroupItem value="dashboard" className="text-xs h-7 px-3 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">看板</ToggleGroupItem>
            </ToggleGroup>
          </div>

          {/* ── Right: Actions ── */}
          <div className="org-topbar-right">
            {currentOrg.status === "archived" ? (
              <span style={{ fontSize: 11, color: "var(--muted)" }}>已归档</span>
            ) : currentOrg.status === "dormant" ? (
              <button className="org-tb-btn org-tb-btn--ok" onClick={handleStartOrg} title="启动组织">
                <IconPlay size={13} /> {!isMobile && "启动"}
              </button>
            ) : (
              <button className="org-tb-btn org-tb-btn--danger" onClick={handleStopOrg} title="停止组织">
                <IconStop size={13} /> {!isMobile && "停止"}
              </button>
            )}
            <button
              className={`org-tb-btn${(showRightPanel && !selectedNode && !selectedEdge) ? " org-tb-btn--active" : ""}`}
              onClick={() => { setShowRightPanel(!showRightPanel); setSelectedNodeId(null); setSelectedEdgeId(null); }}
              title="组织设置"
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
              <span style={{ fontWeight: 600, fontSize: 14 }}>组织编排</span>
              <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                <TooltipProvider delayDuration={300}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="link" size="sm" onClick={() => setShowTemplates(!showTemplates)} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">模板</Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">从模板创建组织</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="link" size="sm" onClick={() => void handleCreateOrg()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">新建</Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">新建空白组织</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="link" size="sm" onClick={() => orgImportRef.current?.click()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">导入</Button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">从文件导入组织</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            </div>
            {showTemplates && (
              <div style={{ padding: "0 8px 8px" }}>
                <div className="card" style={{ padding: 8, fontSize: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>从模板创建</div>
                  {templates.map((tpl) => (
                    <div key={tpl.id} onClick={() => handleCreateFromTemplate(tpl.id)}
                      style={{ padding: "6px 8px", borderRadius: "var(--radius-sm)", cursor: "pointer", display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}
                      className="navItem"
                    >
                      <span><IconBuilding size={14} /></span>
                      <div>
                        <div style={{ fontWeight: 500 }}>{tpl.name}</div>
                        <div style={{ fontSize: 10, color: "var(--muted)" }}>{tpl.node_count} 节点</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div style={{ flex: 1, overflowY: "auto", padding: "0 8px 8px" }}>
              {orgList.length === 0 && (
                <div style={{ textAlign: "center", color: "var(--muted)", fontSize: 12, padding: 20 }}>
                  暂无组织，点击上方创建
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
                      <div style={{ fontSize: 10, color: "var(--muted)" }}>{org.node_count} 节点 · {ORG_STATUS_LABELS[org.status] || org.status}</div>
                    </div>
                  </div>
                  <button className="btnSmall" onClick={(e) => { e.stopPropagation(); setConfirmDeleteOrgId(org.id); }} style={{ opacity: 0.5, fontSize: 10 }} title="删除组织">
                    <IconTrash size={10} />
                  </button>
                  {confirmDeleteOrgId === org.id && (
                    <div style={{ position: "absolute", right: 0, top: "100%", zIndex: 10, background: "var(--card-bg, #fff)", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px", boxShadow: "0 4px 12px rgba(0,0,0,0.12)", display: "flex", gap: 6, alignItems: "center", fontSize: 11 }} onClick={(e) => e.stopPropagation()}>
                      <span>确认删除?</span>
                      <button className="btnSmall" onClick={() => handleDeleteOrg(org.id)} style={{ color: "var(--danger)", fontSize: 11 }}>删除</button>
                      <button className="btnSmall" onClick={() => setConfirmDeleteOrgId(null)} style={{ fontSize: 11 }}>取消</button>
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
                alt="组织编排示意图"
                style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", borderRadius: 8 }}
              />
            </div>
            <div style={{ padding: "16px 20px", borderTop: "1px solid var(--line)", flexShrink: 0 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>欢迎使用组织编排</h3>
              <p style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.8 }}>
                组织编排可以让多个 AI Agent 协同工作——你可以设计组织架构、定义节点角色、配置协作关系，然后一键启动，让整个 AI 团队自动运转。
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
          <span style={{ fontWeight: 600, fontSize: 14 }}>组织编排</span>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="link" size="sm" onClick={() => setShowTemplates(!showTemplates)} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">
                    模板
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">从模板创建组织</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="link" size="sm" onClick={() => void handleCreateOrg()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">
                    新建
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">新建空白组织</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="link" size="sm" onClick={() => orgImportRef.current?.click()} disabled={creatingOrg} className="h-7 px-2 text-xs text-primary cursor-pointer">
                    导入
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">从文件导入组织</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <input
              ref={orgImportRef}
              type="file"
              accept=".json,.akita-org"
              style={{ display: "none" }}
              onChange={handleImportOrg}
            />
            <button className="btnSmall" onClick={() => setShowLeftPanel(false)} title="关闭" style={{ minWidth: 28, minHeight: 28, opacity: 0.5 }}>
              <IconX size={14} />
            </button>
          </div>
        </div>

        {/* Templates dropdown */}
        {showTemplates && (
          <div style={{ padding: "0 8px 8px" }}>
            <div className="card" style={{ padding: 8, fontSize: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>从模板创建</div>
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
                    <div style={{ fontSize: 10, color: "var(--muted)" }}>{tpl.node_count} 节点</div>
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
              暂无组织，点击上方创建
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
                    {org.node_count} 节点 · {ORG_STATUS_LABELS[org.status] || org.status}
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
                title="删除组织"
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
                  <span>确认删除?</span>
                  <button className="btnSmall" onClick={() => handleDeleteOrg(org.id)} style={{ color: "var(--danger)", fontSize: 11 }}>删除</button>
                  <button className="btnSmall" onClick={() => setConfirmDeleteOrgId(null)} style={{ fontSize: 11 }}>取消</button>
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
                <span>添加节点</span>
                <button className="org-modal-close" onClick={() => setShowNewNodeForm(false)}><IconX size={14} /></button>
              </div>
              <div className="org-modal-body">
                <label className="org-modal-label">岗位名称 *</label>
                <input
                  className="input"
                  placeholder="例如：产品经理"
                  value={newNodeTitle}
                  onChange={(e) => setNewNodeTitle(e.target.value)}
                  style={{ width: "100%", fontSize: 13, marginBottom: 12 }}
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
                />
                <label className="org-modal-label">部门（可选）</label>
                <input
                  className="input"
                  placeholder="例如：技术部"
                  value={newNodeDept}
                  onChange={(e) => setNewNodeDept(e.target.value)}
                  style={{ width: "100%", fontSize: 13 }}
                  onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
                />
              </div>
              <div className="org-modal-footer">
                <button className="org-modal-btn" onClick={() => setShowNewNodeForm(false)}>取消</button>
                <button className="org-modal-btn org-modal-btn--primary" onClick={handleAddNode}>添加</button>
              </div>
            </div>
          </div>,
          document.body
        )}

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
                  请先选择一个组织
                </div>
              )}
            </div>
          ) : (
          <div style={{ flex: 1, position: "relative" }} onContextMenu={(e) => e.preventDefault()}>
            <ReactFlow
              onInit={(instance) => {
                reactFlowRef.current = instance;
              }}
              nodes={nodes}
              edges={edges.map((e) => {
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
              })}
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
              nodesDraggable={!layoutLocked}
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
                  <button className="org-cvs-btn" onClick={() => setShowNewNodeForm(true)} title="添加节点">
                    <IconPlus size={13} /> 节点
                  </button>
                  <button className="org-cvs-btn" title="自动布局" onClick={() => { setNodes(computeTreeLayout(nodes, edges)); }}>
                    <IconSitemap size={13} /> 布局
                  </button>
                  <button
                    className={`org-cvs-btn${!layoutLocked ? " org-cvs-btn--active" : ""}`}
                    onClick={() => setLayoutLocked((v) => !v)}
                    title={layoutLocked ? "解锁拖拽" : "锁定布局，防止误拖拽"}
                  >
                    <IconSitemap size={13} /> {layoutLocked ? "拖拽关" : "拖拽开"}
                  </button>
                  {selectedNodeId && (
                    <button className="org-cvs-btn org-cvs-btn--danger" onClick={handleDeleteNode} title="删除选中节点">
                      <IconTrash size={13} />
                    </button>
                  )}
                </div>
                <div className="org-edge-legend">
                  {([
                    { type: "hierarchy", label: "上下级", dash: false },
                    { type: "collaborate", label: "协作", dash: true },
                    { type: "escalate", label: "上报", dash: false },
                    { type: "consult", label: "咨询", dash: false },
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
                  连线方式：拖拽或点击节点上下圆点
                </div>
              </Panel>
              {saveStatus !== "idle" && (
                <Panel position="bottom-center">
                  <div className={`org-save-indicator org-save-indicator--${saveStatus}`}>
                    {saveStatus === "saving" ? "保存中..." : saveStatus === "saved" ? <><IconCheck size={12} /> 已自动保存~</> : <span onClick={() => doSaveRef.current()} style={{ cursor: "pointer" }}>保存失败 · 重试</span>}
                  </div>
                </Panel>
              )}
            </ReactFlow>
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
                    <button onClick={() => { setChatPanelNode(contextMenu.id); setActiveDrawer("chat"); setContextMenu(null); }}>
                      <span className="org-ctx-icon">💬</span>与该节点对话
                    </button>
                  )}
                  {liveMode && selectedOrgId && (nodes.find(n => n.id === contextMenu.id)?.data as any)?.status === "frozen" && (
                    <button onClick={() => ctxUnfreezeNode(contextMenu.id!)}>
                      <span className="org-ctx-icon">🔓</span>解除冻结
                    </button>
                  )}
                  <button onClick={() => ctxCopyNode(contextMenu.id!)}>
                    <span className="org-ctx-icon">📋</span>复制节点
                  </button>
                  <button onClick={() => ctxDeleteNode(contextMenu.id!)}>
                    <span className="org-ctx-icon" style={{ color: "#ef4444" }}>🗑</span>删除节点
                  </button>
                </>)}
                {contextMenu.type === "edge" && contextMenu.id && (<>
                  <button onClick={() => ctxReverseEdge(contextMenu.id!)}>
                    <span className="org-ctx-icon">🔄</span>反转方向
                  </button>
                  <button onClick={() => ctxDeleteEdge(contextMenu.id!)}>
                    <span className="org-ctx-icon" style={{ color: "#ef4444" }}>🗑</span>删除连线
                  </button>
                </>)}
                {contextMenu.type === "pane" && (<>
                  <button onClick={() => ctxAddNodeAt()}>
                    <span className="org-ctx-icon">➕</span>添加节点
                  </button>
                  {clipboardNode && (
                    <button onClick={() => ctxPasteNode()}>
                      <span className="org-ctx-icon">📌</span>粘贴节点
                    </button>
                  )}
                  <button onClick={() => { setNodes(computeTreeLayout(nodes, edges)); setContextMenu(null); }}>
                    <span className="org-ctx-icon">🔀</span>自动布局
                  </button>
                </>)}
              </div>,
              document.body
            )}
            {/* ── Canvas bottom: live activity feed ── */}
            {liveMode && layoutLocked && orgStats && (() => {
              const perNode: any[] = orgStats.per_node || [];
              const recentTasks: any[] = orgStats.recent_tasks || [];
              const anomalies: any[] = orgStats.anomalies || [];
              const nodeLabel = (id: string) => {
                if (!id) return "";
                const nd = nodes.find(n => n.id === id);
                return (nd?.data as any)?.role_title || id?.slice(0, 6) || "";
              };
              const typeMeta: Record<string, { icon: string; label: string; tip: string; cls: string }> = {
                task_delegated:  { icon: "↗", label: "分配", tip: "分配任务给下级节点",       cls: "feed-delegated" },
                task_delivered:  { icon: "↙", label: "交付", tip: "向上级交付任务成果",       cls: "feed-delivered" },
                task_accepted:   { icon: "✓", label: "通过", tip: "上级验收通过",             cls: "feed-accepted" },
                task_rejected:   { icon: "✗", label: "打回", tip: "上级打回，需要重新处理",   cls: "feed-rejected" },
                task_timeout:    { icon: "⏱", label: "超时", tip: "任务执行超时",             cls: "feed-timeout" },
                task_completed:  { icon: "✓", label: "完成", tip: "节点执行完成",             cls: "feed-completed" },
                node_activated:  { icon: "▶", label: "执行", tip: "节点开始执行任务",         cls: "feed-activated" },
              };
              const defaultMeta = { icon: "•", label: "", tip: "", cls: "" };

              const busyLines: { key: string; node: string; text: string; pct: number }[] = [];
              for (const n of perNode) {
                if (n.status !== "busy" && !n.current_task_title) continue;
                const pp = n.plan_progress || {};
                const pct = pp.total > 0 ? Math.round((pp.completed / pp.total) * 100) : -1;
                const taskDesc = n.current_task_title || (n.current_task ? String(n.current_task) : "执行中…");
                busyLines.push({ key: n.id, node: n.role_title || nodeLabel(n.id), text: taskDesc, pct });
              }

              if (busyLines.length === 0 && recentTasks.length === 0 && anomalies.length === 0) return null;

              const FeedTip = ({ text }: { text: string }) => (
                <div className="org-feed-tip-content">
                  {mdModules ? (
                    <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>
                      {text}
                    </mdModules.ReactMarkdown>
                  ) : (
                    <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{text}</pre>
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
                        异常
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

          {/* ═══ Floating Chat FAB (always visible when org selected) ═══ */}
          {selectedOrgId && !chatPanelOpen && (
            <button
              onClick={() => { setChatPanelNode(null); setActiveDrawer("chat"); }}
              className="org-chat-fab"
              title="打开组织指挥台"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              <span className="org-chat-fab-label">指挥台</span>
            </button>
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
                  nodeId={chatPanelNode}
                  apiBaseUrl={apiBaseUrl}
                  showHeader
                  title={chatPanelNode
                    ? `对话 · ${(nodes.find(n => n.id === chatPanelNode)?.data as any)?.role_title || chatPanelNode}`
                    : `${currentOrg?.name || "组织"} · 指挥台`}
                  onClose={() => setActiveDrawer(null)}
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
            .org-chat-fab {
              position: absolute; bottom: 20px; right: 20px; z-index: 40;
              display: flex; align-items: center; gap: 8px;
              padding: 12px 20px; border: none; border-radius: 16px;
              background: linear-gradient(135deg, #3b82f6, #6366f1) !important;
              color: #ffffff !important; cursor: pointer; font-size: 13px; font-weight: 600;
              box-shadow: 0 4px 20px rgba(99,102,241,0.4), 0 0 40px rgba(99,102,241,0.15);
              transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
              animation: org-fab-in 0.4s cubic-bezier(0.34,1.56,0.64,1);
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

            .org-drawer-overlay {
              position: absolute; inset: 0; z-index: 80;
              background: rgba(0,0,0,0.3);
              backdrop-filter: blur(2px);
              animation: org-overlay-in 0.2s ease;
            }
            @keyframes org-overlay-in { from { opacity: 0; } to { opacity: 1; } }

            .org-drawer-slide {
              position: absolute; top: 0; right: 0; bottom: 0; z-index: 90;
              width: min(420px, 85%);
              background: var(--bg-app);
              border-left: 1px solid var(--line, rgba(51,65,85,0.5));
              box-shadow: -8px 0 30px rgba(0,0,0,0.3);
              animation: org-slide-in 0.3s cubic-bezier(0.4,0,0.2,1);
              display: flex; flex-direction: column; overflow: hidden;
            }
            @keyframes org-slide-in { from { transform: translateX(100%); } to { transform: translateX(0); } }

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

      {/* ── Monitor Panel (liveMode, desktop only) ── */}
      {liveMode && selectedNode && showRightPanel && !isMobile && (
        <div
          style={{
            width: 280, flexShrink: 0,
            borderLeft: "1px solid var(--line)",
            overflowY: "auto", scrollbarGutter: "stable",
            background: "var(--bg-app)",
          }}
        >
          <div style={{ padding: "12px 12px 8px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>运行监控</div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span style={{
                fontSize: 10, padding: "1px 6px", borderRadius: 4,
                background: `${STATUS_COLORS[selectedNode.status] || "var(--muted)"}20`,
                color: STATUS_COLORS[selectedNode.status] || "var(--muted)",
                fontWeight: 500,
              }}>
                {STATUS_LABELS[selectedNode.status] || selectedNode.status}
              </span>
              {selectedNode.is_clone && <span style={{ fontSize: 9, color: "#0369a1" }}>副本</span>}
              {selectedNode.ephemeral && <span style={{ fontSize: 9, color: "#b45309" }}>临时</span>}
            </div>
          </div>
          <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>

                {/* Tasks (detailed) */}
                {selectedNodeId && currentOrg && (
                  <NodeTasksTabContent
                    nodeTasks={nodeTasks}
                    nodeActivePlan={nodeActivePlan}
                    loading={nodeTasksLoading}
                    nodes={nodes}
                    apiBaseUrl={apiBaseUrl}
                    orgId={currentOrg.id}
                    fmtDateTime={fmtDateTime}
                  />
                )}

                {/* Chat */}
                {selectedOrgId && (
                  <div style={{ height: 360 }}>
                    <OrgChatPanel
                      orgId={selectedOrgId}
                      nodeId={selectedNodeId}
                      apiBaseUrl={apiBaseUrl}
                      compact
                    />
                  </div>
                )}

                {/* Schedules */}
                {nodeSchedules.length > 0 && (
                  <div className="card" style={{ padding: 10 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>定时任务</div>
                    {nodeSchedules.map((s: any) => (
                      <div key={s.id} style={{
                        padding: "4px 0",
                        borderBottom: "1px solid var(--line)",
                        fontSize: 11,
                      }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <span style={{ fontWeight: 500 }}>{s.name}</span>
                          <span style={{
                            fontSize: 10,
                            padding: "1px 5px",
                            borderRadius: 3,
                            background: s.enabled ? "#dcfce7" : "#f3f4f6",
                            color: s.enabled ? "#166534" : "#9ca3af",
                          }}>
                            {s.enabled ? "启用" : "禁用"}
                          </span>
                        </div>
                        {s.last_run_at && (
                          <div style={{ fontSize: 10, color: "#9ca3af", marginTop: 2 }}>
                            上次: {fmtDateTime(s.last_run_at)}
                          </div>
                        )}
                        {s.last_result_summary && (
                          <div style={{
                            fontSize: 10,
                            color: "#6b7280",
                            marginTop: 2,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}>
                            {s.last_result_summary}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Recent events */}
                <div className="card" style={{ padding: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                    最近活动
                    {nodeEvents.length > 0 && (
                      <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400, marginLeft: 4 }}>
                        ({nodeEvents.length})
                      </span>
                    )}
                  </div>
                  {nodeEvents.length === 0 ? (
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>暂无活动记录</div>
                  ) : (
                    <div style={{ maxHeight: 300, overflowY: "auto" }}>
                      {nodeEvents.slice(0, 15).map((evt: any, i: number) => {
                        const dataEntries = Object.entries(evt.data || {});
                        const isEvtExpanded = expandedThinkingIdx === `evt-${i}`;
                        const fullText = dataEntries.map(([k, v]) => `**${DATA_KEY_LABELS[k] || k}**: ${_translateDataValue(k, v, nodeNameMap)}`).join("\n\n");
                        return (
                          <div key={evt.event_id || i}
                            onClick={() => setExpandedThinkingIdx(isEvtExpanded ? null : `evt-${i}`)}
                            style={{
                              padding: "4px 0",
                              borderBottom: "1px solid var(--line)",
                              fontSize: 11, cursor: "pointer",
                              background: isEvtExpanded ? "var(--bg-subtle, transparent)" : undefined,
                            }}>
                            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <span style={{
                                width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                                background: evt.event_type?.includes("fail") || evt.event_type?.includes("error")
                                  ? "var(--danger)"
                                  : evt.event_type?.includes("complete")
                                  ? "var(--ok)"
                                  : "var(--primary)",
                              }} />
                              <span style={{ fontWeight: 500 }}>
                                {EVENT_TYPE_LABELS[evt.event_type] || evt.event_type?.replace(/_/g, " ")}
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
                              }}>
                                {mdModules ? (
                                  <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>
                                    {fullText}
                                  </mdModules.ReactMarkdown>
                                ) : <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{fullText}</pre>}
                              </div>
                            )}
                            {!isEvtExpanded && fullText.length > 80 && (
                              <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2, marginLeft: 12 }}>
                                点击展开全文
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Thought chain (merged timeline) */}
                <div className="card" style={{ padding: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                    思维链
                    {nodeThinking.length > 0 && (
                      <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400, marginLeft: 4 }}>
                        ({nodeThinking.length})
                      </span>
                    )}
                  </div>
                  {nodeThinking.length === 0 ? (
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>暂无思维链记录</div>
                  ) : (
                    <div style={{ maxHeight: 400, overflowY: "auto" }}>
                      {nodeThinking.slice(0, 30).map((item: any, i: number) => {
                        const isMsg = item.type === "message";
                        const isEvent = item.type === "event";
                        const tsLocal = fmtTime(item.timestamp);
                        const isExpanded = expandedThinkingIdx === i;

                        if (isMsg) {
                          const isOut = item.direction === "out";
                          const msgTypeColors: Record<string, string> = {
                            task_assign: "#7c3aed", task_result: "#059669",
                            question: "#2563eb", answer: "#0891b2",
                            escalation: "#dc2626", deliverable: "#d97706",
                          };
                          return (
                            <div key={i}
                              onClick={() => setExpandedThinkingIdx(isExpanded ? null : i)}
                              style={{
                                padding: "6px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                                cursor: "pointer", background: isExpanded ? "var(--bg-secondary)" : undefined,
                              }}
                            >
                              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
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
                                    background: `${msgTypeColors[item.msg_type] || "#6b7280"}18`,
                                    color: msgTypeColors[item.msg_type] || "#6b7280",
                                  }}>
                                    {MSG_TYPE_LABELS[item.msg_type] || item.msg_type.replace(/_/g, " ")}
                                  </span>
                                )}
                                <span style={{ color: "var(--muted)", fontSize: 10, marginLeft: "auto" }}>
                                  {tsLocal}
                                </span>
                              </div>
                              <div className="bb-entry-content" style={{
                                marginTop: 3, fontSize: 11,
                                maxHeight: isExpanded ? "none" : 60,
                                overflow: isExpanded ? "visible" : "hidden",
                              }}>
                                {mdModules ? (
                                  <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>
                                    {isExpanded
                                      ? (item.content || "")
                                      : (item.content || "").length > 150
                                        ? (item.content || "").slice(0, 150) + "…"
                                        : (item.content || "")}
                                  </mdModules.ReactMarkdown>
                                ) : (
                                  <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>
                                    {isExpanded ? (item.content || "") : (item.content || "").length > 150 ? (item.content || "").slice(0, 150) + "…" : (item.content || "")}
                                  </pre>
                                )}
                              </div>
                              {!isExpanded && (item.content || "").length > 150 && (
                                <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2 }}>
                                  点击展开全文
                                </div>
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
                              onClick={() => setExpandedThinkingIdx(isExpanded ? null : i)}
                              style={{
                                padding: "4px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                                cursor: "pointer", background: isExpanded ? "var(--bg-secondary)" : undefined,
                              }}
                            >
                              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                                <span style={{
                                  width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                                  background: isError ? "var(--danger)" : isComplete ? "var(--ok)"
                                    : isToolCall ? "#7c3aed" : "var(--primary)",
                                }} />
                                <span style={{
                                  fontWeight: 500, fontSize: 10,
                                  color: isToolCall ? "#7c3aed" : undefined,
                                }}>
                                  {isToolCall ? "⚙ " : ""}{EVENT_TYPE_LABELS[evtType] || evtType.replace(/_/g, " ")}
                                </span>
                                <span style={{ color: "var(--muted)", fontSize: 10, marginLeft: "auto" }}>
                                  {tsLocal}
                                </span>
                              </div>
                              {item.data && Object.keys(item.data).length > 0 && (() => {
                                const entries = Object.entries(item.data).slice(0, isExpanded ? 20 : 3);
                                const mdText = entries.map(([k, v]) => { const tv = _translateDataValue(k, v, nodeNameMap); return `**${DATA_KEY_LABELS[k] || k}**: ${isExpanded ? tv : tv.slice(0, 120)}`; }).join("\n\n");
                                return (
                                  <div className="bb-entry-content" style={{ fontSize: 10, marginTop: 2, marginLeft: 12 }}>
                                    {mdModules ? (
                                      <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>
                                        {mdText}
                                      </mdModules.ReactMarkdown>
                                    ) : <span style={{ color: "var(--muted)" }}>{mdText}</span>}
                                  </div>
                                );
                              })()}
                              {!isExpanded && item.data && Object.keys(item.data).length > 3 && (
                                <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2, marginLeft: 12 }}>
                                  点击查看全部 {Object.keys(item.data).length} 个字段
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
          }}
        >
          <div style={{ padding: "12px 12px 8px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{selectedNode.role_title}</div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>
                {selectedNode.department || "未分配部门"}
                {liveMode && <span style={{ marginLeft: 6, fontSize: 10, color: "#9ca3af" }}>· 配置已锁定</span>}
              </div>
            </div>
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
              {liveMode && selectedOrgId && (
                <button
                  className="btnSmall"
                  onClick={() => { setChatPanelNode(selectedNodeId); setActiveDrawer("chat"); }}
                  style={{
                    minWidth: 36, minHeight: 36, fontSize: 12,
                    background: "linear-gradient(135deg, #3b82f6, #6366f1)",
                    color: "#fff", border: "none", borderRadius: 8,
                  }}
                  title="与该节点对话"
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
                概览
              </ToggleGroupItem>
              <ToggleGroupItem value="identity" className="h-8 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                身份
              </ToggleGroupItem>
              <ToggleGroupItem value="capabilities" className="h-8 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                能力
              </ToggleGroupItem>
            </ToggleGroup>
          </div>

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
                      {orgStats.health === "healthy" ? "运行正常" : orgStats.health === "critical" ? "存在异常" : orgStats.health === "warning" ? "需要关注" : "有待观察"}
                    </div>
                    <div style={{ fontSize: 10, color: "#9ca3af" }}>
                      {orgStats.anomalies?.length > 0 ? `${orgStats.anomalies.length} 个告警` : "所有节点状态良好"}
                    </div>
                  </div>
                </div>

                {/* KPI grid */}
                <div className="card" style={{ padding: 12 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>运行指标</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                    {[
                      { label: "运行时长", value: orgStats.uptime_s ? (orgStats.uptime_s >= 3600 ? `${Math.floor(orgStats.uptime_s / 3600)}h${Math.floor((orgStats.uptime_s % 3600) / 60)}m` : `${Math.round(orgStats.uptime_s / 60)}m`) : "-", color: "var(--primary)" },
                      { label: "完成任务", value: orgStats.total_tasks_completed ?? 0, color: "#22c55e" },
                      { label: "消息交换", value: orgStats.total_messages_exchanged ?? 0, color: "#3b82f6" },
                      { label: "待处理", value: orgStats.pending_messages ?? 0, color: orgStats.pending_messages > 5 ? "#f59e0b" : "#6b7280" },
                      { label: "未读消息", value: orgStats.unread_inbox ?? 0, color: orgStats.unread_inbox > 0 ? "#dc2626" : "#6b7280" },
                      { label: "待审批", value: orgStats.pending_approvals ?? 0, color: orgStats.pending_approvals > 0 ? "#7c3aed" : "#6b7280" },
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
                      告警 ({orgStats.anomalies.length})
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
                            {a.type === "error" ? "错误" : a.type === "stuck" ? "卡住" : a.type === "long_idle" ? "空闲" : "积压"}
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
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>节点负荷</div>
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
                              {nd.current_task}
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
                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>黑板最新动态</div>
                    <div style={{ maxHeight: 160, overflowY: "auto" }}>
                      {orgStats.recent_blackboard.map((bb: any, i: number) => {
                        const bbTypeColors: Record<string, string> = {
                          fact: "#3b82f6", decision: "#8b5cf6", lesson: "#10b981",
                          progress: "#6366f1", todo: "#ef4444",
                        };
                        const bbTypeLabels: Record<string, string> = {
                          fact: "事实", decision: "决策", lesson: "经验",
                          progress: "进展", todo: "待办",
                        };
                        const tc = bbTypeColors[bb.memory_type] || "#6b7280";
                        return (
                          <div key={i} style={{
                            padding: "5px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                          }}>
                            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <span style={{
                                fontSize: 11, padding: "1px 5px", borderRadius: 3,
                                background: tc + "20", color: tc, fontWeight: 600,
                              }}>
                                {bbTypeLabels[bb.memory_type] || bb.memory_type}
                              </span>
                              <span style={{ fontSize: 11, color: "var(--muted)" }}>{(() => { const nd = nodes.find(n => n.id === bb.source_node); return (nd?.data as any)?.role_title || bb.source_node; })()}</span>
                              <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
                                {fmtTime(bb.timestamp)}
                              </span>
                            </div>
                            <div className="bb-entry-content" style={{ marginTop: 3 }}>
                              {mdModules ? (
                                <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>
                                  {bb.content}
                                </mdModules.ReactMarkdown>
                              ) : (
                                <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{bb.content}</pre>
                              )}
                            </div>
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
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>头像</label>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  {AVATAR_PRESETS.map((av) => {
                    const isSel = selectedNode.avatar === av.id;
                    return (
                      <OrgAvatar
                        key={av.id}
                        avatarId={av.id}
                        size={36}
                        onClick={() => updateNodeData("avatar", av.id)}
                        style={{
                          cursor: "pointer",
                          border: isSel ? "2.5px solid var(--primary)" : "2.5px solid transparent",
                          boxShadow: isSel ? "0 0 0 2px var(--primary)" : "none",
                          opacity: isSel ? 1 : 0.75,
                          transition: "all 0.15s",
                        }}
                      />
                    );
                  })}
                  {/* Upload custom avatar */}
                  <label
                    title="上传自定义头像"
                    style={{
                      width: 36, height: 36, borderRadius: 8,
                      border: "2px dashed var(--muted, #9ca3af)",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      cursor: "pointer", opacity: 0.6, transition: "opacity .15s",
                      fontSize: 18, color: "var(--muted, #9ca3af)",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.opacity = "1")}
                    onMouseLeave={(e) => (e.currentTarget.style.opacity = "0.6")}
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
                          alert("图片不能超过 2MB");
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
                            alert(`上传失败: ${err}`);
                          }
                        } catch (err) {
                          alert(`上传失败: ${err}`);
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
                    <span style={{ fontSize: 11, color: "var(--muted)" }}>自定义头像</span>
                    <button
                      className="btn btn-sm"
                      style={{ fontSize: 11, padding: "2px 6px" }}
                      onClick={() => updateNodeData("avatar", null)}
                    >
                      移除
                    </button>
                  </div>
                )}
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>岗位名称</label>
                <input
                  className="input"
                  value={selectedNode.role_title}
                  onChange={(e) => updateNodeData("role_title", e.target.value)}
                  placeholder="如：技术总监、前端工程师、QA 负责人"
                  style={{ fontSize: 13 }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>Agent 来源
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— 决定节点的专业能力</span>
                </label>
                <div style={{ display: "flex", gap: 6 }}>
                  <select
                    className="input"
                    value={selectedNode.agent_source.startsWith("ref:") ? "ref" : "local"}
                    onChange={(e) => updateNodeData("agent_source", e.target.value === "local" ? "local" : `ref:${selectedNode.agent_profile_id || ""}`)}
                    style={{ fontSize: 13, flex: 1 }}
                  >
                    <option value="local">本地专属</option>
                    <option value="ref">引用已有 Agent</option>
                  </select>
                </div>
                {selectedNode.agent_source.startsWith("ref:") && (
                  <>
                    <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginTop: -4 }}>选择 Agent</label>
                    <div style={{ position: "relative" }}>
                      <div
                        className="input"
                        onClick={() => { setAgentDropdownOpen(!agentDropdownOpen); setAgentProfileSearch(""); }}
                        style={{
                          fontSize: 13, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between",
                          background: selectedNode.agent_profile_id ? undefined : "var(--bg-app)",
                        }}
                      >
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {(() => {
                            const ap = agentProfiles.find(p => p.id === selectedNode.agent_profile_id);
                            return ap ? `${ap.icon || "🤖"} ${ap.name}` : "点击选择...";
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
                              placeholder="搜索 Agent..."
                              autoFocus
                              style={{ fontSize: 12, width: "100%" }}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </div>
                          <div style={{ overflowY: "auto", flex: 1 }}>
                            {agentProfiles.length === 0 ? (
                              <div style={{ padding: 12, color: "var(--muted)", textAlign: "center", fontSize: 11 }}>
                                暂无可用 Agent，请先在 Agent 管理页创建
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
                                    <span style={{ fontSize: 16, flexShrink: 0 }}>{ap.icon || "🤖"}</span>
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
                  岗位目标
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— 这个岗位要达成什么</span>
                </label>
                <textarea
                  className="input"
                  value={selectedNode.role_goal}
                  onChange={(e) => updateNodeData("role_goal", e.target.value)}
                  rows={2}
                  placeholder="如：负责整体技术架构设计，把控代码质量，推进技术选型和落地"
                  style={{ fontSize: 13, resize: "vertical" }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                  角色背景
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— 专业经验和能力特长</span>
                </label>
                <textarea
                  className="input"
                  value={selectedNode.role_backstory}
                  onChange={(e) => updateNodeData("role_backstory", e.target.value)}
                  rows={3}
                  placeholder="如：10年全栈开发经验，精通 Python/TypeScript，熟悉微服务架构，曾主导多个大型项目的技术选型"
                  style={{ fontSize: 13, resize: "vertical" }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>部门</label>
                <input
                  className="input"
                  value={selectedNode.department}
                  onChange={(e) => updateNodeData("department", e.target.value)}
                  style={{ fontSize: 13 }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>层级</label>
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
                    提示词构成说明
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.7 }}>
                    <div>系统会自动将以下信息拼装为完整的组织 Agent 提示词：</div>
                    <div style={{ marginTop: 4, paddingLeft: 8 }}>
                      <div>1. <b>精简身份声明</b> — 角色定位 + 协作原则（自动生成）</div>
                      <div>2. <b>角色描述</b> — 来自 ROLE.md / 自定义提示词 / 岗位信息</div>
                      <div>3. <b>组织上下文</b> — 核心业务、架构、上下级关系、权限、黑板</div>
                      <div>4. <b>运行环境</b> — 时间、OS、Shell 等（自动注入）</div>
                      <div>5. <b>工具清单</b> — org_* 工具 + 节点配置的外部工具</div>
                      <div>6. <b>行为准则 &amp; 策略红线</b> — 协作规则、交付流程</div>
                    </div>
                    <div style={{ marginTop: 6 }}>
                      角色描述优先级：ROLE.md 文件 &gt; 自定义提示词 &gt; AgentProfile &gt; 自动生成
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
                      自定义提示词
                    </div>
                    <button
                      className="btnSmall"
                      style={{ fontSize: 10, padding: "2px 8px" }}
                      onClick={() => {
                        if (selectedNode.custom_prompt && !confirm("将覆盖当前自定义提示词，确认？")) return;
                        const tpl = `你是一位经验丰富的${selectedNode.role_title || "专业人员"}。\n\n## 核心职责\n- ${selectedNode.role_goal || "待定义"}\n\n## 工作风格\n- 沟通简洁高效，结论先行\n- 重要决策写入组织黑板\n- 主动向上级汇报进展\n\n## 专业背景\n${selectedNode.role_backstory || "请在此描述角色的专业背景、经验和能力特长"}`;
                        updateNodeData("custom_prompt", tpl);
                      }}
                    >
                      填充模板
                    </button>
                  </div>
                  <textarea
                    className="input"
                    value={selectedNode.custom_prompt}
                    onChange={(e) => updateNodeData("custom_prompt", e.target.value)}
                    rows={10}
                    placeholder={"可选。不填写时系统将根据岗位名称、目标、背景自动生成角色描述。\n\n填写后将替代自动生成的内容，可更精细地控制角色行为。\n\n示例：\n你是一位资深前端工程师，擅长 React/Vue...\n\n## 核心职责\n- 负责前端架构设计和代码审查\n- 协调前端团队的开发进度"}
                    style={{ fontSize: 12, resize: "vertical", fontFamily: "monospace", lineHeight: 1.5, minHeight: 120 }}
                  />
                  <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                    {selectedNode.custom_prompt
                      ? `已配置自定义提示词（${selectedNode.custom_prompt.length} 字符）`
                      : `未配置。系统将自动生成："你是${selectedNode.role_title || "..."}。目标：${selectedNode.role_goal ? selectedNode.role_goal.slice(0, 20) + "..." : "..."}"`}
                  </div>
                </div>

                {/* Section 3: Prompt preview */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--card-bg, #fff)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                      提示词预览
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                      {fullPromptPreview !== null && (
                        <button
                          className="btnSmall"
                          style={{ fontSize: 10, padding: "2px 8px" }}
                          onClick={() => setFullPromptPreview(null)}
                        >
                          简略
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
                              setFullPromptPreview("(获取失败，请先保存组织配置)");
                            }
                          } catch {
                            setFullPromptPreview("(获取失败)");
                          }
                          setPromptPreviewLoading(false);
                        }}
                      >
                        {promptPreviewLoading ? "..." : "完整预览"}
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
                        : `你是${selectedNode.role_title || "(未设置岗位名称)"}。${selectedNode.role_goal ? `目标：${selectedNode.role_goal}。` : ""}${selectedNode.role_backstory ? `背景：${selectedNode.role_backstory}。` : ""}`}
                  </div>
                  {fullPromptPreview === null && (
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                      以上为角色描述部分。点击「完整预览」查看含组织架构、关系、权限等的完整提示词。
                    </div>
                  )}
                  {fullPromptPreview !== null && (
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                      以上为组织上下文提示词（{fullPromptPreview.length} 字符）。实际运行时还会追加运行环境、工具清单、行为准则等（约 1500-2500 字符）。
                    </div>
                  )}
                </div>

                {/* Section 4: Identity files info */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--card-bg, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 4 }}>
                    高级：身份文件
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
                    如需更精细的角色控制，可在组织目录下创建节点专属身份文件：
                    <div style={{ fontFamily: "monospace", fontSize: 10, marginTop: 4, paddingLeft: 8 }}>
                      <div>nodes/{selectedNode.id}/identity/ROLE.md — 角色定义（最高优先级）</div>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--warning, #b8860b)", marginTop: 6 }}>
                      注意：组织模式下使用精简协作身份，不注入 SOUL.md / AGENT.md
                      的完整内容（其「单打独斗」哲学与组织协作冲突）。ROLE.md 正常生效。
                    </div>
                  </div>
                </div>
                </fieldset>
            )}

            {propsTab === "capabilities" && (
              <fieldset disabled={!!liveMode} style={{ border: "none", margin: 0, padding: 0, minWidth: 0, opacity: liveMode ? 0.5 : 1, display: "flex", flexDirection: "column", gap: 10 }}>
                {/* ── Section 1: 执行工具类目 ── */}
                <Card className="gap-0 overflow-hidden py-0">
                  <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
                    <div className="min-w-0">
                      <CardTitle className="text-sm">执行工具</CardTitle>
                      <CardDescription className="mt-1 text-[11px]">
                        未选择时只能使用组织协作工具
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
                      title="根据岗位角色自动推荐工具"
                    >
                      自动推荐
                    </Button>
                  </div>
                  <CardContent className="grid grid-cols-2 gap-2 px-3 py-3">
                    {[
                      { key: "research", label: "搜索" },
                      { key: "planning", label: "计划" },
                      { key: "filesystem", label: "文件/命令" },
                      { key: "memory", label: "记忆" },
                      { key: "browser", label: "浏览器" },
                      { key: "communication", label: "通信" },
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
                </Card>

                {/* ── Section 2: MCP 服务器 ── */}
                <Card className="gap-0 overflow-hidden py-0">
                  <CardHeader className="px-4 py-3" style={{ borderBottom: "1px solid var(--line)" }}>
                    <CardTitle className="text-sm">MCP 服务器</CardTitle>
                    <CardDescription className="text-[11px]">
                      节点可调用的外部服务接口
                    </CardDescription>
                  </CardHeader>
                  {availableMcpServers.length > 3 && (
                    <CardContent className="px-3 pt-3 pb-0">
                      <ShadInput
                        placeholder="搜索服务器..."
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
                              {srv.status === "connected" ? "在线" : "离线"}
                            </Badge>
                          </label>
                        );
                      })}
                    </CardContent>
                  ) : (
                    <CardContent className="px-4 py-3 text-[11px] text-muted-foreground">
                      暂无可用服务器
                    </CardContent>
                  )}
                  {selectedNode.mcp_servers.length > 0 && (
                    <div className="border-t px-4 py-2 text-[10px] text-muted-foreground">
                      已选 {selectedNode.mcp_servers.length} 个
                    </div>
                  )}
                </Card>

                {/* ── Section 3: 技能 ── */}
                <Card className="gap-0 overflow-hidden py-0">
                  <CardHeader className="px-4 py-3" style={{ borderBottom: "1px solid var(--line)" }}>
                    <CardTitle className="text-sm">技能</CardTitle>
                    <CardDescription className="text-[11px]">
                      已安装的专业技能包
                    </CardDescription>
                  </CardHeader>
                  {availableSkills.length > 3 && (
                    <CardContent className="px-3 pt-3 pb-0">
                      <ShadInput
                        placeholder="搜索技能..."
                        value={skillSearch}
                        onChange={(e) => setSkillSearch(e.target.value)}
                        className="h-8 text-xs"
                      />
                    </CardContent>
                  )}
                  {availableSkills.length > 0 ? (
                    <CardContent className="max-h-[150px] space-y-2 overflow-y-auto px-3 py-3">
                      {availableSkills
                        .filter((skill) => {
                          if (!skillSearch) return true;
                          const q = skillSearch.toLowerCase();
                          const ni = skill.name_i18n;
                          const di = skill.description_i18n;
                          const nameStr = typeof ni === "object" && ni ? ((ni as any).zh || (ni as any).en || "") : (ni || "");
                          const descStr = typeof di === "object" && di ? ((di as any).zh || (di as any).en || "") : (di || "");
                          return nameStr.toLowerCase().includes(q)
                            || skill.name.toLowerCase().includes(q)
                            || descStr.toLowerCase().includes(q)
                            || (skill.description || "").toLowerCase().includes(q);
                        })
                        .map((skill) => {
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
                      })}
                    </CardContent>
                  ) : (
                    <CardContent className="px-4 py-3 text-[11px] text-muted-foreground">
                      暂无可用技能
                    </CardContent>
                  )}
                  {selectedNode.skills.length > 0 && (
                    <div className="border-t px-4 py-2 text-[10px] text-muted-foreground">
                      已选 {selectedNode.skills.length} 个
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
                    已选择 MCP 服务器但未启用"搜索"等工具类目中的 MCP 调用能力。
                    <Button
                      variant="outline"
                      size="sm"
                      className="ml-1 h-6 px-2 text-[10px] align-middle"
                      onClick={() => {
                        const cur = selectedNode.external_tools || [];
                        if (!cur.includes("mcp")) updateNodeData("external_tools", [...cur, "mcp"]);
                      }}
                    >
                      一键启用
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
                    <CardTitle className="text-sm">性能限制</CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-3 px-4 pb-4">
                    <div>
                      <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">并行任务数</ShadLabel>
                      <ShadInput
                        type="number"
                        min={1}
                        value={selectedNode.max_concurrent_tasks}
                        onChange={(e) => updateNodeData("max_concurrent_tasks", parseInt(e.target.value) || 1)}
                        className="h-8 text-xs"
                      />
                    </div>
                    <div>
                      <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">超时 (秒)</ShadLabel>
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
                      <CardTitle className="text-sm">自动分身</CardTitle>
                      <CardDescription className="mt-1 text-[11px] leading-5">
                        任务堆积超过阈值时自动创建分身处理。
                      </CardDescription>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 pt-0.5">
                      <ShadLabel className="cursor-pointer text-[11px] text-muted-foreground" htmlFor="auto-clone-enabled">启用</ShadLabel>
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
                        <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">触发阈值（待处理数）</ShadLabel>
                        <ShadInput
                          type="number"
                          min={2}
                          value={selectedNode.auto_clone_threshold || 3}
                          onChange={(e) => updateNodeData("auto_clone_threshold", parseInt(e.target.value) || 3)}
                          className="h-8 text-xs"
                        />
                      </div>
                      <div>
                        <ShadLabel className="mb-1.5 block text-[11px] text-muted-foreground">最大分身数</ShadLabel>
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
                    任务堆积超过阈值时自动创建分身处理。分身共享岗位记忆，同一任务链由同一分身完成。空闲分身在心跳时自动回收。
                  </div>
                  </CardContent>
                </Card>

                {/* Permissions section */}
                <Card className="gap-0 overflow-hidden py-0">
                  <CardHeader className="px-4 py-3" style={{ borderBottom: "1px solid var(--line)" }}>
                    <CardTitle className="text-sm">权限控制</CardTitle>
                    <CardDescription className="text-[11px]">
                      控制节点在组织内的行为权限
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-2 px-3 py-3">
                    {([
                      { key: "can_delegate", label: "委派任务" },
                      { key: "can_escalate", label: "上报问题" },
                      { key: "can_request_scaling", label: "申请扩编" },
                      { key: "ephemeral", label: "临时节点" },
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
                    <CardTitle className="text-sm">LLM 端点偏好</CardTitle>
                    <CardDescription className="text-[11px]">
                      留空时使用默认端点
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="px-4 pb-4">
                    <ShadInput
                      value={selectedNode.preferred_endpoint || ""}
                      onChange={(e) => updateNodeData("preferred_endpoint", e.target.value || null)}
                      placeholder="留空使用默认端点"
                      className="h-8 text-xs"
                    />
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
            <span className="text-sm font-semibold">连线属性</span>
            <Button variant="ghost" size="icon-xs" onClick={() => setSelectedEdgeId(null)}>
              <XIcon className="size-3.5" />
            </Button>
          </div>

          {/* Source / Target */}
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs leading-relaxed text-muted-foreground">
            <div>起点: <strong className="text-foreground">{(() => { const n = nodes.find(n => n.id === selectedEdge.source); return (n?.data as any)?.role_title || selectedEdge.source; })()}</strong></div>
            <div>终点: <strong className="text-foreground">{(() => { const n = nodes.find(n => n.id === selectedEdge.target); return (n?.data as any)?.role_title || selectedEdge.target; })()}</strong></div>
          </div>

          {/* Edge type */}
          <div className="space-y-2">
            <ShadLabel className="text-xs">连线类型</ShadLabel>
            <ToggleGroup
              type="single"
              value={selectedEdge.edge_type || "hierarchy"}
              onValueChange={(v) => { if (v) updateEdgeData("edge_type", v); }}
              className="flex flex-wrap gap-1.5"
            >
              {([
                { key: "hierarchy", label: "上下级", color: EDGE_COLORS.hierarchy },
                { key: "collaborate", label: "协作", color: EDGE_COLORS.collaborate },
                { key: "escalate", label: "上报", color: EDGE_COLORS.escalate },
                { key: "consult", label: "咨询", color: EDGE_COLORS.consult || "var(--muted)" },
              ] as const).map((t) => (
                <ToggleGroupItem
                  key={t.key}
                  value={t.key}
                  size="sm"
                  className="h-7 gap-1.5 px-2.5 text-xs data-[state=on]:font-semibold"
                  style={selectedEdge.edge_type === t.key ? { color: t.color, borderColor: t.color } : undefined}
                >
                  <span className="inline-block h-0.5 w-2.5 rounded-full" style={{ background: t.color }} />
                  {t.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          {/* Label */}
          <div className="space-y-2">
            <ShadLabel className="text-xs" htmlFor="edge-label">标签</ShadLabel>
            <ShadInput
              id="edge-label"
              className="h-8 text-xs"
              placeholder="可选，如「技术指导」「审批」"
              value={selectedEdge.label || ""}
              onChange={(e) => updateEdgeData("label", e.target.value)}
            />
          </div>

          {/* Bidirectional */}
          <div className="flex items-center justify-between rounded-md border border-border px-3 py-2.5">
            <div className="space-y-0.5">
              <ShadLabel className="text-xs cursor-pointer" htmlFor="edge-bidir">双向通信</ShadLabel>
              <p className="text-[11px] text-muted-foreground leading-tight">关闭后只能从起点向终点发消息</p>
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
              <ShadLabel className="text-xs">优先级</ShadLabel>
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
            <ShadLabel className="text-xs" htmlFor="edge-bw">通信频率上限 (次/小时)</ShadLabel>
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
              <IconTrash size={12} /> 删除连线
            </Button>
          </div>
        </div>
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
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>组织设置</div>

          {/* ── 运行模式 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>运行模式</div>
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
                命令模式
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
                自主模式
              </button>
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.5 }}>
              {((currentOrg as any).operation_mode || "command") === "command"
                ? "通过聊天或命令面板下达任务，按需执行"
                : "组织根据核心业务自动运转，顶层负责人持续运营"}
            </div>
          </div>

          {/* ── 核心业务 (仅自主模式) ── */}
          {((currentOrg as any).operation_mode || "command") === "autonomous" && (
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setBizCollapsed(!bizCollapsed)}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                核心业务
                {bizCollapsed && (currentOrg.core_business || "").trim() && (
                  <span style={{ fontWeight: 400, fontSize: 11, color: "var(--ok)", marginLeft: 6 }}>已配置</span>
                )}
              </div>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{bizCollapsed ? "▸" : "▾"}</span>
            </div>
            {!bizCollapsed && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6, lineHeight: 1.5 }}>
                  填写后组织启动即自主运转——顶层负责人自动接收任务书并开始工作，心跳变为定期复盘。
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
                  {[
                    { label: "创业公司", tpl: "## 业务定位\n我们是一家___公司，核心产品/服务是___。\n\n## 当前阶段目标\n- 完成产品 MVP 并上线\n- 获取首批 100 个种子用户\n- 验证产品-市场匹配度\n\n## 工作策略\n- 产品优先：先打磨核心功能，再扩展\n- 精益运营：小规模验证后再投入推广资源\n- 数据驱动：关注用户留存率和活跃度\n\n## 主动运营要求\n负责人需持续推进：产品开发进度跟踪、市场调研执行、用户反馈收集与分析、团队任务协调。每个复盘周期应有可交付成果。" },
                    { label: "内容运营", tpl: "## 业务定位\n面向___领域的内容创作与分发平台/账号。\n\n## 当前阶段目标\n- 建立稳定的内容生产流程（每周___篇）\n- 核心平台粉丝/订阅达到___\n- 形成可复制的爆款内容方法论\n\n## 工作策略\n- 选题驱动：每周策划会确定选题方向\n- 数据复盘：分析每篇内容的阅读/互动数据\n- 持续迭代：根据数据调整内容策略\n\n## 主动运营要求\n负责人需持续推进：选题策划与分配、内容质量把控、发布排期管理、数据复盘与策略调整。确保内容产出不中断。" },
                    { label: "软件项目", tpl: "## 项目定位\n为___开发的___系统/应用。\n\n## 当前阶段目标\n- 完成___模块的开发与测试\n- 交付可演示的版本给___\n- 技术文档同步更新\n\n## 工作策略\n- 迭代开发：按优先级排列功能，每轮迭代2周\n- 质量保障：代码审查 + 自动化测试覆盖\n- 文档先行：关键架构决策必须文档化\n\n## 主动运营要求\n负责人需持续推进：任务拆解与分配、代码审查、进度跟踪、阻塞问题排除、与需求方沟通确认。" },
                    { label: "研究课题", tpl: "## 课题方向\n研究___领域的___问题。\n\n## 当前阶段目标\n- 完成文献调研，形成研究综述\n- 确定研究方案和实验设计\n- 产出阶段性研究报告\n\n## 工作策略\n- 文献先行：系统梳理相关领域进展\n- 实验验证：设计对照实验验证假设\n- 定期交流：团队内部周会分享进展\n\n## 主动运营要求\n负责人需持续推进：文献调研分配、研究方案讨论、实验进度追踪、成果整理与汇报。" },
                    { label: "电商运营", tpl: "## 业务定位\n面向___的___品类电商。\n\n## 当前阶段目标\n- 完成店铺搭建和首批___个 SKU 上架\n- 月销售额达到___\n- 建立稳定的供应链和客服流程\n\n## 工作策略\n- 选品驱动：通过市场分析确定主推品类\n- 流量获取：___平台引流 + 内容营销\n- 复购优先：客户满意度和复购率是核心指标\n\n## 主动运营要求\n负责人需持续推进：选品调研、供应链管理、营销活动策划执行、客户反馈处理、数据分析与策略调整。确保日常运营不中断。" },
                  ].map((tpl) => (
                    <button
                      key={tpl.label}
                      className="btnSmall"
                      style={{ fontSize: 11, padding: "3px 8px" }}
                      onClick={() => {
                        if ((currentOrg.core_business || "").trim() && !confirm("将覆盖当前内容，确认？")) return;
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
                  placeholder={"填写或选择模板后编辑。\n\n组织启动后，顶层节点将根据此内容自动制定策略、分配任务、持续推进。"}
                  value={currentOrg.core_business || ""}
                  onChange={(e) => setCurrentOrg({ ...currentOrg, core_business: e.target.value })}
                />
                {(currentOrg.core_business || "").trim() && (
                  <div style={{ fontSize: 11, color: "var(--ok)", marginTop: 4 }}>
                    启动组织后，顶层负责人将自动接收任务书并开始自主运营
                  </div>
                )}
              </div>
            )}
          </div>
          )}

          {/* ── 用户身份 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setPersonaCollapsed(!personaCollapsed)}
            >
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                用户身份
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
                  你在本组织中的角色。节点会以此身份认知你。
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
                  {[
                    { title: "董事长", desc: "组织最高决策者" },
                    { title: "产品负责人", desc: "项目需求方与最终验收人" },
                    { title: "出品人", desc: "内容方向决策者" },
                    { title: "投资人", desc: "外部投资方" },
                    { title: "甲方", desc: "项目委托方" },
                    { title: "课题负责人", desc: "研究课题主持人" },
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
                      <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>头衔</label>
                      <input
                        className="input"
                        style={{ width: "100%", fontSize: 12 }}
                        placeholder="董事长"
                        value={currentOrg.user_persona?.title || ""}
                        onChange={(e) => setCurrentOrg({
                          ...currentOrg,
                          user_persona: { ...currentOrg.user_persona, title: e.target.value, display_name: currentOrg.user_persona?.display_name || "", description: currentOrg.user_persona?.description || "" },
                        })}
                      />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>显示名</label>
                      <input
                        className="input"
                        style={{ width: "100%", fontSize: 12 }}
                        placeholder="留空用头衔"
                        value={currentOrg.user_persona?.display_name || ""}
                        onChange={(e) => setCurrentOrg({
                          ...currentOrg,
                          user_persona: { ...currentOrg.user_persona, title: currentOrg.user_persona?.title || "负责人", display_name: e.target.value, description: currentOrg.user_persona?.description || "" },
                        })}
                      />
                    </div>
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 2 }}>简介</label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 12 }}
                      placeholder="例如：组织最高决策者"
                      value={currentOrg.user_persona?.description || ""}
                      onChange={(e) => setCurrentOrg({
                        ...currentOrg,
                        user_persona: { ...currentOrg.user_persona, title: currentOrg.user_persona?.title || "负责人", display_name: currentOrg.user_persona?.display_name || "", description: e.target.value },
                      })}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ── Quick actions ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>操作</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={() => setConfirmReset(true)}>重置组织</button>
              <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={handleExportOrg}>导出配置</button>
              <label className="btnSmall" style={{ fontSize: 11, padding: "4px 8px", cursor: "pointer" }}>
                导入配置
                <input type="file" accept=".json" style={{ display: "none" }} onChange={handleImportOrg} />
              </label>
              {liveMode && (<>
                <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={async () => {
                  try { await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/heartbeat/trigger`, { method: "POST" }); } catch {}
                }}>触发心跳</button>
                <button className="btnSmall" style={{ fontSize: 11, padding: "4px 8px" }} onClick={async () => {
                  try { await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/standup/trigger`, { method: "POST" }); } catch {}
                }}>触发晨会</button>
              </>)}
            </div>
          </div>

          {/* ── Blackboard ── */}
          <div style={{ marginTop: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>组织黑板</div>
              <button
                className="btnSmall"
                style={{ fontSize: 11, padding: "2px 8px" }}
                onClick={() => fetchBlackboard(currentOrg.id, bbScope)}
                disabled={bbLoading}
              >
                {bbLoading ? "..." : "刷新"}
              </button>
            </div>

            <div style={{ display: "flex", gap: 2, marginBottom: 8 }}>
              {([
                { key: "all", label: "全部" },
                { key: "org", label: "组织级" },
                { key: "department", label: "部门级" },
                { key: "node", label: "节点级" },
              ] as const).map((s) => (
                <button
                  key={s.key}
                  className="btnSmall"
                  style={{
                    fontSize: 11, padding: "2px 7px",
                    fontWeight: bbScope === s.key ? 600 : 400,
                    background: bbScope === s.key ? "var(--primary)" : "transparent",
                    color: bbScope === s.key ? "#fff" : "var(--muted)",
                    borderRadius: 4,
                  }}
                  onClick={() => setBbScope(s.key)}
                >
                  {s.label}
                </button>
              ))}
            </div>

            {bbEntries.length === 0 ? (
              <div style={{
                fontSize: 11, color: "var(--muted)", padding: "16px 10px",
                textAlign: "center", border: "1px dashed var(--line)", borderRadius: 8,
              }}>
                {bbLoading ? "加载中..." : "暂无记录"}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {bbEntries.map((entry: any) => {
                  const bbNodeName = (id: string) => {
                    if (!id) return "";
                    const nd = nodes.find(n => n.id === id);
                    return (nd?.data as any)?.role_title || id;
                  };
                  const scopeLabel = entry.scope === "org" ? "组织" : entry.scope === "department" ? entry.scope_owner : bbNodeName(entry.source_node) || "节点";
                  const typeColors: Record<string, string> = {
                    fact: "#3b82f6", decision: "#f59e0b", lesson: "#10b981",
                    progress: "#8b5cf6", todo: "#ef4444",
                  };
                  const typeLabels: Record<string, string> = {
                    fact: "事实", decision: "决策", lesson: "经验",
                    progress: "进展", todo: "待办",
                  };
                  return (
                    <div
                      key={entry.id}
                      style={{
                        border: "1px solid var(--line)", borderRadius: 6,
                        padding: "6px 8px", background: "var(--card-bg)",
                        fontSize: 11,
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3 }}>
                        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                          <span style={{
                            fontSize: 11, padding: "1px 5px", borderRadius: 3,
                            background: (typeColors[entry.memory_type] || "#6b7280") + "20",
                            color: typeColors[entry.memory_type] || "var(--muted)",
                            fontWeight: 600,
                          }}>
                            {typeLabels[entry.memory_type] || entry.memory_type}
                          </span>
                          <span style={{ fontSize: 11, color: "var(--muted)" }}>{scopeLabel}</span>
                        </div>
                        <button
                          className="btnSmall"
                          style={{ fontSize: 11, padding: "0 4px", color: "var(--muted)" }}
                          title="删除此条"
                          onClick={async () => {
                            try {
                              await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/memory/${entry.id}`, { method: "DELETE" });
                              setBbEntries((prev) => prev.filter((e: any) => e.id !== entry.id));
                            } catch { /* ignore */ }
                          }}
                        >
                          ×
                        </button>
                      </div>
                      <div className="bb-entry-content">
                        {mdModules ? (
                          <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>
                            {entry.content}
                          </mdModules.ReactMarkdown>
                        ) : (
                          <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{entry.content}</pre>
                        )}
                      </div>
                      {Array.isArray(entry.tags) && entry.tags.length > 0 && (
                        <div style={{ marginTop: 3, display: "flex", gap: 3, flexWrap: "wrap" }}>
                          {entry.tags.map((t: string) => (
                            <span key={t} style={{
                              fontSize: 11, padding: "0 5px", borderRadius: 3,
                              background: "var(--hover-bg, rgba(100,100,100,0.1))", color: "var(--muted)",
                            }}>#{t}</span>
                          ))}
                        </div>
                      )}
                      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
                        {entry.source_node && <span>来自 {bbNodeName(entry.source_node)} · </span>}
                        {fmtShortDate(entry.created_at)}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
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
        dialog={confirmReset ? { message: "确认重置该组织吗？将清空所有运行数据（黑板、消息、事件日志），恢复为初始状态。此操作不可撤销。", onConfirm: handleResetOrg } : null}
        onClose={() => setConfirmReset(false)}
      />
    </div>
  );
}
