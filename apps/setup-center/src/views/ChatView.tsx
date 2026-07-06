// ─── ChatView: 完整 AI 聊天页面 ───
// 组装层: 通过 hooks + 子组件构建完整聊天界面

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { setLanguage } from "../i18n";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ProviderIcon } from "../components/ProviderIcon";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { toast } from "sonner";
import { setThemePref } from "../theme";
import type { Theme } from "../theme";
import { downloadFile, showInFolder, readFileBase64, onDragDrop, IS_TAURI, IS_WEB, IS_MOBILE_BROWSER, onWsEvent, logger } from "../platform";
import { safeFetch } from "../providers";
import type {
  ChatMessage,
  ChatErrorInfo,
  ChatConversation,
  ConversationStatus,
  ChatToolCall,
  ChatTodo,
  ChatProgressEvent,
  ChatTodoStep,
  ChatAskUser,
  ChatAttachment,
  ChatArtifact,
  ChatSource,
  ChatMcpCall,
  SlashCommand,
  EndpointSummary,
  ChainGroup,
  ChainToolCall,
  ChainEntry,
  ChainSummaryItem,
  ChainTimelineGroup,
  ChatDisplayMode,
  PlanApprovalEvent,
  OrgTimelineEntry,
  MessagePart,
  EnvMap,
} from "../types";
import { genId, timeAgo } from "../utils";
import { SseStateMachine, type SseFrame } from "../utils/sseStateMachine";
import { notifyError, notifyInfo } from "../utils/notify";
import { ErrorBoundary } from "../components/ErrorBoundary";
import {
  IconSend, IconPaperclip, IconMic, IconStopCircle,
  IconPlan, IconPlus, IconMenu, IconStop, IconX,
  IconCheck, IconLoader, IconCircle,
  IconChevronDown, IconChevronUp, IconMessageCircle, IconChevronRight,
  IconClipboard, IconTrash, IconZap,
  IconBot, IconEdit, IconDownload,
  IconPin, IconSearch, IconCircleDot, IconXCircle,
  IconBuilding, IconAlertCircle,
  IconHourglass, IconTarget, IconCheckCircle, IconPlug, IconClock, IconBarChart, IconGlobe, IconMail,
} from "../icons";

// ─── Chat module imports ───
import type {
  QueuedMessage, StreamEvent,
  SubAgentEntry, SubAgentTask, StreamContext, AgentProfile,
} from "./chat/utils/chatTypes";
import {
  IDLE_THRESHOLD_MS, IDLE_TOKEN_THRESHOLD, PASTE_CHAR_THRESHOLD, UNDO_MAX_STEPS,
  exportConversation,
  loadMessagesFromStorage, saveMessagesToStorage, STORED_MESSAGE_WINDOW,
  buildChainFromSummary, buildChainFromTimeline, formatAskUserAnswer, patchMessagesWithBackend, patchMessagesWithBackendDetailed,
  chooseHydratedMessages, classifyError, formatToolDescription,
  shouldRenderConversationMessages,
} from "./chat/utils/chatHelpers";
import { useMdModules } from "./chat/hooks/useMdModules";
import { useMessageReducer, useConversationReducer } from "./chat/hooks/useMessages";
import { useQueryGuard } from "./chat/hooks/useQueryGuard";
import { useSecurityPolicy } from "./chat/hooks/useSecurityPolicy";
import {
  AttachmentPreview,
  FloatingPlanBar, PlanApprovalPanel,
  SlashCommandPanel, RenderIcon, SubAgentCards,
  SecurityConfirmModal, ContextMenuInner, LightboxOverlay,
  MessageList,
} from "./chat/components";
import type {
  SecurityCloseInfo,
  SecurityConfirmDisplay,
  SecurityConfirmModalData,
  SecurityDecision,
  SecurityDecisionChainStep,
  SecurityDisplayToken,
  SecurityTimeoutDefault,
} from "./chat/components";
import type { MessageListHandle } from "./chat/components";

type SecurityPresentationState = "active" | "queued" | "resolved";
type SecurityConfirmData = SecurityConfirmModalData & {
  presentationState: SecurityPresentationState;
  queuedCount: number;
};

