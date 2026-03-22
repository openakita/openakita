import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { LogoTelegram, LogoFeishu, LogoWework, LogoDingtalk, LogoQQ, LogoOneBot, LogoWechat } from "../icons";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { EnvMap } from "../types";
import type { IMBot } from "./im-shared";
import { envGet, envSet } from "../utils";
import { copyToClipboard } from "../utils/clipboard";
import { BotConfigTab } from "./IMView";
import { cn } from "@/lib/utils";
import { AlertCircle, BookOpen, BrainCircuit, ExternalLink, Info } from "lucide-react";

type IMConfigViewProps = {
  envDraft: EnvMap;
  setEnvDraft: (updater: (prev: EnvMap) => EnvMap) => void;
  busy?: string | null;
  currentWorkspaceId: string | null;
  venvDir?: string;
  apiBaseUrl?: string;
  onRequestRestart?: () => void;
  wizardMode?: boolean;
  multiAgentEnabled?: boolean;
  onNavigateToBotConfig?: (presetType: string) => void;
  pendingBots?: IMBot[];
  onPendingBotsChange?: React.Dispatch<React.SetStateAction<IMBot[]>>;
};

const DEFAULT_API = "http://127.0.0.1:18900";

const PLATFORMS = [
  { id: "wechat", title: "config.imWechat", logo: LogoWechat, docUrl: "https://developers.weixin.qq.com/doc/" },
  { id: "feishu", title: "config.imFeishu", logo: LogoFeishu, docUrl: "https://open.feishu.cn/" },
  { id: "dingtalk", title: "config.imDingtalk", logo: LogoDingtalk, docUrl: "https://open.dingtalk.com/" },
  { id: "wework", title: "config.imWework", logo: LogoWework, docUrl: "https://work.weixin.qq.com/" },
  { id: "qqbot", title: "config.imQQBot", logo: LogoQQ, docUrl: "https://bot.q.qq.com/wiki/develop/api-v2/" },
  { id: "telegram", title: "Telegram", logo: LogoTelegram, docUrl: "https://t.me/BotFather" },
  { id: "onebot", title: "OneBot", logo: LogoOneBot, docUrl: "https://github.com/botuniverse/onebot-11" },
] as const;

export function IMConfigView(props: IMConfigViewProps) {
  const {
    envDraft, setEnvDraft, busy = null, currentWorkspaceId, venvDir = "",
    apiBaseUrl, onRequestRestart, wizardMode = false, multiAgentEnabled,
  } = props;
  const { t } = useTranslation();

  const chainPushOn = envGet(envDraft, "IM_CHAIN_PUSH", "false").toLowerCase() === "true";

  return (
    <div className="card">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-bold tracking-tight flex items-center gap-2">
            {t("config.imTitle")}
            <Button
              variant="outline" size="sm" className="h-7 gap-1 text-xs"
              onClick={async () => {
                const ok = await copyToClipboard(
                  "https://github.com/anthropic-lab/openakita/blob/main/docs/im-channels.md",
                );
                if (ok) toast.success(t("config.imGuideDocCopied"));
              }}
              title={t("config.imGuideDoc")}
            >
              <BookOpen size={13} />{t("config.imGuideDoc")}
            </Button>
          </h3>
          <p className="text-sm text-muted-foreground mt-1">{t("config.imHint")}</p>
        </div>
        {!wizardMode && (
          <div className="flex flex-col items-end gap-1 shrink-0">
            <label
              className={cn(
                "inline-flex items-center gap-2.5 h-10 px-3.5 rounded-md border cursor-pointer select-none transition-colors",
                chainPushOn
                  ? "border-amber-400 bg-amber-50 dark:border-amber-600 dark:bg-amber-950/40"
                  : "border-input bg-transparent",
              )}
            >
              <BrainCircuit size={16} className={cn(chainPushOn ? "text-amber-500" : "text-muted-foreground")} />
              <span className={cn("text-sm font-semibold", chainPushOn ? "text-amber-700 dark:text-amber-400" : "text-foreground")}>
                {t("config.imChainPush")}
              </span>
              <Switch
                checked={chainPushOn}
                onCheckedChange={(v) =>
                  setEnvDraft((d) => envSet(d, "IM_CHAIN_PUSH", String(v)))
                }
              />
              <span className={cn("text-sm w-8 font-semibold", chainPushOn ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}>
                {chainPushOn ? "ON" : "OFF"}
              </span>
            </label>
            <span className="inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400">
              <AlertCircle size={12} className="shrink-0" />
              {t("config.imChainPushHelp")}
            </span>
          </div>
        )}
      </div>

      {/* IM Platform overview */}
      <div className="mt-4 space-y-2">
        <span className="text-sm font-bold text-foreground">
          {t("config.imPlatformOverview")}
        </span>
        <div className="flex flex-wrap gap-3">
          {PLATFORMS.map((p) => {
            const Logo = p.logo;
            const needsTranslation = p.title.startsWith("config.");
            const title = needsTranslation ? t(p.title) : p.title;
            return (
              <div
                key={p.id}
                className="flex items-center gap-2.5 rounded-lg border px-3 py-2.5 bg-card hover:bg-accent/50 transition-colors"
              >
                <Logo size={28} />
                <span className="font-medium text-sm">{title}</span>
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <a
                        href={p.docUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-muted-foreground hover:text-foreground transition-colors"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <ExternalLink size={13} />
                      </a>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="text-xs">
                      {t("config.imDoc")} — {p.docUrl}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            );
          })}
        </div>
      </div>

      <div className="border-t mt-4 mb-5" />

      {/* Bot guide */}
      {!wizardMode && (
        <div className="flex items-start gap-2.5 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/30 px-4 py-3 mb-4">
          <Info size={16} className="shrink-0 mt-0.5 text-blue-500" />
          <p className="text-sm text-muted-foreground leading-relaxed">
            {t("config.imBotGuide")}
          </p>
        </div>
      )}

      {/* Bot config */}
      {!wizardMode && (
        <BotConfigTab
          apiBase={apiBaseUrl ?? DEFAULT_API}
          multiAgentEnabled={multiAgentEnabled}
          onRequestRestart={onRequestRestart}
          venvDir={venvDir}
          apiBaseUrl={apiBaseUrl}
        />
      )}
    </div>
  );
}
