import { useRef, useCallback, useEffect, forwardRef, useImperativeHandle } from "react";
import { Virtuoso, VirtuosoHandle } from "react-virtuoso";
import type { ChatMessage, MdModules, ChatDisplayMode } from "../utils/chatTypes";
import { MessageBubble } from "./MessageBubble";
import { FlatMessageItem } from "./FlatMessageItem";

export interface MessageListHandle {
  scrollToIndex: (index: number, align?: "start" | "center" | "end") => void;
  scrollToBottom: (behavior?: "auto" | "smooth") => void;
  /** Keep followOutput returning true until cancelFollow is called, even if user scrolled up. */
  forceFollow: () => void;
  /** Stop forced following (call when streaming ends). */
  cancelFollow: () => void;
  /** Whether the user is currently scrolled to the bottom. */
  isAtBottom: () => boolean;
  /** Save current scroll position — call before mutating messages while user is scrolled up. */
  saveScrollPosition: () => void;
  /** Restore previously saved scroll position. */
  restoreScrollPosition: () => void;
}

export interface MessageListProps {
  messages: ChatMessage[];
  displayMode: ChatDisplayMode;
  showChain: boolean;
  apiBaseUrl?: string;
  mdModules?: MdModules | null;
  isStreaming: boolean;
  searchHighlight?: string;
  onAskAnswer?: (msgId: string, answer: string) => void;
  onRetry?: (msgId: string) => void;
  onEdit?: (msgId: string) => void;
  onRegenerate?: (msgId: string) => void;
  onRewind?: (msgId: string) => void;
  onFork?: (msgId: string) => void;
  onSaveMemory?: (msgId: string) => void;
  onSkipStep?: () => void;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
}

function applySearchHighlights(container: HTMLElement, query: string) {
  const css = globalThis.CSS as typeof CSS & { highlights?: Map<string, Highlight> };
  if (!css?.highlights) return;
  const q = query.trim().toLowerCase();
  if (!q) { css.highlights.delete("msg-search"); return; }
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const ranges: Range[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const text = node.textContent?.toLowerCase() ?? "";
    let pos = 0;
    while (pos < text.length) {
      const idx = text.indexOf(q, pos);
      if (idx === -1) break;
      const range = new Range();
      range.setStart(node, idx);
      range.setEnd(node, idx + q.length);
      ranges.push(range);
      pos = idx + q.length;
    }
  }
  css.highlights.set("msg-search", new Highlight(...ranges));
}

