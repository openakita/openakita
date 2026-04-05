import { useState, type ComponentType } from "react";
import { IconX } from "../../icons";
import { safeFetch } from "../../providers";
import type { OrgFull } from "./types";
import { fmtShortDate } from "./helpers";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import { Badge } from "../ui/badge";
import { ToggleGroup, ToggleGroupItem } from "../ui/toggle-group";
import { Label } from "../ui/label";

type MdMods = {
  ReactMarkdown: ComponentType<{ children: string; remarkPlugins?: any[]; rehypePlugins?: any[] }>;
  remarkGfm: any;
  rehypeHighlight: any;
};

export interface OrgSettingsPanelProps {
  currentOrg: OrgFull;
  setCurrentOrg: (org: OrgFull) => void;
  autoSave: () => void;
  onClose: () => void;
  liveMode: boolean;
  apiBaseUrl: string;
  md: MdMods | null;
  handleExportOrg: () => void;
  handleImportOrg: (e: React.ChangeEvent<HTMLInputElement>) => void;
  bbEntries: any[];
  setBbEntries: React.Dispatch<React.SetStateAction<any[]>>;
  bbScope: "all" | "org" | "department" | "node";
  setBbScope: (v: "all" | "org" | "department" | "node") => void;
  bbLoading: boolean;
  fetchBlackboard: (orgId: string, scope: string) => void;
  confirmReset: boolean;
  setConfirmReset: (v: boolean) => void;
}

