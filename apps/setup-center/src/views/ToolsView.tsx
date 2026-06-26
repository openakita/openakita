/**
 * Tools & Skills config view — extracted from App.tsx renderTools().
 */

import { useTranslation } from "react-i18next";
import {
  ArrowRight,
  Blocks,
  GitFork,
  MonitorCog,
  Plug,
  Search,
  ShieldAlert,
  SlidersHorizontal,
} from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { WEB_SEARCH_ENV_KEYS } from "../constants";
import { FieldBool, FieldSelect, FieldText } from "../components/EnvFields";
import { Section } from "../components/Section";
import WebSearchProviderPanel from "../components/WebSearchProviderPanel";
import { useExpandPanel } from "../hooks/useExpandPanel";
import { safeFetch } from "../providers";
import type { EnvMap, ViewId } from "../types";
import { notifySuccess } from "../utils/notify";

export interface ToolsViewProps {
  envDraft: EnvMap;
  setEnvDraft: React.Dispatch<React.SetStateAction<EnvMap>>;
  busy: string | null;
  disabledViews: string[];
  toggleViewDisabled: (viewName: string) => void | Promise<void>;
  shouldUseHttpApi: () => boolean;
  httpApiBase: () => string;
  apiBaseUrl: string;
  saveEnvKeys: (keys: string[]) => Promise<unknown>;
  setView: (view: ViewId) => void;
}

function GroupHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="mt-4 flex items-center gap-2 px-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
      <span className="inline-flex size-5 items-center justify-center rounded-md border border-border/70 bg-muted/40">
        {icon}
      </span>
      <span>{title}</span>
      <span className="h-px flex-1 bg-border/70" />
    </div>
  );
}

