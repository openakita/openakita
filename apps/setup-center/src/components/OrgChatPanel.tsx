/**
 * Reusable chat panel — organization or node level.
 * Renders a scrollable message list, input box, and real-time WS progress.
 * Messages are persisted to backend session API (same as main ChatView).
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, ShieldAlert, Copy as IconCopy } from "lucide-react";
import { toast } from "sonner";
import { safeFetch } from "../providers";
import { copyToClipboard } from "../utils/clipboard";
import { onWsEvent } from "../platform";
import { useMdModules } from "../views/chat/hooks/useMdModules";
import { createV2Stream, type V2StreamEvent } from "../api/v2Stream";
import { ProgressLedgerTimeline, type ProgressLedgerEvent } from "./ProgressLedgerTimeline";
import { FileAttachmentCard } from "./FileAttachmentCard";
import type { FileAttachment } from "./FileAttachmentCard";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

interface ChatMsg {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  streaming?: boolean;
  attachments?: FileAttachment[];
  /**
   * 用户从指挥台 composer 上传的输入附件（上游 e2874585 移植）。区别于
   * ``attachments``（编排输出交付物），这些渲染在 role="user" 的气泡里。
   */
  inputAttachments?: FileAttachment[];
  /**
   * P11: 内容种类的细粒度标记，用于让样式（bubble 颜色 / class 名）跟"语义"
   * 解耦。例如 role="system" 同时被用于真正的错误通知（红色合理）和
   * IM/桌面/指挥台事件流（应当中性、不要红）。kind="activity" 即一组组织
   * 事件聚合后渲染出的活动时间线 bubble，CSS 用 `.ocp-msg-activity` 给中性
   * 颜色覆盖。
   */
  kind?: "activity" | "final_report";
  /**
   * test17: the org command this message belongs to. Lets the command center
   * group a multi-run history into per-command blocks (用户指令 → 编排过程 →
   * 根节点总结) and gives the final-report bubble a stable, dedupable identity
   * across the live / reload / always-on paths.
   */
  commandId?: string;
}

/** 指挥台 composer 待发送的输入附件（上传中/已上传/失败）。 */
interface PendingInputFile {
  _uploadId: string;
  name: string;
  size?: number;
  mimeType?: string;
  type: string;
  url?: string;
  localPath?: string;
  uploadId?: string;
  uploadStatus: "uploading" | "uploaded" | "failed";
}

/** 后端 failure_diagnoser 生成的结构化诊断 payload */
interface FailureDiagnosis {
  root_cause?: string;
  headline?: string;
  evidence?: Array<{
    iter?: number;
    tool?: string;
    args_summary?: string;
    error?: string;
  }>;
  suggestion?: string;
  exit_reason?: string;
}

interface TimelineSegment {
  nodeId: string;
  nodeName: string;
  lines: string[];
  files: FileAttachment[];
  done: boolean;
  /** 上一次 push line 的时间戳（毫秒）；用于抑制 1s 内同行重复 */
  lastPushAt?: number;
  /** segment 标记为 done 的时间戳（毫秒），用于 30s 内的 busy 复用 */
  doneAt?: number;
  /** 已加入 files 的 file_path 集合，按 path 去重 */
  filePaths?: Set<string>;
  resultPreview?: string;
  /**
   * 节点退出原因：
   * - undefined/"normal"/"ask_user"/"waiting_user": 正常完成或等待用户补充
   * - "loop_terminated": Supervisor 强制终止死循环
   * - "max_iterations": 达到最大迭代次数
   * - "verify_incomplete": 内部验证未匹配，普通用户界面不展示为失败
   */
  exitReason?: string;
  /** 是否非正常结束，用于 UI 明确区分"完成" vs "终止/失败" */
  failed?: boolean;
  /** 后端 failure_diagnoser 生成的结构化诊断 */
  diagnosis?: FailureDiagnosis;
  /**
   * P10: 节点退出后处于"需要用户/上级补充输入"的挂起态。
   * - "waiting_user": 节点 escalate 给用户、等待回复。UI 必须显眼提示用户回复，
   *   否则用户会误以为系统卡死、自己点取消导致 producer 链路被 soft_stop。
   */
  paused?: "waiting_user";
}

export interface OrgChatPanelProps {
  orgId: string;
  nodeId?: string | null;
  apiBaseUrl: string;
  compact?: boolean;
  showHeader?: boolean;
  title?: string;
  onClose?: () => void;
  /** Map node IDs to display names so progress lines show readable names. */
  nodeNames?: Record<string, string>;
  /**
   * Which supervisor runtime drives this org. ``"v1"`` keeps the
   * legacy WS + ``onWsEvent`` + ``/api/v2/orgs/.../activity`` path
   * (every existing call site). ``"v2"`` ALSO subscribes to the
   * SSE feed at ``/api/v2/orgs/{id}/stream`` and renders a
   * :class:`ProgressLedgerTimeline` above the chat list. The v1
   * path is left untouched in either mode so a v2-bound org keeps
   * working with the IM-side aggregation while it migrates.
   */
  runtime?: "v1" | "v2";
}

/**
 * 一条可被指挥台用作"完成 / 取消"转发目标的 IM 频道。
 * 与后端 ``ForwardTarget`` dataclass 一一对应（channel + chat_id 是最小可
 * 寻址单位；thread_id 仅 Telegram topic / Lark thread 用得到）。
 */
interface ForwardTargetOption {
  id: string;            // 渲染用稳定 key
  label: string;         // UI 标签（bot 名称 / chat 名称）
  channel: string;       // gateway 适配器 key
  chat_id: string;
  thread_id?: string | null;
  bot_instance_id?: string;
}

function sessionId(orgId: string, nodeId?: string | null): string {
  return nodeId ? `org_${orgId}_node_${nodeId}` : `org_${orgId}`;
}

let _seq = 0;
function genId() { return `orgchat-${Date.now()}-${++_seq}`; }

const LS_PREFIX = "orgchat_msgs_";
const ORG_HISTORY_PAGE_LIMIT = 80;
const ORG_STORED_MESSAGE_WINDOW = 120;

// Survives component unmount so command results aren't lost when navigating away
interface PendingCmd {
  commandId: string;
  orgId: string;
  placeholderId: string;
  lastRendered: string;
  segmentCount: number;
  allFiles: FileAttachment[];
  finalContent: string | null;
  /**
   * test17: id of the right-side user bubble that kicked off this command, so
   * the command_id can be stamped onto it once the POST returns (enables
   * per-command history grouping).
   */
  userMsgId?: string;
  /**
   * test17: once the terminal report bubble is built we must stop the live
   * progress handler from rewriting the (now retired) streaming placeholder --
   * a late ``final_report_pdf`` / ``node_status idle`` event used to overwrite
   * the finalized bubble back to "组织正在处理中…", making the report vanish.
   */
  finalized?: boolean;
}
const _pendingCmds = new Map<string, PendingCmd>();

const SOFT_ORG_EXIT_REASONS = new Set(["normal", "ask_user", "waiting_user", "verify_incomplete"]);

function isSoftOrgExitReason(reason?: string): boolean {
  return !reason || SOFT_ORG_EXIT_REASONS.has(reason);
}

// ─────────────────────────────────────────────────────────────────────────────
// P11: 组织活动时间线（/api/v2/orgs/{org}/activity）的中性渲染器。
//
// 之前的行为是把每个 activity item（user_command / task_assigned /
// workbench_started / workbench_succeeded / task_completed …）映射成一条
// 独立的 role="system" 消息——而 `.ocp-msg-system` 用了红色样式（语义上
// 是错误通知），导致整个 IM 转发流量看起来全是"红色错误条"，并且 raw
// event_type 直接打到标题上（[workbench_started] / [task_completed]），
// 视觉非常吵闹、信息密度极低。
//
// 这里把同一条 user_command（command_id 相同）下产生的所有事件聚合到一条
// "活动 bubble" 里，bubble 内部按时间顺序逐行渲染图标 + 行为简述，整体
// 仍是 markdown 内容（保留 details/collapsed 等能力），而 bubble 本身用
// kind="activity" 标记，CSS 走中性色而不是红色。
// ─────────────────────────────────────────────────────────────────────────────

interface ActivityItem {
  id?: string;
  ts?: string | number;
  at?: string | number;
  kind?: string;
  source?: { surface?: string; channel?: string; display_name?: string };
  from_node?: string;
  to_node?: string;
  content?: string;
  command_id?: string;
  chain_id?: string;
  event_type?: string;
  msg_type?: string;
  status?: string;
  phase?: string;
  // A2 fix: the v2 ``/activity`` endpoint is a thin envelope that returns
  // raw event-store records, whose field names differ from the legacy v1
  // activity shape above. These are the real fields the supervisor /
  // executor emit (see ``_runtime_agent_pipeline_executor._emit``).
  type?: string;
  node_id?: string;
  parent_node_id?: string;
  child_node_id?: string;
  content_preview?: string;
  output_len?: number;
  artifact_path?: string;
  // 上游 e2874585: user_command 事件携带的用户输入附件元数据。
  input_attachments?: Array<Record<string, unknown>>;
  // 核心1/核心2: parent-review verdict reason (退回/上报 原因).
  reason?: string;
}

/** Map a persisted user_command attachment descriptor to a FileAttachment. */
function toInputFileAttachments(raw: unknown): FileAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: FileAttachment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const a = item as Record<string, unknown>;
    const name = String(a.name || a.filename || "").trim();
    if (!name) continue;
    out.push({
      filename: name,
      file_path: String(a.local_path || a.localPath || a.url || ""),
      file_size: typeof a.size === "number" ? a.size : undefined,
    });
  }
  return out;
}

// A2 fix: map the raw event-store ``type`` onto the canonical ``kind``
// vocabulary ``formatActivityLine`` understands. Without this every
// ``agent_run_*`` / ``subtask_assigned`` record fell through to the
// default branch and rendered as a contentless "· ?" line (图1).
const ACTIVITY_TYPE_TO_KIND: Record<string, string> = {
  subtask_assigned: "delegate",
  child_dispatch: "delegate",
  agent_run_started: "node_activated",
  agent_run_finished: "task_completed",
  agent_run_failed: "task_failed",
  agent_run_cancelled: "task_cancelled",
  node_tool_called: "workbench_started",
  node_tool_completed: "workbench_succeeded",
  node_tool_failed: "workbench_failed",
  command_phase: "command_phase",
  user_command: "user_command",
  // 核心1/核心2: 逐级校验 + 重做闭环 events (parent reviews a report; on
  // 退回 the report is re-dispatched; on exhaustion it escalates).
  node_review_passed: "review_passed",
  node_rework_requested: "rework_requested",
  node_review_escalated: "review_escalated",
};

/** Resolve the canonical kind + normalized from/to/content for one item. */
function normalizeActivity(item: ActivityItem): {
  kind: string;
  from: string;
  to: string;
  content: string;
  outputLen?: number;
} {
  const rawType = item.type || item.event_type || "";
  const kind = item.kind && ACTIVITY_TYPE_TO_KIND[item.kind]
    ? ACTIVITY_TYPE_TO_KIND[item.kind]
    : (ACTIVITY_TYPE_TO_KIND[rawType] || item.kind || rawType || "");
  // delegate-style events: parent → child. activation/completion: the
  // acting node sits on ``node_id``.
  const from = item.from_node || item.parent_node_id || item.node_id || "";
  const to = item.to_node || item.child_node_id || "";
  const content = (item.content || item.content_preview || "").trim();
  return { kind, from, to, content, outputLen: item.output_len };
}

function activityTs(item: ActivityItem): number {
  const raw = item?.ts ?? item?.at;
  if (typeof raw === "number") return raw < 1e12 ? raw * 1000 : raw;
  if (typeof raw === "string" && raw) {
    const t = Date.parse(raw);
    if (!Number.isNaN(t)) return t;
    const n = Number(raw);
    if (!Number.isNaN(n)) return n < 1e12 ? n * 1000 : n;
  }
  return Date.now();
}

/** 单条活动事件渲染成一行（不含时间戳前缀；时间戳由 group 渲染器统一加）。 */
function formatActivityLine(item: ActivityItem, opts?: { nameFmt?: (id: string) => string }): string {
  const nameFmt = opts?.nameFmt || ((id: string) => id);
  // A2 fix: resolve the canonical kind + from/to/content across BOTH the
  // legacy v1 activity shape and the raw v2 event-store record shape.
  const norm = normalizeActivity(item);
  const fromN = norm.from ? nameFmt(norm.from) : "";
  const toN = norm.to ? nameFmt(norm.to) : "";
  const flowArrow = toN ? `${fromN || "?"} → ${toN}` : fromN;
  const inlineContent = norm.content ? norm.content.replace(/\s+/g, " ").slice(0, 200) : "";
  const summary = inlineContent ? `：${inlineContent}` : "";
  const out = norm.outputLen ? `（输出 ${norm.outputLen} 字）` : "";
  switch (norm.kind) {
    case "user_command":
      return `🎯 **用户指令**${summary}`;
    case "user_command_cancelled":
      return `⏹ 用户取消指令`;
    case "command":
      return `📡 命令登记：${item.status || ""}${item.phase ? `·${item.phase}` : ""}`;
    case "command_phase":
      return `📡 ${flowArrow || "命令"} 状态变更${summary}`;
    case "delegate":
      return `↪ ${flowArrow || "派单"} 派单${summary}`;
    case "task_completed":
      return `✓ ${fromN || "节点"} 任务完成${out}${summary}`;
    case "task_failed":
      return `✗ ${fromN || "节点"} 任务失败${summary}`;
    case "task_cancelled":
      return `⏹ ${fromN || "节点"} 任务取消${summary}`;
    case "broadcast":
      return `📢 ${fromN || "?"} 广播${summary}`;
    case "node_activated":
      return `🟢 ${fromN || "节点"} 开始执行${summary}`;
    case "workbench_started":
      return `▶ ${fromN || "节点"} 调用工具${summary}`;
    case "workbench_succeeded":
      return `✓ ${fromN || "节点"} 工具完成${summary}`;
    case "workbench_failed":
      return `✗ ${fromN || "节点"} 工具失败${summary}`;
    case "review_passed":
      return `✅ ${fromN || "上级"} 审阅通过下级 ${toN || ""} 的产出${summary}`;
    case "rework_requested": {
      const reason = (item.reason || norm.content || "").toString().replace(/\s+/g, " ").slice(0, 200);
      const r = reason ? `：${reason}` : "";
      return `🔁 ${fromN || "上级"} 退回 ${toN || "下级"} 重做${r}`;
    }
    case "review_escalated": {
      const reason = (item.reason || norm.content || "").toString().replace(/\s+/g, " ").slice(0, 200);
      const r = reason ? `：${reason}` : "";
      return `⚠ ${toN || "下级"} 多次重做仍未达标，已上报上级决策${r}`;
    }
    case "message":
      return `💬 ${flowArrow}${summary}`;
    default:
      // A2 fix: skip purely-structural events with nothing readable
      // (no flow + no content) instead of emitting a "· ?" ghost line.
      if (!inlineContent && !flowArrow) return "";
      return `· ${flowArrow}${norm.kind ? `（${norm.kind}）` : ""}${summary}`;
  }
}

/** 按 command_id（缺失时退化到 chain_id / "ungrouped"）聚合到 bubble。 */
function activityItemsToMessages(
  items: ActivityItem[],
  nameFmt?: (id: string) => string,
): ChatMsg[] {
  if (!items || items.length === 0) return [];
  // 1. 按 command_id 分组
  const groups = new Map<string, ActivityItem[]>();
  const groupOrder: string[] = [];
  for (const it of items) {
    const key = (it.command_id && String(it.command_id))
      || (it.chain_id && `chain:${it.chain_id}`)
      || `solo:${it.id || it.ts || it.kind || "anon"}`;
    let bucket = groups.get(key);
    if (!bucket) {
      bucket = [];
      groups.set(key, bucket);
      groupOrder.push(key);
    }
    bucket.push(it);
  }
  // 2. 每组组内按 ts asc
  const msgs: ChatMsg[] = [];
  for (const key of groupOrder) {
    const bucket = (groups.get(key) || []).slice();
    bucket.sort((a, b) => activityTs(a) - activityTs(b));
    if (bucket.length === 0) continue;
    const first = bucket[0];
    const groupTs = activityTs(first);
    // command_id 在很多事件上是同一个值；以第一条带 user_command 的为锚显示
    const cmdItem = bucket.find(i => normalizeActivity(i).kind === "user_command") || first;
    // UI issue #1: 把"用户指令"还原成一条 role="user" 的右侧气泡，而不是
    // 折进系统活动卡里。这样切组织/切节点/重挂载后从 /activity 重新加载
    // 时，用户自己发出的指令气泡依然在（之前只剩节点回复）。id 用
    // command_id 做稳定键，保证去重幂等、不会和本地乐观气泡叠加成两条。
    const cmdSummaryFull = (cmdItem.content || cmdItem.content_preview || "").trim();
    if (normalizeActivity(cmdItem).kind === "user_command" && cmdSummaryFull) {
      const cmdKey = cmdItem.command_id ? String(cmdItem.command_id) : (key || `${groupTs}`);
      const reloadedInputAtts = toInputFileAttachments(cmdItem.input_attachments);
      msgs.push({
        id: `user-cmd-${cmdKey}`,
        role: "user",
        content: cmdSummaryFull,
        timestamp: activityTs(cmdItem),
        inputAttachments: reloadedInputAtts.length > 0 ? reloadedInputAtts : undefined,
        // test17 Task3: stamp the owning command so the reloaded history can be
        // regrouped into per-command blocks (用户指令 → 编排过程 → 总结) after a
        // refresh, matching the live path.
        commandId: cmdItem.command_id ? String(cmdItem.command_id) : undefined,
      });
    }
    // 图1 fix: the reconstructed flat "🗂 编排过程" system bubble used to be
    // pushed here as a chat message — which is the UPPER duplicate the user
    // asked to delete (a flat "主编 任务完成（输出 N 字）" list living above the
    // detailed live timeline). We no longer emit it. Instead the SAME activity
    // is folded into the SINGLE bottom timeline via :func:`activityItemsToLedger`
    // (seeded into ``v2LedgerEvents`` on load), so the command center shows ONE
    // 编排过程 block. Only the user-command right-side bubble (pushed above) is
    // reconstructed as a chat message here.
    void groupTs;
  }
  return msgs;
}

