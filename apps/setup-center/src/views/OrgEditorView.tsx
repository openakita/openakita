import {
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  useLayoutEffect,
  type ComponentType,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  ReactFlow,
  Background,
  Panel,
  useNodesState,
  useEdgesState,
  addEdge,
  type Node,
  type Edge,
  type Connection,
  type ReactFlowInstance,
  MarkerType,
  type OnConnect,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  IconPlus,
  IconTrash,
  IconCheck,
  IconX,
  IconUsers,
  IconChevronDown,
  IconSitemap,
  IconAlertCircle,
} from "../icons";
import { safeFetch } from "../providers";
import { IS_CAPACITOR, saveFileDialog, IS_TAURI, writeTextFile } from "../platform";
import { OrgInboxSidebar } from "../components/OrgInboxSidebar";
import { PanelShell } from "../components/PanelShell";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../components/ui/dialog";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { OrgAvatar, AVATAR_PRESETS, AVATAR_MAP } from "../components/OrgAvatars";
import { OrgChatPanel } from "../components/OrgChatPanel";
import { OrgDashboard } from "../components/OrgDashboard";
import { OrgProjectBoard } from "../components/OrgProjectBoard";
import {
  type OrgNodeData,
  type OrgEdgeData,
  type OrgSummary,
  type OrgFull,
  type TemplateSummary,
  type RightPanelMode,
  type ActivityEvent,
  EDGE_COLORS,
  STATUS_COLORS,
  STATUS_LABELS,
  fmtTime,
  fmtDateTime,
  fmtShortDate,
  orgNodeToFlowNode,
  orgEdgeToFlowEdge,
  computeTreeLayout,
  getNextNodePosition,
  detectOverlap,
  nodeTypes,
  OrgCanvasControls,
  CollapsibleMiniMap,
  NodeTasksTabContent,
  OrgEdgeInspector,
  OrgNodeInspector,
  OrgSettingsPanel,
  OrgEditorTopBar,
  OrgListPanel,
} from "../components/org-editor";



// ── Lazy markdown rendering (mirrors OrgChatPanel) ──

type MdMods = {
  ReactMarkdown: ComponentType<{ children: string; remarkPlugins?: any[]; rehypePlugins?: any[] }>;
  remarkGfm: any;
  rehypeHighlight: any;
};
let _md: MdMods | null = null;
let _mdTried = false;
function useMd(): MdMods | null {
  const [m, setM] = useState<MdMods | null>(() => _md);
  useEffect(() => {
    if (_md) { setM(_md); return; }
    if (_mdTried) return;
    _mdTried = true;
    try { new RegExp("\\p{ID_Start}", "u"); new RegExp("(?<=a)b"); } catch { return; }
    Promise.all([
      import("react-markdown"),
      import("remark-gfm"),
      import("rehype-highlight"),
    ]).then(([md, gfm, hl]) => {
      _md = { ReactMarkdown: md.default, remarkGfm: gfm.default, rehypeHighlight: hl.default };
      setM(_md);
    }).catch(() => {});
  }, []);
  return m;
}

// ── Main Component ──

