import React, { useEffect, useState, useCallback, useRef, lazy, Suspense } from "react";
import { IconBrain } from "../icons";
import { safeFetch } from "../providers";
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
import { Loader2, RefreshCw, Trash2, Pencil, Check, X, Search, Brain, Ban, List, Network } from "lucide-react";
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from "@/components/ui/table";

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

const TYPE_LABELS: Record<string, string> = {
  fact: "事实",
  preference: "偏好",
  skill: "技能",
  rule: "规则",
  error: "经验教训",
  experience: "经验",
  persona_trait: "人格特征",
  context: "上下文",
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

function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return iso;
  }
}

interface Props {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}

export function MemoryView({ serviceRunning, apiBaseUrl = "" }: Props) {
  const API_BASE = apiBaseUrl;
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

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const loadMemories = useCallback(async () => {
    if (!serviceRunning) return;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (searchQuery) params.set("search", searchQuery);
      if (filterType) params.set("type", filterType);
      const res = await safeFetch(`${API_BASE}/api/memories?${params}`);
      const data = await res.json();
      setMemories(data.memories || []);
    } catch (e: any) {
      toast.error(e.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, [serviceRunning, searchQuery, filterType, API_BASE]);

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
      await safeFetch(`${API_BASE}/api/memories/${id}`, { method: "DELETE" });
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
      message: `确定删除这条记忆？\n\n"${preview}"`,
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
      message: `确定删除选中的 ${ids.length} 条记忆？`,
      onConfirm: () => doBatchDelete(ids),
    });
  };

  const handleUpdate = async (id: string) => {
    try {
      await safeFetch(`${API_BASE}/api/memories/${id}`, {
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
              `LLM 审查完成：删除 ${r.deleted}，更新 ${r.updated}，合并 ${r.merged}，保留 ${r.kept}` +
              (r.errors > 0 ? `，错误 ${r.errors}` : "")
            );
          } else if (progress.status === "cancelled") {
            toast.info("审查已取消（已处理的部分生效）");
          } else if (progress.status === "error") {
            toast.error(`审查出错：${progress.error || "未知错误"}`);
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
        toast.info("审查任务已在运行中");
      }
      pollReviewStatus();
    } catch (e: any) {
      toast.error(e.message || "启动审查失败");
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
      toast.info("正在取消审查...");
    } catch {
      toast.error("取消请求失败");
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
        <div className="mt-3 font-semibold">记忆管理</div>
        <div className="mt-1 text-xs opacity-50">后端服务未启动，请启动后再进行使用</div>
      </div>
    );
  }

  const isGraph = viewMode === "graph";
  const graphPanelHeight = isMobile ? "max(560px, calc(100vh - 22rem))" : "max(620px, calc(100vh - 18rem))";

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      {/* Stats bar */}
      {stats && (
        <Card className="gap-0 overflow-hidden border-border/80 bg-gradient-to-br from-primary/5 via-background to-background py-0 shadow-sm shrink-0">
          <CardContent className="p-0">
            <div className="grid grid-cols-2 sm:grid-cols-3 md:flex md:flex-wrap md:items-stretch divide-y md:divide-y-0 md:divide-x divide-border/50">
              {[
                { value: stats.total, label: "总记忆数", color: "var(--foreground)" },
                { value: stats.avg_score.toFixed(2), label: "平均分数", color: "var(--foreground)" },
                ...Object.entries(stats.by_type).map(([t, c]) => ({
                  value: c,
                  label: TYPE_LABELS[t] || t,
                  color: TYPE_COLORS[t] || "var(--foreground)",
                })),
              ].map((item, i) => (
                <div key={i} className="flex flex-1 min-w-[100px] flex-col items-center justify-center p-4">
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
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex flex-1 flex-wrap items-center gap-2 md:gap-3">
              <div className="relative flex-1 min-w-[200px] max-w-[320px]">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                <Input
                  placeholder="搜索记忆内容..."
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && loadMemories()}
                  className="h-9 pl-9 text-sm"
                />
              </div>

              <Select value={filterType || "__all__"} onValueChange={v => setFilterType(v === "__all__" ? "" : v)}>
                <SelectTrigger className="h-9 w-[110px] text-sm md:w-[130px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">全部类型</SelectItem>
                  {Object.entries(TYPE_LABELS).map(([k, v]) => (
                    <SelectItem key={k} value={k}>{v}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Button variant="outline" onClick={loadMemories} disabled={loading} className="h-9 px-3">
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                {!isMobile && <span className="ml-1.5">刷新</span>}
              </Button>

              {reviewing ? (
                <Button onClick={handleCancelReview} variant="destructive" className="h-9 px-3">
                  <Ban size={14} className="mr-1.5" /> 取消审查
                </Button>
              ) : (
                <Button
                  onClick={handleReviewConfirm}
                  className="h-9 px-3 border-0 bg-gradient-to-br from-indigo-500 to-purple-500 text-white shadow-md shadow-indigo-500/20 hover:from-indigo-600 hover:to-purple-600"
                >
                  <Brain size={14} className="mr-1.5" />
                  {isMobile ? "LLM 审查" : "LLM 智能审查"}
                </Button>
              )}
            </div>

            <div className="flex items-center gap-3">
              {selected.size > 0 && (
                <Button variant="destructive" onClick={handleBatchDelete} className="h-9 px-3">
                  <Trash2 size={14} className="mr-1.5" /> 删除 {selected.size} 条
                </Button>
              )}

              {/* View mode toggle */}
              <ToggleGroup
                type="single"
                value={viewMode}
                onValueChange={(v) => { if (v) setViewMode(v as "list" | "graph"); }}
                variant="outline"
                className="justify-end"
              >
                <ToggleGroupItem value="list" className="h-9 px-3 text-sm data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
                  <List size={14} className="mr-1.5" /> 列表
                </ToggleGroupItem>
                <ToggleGroupItem value="graph" className="h-9 px-3 text-sm data-[state=on]:border-primary data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
                  <Network size={14} className="mr-1.5" /> 图谱
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
            ? `正在审查第 ${(reviewProgress.batch ?? 0) + 1}/${total} 批`
            : `已完成 ${reviewProgress.batch ?? 0}/${total} 批`
          : "准备中...";

        return (
          <div className="card" style={{ margin: 0, padding: isMobile ? "10px 12px" : "12px 16px" }}>
            <div className="flex items-center gap-2 mb-2">
              <Loader2 size={14} className="animate-spin text-indigo-500" />
              <span style={{ fontSize: 13, fontWeight: 500 }}>
                {batchLabel}
                {isLlmCalling && <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: 6 }}>等待 LLM 返回...</span>}
              </span>
              {reviewProgress.total_memories ? (
                <span style={{ fontSize: 12, color: "var(--muted)", marginLeft: "auto" }}>
                  {reviewProgress.processed ?? 0}/{reviewProgress.total_memories} 条记忆
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
                <span>删除 <b style={{ color: "#ef4444" }}>{reviewProgress.report.deleted}</b></span>
                <span>更新 <b style={{ color: "#f59e0b" }}>{reviewProgress.report.updated}</b></span>
                <span>合并 <b style={{ color: "#8b5cf6" }}>{reviewProgress.report.merged}</b></span>
                <span>保留 <b style={{ color: "#10b981" }}>{reviewProgress.report.kept}</b></span>
                {(reviewProgress.report.errors ?? 0) > 0 && (
                  <span>错误 <b style={{ color: "#ef4444" }}>{reviewProgress.report.errors}</b></span>
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
            <AlertDialogTitle>启动 LLM 智能审查</AlertDialogTitle>
            <AlertDialogDescription>
              将由大模型逐条审查所有记忆，删除垃圾、合并重复。此操作在后台异步执行，你可以随时查看进度或取消。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleReview}
              className="bg-gradient-to-br from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white border-0"
            >
              确认审查
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
              <span className="ml-2 text-sm text-muted-foreground">加载图谱组件...</span>
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
                <Loader2 size={20} className="inline animate-spin mr-2" />加载中...
              </CardContent>
            </Card>
          ) : memories.length === 0 ? (
            <Card className="shadow-sm">
              <CardContent className="py-10 text-center text-muted-foreground">
                暂无记忆数据
              </CardContent>
            </Card>
          ) : (
            <>
              <div className="flex items-center gap-2 px-1">
                <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                  <Checkbox checked={selected.size === memories.length && memories.length > 0} onCheckedChange={selectAll} />
                  全选 ({memories.length})
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
                        {TYPE_LABELS[m.type] || m.type}
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
                          <span className="text-xs text-muted-foreground">分数:</span>
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
                            {m.subject && <span>主体: {m.subject}</span>}
                            {m.subject && m.predicate && <span> · </span>}
                            {m.predicate && <span>属性: {m.predicate}</span>}
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
                          <Pencil size={14} className="mr-1" /> 编辑
                        </Button>
                        <Button variant="outline" size="sm" className="h-8 text-xs text-destructive border-destructive/30 hover:text-destructive hover:bg-destructive/10" onClick={() => handleDelete(m.id)}>
                          <Trash2 size={14} className="mr-1" /> 删除
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
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[36px] px-3">
                  <Checkbox
                    checked={selected.size === memories.length && memories.length > 0}
                    onCheckedChange={selectAll}
                  />
                </TableHead>
                <TableHead className="w-[80px]">类型</TableHead>
                <TableHead>内容</TableHead>
                <TableHead className="w-[60px] text-center">分数</TableHead>
                <TableHead className="w-[90px] text-center">创建时间</TableHead>
                <TableHead className="w-[100px] text-center">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-10 text-muted-foreground">
                    <Loader2 size={20} className="inline animate-spin mr-2" />加载中...
                  </TableCell>
                </TableRow>
              ) : memories.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center py-10 text-muted-foreground">
                    暂无记忆数据
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
                      {TYPE_LABELS[m.type] || m.type}
                    </span>
                  </TableCell>
                  <TableCell className="max-w-[400px]">
                    {editingId === m.id ? (
                      <div className="flex flex-col gap-1.5">
                        <Textarea
                          value={editContent}
                          onChange={e => setEditContent(e.target.value)}
                          rows={3}
                          className="resize-y text-xs"
                        />
                        <div className="flex items-center gap-1.5">
                          <span className="text-[11px] text-muted-foreground">分数:</span>
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
                        <div className="leading-relaxed break-words whitespace-pre-wrap">
                          {m.content}
                        </div>
                        {(m.subject || m.predicate) && (
                          <div className="mt-1 text-[11px] text-muted-foreground">
                            {m.subject && <span>主体: {m.subject}</span>}
                            {m.subject && m.predicate && <span> · </span>}
                            {m.predicate && <span>属性: {m.predicate}</span>}
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
                      <Button variant="ghost" size="icon-sm" title="编辑" className="text-muted-foreground hover:text-foreground" onClick={() => startEdit(m)}>
                        <Pencil size={13} />
                      </Button>
                      <Button variant="ghost" size="icon-sm" title="删除" className="text-muted-foreground hover:text-destructive" onClick={() => handleDelete(m.id)}>
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
      <AlertDialog open={!!confirmDialog} onOpenChange={open => { if (!open) setConfirmDialog(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认操作</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-wrap">{confirmDialog?.message}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => { confirmDialog?.onConfirm(); setConfirmDialog(null); }}>
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
