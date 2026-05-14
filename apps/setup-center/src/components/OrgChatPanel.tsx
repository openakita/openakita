/**
 * Reusable chat panel — organization or node level.
 * Renders a scrollable message list, input box, and real-time WS progress.
 * Messages are persisted to backend session API (same as main ChatView).
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { onWsEvent } from "../platform";
import { useMdModules } from "../views/chat/hooks/useMdModules";
import { FileAttachmentCard } from "./FileAttachmentCard";
import type { FileAttachment } from "./FileAttachmentCard";

interface ChatMsg {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  streaming?: boolean;
  attachments?: FileAttachment[];
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

function saveToLocalStorage(cid: string, msgs: ChatMsg[]): void {
  try {
    const windowed = msgs.length > ORG_STORED_MESSAGE_WINDOW
      ? msgs.slice(-ORG_STORED_MESSAGE_WINDOW)
      : msgs;
    const slim = windowed
      .filter(m => !m.streaming)
      .map(({ id, role, content, timestamp, attachments }) => {
        const o: Record<string, unknown> = { id, role, content, timestamp };
        if (attachments && attachments.length > 0) o.attachments = attachments;
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

  // Load history: backend first, localStorage fallback
  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    const url = `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${ORG_HISTORY_PAGE_LIMIT}`;
    (async () => {
      try {
        const res = await safeFetch(url);
        const data = await res.json();
        if (cancelled) return;
        const msgs: ChatMsg[] = (data.messages || []).map((m: any) => ({
          id: m.id || genId(),
          role: m.role || "assistant",
          content: m.content || "",
          timestamp: m.timestamp || Date.now(),
        }));
        if (msgs.length > 0) {
          console.log(`[OrgChat] Loaded ${msgs.length} messages from backend for ${convId}`);
          setMessages(msgs);
          saveToLocalStorage(convId, msgs);
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
  const handleStop = useCallback(async () => {
    if (!pendingCmdId) return;
    const ok = window.confirm(t("org.chat.confirmForceStop"));
    if (!ok) return;
    try {
      await safeFetch(
        `${apiBaseUrl}/api/orgs/${encodeURIComponent(orgId)}/commands/${encodeURIComponent(pendingCmdId)}/cancel`,
        { method: "POST" },
      );
    } catch (e) {
      console.warn("[OrgChat] cancel command failed", e);
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
    const cmdStartTime = Date.now();
    const activity = { last: Date.now() };

    function findOrCreateSeg(nodeId: string): TimelineSegment {
      let idx = activeSegIdx.get(nodeId);
      if (idx != null && !segments[idx].done) return segments[idx];
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
      setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: rendered || t("org.chat.thinking") } : m));
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
            seg.done = true;
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
          seg.done = true;
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
        const idx = activeSegIdx.get(nid);
        if (idx != null && segments[idx]) {
          segments[idx].resultPreview = preview;
          segments[idx].exitReason = (d.exit_reason as string) || "normal";
        }
      } else if (event === "org:task_terminated") {
        const preview = ((d.result_preview || "") as string);
        const reason = (d.exit_reason as string) || "loop_terminated";
        const diagnosis = (d.diagnosis as FailureDiagnosis | undefined) || undefined;
        const idx = activeSegIdx.get(nid);
        if (idx != null && segments[idx]) {
          const seg = segments[idx];
          seg.done = true;
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
          seg.done = true;
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
        const fname = d.filename as string | undefined;
        const fpath = d.file_path as string | undefined;
        const fsize = d.file_size as number | undefined;
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
              const seg = findOrCreateSeg("system");
              if (pushSegLine(seg, t("org.chat.commandBlocker", { reason: pd.blocker_summary }))) {
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
  }, [input, sending, orgId, nodeId, apiBaseUrl, convId, persistToBackend]);

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
            <span className="ocp-header-title">{title || (nodeId ? t("org.chat.conversationTitle", { name: nodeId }) : t("org.chat.commandCenter"))}</span>
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
          <div key={m.id} className={`ocp-msg ocp-msg-${m.role} ${m.streaming ? "ocp-msg-streaming" : ""}`}>
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
              {!m.streaming && m.attachments && m.attachments.length > 0 && (
                <div style={{ borderTop: "1px solid rgba(100,116,139,0.2)", marginTop: 10, paddingTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                  {m.attachments.map((f, i) => (
                    <FileAttachmentCard key={f.file_path || i} file={f} apiBaseUrl={apiBaseUrl} />
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
