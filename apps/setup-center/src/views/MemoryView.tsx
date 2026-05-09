import React, { useEffect, useState, useCallback, useRef, lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { IconBrain } from "../icons";
import { safeFetch } from "../providers";
import { encodePathSegment } from "../platform/apiUrl";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Loader2, RefreshCw, Trash2, Pencil, Check, X, Search, Brain, Ban, List, Network, ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from "@/components/ui/table";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { useMdModules } from "./chat/hooks/useMdModules";
import type { MdModules } from "./chat/utils/chatTypes";

const MemoryGraph3D = lazy(() =>
  import("../components/MemoryGraph3D").then((m) => ({ default: m.MemoryGraph3D }))
);

type MemoryItem = {
  id: string;
  type: string;
  priority: string;
  content: string;
  source: string;
  subject: string;
  predicate: string;
  tags: string[];
  importance_score: number;
  confidence: number;
  access_count: number;
  created_at: string | null;
  updated_at: string | null;
  last_accessed_at: string | null;
  expires_at: string | null;
};

type Stats = {
  total: number;
  by_type: Record<string, number>;
  avg_score: number;
};

type ReviewResult = {
  deleted: number;
  updated: number;
  merged: number;
  kept: number;
  errors: number;
};

type ReviewProgress = {
  status: "idle" | "running" | "done" | "error" | "cancelled";
  phase?: "llm_calling" | "batch_done" | "done";
  batch?: number;
  total_batches?: number;
  total_memories?: number;
  processed?: number;
  report?: ReviewResult;
  error?: string;
  started_at?: number;
  finished_at?: number;
};

const TYPE_LABEL_KEYS: Record<string, string> = {
  fact: "memory.typeFact",
  preference: "memory.typePreference",
  skill: "memory.typeSkill",
  rule: "memory.typeRule",
  error: "memory.typeError",
  experience: "memory.typeExperience",
  persona_trait: "memory.typePersonaTrait",
  context: "memory.typeContext",
};

const TYPE_COLORS: Record<string, string> = {
  fact: "#3b82f6",
  preference: "#8b5cf6",
  skill: "#10b981",
  rule: "#f59e0b",
  error: "#ef4444",
  experience: "#06b6d4",
  persona_trait: "#ec4899",
  context: "#6b7280",
};

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "importance_score", label: "重要性" },
  { value: "created_at", label: "创建时间" },
  { value: "updated_at", label: "更新时间" },
  { value: "last_accessed_at", label: "最近访问" },
  { value: "access_count", label: "访问次数" },
];

const PAGE_SIZE = 50;

function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return iso;
  }
}

function stripMd(s: string): string {
  return s
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\n/g, " ");
}

function MemoryContent({ content, mdMods }: { content: string; mdMods: MdModules | null }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div style={{
          fontSize: 13, lineHeight: 1.6, color: "var(--text)",
          overflow: "hidden", textOverflow: "ellipsis",
          whiteSpace: "nowrap", cursor: "pointer",
        }}>
          {stripMd(content)}
        </div>
      </TooltipTrigger>
      <TooltipContent
        side="bottom"
        align="start"
        className="memory-tip-wrap bg-popover text-popover-foreground border border-border shadow-lg"
      >
        {mdMods ? (
          <div className="feedbackMdContent memory-tip-content">
            <mdMods.ReactMarkdown remarkPlugins={mdMods.remarkPlugins}>{content}</mdMods.ReactMarkdown>
          </div>
        ) : (
          <div className="memory-tip-content" style={{ whiteSpace: "pre-wrap" }}>{content}</div>
        )}
      </TooltipContent>
    </Tooltip>
  );
}

interface Props {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}

