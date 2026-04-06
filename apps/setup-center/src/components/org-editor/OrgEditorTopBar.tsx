import {
  IconSitemap,
  IconClipboard,
  IconCircleDot,
  IconPlay,
  IconStop,
  IconSave,
  IconLayoutGrid,
  IconInbox,
  IconMaximize2,
  IconMenu,
} from "../../icons";
import { canOpenPopupWindow, openPopupWindow } from "../../platform";
import { STATUS_COLORS } from "./helpers";
import type { OrgFull, RightPanelMode } from "./types";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { ToggleGroup, ToggleGroupItem } from "../ui/toggle-group";
import { Tooltip, TooltipTrigger, TooltipContent } from "../ui/tooltip";

const TOPBAR_STATUS_LABELS: Record<string, string> = {
  active: "运行中",
  dormant: "未启动",
  archived: "已归档",
  busy: "忙碌",
  waiting: "等待",
  frozen: "冻结",
  error: "异常",
  idle: "空闲",
};

export interface OrgEditorTopBarProps {
  currentOrg: OrgFull;
  setCurrentOrg: (org: OrgFull) => void;
  showLeftPanel: boolean;
  setShowLeftPanel: (v: boolean) => void;
  isMobile: boolean;
  saveStatus: "idle" | "saving" | "saved" | "error";
  doSaveRef: React.RefObject<(quiet?: boolean) => Promise<boolean>>;
  liveMode: boolean;
  orgStats: any;
  viewMode: "canvas" | "projects" | "dashboard";
  setViewMode: (v: "canvas" | "projects" | "dashboard") => void;
  autoSave: () => void;
  handleStartOrg: () => void;
  handleStopOrg: () => void;
  layoutLocked: boolean;
  setLayoutLocked: (v: boolean | ((prev: boolean) => boolean)) => void;
  saving: boolean;
  handleSave: () => void;
  rightPanel: RightPanelMode;
  setRightPanel: (v: RightPanelMode) => void;
  activityFeed: any[];
}

