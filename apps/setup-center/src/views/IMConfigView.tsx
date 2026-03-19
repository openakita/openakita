import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { FieldText, FieldBool, TelegramPairingCodeHint } from "../components/EnvFields";
import { FeishuQRModal } from "../components/FeishuQRModal";
import { QQBotQRModal } from "../components/QQBotQRModal";
import { WecomQRModal } from "../components/WecomQRModal";
import { IconBook, IconBot, IconIM, LogoTelegram, LogoFeishu, LogoWework, LogoDingtalk, LogoQQ, LogoOneBot } from "../icons";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import type { EnvMap } from "../types";
import type { IMBot } from "./im-shared";
import { envGet, envSet } from "../utils";
import { copyToClipboard } from "../utils/clipboard";
import { BotConfigTab } from "./IMView";
import { cn } from "@/lib/utils";
import { BookOpen, ClipboardCopy } from "lucide-react";

type IMConfigViewProps = {
  envDraft: EnvMap;
  setEnvDraft: (updater: (prev: EnvMap) => EnvMap) => void;
  busy?: string | null;
  currentWorkspaceId: string | null;
  venvDir?: string;
  imDisabled?: boolean;
  onToggleIM?: () => void;
  multiAgentEnabled?: boolean;
  apiBaseUrl?: string;
  onRequestRestart?: () => void;
  wizardMode?: boolean;
  onNavigateToBotConfig?: (presetType: string) => void;
  pendingBots?: IMBot[];
  onPendingBotsChange?: React.Dispatch<React.SetStateAction<IMBot[]>>;
};

const DEFAULT_API = "http://127.0.0.1:18900";

