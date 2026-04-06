// ─── ChatView: 完整 AI 聊天页面 ───
// 组装层: 通过 hooks + 子组件构建完整聊天界面

import { useEffect, useMemo, useRef, useState, useCallback, memo } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { setThemePref } from "../theme";
import type { Theme } from "../theme";
import { invoke, downloadFile, openFileWithDefault, showInFolder, readFileBase64, onDragDrop, IS_TAURI, IS_WEB, IS_MOBILE_BROWSER, onWsEvent, logger, getAssetUrl } from "../platform";
import { getAccessToken } from "../platform/auth";
import { safeFetch } from "../providers";
import type {
  ChatMessage,
  ChatErrorInfo,
  ChatConversation,
  ConversationStatus,
  ChatToolCall,
  ChatTodo,
  ChatTodoStep,
  ChatAskUser,
  ChatAskQuestion,
  ChatAttachment,
  ChatArtifact,
  SlashCommand,
  EndpointSummary,
  ChainGroup,
  ChainToolCall,
  ChainEntry,
  ChainSummaryItem,
  ChatDisplayMode,
  PlanApprovalEvent,
} from "../types";
import { genId, formatTime, formatDate, timeAgo } from "../utils";
import { notifyError } from "../utils/notify";
import { ErrorBoundary } from "../components/ErrorBoundary";
import {
  IconSend, IconPaperclip, IconMic, IconStopCircle,
  IconPlan, IconPlus, IconMenu, IconStop, IconX,
  IconCheck, IconLoader, IconCircle, IconPlay, IconMinus,
  IconChevronDown, IconChevronUp, IconMessageCircle, IconChevronRight,
  IconImage, IconRefresh, IconClipboard, IconTrash, IconZap,
  IconMask, IconBot, IconUsers, IconHelp, IconEdit, IconDownload,
  IconPin, IconSearch, IconCircleDot, IconXCircle,
  IconBuilding, IconShield, IconAlertCircle,
  getFileTypeIcon,
} from "../icons";

// ─── Chat module imports ───
import type {
  MdModules, QueuedMessage, StreamEvent,
  SubAgentEntry, SubAgentTask, StreamContext, AgentProfile,
} from "./chat/utils/chatTypes";
import {
  STORAGE_KEY_CONVS, STORAGE_KEY_ACTIVE, STORAGE_KEY_MSGS_PREFIX,
  IDLE_THRESHOLD_MS, IDLE_TOKEN_THRESHOLD, PASTE_CHAR_THRESHOLD, UNDO_MAX_STEPS,
  exportConversation, appendAuthToken, stripLegacySummary,
  sanitizeStoredMessages, loadMessagesFromStorage, saveMessagesToStorage,
  buildChainFromSummary, formatAskUserAnswer, patchMessagesWithBackend,
  classifyError, basename, formatToolDescription, generateGroupSummary,
  ERROR_META, SVG_PATHS, getNextSpinnerTip,
} from "./chat/utils/chatHelpers";
import { useMdModules } from "./chat/hooks/useMdModules";
import { useMessageReducer, useConversationReducer } from "./chat/hooks/useMessages";
import type { MessageAction, ConversationAction } from "./chat/hooks/useMessages";
import { useQueryGuard } from "./chat/hooks/useQueryGuard";
import type { QueryState } from "./chat/hooks/useQueryGuard";
import {
  SpinnerTipDisplay, AttachmentPreview, ErrorCard,
  ThinkingBlock, ToolCallDetail, ToolCallsGroup, ThinkingChain,
  FloatingPlanBar, AskUserBlock, ArtifactList, PlanApprovalPanel,
  SlashCommandPanel, RenderIcon, SubAgentCards,
  SecurityConfirmModal, ContextMenuInner, LightboxOverlay,
  MessageBubble, FlatMessageItem,
  MessageList,
} from "./chat/components";
import type { MessageListHandle } from "./chat/components";

// ─── 主组件 ───

