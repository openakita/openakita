import { useRef, useCallback, useEffect, useLayoutEffect, forwardRef, useImperativeHandle } from "react";
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
  onAtBottomChange?: (atBottom: boolean) => void;
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
    onAtBottomChange,
  },
  ref,
) {
  const scrollerElRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef(new Map<string, HTMLDivElement>());
  const forceFollowRef = useRef(false);
  const atBottomRef = useRef(true);
  const savedScrollTopRef = useRef<number | null>(null);

  const emitAtBottomChange = useCallback((atBottom: boolean) => {
    atBottomRef.current = atBottom;
    onAtBottomChange?.(atBottom);
  }, [onAtBottomChange]);

  const computeAtBottom = useCallback(() => {
    const el = scrollerElRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= 80;
  }, []);

  const syncAtBottomState = useCallback(() => {
    emitAtBottomChange(computeAtBottom());
  }, [computeAtBottom, emitAtBottomChange]);

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
    }
  }, []);

  useImperativeHandle(ref, () => ({
    scrollToIndex: (index: number, align: "start" | "center" | "end" = "center") => {
      const msg = messages[index];
      if (!msg) return;
      const target = itemRefs.current.get(msg.id);
      if (!target) return;
      target.scrollIntoView({
        block: align === "end" ? "end" : align === "center" ? "center" : "start",
        behavior: "smooth",
      });
    },
    scrollToBottom: scrollToAbsoluteBottom,
    forceFollow: () => {
      forceFollowRef.current = true;
      requestAnimationFrame(() => scrollToAbsoluteBottom());
    },
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
        syncAtBottomState();
      }
    },
  }), [messages, scrollToAbsoluteBottom, syncAtBottomState]);

  useEffect(() => {
    if (!isStreaming) {
      forceFollowRef.current = false;
    }
  }, [isStreaming]);

  useEffect(() => {
    const el = scrollerElRef.current;
    if (!el) return;

    const onScroll = () => {
      syncAtBottomState();
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    syncAtBottomState();
    return () => el.removeEventListener("scroll", onScroll);
  }, [syncAtBottomState]);

  useLayoutEffect(() => {
    if (forceFollowRef.current || atBottomRef.current) {
      scrollToAbsoluteBottom();
      emitAtBottomChange(true);
      return;
    }
    syncAtBottomState();
  }, [messages, scrollToAbsoluteBottom, syncAtBottomState, emitAtBottomChange]);

  useEffect(() => {
    const el = scrollerElRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      if (forceFollowRef.current || atBottomRef.current) {
        scrollToAbsoluteBottom();
        emitAtBottomChange(true);
      } else {
        syncAtBottomState();
      }
    });

    observer.observe(el);
    const firstChild = el.firstElementChild;
    if (firstChild instanceof HTMLElement) observer.observe(firstChild);
    return () => observer.disconnect();
  }, [messages.length, scrollToAbsoluteBottom, syncAtBottomState, emitAtBottomChange]);

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
      <div
        ref={scrollerElRef}
        style={{ flex: 1, minHeight: 0, overflowY: "auto", overscrollBehavior: "contain" }}
      >
        <div>
          {messages.map((msg, index) => (
            <div
              key={computeItemKey(index, msg)}
              ref={(el) => {
                if (el) itemRefs.current.set(msg.id, el);
                else itemRefs.current.delete(msg.id);
              }}
            >
              {itemContent(index, msg)}
            </div>
          ))}
          <Footer />
        </div>
      </div>
    </div>
  );
});
