// ─── IMView: IM Channel Viewer + Bot Configuration ───

import { useEffect, useState, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  IconIM, IconMessageCircle, IconRefresh, IconFile, IconImage, IconVolume,
  IconBot, IconPlus, IconEdit, IconTrash,
  IconUser, IconUsers,
  DotGreen, DotGray,
  IM_LOGO_MAP,
} from "../icons";
import { safeFetch } from "../providers";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { logger } from "../platform";
import { IS_WEB, onWsEvent } from "../platform";
import { FeishuQRModal } from "../components/FeishuQRModal";
import { QQBotQRModal } from "../components/QQBotQRModal";
import { WecomQRModal } from "../components/WecomQRModal";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetFooter } from "@/components/ui/sheet";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Bot, BotOff, Loader2, MoreHorizontal, Pencil, RefreshCw, Trash2, X } from "lucide-react";

// ─── Types ──────────────────────────────────────────────────────────────

type IMChannel = {
  channel: string;
  channel_type?: string;
  name: string;
  status: "online" | "offline";
  sessionCount: number;
  lastActive: string | null;
};

type IMSession = {
  sessionId: string;
  channel: string;
  chatId: string | null;
  userId: string | null;
  chatType?: string;
  chatName?: string;
  displayName?: string;
  state: string;
  lastActive: string;
  messageCount: number;
  lastMessage: string | null;
  botEnabled?: boolean;
  responseMode?: string | null;
  alias?: string | null;
};

type ChainSummaryItem = {
  iteration: number;
  thinking_preview: string;
  thinking_duration_ms: number;
  tools: { name: string; input_preview: string }[];
  context_compressed?: {
    before_tokens: number;
    after_tokens: number;
  };
};

type IMMessage = {
  id?: number;
  role: string;
  content: string;
  timestamp: string;
  metadata?: Record<string, unknown> | null;
  chain_summary?: ChainSummaryItem[] | null;
};

type IMBot = {
  id: string;
  type: string;
  name: string;
  agent_profile_id: string;
  enabled: boolean;
  credentials: Record<string, unknown>;
};

type AgentProfile = {
  id: string;
  name: string;
  icon: string;
};

const DEFAULT_API = "http://127.0.0.1:18900";

const BOT_TYPES = ["wework", "wework_ws", "qqbot", "feishu", "dingtalk", "telegram", "onebot", "onebot_reverse"] as const;

const BOT_TYPE_LABEL_KEYS: Record<string, string> = {
  feishu: "im.botTypeFeishu",
  telegram: "im.botTypeTelegram",
  dingtalk: "im.botTypeDingtalk",
  wework: "im.botTypeWeworkHttp",
  wework_ws: "im.botTypeWeworkWs",
  onebot: "im.botTypeOnebotForward",
  onebot_reverse: "im.botTypeOnebotReverse",
  qqbot: "im.botTypeQQBot",
};

const WEWORK_TYPES = new Set(["wework", "wework_ws"]);
const ONEBOT_TYPES = new Set(["onebot", "onebot_reverse"]);

const CREDENTIAL_FIELDS: Record<string, { key: string; label: string; secret?: boolean; placeholder?: string }[]> = {
  feishu: [
    { key: "app_id", label: "App ID" },
    { key: "app_secret", label: "App Secret", secret: true },
  ],
  telegram: [
    { key: "bot_token", label: "Bot Token", secret: true, placeholder: "BotFather token" },
    { key: "proxy", label: "config.imProxy", placeholder: "http://127.0.0.1:7890" },
    { key: "pairing_code", label: "config.imPairingCode", placeholder: "config.imPairingCodeHint" },
    { key: "webhook_url", label: "Webhook URL", placeholder: "https://..." },
  ],
  dingtalk: [
    { key: "client_id", label: "Client ID / App Key" },
    { key: "client_secret", label: "Client Secret / App Secret", secret: true },
  ],
  wework: [
    { key: "corp_id", label: "Corp ID" },
    { key: "token", label: "Token", secret: true },
    { key: "encoding_aes_key", label: "Encoding AES Key", secret: true },
    { key: "callback_port", label: "Callback Port" },
    { key: "callback_host", label: "Callback Host" },
  ],
  wework_ws: [
    { key: "bot_id", label: "Bot ID" },
    { key: "secret", label: "Secret", secret: true },
  ],
  onebot: [
    { key: "ws_url", label: "WebSocket URL" },
    { key: "access_token", label: "Access Token", secret: true },
  ],
  onebot_reverse: [
    { key: "reverse_host", label: "Listen Host" },
    { key: "reverse_port", label: "Listen Port" },
    { key: "access_token", label: "Access Token", secret: true },
  ],
  qqbot: [
    { key: "app_id", label: "App ID" },
    { key: "app_secret", label: "App Secret", secret: true },
  ],
};

const EMPTY_BOT: IMBot = {
  id: "",
  type: "feishu",
  name: "",
  agent_profile_id: "default",
  enabled: true,
  credentials: {},
};

// ─── Main Component ─────────────────────────────────────────────────────

export function IMView({
  serviceRunning,
  apiBaseUrl,
}: {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}) {
  const { t } = useTranslation();
  const api = apiBaseUrl ?? DEFAULT_API;

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconIM size={48} />
        <div className="mt-3 font-semibold">{t("im.channels")}</div>
        <div className="mt-1 text-xs opacity-50">后端服务未启动，请启动后再进行使用</div>
      </div>
    );
  }

  const [activeTab, setActiveTab] = useState<"messages" | "groupPolicy">("messages");

  return (
    <div className="flex flex-col h-full">
      <div style={{ display: "flex", alignItems: "center", marginBottom: 12, flexShrink: 0 }}>
        <ToggleGroup
          type="single"
          value={activeTab}
          onValueChange={(v) => { if (v) setActiveTab(v as "messages" | "groupPolicy"); }}
          variant="outline"
        >
          <ToggleGroupItem
            value="messages"
            className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
          >
            {t("im.tabMessages")}
          </ToggleGroupItem>
          <ToggleGroupItem
            value="groupPolicy"
            className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
          >
            {t("im.tabGroupPolicy")}
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {activeTab === "messages" && <MessagesTab serviceRunning={serviceRunning} apiBase={api} />}
        {activeTab === "groupPolicy" && <GroupPolicyTab apiBase={api} />}
      </div>
    </div>
  );
}

const MENU_ITEM_CLASS =
  "relative flex w-full cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none hover:bg-accent hover:text-accent-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg:not([class*='text-'])]:text-muted-foreground";

const MENU_ITEM_DESTRUCTIVE_CLASS =
  "relative flex w-full cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none text-destructive hover:bg-destructive/10 hover:text-destructive dark:hover:bg-destructive/20 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 [&_svg]:text-destructive!";

// ─── Messages Tab (original IM view) ────────────────────────────────────

