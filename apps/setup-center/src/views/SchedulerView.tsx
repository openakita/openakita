import React, { useEffect, useState, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  IconRefresh, IconPlus, IconTrash, IconEdit, IconCheck, IconX,
  IconPlay, IconClock, IconCalendar, IconSearch,
  DotGreen, DotGray, DotYellow, DotRed,
} from "../icons";

type ScheduledTask = {
  id: string;
  name: string;
  description: string;
  trigger_type: string;
  trigger_config: Record<string, any>;
  task_type: string;
  reminder_message: string | null;
  prompt: string;
  channel_id: string | null;
  chat_id: string | null;
  enabled: boolean;
  status: string;
  deletable: boolean;
  last_run: string | null;
  next_run: string | null;
  run_count: number;
  fail_count: number;
  created_at: string;
  updated_at: string;
  metadata: Record<string, any>;
};

type IMChannel = {
  channel_id: string;
  chat_id: string;
  user_id: string | null;
  last_active: string;
};

// Frontend-only schedule mode; maps to backend trigger_type (once/interval/cron)
type ScheduleMode = "once" | "interval" | "daily" | "weekly" | "monthly" | "custom";

type TaskForm = {
  name: string;
  task_type: string;
  scheduleMode: ScheduleMode;
  // once
  runAt: string;
  // interval
  intervalValue: number;
  intervalUnit: "seconds" | "minutes" | "hours" | "days";
  // daily / weekly / monthly
  timeHour: number;
  timeMinute: number;
  weekday: number;     // 0-6 (Sun-Sat)
  dayOfMonth: number;  // 1-31
  // custom cron
  cronExpr: string;
  // content
  reminder_message: string;
  prompt: string;
  channel_id: string;
  chat_id: string;
  enabled: boolean;
};

const API_BASE = "http://127.0.0.1:18900";

const defaultForm: TaskForm = {
  name: "",
  task_type: "reminder",
  scheduleMode: "once",
  runAt: "",
  intervalValue: 30,
  intervalUnit: "minutes",
  timeHour: 9,
  timeMinute: 0,
  weekday: 1,
  dayOfMonth: 1,
  cronExpr: "",
  reminder_message: "",
  prompt: "",
  channel_id: "",
  chat_id: "",
  enabled: true,
};

function pad2(n: number): string { return n.toString().padStart(2, "0"); }

