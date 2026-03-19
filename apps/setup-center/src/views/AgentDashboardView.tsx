import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { IconRefresh } from "../icons";
import { safeFetch } from "../providers";

// ── Types ──────────────────────────────────────────────────────────

type TopoNode = {
  id: string;
  profile_id: string;
  name: string;
  icon: string;
  color: string;
  status: "idle" | "running" | "completed" | "error" | "dormant";
  is_sub_agent: boolean;
  parent_id: string | null;
  iteration: number;
  tools_executed: string[];
  tools_total: number;
  elapsed_s: number;
  conversation_title: string;
};

type TopoEdge = { from: string; to: string; type: string };
type TopoStats = {
  total_requests: number;
  successful: number;
  failed: number;
  avg_latency_ms: number;
};
type TopoData = { nodes: TopoNode[]; edges: TopoEdge[]; stats: TopoStats };

// ── Force simulation node ──────────────────────────────────────────

type SimNode = TopoNode & {
  x: number;
  y: number;
  vx: number;
  vy: number;
  targetR: number;
  r: number;
  opacity: number;
  birthT: number;
  deathT: number | null;
  ripples: { t: number; maxR: number }[];
  prevToolsTotal: number;
  rgb: [number, number, number];
};

// ── Pulse along an edge ────────────────────────────────────────────

type Pulse = { edge: TopoEdge; t: number; speed: number };

// ── Tool satellite node ─────────────────────────────────────────────

type ToolSat = {
  id: string;
  parentId: string;
  name: string;
  x: number;
  y: number;
  angle: number;
  dist: number;
  r: number;
  opacity: number;
  birthT: number;
  deathT: number;
  color: string;
};

// ── Ambient particle ───────────────────────────────────────────────

type Mote = { x: number; y: number; vx: number; vy: number; size: number; alpha: number };

// ── Helpers ────────────────────────────────────────────────────────

const _rgbCache = new Map<string, [number, number, number]>();
function hexToRgb(hex: string): [number, number, number] {
  const cached = _rgbCache.get(hex);
  if (cached) return cached;
  let h = hex.replace("#", "");
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  const rgb: [number, number, number] = [
    Number.isFinite(r) ? r : 107,
    Number.isFinite(g) ? g : 114,
    Number.isFinite(b) ? b : 128,
  ];
  _rgbCache.set(hex, rgb);
  return rgb;
}

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

function isDark(): boolean {
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
  if (!bg) return true;
  if (bg.startsWith("#")) {
    const [r, g, b] = hexToRgb(bg);
    return r * 0.299 + g * 0.587 + b * 0.114 < 128;
  }
  return document.documentElement.dataset.theme === "dark";
}

const now = () => performance.now() / 1000;

const TOOL_ICONS: Record<string, string> = {
  web_search: "🔍", browser_navigate: "🌐", browser_snapshot: "📸", browser: "🌐",
  file_read: "📄", read_file: "📄", file_write: "✏️", write_file: "✏️",
  execute_command: "⚡", create_agent: "🤖", delegate_to_agent: "🔗",
  list_skills: "📋", memory_read: "🧠", memory_write: "💾",
  create_todo: "📝", mcp_call: "🔌", send_message: "💬",
  desktop_click: "🖱️", desktop_type: "⌨️", desktop_action: "🖥️",
};
function toolIcon(name: string): string {
  const lc = name.toLowerCase();
  for (const [k, v] of Object.entries(TOOL_ICONS)) {
    if (lc.includes(k)) return v;
  }
  return "⚙️";
}

// SVG icon paths for canvas rendering (must match AgentManagerView)
const SVG_ICON_PATHS: Record<string, string> = {
  terminal:   "M4 17l6-5-6-5M12 19h8",
  code:       "M16 18l6-6-6-6M8 6l-6 6 6 6",
  globe:      "M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10A15.3 15.3 0 0112 2z",
  shield:     "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  database:   "M12 2C6.48 2 2 3.79 2 6v12c0 2.21 4.48 4 10 4s10-1.79 10-4V6c0-2.21-4.48-4-10-4zM2 12c0 2.21 4.48 4 10 4s10-1.79 10-4M2 6c0 2.21 4.48 4 10 4s10-1.79 10-4",
  cpu:        "M6 6h12v12H6zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",
  cloud:      "M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z",
  lock:       "M19 11H5a2 2 0 00-2 2v7a2 2 0 002 2h14a2 2 0 002-2v-7a2 2 0 00-2-2zM7 11V7a5 5 0 0110 0v4",
  zap:        "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
  eye:        "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 100 6 3 3 0 000-6z",
  message:    "M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z",
  mail:       "M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6",
  chart:      "M18 20V10M12 20V4M6 20v-6",
  network:    "M5.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM18.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM12 24a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM5.5 5.5L12 19M18.5 5.5L12 19",
  target:     "M12 2a10 10 0 100 20 10 10 0 000-20zM12 6a6 6 0 100 12 6 6 0 000-12zM12 10a2 2 0 100 4 2 2 0 000-4z",
  compass:    "M12 2a10 10 0 100 20 10 10 0 000-20zM16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36z",
  layers:     "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  workflow:   "M6 3a3 3 0 100 6 3 3 0 000-6zM18 15a3 3 0 100 6 3 3 0 000-6zM8.59 13.51l6.83 3.98M6 9v4M18 9v6",
  flask:      "M9 3h6M10 3v6.5l-5 8.5h14l-5-8.5V3",
  pen:        "M12 20h9M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4L16.5 3.5z",
  mic:        "M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8",
  bot:        "M12 2a2 2 0 012 2v1h3a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2h3V4a2 2 0 012-2zM9 13h0M15 13h0M9 17h6",
  puzzle:     "M19.439 12.956l-1.5 0a2 2 0 010-4l1.5 0a.5.5 0 00.5-.5l0-2.5a2 2 0 00-2-2l-2.5 0a.5.5 0 01-.5-.5l0-1.5a2 2 0 00-4 0l0 1.5a.5.5 0 01-.5.5L7.939 3.956a2 2 0 00-2 2l0 2.5a.5.5 0 00.5.5l1.5 0a2 2 0 010 4l-1.5 0a.5.5 0 00-.5.5l0 2.5a2 2 0 002 2l2.5 0a.5.5 0 01.5.5l0 1.5a2 2 0 004 0l0-1.5a.5.5 0 01.5-.5l2.5 0a2 2 0 002-2l0-2.5a.5.5 0 00-.5-.5z",
  heart:      "M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 000-7.78z",
};
const _svgImgCache = new Map<string, HTMLImageElement>();
function getSvgImage(name: string, color: string): HTMLImageElement | null {
  const key = `${name}:${color}`;
  const cached = _svgImgCache.get(key);
  if (cached) return cached.complete ? cached : null;
  const pathD = SVG_ICON_PATHS[name];
  if (!pathD) return null;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${pathD}"/></svg>`;
  const img = new Image();
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  _svgImgCache.set(key, img);
  return null;
}

