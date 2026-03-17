// ─── IMView: IM Channel Viewer + Bot Configuration ───

import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  IconIM, IconMessageCircle, IconRefresh, IconFile, IconImage, IconVolume,
  IconBot, IconPlus, IconEdit, IconTrash,
  DotGreen, DotGray,
} from "../icons";
import { safeFetch } from "../providers";
import { ModalOverlay } from "../components/ModalOverlay";
import { logger } from "../platform";
import { IS_WEB, onWsEvent } from "../platform";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

// ─── Types ──────────────────────────────────────────────────────────────

type IMChannel = {
  channel: string;
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
  state: string;
  lastActive: string;
  messageCount: number;
  lastMessage: string | null;
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

const BOT_TYPES = ["feishu", "telegram", "dingtalk", "wework", "wework_ws", "onebot", "onebot_reverse", "qqbot"] as const;

const BOT_TYPE_LABELS: Record<string, string> = {
  feishu: "飞书",
  telegram: "Telegram",
  dingtalk: "钉钉",
  wework: "企业微信(HTTP)",
  wework_ws: "企业微信(WS)",
  onebot: "OneBot (正向WS)",
  onebot_reverse: "OneBot (反向WS)",
  qqbot: "QQ 官方机器人",
};

const WEWORK_TYPES = new Set(["wework", "wework_ws"]);
const ONEBOT_TYPES = new Set(["onebot", "onebot_reverse"]);

const CREDENTIAL_FIELDS: Record<string, { key: string; label: string; secret?: boolean }[]> = {
  feishu: [
    { key: "app_id", label: "App ID" },
    { key: "app_secret", label: "App Secret", secret: true },
  ],
  telegram: [
    { key: "bot_token", label: "Bot Token", secret: true },
    { key: "webhook_url", label: "Webhook URL" },
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
  multiAgentEnabled = false,
  apiBaseUrl,
  onRequestRestart,
}: {
  serviceRunning: boolean;
  multiAgentEnabled?: boolean;
  apiBaseUrl?: string;
  onRequestRestart?: () => void;
}) {
  const { t } = useTranslation();
  const api = apiBaseUrl ?? DEFAULT_API;
  const [tab, setTab] = useState<"messages" | "bots">("messages");
  const [channels, setChannels] = useState<IMChannel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [sessions, setSessions] = useState<IMSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<IMMessage[]>([]);
  const [totalMessages, setTotalMessages] = useState(0);
  const [loading, setLoading] = useState(false);

  const getChannelDisplayName = useCallback((ch: IMChannel): string => {
    const key = `status.${(ch.channel || "").toLowerCase()}`;
    const translated = t(key);
    return translated && translated !== key ? translated : (ch.name || ch.channel);
  }, [t]);

  const fetchChannels = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${api}/api/im/channels`);
      const data = await res.json();
      setChannels(data.channels || []);
    } catch { /* ignore */ }
  }, [serviceRunning, api]);

  const fetchSessions = useCallback(async (channel: string): Promise<IMSession[]> => {
    if (!serviceRunning) return [];
    try {
      const res = await safeFetch(`${api}/api/im/sessions?channel=${encodeURIComponent(channel)}`);
      const data = await res.json();
      const list: IMSession[] = data.sessions || [];
      setSessions(list);
      return list;
    } catch { /* ignore */ }
    return [];
  }, [serviceRunning, api]);

  const fetchMessages = useCallback(async (sessionId: string, limit = 50, offset = 0) => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${api}/api/im/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}&offset=${offset}`);
      const data = await res.json();
      setMessages(data.messages || []);
      setTotalMessages(data.total || 0);
    } catch { /* ignore */ }
  }, [serviceRunning, api]);

  useEffect(() => {
    fetchChannels();
  }, [fetchChannels]);

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
    fetchMessages(selectedSessionId);
    const msgTimer = setInterval(() => {
      fetchMessages(selectedSessionId);
    }, IS_WEB ? 30_000 : 8000);
    return () => clearInterval(msgTimer);
  }, [serviceRunning, selectedSessionId, fetchMessages]);

  useEffect(() => {
    if (!IS_WEB) return;
    return onWsEvent((event) => {
      if (event === "im:channel_status") fetchChannels();
      if (event === "im:new_message") {
        if (selectedChannel) fetchSessions(selectedChannel);
        if (selectedSessionId) fetchMessages(selectedSessionId);
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
      fetchMessages(first.sessionId);
    }
  }, [fetchSessions, fetchMessages]);

  const handleSelectSession = useCallback((sid: string) => {
    setSelectedSessionId(sid);
    fetchMessages(sid);
  }, [fetchMessages]);

  if (!serviceRunning) {
    return (
      <div className="imViewEmpty">
        <IconIM size={48} />
        <div style={{ marginTop: 12, fontWeight: 600 }}>{t("im.channels")}</div>
        <div style={{ marginTop: 4, opacity: 0.5, fontSize: 13 }}>后端服务未启动，请启动后再进行使用</div>
      </div>
    );
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Tabs */}
      {multiAgentEnabled && (
        <div style={{
          display: "flex", gap: 0, borderBottom: "1px solid var(--line)",
          padding: "0 16px", background: "var(--panel)", flexShrink: 0,
        }}>
          {(["messages", "bots"] as const).map((key) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              style={{
                padding: "10px 20px", border: "none", cursor: "pointer",
                background: "transparent", fontSize: 13, fontWeight: tab === key ? 600 : 400,
                color: tab === key ? "var(--primary, #3b82f6)" : "inherit",
                borderBottom: tab === key ? "2px solid var(--primary, #3b82f6)" : "2px solid transparent",
                transition: "all 0.15s",
              }}
            >
              {key === "messages" ? t("im.tabMessages") : t("im.tabBots")}
            </button>
          ))}
        </div>
      )}

      {/* Tab Content */}
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {tab === "messages" ? (
          <MessagesTab serviceRunning={serviceRunning} apiBase={api} />
        ) : (
          <BotConfigTab apiBase={api} multiAgentEnabled={multiAgentEnabled} onRequestRestart={onRequestRestart} />
        )}
      </div>
    </div>
  );
}

