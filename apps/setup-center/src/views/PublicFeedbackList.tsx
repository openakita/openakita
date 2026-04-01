import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { openExternalUrl } from "../platform";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";
import {
  IconSearch, IconBug, IconZap, IconUser, IconLoader,
  IconChevronDown, IconChevronRight, IconMessageCircle,
  IconSend, IconGlobe, IconRefresh,
} from "../icons";
import { useMdModules } from "../hooks/useMdModules";

type PublicIssue = {
  number: number;
  title: string;
  type: "bug" | "feature";
  status: string;
  created_at: string;
  updated_at: string;
  comments_count: number;
  html_url: string;
  labels: string[];
};

type IssueComment = {
  author: string;
  body: string;
  created_at: string;
  source: string;
};

type IssueDetail = {
  number: number;
  title: string;
  type: string;
  status: string;
  summary?: string;
  created_at: string;
  html_url: string;
  comments: IssueComment[];
};

type Props = {
  apiBaseUrl: string;
  serviceRunning: boolean;
  refreshTrigger?: number;
};

const STATUS_STYLES: Record<string, { bg: string; text: string }> = {
  open: { bg: "bg-blue-50 dark:bg-blue-900/30", text: "text-blue-600 dark:text-blue-400" },
  confirmed: { bg: "bg-orange-50 dark:bg-orange-900/30", text: "text-orange-600 dark:text-orange-400" },
  resolved: { bg: "bg-green-50 dark:bg-green-900/30", text: "text-green-600 dark:text-green-400" },
  wontfix: { bg: "bg-gray-100 dark:bg-gray-800", text: "text-gray-600 dark:text-gray-400" },
};

const STATUS_I18N: Record<string, string> = {
  open: "statusOpen",
  confirmed: "statusConfirmed",
  resolved: "statusResolved",
  wontfix: "statusResolved",
};

const SOURCE_COLORS: Record<string, string> = {
  developer: "text-primary",
  user_reply: "text-blue-600 dark:text-blue-400",
  community: "text-emerald-600 dark:text-emerald-400",
};