/**
 * 图1 fix: convert raw /activity items into the SAME
 * :class:`ProgressLedgerEvent` shape the live bottom timeline consumes, so
 * reloaded history and live SSE render as ONE unified 编排过程 timeline
 * instead of a flat duplicate bubble + the live feed. Each item becomes one
 * ledger entry whose ``next_speaker`` is the acting node and whose
 * ``instruction_or_question`` is the human Chinese line (with 谁派给谁 + 内容
 *摘要, reusing :func:`formatActivityLine`). Ids are stable (``act:<id>``) so a
 * later merge dedups idempotently.
 */
// 图3: map an activity ``kind`` to the node lifecycle phase the timeline uses
// to converge a node's status across rounds. Coordination/command-level kinds
// have no phase (and aren't node-grouped) so they don't latch a node terminal.
const ACTIVITY_KIND_TO_PHASE: Record<
  string,
  "start" | "active" | "done" | "incomplete" | "failed"
> = {
  node_activated: "start",
  workbench_started: "active",
  workbench_succeeded: "active",
  workbench_failed: "active",
  broadcast: "active",
  task_completed: "done",
  task_failed: "failed",
  task_cancelled: "failed",
  // The final PDF render is the root node's closing deliverable — treat it as a
  // terminal "done" for that node so it folds into the node's segment instead
  // of spawning a stray "进行中" row (图3).
  final_report_pdf: "done",
  // NOTE: 核心2 rework reopening is driven by the child's own re-issued
  // ``agent_run_started`` (node_activated→start), so ``rework_requested`` is a
  // pure trace line here (no phase) to avoid mis-latching the PARENT segment
  // (its ``from`` is the parent, not the reworking child).
};

// Kinds whose ``from`` is genuinely an acting node id we can group by. Command
// registration / phase rows are command-level (no single owning node) and stay
// on the legacy consecutive grouping.
const NODE_SCOPED_KINDS = new Set([
  "node_activated",
  "workbench_started",
  "workbench_succeeded",
  "workbench_failed",
  "broadcast",
  "task_completed",
  "task_failed",
  "task_cancelled",
  "delegate",
  "final_report_pdf",
]);

function activityItemsToLedger(
  items: ActivityItem[],
  nameFmt?: (id: string) => string,
): ProgressLedgerEvent[] {
  if (!items || items.length === 0) return [];
  const sorted = [...items].sort((a, b) => activityTs(a) - activityTs(b));
  const out: ProgressLedgerEvent[] = [];
  for (const it of sorted) {
    const norm = normalizeActivity(it);
    if (norm.kind === "user_command") continue; // shown as right-side bubble
    const line = formatActivityLine(it, { nameFmt });
    if (!line.trim()) continue;
    // review/rework events carry parent_node_id (reviewer) +
    // node_id/child_node_id (the reviewed CHILD). normalizeActivity puts the
    // PARENT in ``from`` and the CHILD in ``to``.
    // test16 审阅归属修复: 审核这个动作由【上级】(reviewer) 执行，因此
    // ``review_passed`` 必须归属到 ``from``(上级)——与 LIVE 路径保持一致。每条
    // 审核行都带不同的被审下级名("审阅通过下级X")，语义清晰、不是重复行。
    // 而【退回重做】/【上报升级】要重新点亮或聚焦【被审下级】自身，仍归属到
    // ``to``(child)。
    const isReviewKind =
      norm.kind === "review_passed" ||
      norm.kind === "rework_requested" ||
      norm.kind === "review_escalated";
    const node =
      norm.kind === "review_passed"
        ? norm.from || norm.to || ""
        : isReviewKind
          ? norm.to || norm.from || ""
          : norm.from || norm.to || "";
    const satisfied = norm.kind === "task_completed";
    // 图3: attribute the entry to its acting node + lifecycle phase so the
    // reload/rebuild timeline groups all of a node's rounds into ONE
    // converging segment (matching the live SSE path), instead of leaving the
    // same node split across many "进行中" rows.
    const nodeId =
      NODE_SCOPED_KINDS.has(norm.kind) || isReviewKind ? (node || undefined) : undefined;
    let phase = ACTIVITY_KIND_TO_PHASE[norm.kind];
    // 质量门禁: a finished run flagged incomplete is NOT a delivery — converge
    // it to "未通过校验" on reload, matching the live path.
    if (phase === "done" && (it as { incomplete?: boolean }).incomplete) {
      phase = "incomplete";
    }
    out.push({
      id: `act:${it.id || `${activityTs(it)}:${norm.kind}:${node}`}`,
      ts: String(activityTs(it) || ""),
      is_request_satisfied: satisfied,
      is_in_loop: false,
      is_progress_being_made: norm.kind !== "task_failed",
      next_speaker: node,
      instruction_or_question: line,
      nodeId,
      phase,
      // Item 3: stamp the owning command so the timeline can scope the rebuilt
      // /activity history to the CURRENT command and not cross-render stale
      // node segments from this org's earlier commands.
      commandId: it.command_id ? String(it.command_id) : undefined,
    });
  }
  return out;
}

function saveToLocalStorage(cid: string, msgs: ChatMsg[]): void {
  try {
    const windowed = msgs.length > ORG_STORED_MESSAGE_WINDOW
      ? msgs.slice(-ORG_STORED_MESSAGE_WINDOW)
      : msgs;
    const slim = windowed
      .filter(m => !m.streaming)
      .map(({ id, role, content, timestamp, attachments, kind }) => {
        const o: Record<string, unknown> = { id, role, content, timestamp };
        if (attachments && attachments.length > 0) o.attachments = attachments;
        if (kind) o.kind = kind;
        return o;
      });
    localStorage.setItem(LS_PREFIX + cid, JSON.stringify(slim));
  } catch { /* quota exceeded */ }
}

function loadFromLocalStorage(cid: string): ChatMsg[] {
  try {
    const raw = localStorage.getItem(LS_PREFIX + cid);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(-ORG_STORED_MESSAGE_WINDOW) : [];
  } catch { return []; }
}

