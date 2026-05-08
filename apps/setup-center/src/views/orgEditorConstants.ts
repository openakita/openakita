/**
 * Shared constants, types, and utility functions for OrgEditorView and its sub-panels.
 * Extracted to eliminate duplication and ensure single-source-of-truth for labels/colors.
 *
 * Label maps now store i18n key paths — callers must resolve via t(key).
 */

import i18n from "../i18n";

// ── Time helpers (locale-aware) ──

function currentLocale(): string {
  const lang = i18n.language;
  return lang === "zh" ? "zh-CN" : "en-US";
}

export function fmtTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(currentLocale(), { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDateTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString(currentLocale(), { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtShortDate(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString(currentLocale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
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
// Values are i18n key paths — resolve via t(TASK_STATUS_LABELS[key]) in components.

export const TASK_STATUS_LABELS: Record<string, string> = {
  todo: "org.taskStatus.todo",
  in_progress: "org.taskStatus.inProgress",
  delivered: "org.taskStatus.delivered",
  rejected: "org.taskStatus.rejected",
  accepted: "org.taskStatus.accepted",
  cancelled: "org.taskStatus.cancelled",
  blocked: "org.taskStatus.blocked",
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
  node_status_change: "org.eventType.nodeStatusChange",
  llm_usage: "org.eventType.llmUsage",
  task_completed: "org.eventType.taskCompleted",
  task_assigned: "org.eventType.taskAssigned",
  task_delivered: "org.eventType.taskDelivered",
  task_accepted: "org.eventType.taskAccepted",
  task_rejected: "org.eventType.taskRejected",
  task_failed: "org.eventType.taskFailed",
  node_activated: "org.eventType.nodeActivated",
  node_deactivated: "org.eventType.nodeDeactivated",
  node_dismissed: "org.eventType.nodeDismissed",
  node_frozen: "org.eventType.nodeFrozen",
  node_unfrozen: "org.eventType.nodeUnfrozen",
  org_started: "org.eventType.orgStarted",
  org_stopped: "org.eventType.orgStopped",
  org_paused: "org.eventType.orgPaused",
  org_resumed: "org.eventType.orgResumed",
  org_reset: "org.eventType.orgReset",
  schedule_assigned: "org.eventType.scheduleAssigned",
  schedule_completed: "org.eventType.scheduleCompleted",
  schedule_triggered: "org.eventType.scheduleTriggered",
  schedule_requested: "org.eventType.scheduleRequested",
  broadcast: "org.eventType.broadcast",
  auto_clone_created: "org.eventType.autoCloneCreated",
  clones_reclaimed: "org.eventType.clonesReclaimed",
  auto_kickoff: "org.eventType.autoKickoff",
  scaling_requested: "org.eventType.scalingRequested",
  scaling_approved: "org.eventType.scalingApproved",
  scaling_rejected: "org.eventType.scalingRejected",
  tools_granted: "org.eventType.toolsGranted",
  tools_requested: "org.eventType.toolsRequested",
  tools_revoked: "org.eventType.toolsRevoked",
  user_command: "org.eventType.userCommand",
  watchdog_recovery: "org.eventType.watchdogRecovery",
  heartbeat_triggered: "org.eventType.heartbeatTriggered",
  heartbeat_decision: "org.eventType.heartbeatDecision",
  standup_started: "org.eventType.standupStarted",
  standup_completed: "org.eventType.standupCompleted",
  meeting_completed: "org.eventType.meetingCompleted",
  conflict_detected: "org.eventType.conflictDetected",
  policy_proposed: "org.eventType.policyProposed",
  approval_resolved: "org.eventType.approvalResolved",
  tool_call_start: "org.eventType.toolCallStart",
  tool_call_end: "org.eventType.toolCallEnd",
  plan_created: "org.eventType.planCreated",
  plan_completed: "org.eventType.planCompleted",
  plan_cancelled: "org.eventType.planCancelled",
  plan_step_updated: "org.eventType.planStepUpdated",
  iteration_start: "org.eventType.iterationStart",
  agent_handoff: "org.eventType.agentHandoff",
  ask_user: "org.eventType.askUser",
  done: "org.eventType.done",
  error: "org.eventType.error",
};

export const MSG_TYPE_LABELS: Record<string, string> = {
  task_assign: "org.msgType.taskAssign",
  task_result: "org.msgType.taskResult",
  task_delivered: "org.msgType.taskDelivered",
  task_accepted: "org.msgType.taskAccepted",
  task_rejected: "org.msgType.taskRejected",
  report: "org.msgType.report",
  question: "org.msgType.question",
  answer: "org.msgType.answer",
  escalate: "org.msgType.escalate",
  escalation: "org.msgType.escalation",
  broadcast: "org.msgType.broadcast",
  dept_broadcast: "org.msgType.deptBroadcast",
  feedback: "org.msgType.feedback",
  handshake: "org.msgType.handshake",
  deliverable: "org.msgType.deliverable",
};

export const DATA_KEY_LABELS: Record<string, string> = {
  from: "org.dataKey.from",
  to: "org.dataKey.to",
  reason: "org.dataKey.reason",
  node_id: "org.dataKey.nodeId",
  calls: "org.dataKey.calls",
  tokens_in: "org.dataKey.tokensIn",
  tokens_out: "org.dataKey.tokensOut",
  model: "org.dataKey.model",
  result_preview: "org.dataKey.resultPreview",
  deliverable_preview: "org.dataKey.deliverablePreview",
  error: "org.dataKey.error",
  content: "org.dataKey.content",
  task: "org.dataKey.task",
  title: "org.dataKey.title",
  role: "org.dataKey.role",
  name: "org.dataKey.name",
  tools: "org.dataKey.tools",
  source: "org.dataKey.source",
  target: "org.dataKey.target",
  scope: "org.dataKey.scope",
  prompt: "org.dataKey.prompt",
  schedule_id: "org.dataKey.scheduleId",
  chain_id: "org.dataKey.chainId",
  clone_id: "org.dataKey.cloneId",
  approval_id: "org.dataKey.approvalId",
  request_id: "org.dataKey.requestId",
  new_node_id: "org.dataKey.newNodeId",
  superior: "org.dataKey.superior",
  participants: "org.dataKey.participants",
  pending_count: "org.dataKey.pendingCount",
  node_count: "org.dataKey.nodeCount",
  rounds: "org.dataKey.rounds",
  cycle: "org.dataKey.cycle",
  decision: "org.dataKey.decision",
  stuck_secs: "org.dataKey.stuckSecs",
  threshold: "org.dataKey.threshold",
  dismissed: "org.dataKey.dismissed",
  type: "org.dataKey.type",
  topic: "org.dataKey.topic",
  filename: "org.dataKey.filename",
  core_business_len: "org.dataKey.coreBusinessLen",
  tool: "org.dataKey.tool",
  args: "org.dataKey.args",
  result: "org.dataKey.result",
  duration_ms: "org.dataKey.durationMs",
  status: "org.dataKey.status",
  question: "org.dataKey.question",
  message: "org.dataKey.message",
};

export const DATA_VALUE_LABELS: Record<string, string> = {
  idle: "org.dataValue.idle",
  busy: "org.dataValue.busy",
  waiting: "org.dataValue.waiting",
  error: "org.dataValue.error",
  offline: "org.dataValue.offline",
  frozen: "org.dataValue.frozen",
  task_started: "org.dataValue.taskStarted",
  task_completed: "org.dataValue.taskCompleted",
  task_failed: "org.dataValue.taskFailed",
  task_assigned: "org.dataValue.taskAssigned",
  task_delivered: "org.dataValue.taskDelivered",
  task_accepted: "org.dataValue.taskAccepted",
  task_rejected: "org.dataValue.taskRejected",
  org_stopped: "org.dataValue.orgStopped",
  org_reset: "org.dataValue.orgReset",
  org_paused: "org.dataValue.orgPaused",
  org_resumed: "org.dataValue.orgResumed",
  restart_cleanup: "org.dataValue.restartCleanup",
  watchdog_recovery: "org.dataValue.watchdogRecovery",
  health_check_recovery: "org.dataValue.healthCheckRecovery",
  org_quota_pause: "org.dataValue.orgQuotaPause",
  quota_exhausted: "org.dataValue.quotaExhausted",
  auto_recover_before_activate: "org.dataValue.autoRecoverBeforeActivate",
  unfreeze: "org.dataValue.unfreeze",
  stuck_busy: "org.dataValue.stuckBusy",
  error_not_recovering: "org.dataValue.errorNotRecovering",
  idle_no_progress: "org.dataValue.idleNoProgress",
  root_busy: "org.dataValue.rootBusy",
  root_has_task: "org.dataValue.rootHasTask",
  skip: "org.dataValue.skip",
  activate: "org.dataValue.activate",
  do_nothing: "org.dataValue.doNothing",
  pending: "org.dataValue.pending",
  approved: "org.dataValue.approved",
  rejected: "org.dataValue.rejected",
  completed: "org.dataValue.completed",
  in_progress: "org.dataValue.inProgress",
  delivered: "org.dataValue.delivered",
  accepted: "org.dataValue.accepted",
  blocked: "org.dataValue.blocked",
  healthy: "org.dataValue.healthy",
  warning: "org.dataValue.warning",
  critical: "org.dataValue.critical",
  attention: "org.dataValue.attention",
};

export const STATUS_LABELS: Record<string, string> = {
  idle: "org.status.idle",
  busy: "org.status.busy",
  waiting: "org.status.waiting",
  error: "org.status.error",
  offline: "org.status.offline",
  frozen: "org.status.frozen",
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
  dormant: "org.orgStatus.dormant",
  active: "org.orgStatus.active",
  running: "org.orgStatus.running",
  paused: "org.orgStatus.paused",
  archived: "org.orgStatus.archived",
};

export const EDGE_COLORS: Record<string, string> = {
  hierarchy: "var(--primary)",
  collaborate: "var(--ok)",
  escalate: "var(--danger)",
  consult: "#a78bfa",
};

const DEPT_KEY_MAP: Record<string, string> = {
  "管理层": "org.dept.management",
  "技术部": "org.dept.techDept",
  "产品部": "org.dept.productDept",
  "市场部": "org.dept.marketDept",
  "行政支持": "org.dept.adminSupport",
  "工程": "org.dept.engineering",
  "前端组": "org.dept.frontendTeam",
  "后端组": "org.dept.backendTeam",
  "编辑部": "org.dept.editorialDept",
  "创作组": "org.dept.creativeTeam",
  "运营组": "org.dept.opsTeam",
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

/**
 * Translate a department name from backend (Chinese key) to localised label.
 * Falls back to the raw string when no mapping exists.
 */
export function translateDept(dept: string, t: (k: string) => string): string {
  const key = DEPT_KEY_MAP[dept];
  return key ? t(key) : dept;
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

/** Unified blackboard entry type labels (i18n keys). */
export const BB_TYPE_LABELS: Record<string, string> = {
  fact: "org.bbType.fact",
  decision: "org.bbType.decision",
  lesson: "org.bbType.lesson",
  progress: "org.bbType.progress",
  todo: "org.bbType.todo",
  resource: "org.bbType.resource",
};

export function translateDataValue(
  key: string, value: unknown,
  nodeNameMap?: Map<string, string>,
): string {
  const s = String(value);
  if ((key === "node_id" || key === "new_node_id") && nodeNameMap?.has(s)) {
    return nodeNameMap.get(s)!;
  }
  const i18nKey = DATA_VALUE_LABELS[s];
  return i18nKey ? i18n.t(i18nKey) : s;
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
  endpoint_policy?: "prefer" | "require";
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
  layout_locked?: boolean;
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
