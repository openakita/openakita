import { useState, useEffect, useCallback, useMemo, useRef, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { IconBot, IconRefresh, IconPlus, IconEdit, IconTrash, IconDownload, IconUpload, IconImage } from "../icons";
import { safeFetch } from "../providers";
import { logger, onWsEvent, saveFileDialog, IS_TAURI } from "../platform";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { ProviderIcon } from "@/components/ProviderIcon";
import { AgentIcon, AGENT_SVG_ICONS, isCustomAgentIcon } from "@/components/AgentIcon";

type AgentProfile = {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  type: string;
  skills: string[];
  skills_mode: string;
  tools: string[];
  tools_mode: string;
  mcp_servers: string[];
  mcp_mode: string;
  custom_prompt: string;
  preferred_endpoint?: string | null;
  endpoint_policy?: "prefer" | "require";
  category?: string;
  hidden?: boolean;
  user_customized?: boolean;
  identity_mode?: string;
  memory_mode?: string;
  memory_inherit_global?: boolean;
  name_i18n?: Record<string, string>;
  description_i18n?: Record<string, string>;
};

type SkillItem = {
  skillId: string;
  name: string;
  enabled: boolean;
  name_i18n?: Record<string, string> | null;
};

type ToolCategoryItem = {
  id: string;
  tools: string[];
};

type McpServerItem = {
  name: string;
  description?: string;
  connected?: boolean;
  enabled?: boolean;
  tool_count?: number;
  catalog_tool_count?: number;
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
  tools: [],
  tools_mode: "all",
  mcp_servers: [],
  mcp_mode: "all",
  custom_prompt: "",
  preferred_endpoint: null,
  endpoint_policy: "prefer",
  category: "",
  hidden: false,
  identity_mode: "shared",
  memory_mode: "shared",
  memory_inherit_global: true,
};

type CategoryInfo = {
  id: string;
  label: string;
  color: string;
  builtin: boolean;
  agent_count: number;
};

type AgentManagerStateResponse = {
  profiles?: AgentProfile[];
  categories?: CategoryInfo[];
};

const SVG_ICONS = AGENT_SVG_ICONS;
const SVG_ICON_KEYS = Object.keys(SVG_ICONS);

const AGENT_ICON_MAX_BYTES = 4 * 1024 * 1024;
const AGENT_ICON_ACCEPT = ".bmp,.gif,.jpg,.jpeg,.png,.webp,image/bmp,image/gif,image/jpeg,image/png,image/webp";

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
export function AgentManagerView({
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
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
  const [availableToolCategories, setAvailableToolCategories] = useState<ToolCategoryItem[]>([]);
  const [availableMcpServers, setAvailableMcpServers] = useState<McpServerItem[]>([]);
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
  const [toolSearch, setToolSearch] = useState("");
  const [mcpSearch, setMcpSearch] = useState("");
  const [skillSearch, setSkillSearch] = useState("");
  const importInputRef = useRef<HTMLInputElement>(null);
  const iconUploadInputRef = useRef<HTMLInputElement>(null);
  const [iconUploading, setIconUploading] = useState(false);

  // Isolation UI state
  const [identityTab, setIdentityTab] = useState<string>("SOUL.md");
  const [identityContent, setIdentityContent] = useState<string>("");
  const [identitySource, setIdentitySource] = useState<string>("global");
  const [identityLoading, setIdentityLoading] = useState(false);
  const [memoryStats, setMemoryStats] = useState<{ exists: boolean; semantic_count: number; db_size_bytes: number } | null>(null);

  const showToast = useCallback((text: string, type: "ok" | "err" = "ok") => {
    setToastMsg({ text, type });
    setTimeout(() => setToastMsg(null), 3500);
  }, []);

  const loadIdentityFile = useCallback(async (profileId: string, filename: string) => {
    if (!profileId) return;
    setIdentityLoading(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}/identity/${filename}`);
      const data = await res.json();
      setIdentityContent(data.content || "");
      setIdentitySource(data.source || "global");
    } catch {
      setIdentityContent("");
      setIdentitySource("global");
    }
    setIdentityLoading(false);
  }, [apiBaseUrl]);

  const saveIdentityFile = useCallback(async (profileId: string, filename: string, content: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}/identity/${filename}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      showToast(t("agentManager.identitySaveSuccess"), "ok");
      setIdentitySource("profile");
    } catch {
      showToast(t("agentManager.identitySaveFailed"), "err");
    }
  }, [apiBaseUrl, showToast, t]);

  const loadMemoryStats = useCallback(async (profileId: string) => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}/memory/stats`);
      const data = await res.json();
      setMemoryStats(data);
    } catch {
      setMemoryStats(null);
    }
  }, [apiBaseUrl]);

  const initProfileIdentity = useCallback(async (profileId: string) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}/identity/init`, { method: "POST" });
    } catch {}
  }, [apiBaseUrl]);

  const fetchManagerState = useCallback(async (opts?: { showLoading?: boolean }) => {
    const showLoading = opts?.showLoading !== false;
    if (showLoading) setLoading(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/manager-state?include_hidden=true`);
      const data = await res.json();
      const state = data as AgentManagerStateResponse;
      setProfiles(state.profiles || []);
      setCategories(state.categories || []);
    } catch (e) {
      logger.warn("AgentManager", "Failed to fetch manager state", { error: String(e) });
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [apiBaseUrl]);

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

  const fetchToolCategories = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/agents/tool-categories`);
      const data = await res.json();
      setAvailableToolCategories(
        (data.categories || []).map((cat: any) => ({
          id: String(cat.id || cat.name || ""),
          tools: Array.isArray(cat.tools) ? cat.tools.map((name: unknown) => String(name)) : [],
        })).filter((cat: ToolCategoryItem) => cat.id),
      );
    } catch {
      /* tool categories endpoint may not be available */
    }
  }, [apiBaseUrl]);

  const fetchMcpServers = useCallback(async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/mcp/servers`);
      const data = await res.json();
      setAvailableMcpServers(
        (data.servers || []).map((server: any) => ({
          name: String(server.name || ""),
          description: server.description || "",
          connected: !!server.connected,
          enabled: server.enabled !== false,
          tool_count: Number(server.tool_count || 0),
          catalog_tool_count: Number(server.catalog_tool_count || 0),
        })).filter((server: McpServerItem) => server.name),
      );
    } catch {
      /* MCP endpoint may not be available */
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
      fetchManagerState();
    } catch (err) { showToast(String(err), "err"); }
    if (importInputRef.current) importInputRef.current.value = "";
  }, [apiBaseUrl, showToast, fetchManagerState]);

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
    if (visible) {
      fetchManagerState();
      fetchSkills();
      fetchToolCategories();
      fetchMcpServers();
      fetchModels();
    }
  }, [
    visible,
    fetchManagerState,
    fetchSkills,
    fetchToolCategories,
    fetchMcpServers,
    fetchModels,
  ]);

  useEffect(() => {
    if (!visible) return;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleRefresh = () => {
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        fetchManagerState({ showLoading: false });
      }, 50);
    };
    const unsubscribe = onWsEvent((event) => {
      if (event === "agents:profiles_changed" || event === "agents:categories_changed") {
        scheduleRefresh();
      }
    });
    return () => {
      if (refreshTimer) clearTimeout(refreshTimer);
      unsubscribe();
    };
  }, [visible, fetchManagerState]);

  useEffect(() => {
    if (activeCategory && !categories.some((cat) => cat.id === activeCategory)) {
      setActiveCategory("");
    }
  }, [activeCategory, categories]);

  const openCreateEditor = () => {
    setEditingProfile({ ...EMPTY_PROFILE });
    setIsCreating(true);
    setEditorOpen(true);
    setEmojiPickerOpen(false);
    setToolSearch("");
    setMcpSearch("");
    setSkillSearch("");
    setIdentityContent("");
    setIdentitySource("global");
    setMemoryStats(null);
  };

  const openEditEditor = (profile: AgentProfile) => {
    setEditingProfile({
      ...EMPTY_PROFILE,
      ...profile,
      tools: profile.tools || [],
      tools_mode: profile.tools_mode || "all",
      mcp_servers: profile.mcp_servers || [],
      mcp_mode: profile.mcp_mode || "all",
      skills: profile.skills || [],
      skills_mode: profile.skills_mode || "all",
    });
    setIsCreating(false);
    setEditorOpen(true);
    setEmojiPickerOpen(false);
    setToolSearch("");
    setMcpSearch("");
    setSkillSearch("");
    if (profile.identity_mode === "custom") {
      loadIdentityFile(profile.id, identityTab);
    }
    if (profile.memory_mode === "isolated") {
      loadMemoryStats(profile.id);
    }
  };

  const closeEditor = () => {
    setEditorOpen(false);
    setEmojiPickerOpen(false);
    setToolSearch("");
    setMcpSearch("");
    setSkillSearch("");
  };

  const handleIconUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > AGENT_ICON_MAX_BYTES) {
      showToast(t("agentManager.iconImageTooLarge"), "err");
      return;
    }

    setIconUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await safeFetch(`${apiBaseUrl}/api/agents/avatars/upload`, {
        method: "POST",
        body: form,
      });
      const data = await res.json();
      if (!data?.url) throw new Error("Missing uploaded icon URL");
      setEditingProfile((p) => ({ ...p, icon: data.url }));
      setEmojiPickerOpen(false);
      showToast(t("agentManager.iconUploadSuccess"), "ok");
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      showToast(t("agentManager.iconUploadFailed", { error: message }), "err");
    } finally {
      setIconUploading(false);
    }
  };

  const generateId = (name: string) =>
    name
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 32) || "custom-agent";

  const ID_PATTERN = /^[a-z0-9_-]+$/;
  const isIdValid =
    editingProfile.id.length > 0 &&
    editingProfile.id.length <= 64 &&
    ID_PATTERN.test(editingProfile.id);

  const handleSave = async () => {
    if (!editingProfile.name.trim()) return;
    if (isCreating && !isIdValid) {
      showToast(t("agentManager.idInvalid"), "err");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        id: editingProfile.id,
        name: editingProfile.name,
        description: editingProfile.description,
        icon: editingProfile.icon,
        color: editingProfile.color,
        tools: editingProfile.tools,
        tools_mode: editingProfile.tools_mode,
        mcp_servers: editingProfile.mcp_servers,
        mcp_mode: editingProfile.mcp_mode,
        skills: editingProfile.skills,
        skills_mode: editingProfile.skills_mode,
        custom_prompt: editingProfile.custom_prompt,
        preferred_endpoint: editingProfile.preferred_endpoint || null,
        endpoint_policy: editingProfile.endpoint_policy || "prefer",
        category: editingProfile.category || "",
        identity_mode: editingProfile.identity_mode || "shared",
        memory_mode: editingProfile.memory_mode || "shared",
        memory_inherit_global: editingProfile.memory_inherit_global ?? true,
      };

      const url = isCreating
        ? `${apiBaseUrl}/api/agents/profiles`
        : `${apiBaseUrl}/api/agents/profiles/${editingProfile.id}`;
      const method = isCreating ? "POST" : "PUT";

      await safeFetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      closeEditor();
      fetchManagerState();
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
      fetchManagerState();
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

  const toggleToolCategory = (categoryId: string) => {
    setEditingProfile((prev) => {
      const tools = prev.tools.includes(categoryId)
        ? prev.tools.filter((name) => name !== categoryId)
        : [...prev.tools, categoryId];
      return { ...prev, tools };
    });
  };

  const toggleMcpServer = (serverName: string) => {
    setEditingProfile((prev) => {
      const mcp_servers = prev.mcp_servers.includes(serverName)
        ? prev.mcp_servers.filter((name) => name !== serverName)
        : [...prev.mcp_servers, serverName];
      return { ...prev, mcp_servers };
    });
  };

  const handleVisibility = async (profileId: string, hidden: boolean) => {
    try {
      await safeFetch(`${apiBaseUrl}/api/agents/profiles/${profileId}/visibility`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden }),
      });
      fetchManagerState();
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
      fetchManagerState();
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

  const langKey = i18n.language?.startsWith("zh") ? "zh" : "en";
  const getI18nName = (agent: AgentProfile) => agent.name_i18n?.[langKey] || agent.name;
  const getI18nDesc = (agent: AgentProfile) => agent.description_i18n?.[langKey] || agent.description;

  const getToolCategoryLabel = (categoryId: string): string =>
    t(`agentManager.toolCategory.${categoryId}.label`, { defaultValue: categoryId });

  const getToolCategoryDescription = (categoryId: string): string =>
    t(`agentManager.toolCategory.${categoryId}.description`, { defaultValue: "" });

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
      fetchManagerState();
    } catch (err) { showToast(String(err), "err"); }
  };

  const visibleProfiles = useMemo(() => profiles.filter((p) => !p.hidden), [profiles]);
  const hiddenProfiles = useMemo(() => profiles.filter((p) => p.hidden), [profiles]);
  const categoryProfileCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const profile of visibleProfiles) {
      if (!profile.category) continue;
      counts.set(profile.category, (counts.get(profile.category) || 0) + 1);
    }
    return counts;
  }, [visibleProfiles]);
  const filteredProfiles = activeCategory
    ? visibleProfiles.filter((p) => p.category === activeCategory)
    : visibleProfiles;
  const toolQuery = toolSearch.trim().toLowerCase();
  const filteredToolCategories = availableToolCategories.filter((category) => {
    if (!toolQuery) return true;
    return (
      category.id.toLowerCase().includes(toolQuery) ||
      getToolCategoryLabel(category.id).toLowerCase().includes(toolQuery) ||
      getToolCategoryDescription(category.id).toLowerCase().includes(toolQuery)
    );
  });
  const mcpQuery = mcpSearch.trim().toLowerCase();
  const filteredMcpServers = availableMcpServers.filter((server) => {
    if (!mcpQuery) return true;
    return (
      server.name.toLowerCase().includes(mcpQuery) ||
      (server.description || "").toLowerCase().includes(mcpQuery)
    );
  });
  const skillQuery = skillSearch.trim().toLowerCase();
  const filteredSkills = availableSkills.filter((skill) => {
    if (!skillQuery) return true;
    const displayName = skill.name_i18n?.[langKey] || skill.name;
    return displayName.toLowerCase().includes(skillQuery) || skill.name.toLowerCase().includes(skillQuery);
  });

  return (
    <div style={{ padding: 20, position: "relative", overflow: "auto", height: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 12px", marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 18, whiteSpace: "nowrap" }}>{t("agentManager.title")}</h2>
        <div style={{ flex: 1, minWidth: 24 }} />
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <button
            onClick={() => fetchManagerState()}
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
            <Badge variant="secondary" className={cn("ml-1 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full", activeCategory === cat.id ? "bg-white/25 text-primary-foreground" : "bg-foreground/10 text-foreground/60")}>{categoryProfileCounts.get(cat.id) || 0}</Badge>
            {!cat.builtin && (
              <span
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    await safeFetch(`${apiBaseUrl}/api/agents/categories/${cat.id}`, { method: "DELETE" });
                    showToast(`已删除分类「${cat.label}」`);
                    if (activeCategory === cat.id) setActiveCategory("");
                    fetchManagerState();
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
                  <AgentIcon icon={agent.icon} size={28} color={agent.color || "currentColor"} apiBaseUrl={apiBaseUrl} />
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{getI18nName(agent)}</div>
                  <div style={{ fontSize: 11, opacity: 0.45, fontFamily: "monospace" }}>{agent.id}</div>
                </div>
              </div>
              <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 10, minHeight: 18, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {getI18nDesc(agent) || "\u2014"}
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
        <div style={{ textAlign: "center", padding: 28, opacity: 0.55 }}>
          <IconBot size={32} />
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
                      <AgentIcon icon={agent.icon} size={28} color={agent.color || "currentColor"} apiBaseUrl={apiBaseUrl} />
                    </span>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{getI18nName(agent)}</div>
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
        <SheetContent side="right" className="w-[460px] max-w-[90vw] overflow-hidden p-0" onOpenAutoFocus={(e) => e.preventDefault()}>
          <SheetHeader className="shrink-0 px-6 pt-6 pb-2">
            <SheetTitle>{isCreating ? t("agentManager.create") : t("agentManager.edit")}</SheetTitle>
            <SheetDescription className="sr-only">
              {isCreating ? t("agentManager.create") : t("agentManager.edit")}
            </SheetDescription>
          </SheetHeader>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 pt-4">
          <div className="flex flex-col gap-4">
            {/* ID */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.id")}</Label>
              <Input
                value={editingProfile.id}
                onChange={(e) => setEditingProfile((p) => ({ ...p, id: e.target.value }))}
                disabled={!isCreating}
                placeholder="my-agent"
                className={`font-mono text-[13px] ${isCreating && editingProfile.id && !isIdValid ? "border-red-500 focus-visible:ring-red-500" : ""}`}
              />
              {isCreating && (
                <p className={`text-[11px] ${editingProfile.id && !isIdValid ? "text-red-400" : "opacity-40"}`}>
                  {editingProfile.id && !isIdValid
                    ? t("agentManager.idInvalid")
                    : t("agentManager.idHint")}
                </p>
              )}
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
              <div className="flex items-center gap-2">
                <div className="relative">
                  <Button
                    variant="outline"
                    className="h-9 w-9 text-[22px] p-0 overflow-hidden"
                    onClick={() => setEmojiPickerOpen((v) => !v)}
                  >
                    <AgentIcon icon={editingProfile.icon} size={24} apiBaseUrl={apiBaseUrl} />
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
                              {isSvg ? <AgentIcon icon={iconVal} size={22} /> : iconVal}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
                <input
                  ref={iconUploadInputRef}
                  type="file"
                  accept={AGENT_ICON_ACCEPT}
                  className="hidden"
                  onChange={handleIconUpload}
                />
                <Button
                  variant="outline"
                  size="sm"
                  type="button"
                  className="h-9 gap-1.5 text-xs"
                  disabled={iconUploading}
                  onClick={() => iconUploadInputRef.current?.click()}
                >
                  <IconImage size={14} />
                  {iconUploading ? t("agentManager.iconUploading") : t("agentManager.uploadIcon")}
                </Button>
                {isCustomAgentIcon(editingProfile.icon) && (
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    className="h-9 text-xs"
                    onClick={() => setEditingProfile((p) => ({ ...p, icon: "🤖" }))}
                  >
                    {t("agentManager.removeCustomIcon")}
                  </Button>
                )}
              </div>
              <p className="text-[11px] opacity-40">{t("agentManager.iconUploadHint")}</p>
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

            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.systemAbilities")}</Label>
              <Select
                value={editingProfile.tools_mode}
                onValueChange={(v) => { setEditingProfile((p) => ({ ...p, tools_mode: v })); setToolSearch(""); }}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("agentManager.modeAll")}</SelectItem>
                  <SelectItem value="inclusive">{t("agentManager.modeInclusive")}</SelectItem>
                  <SelectItem value="exclusive">{t("agentManager.modeExclusive")}</SelectItem>
                </SelectContent>
              </Select>
              {editingProfile.tools_mode !== "all" && (
                <>
                  {availableToolCategories.length > 4 && (
                    <Input
                      placeholder={t("agentManager.systemSearchPlaceholder")}
                      value={toolSearch}
                      onChange={(e) => setToolSearch(e.target.value)}
                      className="h-8 text-xs"
                    />
                  )}
                  <div className="max-h-[220px] overflow-y-auto rounded-md border p-1">
                    {availableToolCategories.length === 0 ? (
                      <div className="px-2 py-3 text-center text-[11px] text-muted-foreground">
                        {t("agentManager.noSystemAbilities")}
                      </div>
                    ) : filteredToolCategories.length === 0 ? (
                      <div className="px-2 py-3 text-center text-[11px] text-muted-foreground">
                        {t("agentManager.noCapabilityMatches")}
                      </div>
                    ) : (
                      filteredToolCategories.map((category) => {
                        const checked = editingProfile.tools.includes(category.id);
                        return (
                          <label
                            key={category.id}
                            className={`flex cursor-pointer items-start gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors ${
                              checked ? "bg-primary/8" : "hover:bg-accent/50"
                            }`}
                          >
                            <Checkbox
                              checked={checked}
                              onCheckedChange={() => toggleToolCategory(category.id)}
                              className="mt-0.5"
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-medium">{getToolCategoryLabel(category.id)}</span>
                              <span className="block truncate text-[11px] text-muted-foreground">
                                {getToolCategoryDescription(category.id)}
                              </span>
                            </span>
                          </label>
                        );
                      })
                    )}
                  </div>
                </>
              )}
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.externalServices")}</Label>
              <Select
                value={editingProfile.mcp_mode}
                onValueChange={(v) => { setEditingProfile((p) => ({ ...p, mcp_mode: v })); setMcpSearch(""); }}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("agentManager.modeAll")}</SelectItem>
                  <SelectItem value="inclusive">{t("agentManager.modeInclusive")}</SelectItem>
                  <SelectItem value="exclusive">{t("agentManager.modeExclusive")}</SelectItem>
                </SelectContent>
              </Select>
              {editingProfile.mcp_mode !== "all" && (
                <>
                  {availableMcpServers.length > 4 && (
                    <Input
                      placeholder={t("agentManager.serviceSearchPlaceholder")}
                      value={mcpSearch}
                      onChange={(e) => setMcpSearch(e.target.value)}
                      className="h-8 text-xs"
                    />
                  )}
                  <div className="max-h-[220px] overflow-y-auto rounded-md border p-1">
                    {availableMcpServers.length === 0 ? (
                      <div className="px-2 py-3 text-center text-[11px] text-muted-foreground">
                        {t("agentManager.noExternalServices")}
                      </div>
                    ) : filteredMcpServers.length === 0 ? (
                      <div className="px-2 py-3 text-center text-[11px] text-muted-foreground">
                        {t("agentManager.noCapabilityMatches")}
                      </div>
                    ) : (
                      filteredMcpServers.map((server) => {
                        const checked = editingProfile.mcp_servers.includes(server.name);
                        const actionCount = server.tool_count || server.catalog_tool_count || 0;
                        return (
                          <label
                            key={server.name}
                            className={`flex cursor-pointer items-start gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors ${
                              checked ? "bg-primary/8" : "hover:bg-accent/50"
                            }`}
                          >
                            <Checkbox
                              checked={checked}
                              onCheckedChange={() => toggleMcpServer(server.name)}
                              className="mt-0.5"
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate font-medium">{server.name}</span>
                              <span className="block truncate text-[11px] text-muted-foreground">
                                {server.description || t("agentManager.serviceDefaultDescription")}
                              </span>
                            </span>
                            <span className="flex shrink-0 flex-col items-end gap-1">
                              <Badge
                                variant={server.enabled === false || !server.connected ? "outline" : "default"}
                                className="text-[10px] font-normal"
                              >
                                {server.enabled === false
                                  ? t("agentManager.serviceDisabled")
                                  : server.connected
                                    ? t("agentManager.serviceReady")
                                    : t("agentManager.serviceNotConnected")}
                              </Badge>
                              {actionCount > 0 && (
                                <span className="text-[10px] text-muted-foreground">
                                  {t("agentManager.availableActions", { count: actionCount })}
                                </span>
                              )}
                            </span>
                          </label>
                        );
                      })
                    )}
                  </div>
                </>
              )}
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.skillExtensions")}</Label>
              <Select
                value={editingProfile.skills_mode}
                onValueChange={(v) => { setEditingProfile((p) => ({ ...p, skills_mode: v })); setSkillSearch(""); }}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("agentManager.modeAll")}</SelectItem>
                  <SelectItem value="inclusive">{t("agentManager.modeInclusive")}</SelectItem>
                  <SelectItem value="exclusive">{t("agentManager.modeExclusive")}</SelectItem>
                </SelectContent>
              </Select>
              {editingProfile.skills_mode !== "all" && (
                <>
                  {availableSkills.length > 4 && (
                    <Input
                      placeholder={t("agentManager.skillSearchPlaceholder")}
                      value={skillSearch}
                      onChange={(e) => setSkillSearch(e.target.value)}
                      className="h-8 text-xs"
                    />
                  )}
                  <div className="max-h-[220px] overflow-y-auto rounded-md border p-1">
                    {availableSkills.length === 0 ? (
                      <div className="px-2 py-3 text-center text-[11px] text-muted-foreground">
                        {t("agentManager.noSkills")}
                      </div>
                    ) : filteredSkills.length === 0 ? (
                      <div className="px-2 py-3 text-center text-[11px] text-muted-foreground">
                        {t("agentManager.noCapabilityMatches")}
                      </div>
                    ) : (
                      filteredSkills.map((skill) => {
                        const checked = editingProfile.skills.includes(skill.skillId);
                        return (
                          <label
                            key={skill.skillId}
                            className={`flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors ${
                              checked ? "bg-primary/8" : "hover:bg-accent/50"
                            }`}
                          >
                            <Checkbox
                              checked={checked}
                              onCheckedChange={() => toggleSkill(skill.skillId)}
                            />
                            <span className="min-w-0 flex-1 truncate">
                              {skill.name_i18n?.[langKey] || skill.name}
                            </span>
                            {!skill.enabled && (
                              <Badge variant="outline" className="shrink-0 text-[10px] font-normal">
                                {t("agentManager.skillDisabled")}
                              </Badge>
                            )}
                          </label>
                        );
                      })
                    )}
                  </div>
                </>
              )}
            </div>

            {/* Preferred Endpoint */}
            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.preferredEndpoint")}</Label>
              <Select
                value={editingProfile.preferred_endpoint || "_auto_"}
                onValueChange={(v) => setEditingProfile((p) => ({
                  ...p,
                  preferred_endpoint: v === "_auto_" ? null : v,
                  endpoint_policy: v === "_auto_" ? "prefer" : (p.endpoint_policy || "prefer"),
                }))}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="_auto_">{t("agentManager.preferredEndpointAuto")}</SelectItem>
                  {availableModels.map((m) => (
                    <SelectItem key={m.name} value={m.name} disabled={m.status !== "healthy"}>
                      <span className="inline-flex items-center gap-2 align-middle">
                        <ProviderIcon slug={m.provider} size={14} title={m.provider} />
                        <span>{m.name} ({m.model}){m.status !== "healthy" ? " ⚠" : ""}</span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs opacity-70">{t("agentManager.endpointPolicy")}</Label>
              <Select
                value={editingProfile.endpoint_policy || "prefer"}
                onValueChange={(v) => setEditingProfile((p) => ({ ...p, endpoint_policy: v as "prefer" | "require" }))}
                disabled={!editingProfile.preferred_endpoint}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="prefer">{t("agentManager.endpointPolicyPrefer")}</SelectItem>
                  <SelectItem value="require">{t("agentManager.endpointPolicyRequire")}</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">{t("agentManager.endpointPolicyHint")}</p>
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

            {/* Isolation Config */}
            {!isCreating && (
            <div className="space-y-4 rounded-lg border p-4">
              <p className="text-xs font-semibold tracking-wide uppercase text-muted-foreground">{t("agentManager.isolationTitle")}</p>

              {/* Identity mode */}
              <div className="flex items-center justify-between gap-4 overflow-x-auto">
                <div className="min-w-0 space-y-0.5">
                  <Label className="truncate text-sm font-medium leading-none" title={t("agentManager.identityMode")}>{t("agentManager.identityMode")}</Label>
                  <p className="truncate text-[11px] text-muted-foreground" title={t("agentManager.identityModeHint")}>{t("agentManager.identityModeHint")}</p>
                </div>
                <Switch
                  checked={editingProfile.identity_mode === "custom"}
                  onCheckedChange={async (checked) => {
                    const next = checked ? "custom" : "shared";
                    setEditingProfile((p) => ({ ...p, identity_mode: next }));
                    if (checked) {
                      await initProfileIdentity(editingProfile.id);
                      loadIdentityFile(editingProfile.id, identityTab);
                    }
                  }}
                />
              </div>

              {/* Memory mode */}
              <div className="flex items-center justify-between gap-4 overflow-x-auto">
                <div className="min-w-0 space-y-0.5">
                  <Label className="truncate text-sm font-medium leading-none" title={t("agentManager.memoryMode")}>{t("agentManager.memoryMode")}</Label>
                  <p className="truncate text-[11px] text-muted-foreground" title={t("agentManager.memoryModeHint")}>{t("agentManager.memoryModeHint")}</p>
                </div>
                <Switch
                  checked={editingProfile.memory_mode === "isolated"}
                  onCheckedChange={(checked) => {
                    const next = checked ? "isolated" : "shared";
                    setEditingProfile((p) => ({ ...p, memory_mode: next }));
                    if (checked) loadMemoryStats(editingProfile.id);
                  }}
                />
              </div>

              {/* Inherit global memory */}
              {editingProfile.memory_mode === "isolated" && (
                <div className="flex items-center gap-2.5 overflow-x-auto rounded-md bg-muted/50 px-3 py-2">
                  <Checkbox
                    id="inherit-global"
                    checked={editingProfile.memory_inherit_global ?? true}
                    onCheckedChange={(checked) => setEditingProfile((p) => ({ ...p, memory_inherit_global: !!checked }))}
                  />
                  <div className="min-w-0 space-y-0.5">
                    <Label htmlFor="inherit-global" className="truncate text-xs font-medium leading-none cursor-pointer" title={t("agentManager.inheritGlobal")}>{t("agentManager.inheritGlobal")}</Label>
                    <p className="truncate text-[11px] text-muted-foreground" title={t("agentManager.inheritGlobalHint")}>{t("agentManager.inheritGlobalHint")}</p>
                  </div>
                </div>
              )}

              {/* Memory stats */}
              {editingProfile.memory_mode === "isolated" && memoryStats && (
                <div className="flex items-center gap-3 overflow-x-auto rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground whitespace-nowrap">
                  <span title={t("agentManager.semanticCount", { count: memoryStats.semantic_count })}>{t("agentManager.semanticCount", { count: memoryStats.semantic_count })}</span>
                  <span className="text-muted-foreground/50">·</span>
                  <span title={`${(memoryStats.db_size_bytes / 1024).toFixed(0)} KB`}>{(memoryStats.db_size_bytes / 1024).toFixed(0)} KB</span>
                </div>
              )}

              {/* Identity file editor */}
              {editingProfile.identity_mode === "custom" && (
                <div className="space-y-2.5 pt-1">
                  <div className="flex gap-1 rounded-md bg-muted p-0.5">
                    {["SOUL.md", "AGENT.md", "USER.md", "MEMORY.md"].map((f) => (
                      <button
                        key={f}
                        type="button"
                        className={cn(
                          "flex-1 rounded-sm px-2 py-1 text-xs font-medium transition-colors",
                          identityTab === f
                            ? "bg-background text-foreground shadow-sm"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                        onClick={() => { setIdentityTab(f); loadIdentityFile(editingProfile.id, f); }}
                      >
                        {f.replace(".md", "")}
                      </button>
                    ))}
                  </div>
                  {identityLoading ? (
                    <p className="text-xs text-muted-foreground py-6 text-center">{t("common.loading")}</p>
                  ) : (
                    <>
                      <Textarea
                        value={identityContent}
                        onChange={(e) => setIdentityContent(e.target.value)}
                        rows={8}
                        className="min-h-[120px] resize-y font-mono text-xs leading-relaxed"
                        placeholder={identitySource === "global" ? t("agentManager.identityInheritHint") : ""}
                      />
                      <div className="flex items-center justify-between gap-3 overflow-x-auto">
                        <Badge variant="outline" className="shrink-0 text-[10px] font-normal" title={identitySource === "global" ? t("agentManager.sourceGlobal") : t("agentManager.sourceProfile")}>
                          {identitySource === "global" ? t("agentManager.sourceGlobal") : t("agentManager.sourceProfile")}
                        </Badge>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 shrink-0 text-xs"
                          onClick={() => saveIdentityFile(editingProfile.id, identityTab, identityContent)}
                        >
                          {t("agentManager.saveFile")}
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
            )}

          </div>
          </div>

          {/* Actions */}
          <div className="sticky bottom-0 z-10 flex shrink-0 gap-2 border-t bg-background/95 px-6 py-4 shadow-[0_-8px_24px_rgba(0,0,0,0.08)] backdrop-blur supports-[backdrop-filter]:bg-background/80">
            <Button variant="outline" className="flex-1" onClick={closeEditor}>
              {t("agentManager.cancel")}
            </Button>
            <Button
              className="flex-1"
              onClick={handleSave}
              disabled={saving || !editingProfile.name.trim() || (isCreating && !isIdValid)}
            >
              {saving ? t("common.loading") : t("agentManager.save")}
            </Button>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
