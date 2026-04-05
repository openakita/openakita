import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { LogoTelegram, LogoFeishu, LogoWework, LogoDingtalk, LogoQQ, LogoOneBot, LogoWechat } from "../icons";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import type { EnvMap } from "../types";
import { envGet, envSet } from "../utils";
import { copyToClipboard } from "../utils/clipboard";
import { BotConfigTab } from "./IMView";
import { cn } from "@/lib/utils";
import { AlertCircle, BookOpen, BrainCircuit, ExternalLink, Info, Terminal } from "lucide-react";
import { openExternalUrl } from "../platform";

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
  const [showCmdRef, setShowCmdRef] = useState(false);

  const chainPushOn = envGet(envDraft, "IM_CHAIN_PUSH", "false").toLowerCase() === "true";

  return (
    <div className="card">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 overflow-x-auto">
        <div className="min-w-0">
          <h3 className="flex min-w-max items-center gap-2 text-base font-bold tracking-tight">
            <span className="max-w-[240px] truncate" title={t("config.imTitle")}>{t("config.imTitle")}</span>
            <Button
              variant="outline" size="sm" className="h-7 shrink-0 gap-1 text-xs"
              onClick={async () => {
                const ok = await copyToClipboard(
                  "https://github.com/anthropic-lab/openakita/blob/main/docs/im-channels.md",
                );
                if (ok) toast.success(t("config.imGuideDocCopied"));
              }}
              title={t("config.imGuideDoc")}
            >
              <BookOpen size={13} />
              <span className="hidden xl:inline">{t("config.imGuideDoc")}</span>
            </Button>
            <Button
              variant="outline" size="sm" className="h-7 shrink-0 gap-1 text-xs"
              onClick={() => setShowCmdRef(true)}
              title={t("config.imQuickCommands")}
            >
              <Terminal size={13} />
              <span className="hidden xl:inline">{t("config.imQuickCommands")}</span>
            </Button>
          </h3>
          <p className="mt-1 truncate text-sm text-muted-foreground" title={t("config.imHint")}>{t("config.imHint")}</p>
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
                      <span
                        className="text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                        onClick={(e) => {
                          e.stopPropagation();
                          openExternalUrl(p.docUrl);
                        }}
                      >
                        <ExternalLink size={13} />
                      </span>
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

      <QuickCommandsDialog open={showCmdRef} onOpenChange={setShowCmdRef} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Quick Commands Reference Dialog                                    */
/* ------------------------------------------------------------------ */

type CmdEntry = { cmd: string; desc: string };
type CmdCategory = { labelKey: string; icon: string; entries: CmdEntry[] };

const CMD_CATEGORIES: CmdCategory[] = [
  {
    labelKey: "imCmdCatTask", icon: "🎯",
    entries: [
      { cmd: "停止 / stop / /stop / kill", desc: "imCmdStop" },
      { cmd: "跳过 / skip / /skip", desc: "imCmdSkip" },
      { cmd: "——", desc: "imCmdInsert" },
    ],
  },
  {
    labelKey: "imCmdCatChat", icon: "💬",
    entries: [
      { cmd: "/new  /新话题", desc: "imCmdNew" },
      { cmd: "/help  /帮助", desc: "imCmdHelp" },
    ],
  },
  {
    labelKey: "imCmdCatModel", icon: "🤖",
    entries: [
      { cmd: "/model", desc: "imCmdModel" },
      { cmd: "/switch [name]", desc: "imCmdSwitch" },
      { cmd: "/restore", desc: "imCmdRestore" },
    ],
  },
  {
    labelKey: "imCmdCatThinking", icon: "🧠",
    entries: [
      { cmd: "/thinking [on|off|auto]", desc: "imCmdThinking" },
      { cmd: "/thinking_depth [low|medium|high]", desc: "imCmdThinkingDepth" },
      { cmd: "/chain [on|off]", desc: "imCmdChain" },
    ],
  },
  {
    labelKey: "imCmdCatMode", icon: "🔧",
    entries: [
      { cmd: "/模式  /mode", desc: "imCmdMode" },
    ],
  },
  {
    labelKey: "imCmdCatMultiAgent", icon: "👥",
    entries: [
      { cmd: "/切换  /switch", desc: "imCmdAgentSwitch" },
      { cmd: "/状态  /status", desc: "imCmdAgentStatus" },
      { cmd: "/重置  /agent_reset", desc: "imCmdAgentReset" },
    ],
  },
  {
    labelKey: "imCmdCatSystem", icon: "⚙️",
    entries: [
      { cmd: "/restart  /重启", desc: "imCmdRestart" },
    ],
  },
];

function QuickCommandsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Terminal size={18} />
            {t("config.imQuickCommandsTitle")}
          </DialogTitle>
          <DialogDescription>{t("config.imQuickCommandsDesc")}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 mt-2">
          {CMD_CATEGORIES.map((cat) => (
            <div
              key={cat.labelKey}
              className="rounded-lg border bg-card px-4 py-3"
            >
              <div className="text-sm font-semibold mb-2 flex items-center gap-1.5">
                <span>{cat.icon}</span>
                {t(`config.${cat.labelKey}`)}
              </div>
              <div className="space-y-1.5">
                {cat.entries.map((e, i) => (
                  <div key={i} className="flex items-baseline gap-3 text-sm">
                    <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-foreground">
                      {e.cmd}
                    </code>
                    <span className="text-muted-foreground text-xs">
                      {t(`config.${e.desc}`)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
