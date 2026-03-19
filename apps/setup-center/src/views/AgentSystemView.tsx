import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldText, FieldBool, FieldSelect } from "../components/EnvFields";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Brain, Loader2, Upload, Download } from "lucide-react";
import { Section } from "../components/Section";
import { toast } from "sonner";
import { safeFetch } from "../providers";
import { IS_TAURI, saveFileDialog, showInFolder } from "../platform";
import type { EnvMap } from "../types";
import { envGet, envSet } from "../utils";

// ─── Types ──────────────────────────────────────────────────────────────

type AgentSystemViewProps = {
  envDraft: EnvMap;
  setEnvDraft: (updater: (prev: EnvMap) => EnvMap) => void;
  busy?: string | null;
  disabledViews: string[];
  toggleViewDisabled: (viewName: string) => void;
  serviceRunning?: boolean;
  apiBaseUrl?: string;
};

// ─── Reusable: toggle pill (iOS-style switch in summary) ────────────────

function TogglePill({ enabled, label, onToggle }: {
  enabled: boolean;
  label: [string, string]; // [enabledText, disabledText]
  onToggle: () => void;
}) {
  return (
    <label
      className="inline-flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none"
      onClick={(e) => e.stopPropagation()}
    >
      <span>{enabled ? label[0] : label[1]}</span>
      <div
        onClick={onToggle}
        className="relative shrink-0 transition-colors duration-200 rounded-full"
        style={{
          width: 40, height: 22,
          background: enabled ? "var(--ok, #22c55e)" : "var(--line, #d1d5db)",
        }}
      >
        <div
          className="absolute top-0.5 rounded-full bg-white shadow-sm transition-[left] duration-200"
          style={{ width: 18, height: 18, left: enabled ? 20 : 2 }}
        />
      </div>
    </label>
  );
}

// ─── Main Component ─────────────────────────────────────────────────────

