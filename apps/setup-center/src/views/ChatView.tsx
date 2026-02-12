// ─── ChatView: 完整 AI 聊天页面 ───
// 支持流式 MD 渲染、思考内容折叠、Plan/Todo、斜杠命令、多模态、多 Agent、端点选择

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type {
  ChatMessage,
  ChatConversation,
  ChatToolCall,
  ChatPlan,
  ChatPlanStep,
  ChatAskUser,
  ChatAttachment,
  ChatArtifact,
  SlashCommand,
  EndpointSummary,
} from "../types";
import { genId, formatTime, formatDate } from "../utils";
import {
  IconSend, IconPaperclip, IconMic, IconStopCircle,
  IconPlan, IconPlus, IconMenu, IconStop, IconX,
  IconCheck, IconLoader, IconCircle, IconPlay, IconMinus,
  IconChevronDown, IconMessageCircle, IconChevronRight,
  IconImage, IconRefresh, IconClipboard, IconTrash, IconZap,
  IconMask, IconBot, IconUsers, IconHelp,
} from "../icons";

// ─── SSE 事件处理 ───

type StreamEvent =
  | { type: "thinking_start" }
  | { type: "thinking_delta"; content: string }
  | { type: "thinking_end" }
  | { type: "text_delta"; content: string }
  | { type: "tool_call_start"; tool: string; args: Record<string, unknown>; id?: string }
  | { type: "tool_call_end"; tool: string; result: string; id?: string }
  | { type: "plan_created"; plan: ChatPlan }
  | { type: "plan_step_updated"; stepIdx: number; status: string }
  | { type: "ask_user"; question: string; options?: { id: string; label: string }[] }
  | { type: "agent_switch"; agentName: string; reason: string }
  | { type: "artifact"; artifact_type: string; file_url: string; path: string; name: string; caption: string; size?: number }
  | { type: "error"; message: string }
  | { type: "done"; usage?: { input_tokens: number; output_tokens: number } };

// ─── 子组件 ───

