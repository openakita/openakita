import type { RefObject, ChangeEvent } from "react";
import { IconClipboard, IconPlus, IconUpload, IconX, IconBuilding, IconTrash } from "../../icons";
import type { OrgSummary, TemplateSummary } from "./types";
import { Button } from "../ui/button";
import { Tooltip, TooltipTrigger, TooltipContent } from "../ui/tooltip";

export interface OrgListPanelProps {
  showTemplates: boolean;
  setShowTemplates: (v: boolean) => void;
  templates: TemplateSummary[];
  handleCreateOrg: () => void;
  handleCreateFromTemplate: (id: string) => void;
  orgImportRef: RefObject<HTMLInputElement>;
  handleImportOrg: (e: ChangeEvent<HTMLInputElement>) => void;
  isMobile: boolean;
  setShowLeftPanel: (v: boolean) => void;
  orgList: OrgSummary[];
  selectedOrgId: string | null;
  setSelectedOrgId: (id: string) => void;
  doSave: (quiet?: boolean) => Promise<boolean>;
  confirmDeleteOrgId: string | null;
  setConfirmDeleteOrgId: (id: string | null) => void;
  handleDeleteOrg: (id: string) => void;
}

export function OrgListPanel({
  showTemplates,
  setShowTemplates,
  templates,
  handleCreateOrg,
  handleCreateFromTemplate,
  orgImportRef,
  handleImportOrg,
  isMobile,
  setShowLeftPanel,
  orgList,
  selectedOrgId,
  setSelectedOrgId,
  doSave,
  confirmDeleteOrgId,
  setConfirmDeleteOrgId,
  handleDeleteOrg,
}: OrgListPanelProps) {
  return (
    <>
      <div className="flex items-center justify-between gap-3 overflow-x-auto px-3 pt-3 pb-2">
        <span className="truncate font-semibold text-sm" title="组织编排">组织编排</span>
        <div className="flex shrink-0 gap-1 items-center">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="outline" size="icon-xs" onClick={() => setShowTemplates(!showTemplates)}>
                <IconClipboard size={12} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>从模板创建</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="outline" size="icon-xs" onClick={handleCreateOrg}>
                <IconPlus size={12} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>新建空白组织</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="outline" size="icon-xs" onClick={() => orgImportRef.current?.click()}>
                <IconUpload size={12} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>导入组织</TooltipContent>
          </Tooltip>
          <input
            ref={orgImportRef}
            type="file"
            accept=".json,.akita-org"
            style={{ display: "none" }}
            onChange={handleImportOrg}
          />
          {isMobile && (
            <Button variant="ghost" size="icon-xs" onClick={() => setShowLeftPanel(false)}>
              <IconX size={16} />
            </Button>
          )}
        </div>
      </div>

      {showTemplates && (
        <div className="px-2 pb-2">
          <div className="rounded-lg border bg-card p-2 text-xs">
            <div className="font-semibold mb-1.5">从模板创建</div>
            {templates.map((tpl) => (
              <div
                key={tpl.id}
                onClick={() => handleCreateFromTemplate(tpl.id)}
                className="navItem"
                style={{ padding: "6px 8px", borderRadius: "var(--radius-sm)", marginBottom: 2 }}
              >
                <span><IconBuilding size={14} /></span>
                <div className="min-w-0">
                  <div className="truncate font-medium" title={tpl.name}>{tpl.name}</div>
                  <div className="truncate text-[10px] text-muted-foreground" title={`${tpl.node_count} 节点`}>{tpl.node_count} 节点</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-2">
        {orgList.length === 0 && (
          <div className="text-center text-muted-foreground text-xs p-5">
            暂无组织，点击上方创建
          </div>
        )}
        {orgList.map((org) => (
          <div
            key={org.id}
            onClick={async () => { if (selectedOrgId && selectedOrgId !== org.id) await doSave(true); setSelectedOrgId(org.id); setShowLeftPanel(false); }}
            className={`navItem ${selectedOrgId === org.id ? "navItemActive" : ""} relative justify-between`}
            style={{ padding: "8px 10px", marginBottom: 4, borderRadius: "var(--radius-sm)" }}
          >
            <div className="flex flex-1 min-w-0 items-center gap-2 overflow-hidden">
              <IconBuilding size={16} />
              <div className="min-w-0 overflow-hidden">
                <div className="font-medium text-[13px] truncate" title={org.name}>{org.name}</div>
                <div className="truncate text-[10px] text-muted-foreground" title={`${org.node_count} 节点 · ${org.status}`}>{org.node_count} 节点 · {org.status}</div>
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon-xs"
              className="ml-2 shrink-0 opacity-50 hover:opacity-100"
              onClick={(e) => { e.stopPropagation(); setConfirmDeleteOrgId(org.id); }}
            >
              <IconTrash size={10} />
            </Button>
            {confirmDeleteOrgId === org.id && (
              <div
                className="absolute right-0 top-full z-10 bg-popover border rounded-lg p-2 shadow-lg flex gap-1.5 items-center text-[11px]"
                onClick={(e) => e.stopPropagation()}
              >
                <span>确认删除?</span>
                <Button variant="destructive" size="xs" onClick={() => handleDeleteOrg(org.id)}>删除</Button>
                <Button variant="outline" size="xs" onClick={() => setConfirmDeleteOrgId(null)}>取消</Button>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