function safeInt(s: string, fallback: number): number {
  const v = parseInt(s, 10);
  return Number.isNaN(v) ? fallback : v;
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

/** Parse backend task data into the frontend ScheduleMode.
 *  Only recognizes simple patterns (single numbers); anything with ranges,
 *  steps or lists falls back to "custom" to avoid destructive edits. */
function detectScheduleMode(triggerType: string, config: Record<string, any>): ScheduleMode {
  if (triggerType === "once") return "once";
  if (triggerType === "interval") return "interval";
  if (triggerType === "cron" && typeof config.cron === "string") {
    const parts = config.cron.trim().split(/\s+/);
    if (parts.length === 5) {
      const [min, hour, day, month, weekday] = parts;
      const isNum = (s: string) => /^\d{1,2}$/.test(s);
      if (!isNum(min) || !isNum(hour)) return "custom";
      if (day === "*" && month === "*" && weekday === "*") return "daily";
      if (day === "*" && month === "*" && isNum(weekday)) return "weekly";
      if (isNum(day) && month === "*" && weekday === "*") return "monthly";
    }
    return "custom";
  }
  return "once";
}

/** Build a TaskForm from an existing backend task for editing */
function taskToForm(task: ScheduledTask): TaskForm {
  const mode = detectScheduleMode(task.trigger_type, task.trigger_config);
  const f: TaskForm = { ...defaultForm };
  f.name = task.name;
  f.task_type = task.task_type;
  f.scheduleMode = mode;
  f.reminder_message = task.reminder_message || "";
  f.prompt = task.prompt || "";
  f.channel_id = task.channel_id || "";
  f.chat_id = task.chat_id || "";
  f.enabled = task.enabled;

  if (mode === "once") {
    const raw = task.trigger_config.run_at || "";
    f.runAt = typeof raw === "string" ? raw.replace(" ", "T").slice(0, 16) : "";
  } else if (mode === "interval") {
    const secs = task.trigger_config.interval_seconds || 0;
    const mins = task.trigger_config.interval_minutes || task.trigger_config.interval || 0;
    const hours = task.trigger_config.interval_hours || 0;
    const days = task.trigger_config.interval_days || 0;
    const totalSecs = days * 86400 + hours * 3600 + mins * 60 + secs;
    if (totalSecs >= 86400 && totalSecs % 86400 === 0) { f.intervalValue = totalSecs / 86400; f.intervalUnit = "days"; }
    else if (totalSecs >= 3600 && totalSecs % 3600 === 0) { f.intervalValue = totalSecs / 3600; f.intervalUnit = "hours"; }
    else if (totalSecs >= 60 && totalSecs % 60 === 0) { f.intervalValue = totalSecs / 60; f.intervalUnit = "minutes"; }
    else { f.intervalValue = Math.max(1, totalSecs) || 30; f.intervalUnit = "seconds"; }
  } else if (mode === "custom") {
    f.cronExpr = task.trigger_config.cron || "";
  } else {
    // daily / weekly / monthly — parse cron parts
    const parts = (task.trigger_config.cron || "0 9 * * *").trim().split(/\s+/);
    f.timeMinute = safeInt(parts[0], 0);
    f.timeHour = safeInt(parts[1], 9);
    if (mode === "weekly") f.weekday = safeInt(parts[4], 1);
    if (mode === "monthly") f.dayOfMonth = safeInt(parts[2], 1);
  }

  return f;
}

/** Convert frontend form back to backend trigger_type + trigger_config */
function formToTrigger(f: TaskForm): { trigger_type: string; trigger_config: Record<string, any> } {
  switch (f.scheduleMode) {
    case "once":
      return { trigger_type: "once", trigger_config: { run_at: f.runAt.replace("T", " ") } };
    case "interval": {
      if (f.intervalUnit === "seconds") {
        return { trigger_type: "interval", trigger_config: { interval_seconds: f.intervalValue } };
      }
      let mins = f.intervalValue;
      if (f.intervalUnit === "hours") mins *= 60;
      if (f.intervalUnit === "days") mins *= 1440;
      return { trigger_type: "interval", trigger_config: { interval_minutes: mins } };
    }
    case "daily":
      return { trigger_type: "cron", trigger_config: { cron: `${f.timeMinute} ${f.timeHour} * * *` } };
    case "weekly":
      return { trigger_type: "cron", trigger_config: { cron: `${f.timeMinute} ${f.timeHour} * * ${f.weekday}` } };
    case "monthly":
      return { trigger_type: "cron", trigger_config: { cron: `${f.timeMinute} ${f.timeHour} ${f.dayOfMonth} * *` } };
    case "custom":
      return { trigger_type: "cron", trigger_config: { cron: f.cronExpr } };
  }
}

/** Human-readable trigger description for task list cards */
function triggerDescription(
  t: (k: string, opts?: any) => string,
  triggerType: string,
  config: Record<string, any>,
): string {
  if (triggerType === "once") {
    return config.run_at ? formatDateTime(config.run_at) : t("scheduler.triggerOnce");
  }
  if (triggerType === "interval") {
    const secs = config.interval_seconds || 0;
    const mins = config.interval_minutes || config.interval || 0;
    const hours = config.interval_hours || 0;
    const days = config.interval_days || 0;
    const totalSecs = days * 86400 + hours * 3600 + mins * 60 + secs;
    if (totalSecs > 0 && totalSecs < 60) return `${t("scheduler.triggerInterval")} ${totalSecs}s`;
    const totalMins = totalSecs / 60;
    if (totalMins >= 1440 && totalMins % 1440 === 0) return `${t("scheduler.triggerInterval")} ${totalMins / 1440} ${t("scheduler.intervalDays")}`;
    if (totalMins >= 60 && totalMins % 60 === 0) return `${t("scheduler.triggerInterval")} ${totalMins / 60} ${t("scheduler.intervalHours")}`;
    return `${t("scheduler.triggerInterval")} ${totalMins} ${t("scheduler.intervalMinutes")}`;
  }
  if (triggerType === "cron" && typeof config.cron === "string") {
    const parts = config.cron.trim().split(/\s+/);
    if (parts.length === 5) {
      const [min, hour, day, month, weekday] = parts;
      const isNum = (s: string) => /^\d{1,2}$/.test(s);
      if (isNum(min) && isNum(hour)) {
        const weekdayNames: string[] = t("scheduler.weekdays", { returnObjects: true }) as any;
        const timeStr = `${pad2(parseInt(hour))}:${pad2(parseInt(min))}`;
        if (day === "*" && month === "*" && weekday === "*") return `${t("scheduler.triggerDaily")} ${timeStr}`;
        if (day === "*" && month === "*" && isNum(weekday)) {
          const wdIdx = parseInt(weekday);
          const wdName = (Array.isArray(weekdayNames) && weekdayNames[wdIdx]) || weekday;
          return `${t("scheduler.triggerWeekly")} ${wdName} ${timeStr}`;
        }
        if (isNum(day) && month === "*" && weekday === "*") return `${t("scheduler.triggerMonthly")} ${day} ${timeStr}`;
      }
    }
    return config.cron;
  }
  return triggerType;
}

const selectStyle: React.CSSProperties = {
  appearance: "none", WebkitAppearance: "none",
  padding: "6px 10px", borderRadius: 6,
  border: "1px solid var(--line, #d1d5db)",
  background: "var(--panel, #fff)",
  color: "var(--text)",
  fontSize: 13,
  cursor: "pointer",
  minWidth: 0,
};

const hourOptions = Array.from({ length: 24 }, (_, i) => i);
const minuteOptions = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];

