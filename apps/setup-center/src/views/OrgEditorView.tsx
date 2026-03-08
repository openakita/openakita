import {
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  useLayoutEffect,
} from "react";
import { useTranslation } from "react-i18next";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
  type EdgeTypes,
  type NodeChange,
  type EdgeChange,
  Handle,
  Position,
  MarkerType,
  Panel,
  type OnConnect,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  IconPlus,
  IconTrash,
  IconRefresh,
  IconPlay,
  IconStop,
  IconCheck,
  IconX,
  IconUsers,
  IconChevronDown,
  IconChevronRight,
  IconRadar,
  IconSave,
  IconHeartPulse,
  IconSun,
  IconInbox,
  IconMaximize2,
  IconSnowflake,
  IconLayoutGrid,
  IconBuilding,
  IconClipboard,
  IconMenu,
} from "../icons";
import { safeFetch } from "../providers";
import { openPopupWindow, canOpenPopupWindow, IS_CAPACITOR } from "../platform";
import { OrgInboxSidebar } from "../components/OrgInboxSidebar";

// ── Types ──

interface OrgNodeData {
  id: string;
  role_title: string;
  role_goal: string;
  role_backstory: string;
  agent_source: string;
  agent_profile_id: string | null;
  position: { x: number; y: number };
  level: number;
  department: string;
  custom_prompt: string;
  identity_dir: string | null;
  mcp_servers: string[];
  skills: string[];
  skills_mode: string;
  preferred_endpoint: string | null;
  max_concurrent_tasks: number;
  timeout_s: number;
  can_delegate: boolean;
  can_escalate: boolean;
  can_request_scaling: boolean;
  is_clone: boolean;
  clone_source: string | null;
  external_tools: string[];
  ephemeral: boolean;
  frozen_by: string | null;
  frozen_reason: string | null;
  frozen_at: string | null;
  status: string;
  auto_clone_enabled?: boolean;
  auto_clone_threshold?: number;
  auto_clone_max?: number;
}

interface OrgEdgeData {
  id: string;
  source: string;
  target: string;
  edge_type: string;
  label: string;
  bidirectional: boolean;
  priority: number;
  bandwidth_limit: number;
}

interface OrgSummary {
  id: string;
  name: string;
  description: string;
  icon: string;
  status: string;
  node_count: number;
  edge_count: number;
  tags: string[];
  created_at: string;
  updated_at: string;
}

interface UserPersona {
  title: string;
  display_name: string;
  description: string;
}

interface OrgFull {
  id: string;
  name: string;
  description: string;
  icon: string;
  status: string;
  nodes: OrgNodeData[];
  edges: OrgEdgeData[];
  user_persona?: UserPersona;
  [key: string]: any;
}

interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  icon: string;
  node_count: number;
  tags: string[];
}

// ── Helpers ──

const EDGE_COLORS: Record<string, string> = {
  hierarchy: "var(--primary)",
  collaborate: "var(--ok)",
  escalate: "var(--danger)",
  consult: "#a78bfa",
};

const STATUS_COLORS: Record<string, string> = {
  idle: "var(--ok)",
  busy: "var(--primary)",
  waiting: "#f59e0b",
  error: "var(--danger)",
  offline: "var(--muted)",
  frozen: "#93c5fd",
  dormant: "var(--muted)",
  active: "var(--ok)",
  running: "var(--primary)",
  paused: "#f59e0b",
  archived: "var(--muted)",
};

const DEPT_COLORS: Record<string, string> = {
  "管理层": "#6366f1",
  "技术部": "#0ea5e9",
  "产品部": "#8b5cf6",
  "市场部": "#f97316",
  "行政支持": "#64748b",
  "工程": "#0ea5e9",
  "前端组": "#06b6d4",
  "后端组": "#14b8a6",
  "编辑部": "#f97316",
  "创作组": "#ec4899",
  "运营组": "#84cc16",
};

function getDeptColor(dept: string): string {
  return DEPT_COLORS[dept] || "#6b7280";
}

function orgNodeToFlowNode(n: OrgNodeData): Node {
  return {
    id: n.id,
    type: "orgNode",
    position: n.position,
    data: { ...n },
  };
}

function orgEdgeToFlowEdge(e: OrgEdgeData): Edge {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    type: "default",
    label: e.label || undefined,
    style: { stroke: EDGE_COLORS[e.edge_type] || "var(--muted)", strokeWidth: e.edge_type === "hierarchy" ? 2 : 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS[e.edge_type] || "var(--muted)" },
    animated: e.edge_type === "collaborate",
    data: { ...e },
  };
}

// ── Auto-layout: tree hierarchy ──

function computeTreeLayout(nodes: Node[], edges: Edge[]): Node[] {
  if (nodes.length === 0) return nodes;

  const NODE_W = 240;
  const NODE_H = 100;
  const GAP_X = 40;
  const GAP_Y = 80;

  const childrenMap: Record<string, string[]> = {};
  const parentSet = new Set<string>();
  for (const e of edges) {
    const src = e.source;
    const tgt = e.target;
    if (!childrenMap[src]) childrenMap[src] = [];
    childrenMap[src].push(tgt);
    parentSet.add(tgt);
  }

  const roots = nodes.filter((n) => !parentSet.has(n.id));
  if (roots.length === 0) return nodes;

  const levels: string[][] = [];
  const visited = new Set<string>();

  function bfs() {
    let queue = roots.map((r) => r.id);
    while (queue.length > 0) {
      const level: string[] = [];
      const next: string[] = [];
      for (const id of queue) {
        if (visited.has(id)) continue;
        visited.add(id);
        level.push(id);
        for (const c of childrenMap[id] || []) {
          if (!visited.has(c)) next.push(c);
        }
      }
      if (level.length > 0) levels.push(level);
      queue = next;
    }
  }
  bfs();

  for (const n of nodes) {
    if (!visited.has(n.id)) {
      if (levels.length === 0) levels.push([]);
      levels[levels.length - 1].push(n.id);
    }
  }

  const posMap: Record<string, { x: number; y: number }> = {};
  const maxLevelWidth = Math.max(...levels.map((l) => l.length));
  const totalW = maxLevelWidth * (NODE_W + GAP_X) - GAP_X;

  for (let li = 0; li < levels.length; li++) {
    const level = levels[li];
    const levelW = level.length * (NODE_W + GAP_X) - GAP_X;
    const offsetX = (totalW - levelW) / 2;
    for (let ni = 0; ni < level.length; ni++) {
      posMap[level[ni]] = {
        x: offsetX + ni * (NODE_W + GAP_X),
        y: li * (NODE_H + GAP_Y),
      };
    }
  }

  return nodes.map((n) => {
    const pos = posMap[n.id];
    if (!pos) return n;
    return { ...n, position: { x: pos.x, y: pos.y } };
  });
}

function detectOverlap(nodes: Node[]): boolean {
  const NODE_W = 200;
  const NODE_H = 80;
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i].position;
      const b = nodes[j].position;
      if (Math.abs(a.x - b.x) < NODE_W && Math.abs(a.y - b.y) < NODE_H) return true;
    }
  }
  return false;
}

// ── Custom Node Component ──

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  busy: "执行中",
  waiting: "等待中",
  error: "异常",
  offline: "离线",
  frozen: "已冻结",
};

