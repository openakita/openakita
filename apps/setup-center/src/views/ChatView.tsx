// ─── ChatView: 完整 AI 聊天页面 ───
// 支持流式 MD 渲染、思考内容折叠、Plan/Todo、斜杠命令、多模态、多 Agent、端点选择

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
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
  SlashCommand,
  EndpointSummary,
} from "../types";
import { genId, formatTime, formatDate } from "../utils";

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
  | { type: "error"; message: string }
  | { type: "done"; usage?: { input_tokens: number; output_tokens: number } };

// ─── 子组件 ───

function ThinkingBlock({ content, defaultOpen }: { content: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="thinkingBlock">
      <div
        className="thinkingHeader"
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, padding: "6px 0", userSelect: "none" }}
      >
        <span style={{ fontSize: 12, opacity: 0.5, transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s" }}>▶</span>
        <span style={{ fontWeight: 700, fontSize: 13, opacity: 0.6 }}>思考过程</span>
      </div>
      {open && (
        <div style={{ padding: "8px 12px", background: "rgba(124,58,237,0.04)", borderRadius: 10, fontSize: 13, lineHeight: 1.6, opacity: 0.75, whiteSpace: "pre-wrap" }}>
          {content}
        </div>
      )}
    </div>
  );
}

function ToolCallBlock({ tc }: { tc: ChatToolCall }) {
  const [open, setOpen] = useState(false);
  const statusIcon = tc.status === "done" ? "✓" : tc.status === "error" ? "✗" : tc.status === "running" ? "⟳" : "…";
  const statusColor = tc.status === "done" ? "var(--ok)" : tc.status === "error" ? "var(--danger)" : "var(--brand)";
  return (
    <div style={{ margin: "6px 0", border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", background: "rgba(14,165,233,0.04)", userSelect: "none" }}
      >
        <span style={{ color: statusColor, fontWeight: 800 }}>{statusIcon}</span>
        <span style={{ fontWeight: 700, fontSize: 13 }}>工具调用：{tc.tool}</span>
        <span style={{ fontSize: 11, opacity: 0.5, marginLeft: "auto" }}>{open ? "收起" : "展开"}</span>
      </div>
      {open && (
        <div style={{ padding: "8px 12px", fontSize: 12, background: "rgba(255,255,255,0.5)" }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>参数：</div>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11 }}>
            {JSON.stringify(tc.args, null, 2)}
          </pre>
          {tc.result != null && (
            <>
              <div style={{ fontWeight: 700, marginTop: 8, marginBottom: 4 }}>结果：</div>
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

function PlanBlock({ plan }: { plan: ChatPlan }) {
  const completed = plan.steps.filter((s) => s.status === "completed").length;
  const total = plan.steps.length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  return (
    <div style={{ margin: "8px 0", border: "1px solid rgba(14,165,233,0.2)", borderRadius: 12, padding: "12px 14px", background: "rgba(14,165,233,0.03)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontWeight: 800, fontSize: 14 }}>计划：{plan.taskSummary}</span>
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
  const icon = step.status === "completed" ? "✓" : step.status === "in_progress" ? "▶" : step.status === "skipped" ? "—" : "○";
  const color =
    step.status === "completed" ? "rgba(16,185,129,1)" : step.status === "in_progress" ? "var(--brand)" : step.status === "skipped" ? "var(--muted)" : "var(--muted)";
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "4px 0", fontSize: 13 }}>
      <span style={{ color, fontWeight: 800, minWidth: 16, textAlign: "center" }}>{icon}</span>
      <div style={{ flex: 1 }}>
        <span style={{ opacity: step.status === "skipped" ? 0.5 : 1 }}>{idx + 1}. {step.description}</span>
        {step.result && <div style={{ fontSize: 11, opacity: 0.6, marginTop: 2 }}>{step.result}</div>}
      </div>
    </div>
  );
}

function AskUserBlock({ ask, onAnswer }: { ask: ChatAskUser; onAnswer: (answer: string) => void }) {
  const [input, setInput] = useState("");
  if (ask.answered) {
    return (
      <div style={{ margin: "8px 0", padding: "10px 14px", borderRadius: 10, background: "rgba(14,165,233,0.06)", border: "1px solid rgba(14,165,233,0.15)" }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>{ask.question}</div>
        <div style={{ fontSize: 13, opacity: 0.7 }}>已回答：{ask.answer}</div>
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
            placeholder="输入回答..."
            style={{ flex: 1, fontSize: 13 }}
            onKeyDown={(e) => { if (e.key === "Enter" && input.trim()) { onAnswer(input.trim()); setInput(""); } }}
          />
          <button className="btnPrimary" onClick={() => { if (input.trim()) { onAnswer(input.trim()); setInput(""); } }} style={{ fontSize: 13, padding: "6px 16px" }}>
            发送
          </button>
        </div>
      )}
    </div>
  );
}

function AttachmentPreview({ att }: { att: ChatAttachment }) {
  if (att.type === "image" && att.previewUrl) {
    return (
      <div style={{ display: "inline-block", margin: "4px 4px 4px 0", borderRadius: 8, overflow: "hidden", border: "1px solid var(--line)" }}>
        <img src={att.previewUrl} alt={att.name} style={{ maxWidth: 200, maxHeight: 150, display: "block" }} />
      </div>
    );
  }
  const icon = att.type === "voice" ? "🎤" : att.type === "image" ? "🖼" : "📎";
  const sizeStr = att.size ? `${(att.size / 1024).toFixed(1)} KB` : "";
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 8, border: "1px solid var(--line)", fontSize: 12, margin: "4px 4px 4px 0" }}>
      <span>{icon}</span>
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
          <span style={{ fontSize: 16, opacity: 0.7 }}>{cmd.icon || "/"}</span>
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
}: {
  msg: ChatMessage;
  onAskAnswer?: (msgId: string, answer: string) => void;
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

        {/* Tool calls */}
        {msg.toolCalls && msg.toolCalls.map((tc, i) => (
          <ToolCallBlock key={i} tc={tc} />
        ))}

        {/* Plan */}
        {msg.plan && <PlanBlock plan={msg.plan} />}

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
  // ── 状态 ──
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

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
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

  // ── 斜杠命令定义 ──
  const slashCommands: SlashCommand[] = useMemo(() => [
    { id: "model", label: "切换模型", description: "选择使用的 LLM 端点", icon: "🔄", action: (args) => {
      if (args && endpoints.find((e) => e.name === args)) {
        setSelectedEndpoint(args);
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `已切换到端点: ${args}`, timestamp: Date.now() }]);
      } else {
        const names = ["auto", ...endpoints.map((e) => e.name)];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `可用端点: ${names.join(", ")}\n用法: /model <端点名>`, timestamp: Date.now() }]);
      }
    }},
    { id: "plan", label: "计划模式", description: "开启/关闭 Plan 模式，先计划再执行", icon: "📋", action: () => {
      setPlanMode((v) => {
        const next = !v;
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: next ? "已开启 Plan 模式" : "已关闭 Plan 模式", timestamp: Date.now() }]);
        return next;
      });
    }},
    { id: "clear", label: "清空对话", description: "清除当前对话的所有消息", icon: "🗑", action: () => { setMessages([]); } },
    { id: "skill", label: "使用技能", description: "调用已安装的技能（发送 /skill:<技能名> 触发）", icon: "⚡", action: (args) => {
      if (args) {
        setInputText(`请使用技能「${args}」来帮我：`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /skill <技能名>，如 /skill web-search。在消息中提及技能名即可触发。", timestamp: Date.now() }]);
      }
    }},
    { id: "persona", label: "切换角色", description: "切换 Agent 的人格预设", icon: "🎭", action: (args) => {
      if (args) {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `角色切换请在「设置 → Agent 系统」中修改 PERSONA_NAME 为 "${args}"`, timestamp: Date.now() }]);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "可用角色: default, business, tech_expert, butler, girlfriend, boyfriend, family, jarvis\n用法: /persona <角色ID>", timestamp: Date.now() }]);
      }
    }},
    { id: "agent", label: "切换 Agent", description: "在多 Agent 间切换（handoff 模式）", icon: "🤖", action: (args) => {
      if (args) {
        setInputText(`请切换到 Agent「${args}」来处理接下来的任务。`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /agent <Agent名称>。在 handoff 模式下，AI 会自动在 Agent 间切换。", timestamp: Date.now() }]);
      }
    }},
    { id: "agents", label: "查看 Agent 列表", description: "显示可用的 Agent 列表", icon: "👥", action: () => {
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: "Agent 列表取决于 handoff 配置。当前可通过 /agent <名称> 手动请求切换。", timestamp: Date.now() }]);
    }},
    { id: "help", label: "帮助", description: "显示可用命令列表", icon: "❓", action: () => {
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

    // 连接超时：2 分钟内如果没有收到任何数据则放弃
    const connectTimeout = setTimeout(() => abort.abort(), 120_000);

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

      // 处理 SSE 流
      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";
      let currentContent = "";
      let currentThinking = "";
      let isThinking = false;
      let currentToolCalls: ChatToolCall[] = [];
      let currentPlan: ChatPlan | null = null;
      let currentAsk: ChatAskUser | null = null;
      let currentAgent: string | null = null;

      while (true) {
        const { done, value } = await reader.read();

        if (value) {
          buffer += decoder.decode(value, { stream: true });
        }
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
                // 处理此事件（简化版：仅处理 text_delta 和 done）
                if (event.type === "text_delta") currentContent += event.content;
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
                // 优先用 id 精确匹配（支持同一工具的并行调用），fallback 到名称匹配
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
                  const newSteps = [...currentPlan.steps];
                  if (newSteps[event.stepIdx]) {
                    newSteps[event.stepIdx] = { ...newSteps[event.stepIdx], status: event.status as ChatPlanStep["status"] };
                  }
                  currentPlan = { ...currentPlan, steps: newSteps };
                }
                break;
              case "ask_user":
                currentAsk = { question: event.question, options: event.options };
                break;
              case "agent_switch":
                currentAgent = event.agentName;
                // 添加一条系统提示
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

      // 完成流式
      setMessages((prev) => prev.map((m) =>
        m.id === assistantMsg.id ? { ...m, streaming: false } : m
      ));
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") {
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
      clearTimeout(connectTimeout);
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
    abortRef.current?.abort();
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
        <div style={{ fontSize: 48, marginBottom: 16 }}>💬</div>
        <div className="cardTitle">AI 聊天助手</div>
        <div className="cardHint" style={{ marginTop: 8, marginBottom: 20 }}>
          聊天功能需要后台服务（openakita serve）运行中。
          <br />
          请先启动服务后再使用。
        </div>
        <button className="btnPrimary" onClick={onStartService}>
          启动服务
        </button>
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
              + 新对话
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
                  {formatDate(conv.timestamp)} · {conv.messageCount} 条消息
                </div>
              </div>
            ))}
            {conversations.length === 0 && (
              <div style={{ padding: 16, textAlign: "center", opacity: 0.4, fontSize: 13 }}>
                暂无对话
              </div>
            )}
          </div>
        </div>
      )}

      {/* 主聊天区 */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* 顶栏 */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 16px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
          <button
            onClick={() => setSidebarOpen((v) => !v)}
            style={{ padding: "4px 8px", borderRadius: 8, border: "1px solid var(--line)", background: sidebarOpen ? "rgba(14,165,233,0.08)" : "transparent", cursor: "pointer", fontSize: 14 }}
            title="对话历史"
          >
            ☰
          </button>

          {/* 端点选择 */}
          <select
            value={selectedEndpoint}
            onChange={(e) => setSelectedEndpoint(e.target.value)}
            style={{ fontSize: 13, padding: "4px 10px", borderRadius: 8, border: "1px solid var(--line)", background: "rgba(255,255,255,0.7)" }}
          >
            <option value="auto">Auto（自动选择/回退）</option>
            {endpoints.map((ep) => (
              <option key={ep.name} value={ep.name}>
                {ep.name} — {ep.model}
                {ep.health ? (ep.health.status === "healthy" ? " ●" : ep.health.status === "unhealthy" ? " ○" : "") : ""}
              </option>
            ))}
          </select>

          <div style={{ flex: 1 }} />

          <button
            onClick={newConversation}
            style={{ padding: "4px 12px", borderRadius: 8, border: "1px solid var(--line)", background: "transparent", cursor: "pointer", fontSize: 13, fontWeight: 700 }}
          >
            + 新对话
          </button>
        </div>

        {/* 消息列表 */}
        <div style={{ flex: 1, overflow: "auto", padding: "16px 20px", minHeight: 0 }}>
          {messages.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", opacity: 0.4 }}>
              <div style={{ fontSize: 48, marginBottom: 12 }}>💬</div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>开始聊天</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>输入消息或使用 / 命令</div>
            </div>
          )}
          {messages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} onAskAnswer={handleAskAnswer} />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* 附件预览栏 */}
        {pendingAttachments.length > 0 && (
          <div style={{ padding: "8px 16px", borderTop: "1px solid var(--line)", display: "flex", flexWrap: "wrap", gap: 6, background: "rgba(255,255,255,0.5)" }}>
            {pendingAttachments.map((att, idx) => (
              <div key={`${att.name}-${att.type}-${idx}`} style={{ position: "relative" }}>
                <AttachmentPreview att={att} />
                <button
                  onClick={() => setPendingAttachments((prev) => prev.filter((_, i) => i !== idx))}
                  style={{
                    position: "absolute",
                    top: -4,
                    right: -4,
                    width: 18,
                    height: 18,
                    borderRadius: 9,
                    border: "none",
                    background: "var(--danger)",
                    color: "#fff",
                    fontSize: 10,
                    cursor: "pointer",
                    display: "grid",
                    placeItems: "center",
                    lineHeight: 1,
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 输入区 */}
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--line)", flexShrink: 0, position: "relative" }}>
          {/* 斜杠命令面板 */}
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

          <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
            {/* 附件按钮 */}
            <button
              onClick={() => fileInputRef.current?.click()}
              style={{ padding: "8px", borderRadius: 8, border: "1px solid var(--line)", background: "transparent", cursor: "pointer", fontSize: 16, flexShrink: 0 }}
              title="添加附件（图片/文件/语音）"
            >
              📎
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*,audio/*,.pdf,.txt,.md,.py,.js,.ts,.json,.csv"
              style={{ display: "none" }}
              onChange={handleFileSelect}
            />

            {/* 语音录制按钮 */}
            <button
              onClick={toggleRecording}
              style={{
                padding: "8px",
                borderRadius: 8,
                border: `1px solid ${isRecording ? "rgba(239,68,68,0.4)" : "var(--line)"}`,
                background: isRecording ? "rgba(239,68,68,0.1)" : "transparent",
                cursor: "pointer",
                fontSize: 16,
                flexShrink: 0,
                animation: isRecording ? "pulse 1.5s infinite" : "none",
              }}
              title={isRecording ? "停止录音" : "开始录音"}
            >
              {isRecording ? "⏹" : "🎤"}
            </button>

            {/* 输入框 */}
            <div style={{ flex: 1, position: "relative" }}>
              <textarea
                ref={inputRef}
                value={inputText}
                onChange={handleInputChange}
                onKeyDown={handleInputKeyDown}
                onPaste={handlePaste}
                placeholder={planMode ? "Plan 模式 — 描述任务，AI 会先制定计划再执行..." : "输入消息... (Enter 发送, Shift+Enter 换行, / 命令)"}
                rows={1}
                style={{
                  width: "100%",
                  resize: "none",
                  padding: "10px 14px",
                  borderRadius: 12,
                  border: `1px solid ${planMode ? "rgba(124,58,237,0.3)" : "var(--line)"}`,
                  background: planMode ? "rgba(124,58,237,0.03)" : "rgba(255,255,255,0.7)",
                  fontSize: 14,
                  lineHeight: 1.5,
                  fontFamily: "inherit",
                  maxHeight: 120,
                  overflow: "auto",
                }}
                onInput={(e) => {
                  const el = e.currentTarget;
                  el.style.height = "auto";
                  el.style.height = Math.min(el.scrollHeight, 120) + "px";
                }}
              />
            </div>

            {/* Plan 模式切换 */}
            <button
              onClick={() => setPlanMode((v) => !v)}
              style={{
                padding: "8px 12px",
                borderRadius: 8,
                border: `1px solid ${planMode ? "rgba(124,58,237,0.4)" : "var(--line)"}`,
                background: planMode ? "rgba(124,58,237,0.1)" : "transparent",
                cursor: "pointer",
                fontSize: 12,
                fontWeight: 700,
                color: planMode ? "rgba(124,58,237,1)" : "var(--muted)",
                flexShrink: 0,
              }}
              title="Plan 模式：开启后 AI 会先制定计划再执行"
            >
              Plan
            </button>

            {/* 发送/停止按钮 */}
            {isStreaming ? (
              <button
                onClick={stopStreaming}
                className="btnDanger"
                style={{ padding: "8px 16px", borderRadius: 10, fontSize: 13, fontWeight: 700, flexShrink: 0 }}
              >
                停止
              </button>
            ) : (
              <button
                onClick={() => sendMessage()}
                className="btnPrimary"
                style={{ padding: "8px 16px", borderRadius: 10, fontSize: 13, fontWeight: 700, flexShrink: 0 }}
                disabled={!inputText.trim() && pendingAttachments.length === 0}
              >
                发送
              </button>
            )}
          </div>

          {/* 端点提醒 */}
          {selectedEndpoint !== "auto" && (
            <div style={{ fontSize: 11, opacity: 0.5, marginTop: 6, paddingLeft: 40 }}>
              指定端点模式：使用 {selectedEndpoint}，出错不自动回退
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
