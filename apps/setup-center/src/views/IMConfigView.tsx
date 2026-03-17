import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { FieldText, FieldBool, TelegramPairingCodeHint } from "../components/EnvFields";
import { IconBook, IconClipboard, LogoTelegram, LogoFeishu, LogoWework, LogoDingtalk, LogoQQ } from "../icons";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { EnvMap } from "../types";
import type { IMBot } from "./im-shared";
import { envGet, envSet } from "../utils";
import { copyToClipboard } from "../utils/clipboard";

type IMConfigViewProps = {
  envDraft: EnvMap;
  setEnvDraft: (updater: (prev: EnvMap) => EnvMap) => void;
  busy?: string | null;
  currentWorkspaceId: string | null;
  imDisabled?: boolean;
  onToggleIM?: () => void;
  apiBaseUrl?: string;
  onNavigateToBotConfig?: (presetType: string) => void;
  pendingBots?: IMBot[];
  onPendingBotsChange?: React.Dispatch<React.SetStateAction<IMBot[]>>;
};

export function IMConfigView(props: IMConfigViewProps) {
  const { envDraft, setEnvDraft, busy = null, currentWorkspaceId, imDisabled = false, onToggleIM } = props;
  const { t } = useTranslation();

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
          <TelegramPairingCodeHint currentWorkspaceId={currentWorkspaceId} />
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
          {FT({ k: "FEISHU_APP_ID", label: "App ID" })}
          {FT({ k: "FEISHU_APP_SECRET", label: "App Secret", type: "password" })}
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
            <div style={{ marginBottom: 8 }}>
              <div className="label">{t("config.imWeworkMode")}</div>
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
              }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                <ToggleGroupItem value="http">{t("config.imWeworkModeHttp")}</ToggleGroupItem>
                <ToggleGroupItem value="websocket">{t("config.imWeworkModeWs")}</ToggleGroupItem>
              </ToggleGroup>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                {isWs ? t("config.imWeworkModeWsHint") : t("config.imWeworkModeHttpHint")}
              </div>
            </div>
            {isWs ? (
              <>
                {FT({ k: "WEWORK_WS_BOT_ID", label: t("config.imWeworkBotId"), help: t("config.imWeworkBotIdHelp") })}
                {FT({ k: "WEWORK_WS_SECRET", label: t("config.imWeworkSecret"), type: "password", help: t("config.imWeworkSecretHelp") })}
              </>
            ) : (
              <>
                {FT({ k: "WEWORK_CORP_ID", label: "Corp ID", help: t("config.imWeworkCorpIdHelp") })}
                {FT({ k: "WEWORK_TOKEN", label: "Callback Token", help: t("config.imWeworkTokenHelp") })}
                {FT({ k: "WEWORK_ENCODING_AES_KEY", label: "EncodingAESKey", type: "password", help: t("config.imWeworkAesKeyHelp") })}
                {FT({ k: "WEWORK_CALLBACK_PORT", label: t("config.imCallbackPort"), placeholder: "9880" })}
                <div className="fieldHint" style={{ fontSize: 12, color: "var(--text3)", margin: "4px 0 0 0", lineHeight: 1.6 }}>
                  {t("config.imWeworkCallbackUrlHint")}<code style={{ background: "var(--bg2)", padding: "1px 5px", borderRadius: 4, fontSize: 11 }}>http://your-domain:9880/callback</code>
                </div>
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
      title: "QQ 机器人",
      appType: `${t("config.imTypeQQBot")} (${(envDraft["QQBOT_MODE"] || "websocket") === "webhook" ? "Webhook" : "WebSocket"})`,
      logo: <LogoQQ size={22} />,
      enabledKey: "QQBOT_ENABLED",
      docUrl: "https://bot.q.qq.com/wiki/develop/api-v2/",
      needPublicIp: false,
      body: (
        <>
          {FT({ k: "QQBOT_APP_ID", label: "AppID", placeholder: "q.qq.com 开发设置" })}
          {FT({ k: "QQBOT_APP_SECRET", label: "AppSecret", type: "password", placeholder: "q.qq.com 开发设置" })}
          {FB({ k: "QQBOT_SANDBOX", label: t("config.imQQBotSandbox") })}
          <div style={{ marginTop: 8 }}>
            <div className="label">{t("config.imQQBotMode")}</div>
            <ToggleGroup type="single" variant="outline" size="sm" value={envDraft["QQBOT_MODE"] || "websocket"} onValueChange={(v) => { if (v) setEnvDraft((d) => ({ ...d, QQBOT_MODE: v })); }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
              <ToggleGroupItem value="websocket">WebSocket</ToggleGroupItem>
              <ToggleGroupItem value="webhook">Webhook</ToggleGroupItem>
            </ToggleGroup>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
              {(envDraft["QQBOT_MODE"] || "websocket") === "websocket"
                ? t("config.imQQBotModeWsHint")
                : t("config.imQQBotModeWhHint")}
            </div>
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
        logo: <LogoQQ size={22} />,
        enabledKey: "ONEBOT_ENABLED",
        docUrl: "https://github.com/botuniverse/onebot-11",
        needPublicIp: false,
        body: (
          <>
            <div style={{ marginBottom: 8 }}>
              <div className="label">{t("config.imOneBotMode")}</div>
              <ToggleGroup type="single" variant="outline" size="sm" value={obMode} onValueChange={(v) => { if (v) setEnvDraft((d) => ({ ...d, ONEBOT_MODE: v })); }} className="mt-1 [&_[data-state=on]]:bg-primary [&_[data-state=on]]:text-primary-foreground">
                <ToggleGroupItem value="reverse">{t("config.imOneBotModeReverse")}</ToggleGroupItem>
                <ToggleGroupItem value="forward">{t("config.imOneBotModeForward")}</ToggleGroupItem>
              </ToggleGroup>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                {isReverse ? t("config.imOneBotModeReverseHint") : t("config.imOneBotModeForwardHint")}
              </div>
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
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div className="cardTitle" style={{ marginBottom: 0 }}>{t("config.imTitle")}</div>
            <button className="btnSmall" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}
              onClick={async () => { const ok = await copyToClipboard("https://github.com/anthropic-lab/openakita/blob/main/docs/im-channels.md"); if (ok) toast.success(t("config.imGuideDocCopied")); }}
              title={t("config.imGuideDoc")}
            ><IconBook size={13} />{t("config.imGuideDoc")}</button>
          </div>
          {onToggleIM && (
            <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--muted)", cursor: "pointer" }}>
              <span>{imDisabled ? t("config.imDisabledLabel", { defaultValue: "已禁用" }) : t("config.imEnabledLabel", { defaultValue: "已启用" })}</span>
              <div
                onClick={onToggleIM}
                style={{
                  width: 40, height: 22, borderRadius: 11, cursor: "pointer",
                  background: imDisabled ? "var(--line)" : "var(--ok)",
                  position: "relative", transition: "background 0.2s",
                }}
              >
                <div style={{
                  width: 18, height: 18, borderRadius: 9, background: "#fff",
                  position: "absolute", top: 2,
                  left: imDisabled ? 2 : 20,
                  transition: "left 0.2s", boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                }} />
              </div>
            </label>
          )}
        </div>
        <div className="cardHint">{t("config.imHint")}</div>
        <div className="divider" />

        {FB({ k: "IM_CHAIN_PUSH", label: t("config.imChainPush"), help: t("config.imChainPushHelp") })}
        <div className="divider" />

        {channels.map((c) => {
          const enabled = envGet(envDraft, c.enabledKey, "false").toLowerCase() === "true";
          return (
            <div key={c.enabledKey} className="card" style={{ marginTop: 10 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div className="row" style={{ alignItems: "center", gap: 10 }}>
                  {c.logo}
                  <span className="label" style={{ marginBottom: 0 }}>{c.title}</span>
                  <span className="pill" style={{ fontSize: 10, padding: "1px 6px", background: "#f1f5f9", color: "#475569" }}>{c.appType}</span>
                  {c.needPublicIp && <span className="pill" style={{ fontSize: 10, padding: "1px 6px", background: "#fef3c7", color: "#92400e" }}>{t("config.imNeedPublicIp")}</span>}
                </div>
                <label className="pill" style={{ cursor: "pointer", userSelect: "none" }}>
                  <input style={{ width: 16, height: 16 }} type="checkbox" checked={enabled}
                    onChange={(e) => setEnvDraft((m) => envSet(m, c.enabledKey, String(e.target.checked)))} />
                  {t("config.enable")}
                </label>
              </div>
              <div className="row" style={{ alignItems: "center", gap: 6, marginTop: 4 }}>
                <button className="btnSmall"
                  style={{ fontSize: 11, padding: "2px 8px", display: "inline-flex", alignItems: "center", gap: 3 }}
                  title={c.docUrl}
                  onClick={async () => { const ok = await copyToClipboard(c.docUrl); if (ok) toast.success(t("config.imDocCopied")); }}
                ><IconClipboard size={12} />{t("config.imDoc")}</button>
                <span className="help" style={{ fontSize: 11, userSelect: "all", opacity: 0.6 }}>{c.docUrl}</span>
              </div>
              {enabled && (
                <>
                  <div className="divider" />
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>{c.body}</div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
