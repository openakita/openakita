import { memo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ChatMessage, MdModules } from "../utils/chatTypes";
import { stripLegacySummary } from "../utils/chatHelpers";
import { resolveMessageParts, hasRenderableBody } from "../utils/messageParts";
import { formatTime } from "../../../utils";
import { AttachmentPreview } from "./AttachmentPreview";
import { SpinnerTipDisplay } from "./SpinnerTipDisplay";
import { MarkdownContent } from "./MarkdownContent";
import { MessageParts } from "./MessageParts";
import { useSourceTagFormatter, extractTrailingSourceTag, SourceBadge } from "./SourceBadge";
import { IconClipboard, IconEdit, IconRefresh, IconRewind, IconChevronRight } from "../../../icons";

export const FlatMessageItem = memo(function FlatMessageItem({
  msg,
  onAskAnswer,
  onRetry,
  onEdit,
  onRegenerate,
  onRewind,
  isLast,
  apiBaseUrl,
  showChain = true,
  onSkipStep,
  onImagePreview,
  mdModules,
  conversationId,
  httpApiBase,
  onPlanStepAction,
}: {
  msg: ChatMessage;
  onAskAnswer?: (msgId: string, answer: string) => void;
  onRetry?: (msgId: string) => void;
  onEdit?: (msgId: string) => void;
  onRegenerate?: (msgId: string) => void;
  onRewind?: (msgId: string) => void;
  isLast?: boolean;
  apiBaseUrl?: string;
  showChain?: boolean;
  onSkipStep?: () => void;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
  mdModules?: MdModules | null;
  conversationId?: string;
  httpApiBase?: () => string;
  onPlanStepAction?: (action: "skip" | "retry", stepIdx: number, description: string) => void;
}) {
  const { t } = useTranslation();
  const formatSourceTags = useSourceTagFormatter();
  const [revealChain, setRevealChain] = useState(false);
  const isUser = msg.role === "user";
  const isAssistant = msg.role === "assistant";
  const isSystem = msg.role === "system";
  const usageTotal = msg.usage
    ? (msg.usage.total_tokens ?? (msg.usage.input_tokens + msg.usage.output_tokens))
    : 0;
  const showUsage = Boolean(msg.usage && usageTotal > 0);
  const usagePrefix = msg.usage?.usage_estimated ? "~" : "";

  // Peel off the trailing [来源:X] tag (assistant only) so the badge can ride
  // the footer line instead of taking its own paragraph at the bottom.
  const rawBody = stripLegacySummary(msg.content || "");
  const { stripped: bodyContent, trailingType: footerSourceType } =
    isUser || isSystem ? { stripped: rawBody, trailingType: null } : extractTrailingSourceTag(rawBody);

  if (isSystem) {
    return (
      <div className="flatMsgSystem">
        <span>{msg.content}</span>
      </div>
    );
  }

  const parts = isAssistant ? resolveMessageParts(msg) : [];
  // Local "view process" reveal can override the global hide-chain toggle for
  // this one bubble.
  const effShowChain = showChain || revealChain;
  // Keep the streaming loading indicator up only while nothing else renders, so
  // it never double-stacks under a plan / ask_user / artifact card and never
  // lingers as a fake spinner when the chain is hidden (showChain=off).
  const hasBody = isAssistant && hasRenderableBody(msg, parts, effShowChain, bodyContent);
  // Avoid a blank completed bubble when the only thing produced was a chain the
  // user chose to hide — surface a plain one-line handle into the process. Gate
  // it on the chain actually being revealable (non-empty AND carried by a
  // `reasoning` part) so the handle never becomes a dead click.
  const canRevealChain =
    !!msg.thinkingChain && msg.thinkingChain.length > 0 && parts.some((p) => p.kind === "reasoning");
  const showRevealHandle = isAssistant && !msg.streaming && !hasBody && canRevealChain;

  return (
    <div className={`flatMessage flatMsgItem ${isUser ? "flatMsgUser" : "flatMsgAssistant"}`}>
      {isUser && (
        <div className="flatUserContent">
          {msg.attachments && msg.attachments.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              {msg.attachments.map((att, i) => (
                <AttachmentPreview key={i} att={att} apiBaseUrl={apiBaseUrl} onImagePreview={onImagePreview} />
              ))}
            </div>
          )}
          <MarkdownContent
            content={formatSourceTags(msg.content)}
            mdModules={mdModules}
            className="chatMdContent"
            streaming={!!msg.streaming}
            apiBaseUrl={apiBaseUrl}
            onImagePreview={onImagePreview}
          />
        </div>
      )}

      {!isUser && (
        <>
          {msg.agentName && (
            <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.4, marginBottom: 4 }}>
              {msg.agentName}
            </div>
          )}

          {msg.streaming && !msg.content && showChain && msg.streamStatus && msg.thinkingChain && msg.thinkingChain.length > 0 && (
            <SpinnerTipDisplay statusText={msg.streamStatus} />
          )}

          <MessageParts
            msg={msg}
            parts={parts}
            bodyContent={bodyContent}
            formatSourceTags={formatSourceTags}
            mdModules={mdModules}
            showChain={effShowChain}
            forceExpandChain={revealChain}
            onSkipStep={onSkipStep}
            onImagePreview={onImagePreview}
            onAskAnswer={onAskAnswer}
            onPlanStepAction={onPlanStepAction}
            onRetry={onRetry}
            apiBaseUrl={apiBaseUrl}
            conversationId={conversationId}
            httpApiBase={httpApiBase}
          />

          {msg.streaming && !hasBody && (
            <div style={{ padding: "4px 0" }}>
              <div style={{ display: "flex", gap: 4 }}>
                <span className="dotBounce" style={{ animationDelay: "0s" }} />
                <span className="dotBounce" style={{ animationDelay: "0.15s" }} />
                <span className="dotBounce" style={{ animationDelay: "0.3s" }} />
              </div>
              <SpinnerTipDisplay statusText={msg.streamStatus} />
            </div>
          )}

          {showRevealHandle && (
            <div
              className="chainCollapsedSummary"
              onClick={() => setRevealChain(true)}
              role="button"
            >
              <IconChevronRight size={11} />
              <span>{t("chat.noBodyReveal")}</span>
            </div>
          )}
        </>
      )}

      <div className="msgActions" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, marginTop: 2 }}>
        {footerSourceType && <SourceBadge type={footerSourceType} />}
        <span style={{ opacity: 0.25 }}>{formatTime(msg.timestamp)}</span>
        {showUsage && msg.usage && (
          <span style={{ opacity: 0.18 }} title={`${msg.usage.usage_estimated ? "Estimated · " : ""}In: ${msg.usage.input_tokens} · Out: ${msg.usage.output_tokens}`}>
            {usagePrefix}{usageTotal} tokens
          </span>
        )}
        {!msg.streaming && msg.content && (
          <button className="msgActionBtn" onClick={() => navigator.clipboard.writeText(msg.content).catch(() => {})} title={t("chat.copyMessage", "复制")}><IconClipboard size={12} /></button>
        )}
        {isUser && !msg.streaming && onEdit && (
          <button className="msgActionBtn" onClick={() => onEdit(msg.id)} title={t("chat.edit", "编辑")}><IconEdit size={12} /></button>
        )}
        {isAssistant && !msg.streaming && onRegenerate && (
          <button className="msgActionBtn" onClick={() => onRegenerate(msg.id)} title={t("chat.regenerate", "重新生成")}><IconRefresh size={12} /></button>
        )}
        {!isLast && !msg.streaming && onRewind && (
          <button className="msgActionBtn" onClick={() => onRewind(msg.id)} title={t("chat.rewind", "回到这里")}><IconRewind size={12} /></button>
        )}
      </div>
    </div>
  );
});