export function MemoryView({ serviceRunning, apiBaseUrl = "" }: Props) {
  const { t } = useTranslation();
  const API_BASE = apiBaseUrl;
  const typeLabel = (type: string) => TYPE_LABEL_KEYS[type] ? t(TYPE_LABEL_KEYS[type]) : type;
  const mdMods = useMdModules();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editScore, setEditScore] = useState(0);
  const [reviewing, setReviewing] = useState(false);
  const [reviewProgress, setReviewProgress] = useState<ReviewProgress>({ status: "idle" });
  const reviewPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [showReviewConfirm, setShowReviewConfirm] = useState(false);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth <= 768);
  const [viewMode, setViewMode] = useState<"list" | "graph">("list");
  const [sortBy, setSortBy] = useState("importance_score");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);
  const [totalCount, setTotalCount] = useState(0);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const isSearchMode = !!searchQuery;

  const loadMemories = useCallback(async () => {
    if (!serviceRunning) return;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (searchQuery) params.set("search", searchQuery);
      if (filterType) params.set("type", filterType);
      if (!searchQuery) {
        params.set("sort_by", sortBy);
        params.set("sort_order", sortOrder);
        params.set("limit", String(PAGE_SIZE));
        params.set("offset", String(page * PAGE_SIZE));
      }
      const res = await safeFetch(`${API_BASE}/api/memories?${params}`);
      const data = await res.json();
      setMemories(data.memories || []);
      setTotalCount(data.total ?? 0);
      setSelected(new Set());
    } catch (e: any) {
      toast.error(e.message || t("memory.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [serviceRunning, searchQuery, filterType, sortBy, sortOrder, page, API_BASE]);

  const loadStats = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${API_BASE}/api/memories/stats`);
      setStats(await res.json());
    } catch { /* ignore */ }
  }, [serviceRunning, API_BASE]);

  useEffect(() => {
    loadMemories();
    loadStats();
  }, [loadMemories, loadStats]);

  const doDelete = async (id: string) => {
    try {
      // PR-O1: encodePathSegment 防止 id 含特殊字符破坏路径
      await safeFetch(`${API_BASE}/api/memories/${encodePathSegment(id)}`, { method: "DELETE" });
      setMemories(prev => prev.filter(m => m.id !== id));
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n; });
      loadStats();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleDelete = (id: string) => {
    const mem = memories.find(m => m.id === id);
    const preview = mem ? (mem.content.length > 40 ? mem.content.slice(0, 40) + "..." : mem.content) : "";
    setConfirmDialog({
      message: t("memory.confirmDeleteOne", { preview }),
      onConfirm: () => doDelete(id),
    });
  };

  const doBatchDelete = useCallback(async (ids: string[]) => {
    try {
      await safeFetch(`${API_BASE}/api/memories/batch-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      setMemories(prev => prev.filter(m => !new Set(ids).has(m.id)));
      setSelected(new Set());
      loadStats();
    } catch (e: any) {
      toast.error(e.message);
    }
  }, [API_BASE, loadStats]);

  const handleBatchDelete = () => {
    if (selected.size === 0) return;
    const ids = Array.from(selected);
    setConfirmDialog({
      message: t("memory.confirmDeleteBatch", { count: ids.length }),
      onConfirm: () => doBatchDelete(ids),
    });
  };

  const handleUpdate = async (id: string) => {
    try {
      await safeFetch(`${API_BASE}/api/memories/${encodePathSegment(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editContent, importance_score: editScore }),
      });
      setMemories(prev => prev.map(m =>
        m.id === id ? { ...m, content: editContent, importance_score: editScore } : m
      ));
      setEditingId(null);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const startEdit = (m: MemoryItem) => {
    setEditingId(m.id);
    setEditContent(m.content);
    setEditScore(m.importance_score);
  };

  const handleReviewConfirm = () => setShowReviewConfirm(true);

  const stopPolling = useCallback(() => {
    if (reviewPollRef.current) {
      clearInterval(reviewPollRef.current);
      reviewPollRef.current = null;
    }
  }, []);

  const pollReviewStatus = useCallback(() => {
    stopPolling();
    reviewPollRef.current = setInterval(async () => {
      try {
        const res = await safeFetch(`${API_BASE}/api/memories/review/status`, {
          signal: AbortSignal.timeout(5_000),
        });
        const data = await res.json();
        const progress: ReviewProgress = data.progress ?? data;
        setReviewProgress(progress);

        if (progress.status === "done" || progress.status === "error" || progress.status === "cancelled") {
          stopPolling();
          setReviewing(false);
          if (progress.status === "done" && progress.report) {
            const r = progress.report;
            toast.success(
              t("memory.reviewDone", { deleted: r.deleted, updated: r.updated, merged: r.merged, kept: r.kept }) +
              (r.errors > 0 ? t("memory.reviewDoneWithErrors", { errors: r.errors }) : "")
            );
          } else if (progress.status === "cancelled") {
            toast.info(t("memory.reviewCancelled"));
          } else if (progress.status === "error") {
            toast.error(t("memory.reviewError", { error: progress.error || "unknown" }));
          }
          loadMemories();
          loadStats();
        }
      } catch {
        /* transient network error, keep polling */
      }
    }, 2_000);
  }, [API_BASE, stopPolling, loadMemories, loadStats]);

  useEffect(() => stopPolling, [stopPolling]);

  useEffect(() => {
    if (!serviceRunning) return;
    (async () => {
      try {
        const res = await safeFetch(`${API_BASE}/api/memories/review/status`, {
          signal: AbortSignal.timeout(3_000),
        });
        const data = await res.json();
        if (data.status === "running") {
          setReviewing(true);
          setReviewProgress(data.progress ?? {});
          pollReviewStatus();
        }
      } catch { /* not running */ }
    })();
  }, [serviceRunning, API_BASE, pollReviewStatus]);

  const handleReview = async () => {
    setShowReviewConfirm(false);
    setReviewing(true);
    setReviewProgress({ status: "running" });
    try {
      const res = await safeFetch(`${API_BASE}/api/memories/review`, {
        method: "POST",
        signal: AbortSignal.timeout(10_000),
      });
      const data = await res.json();
      if (data.status === "already_running") {
        toast.info(t("memory.reviewAlreadyRunning"));
      }
      pollReviewStatus();
    } catch (e: any) {
      toast.error(e.message || t("memory.reviewStartFailed"));
      setReviewing(false);
      setReviewProgress({ status: "idle" });
    }
  };

  const handleCancelReview = async () => {
    try {
      await safeFetch(`${API_BASE}/api/memories/review/cancel`, {
        method: "POST",
        signal: AbortSignal.timeout(5_000),
      });
      toast.info(t("memory.cancellingReview"));
    } catch {
      toast.error(t("memory.cancelReviewFailed"));
    }
  };

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const selectAll = () => {
    if (selected.size === memories.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(memories.map(m => m.id)));
    }
  };

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconBrain size={48} />
        <div className="mt-3 font-semibold">{t("memory.title")}</div>
        <div className="mt-1 text-xs opacity-50">{t("memory.serviceNotRunning")}</div>
      </div>
    );
  }

  const isGraph = viewMode === "graph";
  const graphPanelHeight = isMobile ? "max(560px, calc(100vh - 22rem))" : "max(620px, calc(100vh - 18rem))";

  return (
    <TooltipProvider delayDuration={300}>
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      {/* Stats bar */}
      {stats && (
        <Card className="gap-0 overflow-hidden border-border/80 bg-gradient-to-br from-primary/5 via-background to-background py-0 shadow-sm shrink-0">
          <CardContent className="p-0">
            <div className="grid grid-cols-2 sm:grid-cols-3 md:flex md:flex-wrap md:items-stretch divide-y md:divide-y-0 md:divide-x divide-border/50">
              {[
                { value: stats.total, label: t("memory.totalMemories"), color: "var(--foreground)" },
                { value: stats.avg_score.toFixed(2), label: t("memory.avgScore"), color: "var(--foreground)" },
                ...Object.entries(stats.by_type).map(([tp, c]) => ({
                  value: c,
                  label: typeLabel(tp),
                  color: TYPE_COLORS[tp] || "var(--foreground)",
                })),
              ].map((item, i) => (
                <div key={i} className="flex flex-1 min-w-[80px] flex-col items-center justify-center p-3 md:p-4">
                  <div className="text-2xl font-bold tracking-tight" style={{ color: item.color }}>
                    {item.value}
                  </div>
                  <div className="mt-1 text-xs font-medium text-muted-foreground">
                    {item.label}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Toolbar */}
      <Card className="gap-0 border-border/80 py-0 shadow-sm shrink-0">
        <CardContent className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 md:gap-3">
              <div className="relative w-full min-w-[160px] max-w-[280px] sm:w-[220px] md:w-[280px]">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                <Input
                  placeholder={t("memory.searchPlaceholder")}
                  value={searchQuery}
                  onChange={e => { setSearchQuery(e.target.value); setPage(0); }}
                  onKeyDown={e => e.key === "Enter" && loadMemories()}
                  className="h-9 pl-9 text-sm"
                />
              </div>

              <Select value={filterType || "__all__"} onValueChange={v => { setFilterType(v === "__all__" ? "" : v); setPage(0); }}>
                <SelectTrigger className="h-9 w-[110px] text-sm md:w-[130px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">{t("memory.allTypes")}</SelectItem>
                  {Object.keys(TYPE_LABEL_KEYS).map((k) => (
                    <SelectItem key={k} value={k}>{typeLabel(k)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select
                value={sortBy}
                onValueChange={v => { setSortBy(v); setPage(0); }}
                disabled={isSearchMode}
              >
                <SelectTrigger className="h-9 w-[120px] text-sm" title={isSearchMode ? "搜索结果按相关度排序" : ""}>
                  <ArrowUpDown size={12} className="mr-1 shrink-0" />
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORT_OPTIONS.map(o => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Button
                variant="outline"
                size="icon"
                className="h-9 w-9 shrink-0"
                disabled={isSearchMode}
                title={sortOrder === "desc" ? "降序（点击切换升序）" : "升序（点击切换降序）"}
                onClick={() => { setSortOrder(prev => prev === "desc" ? "asc" : "desc"); setPage(0); }}
              >
                <span style={{ fontSize: 13, fontWeight: 600, opacity: isSearchMode ? 0.4 : 1 }}>
                  {sortOrder === "desc" ? "↓" : "↑"}
                </span>
              </Button>

              <Button variant="outline" onClick={loadMemories} disabled={loading} className="h-9 px-3 shrink-0" title={t("memory.refresh")}>
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {!isMobile && <span className="ml-1.5 hidden xl:inline">{t("memory.refresh")}</span>}
              </Button>

              {reviewing ? (
                <Button onClick={handleCancelReview} variant="destructive" className="h-9 px-3 shrink-0" title={t("memory.cancelReview")}>
                  <Ban size={14} className="mr-1.5" />
                  <span className="hidden xl:inline">{t("memory.cancelReview")}</span>
                  <span className="xl:hidden">{t("memory.cancel")}</span>
                </Button>
              ) : (
                <Button
                  onClick={handleReviewConfirm}
                  className="h-9 px-3 shrink-0 border-0 bg-gradient-to-br from-indigo-500 to-purple-500 text-white shadow-md shadow-indigo-500/20 hover:from-indigo-600 hover:to-purple-600"
                  title={t("memory.llmReviewFull")}
                >
                  <Brain size={14} className="mr-1.5" />
                  <span className="hidden xl:inline">{t("memory.llmReviewFull")}</span>
                  <span className="xl:hidden">{t("memory.llmReview")}</span>
                </Button>
              )}
            </div>

            <div className="flex shrink-0 items-center gap-3">
              {selected.size > 0 && (
                <Button variant="destructive" onClick={handleBatchDelete} className="h-9 px-3 shrink-0" title={t("memory.deleteCount", { count: selected.size })}>
                  <Trash2 size={14} className="mr-1.5" />
                  <span className="hidden xl:inline">{t("memory.deleteCount", { count: selected.size })}</span>
                  <span className="xl:hidden">{t("memory.delete")}</span>
                </Button>
              )}

              {/* View mode toggle */}
              <ToggleGroup
                type="single"
                value={viewMode}
                onValueChange={(v) => { if (v) setViewMode(v as "list" | "graph"); }}
                variant="outline"
                className="shrink-0 justify-end"
              >
                <ToggleGroupItem value="list" className="h-9 px-3 text-sm data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:text-primary-foreground" title={t("memory.listView")}>
                  <List size={14} className="mr-1.5" />
                  <span className="hidden xl:inline">{t("memory.listView")}</span>
                </ToggleGroupItem>
                <ToggleGroupItem value="graph" className="h-9 px-3 text-sm data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:text-primary-foreground" title={t("memory.graphView")}>
                  <Network size={14} className="mr-1.5" />
                  <span className="hidden xl:inline">{t("memory.graphView")}</span>
                </ToggleGroupItem>
              </ToggleGroup>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Review progress bar */}
      {reviewing && reviewProgress.status === "running" && (() => {
        const total = reviewProgress.total_batches ?? 0;
        const isLlmCalling = reviewProgress.phase === "llm_calling";
        const completedBatches = isLlmCalling ? (reviewProgress.batch ?? 0) : (reviewProgress.batch ?? 0);
        const pct = total > 0
          ? isLlmCalling
            ? ((completedBatches + 0.5) / total) * 100
            : (completedBatches / total) * 100
          : 0;
        const batchLabel = total > 0
          ? isLlmCalling
            ? t("memory.reviewBatchProgress", { current: (reviewProgress.batch ?? 0) + 1, total })
            : t("memory.reviewBatchDone", { done: reviewProgress.batch ?? 0, total })
          : t("memory.reviewPreparing");

        return (
          <div className="card" style={{ margin: 0, padding: isMobile ? "10px 12px" : "12px 16px" }}>
            <div className="flex items-center gap-2 mb-2">
              <Loader2 size={14} className="animate-spin text-indigo-500" />
              <span style={{ fontSize: 13, fontWeight: 500 }}>
                {batchLabel}
                {isLlmCalling && <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: 6 }}>{t("memory.reviewWaitingLLM")}</span>}
              </span>
              {reviewProgress.total_memories ? (
                <span style={{ fontSize: 12, color: "var(--muted)", marginLeft: "auto" }}>
                  {t("memory.reviewMemoryCount", { processed: reviewProgress.processed ?? 0, total: reviewProgress.total_memories })}
                </span>
              ) : null}
            </div>
            <div style={{ position: "relative", width: "100%", height: 6, borderRadius: 3, background: "rgba(100,116,139,0.12)" }}>
              <div style={{
                position: "absolute", top: 0, left: 0,
                height: "100%", borderRadius: 3, transition: "width 0.6s ease",
                background: isLlmCalling
                  ? "repeating-linear-gradient(90deg, #6366f1 0%, #8b5cf6 50%, #6366f1 100%)"
                  : "linear-gradient(90deg, #6366f1, #8b5cf6)",
                backgroundSize: isLlmCalling ? "200% 100%" : "100% 100%",
                animation: isLlmCalling ? "reviewShimmer 1.5s linear infinite" : "none",
                width: `${Math.min(pct, 100)}%`,
              }} />
            </div>
            {reviewProgress.report && (
              <div style={{ display: "flex", gap: 12, marginTop: 8, fontSize: 11, color: "var(--muted)" }}>
                <span>{t("memory.reviewDeleted")} <b style={{ color: "#ef4444" }}>{reviewProgress.report.deleted}</b></span>
                <span>{t("memory.reviewUpdated")} <b style={{ color: "#f59e0b" }}>{reviewProgress.report.updated}</b></span>
                <span>{t("memory.reviewMerged")} <b style={{ color: "#8b5cf6" }}>{reviewProgress.report.merged}</b></span>
                <span>{t("memory.reviewKept")} <b style={{ color: "#10b981" }}>{reviewProgress.report.kept}</b></span>
                {(reviewProgress.report.errors ?? 0) > 0 && (
                  <span>{t("memory.reviewErrors")} <b style={{ color: "#ef4444" }}>{reviewProgress.report.errors}</b></span>
                )}
              </div>
            )}
            <style>{`@keyframes reviewShimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>
          </div>
        );
      })()}

      {/* Review confirm dialog */}
      <AlertDialog open={showReviewConfirm} onOpenChange={setShowReviewConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("memory.reviewConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("memory.reviewConfirmDesc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("memory.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleReview}
              className="bg-gradient-to-br from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white border-0"
            >
              {t("memory.reviewConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Graph view */}
      {viewMode === "graph" ? (
        <Card className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm">
          <Suspense fallback={
            <div className="flex items-center justify-center" style={{ height: graphPanelHeight }}>
              <Loader2 size={24} className="animate-spin text-indigo-500" />
              <span className="ml-2 text-sm text-muted-foreground">{t("memory.loadingGraph")}</span>
            </div>
          }>
            <CardContent className="p-0" style={{ height: graphPanelHeight }}>
              <MemoryGraph3D apiBaseUrl={API_BASE} searchQuery={searchQuery} />
            </CardContent>
          </Suspense>
        </Card>
      ) : null}

      {/* Memory list */}
      {viewMode !== "list" ? null : isMobile ? (
        /* ── Mobile: card-based layout ── */
        <div className="flex flex-col gap-3">
          {loading ? (
            <Card className="shadow-sm">
              <CardContent className="py-10 text-center text-muted-foreground">
                <Loader2 size={20} className="inline animate-spin mr-2" />{t("memory.loading")}
              </CardContent>
            </Card>
          ) : memories.length === 0 ? (
            <Card className="shadow-sm">
              <CardContent className="py-10 text-center text-muted-foreground">
                {t("memory.noData")}
              </CardContent>
            </Card>
          ) : (
            <>
              <div className="flex items-center gap-2 px-1">
                <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                  <Checkbox checked={selected.size === memories.length && memories.length > 0} onCheckedChange={selectAll} />
                  {t("memory.selectAll")} ({memories.length})
                </label>
              </div>
              {memories.map(m => (
                <Card
                  key={m.id}
                  className={`gap-0 overflow-hidden border-border/80 py-0 shadow-sm transition-colors ${selected.has(m.id) ? "bg-indigo-500/5 border-indigo-500/20" : ""}`}
                >
                  <CardContent className="p-4">
                    {/* Header row: checkbox + type badge + score + date */}
                    <div className="flex items-center gap-3 mb-3">
                      <Checkbox checked={selected.has(m.id)} onCheckedChange={() => toggleSelect(m.id)} />
                      <Badge variant="outline" style={{
                        backgroundColor: `${TYPE_COLORS[m.type] || "#6b7280"}18`,
                        color: TYPE_COLORS[m.type] || "#6b7280",
                        borderColor: `${TYPE_COLORS[m.type] || "#6b7280"}30`,
                      }}>
                        {typeLabel(m.type)}
                      </Badge>
                      <span className="font-semibold text-xs" style={{
                        color: m.importance_score >= 0.85 ? "#10b981" : m.importance_score >= 0.7 ? "#f59e0b" : "#6b7280",
                      }}>
                        {m.importance_score.toFixed(2)}
                      </span>
                      <span className="text-xs text-muted-foreground ml-auto">
                        {fmtDate(m.created_at)}
                      </span>
                    </div>

                    {/* Content */}
                    {editingId === m.id ? (
                      <div className="flex flex-col gap-2">
                        <Textarea
                          value={editContent}
                          onChange={e => setEditContent(e.target.value)}
                          rows={3}
                          className="resize-y text-sm"
                        />
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">{t("memory.score")}:</span>
                          <Input
                            type="number" min={0} max={1} step={0.05}
                            value={editScore}
                            onChange={e => setEditScore(parseFloat(e.target.value) || 0)}
                            className="w-[80px] h-8 text-xs"
                          />
                          <Button variant="ghost" size="icon-sm" className="text-emerald-500 hover:text-emerald-600" onClick={() => handleUpdate(m.id)}>
                            <Check size={16} />
                          </Button>
                          <Button variant="ghost" size="icon-sm" className="text-destructive hover:text-destructive" onClick={() => setEditingId(null)}>
                            <X size={16} />
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground">
                        {m.content}
                      </div>
                    )}

                    {/* Meta: subject + predicate + tags */}
                    {(m.subject || m.predicate || (m.tags && m.tags.length > 0)) && editingId !== m.id && (
                      <div className="mt-3 space-y-1.5">
                        {(m.subject || m.predicate) && (
                          <div className="text-xs text-muted-foreground">
                            {m.subject && <span>{t("memory.subject")}: {m.subject}</span>}
                            {m.subject && m.predicate && <span> · </span>}
                            {m.predicate && <span>{t("memory.predicate")}: {m.predicate}</span>}
                          </div>
                        )}
                        {m.tags && m.tags.length > 0 && (
                          <div className="flex gap-1.5 flex-wrap">
                            {m.tags.map(tag => (
                              <Badge key={tag} variant="outline" className="bg-indigo-500/10 text-indigo-500 border-indigo-500/20 text-[10px] px-1.5 py-0">
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Actions */}
                    {editingId !== m.id && (
                      <div className="flex gap-2 mt-4 justify-end">
                        <Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => startEdit(m)}>
                          <Pencil size={14} className="mr-1" /> {t("memory.edit")}
                        </Button>
                        <Button variant="outline" size="sm" className="h-8 text-xs text-destructive border-destructive/30 hover:text-destructive hover:bg-destructive/10" onClick={() => handleDelete(m.id)}>
                          <Trash2 size={14} className="mr-1" /> {t("memory.delete")}
                        </Button>
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}
            </>
          )}
        </div>
      ) : (
        /* ── Desktop: table layout ── */
        <Card className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm">
          <Table style={{ tableLayout: "fixed", width: "100%" }}>
            <colgroup>
              <col style={{ width: 36 }} />
              <col style={{ width: 80 }} />
              <col />
              <col style={{ width: 60 }} />
              <col style={{ width: 90 }} />
              <col style={{ width: 80 }} />
            </colgroup>
            <TableHeader>
              <TableRow>
                <TableHead className="px-3">
                  <Checkbox
                    checked={selected.size === memories.length && memories.length > 0}
                    onCheckedChange={selectAll}
                  />
                </TableHead>
                <TableHead>{t("memory.type")}</TableHead>
                <TableHead>{t("memory.content")}</TableHead>
                <TableHead className="text-center">{t("memory.score")}</TableHead>
                <TableHead className="text-center">{t("memory.createdAt")}</TableHead>
                <TableHead className="text-center">{t("memory.actions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-10 text-muted-foreground">
                    <Loader2 size={20} className="inline animate-spin mr-2" />{t("memory.loading")}
                  </TableCell>
                </TableRow>
              ) : memories.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-10 text-muted-foreground">
                    {t("memory.noData")}
                  </TableCell>
                </TableRow>
              ) : memories.map(m => (
                <TableRow
                  key={m.id}
                  className={selected.has(m.id) ? "bg-indigo-500/[0.06]" : ""}
                >
                  <TableCell className="px-3">
                    <Checkbox checked={selected.has(m.id)} onCheckedChange={() => toggleSelect(m.id)} />
                  </TableCell>
                  <TableCell>
                    <span style={{
                      display: "inline-block", padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 500,
                      whiteSpace: "nowrap",
                      background: `${TYPE_COLORS[m.type] || "#6b7280"}18`,
                      color: TYPE_COLORS[m.type] || "#6b7280",
                      border: `1px solid ${TYPE_COLORS[m.type] || "#6b7280"}30`,
                    }}>
                      {typeLabel(m.type)}
                    </span>
                  </TableCell>
                  <TableCell style={{ overflow: "hidden" }}>
                    {editingId === m.id ? (
                      <div className="flex flex-col gap-1.5">
                        <Textarea
                          value={editContent}
                          onChange={e => setEditContent(e.target.value)}
                          rows={3}
                          className="resize-y text-xs"
                        />
                        <div className="flex items-center gap-1.5">
                          <span className="text-[11px] text-muted-foreground">{t("memory.score")}:</span>
                          <Input
                            type="number" min={0} max={1} step={0.05}
                            value={editScore}
                            onChange={e => setEditScore(parseFloat(e.target.value) || 0)}
                            className="w-[70px] h-7 text-xs"
                          />
                          <Button variant="ghost" size="icon-sm" className="text-emerald-500 hover:text-emerald-600" onClick={() => handleUpdate(m.id)}>
                            <Check size={14} />
                          </Button>
                          <Button variant="ghost" size="icon-sm" className="text-destructive hover:text-destructive" onClick={() => setEditingId(null)}>
                            <X size={14} />
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div>
                        <MemoryContent content={m.content} mdMods={mdMods} />
                        {(m.subject || m.predicate) && (
                          <div className="mt-1 text-[11px] text-muted-foreground" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {m.subject && <span>{t("memory.subject")}: {m.subject}</span>}
                            {m.subject && m.predicate && <span> · </span>}
                            {m.predicate && <span>{t("memory.predicate")}: {m.predicate}</span>}
                          </div>
                        )}
                        {m.tags && m.tags.length > 0 && (
                          <div className="mt-1 flex gap-1 flex-wrap">
                            {m.tags.map(tag => (
                              <span key={tag} className="px-1.5 py-px rounded-lg text-[10px] bg-indigo-500/10 text-indigo-500 whitespace-nowrap">
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-center">
                    <span className="font-semibold text-xs" style={{
                      color: m.importance_score >= 0.85 ? "#10b981" : m.importance_score >= 0.7 ? "#f59e0b" : "#6b7280",
                    }}>
                      {m.importance_score.toFixed(2)}
                    </span>
                  </TableCell>
                  <TableCell className="text-center text-[11px] text-muted-foreground">
                    {fmtDate(m.created_at)}
                  </TableCell>
                  <TableCell className="text-center">
                    <div className="flex gap-1 justify-center">
                      <Button variant="ghost" size="icon-sm" title={t("memory.edit")} className="text-muted-foreground hover:text-foreground" onClick={() => startEdit(m)}>
                        <Pencil size={13} />
                      </Button>
                      <Button variant="ghost" size="icon-sm" title={t("memory.delete")} className="text-muted-foreground hover:text-destructive" onClick={() => handleDelete(m.id)}>
                        <Trash2 size={13} />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      {/* Pagination */}
      {!isSearchMode && totalCount > 0 && (() => {
        const totalPages = Math.ceil(totalCount / PAGE_SIZE);
        if (totalPages <= 1) return null;
        return (
          <div className="flex items-center justify-between gap-2 flex-wrap" style={{ fontSize: 13 }}>
            <span className="text-muted-foreground text-xs">
              共 {totalCount} 条 · 第 {page + 1}/{totalPages} 页
            </span>
            <div className="flex items-center gap-1">
              <Button variant="outline" size="sm" disabled={page <= 0} onClick={() => setPage(p => p - 1)}>
                <ChevronLeft size={14} /> 上一页
              </Button>
              <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>
                下一页 <ChevronRight size={14} />
              </Button>
            </div>
          </div>
        );
      })()}

      <AlertDialog open={!!confirmDialog} onOpenChange={open => { if (!open) setConfirmDialog(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("memory.confirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-wrap">{confirmDialog?.message}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("memory.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => { confirmDialog?.onConfirm(); setConfirmDialog(null); }}>
              {t("memory.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
    </TooltipProvider>
  );
}
