import type { Node } from "@xyflow/react";
import { TASK_STATUS_LABELS } from "./helpers";
import { Badge } from "../ui/badge";

export function NodeTasksTabContent({
  nodeTasks,
  nodeActivePlan,
  loading,
  nodes,
  apiBaseUrl,
  orgId,
  fmtDateTime,
}: {
  nodeTasks: { assigned: any[]; delegated: any[] } | null;
  nodeActivePlan: any;
  loading: boolean;
  nodes: Node[];
  apiBaseUrl: string;
  orgId: string;
  fmtDateTime: (v: string | number | undefined | null) => string;
}) {
  const nodeMap = new Map(nodes.map((n) => [n.id, (n.data as any)?.role_title || n.id]));
  const getNodeLabel = (id: string | null) => (id ? nodeMap.get(id) || id : "-");
  const assignedCount = nodeTasks?.assigned?.length ?? 0;
  const delegatedCount = nodeTasks?.delegated?.length ?? 0;

  if (loading) {
    return (
      <div className="rounded-xl border bg-card p-3 text-xs text-muted-foreground">加载中...</div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5 text-xs">
      <div className="rounded-xl border bg-card p-2.5">
        <div className="text-sm font-semibold">任务视图</div>
        <div className="mt-1 text-[11px] leading-5 text-muted-foreground">
          查看该节点当前正在执行的任务，以及收到和委派出去的任务流转情况。
        </div>
        <div className="mt-2 grid grid-cols-3 gap-1.5">
          <div className="rounded-lg border bg-background px-2.5 py-2">
            <div className="text-[10px] text-muted-foreground">当前执行</div>
            <div className="mt-1 text-sm font-semibold">{nodeActivePlan ? "1" : "0"}</div>
          </div>
          <div className="rounded-lg border bg-background px-2.5 py-2">
            <div className="text-[10px] text-muted-foreground">分配给我</div>
            <div className="mt-1 text-sm font-semibold">{assignedCount}</div>
          </div>
          <div className="rounded-lg border bg-background px-2.5 py-2">
            <div className="text-[10px] text-muted-foreground">我已委派</div>
            <div className="mt-1 text-sm font-semibold">{delegatedCount}</div>
          </div>
        </div>
      </div>

      {nodeActivePlan && (
        <div className="rounded-xl border bg-card p-2.5">
          <div className="mb-1.5 text-sm font-semibold text-amber-700">
            当前任务
          </div>
          <div className="mb-2 text-sm font-medium leading-5">{nodeActivePlan.title}</div>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-[10px] text-muted-foreground">进度</span>
            <div className="flex-1 h-1 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${nodeActivePlan.progress_pct ?? 0}%` }}
              />
            </div>
            <span className="text-[10px] text-muted-foreground">{nodeActivePlan.progress_pct ?? 0}%</span>
          </div>
          {(nodeActivePlan.plan_steps?.length ?? 0) > 0 && (
            <div className="rounded-lg border bg-background px-2.5 py-2 text-[11px] space-y-1">
              {(nodeActivePlan.plan_steps || []).map((s: any, i: number) => {
                const st = s.status || "pending";
                const icon = st === "completed" ? "✓" : st === "in_progress" ? "→" : "○";
                const color = st === "completed" ? "#22c55e" : st === "in_progress" ? "#3b82f6" : undefined;
                return (
                  <div key={s.id || i} className="flex items-start gap-1.5">
                    <span className="shrink-0 font-semibold" style={{ color }}>{icon}</span>
                    <span className="text-foreground leading-5">{s.description || s.title || `步骤 ${i + 1}`}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div className="rounded-xl border bg-card p-2.5">
        <div className="mb-1.5 flex items-center justify-between gap-2 overflow-x-auto">
          <div className="truncate text-sm font-semibold" title="分配给我的任务">分配给我的任务</div>
          <Badge variant="outline" className="h-5 shrink-0 px-1.5 text-[10px]" title={String(assignedCount)}>
            {assignedCount}
          </Badge>
        </div>
        <div className="mb-2 text-[11px] leading-5 text-muted-foreground">
          这些任务正等待该节点处理或继续推进。
        </div>
        {assignedCount === 0 ? (
          <div className="rounded-lg border border-dashed px-3 py-3 text-[11px] text-muted-foreground">暂无</div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {(nodeTasks?.assigned || []).map((t: any) => (
              <div key={t.id} className="rounded-lg border bg-background px-2.5 py-2">
                <div className="mb-1 text-sm font-medium leading-5">{t.title}</div>
                <div className="flex items-center gap-1.5 overflow-x-auto text-[10px] whitespace-nowrap">
                  <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                    {TASK_STATUS_LABELS[t.status] || t.status}
                  </Badge>
                  <span className="text-muted-foreground">{(t.progress_pct ?? 0)}%</span>
                </div>
                <div className="mt-1.5 h-1 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${Math.min(100, t.progress_pct ?? 0)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-2.5">
        <div className="mb-1.5 flex items-center justify-between gap-2 overflow-x-auto">
          <div className="truncate text-sm font-semibold" title="我委派的任务">我委派的任务</div>
          <Badge variant="outline" className="h-5 shrink-0 px-1.5 text-[10px]" title={String(delegatedCount)}>
            {delegatedCount}
          </Badge>
        </div>
        <div className="mb-2 text-[11px] leading-5 text-muted-foreground">
          这些任务已经从当前节点分发给其他执行节点，可在这里快速查看状态。
        </div>
        {delegatedCount === 0 ? (
          <div className="rounded-lg border border-dashed px-3 py-3 text-[11px] text-muted-foreground">暂无</div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {(nodeTasks?.delegated || []).map((t: any) => (
              <div key={t.id} className="rounded-lg border bg-background px-2.5 py-2">
                <div className="mb-1 text-sm font-medium leading-5">{t.title}</div>
                <div className="flex items-center gap-1.5 overflow-x-auto text-[10px] whitespace-nowrap">
                  <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                    {TASK_STATUS_LABELS[t.status] || t.status}
                  </Badge>
                  <span className="text-muted-foreground">{(t.progress_pct ?? 0)}%</span>
                  <span className="text-muted-foreground ml-auto">
                    执行人: {getNodeLabel(t.assignee_node_id)}
                  </span>
                </div>
                <div className="mt-1.5 h-1 rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${Math.min(100, t.progress_pct ?? 0)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
