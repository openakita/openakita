// ─── SkillManager: 技能管理页面 ───
// 支持已安装技能列表、配置表单自动生成、启用/禁用、技能市场浏览与安装

import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { invoke, IS_TAURI } from "../platform";
import { useTranslation } from "react-i18next";
import { useMdModules } from "../hooks/useMdModules";
import type { SkillInfo, SkillConfigField, MarketplaceSkill, EnvMap } from "../types";
import { envGet, envSet } from "../utils";
import { IconGear, IconZap, IconPackage, IconStar, IconCheck, IconX, IconDownload, IconSearch, IconConfig, IconFolderOpen, IconEdit, IconTrash, IconEye } from "../icons";
import { Loader2 } from "lucide-react";
import { safeFetch } from "../providers";
import { toast } from "sonner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ModalOverlay } from "../components/ModalOverlay";

// ─── i18n 辅助：按当前语言优先显示中文名/描述 ───

function getSkillDisplayName(skill: SkillInfo, lang: string): string {
  const key = lang.startsWith("zh") ? "zh" : lang;
  return skill.name_i18n?.[key] || skill.name;
}

function getSkillDisplayDesc(skill: SkillInfo, lang: string): string {
  const key = lang.startsWith("zh") ? "zh" : lang;
  return skill.description_i18n?.[key] || skill.description;
}

// ─── 错误消息友好化 ───

type ErrorContext = "load" | "save" | "install" | "uninstall" | "reload" | "general";

