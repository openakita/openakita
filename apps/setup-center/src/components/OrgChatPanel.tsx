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
   * P11: 内容种类的细粒度标记，用于让样式（bubble 颜色 / class 名）跟"语义"
   * 解耦。例如 role="system" 同时被用于真正的错误通知（红色合理）和
   * IM/桌面/指挥台事件流（应当中性、不要红）。kind="activity" 即一组组织
   * 事件聚合后渲染出的活动时间线 bubble，CSS 用 `.ocp-msg-activity` 给中性
   * 颜色覆盖。
   */
  kind?: "activity";
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
}
const _pendingCmds = new Map<string, PendingCmd>();

const SOFT_ORG_EXIT_REASONS = new Set(["normal", "ask_user", "waiting_user", "verify_incomplete"]);

function isSoftOrgExitReason(reason?: string): boolean {
  return !reason || SOFT_ORG_EXIT_REASONS.has(reason);
}

// ─────────────────────────────────────────────────────────────────────────────
// P11: 组织活动时间线（/api/orgs/{org}/activity）的中性渲染器。
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
}

function activitySourceLabel(item: ActivityItem): string {
  const s = item?.source?.surface;
  if (s === "im") {
    const ch = item?.source?.channel || "";
    return ch ? `IM·${ch}` : "IM";
  }
  if (s === "desktop_chat") return "桌面聊天";
  if (s === "org_console") return "指挥台";
  if (s === "org" || !s) return "组织";
  return s;
}

function activityTs(item: ActivityItem): number {
  const ts = item?.ts;
  if (typeof ts === "number") return ts * 1000;
  if (typeof ts === "string" && ts) {
    const t = Date.parse(ts);
    if (!Number.isNaN(t)) return t;
  }
  return Date.now();
}