export function OrgSettingsPanel({
  currentOrg,
  setCurrentOrg,
  autoSave,
  onClose,
  liveMode,
  apiBaseUrl,
  md,
  handleExportOrg,
  handleImportOrg,
  bbEntries,
  setBbEntries,
  bbScope,
  setBbScope,
  bbLoading,
  fetchBlackboard,
  confirmReset,
  setConfirmReset,
}: OrgSettingsPanelProps) {
  const [personaCollapsed, setPersonaCollapsed] = useState(false);
  const [bizCollapsed, setBizCollapsed] = useState(false);
  const opMode = (currentOrg as any).operation_mode || "command";

  return (
    <div className="p-3 space-y-2.5">
      <div className="flex justify-between items-center">
        <div className="font-semibold text-[13px]">组织设置</div>
        <Button variant="ghost" size="icon-xs" onClick={() => { autoSave(); onClose(); }}>
          <IconX size={12} />
        </Button>
      </div>

      {/* ── 运行模式 ── */}
      <div className="rounded-lg border bg-card p-2.5">
        <div className="font-semibold text-xs mb-1.5">运行模式</div>
        <ToggleGroup
          type="single"
          value={opMode}
          onValueChange={(v) => { if (v) setCurrentOrg({ ...currentOrg, operation_mode: v } as any); }}
          variant="outline"
          size="sm"
          className="w-full"
        >
          <ToggleGroupItem value="command" className="flex-1 text-[11px]">命令模式</ToggleGroupItem>
          <ToggleGroupItem value="autonomous" className="flex-1 text-[11px]">自主模式</ToggleGroupItem>
        </ToggleGroup>
        <div className="text-[10px] text-muted-foreground mt-1 leading-relaxed">
          {opMode === "command"
            ? "通过聊天或命令面板下达任务，按需执行"
            : "组织根据核心业务自动运转，顶层负责人持续运营"}
        </div>
      </div>

      {/* ── 核心业务 (仅自主模式) ── */}
      {opMode === "autonomous" && (
        <div className="rounded-lg border bg-card p-2.5">
          <div
            className="flex justify-between items-center cursor-pointer"
            onClick={() => setBizCollapsed(!bizCollapsed)}
          >
            <div className="font-semibold text-xs">
              核心业务
              {bizCollapsed && (currentOrg.core_business || "").trim() && (
                <Badge variant="secondary" className="ml-1.5 text-[10px] px-1.5 py-0">已配置</Badge>
              )}
            </div>
            <span className="text-[10px] text-muted-foreground">{bizCollapsed ? "▸" : "▾"}</span>
          </div>
          {!bizCollapsed && (
            <div className="mt-1.5">
              <div className="text-[10px] text-muted-foreground mb-1.5 leading-relaxed">
                填写后组织启动即自主运转——顶层负责人自动接收任务书并开始工作，心跳变为定期复盘。
              </div>
              <div className="flex flex-wrap gap-1 mb-2">
                {[
                  { label: "创业公司", tpl: "## 业务定位\n我们是一家___公司，核心产品/服务是___。\n\n## 当前阶段目标\n- 完成产品 MVP 并上线\n- 获取首批 100 个种子用户\n- 验证产品-市场匹配度\n\n## 工作策略\n- 产品优先：先打磨核心功能，再扩展\n- 精益运营：小规模验证后再投入推广资源\n- 数据驱动：关注用户留存率和活跃度\n\n## 主动运营要求\n负责人需持续推进：产品开发进度跟踪、市场调研执行、用户反馈收集与分析、团队任务协调。每个复盘周期应有可交付成果。" },
                  { label: "内容运营", tpl: "## 业务定位\n面向___领域的内容创作与分发平台/账号。\n\n## 当前阶段目标\n- 建立稳定的内容生产流程（每周___篇）\n- 核心平台粉丝/订阅达到___\n- 形成可复制的爆款内容方法论\n\n## 工作策略\n- 选题驱动：每周策划会确定选题方向\n- 数据复盘：分析每篇内容的阅读/互动数据\n- 持续迭代：根据数据调整内容策略\n\n## 主动运营要求\n负责人需持续推进：选题策划与分配、内容质量把控、发布排期管理、数据复盘与策略调整。确保内容产出不中断。" },
                  { label: "软件项目", tpl: "## 项目定位\n为___开发的___系统/应用。\n\n## 当前阶段目标\n- 完成___模块的开发与测试\n- 交付可演示的版本给___\n- 技术文档同步更新\n\n## 工作策略\n- 迭代开发：按优先级排列功能，每轮迭代2周\n- 质量保障：代码审查 + 自动化测试覆盖\n- 文档先行：关键架构决策必须文档化\n\n## 主动运营要求\n负责人需持续推进：任务拆解与分配、代码审查、进度跟踪、阻塞问题排除、与需求方沟通确认。" },
                  { label: "研究课题", tpl: "## 课题方向\n研究___领域的___问题。\n\n## 当前阶段目标\n- 完成文献调研，形成研究综述\n- 确定研究方案和实验设计\n- 产出阶段性研究报告\n\n## 工作策略\n- 文献先行：系统梳理相关领域进展\n- 实验验证：设计对照实验验证假设\n- 定期交流：团队内部周会分享进展\n\n## 主动运营要求\n负责人需持续推进：文献调研分配、研究方案讨论、实验进度追踪、成果整理与汇报。" },
                  { label: "电商运营", tpl: "## 业务定位\n面向___的___品类电商。\n\n## 当前阶段目标\n- 完成店铺搭建和首批___个 SKU 上架\n- 月销售额达到___\n- 建立稳定的供应链和客服流程\n\n## 工作策略\n- 选品驱动：通过市场分析确定主推品类\n- 流量获取：___平台引流 + 内容营销\n- 复购优先：客户满意度和复购率是核心指标\n\n## 主动运营要求\n负责人需持续推进：选品调研、供应链管理、营销活动策划执行、客户反馈处理、数据分析与策略调整。确保日常运营不中断。" },
                ].map((tpl) => (
                  <Button
                    key={tpl.label}
                    variant="outline"
                    size="xs"
                    className="text-[10px] h-5 px-1.5"
                    onClick={() => {
                      if ((currentOrg.core_business || "").trim() && !confirm("将覆盖当前内容，确认？")) return;
                      setCurrentOrg({ ...currentOrg, core_business: tpl.tpl });
                    }}
                  >
                    {tpl.label}
                  </Button>
                ))}
              </div>
              <Textarea
                className="text-[11px] min-h-[120px] leading-relaxed"
                placeholder={"填写或选择模板后编辑。\n\n组织启动后，顶层节点将根据此内容自动制定策略、分配任务、持续推进。"}
                value={currentOrg.core_business || ""}
                onChange={(e) => setCurrentOrg({ ...currentOrg, core_business: e.target.value })}
              />
              {(currentOrg.core_business || "").trim() && (
                <div className="text-[9px] text-green-600 mt-1">
                  启动组织后，顶层负责人将自动接收任务书并开始自主运营
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── 用户身份 ── */}
      <div className="rounded-lg border bg-card p-2.5">
        <div
          className="flex justify-between items-center cursor-pointer"
          onClick={() => setPersonaCollapsed(!personaCollapsed)}
        >
          <div className="font-semibold text-xs">
            用户身份
            {currentOrg.user_persona?.title && (
              <span className="font-normal text-[10px] text-muted-foreground ml-1.5">
                {currentOrg.user_persona.display_name || currentOrg.user_persona.title}
              </span>
            )}
          </div>
          <span className="text-[10px] text-muted-foreground">{personaCollapsed ? "▸" : "▾"}</span>
        </div>
        {!personaCollapsed && (
          <div className="mt-1.5">
            <div className="text-[10px] text-muted-foreground mb-1.5 leading-relaxed">
              你在本组织中的角色。节点会以此身份认知你。
            </div>
            <div className="flex flex-wrap gap-1 mb-2">
              {[
                { title: "董事长", desc: "组织最高决策者" },
                { title: "产品负责人", desc: "项目需求方与最终验收人" },
                { title: "出品人", desc: "内容方向决策者" },
                { title: "投资人", desc: "外部投资方" },
                { title: "甲方", desc: "项目委托方" },
                { title: "课题负责人", desc: "研究课题主持人" },
              ].map((preset) => (
                <Button
                  key={preset.title}
                  variant={currentOrg.user_persona?.title === preset.title ? "default" : "outline"}
                  size="xs"
                  className="text-[10px] h-5 px-1.5"
                  onClick={() => setCurrentOrg({
                    ...currentOrg,
                    user_persona: { title: preset.title, display_name: preset.title, description: preset.desc },
                  })}
                >
                  {preset.title}
                </Button>
              ))}
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="flex gap-1.5">
                <div className="flex-1">
                  <Label className="text-[9px] text-muted-foreground mb-0.5">头衔</Label>
                  <Input
                    className="h-7 text-[11px]"
                    placeholder="董事长"
                    value={currentOrg.user_persona?.title || ""}
                    onChange={(e) => setCurrentOrg({
                      ...currentOrg,
                      user_persona: { ...currentOrg.user_persona, title: e.target.value, display_name: currentOrg.user_persona?.display_name || "", description: currentOrg.user_persona?.description || "" },
                    })}
                  />
                </div>
                <div className="flex-1">
                  <Label className="text-[9px] text-muted-foreground mb-0.5">显示名</Label>
                  <Input
                    className="h-7 text-[11px]"
                    placeholder="留空用头衔"
                    value={currentOrg.user_persona?.display_name || ""}
                    onChange={(e) => setCurrentOrg({
                      ...currentOrg,
                      user_persona: { ...currentOrg.user_persona, title: currentOrg.user_persona?.title || "负责人", display_name: e.target.value, description: currentOrg.user_persona?.description || "" },
                    })}
                  />
                </div>
              </div>
              <div>
                <Label className="text-[9px] text-muted-foreground mb-0.5">简介</Label>
                <Input
                  className="h-7 text-[11px]"
                  placeholder="例如：组织最高决策者"
                  value={currentOrg.user_persona?.description || ""}
                  onChange={(e) => setCurrentOrg({
                    ...currentOrg,
                    user_persona: { ...currentOrg.user_persona, title: currentOrg.user_persona?.title || "负责人", display_name: currentOrg.user_persona?.display_name || "", description: e.target.value },
                  })}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Quick actions ── */}
      <div className="rounded-lg border bg-card p-2.5">
        <div className="font-semibold text-xs mb-1.5">操作</div>
        <div className="flex flex-wrap gap-1">
          <Button variant="outline" size="xs" className="text-[10px]" onClick={() => setConfirmReset(true)}>重置组织</Button>
          <Button variant="outline" size="xs" className="text-[10px]" onClick={handleExportOrg}>导出配置</Button>
          <Button variant="outline" size="xs" className="text-[10px]" asChild>
            <label className="cursor-pointer">
              导入配置
              <input type="file" accept=".json" style={{ display: "none" }} onChange={handleImportOrg} />
            </label>
          </Button>
          {liveMode && (<>
            <Button variant="outline" size="xs" className="text-[10px]" onClick={async () => {
              try { await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/heartbeat/trigger`, { method: "POST" }); } catch {}
            }}>触发心跳</Button>
            <Button variant="outline" size="xs" className="text-[10px]" onClick={async () => {
              try { await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/standup/trigger`, { method: "POST" }); } catch {}
            }}>触发晨会</Button>
          </>)}
        </div>
      </div>

      {/* ── Blackboard ── */}
      <div>
        <div className="flex justify-between items-center mb-1.5">
          <div className="font-semibold text-[13px]">组织黑板</div>
          <Button
            variant="outline"
            size="xs"
            className="text-[10px]"
            onClick={() => fetchBlackboard(currentOrg.id, bbScope)}
            disabled={bbLoading}
          >
            {bbLoading ? "..." : "刷新"}
          </Button>
        </div>

        <ToggleGroup
          type="single"
          value={bbScope}
          onValueChange={(v) => { if (v) setBbScope(v as typeof bbScope); }}
          variant="outline"
          size="sm"
          className="mb-2"
        >
          <ToggleGroupItem value="all" className="text-[10px] h-6 px-2">全部</ToggleGroupItem>
          <ToggleGroupItem value="org" className="text-[10px] h-6 px-2">组织级</ToggleGroupItem>
          <ToggleGroupItem value="department" className="text-[10px] h-6 px-2">部门级</ToggleGroupItem>
          <ToggleGroupItem value="node" className="text-[10px] h-6 px-2">节点级</ToggleGroupItem>
        </ToggleGroup>

        {bbEntries.length === 0 ? (
          <div className="text-[11px] text-muted-foreground py-4 px-2.5 text-center border border-dashed rounded-lg">
            {bbLoading ? "加载中..." : "暂无记录"}
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {bbEntries.map((entry: any) => {
              const scopeLabel = entry.scope === "org" ? "组织" : entry.scope === "department" ? entry.scope_owner : entry.source_node || "节点";
              const typeColors: Record<string, string> = {
                fact: "#3b82f6", decision: "#f59e0b", lesson: "#10b981",
                progress: "#8b5cf6", todo: "#ef4444",
              };
              const typeLabels: Record<string, string> = {
                fact: "事实", decision: "决策", lesson: "经验",
                progress: "进展", todo: "待办",
              };
              return (
                <div key={entry.id} className="rounded-md border p-1.5 bg-card text-[11px]">
                  <div className="flex justify-between items-center mb-0.5">
                    <div className="flex gap-1 items-center">
                      <Badge
                        variant="outline"
                        className="text-[9px] px-1.5 py-0 font-semibold"
                        style={{
                          background: (typeColors[entry.memory_type] || "#6b7280") + "18",
                          color: typeColors[entry.memory_type] || "#6b7280",
                          borderColor: (typeColors[entry.memory_type] || "#6b7280") + "40",
                        }}
                      >
                        {typeLabels[entry.memory_type] || entry.memory_type}
                      </Badge>
                      <span className="text-[9px] text-muted-foreground">{scopeLabel}</span>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="h-4 w-4 text-muted-foreground"
                      title="删除此条"
                      onClick={async () => {
                        try {
                          await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/memory/${entry.id}`, { method: "DELETE" });
                          setBbEntries((prev) => prev.filter((e: any) => e.id !== entry.id));
                        } catch { /* ignore */ }
                      }}
                    >
                      ×
                    </Button>
                  </div>
                  <div className="bb-entry-content leading-relaxed break-words">
                    {md ? (
                      <md.ReactMarkdown remarkPlugins={[md.remarkGfm]} rehypePlugins={[md.rehypeHighlight]}>
                        {entry.content}
                      </md.ReactMarkdown>
                    ) : (
                      <pre className="whitespace-pre-wrap m-0 font-[inherit]">{entry.content}</pre>
                    )}
                  </div>
                  {Array.isArray(entry.tags) && entry.tags.length > 0 && (
                    <div className="mt-0.5 flex gap-1 flex-wrap">
                      {entry.tags.map((t: string) => (
                        <Badge key={t} variant="secondary" className="text-[9px] px-1 py-0">#{t}</Badge>
                      ))}
                    </div>
                  )}
                  <div className="text-[9px] text-muted-foreground mt-0.5">
                    {entry.source_node && <span>来自 {entry.source_node} · </span>}
                    {fmtShortDate(entry.created_at)}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