function ThinkingBlock({ content, defaultOpen }: { content: string; defaultOpen?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="thinkingBlock">
      <div
        className="thinkingHeader"
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 0", userSelect: "none" }}
      >
        <span style={{ fontSize: 12, opacity: 0.5, transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s", display: "inline-flex", alignItems: "center" }}><IconChevronRight size={12} /></span>
        <span style={{ fontWeight: 700, fontSize: 13, opacity: 0.6 }}>{t("chat.thinkingBlock")}</span>
      </div>
      {open && (
        <div style={{ padding: "8px 12px", background: "rgba(124,58,237,0.04)", borderRadius: 10, fontSize: 13, lineHeight: 1.6, opacity: 0.75, whiteSpace: "pre-wrap" }}>
          {content}
        </div>
      )}
    </div>
  );
}

/** Single tool call detail (used inside expanded group) */
function ToolCallDetail({ tc }: { tc: ChatToolCall }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const statusIcon =
    tc.status === "done" ? <IconCheck size={14} /> :
    tc.status === "error" ? <IconX size={14} /> :
    tc.status === "running" ? <IconLoader size={14} /> :
    <IconCircle size={10} />;
  const statusColor = tc.status === "done" ? "var(--ok)" : tc.status === "error" ? "var(--danger)" : "var(--brand)";
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", background: "rgba(14,165,233,0.03)", userSelect: "none" }}
      >
        <span style={{ color: statusColor, fontWeight: 800, display: "inline-flex", alignItems: "center" }}>{statusIcon}</span>
        <span style={{ fontWeight: 600, fontSize: 12 }}>{tc.tool}</span>
        <span style={{ fontSize: 10, opacity: 0.4, marginLeft: "auto" }}>{open ? t("chat.collapse") : t("chat.expand")}</span>
      </div>
      {open && (
        <div style={{ padding: "6px 10px", fontSize: 12, background: "rgba(255,255,255,0.5)" }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{t("chat.args")}</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>
            {JSON.stringify(tc.args, null, 2)}
          </pre>
          {tc.result != null && (
            <>
              <div style={{ fontWeight: 700, marginTop: 8, marginBottom: 4 }}>{t("chat.result")}</div>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11, maxHeight: 200, overflow: "auto" }}>
                {tc.result}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Grouped tool calls: collapsed into one line by default, expandable */
function ToolCallsGroup({ toolCalls }: { toolCalls: ChatToolCall[] }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  const doneCount = toolCalls.filter((tc) => tc.status === "done").length;
  const errorCount = toolCalls.filter((tc) => tc.status === "error").length;
  const runningCount = toolCalls.filter((tc) => tc.status === "running").length;
  const allDone = doneCount === toolCalls.length;
  const hasError = errorCount > 0;
  const summaryColor = hasError ? "var(--danger)" : runningCount > 0 ? "var(--brand)" : "var(--ok)";
  const summaryIcon = hasError ? <IconX size={14} /> : runningCount > 0 ? <IconLoader size={14} /> : <IconCheck size={14} />;
  const toolNames = toolCalls.map((tc) => tc.tool);
  // Deduplicate and show counts
  const nameCounts: Record<string, number> = {};
  for (const n of toolNames) nameCounts[n] = (nameCounts[n] || 0) + 1;
  const nameLabels = Object.entries(nameCounts).map(([n, c]) => c > 1 ? `${n} ×${c}` : n);
  const summaryText = nameLabels.join(", ");

  return (
    <div style={{ margin: "6px 0", border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", background: "rgba(14,165,233,0.04)", userSelect: "none" }}
      >
        <span style={{ color: summaryColor, fontWeight: 800, display: "inline-flex", alignItems: "center" }}>{summaryIcon}</span>
        <span style={{ fontWeight: 700, fontSize: 13 }}>
          {t("chat.toolCallLabel")}{toolCalls.length > 1 ? `${toolCalls.length} ` : ""}{toolCalls.length === 1 ? toolCalls[0].tool : ""}
        </span>
        {toolCalls.length > 1 && (
          <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
            {summaryText}
          </span>
        )}
        <span style={{ fontSize: 11, opacity: 0.5, marginLeft: "auto", flexShrink: 0 }}>{expanded ? t("chat.collapse") : t("chat.expand")}</span>
      </div>
      {expanded && (
        <div style={{ padding: "6px 8px", display: "flex", flexDirection: "column", gap: 4, background: "rgba(255,255,255,0.3)" }}>
          {toolCalls.map((tc, i) => (
            <ToolCallDetail key={i} tc={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

function PlanBlock({ plan }: { plan: ChatPlan }) {
  const { t } = useTranslation();
  const completed = plan.steps.filter((s) => s.status === "completed").length;
  const total = plan.steps.length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  return (
    <div style={{ margin: "8px 0", border: "1px solid rgba(14,165,233,0.2)", borderRadius: 12, padding: "12px 14px", background: "rgba(14,165,233,0.03)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontWeight: 800, fontSize: 14 }}>{t("chat.planLabel")}{plan.taskSummary}</span>
        <span style={{ fontSize: 12, opacity: 0.6 }}>{completed}/{total} ({pct}%)</span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: "rgba(14,165,233,0.12)", overflow: "hidden", marginBottom: 10 }}>
        <div style={{ height: "100%", width: `${pct}%`, background: "var(--brand)", borderRadius: 2, transition: "width 0.3s" }} />
      </div>
      {plan.steps.map((step, idx) => (
        <PlanStepItem key={idx} step={step} idx={idx} />
      ))}
    </div>
  );
}

function PlanStepItem({ step, idx }: { step: ChatPlanStep; idx: number }) {
  const icon =
    step.status === "completed" ? <IconCheck size={14} /> :
    step.status === "in_progress" ? <IconPlay size={12} /> :
    step.status === "skipped" ? <IconMinus size={14} /> :
    <IconCircle size={10} />;
  const color =
    step.status === "completed" ? "rgba(16,185,129,1)" : step.status === "in_progress" ? "var(--brand)" : step.status === "skipped" ? "var(--muted)" : "var(--muted)";
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "4px 0", fontSize: 13 }}>
      <span style={{ color, fontWeight: 800, minWidth: 16, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>{icon}</span>
      <div style={{ flex: 1 }}>
        <span style={{ opacity: step.status === "skipped" ? 0.5 : 1 }}>{idx + 1}. {step.description}</span>
        {step.result && <div style={{ fontSize: 11, opacity: 0.6, marginTop: 2 }}>{step.result}</div>}
      </div>
    </div>
  );
}

function AskUserBlock({ ask, onAnswer }: { ask: ChatAskUser; onAnswer: (answer: string) => void }) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  if (ask.answered) {
    return (
      <div style={{ margin: "8px 0", padding: "10px 14px", borderRadius: 10, background: "rgba(14,165,233,0.06)", border: "1px solid rgba(14,165,233,0.15)" }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>{ask.question}</div>
        <div style={{ fontSize: 13, opacity: 0.7 }}>{t("chat.answered")}{ask.answer}</div>
      </div>
    );
  }
  return (
    <div style={{ margin: "8px 0", padding: "12px 14px", borderRadius: 12, background: "rgba(124,58,237,0.05)", border: "1px solid rgba(124,58,237,0.18)" }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>{ask.question}</div>
      {ask.options && ask.options.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {ask.options.map((opt) => (
            <button
              key={opt.id}
              className="btnPrimary"
              style={{ fontSize: 13, padding: "6px 16px" }}
              onClick={() => onAnswer(opt.id)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t("chat.askPlaceholder")}
            style={{ flex: 1, fontSize: 13 }}
            onKeyDown={(e) => { if (e.key === "Enter" && input.trim()) { onAnswer(input.trim()); setInput(""); } }}
          />
          <button className="btnPrimary" onClick={() => { if (input.trim()) { onAnswer(input.trim()); setInput(""); } }} style={{ fontSize: 13, padding: "6px 16px" }}>
            {t("chat.submitAnswer")}
          </button>
        </div>
      )}
    </div>
  );
}

function AttachmentPreview({ att, onRemove }: { att: ChatAttachment; onRemove?: () => void }) {
  if (att.type === "image" && att.previewUrl) {
    return (
      <div style={{ position: "relative", display: "inline-block" }}>
        <img src={att.previewUrl} alt={att.name} style={{ width: 80, height: 80, objectFit: "cover", display: "block", borderRadius: 10, border: "1px solid var(--line)" }} />
        {onRemove && (
          <button
            onClick={onRemove}
            style={{
              position: "absolute", top: -6, right: -6,
              width: 22, height: 22, borderRadius: 11,
              border: "2px solid #fff", background: "var(--danger)", color: "#fff",
              fontSize: 11, cursor: "pointer", display: "grid", placeItems: "center",
              boxShadow: "0 1px 4px rgba(0,0,0,0.18)", zIndex: 2, padding: 0, lineHeight: 1,
            }}
          >
            <IconX size={11} />
          </button>
        )}
      </div>
    );
  }
  const icon = att.type === "voice" ? <IconMic size={14} /> : att.type === "image" ? <IconImage size={14} /> : <IconPaperclip size={14} />;
  const sizeStr = att.size ? `${(att.size / 1024).toFixed(1)} KB` : "";
  return (
    <div style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 28px 6px 10px", borderRadius: 10, border: "1px solid var(--line)", fontSize: 12 }}>
      {onRemove && (
        <button
          onClick={onRemove}
          style={{
            position: "absolute", top: -6, right: -6,
            width: 22, height: 22, borderRadius: 11,
            border: "2px solid #fff", background: "var(--danger)", color: "#fff",
            fontSize: 11, cursor: "pointer", display: "grid", placeItems: "center",
            boxShadow: "0 1px 4px rgba(0,0,0,0.18)", zIndex: 2, padding: 0, lineHeight: 1,
          }}
        >
          <IconX size={11} />
        </button>
      )}
      <span style={{ display: "inline-flex", alignItems: "center" }}>{icon}</span>
      <span style={{ fontWeight: 600 }}>{att.name}</span>
      {sizeStr && <span style={{ opacity: 0.5 }}>{sizeStr}</span>}
    </div>
  );
}

// ─── Slash 命令面板 ───

function SlashCommandPanel({
  commands,
  filter,
  onSelect,
  selectedIdx,
}: {
  commands: SlashCommand[];
  filter: string;
  onSelect: (cmd: SlashCommand) => void;
  selectedIdx: number;
}) {
  const filtered = useMemo(() => {
    const q = filter.toLowerCase();
    return commands.filter((c) => c.id.includes(q) || c.label.includes(q) || c.description.includes(q));
  }, [commands, filter]);

  if (filtered.length === 0) return null;
  return (
    <div
      style={{
        position: "absolute",
        bottom: "100%",
        left: 0,
        right: 0,
        marginBottom: 6,
        maxHeight: 260,
        overflow: "auto",
        border: "1px solid var(--line)",
        borderRadius: 14,
        background: "rgba(255,255,255,0.98)",
        boxShadow: "0 -12px 48px rgba(17,24,39,0.12)",
        zIndex: 100,
      }}
    >
      {filtered.map((cmd, idx) => (
        <div
          key={cmd.id}
          onClick={() => onSelect(cmd)}
          style={{
            padding: "10px 14px",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 10,
            background: idx === selectedIdx ? "rgba(14,165,233,0.08)" : "transparent",
            borderTop: idx === 0 ? "none" : "1px solid rgba(17,24,39,0.06)",
          }}
        >
          <span style={{ fontSize: 16, opacity: 0.7, display: "inline-flex", alignItems: "center" }}>
            {cmd.id === "model" ? <IconRefresh size={16} /> :
             cmd.id === "plan" ? <IconClipboard size={16} /> :
             cmd.id === "clear" ? <IconTrash size={16} /> :
             cmd.id === "skill" ? <IconZap size={16} /> :
             cmd.id === "persona" ? <IconMask size={16} /> :
             cmd.id === "agent" ? <IconBot size={16} /> :
             cmd.id === "agents" ? <IconUsers size={16} /> :
             cmd.id === "help" ? <IconHelp size={16} /> :
             <span style={{ fontSize: 14 }}>/</span>}
          </span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 13 }}>/{cmd.id} <span style={{ fontWeight: 400, opacity: 0.6 }}>{cmd.label}</span></div>
            <div style={{ fontSize: 12, opacity: 0.5 }}>{cmd.description}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── 消息渲染 ───

function MessageBubble({
  msg,
  onAskAnswer,
  apiBaseUrl,
}: {
  msg: ChatMessage;
  onAskAnswer?: (msgId: string, answer: string) => void;
  apiBaseUrl?: string;
}) {
  const isUser = msg.role === "user";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : "flex-start", marginBottom: 16 }}>
      {/* Agent name label */}
      {!isUser && msg.agentName && (
        <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.5, marginBottom: 2, paddingLeft: 2 }}>
          {msg.agentName}
        </div>
      )}
      <div
        style={{
          maxWidth: "85%",
          padding: isUser ? "10px 16px" : "12px 16px",
          borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
          background: isUser ? "var(--brand)" : "rgba(255,255,255,0.85)",
          color: isUser ? "#fff" : "var(--text)",
          border: isUser ? "none" : "1px solid var(--line)",
          boxShadow: isUser ? "0 2px 12px rgba(14,165,233,0.18)" : "0 1px 4px rgba(17,24,39,0.06)",
          fontSize: 14,
          lineHeight: 1.7,
          wordBreak: "break-word",
        }}
      >
        {/* Attachments */}
        {msg.attachments && msg.attachments.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {msg.attachments.map((att, i) => (
              <AttachmentPreview key={i} att={att} />
            ))}
          </div>
        )}

        {/* Thinking content */}
        {msg.thinking && <ThinkingBlock content={msg.thinking} />}

        {/* Main content (markdown) */}
        {msg.content && (
          <div className="chatMdContent">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {msg.content}
            </ReactMarkdown>
          </div>
        )}

        {/* Streaming indicator */}
        {msg.streaming && !msg.content && (
          <div style={{ display: "flex", gap: 4, padding: "4px 0" }}>
            <span className="dotBounce" style={{ animationDelay: "0s" }} />
            <span className="dotBounce" style={{ animationDelay: "0.15s" }} />
            <span className="dotBounce" style={{ animationDelay: "0.3s" }} />
          </div>
        )}

        {/* Tool calls (grouped & collapsed by default) */}
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <ToolCallsGroup toolCalls={msg.toolCalls} />
        )}

        {/* Plan */}
        {msg.plan && <PlanBlock plan={msg.plan} />}

        {/* Artifacts (images, files delivered by agent) */}
        {msg.artifacts && msg.artifacts.length > 0 && (
          <div style={{ marginTop: 8 }}>
            {msg.artifacts.map((art, i) => {
              const fullUrl = art.file_url.startsWith("http")
                ? art.file_url
                : `${apiBaseUrl || ""}${art.file_url}`;
              if (art.artifact_type === "image") {
                return (
                  <div key={i} style={{ marginBottom: 8 }}>
                    <img
                      src={fullUrl}
                      alt={art.caption || art.name}
                      style={{
                        maxWidth: "100%",
                        maxHeight: 400,
                        borderRadius: 8,
                        border: "1px solid var(--line)",
                        cursor: "pointer",
                      }}
                      onClick={() => window.open(fullUrl, "_blank")}
                    />
                    {art.caption && (
                      <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>{art.caption}</div>
                    )}
                  </div>
                );
              }
              if (art.artifact_type === "voice") {
                return (
                  <div key={i} style={{ marginBottom: 8 }}>
                    <audio controls src={fullUrl} style={{ maxWidth: "100%" }} />
                    {art.caption && (
                      <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>{art.caption}</div>
                    )}
                  </div>
                );
              }
              // Generic file artifact — clickable card to download/open
              const sizeStr = art.size != null
                ? art.size > 1048576 ? `${(art.size / 1048576).toFixed(1)} MB` : `${(art.size / 1024).toFixed(1)} KB`
                : "";
              return (
                <div key={i} style={{
                  display: "inline-flex", alignItems: "center", gap: 8,
                  padding: "8px 14px", borderRadius: 8, border: "1px solid var(--line)",
                  fontSize: 13, marginBottom: 4, cursor: "pointer",
                  background: "rgba(255,255,255,0.5)",
                  transition: "background 0.15s",
                }}
                  onClick={async () => {
                    // Use Tauri command to download file (WebView2 doesn't support <a download>)
                    try {
                      const savedPath = await invoke<string>("download_file", {
                        url: fullUrl,
                        filename: art.name || "file",
                      });
                      console.log("文件已保存:", savedPath);
                    } catch (err) {
                      console.error("文件下载失败:", err);
                    }
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(14,165,233,0.08)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(255,255,255,0.5)"; }}
                >
                  <IconPaperclip size={14} />
                  <span style={{ fontWeight: 600 }}>{art.name}</span>
                  {sizeStr && <span style={{ opacity: 0.5 }}>{sizeStr}</span>}
                  {art.caption && <span style={{ opacity: 0.6, fontSize: 12 }}>{art.caption}</span>}
                </div>
              );
            })}
          </div>
        )}

        {/* Ask user */}
        {msg.askUser && (
          <AskUserBlock
            ask={msg.askUser}
            onAnswer={(ans) => onAskAnswer?.(msg.id, ans)}
          />
        )}
      </div>
      <div style={{ fontSize: 11, opacity: 0.35, marginTop: 2, paddingLeft: 2, paddingRight: 2 }}>
        {formatTime(msg.timestamp)}
      </div>
    </div>
  );
}

// ─── 主组件 ───

export function ChatView({
  serviceRunning,
  endpoints,
  onStartService,
  apiBaseUrl = "http://127.0.0.1:18900",
}: {
  serviceRunning: boolean;
  endpoints: EndpointSummary[];
  onStartService: () => void;
  apiBaseUrl?: string;
}) {
  const { t } = useTranslation();
  // ── State ──
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [selectedEndpoint, setSelectedEndpoint] = useState("auto");
  const [planMode, setPlanMode] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashFilter, setSlashFilter] = useState("");
  const [slashSelectedIdx, setSlashSelectedIdx] = useState(0);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);

  const [isRecording, setIsRecording] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // ── API base URL ──
  const apiBase = apiBaseUrl;

  // ── 文件上传辅助函数：上传文件到 /api/upload 并返回访问 URL ──
  const uploadFile = useCallback(async (file: Blob, filename: string): Promise<string> => {
    const form = new FormData();
    form.append("file", file, filename);
    const res = await fetch(`${apiBase}/api/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`上传失败: ${res.status}`);
    const data = await res.json();
    return data.url as string;  // 后端返回 { url: "/api/uploads/<filename>" }
  }, [apiBase]);

  // ── 组件卸载清理：abort 流式请求 + 停止麦克风 ──
  useEffect(() => {
    return () => {
      // 终止正在进行的 SSE 流式请求，避免内存泄漏和 React 状态更新警告
      abortRef.current?.abort();
      readerRef.current?.cancel().catch(() => {});
      readerRef.current = null;
      // 停止录音并释放麦克风
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        try { mediaRecorderRef.current.stop(); } catch { /* ignore */ }
      }
      mediaRecorderRef.current = null;
    };
  }, []);

  // ── 自动滚到底部 ──
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── 点击外部关闭模型菜单 ──
  useEffect(() => {
    if (!modelMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) {
        setModelMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modelMenuOpen]);

  // ── 斜杠命令定义 ──
  const slashCommands: SlashCommand[] = useMemo(() => [
    { id: "model", label: "切换模型", description: "选择使用的 LLM 端点", action: (args) => {
      if (args && endpoints.find((e) => e.name === args)) {
        setSelectedEndpoint(args);
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `已切换到端点: ${args}`, timestamp: Date.now() }]);
      } else {
        const names = ["auto", ...endpoints.map((e) => e.name)];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `可用端点: ${names.join(", ")}\n用法: /model <端点名>`, timestamp: Date.now() }]);
      }
    }},
    { id: "plan", label: "计划模式", description: "开启/关闭 Plan 模式，先计划再执行", action: () => {
      setPlanMode((v) => {
        const next = !v;
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: next ? "已开启 Plan 模式" : "已关闭 Plan 模式", timestamp: Date.now() }]);
        return next;
      });
    }},
    { id: "clear", label: "清空对话", description: "清除当前对话的所有消息", action: () => { setMessages([]); } },
    { id: "skill", label: "使用技能", description: "调用已安装的技能（发送 /skill:<技能名> 触发）", action: (args) => {
      if (args) {
        setInputText(`请使用技能「${args}」来帮我：`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /skill <技能名>，如 /skill web-search。在消息中提及技能名即可触发。", timestamp: Date.now() }]);
      }
    }},
    { id: "persona", label: "切换角色", description: "切换 Agent 的人格预设", action: (args) => {
      if (args) {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `角色切换请在「设置 → Agent 系统」中修改 PERSONA_NAME 为 "${args}"`, timestamp: Date.now() }]);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "可用角色: default, business, tech_expert, butler, girlfriend, boyfriend, family, jarvis\n用法: /persona <角色ID>", timestamp: Date.now() }]);
      }
    }},
    { id: "agent", label: "切换 Agent", description: "在多 Agent 间切换（handoff 模式）", action: (args) => {
      if (args) {
        setInputText(`请切换到 Agent「${args}」来处理接下来的任务。`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /agent <Agent名称>。在 handoff 模式下，AI 会自动在 Agent 间切换。", timestamp: Date.now() }]);
      }
    }},
    { id: "agents", label: "查看 Agent 列表", description: "显示可用的 Agent 列表", action: () => {
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: "Agent 列表取决于 handoff 配置。当前可通过 /agent <名称> 手动请求切换。", timestamp: Date.now() }]);
    }},
    { id: "help", label: "帮助", description: "显示可用命令列表", action: () => {
      setMessages((prev) => [...prev, {
        id: genId(),
        role: "system",
        content: "**可用命令：**\n- `/model [端点名]` — 切换 LLM 端点\n- `/plan` — 开启/关闭计划模式\n- `/clear` — 清空对话\n- `/skill [技能名]` — 使用技能\n- `/persona [角色ID]` — 查看/切换角色\n- `/agent [Agent名]` — 切换 Agent\n- `/agents` — 查看 Agent 列表\n- `/help` — 显示此帮助",
        timestamp: Date.now(),
      }]);
    }},
  ], [endpoints]);

  // ── 新建对话 ──
  const newConversation = useCallback(() => {
    const id = genId();
    setActiveConvId(id);
    setMessages([]);
    setPendingAttachments([]);
    setConversations((prev) => [{
      id,
      title: "新对话",
      lastMessage: "",
      timestamp: Date.now(),
      messageCount: 0,
    }, ...prev]);
  }, []);

  // ── 发送消息（overrideText 用于 ask_user 回复等场景，绕过 inputText） ──
  const sendMessage = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? inputText).trim();
    if (!text && pendingAttachments.length === 0) return;
    if (isStreaming) return;

    // 斜杠命令处理
    if (text.startsWith("/")) {
      const parts = text.slice(1).split(/\s+/);
      const cmdId = parts[0].toLowerCase();
      const cmd = slashCommands.find((c) => c.id === cmdId);
      if (cmd) {
        cmd.action(parts.slice(1).join(" "));
        setInputText("");
        setSlashOpen(false);
        return;
      }
    }

    // 创建用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: "user",
      content: text,
      attachments: pendingAttachments.length > 0 ? [...pendingAttachments] : undefined,
      timestamp: Date.now(),
    };

    // 创建流式助手消息占位
    const assistantMsg: ChatMessage = {
      id: genId(),
      role: "assistant",
      content: "",
      streaming: true,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInputText("");
    setPendingAttachments([]);
    setIsStreaming(true);
    setSlashOpen(false);

    // 确保有对话（注意 setState 是异步的，需要用局部变量保存新 id）
    let convId = activeConvId;
    if (!convId) {
      convId = genId();
      setActiveConvId(convId);
      setConversations((prev) => [{
        id: convId!,
        title: text.slice(0, 30) || "新对话",
        lastMessage: text,
        timestamp: Date.now(),
        messageCount: 1,
      }, ...prev]);
    }

    // SSE 流式请求
    const abort = new AbortController();
    abortRef.current = abort;

    // 空闲超时：如果 IDLE_TIMEOUT_MS 内没有收到任何数据则放弃
    // （每次收到数据后重置计时器，不影响长对话）
    const IDLE_TIMEOUT_MS = 120_000; // 2 minutes idle
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        abort.abort();
        readerRef.current?.cancel().catch(() => {});
      }, IDLE_TIMEOUT_MS);
    };

    try {
      const body: Record<string, unknown> = {
        message: text,
        conversation_id: convId,
        plan_mode: planMode,
        endpoint: selectedEndpoint === "auto" ? null : selectedEndpoint,
      };

      // 附件信息
      if (pendingAttachments.length > 0) {
        body.attachments = pendingAttachments.map((a) => ({
          type: a.type,
          name: a.name,
          url: a.url,
          mime_type: a.mimeType,
        }));
      }

      resetIdleTimer(); // Start idle timer before fetch

      const response = await fetch(`${apiBase}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: abort.signal,
      });

      if (!response.ok) {
        const errText = await response.text().catch(() => "请求失败");
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: `错误：${response.status} ${errText}`, streaming: false } : m
        ));
        setIsStreaming(false);
        return;
      }

      // 收到响应头，重置空闲计时
      resetIdleTimer();

      // 处理 SSE 流
      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");
      readerRef.current = reader;

      const decoder = new TextDecoder();
      let buffer = "";
      let currentContent = "";
      let currentThinking = "";
      let isThinking = false;
      let currentToolCalls: ChatToolCall[] = [];
      let currentPlan: ChatPlan | null = null;
      let currentAsk: ChatAskUser | null = null;
      let currentAgent: string | null = null;
      let currentArtifacts: ChatArtifact[] = [];
      let gracefulDone = false; // SSE 正常发送了 "done" 事件

      while (true) {
        // ── 1. 每次循环检查 abort 状态 ──
        if (abort.signal.aborted) break;

        let done: boolean;
        let value: Uint8Array | undefined;
        try {
          ({ done, value } = await reader.read());
        } catch (readErr) {
          // reader.read() 抛异常（abort 或网络错误）→ 跳到外层 catch
          throw readErr;
        }

        if (value) {
          buffer += decoder.decode(value, { stream: true });
          resetIdleTimer(); // 收到数据，重置空闲计时
        }

        // ── 2. 再次检查 abort（read 可能返回 done:true 而非抛异常） ──
        if (abort.signal.aborted) break;

        if (done) {
          // 流结束：处理 buffer 中可能的残余内容（最后一行无换行符的情况）
          if (buffer.trim()) {
            const remaining = buffer.split("\n");
            for (const line of remaining) {
              if (!line.startsWith("data: ")) continue;
              const data = line.slice(6).trim();
              if (data === "[DONE]") continue;
              try {
                const event: StreamEvent = JSON.parse(data);
                if (event.type === "text_delta") currentContent += event.content;
                if (event.type === "done") gracefulDone = true;
              } catch { /* ignore malformed */ }
            }
          }
          break;
        }

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6).trim();
          if (data === "[DONE]") continue;

          try {
            const event: StreamEvent = JSON.parse(data);

            switch (event.type) {
              case "thinking_start":
                isThinking = true;
                break;
              case "thinking_delta":
                currentThinking += event.content;
                break;
              case "thinking_end":
                isThinking = false;
                break;
              case "text_delta":
                currentContent += event.content;
                break;
              case "tool_call_start":
                currentToolCalls = [...currentToolCalls, { tool: event.tool, args: event.args, status: "running", id: event.id }];
                break;
              case "tool_call_end": {
                let matched = false;
                currentToolCalls = currentToolCalls.map((tc) => {
                  if (matched) return tc;
                  const idMatch = event.id && tc.id && tc.id === event.id;
                  const nameMatch = !event.id && tc.tool === event.tool && tc.status === "running";
                  if (idMatch || nameMatch) {
                    matched = true;
                    return { ...tc, result: event.result, status: "done" as const };
                  }
                  return tc;
                });
                break;
              }
              case "plan_created":
                currentPlan = event.plan;
                break;
              case "plan_step_updated":
                if (currentPlan) {
                  const newSteps: ChatPlanStep[] = [...currentPlan.steps];
                  if (newSteps[event.stepIdx]) {
                    newSteps[event.stepIdx] = { ...newSteps[event.stepIdx], status: event.status as ChatPlanStep["status"] };
                  }
                  currentPlan = { ...currentPlan, steps: newSteps } as ChatPlan;
                }
                break;
              case "ask_user":
                currentAsk = { question: event.question, options: event.options };
                break;
              case "artifact":
                currentArtifacts = [...currentArtifacts, {
                  artifact_type: event.artifact_type,
                  file_url: event.file_url,
                  path: event.path,
                  name: event.name,
                  caption: event.caption,
                  size: event.size,
                }];
                break;
              case "agent_switch":
                currentAgent = event.agentName;
                setMessages((prev) => {
                  const switchMsg: ChatMessage = {
                    id: genId(),
                    role: "system",
                    content: `Agent 切换到：${event.agentName}${event.reason ? ` — ${event.reason}` : ""}`,
                    timestamp: Date.now(),
                  };
                  return [...prev.filter((m) => m.id !== assistantMsg.id), switchMsg, {
                    ...assistantMsg,
                    content: currentContent,
                    thinking: currentThinking || null,
                    agentName: event.agentName,
                    toolCalls: currentToolCalls.length > 0 ? currentToolCalls : null,
                    plan: currentPlan,
                    askUser: currentAsk,
                    streaming: true,
                  }];
                });
                continue; // skip normal update below
              case "error":
                currentContent += `\n\n**错误**：${event.message}`;
                break;
              case "done":
                gracefulDone = true;
                break;
            }

            // 更新助手消息
            setMessages((prev) => prev.map((m) =>
              m.id === assistantMsg.id
                ? {
                    ...m,
                    content: currentContent,
                    thinking: currentThinking || null,
                    agentName: currentAgent,
                    toolCalls: currentToolCalls.length > 0 ? [...currentToolCalls] : null,
                    plan: currentPlan ? { ...currentPlan } : null,
                    askUser: currentAsk ? { ...currentAsk } : null,
                    artifacts: currentArtifacts.length > 0 ? [...currentArtifacts] : null,
                    streaming: event.type !== "done",
                  }
                : m
            ));

            if (event.type === "done") break;
          } catch {
            // ignore malformed SSE
          }
        }
      }

      // ── 循环结束后：判断是正常完成还是被用户中止 ──
      if (abort.signal.aborted) {
        // 用户点击了停止（或空闲超时触发 abort）
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id
            ? { ...m, content: m.content || "（已中止）", streaming: false }
            : m
        ));
      } else {
        // 正常完成流式
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, streaming: false } : m
        ));
      }
    } catch (e: unknown) {
      // ── 兼容多种 abort 错误形式 ──
      // Chromium/WebView2: DOMException { name: "AbortError" }
      // 某些环境: Error { name: "AbortError" }
      // 安全检查: abort.signal.aborted 为 true
      const isAbort =
        abort.signal.aborted ||
        (e instanceof DOMException && e.name === "AbortError") ||
        (e instanceof Error && e.name === "AbortError");

      if (isAbort) {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: m.content || "（已中止）", streaming: false } : m
        ));
      } else {
        const errMsg = e instanceof Error ? e.message : String(e);
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: `连接失败：${errMsg}\n\n请确认后台服务（openakita serve）已启动，且 HTTP API 端口（18900）可访问。`, streaming: false } : m
        ));
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      // 确保 reader 被释放
      try { readerRef.current?.cancel().catch(() => {}); } catch { /* ignore */ }
      readerRef.current = null;
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [inputText, pendingAttachments, isStreaming, activeConvId, planMode, selectedEndpoint, apiBase, slashCommands]);

  // ── 处理用户回答 (ask_user) ──
  const handleAskAnswer = useCallback((msgId: string, answer: string) => {
    setMessages((prev) => prev.map((m) =>
      m.id === msgId && m.askUser
        ? { ...m, askUser: { ...m.askUser, answered: true, answer } }
        : m
    ));
    // reason_stream 在 ask_user 后中断流，用户回复通过新 /api/chat 请求继续处理
    // 直接通过 sendMessage(overrideText) 发送，无需等待 state 更新
    sendMessage(answer);
  }, [sendMessage]);

  // ── 停止生成 ──
  const stopStreaming = useCallback(() => {
    // 1. Abort fetch 请求（触发 AbortError）
    abortRef.current?.abort();
    // 2. 显式取消 reader（某些浏览器/WebView 下 abort 不会立即终止 reader.read()）
    try { readerRef.current?.cancel().catch(() => {}); } catch { /* ignore */ }
    readerRef.current = null;
  }, []);

  // ── 文件/图片上传 ──
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of Array.from(files)) {
      const att: ChatAttachment = {
        type: file.type.startsWith("image/") ? "image" : file.type.startsWith("audio/") ? "voice" : "file",
        name: file.name,
        size: file.size,
        mimeType: file.type,
      };
      // 图片预览
      if (att.type === "image") {
        const reader = new FileReader();
        reader.onload = () => {
          att.previewUrl = reader.result as string;
          att.url = reader.result as string;
          setPendingAttachments((prev) => [...prev, att]);
        };
        reader.readAsDataURL(file);
      } else {
        // 先添加占位，然后异步上传
        setPendingAttachments((prev) => [...prev, att]);
        uploadFile(file, file.name)
          .then((serverUrl) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a.name === att.name && a.type === att.type && !a.url
                ? { ...a, url: `${apiBase}${serverUrl}` } : a)
            );
          })
          .catch(() => {
            setPendingAttachments((prev) =>
              prev.filter((a) => !(a.name === att.name && a.type === att.type && !a.url)));
          });
      }
    }
    e.target.value = "";
  }, []);

  // ── 粘贴图片 ──
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) continue;
        const reader = new FileReader();
        reader.onload = () => {
          setPendingAttachments((prev) => [...prev, {
            type: "image",
            name: `粘贴图片-${Date.now()}.png`,
            previewUrl: reader.result as string,
            url: reader.result as string,
            size: file.size,
            mimeType: file.type,
          }]);
        };
        reader.readAsDataURL(file);
      }
    }
  }, []);

  // ── 拖拽图片/文件 (Tauri native webview-scoped drag-drop) ──
  const [dragOver, setDragOver] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | null = null;

    (async () => {
      try {
        const webview = getCurrentWebview();
        // onDragDropEvent: official Tauri v2 webview-scoped API
        unlisten = await webview.onDragDropEvent((event) => {
          if (cancelled) return;
          const payload = event.payload as any;
          console.log("[DragDrop] event:", payload.type, payload);
          if (payload.type === "over" || payload.type === "enter") {
            setDragOver(true);
          } else if (payload.type === "leave" || payload.type === "cancel") {
            setDragOver(false);
          } else if (payload.type === "drop") {
            setDragOver(false);
            const paths: string[] = payload.paths || [];
            console.log("[DragDrop] dropped paths:", paths);
            for (const filePath of paths) {
              const name = filePath.split(/[\\/]/).pop() || "file";
              const ext = (name.split(".").pop() || "").toLowerCase();
              const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
              const mimeMap: Record<string, string> = {
                png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
                gif: "image/gif", webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
                pdf: "application/pdf", txt: "text/plain", md: "text/plain",
                json: "application/json", csv: "text/csv",
              };
              const mimeType = mimeMap[ext] || "application/octet-stream";
              invoke<string>("read_file_base64", { path: filePath })
                .then((dataUrl) => {
                  if (cancelled) return;
                  console.log("[DragDrop] file read OK:", name, dataUrl.length, "bytes");
                  setPendingAttachments((prev) => [...prev, {
                    type: isImage ? "image" : "file",
                    name,
                    previewUrl: isImage ? dataUrl : undefined,
                    url: dataUrl,
                    mimeType,
                  }]);
                })
                .catch((err) => console.error("[DragDrop] read_file_base64 failed:", name, err));
            }
          }
        });
        console.log("[DragDrop] listener registered via onDragDropEvent");
      } catch (e) {
        console.warn("[DragDrop] onDragDropEvent failed, trying fallback:", e);
        // Fallback: try webview.listen for individual events
        try {
          const webview = getCurrentWebview();
          const unlisteners: Array<() => void> = [];
          const u1 = await webview.listen<any>("tauri://drag-enter", () => { if (!cancelled) setDragOver(true); });
          const u2 = await webview.listen<any>("tauri://drag-over", () => { if (!cancelled) setDragOver(true); });
          const u3 = await webview.listen<any>("tauri://drag-leave", () => { if (!cancelled) setDragOver(false); });
          const u4 = await webview.listen<any>("tauri://drag-drop", (ev) => {
            if (cancelled) return;
            setDragOver(false);
            const paths: string[] = ev.payload?.paths || [];
            console.log("[DragDrop-fallback] dropped paths:", paths);
            for (const filePath of paths) {
              const name = filePath.split(/[\\/]/).pop() || "file";
              const ext = (name.split(".").pop() || "").toLowerCase();
              const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
              invoke<string>("read_file_base64", { path: filePath })
                .then((dataUrl) => {
                  if (cancelled) return;
                  setPendingAttachments((prev) => [...prev, {
                    type: isImage ? "image" : "file",
                    name,
                    previewUrl: isImage ? dataUrl : undefined,
                    url: dataUrl,
                    mimeType: isImage ? `image/${ext === "jpg" ? "jpeg" : ext}` : "application/octet-stream",
                  }]);
                })
                .catch((err) => console.error("[DragDrop-fallback] read failed:", err));
            }
          });
          unlisteners.push(u1, u2, u3, u4);
          unlisten = () => unlisteners.forEach((u) => u());
          console.log("[DragDrop] fallback listeners registered");
        } catch (e2) {
          console.error("[DragDrop] all methods failed:", e2);
        }
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // ── 语音录制 ──
  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      // 停止录制
      mediaRecorderRef.current?.stop();
      setIsRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      audioChunksRef.current = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const localPreview = URL.createObjectURL(blob);
        const filename = `voice-${Date.now()}.webm`;
        // 立即添加为"上传中"状态（有预览但无 url）
        const tempAtt: ChatAttachment = {
          type: "voice",
          name: filename,
          previewUrl: localPreview,
          size: blob.size,
          mimeType: "audio/webm",
        };
        setPendingAttachments((prev) => [...prev, tempAtt]);
        // 异步上传到后端
        uploadFile(blob, filename)
          .then((serverUrl) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a.name === filename && a.type === "voice"
                ? { ...a, url: `${apiBase}${serverUrl}` } : a)
            );
          })
          .catch(() => {
            setPendingAttachments((prev) => prev.filter((a) => !(a.name === filename && a.type === "voice")));
          });
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.start();
      setIsRecording(true);
    } catch {
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: "无法访问麦克风，请检查浏览器权限设置。", timestamp: Date.now() }]);
    }
  }, [isRecording]);

  // ── 输入框键盘处理 ──
  const handleInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashOpen) {
      // 与 SlashCommandPanel 保持一致的过滤逻辑（包含 description）
      const q = slashFilter.toLowerCase();
      const filtered = slashCommands.filter((c) =>
        c.id.includes(q) || c.label.includes(q) || c.description.includes(q),
      );
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashSelectedIdx((i) => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashSelectedIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const cmd = filtered[slashSelectedIdx];
        if (cmd) {
          cmd.action("");
          setInputText("");
          setSlashOpen(false);
        }
      } else if (e.key === "Escape") {
        setSlashOpen(false);
      }
      return;
    }

    // Ctrl+Enter or Cmd+Enter to send
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendMessage();
    }
    // Enter without shift to send (single line mode)
    else if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [slashOpen, slashFilter, slashCommands, slashSelectedIdx, sendMessage]);

  // ── 输入变化处理 ──
  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInputText(val);

    // 斜杠命令检测
    if (val.startsWith("/") && !val.includes(" ")) {
      setSlashOpen(true);
      setSlashFilter(val.slice(1));
      setSlashSelectedIdx(0);
    } else {
      setSlashOpen(false);
    }
  }, []);

  // ── 未启动服务提示 ──
  if (!serviceRunning) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "60px 40px" }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}><IconMessageCircle size={48} /></div>
        <div className="cardTitle">{t("chat.title")}</div>
        <div className="cardHint" style={{ marginTop: 8, marginBottom: 20 }}>
          {t("chat.serviceHint")}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0 }}>
      {/* 对话历史侧边栏 */}
      {sidebarOpen && (
        <div
          style={{
            width: 260,
            minWidth: 260,
            borderRight: "1px solid var(--line)",
            background: "rgba(255,255,255,0.6)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div style={{ padding: "14px 14px 10px", borderBottom: "1px solid var(--line)" }}>
            <button className="btnPrimary" onClick={newConversation} style={{ width: "100%", fontSize: 13 }}>
              <IconPlus size={12} /> {t("chat.newConversation")}
            </button>
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: "8px 6px" }}>
            {conversations.map((conv) => (
              <div
                key={conv.id}
                onClick={() => {
                  setActiveConvId(conv.id);
                  // TODO: 从 API 加载对话消息
                }}
                style={{
                  padding: "10px 12px",
                  borderRadius: 10,
                  cursor: "pointer",
                  marginBottom: 4,
                  background: conv.id === activeConvId ? "rgba(14,165,233,0.08)" : "transparent",
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {conv.title}
                </div>
                <div style={{ fontSize: 11, opacity: 0.5, marginTop: 2 }}>
                  {formatDate(conv.timestamp)} · {t("im.messageCount", { count: conv.messageCount })}
                </div>
              </div>
            ))}
            {conversations.length === 0 && (
              <div style={{ padding: 16, textAlign: "center", opacity: 0.4, fontSize: 13 }}>
                {t("common.noData")}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 主聊天区 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Chat top bar */}
        <div className="chatTopBar">
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            className="chatTopBarBtn"
            style={{ background: sidebarOpen ? "rgba(14,165,233,0.08)" : "transparent" }}
            title={t("chat.newConversation")}
          >
            <IconMenu size={16} />
          </button>
          <div style={{ flex: 1 }} />
          <button onClick={newConversation} className="chatTopBarBtn">
            <IconPlus size={14} /> <span>{t("chat.newConversation")}</span>
          </button>
        </div>

        {/* 消息列表 */}
        <div style={{ flex: 1, overflow: "auto", padding: "16px 20px", minHeight: 0 }}>
          {messages.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", opacity: 0.4 }}>
              <div style={{ marginBottom: 12 }}><IconMessageCircle size={48} /></div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>{t("chat.emptyTitle")}</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>{t("chat.emptyDesc")}</div>
            </div>
          )}
          {messages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} onAskAnswer={handleAskAnswer} apiBaseUrl={apiBaseUrl} />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* 附件预览栏 */}
        {pendingAttachments.length > 0 && (
          <div style={{ padding: "12px 16px 8px", borderTop: "1px solid var(--line)", display: "flex", flexWrap: "wrap", gap: 12, background: "rgba(255,255,255,0.5)" }}>
            {pendingAttachments.map((att, idx) => (
              <AttachmentPreview
                key={`${att.name}-${att.type}-${idx}`}
                att={att}
                onRemove={() => setPendingAttachments((prev) => prev.filter((_, i) => i !== idx))}
              />
            ))}
          </div>
        )}

        {/* Cursor-style unified input box */}
        <div
          className="chatInputArea"
          style={dragOver ? { outline: "2px dashed var(--brand)", outlineOffset: -2, background: "rgba(37,99,235,0.04)", borderRadius: 16 } : undefined}
        >
          {/* Slash command panel */}
          {slashOpen && (
            <SlashCommandPanel
              commands={slashCommands}
              filter={slashFilter}
              onSelect={(cmd) => {
                cmd.action("");
                setInputText("");
                setSlashOpen(false);
              }}
              selectedIdx={slashSelectedIdx}
            />
          )}

          <div className={`chatInputBox ${planMode ? "chatInputBoxPlan" : ""}`}>
            {/* Top row: compact model picker */}
            <div className="chatInputTop" ref={modelMenuRef} style={{ position: "relative" }}>
              <button
                className="chatModelPickerBtn"
                onClick={() => setModelMenuOpen((v) => !v)}
              >
                <span className="chatModelPickerLabel">
                  {selectedEndpoint === "auto"
                    ? t("chat.selectModel")
                    : (() => { const ep = endpoints.find(e => e.name === selectedEndpoint); return ep ? ep.model : selectedEndpoint; })()}
                </span>
                <IconChevronDown size={12} />
              </button>
              {modelMenuOpen && (
                <div className="chatModelMenu">
                  <div
                    className={`chatModelMenuItem ${selectedEndpoint === "auto" ? "chatModelMenuItemActive" : ""}`}
                    onClick={() => { setSelectedEndpoint("auto"); setModelMenuOpen(false); }}
                  >
                    {t("chat.selectModel")}
                  </div>
                  {endpoints.map((ep) => (
                    <div
                      key={ep.name}
                      className={`chatModelMenuItem ${selectedEndpoint === ep.name ? "chatModelMenuItemActive" : ""}`}
                      onClick={() => { setSelectedEndpoint(ep.name); setModelMenuOpen(false); }}
                    >
                      <span style={{ fontWeight: 600 }}>{ep.model}</span>
                      <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{ep.name}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Textarea */}
            <textarea
              ref={inputRef}
              value={inputText}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              onPaste={handlePaste}
              placeholder={planMode ? `Plan ${t("chat.planMode")}` : t("chat.placeholder")}
              rows={1}
              className="chatInputTextarea"
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = "auto";
                el.style.height = Math.min(el.scrollHeight, 120) + "px";
              }}
            />

            {/* Bottom toolbar */}
            <div className="chatInputToolbar">
              <div className="chatInputToolbarLeft">
                <button onClick={() => fileInputRef.current?.click()} className="chatInputIconBtn" title={t("chat.attach")}>
                  <IconPaperclip size={16} />
                </button>
                <input ref={fileInputRef} type="file" multiple accept="image/*,audio/*,.pdf,.txt,.md,.py,.js,.ts,.json,.csv" style={{ display: "none" }} onChange={handleFileSelect} />

                <button onClick={toggleRecording} className={`chatInputIconBtn ${isRecording ? "chatInputIconBtnDanger" : ""}`} title={isRecording ? t("chat.stopRecording") : t("chat.voice")}>
                  {isRecording ? <IconStopCircle size={16} /> : <IconMic size={16} />}
                </button>

                <button onClick={() => setPlanMode((v) => !v)} className={`chatInputIconBtn ${planMode ? "chatInputIconBtnActive" : ""}`} title={t("chat.planMode")}>
                  <IconPlan size={16} />
                  <span style={{ fontSize: 11, marginLeft: 2 }}>Plan</span>
                </button>
              </div>

              <div className="chatInputToolbarRight">
                {isStreaming ? (
                  <button onClick={stopStreaming} className="chatInputSendBtn chatInputStopBtn" title={t("chat.stopGeneration")}>
                    <IconStop size={14} />
                  </button>
                ) : (
                  <button
                    onClick={() => sendMessage()}
                    className="chatInputSendBtn"
                    disabled={!inputText.trim() && pendingAttachments.length === 0}
                    title={t("chat.send")}
                  >
                    <IconSend size={14} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
