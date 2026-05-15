import { memo } from "react";
import { useTranslation } from "react-i18next";
import type { ChatMessage, ChatAttachment, MdModules } from "../utils/chatTypes";
import { stripLegacySummary } from "../utils/chatHelpers";
import { formatTime } from "../../../utils";
import { ThinkingChain, ThinkingBlock, ToolCallsGroup } from "./ThinkingChain";
import { ArtifactList } from "./Artifacts";
import { AskUserBlock } from "./AskUser";
import { ErrorCard } from "./ErrorCard";
import { AttachmentPreview } from "./AttachmentPreview";
import { SpinnerTipDisplay } from "./SpinnerTipDisplay";
import { SourceStrip } from "./SourceStrip";
import { PlanCard } from "./PlanCard";
import { MCPCallStrip } from "./MCPCallStrip";
import { useSourceTagFormatter } from "./SourceBadge";
import { IconClipboard, IconEdit, IconRefresh, IconRewind } from "../../../icons";

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
  const isUser = msg.role === "user";
  const isAssistant = msg.role === "assistant";
  const usageTotal = msg.usage
    ? (msg.usage.total_tokens ?? (msg.usage.input_tokens + msg.usage.output_tokens))
    : 0;
  const showUsage = Boolean(msg.usage && usageTotal > 0);
  const usagePrefix = msg.usage?.usage_estimated ? "~" : "";
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

        {msg.thinkingChain && msg.thinkingChain.length > 0 && (
          <ThinkingChain chain={msg.thinkingChain} streaming={!!msg.streaming} showChain={showChain} onSkipStep={onSkipStep} />
        )}

        {msg.streaming && !msg.content && msg.streamStatus && msg.thinkingChain && msg.thinkingChain.length > 0 && (
          <SpinnerTipDisplay statusText={msg.streamStatus} />
        )}

        {msg.thinking && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
          <ThinkingBlock content={msg.thinking} />
        )}

        <SourceStrip sources={msg.sources} conversationId={conversationId} httpApiBase={httpApiBase} />
        <MCPCallStrip calls={msg.mcpCalls} />

        {msg.todo && msg.todo.steps?.length > 0 && (
          <PlanCard plan={msg.todo} onStepAction={onPlanStepAction} />
        )}

        {msg.content && (isUser ? msg.content : stripLegacySummary(msg.content)) && (
          <div className={isUser ? "chatMdContent chatMdContentUser" : "chatMdContent"}>
            {mdModules ? (
              <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>
                {formatSourceTags(isUser ? msg.content : stripLegacySummary(msg.content))}
              </mdModules.ReactMarkdown>
            ) : (
              <div style={{ whiteSpace: "pre-wrap" }}>{isUser ? msg.content : stripLegacySummary(msg.content)}</div>
            )}
          </div>
        )}

        {msg.streaming && !msg.content && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
          <div style={{ padding: "4px 0" }}>
            <div style={{ display: "flex", gap: 4 }}>
              <span className="dotBounce" style={{ animationDelay: "0s" }} />
              <span className="dotBounce" style={{ animationDelay: "0.15s" }} />
              <span className="dotBounce" style={{ animationDelay: "0.3s" }} />
            </div>
            <SpinnerTipDisplay statusText={msg.streamStatus} />
          </div>
        )}

        {msg.toolCalls && msg.toolCalls.length > 0 && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
          <ToolCallsGroup toolCalls={msg.toolCalls} />
        )}

        {msg.artifacts && msg.artifacts.length > 0 && (
          <ArtifactList artifacts={msg.artifacts} apiBaseUrl={apiBaseUrl} onImagePreview={onImagePreview} />
        )}

        {msg.askUser && (
          <AskUserBlock
            ask={msg.askUser}
            onAnswer={(ans) => onAskAnswer?.(msg.id, ans)}
          />
        )}

        {msg.errorInfo && (
          <ErrorCard error={msg.errorInfo} onRetry={onRetry ? () => onRetry(msg.id) : undefined} />
        )}
      </div>
      <div className="msgActions" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, opacity: 0.35, marginTop: 2, paddingLeft: 2, paddingRight: 2 }}>
        <span>{formatTime(msg.timestamp)}</span>
        {showUsage && msg.usage && (
          <span style={{ opacity: 0.7 }} title={`${msg.usage.usage_estimated ? "Estimated · " : ""}In: ${msg.usage.input_tokens} · Out: ${msg.usage.output_tokens}`}>
            {usagePrefix}{usageTotal} tokens
          </span>
        )}
        {!msg.streaming && msg.content && (
          <button className="msgActionBtn" onClick={() => navigator.clipboard.writeText(msg.content).catch(() => {})} title={t("chat.copyMessage", "复制")}><IconClipboard size={13} /></button>
        )}
        {isUser && !msg.streaming && onEdit && (
          <button className="msgActionBtn" onClick={() => onEdit(msg.id)} title={t("chat.edit", "编辑")}><IconEdit size={13} /></button>
        )}
        {isAssistant && !msg.streaming && onRegenerate && (
          <button className="msgActionBtn" onClick={() => onRegenerate(msg.id)} title={t("chat.regenerate", "重新生成")}><IconRefresh size={13} /></button>
        )}
        {!isLast && !msg.streaming && onRewind && (
          <button className="msgActionBtn" onClick={() => onRewind(msg.id)} title={t("chat.rewind", "回到这里")}><IconRewind size={13} /></button>
        )}
      </div>
    </div>
  );
});
