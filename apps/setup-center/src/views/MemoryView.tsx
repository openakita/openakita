import React, { useEffect, useState, useCallback } from "react";
import {
  IconRefresh, IconTrash, IconEdit, IconCheck, IconX,
  IconSearch, IconBrain, IconLoader,
} from "../icons";
import { safeFetch } from "../providers";
import { ConfirmDialog } from "../components/ConfirmDialog";

type MemoryItem = {
  id: string;
  type: string;
  priority: string;
  content: string;
  source: string;
  subject: string;
  predicate: string;
  tags: string[];
  importance_score: number;
  confidence: number;
  access_count: number;
  created_at: string | null;
  updated_at: string | null;
  last_accessed_at: string | null;
  expires_at: string | null;
};

type Stats = {
  total: number;
  by_type: Record<string, number>;
  avg_score: number;
};

type ReviewResult = {
  deleted: number;
  updated: number;
  merged: number;
  kept: number;
  errors: number;
};

const TYPE_LABELS: Record<string, string> = {
  fact: "事实",
  preference: "偏好",
  skill: "技能",
  rule: "规则",
  error: "经验教训",
  experience: "经验",
  persona_trait: "人格特征",
  context: "上下文",
};

const TYPE_COLORS: Record<string, string> = {
  fact: "#3b82f6",
  preference: "#8b5cf6",
  skill: "#10b981",
  rule: "#f59e0b",
  error: "#ef4444",
  experience: "#06b6d4",
  persona_trait: "#ec4899",
  context: "#6b7280",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return iso;
  }
}

interface Props {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}

