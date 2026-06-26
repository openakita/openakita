import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { IS_WEB, onWsEvent } from "../platform";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  Loader2, RefreshCw, ShieldCheck, ShieldX, Clock, AlertTriangle, CheckCircle2, XCircle,
} from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { toast } from "sonner";

type PendingApprovalEntry = {
  id: string;
  task_id: string | null;
  session_id: string;
  tool_name: string;
  params: Record<string, unknown>;
  approval_class: string | null;
  reason: string;
  unattended_strategy: string;
  created_at: number;
  expires_at: number;
  status: "pending" | "approved" | "denied" | "expired";
  resolved_at: number | null;
  resolved_by: string | null;
  resolution: string | null;
  note: string;
};

type StatsMap = {
  pending: number;
  approved: number;
  denied: number;
  expired: number;
};

type ViewFilter = "active" | "all";

export function PendingApprovalsView({
  serviceRunning,
  apiBaseUrl,
}: {
  serviceRunning: boolean;
  apiBaseUrl: string;
}) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<PendingApprovalEntry[]>([]);
  const [stats, setStats] = useState<StatsMap | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<ViewFilter>("active");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [resolveNote, setResolveNote] = useState("");
  const [resolving, setResolving] = useState<string | null>(null);

  const toggleExpand = useCallback((id: string) => {
    setExpandedId(prev => {
      if (prev === id) return null;
      setResolveNote("");
      return id;
    });
  }, []);

  const fetchEntries = useCallback(async (showLoading = true) => {
    if (!serviceRunning) return;
    if (showLoading) setLoading(true);
    try {
      const params = filter === "all" ? "?include=all" : "";
      const resp = await safeFetch(`${apiBaseUrl}/api/pending_approvals${params}`);
      const data = await resp.json();
      setEntries(data.entries ?? []);
    } catch {
      // silent — background refresh failures are expected during reconnect
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [serviceRunning, apiBaseUrl, filter]);

  const fetchStats = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/pending_approvals/stats`);
      setStats(await resp.json());
    } catch {
      // silent
    }
  }, [serviceRunning, apiBaseUrl]);

  useEffect(() => {
    fetchEntries();
    fetchStats();
  }, [fetchEntries, fetchStats]);

  // Auto-refresh every 30s
  useEffect(() => {
    if (!serviceRunning) return;
    const interval = setInterval(() => {
      fetchEntries(false);
      fetchStats();
    }, 30_000);
    return () => clearInterval(interval);
  }, [serviceRunning, fetchEntries, fetchStats]);

  // WebSocket real-time refresh
  useEffect(() => {
    if (!IS_WEB) return;
    return onWsEvent((event) => {
      if (
        event === "pending_approval_created" ||
        event === "pending_approval_resolved"
      ) {
        fetchEntries(false);
        fetchStats();
      }
    });
  }, [fetchEntries, fetchStats]);

  const handleResolve = useCallback(async (id: string, decision: "allow" | "deny") => {
    setResolving(id);
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/pending_approvals/${id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note: resolveNote }),
      });
      const data = await resp.json();
      toast.success(
        decision === "allow"
          ? t("pendingApprovals.approved")
          : t("pendingApprovals.denied")
      );
      if (data.follow_up?.task_resumed) {
        toast.info(t("pendingApprovals.taskResumed"));
      }
      setResolveNote("");
      setExpandedId(null);
      fetchEntries(false);
      fetchStats();
    } catch {
      toast.error(t("pendingApprovals.resolveFailed"));
    } finally {
      setResolving(null);
    }
  }, [apiBaseUrl, resolveNote, t, fetchEntries, fetchStats]);

  const formatTime = (ts: number) => {
    return new Date(ts * 1000).toLocaleString();
  };

  const timeRemaining = (expiresAt: number) => {
    const now = Date.now() / 1000;
    const diff = expiresAt - now;
    if (diff <= 0) return t("pendingApprovals.expired");
    const hours = Math.floor(diff / 3600);
    const minutes = Math.floor((diff % 3600) / 60);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  };

  const statusBadge = (status: string) => {
    switch (status) {
      case "pending":
        return <Badge variant="outline" className="text-amber-600 border-amber-300"><Clock className="w-3 h-3 mr-1" />{t("pendingApprovals.statusPending")}</Badge>;
      case "approved":
        return <Badge variant="outline" className="text-green-600 border-green-300"><CheckCircle2 className="w-3 h-3 mr-1" />{t("pendingApprovals.statusApproved")}</Badge>;
      case "denied":
        return <Badge variant="outline" className="text-red-600 border-red-300"><XCircle className="w-3 h-3 mr-1" />{t("pendingApprovals.statusDenied")}</Badge>;
      case "expired":
        return <Badge variant="outline" className="text-gray-500 border-gray-300"><AlertTriangle className="w-3 h-3 mr-1" />{t("pendingApprovals.statusExpired")}</Badge>;
      default:
        return <Badge variant="outline">{status}</Badge>;
    }
  };

  if (!serviceRunning) {
    return (
      <div className="p-6 text-center text-muted-foreground">
        <AlertTriangle className="w-8 h-8 mx-auto mb-2 opacity-50" />
        <p>{t("pendingApprovals.serviceNotRunning")}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 p-4 h-full overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">{t("pendingApprovals.title")}</h2>
          <p className="text-sm text-muted-foreground">{t("pendingApprovals.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => { fetchEntries(); fetchStats(); }}
            disabled={loading}
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </Button>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-4 gap-3">
          {([
            { key: "pending", color: "text-amber-600", bg: "bg-amber-50 dark:bg-amber-950/20" },
            { key: "approved", color: "text-green-600", bg: "bg-green-50 dark:bg-green-950/20" },
            { key: "denied", color: "text-red-600", bg: "bg-red-50 dark:bg-red-950/20" },
            { key: "expired", color: "text-gray-500", bg: "bg-gray-50 dark:bg-gray-950/20" },
          ] as const).map(({ key, color, bg }) => (
            <div key={key} className={cn("rounded-lg p-3 text-center", bg)}>
              <div className={cn("text-2xl font-bold", color)}>
                {stats[key]}
              </div>
              <div className="text-xs text-muted-foreground">
                {t(`pendingApprovals.stat_${key}`)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Filter */}
      <div className="flex gap-2">
        <Button
          variant={filter === "active" ? "default" : "outline"}
          size="sm"
          onClick={() => setFilter("active")}
        >
          {t("pendingApprovals.filterActive")}
          {stats && stats.pending > 0 && (
            <Badge variant="secondary" className="ml-1.5">{stats.pending}</Badge>
          )}
        </Button>
        <Button
          variant={filter === "all" ? "default" : "outline"}
          size="sm"
          onClick={() => setFilter("all")}
        >
          {t("pendingApprovals.filterAll")}
        </Button>
      </div>

      {/* List */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <ShieldCheck className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p>{t("pendingApprovals.noEntries")}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {entries.map((entry) => (
            <Card
              key={entry.id}
              className={cn(
                "transition-all",
                entry.status === "pending" && "border-amber-200 dark:border-amber-800",
                expandedId === entry.id && "ring-2 ring-primary/20",
              )}
            >
              <CardHeader
                className="cursor-pointer py-3 px-4"
                onClick={() => toggleExpand(entry.id)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {statusBadge(entry.status)}
                    <code className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">
                      {entry.tool_name}
                    </code>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    {entry.status === "pending" && (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger>
                            <span className="flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {timeRemaining(entry.expires_at)}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            {t("pendingApprovals.expiresAt", { time: formatTime(entry.expires_at) })}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    )}
                    <span className="font-mono opacity-60">{entry.id}</span>
                  </div>
                </div>
                <CardTitle className="text-sm font-normal mt-1.5 text-muted-foreground">
                  {entry.reason}
                </CardTitle>
              </CardHeader>

              {expandedId === entry.id && (
                <CardContent className="pt-0 px-4 pb-4">
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs mb-3">
                    <div>
                      <span className="text-muted-foreground">{t("pendingApprovals.createdAt")}:</span>{" "}
                      {formatTime(entry.created_at)}
                    </div>
                    <div>
                      <span className="text-muted-foreground">{t("pendingApprovals.strategy")}:</span>{" "}
                      <code className="bg-muted px-1 rounded">{entry.unattended_strategy}</code>
                    </div>
                    {entry.task_id && (
                      <div>
                        <span className="text-muted-foreground">{t("pendingApprovals.taskId")}:</span>{" "}
                        <code className="bg-muted px-1 rounded">{entry.task_id}</code>
                      </div>
                    )}
                    {entry.session_id && (
                      <div>
                        <span className="text-muted-foreground">{t("pendingApprovals.sessionId")}:</span>{" "}
                        <code className="bg-muted px-1 rounded">{entry.session_id.slice(0, 12)}…</code>
                      </div>
                    )}
                    {entry.approval_class && (
                      <div>
                        <span className="text-muted-foreground">{t("pendingApprovals.approvalClass")}:</span>{" "}
                        <code className="bg-muted px-1 rounded">{entry.approval_class}</code>
                      </div>
                    )}
                    {entry.resolved_at && (
                      <div>
                        <span className="text-muted-foreground">{t("pendingApprovals.resolvedAt")}:</span>{" "}
                        {formatTime(entry.resolved_at)}
                      </div>
                    )}
                    {entry.resolved_by && (
                      <div>
                        <span className="text-muted-foreground">{t("pendingApprovals.resolvedBy")}:</span>{" "}
                        {entry.resolved_by}
                      </div>
                    )}
                    {entry.note && (
                      <div className="col-span-2">
                        <span className="text-muted-foreground">{t("pendingApprovals.note")}:</span>{" "}
                        {entry.note}
                      </div>
                    )}
                  </div>

                  {/* Tool params preview */}
                  {entry.params && Object.keys(entry.params).length > 0 && (
                    <details className="mb-3">
                      <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
                        {t("pendingApprovals.viewParams")}
                      </summary>
                      <pre className="mt-1 text-xs bg-muted p-2 rounded overflow-auto max-h-40">
                        {JSON.stringify(entry.params, null, 2)}
                      </pre>
                    </details>
                  )}

                  {/* Action buttons for pending entries */}
                  {entry.status === "pending" && (
                    <div className="flex flex-col gap-2 mt-2 pt-2 border-t">
                      <Textarea
                        placeholder={t("pendingApprovals.notePlaceholder")}
                        value={resolveNote}
                        onChange={(e) => setResolveNote(e.target.value)}
                        className="text-xs h-16 resize-none"
                      />
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="default"
                          className="bg-green-600 hover:bg-green-700"
                          disabled={resolving === entry.id}
                          onClick={() => handleResolve(entry.id, "allow")}
                        >
                          {resolving === entry.id
                            ? <Loader2 className="w-3 h-3 animate-spin mr-1" />
                            : <ShieldCheck className="w-3 h-3 mr-1" />
                          }
                          {t("pendingApprovals.approve")}
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={resolving === entry.id}
                          onClick={() => handleResolve(entry.id, "deny")}
                        >
                          {resolving === entry.id
                            ? <Loader2 className="w-3 h-3 animate-spin mr-1" />
                            : <ShieldX className="w-3 h-3 mr-1" />
                          }
                          {t("pendingApprovals.deny")}
                        </Button>
                      </div>
                    </div>
                  )}
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