function OrgNodeComponent({ data, selected }: { data: OrgNodeData; selected: boolean }) {
  const deptColor = getDeptColor(data.department);
  const statusColor = STATUS_COLORS[data.status] || "var(--muted)";
  const isFrozen = data.status === "frozen";
  const isBusy = data.status === "busy";
  const isError = data.status === "error";
  const isWaiting = data.status === "waiting";
  const isClone = data.is_clone;
  const isEphemeral = data.ephemeral;

  return (
    <div
      style={{
        background: "var(--bg-card, #fff)",
        border: `2px solid ${selected ? "var(--primary)" : isError ? "var(--danger)" : isBusy ? statusColor : "var(--line)"}`,
        borderRadius: "var(--radius)",
        padding: 0,
        minWidth: 180,
        maxWidth: 220,
        boxShadow: selected
          ? "0 0 0 2px var(--primary)"
          : isBusy
          ? `0 0 16px ${statusColor}50`
          : isError
          ? `0 0 12px var(--danger, #ef4444)30`
          : "0 1px 4px rgba(0,0,0,0.08)",
        opacity: isFrozen ? 0.5 : 1,
        filter: isFrozen ? "grayscale(0.6)" : "none",
        transition: "all 0.3s ease",
        animation: isBusy
          ? "orgNodePulse 2s ease-in-out infinite"
          : isError
          ? "orgNodeError 1s ease-in-out infinite"
          : isWaiting
          ? "orgNodeWait 3s ease-in-out infinite"
          : "none",
        position: "relative",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: "var(--primary)", width: 8, height: 8 }} />

      {/* Department color strip */}
      <div style={{
        height: 4,
        borderRadius: "var(--radius) var(--radius) 0 0",
        background: isBusy
          ? `linear-gradient(90deg, ${deptColor}, ${statusColor}, ${deptColor})`
          : deptColor,
        backgroundSize: isBusy ? "200% 100%" : undefined,
        animation: isBusy ? "orgStripFlow 2s linear infinite" : undefined,
      }} />

      <div style={{ padding: "8px 12px" }}>
        {/* Status dot + title */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: statusColor,
              flexShrink: 0,
              boxShadow: isBusy ? `0 0 8px ${statusColor}` : isError ? `0 0 6px var(--danger)` : "none",
              animation: isBusy ? "orgDotPulse 1.5s ease-in-out infinite" : undefined,
            }}
          />
          <span style={{
            fontSize: 13,
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
          }}>
            {data.role_title}
          </span>
          {(isClone || isEphemeral) && (
            <span style={{
              fontSize: 9,
              padding: "0 4px",
              borderRadius: 3,
              background: isEphemeral ? "#fef3c7" : "#e0f2fe",
              color: isEphemeral ? "#b45309" : "#0369a1",
              fontWeight: 500,
            }}>
              {isEphemeral ? "临时" : "副本"}
            </span>
          )}
        </div>

        {/* Goal preview */}
        {data.role_goal && (
          <div style={{
            fontSize: 10,
            color: "var(--muted)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            marginBottom: 4,
            maxWidth: 180,
          }}>
            {data.role_goal}
          </div>
        )}

        {/* Department + status tags */}
        <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
          {data.department && (
            <span style={{
              fontSize: 10,
              padding: "1px 6px",
              borderRadius: 4,
              background: `${deptColor}15`,
              color: deptColor,
              fontWeight: 500,
            }}>
              {data.department}
            </span>
          )}
          {data.status !== "idle" && (
            <span style={{
              fontSize: 10,
              padding: "1px 6px",
              borderRadius: 4,
              background: `${statusColor}15`,
              color: statusColor,
              fontWeight: 500,
            }}>
              {STATUS_LABELS[data.status] || data.status}
            </span>
          )}
        </div>

        {/* Frozen indicator */}
        {isFrozen && (
          <div style={{ fontSize: 10, color: "#93c5fd", marginTop: 4, display: "flex", alignItems: "center", gap: 3 }}>
            <IconSnowflake size={11} color="#93c5fd" />
            <span>{data.frozen_reason || "已冻结"}</span>
          </div>
        )}
      </div>

      <Handle type="source" position={Position.Bottom} style={{ background: "var(--primary)", width: 8, height: 8 }} />
    </div>
  );
}

const nodeTypes: NodeTypes = {
  orgNode: OrgNodeComponent as any,
};

// ── Main Component ──

