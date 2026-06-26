import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Bell, CheckCheck, ExternalLink, Inbox, Loader2, RefreshCw, Search, ShieldAlert, Trash2 } from "lucide-react";
import { safeFetch } from "../providers";
import { openExternalUrl } from "../platform";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "../components/ui/toggle-group";
import { INBOX_REFRESH_EVENT, INBOX_UNREAD_CHANGED_EVENT } from "../components/InboxBadge";
import { useMdModules } from "./chat/hooks/useMdModules";
import type { InboxListResponse, InboxMessage } from "../inboxTypes";
import { isHighPriorityInbox } from "../inboxTypes";

type InboxFilter = "all" | "unread" | "updates" | "important";

type InboxViewProps = {
  apiBaseUrl: string;
  serviceRunning: boolean;
  refreshKey?: number;
  onUnreadChange?: (count: number) => void;
};

const FILTERS: InboxFilter[] = ["all", "unread", "updates", "important"];

function isUnread(message: InboxMessage): boolean {
  return !message.read_at && !message.dismissed_at;
}

function formatDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function typeLabelKey(type: string): string {
  const key = String(type || "notice").toLowerCase();
  if (key === "update") return "inbox.typeUpdate";
  if (key === "security") return "inbox.typeSecurity";
  if (key === "activity") return "inbox.typeActivity";
  if (key === "tip") return "inbox.typeTip";
  return "inbox.typeNotice";
}

function priorityLabelKey(priority: string): string {
  const key = String(priority || "normal").toLowerCase();
  if (key === "critical") return "inbox.priorityCritical";
  if (key === "high") return "inbox.priorityHigh";
  if (key === "low") return "inbox.priorityLow";
  return "inbox.priorityNormal";
}

function messageIcon(message: InboxMessage) {
  const type = String(message.type || "").toLowerCase();
  if (type === "security") return <ShieldAlert size={18} />;
  if (type === "update") return <RefreshCw size={18} />;
  if (isHighPriorityInbox(message.priority)) return <AlertTriangle size={18} />;
  return <Bell size={18} />;
}