export function AgentSystemView(props: AgentSystemViewProps) {
  const { envDraft, setEnvDraft, busy = null, disabledViews, toggleViewDisabled, serviceRunning, apiBaseUrl = "" } = props;
  const { t } = useTranslation();

  const [reviewing, setReviewing] = useState(false);
  const [showReviewConfirm, setShowReviewConfirm] = useState(false);
  const [importing, setImporting] = useState(false);

  const handleImportPersona = async () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".md";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setImporting(true);
      try {
        const formData = new FormData();
        formData.append("file", file);
        const res = await safeFetch(`${apiBaseUrl}/api/identity/persona/import`, {
          method: "POST",
          body: formData,
        });
        const data = await res.json();
        if (data.persona_id) {
          setEnvDraft((m) => envSet(m, "PERSONA_NAME", data.persona_id));
          toast.success(t("config.personaImportSuccess", { name: data.persona_id }));
        }
      } catch (e: any) {
        toast.error(t("config.personaImportError") + ": " + (e.message || "Unknown error"));
      } finally {
        setImporting(false);
      }
    };
    input.click();
  };

  const handleDownloadTemplate = async () => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/identity/persona/template`);
      const text = await res.text();

      if (IS_TAURI) {
        const chosen = await saveFileDialog({
          title: t("config.personaTemplateSaveTitle"),
          defaultPath: "persona_template.md",
          filters: [{ name: "Markdown", extensions: ["md"] }],
        });
        if (!chosen) return;
        const { writeTextFile } = await import("@tauri-apps/plugin-fs");
        await writeTextFile(chosen, text);
        toast.success(t("config.personaTemplateSaved", { path: chosen }), {
          action: { label: t("config.personaOpenFolder"), onClick: () => showInFolder(chosen) },
          duration: 8000,
        });
      } else {
        const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "persona_template.md";
        a.click();
        URL.revokeObjectURL(url);
        toast.success(t("config.personaTemplateDownloaded"));
      }
    } catch (e: any) {
      toast.error(e.message || "Download failed");
    }
  };

  const handleReview = async () => {
    setShowReviewConfirm(false);
    setReviewing(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/memories/review`, {
        method: "POST",
        signal: AbortSignal.timeout(180_000),
      });
      const data = await res.json();
      const review = data?.review ?? data;
      if (review && typeof review.deleted === "number") {
        toast.success(
          `LLM 审查完成：删除 ${review.deleted}，更新 ${review.updated}，合并 ${review.merged}，保留 ${review.kept}` +
          (review.errors > 0 ? `，错误 ${review.errors}` : "")
        );
      } else {
        toast.error("审查完成，但返回数据格式异常");
      }
    } catch (e: any) {
      toast.error(e.message || "审查请求失败");
    } finally {
      setReviewing(false);
    }
  };

  const _envBase = { envDraft, onEnvChange: setEnvDraft, busy };
  const FT = (p: { k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password" }) =>
    <FieldText key={p.k} {...p} {..._envBase} />;
  const FB = (p: { k: string; label: string; help?: string; defaultValue?: boolean }) =>
    <FieldBool key={p.k} {...p} {..._envBase} />;
  const FS = (p: { k: string; label: string; options: { value: string; label: string }[]; help?: string }) =>
    <FieldSelect key={p.k} {...p} {..._envBase} />;

  const enabledLabel: [string, string] = [
    t("config.toolsSkillsEnabled"),
    t("config.toolsSkillsDisabled"),
  ];

  const proactiveEnabled = envGet(envDraft, "PROACTIVE_ENABLED", "true") !== "false";

  const personas = [
    { id: "default", desc: "config.agentPersonaDefault" },
    { id: "business", desc: "config.agentPersonaBusiness" },
    { id: "tech_expert", desc: "config.agentPersonaTech" },
    { id: "butler", desc: "config.agentPersonaButler" },
    { id: "girlfriend", desc: "config.agentPersonaGirlfriend" },
    { id: "boyfriend", desc: "config.agentPersonaBoyfriend" },
    { id: "family", desc: "config.agentPersonaFamily" },
    { id: "jarvis", desc: "config.agentPersonaJarvis" },
  ];
  const curPersona = envGet(envDraft, "PERSONA_NAME", "default");

  return (
    <>
      {/* ═══════ 灵魂 Soul ═══════ */}
      <div className="card">
        <h3 className="text-base font-bold tracking-tight">{t("config.soulTitle")}</h3>
        <p className="text-sm text-muted-foreground mt-1 mb-3">{t("config.soulSubtitle")}</p>

        {/* ── 角色选择 ── */}
        <Section title={t("config.agentPersona")} subtitle={t("config.agentPersonaSub")}>
          <ToggleGroup
            type="single"
            variant="outline"
            spacing={2}
            value={curPersona}
            onValueChange={(val) => {
              if (val) setEnvDraft((m) => envSet(m, "PERSONA_NAME", val));
            }}
            className="flex-wrap"
          >
            {personas.map((p) => (
              <ToggleGroupItem
                key={p.id}
                value={p.id}
                className="text-sm min-w-[5.5rem] data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary"
              >
                {t(p.desc)}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          {(curPersona === "custom" || !personas.find((p) => p.id === curPersona)) && (
            <Input
              className="max-w-[300px]"
              placeholder={t("config.agentCustomId")}
              value={envGet(envDraft, "PERSONA_NAME", "custom")}
              onChange={(e) => {
                setEnvDraft((m) => envSet(m, "PERSONA_NAME", e.target.value || "custom"));
              }}
            />
          )}
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border/50">
            <Button
              size="sm"
              variant="outline"
              onClick={handleImportPersona}
              disabled={importing || !serviceRunning}
              className="text-xs"
            >
              {importing ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              {t("config.personaImport")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleDownloadTemplate}
              disabled={!serviceRunning}
              className="text-xs text-muted-foreground"
            >
              <Download size={14} />
              {t("config.personaTemplateDownload")}
            </Button>
          </div>
        </Section>

        {/* ── 核心参数 ── */}
        <Section title={t("config.agentCore")} subtitle={t("config.agentCoreSub")} className="mt-2">
          <div className="grid3">
            {FT({ k: "AGENT_NAME", label: t("config.agentName"), placeholder: "OpenAkita" })}
            {FT({ k: "MAX_ITERATIONS", label: t("config.agentMaxIter"), placeholder: "300", help: t("config.agentMaxIterHelp") })}
            {FS({ k: "THINKING_MODE", label: t("config.agentThinking"), options: [
              { value: "auto", label: t("config.agentThinkingAuto") },
              { value: "always", label: t("config.agentThinkingAlways") },
              { value: "never", label: t("config.agentThinkingNever") },
            ] })}
          </div>
        </Section>

        {/* ── 记忆管理 ── */}
        <Section
          title={t("sidebar.memory")}
          subtitle={t("config.memorySub")}
          className="mt-2"
          toggle={
            <TogglePill
              enabled={!disabledViews.includes("memory")}
              label={enabledLabel}
              onToggle={() => toggleViewDisabled("memory")}
            />
          }
        >
          <Button
            size="sm"
            onClick={() => setShowReviewConfirm(true)}
            disabled={reviewing || !serviceRunning}
            className="bg-gradient-to-br from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white border-0 w-fit"
          >
            {reviewing ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
            {reviewing ? t("config.memoryReviewing") : t("config.memoryReviewBtn")}
          </Button>
        </Section>

        <AlertDialog open={showReviewConfirm} onOpenChange={setShowReviewConfirm}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("config.memoryReviewTitle")}</AlertDialogTitle>
              <AlertDialogDescription>{t("config.memoryReviewDesc")}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t("config.memoryReviewCancel")}</AlertDialogCancel>
              <AlertDialogAction
                onClick={handleReview}
                className="bg-gradient-to-br from-indigo-500 to-purple-500 hover:from-indigo-600 hover:to-purple-600 text-white border-0"
              >
                {t("config.memoryReviewConfirm")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      {/* ═══════ 意志 Will ═══════ */}
      <div className="card" style={{ marginTop: 16 }}>
        <h3 className="text-base font-bold tracking-tight">{t("config.willTitle")}</h3>
        <p className="text-sm text-muted-foreground mt-1 mb-3">{t("config.willSubtitle")}</p>

        {/* ── 活人感模式 ── */}
        <Section
          title={t("config.agentProactive")}
          subtitle={t("config.agentProactiveSub")}
          toggle={
            <TogglePill
              enabled={proactiveEnabled}
              label={enabledLabel}
              onToggle={() => setEnvDraft((p) => ({ ...p, PROACTIVE_ENABLED: proactiveEnabled ? "false" : "true" }))}
            />
          }
        >
          <div className="grid3">
            {FT({ k: "PROACTIVE_MAX_DAILY_MESSAGES", label: t("config.agentMaxDaily"), placeholder: "3", help: t("config.agentMaxDailyHelp") })}
            {FT({ k: "PROACTIVE_MIN_INTERVAL_MINUTES", label: t("config.agentMinInterval"), placeholder: "120", help: t("config.agentMinIntervalHelp") })}
            {FT({ k: "PROACTIVE_IDLE_THRESHOLD_HOURS", label: t("config.agentIdleThreshold"), placeholder: "24", help: t("config.agentIdleThresholdHelp") })}
          </div>
          <div className="grid3">
            {FT({ k: "PROACTIVE_QUIET_HOURS_START", label: t("config.agentQuietStart"), placeholder: "23", help: t("config.agentQuietStartHelp") })}
            {FT({ k: "PROACTIVE_QUIET_HOURS_END", label: t("config.agentQuietEnd"), placeholder: "7" })}
            <div />
          </div>
          <div className="grid3">
            {FB({ k: "STICKER_ENABLED", label: t("config.agentSticker"), help: t("config.agentStickerHelp") })}
            {FT({ k: "STICKER_DATA_DIR", label: t("config.agentStickerDir"), placeholder: "data/sticker" })}
            <div />
          </div>
        </Section>

        {/* ── 计划任务 ── */}
        <Section
          title={t("config.agentScheduler")}
          subtitle={t("config.agentSchedulerSub")}
          className="mt-2"
          toggle={
            <TogglePill
              enabled={!disabledViews.includes("scheduler")}
              label={enabledLabel}
              onToggle={() => toggleViewDisabled("scheduler")}
            />
          }
        >
          <div className="grid3">
            {FT({ k: "SCHEDULER_TIMEZONE", label: t("config.agentTimezone"), placeholder: "Asia/Shanghai", help: t("config.agentTimezoneHelp") })}
          </div>
        </Section>
      </div>
    </>
  );
}