function _asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function _asFiniteCount(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

const SECURITY_DECISION_VALUES: readonly SecurityDecision[] = [
  "allow_once",
  "allow_session",
  "allow_always",
  "deny",
  "sandbox",
];
const SECURITY_DECISION_SET = new Set<string>(SECURITY_DECISION_VALUES);
const SECURITY_TIMEOUT_DEFAULT_VALUES: readonly SecurityTimeoutDefault[] = ["allow_once", "deny"];
const SECURITY_TIMEOUT_DEFAULT_SET = new Set<string>(SECURITY_TIMEOUT_DEFAULT_VALUES);

function _isSecurityRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function _stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function _nonEmptyStringOrNull(value: unknown): string | null {
  const s = _stringOrNull(value);
  return s && s.length > 0 ? s : null;
}

function _optionalString(
  record: Record<string, unknown>,
  key: string,
): string | undefined | null {
  if (!(key in record) || record[key] === undefined) return undefined;
  return typeof record[key] === "string" ? record[key] as string : null;
}

function _invalidSecurityConfirm(
  raw: Record<string, unknown>,
  reason: string,
): null {
  logger.warn("Chat.SecurityConfirm", "Ignoring security_confirm with invalid backend display metadata", {
    reason,
    keys: Object.keys(raw),
  });
  return null;
}

function _parseDisplayToken(value: unknown): SecurityDisplayToken | null {
  if (!_isSecurityRecord(value)) return null;
  const tokenValue = _stringOrNull(value.value);
  const label = _stringOrNull(value.label);
  if (tokenValue === null || label === null) return null;
  const color = _optionalString(value, "color");
  if (color === null) return null;
  const description = _optionalString(value, "description");
  if (description === null) return null;
  return {
    value: tokenValue,
    label,
    ...(color !== undefined ? { color } : {}),
    ...(description !== undefined ? { description } : {}),
  };
}

function _parseDisplayTokenWithColor(value: unknown): (SecurityDisplayToken & { color: string }) | null {
  const token = _parseDisplayToken(value);
  if (!token || typeof token.color !== "string") return null;
  return token as SecurityDisplayToken & { color: string };
}

function _parseSecurityConfirmDisplay(value: unknown): SecurityConfirmDisplay | null {
  if (!_isSecurityRecord(value)) return null;
  const title = _stringOrNull(value.title);
  const reasonRaw = _isSecurityRecord(value.reason) ? value.reason : null;
  const reasonText = reasonRaw ? _stringOrNull(reasonRaw.text) : null;
  const reasonOriginal = reasonRaw ? _optionalString(reasonRaw, "raw") : null;
  const risk = _parseDisplayTokenWithColor(value.risk);
  const tool = _parseDisplayToken(value.tool);
  const argsRaw = _isSecurityRecord(value.arguments) ? value.arguments : null;
  const argsText = argsRaw ? _stringOrNull(argsRaw.text) : null;
  const argsFormat = argsRaw ? _optionalString(argsRaw, "format") : null;
  if (
    title === null
    || reasonRaw === null
    || reasonText === null
    || reasonOriginal === null
    || risk === null
    || tool === null
    || argsRaw === null
    || argsText === null
    || argsFormat === null
  ) {
    return null;
  }

  const channel = value.channel === undefined ? undefined : _parseDisplayToken(value.channel);
  if (channel === null) return null;
  const approvalClass = value.approval_class === undefined
    ? undefined
    : _parseDisplayToken(value.approval_class);
  if (approvalClass === null) return null;

  return {
    title,
    reason: {
      text: reasonText,
      ...(reasonOriginal !== undefined ? { raw: reasonOriginal } : {}),
    },
    risk,
    tool,
    ...(channel !== undefined ? { channel } : {}),
    ...(approvalClass !== undefined ? { approval_class: approvalClass } : {}),
    arguments: {
      text: argsText,
      ...(argsFormat !== undefined ? { format: argsFormat } : {}),
    },
  };
}

function _parseSecurityDecisionOptions(value: unknown): SecurityDecision[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const options: SecurityDecision[] = [];
  for (const item of value) {
    if (typeof item !== "string" || !SECURITY_DECISION_SET.has(item)) return null;
    options.push(item as SecurityDecision);
  }
  return options;
}

function _parseSecurityTimeoutDefault(value: unknown): SecurityTimeoutDefault | null {
  if (typeof value !== "string" || !SECURITY_TIMEOUT_DEFAULT_SET.has(value)) return null;
  return value as SecurityTimeoutDefault;
}

function _parseSecurityDecisionChain(value: unknown): SecurityDecisionChainStep[] | null {
  if (!Array.isArray(value)) return null;
  const steps: SecurityDecisionChainStep[] = [];
  for (const item of value) {
    if (!_isSecurityRecord(item)) return null;
    const name = _stringOrNull(item.name);
    const action = _stringOrNull(item.action);
    const note = _stringOrNull(item.note);
    const metadata = item.metadata === undefined ? undefined : (
      _isSecurityRecord(item.metadata) ? item.metadata : null
    );
    const displayRaw = _isSecurityRecord(item.display) ? item.display : null;
    const label = displayRaw ? _stringOrNull(displayRaw.label) : null;
    const displayAction = displayRaw ? _parseDisplayTokenWithColor(displayRaw.action) : null;
    const displayNote = displayRaw ? _optionalString(displayRaw, "note") : null;
    if (
      name === null
      || action === null
      || note === null
      || metadata === null
      || displayRaw === null
      || label === null
      || displayAction === null
      || displayNote === null
    ) {
      return null;
    }
    steps.push({
      name,
      action,
      note,
      ...(metadata !== undefined ? { metadata } : {}),
      display: {
        label,
        action: displayAction,
        ...(displayNote !== undefined ? { note: displayNote } : {}),
      },
    });
  }
  return steps;
}

function _parseSecuritySource(value: unknown): "risk_gate" | "policy_v2" | null {
  return value === "risk_gate" || value === "policy_v2" ? value : null;
}

function _parseSecurityPresentationState(value: unknown): SecurityPresentationState | null {
  return value === "active" || value === "queued" || value === "resolved" ? value : null;
}

function _securityConfirmFromBackend(raw: Record<string, unknown>): SecurityConfirmData | null {
  const args = _isSecurityRecord(raw.args) ? raw.args : null;
  const source = _parseSecuritySource(raw.source);
  const tool = _nonEmptyStringOrNull(raw.tool);
  const reason = _stringOrNull(raw.reason);
  const riskLevel = _nonEmptyStringOrNull(raw.risk_level);
  const needsSandbox = typeof raw.needs_sandbox === "boolean" ? raw.needs_sandbox : null;
  const toolId = _nonEmptyStringOrNull(raw.confirm_id);
  const countdown = Number(raw.timeout_seconds);
  const defaultOnTimeout = _parseSecurityTimeoutDefault(raw.default_on_timeout);
  const conversationId = _nonEmptyStringOrNull(raw.conversation_id);
  const presentationState = _parseSecurityPresentationState(raw.presentation_state);
  const queuedCount = Number(raw.queued_count);
  const display = _parseSecurityConfirmDisplay(raw.display);
  const decisionChain = _parseSecurityDecisionChain(raw.decision_chain);
  const options = _parseSecurityDecisionOptions(raw.options);
  const riskIntent = raw.risk_intent === undefined ? undefined : (
    _isSecurityRecord(raw.risk_intent) ? raw.risk_intent : null
  );
  const originalMessage = raw.original_message === undefined
    ? undefined
    : _stringOrNull(raw.original_message);

  if (args === null) return _invalidSecurityConfirm(raw, "args must be an object");
  if (source === null) return _invalidSecurityConfirm(raw, "source must be risk_gate or policy_v2");
  if (tool === null) return _invalidSecurityConfirm(raw, "tool is required");
  if (reason === null) return _invalidSecurityConfirm(raw, "reason must be a string");
  if (riskLevel === null) return _invalidSecurityConfirm(raw, "risk_level is required");
  if (needsSandbox === null) return _invalidSecurityConfirm(raw, "needs_sandbox must be boolean");
  if (toolId === null) return _invalidSecurityConfirm(raw, "confirm_id is required");
  if (!Number.isFinite(countdown)) {
    return _invalidSecurityConfirm(raw, "timeout_seconds must be finite");
  }
  if (defaultOnTimeout === null) {
    return _invalidSecurityConfirm(raw, "default_on_timeout must be allow_once or deny");
  }
  if (conversationId === null) {
    return _invalidSecurityConfirm(raw, "conversation_id is required");
  }
  if (presentationState === null) {
    return _invalidSecurityConfirm(raw, "presentation_state is required");
  }
  if (!Number.isFinite(queuedCount) || queuedCount < 0) {
    return _invalidSecurityConfirm(raw, "queued_count must be a non-negative number");
  }
  if (display === null) return _invalidSecurityConfirm(raw, "display is required");
  if (decisionChain === null) {
    return _invalidSecurityConfirm(raw, "decision_chain with step display metadata is required");
  }
  if (options === null) return _invalidSecurityConfirm(raw, "options are required");
  if (riskIntent === null) return _invalidSecurityConfirm(raw, "risk_intent must be an object");
  if (originalMessage === null) {
    return _invalidSecurityConfirm(raw, "original_message must be a string when present");
  }

  return {
    tool,
    args,
    reason,
    riskLevel,
    needsSandbox,
    toolId,
    countdown,
    defaultOnTimeout,
    source,
    conversationId,
    originalMessage,
    riskIntent,
    presentationState,
    queuedCount: Math.floor(queuedCount),
    decisionChain,
    options,
    display,
  };
}

function _isActiveSecurityConfirm(confirm: SecurityConfirmData | null): confirm is SecurityConfirmData {
  return confirm?.presentationState === "active";
}

const HISTORY_PAGE_LIMIT = 80;
type EndpointPolicy = "prefer" | "require";
type AskUserReplyBody = {
  kind: "normal";
  message_id: string;
  answer: string;
};

type StreamTransport =
  | {
      kind: "resume";
      url: string;
    };

type SendMessageOptions = {
  appendUserMessage?: boolean;
  countAssistantMessage?: boolean;
  initialStreamStatus?: string;
  reuseAssistantMessageId?: string;
  streamTransport?: StreamTransport;
};

type HistoryPageState = {
  total: number;
  startIndex: number | null;
  hasMoreBefore: boolean;
  loadingOlder: boolean;
};

// ─── 主组件 ───

export function ChatView({
  serviceRunning,
  endpoints,
  onStartService,
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
  multiAgentEnabled = false,
  currentWorkspaceId,
  feedbackModalOpen = false,
  envDraft = {},
  setEnvDraft,
  saveEnvKeys,
}: {
  serviceRunning: boolean;
  endpoints: EndpointSummary[];
  onStartService: () => void;
  apiBaseUrl?: string;
  visible?: boolean;
  multiAgentEnabled?: boolean;
  currentWorkspaceId?: string | null;
  feedbackModalOpen?: boolean;
  envDraft?: EnvMap;
  setEnvDraft?: (updater: (prev: EnvMap) => EnvMap) => void;
  saveEnvKeys?: (keys: string[]) => Promise<{ restartRequired?: boolean; hotReloadable?: boolean } | unknown>;
}) {
  // multiAgentEnabled is currently observed by App but not consumed inside ChatView
  // (single-agent only); accept the prop for forward compat to avoid runtime warnings.
  void multiAgentEnabled;

  // Track feedbackModalOpen via ref so the Tauri drag-drop effect (deps=[]) can
  // read the latest value without re-registering the webview listener.
  const feedbackModalOpenRef = useRef(false);
  useEffect(() => { feedbackModalOpenRef.current = feedbackModalOpen; }, [feedbackModalOpen]);

  const { t, i18n } = useTranslation();
  const mdModules = useMdModules();

  // ── Workspace-scoped localStorage keys ──
  // wsTag is the active workspace identifier used to namespace chat persistence.
  // When currentWorkspaceId is null (initial mount before App.tsx hydrates the
  // workspace), we use "_default"; the workspace-change effect below detects the
  // "_default" → real transition and migrates legacy global keys exactly once.
  const wsTag = currentWorkspaceId || "_default";
  const STORAGE_KEY_CONVS = `chat_conversations_${wsTag}`;
  const STORAGE_KEY_ACTIVE = `chat_activeConvId_${wsTag}`;
  const STORAGE_KEY_MSGS_PREFIX = `chat_msgs_${wsTag}_`;
  // data_epoch is PER-WORKSPACE (each workspace owns its own
  // data/web_access.json and thus its own randomly generated epoch), so its
  // factory-reset cache MUST also be workspace-scoped — keep it here alongside
  // the other scoped keys so it can't silently drift back to a global key (#635).
  const STORAGE_KEY_DATA_EPOCH = `openakita_data_epoch_${wsTag}`;

  // Old (pre-isolation) global keys — used only for the one-time migration
  // performed in the workspace-change effect.
  const OLD_KEY_CONVS = "chat_conversations";
  const OLD_KEY_ACTIVE = "chat_activeConvId";
  const OLD_KEY_MSGS_PREFIX = "chat_msgs_";

  // ── State（useReducer 集中管理，从 localStorage 恢复） ──
  const { messages, dispatch: msgDispatch, messagesRef: latestMessagesRef } = useMessageReducer(currentWorkspaceId);
  const { conversations, dispatch: convDispatch, conversationsRef: latestConversationsRef } = useConversationReducer(currentWorkspaceId);
  const displayedMessagesConvIdRef = useRef<string | null>(null);
  const queryGuard = useQueryGuard();
  const securityPolicy = useSecurityPolicy(apiBaseUrl);

  // 向后兼容别名：逐步迁移后可移除
  const setMessages = useCallback((arg: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => {
    const next = typeof arg === "function" ? arg(latestMessagesRef.current) : arg;
    latestMessagesRef.current = next;
    msgDispatch({ type: "SET_ALL", messages: next });
  }, [msgDispatch, latestMessagesRef]);

  const setConversations = useCallback((arg: ChatConversation[] | ((prev: ChatConversation[]) => ChatConversation[])) => {
    const next = typeof arg === "function" ? arg(latestConversationsRef.current) : arg;
    latestConversationsRef.current = next;
    convDispatch({ type: "SET_ALL", conversations: next });
  }, [convDispatch, latestConversationsRef]);

  const [activeConvId, setActiveConvId] = useState<string | null>(() => {
    try {
      // On first mount with no workspaceId yet (wsTag === "_default"), read the
      // legacy global key — matches what useMessageReducer/useConversationReducer
      // do via getWorkspaceStorageKeys(null). The workspace-change effect below
      // performs the one-time _default → real migration when the workspace ID
      // arrives, so we deliberately avoid writing to the "_default"-suffixed key
      // before that migration runs.
      const initialKey = currentWorkspaceId ? STORAGE_KEY_ACTIVE : OLD_KEY_ACTIVE;
      return localStorage.getItem(initialKey) || null;
    } catch { return null; }
  });
  const [hydrating, setHydrating] = useState(false);
  const [historyPage, setHistoryPage] = useState<HistoryPageState>({
    total: 0,
    startIndex: null,
    hasMoreBefore: false,
    loadingOlder: false,
  });

  // ── Workspace switch: reload chat state from new scoped keys ──
  // Also performs one-time migration of legacy global keys into the first real
  // workspace ID encountered (default _default → real transition).
  const prevWsRef = useRef(wsTag);
  useEffect(() => {
    if (wsTag === prevWsRef.current) return;
    const migrateFromGlobal = prevWsRef.current === "_default" && wsTag !== "_default";
    prevWsRef.current = wsTag;

    // ── Conversations ──
    let convs: ChatConversation[] = [];
    try {
      const raw = localStorage.getItem(STORAGE_KEY_CONVS);
      if (raw) {
        convs = JSON.parse(raw);
      } else if (migrateFromGlobal) {
        const oldRaw = localStorage.getItem(OLD_KEY_CONVS);
        if (oldRaw) {
          convs = JSON.parse(oldRaw);
          localStorage.setItem(STORAGE_KEY_CONVS, oldRaw);
          localStorage.removeItem(OLD_KEY_CONVS);
        }
      }
    } catch { convs = []; }
    latestConversationsRef.current = convs;
    convDispatch({ type: "SET_ALL", conversations: convs });

    // ── activeConvId ──
    let convId: string | null = null;
    try {
      convId = localStorage.getItem(STORAGE_KEY_ACTIVE) || null;
      if (!convId && migrateFromGlobal) {
        convId = localStorage.getItem(OLD_KEY_ACTIVE) || null;
        if (convId) {
          localStorage.setItem(STORAGE_KEY_ACTIVE, convId);
          localStorage.removeItem(OLD_KEY_ACTIVE);
        }
      }
    } catch { convId = null; }
    setActiveConvId(convId);

    // ── Messages for active conversation ──
    let msgs: ChatMessage[] = [];
    if (convId) {
      try {
        const rawMsgs = localStorage.getItem(STORAGE_KEY_MSGS_PREFIX + convId);
        if (rawMsgs) {
          msgs = JSON.parse(rawMsgs);
        } else if (migrateFromGlobal) {
          const oldRaw = localStorage.getItem(OLD_KEY_MSGS_PREFIX + convId);
          if (oldRaw) {
            msgs = JSON.parse(oldRaw);
            localStorage.setItem(STORAGE_KEY_MSGS_PREFIX + convId, oldRaw);
            localStorage.removeItem(OLD_KEY_MSGS_PREFIX + convId);
          }
        }
      } catch { msgs = []; }
    }
    displayedMessagesConvIdRef.current = convId;
    latestMessagesRef.current = msgs;
    msgDispatch({ type: "SET_ALL", messages: msgs });

    // ── Migrate remaining message entries for non-active conversations ──
    if (migrateFromGlobal && convs.length > 0) {
      for (const c of convs) {
        if (c.id === convId) continue;
        try {
          const oldMsgKey = OLD_KEY_MSGS_PREFIX + c.id;
          const oldMsgRaw = localStorage.getItem(oldMsgKey);
          if (oldMsgRaw && !localStorage.getItem(STORAGE_KEY_MSGS_PREFIX + c.id)) {
            localStorage.setItem(STORAGE_KEY_MSGS_PREFIX + c.id, oldMsgRaw);
            localStorage.removeItem(oldMsgKey);
          }
        } catch { /* best effort */ }
      }
    }

    // ── Clean up orphaned "_default" keys from the brief null→real transition ──
    if (migrateFromGlobal) {
      try {
        localStorage.removeItem("chat_conversations__default");
        localStorage.removeItem("chat_activeConvId__default");
      } catch { /* ignore */ }
    }

    setSelectedEndpoint("auto");
    setSelectedEndpointPolicy("prefer");
  // eslint-disable-next-line react-hooks/exhaustive-deps -- STORAGE_KEY_*/OLD_KEY_* are
  // derived from wsTag (or are constants); listing wsTag alone is sufficient and
  // avoids re-running the migration on every render.
  }, [wsTag]);
  const inputTextRef = useRef("");
  const [hasInputText, setHasInputText] = useState(false);
  const [selectedEndpoint, setSelectedEndpoint] = useState("auto");
  const [selectedEndpointPolicy, setSelectedEndpointPolicy] = useState<EndpointPolicy>("prefer");
  const [chatMode, setChatMode] = useState<"agent" | "plan" | "ask">("agent");
  const [pendingApproval, setPendingApproval] = useState<PlanApprovalEvent | null>(null);
  const pendingApprovalRef = useRef<PlanApprovalEvent | null>(null);
  const [deathSwitchActive, setDeathSwitchActive] = useState(false);
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
  // 会话大纲（Conversation outline）：右侧常驻迷你导航，悬浮展开，列出所有用户提问并支持点击跳转
  const [activeOutlineId, setActiveOutlineId] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<ChatAttachment[]>([]);
  const [lightbox, setLightbox] = useState<{ url: string; downloadUrl: string; name: string } | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  const [securityConfirm, setSecurityConfirm] = useState<SecurityConfirmData | null>(null);
  // Backend-owned queued count from UIConfirmBus presentation state. The
  // frontend does not keep its own queue or decide RiskGate priority.
  const [securityQueueLen, setSecurityQueueLen] = useState(0);
  const securityExecutionStarterRef = useRef<(info: SecurityCloseInfo) => boolean>(() => false);
  // C18 Phase B: POLICIES.yaml ``confirmation.aggregation_window_seconds``.
  // 0 = batch UI hidden; >0 = show "Approve all (N+1)" affordance and
  // pass as ``within_seconds`` to POST /api/chat/security-confirm/batch
  // (server clamps to its own config). Loaded once on mount.
  const [securityAggWindow, setSecurityAggWindow] = useState<number>(0);
  const appendBackendSystemMessage = useCallback((convId: string, content: string) => {
    if (!convId || !content) return;
    const systemMsg: ChatMessage = {
      id: genId(),
      role: "system",
      content,
      timestamp: Date.now(),
    };
    const baseMessages = convId === activeConvIdRef.current
      ? latestMessagesRef.current
      : loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + convId);
    const nextMessages = [...baseMessages, systemMsg];
    try { saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, nextMessages); } catch { /* quota */ }
    if (shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
      displayedMessagesConvIdRef.current = convId;
      setMessages(nextMessages);
    }
  }, [STORAGE_KEY_MSGS_PREFIX, setMessages]);

  const applySecurityResolution = useCallback((info?: SecurityCloseInfo) => {
    if (info && (info.decision === "deny" || info.decision === "timeout")) {
      securityPolicy.recordDeny(info.tool);
    }

    const nextRaw = info?.nextConfirm ? _asRecord(info.nextConfirm) : {};
    const next = Object.keys(nextRaw).length > 0
      ? _securityConfirmFromBackend(nextRaw)
      : null;
    setSecurityConfirm(_isActiveSecurityConfirm(next) ? next : null);
    setSecurityQueueLen(next ? next.queuedCount : _asFiniteCount(info?.queuedCount));

    if (!info) return;
    const convId = info.conversationId || activeConvIdRef.current || "";
    const started = securityExecutionStarterRef.current(info);
    if (!started && info.uiMessage) {
      appendBackendSystemMessage(convId, info.uiMessage);
    }
  }, [appendBackendSystemMessage, securityPolicy]);

  const handleSecurityClose = useCallback((info?: SecurityCloseInfo) => {
    applySecurityResolution(info);
  }, [applySecurityResolution]);

  // C18 Phase B：批量 resolve 当前 session 内 confirm。banner 点击进入。
  const handleSecurityBatchResolve = useCallback(
    async (decision: "allow_once" | "deny") => {
      const convId = activeConvIdRef.current;
      if (!convId) return;
      try {
        const r = await safeFetch(`${apiBaseUrl}/api/chat/security-confirm/batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: convId,
            decision,
            within_seconds: securityAggWindow > 0 ? securityAggWindow : undefined,
          }),
        });
        // C18 二轮自审 (IMPROVEMENT-B1)：必须检查 HTTP status 才能
        // 决定是否清本地 queue。500/4xx 时静默清掉用户会以为搞定，
        // 实际却什么都没 resolve——后续 SSE waiter 也没醒，IM 卡片
        // 仍然挂着。出错就让用户单条点。
        if (!r.ok) {
          return;
        }
        const body = await r.json().catch(() => null) as { status?: string } | null;
        if (body && body.status === "error") {
          return;
        }
      } catch {
        // Network error: leave queue alone so user can retry one-by-one.
        return;
      }
      setSecurityQueueLen(0);
      setSecurityConfirm(null);
    },
    [apiBaseUrl, securityAggWindow],
  );
  const [, setWinSize] = useState({ w: window.innerWidth, h: window.innerHeight });
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
  const isOrgConvSwitchRef = useRef(false);
  const [orgCommandPending, setOrgCommandPending] = useState(false);
  const orgCommandPendingRef = useRef(false);
  const activeOrgCommandRef = useRef<{ orgId: string; commandId: string } | null>(null);

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

  // P5.1: agent_id → parent_agent_id map, populated client-side from
  // agent_handoff / delegate_to_agent / delegate_parallel events.  Used by
  // SubAgentCards to render delegation chains as a tree.  We keep it in a
  // ref (not state) because SubAgentTask.parent_agent_id snapshots the value
  // at the time we apply the next sub_agent_state / agents:sub_state patch.
  const parentAgentMapRef = useRef<Map<string, string>>(new Map());

  // Enrich sub-agent tasks with parent_agent_id inferred from the map.
  // Used at every place that hydrates ctx.subAgentTasks (WS patch, REST
  // polling fallback, refresh handlers) so the tree view stays consistent.
  const enrichTasksWithParents = useCallback((tasks: SubAgentTask[]): SubAgentTask[] => {
    if (!tasks?.length) return tasks;
    let mutated = false;
    const next = tasks.map((t) => {
      if (t.parent_agent_id) return t;
      const p = parentAgentMapRef.current.get(t.agent_id);
      if (!p) return t;
      mutated = true;
      return { ...t, parent_agent_id: p };
    });
    return mutated ? next : tasks;
  }, []);

  // ── Per-session streaming context (supports concurrent streams) ──
  const streamContexts = useRef<Map<string, StreamContext>>(new Map());
  const activeConvIdRef = useRef(activeConvId);
  const latestActiveConvIdRef = useRef<string | null>(activeConvId);
  const isCurrentConvStreaming = streamContexts.current.get(activeConvId ?? "")?.isStreaming ?? false;
  const renderConversationMessages = useCallback((convId: string, nextMessages: ChatMessage[]) => {
    if (!shouldRenderConversationMessages(convId, activeConvIdRef.current)) return false;
    displayedMessagesConvIdRef.current = convId;
    setMessages(nextMessages);
    return true;
  }, [setMessages]);

  // C17 Phase B.3: SSE seq tracking per conversation.
  //   - lastSeqByConv: max seq we've already processed. Used as ``since_seq``
  //     when re-attaching to a still-running turn via GET /api/chat/resume.
  //     It is deliberately NOT sent as a Last-Event-ID header on the new-turn
  //     POST — that replays the *previous* turn's buffered tail across the turn
  //     boundary (see the POST below for the full rationale).
  //   - seenSeqsByConv: ringbuffer of recently-seen seqs to drop
  //     duplicates that may arrive during replay→live overlap (resume).
  // Both are refs (no re-render needed); only the streaming loop reads them.
  const lastSeqByConv = useRef<Map<string, number>>(new Map());
  const seenSeqsByConv = useRef<Map<string, Set<number>>>(new Map());
  const SEEN_SEQ_CAP = 256;  // cap memory per conv

  const rememberSeq = useCallback((convId: string, seq: number) => {
    if (!convId || !Number.isFinite(seq) || seq <= 0) return;
    const prev = lastSeqByConv.current.get(convId) ?? 0;
    if (seq > prev) lastSeqByConv.current.set(convId, seq);
    let seen = seenSeqsByConv.current.get(convId);
    if (!seen) {
      seen = new Set();
      seenSeqsByConv.current.set(convId, seen);
    }
    seen.add(seq);
    // Cap the dedup set; drop oldest by iteration order (insertion order).
    if (seen.size > SEEN_SEQ_CAP) {
      const it = seen.values();
      const drop = seen.size - SEEN_SEQ_CAP;
      for (let i = 0; i < drop; i++) {
        const v = it.next().value;
        if (typeof v === "number") seen.delete(v);
      }
    }
  }, []);

  const hasSeenSeq = useCallback((convId: string, seq: number): boolean => {
    if (!convId || !Number.isFinite(seq) || seq <= 0) return false;
    const seen = seenSeqsByConv.current.get(convId);
    return seen ? seen.has(seq) : false;
  }, []);

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

  const activateConversation = useCallback((convId: string | null) => {
    activeConvIdRef.current = convId;
    latestActiveConvIdRef.current = convId;
    if (displayedMessagesConvIdRef.current !== convId) {
      displayedMessagesConvIdRef.current = null;
    }
    setActiveConvId(convId);
  }, []);

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
  const [thinkingDepth, setThinkingDepth] = useState<"low" | "medium" | "high" | "max">(() => {
    try {
      const v = localStorage.getItem("chat_thinkingDepth");
      if (v === "xhigh") return "max";
      return (v === "low" || v === "medium" || v === "high" || v === "max") ? v : "medium";
    }
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
  const [contextEditOpen, setContextEditOpen] = useState(false);
  const [editingContextLimit, setEditingContextLimit] = useState("");
  const [contextSaving, setContextSaving] = useState(false);
  const contextStatsReqSeqRef = useRef(0);

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
  // STORAGE_KEY_* intentionally excluded from deps: when only the key changes
  // (workspace switch), the workspace-change effect handles loading new data; if
  // we included the key here, old workspace data would be written to the new key
  // before the workspace-change effect has a chance to run.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY_CONVS, JSON.stringify(conversations));
    } catch { /* quota exceeded or private mode */ }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations]);

  useEffect(() => {
    activeConvIdRef.current = activeConvId;
    try {
      if (activeConvId) localStorage.setItem(STORAGE_KEY_ACTIVE, activeConvId);
      else localStorage.removeItem(STORAGE_KEY_ACTIVE);
    } catch {}
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConvId]);

  // Force re-render every 30s to refresh relative timestamps
  const [, setTimeTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTimeTick((t) => t + 1), 30_000);
    return () => clearInterval(iv);
  }, []);

  // ── 持久化消息（流式中由 StreamContext 管理，finally 一次性写入） ──
  const saveMessagesTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { latestActiveConvIdRef.current = activeConvId; }, [activeConvId]);

  const flushCurrentConversationToStorage = useCallback(() => {
    const convId = latestActiveConvIdRef.current;
    if (!convId) return;
    const ctx = streamContexts.current.get(convId);
    if (ctx?.messages?.length) {
      saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, ctx.messages);
      return;
    }
    if (displayedMessagesConvIdRef.current !== convId) return;
    saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, latestMessagesRef.current);
  }, [STORAGE_KEY_MSGS_PREFIX]);

  useEffect(() => {
    if (!activeConvId) return;
    if (streamContexts.current.get(activeConvId)?.isStreaming) return;
    if (displayedMessagesConvIdRef.current !== activeConvId) return;
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
  // eslint-disable-next-line react-hooks/exhaustive-deps -- STORAGE_KEY_* excluded
  // to avoid writing stale data to a new workspace key during workspace transition.
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
          void hydrateConversationMessages(curActive);
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
    (rows: { id: string; index?: number; role: string; content: string; timestamp: number; chain_summary?: ChainSummaryItem[]; chain_timeline?: ChainTimelineGroup[]; artifacts?: ChatArtifact[]; sources?: ChatSource[]; mcp_calls?: ChatMcpCall[]; attachments?: ChatAttachment[]; org_timeline?: OrgTimelineEntry[]; ask_user?: ChatAskUser; todo?: ChatTodo; progress_events?: ChatProgressEvent[]; parts?: MessagePart[]; usage?: ChatMessage["usage"] }[]): ChatMessage[] => {
      return rows.map((m) => ({
        id: m.id,
        ...(typeof m.index === "number" ? { historyIndex: m.index } : {}),
        role: m.role as "user" | "assistant" | "system",
        content: m.content,
        timestamp: m.timestamp,
        // Prefer the faithful causal timeline; fall back to the lossy summary
        // for messages persisted before chain_timeline existed.
        ...(m.chain_timeline?.length
          ? { thinkingChain: buildChainFromTimeline(m.chain_timeline) }
          : m.chain_summary?.length
            ? { thinkingChain: buildChainFromSummary(m.chain_summary) }
            : {}),
        ...(m.artifacts?.length ? { artifacts: m.artifacts } : {}),
        ...(m.sources?.length ? { sources: m.sources } : {}),
        ...(m.mcp_calls?.length ? { mcpCalls: m.mcp_calls } : {}),
        ...(m.attachments?.length ? { attachments: m.attachments } : {}),
        ...(m.org_timeline?.length ? { orgTimeline: m.org_timeline } : {}),
        ...(m.todo?.steps?.length ? { todo: m.todo } : {}),
        ...(m.progress_events?.length ? { progressEvents: m.progress_events } : {}),
        ...(m.ask_user ? { askUser: m.ask_user, content: "" } : {}),
        ...(m.parts?.length ? { parts: m.parts } : {}),
        ...(m.usage ? { usage: m.usage } : {}),
      }));
    },
    [],
  );

  // Re-attach a still-executing plan (not yet finalized into history) to the
  // latest assistant message so switching windows / reloading mid-run does not
  // drop the live plan card (#615).
  const mergeActiveTodo = useCallback(
    (msgs: ChatMessage[], activeTodo: ChatTodo | null | undefined): ChatMessage[] => {
      if (!activeTodo || !activeTodo.steps?.length) return msgs;
      let lastAssistant = -1;
      for (let i = msgs.length - 1; i >= 0; i -= 1) {
        if (msgs[i].role === "assistant") { lastAssistant = i; break; }
      }
      if (lastAssistant < 0) return msgs;
      const target = msgs[lastAssistant];
      if (target.todo && JSON.stringify(target.todo) === JSON.stringify(activeTodo)) return msgs;
      const next = msgs.slice();
      next[lastAssistant] = { ...target, todo: activeTodo };
      return next;
    },
    [],
  );

  const hydrateConversationMessages = useCallback(async (convId: string) => {
    const seq = ++hydrateSeqRef.current;
    if (shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
      setHydrating(true);
    }
    const finishHydrating = () => {
      if (seq === hydrateSeqRef.current && shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
        setHydrating(false);
      }
    };
    const storedMsgs = loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + convId).slice(-STORED_MESSAGE_WINDOW);
    const liveCtx = streamContexts.current.get(convId);
    const liveMsgs = liveCtx?.messages?.length ? liveCtx.messages.slice(-STORED_MESSAGE_WINDOW) : [];
    const canUseActiveMsgs =
      convId === activeConvIdRef.current &&
      displayedMessagesConvIdRef.current === convId &&
      latestMessagesRef.current.length > 0;
    const activeMsgs = canUseActiveMsgs
      ? latestMessagesRef.current.slice(-STORED_MESSAGE_WINDOW)
      : [];
    const localRichness = (msgs: ChatMessage[]) => msgs.reduce((score, msg) =>
      score +
      (msg.todo?.steps?.length ? 1000 + msg.todo.steps.length : 0) +
      (msg.parts?.length ? 100 + msg.parts.length : 0) +
      (msg.progressEvents?.length ? 10 + msg.progressEvents.length : 0) +
      (msg.thinkingChain?.length ? 50 + msg.thinkingChain.length : 0) +
      (msg.artifacts?.length ? 20 + msg.artifacts.length : 0) +
      (msg.attachments?.length ? 20 + msg.attachments.length : 0) +
      (msg.askUser ? 10 : 0) +
      (msg.streaming ? 5 : 0) +
      Math.min(msg.content.length, 2000) / 2000,
    0);
    const localMsgs = [storedMsgs, liveMsgs, activeMsgs].reduce((best, candidate) => {
      if (candidate.length !== best.length) {
        return candidate.length > best.length ? candidate : best;
      }
      return localRichness(candidate) > localRichness(best) ? candidate : best;
    }, [] as ChatMessage[]);

    // Always ask the backend when available.  A completed answer may be saved
    // there after a desktop/web SSE disconnect while localStorage still has the
    // interrupted placeholder with the same message count.
    const shouldSyncBackend = serviceRunning;

    if (!shouldSyncBackend) {
      if (seq === hydrateSeqRef.current && shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
        renderConversationMessages(convId, localMsgs);
        setHistoryPage({
          total: localMsgs.length,
          startIndex: null,
          hasMoreBefore: false,
          loadingOlder: false,
        });
      }
      finishHydrating();
      return;
    }

    try {
      const res = await safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${HISTORY_PAGE_LIMIT}`);
      const data = await res.json();
      const backendMsgs = Array.isArray(data?.messages) ? mapBackendHistoryToMessages(data.messages) : [];

      const chosen = mergeActiveTodo(chooseHydratedMessages(localMsgs, backendMsgs), data?.active_todo);
      if (seq === hydrateSeqRef.current && shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
        renderConversationMessages(convId, chosen);
        setHistoryPage({
          total: typeof data?.total === "number" ? data.total : chosen.length,
          startIndex: typeof data?.start_index === "number" ? data.start_index : null,
          hasMoreBefore: Boolean(data?.has_more_before),
          loadingOlder: false,
        });
      }
      finishHydrating();

      if (chosen !== localMsgs) {
        saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, chosen);
      }
    } catch {
      if (seq === hydrateSeqRef.current && shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
        renderConversationMessages(convId, localMsgs);
        setHistoryPage({
          total: localMsgs.length,
          startIndex: null,
          hasMoreBefore: false,
          loadingOlder: false,
        });
      }
      finishHydrating();
    }
  }, [serviceRunning, apiBaseUrl, mapBackendHistoryToMessages, mergeActiveTodo, STORAGE_KEY_MSGS_PREFIX, renderConversationMessages]);

  const loadOlderMessages = useCallback(async () => {
    const convId = activeConvIdRef.current;
    if (!convId || !serviceRunning || historyPage.loadingOlder || !historyPage.hasMoreBefore || historyPage.startIndex == null) {
      return;
    }
    setHistoryPage((prev) => ({ ...prev, loadingOlder: true }));
    messageListRef.current?.saveScrollPosition();
    try {
      const res = await safeFetch(
        `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${HISTORY_PAGE_LIMIT}&before=${historyPage.startIndex}`,
      );
      const data = await res.json();
      const olderMsgs = Array.isArray(data?.messages) ? mapBackendHistoryToMessages(data.messages) : [];
      if (!shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
        setHistoryPage((prev) => ({ ...prev, loadingOlder: false }));
        return;
      }
      if (olderMsgs.length > 0) {
        setMessages((prev) => {
          const seen = new Set(prev.map((m) => m.id));
          return [...olderMsgs.filter((m) => !seen.has(m.id)), ...prev];
        });
      }
      setHistoryPage({
        total: typeof data?.total === "number" ? data.total : historyPage.total,
        startIndex: typeof data?.start_index === "number" ? data.start_index : historyPage.startIndex,
        hasMoreBefore: Boolean(data?.has_more_before),
        loadingOlder: false,
      });
      requestAnimationFrame(() => messageListRef.current?.restoreScrollPosition());
    } catch {
      setHistoryPage((prev) => ({ ...prev, loadingOlder: false }));
      requestAnimationFrame(() => messageListRef.current?.restoreScrollPosition());
    }
  }, [serviceRunning, historyPage, apiBaseUrl, mapBackendHistoryToMessages]);

  useEffect(() => {
    if (!activeConvId) {
      setMessages([]);
      setHistoryPage({ total: 0, startIndex: null, hasMoreBefore: false, loadingOlder: false });
      displayedMessagesConvIdRef.current = null;
      return;
    }
    if (skipConvLoadRef.current) {
      skipConvLoadRef.current = false;
      return;
    }

    // If a StreamContext is actively streaming for this conv, restore its state directly
    const ctx = streamContexts.current.get(activeConvId);
    if (ctx?.isStreaming) {
      renderConversationMessages(activeConvId, ctx.messages);
      setDisplayActiveSubAgents(ctx.activeSubAgents);
      setDisplaySubAgentTasks(ctx.subAgentTasks);
    } else {
      void hydrateConversationMessages(activeConvId);
      setDisplayActiveSubAgents([]);
      setDisplaySubAgentTasks([]);
    }

    convSwitchScrollRef.current = true;
    const conv = conversations.find((c) => c.id === activeConvId);
    const agentId = conv?.agentProfileId || "default";
    isConvSwitchRef.current = true;
    setSelectedAgent(agentId);
    setSelectedEndpoint(conv?.endpointId || "auto");
    setSelectedEndpointPolicy(conv?.endpointPolicy || "prefer");
    isOrgConvSwitchRef.current = true;
    setOrgMode(Boolean(conv?.orgMode && conv?.orgId));
    setSelectedOrgId(conv?.orgId || null);
    setSelectedOrgNodeId(conv?.orgNodeId || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- conversations 故意排除：
    // 此 effect 语义是"切换对话时加载消息"，不应因 messageCount/title 等元数据变更而重新 hydrate，
    // 否则流结束后 setConversations 更新 messageCount 会触发竞态覆盖。
  }, [activeConvId, hydrateConversationMessages, renderConversationMessages]);

  // If the local SSE was lost while the backend task keeps running (for
  // example after a window switch or a superseded fetch), StreamContext is gone
  // and the normal live renderer has nothing to flush. Keep the visible
  // conversation reconciled from backend history/active_todo until the backend
  // reports it idle.
  useEffect(() => {
    if (!serviceRunning || !activeConvId) return;
    if (streamContexts.current.get(activeConvId)?.isStreaming) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const convId = activeConvId;

    const pollDetachedRunningConversation = async () => {
      if (cancelled || !shouldRenderConversationMessages(convId, activeConvIdRef.current)) return;
      if (streamContexts.current.get(convId)?.isStreaming) return;

      let busy = false;
      try {
        const busyResp = await safeFetch(
          `${apiBaseUrl}/api/chat/busy?conversation_id=${encodeURIComponent(convId)}`,
          { method: "GET", signal: AbortSignal.timeout(4000) },
        );
        const busyData = await busyResp.json().catch(() => null);
        busy = Boolean(busyData?.busy);
      } catch {
        // If the probe fails, leave the existing UI alone and try again later.
        if (!cancelled) timer = setTimeout(pollDetachedRunningConversation, 5000);
        return;
      }

      if (cancelled || !shouldRenderConversationMessages(convId, activeConvIdRef.current)) return;

      if (!busy) {
        const conv = latestConversationsRef.current.find((c) => c.id === convId);
        if (conv?.status === "running") updateConvStatus(convId, "completed");
        void hydrateConversationMessages(convId);
        return;
      }

      if (latestConversationsRef.current.find((c) => c.id === convId)?.status !== "running") {
        updateConvStatus(convId, "running");
      }
      try {
        const histResp = await safeFetch(
          `${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/history?limit=${HISTORY_PAGE_LIMIT}`,
          { method: "GET", signal: AbortSignal.timeout(6000) },
        );
        const data = await histResp.json();
        const backendMsgs = Array.isArray(data?.messages) ? mapBackendHistoryToMessages(data.messages) : [];
        const displayedMsgs =
          displayedMessagesConvIdRef.current === convId
            ? latestMessagesRef.current.slice(-STORED_MESSAGE_WINDOW)
            : [];
        const storedMsgs = loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + convId).slice(-STORED_MESSAGE_WINDOW);
        const localMsgs = displayedMsgs.length >= storedMsgs.length ? displayedMsgs : storedMsgs;
        const chosen = mergeActiveTodo(chooseHydratedMessages(localMsgs, backendMsgs), data?.active_todo);
        if (!cancelled && shouldRenderConversationMessages(convId, activeConvIdRef.current)) {
          renderConversationMessages(convId, chosen);
          saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + convId, chosen);
        }
      } catch {
        // The next poll will retry; avoid replacing visible messages with an
        // error bubble for a transient history read.
      }

      if (!cancelled) timer = setTimeout(pollDetachedRunningConversation, 2500);
    };

    void pollDetachedRunningConversation();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [
    activeConvId,
    serviceRunning,
    streamingTick,
    apiBaseUrl,
    hydrateConversationMessages,
    mapBackendHistoryToMessages,
    mergeActiveTodo,
    renderConversationMessages,
    updateConvStatus,
    STORAGE_KEY_MSGS_PREFIX,
  ]);

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

  const refreshContextStats = useCallback(async (conversationId?: string | null) => {
    if (!serviceRunning) return;
    const reqSeq = ++contextStatsReqSeqRef.current;
    try {
      const params = new URLSearchParams();
      if (conversationId) params.set("conversation_id", conversationId);
      const query = params.toString();
      const res = await safeFetch(
        `${apiBaseUrl}/api/stats/tokens/context${query ? `?${query}` : ""}`,
      );
      const data = await res.json();
      if (reqSeq !== contextStatsReqSeqRef.current) return;
      if (typeof data.context_tokens === "number" && Number.isFinite(data.context_tokens)) {
        setContextTokens(Math.max(0, data.context_tokens));
      }
      if (typeof data.context_limit === "number" && Number.isFinite(data.context_limit) && data.context_limit > 0) {
        setContextLimit(data.context_limit);
      }
    } catch {
      // ignore context stat refresh errors
    }
  }, [apiBaseUrl, serviceRunning]);

  // Fetch context stats when current conversation changes.
  useEffect(() => {
    if (!visible) return;
    void refreshContextStats(activeConvId);
  }, [activeConvId, visible, refreshContextStats]);

  const formatContextTokens = useCallback((n: number): string => {
    if (!Number.isFinite(n) || n <= 0) return "0";
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
  }, []);

  const openContextEditor = useCallback(() => {
    const draftLimit = Number(envDraft.CONTEXT_MAX_WINDOW || "0");
    const preferredLimit = contextLimit > 0 ? contextLimit : (Number.isFinite(draftLimit) ? draftLimit : 0);
    setEditingContextLimit(String(Math.max(preferredLimit, 1000)));
    setContextEditOpen(true);
  }, [contextLimit, envDraft]);

  const saveContextLimit = useCallback(async () => {
    const parsed = Number(editingContextLimit);
    const nextLimit = Number.isFinite(parsed) ? Math.round(parsed) : 0;
    if (nextLimit < 1000) {
      notifyError(t("chat.contextEditInvalid", "上下文长度至少为 1000 tokens"));
      return;
    }

    setContextSaving(true);
    const nextValue = String(nextLimit);
    let restartRequired = false;

    try {
      if (setEnvDraft) {
        setEnvDraft((prev) => ({ ...prev, CONTEXT_MAX_WINDOW: nextValue }));
      }

      if (saveEnvKeys) {
        try {
          const result = await saveEnvKeys(["CONTEXT_MAX_WINDOW"]);
          if (result && typeof result === "object" && "restartRequired" in result) {
            restartRequired = Boolean((result as { restartRequired?: boolean }).restartRequired);
          }
        } catch (err) {
          logger.warn("Chat", "saveEnvKeys failed before direct env write", { error: String(err) });
        }
      }

      const envRes = await safeFetch(`${apiBaseUrl}/api/config/env`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries: { CONTEXT_MAX_WINDOW: nextValue }, delete_keys: [] }),
      });
      if (!envRes.ok) throw new Error(`HTTP ${envRes.status}`);
      const envData = await envRes.json().catch(() => ({}));
      restartRequired = restartRequired || Boolean(envData.restart_required);

      setContextLimit(nextLimit);
      setContextEditOpen(false);
      notifyInfo(t("chat.contextEditSaved", "上下文长度已更新"));
      if (restartRequired) {
        notifyInfo(t("chat.contextEditRestartHint", "该配置在当前版本可能需要重启服务后完全生效"));
      }
      await refreshContextStats(activeConvId);
    } catch (err) {
      logger.error("Chat", "Failed to save context length", { error: String(err) });
      notifyError(t("chat.contextEditFailed", "保存上下文长度失败，请稍后重试"));
    } finally {
      setContextSaving(false);
    }
  }, [editingContextLimit, setEnvDraft, saveEnvKeys, apiBaseUrl, t, refreshContextStats, activeConvId]);

  useEffect(() => {
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
  }, [apiBaseUrl, serviceRunning, visible]);

  useEffect(() => {
    if (!visible || !serviceRunning) return;
    const fetchOrgs = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/v2/orgs`);
        const data = await res.json();
        setOrgList(data.map((o: any) => ({ id: o.id, name: o.name, icon: o.icon || "", status: o.status })));
      } catch { /* ignore */ }
    };
    fetchOrgs();
  }, [apiBaseUrl, serviceRunning, visible]);

  // Sync selectedAgent → current conversation's agentProfileId
  // Only react to selectedAgent changes (not activeConvId) to avoid overwriting
  // a newly-switched conversation with the previous conversation's agent.
  // isConvSwitchRef prevents write-back when selectedAgent was set by a conversation switch.
  const prevSelectedAgentRef = useRef(selectedAgent);
  const isConvSwitchRef = useRef(false);
  useEffect(() => {
    if (isConvSwitchRef.current) {
      isConvSwitchRef.current = false;
      prevSelectedAgentRef.current = selectedAgent;
      return;
    }
    if (selectedAgent === prevSelectedAgentRef.current) return;
    prevSelectedAgentRef.current = selectedAgent;
    const convId = activeConvIdRef.current;
    if (!convId) return;
    setConversations((prev) => {
      const current = prev.find((c) => c.id === convId);
      if (current?.agentProfileId === selectedAgent) return prev;
      return prev.map((c) => c.id === convId ? { ...c, agentProfileId: selectedAgent } : c);
    });
  }, [activeConvId, selectedAgent]);

  // Sync selectedEndpoint/selectedEndpointPolicy → current conversation's model selection.
  const prevSelectedEndpointRef = useRef({ selectedEndpoint, selectedEndpointPolicy });
  useEffect(() => {
    const prevSelected = prevSelectedEndpointRef.current;
    if (
      selectedEndpoint === prevSelected.selectedEndpoint &&
      selectedEndpointPolicy === prevSelected.selectedEndpointPolicy
    ) return;
    prevSelectedEndpointRef.current = { selectedEndpoint, selectedEndpointPolicy };
    const convId = activeConvIdRef.current;
    if (!convId) return;
    const epVal = selectedEndpoint === "auto" ? undefined : selectedEndpoint;
    const policyVal = epVal ? selectedEndpointPolicy : undefined;
    setConversations((prev) => {
      const current = prev.find((c) => c.id === convId);
      if (
        (current?.endpointId || undefined) === epVal &&
        (current?.endpointPolicy || undefined) === policyVal
      ) return prev;
      return prev.map((c) => c.id === convId ? { ...c, endpointId: epVal, endpointPolicy: policyVal } : c);
    });
  }, [selectedEndpoint, selectedEndpointPolicy]);

  // Sync organization mode → current conversation.
  // This mirrors endpoint/agent isolation so two chat windows can keep different orgs.
  const prevOrgSelectionRef = useRef({
    orgMode,
    selectedOrgId,
    selectedOrgNodeId,
  });
  useEffect(() => {
    const prev = prevOrgSelectionRef.current;
    if (isOrgConvSwitchRef.current) {
      isOrgConvSwitchRef.current = false;
      prevOrgSelectionRef.current = { orgMode, selectedOrgId, selectedOrgNodeId };
      return;
    }
    if (
      prev.orgMode === orgMode &&
      prev.selectedOrgId === selectedOrgId &&
      prev.selectedOrgNodeId === selectedOrgNodeId
    ) {
      return;
    }
    prevOrgSelectionRef.current = { orgMode, selectedOrgId, selectedOrgNodeId };
    const convId = activeConvIdRef.current;
    if (!convId) return;
    const nextOrgMode = Boolean(orgMode && selectedOrgId);
    setConversations((prevConvs) => {
      const current = prevConvs.find((c) => c.id === convId);
      if (
        current?.orgMode === nextOrgMode &&
        (current?.orgId || undefined) === (nextOrgMode ? selectedOrgId || undefined : undefined) &&
        (current?.orgNodeId || undefined) === (
          nextOrgMode ? selectedOrgNodeId || undefined : undefined
        )
      ) {
        return prevConvs;
      }
      return prevConvs.map((c) =>
        c.id === convId
          ? {
              ...c,
              orgMode: nextOrgMode,
              orgId: nextOrgMode ? selectedOrgId || undefined : undefined,
              orgNodeId: nextOrgMode ? selectedOrgNodeId || undefined : undefined,
            }
          : c
      );
    });
  }, [activeConvId, orgMode, selectedOrgId, selectedOrgNodeId, setConversations]);

  // Validate selectedEndpoint against current endpoints list.
  // When endpoints is empty (new workspace / no config), also reset to "auto"
  // so a stale selection from a previous workspace doesn't leak through.
  useEffect(() => {
    if (selectedEndpoint === "auto") return;
    if (!endpoints.some((ep) => ep.name === selectedEndpoint)) {
      setSelectedEndpoint("auto");
    }
  }, [endpoints, selectedEndpoint]);

  useEffect(() => {
    const convId = activeConvId;
    if (!convId) return;
    const conv = conversations.find((c) => c.id === convId);
    if (!conv) return;
    // Avoid creating empty backend sessions just because the user explored selectors.
    if ((conv.messageCount || 0) === 0 && messages.length === 0) return;

    const timer = setTimeout(() => {
      safeFetch(`${apiBaseUrl}/api/sessions/${encodeURIComponent(convId)}/ui-state`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpointId: selectedEndpoint === "auto" ? null : selectedEndpoint,
          endpointPolicy: selectedEndpoint === "auto" ? "prefer" : selectedEndpointPolicy,
          orgMode: Boolean(orgMode && selectedOrgId),
          orgId: orgMode && selectedOrgId ? selectedOrgId : null,
          orgNodeId: orgMode && selectedOrgId ? selectedOrgNodeId : null,
        }),
      }).catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [
    activeConvId,
    selectedEndpoint,
    selectedEndpointPolicy,
    orgMode,
    selectedOrgId,
    selectedOrgNodeId,
    conversations,
    messages.length,
    apiBaseUrl,
  ]);

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

        const backendSessions: ChatConversation[] = data.sessions || [];

        // ── Factory reset detection (epoch-based only) ──
        // Only clear local data when data_epoch actually changes, which signals
        // that the backend's data/ directory was recreated (true factory reset).
        // We intentionally do NOT wipe localStorage when "ready + 0 sessions",
        // because that can be a false positive: e.g. a version upgrade changes
        // Session serialisation and _load_sessions silently skips all old
        // sessions, yet sessions.json on disk is still intact.
        const epoch = data.data_epoch as string | undefined;
        // Drop the legacy GLOBAL epoch key that caused #635: it was shared across
        // all workspaces, so switching workspaces always made it differ from the
        // new workspace's epoch and was misread as a factory reset, wiping the
        // local conversation list of the workspace you just switched into.
        // Harmless if absent; idempotent.
        try { localStorage.removeItem("openakita_data_epoch"); } catch { /* ignore */ }

        if (epoch) {
          const cached = localStorage.getItem(STORAGE_KEY_DATA_EPOCH);
          localStorage.setItem(STORAGE_KEY_DATA_EPOCH, epoch);
          if (cached && cached !== epoch) {
            setConversations((prev) => {
              for (const c of prev) {
                try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + c.id); } catch {}
              }
              return [];
            });
            activateConversation(null);
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
          endpointId: s.endpointId,
          endpointPolicy: s.endpointPolicy,
          orgMode: s.orgMode,
          orgId: s.orgId,
          orgNodeId: s.orgNodeId,
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
              // 后端时间戳现以"最后一条真实消息"为准（见后端 #628 修复），是列表
              // 排序/显示的权威值。这里**不能**再无脑 Math.max：早期被旧逻辑
              // 污染（≈打开时刻）并缓存进 localStorage 的 local.timestamp 会被
              // Math.max 永久锁住，导致升级后时间与顺序仍然不对。只有正在流式
              // 输出的会话保留本地乐观值，避免对账瞬间把活跃会话往下挪。
              timestamp: streamContexts.current.has(b.id)
                ? Math.max(local.timestamp || 0, b.timestamp || 0)
                : (b.timestamp || local.timestamp || 0),
              messageCount: Math.max(local.messageCount || 0, b.messageCount || 0),
              agentProfileId: b.agentProfileId || local.agentProfileId,
              endpointId: b.endpointId || local.endpointId,
              endpointPolicy: b.endpointPolicy || local.endpointPolicy,
              orgMode: b.orgMode ?? local.orgMode,
              orgId: b.orgId || local.orgId,
              orgNodeId: b.orgNodeId || local.orgNodeId,
            };
          });
          const backendIds = new Set(restoredConvs.map((c) => c.id));
          const localOnly = prev.filter((c) => !backendIds.has(c.id));
          return [...mergedFromBackend, ...localOnly];
        });

        // 没有活跃会话时，默认打开后端最新会话
        if (!activeConvId) {
          activateConversation(restoredConvs[0].id);
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
  }, [serviceRunning, apiBaseUrl, activeConvId, activateConversation]);

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
            .then((d2) => {
              if (!d2?.messages?.length || activeConvIdRef.current !== convId) return;
              if (displayedMessagesConvIdRef.current !== convId) return;
              renderConversationMessages(
                convId,
                patchMessagesWithBackend(latestMessagesRef.current, d2.messages),
              );
            })
            .catch(() => {});
        }
        const preview = (d.last_message_preview as string) || "";
        const title = (d.title as string) || "";
        const ts = ((d.timestamp as number) || 0) * 1000 || Date.now();
        setConversations((prev) => {
          const idx = prev.findIndex(c => c.id === convId);
          if (idx >= 0) {
            const updated = [...prev];
            updated[idx] = { ...updated[idx], title: title || updated[idx].title, lastMessage: preview || updated[idx].lastMessage, timestamp: Math.max(updated[idx].timestamp || 0, ts), messageCount: (updated[idx].messageCount || 0) + 1 };
            return updated;
          }
          return [{ id: convId, title: title || preview.slice(0, 20) || "对话", lastMessage: preview, timestamp: ts, messageCount: 1 }, ...prev];
        });
        if (!activeConvIdRef.current) {
          activateConversation(convId);
        }
      } else if (event === "chat:conversation_deleted") {
        setConversations((prev) => {
          const filtered = prev.filter(c => c.id !== convId);
          if (filtered.length < prev.length) {
            try { localStorage.removeItem(STORAGE_KEY_MSGS_PREFIX + convId); } catch {}
          }
          return filtered;
        });
        if (activeConvIdRef.current === convId) {
          activateConversation(null);
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
  }, [apiBaseUrl, getClientId, activateConversation]);

  // ── Read-only protection state initialization + WS listener ──
  useEffect(() => {
    safeFetch(`${apiBaseUrl}/api/config/security/self-protection`)
      .then(r => r.json())
      .then(data => { if (data?.readonly_mode) setDeathSwitchActive(true); })
      .catch(() => {});
    return onWsEvent((event, data) => {
      if (event !== "security:death_switch") return;
      const d = data as Record<string, unknown> | null;
      if (d && typeof d.active === "boolean") setDeathSwitchActive(d.active);
    });
  }, [apiBaseUrl]);

  // ── C18 Phase B: load confirmation.aggregation_window_seconds once ──
  // 默认 0（关）。后端 POLICIES.yaml hot-reload 路径 (C18 Phase A) 改了
  // 这个字段时，前端不会自动刷新——下次 ChatView 重新挂载（reload / 切
  // 换页面回来）即可。这与已有的 self-protection 读法保持一致。
  useEffect(() => {
    safeFetch(`${apiBaseUrl}/api/config/security/confirmation`)
      .then(r => r.json())
      .then(data => {
        const v = Number(data?.aggregation_window_seconds);
        if (Number.isFinite(v) && v > 0) setSecurityAggWindow(v);
      })
      .catch(() => {});
  }, [apiBaseUrl]);

  // ── Backend-owned security-confirm presentation queue ──
  useEffect(() => {
    return onWsEvent((event, raw) => {
      const data = _asRecord(raw);
      if (event === "security_confirm_promoted") {
        const confirmRaw = _asRecord(data.confirm);
        if (Object.keys(confirmRaw).length === 0) return;
        const next = _securityConfirmFromBackend(confirmRaw);
        if (!next) return;
        const convId = next.conversationId || "";
        const activeConvId = activeConvIdRef.current || "";
        if (convId && activeConvId && convId !== activeConvId) return;
        if (!_isActiveSecurityConfirm(next)) return;
        setSecurityConfirm(next);
        setSecurityQueueLen(next.queuedCount);
        return;
      }

      if (event !== "confirm_revoked") return;
      const convId = String(data.session_id || "");
      const activeConvId = activeConvIdRef.current || "";
      if (convId && activeConvId && convId !== activeConvId) return;
      const confirmId = String(data.confirm_id || "");
      setSecurityConfirm((prev) => (
        prev && prev.toolId === confirmId ? null : prev
      ));
      setSecurityQueueLen(_asFiniteCount(data.queued_count));
    });
  }, []);

  // ── Sub-agent real-time updates via WebSocket (reduces polling dependency) ──
  useEffect(() => {
    return onWsEvent((event, raw) => {
      if (event !== "agents:sub_state") return;
      const d = raw as Record<string, unknown> | null;
      if (!d || !d.agent_id) return;

      const convId = activeConvIdRef.current;
      if (!convId) return;
      const chatId = (d.chat_id || d.session_id || "") as string;
      if (chatId && chatId !== convId) return;

      const patch = d as unknown as SubAgentTask;
      const ctx = streamContexts.current.get(convId);
      if (!ctx) return;

      // P5.1: enrich with the inferred parent so SubAgentCards can build the
      // delegation tree.  We only set when known so a missing parent never
      // overwrites a previously-known one.
      const inferredParent = parentAgentMapRef.current.get(patch.agent_id);
      const enrichedPatch: SubAgentTask = inferredParent
        ? { ...patch, parent_agent_id: patch.parent_agent_id || inferredParent }
        : patch;

      const idx = ctx.subAgentTasks.findIndex((t) => t.agent_id === enrichedPatch.agent_id);
      if (idx >= 0) {
        ctx.subAgentTasks = ctx.subAgentTasks.map((t, i) =>
          i === idx ? { ...t, ...enrichedPatch } : t,
        );
      } else if (enrichedPatch.status === "starting" || enrichedPatch.status === "running") {
        ctx.subAgentTasks = [...ctx.subAgentTasks, enrichedPatch];
      }
      if (activeConvIdRef.current === convId) {
        setDisplaySubAgentTasks([...ctx.subAgentTasks]);
      }
    });
  }, []);

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
        if (activeConvIdRef.current !== convId) return;
        if (displayedMessagesConvIdRef.current !== convId) return;
        renderConversationMessages(
          convId,
          patchMessagesWithBackend(latestMessagesRef.current, data.messages),
        );
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
  const uploadFile = useCallback(async (file: Blob, filename: string): Promise<{
    url: string;
    localPath?: string;
    uploadId?: string;
    size?: number;
    mimeType?: string;
  }> => {
    const form = new FormData();
    form.append("file", file, filename);
    const res = await safeFetch(`${apiBaseRef.current}/api/upload`, {
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
  // MessageList 内部的 sticky-bottom 状态机负责流式追踪与"上滚即停"；
  // 此处只处理: (1) 切换对话后 hydrate 完成 (2) 从隐藏变可见。
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
    { id: "agent", label: "切换 Agent", description: "切换当前会话的 Agent", action: (args) => {
      // 真切换：直接写 selectedAgent（下一条消息会随 chat 请求带上 agent_profile_id
      // 由后端 _apply_agent_profile 写入 session.context，与 IM `/切换` 语义对齐）。
      const trimmed = (args || "").trim();
      if (!trimmed) {
        const lines = agentProfiles.map((p) => {
          const marker = p.id === selectedAgent ? " ⬅️ 当前" : "";
          return `- \`${p.id}\` — ${p.icon || "🤖"} ${p.name}: ${p.description}${marker}`;
        });
        const body = lines.length
          ? `**可用 Agent**（共 ${agentProfiles.length} 个）：\n${lines.join("\n")}\n\n用法：\`/agent <agent_id>\``
          : "暂无可用 Agent。请检查 Agent 配置或稍后再试。";
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: body, timestamp: Date.now() }]);
        return;
      }
      const q = trimmed.toLowerCase();
      // 与 @ 选择器一致：先精确 id 命中，再宽松匹配 id/name
      const exact = agentProfiles.find((p) => p.id.toLowerCase() === q);
      const candidates = exact ? [exact] : agentProfiles.filter(
        (p) => p.id.toLowerCase().includes(q) || p.name.toLowerCase().includes(q),
      );
      if (candidates.length === 0) {
        setMessages((prev) => [...prev, {
          id: genId(), role: "system",
          content: `❌ 未找到 Agent \`${trimmed}\`。\n发送 \`/agent\` 不带参数可以查看所有 Agent。`,
          timestamp: Date.now(),
        }]);
        return;
      }
      if (candidates.length > 1) {
        const lines = candidates.map((p) => `- \`${p.id}\` — ${p.icon || "🤖"} ${p.name}`);
        setMessages((prev) => [...prev, {
          id: genId(), role: "system",
          content: `🔍 匹配到 ${candidates.length} 个 Agent，请用更精确的 id：\n${lines.join("\n")}`,
          timestamp: Date.now(),
        }]);
        return;
      }
      const target = candidates[0];
      if (target.id === selectedAgent) {
        setMessages((prev) => [...prev, {
          id: genId(), role: "system",
          content: `ℹ️ 当前已是 ${target.icon || "🤖"} **${target.name}**`,
          timestamp: Date.now(),
        }]);
        return;
      }
      setSelectedAgent(target.id);
      setMessages((prev) => [...prev, {
        id: genId(), role: "system",
        content: `✅ 已切换到 ${target.icon || "🤖"} **${target.name}** (\`${target.id}\`)`,
        timestamp: Date.now(),
      }]);
    }},
    { id: "agents", label: "查看 Agent 列表", description: "显示可用的 Agent 列表", action: () => {
      if (!agentProfiles.length) {
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "暂无可用 Agent。请检查 Agent 配置或稍后再试。", timestamp: Date.now() }]);
        return;
      }
      const lines = agentProfiles.map((p) => {
        const marker = p.id === selectedAgent ? " ⬅️ 当前" : "";
        return `- \`${p.id}\` — ${p.icon || "🤖"} ${p.name}: ${p.description}${marker}`;
      });
      setMessages((prev) => [...prev, {
        id: genId(), role: "system",
        content: `**可用 Agent**（共 ${agentProfiles.length} 个）：\n${lines.join("\n")}\n\n切换：\`/agent <agent_id>\``,
        timestamp: Date.now(),
      }]);
    }},
    { id: "org", label: "组织模式", description: "切换到组织编排模式，向组织下命令", action: (args) => {
      if (args === "off" || args === "关闭") {
        setOrgMode(false);
        setSelectedOrgId(null);
        setSelectedOrgNodeId(null);
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: "已退出组织模式", timestamp: Date.now() }]);
      } else if (args) {
        const match = orgList.find(o => o.name.includes(args) || o.id === args);
        if (match) {
          setOrgMode(true);
          setSelectedOrgId(match.id);
          setSelectedOrgNodeId(null);
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
    { id: "thinking_depth", label: "思考程度", description: "设置思考程度 (low/medium/high/max)", action: (args) => {
      const depth = args?.toLowerCase().trim();
      const normalizedDepth = depth === "xhigh" ? "max" : depth;
      if (normalizedDepth === "low" || normalizedDepth === "medium" || normalizedDepth === "high" || normalizedDepth === "max") {
        setThinkingDepth(normalizedDepth);
        const label = { low: "低", medium: "中", high: "高", max: "最大" }[normalizedDepth];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `思考程度已设置为: ${label}`, timestamp: Date.now() }]);
      } else {
        const currentLabel = { low: "低", medium: "中", high: "高", max: "最大" }[thinkingDepth];
        setMessages((prev) => [...prev, { id: genId(), role: "system", content: `当前思考程度: ${currentLabel}\n用法: /thinking_depth low|medium|high|max`, timestamp: Date.now() }]);
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
    { id: "unlock", label: "解除只读", description: "解除只读保护，恢复 Agent 写入能力", action: () => {
      safeFetch(`${apiBase}/api/config/security/death-switch/reset`, { method: "POST" })
        .then(() => {
          setDeathSwitchActive(false);
          setMessages((prev) => [...prev, { id: genId(), role: "system", content: "只读保护已解除，Agent 可以继续执行写入操作。", timestamp: Date.now() }]);
        })
        .catch(() => {
          setMessages((prev) => [...prev, { id: genId(), role: "system", content: "重置失败，请检查后端服务状态。", timestamp: Date.now() }]);
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
  }, [endpoints, chatMode, orgList, orgMode, thinkingMode, thinkingDepth, activeConvId, apiBase, agentProfiles, selectedAgent]);

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
    activateConversation(id);
    displayedMessagesConvIdRef.current = id;
    setMessages([]);
    setPendingAttachments([]);
    setDisplayActiveSubAgents([]);
    setDisplaySubAgentTasks([]);
    setSelectedEndpoint("auto");
    setOrgMode(false);
    setSelectedOrgId(null);
    setSelectedOrgNodeId(null);
    setConversations((prev) => [{
      id,
      title: "新对话",
      lastMessage: "",
      timestamp: Date.now(),
      messageCount: 0,
      agentProfileId: selectedAgent,
      orgMode: false,
    }, ...prev]);
  }, [activeConvId, messages, selectedAgent, setInputValue, activateConversation]);

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
          activateConversation(remaining[0].id);
          renderConversationMessages(
            remaining[0].id,
            loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + remaining[0].id),
          );
        } else {
          activateConversation(null);
          setMessages([]);
        }
        return remaining;
      });
    } else {
      setConversations((prev) => prev.filter((c) => c.id !== convId));
    }
  }, [serviceRunning, activateConversation, renderConversationMessages]);

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
  const sendMessage = useCallback(async (
    overrideText?: string,
    targetConvId?: string,
    displayContent?: string,
    modeOverride?: "agent" | "plan" | "ask",
    attachmentsOverride?: ChatAttachment[],
    askUserReply?: AskUserReplyBody,
    options?: SendMessageOptions,
  ) => {
    const text = (overrideText ?? inputTextRef.current).trim();
    const streamTransport = options?.streamTransport;
    const isResumeTransport = streamTransport?.kind === "resume";
    const appendUserMessage = options?.appendUserMessage !== false;
    // ``attachmentsOverride`` lets callers (e.g. the queue drain) replay a
    // previously-captured attachment set instead of the live composer state.
    // When omitted we fall back to the composer's pending attachments.
    const attachmentsToSend = attachmentsOverride ?? pendingAttachments;
    if (!text && attachmentsToSend.length === 0 && !isResumeTransport) return;
    const pendingUploads = attachmentsToSend.filter((a) =>
      a.type !== "image" && a.type !== "video" && (!a.url || a.uploadStatus === "uploading")
    );
    if (pendingUploads.length > 0) {
      notifyError(t("chat.uploadStillRunning", "附件还在上传，请稍等一下"));
      return;
    }
    const failedUploads = attachmentsToSend.filter((a) => a.uploadStatus === "failed");
    if (failedUploads.length > 0) {
      notifyError(t("chat.uploadFailedRetry", "有附件上传失败，请重新选择或稍后重试"));
      return;
    }
    if (orgCommandPendingRef.current) return;

    const resolvedConvId = targetConvId || activeConvIdRef.current;
    const targetIsStreaming = resolvedConvId ? !!streamContexts.current.get(resolvedConvId)?.isStreaming : false;
    if (targetIsStreaming) return;

    if (resolvedConvId && isConvBusyOnOtherDevice(resolvedConvId)) return;

    // 斜杠命令处理
    if (!isResumeTransport && text.startsWith("/")) {
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

    // @org: 前缀或组织模式 — 统一交给 /api/chat 的组织 SSE 摘要路径。
    // 注意：这里不能再直接订阅全量 org:* WebSocket，否则聊天会泄露指挥台内部交互。
    const orgPrefixMatch = text.match(/^@org:(\S+?)(?:\/(\S+?))?\s+([\s\S]+)/);
    let orgRouteOverride: { orgId: string; nodeId: string | null; content: string } | null = null;
    if (orgPrefixMatch) {
      let targetOrgId = selectedOrgId;
      let targetNodeId = selectedOrgNodeId;
      let msgContent = text;
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
      if (targetOrgId) {
        orgRouteOverride = { orgId: targetOrgId, nodeId: targetNodeId || null, content: msgContent };
        setOrgMode(true);
        setSelectedOrgId(targetOrgId);
        setSelectedOrgNodeId(targetNodeId || null);
      }
    }

    const orgRouteActive = Boolean(orgRouteOverride || (orgMode && selectedOrgId));
    if (!isResumeTransport && endpoints.length === 0 && !orgRouteActive) {
      notifyError(t("chat.noChatEndpointConfigured"));
      return;
    }

    // 创建用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: "user",
      content: displayContent || text,
      attachments: attachmentsToSend.length > 0 ? attachmentsToSend.map(({ _uploadId, ...rest }) => rest) : undefined,
      timestamp: Date.now(),
    };

    // 创建流式助手消息占位
    let assistantMsg: ChatMessage = {
      id: genId(),
      role: "assistant",
      content: "",
      streaming: true,
      streamStatus: options?.initialStreamStatus ?? null,
      timestamp: Date.now(),
    };

    let convId = resolvedConvId;

    // Clear the composer for every send that draws on it (normal send,
    // regenerate, ask_user reply — all use the live composer attachments).
    // The ONLY sends that must leave the composer untouched are replays that
    // bring their own attachment set (``attachmentsOverride`` defined): the
    // queue drain and the steer-fallback. Wiping the composer there would
    // destroy a draft the user is typing for their next message.
    if (attachmentsOverride === undefined) {
      setInputValue("");
      setPendingAttachments([]);
    }
    setSlashOpen(false);
    if (!convId) {
      convId = genId();
      skipConvLoadRef.current = true;
      // React state updates asynchronously; update refs immediately so the
      // optimistic first turn renders before SSE/WebSocket events arrive.
      activateConversation(convId);
      setConversations((prev) => [{
        id: convId!,
        title: text.slice(0, 30) || "新对话",
        lastMessage: text,
        timestamp: Date.now(),
        messageCount: 1,
        status: "running",
        agentProfileId: selectedAgent,
        endpointId: selectedEndpoint !== "auto" ? selectedEndpoint : undefined,
        endpointPolicy: selectedEndpoint !== "auto" ? selectedEndpointPolicy : undefined,
        orgMode: Boolean(orgRouteOverride || (orgMode && selectedOrgId)),
        orgId: orgRouteOverride?.orgId || (orgMode && selectedOrgId ? selectedOrgId : undefined),
        orgNodeId: orgRouteOverride
          ? orgRouteOverride.nodeId || undefined
          : (orgMode && selectedOrgId ? selectedOrgNodeId || undefined : undefined),
      }, ...prev]);
    } else {
      updateConvStatus(convId, "running");
    }

    const thisConvId = convId!;

    // SSE 流式请求 (QueryGuard 保护并发)
    const guardHandle = queryGuard.startQuery(thisConvId);
    const abort = guardHandle.abort;

    // Build per-session StreamContext with initial messages
    const canUseRenderedMessages =
      shouldRenderConversationMessages(thisConvId, activeConvIdRef.current) &&
      displayedMessagesConvIdRef.current === thisConvId;
    const fallbackMessages = canUseRenderedMessages
      ? [...latestMessagesRef.current]
      : loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId);
    const reuseAssistantMessageId = options?.reuseAssistantMessageId;
    let initialMessages = [...fallbackMessages, ...(appendUserMessage ? [userMsg] : [])];
    if (reuseAssistantMessageId) {
      let reused = false;
      initialMessages = initialMessages.map((m) => {
        if (m.id !== reuseAssistantMessageId || m.role !== "assistant") return m;
        reused = true;
        assistantMsg = {
          ...m,
          streaming: true,
          streamStatus: options?.initialStreamStatus ?? m.streamStatus ?? null,
        };
        return assistantMsg;
      });
      if (!reused) initialMessages = [...initialMessages, assistantMsg];
    } else {
      initialMessages = [...initialMessages, assistantMsg];
    }
    const sctx: StreamContext = {
      abort,
      reader: null,
      isStreaming: true,
      userStopped: false,
      messages: initialMessages,
      activeSubAgents: [],
      subAgentTasks: [],
      isDelegating: false,
      pollingTimer: null,
      _hadError: false,
      mode: modeOverride ?? chatMode,
    };
    streamContexts.current.set(thisConvId, sctx);
    const isTargetConversationActive = () =>
      shouldRenderConversationMessages(thisConvId, activeConvIdRef.current);
    const renderTargetMessages = (nextMessages: ChatMessage[]) => {
      renderConversationMessages(thisConvId, nextMessages);
    };

    // Sending a turn in the visible conversation should reveal the latest messages
    // immediately. Background queued turns must not repaint the active chat.
    if (isTargetConversationActive()) {
      messageListRef.current?.forceFollow();
      isMessageListAtBottomRef.current = true;
    }
    if (isTargetConversationActive()) {
      renderTargetMessages(sctx.messages);
    } else {
      saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId, sctx.messages);
    }
    setStreamingTick(t => t + 1);

    // ── Per-session helpers: write to StreamContext, sync to screen only if active ──
    // StreamContext always gets the latest data immediately, while React state is
    // flushed at a bounded cadence. This protects typing from long SSE event bursts.
    //
    // 50ms ≈ 20fps: keep perceived streaming responsive. SSE events themselves
    // are still consumed in real time; only the visual flush is throttled.
    const SCREEN_FLUSH_MIN_MS = 50;
    let screenFlushRaf = 0;
    let screenFlushTimer: ReturnType<typeof setTimeout> | null = null;
    let lastScreenFlushAt = 0;
    const flushToScreen = () => {
      screenFlushRaf = 0;
      if (screenFlushTimer) {
        clearTimeout(screenFlushTimer);
        screenFlushTimer = null;
      }
      lastScreenFlushAt = Date.now();
      const c = streamContexts.current.get(thisConvId);
      if (c) renderTargetMessages(c.messages);
    };
    const scheduleScreenFlush = () => {
      if (screenFlushRaf || screenFlushTimer) return;
      const elapsed = Date.now() - lastScreenFlushAt;
      const delay = Math.max(0, SCREEN_FLUSH_MIN_MS - elapsed);
      screenFlushTimer = setTimeout(() => {
        screenFlushTimer = null;
        screenFlushRaf = requestAnimationFrame(flushToScreen);
      }, delay);
    };
    const updateMessages = (updater: (msgs: ChatMessage[]) => ChatMessage[]) => {
      const c = streamContexts.current.get(thisConvId);
      if (!c) return;
      c.messages = updater(c.messages);
      if (isTargetConversationActive()) {
        scheduleScreenFlush();
      }
    };
    const patchTargetMessages = (updater: (msgs: ChatMessage[]) => ChatMessage[]) => {
      const c = streamContexts.current.get(thisConvId);
      const baseMessages = c?.messages?.length
        ? c.messages
        : loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId);
      const updated = updater(baseMessages);
      if (c) c.messages = updated;
      try { saveMessagesToStorage(STORAGE_KEY_MSGS_PREFIX + thisConvId, updated); } catch { /* quota */ }
      renderTargetMessages(updated);
      return updated;
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
      // Recovery only fires when the stream was interrupted / incomplete, so the
      // local bubble's text is suspect. Flag it as a stream fallback up front:
      // any backend reconciliation that follows (recovery poll, cross-window
      // history patch, idle re-sync) will then prefer the persisted answer over
      // this partial copy, even if the backend text is shorter. See
      // ChatMessage.streamFallback and patchMessagesWithBackendDetailed.
      updateMessages((prev) =>
        prev.map((m) => (m.id === _recoverMsgId && !m.streamFallback ? { ...m, streamFallback: true } : m)),
      );
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
        if (sctx.userStopped) return;
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
            patchTargetMessages((prev) => {
              return prev.map((m) => {
                if (m.id !== _recoverMsgId) return m;
                // A stream-fallback bubble holds untrustworthy text (e.g. a
                // "connection failed" notice that may be *longer* than the real
                // answer), so the length guard would wrongly keep it. Adopt the
                // backend copy regardless of length and clear the flag; the guard
                // still applies once the bubble is reconciled.
                if (!m.streamFallback && m.content && !m.streaming && m.content.length >= contentLen) return m;
                const patched: ChatMessage = { ...m, content: lastAssistant.content, streaming: false, streamStatus: null, streamFallback: undefined };
                if (!m.thinkingChain || m.thinkingChain.length === 0) {
                  // Prefer the faithful timeline; fall back to the lossy summary.
                  if (Array.isArray(lastAssistant.chain_timeline) && lastAssistant.chain_timeline.length > 0) {
                    patched.thinkingChain = buildChainFromTimeline(lastAssistant.chain_timeline);
                  } else if (Array.isArray(lastAssistant.chain_summary) && lastAssistant.chain_summary.length > 0) {
                    patched.thinkingChain = buildChainFromSummary(lastAssistant.chain_summary);
                  }
                }
                return patched;
              });
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
      const effectiveOrgId = orgRouteOverride?.orgId || (orgMode && selectedOrgId ? selectedOrgId : null);
      const effectiveOrgNodeId = orgRouteOverride ? orgRouteOverride.nodeId : (orgMode && selectedOrgId ? selectedOrgNodeId : null);
      const body: Record<string, unknown> = {
        message: orgRouteOverride?.content || text,
        conversation_id: convId,
        mode: effectiveMode,
        plan_mode: effectiveMode === "plan",
        endpoint: selectedEndpoint === "auto" ? null : selectedEndpoint,
        endpoint_policy: selectedEndpoint === "auto" ? "prefer" : selectedEndpointPolicy,
        thinking_mode: thinkingMode !== "auto" ? thinkingMode : null,
        thinking_depth: thinkingMode !== "off" ? thinkingDepth : null,
        agent_profile_id: selectedAgent,
        org_mode: Boolean(effectiveOrgId),
        org_id: effectiveOrgId,
        org_node_id: effectiveOrgNodeId,
        client_id: getClientId(),
        ...(askUserReply ? { ask_user_reply: askUserReply } : {}),
      };

      // 附件信息
      if (attachmentsToSend.length > 0) {
        body.attachments = attachmentsToSend.map((a) => ({
          type: a.type,
          name: a.name,
          url: a.url,
          local_path: a.localPath,
          upload_id: a.uploadId,
          size: a.size,
          mime_type: a.mimeType,
        }));
      }

      resetIdleTimer(); // Start idle timer before fetch

      // Crash diagnostics: record desensitized task start before the SSE fetch.
      // Field set is intentionally narrow — no message content, no API keys.
      // Force-flush so a native WebView2 crash during/after the request still
      // leaves this breadcrumb on disk; logger.flush is internally re-entrant
      // safe and swallows IPC errors.
      logger.info("Chat", "task_started", {
        convId,
        mode: effectiveMode,
        endpoint: typeof selectedEndpoint === "string" ? selectedEndpoint : "auto",
        thinkingMode,
        thinkingDepth: thinkingMode !== "off" ? thinkingDepth : null,
        attachments: attachmentsToSend.length,
        textLen: text.length,
        orgMode: Boolean(orgMode && selectedOrgId),
      });
      void logger.flush();

      // 重要：一条全新的用户消息**不是**断点重连，绝不能带 Last-Event-ID。
      //
      // 后端的 SSE ringbuffer 是 per-conversation、seq 单调递增、且跨多个
      // turn 持续累积的。当上一轮 turn 的 SSE 在中途断开（切后台 / 切会话 /
      // 网络抖动），后端在 finally 里仍会把这一轮**剩余的事件（含最终答复）**
      // 继续 add_event 进 ringbuffer——但此时客户端已断开，看不到这些事件，
      // 于是本地 ``lastSeqByConv`` 停在断点处、落后于 buffer 真实 max。
      //
      // 如果此刻发新消息时把这个滞后的 seq 当 Last-Event-ID 传给后端，后端
      // ``replay_from(staleSeq)`` 会把上一轮缓冲的尾巴（旧的最终答复）瞬间
      // flush 进**这一轮**的流里，顶在新问题下面——也就是用户反馈的“跑完了，
      // 一问新问题就又把旧回复刷出来一遍”。seq 去重也救不了，因为客户端从未
      // 见过那些 seq。
      //
      // 真正的重连/补齐另有其路：流中途断了由 ``attemptRecovery`` 走 REST
      // history 轮询补齐；要挂载仍在跑的 turn 走 202 steered → GET
      // /api/chat/resume（用 since_seq，仅在**同一** turn 内 replay）。POST
      // /api/chat 永远是开新 turn，因此这里固定不带 Last-Event-ID。
      const _headers: Record<string, string> = { "Content-Type": "application/json" };
      let response = isResumeTransport
        ? await safeFetch(streamTransport!.url, {
            method: "GET",
            signal: abort.signal,
          })
        : await safeFetch(`${apiBase}/api/chat`, {
            method: "POST",
            headers: _headers,
            body: JSON.stringify(body),
            signal: abort.signal,
          });

      // 方案3 STEER: desktop 默认策略是 steer。当前端以为会话空闲、但后端
      // 的上一轮 turn 其实还在跑时（典型场景：SSE 断连或页面重载后丢了流），
      // 后端不再排队 6s 后报错，而是把这条消息注入到正在运行的 turn，并返回
      // HTTP 202（JSON，而非 SSE 流）。这里把 202 拦下来：
      //   - status=steered  → 消息已注入 → 改去 /api/chat/resume 挂载原始流，
      //                        让用户看到 Agent 读到新消息后的续写。
      //   - status=steer_failed → 旧任务恰好已结束、未注入 → 当作新消息重发一次。
      if (!isResumeTransport && response.status === 202) {
        let steerData: { status?: string } | null = null;
        try { steerData = await response.json(); } catch { /* 容错 */ }
        if (steerData?.status === "steered") {
          const sinceSeq = thisConvId ? (lastSeqByConv.current.get(thisConvId) ?? 0) : 0;
          const resumeUrl =
            `${apiBase}/api/chat/resume?conversation_id=${encodeURIComponent(thisConvId)}&since_seq=${sinceSeq}`;
          response = await safeFetch(resumeUrl, { method: "GET", signal: abort.signal });
          if (!response.ok) {
            // 后端没有可恢复的流。两种情况，必须区分，否则会话状态会卡住：
            //   1) 任务仍在跑，只是没有可挂载的 SSE writer（少见）→ 保持 running。
            //   2) 任务其实已经结束（重启 / 超时 GC / 刚好跑完）→ 必须置 idle，
            //      否则 UI 会永远显示“运行中”，输入框停在 steer 态无法发新消息。
            // 用 /api/chat/busy 查后端真实状态来决定，不再无条件 running。
            let stillBusy = false;
            try {
              const busyResp = await safeFetch(
                `${apiBase}/api/chat/busy?conversation_id=${encodeURIComponent(thisConvId)}`,
                { method: "GET", signal: abort.signal },
              );
              if (busyResp.ok) {
                const busyData = await busyResp.json().catch(() => null);
                stillBusy = Boolean(busyData?.busy);
              }
            } catch { /* 查询失败时保守按已结束处理，至少不卡 running */ }
            updateMessages((prev) => prev.map((m) =>
              m.id === assistantMsg.id
                ? {
                    ...m,
                    content: stillBusy
                      ? t("chat.steeredNoResume", "已加入当前任务的上下文，正在后台继续处理。")
                      : t("chat.steeredTaskEnded", "已加入当前任务的上下文，该任务已结束。"),
                    streaming: false,
                  }
                : m
            ));
            if (thisConvId) updateConvStatus(thisConvId, stillBusy ? "running" : "idle");
            return;
          }
          // response 现在是 resume 的 SSE 流 → 落入下方 reader 循环正常处理续写。
        } else {
          // steer_failed：作为全新消息重发一次（此时旧任务已结束，应能正常开流）。
          response = await safeFetch(`${apiBase}/api/chat`, {
            method: "POST",
            headers: _headers,
            body: JSON.stringify(body),
            signal: abort.signal,
          });
        }
      }

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
        let displayError = `错误：${response.status} 请求失败`;
        try {
          const contentType = response.headers.get("content-type") || "";
          if (contentType.includes("application/json")) {
            const data = await response.json().catch(() => null);
            const pickText = (value: unknown): string => {
              if (typeof value === "string") return value;
              if (value == null) return "";
              try { return JSON.stringify(value); } catch { return String(value); }
            };
            const message = pickText(data?.message || data?.detail || data?.error);
            const hint = pickText(data?.hint);
            displayError = [`错误：${response.status}`, message, hint].filter(Boolean).join("\n\n");
          } else {
            const errText = await response.text().catch(() => "请求失败");
            displayError = `错误：${response.status} ${errText}`;
          }
        } catch {
          // Keep the compact fallback above.
        }
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: displayError, streaming: false } : m
        ));
        if (thisConvId) updateConvStatus(thisConvId, "error");
        return;
      }

      // 收到响应头，重置空闲计时
      resetIdleTimer();

      // 处理 SSE 流
      const _initialReader = response.body?.getReader();
      if (!_initialReader) throw new Error("No response body");
      let reader = _initialReader;
      sctx.reader = reader;

      const sseMachine = new SseStateMachine();
      sseMachine.start();
      let currentContent = "";
      let currentThinking = "";
      let currentToolCalls: ChatToolCall[] = [];
      const currentToolCallsByKey = new Map<string, ChatToolCall>();
      const currentToolCallOrder: string[] = [];
      const syncCurrentToolCalls = () => {
        currentToolCalls = currentToolCallOrder
          .map((key) => currentToolCallsByKey.get(key))
          .filter((tc): tc is ChatToolCall => Boolean(tc));
      };
      const upsertToolCall = (key: string, tc: ChatToolCall) => {
        if (!currentToolCallsByKey.has(key)) currentToolCallOrder.push(key);
        currentToolCallsByKey.set(key, tc);
        syncCurrentToolCalls();
      };
      const findToolCallKey = (toolName: string, callId?: string) => {
        if (callId) {
          const byId = currentToolCallOrder.find((key) => currentToolCallsByKey.get(key)?.id === callId);
          if (byId) return byId;
        }
        for (let i = currentToolCallOrder.length - 1; i >= 0; i -= 1) {
          const key = currentToolCallOrder[i];
          const tc = currentToolCallsByKey.get(key);
          if (tc?.tool === toolName && tc.status === "running") return key;
        }
        return null;
      };
      let currentPlan: ChatTodo | null = null;
      let currentProgressEvents: ChatProgressEvent[] = [];
      let currentAsk: ChatAskUser | null = null;
      let currentAgent: string | null = null;
      let currentArtifacts: ChatArtifact[] = [];
      let currentAttachments: ChatAttachment[] = [];
      let currentOrgTimeline: OrgTimelineEntry[] = [];
      let currentSources: ChatSource[] = [];
      let currentMcpCalls: ChatMcpCall[] = [];
      let currentError: ChatErrorInfo | null = null;
      let gracefulDone = false; // SSE 正常发送了 "done" 事件
      let currentStreamStatus: string | null = options?.initialStreamStatus ?? null;
      const streamStartedAt = Date.now();
      let longWaitNoticeShown = false;
      const LONG_WAIT_NOTICE_MS = 15_000;
      const LONG_WAIT_NOTICE = t("chat.longWaitNotice", "本地模型还在生成，可能需要几十秒。");

      // 思维链: 分组数据
      let chainGroups: ChainGroup[] = [];
      let currentChainGroup: ChainGroup | null = null;
      let thinkingStartTime = 0;
      let currentThinkingContent = "";
      let pendingCompressedInfo: { beforeTokens: number; afterTokens: number } | null = null;
      let sseParseFailures = 0;
      let sawSecurityConfirm = false;
      const hasAssistantMessagePayload = () =>
        Boolean(
          currentContent ||
          currentThinking ||
          currentToolCalls.length > 0 ||
          currentPlan ||
          currentProgressEvents.length > 0 ||
          currentAsk ||
          currentArtifacts.length > 0 ||
          currentAttachments.length > 0 ||
          currentOrgTimeline.length > 0 ||
          currentSources.length > 0 ||
          currentMcpCalls.length > 0 ||
          currentError ||
          chainGroups.length > 0,
        );
      const hasRenderableStreamPayload = () => hasAssistantMessagePayload() || sawSecurityConfirm;

      // ── 断流后 live resume（复用现成 /api/chat/resume，不动后端）──
      // 一条 turn 的 SSE 中途断开（网络抖动 / 切后台 / 代理 idle 超时）时，后端
      // 的 Agent task 并未结束（DISCONNECT_GRACE=15min），事件仍在 per-turn
      // ringbuffer 里继续累积。与其直接降级成 attemptRecovery（REST 轮询最终答
      // 复、丢掉实时流），不如先用 GET /api/chat/resume 重新挂载实时流：把断点
      // 之后漏掉的事件按 ``since_seq`` 补齐，并继续实时续写。resume 现在受
      // turn-floor 保护，只会回放**本 turn** 内 seq>since 的事件，跨 turn 串回复
      // 已不可能；seq 去重（hasSeenSeq）再兜一层防重叠。返回 null 的两种情况都
      // 安全回落到 attemptRecovery：① resume 404（任务已结束 / GC / 后端重启，
      // 无可恢复流）；② 已是 graceful / 用户中止 / 重试用尽。
      let resumeAttempts = 0;
      const MAX_RESUME_ATTEMPTS = 3;
      const tryAttachLiveResume = async () => {
        if (!thisConvId || orgCommandPendingRef.current) return null;
        if (abort.signal.aborted || sctx.userStopped || gracefulDone) return null;
        if (resumeAttempts >= MAX_RESUME_ATTEMPTS) return null;
        resumeAttempts++;
        try {
          const sinceSeq = lastSeqByConv.current.get(thisConvId) ?? 0;
          const resp = await safeFetch(
            `${apiBase}/api/chat/resume?conversation_id=${encodeURIComponent(thisConvId)}&since_seq=${sinceSeq}`,
            { method: "GET", signal: abort.signal },
          );
          if (!resp.ok) return null;
          const r = resp.body?.getReader();
          if (!r) return null;
          logger.info("Chat", "sse_drop_live_resume", {
            convId: thisConvId,
            sinceSeq,
            attempt: resumeAttempts,
          });
          return r;
        } catch {
          return null;
        }
      };

      while (true) {
        // ── 1. 每次循环检查 abort 状态 ──
        if (abort.signal.aborted) break;

        let done: boolean;
        let value: Uint8Array | undefined;
        try {
          ({ done, value } = await reader.read());
        } catch (readErr) {
          // reader.read() 抛异常（abort 或网络错误）。先试 live resume 重挂实时
          // 流；挂上了就换 reader 继续读，挂不上再抛给外层 catch（→ attemptRecovery）。
          const resumed = await tryAttachLiveResume();
          if (resumed) {
            reader = resumed;
            sctx.reader = resumed;
            // Drop the dropped stream's partial frame + decoder state so a
            // multibyte char split across the break can't bleed into the new
            // stream. resume replays full frames from since_seq.
            sseMachine.resetStream();
            sseMachine.start();
            continue;
          }
          throw readErr;
        }

        let frames: SseFrame[] = [];
        if (value) {
          frames = sseMachine.push(value);
          resetIdleTimer(); // 收到数据，重置空闲计时
        }

        // ── 2. 再次检查 abort（read 可能返回 done:true 而非抛异常） ──
        if (abort.signal.aborted) break;

        if (done) {
          frames = [...frames, ...sseMachine.finish()];
        }

        // C17 Phase B.3：SSE 帧由 ``id: <seq>\ndata: {json}\n\n`` 组成。
        // 解析状态由 SseStateMachine 持有，所以 id/data 跨 chunk 时也能
        // 正确关联；空 id 或无 id 的老服务端帧继续走 no-op dedup。
        for (const frame of frames) {
          const data = frame.data.trim();
          if (data === "[DONE]") continue;
          const frameSeqRaw = frame.id != null ? Number.parseInt(frame.id.trim(), 10) : 0;
          const frameSeq = Number.isFinite(frameSeqRaw) && frameSeqRaw > 0 ? frameSeqRaw : 0;

          // Dedup before parsing: if we already processed this frame seq, skip.
          if (frameSeq > 0 && thisConvId && hasSeenSeq(thisConvId, frameSeq)) {
            continue;
          }
          try {
            const event: StreamEvent = JSON.parse(data);
            sseParseFailures = 0;
            if (frameSeq > 0 && thisConvId) {
              rememberSeq(thisConvId, frameSeq);
            }

            switch (event.type) {
              case "heartbeat":
                if (!currentContent && !longWaitNoticeShown && Date.now() - streamStartedAt >= LONG_WAIT_NOTICE_MS) {
                  longWaitNoticeShown = true;
                  currentStreamStatus = LONG_WAIT_NOTICE;
                  updateMessages((prev) => prev.map((m) =>
                    m.id === assistantMsg.id
                      ? { ...m, streamStatus: currentStreamStatus }
                      : m
                  ));
                }
                continue;
              case "org_command_started": {
                const orgId = (event as any).org_id as string | undefined;
                const commandId = (event as any).command_id as string | undefined;
                if (orgId && commandId) {
                  activeOrgCommandRef.current = { orgId, commandId };
                  orgCommandPendingRef.current = true;
                  setOrgCommandPending(true);
                }
                currentStreamStatus = t("chat.orgProcessing", "组织正在处理中...");
                // 把"命令已下发"写进 timeline 而不是 currentContent，
                // 这样组织的过程展示与最终回复彻底分离。
                currentOrgTimeline = [
                  ...currentOrgTimeline,
                  {
                    status: "started",
                    summary: t("chat.orgTimelineStartedFull", "组织命令已下发，等待节点接管…"),
                    timestamp: Date.now(),
                  },
                ];
                break;
              }
              case "org_progress": {
                const summary = ((event as any).summary || "") as string;
                const nodeId = ((event as any).node_id || "") as string;
                const category = ((event as any).category || (event as any).label || "") as string;
                if (summary) {
                  currentStreamStatus = null;
                  currentOrgTimeline = [
                    ...currentOrgTimeline,
                    {
                      status: "progress",
                      summary,
                      nodeId: nodeId || null,
                      category: category || null,
                      timestamp: Date.now(),
                    },
                  ];
                }
                break;
              }
              case "org_command_done": {
                activeOrgCommandRef.current = null;
                orgCommandPendingRef.current = false;
                setOrgCommandPending(false);
                currentOrgTimeline = [
                  ...currentOrgTimeline,
                  {
                    status: "done",
                    summary: t("chat.orgTimelineDoneFull", "组织命令已结束"),
                    timestamp: Date.now(),
                  },
                ];
                break;
              }
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
                thinkingStartTime = Date.now();
                currentThinkingContent = "";
                if (!currentChainGroup) {
                  currentChainGroup = { iteration: chainGroups.length + 1, entries: [], toolCalls: [], hasThinking: false, collapsed: false };
                  chainGroups = [...chainGroups, currentChainGroup];
                }
                break;
              case "thinking_delta":
                currentStreamStatus = null;
                currentThinking += event.content;
                currentThinkingContent += event.content;
                if (currentChainGroup) {
                  const grp: ChainGroup = currentChainGroup;
                  const entries = [...grp.entries];
                  if (entries.length > 0 && entries[entries.length - 1].kind === "thinking") {
                    entries[entries.length - 1] = { kind: "thinking", content: currentThinkingContent };
                  } else {
                    entries.push({ kind: "thinking", content: currentThinkingContent });
                  }
                  currentChainGroup = { ...grp, entries, hasThinking: true };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              case "thinking_end": {
                const _thinkDuration = event.duration_ms || (Date.now() - thinkingStartTime);
                const _hasThinking = event.has_thinking ?? (currentThinkingContent.length > 0);
                if (currentChainGroup) {
                  const prev: ChainGroup = currentChainGroup;
                  currentChainGroup = {
                    ...prev,
                    durationMs: _thinkDuration,
                    hasThinking: _hasThinking,
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              }
              case "chain_text":
                currentStreamStatus = null;
                if (!currentChainGroup) {
                  currentChainGroup = { iteration: chainGroups.length + 1, entries: [], toolCalls: [], hasThinking: false, collapsed: false };
                  chainGroups = [...chainGroups, currentChainGroup];
                }
                if (event.content) {
                  const grp: ChainGroup = currentChainGroup;
                  const entry: ChainEntry = event.icon
                    ? { kind: "text" as const, content: event.content, icon: event.icon }
                    : { kind: "text" as const, content: event.content };
                  currentChainGroup = {
                    ...grp,
                    entries: [...grp.entries, entry],
                  };
                  chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                }
                break;
              case "text_delta":
                currentStreamStatus = null;
                currentContent += event.content;
                break;
              case "text_replace":
                currentStreamStatus = null;
                currentContent = event.content ?? "";
                if (Array.isArray(event.attachments)) {
                  currentAttachments = event.attachments;
                }
                break;
              case "tool_intent_preview": {
                // C23 P2-3: tool_executor 在跑批之前先发这个事件，每个 tool_call
                // 一条，告知 approval_class。
                // 这里只对"有副作用"的类弹 toast：纯只读 / 搜索 / 交互问询不打扰
                // 用户。toast id 用 tool_use_id 让同一工具多次预览不会叠多个气泡。
                const previewClass = String(event.approval_class || "unknown");
                const noisyClasses = new Set([
                  "readonly_scoped",
                  "readonly_global",
                  "readonly_search",
                  "interactive",
                  "unknown",
                ]);
                if (!noisyClasses.has(previewClass)) {
                  const previewTool = String(event.tool_name || "");
                  const previewId = String(event.tool_use_id || `intent_${previewTool}_${Date.now()}`);
                  // 取一个 param 摘要给用户看（command / path / url 三选一），
                  // 避免把整个 params dump 进 toast。
                  const previewParams = (event.params || {}) as Record<string, unknown>;
                  const previewSummary =
                    (typeof previewParams.command === "string" && (previewParams.command as string)) ||
                    (typeof previewParams.path === "string" && (previewParams.path as string)) ||
                    (typeof previewParams.url === "string" && (previewParams.url as string)) ||
                    "";
                  const previewLabel = previewSummary
                    ? `${previewTool} · ${previewSummary.length > 80 ? previewSummary.slice(0, 80) + "…" : previewSummary}`
                    : previewTool;
                  toast.message(
                    t("chat.toolIntentPreview", "即将执行：{{label}}", { label: previewLabel }),
                    {
                      id: previewId,
                      duration: 2500,
                      description: t(
                        "chat.toolIntentPreviewClass",
                        "类型：{{cls}}",
                        { cls: previewClass },
                      ),
                    },
                  );
                }
                break;
              }
              case "tool_call_start": {
                currentStreamStatus = null;
                const toolName = event.tool_name || event.tool;
                const callId = event.call_id || event.id;
                if (toolName === "delegate_to_agent" && event.args?.agent_id) {
                  const targetId = String(event.args.agent_id);
                  // P5.1: the agent currently driving this stream is the parent.
                  if (currentAgent) parentAgentMapRef.current.set(targetId, currentAgent);
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
                      // P5.1: same parent inference as delegate_to_agent.
                      if (currentAgent) parentAgentMapRef.current.set(targetId, currentAgent);
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
                    multiAgentEnabled: "true",
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
                      .then((rawData: SubAgentTask[]) => {
                        if (!Array.isArray(rawData)) return;
                        const data = enrichTasksWithParents(rawData);
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
                  sctx.pollingTimer = setInterval(doFetch, 5000);
                }

                const _tcId = callId || genId();
                const toolCallKey = `${thisConvId}:${assistantMsg.id}:${_tcId}`;
                upsertToolCall(toolCallKey, { tool: toolName, args: event.args, status: "running", id: _tcId });
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
                currentStreamStatus = null;
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
                    .then((rawData: SubAgentTask[]) => {
                      if (!Array.isArray(rawData)) return;
                      const data = enrichTasksWithParents(rawData);
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
                const toolCallKey = findToolCallKey(toolName, callId);
                if (toolCallKey) {
                  const prev = currentToolCallsByKey.get(toolCallKey);
                  if (prev) upsertToolCall(toolCallKey, { ...prev, result: event.result, status: "done" as const });
                } else {
                  const fallbackId = callId || genId();
                  upsertToolCall(`${thisConvId}:${assistantMsg.id}:${fallbackId}`, {
                    id: fallbackId,
                    tool: toolName,
                    args: {},
                    result: event.result,
                    status: "done",
                  });
                }
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
              case "config_hint": {
                // Structured hint emitted right after tool_call_end when a
                // handler raised ToolConfigError. We MUST surface it in TWO
                // places:
                //
                //   1. ChatMessage.toolCalls[].configHints   — used by the
                //      legacy ToolCallsGroup render path (only active when
                //      thinkingChain is empty, e.g. some IM-imported messages).
                //   2. currentChainGroup.entries [config_hint kind] — used by
                //      ThinkingChain in the default UX where showChain=true.
                //      Without this branch, the card would silently disappear
                //      whenever the agent produced any thinkingChain — which
                //      is essentially every ReAct turn.
                //
                // The hint is intentionally NOT serialized into the LLM
                // context (backend strips ``_hint`` before tool_result
                // reaches working_messages) — it only exists in the UI state.
                const hintToolUseId = event.tool_use_id || "";
                const hintPayload = {
                  scope: event.scope,
                  error_code: event.error_code,
                  title: event.title,
                  message: event.message,
                  actions: event.actions,
                };
                // ── 1) legacy ChatToolCall.configHints ──
                // Skip the silent mass-attach risk: when both sides are empty
                // strings, ``"" === ""`` would match every id-less tool call.
                // In practice ``tool_id`` is always a UUID, but guard anyway.
                if (hintToolUseId) {
                  const toolCallKey = findToolCallKey("", hintToolUseId);
                  if (toolCallKey) {
                    const tc = currentToolCallsByKey.get(toolCallKey);
                    if (tc) {
                      const existing = tc.configHints || [];
                      upsertToolCall(toolCallKey, { ...tc, configHints: [...existing, hintPayload] });
                    }
                  }
                }
                // ── 2) thinkingChain entry — visible in the default UX ──
                // We append even if currentChainGroup is missing thinking;
                // the chain UI handles a "tool-only" group fine.
                if (!currentChainGroup) {
                  currentChainGroup = { iteration: chainGroups.length + 1, entries: [], toolCalls: [], hasThinking: false, collapsed: false };
                  chainGroups = [...chainGroups, currentChainGroup];
                }
                {
                  const grp: ChainGroup = currentChainGroup;
                  // De-dupe by (error_code, scope, title) within the same
                  // group — handlers that retry the same tool with the same
                  // failure shouldn't stack identical cards.
                  const dedupeKey = `${hintPayload.error_code}|${hintPayload.scope}|${hintPayload.title}`;
                  const alreadyShown = grp.entries.some(
                    (e) => e.kind === "config_hint" &&
                      `${e.hint.error_code}|${e.hint.scope}|${e.hint.title}` === dedupeKey,
                  );
                  if (!alreadyShown) {
                    currentChainGroup = {
                      ...grp,
                      entries: [
                        ...grp.entries,
                        { kind: "config_hint" as const, toolId: hintToolUseId, hint: hintPayload },
                      ],
                    };
                    chainGroups = chainGroups.map((g, i) => i === chainGroups.length - 1 ? currentChainGroup! : g);
                  }
                }
                break;
              }
              case "todo_created":
                currentPlan = event.plan;
                currentProgressEvents = [
                  ...currentProgressEvents,
                  { type: "todo_created", seq: currentProgressEvents.length + 1, plan: event.plan },
                ];
                updateMessages((prev) => prev.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, todo: { ...currentPlan! }, progressEvents: [...currentProgressEvents] }
                    : m.todo && m.todo.status !== "completed" && m.todo.status !== "failed" && m.todo.status !== "cancelled"
                    ? { ...m, todo: { ...m.todo, status: "completed" as const } }
                    : m
                ));
                break;
              case "todo_step_updated":
                if (currentPlan) {
                  const stepId = event.step_id || event.stepId;
                  currentProgressEvents = [
                    ...currentProgressEvents,
                    {
                      type: "todo_step_updated",
                      seq: currentProgressEvents.length + 1,
                      ...(stepId ? { stepId } : {}),
                      ...(event.stepIdx != null ? { stepIdx: event.stepIdx } : {}),
                      status: event.status as ChatTodoStep["status"],
                      ...(event.result !== undefined ? { result: event.result } : {}),
                    },
                  ];
                  const newSteps: ChatTodoStep[] = currentPlan.steps.map((s) => {
                    const matched = stepId
                      ? s.id === stepId
                      : event.stepIdx != null && currentPlan!.steps.indexOf(s) === event.stepIdx;
                    return matched
                      ? { ...s, status: event.status as ChatTodoStep["status"], result: event.result ?? s.result }
                      : s;
                  });
                  const allDone = newSteps.every((s) => s.status === "completed" || s.status === "skipped" || s.status === "failed");
                  currentPlan = { ...currentPlan, steps: newSteps, ...(allDone ? { status: "completed" as const } : {}) } as ChatTodo;
                  updateMessages((prev) => prev.map((m) =>
                    m.id === assistantMsg.id ? { ...m, todo: { ...currentPlan! }, progressEvents: [...currentProgressEvents] } : m
                  ));
                }
                break;
              case "todo_completed":
                if (currentPlan) {
                  currentProgressEvents = [
                    ...currentProgressEvents,
                    { type: "todo_completed", seq: currentProgressEvents.length + 1 },
                  ];
                  currentPlan = { ...currentPlan, status: "completed" } as ChatTodo;
                  updateMessages((prev) => prev.map((m) =>
                    m.id === assistantMsg.id ? { ...m, todo: { ...currentPlan! }, progressEvents: [...currentProgressEvents] } : m
                  ));
                }
                break;
              case "todo_cancelled":
                if (currentPlan) {
                  currentProgressEvents = [
                    ...currentProgressEvents,
                    { type: "todo_cancelled", seq: currentProgressEvents.length + 1 },
                  ];
                  currentPlan = { ...currentPlan, status: "cancelled" } as ChatTodo;
                  updateMessages((prev) => prev.map((m) =>
                    m.id === assistantMsg.id ? { ...m, todo: { ...currentPlan! }, progressEvents: [...currentProgressEvents] } : m
                  ));
                }
                break;
              case "plan_ready_for_approval":
                pendingApprovalRef.current = event.data as PlanApprovalEvent;
                break;
              case "security_confirm": {
                sawSecurityConfirm = true;
                const rawConfirm = event as unknown as Record<string, unknown>;
                const newConfirm = _securityConfirmFromBackend(rawConfirm);
                if (!newConfirm) break;
                const isRiskGateConfirm = newConfirm.source === "risk_gate";
                const isActiveConfirm = _isActiveSecurityConfirm(newConfirm);
                if (!isRiskGateConfirm && isActiveConfirm && securityPolicy.checkAutoAllow({
                  tool: newConfirm.tool,
                  args: newConfirm.args,
                  reason: newConfirm.reason,
                  risk_level: newConfirm.riskLevel,
                  needs_sandbox: newConfirm.needsSandbox,
                  id: newConfirm.toolId || "",
                })) {
                  securityPolicy.recordAllow(newConfirm.tool);
                  safeFetch(`${apiBaseUrl}/api/chat/security-confirm`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ confirm_id: newConfirm.toolId, decision: "allow_once" }),
                  })
                    .then((res) => res.json().catch(() => null))
                    .then((payload) => {
                      const nextRaw = _asRecord(_asRecord(payload).next_confirm);
                      if (Object.keys(nextRaw).length === 0) {
                        setSecurityQueueLen(_asFiniteCount(_asRecord(payload).queued_count));
                        return;
                      }
                      const next = _securityConfirmFromBackend(nextRaw);
                      if (!next) {
                        setSecurityQueueLen(_asFiniteCount(_asRecord(payload).queued_count));
                        return;
                      }
                      setSecurityQueueLen(next.queuedCount);
                      if (_isActiveSecurityConfirm(next)) setSecurityConfirm(next);
                    })
                    .catch(() => {});
                  break;
                }
                setSecurityQueueLen(newConfirm.queuedCount);
                if (_isActiveSecurityConfirm(newConfirm)) setSecurityConfirm(newConfirm);
                break;
              }
              case "death_switch": {
                setDeathSwitchActive(event.active);
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
                    kind: "normal",
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
                    kind: "normal",
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
                if (event.language) setLanguage(event.language as "auto" | "zh" | "en");
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
              case "source_used":
                currentSources = [...currentSources, {
                  tool_name: event.tool_name,
                  tool_use_id: event.tool_use_id,
                  requested_url: event.requested_url,
                  final_url: event.final_url,
                  hostname: event.hostname,
                  redirected: event.redirected,
                  from_cache: event.from_cache,
                  status: event.status,
                  hint: event.hint,
                }];
                break;
              case "mcp_call":
                currentMcpCalls = [...currentMcpCalls, {
                  tool_use_id: event.tool_use_id,
                  server: event.server,
                  tool: event.tool,
                  status: event.status,
                  auto_connected: event.auto_connected,
                  reconnected: event.reconnected,
                  error: event.error,
                }];
                break;
              case "agent_handoff": {
                // P5.1: capture delegation parentage so SubAgentCards can render
                // a tree.  We only record when the from_agent is non-empty,
                // letting unknown roots fall through as top-level nodes.
                if (event.from_agent && event.to_agent) {
                  parentAgentMapRef.current.set(String(event.to_agent), String(event.from_agent));
                }
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
                    attachments: currentAttachments.length > 0 ? [...currentAttachments] : null,
                    sources: currentSources.length > 0 ? [...currentSources] : null,
                    mcpCalls: currentMcpCalls.length > 0 ? [...currentMcpCalls] : null,
                    thinkingChain: chainGroups.length > 0 ? chainGroups.map(g => ({ ...g })) : null,
                    orgTimeline: currentOrgTimeline.length > 0 ? currentOrgTimeline.map(e => ({ ...e })) : null,
                    streaming: true,
                  }];
                });
                continue; // skip normal update below
              case "endpoint_notice": {
                // 渲染为系统气泡：thinking_degraded / vision_degraded
                const reasonCode: string =
                  (event as any).reason_code || (event as any).notice_type || "";
                const endpointName: string = (event as any).endpoint || "";
                let noticeText = "";
                if (reasonCode === "thinking_degraded") {
                  noticeText = endpointName
                    ? `当前模型「${endpointName}」未返回思考过程，已自动降级为非思考模式继续回答。`
                    : "当前模型未返回思考过程，已自动降级为非思考模式继续回答。";
                } else if (reasonCode === "endpoint_failover") {
                  const fromEndpoint = String((event as any).from_endpoint || "");
                  noticeText = fromEndpoint && endpointName
                    ? `所选模型「${fromEndpoint}」本轮请求失败，已切换到「${endpointName}」继续回答。`
                    : "所选模型本轮请求失败，已自动切换到可用模型继续回答。";
                } else if (reasonCode === "vision_degraded") {
                  noticeText = endpointName
                    ? `当前选中的模型「${endpointName}」不支持视觉，本轮的图片已被隐藏，仅根据文字内容回答。`
                    : "当前选中的模型不支持视觉，本轮的图片已被隐藏，仅根据文字内容回答。";
                } else {
                  noticeText = "端点能力降级提示";
                }
                updateMessages((prev) => [
                  ...prev,
                  {
                    id: genId(),
                    role: "system" as const,
                    content: noticeText,
                    timestamp: Date.now(),
                  },
                ]);
                continue;
              }
              case "task_checkpoint": {
                // 任务节点检查点：在 cancelled / budget_paused / completed /
                // user_cancelled / loop_terminated / max_iterations 时由后端 emit。
                //
                // 渲染策略（避免与 budget_warning / done / error 文案重复）：
                //   - user_cancelled                 → "已停止 + 继续提示"
                //   - loop_terminated / max_iterations → 失败原因卡片（P5.3）
                //   - budget_paused / completed      → 静默（已被 budget_warning / done 覆盖）
                //   - 其他未知 exit_reason            → 静默累积，未来时间线 UI 使用
                const exitReason = String((event as any).exit_reason || "");
                const summary = String((event as any).summary || "").trim();
                const hint = String((event as any).next_step_hint || "").trim();
                const renderSystemMessage = (lines: string[]) => {
                  updateMessages((prev) => [
                    ...prev,
                    {
                      id: genId(),
                      role: "system" as const,
                      content: lines.join("\n"),
                      timestamp: Date.now(),
                    },
                  ]);
                };
                if (exitReason === "user_cancelled" || exitReason === "cancelled") {
                  const lines = ["⏸️ 任务已停止"];
                  if (summary) lines.push(`已完成：${summary}`);
                  if (hint) lines.push(`继续提示：${hint}`);
                  else lines.push("如需继续，回复\"继续\"即可让我接力。");
                  renderSystemMessage(lines);
                } else if (exitReason === "loop_terminated") {
                  // P5.3 失败原因卡片：循环检测 / 工具预算 / token 异常等异常终止。
                  // 与 ErrorCard 区分 — ErrorCard 处理 LLM 调用层错误，这里是
                  // 任务级"为什么停下来"的归因。
                  const lines = ["⚠️ 任务异常终止"];
                  if (summary) lines.push(`原因：${summary}`);
                  if (hint) lines.push(`建议：${hint}`);
                  renderSystemMessage(lines);
                } else if (exitReason === "max_iterations") {
                  const lines = ["⚠️ 已达迭代上限"];
                  if (summary) lines.push(`原因：${summary}`);
                  if (hint) lines.push(`建议：${hint}`);
                  renderSystemMessage(lines);
                }
                continue;
              }
              case "budget_warning": {
                // 任务预算软提示：duration / tokens / iterations / tool_calls 维度
                // 在 80% / 90% 触达时由后端发来，仅供 UI 展示，不影响任务运行。
                // 后端已做去抖（每个 dim+level 仅 emit 一次），前端无需再过滤。
                const dimension = String((event as any).dimension || "");
                const level = String((event as any).level || "warning");
                const ratio = Number((event as any).usage_ratio || 0);
                const renewed = Boolean((event as any).renewed || false);
                const pct = Math.round(ratio * 100);
                let dimLabel = dimension;
                if (dimension === "duration") dimLabel = "任务时长";
                else if (dimension === "tokens") dimLabel = "Token 用量";
                else if (dimension === "tool_calls") dimLabel = "工具调用次数";
                else if (dimension === "iterations") dimLabel = "迭代次数";
                else if (dimension === "cost_usd" || dimension === "cost") dimLabel = "成本";
                let noticeText = "";
                if (renewed) {
                  noticeText =
                    `${dimLabel}已超出预算（${pct}%），但任务仍在持续产出工具调用 / token，` +
                    `系统已允许它继续推进。如需停止任务，可点击下方"停止"按钮。`;
                } else if (level === "downgrade") {
                  noticeText =
                    `${dimLabel}已用至 ${pct}%，接近你设置的上限。任务仍在继续，之后可能会暂停等待你确认。` +
                    `如需让任务自然结束，可主动让我"基于已有进展给出阶段性结论"。`;
                } else {
                  noticeText =
                    `${dimLabel}已用至 ${pct}%。任务仍在继续，无需操作；` +
                    `如已经获得足够信息，可直接告诉我"基于已有进展收尾"。`;
                }
                updateMessages((prev) => [
                  ...prev,
                  {
                    id: genId(),
                    role: "system" as const,
                    content: noticeText,
                    timestamp: Date.now(),
                  },
                ]);
                continue;
              }
              case "error":
                currentError = {
                  message: event.message,
                  category: classifyError(event.message),
                  raw: event.message,
                };
                break;
              case "done":
                gracefulDone = true;
                // Crash diagnostics: backend signalled end-of-stream. Only metadata,
                // never content. Use info level so it survives in production builds.
                logger.info("Chat", "sse_done", {
                  convId,
                  iters: chainGroups.length,
                  tools: currentToolCalls.length,
                  artifacts: currentArtifacts.length,
                  sources: currentSources.length,
                  mcp: currentMcpCalls.length,
                  contentLen: currentContent.length,
                  thinkingLen: currentThinking.length,
                  hasAskUser: currentAsk !== null,
                  hasError: currentError !== null,
                });
                void logger.flush();
                if (event.usage) {
                  // Fix-13：后端同时下发新旧字段，优先读取语义更清晰的新名字。
                  const ctxTokens = event.usage.history_context_tokens ?? event.usage.context_tokens;
                  const ctxLimit = event.usage.history_context_limit ?? event.usage.context_limit;
                  if (isTargetConversationActive()) {
                    if (typeof ctxTokens === "number") setContextTokens(Math.max(0, ctxTokens));
                    if (typeof ctxLimit === "number" && ctxLimit > 0) setContextLimit(ctxLimit);
                  }
                  const isEstimatedUsage = Boolean(event.usage.usage_estimated);
                  const inTokens = isEstimatedUsage ? event.usage.input_tokens : (event.usage.billable_input_tokens ?? event.usage.input_tokens);
                  const outTokens = isEstimatedUsage ? event.usage.output_tokens : (event.usage.billable_output_tokens ?? event.usage.output_tokens);
                  const totalTokens = isEstimatedUsage ? event.usage.total_tokens : (event.usage.billable_total_tokens ?? event.usage.total_tokens);
                  if (typeof inTokens === "number" && typeof outTokens === "number") {
                    assistantMsg.usage = {
                      input_tokens: inTokens,
                      output_tokens: outTokens,
                      total_tokens: totalTokens ?? inTokens + outTokens,
                      usage_estimated: isEstimatedUsage,
                      usage_source: event.usage.usage_source,
                    };
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
                    progressEvents: currentProgressEvents.length > 0 ? [...currentProgressEvents] : null,
                    askUser: currentAsk,
                    errorInfo: currentError,
                    artifacts: currentArtifacts.length > 0 ? [...currentArtifacts] : null,
                    attachments: currentAttachments.length > 0 ? [...currentAttachments] : null,
                    sources: currentSources.length > 0 ? [...currentSources] : null,
                    mcpCalls: currentMcpCalls.length > 0 ? [...currentMcpCalls] : null,
                    thinkingChain: chainGroups.length > 0 ? chainGroups.map(g => ({ ...g })) : null,
                    orgTimeline: currentOrgTimeline.length > 0 ? currentOrgTimeline.map(e => ({ ...e })) : null,
                    usage: assistantMsg.usage ?? m.usage,
                    streaming: event.type !== "done",
                    streamStatus: event.type === "done" ? null : currentStreamStatus,
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

        if (done) {
          // 流自然 EOF。若是 graceful（已收到 done 事件）/ 用户中止，
          // tryAttachLiveResume 会同步返回 null → 正常 break（零额外开销）。
          // 否则视为中途断开，尝试 live resume 续流；挂不上再 break 落到
          // 循环结束后的恢复逻辑（attemptRecovery）。
          const resumed = await tryAttachLiveResume();
          if (resumed) {
            reader = resumed;
            sctx.reader = resumed;
            sseMachine.resetStream();
            sseMachine.start();
            continue;
          }
          break;
        }
      }

      // ── 循环结束后：判断是正常完成还是被用户中止 ──
      if (abort.signal.aborted) {
        if (sctx.userStopped) {
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: m.content || "（已中止）", streaming: false, streamStatus: null }
              : m
          ));
        } else {
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, streaming: false, streamStatus: null } : m
          ));
          attemptRecovery(4000);
        }
      } else {
        const emptyStream = !hasRenderableStreamPayload();
        const canRecover = emptyStream && !!convId;
        const securityConfirmOnly = sawSecurityConfirm && !hasAssistantMessagePayload();
        if (securityConfirmOnly) {
          updateMessages((prev) => prev.filter((m) => m.id !== assistantMsg.id));
        } else {
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id
              ? {
                  ...m,
                  content: canRecover
                    ? ""
                    : (m.content || (m.askUser || !emptyStream ? "" : "未收到有效回复，请重试。")),
                  streaming: canRecover,
                  streamStatus: canRecover ? t("chat.recovering", "正在恢复回复...") : null,
                }
              : m
          ));
        }

        logger.info("Chat", "task_completed", {
          convId,
          gracefulDone,
          durationMs: Date.now() - streamStartedAt,
          contentLen: currentContent.length,
          tools: currentToolCalls.length,
          iters: chainGroups.length,
          artifacts: currentArtifacts.length,
          hasAskUser: currentAsk !== null,
          sawSecurityConfirm,
          securityConfirmOnly,
        });
        void logger.flush();

        if (securityConfirmOnly) {
          // A RiskGate confirmation is rendered as the global security modal, not
          // as chat content. The empty assistant placeholder must not recover or
          // turn into the generic "no valid response" fallback.
        } else if (canRecover) {
          attemptRecovery(2000);
          const _fallbackMsgId = assistantMsg.id;
          setTimeout(() => {
            patchTargetMessages((prev) => {
              return prev.map((m) => {
                if (m.id !== _fallbackMsgId) return m;
                if (m.content && !m.streaming) return m;
                return { ...m, content: "未收到有效回复，请重试。", streaming: false, streamStatus: null };
              });
            });
          }, 30_000);
        } else if (!gracefulDone && convId) {
          attemptRecovery(3000);
        } else if (gracefulDone && convId) {
          // SSE 正常完成后也静默校验一次后端历史。桌面端恢复前台或
          // fetch 流边界异常时，UI 可能已经显示了半截 Markdown 表格；
          // 后端 session history 是最终落盘结果，用它补齐更完整的回答。
          safeFetch(`${apiBase}/api/sessions/${encodeURIComponent(convId)}/history`)
            .then((r) => r.json())
            .then((data) => {
              const rows = Array.isArray(data?.messages) ? data.messages : [];
              if (!rows.length) return;
              patchTargetMessages((prev) => {
                const patchResult = patchMessagesWithBackendDetailed(prev, rows);
                const patched = patchResult.messages;
                const noop = !patchResult.changed;
                const assistant = prev.find((m) => m.id === assistantMsg.id);
                logger.info("Chat", "history_patch", {
                  convId,
                  rows: rows.length,
                  applied: !noop,
                  fallback: patchResult.stats.matchedByFallback,
                  byId: patchResult.stats.matchedById,
                  byHistoryIndex: patchResult.stats.matchedByHistoryIndex,
                  patched: patchResult.stats.patched,
                  localAssistantEmpty: Boolean(assistant && !assistant.content && !assistant.askUser),
                });
                if (noop) return prev;
                return patched;
              });
            })
            .catch(() => {});
        }
      }
    } catch (e: unknown) {
      sctx._hadError = true;
      if (sctx.userStopped) {
        updateMessages((prev) => prev.map((m) =>
          m.id === assistantMsg.id ? { ...m, content: m.content || "（已中止）", streaming: false, streamStatus: null } : m
        ));
      } else {
        const isAbortLike =
          abort.signal.aborted ||
          (e instanceof DOMException && e.name === "AbortError") ||
          (e instanceof Error && e.name === "AbortError");

        if (isAbortLike) {
          updateMessages((prev) => prev.map((m) =>
            m.id === assistantMsg.id ? { ...m, streaming: false, streamStatus: null } : m
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
      if (screenFlushTimer) { clearTimeout(screenFlushTimer); screenFlushTimer = null; }
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
              .then((rawData: SubAgentTask[]) => {
                if (!Array.isArray(rawData)) return;
                const data = enrichTasksWithParents(rawData);
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
          let finalPollingTimer: ReturnType<typeof setInterval> | null = setInterval(doFetch, 5000);
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
          renderTargetMessages(ctx.messages);
        }
        streamContexts.current.delete(thisConvId);
      }
      queryGuard.endQuery(guardHandle.generation, thisConvId);
      setStreamingTick(t => t + 1);

      const finalStatus = sctx._hadError ? "error" : "completed";
      const messageCountIncrement = appendUserMessage
        ? 2
        : (options?.countAssistantMessage === false ? 0 : 1);
      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === thisConvId
            ? {
                ...c,
                lastMessage: appendUserMessage ? text.slice(0, 60) : (c.lastMessage || text.slice(0, 60)),
                timestamp: Date.now(),
                messageCount: (c.messageCount || 0) + messageCountIncrement,
                status: finalStatus as ConversationStatus,
              }
            : c
        );
        const conv = updated.find((c) => c.id === thisConvId);
        if (appendUserMessage && conv && !conv.titleGenerated && (conv.messageCount || 0) <= 2) {
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
  }, [pendingAttachments, isCurrentConvStreaming, activeConvId, chatMode, selectedEndpoint, selectedEndpointPolicy, apiBase, slashCommands, endpoints.length, thinkingMode, thinkingDepth, t, setInputValue]);

  const startupReattachDoneRef = useRef(false);
  useEffect(() => {
    if (!serviceRunning || startupReattachDoneRef.current) return;
    startupReattachDoneRef.current = true;
    let cancelled = false;

    const pickReusableAssistantId = (convId: string): string | undefined => {
      const renderedMessages =
        displayedMessagesConvIdRef.current === convId ? latestMessagesRef.current : [];
      const storedMessages = loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + convId);
      const baseMessages = renderedMessages.length >= storedMessages.length ? renderedMessages : storedMessages;
      for (let i = baseMessages.length - 1; i >= 0; i -= 1) {
        const msg = baseMessages[i];
        if (msg.role === "assistant" && (msg.streaming || msg.streamStatus)) return msg.id;
      }
      return undefined;
    };

    const toResumeUrl = (convId: string) => {
      const sinceSeq = lastSeqByConv.current.get(convId) ?? 0;
      return `${apiBase}/api/chat/resume?conversation_id=${encodeURIComponent(convId)}&since_seq=${sinceSeq}`;
    };

    (async () => {
      try {
        const res = await safeFetch(`${apiBase}/api/chat/busy`, {
          method: "GET",
          signal: AbortSignal.timeout(5000),
        });
        const data = await res.json().catch(() => null);
        if (cancelled) return;

        const myClientId = getClientId();
        const localIds = new Set(latestConversationsRef.current.map((c) => c.id));
        const busyItems = (
          Array.isArray(data?.busy_conversations) ? data.busy_conversations : []
        ) as { conversation_id?: string; client_id?: string }[];
        const targets = busyItems
          .map((item) => ({
            convId: String(item.conversation_id || ""),
            clientId: String(item.client_id || ""),
          }))
          .filter(({ convId, clientId }) =>
            convId &&
            localIds.has(convId) &&
            !streamContexts.current.get(convId)?.isStreaming &&
            (!clientId || clientId === myClientId)
          );

        if (targets.length === 0) return;
        logger.info("Chat", "startup_resume_attach", {
          count: targets.length,
          conversations: targets.map((tgt) => tgt.convId),
        });

        for (const { convId } of targets) {
          if (cancelled) break;
          updateConvStatus(convId, "running");
          void sendMessage(
            t("chat.resumeOnStartupMessage", "正在重新连接运行中的任务"),
            convId,
            undefined,
            "agent",
            [],
            undefined,
            {
              appendUserMessage: false,
              countAssistantMessage: false,
              initialStreamStatus: t("chat.resumeOnStartup", "正在重新连接运行中的任务..."),
              reuseAssistantMessageId: pickReusableAssistantId(convId),
              streamTransport: {
                kind: "resume",
                url: toResumeUrl(convId),
              },
            },
          );
        }
      } catch {
        // Best-effort startup recovery. The detached-running poll and stale
        // recovery effects will still reconcile visible sessions.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [apiBase, getClientId, sendMessage, serviceRunning, t, updateConvStatus, STORAGE_KEY_MSGS_PREFIX]);

  useEffect(() => {
    const toAbsoluteApiUrl = (rawUrl: string) => {
      try {
        return new URL(rawUrl, apiBase).toString();
      } catch {
        return rawUrl.startsWith("/") ? `${apiBase}${rawUrl}` : `${apiBase}/${rawUrl}`;
      }
    };

    securityExecutionStarterRef.current = (info: SecurityCloseInfo) => {
      if (info.execution?.client_action !== "connect_resume") return false;
      const convId = info.conversationId || activeConvIdRef.current || "";
      if (!convId) return false;
      const resumeUrl = info.execution?.resume_url;
      if (!resumeUrl) {
        if (info.uiMessage) appendBackendSystemMessage(convId, info.uiMessage);
        return true;
      }
      setConversations((prev) =>
        prev.map((c) => c.id === convId ? { ...c, status: "running", timestamp: Date.now() } : c),
      );
      void sendMessage(
        info.originalMessage || t("chat.riskGateAuthorizedReplay", "RiskGate 已授权的高风险操作"),
        convId,
        undefined,
        "agent",
        [],
        undefined,
        {
          appendUserMessage: false,
          initialStreamStatus: info.uiMessage || t("chat.riskGateContinuing", "RiskGate 确认已通过，正在继续执行..."),
          streamTransport: {
            kind: "resume",
            url: toAbsoluteApiUrl(resumeUrl),
          },
        },
      );
      return true;
    };

    return () => {
      securityExecutionStarterRef.current = () => false;
    };
  }, [apiBase, appendBackendSystemMessage, sendMessage, setConversations, t]);

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
    // reason_stream 在 ask_user 后中断流，用户回复通过新 /api/chat 请求继续处理。
    // 标记为结构化 ask_user 回复，避免按钮 id 被后端当作新的用户意图或 RiskGate 授权。
    sendMessage(
      answer,
      undefined,
      displayText !== answer ? displayText : undefined,
      isPlanSwitch ? "plan" : undefined,
      undefined,
      { kind: "normal", message_id: msgId, answer },
    );
  }, [sendMessage, renderConversationMessages]);

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
    queryGuard.cancel(id);
  }, [queryGuard]);

  // ── 消息排队系统 ──
  const [messageQueue, setMessageQueue] = useState<QueuedMessage[]>([]);
  // Mirror of messageQueue for synchronous reads inside async callbacks (e.g.
  // the busy-probe auto-dequeue): the effect closure's ``messageQueue`` goes
  // stale across an await, so we re-check liveness against this ref instead.
  const messageQueueRef = useRef<QueuedMessage[]>(messageQueue);
  useEffect(() => { messageQueueRef.current = messageQueue; }, [messageQueue]);
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
    if (orgCommandPendingRef.current && activeOrgCommandRef.current) {
      const { orgId, commandId } = activeOrgCommandRef.current;
      safeFetch(`${apiBaseRef.current}/api/v2/orgs/${orgId}/commands/${commandId}/cancel`, {
        method: "POST",
      }).catch(() => {
        notifyError("组织命令停止请求失败，请稍后重试");
      });
      return;
    }
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

  // Steer: hand a pure-text message to the running turn via /api/chat/insert.
  // We confirm delivery BEFORE echoing the user bubble, so there is never a
  // double bubble and never a silent loss:
  //   - insert accepted  → echo the user message in front of the live answer.
  //   - no active task / error → the turn already ended; resend as a brand-new
  //     turn (after the local SSE has wound down) instead of dropping it.
  const handleInsertMessage = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const convId = activeConvIdRef.current;
    // The running turn's mode, captured now — used only if we end up parking
    // the message as a fresh turn (see the queue fallback below).
    const steerMode = convId ? streamContexts.current.get(convId)?.mode : undefined;

    const echoOptimistic = () => {
      const inserter = (prev: ChatMessage[]) => {
        const uMsg = { id: genId(), role: "user" as const, content: trimmed, timestamp: Date.now() };
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
      if (convId && ctx) {
        renderConversationMessages(convId, ctx.messages);
      }
      if (convId) {
        setConversations((prev) => prev.map((c) =>
          c.id === convId ? { ...c, messageCount: (c.messageCount || 0) + 1 } : c
        ));
      }
    };

    // Resend as a fresh turn once the local stream has actually closed.
    // The backend turn ended (that's why insert was rejected), but the
    // frontend SSE may take a beat to wind down; sending while the client
    // still thinks it is streaming would early-return and lose the message.
    let resendAttempts = 0;
    const resendAsFreshTurn = () => {
      const stillStreaming = convId ? !!streamContexts.current.get(convId)?.isStreaming : false;
      if (stillStreaming) {
        if (resendAttempts < 25) {
          resendAttempts += 1;
          setTimeout(resendAsFreshTurn, 200);
          return;
        }
        // The backend turn ended but the local SSE refuses to close after 5s.
        // sendMessage would early-return on the streaming guard and silently
        // drop the text, so instead park it in the local queue: it drains as
        // a fresh turn the moment the stream finally ends, and stays visible
        // to the user if it somehow never does. No silent loss.
        if (convId) {
          setMessageQueue(prev => [...prev, {
            id: genId(), text: trimmed, timestamp: Date.now(), convId, mode: steerMode,
          }]);
        }
        return;
      }
      // Steer is always text-only, so the resent turn must be too — pass an
      // explicit [] so it never picks up files the user staged in the
      // meantime.
      void sendMessage(trimmed, convId || undefined, undefined, undefined, []);
    };

    safeFetch(`${apiBaseRef.current}/api/chat/insert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: convId, message: trimmed }),
    })
      .then(async (res) => {
        let data: { status?: string; action?: string } | null = null;
        try { data = await res.json(); } catch { /* non-JSON → decide by HTTP status */ }
        const droppedNoTask =
          data?.status === "error" ||
          (data?.status === "warning" && data?.action === "insert");
        if (!res.ok || droppedNoTask) {
          resendAsFreshTurn();
        } else {
          // action may be "insert" (normal), "cancel"/"skip" (the text was a
          // stop/skip command) — in every accepted case echo the user bubble.
          echoOptimistic();
        }
      })
      .catch(() => {
        // Transport failure on localhost almost always means the request
        // never landed → resend instead of leaving the message stranded.
        resendAsFreshTurn();
      });
  }, [sendMessage]);

  // Queue a message to run as its own turn after the current one finishes.
  // Unlike steer, a queued message carries its own attachments and mode so
  // the drain can replay it faithfully (see the auto-dequeue effect below).
  const handleQueueMessage = useCallback(() => {
    const text = inputTextRef.current.trim();
    const attachments = pendingAttachments;
    if ((!text && attachments.length === 0) || !activeConvId) return;
    // Mirror sendMessage's upload guards: only fully-uploaded attachments may
    // enter the queue, otherwise the drain would reject them and drop the
    // message. Keeping them in the composer lets the user retry/wait.
    const pendingUploads = attachments.filter((a) =>
      a.type !== "image" && a.type !== "video" && (!a.url || a.uploadStatus === "uploading")
    );
    if (pendingUploads.length > 0) {
      notifyError(t("chat.uploadStillRunning", "附件还在上传，请稍等一下"));
      return;
    }
    if (attachments.some((a) => a.uploadStatus === "failed")) {
      notifyError(t("chat.uploadFailedRetry", "有附件上传失败，请重新选择或稍后重试"));
      return;
    }
    setMessageQueue(prev => [...prev, {
      id: genId(),
      text,
      timestamp: Date.now(),
      convId: activeConvId,
      attachments: attachments.length > 0 ? attachments.map(({ _uploadId, ...rest }) => rest) : undefined,
      mode: chatMode,
    }]);
    setInputValue("");
    setPendingAttachments([]);
  }, [activeConvId, setInputValue, pendingAttachments, chatMode, t]);

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
    if (!item) return;
    // A queued message with attachments cannot be steered into the running
    // turn (insert is text-only). Rather than silently dropping the files,
    // leave it queued — the auto-dequeue will replay it (attachments and all)
    // as its own turn the moment the current one finishes.
    if (item.attachments && item.attachments.length > 0) {
      notifyInfo(t("chat.queuedAttachmentDeferred", "含附件的消息会在当前任务结束后自动发送。"));
      return;
    }
    handleInsertMessage(item.text);
    setMessageQueue(prev => prev.filter(m => m.id !== id));
  }, [messageQueue, handleInsertMessage, t]);

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

  // Single decision point for "user submitted while the current turn is still
  // streaming". This keeps the Enter key, the send button, and any other entry
  // in lockstep so steer-vs-queue is decided in exactly one place:
  //   • empty composer        → drain this conversation's first queued item
  //   • attachments present    → queue (steer is text-only; can't carry files)
  //   • composer mode changed  → queue (user wants different behaviour now)
  //   • otherwise (plain text) → steer into the running turn immediately
  const submitWhileStreaming = useCallback(() => {
    const text = inputTextRef.current.trim();
    const hasAttachments = pendingAttachments.length > 0;

    if (!text && !hasAttachments) {
      const myFirst = messageQueue.find(m => m.convId === activeConvId);
      if (!myFirst) return;
      if (myFirst.attachments && myFirst.attachments.length > 0) {
        notifyInfo(t("chat.queuedAttachmentDeferred", "含附件的消息会在当前任务结束后自动发送。"));
        return;
      }
      setMessageQueue(prev => prev.filter(m => m.id !== myFirst.id));
      handleInsertMessage(myFirst.text);
      return;
    }

    const runningMode = activeConvId ? streamContexts.current.get(activeConvId)?.mode : undefined;
    const modeChanged = runningMode !== undefined && runningMode !== chatMode;
    if (hasAttachments || modeChanged) {
      handleQueueMessage();
    } else {
      handleInsertMessage(text);
      setInputValue("");
    }
  }, [pendingAttachments, messageQueue, activeConvId, chatMode, handleInsertMessage, handleQueueMessage, setInputValue, t]);

  // ── 排队消息自动出队 ──
  // 后端支持并发流式 — 每会话独立 Agent 实例。
  // 排队仅限同会话：某会话流结束时，出队该会话排队的下一条消息。
  //
  // 关键：``isStreaming`` 在 fetch 循环退出时**一律**被置 false（见流式
  // finally），其中也包括「turn 中途 SSE 断开」——此时后端的 Agent task 并没
  // 结束（DISCONNECT_GRACE 给了 15 分钟宽限，任务继续跑）。若此刻就把排队消息
  // 当作「新 turn」POST 出去，会撞上仍在运行的上一 turn，被后端 STEER/QUEUE
  // 重路由（改过模式的纯文本会被误并进上一 turn，丢掉用户切换的模式意图）。
  // 因此出队前先探一次 ``/api/chat/busy``：后端确实空闲了才发，仍忙就稍后再探。
  // 这与 openclaw「失序先和服务端真相对账、再行动」是同一思路。
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
          // Drain only once the backend reports this conversation idle. We do
          // NOT remove the item from the visible queue until it is actually
          // sent, so a still-running turn (e.g. after a mid-turn drop) keeps
          // the item parked in the queue UI instead of mis-firing it early.
          const drainWhenIdle = (attempt: number) => {
            safeFetch(
              `${apiBaseRef.current}/api/chat/busy?conversation_id=${encodeURIComponent(finishedId)}`,
            )
              .then((r) => (r.ok ? r.json() : null))
              .then((data) => {
                const busy = Boolean(data?.busy);
                // Up to ~30s of patience for a turn that is still settling after
                // a dropped stream; past that, fall through and let the backend's
                // own STEER/QUEUE policy arbitrate (same as the legacy behaviour).
                if (busy && attempt < 20) {
                  setTimeout(() => drainWhenIdle(attempt + 1), 1500);
                  return;
                }
                doSend();
              })
              // Probe failed (offline / backend down): don't strand the item —
              // fall back to the legacy "send anyway" behaviour.
              .catch(() => doSend());
          };
          const doSend = () => {
            // Re-check liveness against the ref (the closure's messageQueue is
            // stale after the busy-probe await): the user may have edited /
            // removed / manually sent this item during the wait. The removal
            // updater stays pure; sendMessage fires exactly once, outside it.
            if (!messageQueueRef.current.some((m) => m.id === next.id)) return;
            setMessageQueue((prev) => prev.filter((m) => m.id !== next.id));
            // Replay as a brand-new turn carrying the attachments and mode
            // captured at queue time (not the live composer state). Pass an
            // explicit [] when the queued item had no attachments, otherwise
            // sendMessage would fall back to whatever is staged in the composer
            // right now and attach the wrong files.
            sendMessage(next.text, next.convId, undefined, next.mode, next.attachments ?? []);
          };
          drainWhenIdle(0);
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
        uploadStatus: file.type.startsWith("image/") || file.type.startsWith("video/") ? "uploaded" : "uploading",
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
          .then((uploaded) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a._uploadId === uploadId
                ? {
                  ...a,
                  url: `${apiBaseRef.current}${uploaded.url}`,
                  localPath: uploaded.localPath,
                  uploadId: uploaded.uploadId,
                  size: uploaded.size ?? a.size,
                  mimeType: uploaded.mimeType ?? a.mimeType,
                  uploadStatus: "uploaded",
                  uploadError: undefined,
                } : a)
            );
          })
          .catch((err) => {
            notifyError(`文件上传失败: ${file.name}`);
            setPendingAttachments((prev) =>
              prev.map((a) => a._uploadId === uploadId
                ? { ...a, uploadStatus: "failed", uploadError: String(err) }
                : a)
            );
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
      mp3: "audio/mpeg", wav: "audio/wav", m4a: "audio/mp4",
      aac: "audio/aac", flac: "audio/flac", ogg: "audio/ogg", opus: "audio/opus",
      pdf: "application/pdf", txt: "text/plain", md: "text/plain",
      json: "application/json", csv: "text/csv",
    };

    const FILE_MAX_SIZE = 50 * 1024 * 1024;

    const dataUrlToBlob = (dataUrl: string, mimeType: string): Blob | null => {
      try {
        const commaIdx = dataUrl.indexOf(",");
        const b64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl;
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new Blob([bytes], { type: mimeType });
      } catch {
        return null;
      }
    };

    const handleDroppedPaths = (paths: string[]) => {
      for (const filePath of paths) {
        const name = filePath.split(/[\\/]/).pop() || "file";
        const ext = (name.split(".").pop() || "").toLowerCase();
        const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
        const isVideo = ["mp4", "webm", "avi", "mov", "mkv"].includes(ext);
        const isAudio = ["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "weba", "wma", "amr"].includes(ext);
        const mimeType = mimeMap[ext] || "application/octet-stream";
        readFileBase64(filePath)
          .then((dataUrl) => {
            if (cancelled) return;
            const commaIdx = dataUrl.indexOf(",");
            const base64Len = commaIdx >= 0 ? dataUrl.length - commaIdx - 1 : dataUrl.length;
            const estimatedSize = base64Len * 3 / 4;
            if (estimatedSize > FILE_MAX_SIZE) {
              notifyError(`文件过大 (${(estimatedSize / 1024 / 1024).toFixed(1)}MB)，最大支持 50MB`);
              return;
            }
            if (isVideo) {
              const VIDEO_MAX_SIZE = 7 * 1024 * 1024;
              if (estimatedSize > VIDEO_MAX_SIZE) {
                notifyError(`视频文件过大 (${(estimatedSize / 1024 / 1024).toFixed(1)}MB)，最大支持 7MB（base64 编码后需 < 10MB）`);
                return;
              }
            }

            // 图片 / 视频：保留 dataUrl，后端 multimodal 路径会直接消费（不会拼进文本 prompt）
            if (isImage || isVideo) {
              setPendingAttachments((prev) => [...prev, {
                type: isImage ? "image" : "video",
                name,
                previewUrl: isImage ? dataUrl : undefined,
                url: dataUrl,
                size: estimatedSize,
                mimeType,
              }]);
              return;
            }

            // 文档 / 其他文件：必须上传到后端拿短 URL，否则 base64 dataUrl 会被
            // 拼进 LLM prompt 文本 → token 爆炸 + 被中间环节截断（→ "..."）
            // → 模型反复说"找不到文件 / 内容被截断"。与 handleFileSelect 路径保持一致。
            const blob = dataUrlToBlob(dataUrl, mimeType);
            if (!blob) {
              notifyError(`文件解码失败: ${name}`);
              logger.error("Chat.Upload", "DragDrop dataUrl decode failed", { name });
              return;
            }
            const uploadId = genId();
            const isPdf = ext === "pdf" || mimeType === "application/pdf";
            const att: ChatAttachment = {
              type: isAudio ? "voice" : isPdf ? "document" : "file",
              name,
              size: estimatedSize,
              mimeType,
              uploadStatus: "uploading",
              _uploadId: uploadId,
            };
            setPendingAttachments((prev) => [...prev, att]);
            uploadFile(blob, name)
              .then((uploaded) => {
                if (cancelled) return;
                setPendingAttachments((prev) => prev.map((a) =>
                  a._uploadId === uploadId
                    ? {
                      ...a,
                      url: `${apiBaseRef.current}${uploaded.url}`,
                      localPath: uploaded.localPath,
                      uploadId: uploaded.uploadId,
                      size: uploaded.size ?? a.size,
                      mimeType: uploaded.mimeType ?? a.mimeType,
                      uploadStatus: "uploaded",
                      uploadError: undefined,
                    }
                    : a,
                ));
              })
              .catch((err) => {
                notifyError(`文件上传失败: ${name}`);
                logger.error("Chat.Upload", "DragDrop uploadFile failed", { name, error: String(err) });
                setPendingAttachments((prev) => prev.map((a) =>
                  a._uploadId === uploadId ? { ...a, uploadStatus: "failed", uploadError: String(err) } : a));
              });
          })
          .catch((err) => logger.error("Chat", "DragDrop read_file_base64 failed", { name, error: String(err) }));
      }
    };

    onDragDrop({
      onEnter: () => { if (!cancelled && !feedbackModalOpenRef.current) setDragOver(true); },
      onOver: () => { if (!cancelled && !feedbackModalOpenRef.current) setDragOver(true); },
      onLeave: () => { if (!cancelled) setDragOver(false); },
      onDrop: (paths) => {
        if (cancelled) return;
        setDragOver(false);
        if (feedbackModalOpenRef.current) return;
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
          size: blob.size, mimeType: mimeType || "audio/webm", uploadStatus: "uploading", _uploadId: uploadId,
        };
        setPendingAttachments((prev) => [...prev, tempAtt]);
        uploadFile(blob, filename)
          .then((uploaded) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a._uploadId === uploadId ? {
                ...a,
                url: `${apiBaseRef.current}${uploaded.url}`,
                localPath: uploaded.localPath,
                uploadId: uploaded.uploadId,
                size: uploaded.size ?? a.size,
                mimeType: uploaded.mimeType ?? a.mimeType,
                uploadStatus: "uploaded",
                uploadError: undefined,
              } : a)
            );
          })
          .catch((err) => {
            notifyError(t("chat.voiceUploadFailed", "语音上传失败"));
            setPendingAttachments((prev) => prev.map((a) =>
              a._uploadId === uploadId ? { ...a, uploadStatus: "failed", uploadError: String(err) } : a));
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
      // 当前会话正在流式传输（方案3：默认 steer，对齐 Claude Code 的“边跑边追加指令”）:
      //   Escape           = 停止生成（快捷键面板打开时让面板处理）
      //   Enter            = 提交（submitWhileStreaming 决定 steer / 排队）
      //   Ctrl/Cmd+Enter   = 强制排队（等本轮结束后作为新消息发送）
      // 是否 steer 还是排队由 submitWhileStreaming 统一裁决（附件 / 改模式 → 排队）。
      if (e.key === "Escape" && !shortcutsOpen) {
        e.preventDefault();
        handleCancelTask();
        return;
      }
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        handleQueueMessage();
      } else if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitWhileStreaming();
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
  }, [atAgentOpen, atAgentFilter, atAgentIdx, agentProfiles, slashOpen, slashFilter, slashCommands, slashSelectedIdx, sendMessage, isCurrentConvStreaming, submitWhileStreaming, handleQueueMessage, setInputValue, shortcutsOpen, handleCancelTask]);

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
        setSelectedOrgNodeId(null);
      }
    }

    // @agent 联想
    const cursor = e.target.selectionStart ?? val.length;
    const beforeCursor = val.slice(0, cursor);
    const atMatch = beforeCursor.match(/@(\w*)$/);
    if (atMatch && agentProfiles.length > 0) {
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
  }, [orgMode, orgList, agentProfiles.length, pushUndoSnapshot]);

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

  const quickStartItems = useMemo(() => [
    { id: "research", icon: <IconBarChart size={20} />, text: t("chat.quickStart.research", "帮我做一份 OpenAkita 竞品分析") },
    { id: "ppt", icon: <IconPlan size={20} />, text: t("chat.quickStart.ppt", "帮我生成一份项目汇报 PPT 大纲") },
    { id: "search", icon: <IconGlobe size={20} />, text: t("chat.quickStart.search", "帮我搜索 OpenAkita 的最新动态") },
    { id: "email", icon: <IconMail size={20} />, text: t("chat.quickStart.email", "帮我写一封商务邮件") },
    { id: "summary", icon: <IconClipboard size={20} />, text: t("chat.quickStart.summary", "帮我总结一下今天的工作内容") },
    { id: "translate", icon: <IconGlobe size={20} />, text: t("chat.quickStart.translate", "帮我把这段话翻译成英文") },
  ], [i18n.language, t]);
  const quickStartCardWidth = useMemo(() => {
    const textUnits = Math.max(
      ...quickStartItems.map((item) =>
        Array.from(item.text).reduce((total, char) => total + (char.charCodeAt(0) <= 0xff ? 0.55 : 1), 0)
      )
    );
    return `calc(${textUnits}em + 82px)`;
  }, [quickStartItems]);

  // 会话大纲条目：当前会话中所有用户提问（question），保留原始索引用于跳转
  const outlineItems = useMemo(
    () =>
      messages.reduce<{ id: string; index: number; text: string }[]>((acc, m, i) => {
        if (m.role === "user") {
          const text = (m.content || "").replace(/\s+/g, " ").trim();
          if (text) acc.push({ id: m.id, index: i, text });
        }
        return acc;
      }, []),
    [messages],
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
        onClick={() => { if (renamingId !== conv.id) activateConversation(conv.id); }}
        onContextMenu={(e) => { e.preventDefault(); (e.nativeEvent as any)._handled = true; setCtxMenu({ x: e.clientX, y: e.clientY, convId: conv.id }); }}
      >
        <div className="convItemIcon">
          <span title={agentProfile?.name || ""} style={{ fontSize: 16, display: "inline-flex", alignItems: "center" }}>{agentProfile?.icon || <IconMessageCircle size={16} />}</span>
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
            ? <span className="convStatusDot" style={{ color: "var(--warning, #eab308)", whiteSpace: "nowrap", display: "inline-flex", alignItems: "center" }} title={t("chat.busyOnOtherDevice")}><IconHourglass size={10} /></span>
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
      <div className="flex min-w-0 flex-1 flex-col" style={{ position: "relative" }} onMouseDown={() => { if (sidebarOpen && !sidebarPinned) setSidebarOpen(false); }}>
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
                      onClick={() => activateConversation(conv.id)}
                      onMouseEnter={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setOrbitTip({ x: rect.left + rect.width / 2, y: rect.bottom + 6, name: ap?.name || "Default", title: conv.title });
                      }}
                      onMouseLeave={() => setOrbitTip(null)}
                    >
                      <span className="agentOrbitIcon">
                        {ap?.icon || <IconMessageCircle size={16} />}
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
                    <span className="subAgentChipIcon">{sp?.icon ? <RenderIcon icon={sp.icon} size={14} /> : <IconBot size={14} />}</span>
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
            <IconAlertCircle size={14} />
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
              <div className="inline-grid max-w-full grid-cols-1 gap-3 sm:grid-cols-2">
                {quickStartItems.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setInputValue(item.text)}
                    className="quickStartCard"
                    style={{
                      display: "flex", alignItems: "center", gap: 10,
                      width: quickStartCardWidth, maxWidth: "100%",
                      padding: "14px 16px", borderRadius: 14,
                      border: "1px solid var(--line)", background: "var(--panel2)",
                      cursor: "pointer", textAlign: "left", fontSize: 13,
                      transition: "border-color 0.15s, background 0.15s",
                    }}
                  >
                    <span style={{ flexShrink: 0, display: "flex", alignItems: "center" }}>{item.icon}</span>
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
            conversationId={activeConvId || undefined}
            httpApiBase={() => apiBaseUrl}
            hasMoreBefore={historyPage.hasMoreBefore}
            loadingOlder={historyPage.loadingOlder}
            onLoadOlder={loadOlderMessages}
            onPlanStepAction={(action, stepIdx, description) => {
              const msg = action === "skip"
                ? `请跳过当前步骤（第 ${stepIdx + 1} 步：${description}），直接进入下一步。`
                : `请重试这一步（第 ${stepIdx + 1} 步：${description}）。`;
              setInputValue(msg);
            }}
            onAtBottomChange={(atBottom) => { isMessageListAtBottomRef.current = atBottom; }}
            onActiveUserMessageChange={outlineItems.length > 0 ? setActiveOutlineId : undefined}
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

        {/* 会话大纲 —— 右侧常驻迷你导航，默认折叠为短条，悬浮展开为文字列，点击跳转到对应聊天记录 */}
        {outlineItems.length > 0 && (
          <div className="chatOutline" aria-label={t("chat.outline", "会话大纲")}>
            <div className="chatOutlineList">
              {outlineItems.map((item) => (
                <button
                  key={item.id}
                  data-slot="outline"
                  className={`chatOutlineItem ${item.id === activeOutlineId ? "chatOutlineItemActive" : ""}`}
                  title={item.text}
                  onClick={() => {
                    setActiveOutlineId(item.id);
                    messageListRef.current?.scrollToIndex(item.index, "start");
                  }}
                >
                  <span className="chatOutlineBar" />
                  <span className="chatOutlineText">{item.text}</span>
                </button>
              ))}
            </div>
          </div>
        )}

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

        {/* Read-only protection banner */}
        {deathSwitchActive && (
          <div className="flex items-center gap-3 border-t border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm">
            <span style={{ fontSize: 16 }}>&#x1F6D1;</span>
            <span style={{ flex: 1, color: "var(--destructive, #ef4444)" }}>
              Agent 当前处于只读保护状态，写入操作已暂时暂停
            </span>
            <button
              onClick={() => {
                safeFetch(`${apiBase}/api/config/security/death-switch/reset`, { method: "POST" })
                  .then(() => {
                    setDeathSwitchActive(false);
                    setMessages((prev) => [...prev, { id: genId(), role: "system", content: "只读保护已解除，Agent 可以继续执行写入操作。", timestamp: Date.now() }]);
                  })
                  .catch(() => {});
              }}
              style={{ padding: "4px 12px", borderRadius: 6, border: "1px solid var(--destructive, #ef4444)", background: "var(--destructive, #ef4444)", color: "#fff", cursor: "pointer", fontSize: 12, whiteSpace: "nowrap" }}
            >解除只读</button>
          </div>
        )}

        {/* 长闲置回归提示 (6.7) */}
        {idleReturnPrompt && (
          <div className="flex items-center gap-3 border-t border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-sm">
            <IconClock size={16} />
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
                apiBaseUrl={apiBaseUrl}
                onRemove={() => setPendingAttachments((prev) => prev.filter((_, i) => i !== idx))}
              />
            ))}
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
            <IconPlug size={16} />
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
            <IconCheckCircle size={14} />
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
            <IconHourglass size={16} />
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
                    <span style={{ fontSize: 16, display: "inline-flex", alignItems: "center" }}>{a.icon || <IconBot size={16} />}</span>
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
                          {qm.text
                            ? (qm.text.length > 80 ? qm.text.slice(0, 80) + "..." : qm.text)
                            : (qm.attachments && qm.attachments.length > 0
                                ? `📎 ${qm.attachments.length}`
                                : "")}
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
                        const ap = agentProfiles.find(p => p.id === selectedAgent) || null;
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
                    onClick={() => { setSelectedEndpoint("auto"); setSelectedEndpointPolicy("prefer"); setModelMenuOpen(false); }}
                  >
                    {t("chat.selectModel")}
                  </div>
                  {endpoints.map((ep) => {
                    const hs = ep.health?.status;
                    const dotColor = hs === "healthy" ? "#22c55e" : hs === "degraded" ? "#eab308" : hs === "unhealthy" ? "#ef4444" : "#9ca3af";
                    return (
                      <div
                        key={ep.name}
                        className={`chatModelMenuItem ${selectedEndpoint === ep.name ? "chatModelMenuItemActive" : ""}`}
                        onClick={() => { setSelectedEndpoint(ep.name); setModelMenuOpen(false); }}
                      >
                        <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: dotColor, marginRight: 6 }} />
                        <span style={{ display: "inline-flex", alignItems: "center", marginRight: 6 }}>
                          <ProviderIcon slug={ep.provider} size={14} title={ep.provider} />
                        </span>
                        <span style={{ fontWeight: 600 }}>{ep.model}</span>
                        <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 6 }}>{ep.name}</span>
                      </div>
                    );
                  })}
                  {selectedEndpoint !== "auto" && (
                    <div className="px-3 py-2 border-t border-border/50 text-xs text-muted-foreground">
                      <div className="mb-1.5 font-medium text-foreground">
                        {selectedEndpointPolicy === "require"
                          ? t("chat.modelPolicyStrict", "严格使用此模型")
                          : t("chat.modelPolicyPrefer", "优先使用此模型")}
                      </div>
                      <button
                        type="button"
                        className="chatModelMenuItem"
                        style={{ width: "100%", justifyContent: "flex-start", padding: "6px 8px" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedEndpointPolicy((p) => (p === "require" ? "prefer" : "require"));
                        }}
                        title={selectedEndpointPolicy === "require"
                          ? t("chat.modelPolicyStrictHint", "当前模型不可用时直接提示失败，不自动切换。")
                          : t("chat.modelPolicyPreferHint", "当前模型不可用时允许自动切换到可用模型。")}
                      >
                        {selectedEndpointPolicy === "require"
                          ? t("chat.switchToPreferModel", "改为不可用时自动切换")
                          : t("chat.switchToStrictModel", "改为只用当前模型")}
                      </button>
                    </div>
                  )}
                </div>
              )}
              {agentProfiles.length > 0 && !orgMode && (
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
                          <IconTarget size={14} style={{ marginRight: 6 }} />
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
              {orgList.length > 0 && (
                <div ref={orgMenuRef} style={{ position: "relative", marginLeft: 8 }}>
                  <button
                    className="chatModelPickerBtn"
                    onClick={() => {
                      if (orgMode) {
                        setOrgMode(false);
                        setSelectedOrgId(null);
                        setSelectedOrgNodeId(null);
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
                            setSelectedOrgNodeId(null);
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
                          <span style={{ marginTop: 2, flexShrink: 0 }}>{m.icon}</span>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 600 }}>{m.label}</div>
                            <div style={{ fontSize: 11, opacity: 0.5, lineHeight: 1.3 }}>{m.desc}</div>
                          </div>
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
                          setThinkingDepth((d) => d === "low" ? "medium" : d === "medium" ? "high" : d === "high" ? "max" : "low");
                        }}
                        className="chatInputIconBtn"
                      >
                        <svg width="18" height="14" viewBox="0 0 18 14" fill="none" style={{ flexShrink: 0 }}>
                          <rect x="1" y="9" width="3" height="4" rx="0.5" fill="currentColor" opacity={thinkingDepth === "low" || thinkingDepth === "medium" || thinkingDepth === "high" || thinkingDepth === "max" ? 1 : 0.25} />
                          <rect x="5.5" y="5.5" width="3" height="7.5" rx="0.5" fill="currentColor" opacity={thinkingDepth === "medium" || thinkingDepth === "high" || thinkingDepth === "max" ? 1 : 0.25} />
                          <rect x="10" y="2" width="3" height="11" rx="0.5" fill="currentColor" opacity={thinkingDepth === "high" || thinkingDepth === "max" ? 1 : 0.25} />
                          <rect x="14.5" y="0.5" width="2.5" height="12.5" rx="0.5" fill="currentColor" opacity={thinkingDepth === "max" ? 1 : 0.25} />
                        </svg>
                        <span style={{ fontSize: 10 }}>{{ low: t("chat.depthLow"), medium: t("chat.depthMedium"), high: t("chat.depthHigh"), max: t("chat.depthMax") }[thinkingDepth]}</span>
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="text-xs" onPointerDownOutside={(e) => e.preventDefault()}>
                      {{ low: t("chat.depthTipLow"), medium: t("chat.depthTipMedium"), high: t("chat.depthTipHigh"), max: t("chat.depthTipMax") }[thinkingDepth]}
                      <span className="block text-[10px] opacity-60 mt-0.5">{t("chat.depthClickToSwitch")}</span>
                    </TooltipContent>
                  </Tooltip>
                )}
                {/* Context usage bar */}
                {contextLimit > 0 && (() => {
                  const usagePercent = Math.min((contextTokens / contextLimit) * 100, 100);
                  const remaining = Math.max(0, contextLimit - contextTokens);
                  const toneClass = usagePercent > 80 ? "chatContextUsageDanger" : usagePercent > 60 ? "chatContextUsageWarn" : "";
                  const fillClass = usagePercent > 80 ? "chatContextBarFillDanger" : usagePercent > 60 ? "chatContextBarFillWarn" : "";
                  return (
                    <div className="chatContextUsage">
                      <span className={`chatContextUsageText ${toneClass}`}>
                        {formatContextTokens(contextTokens)} /
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              className="chatContextLimitEditable"
                              onClick={openContextEditor}
                              disabled={contextSaving}
                            >
                              {formatContextTokens(contextLimit)}
                            </button>
                          </TooltipTrigger>
                          <TooltipContent side="top" className="text-xs">
                            {t("chat.contextClickToEdit", "点击编辑上下文长度")}
                          </TooltipContent>
                        </Tooltip>
                        {" · "}
                        {t("chat.contextRemaining", "剩余 {{remaining}}", { remaining: formatContextTokens(remaining) })}
                      </span>
                      <div className="chatContextBar">
                        <div className={`chatContextBarFill ${fillClass}`} style={{ width: `${usagePercent}%` }} />
                      </div>
                    </div>
                  );
                })()}
              </div>

              <div className="chatInputToolbarRight">
                {isCurrentConvStreaming || orgCommandPending ? (
                  (hasInputText || pendingAttachments.length > 0) && !orgCommandPending ? (
                    <button
                      data-slot="steer"
                      onClick={() => submitWhileStreaming()}
                      className="chatInputSendBtn"
                      title={t("chat.steerHint", "注入到当前任务（不打断，回车发送 / Ctrl+Enter 排队）")}
                    >
                      <IconSend size={14} />
                    </button>
                  ) : (
                    <button
                      data-slot="stop"
                      onClick={handleCancelTask}
                      className="chatInputSendBtn chatInputStopBtn"
                      title={orgCommandPending ? "停止组织命令" : t("chat.stopGeneration")}
                    >
                      <IconStop size={14} />
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
      <Dialog open={contextEditOpen} onOpenChange={(open) => { if (!contextSaving) setContextEditOpen(open); }}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>{t("chat.contextEditTitle", "编辑上下文长度")}</DialogTitle>
            <DialogDescription>
              {t("chat.contextEditDesc", "设置当前聊天使用的最大上下文长度（tokens）。")}
            </DialogDescription>
          </DialogHeader>
          <div className="chatContextEditBody">
            <Input
              type="number"
              value={editingContextLimit}
              min={1000}
              step={1000}
              inputMode="numeric"
              placeholder={t("chat.contextEditPlaceholder", "例如：256000")}
              onChange={(e) => setEditingContextLimit(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void saveContextLimit();
                }
              }}
            />
            <div className="chatContextEditHint">
              {t("chat.contextEditHint", "推荐使用 64000~512000 之间的值，保存后会立即应用。")}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setContextEditOpen(false)} disabled={contextSaving}>
              {t("chat.contextEditCancel", "取消")}
            </Button>
            <Button onClick={() => void saveContextLimit()} disabled={contextSaving}>
              {contextSaving ? t("common.saving", "保存中...") : t("chat.contextEditSave", "保存")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
          key={securityConfirm.toolId || `${securityConfirm.source || "policy_v2"}:${securityConfirm.tool}`}
          data={securityConfirm}
          apiBase={apiBaseUrl}
          onClose={handleSecurityClose}
        />,
        document.body,
      )}

      {/*
        C18 Phase B：批量 resolve 横幅。仅当
        (a) POLICIES.yaml ``confirmation.aggregation_window_seconds`` > 0
        (b) 当前正显示一个 modal
        (c) queue 还排着 ≥1 个 confirm
        三者同时成立时显示。点击调用 ``/api/chat/security-confirm/batch``
        一次性 resolve 当前 session 窗内全部 confirm（包含正在显示的）。
      */}
      {securityConfirm && securityAggWindow > 0 && securityQueueLen >= 1 && createPortal(
        <div
          role="region"
          aria-label={t("security.batch.banner_label", "批量确认")}
          style={{
            position: "fixed",
            top: 24,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10000,
            display: "flex",
            gap: 8,
            alignItems: "center",
            padding: "10px 14px",
            borderRadius: 10,
            background: "#1f2937",
            color: "#f9fafb",
            boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
            fontSize: 13,
            maxWidth: "min(90vw, 720px)",
          }}
        >
          <span style={{ opacity: 0.85 }}>
            {t(
              "security.batch.queue_hint",
              "本会话还有 {{count}} 个待确认操作（{{window}}s 窗内聚合）",
              { count: securityQueueLen, window: securityAggWindow },
            )}
          </span>
          <button
            type="button"
            onClick={() => handleSecurityBatchResolve("allow_once")}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              background: "#16a34a",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            {t("security.batch.allow_all", "全部允许 ({{n}})", { n: securityQueueLen + 1 })}
          </button>
          <button
            type="button"
            onClick={() => handleSecurityBatchResolve("deny")}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              background: "#dc2626",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            {t("security.batch.deny_all", "全部拒绝 ({{n}})", { n: securityQueueLen + 1 })}
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}