const _emojiCache = new Map<string, HTMLCanvasElement>();
function getEmojiCanvas(emoji: string, size: number): HTMLCanvasElement {
  const key = `${emoji}:${size}`;
  const cached = _emojiCache.get(key);
  if (cached) return cached;
  const res = size * 2;
  const cvs = document.createElement("canvas");
  cvs.width = res;
  cvs.height = res;
  const c = cvs.getContext("2d");
  if (c) {
    c.font = `${size}px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif`;
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillText(emoji, res / 2, res / 2 + size * 0.06);
  }
  _emojiCache.set(key, cvs);
  return cvs;
}

// ── Main component ─────────────────────────────────────────────────

export function AgentDashboardView({
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
  multiAgentEnabled = false,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
  multiAgentEnabled?: boolean;
}) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  const simNodesRef = useRef<Map<string, SimNode>>(new Map());
  const toolSatsRef = useRef<ToolSat[]>([]);
  const motesRef = useRef<Mote[]>([]);
  const pulsesRef = useRef<Pulse[]>([]);
  const edgesRef = useRef<TopoEdge[]>([]);
  const animRef = useRef<number>(0);
  const darkRef = useRef(true);
  const sizeRef = useRef({ w: 800, h: 500 });
  const breathRef = useRef(0);
  const lastPulseRef = useRef<Map<string, number>>(new Map());
  const dragRef = useRef<{ nodeId: string; offsetX: number; offsetY: number; moved: boolean } | null>(null);

  const [topoData, setTopoData] = useState<TopoData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [overlayTick, setOverlayTick] = useState(0);
  const overlayTickRef = useRef(0);

  // ── Fetch topology ────────────────────────────────────────────

  const fetchTopo = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/topology`);
      const data: TopoData = await res.json();
      setTopoData(data);
    } catch { /* silent */ }
  }, [apiBaseUrl]);

  useEffect(() => {
    if (!visible || !multiAgentEnabled) return;
    fetchTopo();
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      const hasRunning = Array.from(simNodesRef.current.values()).some(
        (n) => n.status === "running",
      );
      timer = setTimeout(() => { fetchTopo().then(schedule); }, hasRunning ? 3000 : 10000);
    };
    schedule();
    return () => clearTimeout(timer);
  }, [visible, multiAgentEnabled, fetchTopo]);

  // ── Sync topo data → sim nodes ────────────────────────────────

  useEffect(() => {
    if (!topoData) return;
    const map = simNodesRef.current;
    const currentIds = new Set(topoData.nodes.map((n) => n.id));
    edgesRef.current = topoData.edges;
    const W = sizeRef.current.w;
    const H = sizeRef.current.h;
    const CX = W / 2, CY = H / 2;

    for (const n of topoData.nodes) {
      const existing = map.get(n.id);
      if (existing) {
        // detect new tool executions → trigger ripple + spawn tool satellites
        if (n.tools_total > existing.prevToolsTotal) {
          existing.ripples.push({ t: now(), maxR: 60 });
          const diff = n.tools_total - existing.prevToolsTotal;
          const newTools = n.tools_executed.slice(-diff);
          for (const toolName of newTools) {
            toolSatsRef.current.push({
              id: `${n.id}::tool::${toolName}::${Math.random()}`,
              parentId: n.id,
              name: toolName,
              x: existing.x,
              y: existing.y,
              angle: Math.random() * Math.PI * 2,
              dist: 50 + Math.random() * 25,
              r: 6,
              opacity: 0,
              birthT: now(),
              deathT: now() + 10,
              color: n.color || "#6b7280",
            });
          }
        }
        const tgtR = n.status === "dormant" ? 18 : n.is_sub_agent ? 22 : 32;
        Object.assign(existing, {
          ...n,
          targetR: tgtR,
          prevToolsTotal: n.tools_total,
          rgb: hexToRgb(n.color || "#6b7280"),
        });
        if (n.status === "completed" && !existing.deathT) {
          existing.deathT = now() + 15;
        }
      }       else {
        const parent = n.parent_id ? map.get(n.parent_id) : null;
        let bx: number, by: number;
        if (n.is_sub_agent) {
          const existingSiblings = Array.from(map.values()).filter(
            (s) => s.parent_id === n.parent_id && s.is_sub_agent
          );
          const totalSiblings = topoData.nodes.filter(
            (s) => s.parent_id === n.parent_id && s.is_sub_agent
          );
          const newSiblings = totalSiblings.filter((s) => !map.has(s.id));
          const myNewIdx = newSiblings.indexOf(n);
          const seqIdx = existingSiblings.length + Math.max(myNewIdx, 0);
          const totalCount = Math.max(totalSiblings.length, seqIdx + 1);

          // Full circle around parent, centered on canvas
          const px = parent ? parent.x : CX;
          const py = parent ? parent.y : CY;
          const fanRadius = Math.max(120, totalCount * 50);
          const angleStep = (Math.PI * 2) / Math.max(totalCount, 1);
          const angle = angleStep * seqIdx - Math.PI / 2;
          bx = px + Math.cos(angle) * fanRadius;
          by = py + Math.sin(angle) * fanRadius;
        } else if (n.status === "dormant") {
          // dormant: scatter across full canvas, prefer edges
          const edge = Math.random();
          if (edge < 0.25) { bx = 60 + Math.random() * 80; by = 60 + Math.random() * (H - 120); }
          else if (edge < 0.5) { bx = W - 60 - Math.random() * 80; by = 60 + Math.random() * (H - 120); }
          else if (edge < 0.75) { bx = 60 + Math.random() * (W - 120); by = 60 + Math.random() * 80; }
          else { bx = 60 + Math.random() * (W - 120); by = H - 60 - Math.random() * 80; }
        } else {
          // active root agents: spread around canvas center
          const roots = topoData.nodes.filter((r) => !r.parent_id && !r.is_sub_agent && r.status !== "dormant");
          const rIdx = roots.indexOf(n);
          const rCount = roots.length || 1;
          if (rCount === 1) {
            bx = CX;
            by = CY;
          } else {
            const rAngle = (Math.PI * 2 * rIdx) / rCount - Math.PI / 2;
            const rRadius = Math.min(W, H) * 0.18;
            bx = CX + Math.cos(rAngle) * rRadius;
            by = CY + Math.sin(rAngle) * rRadius;
          }
        }
        const tgtR = n.status === "dormant" ? 18 : n.is_sub_agent ? 22 : 32;
        map.set(n.id, {
          ...n,
          x: bx,
          y: by,
          vx: (Math.random() - 0.5) * 0.5,
          vy: (Math.random() - 0.5) * 0.5,
          targetR: tgtR,
          r: 0,
          opacity: 0,
          birthT: now(),
          deathT: n.status === "completed" ? now() + 15 : null,
          ripples: [],
          prevToolsTotal: n.tools_total,
          rgb: hexToRgb(n.color || "#6b7280"),
        });
      }
    }

    // Mark dead nodes
    for (const [id, node] of map) {
      if (!currentIds.has(id) && !node.deathT) {
        node.deathT = now() + 2;
      }
    }
  }, [topoData]);

  // ── Init motes ────────────────────────────────────────────────

  useEffect(() => {
    const W = sizeRef.current.w;
    const H = sizeRef.current.h;
    if (motesRef.current.length === 0) {
      for (let i = 0; i < 50; i++) {
        motesRef.current.push({
          x: Math.random() * W,
          y: Math.random() * H,
          vx: (Math.random() - 0.5) * 0.3,
          vy: (Math.random() - 0.5) * 0.3,
          size: 1 + Math.random() * 2,
          alpha: 0.15 + Math.random() * 0.2,
        });
      }
    }
  }, []);

  // ── Resize ────────────────────────────────────────────────────

  useEffect(() => {
    const onResize = () => {
      const c = containerRef.current;
      if (!c) return;
      const rect = c.getBoundingClientRect();
      sizeRef.current = { w: rect.width, h: rect.height };
      const cvs = canvasRef.current;
      if (cvs) {
        cvs.width = rect.width * devicePixelRatio;
        cvs.height = rect.height * devicePixelRatio;
        cvs.style.width = rect.width + "px";
        cvs.style.height = rect.height + "px";
      }
    };
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [visible]);

  // ── Animation loop ────────────────────────────────────────────

  useEffect(() => {
    if (!visible || !multiAgentEnabled) return;
    darkRef.current = isDark();
    const themeObs = setInterval(() => { darkRef.current = isDark(); }, 2000);

    const step = () => {
      const cvs = canvasRef.current;
      if (!cvs) { animRef.current = requestAnimationFrame(step); return; }
      const ctx = cvs.getContext("2d");
      if (!ctx) { animRef.current = requestAnimationFrame(step); return; }

      const dpr = devicePixelRatio;
      const W = sizeRef.current.w;
      const H = sizeRef.current.h;
      const t = now();
      const dark = darkRef.current;
      const map = simNodesRef.current;
      const nodes = Array.from(map.values());
      const edges = edgesRef.current;
      const motes = motesRef.current;
      const pulses = pulsesRef.current;

      // ── Physics step ──
      const CX = W / 2, CY = H / 2;
      const PAD = 40;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        const aDorm = a.status === "dormant";
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const bDorm = b.status === "dormant";
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const areSiblings = a.is_sub_agent && b.is_sub_agent
            && a.parent_id && a.parent_id === b.parent_id;
          const bothRoot = !a.is_sub_agent && !b.is_sub_agent && !aDorm && !bDorm;
          const bothActive = !aDorm && !bDorm;
          const strength = areSiblings ? 30000
            : bothRoot ? 35000
            : bothActive ? 28000
            : (aDorm && bDorm) ? 6000 : 10000;
          const minDist = areSiblings ? 120 : bothRoot ? 140
            : bothActive ? 110
            : (aDorm && bDorm) ? 80 : 100;
          if (dist < minDist) dist = minDist;
          const repulse = strength / (dist * dist);
          const fx = (dx / dist) * repulse;
          const fy = (dy / dist) * repulse;
          a.vx += fx * 0.016;
          a.vy += fy * 0.016;
          b.vx -= fx * 0.016;
          b.vy -= fy * 0.016;
        }

        if (!aDorm) {
          const parent = a.parent_id ? map.get(a.parent_id) : null;
          if (a.is_sub_agent) {
            const siblings = nodes.filter(
              (s) => s.parent_id === a.parent_id && s.is_sub_agent
            );
            const myIdx = siblings.indexOf(a);
            const count = siblings.length || 1;
            const px = parent ? parent.x : CX;
            const py = parent ? parent.y : CY;
            const fanR = Math.max(120, count * 50);
            const angStep = (Math.PI * 2) / Math.max(count, 1);
            const ang = angStep * myIdx - Math.PI / 2;
            const targetX = px + Math.cos(ang) * fanR;
            const targetY = py + Math.sin(ang) * fanR;
            a.vx += (targetX - a.x) * 0.006;
            a.vy += (targetY - a.y) * 0.006;
          } else {
            // Root agents: each gets a unique target spread around center
            const roots = nodes.filter(
              (r) => !r.is_sub_agent && r.status !== "dormant"
            );
            const rIdx = roots.indexOf(a);
            const rCount = roots.length || 1;
            let tgtX = CX, tgtY = CY;
            if (rCount > 1) {
              const rAng = (Math.PI * 2 * rIdx) / rCount - Math.PI / 2;
              const rR = Math.min(W, H) * 0.15;
              tgtX = CX + Math.cos(rAng) * rR;
              tgtY = CY + Math.sin(rAng) * rR;
            }
            a.vx += (tgtX - a.x) * 0.003;
            a.vy += (tgtY - a.y) * 0.003;
          }
        }
      }

      // edge attraction — larger ideal distance
      for (const e of edges) {
        const a = map.get(e.from);
        const b = map.get(e.to);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const ideal = 150;
        const force = (dist - ideal) * 0.005;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }

      // damping + position update
      const dragId = dragRef.current?.nodeId;
      for (const n of nodes) {
        if (n.id === dragId) {
          n.vx = 0;
          n.vy = 0;
          n.x = Math.max(PAD, Math.min(W - PAD, n.x));
          n.y = Math.max(PAD, Math.min(H - PAD, n.y));
        } else {
          const isDormant = n.status === "dormant";
          n.vx *= isDormant ? 0.96 : 0.88;
          n.vy *= isDormant ? 0.96 : 0.88;
          if (!isDormant) {
            n.vx += (Math.random() - 0.5) * 0.04;
            n.vy += (Math.random() - 0.5) * 0.04;
          }
          n.x += n.vx;
          n.y += n.vy;
          n.x = Math.max(PAD, Math.min(W - PAD, n.x));
          n.y = Math.max(PAD, Math.min(H - PAD, n.y));
        }

        // animate radius + opacity (birth/death)
        const age = t - n.birthT;
        if (n.deathT && t > n.deathT) {
          n.opacity = Math.max(0, n.opacity - 0.02);
          n.r = Math.max(0, n.r - 0.3);
          if (n.opacity <= 0) map.delete(n.id);
        } else {
          n.r = lerp(n.r, n.targetR, 0.08);
          const maxOpacity = n.status === "dormant" ? 0.75 : 1;
          n.opacity = Math.min(maxOpacity, n.opacity + 0.04);
        }

        // ripple cleanup
        n.ripples = n.ripples.filter((rp) => t - rp.t < 1.5);
      }

      // ── Pulse spawning ──
      for (const e of edges) {
        const src = map.get(e.from);
        const dst = map.get(e.to);
        if (!src || !dst) continue;
        if (src.status === "running" || dst.status === "running") {
          const key = `${e.from}->${e.to}`;
          const last = lastPulseRef.current.get(key) || 0;
          if (t - last > 1.5 + Math.random() * 0.5) {
            pulses.push({ edge: e, t: 0, speed: 0.6 + Math.random() * 0.3 });
            lastPulseRef.current.set(key, t);
          }
        }
      }

      // advance pulses
      for (let i = pulses.length - 1; i >= 0; i--) {
        pulses[i].t += 0.016 * pulses[i].speed;
        if (pulses[i].t > 1) {
          // trigger flash on destination
          const dst = map.get(pulses[i].edge.to);
          if (dst) dst.ripples.push({ t, maxR: 30 });
          pulses.splice(i, 1);
        }
      }

      // ── Tool satellite physics ──
      const sats = toolSatsRef.current;
      for (let i = sats.length - 1; i >= 0; i--) {
        const sat = sats[i];
        const parent = map.get(sat.parentId);
        if (!parent || (t > sat.deathT + 2 && sat.opacity <= 0)) {
          sats.splice(i, 1);
          continue;
        }
        sat.angle += 0.012;
        sat.x = parent.x + Math.cos(sat.angle) * sat.dist;
        sat.y = parent.y + Math.sin(sat.angle) * sat.dist;
        if (t < sat.birthT + 0.5) {
          sat.opacity = Math.min(0.9, (t - sat.birthT) / 0.5);
        } else if (t > sat.deathT) {
          sat.opacity = Math.max(0, sat.opacity - 0.015);
        }
      }

      // ── Motes (ambient particles) ──
      for (const m of motes) {
        m.vx += (Math.random() - 0.5) * 0.02;
        m.vy += (Math.random() - 0.5) * 0.02;
        // attract toward running nodes
        for (const n of nodes) {
          if (n.status !== "running") continue;
          const dx = n.x - m.x;
          const dy = n.y - m.y;
          const d = Math.sqrt(dx * dx + dy * dy) || 1;
          if (d < 200) {
            m.vx += (dx / d) * 0.015;
            m.vy += (dy / d) * 0.015;
          }
        }
        m.vx *= 0.97;
        m.vy *= 0.97;
        m.x += m.vx;
        m.y += m.vy;
        if (m.x < 0) m.x = W;
        if (m.x > W) m.x = 0;
        if (m.y < 0) m.y = H;
        if (m.y > H) m.y = 0;
      }

      // ── Render ──
      ctx.save();
      ctx.scale(dpr, dpr);

      // clear canvas (transparent — inherits container background)
      ctx.clearRect(0, 0, W, H);
      breathRef.current += 0.016;

      // ambient motes
      for (const m of motes) {
        ctx.beginPath();
        ctx.arc(m.x, m.y, m.size, 0, Math.PI * 2);
        if (dark) {
          ctx.fillStyle = `rgba(255,255,255,${m.alpha * 0.5})`;
        } else {
          ctx.fillStyle = `rgba(0,0,0,${m.alpha * 0.2})`;
        }
        ctx.fill();
      }

      // constellation lines between nearby motes (batched by alpha bin)
      ctx.lineWidth = 0.5;
      const moteBins: [number, number, number, number][][] = [[], [], []];
      for (let i = 0; i < motes.length; i++) {
        for (let j = i + 1; j < motes.length; j++) {
          const dx = motes[i].x - motes[j].x;
          const dy = motes[i].y - motes[j].y;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d < 60) {
            const a = (1 - d / 60) * 0.06;
            const bin = a > 0.04 ? 2 : a > 0.02 ? 1 : 0;
            moteBins[bin].push([motes[i].x, motes[i].y, motes[j].x, motes[j].y]);
          }
        }
      }
      const binAlphas = [0.01, 0.03, 0.05];
      for (let b = 0; b < 3; b++) {
        if (moteBins[b].length === 0) continue;
        ctx.beginPath();
        for (const [x1, y1, x2, y2] of moteBins[b]) {
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
        }
        const ba = binAlphas[b];
        ctx.strokeStyle = dark ? `rgba(255,255,255,${ba})` : `rgba(0,0,0,${ba * 0.5})`;
        ctx.stroke();
      }

      // faint connections from dormant nodes to nearby motes (batched per node)
      for (const n of nodes) {
        if (n.status !== "dormant" || n.opacity <= 0) continue;
        const [cr, cg, cb] = n.rgb;
        ctx.beginPath();
        let hasLines = false;
        for (const m of motes) {
          const dx = n.x - m.x;
          const dy = n.y - m.y;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d < 70) {
            ctx.moveTo(n.x, n.y);
            ctx.lineTo(m.x, m.y);
            hasLines = true;
          }
        }
        if (hasLines) {
          ctx.strokeStyle = `rgba(${cr},${cg},${cb},${0.04 * n.opacity})`;
          ctx.stroke();
        }
      }

      // ── Dormant inter-connections (latent neural web) ──
      const dormantArr = nodes.filter((n) => n.status === "dormant" && n.opacity > 0);
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 8]);
      for (let i = 0; i < dormantArr.length; i++) {
        for (let j = i + 1; j < dormantArr.length; j++) {
          const a = dormantArr[i], b = dormantArr[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d > 300) continue;
          const [ar, ag, ab] = a.rgb;
          const [br, bg, bb] = b.rgb;
          const proximity = 1 - d / 300;
          const lineA = proximity * 0.12 * Math.min(a.opacity, b.opacity);
          const mr = (ar + br) >> 1, mg = (ag + bg) >> 1, mb = (ab + bb) >> 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.strokeStyle = `rgba(${mr},${mg},${mb},${lineA})`;
          ctx.stroke();
        }
      }
      ctx.setLineDash([]);

      // ── Draw edges (synapses) — organic cubic bezier ──
      for (const e of edges) {
        const src = map.get(e.from);
        const dst = map.get(e.to);
        if (!src || !dst || src.opacity <= 0 || dst.opacity <= 0) continue;

        const dx = dst.x - src.x;
        const dy = dst.y - src.y;
        const perp = { x: -dy * 0.15, y: dx * 0.15 };
        const drift1 = Math.sin(t * 0.25 + src.x * 0.007) * 12;
        const drift2 = Math.cos(t * 0.3 + dst.y * 0.009) * 12;
        const cp1x = src.x + dx * 0.3 + perp.x + drift1;
        const cp1y = src.y + dy * 0.3 + perp.y + drift1;
        const cp2x = src.x + dx * 0.7 - perp.x + drift2;
        const cp2y = src.y + dy * 0.7 - perp.y + drift2;

        const [sr, sg, sb] = src.rgb;
        const [dr, dg, db] = dst.rgb;
        const isActive = src.status === "running" || dst.status === "running";
        const baseAlpha = isActive ? 0.7 : 0.2;
        const alpha = baseAlpha * Math.min(src.opacity, dst.opacity);

        const grad = ctx.createLinearGradient(src.x, src.y, dst.x, dst.y);
        grad.addColorStop(0, `rgba(${sr},${sg},${sb},${alpha})`);
        grad.addColorStop(0.5, `rgba(${(sr + dr) >> 1},${(sg + dg) >> 1},${(sb + db) >> 1},${alpha * 1.2})`);
        grad.addColorStop(1, `rgba(${dr},${dg},${db},${alpha})`);

        ctx.beginPath();
        ctx.moveTo(src.x, src.y);
        ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, dst.x, dst.y);
        ctx.strokeStyle = grad;
        ctx.lineWidth = isActive ? 3 : 1.5;
        ctx.stroke();

        // Store control points for pulse positioning
        (e as any)._cp = { cp1x, cp1y, cp2x, cp2y };
      }

      // ── Draw pulses (comet-like with trail) ──
      for (const p of pulses) {
        const src = map.get(p.edge.from);
        const dst = map.get(p.edge.to);
        if (!src || !dst) continue;

        const cp = (p.edge as any)._cp;
        const bezAt = (u: number) => {
          if (cp) {
            const u1 = 1 - u;
            return {
              x: u1*u1*u1*src.x + 3*u1*u1*u*cp.cp1x + 3*u1*u*u*cp.cp2x + u*u*u*dst.x,
              y: u1*u1*u1*src.y + 3*u1*u1*u*cp.cp1y + 3*u1*u*u*cp.cp2y + u*u*u*dst.y,
            };
          }
          return { x: lerp(src.x, dst.x, u), y: lerp(src.y, dst.y, u) };
        };

        const [cr, cg, cb] = src.rgb;
        const headAlpha = Math.sin(p.t * Math.PI) * 0.95;

        // Trail: draw fading segments behind the head
        const trailLen = 6;
        for (let s = trailLen; s >= 0; s--) {
          const u = Math.max(0, p.t - s * 0.03);
          const pt = bezAt(u);
          const fade = (1 - s / trailLen) * headAlpha;
          const r = 4 + (1 - s / trailLen) * 7;
          const grd = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, r);
          grd.addColorStop(0, `rgba(${cr},${cg},${cb},${fade * 0.8})`);
          grd.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
          ctx.beginPath();
          ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
          ctx.fillStyle = grd;
          ctx.fill();
        }

        // Bright head — solid core + glow
        const head = bezAt(p.t);
        const hGrd = ctx.createRadialGradient(head.x, head.y, 0, head.x, head.y, 16);
        hGrd.addColorStop(0, `rgba(${cr},${cg},${cb},${headAlpha})`);
        hGrd.addColorStop(0.35, `rgba(${cr},${cg},${cb},${headAlpha * 0.5})`);
        hGrd.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
        ctx.beginPath();
        ctx.arc(head.x, head.y, 16, 0, Math.PI * 2);
        ctx.fillStyle = hGrd;
        ctx.fill();

        ctx.beginPath();
        ctx.arc(head.x, head.y, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255,255,255,${headAlpha * 0.9})`;
        ctx.fill();
      }

      // ── Draw neurons (solid + clear) ──
      for (const n of nodes) {
        if (n.opacity <= 0 || n.r <= 0) continue;
        const [cr, cg, cb] = n.rgb;
        const isRunning = n.status === "running";
        const isDorm = n.status === "dormant";
        const isErr = n.status === "error";

        // breathing scale
        const pulse = isRunning
          ? 1 + 0.1 * Math.sin(t * 2.5 + n.x * 0.01)
          : isDorm
            ? 1 + 0.04 * Math.sin(t * 0.6 + n.x * 0.005)
            : 1;
        const drawR = n.r * pulse;

        // outer glow ring (running or being dragged)
        const isDragging = n.id === dragId;
        if (isRunning || isDragging) {
          const gR = drawR + (isDragging ? 18 : 12);
          const gAlpha = isDragging ? 0.5 : 0.35;
          const g = ctx.createRadialGradient(n.x, n.y, drawR, n.x, n.y, gR);
          g.addColorStop(0, `rgba(${cr},${cg},${cb},${n.opacity * gAlpha})`);
          g.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
          ctx.beginPath();
          ctx.arc(n.x, n.y, gR, 0, Math.PI * 2);
          ctx.fillStyle = g;
          ctx.fill();
        }

        // ripples (tool execution) — ring that expands outward
        for (const rp of n.ripples) {
          const age = t - rp.t;
          const rpR = drawR + age * rp.maxR;
          const rpA = Math.max(0, 0.5 * (1 - age / 1.5)) * n.opacity;
          ctx.beginPath();
          ctx.arc(n.x, n.y, rpR, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${cr},${cg},${cb},${rpA})`;
          ctx.lineWidth = 2;
          ctx.stroke();
        }

        // solid filled circle
        const fillAlpha = n.opacity * (isRunning ? 0.9 : isDorm ? 0.35 : 0.6);
        ctx.beginPath();
        ctx.arc(n.x, n.y, drawR, 0, Math.PI * 2);
        ctx.fillStyle = dark
          ? `rgba(${cr},${cg},${cb},${fillAlpha})`
          : `rgba(${cr},${cg},${cb},${fillAlpha * 0.85})`;
        ctx.fill();

        // border ring
        const borderAlpha = n.opacity * (isRunning ? 0.9 : isDorm ? 0.3 : 0.5);
        ctx.beginPath();
        ctx.arc(n.x, n.y, drawR, 0, Math.PI * 2);
        ctx.strokeStyle = dark
          ? `rgba(${Math.min(255, cr + 60)},${Math.min(255, cg + 60)},${Math.min(255, cb + 60)},${borderAlpha})`
          : `rgba(${cr},${cg},${cb},${borderAlpha})`;
        ctx.lineWidth = isDorm ? 1 : 2;
        if (isDorm) {
          ctx.setLineDash([3, 3]);
        }
        ctx.stroke();
        ctx.setLineDash([]);

        // inner highlight (top-left light spot)
        if (!isDorm) {
          const hlG = ctx.createRadialGradient(
            n.x - drawR * 0.3, n.y - drawR * 0.3, 0,
            n.x - drawR * 0.3, n.y - drawR * 0.3, drawR * 0.6,
          );
          hlG.addColorStop(0, `rgba(255,255,255,${n.opacity * (isRunning ? 0.35 : 0.15)})`);
          hlG.addColorStop(1, `rgba(255,255,255,0)`);
          ctx.beginPath();
          ctx.arc(n.x, n.y, drawR, 0, Math.PI * 2);
          ctx.fillStyle = hlG;
          ctx.fill();
        }

        // error: red pulsing border
        if (isErr) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, drawR + 2, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(239,68,68,${0.5 + 0.3 * Math.sin(t * 6)})`;
          ctx.lineWidth = 2.5;
          ctx.stroke();
        }

        // running: spinning arc indicator
        if (isRunning) {
          const arcStart = t * 3;
          ctx.beginPath();
          ctx.arc(n.x, n.y, drawR + 5, arcStart, arcStart + Math.PI * 0.6);
          ctx.strokeStyle = `rgba(${cr},${cg},${cb},${n.opacity * 0.7})`;
          ctx.lineWidth = 2.5;
          ctx.lineCap = "round";
          ctx.stroke();
          ctx.lineCap = "butt";
        }

        // icon/emoji inside the node
        if (n.icon && drawR > 8) {
          const iconAlpha = n.opacity * (isDorm ? 0.6 : 0.95);
          ctx.save();
          ctx.globalAlpha = iconAlpha;
          if (n.icon.startsWith("svg:")) {
            const svgName = n.icon.slice(4);
            const strokeColor = dark ? `rgba(255,255,255,0.9)` : `rgba(255,255,255,0.95)`;
            const img = getSvgImage(svgName, strokeColor);
            if (img) {
              const iconSize = drawR * 1.2;
              ctx.drawImage(img, n.x - iconSize / 2, n.y - iconSize / 2, iconSize, iconSize);
            }
          } else {
            const emojiSize = Math.round(drawR * (isDorm ? 0.9 : 1.1));
            const cached = getEmojiCanvas(n.icon, emojiSize);
            const half = cached.width / 2;
            ctx.drawImage(cached, n.x - half, n.y - half);
          }
          ctx.restore();
        }
      }

      // ── Draw tool satellites (clear style) ──
      for (const sat of sats) {
        if (sat.opacity <= 0) continue;
        const parent = map.get(sat.parentId);
        if (!parent) continue;
        const [cr, cg, cb] = hexToRgb(sat.color);

        // connecting line to parent
        ctx.beginPath();
        ctx.moveTo(parent.x, parent.y);
        ctx.lineTo(sat.x, sat.y);
        ctx.strokeStyle = `rgba(${cr},${cg},${cb},${sat.opacity * 0.35})`;
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        // solid circle
        ctx.beginPath();
        ctx.arc(sat.x, sat.y, sat.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${cr},${cg},${cb},${sat.opacity * 0.6})`;
        ctx.fill();
        ctx.strokeStyle = dark
          ? `rgba(255,255,255,${sat.opacity * 0.4})`
          : `rgba(${cr},${cg},${cb},${sat.opacity * 0.5})`;
        ctx.lineWidth = 1;
        ctx.stroke();

        // tool name label
        const icon = toolIcon(sat.name);
        ctx.font = "bold 9px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillStyle = dark
          ? `rgba(255,255,255,${sat.opacity * 0.8})`
          : `rgba(0,0,0,${sat.opacity * 0.7})`;
        ctx.fillText(`${icon} ${sat.name}`, sat.x, sat.y + sat.r + 12);
      }

      ctx.restore();

      // ── Update HTML overlay positions ──
      const overlay = overlayRef.current;
      let visCount = 0;
      if (overlay) {
        const children = overlay.children;
        let idx = 0;
        for (const n of nodes) {
          if (n.opacity <= 0 || n.r <= 0) continue;
          visCount++;
          const el = children[idx] as HTMLElement | undefined;
          if (el) {
            el.style.transform = `translate(${n.x}px, ${n.y}px)`;
            el.style.opacity = String(n.opacity);
          }
          idx++;
        }
      } else {
        for (const n of nodes) {
          if (n.opacity > 0 && n.r > 0) visCount++;
        }
      }
      // Trigger React re-render when visible node count changes
      if (visCount !== overlayTickRef.current) {
        overlayTickRef.current = visCount;
        setOverlayTick(visCount);
      }

      animRef.current = requestAnimationFrame(step);
    };

    animRef.current = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(animRef.current);
      clearInterval(themeObs);
    };
  }, [visible, multiAgentEnabled]);

  // ── ESC to close detail panel ─────────────────────────────────

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── Node drag interaction ──────────────────────────────────────

  const getMousePos = useCallback((e: MouseEvent): { x: number; y: number } => {
    const c = containerRef.current;
    if (!c) return { x: 0, y: 0 };
    const rect = c.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }, []);

  const findNodeAt = useCallback((mx: number, my: number): SimNode | null => {
    const nodes = Array.from(simNodesRef.current.values());
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (n.opacity <= 0 || n.r <= 0) continue;
      const dx = mx - n.x, dy = my - n.y;
      if (dx * dx + dy * dy <= (n.r + 8) * (n.r + 8)) return n;
    }
    return null;
  }, []);

  useEffect(() => {
    const cvs = canvasRef.current;
    if (!cvs) return;

    const startDrag = (nodeId: string, mx: number, my: number) => {
      const node = simNodesRef.current.get(nodeId);
      if (!node) return;
      dragRef.current = { nodeId, offsetX: mx - node.x, offsetY: my - node.y, moved: false };
      node.vx = 0;
      node.vy = 0;
    };

    const onDown = (e: MouseEvent) => {
      const pos = getMousePos(e);
      const hit = findNodeAt(pos.x, pos.y);
      if (hit) {
        startDrag(hit.id, pos.x, pos.y);
        cvs.style.cursor = "grabbing";
        e.preventDefault();
      }
    };

    const onMove = (e: MouseEvent) => {
      const drag = dragRef.current;
      if (drag) {
        const pos = getMousePos(e);
        const node = simNodesRef.current.get(drag.nodeId);
        if (node) {
          node.x = pos.x - drag.offsetX;
          node.y = pos.y - drag.offsetY;
          node.vx = 0;
          node.vy = 0;
          drag.moved = true;
        }
        const container = containerRef.current;
        if (container) container.style.cursor = "grabbing";
      } else {
        const pos = getMousePos(e);
        const hit = findNodeAt(pos.x, pos.y);
        cvs.style.cursor = hit ? "grab" : "";
      }
    };

    const onUp = () => {
      if (dragRef.current) {
        const node = simNodesRef.current.get(dragRef.current.nodeId);
        if (node) {
          node.ripples.push({ t: now(), maxR: 40 });
        }
        dragRef.current = null;
        cvs.style.cursor = "";
        const container = containerRef.current;
        if (container) container.style.cursor = "";
      }
    };

    const onLabelDown = (e: MouseEvent) => {
      const target = (e.target as HTMLElement).closest(".neural-label");
      if (!target) return;
      const idx = Array.from(target.parentElement!.children).indexOf(target);
      const nodes = Array.from(simNodesRef.current.values()).filter((n) => n.opacity > 0 && n.r > 0);
      const node = nodes[idx];
      if (!node) return;
      const pos = getMousePos(e);
      startDrag(node.id, pos.x, pos.y);
      e.preventDefault();
    };

    const overlay = overlayRef.current;
    cvs.addEventListener("mousedown", onDown);
    overlay?.addEventListener("mousedown", onLabelDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      cvs.removeEventListener("mousedown", onDown);
      overlay?.removeEventListener("mousedown", onLabelDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [getMousePos, findNodeAt]);

  // ── Visible nodes for overlay ─────────────────────────────────

  const visibleNodes = useMemo(() => {
    const map = simNodesRef.current;
    return Array.from(map.values()).filter((n) => n.opacity > 0 && n.r > 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topoData, selected, hovered, overlayTick]);

  // ── Disabled state ────────────────────────────────────────────

  if (!multiAgentEnabled) {
    return (
      <div style={{ padding: 40, textAlign: "center", opacity: 0.5 }}>
        <div style={{ fontSize: 48 }}>🧠</div>
        <div style={{ marginTop: 12, fontWeight: 700 }}>{t("dashboard.disabled")}</div>
        <div style={{ fontSize: 13, marginTop: 4 }}>{t("dashboard.enableHint")}</div>
      </div>
    );
  }

  const stats = topoData?.stats;
  const selectedNode = selected ? simNodesRef.current.get(selected) || null : null;

  return (
    <div ref={containerRef} className="neural-dash" onClick={() => setSelected(null)}>
      <NeuralStyles />

      {/* Canvas */}
      <canvas ref={canvasRef} className="neural-canvas" />

      {/* Node labels overlay */}
      <div ref={overlayRef} className="neural-overlay">
        {visibleNodes.map((n) => (
          <div
            key={n.id}
            className={`neural-label ${n.status} ${hovered === n.id ? "hovered" : ""} ${selected === n.id ? "selected" : ""}`}
            style={{ "--nc": n.color } as React.CSSProperties}
            onMouseEnter={() => setHovered(n.id)}
            onMouseLeave={() => setHovered(null)}
            onClick={(e) => { e.stopPropagation(); if (dragRef.current?.moved) return; setSelected(n.id === selected ? null : n.id); }}
          >
            <span className="neural-name">{n.name}</span>
            {n.status === "running" && (
              <span className="neural-status-dot running" />
            )}
            {n.status === "error" && (
              <span className="neural-status-dot error" />
            )}
            {n.is_sub_agent && <span className="neural-sub-badge">SUB</span>}
          </div>
        ))}
      </div>

      {/* HUD: stats + refresh */}
      <div className="neural-hud">
        <div className="neural-hud-stats">
          {stats && stats.total_requests > 0 ? (
            <>
              <span>{stats.total_requests} {t("dashboard.requests")}</span>
              <span className="neural-hud-sep">·</span>
              <span style={{ color: "#10b981" }}>
                {stats.total_requests > 0
                  ? Math.round((stats.successful / stats.total_requests) * 100)
                  : 0}%
              </span>
              <span className="neural-hud-sep">·</span>
              <span>{(stats.avg_latency_ms / 1000).toFixed(1)}s</span>
            </>
          ) : (
            <span style={{ opacity: 0.4 }}>{t("dashboard.noActivity")}</span>
          )}
        </div>
        <button className="neural-hud-btn" onClick={fetchTopo} title={t("dashboard.refresh")}>
          <IconRefresh size={14} />
        </button>
      </div>

      {/* Empty state — only show if truly no nodes at all (not even dormant) */}
      {(!topoData || topoData.nodes.length === 0) && (
        <div className="neural-empty">
          <div className="neural-empty-ring" />
          <div className="neural-empty-text">{t("dashboard.waiting")}</div>
        </div>
      )}

      {/* Dormant hint — all nodes are dormant, no active work */}
      {topoData && topoData.nodes.length > 0 && topoData.nodes.every((n) => n.status === "dormant") && (
        <div className="neural-dormant-hint">
          {t("dashboard.allDormant")}
        </div>
      )}

      {/* Detail panel */}
      {selectedNode && (
        <div className="neural-detail" onClick={(e) => e.stopPropagation()}>
          <div className="neural-detail-header" style={{ "--nc": selectedNode.color } as React.CSSProperties}>
            <span className="neural-detail-icon">
              {selectedNode.icon.startsWith("svg:") ? (
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                  <path d={SVG_ICON_PATHS[selectedNode.icon.slice(4)] || ""} />
                </svg>
              ) : selectedNode.icon}
            </span>
            <div>
              <div className="neural-detail-name">{selectedNode.name}</div>
              <div className="neural-detail-pid">{selectedNode.profile_id}</div>
            </div>
            <span className={`neural-detail-badge ${selectedNode.status}`}>
              {t(`dashboard.status_${selectedNode.status}`)}
            </span>
          </div>

          {selectedNode.conversation_title && (
            <div className="neural-detail-section">
              <div className="neural-detail-label">{t("dashboard.conversation")}</div>
              <div className="neural-detail-conv">{selectedNode.conversation_title}</div>
            </div>
          )}

          <div className="neural-detail-section">
            <div className="neural-detail-label">{t("dashboard.progress")}</div>
            <div className="neural-detail-meta">
              <span>Iter #{selectedNode.iteration}</span>
              <span>{selectedNode.tools_total} {t("dashboard.tools")}</span>
              <span>{selectedNode.elapsed_s}s</span>
            </div>
          </div>

          {selectedNode.tools_executed.length > 0 && (
            <div className="neural-detail-section">
              <div className="neural-detail-label">{t("dashboard.recentTools")}</div>
              <div className="neural-detail-timeline">
                {selectedNode.tools_executed.map((tool, i) => (
                  <div key={i} className="neural-detail-tool">
                    <span className="neural-detail-tool-dot" style={{ background: selectedNode.color }} />
                    <span>{tool}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button className="neural-detail-close" onClick={() => setSelected(null)}>ESC</button>
        </div>
      )}
    </div>
  );
}

// ── Styles ──────────────────────────────────────────────────────────

function NeuralStyles() {
  return (
    <style>{`
      .neural-dash {
        position: relative;
        width: 100%;
        height: 100%;
        overflow: hidden;
        cursor: default;
        user-select: none;
        background: var(--bg, #fff);
      }
      .neural-canvas {
        position: absolute;
        inset: 0;
        z-index: 0;
      }
      .neural-overlay {
        position: absolute;
        inset: 0;
        z-index: 1;
        pointer-events: none;
      }
      .neural-label {
        position: absolute;
        left: 0; top: 0;
        pointer-events: all;
        cursor: grab;
        display: flex;
        align-items: center;
        gap: 5px;
        padding: 3px 10px 3px 6px;
        border-radius: 20px;
        background: rgba(15,15,25,0.7);
        border: 1px solid rgba(255,255,255,0.06);
        transform-origin: center center;
        margin-left: -40px;
        margin-top: 24px;
        transition: border-color 0.3s, box-shadow 0.3s;
        white-space: nowrap;
      }
      [data-theme="light"] .neural-label,
      :root:not([data-theme="dark"]) .neural-label {
        background: rgba(255,255,255,0.75);
        border: 1px solid rgba(0,0,0,0.08);
      }
      .neural-label.hovered, .neural-label.selected {
        border-color: var(--nc, #7c3aed);
        box-shadow: 0 0 12px rgba(124,58,237,0.2);
      }
      .neural-icon {
        font-size: 16px;
        line-height: 1;
      }
      .neural-name {
        font-size: 11px;
        font-weight: 600;
        opacity: 0.85;
      }
      .neural-status-dot {
        width: 6px; height: 6px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .neural-status-dot.running {
        background: #10b981;
        box-shadow: 0 0 6px #10b981;
        animation: ndPulse 1.5s ease-in-out infinite;
      }
      .neural-status-dot.error {
        background: #ef4444;
        box-shadow: 0 0 6px #ef4444;
      }
      @keyframes ndPulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.5; transform: scale(1.5); }
      }
      .neural-sub-badge {
        font-size: 8px;
        font-weight: 700;
        padding: 1px 4px;
        border-radius: 4px;
        background: rgba(124,58,237,0.15);
        color: #a78bfa;
        letter-spacing: 0.5px;
      }

      /* HUD */
      .neural-hud {
        position: absolute;
        top: 14px; left: 14px; right: 14px;
        z-index: 2;
        display: flex;
        align-items: center;
        justify-content: space-between;
        pointer-events: none;
      }
      .neural-hud-stats {
        pointer-events: all;
        padding: 6px 14px;
        border-radius: 20px;
        background: rgba(0,0,0,0.3);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        font-size: 12px;
        font-weight: 600;
        color: rgba(255,255,255,0.8);
        display: flex;
        align-items: center;
        gap: 6px;
      }
      [data-theme="light"] .neural-hud-stats,
      :root:not([data-theme="dark"]) .neural-hud-stats {
        background: rgba(255,255,255,0.75);
        color: rgba(0,0,0,0.7);
        box-shadow: 0 1px 4px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.06);
      }
      .neural-hud-sep { opacity: 0.3; }
      button.neural-hud-btn {
        pointer-events: all;
        width: 32px; height: 32px;
        padding: 0;
        border-radius: 50%;
        border: none;
        background: rgba(0,0,0,0.3);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        color: rgba(255,255,255,0.7);
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.2s;
        letter-spacing: normal;
      }
      button.neural-hud-btn:hover { background: rgba(0,0,0,0.5); }
      [data-theme="light"] button.neural-hud-btn,
      :root:not([data-theme="dark"]) button.neural-hud-btn {
        background: rgba(255,255,255,0.75);
        color: rgba(0,0,0,0.55);
        box-shadow: 0 1px 4px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.06);
      }
      [data-theme="light"] button.neural-hud-btn:hover,
      :root:not([data-theme="dark"]) button.neural-hud-btn:hover {
        background: rgba(255,255,255,0.92);
        color: rgba(0,0,0,0.75);
      }

      /* Empty state */
      .neural-empty {
        position: absolute;
        inset: 0; z-index: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        pointer-events: none;
      }
      .neural-empty-ring {
        width: 80px; height: 80px;
        border-radius: 50%;
        border: 1px solid rgba(124,58,237,0.12);
        animation: emptyPulse 4s ease-in-out infinite;
      }
      @keyframes emptyPulse {
        0%, 100% { transform: scale(1); opacity: 0.3; }
        50% { transform: scale(1.1); opacity: 0.15; }
      }
      .neural-empty-text {
        margin-top: 14px;
        font-size: 13px;
        font-weight: 500;
        opacity: 0.25;
      }

      /* Detail panel */
      .neural-detail {
        position: absolute;
        top: 60px;
        right: 14px;
        z-index: 3;
        width: 280px;
        max-height: calc(100% - 80px);
        overflow-y: auto;
        border-radius: 16px;
        background: rgba(10,14,26,0.75);
        backdrop-filter: blur(24px);
        -webkit-backdrop-filter: blur(24px);
        border: 1px solid rgba(255,255,255,0.08);
        padding: 18px;
        animation: detailSlide 0.25s ease-out;
      }
      [data-theme="light"] .neural-detail,
      :root:not([data-theme="dark"]) .neural-detail {
        background: rgba(255,255,255,0.8);
        border: 1px solid rgba(0,0,0,0.08);
      }
      @keyframes detailSlide {
        from { transform: translateX(20px); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
      }
      .neural-detail-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 16px;
      }
      .neural-detail-icon {
        font-size: 28px;
        width: 44px; height: 44px;
        border-radius: 12px;
        background: rgba(124,58,237,0.1);
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
      }
      .neural-detail-name {
        font-weight: 700;
        font-size: 15px;
      }
      .neural-detail-pid {
        font-size: 10px;
        opacity: 0.4;
        font-family: monospace;
      }
      .neural-detail-badge {
        margin-left: auto;
        padding: 3px 8px;
        border-radius: 8px;
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .neural-detail-badge.running { background: rgba(16,185,129,0.15); color: #10b981; }
      .neural-detail-badge.idle { background: rgba(107,114,128,0.15); color: #9ca3af; }
      .neural-detail-badge.completed { background: rgba(59,130,246,0.15); color: #60a5fa; }
      .neural-detail-badge.error { background: rgba(239,68,68,0.15); color: #f87171; }
      .neural-detail-badge.dormant { background: rgba(107,114,128,0.08); color: #6b7280; }

      .neural-label.dormant {
        opacity: 0.5;
      }
      .neural-label.dormant .neural-name {
        opacity: 0.6;
      }

      .neural-dormant-hint {
        position: absolute;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 1;
        font-size: 12px;
        font-weight: 500;
        opacity: 0.2;
        pointer-events: none;
        white-space: nowrap;
        animation: dormantFloat 6s ease-in-out infinite;
      }
      @keyframes dormantFloat {
        0%, 100% { transform: translateX(-50%) translateY(0); }
        50% { transform: translateX(-50%) translateY(-4px); }
      }

      .neural-detail-section {
        margin-bottom: 14px;
      }
      .neural-detail-label {
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        opacity: 0.4;
        margin-bottom: 6px;
      }
      .neural-detail-conv {
        font-size: 13px;
        line-height: 1.4;
        opacity: 0.75;
      }
      .neural-detail-meta {
        display: flex;
        gap: 12px;
        font-size: 12px;
        font-weight: 600;
      }
      .neural-detail-meta span {
        padding: 3px 8px;
        border-radius: 6px;
        background: rgba(255,255,255,0.05);
      }
      [data-theme="light"] .neural-detail-meta span,
      :root:not([data-theme="dark"]) .neural-detail-meta span {
        background: rgba(0,0,0,0.04);
      }
      .neural-detail-timeline {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .neural-detail-tool {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 12px;
        opacity: 0.7;
      }
      .neural-detail-tool-dot {
        width: 6px; height: 6px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .neural-detail-close {
        display: block;
        margin: 12px auto 0;
        padding: 4px 16px;
        border-radius: 8px;
        border: 1px solid rgba(255,255,255,0.1);
        background: transparent;
        color: inherit;
        font-size: 11px;
        font-weight: 600;
        opacity: 0.4;
        cursor: pointer;
        transition: opacity 0.2s;
      }
      .neural-detail-close:hover { opacity: 0.8; }
      [data-theme="light"] .neural-detail-close,
      :root:not([data-theme="dark"]) .neural-detail-close {
        border-color: rgba(0,0,0,0.1);
      }
    `}</style>
  );
}