function MessagesTab({ serviceRunning, apiBase }: { serviceRunning: boolean; apiBase: string }) {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<IMChannel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [sessions, setSessions] = useState<IMSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<IMMessage[]>([]);
  const [totalMessages, setTotalMessages] = useState(0);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedMsgIds, setSelectedMsgIds] = useState<Set<number>>(new Set());
  const [loadingMore, setLoadingMore] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const topSentinelRef = useRef<HTMLDivElement>(null);
  const isFirstLoad = useRef(true);

  const [inlineEditSessionId, setInlineEditSessionId] = useState<string | null>(null);
  const [inlineEditValue, setInlineEditValue] = useState("");
  const [aliasDialogSession, setAliasDialogSession] = useState<IMSession | null>(null);
  const [aliasDialogValue, setAliasDialogValue] = useState("");
  const [openMenuSessionId, setOpenMenuSessionId] = useState<string | null>(null);
  const [menuPos, setMenuPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 });
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!openMenuSessionId) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuSessionId(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenMenuSessionId(null);
    };
    window.addEventListener("mousedown", onDown, true);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [openMenuSessionId]);

  const getChannelDisplayName = useCallback((ch: IMChannel): string => {
    const key = `status.${(ch.channel || "").toLowerCase()}`;
    const translated = t(key);
    return translated && translated !== key ? translated : (ch.name || ch.channel);
  }, [t]);

  const fetchChannels = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${apiBase}/api/im/channels`);
      const data = await res.json();
      setChannels(data.channels || []);
    } catch { /* ignore */ }
  }, [serviceRunning, apiBase]);

  const fetchSessions = useCallback(async (channel: string): Promise<IMSession[]> => {
    if (!serviceRunning) return [];
    try {
      const res = await safeFetch(`${apiBase}/api/im/sessions?channel=${encodeURIComponent(channel)}`);
      const data = await res.json();
      const list: IMSession[] = data.sessions || [];
      setSessions(list);
      return list;
    } catch { /* ignore */ }
    return [];
  }, [serviceRunning, apiBase]);

  const fetchMessages = useCallback(async (sessionId: string, limit = 50, offset = 0, df?: string, dt?: string) => {
    if (!serviceRunning) return;
    try {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (df) params.set("date_from", df);
      if (dt) params.set("date_to", dt);
      const res = await safeFetch(`${apiBase}/api/im/sessions/${encodeURIComponent(sessionId)}/messages?${params}`);
      const data = await res.json();
      setMessages(data.messages || []);
      setTotalMessages(data.total || 0);
    } catch { /* ignore */ }
  }, [serviceRunning, apiBase]);

  const deleteSession = useCallback(async (sessionId: string) => {
    try {
      await safeFetch(`${apiBase}/api/im/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    } catch { /* ignore */ }
    if (selectedSessionId === sessionId) {
      setSelectedSessionId(null);
      setMessages([]);
    }
    if (selectedChannel) fetchSessions(selectedChannel);
    fetchChannels();
  }, [apiBase, selectedChannel, selectedSessionId, fetchSessions, fetchChannels]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await fetchChannels();
      if (selectedChannel) await fetchSessions(selectedChannel);
    } finally {
      setRefreshing(false);
    }
  }, [fetchChannels, fetchSessions, selectedChannel]);

  useEffect(() => { fetchChannels(); }, [fetchChannels]);

  useEffect(() => {
    if (!serviceRunning) return;
    const channelTimer = setInterval(() => {
      fetchChannels();
      if (selectedChannel) fetchSessions(selectedChannel);
    }, IS_WEB ? 60_000 : 15000);
    return () => clearInterval(channelTimer);
  }, [serviceRunning, selectedChannel, fetchChannels, fetchSessions]);

  useEffect(() => {
    if (!serviceRunning || !selectedSessionId) return;
    fetchMessages(selectedSessionId, 50, 0, dateFrom || undefined, dateTo || undefined);
    const msgTimer = setInterval(() => {
      fetchMessages(selectedSessionId, 50, 0, dateFrom || undefined, dateTo || undefined);
    }, IS_WEB ? 30_000 : 8000);
    return () => clearInterval(msgTimer);
  }, [serviceRunning, selectedSessionId, fetchMessages, dateFrom, dateTo]);

  useEffect(() => {
    if (!IS_WEB) return;
    return onWsEvent((event, data) => {
      if (event === "im:channel_status") fetchChannels();
      if (event === "im:new_message") {
        const d = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
        const evtChannel = d.channel as string | undefined;
        if (selectedChannel && (!evtChannel || evtChannel === selectedChannel)) {
          fetchSessions(selectedChannel);
        }
        if (selectedSessionId) fetchMessages(selectedSessionId);
      }
      if (event === "im:bot_config_changed") {
        if (selectedChannel) fetchSessions(selectedChannel);
      }
    });
  }, [fetchChannels, fetchSessions, fetchMessages, selectedChannel, selectedSessionId]);

  const handleSelectChannel = useCallback(async (ch: string) => {
    setSelectedChannel(ch);
    setSelectedSessionId(null);
    setMessages([]);
    const list = await fetchSessions(ch);
    if (list.length > 0) {
      const first = list[0];
      setSelectedSessionId(first.sessionId);
      isFirstLoad.current = true;
      fetchMessages(first.sessionId);
    }
  }, [fetchSessions, fetchMessages]);

  const handleSelectSession = useCallback((sid: string) => {
    setSelectedSessionId(sid);
    setSelectMode(false);
    setSelectedMsgIds(new Set());
    isFirstLoad.current = true;
    fetchMessages(sid);
  }, [fetchMessages]);

  const handleLoadMore = useCallback(async () => {
    if (!selectedSessionId || loadingMore) return;
    const container = scrollContainerRef.current;
    const prevScrollHeight = container?.scrollHeight ?? 0;
    setLoadingMore(true);
    try {
      const nextOffset = messages.length;
      const params = new URLSearchParams({ limit: "50", offset: String(nextOffset) });
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      const res = await safeFetch(
        `${apiBase}/api/im/sessions/${encodeURIComponent(selectedSessionId)}/messages?${params}`,
      );
      const data = await res.json();
      const more: IMMessage[] = data.messages || [];
      if (more.length) {
        setMessages((prev) => [...prev, ...more]);
        requestAnimationFrame(() => {
          if (container) {
            container.scrollTop += container.scrollHeight - prevScrollHeight;
          }
        });
      }
      setTotalMessages(data.total || totalMessages);
    } catch { /* ignore */ }
    setLoadingMore(false);
  }, [apiBase, selectedSessionId, messages.length, totalMessages, loadingMore, dateFrom, dateTo]);

  const handleDeleteMessages = useCallback(async () => {
    if (!selectedSessionId || selectedMsgIds.size === 0) return;
    const turnIds = messages
      .filter((_, i) => selectedMsgIds.has(i))
      .map((m) => m.id)
      .filter((id): id is number => id != null);
    try {
      if (turnIds.length > 0) {
        await safeFetch(`${apiBase}/api/im/sessions/${encodeURIComponent(selectedSessionId)}/messages/delete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ turn_ids: turnIds }),
        });
      }
      setMessages((prev) => prev.filter((_, i) => !selectedMsgIds.has(i)));
      setTotalMessages((prev) => Math.max(0, prev - selectedMsgIds.size));
      setSelectedMsgIds(new Set());
      setSelectMode(false);
    } catch { /* ignore */ }
  }, [apiBase, selectedSessionId, selectedMsgIds, messages]);

  const toggleMsgSelect = useCallback((idx: number) => {
    setSelectedMsgIds((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  }, []);

  useEffect(() => {
    if (isFirstLoad.current && messages.length > 0 && scrollContainerRef.current) {
      isFirstLoad.current = false;
      requestAnimationFrame(() => {
        const el = scrollContainerRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      });
    }
  }, [messages]);

  useEffect(() => {
    const sentinel = topSentinelRef.current;
    const container = scrollContainerRef.current;
    if (!sentinel || !container) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && messages.length < totalMessages && !loadingMore) {
          handleLoadMore();
        }
      },
      { root: container, threshold: 0.1 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [messages.length, totalMessages, loadingMore, handleLoadMore]);

  const handleDeleteSession = useCallback((s: IMSession, e?: React.MouseEvent) => {
    e?.stopPropagation();
    const name = s.chatType === "group"
      ? (s.chatName || s.chatId || s.sessionId.slice(0, 12))
      : (s.displayName || s.userId || s.chatId || s.sessionId.slice(0, 12));
    setConfirmDialog({
      message: `确定要删除会话「${name}」吗？\n会话及其所有消息记录将被永久删除，不可恢复。`,
      onConfirm: () => deleteSession(s.sessionId),
    });
  }, [deleteSession]);

  const handleToggleBot = useCallback(async (s: IMSession, e?: React.MouseEvent) => {
    e?.stopPropagation();
    const isCurrentlyDisabled = s.botEnabled === false || s.responseMode === "disabled";
    const newEnabled = isCurrentlyDisabled;
    const newMode = isCurrentlyDisabled ? null : "disabled";
    setSessions((prev) => prev.map((x) =>
      x.sessionId === s.sessionId
        ? { ...x, botEnabled: newEnabled, responseMode: newMode }
        : (s.chatId && x.chatId === s.chatId ? { ...x, botEnabled: newEnabled, responseMode: newMode } : x),
    ));
    try {
      await safeFetch(`${apiBase}/api/im/bot-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel: s.channel,
          chat_id: s.chatId || "",
          user_id: "*",
          enabled: newEnabled,
          response_mode: newMode,
        }),
      });
      if (selectedChannel) fetchSessions(selectedChannel);
    } catch {
      setSessions((prev) => prev.map((x) => x.sessionId === s.sessionId ? { ...x, botEnabled: s.botEnabled, responseMode: s.responseMode } : x));
    }
  }, [apiBase, selectedChannel, fetchSessions]);

  const saveAlias = useCallback(async (s: IMSession, alias: string) => {
    const trimmed = alias.trim();
    if (!s.channel || !s.chatId) return;
    try {
      if (trimmed) {
        await safeFetch(`${apiBase}/api/im/chat-aliases`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ channel: s.channel, chat_id: s.chatId, alias: trimmed }),
        });
        setSessions((prev) =>
          prev.map((x) => x.chatId === s.chatId && x.channel === s.channel ? { ...x, alias: trimmed } : x),
        );
      } else {
        await safeFetch(
          `${apiBase}/api/im/chat-aliases?channel=${encodeURIComponent(s.channel)}&chat_id=${encodeURIComponent(s.chatId)}`,
          { method: "DELETE" },
        );
        setSessions((prev) =>
          prev.map((x) => x.chatId === s.chatId && x.channel === s.channel ? { ...x, alias: null } : x),
        );
      }
    } catch { /* ignore */ }
  }, [apiBase]);

  const handleInlineEditStart = useCallback((s: IMSession) => {
    setInlineEditSessionId(s.sessionId);
    setInlineEditValue(s.alias || "");
  }, []);

  const handleInlineEditSave = useCallback((s: IMSession) => {
    setInlineEditSessionId(null);
    saveAlias(s, inlineEditValue);
  }, [inlineEditValue, saveAlias]);

  const handleAliasDialogOpen = useCallback((s: IMSession, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setAliasDialogSession(s);
    setAliasDialogValue(s.alias || "");
  }, []);

  const handleAliasDialogSave = useCallback(() => {
    if (!aliasDialogSession) return;
    saveAlias(aliasDialogSession, aliasDialogValue);
    setAliasDialogSession(null);
  }, [aliasDialogSession, aliasDialogValue, saveAlias]);

  const handleAliasDialogClear = useCallback(() => {
    if (!aliasDialogSession) return;
    saveAlias(aliasDialogSession, "");
    setAliasDialogSession(null);
  }, [aliasDialogSession, saveAlias]);

  const getSessionDisplayName = useCallback((s: IMSession): string => {
    if (s.alias) return s.alias;
    if (s.chatType === "group") return s.chatName || s.chatId || s.sessionId.slice(0, 12);
    return s.displayName || s.userId || s.chatId || s.sessionId.slice(0, 12);
  }, []);

  return (
    <>
      <div className="imView">
        {/* ── Left sidebar: channels + sessions ── */}
        <div className="imLeft">
          {/* Channel list header */}
          <div className="flex items-center justify-between px-3 pt-2.5 pb-1.5">
            <span className="text-sm font-semibold text-foreground">{t("im.channels")}</span>
            <Button variant="outline" size="sm" className="h-6 px-2 text-[11px] gap-1" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? <Loader2 className="animate-spin size-3" /> : <RefreshCw className="size-3" />}
              {t("topbar.refresh")}
            </Button>
          </div>

          {/* Channel list */}
          <div className="px-1.5 space-y-0.5">
            {channels.length === 0 && (
              <div className="px-4 py-4 text-center text-xs text-muted-foreground">{t("im.noChannels")}</div>
            )}
            {channels.map((ch) => (
              <button
                key={ch.channel}
                className={cn(
                  "flex w-full items-center justify-between rounded-[10px] px-2.5 py-2 text-[13px] font-semibold transition-[background,color,border,box-shadow] duration-150 cursor-pointer select-none",
                  selectedChannel === ch.channel
                    ? "bg-[#93c5fd] dark:bg-[#1d4ed8]/40 text-[#1e40af] dark:text-[#93c5fd] font-bold border-l-[4px] border-l-primary ring-1 ring-primary/50 shadow-md"
                    : "hover:bg-[var(--nav-hover)] text-muted-foreground border-l-[3px] border-transparent hover:text-foreground"
                )}
                onClick={() => handleSelectChannel(ch.channel)}
              >
                <span className="flex items-center gap-1.5 min-w-0">
                  {ch.status === "online" ? <DotGreen /> : <DotGray />}
                  {(IM_LOGO_MAP[(ch.channel_type || "").toLowerCase()] || IM_LOGO_MAP[(ch.channel || "").toLowerCase()])?.({ size: 14 })}
                  <span className="truncate">{getChannelDisplayName(ch)}</span>
                </span>
                <Badge variant="secondary" className="ml-1.5 h-5 min-w-[20px] justify-center text-[11px] px-1.5">
                  {ch.sessionCount}
                </Badge>
              </button>
            ))}
          </div>

          {/* Session list */}
          {selectedChannel && (
            <>
              <div className="flex items-center justify-between px-3 pt-3 pb-1.5">
                <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-wide">{t("im.sessions")}</span>
              </div>
              <div className="px-1.5">
                {sessions.length === 0 && (
                  <div className="px-4 py-4 text-center text-xs text-muted-foreground">{t("im.noSessions")}</div>
                )}
                {(() => {
                  const chatIdCount = new Map<string, number>();
                  for (const s of sessions) {
                    const cid = s.chatId || s.sessionId;
                    chatIdCount.set(cid, (chatIdCount.get(cid) || 0) + 1);
                  }
                  const isGroupChat = (s: IMSession) =>
                    s.chatType === "group" || (chatIdCount.get(s.chatId || s.sessionId) || 0) > 1;

                  const groupSessions = sessions.filter(isGroupChat);
                  const privateSessions = sessions.filter((s) => !isGroupChat(s));
                  const groupByChatId = new Map<string, IMSession[]>();
                  for (const s of groupSessions) {
                    const key = s.chatId || s.sessionId;
                    if (!groupByChatId.has(key)) groupByChatId.set(key, []);
                    groupByChatId.get(key)!.push(s);
                  }
                  type RenderItem = { type: "group-header"; chatId: string; name: string; alias?: string | null } | { type: "session"; session: IMSession; indented: boolean };
                  const renderItems: RenderItem[] = [];
                  for (const [chatId, items] of groupByChatId) {
                    const first = items[0];
                    renderItems.push({ type: "group-header" as const, chatId, name: first.chatName || chatId, alias: first.alias });
                    for (const s of items) renderItems.push({ type: "session" as const, session: s, indented: true });
                  }
                  for (const s of privateSessions) renderItems.push({ type: "session" as const, session: s, indented: false });

                  return renderItems.map((item) => {
                    if (item.type === "group-header") {
                      return (
                        <div key={`gh-${item.chatId}`} className="flex items-center gap-1.5 px-2.5 pt-3 pb-1">
                          <IconUsers size={12} className="shrink-0 text-muted-foreground" />
                          <span className="text-[11px] font-bold text-muted-foreground truncate">
                            {item.alias || item.name}
                          </span>
                          {item.alias && item.name !== item.alias && (
                            <span className="text-[10px] text-muted-foreground/50 truncate">({item.name})</span>
                          )}
                        </div>
                      );
                    }
                    const s = item.session;
                    const isBotActive = s.botEnabled !== false && s.responseMode !== "disabled";
                    const subtitle = s.alias
                      ? (s.chatType === "group" ? (s.chatName || s.chatId || "") : (s.displayName || s.chatId || ""))
                      : (s.chatType === "group" && s.displayName ? s.displayName : (s.chatId || s.sessionId.slice(0, 12)));
                    return (
                    <div
                      key={s.sessionId}
                      className={cn(
                        "group flex flex-col gap-0.5 rounded-[10px] py-1.5 transition-[background,color,border,box-shadow] duration-150 cursor-pointer select-none border",
                        item.indented ? "pl-5 pr-2.5" : "px-2.5",
                        selectedSessionId === s.sessionId
                          ? "bg-[#dbeafe] dark:bg-[#1e3a5f] text-primary border-primary/40 dark:border-primary/50 ring-1 ring-primary/30 dark:ring-primary/40 shadow-md"
                          : "hover:bg-[var(--nav-hover)] border-transparent",
                        !isBotActive && "opacity-50",
                      )}
                      onClick={() => handleSelectSession(s.sessionId)}
                      title={[
                        s.alias ? `✏ ${s.alias}` : null,
                        s.chatType === "group"
                          ? (s.chatName || s.chatId || s.sessionId)
                          : (s.displayName || s.userId || s.chatId || s.sessionId),
                        s.chatType === "group" && s.displayName ? `(${s.displayName})` : "",
                        s.chatId ? `chat: ${s.chatId}` : "",
                        s.userId ? `user: ${s.userId}` : "",
                      ].filter(Boolean).join("\n")}
                      role="button"
                      tabIndex={0}
                    >
                      {/* Row 1: icon + name + alias badge + time */}
                      <div
                        className="flex items-center gap-1.5"
                        onDoubleClick={(e) => { e.stopPropagation(); handleInlineEditStart(s); }}
                      >
                        {s.chatType === "group" ? <IconUsers size={13} className="shrink-0" /> : <IconUser size={13} className="shrink-0" />}
                        {inlineEditSessionId === s.sessionId ? (
                          <Input
                            autoFocus
                            value={inlineEditValue}
                            onChange={(e) => setInlineEditValue(e.target.value)}
                            onBlur={() => handleInlineEditSave(s)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") handleInlineEditSave(s);
                              if (e.key === "Escape") setInlineEditSessionId(null);
                            }}
                            onClick={(e) => e.stopPropagation()}
                            className="h-5 text-[13px] px-1 py-0 min-w-0 flex-1"
                            placeholder={t("im.aliasPlaceholder")}
                          />
                        ) : (
                          <span className={cn("font-semibold truncate text-[13px] flex-1 min-w-0", s.alias && "text-primary")}>
                            {getSessionDisplayName(s)}
                          </span>
                        )}
                        {!inlineEditSessionId && s.alias && (
                          <Badge variant="outline" className="h-4 text-[9px] px-1 py-0 shrink-0">{t("im.aliasSet")}</Badge>
                        )}
                        <span className="text-[11px] text-muted-foreground whitespace-nowrap shrink-0">
                          {s.lastActive ? new Date(s.lastActive).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                        </span>
                      </div>
                      {/* Row 2: subtitle + count + actions menu */}
                      <div className="flex items-center gap-1.5 pl-[19px]">
                        <span className="text-[11px] text-muted-foreground truncate flex-1 min-w-0">
                          {subtitle}
                        </span>
                        <Badge variant="outline" className="h-5 min-w-[20px] justify-center text-[11px] px-1.5 shrink-0">
                          {s.messageCount}
                        </Badge>
                        {!isBotActive && (
                          <BotOff className="size-3 text-destructive/60 shrink-0" />
                        )}
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className={cn(
                            "transition-opacity text-muted-foreground hover:text-foreground shrink-0",
                            openMenuSessionId === s.sessionId ? "opacity-100" : "opacity-50 group-hover:opacity-100",
                          )}
                          onClick={(e) => {
                            e.stopPropagation();
                            const rect = e.currentTarget.getBoundingClientRect();
                            setMenuPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
                            setOpenMenuSessionId((prev) => (prev === s.sessionId ? null : s.sessionId));
                          }}
                        >
                          <MoreHorizontal className="size-3.5" />
                        </Button>
                        {openMenuSessionId === s.sessionId && createPortal(
                          <div
                            ref={menuRef}
                            className="fixed z-50 min-w-[8rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95"
                            style={{ top: menuPos.top, right: menuPos.right }}
                          >
                            <button
                              className={MENU_ITEM_CLASS}
                              onClick={() => { setOpenMenuSessionId(null); handleAliasDialogOpen(s); }}
                            >
                              <Pencil className="size-3.5" />
                              {t("im.renameChat")}
                            </button>
                            <button
                              className={MENU_ITEM_CLASS}
                              onClick={() => { setOpenMenuSessionId(null); handleToggleBot(s); }}
                            >
                              {isBotActive
                                ? <><BotOff className="size-3.5 text-destructive" />{t("im.disableBot")}</>
                                : <><Bot className="size-3.5 text-emerald-500" />{t("im.enableBot")}</>
                              }
                            </button>
                            <div className="-mx-1 my-1 h-px bg-border" />
                            <button
                              className={MENU_ITEM_DESTRUCTIVE_CLASS}
                              onClick={() => { setOpenMenuSessionId(null); handleDeleteSession(s); }}
                            >
                              <Trash2 className="size-3.5" />
                              {t("im.deleteSession")}
                            </button>
                          </div>,
                          document.body,
                        )}
                      </div>
                    </div>
                    );
                  });
                })()}
              </div>
            </>
          )}
        </div>

        {/* ── Right: message area ── */}
        <div className="imRight">
          {!selectedSessionId ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <IconMessageCircle size={40} />
              <div className="mt-2 text-xs opacity-50">{t("im.noMessages")}</div>
            </div>
          ) : (
            <div className="flex flex-col h-full">
              {/* Messages header + toolbar */}
              <div className="flex items-center justify-between px-4 py-2 border-b">
                <span className="text-xs font-bold text-muted-foreground">
                  {t("im.messages")} ({totalMessages})
                </span>
                {/* TODO: batch delete — hidden until backend reliability is confirmed */}
              </div>
              {/* Date range filter */}
              <div className="flex items-center gap-2 px-4 py-1.5 border-b">
                <span className="text-[11px] text-muted-foreground shrink-0">{t("im.dateFrom")}</span>
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="h-7 text-xs w-32"
                />
                <span className="text-[11px] text-muted-foreground shrink-0">{t("im.dateTo")}</span>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="h-7 text-xs w-32"
                />
                {(dateFrom || dateTo) && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 text-[11px] px-2"
                    onClick={() => { setDateFrom(""); setDateTo(""); }}
                  >
                    {t("im.clearFilter")}
                  </Button>
                )}
              </div>
              {/* Messages list */}
              <div ref={scrollContainerRef} className="flex-1 overflow-auto px-4 py-3 space-y-3">
                {/* Top sentinel for infinite scroll */}
                <div ref={topSentinelRef} className="h-px" />
                {messages.length < totalMessages && (
                  <div className="flex justify-center py-1">
                    {loadingMore && <Loader2 className="animate-spin size-4 text-muted-foreground" />}
                  </div>
                )}
                {messages.map((msg, idx) => {
                  const curDate = msg.timestamp ? new Date(msg.timestamp).toLocaleDateString() : "";
                  const prevDate = idx > 0 && messages[idx - 1].timestamp
                    ? new Date(messages[idx - 1].timestamp).toLocaleDateString()
                    : null;
                  const showDateLine = curDate && (!prevDate || curDate !== prevDate);
                  return (
                  <div key={msg.id ?? idx}>
                  {showDateLine && (
                    <div className="flex items-center gap-3 py-2">
                      <div className="flex-1 h-px bg-border" />
                      <span className="text-[11px] text-muted-foreground whitespace-nowrap">{curDate}</span>
                      <div className="flex-1 h-px bg-border" />
                    </div>
                  )}
                  <div className="flex gap-2">
                    {selectMode && (
                      <Checkbox
                        checked={selectedMsgIds.has(idx)}
                        onCheckedChange={() => toggleMsgSelect(idx)}
                        className="mt-1.5 shrink-0"
                      />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge
                          variant={msg.role === "user" ? "default" : msg.role === "system" ? "outline" : "secondary"}
                          className="text-[10px] px-1.5 py-0 h-[18px]"
                        >
                          {msg.role === "user" ? t("im.user") : msg.role === "system" ? t("im.system") : t("im.bot")}
                        </Badge>
                        <span className="text-[10px] text-muted-foreground">
                          {msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : ""}
                        </span>
                      </div>
                      {msg.role !== "user" && msg.chain_summary && msg.chain_summary.length > 0 && (
                        <IMChainSummary chain={msg.chain_summary} />
                      )}
                      <div className={cn(
                        "text-[13px] leading-relaxed p-2.5 rounded-lg border",
                        msg.role === "user"
                          ? "bg-primary/[0.04] border-primary/[0.12]"
                          : "bg-muted/50 border-border",
                        selectMode && selectedMsgIds.has(idx) && "ring-2 ring-primary/30",
                      )}>
                        <MediaContent content={msg.content} />
                      </div>
                    </div>
                  </div>
                  </div>
                  );
                })}
                {messages.length === 0 && (
                  <div className="px-4 py-4 text-center text-xs text-muted-foreground">{t("im.noMessages")}</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />

      {/* Alias edit dialog */}
      <AlertDialog open={!!aliasDialogSession} onOpenChange={(open) => { if (!open) setAliasDialogSession(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("im.renameChat")}</AlertDialogTitle>
          </AlertDialogHeader>
          <div className="px-1 py-2 space-y-3">
            <div className="text-xs text-muted-foreground">
              {aliasDialogSession?.chatType === "group"
                ? (aliasDialogSession.chatName || aliasDialogSession.chatId || "")
                : (aliasDialogSession?.displayName || aliasDialogSession?.userId || aliasDialogSession?.chatId || "")}
            </div>
            <Input
              autoFocus
              value={aliasDialogValue}
              onChange={(e) => setAliasDialogValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleAliasDialogSave(); }}
              placeholder={t("im.aliasPlaceholder")}
            />
          </div>
          <AlertDialogFooter>
            {aliasDialogSession?.alias && (
              <Button variant="outline" size="sm" onClick={handleAliasDialogClear} className="mr-auto">
                {t("im.clearAlias")}
              </Button>
            )}
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleAliasDialogSave}>{t("common.confirm")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ─── Group Policy Tab ───────────────────────────────────────────────────

type GroupSessionInfo = {
  sessionId: string;
  chatId: string;
  chatName: string;
  alias: string | null;
  responseMode: string | null;
  botEnabled: boolean;
};

const RESPONSE_MODES = [
  { value: "global", labelKey: "im.responseMode_global" },
  { value: "mention_only", labelKey: "im.responseMode_mention_only" },
  { value: "always", labelKey: "im.responseMode_always" },
  { value: "disabled", labelKey: "im.responseMode_disabled" },
] as const;

function GroupPolicyTab({ apiBase }: { apiBase: string }) {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<IMChannel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [groupSessions, setGroupSessions] = useState<GroupSessionInfo[]>([]);
  const [savingChat, setSavingChat] = useState<string | null>(null);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBase}/api/im/channels`);
      const data = await res.json();
      setChannels(data.channels || []);
    } catch { /* ignore */ }
  }, [apiBase]);

  useEffect(() => { fetchChannels(); }, [fetchChannels]);

  const fetchGroupSessions = useCallback(async (ch: string) => {
    try {
      const res = await safeFetch(`${apiBase}/api/im/sessions?channel=${encodeURIComponent(ch)}`);
      const data = await res.json();
      const all: IMSession[] = data.sessions || [];
      const groups = all
        .filter((s) => s.chatType === "group")
        .map((s) => ({
          sessionId: s.sessionId,
          chatId: s.chatId || "",
          chatName: s.chatName || s.chatId || s.sessionId.slice(0, 12),
          alias: s.alias || null,
          responseMode: (s as any).responseMode ?? null,
          botEnabled: s.botEnabled !== false,
        }));
      const deduped = new Map<string, GroupSessionInfo>();
      for (const g of groups) {
        if (!deduped.has(g.chatId)) deduped.set(g.chatId, g);
      }
      setGroupSessions(Array.from(deduped.values()));
    } catch { /* ignore */ }
  }, [apiBase]);

  const handleSelectChannel = useCallback((ch: string) => {
    setSelectedChannel(ch);
    fetchGroupSessions(ch);
  }, [fetchGroupSessions]);

  useEffect(() => {
    if (!IS_WEB) return;
    return onWsEvent((event) => {
      if (event === "im:bot_config_changed" && selectedChannel) {
        fetchGroupSessions(selectedChannel);
      }
    });
  }, [selectedChannel, fetchGroupSessions]);

  const handleSetMode = useCallback(async (g: GroupSessionInfo, mode: string) => {
    if (!selectedChannel) return;
    setSavingChat(g.chatId);
    const apiMode = mode === "global" ? null : mode;
    setGroupSessions((prev) =>
      prev.map((x) => x.chatId === g.chatId ? { ...x, responseMode: apiMode } : x),
    );
    try {
      await safeFetch(`${apiBase}/api/im/bot-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel: selectedChannel,
          chat_id: g.chatId,
          user_id: "*",
          enabled: mode !== "disabled",
          response_mode: apiMode,
        }),
      });
      await new Promise((r) => setTimeout(r, 800));
    } catch { /* ignore */ }
    setSavingChat(null);
  }, [apiBase, selectedChannel]);

  const getChannelDisplayName = useCallback((ch: IMChannel): string => {
    const key = `status.${(ch.channel || "").toLowerCase()}`;
    const translated = t(key);
    return translated && translated !== key ? translated : (ch.name || ch.channel);
  }, [t]);

  return (
    <div className="flex h-full">
      {/* Left: channel list */}
      <div className="w-56 shrink-0 border-r overflow-y-auto">
        <div className="px-3 pt-2.5 pb-1.5">
          <span className="text-sm font-semibold text-foreground">{t("im.groupPolicyTitle")}</span>
        </div>
        <div className="px-1.5 space-y-0.5">
          {channels.map((ch) => (
            <button
              key={ch.channel}
              className={cn(
                "flex w-full items-center gap-1.5 rounded-[10px] px-2.5 py-2 text-[13px] font-semibold transition-[background,color,border,box-shadow] duration-150 cursor-pointer select-none",
                selectedChannel === ch.channel
                  ? "bg-[#93c5fd] dark:bg-[#1d4ed8]/40 text-[#1e40af] dark:text-[#93c5fd] font-bold border-l-[4px] border-l-primary ring-1 ring-primary/50 shadow-md"
                  : "hover:bg-[var(--nav-hover)] text-muted-foreground border-l-[3px] border-transparent hover:text-foreground",
              )}
              onClick={() => handleSelectChannel(ch.channel)}
            >
              {ch.status === "online" ? <DotGreen /> : <DotGray />}
              {(IM_LOGO_MAP[(ch.channel_type || "").toLowerCase()] || IM_LOGO_MAP[(ch.channel || "").toLowerCase()])?.({ size: 14 })}
              <span className="font-semibold truncate">{getChannelDisplayName(ch)}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Right: per-group mode config */}
      <div className="flex-1 min-w-0 overflow-y-auto p-4">
        {!selectedChannel ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-sm">
            <IconUsers size={40} />
            <p className="mt-2">{t("im.noChannel")}</p>
          </div>
        ) : groupSessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-sm">
            <IconUsers size={40} />
            <p className="mt-2">{t("im.groupAllowlistEmpty")}</p>
          </div>
        ) : (
          <div className="space-y-2 max-w-2xl">
            <p className="text-xs text-muted-foreground mb-3">{t("im.groupPolicyDesc")}</p>
            {groupSessions.map((g) => (
              <div key={g.chatId} className="flex items-center justify-between rounded-lg border px-3 py-2.5 gap-3">
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <IconUsers size={14} className="shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <span className={cn("text-sm font-medium truncate block", g.alias && "text-primary")}>{g.alias || g.chatName}</span>
                    {(g.alias || g.chatName !== g.chatId) && (
                      <span className="text-[11px] text-muted-foreground truncate block">{g.alias ? g.chatName : g.chatId}</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {savingChat === g.chatId && <Loader2 className="animate-spin size-3.5 text-muted-foreground" />}
                  <ToggleGroup
                    type="single"
                    variant="outline"
                    size="sm"
                    value={g.responseMode || "global"}
                    onValueChange={(v) => handleSetMode(g, v)}
                    className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground"
                  >
                    {RESPONSE_MODES.map((m) => (
                      <ToggleGroupItem key={m.value} value={m.value} className="text-[11px] h-6 px-2">
                        {t(m.labelKey)}
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Bot Configuration Tab ──────────────────────────────────────────────

export function BotConfigTab({ apiBase, multiAgentEnabled, onRequestRestart, venvDir, apiBaseUrl, enabledChannels }: { apiBase: string; multiAgentEnabled: boolean; onRequestRestart?: () => void; venvDir?: string; apiBaseUrl?: string; enabledChannels?: string[] }) {
  const { t } = useTranslation();
  const [bots, setBots] = useState<IMBot[]>([]);
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingBot, setEditingBot] = useState<IMBot>(EMPTY_BOT);
  const [isCreating, setIsCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [revealedSecrets, setRevealedSecrets] = useState<Set<string>>(new Set());
  const [showFeishuQR, setShowFeishuQR] = useState(false);
  const [showQQBotQR, setShowQQBotQR] = useState(false);
  const [showWecomQR, setShowWecomQR] = useState(false);
  const [tgPairingCode, setTgPairingCode] = useState<string | null>(null);
  const [tgPairingLoading, setTgPairingLoading] = useState(false);

  const loadTgPairingCode = useCallback(async () => {
    setTgPairingLoading(true);
    try {
      const res = await safeFetch(`${apiBase}/api/im/telegram/pairing-code`);
      const data = await res.json();
      setTgPairingCode(data.code || null);
    } catch {
      setTgPairingCode(null);
    } finally {
      setTgPairingLoading(false);
    }
  }, [apiBase]);

  const fetchBots = useCallback(async () => {
    setLoading(true);
    try {
      const res = await safeFetch(`${apiBase}/api/agents/bots`);
      const data = await res.json();
      setBots(data.bots || []);
    } catch (e) { logger.warn("IM", "Failed to fetch bots", { error: String(e) }); }
    setLoading(false);
  }, [apiBase]);

  const fetchProfiles = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBase}/api/agents/profiles`);
      const data = await res.json();
      setProfiles(data.profiles || []);
    } catch { /* ignore */ }
  }, [apiBase]);

  useEffect(() => {
    fetchBots();
    fetchProfiles();
  }, [multiAgentEnabled, fetchBots, fetchProfiles]);

  const openCreate = () => {
    setEditingBot({ ...EMPTY_BOT });
    setIsCreating(true);
    setEditorOpen(true);
    setRevealedSecrets(new Set());
  };

  const openEdit = (bot: IMBot) => {
    setEditingBot({ ...bot, credentials: { ...bot.credentials } });
    setIsCreating(false);
    setEditorOpen(true);
    setRevealedSecrets(new Set());
    if (bot.type === "telegram") loadTgPairingCode();
  };

  const closeEditor = () => {
    setEditorOpen(false);
  };

  const handleSave = async () => {
    if (!editingBot.id.trim()) return;
    setSaving(true);
    try {
      const url = isCreating
        ? `${apiBase}/api/agents/bots`
        : `${apiBase}/api/agents/bots/${editingBot.id}`;
      const method = isCreating ? "POST" : "PUT";
      const payload = isCreating
        ? {
            id: editingBot.id,
            type: editingBot.type,
            name: editingBot.name,
            agent_profile_id: editingBot.agent_profile_id,
            enabled: editingBot.enabled,
            credentials: editingBot.credentials,
          }
        : {
            type: editingBot.type,
            name: editingBot.name,
            agent_profile_id: editingBot.agent_profile_id,
            enabled: editingBot.enabled,
            credentials: editingBot.credentials,
          };

      await safeFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      closeEditor();
      fetchBots();
      toast.success(t("im.botSaveSuccess"));
    } catch (e) {
      toast.error(String(e) || t("im.botSaveFailed"));
    }
    setSaving(false);
  };

  const handleSaveAndRestart = async () => {
    if (!editingBot.id.trim()) return;
    setSaving(true);
    try {
      const url = isCreating
        ? `${apiBase}/api/agents/bots`
        : `${apiBase}/api/agents/bots/${editingBot.id}`;
      const method = isCreating ? "POST" : "PUT";
      const payload = isCreating
        ? {
            id: editingBot.id,
            type: editingBot.type,
            name: editingBot.name,
            agent_profile_id: editingBot.agent_profile_id,
            enabled: editingBot.enabled,
            credentials: editingBot.credentials,
          }
        : {
            type: editingBot.type,
            name: editingBot.name,
            agent_profile_id: editingBot.agent_profile_id,
            enabled: editingBot.enabled,
            credentials: editingBot.credentials,
          };

      await safeFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      closeEditor();
      fetchBots();
      toast.success(t("im.botSaveSuccess"));
      onRequestRestart?.();
    } catch (e) {
      toast.error(String(e) || t("im.botSaveFailed"));
    }
    setSaving(false);
  };

  const handleDelete = async (botId: string) => {
    try {
      await safeFetch(`${apiBase}/api/agents/bots/${botId}`, { method: "DELETE" });
      setConfirmDeleteId(null);
      fetchBots();
      toast.success(t("im.botDeleteSuccess"));
    } catch (e) {
      toast.error(String(e) || t("im.botDeleteFailed"));
    }
  };

  const handleToggle = async (bot: IMBot) => {
    try {
      await safeFetch(`${apiBase}/api/agents/bots/${bot.id}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !bot.enabled }),
      });
      fetchBots();
      toast.success(t("im.botToggleSuccess"));
    } catch { /* ignore */ }
  };

  const updateCredential = (key: string, value: string) => {
    setEditingBot((prev) => ({
      ...prev,
      credentials: { ...prev.credentials, [key]: value },
    }));
  };

  const credFields = CREDENTIAL_FIELDS[editingBot.type] || [];

  const streamingEnabled = editingBot.credentials.streaming_enabled === "true" || editingBot.credentials.streaming_enabled === true;
  const groupStreamingEnabled = editingBot.credentials.group_streaming === "true" || editingBot.credentials.group_streaming === true;

  if (!multiAgentEnabled) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-muted-foreground opacity-50">
        <IconBot size={48} />
        <div className="mt-3 font-bold">{t("im.needMultiAgent")}</div>
      </div>
    );
  }

  return (
    <div className="p-5 relative">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <IconBot size={24} />
        <div className="flex-1 min-w-0">
          <h2 className="text-lg font-semibold leading-tight">{t("im.botsTitle")}</h2>
          <p className="text-xs text-muted-foreground mt-0.5">{t("im.botsDesc")}</p>
        </div>
        <Button variant="outline" size="icon-sm" onClick={fetchBots} disabled={loading}>
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        </Button>
        <Button size="sm" onClick={openCreate}>
          <IconPlus size={14} />
          {t("im.createBot")}
        </Button>
      </div>

      {/* Bot Grid */}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3.5">
        {bots.map((bot) => {
          const agentProfile = profiles.find((p) => p.id === bot.agent_profile_id);
          return (
            <div
              key={bot.id}
              className={cn(
                "rounded-xl border bg-card p-4 relative overflow-hidden transition-shadow hover:shadow-md",
                !bot.enabled && "opacity-55"
              )}
            >
              <div className="absolute top-2 right-2 flex gap-1">
                <Badge variant="secondary" className="text-[10px] gap-1 px-1.5 py-0">
                  {IM_LOGO_MAP[bot.type]?.({ size: 12 })}
                  {t(BOT_TYPE_LABEL_KEYS[bot.type] || "", { defaultValue: bot.type })}
                </Badge>
                <Badge variant={bot.enabled ? "default" : "destructive"} className="text-[10px] px-1.5 py-0">
                  {bot.enabled ? t("im.botEnabled") : t("im.botDisabled")}
                </Badge>
              </div>

              <div className="flex items-center gap-2.5 mt-0.5 mb-1.5">
                <span className="text-2xl leading-none">{agentProfile?.icon || "🤖"}</span>
                <div className="min-w-0">
                  <div className="font-bold text-sm truncate">{bot.name || bot.id}</div>
                  <div className="text-[11px] text-muted-foreground/45 font-mono">{bot.id}</div>
                </div>
              </div>
              <p className="text-xs text-muted-foreground mb-2.5">
                {t("im.botAgent")}: {agentProfile?.name || bot.agent_profile_id}
              </p>

              <div className="flex gap-2">
                <Button
                  variant="outline" size="sm"
                  className={cn("h-7 text-xs", bot.enabled ? "text-destructive" : "text-emerald-600")}
                  onClick={() => handleToggle(bot)}
                >
                  {bot.enabled ? t("scheduler.disable") : t("scheduler.enable")}
                </Button>
                <Button variant="outline" size="sm" className="h-7 text-xs gap-1" onClick={() => openEdit(bot)}>
                  <IconEdit size={12} />{t("agentManager.edit")}
                </Button>
                <Button variant="ghost" size="icon-sm" className="text-destructive" onClick={() => setConfirmDeleteId(bot.id)}>
                  <IconTrash size={12} />
                </Button>
              </div>
            </div>
          );
        })}
      </div>

      {bots.length === 0 && !loading && (
        <div className="flex flex-col items-center justify-center py-10 text-muted-foreground opacity-50">
          <IconBot size={40} />
          <div className="mt-3">{t("im.noBots")}</div>
          <div className="text-xs mt-1">{t("im.noBotsHint")}</div>
        </div>
      )}

      {/* Delete confirmation */}
      <AlertDialog open={!!confirmDeleteId} onOpenChange={(open) => { if (!open) setConfirmDeleteId(null); }}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("im.botConfirmDelete")}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => confirmDeleteId && handleDelete(confirmDeleteId)}>
              {t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Editor Sheet */}
      <Sheet open={editorOpen} onOpenChange={(open) => { if (!open) closeEditor(); }}>
        <SheetContent side="right" className="sm:max-w-md flex flex-col"
          onPointerDownOutside={(e) => { if (showFeishuQR || showQQBotQR || showWecomQR) e.preventDefault(); }}
          onInteractOutside={(e) => { if (showFeishuQR || showQQBotQR || showWecomQR) e.preventDefault(); }}
        >
          <SheetHeader>
            <SheetTitle>{isCreating ? t("im.createBot") : t("im.editBot")}</SheetTitle>
          </SheetHeader>

          <div className="flex-1 overflow-auto px-6 py-4 space-y-4">
            {/* Bot ID */}
            <div className="space-y-1.5">
              <Label>{t("im.botId")}</Label>
              <Input
                value={editingBot.id}
                onChange={(e) => setEditingBot((p) => ({ ...p, id: e.target.value.replace(/[^a-z0-9_-]/gi, "").toLowerCase() }))}
                disabled={!isCreating}
                placeholder="my-feishu-bot"
              />
              {isCreating && <p className="text-[11px] text-muted-foreground/40">{t("im.botIdHint")}</p>}
            </div>

            {/* Bot Name */}
            <div className="space-y-1.5">
              <Label>{t("im.botName")}</Label>
              <Input
                value={editingBot.name}
                onChange={(e) => setEditingBot((p) => ({ ...p, name: e.target.value }))}
                placeholder="My Bot"
              />
            </div>

            {/* Bot Type */}
            <div className="space-y-1.5">
              <Label>{t("im.botType")}</Label>
              <Select
                value={WEWORK_TYPES.has(editingBot.type) ? "wework_ws" : ONEBOT_TYPES.has(editingBot.type) ? "onebot_reverse" : editingBot.type}
                onValueChange={(val) => setEditingBot((p) => ({ ...p, type: val, credentials: {} }))}
                disabled={!isCreating}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {BOT_TYPES.filter((bt) => bt !== "wework" && bt !== "onebot")
                    .filter((bt) => !enabledChannels || enabledChannels.includes(bt))
                    .map((bt) => (
                    <SelectItem key={bt} value={bt}>
                      {bt === "wework_ws" ? t("im.botTypeWework") : bt === "onebot_reverse" ? t("im.botTypeOnebot") : t(BOT_TYPE_LABEL_KEYS[bt] || "", { defaultValue: bt })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Agent Profile */}
            <div className="space-y-1.5">
              <Label>{t("im.botAgent")}</Label>
              <Select value={editingBot.agent_profile_id} onValueChange={(v) => setEditingBot((p) => ({ ...p, agent_profile_id: v }))}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">{t("im.botAgentDefault")}</SelectItem>
                  {profiles.map((p) => (
                    <SelectItem key={p.id} value={p.id}>{p.icon} {p.name} ({p.id})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Enabled */}
            <label className="flex items-center gap-2.5 cursor-pointer select-none">
              <Switch checked={editingBot.enabled} onCheckedChange={(v) => setEditingBot((p) => ({ ...p, enabled: v }))} />
              <span className="text-sm font-medium">{t("im.botEnabled")}</span>
            </label>

            {/* OneBot mode selector */}
            {ONEBOT_TYPES.has(editingBot.type) && (
              <div className="space-y-1.5">
                <Label>{t("config.imOneBotMode")}</Label>
                <ToggleGroup type="single" variant="outline" size="sm" value={editingBot.type} onValueChange={(v) => {
                  if (v && v !== editingBot.type) setEditingBot((p) => ({ ...p, type: v as typeof editingBot.type, credentials: {} }));
                }} className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                  <ToggleGroupItem value="onebot_reverse">{t("config.imOneBotModeReverse")}</ToggleGroupItem>
                  <ToggleGroupItem value="onebot">{t("config.imOneBotModeForward")}</ToggleGroupItem>
                </ToggleGroup>
                <p className="text-[11px] text-muted-foreground">
                  {editingBot.type === "onebot_reverse" ? t("config.imOneBotModeReverseHint") : t("config.imOneBotModeForwardHint")}
                </p>
              </div>
            )}

            {/* WeWork mode selector */}
            {WEWORK_TYPES.has(editingBot.type) && (
              <div className="space-y-1.5">
                <Label>{t("config.imWeworkMode")}</Label>
                <ToggleGroup type="single" variant="outline" size="sm" value={editingBot.type} onValueChange={(v) => {
                  if (v && v !== editingBot.type) setEditingBot((p) => ({ ...p, type: v as typeof editingBot.type, credentials: {} }));
                }} className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                  <ToggleGroupItem value="wework_ws">{t("config.imWeworkModeWs")}</ToggleGroupItem>
                  <ToggleGroupItem value="wework">{t("config.imWeworkModeHttp")}</ToggleGroupItem>
                </ToggleGroup>
                <p className="text-[11px] text-muted-foreground">
                  {editingBot.type === "wework_ws" ? t("config.imWeworkModeWsHint") : t("config.imWeworkModeHttpHint")}
                </p>
              </div>
            )}

            {/* WeCom WS: QR onboard */}
            {editingBot.type === "wework_ws" && venvDir && (
              <Button variant="outline" className="w-full border-dashed border-primary text-primary" onClick={() => setShowWecomQR(true)}>
                {t("wecom.qrScanCreate")}
              </Button>
            )}

            {/* Credentials */}
            <div className="space-y-2.5">
              <Label className="text-xs font-semibold">{t("im.botCredentials")}</Label>
              {credFields.map((field) => (
                <div key={field.key} className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground/60">{t(field.label, { defaultValue: field.label })}</Label>
                  <div className="flex gap-1">
                    <Input
                      type={field.secret && !revealedSecrets.has(field.key) ? "password" : "text"}
                      value={String(editingBot.credentials[field.key] ?? "")}
                      onChange={(e) => updateCredential(field.key, e.target.value)}
                      placeholder={field.placeholder ? t(field.placeholder, { defaultValue: field.placeholder }) : undefined}
                      className="flex-1 text-xs"
                    />
                    {field.secret && (
                      <Button variant="outline" size="sm" className="h-9 px-2 text-[11px] shrink-0"
                        onClick={() => setRevealedSecrets((prev) => {
                          const next = new Set(prev);
                          if (next.has(field.key)) next.delete(field.key); else next.add(field.key);
                          return next;
                        })}
                      >
                        {revealedSecrets.has(field.key) ? t("skills.hide") : t("skills.show")}
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Telegram extras */}
            {editingBot.type === "telegram" && (
              <div className="space-y-2">
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <Checkbox
                    checked={editingBot.credentials.require_pairing === "true" || editingBot.credentials.require_pairing === true || editingBot.credentials.require_pairing === undefined}
                    onCheckedChange={(v) => updateCredential("require_pairing", v ? "true" : "false")}
                  />
                  <span className="text-xs">{t("config.imPairing")}</span>
                </label>
                <div className="flex items-center gap-1.5 flex-wrap text-[11px] text-muted-foreground/70 leading-6">
                  <span>🔑 {t("config.imCurrentPairingCode")}：</span>
                  {tgPairingLoading ? (
                    <span className="opacity-50">...</span>
                  ) : tgPairingCode ? (
                    <code className="bg-muted px-2 py-0.5 rounded text-xs font-semibold tracking-widest select-all">{tgPairingCode}</code>
                  ) : (
                    <span className="opacity-50">{t("config.imPairingCodeNotGenerated")}</span>
                  )}
                  <Button variant="outline" size="sm" className="h-5 px-2 text-[11px] gap-1" onClick={loadTgPairingCode} disabled={tgPairingLoading}>
                    <IconRefresh size={11} /> {t("common.refresh")}
                  </Button>
                </div>
              </div>
            )}

            {/* QQ Bot extras */}
            {editingBot.type === "qqbot" && (
              <div className="space-y-2.5">
                {venvDir && (
                  <Button variant="outline" className="w-full border-dashed border-primary text-primary" onClick={() => setShowQQBotQR(true)}>
                    {t("qqbot.qrScanCreate")}
                  </Button>
                )}
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <Checkbox
                    checked={editingBot.credentials.sandbox === "true" || editingBot.credentials.sandbox === true}
                    onCheckedChange={(v) => updateCredential("sandbox", v ? "true" : "false")}
                  />
                  <span className="text-xs">{t("config.imQQBotSandbox")}</span>
                </label>
                <div className="space-y-1">
                  <Label className="text-[11px] text-muted-foreground/60">{t("config.imQQBotMode")}</Label>
                  <ToggleGroup type="single" variant="outline" size="sm" value={String(editingBot.credentials.mode || "websocket")} onValueChange={(v) => { if (v) updateCredential("mode", v); }} className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                    <ToggleGroupItem value="websocket">WebSocket</ToggleGroupItem>
                    <ToggleGroupItem value="webhook">Webhook</ToggleGroupItem>
                  </ToggleGroup>
                  <p className="text-[11px] text-muted-foreground">
                    {(String(editingBot.credentials.mode || "websocket")) === "websocket"
                      ? t("config.imQQBotModeWsHint")
                      : t("config.imQQBotModeWhHint")}
                  </p>
                </div>
              </div>
            )}

            {/* Feishu extras */}
            {editingBot.type === "feishu" && (
              <div className="space-y-3">
                {venvDir && (
                  <Button variant="outline" className="w-full border-dashed border-primary text-primary" onClick={() => setShowFeishuQR(true)}>
                    {t("feishu.qrScanCreate")}
                  </Button>
                )}
                <div className="border-t" />
                <Label className="text-xs font-semibold">{t("feishu.streaming")}</Label>
                <label className="flex items-center justify-between p-2.5 rounded-lg border cursor-pointer select-none">
                  <span className="text-sm">{t("feishu.streaming")}</span>
                  <Switch checked={streamingEnabled} onCheckedChange={(v) => updateCredential("streaming_enabled", v ? "true" : "false")} />
                </label>
                {streamingEnabled && (
                  <label className="flex items-center justify-between p-2.5 rounded-lg border cursor-pointer select-none ml-3">
                    <span className="text-sm">{t("feishu.groupStreaming")}</span>
                    <Switch checked={groupStreamingEnabled} onCheckedChange={(v) => updateCredential("group_streaming", v ? "true" : "false")} />
                  </label>
                )}
                <div className="space-y-1.5">
                  <Label>{t("feishu.groupMode")}</Label>
                  <ToggleGroup type="single" variant="outline" size="sm"
                    value={String(editingBot.credentials.group_response_mode || "mention_only")}
                    onValueChange={(v) => { if (v) updateCredential("group_response_mode", v); }}
                    className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground"
                  >
                    {(["mention_only", "smart", "always"] as const).map((m) => (
                      <ToggleGroupItem key={m} value={m}>{t(`feishu.groupMode_${m}`)}</ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                  {(editingBot.credentials.group_response_mode === "smart" || editingBot.credentials.group_response_mode === "always") && (
                    <p className="text-[11px] text-amber-600 dark:text-amber-400 leading-relaxed">{t("feishu.groupModeHint")}</p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <SheetFooter className="border-t p-4">
            <Button variant="outline" onClick={closeEditor}>{t("common.cancel")}</Button>
            <Button onClick={handleSave} disabled={saving || !editingBot.id.trim()}>
              {saving ? "..." : t("im.botSaveOnly")}
            </Button>
            <Button className="btnApplyRestart" onClick={handleSaveAndRestart} disabled={saving || !editingBot.id.trim()} title={t("im.botApplyRestartHint")}>
              {saving ? "..." : t("im.botApplyRestart")}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      {showFeishuQR && venvDir && (
        <FeishuQRModal
          venvDir={venvDir}
          apiBaseUrl={apiBaseUrl}
          onClose={() => setShowFeishuQR(false)}
          onSuccess={(appId, appSecret) => {
            updateCredential("app_id", appId);
            updateCredential("app_secret", appSecret);
            setShowFeishuQR(false);
          }}
        />
      )}

      {showQQBotQR && venvDir && (
        <QQBotQRModal
          venvDir={venvDir}
          apiBaseUrl={apiBaseUrl}
          onClose={() => setShowQQBotQR(false)}
          onSuccess={(appId, appSecret) => {
            updateCredential("app_id", appId);
            updateCredential("app_secret", appSecret);
            setShowQQBotQR(false);
          }}
        />
      )}

      {showWecomQR && venvDir && (
        <WecomQRModal
          venvDir={venvDir}
          apiBaseUrl={apiBaseUrl}
          onClose={() => setShowWecomQR(false)}
          onSuccess={(botId, secret) => {
            updateCredential("bot_id", botId);
            updateCredential("secret", secret);
            setShowWecomQR(false);
          }}
        />
      )}
    </div>
  );
}

// ─── Helper Components ──────────────────────────────────────────────────

function MediaContent({ content }: { content: string }) {
  const mediaPattern = /\[(图片|语音转文字|语音|文件|image|voice|file)[:\uff1a]\s*([^\]]*)\]/gi;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match;

  while ((match = mediaPattern.exec(content)) !== null) {
    if (match.index > lastIndex) {
      parts.push(<span key={lastIndex}>{content.slice(lastIndex, match.index)}</span>);
    }
    const type = match[1].toLowerCase();
    const ref = match[2];
    const isImage = type.includes("图片") || type === "image";
    const isVoice = type.includes("语音") || type === "voice";

    parts.push(
      <span key={match.index} className="imMediaCard">
        {isImage ? <IconImage size={14} /> : isVoice ? <IconVolume size={14} /> : <IconFile size={14} />}
        <span>{ref || match[0]}</span>
      </span>
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    parts.push(<span key={lastIndex}>{content.slice(lastIndex)}</span>);
  }

  return <>{parts.length > 0 ? parts : content}</>;
}

function IMChainSummary({ chain }: { chain: ChainSummaryItem[] }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className="imChainSummary"
      onClick={() => setExpanded(v => !v)}
      style={{ cursor: "pointer" }}
    >
      <div style={{ fontSize: 11, opacity: 0.5, marginBottom: 2 }}>
        {t("chat.chainSummary")} ({chain.length})
        <span style={{ marginLeft: 4, fontSize: 10 }}>{expanded ? "▼" : "▶"}</span>
      </div>
      {expanded && chain.map((item, idx) => (
        <div key={idx} className="imChainGroup">
          {item.context_compressed && (
            <div className="imChainCompressedLine">
              {t("chat.contextCompressed", {
                before: Math.round(item.context_compressed.before_tokens / 1000),
                after: Math.round(item.context_compressed.after_tokens / 1000),
              })}
            </div>
          )}
          {item.thinking_preview && (
            <div className="imChainThinkingLine">
              {t("chat.thoughtFor", { seconds: (item.thinking_duration_ms / 1000).toFixed(1) })}
              {" — "}
              {item.thinking_preview}
            </div>
          )}
          {item.tools.map((tool, ti) => (
            <div key={ti} className="imChainToolLine">
              {tool.name}{tool.input_preview ? `: ${tool.input_preview}` : ""}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