export function ChatView({
  serviceRunning,
  endpoints,
  onStartService,
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
  multiAgentEnabled = false,
}: {
  serviceRunning: boolean;
  endpoints: EndpointSummary[];
  onStartService: () => void;
  apiBaseUrl?: string;
  visible?: boolean;
  multiAgentEnabled?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const mdModules = useMdModules();

  // ── State（useReducer 集中管理，从 localStorage 恢复） ──
  const { messages, dispatch: msgDispatch, messagesRef: latestMessagesRef } = useMessageReducer();
  const { conversations, dispatch: convDispatch, conversationsRef: latestConversationsRef } = useConversationReducer();
  const queryGuard = useQueryGuard();

  // 向后兼容别名：逐步迁移后可移除
  const setMessages = useCallback((arg: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => {
    if (typeof arg === "function") {
      const next = arg(latestMessagesRef.current);
      msgDispatch({ type: "SET_ALL", messages: next });
    } else {
      msgDispatch({ type: "SET_ALL", messages: arg });
    }
  }, [msgDispatch, latestMessagesRef]);

  const setConversations = useCallback((arg: ChatConversation[] | ((prev: ChatConversation[]) => ChatConversation[])) => {
    if (typeof arg === "function") {
      convDispatch({ type: "SET_ALL", conversations: arg(latestConversationsRef.current) });
    } else {
      convDispatch({ type: "SET_ALL", conversations: arg });
    }
  }, [convDispatch, latestConversationsRef]);

  const [activeConvId, setActiveConvId] = useState<string | null>(() => {
    try { return localStorage.getItem(STORAGE_KEY_ACTIVE) || null; }
    catch { return null; }
  });
  const [hydrating, setHydrating] = useState(false);
  const inputTextRef = useRef("");
  const [hasInputText, setHasInputText] = useState(false);
  const [selectedEndpoint, setSelectedEndpoint] = useState("auto");
  const [chatMode, setChatMode] = useState<"agent" | "plan" | "ask">("agent");
  const planMode = chatMode === "plan";
  const [pendingApproval, setPendingApproval] = useState<PlanApprovalEvent | null>(null);
  const pendingApprovalRef = useRef<PlanApprovalEvent | null>(null);
  const [streamingTick, setStreamingTick] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(() => typeof window !== "undefined" && window.innerWidth > 768);
  const [sidebarPinned, setSidebarPinned] = useState(() => {
    try { return localStorage.getItem("openakita_convSidebarPinned") === "true"; } catch { return false; }
  });
  const [convSearchQuery, setConvSearchQuery] = useState("");
  const [orbitTip, setOrbitTip] = useState<{ x: number; y: number; name: string; title: string } | null>(null);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashFilter, setSlashFilter] = useState("");
  const [slashSelectedIdx, setSlashSelectedIdx] = useState(0);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [msgSearchOpen, setMsgSearchOpen] = useState(false);
  const [msgSearchQuery, setMsgSearchQuery] = useState("");
  const [msgSearchIdx, setMsgSearchIdx] = useState(0);
  const msgSearchRef = useRef<HTMLInputElement | null>(null);
  const messageListRef = useRef<MessageListHandle>(null);
  const isMessageListAtBottomRef = useRef(true);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);
  const [lightbox, setLightbox] = useState<{ url: string; downloadUrl: string; name: string } | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  type SecurityConfirmData = {
    tool: string; args: Record<string, unknown>; reason: string;
    riskLevel: string; needsSandbox: boolean; toolId?: string;
    countdown: number;
  };
  const [securityConfirm, setSecurityConfirm] = useState<SecurityConfirmData | null>(null);
  const securityQueueRef = useRef<SecurityConfirmData[]>([]);
  const securityTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const handleSecurityClose = useCallback(() => {
    if (securityTimerRef.current) clearInterval(securityTimerRef.current);
    const next = securityQueueRef.current.shift();
    setSecurityConfirm(next ?? null);
  }, []);
  const [winSize, setWinSize] = useState({ w: window.innerWidth, h: window.innerHeight });
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setLightbox(null); };
    const onResize = () => setWinSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("resize", onResize); };
  }, [lightbox]);

  // 思维链 & 显示模式（从 localStorage 恢复用户习惯）
  const [showChain, setShowChain] = useState(() => {
    try { const v = localStorage.getItem("chat_showChain"); return v !== null ? v === "true" : true; }
    catch { return true; }
  });
  const [displayMode, setDisplayMode] = useState<ChatDisplayMode>(() => {
    try { const v = localStorage.getItem("chat_displayMode"); return (v === "bubble" || v === "flat") ? v : "flat"; }
    catch { return "flat"; }
  });

  // 持久化用户偏好
  useEffect(() => { try { localStorage.setItem("chat_showChain", String(showChain)); } catch {} }, [showChain]);
  useEffect(() => { try { localStorage.setItem("chat_displayMode", displayMode); } catch {} }, [displayMode]);

  const [isRecording, setIsRecording] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement | null>(null);

  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const modeMenuRef = useRef<HTMLDivElement | null>(null);

  const [agentProfiles, setAgentProfiles] = useState<AgentProfile[]>([]);
  const [selectedAgent, setSelectedAgent] = useState("default");
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const agentMenuRef = useRef<HTMLDivElement | null>(null);

  // ── Org mode state ──
  const [orgMode, setOrgMode] = useState(false);
  const [orgList, setOrgList] = useState<{id: string; name: string; icon: string; status: string}[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [selectedOrgNodeId, setSelectedOrgNodeId] = useState<string | null>(null);
  const [orgMenuOpen, setOrgMenuOpen] = useState(false);
  const orgMenuRef = useRef<HTMLDivElement | null>(null);
  const [orgCommandPending, setOrgCommandPending] = useState(false);
  const orgCommandPendingRef = useRef(false);

  // Org 协调可视化面板状态
  const [orgNodeStates, setOrgNodeStates] = useState<Map<string, { status: string; task?: string; ts: number }>>(new Map());
  const [orgFlowPanelOpen, setOrgFlowPanelOpen] = useState(true);
  const [orgDelegations, setOrgDelegations] = useState<{ from: string; to: string; task: string; ts: number }[]>([]);

  useEffect(() => {
    if (!orgMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (orgMenuRef.current && !orgMenuRef.current.contains(e.target as HTMLElement)) {
        setOrgMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [orgMenuOpen]);

  useEffect(() => {
    const handler = (e: Event) => {
      const { orgId, nodeId } = (e as CustomEvent).detail ?? {};
      if (!orgId) return;
      setOrgMode(true);
      setSelectedOrgId(orgId);
      setSelectedOrgNodeId(nodeId ?? null);
    };
    window.addEventListener("openakita_activate_org", handler);
    return () => window.removeEventListener("openakita_activate_org", handler);
  }, []);

  const [displayActiveSubAgents, setDisplayActiveSubAgents] = useState<SubAgentEntry[]>([]);
  const [displaySubAgentTasks, setDisplaySubAgentTasks] = useState<SubAgentTask[]>([]);

  // ── Per-session streaming context (supports concurrent streams) ──
  const streamContexts = useRef<Map<string, StreamContext>>(new Map());
  const activeConvIdRef = useRef(activeConvId);
  const isCurrentConvStreaming = streamContexts.current.get(activeConvId ?? "")?.isStreaming ?? false;

  // ── Multi-device busy lock ──
  const clientIdRef = useRef(() => {
    let id = sessionStorage.getItem("openakita_client_id");
    if (!id) {
      id = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : genId();
      sessionStorage.setItem("openakita_client_id", id);
    }
    return id;
  });
  const getClientId = useCallback(() => clientIdRef.current(), []);
  const [busyConversations, setBusyConversations] = useState<Map<string, string>>(new Map());
  const busyConvRef = useRef(busyConversations);
  busyConvRef.current = busyConversations;

  // ── IM 通道状态告警 ──
  const [imChannelAlerts, setImChannelAlerts] = useState<{ channel: string; status: string; ts: number }[]>([]);

  const isConvBusyOnOtherDevice = useCallback((convId: string) => {
    const busyClientId = busyConvRef.current.get(convId);
    return !!busyClientId && busyClientId !== getClientId();
  }, [getClientId]);

  const updateConvStatus = useCallback((convId: string, status: ConversationStatus) => {
    setConversations((prev) =>
      prev.map((c) => c.id === convId ? { ...c, status, timestamp: Date.now() } : c)
    );
  }, []);

  // 会话右键菜单 & 重命名
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; convId: string } | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  useEffect(() => {
    if (!ctxMenu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setCtxMenu(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ctxMenu]);

  // 深度思考模式 & 深度（从 localStorage 恢复用户习惯）
  const [thinkingMode, setThinkingMode] = useState<"auto" | "on" | "off">(() => {
    try { const v = localStorage.getItem("chat_thinkingMode"); return (v === "on" || v === "off") ? v : "auto"; }
    catch { return "auto"; }
  });
  const [thinkingDepth, setThinkingDepth] = useState<"low" | "medium" | "high">(() => {
    try { const v = localStorage.getItem("chat_thinkingDepth"); return (v === "low" || v === "medium" || v === "high") ? v : "medium"; }
    catch { return "medium"; }
  });
  const [thinkingModeTipOpen, setThinkingModeTipOpen] = useState(false);
  const [thinkingDepthTipOpen, setThinkingDepthTipOpen] = useState(false);

  // 持久化思考偏好
  useEffect(() => { try { localStorage.setItem("chat_thinkingMode", thinkingMode); } catch {} }, [thinkingMode]);
  useEffect(() => { try { localStorage.setItem("chat_thinkingDepth", thinkingDepth); } catch {} }, [thinkingDepth]);

  // ── 上下文占用追踪 ──
  const [contextTokens, setContextTokens] = useState(0);
  const [contextLimit, setContextLimit] = useState(0);
  const [contextTooltipVisible, setContextTooltipVisible] = useState(false);

  // ── 长闲置回归检测 (6.7) ──
  const lastActivityRef = useRef(Date.now());
  const [idleReturnPrompt, setIdleReturnPrompt] = useState(false);
  const contextTokensRef = useRef(contextTokens);
  contextTokensRef.current = contextTokens;

  useEffect(() => {
    lastActivityRef.current = Date.now();
    setIdleReturnPrompt(false);
  }, [messages.length, activeConvId]);

  useEffect(() => {
    const iv = setInterval(() => {
      const idle = Date.now() - lastActivityRef.current;
      if (idle >= IDLE_THRESHOLD_MS && contextTokensRef.current >= IDLE_TOKEN_THRESHOLD) {
        setIdleReturnPrompt(true);
      }
    }, 60_000);
    return () => clearInterval(iv);
  }, []);

  // ── 持久化会话列表 & 当前对话 ID ──
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY_CONVS, JSON.stringify(conversations));
    } catch { /* quota exceeded or private mode */ }
  }, [conversations]);

  useEffect(() => {
    activeConvIdRef.current = activeConvId;
    try {
      if (activeConvId) localStorage.setItem(STORAGE_KEY_ACTIVE, activeConvId);
      else localStorage.removeItem(STORAGE_KEY_ACTIVE);
    } catch {}
  }, [activeConvId]);

  // Force re-render every 30s to refresh relative timestamps
  const [, setTimeTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTimeTick((t) => t + 1), 30_000);
    return () => clearInterval(iv);
  }, []);

  // ── 持久化消息（流式中由 StreamContext 管理，finally 一次性写入） ──
  const saveMessagesTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestActiveConvIdRef = useRef<string | null>(activeConvId);
  useEffect(() => { latestActiveConvIdRef.current = activeConvId; }, [activeConvId]);

  const flushCurrentConversationToStorage = useCallback(() => {
    const convId = latestActiveConvIdRef.current;
    if (!convId) return;
    saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, latestMessagesRef.current);
  }, [STORAGE_KEY_MSGS_PREFIX]);

  useEffect(() => {
    if (!activeConvId) return;
    if (streamContexts.current.get(activeConvId)?.isStreaming) return;
    if (saveMessagesTimerRef.current) clearTimeout(saveMessagesTimerRef.current);

    const doSave = () => {
      if (!saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + activeConvId, messages)) {
        try {
          const convs: ChatConversation[] = JSON.parse(localStorage.getItem(STORAGE_KEY_CONVS) || "[]");
          const toEvict = [...convs].reverse().find(c => c.id !== activeConvId);
          if (toEvict) {
            localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + toEvict.id);
            saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + activeConvId, messages);
          }
        } catch { /* give up */ }
      }
    };

    const ric = typeof requestIdleCallback === "function" ? requestIdleCallback : null;
    if (ric) {
      saveMessagesTimerRef.current = setTimeout(() => {
        ric(doSave, { timeout: 2000 });
      }, 150) as unknown as number;
    } else {
      saveMessagesTimerRef.current = setTimeout(doSave, 300) as unknown as number;
    }
    return () => { if (saveMessagesTimerRef.current) clearTimeout(saveMessagesTimerRef.current); };
  }, [messages, activeConvId, streamingTick]);

  // (messagesSnapshotRef / liveMessagesCache removed — StreamContext manages live messages)

  // 页面隐藏/关闭时立即落盘，降低"当天消息未及时写入 localStorage"的概率
  useEffect(() => {
    const flushNow = () => {
      if (saveMessagesTimerRef.current) {
        clearTimeout(saveMessagesTimerRef.current);
        saveMessagesTimerRef.current = null;
      }
      flushCurrentConversationToStorage();

      // Reset "running" conversations that have no active SSE stream,
      // preventing stale status after page reload / HMR.
      try {
        const raw = localStorage.getItem(STORAGE_KEY_CONVS);
        if (raw) {
          const convs: ChatConversation[] = JSON.parse(raw);
          let dirty = false;
          for (const c of convs) {
            if (c.status === "running" && !streamContexts.current.get(c.id)?.isStreaming) {
              c.status = "idle";
              dirty = true;
            }
          }
          if (dirty) localStorage.setItem(STORAGE_KEY_CONVS, JSON.stringify(convs));
        }
      } catch { /* ignore */ }
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flushNow();
    };
    window.addEventListener("beforeunload", flushNow);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("beforeunload", flushNow);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [flushCurrentConversationToStorage]);

  // ── Stale "running" status recovery on mount ──
  // After HMR / manual refresh, conversations may still show status="running" in
  // localStorage while no SSE stream is active. Reconcile with backend busy state.
  const staleRecoveryDoneRef = useRef(false);
  useEffect(() => {
    if (!serviceRunning || staleRecoveryDoneRef.current) return;
    const convs = latestConversationsRef.current;
    const stale = convs.filter(
      (c) => c.status === "running" && !streamContexts.current.get(c.id)?.isStreaming,
    );
    if (stale.length === 0) { staleRecoveryDoneRef.current = true; return; }
    staleRecoveryDoneRef.current = true;

    const staleIds = new Set(stale.map((c) => c.id));

    (async () => {
      try {
        const res = await safeFetch(`${apiBase}/api/chat/busy`);
        const data = await res.json();
        const busyIds = new Set(
          ((data?.busy_conversations as { conversation_id: string }[]) ?? []).map(
            (b) => b.conversation_id,
          ),
        );
        setConversations((prev) =>
          prev.map((c) => {
            if (!staleIds.has(c.id) || busyIds.has(c.id)) return c;
            return { ...c, status: "completed" as ConversationStatus };
          }),
        );
        // Re-hydrate active conversation if it was among the stale ones
        const curActive = activeConvIdRef.current;
        if (curActive && staleIds.has(curActive) && !busyIds.has(curActive)) {
          const meta = convs.find((c) => c.id === curActive);
          void hydrateConversationMessages(curActive, meta?.messageCount || 0);
        }
      } catch {
        setConversations((prev) =>
          prev.map((c) =>
            staleIds.has(c.id) ? { ...c, status: "idle" as ConversationStatus } : c,
          ),
        );
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally run once when service becomes available
  }, [serviceRunning]);

  // ── APP 后台恢复：中断已断开的 SSE 流 ──
  // Tauri / Capacitor / mobile browsers kill HTTP streams when the app/tab is
  // in the background.  Desktop browsers keep fetch streams alive across tab
  // switches, so we only register this handler for non-desktop-web platforms.
  // The catch handler uses sctx.userStopped (positive flag) to decide whether
  // to show "已中止" vs. attempt recovery — no reliance on abort reason strings.
  useEffect(() => {
    if (IS_WEB && !IS_MOBILE_BROWSER) return;
    const handler = () => {
      for (const [convId, ctx] of streamContexts.current) {
        if (!ctx.isStreaming) continue;
        ctx.abort.abort("app_resumed");
        logger.warn("Chat", "SSE stream aborted after app resume", { convId });
      }
    };
    window.addEventListener("openakita_app_resumed", handler);
    return () => window.removeEventListener("openakita_app_resumed", handler);
  }, []);

  // ── 切换对话时加载对应消息 ──
  const skipConvLoadRef = useRef(false);
  const hydrateSeqRef = useRef(0);

  const mapBackendHistoryToMessages = useCallback(
    (rows: { id: string; role: string; content: string; timestamp: number; chain_summary?: ChainSummaryItem[]; artifacts?: ChatArtifact[]; ask_user?: { question: string; options?: { id: string; label: string }[]; questions?: ChatAskQuestion[] } }[]): ChatMessage[] => {
      return rows.map((m) => ({
        id: m.id,
        role: m.role as "user" | "assistant" | "system",
        content: m.content,
        timestamp: m.timestamp,
        ...(m.chain_summary?.length ? { thinkingChain: buildChainFromSummary(m.chain_summary) } : {}),
        ...(m.artifacts?.length ? { artifacts: m.artifacts } : {}),
        ...(m.ask_user ? { askUser: m.ask_user, content: "" } : {}),
      }));
    },
    [],
  );

  const hydrateConversationMessages = useCallback(async (convId: string, expectedCount = 0) => {
    const seq = ++hydrateSeqRef.current;
    setHydrating(true);
    const localMsgs = loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + convId);

    const localCount = Array.isArray(localMsgs) ? localMsgs.length : 0;
    const shouldSyncBackend = serviceRunning && (localCount === 0 || (expectedCount > 0 && localCount < expectedCount));

    if (!shouldSyncBackend) {
      if (seq === hydrateSeqRef.current) { setMessages(localMsgs); setHydrating(false); }
      return;
    }

    try {
      const res = await safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`);
      const data = await res.json();
      const backendMsgs = Array.isArray(data?.messages) ? mapBackendHistoryToMessages(data.messages) : [];

      const chosen = backendMsgs.length >= localCount ? backendMsgs : localMsgs;
      if (seq === hydrateSeqRef.current) { setMessages(chosen); setHydrating(false); }

      if (backendMsgs.length >= localCount) {
        saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, backendMsgs);
      }
    } catch {
      if (seq === hydrateSeqRef.current) { setMessages(localMsgs); setHydrating(false); }
    }
  }, [serviceRunning, apiBaseUrl, mapBackendHistoryToMessages, STORAGE_KEY_MSGS_PREFIX]);

  useEffect(() => {
    if (!activeConvId) {
      setMessages([]);
      return;
    }
    if (skipConvLoadRef.current) {
      skipConvLoadRef.current = false;
      return;
    }

    // If a StreamContext is actively streaming for this conv, restore its state directly
    const ctx = streamContexts.current.get(activeConvId);
    if (ctx?.isStreaming) {
      setMessages(ctx.messages);
      setDisplayActiveSubAgents(ctx.activeSubAgents);
      setDisplaySubAgentTasks(ctx.subAgentTasks);
    } else {
      const activeMeta = conversations.find((c) => c.id === activeConvId);
      const expectedCount = activeMeta?.messageCount || 0;
      void hydrateConversationMessages(activeConvId, expectedCount);
      setDisplayActiveSubAgents([]);
      setDisplaySubAgentTasks([]);
    }

    convSwitchScrollRef.current = true;
    const conv = conversations.find((c) => c.id === activeConvId);
    if (multiAgentEnabled) {
      const agentId = conv?.agentProfileId || "default";
      isConvSwitchRef.current = true;
      setSelectedAgent(agentId);
    }
    setSelectedEndpoint(conv?.endpointId || "auto");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- conversations 故意排除：
    // 此 effect 语义是"切换对话时加载消息"，不应因 messageCount/title 等元数据变更而重新 hydrate，
    // 否则流结束后 setConversations 更新 messageCount 会触发竞态覆盖。
  }, [activeConvId, hydrateConversationMessages, multiAgentEnabled]);

  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // abortRef/readerRef removed — now per-session in StreamContext
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // ── 输入框 Undo/Redo 栈 (6.2) ──
  const undoStackRef = useRef<string[]>([""]);
  const undoIdxRef = useRef(0);
  const undoDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);


  const pushUndoSnapshot = useCallback((val: string) => {
    if (undoDebounceRef.current) clearTimeout(undoDebounceRef.current);
    undoDebounceRef.current = setTimeout(() => {
      const stack = undoStackRef.current;
      const idx = undoIdxRef.current;
      if (stack[idx] === val) return;
      const trimmed = stack.slice(0, idx + 1);
      trimmed.push(val);
      if (trimmed.length > UNDO_MAX_STEPS) trimmed.shift();
      undoStackRef.current = trimmed;
      undoIdxRef.current = trimmed.length - 1;
    }, 1000);
  }, []);

  const setInputValue = useCallback((val: string) => {
    inputTextRef.current = val;
    setHasInputText(val.trim().length > 0);
    if (inputRef.current) {
      inputRef.current.value = val;
      inputRef.current.style.height = "auto";
      if (val) {
        inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 120) + "px";
      }
    }
  }, []);

  // Fetch initial context size on mount / when service starts
  useEffect(() => {
    if (!serviceRunning) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/stats/tokens/context`);
        const data = await res.json();
        if (cancelled) return;
        if (typeof data.context_tokens === "number") setContextTokens(data.context_tokens);
        if (typeof data.context_limit === "number") setContextLimit(data.context_limit);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [serviceRunning, apiBaseUrl]);

  useEffect(() => {
    if (!multiAgentEnabled) {
      setAgentProfiles([]);
      return;
    }
    if (!visible) return;
    const fetchProfiles = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/agents/profiles`);
        const data = await res.json();
        setAgentProfiles(data.profiles || []);
      } catch (e) {
        logger.warn("Chat", "Failed to fetch agent profiles", { error: String(e) });
      }
    };
    fetchProfiles();
  }, [multiAgentEnabled, apiBaseUrl, serviceRunning, visible]);

  useEffect(() => {
    if (!multiAgentEnabled || !visible || !serviceRunning) return;
    const fetchOrgs = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/orgs`);
        const data = await res.json();
        setOrgList(data.map((o: any) => ({ id: o.id, name: o.name, icon: o.icon || "", status: o.status })));
      } catch { /* ignore */ }
    };
    fetchOrgs();
  }, [multiAgentEnabled, apiBaseUrl, serviceRunning, visible]);

  // Sync selectedAgent → current conversation's agentProfileId
  // Only react to selectedAgent changes (not activeConvId) to avoid overwriting
  // a newly-switched conversation with the previous conversation's agent.
  // isConvSwitchRef prevents write-back when selectedAgent was set by a conversation switch.
  const prevSelectedAgentRef = useRef(selectedAgent);
  const isConvSwitchRef = useRef(false);
  useEffect(() => {
    if (!multiAgentEnabled) return;
    if (selectedAgent === prevSelectedAgentRef.current) return;
    prevSelectedAgentRef.current = selectedAgent;
    if (isConvSwitchRef.current) {
      isConvSwitchRef.current = false;
      return;
    }
    const convId = activeConvIdRef.current;
    if (!convId) return;
    setConversations((prev) => {
      const current = prev.find((c) => c.id === convId);
      if (current?.agentProfileId === selectedAgent) return prev;
      return prev.map((c) => c.id === convId ? { ...c, agentProfileId: selectedAgent } : c);
    });
  }, [selectedAgent, multiAgentEnabled]);

  // Sync selectedEndpoint → current conversation's endpointId
  const prevSelectedEndpointRef = useRef(selectedEndpoint);
  useEffect(() => {
    if (selectedEndpoint === prevSelectedEndpointRef.current) return;
    prevSelectedEndpointRef.current = selectedEndpoint;
    const convId = activeConvIdRef.current;
    if (!convId) return;
    const epVal = selectedEndpoint === "auto" ? undefined : selectedEndpoint;
    setConversations((prev) => {
      const current = prev.find((c) => c.id === convId);
      if ((current?.endpointId || undefined) === epVal) return prev;
      return prev.map((c) => c.id === convId ? { ...c, endpointId: epVal } : c);
    });
  }, [selectedEndpoint]);

  // Validate selectedEndpoint against current endpoints list
  useEffect(() => {
    if (selectedEndpoint === "auto") return;
    if (endpoints.length === 0) return;
    if (!endpoints.some((ep) => ep.name === selectedEndpoint)) {
      setSelectedEndpoint("auto");
    }
  }, [endpoints, selectedEndpoint]);

  useEffect(() => {
    if (!agentMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (agentMenuRef.current && !agentMenuRef.current.contains(e.target as Node)) {
        setAgentMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [agentMenuOpen]);

  // 启动后后台对账会话列表：本地先展示，后端异步增量合并，避免"今天新会话缺失"
  // 同时检测 data_epoch 是否变化（factory reset / 数据重置）
  const sessionRestoreAttempted = useRef(false);

  // 后端断开时重置对账标志，使重连后能重新对账 + 检测 epoch 变化
  // （覆盖 factory reset 后不刷新页面的场景）
  useEffect(() => {
    if (!serviceRunning) {
      sessionRestoreAttempted.current = false;
    }
  }, [serviceRunning]);

  const sessionRetryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!serviceRunning || sessionRestoreAttempted.current) return;
    sessionRestoreAttempted.current = true;

    let cancelled = false;
    let attempt = 0;

    const reconcile = async () => {
      attempt++;
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/sessions?channel=desktop`);
        if (cancelled) return;
        const data = await res.json();
        if (cancelled) return;

        // Backend still loading sessions — retry with backoff (max ~20s total)
        if (data.ready === false && attempt < 6) {
          const delay = Math.min(1000 * Math.pow(1.5, attempt - 1), 5000);
          sessionRetryTimer.current = setTimeout(reconcile, delay);
          return;
        }

        const backendSessions: { id: string; title: string; lastMessage: string; timestamp: number; messageCount: number; agentProfileId?: string }[] = data.sessions || [];

        // ── Factory reset detection (epoch-based only) ──
        // Only clear local data when data_epoch actually changes, which signals
        // that the backend's data/ directory was recreated (true factory reset).
        // We intentionally do NOT wipe localStorage when "ready + 0 sessions",
        // because that can be a false positive: e.g. a version upgrade changes
        // Session serialisation and _load_sessions silently skips all old
        // sessions, yet sessions.json on disk is still intact.
        const epoch = data.data_epoch as string | undefined;
        const EPOCH_KEY = "openakita_data_epoch";

        if (epoch) {
          const cached = localStorage.getItem(EPOCH_KEY);
          localStorage.setItem(EPOCH_KEY, epoch);
          if (cached && cached !== epoch) {
            setConversations((prev) => {
              for (const c of prev) {
                try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + c.id); } catch {}
              }
              return [];
            });
            setActiveConvId(null);
            setMessages([]);
            return;
          }
        }
        if (backendSessions.length === 0) return;

        const restoredConvs: ChatConversation[] = backendSessions.map((s) => ({
          id: s.id,
          title: s.title || "对话",
          lastMessage: s.lastMessage || "",
          timestamp: s.timestamp,
          messageCount: s.messageCount || 0,
          agentProfileId: s.agentProfileId,
        }));

        setConversations((prev) => {
          const prevMap = new Map(prev.map((c) => [c.id, c]));
          const mergedFromBackend: ChatConversation[] = restoredConvs.map((b) => {
            const local = prevMap.get(b.id);
            if (!local) return b;
            return {
              ...local,
              title: local.titleGenerated ? local.title : (b.title || local.title || "对话"),
              lastMessage: b.lastMessage || local.lastMessage,
              timestamp: Math.max(local.timestamp || 0, b.timestamp || 0),
              messageCount: Math.max(local.messageCount || 0, b.messageCount || 0),
              agentProfileId: b.agentProfileId || local.agentProfileId,
            };
          });
          const backendIds = new Set(restoredConvs.map((c) => c.id));
          const localOnly = prev.filter((c) => !backendIds.has(c.id));
          return [...mergedFromBackend, ...localOnly];
        });

        // 没有活跃会话时，默认打开后端最新会话
        if (!activeConvId) {
          setActiveConvId(restoredConvs[0].id);
        }
      } catch {
        // Network error — retry if backend might still be starting
        if (!cancelled && attempt < 6) {
          const delay = Math.min(1000 * Math.pow(1.5, attempt - 1), 5000);
          sessionRetryTimer.current = setTimeout(reconcile, delay);
        }
      }
    };

    reconcile();
    return () => {
      cancelled = true;
      if (sessionRetryTimer.current) clearTimeout(sessionRetryTimer.current);
    };
  }, [serviceRunning, apiBaseUrl, activeConvId]);

  // ── Multi-device busy state: poll + WS events ──
  useEffect(() => {
    if (!serviceRunning) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/chat/busy`);
        if (cancelled) return;
        const data = await res.json();
        const items: { conversation_id: string; client_id: string }[] = data.busy_conversations || [];
        const myId = getClientId();
        const m = new Map<string, string>();
        for (const it of items) {
          if (it.client_id !== myId) m.set(it.conversation_id, it.client_id);
        }
        setBusyConversations(m);
      } catch { /* ignore */ }
    };
    poll();
    const timer = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [serviceRunning, apiBaseUrl, getClientId]);

  // ── Cross-device sync: conversation lifecycle events via WebSocket ──
  // onWsEvent handles platform detection internally (no-op for Tauri local,
  // active for Web / Capacitor / Tauri-remote).
  useEffect(() => {
    const myId = getClientId();
    return onWsEvent((event, data) => {
      const d = data as Record<string, unknown> | null;
      if (!d) return;
      const convId = d.conversation_id as string | undefined;
      if (!convId) return;

      if (event === "chat:busy") {
        const clientId = d.client_id as string | undefined;
        if (clientId && clientId !== myId) {
          setBusyConversations((prev) => { const m = new Map(prev); m.set(convId, clientId); return m; });
        }
      } else if (event === "chat:idle") {
        setBusyConversations((prev) => { const m = new Map(prev); m.delete(convId); return m; });
      } else if (event === "chat:message_update") {
        const clientId = d.client_id as string | undefined;
        if (clientId && clientId === myId) return;
        if (convId === activeConvIdRef.current) {
          safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`)
            .then((r) => r.json())
            .then((d2) => { if (d2?.messages?.length) setMessages((prev) => patchMessagesWithBackend(prev, d2.messages)); })
            .catch(() => {});
        }
        const preview = (d.last_message_preview as string) || "";
        const ts = ((d.timestamp as number) || 0) * 1000 || Date.now();
        setConversations((prev) => {
          const idx = prev.findIndex(c => c.id === convId);
          if (idx >= 0) {
            const updated = [...prev];
            updated[idx] = { ...updated[idx], lastMessage: preview || updated[idx].lastMessage, timestamp: Math.max(updated[idx].timestamp || 0, ts), messageCount: (updated[idx].messageCount || 0) + 1 };
            return updated;
          }
          return [{ id: convId, title: preview.slice(0, 20) || "对话", lastMessage: preview, timestamp: ts, messageCount: 1 }, ...prev];
        });
      } else if (event === "chat:conversation_deleted") {
        setConversations((prev) => {
          const filtered = prev.filter(c => c.id !== convId);
          if (filtered.length < prev.length) {
            try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + convId); } catch {}
          }
          return filtered;
        });
        if (activeConvIdRef.current === convId) {
          setActiveConvId(null);
          setMessages([]);
        }
      } else if (event === "chat:title_update") {
        const title = d.title as string;
        if (title) {
          setConversations((prev) => prev.map(c => {
            if (c.id !== convId) return c;
            if (c.titleManuallySet) return c;
            return { ...c, title, titleGenerated: true };
          }));
        }
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBaseUrl, getClientId]);

  // ── IM 通道掉线主动告警：监听 im:channel_status 事件 ──
  useEffect(() => {
    return onWsEvent((event, raw) => {
      if (event !== "im:channel_status") return;
      const d = raw as Record<string, unknown> | null;
      if (!d) return;
      const channel = (d.channel || d.adapter || "") as string;
      const status = (d.status || "") as string;
      if (!channel || !status) return;
      const isOffline = status === "offline" || status === "error" || status === "stopped";
      const isOnline = status === "online" || status === "running";
      if (isOffline || isOnline) {
        const alert = { channel, status: isOffline ? "offline" : "online", ts: Date.now() };
        setImChannelAlerts((prev) => {
          const filtered = prev.filter((a) => a.channel !== channel);
          return [...filtered, alert];
        });
        if (isOffline) {
          notifyError(t("chat.imChannelOffline", { channel, defaultValue: `IM 通道 ${channel} 已断开连接` }));
        }
        if (isOnline) {
          setTimeout(() => {
            setImChannelAlerts((prev) => prev.filter((a) => !(a.channel === channel && a.status === "online")));
          }, 8000);
        }
      }
    });
  }, [t]);

  // ── 消息补全：用后端数据修复 localStorage 中不完整的消息（中断的流式传输等）──
  const patchedConvsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!serviceRunning || !activeConvId || isCurrentConvStreaming) return;
    if (patchedConvsRef.current.has(activeConvId)) return;

    patchedConvsRef.current.add(activeConvId);
    const convId = activeConvId;

    safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history`)
      .then((r) => r.json())
      .then((data) => {
        if (!data?.messages?.length) return;
        setMessages((prev) => patchMessagesWithBackend(prev, data.messages));
      })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceRunning, activeConvId, streamingTick, apiBaseUrl, messages.length]);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const blobUrlsRef = useRef<string[]>([]);

  // ── API base URL (ref for stable closure access) ──
  const apiBase = apiBaseUrl;
  const apiBaseRef = useRef(apiBase);
  useEffect(() => { apiBaseRef.current = apiBase; }, [apiBase]);

  // ── 文件上传辅助函数：上传文件到 /api/upload 并返回访问 URL ──
  const uploadFile = useCallback(async (file: Blob, filename: string): Promise<string> => {
    const form = new FormData();
    form.append("file", file, filename);
    const res = await safeFetch(`${apiBaseRef.current}/api/upload`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    const data = await res.json();
    return data.url as string;
  }, []);

  // ── 组件卸载清理：abort 所有流式请求 + 停止麦克风 ──
  useEffect(() => {
    return () => {
      for (const [, ctx] of streamContexts.current) {
        try { ctx.abort.abort(); } catch {}
        try { ctx.reader?.cancel().catch(() => {}); } catch {}
        if (ctx.pollingTimer) clearInterval(ctx.pollingTimer);
      }
      streamContexts.current.clear();
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        try { mediaRecorderRef.current.stop(); } catch { /* ignore */ }
      }
      mediaRecorderRef.current = null;
      if (recordingTimerRef.current) { clearInterval(recordingTimerRef.current); recordingTimerRef.current = null; }
      for (const url of blobUrlsRef.current) {
        try { URL.revokeObjectURL(url); } catch {}
      }
      blobUrlsRef.current = [];
    };
  }, []);

  // ── 自动滚到底部 ──
  // Virtuoso 的 followOutput 已自动处理流式追踪；
  // 此处处理: (1) 切换对话后 hydrate 完成 (2) 从隐藏变可见。
  const needsScrollOnVisible = useRef(false);
  const convSwitchScrollRef = useRef(false);

  useEffect(() => {
    if (convSwitchScrollRef.current && messages.length > 0) {
      requestAnimationFrame(() => messageListRef.current?.scrollToBottom("auto"));
      isMessageListAtBottomRef.current = true;
      convSwitchScrollRef.current = false;
    }
  }, [messages]);

  useEffect(() => {
    if (!isCurrentConvStreaming) {
      messageListRef.current?.cancelFollow();
    }
  }, [isCurrentConvStreaming]);

  useEffect(() => {
    if (!visible) {
      needsScrollOnVisible.current = true;
      return;
    }
    if (needsScrollOnVisible.current) {
      requestAnimationFrame(() => {
        messageListRef.current?.scrollToBottom("auto");
      });
      isMessageListAtBottomRef.current = true;
      needsScrollOnVisible.current = false;
    }
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }, [visible]);

  // ── 思维链: 流式结束后自动折叠 ──
  useEffect(() => {
    if (!isCurrentConvStreaming && messages.some(m => m.thinkingChain?.length)) {
      const timer = setTimeout(() => {
        setMessages(prev => prev.map(m => ({
          ...m,
          thinkingChain: m.thinkingChain?.map(g => ({ ...g, collapsed: true })) ?? null,
        })));
      }, 1500);
      return () => clearTimeout(timer);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCurrentConvStreaming, streamingTick]);

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

  // ── 点击外部关闭模式菜单 ──
  useEffect(() => {
    if (!modeMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (modeMenuRef.current && !modeMenuRef.current.contains(e.target as Node)) {
        setModeMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [modeMenuOpen]);

  // ── Ctrl+/ 快捷键面板 + Ctrl+F 消息搜索 ──
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "/") {
        e.preventDefault();
        setShortcutsOpen((v) => !v);
      }
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        setMsgSearchOpen((v) => {
          if (!v) setTimeout(() => msgSearchRef.current?.focus(), 50);
          else setMsgSearchQuery("");
          return !v;
        });
      }
      if (e.key === "Escape" && shortcutsOpen) {
        e.preventDefault();
        e.stopPropagation();
        setShortcutsOpen(false);
      }
      if (e.key === "Escape" && msgSearchOpen) {
        e.preventDefault();
        setMsgSearchOpen(false);
        setMsgSearchQuery("");
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [shortcutsOpen, msgSearchOpen]);

  // ── 斜杠命令定义 ──
  const slashCommands: SlashCommand[] = useMemo(() => {
    const cmds: SlashCommand[] = [
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
      const next = chatMode === "plan" ? "agent" : "plan";
      setChatMode(next);
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: next === "plan" ? "已开启 Plan 模式" : "已关闭 Plan 模式", timestamp: Date.now() }]);
    }},
    { id: "ask", label: "问答模式", description: "开启/关闭 Ask 模式，仅问答不执行工具", action: () => {
      const next = chatMode === "ask" ? "agent" : "ask";
      setChatMode(next);
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: next === "ask" ? "已开启问答模式（仅问答，不执行工具）" : "已退出问答模式", timestamp: Date.now() }]);
    }},
    { id: "clear", label: "清空对话", description: "清除当前对话的所有消息", action: () => {
      setMessages([]);
      if (activeConvId) {
        safeFetch(`${apiBase}/api/chat/clear`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: activeConvId }),
        }).catch(() => {});
      }
    }},
    { id: "skill", label: "使用技能", description: "调用已安装的技能（发送 /skill:<技能名> 触发）", action: (args) => {
      if (args) {
        setInputValue(`请使用技能「${args}」来帮我：`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /skill <技能名>，如 /skill web-search。在消息中提及技能名即可触发。", timestamp: Date.now() }]);
      }
    }},
    { id: "persona", label: "切换角色", description: "切换 Agent 的人格预设", action: (args) => {
      if (args) {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `角色切换请在「设置 → 灵魂与意志」中修改 PERSONA_NAME 为 "${args}"`, timestamp: Date.now() }]);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "可用角色: default, business, tech_expert, butler, girlfriend, boyfriend, family, jarvis\n用法: /persona <角色ID>", timestamp: Date.now() }]);
      }
    }},
    { id: "agent", label: "切换 Agent", description: "在多 Agent 间切换（handoff 模式）", action: (args) => {
      if (args) {
        setInputValue(`请切换到 Agent「${args}」来处理接下来的任务。`);
      } else {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "用法: /agent <Agent名称>。在 handoff 模式下，AI 会自动在 Agent 间切换。", timestamp: Date.now() }]);
      }
    }},
    { id: "agents", label: "查看 Agent 列表", description: "显示可用的 Agent 列表", action: () => {
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: "Agent 列表取决于 handoff 配置。当前可通过 /agent <名称> 手动请求切换。", timestamp: Date.now() }]);
    }},
    { id: "org", label: "组织模式", description: "切换到组织编排模式，向组织下命令", action: (args) => {
      if (args === "off" || args === "关闭") {
        setOrgMode(false);
        setSelectedOrgId(null);
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "已退出组织模式", timestamp: Date.now() }]);
      } else if (args) {
        const match = orgList.find(o => o.name.includes(args) || o.id === args);
        if (match) {
          setOrgMode(true);
          setSelectedOrgId(match.id);
          setMessages((prev) => [...prev, { id: genId(), role: "system", content: `已切换到组织: ${match.icon} ${match.name}`, timestamp: Date.now() }]);
        } else {
          setMessages((prev) => [...prev, { id: genId(), role: "system", content: `未找到组织「${args}」。可用组织: ${orgList.map(o => o.name).join(", ") || "无"}`, timestamp: Date.now() }]);
        }
      } else {
        const names = orgList.map(o => `${o.icon} ${o.name}`).join("\n") || "（暂无组织）";
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `组织模式 ${orgMode ? "已开启" : "已关闭"}\n可用组织:\n${names}\n\n用法: /org <组织名> 或 /org off`, timestamp: Date.now() }]);
      }
    }},
    { id: "thinking", label: "深度思考", description: "设置思考模式 (on/off/auto)", action: (args) => {
      const mode = args?.toLowerCase().trim();
      if (mode === "on" || mode === "off" || mode === "auto") {
        setThinkingMode(mode);
        const label = { on: "开启", off: "关闭", auto: "自动" }[mode];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `思考模式已设置为: ${label}`, timestamp: Date.now() }]);
      } else {
        const currentLabel = { on: "开启", off: "关闭", auto: "自动" }[thinkingMode];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `当前思考模式: ${currentLabel}\n用法: /thinking on|off|auto`, timestamp: Date.now() }]);
      }
    }},
    { id: "thinking_depth", label: "思考程度", description: "设置思考程度 (low/medium/high)", action: (args) => {
      const depth = args?.toLowerCase().trim();
      if (depth === "low" || depth === "medium" || depth === "high") {
        setThinkingDepth(depth);
        const label = { low: "低", medium: "中", high: "高" }[depth];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `思考程度已设置为: ${label}`, timestamp: Date.now() }]);
      } else {
        const currentLabel = { low: "低", medium: "中", high: "高" }[thinkingDepth];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `当前思考程度: ${currentLabel}\n用法: /thinking_depth low|medium|high`, timestamp: Date.now() }]);
      }
    }},
    { id: "export", label: t("chat.exportLabel", "导出会话"), description: t("chat.exportDesc", "导出当前对话 (md/json)"), action: (args) => {
      const fmt = args?.trim().toLowerCase() === "json" ? "json" : "md";
      const conv = conversations.find((c) => c.id === activeConvId);
      exportConversation(messages, conv?.title || t("chat.conversation", "对话"), fmt as "md" | "json");
      setMessages((prev) => [...prev, { id: genId(), role: "system", content: t("chat.exportDone", { format: fmt.toUpperCase(), defaultValue: `已导出为 ${fmt.toUpperCase()} 格式` }), timestamp: Date.now() }]);
    }},
    { id: "memory", label: t("chat.memoryCmd", "记忆管理"), description: t("chat.memoryCmdDesc", "查看/管理 AI 记忆条目"), action: (args) => {
      if (args === "list" || !args) {
        safeFetch(`${apiBase}/api/memory/entries?limit=20`).then(r => r.json()).then(data => {
          const entries = data?.entries || data?.memories || [];
          if (!entries.length) {
            setMessages(prev => [...prev, { id: genId(), role: "system", content: t("chat.memoryEmpty", "暂无记忆条目。AI 会在对话中自动学习和记忆。"), timestamp: Date.now() }]);
          } else {
            const lines = entries.slice(0, 15).map((e: any, i: number) => `${i + 1}. ${(e.content || e.text || "").slice(0, 100)}`);
            setMessages(prev => [...prev, { id: genId(), role: "system", content: `**记忆条目** (${entries.length} 条)：\n${lines.join("\n")}`, timestamp: Date.now() }]);
          }
        }).catch(() => {
          setMessages(prev => [...prev, { id: genId(), role: "system", content: t("chat.memoryLoadFail", "无法加载记忆条目，请确认服务已启动。"), timestamp: Date.now() }]);
        });
      } else {
        setMessages(prev => [...prev, { id: genId(), role: "system", content: "用法: /memory [list]", timestamp: Date.now() }]);
      }
    }},
    { id: "skills", label: t("chat.skillsCmd", "技能管理"), description: t("chat.skillsCmdDesc", "查看已安装的技能列表"), action: () => {
      safeFetch(`${apiBase}/api/skills`).then(r => r.json()).then(data => {
        const skills = Array.isArray(data?.skills) ? data.skills : [];
        if (!skills.length) {
          setMessages(prev => [...prev, { id: genId(), role: "system", content: t("chat.skillsEmpty", "暂无已安装技能。可在设置 > 高级 > 平台连接中启用技能商店，或使用 /skill install <url> 安装。"), timestamp: Date.now() }]);
        } else {
          const lines = skills.map((s: any) => `- **${s.name || s.skill_id}**: ${s.description || t("chat.skillsNoDesc", "无描述")} ${s.enabled === false ? "(已禁用)" : ""}`);
          setMessages(prev => [...prev, { id: genId(), role: "system", content: `**已安装技能** (${skills.length})：\n${lines.join("\n")}`, timestamp: Date.now() }]);
        }
      }).catch(() => {
        setMessages(prev => [...prev, { id: genId(), role: "system", content: t("chat.skillsLoadFail", "无法加载技能列表。"), timestamp: Date.now() }]);
      });
    }},
    { id: "help", label: "帮助", description: "显示可用命令列表", action: () => {} },
  ];
    const helpCmd = cmds.find((c) => c.id === "help");
    if (helpCmd) {
      helpCmd.action = () => {
        const lines = cmds.map((c) => `- \`/${c.id}\` — ${c.description}`).join("\n");
        setMessages((prev) => [...prev, {
          id: genId(), role: "system", content: `**可用命令：**\n${lines}`, timestamp: Date.now(),
        }]);
      };
    }
    return cmds;
  }, [endpoints, chatMode, orgList, orgMode, thinkingMode, thinkingDepth, activeConvId, apiBase]);

  // ── 新建对话 ──
  const newConversation = useCallback(() => {
    const id = genId();
    if (activeConvId) {
      const ctx = streamContexts.current.get(activeConvId);
      const msgsToSave = ctx?.isStreaming ? ctx.messages : messages;
      if (msgsToSave.length > 0) {
        saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + activeConvId, msgsToSave);
      }
    }
    setActiveConvId(id);
    setMessages([]);
    setPendingAttachments([]);
    setDisplayActiveSubAgents([]);
    setDisplaySubAgentTasks([]);
    setSelectedEndpoint("auto");
    setConversations((prev) => [{
      id,
      title: "新对话",
      lastMessage: "",
      timestamp: Date.now(),
      messageCount: 0,
      agentProfileId: multiAgentEnabled ? selectedAgent : undefined,
    }, ...prev]);
  }, [activeConvId, messages, multiAgentEnabled, selectedAgent]);

  // ── 删除对话（实际执行） ──
  const doDeleteConversation = useCallback(async (convId: string) => {
    // Stop any active streams for this conversation first
    const ctx = streamContexts.current.get(convId);
    if (ctx) {
      ctx.userStopped = true;
      try { ctx.abort.abort("user_stop"); } catch {}
      try { ctx.reader?.cancel().catch(() => {}); } catch {}
      if (ctx.pollingTimer) clearInterval(ctx.pollingTimer);
      streamContexts.current.delete(convId);
      setStreamingTick(t => t + 1);
    }

    // Atomic delete: call backend first, only clean local data on success
    if (serviceRunning) {
      try {
        const res = await safeFetch(`${apiBaseRef.current}/api/sessions/${encodeURIComponent(convId)}`, {
          method: "DELETE",
        });
        if (!res.ok) {
          notifyError(t("chat.deleteConvFailed", "删除会话失败，请重试"));
          return;
        }
      } catch {
        notifyError(t("chat.deleteConvNetworkFailed", "删除会话失败，请检查网络连接"));
        return;
      }
    }

    try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + convId); } catch {}
    setMessageQueue(prev => prev.filter(m => m.convId !== convId));
    setBusyConversations((prev) => { const m = new Map(prev); m.delete(convId); return m; });

    const curActiveId = activeConvIdRef.current;
    if (convId === curActiveId) {
      setConversations((prev) => {
        const remaining = prev.filter((c) => c.id !== convId);
        if (remaining.length > 0) {
          setActiveConvId(remaining[0].id);
          setMessages(loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + remaining[0].id));
        } else {
          setActiveConvId(null);
          setMessages([]);
        }
        return remaining;
      });
    } else {
      setConversations((prev) => prev.filter((c) => c.id !== convId));
    }
  }, [serviceRunning]);

  // ── 删除对话（弹窗确认） ──
  const deleteConversation = useCallback((convId: string, e?: React.MouseEvent) => {
    if (e) { e.stopPropagation(); e.preventDefault(); }
    const conv = conversations.find((c) => c.id === convId);
    const title = conv?.title || t("chat.defaultTitle");
    setConfirmDialog({
      message: t("chat.confirmDeleteConversation", { title }),
      onConfirm: () => doDeleteConversation(convId),
    });
  }, [conversations, t, doDeleteConversation]);

  // ── 置顶/取消置顶 ──
  const togglePinConversation = useCallback((convId: string) => {
    setConversations((prev) => prev.map((c) =>
      c.id === convId ? { ...c, pinned: !c.pinned } : c
    ));
    setCtxMenu(null);
  }, []);

  // ── 重命名确认 ──
  const confirmRename = useCallback((convId: string, newTitle: string) => {
    const title = newTitle.trim();
    if (title) {
      setConversations((prev) => prev.map((c) =>
        c.id === convId ? { ...c, title, titleManuallySet: true } : c
      ));
    }
    setRenamingId(null);
    setRenameText("");
  }, []);

  // ── 发送消息（overrideText 用于 ask_user 回复等场景，绕过 inputText；targetConvId 用于自动出队等需要指定目标会话的场景） ──
  // displayContent: 当发送给 API 的原文（如 JSON）不适合直接展示时，可指定用户气泡中的显示文本
  const sendMessage = useCallback(async (overrideText?: string, targetConvId?: string, displayContent?: string, modeOverride?: "agent" | "plan" | "ask") => {
    const text = (overrideText ?? inputTextRef.current).trim();
    if (!text && pendingAttachments.length === 0) return;
    if (orgCommandPendingRef.current) return;

    const resolvedConvId = targetConvId || activeConvId;
    const targetIsStreaming = resolvedConvId ? !!streamContexts.current.get(resolvedConvId)?.isStreaming : false;
    if (targetIsStreaming) return;

    if (resolvedConvId && isConvBusyOnOtherDevice(resolvedConvId)) return;

    // 斜杠命令处理
    if (text.startsWith("/")) {
      const parts = text.slice(1).split(/\s+/);
      const cmdId = parts[0].toLowerCase();
      const cmd = slashCommands.find((c) => c.id === cmdId);
      if (cmd) {
        cmd.action(parts.slice(1).join(" "));
        setInputValue("");
        setSlashOpen(false);
        return;
      }
    }

    // @org: 前缀或组织模式 — 路由到组织 API
    const orgPrefixMatch = text.match(/^@org:(\S+?)(?:\/(\S+?))?\s+([\s\S]+)/);
    if (orgPrefixMatch || (orgMode && selectedOrgId)) {
      let targetOrgId = selectedOrgId;
      let targetNodeId = selectedOrgNodeId;
      let msgContent = text;
      if (orgPrefixMatch) {
        const orgRef = orgPrefixMatch[1];
        targetNodeId = orgPrefixMatch[2] || null;
        msgContent = orgPrefixMatch[3];
        const match = orgList.find(o => o.name.includes(orgRef) || o.id === orgRef);
        if (match) {
          targetOrgId = match.id;
        } else {
          notifyError(`未找到组织「${orgRef}」，请检查名称是否正确`);
          return;
        }
      }
      if (targetOrgId) {
        const orgUserMsg: ChatMessage = { id: genId(), role: "user", content: text, timestamp: Date.now() };
        const placeholderId = genId();
        const orgOrgName = orgList.find(o => o.id === targetOrgId)?.name || targetOrgId;
        const orgConvId = activeConvId;
        const orgMsgsSnapshot: ChatMessage[] = [...messages, orgUserMsg, {
          id: placeholderId, role: "assistant" as const,
          content: "", streaming: true, timestamp: Date.now(),
        }];
        let orgMsgsLive = orgMsgsSnapshot;

        const updateOrgMessages = (updater: (msgs: ChatMessage[]) => ChatMessage[]) => {
          orgMsgsLive = updater(orgMsgsLive);
          if (activeConvIdRef.current === orgConvId) {
            setMessages(orgMsgsLive);
          }
        };

        setMessages(orgMsgsSnapshot);
        setInputValue("");
        orgCommandPendingRef.current = true;
        setOrgCommandPending(true);

        const progressLines: string[] = [];
        const pushProgress = (line: string) => {
          progressLines.push(line);
          const preview = progressLines.slice(-8).map(l => `> ${l}`).join("\n");
          updateOrgMessages((prev) => prev.map(m =>
            m.id === placeholderId ? { ...m, content: preview } : m
          ));
        };

        // Reset org flow panel for new command
        setOrgNodeStates(new Map());
        setOrgDelegations([]);
        setOrgFlowPanelOpen(true);

        const unsub = onWsEvent((event, raw) => {
          const d = raw as Record<string, unknown> | null;
          if (!d || d.org_id !== targetOrgId) return;
          const nodeId = (d.node_id || d.from_node || "") as string;
          const toNode = (d.to_node || "") as string;
          if (event === "org:node_status") {
            const st = d.status as string;
            const task = (d.current_task || "") as string;
            // Update node state for flow panel
            setOrgNodeStates(prev => {
              const m = new Map(prev);
              m.set(nodeId, { status: st, task: task || undefined, ts: Date.now() });
              return m;
            });
            if (st === "busy") {
              pushProgress(`🟢 **${nodeId}** 开始处理${task ? `：${task.slice(0, 60)}` : ""}`);
            } else if (st === "idle") {
              pushProgress(`✅ **${nodeId}** 完成`);
            } else if (st === "error") {
              pushProgress(`❌ **${nodeId}** 出错`);
            }
          } else if (event === "org:task_delegated") {
            const task = (d.task || "") as string;
            setOrgDelegations(prev => [...prev.slice(-20), { from: nodeId, to: toNode, task, ts: Date.now() }]);
            pushProgress(`📋 **${nodeId}** → **${toNode}** 分配任务：${(task as string).slice(0, 50)}`);
          } else if (event === "org:message") {
            const msgType = d.msg_type as string || "消息";
            pushProgress(`💬 **${nodeId}** → **${toNode}** ${msgType}`);
          } else if (event === "org:escalation") {
            pushProgress(`⬆️ **${nodeId}** 向上汇报`);
          } else if (event === "org:blackboard_update") {
            pushProgress(`📝 **${nodeId}** 更新黑板`);
          } else if (event === "org:task_complete") {
            setOrgNodeStates(prev => {
              const m = new Map(prev);
              m.set(nodeId, { status: "done", ts: Date.now() });
              return m;
            });
            pushProgress(`🎯 **${nodeId}** 任务完成`);
          } else if (event === "org:task_timeout") {
            setOrgNodeStates(prev => {
              const m = new Map(prev);
              m.set(nodeId, { status: "timeout", ts: Date.now() });
              return m;
            });
            pushProgress(`⏰ **${nodeId}** 任务超时`);
          }
        });

        try {
          const submitRes = await safeFetch(`${apiBaseUrl}/api/orgs/${targetOrgId}/command`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: msgContent, target_node_id: targetNodeId }),
          });
          const submitData = await submitRes.json();
          const commandId = submitData.command_id as string | undefined;

          if (!commandId) {
            const resultText = submitData.result || submitData.error || JSON.stringify(submitData);
            const progressSummary = progressLines.length > 0
              ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
              : "";
            updateOrgMessages((prev) => prev.map(m =>
              m.id === placeholderId
                ? { ...m, content: `${progressSummary}**[${orgOrgName}]** ${resultText}`, streaming: false }
                : m
            ));
          } else {
            let resolved = false;
            const onDone = onWsEvent((evt, raw) => {
              const d = raw as Record<string, unknown> | null;
              if (evt !== "org:command_done" || !d || d.command_id !== commandId) return;
              resolved = true;
              const result = d.result as Record<string, unknown> | null;
              const error = d.error as string | undefined;
              const resultText = (result && (result.result || result.error)) || error || JSON.stringify(d);
              const progressSummary = progressLines.length > 0
                ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
                : "";
              updateOrgMessages((prev) => prev.map(m =>
                m.id === placeholderId
                  ? { ...m, content: `${progressSummary}**[${orgOrgName}]** ${resultText}`, streaming: false }
                  : m
              ));
            });

            const pollInterval = 5_000;
            const stallThreshold = 60_000;
            let lastProgressAt = Date.now();
            const origPushProgress = pushProgress;
            const wrappedPush = (line: string) => { lastProgressAt = Date.now(); origPushProgress(line); };
            // Replace the outer pushProgress's timestamp tracking
            void wrappedPush;

            const pollStartTime = Date.now();
            const MAX_POLL_WAIT_MS = 10 * 60 * 1000;

            while (!resolved && (Date.now() - pollStartTime < MAX_POLL_WAIT_MS)) {
              await new Promise(r => setTimeout(r, pollInterval));
              if (resolved) break;
              try {
                const pollRes = await safeFetch(
                  `${apiBaseUrl}/api/orgs/${targetOrgId}/commands/${commandId}`
                );
                const pollData = await pollRes.json();
                if (pollData.status === "done" || pollData.status === "error") {
                  if (!resolved) {
                    resolved = true;
                    const resultText = pollData.result?.result || pollData.result?.error || pollData.error || JSON.stringify(pollData);
                    const progressSummary = progressLines.length > 0
                      ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
                      : "";
                    updateOrgMessages((prev) => prev.map(m =>
                      m.id === placeholderId
                        ? { ...m, content: `${progressSummary}**[${orgOrgName}]** ${resultText}`, streaming: false }
                        : m
                    ));
                  }
                }
              } catch { /* poll failed, retry next cycle */ }

              if (!resolved && Date.now() - lastProgressAt > stallThreshold) {
                pushProgress("⏳ 执行时间较长，组织仍在处理中...");
                lastProgressAt = Date.now();
              }
            }

            if (!resolved) {
              resolved = true;
              const progressSummary = progressLines.length > 0
                ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
                : "";
              updateOrgMessages((prev) => prev.map(m =>
                m.id === placeholderId
                  ? { ...m, content: `${progressSummary}**[${orgOrgName}]** ⏱️ 命令执行超时（已等待 10 分钟），请稍后手动检查结果。`, streaming: false }
                  : m
              ));
            }

            onDone();
          }
        } catch (e: any) {
          updateOrgMessages((prev) => prev.map(m =>
            m.id === placeholderId
              ? { ...m, content: `组织命令失败: ${e.message || e}`, streaming: false, role: "system" as const }
              : m
          ));
        } finally {
          unsub();
          orgCommandPendingRef.current = false;
          setOrgCommandPending(false);
          if (orgConvId) {
            saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + orgConvId, orgMsgsLive);
          }
        }
        return;
      }
    }

    // 创建用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: "user",
      content: displayContent || text,
      attachments: pendingAttachments.length > 0 ? pendingAttachments.map(({ _uploadId, ...rest }) => rest) : undefined,
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

    let convId = resolvedConvId;

    setInputValue("");
    setPendingAttachments([]);
    setSlashOpen(false);
    if (!convId) {
      convId = genId();
      skipConvLoadRef.current = true;
      setActiveConvId(convId);
      setConversations((prev) => [{
        id: convId!,
        title: text.slice(0, 30) || "新对话",
        lastMessage: text,
        timestamp: Date.now(),
        messageCount: 1,
        status: "running",
        agentProfileId: multiAgentEnabled ? selectedAgent : undefined,
        endpointId: selectedEndpoint !== "auto" ? selectedEndpoint : undefined,
      }, ...prev]);
    } else {
      updateConvStatus(convId, "running");
    }

    const thisConvId = convId!;

    // SSE 流式请求 (QueryGuard 保护并发)
    const guardHandle = queryGuard.startQuery();
    const abort = guardHandle.abort;

    // Build per-session StreamContext with initial messages
    const fallbackMessages = thisConvId === activeConvId ? [...messages]
      : loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId);
    const sctx: StreamContext = {
      abort,
      reader: null,
      isStreaming: true,
      userStopped: false,
      messages: [...fallbackMessages, userMsg, assistantMsg],
      activeSubAgents: [],
      subAgentTasks: [],
      isDelegating: false,
      pollingTimer: null,
    };
    streamContexts.current.set(thisConvId, sctx);
    // Sending a new turn should always reveal the latest messages immediately.
    messageListRef.current?.forceFollow();
    isMessageListAtBottomRef.current = true;
    // Functional updater chains with any pending setMessages (e.g. handleAskAnswer's answered flag)
    if (thisConvId === activeConvIdRef.current) {
      setMessages((prev) => {
        const updated = [...prev, userMsg, assistantMsg];
        sctx.messages = updated;
        return updated;
      });
    } else {
      setMessages(sctx.messages);
    }
    setStreamingTick(t => t + 1);

    // ── Per-session helpers: write to StreamContext, sync to screen only if active ──
    // rAF throttle: StreamContext always gets the latest data immediately,
    // but React state (setMessages) is flushed at most once per animation frame.
    // This reduces O(N) reconciliation from ~30-60/s to ≤60fps, critical for long histories.
    let screenFlushRaf = 0;
    const flushToScreen = () => {
      screenFlushRaf = 0;
      const c = streamContexts.current.get(thisConvId);
      if (c && activeConvIdRef.current === thisConvId) setMessages(c.messages);
    };
    const updateMessages = (updater: (msgs: ChatMessage[]) => ChatMessage[]) => {
      const c = streamContexts.current.get(thisConvId);
      if (!c) return;
      c.messages = updater(c.messages);
      if (activeConvIdRef.current === thisConvId && !screenFlushRaf) {
        screenFlushRaf = requestAnimationFrame(flushToScreen);
      }
    };
    const updateSubAgents = (
      agentsUpdater?: (prev: SubAgentEntry[]) => SubAgentEntry[],
      tasksUpdater?: (prev: SubAgentTask[]) => SubAgentTask[],
    ) => {
      const c = streamContexts.current.get(thisConvId);
      if (!c) return;
      if (agentsUpdater) c.activeSubAgents = agentsUpdater(c.activeSubAgents);
      if (tasksUpdater) c.subAgentTasks = tasksUpdater(c.subAgentTasks);
      if (activeConvIdRef.current === thisConvId) {
        if (agentsUpdater) setDisplayActiveSubAgents(c.activeSubAgents);
        if (tasksUpdater) setDisplaySubAgentTasks(c.subAgentTasks);
      }
    };

    const IDLE_TIMEOUT_MS = 300_000;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        if (document.hidden) {
          resetIdleTimer();
          return;
        }
        abort.abort("idle_timeout");
        const c = streamContexts.current.get(thisConvId);
        c?.reader?.cancel().catch(() => {});
      }, IDLE_TIMEOUT_MS);
    };

    // ── SSE 断连恢复：轮询 session history 补全被中断的回复 ──
    // 当 SSE 流因 app 后台恢复、浏览器断连等原因中断时，后端 task 可能仍在运行。
    // 通过轮询 session history 获取后端已保存的（部分或完整）回复来恢复对话。
    const attemptRecovery = (initialDelay: number) => {
      if (!convId) return;
      const _recoverMsgId = assistantMsg.id;
      const _recoverUserTs = userMsg.timestamp;
      const _recoverKey = STORAGE_KEY_MSGS_PREFIX + thisConvId;
      let attempts = 0;
      const maxAttempts = 40;
      const basePollInterval = 3000;
      let lastContentLen = 0;
      let staleCount = 0;
      const maxStale = 5;

      const getInterval = () => {
        if (attempts <= 10) return basePollInterval;
        if (attempts <= 20) return 5000;
        return 8000;
      };

      const poll = () => {
        const ctx = streamContexts.current.get(thisConvId);
        if (!ctx || ctx.userStopped) return;
        attempts++;
        safeFetch(`${apiBaseRef.current}/api/sessions/${encodeURIComponent(convId)}/history`)
          .then((r) => r.ok ? r.json() : null)
          .then((data) => {
            if (!data) {
              if (attempts < maxAttempts) setTimeout(poll, getInterval());
              return;
            }
            const rows = Array.isArray(data?.messages) ? data.messages : [];
            const candidates = rows.filter(
              (m: { role?: string; content?: string }) =>
                m?.role === "assistant" && typeof m?.content === "string",
            );
            const newerThanUser = candidates.filter(
              (m: { timestamp?: number }) =>
                typeof m?.timestamp === "number" && m.timestamp >= _recoverUserTs,
            );
            const lastAssistant = (newerThanUser.length > 0 ? newerThanUser : candidates).slice(-1)[0];
            if (!lastAssistant?.content) {
              if (attempts < maxAttempts) setTimeout(poll, getInterval());
              return;
            }
            const contentLen = (lastAssistant.content as string).length;
            if (contentLen > lastContentLen) {
              staleCount = 0;
              lastContentLen = contentLen;
            } else {
              staleCount++;
            }
            setMessages((prev) => {
              const updated = prev.map((m) => {
                if (m.id !== _recoverMsgId) return m;
                if (m.content && m.content.length >= contentLen) return m;
                const patched: ChatMessage = { ...m, content: lastAssistant.content };
                if (
                  (!m.thinkingChain || m.thinkingChain.length === 0) &&
                  Array.isArray(lastAssistant.chain_summary) &&
                  lastAssistant.chain_summary.length > 0
                ) {
                  patched.thinkingChain = buildChainFromSummary(lastAssistant.chain_summary);
                }
                return patched;
              });
              try { saveMessagesToStorage(_recoverKey, updated); } catch { /* quota */ }
              return updated;
            });
            if (staleCount < maxStale && attempts < maxAttempts) {
              setTimeout(poll, getInterval());
            }
          })
          .catch(() => {
            if (attempts < maxAttempts) setTimeout(poll, getInterval());
            else logger.warn("Chat", "SSE recovery polling exhausted", { convId });
          });
      };
      setTimeout(poll, initialDelay);
    };

    try {
      const effectiveMode = modeOverride ?? chatMode;
      const body: Record<string, unknown> = {
        message: text,
        conversation_id: convId,
        mode: effectiveMode,
        plan_mode: effectiveMode === "plan",
        endpoint: selectedEndpoint === "auto" ? null : selectedEndpoint,
        thinking_mode: thinkingMode !== "auto" ? thinkingMode : null,
        thinking_depth: thinkingMode !== "off" ? thinkingDepth : null,
        agent_profile_id: multiAgentEnabled ? selectedAgent : undefined,
        client_id: getClientId(),
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
        if (response.status === 409) {
          try {
            const busyData = await response.json();
            if (busyData?.error === "conversation_busy") {
              const busyCid = busyData.busy_client_id as string;
              setBusyConversations((prev) => { const m = new Map(prev); m.set(thisConvId, busyCid); return m; });
              updateMessages((prev) => prev.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, content: t("chat.busyOnOtherDevice"), streaming: false }
                  : m
              ));
              if (thisConvId) updateConvStatus(thisConvId, "idle");
              return;
            }
          } catch { /* fall through to generic error */ }
        }
        const errText = await response.text().catch(() => "请求失败");
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: `错误：${response.status} ${errText}`, streaming: false } : m
        ));
        if (thisConvId) updateConvStatus(thisConvId, "error");
        return;
      }

      // 收到响应头，重置空闲计时
      resetIdleTimer();

      // 处理 SSE 流
      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");
      sctx.reader = reader;

      const decoder = new TextDecoder();
      let buffer = "";
      let currentContent = "";
      let currentThinking = "";
      let isThinking = false;
      let currentToolCalls: ChatToolCall[] = [];
      let currentPlan: ChatTodo | null = null;
      let currentAsk: ChatAskUser | null = null;
      let currentAgent: string | null = null;
      let currentArtifacts: ChatArtifact[] = [];
      let currentError: ChatErrorInfo | null = null;
      let gracefulDone = false; // SSE 正常发送了 "done" 事件

      // 思维链: 分组数据
      let chainGroups: ChainGroup[] = [];
      let currentChainGroup: ChainGroup | null = null;
      let thinkingStartTime = 0;
      let currentThinkingContent = "";
      let pendingCompressedInfo: { beforeTokens: number; afterTokens: number } | null = null;
      let sseParseFailures = 0;

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

        // 拆行：done 时 flush 全部 buffer，否则保留不完整的末行
        let lines: string[];
        if (done) {
          lines = buffer.split("\n");
          buffer = "";
        } else {
          lines = buffer.split("\n");
          buffer = lines.pop() || "";
        }

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6).trim();
          if (data === "[DONE]") continue;

          try {
            const event: StreamEvent = JSON.parse(data);
            sseParseFailures = 0;

            switch (event.type) {
              case "heartbeat":
                continue;
              case "user_insert": {
                const insertContent = (event.content || "").trim();
                if (insertContent) {
                  updateMessages((prev) => {
                    const assistantIdx = prev.findIndex((m) => m.id === assistantMsg.id);
                    const existingIdx = prev.findIndex(
                      (m) => m.role === "user" && m.content === insertContent && Date.now() - m.timestamp < 10000
                    );

                    if (existingIdx >= 0 && assistantIdx >= 0 && existingIdx > assistantIdx) {
                      const newArr = [...prev];
                      const [moved] = newArr.splice(existingIdx, 1);
                      const newAIdx = newArr.findIndex((m) => m.id === assistantMsg.id);
                      if (newAIdx >= 0) newArr.splice(newAIdx, 0, moved);
                      return newArr;
                    }

                    if (existingIdx >= 0) return prev;

                    const uMsg = { id: genId(), role: "user" as const, content: insertContent, timestamp: Date.now() };
                    if (assistantIdx >= 0) {
                      const newArr = [...prev];
                      newArr.splice(assistantIdx, 0, uMsg);
                      return newArr;
                    }
                    return [...prev, uMsg];
                  });
                }
                continue;
              }
              case "context_compressed":
                pendingCompressedInfo = { beforeTokens: event.before_tokens, afterTokens: event.after_tokens };
                break;
              case "iteration_start": {
                // 新迭代 → 新 chain group
                const newGroup: ChainGroup = {
                  iteration: event.iteration,
                  entries: [],
                  toolCalls: [],
                  hasThinking: false,
                  collapsed: false,
                };
                // 附加上下文压缩条目
                if (pendingCompressedInfo) {
                  newGroup.entries.push({ kind: "compressed", beforeTokens: pendingCompressedInfo.beforeTokens, afterTokens: pendingCompressedInfo.afterTokens });
                  pendingCompressedInfo = null;
                }
                currentChainGroup = newGroup;
                chainGroups = [...chainGroups, currentChainGroup];
                break;
              }
              case "thinking_start":
                isThinking = true;
                thinkingStartTime = Date.now();
                currentThinkingContent = "";
                if (!currentChainGroup) {
                  currentChainGroup = { iteration: chainGroups.length + 1, entries: [], toolCalls: [], hasThinking: false, collapsed: false };
                  chainGroups = [...chainGroups, currentChainGroup];
                }
                break;
              case "thinking_delta":
                currentThinking += event.content;
                currentThinkingContent += event.content;
                break;
              case "thinking_end": {
                isThinking = false;
                const _thinkDuration = event.duration_ms || (Date.now() - thinkingStartTime);
                const _hasThinking = event.has_thinking ?? (currentThinkingContent.length > 0);
                if (currentChainGroup) {
                  const grp: ChainGroup = currentChainGroup;
                  if (_hasThinking && currentThinkingContent) {
                    currentChainGroup = {
                      ...grp,
                      entries: [...grp.entries, { kind: "thinking" as const, content: currentThinkingContent }],
                      hasThinking: true,
                      durationMs: _thinkDuration,
                    };
                  } else {
                    currentChainGroup = { ...grp, durationMs: _thinkDuration };
                  }
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              }
              case "chain_text":
                if (!currentChainGroup) {
                  currentChainGroup = { iteration: chainGroups.length + 1, entries: [], toolCalls: [], hasThinking: false, collapsed: false };
                  chainGroups = [...chainGroups, currentChainGroup];
                }
                if (event.content) {
                  const grp: ChainGroup = currentChainGroup;
                  currentChainGroup = {
                    ...grp,
                    entries: [...grp.entries, { kind: "text" as const, content: event.content }],
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              case "text_delta":
                currentContent += event.content;
                break;
              case "text_replace":
                currentContent = event.content ?? "";
                break;
              case "tool_call_start": {
                const toolName = event.tool_name || event.tool;
                const callId = event.call_id || event.id;
                if (toolName === "delegate_to_agent" && event.args?.agent_id) {
                  const targetId = String(event.args.agent_id);
                  updateSubAgents((prev) => {
                    const exists = prev.find((s) => s.agentId === targetId);
                    if (exists) return prev.map((s) => s.agentId === targetId ? { ...s, status: "delegating", startTime: Date.now() } : s);
                    return [...prev, { agentId: targetId, status: "delegating" as const, reason: String(event.args.reason || ""), startTime: Date.now() }];
                  }, undefined);
                }
                if (toolName === "delegate_parallel" && Array.isArray(event.args?.tasks)) {
                  updateSubAgents((prev) => {
                    let updated = [...prev];
                    for (const task of event.args.tasks as Array<{ agent_id?: string; reason?: string }>) {
                      if (!task.agent_id) continue;
                      const targetId = String(task.agent_id);
                      const exists = updated.find((s) => s.agentId === targetId);
                      if (exists) {
                        updated = updated.map((s) => s.agentId === targetId ? { ...s, status: "delegating" as const, startTime: Date.now() } : s);
                      } else {
                        updated.push({ agentId: targetId, status: "delegating" as const, reason: String(task.reason || ""), startTime: Date.now() });
                      }
                    }
                    return updated;
                  }, undefined);
                }
                if (toolName === "spawn_agent") {
                  const targetId = String(event.args?.inherit_from || event.args?.agent_id || `spawn_${Date.now()}`);
                  updateSubAgents((prev) => {
                    const exists = prev.find((s) => s.agentId === targetId);
                    if (exists) return prev.map((s) => s.agentId === targetId ? { ...s, status: "delegating" as const, startTime: Date.now() } : s);
                    return [...prev, { agentId: targetId, status: "delegating" as const, reason: String(event.args?.task || event.args?.reason || ""), startTime: Date.now() }];
                  }, undefined);
                }
                if (toolName === "create_agent" && event.args?.name) {
                  const targetId = String(event.args.name);
                  updateSubAgents((prev) => {
                    const exists = prev.find((s) => s.agentId === targetId);
                    if (exists) return prev.map((s) => s.agentId === targetId ? { ...s, status: "delegating" as const, startTime: Date.now() } : s);
                    return [...prev, { agentId: targetId, status: "delegating" as const, reason: String(event.args.description || ""), startTime: Date.now() }];
                  }, undefined);
                }

                // Per-session polling for sub-agent progress
                const _isAgentTool = toolName === "delegate_to_agent" || toolName === "delegate_parallel" || toolName === "spawn_agent" || toolName === "create_agent";
                if (_isAgentTool) {
                  logger.info("Chat", "Agent tool detected in SSE", {
                    tool: toolName, args: JSON.stringify(event.args || {}).slice(0, 200),
                    multiAgentEnabled: String(multiAgentEnabled),
                    activeConv: activeConvIdRef.current, thisConv: thisConvId,
                    subAgentsCount: sctx.activeSubAgents.length,
                  });
                }
                if (_isAgentTool && !sctx.isDelegating) {
                  sctx.isDelegating = true;
                  if (sctx.pollingTimer) clearInterval(sctx.pollingTimer);
                  const doFetch = () => {
                    safeFetch(`${apiBase}/api/agents/sub-tasks?conversation_id=${encodeURIComponent(thisConvId)}`)
                      .then((r) => r.json())
                      .then((data: SubAgentTask[]) => {
                        if (!Array.isArray(data)) return;
                        const c = streamContexts.current.get(thisConvId);
                        if (c) c.subAgentTasks = data;
                        if (activeConvIdRef.current === thisConvId) setDisplaySubAgentTasks(data);
                        logger.debug("Chat", "Sub-tasks poll result", {
                          count: data.length,
                          activeConvMatch: String(activeConvIdRef.current === thisConvId),
                        });
                        const allDone = data.length > 0 && data.every(
                          (t) => t.status === "completed" || t.status === "error" || t.status === "timeout" || t.status === "cancelled"
                        );
                        if (allDone && c?.pollingTimer) {
                          clearInterval(c.pollingTimer);
                          c.pollingTimer = null;
                          c.isDelegating = false;
                        }
                      })
                      .catch((e) => {
                        logger.warn("Chat", "Sub-tasks poll failed", { error: String(e) });
                      });
                  };
                  setTimeout(doFetch, 500);
                  sctx.pollingTimer = setInterval(doFetch, 2000);
                }

                currentToolCalls = [...currentToolCalls, { tool: toolName, args: event.args, status: "running", id: callId }];
                const _tcId = callId || genId();
                const _desc = formatToolDescription(toolName, event.args);
                const newTc: ChainToolCall = { toolId: _tcId, tool: toolName, args: event.args, status: "running", description: _desc };
                if (currentChainGroup) {
                  const grp: ChainGroup = currentChainGroup;
                  currentChainGroup = {
                    ...grp,
                    toolCalls: [...grp.toolCalls, newTc],
                    entries: [...grp.entries, { kind: "tool_start" as const, toolId: _tcId, tool: toolName, args: event.args, description: _desc, status: "running" }],
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              }
              case "tool_call_end": {
                const toolName = event.tool_name || event.tool;
                const callId = event.call_id || event.id;
                const _isAgentToolEnd = toolName === "delegate_to_agent" || toolName === "delegate_parallel" || toolName === "spawn_agent" || toolName === "create_agent";
                if (_isAgentToolEnd) {
                  const isErr = event.is_error === true || (event.result || "").startsWith("❌");
                  updateSubAgents((prev) => prev.map((s) =>
                    s.status === "delegating" ? { ...s, status: isErr ? "error" : "done" } : s
                  ), undefined);
                  sctx.isDelegating = false;
                  if (sctx.pollingTimer) { clearInterval(sctx.pollingTimer); sctx.pollingTimer = null; }
                  safeFetch(`${apiBase}/api/agents/sub-tasks?conversation_id=${encodeURIComponent(thisConvId)}`)
                    .then((r) => r.json())
                    .then((data: SubAgentTask[]) => {
                      if (!Array.isArray(data)) return;
                      const c = streamContexts.current.get(thisConvId);
                      if (c) c.subAgentTasks = data;
                      if (activeConvIdRef.current === thisConvId) setDisplaySubAgentTasks(data);
                      const allDone = data.length > 0 && data.every(
                        (t) => t.status === "completed" || t.status === "error" || t.status === "timeout" || t.status === "cancelled"
                      );
                      if (allDone) {
                        setTimeout(() => {
                          const c2 = streamContexts.current.get(thisConvId);
                          if (c2) { c2.subAgentTasks = []; c2.activeSubAgents = []; }
                          if (activeConvIdRef.current === thisConvId) {
                            setDisplaySubAgentTasks([]);
                            setDisplayActiveSubAgents([]);
                          }
                        }, 5000);
                      }
                    })
                    .catch(() => {});
                }
                // Refresh profiles when a new agent is created
                if (toolName === "create_agent" && !(event.is_error || (event.result || "").startsWith("❌"))) {
                  safeFetch(`${apiBase}/api/agents/profiles`)
                    .then((r) => r.json())
                    .then((data) => { if (data?.profiles) setAgentProfiles(data.profiles); })
                    .catch(() => {});
                }
                let matched = false;
                currentToolCalls = currentToolCalls.map((tc) => {
                  if (matched) return tc;
                  const idMatch = callId && tc.id && tc.id === callId;
                  const nameMatch = !callId && tc.tool === toolName && tc.status === "running";
                  if (idMatch || nameMatch) { matched = true; return { ...tc, result: event.result, status: "done" as const }; }
                  return tc;
                });
                if (currentChainGroup) {
                  const grp: ChainGroup = currentChainGroup;
                  let chainMatched = false;
                  const isError = event.is_error === true || (event.result || "").startsWith("Tool error");
                  const endStatus = isError ? "error" as const : "done" as const;
                  currentChainGroup = {
                    ...grp,
                    toolCalls: grp.toolCalls.map((tc: ChainToolCall) => {
                      if (chainMatched) return tc;
                      const idMatch = callId && tc.toolId === callId;
                      const nameMatch = !callId && tc.tool === toolName && tc.status === "running";
                      if (idMatch || nameMatch) { chainMatched = true; return { ...tc, status: endStatus as ChainToolCall["status"], result: event.result }; }
                      return tc;
                    }),
                    // 更新 tool_start 状态 + 追加 tool_end
                    entries: [
                      ...grp.entries.map(e => {
                        if (e.kind === "tool_start" && (!e.status || e.status === "running")) {
                          const eIdMatch = callId && e.toolId === callId;
                          const eNameMatch = !callId && e.tool === toolName;
                          if (eIdMatch || eNameMatch) return { ...e, status: endStatus };
                        }
                        return e;
                      }),
                      { kind: "tool_end" as const, toolId: callId || "", tool: toolName, result: event.result, status: endStatus },
                    ],
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              }
              case "todo_created":
                currentPlan = event.plan;
                updateMessages((prev) => prev.map((m) =>
                  m.todo && m.todo.status !== "completed" && m.todo.status !== "failed" && m.todo.status !== "cancelled"
                    ? { ...m, todo: { ...m.todo, status: "completed" as const } }
                    : m
                ));
                break;
              case "todo_step_updated":
                if (currentPlan) {
                  const newSteps: ChatTodoStep[] = currentPlan.steps.map((s) => {
                    const stepId = event.step_id || event.stepId;
                    const matched = stepId
                      ? s.id === stepId
                      : event.stepIdx != null && currentPlan!.steps.indexOf(s) === event.stepIdx;
                    return matched ? { ...s, status: event.status as ChatTodoStep["status"] } : s;
                  });
                  const allDone = newSteps.every((s) => s.status === "completed" || s.status === "skipped" || s.status === "failed");
                  currentPlan = { ...currentPlan, steps: newSteps, ...(allDone ? { status: "completed" as const } : {}) } as ChatTodo;
                }
                break;
              case "todo_completed":
                if (currentPlan) {
                  currentPlan = { ...currentPlan, status: "completed" } as ChatTodo;
                }
                break;
              case "todo_cancelled":
                if (currentPlan) {
                  currentPlan = { ...currentPlan, status: "cancelled" } as ChatTodo;
                }
                break;
              case "plan_ready_for_approval":
                pendingApprovalRef.current = event.data as PlanApprovalEvent;
                break;
              case "security_confirm": {
                const newConfirm: SecurityConfirmData = {
                  tool: (event.tool_name || event.tool) as string,
                  args: event.args as Record<string, unknown>,
                  reason: event.reason as string,
                  riskLevel: event.risk_level as string,
                  needsSandbox: event.needs_sandbox as boolean,
                  toolId: ((event.confirm_id || event.call_id || event.id) ?? "") as string,
                  countdown: 120,
                };
                setSecurityConfirm((prev) => {
                  if (prev) {
                    securityQueueRef.current.push(newConfirm);
                    return prev;
                  }
                  return newConfirm;
                });
                break;
              }
              case "sub_agent_state": {
                const agentId = String(event.agent_id || event.agentId || "");
                if (!agentId) break;
                updateSubAgents((prev) => {
                  const exists = prev.find((s) => s.agentId === agentId);
                  const nextState = {
                    agentId,
                    status: (event.status || "running") as typeof prev[number]["status"],
                    reason: String(event.reason || ""),
                    startTime: Date.now(),
                  };
                  if (exists) {
                    return prev.map((s) => s.agentId === agentId ? { ...s, ...nextState } : s);
                  }
                  return [...prev, nextState];
                }, undefined);
                break;
              }
              case "ask_user": {
                const askQuestions = event.questions;
                // 如果没有 questions 数组但有 allow_multiple，构造一个统一的 questions
                if (!askQuestions && event.allow_multiple && event.options?.length) {
                  currentAsk = {
                    question: event.question,
                    options: event.options,
                    questions: [{
                      id: "__single__",
                      prompt: event.question,
                      options: event.options,
                      allow_multiple: true,
                    }],
                  };
                } else {
                  currentAsk = {
                    question: event.question,
                    options: event.options,
                    questions: askQuestions,
                  };
                }
                // AskUserBlock renders the question — clear streamed content
                // if it's a duplicate/prefix of the ask question to avoid showing it twice
                if (currentContent && event.question && event.question.includes(currentContent.trim())) {
                  currentContent = "";
                }
                break;
              }
              case "ui_preference":
                if (event.theme) setThemePref(event.theme as Theme);
                if (event.language) i18n.changeLanguage(event.language);
                break;
              case "artifact":
                logger.debug("Chat", "Artifact SSE received", { name: event.name, file_url: event.file_url, artifact_type: event.artifact_type });
                currentArtifacts = [...currentArtifacts, {
                  artifact_type: event.artifact_type,
                  file_url: event.file_url,
                  path: event.path,
                  name: event.name,
                  caption: event.caption,
                  size: event.size,
                }];
                break;
              case "agent_handoff": {
                updateSubAgents((prev) => {
                  const exists = prev.find((s) => s.agentId === event.to_agent);
                  if (exists) return prev.map((s) => s.agentId === event.to_agent ? { ...s, status: "delegating", startTime: Date.now() } : s);
                  return [...prev, { agentId: event.to_agent, status: "delegating" as const, reason: event.reason, startTime: Date.now() }];
                }, undefined);
                break;
              }
              case "agent_switch":
                currentAgent = event.agentName;
                updateMessages((prev) => {
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
                    todo: currentPlan,
                    askUser: currentAsk,
                    errorInfo: currentError,
                    artifacts: currentArtifacts.length > 0 ? [...currentArtifacts] : null,
                    thinkingChain: chainGroups.length > 0 ? chainGroups.map(g => ({ ...g })) : null,
                    streaming: true,
                  }];
                });
                continue; // skip normal update below
              case "error":
                currentError = {
                  message: event.message,
                  category: classifyError(event.message),
                  raw: event.message,
                };
                break;
              case "done":
                gracefulDone = true;
                if (event.usage) {
                  if (typeof event.usage.context_tokens === "number") setContextTokens(event.usage.context_tokens);
                  if (typeof event.usage.context_limit === "number") setContextLimit(event.usage.context_limit);
                  const { input_tokens, output_tokens, total_tokens } = event.usage;
                  if (typeof input_tokens === "number" && typeof output_tokens === "number") {
                    assistantMsg.usage = { input_tokens, output_tokens, total_tokens: total_tokens ?? input_tokens + output_tokens };
                  }
                }
                if (currentPlan && currentPlan.status === "in_progress") {
                  currentPlan = { ...(currentPlan as ChatTodo), status: "completed" as const };
                }
                updateMessages((prev) => {
                  const hasStaleTodo = prev.some((m) => m.id !== assistantMsg.id && m.todo && m.todo.status !== "completed" && m.todo.status !== "failed" && m.todo.status !== "cancelled");
                  if (!hasStaleTodo) return prev;
                  return prev.map((m) =>
                    m.id !== assistantMsg.id && m.todo && m.todo.status !== "completed" && m.todo.status !== "failed" && m.todo.status !== "cancelled"
                      ? { ...m, todo: { ...m.todo, status: "completed" as const } }
                      : m
                  );
                });
                if (pendingApprovalRef.current) {
                  setPendingApproval(pendingApprovalRef.current);
                  pendingApprovalRef.current = null;
                }
                break;
              default:
                break;
            }

            // 更新助手消息
            updateMessages((prev) => prev.map((m) =>
              m.id === assistantMsg.id
                ? {
                    ...m,
                    content: currentContent,
                    thinking: currentThinking || null,
                    agentName: currentAgent,
                    toolCalls: currentToolCalls.length > 0 ? [...currentToolCalls] : null,
                    todo: currentPlan ? { ...currentPlan } : null,
                    askUser: currentAsk,
                    errorInfo: currentError,
                    artifacts: currentArtifacts.length > 0 ? [...currentArtifacts] : null,
                    thinkingChain: chainGroups.length > 0 ? chainGroups.map(g => ({ ...g })) : null,
                    usage: assistantMsg.usage ?? m.usage,
                    streaming: event.type !== "done",
                  }
                : m
            ));

            if (event.type === "done") break;
          } catch {
            sseParseFailures++;
            if (sseParseFailures >= 5) {
              notifyError(t("chat.sseParseError", "SSE 数据解析异常频繁，可能存在通信问题"));
              sseParseFailures = 0;
            }
          }
        }

        if (done) break;
      }

      // ── 循环结束后：判断是正常完成还是被用户中止 ──
      if (abort.signal.aborted) {
        if (sctx.userStopped) {
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: m.content || "（已中止）", streaming: false }
              : m
          ));
        } else {
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, streaming: false } : m
          ));
          attemptRecovery(4000);
        }
      } else {
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id
            ? {
                ...m,
                content: m.content || (m.askUser ? "" : "⚠️ 未收到有效回复，请重试。"),
                streaming: false,
              }
            : m
        ));

        if (!gracefulDone && convId) {
          // SSE 连接被中断（未收到 "done" 事件），后端可能仍在运行，启动持续轮询恢复
          attemptRecovery(3000);
        } else if (gracefulDone) {
          // SSE 正常完成，但若未交付任何有效响应，做一次性回填
          const streamDeliveredPayload = !!(
            currentContent.trim() || currentAsk || currentToolCalls.length > 0
          );
          if (!streamDeliveredPayload && convId) {
            safeFetch(`${apiBase}/api/sessions/${encodeURIComponent(convId)}/history`)
              .then((r) => r.json())
              .then((data) => {
                const rows = Array.isArray(data?.messages) ? data.messages : [];
                const candidates = rows.filter((m: { role?: string; content?: string }) => m?.role === "assistant" && typeof m?.content === "string");
                const newerThanUser = candidates.filter((m: { timestamp?: number }) => typeof m?.timestamp === "number" && m.timestamp >= userMsg.timestamp);
                const lastAssistant = (newerThanUser.length > 0 ? newerThanUser : candidates).slice(-1)[0];
                if (!lastAssistant?.content) return;
                const backendLen = (lastAssistant.content as string).length;
                setMessages((prev) => prev.map((m) => {
                  if (m.id !== assistantMsg.id) return m;
                  if (m.content && m.content.length >= backendLen) return m;
                  const patched: ChatMessage = { ...m, content: lastAssistant.content };
                  if ((!m.thinkingChain || m.thinkingChain.length === 0) && Array.isArray(lastAssistant.chain_summary) && lastAssistant.chain_summary.length > 0) {
                    patched.thinkingChain = buildChainFromSummary(lastAssistant.chain_summary);
                  }
                  return patched;
                }));
              })
              .catch(() => {});
          }
        }
      }
    } catch (e: unknown) {
      if (sctx.userStopped) {
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: m.content || "（已中止）", streaming: false } : m
        ));
      } else {
        const isAbortLike =
          abort.signal.aborted ||
          (e instanceof DOMException && e.name === "AbortError") ||
          (e instanceof Error && e.name === "AbortError");

        if (isAbortLike) {
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, streaming: false } : m
          ));
        } else {
          const errMsg = e instanceof Error ? e.message : String(e);
          let guidance = t("chat.backendServiceHint");
          try {
            const healthRes = await fetch(`${apiBase}/api/health`, { signal: AbortSignal.timeout(5000) });
            if (healthRes.ok) {
              guidance = t("chat.backendOnlineUpstreamHint");
            }
          } catch { /* health probe failed -> keep backend guidance */ }
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content || `连接失败：${errMsg}\n\n${guidance}`, streaming: false } : m
          ));
        }

        attemptRecovery(abort.signal.aborted ? 4000 : 3000);
      }
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      if (screenFlushRaf) { cancelAnimationFrame(screenFlushRaf); screenFlushRaf = 0; }
      const ctx = streamContexts.current.get(thisConvId);
      if (ctx) {
        ctx.isStreaming = false;
        try { ctx.reader?.cancel().catch(() => {}); } catch {}
        ctx.reader = null;
        const hasRunning = ctx.subAgentTasks.some(
          (t) => t.status === "running" || t.status === "starting"
        );
        if (hasRunning && !ctx.pollingTimer) {
          const doFetch = () => {
            safeFetch(`${apiBase}/api/agents/sub-tasks?conversation_id=${encodeURIComponent(thisConvId)}`)
              .then((r) => r.json())
              .then((data: SubAgentTask[]) => {
                if (!Array.isArray(data)) return;
                if (activeConvIdRef.current === thisConvId) setDisplaySubAgentTasks(data);
                const allDone = data.length > 0 && data.every(
                  (t) => t.status === "completed" || t.status === "error" || t.status === "timeout" || t.status === "cancelled"
                );
                if (allDone) {
                  if (finalPollingTimer) { clearInterval(finalPollingTimer); finalPollingTimer = null; }
                  setTimeout(() => {
                    if (activeConvIdRef.current === thisConvId) {
                      setDisplaySubAgentTasks([]);
                      setDisplayActiveSubAgents([]);
                    }
                  }, 30_000);
                }
              })
              .catch(() => {});
          };
          let finalPollingTimer: ReturnType<typeof setInterval> | null = setInterval(doFetch, 2000);
          doFetch();
          setTimeout(() => {
            if (finalPollingTimer) { clearInterval(finalPollingTimer); finalPollingTimer = null; }
          }, 600_000);
        } else if (!hasRunning) {
          if (ctx.pollingTimer) { clearInterval(ctx.pollingTimer); ctx.pollingTimer = null; }
          if (activeConvIdRef.current === thisConvId) {
            setTimeout(() => {
              setDisplayActiveSubAgents([]);
              setDisplaySubAgentTasks([]);
            }, 30_000);
          }
        } else {
          if (ctx.pollingTimer) { clearInterval(ctx.pollingTimer); ctx.pollingTimer = null; }
        }
        saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId, ctx.messages);
        if (activeConvIdRef.current === thisConvId) {
          setMessages(ctx.messages);
        }
        streamContexts.current.delete(thisConvId);
      }
      queryGuard.endQuery(guardHandle.generation);
      setStreamingTick(t => t + 1);

      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === thisConvId
            ? { ...c, lastMessage: text.slice(0, 60), timestamp: Date.now(), messageCount: (c.messageCount || 0) + 2, status: "completed" as ConversationStatus }
            : c
        );
        const conv = updated.find((c) => c.id === thisConvId);
        if (conv && !conv.titleGenerated && (conv.messageCount || 0) <= 2) {
          (async () => {
            try {
              const res = await safeFetch(`${apiBase}/api/sessions/generate-title`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text, conversation_id: thisConvId }),
                signal: AbortSignal.timeout(15000),
              });
              const data = await res.json();
              if (data.title) {
                setConversations((p) => p.map((c) =>
                  c.id === thisConvId ? { ...c, title: data.title, titleGenerated: true } : c
                ));
              }
            } catch { /* fallback: keep truncated title */ }
          })();
        }
        return updated;
      });
    }
  }, [pendingAttachments, isCurrentConvStreaming, activeConvId, chatMode, selectedEndpoint, apiBase, slashCommands, thinkingMode, thinkingDepth, t, setInputValue]);

  // ── 处理用户回答 (ask_user) ──
  const handleAskAnswer = useCallback((msgId: string, answer: string) => {
    const target = latestMessagesRef.current.find((m) => m.id === msgId);
    const displayText = target?.askUser
      ? formatAskUserAnswer(answer, target.askUser)
      : undefined;

    const isPlanSwitch = answer === "plan" && target?.askUser?.options?.some((o: { id: string }) => o.id === "plan");
    if (isPlanSwitch) {
      setChatMode("plan");
    }

    setMessages((prev) => prev.map((m) =>
      m.id === msgId && m.askUser
        ? { ...m, askUser: { ...m.askUser, answered: true, answer } }
        : m
    ));
    // reason_stream 在 ask_user 后中断流，用户回复通过新 /api/chat 请求继续处理
    sendMessage(answer, undefined, displayText !== answer ? displayText : undefined, isPlanSwitch ? "plan" : undefined);
  }, [sendMessage]);

  // ── Plan 审批回调 ──
  const handlePlanApprove = useCallback(() => {
    setPendingApproval(null);
    setChatMode("agent");
    sendMessage("请按计划执行", undefined, undefined, "agent");
  }, [sendMessage]);

  const handlePlanReject = useCallback((feedback: string) => {
    setPendingApproval(null);
    const msg = feedback
      ? `计划需要修改。修改意见：\n${feedback}`
      : "计划需要修改，请重新调整。";
    sendMessage(msg, undefined, undefined, "plan");
  }, [sendMessage]);

  const handlePlanDismiss = useCallback(() => {
    const approval = pendingApproval;
    setPendingApproval(null);
    if (approval?.conversation_id) {
      safeFetch(`${apiBase}/api/plan/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: approval.conversation_id }),
      }).catch(() => {});
    }
  }, [pendingApproval, apiBase]);

  // ── 停止生成 ──
  const stopStreaming = useCallback((targetConvId?: string) => {
    const id = targetConvId ?? activeConvIdRef.current;
    if (!id) return;
    const ctx = streamContexts.current.get(id);
    if (ctx) {
      ctx.userStopped = true;
      ctx.abort.abort("user_stop");
      try { ctx.reader?.cancel().catch(() => {}); } catch {}
      ctx.reader = null;
    }
    queryGuard.cancel();
  }, [queryGuard]);

  // ── 消息排队系统 ──
  const [messageQueue, setMessageQueue] = useState<QueuedMessage[]>([]);
  const [queueExpanded, setQueueExpanded] = useState(true);

  // ── 消息编辑：回填到输入框，删除该条及后续消息 ──
  const handleEditMessage = useCallback((msgId: string) => {
    const msgs = latestMessagesRef.current;
    const idx = msgs.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    const target = msgs[idx];
    if (target.role !== "user") return;
    setInputValue(target.content);
    setMessages((prev) => prev.slice(0, idx));
  }, []);

  // ── 重新生成：删除助手回复，重发上一条用户消息 ──
  const handleRegenerate = useCallback((msgId: string) => {
    const msgs = latestMessagesRef.current;
    const idx = msgs.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    const target = msgs[idx];
    if (target.role !== "assistant") return;
    const prevUserMsg = msgs.slice(0, idx).reverse().find((m) => m.role === "user");
    if (!prevUserMsg) return;
    const textToResend = prevUserMsg.content;
    setMessages((prev) => prev.slice(0, idx));
    setTimeout(() => sendMessage(textToResend), 50);
  }, [sendMessage]);

  const handleRewind = useCallback((msgId: string) => {
    const msgs = latestMessagesRef.current;
    const idx = msgs.findIndex((m) => m.id === msgId);
    if (idx < 0 || idx >= msgs.length - 1) return;
    setMessages((prev) => prev.slice(0, idx + 1));
  }, []);

  const handleSkipStep = useCallback(() => {
    safeFetch(`${apiBase}/api/chat/skip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, reason: "用户从界面跳过步骤" }),
    }).catch(() => {});
  }, [apiBase, activeConvId]);

  const handleImagePreview = useCallback((displayUrl: string, downloadUrl: string, name: string) => {
    setLightbox({ url: displayUrl, downloadUrl, name });
  }, []);

  const closeLightbox = useCallback(() => setLightbox(null), []);

  const handleCancelTask = useCallback(() => {
    safeFetch(`${apiBase}/api/chat/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: activeConvId, reason: "用户从界面取消任务" }),
    }).then(() => {
      const cid = activeConvId;
      setTimeout(() => {
        if (cid && streamContexts.current.get(cid)?.reader) stopStreaming(cid);
      }, 2000);
    }).catch(() => {
      stopStreaming();
    });
  }, [apiBase, activeConvId, stopStreaming]);

  const handleInsertMessage = useCallback((text: string) => {
    if (!text.trim()) return;
    const convId = activeConvIdRef.current;
    const inserter = (prev: ChatMessage[]) => {
      const uMsg = { id: genId(), role: "user" as const, content: text.trim(), timestamp: Date.now() };
      const streamingIdx = prev.findIndex((m) => m.role === "assistant" && m.streaming);
      if (streamingIdx >= 0) {
        const newArr = [...prev];
        newArr.splice(streamingIdx, 0, uMsg);
        return newArr;
      }
      return [...prev, uMsg];
    };
    const ctx = convId ? streamContexts.current.get(convId) : null;
    if (ctx) ctx.messages = inserter(ctx.messages);
    setMessages(inserter);
    if (convId) {
      setConversations((prev) => prev.map((c) =>
        c.id === convId ? { ...c, messageCount: (c.messageCount || 0) + 1 } : c
      ));
    }
    safeFetch(`${apiBaseRef.current}/api/chat/insert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: convId, message: text }),
    }).catch(() => {});
  }, []);

  const handleQueueMessage = useCallback(() => {
    const text = inputTextRef.current.trim();
    if (!text || !activeConvId) return;
    setMessageQueue(prev => [...prev, { id: genId(), text, timestamp: Date.now(), convId: activeConvId }]);
    setInputValue("");
  }, [activeConvId, setInputValue]);

  const handleRemoveQueued = useCallback((id: string) => {
    setMessageQueue(prev => prev.filter(m => m.id !== id));
  }, []);

  const handleEditQueued = useCallback((id: string) => {
    const item = messageQueue.find(m => m.id === id);
    if (item) {
      setInputValue(item.text);
      setMessageQueue(prev => prev.filter(m => m.id !== id));
      inputRef.current?.focus();
    }
  }, [messageQueue, setInputValue]);

  const handleSendQueuedNow = useCallback((id: string) => {
    const item = messageQueue.find(m => m.id === id);
    if (item) {
      handleInsertMessage(item.text);
      setMessageQueue(prev => prev.filter(m => m.id !== id));
    }
  }, [messageQueue, handleInsertMessage]);

  const handleMoveQueued = useCallback((id: string, direction: "up" | "down") => {
    setMessageQueue(prev => {
      const idx = prev.findIndex(m => m.id === id);
      if (idx < 0) return prev;
      const newIdx = direction === "up" ? idx - 1 : idx + 1;
      if (newIdx < 0 || newIdx >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
      return next;
    });
  }, []);

  // ── 排队消息自动出队 ──
  // 后端支持并发流式 — 每会话独立 Agent 实例。
  // 排队仅限同会话：某会话流结束时，出队该会话排队的下一条消息。
  const prevStreamingSetRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const currentStreamingSet = new Set(
      [...streamContexts.current.entries()].filter(([, c]) => c.isStreaming).map(([id]) => id),
    );
    if (messageQueue.length === 0) {
      prevStreamingSetRef.current = currentStreamingSet;
      return;
    }
    for (const finishedId of prevStreamingSetRef.current) {
      if (!currentStreamingSet.has(finishedId)) {
        const nextIdx = messageQueue.findIndex(m => m.convId === finishedId);
        if (nextIdx >= 0) {
          const next = messageQueue[nextIdx];
          setMessageQueue(prev => prev.filter((_, i) => i !== nextIdx));
          const targetId = next.convId;
          setTimeout(() => {
            sendMessage(next.text, targetId);
          }, 100);
          break;
        }
      }
    }
    prevStreamingSetRef.current = currentStreamingSet;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamingTick, messageQueue, sendMessage]);

  // ── 文件/图片上传 ──
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of Array.from(files)) {
      const uploadId = genId();
      const att: ChatAttachment = {
        type: file.type.startsWith("image/") ? "image" : file.type.startsWith("video/") ? "video" : file.type.startsWith("audio/") ? "voice" : file.type === "application/pdf" ? "document" : "file",
        name: file.name,
        size: file.size,
        mimeType: file.type,
        _uploadId: uploadId,
      };
      if (att.type === "video" && file.size > 7 * 1024 * 1024) {
        notifyError(`视频文件过大 (${(file.size / 1024 / 1024).toFixed(1)}MB)，桌面端最大支持 7MB（base64 编码后需 < 10MB）`);
        continue;
      }
      if (att.type === "image" || att.type === "video") {
        const reader = new FileReader();
        reader.onload = () => {
          att.previewUrl = att.type === "image" ? reader.result as string : undefined;
          att.url = reader.result as string;
          setPendingAttachments((prev) => [...prev, att]);
        };
        reader.onerror = () => {
          notifyError(`文件读取失败: ${file.name}`);
        };
        reader.readAsDataURL(file);
      } else {
        setPendingAttachments((prev) => [...prev, att]);
        uploadFile(file, file.name)
          .then((serverUrl) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a._uploadId === uploadId
                ? { ...a, url: `${apiBaseRef.current}${serverUrl}` } : a)
            );
          })
          .catch(() => {
            notifyError(`文件上传失败: ${file.name}`);
            setPendingAttachments((prev) =>
              prev.filter((a) => a._uploadId !== uploadId || a.url));
          });
      }
    }
    e.target.value = "";
  }, [uploadFile]);

  // ── 粘贴处理 ──
  const [pastedLargeText, setPastedLargeText] = useState<{ text: string; lines: number } | null>(null);
  useEffect(() => { setPastedLargeText(null); setPendingApproval(null); pendingApprovalRef.current = null; }, [activeConvId]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    // Large text paste detection (6.4)
    const plainText = e.clipboardData?.getData("text/plain") || "";
    if (plainText.length > PASTE_CHAR_THRESHOLD) {
      e.preventDefault();
      const lineCount = plainText.split("\n").length;
      setPastedLargeText({ text: plainText, lines: lineCount });
      return;
    }

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

  // ── 拖拽图片/文件 (Tauri native or HTML5 drag-drop) ──
  const [dragOver, setDragOver] = useState(false);
  useEffect(() => {
    if (!IS_TAURI) return; // Web uses HTML5 drag-drop via onDrop on the container
    let cancelled = false;
    let unlisten: (() => void) | null = null;

    const mimeMap: Record<string, string> = {
      png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
      gif: "image/gif", webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
      mp4: "video/mp4", webm: "video/webm", avi: "video/x-msvideo",
      mov: "video/quicktime", mkv: "video/x-matroska",
      pdf: "application/pdf", txt: "text/plain", md: "text/plain",
      json: "application/json", csv: "text/csv",
    };

    const handleDroppedPaths = (paths: string[]) => {
      for (const filePath of paths) {
        const name = filePath.split(/[\\/]/).pop() || "file";
        const ext = (name.split(".").pop() || "").toLowerCase();
        const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
        const isVideo = ["mp4", "webm", "avi", "mov", "mkv"].includes(ext);
        const mimeType = mimeMap[ext] || "application/octet-stream";
        readFileBase64(filePath)
          .then((dataUrl) => {
            if (cancelled) return;
            if (isVideo) {
              const commaIdx = dataUrl.indexOf(",");
              const base64Len = commaIdx >= 0 ? dataUrl.length - commaIdx - 1 : dataUrl.length;
              const estimatedSize = base64Len * 3 / 4;
              const VIDEO_MAX_SIZE = 7 * 1024 * 1024;
              if (estimatedSize > VIDEO_MAX_SIZE) {
                notifyError(`视频文件过大 (${(estimatedSize / 1024 / 1024).toFixed(1)}MB)，最大支持 7MB（base64 编码后需 < 10MB）`);
                return;
              }
            }
            setPendingAttachments((prev) => [...prev, {
              type: isImage ? "image" : isVideo ? "video" : "file",
              name,
              previewUrl: isImage ? dataUrl : undefined,
              url: dataUrl,
              mimeType,
            }]);
          })
          .catch((err) => logger.error("Chat", "DragDrop read_file_base64 failed", { name, error: String(err) }));
      }
    };

    onDragDrop({
      onEnter: () => { if (!cancelled) setDragOver(true); },
      onOver: () => { if (!cancelled) setDragOver(true); },
      onLeave: () => { if (!cancelled) setDragOver(false); },
      onDrop: (paths) => {
        if (cancelled) return;
        setDragOver(false);
        handleDroppedPaths(paths);
      },
    }).then((unsub) => { unlisten = unsub; });

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // ── 语音录制 ──
  const [recordingDuration, setRecordingDuration] = useState(0);
  const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      mediaRecorderRef.current?.stop();
      setIsRecording(false);
      if (recordingTimerRef.current) { clearInterval(recordingTimerRef.current); recordingTimerRef.current = null; }
      setRecordingDuration(0);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm"
        : MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4"
        : MediaRecorder.isTypeSupported("audio/ogg") ? "audio/ogg" : "";
      const ext = mimeType.includes("mp4") ? "m4a" : mimeType.includes("ogg") ? "ogg" : "webm";
      const opts: MediaRecorderOptions = mimeType ? { mimeType } : {};
      const mediaRecorder = new MediaRecorder(stream, opts);
      const uploadId = genId();
      audioChunksRef.current = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: mimeType || "audio/webm" });
        const localPreview = URL.createObjectURL(blob);
        blobUrlsRef.current.push(localPreview);
        const filename = `voice-${Date.now()}.${ext}`;
        const tempAtt: ChatAttachment = {
          type: "voice", name: filename, previewUrl: localPreview,
          size: blob.size, mimeType: mimeType || "audio/webm", _uploadId: uploadId,
        };
        setPendingAttachments((prev) => [...prev, tempAtt]);
        uploadFile(blob, filename)
          .then((serverUrl) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a._uploadId === uploadId ? { ...a, url: `${apiBaseRef.current}${serverUrl}` } : a)
            );
          })
          .catch(() => {
            notifyError(t("chat.voiceUploadFailed", "语音上传失败"));
            setPendingAttachments((prev) => prev.filter((a) => a._uploadId !== uploadId || a.url));
          });
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.start();
      setIsRecording(true);
      setRecordingDuration(0);
      recordingTimerRef.current = setInterval(() => setRecordingDuration(d => d + 1), 1000);
    } catch (err: any) {
      const name = err?.name || "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        notifyError(t("chat.micPermissionDenied", "麦克风权限被拒绝，请在浏览器/系统设置中允许访问"));
      } else if (name === "NotFoundError") {
        notifyError(t("chat.micNotFound", "未检测到麦克风设备"));
      } else {
        notifyError(t("chat.micError", "无法访问麦克风，请检查浏览器权限设置"));
      }
    }
  }, [isRecording]);

  const [atAgentOpen, setAtAgentOpen] = useState(false);
  const [atAgentFilter, setAtAgentFilter] = useState("");
  const [atAgentIdx, setAtAgentIdx] = useState(0);

  // ── 输入框键盘处理 ──
  const handleInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // macOS 中文输入法按回车选字时 isComposing=true，此时不应触发发送
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;

    // Undo/Redo (6.2)
    if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) {
      e.preventDefault();
      if (undoIdxRef.current > 0) {
        undoIdxRef.current--;
        setInputValue(undoStackRef.current[undoIdxRef.current]);
      }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === "Z" || (e.key === "z" && e.shiftKey))) {
      e.preventDefault();
      if (undoIdxRef.current < undoStackRef.current.length - 1) {
        undoIdxRef.current++;
        setInputValue(undoStackRef.current[undoIdxRef.current]);
      }
      return;
    }

    if (atAgentOpen) {
      const q = atAgentFilter;
      const agents = agentProfiles.filter((a) => a.name.toLowerCase().includes(q) || a.id.toLowerCase().includes(q));
      if (e.key === "ArrowDown") { e.preventDefault(); setAtAgentIdx((i) => Math.min(i + 1, agents.length - 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setAtAgentIdx((i) => Math.max(0, i - 1)); return; }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const agent = agents[atAgentIdx];
        if (agent) {
          setSelectedAgent(agent.id);
          const ta = e.target as HTMLTextAreaElement;
          const val = ta.value;
          const cursor = ta.selectionStart ?? val.length;
          const before = val.slice(0, cursor).replace(/@\w*$/, "");
          setInputValue(before + val.slice(cursor));
        }
        setAtAgentOpen(false);
        return;
      }
      if (e.key === "Escape") { setAtAgentOpen(false); return; }
    }

    if (slashOpen) {
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
          setInputValue("");
          setSlashOpen(false);
        }
      } else if (e.key === "Escape") {
        setSlashOpen(false);
      }
      return;
    }

    if (isCurrentConvStreaming) {
      // 当前会话正在流式传输:
      //   Escape             = 停止生成（快捷键面板打开时让面板处理）
      //   有文本 + Ctrl+Enter = 立即插入（仅当前会话流式时可用）
      //   有文本 + Enter     = 排队
      //   空文本 + Enter     = 取队列第一条立即插入
      if (e.key === "Escape" && !shortcutsOpen) {
        e.preventDefault();
        handleCancelTask();
        return;
      }
      const domText = (e.target as HTMLTextAreaElement).value.trim();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (domText) {
          handleInsertMessage(domText);
          setInputValue("");
        }
      } else if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (domText) {
          handleQueueMessage();
        } else {
          const myFirst = messageQueue.find(m => m.convId === activeConvId);
          if (myFirst) {
            setMessageQueue(prev => prev.filter(m => m.id !== myFirst.id));
            handleInsertMessage(myFirst.text);
          }
        }
      }
    } else {
      // 非当前会话流式中: Enter / Ctrl+Enter 直接发送（后端支持并发）
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        sendMessage();
      } else if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        sendMessage();
      }
    }
  }, [atAgentOpen, atAgentFilter, atAgentIdx, agentProfiles, slashOpen, slashFilter, slashCommands, slashSelectedIdx, sendMessage, isCurrentConvStreaming, handleInsertMessage, handleQueueMessage, messageQueue, activeConvId, setInputValue, shortcutsOpen, handleCancelTask]);

  // ── 输入变化处理（非受控模式：仅更新 ref，不触发全局重渲染） ──
  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    inputTextRef.current = val;
    const has = val.trim().length > 0;
    setHasInputText(prev => prev !== has ? has : prev);
    pushUndoSnapshot(val);

    // @org: 前缀检测 — 自动切换到组织模式
    const orgMatch = val.match(/^@org:(\S+)\s/);
    if (orgMatch && !orgMode) {
      const target = orgMatch[1];
      const match = orgList.find(o => o.name.includes(target) || o.id === target);
      if (match) {
        setOrgMode(true);
        setSelectedOrgId(match.id);
      }
    }

    // @agent 联想
    const cursor = e.target.selectionStart ?? val.length;
    const beforeCursor = val.slice(0, cursor);
    const atMatch = beforeCursor.match(/@(\w*)$/);
    if (atMatch && multiAgentEnabled && agentProfiles.length > 0) {
      setAtAgentOpen(true);
      setAtAgentFilter(atMatch[1].toLowerCase());
      setAtAgentIdx(0);
    } else {
      setAtAgentOpen(false);
    }

    if (val.startsWith("/") && !val.includes(" ")) {
      setSlashOpen(true);
      setSlashFilter(val.slice(1));
      setSlashSelectedIdx(0);
    } else {
      setSlashOpen(false);
    }
  }, [orgMode, orgList, multiAgentEnabled, agentProfiles.length, pushUndoSnapshot]);

  // ── Filtered + grouped conversations for Cursor-style sidebar ──
  const filteredConversations = useMemo(() => {
    const q = convSearchQuery.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) =>
      c.title.toLowerCase().includes(q) ||
      (c.lastMessage || "").toLowerCase().includes(q)
    );
  }, [conversations, convSearchQuery]);

  const pinnedConvs = useMemo(() =>
    filteredConversations.filter((c) => c.pinned).sort((a, b) => b.timestamp - a.timestamp),
    [filteredConversations]
  );
  const agentConvs = useMemo(() =>
    filteredConversations.filter((c) => !c.pinned).sort((a, b) => b.timestamp - a.timestamp),
    [filteredConversations]
  );

  // ── 未启动服务提示 ──
  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconMessageCircle size={48} />
        <div className="mt-3 font-semibold">{t("chat.title")}</div>
        <div className="mt-1 text-xs opacity-50">{t("chat.serviceNotRunning", "后端服务未启动，请启动后再进行使用")}</div>
      </div>
    );
  }

  const statusIcon = (status?: ConversationStatus) => {
    switch (status) {
      case "running":
        return <span className="convStatusDot convStatusRunning"><IconLoader size={12} /></span>;
      case "completed":
        return <span className="convStatusDot convStatusCompleted"><IconCheck size={12} /></span>;
      case "error":
        return <span className="convStatusDot convStatusError"><IconXCircle size={12} /></span>;
      default:
        return <span className="convStatusDot convStatusIdle"><IconCircleDot size={12} /></span>;
    }
  };

  const renderConvItem = (conv: ChatConversation) => {
    const isActive = conv.id === activeConvId;
    const profileId = conv.agentProfileId || "default";
    const agentProfile = agentProfiles.find((p) => p.id === profileId) ?? null;
    return (
      <div
        key={conv.id}
        className={`convItem ${isActive ? "convItemActive" : ""}`}
        onClick={() => { if (renamingId !== conv.id) setActiveConvId(conv.id); }}
        onContextMenu={(e) => { e.preventDefault(); (e.nativeEvent as any)._handled = true; setCtxMenu({ x: e.clientX, y: e.clientY, convId: conv.id }); }}
      >
        <div className="convItemIcon">
          <span title={agentProfile?.name || ""} style={{ fontSize: 16 }}>{agentProfile?.icon || "💬"}</span>
        </div>
        <div className="convItemBody">
          {renamingId === conv.id ? (
            <input
              autoFocus
              value={renameText}
              onChange={(e) => setRenameText(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                if (e.key === "Enter") confirmRename(conv.id, renameText);
                if (e.key === "Escape") { setRenamingId(null); setRenameText(""); }
              }}
              onBlur={() => confirmRename(conv.id, renameText)}
              onClick={(e) => e.stopPropagation()}
              className="convRenameInput"
            />
          ) : (
            <>
              <div className="convItemTitle">{conv.title}</div>
              <div className="convItemMeta">
                {agentProfile && <span className="convItemAgent">{agentProfile.name}</span>}
                {conv.lastMessage && <span className="convItemDesc">{conv.lastMessage.slice(0, 40)}</span>}
              </div>
            </>
          )}
        </div>
        <div className="convItemRight">
          <span className="convItemTime">{timeAgo(conv.timestamp)}</span>
          {isConvBusyOnOtherDevice(conv.id)
            ? <span className="convStatusDot" style={{ color: "var(--warning, #eab308)", fontSize: 10, whiteSpace: "nowrap" }} title={t("chat.busyOnOtherDevice")}>⏳</span>
            : statusIcon(conv.status)}
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0 }}>

      {/* 会话右键菜单 — portal 到 body 避免父级 backdrop-filter 影响 fixed 定位 */}
      {ctxMenu && createPortal(
        <div
          style={{ position: "fixed", inset: 0, zIndex: 9999 }}
          onClick={() => setCtxMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setCtxMenu(null); }}
        >
          <ContextMenuInner ctxMenu={ctxMenu} setCtxMenu={setCtxMenu}>
            {([
              {
                label: conversations.find((c) => c.id === ctxMenu.convId)?.pinned
                  ? t("chat.unpinConversation") : t("chat.pinConversation"),
                icon: <IconPin size={13} />,
                danger: false,
                action: () => { togglePinConversation(ctxMenu.convId); setCtxMenu(null); },
              },
              {
                label: t("chat.renameConversation"),
                icon: <IconEdit size={13} />,
                danger: false,
                action: () => {
                  const conv = conversations.find((c) => c.id === ctxMenu.convId);
                  if (conv) { setRenamingId(conv.id); setRenameText(conv.title); }
                  setCtxMenu(null);
                },
              },
              {
                label: t("chat.exportConversation", "导出会话"),
                icon: <IconDownload size={13} />,
                danger: false,
                action: () => {
                  const conv = conversations.find((c) => c.id === ctxMenu.convId);
                  const convMsgs = ctxMenu.convId === activeConvId
                    ? messages
                    : loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + ctxMenu.convId);
                  exportConversation(convMsgs, conv?.title || t("chat.conversation", "对话"), "md");
                  setCtxMenu(null);
                },
              },
              {
                label: t("chat.deleteConversation"),
                icon: <IconTrash size={13} />,
                danger: true,
                action: () => { deleteConversation(ctxMenu.convId); setCtxMenu(null); },
              },
            ]).map((item, i) => (
              <div
                key={i}
                onClick={item.action}
                style={{
                  padding: "8px 14px",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  color: item.danger ? "#ef4444" : "inherit",
                  transition: "background 0.1s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = item.danger ? "rgba(239,68,68,0.08)" : "rgba(37,99,235,0.08)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                <span style={{ opacity: 0.6, display: "flex" }}>{item.icon}</span>
                {item.label}
              </div>
            ))}
          </ContextMenuInner>
        </div>,
        document.body,
      )}

      {/* 主聊天区 */}
      <div className="flex min-w-0 flex-1 flex-col" onMouseDown={() => { if (sidebarOpen && !sidebarPinned) setSidebarOpen(false); }}>
        {/* Chat top bar */}
        <div className="chatTopBar">
          <button onClick={newConversation} className="chatTopBarBtn" aria-label={t("chat.newConversation", "新建会话")}>
            <IconPlus size={14} />
          </button>

          {/* Active agent orbits — shown when sidebar is closed */}
          {!sidebarOpen && conversations.length > 0 && (
            <div className="agentOrbitStrip">
              {conversations
                .slice()
                .sort((a, b) => b.timestamp - a.timestamp)
                .slice(0, 8)
                .map((conv) => {
                  const pid = conv.agentProfileId || "default";
                  const ap = agentProfiles.find((p) => p.id === pid) ?? null;
                  const isActive = conv.id === activeConvId;
                  const isRunning = conv.status === "running" || streamContexts.current.has(conv.id);
                  return (
                    <button
                      key={conv.id}
                      className={`agentOrbitNode ${isActive ? "agentOrbitActive" : ""} ${isRunning ? "agentOrbitRunning" : ""}`}
                      onClick={() => setActiveConvId(conv.id)}
                      onMouseEnter={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setOrbitTip({ x: rect.left + rect.width / 2, y: rect.bottom + 6, name: ap?.name || "Default", title: conv.title });
                      }}
                      onMouseLeave={() => setOrbitTip(null)}
                    >
                      <span className="agentOrbitIcon">
                        {ap?.icon || "💬"}
                      </span>
                      {isRunning && <span className="agentOrbitPulse" />}
                    </button>
                  );
                })}
            </div>
          )}

          {/* Active sub-agents in current conversation */}
          {displayActiveSubAgents.length > 0 && (
            <div className="subAgentStrip">
              <span className="subAgentLabel">{t("chat.collaborating", "协作中")}</span>
              {displayActiveSubAgents.map((sub) => {
                const sp = agentProfiles.find((p) => p.id === sub.agentId);
                return (
                  <div
                    key={sub.agentId}
                    className={`subAgentChip ${sub.status === "delegating" ? "subAgentActive" : sub.status === "error" ? "subAgentError" : "subAgentDone"}`}
                    title={sp?.name || sub.agentId}
                  >
                    <span className="subAgentChipIcon"><RenderIcon icon={sp?.icon || "🤖"} size={14} /></span>
                    <span className="subAgentChipName">{sp?.name || sub.agentId}</span>
                    {sub.status === "delegating" && <span className="subAgentSpinner" />}
                    {sub.status === "done" && <span className="subAgentCheck">✓</span>}
                    {sub.status === "error" && <span className="subAgentCross">✗</span>}
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ flex: 1 }} />

          <button
            onClick={() => setShowChain(v => !v)}
            className="chatTopBarBtn chainToggleBtn"
            title={showChain ? t("chat.hideChain") : t("chat.showChain")}
            style={{ opacity: showChain ? 1 : 0.4 }}
          >
            <IconZap size={14} />
          </button>

          <button
            onClick={() => setDisplayMode(v => v === "bubble" ? "flat" : "bubble")}
            className="chatTopBarBtn modeToggleBtn"
            title={displayMode === "bubble" ? t("chat.flatMode") : t("chat.bubbleMode")}
          >
            <IconMessageCircle size={14} />
            <span style={{ fontSize: 11, marginLeft: 2 }}>
              {displayMode === "bubble" ? t("chat.flatMode") : t("chat.bubbleMode")}
            </span>
          </button>

          <button
            onClick={() => setSidebarOpen((v) => !v)}
            className="chatTopBarBtn"
            style={{ background: sidebarOpen ? "rgba(37,99,235,0.08)" : "transparent" }}
            title={t("chat.toggleHistory") || "会话列表"}
          >
            <IconMenu size={16} />
          </button>
        </div>

        {/* 消息搜索栏 */}
        {msgSearchOpen && (() => {
          const q = msgSearchQuery.trim().toLowerCase();
          const matches = q ? messages.reduce<number[]>((acc, m, idx) => {
            if (m.content.toLowerCase().includes(q)) acc.push(idx);
            return acc;
          }, []) : [];
          const total = matches.length;
          return (
            <div className="flex items-center gap-2 border-b border-border/60 bg-muted/20 px-4 py-2 text-sm">
              <input
                ref={msgSearchRef}
                value={msgSearchQuery}
                onChange={(e) => { setMsgSearchQuery(e.target.value); setMsgSearchIdx(0); }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    if (total > 0) {
                      const nextIdx = e.shiftKey
                        ? (msgSearchIdx - 1 + total) % total
                        : (msgSearchIdx + 1) % total;
                      setMsgSearchIdx(nextIdx);
                      messageListRef.current?.scrollToIndex(matches[nextIdx], "center");
                    }
                  }
                  if (e.key === "Escape") { setMsgSearchOpen(false); setMsgSearchQuery(""); }
                }}
                placeholder={t("chat.searchMessages", "搜索消息...")}
                style={{
                  flex: 1, background: "var(--bg)", border: "1px solid var(--line)",
                  borderRadius: 8, padding: "6px 10px", fontSize: 13, outline: "none",
                  color: "var(--fg)",
                }}
              />
              {q && <span style={{ opacity: 0.5, fontSize: 11, whiteSpace: "nowrap" }}>{total > 0 ? `${msgSearchIdx + 1}/${total}` : t("common.noResults", "无结果")}</span>}
              <button onClick={() => { setMsgSearchOpen(false); setMsgSearchQuery(""); }} style={{ background: "none", border: "none", cursor: "pointer", opacity: 0.5, padding: 2 }}>
                <IconX size={14} />
              </button>
            </div>
          );
        })()}

        {/* 离线横幅 */}
        {!serviceRunning && (
          <div className="flex items-center gap-2 border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-xs text-amber-600 dark:text-amber-400">
            <span style={{ fontSize: 14 }}>⚠️</span>
            {t("chat.offline", "后端服务未连接，部分功能暂不可用")}
          </div>
        )}

        {/* 消息列表 */}
        <div ref={scrollContainerRef} role="log" aria-live="polite" aria-label={t("chat.messageList", "消息列表")} className="flex min-h-0 flex-1 flex-col overflow-hidden px-5 py-4">
          {hydrating && messages.length === 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 18, padding: "16px 0", animation: "pulse 1.5s ease-in-out infinite" }}>
              {[0.6, 0.85, 0.45].map((w, i) => (
                <div key={i} style={{ display: "flex", gap: 10, flexDirection: i % 2 === 0 ? "row" : "row-reverse" }}>
                  <div style={{ width: 32, height: 32, borderRadius: "50%", background: "var(--line)", flexShrink: 0 }} />
                  <div style={{ flex: 1, maxWidth: `${w * 100}%` }}>
                    <div style={{ height: 12, borderRadius: 6, background: "var(--line)", marginBottom: 8, width: "70%" }} />
                    <div style={{ height: 12, borderRadius: 6, background: "var(--line)", width: "90%" }} />
                    <div style={{ height: 12, borderRadius: 6, background: "var(--line)", marginTop: 8, width: "50%" }} />
                  </div>
                </div>
              ))}
            </div>
          )}
          {!hydrating && messages.length === 0 && (
            <div className="flex flex-1 flex-col items-center justify-center gap-6">
              <div className="flex flex-col items-center text-center opacity-50">
                <IconMessageCircle size={48} style={{ marginBottom: 12 }} />
                <div className="text-base font-semibold">{t("chat.emptyTitle")}</div>
                <div className="mt-1 text-sm text-muted-foreground">{t("chat.emptyDesc")}</div>
              </div>
              <div className="grid w-full max-w-[520px] grid-cols-1 gap-3 sm:grid-cols-2">
                {[
                  { id: "research", icon: "📊", text: t("chat.quickStart.research", "做一份 XX 领域的市场调研报告") },
                  { id: "ppt", icon: "📝", text: t("chat.quickStart.ppt", "帮我做一个项目汇报 PPT 大纲") },
                  { id: "search", icon: "🌐", text: t("chat.quickStart.search", "打开百度搜索 XX") },
                  { id: "email", icon: "✉️", text: t("chat.quickStart.email", "帮我写一封商务邮件") },
                ].map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setInputValue(item.text)}
                    className="quickStartCard"
                    style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "14px 16px", borderRadius: 14,
                      border: "1px solid var(--line)", background: "var(--panel2)",
                      cursor: "pointer", textAlign: "left", fontSize: 13,
                      transition: "border-color 0.15s, background 0.15s",
                    }}
                  >
                    <span style={{ fontSize: 20, flexShrink: 0 }}>{item.icon}</span>
                    <span style={{ color: "var(--text)", lineHeight: 1.4 }}>{item.text}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.length > 0 && (
          <ErrorBoundary>
          <MessageList
            ref={messageListRef}
            messages={messages}
            displayMode={displayMode}
            showChain={showChain}
            apiBaseUrl={apiBaseUrl}
            mdModules={mdModules}
            isStreaming={isCurrentConvStreaming}
            onAtBottomChange={(atBottom) => { isMessageListAtBottomRef.current = atBottom; }}
            onAskAnswer={handleAskAnswer}
            onRetry={handleRegenerate}
            onEdit={handleEditMessage}
            onRegenerate={handleRegenerate}
            onRewind={handleRewind}
            onSkipStep={handleSkipStep}
            onImagePreview={handleImagePreview}
          />
          </ErrorBoundary>
          )}
        </div>

        {/* Sub-agent progress cards */}
        {displaySubAgentTasks.length > 0 && (
          <div style={{ flexShrink: 0, padding: "0 20px 8px" }}>
            <SubAgentCards tasks={displaySubAgentTasks} />
          </div>
        )}

        {/* Plan 审批面板 —— exit_plan_mode 后弹出，等待用户批准或修改 */}
        {pendingApproval && (
          <PlanApprovalPanel
            approval={pendingApproval}
            plan={
              [...messages].reverse().find(
                (m) => m.todo && m.todo.id === pendingApproval.plan_id
              )?.todo ?? null
            }
            onApprove={handlePlanApprove}
            onReject={handlePlanReject}
            onDismiss={handlePlanDismiss}
          />
        )}

        {/* 浮动 Plan 进度条 —— 贴在输入框上方，仅显示进行中的 plan */}
        {(() => {
          const activePlan = [...messages].reverse().find((m) => m.todo && m.todo.status !== "completed" && m.todo.status !== "failed" && m.todo.status !== "cancelled")?.todo;
          return activePlan ? <FloatingPlanBar plan={activePlan} /> : null;
        })()}

        {/* 长闲置回归提示 (6.7) */}
        {idleReturnPrompt && (
          <div className="flex items-center gap-3 border-t border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-sm">
            <span>⏰</span>
            <span style={{ flex: 1 }}>{t("chat.idleReturnHint", "你已离开较长时间，当前会话上下文较长。建议使用 /clear 节省 token 或新建会话。")}</span>
            <button
              onClick={() => { setIdleReturnPrompt(false); newConversation(); }}
              style={{ padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--primary)", color: "#fff", cursor: "pointer", fontSize: 12, whiteSpace: "nowrap" }}
            >{t("chat.newConversation", "新建会话")}</button>
            <button
              onClick={() => setIdleReturnPrompt(false)}
              style={{ padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "transparent", color: "var(--text)", cursor: "pointer", fontSize: 12, whiteSpace: "nowrap" }}
            >{t("common.dismiss", "忽略")}</button>
          </div>
        )}

        {/* 大文本粘贴预览 (6.4) */}
        {pastedLargeText && (
          <div className="border-t border-border/60 bg-muted/20 px-4 py-2.5 text-sm">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ opacity: 0.7 }}>
                {t("chat.largePaste", "粘贴文本")} — {pastedLargeText.text.length} {t("common.chars", "字符")} / {pastedLargeText.lines} {t("common.lines", "行")}
              </span>
              <span style={{ display: "flex", gap: 6 }}>
                <button
                  onClick={() => {
                    const newVal = inputTextRef.current + pastedLargeText.text;
                    setInputValue(newVal);
                    // Immediate undo snapshot (bypass debounce for explicit actions)
                    if (undoDebounceRef.current) clearTimeout(undoDebounceRef.current);
                    const stack = undoStackRef.current;
                    const idx = undoIdxRef.current;
                    if (stack[idx] !== newVal) {
                      const trimmed = stack.slice(0, idx + 1);
                      trimmed.push(newVal);
                      if (trimmed.length > UNDO_MAX_STEPS) trimmed.shift();
                      undoStackRef.current = trimmed;
                      undoIdxRef.current = trimmed.length - 1;
                    }
                    setPastedLargeText(null);
                  }}
                  style={{ padding: "3px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--primary)", color: "#fff", cursor: "pointer", fontSize: 12 }}
                >
                  {t("common.insert", "插入")}
                </button>
                <button
                  onClick={() => setPastedLargeText(null)}
                  style={{ padding: "3px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "transparent", color: "var(--text)", cursor: "pointer", fontSize: 12 }}
                >
                  {t("common.discard", "丢弃")}
                </button>
              </span>
            </div>
            <pre style={{ maxHeight: 80, overflow: "auto", padding: 8, background: "var(--bg)", borderRadius: 6, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0 }}>
              {pastedLargeText.text.slice(0, 500)}{pastedLargeText.text.length > 500 ? "\n..." : ""}
            </pre>
          </div>
        )}

        {/* 附件预览栏 */}
        {pendingAttachments.length > 0 && (
          <div className="flex max-h-[140px] flex-wrap gap-3 overflow-y-auto border-t border-border/60 bg-muted/20 px-4 py-3">
            {pendingAttachments.map((att, idx) => (
              <AttachmentPreview
                key={`${att.name}-${att.type}-${idx}`}
                att={att}
                onRemove={() => setPendingAttachments((prev) => prev.filter((_, i) => i !== idx))}
              />
            ))}
          </div>
        )}

        {/* Org Flow Status Panel */}
        {orgCommandPending && orgNodeStates.size > 0 && (
          <div style={{
            margin: "0 16px 8px", borderRadius: 12,
            border: "1px solid var(--border, rgba(255,255,255,0.1))",
            background: "var(--card, rgba(0,0,0,0.03))",
            overflow: "hidden",
            transition: "opacity 0.2s ease",
            flexShrink: 0,
          }}>
            <button
              onClick={() => setOrgFlowPanelOpen(p => !p)}
              style={{
                width: "100%", display: "flex", alignItems: "center", gap: 8,
                padding: "8px 14px", border: "none", background: "transparent",
                color: "var(--text)", cursor: "pointer", fontSize: 13, fontWeight: 600,
              }}
            >
              <span>{orgFlowPanelOpen ? "▼" : "▶"}</span>
              <span>{t("chat.orgFlowPanel", "组织协调状态")}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, opacity: 0.6 }}>
                {orgNodeStates.size} {t("chat.orgNodes", "节点")}
              </span>
            </button>
            {orgFlowPanelOpen && (
              <div style={{ padding: "4px 14px 12px", display: "flex", flexWrap: "wrap", gap: 8, maxHeight: 120, overflowY: "auto" }}>
                {Array.from(orgNodeStates.entries()).map(([nid, ns]) => {
                  const color = ns.status === "busy" ? "#22c55e" : ns.status === "done" || ns.status === "idle" ? "#3b82f6" : ns.status === "error" ? "#ef4444" : ns.status === "timeout" ? "#f59e0b" : "#6b7280";
                  const icon = ns.status === "busy" ? "🟢" : ns.status === "done" || ns.status === "idle" ? "🔵" : ns.status === "error" ? "🔴" : ns.status === "timeout" ? "🟡" : "⚪";
                  return (
                    <div key={nid} style={{
                      display: "flex", alignItems: "center", gap: 6,
                      padding: "4px 10px", borderRadius: 8, fontSize: 12,
                      background: `${color}15`, border: `1px solid ${color}30`,
                    }}>
                      <span>{icon}</span>
                      <span style={{ fontWeight: 600 }}>{nid}</span>
                      {ns.task && <span style={{ opacity: 0.7, maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ns.task}</span>}
                    </div>
                  );
                })}
                {orgDelegations.length > 0 && (
                  <div style={{ width: "100%", marginTop: 4, fontSize: 11, opacity: 0.6 }}>
                    {orgDelegations.slice(-5).map((d, i) => (
                      <div key={i}>📋 {d.from} → {d.to}: {d.task.slice(0, 40)}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* IM channel alert banners */}
        {imChannelAlerts.filter((a) => a.status === "offline").map((a) => (
          <div key={a.channel} style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "8px 16px", margin: "0 16px 6px",
            borderRadius: 10, fontSize: 13,
            background: "rgba(239,68,68,0.10)", color: "var(--text)",
            border: "1px solid rgba(239,68,68,0.25)",
          }}>
            <span style={{ fontSize: 16 }}>🔌</span>
            <span style={{ flex: 1 }}>
              {t("chat.imChannelDisconnected", { channel: a.channel, defaultValue: `IM 通道 "${a.channel}" 已断开` })}
            </span>
            <button
              onClick={() => setImChannelAlerts((prev) => prev.filter((x) => x.channel !== a.channel))}
              style={{
                padding: "2px 8px", borderRadius: 4, border: "none",
                background: "transparent", color: "var(--muted-foreground)",
                cursor: "pointer", fontSize: 11,
              }}
            >✕</button>
          </div>
        ))}
        {imChannelAlerts.filter((a) => a.status === "online").map((a) => (
          <div key={`${a.channel}-online`} style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "6px 16px", margin: "0 16px 4px",
            borderRadius: 10, fontSize: 12,
            background: "rgba(34,197,94,0.10)", color: "var(--text)",
            border: "1px solid rgba(34,197,94,0.25)",
          }}>
            <span style={{ fontSize: 14 }}>✅</span>
            <span>{t("chat.imChannelReconnected", { channel: a.channel, defaultValue: `IM 通道 "${a.channel}" 已重连` })}</span>
          </div>
        ))}

        {/* Busy-on-other-device banner */}
        {activeConvId && isConvBusyOnOtherDevice(activeConvId) && (
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "8px 16px", margin: "0 16px 6px",
            borderRadius: 10, fontSize: 13,
            background: "rgba(234,179,8,0.12)", color: "var(--text)",
            border: "1px solid rgba(234,179,8,0.25)",
          }}>
            <span style={{ fontSize: 16 }}>⏳</span>
            <span style={{ flex: 1 }}>{t("chat.busyOnOtherDevice")}</span>
            <button
              onClick={newConversation}
              style={{
                padding: "4px 12px", borderRadius: 6, border: "none",
                background: "var(--primary, #3b82f6)", color: "#fff",
                cursor: "pointer", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap",
              }}
            >{t("chat.busyNewConversation")}</button>
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
                setInputValue("");
                setSlashOpen(false);
              }}
              selectedIdx={slashSelectedIdx}
            />
          )}

          {/* @Agent 联想面板 */}
          {atAgentOpen && (() => {
            const agents = agentProfiles.filter((a) =>
              a.name.toLowerCase().includes(atAgentFilter) || a.id.toLowerCase().includes(atAgentFilter),
            );
            if (agents.length === 0) return null;
            return (
              <div style={{
                position: "absolute", bottom: "100%", left: 0, right: 0,
                background: "var(--panel)", border: "1px solid var(--line)",
                borderRadius: 10, boxShadow: "0 -4px 16px rgba(0,0,0,0.12)",
                maxHeight: 200, overflow: "auto", zIndex: 100,
                padding: "4px 0", marginBottom: 4,
              }}>
                {agents.map((a, i) => (
                  <div
                    key={a.id}
                    onClick={() => {
                      setSelectedAgent(a.id);
                      const ta = inputRef.current;
                      if (ta) {
                        const val = ta.value;
                        const cursor = ta.selectionStart ?? val.length;
                        const before = val.slice(0, cursor).replace(/@\w*$/, "");
                        setInputValue(before + val.slice(cursor));
                      }
                      setAtAgentOpen(false);
                      inputRef.current?.focus();
                    }}
                    style={{
                      padding: "6px 12px", cursor: "pointer", display: "flex", alignItems: "center", gap: 8,
                      background: i === atAgentIdx ? "rgba(37,99,235,0.08)" : "transparent",
                      transition: "background 0.1s",
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(37,99,235,0.08)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = i === atAgentIdx ? "rgba(37,99,235,0.08)" : "transparent"; }}
                  >
                    <span style={{ fontSize: 16 }}>{a.icon || "🤖"}</span>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{a.name}</div>
                      {a.description && <div style={{ fontSize: 11, opacity: 0.5 }}>{a.description}</div>}
                    </div>
                  </div>
                ))}
              </div>
            );
          })()}

          {/* Queued messages list — Cursor style, per-session */}
          {(() => {
            const currentQueue = messageQueue.filter(m => m.convId === activeConvId);
            if (currentQueue.length === 0) return null;
            return (
              <div className="queuedContainer">
                <button
                  className="queuedHeader"
                  onClick={() => setQueueExpanded(v => !v)}
                >
                  <span className="queuedHeaderChevron">
                    {queueExpanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
                  </span>
                  <span className="queuedHeaderLabel">
                    {currentQueue.length} {t("chat.queuedCount")}
                  </span>
                </button>
                {queueExpanded && (
                  <div className="queuedList">
                    {currentQueue.map((qm, idx) => (
                      <div key={qm.id} className="queuedItem">
                        <span className="queuedItemIndicator">
                          <IconCircle size={10} />
                        </span>
                        <span className="queuedItemText" title={qm.text}>
                          {qm.text.length > 80 ? qm.text.slice(0, 80) + "..." : qm.text}
                        </span>
                        <div className="queuedItemActions">
                          <button
                            data-slot="queued"
                            className="queuedItemBtn queuedItemSendBtn"
                            onClick={() => handleSendQueuedNow(qm.id)}
                            title={t("chat.sendNow")}
                          >
                            <IconSend size={12} />
                          </button>
                          <button
                            data-slot="queued"
                            className="queuedItemBtn"
                            onClick={() => handleEditQueued(qm.id)}
                            title={t("chat.editMessage")}
                          >
                            <IconEdit size={13} />
                          </button>
                          <button
                            className="queuedItemBtn"
                            onClick={() => handleMoveQueued(qm.id, "up")}
                            disabled={idx === 0}
                            title="Move up"
                          >
                            <IconChevronUp size={13} />
                          </button>
                          <button
                            className="queuedItemBtn queuedItemDeleteBtn"
                            onClick={() => handleRemoveQueued(qm.id)}
                            title={t("chat.deleteQueued")}
                          >
                            <IconTrash size={13} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}

          <div className={`chatInputBox ${chatMode === "plan" ? "chatInputBoxPlan" : chatMode === "ask" ? "chatInputBoxAsk" : ""}`}>
            {/* Top row: compact model picker */}
            <div className="chatInputTop" ref={modelMenuRef} style={{ position: "relative" }}>
              <button
                className="chatModelPickerBtn"
                onClick={() => setModelMenuOpen((v) => !v)}
              >
                <span className="chatModelPickerLabel">
                  {selectedEndpoint === "auto"
                    ? (() => {
                        const ap = multiAgentEnabled ? agentProfiles.find(p => p.id === selectedAgent) : null;
                        const pe = ap?.preferred_endpoint;
                        if (pe) {
                          const ep = endpoints.find(e => e.name === pe);
                          return `${t("chat.selectModel")} → ${ep ? ep.model : pe}`;
                        }
                        return t("chat.selectModel");
                      })()
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
                  {endpoints.map((ep) => {
                    const hs = ep.health?.status;
                    const dot = hs === "healthy" ? "🟢" : hs === "degraded" ? "🟡" : hs === "unhealthy" ? "🔴" : "⚪";
                    return (
                      <div
                        key={ep.name}
                        className={`chatModelMenuItem ${selectedEndpoint === ep.name ? "chatModelMenuItemActive" : ""}`}
                        onClick={() => { setSelectedEndpoint(ep.name); setModelMenuOpen(false); }}
                      >
                        <span style={{ fontSize: 8, marginRight: 6, lineHeight: 1 }}>{dot}</span>
                        <span style={{ fontWeight: 600 }}>{ep.model}</span>
                        <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{ep.name}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {multiAgentEnabled && agentProfiles.length > 0 && !orgMode && (
                <div ref={agentMenuRef} style={{ position: "relative", marginLeft: 8 }}>
                  <button
                    className="chatModelPickerBtn"
                    onClick={() => setAgentMenuOpen((v) => !v)}
                    style={{ gap: 4 }}
                  >
                    <span style={{ fontSize: 13 }}>
                      {(() => {
                        const ap = agentProfiles.find(p => p.id === selectedAgent);
                        return ap ? `${ap.icon} ${ap.name}` : t("chat.agentDefault");
                      })()}
                    </span>
                    <IconChevronDown size={12} />
                  </button>
                  {agentMenuOpen && (
                    <div className="chatModelMenu" style={{ minWidth: 220 }}>
                      {!agentProfiles.some(p => p.id === "default") && (
                        <div
                          key="__default__"
                          className={`chatModelMenuItem ${selectedAgent === "default" ? "chatModelMenuItemActive" : ""}`}
                          onClick={() => { setSelectedAgent("default"); setAgentMenuOpen(false); }}
                        >
                          <span style={{ marginRight: 6 }}>🎯</span>
                          <span style={{ fontWeight: 600 }}>{t("chat.agentDefault")}</span>
                        </div>
                      )}
                      {agentProfiles.map((ap) => (
                        <div
                          key={ap.id}
                          className={`chatModelMenuItem ${selectedAgent === ap.id ? "chatModelMenuItemActive" : ""}`}
                          onClick={() => { setSelectedAgent(ap.id); setAgentMenuOpen(false); }}
                        >
                          <span style={{ marginRight: 6 }}>{ap.icon}</span>
                          <span style={{ fontWeight: 600 }}>{ap.name}</span>
                          <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{ap.description}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {/* Org mode selector */}
              {multiAgentEnabled && orgList.length > 0 && (
                <div ref={orgMenuRef} style={{ position: "relative", marginLeft: 8 }}>
                  <button
                    className="chatModelPickerBtn"
                    onClick={() => {
                      if (orgMode) {
                        setOrgMode(false);
                        setSelectedOrgId(null);
                        setOrgMenuOpen(false);
                      } else {
                        setOrgMenuOpen((v) => !v);
                      }
                    }}
                    style={{
                      gap: 4,
                      background: orgMode ? "rgba(14,165,233,0.15)" : undefined,
                      borderColor: orgMode ? "var(--primary)" : undefined,
                    }}
                  >
                    <span style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 4 }}>
                      <IconBuilding size={13} />
                      {orgMode && selectedOrgId
                        ? (() => { const o = orgList.find(x => x.id === selectedOrgId); return o ? o.name : "组织"; })()
                        : "组织"}
                    </span>
                    {orgMode ? <IconX size={10} /> : <IconChevronDown size={12} />}
                  </button>
                  {orgMenuOpen && (
                    <div className="chatModelMenu" style={{ minWidth: 200 }}>
                      {orgList.map((o) => (
                        <div
                          key={o.id}
                          className={`chatModelMenuItem ${selectedOrgId === o.id ? "chatModelMenuItemActive" : ""}`}
                          onClick={() => {
                            setOrgMode(true);
                            setSelectedOrgId(o.id);
                            setOrgMenuOpen(false);
                          }}
                        >
                          <IconBuilding size={13} style={{ marginRight: 4, flexShrink: 0 }} />
                          <span style={{ fontWeight: 600 }}>{o.name}</span>
                          <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{o.status}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Org mode hint bar */}
            {orgMode && selectedOrgId && (
              <div style={{
                fontSize: 11, color: "var(--primary)", padding: "4px 8px",
                background: "rgba(14,165,233,0.08)", borderRadius: 6, marginBottom: 4,
                display: "flex", alignItems: "center", gap: 6,
              }}>
                <IconBuilding size={12} />
                {t("chat.orgTalkingWith", "正在与「{{org}}」{{node}}对话", { org: orgList.find(o => o.id === selectedOrgId)?.name ?? "", node: selectedOrgNodeId ? ` / ${selectedOrgNodeId}` : "" })}
                {selectedOrgNodeId && (
                  <button
                    onClick={() => setSelectedOrgNodeId(null)}
                    style={{
                      background: "none", border: "none", cursor: "pointer",
                      color: "var(--muted)", fontSize: 10, padding: "0 2px",
                      display: "flex", alignItems: "center",
                    }}
                    title={t("chat.cancelNodeTarget", "取消节点指定，改为与整个组织对话")}
                  >
                    <IconX size={10} />
                  </button>
                )}
                {orgCommandPending && <span style={{ opacity: 0.6 }}> — {t("chat.orgCoordinating", "组织协调中，进度实时显示 ↓")}</span>}
              </div>
            )}

            {/* Textarea */}
            <textarea
              ref={inputRef}
              aria-label={t("chat.inputAriaLabel", "输入消息")}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              onPaste={handlePaste}
              placeholder={orgCommandPending ? t("chat.orgProcessing", "组织正在处理中...") : orgMode ? (selectedOrgNodeId ? t("chat.orgSendToNode", "输入指令发送给 {{node}}...", { node: selectedOrgNodeId }) : t("chat.orgSendToOrg", "输入指令发送给组织...")) : isCurrentConvStreaming ? `Enter ${t("chat.queueHint")}${t("chat.commaEscStop", "，Esc 停止")}` : chatMode === "plan" ? t("chat.planModePlaceholder", { enterSend: t("chat.enterSend") }) : chatMode === "ask" ? t("chat.askModePlaceholder") : `${t("chat.placeholder")}  · ${t("chat.enterSendSlash", "Enter 发送，Shift+Enter 换行，/ 命令")}`}
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
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button data-slot="toolbar" onClick={() => fileInputRef.current?.click()} className="chatInputIconBtn">
                      <IconPaperclip size={16} />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">{t("chat.attach")}</TooltipContent>
                </Tooltip>
                <input ref={fileInputRef} type="file" multiple accept="image/*,video/*,audio/*,.pdf,.txt,.md,.py,.js,.ts,.json,.csv" style={{ display: "none" }} onChange={handleFileSelect} />

                <Tooltip>
                  <TooltipTrigger asChild>
                    <button data-slot="toolbar" onClick={toggleRecording} className={`chatInputIconBtn ${isRecording ? "chatInputIconBtnDanger" : ""}`} style={isRecording ? { animation: "pulse 1.5s ease-in-out infinite" } : undefined}>
                      {isRecording ? <IconStopCircle size={16} /> : <IconMic size={16} />}
                      {isRecording && recordingDuration > 0 && (
                        <span style={{ fontSize: 10, marginLeft: 2, fontWeight: 600 }}>
                          {Math.floor(recordingDuration / 60)}:{String(recordingDuration % 60).padStart(2, "0")}
                        </span>
                      )}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">{isRecording ? t("chat.stopRecording") : t("chat.voice")}</TooltipContent>
                </Tooltip>

                <div ref={modeMenuRef} style={{ position: "relative", display: "inline-flex" }}>
                  <button
                    data-slot="toolbar"
                    onClick={() => setModeMenuOpen((v) => !v)}
                    className={`chatInputIconBtn ${chatMode === "plan" ? "chatInputIconBtnPlan" : chatMode === "ask" ? "chatInputIconBtnAsk" : ""}`}
                    title={chatMode === "agent" ? t("chat.modeAgentTitle") : chatMode === "plan" ? t("chat.modePlanTitle") : t("chat.modeAskTitle")}
                  >
                    {{ agent: <IconBot size={16} />, plan: <IconPlan size={16} />, ask: <IconSearch size={16} /> }[chatMode]}
                    <span style={{ fontSize: 11, marginLeft: 2 }}>
                      {chatMode === "agent" ? t("chat.modeAgent") : chatMode === "plan" ? t("chat.modePlan") : t("chat.modeAsk")}
                    </span>
                    <IconChevronDown size={10} style={{ marginLeft: 2, opacity: 0.5 }} />
                  </button>
                  {modeMenuOpen && (
                    <div className="chatModeMenu">
                      <div className="chatModeMenuSection">{t("chat.executionMode")}</div>
                      {([
                        { key: "agent" as const, icon: <IconBot size={14} />, label: t("chat.modeAgent"), desc: t("chat.modeAgentDesc") },
                        { key: "plan" as const, icon: <IconPlan size={14} />, label: t("chat.modePlan"), desc: t("chat.modePlanDesc") },
                        { key: "ask" as const, icon: <IconSearch size={14} />, label: t("chat.modeAsk"), desc: t("chat.modeAskDesc") },
                      ]).map((m) => (
                        <div
                          key={m.key}
                          className={`chatModeMenuItem ${chatMode === m.key ? (m.key === "ask" ? "chatModeMenuItemActiveAsk" : m.key === "plan" ? "chatModeMenuItemActive" : "chatModeMenuItemActiveAgent") : ""}`}
                          onClick={() => { setChatMode(m.key); setModeMenuOpen(false); }}
                        >
                          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            {m.icon}
                            <span style={{ fontWeight: 600 }}>{m.label}</span>
                          </span>
                          <span style={{ fontSize: 10, opacity: 0.5 }}>{m.desc}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* 深度思考按钮 + 思考程度按钮 */}
                <Tooltip open={thinkingModeTipOpen}>
                  <TooltipTrigger asChild>
                    <button
                      data-slot="toolbar"
                      onMouseEnter={() => setThinkingModeTipOpen(true)}
                      onMouseLeave={() => setThinkingModeTipOpen(false)}
                      onClick={() => {
                        if (thinkingMode === "auto") {
                          setThinkingMode("on");
                        } else if (thinkingMode === "on") {
                          setThinkingMode("off");
                        } else {
                          setThinkingMode("auto");
                        }
                      }}
                      className={`chatInputIconBtn ${thinkingMode === "on" ? "chatInputIconBtnActive" : thinkingMode === "off" ? "chatInputIconBtnOff" : ""}`}
                    >
                      <IconZap size={16} />
                      <span style={{ fontSize: 11, marginLeft: 2 }}>
                        {thinkingMode === "on" ? t("chat.thinkingBtnOn") : thinkingMode === "off" ? t("chat.thinkingBtnOff") : t("chat.thinkingBtnAuto")}
                      </span>
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs" onPointerDownOutside={(e) => e.preventDefault()}>
                    {thinkingMode === "on" ? t("chat.thinkingOn") : thinkingMode === "off" ? t("chat.thinkingOff") : t("chat.thinkingAuto")}
                  </TooltipContent>
                </Tooltip>
                {thinkingMode !== "off" && (
                  <Tooltip open={thinkingDepthTipOpen}>
                    <TooltipTrigger asChild>
                      <button
                        data-slot="toolbar"
                        onMouseEnter={() => setThinkingDepthTipOpen(true)}
                        onMouseLeave={() => setThinkingDepthTipOpen(false)}
                        onClick={() => {
                          setThinkingDepth((d) => d === "low" ? "medium" : d === "medium" ? "high" : "low");
                        }}
                        className="chatInputIconBtn"
                      >
                        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ flexShrink: 0 }}>
                          <rect x="1" y="9" width="3" height="4" rx="0.5" fill="currentColor" opacity={thinkingDepth === "low" || thinkingDepth === "medium" || thinkingDepth === "high" ? 1 : 0.25} />
                          <rect x="5.5" y="5.5" width="3" height="7.5" rx="0.5" fill="currentColor" opacity={thinkingDepth === "medium" || thinkingDepth === "high" ? 1 : 0.25} />
                          <rect x="10" y="2" width="3" height="11" rx="0.5" fill="currentColor" opacity={thinkingDepth === "high" ? 1 : 0.25} />
                        </svg>
                        <span style={{ fontSize: 10 }}>{{ low: t("chat.depthLow"), medium: t("chat.depthMedium"), high: t("chat.depthHigh") }[thinkingDepth]}</span>
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="text-xs" onPointerDownOutside={(e) => e.preventDefault()}>
                      {{ low: t("chat.depthTipLow"), medium: t("chat.depthTipMedium"), high: t("chat.depthTipHigh") }[thinkingDepth]}
                      <span className="block text-[10px] opacity-60 mt-0.5">{t("chat.depthClickToSwitch")}</span>
                    </TooltipContent>
                  </Tooltip>
                )}
              </div>

              <div className="chatInputToolbarRight">
                {/* Context usage ring — only show when we have real usage data */}
                {contextLimit > 0 && contextTokens > 0 && (() => {
                  const pct = Math.min(contextTokens / contextLimit, 1);
                  const pctLabel = (pct * 100).toFixed(1);
                  const fmtK = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
                  const r = 9; const sw = 2; const circ = 2 * Math.PI * r;
                  const offset = circ * (1 - pct);
                  const color = pct > 0.95 ? "#ef4444" : pct > 0.8 ? "#f59e0b" : pct > 0.5 ? "#3b82f6" : "#999";
                  return (
                    <div
                      style={{ position: "relative", display: "inline-flex", alignItems: "center", cursor: "default", marginRight: 4 }}
                      onMouseEnter={() => setContextTooltipVisible(true)}
                      onMouseLeave={() => setContextTooltipVisible(false)}
                    >
                      <svg width={22} height={22} viewBox="0 0 22 22">
                        <circle cx={11} cy={11} r={r} fill="none" stroke="var(--line)" strokeWidth={sw} />
                        <circle cx={11} cy={11} r={r} fill="none" stroke={color} strokeWidth={sw}
                          strokeDasharray={circ} strokeDashoffset={offset}
                          strokeLinecap="round" transform="rotate(-90 11 11)" style={{ transition: "stroke-dashoffset 0.4s ease" }} />
                      </svg>
                      {contextTooltipVisible && (
                        <div style={{
                          position: "absolute", bottom: "calc(100% + 6px)", right: 0,
                          background: "rgba(0,0,0,0.82)", color: "#fff", fontSize: 11, fontWeight: 500,
                          padding: "4px 8px", borderRadius: 6, whiteSpace: "nowrap", pointerEvents: "none",
                          zIndex: 100,
                        }}>
                          {pctLabel}% · {fmtK(contextTokens)} / {fmtK(contextLimit)} context used
                        </div>
                      )}
                    </div>
                  );
                })()}
                {isCurrentConvStreaming || orgCommandPending ? (
                  hasInputText && !orgCommandPending ? (
                    <button
                      data-slot="queue"
                      onClick={handleQueueMessage}
                      className="chatInputSendBtn"
                      title={t("chat.queueHint")}
                    >
                      <IconSend size={14} />
                    </button>
                  ) : (
                    <button
                      data-slot="stop"
                      onClick={orgCommandPending ? undefined : handleCancelTask}
                      className={`chatInputSendBtn ${orgCommandPending ? "" : "chatInputStopBtn"}`}
                      title={orgCommandPending ? "组织处理中..." : t("chat.stopGeneration")}
                      disabled={orgCommandPending}
                      style={orgCommandPending ? { opacity: 0.5, cursor: "wait" } : undefined}
                    >
                      {orgCommandPending ? <IconSend size={14} /> : <IconStop size={14} />}
                    </button>
                  )
                ) : (
                  <button
                    data-slot="send"
                    onClick={() => sendMessage()}
                    className="chatInputSendBtn"
                    disabled={!hasInputText && pendingAttachments.length === 0}
                    title={t("chat.send")}
                    aria-label={t("chat.send", "发送")}
                  >
                    <IconSend size={14} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Cursor-style right sidebar — conversations */}
      {sidebarOpen && (
        <>
        {typeof window !== "undefined" && window.innerWidth <= 768 && (
          <div className="sidebarOverlay" style={{ zIndex: 1000 }} onClick={() => setSidebarOpen(false)} />
        )}
        <nav className={`convSidebar${typeof window !== "undefined" && window.innerWidth <= 768 ? " convSidebarMobileOpen" : ""}`} aria-label={t("chat.conversationList", "会话列表")}>
          <div className="convSidebarHeader">
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div className="convSearchBox" style={{ flex: 1 }}>
                <IconSearch size={13} style={{ opacity: 0.4, flexShrink: 0 }} />
                <input
                  data-slot="search"
                  className="convSearchInput"
                  placeholder={t("chat.searchConversations") || "搜索会话..."}
                  value={convSearchQuery}
                  onChange={(e) => setConvSearchQuery(e.target.value)}
                />
                {convSearchQuery && (
                  <button data-slot="clear" className="convSearchClear" onClick={() => setConvSearchQuery("")}>
                    <IconX size={11} />
                  </button>
                )}
              </div>
              <button
                data-slot="pin"
                className="convPinBtn"
                onClick={() => {
                  const next = !sidebarPinned;
                  setSidebarPinned(next);
                  try { localStorage.setItem("openakita_convSidebarPinned", String(next)); } catch {}
                }}
                title={sidebarPinned ? (t("chat.unpinSidebar") || "取消固定") : (t("chat.pinSidebar") || "固定会话列表")}
                style={{ color: sidebarPinned ? "var(--brand, #2563eb)" : "var(--muted2, #999)" }}
              >
                <IconPin size={14} />
              </button>
            </div>
            <button data-slot="new-chat" className="convNewBtn" onClick={newConversation}>
              {t("chat.newConversation")}
            </button>
          </div>

          <div className="convSidebarList">
            {pinnedConvs.length > 0 && (
              <>
                <div className="convSectionLabel">{t("chat.pinnedSection")}</div>
                {pinnedConvs.map(renderConvItem)}
              </>
            )}

            {agentConvs.length > 0 && (
              <>
                <div className="convSectionLabel">{t("chat.conversationsLabel") || "会话"}</div>
                {agentConvs.map(renderConvItem)}
              </>
            )}

            {filteredConversations.length === 0 && (
              <div className="convEmpty">
                {convSearchQuery ? t("common.noResults") || "无结果" : t("common.noData")}
              </div>
            )}
          </div>
        </nav>
        </>
      )}

      {/* Orbit tooltip — portal to body to escape overflow:hidden */}
      {orbitTip && createPortal(
        <div className="agentOrbitTooltip agentOrbitTooltipVisible" style={{ left: orbitTip.x, top: orbitTip.y }}>
          <span className="agentOrbitTooltipName">{orbitTip.name}</span>
          <span className="agentOrbitTooltipTitle">{orbitTip.title}</span>
        </div>,
        document.body,
      )}

      {/* Enhanced image lightbox — zoom/drag/keyboard (2.7) */}
      {lightbox && <LightboxOverlay
        lightbox={lightbox}
        onClose={closeLightbox}
        downloadFile={downloadFile}
        showInFolder={showInFolder}
        t={(k, d) => t(k, d ?? "")}
      />}
      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />

      {/* Keyboard shortcuts panel */}
      {shortcutsOpen && createPortal(
        <div style={{ position: "fixed", inset: 0, zIndex: 10000, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.4)" }} onClick={() => setShortcutsOpen(false)}>
          <div style={{ background: "var(--panel)", borderRadius: 16, padding: "24px 28px", minWidth: 340, maxWidth: 420, boxShadow: "0 24px 64px rgba(0,0,0,0.3)", border: "1px solid var(--line)" }} onClick={(e) => e.stopPropagation()}>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 16 }}>{t("chat.shortcuts", "键盘快捷键")}</div>
            {[
              ["Enter", t("chat.shortcutSend", "发送消息")],
              ["Shift + Enter", t("chat.shortcutNewline", "换行")],
              ["Esc", t("chat.shortcutStop", "停止生成 / 取消")],
              ["Ctrl + /", t("chat.shortcutPanel", "打开此面板")],
              ["/", t("chat.shortcutSlash", "打开斜杠命令菜单")],
              ["↑ / ↓", t("chat.shortcutNav", "命令菜单导航")],
            ].map(([key, desc]) => (
              <div key={key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                <span style={{ fontSize: 13, opacity: 0.7 }}>{desc}</span>
                <kbd style={{ fontSize: 12, padding: "2px 8px", borderRadius: 4, background: "var(--panel2)", border: "1px solid var(--line)", fontFamily: "monospace" }}>{key}</kbd>
              </div>
            ))}
            <div style={{ marginTop: 14, textAlign: "right" }}>
              <button onClick={() => setShortcutsOpen(false)} style={{ fontSize: 13, padding: "5px 14px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--brand)", color: "#fff", cursor: "pointer" }}>
                {t("common.close", "关闭")}
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {securityConfirm && createPortal(
        <SecurityConfirmModal
          data={securityConfirm}
          apiBase={apiBaseUrl}
          onClose={handleSecurityClose}
          timerRef={securityTimerRef}
          setData={setSecurityConfirm}
        />,
        document.body,
      )}
    </div>
  );
}