export function ToolsView(props: ToolsViewProps) {
  const {
    envDraft, setEnvDraft, busy,
    disabledViews, toggleViewDisabled,
    shouldUseHttpApi, httpApiBase, apiBaseUrl, saveEnvKeys, setView,
  } = props;

  const { t } = useTranslation();

  const _envBase = { envDraft, onEnvChange: setEnvDraft, busy };
  const FT = (p: { k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password" }) =>
    <FieldText key={p.k} {...p} {..._envBase} />;
  const FB = (p: { k: string; label: string; help?: string; defaultValue?: boolean }) =>
    <FieldBool key={p.k} {...p} {..._envBase} />;
  const FS = (p: { k: string; label: string; options: { value: string; label: string }[]; help?: string; defaultValue?: string }) =>
    <FieldSelect key={p.k} {...p} {..._envBase} />;

  const mcpRef = useExpandPanel("mcp");
  const desktopRef = useExpandPanel("desktop");
  const parallelRef = useExpandPanel("tool-parallelism");
  const guardRef = useExpandPanel("hallucination-guard");
  const webSearchRef = useExpandPanel("web-search");

  const mcpEnabled = !disabledViews.includes("mcp");
  const desktopEnabled = envDraft["DESKTOP_ENABLED"] !== "false";
  const webSearchProvider = (envDraft["WEB_SEARCH_PROVIDER"] || "").trim();

  const stateLabel = (enabled: boolean) =>
    enabled ? t("config.toolsSkillsEnabled") : t("config.toolsSkillsDisabled");

  const renderToggle = (enabled: boolean, onCheckedChange: (checked: boolean) => void) => (
    <label
      className="inline-flex items-center gap-2 text-xs text-muted-foreground"
      onClick={(e) => e.stopPropagation()}
    >
      <span
        className={cn(
          "inline-flex items-center rounded-full border px-2 py-0.5 font-medium",
          enabled
            ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : "border-muted-foreground/30 bg-muted/50 text-muted-foreground",
        )}
      >
        {stateLabel(enabled)}
      </span>
      <Switch checked={enabled} onCheckedChange={onCheckedChange} disabled={!!busy} />
    </label>
  );

  const onToggleMcp = async (next: boolean) => {
    const willDisable = !next;
    toggleViewDisabled("mcp");
    setEnvDraft((p) => ({ ...p, MCP_ENABLED: willDisable ? "false" : "true" }));
    try {
      if (shouldUseHttpApi()) {
        await safeFetch(`${httpApiBase()}/api/config/env`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ entries: { MCP_ENABLED: willDisable ? "false" : "true" } }),
        });
        notifySuccess(willDisable
          ? t("config.mcpDisabledNeedRestart", { defaultValue: "MCP 已禁用，重启后生效" })
          : t("config.mcpEnabledNeedRestart", { defaultValue: "MCP 已启用，重启后生效" }));
      }
    } catch {
      // ignore
    }
  };

  return (
    <div className="card">
      <div className="rounded-lg border border-border/70 bg-muted/20 px-3 py-3">
        <h3 className="text-base font-bold tracking-tight">{t("config.toolsTitle")}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{t("config.toolsHint")}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-normal">
            MCP: {stateLabel(mcpEnabled)}
          </Badge>
          <Badge variant="outline" className="font-normal">
            Desktop: {stateLabel(desktopEnabled)}
          </Badge>
          <Badge variant="outline" className="font-normal">
            {t("toolsWebSearch.activeProvider", "激活搜索源")}:
            {" "}
            {webSearchProvider || t("toolsWebSearch.autoDetect", "自动检测（推荐）")}
          </Badge>
        </div>
      </div>

      <GroupHeader
        icon={<Blocks className="size-3.5" />}
        title={t("config.toolsGroupCapabilities", { defaultValue: "能力来源" })}
      />
      <div className="mt-2 flex flex-col gap-2">
        <Section
          panelRef={mcpRef}
          panelId="mcp"
          icon={<Plug className="size-4" />}
          title={t("config.toolsMCP")}
          subtitle={t("config.toolsMCPSubtitle", { defaultValue: "接入外部 MCP 工具服务器" })}
          toggle={renderToggle(mcpEnabled, onToggleMcp)}
        >
          <div className="grid2">
            {FT({ k: "MCP_TIMEOUT", label: "Timeout (s)", placeholder: "60" })}
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-fit px-2 text-xs text-muted-foreground"
            onClick={() => setView("mcp")}
          >
            {t("config.toolsManageMcp", { defaultValue: "管理 MCP 服务器" })}
            <ArrowRight className="size-3.5" />
          </Button>
        </Section>

        <Section
          panelRef={webSearchRef}
          panelId="web-search"
          icon={<Search className="size-4" />}
          title={t("toolsWebSearch.sectionTitle", "网页搜索源（Web Search Source）")}
          subtitle={t("config.toolsWebSearchSubtitle", { defaultValue: "联网搜索的服务来源与密钥" })}
        >
          <WebSearchProviderPanel
            envDraft={envDraft}
            onEnvChange={setEnvDraft}
            onSaveEnv={async () => { await saveEnvKeys(WEB_SEARCH_ENV_KEYS); }}
            busy={busy}
            apiBaseUrl={apiBaseUrl}
          />
        </Section>
      </div>

      <GroupHeader
        icon={<MonitorCog className="size-3.5" />}
        title={t("config.toolsGroupAutomation", { defaultValue: "自动化" })}
      />
      <div className="mt-2 flex flex-col gap-2">
        <Section
          panelRef={desktopRef}
          panelId="desktop"
          icon={<MonitorCog className="size-4" />}
          title={t("config.toolsDesktop")}
          subtitle={t("config.toolsDesktopSubtitle", { defaultValue: "让助手控制桌面（截图/点击/输入）" })}
          toggle={renderToggle(desktopEnabled, (v) => setEnvDraft((p) => ({ ...p, DESKTOP_ENABLED: v ? "true" : "false" })))}
        >
          <div className="grid3">
            {FT({ k: "DESKTOP_DEFAULT_MONITOR", label: t("config.toolsMonitor"), placeholder: "0" })}
            {FT({ k: "DESKTOP_MAX_WIDTH", label: t("config.toolsMaxW"), placeholder: "1920" })}
            {FT({ k: "DESKTOP_MAX_HEIGHT", label: t("config.toolsMaxH"), placeholder: "1080" })}
          </div>
          <Section
            title={t("config.toolsDesktopAdvanced")}
            className="border-dashed bg-muted/30"
            contentClassName="bg-muted/20"
          >
            <div className="grid3">
              {FT({ k: "DESKTOP_COMPRESSION_QUALITY", label: t("config.toolsCompression"), placeholder: "85" })}
              {FT({ k: "DESKTOP_CACHE_TTL", label: "Cache TTL", placeholder: "1.0" })}
              {FB({ k: "DESKTOP_FAILSAFE", label: "安全角保护", help: "鼠标移到屏幕角落时自动停止桌面操作，避免误点或误操作。" })}
            </div>
            {FB({ k: "DESKTOP_VISION_ENABLED", label: t("config.toolsVision"), help: t("config.toolsVisionHelp") })}
            <div className="grid3">
              {FT({ k: "DESKTOP_CLICK_DELAY", label: "Click Delay", placeholder: "0.1" })}
              {FT({ k: "DESKTOP_TYPE_INTERVAL", label: "Type Interval", placeholder: "0.03" })}
              {FT({ k: "DESKTOP_MOVE_DURATION", label: "Move Duration", placeholder: "0.15" })}
            </div>
          </Section>
        </Section>
      </div>

      <GroupHeader
        icon={<SlidersHorizontal className="size-3.5" />}
        title={t("config.toolsGroupExecution", { defaultValue: "执行与安全" })}
      />
      <div className="mt-2 flex flex-col gap-2">
        <Section
          panelRef={parallelRef}
          panelId="tool-parallelism"
          icon={<GitFork className="size-4" />}
          title={t("config.toolsParallel")}
          subtitle={t("config.toolsParallelSubtitle", { defaultValue: "同时执行的工具调用数量上限" })}
        >
          <div className="grid2">
            {FT({ k: "TOOL_MAX_PARALLEL", label: t("config.toolsParallel"), placeholder: "1", help: t("config.toolsParallelHelp") })}
          </div>
        </Section>

        <Section
          panelRef={guardRef}
          panelId="hallucination-guard"
          icon={<ShieldAlert className="size-4" />}
          title={t("config.toolsHallucinationGuard")}
          subtitle={t("config.toolsHallucinationGuardSubtitle", { defaultValue: "强制工具调用与确认重试策略" })}
        >
          <p className="text-xs text-muted-foreground">{t("config.toolsHallucinationGuardHint")}</p>
          <div className="grid2">
            {FS({
              k: "FORCE_TOOL_CALL_MAX_RETRIES",
              label: t("config.toolsForceRetry"),
              defaultValue: "2",
              options: [
                { value: "0", label: t("config.guardOff") },
                { value: "1", label: "1" },
                { value: "2", label: "2" },
                { value: "3", label: "3" },
              ],
            })}
            {FS({
              k: "FORCE_TOOL_CALL_IM_FLOOR",
              label: t("config.toolsImFloor"),
              defaultValue: "2",
              options: [
                { value: "0", label: t("config.guardSameAsGlobal") },
                { value: "1", label: "1" },
                { value: "2", label: "2" },
              ],
            })}
          </div>
          <div className="grid2">
            {FS({
              k: "CONFIRMATION_TEXT_MAX_RETRIES",
              label: t("config.toolsConfirmTextRetry"),
              defaultValue: "2",
              options: [
                { value: "0", label: t("config.guardOff") },
                { value: "1", label: "1" },
                { value: "2", label: "2" },
                { value: "3", label: "3" },
              ],
            })}
          </div>
        </Section>
      </div>
    </div>
  );
}

