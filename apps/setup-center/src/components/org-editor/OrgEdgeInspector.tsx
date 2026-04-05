import type { Node } from "@xyflow/react";
import { IconX, IconTrash } from "../../icons";
import { EDGE_COLORS } from "./helpers";
import type { OrgEdgeData } from "./types";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Checkbox } from "../ui/checkbox";
import { Slider } from "../ui/slider";
import { Label } from "../ui/label";

export interface OrgEdgeInspectorProps {
  selectedEdge: OrgEdgeData;
  nodes: Node[];
  updateEdgeData: (field: string, value: any) => void;
  handleDeleteEdge: () => void;
  onClose: () => void;
}

export function OrgEdgeInspector({
  selectedEdge,
  nodes,
  updateEdgeData,
  handleDeleteEdge,
  onClose,
}: OrgEdgeInspectorProps) {
  const sourceLabel = (() => {
    const n = nodes.find((node) => node.id === selectedEdge.source);
    return (n?.data as any)?.role_title || selectedEdge.source;
  })();
  const targetLabel = (() => {
    const n = nodes.find((node) => node.id === selectedEdge.target);
    return (n?.data as any)?.role_title || selectedEdge.target;
  })();

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between gap-3 overflow-x-auto">
        <div className="truncate font-semibold text-sm" title="连线属性">连线属性</div>
        <Button variant="ghost" size="icon-xs" onClick={onClose}>
          <IconX size={12} />
        </Button>
      </div>

      <div className="rounded-xl border bg-card/60 p-3 text-xs text-muted-foreground leading-relaxed space-y-1.5">
        <div>起点：<strong className="text-foreground font-semibold">{sourceLabel}</strong></div>
        <div>终点：<strong className="text-foreground font-semibold">{targetLabel}</strong></div>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-semibold">连线类型</div>
        <div className="grid grid-cols-2 gap-1.5">
          {([
            { key: "hierarchy", label: "上下级", color: EDGE_COLORS.hierarchy },
            { key: "collaborate", label: "协作", color: EDGE_COLORS.collaborate },
            { key: "escalate", label: "上报", color: EDGE_COLORS.escalate },
            { key: "consult", label: "咨询", color: EDGE_COLORS.consult || "var(--muted)" },
          ] as const).map((t) => (
            <Button
              key={t.key}
              variant="outline"
              size="xs"
              className="h-8 w-full justify-center gap-1.5 text-[11px]"
              style={{
                background: selectedEdge.edge_type === t.key ? `${t.color}20` : undefined,
                color: selectedEdge.edge_type === t.key ? t.color : "var(--muted)",
                borderColor: selectedEdge.edge_type === t.key ? t.color : undefined,
                fontWeight: selectedEdge.edge_type === t.key ? 600 : 400,
              }}
              onClick={() => updateEdgeData("edge_type", t.key)}
            >
              <span style={{ display: "inline-block", width: 12, height: 2, background: t.color, borderRadius: 1 }} />
              {t.label}
            </Button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-semibold">标签</div>
        <Input
          className="h-9 text-sm"
          placeholder="可选，如「技术指导」「审批」"
          value={selectedEdge.label || ""}
          onChange={(e) => updateEdgeData("label", e.target.value)}
        />
      </div>

      <div className="rounded-xl border bg-card/60 p-3 space-y-3">
        <div className="flex items-start gap-3">
          <Checkbox
            id="edge-bidir"
            checked={selectedEdge.bidirectional ?? true}
            onCheckedChange={(v) => updateEdgeData("bidirectional", !!v)}
            className="mt-0.5"
          />
          <div className="space-y-1">
            <Label htmlFor="edge-bidir" className="text-sm font-semibold cursor-pointer">双向通信</Label>
            <div className="text-xs text-muted-foreground leading-relaxed">
              开启后，终点节点也可以向起点节点回传消息；关闭后，只允许从起点发往终点。
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3 overflow-x-auto">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold" title="优先级">优先级</div>
              <div className="truncate text-xs text-muted-foreground" title="数值越高，表示这条关系越重要。">数值越高，表示这条关系越重要。</div>
            </div>
            <div className="min-w-6 shrink-0 text-right text-sm font-semibold text-primary" title={String(selectedEdge.priority ?? 0)}>
              {selectedEdge.priority ?? 0}
            </div>
          </div>
          <Slider
            min={0} max={10} step={1}
            value={[selectedEdge.priority ?? 0]}
            onValueChange={([v]) => updateEdgeData("priority", v)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-xs font-semibold">通信频率上限（次/小时）</div>
        <Input
          type="number" min={1} max={999}
          className="h-9 w-24 text-sm"
          value={selectedEdge.bandwidth_limit ?? 60}
          onChange={(e) => updateEdgeData("bandwidth_limit", Number(e.target.value))}
        />
      </div>

      <div className="border-t pt-3">
        <Button
          variant="outline"
          size="sm"
          className="w-full text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
          onClick={handleDeleteEdge}
        >
          <IconTrash size={12} /> 删除连线
        </Button>
      </div>
    </div>
  );
}