export function OrgEditorTopBar({
  currentOrg,
  setCurrentOrg,
  showLeftPanel,
  setShowLeftPanel,
  isMobile,
  saveStatus,
  doSaveRef,
  liveMode,
  orgStats,
  viewMode,
  setViewMode,
  autoSave,
  handleStartOrg,
  handleStopOrg,
  layoutLocked,
  setLayoutLocked,
  saving,
  handleSave,
  rightPanel,
  setRightPanel,
  activityFeed,
}: OrgEditorTopBarProps) {
  const statusLabel = TOPBAR_STATUS_LABELS[currentOrg.status] || currentOrg.status;
  const statusColor = STATUS_COLORS[currentOrg.status] || "var(--muted)";
  const isDormant = currentOrg.status === "dormant";
  const isArchived = currentOrg.status === "archived";

  return (
    <div className="org-topbar">
      {/* Left: Org info */}
      <div className="org-topbar-left">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-xs" onClick={() => setShowLeftPanel(!showLeftPanel)}>
              <IconMenu size={14} />
            </Button>
          </TooltipTrigger>
          <TooltipContent>组织列表</TooltipContent>
        </Tooltip>
        {!isMobile && (
          <input
            className="org-topbar-name"
            value={currentOrg.name}
            onChange={(e) => setCurrentOrg({ ...currentOrg, name: e.target.value })}
          />
        )}
        <Badge
          variant="outline"
          className="h-8 min-w-[68px] justify-center rounded-full px-2.5 text-[11px] leading-none font-semibold"
          style={{
            background: `${statusColor}20`,
            color: statusColor,
            borderColor: `${statusColor}40`,
          }}
        >
          {statusLabel}
        </Badge>
        {saveStatus !== "idle" && (
          <div className={`org-save-indicator org-save-indicator--${saveStatus}`}>
            {saveStatus === "saving" ? "保存中..." : saveStatus === "saved" ? "已自动保存~" : <span onClick={() => doSaveRef.current!()} style={{ cursor: "pointer" }}>保存失败 · 重试</span>}
          </div>
        )}
        {liveMode && orgStats && !isMobile && (
          <div className="org-topbar-stats">
            <span className="org-health-dot" style={{
              background: orgStats.health === "critical" ? "#ef4444" : orgStats.health === "warning" ? "#f59e0b" : orgStats.health === "attention" ? "#3b82f6" : "#22c55e",
              animation: orgStats.health !== "healthy" ? "orgDotPulse 1.5s ease-in-out infinite" : undefined,
            }} />
            <span>✓{orgStats.total_tasks_completed ?? 0}</span>
            <span>✉{orgStats.total_messages_exchanged ?? 0}</span>
            {orgStats.pending_messages > 0 && <span style={{ color: "#f59e0b" }}>▪{orgStats.pending_messages}</span>}
            {orgStats.anomalies?.length > 0 && <span style={{ color: "#ef4444", fontWeight: 600 }}>!{orgStats.anomalies.length}</span>}
          </div>
        )}
      </div>

      {/* Center: View tabs */}
      <div className="org-topbar-center">
        <ToggleGroup
          type="single"
          value={viewMode}
          onValueChange={(v) => { if (v) { autoSave(); setViewMode(v as typeof viewMode); } }}
          variant="outline"
          size="sm"
          className="org-topbar-tabs"
        >
          <ToggleGroupItem value="canvas" className="gap-1 text-xs">
            <IconSitemap size={13} /> 编排
          </ToggleGroupItem>
          <ToggleGroupItem value="projects" className="gap-1 text-xs">
            <IconClipboard size={13} /> 项目
          </ToggleGroupItem>
          <ToggleGroupItem value="dashboard" className="gap-1 text-xs">
            <IconCircleDot size={13} /> 看板
          </ToggleGroupItem>
        </ToggleGroup>
      </div>

      {/* Right: Actions */}
      <div className="org-topbar-right">
        {isArchived ? (
          <span className="text-[11px] text-muted-foreground">已归档</span>
        ) : (
          <>
            <Button
              variant="outline"
              size="sm"
              className={`min-w-[88px] justify-center ${
                isDormant
                  ? "text-green-600 border-green-500/30 hover:bg-green-500/10"
                  : "text-destructive border-destructive/30 hover:bg-destructive/10"
              }`}
              onClick={isDormant ? handleStartOrg : handleStopOrg}
            >
              {isDormant ? <IconPlay size={14} /> : <IconStop size={14} />}
              {!isMobile && (isDormant ? "启动" : "停止")}
            </Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <div
                  className={`org-topbar-live-pill ${
                    liveMode ? "org-topbar-live-pill--active" : ""
                  }`}
                >
                  <IconCircleDot size={14} />
                </div>
              </TooltipTrigger>
              <TooltipContent>
                {liveMode ? "实况已开启（组织运行中自动开启）" : "实况待机（启动组织后自动开启）"}
              </TooltipContent>
            </Tooltip>
          </>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className={
                layoutLocked
                  ? "border-amber-400/50 bg-amber-500/8 text-amber-600 hover:bg-amber-500/15"
                  : "border-primary/30 bg-primary/5 text-primary hover:bg-primary/10"
              }
              onClick={() => setLayoutLocked((v) => !v)}
            >
              {layoutLocked
                ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>
              }
              {!isMobile && (layoutLocked ? "拖拽已锁" : "拖拽已开")}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{layoutLocked ? "解锁布局（可拖拽/连线）" : "锁定布局（防止误操作）"}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className={`org-save-btn ${saving ? "org-save-btn--saving" : ""}`}
              onClick={handleSave}
              disabled={saving}
            >
              <IconSave size={14} /> {saving ? "..." : (!isMobile && "保存")}
            </Button>
          </TooltipTrigger>
          <TooltipContent>保存</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              className={rightPanel === "org" ? "text-primary font-semibold" : ""}
              onClick={() => {
                if (rightPanel === "node" || rightPanel === "edge") autoSave();
                setRightPanel(rightPanel === "org" ? "none" : "org");
              }}
            >
              <IconLayoutGrid size={15} />
            </Button>
          </TooltipTrigger>
          <TooltipContent>组织设置</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              className={rightPanel === "inbox" ? "text-primary font-semibold" : ""}
              onClick={() => setRightPanel(rightPanel === "inbox" ? "none" : "inbox")}
              style={{ position: "relative" }}
            >
              <IconInbox size={15} />
              {activityFeed.length > 0 && <span className="org-notif-dot" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>收件箱</TooltipContent>
        </Tooltip>
        {canOpenPopupWindow() && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => {
                  const base = window.location.href.split("#")[0].split("?")[0];
                  const orgParam = currentOrg?.id ? `?org=${encodeURIComponent(currentOrg.id)}` : "";
                  openPopupWindow(
                    `${base}${orgParam}#/org-editor`,
                    "org-editor-popup",
                    { width: 1400, height: 900, title: `组织编排 · ${currentOrg?.name || ""}` },
                  );
                }}
              >
                <IconMaximize2 size={15} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>在独立窗口中打开</TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  );
}
