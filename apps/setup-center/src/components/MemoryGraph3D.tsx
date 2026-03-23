import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { safeFetch } from "../providers";
import { Loader2, X, Zap, Monitor, BatteryLow } from "lucide-react";
import { Button } from "@/components/ui/button";

export type GraphQuality = "high" | "medium" | "low";

const QUALITY_PRESETS: Record<GraphQuality, {
  bloom: boolean;
  particles: number;
  particleWidth: number;
  alphaDecay: number;
  warmupTicks: number;
  cooldownTicks: number;
}> = {
  high:   { bloom: true,  particles: 2, particleWidth: 1.2, alphaDecay: 0.02, warmupTicks: 80, cooldownTicks: 100 },
  medium: { bloom: false, particles: 1, particleWidth: 0.8, alphaDecay: 0.04, warmupTicks: 40, cooldownTicks: 60 },
  low:    { bloom: false, particles: 0, particleWidth: 0,   alphaDecay: 0.06, warmupTicks: 20, cooldownTicks: 30 },
};

const QUALITY_LABELS: Record<GraphQuality, string> = { high: "高", medium: "中", low: "低" };
const QUALITY_ICONS: Record<GraphQuality, typeof Zap> = { high: Zap, medium: Monitor, low: BatteryLow };
const QUALITY_ORDER: GraphQuality[] = ["high", "medium", "low"];

function loadQuality(): GraphQuality {
  const v = localStorage.getItem("memoryGraph3dQuality");
  if (v === "high" || v === "medium" || v === "low") return v;
  return "high";
}

type GraphNode = {
  id: string;
  content: string;
  node_type: string;
  importance: number;
  entities: { name: string; type: string }[];
  action_category: string;
  occurred_at: string | null;
  session_id: string;
  project: string;
  group: string;
  x?: number;
  y?: number;
  z?: number;
};

type GraphLink = {
  source: string | GraphNode;
  target: string | GraphNode;
  edge_type: string;
  dimension: string;
  weight: number;
};

type GraphData = {
  nodes: GraphNode[];
  links: GraphLink[];
  meta: { total_nodes: number; total_edges: number; mode: string };
};

const NODE_COLORS: Record<string, string> = {
  EVENT: "#3b82f6",
  FACT: "#10b981",
  DECISION: "#f59e0b",
  GOAL: "#a855f7",
};

const DIMENSION_COLORS: Record<string, string> = {
  temporal: "#06b6d4",
  causal: "#ef4444",
  entity: "#10b981",
  action: "#f59e0b",
  context: "#6b7280",
};

const NODE_TYPE_LABELS: Record<string, string> = {
  EVENT: "事件",
  FACT: "事实",
  DECISION: "决策",
  GOAL: "目标",
};

interface Props {
  apiBaseUrl?: string;
  searchQuery?: string;
  quality?: GraphQuality;
  onQualityChange?: (q: GraphQuality) => void;
}

