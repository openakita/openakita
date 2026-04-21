/**
 * Shared constants, types, and utility functions for OrgEditorView and its sub-panels.
 * Extracted to eliminate duplication and ensure single-source-of-truth for labels/colors.
 */

// ── Time helpers (always show local timezone) ──

export function fmtTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDateTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtShortDate(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function stripMd(s: string): string {
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

// ── Label & color maps ──

export const TASK_STATUS_LABELS: Record<string, string> = {
  todo: "待办",
  in_progress: "进行中",
  delivered: "已交付",
  rejected: "已打回",
  accepted: "已验收",
  cancelled: "已取消",
  blocked: "已阻塞",
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
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

export const MSG_TYPE_LABELS: Record<string, string> = {
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

export const DATA_KEY_LABELS: Record<string, string> = {
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

export const DATA_VALUE_LABELS: Record<string, string> = {
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

export const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  busy: "执行中",
  waiting: "等待中",
  error: "异常",
  offline: "离线",
  frozen: "已冻结",
};

export const STATUS_COLORS: Record<string, string> = {
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

export const ORG_STATUS_LABELS: Record<string, string> = {
  dormant: "休眠",
  active: "运行中",
  running: "运行中",
  paused: "已暂停",
  archived: "已归档",
};

export const EDGE_COLORS: Record<string, string> = {
  hierarchy: "var(--primary)",
  collaborate: "var(--ok)",
  escalate: "var(--danger)",
  consult: "#a78bfa",
};

export const DEPT_COLORS: Record<string, string> = {
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

export function getDeptColor(dept: string): string {
  return DEPT_COLORS[dept] || "#6b7280";
}

/** Unified blackboard entry type colors — single source of truth. */
export const BB_TYPE_COLORS: Record<string, string> = {
  fact: "#3b82f6",
  decision: "#f59e0b",
  lesson: "#10b981",
  progress: "#8b5cf6",
  todo: "#ef4444",
  resource: "#0891b2",
};

/** Unified blackboard entry type labels — single source of truth. */
export const BB_TYPE_LABELS: Record<string, string> = {
  fact: "事实",
  decision: "决策",
  lesson: "经验",
  progress: "进展",
  todo: "待办",
  resource: "产出",
};

export function translateDataValue(
  key: string, value: unknown,
  nodeNameMap?: Map<string, string>,
): string {
  const s = String(value);
  if ((key === "node_id" || key === "new_node_id") && nodeNameMap?.has(s)) {
    return nodeNameMap.get(s)!;
  }
  return DATA_VALUE_LABELS[s] || s;
}

// ── Types ──

export interface OrgNodeData {
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
  enable_file_tools?: boolean;
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

export interface OrgEdgeData {
  id: string;
  source: string;
  target: string;
  edge_type: string;
  label: string;
  bidirectional: boolean;
  priority: number;
  bandwidth_limit: number;
}

export interface OrgSummary {
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

export interface UserPersona {
  title: string;
  display_name: string;
  description: string;
}

export interface OrgFull {
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

export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  icon: string;
  node_count: number;
  tags: string[];
}
