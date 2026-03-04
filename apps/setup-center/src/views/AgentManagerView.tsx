import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { IconBot, IconRefresh, IconPlus, IconEdit, IconTrash, IconDownload, IconUpload } from "../icons";
import { safeFetch } from "../providers";

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
      console.warn("Failed to fetch categories:", e);
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
      console.warn("Failed to fetch profiles:", e);
    }
    setLoading(false);
  }, [apiBaseUrl, multiAgentEnabled]);

  const fetchSkills = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/skills`);
      const data = await res.json();
      setAvailableSkills(data.skills || []);
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

  const handleExport = useCallback(async (profileId: string) => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/package/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profileId }),
      });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${profileId}.akita-agent`;
      a.click();
      URL.revokeObjectURL(url);
      showToast("Agent 已导出");
    } catch (e) { showToast(String(e), "err"); }
  }, [apiBaseUrl, showToast]);

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
      showToast(`Agent「${data.profile?.name || ""}」导入成功`);
      fetchProfiles();
    } catch (err) { showToast(String(err), "err"); }
    if (importInputRef.current) importInputRef.current.value = "";
  }, [apiBaseUrl, showToast, fetchProfiles]);

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
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <IconBot size={24} />
        <h2 style={{ margin: 0, fontSize: 18 }}>{t("agentManager.title")}</h2>
        <div style={{ flex: 1 }} />
        <button
          onClick={fetchProfiles}
          disabled={loading}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderRadius: 8, border: "1px solid var(--line)",
            background: "var(--panel)", cursor: "pointer", fontSize: 13,
          }}
        >
          <IconRefresh size={14} />
          {loading ? t("dashboard.loading") : t("dashboard.refresh")}
        </button>
        <button
          onClick={() => importInputRef.current?.click()}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 14px", borderRadius: 8, border: "1px solid var(--line)",
            background: "var(--panel)", cursor: "pointer", fontSize: 13,
          }}
        >
          <IconUpload size={14} />
          {t("agentManager.import")}
        </button>
        <button
          onClick={openCreateEditor}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 14px", borderRadius: 8, border: "none",
            background: "var(--primary, #3b82f6)", color: "#fff",
            cursor: "pointer", fontSize: 13, fontWeight: 600,
          }}
        >
          <IconPlus size={14} />
          {t("agentManager.create")}
        </button>
        <input
          ref={importInputRef}
          type="file"
          accept=".akita-agent"
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
          <div style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <input
              autoFocus
              placeholder="分类名称"
              value={newCatLabel}
              onChange={(e) => setNewCatLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") { setAddingCategory(false); setNewCatLabel(""); }
                if (e.key === "Enter" && newCatLabel.trim()) handleAddCategory();
              }}
              style={{
                padding: "4px 10px", borderRadius: 16, border: "1px solid var(--line)",
                background: "var(--panel)", fontSize: 12, width: 90, outline: "none",
              }}
            />
            <input
              type="color"
              value={newCatColor}
              onChange={(e) => setNewCatColor(e.target.value)}
              style={{ width: 26, height: 26, border: "none", cursor: "pointer", padding: 0, borderRadius: 4 }}
            />
            <button
              onClick={handleAddCategory}
              disabled={!newCatLabel.trim()}
              style={{
                padding: "4px 10px", borderRadius: 16, border: "none",
                background: "var(--primary, #3b82f6)", color: "#fff",
                cursor: newCatLabel.trim() ? "pointer" : "not-allowed",
                fontSize: 12, fontWeight: 600, opacity: newCatLabel.trim() ? 1 : 0.5,
              }}
            >
              {t("common.confirm") || "确定"}
            </button>
            <button
              onClick={() => { setAddingCategory(false); setNewCatLabel(""); }}
              style={{
                padding: "4px 8px", borderRadius: 16, border: "1px solid var(--line)",
                background: "var(--panel)", cursor: "pointer", fontSize: 12,
              }}
            >
              {t("common.cancel") || "取消"}
            </button>
          </div>
        ) : (
          <button
            onClick={() => setAddingCategory(true)}
            style={{
              padding: "5px 10px", borderRadius: 20, border: "1px dashed var(--line)",
              background: "transparent", cursor: "pointer", fontSize: 12,
              opacity: 0.6, display: "inline-flex", alignItems: "center", gap: 2,
            }}
          >
            <IconPlus size={12} /> {t("agentManager.addCategory") || "添加分类"}
          </button>
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

      {/* Editor Slide-in Panel */}
      {editorOpen && (
        <div
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
            backdropFilter: "blur(4px)", WebkitBackdropFilter: "blur(4px)",
            display: "flex", justifyContent: "flex-end", zIndex: 1000,
          }}
          onClick={closeEditor}
        >
          <div
            style={{
              width: 460, maxWidth: "90vw", height: "100%",
              background: "var(--panel)", boxShadow: "-4px 0 24px rgba(0,0,0,0.15)",
              overflowY: "auto", padding: 24,
              animation: "slideIn 0.2s ease-out",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <style>{`@keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }`}</style>

            <h3 style={{ margin: "0 0 20px 0", fontSize: 16 }}>
              {isCreating ? t("agentManager.create") : t("agentManager.edit")}
            </h3>

            {/* ID */}
            <label style={labelStyle}>{t("agentManager.id")}</label>
            <input
              value={editingProfile.id}
              onChange={(e) => setEditingProfile((p) => ({ ...p, id: e.target.value }))}
              disabled={!isCreating}
              style={{ ...inputStyle, opacity: isCreating ? 1 : 0.5, fontFamily: "monospace", fontSize: 13 }}
              placeholder="my-agent"
            />

            {/* Name */}
            <label style={labelStyle}>{t("agentManager.name")}</label>
            <input
              value={editingProfile.name}
              onChange={(e) => {
                const name = e.target.value;
                setEditingProfile((p) => ({
                  ...p,
                  name,
                  ...(isCreating && !p.id ? { id: generateId(name) } : {}),
                }));
              }}
              style={inputStyle}
              placeholder="My Agent"
            />

            {/* Description */}
            <label style={labelStyle}>{t("agentManager.description")}</label>
            <input
              value={editingProfile.description}
              onChange={(e) => setEditingProfile((p) => ({ ...p, description: e.target.value }))}
              style={inputStyle}
              placeholder="A brief description..."
            />

            {/* Category */}
            <label style={labelStyle}>{t("agentManager.category")}</label>
            <select
              value={editingProfile.category || ""}
              onChange={(e) => setEditingProfile((p) => ({ ...p, category: e.target.value }))}
              style={{ ...inputStyle, cursor: "pointer" }}
            >
              <option value="">—</option>
              {categories.map((cat) => (
                <option key={cat.id} value={cat.id}>{cat.label}</option>
              ))}
            </select>

            {/* Icon + Color row */}
            <div style={{ display: "flex", gap: 12, marginBottom: 4 }}>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>{t("agentManager.icon")}</label>
                <div style={{ position: "relative" }}>
                  <button
                    onClick={() => setEmojiPickerOpen((v) => !v)}
                    style={{
                      ...inputStyle,
                      cursor: "pointer", fontSize: 22, textAlign: "center",
                      padding: "6px", width: "100%", display: "flex",
                      alignItems: "center", justifyContent: "center", minHeight: 40,
                    }}
                  >
                    {editingProfile.icon.startsWith("svg:")
                      ? <SvgIcon name={editingProfile.icon.slice(4)} size={24} />
                      : editingProfile.icon}
                  </button>
                  {emojiPickerOpen && (
                    <div style={{
                      position: "absolute", top: "100%", left: 0, zIndex: 10,
                      background: "var(--panel)", border: "1px solid var(--line)",
                      borderRadius: 10, padding: 0, width: 260,
                      boxShadow: "0 8px 24px rgba(0,0,0,0.15)", overflow: "hidden",
                    }}>
                      <div style={{
                        display: "flex", borderBottom: "1px solid var(--line)",
                        overflowX: "auto", flexShrink: 0,
                      }}>
                        {Object.entries(ICON_CATEGORIES).map(([key, cat]) => (
                          <button
                            key={key}
                            onClick={() => setIconCat(key)}
                            style={{
                              flex: "0 0 auto", padding: "7px 10px", fontSize: 12,
                              border: "none", cursor: "pointer", whiteSpace: "nowrap",
                              background: iconCat === key ? "var(--primary-bg, rgba(59,130,246,0.1))" : "transparent",
                              fontWeight: iconCat === key ? 700 : 400,
                              color: iconCat === key ? "var(--primary, #3b82f6)" : "inherit",
                              borderBottom: iconCat === key ? "2px solid var(--primary, #3b82f6)" : "2px solid transparent",
                            }}
                          >
                            {cat.label}
                          </button>
                        ))}
                      </div>
                      <div style={{
                        display: "flex", flexWrap: "wrap", gap: 2, padding: 8,
                        maxHeight: 180, overflowY: "auto",
                      }}>
                        {(ICON_CATEGORIES[iconCat]?.icons || []).map((iconVal) => {
                          const isSvg = iconVal.startsWith("svg:");
                          const selected = editingProfile.icon === iconVal;
                          return (
                            <button
                              key={iconVal}
                              title={isSvg ? (SVG_ICONS[iconVal.slice(4)]?.label || iconVal.slice(4)) : undefined}
                              onClick={() => {
                                setEditingProfile((p) => ({ ...p, icon: iconVal }));
                                setEmojiPickerOpen(false);
                              }}
                              style={{
                                width: 38, height: 38, fontSize: isSvg ? 0 : 21, border: "none",
                                borderRadius: 8, cursor: "pointer", transition: "background 0.12s",
                                background: selected ? "var(--line)" : "transparent",
                                display: "flex", alignItems: "center", justifyContent: "center",
                              }}
                              onMouseEnter={(e) => { if (!selected) e.currentTarget.style.background = "var(--hover, rgba(0,0,0,0.05))"; }}
                              onMouseLeave={(e) => { e.currentTarget.style.background = selected ? "var(--line)" : "transparent"; }}
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
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>{t("agentManager.color")}</label>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    type="color"
                    value={editingProfile.color}
                    onChange={(e) => setEditingProfile((p) => ({ ...p, color: e.target.value }))}
                    style={{ width: 40, height: 36, border: "none", cursor: "pointer", borderRadius: 6, padding: 0, background: "none" }}
                  />
                  <input
                    value={editingProfile.color}
                    onChange={(e) => setEditingProfile((p) => ({ ...p, color: e.target.value }))}
                    style={{ ...inputStyle, flex: 1, fontFamily: "monospace", fontSize: 13 }}
                  />
                </div>
              </div>
            </div>

            {/* Skills Mode */}
            <label style={labelStyle}>{t("agentManager.skills")}</label>
            <select
              value={editingProfile.skills_mode}
              onChange={(e) => setEditingProfile((p) => ({ ...p, skills_mode: e.target.value }))}
              style={{ ...inputStyle, cursor: "pointer" }}
            >
              <option value="all">{t("agentManager.skillsModeAll")}</option>
              <option value="inclusive">{t("agentManager.skillsModeInclusive")}</option>
              <option value="exclusive">{t("agentManager.skillsModeExclusive")}</option>
            </select>

            {/* Skills multi-select */}
            {editingProfile.skills_mode !== "all" && availableSkills.length > 0 && (
              <div style={{
                maxHeight: 220, overflowY: "auto", border: "1px solid var(--line)",
                borderRadius: 8, padding: 4, marginBottom: 12,
              }}>
                {availableSkills.map((skill) => {
                  const checked = editingProfile.skills.includes(skill.name);
                  return (
                    <label
                      key={skill.name}
                      style={{
                        display: "flex", alignItems: "center", gap: 8,
                        padding: "6px 10px", borderRadius: 6, cursor: "pointer",
                        fontSize: 13, lineHeight: 1.4,
                        background: checked ? "var(--primary-bg, rgba(59,130,246,0.08))" : "transparent",
                        transition: "background 0.15s",
                      }}
                      onMouseEnter={(e) => { if (!checked) e.currentTarget.style.background = "var(--hover, rgba(0,0,0,0.04))"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = checked ? "var(--primary-bg, rgba(59,130,246,0.08))" : "transparent"; }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSkill(skill.name)}
                        style={{ accentColor: "var(--primary, #3b82f6)", flexShrink: 0, width: 16, height: 16 }}
                      />
                      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {skill.name_i18n?.[i18n.language?.startsWith("zh") ? "zh" : i18n.language || "zh"] || skill.name}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}

            {/* Preferred Endpoint */}
            <label style={labelStyle}>{t("agentManager.preferredEndpoint")}</label>
            <select
              value={editingProfile.preferred_endpoint || ""}
              onChange={(e) => setEditingProfile((p) => ({ ...p, preferred_endpoint: e.target.value || null }))}
              style={{ ...inputStyle, cursor: "pointer" }}
            >
              <option value="">{t("agentManager.preferredEndpointAuto")}</option>
              {availableModels.map((m) => (
                <option key={m.name} value={m.name} disabled={m.status !== "healthy"}>
                  {m.name} ({m.model}){m.status !== "healthy" ? " ⚠" : ""}
                </option>
              ))}
            </select>

            {/* Custom Prompt */}
            <label style={labelStyle}>{t("agentManager.prompt")}</label>
            <textarea
              value={editingProfile.custom_prompt}
              onChange={(e) => setEditingProfile((p) => ({ ...p, custom_prompt: e.target.value }))}
              maxLength={5000}
              rows={6}
              style={{
                ...inputStyle, resize: "vertical", fontFamily: "inherit",
                minHeight: 100, lineHeight: 1.5,
              }}
              placeholder="Additional system prompt for this agent..."
            />
            <div style={{ textAlign: "right", fontSize: 12, color: editingProfile.custom_prompt.length > 4500 ? "#e74c3c" : "#999", marginTop: 2 }}>
              {editingProfile.custom_prompt.length} / 5000
            </div>

            {/* Actions */}
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button
                onClick={closeEditor}
                style={{
                  flex: 1, padding: "8px 0", borderRadius: 8, border: "1px solid var(--line)",
                  background: "var(--panel)", cursor: "pointer", fontSize: 13,
                }}
              >
                {t("agentManager.cancel")}
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !editingProfile.name.trim()}
                style={{
                  flex: 1, padding: "8px 0", borderRadius: 8, border: "none",
                  background: "var(--primary, #3b82f6)", color: "#fff",
                  cursor: saving ? "wait" : "pointer", fontSize: 13, fontWeight: 600,
                  opacity: !editingProfile.name.trim() ? 0.5 : 1,
                }}
              >
                {saving ? t("common.loading") : t("agentManager.save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4, marginTop: 12, opacity: 0.7,
};

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "8px 10px", borderRadius: 8,
  border: "1px solid var(--line)", background: "var(--bg, #fff)",
  fontSize: 13, outline: "none", boxSizing: "border-box",
};