function friendlyError(e: unknown, t: (key: string) => string, context: ErrorContext = "general"): string {
  const raw = e instanceof Error ? e.message : String(e);

  if (/AbortError|signal timed out|timeout/i.test(raw)) {
    return t("skills.errorTimeout");
  }
  if (/Failed to fetch|NetworkError|ECONNREFUSED|net::|ERR_CONNECTION|Load failed/i.test(raw)) {
    return t("skills.errorNetwork");
  }
  if (/\b50[0-9]\b|Internal Server Error/i.test(raw)) {
    return t("skills.errorServer");
  }

  const contextMap: Record<ErrorContext, string> = {
    load: "skills.errorLoadFailed",
    save: "skills.errorSaveFailed",
    install: "skills.errorInstallFailed",
    uninstall: "skills.errorUninstallFailed",
    reload: "skills.errorReloadFailed",
    general: "skills.errorUnknown",
  };
  return t(contextMap[context]);
}

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
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setSecretShown((s) => ({ ...s, [field.key]: !s[field.key] }))}
                  >
                    {shown ? t("skills.hide") : t("skills.show")}
                  </Button>
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
  onUninstall,
  uninstalling,
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
  onUninstall?: () => void;
  uninstalling?: boolean;
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
  const { t, i18n } = useTranslation();
  const lang = i18n.language || "zh";
  const displayName = getSkillDisplayName(skill, lang);
  const displayDesc = getSkillDisplayDesc(skill, lang);
  const statusText = skill.enabled === false
    ? t("skills.disabled")
    : configComplete
      ? t("skills.configComplete")
      : t("skills.configIncomplete");

  return (
    <div className="card" style={{ marginTop: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 36, height: 36, borderRadius: 10, background: skill.system ? "rgba(37,99,235,0.1)" : "rgba(124,58,237,0.1)", display: "grid", placeItems: "center", fontSize: 18, flexShrink: 0 }}>
          {skill.system ? <IconGear size={18} /> : <IconZap size={18} />}
        </div>
        <div style={{ flex: 1, minWidth: 0, cursor: "pointer" }} onClick={onViewDetail}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 800, fontSize: 14 }}>{displayName}</span>
            {displayName !== skill.name && (
              <span style={{ fontSize: 11, opacity: 0.4, fontFamily: "monospace" }}>{skill.name}</span>
            )}
            {!skill.system && skill.sourceUrl && (() => {
              const src = skill.sourceUrl!;
              const ownerRepo = src.includes("@") ? src.split("@")[0] : src.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "");
              return ownerRepo ? (
                <span style={{ fontSize: 11, opacity: 0.45, fontFamily: "monospace" }}>{ownerRepo}</span>
              ) : null;
            })()}
            <span className="pill" style={{ fontSize: 11, borderColor: statusColor + "33" }}>
              <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 3, background: statusColor, marginRight: 4 }} />
              {statusText}
            </span>
            <span style={{ fontSize: 11, opacity: 0.5 }}>{skill.system ? t("skills.system") : t("skills.external")}</span>
          </div>
          <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {displayDesc}
          </div>
        </div>
        <div style={{ display: "flex", gap: 2, flexShrink: 0, alignItems: "center" }}>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onViewDetail}
            title={t("skills.viewDetail")}
            className="text-muted-foreground hover:text-foreground"
          >
            <IconEye size={14} />
          </Button>
          {!skill.system && onUninstall && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onUninstall}
              disabled={uninstalling}
              title={t("skills.uninstall")}
              className="text-muted-foreground hover:text-destructive"
            >
              {uninstalling ? <Loader2 className="animate-spin" size={14} /> : <IconTrash size={14} />}
            </Button>
          )}
          <Label className="flex items-center gap-1.5 cursor-pointer text-xs font-normal ml-1">
            <Checkbox
              checked={skill.enabled !== false}
              onCheckedChange={() => onToggleEnabled()}
            />
            {t("skills.enabled")}
          </Label>
          {hasConfig && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onToggleExpand}
              className={expanded ? "bg-accent" : ""}
            >
              {expanded ? t("chat.collapse") : t("skills.configure")}
            </Button>
          )}
        </div>
      </div>

      {expanded && hasConfig && skill.config && (
        <div style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <SkillConfigForm fields={skill.config} envDraft={envDraft} onEnvChange={onEnvChange} />
          <Button
            onClick={onSaveConfig}
            disabled={saving}
            size="sm"
            className="mt-2.5"
          >
            {saving && <Loader2 className="animate-spin" />}
            {t("skills.saveConfig")}
          </Button>
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
  onUninstall,
  uninstalling,
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
  onUninstall?: () => void;
  uninstalling?: boolean;
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
    <ModalOverlay onClose={savingContent ? () => {} : onClose}>
      <div
        className="modalContent"
        style={{ maxWidth: 720, width: "90vw", maxHeight: "85vh", display: "flex", flexDirection: "column", padding: 0 }}
      >
        {/* Header */}
        <div style={{ padding: "18px 24px 14px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 32, height: 32, borderRadius: 8, background: isSystem ? "rgba(37,99,235,0.1)" : "rgba(124,58,237,0.1)", display: "grid", placeItems: "center", flexShrink: 0 }}>
              {isSystem ? <IconGear size={16} /> : <IconZap size={16} />}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 800, fontSize: 15 }}>{skill.name}</div>
              <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }}>{skill.description}</div>
            </div>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={onClose}
              disabled={savingContent}
            >
              <IconX size={18} />
            </Button>
          </div>

          {/* Meta info */}
          <div style={{ display: "flex", gap: 16, marginTop: 12, fontSize: 12, opacity: 0.6, flexWrap: "wrap" }}>
            <span><b>{t("skills.skillType")}:</b> {isSystem ? t("skills.system") : t("skills.external")}</span>
            {skill.category && <span><b>{t("skills.skillCategory")}:</b> {skill.category}</span>}
            {!isSystem && skill.sourceUrl && (
              <span style={{ fontFamily: "monospace", fontSize: 11, opacity: 0.8 }}>
                <b>{t("skills.source")}:</b> {skill.sourceUrl}
              </span>
            )}
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
              {onUninstall && !isEditing && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onUninstall}
                  disabled={uninstalling || savingContent}
                  className="text-destructive border-destructive/30 hover:bg-destructive/10 hover:text-destructive"
                >
                  {uninstalling ? <Loader2 className="animate-spin" size={12} /> : <IconTrash size={12} />} {t("skills.uninstall")}
                </Button>
              )}
              <div style={{ flex: 1 }} />
              {isEditing ? (
                <>
                  <Button variant="outline" size="sm" onClick={onCancelEdit} disabled={savingContent}>
                    {t("skills.cancelEdit")}
                  </Button>
                  <Button size="sm" onClick={onSave} disabled={savingContent || editContent === content}>
                    {savingContent && <Loader2 className="animate-spin" />}
                    {t("skills.saveAndReload")}
                  </Button>
                </>
              ) : (
                <Button variant="outline" size="sm" onClick={onStartEdit} disabled={contentLoading || !!contentError}>
                  <IconEdit size={12} /> {t("skills.editContent")}
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
}

// ─── 市场技能卡片 ───

function MarketplaceSkillCard({
  skill,
  onInstall,
  installing,
  onClick,
}: {
  skill: MarketplaceSkill;
  onInstall: () => void;
  installing: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="card"
      style={{ marginTop: 0, cursor: "pointer", transition: "box-shadow 0.15s" }}
      onClick={onClick}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = "0 2px 12px rgba(0,0,0,0.08)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = ""; }}
    >
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
                <span key={tag} style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(37,99,235,0.08)", color: "var(--brand)" }}>
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <Button
          variant={skill.installed ? "outline" : "default"}
          size="sm"
          onClick={(e) => { e.stopPropagation(); onInstall(); }}
          disabled={skill.installed || installing}
        >
          {installing && <Loader2 className="animate-spin" />}
          {skill.installed ? t("skills.installed") : t("skills.install")}
        </Button>
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
}: {
  venvDir: string;
  currentWorkspaceId: string | null;
  envDraft: EnvMap;
  onEnvChange: (fn: (prev: EnvMap) => EnvMap) => void;
  onSaveEnvKeys: (keys: string[]) => Promise<void>;
  apiBaseUrl?: string;
  serviceRunning?: boolean;
  dataMode?: "local" | "remote";
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
  const [installingSet, setInstallingSet] = useState<Set<string>>(new Set());
  const [manualUrl, setManualUrl] = useState("");
  const [manualInstalling, setManualInstalling] = useState(false);
  const [marketDetailSkill, setMarketDetailSkill] = useState<MarketplaceSkill | null>(null);
  const [marketDetailContent, setMarketDetailContent] = useState<{ frontmatter: Record<string, string>; body: string; empty?: boolean } | null>(null);
  const [marketDetailLoading, setMarketDetailLoading] = useState(false);
  const [marketDetailError, setMarketDetailError] = useState<"timeout" | "network" | "server" | null>(null);
  const marketDetailReqRef = useRef(0);
  const [enabledDraft, setEnabledDraft] = useState<Record<string, boolean>>({});
  const [enabledDirty, setEnabledDirty] = useState(false);
  const [savingEnabled, setSavingEnabled] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  
  const [installedSearch, setInstalledSearch] = useState("");
  const [aiOrganizing, setAiOrganizing] = useState(false);
  const [localImporting, setLocalImporting] = useState(false);
  const [detailSkill, setDetailSkill] = useState<SkillInfo | null>(null);
  const [detailContent, setDetailContent] = useState("");
  const [detailContentLoading, setDetailContentLoading] = useState(false);
  const [detailContentError, setDetailContentError] = useState<string | null>(null);
  const [detailEditing, setDetailEditing] = useState(false);
  const [detailEditContent, setDetailEditContent] = useState("");
  const [detailSaving, setDetailSaving] = useState(false);
  const [uninstallingSet, setUninstallingSet] = useState<Set<string>>(new Set());
  const [uninstallConfirm, setUninstallConfirm] = useState<SkillInfo | null>(null);
  const marketRequestId = useRef(0);
  const detailRequestNameRef = useRef<string | null>(null);
  const { t } = useTranslation();
  const mdModules = useMdModules();

  // ── 加载已安装技能（返回 true 表示成功，false 表示出错） ──
  const loadSkills = useCallback(async (): Promise<boolean> => {
    setLoading(true);
    setError(null);
    try {
      let data: { skills: Record<string, unknown>[] } | null = null;

      let httpError: string | null = null;

      // 优先从运行中的服务 HTTP API 获取（远程模式或本地服务运行时）
      if (serviceRunning && apiBaseUrl != null) {
        try {
          const res = await safeFetch(`${apiBaseUrl}/api/skills`, { signal: AbortSignal.timeout(15_000) });
          data = await res.json();
        } catch (e) {
          httpError = String(e);
        }
      }

      // Fallback: Tauri 本地命令（仅本地模式，且 HTTP 未成功时）
      if (!data && IS_TAURI && dataMode !== "remote" && venvDir && currentWorkspaceId) {
        try {
          const raw = await invoke<string>("openakita_list_skills", { venvDir, workspaceId: currentWorkspaceId });
          data = JSON.parse(raw);
        } catch {
          // Tauri 也失败了——如果 HTTP 也失败了，显示错误
          if (httpError) {
            setError(friendlyError(httpError, t, "load"));
            return false;
          }
        }
      }

      if (!data) {
        setSkills([]);
        return !httpError;
      }

      const list: SkillInfo[] = (data.skills || []).map((s: Record<string, unknown>) => ({
        skillId: (s.skill_id as string) || (s.name as string),
        name: s.name as string,
        description: s.description as string || "",
        name_i18n: (s.name_i18n as Record<string, string> | null) || null,
        description_i18n: (s.description_i18n as Record<string, string> | null) || null,
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
      for (const s of list) draft[s.skillId] = s.enabled !== false;
      setEnabledDraft(draft);
      setEnabledDirty(false);
      return true;
    } catch (e) {
      setError(friendlyError(e, t, "load"));
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
      enabled: enabledDraft[s.skillId] ?? (s.enabled !== false),
      configComplete: checkConfigComplete(s.config, envDraft),
    })),
    [skills, envDraft, enabledDraft],
  );

  // 已安装技能搜索过滤（同时匹配原始字段、i18n 字段、目录名和安装来源）
  const filteredSkills = useMemo(() => {
    const q = installedSearch.trim().toLowerCase();
    if (!q) return skillsWithConfig;
    return skillsWithConfig.filter((s) => {
      if (s.name.toLowerCase().includes(q)) return true;
      if (s.description && s.description.toLowerCase().includes(q)) return true;
      if (s.category && s.category.toLowerCase().includes(q)) return true;
      if (s.path) {
        const parts = s.path.replace(/\\/g, "/").split("/");
        const dirName = parts.length >= 2 ? parts[parts.length - 2] : "";
        if (dirName.toLowerCase().includes(q)) return true;
      }
      if (s.sourceUrl && s.sourceUrl.toLowerCase().includes(q)) return true;
      const i18nValues = [
        ...Object.values(s.name_i18n || {}),
        ...Object.values(s.description_i18n || {}),
      ];
      return i18nValues.some((v) => v.toLowerCase().includes(q));
    });
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
      setError(friendlyError(e, t, "save"));
    } finally {
      setSaving(false);
    }
  }, [onSaveEnvKeys, loadSkills, onEnvChange]);

  // ── 切换启用/禁用（仅更新本地 draft，不自动保存） ──
  const handleToggleEnabled = useCallback((skill: SkillInfo) => {
    const cur = enabledDraft[skill.skillId] ?? (skill.enabled !== false);
    setEnabledDraft((prev) => ({ ...prev, [skill.skillId]: !cur }));
    setEnabledDirty(true);
  }, [enabledDraft]);

  // ── 保存启用/禁用状态到后端 ──
  const handleSaveEnabledState = useCallback(async () => {
    setSavingEnabled(true);
    setError(null);
    try {
      const externalAllowlist = skills
        .filter((s) => !s.system && (enabledDraft[s.skillId] ?? (s.enabled !== false)))
        .map((s) => s.skillId);

      const content = {
        version: 1,
        external_allowlist: externalAllowlist,
        updated_at: new Date().toISOString(),
      };

      if (serviceRunning && apiBaseUrl != null) {
        const res = await safeFetch(`${apiBaseUrl}/api/config/skills`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
          signal: AbortSignal.timeout(5000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        // 通知后端热重载
        try {
          await safeFetch(`${apiBaseUrl}/api/skills/reload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(10_000),
          });
        } catch { /* reload 失败不阻塞 */ }
      } else if (IS_TAURI && dataMode !== "remote" && currentWorkspaceId) {
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
      setError(friendlyError(e, t, "save"));
    } finally {
      setSavingEnabled(false);
    }
  }, [skills, enabledDraft, serviceRunning, apiBaseUrl, dataMode, currentWorkspaceId, loadSkills]);

  const handleDiscard = useCallback(() => { loadSkills(); }, [loadSkills]);

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

      const res = await safeFetch(`${apiBaseUrl}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          conversation_id: `skill-organize-${Date.now()}`,
          depth: "deep",
        }),
        signal: AbortSignal.timeout(120_000),
      });

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
      setError(friendlyError(e, t));
    } finally {
      setAiOrganizing(false);
    }
  }, [serviceRunning, apiBaseUrl, skillsWithConfig, loadSkills]);

  // ── 导入本地技能 ──
  const handleImportLocal = useCallback(async () => {
    setLocalImporting(true);
    setError(null);
    try {
      const { openFileDialog } = await import("../platform");
      const folderPath = await openFileDialog({ directory: true, title: t("skills.importLocalTitle") });
      if (!folderPath) { setLocalImporting(false); return; }

      let installed = false;

      if (serviceRunning && apiBaseUrl != null) {
        try {
          const res = await safeFetch(`${apiBaseUrl}/api/skills/install`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: folderPath }),
            signal: AbortSignal.timeout(180_000),
          });
          const data = await res.json();
          if (data.error) throw new Error(data.error);
          installed = true;
          try {
            await safeFetch(`${apiBaseUrl}/api/skills/reload`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({}),
              signal: AbortSignal.timeout(10_000),
            });
          } catch { /* reload 失败不阻塞 */ }
        } catch (apiErr) {
          const m = String(apiErr).toLowerCase();
          if (m.includes("request cancel") || m.includes("request_cancel")) {
            installed = false; // fallback to Tauri invoke below
          } else {
            throw apiErr;
          }
        }
      }

      if (!installed && IS_TAURI && currentWorkspaceId) {
        await invoke<string>("openakita_install_skill", {
          venvDir,
          workspaceId: currentWorkspaceId,
          url: folderPath,
        });
      }

      await loadSkills();
      toast.success(t("skills.importLocalSuccess"));
    } catch (e) {
      const msg = String(e);
      if (msg.includes("该技能已安装") || msg.toLowerCase().includes("already installed")) {
        await loadSkills();
        toast.success(t("skills.alreadyInstalled"));
      } else {
        setError(friendlyError(e, t, "install"));
      }
    } finally {
      setLocalImporting(false);
    }
  }, [dataMode, serviceRunning, apiBaseUrl, currentWorkspaceId, venvDir, loadSkills, t]);

  // ── 打开技能详情弹窗 ──
  const handleViewDetail = useCallback(async (skill: SkillInfo) => {
    const requestName = skill.skillId;
    detailRequestNameRef.current = requestName;
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
      const res = await safeFetch(`${apiBaseUrl}/api/skills/content/${encodeURIComponent(skill.skillId)}`, {
        signal: AbortSignal.timeout(10_000),
      });
      if (detailRequestNameRef.current !== requestName) return;
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (detailRequestNameRef.current !== requestName) return;
      if (data.error) {
        setDetailContentError(data.error);
      } else {
        setDetailContent(data.content || "");
      }
    } catch (e) {
      if (detailRequestNameRef.current !== requestName) return;
      setDetailContentError(String(e));
    } finally {
      if (detailRequestNameRef.current === requestName) {
        setDetailContentLoading(false);
      }
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
      const res = await safeFetch(`${apiBaseUrl}/api/skills/content/${encodeURIComponent(detailSkill.skillId)}`, {
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
        toast.success(t("skills.contentSaved"));
        await loadSkills();
      }
    } catch (e) {
      setDetailContentError(`${t("skills.contentSaveFailed")}: ${e}`);
    } finally {
      setDetailSaving(false);
    }
  }, [detailSkill, detailEditContent, serviceRunning, apiBaseUrl, loadSkills, t]);

  // ── 卸载技能（第一步：弹出确认） ──
  const requestUninstall = useCallback((skill: SkillInfo) => {
    if (skill.system) return;
    setUninstallConfirm(skill);
  }, []);

  // ── 卸载技能（第二步：确认后执行） ──
  const executeUninstall = useCallback(async (skill: SkillInfo) => {
    const displayName = skill.name_i18n?.zh || skill.name_i18n?.en || skill.name;
    const key = skill.skillId;
    setUninstallingSet(prev => new Set(prev).add(key));
    setError(null);
    try {
      if (serviceRunning && apiBaseUrl != null) {
        const res = await safeFetch(`${apiBaseUrl}/api/skills/uninstall`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ skill_id: key }),
          signal: AbortSignal.timeout(30_000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
      } else if (IS_TAURI && currentWorkspaceId) {
        await invoke<string>("openakita_uninstall_skill", {
          venvDir,
          workspaceId: currentWorkspaceId,
          skillName: key,
        });
      } else {
        throw new Error(t("skills.envNotReady") || "环境未就绪");
      }

      if (detailSkill?.skillId === key) setDetailSkill(null);
      setMarketplace(prev => prev.map(s => {
        const sid = s.skillId || s.name;
        if (sid === key || s.url === skill.sourceUrl) return { ...s, installed: false };
        return s;
      }));
      toast.success(t("skills.uninstallSuccess", { name: displayName }));
      await loadSkills();
    } catch (e) {
      const msg = friendlyError(e, t, "uninstall");
      setError(msg);
      toast.error(msg);
    } finally {
      setUninstallingSet(prev => { const next = new Set(prev); next.delete(key); return next; });
    }
  }, [serviceRunning, apiBaseUrl, venvDir, currentWorkspaceId, detailSkill, loadSkills, t]);

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
          // 有来源追踪的技能，要求来源精确匹配（避免同名不同仓库误判）
          if (local.sourceUrl) return local.sourceUrl === installUrl;
          // 无来源信息的旧技能，回退到名称/目录匹配
          if (local.name === skillId) return true;
          const pathParts = local.path ? local.path.replace(/\\/g, "/").split("/") : [];
          const dirName = pathParts.length >= 2 ? pathParts[pathParts.length - 2] : "";
          return dirName === skillId;
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
        if (serviceRunning && apiBaseUrl != null) {
          try {
            const res = await safeFetch(`${apiBaseUrl}/api/skills/marketplace?q=${encodeURIComponent(q)}`, {
              signal: AbortSignal.timeout(10000),
            });
            data = await res.json();
          } catch { /* fallback to direct */ }
        }
        // 备选：直接请求（可能被 CORS 阻止）
        if (!data) {
          const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
          if (!res.ok) throw new Error(`skills.sh returned ${res.status}`);
          data = await res.json();
        }
      } else {
        if (IS_TAURI) {
          try {
            const raw = await invoke<string>("http_get_json", { url });
            data = JSON.parse(raw);
          } catch { /* Tauri invoke 失败，继续 fallback */ }
        }

        // 方式2: 通过后端 API 代理
        if (!data && serviceRunning && apiBaseUrl != null) {
          try {
            const res = await safeFetch(`${apiBaseUrl}/api/skills/marketplace?q=${encodeURIComponent(q)}`, {
              signal: AbortSignal.timeout(10000),
            });
            data = await res.json();
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
      setError(`${t("skills.marketplace")}: ${friendlyError(e, t)}`);
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

  const openMarketDetail = useCallback(async (skill: MarketplaceSkill) => {
    setMarketDetailSkill(skill);
    setMarketDetailContent(null);
    setMarketDetailError(null);
    setMarketDetailLoading(true);
    const reqId = ++marketDetailReqRef.current;

    const source = skill.url?.split("@")[0] || "";
    const sid = skill.skillId || skill.name;
    if (!source || !source.includes("/")) {
      setMarketDetailLoading(false);
      return;
    }

    try {
      const params = new URLSearchParams({ source, skill_id: sid });
      const resp = await safeFetch(
        `${apiBaseUrl}/api/skills/marketplace/detail?${params}`,
        { signal: AbortSignal.timeout(20_000) },
      );
      const data = await resp.json();
      if (reqId !== marketDetailReqRef.current) return;
      setMarketDetailContent({
        frontmatter: data.frontmatter || {},
        body: data.body || "",
        empty: !!data.empty,
      });
    } catch (err: unknown) {
      if (reqId !== marketDetailReqRef.current) return;
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("TimeoutError") || msg.includes("timed out") || msg.includes("aborted")) {
        setMarketDetailError("timeout");
      } else if (msg.startsWith("HTTP 5")) {
        setMarketDetailError("server");
      } else {
        setMarketDetailError("network");
      }
    } finally {
      if (reqId === marketDetailReqRef.current) setMarketDetailLoading(false);
    }
  }, [apiBaseUrl]);

  // ── 安装技能 ──
  const handleInstall = useCallback(async (skill: MarketplaceSkill) => {
    if (dataMode !== "remote" && !serviceRunning && (!venvDir || !currentWorkspaceId)) {
      setError(t("skills.envNotReady"));
      return;
    }
    const uniqueKey = skill.url || skill.id || skill.name;
    setInstallingSet(prev => new Set(prev).add(uniqueKey));
    setError(null);
    try {
      let installed = false;

      // 方式1：服务运行中 → HTTP API 安装（首选，不回退 Tauri 避免 venv 缺失报错）
      if (serviceRunning && apiBaseUrl != null) {
        const res = await safeFetch(`${apiBaseUrl}/api/skills/install`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: skill.url }),
          signal: AbortSignal.timeout(180_000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        installed = true;
        try {
          await safeFetch(`${apiBaseUrl}/api/skills/reload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(10_000),
          });
        } catch { /* reload 失败不阻塞，技能下次重启时自动加载 */ }
      }

      // 方式2：服务未运行 → Tauri invoke（本地模式）
      if (!installed && IS_TAURI && dataMode !== "remote" && currentWorkspaceId) {
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
      setExpandedSkill(skill.skillId || skill.name);
    } catch (e) {
      const raw = String(e);
      if (raw.includes("该技能已安装") || raw.toLowerCase().includes("already installed")) {
        const refreshed = await loadSkills();
        if (refreshed) {
          setMarketplace((prev) => prev.map((s) =>
            s.url === skill.url ? { ...s, installed: true } : s
          ));
          toast.success(t("skills.alreadyInstalled"));
          setTab("installed");
        } else {
          setError(friendlyError(e, t, "install"));
        }
      } else {
        const friendly = friendlyError(e, t, "install");
        setError(friendly);
        toast.error(friendly);
      }
    } finally {
      setInstallingSet(prev => { const next = new Set(prev); next.delete(uniqueKey); return next; });
    }
  }, [loadSkills, venvDir, currentWorkspaceId, dataMode, serviceRunning, apiBaseUrl, t]);

  // ── 手动输入链接安装技能 ──
  const handleManualInstall = useCallback(async () => {
    const url = manualUrl.trim();
    if (!url) return;
    if (dataMode !== "remote" && !serviceRunning && (!venvDir || !currentWorkspaceId)) {
      setError(t("skills.envNotReady"));
      return;
    }
    setManualInstalling(true);
    setError(null);
    try {
      let installed = false;

      if (serviceRunning && apiBaseUrl != null) {
        const res = await safeFetch(`${apiBaseUrl}/api/skills/install`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url }),
          signal: AbortSignal.timeout(180_000),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        installed = true;
        try {
          await safeFetch(`${apiBaseUrl}/api/skills/reload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: AbortSignal.timeout(10_000),
          });
        } catch { /* reload 失败不阻塞 */ }
      }

      if (!installed && IS_TAURI && dataMode !== "remote" && currentWorkspaceId) {
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
      const raw = String(e);
      if (raw.includes("该技能已安装") || raw.toLowerCase().includes("already installed")) {
        setManualUrl("");
        await loadSkills();
        toast.success(t("skills.alreadyInstalled"));
        setTab("installed");
      } else {
        const friendly = friendlyError(e, t, "install");
        setError(friendly);
        toast.error(friendly);
      }
    } finally {
      setManualInstalling(false);
    }
  }, [manualUrl, loadSkills, venvDir, currentWorkspaceId, dataMode, serviceRunning, apiBaseUrl, t]);

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <IconZap size={48} />
        <div className="mt-3 font-semibold">技能</div>
        <div className="mt-1 text-xs opacity-50">后端服务未启动，请启动后再进行使用</div>
      </div>
    );
  }

  return (
    <>
      {/* Tab 切换 */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <ToggleGroup
          type="single"
          value={tab}
          onValueChange={(v) => { if (v) setTab(v as "installed" | "marketplace"); }}
          variant="outline"
        >
          <ToggleGroupItem
            value="installed"
            className="text-sm min-w-[5.5rem] data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
          >
            {t("skills.installed")}
            <Badge
              variant="secondary"
              className={
                tab === "installed"
                  ? "ml-1.5 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full bg-white/25 text-primary-foreground"
                  : "ml-1.5 px-1.5 py-0 text-[11px] min-w-[1.25rem] justify-center rounded-full bg-foreground/10 text-foreground/60"
              }
            >
              {skillsWithConfig.length}
            </Badge>
          </ToggleGroupItem>
          <ToggleGroupItem
            value="marketplace"
            className="text-sm min-w-[5.5rem] data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
          >
            {t("skills.marketplace")}
          </ToggleGroupItem>
        </ToggleGroup>
        <div style={{ flex: 1 }} />
        <Button
          variant="outline"
          onClick={async () => {
            if (refreshing || loading) return;
            setRefreshing(true);
            setError(null);
            try {
              if (serviceRunning) {
                const res = await safeFetch(`${apiBaseUrl}/api/skills/reload`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({}),
                  signal: AbortSignal.timeout(15_000),
                });
                const data = await res.json();
                if (data.error) { setError(friendlyError(data.error, t, "reload")); return; }
              }
              const ok = await loadSkills();
              if (ok) toast.success(t("skills.refreshed"));
            } catch (e) {
              setError(friendlyError(e, t, "reload"));
            } finally {
              setRefreshing(false);
            }
          }}
          disabled={refreshing || loading}
          title={t("skills.reloadHint")}
        >
          {(refreshing || loading) && <Loader2 className="animate-spin" />}
          {t("topbar.refresh")}
        </Button>
      </div>

      {error && <div className="errorBox" style={{ marginBottom: 12 }}>{error}</div>}

      {/* 已安装技能 */}
      {tab === "installed" && (
        <>
          <div style={{ display: "grid", gap: 10, minWidth: 0 }}>
            {/* 搜索 + AI 整理 */}
            {skillsWithConfig.length > 0 && (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <div style={{ flex: 1, position: "relative" }}>
                  <IconSearch size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", opacity: 0.4, pointerEvents: "none" }} />
                  <Input
                    value={installedSearch}
                    onChange={(e) => setInstalledSearch(e.target.value)}
                    placeholder={t("skills.filterPlaceholder")}
                    className="pl-8"
                  />
                </div>
                <Button
                  variant="outline"
                  onClick={handleImportLocal}
                  disabled={localImporting}
                  title={t("skills.importLocalTitle")}
                >
                  {localImporting ? <Loader2 className="animate-spin" size={14} /> : <IconFolderOpen size={14} />}
                  {t("skills.importLocal")}
                </Button>
                {serviceRunning && (
                  <Button
                    variant="outline"
                    onClick={handleAiOrganize}
                    disabled={aiOrganizing}
                    title={t("skills.aiOrganizeHint")}
                  >
                    {aiOrganizing ? <Loader2 className="animate-spin" size={14} /> : <IconZap size={14} />}
                    {t("skills.aiOrganize")}
                  </Button>
                )}
              </div>
            )}

            {loading && skillsWithConfig.length === 0 && <div className="cardHint">{t("skills.loading")}</div>}
            {!loading && skillsWithConfig.length === 0 && (
              <div className="card" style={{ textAlign: "center", padding: "30px 20px" }}>
                <div style={{ marginBottom: 8, display: "flex", justifyContent: "center" }}><IconZap size={36} /></div>
                <div style={{ fontWeight: 700, marginBottom: 4 }}>{t("skills.noSkills")}</div>
                <div className="help">{t("skills.noSkillsHint")}</div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleImportLocal}
                  disabled={localImporting}
                  className="mt-3"
                >
                  {localImporting ? <Loader2 className="animate-spin" size={13} /> : <IconFolderOpen size={13} />}
                  {t("skills.importLocal")}
                </Button>
              </div>
            )}
            {installedSearch && filteredSkills.length === 0 && skillsWithConfig.length > 0 && (
              <div className="cardHint" style={{ textAlign: "center", padding: 16 }}>
                {t("skills.noResults")}
              </div>
            )}
            {filteredSkills.map((skill) => (
              <SkillCard
                key={skill.skillId}
                skill={skill}
                expanded={expandedSkill === skill.skillId}
                onToggleExpand={() => setExpandedSkill(expandedSkill === skill.skillId ? null : skill.skillId)}
                onToggleEnabled={() => handleToggleEnabled(skill)}
                onViewDetail={() => handleViewDetail(skill)}
                onUninstall={!skill.system ? () => requestUninstall(skill) : undefined}
                uninstalling={uninstallingSet.has(skill.skillId)}
                envDraft={envDraft}
                onEnvChange={onEnvChange}
                onSaveConfig={() => handleSaveConfig(skill)}
                saving={saving}
              />
            ))}
          </div>

        </>
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
              <Button
                size="sm"
                onClick={handleManualInstall}
                disabled={!manualUrl.trim() || manualInstalling}
              >
                {manualInstalling && <Loader2 className="animate-spin" />}
                {t("skills.install")}
              </Button>
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
            {!marketLoading && marketplace.map((skill) => {
              const uk = skill.url || skill.id || skill.name;
              return (
                <MarketplaceSkillCard
                  key={uk}
                  skill={skill}
                  onInstall={() => handleInstall(skill)}
                  installing={installingSet.has(uk)}
                  onClick={() => openMarketDetail(skill)}
                />
              );
            })}
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
          onUninstall={!detailSkill.system ? () => requestUninstall(detailSkill) : undefined}
          uninstalling={uninstallingSet.has(detailSkill.skillId)}
        />
      )}

      {/* 卸载确认弹窗 */}
      <AlertDialog open={!!uninstallConfirm} onOpenChange={(open) => { if (!open) setUninstallConfirm(null); }}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("skills.uninstall")}</AlertDialogTitle>
            <AlertDialogDescription>
              {uninstallConfirm && t("skills.confirmUninstall", {
                name: uninstallConfirm.name_i18n?.zh || uninstallConfirm.name_i18n?.en || uninstallConfirm.name,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const skill = uninstallConfirm!;
                setUninstallConfirm(null);
                executeUninstall(skill);
              }}
            >
              {t("skills.uninstall")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 市场技能详情弹窗 (skills.sh 风格) */}
      {marketDetailSkill && (() => {
        const fm = marketDetailContent?.frontmatter;
        const desc = fm?.description || marketDetailSkill.description || "";
        const installCmd = `npx skills add ${marketDetailSkill.url?.split("@")[0] || ""} --skill ${marketDetailSkill.skillId || marketDetailSkill.name}`;
        const repoUrl = marketDetailSkill.url ? `https://github.com/${marketDetailSkill.url.split("@")[0]}` : "";
        return (
          <ModalOverlay onClose={() => setMarketDetailSkill(null)}>
            <div
              className="modalContent"
              style={{ maxWidth: 720, width: "92vw", maxHeight: "85vh", display: "flex", flexDirection: "column", padding: 0 }}
            >
              {/* Header */}
              <div style={{ padding: "20px 24px 16px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 11, opacity: 0.4, fontFamily: "monospace", marginBottom: 4 }}>
                      skills / {marketDetailSkill.url?.split("@")[0]?.replace("/", " / ")} / {marketDetailSkill.skillId || marketDetailSkill.name}
                    </div>
                    <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>{marketDetailSkill.name}</h2>
                  </div>
                  <Button variant="ghost" size="icon-xs" onClick={() => setMarketDetailSkill(null)}>
                    <IconX size={18} />
                  </Button>
                </div>

                {/* Install command */}
                <div style={{
                  marginTop: 12, padding: "8px 12px", borderRadius: 6,
                  background: "var(--panel2, #1a1a2e)", border: "1px solid var(--line)",
                  fontFamily: "monospace", fontSize: 12, opacity: 0.7,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  $ {installCmd}
                </div>

                {/* Meta row */}
                <div style={{ display: "flex", gap: 16, marginTop: 12, fontSize: 12, opacity: 0.5, flexWrap: "wrap", alignItems: "center" }}>
                  {marketDetailSkill.installs != null && marketDetailSkill.installs > 0 && (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                      <IconDownload size={11} />{marketDetailSkill.installs.toLocaleString()} {t("skills.installCount")}
                    </span>
                  )}
                  {repoUrl && (
                    <a href={repoUrl} target="_blank" rel="noopener noreferrer"
                      style={{ color: "var(--brand)", textDecoration: "none", fontFamily: "monospace", fontSize: 11 }}>
                      {marketDetailSkill.url?.split("@")[0]}
                    </a>
                  )}
                  {marketDetailSkill.installed && (
                    <span className="pill" style={{ fontSize: 10, borderColor: "rgba(16,185,129,0.25)" }}>{t("skills.installed")}</span>
                  )}
                </div>
              </div>

              {/* Body */}
              <div style={{ flex: 1, overflow: "auto", padding: "0 24px 16px" }}>
                {marketDetailLoading ? (
                  <div style={{ textAlign: "center", padding: 40, opacity: 0.5 }}>
                    <Loader2 className="animate-spin" style={{ display: "inline-block", marginBottom: 8 }} />
                    <div>{t("skills.loadingContent")}</div>
                  </div>
                ) : marketDetailError ? (
                  <div style={{ textAlign: "center", padding: 32 }}>
                    <div style={{ fontSize: 13, color: "var(--destructive, #ef4444)", marginBottom: 12 }}>
                      {marketDetailError === "timeout" && t("skills.detailTimeout")}
                      {marketDetailError === "network" && t("skills.detailNetworkError")}
                      {marketDetailError === "server" && t("skills.detailServerError")}
                    </div>
                    <Button
                      variant="outline" size="sm"
                      onClick={() => openMarketDetail(marketDetailSkill)}
                    >
                      {t("skills.detailRetry")}
                    </Button>
                  </div>
                ) : (
                  <>
                    {/* Summary section */}
                    {desc && (
                      <div style={{ marginTop: 16 }}>
                        <div style={{
                          fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
                          opacity: 0.4, marginBottom: 8,
                        }}>
                          Summary
                        </div>
                        <div style={{
                          padding: "14px 16px", borderRadius: 8,
                          background: "var(--panel2, #f8f9fa)", border: "1px solid var(--line)",
                          fontSize: 13, lineHeight: 1.7,
                        }}>
                          <strong>{desc}</strong>
                        </div>
                      </div>
                    )}

                    {/* Tags */}
                    {marketDetailSkill.tags && marketDetailSkill.tags.length > 0 && (
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 12 }}>
                        {marketDetailSkill.tags.map((tag) => (
                          <span key={tag} style={{
                            fontSize: 11, padding: "2px 8px", borderRadius: 10,
                            background: "rgba(37,99,235,0.08)", color: "var(--brand)",
                          }}>
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* SKILL.md section */}
                    {marketDetailContent?.body && (
                      <div style={{ marginTop: 20 }}>
                        <div style={{
                          display: "flex", alignItems: "center", gap: 6,
                          fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em",
                          opacity: 0.4, marginBottom: 10,
                        }}>
                          <IconEye size={12} /> SKILL.md
                        </div>
                        <div className="skillMdContent" style={{
                          fontSize: 14,
                        }}>
                          {mdModules ? (
                            <mdModules.ReactMarkdown
                              remarkPlugins={[mdModules.remarkGfm]}
                              rehypePlugins={[mdModules.rehypeHighlight]}
                            >
                              {marketDetailContent.body}
                            </mdModules.ReactMarkdown>
                          ) : (
                            <pre style={{
                              whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: 13,
                              padding: 16, borderRadius: 8,
                              background: "var(--panel2, #f8f9fa)", border: "1px solid var(--line)",
                              overflow: "auto",
                            }}>
                              {marketDetailContent.body}
                            </pre>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Content actually empty — SKILL.md not found in repo */}
                    {!marketDetailContent?.body && !desc && marketDetailContent?.empty && (
                      <div style={{ textAlign: "center", padding: 32 }}>
                        <div style={{ fontSize: 13, opacity: 0.5, marginBottom: 12 }}>
                          {t("skills.detailEmpty")}
                        </div>
                        <Button
                          variant="outline" size="sm"
                          onClick={() => openMarketDetail(marketDetailSkill)}
                        >
                          {t("skills.detailRetry")}
                        </Button>
                      </div>
                    )}

                    {/* Loaded but simply no description */}
                    {!marketDetailContent?.body && !desc && !marketDetailContent?.empty && (
                      <div style={{ textAlign: "center", padding: 32, opacity: 0.4 }}>
                        {t("skillStore.noDesc")}
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Footer */}
              <div style={{
                padding: "12px 24px", borderTop: "1px solid var(--line)", flexShrink: 0,
                display: "flex", gap: 8, justifyContent: "flex-end", alignItems: "center",
              }}>
                {repoUrl && (
                  <a href={repoUrl} target="_blank" rel="noopener noreferrer"
                    style={{ fontSize: 12, color: "var(--brand)", textDecoration: "none", marginRight: "auto" }}>
                    GitHub →
                  </a>
                )}
                <Button variant="outline" size="sm" onClick={() => setMarketDetailSkill(null)}>
                  {t("common.close")}
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    const s = marketDetailSkill;
                    setMarketDetailSkill(null);
                    handleInstall(s);
                  }}
                  disabled={marketDetailSkill.installed || installingSet.has(marketDetailSkill.url || marketDetailSkill.id || marketDetailSkill.name)}
                >
                  {marketDetailSkill.installed ? t("skills.installed") : t("skills.install")}
                </Button>
              </div>
            </div>
          </ModalOverlay>
        );
      })()}

      {/* 未保存更改提示栏 */}
      {enabledDirty && (
        <div className="fixed bottom-6 left-1/2 z-[9999] -translate-x-1/2">
          <div className="flex items-center gap-3 rounded-xl border border-border bg-background px-5 py-3 shadow-lg"
            style={{ width: "min(560px, 90vw)" }}
          >
            <span className="text-sm text-foreground/70 flex-1 min-w-0">
              {t("skills.unsavedChanges")}
            </span>
            <Button variant="outline" size="sm" onClick={handleDiscard}>
              {t("skills.discardChanges")}
            </Button>
            <Button size="sm" disabled={savingEnabled} onClick={handleSaveEnabledState}>
              {savingEnabled && <Loader2 className="animate-spin mr-1" size={14} />}
              {t("skills.saveEnabledState")}
            </Button>
          </div>
        </div>
      )}
    </>
  );
}
