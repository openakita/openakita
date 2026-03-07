// ─── SkillManager: 技能管理页面 ───
// 支持已安装技能列表、配置表单自动生成、启用/禁用、技能市场浏览与安装

import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useTranslation } from "react-i18next";
import type { SkillInfo, SkillConfigField, MarketplaceSkill, EnvMap } from "../types";
import { envGet, envSet } from "../utils";
import { IconGear, IconZap, IconPackage, IconStar, IconCheck, IconX, IconDownload, IconSearch, IconConfig, IconEdit } from "../icons";

// ─── 配置表单自动生成 ───

function SkillConfigForm({
  fields,
  envDraft,
  onEnvChange,
}: {
  fields: SkillConfigField[];
  envDraft: EnvMap;
  onEnvChange: (fn: (prev: EnvMap) => EnvMap) => void;
}) {
  const [secretShown, setSecretShown] = useState<Record<string, boolean>>({});
  const [localDraft, setLocalDraft] = useState<EnvMap>({});
  const { t } = useTranslation();

  const getValue = (key: string, fallback: string) =>
    key in localDraft ? localDraft[key] : envGet(envDraft, key, fallback);

  const handleChange = (key: string, value: string) => {
    setLocalDraft((prev) => ({ ...prev, [key]: value }));
  };

  const flushField = (key: string) => {
    if (key in localDraft) {
      const v = localDraft[key];
      onEnvChange((m) => envSet(m, key, v));
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "8px 0" }}>
      {fields.map((field) => {
        const value = getValue(field.key, String(field.default ?? ""));
        const isSecret = field.type === "secret";
        const shown = secretShown[field.key] ?? false;

        return (
          <div key={field.key} className="field">
            <div className="labelRow">
              <div className="label">
                {field.label}
                {field.required && <span style={{ color: "var(--danger)", marginLeft: 4 }}>*</span>}
              </div>
              {field.help && <div className="help">{field.help}</div>}
            </div>

            {field.type === "select" && field.options ? (
              <select
                value={value}
                onChange={(e) => {
                  handleChange(field.key, e.target.value);
                  onEnvChange((m) => envSet(m, field.key, e.target.value));
                }}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 10, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--text)", fontSize: 14 }}
              >
                {field.options.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            ) : field.type === "bool" ? (
              <label className="pill" style={{ cursor: "pointer", userSelect: "none", alignSelf: "flex-start" }}>
                <input
                  type="checkbox"
                  checked={value.toLowerCase() === "true"}
                  onChange={(e) => {
                    handleChange(field.key, String(e.target.checked));
                    onEnvChange((m) => envSet(m, field.key, String(e.target.checked)));
                  }}
                  style={{ width: 16, height: 16 }}
                />
                {field.label}
              </label>
            ) : field.type === "number" ? (
              <input
                type="number"
                value={value}
                min={field.min}
                max={field.max}
                onChange={(e) => handleChange(field.key, e.target.value)}
                onBlur={() => flushField(field.key)}
                placeholder={String(field.default ?? "")}
                style={{ width: "100%" }}
              />
            ) : (
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  type={isSecret && !shown ? "password" : "text"}
                  value={value}
                  onChange={(e) => handleChange(field.key, e.target.value)}
                  onBlur={() => flushField(field.key)}
                  placeholder={field.type === "secret" ? t("skills.secretPlaceholder") : String(field.default ?? "")}
                  style={{ flex: 1 }}
                />
                {isSecret && (
                  <button
                    type="button"
                    onClick={() => setSecretShown((s) => ({ ...s, [field.key]: !s[field.key] }))}
                    style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid var(--line)", background: "transparent", cursor: "pointer", fontSize: 12 }}
                  >
                    {shown ? t("skills.hide") : t("skills.show")}
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── 技能卡片 ───

function SkillCard({
  skill,
  expanded,
  onToggleExpand,
  onToggleEnabled,
  onViewDetail,
  envDraft,
  onEnvChange,
  onSaveConfig,
  saving,
}: {
  skill: SkillInfo;
  expanded: boolean;
  onToggleExpand: () => void;
  onToggleEnabled: () => void;
  onViewDetail: () => void;
  envDraft: EnvMap;
  onEnvChange: (fn: (prev: EnvMap) => EnvMap) => void;
  onSaveConfig: () => void;
  saving: boolean;
}) {
  const hasConfig = skill.config && skill.config.length > 0;
  const configComplete = skill.configComplete ?? true;
  const statusColor = skill.enabled === false
    ? "var(--muted)"
    : configComplete
      ? "rgba(16,185,129,1)"
      : "rgba(245,158,11,1)";
  const { t } = useTranslation();
  const statusText = skill.enabled === false
    ? t("skills.disabled")
    : configComplete
      ? t("skills.configComplete")
      : t("skills.configIncomplete");

  return (
    <div className="card" style={{ marginTop: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 36, height: 36, borderRadius: 10, background: skill.system ? "rgba(14,165,233,0.1)" : "rgba(124,58,237,0.1)", display: "grid", placeItems: "center", fontSize: 18, flexShrink: 0 }}>
          {skill.system ? <IconGear size={18} /> : <IconZap size={18} />}
        </div>
        <div style={{ flex: 1, minWidth: 0, cursor: "pointer" }} onClick={onViewDetail}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontWeight: 800, fontSize: 14 }}>{skill.name}</span>
            <span className="pill" style={{ fontSize: 11, borderColor: statusColor + "33" }}>
              <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 3, background: statusColor, marginRight: 4 }} />
              {statusText}
            </span>
            <span style={{ fontSize: 11, opacity: 0.5 }}>{skill.system ? t("skills.system") : t("skills.external")}</span>
          </div>
          <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {skill.description}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          <button
            onClick={onViewDetail}
            style={{ padding: "4px 10px", borderRadius: 8, border: "1px solid var(--line)", background: "transparent", cursor: "pointer", fontSize: 12, fontWeight: 700 }}
          >
            {t("skills.viewDetail")}
          </button>
          <label style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={skill.enabled !== false}
              onChange={onToggleEnabled}
              style={{ width: 16, height: 16 }}
            />
            {t("skills.enabled")}
          </label>
          {hasConfig && (
            <button
              onClick={onToggleExpand}
              style={{ padding: "4px 10px", borderRadius: 8, border: "1px solid var(--line)", background: expanded ? "rgba(14,165,233,0.08)" : "transparent", cursor: "pointer", fontSize: 12, fontWeight: 700 }}
            >
              {expanded ? t("chat.collapse") : t("skills.configure")}
            </button>
          )}
        </div>
      </div>

      {expanded && hasConfig && skill.config && (
        <div style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <SkillConfigForm fields={skill.config} envDraft={envDraft} onEnvChange={onEnvChange} />
          <button
            className="btnPrimary"
            onClick={onSaveConfig}
            disabled={saving}
            style={{ marginTop: 10, fontSize: 13, padding: "6px 20px" }}
          >
            {saving ? t("skills.saving") : t("skills.saveConfig")}
          </button>
        </div>
      )}
    </div>
  );
}

// ─── 技能详情弹窗 ───

function SkillDetailModal({
  skill,
  content,
  contentLoading,
  contentError,
  isEditing,
  editContent,
  savingContent,
  isSystem,
  serviceRunning,
  onClose,
  onStartEdit,
  onCancelEdit,
  onEditChange,
  onSave,
}: {
  skill: SkillInfo;
  content: string;
  contentLoading: boolean;
  contentError: string | null;
  isEditing: boolean;
  editContent: string;
  savingContent: boolean;
  isSystem: boolean;
  serviceRunning: boolean;
  onClose: () => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onEditChange: (v: string) => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isEditing]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !savingContent) onClose();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [onClose, savingContent]);

  return (
    <div className="modalOverlay" onClick={savingContent ? undefined : onClose}>
      <div
        className="modalContent"
        style={{ maxWidth: 720, width: "90vw", maxHeight: "85vh", display: "flex", flexDirection: "column", padding: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ padding: "18px 24px 14px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 32, height: 32, borderRadius: 8, background: isSystem ? "rgba(14,165,233,0.1)" : "rgba(124,58,237,0.1)", display: "grid", placeItems: "center", flexShrink: 0 }}>
              {isSystem ? <IconGear size={16} /> : <IconZap size={16} />}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 800, fontSize: 15 }}>{skill.name}</div>
              <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }}>{skill.description}</div>
            </div>
            <button
              onClick={onClose}
              disabled={savingContent}
              style={{ background: "transparent", border: "none", cursor: savingContent ? "not-allowed" : "pointer", padding: 4, fontSize: 18, opacity: savingContent ? 0.3 : 0.5, lineHeight: 1 }}
            >
              <IconX size={18} />
            </button>
          </div>

          {/* Meta info */}
          <div style={{ display: "flex", gap: 16, marginTop: 12, fontSize: 12, opacity: 0.6, flexWrap: "wrap" }}>
            <span><b>{t("skills.skillType")}:</b> {isSystem ? t("skills.system") : t("skills.external")}</span>
            {skill.category && <span><b>{t("skills.skillCategory")}:</b> {skill.category}</span>}
            {skill.path && (
              <span style={{ fontFamily: "monospace", fontSize: 11, opacity: 0.8, wordBreak: "break-all" }}>
                <b>{t("skills.filePath")}:</b> {skill.path}
              </span>
            )}
          </div>
        </div>

        {/* Content area */}
        <div style={{ flex: 1, overflow: "auto", padding: "16px 24px" }}>
          {contentLoading ? (
            <div style={{ textAlign: "center", padding: 40, opacity: 0.5 }}>{t("skills.loadingContent")}</div>
          ) : contentError ? (
            <div className="errorBox" style={{ margin: 0 }}>{contentError}</div>
          ) : isEditing ? (
            <textarea
              ref={textareaRef}
              value={editContent}
              onChange={(e) => onEditChange(e.target.value)}
              spellCheck={false}
              style={{
                width: "100%",
                minHeight: 400,
                fontFamily: "monospace",
                fontSize: 13,
                lineHeight: 1.6,
                padding: 12,
                border: "1px solid var(--brand)",
                borderRadius: 8,
                background: "var(--panel2)",
                color: "var(--text)",
                resize: "vertical",
                outline: "none",
                tabSize: 2,
              }}
            />
          ) : (
            <pre style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontFamily: "monospace",
              fontSize: 13,
              lineHeight: 1.6,
              margin: 0,
              padding: 12,
              background: "var(--panel2)",
              borderRadius: 8,
              border: "1px solid var(--line)",
              minHeight: 200,
              maxHeight: "none",
              overflow: "visible",
            }}>
              {content}
            </pre>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: "12px 24px 18px",
          borderTop: "1px solid var(--line)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexShrink: 0,
        }}>
          {isSystem && (
            <span style={{ fontSize: 12, opacity: 0.5, flex: 1 }}>{t("skills.readOnlyHint")}</span>
          )}
          {!isSystem && !serviceRunning && (
            <span style={{ fontSize: 12, opacity: 0.5, flex: 1 }}>{t("skills.requiresBackend")}</span>
          )}
          {!isSystem && serviceRunning && (
            <>
              <div style={{ flex: 1 }} />
              {isEditing ? (
                <>
                  <button
                    className="btnSmall"
                    onClick={onCancelEdit}
                    disabled={savingContent}
                    style={{ fontSize: 12, padding: "6px 16px" }}
                  >
                    {t("skills.cancelEdit")}
                  </button>
                  <button
                    className="btnPrimary"
                    onClick={onSave}
                    disabled={savingContent || editContent === content}
                    style={{ fontSize: 12, padding: "6px 16px" }}
                  >
                    {savingContent ? t("skills.saving") : t("skills.saveAndReload")}
                  </button>
                </>
              ) : (
                <button
                  onClick={onStartEdit}
                  disabled={contentLoading || !!contentError}
                  style={{ padding: "6px 16px", borderRadius: 8, border: "1px solid var(--line)", background: "transparent", cursor: "pointer", fontSize: 12, fontWeight: 700 }}
                >
                  <IconEdit size={12} /> {t("skills.editContent")}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── 市场技能卡片 ───

function MarketplaceSkillCard({
  skill,
  onInstall,
  installing,
}: {
  skill: MarketplaceSkill;
  onInstall: () => void;
  installing: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="card" style={{ marginTop: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 36, height: 36, borderRadius: 10, background: "rgba(124,58,237,0.08)", display: "grid", placeItems: "center", fontSize: 18, flexShrink: 0 }}>
          <IconPackage size={18} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontWeight: 800, fontSize: 14 }}>{skill.name}</span>
            {skill.installed && <span className="pill" style={{ fontSize: 11, borderColor: "rgba(16,185,129,0.25)" }}>{t("skills.installed")}</span>}
            {skill.installs != null && skill.installs > 0 && (
              <span style={{ fontSize: 11, opacity: 0.5, display: "inline-flex", alignItems: "center", gap: 4 }}>
                <IconDownload size={10} />{skill.installs.toLocaleString()}
              </span>
            )}
            {skill.stars != null && skill.stars > 0 && <span style={{ fontSize: 11, opacity: 0.5, display: "inline-flex", alignItems: "center", gap: 4 }}><IconStar size={11} />{skill.stars}</span>}
          </div>
          {skill.description && (
            <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{skill.description}</div>
          )}
          <div style={{ fontSize: 11, opacity: 0.4, marginTop: 2 }}>
            {skill.url && <span style={{ fontFamily: "monospace" }}>{skill.url}</span>}
          </div>
          {skill.tags && skill.tags.length > 0 && (
            <div style={{ display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap" }}>
              {skill.tags.map((tag) => (
                <span key={tag} style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(14,165,233,0.08)", color: "var(--brand)" }}>
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <button
          className={skill.installed ? "" : "btnPrimary"}
          onClick={onInstall}
          disabled={skill.installed || installing}
          style={{ fontSize: 12, padding: "6px 14px", flexShrink: 0 }}
        >
          {installing ? t("common.loading") : skill.installed ? t("skills.installed") : t("skills.install")}
        </button>
      </div>
    </div>
  );
}

// ─── 主组件 ───

export function SkillManager({
  venvDir,
  currentWorkspaceId,
  envDraft,
  onEnvChange,
  onSaveEnvKeys,
  apiBaseUrl = "http://127.0.0.1:18900",
  serviceRunning = false,
  dataMode = "local",
  onNotice,
}: {
  venvDir: string;
  currentWorkspaceId: string | null;
  envDraft: EnvMap;
  onEnvChange: (fn: (prev: EnvMap) => EnvMap) => void;
  onSaveEnvKeys: (keys: string[]) => Promise<void>;
  apiBaseUrl?: string;
  serviceRunning?: boolean;
  dataMode?: "local" | "remote";
  onNotice?: (msg: string) => void;
}) {
  const [tab, setTab] = useState<"installed" | "marketplace">("installed");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [marketplace, setMarketplace] = useState<MarketplaceSkill[]>([]);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketSearch, setMarketSearch] = useState("");
  const [installing, setInstalling] = useState<string | null>(null);
  const [manualUrl, setManualUrl] = useState("");
  const [manualInstalling, setManualInstalling] = useState(false);
  const [enabledDraft, setEnabledDraft] = useState<Record<string, boolean>>({});
  const [enabledDirty, setEnabledDirty] = useState(false);
  const [savingEnabled, setSavingEnabled] = useState(false);
  const [installedSearch, setInstalledSearch] = useState("");
  const [aiOrganizing, setAiOrganizing] = useState(false);
  const [detailSkill, setDetailSkill] = useState<SkillInfo | null>(null);
  const [detailContent, setDetailContent] = useState("");
  const [detailContentLoading, setDetailContentLoading] = useState(false);
  const [detailContentError, setDetailContentError] = useState<string | null>(null);
  const [detailEditing, setDetailEditing] = useState(false);
  const [detailEditContent, setDetailEditContent] = useState("");
  const [detailSaving, setDetailSaving] = useState(false);
  const marketRequestId = useRef(0);
  const { t } = useTranslation();

  // ── 加载已安装技能（返回 true 表示成功，false 表示出错） ──
  const loadSkills = useCallback(async (): Promise<boolean> => {
    setLoading(true);
    setError(null);
    try {
      let data: { skills: Record<string, unknown>[] } | null = null;

      let httpError: string | null = null;

      // 优先从运行中的服务 HTTP API 获取（远程模式或本地服务运行时）
      if (serviceRunning && apiBaseUrl) {
        try {
          const res = await fetch(`${apiBaseUrl}/api/skills`, { signal: AbortSignal.timeout(5000) });
          if (res.ok) {
            data = await res.json();
          } else {
            httpError = `HTTP ${res.status}`;
          }
        } catch (e) {
          httpError = String(e);
        }
      }

      // Fallback: Tauri 本地命令（仅本地模式，且 HTTP 未成功时）
      if (!data && dataMode !== "remote" && venvDir && currentWorkspaceId) {
        try {
          const raw = await invoke<string>("openakita_list_skills", { venvDir, workspaceId: currentWorkspaceId });
          data = JSON.parse(raw);
        } catch {
          // Tauri 也失败了——如果 HTTP 也失败了，显示错误
          if (httpError) {
            setError(`技能列表获取失败 (HTTP: ${httpError})`);
            return false;
          }
        }
      }

      if (!data) {
        setSkills([]);
        return !httpError;
      }

      const list: SkillInfo[] = (data.skills || []).map((s: Record<string, unknown>) => ({
        name: s.name as string,
        description: s.description as string || "",
        system: s.system as boolean || false,
        enabled: s.enabled as boolean | undefined,
        toolName: s.tool_name as string | null,
        category: s.category as string | null,
        path: s.path as string | null,
        sourceUrl: (s.source_url as string | null) || null,
        config: (s.config as SkillConfigField[] | null) || null,
        configComplete: true,  // 由 useMemo 动态计算，这里先占位
      }));
      setSkills(list);
      // 同步 enabledDraft 到后端最新状态
      const draft: Record<string, boolean> = {};
      for (const s of list) draft[s.name] = s.enabled !== false;
      setEnabledDraft(draft);
      setEnabledDirty(false);
      return true;
    } catch (e) {
      setError(String(e));
      return false;
    } finally {
      setLoading(false);
    }
  }, [venvDir, currentWorkspaceId, serviceRunning, apiBaseUrl, dataMode]);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  // ── 检查配置是否完整（纯函数，不依赖于状态） ──
  function checkConfigComplete(config: SkillConfigField[] | null | undefined, env: EnvMap): boolean {
    if (!config || config.length === 0) return true;
    return config.filter((f) => f.required).every((f) => {
      const v = env[f.key];
      return v != null && v.trim() !== "";
    });
  }

  // 动态计算每个技能的 configComplete 和 enabled 状态
  const skillsWithConfig = useMemo(() =>
    skills.map((s) => ({
      ...s,
      enabled: enabledDraft[s.name] ?? (s.enabled !== false),
      configComplete: checkConfigComplete(s.config, envDraft),
    })),
    [skills, envDraft, enabledDraft],
  );

  // 已安装技能搜索过滤
  const filteredSkills = useMemo(() => {
    const q = installedSearch.trim().toLowerCase();
    if (!q) return skillsWithConfig;
    return skillsWithConfig.filter((s) =>
      s.name.toLowerCase().includes(q) ||
      (s.description && s.description.toLowerCase().includes(q)) ||
      (s.category && s.category.toLowerCase().includes(q))
    );
  }, [skillsWithConfig, installedSearch]);

  // ── 保存技能配置 ──
  const handleSaveConfig = useCallback(async (skill: SkillInfo) => {
    if (!skill.config) return;
    setSaving(true);
    try {
      // 确保未手动修改但有默认值的字段也写入 envDraft，否则 saveEnvKeys 会跳过它们
      for (const f of skill.config) {
        if (f.default != null) {
          onEnvChange((m) => {
            if (Object.prototype.hasOwnProperty.call(m, f.key)) return m;  // 用户已修改过，不覆盖
            return envSet(m, f.key, String(f.default));
          });
        }
      }
      const keys = skill.config.map((f) => f.key);
      await onSaveEnvKeys(keys);
      // 刷新
      await loadSkills();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }, [onSaveEnvKeys, loadSkills, onEnvChange]);

  // ── 切换启用/禁用（仅更新本地 draft，不自动保存） ──
  const handleToggleEnabled = useCallback((skill: SkillInfo) => {
    const cur = enabledDraft[skill.name] ?? (skill.enabled !== false);
    setEnabledDraft((prev) => ({ ...prev, [skill.name]: !cur }));
    setEnabledDirty(true);
  }, [enabledDraft]);

  // ── 保存启用/禁用状态到后端 ──
  const handleSaveEnabledState = useCallback(async () => {
    setSavingEnabled(true);
    setError(null);
    try {
      const externalAllowlist = skills
        .filter((s) => !s.system && (enabledDraft[s.name] ?? (s.enabled !== false)))
        .map((s) => s.name);

      const content = {
        version: 1,
        external_allowlist: externalAllowlist,
        updated_at: new Date().toISOString(),
      };

      if (serviceRunning && apiBaseUrl) {
        const res = await fetch(`${apiBaseUrl}/api/config/skills`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
          signal: AbortSignal.timeout(5000),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        // 通知后端热重载
        try {
          await fetch(`${apiBaseUrl}/api/skills/reload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(10_000),
          });
        } catch { /* reload 失败不阻塞 */ }
      } else if (dataMode !== "remote" && currentWorkspaceId) {
        await invoke("workspace_write_file", {
          workspaceId: currentWorkspaceId,
          relativePath: "data/skills.json",
          content: JSON.stringify(content, null, 2) + "\n",
        });
      }

      setEnabledDirty(false);
      // 刷新技能列表确认后端状态
      await loadSkills();
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingEnabled(false);
    }
  }, [skills, enabledDraft, serviceRunning, apiBaseUrl, dataMode, currentWorkspaceId, loadSkills]);

  // ── AI 整理技能 ──
  const handleAiOrganize = useCallback(async () => {
    if (!serviceRunning || !apiBaseUrl) return;
    setAiOrganizing(true);
    setError(null);
    try {
      const skillSummary = skillsWithConfig
        .filter((s) => !s.system)
        .map((s) => `${s.name} [${s.enabled ? "启用" : "禁用"}]: ${s.description}`)
        .join("\n");

      const message = [
        "请帮我整理技能。以下是我当前安装的外部技能列表：",
        "",
        skillSummary,
        "",
        "请根据以下原则给出建议并使用 manage_skill_enabled 工具执行：",
        "1. 功能重复或相似的技能，只保留最好的一个",
        "2. 通用性强、常用的技能保持启用",
        "3. 非常小众或几乎不会用到的技能可以禁用",
        "4. 先列出你的分析和建议，征得我同意后再执行变更",
      ].join("\n");

      const res = await fetch(`${apiBaseUrl}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          conversation_id: `skill-organize-${Date.now()}`,
          depth: "deep",
        }),
        signal: AbortSignal.timeout(120_000),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      // SSE 流式消息只需触发，AI 的回复在聊天界面可见
      // 读完流确保不 abort
      const reader = res.body?.getReader();
      if (reader) {
        while (true) {
          const { done } = await reader.read();
          if (done) break;
        }
      }

      // 完成后刷新技能列表
      await loadSkills();
    } catch (e) {
      setError(String(e));
    } finally {
      setAiOrganizing(false);
    }
  }, [serviceRunning, apiBaseUrl, skillsWithConfig, loadSkills]);

  // ── 打开技能详情弹窗 ──
  const handleViewDetail = useCallback(async (skill: SkillInfo) => {
    setDetailSkill(skill);
    setDetailEditing(false);
    setDetailEditContent("");
    setDetailContentError(null);
    setDetailContent("");
    setDetailContentLoading(true);
    setDetailSaving(false);

    if (!serviceRunning || !apiBaseUrl) {
      setDetailContentError(t("skills.requiresBackend"));
      setDetailContentLoading(false);
      return;
    }

    try {
      const res = await fetch(`${apiBaseUrl}/api/skills/${encodeURIComponent(skill.name)}/content`, {
        signal: AbortSignal.timeout(10_000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) {
        setDetailContentError(data.error);
      } else {
        setDetailContent(data.content || "");
      }
    } catch (e) {
      setDetailContentError(String(e));
    } finally {
      setDetailContentLoading(false);
    }
  }, [serviceRunning, apiBaseUrl, t]);

  const handleCloseDetail = useCallback(() => {
    setDetailSkill(null);
    setDetailEditing(false);
    setDetailEditContent("");
    setDetailContentError(null);
  }, []);

  const handleSaveContent = useCallback(async () => {
    if (!detailSkill || !serviceRunning || !apiBaseUrl) return;
    setDetailSaving(true);
    setDetailContentError(null);
    try {
      const res = await fetch(`${apiBaseUrl}/api/skills/${encodeURIComponent(detailSkill.name)}/content`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: detailEditContent }),
        signal: AbortSignal.timeout(15_000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) {
        setDetailContentError(data.error);
      } else {
        setDetailContent(detailEditContent);
        setDetailEditing(false);
        onNotice?.(t("skills.contentSaved"));
        await loadSkills();
      }
    } catch (e) {
      setDetailContentError(`${t("skills.contentSaveFailed")}: ${e}`);
    } finally {
      setDetailSaving(false);
    }
  }, [detailSkill, detailEditContent, serviceRunning, apiBaseUrl, loadSkills, onNotice, t]);

  // ── 搜索 skills.sh 市场技能 ──
  const parseMarketplaceResponse = useCallback((data: Record<string, unknown>) => {
    const items: MarketplaceSkill[] = ((data.skills || []) as Record<string, unknown>[]).map((s) => {
      const source = String(s.source || "");
      const skillId = String(s.skillId || s.name || "");
      const installUrl = source ? `${source}@${skillId}` : skillId;
      return {
        id: String(s.id || ""),
        skillId,
        name: String(s.name || ""),
        description: "",  // skills.sh API doesn't return description
        author: source.split("/")[0] || "unknown",
        url: installUrl,
        installs: typeof s.installs === "number" ? s.installs : undefined,
        tags: [],
        installed: skills.some((local) => {
          const nameMatch = local.name === skillId;
          const dirMatch = local.path ? local.path.replace(/\\/g, "/").split("/").pop() === skillId : false;
          if (!nameMatch && !dirMatch) return false;
          if (local.sourceUrl && installUrl) return local.sourceUrl === installUrl;
          return true;
        }),
      };
    });
    return items;
  }, [skills]);

  const searchMarketplace = useCallback(async (query: string) => {
    const reqId = ++marketRequestId.current;
    setMarketLoading(true);
    setError(null);
    try {
      const q = query.trim() || "agent";  // 默认搜索 "agent" 展示热门技能
      const url = `https://skills.sh/api/search?q=${encodeURIComponent(q)}`;
      let data: Record<string, unknown> | null = null;

      if (dataMode === "remote") {
        // 远程模式：只走后端 API 代理（Tauri 不可用）
        if (serviceRunning && apiBaseUrl) {
          try {
            const res = await fetch(`${apiBaseUrl}/api/skills/marketplace?q=${encodeURIComponent(q)}`, {
              signal: AbortSignal.timeout(10000),
            });
            if (res.ok) data = await res.json();
          } catch { /* fallback to direct */ }
        }
        // 备选：直接请求（可能被 CORS 阻止）
        if (!data) {
          const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
          if (!res.ok) throw new Error(`skills.sh returned ${res.status}`);
          data = await res.json();
        }
      } else {
        // 本地模式：方式1 Tauri invoke 代理（绕过 CORS）
        try {
          const raw = await invoke<string>("http_get_json", { url });
          data = JSON.parse(raw);
        } catch { /* Tauri 不可用或命令不存在，继续 fallback */ }

        // 方式2: 通过后端 API 代理
        if (!data && serviceRunning && apiBaseUrl) {
          try {
            const res = await fetch(`${apiBaseUrl}/api/skills/marketplace?q=${encodeURIComponent(q)}`, {
              signal: AbortSignal.timeout(10000),
            });
            if (res.ok) data = await res.json();
          } catch { /* fallback */ }
        }

        // 方式3: 直接请求
        if (!data) {
          const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
          if (!res.ok) throw new Error(`skills.sh returned ${res.status}`);
          data = await res.json();
        }
      }

      // 如果已有更新的请求在飞行中，丢弃此次结果
      if (reqId !== marketRequestId.current) return;
      setMarketplace(parseMarketplaceResponse(data!));
    } catch (e) {
      if (reqId !== marketRequestId.current) return;
      // 失败时不清空已有数据，只在没有任何数据时显示错误
      setError(`${t("skills.marketplace")}: ${String(e)}`);
    } finally {
      if (reqId === marketRequestId.current) {
        setMarketLoading(false);
      }
    }
  }, [skills, t, serviceRunning, apiBaseUrl, dataMode, parseMarketplaceResponse]);  // eslint-disable-line react-hooks/exhaustive-deps

  // 统一的市场搜索 effect：切换 tab 或搜索词变化时触发
  useEffect(() => {
    if (tab !== "marketplace") return;
    // 切换到市场标签时立即加载，搜索时 debounce 400ms
    const delay = marketSearch.trim() ? 400 : 50;
    const timer = setTimeout(() => {
      searchMarketplace(marketSearch);
    }, delay);
    return () => clearTimeout(timer);
  }, [marketSearch, tab]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ── 安装技能 ──
  const handleInstall = useCallback(async (skill: MarketplaceSkill) => {
    if (dataMode !== "remote" && !serviceRunning && (!venvDir || !currentWorkspaceId)) {
      setError("环境未就绪：请先完成 Python 环境和工作区配置");
      return;
    }
    setInstalling(skill.name);
    setError(null);
    try {
      let installed = false;

      // 方式1：服务运行中 → HTTP API 安装（首选，不回退 Tauri 避免 venv 缺失报错）
      if (serviceRunning && apiBaseUrl) {
        const res = await fetch(`${apiBaseUrl}/api/skills/install`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: skill.url }),
          signal: AbortSignal.timeout(60_000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        installed = true;
        // 安装成功后通知后端热重载技能
        try {
          await fetch(`${apiBaseUrl}/api/skills/reload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(10_000),
          });
        } catch { /* reload 失败不阻塞，技能下次重启时自动加载 */ }
      }

      // 方式2：服务未运行 → Tauri invoke（本地模式）
      if (!installed && dataMode !== "remote" && currentWorkspaceId) {
        await invoke<string>("openakita_install_skill", {
          venvDir,
          workspaceId: currentWorkspaceId,
          url: skill.url,
        });
      }

      setMarketplace((prev) => prev.map((s) =>
        s.url === skill.url ? { ...s, installed: true } : s
      ));
      await loadSkills();
      setTab("installed");
      setExpandedSkill(skill.name);
    } catch (e) {
      const msg = String(e);
      if (msg.includes("已存在") || msg.toLowerCase().includes("already exist")) {
        setMarketplace((prev) => prev.map((s) =>
          s.url === skill.url ? { ...s, installed: true } : s
        ));
        await loadSkills();
        onNotice?.(t("skills.alreadyInstalled"));
        setTab("installed");
      } else {
        setError(msg);
      }
    } finally {
      setInstalling(null);
    }
  }, [loadSkills, venvDir, currentWorkspaceId, dataMode, serviceRunning, apiBaseUrl, onNotice, t]);

  // ── 手动输入链接安装技能 ──
  const handleManualInstall = useCallback(async () => {
    const url = manualUrl.trim();
    if (!url) return;
    if (dataMode !== "remote" && !serviceRunning && (!venvDir || !currentWorkspaceId)) {
      setError(t("skills.envNotReady") || "环境未就绪：请先完成 Python 环境和工作区配置");
      return;
    }
    setManualInstalling(true);
    setError(null);
    try {
      let installed = false;

      if (serviceRunning && apiBaseUrl) {
        const res = await fetch(`${apiBaseUrl}/api/skills/install`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
          signal: AbortSignal.timeout(120_000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        installed = true;
        try {
          await fetch(`${apiBaseUrl}/api/skills/reload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(10_000),
          });
        } catch { /* reload 失败不阻塞 */ }
      }

      if (!installed && dataMode !== "remote" && currentWorkspaceId) {
        await invoke<string>("openakita_install_skill", {
          venvDir,
          workspaceId: currentWorkspaceId,
          url,
        });
      }

      setManualUrl("");
      await loadSkills();
      setTab("installed");
    } catch (e) {
      const msg = String(e);
      if (msg.includes("已存在") || msg.toLowerCase().includes("already exist")) {
        setManualUrl("");
        await loadSkills();
        onNotice?.(t("skills.alreadyInstalled"));
        setTab("installed");
      } else {
        setError(msg);
      }
    } finally {
      setManualInstalling(false);
    }
  }, [manualUrl, loadSkills, venvDir, currentWorkspaceId, dataMode, serviceRunning, apiBaseUrl, t, onNotice]);

  // Debounced search: trigger API call when user stops typing
  useEffect(() => {
    if (tab !== "marketplace") return;
    const timer = setTimeout(() => {
      searchMarketplace(marketSearch);
    }, 400);
    return () => clearTimeout(timer);
  }, [marketSearch, tab]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {/* Tab 切换 */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <button
          className={tab === "installed" ? "btnPrimary" : ""}
          onClick={() => setTab("installed")}
          style={{ fontSize: 13, padding: "6px 20px" }}
        >
          {t("skills.installed")} ({skillsWithConfig.length})
        </button>
        <button
          className={tab === "marketplace" ? "btnPrimary" : ""}
          onClick={() => setTab("marketplace")}
          style={{ fontSize: 13, padding: "6px 20px" }}
        >
          {t("skills.marketplace")}
        </button>
        <div style={{ flex: 1 }} />
        <button
          onClick={async () => {
            setError(null);
            if (serviceRunning) {
              try {
                const res = await fetch(`${apiBaseUrl}/api/skills/reload`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({}),
                  signal: AbortSignal.timeout(15_000),
                });
                const data = await res.json();
                if (data.error) { setError(data.error); return; }
              } catch (e) { setError(String(e)); return; }
            }
            const ok = await loadSkills();
            if (ok) onNotice?.(t("skills.refreshed"));
          }}
          disabled={loading}
          style={{ fontSize: 12, padding: "6px 14px", borderRadius: 8, border: "1px solid var(--line)", cursor: "pointer" }}
          title={t("skills.reloadHint")}
        >
          {loading ? t("common.loading") : t("topbar.refresh")}
        </button>
      </div>

      {error && <div className="errorBox" style={{ marginBottom: 12 }}>{error}</div>}

      {/* 已安装技能 */}
      {tab === "installed" && (
        <div style={{ display: "grid", gap: 10 }}>
          {/* 搜索 + AI 整理 */}
          {skillsWithConfig.length > 0 && (
            <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
              <div style={{ flex: 1, position: "relative" }}>
                <IconSearch size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", opacity: 0.4, pointerEvents: "none" }} />
                <input
                  value={installedSearch}
                  onChange={(e) => setInstalledSearch(e.target.value)}
                  placeholder={t("skills.filterPlaceholder")}
                  style={{ width: "100%", fontSize: 13, paddingLeft: 32 }}
                />
              </div>
              {serviceRunning && (
                <button
                  onClick={handleAiOrganize}
                  disabled={aiOrganizing}
                  style={{ fontSize: 12, padding: "0 14px", borderRadius: 10, border: "1px solid var(--line)", cursor: "pointer", whiteSpace: "nowrap" }}
                  title={t("skills.aiOrganizeHint")}
                >
                  {aiOrganizing ? t("common.loading") : t("skills.aiOrganize")}
                </button>
              )}
            </div>
          )}

          {loading && skillsWithConfig.length === 0 && <div className="cardHint">{t("skills.loading")}</div>}
          {!loading && skillsWithConfig.length === 0 && (
            <div className="card" style={{ textAlign: "center", padding: "30px 20px" }}>
              <div style={{ marginBottom: 8, display: "flex", justifyContent: "center" }}><IconZap size={36} /></div>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>{t("skills.noSkills")}</div>
              <div className="help">{t("skills.noSkillsHint")}</div>
            </div>
          )}
          {installedSearch && filteredSkills.length === 0 && skillsWithConfig.length > 0 && (
            <div className="cardHint" style={{ textAlign: "center", padding: 16 }}>
              {t("skills.noResults")}
            </div>
          )}
          {filteredSkills.map((skill) => (
            <SkillCard
              key={skill.name}
              skill={skill}
              expanded={expandedSkill === skill.name}
              onToggleExpand={() => setExpandedSkill(expandedSkill === skill.name ? null : skill.name)}
              onToggleEnabled={() => handleToggleEnabled(skill)}
              onViewDetail={() => handleViewDetail(skill)}
              envDraft={envDraft}
              onEnvChange={onEnvChange}
              onSaveConfig={() => handleSaveConfig(skill)}
              saving={saving}
            />
          ))}

          {/* 保存启用/禁用状态 */}
          {enabledDirty && (
            <div style={{
              position: "sticky",
              bottom: 0,
              padding: "12px 0",
              background: "var(--bg)",
              borderTop: "1px solid var(--line)",
              display: "flex",
              alignItems: "center",
              gap: 12,
              zIndex: 10,
            }}>
              <span style={{ fontSize: 12, opacity: 0.6, flex: 1 }}>
                {t("skills.unsavedChanges")}
              </span>
              <button
                onClick={() => { loadSkills(); }}
                disabled={savingEnabled}
                style={{ fontSize: 12, padding: "6px 16px", borderRadius: 8, border: "1px solid var(--line)", cursor: "pointer" }}
              >
                {t("skills.discardChanges")}
              </button>
              <button
                className="btnPrimary"
                onClick={handleSaveEnabledState}
                disabled={savingEnabled}
                style={{ fontSize: 13, padding: "6px 20px" }}
              >
                {savingEnabled ? t("skills.saving") : t("skills.saveEnabledState")}
              </button>
            </div>
          )}
        </div>
      )}

      {/* 技能市场 */}
      {tab === "marketplace" && (
        <>
          {/* 安全提示 */}
          <div style={{
            marginBottom: 12,
            padding: "10px 14px",
            borderRadius: 10,
            background: "rgba(245,158,11,0.08)",
            border: "1px solid rgba(245,158,11,0.2)",
            fontSize: 12,
            lineHeight: 1.6,
            color: "var(--text)",
          }}>
            <span style={{ fontWeight: 700 }}>&#9888; {t("skills.securityTitle")}</span>
            <span style={{ opacity: 0.75 }}>&ensp;{t("skills.securityWarning")}</span>
          </div>

          {/* 手动输入链接安装 */}
          <div className="card" style={{ marginBottom: 12, padding: "12px 14px" }}>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8, opacity: 0.7 }}>
              {t("skills.manualInstallTitle")}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                value={manualUrl}
                onChange={(e) => setManualUrl(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && manualUrl.trim()) handleManualInstall(); }}
                placeholder={t("skills.manualInstallPlaceholder")}
                disabled={manualInstalling}
                style={{ flex: 1, fontSize: 13 }}
              />
              <button
                className="btnPrimary"
                onClick={handleManualInstall}
                disabled={!manualUrl.trim() || manualInstalling}
                style={{ fontSize: 12, padding: "6px 16px", flexShrink: 0 }}
              >
                {manualInstalling ? t("common.loading") : t("skills.install")}
              </button>
            </div>
            <div style={{ fontSize: 11, opacity: 0.4, marginTop: 6 }}>
              {t("skills.manualInstallHint")}
            </div>
          </div>

          <div style={{ marginBottom: 12, position: "relative" }}>
            <IconSearch size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", opacity: 0.4, pointerEvents: "none" }} />
            <input
              value={marketSearch}
              onChange={(e) => setMarketSearch(e.target.value)}
              placeholder={t("skills.searchPlaceholder")}
              style={{ width: "100%", fontSize: 14, paddingLeft: 32 }}
            />
          </div>
          <div style={{ display: "grid", gap: 10 }}>
            {marketLoading && <div className="cardHint">{t("common.loading")}</div>}
            {!marketLoading && marketplace.map((skill) => (
              <MarketplaceSkillCard
                key={skill.id || skill.name}
                skill={skill}
                onInstall={() => handleInstall(skill)}
                installing={installing === skill.name}
              />
            ))}
            {!marketLoading && marketplace.length === 0 && (
              <div className="cardHint" style={{ textAlign: "center", padding: 20 }}>
                {marketSearch ? t("skills.noResults") : t("skills.noSkills")}
              </div>
            )}
          </div>
          <div style={{ textAlign: "center", fontSize: 11, opacity: 0.4, marginTop: 16 }}>
            {t("skills.poweredBy")} &middot;{" "}
            <a href="https://skills.sh" target="_blank" rel="noreferrer" style={{ color: "var(--brand)", textDecoration: "none" }}>
              skills.sh
            </a>
          </div>
        </>
      )}

      {/* 技能详情弹窗 */}
      {detailSkill && (
        <SkillDetailModal
          skill={detailSkill}
          content={detailContent}
          contentLoading={detailContentLoading}
          contentError={detailContentError}
          isEditing={detailEditing}
          editContent={detailEditContent}
          savingContent={detailSaving}
          isSystem={detailSkill.system}
          serviceRunning={serviceRunning}
          onClose={handleCloseDetail}
          onStartEdit={() => { setDetailEditing(true); setDetailEditContent(detailContent); }}
          onCancelEdit={() => { setDetailEditing(false); setDetailEditContent(""); }}
          onEditChange={setDetailEditContent}
          onSave={handleSaveContent}
        />
      )}
    </>
  );
}
