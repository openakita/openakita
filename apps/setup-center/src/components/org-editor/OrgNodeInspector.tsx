import { useState, useEffect, type ComponentType } from "react";
import type { Node } from "@xyflow/react";
import { IconX, IconCheck, IconChevronDown } from "../../icons";
import { safeFetch } from "../../providers";
import { OrgAvatar, AVATAR_PRESETS } from "../OrgAvatars";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import { Badge } from "../ui/badge";
import { Checkbox } from "../ui/checkbox";
import { Label } from "../ui/label";
import { ToggleGroup, ToggleGroupItem } from "../ui/toggle-group";
import { Switch } from "../ui/switch";
import {
  type OrgNodeData,
  type OrgFull,
  type RightPanelMode,
  STATUS_COLORS,
  STATUS_LABELS,
  fmtTime,
  fmtDateTime,
  NodeTasksTabContent,
} from "./index";

type MdMods = {
  ReactMarkdown: ComponentType<{ children: string; remarkPlugins?: any[]; rehypePlugins?: any[] }>;
  remarkGfm: any;
  rehypeHighlight: any;
};

export interface OrgNodeInspectorProps {
  selectedNode: OrgNodeData;
  selectedNodeId: string;
  selectedOrgId: string | null;
  updateNodeData: (field: string, value: any) => void;
  autoSave: () => void;
  onClose: () => void;
  liveMode: boolean;
  currentOrg: OrgFull | null;
  apiBaseUrl: string;
  nodes: Node[];
  md: MdMods | null;
  setChatPanelNode: (v: string | null) => void;
  setRightPanel: (v: RightPanelMode) => void;
  setSelectedNodeId: (v: string | null) => void;
  nodeSchedules: any[];
  nodeEvents: any[];
  nodeThinking: any[];
  orgStats: any;
  agentProfiles: { id: string; name: string; description: string; icon: string }[];
  availableMcpServers: { name: string; status: string }[];
  availableSkills: { name: string; description?: string; name_i18n?: string; description_i18n?: string }[];
  propsTab: "overview" | "identity" | "capabilities" | "tasks";
  setPropsTab: (v: "overview" | "identity" | "capabilities" | "tasks") => void;
}

