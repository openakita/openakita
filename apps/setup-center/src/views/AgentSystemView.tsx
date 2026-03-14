import { useTranslation } from "react-i18next";
import { FieldText, FieldBool, FieldSelect } from "../components/EnvFields";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Input } from "@/components/ui/input";
import type { EnvMap } from "../types";
import { envGet, envSet } from "../utils";

type AgentSystemViewProps = {
  envDraft: EnvMap;
  setEnvDraft: (updater: (prev: EnvMap) => EnvMap) => void;
  busy: string | null;
};

export function AgentSystemView(props: AgentSystemViewProps) {
  const { envDraft, setEnvDraft, busy } = props;
  const { t } = useTranslation();

  const _envBase = { envDraft, onEnvChange: setEnvDraft, busy };
  const FT = (p: { k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password" }) =>
    <FieldText key={p.k} {...p} {..._envBase} />;
  const FB = (p: { k: string; label: string; help?: string; defaultValue?: boolean }) =>
    <FieldBool key={p.k} {...p} {..._envBase} />;
  const FS = (p: { k: string; label: string; options: { value: string; label: string }[]; help?: string }) =>
    <FieldSelect key={p.k} {...p} {..._envBase} />;

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
      <div className="card">
        <div className="cardTitle">{t("config.agentTitle")}</div>
        <div className="cardHint">{t("config.agentHint")}</div>
        <div className="divider" />

        {/* Persona Selection */}
        <div className="mb-3">
          <div className="text-sm font-medium mb-2">{t("config.agentPersona")}</div>
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
              className="mt-2 max-w-[300px]"
              placeholder={t("config.agentCustomId")}
              value={envGet(envDraft, "PERSONA_CUSTOM_ID", "")}
              onChange={(e) => {
                setEnvDraft((m) => envSet(m, "PERSONA_CUSTOM_ID", e.target.value));
                setEnvDraft((m) => envSet(m, "PERSONA_NAME", e.target.value || "custom"));
              }}
            />
          )}
        </div>

        {/* Core Parameters */}
        <div className="label">{t("config.agentCore")}</div>
        <div className="grid3" style={{ marginTop: 4 }}>
          {FT({ k: "AGENT_NAME", label: t("config.agentName"), placeholder: "OpenAkita" })}
          {FT({ k: "MAX_ITERATIONS", label: t("config.agentMaxIter"), placeholder: "300", help: t("config.agentMaxIterHelp") })}
          {FS({ k: "THINKING_MODE", label: t("config.agentThinking"), options: [
            { value: "auto", label: "auto (自动判断)" },
            { value: "always", label: "always (始终思考)" },
            { value: "never", label: "never (从不思考)" },
          ] })}
        </div>
        <div style={{ marginTop: 8 }}>
          {FB({ k: "AUTO_CONFIRM", label: t("config.agentAutoConfirm"), help: t("config.agentAutoConfirmHelp") })}
        </div>

        <div className="divider" />

        {/* Living Presence */}
        <div className="label">{t("config.agentProactive")}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
          <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
            {FB({ k: "PROACTIVE_ENABLED", label: t("config.agentProactiveEnable"), help: t("config.agentProactiveEnableHelp") })}
            {FB({ k: "STICKER_ENABLED", label: t("config.agentSticker"), help: t("config.agentStickerHelp") })}
          </div>
          <div className="grid3">
            {FT({ k: "PROACTIVE_MAX_DAILY_MESSAGES", label: t("config.agentMaxDaily"), placeholder: "3", help: t("config.agentMaxDailyHelp") })}
            {FT({ k: "PROACTIVE_QUIET_HOURS_START", label: t("config.agentQuietStart"), placeholder: "23", help: t("config.agentQuietStartHelp") })}
            {FT({ k: "PROACTIVE_QUIET_HOURS_END", label: t("config.agentQuietEnd"), placeholder: "7" })}
          </div>
        </div>

        <div className="divider" />

        {/* Desktop Notification */}
        <div className="label">{t("config.agentDesktopNotify")}</div>
        <div className="row" style={{ gap: 16, flexWrap: "wrap", marginTop: 4 }}>
          {FB({ k: "DESKTOP_NOTIFY_ENABLED", label: t("config.agentDesktopNotifyEnable"), help: t("config.agentDesktopNotifyEnableHelp") })}
          {FB({ k: "DESKTOP_NOTIFY_SOUND", label: t("config.agentDesktopNotifySound"), help: t("config.agentDesktopNotifySoundHelp") })}
        </div>

        <div className="divider" />

        {/* Scheduler */}
        <div className="label">{t("config.agentScheduler")}</div>
        <div className="grid3" style={{ marginTop: 4 }}>
          {FB({ k: "SCHEDULER_ENABLED", label: t("config.agentSchedulerEnable"), help: t("config.agentSchedulerEnableHelp"), defaultValue: true })}
          {FT({ k: "SCHEDULER_TIMEZONE", label: t("config.agentTimezone"), placeholder: "Asia/Shanghai" })}
          {FT({ k: "SCHEDULER_MAX_CONCURRENT", label: t("config.agentMaxConcurrent"), placeholder: "5", help: t("config.agentMaxConcurrentHelp") })}
        </div>

        <div className="divider" />

        {/* Advanced (collapsed) */}
        <details className="configDetails">
          <summary>{t("config.agentAdvanced")}</summary>
          <div className="configDetailsBody">
            <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentLogSection")}</div>
            <div className="grid3">
              {FS({ k: "LOG_LEVEL", label: t("config.agentLogLevel"), options: [
                { value: "DEBUG", label: "DEBUG" },
                { value: "INFO", label: "INFO" },
                { value: "WARNING", label: "WARNING" },
                { value: "ERROR", label: "ERROR" },
              ] })}
              {FT({ k: "LOG_DIR", label: t("config.agentLogDir"), placeholder: "logs" })}
              {FT({ k: "DATABASE_PATH", label: t("config.agentDbPath"), placeholder: "data/agent.db" })}
            </div>
            <div className="grid3">
              {FT({ k: "LOG_MAX_SIZE_MB", label: t("config.agentLogMaxMB"), placeholder: "10" })}
              {FT({ k: "LOG_BACKUP_COUNT", label: t("config.agentLogBackup"), placeholder: "30" })}
              {FT({ k: "LOG_RETENTION_DAYS", label: t("config.agentLogRetention"), placeholder: "30" })}
            </div>
            <div className="grid2">
              {FB({ k: "LOG_TO_CONSOLE", label: t("config.agentLogConsole") })}
              {FB({ k: "LOG_TO_FILE", label: t("config.agentLogFile") })}
            </div>

            <div className="divider" />
            <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentMemorySection")}</div>
            <div className="grid3">
              {FT({ k: "EMBEDDING_MODEL", label: t("config.agentEmbedModel"), placeholder: "shibing624/text2vec-base-chinese" })}
              {FT({ k: "EMBEDDING_DEVICE", label: t("config.agentEmbedDevice"), placeholder: "cpu" })}
              {FS({ k: "MODEL_DOWNLOAD_SOURCE", label: t("config.agentDownloadSource"), options: [
                { value: "auto", label: "Auto (自动选择)" },
                { value: "hf-mirror", label: "hf-mirror (国内镜像)" },
                { value: "modelscope", label: "ModelScope (魔搭)" },
                { value: "huggingface", label: "HuggingFace (官方)" },
              ] })}
            </div>
            <div className="grid3">
              {FT({ k: "MEMORY_HISTORY_DAYS", label: t("config.agentMemDays"), placeholder: "30" })}
              {FT({ k: "MEMORY_MAX_HISTORY_FILES", label: t("config.agentMemFiles"), placeholder: "1000" })}
              {FT({ k: "MEMORY_MAX_HISTORY_SIZE_MB", label: t("config.agentMemSize"), placeholder: "500" })}
            </div>

            <div className="divider" />
            <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentSessionSection")}</div>
            <div className="grid3">
              {FT({ k: "SESSION_TIMEOUT_MINUTES", label: t("config.agentSessionTimeout"), placeholder: "30" })}
              {FT({ k: "SESSION_MAX_HISTORY", label: t("config.agentSessionMax"), placeholder: "50" })}
              {FT({ k: "SESSION_STORAGE_PATH", label: t("config.agentSessionPath"), placeholder: "data/sessions" })}
            </div>

            <div className="divider" />
            <div className="label" style={{ fontSize: 13, opacity: 0.7 }}>{t("config.agentProactiveAdv")}</div>
            <div className="grid2">
              {FT({ k: "PROACTIVE_MIN_INTERVAL_MINUTES", label: t("config.agentMinInterval"), placeholder: "120" })}
              {FT({ k: "PROACTIVE_IDLE_THRESHOLD_HOURS", label: t("config.agentIdleThreshold"), placeholder: "24" })}
              {FT({ k: "STICKER_DATA_DIR", label: t("config.agentStickerDir"), placeholder: "data/sticker" })}
            </div>
          </div>
        </details>
      </div>
    </>
  );
}