export function PublicFeedbackList({ apiBaseUrl, serviceRunning, refreshTrigger }: Props) {
  const { t } = useTranslation();
  const mdModules = useMdModules();
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("");
  const [issues, setIssues] = useState<PublicIssue[]>([]);
  const [page, setPage] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [expandedNumber, setExpandedNumber] = useState<number | null>(null);
  const [detail, setDetail] = useState<IssueDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [sending, setSending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const replyEndRef = useRef<HTMLDivElement | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expandedRef = useRef<number | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(query), 500);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query]);

  const fetchIssues = useCallback(async (pg: number, append: boolean) => {
    if (!serviceRunning) return;
    if (pg === 1) { setLoading(true); setLoadError(false); } else setLoadingMore(true);
    try {
      const params = new URLSearchParams({
        q: debouncedQuery, page: String(pg), per_page: "20",
        state: stateFilter,
      });
      if (typeFilter) params.set("type", typeFilter);
      const res = await safeFetch(`${apiBaseUrl}/api/feedback-public-search?${params}`, {
        signal: AbortSignal.timeout(20_000),
      });
      const data = await res.json();
      const items: PublicIssue[] = data.items ?? [];
      setIssues(prev => append ? [...prev, ...items] : items);
      setHasNext(data.has_next ?? false);
      setPage(pg);
    } catch {
      if (!append) { setIssues([]); setLoadError(true); }
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [apiBaseUrl, serviceRunning, debouncedQuery, stateFilter, typeFilter]);

  useEffect(() => {
    setExpandedNumber(null);
    setDetail(null);
    fetchIssues(1, false);
  }, [fetchIssues]);

  useEffect(() => {
    if (refreshTrigger && refreshTrigger > 0) {
      setExpandedNumber(null);
      setDetail(null);
      fetchIssues(1, false);
    }
  }, [refreshTrigger]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMore = useCallback(() => {
    fetchIssues(page + 1, true);
  }, [fetchIssues, page]);

  const fetchDetail = useCallback(async (num: number) => {
    expandedRef.current = num;
    setDetailLoading(true);
    setReplyError(null);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/feedback-public-issue/${num}`, {
        signal: AbortSignal.timeout(20_000),
      });
      const data: IssueDetail = await res.json();
      if (expandedRef.current === num) setDetail(data);
    } catch {
      if (expandedRef.current === num) setDetail(null);
    } finally {
      if (expandedRef.current === num) setDetailLoading(false);
    }
  }, [apiBaseUrl]);

  const toggleExpand = useCallback((num: number) => {
    if (expandedNumber === num) {
      setExpandedNumber(null);
      setDetail(null);
      expandedRef.current = null;
    } else {
      setExpandedNumber(num);
      setDetail(null);
      setReplyText("");
      setReplyError(null);
      fetchDetail(num);
    }
  }, [expandedNumber, fetchDetail]);

  const sendComment = useCallback(async () => {
    if (!replyText.trim() || sending || expandedNumber == null) return;
    setSending(true);
    setReplyError(null);
    try {
      await safeFetch(`${apiBaseUrl}/api/feedback-public-comment/${expandedNumber}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: replyText.trim() }),
        signal: AbortSignal.timeout(30_000),
      });
      setReplyText("");
      const newComment: IssueComment = {
        author: "you",
        body: replyText.trim(),
        created_at: new Date().toISOString(),
        source: "community",
      };
      setDetail(prev => prev ? {
        ...prev, comments: [...prev.comments, newComment],
      } : prev);
      setTimeout(() => {
        replyEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 50);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.startsWith("HTTP 429")) {
        setReplyError(t("myFeedback.publicRateLimit"));
      } else {
        setReplyError(t("myFeedback.publicReplyFailed"));
      }
    } finally {
      setSending(false);
    }
  }, [apiBaseUrl, expandedNumber, replyText, sending, t]);

  const formatDate = useMemo(() => (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
        + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  }, []);

  const sourceLabel = useCallback((source: string) => {
    if (source === "user_reply") return t("myFeedback.you");
    if (source === "community") return t("myFeedback.communityReply");
    return source;
  }, [t]);

  if (!serviceRunning) {
    return (
      <div className="text-center py-16">
        <p className="text-muted-foreground text-[14px]">{t("myFeedback.publicEmpty")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Search + filters */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <IconSearch size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", opacity: 0.4, pointerEvents: "none" }} />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("myFeedback.publicSearchPlaceholder")}
            className="pl-8"
          />
        </div>
        <Select value={stateFilter} onValueChange={setStateFilter}>
          <SelectTrigger size="sm" className="min-w-[5rem]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("myFeedback.publicFilterAll")}</SelectItem>
            <SelectItem value="open">{t("myFeedback.publicFilterOpen")}</SelectItem>
            <SelectItem value="closed">{t("myFeedback.publicFilterClosed")}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={typeFilter || "__all__"} onValueChange={(v) => setTypeFilter(v === "__all__" ? "" : v)}>
          <SelectTrigger size="sm" className="min-w-[5rem]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">{t("myFeedback.publicTypeAll")}</SelectItem>
            <SelectItem value="bug">{t("myFeedback.publicTypeBug")}</SelectItem>
            <SelectItem value="feature">{t("myFeedback.publicTypeFeature")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Loading */}
      {loading ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <IconLoader size={28} className="animate-spin text-muted-foreground/60" />
          <p className="text-muted-foreground text-[13px]">{t("myFeedback.publicLoading")}</p>
        </div>
      ) : loadError && issues.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <IconMessageCircle size={40} className="text-muted-foreground/30" />
          <p className="text-muted-foreground text-[14px]">{t("myFeedback.publicLoadError")}</p>
          <Button variant="outline" size="sm" onClick={() => fetchIssues(1, false)} className="gap-1.5 mt-1">
            <IconRefresh size={14} />
            {t("myFeedback.publicRetry")}
          </Button>
        </div>
      ) : issues.length === 0 ? (
        <div className="text-center py-16">
          <IconMessageCircle size={40} className="mx-auto mb-3 text-muted-foreground/30" />
          <p className="text-muted-foreground text-[14px]">
            {debouncedQuery ? t("myFeedback.publicNoResults") : t("myFeedback.publicEmpty")}
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-2">
            {issues.map(issue => {
              const isExpanded = expandedNumber === issue.number;
              const style = STATUS_STYLES[issue.status] ?? STATUS_STYLES.open;
              return (
                <div key={issue.number} className="rounded-lg border border-border overflow-hidden">
                  <div
                    className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-muted/50 transition-colors"
                    onClick={() => toggleExpand(issue.number)}
                  >
                    <div className="shrink-0">
                      {isExpanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                    </div>
                    <div className="shrink-0">
                      {issue.type === "bug"
                        ? <IconBug size={16} className="text-red-500" />
                        : <IconZap size={16} className="text-amber-500" />}
                    </div>
                    <div className="flex-1 min-w-0 flex items-center gap-2">
                      <span className="text-[14px] font-medium truncate">{issue.title}</span>
                      {issue.comments_count > 0 && (
                        <span className="text-[11px] text-muted-foreground flex items-center gap-0.5 shrink-0">
                          <IconMessageCircle size={12} />
                          {issue.comments_count}
                        </span>
                      )}
                      <span className="text-[11px] text-muted-foreground whitespace-nowrap ml-auto">
                        {formatDate(issue.created_at)}
                      </span>
                    </div>
                    <Badge
                      variant="secondary"
                      className={`text-[11px] px-2 py-0.5 ${style.bg} ${style.text}`}
                    >
                      {t(`myFeedback.${STATUS_I18N[issue.status] ?? "statusOpen"}`)}
                    </Badge>
                    <IconGlobe
                      size={14}
                      className="shrink-0 cursor-pointer text-muted-foreground/40 hover:text-primary transition-colors"
                      onClick={(e: React.MouseEvent) => {
                        e.stopPropagation();
                        if (issue.html_url) openExternalUrl(issue.html_url);
                      }}
                    />
                  </div>

                  {isExpanded && (
                    <div className="px-4 pb-4 pt-1 border-t border-border bg-muted/20">
                      {detailLoading ? (
                        <div className="flex items-center gap-2 text-[13px] text-muted-foreground py-2">
                          <IconLoader size={14} className="animate-spin" />
                          {t("myFeedback.publicLoading")}
                        </div>
                      ) : detail ? (
                        <div className="space-y-3 mt-2">
                          {detail.summary && (
                            <div className="flex gap-2.5">
                              <div className="w-6 h-6 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 flex items-center justify-center shrink-0 mt-0.5">
                                <IconUser size={14} />
                              </div>
                              <div className="max-w-[85%]">
                                <div className="flex items-center gap-2 text-[12px]">
                                  <span className="font-medium text-blue-600 dark:text-blue-400">
                                    {t("myFeedback.publicSubmitter")}
                                  </span>
                                  <span className="text-muted-foreground">{formatDate(detail.created_at)}</span>
                                </div>
                                <div className="mt-1 px-3 py-2 rounded-lg bg-blue-50 dark:bg-blue-900/30 text-[13px] whitespace-pre-wrap break-words">
                                  {detail.summary}
                                </div>
                              </div>
                            </div>
                          )}

                          {detail.comments.length > 0 ? (
                            detail.comments.map((cm, i) => {
                              const isCommunity = cm.source === "community";
                              const isUserReply = cm.source === "user_reply";
                              const isDev = !isCommunity && !isUserReply;
                              const avatarClass = isDev
                                ? "bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-400"
                                : isUserReply
                                  ? "bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400"
                                  : "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-400";
                              const bubbleClass = isDev
                                ? "bg-slate-100 dark:bg-slate-800 border border-border/50"
                                : isUserReply
                                  ? "bg-blue-50 dark:bg-blue-900/30"
                                  : "bg-emerald-50 dark:bg-emerald-900/30";
                              return (
                                <div key={i} className="flex gap-2.5">
                                  <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 mt-0.5 ${avatarClass}`}>
                                    {isDev ? cm.author.charAt(0).toUpperCase() : <IconUser size={14} />}
                                  </div>
                                  <div className="max-w-[85%]">
                                    <div className="flex items-center gap-2 text-[12px]">
                                      <span className={`font-medium ${SOURCE_COLORS[cm.source] ?? ""}`}>
                                        {isDev ? cm.author : sourceLabel(cm.source)}
                                      </span>
                                      <span className="text-muted-foreground">{formatDate(cm.created_at)}</span>
                                    </div>
                                    <div className={`mt-1 px-3 py-2 rounded-lg text-[13px] break-words ${bubbleClass}`}>
                                      {isDev && mdModules ? (
                                        <div className="feedbackMdContent">
                                          <mdModules.ReactMarkdown remarkPlugins={[mdModules.remarkGfm]}>{cm.body}</mdModules.ReactMarkdown>
                                        </div>
                                      ) : <span style={{ whiteSpace: "pre-wrap" }}>{cm.body}</span>}
                                    </div>
                                  </div>
                                </div>
                              );
                            })
                          ) : !detail.summary ? (
                            <p className="text-[13px] text-muted-foreground">{t("myFeedback.noReplies")}</p>
                          ) : null}

                          <div ref={replyEndRef} />

                          {/* Reply input */}
                          <div className="mt-3 mx-6">
                            <div style={{ position: "relative", borderRadius: 8, border: "1px solid var(--border)", background: "var(--background)", boxShadow: "0 1px 2px rgba(0,0,0,0.05)" }}>
                              <textarea
                                style={{ width: "100%", minHeight: 48, maxHeight: 120, padding: "10px 36px 10px 12px", fontSize: 13, lineHeight: 1.5, background: "transparent", border: "none", outline: "none", boxShadow: "none", resize: "vertical", color: "inherit", fontFamily: "inherit" }}
                                placeholder={t("myFeedback.publicReplyPlaceholder")}
                                value={replyText}
                                onChange={(e) => setReplyText(e.target.value)}
                                disabled={sending}
                                maxLength={2000}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                                    e.preventDefault();
                                    sendComment();
                                  }
                                }}
                              />
                              {(() => {
                                const disabled = sending || !replyText.trim();
                                return (
                                  <span
                                    onClick={disabled ? undefined : sendComment}
                                    title={t("myFeedback.sendReply")}
                                    style={{ position: "absolute", right: 14, bottom: 10, cursor: disabled ? "default" : "pointer", color: disabled ? "var(--muted-foreground)" : "#3b82f6", opacity: disabled ? 0.3 : 1, transition: "color 0.15s, opacity 0.15s" }}
                                  >
                                    {sending
                                      ? <IconLoader size={18} className="animate-spin" />
                                      : <IconSend size={18} />}
                                  </span>
                                );
                              })()}
                              {replyError && !sending && (
                                <p style={{ fontSize: 12, color: "var(--destructive)", padding: "0 12px 8px", margin: 0 }}>{replyError}</p>
                              )}
                            </div>
                          </div>

                          {detail.html_url && (
                            <p className="text-center mt-1">
                              <span
                                className="text-[11px] text-muted-foreground/60 hover:text-primary cursor-pointer"
                                onClick={() => openExternalUrl(detail.html_url)}
                              >
                                {t("myFeedback.publicViewOnGithub")} ↗
                              </span>
                            </p>
                          )}
                        </div>
                      ) : (
                        <p className="text-[13px] text-muted-foreground py-2">{t("myFeedback.publicNoResults")}</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {hasNext && (
            <div className="text-center pt-2">
              <Button
                variant="outline"
                size="sm"
                disabled={loadingMore}
                onClick={loadMore}
              >
                {loadingMore
                  ? <><IconLoader size={14} className="animate-spin mr-1.5" />{t("myFeedback.publicLoading")}</>
                  : t("myFeedback.publicLoadMore")}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