type TaskTab = "active" | "completed" | "all";

const ACTIVE_STATUSES = new Set(["pending", "scheduled", "running"]);
const COMPLETED_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function SchedulerView({ serviceRunning }: { serviceRunning: boolean }) {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<TaskForm>({ ...defaultForm });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [channels, setChannels] = useState<IMChannel[]>([]);
  const [activeTab, setActiveTab] = useState<TaskTab>("active");
  const [searchQuery, setSearchQuery] = useState("");

  const fetchTasks = useCallback(async (showLoading = true) => {
    if (!serviceRunning) return;
    if (showLoading) setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks`);
      if (res.ok) {
        const data = await res.json();
        setTasks(data.tasks || []);
      }
    } catch { /* ignore */ }
    if (showLoading) setLoading(false);
  }, [serviceRunning]);

  const fetchChannels = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/channels`);
      if (res.ok) {
        const data = await res.json();
        setChannels(data.channels || []);
      }
    } catch { /* ignore */ }
  }, [serviceRunning]);

  useEffect(() => { fetchTasks(); fetchChannels(); }, [fetchTasks, fetchChannels]);

  useEffect(() => {
    if (!serviceRunning) return;
    const interval = setInterval(() => fetchTasks(false), 10_000);
    return () => clearInterval(interval);
  }, [serviceRunning, fetchTasks]);

  const showMsg = (text: string, ok: boolean) => {
    setMessage({ text, ok });
    setTimeout(() => setMessage(null), 4000);
  };

  const openCreate = () => {
    setEditingId(null);
    setForm({ ...defaultForm });
    setShowForm(true);
  };

  const openEdit = (task: ScheduledTask) => {
    setEditingId(task.id);
    setForm(taskToForm(task));
    setShowForm(true);
  };

  const closeForm = () => {
    setShowForm(false);
    setEditingId(null);
    setForm({ ...defaultForm });
  };

  const saveTask = async () => {
    if (!form.name.trim()) { showMsg(t("scheduler.namePlaceholder"), false); return; }

    if (form.scheduleMode === "once" && !form.runAt) {
      showMsg(t("scheduler.runAt"), false); return;
    }
    if (form.scheduleMode === "custom" && !form.cronExpr.trim()) {
      showMsg(t("scheduler.cronExpression"), false); return;
    }
    if (form.task_type === "reminder" && !form.reminder_message.trim()) {
      showMsg(t("scheduler.reminderPlaceholder"), false); return;
    }
    if (form.task_type === "task" && !form.prompt.trim()) {
      showMsg(t("scheduler.promptPlaceholder"), false); return;
    }

    const { trigger_type, trigger_config } = formToTrigger(form);

    setBusy(true);
    try {
      const payload = {
        name: form.name.trim(),
        task_type: form.task_type,
        trigger_type,
        trigger_config,
        reminder_message: form.task_type === "reminder" ? form.reminder_message : null,
        prompt: form.task_type === "task" ? form.prompt : "",
        channel_id: form.channel_id || "",
        chat_id: form.chat_id || "",
        enabled: form.enabled,
      };

      let res: Response;
      if (editingId) {
        res = await fetch(`${API_BASE}/api/scheduler/tasks/${editingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        res = await fetch(`${API_BASE}/api/scheduler/tasks`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }

      const data = await res.json();
      if (data.error) {
        showMsg(data.error, false);
      } else {
        showMsg(editingId ? t("scheduler.updateSuccess") : t("scheduler.createSuccess"), true);
        closeForm();
        await fetchTasks();
      }
    } catch (e) {
      showMsg(String(e), false);
    }
    setBusy(false);
  };

  const deleteTask = async (task: ScheduledTask) => {
    if (!confirm(t("scheduler.confirmDelete", { name: task.name }))) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks/${task.id}`, { method: "DELETE" });
      const data = await res.json();
      if (data.error) {
        showMsg(data.error, false);
      } else {
        showMsg(t("scheduler.deleteSuccess"), true);
        await fetchTasks();
      }
    } catch (e) { showMsg(String(e), false); }
    setBusy(false);
  };

  const toggleTask = async (task: ScheduledTask) => {
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks/${task.id}/toggle`, { method: "POST" });
      const data = await res.json();
      if (!data.error) await fetchTasks();
    } catch { /* ignore */ }
  };

  const triggerTask = async (task: ScheduledTask) => {
    try {
      const res = await fetch(`${API_BASE}/api/scheduler/tasks/${task.id}/trigger`, { method: "POST" });
      const data = await res.json();
      if (data.error) {
        showMsg(data.error, false);
      } else {
        showMsg(t("scheduler.triggerSuccess"), true);
        setTimeout(() => fetchTasks(), 2000);
      }
    } catch (e) { showMsg(String(e), false); }
  };

  const filteredTasks = useMemo(() => {
    let list = tasks;
    if (activeTab === "active") {
      list = list.filter(t => ACTIVE_STATUSES.has(t.status) || (t.status === "disabled" && t.enabled));
    } else if (activeTab === "completed") {
      list = list.filter(t => COMPLETED_STATUSES.has(t.status));
    }
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      list = list.filter(t =>
        t.name.toLowerCase().includes(q) ||
        (t.reminder_message || "").toLowerCase().includes(q) ||
        (t.prompt || "").toLowerCase().includes(q)
      );
    }
    return list;
  }, [tasks, activeTab, searchQuery]);

  const tabCounts = useMemo(() => ({
    active: tasks.filter(t => ACTIVE_STATUSES.has(t.status) || (t.status === "disabled" && t.enabled)).length,
    completed: tasks.filter(t => COMPLETED_STATUSES.has(t.status)).length,
    all: tasks.length,
  }), [tasks]);

  const statusDot = (status: string) => {
    switch (status) {
      case "scheduled": case "pending": return <DotGreen />;
      case "running": return <DotYellow />;
      case "completed": return <DotGray />;
      case "failed": return <DotRed />;
      case "disabled": case "cancelled": return <DotGray />;
      default: return <DotGray />;
    }
  };

  const statusLabel = (status: string): string => {
    const map: Record<string, string> = {
      pending: t("scheduler.statusPending"),
      scheduled: t("scheduler.statusScheduled"),
      running: t("scheduler.statusRunning"),
      completed: t("scheduler.statusCompleted"),
      failed: t("scheduler.statusFailed"),
      disabled: t("scheduler.statusDisabled"),
      cancelled: t("scheduler.statusCancelled"),
    };
    return map[status] || status;
  };

  const triggerBadgeLabel = (triggerType: string, config: Record<string, any>): string => {
    const mode = detectScheduleMode(triggerType, config);
    const map: Record<string, string> = {
      once: t("scheduler.triggerOnce"),
      interval: t("scheduler.triggerInterval"),
      daily: t("scheduler.triggerDaily"),
      weekly: t("scheduler.triggerWeekly"),
      monthly: t("scheduler.triggerMonthly"),
      custom: t("scheduler.triggerCron"),
    };
    return map[mode] || mode;
  };

  // ── Not running ──
  if (!serviceRunning) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--muted)" }}>
        <IconClock size={48} style={{ opacity: 0.3, marginBottom: 12 }} />
        <p>{t("scheduler.serviceNotRunning")}</p>
      </div>
    );
  }

  const weekdays: string[] = (t("scheduler.weekdays", { returnObjects: true }) as any) || ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];

  const renderTimePicker = () => (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <label className="label" style={{ marginBottom: 0, minWidth: "fit-content" }}>{t("scheduler.timeAt")}</label>
      <select
        style={{ ...selectStyle, width: 72 }}
        value={form.timeHour}
        onChange={e => setForm(f => ({ ...f, timeHour: parseInt(e.target.value) }))}
      >
        {hourOptions.map(h => <option key={h} value={h}>{pad2(h)}</option>)}
      </select>
      <span style={{ fontWeight: 600 }}>:</span>
      <select
        style={{ ...selectStyle, width: 72 }}
        value={form.timeMinute}
        onChange={e => setForm(f => ({ ...f, timeMinute: parseInt(e.target.value) }))}
      >
        {minuteOptions.map(m => <option key={m} value={m}>{pad2(m)}</option>)}
      </select>
    </div>
  );

  // ── Trigger config form fields ──
  const renderTriggerFields = () => {
    switch (form.scheduleMode) {
      case "once": {
        const [datePart = "", timePart = ""] = (form.runAt || "").split("T");
        const curH = timePart ? parseInt(timePart.split(":")[0]) || 0 : new Date().getHours();
        const curM = timePart ? parseInt(timePart.split(":")[1]) || 0 : 0;
        const updateRunAt = (d: string, h: number, m: number) => {
          if (!d) { setForm(f => ({ ...f, runAt: "" })); return; }
          setForm(f => ({ ...f, runAt: `${d}T${pad2(h)}:${pad2(m)}` }));
        };
        return (
          <div className="field">
            <label className="label">{t("scheduler.runAt")}</label>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="date"
                className="input"
                value={datePart}
                min={new Date().toISOString().slice(0, 10)}
                max="2099-12-31"
                onChange={e => updateRunAt(e.target.value, curH, curM)}
                style={{ flex: 1 }}
              />
              <select
                style={{ ...selectStyle, width: 72 }}
                value={curH}
                onChange={e => updateRunAt(datePart || new Date().toISOString().slice(0, 10), parseInt(e.target.value), curM)}
              >
                {hourOptions.map(h => <option key={h} value={h}>{pad2(h)}</option>)}
              </select>
              <span style={{ fontWeight: 600 }}>:</span>
              <select
                style={{ ...selectStyle, width: 72 }}
                value={curM}
                onChange={e => updateRunAt(datePart || new Date().toISOString().slice(0, 10), curH, parseInt(e.target.value))}
              >
                {minuteOptions.map(m => <option key={m} value={m}>{pad2(m)}</option>)}
              </select>
            </div>
          </div>
        );
      }

      case "interval":
        return (
          <div className="field">
            <label className="label">{t("scheduler.intervalValue")}</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                type="number"
                className="input"
                min={1}
                value={form.intervalValue}
                onChange={e => setForm(f => ({ ...f, intervalValue: Math.max(1, parseInt(e.target.value) || 1) }))}
                style={{ flex: 1 }}
              />
              <select
                style={{ ...selectStyle, minWidth: 80 }}
                value={form.intervalUnit}
                onChange={e => setForm(f => ({ ...f, intervalUnit: e.target.value as any }))}
              >
                <option value="seconds">{t("scheduler.intervalSeconds")}</option>
                <option value="minutes">{t("scheduler.intervalMinutes")}</option>
                <option value="hours">{t("scheduler.intervalHours")}</option>
                <option value="days">{t("scheduler.intervalDays")}</option>
              </select>
            </div>
          </div>
        );

      case "daily":
        return (
          <div className="field">
            {renderTimePicker()}
          </div>
        );

      case "weekly":
        return (
          <div className="field" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <label className="label" style={{ marginBottom: 0, minWidth: "fit-content" }}>{t("scheduler.weekday")}</label>
              <div style={{ display: "flex", gap: 4 }}>
                {weekdays.map((wd, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setForm(f => ({ ...f, weekday: i }))}
                    style={{
                      padding: "4px 10px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                      border: form.weekday === i ? "1.5px solid var(--brand, #0ea5e9)" : "1px solid var(--line, #d1d5db)",
                      background: form.weekday === i ? "var(--brand-bg, #e0f2fe)" : "var(--panel, #fff)",
                      color: form.weekday === i ? "var(--brand, #0ea5e9)" : "var(--text)",
                      fontWeight: form.weekday === i ? 600 : 400,
                    }}
                  >
                    {wd}
                  </button>
                ))}
              </div>
            </div>
            {renderTimePicker()}
          </div>
        );

      case "monthly":
        return (
          <div className="field" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <label className="label" style={{ marginBottom: 0, minWidth: "fit-content" }}>{t("scheduler.dayOfMonth")}</label>
              <select
                style={{ ...selectStyle, width: 80 }}
                value={form.dayOfMonth}
                onChange={e => setForm(f => ({ ...f, dayOfMonth: parseInt(e.target.value) }))}
              >
                {Array.from({ length: 31 }, (_, i) => i + 1).map(d => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
            </div>
            {renderTimePicker()}
          </div>
        );

      case "custom":
        return (
          <div className="field">
            <label className="label">{t("scheduler.cronExpression")}</label>
            <input
              type="text"
              className="input"
              placeholder="0 9 * * *"
              value={form.cronExpr}
              onChange={e => setForm(f => ({ ...f, cronExpr: e.target.value }))}
            />
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{t("scheduler.cronHint")}</div>
          </div>
        );

      default:
        return null;
    }
  };

  const tabStyle = (tab: TaskTab): React.CSSProperties => ({
    padding: "6px 16px",
    fontSize: 13,
    fontWeight: activeTab === tab ? 600 : 400,
    color: activeTab === tab ? "var(--brand, #0ea5e9)" : "var(--muted, #6b7280)",
    background: "none",
    border: "none",
    borderBottom: `2px solid ${activeTab === tab ? "var(--brand, #0ea5e9)" : "transparent"}`,
    cursor: "pointer",
    whiteSpace: "nowrap",
    transition: "color 0.15s, border-color 0.15s",
  });

  const countBadge = (count: number) => (
    <span style={{
      display: "inline-block", minWidth: 18, height: 18, lineHeight: "18px",
      borderRadius: 9, fontSize: 11, textAlign: "center",
      background: "var(--bg-elevated, rgba(0,0,0,0.06))", marginLeft: 4,
    }}>
      {count}
    </span>
  );

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>
          <IconCalendar size={20} style={{ verticalAlign: -3, marginRight: 6 }} />
          {t("scheduler.title")}
        </h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btnSmall" onClick={fetchTasks} disabled={loading}>
            <IconRefresh size={14} /> {t("scheduler.refresh")}
          </button>
          <button className="btn" onClick={openCreate}>
            <IconPlus size={14} /> {t("scheduler.addTask")}
          </button>
        </div>
      </div>

      {/* Tabs + Search */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: "1px solid var(--line, #e5e7eb)", marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 0 }}>
          <button style={tabStyle("active")} onClick={() => setActiveTab("active")}>
            {t("scheduler.tabActive")}{countBadge(tabCounts.active)}
          </button>
          <button style={tabStyle("completed")} onClick={() => setActiveTab("completed")}>
            {t("scheduler.tabCompleted")}{countBadge(tabCounts.completed)}
          </button>
          <button style={tabStyle("all")} onClick={() => setActiveTab("all")}>
            {t("scheduler.tabAll")}{countBadge(tabCounts.all)}
          </button>
        </div>
        <div style={{ position: "relative", marginBottom: -1 }}>
          <IconSearch size={14} style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", opacity: 0.4, pointerEvents: "none" }} />
          <input
            type="text"
            className="input"
            placeholder={t("scheduler.searchPlaceholder")}
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            style={{ paddingLeft: 28, fontSize: 12, height: 30, width: 180, borderRadius: 6 }}
          />
        </div>
      </div>

      {/* Message toast */}
      {message && (
        <div
          style={{
            padding: "8px 16px", borderRadius: 8, marginBottom: 12, fontSize: 13,
            background: message.ok ? "var(--ok-bg, #dcfce7)" : "var(--err-bg, #fee2e2)",
            color: message.ok ? "var(--ok-text, #166534)" : "var(--err-text, #991b1b)",
          }}
        >
          {message.ok ? <IconCheck size={14} style={{ verticalAlign: -2, marginRight: 4 }} /> : <IconX size={14} style={{ verticalAlign: -2, marginRight: 4 }} />}
          {message.text}
        </div>
      )}

      {/* Form dialog */}
      {showForm && (
        <div className="card" style={{ marginBottom: 16, border: "1px solid var(--brand, #0ea5e9)", position: "relative" }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 15, fontWeight: 600 }}>
            {editingId ? t("scheduler.editTask") : t("scheduler.addTask")}
          </h3>

          <div className="field">
            <label className="label">{t("scheduler.name")}</label>
            <input
              type="text"
              className="input"
              placeholder={t("scheduler.namePlaceholder")}
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <label className="label">{t("scheduler.taskType")}</label>
              <select
                className="input"
                value={form.task_type}
                onChange={e => setForm(f => ({ ...f, task_type: e.target.value }))}
              >
                <option value="reminder">{t("scheduler.typeReminder")}</option>
                <option value="task">{t("scheduler.typeTask")}</option>
              </select>
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label className="label">{t("scheduler.triggerType")}</label>
              <select
                className="input"
                value={form.scheduleMode}
                onChange={e => setForm(f => ({ ...f, scheduleMode: e.target.value as ScheduleMode }))}
              >
                <option value="once">{t("scheduler.triggerOnce")}</option>
                <option value="daily">{t("scheduler.triggerDaily")}</option>
                <option value="weekly">{t("scheduler.triggerWeekly")}</option>
                <option value="monthly">{t("scheduler.triggerMonthly")}</option>
                <option value="interval">{t("scheduler.triggerInterval")}</option>
                <option value="custom">{t("scheduler.triggerCron")}</option>
              </select>
            </div>
          </div>

          {renderTriggerFields()}

          {form.task_type === "reminder" ? (
            <div className="field">
              <label className="label">{t("scheduler.reminderMessage")}</label>
              <textarea
                className="input"
                rows={3}
                placeholder={t("scheduler.reminderPlaceholder")}
                value={form.reminder_message}
                onChange={e => setForm(f => ({ ...f, reminder_message: e.target.value }))}
                style={{ resize: "vertical", fontFamily: "inherit" }}
              />
            </div>
          ) : (
            <div className="field">
              <label className="label">{t("scheduler.prompt")}</label>
              <textarea
                className="input"
                rows={3}
                placeholder={t("scheduler.promptPlaceholder")}
                value={form.prompt}
                onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
                style={{ resize: "vertical", fontFamily: "inherit" }}
              />
            </div>
          )}

          <div className="field">
            <label className="label">{t("scheduler.channel")}</label>
            {channels.length > 0 ? (
              <select
                className="input"
                value={form.channel_id && form.chat_id ? `${form.channel_id}|${form.chat_id}` : ""}
                onChange={e => {
                  const v = e.target.value;
                  if (!v) {
                    setForm(f => ({ ...f, channel_id: "", chat_id: "" }));
                  } else {
                    const [ch, ...rest] = v.split("|");
                    setForm(f => ({ ...f, channel_id: ch, chat_id: rest.join("|") }));
                  }
                }}
              >
                <option value="">{t("scheduler.channelNone")}</option>
                {channels.map(ch => (
                  <option key={`${ch.channel_id}|${ch.chat_id}`} value={`${ch.channel_id}|${ch.chat_id}`}>
                    {ch.channel_id} / {ch.chat_id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                className="input"
                placeholder={t("scheduler.channelPlaceholder")}
                value={form.channel_id}
                onChange={e => setForm(f => ({ ...f, channel_id: e.target.value }))}
              />
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4, marginBottom: 8 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 13 }}>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
              />
              {t("scheduler.enabled")}
            </label>
          </div>

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btnSmall" onClick={closeForm}>{t("scheduler.cancel")}</button>
            <button className="btn" onClick={saveTask} disabled={busy}>
              {busy ? "..." : (editingId ? t("scheduler.save") : t("scheduler.addTask"))}
            </button>
          </div>
        </div>
      )}

      {/* Task list */}
      {loading && tasks.length === 0 ? (
        <div style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>{t("scheduler.loading")}</div>
      ) : tasks.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 40 }}>
          <IconCalendar size={40} style={{ opacity: 0.2, marginBottom: 8 }} />
          <p style={{ color: "var(--muted)", margin: "8px 0 4px" }}>{t("scheduler.noTasks")}</p>
          <p style={{ color: "var(--muted)", fontSize: 12, margin: 0 }}>{t("scheduler.noTasksHint")}</p>
        </div>
      ) : filteredTasks.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 32 }}>
          <IconSearch size={32} style={{ opacity: 0.2, marginBottom: 8 }} />
          <p style={{ color: "var(--muted)", margin: "8px 0 0", fontSize: 13 }}>{t("scheduler.noMatchingTasks")}</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {filteredTasks.map(task => (
            <div key={task.id} className="card" style={{ padding: "12px 16px" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                  {statusDot(task.status)}
                  <span style={{ fontWeight: 600, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {task.name}
                  </span>
                  {!task.deletable && (
                    <span className="pill" style={{ fontSize: 10, padding: "1px 6px", opacity: 0.7 }}>{t("scheduler.system")}</span>
                  )}
                  <span className="pill" style={{ fontSize: 10, padding: "1px 6px" }}>
                    {task.task_type === "reminder" ? t("scheduler.typeReminder") : t("scheduler.typeTask")}
                  </span>
                  <span className="pill" style={{ fontSize: 10, padding: "1px 6px" }}>
                    {triggerBadgeLabel(task.trigger_type, task.trigger_config)}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                  <button
                    className="btnSmall"
                    style={{ padding: "2px 8px", fontSize: 12 }}
                    onClick={() => toggleTask(task)}
                    title={task.enabled ? t("scheduler.disable") : t("scheduler.enable")}
                  >
                    {task.enabled ? t("scheduler.disable") : t("scheduler.enable")}
                  </button>
                  <button
                    className="btnSmall"
                    style={{ padding: "2px 8px", fontSize: 12 }}
                    onClick={() => triggerTask(task)}
                    title={t("scheduler.trigger")}
                  >
                    <IconPlay size={11} />
                  </button>
                  {task.deletable && (
                    <>
                      <button
                        className="btnSmall"
                        style={{ padding: "2px 8px", fontSize: 12 }}
                        onClick={() => openEdit(task)}
                        title={t("scheduler.editTask")}
                      >
                        <IconEdit size={11} />
                      </button>
                      <button
                        className="btnSmall btnDanger"
                        style={{ padding: "2px 8px", fontSize: 12 }}
                        onClick={() => deleteTask(task)}
                        title={t("scheduler.delete")}
                      >
                        <IconTrash size={11} />
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* Task details */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "4px 16px", fontSize: 12, color: "var(--muted)" }}>
                <div>
                  <span style={{ opacity: 0.7 }}>{t("scheduler.status")}:</span>{" "}
                  <span style={{ color: "var(--text)" }}>{statusLabel(task.status)}</span>
                </div>
                <div>
                  <span style={{ opacity: 0.7 }}>{t("scheduler.triggerType")}:</span>{" "}
                  <span style={{ color: "var(--text)" }}>{triggerDescription(t, task.trigger_type, task.trigger_config)}</span>
                </div>
                <div>
                  <span style={{ opacity: 0.7 }}>{t("scheduler.nextRun")}:</span>{" "}
                  <span style={{ color: "var(--text)" }}>{task.next_run ? formatDateTime(task.next_run) : t("scheduler.notScheduled")}</span>
                </div>
                <div>
                  <span style={{ opacity: 0.7 }}>{t("scheduler.lastRun")}:</span>{" "}
                  <span style={{ color: "var(--text)" }}>{task.last_run ? formatDateTime(task.last_run) : t("scheduler.never")}</span>
                </div>
                <div>
                  <span style={{ opacity: 0.7 }}>{t("scheduler.channel")}:</span>{" "}
                  <span style={{ color: "var(--text)" }}>
                    {task.channel_id ? (task.chat_id ? `${task.channel_id}/${task.chat_id}` : task.channel_id) : "-"}
                  </span>
                </div>
                <div>
                  <span style={{ opacity: 0.7 }}>{t("scheduler.runCount")}:</span>{" "}
                  <span style={{ color: "var(--text)" }}>{task.run_count}</span>
                  {task.fail_count > 0 && (
                    <span style={{ color: "var(--err-text, #991b1b)", marginLeft: 8 }}>
                      {t("scheduler.failCount")}: {task.fail_count}
                    </span>
                  )}
                </div>
              </div>

              {/* Content preview */}
              {(task.reminder_message || task.prompt) && (
                <div style={{
                  marginTop: 8, padding: "6px 10px", borderRadius: 6,
                  background: "var(--bg-elevated, rgba(0,0,0,0.03))", fontSize: 12,
                  color: "var(--text)", whiteSpace: "pre-wrap", wordBreak: "break-word",
                  maxHeight: 60, overflow: "hidden",
                }}>
                  {task.reminder_message || task.prompt}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