export function MemoryGraph3D({ apiBaseUrl = "", searchQuery = "", quality: qualityProp, onQualityChange }: Props) {
  // ForceGraph3D ref type doesn't export cleanly; use its expected shape
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const bloomRef = useRef<UnrealBloomPass | null>(null);
  const bloomAdded = useRef(false);

  const [internalQuality, setInternalQuality] = useState<GraphQuality>(loadQuality);
  const quality = qualityProp ?? internalQuality;
  const preset = QUALITY_PRESETS[quality];

  const handleQualityChange = useCallback((q: GraphQuality) => {
    localStorage.setItem("memoryGraph3dQuality", q);
    setInternalQuality(q);
    onQualityChange?.(q);
  }, [onQualityChange]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setDimensions({ width: Math.floor(width), height: Math.floor(height) });
      }
    });
    obs.observe(el);
    setDimensions({ width: el.clientWidth, height: el.clientHeight });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await safeFetch(`${apiBaseUrl}/api/memories/graph?limit=500`);
        const data: GraphData = await res.json();
        if (!cancelled) setGraphData(data);
      } catch {
        if (!cancelled) setGraphData({ nodes: [], links: [], meta: { total_nodes: 0, total_edges: 0, mode: "error" } });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [apiBaseUrl]);

  // Bloom post-processing — only when quality = high
  useEffect(() => {
    if (!preset.bloom) {
      if (bloomRef.current) {
        bloomRef.current.enabled = false;
      }
      return;
    }
    if (bloomRef.current) {
      bloomRef.current.enabled = true;
      return;
    }
    if (!fgRef.current || bloomAdded.current) return;
    const timer = setTimeout(() => {
      try {
        if (!fgRef.current) return;
        const renderer = fgRef.current.renderer?.();
        if (!renderer) return;
        const bloom = new UnrealBloomPass(
          new THREE.Vector2(dimensions.width, dimensions.height),
          1.2, 0.5, 0.15,
        );
        const composer = fgRef.current.postProcessingComposer?.();
        if (composer) {
          composer.addPass(bloom);
          bloomRef.current = bloom;
          bloomAdded.current = true;
        } else {
          bloom.dispose();
        }
      } catch { /* bloom unavailable */ }
    }, 500);
    return () => clearTimeout(timer);
  }, [graphData, preset.bloom]);

  // Update bloom resolution on container resize
  useEffect(() => {
    if (bloomRef.current) {
      bloomRef.current.resolution.set(dimensions.width, dimensions.height);
    }
  }, [dimensions]);

  // Track node meshes for hover-dimming
  const nodeMeshes = useRef<Map<string, THREE.Mesh>>(new Map());

  const neighborSet = useMemo(() => {
    if (!hoveredNode || !graphData) return new Set<string>();
    const s = new Set<string>();
    s.add(hoveredNode.id);
    for (const link of graphData.links) {
      const srcId = typeof link.source === "string" ? link.source : link.source.id;
      const tgtId = typeof link.target === "string" ? link.target : link.target.id;
      if (srcId === hoveredNode.id) s.add(tgtId);
      if (tgtId === hoveredNode.id) s.add(srcId);
    }
    return s;
  }, [hoveredNode, graphData]);

  // Shared materials per node type
  const materials = useMemo(() => {
    const m: Record<string, THREE.MeshBasicMaterial> = {};
    for (const [type, color] of Object.entries(NODE_COLORS)) {
      m[type] = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9 });
    }
    m["_default"] = new THREE.MeshBasicMaterial({ color: "#6b7280", transparent: true, opacity: 0.7 });
    return m;
  }, []);

  // Dispose shared materials on unmount
  useEffect(() => {
    return () => {
      Object.values(materials).forEach((mat) => mat.dispose());
    };
  }, [materials]);

  // Clear stale node mesh refs and dispose cloned materials when graph data changes
  useEffect(() => {
    nodeMeshes.current.forEach((mesh) => {
      const mat = mesh.material as THREE.MeshBasicMaterial;
      if (mat && typeof mat.dispose === "function") {
        mat.dispose();
      }
    });
    nodeMeshes.current.clear();
  }, [graphData]);

  // Cache geometries by bucketed radius to reduce GPU allocations
  const geoCache = useRef<Map<number, THREE.SphereGeometry>>(new Map());
  useEffect(() => {
    return () => {
      geoCache.current.forEach((g) => g.dispose());
      geoCache.current.clear();
    };
  }, []);

  // Track sprite materials for cleanup
  const spriteMats = useRef<THREE.SpriteMaterial[]>([]);
  useEffect(() => {
    return () => {
      spriteMats.current.forEach((m) => m.dispose());
      spriteMats.current = [];
    };
  }, []);

  const nodeThreeObject = useCallback((node: GraphNode) => {
    const radius = 1.5 + node.importance * 4;
    const bucketedRadius = Math.round(radius * 2) / 2;
    let geo = geoCache.current.get(bucketedRadius);
    if (!geo) {
      geo = new THREE.SphereGeometry(bucketedRadius, 10, 10);
      geoCache.current.set(bucketedRadius, geo);
    }
    const mat = materials[node.node_type] || materials["_default"];
    const nodeMat = mat.clone();
    const mesh = new THREE.Mesh(geo, nodeMat);
    mesh.userData = { nodeType: node.node_type };
    nodeMeshes.current.set(node.id, mesh);

    if (node.importance >= 0.6) {
      const spriteMat = new THREE.SpriteMaterial({
        color: NODE_COLORS[node.node_type] || "#6b7280",
        transparent: true,
        opacity: 0.25,
        blending: THREE.AdditiveBlending,
      });
      spriteMats.current.push(spriteMat);
      const sprite = new THREE.Sprite(spriteMat);
      sprite.scale.set(bucketedRadius * 4, bucketedRadius * 4, 1);
      mesh.add(sprite);
    }

    return mesh;
  }, [materials]);

  const handleNodeClick = useCallback((node: GraphNode) => {
    setSelectedNode(node);
    if (fgRef.current) {
      const dist = 80;
      const coords = {
        x: (node.x || 0) + dist,
        y: (node.y || 0) + dist * 0.3,
        z: (node.z || 0) + dist,
      };
      fgRef.current.cameraPosition(coords, { x: node.x, y: node.y, z: node.z }, 800);
    }
  }, []);

  const handleNodeHover = useCallback((node: GraphNode | null) => {
    setHoveredNode(node);
    if (containerRef.current) {
      containerRef.current.style.cursor = node ? "pointer" : "default";
    }
  }, []);

  // Search: compute matched node IDs
  const searchLower = searchQuery.trim().toLowerCase();
  const matchedNodeIds = useMemo(() => {
    if (!searchLower || searchLower.length < 2 || !graphData) return null;
    const matched = new Set<string>();
    for (const node of graphData.nodes) {
      const haystack = [
        node.content,
        node.project,
        node.action_category,
        ...node.entities.map((e) => e.name),
      ].join(" ").toLowerCase();
      if (haystack.includes(searchLower)) {
        matched.add(node.id);
      }
    }
    return matched.size > 0 ? matched : null;
  }, [searchLower, graphData]);

  // Auto-focus camera on first search match
  const prevSearch = useRef("");
  useEffect(() => {
    if (!matchedNodeIds || !graphData || !fgRef.current) return;
    if (searchLower === prevSearch.current) return;
    prevSearch.current = searchLower;
    const firstId = matchedNodeIds.values().next().value;
    const target = graphData.nodes.find((n) => n.id === firstId);
    if (target && target.x != null) {
      const dist = 120;
      fgRef.current.cameraPosition(
        { x: (target.x || 0) + dist, y: (target.y || 0) + dist * 0.3, z: (target.z || 0) + dist },
        { x: target.x, y: target.y, z: target.z },
        600,
      );
    }
  }, [matchedNodeIds, searchLower, graphData]);

  // Apply hover-dimming and search highlighting on material opacity
  useEffect(() => {
    nodeMeshes.current.forEach((mesh, id) => {
      const mat = mesh.material as THREE.MeshBasicMaterial;
      if (hoveredNode) {
        mat.opacity = neighborSet.has(id) ? 0.9 : 0.1;
      } else if (matchedNodeIds) {
        mat.opacity = matchedNodeIds.has(id) ? 1.0 : 0.08;
      } else {
        mat.opacity = 0.9;
      }
    });
  }, [hoveredNode, neighborSet, matchedNodeIds]);

  const linkColor = useCallback((link: GraphLink) => {
    return DIMENSION_COLORS[link.dimension] || "#444";
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 size={24} className="animate-spin text-indigo-500" />
        <span className="ml-2 text-sm text-muted-foreground">加载记忆图谱...</span>
      </div>
    );
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <div className="text-lg font-semibold mb-1">暂无记忆图谱数据</div>
        <div className="text-xs opacity-60">
          对话后将自动生成关系型记忆（当前记忆模式需为 mode2 或 auto）
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%", position: "relative", background: "#0a0a12", borderRadius: 8, overflow: "hidden" }}>
      {/* Legend + Quality selector */}
      <div style={{
        position: "absolute", top: 10, left: 10, right: 10, zIndex: 10,
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <div style={{
          background: "rgba(10,10,18,0.85)", borderRadius: 8, padding: "8px 12px",
          display: "flex", gap: 12, fontSize: 11, color: "#ccc",
        }}>
          {Object.entries(NODE_COLORS).map(([type, color]) => (
            <span key={type} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, display: "inline-block" }} />
              {NODE_TYPE_LABELS[type] || type}
            </span>
          ))}
          <span style={{ borderLeft: "1px solid #333", paddingLeft: 10, color: "#888" }}>
            {graphData.meta.total_nodes} 节点 · {graphData.meta.total_edges} 边 · {graphData.meta.mode}
          </span>
          {matchedNodeIds && (
            <span style={{ borderLeft: "1px solid #333", paddingLeft: 10, color: "#f59e0b", fontWeight: 600 }}>
              搜索匹配: {matchedNodeIds.size} 个节点
            </span>
          )}
        </div>
        <div style={{
          background: "rgba(10,10,18,0.85)", borderRadius: 8, padding: "4px",
          display: "flex", gap: 2,
        }}>
          {QUALITY_ORDER.map((q) => {
            const Icon = QUALITY_ICONS[q];
            const active = quality === q;
            return (
              <button
                key={q}
                onClick={() => handleQualityChange(q)}
                title={`画质: ${QUALITY_LABELS[q]}`}
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  padding: "4px 10px", borderRadius: 6, border: "none",
                  fontSize: 11, fontWeight: active ? 600 : 400, cursor: "pointer",
                  background: active ? "rgba(99,102,241,0.3)" : "transparent",
                  color: active ? "#a5b4fc" : "#888",
                  transition: "all 0.15s",
                }}
              >
                <Icon size={12} />
                {QUALITY_LABELS[q]}
              </button>
            );
          })}
        </div>
      </div>

      <ForceGraph3D
        ref={fgRef}
        graphData={graphData}
        width={dimensions.width}
        height={dimensions.height}
        backgroundColor="#0a0a12"
        nodeThreeObject={nodeThreeObject}
        nodeThreeObjectExtend={false}
        nodeLabel={(node: any) => `${node.content?.slice(0, 60) || node.id}`}
        onNodeClick={handleNodeClick as any}
        onNodeHover={handleNodeHover as any}
        linkColor={linkColor as any}
        linkWidth={(link: any) => Math.max(0.3, (link.weight || 0.5) * 1.5)}
        linkOpacity={0.4}
        linkDirectionalParticles={preset.particles}
        linkDirectionalParticleWidth={preset.particleWidth}
        linkDirectionalParticleSpeed={0.005}
        d3AlphaDecay={preset.alphaDecay}
        d3VelocityDecay={0.3}
        warmupTicks={preset.warmupTicks}
        cooldownTicks={preset.cooldownTicks}
        enablePointerInteraction={true}
      />

      {/* Node detail panel */}
      {selectedNode && (
        <NodeDetailPanel
          node={selectedNode}
          links={graphData.links}
          onClose={() => setSelectedNode(null)}
          onNavigate={(id) => {
            const target = graphData.nodes.find((n) => n.id === id);
            if (target) handleNodeClick(target);
          }}
        />
      )}
    </div>
  );
}