export function OrgEditorView({
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
}) {
  useTranslation();

  // State
  const [orgList, setOrgList] = useState<OrgSummary[]>([]);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [currentOrg, setCurrentOrg] = useState<OrgFull | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [showNewNodeForm, setShowNewNodeForm] = useState(false);
  const [propsTab, setPropsTab] = useState<"basic" | "identity" | "capabilities" | "advanced" | "live">("basic");
  const [fullPromptPreview, setFullPromptPreview] = useState<string | null>(null);
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false);
  const [liveMode, setLiveMode] = useState(false);
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, string>>({});
  const [inboxOpen, setInboxOpen] = useState(false);
  const [nodeEvents, setNodeEvents] = useState<any[]>([]);
  const [nodeSchedules, setNodeSchedules] = useState<any[]>([]);

  // React Flow state
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([] as Edge[]);

  // MCP/Skill lists for selection
  const [availableMcpServers, setAvailableMcpServers] = useState<{ name: string; status: string }[]>([]);
  const [availableSkills, setAvailableSkills] = useState<{ name: string; description?: string; name_i18n?: string; description_i18n?: string }[]>([]);

  // Blackboard state
  const [bbEntries, setBbEntries] = useState<any[]>([]);
  const [bbScope, setBbScope] = useState<"all" | "org" | "department" | "node">("all");

  // Capabilities search
  const [mcpSearch, setMcpSearch] = useState("");
  const [skillSearch, setSkillSearch] = useState("");

  // Org settings panel collapse
  const [personaCollapsed, setPersonaCollapsed] = useState(false);
  const [bizCollapsed, setBizCollapsed] = useState(false);
  const [bbLoading, setBbLoading] = useState(false);

  // New node form
  const [newNodeTitle, setNewNodeTitle] = useState("");
  const [newNodeDept, setNewNodeDept] = useState("");
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768 || IS_CAPACITOR);
  const [showLeftPanel, setShowLeftPanel] = useState(() => !(window.innerWidth < 768 || IS_CAPACITOR));
  const [showRightPanel, setShowRightPanel] = useState(true);

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
      const flowNodes = data.nodes.map(orgNodeToFlowNode);
      const flowEdges = data.edges.map(orgEdgeToFlowEdge);
      const hasOverlap = detectOverlap(flowNodes);
      setNodes(hasOverlap ? computeTreeLayout(flowNodes, flowEdges) : flowNodes);
      setEdges(flowEdges);
      setSelectedNodeId(null);
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

  useEffect(() => {
    if (visible) {
      fetchOrgList();
      fetchTemplates();
      fetchMcpServers();
      fetchAvailableSkills();
    }
  }, [visible, fetchOrgList, fetchTemplates, fetchMcpServers, fetchAvailableSkills]);

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

  // ── WebSocket for live mode ──
  useEffect(() => {
    if (!liveMode || !currentOrg) return;
    const wsUrl = apiBaseUrl.replace(/^http/, "ws") + "/ws";
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          if (data.event === "org:node_status" && data.data?.org_id === currentOrg.id) {
            const { node_id, status } = data.data;
            setNodeStatuses((prev) => ({ ...prev, [node_id]: status }));
            setNodes((prev) =>
              prev.map((n) =>
                n.id === node_id
                  ? { ...n, data: { ...n.data, status } }
                  : n,
              ),
            );
          }
        } catch { /* ignore parse errors */ }
      };
    } catch { /* WebSocket not available */ }
    return () => { ws?.close(); };
  }, [liveMode, currentOrg, apiBaseUrl, setNodes]);

  // ── Start/Stop org ──
  const handleStartOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/start`, { method: "POST" });
      setCurrentOrg({ ...currentOrg, status: "active" });
      setLiveMode(true);
    } catch (e) { console.error("Failed to start org:", e); }
  }, [currentOrg, apiBaseUrl]);

  const handleStopOrg = useCallback(async () => {
    if (!currentOrg) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/stop`, { method: "POST" });
      setCurrentOrg({ ...currentOrg, status: "dormant" });
      setLiveMode(false);
    } catch (e) { console.error("Failed to stop org:", e); }
  }, [currentOrg, apiBaseUrl]);

  // ── Save ──

  const handleSave = useCallback(async () => {
    if (!currentOrg) return;
    setSaving(true);
    try {
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
      await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: currentOrg.name,
          description: currentOrg.description,
          user_persona: currentOrg.user_persona || { title: "负责人", display_name: "", description: "" },
          core_business: currentOrg.core_business || "",
          heartbeat_enabled: currentOrg.heartbeat_enabled,
          heartbeat_interval_s: currentOrg.heartbeat_interval_s,
          standup_enabled: currentOrg.standup_enabled,
          nodes: updatedNodes,
          edges: updatedEdges,
        }),
      });
      fetchOrgList();
    } catch (e) {
      console.error("Failed to save org:", e);
    } finally {
      setSaving(false);
    }
  }, [currentOrg, nodes, edges, apiBaseUrl, fetchOrgList]);

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
    const newNode: OrgNodeData = {
      id: `node_${Date.now().toString(36)}`,
      role_title: newNodeTitle.trim(),
      role_goal: "",
      role_backstory: "",
      agent_source: "local",
      agent_profile_id: null,
      position: { x: 250, y: 200 },
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
      status: "idle",
    };
    setNodes((prev) => [...prev, orgNodeToFlowNode(newNode)]);
    setNewNodeTitle("");
    setNewNodeDept("");
    setShowNewNodeForm(false);
  }, [currentOrg, newNodeTitle, newNodeDept, setNodes]);

  const handleDeleteNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((prev) => prev.filter((n) => n.id !== selectedNodeId));
    setEdges((prev) => prev.filter((e) => e.source !== selectedNodeId && e.target !== selectedNodeId));
    setSelectedNodeId(null);
  }, [selectedNodeId, setNodes, setEdges]);

  // ── Edge connection ──

  const onConnect: OnConnect = useCallback(
    (params: Connection) => {
      const newEdge: Edge = {
        id: `edge_${Date.now().toString(36)}`,
        source: params.source!,
        target: params.target!,
        type: "default",
        style: { stroke: EDGE_COLORS.hierarchy, strokeWidth: 2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS.hierarchy },
        data: {
          id: `edge_${Date.now().toString(36)}`,
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
    },
    [setEdges],
  );

  // ── Node click ──

  const onNodeClick = useCallback((_: any, node: Node) => {
    setSelectedNodeId(node.id);
    setPropsTab(liveMode ? "live" : "basic");
    setFullPromptPreview(null);
  }, [liveMode]);

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
  }, []);

  // ── Fetch node detail when selected in live mode ──
  useEffect(() => {
    if (!selectedNodeId || !currentOrg || !liveMode) {
      setNodeEvents([]);
      setNodeSchedules([]);
      return;
    }
    const fetchNodeDetail = async () => {
      try {
        const [eventsRes, schedulesRes] = await Promise.all([
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/events?actor=${selectedNodeId}&limit=20`),
          safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/nodes/${selectedNodeId}/schedules`),
        ]);
        if (eventsRes.ok) setNodeEvents(await eventsRes.json());
        if (schedulesRes.ok) setNodeSchedules(await schedulesRes.json());
      } catch (e) {
        console.error("Failed to fetch node detail:", e);
      }
    };
    fetchNodeDetail();
    const interval = setInterval(fetchNodeDetail, 15000);
    return () => clearInterval(interval);
  }, [selectedNodeId, currentOrg, liveMode, apiBaseUrl]);

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

  // ── Render ──

  if (!visible) return null;

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden", position: "relative" }}>
      {/* ── Left Panel: Org List ── */}
      {isMobile && showLeftPanel && (
        <div
          onClick={() => setShowLeftPanel(false)}
          style={{
            position: "absolute", inset: 0, zIndex: 49,
            background: "rgba(0,0,0,0.3)",
          }}
        />
      )}
      {showLeftPanel && (
      <div
        style={{
          width: isMobile ? "80%" : 240,
          maxWidth: isMobile ? 320 : 240,
          borderRight: isMobile ? "none" : "1px solid var(--line)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "var(--bg-app)",
          flexShrink: 0,
          position: isMobile ? "absolute" : "relative",
          zIndex: isMobile ? 50 : "auto",
          top: 0,
          left: 0,
          bottom: 0,
          boxShadow: isMobile ? "4px 0 12px rgba(0,0,0,0.15)" : "none",
        }}
      >
        <div style={{ padding: "12px 12px 8px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>组织编排</span>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <button className="btnSmall" onClick={() => setShowTemplates(!showTemplates)} title="从模板创建" style={{ fontSize: 11 }}>
              <IconClipboard size={12} />
            </button>
            <button className="btnSmall" onClick={handleCreateOrg} title="新建空白组织">
              <IconPlus size={12} />
            </button>
            {isMobile && (
              <button className="btnSmall" onClick={() => setShowLeftPanel(false)} title="关闭" style={{ minWidth: 36, minHeight: 36 }}>
                <IconX size={16} />
              </button>
            )}
          </div>
        </div>

        {/* Templates dropdown */}
        {showTemplates && (
          <div style={{ padding: "0 8px 8px" }}>
            <div className="card" style={{ padding: 8, fontSize: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>从模板创建</div>
              {templates.map((tpl) => (
                <div
                  key={tpl.id}
                  onClick={() => handleCreateFromTemplate(tpl.id)}
                  style={{
                    padding: "6px 8px",
                    borderRadius: "var(--radius-sm)",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 2,
                  }}
                  className="navItem"
                >
                  <span><IconBuilding size={14} /></span>
                  <div>
                    <div style={{ fontWeight: 500 }}>{tpl.name}</div>
                    <div style={{ fontSize: 10, color: "var(--muted)" }}>{tpl.node_count} 节点</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Org list */}
        <div style={{ flex: 1, overflowY: "auto", padding: "0 8px" }}>
          {orgList.length === 0 && (
            <div style={{ textAlign: "center", color: "var(--muted)", fontSize: 12, padding: 20 }}>
              暂无组织，点击上方创建
            </div>
          )}
          {orgList.map((org) => (
            <div
              key={org.id}
              onClick={() => { setSelectedOrgId(org.id); setShowLeftPanel(false); }}
              className={`navItem ${selectedOrgId === org.id ? "navItemActive" : ""}`}
              style={{
                padding: "8px 10px",
                marginBottom: 4,
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                position: "relative",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, overflow: "hidden" }}>
                <IconBuilding size={16} />
                <div style={{ overflow: "hidden" }}>
                  <div style={{ fontWeight: 500, fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {org.name}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--muted)" }}>
                    {org.node_count} 节点 · {org.status}
                  </div>
                </div>
              </div>
              <button
                className="btnSmall"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmDeleteOrgId(org.id);
                }}
                style={{ opacity: 0.5, fontSize: 10 }}
                title="删除组织"
              >
                <IconTrash size={10} />
              </button>
              {confirmDeleteOrgId === org.id && (
                <div
                  style={{
                    position: "absolute", right: 0, top: "100%", zIndex: 10,
                    background: "var(--bg-card, #fff)", border: "1px solid var(--line)",
                    borderRadius: 8, padding: "8px 10px", boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
                    display: "flex", gap: 6, alignItems: "center", fontSize: 11,
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <span>确认删除?</span>
                  <button className="btnSmall" onClick={() => handleDeleteOrg(org.id)} style={{ color: "var(--danger)", fontSize: 11 }}>删除</button>
                  <button className="btnSmall" onClick={() => setConfirmDeleteOrgId(null)} style={{ fontSize: 11 }}>取消</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      )}

      {/* ── Center: Canvas ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Toolbar */}
        {currentOrg && (
          <div
            style={{
              height: 44,
              borderBottom: "1px solid var(--line)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0 12px",
              background: "var(--bg-app)",
              flexShrink: 0,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button
                className="btnSmall"
                onClick={() => setShowLeftPanel(!showLeftPanel)}
                title="组织列表"
                style={{ fontSize: 14, minWidth: 32, minHeight: 32 }}
              >
                <IconMenu size={14} />
              </button>
              <IconBuilding size={16} />
              {!isMobile && (
              <input
                style={{
                  border: "none",
                  background: "transparent",
                  fontWeight: 600,
                  fontSize: 14,
                  outline: "none",
                  width: 200,
                  color: "var(--text)",
                }}
                value={currentOrg.name}
                onChange={(e) => setCurrentOrg({ ...currentOrg, name: e.target.value })}
              />
              )}
              <span
                style={{
                  fontSize: 10,
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: `${STATUS_COLORS[currentOrg.status] || "var(--muted)"}20`,
                  color: STATUS_COLORS[currentOrg.status] || "var(--muted)",
                  fontWeight: 600,
                }}
              >
                {currentOrg.status}
              </span>
            </div>

            <div style={{ display: "flex", gap: isMobile ? 4 : 6, alignItems: "center", overflowX: "auto", flexShrink: 1 }}>
              <button
                className="btnSmall"
                onClick={() => setShowNewNodeForm(true)}
                title="添加节点"
                style={{ minHeight: 36, whiteSpace: "nowrap" }}
              >
                <IconPlus size={12} /> {!isMobile && "添加节点"}
              </button>
              {selectedNodeId && (
                <button className="btnSmall" onClick={handleDeleteNode} title="删除选中节点" style={{ color: "var(--danger)", minHeight: 36 }}>
                  <IconTrash size={12} /> {!isMobile && "删除节点"}
                </button>
              )}
              <div style={{ width: 1, height: 20, background: "var(--line)" }} />
              {currentOrg.status === "archived" ? (
                <span style={{ fontSize: 11, color: "var(--muted)", padding: "4px 8px" }}>已归档</span>
              ) : currentOrg.status === "dormant" ? (
                <button className="btnSmall" onClick={handleStartOrg} style={{ color: "var(--ok)" }}>
                  <IconPlay size={12} /> 启动
                </button>
              ) : (
                <button className="btnSmall" onClick={handleStopOrg} style={{ color: "var(--danger)" }}>
                  <IconStop size={12} /> 停止
                </button>
              )}
              <button
                className="btnSmall"
                onClick={() => setLiveMode(!liveMode)}
                style={{
                  fontWeight: liveMode ? 600 : 400,
                  color: liveMode ? "var(--primary)" : undefined,
                  background: liveMode ? "rgba(14,165,233,0.1)" : undefined,
                }}
              >
                <IconRadar size={12} /> {!isMobile && "实况"}
              </button>
              <div style={{ width: 1, height: 20, background: "var(--line)" }} />
              <button
                className="btnSmall"
                onClick={handleSave}
                disabled={saving}
                style={{ fontWeight: 600 }}
              >
                <IconSave size={12} /> {saving ? "保存中..." : (!isMobile && "保存")}
              </button>
              {liveMode && currentOrg && (
                <>
                  <div style={{ width: 1, height: 20, background: "var(--line)" }} />
                  <button
                    className="btnSmall"
                    onClick={async () => {
                      try {
                        const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/heartbeat/trigger`, { method: "POST" });
                        if (!resp.ok) console.error("heartbeat trigger failed:", resp.status);
                      } catch (e) { console.error("heartbeat trigger error:", e); }
                    }}
                    title="手动触发心跳"
                  >
                    <IconHeartPulse size={12} /> {!isMobile && "心跳"}
                  </button>
                  <button
                    className="btnSmall"
                    onClick={async () => {
                      try {
                        const resp = await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/standup/trigger`, { method: "POST" });
                        if (!resp.ok) console.error("standup trigger failed:", resp.status);
                      } catch (e) { console.error("standup trigger error:", e); }
                    }}
                    title="手动触发晨会"
                  >
                    <IconSun size={12} /> {!isMobile && "晨会"}
                  </button>
                </>
              )}
              <div style={{ width: 1, height: 20, background: "var(--line)" }} />
              <button
                className="btnSmall"
                onClick={() => setInboxOpen(!inboxOpen)}
                style={{
                  fontWeight: inboxOpen ? 600 : 400,
                  color: inboxOpen ? "var(--primary)" : undefined,
                  background: inboxOpen ? "rgba(14,165,233,0.1)" : undefined,
                }}
              >
                <IconInbox size={12} /> {!isMobile && "消息"}
              </button>
              {canOpenPopupWindow() && (
              <button
                className="btnSmall"
                onClick={() => {
                  const base = window.location.href.split("#")[0].split("?")[0];
                  openPopupWindow(
                    `${base}#/org-editor`,
                    "org-editor-popup",
                    { width: 1400, height: 900, title: "组织编排" },
                  );
                }}
                title="在独立窗口中打开"
              >
                <IconMaximize2 size={12} />
              </button>
              )}
              {!isMobile && (
                <>
                  <div style={{ width: 1, height: 20, background: "var(--line)" }} />
                  <button
                    className="btnSmall"
                    onClick={() => setShowRightPanel(!showRightPanel)}
                    title={showRightPanel ? "收起设置面板" : "展开设置面板"}
                    style={{
                      fontWeight: showRightPanel ? 600 : 400,
                      color: showRightPanel ? "var(--primary)" : undefined,
                      background: showRightPanel ? "rgba(14,165,233,0.1)" : undefined,
                    }}
                  >
                    <IconChevronRight size={12} style={{ transform: showRightPanel ? "rotate(0deg)" : "rotate(180deg)", transition: "transform 0.15s" }} />
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {/* Add node dialog */}
        {showNewNodeForm && (
          <div
            style={{
              padding: 12,
              borderBottom: "1px solid var(--line)",
              background: "var(--bg-app)",
              display: "flex",
              gap: 8,
              alignItems: "center",
            }}
          >
            <input
              className="input"
              placeholder="岗位名称"
              value={newNodeTitle}
              onChange={(e) => setNewNodeTitle(e.target.value)}
              style={{ flex: 1, fontSize: 13 }}
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
            />
            <input
              className="input"
              placeholder="部门（可选）"
              value={newNodeDept}
              onChange={(e) => setNewNodeDept(e.target.value)}
              style={{ width: 120, fontSize: 13 }}
              onKeyDown={(e) => e.key === "Enter" && handleAddNode()}
            />
            <button className="btnSmall" onClick={handleAddNode}>
              <IconCheck size={12} />
            </button>
            <button className="btnSmall" onClick={() => setShowNewNodeForm(false)}>
              <IconX size={12} />
            </button>
          </div>
        )}

        {/* React Flow canvas */}
        {currentOrg ? (
          <div style={{ flex: 1 }}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={onNodeClick}
              onPaneClick={onPaneClick}
              nodeTypes={nodeTypes}
              fitView
              snapToGrid
              snapGrid={[20, 20]}
              nodesDraggable={!liveMode}
              nodesConnectable={!liveMode}
              defaultEdgeOptions={{
                type: "default",
                style: { strokeWidth: 2 },
              }}
              style={{ background: "var(--bg-app)" }}
            >
              <Background gap={20} size={1} color="var(--line)" />
              <Controls position="bottom-left" />
              {!isMobile && (
              <MiniMap
                nodeStrokeWidth={2}
                pannable
                zoomable
                style={{ background: "var(--bg-card, #fff)" }}
              />
              )}
              {!isMobile && (
              <Panel position="bottom-right">
                <div style={{ background: "var(--bg-card, #fff)", padding: "6px 10px", borderRadius: "var(--radius-sm)", fontSize: 10, border: "1px solid var(--line)" }}>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    {Object.entries(EDGE_COLORS).map(([type, color]) => (
                      <span key={type} style={{ display: "flex", alignItems: "center", gap: 3 }}>
                        <span style={{ display: "inline-block", width: 16, height: 2, background: color, borderRadius: 1 }} />
                        {type === "hierarchy" ? "上下级" : type === "collaborate" ? "协作" : type === "escalate" ? "上报" : "咨询"}
                      </span>
                    ))}
                  </div>
                </div>
              </Panel>
              )}
            </ReactFlow>
          </div>
        ) : (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}
            onClick={() => { if (isMobile) setShowLeftPanel(true); }}
          >
            <div style={{ textAlign: "center" }}>
              <IconUsers size={48} />
              <p style={{ marginTop: 12, fontSize: 14 }}>
                {isMobile ? "点击打开组织列表" : "选择或创建一个组织开始编排"}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* ── Right Panel: Node Properties ── */}
      {isMobile && selectedNode && showRightPanel && (
        <div
          onClick={() => setSelectedNodeId(null)}
          style={{
            position: "absolute", inset: 0, zIndex: 49,
            background: "rgba(0,0,0,0.3)",
          }}
        />
      )}
      {selectedNode && showRightPanel && (
        <div
          style={{
            width: isMobile ? "85%" : 300,
            maxWidth: isMobile ? 360 : 300,
            borderLeft: isMobile ? "none" : "1px solid var(--line)",
            overflowY: "auto",
            background: "var(--bg-app)",
            position: isMobile ? "absolute" : "relative",
            right: 0,
            top: 0,
            bottom: 0,
            zIndex: isMobile ? 50 : "auto",
            boxShadow: isMobile ? "-4px 0 12px rgba(0,0,0,0.15)" : "none",
            flexShrink: 0,
          }}
        >
          <div style={{ padding: "12px 12px 8px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{selectedNode.role_title}</div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>{selectedNode.department || "未分配部门"}</div>
            </div>
            {isMobile && (
              <button className="btnSmall" onClick={() => setSelectedNodeId(null)} style={{ minWidth: 36, minHeight: 36 }}><IconX size={14} /></button>
            )}
          </div>

          {/* Tabs */}
          <div style={{ display: "flex", borderBottom: "1px solid var(--line)" }}>
            {(liveMode
              ? (["live", "basic", "identity", "capabilities", "advanced"] as const)
              : (["basic", "identity", "capabilities", "advanced"] as const)
            ).map((tab) => (
              <button
                key={tab}
                onClick={() => setPropsTab(tab)}
                style={{
                  flex: 1,
                  padding: "8px 4px",
                  fontSize: 11,
                  fontWeight: propsTab === tab ? 600 : 400,
                  color: propsTab === tab ? "var(--primary)" : "var(--muted)",
                  background: "transparent",
                  border: "none",
                  borderBottomWidth: 2,
                  borderBottomStyle: "solid",
                  borderBottomColor: propsTab === tab ? "var(--primary)" : "transparent",
                  cursor: "pointer",
                }}
              >
                {tab === "live" ? "实况" : tab === "basic" ? "基本" : tab === "identity" ? "身份" : tab === "capabilities" ? "能力" : "高级"}
              </button>
            ))}
          </div>

          <div style={{ padding: 12 }}>
            {propsTab === "live" && liveMode && (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {/* Node status summary */}
                <div className="card" style={{ padding: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>节点状态</div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <span style={{
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 4,
                      background: `${STATUS_COLORS[selectedNode.status] || "var(--muted)"}20`,
                      color: STATUS_COLORS[selectedNode.status] || "var(--muted)",
                      fontWeight: 500,
                    }}>
                      {STATUS_LABELS[selectedNode.status] || selectedNode.status}
                    </span>
                    {selectedNode.is_clone && <span style={{ fontSize: 10, color: "#0369a1" }}>副本</span>}
                    {selectedNode.ephemeral && <span style={{ fontSize: 10, color: "#b45309" }}>临时</span>}
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
                          <span style={{
                            fontSize: 10,
                            padding: "1px 5px",
                            borderRadius: 3,
                            background: s.enabled ? "#dcfce7" : "#f3f4f6",
                            color: s.enabled ? "#166534" : "#9ca3af",
                          }}>
                            {s.enabled ? "启用" : "禁用"}
                          </span>
                        </div>
                        {s.last_run_at && (
                          <div style={{ fontSize: 10, color: "#9ca3af", marginTop: 2 }}>
                            上次: {s.last_run_at.slice(0, 19).replace("T", " ")}
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
                              {evt.timestamp?.slice(11, 19)}
                            </span>
                          </div>
                          {evt.data && Object.keys(evt.data).length > 0 && (
                            <div style={{ fontSize: 10, color: "#6b7280", marginTop: 2, marginLeft: 12 }}>
                              {Object.entries(evt.data).slice(0, 2).map(([k, v]) => (
                                <span key={k} style={{ marginRight: 8 }}>
                                  {k}: {String(v).slice(0, 40)}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {propsTab === "basic" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>岗位名称</label>
                <input
                  className="input"
                  value={selectedNode.role_title}
                  onChange={(e) => updateNodeData("role_title", e.target.value)}
                  placeholder="如：技术总监、前端工程师、QA 负责人"
                  style={{ fontSize: 13 }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                  岗位目标
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— 这个岗位要达成什么</span>
                </label>
                <textarea
                  className="input"
                  value={selectedNode.role_goal}
                  onChange={(e) => updateNodeData("role_goal", e.target.value)}
                  rows={2}
                  placeholder="如：负责整体技术架构设计，把控代码质量，推进技术选型和落地"
                  style={{ fontSize: 13, resize: "vertical" }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                  角色背景
                  <span style={{ fontWeight: 400, marginLeft: 6 }}>— 专业经验和能力特长</span>
                </label>
                <textarea
                  className="input"
                  value={selectedNode.role_backstory}
                  onChange={(e) => updateNodeData("role_backstory", e.target.value)}
                  rows={3}
                  placeholder="如：10年全栈开发经验，精通 Python/TypeScript，熟悉微服务架构，曾主导多个大型项目的技术选型"
                  style={{ fontSize: 13, resize: "vertical" }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>部门</label>
                <input
                  className="input"
                  value={selectedNode.department}
                  onChange={(e) => updateNodeData("department", e.target.value)}
                  style={{ fontSize: 13 }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>层级</label>
                <input
                  className="input"
                  type="number"
                  min={0}
                  value={selectedNode.level}
                  onChange={(e) => updateNodeData("level", parseInt(e.target.value) || 0)}
                  style={{ fontSize: 13, width: 80 }}
                />
                <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>Agent 来源</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <select
                    className="input"
                    value={selectedNode.agent_source.startsWith("ref:") ? "ref" : "local"}
                    onChange={(e) => updateNodeData("agent_source", e.target.value === "local" ? "local" : `ref:${selectedNode.agent_profile_id || ""}`)}
                    style={{ fontSize: 13, flex: 1 }}
                  >
                    <option value="local">本地专属</option>
                    <option value="ref">引用已有 Agent</option>
                  </select>
                </div>
                {selectedNode.agent_source.startsWith("ref:") && (
                  <>
                    <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>Agent Profile ID</label>
                    <input
                      className="input"
                      value={selectedNode.agent_profile_id || ""}
                      onChange={(e) => {
                        updateNodeData("agent_profile_id", e.target.value || null);
                        updateNodeData("agent_source", `ref:${e.target.value}`);
                      }}
                      placeholder="profile_id"
                      style={{ fontSize: 13 }}
                    />
                  </>
                )}
              </div>
            )}

            {propsTab === "identity" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {/* Section 1: Field relationship */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 6 }}>
                    提示词构成说明
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.7 }}>
                    <div>系统会自动将以下信息拼装为完整的角色提示词：</div>
                    <div style={{ marginTop: 4, paddingLeft: 8 }}>
                      <div>1. <b>岗位名称 / 目标 / 背景</b>（基本 tab）— 自动生成角色描述</div>
                      <div>2. <b>自定义提示词</b>（下方）— 覆盖自动生成，精细控制</div>
                      <div>3. <b>组织上下文</b>（自动注入）— 架构、关系、权限、黑板</div>
                    </div>
                    <div style={{ marginTop: 6 }}>
                      优先级：ROLE.md 文件 &gt; 自定义提示词 &gt; AgentProfile &gt; 自动生成
                    </div>
                  </div>
                </div>

                {/* Section 2: Custom prompt */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                      自定义提示词
                    </div>
                    <button
                      className="btnSmall"
                      style={{ fontSize: 10, padding: "2px 8px" }}
                      onClick={() => {
                        if (selectedNode.custom_prompt && !confirm("将覆盖当前自定义提示词，确认？")) return;
                        const tpl = `你是一位经验丰富的${selectedNode.role_title || "专业人员"}。\n\n## 核心职责\n- ${selectedNode.role_goal || "待定义"}\n\n## 工作风格\n- 沟通简洁高效，结论先行\n- 重要决策写入组织黑板\n- 主动向上级汇报进展\n\n## 专业背景\n${selectedNode.role_backstory || "请在此描述角色的专业背景、经验和能力特长"}`;
                        updateNodeData("custom_prompt", tpl);
                      }}
                    >
                      填充模板
                    </button>
                  </div>
                  <textarea
                    className="input"
                    value={selectedNode.custom_prompt}
                    onChange={(e) => updateNodeData("custom_prompt", e.target.value)}
                    rows={10}
                    placeholder={"可选。不填写时系统将根据岗位名称、目标、背景自动生成角色描述。\n\n填写后将替代自动生成的内容，可更精细地控制角色行为。\n\n示例：\n你是一位资深前端工程师，擅长 React/Vue...\n\n## 核心职责\n- 负责前端架构设计和代码审查\n- 协调前端团队的开发进度"}
                    style={{ fontSize: 12, resize: "vertical", fontFamily: "monospace", lineHeight: 1.5, minHeight: 120 }}
                  />
                  <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                    {selectedNode.custom_prompt
                      ? `已配置自定义提示词（${selectedNode.custom_prompt.length} 字符）`
                      : `未配置。系统将自动生成："你是${selectedNode.role_title || "..."}。目标：${selectedNode.role_goal ? selectedNode.role_goal.slice(0, 20) + "..." : "..."}"`}
                  </div>
                </div>

                {/* Section 3: Prompt preview */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                      提示词预览
                    </div>
                    <div style={{ display: "flex", gap: 4 }}>
                      {fullPromptPreview !== null && (
                        <button
                          className="btnSmall"
                          style={{ fontSize: 10, padding: "2px 8px" }}
                          onClick={() => setFullPromptPreview(null)}
                        >
                          简略
                        </button>
                      )}
                      <button
                        className="btnSmall"
                        style={{ fontSize: 10, padding: "2px 8px" }}
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
                      </button>
                    </div>
                  </div>
                  <div style={{
                    fontSize: 11, color: "var(--fg)", lineHeight: 1.6,
                    background: "var(--bg-code, #f5f5f5)", borderRadius: 6,
                    padding: "8px 10px", maxHeight: 300, overflowY: "auto",
                    fontFamily: "monospace", whiteSpace: "pre-wrap",
                  }}>
                    {fullPromptPreview !== null
                      ? fullPromptPreview
                      : selectedNode.custom_prompt
                        ? selectedNode.custom_prompt
                        : `你是${selectedNode.role_title || "(未设置岗位名称)"}。${selectedNode.role_goal ? `目标：${selectedNode.role_goal}。` : ""}${selectedNode.role_backstory ? `背景：${selectedNode.role_backstory}。` : ""}`}
                  </div>
                  {fullPromptPreview === null && (
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                      以上为角色描述部分。点击「完整预览」查看含组织架构、关系、权限等的完整提示词。
                    </div>
                  )}
                  {fullPromptPreview !== null && (
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                      以上为运行时注入给 LLM 的完整组织上下文提示词（{fullPromptPreview.length} 字符）
                    </div>
                  )}
                </div>

                {/* Section 4: Identity files info */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 4 }}>
                    高级：身份文件
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
                    如需更精细的身份控制，可在组织目录下创建节点专属身份文件：
                    <div style={{ fontFamily: "monospace", fontSize: 10, marginTop: 4, paddingLeft: 8 }}>
                      <div>nodes/{selectedNode.id}/identity/ROLE.md — 角色定义</div>
                      <div>nodes/{selectedNode.id}/identity/AGENT.md — 覆盖全局 Agent 人格</div>
                      <div>nodes/{selectedNode.id}/identity/SOUL.md — 覆盖全局核心价值观</div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {propsTab === "capabilities" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>

                {/* ── Section 1: 执行工具类目 ── */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8,
                  background: "var(--bg-card, #fff)", overflow: "hidden",
                }}>
                  <div style={{
                    padding: "8px 10px", borderBottom: "1px solid var(--line)",
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                  }}>
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 600 }}>执行工具</div>
                      <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 2 }}>
                        未选择时只能使用组织协作工具
                      </div>
                    </div>
                    <button
                      className="btnSmall"
                      style={{ fontSize: 10, padding: "2px 8px", flexShrink: 0 }}
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
                        updateNodeData("external_tools", preset);
                      }}
                      title="根据岗位角色自动推荐工具"
                    >
                      自动推荐
                    </button>
                  </div>
                  <div style={{ padding: 4, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2 }}>
                    {[
                      { key: "research", label: "搜索", icon: "🔍" },
                      { key: "planning", label: "计划", icon: "📋" },
                      { key: "filesystem", label: "文件/命令", icon: "📁" },
                      { key: "memory", label: "记忆", icon: "🧠" },
                      { key: "browser", label: "浏览器", icon: "🌐" },
                      { key: "communication", label: "通信", icon: "📨" },
                    ].map((cat) => {
                      const checked = (selectedNode.external_tools || []).includes(cat.key);
                      return (
                        <label
                          key={cat.key}
                          style={{
                            display: "flex", alignItems: "center", gap: 6,
                            padding: "5px 8px", borderRadius: 6, cursor: "pointer",
                            fontSize: 11,
                            background: checked ? "rgba(14,165,233,0.1)" : "transparent",
                            transition: "background 0.15s",
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              const cur = selectedNode.external_tools || [];
                              const next = checked
                                ? cur.filter((s: string) => s !== cat.key)
                                : [...cur, cat.key];
                              updateNodeData("external_tools", next);
                            }}
                            style={{ accentColor: "var(--primary)", flexShrink: 0, width: 14, height: 14 }}
                          />
                          <span>{cat.icon} {cat.label}</span>
                        </label>
                      );
                    })}
                  </div>
                </div>

                {/* ── Section 2: MCP 服务器 ── */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8,
                  background: "var(--bg-card, #fff)", overflow: "hidden",
                }}>
                  <div style={{
                    padding: "8px 10px", borderBottom: "1px solid var(--line)",
                  }}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>MCP 服务器</div>
                    <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 2 }}>
                      节点可调用的外部服务接口
                    </div>
                  </div>
                  {availableMcpServers.length > 3 && (
                    <div style={{ padding: "4px 6px 0" }}>
                      <input
                        className="input"
                        placeholder="搜索服务器..."
                        value={mcpSearch}
                        onChange={(e) => setMcpSearch(e.target.value)}
                        style={{ fontSize: 11, width: "100%", padding: "4px 8px" }}
                      />
                    </div>
                  )}
                  {availableMcpServers.length > 0 ? (
                    <div style={{ padding: 4, maxHeight: 150, overflowY: "auto" }}>
                      {availableMcpServers
                        .filter((srv) => !mcpSearch || srv.name.toLowerCase().includes(mcpSearch.toLowerCase()))
                        .map((srv) => {
                        const checked = selectedNode.mcp_servers.includes(srv.name);
                        return (
                          <label
                            key={srv.name}
                            style={{
                              display: "flex", alignItems: "center", gap: 6,
                              padding: "5px 8px", borderRadius: 6, cursor: "pointer",
                              fontSize: 11,
                              background: checked ? "rgba(14,165,233,0.1)" : "transparent",
                              transition: "background 0.15s",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                const next = checked
                                  ? selectedNode.mcp_servers.filter((s: string) => s !== srv.name)
                                  : [...selectedNode.mcp_servers, srv.name];
                                updateNodeData("mcp_servers", next);
                              }}
                              style={{ accentColor: "var(--primary)", flexShrink: 0, width: 14, height: 14 }}
                            />
                            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {srv.name}
                            </span>
                            <span style={{
                              fontSize: 9, padding: "1px 5px", borderRadius: 3, flexShrink: 0,
                              background: srv.status === "connected" ? "#dcfce7" : "#f3f4f6",
                              color: srv.status === "connected" ? "#166534" : "#9ca3af",
                            }}>
                              {srv.status === "connected" ? "在线" : "离线"}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  ) : (
                    <div style={{ fontSize: 10, color: "var(--muted)", padding: "10px" }}>
                      暂无可用服务器
                    </div>
                  )}
                  {selectedNode.mcp_servers.length > 0 && (
                    <div style={{ fontSize: 9, color: "var(--muted)", padding: "2px 10px 6px", borderTop: "1px solid var(--line)" }}>
                      已选 {selectedNode.mcp_servers.length} 个
                    </div>
                  )}
                </div>

                {/* ── Section 3: 技能 ── */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8,
                  background: "var(--bg-card, #fff)", overflow: "hidden",
                }}>
                  <div style={{
                    padding: "8px 10px", borderBottom: "1px solid var(--line)",
                  }}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>技能</div>
                    <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 2 }}>
                      已安装的专业技能包
                    </div>
                  </div>
                  {availableSkills.length > 3 && (
                    <div style={{ padding: "4px 6px 0" }}>
                      <input
                        className="input"
                        placeholder="搜索技能..."
                        value={skillSearch}
                        onChange={(e) => setSkillSearch(e.target.value)}
                        style={{ fontSize: 11, width: "100%", padding: "4px 8px" }}
                      />
                    </div>
                  )}
                  {availableSkills.length > 0 ? (
                    <div style={{ padding: 4, maxHeight: 150, overflowY: "auto" }}>
                      {availableSkills
                        .filter((skill) => {
                          if (!skillSearch) return true;
                          const q = skillSearch.toLowerCase();
                          return (skill.name_i18n || "").toLowerCase().includes(q)
                            || skill.name.toLowerCase().includes(q)
                            || (skill.description_i18n || "").toLowerCase().includes(q)
                            || (skill.description || "").toLowerCase().includes(q);
                        })
                        .map((skill) => {
                        const checked = selectedNode.skills.includes(skill.name);
                        const displayName = skill.name_i18n || skill.name;
                        const displayDesc = skill.description_i18n || skill.description || "";
                        return (
                          <label
                            key={skill.name}
                            style={{
                              display: "flex", alignItems: "flex-start", gap: 6,
                              padding: "5px 8px", borderRadius: 6, cursor: "pointer",
                              fontSize: 11,
                              background: checked ? "rgba(14,165,233,0.1)" : "transparent",
                              transition: "background 0.15s",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                const next = checked
                                  ? selectedNode.skills.filter((s: string) => s !== skill.name)
                                  : [...selectedNode.skills, skill.name];
                                updateNodeData("skills", next);
                              }}
                              style={{ accentColor: "var(--primary)", flexShrink: 0, width: 14, height: 14, marginTop: 2 }}
                            />
                            <div style={{ flex: 1, overflow: "hidden" }}>
                              <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {displayName}
                              </div>
                              {displayDesc && (
                                <div style={{ fontSize: 9, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {displayDesc}
                                </div>
                              )}
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  ) : (
                    <div style={{ fontSize: 10, color: "var(--muted)", padding: "10px" }}>
                      暂无可用技能
                    </div>
                  )}
                  {selectedNode.skills.length > 0 && (
                    <div style={{ fontSize: 9, color: "var(--muted)", padding: "2px 10px 6px", borderTop: "1px solid var(--line)" }}>
                      已选 {selectedNode.skills.length} 个
                    </div>
                  )}
                </div>

                {/* ── 需要启用 MCP 工具类目提示 ── */}
                {selectedNode.mcp_servers.length > 0 && !(selectedNode.external_tools || []).includes("mcp") && (
                  <div style={{
                    fontSize: 10, color: "#b45309", background: "#fffbeb",
                    padding: "6px 10px", borderRadius: 6, border: "1px solid #fde68a",
                    lineHeight: 1.5,
                  }}>
                    已选择 MCP 服务器但未启用"搜索"等工具类目中的 MCP 调用能力。
                    <button
                      className="btnSmall"
                      style={{ fontSize: 10, marginLeft: 4, padding: "1px 6px", verticalAlign: "middle" }}
                      onClick={() => {
                        const cur = selectedNode.external_tools || [];
                        if (!cur.includes("mcp")) updateNodeData("external_tools", [...cur, "mcp"]);
                      }}
                    >
                      一键启用
                    </button>
                  </div>
                )}
              </div>
            )}

            {propsTab === "advanced" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {/* Performance section */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 8 }}>
                    性能限制
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    <div>
                      <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 3 }}>并行任务数</div>
                      <input
                        className="input"
                        type="number"
                        min={1}
                        value={selectedNode.max_concurrent_tasks}
                        onChange={(e) => updateNodeData("max_concurrent_tasks", parseInt(e.target.value) || 1)}
                        style={{ fontSize: 12, width: "100%" }}
                      />
                    </div>
                    <div>
                      <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 3 }}>超时 (秒)</div>
                      <input
                        className="input"
                        type="number"
                        min={30}
                        value={selectedNode.timeout_s}
                        onChange={(e) => updateNodeData("timeout_s", parseInt(e.target.value) || 300)}
                        style={{ fontSize: 12, width: "100%" }}
                      />
                    </div>
                  </div>
                </div>

                {/* Auto-clone section */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
                      自动分身
                    </div>
                    <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        checked={selectedNode.auto_clone_enabled || false}
                        onChange={(e) => updateNodeData("auto_clone_enabled", e.target.checked)}
                      />
                      <span style={{ fontSize: 10, color: "var(--muted)" }}>启用</span>
                    </label>
                  </div>
                  {selectedNode.auto_clone_enabled && (
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <div>
                        <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 3 }}>触发阈值（待处理数）</div>
                        <input
                          className="input"
                          type="number"
                          min={2}
                          value={selectedNode.auto_clone_threshold || 3}
                          onChange={(e) => updateNodeData("auto_clone_threshold", parseInt(e.target.value) || 3)}
                          style={{ fontSize: 12, width: "100%" }}
                        />
                      </div>
                      <div>
                        <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 3 }}>最大分身数</div>
                        <input
                          className="input"
                          type="number"
                          min={1}
                          max={5}
                          value={selectedNode.auto_clone_max || 3}
                          onChange={(e) => updateNodeData("auto_clone_max", parseInt(e.target.value) || 3)}
                          style={{ fontSize: 12, width: "100%" }}
                        />
                      </div>
                    </div>
                  )}
                  <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 6, lineHeight: 1.5 }}>
                    任务堆积超过阈值时自动创建分身处理。分身共享岗位记忆，同一任务链由同一分身完成。空闲分身在心跳时自动回收。
                  </div>
                </div>

                {/* Permissions section */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 8 }}>
                    权限控制
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 12px" }}>
                    {([
                      { key: "can_delegate", label: "委派任务" },
                      { key: "can_escalate", label: "上报问题" },
                      { key: "can_request_scaling", label: "申请扩编" },
                      { key: "ephemeral", label: "临时节点" },
                    ] as const).map(({ key, label }) => (
                      <label
                        key={key}
                        style={{
                          display: "flex", alignItems: "center", gap: 6,
                          fontSize: 12, padding: "4px 6px", borderRadius: 6,
                          cursor: "pointer",
                          background: selectedNode[key] ? "rgba(14,165,233,0.06)" : "transparent",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={selectedNode[key]}
                          onChange={(e) => updateNodeData(key, e.target.checked)}
                          style={{ accentColor: "var(--primary)", flexShrink: 0 }}
                        />
                        <span style={{ whiteSpace: "nowrap" }}>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                {/* LLM endpoint */}
                <div style={{
                  border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px",
                  background: "var(--bg-card, #fff)",
                }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", marginBottom: 6 }}>
                    LLM 端点偏好
                  </div>
                  <input
                    className="input"
                    value={selectedNode.preferred_endpoint || ""}
                    onChange={(e) => updateNodeData("preferred_endpoint", e.target.value || null)}
                    placeholder="留空使用默认端点"
                    style={{ fontSize: 12, width: "100%" }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Right Panel: Org Settings (when no node selected) ── */}
      {currentOrg && !selectedNode && !isMobile && showRightPanel && (
        <div
          style={{
            width: 300,
            borderLeft: "1px solid var(--line)",
            overflowY: "auto",
            background: "var(--bg-app)",
            flexShrink: 0,
            padding: 12,
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>组织设置</div>

          {/* ── 核心业务 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setBizCollapsed(!bizCollapsed)}
            >
              <div style={{ fontWeight: 600, fontSize: 12 }}>
                核心业务
                {bizCollapsed && (currentOrg.core_business || "").trim() && (
                  <span style={{ fontWeight: 400, fontSize: 10, color: "var(--ok)", marginLeft: 6 }}>已配置</span>
                )}
              </div>
              <span style={{ fontSize: 10, color: "var(--muted)" }}>{bizCollapsed ? "▸" : "▾"}</span>
            </div>
            {!bizCollapsed && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 6, lineHeight: 1.5 }}>
                  填写后组织启动即自主运转——顶层负责人自动接收任务书并开始工作，心跳变为定期复盘。
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
                  {[
                    { label: "创业公司", tpl: "## 业务定位\n我们是一家___公司，核心产品/服务是___。\n\n## 当前阶段目标\n- 完成产品 MVP 并上线\n- 获取首批 100 个种子用户\n- 验证产品-市场匹配度\n\n## 工作策略\n- 产品优先：先打磨核心功能，再扩展\n- 精益运营：小规模验证后再投入推广资源\n- 数据驱动：关注用户留存率和活跃度\n\n## 主动运营要求\n负责人需持续推进：产品开发进度跟踪、市场调研执行、用户反馈收集与分析、团队任务协调。每个复盘周期应有可交付成果。" },
                    { label: "内容运营", tpl: "## 业务定位\n面向___领域的内容创作与分发平台/账号。\n\n## 当前阶段目标\n- 建立稳定的内容生产流程（每周___篇）\n- 核心平台粉丝/订阅达到___\n- 形成可复制的爆款内容方法论\n\n## 工作策略\n- 选题驱动：每周策划会确定选题方向\n- 数据复盘：分析每篇内容的阅读/互动数据\n- 持续迭代：根据数据调整内容策略\n\n## 主动运营要求\n负责人需持续推进：选题策划与分配、内容质量把控、发布排期管理、数据复盘与策略调整。确保内容产出不中断。" },
                    { label: "软件项目", tpl: "## 项目定位\n为___开发的___系统/应用。\n\n## 当前阶段目标\n- 完成___模块的开发与测试\n- 交付可演示的版本给___\n- 技术文档同步更新\n\n## 工作策略\n- 迭代开发：按优先级排列功能，每轮迭代2周\n- 质量保障：代码审查 + 自动化测试覆盖\n- 文档先行：关键架构决策必须文档化\n\n## 主动运营要求\n负责人需持续推进：任务拆解与分配、代码审查、进度跟踪、阻塞问题排除、与需求方沟通确认。" },
                    { label: "研究课题", tpl: "## 课题方向\n研究___领域的___问题。\n\n## 当前阶段目标\n- 完成文献调研，形成研究综述\n- 确定研究方案和实验设计\n- 产出阶段性研究报告\n\n## 工作策略\n- 文献先行：系统梳理相关领域进展\n- 实验验证：设计对照实验验证假设\n- 定期交流：团队内部周会分享进展\n\n## 主动运营要求\n负责人需持续推进：文献调研分配、研究方案讨论、实验进度追踪、成果整理与汇报。" },
                    { label: "电商运营", tpl: "## 业务定位\n面向___的___品类电商。\n\n## 当前阶段目标\n- 完成店铺搭建和首批___个 SKU 上架\n- 月销售额达到___\n- 建立稳定的供应链和客服流程\n\n## 工作策略\n- 选品驱动：通过市场分析确定主推品类\n- 流量获取：___平台引流 + 内容营销\n- 复购优先：客户满意度和复购率是核心指标\n\n## 主动运营要求\n负责人需持续推进：选品调研、供应链管理、营销活动策划执行、客户反馈处理、数据分析与策略调整。确保日常运营不中断。" },
                  ].map((tpl) => (
                    <button
                      key={tpl.label}
                      className="btnSmall"
                      style={{ fontSize: 10, padding: "2px 7px" }}
                      onClick={() => {
                        if ((currentOrg.core_business || "").trim() && !confirm("将覆盖当前内容，确认？")) return;
                        setCurrentOrg({ ...currentOrg, core_business: tpl.tpl });
                      }}
                    >
                      {tpl.label}
                    </button>
                  ))}
                </div>
                <textarea
                  className="input"
                  style={{ width: "100%", fontSize: 11, minHeight: 120, resize: "vertical", lineHeight: 1.6, fontFamily: "inherit" }}
                  placeholder={"填写或选择模板后编辑。\n\n组织启动后，顶层节点将根据此内容自动制定策略、分配任务、持续推进。"}
                  value={currentOrg.core_business || ""}
                  onChange={(e) => setCurrentOrg({ ...currentOrg, core_business: e.target.value })}
                />
                {(currentOrg.core_business || "").trim() && (
                  <div style={{ fontSize: 9, color: "var(--ok)", marginTop: 4 }}>
                    启动组织后，顶层负责人将自动接收任务书并开始自主运营
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── 用户身份 ── */}
          <div className="card" style={{ padding: 10, marginBottom: 10 }}>
            <div
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
              onClick={() => setPersonaCollapsed(!personaCollapsed)}
            >
              <div style={{ fontWeight: 600, fontSize: 12 }}>
                用户身份
                {currentOrg.user_persona?.title && (
                  <span style={{ fontWeight: 400, fontSize: 10, color: "var(--muted)", marginLeft: 6 }}>
                    {currentOrg.user_persona.display_name || currentOrg.user_persona.title}
                  </span>
                )}
              </div>
              <span style={{ fontSize: 10, color: "var(--muted)" }}>{personaCollapsed ? "▸" : "▾"}</span>
            </div>
            {!personaCollapsed && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 6, lineHeight: 1.5 }}>
                  你在本组织中的角色。节点会以此身份认知你。
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
                  {[
                    { title: "董事长", desc: "组织最高决策者" },
                    { title: "产品负责人", desc: "项目需求方与最终验收人" },
                    { title: "出品人", desc: "内容方向决策者" },
                    { title: "投资人", desc: "外部投资方" },
                    { title: "甲方", desc: "项目委托方" },
                    { title: "课题负责人", desc: "研究课题主持人" },
                  ].map((preset) => (
                    <button
                      key={preset.title}
                      className="btnSmall"
                      style={{
                        fontSize: 10, padding: "2px 7px",
                        background: currentOrg.user_persona?.title === preset.title ? "var(--primary)" : undefined,
                        color: currentOrg.user_persona?.title === preset.title ? "#fff" : undefined,
                      }}
                      onClick={() => setCurrentOrg({
                        ...currentOrg,
                        user_persona: { title: preset.title, display_name: preset.title, description: preset.desc },
                      })}
                    >
                      {preset.title}
                    </button>
                  ))}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  <div style={{ display: "flex", gap: 6 }}>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: 9, color: "var(--muted)", display: "block", marginBottom: 1 }}>头衔</label>
                      <input
                        className="input"
                        style={{ width: "100%", fontSize: 11 }}
                        placeholder="董事长"
                        value={currentOrg.user_persona?.title || ""}
                        onChange={(e) => setCurrentOrg({
                          ...currentOrg,
                          user_persona: { ...currentOrg.user_persona, title: e.target.value, display_name: currentOrg.user_persona?.display_name || "", description: currentOrg.user_persona?.description || "" },
                        })}
                      />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: 9, color: "var(--muted)", display: "block", marginBottom: 1 }}>显示名</label>
                      <input
                        className="input"
                        style={{ width: "100%", fontSize: 11 }}
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
                    <label style={{ fontSize: 9, color: "var(--muted)", display: "block", marginBottom: 1 }}>简介</label>
                    <input
                      className="input"
                      style={{ width: "100%", fontSize: 11 }}
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

          {/* ── Blackboard ── */}
          <div style={{ marginTop: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>组织黑板</div>
              <button
                className="btnSmall"
                style={{ fontSize: 10, padding: "2px 8px" }}
                onClick={() => fetchBlackboard(currentOrg.id, bbScope)}
                disabled={bbLoading}
              >
                {bbLoading ? "..." : "刷新"}
              </button>
            </div>

            <div style={{ display: "flex", gap: 2, marginBottom: 8 }}>
              {([
                { key: "all", label: "全部" },
                { key: "org", label: "组织级" },
                { key: "department", label: "部门级" },
                { key: "node", label: "节点级" },
              ] as const).map((s) => (
                <button
                  key={s.key}
                  className="btnSmall"
                  style={{
                    fontSize: 10, padding: "2px 6px",
                    fontWeight: bbScope === s.key ? 600 : 400,
                    background: bbScope === s.key ? "var(--primary)" : "transparent",
                    color: bbScope === s.key ? "#fff" : "var(--muted)",
                    borderRadius: 4,
                  }}
                  onClick={() => setBbScope(s.key)}
                >
                  {s.label}
                </button>
              ))}
            </div>

            {bbEntries.length === 0 ? (
              <div style={{
                fontSize: 11, color: "var(--muted)", padding: "16px 10px",
                textAlign: "center", border: "1px dashed var(--line)", borderRadius: 8,
              }}>
                {bbLoading ? "加载中..." : "暂无记录"}
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
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
                    <div
                      key={entry.id}
                      style={{
                        border: "1px solid var(--line)", borderRadius: 6,
                        padding: "6px 8px", background: "var(--bg-card, #fff)",
                        fontSize: 11,
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3 }}>
                        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                          <span style={{
                            fontSize: 9, padding: "1px 5px", borderRadius: 3,
                            background: (typeColors[entry.memory_type] || "#6b7280") + "18",
                            color: typeColors[entry.memory_type] || "#6b7280",
                            fontWeight: 600,
                          }}>
                            {typeLabels[entry.memory_type] || entry.memory_type}
                          </span>
                          <span style={{ fontSize: 9, color: "var(--muted)" }}>{scopeLabel}</span>
                        </div>
                        <button
                          className="btnSmall"
                          style={{ fontSize: 9, padding: "0 4px", color: "var(--muted)" }}
                          title="删除此条"
                          onClick={async () => {
                            try {
                              await safeFetch(`${apiBaseUrl}/api/orgs/${currentOrg.id}/memory/${entry.id}`, { method: "DELETE" });
                              setBbEntries((prev) => prev.filter((e: any) => e.id !== entry.id));
                            } catch { /* ignore */ }
                          }}
                        >
                          ×
                        </button>
                      </div>
                      <div style={{ lineHeight: 1.5, wordBreak: "break-word" }}>
                        {entry.content}
                      </div>
                      {entry.tags && entry.tags.length > 0 && (
                        <div style={{ marginTop: 3, display: "flex", gap: 3, flexWrap: "wrap" }}>
                          {entry.tags.map((t: string) => (
                            <span key={t} style={{
                              fontSize: 9, padding: "0 4px", borderRadius: 3,
                              background: "#f3f4f6", color: "#6b7280",
                            }}>#{t}</span>
                          ))}
                        </div>
                      )}
                      <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 3 }}>
                        {entry.source_node && <span>来自 {entry.source_node} · </span>}
                        {entry.created_at ? new Date(entry.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Inbox Sidebar */}
      {currentOrg && (
        <OrgInboxSidebar
          apiBaseUrl={apiBaseUrl}
          orgId={currentOrg.id}
          visible={inboxOpen}
          onClose={() => setInboxOpen(false)}
        />
      )}
    </div>
  );
}
