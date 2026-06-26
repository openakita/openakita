import { memo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ChatMessage, ChatAttachment, MdModules } from "../utils/chatTypes";
import { stripLegacySummary } from "../utils/chatHelpers";
import { resolveMessageParts, hasRenderableBody } from "../utils/messageParts";
import { formatTime } from "../../../utils";
import { AttachmentPreview } from "./AttachmentPreview";
import { SpinnerTipDisplay } from "./SpinnerTipDisplay";
import { MarkdownContent } from "./MarkdownContent";
import { MessageParts } from "./MessageParts";
import { useSourceTagFormatter, extractTrailingSourceTag, SourceBadge } from "./SourceBadge";
import { IconClipboard, IconEdit, IconRefresh, IconRewind, IconChevronRight } from "../../../icons";

export const MessageBubble = memo(function MessageBubble({
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
  const usageTotal = msg.usage
    ? (msg.usage.total_tokens ?? (msg.usage.input_tokens + msg.usage.output_tokens))
    : 0;
  const showUsage = Boolean(msg.usage && usageTotal > 0);
  const usagePrefix = msg.usage?.usage_estimated ? "~" : "";

  // Peel off the trailing [来源:X] tag (assistant only) so the badge can ride
  // the footer line instead of taking its own paragraph at the bottom of the
  // bubble. User messages never carry these tags, so the helper is no-op there.
  const rawBody = isUser ? msg.content : stripLegacySummary(msg.content || "");
  const { stripped: bodyContent, trailingType: footerSourceType } =
    isUser ? { stripped: rawBody, trailingType: null } : extractTrailingSourceTag(rawBody);

  const parts = isAssistant ? resolveMessageParts(msg) : [];
  // The local "view process" reveal can override the global hide-chain toggle
  // for this one bubble, so the effective value drives both rendering and the
  // emptiness check.
  const effShowChain = showChain || revealChain;
  // Drives the streaming loading indicator: keep it visible only while the
  // bubble has nothing else to render (covers showChain=off where the chain is
  // hidden, without double-stacking under a plan / ask_user / artifact card).
  const hasBody = isAssistant && hasRenderableBody(msg, parts, effShowChain, bodyContent);
  // A finished assistant turn that renders nothing visible yet still hides a
  // reasoning chain (global toggle off) would otherwise be a blank bubble —
  // offer a plain one-line handle into the process instead. Only when revealing
  // will actually surface the chain: the chain must be non-empty AND carried by
  // a `reasoning` part (the only part `showChain` gates), so the handle can
  // never become a dead click that leaves the bubble blank.
  const canRevealChain =
    !!msg.thinkingChain && msg.thinkingChain.length > 0 && parts.some((p) => p.kind === "reasoning");
  const showRevealHandle = isAssistant && !msg.streaming && !hasBody && canRevealChain;

  return (
    <div className="msgBubbleWrap" style={{ display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : "flex-start", marginBottom: 16, position: "relative" }}>
      {!isUser && msg.agentName && (
        <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.5, marginBottom: 2, paddingLeft: 2 }}>
          {msg.agentName}
        </div>
      )}
      <div
        style={{
          maxWidth: "85%",
          padding: isUser ? "10px 16px" : "12px 16px",
          borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
          background: isUser ? "var(--brand)" : "var(--panel2)",
          color: isUser ? "#fff" : "var(--text)",
          border: isUser ? "none" : "1px solid var(--line)",
          boxShadow: isUser ? "var(--glow-shadow)" : "var(--shadow)",
          fontSize: 14,
          lineHeight: 1.7,
          wordBreak: "break-word",
        }}
      >
        {msg.attachments && msg.attachments.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {msg.attachments.map((att: ChatAttachment, i: number) => (
              <AttachmentPreview key={i} att={att} />
            ))}
          </div>
        )}

        {isAssistant ? (
          <>
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
        ) : (
          bodyContent && (
            <MarkdownContent
              content={formatSourceTags(bodyContent)}
              mdModules={mdModules}
              className={isUser ? "chatMdContent chatMdContentUser" : "chatMdContent"}
              streaming={!!msg.streaming}
            />
          )
        )}
      </div>
      <div className="msgActions" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, marginTop: 2, paddingLeft: 2, paddingRight: 2 }}>
        {footerSourceType && <SourceBadge type={footerSourceType} />}
        <span style={{ opacity: 0.35 }}>{formatTime(msg.timestamp)}</span>
        {showUsage && msg.usage && (
          <span style={{ opacity: 0.25 }} title={`${msg.usage.usage_estimated ? "Estimated · " : ""}In: ${msg.usage.input_tokens} · Out: ${msg.usage.output_tokens}`}>
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