export function InboxView({
  apiBaseUrl,
  serviceRunning,
  refreshKey = 0,
  onUnreadChange,
}: InboxViewProps) {
  const { t } = useTranslation();
  const mdModules = useMdModules();
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [unreadCount, setUnreadCount] = useState(0);
  const [filter, setFilter] = useState<InboxFilter>("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const publishUnread = useCallback((count: number) => {
    const next = Math.max(0, count);
    setUnreadCount(next);
    onUnreadChange?.(next);
    window.dispatchEvent(
      new CustomEvent(INBOX_UNREAD_CHANGED_EVENT, {
        detail: { unreadCount: next },
      }),
    );
  }, [onUnreadChange]);

  const fetchMessages = useCallback(async (showLoading = false) => {
    if (!serviceRunning) {
      setMessages([]);
      publishUnread(0);
      setLoading(false);
      return;
    }
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/inbox/messages`, {
        signal: AbortSignal.timeout(8_000),
      });
      const data: InboxListResponse = await resp.json();
      const nextMessages = Array.isArray(data.messages) ? data.messages : [];
      setMessages(nextMessages);
      publishUnread(Number(data.unread_count || 0));
      setSelectedId((current) => {
        if (current && nextMessages.some((message) => message.id === current)) return current;
        return nextMessages[0]?.id || null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [apiBaseUrl, publishUnread, serviceRunning]);

  useEffect(() => {
    void fetchMessages(true);
  }, [fetchMessages, refreshKey]);

  useEffect(() => {
    const onRefresh = () => { void fetchMessages(false); };
    window.addEventListener(INBOX_REFRESH_EVENT, onRefresh);
    return () => window.removeEventListener(INBOX_REFRESH_EVENT, onRefresh);
  }, [fetchMessages]);

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return messages.filter((message) => {
      if (filter === "unread" && !isUnread(message)) return false;
      if (filter === "updates" && String(message.type || "").toLowerCase() !== "update") return false;
      if (filter === "important" && !isHighPriorityInbox(message.priority)) return false;
      if (!normalizedQuery) return true;
      return (
        String(message.title || "").toLowerCase().includes(normalizedQuery) ||
        String(message.body_markdown || "").toLowerCase().includes(normalizedQuery)
      );
    });
  }, [filter, messages, query]);

  const selected = useMemo(() => {
    if (!selectedId) return filtered[0] || messages[0] || null;
    return messages.find((message) => message.id === selectedId) || filtered[0] || null;
  }, [filtered, messages, selectedId]);

  const stats = useMemo(() => {
    const updates = messages.filter((message) => String(message.type || "").toLowerCase() === "update").length;
    const important = messages.filter((message) => isHighPriorityInbox(message.priority)).length;
    return { all: messages.length, unread: unreadCount, updates, important };
  }, [messages, unreadCount]);

  const refreshNow = useCallback(async () => {
    if (!serviceRunning || refreshing) return;
    setRefreshing(true);
    setError(null);
    try {
      await safeFetch(`${apiBaseUrl}/api/inbox/refresh`, { method: "POST" });
      await fetchMessages(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, [apiBaseUrl, fetchMessages, refreshing, serviceRunning]);

  const markEvent = useCallback(async (message: InboxMessage, event: "read" | "dismiss" | "clicked") => {
    if (!message?.id) return;
    setBusyId(`${event}:${message.id}`);
    try {
      const endpoint = event === "dismiss" ? "dismiss" : event;
      const resp = await safeFetch(`${apiBaseUrl}/api/inbox/messages/${encodeURIComponent(message.id)}/${endpoint}`, {
        method: "POST",
      });
      const data = await resp.json();
      if (typeof data?.unread_count === "number") publishUnread(data.unread_count);
      await fetchMessages(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }, [apiBaseUrl, fetchMessages, publishUnread]);

  const openCta = useCallback(async (message: InboxMessage) => {
    const url = message.cta?.url;
    if (typeof url !== "string" || !url.trim()) return;
    await markEvent(message, "clicked");
    await openExternalUrl(url);
  }, [markEvent]);

  if (!serviceRunning) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-sm text-center">
          <Inbox size={36} className="mx-auto mb-3 text-muted-foreground/35" />
          <h2 className="text-base font-semibold">{t("inbox.serviceNotRunning")}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t("inbox.serviceNotRunningHint")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="inboxView">
      <div className="inboxHeader">
        <div className="min-w-0">
          <h1 className="inboxTitle">{t("inbox.title")}</h1>
          <p className="inboxSubtitle">{t("inbox.description")}</p>
        </div>
        <Button variant="outline" size="sm" onClick={refreshNow} disabled={refreshing}>
          {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          {t("inbox.refresh")}
        </Button>
      </div>

      <div className="inboxToolbar">
        <ToggleGroup
          type="single"
          value={filter}
          onValueChange={(value) => { if (value) setFilter(value as InboxFilter); }}
          variant="outline"
          className="flex-wrap justify-start"
        >
          {FILTERS.map((item) => (
            <ToggleGroupItem
              key={item}
              value={item}
              className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
            >
              {t(`inbox.filter${item[0].toUpperCase()}${item.slice(1)}`)}
              <Badge
                variant="secondary"
                className="inboxFilterCount min-w-[1.25rem] rounded-full bg-foreground/10 px-1.5 py-0 text-[11px] text-foreground/60"
              >
                {stats[item]}
              </Badge>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="relative min-w-[220px] flex-1">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground/55" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("inbox.searchPlaceholder")}
            className="pl-8"
          />
        </div>
      </div>

      {error && (
        <div className="inboxError">
          <AlertTriangle size={14} />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="flex flex-1 items-center justify-center py-16 text-muted-foreground">
          <Loader2 size={24} className="mr-2 animate-spin" />
          {t("common.loading")}
        </div>
      ) : messages.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-16">
          <div className="text-center">
            <Inbox size={40} className="mx-auto mb-3 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">{t("inbox.empty")}</p>
          </div>
        </div>
      ) : (
        <div className="inboxLayout">
          <div className="inboxList">
            {filtered.length === 0 ? (
              <div className="p-8 text-center text-sm text-muted-foreground">{t("inbox.noResults")}</div>
            ) : filtered.map((message) => {
              const selectedRow = selected?.id === message.id;
              const unread = isUnread(message);
              const important = isHighPriorityInbox(message.priority);
              return (
                <button
                  key={message.id}
                  data-slot="inbox-list-item"
                  className={`inboxListItem${selectedRow ? " inboxListItemActive" : ""}${unread ? " inboxListItemUnread" : ""}`}
                  onClick={() => setSelectedId(message.id)}
                >
                  <span className={`inboxListIcon${important ? " inboxListIconHot" : ""}`}>
                    {messageIcon(message)}
                  </span>
                  <span className="inboxListBody">
                    <span className="inboxListTop">
                      <span className="inboxListTitle">{message.title || t("inbox.untitled")}</span>
                      {unread && <span className="inboxUnreadDot" />}
                    </span>
                    <span className="inboxListMeta">
                      {t(typeLabelKey(message.type))}
                      <span>·</span>
                      {t(priorityLabelKey(message.priority))}
                      {message.publish_at && <span>·</span>}
                      {message.publish_at && <span>{formatDate(message.publish_at)}</span>}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          <Card className="inboxDetail">
            {selected ? (
              <CardContent className="flex min-h-0 flex-1 flex-col p-0">
                <div className="inboxDetailHeader">
                  <div className="min-w-0">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <Badge variant={String(selected.type).toLowerCase() === "security" ? "destructive" : "secondary"}>
                        {t(typeLabelKey(selected.type))}
                      </Badge>
                      <Badge variant={isHighPriorityInbox(selected.priority) ? "destructive" : "outline"}>
                        {t(priorityLabelKey(selected.priority))}
                      </Badge>
                      {selected.source && <Badge variant="outline">{selected.source}</Badge>}
                    </div>
                    <h2 className="inboxDetailTitle">{selected.title || t("inbox.untitled")}</h2>
                    <p className="inboxDetailTime">
                      {formatDate(selected.publish_at || selected.received_at || null)}
                      {selected.expire_at ? ` · ${t("inbox.expiresAt", { time: formatDate(selected.expire_at) })}` : ""}
                    </p>
                  </div>
                  <div className="inboxDetailActions">
                    {!selected.read_at && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busyId === `read:${selected.id}`}
                        onClick={() => markEvent(selected, "read")}
                      >
                        <CheckCheck size={14} />
                        {t("inbox.markRead")}
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="text-muted-foreground hover:text-destructive"
                      disabled={busyId === `dismiss:${selected.id}`}
                      onClick={() => markEvent(selected, "dismiss")}
                      title={t("inbox.dismiss")}
                    >
                      <Trash2 size={15} />
                    </Button>
                  </div>
                </div>

                <div className="inboxDetailBody">
                  {mdModules ? (
                    <div className="feedbackMdContent inboxMarkdown">
                      <mdModules.ReactMarkdown
                        remarkPlugins={mdModules.remarkPlugins}
                        rehypePlugins={mdModules.rehypePlugins}
                      >
                        {selected.body_markdown || t("inbox.emptyBody")}
                      </mdModules.ReactMarkdown>
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap text-sm leading-7">{selected.body_markdown || t("inbox.emptyBody")}</p>
                  )}
                </div>

                {selected.cta?.url && (
                  <div className="inboxDetailFooter">
                    <Button onClick={() => openCta(selected)} disabled={busyId === `clicked:${selected.id}`}>
                      <ExternalLink size={14} />
                      {selected.cta.label || t("inbox.openLink")}
                    </Button>
                  </div>
                )}
              </CardContent>
            ) : (
              <CardContent className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                {t("inbox.selectMessage")}
              </CardContent>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
