import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { openExternalUrl } from "../platform";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ConfirmDialog } from "../components/ConfirmDialog";
import {
  IconRefresh, IconTrash, IconBug, IconZap, IconUser,
  IconChevronDown, IconChevronRight, IconLoader, IconMessageCircle,
  IconSend, IconPlus, IconSearch,
} from "../icons";
import { useMdModules } from "../hooks/useMdModules";
import { PublicFeedbackList } from "./PublicFeedbackList";

type FeedbackTab = "mine" | "all";

type FeedbackRecord = {
  report_id: string;
  has_token: boolean;
  title: string;
  type: "bug" | "feature";
  contact_email: string;
  submitted_at: string;
  cached_status: string;
  has_unread: boolean;
};

type DeveloperReply = {
  author: string;
  body: string;
  created_at: string;
  source?: string;
};

type FeedbackDetail = {
  status: string;
  summary?: string;
  created_at?: string;
  developer_replies?: DeveloperReply[];
  labels?: string[];
  source?: string;
  github_issue_url?: string;
};

type FilterTab = "all" | "active" | "resolved" | "unread";
type SortBy = "date" | "status" | "type";

type FeedbackPrefill = {
  mode?: "bug" | "feature";
  title?: string;
  description?: string;
};

type MyFeedbackViewProps = {
  apiBaseUrl: string;
  serviceRunning: boolean;
  onOpenFeedbackModal?: (prefill?: FeedbackPrefill) => void;
  refreshTrigger?: number;
};

const STATUS_STYLES: Record<string, { bg: string; text: string; border?: string }> = {
  pending: { bg: "bg-gray-100 dark:bg-gray-800", text: "text-gray-600 dark:text-gray-400" },
  open: { bg: "bg-blue-50 dark:bg-blue-900/30", text: "text-blue-600 dark:text-blue-400" },
  confirmed: { bg: "bg-orange-50 dark:bg-orange-900/30", text: "text-orange-600 dark:text-orange-400" },
  resolved: { bg: "bg-green-50 dark:bg-green-900/30", text: "text-green-600 dark:text-green-400" },
  closed: { bg: "bg-green-50 dark:bg-green-900/30", text: "text-green-600 dark:text-green-400" },
  local_only: { bg: "bg-transparent", text: "text-gray-500 dark:text-gray-500", border: "border border-dashed border-gray-300 dark:border-gray-600" },
};

const TERMINAL_STATUSES = ["resolved", "closed", "wontfix"];
const ACTIVE_STATUSES = ["pending", "open", "confirmed"];
const RESOLVED_STATUSES = ["resolved", "closed", "wontfix"];
const STATUS_WEIGHT: Record<string, number> = {
  open: 0, pending: 1, confirmed: 2, resolved: 3, closed: 4, wontfix: 5, local_only: 6,
};

function statusKey(status: string): string {
  const map: Record<string, string> = {
    pending: "statusPending",
    open: "statusOpen",
    confirmed: "statusConfirmed",
    resolved: "statusResolved",
    closed: "statusResolved",
    local_only: "statusLocalOnly",
  };
  return map[status] ?? "statusPending";
}

