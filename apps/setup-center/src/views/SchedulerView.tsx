import React, { useEffect, useState, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  IconClock,
  DotGreen, DotGray, DotYellow, DotRed, DotBlueProcessing,
  IM_LOGO_MAP,
} from "../icons";
import { safeFetch } from "../providers";
import { IS_WEB, onWsEvent } from "../platform";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Loader2, RefreshCw, Plus, Trash2, Pencil, Power, PowerOff, Zap, Search, CalendarX2, SearchX, Info, AlertTriangle, History } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { toast } from "sonner";

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

type TaskExecution = {
  id: string;
  task_id: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  result: string | null;
  error: string | null;
  duration_seconds: number | null;
};

type IMChannel = {
  channel_id: string;
  chat_id: string;
  user_id: string | null;
  last_active: string;
  chat_name?: string;
  chat_type?: string;
  display_name?: string;
  alias?: string;
  bot_display_name?: string;
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

// API_BASE is derived from the apiBaseUrl prop (empty string = relative path for web mode)

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

const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  dingtalk: "钉钉",
  feishu: "飞书",
  wework: "企业微信",
  wework_ws: "企业微信",
  wework_bot: "企业微信",
  qqbot: "QQ",
  onebot: "QQ(OneBot)",
  onebot_reverse: "QQ(OneBot)",
  wechat: "微信",
  discord: "Discord",
  slack: "Slack",
  whatsapp: "WhatsApp",
  web: "Web",
};

function extractPlatformBase(channelId: string): string {
  return channelId.split(":")[0];
}

function extractPlatformLabel(channelId: string): string {
  const base = extractPlatformBase(channelId);
  return CHANNEL_LABELS[base.toLowerCase()] || base;
}

function extractBotName(channelId: string): string {
  const parts = channelId.split(":");
  return parts.length > 1 ? parts.slice(1).join(":") : "";
}

function shortChatId(chatId: string): string {
  if (!chatId) return "";
  return chatId.length > 16 ? chatId.slice(0, 8) + "…" + chatId.slice(-6) : chatId;
}

function formatChannelLabel(channelId: string, chatId: string, ch?: IMChannel): string {
  if (ch) {
    const typeIcon = ch.chat_type === "group" ? "👥 " : "💬 ";
    const name = ch.alias || ch.chat_name || shortChatId(ch.chat_id);
    return typeIcon + name;
  }
  const platform = extractPlatformLabel(channelId);
  if (!chatId) return platform;
  return `${platform} · ${shortChatId(chatId)}`;
}

function groupChannelsByPlatform(channels: IMChannel[]): Record<string, IMChannel[]> {
  const groups: Record<string, IMChannel[]> = {};
  for (const ch of channels) {
    const platform = extractPlatformLabel(ch.channel_id);
    const botLabel = ch.bot_display_name || extractBotName(ch.channel_id);
    const key = botLabel ? `${platform} · ${botLabel}` : platform;
    if (!groups[key]) groups[key] = [];
    groups[key].push(ch);
  }
  return groups;
}

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


const hourOptions = Array.from({ length: 24 }, (_, i) => i);
const minuteOptions = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
const currentYear = new Date().getFullYear();
const yearOptions = Array.from({ length: 6 }, (_, i) => currentYear + i);
const monthOptions = Array.from({ length: 12 }, (_, i) => i + 1);
const dayOptions = Array.from({ length: 31 }, (_, i) => i + 1);

type TaskTab = "active" | "completed" | "all";