// ─── Messages Tab (original IM view) ────────────────────────────────────

function MessagesTab({ serviceRunning, apiBase }: { serviceRunning: boolean; apiBase: string }) {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<IMChannel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [sessions, setSessions] = useState<IMSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<IMMessage[]>([]);
  const [totalMessages, setTotalMessages] = useState(0);

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

  const fetchMessages = useCallback(async (sessionId: string, limit = 50, offset = 0) => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${apiBase}/api/im/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}&offset=${offset}`);
      const data = await res.json();
      setMessages(data.messages || []);
      setTotalMessages(data.total || 0);
    } catch { /* ignore */ }
  }, [serviceRunning, apiBase]);

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
    fetchMessages(selectedSessionId);
    const msgTimer = setInterval(() => { fetchMessages(selectedSessionId); }, IS_WEB ? 30_000 : 8000);
    return () => clearInterval(msgTimer);
  }, [serviceRunning, selectedSessionId, fetchMessages]);

  useEffect(() => {
    if (!IS_WEB) return;
    return onWsEvent((event) => {
      if (event === "im:channel_status") fetchChannels();
      if (event === "im:new_message") {
        if (selectedChannel) fetchSessions(selectedChannel);
        if (selectedSessionId) fetchMessages(selectedSessionId);
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
      fetchMessages(first.sessionId);
    }
  }, [fetchSessions, fetchMessages]);

  const handleSelectSession = useCallback((sid: string) => {
    setSelectedSessionId(sid);
    fetchMessages(sid);
  }, [fetchMessages]);

  return (
    <div className="imView">
      <div className="imLeft">
        <div className="imSectionTitle">
          <span>{t("im.channels")}</span>
          <button className="imRefreshBtn" onClick={fetchChannels} title={t("topbar.refresh")}><IconRefresh size={13} /></button>
        </div>
        <div className="imChannelList">
          {channels.length === 0 && <div className="imEmptyHint">{t("im.noChannels")}</div>}
          {channels.map((ch) => (
            <div
              key={ch.channel}
              className={`imChannelItem ${selectedChannel === ch.channel ? "imChannelItemActive" : ""}`}
              onClick={() => handleSelectChannel(ch.channel)}
              role="button"
              tabIndex={0}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                {ch.status === "online" ? <DotGreen /> : <DotGray />}
                <span className="imChannelName">{getChannelDisplayName(ch)}</span>
              </div>
              <span className="imChannelCount">{ch.sessionCount}</span>
            </div>
          ))}
        </div>

        {selectedChannel && (
          <>
            <div className="imSectionTitle" style={{ marginTop: 8 }}>
              <span>{t("im.sessions")}</span>
            </div>
            <div className="imSessionList">
              {sessions.length === 0 && <div className="imEmptyHint">{t("im.noSessions")}</div>}
              {sessions.map((s) => (
                <div
                  key={s.sessionId}
                  className={`imSessionItem ${selectedSessionId === s.sessionId ? "imSessionItemActive" : ""}`}
                  onClick={() => handleSelectSession(s.sessionId)}
                  role="button"
                  tabIndex={0}
                >
                  <div className="imSessionId">{s.userId || s.chatId || s.sessionId.slice(0, 12)}</div>
                  <div className="imSessionMeta">
                    {s.messageCount} {t("im.messages")} · {s.lastActive ? new Date(s.lastActive).toLocaleTimeString() : ""}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="imRight">
        {!selectedSessionId ? (
          <div className="imViewEmpty">
            <IconMessageCircle size={40} />
            <div style={{ marginTop: 8, opacity: 0.5, fontSize: 13 }}>{t("im.noMessages")}</div>
          </div>
        ) : (
          <div className="imMessages">
            <div className="imMessagesHeader">
              <span>{t("im.messages")} ({totalMessages})</span>
            </div>
            <div className="imMessagesList">
              {messages.map((msg, idx) => (
                <div key={idx} className={`imMsg ${msg.role === "user" ? "imMsgUser" : "imMsgBot"}`}>
                  <div className="imMsgRole">
                    {msg.role === "user" ? t("im.user") : msg.role === "system" ? t("im.system") : t("im.bot")}
                    <span className="imMsgTime">{msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : ""}</span>
                  </div>
                  {msg.role !== "user" && msg.chain_summary && msg.chain_summary.length > 0 && (
                    <IMChainSummary chain={msg.chain_summary} />
                  )}
                  <div className="imMsgContent">
                    <MediaContent content={msg.content} />
                  </div>
                </div>
              ))}
              {messages.length === 0 && <div className="imEmptyHint">{t("im.noMessages")}</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Bot Configuration Tab ──────────────────────────────────────────────

function BotConfigTab({ apiBase, multiAgentEnabled, onRequestRestart }: { apiBase: string; multiAgentEnabled: boolean; onRequestRestart?: () => void }) {
  const { t } = useTranslation();
  const [bots, setBots] = useState<IMBot[]>([]);
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingBot, setEditingBot] = useState<IMBot>(EMPTY_BOT);
  const [isCreating, setIsCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [toastMsg, setToastMsg] = useState<{ text: string; type: "ok" | "err" } | null>(null);
  const [revealedSecrets, setRevealedSecrets] = useState<Set<string>>(new Set());

  const showToast = useCallback((text: string, type: "ok" | "err" = "ok") => {
    setToastMsg({ text, type });
    setTimeout(() => setToastMsg(null), 3500);
  }, []);

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
    if (multiAgentEnabled) {
      fetchBots();
      fetchProfiles();
    }
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
      showToast(t("im.botSaveSuccess"), "ok");
    } catch (e) {
      showToast(String(e) || t("im.botSaveFailed"), "err");
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
      showToast(t("im.botSaveSuccess"), "ok");
      onRequestRestart?.();
    } catch (e) {
      showToast(String(e) || t("im.botSaveFailed"), "err");
    }
    setSaving(false);
  };

  const handleDelete = async (botId: string) => {
    try {
      await safeFetch(`${apiBase}/api/agents/bots/${botId}`, { method: "DELETE" });
      setConfirmDeleteId(null);
      fetchBots();
      showToast(t("im.botDeleteSuccess"), "ok");
    } catch (e) {
      showToast(String(e) || t("im.botDeleteFailed"), "err");
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
      showToast(t("im.botToggleSuccess"), "ok");
    } catch { /* ignore */ }
  };

  const updateCredential = (key: string, value: string) => {
    setEditingBot((prev) => ({
      ...prev,
      credentials: { ...prev.credentials, [key]: value },
    }));
  };

  const credFields = CREDENTIAL_FIELDS[editingBot.type] || [];

  if (!multiAgentEnabled) {
    return (
      <div style={{ padding: 40, textAlign: "center", opacity: 0.5 }}>
        <IconBot size={48} />
        <div style={{ marginTop: 12, fontWeight: 700 }}>{t("im.needMultiAgent")}</div>
      </div>
    );
  }

  return (
    <div style={{ padding: 20, position: "relative" }}>
      {/* Toast */}
      {toastMsg && (
        <div style={{
          position: "fixed", top: 20, right: 20, zIndex: 9999,
          padding: "10px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
          background: toastMsg.type === "ok" ? "var(--ok, #10b981)" : "#ef4444",
          color: "#fff", boxShadow: "0 4px 16px rgba(0,0,0,0.15)",
          animation: "fadeIn 0.2s",
        }}>
          {toastMsg.text}
        </div>
      )}

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <IconBot size={24} />
        <div>
          <h2 style={{ margin: 0, fontSize: 18 }}>{t("im.botsTitle")}</h2>
          <div style={{ fontSize: 12, opacity: 0.5, marginTop: 2 }}>{t("im.botsDesc")}</div>
        </div>
        <div style={{ flex: 1 }} />
        <button
          onClick={fetchBots}
          disabled={loading}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderRadius: 8, border: "1px solid var(--line)",
            background: "var(--panel)", cursor: "pointer", fontSize: 13,
          }}
        >
          <IconRefresh size={14} />
        </button>
        <button
          onClick={openCreate}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 14px", borderRadius: 8, border: "none",
            background: "var(--primary, #3b82f6)", color: "#fff",
            cursor: "pointer", fontSize: 13, fontWeight: 600,
          }}
        >
          <IconPlus size={14} />
          {t("im.createBot")}
        </button>
      </div>

      {/* Bot Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 14 }}>
        {bots.map((bot) => {
          const agentProfile = profiles.find((p) => p.id === bot.agent_profile_id);
          return (
            <div
              key={bot.id}
              style={{
                padding: 16, borderRadius: 12,
                background: "var(--panel)", border: "1px solid var(--line)",
                position: "relative", overflow: "hidden",
                opacity: bot.enabled ? 1 : 0.55,
                transition: "box-shadow 0.2s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 2px 12px rgba(0,0,0,0.08)")}
              onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "none")}
            >
              {/* Type badge */}
              <div style={{ position: "absolute", top: 8, right: 8, display: "flex", gap: 4 }}>
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 4,
                  background: "rgba(99,102,241,0.12)", color: "#6366f1",
                }}>
                  {BOT_TYPE_LABELS[bot.type] || bot.type}
                </span>
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 4,
                  background: bot.enabled ? "rgba(16,185,129,0.12)" : "rgba(239,68,68,0.12)",
                  color: bot.enabled ? "#10b981" : "#ef4444",
                }}>
                  {bot.enabled ? t("im.botEnabled") : t("im.botDisabled")}
                </span>
              </div>

              {/* Content */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, marginTop: 2 }}>
                <span style={{ fontSize: 24, lineHeight: 1 }}>{agentProfile?.icon || "🤖"}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {bot.name || bot.id}
                  </div>
                  <div style={{ fontSize: 11, opacity: 0.45, fontFamily: "monospace" }}>{bot.id}</div>
                </div>
              </div>
              <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 10 }}>
                {t("im.botAgent")}: {agentProfile?.name || bot.agent_profile_id}
              </div>

              {/* Actions */}
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={() => handleToggle(bot)}
                  style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                    background: "transparent", cursor: "pointer", fontSize: 12,
                    color: bot.enabled ? "#ef4444" : "#10b981",
                  }}
                >
                  {bot.enabled ? t("scheduler.disable") : t("scheduler.enable")}
                </button>
                <button
                  onClick={() => openEdit(bot)}
                  style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                    background: "transparent", cursor: "pointer", fontSize: 12,
                  }}
                >
                  <IconEdit size={12} />
                  {t("agentManager.edit")}
                </button>
                <button
                  onClick={() => setConfirmDeleteId(bot.id)}
                  style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                    background: "transparent", cursor: "pointer", fontSize: 12,
                    color: "#ef4444",
                  }}
                >
                  <IconTrash size={12} />
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {bots.length === 0 && !loading && (
        <div style={{ textAlign: "center", padding: 40, opacity: 0.5 }}>
          <IconBot size={40} />
          <div style={{ marginTop: 12 }}>{t("im.noBots")}</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>{t("im.noBotsHint")}</div>
        </div>
      )}

      {/* Delete confirmation overlay */}
      {confirmDeleteId && (
        <ModalOverlay onClose={() => setConfirmDeleteId(null)} className="" style={{
          position: "fixed", inset: 0, zIndex: 9000,
          background: "rgba(0,0,0,0.35)", display: "flex",
          alignItems: "center", justifyContent: "center",
        }}>
          <div style={{
            background: "var(--panel)", borderRadius: 12, padding: 24,
            minWidth: 320, boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
          }}>
            <div style={{ fontWeight: 600, marginBottom: 12 }}>{t("im.botConfirmDelete")}</div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                onClick={() => setConfirmDeleteId(null)}
                style={{
                  padding: "6px 16px", borderRadius: 6, border: "1px solid var(--line)",
                  background: "transparent", cursor: "pointer", fontSize: 13,
                }}
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={() => handleDelete(confirmDeleteId)}
                style={{
                  padding: "6px 16px", borderRadius: 6, border: "none",
                  background: "#ef4444", color: "#fff", cursor: "pointer",
                  fontSize: 13, fontWeight: 600,
                }}
              >
                {t("common.delete")}
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Slide-in Editor Panel */}
      {editorOpen && (
        <ModalOverlay onClose={closeEditor} className="" style={{
          position: "fixed", inset: 0, zIndex: 7999,
          background: "rgba(15, 23, 42, 0.45)", backdropFilter: "blur(4px)",
          display: "flex", justifyContent: "flex-end",
        }}>
        <div style={{
          position: "relative", width: 420, height: "100%",
          background: "var(--panel)", borderLeft: "1px solid var(--line)",
          boxShadow: "-4px 0 24px rgba(0,0,0,0.12)",
          display: "flex", flexDirection: "column",
          animation: "slideInRight 0.2s ease-out",
        }}>
          {/* Editor header */}
          <div style={{
            padding: "16px 20px", borderBottom: "1px solid var(--line)",
            display: "flex", alignItems: "center", gap: 10,
          }}>
            <h3 style={{ margin: 0, fontSize: 16, flex: 1 }}>
              {isCreating ? t("im.createBot") : t("im.editBot")}
            </h3>
            <button
              onClick={closeEditor}
              style={{
                border: "none", background: "transparent", cursor: "pointer",
                fontSize: 18, opacity: 0.5, padding: "4px 8px",
              }}
            >
              ✕
            </button>
          </div>

          {/* Editor body */}
          <div style={{ flex: 1, overflow: "auto", padding: "16px 20px" }}>
            {/* Bot ID */}
            <label style={{ display: "block", marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{t("im.botId")}</div>
              <input
                value={editingBot.id}
                onChange={(e) => setEditingBot((p) => ({ ...p, id: e.target.value.replace(/[^a-z0-9_-]/gi, "").toLowerCase() }))}
                disabled={!isCreating}
                placeholder="my-feishu-bot"
                style={{
                  width: "100%", padding: "8px 10px", borderRadius: 6,
                  border: "1px solid var(--line)", background: isCreating ? "var(--bg)" : "var(--panel)",
                  fontSize: 13, boxSizing: "border-box",
                }}
              />
              {isCreating && (
                <div style={{ fontSize: 11, opacity: 0.4, marginTop: 2 }}>{t("im.botIdHint")}</div>
              )}
            </label>

            {/* Bot Name */}
            <label style={{ display: "block", marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{t("im.botName")}</div>
              <input
                value={editingBot.name}
                onChange={(e) => setEditingBot((p) => ({ ...p, name: e.target.value }))}
                placeholder="My Bot"
                style={{
                  width: "100%", padding: "8px 10px", borderRadius: 6,
                  border: "1px solid var(--line)", background: "var(--bg)",
                  fontSize: 13, boxSizing: "border-box",
                }}
              />
            </label>

            {/* Bot Type */}
            <label style={{ display: "block", marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{t("im.botType")}</div>
              <select
                value={WEWORK_TYPES.has(editingBot.type) ? "wework_ws" : ONEBOT_TYPES.has(editingBot.type) ? "onebot_reverse" : editingBot.type}
                onChange={(e) => {
                  const val = e.target.value;
                  setEditingBot((p) => ({ ...p, type: val, credentials: {} }));
                }}
                disabled={!isCreating}
                style={{
                  width: "100%", padding: "8px 10px", borderRadius: 6,
                  border: "1px solid var(--line)", background: isCreating ? "var(--bg)" : "var(--panel)",
                  fontSize: 13, boxSizing: "border-box",
                }}
              >
                {BOT_TYPES.filter((bt) => bt !== "wework" && bt !== "onebot").map((bt) => (
                  <option key={bt} value={bt}>
                    {bt === "wework_ws" ? "企业微信" : bt === "onebot_reverse" ? "OneBot" : (BOT_TYPE_LABELS[bt] || bt)}
                  </option>
                ))}
              </select>
            </label>

            {/* Agent Profile */}
            <label style={{ display: "block", marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{t("im.botAgent")}</div>
              <select
                value={editingBot.agent_profile_id}
                onChange={(e) => setEditingBot((p) => ({ ...p, agent_profile_id: e.target.value }))}
                style={{
                  width: "100%", padding: "8px 10px", borderRadius: 6,
                  border: "1px solid var(--line)", background: "var(--bg)",
                  fontSize: 13, boxSizing: "border-box",
                }}
              >
                <option value="default">{t("im.botAgentDefault")}</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.icon} {p.name} ({p.id})
                  </option>
                ))}
              </select>
            </label>

            {/* Enabled toggle */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{t("im.botEnabled")}</span>
              <div
                onClick={() => setEditingBot((p) => ({ ...p, enabled: !p.enabled }))}
                style={{
                  width: 40, height: 22, borderRadius: 11, cursor: "pointer",
                  background: editingBot.enabled ? "var(--ok, #10b981)" : "var(--line)",
                  position: "relative", transition: "background 0.2s",
                }}
              >
                <div style={{
                  width: 18, height: 18, borderRadius: 9, background: "#fff",
                  position: "absolute", top: 2,
                  left: editingBot.enabled ? 20 : 2,
                  transition: "left 0.2s", boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                }} />
              </div>
            </div>

            {/* OneBot mode selector */}
            {ONEBOT_TYPES.has(editingBot.type) && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{t("config.imOneBotMode")}</div>
                <ToggleGroup type="single" variant="outline" size="sm" value={editingBot.type} onValueChange={(v) => {
                  if (v && v !== editingBot.type) setEditingBot((p) => ({ ...p, type: v as typeof editingBot.type, credentials: {} }));
                }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                  <ToggleGroupItem value="onebot_reverse">{t("config.imOneBotModeReverse")}</ToggleGroupItem>
                  <ToggleGroupItem value="onebot">{t("config.imOneBotModeForward")}</ToggleGroupItem>
                </ToggleGroup>
                <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                  {editingBot.type === "onebot_reverse" ? t("config.imOneBotModeReverseHint") : t("config.imOneBotModeForwardHint")}
                </div>
              </div>
            )}

            {/* WeWork mode selector */}
            {WEWORK_TYPES.has(editingBot.type) && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{t("config.imWeworkMode")}</div>
                <ToggleGroup type="single" variant="outline" size="sm" value={editingBot.type} onValueChange={(v) => {
                  if (v && v !== editingBot.type) setEditingBot((p) => ({ ...p, type: v as typeof editingBot.type, credentials: {} }));
                }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                  <ToggleGroupItem value="wework_ws">{t("config.imWeworkModeWs")}</ToggleGroupItem>
                  <ToggleGroupItem value="wework">{t("config.imWeworkModeHttp")}</ToggleGroupItem>
                </ToggleGroup>
                <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                  {editingBot.type === "wework_ws" ? t("config.imWeworkModeWsHint") : t("config.imWeworkModeHttpHint")}
                </div>
              </div>
            )}

            {/* Credentials */}
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{t("im.botCredentials")}</div>
            {credFields.map((field) => (
              <label key={field.key} style={{ display: "block", marginBottom: 10 }}>
                <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 2 }}>{field.label}</div>
                <div style={{ display: "flex", gap: 4 }}>
                  <input
                    type={field.secret && !revealedSecrets.has(field.key) ? "password" : "text"}
                    value={String(editingBot.credentials[field.key] ?? "")}
                    onChange={(e) => updateCredential(field.key, e.target.value)}
                    style={{
                      flex: 1, padding: "7px 10px", borderRadius: 6,
                      border: "1px solid var(--line)", background: "var(--bg)",
                      fontSize: 12, boxSizing: "border-box",
                    }}
                  />
                  {field.secret && (
                    <button
                      type="button"
                      onClick={() => setRevealedSecrets((prev) => {
                        const next = new Set(prev);
                        if (next.has(field.key)) next.delete(field.key);
                        else next.add(field.key);
                        return next;
                      })}
                      style={{
                        padding: "4px 8px", borderRadius: 6,
                        border: "1px solid var(--line)", background: "transparent",
                        cursor: "pointer", fontSize: 11,
                      }}
                    >
                      {revealedSecrets.has(field.key) ? t("skills.hide") : t("skills.show")}
                    </button>
                  )}
                </div>
              </label>
            ))}

            {/* QQ Bot: sandbox checkbox + mode toggle */}
            {editingBot.type === "qqbot" && (
              <>
                <label style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={editingBot.credentials.sandbox === "true" || editingBot.credentials.sandbox === true}
                    onChange={(e) => updateCredential("sandbox", e.target.checked ? "true" : "false")}
                    style={{ width: 16, height: 16 }}
                  />
                  <span style={{ fontSize: 12 }}>{t("config.imQQBotSandbox")}</span>
                </label>
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>{t("config.imQQBotMode")}</div>
                  <ToggleGroup type="single" variant="outline" size="sm" value={String(editingBot.credentials.mode || "websocket")} onValueChange={(v) => { if (v) updateCredential("mode", v); }} className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                    <ToggleGroupItem value="websocket">WebSocket</ToggleGroupItem>
                    <ToggleGroupItem value="webhook">Webhook</ToggleGroupItem>
                  </ToggleGroup>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                    {(String(editingBot.credentials.mode || "websocket")) === "websocket"
                      ? t("config.imQQBotModeWsHint")
                      : t("config.imQQBotModeWhHint")}
                  </div>
                </div>
              </>
            )}
          </div>

          {/* Editor footer */}
          <div style={{
            padding: "12px 20px", borderTop: "1px solid var(--line)",
            display: "flex", gap: 8, justifyContent: "flex-end",
          }}>
            <button
              onClick={closeEditor}
              style={{
                padding: "8px 18px", borderRadius: 8, border: "1px solid var(--line)",
                background: "transparent", cursor: "pointer", fontSize: 13,
              }}
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !editingBot.id.trim()}
              style={{
                padding: "8px 18px", borderRadius: 8, border: "none",
                background: "var(--primary, #3b82f6)", color: "#fff",
                cursor: saving ? "wait" : "pointer", fontSize: 13,
                fontWeight: 600, opacity: saving || !editingBot.id.trim() ? 0.5 : 1,
              }}
            >
              {saving ? "..." : t("im.botSaveOnly")}
            </button>
            <button
              className="btnApplyRestart"
              onClick={handleSaveAndRestart}
              disabled={saving || !editingBot.id.trim()}
              title={t("im.botApplyRestartHint")}
              style={{
                padding: "8px 18px", borderRadius: 8, border: "none",
                color: "#fff", cursor: saving ? "wait" : "pointer", fontSize: 13,
                fontWeight: 600, opacity: saving || !editingBot.id.trim() ? 0.5 : 1,
              }}
            >
              {saving ? "..." : t("im.botApplyRestart")}
            </button>
          </div>
        </div>
        </ModalOverlay>
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