export function MyFeedbackView({ apiBaseUrl, serviceRunning, onOpenFeedbackModal, refreshTrigger }: MyFeedbackViewProps) {
  const { t } = useTranslation();
  const mdModules = useMdModules();
  const [activeTab, setActiveTab] = useState<FeedbackTab>("mine");
  const [records, setRecords] = useState<FeedbackRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [publicRefreshing, setPublicRefreshing] = useState(false);
  const [publicRefreshTrigger, setPublicRefreshTrigger] = useState(0);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, FeedbackDetail>>({});
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  const [replyText, setReplyText] = useState<Record<string, string>>({});
  const [sending, setSending] = useState<string | null>(null);
  const [replyError, setReplyError] = useState<string | null>(null);
  const replyEndRef = useRef<Record<string, HTMLDivElement | null>>({});
  const [filterTab, setFilterTab] = useState<FilterTab>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("date");

  const fetchRecords = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/feedback-history`);
      const data = await res.json();
      if (Array.isArray(data)) setRecords(data);
    } catch {
      // silently fail
    }
  }, [apiBaseUrl]);

  const batchRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await safeFetch(`${apiBaseUrl}/api/feedback-status/batch`, { method: "POST" });
    } catch {
      // batch update failed, still refresh local records below
    }
    try {
      await fetchRecords();
    } catch {
      // silently fail
    }
    if (expandedIds.size > 0) {
      for (const eid of expandedIds) {
        try {
          const res = await safeFetch(`${apiBaseUrl}/api/feedback-status/${eid}`);
          const data = await res.json();
          setDetails((prev) => ({ ...prev, [eid]: data }));
          if (data.status) {
            setRecords((prev) => prev.map((r) =>
              r.report_id === eid
                ? { ...r, cached_status: data.status }
                : r
            ));
          }
        } catch {
          // detail refresh failed, keep stale data
        }
      }
    }
    setRefreshing(false);
  }, [apiBaseUrl, fetchRecords, expandedIds]);

  const handlePublicRefresh = useCallback(() => {
    setPublicRefreshing(true);
    setPublicRefreshTrigger(n => n + 1);
    setTimeout(() => setPublicRefreshing(false), 1500);
  }, []);

  const batchRefreshRef = useRef(batchRefresh);
  batchRefreshRef.current = batchRefresh;

  useEffect(() => {
    if (!serviceRunning) {
      setLoading(false);
      return;
    }
    setLoading(true);
    fetchRecords().then(() => {
      setLoading(false);
      batchRefreshRef.current();
    });
  }, [serviceRunning, fetchRecords, refreshTrigger]);

  const fetchDetail = useCallback(async (reportId: string) => {
    setDetailLoading(reportId);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/feedback-status/${reportId}`);
      const data = await res.json();
      setDetails((prev) => ({ ...prev, [reportId]: data }));
      if (data.status) {
        setRecords((prev) => prev.map((r) =>
          r.report_id === reportId
            ? { ...r, cached_status: data.status, has_unread: false }
            : r
        ));
      }
    } catch {
      // silently fail
    } finally {
      setDetailLoading((prev) => (prev === reportId ? null : prev));
    }
  }, [apiBaseUrl]);

  const toggleExpand = useCallback((reportId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(reportId)) {
        next.delete(reportId);
      } else {
        next.add(reportId);
        setReplyError(null);
        if (!details[reportId]) fetchDetail(reportId);
      }
      return next;
    });
  }, [details, fetchDetail]);

  const handleDelete = useCallback((reportId: string) => {
    setConfirmDialog({
      message: t("myFeedback.deleteConfirm"),
      onConfirm: async () => {
        try {
          await safeFetch(`${apiBaseUrl}/api/feedback-history/${reportId}`, { method: "DELETE" });
          setRecords((prev) => prev.filter((r) => r.report_id !== reportId));
          setExpandedIds((prev) => { const next = new Set(prev); next.delete(reportId); return next; });
        } catch {
          // silently fail
        }
      },
    });
  }, [apiBaseUrl, t]);

  const stats = useMemo(() => {
    let active = 0, unread = 0, resolved = 0;
    for (const r of records) {
      if (ACTIVE_STATUSES.includes(r.cached_status)) active++;
      if (RESOLVED_STATUSES.includes(r.cached_status)) resolved++;
      if (r.has_unread) unread++;
    }
    return { active, unread, resolved };
  }, [records]);

  const filteredRecords = useMemo(() => {
    let result = records;
    if (filterTab === "active") result = result.filter(r => ACTIVE_STATUSES.includes(r.cached_status));
    else if (filterTab === "resolved") result = result.filter(r => RESOLVED_STATUSES.includes(r.cached_status));
    else if (filterTab === "unread") result = result.filter(r => r.has_unread);

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(r => r.title.toLowerCase().includes(q));
    }

    if (sortBy === "date") {
      result = [...result].sort((a, b) => b.submitted_at.localeCompare(a.submitted_at));
    } else if (sortBy === "status") {
      result = [...result].sort((a, b) => {
        const wa = a.has_unread ? -1 : (STATUS_WEIGHT[a.cached_status] ?? 9);
        const wb = b.has_unread ? -1 : (STATUS_WEIGHT[b.cached_status] ?? 9);
        return wa !== wb ? wa - wb : b.submitted_at.localeCompare(a.submitted_at);
      });
    } else if (sortBy === "type") {
      result = [...result].sort((a, b) => {
        if (a.type !== b.type) return a.type === "bug" ? -1 : 1;
        return b.submitted_at.localeCompare(a.submitted_at);
      });
    }

    return result;
  }, [records, filterTab, searchQuery, sortBy]);

  const sendReply = useCallback(async (reportId: string) => {
    const text = (replyText[reportId] || "").trim();
    if (!text || sending) return;

    setSending(reportId);
    setReplyError(null);
    try {
      await safeFetch(`${apiBaseUrl}/api/feedback-reply/${reportId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: text }),
        signal: AbortSignal.timeout(30_000),
      });
      setReplyText((prev) => ({ ...prev, [reportId]: "" }));
      const newReply: DeveloperReply = {
        author: "user",
        body: text,
        created_at: new Date().toISOString(),
        source: "user_reply",
      };
      setDetails((prev) => {
        const existing = prev[reportId];
        if (!existing) return prev;
        return {
          ...prev,
          [reportId]: {
            ...existing,
            developer_replies: [...(existing.developer_replies || []), newReply],
          },
        };
      });
      setTimeout(() => {
        replyEndRef.current[reportId]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 50);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.startsWith("HTTP 429")) {
        setReplyError(t("myFeedback.replyRateLimit"));
      } else {
        setReplyError(t("myFeedback.replyFailed"));
      }
    } finally {
      setSending(null);
    }
  }, [apiBaseUrl, replyText, sending, t]);

  const formatDate = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) +
        " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <IconLoader size={28} className="animate-spin text-muted-foreground/60" />
        <p className="text-muted-foreground text-[13px]">{t("myFeedback.publicLoading")}</p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <ToggleGroup
          type="single"
          value={activeTab}
          onValueChange={(v) => { if (v) setActiveTab(v as FeedbackTab); }}
          variant="outline"
        >
          <ToggleGroupItem
            value="mine"
            className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
          >
            {t("myFeedback.tabMine")}
          </ToggleGroupItem>
          <ToggleGroupItem
            value="all"
            className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
          >
            {t("myFeedback.tabAll")}
          </ToggleGroupItem>
        </ToggleGroup>
        <div className="flex items-center gap-2">
          {onOpenFeedbackModal && (
            <Button
              size="sm"
              disabled={!serviceRunning}
              onClick={() => onOpenFeedbackModal()}
              className="gap-1.5"
            >
              <IconPlus size={14} />
              {t("myFeedback.submitFeedback")}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={(activeTab === "mine" ? refreshing : publicRefreshing) || !serviceRunning}
            onClick={activeTab === "mine" ? batchRefresh : handlePublicRefresh}
            className="gap-1.5"
          >
            {(activeTab === "mine" ? refreshing : publicRefreshing)
              ? <IconLoader size={14} className="animate-spin" />
              : <IconRefresh size={14} />}
            {t("myFeedback.refresh")}
          </Button>
        </div>
      </div>

      {activeTab === "all" ? (
        <PublicFeedbackList apiBaseUrl={apiBaseUrl} serviceRunning={serviceRunning} refreshTrigger={publicRefreshTrigger} />
      ) : records.length === 0 ? (
        <div className="text-center py-16">
          <IconMessageCircle size={40} className="mx-auto mb-3 text-muted-foreground/30" />
          <p className="text-muted-foreground text-[15px]">{t("myFeedback.empty")}</p>
          <p className="text-muted-foreground/60 text-[13px] mt-1">{t("myFeedback.emptyHint")}</p>
        </div>
      ) : (
        <>
          {/* Filter tabs */}
          <div className="flex gap-2 mb-3">
            <ToggleGroup
              type="single"
              value={filterTab}
              onValueChange={(v) => { if (v) setFilterTab(v as FilterTab); }}
              variant="outline"
            >
              {([
                ["all", t("myFeedback.filterAll"), records.length],
                ["active", t("myFeedback.filterActive"), stats.active],
                ["resolved", t("myFeedback.filterResolved"), stats.resolved],
                ["unread", t("myFeedback.filterUnread"), stats.unread],
              ] as [FilterTab, string, number][]).map(([tab, label, count]) => (
                <ToggleGroupItem
                  key={tab}
                  value={tab}
                  className="text-sm min-w-[4.5rem] data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
                >
                  {label}
                  <Badge
                    variant="secondary"
                    className={
                      filterTab === tab
                        ? "ml-1.5 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full bg-white/25 text-primary-foreground"
                        : "ml-1.5 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full bg-foreground/10 text-foreground/60"
                    }
                  >
                    {count}
                  </Badge>
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          {/* Search + Sort row */}
          <div className="flex items-center gap-2 mb-2">
            <div className="relative flex-1">
              <IconSearch size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", opacity: 0.4, pointerEvents: "none" }} />
              <Input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t("myFeedback.searchPlaceholder")}
                className="pl-8"
              />
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <span className="text-[13px] text-muted-foreground whitespace-nowrap">{t("myFeedback.sortLabel")}:</span>
              <Select value={sortBy} onValueChange={(v) => setSortBy(v as SortBy)}>
                <SelectTrigger size="sm" className="min-w-[5rem]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="date">{t("myFeedback.sortDate")}</SelectItem>
                  <SelectItem value="status">{t("myFeedback.sortStatus")}</SelectItem>
                  <SelectItem value="type">{t("myFeedback.sortType")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Filtered list */}
          {filteredRecords.length === 0 ? (
            <div className="text-center py-10">
              <p className="text-muted-foreground text-[14px]">{t("myFeedback.noResults")}</p>
            </div>
          ) : (
        <div className="space-y-2">
          {filteredRecords.map((rec) => {
            const isExpanded = expandedIds.has(rec.report_id);
            const detail = details[rec.report_id];
            const isLoadingDetail = detailLoading === rec.report_id;
            const style = STATUS_STYLES[rec.cached_status] ?? STATUS_STYLES.pending;

            return (
              <div key={rec.report_id} className="rounded-lg border border-border overflow-hidden">
                {/* Card header */}
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/50 transition-colors"
                  onClick={() => toggleExpand(rec.report_id)}
                >
                  <div className="shrink-0">
                    {isExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                  </div>
                  <div className="shrink-0">
                    {rec.type === "bug" ? (
                      <IconBug size={16} className="text-red-500" />
                    ) : (
                      <IconZap size={16} className="text-amber-500" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0 flex items-center gap-2">
                    <span className="text-[14px] font-medium truncate">{rec.title}</span>
                    {rec.has_unread && (
                      <span className="w-2 h-2 rounded-full bg-blue-500 shrink-0" />
                    )}
                    <span className="text-[11px] text-muted-foreground whitespace-nowrap ml-auto">
                      {formatDate(rec.submitted_at)}
                    </span>
                  </div>
                  <Badge
                    variant="secondary"
                    className={`text-[11px] px-2 py-0.5 ${style.bg} ${style.text} ${style.border ?? ""}`}
                  >
                    {t(`myFeedback.${statusKey(rec.cached_status)}`)}
                  </Badge>
                  <IconTrash
                    size={14}
                    className="shrink-0 cursor-pointer text-muted-foreground/40 hover:text-destructive transition-colors"
                    onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleDelete(rec.report_id); }}
                  />
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="px-4 pb-4 pt-1 border-t border-border bg-muted/20">
                    {!rec.has_token && (
                      <p className="text-[12px] text-muted-foreground italic mb-2">
                        {t("myFeedback.localOnlyHint")}
                      </p>
                    )}

                    {isLoadingDetail ? (
                      <div className="flex items-center gap-2 text-[13px] text-muted-foreground py-2">
                        <IconLoader size={14} className="animate-spin" />
                        {t("myFeedback.refreshing")}
                      </div>
                    ) : rec.has_token && detail ? (
                      <div className="space-y-3 mt-2">
                        {detail.summary && (
                          <div className="flex justify-end gap-2.5">
                            <div className="max-w-[80%]">
                              <div className="flex items-center justify-end gap-2 text-[12px]">
                                <span className="text-muted-foreground">{detail.created_at ? formatDate(detail.created_at) : formatDate(rec.submitted_at)}</span>
                                <span className="font-medium text-blue-600 dark:text-blue-400">{t("myFeedback.originalDescription")}</span>
                              </div>
                              <div className="mt-1 px-3 py-2 rounded-lg bg-blue-50 dark:bg-blue-900/30 text-[13px] whitespace-pre-wrap break-words">
                                {detail.summary}
                              </div>
                            </div>
                            <div className="w-6 h-6 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 flex items-center justify-center shrink-0 mt-0.5">
                              <IconUser size={14} />
                            </div>
                          </div>
                        )}

                        {detail.developer_replies && detail.developer_replies.length > 0 ? (
                          detail.developer_replies.map((reply, i) => {
                            const isUserReply = reply.source === "user_reply";
                            return isUserReply ? (
                              <div key={i} className="flex justify-end gap-2.5">
                                <div className="max-w-[80%]">
                                  <div className="flex items-center justify-end gap-2 text-[12px]">
                                    <span className="text-muted-foreground">{formatDate(reply.created_at)}</span>
                                    <span className="font-medium text-blue-600 dark:text-blue-400">{t("myFeedback.you")}</span>
                                  </div>
                                  <div className="mt-1 px-3 py-2 rounded-lg bg-blue-50 dark:bg-blue-900/30 text-[13px] whitespace-pre-wrap break-words">
                                    {reply.body}
                                  </div>
                                </div>
                                <div className="w-6 h-6 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 flex items-center justify-center shrink-0 mt-0.5">
                                  <IconUser size={14} />
                                </div>
                              </div>
                            ) : (
                              <div key={i} className="flex gap-2.5">
                                <div className="w-6 h-6 rounded-full bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-400 flex items-center justify-center text-[11px] font-bold shrink-0 mt-0.5">
                                  {reply.author.charAt(0).toUpperCase()}
                                </div>
                                <div className="max-w-[80%]">
                                  <div className="flex items-center gap-2 text-[12px]">
                                    <span className="font-medium">{reply.author}</span>
                                    <span className="text-muted-foreground">{formatDate(reply.created_at)}</span>
                                  </div>
                                  <div className="mt-1 px-3 py-2 rounded-lg bg-slate-100 dark:bg-slate-800 border border-border/50 text-[13px] break-words inline-block">
                                    {mdModules ? (
                                      <div className="feedbackMdContent">
                                        <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>{reply.body}</mdModules.ReactMarkdown>
                                      </div>
                                    ) : <span style={{ whiteSpace: "pre-wrap" }}>{reply.body}</span>}
                                  </div>
                                </div>
                              </div>
                            );
                          })
                        ) : !detail.summary ? (
                          <p className="text-[13px] text-muted-foreground">{t("myFeedback.noReplies")}</p>
                        ) : null}

                        {!TERMINAL_STATUSES.includes(rec.cached_status) &&
                          (!detail.developer_replies || !detail.developer_replies.some((r) => r.source !== "user_reply")) && (
                          <p className="text-[11px] text-muted-foreground/70 text-center mt-1">
                            {t("myFeedback.pendingHint")}
                          </p>
                        )}

                        <div ref={(el) => { replyEndRef.current[rec.report_id] = el; }} />
                      </div>
                    ) : rec.has_token ? (
                      <div className="py-2">
                        <p className="text-[13px] text-muted-foreground">{t("myFeedback.noReplies")}</p>
                      </div>
                    ) : null}



                    {rec.has_token && !TERMINAL_STATUSES.includes(rec.cached_status) && (
                      <div className="mt-3 mx-6">
                          <div style={{ position: "relative", borderRadius: 8, border: "1px solid var(--border)", background: "var(--background)", boxShadow: "0 1px 2px rgba(0,0,0,0.05)" }}>
                            <textarea
                              style={{ width: "100%", minHeight: 48, maxHeight: 120, padding: "10px 36px 10px 12px", fontSize: 13, lineHeight: 1.5, background: "transparent", border: "none", outline: "none", boxShadow: "none", resize: "vertical", color: "inherit", fontFamily: "inherit" }}
                              placeholder={t("myFeedback.replyPlaceholder")}
                              value={replyText[rec.report_id] || ""}
                              onChange={(e) => setReplyText((prev) => ({ ...prev, [rec.report_id]: e.target.value }))}
                              disabled={sending === rec.report_id}
                              maxLength={2000}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                                  e.preventDefault();
                                  sendReply(rec.report_id);
                                }
                              }}
                            />
                            {(() => {
                              const disabled = sending === rec.report_id || !(replyText[rec.report_id] || "").trim();
                              return (
                                <span
                                  onClick={disabled ? undefined : () => sendReply(rec.report_id)}
                                  title={t("myFeedback.sendReply")}
                                  style={{ position: "absolute", right: 14, bottom: 10, cursor: disabled ? "default" : "pointer", color: disabled ? "var(--muted-foreground)" : "#3b82f6", opacity: disabled ? 0.3 : 1, transition: "color 0.15s, opacity 0.15s" }}
                                >
                                  {sending === rec.report_id ? (
                                    <IconLoader size={18} className="animate-spin" />
                                  ) : (
                                    <IconSend size={18} />
                                  )}
                                </span>
                              );
                            })()}
                            {replyError && sending !== rec.report_id && (
                              <p style={{ fontSize: 12, color: "var(--destructive)", padding: "0 12px 8px", margin: 0 }}>{replyError}</p>
                            )}
                          </div>
                      </div>
                    )}

                    {rec.has_token && TERMINAL_STATUSES.includes(rec.cached_status) && onOpenFeedbackModal && (
                      <p className="mt-2 text-[11px] text-muted-foreground text-center">
                        {t("myFeedback.resubmitLabel")}{" "}
                        <span
                          className="text-blue-500 hover:text-blue-600 cursor-pointer hover:underline"
                          onClick={() => onOpenFeedbackModal({
                            mode: rec.type,
                            title: rec.title,
                            description: t("myFeedback.resubmitHint", { title: rec.title }),
                          })}
                        >
                          {t("myFeedback.resubmitAction")}
                        </span>
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
          )}
        </>
      )}

      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
    </div>
  );
}