export const MessageList = forwardRef<MessageListHandle, MessageListProps>(function MessageList(
  {
    messages,
    displayMode,
    showChain,
    apiBaseUrl,
    mdModules,
    isStreaming,
    searchHighlight,
    onAskAnswer,
    onRetry,
    onEdit,
    onRegenerate,
    onRewind,
    onFork,
    onSaveMemory,
    onSkipStep,
    onImagePreview,
  },
  ref,
) {
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const scrollerElRef = useRef<HTMLElement | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const forceFollowRef = useRef(false);
  const atBottomRef = useRef(true);
  const savedScrollTopRef = useRef<number | null>(null);

  const handleAtBottomChange = useCallback((atBottom: boolean) => {
    atBottomRef.current = atBottom;
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const css = globalThis.CSS as typeof CSS & { highlights?: Map<string, Highlight> };
    if (!css?.highlights) return;

    const q = searchHighlight?.trim().toLowerCase() ?? "";
    applySearchHighlights(el, q);

    if (!q) return;

    const observer = new MutationObserver(() => applySearchHighlights(el, q));
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      observer.disconnect();
      css.highlights.delete("msg-search");
    };
  }, [searchHighlight, messages]);

  const scrollToAbsoluteBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const el = scrollerElRef.current;
    if (el) {
      el.scrollTo({ top: el.scrollHeight, behavior });
    } else {
      virtuosoRef.current?.scrollTo({ top: 1_000_000_000, behavior });
    }
  }, []);

  useImperativeHandle(ref, () => ({
    scrollToIndex: (index: number, align: "start" | "center" | "end" = "center") => {
      virtuosoRef.current?.scrollToIndex({ index, align, behavior: "smooth" });
    },
    scrollToBottom: scrollToAbsoluteBottom,
    forceFollow: () => { forceFollowRef.current = true; },
    cancelFollow: () => { forceFollowRef.current = false; },
    isAtBottom: () => atBottomRef.current,
    saveScrollPosition: () => {
      const el = scrollerElRef.current;
      if (el) savedScrollTopRef.current = el.scrollTop;
    },
    restoreScrollPosition: () => {
      const el = scrollerElRef.current;
      if (el && savedScrollTopRef.current !== null) {
        el.scrollTop = savedScrollTopRef.current;
        savedScrollTopRef.current = null;
      }
    },
  }), [scrollToAbsoluteBottom]);

  const followOutput = useCallback((isAtBottom: boolean) => {
    if (forceFollowRef.current) {
      return "auto";
    }
    if (isAtBottom) return isStreaming ? "auto" : "smooth";
    return false;
  }, [isStreaming]);

  // Explicit scroll-to-bottom when messages count changes while forceFollow is active.
  // Two attempts: immediate (rAF) + delayed (250ms) to catch the loading indicator
  // after it has been fully laid out by the browser.
  useEffect(() => {
    if (forceFollowRef.current && messages.length > 0) {
      requestAnimationFrame(() => scrollToAbsoluteBottom());
      const timer = setTimeout(() => { if (forceFollowRef.current) scrollToAbsoluteBottom(); }, 250);
      return () => clearTimeout(timer);
    }
  }, [messages.length, scrollToAbsoluteBottom]);

  // Keep scroll pinned to bottom during streaming content updates (same message count,
  // but content grows). Throttled via rAF to avoid layout thrashing.
  const streamScrollRaf = useRef(0);
  useEffect(() => {
    if (!isStreaming || !forceFollowRef.current) return;
    if (streamScrollRaf.current) cancelAnimationFrame(streamScrollRaf.current);
    streamScrollRaf.current = requestAnimationFrame(() => {
      streamScrollRaf.current = 0;
      scrollToAbsoluteBottom();
    });
    return () => {
      if (streamScrollRaf.current) {
        cancelAnimationFrame(streamScrollRaf.current);
        streamScrollRaf.current = 0;
      }
    };
  }, [messages, isStreaming, scrollToAbsoluteBottom]);

  const computeItemKey = useCallback((_index: number, msg: ChatMessage) => msg.id, []);

  const itemContent = useCallback((index: number, msg: ChatMessage) => {
    const isLast = index === messages.length - 1;
    const Component = displayMode === "flat" ? FlatMessageItem : MessageBubble;
    return (
      <div data-msg-idx={index}>
        <Component
          msg={msg}
          isLast={isLast}
          apiBaseUrl={apiBaseUrl}
          showChain={showChain}
          mdModules={mdModules}
          onAskAnswer={onAskAnswer}
          onRetry={onRetry}
          onEdit={onEdit}
          onRegenerate={onRegenerate}
          onRewind={onRewind}
          onSkipStep={onSkipStep}
          onImagePreview={onImagePreview}
        />
      </div>
    );
  }, [
    messages.length, displayMode, apiBaseUrl, showChain, mdModules,
    onAskAnswer, onRetry, onEdit, onRegenerate, onRewind, onSkipStep, onImagePreview,
  ]);

  const Footer = useCallback(() => <div style={{ height: 32 }} />, []);

  return (
    <div ref={containerRef} style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      <Virtuoso
        ref={virtuosoRef}
        scrollerRef={(el) => { scrollerElRef.current = el as HTMLElement | null; }}
        data={messages}
        computeItemKey={computeItemKey}
        followOutput={followOutput}
        initialTopMostItemIndex={Math.max(0, messages.length - 1)}
        atBottomStateChange={handleAtBottomChange}
        atBottomThreshold={80}
        increaseViewportBy={{ top: 400, bottom: 200 }}
        itemContent={itemContent}
        components={{ Footer }}
        style={{ flex: 1, minHeight: 0 }}
      />
    </div>
  );
});