function fmtClock(ms: number): string {
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

/** 单条活动事件渲染成一行（不含时间戳前缀；时间戳由 group 渲染器统一加）。 */
function formatActivityLine(item: ActivityItem, opts?: { nameFmt?: (id: string) => string }): string {
  const nameFmt = opts?.nameFmt || ((id: string) => id);
  const from = item.from_node ? nameFmt(item.from_node) : "";
  const to = item.to_node ? nameFmt(item.to_node) : "";
  const flowArrow = to ? `${from || "?"} → ${to}` : (from || "?");
  // content 已经在 backend 端被 _activity_preview 截到 240 字符
  const c = (item.content || "").trim();
  const inlineContent = c ? c.replace(/\s+/g, " ").slice(0, 200) : "";
  const summary = inlineContent ? `：${inlineContent}` : "";
  // tool_name 在 backend 里被合到 content；这里就不再二次解析。
  switch (item.kind) {
    case "user_command":
      return `🎯 **用户指令**${summary}`;
    case "user_command_cancelled":
      return `⏹ 用户取消指令`;
    case "command":
      return `📡 命令登记：${item.status || ""}${item.phase ? `·${item.phase}` : ""}`;
    case "command_phase":
      return `📡 ${flowArrow} 命令状态变更${summary}`;
    case "delegate":
      return `↪ ${flowArrow} 派单${summary}`;
    case "task_completed":
      return `✓ ${from || "?"} 任务完成${summary}`;
    case "task_cancelled":
      return `⏹ ${from || "?"} 任务取消${summary}`;
    case "broadcast":
      return `📢 ${from || "?"} 广播${summary}`;
    case "node_activated":
      return `🟢 ${from || "?"} 节点激活${summary}`;
    case "workbench_started":
      return `▶ ${from || "?"} 启动工具${summary}`;
    case "workbench_succeeded":
      return `✓ ${from || "?"} 工具完成${summary}`;
    case "workbench_failed":
      return `✗ ${from || "?"} 工具失败${summary}`;
    case "message":
      return `💬 ${flowArrow}${summary}`;
    default:
      return `· ${flowArrow}${item.kind ? `（${item.kind}）` : ""}${summary}`;
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
    const sourceLbl = activitySourceLabel(first);
    // command_id 在很多事件上是同一个值；以第一条带 user_command 的为锚显示
    const cmdItem = bucket.find(i => i.kind === "user_command") || first;
    const cmdSummary = (cmdItem.content || "").trim().replace(/\s+/g, " ").slice(0, 200);
    const headerBits: string[] = [`📥 来自 **${sourceLbl}**`];
    if (cmdItem.kind === "user_command" && cmdSummary) {
      headerBits.push(`· 指令：${cmdSummary}`);
    } else if (cmdItem.command_id) {
      headerBits.push(`· command_id=\`${cmdItem.command_id}\``);
    }
    const header = headerBits.join(" ");
    // 时间线内容：每条加上 hh:mm:ss 时间戳
    const lines = bucket.map(it => {
      const clock = fmtClock(activityTs(it));
      const line = formatActivityLine(it, { nameFmt });
      return `\`${clock}\` ${line}`;
    });
    // 折叠：>4 条时把"工具进度细节"折叠，把 user_command/task_completed/task_cancelled
    // 这些"门面事件"始终可见。
    const isHeadline = (it: ActivityItem) => (
      it.kind === "user_command"
      || it.kind === "user_command_cancelled"
      || it.kind === "task_completed"
      || it.kind === "task_cancelled"
      || it.kind === "command"
    );
    let body: string;
    if (bucket.length > 5) {
      const headlineLines: string[] = [];
      const detailLines: string[] = [];
      bucket.forEach((it, ix) => {
        const ln = lines[ix];
        if (isHeadline(it)) headlineLines.push(ln);
        else detailLines.push(ln);
      });
      body = [
        ...headlineLines,
        detailLines.length > 0
          ? `<details>\n<summary>展开过程细节（${detailLines.length} 条）</summary>\n\n${detailLines.join("\n\n")}\n\n</details>`
          : "",
      ].filter(Boolean).join("\n\n");
    } else {
      body = lines.join("\n\n");
    }
    msgs.push({
      id: `act-grp-${key}`,
      role: "system",
      kind: "activity",
      content: `${header}\n\n${body}`,
      timestamp: groupTs,
    });
  }
  return msgs;
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

export function OrgChatPanel({ orgId, nodeId, apiBaseUrl, compact, showHeader, title, onClose, nodeNames }: OrgChatPanelProps) {
  const { t } = useTranslation();
  const md = useMdModules();
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
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
  // 整组织视图额外合并 /api/orgs/{org}/activity（含 IM/桌面/指挥台所有来源），
  // 让 IM 来的指令、节点互发的消息也能在指挥台直接看到。
  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    const url = `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${ORG_HISTORY_PAGE_LIMIT}`;
    const wholeOrgView = !nodeId || String(nodeId).trim() === "";

    const nameFmt = (id: string) => nodeNamesRef.current?.[id] || id;
    const fetchActivityAsMsgs = async (): Promise<ChatMsg[]> => {
      if (!wholeOrgView) return [];
      try {
        const r = await safeFetch(
          `${apiBaseUrl}/api/orgs/${encodeURIComponent(orgId)}/activity?limit=${ORG_HISTORY_PAGE_LIMIT}`,
        );
        const j = await r.json();
        const arr = Array.isArray(j?.items) ? (j.items as ActivityItem[]) : [];
        return activityItemsToMessages(arr, nameFmt);
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
    const orgEvents = new Set([
      "org:command_started",
      "org:command_done",
      "org:command_cancelled",
      "org:message",
      "org:broadcast",
      "org:task_delegated",
      "org:blackboard_update",
      "org:workbench_tool_status",
    ]);
    let pendingTimer: ReturnType<typeof setTimeout> | null = null;

    const refresh = async (): Promise<void> => {
      try {
        const histPromise = safeFetch(
          `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${ORG_HISTORY_PAGE_LIMIT}`,
        ).then(r => r.json()).catch(() => ({}));
        const activityPromise = wholeOrgView
          ? safeFetch(
              `${apiBaseUrl}/api/orgs/${encodeURIComponent(orgId)}/activity?limit=${ORG_HISTORY_PAGE_LIMIT}`,
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
        if (deduped.length > 0) {
          setMessages(deduped);
          saveToLocalStorage(convId, deduped);
        }
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
          const res = await safeFetch(`${apiBaseUrl}/api/orgs/${pending.orgId}/commands/${pending.commandId}`);
          const data = await res.json();
          if (data.status === "done" || data.status === "error") {
            if (!_pendingCmds.has(convId)) break;
            _pendingCmds.delete(convId);
            const result = data.result as Record<string, unknown> | null | undefined;
            let resultText = JSON.stringify(data);
            if (result && typeof result.result === "string" && result.result.trim()) {
              resultText = result.result;
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
        `${apiBaseUrl}/api/orgs/${encodeURIComponent(orgId)}/commands/${encodeURIComponent(pendingCmdId)}/cancel`,
        { method: "POST" },
      );
    } catch (e) {
      console.warn("[OrgChat] cancel command failed", e);
    } finally {
      setStopping(false);
      setStopDialogOpen(false);
    }
  }, [apiBaseUrl, orgId, pendingCmdId]);

  const handleSend = useCallback(async (opts?: { continuePrevious?: boolean; text?: string }) => {
    const text = (opts?.text ?? input).trim();
    if (!text || sending) return;

    const userMsg: ChatMsg = { id: genId(), role: "user", content: text, timestamp: Date.now() };
    const placeholderId = genId();
    const placeholder: ChatMsg = {
      id: placeholderId, role: "assistant", content: t("org.chat.thinking"), timestamp: Date.now(), streaming: true,
    };
    setMessages(prev => [...prev, userMsg, placeholder]);
    setInput("");
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

    // P8.2: 30s 内复用 done segment，避免 wb-hh-* 节点 busy → idle → busy
    // 频繁切换时把一条任务被切成多个碎片。done 之后第一次再 busy 起来
    // 通常是上游的 fan-out 通知（如 task_accepted 后跟一条 workbench_tool
    // 重启），保留在同一 segment 里更可读。超过 SEG_REUSE_AFTER_DONE_MS
    // 还有新 busy 才认为是一段全新的工作。
    const SEG_REUSE_AFTER_DONE_MS = 30_000;

    function findOrCreateSeg(nodeId: string): TimelineSegment {
      const idx = activeSegIdx.get(nodeId);
      if (idx != null) {
        const cur = segments[idx];
        if (!cur.done) return cur;
        const sinceDone = Date.now() - (cur.doneAt ?? 0);
        if (sinceDone <= SEG_REUSE_AFTER_DONE_MS) {
          // P9.2: 复用 segment 时把上一轮的失败状态一并重置，否则
          // 节点先失败（max_iterations / timeout）再重启成功的场景下
          // segment 会一直顶着红色的 ⚠ 和默认展开，掩盖最终成功结果。
          cur.done = false;
          cur.doneAt = undefined;
          cur.failed = false;
          cur.exitReason = undefined;
          cur.diagnosis = undefined;
          cur.resultPreview = undefined;
          cur.paused = undefined;
          return cur;
        }
      }
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
      setMessages(prev => prev.map(m => m.id === placeholderId
        ? { ...m, content: rendered || t("org.chat.thinking"), attachments: streamingAtts ?? m.attachments }
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
          const task = (d.current_task || "") as string;
          if (task.startsWith(t("org.chat.notification"))) return;
          const seg = findOrCreateSeg(nid);
          if (pushSegLine(seg, `${t("org.chat.startProcessing", { name: `**${nn(nid)}**` })}${task ? `: ${task}` : ""}`)) {
            updatePreview();
          }
        } else if (st === "idle") {
          const exitReason = (d.exit_reason as string) || "normal";
          const idx = activeSegIdx.get(nid);
          if (idx != null && segments[idx]) {
            const seg = segments[idx];
            seg.done = true; seg.doneAt = Date.now();
            seg.exitReason = exitReason;
            // 软退出在用户界面按完成/等待处理；真正异常交给后续事件显示极简状态。
            if (isSoftOrgExitReason(exitReason)) {
              seg.failed = false;
              pushSegLine(seg, t("org.chat.completed", { name: `**${nn(nid)}**` }));
            } else {
              seg.failed = true;
            }
          }
          updatePreview();
        } else if (st === "error") {
          const seg = findOrCreateSeg(nid);
          seg.done = true; seg.doneAt = Date.now();
          pushSegLine(seg, t("org.chat.errored", { name: `**${nn(nid)}**` }));
          updatePreview();
        }
      } else if (event === "org:task_delegated") {
        const task = ((d.task || "") as string);
        const seg = findOrCreateSeg(nid);
        if (pushSegLine(seg, t("org.chat.taskAssigned", { from: `**${nn(nid)}**`, to: `**${nn(toN)}**`, task }))) {
          updatePreview();
        }
      } else if (event === "org:task_delivered") {
        const summary = ((d.summary || "") as string);
        const seg = findOrCreateSeg(nid);
        if (pushSegLine(seg, `${t("org.chat.delivered", { name: `**${nn(nid)}**` })}${summary ? `: ${summary}` : ""}`)) {
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
      } else if (event === "org:blackboard_update") {
        const mt = d.memory_type as string;
        const fname = (d.filename || d.name) as string | undefined;
        const fpath = (d.file_path || d.path) as string | undefined;
        const fsize = (d.file_size ?? d.size) as number | undefined;
        if (mt === "resource" && fname && fpath) {
          const seg = findOrCreateSeg(nid);
          const added = pushSegFile(seg, { filename: fname, file_path: fpath, file_size: fsize });
          if (added) {
            pushSegLine(seg, t("org.chat.fileOutput", { name: `**${nn(nid)}**`, file: fname }));
            updatePreview();
          }
        } else {
          const seg = findOrCreateSeg(nid);
          if (pushSegLine(seg, t("org.chat.blackboardUpdate", { name: `**${nn(nid)}**` }))) {
            updatePreview();
          }
        }
      } else if (event === "org:command_stuck_warning") {
        const idle = Number(d.idle_secs || 0);
        const minutes = Math.floor(idle / 60);
        const sec = idle % 60;
        const idleStr = minutes > 0 ? t("org.chat.idleMinSec", { m: minutes, s: sec }) : t("org.chat.idleSec", { s: sec });
        const seg = findOrCreateSeg("system");
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
        const seg = findOrCreateSeg(nid);
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
      if (pending) {
        if (pending.placeholderId !== placeholderId) return;
        pending.finalContent = content;
        _pendingCmds.delete(convId);
      }
      const atts = files && files.length > 0 ? files : undefined;
      if (mountedRef.current) {
        setMessages(prev => {
          const next = prev.map(m =>
            m.id === placeholderId ? { ...m, content, streaming: false, role, attachments: atts } : m
          );
          messagesRef.current = next;
          return next;
        });
      } else {
        const existing = loadFromLocalStorage(convId);
        const msg: ChatMsg = { id: placeholderId, role, content, timestamp: Date.now(), attachments: atts };
        const hasUser = existing.some(m => m.id === userMsg.id);
        const toSave = hasUser ? [...existing, msg] : [...existing, userMsg, msg];
        saveToLocalStorage(convId, toSave);
        persistToBackend(apiBaseUrl, convId, toSave.map(m => ({ role: m.role, content: m.content })), true);
      }
    };

    const wrapWithProcess = (
      resultText: string,
      opts?: { stoppedByWatchdog?: boolean; warning?: string }
    ): string => {
      const stopped = !!opts?.stoppedByWatchdog;
      const banner = stopped
        ? `\n\n<div class="ocp-done-banner ocp-done-banner-warn">&#x26A0;&#xFE0F; ${t("org.chat.orgAutoPaused")}</div>`
        : `\n\n<div class="ocp-done-banner">&#x2705; ${t("org.chat.taskCompleted")}</div>`;
      const warningLine = opts?.warning
        ? `\n\n> ${opts.warning}`
        : "";
      if (segments.length === 0) return resultText + warningLine + banner;
      const allCollapsed = segments.map(seg => {
        const body = seg.lines.join("\n\n");
        return `<details>\n<summary>✓ ${seg.nodeName}</summary>\n\n${body}\n\n</details>`;
      }).join("\n\n");
      return `${allCollapsed}\n\n---\n\n${resultText}${warningLine}${banner}`;
    };

    const getCommandResultText = (
      result: Record<string, unknown> | null | undefined,
      error: unknown,
      fallback: unknown,
    ): string => {
      if (result && typeof result.result === "string" && result.result.trim()) return result.result;
      if (result && typeof result.error === "string" && result.error.trim()) return result.error;
      if (typeof error === "string" && error.trim()) return error;
      return JSON.stringify(fallback);
    };

    let finalContent = "";
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          target_node_id: nodeId || undefined,
          continue_previous: !!opts?.continuePrevious,
          forward_to: forwardTargets.map(ft => ({
            channel: ft.channel,
            chat_id: ft.chat_id,
            thread_id: ft.thread_id ?? null,
            bot_instance_id: ft.bot_instance_id ?? "",
            label: ft.label,
          })),
        }),
      });
      const data = await res.json();
      const commandId = data.command_id as string | undefined;

      if (!commandId) {
        finalContent = data.result || data.error || JSON.stringify(data);
        finalizeResult(finalContent);
      } else {
        _pendingCmds.set(convId, { commandId, orgId, placeholderId, lastRendered: "", segmentCount: 0, allFiles: [], finalContent: null });
        setPendingCmdId(commandId);

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
          const warning = result && typeof result.warning === "string" ? result.warning as string : undefined;
          setTimeout(() => {
            finalContent = wrapWithProcess(resultText, { stoppedByWatchdog: stopped, warning });
            finalizeResult(finalContent, collectAllFiles());
            if (stopped || cancelled) setCanContinuePrevious(true);
          }, 500);
        });

        while (!resolved) {
          await new Promise(r => setTimeout(r, 5000));
          if (resolved) break;
          try {
            const poll = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/commands/${commandId}`);
            const pd = await poll.json();
            if (pd.status === "running" && typeof pd.blocker_summary === "string" && pd.blocker_summary.trim()) {
              const blockerSummary = pd.blocker_summary.trim();
              const seg = findOrCreateSeg("system");
              const line = t("org.chat.commandBlocker", { reason: blockerSummary });
              if (blockerSummary !== lastBlockerSummary && pushSegLine(seg, line)) {
                lastBlockerSummary = blockerSummary;
                updatePreview();
              }
            }
            if (pd.status === "done" || pd.status === "error") {
              if (!resolved) {
                resolved = true;
                const resultText = getCommandResultText(pd.result, pd.error, pd);
                const stopped = !!(pd.result && pd.result.stopped_by_watchdog);
                const cancelled = !!(pd.result && pd.result.cancelled_by_user);
                const warning = pd.result && typeof pd.result.warning === "string" ? pd.result.warning : undefined;
                finalContent = wrapWithProcess(resultText, { stoppedByWatchdog: stopped, warning });
                finalizeResult(finalContent, collectAllFiles());
                if (stopped || cancelled) setCanContinuePrevious(true);
              }
            }
          } catch { /* retry */ }
          if (!resolved && Date.now() - activity.last > 60000) {
            const elapsed = Math.round((Date.now() - cmdStartTime) / 1000);
            const min = Math.floor(elapsed / 60);
            const sec = elapsed % 60;
            const timeStr = min > 0 ? t("org.chat.idleMinSec", { m: min, s: sec }) : t("org.chat.idleSec", { s: sec });
            const seg = findOrCreateSeg("system");
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
  }, [input, sending, orgId, nodeId, apiBaseUrl, convId, persistToBackend, forwardTargets]);

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
        {messages.map(m => (
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
                m.content
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
        ))}
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

      <div className={`ocp-input-area ${compact ? "ocp-compact" : ""}`}>
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

/* ─── Messages ─── */
.ocp-messages {
  flex: 1; overflow-y: auto; padding: 12px;
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
