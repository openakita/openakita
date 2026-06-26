import type { ChatMessage, MdModules, MessagePart } from "../utils/chatTypes";
import { ThinkingChain, ThinkingBlock, ToolCallsGroup } from "./ThinkingChain";
import { ArtifactItem } from "./Artifacts";
import { OrgTimelineCard } from "./OrgTimeline";
import { AskUserBlock } from "./AskUser";
import { AskUserSummary } from "./AskUserSummary";
import { ErrorCard } from "./ErrorCard";
import { SourceStrip } from "./SourceStrip";
import { PlanCard } from "./PlanCard";
import { MCPCallStrip } from "./MCPCallStrip";
import { MarkdownContent } from "./MarkdownContent";

/**
 * Ordered renderer for an assistant message's `MessagePart[]`.
 *
 * This is the single rendering path shared by both display modes
 * (`FlatMessageItem` and `MessageBubble`), so plan / answered-ask_user /
 * image cards render and re-hydrate identically regardless of the chosen mode.
 * Heavy text blocks read their payload from the flat message fields; the parts
 * array only carries ordering (and small inlined data for plan / attachment /
 * ask_user).
 */
export function MessageParts({
  msg,
  parts,
  bodyContent,
  formatSourceTags,
  mdModules,
  showChain = true,
  forceExpandChain = false,
  onSkipStep,
  onImagePreview,
  onAskAnswer,
  onPlanStepAction,
  onRetry,
  apiBaseUrl,
  conversationId,
  httpApiBase,
}: {
  msg: ChatMessage;
  parts: MessagePart[];
  /** Body text with the trailing [来源:X] tag already peeled off by the caller. */
  bodyContent: string;
  formatSourceTags: (text: string) => string;
  mdModules?: MdModules | null;
  showChain?: boolean;
  /** Force the reasoning chain open in a single click (empty-bubble reveal). */
  forceExpandChain?: boolean;
  onSkipStep?: () => void;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
  onAskAnswer?: (msgId: string, answer: string) => void;
  onPlanStepAction?: (action: "skip" | "retry", stepIdx: number, description: string) => void;
  onRetry?: (msgId: string) => void;
  apiBaseUrl?: string;
  conversationId?: string;
  httpApiBase?: () => string;
}) {
  const streaming = !!msg.streaming;
  return (
    <>
      {parts.map((part) => {
        switch (part.kind) {
          case "reasoning":
            return msg.thinkingChain && msg.thinkingChain.length > 0 ? (
              <ThinkingChain key={part.id} chain={msg.thinkingChain} streaming={streaming} showChain={showChain} forceExpand={forceExpandChain} onSkipStep={onSkipStep} />
            ) : null;
          case "thinking":
            return msg.thinking ? <ThinkingBlock key={part.id} content={msg.thinking} /> : null;
          case "org_timeline":
            return msg.orgTimeline && msg.orgTimeline.length > 0 ? (
              <OrgTimelineCard key={part.id} entries={msg.orgTimeline} streaming={streaming} />
            ) : null;
          case "sources":
            return <SourceStrip key={part.id} sources={msg.sources} conversationId={conversationId} httpApiBase={httpApiBase} />;
          case "mcp":
            return <MCPCallStrip key={part.id} calls={msg.mcpCalls} />;
          case "plan": {
            const todo = part.todo || msg.todo;
            return todo && todo.steps?.length > 0 ? (
              <PlanCard key={part.id} plan={todo} onStepAction={onPlanStepAction} />
            ) : null;
          }
          case "text":
            return bodyContent ? (
              <MarkdownContent
                key={part.id}
                content={formatSourceTags(bodyContent)}
                mdModules={mdModules}
                className="chatMdContent"
                streaming={streaming}
              />
            ) : null;
          case "tools":
            return msg.toolCalls && msg.toolCalls.length > 0 ? (
              <ToolCallsGroup key={part.id} toolCalls={msg.toolCalls} />
            ) : null;
          case "attachment": {
            const art = part.artifact;
            return art ? (
              <ArtifactItem key={part.id} art={art} apiBaseUrl={apiBaseUrl} onImagePreview={onImagePreview} />
            ) : null;
          }
          case "ask_user": {
            const ask = part.ask || msg.askUser;
            if (!ask) return null;
            return ask.answered ? (
              <AskUserSummary key={part.id} ask={ask} />
            ) : (
              <AskUserBlock key={part.id} ask={ask} onAnswer={(ans) => onAskAnswer?.(msg.id, ans)} />
            );
          }
          case "error":
            return msg.errorInfo ? (
              <ErrorCard key={part.id} error={msg.errorInfo} onRetry={onRetry ? () => onRetry(msg.id) : undefined} />
            ) : null;
          default:
            return null;
        }
      })}
    </>
  );
}
