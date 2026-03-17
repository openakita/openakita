import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { IconBot, IconRefresh, IconPlus, IconEdit, IconTrash, IconDownload, IconUpload } from "../icons";
import { safeFetch } from "../providers";
import { logger, saveFileDialog, IS_TAURI } from "../platform";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type AgentProfile = {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  type: string;
  skills: string[];
  skills_mode: string;
  custom_prompt: string;
  preferred_endpoint?: string | null;
  category?: string;
  hidden?: boolean;
  user_customized?: boolean;
};

type SkillItem = {
  skillId: string;
  name: string;
  enabled: boolean;
  name_i18n?: Record<string, string> | null;
};

type ModelInfo = {
  name: string;
  provider: string;
  model: string;
  status: string;
  has_api_key: boolean;
};

const EMPTY_PROFILE: AgentProfile = {
  id: "",
  name: "",
  description: "",
  icon: "🤖",
  color: "#6b7280",
  type: "custom",
  skills: [],
  skills_mode: "all",
  custom_prompt: "",
  preferred_endpoint: null,
  category: "",
  hidden: false,
};

type CategoryInfo = {
  id: string;
  label: string;
  color: string;
  builtin: boolean;
  agent_count: number;
};

// SVG icon paths (viewBox 0 0 24 24, stroke-based for consistency)
const SVG_ICONS: Record<string, { path: string; label: string }> = {
  terminal:   { label: "终端",   path: "M4 17l6-5-6-5M12 19h8" },
  code:       { label: "代码",   path: "M16 18l6-6-6-6M8 6l-6 6 6 6" },
  globe:      { label: "全球",   path: "M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10A15.3 15.3 0 0112 2z" },
  shield:     { label: "安全",   path: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" },
  database:   { label: "数据库", path: "M12 2C6.48 2 2 3.79 2 6v12c0 2.21 4.48 4 10 4s10-1.79 10-4V6c0-2.21-4.48-4-10-4zM2 12c0 2.21 4.48 4 10 4s10-1.79 10-4M2 6c0 2.21 4.48 4 10 4s10-1.79 10-4" },
  cpu:        { label: "芯片",   path: "M6 6h12v12H6zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" },
  cloud:      { label: "云",     path: "M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z" },
  lock:       { label: "锁",     path: "M19 11H5a2 2 0 00-2 2v7a2 2 0 002 2h14a2 2 0 002-2v-7a2 2 0 00-2-2zM7 11V7a5 5 0 0110 0v4" },
  zap:        { label: "闪电",   path: "M13 2L3 14h9l-1 8 10-12h-9l1-8z" },
  eye:        { label: "监控",   path: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 100 6 3 3 0 000-6z" },
  message:    { label: "对话",   path: "M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" },
  mail:       { label: "邮件",   path: "M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6" },
  chart:      { label: "图表",   path: "M18 20V10M12 20V4M6 20v-6" },
  network:    { label: "网络",   path: "M5.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM18.5 5.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM12 24a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM5.5 5.5L12 19M18.5 5.5L12 19" },
  target:     { label: "靶心",   path: "M12 2a10 10 0 100 20 10 10 0 000-20zM12 6a6 6 0 100 12 6 6 0 000-12zM12 10a2 2 0 100 4 2 2 0 000-4z" },
  compass:    { label: "指南",   path: "M12 2a10 10 0 100 20 10 10 0 000-20zM16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36z" },
  layers:     { label: "层级",   path: "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" },
  workflow:   { label: "流程",   path: "M6 3a3 3 0 100 6 3 3 0 000-6zM18 15a3 3 0 100 6 3 3 0 000-6zM8.59 13.51l6.83 3.98M6 9v4M18 9v6" },
  flask:      { label: "实验",   path: "M9 3h6M10 3v6.5l-5 8.5h14l-5-8.5V3" },
  pen:        { label: "创作",   path: "M12 20h9M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4L16.5 3.5z" },
  mic:        { label: "语音",   path: "M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8" },
  bot:        { label: "机器人", path: "M12 2a2 2 0 012 2v1h3a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2h3V4a2 2 0 012-2zM9 13h0M15 13h0M9 17h6" },
  puzzle:     { label: "拼图",   path: "M19.439 12.956l-1.5 0a2 2 0 010-4l1.5 0a.5.5 0 00.5-.5l0-2.5a2 2 0 00-2-2l-2.5 0a.5.5 0 01-.5-.5l0-1.5a2 2 0 00-4 0l0 1.5a.5.5 0 01-.5.5L7.939 3.956a2 2 0 00-2 2l0 2.5a.5.5 0 00.5.5l1.5 0a2 2 0 010 4l-1.5 0a.5.5 0 00-.5.5l0 2.5a2 2 0 002 2l2.5 0a.5.5 0 01.5.5l0 1.5a2 2 0 004 0l0-1.5a.5.5 0 01.5-.5l2.5 0a2 2 0 002-2l0-2.5a.5.5 0 00-.5-.5z" },
  heart:      { label: "爱心",   path: "M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 000-7.78z" },
};
const SVG_ICON_KEYS = Object.keys(SVG_ICONS);

function SvgIcon({ name, size = 20, color = "currentColor" }: { name: string; size?: number; color?: string }) {
  const icon = SVG_ICONS[name];
  if (!icon) return <span style={{ fontSize: size }}>?</span>;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d={icon.path} />
    </svg>
  );
}

const ICON_CATEGORIES: Record<string, { label: string; icons: string[] }> = {
  common: {
    label: "常用",
    icons: [
      "🤖", "🧠", "💡", "🎯", "📊", "🔍", "🛠️", "📝",
      "🌐", "🚀", "⚡", "🎨", "📚", "🔬", "💻", "🎵",
    ],
  },
  people: {
    label: "人物",
    icons: [
      "👩‍💻", "👨‍💻", "👩‍🔬", "👨‍🏫", "👩‍🎨", "🧑‍💼", "🕵️", "🦸",
      "🧙", "👷", "👩‍⚕️", "🧑‍🍳", "👨‍🚀", "🥷", "🧝", "🧑‍🎓",
    ],
  },
  animal: {
    label: "动物",
    icons: [
      "🐶", "🐱", "🦊", "🐼", "🐨", "🦁", "🐯", "🐸",
      "🦉", "🐙", "🦋", "🐝", "🐬", "🐺", "🦅", "🐢",
    ],
  },
  object: {
    label: "物品",
    icons: [
      "📱", "🖥️", "⌨️", "🎮", "📡", "🔭", "🧲", "⚙️",
      "🗂️", "📦", "🏷️", "🔐", "🗺️", "🧩", "🪄", "💎",
    ],
  },
  nature: {
    label: "自然",
    icons: [
      "🌸", "🌻", "🌈", "🔥", "❄️", "🌙", "⭐", "☀️",
      "🌊", "🍀", "🌲", "🌋", "💫", "🪐", "🌍", "🌪️",
    ],
  },
  symbol: {
    label: "符号",
    icons: [
      "♟️", "🎲", "🏆", "🎪", "🎭", "🧿", "💠", "⚜️",
      "☯️", "♾️", "🔱", "❇️", "✨", "💥", "🔶", "🔷",
    ],
  },
  svg: {
    label: "线性",
    icons: SVG_ICON_KEYS.map((k) => `svg:${k}`),
  },
};
const EMOJI_PRESETS = Object.values(ICON_CATEGORIES).flatMap((c) => c.icons);

export function AgentManagerView({
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
  multiAgentEnabled = false,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
  multiAgentEnabled?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingProfile, setEditingProfile] = useState<AgentProfile>(EMPTY_PROFILE);
  const [isCreating, setIsCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [availableSkills, setAvailableSkills] = useState<SkillItem[]>([]);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  const [iconCat, setIconCat] = useState("common");
  const [toastMsg, setToastMsg] = useState<{ text: string; type: "ok" | "err" } | null>(null);
  const [activeCategory, setActiveCategory] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [categories, setCategories] = useState<CategoryInfo[]>([]);
  const [addingCategory, setAddingCategory] = useState(false);
  const [newCatLabel, setNewCatLabel] = useState("");
  const [newCatColor, setNewCatColor] = useState("#6b7280");
  const [batchSelected, setBatchSelected] = useState<Set<string>>(new Set());
  const importInputRef = useRef<HTMLInputElement>(null);

  const showToast = useCallback((text: string, type: "ok" | "err" = "ok") => {
    setToastMsg({ text, type });
    setTimeout(() => setToastMsg(null), 3500);
  }, []);

  const extractErrorMsg = (detail: unknown, fallback: string): string => {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d: Record<string, unknown>) => typeof d?.msg === "string" ? d.msg : "")
        .filter(Boolean);
      return msgs.length ? msgs.join("; ") : fallback;
    }
    if (typeof detail === "object" && detail !== null) {
      const d = detail as Record<string, unknown>;
      if (typeof d.msg === "string") return d.msg;
      try { return JSON.stringify(detail); } catch { /* fall through */ }
    }
    return fallback;
  };

  const fetchCategories = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/categories`);
      const data = await res.json();
      setCategories(data.categories || []);
    } catch (e) {
      logger.warn("AgentManager", "Failed to fetch categories", { error: String(e) });
    }
  }, [apiBaseUrl]);

  const fetchProfiles = useCallback(async () => {
    if (!multiAgentEnabled) return;
    setLoading(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/profiles?include_hidden=true`);
      const data = await res.json();
      setProfiles(data.profiles || []);
    } catch (e) {
      logger.warn("AgentManager", "Failed to fetch profiles", { error: String(e) });
    }
    setLoading(false);
  }, [apiBaseUrl, multiAgentEnabled]);

  const fetchSkills = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/skills`);
      const data = await res.json();
      setAvailableSkills(
        (data.skills || []).map((s: any) => ({
          skillId: s.skill_id || s.name,
          name: s.name,
          enabled: s.enabled !== false,
          name_i18n: s.name_i18n || null,
        })),
      );
    } catch {
      /* skills endpoint may not be available */
    }
  }, [apiBaseUrl]);

  const fetchModels = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/models`);
      const data = await res.json();
      setAvailableModels(data.models || []);
    } catch {
      /* models endpoint may not be available */
    }
  }, [apiBaseUrl]);

  const browserDownloadJson = useCallback((data: unknown, filename: string) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  const handleExport = useCallback(async (profileId: string) => {
    try {
      const defaultName = `${profileId}.json`;

      if (IS_TAURI) {
        const savePath = await saveFileDialog({
          title: "导出 Agent",
          defaultPath: defaultName,
          filters: [{ name: "JSON", extensions: ["json"] }],
        });
        if (!savePath) return;
        await safeFetch(`${apiBaseUrl}/api/agents/package/export-json`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile_id: profileId, output_path: savePath }),
        });
        showToast(`已导出到: ${savePath}`);
      } else {
        const res = await safeFetch(`${apiBaseUrl}/api/agents/package/export-json`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile_id: profileId }),
        });
        const data = await res.json();
        browserDownloadJson(data, defaultName);
        showToast(`Agent 已导出为 ${defaultName}`);
      }
    } catch (e) { showToast(String(e), "err"); }
  }, [apiBaseUrl, showToast, browserDownloadJson]);

  const handleImportFile = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await safeFetch(`${apiBaseUrl}/api/agents/package/import`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      showToast(data.message || `Agent「${data.profile?.name || ""}」导入成功`);
      fetchProfiles();
    } catch (err) { showToast(String(err), "err"); }
    if (importInputRef.current) importInputRef.current.value = "";
  }, [apiBaseUrl, showToast, fetchProfiles]);

  const toggleBatchSelect = useCallback((id: string) => {
    setBatchSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  const handleBatchExport = useCallback(async () => {
    if (batchSelected.size === 0) {
      showToast(t("agentManager.batchExportNone"), "err");
      return;
    }
    try {
      const ids = Array.from(batchSelected);
      const defaultName = ids.length === 1 ? `${ids[0]}.json` : `agents_batch_${ids.length}.json`;

      if (IS_TAURI) {
        const savePath = await saveFileDialog({
          title: "批量导出 Agent",
          defaultPath: defaultName,
          filters: [{ name: "JSON", extensions: ["json"] }],
        });
        if (!savePath) return;
        await safeFetch(`${apiBaseUrl}/api/agents/package/batch-export-json`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile_ids: ids, output_path: savePath }),
        });
        showToast(`已导出 ${ids.length} 个 Agent 到: ${savePath}`);
      } else {
        const res = await safeFetch(`${apiBaseUrl}/api/agents/package/batch-export-json`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile_ids: ids }),
        });
        const data = await res.json();
        browserDownloadJson(data, defaultName);
        showToast(t("agentManager.batchExportDone", { count: ids.length }));
      }
      setBatchSelected(new Set());
    } catch (e) { showToast(String(e), "err"); }
  }, [batchSelected, apiBaseUrl, showToast, t, browserDownloadJson]);

  useEffect(() => {
    if (visible && multiAgentEnabled) {
      fetchProfiles();
      fetchSkills();
      fetchCategories();
      fetchModels();
    }
  }, [visible, multiAgentEnabled, fetchProfiles, fetchSkills, fetchCategories, fetchModels]);

  const openCreateEditor = () => {
    setEditingProfile({ ...EMPTY_PROFILE });
    setIsCreating(true);
    setEditorOpen(true);
    setEmojiPickerOpen(false);
  };

  const openEditEditor = (profile: AgentProfile) => {
    setEditingProfile({ ...profile });
    setIsCreating(false);
    setEditorOpen(true);
    setEmojiPickerOpen(false);
  };

  const closeEditor = () => {
    setEditorOpen(false);
    setEmojiPickerOpen(false);
  };

  const generateId = (name: string) =>
    name
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 32) || "custom-agent";

  const handleSave = async () => {
    if (!editingProfile.name.trim()) return;
    setSaving(true);
    try {
      const payload = {
        id: editingProfile.id,
        name: editingProfile.name,
        description: editingProfile.description,
        icon: editingProfile.icon,
        color: editingProfile.color,
        skills: editingProfile.skills,
        skills_mode: editingProfile.skills_mode,
        custom_prompt: editingProfile.custom_prompt,
        preferred_endpoint: editingProfile.preferred_endpoint || null,
        category: editingProfile.category || "",
      };

      const url = isCreating
        ? `${apiBaseUrl}/api/agents/profiles`
        : `${apiBaseUrl}/api/agents/profiles/${editingProfile.id}`;
      const method = isCreating ? "POST" : "PUT";

      const res = await safeFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      closeEditor();
      fetchProfiles();
      showToast(t("agentManager.saveSuccess"), "ok");
    } catch (e) {
      showToast(String(e) || t("agentManager.saveFailed"), "err");
    }
    setSaving(false);
  };

  const handleDelete = async (profileId: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}`, { method: "DELETE" });
      setConfirmDeleteId(null);
      fetchProfiles();
      showToast(t("agentManager.deleteSuccess"), "ok");
    } catch (e) {
      showToast(String(e) || t("agentManager.deleteFailed"), "err");
    }
  };

  const toggleSkill = (skillName: string) => {
    setEditingProfile((prev) => {
      const skills = prev.skills.includes(skillName)
        ? prev.skills.filter((s) => s !== skillName)
        : [...prev.skills, skillName];
      return { ...prev, skills };
    });
  };

  const handleVisibility = async (profileId: string, hidden: boolean) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}/visibility`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden }),
      });
      fetchProfiles();
      showToast(t(hidden ? "agentManager.hideSuccess" : "agentManager.restoreSuccess"), "ok");
    } catch (e) {
      showToast(String(e), "err");
    }
  };

  const handleReset = async (profileId: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}/reset`, {
        method: "POST",
      });
      fetchProfiles();
      showToast(t("agentManager.resetSuccess"), "ok");
    } catch (e) {
      showToast(String(e), "err");
    }
  };

  const getCategoryLabel = (catId: string): string => {
    if (!catId) return t("agentManager.categoryAll");
    const found = categories.find((c) => c.id === catId);
    if (found) return found.label;
    const i18nMap: Record<string, string> = {
      general: "categoryGeneral", content: "categoryContent",
      enterprise: "categoryEnterprise", education: "categoryEducation",
      productivity: "categoryProductivity", devops: "categoryDevops",
    };
    return i18nMap[catId] ? t(`agentManager.${i18nMap[catId]}`) : catId;
  };

  const getCategoryColor = (catId: string): string => {
    const found = categories.find((c) => c.id === catId);
    return found?.color || "var(--primary, #3b82f6)";
  };

  const handleAddCategory = async () => {
    const label = newCatLabel.trim();
    if (!label) return;
    const ascii = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const id = ascii && /^[a-z]/.test(ascii) ? ascii : `cat-${Date.now()}`;
    try {
      await safeFetch(`${apiBaseUrl}/api/agents/categories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, label, color: newCatColor }),
      });
      showToast(`已添加分类「${label}」`);
      setAddingCategory(false);
      setNewCatLabel("");
      setNewCatColor("#6b7280");
      fetchCategories();
    } catch (err) { showToast(String(err), "err"); }
  };

  const visibleProfiles = profiles.filter((p) => !p.hidden);
  const hiddenProfiles = profiles.filter((p) => p.hidden);
  const filteredProfiles = activeCategory
    ? visibleProfiles.filter((p) => p.category === activeCategory)
    : visibleProfiles;

  if (!multiAgentEnabled) {
    return (
      <div style={{ padding: 40, textAlign: "center", opacity: 0.5 }}>
        <IconBot size={48} />
        <div style={{ marginTop: 12, fontWeight: 700 }}>{t("agentManager.disabled")}</div>
      </div>
    );
  }

  return (
    <div style={{ padding: 20, position: "relative", overflow: "auto", height: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 12px", marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 18, whiteSpace: "nowrap" }}>{t("agentManager.title")}</h2>
        <div style={{ flex: 1, minWidth: 24 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <button
            onClick={fetchProfiles}
            disabled={loading}
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "5px 10px", borderRadius: 8, border: "1px solid var(--line)",
              background: "var(--panel)", cursor: "pointer", fontSize: 12,
            }}
          >
            <IconRefresh size={14} />
            {loading ? t("dashboard.loading") : t("dashboard.refresh")}
          </button>
          {batchSelected.size > 0 && (
            <button
              onClick={handleBatchExport}
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "5px 10px", borderRadius: 8, border: "1px solid var(--primary, #3b82f6)",
                background: "rgba(59,130,246,0.08)", cursor: "pointer", fontSize: 12,
                color: "var(--primary, #3b82f6)", fontWeight: 600,
              }}
            >
              <IconDownload size={14} />
              {t("agentManager.batchExport", { count: batchSelected.size })}
            </button>
          )}
          <button
            onClick={() => importInputRef.current?.click()}
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "5px 10px", borderRadius: 8, border: "1px solid var(--line)",
              background: "var(--panel)", cursor: "pointer", fontSize: 12,
            }}
          >
            <IconUpload size={14} />
            {t("agentManager.import")}
          </button>
          <button
            onClick={openCreateEditor}
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "5px 12px", borderRadius: 8, border: "none",
              background: "var(--primary, #3b82f6)", color: "#fff",
              cursor: "pointer", fontSize: 12, fontWeight: 600,
            }}
          >
            <IconPlus size={14} />
            {t("agentManager.create")}
          </button>
        </div>
        <input
          ref={importInputRef}
          type="file"
          accept=".akita-agent,.json"
          style={{ display: "none" }}
          onChange={handleImportFile}
        />
      </div>

      {/* Category Tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 20, flexWrap: "wrap", alignItems: "center" }}>
        {/* "全部" tab */}
        <button
          onClick={() => setActiveCategory("")}
          style={{
            padding: "5px 14px", borderRadius: 20, border: "1px solid var(--line)",
            background: activeCategory === "" ? "var(--primary, #3b82f6)" : "var(--panel)",
            color: activeCategory === "" ? "#fff" : "inherit",
            cursor: "pointer", fontSize: 12, fontWeight: activeCategory === "" ? 600 : 400,
            transition: "all 0.15s",
          }}
        >
          {t("agentManager.categoryAll")}
          <Badge variant="secondary" className={cn("ml-1.5 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full", activeCategory === "" ? "bg-white/25 text-primary-foreground" : "bg-foreground/10 text-foreground/60")}>{visibleProfiles.length}</Badge>
        </button>
        {categories.map((cat) => (
          <button
            key={cat.id}
            onClick={() => setActiveCategory(cat.id)}
            style={{
              padding: "5px 14px", borderRadius: 20, border: "1px solid var(--line)",
              background: activeCategory === cat.id ? cat.color : "var(--panel)",
              color: activeCategory === cat.id ? "#fff" : "inherit",
              cursor: "pointer", fontSize: 12, fontWeight: activeCategory === cat.id ? 600 : 400,
              transition: "all 0.15s", position: "relative",
              display: "inline-flex", alignItems: "center", gap: 4,
            }}
          >
            {cat.label}
            <Badge variant="secondary" className={cn("ml-1 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full", activeCategory === cat.id ? "bg-white/25 text-primary-foreground" : "bg-foreground/10 text-foreground/60")}>{cat.agent_count ?? 0}</Badge>
            {!cat.builtin && (
              <span
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    await safeFetch(`${apiBaseUrl}/api/agents/categories/${cat.id}`, { method: "DELETE" });
                    showToast(`已删除分类「${cat.label}」`);
                    if (activeCategory === cat.id) setActiveCategory("");
                    fetchCategories();
                  } catch (err) { showToast(String(err), "err"); }
                }}
                title="删除此分类"
                style={{
                  marginLeft: 2, cursor: "pointer", opacity: 0.6, fontSize: 11,
                  lineHeight: 1, fontWeight: 700,
                }}
              >
                x
              </span>
            )}
          </button>
        ))}
        {/* Add category button / inline form */}
        {addingCategory ? (
          <div className="inline-flex items-center gap-1.5">
            <Input
              autoFocus
              placeholder={t("agentManager.categoryName", { defaultValue: "分类名称" })}
              value={newCatLabel}
              onChange={(e) => setNewCatLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") { setAddingCategory(false); setNewCatLabel(""); }
                if (e.key === "Enter" && newCatLabel.trim()) handleAddCategory();
              }}
              className="h-7 w-24 text-xs rounded-full px-3"
            />
            <label className="relative size-7 shrink-0 cursor-pointer rounded-full border border-input overflow-hidden" title={t("agentManager.categoryColor", { defaultValue: "选择颜色" })}>
              <span className="absolute inset-0 rounded-full" style={{ background: newCatColor }} />
              <input
                type="color"
                value={newCatColor}
                onChange={(e) => setNewCatColor(e.target.value)}
                className="absolute inset-0 opacity-0 cursor-pointer"
              />
            </label>
            <Button size="sm" className="h-7 rounded-full text-xs px-3" onClick={handleAddCategory} disabled={!newCatLabel.trim()}>
              {t("common.confirm")}
            </Button>
            <Button variant="ghost" size="sm" className="h-7 rounded-full text-xs px-2.5" onClick={() => { setAddingCategory(false); setNewCatLabel(""); }}>
              {t("common.cancel")}
            </Button>
          </div>
        ) : (
          <Button variant="outline" size="sm" className="h-7 rounded-full text-xs px-3 border-dashed opacity-60 hover:opacity-100" onClick={() => setAddingCategory(true)}>
            <IconPlus size={12} /> {t("agentManager.addCategory", { defaultValue: "添加分类" })}
          </Button>
        )}
      </div>


      {/* Agent Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 14 }}>
        {filteredProfiles.map((agent) => {
          const isSystem = agent.type === "system";
          return (
            <div
              key={agent.id}
              style={{
                padding: 16, borderRadius: 12,
                background: "var(--panel)", border: "1px solid var(--line)",
                position: "relative", overflow: "hidden",
                transition: "box-shadow 0.2s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 2px 12px rgba(0,0,0,0.08)")}
              onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "none")}
            >
              {/* Color bar */}
              <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: agent.color || "var(--brand)" }} />

              {/* Batch select checkbox */}
              <label
                title={t("agentManager.selectForBatch")}
                onClick={(e) => e.stopPropagation()}
                style={{
                  position: "absolute", top: 8, left: 8, zIndex: 2,
                  width: 15, height: 15, borderRadius: 3, cursor: "pointer",
                  border: batchSelected.has(agent.id) ? "none" : "1.5px solid #94a3b8",
                  background: batchSelected.has(agent.id) ? "var(--primary, #3b82f6)" : "#fff",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  transition: "all 0.15s",
                }}
              >
                <input
                  type="checkbox"
                  checked={batchSelected.has(agent.id)}
                  onChange={() => toggleBatchSelect(agent.id)}
                  style={{ display: "none" }}
                />
                {batchSelected.has(agent.id) && (
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
              </label>

              {/* Badges */}
              <div style={{ position: "absolute", top: 8, right: 8, display: "flex", gap: 4 }}>
                {agent.category && (
                  <span
                    style={{
                      fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 4,
                      background: `${getCategoryColor(agent.category || "")}20`,
                      color: getCategoryColor(agent.category || ""),
                    }}
                  >
                    {getCategoryLabel(agent.category)}
                  </span>
                )}
                <span
                  style={{
                    fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 4,
                    background: isSystem ? "rgba(99,102,241,0.12)" : "rgba(16,185,129,0.12)",
                    color: isSystem ? "#6366f1" : "#10b981",
                  }}
                >
                  {isSystem ? t("agentManager.systemBadge") : t("agentManager.customBadge")}
                </span>
                {isSystem && agent.user_customized && (
                  <span
                    style={{
                      fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 4,
                      background: "rgba(245,158,11,0.12)", color: "#f59e0b",
                    }}
                  >
                    {t("agentManager.customizedBadge")}
                  </span>
                )}
              </div>

              {/* Content */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, marginTop: 4 }}>
                <span style={{ fontSize: 28, lineHeight: 1, display: "flex", alignItems: "center" }}>
                  {agent.icon.startsWith("svg:") ? <SvgIcon name={agent.icon.slice(4)} size={28} color={agent.color || "currentColor"} /> : agent.icon}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{agent.name}</div>
                  <div style={{ fontSize: 11, opacity: 0.45, fontFamily: "monospace" }}>{agent.id}</div>
                </div>
              </div>
              <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 10, minHeight: 18, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {agent.description || "\u2014"}
              </div>

              {/* Actions */}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  onClick={() => openEditEditor(agent)}
                  style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                    background: "transparent", cursor: "pointer", fontSize: 12,
                  }}
                >
                  <IconEdit size={12} />
                  {t("agentManager.edit")}
                </button>
                <button
                  onClick={() => handleExport(agent.id)}
                  style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                    background: "transparent", cursor: "pointer", fontSize: 12,
                  }}
                  title={t("agentManager.exportTooltip")}
                >
                  <IconDownload size={12} />
                  {t("agentManager.export")}
                </button>
                {!isSystem && (
                  <button
                    onClick={() => setConfirmDeleteId(agent.id)}
                    style={{
                      display: "flex", alignItems: "center", gap: 4,
                      padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                      background: "transparent", cursor: "pointer", fontSize: 12,
                      color: "#ef4444",
                    }}
                  >
                    <IconTrash size={12} />
                    {t("agentManager.delete")}
                  </button>
                )}
                {isSystem && (
                  <button
                    onClick={() => handleVisibility(agent.id, true)}
                    style={{
                      display: "flex", alignItems: "center", gap: 4,
                      padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                      background: "transparent", cursor: "pointer", fontSize: 12,
                      opacity: 0.6,
                    }}
                  >
                    {t("agentManager.hide")}
                  </button>
                )}
                {isSystem && agent.user_customized && (
                  <button
                    onClick={() => handleReset(agent.id)}
                    style={{
                      display: "flex", alignItems: "center", gap: 4,
                      padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                      background: "transparent", cursor: "pointer", fontSize: 12,
                      color: "#f59e0b",
                    }}
                  >
                    {t("agentManager.resetDefault")}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {filteredProfiles.length === 0 && !loading && (
        <div style={{ textAlign: "center", padding: 40, opacity: 0.5 }}>
          <IconBot size={40} />
          <div style={{ marginTop: 8 }}>{t("common.noData")}</div>
        </div>
      )}

      {/* Hidden Agents Section */}
      {hiddenProfiles.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <button
            onClick={() => setShowHidden((v) => !v)}
            style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "6px 12px", borderRadius: 8, border: "1px solid var(--line)",
              background: "var(--panel)", cursor: "pointer", fontSize: 12,
              opacity: 0.7, width: "100%", justifyContent: "center",
            }}
          >
            {t("agentManager.hiddenSection")} ({hiddenProfiles.length})
            <span style={{ fontSize: 10, transform: showHidden ? "rotate(180deg)" : "rotate(0)", transition: "transform 0.2s" }}>&#9660;</span>
          </button>
          {showHidden && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 14, marginTop: 12 }}>
              {hiddenProfiles.map((agent) => (
                <div
                  key={agent.id}
                  style={{
                    padding: 16, borderRadius: 12,
                    background: "var(--panel)", border: "1px solid var(--line)",
                    position: "relative", overflow: "hidden",
                    opacity: 0.5, transition: "opacity 0.2s",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.8")}
                  onMouseLeave={(e) => (e.currentTarget.style.opacity = "0.5")}
                >
                  <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: agent.color || "var(--brand)" }} />
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, marginTop: 4 }}>
                    <span style={{ fontSize: 28, lineHeight: 1, display: "flex", alignItems: "center" }}>
                      {agent.icon.startsWith("svg:") ? <SvgIcon name={agent.icon.slice(4)} size={28} color={agent.color || "currentColor"} /> : agent.icon}
                    </span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{agent.name}</div>
                    </div>
                  </div>
                  <button
                    onClick={() => handleVisibility(agent.id, false)}
                    style={{
                      display: "flex", alignItems: "center", gap: 4,
                      padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                      background: "transparent", cursor: "pointer", fontSize: 12,
                      color: "#10b981",
                    }}
                  >
                    {t("agentManager.restore")}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {confirmDeleteId && (
        <div
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
            backdropFilter: "blur(4px)", WebkitBackdropFilter: "blur(4px)",
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
          }}
          onClick={() => setConfirmDeleteId(null)}
        >
          <div
            style={{
              background: "var(--panel)", borderRadius: 12, padding: 24,
              minWidth: 320, maxWidth: 400, boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>{t("agentManager.confirmDelete")}</div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                onClick={() => setConfirmDeleteId(null)}
                style={{
                  padding: "6px 14px", borderRadius: 8, border: "1px solid var(--line)",
                  background: "var(--panel)", cursor: "pointer", fontSize: 13,
                }}
              >
                {t("agentManager.cancel")}
              </button>
              <button
                onClick={() => handleDelete(confirmDeleteId)}
                style={{
                  padding: "6px 14px", borderRadius: 8, border: "none",
                  background: "#ef4444", color: "#fff", cursor: "pointer", fontSize: 13, fontWeight: 600,
                }}
              >
                {t("agentManager.delete")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast notification */}
      {toastMsg && (
        <div style={{
          position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          padding: "10px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600, zIndex: 2000,
          background: toastMsg.type === "ok" ? "#10b981" : "#ef4444", color: "#fff",
          boxShadow: "0 4px 16px rgba(0,0,0,0.18)",
          animation: "fadeIn 0.2s ease-out",
        }}>
          {toastMsg.text}
        </div>
      )}

      {/* Editor Sheet */}
      <Sheet open={editorOpen} onOpenChange={(open) => { if (!open) closeEditor(); }}>
        <SheetContent side="right" className="w-[460px] max-w-[90vw] overflow-y-auto p-0" onOpenAutoFocus={(e) => e.preventDefault()}>
          <SheetHeader className="px-6 pt-6 pb-2">
            <SheetTitle>{isCreating ? t("agentManager.create") : t("agentManager.edit")}</SheetTitle>
            <SheetDescription className="sr-only">
              {isCreating ? "Create a new agent profile" : "Edit agent profile"}
            </SheetDescription>
          </SheetHeader>

          <div className="flex flex-col gap-4 px-6 pb-6">
            {/* ID */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.id")}</Label>
              <Input
                value={editingProfile.id}
                onChange={(e) => setEditingProfile((p) => ({ ...p, id: e.target.value }))}
                disabled={!isCreating}
                placeholder="my-agent"
                className="font-mono text-[13px]"
              />
            </div>

            {/* Name */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.name")}</Label>
              <Input
                value={editingProfile.name}
                onChange={(e) => {
                  const name = e.target.value;
                  setEditingProfile((p) => ({
                    ...p,
                    name,
                    ...(isCreating && !p.id ? { id: generateId(name) } : {}),
                  }));
                }}
                placeholder="My Agent"
              />
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.description")}</Label>
              <Input
                value={editingProfile.description}
                onChange={(e) => setEditingProfile((p) => ({ ...p, description: e.target.value }))}
                placeholder="A brief description..."
              />
            </div>

            {/* Category */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.category")}</Label>
              <Select
                value={editingProfile.category || "_none_"}
                onValueChange={(v) => setEditingProfile((p) => ({ ...p, category: v === "_none_" ? "" : v }))}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="_none_">—</SelectItem>
                  {categories.map((cat) => (
                    <SelectItem key={cat.id} value={cat.id}>{cat.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Icon */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.icon")}</Label>
              <div className="relative">
                <Button
                  variant="outline"
                  className="h-9 w-9 text-[22px] p-0"
                  onClick={() => setEmojiPickerOpen((v) => !v)}
                >
                  {editingProfile.icon.startsWith("svg:")
                    ? <SvgIcon name={editingProfile.icon.slice(4)} size={24} />
                    : editingProfile.icon}
                </Button>
                {emojiPickerOpen && (
                  <div className="absolute top-full left-0 z-10 w-[260px] rounded-lg border bg-popover shadow-lg overflow-hidden">
                    <div className="flex border-b overflow-x-auto shrink-0">
                      {Object.entries(ICON_CATEGORIES).map(([key, cat]) => (
                        <button
                          key={key}
                          data-slot="skip"
                          onClick={() => setIconCat(key)}
                          className={`flex-none px-2.5 py-1.5 text-xs border-b-2 cursor-pointer whitespace-nowrap transition-colors ${
                            iconCat === key
                              ? "border-primary text-primary font-bold bg-primary/10"
                              : "border-transparent hover:bg-accent"
                          }`}
                        >
                          {cat.label}
                        </button>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-0.5 p-2 max-h-[180px] overflow-y-auto">
                      {(ICON_CATEGORIES[iconCat]?.icons || []).map((iconVal) => {
                        const isSvg = iconVal.startsWith("svg:");
                        const selected = editingProfile.icon === iconVal;
                        return (
                          <button
                            key={iconVal}
                            data-slot="skip"
                            title={isSvg ? (SVG_ICONS[iconVal.slice(4)]?.label || iconVal.slice(4)) : undefined}
                            onClick={() => {
                              setEditingProfile((p) => ({ ...p, icon: iconVal }));
                              setEmojiPickerOpen(false);
                            }}
                            className={`w-[38px] h-[38px] flex items-center justify-center rounded-lg cursor-pointer border-none transition-colors ${
                              selected ? "bg-accent" : "bg-transparent hover:bg-accent/50"
                            }`}
                            style={{ fontSize: isSvg ? 0 : 21 }}
                          >
                            {isSvg ? <SvgIcon name={iconVal.slice(4)} size={22} /> : iconVal}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Color */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.color")}</Label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={editingProfile.color}
                  onChange={(e) => setEditingProfile((p) => ({ ...p, color: e.target.value }))}
                  style={{ width: 36, height: 36, minWidth: 36, flexShrink: 0, border: "none", cursor: "pointer", borderRadius: 6, padding: 0, background: "none" }}
                />
                <Input
                  value={editingProfile.color}
                  onChange={(e) => setEditingProfile((p) => ({ ...p, color: e.target.value }))}
                  className="flex-1 min-w-0 font-mono text-[13px]"
                />
              </div>
            </div>

            {/* Skills Mode */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.skills")}</Label>
              <Select
                value={editingProfile.skills_mode}
                onValueChange={(v) => setEditingProfile((p) => ({ ...p, skills_mode: v }))}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("agentManager.skillsModeAll")}</SelectItem>
                  <SelectItem value="inclusive">{t("agentManager.skillsModeInclusive")}</SelectItem>
                  <SelectItem value="exclusive">{t("agentManager.skillsModeExclusive")}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Skills multi-select */}
            {editingProfile.skills_mode !== "all" && availableSkills.length > 0 && (
              <div className="max-h-[220px] overflow-y-auto rounded-lg border p-1">
                {availableSkills.map((skill) => {
                  const checked = editingProfile.skills.includes(skill.skillId);
                  return (
                    <label
                      key={skill.skillId}
                      className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md cursor-pointer text-[13px] transition-colors ${
                        checked ? "bg-primary/8" : "hover:bg-accent/50"
                      }`}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => toggleSkill(skill.skillId)}
                      />
                      <span className="flex-1 min-w-0 truncate">
                        {skill.name_i18n?.[i18n.language?.startsWith("zh") ? "zh" : i18n.language || "zh"] || skill.name}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}

            {/* Preferred Endpoint */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.preferredEndpoint")}</Label>
              <Select
                value={editingProfile.preferred_endpoint || "_auto_"}
                onValueChange={(v) => setEditingProfile((p) => ({ ...p, preferred_endpoint: v === "_auto_" ? null : v }))}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="_auto_">{t("agentManager.preferredEndpointAuto")}</SelectItem>
                  {availableModels.map((m) => (
                    <SelectItem key={m.name} value={m.name} disabled={m.status !== "healthy"}>
                      {m.name} ({m.model}){m.status !== "healthy" ? " ⚠" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Custom Prompt */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.prompt")}</Label>
              <Textarea
                value={editingProfile.custom_prompt}
                onChange={(e) => setEditingProfile((p) => ({ ...p, custom_prompt: e.target.value }))}
                maxLength={5000}
                rows={6}
                className="min-h-[100px] resize-y leading-relaxed"
                placeholder="Additional system prompt for this agent..."
              />
              <p className={`text-right text-xs ${editingProfile.custom_prompt.length > 4500 ? "text-destructive" : "text-muted-foreground"}`}>
                {editingProfile.custom_prompt.length} / 5000
              </p>
            </div>

            {/* Actions */}
            <div className="flex gap-2 pt-2">
              <Button variant="outline" className="flex-1" onClick={closeEditor}>
                {t("agentManager.cancel")}
              </Button>
              <Button
                className="flex-1"
                onClick={handleSave}
                disabled={saving || !editingProfile.name.trim()}
              >
                {saving ? t("common.loading") : t("agentManager.save")}
              </Button>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}