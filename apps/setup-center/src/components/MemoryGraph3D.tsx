import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { safeFetch } from "../providers";
import { Loader2, X, Zap, Monitor, BatteryLow } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

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
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
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

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let frameId = 0;

    const measure = () => {
      const rect = el.getBoundingClientRect();
      const next = {
        width: Math.max(0, Math.floor(rect.width)),
        height: Math.max(0, Math.floor(rect.height)),
      };
      setDimensions((prev) => (
        prev.width === next.width && prev.height === next.height ? prev : next
      ));
      return next;
    };

    const measureWithRetry = (attempt = 0) => {
      const next = measure();
      if ((next.width === 0 || next.height === 0) && attempt < 12) {
        frameId = window.requestAnimationFrame(() => measureWithRetry(attempt + 1));
      }
    };

    const observer = new ResizeObserver(() => {
      measureWithRetry();
    });
    const handleWindowResize = () => {
      measureWithRetry();
    };

    measureWithRetry();
    observer.observe(el);
    window.addEventListener("resize", handleWindowResize);

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", handleWindowResize);
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
    };
  }, [loading]);

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

  // Adjust force engine parameters for better layout
  useEffect(() => {
    if (fgRef.current) {
      const charge = fgRef.current.d3Force("charge");
      if (charge) charge.strength(-150);
      const link = fgRef.current.d3Force("link");
      if (link) link.distance(60);
    }
  }, [graphData]);

  // After layout stabilizes, fit the graph into view so it doesn't stay biased left.
  useEffect(() => {
    if (!graphData || !fgRef.current || dimensions.width <= 0 || dimensions.height <= 0) return;
    const timer = setTimeout(() => {
      try {
        fgRef.current?.zoomToFit?.(600, 80);
      } catch {
        /* ignore fit errors */
      }
    }, 900);
    return () => clearTimeout(timer);
  }, [graphData, dimensions.width, dimensions.height, quality]);

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
    <div ref={containerRef} className="memory-graph-surface relative w-full h-full bg-slate-950 overflow-hidden flex flex-col">
      <style>{`
        .memory-graph-surface .graph-viewport {
          width: 100%;
          height: 100%;
          min-width: 0;
          min-height: 0;
        }
        .memory-graph-surface .graph-viewport > div {
          width: 100% !important;
          height: 100% !important;
          min-width: 0 !important;
          min-height: 0 !important;
        }
        .memory-graph-surface .graph-viewport canvas {
          display: block !important;
          width: 100% !important;
          height: 100% !important;
        }
      `}</style>
      {/* Legend + Quality selector */}
      <div className="absolute top-3 left-3 right-3 z-10 flex justify-between items-start pointer-events-none">
        <div className="flex flex-wrap gap-3 items-center bg-slate-950/80 backdrop-blur-md border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-300 pointer-events-auto shadow-sm max-w-[60%]">
          {Object.entries(NODE_COLORS).map(([type, color]) => (
            <span key={type} className="flex items-center gap-1.5 shrink-0">
              <span className="w-2 h-2 rounded-full" style={{ background: color }} />
              {NODE_TYPE_LABELS[type] || type}
            </span>
          ))}
          <span className="border-l border-slate-700 pl-3 text-slate-400 shrink-0">
            {graphData.meta.total_nodes} 节点 · {graphData.meta.total_edges} 边 · {graphData.meta.mode}
          </span>
          {matchedNodeIds && (
            <span className="border-l border-slate-700 pl-3 font-semibold text-amber-500 shrink-0">
              搜索匹配: {matchedNodeIds.size} 个节点
            </span>
          )}
        </div>
        <div className="flex gap-1 bg-slate-950/80 backdrop-blur-md border border-slate-800 rounded-lg p-1 pointer-events-auto shadow-sm shrink-0">
          <TooltipProvider delayDuration={200}>
            {QUALITY_ORDER.map((q) => {
              const Icon = QUALITY_ICONS[q];
              const active = quality === q;
              return (
                <Tooltip key={q}>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => handleQualityChange(q)}
                      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs transition-colors ${
                        active ? "bg-indigo-500/20 text-indigo-400 font-medium" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                      }`}
                    >
                      <Icon size={12} />
                      {QUALITY_LABELS[q]}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" className="text-xs">
                    {q === "high" ? "高画质：开启发光特效和粒子动画" : q === "medium" ? "中画质：关闭发光特效，保留少量粒子" : "低画质：关闭所有特效，最流畅"}
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </TooltipProvider>
        </div>
      </div>

      <div className="graph-viewport flex-1 w-full h-full min-w-0 min-h-0 relative">
        {dimensions.width > 0 && dimensions.height > 0 ? (
          <ForceGraph3D
            key={`${dimensions.width}x${dimensions.height}`}
            ref={fgRef}
            graphData={graphData}
            width={dimensions.width}
            height={dimensions.height}
            backgroundColor="#020617"
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
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            <Loader2 size={20} className="animate-spin text-indigo-400" />
            <span className="ml-2">正在初始化图谱画布...</span>
          </div>
        )}
      </div>

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
    <Card className="absolute top-0 right-0 bottom-0 w-80 rounded-none border-y-0 border-r-0 border-l border-slate-800 bg-slate-950/95 backdrop-blur-md overflow-y-auto z-20 shadow-2xl animate-in slide-in-from-right-full duration-200">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
        <Badge variant="outline" style={{ backgroundColor: `${color}15`, color, borderColor: `${color}30` }}>
          {NODE_TYPE_LABELS[node.node_type] || node.node_type}
        </Badge>
        <Button variant="ghost" size="icon-sm" onClick={onClose} className="text-slate-400 hover:text-slate-100 hover:bg-slate-800">
          <X size={16} />
        </Button>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="text-sm leading-relaxed text-slate-200 break-words whitespace-pre-wrap">
          {node.content}
        </div>

        <div className="space-y-2 text-xs text-slate-400">
          {node.occurred_at && (
            <div className="flex gap-2">
              <span className="text-slate-500 w-12 shrink-0">时间:</span>
              <span className="text-slate-300">{new Date(node.occurred_at).toLocaleString("zh-CN")}</span>
            </div>
          )}
          <div className="flex gap-2">
            <span className="text-slate-500 w-12 shrink-0">重要性:</span>
            <span className="font-semibold" style={{ color }}>{node.importance.toFixed(2)}</span>
          </div>
          {node.action_category && (
            <div className="flex gap-2">
              <span className="text-slate-500 w-12 shrink-0">动作:</span>
              <span className="text-slate-300">{node.action_category}</span>
            </div>
          )}
          {node.project && (
            <div className="flex gap-2">
              <span className="text-slate-500 w-12 shrink-0">项目:</span>
              <span className="text-slate-300">{node.project}</span>
            </div>
          )}
        </div>

        {node.entities.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">实体</div>
            <div className="flex flex-wrap gap-1.5">
              {node.entities.map((e, i) => (
                <Badge key={i} variant="outline" className="bg-indigo-500/10 text-indigo-400 border-indigo-500/20 text-[10px] px-2 py-0">
                  {e.name}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {related.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
              关联节点 ({related.length})
            </div>
            <div className="space-y-1.5">
              {related.map((r, i) => (
                <div
                  key={i}
                  onClick={() => onNavigate(r.id)}
                  className="flex items-center gap-2.5 px-3 py-2 rounded-md bg-slate-900/50 border border-slate-800/50 cursor-pointer hover:bg-slate-800 hover:border-slate-700 transition-colors"
                >
                  <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: DIMENSION_COLORS[r.dimension] || "#666" }} />
                  <span className="text-xs text-slate-300 truncate">{r.edge_type}</span>
                  <span className="text-[10px] text-slate-500 font-mono ml-auto shrink-0">
                    {r.id.slice(0, 8)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
