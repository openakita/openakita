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
  IconRefresh, IconTrash, IconBug, IconZap,
  IconChevronDown, IconChevronRight, IconLoader, IconMessageCircle,
  IconSend, IconPlus, IconSearch,
} from "../icons";

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
  developer_replies?: DeveloperReply[];
  labels?: string[];
  source?: string;
  github_issue_url?: string;
};

type FilterTab = "all" | "active" | "resolved" | "unread";
type SortBy = "date" | "status" | "type";

type MyFeedbackViewProps = {
  apiBaseUrl: string;
  serviceRunning: boolean;
  onOpenFeedbackModal?: () => void;
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

export function MyFeedbackView({ apiBaseUrl, serviceRunning, onOpenFeedbackModal }: MyFeedbackViewProps) {
  const { t } = useTranslation();
  const [records, setRecords] = useState<FeedbackRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
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
      await fetchRecords();
      if (expandedId) {
        try {
          const res = await safeFetch(`${apiBaseUrl}/api/feedback-status/${expandedId}`);
          const data = await res.json();
          setDetails((prev) => ({ ...prev, [expandedId]: data }));
          if (data.status) {
            setRecords((prev) => prev.map((r) =>
              r.report_id === expandedId
                ? { ...r, cached_status: data.status, has_unread: false }
                : r
            ));
          }
        } catch {
          // detail refresh failed, keep stale data
        }
      }
    } catch {
      // silently fail
    } finally {
      setRefreshing(false);
    }
  }, [apiBaseUrl, fetchRecords, expandedId]);

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
  }, [serviceRunning, fetchRecords]);

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
    if (expandedId === reportId) {
      setExpandedId(null);
    } else {
      setExpandedId(reportId);
      setReplyError(null);
      if (!details[reportId]) fetchDetail(reportId);
    }
  }, [expandedId, details, fetchDetail]);

  const handleDelete = useCallback((reportId: string) => {
    setConfirmDialog({
      message: t("myFeedback.deleteConfirm"),
      onConfirm: async () => {
        try {
          await safeFetch(`${apiBaseUrl}/api/feedback-history/${reportId}`, { method: "DELETE" });
          setRecords((prev) => prev.filter((r) => r.report_id !== reportId));
          if (expandedId === reportId) setExpandedId(null);
        } catch {
          // silently fail
        }
      },
    });
  }, [apiBaseUrl, expandedId, t]);

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
      const res = await safeFetch(`${apiBaseUrl}/api/feedback-reply/${reportId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: text }),
      });
      if (res.status === 429) {
        setReplyError(t("myFeedback.replyRateLimit"));
        return;
      }
      if (!res.ok) {
        setReplyError(t("myFeedback.replyFailed"));
        return;
      }
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
    } catch {
      setReplyError(t("myFeedback.replyFailed"));
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
      <div className="p-6 space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-16 bg-muted rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-semibold">{t("myFeedback.title")}</h2>
        <div className="flex items-center gap-2">
          {onOpenFeedbackModal && (
            <Button
              size="sm"
              disabled={!serviceRunning}
              onClick={onOpenFeedbackModal}
              className="gap-1.5"
            >
              <IconPlus size={14} />
              {t("myFeedback.submitFeedback")}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={refreshing || !serviceRunning}
            onClick={batchRefresh}
            className="gap-1.5"
          >
            {refreshing ? <IconLoader size={14} className="animate-spin" /> : <IconRefresh size={14} />}
            {t("myFeedback.refresh")}
          </Button>
        </div>
      </div>

      {records.length === 0 ? (
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

          {/* Stats summary */}
          {records.length > 0 && (
            <p className="text-[11px] text-muted-foreground mb-2">
              {stats.active > 0 && <>{stats.active} {t("myFeedback.statsActive")}</>}
              {stats.active > 0 && stats.unread > 0 && " · "}
              {stats.unread > 0 && <span className="text-blue-500">{stats.unread} {t("myFeedback.statsUnread")}</span>}
              {(stats.active > 0 || stats.unread > 0) && stats.resolved > 0 && " · "}
              {stats.resolved > 0 && <>{stats.resolved} {t("myFeedback.statsResolved")}</>}
            </p>
          )}

          {/* Filtered list */}
          {filteredRecords.length === 0 ? (
            <div className="text-center py-10">
              <p className="text-muted-foreground text-[14px]">{t("myFeedback.noResults")}</p>
            </div>
          ) : (
        <div className="space-y-2">
          {filteredRecords.map((rec) => {
            const isExpanded = expandedId === rec.report_id;
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
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[14px] font-medium truncate">{rec.title}</span>
                      {rec.has_unread && (
                        <span className="w-2 h-2 rounded-full bg-blue-500 shrink-0" />
                      )}
                    </div>
                    <div className="text-[11px] text-muted-foreground mt-0.5">
                      {t("myFeedback.submittedAt")} {formatDate(rec.submitted_at)}
                    </div>
                  </div>
                  <Badge
                    variant="secondary"
                    className={`text-[11px] px-2 py-0.5 ${style.bg} ${style.text} ${style.border ?? ""}`}
                  >
                    {t(`myFeedback.${statusKey(rec.cached_status)}`)}
                  </Badge>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(rec.report_id); }}
                    className="shrink-0 p-1 rounded hover:bg-destructive/10 transition-colors text-muted-foreground hover:text-destructive"
                    title={t("myFeedback.deleteRecord")}
                  >
                    <IconTrash size={14} />
                  </button>
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
                    ) : detail?.developer_replies && detail.developer_replies.length > 0 ? (
                      <div className="space-y-3 mt-2">
                        <p className="text-[13px] font-medium">{t("myFeedback.repliesTitle")}</p>
                        {detail.developer_replies.map((reply, i) => {
                          const isUserReply = reply.source === "user_reply";
                          return isUserReply ? (
                            <div key={i} className="flex justify-end">
                              <div className="max-w-[80%]">
                                <div className="flex items-center justify-end gap-2 text-[12px]">
                                  <span className="text-muted-foreground">{formatDate(reply.created_at)}</span>
                                  <span className="font-medium text-blue-600 dark:text-blue-400">{t("myFeedback.you")}</span>
                                </div>
                                <div className="mt-1 px-3 py-2 rounded-lg bg-blue-500/10 text-[13px] whitespace-pre-wrap break-words">
                                  {reply.body}
                                </div>
                              </div>
                            </div>
                          ) : (
                            <div key={i} className="flex gap-2.5">
                              <div className="w-6 h-6 rounded-full bg-primary/10 text-primary flex items-center justify-center text-[11px] font-bold shrink-0 mt-0.5">
                                {reply.author.charAt(0).toUpperCase()}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 text-[12px]">
                                  <span className="font-medium">{reply.author}</span>
                                  <span className="text-muted-foreground">{formatDate(reply.created_at)}</span>
                                </div>
                                <div className="mt-1 px-3 py-2 rounded-lg bg-muted text-[13px] whitespace-pre-wrap break-words">
                                  {reply.body}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                        <div ref={(el) => { replyEndRef.current[rec.report_id] = el; }} />
                      </div>
                    ) : rec.has_token ? (
                      <p className="text-[13px] text-muted-foreground py-2">{t("myFeedback.noReplies")}</p>
                    ) : null}

                    {detail?.labels && detail.labels.length > 0 && (
                      <div className="flex gap-1 flex-wrap mt-2">
                        {detail.labels.map((label) => (
                          <Badge key={label} variant="outline" className="text-[10px] px-1.5 py-0">
                            {label}
                          </Badge>
                        ))}
                      </div>
                    )}

                    {detail?.github_issue_url && (
                      <div className="mt-2">
                        <a
                          href={detail.github_issue_url}
                          onClick={(e) => {
                            e.preventDefault();
                            openExternalUrl(detail.github_issue_url!);
                          }}
                          className="inline-flex items-center gap-1 text-[12px] text-blue-500 hover:text-blue-600 hover:underline"
                        >
                          {t("myFeedback.viewOnGithub")} ↗
                        </a>
                      </div>
                    )}

                    {rec.has_token && !TERMINAL_STATUSES.includes(rec.cached_status) && (
                      <div className="mt-3 pt-3 border-t border-border">
                        <div className="flex gap-2">
                          <textarea
                            className="flex-1 min-h-[60px] max-h-[120px] px-3 py-2 text-[13px] rounded-md border border-input bg-background resize-y placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
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
                          <Button
                            size="sm"
                            disabled={sending === rec.report_id || !(replyText[rec.report_id] || "").trim()}
                            onClick={() => sendReply(rec.report_id)}
                            className="self-end gap-1.5"
                          >
                            {sending === rec.report_id ? (
                              <IconLoader size={14} className="animate-spin" />
                            ) : (
                              <IconSend size={14} />
                            )}
                            {sending === rec.report_id ? t("myFeedback.sending") : t("myFeedback.sendReply")}
                          </Button>
                        </div>
                        {replyError && sending !== rec.report_id && (
                          <p className="text-[12px] text-destructive mt-1">{replyError}</p>
                        )}
                      </div>
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
