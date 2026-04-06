import { useReactFlow, Panel } from "@xyflow/react";
import { ZoomIn, ZoomOut, Maximize } from "lucide-react";
import { Button } from "../ui/button";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "../ui/tooltip";

export function OrgCanvasControls() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();

  return (
    <Panel position="bottom-left">
      <TooltipProvider>
        <div className="flex flex-col gap-1 rounded-lg border border-border/50 bg-card/90 p-1 shadow-md backdrop-blur-sm">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => zoomIn()}>
                <ZoomIn className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">放大</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => zoomOut()}>
                <ZoomOut className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">缩小</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-xs" onClick={() => fitView({ padding: 0.2 })}>
                <Maximize className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">适应视图</TooltipContent>
          </Tooltip>
        </div>
      </TooltipProvider>
    </Panel>
  );
}
