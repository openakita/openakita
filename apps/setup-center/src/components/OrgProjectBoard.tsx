/**
 * Project management board — Gantt timeline + kanban columns.
 * Full-screen layout with project selector, timeline progress, and task modals.
 */
import { useState, useEffect, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import { safeFetch } from "../providers";
import { OrgAvatar } from "./OrgAvatars";

interface ProjectTask {
  id: string;
  project_id: string;
  title: string;
  description: string;
  status: string;
  assignee_node_id: string | null;
  priority: number;
  progress_pct: number;
  created_at: string;
  started_at: string | null;
  delivered_at: string | null;
  completed_at: string | null;
}

interface Project {
  id: string;
  org_id: string;
  name: string;
  description: string;
  project_type: string;
  status: string;
  owner_node_id: string | null;
  tasks: ProjectTask[];
  created_at: string;
  updated_at: string;
}

interface OrgProjectBoardProps {
  orgId: string;
  apiBaseUrl: string;
  nodes?: Array<{ id: string; role_title?: string; avatar?: string | null }>;
  compact?: boolean;
}

const STATUS_META: Record<string, { label: string; color: string; order: number }> = {
  todo:        { label: "待办",   color: "#64748b", order: 0 },
  in_progress: { label: "进行中", color: "#3b82f6", order: 1 },
  delivered:   { label: "已交付", color: "#8b5cf6", order: 2 },
  rejected:    { label: "已打回", color: "#f97316", order: 3 },
  accepted:    { label: "已验收", color: "#22c55e", order: 4 },
  blocked:     { label: "已阻塞", color: "#ef4444", order: 5 },
};

const COLUMNS = Object.entries(STATUS_META).map(([key, v]) => ({ key, ...v }));

const PROJECT_TYPE_LABEL: Record<string, string> = { temporary: "临时", permanent: "持续" };
const PROJECT_STATUS_LABEL: Record<string, string> = {
  planning: "规划中", active: "进行中", paused: "暂停", completed: "已完成", archived: "已归档",
};
const PROJECT_STATUS_COLOR: Record<string, string> = {
  planning: "#f59e0b", active: "#3b82f6", paused: "#94a3b8", completed: "#22c55e", archived: "#6b7280",
};

export function OrgProjectBoard({ orgId, apiBaseUrl, nodes = [], compact = false }: OrgProjectBoardProps) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showNewProject, setShowNewProject] = useState(false);
  const [showNewTask, setShowNewTask] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDesc, setNewProjectDesc] = useState("");
  const [newProjectType, setNewProjectType] = useState("temporary");
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskDesc, setNewTaskDesc] = useState("");
  const [newTaskAssignee, setNewTaskAssignee] = useState("");
  const [dispatchingTaskId, setDispatchingTaskId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<any>(null);
  const [taskDetail, setTaskDetail] = useState<any>(null);
  const [taskTimeline, setTaskTimeline] = useState<any[]>([]);
  const [taskDetailLoading, setTaskDetailLoading] = useState(false);
  const [subtasksExpanded, setSubtasksExpanded] = useState(true);
  const [viewTab, setViewTab] = useState<"gantt" | "kanban">("gantt");
  const [confirmDeleteProjectId, setConfirmDeleteProjectId] = useState<string | null>(null);

  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  const fetchTaskDetail = useCallback(async (taskId: string) => {
    setTaskDetailLoading(true);
    setTaskDetail(null);
    setTaskTimeline([]);
    try {
      const [detailRes, timelineRes] = await Promise.all([
        safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/tasks/${taskId}`),
        safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/tasks/${taskId}/timeline`),
      ]);
      if (detailRes.ok) setTaskDetail(await detailRes.json());
      if (timelineRes.ok) {
        const tl = await timelineRes.json();
        setTaskTimeline(tl.timeline || []);
      }
    } catch { /* ignore */ }
    setTaskDetailLoading(false);
  }, [orgId, apiBaseUrl]);

  const openTaskDetail = useCallback((task: ProjectTask) => {
    setSelectedTask(task);
    fetchTaskDetail(task.id);
  }, [fetchTaskDetail]);

  const closeTaskDetail = useCallback(() => {
    setSelectedTask(null);
    setTaskDetail(null);
    setTaskTimeline([]);
  }, []);

  const fetchProjects = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects`);
      if (res.ok) {
        const data = await res.json();
        setProjects(data);
        if (!selectedProjectId && data.length > 0) setSelectedProjectId(data[0].id);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [orgId, apiBaseUrl, selectedProjectId]);

  useEffect(() => { fetchProjects(); }, [fetchProjects]);

  const createProject = async () => {
    if (!newProjectName.trim()) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newProjectName, description: newProjectDesc, project_type: newProjectType, status: "active" }),
      });
      setNewProjectName(""); setNewProjectDesc(""); setShowNewProject(false);
      fetchProjects();
    } catch { /* ignore */ }
  };

  const createTask = async () => {
    if (!newTaskTitle.trim() || !selectedProjectId) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects/${selectedProjectId}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTaskTitle, description: newTaskDesc, assignee_node_id: newTaskAssignee || null, status: "todo" }),
      });
      setNewTaskTitle(""); setNewTaskDesc(""); setNewTaskAssignee(""); setShowNewTask(false);
      fetchProjects();
    } catch { /* ignore */ }
  };

  const deleteProject = async (projectId: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects/${projectId}`, { method: "DELETE" });
      if (selectedProjectId === projectId) setSelectedProjectId(null);
      fetchProjects();
    } catch { /* ignore */ }
  };

  const updateTaskStatus = async (projectId: string, taskId: string, newStatus: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects/${projectId}/tasks/${taskId}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      fetchProjects();
    } catch { /* ignore */ }
  };

  const deleteTask = async (projectId: string, taskId: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects/${projectId}/tasks/${taskId}`, { method: "DELETE" });
      if (selectedTask?.id === taskId) closeTaskDetail();
      fetchProjects();
    } catch { /* ignore */ }
  };

  const dispatchTask = async (projectId: string, taskId: string) => {
    setDispatchingTaskId(taskId);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects/${projectId}/tasks/${taskId}/dispatch`, { method: "POST" });
      if (res.ok) fetchProjects();
    } catch { /* ignore */ }
    finally { setDispatchingTaskId(null); }
  };

  const selectedProject = projects.find(p => p.id === selectedProjectId);
  const tasks = selectedProject?.tasks || [];

  const projectStats = useMemo(() => {
    const total = tasks.length;
    const done = tasks.filter(t => t.status === "accepted").length;
    const inProgress = tasks.filter(t => t.status === "in_progress").length;
    const delivered = tasks.filter(t => t.status === "delivered").length;
    const todo = tasks.filter(t => t.status === "todo").length;
    const blocked = tasks.filter(t => t.status === "blocked" || t.status === "rejected").length;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    return { total, done, inProgress, delivered, todo, blocked, pct };
  }, [tasks]);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--muted)" }}>
        加载中...
      </div>
    );
  }

  return (
    <div className="opb-root">
      <style>{`
        .opb-root {
          height: 100%; display: flex; flex-direction: column;
          overflow: hidden; background: var(--bg-app);
          font-size: 13px; color: var(--text);
        }
        .opb-header {
          display: flex; align-items: center; gap: 10px;
          padding: 10px 16px; border-bottom: 1px solid var(--line);
          flex-shrink: 0;
        }
        .opb-proj-btn {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 14px; border-radius: 6px; border: none;
          font-size: 13px; cursor: pointer; white-space: nowrap;
          transition: all 0.15s;
        }
        .opb-proj-btn--active {
          background: var(--primary, #6366f1); color: #fff !important; font-weight: 600;
          box-shadow: 0 1px 4px rgba(99,102,241,0.25);
        }
        .opb-proj-btn--active:hover {
          background: #4f46e5; color: #fff !important;
        }
        .opb-proj-btn--inactive {
          background: var(--bg-subtle, rgba(100,116,139,0.08)); color: var(--text);
          border: 1px solid var(--line, rgba(100,116,139,0.2));
        }
        .opb-proj-btn--inactive:hover { border-color: var(--primary, #6366f1); color: var(--primary, #6366f1); background: var(--nav-hover, rgba(99,102,241,0.08)); }
        .opb-type-tag {
          font-size: 9px; padding: 1px 5px; border-radius: 3px;
          background: var(--bg-subtle, rgba(100,116,139,0.15)); margin-left: 2px;
          color: var(--muted, #64748b);
        }
        .opb-proj-btn--active .opb-type-tag {
          background: rgba(255,255,255,0.2); color: rgba(255,255,255,0.85);
        }
        .opb-proj-color {
          width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
        }
        .opb-proj-del {
          position: absolute; top: -6px; right: -6px;
          width: 16px; height: 16px; border-radius: 50%;
          background: var(--danger, #ef4444); color: #fff;
          border: none; font-size: 11px; line-height: 1;
          cursor: pointer; display: flex; align-items: center; justify-content: center;
          opacity: 0.7; transition: opacity 0.15s;
        }
        .opb-proj-del:hover { opacity: 1; }
        .opb-proj-confirm {
          position: absolute; left: 0; top: 100%; z-index: 10;
          background: var(--card-bg); border: 1px solid var(--line);
          border-radius: 8px; padding: 8px 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.2);
          display: flex; gap: 6px; align-items: center; font-size: 11px;
          white-space: nowrap; margin-top: 4px;
          color: var(--text);
        }
        .opb-view-tabs {
          display: flex; gap: 0; margin-left: auto;
        }
        .opb-view-tab {
          padding: 4px 14px; border: 1px solid var(--line); cursor: pointer;
          font-size: 12px; background: transparent; color: var(--muted);
          transition: all 0.15s;
        }
        .opb-view-tab:first-child { border-radius: 6px 0 0 6px; }
        .opb-view-tab:last-child { border-radius: 0 6px 6px 0; border-left: none; }
        .opb-view-tab--active {
          background: var(--primary, #6366f1); color: #fff;
          border-color: var(--primary, #6366f1); font-weight: 600;
        }
        .opb-stats {
          display: flex; align-items: center; gap: 12px; padding: 8px 16px;
          border-bottom: 1px solid var(--line); flex-shrink: 0;
        }
        .opb-stat-item { display: flex; flex-direction: column; align-items: center; gap: 1px; }
        .opb-stat-num { font-size: 18px; font-weight: 700; line-height: 1; }
        .opb-stat-label { font-size: 10px; color: var(--muted); }
        .opb-progress-bar {
          flex: 1; height: 8px; border-radius: 4px;
          background: var(--line, rgba(51,65,85,0.3));
          overflow: hidden; display: flex;
        }
        .opb-progress-seg {
          height: 100%; transition: width 0.3s ease;
        }
        .opb-pct { font-size: 14px; font-weight: 700; min-width: 40px; text-align: right; }

        /* ── Gantt chart ── */
        .opb-gantt { flex: 1; overflow: auto; padding: 0 16px 16px; }
        .opb-gantt-table { width: 100%; border-collapse: collapse; }
        .opb-gantt-table th {
          text-align: left; font-size: 11px; font-weight: 600;
          color: var(--muted); padding: 8px 6px;
          border-bottom: 1px solid var(--line);
          position: sticky; top: 0; background: var(--bg-app); z-index: 1;
        }
        .opb-gantt-table td {
          padding: 6px; border-bottom: 1px solid var(--line, rgba(51,65,85,0.2));
          vertical-align: middle; font-size: 12px;
        }
        .opb-gantt-row { cursor: pointer; transition: background 0.1s; }
        .opb-gantt-row:hover { background: rgba(99,102,241,0.06); }
        .opb-gantt-bar-wrap {
          position: relative; height: 22px;
          background: var(--line, rgba(51,65,85,0.15));
          border-radius: 4px; overflow: hidden; min-width: 60px;
        }
        .opb-gantt-bar {
          position: absolute; left: 0; top: 0; bottom: 0;
          border-radius: 4px; transition: width 0.3s ease;
        }
        .opb-gantt-bar-label {
          position: absolute; left: 6px; top: 50%; transform: translateY(-50%);
          font-size: 10px; font-weight: 600; color: #fff;
          text-shadow: 0 1px 2px rgba(0,0,0,0.3);
          white-space: nowrap;
        }
        .opb-status-dot {
          display: inline-block; width: 8px; height: 8px;
          border-radius: 50%; flex-shrink: 0;
        }
        .opb-status-badge {
          display: inline-flex; align-items: center; gap: 4px;
          padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500;
          white-space: nowrap;
        }
        .opb-action-btn {
          padding: 2px 8px; border: none; border-radius: 4px;
          font-size: 11px; cursor: pointer; transition: background 0.15s;
          font-weight: 500;
        }

        /* ── Kanban ── */
        .opb-kanban {
          flex: 1; display: flex; gap: 10px; padding: 12px 16px;
          overflow-x: auto; overflow-y: hidden;
        }
        .opb-kanban-col {
          flex: 1 1 170px; min-width: 170px; max-width: 260px;
          display: flex; flex-direction: column;
          background: var(--bg-subtle, rgba(30,41,59,0.3));
          border-radius: 10px; overflow: hidden;
        }
        .opb-kanban-col-header {
          padding: 8px 10px; display: flex; align-items: center; gap: 6px;
          flex-shrink: 0;
        }
        .opb-kanban-col-count {
          font-size: 10px; color: var(--muted);
          background: var(--bg-app); padding: 1px 6px; border-radius: 8px;
        }
        .opb-kanban-list { flex: 1; overflow-y: auto; padding: 4px 6px 6px; display: flex; flex-direction: column; gap: 4px; }
        .opb-kanban-card {
          padding: 8px 10px; border-radius: 8px;
          background: var(--bg-app); border: 1px solid var(--line, rgba(51,65,85,0.3));
          cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s;
        }
        .opb-kanban-card:hover {
          border-color: var(--primary, #6366f1);
          box-shadow: 0 1px 4px rgba(99,102,241,0.15);
        }

        /* ── Modal ── */
        .opb-modal-overlay {
          position: fixed; inset: 0; z-index: 10000;
          background: rgba(0,0,0,0.45); backdrop-filter: blur(3px);
          display: flex; align-items: center; justify-content: center;
          animation: opb-fade-in 0.15s ease;
        }
        @keyframes opb-fade-in { from { opacity: 0; } to { opacity: 1; } }
        .opb-modal {
          background: var(--bg-app, #0f172a);
          border: 1px solid var(--line);
          border-radius: 12px; box-shadow: 0 12px 40px rgba(0,0,0,0.4);
          width: 420px; max-width: 90vw;
          animation: opb-scale-in 0.2s ease;
        }
        @keyframes opb-scale-in { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
        .opb-modal-header {
          display: flex; justify-content: space-between; align-items: center;
          padding: 14px 16px 10px; font-weight: 600; font-size: 14px;
        }
        .opb-modal-close {
          background: none; border: none; color: var(--muted); cursor: pointer;
          padding: 4px; border-radius: 4px; font-size: 16px;
        }
        .opb-modal-close:hover { color: var(--text); }
        .opb-modal-body { padding: 0 16px 12px; }
        .opb-modal-label {
          display: block; font-size: 11px; font-weight: 500;
          color: var(--muted); margin-bottom: 4px; margin-top: 10px;
        }
        .opb-modal-label:first-child { margin-top: 0; }
        .opb-modal-footer {
          display: flex; justify-content: flex-end; gap: 8px;
          padding: 10px 16px 14px; border-top: 1px solid var(--line, rgba(51,65,85,0.4));
        }
        .opb-modal-btn {
          height: 32px; padding: 0 16px; border-radius: 6px;
          border: 1px solid var(--line); background: transparent;
          color: var(--text); font-size: 12px; cursor: pointer;
        }
        .opb-modal-btn:hover { background: rgba(99,102,241,0.1); }
        .opb-modal-btn--primary {
          background: var(--primary, #6366f1); color: #fff;
          border-color: var(--primary, #6366f1);
        }
        .opb-modal-btn--primary:hover { background: #4f46e5; }

        /* ── Task detail slide-out ── */
        .opb-detail-overlay {
          position: absolute; inset: 0; z-index: 100;
          display: flex; background: rgba(0,0,0,0.3);
        }
        .opb-detail-panel {
          width: min(440px, 100%); margin-left: auto;
          background: var(--bg-app); border-left: 1px solid var(--line);
          box-shadow: -4px 0 16px rgba(0,0,0,0.15);
          display: flex; flex-direction: column; overflow: hidden;
        }
      `}</style>

      {/* ── Header: project selector + view tabs ── */}
      <div className="opb-header">
        {projects.map(p => (
          <div key={p.id} style={{ position: "relative", display: "inline-flex" }}>
            <button
              className={`opb-proj-btn ${p.id === selectedProjectId ? "opb-proj-btn--active" : "opb-proj-btn--inactive"}`}
              onClick={() => setSelectedProjectId(p.id)}
              onContextMenu={(e) => { e.preventDefault(); setConfirmDeleteProjectId(p.id); }}
            >
              <span className="opb-proj-color" style={{ background: PROJECT_STATUS_COLOR[p.status] || "var(--primary, #6366f1)" }} />
              {p.name}
              <span className="opb-type-tag">{PROJECT_TYPE_LABEL[p.project_type] || p.project_type}</span>
            </button>
            {p.id === selectedProjectId && (
              <button
                className="opb-proj-del"
                onClick={(e) => { e.stopPropagation(); setConfirmDeleteProjectId(p.id); }}
                title="删除项目"
              >×</button>
            )}
            {confirmDeleteProjectId === p.id && (
              <div className="opb-proj-confirm" onClick={(e) => e.stopPropagation()}>
                <span>确认删除项目？</span>
                <button className="opb-action-btn" style={{ background: "#ef4444", color: "#fff", fontSize: 10, padding: "2px 8px" }}
                  onClick={() => { deleteProject(p.id); setConfirmDeleteProjectId(null); }}>删除</button>
                <button className="opb-action-btn" style={{ fontSize: 10, padding: "2px 8px", background: "transparent", color: "var(--text)", border: "1px solid var(--line)" }}
                  onClick={() => setConfirmDeleteProjectId(null)}>取消</button>
              </div>
            )}
          </div>
        ))}
        <button
          className="opb-proj-btn opb-proj-btn--inactive"
          onClick={() => setShowNewProject(true)}
          style={{ borderStyle: "dashed" }}
        >
          + 新项目
        </button>

        {selectedProject && (
          <div className="opb-view-tabs">
            <button className={`opb-view-tab${viewTab === "gantt" ? " opb-view-tab--active" : ""}`} onClick={() => setViewTab("gantt")}>
              甘特图
            </button>
            <button className={`opb-view-tab${viewTab === "kanban" ? " opb-view-tab--active" : ""}`} onClick={() => setViewTab("kanban")}>
              看板
            </button>
          </div>
        )}
      </div>

      {/* ── Stats bar ── */}
      {selectedProject && (
        <div className="opb-stats">
          {projectStats.total > 0 && (<>
            <div className="opb-stat-item">
              <span className="opb-stat-num">{projectStats.total}</span>
              <span className="opb-stat-label">总任务</span>
            </div>
            <div className="opb-stat-item">
              <span className="opb-stat-num" style={{ color: "#3b82f6" }}>{projectStats.inProgress}</span>
              <span className="opb-stat-label">进行中</span>
            </div>
            <div className="opb-stat-item">
              <span className="opb-stat-num" style={{ color: "#22c55e" }}>{projectStats.done}</span>
              <span className="opb-stat-label">已完成</span>
            </div>
            {projectStats.blocked > 0 && (
              <div className="opb-stat-item">
                <span className="opb-stat-num" style={{ color: "#ef4444" }}>{projectStats.blocked}</span>
                <span className="opb-stat-label">异常</span>
              </div>
            )}

            <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, marginLeft: 8 }}>
              <div className="opb-progress-bar">
                {projectStats.done > 0 && <div className="opb-progress-seg" style={{ width: `${(projectStats.done / projectStats.total) * 100}%`, background: "#22c55e" }} />}
                {projectStats.delivered > 0 && <div className="opb-progress-seg" style={{ width: `${(projectStats.delivered / projectStats.total) * 100}%`, background: "#8b5cf6" }} />}
                {projectStats.inProgress > 0 && <div className="opb-progress-seg" style={{ width: `${(projectStats.inProgress / projectStats.total) * 100}%`, background: "#3b82f6" }} />}
              </div>
              <span className="opb-pct">{projectStats.pct}%</span>
            </div>
          </>)}
          {projectStats.total === 0 && <div style={{ flex: 1 }} />}

          <button
            className="opb-action-btn"
            style={{ background: "var(--primary, #6366f1)", color: "#fff" }}
            onClick={() => setShowNewTask(true)}
          >
            + 新任务
          </button>
        </div>
      )}

      {/* ── Main content ── */}
      {selectedProject ? (
        viewTab === "gantt" ? (
          <GanttView
            tasks={tasks}
            nodeMap={nodeMap}
            onTaskClick={openTaskDetail}
            onStatusChange={(tid, st) => updateTaskStatus(selectedProject.id, tid, st)}
            onDispatch={(tid) => dispatchTask(selectedProject.id, tid)}
            onDelete={(tid) => deleteTask(selectedProject.id, tid)}
            dispatchingTaskId={dispatchingTaskId}
          />
        ) : (
          <KanbanView
            tasks={tasks}
            nodeMap={nodeMap}
            onTaskClick={openTaskDetail}
            onStatusChange={(tid, st) => updateTaskStatus(selectedProject.id, tid, st)}
            onDispatch={(tid) => dispatchTask(selectedProject.id, tid)}
            onDelete={(tid) => deleteTask(selectedProject.id, tid)}
            dispatchingTaskId={dispatchingTaskId}
          />
        )
      ) : (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", flexDirection: "column", gap: 12 }}>
          <span style={{ fontSize: 14 }}>暂无项目</span>
          <button
            className="opb-action-btn"
            style={{ background: "var(--primary, #6366f1)", color: "#fff", padding: "8px 20px", fontSize: 13, borderRadius: 8 }}
            onClick={() => setShowNewProject(true)}
          >
            创建第一个项目
          </button>
        </div>
      )}

      {/* ── New Project Modal ── */}
      {showNewProject && createPortal(
        <div className="opb-modal-overlay" onClick={() => setShowNewProject(false)}>
          <div className="opb-modal" onClick={e => e.stopPropagation()}>
            <div className="opb-modal-header">
              <span>新建项目</span>
              <button className="opb-modal-close" onClick={() => setShowNewProject(false)}>×</button>
            </div>
            <div className="opb-modal-body">
              <label className="opb-modal-label">项目名称 *</label>
              <input className="input" placeholder="例如：Q2 产品迭代" value={newProjectName}
                onChange={e => setNewProjectName(e.target.value)}
                style={{ width: "100%", fontSize: 13 }} autoFocus
                onKeyDown={e => e.key === "Enter" && createProject()} />
              <label className="opb-modal-label">项目描述</label>
              <textarea className="input" placeholder="项目目标和范围..."
                value={newProjectDesc} onChange={e => setNewProjectDesc(e.target.value)}
                style={{ width: "100%", fontSize: 12, minHeight: 60, resize: "vertical" }} />
              <label className="opb-modal-label">项目类型</label>
              <div style={{ display: "flex", gap: 8 }}>
                {(["temporary", "permanent"] as const).map(t => (
                  <button key={t}
                    className="opb-action-btn"
                    style={{
                      background: newProjectType === t ? "var(--primary, #6366f1)" : "transparent",
                      color: newProjectType === t ? "#fff" : "var(--text)",
                      border: `1px solid ${newProjectType === t ? "var(--primary)" : "var(--line)"}`,
                      padding: "4px 14px",
                    }}
                    onClick={() => setNewProjectType(t)}
                  >{PROJECT_TYPE_LABEL[t]}</button>
                ))}
              </div>
            </div>
            <div className="opb-modal-footer">
              <button className="opb-modal-btn" onClick={() => setShowNewProject(false)}>取消</button>
              <button className="opb-modal-btn opb-modal-btn--primary" onClick={createProject}>创建</button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* ── New Task Modal ── */}
      {showNewTask && createPortal(
        <div className="opb-modal-overlay" onClick={() => setShowNewTask(false)}>
          <div className="opb-modal" onClick={e => e.stopPropagation()}>
            <div className="opb-modal-header">
              <span>新建任务</span>
              <button className="opb-modal-close" onClick={() => setShowNewTask(false)}>×</button>
            </div>
            <div className="opb-modal-body">
              <label className="opb-modal-label">任务标题 *</label>
              <input className="input" placeholder="例如：设计首页原型" value={newTaskTitle}
                onChange={e => setNewTaskTitle(e.target.value)}
                style={{ width: "100%", fontSize: 13 }} autoFocus
                onKeyDown={e => e.key === "Enter" && createTask()} />
              <label className="opb-modal-label">任务描述</label>
              <textarea className="input" placeholder="任务详细说明..."
                value={newTaskDesc} onChange={e => setNewTaskDesc(e.target.value)}
                style={{ width: "100%", fontSize: 12, minHeight: 50, resize: "vertical" }} />
              <label className="opb-modal-label">指派给</label>
              <select className="input" value={newTaskAssignee} onChange={e => setNewTaskAssignee(e.target.value)}
                style={{ width: "100%", fontSize: 12 }}>
                <option value="">未分配</option>
                {nodes.map(n => <option key={n.id} value={n.id}>{n.role_title || n.id}</option>)}
              </select>
            </div>
            <div className="opb-modal-footer">
              <button className="opb-modal-btn" onClick={() => setShowNewTask(false)}>取消</button>
              <button className="opb-modal-btn opb-modal-btn--primary" onClick={createTask}>添加</button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* ── Task Detail Panel ── */}
      {selectedTask && (
        <div className="opb-detail-overlay" onClick={closeTaskDetail}>
          <div className="opb-detail-panel" onClick={e => e.stopPropagation()}>
            <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>任务详情</span>
              <button className="opb-modal-close" onClick={closeTaskDetail}>×</button>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
              {taskDetailLoading ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: 24 }}>加载中...</div>
              ) : taskDetail ? (
                <TaskDetailContent
                  task={taskDetail} timeline={taskTimeline} nodeMap={nodeMap}
                  subtasksExpanded={subtasksExpanded} setSubtasksExpanded={setSubtasksExpanded}
                  onAncestorClick={(t: any) => { setSelectedTask(t); fetchTaskDetail(t.id); }}
                  statusLabel={(s: string) => STATUS_META[s]?.label || s}
                />
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: 24 }}>无法加载任务详情</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════ Gantt View ═══════════════════ */

function GanttView({
  tasks, nodeMap, onTaskClick, onStatusChange, onDispatch, onDelete, dispatchingTaskId,
}: {
  tasks: ProjectTask[];
  nodeMap: Map<string, { id: string; role_title?: string; avatar?: string | null }>;
  onTaskClick: (t: ProjectTask) => void;
  onStatusChange: (tid: string, status: string) => void;
  onDispatch: (tid: string) => void;
  onDelete: (tid: string) => void;
  dispatchingTaskId: string | null;
}) {
  const sorted = useMemo(() =>
    [...tasks].sort((a, b) => {
      const oa = STATUS_META[a.status]?.order ?? 9;
      const ob = STATUS_META[b.status]?.order ?? 9;
      if (oa !== ob) return oa - ob;
      return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    }),
    [tasks]
  );

  const timeRange = useMemo(() => {
    if (tasks.length === 0) return { start: new Date(), end: new Date(), days: 7 };
    let earliest = Infinity;
    let latest = -Infinity;
    const now = Date.now();
    for (const t of tasks) {
      const s = new Date(t.created_at).getTime();
      if (s < earliest) earliest = s;
      const e = t.completed_at ? new Date(t.completed_at).getTime()
        : t.delivered_at ? new Date(t.delivered_at).getTime()
        : now;
      if (e > latest) latest = e;
    }
    const pad = 86400000;
    earliest -= pad;
    latest += pad;
    const days = Math.max(3, Math.ceil((latest - earliest) / 86400000));
    return { start: new Date(earliest), end: new Date(latest), days };
  }, [tasks]);

  const fmtDay = (d: Date) => `${d.getMonth() + 1}/${d.getDate()}`;
  const dayMarkers = useMemo(() => {
    const markers: Date[] = [];
    const step = Math.max(1, Math.floor(timeRange.days / 8));
    for (let i = 0; i <= timeRange.days; i += step) {
      markers.push(new Date(timeRange.start.getTime() + i * 86400000));
    }
    return markers;
  }, [timeRange]);

  const getBarStyle = (task: ProjectTask) => {
    const rangeMs = timeRange.end.getTime() - timeRange.start.getTime();
    if (rangeMs <= 0) return { left: "0%", width: "100%" };
    const start = new Date(task.created_at).getTime();
    const now = Date.now();
    const end = task.completed_at ? new Date(task.completed_at).getTime()
      : task.delivered_at ? new Date(task.delivered_at).getTime()
      : task.started_at ? Math.max(new Date(task.started_at).getTime() + 3600000, now)
      : start + 86400000;
    const left = Math.max(0, ((start - timeRange.start.getTime()) / rangeMs) * 100);
    const width = Math.max(2, ((end - start) / rangeMs) * 100);
    return { left: `${left}%`, width: `${Math.min(width, 100 - left)}%` };
  };

  return (
    <div className="opb-gantt">
      {sorted.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--muted)" }}>暂无任务，点击「+ 新任务」开始</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {/* Time axis header */}
          <div style={{ display: "flex", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
            <div style={{ width: 240, flexShrink: 0, padding: "8px 10px", fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>任务</div>
            <div style={{ width: 70, flexShrink: 0, padding: "8px 4px", fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>状态</div>
            <div style={{ flex: 1, position: "relative", padding: "8px 0" }}>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "0 4px" }}>
                {dayMarkers.map((d, i) => (
                  <span key={i} style={{ fontSize: 9, color: "var(--muted)", textAlign: "center", minWidth: 30 }}>
                    {fmtDay(d)}
                  </span>
                ))}
              </div>
            </div>
            <div style={{ width: 90, flexShrink: 0, padding: "8px 4px", fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>操作</div>
          </div>

          {/* Task rows */}
          {sorted.map(task => {
            const meta = STATUS_META[task.status] || { label: task.status, color: "#64748b" };
            const assignee = task.assignee_node_id ? nodeMap.get(task.assignee_node_id) : null;
            const pct = task.progress_pct ?? 0;
            const barStyle = getBarStyle(task);
            return (
              <div key={task.id} className="opb-gantt-row" onClick={() => onTaskClick(task)}
                style={{ display: "flex", flexDirection: "column", borderBottom: "1px solid var(--line, rgba(51,65,85,0.15))", padding: "10px 12px", gap: 6 }}>
                {/* Row 1: Header — avatar, title, status badge, progress, actions */}
                <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                  {assignee && <OrgAvatar avatarId={(assignee as any).avatar || null} size={22} />}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginBottom: 2 }}>
                      <span style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.3, color: "var(--text)" }}>{task.title}</span>
                      <span className="opb-status-badge" style={{ background: meta.color + "18", color: meta.color, fontSize: 10, padding: "1px 6px", flexShrink: 0 }}>
                        {meta.label}
                      </span>
                      {pct > 0 && <span style={{ fontSize: 10, fontWeight: 600, color: meta.color, flexShrink: 0 }}>{pct}%</span>}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--muted)" }}>
                      {assignee ? (assignee.role_title || assignee.id) : "未分配"}
                      {task.id && <span style={{ marginLeft: 8, fontFamily: "monospace" }}>#{task.id.slice(0, 8)}</span>}
                    </div>
                  </div>
                  <div style={{ flexShrink: 0 }} onClick={e => e.stopPropagation()}>
                    <div style={{ display: "flex", gap: 3 }}>
                      {task.status === "todo" && (
                        <button className="opb-action-btn" style={{ background: "var(--primary, #6366f1)", color: "#fff", fontSize: 10, padding: "2px 8px" }}
                          onClick={() => onDispatch(task.id)} disabled={dispatchingTaskId === task.id}>
                          {dispatchingTaskId === task.id ? "…" : "派发"}
                        </button>
                      )}
                      {task.status === "in_progress" && (<>
                        <span style={{ fontSize: 10, color: "#3b82f6", fontWeight: 500 }}>⏳ 执行中</span>
                        <button className="opb-action-btn" style={{ color: "#ef4444", fontSize: 10, padding: "2px 6px" }}
                          onClick={() => onStatusChange(task.id, "blocked")} title="中止任务">⏹ 中止</button>
                      </>)}
                      {task.status === "delivered" && (<>
                        <button className="opb-action-btn" style={{ background: "#22c55e", color: "#fff", fontSize: 10, padding: "2px 6px" }}
                          onClick={() => onStatusChange(task.id, "accepted")}>✓ 验收</button>
                        <button className="opb-action-btn" style={{ background: "#ef4444", color: "#fff", fontSize: 10, padding: "2px 6px" }}
                          onClick={() => onStatusChange(task.id, "rejected")}>✗ 打回</button>
                      </>)}
                      {(task.status === "rejected" || task.status === "blocked") && (
                        <button className="opb-action-btn" style={{ background: "rgba(59,130,246,0.15)", color: "#3b82f6", fontSize: 10, padding: "2px 8px" }}
                          onClick={() => onStatusChange(task.id, "in_progress")}>重新派发</button>
                      )}
                      <button className="opb-action-btn" style={{ color: "#ef4444", fontSize: 10, padding: "2px 6px" }}
                        onClick={() => { if (confirm("确定删除该任务？")) onDelete(task.id); }}
                        title="删除任务">🗑</button>
                    </div>
                  </div>
                </div>
                {/* Row 2: Full description — no truncation */}
                {task.description && (
                  <div style={{
                    fontSize: 11, color: "var(--muted, #94a3b8)", lineHeight: 1.5,
                    whiteSpace: "pre-wrap", wordBreak: "break-word",
                    paddingLeft: 30,
                  }}>
                    {task.description}
                  </div>
                )}
                {/* Row 3: Gantt progress bar */}
                <div style={{ position: "relative", height: 18, paddingLeft: 30 }}>
                  <div style={{ position: "relative", height: "100%", background: "var(--line, rgba(51,65,85,0.15))", borderRadius: 4, overflow: "hidden" }}>
                    <div style={{ position: "absolute", inset: 0, display: "flex", justifyContent: "space-between", pointerEvents: "none" }}>
                      {dayMarkers.map((_, i) => (
                        <div key={i} style={{ width: 1, height: "100%", background: "rgba(51,65,85,0.1)" }} />
                      ))}
                    </div>
                    <div style={{
                      position: "absolute", top: 2, bottom: 2,
                      left: barStyle.left, width: barStyle.width,
                      borderRadius: 3, overflow: "hidden",
                      background: meta.color + "35",
                      border: `1px solid ${meta.color}50`,
                      transition: "left 0.3s, width 0.3s",
                    }}>
                      <div style={{
                        position: "absolute", left: 0, top: 0, bottom: 0,
                        width: `${pct}%`, background: meta.color, opacity: 0.7,
                        borderRadius: 2, transition: "width 0.3s",
                      }} />
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════ Kanban View ═══════════════════ */

function KanbanView({
  tasks, nodeMap, onTaskClick, onStatusChange, onDispatch, onDelete, dispatchingTaskId,
}: {
  tasks: ProjectTask[];
  nodeMap: Map<string, { id: string; role_title?: string; avatar?: string | null }>;
  onTaskClick: (t: ProjectTask) => void;
  onStatusChange: (tid: string, status: string) => void;
  onDispatch: (tid: string) => void;
  onDelete: (tid: string) => void;
  dispatchingTaskId: string | null;
}) {
  return (
    <div className="opb-kanban">
      {COLUMNS.map(col => {
        const colTasks = tasks.filter(t => t.status === col.key);
        return (
          <div key={col.key} className="opb-kanban-col">
            <div className="opb-kanban-col-header" style={{ borderBottom: `2px solid ${col.color}` }}>
              <span className="opb-status-dot" style={{ background: col.color }} />
              <span style={{ fontSize: 12, fontWeight: 600 }}>{col.label}</span>
              <span className="opb-kanban-col-count">{colTasks.length}</span>
            </div>
            <div className="opb-kanban-list">
              {colTasks.map(task => {
                const assignee = task.assignee_node_id ? nodeMap.get(task.assignee_node_id) : null;
                return (
                  <div key={task.id} className="opb-kanban-card" onClick={() => onTaskClick(task)}>
                    <div style={{ fontWeight: 500, marginBottom: 4, fontSize: 12 }}>{task.title}</div>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 4 }}>
                      {assignee ? (
                        <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                          <OrgAvatar avatarId={(assignee as any).avatar || null} size={16} />
                          <span style={{ fontSize: 10, color: "var(--muted)" }}>{assignee.role_title || assignee.id}</span>
                        </div>
                      ) : (
                        <span style={{ fontSize: 10, color: "var(--muted)" }}>未分配</span>
                      )}
                      <div style={{ display: "flex", gap: 2 }} onClick={e => e.stopPropagation()}>
                        {col.key === "todo" && (
                          <>
                            <button className="opb-action-btn" style={{ background: "var(--primary)", color: "#fff", fontSize: 10, padding: "1px 6px" }}
                              onClick={() => onDispatch(task.id)} disabled={dispatchingTaskId === task.id}>
                              {dispatchingTaskId === task.id ? "…" : "派发"}
                            </button>
                            <button className="opb-action-btn" style={{ color: "#3b82f6", fontSize: 10 }}
                              onClick={() => onStatusChange(task.id, "in_progress")}>▶</button>
                          </>
                        )}
                        {col.key === "in_progress" && (<>
                          <span style={{ fontSize: 9, color: "#3b82f6" }}>⏳ 执行中</span>
                          <button className="opb-action-btn" style={{ color: "#ef4444", fontSize: 9, padding: "1px 4px" }}
                            onClick={() => onStatusChange(task.id, "blocked")} title="中止任务">⏹</button>
                        </>)}
                        {col.key === "delivered" && (
                          <>
                            <button className="opb-action-btn" style={{ background: "#22c55e", color: "#fff", fontSize: 10, padding: "1px 5px" }}
                              onClick={() => onStatusChange(task.id, "accepted")}>✓</button>
                            <button className="opb-action-btn" style={{ background: "#ef4444", color: "#fff", fontSize: 10, padding: "1px 5px" }}
                              onClick={() => onStatusChange(task.id, "rejected")}>✗</button>
                          </>
                        )}
                        {(col.key === "rejected" || col.key === "blocked") && (
                          <button className="opb-action-btn" style={{ color: "#3b82f6", fontSize: 10 }}
                            onClick={() => onStatusChange(task.id, "in_progress")}>↻</button>
                        )}
                        <button className="opb-action-btn" style={{ color: "#ef4444", fontSize: 10, padding: "1px 4px" }}
                          onClick={() => { if (confirm("确定删除该任务？")) onDelete(task.id); }}
                          title="删除任务">🗑</button>
                      </div>
                    </div>
                    {(task.progress_pct ?? 0) > 0 && (task.progress_pct ?? 0) < 100 && (
                      <div style={{ marginTop: 4, height: 3, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
                        <div style={{ height: "100%", borderRadius: 2, background: col.color, width: `${task.progress_pct}%` }} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ═══════════════════ Task Detail Content ═══════════════════ */

function TaskDetailContent({
  task, timeline, nodeMap, subtasksExpanded, setSubtasksExpanded, onAncestorClick, statusLabel,
}: {
  task: any; timeline: any[];
  nodeMap: Map<string, { id: string; role_title?: string; avatar?: string | null }>;
  subtasksExpanded: boolean; setSubtasksExpanded: (v: boolean) => void;
  onAncestorClick: (t: any) => void; statusLabel: (s: string) => string;
}) {
  const assignee = task.assignee_node_id ? nodeMap.get(task.assignee_node_id) : null;
  const delegatedBy = task.delegated_by ? nodeMap.get(task.delegated_by) : null;
  const fmt = (s: string | null | undefined) => s ? new Date(s).toLocaleString("zh-CN") : "-";
  const meta = STATUS_META[task.status] || { label: task.status, color: "#64748b" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 12 }}>
      {(task.ancestors?.length ?? 0) > 0 && (
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          {(task.ancestors || []).map((a: any, i: number) => (
            <span key={a.id}>
              {i > 0 && " / "}
              <button type="button" onClick={() => onAncestorClick(a)}
                style={{ background: "none", border: "none", color: "var(--primary)", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
                {a.title || a.id}
              </button>
            </span>
          ))}
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: 10, color: "var(--muted)", fontFamily: "monospace" }}>#{task.id}</span>
        <span className="opb-status-badge" style={{ background: meta.color + "18", color: meta.color }}>
          <span className="opb-status-dot" style={{ background: meta.color }} />
          {meta.label}
        </span>
      </div>

      <div>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{task.title}</div>
        {task.description && <div style={{ color: "var(--muted)", fontSize: 11, whiteSpace: "pre-wrap" }}>{task.description}</div>}
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
          <span>进度</span><span style={{ fontWeight: 600 }}>{task.progress_pct ?? 0}%</span>
        </div>
        <div style={{ height: 6, borderRadius: 3, background: "var(--line)", overflow: "hidden" }}>
          <div style={{ height: "100%", borderRadius: 3, background: meta.color, width: `${Math.min(100, task.progress_pct ?? 0)}%`, transition: "width 0.3s" }} />
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11 }}>
        {assignee && <div><span style={{ color: "var(--muted)" }}>执行人: </span><span>{assignee.role_title || assignee.id}</span></div>}
        {delegatedBy && <div><span style={{ color: "var(--muted)" }}>委派者: </span><span>{delegatedBy.role_title || delegatedBy.id}</span></div>}
        <div><span style={{ color: "var(--muted)" }}>创建时间: </span><span>{fmt(task.created_at)}</span></div>
      </div>

      {(task.plan_steps?.length ?? 0) > 0 && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>计划步骤</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {(task.plan_steps || []).map((s: any, i: number) => {
              const st = s.status || "pending";
              const icon = st === "completed" ? "✓" : st === "in_progress" ? "→" : "○";
              const c = st === "completed" ? "#22c55e" : st === "in_progress" ? "#3b82f6" : "var(--muted)";
              return (
                <div key={s.id || i} style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 11 }}>
                  <span style={{ color: c, fontWeight: 600, flexShrink: 0 }}>{icon}</span>
                  <span>{s.description || s.title || `步骤 ${i + 1}`}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {(task.subtasks?.length ?? 0) > 0 && (
        <div>
          <button type="button" onClick={() => setSubtasksExpanded(!subtasksExpanded)}
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600, marginBottom: 6, color: "var(--text)", padding: 0 }}>
            {subtasksExpanded ? "▼" : "▶"} 子任务 ({task.subtasks.length})
          </button>
          {subtasksExpanded && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(task.subtasks || []).map((st: any) => {
                const sm = STATUS_META[st.status] || { label: st.status, color: "#64748b" };
                return (
                  <div key={st.id} style={{ padding: 8, borderRadius: 6, border: "1px solid var(--line)", background: "var(--bg-subtle, rgba(30,41,59,0.3))" }}>
                    <div style={{ fontWeight: 500, marginBottom: 4 }}>{st.title}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 10 }}>
                      <span className="opb-status-badge" style={{ background: sm.color + "18", color: sm.color, fontSize: 10, padding: "1px 6px" }}>
                        {sm.label}
                      </span>
                      <span style={{ color: "var(--muted)" }}>{(st.progress_pct ?? 0)}%</span>
                    </div>
                    {(st.progress_pct ?? 0) > 0 && (st.progress_pct ?? 0) < 100 && (
                      <div style={{ marginTop: 4, height: 3, borderRadius: 2, background: "var(--line)", overflow: "hidden" }}>
                        <div style={{ height: "100%", borderRadius: 2, background: sm.color, width: `${st.progress_pct}%` }} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>执行时间线</div>
        {timeline.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>暂无事件</div>
        ) : (
          <div style={{ maxHeight: 200, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
            {timeline.map((ev: any, i: number) => (
              <div key={i} style={{ padding: "4px 8px", borderRadius: 4, background: "var(--bg-subtle, rgba(30,41,59,0.3))", fontSize: 11 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: 500 }}>{ev.event || "event"}</span>
                  <span style={{ color: "var(--muted)", fontSize: 10 }}>{ev.ts ? new Date(ev.ts).toLocaleString("zh-CN") : ""}</span>
                </div>
                {ev.actor && <div style={{ fontSize: 10, color: "var(--muted)" }}>by {ev.actor}</div>}
                {ev.detail && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{String(ev.detail)}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