export function OrgNodeInspector({
  selectedNode,
  selectedNodeId,
  selectedOrgId,
  updateNodeData,
  autoSave,
  onClose,
  liveMode,
  currentOrg,
  apiBaseUrl,
  nodes,
  md,
  setChatPanelNode,
  setRightPanel,
  setSelectedNodeId,
  nodeSchedules,
  nodeEvents,
  nodeThinking,
  orgStats,
  agentProfiles,
  availableMcpServers,
  availableSkills,
  propsTab,
  setPropsTab,
}: OrgNodeInspectorProps) {
  const [expandedThinkingIdx, setExpandedThinkingIdx] = useState<number | null>(null);
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);
  const [agentProfileSearch, setAgentProfileSearch] = useState("");
  const [fullPromptPreview, setFullPromptPreview] = useState<string | null>(null);
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false);
  const [mcpSearch, setMcpSearch] = useState("");
  const [skillSearch, setSkillSearch] = useState("");
  const [nodeTasks, setNodeTasks] = useState<{ assigned: any[]; delegated: any[] } | null>(null);
  const [nodeActivePlan, setNodeActivePlan] = useState<any>(null);
  const [nodeTasksLoading, setNodeTasksLoading] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);

  useEffect(() => {
    setExpandedThinkingIdx(null);
    setAgentDropdownOpen(false);
    setAgentProfileSearch("");
    setFullPromptPreview(null);
    setPromptPreviewLoading(false);
    setMcpSearch("");
    setSkillSearch("");
    setAvatarError(null);
  }, [selectedNodeId, currentOrg?.id]);

  useEffect(() => {
    if (!selectedNodeId || !currentOrg || propsTab !== "tasks") {
      setNodeTasks(null);
      setNodeActivePlan(null);
      return;
    }
    setNodeTasksLoading(true);
    const fetchNodeTasks = async () => {
      try {
        const [tasksRes, planRes] = await Promise.all([
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/tasks`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/active-plan`),
        ]);
        if (tasksRes.ok) {
          const data = await tasksRes.json();
          setNodeTasks({ assigned: data.assigned || [], delegated: data.delegated || [] });
        } else {
          setNodeTasks({ assigned: [], delegated: [] });
        }
        if (planRes.ok) {
          const planData = await planRes.json();
          setNodeActivePlan(planData.task_id ? planData : null);
        } else {
          setNodeActivePlan(null);
        }
      } catch (e) {
        setNodeTasks({ assigned: [], delegated: [] });
        setNodeActivePlan(null);
      } finally {
        setNodeTasksLoading(false);
      }
    };
    fetchNodeTasks();
  }, [selectedNodeId, currentOrg, propsTab, apiBaseUrl]);

  return (
    <>
<div className="px-4 pt-4 pb-3 border-b flex justify-between items-start">
  <div>
    <div className="font-semibold text-base mb-1">{selectedNode.role_title}</div>
    <div className="text-xs text-muted-foreground">{selectedNode.department || "未分配部门"}</div>
  </div>
  <div className="flex gap-1 items-center">
    {liveMode && selectedOrgId && (
      <Button
        size="icon-sm"
        onClick={() => { setChatPanelNode(selectedNodeId); setRightPanel("command"); }}
        style={{ background: "linear-gradient(135deg, #3b82f6, #6366f1)" }}
        title="与该节点对话"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </Button>
    )}
    <Button variant="ghost" size="icon-sm" onClick={onClose}><IconX size={14} /></Button>
  </div>
</div>

<div className="border-b px-3 py-2 bg-muted/20">
  <ToggleGroup
    type="single"
    value={propsTab}
    onValueChange={(v) => { if (v) setPropsTab(v as typeof propsTab); }}
    variant="outline"
    size="sm"
    className="grid w-full grid-cols-4 rounded-xl border bg-background p-1 shadow-xs"
  >
    <ToggleGroupItem value="overview" className="h-9 rounded-lg border-0 text-xs font-medium data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:shadow-sm">概览</ToggleGroupItem>
    <ToggleGroupItem value="identity" className="h-9 rounded-lg border-0 text-xs font-medium data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:shadow-sm">身份</ToggleGroupItem>
    <ToggleGroupItem value="capabilities" className="h-9 rounded-lg border-0 text-xs font-medium data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:shadow-sm">能力</ToggleGroupItem>
    <ToggleGroupItem value="tasks" className="h-9 rounded-lg border-0 text-xs font-medium data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:shadow-sm">任务</ToggleGroupItem>
  </ToggleGroup>
</div>

<div className="px-4 py-4">
  {propsTab === "overview" && liveMode && (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Node status summary */}
      <div className="card" style={{ padding: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>节点状态</div>
        <div className="flex gap-2 flex-wrap">
          <Badge
            variant="outline"
            className="text-[11px] font-medium"
            style={{
              background: `${STATUS_COLORS[selectedNode.status] || "var(--muted)"}20`,
              color: STATUS_COLORS[selectedNode.status] || "var(--muted)",
              borderColor: `${STATUS_COLORS[selectedNode.status] || "var(--muted)"}40`,
            }}
          >
            {STATUS_LABELS[selectedNode.status] || selectedNode.status}
          </Badge>
          {selectedNode.is_clone && <Badge variant="secondary" className="text-[10px]">副本</Badge>}
          {selectedNode.ephemeral && <Badge variant="secondary" className="text-[10px] text-amber-700">临时</Badge>}
        </div>
      </div>

      {/* Schedules */}
      {nodeSchedules.length > 0 && (
        <div className="card" style={{ padding: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>定时任务</div>
          {nodeSchedules.map((s: any) => (
            <div key={s.id} style={{
              padding: "4px 0",
              borderBottom: "1px solid var(--line)",
              fontSize: 11,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontWeight: 500 }}>{s.name}</span>
                <Badge variant={s.enabled ? "default" : "secondary"} className="text-[10px] px-1.5 py-0">
                  {s.enabled ? "启用" : "禁用"}
                </Badge>
              </div>
              {s.last_run_at && (
                <div style={{ fontSize: 10, color: "#9ca3af", marginTop: 2 }}>
                  上次: {fmtDateTime(s.last_run_at)}
                </div>
              )}
              {s.last_result_summary && (
                <div style={{
                  fontSize: 10,
                  color: "#6b7280",
                  marginTop: 2,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}>
                  {s.last_result_summary}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Recent events */}
      <div className="card" style={{ padding: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
          最近活动
          {nodeEvents.length > 0 && (
            <span style={{ fontSize: 10, color: "#9ca3af", fontWeight: 400, marginLeft: 4 }}>
              ({nodeEvents.length})
            </span>
          )}
        </div>
        {nodeEvents.length === 0 ? (
          <div style={{ fontSize: 11, color: "#9ca3af" }}>暂无活动记录</div>
        ) : (
          <div style={{ maxHeight: 300, overflowY: "auto" }}>
            {nodeEvents.slice(0, 15).map((evt: any, i: number) => (
              <div key={evt.event_id || i} style={{
                padding: "4px 0",
                borderBottom: "1px solid var(--line)",
                fontSize: 11,
              }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <span style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: evt.event_type?.includes("fail") || evt.event_type?.includes("error")
                      ? "var(--danger)"
                      : evt.event_type?.includes("complete")
                      ? "var(--ok)"
                      : "var(--primary)",
                    flexShrink: 0,
                  }} />
                  <span style={{ fontWeight: 500 }}>
                    {evt.event_type?.replace(/_/g, " ")}
                  </span>
                  <span style={{ color: "#9ca3af", fontSize: 10, marginLeft: "auto" }}>
                    {fmtTime(evt.timestamp)}
                  </span>
                </div>
                {evt.data && Object.keys(evt.data).length > 0 && (
                  <div style={{ fontSize: 10, color: "#6b7280", marginTop: 2, marginLeft: 12 }}>
                    {Object.entries(evt.data).slice(0, 3).map(([k, v]) => (
                      <span key={k} style={{ marginRight: 8 }}>
                        {k}: {String(v).slice(0, 60)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Thought chain (merged timeline) */}
      <div className="card" style={{ padding: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
          思维链
          {nodeThinking.length > 0 && (
            <span style={{ fontSize: 10, color: "#9ca3af", fontWeight: 400, marginLeft: 4 }}>
              ({nodeThinking.length})
            </span>
          )}
        </div>
        {nodeThinking.length === 0 ? (
          <div style={{ fontSize: 11, color: "#9ca3af" }}>暂无思维链记录</div>
        ) : (
          <div style={{ maxHeight: 400, overflowY: "auto" }}>
            {nodeThinking.slice(0, 30).map((item: any, i: number) => {
              const isMsg = item.type === "message";
              const isEvent = item.type === "event";
              const tsLocal = fmtTime(item.timestamp);
              const isExpanded = expandedThinkingIdx === i;

              if (isMsg) {
                const isOut = item.direction === "out";
                const msgTypeColors: Record<string, string> = {
                  task_assign: "#7c3aed", task_result: "#059669",
                  question: "#2563eb", answer: "#0891b2",
                  escalation: "#dc2626", deliverable: "#d97706",
                };
                return (
                  <div key={i}
                    onClick={() => setExpandedThinkingIdx(isExpanded ? null : i)}
                    style={{
                      padding: "6px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                      cursor: "pointer", background: isExpanded ? "var(--bg-secondary)" : undefined,
                    }}
                  >
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <span style={{
                        fontSize: 10, padding: "1px 5px", borderRadius: 3,
                        background: isOut ? "#dbeafe" : "#fef3c7",
                        color: isOut ? "#1d4ed8" : "#92400e",
                        fontWeight: 500,
                      }}>
                        {isOut ? `→ ${item.peer}` : `← ${item.peer}`}
                      </span>
                      {item.msg_type && (
                        <span style={{
                          fontSize: 9, padding: "1px 4px", borderRadius: 3,
                          background: `${msgTypeColors[item.msg_type] || "#6b7280"}18`,
                          color: msgTypeColors[item.msg_type] || "#6b7280",
                        }}>
                          {item.msg_type.replace(/_/g, " ")}
                        </span>
                      )}
                      <span style={{ color: "#9ca3af", fontSize: 10, marginLeft: "auto" }}>
                        {tsLocal}
                      </span>
                    </div>
                    <div style={{
                      marginTop: 3, fontSize: 11, color: "#374151",
                      whiteSpace: "pre-wrap", wordBreak: "break-word",
                      maxHeight: isExpanded ? "none" : 60,
                      overflow: isExpanded ? "visible" : "hidden",
                      lineHeight: 1.4,
                    }}>
                      {isExpanded
                        ? (item.content || "")
                        : (item.content || "").length > 150
                          ? (item.content || "").slice(0, 150) + "…"
                          : item.content}
                    </div>
                    {!isExpanded && (item.content || "").length > 150 && (
                      <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2 }}>
                        点击展开全文
                      </div>
                    )}
                  </div>
                );
              }

              if (isEvent) {
                const evtType = item.event_type || "";
                const isToolCall = evtType.includes("tool");
                const isComplete = evtType.includes("complete");
                const isError = evtType.includes("fail") || evtType.includes("error");
                return (
                  <div key={i}
                    onClick={() => setExpandedThinkingIdx(isExpanded ? null : i)}
                    style={{
                      padding: "4px 0", borderBottom: "1px solid var(--line)", fontSize: 11,
                      cursor: "pointer", background: isExpanded ? "var(--bg-secondary)" : undefined,
                    }}
                  >
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                        background: isError ? "var(--danger)" : isComplete ? "var(--ok)"
                          : isToolCall ? "#7c3aed" : "var(--primary)",
                      }} />
                      <span style={{
                        fontWeight: 500, fontSize: 10,
                        color: isToolCall ? "#7c3aed" : undefined,
                      }}>
                        {isToolCall ? "⚙ " : ""}{evtType.replace(/_/g, " ")}
                      </span>
                      <span style={{ color: "#9ca3af", fontSize: 10, marginLeft: "auto" }}>
                        {tsLocal}
                      </span>
                    </div>
                    {item.data && Object.keys(item.data).length > 0 && (
                      <div style={{ fontSize: 10, color: "#6b7280", marginTop: 2, marginLeft: 12 }}>
                        {Object.entries(item.data).slice(0, isExpanded ? 20 : 3).map(([k, v]) => (
                          <div key={k} style={{ marginBottom: 1 }}>
                            <span style={{ fontWeight: 500 }}>{k}</span>: {isExpanded ? String(v) : String(v).slice(0, 80)}
                          </div>
                        ))}
                      </div>
                    )}
                    {!isExpanded && item.data && Object.keys(item.data).length > 3 && (
                      <div style={{ fontSize: 9, color: "var(--primary)", marginTop: 2, marginLeft: 12 }}>
                        点击查看全部 {Object.keys(item.data).length} 个字段
                      </div>
                    )}
                  </div>
                );
              }

              return null;
            })}
          </div>
        )}
      </div>

      {/* Current task detail */}
      {selectedNode.current_task && (
        <div className="card" style={{ padding: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "#b45309" }}>
            当前任务
          </div>
          <div style={{
            fontSize: 11,
            color: "#374151",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            lineHeight: 1.4,
            background: "#fffbeb",
            padding: 8,
            borderRadius: 4,
            border: "1px solid #fde68a",
          }}>
            {selectedNode.current_task}
          </div>
        </div>
      )}
    </div>
  )}

  {propsTab === "overview" && (
    <div className="flex flex-col gap-3">
      <div className="rounded-xl border bg-card p-3">
        <div className="mb-3">
          <div className="text-sm font-semibold">节点形象</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            选择一个头像，帮助在画布和对话场景中快速识别这个角色。
          </div>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          {AVATAR_PRESETS.map((av) => {
            const isSel = selectedNode.avatar === av.id;
            return (
              <OrgAvatar
                key={av.id}
                avatarId={av.id}
                size={40}
                onClick={() => updateNodeData("avatar", av.id)}
                style={{
                  cursor: "pointer",
                  border: isSel ? "2px solid var(--primary)" : "2px solid transparent",
                  boxShadow: isSel ? "0 0 0 2px rgba(59,130,246,0.18)" : "none",
                  opacity: isSel ? 1 : 0.85,
                  transition: "all 0.15s",
                }}
              />
            );
          })}
          <label
            title="上传自定义头像"
            className="flex h-10 w-10 cursor-pointer items-center justify-center rounded-full border-2 border-dashed text-lg text-muted-foreground transition-opacity hover:opacity-100"
            style={{ opacity: 0.75 }}
          >
            +
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,image/svg+xml"
              style={{ display: "none" }}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setAvatarError(null);
                if (file.size > 2 * 1024 * 1024) {
                  setAvatarError(`图片不能超过 2MB（当前 ${(file.size / 1024 / 1024).toFixed(1)}MB）`);
                  e.target.value = "";
                  return;
                }
                const form = new FormData();
                form.append("file", file);
                try {
                  const res = await safeFetch(`${apiBaseUrl}/api/orgs/avatars/upload`, {
                    method: "POST",
                    body: form,
                  });
                  if (res.ok) {
                    const data = await res.json();
                    updateNodeData("avatar", data.url);
                  } else {
                    const err = await res.text();
                    setAvatarError(`上传失败: ${err}`);
                  }
                } catch (err) {
                  setAvatarError(`上传失败: ${err}`);
                }
                e.target.value = "";
              }}
            />
          </label>
        </div>
        {avatarError && (
          <div className="mt-2 flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-[12px] font-medium text-destructive">
            <span>{avatarError}</span>
            <Button variant="ghost" size="icon-xs" className="ml-auto shrink-0" onClick={() => setAvatarError(null)}>
              <IconX size={10} />
            </Button>
          </div>
        )}
        {selectedNode.avatar && (selectedNode.avatar.startsWith("/") || selectedNode.avatar.startsWith("http")) && (
          <div className="mt-3 flex items-center gap-3 rounded-lg border bg-muted/30 p-2.5">
            <OrgAvatar avatarId={selectedNode.avatar} size={44} />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">当前为自定义头像</div>
              <div className="text-[11px] text-muted-foreground">可随时移除，恢复使用预设头像。</div>
            </div>
            <Button variant="outline" size="xs" onClick={() => updateNodeData("avatar", null)}>
              移除
            </Button>
          </div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-3 space-y-3">
        <div>
          <div className="text-sm font-semibold">基础身份</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            描述这个节点的岗位身份，以及它继承专业能力的方式。
          </div>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs font-semibold text-foreground">岗位名称</Label>
          <div className="text-[11px] text-muted-foreground">这是画布和协作消息中显示给其他节点看的身份名称。</div>
          <Input
            value={selectedNode.role_title}
            onChange={(e) => updateNodeData("role_title", e.target.value)}
            placeholder="如：技术总监、前端工程师、QA 负责人"
            className="h-10 text-sm"
          />
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs font-semibold text-foreground">Agent 来源</Label>
          <div className="text-[11px] text-muted-foreground">决定这个节点使用本地专属配置，还是复用已有 Agent 的能力配置。</div>
          <select
            data-slot="select"
            className="h-10 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs"
            value={selectedNode.agent_source.startsWith("ref:") ? "ref" : "local"}
            onChange={(e) => updateNodeData("agent_source", e.target.value === "local" ? "local" : `ref:${selectedNode.agent_profile_id || ""}`)}
          >
            <option value="local">本地专属</option>
            <option value="ref">引用已有 Agent</option>
          </select>
        </div>

        {selectedNode.agent_source.startsWith("ref:") && (
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-foreground">选择 Agent</Label>
            <div className="text-[11px] text-muted-foreground">从已有 Agent 模板中选择一个作为这个节点的能力来源。</div>
            <div style={{ position: "relative" }}>
              <div
                className="h-10 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs cursor-pointer flex items-center justify-between"
                onClick={() => { setAgentDropdownOpen(!agentDropdownOpen); setAgentProfileSearch(""); }}
                style={{ background: selectedNode.agent_profile_id ? undefined : "var(--bg-app)" }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {(() => {
                    const ap = agentProfiles.find((p) => p.id === selectedNode.agent_profile_id);
                    return ap ? `${ap.icon || "🤖"} ${ap.name}` : "点击选择...";
                  })()}
                </span>
                <IconChevronDown size={12} style={{ flexShrink: 0, opacity: 0.5 }} />
              </div>
              {agentDropdownOpen && (
                <>
                  <div style={{ position: "fixed", inset: 0, zIndex: 99 }} onClick={() => setAgentDropdownOpen(false)} />
                  <div style={{
                    position: "absolute", top: "calc(100% + 6px)", left: 0, right: 0, zIndex: 100,
                    background: "var(--card-bg, #fff)", border: "1px solid var(--line)",
                    borderRadius: 10, boxShadow: "0 8px 20px rgba(0,0,0,0.12)",
                    maxHeight: 260, display: "flex", flexDirection: "column",
                  }}>
                    <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--line)" }}>
                      <Input
                        className="h-8 text-xs"
                        value={agentProfileSearch}
                        onChange={(e) => setAgentProfileSearch(e.target.value)}
                        placeholder="搜索 Agent..."
                        autoFocus
                        onClick={(e) => e.stopPropagation()}
                      />
                    </div>
                    <div style={{ overflowY: "auto", flex: 1, padding: 4 }}>
                      {agentProfiles.length === 0 ? (
                        <div style={{ padding: 12, color: "var(--muted)", textAlign: "center", fontSize: 11 }}>
                          暂无可用 Agent，请先在 Agent 管理页创建
                        </div>
                      ) : (
                        agentProfiles
                          .filter((ap) => {
                            if (!agentProfileSearch) return true;
                            const q = agentProfileSearch.toLowerCase();
                            return ap.name.toLowerCase().includes(q) || ap.id.toLowerCase().includes(q) || (ap.description || "").toLowerCase().includes(q);
                          })
                          .map((ap) => (
                            <div
                              key={ap.id}
                              onClick={() => {
                                updateNodeData("agent_profile_id", ap.id);
                                updateNodeData("agent_source", `ref:${ap.id}`);
                                setAgentDropdownOpen(false);
                              }}
                              style={{
                                padding: "8px 10px", cursor: "pointer", fontSize: 12,
                                display: "flex", alignItems: "center", gap: 8, borderRadius: 8,
                                background: selectedNode.agent_profile_id === ap.id ? "rgba(14,165,233,0.08)" : undefined,
                              }}
                              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover, rgba(0,0,0,0.04))")}
                              onMouseLeave={(e) => (e.currentTarget.style.background = selectedNode.agent_profile_id === ap.id ? "rgba(14,165,233,0.08)" : "")}
                            >
                              <span style={{ fontSize: 16, flexShrink: 0 }}>{ap.icon || "🤖"}</span>
                              <div style={{ minWidth: 0, flex: 1 }}>
                                <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ap.name}</div>
                                {ap.description && <div style={{ fontSize: 10, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ap.description}</div>}
                              </div>
                              {selectedNode.agent_profile_id === ap.id && <IconCheck size={14} style={{ color: "var(--primary)", flexShrink: 0 }} />}
                            </div>
                          ))
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-3 space-y-3">
        <div>
          <div className="text-sm font-semibold">职责与背景</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            说明这个岗位要完成什么工作，以及它为什么有能力完成这些工作。
          </div>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs font-semibold text-foreground">岗位目标</Label>
          <div className="text-[11px] text-muted-foreground">这个节点需要为组织达成的核心目标。</div>
          <Textarea
            value={selectedNode.role_goal}
            onChange={(e) => updateNodeData("role_goal", e.target.value)}
            rows={3}
            placeholder="如：制定内容策略、审核发布内容、确保内容质量"
            className="min-h-[88px] resize-y text-sm leading-relaxed"
          />
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs font-semibold text-foreground">角色背景</Label>
          <div className="text-[11px] text-muted-foreground">补充专业经历、方法论、风格偏好等，让角色行为更稳定。</div>
          <Textarea
            value={selectedNode.role_backstory}
            onChange={(e) => updateNodeData("role_backstory", e.target.value)}
            rows={4}
            placeholder="如：资深主编，擅长内容策略梳理与团队管理"
            className="min-h-[108px] resize-y text-sm leading-relaxed"
          />
        </div>
      </div>

      <div className="rounded-xl border bg-card p-3 space-y-3">
        <div>
          <div className="text-sm font-semibold">组织位置</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            用于描述这个节点在组织结构里的归属和层级位置。
          </div>
        </div>

        <div className="grid grid-cols-[minmax(0,1fr)_88px] items-end gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-foreground">部门</Label>
            <div className="text-[11px] text-muted-foreground">例如编辑部、创作组、运营组。</div>
            <Input
              value={selectedNode.department}
              onChange={(e) => updateNodeData("department", e.target.value)}
              className="h-10 text-sm"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-foreground" title="数字越小越靠上">层级</Label>
            <Input
              type="number"
              min={0}
              className="h-10 text-sm"
              value={selectedNode.level}
              onChange={(e) => updateNodeData("level", parseInt(e.target.value) || 0)}
              title="数字越小越靠上"
            />
          </div>
        </div>
      </div>
    </div>
  )}

  {propsTab === "identity" && (
    <div className="flex flex-col gap-3">
      <div className="rounded-xl border bg-card p-3 space-y-3">
        <div>
          <div className="text-sm font-semibold">身份提示词说明</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            系统会把岗位信息、自定义提示词、组织上下文、工具清单等组合成完整的节点身份描述。
          </div>
        </div>
        <div className="rounded-lg border bg-muted/30 p-3 text-[11px] leading-6 text-muted-foreground">
          <div>1. <b>精简身份声明</b>：角色定位与协作原则</div>
          <div>2. <b>角色描述</b>：来自 ROLE.md / 自定义提示词 / 岗位信息</div>
          <div>3. <b>组织上下文</b>：核心业务、架构、上下级关系、权限、黑板</div>
          <div>4. <b>运行环境</b>：时间、OS、Shell 等自动注入信息</div>
          <div>5. <b>工具清单</b>：组织工具 + 节点启用的外部工具</div>
          <div>6. <b>行为准则</b>：协作规则、交付流程和限制条件</div>
        </div>
        <div className="text-[11px] text-muted-foreground">
          角色描述优先级：`ROLE.md` &gt; 自定义提示词 &gt; AgentProfile &gt; 自动生成
        </div>
      </div>

      <div className="rounded-xl border bg-card p-3 space-y-3">
        <div className="flex items-start justify-between gap-3 overflow-x-auto">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold" title="自定义提示词">自定义提示词</div>
            <div className="mt-1 truncate text-[11px] leading-relaxed text-muted-foreground" title="用你自己的语言精细定义这个角色，覆盖自动生成的角色描述。">
              用你自己的语言精细定义这个角色，覆盖自动生成的角色描述。
            </div>
          </div>
          <Button
            variant="outline"
            size="xs"
            className="shrink-0"
            onClick={() => {
              if (selectedNode.custom_prompt && !confirm("将覆盖当前自定义提示词，确认？")) return;
              const tpl = `你是一位经验丰富的${selectedNode.role_title || "专业人员"}。\n\n## 核心职责\n- ${selectedNode.role_goal || "待定义"}\n\n## 工作风格\n- 沟通简洁高效，结论先行\n- 重要决策写入组织黑板\n- 主动向上级汇报进展\n\n## 专业背景\n${selectedNode.role_backstory || "请在此描述角色的专业背景、经验和能力特长"}`;
              updateNodeData("custom_prompt", tpl);
            }}
          >
            填充模板
          </Button>
        </div>
        <Textarea
          value={selectedNode.custom_prompt}
          onChange={(e) => updateNodeData("custom_prompt", e.target.value)}
          rows={10}
          placeholder={"可选。不填写时系统将根据岗位名称、目标、背景自动生成角色描述。\n\n填写后将替代自动生成的内容，可更精细地控制角色行为。\n\n示例：\n你是一位资深前端工程师，擅长 React/Vue...\n\n## 核心职责\n- 负责前端架构设计和代码审查\n- 协调前端团队的开发进度"}
          className="min-h-[180px] resize-y font-mono text-xs leading-relaxed"
        />
        <div className="text-[11px] text-muted-foreground">
          {selectedNode.custom_prompt
            ? `已配置自定义提示词（${selectedNode.custom_prompt.length} 字符）`
            : `当前未配置，系统会根据岗位名称、目标和背景自动生成角色描述。`}
        </div>
      </div>

      <div className="rounded-xl border bg-card p-3 space-y-3">
        <div className="flex items-start justify-between gap-3 overflow-x-auto">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold" title="提示词预览">提示词预览</div>
            <div className="mt-1 truncate text-[11px] leading-relaxed text-muted-foreground" title="预览当前角色描述，必要时查看拼装后的完整提示词。">
              预览当前角色描述，必要时查看拼装后的完整提示词。
            </div>
          </div>
          <div className="flex shrink-0 gap-1.5">
            {fullPromptPreview !== null && (
              <Button variant="outline" size="xs" onClick={() => setFullPromptPreview(null)}>
                简略
              </Button>
            )}
            <Button
              variant="outline"
              size="xs"
              disabled={promptPreviewLoading}
              onClick={async () => {
                if (!currentOrg) return;
                setPromptPreviewLoading(true);
                try {
                  const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNode.id}/prompt-preview`);
                  if (resp.ok) {
                    const data = await resp.json();
                    setFullPromptPreview(data.full_prompt);
                  } else {
                    setFullPromptPreview("(获取失败，请先保存组织配置)");
                  }
                } catch {
                  setFullPromptPreview("(获取失败)");
                }
                setPromptPreviewLoading(false);
              }}
            >
              {promptPreviewLoading ? "..." : "完整预览"}
            </Button>
          </div>
        </div>
        <div className="max-h-[320px] overflow-y-auto rounded-lg border bg-muted/30 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-foreground">
          {fullPromptPreview !== null
            ? fullPromptPreview
            : selectedNode.custom_prompt
              ? selectedNode.custom_prompt
              : `你是${selectedNode.role_title || "(未设置岗位名称)"}。${selectedNode.role_goal ? `目标：${selectedNode.role_goal}。` : ""}${selectedNode.role_backstory ? `背景：${selectedNode.role_backstory}。` : ""}`}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {fullPromptPreview === null
            ? "以上为角色描述部分。点击「完整预览」可查看含组织架构、权限、黑板等上下文的完整提示词。"
            : `以上为组织上下文提示词（${fullPromptPreview.length} 字符）。实际运行时还会追加运行环境、工具清单、行为准则等内容。`}
        </div>
      </div>

      <div className="rounded-xl border bg-card p-3 space-y-2.5">
        <div className="text-sm font-semibold">高级：身份文件</div>
        <div className="text-[11px] leading-relaxed text-muted-foreground">
          如需更精细的角色控制，可在组织目录中创建该节点的专属身份文件：
        </div>
        <div className="rounded-lg border bg-muted/30 px-3 py-2 font-mono text-[11px]">
          nodes/{selectedNode.id}/identity/ROLE.md
        </div>
        <div className="text-[11px] leading-relaxed text-amber-700">
          组织模式下不会注入完整的 `SOUL.md / AGENT.md`，以避免与协作型组织规则冲突；`ROLE.md` 仍然会按最高优先级生效。
        </div>
      </div>
    </div>
  )}

  {propsTab === "capabilities" && (
    <div className="flex flex-col gap-2.5">
      <div className="rounded-xl border bg-card p-2.5 space-y-2.5">
        <div className="flex items-start justify-between gap-2.5 overflow-x-auto">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold" title="执行工具">执行工具</div>
            <div className="mt-1 truncate text-[11px] leading-relaxed text-muted-foreground" title="选择这个节点可以直接调用的工具能力；未选择时仅能使用组织协作相关工具。">
              选择这个节点可以直接调用的工具能力；未选择时仅能使用组织协作相关工具。
            </div>
          </div>
          <Button
            variant="outline"
            size="xs"
            className="shrink-0"
            onClick={() => {
              const title = (selectedNode.role_title || "").toLowerCase();
              let preset: string[] = ["research", "memory"];
              if (title.includes("ceo") || title.includes("执行官")) preset = ["research", "planning", "memory"];
              else if (title.includes("cto") || title.includes("技术总监")) preset = ["research", "planning", "filesystem", "memory"];
              else if (title.includes("cmo") || title.includes("市场")) preset = ["research", "planning", "memory"];
              else if (title.includes("cpo") || title.includes("产品总监")) preset = ["research", "planning", "memory"];
              else if (title.includes("工程师") || title.includes("开发") || title.includes("dev")) preset = ["filesystem", "memory"];
              else if (title.includes("运营") || title.includes("content")) preset = ["research", "filesystem", "memory"];
              else if (title.includes("设计") || title.includes("design")) preset = ["browser", "filesystem"];
              else if (title.includes("产品经理") || title.includes("pm")) preset = ["research", "planning", "memory"];
              else if (title.includes("seo")) preset = ["research", "memory"];
              else if (title.includes("devops")) preset = ["filesystem", "memory"];
              if (selectedNode.mcp_servers.length > 0) preset = Array.from(new Set([...preset, "mcp"]));
              updateNodeData("external_tools", preset);
            }}
            title="根据岗位角色自动推荐工具"
          >
            自动推荐
          </Button>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {[
            { key: "research", label: "搜索", desc: "检索资料、知识与上下文" },
            { key: "planning", label: "计划", desc: "分解任务与制定执行方案" },
            { key: "filesystem", label: "文件/命令", desc: "读取文件并执行命令" },
            { key: "memory", label: "记忆", desc: "使用长期与工作记忆" },
            { key: "browser", label: "浏览器", desc: "访问网页并执行浏览操作" },
            { key: "communication", label: "通信", desc: "发送消息与跨节点协作" },
            { key: "mcp", label: "MCP 调用", desc: "通过 MCP 服务器访问外部能力" },
          ].map((cat) => {
            const checked = (selectedNode.external_tools || []).includes(cat.key);
            return (
              <label
                key={cat.key}
                className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors ${
                  checked ? "border-primary/40 bg-primary/5" : "bg-background hover:bg-muted/40"
                }`}
              >
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => {
                    const cur = selectedNode.external_tools || [];
                    const next = checked ? cur.filter((s: string) => s !== cat.key) : [...cur, cat.key];
                    updateNodeData("external_tools", next);
                  }}
                  className="mt-0.5"
                />
                <div className="min-w-0">
                  <div className="text-sm font-medium leading-none">{cat.label}</div>
                  <div className="mt-0.5 text-[10px] leading-5 text-muted-foreground">{cat.desc}</div>
                </div>
              </label>
            );
          })}
        </div>
        {selectedNode.mcp_servers.length > 0 && !(selectedNode.external_tools || []).includes("mcp") && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-5 text-amber-700">
            已选择 MCP 服务器，但当前还未启用 `MCP 调用` 能力。
            <Button
              variant="outline"
              size="xs"
              className="ml-2 h-7 border-amber-300 bg-white text-amber-700 hover:bg-amber-100"
              onClick={() => {
                const cur = selectedNode.external_tools || [];
                if (!cur.includes("mcp")) updateNodeData("external_tools", [...cur, "mcp"]);
              }}
            >
              一键启用
            </Button>
          </div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-2.5 space-y-2.5">
        <div>
          <div className="text-sm font-semibold">MCP 服务器</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            为节点分配可调用的外部服务接口，例如工具集、知识库或集成服务。
          </div>
        </div>
        {availableMcpServers.length > 3 && (
          <Input
            className="h-[34px] text-sm"
            placeholder="搜索服务器..."
            value={mcpSearch}
            onChange={(e) => setMcpSearch(e.target.value)}
          />
        )}
        {availableMcpServers.length > 0 ? (
          <div className="max-h-[220px] space-y-1.5 overflow-y-auto pr-1">
            {availableMcpServers
              .filter((srv) => !mcpSearch || srv.name.toLowerCase().includes(mcpSearch.toLowerCase()))
              .map((srv) => {
                const checked = selectedNode.mcp_servers.includes(srv.name);
                return (
                  <label
                    key={srv.name}
                    className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors ${
                      checked ? "border-primary/40 bg-primary/5" : "bg-background hover:bg-muted/40"
                    }`}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={() => {
                        const next = checked
                          ? selectedNode.mcp_servers.filter((s: string) => s !== srv.name)
                          : [...selectedNode.mcp_servers, srv.name];
                        updateNodeData("mcp_servers", next);
                      }}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center gap-2">
                        <div className="truncate text-sm font-medium" title={srv.name}>{srv.name}</div>
                        <Badge
                          variant="outline"
                          className={
                            srv.status === "connected"
                              ? "h-5 shrink-0 border-emerald-200 bg-emerald-50 px-1.5 text-[10px] text-emerald-700"
                              : "h-5 shrink-0 px-1.5 text-[10px]"
                          }
                          title={srv.status === "connected" ? "在线" : "离线"}
                        >
                          {srv.status === "connected" ? "在线" : "离线"}
                        </Badge>
                      </div>
                      <div className="mt-0.5 truncate text-[10px] leading-5 text-muted-foreground" title={srv.status === "connected" ? "当前已连接，可直接用于节点执行。" : "当前未连接，节点运行时可能无法调用。"}>
                        {srv.status === "connected" ? "当前已连接，可直接用于节点执行。" : "当前未连接，节点运行时可能无法调用。"}
                      </div>
                    </div>
                  </label>
                );
              })}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed px-3 py-3 text-[11px] text-muted-foreground">
            暂无可用服务器。
          </div>
        )}
        {selectedNode.mcp_servers.length > 0 && (
          <div className="text-[11px] text-muted-foreground">已选 {selectedNode.mcp_servers.length} 个服务器</div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-2.5 space-y-2.5">
        <div>
          <div className="text-sm font-semibold">技能</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            为节点挂载已安装的专业技能包，补充领域能力和专用工作流。
          </div>
        </div>
        {availableSkills.length > 3 && (
          <Input
            className="h-[34px] text-sm"
            placeholder="搜索技能..."
            value={skillSearch}
            onChange={(e) => setSkillSearch(e.target.value)}
          />
        )}
        {availableSkills.length > 0 ? (
          <div className="max-h-[240px] space-y-1.5 overflow-y-auto pr-1">
            {availableSkills
              .filter((skill) => {
                if (!skillSearch) return true;
                const q = skillSearch.toLowerCase();
                const ni = skill.name_i18n;
                const di = skill.description_i18n;
                const nameStr = typeof ni === "object" && ni ? ((ni as any).zh || (ni as any).en || "") : (ni || "");
                const descStr = typeof di === "object" && di ? ((di as any).zh || (di as any).en || "") : (di || "");
                return (
                  nameStr.toLowerCase().includes(q) ||
                  skill.name.toLowerCase().includes(q) ||
                  descStr.toLowerCase().includes(q) ||
                  (skill.description || "").toLowerCase().includes(q)
                );
              })
              .map((skill) => {
                const checked = selectedNode.skills.includes(skill.name);
                const rawName = skill.name_i18n;
                const displayName =
                  typeof rawName === "object" && rawName !== null
                    ? (rawName as any).zh || (rawName as any).en || skill.name
                    : rawName || skill.name;
                const rawDesc = skill.description_i18n;
                const displayDesc =
                  typeof rawDesc === "object" && rawDesc !== null
                    ? (rawDesc as any).zh || (rawDesc as any).en || skill.description || ""
                    : rawDesc || skill.description || "";
                return (
                  <label
                    key={skill.name}
                    className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors ${
                      checked ? "border-primary/40 bg-primary/5" : "bg-background hover:bg-muted/40"
                    }`}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={() => {
                        const next = checked
                          ? selectedNode.skills.filter((s: string) => s !== skill.name)
                          : [...selectedNode.skills, skill.name];
                        updateNodeData("skills", next);
                      }}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium" title={displayName}>{displayName}</div>
                      {displayDesc && (
                        <div className="mt-0.5 truncate text-[10px] leading-5 text-muted-foreground" title={displayDesc}>{displayDesc}</div>
                      )}
                    </div>
                  </label>
                );
              })}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed px-3 py-3 text-[11px] text-muted-foreground">
            暂无可用技能。
          </div>
        )}
        {selectedNode.skills.length > 0 && (
          <div className="text-[11px] text-muted-foreground">已选 {selectedNode.skills.length} 个技能</div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-2.5 space-y-2.5">
        <div>
          <div className="text-sm font-semibold">性能限制</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            控制单个节点的并发处理能力和单次任务超时上限。
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-foreground">并行任务数</Label>
            <div className="text-[10px] leading-5 text-muted-foreground">同一时间允许处理的最大任务数量。</div>
            <Input
              type="number"
              min={1}
              className="h-9 text-sm"
              value={selectedNode.max_concurrent_tasks}
              onChange={(e) => updateNodeData("max_concurrent_tasks", parseInt(e.target.value) || 1)}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-foreground">超时（秒）</Label>
            <div className="text-[10px] leading-5 text-muted-foreground">超过该时长后，任务会被视为超时。</div>
            <Input
              type="number"
              min={30}
              className="h-9 text-sm"
              value={selectedNode.timeout_s}
              onChange={(e) => updateNodeData("timeout_s", parseInt(e.target.value) || 300)}
            />
          </div>
        </div>
      </div>

      <div className="rounded-xl border bg-card p-2.5 space-y-2.5">
        <div className="flex items-start justify-between gap-2.5 overflow-x-auto">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold" title="自动分身">自动分身</div>
            <div className="mt-1 truncate text-[11px] leading-relaxed text-muted-foreground" title="当任务堆积超过阈值时自动创建分身协同处理，空闲后会自动回收。">
              当任务堆积超过阈值时自动创建分身协同处理，空闲后会自动回收。
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2 rounded-full border bg-background px-2 py-1">
            <span className="text-[11px] text-muted-foreground" title="启用">启用</span>
            <Switch
              size="sm"
              checked={selectedNode.auto_clone_enabled || false}
              onCheckedChange={(v) => updateNodeData("auto_clone_enabled", v)}
            />
          </div>
        </div>
        {selectedNode.auto_clone_enabled && (
          <div className="grid grid-cols-2 gap-2.5">
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-foreground">触发阈值</Label>
              <div className="text-[10px] leading-5 text-muted-foreground">待处理任务达到这个数量后开始扩容。</div>
              <Input
                type="number"
                min={2}
                className="h-9 text-sm"
                value={selectedNode.auto_clone_threshold || 3}
                onChange={(e) => updateNodeData("auto_clone_threshold", parseInt(e.target.value) || 3)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-foreground">最大分身数</Label>
              <div className="text-[10px] leading-5 text-muted-foreground">限制同一节点可以扩展出的分身上限。</div>
              <Input
                type="number"
                min={1}
                max={5}
                className="h-9 text-sm"
                value={selectedNode.auto_clone_max || 3}
                onChange={(e) => updateNodeData("auto_clone_max", parseInt(e.target.value) || 3)}
              />
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border bg-card p-2.5 space-y-2.5">
        <div>
          <div className="text-sm font-semibold">权限控制</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            定义节点在组织中的协作边界，例如是否允许委派、上报或请求扩编。
          </div>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {([
            { key: "can_delegate", label: "委派任务", desc: "允许将任务分配给下游节点。" },
            { key: "can_escalate", label: "上报问题", desc: "允许向上游节点反馈阻塞与风险。" },
            { key: "can_request_scaling", label: "申请扩编", desc: "允许在任务压力过大时申请更多资源。" },
            { key: "ephemeral", label: "临时节点", desc: "标记为可回收的临时角色，不长期保留。" },
          ] as const).map(({ key, label, desc }) => {
            const checked = !!selectedNode[key];
            return (
              <label
                key={key}
                className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors ${
                  checked ? "border-primary/40 bg-primary/5" : "bg-background hover:bg-muted/40"
                }`}
              >
                <Checkbox
                  checked={checked}
                  onCheckedChange={(v) => updateNodeData(key, !!v)}
                  className="mt-0.5"
                />
                <div className="min-w-0">
                  <div className="text-sm font-medium">{label}</div>
                  <div className="mt-0.5 text-[10px] leading-5 text-muted-foreground">{desc}</div>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      <div className="rounded-xl border bg-card p-2.5 space-y-1.5">
        <div className="text-sm font-semibold">LLM 端点偏好</div>
        <div className="text-[11px] leading-relaxed text-muted-foreground">
          可为该节点指定优先使用的模型端点；留空时跟随组织默认配置。
        </div>
        <Input
          className="h-9 text-sm"
          value={selectedNode.preferred_endpoint || ""}
          onChange={(e) => updateNodeData("preferred_endpoint", e.target.value || null)}
          placeholder="留空使用默认端点"
        />
      </div>
    </div>
  )}

  {propsTab === "tasks" && selectedNodeId && currentOrg && (
    <NodeTasksTabContent
      nodeTasks={nodeTasks}
      nodeActivePlan={nodeActivePlan}
      loading={nodeTasksLoading}
      nodes={nodes}
      apiBaseUrl={apiBaseUrl}
      orgId={currentOrg.id}
      fmtDateTime={fmtDateTime}
    />
  )}
</div>
    </>
  );
}