const ACTIVE_STATUSES = new Set(["pending", "scheduled", "running", "missed"]);
const COMPLETED_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function SchedulerView({ serviceRunning, apiBaseUrl = "" }: { serviceRunning: boolean; apiBaseUrl?: string }) {
  const API_BASE = apiBaseUrl;
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<TaskForm>({ ...defaultForm });
  const [busy, setBusy] = useState(false);
  
  const [channels, setChannels] = useState<IMChannel[]>([]);
  const [activeTab, setActiveTab] = useState<TaskTab>("active");
  const [searchQuery, setSearchQuery] = useState("");
  const [confirmDialog, setConfirmDialog] = useState<{ message: string; onConfirm: () => void } | null>(null);
  const [expandedHistory, setExpandedHistory] = useState<Record<string, TaskExecution[]>>({});
  const expandedHistoryRef = React.useRef(expandedHistory);
  expandedHistoryRef.current = expandedHistory;

  const toggleHistory = useCallback(async (taskId: string) => {
    if (expandedHistoryRef.current[taskId]) {
      setExpandedHistory(prev => { const n = { ...prev }; delete n[taskId]; return n; });
      return;
    }
    try {
      const res = await safeFetch(`${API_BASE}/api/scheduler/tasks/${taskId}/executions?limit=10`);
      const data = await res.json();
      setExpandedHistory(prev => ({ ...prev, [taskId]: data.executions || [] }));
    } catch {
      setExpandedHistory(prev => ({ ...prev, [taskId]: [] }));
    }
  }, [API_BASE]);

  const fetchTasks = useCallback(async (showLoading = true) => {
    if (!serviceRunning) return;
    if (showLoading) setLoading(true);
    try {
      const res = await safeFetch(`${API_BASE}/api/scheduler/tasks`);
      const data = await res.json();
      setTasks(data.tasks || []);
    } catch {
      if (showLoading) toast.error(t("scheduler.loadError"));
    }
    if (showLoading) setLoading(false);
  }, [serviceRunning, t]);

  const fetchChannels = useCallback(async () => {
    if (!serviceRunning) return;
    try {
      const res = await safeFetch(`${API_BASE}/api/scheduler/channels`);
      const data = await res.json();
      setChannels(data.channels || []);
    } catch {
      toast.error(t("scheduler.loadChannelError"));
    }
  }, [serviceRunning, t]);

  useEffect(() => { fetchTasks(); fetchChannels(); }, [fetchTasks, fetchChannels]);

  useEffect(() => {
    if (!serviceRunning) return;
    const interval = setInterval(() => fetchTasks(false), IS_WEB ? 60_000 : 10_000);
    return () => clearInterval(interval);
  }, [serviceRunning, fetchTasks]);

  useEffect(() => {
    if (!IS_WEB) return;
    return onWsEvent((event) => {
      if (event === "scheduler:task_update") fetchTasks(false);
    });
  }, [fetchTasks]);

  const showMsg = (text: string, ok: boolean) => {
    if (ok) toast.success(text);
    else toast.error(text);
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
    if (!form.name.trim()) { showMsg(t("scheduler.nameRequired"), false); return; }

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
        res = await safeFetch(`${API_BASE}/api/scheduler/tasks/${editingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        res = await safeFetch(`${API_BASE}/api/scheduler/tasks`, {
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

  const doDeleteTask = useCallback(async (taskId: string) => {
    setBusy(true);
    try {
      const res = await safeFetch(`${API_BASE}/api/scheduler/tasks/${taskId}`, { method: "DELETE" });
      const data = await res.json();
      if (data.error) {
        showMsg(data.error, false);
      } else {
        showMsg(t("scheduler.deleteSuccess"), true);
        await fetchTasks();
      }
    } catch (e) { showMsg(String(e), false); }
    setBusy(false);
  }, [API_BASE, t, fetchTasks]);

  const deleteTask = (task: ScheduledTask) => {
    setConfirmDialog({
      message: t("scheduler.confirmDelete", { name: task.name }),
      onConfirm: () => doDeleteTask(task.id),
    });
  };

  const toggleTask = async (task: ScheduledTask) => {
    try {
      const res = await safeFetch(`${API_BASE}/api/scheduler/tasks/${task.id}/toggle`, { method: "POST" });
      const data = await res.json();
      if (data.error) {
        showMsg(data.error, false);
      } else {
        showMsg(task.enabled ? t("scheduler.disableSuccess") : t("scheduler.enableSuccess"), true);
        await fetchTasks();
      }
    } catch (e) { showMsg(String(e), false); }
  };

  const triggerTask = async (task: ScheduledTask) => {
    try {
      const res = await safeFetch(`${API_BASE}/api/scheduler/tasks/${task.id}/trigger`, { method: "POST" });
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

  const statusDotEl = (status: string) => {
    switch (status) {
      case "scheduled": case "pending": return <DotGreen />;
      case "running": return <DotBlueProcessing />;
      case "completed": return <DotGray />;
      case "failed": return <DotRed />;
      case "missed": return <DotYellow />;
      case "disabled": case "cancelled": return <DotGray />;
      default: return <DotGray />;
    }
  };

  const statusDotTip = (status: string): string => {
    const map: Record<string, string> = {
      pending: t("scheduler.statusPending"), scheduled: t("scheduler.statusScheduled"), running: t("scheduler.statusRunning"),
      completed: t("scheduler.statusCompleted"), failed: t("scheduler.statusFailed"), disabled: t("scheduler.statusDisabled"), cancelled: t("scheduler.statusCancelled"),
      missed: t("scheduler.statusMissed"),
    };
    return map[status] || status;
  };

  const statusDot = (status: string) => (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span style={{ cursor: "pointer", display: "inline-flex" }}>{statusDotEl(status)}</span>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs px-2 py-1">
          {statusDotTip(status)}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );

  const statusLabel = (status: string): string => {
    const map: Record<string, string> = {
      pending: t("scheduler.statusPending"),
      scheduled: t("scheduler.statusScheduled"),
      running: t("scheduler.statusRunning"),
      completed: t("scheduler.statusCompleted"),
      failed: t("scheduler.statusFailed"),
      disabled: t("scheduler.statusDisabled"),
      cancelled: t("scheduler.statusCancelled"),
      missed: t("scheduler.statusMissed"),
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
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconClock size={48} />
        <div className="mt-3 font-semibold">{t("scheduler.title")}</div>
        <div className="mt-1 text-xs opacity-50">{t("scheduler.serviceNotRunning")}</div>
      </div>
    );
  }

  const weekdays: string[] = (t("scheduler.weekdays", { returnObjects: true }) as any) || ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];

  const renderTimePicker = () => (
    <div className="flex items-center gap-2">
      <Label className="shrink-0 mb-0">{t("scheduler.timeAt")}</Label>
      <Select value={String(form.timeHour)} onValueChange={v => setForm(f => ({ ...f, timeHour: parseInt(v) }))}>
        <SelectTrigger className="w-[72px]"><SelectValue /></SelectTrigger>
        <SelectContent>{hourOptions.map(h => <SelectItem key={h} value={String(h)}>{pad2(h)}</SelectItem>)}</SelectContent>
      </Select>
      <span className="font-semibold">:</span>
      <Select value={String(form.timeMinute)} onValueChange={v => setForm(f => ({ ...f, timeMinute: parseInt(v) }))}>
        <SelectTrigger className="w-[72px]"><SelectValue /></SelectTrigger>
        <SelectContent>{minuteOptions.map(m => <SelectItem key={m} value={String(m)}>{pad2(m)}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  );

  // ── Trigger config form fields ──
  const renderTriggerFields = () => {
    switch (form.scheduleMode) {
      case "once": {
        const [datePart = "", timePart = ""] = (form.runAt || "").split("T");
        const [yStr = "", moStr = "", dStr = ""] = datePart.split("-");
        const curY = yStr ? parseInt(yStr) : 0;
        const curMo = moStr ? parseInt(moStr) : 0;
        const curD = dStr ? parseInt(dStr) : 0;
        const curH = timePart ? parseInt(timePart.split(":")[0]) || 0 : new Date().getHours();
        const curM = timePart ? parseInt(timePart.split(":")[1]) || 0 : 0;
        const now = new Date();
        const defY = now.getFullYear();
        const defMo = now.getMonth() + 1;
        const defD = now.getDate();
        const updateRunAt = (y: number, mo: number, d: number, h: number, m: number) => {
          setForm(f => ({ ...f, runAt: `${y}-${pad2(mo)}-${pad2(d)}T${pad2(h)}:${pad2(m)}` }));
        };
        return (
          <div className="space-y-1.5 mb-3">
            <Label>{t("scheduler.runAt")}</Label>
            <div className="flex items-center gap-1.5">
              <Select value={curY ? String(curY) : ""} onValueChange={v => updateRunAt(parseInt(v), curMo || defMo, curD || defD, curH, curM)}>
                <SelectTrigger className="w-[82px]"><SelectValue placeholder={t("scheduler.unitYear")} /></SelectTrigger>
                <SelectContent>{yearOptions.map(y => <SelectItem key={y} value={String(y)}>{y}</SelectItem>)}</SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground">{t("scheduler.unitYear")}</span>
              <Select value={curMo ? String(curMo) : ""} onValueChange={v => updateRunAt(curY || defY, parseInt(v), curD || defD, curH, curM)}>
                <SelectTrigger className="w-[68px]"><SelectValue placeholder={t("scheduler.unitMonth")} /></SelectTrigger>
                <SelectContent>{monthOptions.map(m => <SelectItem key={m} value={String(m)}>{pad2(m)}</SelectItem>)}</SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground">{t("scheduler.unitMonth")}</span>
              <Select value={curD ? String(curD) : ""} onValueChange={v => updateRunAt(curY || defY, curMo || defMo, parseInt(v), curH, curM)}>
                <SelectTrigger className="w-[68px]"><SelectValue placeholder={t("scheduler.unitDay")} /></SelectTrigger>
                <SelectContent>{dayOptions.map(d => <SelectItem key={d} value={String(d)}>{pad2(d)}</SelectItem>)}</SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground mr-1">{t("scheduler.unitDay")}</span>
              <Select value={String(curH)} onValueChange={v => updateRunAt(curY || defY, curMo || defMo, curD || defD, parseInt(v), curM)}>
                <SelectTrigger className="w-[68px]"><SelectValue /></SelectTrigger>
                <SelectContent>{hourOptions.map(h => <SelectItem key={h} value={String(h)}>{pad2(h)}</SelectItem>)}</SelectContent>
              </Select>
              <span className="font-semibold">:</span>
              <Select value={String(curM)} onValueChange={v => updateRunAt(curY || defY, curMo || defMo, curD || defD, curH, parseInt(v))}>
                <SelectTrigger className="w-[68px]"><SelectValue /></SelectTrigger>
                <SelectContent>{minuteOptions.map(m => <SelectItem key={m} value={String(m)}>{pad2(m)}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>
        );
      }

      case "interval":
        return (
          <div className="space-y-1.5 mb-3">
            <Label>{t("scheduler.intervalValue")}</Label>
            <div className="flex gap-2">
              <Input
                type="number"
                min={1}
                value={form.intervalValue}
                onChange={e => setForm(f => ({ ...f, intervalValue: Math.max(1, parseInt(e.target.value) || 1) }))}
                className="flex-1"
              />
              <Select value={form.intervalUnit} onValueChange={v => setForm(f => ({ ...f, intervalUnit: v as any }))}>
                <SelectTrigger className="w-[100px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="seconds">{t("scheduler.intervalSeconds")}</SelectItem>
                  <SelectItem value="minutes">{t("scheduler.intervalMinutes")}</SelectItem>
                  <SelectItem value="hours">{t("scheduler.intervalHours")}</SelectItem>
                  <SelectItem value="days">{t("scheduler.intervalDays")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        );

      case "daily":
        return (
          <div className="space-y-1.5 mb-3">
            {renderTimePicker()}
          </div>
        );

      case "weekly":
        return (
          <div className="space-y-3 mb-3">
            <div className="flex items-center gap-2">
              <Label className="shrink-0 mb-0">{t("scheduler.weekday")}</Label>
              <ToggleGroup type="single" value={String(form.weekday)} onValueChange={v => { if (v) setForm(f => ({ ...f, weekday: parseInt(v) })); }}>
                {weekdays.map((wd, i) => (
                  <ToggleGroupItem key={i} value={String(i)} className="h-7 px-2.5 text-xs">
                    {wd}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
            {renderTimePicker()}
          </div>
        );

      case "monthly":
        return (
          <div className="space-y-3 mb-3">
            <div className="flex items-center gap-2">
              <Label className="shrink-0 mb-0">{t("scheduler.dayOfMonth")}</Label>
              <Select value={String(form.dayOfMonth)} onValueChange={v => setForm(f => ({ ...f, dayOfMonth: parseInt(v) }))}>
                <SelectTrigger className="w-[80px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Array.from({ length: 31 }, (_, i) => i + 1).map(d => (
                    <SelectItem key={d} value={String(d)}>{d}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {renderTimePicker()}
          </div>
        );

      case "custom":
        return (
          <div className="space-y-1.5 mb-3">
            <Label>{t("scheduler.cronExpression")}</Label>
            <Input
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

  

  const countBadge = (count: number, tab: TaskTab) => (
    <Badge
      variant="secondary"
      className={cn(
        "ml-1.5 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full",
        activeTab === tab
          ? "bg-white/25 text-primary-foreground"
          : "bg-foreground/10 text-foreground/60",
      )}
    >
      {count}
    </Badge>
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 py-5">
      {/* Hero Card: Title & Legend */}
      <Card className="gap-0 overflow-hidden border-border/80 bg-gradient-to-br from-primary/5 via-background to-background py-0 shadow-sm">
        <CardHeader className="px-6 py-5 pb-4">
          <CardTitle className="flex items-center gap-2 text-xl font-bold">
            <IconClock size={24} className="text-primary" />
            {t("scheduler.title")}
          </CardTitle>
          <CardDescription>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mt-2 text-xs">
              <span className="flex items-center gap-1.5"><DotGreen size={8} /> {t("scheduler.statusScheduled")}</span>
              <span className="flex items-center gap-1.5"><DotBlueProcessing size={8} /> {t("scheduler.statusRunning")}</span>
              <span className="flex items-center gap-1.5"><DotYellow size={8} /> {t("scheduler.statusMissed")}</span>
              <span className="flex items-center gap-1.5"><DotRed size={8} /> {t("scheduler.statusFailed")}</span>
              <span className="flex items-center gap-1.5"><DotGray size={8} /> {t("scheduler.statusCompleted")} / {t("scheduler.statusDisabled")}</span>
            </div>
          </CardDescription>
        </CardHeader>
      </Card>

      {/* Header: Tabs + Search + Actions */}
      <Card className="gap-0 border-border/80 py-0 shadow-sm">
        <CardContent className="p-4">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <ToggleGroup
              type="single"
              value={activeTab}
              onValueChange={(v) => { if (v) setActiveTab(v as TaskTab); }}
              variant="outline"
              className="justify-start"
            >
              <ToggleGroupItem value="active" className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                {t("scheduler.tabActive")} {countBadge(tabCounts.active, "active")}
              </ToggleGroupItem>
              <ToggleGroupItem value="completed" className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                {t("scheduler.tabCompleted")} {countBadge(tabCounts.completed, "completed")}
              </ToggleGroupItem>
              <ToggleGroupItem value="all" className="text-sm data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                {t("scheduler.tabAll")} {countBadge(tabCounts.all, "all")}
              </ToggleGroupItem>
            </ToggleGroup>
            <div className="flex flex-wrap items-center gap-3">
              <div className="relative flex-1 min-w-[200px]">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                <Input
                  placeholder={t("scheduler.searchPlaceholder")}
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  className="pl-9 h-9 text-sm"
                />
              </div>
              <Button variant="outline" size="sm" className="h-9" onClick={() => fetchTasks()} disabled={loading}>
                {loading ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={14} />}
                <span className="hidden sm:inline ml-1.5">{t("scheduler.refresh")}</span>
              </Button>
              <Button size="sm" className="h-9 bg-gradient-to-br from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white border-0 shadow-md shadow-indigo-500/20" onClick={openCreate}>
                <Plus size={14} className="mr-1.5" /> {t("scheduler.addTask")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Form dialog */}
      {showForm && (
        <Card className="gap-0 border-primary py-0 shadow-sm relative overflow-hidden">
          <div className="absolute top-0 left-0 w-1 h-full bg-primary" />
          <CardHeader className="px-6 py-4 pb-3">
            <CardTitle className="text-lg">
              {editingId ? t("scheduler.editTask") : t("scheduler.addTask")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>{t("scheduler.name")}</Label>
              <Input
                placeholder={t("scheduler.namePlaceholder")}
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>{t("scheduler.taskType")}</Label>
                <Select value={form.task_type} onValueChange={v => setForm(f => ({ ...f, task_type: v }))}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="reminder">{t("scheduler.typeReminder")}</SelectItem>
                    <SelectItem value="task">{t("scheduler.typeTask")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>{t("scheduler.triggerType")}</Label>
                <Select value={form.scheduleMode} onValueChange={v => setForm(f => ({ ...f, scheduleMode: v as ScheduleMode }))}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="once">{t("scheduler.triggerOnce")}</SelectItem>
                    <SelectItem value="daily">{t("scheduler.triggerDaily")}</SelectItem>
                    <SelectItem value="weekly">{t("scheduler.triggerWeekly")}</SelectItem>
                    <SelectItem value="monthly">{t("scheduler.triggerMonthly")}</SelectItem>
                    <SelectItem value="interval">{t("scheduler.triggerInterval")}</SelectItem>
                    <SelectItem value="custom">{t("scheduler.triggerCron")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {renderTriggerFields()}

            {form.task_type === "reminder" ? (
              <div className="space-y-1.5">
                <Label>{t("scheduler.reminderMessage")}</Label>
                <Textarea
                  rows={3}
                  placeholder={t("scheduler.reminderPlaceholder")}
                  value={form.reminder_message}
                  onChange={e => setForm(f => ({ ...f, reminder_message: e.target.value }))}
                  className="resize-y"
                />
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label>{t("scheduler.prompt")}</Label>
                <Textarea
                  rows={3}
                  placeholder={t("scheduler.promptPlaceholder")}
                  value={form.prompt}
                  onChange={e => setForm(f => ({ ...f, prompt: e.target.value }))}
                  className="resize-y"
                />
              </div>
            )}

            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <Label className="mb-0">{t("scheduler.channel")}</Label>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Info size={14} className="text-muted-foreground cursor-help shrink-0" />
                    </TooltipTrigger>
                    <TooltipContent side="top" className="max-w-[260px]">
                      {t("scheduler.channelTooltip")}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              {(() => {
                const currentKey = form.channel_id && form.chat_id ? `${form.channel_id}|${form.chat_id}` : "";
                const knownKeys = new Set(channels.map(ch => `${ch.channel_id}|${ch.chat_id}`));
                const isStale = !!currentKey && !knownKeys.has(currentKey);

                return (
                  <>
                    <Select
                      value={currentKey || "__none__"}
                      onValueChange={v => {
                        if (v === "__none__") {
                          setForm(f => ({ ...f, channel_id: "", chat_id: "" }));
                        } else {
                          const [ch, ...rest] = v.split("|");
                          setForm(f => ({ ...f, channel_id: ch, chat_id: rest.join("|") }));
                        }
                      }}
                    >
                      <SelectTrigger className={cn("w-full", isStale && "border-amber-400 dark:border-amber-600")}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent position="popper" className="max-h-[300px]">
                        <SelectItem value="__none__">
                          {t("scheduler.channelNone")}
                        </SelectItem>
                        {(() => {
                          const grouped = groupChannelsByPlatform(channels);
                          return Object.entries(grouped).map(([platform, items], gi) => {
                            const base = items[0] ? extractPlatformBase(items[0].channel_id).toLowerCase() : "";
                            const LogoIcon = IM_LOGO_MAP[base];
                            return (
                              <SelectGroup key={platform}>
                                {gi > 0 && <div className="mx-2 my-1 h-px bg-border" />}
                                <SelectLabel className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground px-2">
                                  {LogoIcon && <LogoIcon size={14} />}
                                  {platform}
                                </SelectLabel>
                                {items.map(ch => {
                                  const itemBase = extractPlatformBase(ch.channel_id).toLowerCase();
                                  const ItemLogo = IM_LOGO_MAP[itemBase];
                                  const noPairedChat = !ch.chat_id;
                                  const label = ch.alias || ch.chat_name || shortChatId(ch.chat_id)
                                    || ch.bot_display_name || t("scheduler.channelPending");
                                  return (
                                    <SelectItem key={`${ch.channel_id}|${ch.chat_id}`} value={`${ch.channel_id}|${ch.chat_id}`}>
                                      <span className="flex items-center gap-1.5">
                                        {ItemLogo && <ItemLogo size={14} />}
                                        {noPairedChat ? "🔗" : ch.chat_type === "group" ? "👥" : "💬"}
                                        <span className={noPairedChat ? "text-muted-foreground" : ""}>{label}</span>
                                      </span>
                                    </SelectItem>
                                  );
                                })}
                              </SelectGroup>
                            );
                          });
                        })()}
                        {isStale && (
                          <SelectItem value={currentKey}>
                            ⚠ {formatChannelLabel(form.channel_id, form.chat_id)}
                          </SelectItem>
                        )}
                      </SelectContent>
                    </Select>
                    {isStale && (
                      <p className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400 mt-1">
                        <AlertTriangle size={12} className="shrink-0" />
                        {t("scheduler.channelStale")}
                      </p>
                    )}
                    {channels.length === 0 && !currentKey && (
                      <p className="text-xs text-muted-foreground mt-1">
                        {t("scheduler.channelEmpty")}
                      </p>
                    )}
                  </>
                );
              })()}
            </div>

            <div className="flex items-center gap-2 pt-2">
              <Label className="flex items-center gap-2 cursor-pointer text-sm font-normal">
                <Checkbox checked={form.enabled} onCheckedChange={(v) => setForm(f => ({ ...f, enabled: !!v }))} />
                {t("scheduler.enabled")}
              </Label>
            </div>
          </CardContent>
          <CardFooter className="flex gap-3 justify-end bg-muted/30 py-4 border-t border-border/50">
            <Button variant="outline" size="sm" onClick={closeForm}>{t("scheduler.cancel")}</Button>
            <Button size="sm" onClick={saveTask} disabled={busy}>
              {busy && <Loader2 className="animate-spin mr-1.5" size={14} />}
              {editingId ? t("scheduler.save") : t("scheduler.addTask")}
            </Button>
          </CardFooter>
        </Card>
      )}

      {/* Task list */}
      <div className="flex flex-col gap-4">
        {loading && tasks.length === 0 ? (
          <Card className="border-dashed border-border/80 shadow-sm">
            <CardContent className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Loader2 className="animate-spin mb-3" size={28} />
              <p className="text-sm">{t("scheduler.loading")}</p>
            </CardContent>
          </Card>
        ) : tasks.length === 0 ? (
          <Card className="border-dashed border-border/80 shadow-sm">
            <CardContent className="flex flex-col items-center justify-center py-16">
              <CalendarX2 size={40} className="text-muted-foreground/30 mb-3" />
              <p className="text-sm text-muted-foreground">{t("scheduler.noTasks")}</p>
              <p className="text-xs text-muted-foreground/60 mt-1">{t("scheduler.noTasksHint")}</p>
            </CardContent>
          </Card>
        ) : filteredTasks.length === 0 ? (
          <Card className="border-dashed border-border/80 shadow-sm">
            <CardContent className="flex flex-col items-center justify-center py-14">
              <SearchX size={32} className="text-muted-foreground/30 mb-3" />
              <p className="text-sm text-muted-foreground">{t("scheduler.noMatchingTasks")}</p>
            </CardContent>
          </Card>
        ) : (
          <div className="flex flex-col gap-2.5">
            {filteredTasks.map(task => (
              <Card key={task.id} className="gap-0 overflow-hidden border-border/80 py-0 shadow-sm transition-all hover:shadow-md">
                <CardContent className="px-4 py-3.5">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-2.5">
                    <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                      {statusDot(task.status)}
                      <span className="font-semibold text-sm whitespace-nowrap overflow-hidden text-ellipsis max-w-[200px] sm:max-w-[300px]">
                        {task.name}
                      </span>
                      {!task.deletable && (
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4.5 bg-muted/50">{t("scheduler.system")}</Badge>
                      )}
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4.5">
                        {task.task_type === "reminder" ? t("scheduler.typeReminder") : t("scheduler.typeTask")}
                      </Badge>
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4.5">
                        {triggerBadgeLabel(task.trigger_type, task.trigger_config)}
                      </Badge>
                    </div>
                    
                    <div className="flex items-center gap-1.5 shrink-0 ml-auto sm:ml-0">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => toggleTask(task)}
                        title={task.enabled ? t("scheduler.disable") : t("scheduler.enable")}
                        className={cn(
                          "h-7.5 text-xs px-2.5 mr-1",
                          task.enabled
                            ? "bg-amber-50 text-amber-600 border-amber-200 hover:bg-amber-100 hover:text-amber-700 dark:bg-amber-950 dark:text-amber-400 dark:border-amber-800 dark:hover:bg-amber-900"
                            : "bg-emerald-50 text-emerald-600 border-emerald-200 hover:bg-emerald-100 hover:text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400 dark:border-emerald-800 dark:hover:bg-emerald-900",
                        )}
                      >
                        {task.enabled ? <PowerOff size={12} className="mr-1.5" /> : <Power size={12} className="mr-1.5" />}
                        {task.enabled ? t("scheduler.disable") : t("scheduler.enable")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => triggerTask(task)}
                        title={t("scheduler.trigger")}
                        className="h-7.5 w-7.5 text-muted-foreground hover:text-primary hover:bg-primary/10"
                      >
                        <Zap size={14} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => toggleHistory(task.id)}
                        title={expandedHistory[task.id] ? t("scheduler.hideHistory") : t("scheduler.viewHistory")}
                        className={cn("h-7.5 w-7.5 text-muted-foreground hover:text-foreground hover:bg-muted", expandedHistory[task.id] && "text-primary bg-primary/5")}
                      >
                        <History size={14} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => openEdit(task)}
                        title={t("scheduler.editTask")}
                        className="h-7.5 w-7.5 text-muted-foreground hover:text-foreground hover:bg-muted"
                      >
                        <Pencil size={14} />
                      </Button>
                      {/* Fixed width container for delete button to maintain alignment */}
                      <div className="w-7.5 flex justify-center">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => task.deletable && deleteTask(task)}
                          title={t("scheduler.delete")}
                          className={cn(
                            "h-7.5 w-7.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10",
                            !task.deletable && "invisible pointer-events-none"
                          )}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </div>
                  </div>

                  {/* Task details */}
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-y-2.5 gap-x-3 text-xs text-muted-foreground bg-muted/30 px-3 py-2.5 rounded-md border border-border/50">
                    <div className="flex flex-col gap-0.5">
                      <span className="opacity-70">{t("scheduler.status")}:</span>
                      <span className="text-foreground font-medium">{statusLabel(task.status)}</span>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="opacity-70">{t("scheduler.triggerType")}:</span>
                      <span className="text-foreground font-medium">{triggerDescription(t, task.trigger_type, task.trigger_config)}</span>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="opacity-70">{t("scheduler.nextRun")}:</span>
                      <span className="text-foreground font-medium">{task.next_run ? formatDateTime(task.next_run) : t("scheduler.notScheduled")}</span>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="opacity-70">{t("scheduler.lastRun")}:</span>
                      <span className="text-foreground font-medium">{task.last_run ? formatDateTime(task.last_run) : t("scheduler.never")}</span>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="opacity-70">{t("scheduler.channel")}:</span>
                      <span className="text-foreground font-medium truncate" title={task.channel_id ? formatChannelLabel(task.channel_id, task.chat_id || "") : t("scheduler.channelNone")}>
                        {task.channel_id
                          ? formatChannelLabel(task.channel_id, task.chat_id || "")
                          : t("scheduler.channelNone")}
                      </span>
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="opacity-70">{t("scheduler.runCount")}:</span>
                      <div className="flex items-center text-foreground font-medium">
                        {task.run_count}
                        {task.fail_count > 0 && (
                          <TooltipProvider delayDuration={200}>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="text-destructive ml-2 cursor-pointer flex items-center gap-1 bg-destructive/10 px-1.5 py-0.5 rounded text-[10px] leading-none">
                                  <AlertTriangle size={10} />
                                  {task.fail_count}
                                </span>
                              </TooltipTrigger>
                              <TooltipContent side="top" className="text-xs px-2 py-1 max-w-[240px]">
                                {task.fail_count >= 3
                                  ? t("scheduler.failWarning", { count: task.fail_count })
                                  : `${t("scheduler.failCount")}: ${task.fail_count}`}
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Auto-disabled warning */}
                  {task.status === "failed" && !task.enabled && (
                    <div className="flex items-center gap-2 mt-2.5 px-3 py-1.5 rounded-md bg-amber-500/10 border border-amber-500/20 text-xs text-amber-600 dark:text-amber-400">
                      <AlertTriangle size={14} className="shrink-0" />
                      <span>{t("scheduler.autoDisabledWarning")}</span>
                    </div>
                  )}

                  {/* Content preview */}
                  {(task.reminder_message || task.prompt) && (
                    <div className="mt-2.5 px-3 py-1.5 rounded-md bg-muted/30 border border-border/50 text-xs leading-5 text-foreground whitespace-pre-wrap break-words max-h-20 overflow-y-auto custom-scrollbar">
                      {task.reminder_message || task.prompt}
                    </div>
                  )}

                  {/* Execution history */}
                  {expandedHistory[task.id] && (
                    <div className="mt-2.5 px-3 py-2 rounded-md bg-muted/40 border border-border/60 text-xs text-muted-foreground animate-in slide-in-from-top-2 duration-200">
                      <div className="font-medium mb-1.5 text-foreground flex items-center gap-1.5">
                        <History size={12} />
                        {t("scheduler.executionHistory")}
                      </div>
                      {expandedHistory[task.id].length === 0 ? (
                        <div className="py-1.5 text-center opacity-70">{t("scheduler.noExecutions")}</div>
                      ) : (
                        <div className="flex flex-col gap-1.5">
                          {expandedHistory[task.id].map((exec) => (
                            <div key={exec.id} className="flex items-center gap-2 px-2 py-1.5 rounded bg-background border border-border/40 shadow-sm">
                              <Badge variant={exec.status === "success" ? "secondary" : "destructive"} className="text-[10px] px-1.5 py-0 h-4 min-w-[48px] justify-center leading-none">
                                {exec.status === "success" ? t("scheduler.executionSuccess") : t("scheduler.executionFailed")}
                              </Badge>
                              <span className="flex-1 text-foreground font-mono text-[11px]">
                                {exec.started_at ? formatDateTime(exec.started_at) : "-"}
                              </span>
                              {exec.duration_seconds != null && (
                                <span className="opacity-70 text-[11px]">{t("scheduler.duration")}: {exec.duration_seconds.toFixed(1)}s</span>
                              )}
                              {exec.error && (
                                <TooltipProvider delayDuration={200}>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <span className="text-destructive cursor-pointer max-w-[120px] sm:max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap text-[11px]">
                                        {exec.error}
                                      </span>
                                    </TooltipTrigger>
                                    <TooltipContent side="top" className="text-xs px-2 py-1 max-w-[320px]">
                                      {exec.error}
                                    </TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog dialog={confirmDialog} onClose={() => setConfirmDialog(null)} />
    </div>
  );
}