function NodeDetailPanel({
  node,
  links,
  onClose,
  onNavigate,
}: {
  node: GraphNode;
  links: GraphLink[];
  onClose: () => void;
  onNavigate: (id: string) => void;
}) {
  const related = useMemo(() => {
    const result: { id: string; edge_type: string; dimension: string }[] = [];
    for (const link of links) {
      const srcId = typeof link.source === "string" ? link.source : link.source.id;
      const tgtId = typeof link.target === "string" ? link.target : link.target.id;
      if (srcId === node.id) {
        result.push({ id: tgtId, edge_type: link.edge_type, dimension: link.dimension });
      } else if (tgtId === node.id) {
        result.push({ id: srcId, edge_type: link.edge_type, dimension: link.dimension });
      }
    }
    return result.slice(0, 15);
  }, [node, links]);

  const color = NODE_COLORS[node.node_type] || "#6b7280";

  return (
    <div style={{
      position: "absolute", top: 0, right: 0, bottom: 0, width: 320,
      background: "rgba(15,15,25,0.95)", borderLeft: "1px solid #222",
      overflowY: "auto", zIndex: 20, padding: 16,
      animation: "slideIn 0.2s ease-out",
    }}>
      <style>{`@keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }`}</style>

      <div className="flex items-center justify-between mb-4">
        <span style={{
          padding: "3px 10px", borderRadius: 12, fontSize: 12, fontWeight: 600,
          background: `${color}20`, color, border: `1px solid ${color}40`,
        }}>
          {NODE_TYPE_LABELS[node.node_type] || node.node_type}
        </span>
        <Button variant="ghost" size="icon-sm" onClick={onClose} className="text-gray-400 hover:text-white">
          <X size={16} />
        </Button>
      </div>

      <div style={{ fontSize: 13, lineHeight: 1.7, color: "#e0e0e0", marginBottom: 16, wordBreak: "break-word" }}>
        {node.content}
      </div>

      <div style={{ fontSize: 11, color: "#888", display: "flex", flexDirection: "column", gap: 6, marginBottom: 16 }}>
        {node.occurred_at && (
          <div>
            <span style={{ color: "#666" }}>时间: </span>
            {new Date(node.occurred_at).toLocaleString("zh-CN")}
          </div>
        )}
        <div>
          <span style={{ color: "#666" }}>重要性: </span>
          <span style={{ color, fontWeight: 600 }}>{node.importance.toFixed(2)}</span>
        </div>
        {node.action_category && (
          <div><span style={{ color: "#666" }}>动作: </span>{node.action_category}</div>
        )}
        {node.project && (
          <div><span style={{ color: "#666" }}>项目: </span>{node.project}</div>
        )}
      </div>

      {node.entities.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "#888", marginBottom: 6 }}>实体</div>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {node.entities.map((e, i) => (
              <span key={i} style={{
                padding: "2px 8px", borderRadius: 10, fontSize: 10,
                background: "rgba(99,102,241,0.15)", color: "#818cf8",
                border: "1px solid rgba(99,102,241,0.25)",
              }}>
                {e.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {related.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: "#888", marginBottom: 6 }}>
            关联节点 ({related.length})
          </div>
          {related.map((r, i) => (
            <div
              key={i}
              onClick={() => onNavigate(r.id)}
              style={{
                padding: "6px 8px", borderRadius: 6, fontSize: 11,
                cursor: "pointer", marginBottom: 4,
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.06)",
                transition: "background 0.15s",
                display: "flex", alignItems: "center", gap: 6,
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.08)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.03)")}
            >
              <span style={{
                width: 6, height: 6, borderRadius: "50%",
                background: DIMENSION_COLORS[r.dimension] || "#666",
                flexShrink: 0,
              }} />
              <span style={{ color: "#aaa" }}>{r.edge_type}</span>
              <span style={{ color: "#666", marginLeft: "auto", fontFamily: "monospace", fontSize: 10 }}>
                {r.id.slice(0, 8)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
