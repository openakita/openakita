/**
 * Reusable chat panel — organization or node level.
 * Renders a scrollable message list, input box, and real-time WS progress.
 * Messages are persisted to backend session API (same as main ChatView).
 */
import { useState, useRef, useEffect, useCallback } from "react";
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
  resultPreview?: string;
  /**
   * 节点退出原因：
   * - undefined/"normal"/"ask_user": 正常完成
   * - "loop_terminated": Supervisor 强制终止死循环
   * - "max_iterations": 达到最大迭代次数
   * - "verify_incomplete": 任务验证未通过
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

function saveToLocalStorage(cid: string, msgs: ChatMsg[]): void {
  try {
    const slim = msgs
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
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

export function OrgChatPanel({ orgId, nodeId, apiBaseUrl, compact, showHeader, title, onClose, nodeNames }: OrgChatPanelProps) {
  const md = useMdModules();
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loaded, setLoaded] = useState(false);
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
    const url = `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`;
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
    const progress = pending.lastRendered || "思考中...";

    setMessages(prev => {
      if (prev.some(m => m.streaming)) return prev;
      return [...prev, { id: phId, role: "assistant" as const, content: progress, timestamp: Date.now(), streaming: true }];
    });
    setSending(true);

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
            const resultText = data.result?.result || data.result?.error || data.error || JSON.stringify(data);
            const steps = pending.lastRendered;
            const content = steps
              ? `<details>\n<summary>执行过程（${pending.segmentCount} 步）</summary>\n\n${steps}\n\n</details>\n\n${resultText}`
              : resultText;
            if (mountedRef.current) {
              setMessages(prev => prev.map(m => m.id === phId ? { ...m, content, streaming: false, attachments: pending.allFiles.length > 0 ? pending.allFiles : undefined } : m));
              setSending(false);
            }
            return;
          }
        } catch { /* poll retry */ }
      }
      if (!cancelled && mountedRef.current && !_pendingCmds.has(convId)) {
        const saved = loadFromLocalStorage(convId);
        if (saved.length > 0) setMessages(saved);
        setSending(false);
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
    try { localStorage.removeItem(LS_PREFIX + convId); } catch {}
    try {
      await safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}`, {
        method: "DELETE",
      });
    } catch {}
  }, [apiBaseUrl, convId]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: ChatMsg = { id: genId(), role: "user", content: text, timestamp: Date.now() };
    const placeholderId = genId();
    const placeholder: ChatMsg = {
      id: placeholderId, role: "assistant", content: "思考中...", timestamp: Date.now(), streaming: true,
    };
    setMessages(prev => [...prev, userMsg, placeholder]);
    setInput("");
    setSending(true);

    const nn = (id: string) => nodeNamesRef.current?.[id] || id;

    const segments: TimelineSegment[] = [];
    const activeSegIdx = new Map<string, number>();
    const cmdStartTime = Date.now();
    const activity = { last: Date.now() };

    function findOrCreateSeg(nodeId: string): TimelineSegment {
      let idx = activeSegIdx.get(nodeId);
      if (idx != null && !segments[idx].done) return segments[idx];
      const seg: TimelineSegment = { nodeId, nodeName: nn(nodeId), lines: [], files: [], done: false };
      segments.push(seg);
      activeSegIdx.set(nodeId, segments.length - 1);
      return seg;
    }

    function segSummaryIcon(seg: TimelineSegment): string {
      if (!seg.failed) return "✓";
      if (seg.exitReason === "loop_terminated") return "⏹";
      return "⚠";
    }

    /** 把后端返回的结构化诊断 payload 渲染成一段 markdown，追加到 seg.lines 末尾 */
    function formatDiagnosisMarkdown(d: FailureDiagnosis): string {
      const parts: string[] = [];
      const headline = d.headline || "任务未正常完成";
      parts.push(`**🔍 为什么失败**：${headline}`);
      const evidence = d.evidence || [];
      if (evidence.length > 0) {
        parts.push("");
        parts.push(`**关键动作**（共 ${evidence.length} 条）：`);
        for (const item of evidence) {
          const iterN = item.iter ?? "?";
          const tool = item.tool || "?";
          const args = item.args_summary ? `(${item.args_summary})` : "";
          const err = (item.error || "").replace(/\s+/g, " ").trim();
          const errShort = err.length > 140 ? err.slice(0, 140) + "…" : err;
          parts.push(`- 第 ${iterN} 轮 \`${tool}\`${args} → ${errShort}`);
        }
      }
      const suggestion = d.suggestion || "";
      if (suggestion) {
        parts.push("");
        parts.push("**建议**：");
        parts.push(suggestion);
      }
      return parts.join("\n");
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
        return `**● ${seg.nodeName} 处理中...**\n\n${body}`;
      }).join("\n\n");
    }

    function updatePreview() {
      activity.last = Date.now();
      const rendered = renderTimeline();
      const pending = _pendingCmds.get(convId);
      if (pending) {
        pending.lastRendered = rendered;
        pending.segmentCount = segments.length;
        pending.allFiles = segments.flatMap(s => s.files);
      }
      if (!mountedRef.current) return;
      setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: rendered || "思考中..." } : m));
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
          if (task.startsWith("[通知]")) return;
          const seg = findOrCreateSeg(nid);
          seg.lines.push(`● **${nn(nid)}** 开始处理${task ? `：${task}` : ""}`);
          updatePreview();
        } else if (st === "idle") {
          const exitReason = (d.exit_reason as string) || "normal";
          const idx = activeSegIdx.get(nid);
          if (idx != null && segments[idx]) {
            segments[idx].done = true;
            segments[idx].exitReason = exitReason;
            // 正常完成才打 ✓；非正常退出交给后续 task_terminated/task_failed 事件打"⏹/⚠"
            if (exitReason === "normal" || exitReason === "ask_user") {
              segments[idx].lines.push(`✓ **${nn(nid)}** 完成`);
            } else {
              segments[idx].failed = true;
            }
          }
          updatePreview();
        } else if (st === "error") {
          const seg = findOrCreateSeg(nid);
          seg.done = true;
          seg.lines.push(`✗ **${nn(nid)}** 出错`);
          updatePreview();
        }
      } else if (event === "org:task_delegated") {
        const task = ((d.task || "") as string);
        const seg = findOrCreateSeg(nid);
        seg.lines.push(`→ **${nn(nid)}** → **${nn(toN)}** 分配任务：${task}`);
        updatePreview();
      } else if (event === "org:task_delivered") {
        const summary = ((d.summary || "") as string);
        const seg = findOrCreateSeg(nid);
        seg.lines.push(`📦 **${nn(nid)}** 提交交付${summary ? `：${summary}` : ""}`);
        updatePreview();
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
          segments[idx].done = true;
          segments[idx].resultPreview = preview;
          segments[idx].exitReason = reason;
          segments[idx].failed = true;
          segments[idx].diagnosis = diagnosis;
          segments[idx].lines.push(`⏹ **${nn(nid)}** 被强制终止（检测到死循环）`);
          if (diagnosis) {
            segments[idx].lines.push(formatDiagnosisMarkdown(diagnosis));
          }
        }
        updatePreview();
      } else if (event === "org:task_failed") {
        const preview = ((d.result_preview || "") as string);
        const reason = (d.exit_reason as string) || "max_iterations";
        const diagnosis = (d.diagnosis as FailureDiagnosis | undefined) || undefined;
        const idx = activeSegIdx.get(nid);
        if (idx != null && segments[idx]) {
          segments[idx].done = true;
          segments[idx].resultPreview = preview;
          segments[idx].exitReason = reason;
          segments[idx].failed = true;
          segments[idx].diagnosis = diagnosis;
          const reasonLabel =
            reason === "max_iterations" ? "达到最大迭代次数" :
            reason === "verify_incomplete" ? "任务验证未通过" :
            "执行失败";
          segments[idx].lines.push(`⚠ **${nn(nid)}** 未完成：${reasonLabel}`);
          if (diagnosis) {
            segments[idx].lines.push(formatDiagnosisMarkdown(diagnosis));
          }
        }
        updatePreview();
      } else if (event === "org:blackboard_update") {
        const mt = d.memory_type as string;
        const fname = d.filename as string | undefined;
        const fpath = d.file_path as string | undefined;
        const fsize = d.file_size as number | undefined;
        if (mt === "resource" && fname && fpath) {
          const seg = findOrCreateSeg(nid);
          seg.lines.push(`📎 **${nn(nid)}** 产出文件：${fname}`);
          seg.files.push({ filename: fname, file_path: fpath, file_size: fsize });
          updatePreview();
        } else {
          const seg = findOrCreateSeg(nid);
          seg.lines.push(`~ **${nn(nid)}** 更新黑板`);
          updatePreview();
        }
      }
    });

    function collectAllFiles(): FileAttachment[] {
      return segments.flatMap(s => s.files);
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
        setMessages(prev => prev.map(m =>
          m.id === placeholderId ? { ...m, content, streaming: false, role, attachments: atts } : m
        ));
      } else {
        const existing = loadFromLocalStorage(convId);
        const msg: ChatMsg = { id: placeholderId, role, content, timestamp: Date.now(), attachments: atts };
        const hasUser = existing.some(m => m.id === userMsg.id);
        const toSave = hasUser ? [...existing, msg] : [...existing, userMsg, msg];
        saveToLocalStorage(convId, toSave);
        persistToBackend(apiBaseUrl, convId, toSave.map(m => ({ role: m.role, content: m.content })), true);
      }
    };

    const wrapWithProcess = (resultText: string): string => {
      const doneBanner = '\n\n<div class="ocp-done-banner">&#x2705; 任务已完成</div>';
      if (segments.length === 0) return resultText + doneBanner;
      const allCollapsed = segments.map(seg => {
        const body = seg.lines.join("\n\n");
        return `<details>\n<summary>✓ ${seg.nodeName}</summary>\n\n${body}\n\n</details>`;
      }).join("\n\n");
      return `${allCollapsed}\n\n---\n\n${resultText}${doneBanner}`;
    };

    let finalContent = "";
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text, target_node_id: nodeId || undefined }),
      });
      const data = await res.json();
      const commandId = data.command_id as string | undefined;

      if (!commandId) {
        finalContent = data.result || data.error || JSON.stringify(data);
        finalizeResult(finalContent);
      } else {
        _pendingCmds.set(convId, { commandId, orgId, placeholderId, lastRendered: "", segmentCount: 0, allFiles: [], finalContent: null });

        let resolved = false;
        const unsubDone = onWsEvent((evt, raw) => {
          const d = raw as Record<string, unknown> | null;
          if (evt !== "org:command_done" || !d || d.command_id !== commandId) return;
          if (resolved) return;
          resolved = true;
          const result = d.result as Record<string, unknown> | null;
          const error = d.error as string | undefined;
          const resultText = String((result && (result.result || result.error)) || error || JSON.stringify(d));
          setTimeout(() => {
            finalContent = wrapWithProcess(resultText);
            finalizeResult(finalContent, collectAllFiles());
          }, 500);
        });

        while (!resolved) {
          await new Promise(r => setTimeout(r, 5000));
          if (resolved) break;
          try {
            const poll = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/commands/${commandId}`);
            const pd = await poll.json();
            if (pd.status === "done" || pd.status === "error") {
              if (!resolved) {
                resolved = true;
                const resultText = pd.result?.result || pd.result?.error || pd.error || JSON.stringify(pd);
                finalContent = wrapWithProcess(resultText);
                finalizeResult(finalContent, collectAllFiles());
              }
            }
          } catch { /* retry */ }
          if (!resolved && Date.now() - activity.last > 60000) {
            const elapsed = Math.round((Date.now() - cmdStartTime) / 1000);
            const min = Math.floor(elapsed / 60);
            const sec = elapsed % 60;
            const timeStr = min > 0 ? `${min}分${sec}秒` : `${sec}秒`;
            const seg = findOrCreateSeg("system");
            seg.lines = [`... 执行时间较长，组织仍在处理中（已用时 ${timeStr}）...`];
            updatePreview();
          }
        }
        unsubDone();
      }
    } catch (e: any) {
      finalContent = `发送失败: ${e.message || e}`;
      finalizeResult(finalContent, undefined, "system");
    } finally {
      unsubProgress();
      setSending(false);
      if (mountedRef.current) {
        const all = messagesRef.current.filter(m => !m.streaming);
        if (all.length > 0) {
          persistToBackend(apiBaseUrl, convId, all.map(m => ({ role: m.role, content: m.content })), true);
        }
      }
    }
  }, [input, sending, orgId, nodeId, apiBaseUrl, convId, persistToBackend]);

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
            <span className="ocp-header-title">{title || (nodeId ? `对话 · ${nodeId}` : "组织指挥台")}</span>
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {messages.length > 0 && (
              <button className="ocp-close" data-slot="ocp" onClick={handleClear} title="清空历史">
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
              {nodeId ? "向该节点发送指令开始对话" : "向组织发送指令，AI 团队将协作执行"}
            </div>
            <div className="ocp-empty-hint">Shift+Enter 换行，Enter 发送</div>
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
            清空对话记录
          </button>
        </div>
      )}

      <div className={`ocp-input-area ${compact ? "ocp-compact" : ""}`}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={nodeId ? "输入指令..." : "输入组织命令..."}
          rows={1}
          className="ocp-textarea"
        />
        <button
          data-slot="ocp"
          onClick={handleSend}
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

.ocp-send-spinner {
  width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff; border-radius: 50%;
  animation: ocp-spin 0.6s linear infinite;
}
@keyframes ocp-spin { to { transform: rotate(360deg); } }
`;
