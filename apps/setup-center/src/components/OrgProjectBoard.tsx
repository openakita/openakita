/**
 * Project management board — Gantt timeline + kanban columns.
 * Full-screen layout with project selector, timeline progress, and task modals.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Check, CornerUpLeft, Pencil, Play, RefreshCw, Trash2, X } from "lucide-react";

import { safeFetch } from "../providers";
import { useMdModules } from "../views/chat/hooks/useMdModules";
import { OrgAvatar } from "./OrgAvatars";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./ui/alert-dialog";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "./ui/dialog";
import { Button } from "./ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";
import { Label } from "./ui/label";
import { ToggleGroup, ToggleGroupItem } from "./ui/toggle-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Badge } from "./ui/badge";
import { FileAttachmentCard } from "./FileAttachmentCard";
import type { FileAttachment } from "./FileAttachmentCard";

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
  deliverable_content?: string;
  delivery_summary?: string;
  file_attachments?: FileAttachment[];
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

interface PendingTaskDelete {
  projectId: string;
  taskId: string;
  taskTitle: string;
}

interface OrgProjectBoardProps {
  orgId: string;
  apiBaseUrl: string;
  nodes?: Array<{ id: string; role_title?: string; avatar?: string | null }>;
  compact?: boolean;
}

const STATUS_META: Record<string, { label: string; color: string; order: number }> = {
  todo:        { label: "Todo",        color: "#64748b", order: 0 },
  in_progress: { label: "In Progress", color: "#3b82f6", order: 1 },
  delivered:   { label: "Delivered",   color: "#8b5cf6", order: 2 },
  rejected:    { label: "Rejected",    color: "#f97316", order: 3 },
  accepted:    { label: "Accepted",    color: "#22c55e", order: 4 },
  blocked:     { label: "Blocked",     color: "#ef4444", order: 5 },
  cancelled:   { label: "Cancelled",   color: "#94a3b8", order: 6 },
};

const COLUMNS = Object.entries(STATUS_META).map(([key, v]) => ({ key, ...v }));

const PROJECT_TYPE_LABEL: Record<string, string> = { temporary: "Temporary", permanent: "Ongoing" };
const PROJECT_STATUS_LABEL: Record<string, string> = {
  planning: "Planning", active: "In Progress", paused: "Paused", completed: "Completed", archived: "Archived",
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
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDesc, setNewProjectDesc] = useState("");
  const [newProjectType, setNewProjectType] = useState("temporary");
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskDesc, setNewTaskDesc] = useState("");
  const [newTaskAssignee, setNewTaskAssignee] = useState("");
  const [dispatchingTaskId, setDispatchingTaskId] = useState<string | null>(null);
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<any>(null);
  const [taskDetail, setTaskDetail] = useState<any>(null);
  const [taskTimeline, setTaskTimeline] = useState<any[]>([]);
  const [taskDetailLoading, setTaskDetailLoading] = useState(false);
  const [subtasksExpanded, setSubtasksExpanded] = useState(true);
  const [viewTab, setViewTab] = useState<"gantt" | "kanban">("gantt");
  const [projectPendingDelete, setProjectPendingDelete] = useState<Project | null>(null);
  const [taskPendingDelete, setTaskPendingDelete] = useState<PendingTaskDelete | null>(null);
  const [projectStripWidth, setProjectStripWidth] = useState<number | null>(null);
  const [projectScrollbarSize, setProjectScrollbarSize] = useState(0);
  const projectRailRef = useRef<HTMLDivElement | null>(null);
  const projectStripRef = useRef<HTMLDivElement | null>(null);
  const projectTrackRef = useRef<HTMLDivElement | null>(null);
  const projectAddRef = useRef<HTMLDivElement | null>(null);

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
        if (data.length === 0) {
          setSelectedProjectId(null);
        } else if (!selectedProjectId || !data.some((p: Project) => p.id === selectedProjectId)) {
          setSelectedProjectId(data[0].id);
        }
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [orgId, apiBaseUrl, selectedProjectId]);

  useEffect(() => { fetchProjects(); }, [fetchProjects]);

  useEffect(() => {
    const rail = projectRailRef.current;
    const strip = projectStripRef.current;
    const track = projectTrackRef.current;
    const add = projectAddRef.current;
    if (!rail || !strip || !track || !add) {
      setProjectStripWidth(null);
      return;
    }

    const gap = 10;
    const measureLayout = () => {
      const available = Math.max(160, rail.clientWidth - add.offsetWidth - gap);
      const content = track.scrollWidth;
      setProjectStripWidth(Math.min(content, available));
      setProjectScrollbarSize(Math.max(0, strip.offsetHeight - strip.clientHeight));
    };

    measureLayout();
    const observer = new ResizeObserver(measureLayout);
    observer.observe(rail);
    observer.observe(strip);
    observer.observe(track);
    observer.observe(add);
    window.addEventListener("resize", measureLayout);

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measureLayout);
    };
  }, [projects]);

  const resetProjectForm = () => {
    setNewProjectName("");
    setNewProjectDesc("");
    setNewProjectType("temporary");
    setEditingProject(null);
  };

  const openEditProject = (project: Project) => {
    setEditingProject(project);
    setNewProjectName(project.name || "");
    setNewProjectDesc(project.description || "");
    setNewProjectType(project.project_type || "temporary");
    setShowNewProject(true);
  };

  const submitProject = async () => {
    if (!newProjectName.trim()) return;
    try {
      await safeFetch(
        editingProject
          ? `${apiBaseUrl}/api/orgs/${orgId}/projects/${editingProject.id}`
          : `${apiBaseUrl}/api/orgs/${orgId}/projects`,
        {
        method: editingProject ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newProjectName,
          description: newProjectDesc,
          project_type: newProjectType,
          status: editingProject?.status ?? "active",
        }),
      });
      resetProjectForm();
      setShowNewProject(false);
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

  const requestTaskDelete = (projectId: string, task: ProjectTask) => {
    setTaskPendingDelete({
      projectId,
      taskId: task.id,
      taskTitle: task.title || task.id,
    });
  };

  const dispatchTask = async (projectId: string, taskId: string) => {
    setDispatchingTaskId(taskId);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects/${projectId}/tasks/${taskId}/dispatch`, { method: "POST" });
      if (res.ok) fetchProjects();
    } catch { /* ignore */ }
    finally { setDispatchingTaskId(null); }
  };

  const cancelTask = async (projectId: string, taskId: string) => {
    setCancellingTaskId(taskId);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/projects/${projectId}/tasks/${taskId}/cancel`, { method: "POST" });
      if (res.ok) fetchProjects();
    } catch { /* ignore */ }
    finally { setCancellingTaskId(null); }
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
        Loading...
      </div>
    );
  }

  return (
    <div className="opb-root">
      <style>{`
        .opb-root {
          height: 100%; display: flex; flex-direction: column;
          overflow: hidden; background: var(--panel, var(--bg-app));
          font-size: 13px; color: var(--text);
          font-family: inherit; line-height: 1.45; letter-spacing: normal;
        }

        /* ── Header ── */
        .opb-project-rail {
          display: flex; align-items: stretch; gap: 10px;
          padding: 12px 16px; border-bottom: 1px solid var(--line);
          flex-shrink: 0; background: var(--panel2, var(--card-bg));
        }
        .opb-project-strip {
          min-width: 0; overflow-x: auto; scrollbar-width: thin;
          scrollbar-gutter: stable;
        }
        .opb-project-track {
          display: flex; gap: 10px; align-items: stretch; width: max-content;
        }
        .opb-project-card {
          min-width: 220px; max-width: 260px; cursor: pointer;
          min-height: 82px; height: 100%;
          border: 1px solid var(--line); background: var(--card-bg, var(--bg-app));
          transition: border-color .15s ease, box-shadow .15s ease, background-color .15s ease;
          position: relative; overflow: hidden;
          flex: 0 0 auto; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .opb-project-card:hover {
          border-color: color-mix(in srgb, var(--primary) 28%, var(--line));
          background: color-mix(in srgb, var(--card-bg) 96%, var(--primary) 4%);
          box-shadow: 0 2px 6px rgba(15, 23, 42, 0.06);
        }
        .opb-project-card--selected {
          border-color: color-mix(in srgb, var(--primary) 55%, var(--line));
          background: color-mix(in srgb, var(--card-bg) 93%, var(--primary) 7%);
          box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary) 22%, transparent), 0 1px 3px rgba(37, 99, 235, 0.08);
        }
        .opb-project-card__title {
          font-size: 14px; font-weight: 600; color: var(--text);
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          line-height: 1.25;
        }
        .opb-project-card__desc {
          font-size: 11px; color: var(--muted);
          line-height: 1.35;
          display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden;
        }
        .opb-project-card__body {
          display: flex; flex-direction: column; gap: 6px; height: 100%;
        }
        .opb-project-card__meta {
          display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
        }
        .opb-project-card__summary {
          display: flex; flex-direction: column; gap: 2px;
        }
        .opb-project-card__stats {
          display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px;
          font-size: 10px; color: var(--muted);
        }
        .opb-project-card__stats strong {
          display: block; font-size: 12px; line-height: 1.1; color: var(--text); font-weight: 600;
          margin-top: 2px;
        }
        .opb-project-card__progress {
          height: 5px; border-radius: 999px; overflow: hidden;
          background: color-mix(in srgb, var(--bg-subtle) 76%, var(--line) 24%);
          border: 1px solid color-mix(in srgb, var(--line) 82%, transparent);
        }
        .opb-project-card__actions {
          position: absolute; top: 6px; right: 6px; z-index: 2;
          display: flex; gap: 4px;
          opacity: 0; pointer-events: none; transform: scale(0.92);
          transition: opacity .15s ease, transform .15s ease;
        }
        .opb-project-card__edit,
        .opb-project-card__delete {
          pointer-events: auto;
        }
        .opb-project-card:hover .opb-project-card__actions,
        .opb-project-card--selected .opb-project-card__actions {
          opacity: 1; pointer-events: auto; transform: scale(1);
        }
        .opb-project-add-card {
          min-width: 132px; max-width: 132px; cursor: pointer;
          min-height: 82px; height: 100%;
          border: 1px dashed color-mix(in srgb, var(--line) 90%, transparent);
          background: color-mix(in srgb, var(--card-bg) 70%, var(--bg-subtle) 30%);
          color: var(--muted);
          transition: border-color .15s ease, color .15s ease, background .15s ease;
          flex: 0 0 auto; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
        }
        .opb-project-add-card:hover {
          border-color: color-mix(in srgb, var(--primary) 45%, var(--line));
          color: var(--primary);
          background: color-mix(in srgb, var(--primary) 6%, var(--bg-app));
        }
        .opb-project-add-slot {
          flex: 0 0 auto;
          display: flex; align-items: stretch;
        }

        /* ── Stats row ── */
        .opb-stats-row {
          display: flex; align-items: center; justify-content: space-between;
          gap: 8px 12px; padding: 6px 16px; flex-wrap: wrap;
          border-bottom: 1px solid var(--line); flex-shrink: 0; font-size: 12px;
          background: color-mix(in srgb, var(--panel2, var(--card-bg)) 82%, transparent);
        }
        .opb-stats-summary {
          flex: 1 1 360px; min-width: 0;
          display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
        }
        .opb-stats-actions {
          flex: 0 1 auto;
          display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
          justify-content: flex-end;
        }
        .opb-stat-chip {
          display: inline-flex; align-items: center; gap: 4px;
          padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500;
          background: color-mix(in srgb, var(--bg-subtle) 78%, var(--card-bg) 22%);
        }
        .opb-progress-track {
          flex: 1 1 140px; min-width: 120px; height: 8px; border-radius: 999px;
          background: color-mix(in srgb, var(--bg-subtle) 75%, var(--line) 25%);
          overflow: hidden; display: flex; margin: 0 4px;
          border: 1px solid color-mix(in srgb, var(--line) 85%, transparent);
        }
        .opb-progress-fill { height: 100%; border-radius: 999px; }

        /* ── Status badges ── */
        .opb-status-dot {
          display: inline-block; width: 7px; height: 7px;
          border-radius: 50%; flex-shrink: 0;
        }
        .opb-status-badge {
          display: inline-flex; align-items: center; gap: 4px;
          padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600;
          white-space: nowrap;
        }

        /* ── Action buttons ── */
        .opb-act {
          display: inline-flex; align-items: center; gap: 3px;
          padding: 2px 8px; border: none; border-radius: 4px;
          font-size: 10px; cursor: pointer; font-weight: 500;
          background: transparent; color: var(--muted); font-family: inherit;
        }
        .opb-act:hover { background: var(--bg-subtle, rgba(100,116,139,0.1)); }
        .opb-act--primary { background: #3b82f6; color: #fff; }
        .opb-act--primary:hover { background: #2563eb; }
        .opb-act--success { background: #22c55e; color: #fff; }
        .opb-act--success:hover { background: #16a34a; }
        .opb-act--danger { color: #ef4444; }
        .opb-act--danger:hover { background: rgba(239,68,68,0.1); }
        .opb-act--danger-fill { background: #ef4444; color: #fff; }
        .opb-act--danger-fill:hover { background: #dc2626; }
        .opb-act--ghost { background: rgba(59,130,246,0.1); color: #3b82f6; }
        .opb-act--ghost:hover { background: rgba(59,130,246,0.2); }

        /* ── Gantt ── */
        .opb-gantt {
          flex: 1; overflow: auto; padding: 12px 16px 16px;
          background: color-mix(in srgb, var(--panel) 88%, var(--bg-app) 12%);
        }
        .opb-gantt-row {
          display: flex; flex-direction: column; gap: 6px;
          padding: 12px 14px; cursor: pointer; margin-bottom: 10px;
          border: 1px solid color-mix(in srgb, var(--line) 90%, transparent);
          border-radius: 14px; background: var(--card-bg);
          box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .opb-gantt-row:hover {
          background: color-mix(in srgb, var(--card-bg) 92%, var(--primary) 8%);
          border-color: color-mix(in srgb, var(--primary) 18%, var(--line));
        }

        /* ── Kanban ── */
        .opb-kanban {
          flex: 1; display: flex; gap: 10px; padding: 12px 16px;
          overflow-x: auto; overflow-y: hidden;
          background: color-mix(in srgb, var(--panel) 88%, var(--bg-app) 12%);
        }
        .opb-kanban-col {
          flex: 1 1 170px; min-width: 170px; max-width: 260px;
          display: flex; flex-direction: column;
          background: color-mix(in srgb, var(--card-bg) 65%, var(--bg-subtle) 35%);
          border-radius: 14px; overflow: hidden;
          border: 1px solid color-mix(in srgb, var(--line) 85%, transparent);
        }
        .opb-kanban-col-header {
          padding: 8px 10px; display: flex; align-items: center; gap: 6px;
          flex-shrink: 0;
        }
        .opb-kanban-col-count {
          font-size: 10px; color: var(--muted);
          background: var(--bg-app); padding: 1px 6px; border-radius: 8px;
        }
        .opb-kanban-list {
          flex: 1; overflow-y: auto; padding: 4px 6px 6px;
          display: flex; flex-direction: column; gap: 4px;
        }
        .opb-kanban-card {
          padding: 8px 10px; border-radius: 8px;
          background: var(--card-bg); border: 1px solid color-mix(in srgb, var(--line) 90%, transparent);
          cursor: pointer;
          box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .opb-kanban-card:hover {
          border-color: color-mix(in srgb, var(--primary) 24%, var(--line));
          box-shadow: 0 4px 12px rgba(37, 99, 235, 0.08);
        }
        .opb-kanban-card__title {
          font-size: 13px; font-weight: 600; color: var(--text);
          line-height: 1.35; margin-bottom: 6px;
          word-break: break-word;
        }
        .opb-kanban-card__footer {
          display: flex; align-items: flex-start; justify-content: space-between;
          gap: 8px; flex-wrap: wrap;
        }
        .opb-kanban-card__owner {
          min-width: 0; flex: 1 1 96px;
          display: flex; align-items: center; gap: 4px;
        }
        .opb-kanban-card__owner-label {
          min-width: 0; font-size: 10px; color: var(--muted);
          line-height: 1.35; word-break: break-word;
        }
        .opb-kanban-card__actions {
          display: flex; gap: 4px; flex-wrap: wrap;
          justify-content: flex-end; margin-left: auto;
        }

        @media (max-width: 900px) {
          .opb-stats-actions {
            width: 100%;
            justify-content: flex-start;
          }
        }

        @media (max-width: 720px) {
          .opb-stats-row {
            padding-inline: 12px;
          }
          .opb-stats-summary {
            flex-basis: 100%;
          }
          .opb-progress-track {
            flex-basis: 100%;
            margin-inline: 0;
          }
          .opb-kanban {
            padding-inline: 12px;
          }
          .opb-kanban-card__footer {
            flex-direction: column;
            align-items: stretch;
          }
          .opb-kanban-card__actions {
            width: 100%;
            margin-left: 0;
            justify-content: flex-start;
          }
        }

        /* ── Empty state ── */
        .opb-empty {
          flex: 1; display: flex; flex-direction: column;
          align-items: center; justify-content: center; gap: 16px;
          color: var(--muted);
        }

        /* ── Detail panel ── */
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

      {projects.length > 0 && (
        <div ref={projectRailRef} className="opb-project-rail">
          <div
            ref={projectStripRef}
            className="opb-project-strip"
            style={{ width: projectStripWidth ? `${projectStripWidth}px` : undefined }}
            onWheel={(e) => {
              const el = e.currentTarget;
              if (el.scrollWidth <= el.clientWidth) return;
              if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
              e.preventDefault();
              el.scrollLeft += e.deltaY;
            }}
          >
            <div ref={projectTrackRef} className="opb-project-track">
              {projects.map((project) => {
                const total = project.tasks.length;
                const done = project.tasks.filter((t) => t.status === "accepted").length;
                const selected = project.id === selectedProjectId;
                return (
                  <Card
                    key={project.id}
                    className={`opb-project-card py-0 ${selected ? "opb-project-card--selected" : ""}`}
                    onClick={() => setSelectedProjectId(project.id)}
                  >
                    <div className="opb-project-card__actions">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="opb-project-card__edit text-muted-foreground hover:bg-primary/10 hover:text-primary"
                        onClick={(e) => {
                          e.stopPropagation();
                          openEditProject(project);
                        }}
                        title={`Edit project ${project.name}`}
                        aria-label={`Edit project ${project.name}`}
                      >
                        <Pencil />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className="opb-project-card__delete text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        onClick={(e) => {
                          e.stopPropagation();
                          setProjectPendingDelete(project);
                        }}
                        title={`Delete project ${project.name}`}
                        aria-label={`Delete project ${project.name}`}
                      >
                        <X />
                      </Button>
                    </div>
                    <CardContent className="px-3 py-3">
                      <div className="opb-project-card__body">
                        <div className="opb-project-card__meta">
                          <Badge variant="secondary" className="h-5 gap-1 px-1.5 text-[10px] font-medium">
                            <span className="opb-status-dot" style={{ background: PROJECT_STATUS_COLOR[project.status] || "#3b82f6" }} />
                            {PROJECT_STATUS_LABEL[project.status] || project.status}
                          </Badge>
                          <Badge variant="outline" className="h-5 px-1.5 text-[10px] font-medium">
                            {PROJECT_TYPE_LABEL[project.project_type] || project.project_type}
                          </Badge>
                        </div>
                        <div className="opb-project-card__summary">
                          <div className="opb-project-card__title">{project.name}</div>
                          <div className="opb-project-card__desc">{project.description || "No description"}</div>
                        </div>
                        <div className="opb-project-card__stats">
                          <div>
                            Tasks
                            <strong>{total}</strong>
                          </div>
                          <div>
                            Completed
                            <strong>{done}</strong>
                          </div>
                          <div>
                            Progress
                            <strong>{total > 0 ? Math.round((done / total) * 100) : 0}%</strong>
                          </div>
                        </div>
                        <div className="opb-project-card__progress">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${total > 0 ? Math.round((done / total) * 100) : 0}%`,
                              background: "linear-gradient(90deg, var(--primary), color-mix(in srgb, var(--primary) 78%, white))",
                            }}
                          />
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          </div>
          <div
            ref={projectAddRef}
            className="opb-project-add-slot"
            style={{ paddingBottom: projectScrollbarSize ? `${projectScrollbarSize}px` : undefined }}
          >
            <Card className="opb-project-add-card py-0" onClick={() => {
              resetProjectForm();
              setShowNewProject(true);
            }}>
              <CardContent className="flex h-full flex-col items-center justify-center gap-2 px-3 py-3 text-center">
                <span className="text-lg leading-none">+</span>
                <span className="text-xs font-medium">New Project</span>
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {/* ── Stats row ── */}
      {selectedProject && (
        <div className="opb-stats-row">
          <div className="opb-stats-summary">
            {projectStats.total > 0 ? (<>
              <span className="opb-stat-chip">
                Total <strong>{projectStats.total}</strong>
              </span>
              {projectStats.inProgress > 0 && (
                <span className="opb-stat-chip" style={{ color: "#3b82f6" }}>
                  <span className="opb-status-dot" style={{ background: "#3b82f6", width: 6, height: 6 }} />
                  In Progress {projectStats.inProgress}
                </span>
              )}
              {projectStats.done > 0 && (
                <span className="opb-stat-chip" style={{ color: "#22c55e" }}>
                  <span className="opb-status-dot" style={{ background: "#22c55e", width: 6, height: 6 }} />
                  Completed {projectStats.done}
                </span>
              )}
              {projectStats.blocked > 0 && (
                <span className="opb-stat-chip" style={{ color: "#ef4444" }}>
                  <span className="opb-status-dot" style={{ background: "#ef4444", width: 6, height: 6 }} />
                  Error {projectStats.blocked}
                </span>
              )}

              <div className="opb-progress-track">
                {projectStats.done > 0 && <div className="opb-progress-fill" style={{ width: `${(projectStats.done / projectStats.total) * 100}%`, background: "#22c55e" }} />}
                {projectStats.delivered > 0 && <div className="opb-progress-fill" style={{ width: `${(projectStats.delivered / projectStats.total) * 100}%`, background: "#8b5cf6" }} />}
                {projectStats.inProgress > 0 && <div className="opb-progress-fill" style={{ width: `${(projectStats.inProgress / projectStats.total) * 100}%`, background: "#3b82f6" }} />}
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, minWidth: 32, textAlign: "right" }}>{projectStats.pct}%</span>
            </>) : (
              <span style={{ color: "var(--muted)", fontSize: 12 }}>No tasks</span>
            )}
          </div>

          <div className="opb-stats-actions">
            <ToggleGroup type="single" variant="outline" value={viewTab}
              onValueChange={v => { if (v) setViewTab(v as "gantt" | "kanban"); }}
              className="h-8">
              <ToggleGroupItem value="gantt" className={`text-xs px-3 h-7 ${viewTab === "gantt" ? "!bg-primary !text-primary-foreground !border-primary" : ""}`}>
                Task List
              </ToggleGroupItem>
              <ToggleGroupItem value="kanban" className={`text-xs px-3 h-7 ${viewTab === "kanban" ? "!bg-primary !text-primary-foreground !border-primary" : ""}`}>
                Board
              </ToggleGroupItem>
            </ToggleGroup>
            <Button size="sm" className="h-7 text-xs" onClick={() => setShowNewTask(true)}>
              + New Task
            </Button>
          </div>
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
            onCancel={(tid) => cancelTask(selectedProject.id, tid)}
            onDelete={(task) => requestTaskDelete(selectedProject.id, task)}
            dispatchingTaskId={dispatchingTaskId}
            cancellingTaskId={cancellingTaskId}
          />
        ) : (
          <KanbanView
            tasks={tasks}
            nodeMap={nodeMap}
            onTaskClick={openTaskDetail}
            onStatusChange={(tid, st) => updateTaskStatus(selectedProject.id, tid, st)}
            onDispatch={(tid) => dispatchTask(selectedProject.id, tid)}
            onCancel={(tid) => cancelTask(selectedProject.id, tid)}
            onDelete={(task) => requestTaskDelete(selectedProject.id, task)}
            dispatchingTaskId={dispatchingTaskId}
            cancellingTaskId={cancellingTaskId}
          />
        )
      ) : (
        <div className="opb-empty">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.4 }}>
            <path d="M3 3h7l2 2h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/>
            <line x1="12" y1="10" x2="12" y2="14"/><line x1="10" y1="12" x2="14" y2="12"/>
          </svg>
          <span style={{ fontSize: 14 }}>No projects yet</span>
          <span style={{ fontSize: 12 }}>Create a project to manage your organization's tasks and progress</span>
          <Button onClick={() => setShowNewProject(true)}>Create Your First Project</Button>
        </div>
      )}

      {/* ── New Project Modal ── */}
      <Dialog open={showNewProject} onOpenChange={(open) => {
        setShowNewProject(open);
        if (!open) resetProjectForm();
      }}>
        <DialogContent className="sm:max-w-md" onOpenAutoFocus={e => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>{editingProject ? "Edit Project" : "New Project"}</DialogTitle>
            <DialogDescription className="sr-only">
              {editingProject ? "Edit the current organization project" : "Create a new organization project"}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2 grid gap-2">
                <Label htmlFor="project-name">Project Name *</Label>
                <Input id="project-name" placeholder="e.g. Q2 Product Iteration" value={newProjectName}
                  onChange={e => setNewProjectName(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && submitProject()} />
              </div>
              <div className="grid gap-2">
                <Label>Project Type</Label>
                <ToggleGroup type="single" variant="outline" value={newProjectType}
                  onValueChange={v => { if (v) setNewProjectType(v as "temporary" | "permanent"); }}
                  className="h-9">
                  {(["temporary", "permanent"] as const).map(t => (
                    <ToggleGroupItem key={t} value={t}
                      className={`flex-1 ${newProjectType === t ? "!bg-primary !text-primary-foreground !border-primary" : ""}`}>
                      {PROJECT_TYPE_LABEL[t]}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="project-desc">Project Description</Label>
              <Textarea id="project-desc" placeholder="Project goals and scope..."
                value={newProjectDesc} onChange={e => setNewProjectDesc(e.target.value)}
                className="min-h-[80px] resize-y" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => {
              setShowNewProject(false);
              resetProjectForm();
            }}>Cancel</Button>
            <Button onClick={submitProject}>{editingProject ? "Save" : "Create"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── New Task Modal ── */}
      <Dialog open={showNewTask} onOpenChange={setShowNewTask}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New Task</DialogTitle>
            <DialogDescription className="sr-only">Create a new task for the current project</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="task-title">Task Title *</Label>
              <Input id="task-title" placeholder="e.g. Design homepage prototype" value={newTaskTitle}
                onChange={e => setNewTaskTitle(e.target.value)} autoFocus
                onKeyDown={e => e.key === "Enter" && createTask()} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="task-desc">Task Description</Label>
              <Textarea id="task-desc" placeholder="Task details..."
                value={newTaskDesc} onChange={e => setNewTaskDesc(e.target.value)}
                className="min-h-[60px] resize-y" />
            </div>
            <div className="grid gap-2">
              <Label>Assign to</Label>
              <Select value={newTaskAssignee || "__none__"} onValueChange={v => setNewTaskAssignee(v === "__none__" ? "" : v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Unassigned" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">Unassigned</SelectItem>
                  {nodes.map(n => (
                    <SelectItem key={n.id} value={n.id}>{n.role_title || n.id}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowNewTask(false)}>Cancel</Button>
            <Button onClick={createTask}>Add</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!projectPendingDelete} onOpenChange={(open) => { if (!open) setProjectPendingDelete(null); }}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete project?</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-wrap">
              {projectPendingDelete ? `Are you sure you want to delete project "${projectPendingDelete.name}"?\nThis action cannot be undone, and the tasks within the project will also be deleted.` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (projectPendingDelete) {
                  deleteProject(projectPendingDelete.id);
                }
                setProjectPendingDelete(null);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={!!taskPendingDelete} onOpenChange={(open) => { if (!open) setTaskPendingDelete(null); }}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete task?</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-wrap">
              {taskPendingDelete ? `Are you sure you want to delete task "${taskPendingDelete.taskTitle}"?\nThis action cannot be undone.` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (taskPendingDelete) {
                  deleteTask(taskPendingDelete.projectId, taskPendingDelete.taskId);
                }
                setTaskPendingDelete(null);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ── Task Detail Panel ── */}
      {selectedTask && (
        <div className="opb-detail-overlay" onClick={closeTaskDetail}>
          <div className="opb-detail-panel" onClick={e => e.stopPropagation()}>
            <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>Task Details</span>
              <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground" onClick={closeTaskDetail}>×</Button>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
              {taskDetailLoading ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: 24 }}>Loading...</div>
              ) : taskDetail ? (
                <TaskDetailContent
                  task={taskDetail} timeline={taskTimeline} nodeMap={nodeMap}
                  apiBaseUrl={apiBaseUrl}
                  subtasksExpanded={subtasksExpanded} setSubtasksExpanded={setSubtasksExpanded}
                  onAncestorClick={(t: any) => { setSelectedTask(t); fetchTaskDetail(t.id); }}
                  statusLabel={(s: string) => STATUS_META[s]?.label || s}
                  onStatusChange={async (st: string) => {
                    await updateTaskStatus(taskDetail.project_id, taskDetail.id, st);
                    fetchTaskDetail(taskDetail.id);
                  }}
                  onDispatch={async () => {
                    await dispatchTask(taskDetail.project_id, taskDetail.id);
                    fetchTaskDetail(taskDetail.id);
                  }}
                  onCancel={async () => {
                    await cancelTask(taskDetail.project_id, taskDetail.id);
                    fetchTaskDetail(taskDetail.id);
                  }}
                  dispatchingTaskId={dispatchingTaskId}
                  cancellingTaskId={cancellingTaskId}
                />
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: 24 }}>Unable to load task details</div>
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
  tasks, nodeMap, onTaskClick, onStatusChange, onDispatch, onCancel, onDelete, dispatchingTaskId, cancellingTaskId,
}: {
  tasks: ProjectTask[];
  nodeMap: Map<string, { id: string; role_title?: string; avatar?: string | null }>;
  onTaskClick: (t: ProjectTask) => void;
  onStatusChange: (tid: string, status: string) => void;
  onDispatch: (tid: string) => void;
  onCancel: (tid: string) => void;
  onDelete: (task: ProjectTask) => void;
  dispatchingTaskId: string | null;
  cancellingTaskId: string | null;
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
        <div style={{ padding: 40, textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
          No tasks yet. Click "+ New Task" to begin.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {sorted.map(task => {
            const meta = STATUS_META[task.status] || { label: task.status, color: "#64748b" };
            const assignee = task.assignee_node_id ? nodeMap.get(task.assignee_node_id) : null;
            const pct = task.progress_pct ?? 0;
            const barStyle = getBarStyle(task);
            return (
              <div key={task.id} className="opb-gantt-row" onClick={() => onTaskClick(task)}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <OrgAvatar avatarId={(assignee as any)?.avatar || null} size={24} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{task.title}</div>
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 1 }}>
                      {assignee ? (assignee.role_title || assignee.id) : "Unassigned"}
                      <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 500, opacity: 0.68 }}>#{task.id.slice(0, 8)}</span>
                    </div>
                  </div>
                  <div style={{ flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <span className="opb-status-badge" style={{ background: meta.color + "18", color: meta.color, fontSize: 10, padding: "1px 6px" }}>
                        {meta.label}
                      </span>
                      {pct > 0 && <span style={{ fontSize: 10, fontWeight: 600, color: meta.color }}>{pct}%</span>}
                    </div>
                    <div style={{ display: "flex", gap: 4, alignItems: "center" }} onClick={e => e.stopPropagation()}>
                      {task.status === "todo" && (
                        <Button variant="outline" size="xs" className="h-6 px-2"
                          onClick={() => onDispatch(task.id)} disabled={dispatchingTaskId === task.id}>
                          <Play className="h-3 w-3" />
                          {dispatchingTaskId === task.id ? "…" : "Dispatch"}
                        </Button>
                      )}
                      {task.status === "in_progress" && (
                        <Button variant="outline" size="xs" className="h-6 px-2 text-destructive border-destructive/40 hover:bg-destructive/10"
                          onClick={() => onCancel(task.id)} disabled={cancellingTaskId === task.id} title="Abort the running task">
                          <X className="h-3 w-3" />
                          {cancellingTaskId === task.id ? "Aborting…" : "Abort"}
                        </Button>
                      )}
                      {task.status === "delivered" && (<>
                        <Button variant="outline" size="xs" className="h-6 px-2" onClick={() => onStatusChange(task.id, "accepted")}>
                          <Check className="h-3 w-3" />Accept
                        </Button>
                        <Button variant="outline" size="xs" className="h-6 px-2 text-destructive border-destructive/40 hover:bg-destructive/10" onClick={() => onStatusChange(task.id, "rejected")}>
                          <CornerUpLeft className="h-3 w-3" />Reject
                        </Button>
                      </>)}
                      {(task.status === "rejected" || task.status === "blocked" || task.status === "cancelled") && (
                        <Button variant="outline" size="xs" className="h-6 px-2"
                          onClick={() => onDispatch(task.id)} disabled={dispatchingTaskId === task.id}>
                          <RefreshCw className="h-3 w-3" />
                          {dispatchingTaskId === task.id ? "…" : "Redispatch"}
                        </Button>
                      )}
                      <Button variant="outline" size="xs" className="h-6 px-2 text-muted-foreground hover:text-destructive hover:border-destructive/40 hover:bg-destructive/10"
                        onClick={() => onDelete(task)}
                        title="Delete task"><Trash2 className="h-3 w-3" /></Button>
                    </div>
                  </div>
                </div>
                {task.description && (
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", paddingLeft: 32 }}>
                    {task.description}
                  </div>
                )}
                <div style={{ position: "relative", height: 12, paddingLeft: 32, marginTop: 2 }}>
                  <div style={{
                    position: "relative",
                    height: "100%",
                    background: "color-mix(in srgb, var(--bg-subtle) 76%, var(--line) 24%)",
                    borderRadius: 999,
                    overflow: "hidden",
                    border: "1px solid color-mix(in srgb, var(--line) 82%, transparent)",
                  }}>
                    <div style={{
                      position: "absolute",
                      top: 1,
                      bottom: 1,
                      left: barStyle.left,
                      width: barStyle.width,
                      borderRadius: 999,
                      background: `linear-gradient(90deg, ${meta.color}33, ${meta.color}20)`,
                      border: `1px solid ${meta.color}35`,
                    }}>
                      <div style={{
                        position: "absolute", left: 1, top: 1, bottom: 1,
                        width: `calc(${pct}% - 2px)`,
                        minWidth: pct > 0 ? 6 : 0,
                        background: `linear-gradient(90deg, ${meta.color}, ${meta.color}cc)`,
                        borderRadius: 999,
                        boxShadow: `0 0 0 1px ${meta.color}22 inset`,
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
  tasks, nodeMap, onTaskClick, onStatusChange, onDispatch, onCancel, onDelete, dispatchingTaskId, cancellingTaskId,
}: {
  tasks: ProjectTask[];
  nodeMap: Map<string, { id: string; role_title?: string; avatar?: string | null }>;
  onTaskClick: (t: ProjectTask) => void;
  onStatusChange: (tid: string, status: string) => void;
  onDispatch: (tid: string) => void;
  onCancel: (tid: string) => void;
  onDelete: (task: ProjectTask) => void;
  dispatchingTaskId: string | null;
  cancellingTaskId: string | null;
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
                    <div className="opb-kanban-card__title">{task.title}</div>
                    <div className="opb-kanban-card__footer">
                      <div className="opb-kanban-card__owner">
                        <OrgAvatar avatarId={(assignee as any)?.avatar || null} size={16} />
                        <span className="opb-kanban-card__owner-label">{assignee ? (assignee.role_title || assignee.id) : "Unassigned"}</span>
                      </div>
                      <div className="opb-kanban-card__actions" onClick={e => e.stopPropagation()}>
                        {col.key === "todo" && (
                          <Button variant="outline" size="xs" className="h-6 px-2"
                            onClick={() => onDispatch(task.id)} disabled={dispatchingTaskId === task.id}>
                            <Play className="h-3 w-3" />
                            {dispatchingTaskId === task.id ? "…" : "Dispatch"}
                          </Button>
                        )}
                        {col.key === "in_progress" && (
                          <Button variant="outline" size="xs" className="h-6 px-2 text-destructive border-destructive/40 hover:bg-destructive/10"
                            onClick={() => onCancel(task.id)} disabled={cancellingTaskId === task.id} title="Abort the running task">
                            <X className="h-3 w-3" />
                            {cancellingTaskId === task.id ? "…" : "Abort"}
                          </Button>
                        )}
                        {col.key === "delivered" && (<>
                          <Button variant="outline" size="xs" className="h-6 px-2" onClick={() => onStatusChange(task.id, "accepted")} title="Accept">
                            <Check className="h-3 w-3" />Accept
                          </Button>
                          <Button variant="outline" size="xs" className="h-6 px-2 text-destructive border-destructive/40 hover:bg-destructive/10" onClick={() => onStatusChange(task.id, "rejected")} title="Reject">
                            <CornerUpLeft className="h-3 w-3" />Reject
                          </Button>
                        </>)}
                        {(col.key === "rejected" || col.key === "blocked" || col.key === "cancelled") && (
                          <Button variant="outline" size="xs" className="h-6 px-2"
                            onClick={() => onDispatch(task.id)} disabled={dispatchingTaskId === task.id}>
                            <RefreshCw className="h-3 w-3" />
                            {dispatchingTaskId === task.id ? "…" : "Redispatch"}
                          </Button>
                        )}
                        <Button variant="outline" size="xs" className="h-6 px-2 text-muted-foreground hover:text-destructive hover:border-destructive/40 hover:bg-destructive/10"
                          onClick={() => onDelete(task)}><Trash2 className="h-3 w-3" /></Button>
                      </div>
                    </div>
                    {(task.progress_pct ?? 0) > 0 && (task.progress_pct ?? 0) < 100 && (
                      <div style={{
                        marginTop: 8,
                        height: 8,
                        borderRadius: 999,
                        background: "color-mix(in srgb, var(--bg-subtle) 76%, var(--line) 24%)",
                        overflow: "hidden",
                        border: "1px solid color-mix(in srgb, var(--line) 82%, transparent)",
                      }}>
                        <div style={{
                          height: "100%",
                          borderRadius: 999,
                          background: `linear-gradient(90deg, ${col.color}, ${col.color}cc)`,
                          width: `${task.progress_pct}%`,
                        }} />
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
  onStatusChange, onDispatch, onCancel, dispatchingTaskId, cancellingTaskId, apiBaseUrl,
}: {
  task: any; timeline: any[];
  nodeMap: Map<string, { id: string; role_title?: string; avatar?: string | null }>;
  subtasksExpanded: boolean; setSubtasksExpanded: (v: boolean) => void;
  onAncestorClick: (t: any) => void; statusLabel: (s: string) => string;
  onStatusChange: (status: string) => void;
  onDispatch: () => void;
  onCancel: () => void;
  dispatchingTaskId: string | null;
  cancellingTaskId: string | null;
  apiBaseUrl: string;
}) {
  const md = useMdModules();
  const assignee = task.assignee_node_id ? nodeMap.get(task.assignee_node_id) : null;
  const delegatedBy = task.delegated_by ? nodeMap.get(task.delegated_by) : null;
  const fmt = (s: string | null | undefined) => s ? new Date(s).toLocaleString("zh-CN") : "-";
  const meta = STATUS_META[task.status] || { label: task.status, color: "#64748b" };
  const progress = Math.min(100, Math.max(0, task.progress_pct ?? 0));

  const statusContext = (() => {
    const assigneeName = assignee ? (assignee.role_title || assignee.id) : "Unassigned";
    const actionBtn = "h-7 px-2.5 text-xs shrink-0";
    switch (task.status) {
      case "todo":
        return (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed px-3 py-2">
            <span className="min-w-0 flex-1 text-xs text-muted-foreground">This task has not been dispatched yet</span>
            <Button variant="outline" size="sm" className={actionBtn} onClick={onDispatch} disabled={dispatchingTaskId === task.id}>
              <Play className="h-3 w-3 mr-1" />{dispatchingTaskId === task.id ? "Dispatching…" : "Dispatch"}
            </Button>
          </div>
        );
      case "in_progress":
        return (
          <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
            <span className="min-w-0 flex-1 text-xs">Currently being executed by <strong>{assigneeName}</strong></span>
            <Button variant="outline" size="sm" className={`${actionBtn} text-destructive border-destructive/40 hover:bg-destructive/10`} onClick={onCancel} disabled={cancellingTaskId === task.id}>
              <X className="h-3 w-3 mr-1" />{cancellingTaskId === task.id ? "Aborting…" : "Abort"}
            </Button>
          </div>
        );
      case "delivered":
        return (
          <div className="rounded-lg border border-amber-500/50 bg-amber-50/30 dark:bg-amber-950/20 p-3 space-y-2">
            <div className="text-xs font-medium text-amber-700 dark:text-amber-400">Pending Review</div>
            {task.delivery_summary && (
              <div className="text-xs text-muted-foreground">{task.delivery_summary}</div>
            )}
            {task.deliverable_content ? (
              <div className="bg-muted/50 rounded p-2 text-xs max-h-40 overflow-y-auto chatMdContent">
                {md ? (
                  <md.ReactMarkdown remarkPlugins={md.remarkPlugins} rehypePlugins={md.rehypePlugins}>
                    {task.deliverable_content}
                  </md.ReactMarkdown>
                ) : (
                  <div className="whitespace-pre-wrap break-all">{task.deliverable_content}</div>
                )}
              </div>
            ) : (
              <div className="text-muted-foreground text-xs italic">Deliverable content not recorded</div>
            )}
            {task.file_attachments && task.file_attachments.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                {task.file_attachments.map((f: FileAttachment, i: number) => (
                  <FileAttachmentCard key={f.file_path || i} file={f} apiBaseUrl={apiBaseUrl} />
                ))}
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <Button variant="outline" size="sm" className={actionBtn} onClick={() => onStatusChange("accepted")}>
                <Check className="h-3 w-3 mr-1" />Accept
              </Button>
              <Button variant="outline" size="sm" className={`${actionBtn} text-destructive border-destructive/40 hover:bg-destructive/10`} onClick={() => onStatusChange("rejected")}>
                <CornerUpLeft className="h-3 w-3 mr-1" />Reject
              </Button>
            </div>
          </div>
        );
      case "accepted":
        return (
          <div className="rounded-lg border border-green-500/50 bg-green-50/30 dark:bg-green-950/20 p-3 space-y-2">
            <div className="text-xs font-medium text-green-700 dark:text-green-400">Accepted</div>
            {task.deliverable_content ? (
              <div className="bg-muted/50 rounded p-2 text-xs max-h-40 overflow-y-auto chatMdContent">
                {md ? (
                  <md.ReactMarkdown remarkPlugins={md.remarkPlugins} rehypePlugins={md.rehypePlugins}>
                    {task.deliverable_content}
                  </md.ReactMarkdown>
                ) : (
                  <div className="whitespace-pre-wrap break-all">{task.deliverable_content}</div>
                )}
              </div>
            ) : task.delivery_summary ? (
              <div className="text-xs text-muted-foreground">{task.delivery_summary}</div>
            ) : null}
            {task.file_attachments && task.file_attachments.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                {task.file_attachments.map((f: FileAttachment, i: number) => (
                  <FileAttachmentCard key={f.file_path || i} file={f} apiBaseUrl={apiBaseUrl} />
                ))}
              </div>
            )}
          </div>
        );
      case "rejected":
        return (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-red-500/50 px-3 py-2">
            <span className="min-w-0 flex-1 text-xs text-red-600 dark:text-red-400">Rejected</span>
            <Button variant="outline" size="sm" className={actionBtn} onClick={onDispatch} disabled={dispatchingTaskId === task.id}>
              <RefreshCw className="h-3 w-3 mr-1" />{dispatchingTaskId === task.id ? "…" : "Redispatch"}
            </Button>
          </div>
        );
      case "blocked":
      case "cancelled":
        return (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed px-3 py-2">
            <span className="min-w-0 flex-1 text-xs text-muted-foreground">{task.status === "blocked" ? "Task blocked" : "Task cancelled"}</span>
            <Button variant="outline" size="sm" className={actionBtn} onClick={onDispatch} disabled={dispatchingTaskId === task.id}>
              <RefreshCw className="h-3 w-3 mr-1" />{dispatchingTaskId === task.id ? "…" : "Redispatch"}
            </Button>
          </div>
        );
      default:
        return null;
    }
  })();

  return (
    <div className="flex flex-col gap-3 text-xs">
      {statusContext}

      {(task.ancestors?.length ?? 0) > 0 && (
        <div className="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
          <span>Parent task:</span>
          {(task.ancestors || []).map((a: any, i: number) => (
            <span key={a.id} className="inline-flex items-center gap-1">
              {i > 0 && <span className="text-muted-foreground/60">/</span>}
              <Button variant="link" size="xs" className="h-auto px-0 text-xs text-primary" onClick={() => onAncestorClick(a)}>
                {a.title || a.id}
              </Button>
            </span>
          ))}
        </div>
      )}

      <Card className="gap-0 py-0">
        <CardHeader className="gap-3 px-4 pt-4 pb-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-normal text-[10px] text-muted-foreground">
              #{task.id}
            </Badge>
            <Badge variant="secondary" className="gap-1 border-0" style={{ background: meta.color + "18", color: meta.color }}>
              <span className="opb-status-dot" style={{ background: meta.color }} />
              {meta.label}
            </Badge>
          </div>
          <CardTitle className="text-xl leading-tight">{task.title}</CardTitle>
          {task.description ? (
            <CardDescription className="text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
              {task.description}
            </CardDescription>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4 px-4 py-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">Progress</span>
              <span className="font-semibold">{progress}%</span>
            </div>
            <div
              className="h-2.5 overflow-hidden rounded-full border"
              style={{
                background: "color-mix(in srgb, var(--bg-subtle) 76%, var(--line) 24%)",
                borderColor: "color-mix(in srgb, var(--line) 82%, transparent)",
              }}
            >
              <div
                className="h-full rounded-full transition-[width]"
                style={{
                  width: `${progress}%`,
                  background: `linear-gradient(90deg, ${meta.color}, ${meta.color}cc)`,
                }}
              />
            </div>
          </div>

          <div className="grid gap-2 text-xs sm:grid-cols-2">
            {assignee && (
              <div className="rounded-lg border px-3 py-2">
                <div className="text-[11px] text-muted-foreground">Assignee</div>
                <div className="mt-1 font-medium">{assignee.role_title || assignee.id}</div>
              </div>
            )}
            {delegatedBy && (
              <div className="rounded-lg border px-3 py-2">
                <div className="text-[11px] text-muted-foreground">Delegated by</div>
                <div className="mt-1 font-medium">{delegatedBy.role_title || delegatedBy.id}</div>
              </div>
            )}
            <div className="rounded-lg border px-3 py-2 sm:col-span-2">
              <div className="text-[11px] text-muted-foreground">Created at</div>
              <div className="mt-1 font-medium">{fmt(task.created_at)}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {(task.plan_steps?.length ?? 0) > 0 && (
        <Card className="gap-0 py-0">
          <CardHeader className="px-4 pt-4 pb-0">
            <CardTitle className="text-base">Plan Steps</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 px-4 py-4">
            {(task.plan_steps || []).map((s: any, i: number) => {
              const st = s.status || "pending";
              const label = st === "completed" ? "Completed" : st === "in_progress" ? "In Progress" : "Pending";
              const c = st === "completed" ? "#22c55e" : st === "in_progress" ? "#3b82f6" : "#94a3b8";
              return (
                <div key={s.id || i} className="flex items-start gap-3 rounded-lg border px-3 py-2">
                  <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: c }} />
                  <div className="min-w-0 flex-1 text-sm leading-5">{s.description || s.title || `Step ${i + 1}`}</div>
                  <Badge variant="outline" className="text-[10px]" style={{ color: c }}>
                    {label}
                  </Badge>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {(task.subtasks?.length ?? 0) > 0 && (
        <Card className="gap-0 py-0">
          <CardHeader className="px-4 pt-4 pb-0">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-base">Subtasks</CardTitle>
              <Button variant="ghost" size="xs" className="text-xs text-muted-foreground" onClick={() => setSubtasksExpanded(!subtasksExpanded)}>
                {subtasksExpanded ? "Collapse" : "Expand"} {task.subtasks.length}
              </Button>
            </div>
          </CardHeader>
          {subtasksExpanded && (
            <CardContent className="space-y-2 px-4 py-4">
              {(task.subtasks || []).map((st: any) => {
                const sm = STATUS_META[st.status] || { label: st.status, color: "#64748b" };
                const subProgress = Math.min(100, Math.max(0, st.progress_pct ?? 0));
                return (
                  <div key={st.id} className="space-y-2 rounded-lg border px-3 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0 text-sm font-medium">{st.title}</div>
                      <Badge variant="secondary" style={{ background: sm.color + "18", color: sm.color }}>
                        {sm.label}
                      </Badge>
                    </div>
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                      <span>Progress</span>
                      <span>{subProgress}%</span>
                    </div>
                    <div
                      className="h-2 overflow-hidden rounded-full border"
                      style={{
                        background: "color-mix(in srgb, var(--bg-subtle) 76%, var(--line) 24%)",
                        borderColor: "color-mix(in srgb, var(--line) 82%, transparent)",
                      }}
                    >
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${subProgress}%`,
                          background: `linear-gradient(90deg, ${sm.color}, ${sm.color}cc)`,
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </CardContent>
          )}
        </Card>
      )}

      <Card className="gap-0 py-0">
        <CardHeader className="px-4 pt-4 pb-0">
          <CardTitle className="text-base">Execution Timeline</CardTitle>
        </CardHeader>
        <CardContent className="px-4 py-4">
        {timeline.length === 0 ? (
          <div className="text-xs text-muted-foreground">No events</div>
        ) : (
          <div className="flex max-h-[240px] flex-col gap-2 overflow-y-auto pr-1">
            {timeline.map((ev: any, i: number) => (
              <div key={i} className="rounded-lg border px-3 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium">{ev.event || "event"}</div>
                  <div className="text-[11px] text-muted-foreground">{ev.ts ? new Date(ev.ts).toLocaleString("zh-CN") : ""}</div>
                </div>
                {ev.actor && <div className="mt-1 text-[11px] text-muted-foreground">by {ev.actor}</div>}
                {ev.detail && <div className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">{String(ev.detail)}</div>}
              </div>
            ))}
          </div>
        )}
        </CardContent>
      </Card>
    </div>
  );
}
