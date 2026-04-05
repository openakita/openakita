import { MarkerType, type Node, type Edge } from "@xyflow/react";
import type { OrgNodeData, OrgEdgeData } from "./types";

// ── Color maps ──

export const EDGE_COLORS: Record<string, string> = {
  hierarchy: "var(--primary)",
  collaborate: "var(--ok)",
  escalate: "var(--danger)",
  consult: "#a78bfa",
};

export const STATUS_COLORS: Record<string, string> = {
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

export const DEPT_COLORS: Record<string, string> = {
  "管理层": "#6366f1",
  "技术部": "#0ea5e9",
  "产品部": "#8b5cf6",
  "市场部": "#f97316",
  "行政支持": "#64748b",
  "研发部": "#0ea5e9",
  "设计部": "#ec4899",
  "运维部": "#14b8a6",
  "财务部": "#eab308",
  "人事部": "#f43f5e",
  "法务部": "#6b7280",
  "内容部": "#f97316",
  "销售部": "#22c55e",
  "客服部": "#06b6d4",
  "战略部": "#a855f7",
  "质量部": "#84cc16",
  "安全部": "#dc2626",
  "数据部": "#3b82f6",
  "工程": "#0ea5e9",
  "前端组": "#06b6d4",
  "后端组": "#14b8a6",
  "编辑部": "#f97316",
  "创作组": "#ec4899",
  "运营组": "#84cc16",
};

export function getDeptColor(dept: string): string {
  return DEPT_COLORS[dept] || "#6b7280";
}

// ── Time formatting helpers ──

export function fmtTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDateTime(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtShortDate(v: string | number | undefined | null): string {
  if (!v) return "";
  const d = new Date(typeof v === "number" ? v : v);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ── Data conversion ──

export function orgNodeToFlowNode(n: OrgNodeData): Node {
  return {
    id: n.id,
    type: "orgNode",
    position: n.position,
    data: { ...n },
  };
}

export function orgEdgeToFlowEdge(e: OrgEdgeData): Edge {
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

export function computeTreeLayout(nodes: Node[], edges: Edge[]): Node[] {
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

// ── Node position helpers ──

const NODE_COLLISION_W = 200;
const NODE_COLLISION_H = 80;
const NEW_NODE_ANCHOR = { x: 250, y: 200 };
const NEW_NODE_STEP_X = 240;
const NEW_NODE_STEP_Y = 140;

function isPositionOccupied(nodes: Node[], candidate: { x: number; y: number }): boolean {
  return nodes.some((n) => {
    const p = n.position || { x: 0, y: 0 };
    return Math.abs(p.x - candidate.x) < NODE_COLLISION_W && Math.abs(p.y - candidate.y) < NODE_COLLISION_H;
  });
}

export function getNextNodePosition(nodes: Node[]): { x: number; y: number } {
  if (nodes.length === 0) return { ...NEW_NODE_ANCHOR };
  if (!isPositionOccupied(nodes, NEW_NODE_ANCHOR)) return { ...NEW_NODE_ANCHOR };

  const columns = Math.max(3, Math.ceil(Math.sqrt(nodes.length + 1)));
  const maxAttempts = (nodes.length + 16) * 2;

  for (let i = 0; i < maxAttempts; i++) {
    const row = Math.floor(i / columns);
    const col = i % columns;
    const candidate = {
      x: NEW_NODE_ANCHOR.x + col * NEW_NODE_STEP_X,
      y: NEW_NODE_ANCHOR.y + row * NEW_NODE_STEP_Y,
    };
    if (!isPositionOccupied(nodes, candidate)) return candidate;
  }

  const maxX = nodes.reduce((m, n) => Math.max(m, n.position?.x ?? 0), 0);
  const maxY = nodes.reduce((m, n) => Math.max(m, n.position?.y ?? 0), 0);
  return { x: maxX + NEW_NODE_STEP_X, y: maxY + NEW_NODE_STEP_Y };
}

export function detectOverlap(nodes: Node[]): boolean {
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i].position || { x: 0, y: 0 };
      const b = nodes[j].position || { x: 0, y: 0 };
      if (Math.abs(a.x - b.x) < NODE_COLLISION_W && Math.abs(a.y - b.y) < NODE_COLLISION_H) return true;
    }
  }
  return false;
}

// ── Task status labels ──

export const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  busy: "执行中",
  waiting: "等待中",
  error: "异常",
  offline: "离线",
  frozen: "已冻结",
};

export const TASK_STATUS_LABELS: Record<string, string> = {
  todo: "待办",
  in_progress: "进行中",
  delivered: "已交付",
  rejected: "已打回",
  accepted: "已验收",
  blocked: "已阻塞",
};