export function OrgEditorView({
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
}) {
  useTranslation();
  const md = useMd();

  // State
  const [orgList, setOrgList] = useState<OrgSummary[]>([]);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [currentOrg, setCurrentOrg] = useState<OrgFull | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const lastSavedRef = useRef<string>("");
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const doSaveRef = useRef<(quiet?: boolean) => Promise<boolean>>(async () => false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [showNewNodeForm, setShowNewNodeForm] = useState(false);
  const [propsTab, setPropsTab] = useState<"overview" | "identity" | "capabilities" | "tasks">("overview");
  const liveMode = currentOrg?.status === "active" || currentOrg?.status === "running";
  const [layoutLocked, setLayoutLocked] = useState(false);
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, string>>({});
  const [rightPanel, setRightPanel] = useState<RightPanelMode>("none");
  const [nodeEvents, setNodeEvents] = useState<any[]>([]);
  const [nodeSchedules, setNodeSchedules] = useState<any[]>([]);
  const [nodeMessages, setNodeMessages] = useState<any[]>([]);
  const [nodeThinking, setNodeThinking] = useState<any[]>([]);
  const [orgStats, setOrgStats] = useState<any>(null);
  const [toast, setToast] = useState<{ message: string; type: "ok" | "error" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  type AgentProfileEntry = { id: string; name: string; description: string; icon: string };
  const [agentProfiles, setAgentProfiles] = useState<AgentProfileEntry[]>([]);

  // Activity feed state
  const [activityFeed, setActivityFeed] = useState<ActivityEvent[]>([]);
  const [viewMode, setViewMode] = useState<"canvas" | "projects" | "dashboard">("canvas");
  const [chatPanelNode, setChatPanelNode] = useState<string | null>(null);
  const reactFlowRef = useRef<ReactFlowInstance<Node, Edge> | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    type: "node" | "edge" | "pane";
    id: string | null;
    flowX?: number;
    flowY?: number;
  } | null>(null);
  const [clipboardNode, setClipboardNode] = useState<any>(null);
  useEffect(() => {
    if (!contextMenu) return;
    const dismiss = () => setContextMenu(null);
    window.addEventListener("click", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => { window.removeEventListener("click", dismiss); window.removeEventListener("scroll", dismiss, true); };
  }, [contextMenu]);
  const [edgeAnimations, setEdgeAnimations] = useState<Record<string, { color: string; ts: number }>>({});
  const [edgeFlowCounts, setEdgeFlowCounts] = useState<Record<string, number>>({});

  // React Flow state
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([] as Edge[]);

  const showToast = useCallback((message: string, type: "ok" | "error" = "ok") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ message, type });
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  // MCP/Skill lists for selection
  const [availableMcpServers, setAvailableMcpServers] = useState<{ name: string; status: string }[]>([]);
  const [availableSkills, setAvailableSkills] = useState<{ name: string; description?: string; name_i18n?: string; description_i18n?: string }[]>([]);

  // Blackboard state
  const [bbEntries, setBbEntries] = useState<any[]>([]);
  const [bbScope, setBbScope] = useState<"all" | "org" | "department" | "node">("all");

  // Org settings panel collapse
  const [bbLoading, setBbLoading] = useState(false);

  // New node form
  const [newNodeTitle, setNewNodeTitle] = useState("");
  const [newNodeDept, setNewNodeDept] = useState("");
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768 || IS_CAPACITOR);
  const [showLeftPanel, setShowLeftPanel] = useState(() => !(window.innerWidth < 768 || IS_CAPACITOR));
  // rightPanel state (declared above) replaces showRightPanel / inboxOpen / chatPanelOpen
  const wasRunningRef = useRef(false);

  useLayoutEffect(() => {
    let prev = window.innerWidth < 768 || IS_CAPACITOR;
    const onResize = () => {
      const mobile = window.innerWidth < 768 || IS_CAPACITOR;
      setIsMobile(mobile);
      if (mobile && !prev) setShowLeftPanel(false);
      if (!mobile && prev) setShowLeftPanel(true);
      prev = mobile;
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!currentOrg) {
      wasRunningRef.current = false;
      return;
    }
    const running = currentOrg.status === "active" || currentOrg.status === "running";
    if (running !== wasRunningRef.current) {
      setLayoutLocked(running);
      wasRunningRef.current = running;
    }
  }, [currentOrg?.id, currentOrg?.status]);

  // ── Data fetching ──

  const fetchOrgList = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs`);
      const data = await res.json();
      setOrgList(data);
    } catch (e) {
      console.error("Failed to fetch orgs:", e);
    }
  }, [apiBaseUrl]);

  const fetchTemplates = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/templates`);
      const data = await res.json();
      setTemplates(data);
    } catch (e) {
      console.error("Failed to fetch templates:", e);
    }
  }, [apiBaseUrl]);

  const fetchOrg = useCallback(async (orgId: string) => {
    setLoading(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}`);
      const data: OrgFull = await res.json();
      setCurrentOrg(data);
      lastSavedRef.current = "";
      const flowNodes = data.nodes.map(orgNodeToFlowNode);
      const flowEdges = data.edges.map(orgEdgeToFlowEdge);
      const hasOverlap = detectOverlap(flowNodes);
      setNodes(hasOverlap ? computeTreeLayout(flowNodes, flowEdges) : flowNodes);
      setEdges(flowEdges);
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      setRightPanel("none");
      const running = data.status === "active" || data.status === "running";
      setLayoutLocked(running);
    } catch (e) {
      console.error("Failed to fetch org:", e);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, setNodes, setEdges]);

  const fetchMcpServers = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/servers`);
      const data = await res.json();
      setAvailableMcpServers(data.servers || []);
    } catch { /* MCP endpoint may not be available */ }
  }, [apiBaseUrl]);

  const fetchAvailableSkills = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/skills`);
      const data = await res.json();
      setAvailableSkills(data.skills || []);
    } catch { /* skills endpoint may not be available */ }
  }, [apiBaseUrl]);

  const fetchBlackboard = useCallback(async (orgId: string, scope?: string) => {
    setBbLoading(true);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (scope && scope !== "all") params.set("scope", scope);
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/memory?${params}`);
      const data = await res.json();
      setBbEntries(data || []);
    } catch {
      setBbEntries([]);
    } finally {
      setBbLoading(false);
    }
  }, [apiBaseUrl]);

  const fetchAgentProfiles = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/profiles`);
      const data = await res.json();
      setAgentProfiles(data.profiles || []);
    } catch { /* ignore */ }
  }, [apiBaseUrl]);

  useEffect(() => {
    if (visible) {
      fetchOrgList().then(() => {
        if (!selectedOrgId) {
          const params = new URLSearchParams(window.location.search);
          const urlOrg = params.get("org");
          if (urlOrg) setSelectedOrgId(urlOrg);
        }
      });
      fetchTemplates();
      fetchMcpServers();
      fetchAvailableSkills();
      fetchAgentProfiles();
    }
  }, [visible, fetchOrgList, fetchTemplates, fetchMcpServers, fetchAvailableSkills, fetchAgentProfiles]);

  useEffect(() => {
    if (selectedOrgId) {
      fetchOrg(selectedOrgId);
    }
  }, [selectedOrgId, fetchOrg]);

  useEffect(() => {
    if (currentOrg && !selectedNodeId) {
      fetchBlackboard(currentOrg.id, bbScope);
    }
  }, [currentOrg?.id, selectedNodeId, bbScope, fetchBlackboard]);

  // ── Load historical events on org switch ──
  const loadHistoricalEvents = useCallback(async (orgId: string) => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/events?limit=50`);
      const events: any[] = await res.json();
      if (!Array.isArray(events) || events.length === 0) return;
      const evtTypeMap: Record<string, string> = {
        node_activated: "org:node_status",
        node_status_change: "org:node_status",
        task_completed: "org:task_complete",
        task_assigned: "org:task_delegated",
        task_timeout: "org:task_timeout",
        task_failed: "org:node_status",
        broadcast: "org:broadcast",
        blackboard_write: "org:blackboard_update",
        meeting_completed: "org:meeting_completed",
        meeting_started: "org:meeting_started",
        conflict_detected: "org:deadlock",
        heartbeat_decision: "org:heartbeat_done",
        tools_granted: "org:node_status",
        watchdog_recovery: "org:watchdog_recovery",
      };
      const mapped: ActivityEvent[] = events
        .filter(e => e.event_type && evtTypeMap[e.event_type])
        .map(e => {
          const evName = evtTypeMap[e.event_type] || `org:${e.event_type}`;
          const data = { ...(e.data || {}), org_id: orgId, node_id: e.actor };
          if (e.event_type === "node_activated") data.status = "busy";
          if (e.event_type === "task_completed") data.status = "idle";
          if (e.event_type === "task_failed") data.status = "error";
          return {
            id: `hist_${e.timestamp || ""}${Math.random().toString(36).slice(2, 6)}`,
            time: e.timestamp ? new Date(e.timestamp).getTime() : Date.now(),
            event: evName,
            data,
          };
        })
        .sort((a, b) => b.time - a.time)
        .slice(0, 50);
      if (mapped.length > 0) {
        setActivityFeed(mapped);
      }
    } catch { /* ignore */ }
  }, [apiBaseUrl]);

  useEffect(() => {
    if (currentOrg && liveMode) {
      loadHistoricalEvents(currentOrg.id);
    }
  }, [currentOrg?.id, liveMode, loadHistoricalEvents]);

  // ── WebSocket for live mode ──

  const pushActivity = useCallback((event: string, data: any) => {
    const entry: ActivityEvent = { id: `${Date.now()}_${Math.random().toString(36).slice(2, 6)}`, time: Date.now(), event, data };
    setActivityFeed((prev) => [entry, ...prev].slice(0, 200));
  }, []);

  const triggerEdgeAnimation = useCallback((fromNode: string, toNode: string, color: string) => {
    const edgeKey = edges.find(
      (e) => (e.source === fromNode && e.target === toNode) || (e.source === toNode && e.target === fromNode),
    )?.id;
    if (!edgeKey) return;
    setEdgeAnimations((prev) => ({ ...prev, [edgeKey]: { color, ts: Date.now() } }));
    setEdgeFlowCounts((prev) => ({ ...prev, [edgeKey]: (prev[edgeKey] || 0) + 1 }));
    setTimeout(() => {
      setEdgeAnimations((prev) => {
        const copy = { ...prev };
        if (copy[edgeKey]?.ts && Date.now() - copy[edgeKey].ts >= 4500) delete copy[edgeKey];
        return copy;
      });
    }, 5000);
  }, [edges]);

  useEffect(() => {
    if (!liveMode || !currentOrg) return;
    const wsUrl = apiBaseUrl.replace(/^http/, "ws") + "/ws";
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (evt) => {
        try {
          const parsed = JSON.parse(evt.data);
          const ev = parsed.event as string;
          const d = parsed.data;
          if (!d || d.org_id !== currentOrg.id) return;

          if (currentOrg.status !== "active" && currentOrg.status !== "running") {
            setCurrentOrg((prev) => prev ? { ...prev, status: "active" } : prev);
          }

          if (ev === "org:node_status") {
            const { node_id, status, current_task } = d;
            setNodeStatuses((prev) => ({ ...prev, [node_id]: status }));
            setNodes((prev) =>
              prev.map((n) =>
                n.id === node_id
                  ? { ...n, data: { ...n.data, status, current_task: current_task || n.data.current_task } }
                  : n,
              ),
            );
            if (status === "busy" || status === "error") pushActivity(ev, d);
          } else if (ev === "org:task_timeout") {
            pushActivity(ev, d);
          } else if (ev === "org:task_delegated") {
            pushActivity(ev, d);
            triggerEdgeAnimation(d.from_node, d.to_node, "var(--primary)");
          } else if (ev === "org:task_delivered") {
            pushActivity(ev, d);
            triggerEdgeAnimation(d.from_node, d.to_node, "var(--ok)");
          } else if (ev === "org:task_accepted") {
            pushActivity(ev, d);
            triggerEdgeAnimation(d.accepted_by, d.from_node, "#22c55e");
          } else if (ev === "org:task_rejected") {
            pushActivity(ev, d);
            triggerEdgeAnimation(d.rejected_by, d.from_node, "var(--danger)");
          } else if (ev === "org:escalation") {
            pushActivity(ev, d);
            triggerEdgeAnimation(d.from_node, d.to_node, "var(--danger)");
          } else if (ev === "org:message") {
            pushActivity(ev, d);
            triggerEdgeAnimation(d.from_node, d.to_node, "#a78bfa");
          } else if (ev === "org:broadcast") {
            pushActivity(ev, d);
          } else if (ev === "org:blackboard_update") {
            pushActivity(ev, d);
            if (currentOrg && !selectedNodeId) fetchBlackboard(currentOrg.id, bbScope);
          } else if (ev === "org:heartbeat_start" || ev === "org:heartbeat_done") {
            pushActivity(ev, d);
          } else if (ev === "org:task_complete") {
            pushActivity(ev, d);
          } else if (ev === "org:meeting_started" || ev === "org:meeting_round" || ev === "org:meeting_speak" || ev === "org:meeting_completed") {
            pushActivity(ev, d);
          } else if (ev === "org:watchdog_recovery") {
            pushActivity(ev, d);
          }
        } catch { /* ignore parse errors */ }
      };
    } catch { /* WebSocket not available */ }
    return () => { ws?.close(); };
  }, [liveMode, currentOrg, apiBaseUrl, setNodes, pushActivity, triggerEdgeAnimation, selectedNodeId, bbScope, fetchBlackboard]);

  // ── Start/Stop org ──
  const handleStartOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/start`, { method: "POST" });
      setCurrentOrg({ ...currentOrg, status: "active" });
      setLayoutLocked(true);
      const mode = (currentOrg as any).operation_mode || "command";
      showToast(
        mode === "autonomous"
          ? "组织已启动（自主模式）——顶层负责人将根据核心业务自动运营"
          : "组织已启动（命令模式）——可通过聊天或命令面板下达任务",
        "ok",
      );
    } catch (e) { console.error("Failed to start org:", e); }
  }, [currentOrg, apiBaseUrl, showToast]);

  const handleStopOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/stop`, { method: "POST" });
      setCurrentOrg({ ...currentOrg, status: "dormant" });
      setLayoutLocked(false);
    } catch (e) { console.error("Failed to stop org:", e); }
  }, [currentOrg, apiBaseUrl]);

  // ── Org export/import ──
  const orgImportRef = useRef<HTMLInputElement>(null);

  const handleExportOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      const safeName = currentOrg.name.replace(/\s+/g, "_").replace(/[/\\]/g, "_").slice(0, 30);
      const defaultName = `${safeName}.json`;

      if (IS_TAURI) {
        const savePath = await saveFileDialog({
          title: "导出组织配置",
          defaultPath: defaultName,
          filters: [{ name: "JSON", extensions: ["json"] }],
        });
        if (!savePath) return;
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/export`, { method: "POST" });
        const data = await res.json();
        await writeTextFile(savePath, JSON.stringify(data, null, 2));
        showToast(`组织已导出到: ${savePath}`);
      } else {
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/export`, { method: "POST" });
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = defaultName;
        a.click();
        URL.revokeObjectURL(url);
        showToast(`组织「${currentOrg.name}」已导出为 ${defaultName}`);
      }
    } catch (e) { showToast(String(e), "error"); }
  }, [currentOrg, apiBaseUrl, showToast]);

  const handleImportOrg = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/import`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      showToast(data.message || `组织「${data.organization?.name || ""}」导入成功`);
      fetchOrgList();
      if (data.organization?.id) {
        setSelectedOrgId(data.organization.id);
      }
    } catch (err) { showToast(String(err), "error"); }
    if (orgImportRef.current) orgImportRef.current.value = "";
  }, [apiBaseUrl, showToast, fetchOrgList]);

  const [confirmReset, setConfirmReset] = useState(false);
  const handleResetOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/reset`, { method: "POST" });
      const data = await res.json();
      setCurrentOrg(data);
      setLayoutLocked(false);
      setActivityFeed([]);
      setBbEntries([]);
      setNodeEvents([]);
      setNodeThinking([]);
      setNodeSchedules([]);
      setOrgStats(null);
      showToast("组织已重置");
    } catch (e) { console.error("Failed to reset org:", e); }
    setConfirmReset(false);
  }, [currentOrg, apiBaseUrl]);

  // ── Save ──

  const buildSavePayload = useCallback(() => {
    if (!currentOrg) return null;
    const updatedNodes = nodes.map((n) => ({
      ...n.data,
      position: n.position,
    }));
    const updatedEdges = edges.map((e) => ({
      ...(e.data || {}),
      id: e.id,
      source: e.source,
      target: e.target,
      edge_type: (e.data as any)?.edge_type || "hierarchy",
      label: (e.data as any)?.label || (e.label as string) || "",
      bidirectional: (e.data as any)?.bidirectional ?? true,
      priority: (e.data as any)?.priority ?? 0,
      bandwidth_limit: (e.data as any)?.bandwidth_limit ?? 60,
    }));
    return {
      name: currentOrg.name,
      description: currentOrg.description,
      user_persona: currentOrg.user_persona || { title: "负责人", display_name: "", description: "" },
      operation_mode: (currentOrg as any).operation_mode || "command",
      core_business: currentOrg.core_business || "",
      heartbeat_enabled: currentOrg.heartbeat_enabled,
      heartbeat_interval_s: currentOrg.heartbeat_interval_s,
      standup_enabled: currentOrg.standup_enabled,
      nodes: updatedNodes,
      edges: updatedEdges,
    };
  }, [currentOrg, nodes, edges]);

  const doSave = useCallback(async (quiet = false): Promise<boolean> => {
    if (!currentOrg) return false;
    const payload = buildSavePayload();
    if (!payload) return false;
    const snapshot = JSON.stringify(payload);
    if (snapshot === lastSavedRef.current) return true;
    setSaving(true);
    setSaveStatus("saving");
    try {
      const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: snapshot,
      });
      if (!resp.ok) throw new Error(`保存失败 (${resp.status})`);
      lastSavedRef.current = snapshot;
      if (!quiet) showToast("保存成功", "ok");
      fetchOrgList();
      setSaveStatus("saved");
      return true;
    } catch (e: any) {
      console.error("Failed to save org:", e);
      if (!quiet) showToast(e.message || "保存失败", "error");
      setSaveStatus("error");
      return false;
    } finally {
      setSaving(false);
    }
  }, [currentOrg, buildSavePayload, apiBaseUrl, fetchOrgList, showToast]);

  const handleSave = useCallback(() => doSave(false), [doSave]);

  doSaveRef.current = doSave;

  const autoSave = useCallback(() => {
    if (autoSaveTimerRef.current) clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = setTimeout(() => doSaveRef.current(true), 300);
  }, []);

  useEffect(() => {
    if (saveStatus !== "saved") return;
    const t = setTimeout(() => setSaveStatus("idle"), 2000);
    return () => clearTimeout(t);
  }, [saveStatus]);

  useEffect(() => {
    if (!currentOrg) return;
    const payload = buildSavePayload();
    if (!payload) return;
    const snap = JSON.stringify(payload);
    if (!lastSavedRef.current) lastSavedRef.current = snap;
  }, [currentOrg, buildSavePayload]);

  // ── Global ESC handler for all panels ──
  const rightPanelRef = useRef(rightPanel);
  rightPanelRef.current = rightPanel;
  const isMobileRef = useRef(isMobile);
  isMobileRef.current = isMobile;
  const showLeftPanelRef = useRef(showLeftPanel);
  showLeftPanelRef.current = showLeftPanel;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (contextMenu) {
        setContextMenu(null);
        return;
      }
      const rp = rightPanelRef.current;
      if (rp !== "none") {
        if (rp === "node" || rp === "edge") autoSave();
        if (rp === "node") setSelectedNodeId(null);
        if (rp === "edge") setSelectedEdgeId(null);
        setRightPanel("none");
        return;
      }
      if (isMobileRef.current && showLeftPanelRef.current) {
        setShowLeftPanel(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [autoSave, contextMenu]);

  // ── Create org ──

  const handleCreateOrg = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "新组织", description: "" }),
      });
      const data = await res.json();
      await fetchOrgList();
      setSelectedOrgId(data.id);
    } catch (e) {
      console.error("Failed to create org:", e);
    }
  }, [apiBaseUrl, fetchOrgList]);

  const handleCreateFromTemplate = useCallback(async (templateId: string) => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/from-template`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: templateId }),
      });
      const data = await res.json();
      await fetchOrgList();
      setSelectedOrgId(data.id);
      setShowTemplates(false);
    } catch (e) {
      console.error("Failed to create from template:", e);
    }
  }, [apiBaseUrl, fetchOrgList]);

  const [confirmDeleteOrgId, setConfirmDeleteOrgId] = useState<string | null>(null);

  const handleDeleteOrg = useCallback(async (orgId: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}`, { method: "DELETE" });
      if (selectedOrgId === orgId) {
        setSelectedOrgId(null);
        setCurrentOrg(null);
        setNodes([]);
        setEdges([]);
        setRightPanel("none");
      }
      fetchOrgList();
    } catch (e) {
      console.error("Failed to delete org:", e);
    } finally {
      setConfirmDeleteOrgId(null);
    }
  }, [apiBaseUrl, selectedOrgId, fetchOrgList, setNodes, setEdges]);

  // ── Node management ──

  const handleAddNode = useCallback(() => {
    if (!currentOrg || !newNodeTitle.trim()) return;
    const newId = `node_${Date.now().toString(36)}`;
    setNodes((prev) => {
      const newNode: OrgNodeData = {
        id: newId,
        role_title: newNodeTitle.trim(),
        role_goal: "",
        role_backstory: "",
        agent_source: "local",
        agent_profile_id: null,
        position: getNextNodePosition(prev),
        level: 0,
        department: newNodeDept.trim(),
        custom_prompt: "",
        identity_dir: null,
        mcp_servers: [],
        skills: [],
        skills_mode: "all",
        preferred_endpoint: null,
        max_concurrent_tasks: 1,
        timeout_s: 300,
        can_delegate: true,
        can_escalate: true,
        can_request_scaling: true,
        is_clone: false,
        clone_source: null,
        external_tools: [],
        ephemeral: false,
        frozen_by: null,
        frozen_reason: null,
        frozen_at: null,
        avatar: null,
        status: "idle",
      };
      return [...prev, orgNodeToFlowNode(newNode)];
    });
    setSelectedNodeId(newId);
    setSelectedEdgeId(null);
    setRightPanel("node");
    setPropsTab("overview");
    setNewNodeTitle("");
    setNewNodeDept("");
    setShowNewNodeForm(false);
  }, [currentOrg, newNodeTitle, newNodeDept, setNodes]);

  const handleDeleteNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((prev) => prev.filter((n) => n.id !== selectedNodeId));
    setEdges((prev) => prev.filter((e) => e.source !== selectedNodeId && e.target !== selectedNodeId));
    setSelectedNodeId(null);
    setRightPanel("none");
  }, [selectedNodeId, setNodes, setEdges]);

  // ── Edge connection ──

  const onConnect: OnConnect = useCallback(
    (params: Connection) => {
      const edgeId = `edge_${Date.now().toString(36)}`;
      const newEdge: Edge = {
        id: edgeId,
        source: params.source!,
        target: params.target!,
        type: "default",
        style: { stroke: EDGE_COLORS.hierarchy, strokeWidth: 2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS.hierarchy },
        data: {
          id: edgeId,
          source: params.source,
          target: params.target,
          edge_type: "hierarchy",
          label: "",
          bidirectional: true,
          priority: 0,
          bandwidth_limit: 60,
        },
      };
      setEdges((prev) => addEdge(newEdge, prev));
      autoSave();
    },
    [setEdges, autoSave],
  );

  // ── Node click ──

  const onNodeClick = useCallback((_: any, node: Node) => {
    if (selectedNodeId && selectedNodeId !== node.id) autoSave();
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    setPropsTab("overview");
    setRightPanel("node");
  }, [liveMode, selectedNodeId, autoSave]);

  const onEdgeClick = useCallback((_: any, edge: Edge) => {
    if (selectedNodeId || selectedEdgeId) autoSave();
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
    setRightPanel("edge");
  }, [selectedNodeId, selectedEdgeId, autoSave]);

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setContextMenu(null);
    if (rightPanel === "node" || rightPanel === "edge") {
      autoSave();
      setRightPanel("none");
    }
  }, [rightPanel, autoSave]);

  const onNodeDragStop = useCallback(() => {
    autoSave();
  }, [autoSave]);

  // ── Fetch node detail when selected in live mode ──
  useEffect(() => {
    if (!selectedNodeId || !currentOrg || !liveMode) {
      setNodeEvents([]);
      setNodeSchedules([]);
      setNodeMessages([]);
      setNodeThinking([]);
      return;
    }
    const fetchNodeDetail = async () => {
      try {
        const [eventsRes, schedulesRes, msgsRes, thinkingRes] = await Promise.all([
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/events?actor=${selectedNodeId}&limit=20`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/schedules`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/messages?from_node=${selectedNodeId}&limit=20`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/thinking?limit=30`),
        ]);
        if (eventsRes.ok) setNodeEvents(await eventsRes.json());
        if (schedulesRes.ok) setNodeSchedules(await schedulesRes.json());
        if (msgsRes.ok) {
          const data = await msgsRes.json();
          setNodeMessages(data.messages || data || []);
        }
        if (thinkingRes.ok) {
          const data = await thinkingRes.json();
          setNodeThinking(data.timeline || []);
        }
      } catch (e) {
        console.error("Failed to fetch node detail:", e);
      }
    };
    fetchNodeDetail();
    const interval = setInterval(fetchNodeDetail, 8000);
    return () => clearInterval(interval);
  }, [selectedNodeId, currentOrg, liveMode, apiBaseUrl]);

  // ── Fetch org stats in live mode ──
  useEffect(() => {
    if (!currentOrg || !liveMode) { setOrgStats(null); return; }
    const fetchStats = async () => {
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/stats`);
        if (res.ok) setOrgStats(await res.json());
      } catch (e) { /* ignore */ }
    };
    fetchStats();
    const interval = setInterval(fetchStats, 8000);
    return () => clearInterval(interval);
  }, [currentOrg, liveMode, apiBaseUrl]);

  // Node tasks fetch moved to OrgNodeInspector

  // ── Inject runtime metrics into nodes from orgStats ──
  useEffect(() => {
    if (!orgStats?.per_node || !orgStats?.anomalies) return;
    const nodeMap = new Map<string, any>();
    for (const nd of orgStats.per_node) nodeMap.set(nd.id, nd);
    const anomalyMap = new Map<string, string>();
    for (const a of orgStats.anomalies) anomalyMap.set(a.node_id, a.message);
    setNodes((prev) =>
      prev.map((n) => {
        const rt = nodeMap.get(n.id);
        if (!rt) return n;
        return {
          ...n,
          data: {
            ...n.data,
            _runtime: {
              idle_seconds: rt.idle_seconds,
              pending_messages: rt.pending_messages,
              anomaly: anomalyMap.get(n.id) || null,
              plan_progress: rt.plan_progress,
              delegated_summary: rt.delegated_summary,
              external_tools: rt.external_tools,
              running_since: rt.running_since,
              recent_activity_ts: rt.recent_activity_ts,
              last_watchdog_action: rt.last_watchdog_action,
            },
          },
        };
      }),
    );
  }, [orgStats, setNodes]);

  // ── Selected node data ──

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) return null;
    const n = nodes.find((n) => n.id === selectedNodeId);
    return n ? (n.data as unknown as OrgNodeData) : null;
  }, [selectedNodeId, nodes]);

  const updateNodeData = useCallback((field: string, value: any) => {
    if (!selectedNodeId) return;
    setNodes((prev) =>
      prev.map((n) =>
        n.id === selectedNodeId ? { ...n, data: { ...n.data, [field]: value } } : n,
      ),
    );
  }, [selectedNodeId, setNodes]);

  // ── Selected edge data ──

  const selectedEdge = useMemo(() => {
    if (!selectedEdgeId) return null;
    const e = edges.find((e) => e.id === selectedEdgeId);
    if (!e) return null;
    return { ...((e.data as any) || {}), source: e.source, target: e.target, _id: e.id };
  }, [selectedEdgeId, edges]);

  const updateEdgeData = useCallback((field: string, value: any) => {
    if (!selectedEdgeId) return;
    setEdges((prev) =>
      prev.map((e) => {
        if (e.id !== selectedEdgeId) return e;
        const newData = { ...e.data, [field]: value };
        const edgeType = field === "edge_type" ? value : (e.data as any)?.edge_type;
        return {
          ...e,
          data: newData,
          style: { stroke: EDGE_COLORS[edgeType] || "var(--muted)", strokeWidth: edgeType === "hierarchy" ? 2 : 1.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS[edgeType] || "var(--muted)" },
          animated: edgeType === "collaborate",
          label: field === "label" ? value : (e.data as any)?.label || undefined,
        };
      }),
    );
  }, [selectedEdgeId, setEdges]);

  const handleDeleteEdge = useCallback(() => {
    if (!selectedEdgeId) return;
    setEdges((prev) => prev.filter((e) => e.id !== selectedEdgeId));
    setSelectedEdgeId(null);
    setRightPanel("none");
  }, [selectedEdgeId, setEdges]);

  const ctxCopyNode = useCallback((nodeId: string) => {
    const n = nodes.find((n) => n.id === nodeId);
    if (n) setClipboardNode(structuredClone(n));
    setContextMenu(null);
  }, [nodes]);

  const ctxDeleteNode = useCallback((nodeId: string) => {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId));
    setEdges((prev) => prev.filter((e) => e.source !== nodeId && e.target !== nodeId));
    if (selectedNodeId === nodeId) {
      setSelectedNodeId(null);
      setRightPanel("none");
    }
    setContextMenu(null);
  }, [selectedNodeId, setNodes, setEdges]);

  const ctxDeleteEdge = useCallback((edgeId: string) => {
    setEdges((prev) => prev.filter((e) => e.id !== edgeId));
    if (selectedEdgeId === edgeId) {
      setSelectedEdgeId(null);
      setRightPanel("none");
    }
    setContextMenu(null);
  }, [selectedEdgeId, setEdges]);

  const ctxReverseEdge = useCallback((edgeId: string) => {
    setEdges((prev) => prev.map((e) => {
      if (e.id !== edgeId) return e;
      return { ...e, source: e.target, target: e.source };
    }));
    setContextMenu(null);
  }, [setEdges]);

  const ctxUnfreezeNode = useCallback(async (nodeId: string) => {
    setContextMenu(null);
    if (!selectedOrgId) return;
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${selectedOrgId}/nodes/${nodeId}/unfreeze`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      setNodes((prev) => prev.map((n) => {
        if (n.id !== nodeId) return n;
        return { ...n, data: { ...n.data, status: "idle", frozen_by: null, frozen_reason: null, frozen_at: null } };
      }));
      showToast("节点已解除冻结");
    } catch (e) {
      showToast(`解除冻结失败: ${e}`, "error");
    }
  }, [selectedOrgId, apiBaseUrl, setNodes, showToast]);

  const ctxPasteNode = useCallback(() => {
    if (!clipboardNode) return;
    const offset = 60;
    const newId = `node_${Date.now().toString(36)}`;
    const pasted = {
      ...structuredClone(clipboardNode),
      id: newId,
      position: { x: (clipboardNode.position?.x ?? 200) + offset, y: (clipboardNode.position?.y ?? 200) + offset },
      data: { ...clipboardNode.data, id: newId, role_title: `${clipboardNode.data?.role_title || "节点"} (副本)` },
      selected: false,
    };
    setNodes((prev) => [...prev, pasted]);
    setContextMenu(null);
  }, [clipboardNode, setNodes]);

  const ctxAddNodeAt = useCallback(() => {
    const newId = `node_${Date.now().toString(36)}`;
    const hasPanePosition = contextMenu?.type === "pane"
      && typeof contextMenu.flowX === "number"
      && typeof contextMenu.flowY === "number";
    const pos = hasPanePosition
      ? { x: contextMenu.flowX!, y: contextMenu.flowY! }
      : getNextNodePosition(nodes);
    const newNode: OrgNodeData = {
      id: newId, role_title: "新节点", role_goal: "", role_backstory: "",
      agent_source: "local", agent_profile_id: null, position: pos, level: 0,
      department: "", custom_prompt: "", identity_dir: null, mcp_servers: [], skills: [],
      skills_mode: "all", preferred_endpoint: null, max_concurrent_tasks: 1, timeout_s: 0,
      can_delegate: true, can_escalate: true, can_request_scaling: true, is_clone: false,
      clone_source: null, external_tools: [], ephemeral: false, frozen_by: null,
      frozen_reason: null, frozen_at: null, avatar: null, status: "idle",
    };
    setNodes((prev) => [...prev, orgNodeToFlowNode(newNode)]);
    setSelectedNodeId(newId);
    setSelectedEdgeId(null);
    setRightPanel("node");
    setPropsTab("overview");
    setContextMenu(null);
  }, [nodes, contextMenu, setNodes]);

  // ── Render ──

  if (!visible) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {currentOrg && (
        <OrgEditorTopBar
          currentOrg={currentOrg}
          setCurrentOrg={setCurrentOrg}
          showLeftPanel={showLeftPanel}
          setShowLeftPanel={setShowLeftPanel}
          isMobile={isMobile}
          saveStatus={saveStatus}
          doSaveRef={doSaveRef}
          liveMode={liveMode}
          orgStats={orgStats}
          viewMode={viewMode}
          setViewMode={setViewMode}
          autoSave={autoSave}
          handleStartOrg={handleStartOrg}
          handleStopOrg={handleStopOrg}
          layoutLocked={layoutLocked}
          setLayoutLocked={setLayoutLocked}
          saving={saving}
          handleSave={handleSave}
          rightPanel={rightPanel}
          setRightPanel={setRightPanel}
          activityFeed={activityFeed}
        />
      )}

      {/* ── Content area: Left + Canvas + Right ── */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden", position: "relative" }}>
      {/* ── Left Panel: Org List ── */}
      <PanelShell
        open={showLeftPanel}
        onClose={() => setShowLeftPanel(false)}
        width={240}
        maxWidth={320}
        side="left"
        isMobile={isMobile}
        style={{ overflow: "hidden" }}
      >
        <OrgListPanel
          showTemplates={showTemplates}
          setShowTemplates={setShowTemplates}
          templates={templates}
          handleCreateOrg={handleCreateOrg}
          handleCreateFromTemplate={handleCreateFromTemplate}
          orgImportRef={orgImportRef}
          handleImportOrg={handleImportOrg}
          isMobile={isMobile}
          setShowLeftPanel={setShowLeftPanel}
          orgList={orgList}
          selectedOrgId={selectedOrgId}
          setSelectedOrgId={setSelectedOrgId}
          doSave={doSave}
          confirmDeleteOrgId={confirmDeleteOrgId}
          setConfirmDeleteOrgId={setConfirmDeleteOrgId}
          handleDeleteOrg={handleDeleteOrg}
        />
      </PanelShell>

      {/* ── Center: Canvas ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Add node dialog */}
        <Dialog open={showNewNodeForm} onOpenChange={setShowNewNodeForm}>
          <DialogContent className="sm:max-w-[360px]">
            <DialogHeader>
              <DialogTitle>添加节点</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div>
                <Label className="text-[11px] mb-1">岗位名称 *</Label>
                <Input
                  placeholder="例如：产品经理"
                  value={newNodeTitle}
                  onChange={(e) => setNewNodeTitle(e.target.value)}
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
                />
              </div>
              <div>
                <Label className="text-[11px] mb-1">部门（可选）</Label>
                <Input
                  placeholder="例如：技术部"
                  value={newNodeDept}
                  onChange={(e) => setNewNodeDept(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowNewNodeForm(false)}>取消</Button>
              <Button onClick={handleAddNode}>添加</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Main content: Canvas / Projects / Dashboard */}
        {currentOrg ? (
          <>
          {viewMode === "dashboard" ? (
            <div style={{ flex: 1, overflow: "hidden" }}>
              <OrgDashboard
                orgId={currentOrg.id}
                apiBaseUrl={apiBaseUrl}
                orgName={currentOrg.name}
                onNodeClick={(nodeId) => {
                  setViewMode("canvas");
                  const n = nodes.find(nd => nd.id === nodeId);
                  if (n) {
                    setSelectedNodeId(nodeId);
                    setSelectedEdgeId(null);
                    setRightPanel("node");
                    setPropsTab("overview");
                  }
                }}
              />
            </div>
          ) : viewMode === "projects" ? (
            <div style={{ flex: 1, overflow: "hidden" }}>
              {selectedOrgId ? (
                <OrgProjectBoard
                  orgId={selectedOrgId}
                  apiBaseUrl={apiBaseUrl}
                  nodes={nodes.map(n => ({ id: n.id, role_title: (n.data as any)?.role_title, avatar: (n.data as any)?.avatar }))}
                />
              ) : (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", height: "100%" }}>
                  请先选择一个组织
                </div>
              )}
            </div>
          ) : (
          <div style={{ flex: 1, position: "relative" }} onContextMenu={(e) => e.preventDefault()}>
            <ReactFlow
              onInit={(instance) => {
                reactFlowRef.current = instance;
              }}
              nodes={nodes}
              edges={edges.map((e) => {
                const anim = edgeAnimations[e.id];
                const flowCount = liveMode ? edgeFlowCounts[e.id] : undefined;
                const base = flowCount && flowCount > 0
                  ? { ...e, label: `${(e.data as any)?.label || ""} ${flowCount > 0 ? `(${flowCount})` : ""}`.trim() || undefined }
                  : e;
                if (!anim) return base;
                return {
                  ...base,
                  animated: true,
                  style: { ...base.style, stroke: anim.color, strokeWidth: 3, filter: `drop-shadow(0 0 4px ${anim.color})` },
                  markerEnd: { ...(base.markerEnd as any), color: anim.color },
                };
              })}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={onNodeClick}
              onEdgeClick={onEdgeClick}
              onPaneClick={onPaneClick}
              onNodeDragStop={onNodeDragStop}
              onNodeContextMenu={(e, node) => { e.preventDefault(); e.stopPropagation(); setSelectedNodeId(node.id); setSelectedEdgeId(null); setContextMenu({ x: e.clientX, y: e.clientY, type: "node", id: node.id }); }}
              onEdgeContextMenu={(e, edge) => { e.preventDefault(); e.stopPropagation(); setSelectedEdgeId(edge.id); setSelectedNodeId(null); setContextMenu({ x: e.clientX, y: e.clientY, type: "edge", id: edge.id }); }}
              onPaneContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                const flow = reactFlowRef.current?.screenToFlowPosition({ x: e.clientX, y: e.clientY });
                setContextMenu({
                  x: e.clientX,
                  y: e.clientY,
                  type: "pane",
                  id: null,
                  flowX: flow?.x,
                  flowY: flow?.y,
                });
              }}
              nodeTypes={nodeTypes}
              connectOnClick
              connectionLineStyle={{ stroke: "#6366f1", strokeWidth: 2.5, strokeDasharray: "6 3" }}
              fitView
              snapToGrid
              snapGrid={[20, 20]}
              nodesDraggable={!layoutLocked}
              nodesConnectable={!layoutLocked}
              defaultEdgeOptions={{
                type: "default",
                style: { strokeWidth: 2 },
              }}
              style={{ background: "var(--bg-app)" }}
            >
              <Background gap={20} size={1} color="var(--line)" />
              <OrgCanvasControls />
              {/* Canvas-specific toolbar */}
              <Panel position="top-left">
                <div className="org-canvas-toolbar">
                  <button className="org-cvs-btn" onClick={() => setShowNewNodeForm(true)} title="添加节点">
                    <IconPlus size={13} /> 节点
                  </button>
                  <button className="org-cvs-btn" title="自动布局" onClick={() => { setNodes(computeTreeLayout(nodes, edges)); }}>
                    <IconSitemap size={13} /> 布局
                  </button>
                  {selectedNodeId && (
                    <button className="org-cvs-btn org-cvs-btn--danger" onClick={handleDeleteNode} title="删除选中节点">
                      <IconTrash size={13} />
                    </button>
                  )}
                </div>
              </Panel>
              {!isMobile && <CollapsibleMiniMap edgeColors={EDGE_COLORS} />}

            </ReactFlow>
            {/* ── Context menu (portal to body to avoid clipping) ── */}
            {contextMenu && createPortal(
              <div
                className="org-ctx-menu"
                style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y, zIndex: 99999 }}
                onClick={() => setContextMenu(null)}
                onContextMenu={(e) => e.preventDefault()}
              >
                {contextMenu.type === "node" && contextMenu.id && (<>
                  {liveMode && selectedOrgId && (
                    <button onClick={() => { setChatPanelNode(contextMenu.id); setRightPanel("command"); setContextMenu(null); }}>
                      <span className="org-ctx-icon">💬</span>与该节点对话
                    </button>
                  )}
                  {liveMode && selectedOrgId && (nodes.find(n => n.id === contextMenu.id)?.data as any)?.status === "frozen" && (
                    <button onClick={() => ctxUnfreezeNode(contextMenu.id!)}>
                      <span className="org-ctx-icon">🔓</span>解除冻结
                    </button>
                  )}
                  <button onClick={() => ctxCopyNode(contextMenu.id!)}>
                    <span className="org-ctx-icon">📋</span>复制节点
                  </button>
                  <button onClick={() => ctxDeleteNode(contextMenu.id!)}>
                    <span className="org-ctx-icon" style={{ color: "#ef4444" }}>🗑</span>删除节点
                  </button>
                </>)}
                {contextMenu.type === "edge" && contextMenu.id && (<>
                  <button onClick={() => ctxReverseEdge(contextMenu.id!)}>
                    <span className="org-ctx-icon">🔄</span>反转方向
                  </button>
                  <button onClick={() => ctxDeleteEdge(contextMenu.id!)}>
                    <span className="org-ctx-icon" style={{ color: "#ef4444" }}>🗑</span>删除连线
                  </button>
                </>)}
                {contextMenu.type === "pane" && (<>
                  <button onClick={() => ctxAddNodeAt()}>
                    <span className="org-ctx-icon">➕</span>添加节点
                  </button>
                  {clipboardNode && (
                    <button onClick={() => ctxPasteNode()}>
                      <span className="org-ctx-icon">📌</span>粘贴节点
                    </button>
                  )}
                  <button onClick={() => { setNodes(computeTreeLayout(nodes, edges)); setContextMenu(null); }}>
                    <span className="org-ctx-icon">🔀</span>自动布局
                  </button>
                </>)}
              </div>,
              document.body
            )}
            {/* ── Canvas bottom: live activity feed ── */}
            {liveMode && layoutLocked && orgStats && (() => {
              const perNode: any[] = orgStats.per_node || [];
              const recentTasks: any[] = orgStats.recent_tasks || [];
              const anomalies: any[] = orgStats.anomalies || [];
              const nodeLabel = (id: string) => {
                const nd = nodes.find(n => n.id === id);
                return (nd?.data as any)?.role_title || id?.slice(0, 6) || id || "?";
              };
              const typeIcon: Record<string, string> = {
                task_delegated: "📤", task_delivered: "📦", task_accepted: "✅",
                task_rejected: "↩️", task_timeout: "⏰",
              };
              const typeLabel: Record<string, string> = {
                task_delegated: "分配任务", task_delivered: "交付成果",
                task_accepted: "验收通过", task_rejected: "打回",
                task_timeout: "超时",
              };

              const busyLines: { key: string; node: string; text: string; pct: number; color: string }[] = [];
              for (const n of perNode) {
                if (n.status !== "busy" && !n.current_task_title) continue;
                const pp = n.plan_progress || {};
                const pct = pp.total > 0 ? Math.round((pp.completed / pp.total) * 100) : -1;
                const taskDesc = n.current_task_title || (n.current_task ? String(n.current_task).slice(0, 50) : "执行中…");
                busyLines.push({ key: n.id, node: n.role_title || nodeLabel(n.id), text: taskDesc, pct, color: "#3b82f6" });
              }

              if (busyLines.length === 0 && recentTasks.length === 0 && anomalies.length === 0) return null;

              return (
                <div className="org-live-feed">
                  {busyLines.map(b => (
                    <div key={b.key} className="org-feed-item org-feed-busy" onClick={() => {
                      setSelectedNodeId(b.key); setSelectedEdgeId(null); setRightPanel("node"); setPropsTab("tasks");
                    }}>
                      <span className="org-feed-dot" style={{ background: b.color, animation: "orgDotPulse 1.5s ease-in-out infinite" }} />
                      <span className="org-feed-who">{b.node}</span>
                      <span className="org-feed-text">{b.text}</span>
                      {b.pct >= 0 && (
                        <span className="org-feed-progress">
                          <span className="org-feed-bar"><span className="org-feed-bar-fill" style={{ width: `${b.pct}%` }} /></span>
                          <span className="org-feed-pct">{b.pct}%</span>
                        </span>
                      )}
                    </div>
                  ))}
                  {recentTasks.slice(0, 6).map((t: any, i: number) => {
                    const ts = t.t ? new Date(typeof t.t === "number" && t.t < 1e12 ? t.t * 1000 : t.t) : null;
                    const timeStr = ts ? `${String(ts.getHours()).padStart(2, "0")}:${String(ts.getMinutes()).padStart(2, "0")}` : "";
                    const icon = typeIcon[t.type] || "📋";
                    const label = typeLabel[t.type] || t.type;
                    const statusCls = t.status === "accepted" ? "org-feed-ok" : t.status === "rejected" ? "org-feed-err" : "";
                    return (
                      <div key={`rt-${i}`} className={`org-feed-item ${statusCls}`}>
                        <span className="org-feed-time">{timeStr}</span>
                        <span className="org-feed-icon">{icon}</span>
                        <span className="org-feed-who">{nodeLabel(t.from)}</span>
                        <span className="org-feed-arrow">→</span>
                        <span className="org-feed-who">{nodeLabel(t.to)}</span>
                        <span className="org-feed-label">{label}</span>
                        {t.task && <span className="org-feed-text">{t.task.slice(0, 40)}{t.task.length > 40 ? "…" : ""}</span>}
                      </div>
                    );
                  })}
                  {anomalies.map((a: any, i: number) => (
                    <div key={`an-${i}`} className="org-feed-item org-feed-warn">
                      <span className="org-feed-icon">⚠</span>
                      <span className="org-feed-who" style={{ color: "#f59e0b" }}>{a.role_title || nodeLabel(a.node_id)}</span>
                      <span className="org-feed-text" style={{ color: "#f59e0b" }}>{String(a.message).slice(0, 50)}</span>
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
          )}

          {/* ═══ Floating Chat FAB (always visible when org selected) ═══ */}
          {selectedOrgId && rightPanel !== "command" && (
            <button
              onClick={() => { setChatPanelNode(null); setRightPanel("command"); }}
              className="org-chat-fab"
              title="打开组织指挥台"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              <span className="org-chat-fab-label">指挥台</span>
            </button>
          )}

          {/* ═══ Slide-out Chat Panel ═══ */}
          {selectedOrgId && (
            <>
              <div
                className="org-chat-overlay"
                onClick={() => setRightPanel("none")}
                style={{ display: rightPanel === "command" ? undefined : "none" }}
              />
              <div className="org-chat-slide" style={{ display: rightPanel === "command" ? undefined : "none" }}>
                <OrgChatPanel
                  orgId={selectedOrgId}
                  nodeId={chatPanelNode}
                  apiBaseUrl={apiBaseUrl}
                  showHeader
                  title={chatPanelNode
                    ? `对话 · ${(nodes.find(n => n.id === chatPanelNode)?.data as any)?.role_title || chatPanelNode}`
                    : `${currentOrg?.name || "组织"} · 指挥台`}
                  onClose={() => setRightPanel("none")}
                />
              </div>
            </>
          )}

          <style>{`
            .org-chat-fab {
              position: absolute; bottom: 18px; left: 0; right: 0; z-index: 40;
              display: flex; align-items: center; gap: 8px;
              width: fit-content;
              margin: 0 auto;
              padding: 12px 20px; border: none; border-radius: 16px;
              background: linear-gradient(135deg, #3b82f6, #6366f1) !important;
              color: #ffffff !important; cursor: pointer; font-size: 13px; font-weight: 600;
              box-shadow: 0 4px 20px rgba(99,102,241,0.4), 0 0 40px rgba(99,102,241,0.15);
              transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
              animation: org-fab-in 0.4s cubic-bezier(0.34,1.56,0.64,1);
              -webkit-text-fill-color: #ffffff !important;
            }
            @keyframes org-fab-in {
              from { transform: scale(0.5) translateY(20px); opacity: 0; }
              to { transform: scale(1) translateY(0); opacity: 1; }
            }
            .org-chat-fab:hover {
              transform: translateY(-2px) scale(1.02);
              background: linear-gradient(135deg, #2563eb, #4f46e5) !important;
              color: #ffffff !important;
              -webkit-text-fill-color: #ffffff !important;
              box-shadow: 0 6px 28px rgba(99,102,241,0.6), 0 0 60px rgba(99,102,241,0.25);
            }
            .org-chat-fab:active { transform: scale(0.97); }
            .org-chat-fab svg { stroke: #ffffff !important; }
            .org-chat-fab-label { letter-spacing: 0.5px; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }

            .org-chat-overlay {
              position: absolute; inset: 0; z-index: 80;
              background: rgba(0,0,0,0.3);
              backdrop-filter: blur(2px);
              animation: org-overlay-in 0.2s ease;
            }
            @keyframes org-overlay-in { from { opacity: 0; } to { opacity: 1; } }

            .org-chat-slide {
              position: absolute; top: 0; right: 0; bottom: 0; z-index: 90;
              width: min(420px, 85%);
              background: var(--bg-app);
              border-left: 1px solid var(--line);
              box-shadow: -8px 0 30px rgba(0,0,0,0.3);
              animation: org-slide-in 0.3s cubic-bezier(0.4,0,0.2,1);
            }
            @keyframes org-slide-in { from { transform: translateX(100%); } to { transform: translateX(0); } }

            .org-ctx-menu {
              min-width: 160px;
              background: var(--card-bg);
              border: 1px solid var(--line);
              border-radius: 10px;
              padding: 4px;
              box-shadow: 0 8px 30px rgba(0,0,0,0.35), 0 0 1px rgba(255,255,255,0.1);
              backdrop-filter: blur(12px);
              animation: org-ctx-in 0.15s ease;
            }
            @keyframes org-ctx-in { from { opacity: 0; transform: scale(0.92); } to { opacity: 1; transform: scale(1); } }
            .org-ctx-menu button {
              display: flex; align-items: center; gap: 8px; width: 100%;
              padding: 8px 12px; border: none; border-radius: 7px;
              background: transparent; color: var(--text);
              font-size: 13px; cursor: pointer; text-align: left;
              transition: background 0.15s;
            }
            .org-ctx-menu button:hover { background: var(--hover-bg); }
            .org-ctx-icon { width: 18px; text-align: center; flex-shrink: 0; font-size: 14px; }

            /* ── Top bar layout ── */
            .org-topbar {
              height: 52px;
              border-bottom: 1px solid var(--line);
              display: grid;
              grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
              align-items: center;
              padding: 0 10px;
              background: var(--bg-app);
              flex-shrink: 0;
              gap: 10px;
            }
            .org-topbar-left {
              display: flex; align-items: center; gap: 6px;
              flex-shrink: 1; min-width: 0; overflow: hidden;
            }
            .org-topbar-center {
              display: flex;
              justify-content: center;
              min-width: 0;
            }
            .org-topbar-name {
              height: 32px;
              border: none; background: transparent;
              font-weight: 600; font-size: 14px;
              outline: none; width: 110px;
              color: var(--text);
              padding: 0 10px;
              border-radius: 10px;
              transition: background 0.15s, box-shadow 0.15s;
            }
            .org-topbar-name:hover { background: rgba(99,102,241,0.05); }
            .org-topbar-name:focus {
              background: rgba(99,102,241,0.06);
              box-shadow: inset 0 0 0 1px rgba(99,102,241,0.22);
            }
            .org-topbar-status {
              font-size: 10px; padding: 2px 6px; border-radius: 4px;
              font-weight: 600; white-space: nowrap; flex-shrink: 0;
            }
            .org-topbar-stats {
              display: flex; gap: 5px; align-items: center;
              font-size: 10px; color: var(--muted);
              flex-shrink: 0;
            }
            .org-health-dot {
              width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
            }

            /* ── View tabs (center) ── */
            .org-topbar-tabs {
              display: flex; align-items: center; gap: 0;
              border-bottom: 2px solid transparent;
              flex-shrink: 0;
            }
            .org-view-tab {
              display: inline-flex; align-items: center; gap: 5px;
              height: 32px; padding: 0 16px;
              border: none; background: transparent;
              border-bottom: 2px solid transparent;
              margin-bottom: -1px;
              color: var(--muted); font-size: 13px; font-weight: 500;
              cursor: pointer; white-space: nowrap;
              transition: color 0.15s, border-color 0.15s;
            }
            .org-view-tab:hover { color: var(--text); }
            .org-view-tab--active {
              color: var(--primary) !important; font-weight: 600;
              border-bottom-color: var(--primary) !important;
            }

            /* ── Right actions ── */
            .org-topbar-right {
              display: flex; align-items: center; justify-content: flex-end; gap: 6px; flex-shrink: 0;
              min-width: 0;
            }
            .org-topbar-live-pill {
              display: inline-flex;
              justify-content: center;
              align-items: center;
              width: 32px;
              min-width: 32px;
              height: 32px;
              border: 1px solid var(--line);
              border-radius: 0.5rem;
              background: var(--background, var(--bg-app));
              color: var(--muted);
              transition: background 0.15s, color 0.15s, border-color 0.15s, box-shadow 0.15s;
            }
            .org-topbar-live-pill--active {
              color: var(--primary);
              background: rgba(99,102,241,0.08);
              border-color: rgba(99,102,241,0.25);
              box-shadow: inset 0 0 0 1px rgba(99,102,241,0.08);
            }
            .org-topbar-live-pill--active svg {
              animation: orgLivePulse 1.5s ease-in-out infinite;
            }
            @keyframes orgLivePulse {
              0%, 100% { transform: scale(1); opacity: 0.85; }
              50% { transform: scale(1.12); opacity: 1; }
            }
            .org-tb-btn {
              display: inline-flex; align-items: center; gap: 4px;
              height: 28px; padding: 0 8px; border-radius: 6px;
              border: 1px solid var(--line);
              background: transparent;
              color: var(--text);
              font-size: 12px; cursor: pointer; white-space: nowrap;
              transition: background 0.15s, color 0.15s, border-color 0.15s;
              position: relative;
            }
            .org-tb-btn:hover {
              background: var(--hover-bg);
              border-color: rgba(99,102,241,0.3);
            }
            .org-tb-btn:active { background: rgba(99,102,241,0.2); }
            .org-tb-btn:disabled { opacity: 0.4; cursor: not-allowed; }
            .org-tb-btn--active {
              color: var(--primary); font-weight: 600;
              background: rgba(99,102,241,0.12);
              border-color: rgba(99,102,241,0.35);
            }
            .org-tb-btn--ok { color: #22c55e; border-color: rgba(34,197,94,0.3); }
            .org-tb-btn--ok:hover { background: rgba(34,197,94,0.12); }
            .org-tb-btn--danger { color: #ef4444; border-color: rgba(239,68,68,0.3); }
            .org-tb-btn--danger:hover { background: rgba(239,68,68,0.12); }
            .org-notif-dot {
              position: absolute; top: 3px; right: 3px;
              width: 5px; height: 5px; border-radius: 50%;
              background: var(--ok, #22c55e);
              animation: orgDotPulse 1.5s ease-in-out infinite;
            }

            /* ── Node handle styling ── */
            .org-handle {
              width: 10px !important;
              height: 10px !important;
              background: #6366f1 !important;
              border: 2px solid #fff !important;
              border-radius: 50% !important;
              transition: all 0.2s ease;
            }
            .org-handle:hover {
              width: 14px !important;
              height: 14px !important;
              background: #4f46e5 !important;
              box-shadow: 0 0 0 3px rgba(99,102,241,0.3), 0 0 8px rgba(99,102,241,0.4) !important;
            }

            /* ── Canvas toolbar (inside ReactFlow) ── */
            .org-canvas-toolbar {
              display: flex; align-items: center; gap: 4px;
              background: var(--card-bg);
              border: 1px solid var(--line);
              border-radius: 8px; padding: 3px 4px;
              box-shadow: 0 2px 8px rgba(0,0,0,0.2);
              backdrop-filter: blur(8px);
            }
            .org-cvs-btn {
              display: inline-flex; align-items: center; gap: 4px;
              height: 26px; padding: 0 10px; border-radius: 5px;
              border: none; background: transparent;
              color: var(--text); font-size: 11px;
              cursor: pointer; white-space: nowrap;
              transition: background 0.15s;
            }
            .org-cvs-btn:hover { background: rgba(99,102,241,0.15); }
            .org-cvs-btn--danger { color: #ef4444; }
            .org-cvs-btn--danger:hover { background: rgba(239,68,68,0.15); }

            .org-tb-stats {
              display: flex; gap: 6px; align-items: center;
              font-size: 10px; color: var(--muted);
              padding: 0 4px;
            }

            /* ── Canvas bottom live activity feed ── */
            .org-live-feed {
              position: absolute; bottom: 0; left: 0; right: 0;
              z-index: 5; max-height: 140px; overflow-y: auto;
              background: linear-gradient(to top, var(--bg-app) 75%, transparent);
              padding: 10px 14px 6px;
              scrollbar-width: thin;
            }
            .org-feed-item {
              display: flex; align-items: center; gap: 6px;
              padding: 3px 0; font-size: 11px; color: var(--text);
              line-height: 1.4; white-space: nowrap;
              border-bottom: 1px solid rgba(51,65,85,0.15);
            }
            .org-feed-item:last-child { border-bottom: none; }
            .org-feed-busy { cursor: pointer; }
            .org-feed-busy:hover .org-feed-who { color: var(--primary); }
            .org-feed-ok { }
            .org-feed-err .org-feed-label { color: #ef4444; }
            .org-feed-warn { color: #f59e0b; }
            .org-feed-dot {
              width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
            }
            .org-feed-time {
              font-size: 10px; color: var(--muted); font-family: monospace;
              flex-shrink: 0; min-width: 36px;
            }
            .org-feed-icon { flex-shrink: 0; font-size: 12px; }
            .org-feed-who {
              font-weight: 600; color: var(--text); flex-shrink: 0;
              max-width: 100px; overflow: hidden; text-overflow: ellipsis;
              transition: color 0.15s;
            }
            .org-feed-arrow { color: var(--muted); flex-shrink: 0; font-size: 10px; }
            .org-feed-label {
              font-size: 10px; padding: 1px 5px; border-radius: 3px;
              background: rgba(99,102,241,0.12); color: var(--primary);
              flex-shrink: 0;
            }
            .org-feed-text {
              color: var(--muted); font-size: 10px;
              overflow: hidden; text-overflow: ellipsis; min-width: 0;
            }
            .org-feed-progress {
              display: inline-flex; align-items: center; gap: 4px;
              flex-shrink: 0;
            }
            .org-feed-bar {
              width: 48px; height: 3px; border-radius: 2px;
              background: rgba(51,65,85,0.3); overflow: hidden;
            }
            .org-feed-bar-fill {
              height: 100%; border-radius: 2px;
              background: #3b82f6; transition: width 0.3s ease;
            }
            .org-feed-pct {
              font-size: 9px; color: var(--muted); font-weight: 600;
            }

            /* ── Blackboard markdown content ── */
            .bb-entry-content { font-size: 11px; line-height: 1.5; }
            .bb-entry-content p { margin: 0 0 4px; }
            .bb-entry-content p:last-child { margin-bottom: 0; }
            .bb-entry-content h1, .bb-entry-content h2, .bb-entry-content h3,
            .bb-entry-content h4, .bb-entry-content h5, .bb-entry-content h6 {
              margin: 4px 0 2px; font-weight: 600;
            }
            .bb-entry-content h1 { font-size: 14px; }
            .bb-entry-content h2 { font-size: 13px; }
            .bb-entry-content ul, .bb-entry-content ol {
              margin: 2px 0; padding-left: 16px;
            }
            .bb-entry-content li { margin: 1px 0; }
            .bb-entry-content li::marker { color: var(--muted); }
            .bb-entry-content strong { font-weight: 600; }
            .bb-entry-content em { font-style: italic; }
            .bb-entry-content code {
              font-size: 10px; padding: 1px 3px;
              background: var(--hover-bg); border-radius: 2px;
            }
            .bb-entry-content pre {
              margin: 4px 0; padding: 4px 6px;
              background: var(--hover-bg); border-radius: 3px;
              overflow-x: auto; font-size: 10px;
            }
            .bb-entry-content pre code { padding: 0; background: none; }
            .bb-entry-content blockquote {
              margin: 4px 0; padding-left: 8px;
              border-left: 2px solid var(--line);
              color: var(--muted);
            }
            .bb-entry-content table { border-collapse: collapse; margin: 4px 0; font-size: 11px; width: 100%; }
            .bb-entry-content th, .bb-entry-content td {
              padding: 2px 6px; border: 1px solid var(--line);
            }
            .bb-entry-content th { font-weight: 600; background: var(--hover-bg); }
            .bb-entry-content hr { border: none; border-top: 1px solid var(--line); margin: 6px 0; }
            .bb-entry-content a { color: var(--primary); text-decoration: underline; }

            /* ── Save button feedback ── */
            .org-save-btn {
              transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            }
            .org-save-btn:active:not(:disabled) {
              transform: scale(0.92);
              box-shadow: 0 0 0 2px rgba(99,102,241,0.3);
            }
            .org-save-btn--saving {
              animation: orgSavePulse 0.8s ease-in-out infinite;
              pointer-events: none;
            }
            @keyframes orgSavePulse {
              0%, 100% { opacity: 1; }
              50% { opacity: 0.6; }
            }

            /* ── Auto-save status indicator ── */
            .org-save-indicator {
              font-size: 10px; padding: 2px 6px; border-radius: 4px;
              transition: opacity 0.3s;
            }
            .org-save-indicator--saving { color: var(--muted); }
            .org-save-indicator--saved { color: #22c55e; }
            .org-save-indicator--error { color: #ef4444; }
          `}</style>
          </>
        ) : (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}
            onClick={() => { if (isMobile) setShowLeftPanel(true); }}
          >
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
              <IconUsers size={48} />
              <p style={{ marginTop: 12, fontSize: 14 }}>
                {isMobile ? "点击打开组织列表" : "选择或创建一个组织开始编排"}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* ── Right Panel: Node Properties ── */}
      <PanelShell
        open={rightPanel === "node" && !!selectedNode}
        onClose={() => { autoSave(); setSelectedNodeId(null); setRightPanel("none"); }}
        width={480}
        isMobile={isMobile}
      >
        {selectedNode && (
          <OrgNodeInspector
            selectedNode={selectedNode}
            selectedNodeId={selectedNodeId!}
            selectedOrgId={selectedOrgId}
            updateNodeData={updateNodeData}
            autoSave={autoSave}
            onClose={() => { autoSave(); setSelectedNodeId(null); setRightPanel('none'); }}
            liveMode={liveMode}
            currentOrg={currentOrg}
            apiBaseUrl={apiBaseUrl}
            nodes={nodes}
            md={md}
            setChatPanelNode={setChatPanelNode}
            setRightPanel={setRightPanel}
            setSelectedNodeId={setSelectedNodeId}
            nodeSchedules={nodeSchedules}
            nodeEvents={nodeEvents}
            nodeThinking={nodeThinking}
            orgStats={orgStats}
            agentProfiles={agentProfiles}
            availableMcpServers={availableMcpServers}
            availableSkills={availableSkills}
            propsTab={propsTab}
            setPropsTab={setPropsTab}
          />
        )}
      </PanelShell>

      {/* ── Right Panel: Edge Properties ── */}
      <PanelShell
        open={rightPanel === "edge" && !!selectedEdge}
        onClose={() => { autoSave(); setSelectedEdgeId(null); setRightPanel("none"); }}
        width={280}
        isMobile={isMobile}
      >
        {selectedEdge && (
          <OrgEdgeInspector
            selectedEdge={selectedEdge}
            nodes={nodes}
            updateEdgeData={updateEdgeData}
            handleDeleteEdge={handleDeleteEdge}
            onClose={() => { autoSave(); setSelectedEdgeId(null); setRightPanel("none"); }}
          />
        )}
      </PanelShell>

      {/* ── Right Panel: Org Settings ── */}
      <PanelShell
        open={rightPanel === "org" && !!currentOrg}
        onClose={() => { autoSave(); setRightPanel("none"); }}
        width={300}
        isMobile={isMobile}
      >
        {currentOrg && (
          <OrgSettingsPanel
            currentOrg={currentOrg}
            setCurrentOrg={setCurrentOrg}
            autoSave={autoSave}
            onClose={() => { autoSave(); setRightPanel("none"); }}
            liveMode={liveMode}
            apiBaseUrl={apiBaseUrl}
            md={md}
            handleExportOrg={handleExportOrg}
            handleImportOrg={handleImportOrg}
            bbEntries={bbEntries}
            setBbEntries={setBbEntries}
            bbScope={bbScope}
            setBbScope={setBbScope}
            bbLoading={bbLoading}
            fetchBlackboard={fetchBlackboard}
            confirmReset={confirmReset}
            setConfirmReset={setConfirmReset}
          />
        )}
      </PanelShell>

      {/* Inbox Sidebar */}
      <PanelShell
        open={rightPanel === "inbox" && !!currentOrg}
        onClose={() => setRightPanel("none")}
        width={380}
        isMobile={isMobile}
      >
        {currentOrg && (
          <OrgInboxSidebar
            apiBaseUrl={apiBaseUrl}
            orgId={currentOrg.id}
            visible={true}
            onClose={() => setRightPanel("none")}
          />
        )}
      </PanelShell>
      </div>{/* close content area */}

      {/* Toast notification */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          zIndex: 9999, display: "flex", alignItems: "center", gap: 6,
          padding: "8px 16px", borderRadius: 8, fontSize: 13, fontWeight: 500,
          color: "#fff", boxShadow: "0 4px 16px rgba(0,0,0,0.2)",
          background: toast.type === "ok" ? "var(--ok, #22c55e)" : "var(--danger, #ef4444)",
          animation: "toast-in 0.2s ease",
        }}>
          {toast.type === "ok" ? <IconCheck size={14} /> : <IconAlertCircle size={14} />}
          {toast.message}
        </div>
      )}
      <ConfirmDialog
        dialog={confirmReset ? { message: "确认重置该组织吗？将清空所有运行数据（黑板、消息、事件日志），恢复为初始状态。此操作不可撤销。", onConfirm: handleResetOrg } : null}
        onClose={() => setConfirmReset(false)}
      />
    </div>
  );
}