// Strip any <thinking>…</thinking> chain-of-thought the node leaked into its
// final answer. UI issue #3/#4: the v2 executor returns the node's raw reply as
// the deliverable, and some nodes prepend a long <thinking> block — that is
// internal reasoning, not part of the "任务完成汇报", so it must never show in
// the final receipt bubble.
function stripThinking(text: string): string {
  if (!text) return text;
  const cleaned = text.replace(/<thinking>[\s\S]*?<\/thinking>/gi, "").trim();
  // If a node opened <thinking> but never closed it (truncated), drop the tag
  // and everything up to the first markdown heading so we still show content.
  if (/<thinking>/i.test(cleaned)) {
    const afterHeading = cleaned.replace(/^[\s\S]*?(?=^#{1,6}\s)/m, "");
    return (afterHeading || cleaned).replace(/<\/?thinking>/gi, "").trim();
  }
  return cleaned;
}

// UI issue #4: the v2 command result is shaped {final_message: "..."} (the
// supervisor's closing summary), NOT {result: "..."}. Reading only ``result``
// meant the final report fell through to a JSON.stringify(data) fallback and
// the user never saw a clean receipt. Probe the real key plus sensible
// fallbacks, then strip any leaked <thinking> block.
function extractCommandResultText(
  result: Record<string, unknown> | null | undefined,
): string | null {
  if (!result || typeof result !== "object") return null;
  for (const key of ["final_message", "result", "summary", "content", "message", "answer"]) {
    const v = (result as Record<string, unknown>)[key];
    if (typeof v === "string" && v.trim()) return stripThinking(v);
  }
  return null;
}

// test15: the file extensions we treat as downloadable deliverables. Kept in
// sync with the reload path's ``DELIVERABLE_RE`` so the live and reload receipts
// attach the same cards.
const _DELIVERABLE_RE =
  /\.(md|markdown|txt|pdf|png|jpe?g|gif|webp|svg|mp4|mov|webm|csv|json|html?|docx?|pptx?|xlsx?|zip)$/i;

// test15: reconstruct a command's registered deliverables (md/pdf/media) from
// the org event store, filtered to a single command_id. Used by the always-on
// final-report listener so a LIVE completion (of a command this panel did NOT
// dispatch) still shows downloadable cards, exactly like the reload path.
async function fetchCommandDeliverables(
  apiBaseUrl: string,
  orgId: string,
  cid: string,
): Promise<FileAttachment[]> {
  try {
    const r = await safeFetch(
      `${apiBaseUrl}/api/v2/orgs/${encodeURIComponent(orgId)}/events?limit=800`,
    );
    const j = await r.json();
    const evs = Array.isArray(j) ? j : Array.isArray(j?.events) ? j.events : [];
    const seen = new Set<string>();
    const out: FileAttachment[] = [];
    for (const e of evs) {
      const etype = (e?.type || e?.event_type || "") as string;
      const isFileOut = etype === "file_output_registered";
      if (etype !== "agent_run_finished" && etype !== "final_report_pdf" && !isFileOut) continue;
      if (e?.incomplete) continue; // 质量门禁: 未通过的不作为交付物
      if (String(e?.command_id || "") !== cid) continue;
      const apath = String((isFileOut ? e?.path : e?.artifact_path) || "");
      if (!apath || !_DELIVERABLE_RE.test(apath) || seen.has(apath)) continue;
      seen.add(apath);
      const fname = apath.replace(/\\/g, "/").split("/").pop() || "deliverable";
      const size = Number(isFileOut ? e?.size_bytes : e?.output_len) || undefined;
      out.push({ filename: fname, file_path: apath, file_size: size });
    }
    return out;
  } catch {
    return [];
  }
}

export function OrgChatPanel({ orgId, nodeId, apiBaseUrl, compact, showHeader, title, onClose, nodeNames, runtime }: OrgChatPanelProps) {
  const { t } = useTranslation();
  const md = useMdModules();
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  // 上游 e2874585: 指挥台 composer 待发送的输入附件（上传后随命令提交）。
  const [pendingFiles, setPendingFiles] = useState<PendingInputFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [canContinuePrevious, setCanContinuePrevious] = useState(false);
  // 当前在跑命令的 command_id；用于"强制终止"按键判断启用状态。
  // - 在 send_command 拿到 commandId 后置位
  // - finalizeResult / send 异常 / 不可恢复重连完成时清空
  // 与 _pendingCmds 解耦的目的：组件内的 React state 才能驱动按键 enable/disable。
  const [pendingCmdId, setPendingCmdId] = useState<string | null>(null);
  const [stopDialogOpen, setStopDialogOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  // Sprint-9: per-org single-root 409 conflict dialog. When a submit
  // races with an already-running command on the same root, the
  // backend returns 409 with ``code=org_command_conflict`` +
  // ``command_id`` of the in-flight command. The dialog lets the
  // user choose one of three branches:
  //   - replace_existing -> cancel current + start new
  //   - continue_previous -> resume from the previous command's
  //                          final checkpoint (falls back to content
  //                          concatenation when no checkpoint exists)
  //   - cancel -> abandon this submit, the running command keeps
  //               going
  const [conflictDialog, setConflictDialog] = useState<{
    pendingText: string;
    existingCommandId: string;
    message: string;
  } | null>(null);
  const [resolvingConflict, setResolvingConflict] = useState(false);
  // P3：可选的 IM 转发目标。当用户选中一个或多个 bot/聊天，命令完成 / 取消时
  // 后端会顺手把最终消息投递到这些 IM 频道——指挥台因此成为"统一入口/出口"。
  // 列表来自 ``GET /api/agents/bots``；每项形如 ``{channel, chat_id, label}``。
  const [availableForwards, setAvailableForwards] = useState<ForwardTargetOption[]>([]);
  const [forwardTargets, setForwardTargets] = useState<ForwardTargetOption[]>([]);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const mountedRef = useRef(true);
  const nodeNamesRef = useRef(nodeNames);
  nodeNamesRef.current = nodeNames;
  const convId = sessionId(orgId, nodeId);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    });
  }, []);

  useEffect(scrollToBottom, [messages, scrollToBottom]);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);

  // P-RC-2 commit P2.6: v2-bound orgs additionally subscribe to the
  // SSE progress feed at ``/api/v2/orgs/{id}/stream`` and surface
  // ``progress_ledger`` events through :class:`ProgressLedgerTimeline`.
  // The legacy ``onWsEvent`` path below stays intact -- this useEffect
  // is purely additive and is short-circuited when ``runtime !== "v2"``.
  const [v2LedgerEvents, setV2LedgerEvents] = useState<ProgressLedgerEvent[]>([]);
  // Keep the conversation pinned to the bottom as live process events stream in
  // (the feed now lives inside the message column, so new node activity should
  // scroll into view just like a new message).
  useEffect(scrollToBottom, [v2LedgerEvents, scrollToBottom]);
  // (``nodeNamesRef`` is declared once above and kept current there.)
  useEffect(() => {
    if (runtime !== "v2" || !orgId) return;
    const stream = createV2Stream(orgId, { apiBase: apiBaseUrl });
    const nameOf = (id?: string) => (id ? (nodeNamesRef.current?.[id] || id) : "");
    // Upsert by id: a node_run_delta carries a STABLE id per (command,node)
    // so successive token increments REPLACE the same rolling entry instead of
    // appending hundreds of rows. Every other event uses a per-event unique id,
    // so for them this behaves exactly like the old append.
    const push = (e: ProgressLedgerEvent) =>
      setV2LedgerEvents((prev) => {
        const idx = prev.findIndex((x) => x.id === e.id);
        if (idx >= 0) {
          const next = prev.slice();
          next[idx] = e;
          return next;
        }
        return prev.length > 200 ? [...prev.slice(-200), e] : [...prev, e];
      });

    const offLedger = stream.onEvent("progress_ledger", (ev: V2StreamEvent) => {
      const p = ev.payload as Record<string, unknown>;
      push({
        id: ev.event_id ?? `${ev.command_id}:${ev.superstep}:${ev.ts}`,
        ts: ev.ts ?? ev.emitted_at ?? new Date().toISOString(),
        is_request_satisfied: Boolean(p?.is_request_satisfied),
        is_in_loop: Boolean(p?.is_in_loop),
        is_progress_being_made: Boolean(p?.is_progress_being_made),
        next_speaker: typeof p?.next_speaker === "string" ? (p.next_speaker as string) : "",
        instruction_or_question:
          typeof p?.instruction_or_question === "string"
            ? (p.instruction_or_question as string)
            : "",
        commandId: (ev.command_id as string | undefined) || undefined,
      });
    });

    // A3 fix: the node-driven org command path runs through the agent
    // pipeline executor, which emits ``agent_run_*`` / ``subtask_assigned``
    // onto the ``lifecycle`` channel (via the OrgRuntime stream tap) rather
    // than the group-chat supervisor's ``progress_ledger`` snapshots. Fold
    // those into the same timeline so the user sees live node activity
    // immediately instead of a frozen "处理中…" with no reaction (图2).
    const offLifecycle = stream.onEvent("lifecycle", (ev: V2StreamEvent) => {
      const p = (ev.payload || {}) as Record<string, unknown>;
      const etype = ev.type || (p.type as string) || "";
      const node = (p.node_id as string) || "";
      const child = (p.child_node_id as string) || "";
      const parent = (p.parent_node_id as string) || "";
      const preview = (p.content_preview as string) || "";
      const outputLen = Number(p.output_len || 0);
      const artifact = (p.artifact_path as string) || "";
      const artifactName = artifact ? artifact.replace(/\\/g, "/").split("/").pop() : "";
      const incomplete = Boolean(p.incomplete);
      const qualityReason = (p.quality_reason as string) || "";
      const toolName = (p.tool_name as string) || "";
      const argsPreview = (p.args_preview as string) || "";
      const resultLen = Number(p.result_len || 0);
      const resultPreview = (p.result_preview as string) || "";
      const streamText = (p.text as string) || "";
      let speaker = ""; let note = ""; let satisfied = false; let progress = true;
      // 图3: lifecycle phase so the timeline can converge a node's status
      // across rounds (start→active→done/incomplete/failed) instead of
      // leaving every node stuck "进行中".
      let phase: "start" | "active" | "done" | "incomplete" | "failed" | undefined;
      // 任务2：无工具(写类)节点的 token 级流式增量。后端按 (command,node)
      // 用稳定 id 滚动更新一条时间线条目，让"文案写手"等节点在生成长文时
      // 实时滚字，而不是结束后才一次性出现。done=true 时落定该条目。
      if (etype === "node_run_delta") {
        const thinkText = (p.thinking as string) || "";
        if (!streamText.trim() && !thinkText.trim()) return;
        // 图4：执行中实时展示节点的【思考过程】+【正在生成】。done=true 时
        // 丢弃思考片段，条目收敛为最终产出摘要并随时间线自动折叠（避免完成后
        // 仍占用大段思考链）。
        const parts: string[] = [];
        if (!p.done && thinkText.trim()) {
          const tclip = thinkText.length > 240 ? `${thinkText.slice(0, 240)}…` : thinkText;
          parts.push(`💭 思考：${tclip}`);
        }
        if (streamText.trim()) parts.push(`✍ ${p.done ? "已生成" : "正在生成"}：${streamText}`);
        else if (!p.done) parts.push("✍ 正在生成…");
        push({
          id: `node_run_delta:${ev.command_id}:${node}`,
          ts: ev.ts ?? ev.emitted_at ?? new Date().toISOString(),
          is_request_satisfied: false,
          is_in_loop: false,
          is_progress_being_made: true,
          next_speaker: nameOf(node),
          instruction_or_question: parts.join("\n\n"),
          nodeId: node || undefined,
          phase: "active",
          commandId: (ev.command_id as string | undefined) || undefined,
        });
        return;
      }
      switch (etype) {
        case "agent_run_started":
          // Show what the node was actually asked to do, not just "开始执行".
          speaker = nameOf(node); note = `开始执行${preview ? `：${preview}` : ""}`; phase = "start"; break;
        case "agent_run_finished": {
          // UI issue #3/#7: a completion must carry CONTENT, not just an action
          // verb — surface the output size and any delivered file.
          speaker = nameOf(node);
          if (incomplete) {
            // Quality gate (test7 RCA): an output that failed the completion
            // check is NOT a delivery — show it as "未通过完成度校验" with the
            // reason and mark progress=false so the timeline doesn't read green.
            const reasonText =
              qualityReason === "thinking_leak" ? "仅输出思考过程，未产出成果" :
              qualityReason === "mid_reasoning" ? "中途停在反复检索，未完成产出" :
              qualityReason === "empty_output" ? "无有效产出" : qualityReason;
            note = `⚠ 产出未通过完成度校验（${reasonText}），需重做/上报`;
            progress = false;
            phase = "incomplete";
            break;
          }
          const bits = ["完成任务"];
          if (outputLen > 0) bits.push(`（产出 ${outputLen} 字）`);
          if (artifactName) bits.push(`📎 ${artifactName}`);
          note = bits.join(" ");
          phase = "done";
          break;
        }
        case "agent_run_failed":
          speaker = nameOf(node); note = "执行失败"; progress = false; phase = "failed"; break;
        // 任务1：把节点执行【过程中】实时产生的工具调用事件并入时间线，
        // 让用户看到"开始执行"和"完成"之间的真实中间动作（调用了什么工具、
        // 入参摘要、产出多少），而不是中间一片空白干等。这些事件本就经
        // org_event_bus → SSE lifecycle 通道实时下发，过去前端 default 丢弃了。
        case "node_tool_called":
          speaker = nameOf(node);
          note = `🛠 调用工具 \`${toolName || "?"}\`${argsPreview ? `：${argsPreview}` : ""}`;
          phase = "active";
          break;
        case "node_tool_completed": {
          speaker = nameOf(node);
          const head = `✓ 工具 \`${toolName || "?"}\` 完成${resultLen > 0 ? `（返回 ${resultLen} 字）` : ""}`;
          // 其余 UI: 在字数之外附上返回内容摘要，至少知道返回了什么（整段过程
          // 在节点完成后由 segment 折叠收起，展开即可逐行查看）。
          note = resultPreview
            ? `${head}\n   ↳ 返回摘要：${resultPreview}${resultLen > resultPreview.length ? "…" : ""}`
            : head;
          phase = "active";
          break;
        }
        case "node_tool_failed": {
          speaker = nameOf(node);
          const failReason = (p.reason as string) || "";
          // test17 "进展缓慢"/自动折叠 收敛: a SINGLE tool failure during an
          // otherwise-active node is normal execution churn, NOT a stall. A
          // failed read_file / web_fetch / web_search / list_directory (a bad
          // path, a 404, a search-budget cap, a flaky network read) is something
          // the node routinely recovers from -- it retries, adapts, or moves on
          // to compose its deliverable. Marking is_progress_being_made=false
          // here flipped the node's segment to status="stall", which BOTH
          // rendered "进展缓慢" AND (because a stalled segment is no longer
          // "running") collapsed the in-flight process log to a single line --
          // exactly the false report the user saw during read_file / 联网检索.
          // Keep the node "进行中" (progress stays true) and surface the failure
          // as an informational line in the expanded body; only a NODE-level
          // failure (agent_run_failed) or escalation is a genuine stall/error.
          if (failReason === "search_budget_reached") {
            note = `ℹ 工具 \`${toolName || "?"}\` 检索预算已用尽，转入基于已获取信息成文`;
          } else {
            note = `⚠ 工具 \`${toolName || "?"}\` 失败${failReason ? `（${failReason}）` : ""}，节点将重试或改用其他方式`;
          }
          phase = "active";
        }
          break;
        case "subtask_assigned":
        case "child_dispatch":
          // 图2: a coordination entry must read 谁→谁 + 为什么/内容摘要, not a
          // bare "已完成". ``parent`` delegates to ``child`` with the task brief.
          speaker = nameOf(child || node);
          note = `↪ ${nameOf(parent) || "主管"} → ${nameOf(child || node)} 派单${preview ? `：${preview}` : ""}`;
          break;
        case "command_done":
          speaker = nameOf(node); note = "指令完成"; satisfied = true; break;
        // 核心1/核心2: 逐级校验 + 重做闭环 — surface the review verdict so the
        // process trace shows the upstream node ACTUALLY reviewing its report.
        case "node_review_passed":
          speaker = nameOf(parent || node);
          note = `✅ 审阅通过下级 ${nameOf(child || node)} 的产出`;
          break;
        case "node_rework_requested": {
          // The report genuinely re-enters 进行中: reopen the child's segment.
          const reason = (p.reason as string) || "";
          speaker = nameOf(child || node);
          note = `🔁 ${nameOf(parent) || "上级"} 退回重做${reason ? `：${reason}` : ""}`;
          phase = "start";
          push({
            id: `node_rework_requested:${ev.command_id}:${child || node}:${ev.ts}`,
            ts: ev.ts ?? ev.emitted_at ?? new Date().toISOString(),
            is_request_satisfied: false,
            is_in_loop: true,
            is_progress_being_made: true,
            next_speaker: speaker,
            instruction_or_question: note,
            nodeId: (child || node) || undefined,
            phase,
            commandId: (ev.command_id as string | undefined) || undefined,
          });
          return;
        }
        case "node_review_escalated": {
          const reason = (p.reason as string) || "";
          speaker = nameOf(child || node);
          note = `⚠ 多次重做仍未达标，已上报上级决策${reason ? `：${reason}` : ""}`;
          progress = false;
          break;
        }
        default:
          return; // ignore high-volume / non-progress lifecycle events
      }
      push({
        id: ev.event_id ?? `${etype}:${ev.command_id}:${ev.superstep}:${ev.ts}`,
        ts: ev.ts ?? ev.emitted_at ?? new Date().toISOString(),
        is_request_satisfied: satisfied,
        is_in_loop: false,
        is_progress_being_made: progress,
        next_speaker: speaker,
        instruction_or_question: note,
        // 图3: attribute the entry to its node so the timeline groups all of a
        // node's rounds into one converging segment (subtask_assigned keys to
        // the dispatching parent so the coordination row sits under it).
        // test16 审阅归属修复: node_review_passed 的 ``node_id`` 是【被审下级】，
        // 但审核这个动作是【上级】执行的。过去按 node（被审下级）归属，导致
        // "✅审阅通过下级X"挂在 X 已收口的段上、又因非终态事件重开一段而错误
        // 显示"进行中"。审核语义上属于上级：keyed 到 parent（reviewer），它在
        // 复核期本就是 busy/running，命令结束后随上级收敛为"已完成"。
        nodeId: (etype === "subtask_assigned" || etype === "child_dispatch" || etype === "node_review_passed")
          ? (parent || node || undefined)
          : (node || undefined),
        phase,
        commandId: (ev.command_id as string | undefined) || undefined,
      });
    });

    return () => {
      offLedger();
      offLifecycle();
      stream.close();
    };
  }, [runtime, orgId, apiBaseUrl]);

  // 拉取可用 IM bot 列表，转成 ForwardTargetOption。
  // 当前每个 bot 只取它的默认 chat_id（credentials 里的 ``default_chat_id``
  // 或 ``chat_id``）；没有默认聊天的 bot 暂不展示，避免空 chat_id 报错。
  // 后续 P3+ 可以让用户在 UI 里手工填 chat_id / thread_id。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/agents/bots`);
        const data = await res.json();
        if (cancelled) return;
        const bots = Array.isArray(data?.bots) ? data.bots : [];
        const opts: ForwardTargetOption[] = [];
        for (const b of bots) {
          if (!b || b.enabled === false) continue;
          const channel = String(b.type || b.id || "").toLowerCase();
          const creds = (b.credentials || {}) as Record<string, unknown>;
          const chatId = String(
            creds.default_chat_id ?? creds.chat_id ?? creds.openid ?? creds.user_id ?? "",
          ).trim();
          if (!channel || !chatId) continue;
          opts.push({
            id: `${b.id || channel}:${chatId}`,
            label: String(b.name || b.id || channel),
            channel,
            chat_id: chatId,
            bot_instance_id: String(b.id || ""),
          });
        }
        if (!cancelled) setAvailableForwards(opts);
      } catch (err) {
        if (!cancelled) console.warn("[OrgChat] load forward targets failed:", err);
      }
    })();
    return () => { cancelled = true; };
  }, [apiBaseUrl]);

  // Load history: backend first, localStorage fallback
  // 整组织视图额外合并 /api/v2/orgs/{org}/activity（含 IM/桌面/指挥台所有来源），
  // 让 IM 来的指令、节点互发的消息也能在指挥台直接看到。
  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    const url = `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${ORG_HISTORY_PAGE_LIMIT}`;
    const wholeOrgView = !nodeId || String(nodeId).trim() === "";

    const nameFmt = (id: string) => nodeNamesRef.current?.[id] || id;

    // UI issue #4/#10: on a fresh reload the per-node activity cards are
    // reconstructed from /activity, but the ROOT NODE's final summary ("任务完成
    // 汇报") was only ever rendered live (when the command_done SSE fired). After
    // a reload the receipt vanished. Re-fetch each completed command's result and
    // append a prominent final-report bubble so the closing summary survives a
    // remount. Bounded to the most recent few commands; idempotent via a stable
    // ``final-report-<cmd>`` id so it dedups against the live bubble.
    // Item 3: build a command_id -> deliverable cards map from the org event
    // store so the reload/rebuild path reattaches the SAME md/pdf download
    // cards the live finalize showed. ``agent_run_finished`` carries the .md
    // artifacts; ``final_report_pdf`` carries the post-convergence PDF. Both
    // stamp ``command_id`` + ``artifact_path`` so we can partition per command.
    const fetchDeliverablesByCommand = async (): Promise<Map<string, FileAttachment[]>> => {
      const byCmd = new Map<string, FileAttachment[]>();
      if (!wholeOrgView) return byCmd;
      try {
        const r = await safeFetch(
          `${apiBaseUrl}/api/v2/orgs/${encodeURIComponent(orgId)}/events?limit=800`,
        );
        const j = await r.json();
        const evs = Array.isArray(j) ? j : Array.isArray(j?.events) ? j.events : [];
        const seenByCmd = new Map<string, Set<string>>();
        // test11 P2: ``file_output_registered`` (emitted the moment a node's
        // write_file / append_file / deliver_artifacts succeeds) is the
        // RELIABLE, universal source of downloadable deliverables -- it covers
        // the real write_file outputs + delivered media (img/video/pdf) that
        // the agent_run_finished.artifact_path (output-text dump) + final PDF
        // alone missed. We accept all three so every completion path shows
        // cards after a refresh.
        const DELIVERABLE_RE = /\.(md|markdown|txt|pdf|png|jpe?g|gif|webp|svg|mp4|mov|webm|csv|json|html?|docx?|pptx?|xlsx?|zip)$/i;
        for (const e of evs) {
          const etype = (e?.type || e?.event_type || "") as string;
          const isFileOut = etype === "file_output_registered";
          if (etype !== "agent_run_finished" && etype !== "final_report_pdf" && !isFileOut) continue;
          if (e?.incomplete) continue; // 质量门禁: 未通过的不作为交付物
          const cid = String(e?.command_id || "");
          // file_output_registered carries ``path`` + ``size_bytes``; the other
          // two carry ``artifact_path`` + ``output_len``.
          const apath = String((isFileOut ? e?.path : e?.artifact_path) || "");
          if (!cid || !apath) continue;
          if (!DELIVERABLE_RE.test(apath)) continue;
          let seen = seenByCmd.get(cid);
          if (!seen) { seen = new Set(); seenByCmd.set(cid, seen); }
          if (seen.has(apath)) continue;
          seen.add(apath);
          const fname = apath.replace(/\\/g, "/").split("/").pop() || "deliverable";
          const size = Number(isFileOut ? e?.size_bytes : e?.output_len) || undefined;
          const arr = byCmd.get(cid) || [];
          arr.push({ filename: fname, file_path: apath, file_size: size });
          byCmd.set(cid, arr);
        }
      } catch {
        /* best effort: events query failure must not break history load */
      }
      return byCmd;
    };

    const fetchFinalReports = async (items: ActivityItem[]): Promise<ChatMsg[]> => {
      if (!wholeOrgView) return [];
      const lastTs = new Map<string, number>();
      for (const it of items) {
        const cid = it.command_id ? String(it.command_id) : "";
        if (!cid) continue;
        const ts = typeof it.ts === "number" ? it.ts : Date.parse(String(it.ts || "")) || 0;
        lastTs.set(cid, Math.max(lastTs.get(cid) || 0, ts));
      }
      const recent = [...lastTs.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
      const deliverablesByCmd = await fetchDeliverablesByCommand();
      const out: ChatMsg[] = [];
      await Promise.all(
        recent.map(async ([cid, ts]) => {
          try {
            const r = await safeFetch(
              `${apiBaseUrl}/api/v2/orgs/${encodeURIComponent(orgId)}/commands/${encodeURIComponent(cid)}`,
            );
            const d = await r.json();
            // test16 ROOT FIX: a command that hit the wall-clock ceiling ends
            // up persisted as ``status: "error"`` (outcome=failed / partial=True)
            // even though the root already produced a full ``final_message`` +
            // PDF on disk. The reload path used to require ``status === "done"``
            // and silently dropped these, so after a mid-run hard-refresh (very
            // likely on a ceiling-length run) the closing 任务完成汇报 receipt
            // never came back -- exactly the "真机没出现" the always-on listener
            // (which already accepts "error") could not cover because the WS
            // command_done had fired during the refresh gap. Accept both so a
            // partial/ceiling result with a real report still gets its bubble.
            const st = String(d?.status || "");
            // test16: "partial" is the backend's delivered-but-hit-a-limit
            // terminal (a real report exists) -- render it like done. "error"
            // is kept for legacy records that still carry a final_message.
            if (st !== "done" && st !== "error" && st !== "partial") return;
            const text = extractCommandResultText(
              d.result as Record<string, unknown> | null | undefined,
            );
            if (!text) return;
            // Item 3: reattach the command's md/pdf deliverables (prefer the
            // final PDF + the longest md first so the receipt leads with the
            // polished report), so the reloaded report bubble is downloadable.
            const files = (deliverablesByCmd.get(cid) || []).slice().sort((a, b) => {
              const ap = /\.pdf$/i.test(a.filename) ? 0 : 1;
              const bp = /\.pdf$/i.test(b.filename) ? 0 : 1;
              if (ap !== bp) return ap - bp;
              return (b.file_size || 0) - (a.file_size || 0);
            });
            const manifest = files.length > 0
              ? `\n\n**📎 ${t("org.chat.deliverablesHeading", "交付物清单")}（${files.length}）**\n\n`
                + files.map(f => `- \`${f.filename}\``).join("\n")
              : "";
            out.push({
              id: `final-report-${cid}`,
              role: "assistant",
              content: `### 📋 ${t("org.chat.finalReportHeading", "任务完成汇报")}\n\n${text}${manifest}`,
              timestamp: (ts || Date.now()) + 1,
              attachments: files.length > 0 ? files : undefined,
              // v21: tag so it renders at the BOTTOM of the command center
              // (below the 编排过程 timeline), consistent with the live finalize.
              kind: "final_report",
              commandId: cid,
            });
          } catch {
            /* best effort: a missing/old command must not break history load */
          }
        }),
      );
      return out;
    };

    const fetchActivityAsMsgs = async (): Promise<ChatMsg[]> => {
      if (!wholeOrgView) return [];
      try {
        const r = await safeFetch(
          `${apiBaseUrl}/api/v2/orgs/${encodeURIComponent(orgId)}/activity?limit=${ORG_HISTORY_PAGE_LIMIT}`,
        );
        const j = await r.json();
        const arr = Array.isArray(j?.items) ? (j.items as ActivityItem[]) : [];
        // 图1 fix: fold the reconstructed /activity history INTO the single
        // bottom 编排过程 timeline (merge by id so we never double-count entries
        // the live SSE already streamed). The flat duplicate bubble is gone;
        // this is where the "有用内容" gets merged "进下方时间线".
        if (runtime === "v2" && !cancelled) {
          const seed = activityItemsToLedger(arr, nameFmt);
          if (seed.length > 0) {
            const tnum = (s: string) =>
              /^\d+$/.test(s) ? Number(s) : Date.parse(s) || 0;
            setV2LedgerEvents((prev) => {
              const byId = new Map(prev.map((e) => [e.id, e]));
              for (const e of seed) if (!byId.has(e.id)) byId.set(e.id, e);
              return [...byId.values()].sort(
                (a, b) => tnum(a.ts || "") - tnum(b.ts || ""),
              );
            });
          }
        }
        const [msgs, reports] = await Promise.all([
          Promise.resolve(activityItemsToMessages(arr, nameFmt)),
          fetchFinalReports(arr),
        ]);
        return [...msgs, ...reports];
      } catch {
        return [];
      }
    };

    (async () => {
      try {
        const [res, activityMsgs] = await Promise.all([
          safeFetch(url),
          fetchActivityAsMsgs(),
        ]);
        const data = await res.json();
        if (cancelled) return;
        const histMsgs: ChatMsg[] = (data.messages || []).map((m: any) => ({
          id: m.id || genId(),
          role: m.role || "assistant",
          content: m.content || "",
          timestamp: m.timestamp || Date.now(),
        }));
        const merged = [...activityMsgs, ...histMsgs].sort(
          (a, b) => (a.timestamp || 0) - (b.timestamp || 0),
        );
        const deduped: ChatMsg[] = [];
        const seen = new Set<string>();
        for (const m of merged) {
          if (m.id && seen.has(m.id)) continue;
          if (m.id) seen.add(m.id);
          deduped.push(m);
        }
        if (deduped.length > 0) {
          console.log(`[OrgChat] Loaded ${deduped.length} entries (hist=${histMsgs.length}, activity=${activityMsgs.length}) for ${convId}`);
          setMessages(deduped);
          saveToLocalStorage(convId, deduped);
        } else {
          const local = loadFromLocalStorage(convId);
          if (local.length > 0) {
            console.log(`[OrgChat] Backend empty, restored ${local.length} messages from localStorage for ${convId}`);
            setMessages(local);
          } else {
            setMessages([]);
          }
        }
      } catch (err) {
        console.warn(`[OrgChat] Backend load failed for ${convId}:`, err);
        if (!cancelled) {
          const local = loadFromLocalStorage(convId);
          console.log(`[OrgChat] Falling back to localStorage: ${local.length} messages for ${convId}`);
          setMessages(local);
        }
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, [convId, apiBaseUrl]);

  // IM / 主聊天组织模式在下发命令时会立刻写入桥接会话；已打开的指挥台需主动拉取
  // 历史，否则要等到用户手动刷新或命令结束事件。
  //
  // 整组织视图（panelNode 为空）会接收所有 IM / 桌面 / org_console 发起的命令并刷新；
  // 节点视图（panelNode 非空）严格按 target 过滤，避免一个节点页面被无关命令污染。
  // 这与 P1 的设计文档"指挥台 = 所有来源的统一时间线"一致：根视图不再因为
  // IM 指令带了 target_node_id 就把整个事件丢弃。
  useEffect(() => {
    if (!loaded) return;
    const wholeOrgView = !nodeId || String(nodeId).trim() === "";
    // Only the org:* WS events the v2 OrgRuntime actually emits trigger a
    // command-center refresh. The v1-era names (command_started, message,
    // broadcast, workbench_tool_status) were dead — v2 never fires them — so
    // they were dropped from the trigger set.
    const orgEvents = new Set([
      "org:command_done",
      "org:command_cancelled",
      "org:task_delegated",
      "org:blackboard_update",
    ]);
    let pendingTimer: ReturnType<typeof setTimeout> | null = null;

    const refresh = async (): Promise<void> => {
      try {
        const histPromise = safeFetch(
          `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${ORG_HISTORY_PAGE_LIMIT}`,
        ).then(r => r.json()).catch(() => ({}));
        const activityPromise = wholeOrgView
          ? safeFetch(
              `${apiBaseUrl}/api/v2/orgs/${encodeURIComponent(orgId)}/activity?limit=${ORG_HISTORY_PAGE_LIMIT}`,
            ).then(r => r.json()).catch(() => ({ items: [] }))
          : Promise.resolve({ items: [] });
        const [histData, actData] = await Promise.all([histPromise, activityPromise]);
        if (!mountedRef.current) return;
        const histMsgs: ChatMsg[] = (histData.messages || []).map((m: any) => ({
          id: m.id || genId(),
          role: m.role || "assistant",
          content: m.content || "",
          timestamp: m.timestamp || Date.now(),
        }));
        const nameFmt2 = (id: string) => nodeNamesRef.current?.[id] || id;
        const actMsgs: ChatMsg[] = activityItemsToMessages(
          (Array.isArray(actData?.items) ? actData.items : []) as ActivityItem[],
          nameFmt2,
        );
        // v21 FIX: this WS-triggered refresh used to ``setMessages(deduped)``
        // with ONLY actMsgs(用户指令) + histMsgs — which WIPED the live final
        // report bubble that ``finalizeResult`` had just appended (it is a
        // local message, not persisted to session history nor reconstructible
        // from /activity). Because ``org:command_done`` ALSO triggers this
        // refresh, the 最终汇报 flashed then vanished ~250ms later. We now
        // preserve any current ``kind==="final_report"`` bubbles across the
        // refresh so the closing summary + download cards stay put.
        setMessages(prev => {
          const merged = [...actMsgs, ...histMsgs].sort(
            (a, b) => (a.timestamp || 0) - (b.timestamp || 0),
          );
          const deduped: ChatMsg[] = [];
          const seen = new Set<string>();
          for (const m of merged) {
            if (m.id && seen.has(m.id)) continue;
            if (m.id) seen.add(m.id);
            deduped.push(m);
          }
          const keptReports = prev.filter(
            m => m.kind === "final_report" && !(m.id && seen.has(m.id)),
          );
          const next = deduped.length > 0 || keptReports.length > 0
            ? [...deduped, ...keptReports].sort(
                (a, b) => (a.timestamp || 0) - (b.timestamp || 0),
              )
            : prev;
          if (next !== prev) saveToLocalStorage(convId, next);
          return next;
        });
      } catch {
        /* ignore */
      }
    };

    const unsub = onWsEvent((event, raw) => {
      if (!orgEvents.has(event)) return;
      const d = raw as Record<string, unknown> | null;
      if (!d || String(d.org_id) !== orgId) return;
      const panelNode = nodeId != null && String(nodeId).trim() !== "" ? String(nodeId) : "";
      if (panelNode) {
        const target = String(
          d.target_node_id ?? d.to_node ?? d.from_node ?? "",
        ).trim();
        if (target && target !== panelNode) return;
      }
      // 多个 WS 事件常常密集到达；用 250ms debounce 合并刷新一次，
      // 避免短时间内连发多次 history/activity 请求。
      if (pendingTimer) clearTimeout(pendingTimer);
      pendingTimer = setTimeout(() => { void refresh(); }, 250);
    });
    return () => {
      unsub();
      if (pendingTimer) clearTimeout(pendingTimer);
    };
  }, [loaded, orgId, nodeId, convId, apiBaseUrl]);

  // Debounced localStorage write on every messages change
  useEffect(() => {
    if (!loaded) return;
    const t = setTimeout(() => saveToLocalStorage(convId, messages), 300);
    return () => clearTimeout(t);
  }, [messages, convId, loaded]);

  // Recover pending commands that completed (or are still running) while unmounted
  useEffect(() => {
    if (!loaded) return;
    const pending = _pendingCmds.get(convId);
    if (!pending || !pending.commandId) return;

    if (pending.finalContent !== null) {
      _pendingCmds.delete(convId);
      const content = pending.finalContent;
      const phId = pending.placeholderId;
      setMessages(prev => {
        if (prev.some(m => m.id === phId && !m.streaming)) return prev;
        return [...prev, { id: phId, role: "assistant" as const, content, timestamp: Date.now() }];
      });
      return;
    }

    // Command still running — show progress and resume polling
    let cancelled = false;
    const phId = pending.placeholderId;
    const progress = pending.lastRendered || t("org.chat.thinking");

    setMessages(prev => {
      if (prev.some(m => m.streaming)) return prev;
      return [...prev, { id: phId, role: "assistant" as const, content: progress, timestamp: Date.now(), streaming: true }];
    });
    setSending(true);
    setPendingCmdId(pending.commandId);

    const resumePoll = async () => {
      while (!cancelled && _pendingCmds.has(convId)) {
        await new Promise(r => setTimeout(r, 3000));
        if (cancelled || !_pendingCmds.has(convId)) break;

        if (mountedRef.current && pending.lastRendered) {
          setMessages(prev => prev.map(m => m.id === phId && m.streaming ? { ...m, content: pending.lastRendered } : m));
        }

        try {
          const res = await safeFetch(`${apiBaseUrl}/api/v2/orgs/${pending.orgId}/commands/${pending.commandId}`);
          const data = await res.json();
          if (data.status === "done" || data.status === "error") {
            if (!_pendingCmds.has(convId)) break;
            _pendingCmds.delete(convId);
            const result = data.result as Record<string, unknown> | null | undefined;
            let resultText = JSON.stringify(data);
            const extracted = extractCommandResultText(result);
            if (extracted) {
              resultText = extracted;
            } else if (result && typeof result.error === "string" && result.error.trim()) {
              resultText = result.error;
            } else if (typeof data.error === "string" && data.error.trim()) {
              resultText = data.error;
            }
            const steps = pending.lastRendered;
            const content = steps
              ? `<details>\n<summary>${t("org.chat.executionSteps", { count: pending.segmentCount })}</summary>\n\n${steps}\n\n</details>\n\n${resultText}`
              : resultText;
            if (mountedRef.current) {
              setMessages(prev => prev.map(m => m.id === phId ? { ...m, content, streaming: false, attachments: pending.allFiles.length > 0 ? pending.allFiles : undefined } : m));
              setSending(false);
              setPendingCmdId(null);
            }
            return;
          }
        } catch { /* poll retry */ }
      }
      if (!cancelled && mountedRef.current && !_pendingCmds.has(convId)) {
        const saved = loadFromLocalStorage(convId);
        if (saved.length > 0) setMessages(saved);
        setSending(false);
        setPendingCmdId(null);
      }
    };
    resumePoll();
    return () => { cancelled = true; };
  }, [loaded, convId, apiBaseUrl]);

  // Flush localStorage immediately on page hide / close
  const messagesRef = useRef<ChatMsg[]>([]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  const convIdRef = useRef(convId);
  useEffect(() => { convIdRef.current = convId; }, [convId]);

  useEffect(() => {
    const flush = () => saveToLocalStorage(convIdRef.current, messagesRef.current);
    const onVisibility = () => { if (document.visibilityState === "hidden") flush(); };
    window.addEventListener("beforeunload", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      flush();
      window.removeEventListener("beforeunload", flush);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // test15 ROOT FIX: an ALWAYS-ON final-report listener for the command center.
  //
  // Reproduced live (headless): a command that completed while this panel was
  // mounted but which THIS panel did not dispatch (the user hard-refreshed
  // mid-run, killing the in-flight ``sendCommand`` subscriber; or the command
  // came from IM / another surface / a prior session) produced NO closing
  // "任务完成汇报" bubble -- the count stayed flat across command_done. The only
  // live builder was the in-flight ``sendCommand`` subscription, scoped to the
  // one command_id it dispatched; and because the drawer is display-toggled
  // (never remounted) reopening it did not re-run the mount-time reload path.
  // That is exactly why "硬刷+重跑仍没根治": the refresh removed the sole
  // subscriber, then the command finished unheard.
  //
  // This listener runs for the whole lifetime of the mounted command-center
  // panel and, on ANY command's completion for this org, rebuilds the same
  // ``final-report-<cid>`` bubble from the persisted command result + its
  // registered deliverables. It is idempotent (stable id upserts, dedups vs the
  // reload path) and defers to an in-flight ``sendCommand`` when one owns the
  // command (so in-session dispatch is not double-rendered).
  useEffect(() => {
    const wholeOrgView = !nodeId || String(nodeId).trim() === "";
    if (!wholeOrgView) return;
    const unsub = onWsEvent((evt, raw) => {
      if (evt !== "org:command_done" && evt !== "org:command_cancelled") return;
      const d = raw as Record<string, unknown> | null;
      if (!d) return;
      if (d.org_id && String(d.org_id) !== orgId) return;
      const cid = String(d.command_id || "");
      if (!cid) return;
      // In-flight sendCommand in THIS session owns this command -> let it render
      // the bubble; avoid a duplicate. Orphaned/foreign completions fall through.
      if (_pendingCmds.get(convId)?.commandId === cid) return;
      void (async () => {
        try {
          const r = await safeFetch(
            `${apiBaseUrl}/api/v2/orgs/${encodeURIComponent(orgId)}/commands/${encodeURIComponent(cid)}`,
          );
          const cd = await r.json();
          const st = String(cd?.status || "");
          // test16: accept the delivered-but-limited "partial" terminal.
          if (st !== "done" && st !== "error" && st !== "partial") return;
          const text = extractCommandResultText(
            cd.result as Record<string, unknown> | null | undefined,
          );
          if (!text) return;
          const files = (await fetchCommandDeliverables(apiBaseUrl, orgId, cid)).sort((a, b) => {
            const ap = /\.pdf$/i.test(a.filename) ? 0 : 1;
            const bp = /\.pdf$/i.test(b.filename) ? 0 : 1;
            if (ap !== bp) return ap - bp;
            return (b.file_size || 0) - (a.file_size || 0);
          });
          const manifest = files.length > 0
            ? `\n\n**📎 ${t("org.chat.deliverablesHeading", "交付物清单")}（${files.length}）**\n\n`
              + files.map(f => `- \`${f.filename}\``).join("\n")
            : "";
          const msg: ChatMsg = {
            id: `final-report-${cid}`,
            role: "assistant",
            content: `### 📋 ${t("org.chat.finalReportHeading", "任务完成汇报")}\n\n${text}${manifest}`,
            timestamp: Date.now(),
            attachments: files.length > 0 ? files : undefined,
            kind: "final_report",
            commandId: cid,
          };
          if (!mountedRef.current) return;
          setMessages(prev => {
            const idx = prev.findIndex(m => m.id === msg.id);
            const next = idx >= 0
              ? prev.map((m, i) => (i === idx ? { ...m, ...msg } : m))
              : [...prev, msg];
            messagesRef.current = next;
            saveToLocalStorage(convId, next);
            return next;
          });
        } catch {
          /* best effort: a missing/racing command must not break the panel */
        }
      })();
    });
    return unsub;
  }, [nodeId, orgId, apiBaseUrl, convId, t]);

  // Push messages to backend session (explicit params to avoid stale-ref bugs)
  const persistToBackend = useCallback(async (
    base: string, cid: string,
    msgs: { role: string; content: string }[],
    replace = false,
  ) => {
    const url = `${base}/api/sessions/${encodeURIComponent(cid)}/messages`;
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: msgs, replace }),
      });
      const data = await res.json();
      console.log(`[OrgChat] Persisted ${msgs.length} messages (replace=${replace}) for ${cid}:`, data);
    } catch (err) {
      console.error(`[OrgChat] Failed to persist messages for ${cid}:`, err);
    }
  }, []);

  const handleClear = useCallback(async () => {
    setMessages([]);
    _pendingCmds.delete(convId);
    setPendingCmdId(null);
    setCanContinuePrevious(false);
    try { localStorage.removeItem(LS_PREFIX + convId); } catch {}
    try {
      await safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}`, {
        method: "DELETE",
      });
    } catch {}
  }, [apiBaseUrl, convId]);

  // 强制终止当前在跑命令：仅 POST 到后端 cancel 端点。
  // 后端会让 send_command 走"stopped_by_watchdog + cancelled_by_user"分支
  // 正常返回，从而触发 handleSend 中的 finalizeResult 收尾；此处不动本地
  // _pendingCmds / 消息流，避免与 send_command 路径竞争产生重复消息。
  const handleStop = useCallback(() => {
    if (!pendingCmdId) return;
    setStopDialogOpen(true);
  }, [pendingCmdId]);

  const confirmStop = useCallback(async () => {
    if (!pendingCmdId) {
      setStopDialogOpen(false);
      return;
    }
    setStopping(true);
    try {
      await safeFetch(
        `${apiBaseUrl}/api/v2/orgs/${encodeURIComponent(orgId)}/commands/${encodeURIComponent(pendingCmdId)}/cancel`,
        { method: "POST" },
      );
    } catch (e) {
      console.warn("[OrgChat] cancel command failed", e);
    } finally {
      setStopping(false);
      setStopDialogOpen(false);
    }
  }, [apiBaseUrl, orgId, pendingCmdId]);

  // Sprint-9: forward declarations -- the two resolvers reference
  // ``handleSend`` which is declared after them, so we keep them in a
  // ref that the dialog's onClick wires up. ``handleSend`` itself is
  // a useCallback that recomputes every render, but the ref is
  // updated in a sibling useEffect-like pattern via the JSX closure.

  // ── 输入附件上传（上游 e2874585 移植；与 ChatView.uploadFile 同协议）──
  const uploadFile = useCallback(async (file: Blob, filename: string): Promise<{
    url: string; localPath?: string; uploadId?: string; size?: number; mimeType?: string;
  }> => {
    const form = new FormData();
    form.append("file", file, filename);
    const res = await safeFetch(`${apiBaseUrl}/api/upload`, {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(15 * 60 * 1000),
    });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    const data = await res.json();
    return {
      url: data.url as string,
      localPath: data.local_path as string | undefined,
      uploadId: data.upload_id as string | undefined,
      size: data.size as number | undefined,
      mimeType: (data.mime_type || data.content_type) as string | undefined,
    };
  }, [apiBaseUrl]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of Array.from(files)) {
      const uploadId = genId();
      const att: PendingInputFile = {
        _uploadId: uploadId,
        name: file.name,
        size: file.size,
        mimeType: file.type,
        type: file.type.startsWith("image/")
          ? "image"
          : file.type.startsWith("video/")
            ? "video"
            : file.type.startsWith("audio/")
              ? "voice"
              : file.type === "application/pdf"
                ? "document"
                : "file",
        uploadStatus: "uploading",
      };
      setPendingFiles(prev => [...prev, att]);
      uploadFile(file, file.name)
        .then(uploaded => {
          setPendingFiles(prev => prev.map(a => a._uploadId === uploadId
            ? {
              ...a,
              url: `${apiBaseUrl}${uploaded.url}`,
              localPath: uploaded.localPath,
              uploadId: uploaded.uploadId,
              size: uploaded.size ?? a.size,
              mimeType: uploaded.mimeType ?? a.mimeType,
              uploadStatus: "uploaded",
            }
            : a));
        })
        .catch(() => {
          setPendingFiles(prev => prev.map(a => a._uploadId === uploadId
            ? { ...a, uploadStatus: "failed" }
            : a));
        });
    }
    e.target.value = "";
  }, [uploadFile, apiBaseUrl]);

  const removePendingFile = useCallback((uploadId: string) => {
    setPendingFiles(prev => prev.filter(a => a._uploadId !== uploadId));
  }, []);

  const handleSend = useCallback(async (opts?: { continuePrevious?: boolean; replaceExisting?: boolean; text?: string }) => {
    const text = (opts?.text ?? input).trim();
    if (!text || sending) return;

    // Fresh composer submits carry pending input attachments; conflict-dialog
    // retries (which pass ``opts.text``) intentionally do not re-send files.
    const isFreshInput = opts?.text === undefined;
    const filesForSend = isFreshInput
      ? pendingFiles.filter(f => f.uploadStatus === "uploaded")
      : [];
    const attachmentsPayload = filesForSend.map(f => ({
      type: f.type,
      name: f.name,
      url: f.url,
      local_path: f.localPath,
      upload_id: f.uploadId,
      size: f.size,
      mime_type: f.mimeType,
    }));
    const userInputAtts: FileAttachment[] = filesForSend.map(f => ({
      filename: f.name,
      file_path: f.localPath || f.url || "",
      file_size: f.size,
    }));

    const userMsg: ChatMsg = {
      id: genId(),
      role: "user",
      content: text,
      timestamp: Date.now(),
      inputAttachments: userInputAtts.length > 0 ? userInputAtts : undefined,
    };
    const placeholderId = genId();
    const placeholder: ChatMsg = {
      id: placeholderId, role: "assistant", content: t("org.chat.thinking"), timestamp: Date.now(), streaming: true,
    };
    setMessages(prev => [...prev, userMsg, placeholder]);
    setInput("");
    if (isFreshInput) setPendingFiles([]);
    setCanContinuePrevious(false);
    setSending(true);

    const nn = (id: string) => nodeNamesRef.current?.[id] || id;

    const segments: TimelineSegment[] = [];
    const activeSegIdx = new Map<string, number>();
    // P8.2: (node_id|tool_name|status) -> last emit ts ms，用于 2s 滑窗去重
    const wbToolStatusDedupe = new Map<string, number>();
    const cmdStartTime = Date.now();
    const activity = { last: Date.now() };
    let lastBlockerSummary = "";

    // Task B: node 忙/闲的唯一真源是 ``org:node_status``。segment 的开合必须
    // 与编排图节点状态 1:1。历史上有两处启发式会让"已 idle 的节点"在指挥台
    // 里被重新点亮成"进行中"，与编排图不符：
    //   1) 30s 内复用 done segment（SEG_REUSE_AFTER_DONE_MS）；
    //   2) 审阅/blackboard/工具等非状态事件走 findOrCreateSeg 时，若上一段已
    //      done 就重开一段。
    // 现在拆成两条严格路径：
    //   * ``openBusySeg`` —— 只由 ``org:node_status=busy``（以及 error）调用，
    //     是唯一可以"开/续"一个节点段的入口。绝不复用已 done 的段：idle 之后
    //     再次 busy 一律开新段（= 编排图里节点重新变忙）。
    //   * ``activeSeg`` —— 返回该节点当前**未收口**的段，没有则返回 null。所有
    //     非状态事件只能往它补充内容；节点已 idle（null）时直接丢弃，绝不重开。
    function newSeg(nodeId: string): TimelineSegment {
      const seg: TimelineSegment = {
        nodeId,
        nodeName: nn(nodeId),
        lines: [],
        files: [],
        done: false,
        lastPushAt: 0,
        filePaths: new Set<string>(),
      };
      segments.push(seg);
      activeSegIdx.set(nodeId, segments.length - 1);
      return seg;
    }

    function openBusySeg(nodeId: string): TimelineSegment {
      const idx = activeSegIdx.get(nodeId);
      if (idx != null) {
        const cur = segments[idx];
        if (!cur.done) return cur; // 仍在忙 → 续用当前打开的段
        // 已 done → 不复用、不重开；一次全新的 busy 开一段全新的工作。
      }
      return newSeg(nodeId);
    }

    // system 层通知（阻塞/长时间运行/空闲提醒）不是编排图节点，不受
    // node_status 契约约束，保留单段复用。
    function findOrCreateSystemSeg(): TimelineSegment {
      const idx = activeSegIdx.get("system");
      if (idx != null && segments[idx]) return segments[idx];
      return newSeg("system");
    }

    // 该节点当前"进行中"（未收口）的段；节点空闲时返回 null。
    function activeSeg(nodeId: string): TimelineSegment | null {
      const idx = activeSegIdx.get(nodeId);
      if (idx == null) return null;
      const seg = segments[idx];
      return seg && !seg.done ? seg : null;
    }

    // 强制收口某节点所有未收口的段（idle/error 的权威信号）。
    function closeNodeSegs(
      nodeId: string,
      mut: (seg: TimelineSegment) => void,
    ): boolean {
      let acted = false;
      for (const seg of segments) {
        if (seg.nodeId === nodeId && !seg.done) {
          mut(seg);
          acted = true;
        }
      }
      return acted;
    }

    // 文件类交付物补充：优先并入当前打开的段；节点已 idle 时并入它最近的
    // （已 done 的）段但**不重开**该段，从而既保留可下载/预览附件，又不会让
    // 已空闲节点在指挥台里错误地重新显示为"进行中"。
    function supplementFile(
      nodeId: string,
      file: FileAttachment,
    ): { seg: TimelineSegment | null; added: boolean } {
      let seg = activeSeg(nodeId);
      if (!seg) {
        const idx = activeSegIdx.get(nodeId);
        seg = idx != null ? segments[idx] : null;
      }
      if (!seg) return { seg: null, added: false };
      return { seg, added: pushSegFile(seg, file) };
    }

    // 进度行去重：相邻同内容、且与上一次 push 间隔 < 1s 视为重复事件，跳过。
    // 用于兜底前端 WebSocket fan-out（已在 platform/websocket.ts 做事件级去重，
    // 此处再加一层 segment 级保险，避免某些 handler 残留导致同行被多次入栈）。
    const SEG_LINE_DEDUPE_MS = 1000;
    function pushSegLine(seg: TimelineSegment, line: string): boolean {
      const now = Date.now();
      const last = seg.lines.length > 0 ? seg.lines[seg.lines.length - 1] : null;
      if (last === line && seg.lastPushAt && now - seg.lastPushAt < SEG_LINE_DEDUPE_MS) {
        return false;
      }
      seg.lines.push(line);
      seg.lastPushAt = now;
      return true;
    }

    // 文件按 file_path 去重：同一交付物在多次事件中只入 files 一次。
    function pushSegFile(seg: TimelineSegment, file: FileAttachment): boolean {
      if (!seg.filePaths) seg.filePaths = new Set<string>();
      const key = file.file_path || file.filename || "";
      if (key && seg.filePaths.has(key)) return false;
      if (key) seg.filePaths.add(key);
      seg.files.push(file);
      return true;
    }

    function segSummaryIcon(seg: TimelineSegment): string {
      if (!seg.failed) return "✓";
      if (seg.exitReason === "loop_terminated") return "⏹";
      return "⚠";
    }

    function renderTimeline(): string {
      return segments.map(seg => {
        const body = seg.lines.join("\n\n");
        if (seg.done) {
          // P10: waiting_user 节点单独走"挂起需回复"模板。默认展开 + 标题用
          // ⏸ 取代 ✓，body 顶部加 blockquote 引导用户在下方输入框回应，
          // 避免被当成普通"完成"折叠而忽略。
          if (seg.paused === "waiting_user") {
            const hint = t("org.chat.waitingUserNotice", {
              name: seg.nodeName,
              defaultValue: `📣 **${seg.nodeName}** 已把决策权交回给你。请在下方输入框直接回应（例如：「同意继续」「换 t2v 降级」「放弃此镜头」），或在指挥台点击「继续」继续推进。`,
            });
            const summaryLabel = t("org.chat.waitingUserSummary", {
              name: seg.nodeName,
              defaultValue: `⏸ ${seg.nodeName} · 正在等待你的回复`,
            });
            return `<details open>\n<summary>${summaryLabel}</summary>\n\n> ${hint}\n\n${body}\n\n</details>`;
          }
          const icon = segSummaryIcon(seg);
          // 非正常结束时默认展开，让用户立刻看到诊断；正常完成保持折叠
          const detailsTag = seg.failed ? "<details open>" : "<details>";
          return `${detailsTag}\n<summary>${icon} ${seg.nodeName}</summary>\n\n${body}\n\n</details>`;
        }
        return `**${t("org.chat.processing", { name: seg.nodeName })}**\n\n${body}`;
      }).join("\n\n");
    }

    function updatePreview() {
      activity.last = Date.now();
      const rendered = renderTimeline();
      const pending = _pendingCmds.get(convId);
      if (pending) {
        pending.lastRendered = rendered;
        pending.segmentCount = segments.length;
        // 持久化时也按 file_path 去重，防止 _pendingCmds 缓存里残留重复
        const seen = new Set<string>();
        const flat: FileAttachment[] = [];
        for (const s of segments) {
          for (const f of s.files) {
            const key = f.file_path || f.filename || "";
            if (key && seen.has(key)) continue;
            if (key) seen.add(key);
            flat.push(f);
          }
        }
        pending.allFiles = flat;
      }
      if (!mountedRef.current) return;
      // P10: 进度流式更新时也把 attachments 推上去，让用户在过程阶段就能
      // 看到图片/视频预览（FileAttachmentCard 已支持 img/video 内嵌）。
      // 之前只在 finalize 时才注入 attachments，进度期间黑板登记的媒体
      // 一直被隐藏直到任务完成。
      const streamingAtts = pending?.allFiles && pending.allFiles.length > 0
        ? pending.allFiles
        : undefined;
      // 图1 fix (test7 RCA): for v2 orgs the LIVE process is already rendered by
      // the ``编排过程`` ProgressLedgerTimeline above the chat. Echoing the same
      // rolling "主编 处理中.../开始处理..." segment reconstruction inside the
      // assistant bubble was the redundant top bubble the user kept seeing. We
      // keep tracking ``segments`` (so the FINAL report's collapsed 执行过程 +
      // attachments still work) but show only a minimal "处理中" indicator in the
      // live bubble. v1 orgs (no ProgressLedgerTimeline) keep the rolling text.
      const liveContent = runtime === "v2"
        ? t("org.chat.orgWorkingLive", "组织正在处理中…（实时编排过程见上方「编排过程」）")
        : (rendered || t("org.chat.thinking"));
      setMessages(prev => prev.map(m => m.id === placeholderId
        ? { ...m, content: liveContent, attachments: streamingAtts ?? m.attachments }
        : m));
    }

    const unsubProgress = onWsEvent((event, raw) => {
      const d = raw as Record<string, unknown> | null;
      if (!d || d.org_id !== orgId) return;
      const nid = (d.node_id || d.from_node || "") as string;
      const toN = (d.to_node || "") as string;

      if (event === "org:node_status") {
        const st = d.status as string;
        if (st === "busy") {
          // 唯一可以"开/续"节点段的入口。
          const task = (d.current_task || "") as string;
          if (task.startsWith(t("org.chat.notification"))) return;
          const seg = openBusySeg(nid);
          if (pushSegLine(seg, `${t("org.chat.startProcessing", { name: `**${nn(nid)}**` })}${task ? `: ${task}` : ""}`)) {
            updatePreview();
          }
        } else if (st === "idle") {
          // 权威收口：强制关闭该节点所有未收口的段（正常只有一段）。
          const exitReason = (d.exit_reason as string) || "normal";
          const soft = isSoftOrgExitReason(exitReason);
          closeNodeSegs(nid, seg => {
            seg.done = true; seg.doneAt = Date.now();
            seg.exitReason = exitReason;
            // 软退出在用户界面按完成/等待处理；真正异常交给后续事件显示极简状态。
            if (soft) {
              seg.failed = false;
              pushSegLine(seg, t("org.chat.completed", { name: `**${nn(nid)}**` }));
            } else {
              seg.failed = true;
            }
          });
          updatePreview();
        } else if (st === "error") {
          // error 同属状态事件：收口未收口段；若该节点没有已打开的段，则开一段
          // 用于呈现失败（这是唯一允许 error 开段的场景）。
          const acted = closeNodeSegs(nid, seg => {
            seg.done = true; seg.doneAt = Date.now();
            pushSegLine(seg, t("org.chat.errored", { name: `**${nn(nid)}**` }));
          });
          if (!acted) {
            const seg = openBusySeg(nid);
            seg.done = true; seg.doneAt = Date.now();
            pushSegLine(seg, t("org.chat.errored", { name: `**${nn(nid)}**` }));
          }
          updatePreview();
        }
      } else if (event === "org:task_delegated") {
        // 非状态事件：只能补充到该节点当前打开的段；已 idle 则丢弃，不重开。
        const task = ((d.task || "") as string);
        const seg = activeSeg(nid);
        if (seg && pushSegLine(seg, t("org.chat.taskAssigned", { from: `**${nn(nid)}**`, to: `**${nn(toN)}**`, task }))) {
          updatePreview();
        }
      } else if (event === "org:task_delivered") {
        const summary = ((d.summary || "") as string);
        const seg = activeSeg(nid);
        if (seg && pushSegLine(seg, `${t("org.chat.delivered", { name: `**${nn(nid)}**` })}${summary ? `: ${summary}` : ""}`)) {
          updatePreview();
        }
      } else if (event === "org:task_complete") {
        const preview = ((d.result_preview || "") as string);
        const reason = (d.exit_reason as string) || "normal";
        const idx = activeSegIdx.get(nid);
        if (idx != null && segments[idx]) {
          segments[idx].resultPreview = preview;
          segments[idx].exitReason = reason;
          // P9.2: 软退出（normal / ask_user / waiting_user / verify_incomplete）
          // 必须把 failed 打回 false，否则节点曾经被 max_iterations / timeout
          // 终止过、随后重启成功的轨迹会一直顶着红色 ⚠ 直到这条 command
          // 全部结束，与"业务上其实成功了"的最终状态矛盾。
          if (isSoftOrgExitReason(reason)) {
            segments[idx].failed = false;
            segments[idx].diagnosis = undefined;
          }
          // P10: waiting_user 单独标记 paused 状态，让 renderTimeline 显眼
          // 提示用户"需要你回复"。如果不标，用户会以为节点正常完成、
          // 没有任何挂起，于是看到 producer/art-director 静默几分钟后自己
          // 点取消（产生"任务莫名其妙被取消"的错觉）。
          if (reason === "waiting_user") {
            segments[idx].paused = "waiting_user";
          } else {
            segments[idx].paused = undefined;
          }
        }
      } else if (event === "org:task_terminated") {
        const preview = ((d.result_preview || "") as string);
        const reason = (d.exit_reason as string) || "loop_terminated";
        const diagnosis = (d.diagnosis as FailureDiagnosis | undefined) || undefined;
        const idx = activeSegIdx.get(nid);
        if (idx != null && segments[idx]) {
          const seg = segments[idx];
          seg.done = true; seg.doneAt = Date.now();
          seg.resultPreview = preview;
          seg.exitReason = reason;
          seg.failed = true;
          seg.diagnosis = diagnosis;
          pushSegLine(seg, t("org.chat.forceTerminated", { name: `**${nn(nid)}**` }));
        }
        updatePreview();
      } else if (event === "org:task_failed") {
        const preview = ((d.result_preview || "") as string);
        const reason = (d.exit_reason as string) || "max_iterations";
        const diagnosis = (d.diagnosis as FailureDiagnosis | undefined) || undefined;
        const idx = activeSegIdx.get(nid);
        if (idx != null && segments[idx]) {
          const seg = segments[idx];
          seg.done = true; seg.doneAt = Date.now();
          seg.resultPreview = preview;
          seg.exitReason = reason;
          if (isSoftOrgExitReason(reason)) {
            seg.failed = false;
            seg.diagnosis = undefined;
            pushSegLine(seg, t("org.chat.completed", { name: `**${nn(nid)}**` }));
            updatePreview();
            return;
          }
          seg.failed = true;
          seg.diagnosis = diagnosis;
          const reasonLabel =
            reason === "max_iterations" ? t("org.chat.maxIterations") :
            t("org.chat.executionFailed");
          pushSegLine(seg, t("org.chat.incomplete", { name: `**${nn(nid)}**`, reason: reasonLabel }));
        }
        updatePreview();
      } else if (event === "org:blackboard_update" || event === "org:file_output_registered") {
        // test11 P2: ``org:file_output_registered`` fires the moment a node's
        // write_file / append_file / deliver_artifacts succeeds, carrying the
        // same ``resource`` shape — so deliverable cards (含 过程/最终 文件 +
        // 图片/视频/pdf) appear live, then persist via the events.jsonl reload.
        const mt = (d.memory_type as string) || (event === "org:file_output_registered" ? "resource" : "");
        const fname = (d.filename || d.name) as string | undefined;
        const fpath = (d.file_path || d.path) as string | undefined;
        const fsize = (d.file_size ?? d.size) as number | undefined;
        if (mt === "resource" && fname && fpath) {
          // 交付物：并入打开的段（含提示行）；节点已 idle 时并入其最近的已 done
          // 段以保留附件，但不重开该段、不新增"进行中"行。
          const { seg, added } = supplementFile(nid, {
            filename: fname,
            file_path: fpath,
            file_size: fsize,
          });
          if (seg && added) {
            if (!seg.done) {
              pushSegLine(seg, t("org.chat.fileOutput", { name: `**${nn(nid)}**`, file: fname }));
            }
            updatePreview();
          }
        } else {
          // 纯 blackboard 更新是非状态事件：只补充打开的段，已 idle 则丢弃。
          const seg = activeSeg(nid);
          if (seg && pushSegLine(seg, t("org.chat.blackboardUpdate", { name: `**${nn(nid)}**` }))) {
            updatePreview();
          }
        }
      } else if (event === "org:command_stuck_warning") {
        const idle = Number(d.idle_secs || 0);
        const minutes = Math.floor(idle / 60);
        const sec = idle % 60;
        const idleStr = minutes > 0 ? t("org.chat.idleMinSec", { m: minutes, s: sec }) : t("org.chat.idleSec", { s: sec });
        const seg = findOrCreateSystemSeg();
        if (pushSegLine(
          seg,
          t("org.chat.orgIdle", { duration: idleStr }),
        )) {
          updatePreview();
        }
      } else if (event === "org:workbench_tool_status") {
        const status = (d.status || "") as string;
        const toolName = (d.tool_name || "") as string;
        const error = (d.error || "") as string;
        // P8.2: 2s 滑窗去重。后端 fan-out + 偶发 retry 会让同一个 (node,
        // tool, status) 在很短间隔内连续 emit；既会让进度行刷屏也会让
        // segment 被反复"重启"。同窗口内重复直接跳过。
        const wbKey = `${nid}|${toolName}|${status}`;
        const now = Date.now();
        const lastEmit = wbToolStatusDedupe.get(wbKey) || 0;
        if (now - lastEmit < 2000) {
          return;
        }
        wbToolStatusDedupe.set(wbKey, now);
        // 工具事件是非状态事件：只能补充到当前打开的段；已 idle 则丢弃，不重开。
        const seg = activeSeg(nid);
        if (!seg) return;
        const line =
          status === "running"
            ? t("org.chat.workbenchToolRunning", { tool: toolName })
            : status === "failed"
              ? t("org.chat.workbenchToolFailed", { tool: toolName, error })
              : t("org.chat.workbenchToolFinished", { tool: toolName });
        if (pushSegLine(seg, line)) {
          updatePreview();
        }
      }
    });

    // 跨 segment 收集时按 file_path 去重，避免最终附件区出现重复
    function collectAllFiles(): FileAttachment[] {
      const seen = new Set<string>();
      const out: FileAttachment[] = [];
      for (const s of segments) {
        for (const f of s.files) {
          const key = f.file_path || f.filename || "";
          if (key && seen.has(key)) continue;
          if (key) seen.add(key);
          out.push(f);
        }
      }
      return out;
    }

    const finalizeResult = (content: string, files?: FileAttachment[], role: "assistant" | "system" = "assistant") => {
      const pending = _pendingCmds.get(convId);
      const cmdId = pending?.commandId || "";
      if (pending) {
        if (pending.placeholderId !== placeholderId) return;
        pending.finalContent = content;
        // test17: mark finalized BEFORE deleting so any in-flight updatePreview
        // that already captured ``pending`` bails out instead of resurrecting
        // the "组织正在处理中…" placeholder over the report.
        pending.finalized = true;
        _pendingCmds.delete(convId);
      }
      const atts = files && files.length > 0 ? files : undefined;
      // test17 ROOT FIX: the closing report is its OWN bottom bubble with a
      // stable ``final-report-<cid>`` id -- NOT the streaming placeholder. The
      // placeholder is retired here so a late ``final_report_pdf`` /
      // ``node_status idle`` event (which fires seconds AFTER command_done and
      // calls updatePreview -> rewrites the placeholder) can no longer clobber
      // the finalized report. This also unifies the live path with the reload /
      // always-on paths, which already key the receipt by ``final-report-<cid>``.
      const finalId = cmdId ? `final-report-${cmdId}` : placeholderId;
      const finalMsg: ChatMsg = {
        id: finalId,
        role,
        content,
        timestamp: Date.now(),
        attachments: atts,
        kind: "final_report",
        commandId: cmdId || undefined,
      };
      if (mountedRef.current) {
        setMessages(prev => {
          // Drop the streaming placeholder (its live process lives in the
          // ProgressLedgerTimeline for v2; v1 keeps the process embedded in the
          // report content itself), then upsert the stable report bubble.
          const withoutPlaceholder = prev.filter(m => m.id !== placeholderId);
          const idx = withoutPlaceholder.findIndex(m => m.id === finalId);
          const next = idx >= 0
            ? withoutPlaceholder.map((m, i) => (i === idx ? { ...m, ...finalMsg } : m))
            : [...withoutPlaceholder, finalMsg];
          messagesRef.current = next;
          return next;
        });
      } else {
        const existing = loadFromLocalStorage(convId);
        const hasUser = existing.some(m => m.id === userMsg.id);
        const base = (hasUser ? existing : [...existing, userMsg]).filter(m => m.id !== placeholderId);
        const idx = base.findIndex(m => m.id === finalId);
        const toSave = idx >= 0
          ? base.map((m, i) => (i === idx ? { ...m, ...finalMsg } : m))
          : [...base, finalMsg];
        saveToLocalStorage(convId, toSave);
        persistToBackend(apiBaseUrl, convId, toSave.map(m => ({ role: m.role, content: m.content })), true);
      }
    };

    // test16: a "delivered but hit a limit" command completes successfully but
    // should carry a gentle note that it wrapped up at a time/budget ceiling,
    // rather than either hiding it or looking like a failure.
    const partialDeliveryNote = (
      result: Record<string, unknown> | null | undefined,
    ): string | undefined => {
      const reason = result && typeof result.degraded_reason === "string"
        ? String(result.degraded_reason)
        : "";
      if (reason === "wall_clock_ceiling") {
        return "本次任务因触达运行时限而收尾，以上为已交付的尽力而为成果（部分内容可能未完全展开）。";
      }
      if (reason === "turn_budget" || reason === "replan_budget") {
        return "本次任务因触达执行预算而收尾，以上为已交付的尽力而为成果（部分内容可能未完全展开）。";
      }
      return "本次任务在触达处理上限后收尾，以上为已交付的尽力而为成果（部分内容可能未完全展开）。";
    };

    // test17: the pure "任务完成汇报" block (heading + report + deliverables
    // manifest + optional limit note + done banner), WITHOUT the collapsed
    // 执行过程. v2 command centers render this as the standalone bottom bubble
    // because the process already lives in the ProgressLedgerTimeline; only the
    // legacy v1 path (no separate timeline) wraps it with the process below.
    const buildReportBlock = (
      resultText: string,
      opts?: { stoppedByWatchdog?: boolean; warning?: string; files?: FileAttachment[] }
    ): string => {
      const stopped = !!opts?.stoppedByWatchdog;
      const banner = stopped
        ? `\n\n<div class="ocp-done-banner ocp-done-banner-warn">&#x26A0;&#xFE0F; ${t("org.chat.orgAutoPaused")}</div>`
        : `\n\n<div class="ocp-done-banner">&#x2705; ${t("org.chat.taskCompleted")}</div>`;
      const warningLine = opts?.warning
        ? `\n\n> ${opts.warning}`
        : "";
      const reportHeading = stopped ? "" : `### 📋 ${t("org.chat.finalReportHeading", "任务完成汇报")}\n\n`;
      const files = opts?.files || [];
      const manifest = files.length > 0
        ? `\n\n**📎 ${t("org.chat.deliverablesHeading", "交付物清单")}（${files.length}）**\n\n`
          + files.map(f => `- \`${f.filename || (f.file_path || "").split(/[\\/]/).pop() || "file"}\``).join("\n")
        : "";
      return `${reportHeading}${resultText}${manifest}${warningLine}${banner}`;
    };

    const wrapWithProcess = (
      resultText: string,
      opts?: { stoppedByWatchdog?: boolean; warning?: string; files?: FileAttachment[] }
    ): string => {
      const reportBlock = buildReportBlock(resultText, opts);
      // v2 keeps the process in the timeline, so the bottom bubble is just the
      // report; only v1 (no timeline) embeds the collapsed 执行过程 below it.
      if (runtime === "v2") return reportBlock;
      if (segments.length === 0) return reportBlock;
      const allCollapsed = segments.map(seg => {
        const body = seg.lines.join("\n\n");
        return `<details>\n<summary>✓ ${seg.nodeName}</summary>\n\n${body}\n\n</details>`;
      }).join("\n\n");
      // UI issue #3: keep the per-node execution process visible (collapsed)
      // above the final report so the user can drill into what each node did.
      return `<details>\n<summary>🛠 ${t("org.chat.processDetailsHeading", "执行过程")}（${segments.length}）</summary>\n\n${allCollapsed}\n\n</details>\n\n---\n\n${reportBlock}`;
    };

    const getCommandResultText = (
      result: Record<string, unknown> | null | undefined,
      error: unknown,
      fallback: unknown,
    ): string => {
      const extracted = extractCommandResultText(result);
      if (extracted) return extracted;
      if (result && typeof result.error === "string" && result.error.trim()) return result.error;
      if (typeof error === "string" && error.trim()) return error;
      return JSON.stringify(fallback);
    };

    let finalContent = "";
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/v2/orgs/${orgId}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          target_node_id: nodeId || undefined,
          continue_previous: !!opts?.continuePrevious,
          replace_existing: !!opts?.replaceExisting,
          attachments: attachmentsPayload,
          forward_to: forwardTargets.map(ft => ({
            channel: ft.channel,
            chat_id: ft.chat_id,
            thread_id: ft.thread_id ?? null,
            bot_instance_id: ft.bot_instance_id ?? "",
            label: ft.label,
          })),
        }),
      });
      // Sprint-9: surface the 409 (org_command_conflict) shape into
      // an inline dialog so the user picks replace_existing /
      // continue_previous / cancel instead of seeing a generic error
      // toast.
      if (res.status === 409) {
        const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        const existingId =
          (typeof data.command_id === "string" && data.command_id) ||
          (typeof (data.detail as Record<string, unknown> | undefined)?.command_id === "string"
            ? ((data.detail as Record<string, unknown>).command_id as string)
            : "");
        const msg =
          (typeof data.detail === "string" && data.detail) ||
          (typeof (data.detail as Record<string, unknown> | undefined)?.message === "string"
            ? ((data.detail as Record<string, unknown>).message as string)
            : typeof data.message === "string"
              ? (data.message as string)
              : t(
                  "org.chat.conflictDefault",
                  "组织当前已有命令在执行，请选择处理方式。",
                ));
        setConflictDialog({
          pendingText: text,
          existingCommandId: existingId,
          message: msg,
        });
        finalContent = `> ${msg}`;
        finalizeResult(finalContent);
        return;
      }
      const data = await res.json();
      const commandId = data.command_id as string | undefined;

      if (!commandId) {
        finalContent =
          extractCommandResultText(data.result as Record<string, unknown> | null | undefined) ||
          (typeof data.result === "string" ? data.result : "") ||
          data.error ||
          JSON.stringify(data);
        finalizeResult(finalContent);
      } else {
        _pendingCmds.set(convId, { commandId, orgId, placeholderId, lastRendered: "", segmentCount: 0, allFiles: [], finalContent: null, userMsgId: userMsg.id });
        setPendingCmdId(commandId);
        // test17: stamp the command_id onto the user bubble + placeholder so the
        // command center can group this run's (用户指令 → 编排过程 → 总结) block
        // and rebuild it per-command after a refresh.
        if (mountedRef.current) {
          setMessages(prev => prev.map(m =>
            m.id === userMsg.id || m.id === placeholderId ? { ...m, commandId } : m
          ));
        }

        let resolved = false;
        const unsubDone = onWsEvent((evt, raw) => {
          const d = raw as Record<string, unknown> | null;
          if (evt !== "org:command_done" || !d || d.command_id !== commandId) return;
          if (resolved) return;
          resolved = true;
          const result = d.result as Record<string, unknown> | null;
          const error = d.error as string | undefined;
          const resultText = getCommandResultText(result, error, d);
          const stopped = !!(result && result.stopped_by_watchdog);
          const cancelled = !!(result && result.cancelled_by_user);
          const partialDelivery = !stopped && !!(result && result.partial);
          const warning = (result && typeof result.warning === "string" ? result.warning as string : undefined)
            || (partialDelivery ? partialDeliveryNote(result as Record<string, unknown>) : undefined);
          setTimeout(() => {
            const files = collectAllFiles();
            finalContent = wrapWithProcess(resultText, { stoppedByWatchdog: stopped, warning, files });
            finalizeResult(finalContent, files);
            if (stopped || cancelled) setCanContinuePrevious(true);
          }, 500);
        });

        while (!resolved) {
          await new Promise(r => setTimeout(r, 5000));
          if (resolved) break;
          try {
            const poll = await safeFetch(`${apiBaseUrl}/api/v2/orgs/${orgId}/commands/${commandId}`);
            const pd = await poll.json();
            if (pd.status === "running" && typeof pd.blocker_summary === "string" && pd.blocker_summary.trim()) {
              const blockerSummary = pd.blocker_summary.trim();
              const seg = findOrCreateSystemSeg();
              const line = t("org.chat.commandBlocker", { reason: blockerSummary });
              if (blockerSummary !== lastBlockerSummary && pushSegLine(seg, line)) {
                lastBlockerSummary = blockerSummary;
                updatePreview();
              }
            }
            if (pd.status === "done" || pd.status === "error" || pd.status === "partial") {
              if (!resolved) {
                resolved = true;
                const resultText = getCommandResultText(pd.result, pd.error, pd);
                const stopped = !!(pd.result && pd.result.stopped_by_watchdog);
                const cancelled = !!(pd.result && pd.result.cancelled_by_user);
                const partialDelivery = !stopped && (pd.status === "partial" || !!(pd.result && pd.result.partial));
                const warning = (pd.result && typeof pd.result.warning === "string" ? pd.result.warning : undefined)
                  || (partialDelivery ? partialDeliveryNote(pd.result as Record<string, unknown>) : undefined);
                const files = collectAllFiles();
                finalContent = wrapWithProcess(resultText, { stoppedByWatchdog: stopped, warning, files });
                finalizeResult(finalContent, files);
                if (stopped || cancelled) setCanContinuePrevious(true);
              }
            }
          } catch { /* retry */ }
          if (!resolved && Date.now() - activity.last > 60000) {
            const elapsed = Math.round((Date.now() - cmdStartTime) / 1000);
            const min = Math.floor(elapsed / 60);
            const sec = elapsed % 60;
            const timeStr = min > 0 ? t("org.chat.idleMinSec", { m: min, s: sec }) : t("org.chat.idleSec", { s: sec });
            const seg = findOrCreateSystemSeg();
            seg.lines = [`... ${t("org.chat.longRunning", { duration: timeStr })} ...`];
            updatePreview();
          }
        }
        unsubDone();
      }
    } catch (e: any) {
      finalContent = t("org.chat.sendFailed", { error: e.message || e });
      finalizeResult(finalContent, undefined, "system");
    } finally {
      unsubProgress();
      setSending(false);
      setPendingCmdId(null);
      if (mountedRef.current) {
        const all = messagesRef.current.filter(m => !m.streaming);
        if (all.length > 0) {
          persistToBackend(apiBaseUrl, convId, all.map(m => ({ role: m.role, content: m.content })), true);
        }
      }
    }
  }, [input, sending, orgId, nodeId, apiBaseUrl, convId, persistToBackend, forwardTargets, pendingFiles]);

  const handleContinuePrevious = useCallback(() => {
    handleSend({
      continuePrevious: true,
      text: t("org.chat.continuePreviousPrompt"),
    });
  }, [handleSend, t]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Shared message-bubble renderer so the main scroll column and the bottom
  // "最终汇报" block (rendered after the timeline) stay identical.
  const renderMsgBubble = (m: ChatMsg) => (
    <div
      key={m.id}
      className={[
        "ocp-msg",
        `ocp-msg-${m.role}`,
        m.kind ? `ocp-msg-${m.kind}` : "",
        m.streaming ? "ocp-msg-streaming" : "",
      ].filter(Boolean).join(" ")}
    >
      <div className={`ocp-msg-bubble ${m.role !== "user" ? "chatMdContent" : ""}`}>
        {m.role === "user" ? (
          <>
            {m.content}
            {m.inputAttachments && m.inputAttachments.length > 0 && (
              <div style={{ marginTop: 8, display: "flex", flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
                {m.inputAttachments.map((f, i) => (
                  <FileAttachmentCard key={f.file_path || `in-${i}`} file={f} apiBaseUrl={apiBaseUrl} inline />
                ))}
              </div>
            )}
          </>
        ) : md ? (
          <md.ReactMarkdown remarkPlugins={md.remarkPlugins} rehypePlugins={md.rehypePlugins}>
            {m.content}
          </md.ReactMarkdown>
        ) : (
          m.content
        )}
        {m.streaming && <span className="ocp-typing">●</span>}
        {m.attachments && m.attachments.length > 0 && (
          /* P10: 不再用 !m.streaming 阻塞 attachments 渲染——进度阶段
             收到的图片/视频可以即时显示给用户，避免任务完成前用户
             一直看不到媒体。FileAttachmentCard 已根据扩展名渲染
             img / video 内嵌预览。 */
          <div style={{ borderTop: "1px solid rgba(100,116,139,0.2)", marginTop: 10, paddingTop: 8, display: "flex", flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
            {m.attachments.map((f, i) => (
              <FileAttachmentCard key={f.file_path || i} file={f} apiBaseUrl={apiBaseUrl} inline />
            ))}
          </div>
        )}
      </div>
    </div>
  );

  // test17 Task3: render the v2 command center as a multi-round conversation.
  // Each command is ONE block (用户指令 → 编排过程 → 根节点总结气泡+附件); blocks
  // are chronological; the in-flight command's process stays expanded while
  // historical commands' processes fold away (they still exist -- click to
  // expand). Messages with no owning command (system errors, legacy bubbles)
  // are interleaved by timestamp so nothing is lost.
  const renderV2Conversation = () => {
    const parseCid = (m: ChatMsg): string =>
      m.commandId ||
      (m.id.startsWith("final-report-") ? m.id.slice("final-report-".length) : "") ||
      (m.id.startsWith("user-cmd-") ? m.id.slice("user-cmd-".length) : "");

    interface Block { cid: string; user?: ChatMsg; report?: ChatMsg; ts: number }
    const blocks = new Map<string, Block>();
    const loose: ChatMsg[] = [];
    const cmdOrder: string[] = [];
    const touch = (cid: string, ts: number): Block => {
      let b = blocks.get(cid);
      if (!b) { b = { cid, ts }; blocks.set(cid, b); cmdOrder.push(cid); }
      b.ts = Math.min(b.ts, ts);
      return b;
    };

    for (const m of messages) {
      if (m.streaming) continue; // v2 live process lives in the timeline
      const cid = parseCid(m);
      if (m.kind === "final_report") {
        if (cid) touch(cid, m.timestamp).report = m;
        else loose.push(m);
        continue;
      }
      if (m.role === "user" && cid) {
        touch(cid, m.timestamp).user = m;
        continue;
      }
      loose.push(m); // system / activity / un-attributed bubbles
    }
    // Commands that only produced ledger events so far (process started before
    // any bubble exists) still deserve a block.
    for (const e of v2LedgerEvents) {
      const cid = (e.commandId || "").trim();
      if (cid && !blocks.has(cid)) {
        const tnum = /^\d+$/.test(e.ts || "") ? Number(e.ts) : Date.parse(e.ts || "") || 0;
        touch(cid, tnum);
      }
    }

    type Unit = { ts: number; el: JSX.Element };
    const units: Unit[] = [];
    for (const m of loose) units.push({ ts: m.timestamp, el: renderMsgBubble(m) });

    for (const cid of cmdOrder) {
      const b = blocks.get(cid)!;
      const evForCmd = v2LedgerEvents.filter(e => (e.commandId || "").trim() === cid);
      const isActive = pendingCmdId === cid;
      const timeline = evForCmd.length > 0 ? (
        <ProgressLedgerTimeline
          events={evForCmd}
          nodeNameOf={(id) => nodeNamesRef.current?.[id] || id}
          running={isActive && (sending || !!pendingCmdId)}
          activeCommandId={cid}
        />
      ) : null;
      const el = (
        <div className="ocp-cmd-block" key={`cmd-${cid}`} data-command-id={cid}>
          {b.user && renderMsgBubble(b.user)}
          {timeline && (
            isActive ? (
              <div className="ocp-process" data-testid="ocp-v2-timeline">
                <div className="ocp-process-title">
                  <span className="ocp-process-spark" />
                  {t("org.chat.liveProcessTitle", "编排过程")}
                </div>
                {timeline}
              </div>
            ) : (
              <details className="ocp-process ocp-process-collapsed" data-testid="ocp-v2-timeline">
                <summary className="ocp-process-title">
                  {t("org.chat.processDetailsHeading", "执行过程")}
                </summary>
                {timeline}
              </details>
            )
          )}
          {b.report && renderMsgBubble(b.report)}
        </div>
      );
      units.push({ ts: b.ts, el });
    }

    units.sort((a, b) => a.ts - b.ts);
    // Each unit's element already carries a stable key (msg id / cmd id).
    return <>{units.map((u) => u.el)}</>;
  };

  return (
    <div className="ocp-root">
      {showHeader && (
        <div className="ocp-header">
          <div className="ocp-header-info">
            <div className="ocp-header-dot" />
            <div className="ocp-header-titles">
              <span className="ocp-header-title">{title || (nodeId ? t("org.chat.conversationTitle", { name: nodeId }) : t("org.chat.commandCenter"))}</span>
              {orgId && (
                <button
                  type="button"
                  className="ocp-header-id"
                  title={`${t("org.chat.copyOrgId")} · ${orgId}`}
                  onClick={async (e) => {
                    e.stopPropagation();
                    const ok = await copyToClipboard(orgId);
                    if (ok) toast.success(t("org.chat.orgIdCopied"));
                    else toast.error(t("org.chat.orgIdCopyFailed"));
                  }}
                >
                  <span className="ocp-header-id-label">ID</span>
                  <code className="ocp-header-id-value">{orgId}</code>
                  <IconCopy size={10} />
                </button>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {messages.length > 0 && (
              <button className="ocp-close" data-slot="ocp" onClick={handleClear} title={t("org.chat.clearHistory")}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/>
                </svg>
              </button>
            )}
            {onClose && (
              <button className="ocp-close" data-slot="ocp" onClick={onClose}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      <div ref={listRef} className="ocp-messages">
        {!loaded && (
          <div className="ocp-empty">
            <span className="ocp-send-spinner" style={{ width: 20, height: 20 }} />
          </div>
        )}
        {loaded && messages.length === 0 && (
          <div className="ocp-empty">
            <div className="ocp-empty-icon">
              {nodeId ? (
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
              ) : (
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
                  <path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/>
                </svg>
              )}
            </div>
            <div className="ocp-empty-text">
              {nodeId ? t("org.chat.nodeEmptyHint") : t("org.chat.orgEmptyHint")}
            </div>
            <div className="ocp-empty-hint">{t("org.chat.inputTip")}</div>
          </div>
        )}
        {runtime !== "v2" ? (
          <>
            {messages.map(m => {
              if (m.kind === "final_report") return null;
              return renderMsgBubble(m);
            })}
            {messages.filter(m => m.kind === "final_report").map(m => renderMsgBubble(m))}
          </>
        ) : (
          // test17 Task3 命令中心多轮历史：不再是 [全部用户指令]→[仅最新时间线]
          // →[全部汇报]，而是按【每条命令一段】渲染：用户指令 → 编排过程(历史命令
          // 自动折叠) → 根节点总结气泡+附件。新命令追加、取消也保留，刷新后由
          // reload 路径重建同样的分组（user-cmd 气泡 / final-report 气泡 / ledger
          // 事件都带 commandId）。无 commandId 的零散消息（系统错误等）按时间穿插。
          renderV2Conversation()
        )}
      </div>

      {/* Non-header mode: show clear button inline */}
      {!showHeader && messages.length > 0 && (
        <div style={{ display: "flex", justifyContent: "center", padding: "2px 0", flexShrink: 0 }}>
          <button
            data-slot="ocp"
            onClick={handleClear}
            style={{
              fontSize: 10, color: "var(--muted, #64748b)", background: "none",
              border: "none", cursor: "pointer", padding: "2px 8px", opacity: 0.6,
            }}
          >
            {t("org.chat.clearConversation")}
          </button>
        </div>
      )}

      {canContinuePrevious && !sending && (
        <div style={{ display: "flex", justifyContent: "flex-end", padding: "0 12px 8px", flexShrink: 0 }}>
          <button
            data-slot="ocp"
            type="button"
            className="ocp-close"
            onClick={handleContinuePrevious}
            title={t("org.chat.continuePreviousTitle")}
            style={{ width: "auto", padding: "4px 10px", fontSize: 12 }}
          >
            {t("org.chat.continuePrevious")}
          </button>
        </div>
      )}

      {availableForwards.length > 0 && (
        <div className="ocp-forward-row" aria-label="转发到 IM 渠道">
          <span className="ocp-forward-label">转发到 IM：</span>
          {availableForwards.map(opt => {
            const active = forwardTargets.some(t => t.id === opt.id);
            return (
              <button
                key={opt.id}
                type="button"
                className={`ocp-forward-chip${active ? " ocp-forward-chip-on" : ""}`}
                onClick={() => {
                  setForwardTargets(prev => active
                    ? prev.filter(t => t.id !== opt.id)
                    : [...prev, opt]
                  );
                }}
                title={`${opt.channel}/${opt.chat_id}`}
              >
                <span className="ocp-forward-dot" />
                {opt.label}
              </button>
            );
          })}
          {forwardTargets.length > 0 && (
            <button
              type="button"
              className="ocp-forward-clear"
              onClick={() => setForwardTargets([])}
              title="清空已选 IM 渠道"
            >
              清空
            </button>
          )}
        </div>
      )}

      {pendingFiles.length > 0 && (
        <div className="ocp-pending-files">
          {pendingFiles.map(f => (
            <span
              key={f._uploadId}
              className={`ocp-pending-chip ocp-pending-${f.uploadStatus}`}
              title={f.name}
            >
              <span className="ocp-pending-name">{f.name}</span>
              {f.uploadStatus === "uploading" && <span className="ocp-pending-spinner" />}
              {f.uploadStatus === "failed" && <span className="ocp-pending-err">!</span>}
              <button
                type="button"
                className="ocp-pending-remove"
                onClick={() => removePendingFile(f._uploadId)}
                aria-label="移除附件"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className={`ocp-input-area ${compact ? "ocp-compact" : ""}`}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,video/*,audio/*,.pdf,.txt,.md,.py,.js,.ts,.json,.csv,.docx,.xlsx,.pptx"
          style={{ display: "none" }}
          onChange={handleFileSelect}
        />
        <button
          data-slot="ocp"
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="ocp-attach"
          title={t("org.chat.attachFile", "添加附件")}
          aria-label={t("org.chat.attachFile", "添加附件")}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
        </button>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={nodeId ? t("org.chat.nodeInputPlaceholder") : t("org.chat.orgInputPlaceholder")}
          rows={1}
          className="ocp-textarea"
        />
        <button
          data-slot="ocp"
          type="button"
          onClick={handleStop}
          disabled={!pendingCmdId}
          className="ocp-stop"
          title={pendingCmdId ? t("org.chat.forceStopTitle") : t("org.chat.noRunningTask")}
          aria-label={t("org.chat.forceStopTitle")}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        </button>
        <button
          data-slot="ocp"
          onClick={() => handleSend()}
          disabled={sending || !input.trim()}
          className={`ocp-send ${sending ? "ocp-send-busy" : ""}`}
        >
          {sending ? (
            <span className="ocp-send-spinner" />
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          )}
        </button>
      </div>

      {conflictDialog ? (
        <AlertDialog
          open={!!conflictDialog}
          onOpenChange={(open) => {
            if (resolvingConflict) return;
            if (!open) setConflictDialog(null);
          }}
        >
          <AlertDialogContent className="sm:max-w-[520px]">
            <AlertDialogHeader>
              <AlertDialogTitle className="flex items-center gap-2">
                <span className="grid size-8 place-items-center rounded-lg border border-amber-500/20 bg-amber-500/10 text-amber-600">
                  <ShieldAlert size={16} />
                </span>
                {t("org.chat.conflictTitle", "组织上已有命令在执行")}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {conflictDialog.message}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
              <AlertDialogCancel disabled={resolvingConflict}>
                {t("org.chat.conflictCancel", "放弃此次提交")}
              </AlertDialogCancel>
              <Button
                type="button"
                variant="outline"
                disabled={resolvingConflict}
                onClick={() => {
                  if (!conflictDialog) return;
                  const { pendingText } = conflictDialog;
                  setResolvingConflict(true);
                  setConflictDialog(null);
                  setTimeout(() => {
                    setResolvingConflict(false);
                    void handleSend({
                      text: pendingText,
                      continuePrevious: true,
                    });
                  }, 0);
                }}
              >
                {resolvingConflict && <Loader2 className="mr-2 size-4 animate-spin" />}
                {t("org.chat.conflictContinue", "继续上一次")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={resolvingConflict}
                onClick={() => {
                  if (!conflictDialog) return;
                  const { pendingText } = conflictDialog;
                  setResolvingConflict(true);
                  setConflictDialog(null);
                  setTimeout(() => {
                    setResolvingConflict(false);
                    void handleSend({
                      text: pendingText,
                      replaceExisting: true,
                    });
                  }, 0);
                }}
              >
                {resolvingConflict && <Loader2 className="mr-2 size-4 animate-spin" />}
                {t("org.chat.conflictReplace", "取消旧任务并重新提交")}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : null}

      <AlertDialog
        open={stopDialogOpen}
        onOpenChange={(open) => {
          if (stopping) return;
          setStopDialogOpen(open);
        }}
      >
        <AlertDialogContent className="sm:max-w-[460px]">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <span className="grid size-8 place-items-center rounded-lg border border-red-500/20 bg-red-500/10 text-red-600">
                <ShieldAlert size={16} />
              </span>
              {t("org.chat.forceStopTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("org.chat.confirmForceStop")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={stopping}>
              {t("common.cancel", "取消")}
            </AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              disabled={stopping}
              onClick={() => void confirmStop()}
            >
              {stopping && <Loader2 className="mr-2 size-4 animate-spin" />}
              {t("org.chat.forceStopConfirm", "强制终止")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <style>{CHAT_CSS}</style>
    </div>
  );
}

const CHAT_CSS = `
.ocp-root {
  display: flex; flex-direction: column; height: 100%; overflow: hidden;
  background: var(--bg-app); color: var(--text);
}

/* ─── Header ─── */
.ocp-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line, rgba(51,65,85,0.5));
  background: var(--bg-subtle, rgba(15,23,42,0.6));
  backdrop-filter: blur(8px);
  flex-shrink: 0;
}
.ocp-header-info { display: flex; align-items: center; gap: 8px; }
.ocp-header-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
  box-shadow: 0 0 8px #22c55e80;
  animation: ocp-pulse 2s ease-in-out infinite;
}
@keyframes ocp-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
.ocp-header-title { font-size: 13px; font-weight: 600; }
.ocp-header-titles { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ocp-header-id {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 9px;
  padding: 1px 6px;
  border-radius: 10px;
  border: 1px dashed var(--border, rgba(99,102,241,0.35));
  background: transparent;
  color: var(--muted, #64748b);
  cursor: pointer;
  width: fit-content;
  max-width: 260px;
  user-select: none;
  transition: all 0.15s;
}
.ocp-header-id:hover {
  background: var(--hover-bg, rgba(99,102,241,0.08));
  color: var(--primary, #6366f1);
  border-color: var(--primary, #6366f1);
}
.ocp-header-id-label { font-weight: 600; letter-spacing: 0.05em; opacity: 0.75; }
.ocp-header-id-value {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  max-width: 200px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ocp-close {
  width: 28px; height: 28px; border: none; border-radius: 6px;
  background: transparent; color: var(--muted, #64748b);
  cursor: pointer; font-size: 14px; display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.ocp-close:hover { background: rgba(239,68,68,0.1); color: #ef4444 !important; -webkit-text-fill-color: #ef4444 !important; }
.ocp-close:hover svg { stroke: #ef4444 !important; }

/* ─── v2 live-process feed (in-conversation) ─── */
/* Now lives INSIDE the message scroll column as the conversation's live tail,
   so everything scrolls as one (the old detached 168px strip was the "无法滚动"
   + "割裂" root cause). Styled to read as a connected process timeline. */
.ocp-process {
  align-self: stretch;
  margin: 4px 0 2px;
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--ocp-process-bg, rgba(99,102,241,0.06));
  border: 1px solid rgba(99,102,241,0.18);
}
.ocp-process-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; font-weight: 600; letter-spacing: .02em;
  color: var(--muted, #64748b); margin-bottom: 8px;
}
/* test17 Task3: one command == one block (用户指令 → 编排过程 → 总结). */
.ocp-cmd-block {
  display: flex; flex-direction: column; align-self: stretch; gap: 2px;
}
/* Historical commands' process folds away (still one click to reopen), so a
   multi-round command center no longer looks like a stack of bare 用户指令. */
.ocp-process-collapsed { padding: 6px 12px; }
.ocp-process-collapsed > summary {
  cursor: pointer; margin-bottom: 0; list-style: none; user-select: none;
}
.ocp-process-collapsed > summary::-webkit-details-marker { display: none; }
.ocp-process-collapsed > summary::before { content: "▸ "; color: #6366f1; }
.ocp-process-collapsed[open] > summary { margin-bottom: 8px; }
.ocp-process-collapsed[open] > summary::before { content: "▾ "; }
.ocp-process-spark {
  width: 6px; height: 6px; border-radius: 50%;
  background: #6366f1; box-shadow: 0 0 0 0 rgba(99,102,241,.5);
  animation: ocpProcessSpark 1.8s ease-out infinite;
}
@keyframes ocpProcessSpark {
  0% { box-shadow: 0 0 0 0 rgba(99,102,241,.45); }
  70% { box-shadow: 0 0 0 6px rgba(99,102,241,0); }
  100% { box-shadow: 0 0 0 0 rgba(99,102,241,0); }
}
.plt-feed { display: flex; flex-direction: column; gap: 2px; }
.plt-seg { display: flex; gap: 8px; }
.plt-rail {
  position: relative; flex: 0 0 12px; display: flex; justify-content: center;
}
/* connecting vertical line through the rail */
.plt-rail::before {
  content: ""; position: absolute; top: 0; bottom: 0; left: 50%;
  width: 1px; transform: translateX(-50%);
  background: rgba(100,116,139,0.25);
}
.plt-feed .plt-seg:first-child .plt-rail::before { top: 9px; }
.plt-feed .plt-seg:last-child .plt-rail::before { bottom: auto; height: 9px; }
.plt-dot {
  position: relative; z-index: 1; margin-top: 5px;
  width: 8px; height: 8px; border-radius: 50%; background: #94a3b8;
}
.plt-dot-running { background: #6366f1; }
.plt-dot-done { background: #22c55e; }
.plt-dot-loop { background: #f59e0b; }
.plt-dot-stall { background: #94a3b8; }
.plt-dot-pulse { animation: pltPulse 1.4s ease-out infinite; }
@keyframes pltPulse {
  0% { box-shadow: 0 0 0 0 rgba(99,102,241,.55); }
  70% { box-shadow: 0 0 0 7px rgba(99,102,241,0); }
  100% { box-shadow: 0 0 0 0 rgba(99,102,241,0); }
}
.plt-body { flex: 1; min-width: 0; padding-bottom: 6px; }
.plt-head {
  display: flex; align-items: center; gap: 8px; width: 100%;
  background: none; border: none; padding: 2px 0; cursor: pointer; text-align: left;
}
.plt-node { font-size: 12px; font-weight: 600; color: var(--fg, #e2e8f0); }
:root[data-theme="light"] .plt-node { color: #0f172a; }
.plt-rounds {
  font-size: 10px; padding: 1px 6px; border-radius: 999px; font-weight: 600;
  white-space: nowrap; background: rgba(99,102,241,.12); color: #a5b4fc;
}
:root[data-theme="light"] .plt-rounds { background: rgba(99,102,241,.12); color: #4f46e5; }
.plt-pill {
  font-size: 10px; padding: 1px 7px; border-radius: 999px; font-weight: 600;
  white-space: nowrap;
}
.plt-pill-running { background: rgba(99,102,241,.16); color: #818cf8; }
.plt-pill-done { background: rgba(34,197,94,.16); color: #4ade80; }
.plt-pill-loop { background: rgba(245,158,11,.16); color: #fbbf24; }
.plt-pill-stall { background: rgba(148,163,184,.16); color: #94a3b8; }
.plt-time { font-size: 10px; color: var(--muted, #64748b); margin-left: auto; }
.plt-caret { font-size: 10px; color: var(--muted, #64748b); }
.plt-lines { margin-top: 3px; display: flex; flex-direction: column; gap: 3px; }
.plt-line {
  font-size: 12px; line-height: 1.55; color: var(--fg, #cbd5e1);
  white-space: pre-wrap; word-break: break-word;
  border-left: 2px solid rgba(99,102,241,0.3); padding-left: 8px;
}
:root[data-theme="light"] .plt-line { color: #334155; }
.plt-summary {
  margin-top: 1px; font-size: 12px; color: var(--muted, #94a3b8);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* ─── Messages ─── */
.ocp-messages {
  flex: 1; min-height: 0; overflow-y: auto; padding: 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.ocp-messages::-webkit-scrollbar { width: 4px; }
.ocp-messages::-webkit-scrollbar-thumb { background: rgba(51,65,85,0.5); border-radius: 2px; }

.ocp-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  flex: 1; gap: 8px; text-align: center; padding: 32px 16px;
}
.ocp-empty-icon { display: flex; align-items: center; justify-content: center; color: var(--muted, #64748b); }
.ocp-empty-text { font-size: 13px; color: var(--muted, #64748b); max-width: 220px; line-height: 1.5; }
.ocp-empty-hint { font-size: 11px; color: var(--muted, #475569); opacity: 0.5; }

.ocp-msg { display: flex; }
.ocp-msg-user { justify-content: flex-end; }
.ocp-msg-assistant, .ocp-msg-system { justify-content: flex-start; }

.ocp-msg-bubble {
  max-width: 85%; padding: 10px 14px; border-radius: 12px;
  font-size: 13px; line-height: 1.6; word-break: break-word;
}
.ocp-msg-user .ocp-msg-bubble {
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  color: #fff; border-bottom-right-radius: 4px;
  white-space: pre-wrap;
}
.ocp-msg-assistant .ocp-msg-bubble {
  background: var(--bg-subtle, rgba(30,41,59,0.8));
  border: 1px solid var(--line, rgba(100,116,139,0.2));
  color: var(--text);
  border-bottom-left-radius: 4px;
}
.ocp-msg-system .ocp-msg-bubble {
  background: rgba(239,68,68,0.08);
  border: 1px solid rgba(239,68,68,0.2);
  color: #fca5a5;
  border-bottom-left-radius: 4px;
}
/* P11: activity bubble（组织事件聚合）走中性色而非红色——红色只留给真正
   的错误/警告通知。class 同时叠加到 role="system" 上，所以放在 system 之
   后才能覆盖；视觉上与 assistant 接近，但用 surface 标签和"活动事件"小
   chip 在 bubble 头部点明这是事件流。 */
.ocp-msg-activity .ocp-msg-bubble {
  background: var(--bg-subtle, rgba(30,41,59,0.55));
  border: 1px solid var(--line, rgba(100,116,139,0.25));
  color: var(--text);
  border-bottom-left-radius: 4px;
}
.ocp-msg-activity .ocp-msg-bubble.chatMdContent code {
  font-size: 11px;
  padding: 0 4px;
  background: var(--bg-app, rgba(0,0,0,0.25));
  border-radius: 3px;
  color: var(--muted-foreground, #94a3b8);
}
.ocp-msg-activity .ocp-msg-bubble.chatMdContent blockquote {
  margin: 4px 0; padding: 4px 10px;
  border-left: 2px solid var(--line, rgba(100,116,139,0.35));
  color: var(--muted-foreground, #94a3b8);
  background: transparent;
}
.ocp-msg-activity .ocp-msg-bubble.chatMdContent p { margin: 4px 0; }
.ocp-msg-streaming .ocp-msg-bubble {
  border-color: rgba(99,102,241,0.3);
}
.ocp-msg-bubble.chatMdContent { font-size: 13px; line-height: 1.6; }
.ocp-msg-bubble.chatMdContent > :first-child { margin-top: 0; }
.ocp-msg-bubble.chatMdContent > :last-child { margin-bottom: 0; }
.ocp-msg-bubble.chatMdContent details {
  margin-bottom: 8px; border: 1px solid var(--line, rgba(100,116,139,0.25));
  border-radius: 8px; overflow: hidden;
}
.ocp-msg-bubble.chatMdContent details summary {
  cursor: pointer; padding: 6px 10px; font-size: 12px; font-weight: 500;
  color: var(--muted-foreground, #94a3b8);
  background: var(--bg-subtle, rgba(30,41,59,0.5));
  user-select: none; list-style: none;
}
.ocp-msg-bubble.chatMdContent details summary::before {
  content: "▸ "; transition: transform 0.2s;
}
.ocp-msg-bubble.chatMdContent details[open] summary::before { content: "▾ "; }
.ocp-msg-bubble.chatMdContent details > :not(summary) {
  padding: 8px 10px; font-size: 12px; line-height: 1.7;
}
.ocp-typing {
  display: inline-block; margin-left: 4px; color: #818cf8;
  animation: ocp-typing-blink 1.2s ease-in-out infinite;
}
@keyframes ocp-typing-blink { 0%,100% { opacity: 1; } 50% { opacity: 0.2; } }

.ocp-done-banner {
  margin-top: 12px; padding: 8px 12px; border-radius: 8px;
  background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.25);
  color: #22c55e; font-size: 13px; font-weight: 500; text-align: center;
}
.ocp-done-banner.ocp-done-banner-warn {
  background: rgba(234,179,8,0.12); border-color: rgba(234,179,8,0.35);
  color: #eab308;
}

/* ─── Input ─── */
/* ─── Forward-to-IM chip row (P3) ─── */
.ocp-forward-row {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  padding: 6px 12px 0 12px;
  font-size: 11px; color: var(--muted, #64748b);
  border-top: 1px dashed var(--line, rgba(51,65,85,0.4));
}
.ocp-forward-label { font-weight: 600; letter-spacing: 0.04em; }
.ocp-forward-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--line, rgba(99,102,241,0.35));
  background: transparent; color: var(--muted, #64748b);
  cursor: pointer; font-size: 11px;
  transition: all 0.15s;
}
.ocp-forward-chip:hover { color: var(--text); border-color: var(--primary, #6366f1); }
.ocp-forward-chip-on {
  background: var(--primary, #6366f1); color: white;
  border-color: var(--primary, #6366f1);
  box-shadow: 0 0 8px rgba(99,102,241,0.4);
}
.ocp-forward-chip-on .ocp-forward-dot { background: white; box-shadow: 0 0 4px white; }
.ocp-forward-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--muted, #64748b);
}
.ocp-forward-clear {
  margin-left: auto;
  padding: 2px 8px; border-radius: 6px;
  border: 1px solid transparent;
  background: transparent; color: var(--muted, #64748b);
  cursor: pointer; font-size: 11px;
}
.ocp-forward-clear:hover { color: #ef4444; border-color: rgba(239,68,68,0.3); }

.ocp-input-area {
  padding: 10px 12px;
  border-top: 1px solid var(--line, rgba(51,65,85,0.5));
  display: flex; gap: 8px; align-items: flex-end;
  background: var(--bg-app);
  flex-shrink: 0;
}
.ocp-compact { padding: 8px 10px; }

/* 上游 e2874585: 待发送输入附件预览条 + 附件按钮 */
.ocp-pending-files {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 8px 12px 0;
  background: var(--bg-app);
}
.ocp-pending-chip {
  display: inline-flex; align-items: center; gap: 6px;
  max-width: 220px;
  padding: 4px 8px; border-radius: 8px;
  border: 1px solid var(--line, rgba(100,116,139,0.3));
  background: var(--bg-subtle, rgba(100,116,139,0.08));
  font-size: 12px; color: var(--text);
}
.ocp-pending-name {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ocp-pending-failed { border-color: rgba(239,68,68,0.55); color: #ef4444; }
.ocp-pending-err { color: #ef4444; font-weight: 700; }
.ocp-pending-spinner {
  width: 11px; height: 11px; border: 2px solid rgba(99,102,241,0.3);
  border-top-color: #6366f1; border-radius: 50%;
  animation: ocp-spin 0.6s linear infinite; flex-shrink: 0;
}
.ocp-pending-remove {
  border: none; background: transparent; cursor: pointer;
  color: var(--muted, #64748b); font-size: 15px; line-height: 1;
  padding: 0 2px; flex-shrink: 0;
}
.ocp-pending-remove:hover { color: #ef4444; }
.ocp-attach {
  width: 36px; height: 36px; border-radius: 10px; flex-shrink: 0;
  border: 1px solid var(--line, rgba(100,116,139,0.3));
  background: transparent; color: var(--muted, #64748b);
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.ocp-attach:hover {
  background: rgba(99,102,241,0.12);
  border-color: rgba(99,102,241,0.55);
  color: #6366f1;
}

.ocp-textarea {
  flex: 1; resize: none; border: 1px solid var(--line, rgba(100,116,139,0.2));
  border-radius: 10px; padding: 10px 14px;
  font-size: 13px; font-family: inherit; line-height: 1.5;
  background: var(--bg-app);
  color: var(--text);
  outline: none; max-height: 100px; overflow-y: auto;
  transition: border-color 0.2s;
}
.ocp-textarea:focus { border-color: #6366f1; box-shadow: 0 0 0 2px rgba(99,102,241,0.15); }
.ocp-textarea::placeholder { color: var(--muted, #64748b); }

.ocp-send {
  width: 40px; height: 40px; border: none; border-radius: 10px;
  background: linear-gradient(135deg, #3b82f6, #6366f1) !important;
  color: #ffffff !important; -webkit-text-fill-color: #ffffff !important;
  cursor: pointer; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s; box-shadow: 0 2px 8px rgba(99,102,241,0.3);
}
.ocp-send svg { stroke: #ffffff !important; }
.ocp-send:hover:not(:disabled) {
  transform: translateY(-1px);
  background: linear-gradient(135deg, #2563eb, #4f46e5) !important;
  color: #ffffff !important; -webkit-text-fill-color: #ffffff !important;
  box-shadow: 0 4px 12px rgba(99,102,241,0.5);
}
.ocp-send:disabled { opacity: 0.4; cursor: not-allowed; box-shadow: none; }
.ocp-send-busy { background: linear-gradient(135deg, #f59e0b, #f97316) !important; }

/* 强制终止当前任务按键：常驻输入区，未运行时灰显 */
.ocp-stop {
  width: 36px; height: 36px; border-radius: 10px; flex-shrink: 0;
  border: 1px solid var(--line, rgba(100,116,139,0.3));
  background: transparent;
  color: var(--muted, #64748b);
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.ocp-stop svg { fill: currentColor; }
.ocp-stop:not(:disabled):hover {
  background: rgba(239,68,68,0.12);
  border-color: rgba(239,68,68,0.55);
  color: #ef4444;
}
.ocp-stop:disabled { opacity: 0.35; cursor: not-allowed; }

.ocp-send-spinner {
  width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff; border-radius: 50%;
  animation: ocp-spin 0.6s linear infinite;
}
@keyframes ocp-spin { to { transform: rotate(360deg); } }
`;
