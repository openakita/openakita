import { useState } from "react";
import { MiniMap, Panel, type Node } from "@xyflow/react";
import { Map as MapIcon, X as XIcon } from "lucide-react";
import { Button } from "../ui/button";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "../ui/tooltip";
import { getDeptColor, STATUS_COLORS } from "./helpers";

const EDGE_TYPE_LABELS: Record<string, string> = {
  hierarchy: "上下级",
  collaborate: "协作",
  escalate: "上报",
  consult: "咨询",
};

export function CollapsibleMiniMap({ edgeColors }: { edgeColors: Record<string, string> }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Panel position="bottom-right">
      <div className="flex flex-col items-end gap-1.5">
        {expanded ? (
          <div className="rounded-lg border border-border/50 bg-card/90 shadow-md backdrop-blur-sm overflow-hidden">
            <div className="flex items-center justify-between px-2 pt-1.5 pb-0.5">
              <span className="text-[10px] font-medium text-muted-foreground">导航图</span>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => setExpanded(false)}
              >
                <XIcon className="size-3" />
              </Button>
            </div>
            <MiniMap
              nodeStrokeWidth={2}
              pannable
              zoomable
              nodeColor={(node: Node) => {
                const d = node.data as any;
                if (d?.status && d.status !== "idle") {
                  const sc = STATUS_COLORS[d.status];
                  if (sc && !sc.startsWith("var")) return sc;
                  if (d.status === "busy") return "#6366f1";
                  if (d.status === "error") return "#ef4444";
                  if (d.status === "frozen") return "#93c5fd";
                  if (d.status === "waiting") return "#f59e0b";
                }
                if (d?.department) return getDeptColor(d.department);
                return "#94a3b8";
              }}
              style={{ position: "relative", width: 180, height: 120, margin: 0, background: "var(--card-bg, #fff)" }}
            />
            <div className="flex flex-wrap gap-2.5 px-2 py-1.5 border-t border-border/30">
              {Object.entries(edgeColors).map(([type, color]) => (
                <span key={type} className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                  <span className="inline-block w-4 h-0.5 rounded-sm" style={{ background: color }} />
                  {EDGE_TYPE_LABELS[type] || type}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="icon-xs"
                  className="shadow-md"
                  onClick={() => setExpanded(true)}
                >
                  <MapIcon className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="left">展开导航图</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
    </Panel>
  );
}
