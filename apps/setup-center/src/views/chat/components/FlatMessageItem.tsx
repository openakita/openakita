import { memo } from "react";
import { useTranslation } from "react-i18next";
import type { ChatMessage, MdModules } from "../utils/chatTypes";
import { stripLegacySummary } from "../utils/chatHelpers";
import { formatTime } from "../../../utils";
import { ThinkingChain, ThinkingBlock, ToolCallsGroup } from "./ThinkingChain";
import { ArtifactList } from "./Artifacts";
import { AskUserBlock } from "./AskUser";
import { ErrorCard } from "./ErrorCard";
import { AttachmentPreview } from "./AttachmentPreview";
import { SpinnerTipDisplay } from "./SpinnerTipDisplay";

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
}) {
  const { t } = useTranslation();
  const isUser = msg.role === "user";
  const isAssistant = msg.role === "assistant";
  const isSystem = msg.role === "system";

  if (isSystem) {
    return (
      <div className="flatMsgSystem">
        <span>{msg.content}</span>
      </div>
    );
  }

  return (
    <div className={`flatMessage flatMsgItem ${isUser ? "flatMsgUser" : "flatMsgAssistant"}`}>
      {isUser && (
        <div className="flatUserContent">
          {msg.attachments && msg.attachments.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              {msg.attachments.map((att, i) => (
                <AttachmentPreview key={i} att={att} />
              ))}
            </div>
          )}
          <div className="chatMdContent">
            {mdModules ? (
              <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>
                {msg.content}
              </mdModules.ReactMarkdown>
            ) : (
              <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{msg.content}</pre>
            )}
          </div>
        </div>
      )}

      {!isUser && (
        <>
          {msg.agentName && (
            <div style={{ fontSize: 11, fontWeight: 700, opacity: 0.4, marginBottom: 4 }}>
              {msg.agentName}
            </div>
          )}

          {msg.thinkingChain && msg.thinkingChain.length > 0 && (
            <ThinkingChain chain={msg.thinkingChain} streaming={!!msg.streaming} showChain={showChain} onSkipStep={onSkipStep} />
          )}

          {msg.thinking && (!msg.thinkingChain || msg.thinkingChain.length === 0) && (
            <ThinkingBlock content={msg.thinking} />
          )}

          {msg.streaming && !msg.content && (
            <div style={{ padding: "4px 0" }}>
              <div style={{ display: "flex", gap: 4 }}>
                <span className="dotBounce" style={{ animationDelay: "0s" }} />
                <span className="dotBounce" style={{ animationDelay: "0.15s" }} />
                <span className="dotBounce" style={{ animationDelay: "0.3s" }} />
              </div>
              <SpinnerTipDisplay />
            </div>
          )}

          {msg.content && stripLegacySummary(msg.content) && (
            <div className="chatMdContent">
              {mdModules ? (
                <mdModules.ReactMarkdown remarkPlugins={mdModules.remarkPlugins} rehypePlugins={mdModules.rehypePlugins}>
                  {stripLegacySummary(msg.content)}
                </mdModules.ReactMarkdown>
              ) : (
                <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit" }}>{stripLegacySummary(msg.content)}</pre>
              )}
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
        </>
      )}

      <div className="msgActions" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, opacity: 0.25, marginTop: 2 }}>
        <span>{formatTime(msg.timestamp)}</span>
        {msg.usage && (
          <span style={{ opacity: 0.7 }} title={`In: ${msg.usage.input_tokens} · Out: ${msg.usage.output_tokens}`}>
            {msg.usage.total_tokens ?? (msg.usage.input_tokens + msg.usage.output_tokens)} tokens
          </span>
        )}
        {!msg.streaming && msg.content && (
          <button className="msgActionBtn" onClick={() => navigator.clipboard.writeText(msg.content).catch(() => {})} title={t("chat.copyMessage", "复制")}>📋</button>
        )}
        {isUser && !msg.streaming && onEdit && (
          <button className="msgActionBtn" onClick={() => onEdit(msg.id)} title={t("chat.edit", "编辑")}>✏️</button>
        )}
        {isAssistant && !msg.streaming && onRegenerate && (
          <button className="msgActionBtn" onClick={() => onRegenerate(msg.id)} title={t("chat.regenerate", "重新生成")}>🔄</button>
        )}
        {!isLast && !msg.streaming && onRewind && (
          <button className="msgActionBtn" onClick={() => onRewind(msg.id)} title={t("chat.rewind", "回到这里")}>⏪</button>
        )}
      </div>
    </div>
  );
});