export function IMConfigView(props: IMConfigViewProps) {
  const {
    envDraft, setEnvDraft, busy = null, currentWorkspaceId, venvDir = "",
    imDisabled = false, onToggleIM,
    multiAgentEnabled = false, apiBaseUrl, onRequestRestart, wizardMode = false,
  } = props;
  const { t } = useTranslation();
  const [showFeishuQR, setShowFeishuQR] = useState(false);
  const [showWecomQR, setShowWecomQR] = useState(false);
  const [showQQBotQR, setShowQQBotQR] = useState(false);
  const [configTab, setConfigTab] = useState<"channels" | "bots">("channels");

  const showBodyInChannels = !wizardMode && !multiAgentEnabled;
  const showDocRow = !wizardMode;

  const enabledChannels: string[] = [];
  if (envGet(envDraft, "TELEGRAM_ENABLED", "false").toLowerCase() === "true") enabledChannels.push("telegram");
  if (envGet(envDraft, "FEISHU_ENABLED", "false").toLowerCase() === "true") enabledChannels.push("feishu");
  if (envGet(envDraft, "DINGTALK_ENABLED", "false").toLowerCase() === "true") enabledChannels.push("dingtalk");
  if (envGet(envDraft, "QQBOT_ENABLED", "false").toLowerCase() === "true") enabledChannels.push("qqbot");
  if (envGet(envDraft, "ONEBOT_ENABLED", "false").toLowerCase() === "true") enabledChannels.push("onebot_reverse");
  if (envGet(envDraft, "WEWORK_ENABLED", "false").toLowerCase() === "true"
      || envGet(envDraft, "WEWORK_WS_ENABLED", "false").toLowerCase() === "true") enabledChannels.push("wework_ws");

  const _envBase = { envDraft, onEnvChange: setEnvDraft, busy };
  const FT = (p: { k: string; label: string; placeholder?: string; help?: string; type?: "text" | "password" }) =>
    <FieldText key={p.k} {...p} {..._envBase} />;
  const FB = (p: { k: string; label: string; help?: string; defaultValue?: boolean }) =>
    <FieldBool key={p.k} {...p} {..._envBase} />;

  const channels = [
    {
      title: "Telegram",
      appType: t("config.imTypeLongPolling"),
      logo: <LogoTelegram size={22} />,
      enabledKey: "TELEGRAM_ENABLED",
      docUrl: "https://t.me/BotFather",
      needPublicIp: false,
      body: (
        <>
          {FT({ k: "TELEGRAM_BOT_TOKEN", label: t("config.imBotToken"), placeholder: "BotFather token", type: "password" })}
          {FT({ k: "TELEGRAM_PROXY", label: t("config.imProxy"), placeholder: "http://127.0.0.1:7890" })}
          {FB({ k: "TELEGRAM_REQUIRE_PAIRING", label: t("config.imPairing") })}
          {FT({ k: "TELEGRAM_PAIRING_CODE", label: t("config.imPairingCode"), placeholder: t("config.imPairingCodeHint") })}
          <TelegramPairingCodeHint currentWorkspaceId={currentWorkspaceId} envDraft={envDraft} onEnvChange={setEnvDraft} />
          {FT({ k: "TELEGRAM_WEBHOOK_URL", label: "Webhook URL", placeholder: "https://..." })}
        </>
      ),
    },
    {
      title: t("config.imFeishu"),
      appType: t("config.imTypeCustomApp"),
      logo: <LogoFeishu size={22} />,
      enabledKey: "FEISHU_ENABLED",
      docUrl: "https://open.feishu.cn/",
      needPublicIp: false,
      body: (
        <>
          {venvDir && (
            <Button variant="outline" size="sm" className="mb-2" onClick={() => setShowFeishuQR(true)}>
              {t("feishu.qrScanCreate")}
            </Button>
          )}
          {FT({ k: "FEISHU_APP_ID", label: "App ID" })}
          {FT({ k: "FEISHU_APP_SECRET", label: "App Secret", type: "password" })}
          <div className="border-t my-2" />
          {FB({ k: "FEISHU_STREAMING_ENABLED", label: t("feishu.streaming"), defaultValue: true })}
          {envGet(envDraft, "FEISHU_STREAMING_ENABLED", "true").toLowerCase() === "true" && (
            FB({ k: "FEISHU_GROUP_STREAMING", label: t("feishu.groupStreaming"), defaultValue: true })
          )}
          <div className="mt-2 space-y-1">
            <Label>{t("feishu.groupMode")}</Label>
            <ToggleGroup type="single" variant="outline" size="sm"
              value={envGet(envDraft, "FEISHU_GROUP_RESPONSE_MODE", "mention_only")}
              onValueChange={(v) => { if (v) setEnvDraft((d) => envSet(d, "FEISHU_GROUP_RESPONSE_MODE", v)); }}
              className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground"
            >
              {(["mention_only", "smart", "always"] as const).map((m) => (
                <ToggleGroupItem key={m} value={m}>{t(`feishu.groupMode_${m}`)}</ToggleGroupItem>
              ))}
            </ToggleGroup>
            <p className="text-[11px] text-muted-foreground">
              {t(`feishu.groupModeHint_${envGet(envDraft, "FEISHU_GROUP_RESPONSE_MODE", "mention_only")}`)}
            </p>
          </div>
        </>
      ),
    },
    (() => {
      const weworkMode = (envDraft["WEWORK_MODE"] || "websocket") as "http" | "websocket";
      const isWs = weworkMode === "websocket";
      return {
        title: t("config.imWework"),
        appType: isWs ? t("config.imTypeSmartBotWs") : t("config.imTypeSmartBot"),
        logo: <LogoWework size={22} />,
        enabledKey: isWs ? "WEWORK_WS_ENABLED" : "WEWORK_ENABLED",
        docUrl: "https://work.weixin.qq.com/",
        needPublicIp: !isWs,
        body: (
          <>
            <div className="mb-2 space-y-1">
              <Label>{t("config.imWeworkMode")}</Label>
              <ToggleGroup type="single" variant="outline" size="sm" value={weworkMode} onValueChange={(v) => {
                if (!v) return;
                const m = v as "http" | "websocket";
                const oldKey = isWs ? "WEWORK_WS_ENABLED" : "WEWORK_ENABLED";
                const newKey = m === "websocket" ? "WEWORK_WS_ENABLED" : "WEWORK_ENABLED";
                setEnvDraft((d) => {
                  const wasEnabled = (d[oldKey] || "false").toLowerCase() === "true";
                  const next: Record<string, string> = { ...d, WEWORK_MODE: m };
                  if (wasEnabled && oldKey !== newKey) { next[oldKey] = "false"; next[newKey] = "true"; }
                  return next;
                });
              }} className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                <ToggleGroupItem value="http">{t("config.imWeworkModeHttp")}</ToggleGroupItem>
                <ToggleGroupItem value="websocket">{t("config.imWeworkModeWs")}</ToggleGroupItem>
              </ToggleGroup>
              <p className="text-[11px] text-muted-foreground">
                {isWs ? t("config.imWeworkModeWsHint") : t("config.imWeworkModeHttpHint")}
              </p>
            </div>
            {isWs ? (
              <>
                {venvDir && (
                  <Button variant="outline" size="sm" className="mb-2" onClick={() => setShowWecomQR(true)}>
                    {t("wecom.qrScanConfig")}
                  </Button>
                )}
                {FT({ k: "WEWORK_WS_BOT_ID", label: t("config.imWeworkBotId"), help: t("config.imWeworkBotIdHelp") })}
                {FT({ k: "WEWORK_WS_SECRET", label: t("config.imWeworkSecret"), type: "password", help: t("config.imWeworkSecretHelp") })}
              </>
            ) : (
              <>
                {FT({ k: "WEWORK_CORP_ID", label: "Corp ID", help: t("config.imWeworkCorpIdHelp") })}
                {FT({ k: "WEWORK_TOKEN", label: "Callback Token", help: t("config.imWeworkTokenHelp") })}
                {FT({ k: "WEWORK_ENCODING_AES_KEY", label: "EncodingAESKey", type: "password", help: t("config.imWeworkAesKeyHelp") })}
                {FT({ k: "WEWORK_CALLBACK_PORT", label: t("config.imCallbackPort"), placeholder: "9880" })}
                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                  {t("config.imWeworkCallbackUrlHint")}<code className="bg-muted px-1.5 py-0.5 rounded text-[11px]">http://your-domain:9880/callback</code>
                </p>
              </>
            )}
          </>
        ),
      };
    })(),
    {
      title: t("config.imDingtalk"),
      appType: t("config.imTypeInternalApp"),
      logo: <LogoDingtalk size={22} />,
      enabledKey: "DINGTALK_ENABLED",
      docUrl: "https://open.dingtalk.com/",
      needPublicIp: false,
      body: (
        <>
          {FT({ k: "DINGTALK_CLIENT_ID", label: "Client ID" })}
          {FT({ k: "DINGTALK_CLIENT_SECRET", label: "Client Secret", type: "password" })}
        </>
      ),
    },
    {
      title: t("config.imQQBot"),
      appType: `${t("config.imTypeQQBot")} (${(envDraft["QQBOT_MODE"] || "websocket") === "webhook" ? "Webhook" : "WebSocket"})`,
      logo: <LogoQQ size={22} />,
      enabledKey: "QQBOT_ENABLED",
      docUrl: "https://bot.q.qq.com/wiki/develop/api-v2/",
      needPublicIp: false,
      body: (
        <>
          {venvDir && (
            <Button variant="outline" size="sm" className="mb-2" onClick={() => setShowQQBotQR(true)}>
              {t("qqbot.qrScanCreate")}
            </Button>
          )}
          {FT({ k: "QQBOT_APP_ID", label: "AppID", placeholder: t("config.imQQBotCredentialHint") })}
          {FT({ k: "QQBOT_APP_SECRET", label: "AppSecret", type: "password", placeholder: t("config.imQQBotCredentialHint") })}
          {FB({ k: "QQBOT_SANDBOX", label: t("config.imQQBotSandbox") })}
          <div className="mt-2 space-y-1">
            <Label>{t("config.imQQBotMode")}</Label>
            <ToggleGroup type="single" variant="outline" size="sm" value={envDraft["QQBOT_MODE"] || "websocket"} onValueChange={(v) => { if (v) setEnvDraft((d) => ({ ...d, QQBOT_MODE: v })); }} className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
              <ToggleGroupItem value="websocket">WebSocket</ToggleGroupItem>
              <ToggleGroupItem value="webhook">Webhook</ToggleGroupItem>
            </ToggleGroup>
            <p className="text-[11px] text-muted-foreground">
              {(envDraft["QQBOT_MODE"] || "websocket") === "websocket"
                ? t("config.imQQBotModeWsHint")
                : t("config.imQQBotModeWhHint")}
            </p>
          </div>
          {(envDraft["QQBOT_MODE"] === "webhook") && (
            <>
              {FT({ k: "QQBOT_WEBHOOK_PORT", label: t("config.imQQBotWebhookPort"), placeholder: "9890" })}
              {FT({ k: "QQBOT_WEBHOOK_PATH", label: t("config.imQQBotWebhookPath"), placeholder: "/qqbot/callback" })}
            </>
          )}
        </>
      ),
    },
    (() => {
      const obMode = (envDraft["ONEBOT_MODE"] || "reverse") as "reverse" | "forward";
      const isReverse = obMode === "reverse";
      return {
        title: "OneBot",
        appType: isReverse ? t("config.imTypeOneBotReverse") : t("config.imTypeOneBotForward"),
        logo: <LogoOneBot size={22} />,
        enabledKey: "ONEBOT_ENABLED",
        docUrl: "https://github.com/botuniverse/onebot-11",
        needPublicIp: false,
        body: (
          <>
            <div className="mb-2 space-y-1">
              <Label>{t("config.imOneBotMode")}</Label>
              <ToggleGroup type="single" variant="outline" size="sm" value={obMode} onValueChange={(v) => { if (v) setEnvDraft((d) => ({ ...d, ONEBOT_MODE: v })); }} className="[&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                <ToggleGroupItem value="reverse">{t("config.imOneBotModeReverse")}</ToggleGroupItem>
                <ToggleGroupItem value="forward">{t("config.imOneBotModeForward")}</ToggleGroupItem>
              </ToggleGroup>
              <p className="text-[11px] text-muted-foreground">
                {isReverse ? t("config.imOneBotModeReverseHint") : t("config.imOneBotModeForwardHint")}
              </p>
            </div>
            {isReverse ? (
              <>
                {FT({ k: "ONEBOT_REVERSE_HOST", label: t("config.imOneBotReverseHost"), placeholder: "0.0.0.0" })}
                {FT({ k: "ONEBOT_REVERSE_PORT", label: t("config.imOneBotReversePort"), placeholder: "6700" })}
              </>
            ) : (
              FT({ k: "ONEBOT_WS_URL", label: "WebSocket URL", placeholder: "ws://127.0.0.1:8080" })
            )}
            {FT({ k: "ONEBOT_ACCESS_TOKEN", label: "Access Token", type: "password", placeholder: t("config.imOneBotTokenHint") })}
          </>
        ),
      };
    })(),
  ];

  return (
    <>
      {showFeishuQR && (
        <FeishuQRModal
          venvDir={venvDir}
          onClose={() => setShowFeishuQR(false)}
          onSuccess={(appId, appSecret) => {
            setEnvDraft((d) => envSet(envSet(d, "FEISHU_APP_ID", appId), "FEISHU_APP_SECRET", appSecret));
            setShowFeishuQR(false);
            toast.success(t("feishu.qrSuccess"));
          }}
        />
      )}
      {showWecomQR && (
        <WecomQRModal
          venvDir={venvDir}
          onClose={() => setShowWecomQR(false)}
          onSuccess={(botId, secret) => {
            setEnvDraft((d) => envSet(envSet(d, "WEWORK_WS_BOT_ID", botId), "WEWORK_WS_SECRET", secret));
            setShowWecomQR(false);
            toast.success(t("wecom.qrSuccess"));
          }}
        />
      )}
      {showQQBotQR && (
        <QQBotQRModal
          venvDir={venvDir}
          onClose={() => setShowQQBotQR(false)}
          onSuccess={(appId, appSecret) => {
            setEnvDraft((d) => envSet(envSet(d, "QQBOT_APP_ID", appId), "QQBOT_APP_SECRET", appSecret));
            setShowQQBotQR(false);
            toast.success(t("qqbot.qrSuccess"));
          }}
        />
      )}

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CardTitle>{t("config.imTitle")}</CardTitle>
              <Button variant="outline" size="sm" className="h-7 gap-1 text-xs"
                onClick={async () => { const ok = await copyToClipboard("https://github.com/anthropic-lab/openakita/blob/main/docs/im-channels.md"); if (ok) toast.success(t("config.imGuideDocCopied")); }}
                title={t("config.imGuideDoc")}
              >
                <BookOpen size={13} />{t("config.imGuideDoc")}
              </Button>
            </div>
            {onToggleIM && (
              <label className="inline-flex items-center gap-2 text-sm text-muted-foreground cursor-pointer select-none">
                <span>{imDisabled ? t("config.imDisabledLabel", { defaultValue: "已禁用" }) : t("config.imEnabledLabel", { defaultValue: "已启用" })}</span>
                <Switch checked={!imDisabled} onCheckedChange={() => onToggleIM()} />
              </label>
            )}
          </div>
          <CardDescription>{t("config.imHint")}</CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {!wizardMode && (
            <>
              {FB({ k: "IM_CHAIN_PUSH", label: t("config.imChainPush"), help: t("config.imChainPushHelp") })}
              <div className="border-t" />
            </>
          )}

          {/* Multi-agent mode: tab switcher */}
          {!wizardMode && multiAgentEnabled && (
            <ToggleGroup type="single" variant="outline" value={configTab} onValueChange={(v) => { if (v) setConfigTab(v as "channels" | "bots"); }}>
              <ToggleGroupItem value="channels" className="gap-1.5 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                <IconIM size={14} />{t("config.imTabChannels")}
              </ToggleGroupItem>
              <ToggleGroupItem value="bots" className="gap-1.5 data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary">
                <IconBot size={14} />{t("config.imTabBots")}
              </ToggleGroupItem>
            </ToggleGroup>
          )}

          {/* Channel list */}
          {(!multiAgentEnabled || configTab === "channels" || wizardMode) && channels.map((c) => {
            const enabled = envGet(envDraft, c.enabledKey, "false").toLowerCase() === "true";
            return (
              <Card key={c.enabledKey} className="py-0">
                <CardContent className="py-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      {c.logo}
                      <span className="font-semibold text-sm">{c.title}</span>
                      <Badge variant="secondary" className="text-[10px] px-1.5 py-0">{c.appType}</Badge>
                      {c.needPublicIp && <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-400">{t("config.imNeedPublicIp")}</Badge>}
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <Checkbox
                        checked={enabled}
                        onCheckedChange={(v) => setEnvDraft((m) => envSet(m, c.enabledKey, String(!!v)))}
                      />
                      <span className="text-sm">{t("config.enable")}</span>
                    </label>
                  </div>
                  {showDocRow && (
                    <div className="flex items-center gap-1.5">
                      <Button variant="ghost" size="sm" className="h-6 px-2 text-[11px] gap-1 text-muted-foreground"
                        title={c.docUrl}
                        onClick={async () => { const ok = await copyToClipboard(c.docUrl); if (ok) toast.success(t("config.imDocCopied")); }}
                      >
                        <ClipboardCopy size={12} />{t("config.imDoc")}
                      </Button>
                      <span className="text-[11px] text-muted-foreground/60 select-all">{c.docUrl}</span>
                    </div>
                  )}
                  {showBodyInChannels && enabled && (
                    <>
                      <div className="border-t" />
                      <div className="flex flex-col gap-2.5">{c.body}</div>
                    </>
                  )}
                </CardContent>
              </Card>
            );
          })}

          {/* Bot config tab (multi-agent only) */}
          {!wizardMode && multiAgentEnabled && configTab === "bots" && (
            <BotConfigTab
              apiBase={apiBaseUrl ?? DEFAULT_API}
              multiAgentEnabled={true}
              onRequestRestart={onRequestRestart}
              venvDir={venvDir}
              apiBaseUrl={apiBaseUrl}
              enabledChannels={enabledChannels}
            />
          )}
        </CardContent>
      </Card>
    </>
  );
}