export function MemoryView({ serviceRunning, apiBaseUrl = "" }: Props) {
  const API_BASE = apiBaseUrl;
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editScore, setEditScore] = useState(0);
  const [reviewing, setReviewing] = useState(false);
  const [reviewResult, setReviewResult] = useState<ReviewResult | null>(null);
  const [error, setError] = useState("");
  const [showReviewConfirm, setShowReviewConfirm] = useState(false);
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth <= 768);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const loadMemories = useCallback(async () => {
    if (!serviceRunning) return;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (searchQuery) params.set("search", searchQuery);
      if (filterType) params.set("type", filterType);
      const res = await safeFetch(`${API_BASE}/api/memories?${params}`);
      const data = await res.json();
      setMemories(data.memories || []);
    } catch (e: any) {
      setError(e.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, [serviceRunning, searchQuery, filterType]);

  const loadStats = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${API_BASE}/api/memories/stats`);
      setStats(await res.json());
    } catch { /* ignore */ }
  }, [serviceRunning]);

  useEffect(() => {
    loadMemories();
    loadStats();
  }, [loadMemories, loadStats]);

  const doDelete = async (id: string) => {
    try {
      await safeFetch(`${API_BASE}/api/memories/${id}`, { method: "DELETE" });
      setMemories(prev => prev.filter(m => m.id !== id));
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n; });
      loadStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = (id: string) => {
    const mem = memories.find(m => m.id === id);
    const preview = mem ? (mem.content.length > 40 ? mem.content.slice(0, 40) + "..." : mem.content) : "";
    setConfirmDialog({
      message: `确定删除这条记忆？\n\n"${preview}"`,
      onConfirm: () => doDelete(id),
    });
  };

  const doBatchDelete = useCallback(async (ids: string[]) => {
    try {
      await safeFetch(`${API_BASE}/api/memories/batch-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      setMemories(prev => prev.filter(m => !new Set(ids).has(m.id)));
      setSelected(new Set());
      loadStats();
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }, [API_BASE, loadStats]);

  const handleBatchDelete = () => {
    if (selected.size === 0) return;
    const ids = Array.from(selected);
    setConfirmDialog({
      message: `确定删除选中的 ${ids.length} 条记忆？`,
      onConfirm: () => doBatchDelete(ids),
    });
  };

  const handleUpdate = async (id: string) => {
    try {
      await safeFetch(`${API_BASE}/api/memories/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editContent, importance_score: editScore }),
      });
      setMemories(prev => prev.map(m =>
        m.id === id ? { ...m, content: editContent, importance_score: editScore } : m
      ));
      setEditingId(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const startEdit = (m: MemoryItem) => {
    setEditingId(m.id);
    setEditContent(m.content);
    setEditScore(m.importance_score);
  };

  const handleReviewConfirm = () => setShowReviewConfirm(true);

  const handleReview = async () => {
    setShowReviewConfirm(false);
    setReviewing(true);
    setReviewResult(null);
    setError("");
    try {
      const res = await safeFetch(`${API_BASE}/api/memories/review`, {
        method: "POST",
        signal: AbortSignal.timeout(180_000),
      });
      const data = await res.json();
      const review: ReviewResult = data?.review ?? data;
      if (review && typeof review.deleted === "number") {
        setReviewResult(review);
        loadMemories();
        loadStats();
      } else {
        setError("审查完成，但返回数据格式异常");
      }
    } catch (e: any) {
      setError(e.message || "审查请求失败");
    } finally {
      setReviewing(false);
    }
  };

  const toggleSelect = (id: string) => {
    setSelected(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const selectAll = () => {
    if (selected.size === memories.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(memories.map(m => m.id)));
    }
  };

  if (!serviceRunning) {
    return (
      <div className="card" style={{ textAlign: "center", padding: 60, color: "var(--muted)" }}>
        <IconBrain size={32} />
        <p style={{ marginTop: 12, fontSize: 15 }}>请先启动服务后使用记忆管理</p>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: isMobile ? 10 : 16 }}>
      {/* Stats bar */}
      {stats && (
        <div style={{
          display: "grid",
          gridTemplateColumns: isMobile
            ? "repeat(auto-fill, minmax(70px, 1fr))"
            : `repeat(${2 + Object.keys(stats.by_type).length}, 1fr)`,
          gap: isMobile ? 6 : 10,
        }}>
          <div className="card" style={{ margin: 0, padding: isMobile ? "8px 6px" : "10px 12px", textAlign: "center", display: "flex", flexDirection: "column", justifyContent: "center" }}>
            <div style={{ fontSize: isMobile ? 18 : 22, fontWeight: 700, color: "var(--text)" }}>{stats.total}</div>
            <div style={{ fontSize: isMobile ? 10 : 11, color: "var(--muted)" }}>总记忆数</div>
          </div>
          <div className="card" style={{ margin: 0, padding: isMobile ? "8px 6px" : "10px 12px", textAlign: "center", display: "flex", flexDirection: "column", justifyContent: "center" }}>
            <div style={{ fontSize: isMobile ? 18 : 22, fontWeight: 700, color: "var(--text)" }}>{stats.avg_score}</div>
            <div style={{ fontSize: isMobile ? 10 : 11, color: "var(--muted)" }}>平均分数</div>
          </div>
          {Object.entries(stats.by_type).map(([t, c]) => (
            <div key={t} className="card" style={{ margin: 0, padding: isMobile ? "8px 6px" : "10px 12px", textAlign: "center", display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div style={{ fontSize: isMobile ? 18 : 22, fontWeight: 700, color: TYPE_COLORS[t] || "var(--text)" }}>{c}</div>
              <div style={{ fontSize: isMobile ? 10 : 11, color: "var(--muted)" }}>{TYPE_LABELS[t] || t}</div>
            </div>
          ))}
        </div>
      )}

      {/* Toolbar */}
      <div className="card" style={{ padding: isMobile ? "10px 12px" : "12px 16px" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ position: "relative", flex: 1, minWidth: isMobile ? 140 : 200 }}>
            <span style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", color: "var(--muted)", pointerEvents: "none", display: "flex" }}>
              <IconSearch size={14} />
            </span>
            <input
              type="text"
              placeholder="搜索记忆内容..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && loadMemories()}
              style={{
                width: "100%", padding: "6px 8px 6px 28px", border: "1px solid var(--line)",
                borderRadius: 6, background: "var(--bg)", color: "var(--text)", fontSize: 13,
              }}
            />
          </div>

          <select
            value={filterType}
            onChange={e => setFilterType(e.target.value)}
            style={{
              padding: "6px 8px", border: "1px solid var(--line)",
              borderRadius: 6, background: "var(--bg)", color: "var(--text)", fontSize: 13,
              maxWidth: isMobile ? 100 : undefined,
            }}
          >
            <option value="">全部类型</option>
            {Object.entries(TYPE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>

          <button
            onClick={loadMemories}
            disabled={loading}
            style={{
              display: "flex", alignItems: "center", gap: 4, padding: "6px 12px",
              border: "1px solid var(--line)", borderRadius: 6,
              background: "var(--bg)", color: "var(--text)", cursor: "pointer", fontSize: 13,
            }}
          >
            <IconRefresh size={14} />{!isMobile && " 刷新"}
          </button>

          {selected.size > 0 && (
            <button
              onClick={handleBatchDelete}
              style={{
                display: "flex", alignItems: "center", gap: 4, padding: "6px 12px",
                border: "1px solid #ef4444", borderRadius: 6,
                background: "#ef4444", color: "#fff", cursor: "pointer", fontSize: 13,
              }}
            >
              <IconTrash size={14} /> 删除 {selected.size} 条
            </button>
          )}

          <button
            onClick={handleReviewConfirm}
            disabled={reviewing}
            style={{
              display: "flex", alignItems: "center", gap: 4, padding: "6px 12px",
              border: "none", borderRadius: 6,
              background: "linear-gradient(135deg, #6366f1, #8b5cf6)", color: "#fff",
              cursor: reviewing ? "wait" : "pointer", fontSize: 13, fontWeight: 500,
              opacity: reviewing ? 0.7 : 1,
            }}
          >
            {reviewing ? <IconLoader size={14} /> : <IconBrain size={14} />}
            {reviewing ? "审查中..." : isMobile ? "LLM 审查" : "LLM 智能审查"}
          </button>
        </div>
      </div>

      {/* Review confirm modal */}
      {showReviewConfirm && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 9999,
            background: "rgba(0,0,0,0.45)", display: "flex",
            alignItems: "center", justifyContent: "center",
            padding: isMobile ? 16 : 0,
          }}
          onClick={() => setShowReviewConfirm(false)}
        >
          <div
            style={{
              background: "var(--card, #fff)", borderRadius: 12,
              padding: "24px 28px", maxWidth: 400, width: "90%",
              boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text)", marginBottom: 10 }}>
              启动 LLM 智能审查
            </div>
            <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6, marginBottom: 20 }}>
              将由大模型逐条审查所有记忆，删除垃圾、合并重复。此操作可能需要较长时间，请耐心等待。
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                onClick={() => setShowReviewConfirm(false)}
                style={{
                  padding: "6px 16px", borderRadius: 6, fontSize: 13,
                  border: "1px solid var(--line)", background: "var(--bg)",
                  color: "var(--text)", cursor: "pointer",
                }}
              >
                取消
              </button>
              <button
                onClick={handleReview}
                style={{
                  padding: "6px 16px", borderRadius: 6, fontSize: 13,
                  border: "none", cursor: "pointer", fontWeight: 500,
                  background: "linear-gradient(135deg, #6366f1, #8b5cf6)", color: "#fff",
                }}
              >
                确认审查
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Review result toast */}
      {reviewResult && (
        <div
          style={{
            padding: "10px 16px", borderRadius: 8,
            background: "linear-gradient(135deg, rgba(99,102,241,0.1), rgba(139,92,246,0.1))",
            border: "1px solid rgba(99,102,241,0.3)",
            fontSize: 13, color: "var(--text)",
          }}
        >
          LLM 审查完成：
          <strong style={{ color: "#ef4444" }}> 删除 {reviewResult.deleted}</strong>，
          <strong style={{ color: "#f59e0b" }}> 更新 {reviewResult.updated}</strong>，
          <strong style={{ color: "#6366f1" }}> 合并 {reviewResult.merged}</strong>，
          <strong style={{ color: "#10b981" }}> 保留 {reviewResult.kept}</strong>
          {reviewResult.errors > 0 && <span style={{ color: "#ef4444" }}>，错误 {reviewResult.errors}</span>}
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{ padding: "8px 12px", borderRadius: 6, background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#ef4444", fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* Memory list */}
      {isMobile ? (
        /* ── Mobile: card-based layout ── */
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {loading ? (
            <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
              <IconLoader size={20} /> 加载中...
            </div>
          ) : memories.length === 0 ? (
            <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
              暂无记忆数据
            </div>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 4px" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)", cursor: "pointer" }}>
                  <input type="checkbox" checked={selected.size === memories.length && memories.length > 0} onChange={selectAll} style={{ width: 16, height: 16, flexShrink: 0 }} />
                  全选 ({memories.length})
                </label>
              </div>
              {memories.map(m => (
                <div
                  key={m.id}
                  className="card"
                  style={{
                    margin: 0, padding: "10px 12px",
                    background: selected.has(m.id) ? "rgba(99,102,241,0.06)" : undefined,
                    transition: "background 0.15s",
                  }}
                >
                  {/* Header row: checkbox + type badge + score + date */}
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <input type="checkbox" checked={selected.has(m.id)} onChange={() => toggleSelect(m.id)} style={{ width: 16, height: 16, flexShrink: 0 }} />
                    <span style={{
                      display: "inline-block", padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 500,
                      whiteSpace: "nowrap",
                      background: `${TYPE_COLORS[m.type] || "#6b7280"}18`,
                      color: TYPE_COLORS[m.type] || "#6b7280",
                      border: `1px solid ${TYPE_COLORS[m.type] || "#6b7280"}30`,
                    }}>
                      {TYPE_LABELS[m.type] || m.type}
                    </span>
                    <span style={{
                      fontWeight: 600, fontSize: 12,
                      color: m.importance_score >= 0.85 ? "#10b981" : m.importance_score >= 0.7 ? "#f59e0b" : "#6b7280",
                    }}>
                      {m.importance_score.toFixed(2)}
                    </span>
                    <span style={{ fontSize: 11, color: "var(--muted)", marginLeft: "auto" }}>
                      {fmtDate(m.created_at)}
                    </span>
                  </div>

                  {/* Content */}
                  {editingId === m.id ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <textarea
                        value={editContent}
                        onChange={e => setEditContent(e.target.value)}
                        rows={3}
                        style={{
                          width: "100%", padding: 6, border: "1px solid var(--line)",
                          borderRadius: 4, background: "var(--bg)", color: "var(--text)",
                          fontSize: 13, resize: "vertical",
                        }}
                      />
                      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>分数:</span>
                        <input
                          type="number" min={0} max={1} step={0.05}
                          value={editScore}
                          onChange={e => setEditScore(parseFloat(e.target.value) || 0)}
                          style={{
                            width: 70, padding: "2px 6px", border: "1px solid var(--line)",
                            borderRadius: 4, background: "var(--bg)", color: "var(--text)", fontSize: 12,
                          }}
                        />
                        <button onClick={() => handleUpdate(m.id)} style={{ background: "none", border: "none", cursor: "pointer", color: "#10b981", padding: 4 }}>
                          <IconCheck size={16} />
                        </button>
                        <button onClick={() => setEditingId(null)} style={{ background: "none", border: "none", cursor: "pointer", color: "#ef4444", padding: 4 }}>
                          <IconX size={16} />
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, lineHeight: 1.6, wordBreak: "break-word", whiteSpace: "pre-wrap", color: "var(--text)" }}>
                      {m.content}
                    </div>
                  )}

                  {/* Meta: subject + predicate + tags */}
                  {(m.subject || m.predicate || (m.tags && m.tags.length > 0)) && editingId !== m.id && (
                    <div style={{ marginTop: 6 }}>
                      {(m.subject || m.predicate) && (
                        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                          {m.subject && <span>主体: {m.subject}</span>}
                          {m.subject && m.predicate && <span> · </span>}
                          {m.predicate && <span>属性: {m.predicate}</span>}
                        </div>
                      )}
                      {m.tags && m.tags.length > 0 && (
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {m.tags.map(tag => (
                            <span key={tag} style={{
                              padding: "1px 6px", borderRadius: 8, fontSize: 10,
                              background: "rgba(99,102,241,0.1)", color: "#6366f1",
                              whiteSpace: "nowrap",
                            }}>
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Actions */}
                  {editingId !== m.id && (
                    <div style={{ display: "flex", gap: 8, marginTop: 8, justifyContent: "flex-end" }}>
                      <button
                        onClick={() => startEdit(m)}
                        style={{
                          background: "none", border: "1px solid var(--line)", borderRadius: 6,
                          cursor: "pointer", padding: "4px 10px", color: "var(--text)",
                          display: "flex", alignItems: "center", gap: 4, fontSize: 12,
                        }}
                      >
                        <IconEdit size={12} /> 编辑
                      </button>
                      <button
                        onClick={() => handleDelete(m.id)}
                        style={{
                          background: "none", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 6,
                          cursor: "pointer", padding: "4px 10px", color: "#ef4444",
                          display: "flex", alignItems: "center", gap: 4, fontSize: 12,
                        }}
                      >
                        <IconTrash size={12} /> 删除
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      ) : (
        /* ── Desktop: table layout ── */
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--bg)", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "8px 12px", textAlign: "left", width: 36 }}>
                  <input
                    type="checkbox"
                    checked={selected.size === memories.length && memories.length > 0}
                    onChange={selectAll}
                    style={{ width: 16, height: 16 }}
                  />
                </th>
                <th style={{ padding: "8px 12px", textAlign: "left", width: 80 }}>类型</th>
                <th style={{ padding: "8px 12px", textAlign: "left" }}>内容</th>
                <th style={{ padding: "8px 12px", textAlign: "center", width: 60 }}>分数</th>
                <th style={{ padding: "8px 12px", textAlign: "center", width: 90 }}>创建时间</th>
                <th style={{ padding: "8px 12px", textAlign: "center", width: 100 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
                    <IconLoader size={20} /> 加载中...
                  </td>
                </tr>
              ) : memories.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
                    暂无记忆数据
                  </td>
                </tr>
              ) : memories.map(m => (
                <tr
                  key={m.id}
                  style={{
                    borderBottom: "1px solid var(--line)",
                    background: selected.has(m.id) ? "rgba(99,102,241,0.06)" : undefined,
                    transition: "background 0.15s",
                  }}
                  onMouseEnter={e => { if (!selected.has(m.id)) (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.02)"; }}
                  onMouseLeave={e => { if (!selected.has(m.id)) (e.currentTarget as HTMLElement).style.background = ""; }}
                >
                  <td style={{ padding: "8px 12px" }}>
                    <input type="checkbox" checked={selected.has(m.id)} onChange={() => toggleSelect(m.id)} style={{ width: 16, height: 16 }} />
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <span style={{
                      display: "inline-block", padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 500,
                      whiteSpace: "nowrap",
                      background: `${TYPE_COLORS[m.type] || "#6b7280"}18`,
                      color: TYPE_COLORS[m.type] || "#6b7280",
                      border: `1px solid ${TYPE_COLORS[m.type] || "#6b7280"}30`,
                    }}>
                      {TYPE_LABELS[m.type] || m.type}
                    </span>
                  </td>
                  <td style={{ padding: "8px 12px", maxWidth: 400 }}>
                    {editingId === m.id ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        <textarea
                          value={editContent}
                          onChange={e => setEditContent(e.target.value)}
                          rows={3}
                          style={{
                            width: "100%", padding: 6, border: "1px solid var(--line)",
                            borderRadius: 4, background: "var(--bg)", color: "var(--text)",
                            fontSize: 12, resize: "vertical",
                          }}
                        />
                        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                          <span style={{ fontSize: 11, color: "var(--muted)" }}>分数:</span>
                          <input
                            type="number" min={0} max={1} step={0.05}
                            value={editScore}
                            onChange={e => setEditScore(parseFloat(e.target.value) || 0)}
                            style={{
                              width: 70, padding: "2px 6px", border: "1px solid var(--line)",
                              borderRadius: 4, background: "var(--bg)", color: "var(--text)", fontSize: 12,
                            }}
                          />
                          <button onClick={() => handleUpdate(m.id)} style={{ background: "none", border: "none", cursor: "pointer", color: "#10b981" }}>
                            <IconCheck size={14} />
                          </button>
                          <button onClick={() => setEditingId(null)} style={{ background: "none", border: "none", cursor: "pointer", color: "#ef4444" }}>
                            <IconX size={14} />
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div>
                        <div style={{ lineHeight: 1.5, wordBreak: "break-word", whiteSpace: "pre-wrap" }}>
                          {m.content}
                        </div>
                        {(m.subject || m.predicate) && (
                          <div style={{ marginTop: 4, fontSize: 11, color: "var(--muted)" }}>
                            {m.subject && <span>主体: {m.subject}</span>}
                            {m.subject && m.predicate && <span> · </span>}
                            {m.predicate && <span>属性: {m.predicate}</span>}
                          </div>
                        )}
                        {m.tags && m.tags.length > 0 && (
                          <div style={{ marginTop: 4, display: "flex", gap: 4, flexWrap: "wrap" }}>
                            {m.tags.map(tag => (
                              <span key={tag} style={{
                                padding: "1px 6px", borderRadius: 8, fontSize: 10,
                                background: "rgba(99,102,241,0.1)", color: "#6366f1",
                                whiteSpace: "nowrap",
                              }}>
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: "8px 12px", textAlign: "center" }}>
                    <span style={{
                      fontWeight: 600, fontSize: 12,
                      color: m.importance_score >= 0.85 ? "#10b981" : m.importance_score >= 0.7 ? "#f59e0b" : "#6b7280",
                    }}>
                      {m.importance_score.toFixed(2)}
                    </span>
                  </td>
                  <td style={{ padding: "8px 12px", textAlign: "center", fontSize: 11, color: "var(--muted)" }}>
                    {fmtDate(m.created_at)}
                  </td>
                  <td style={{ padding: "8px 12px", textAlign: "center" }}>
                    <div style={{ display: "flex", gap: 4, justifyContent: "center" }}>
                      <button
                        onClick={() => startEdit(m)}
                        title="编辑"
                        style={{
                          background: "none", border: "1px solid var(--line)", borderRadius: 4,
                          cursor: "pointer", padding: "3px 6px", color: "var(--text)",
                        }}
                      >
                        <IconEdit size={12} />
                      </button>
                      <button
                        onClick={() => handleDelete(m.id)}
                        title="删除"
                        style={{
                          background: "none", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 4,
                          cursor: "pointer", padding: "3px 6px", color: "#ef4444",
                        }}
                      >
                        <IconTrash size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
    </div>
  );
}
