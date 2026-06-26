import { useRef, useCallback, useEffect, useLayoutEffect, useMemo, useState, forwardRef, useImperativeHandle } from "react";
import { useTranslation } from "react-i18next";
import type { ChatMessage, MdModules, ChatDisplayMode } from "../utils/chatTypes";
import { MessageBubble } from "./MessageBubble";
import { FlatMessageItem } from "./FlatMessageItem";
import { IconChevronDown } from "../../../icons";

const CHAT_RENDER_MESSAGE_LIMIT = 100;
const CHAT_RENDER_CHAR_BUDGET = 240_000;
/** Distance (px) from the bottom within which we treat the viewport as "at bottom". */
const AT_BOTTOM_PX = 40;

function cappedAdd(total: number, amount: number, limit: number) {
  return Math.min(limit, total + Math.max(0, amount));
}

function estimateUnknownChars(value: unknown, limit: number, depth = 0): number {
  if (limit <= 0) return 0;
  if (typeof value === "string") return Math.min(value.length, limit);
  if (typeof value === "number" || typeof value === "boolean") return String(value).length;
  if (!value || typeof value !== "object" || depth >= 6) return 0;

  let chars = 0;
  if (Array.isArray(value)) {
    for (const item of value) {
      chars = cappedAdd(chars, estimateUnknownChars(item, limit - chars, depth + 1), limit);
      if (chars >= limit) break;
    }
    return chars;
  }

  const record = value as Record<string, unknown>;
  for (const key of ["content", "thinking", "thinkingChain", "toolCalls", "todo", "artifacts", "sources", "mcpCalls", "parts", "result", "args", "entries"] as const) {
    chars = cappedAdd(chars, estimateUnknownChars(record[key], limit - chars, depth + 1), limit);
    if (chars >= limit) break;
  }
  return chars;
}

// Identity-keyed cache. ChatMessage is replaced (not mutated) on every patch,
// so once cached, the value stays correct for the lifetime of that object.
// New objects (e.g. the currently streaming assistant message after each
// delta) compute fresh; the rest of the conversation hits the cache.
const messageCharCache = new WeakMap<ChatMessage, number>();

function estimateMessageRenderCharsCached(msg: ChatMessage): number {
  const cached = messageCharCache.get(msg);
  if (cached !== undefined) return cached;
  // Cap the walk at the global char budget — we don't need exact char counts
  // beyond it (any single message that exceeds the budget already dominates
  // the window decision on its own).
  const value = Math.max(1, estimateUnknownChars(msg, CHAT_RENDER_CHAR_BUDGET));
  messageCharCache.set(msg, value);
  return value;
}

function resolveRenderStartIndex(messages: ChatMessage[]): number {
  let visibleCount = 0;
  let renderChars = 0;
  let startIndex = messages.length;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (visibleCount >= CHAT_RENDER_MESSAGE_LIMIT) break;
    const messageChars = estimateMessageRenderCharsCached(messages[index]);
    if (visibleCount > 0 && renderChars + messageChars > CHAT_RENDER_CHAR_BUDGET) break;
    renderChars += messageChars;
    visibleCount += 1;
    startIndex = index;
  }
  return startIndex;
}

export interface MessageListHandle {
  scrollToIndex: (index: number, align?: "start" | "center" | "end") => void;
  scrollToBottom: (behavior?: "auto" | "smooth") => void;
  /** Force a one-shot snap to the bottom and re-arm sticky-bottom (call on user send). */
  forceFollow: () => void;
  /** Clear the pending one-shot snap (call when streaming ends). */
  cancelFollow: () => void;
  /** Whether the viewport is currently stuck to the bottom. */
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
  conversationId?: string;
  httpApiBase?: () => string;
  onPlanStepAction?: (action: "skip" | "retry", stepIdx: number, description: string) => void;
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
  onLoadOlder?: () => void;
  hasMoreBefore?: boolean;
  loadingOlder?: boolean;
  /** Reports the user message currently anchored near the top of the viewport (drives the outline active state). */
  onActiveUserMessageChange?: (msgId: string | null) => void;
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
    conversationId,
    httpApiBase,
    onPlanStepAction,
    onLoadOlder,
    hasMoreBefore = false,
    loadingOlder = false,
    onActiveUserMessageChange,
  },
  ref,
) {
  const { t } = useTranslation();
  const scrollerElRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef(new Map<string, HTMLDivElement>());
  // Unified sticky-bottom state machine (replaces the old forceFollow||atBottom OR):
  //   stickToBottomRef — follow new content; cleared on user scroll-up, re-armed at bottom.
  //   forceSnapRef     — one-shot: snap to bottom on next layout even if not sticky (user send).
  //   programmaticPinRef — guard counter so scroll events WE cause (pinning) are not
  //                        misread as the user scrolling up during same-frame growth.
  const stickToBottomRef = useRef(true);
  const forceSnapRef = useRef(false);
  const programmaticPinRef = useRef(0);
  const lastTopRef = useRef(0);
  const lastHeightRef = useRef(0);
  const lastClientHeightRef = useRef(0);
  const scrolledUpRef = useRef(false);
  const [scrolledUp, setScrolledUp] = useState(false);
  const savedScrollPositionRef = useRef<{ top: number; height: number } | null>(null);
  const pendingScrollRef = useRef<{ id: string; align: "start" | "center" | "end" } | null>(null);
  const [renderAllMessages, setRenderAllMessages] = useState(false);
  const searchActive = Boolean(searchHighlight?.trim());

  const setScrolledUpState = useCallback((v: boolean) => {
    if (scrolledUpRef.current === v) return;
    scrolledUpRef.current = v;
    setScrolledUp(v);
  }, []);

  const renderWindow = useMemo(() => {
    if (renderAllMessages || searchActive) {
      return { startIndex: 0, hiddenCount: 0, visibleMessages: messages };
    }
    const startIndex = resolveRenderStartIndex(messages);
    return {
      startIndex,
      hiddenCount: startIndex,
      visibleMessages: messages.slice(startIndex),
    };
  }, [messages, renderAllMessages, searchActive]);

  useEffect(() => {
    setRenderAllMessages(false);
    // Drop any pending scroll target from the previous conversation so a later
    // re-visit doesn't trigger an unrequested scroll if the same msg id is
    // still reachable. itemRefs are torn down by the unmount cycle anyway.
    pendingScrollRef.current = null;
    // Fresh conversation starts armed at the bottom; clear any leftover
    // scrolled-up state / pin guard from the previous one.
    stickToBottomRef.current = true;
    forceSnapRef.current = false;
    programmaticPinRef.current = 0;
    setScrolledUpState(false);
  }, [conversationId, setScrolledUpState]);

  // Consume any pending scroll target queued by scrollToIndex while the
  // target was hidden by the render budget. We run on every renderWindow
  // change so we catch the moment the message becomes mounted.
  useLayoutEffect(() => {
    const pending = pendingScrollRef.current;
    if (!pending) return;
    const el = itemRefs.current.get(pending.id);
    if (!el) return;
    el.scrollIntoView({
      block: pending.align === "end" ? "end" : pending.align === "center" ? "center" : "start",
      behavior: "smooth",
    });
    pendingScrollRef.current = null;
  }, [renderWindow]);

  const computeAtBottom = useCallback(() => {
    const el = scrollerElRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= AT_BOTTOM_PX;
  }, []);

  const recordScrollMetrics = useCallback(() => {
    const el = scrollerElRef.current;
    if (!el) return;
    lastTopRef.current = el.scrollTop;
    lastHeightRef.current = el.scrollHeight;
    lastClientHeightRef.current = el.clientHeight;
  }, []);

  // Pin the viewport to the bottom. Arms the programmatic-scroll guard so the
  // resulting scroll event is not misread as the user scrolling up.
  const pinToBottom = useCallback(() => {
    const el = scrollerElRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (dist <= AT_BOTTOM_PX) {
      // Already parked: writing scrollTop is a no-op and fires NO scroll event,
      // so do not arm the guard (it would never drain and would later swallow a
      // genuine user scroll-up). Just refresh the metric baseline.
      recordScrollMetrics();
      return;
    }
    programmaticPinRef.current = 1;
    el.scrollTop = el.scrollHeight;
    recordScrollMetrics();
  }, [recordScrollMetrics]);

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
    if (!el) return;
    // Instant scrolls fire a single synchronous scroll event we must claim as
    // ours. Smooth scrolls fire a burst we cannot reliably count, so we do not
    // guard them; callers use "auto" on the hot paths (send / conv switch).
    if (behavior === "auto") programmaticPinRef.current = 1;
    el.scrollTo({ top: el.scrollHeight, behavior });
    recordScrollMetrics();
  }, [recordScrollMetrics]);

  useImperativeHandle(ref, () => ({
    scrollToIndex: (index: number, align: "start" | "center" | "end" = "center") => {
      const msg = messages[index];
      if (!msg) return;
      const target = itemRefs.current.get(msg.id);
      if (target) {
        target.scrollIntoView({
          block: align === "end" ? "end" : align === "center" ? "center" : "start",
          behavior: "smooth",
        });
        return;
      }
      // Target is not mounted. Two reasons:
      //   1. It exists in `messages` but the render budget hid it → expand
      //      the window and finish the scroll in a layout effect once it mounts.
      //   2. It is older than what we hold locally → ask host to page in
      //      history from the backend.
      if (index < renderWindow.startIndex) {
        pendingScrollRef.current = { id: msg.id, align };
        setRenderAllMessages(true);
        return;
      }
      onLoadOlder?.();
    },
    scrollToBottom: (behavior?: "auto" | "smooth") => {
      stickToBottomRef.current = true;
      setScrolledUpState(false);
      scrollToAbsoluteBottom(behavior);
    },
    forceFollow: () => {
      forceSnapRef.current = true;
      stickToBottomRef.current = true;
      setScrolledUpState(false);
      requestAnimationFrame(() => pinToBottom());
    },
    cancelFollow: () => { forceSnapRef.current = false; },
    isAtBottom: () => stickToBottomRef.current,
    saveScrollPosition: () => {
      const el = scrollerElRef.current;
      if (el) savedScrollPositionRef.current = { top: el.scrollTop, height: el.scrollHeight };
    },
    restoreScrollPosition: () => {
      const el = scrollerElRef.current;
      if (el && savedScrollPositionRef.current !== null) {
        const prev = savedScrollPositionRef.current;
        el.scrollTop = prev.top + (el.scrollHeight - prev.height);
        savedScrollPositionRef.current = null;
        // History was prepended above the fold: refresh the metric baseline so
        // the next scroll event sees the grown height and does not misread this
        // restore as a user scroll-up. Do not re-arm follow.
        recordScrollMetrics();
        onAtBottomChange?.(computeAtBottom());
      }
    },
  }), [messages, renderWindow.startIndex, onLoadOlder, scrollToAbsoluteBottom, pinToBottom, recordScrollMetrics, computeAtBottom, onAtBottomChange, setScrolledUpState]);

  useEffect(() => {
    if (!isStreaming) {
      forceSnapRef.current = false;
    }
  }, [isStreaming]);

  useEffect(() => {
    const el = scrollerElRef.current;
    if (!el) return;

    // Authoritative user-intent signals: a wheel-up or any touch-drag means the
    // user wants to read above the fold, so stop following immediately — before
    // the resulting scroll event is even processed.
    const disarm = () => {
      stickToBottomRef.current = false;
      programmaticPinRef.current = 0;
      setScrolledUpState(true);
    };

    const onScroll = () => {
      const el2 = scrollerElRef.current;
      if (!el2) return;
      const top = el2.scrollTop;

      // A scroll event we caused by pinning: keep following and treat a
      // same-frame content-growth clamp as ours, not as a user scroll-up.
      if (programmaticPinRef.current > 0) {
        programmaticPinRef.current -= 1;
        recordScrollMetrics();
        stickToBottomRef.current = true;
        const atBottomNow = el2.scrollHeight - top - el2.clientHeight <= AT_BOTTOM_PX;
        if (atBottomNow) setScrolledUpState(false);
        onAtBottomChange?.(atBottomNow);
        return;
      }

      // Only a scrollTop *decrease* with an otherwise stable layout is a real
      // user scroll-up. History prepend, streaming markdown growth, and
      // composer/viewport resizes move scrollTop as a side effect and must not
      // disarm follow (wheel/touch already cover genuine intent above).
      const heightGrew = el2.scrollHeight > lastHeightRef.current;
      const clientChanged = Math.abs(el2.clientHeight - lastClientHeightRef.current) > 1;
      if (!heightGrew && !clientChanged && top + 1 < lastTopRef.current) {
        stickToBottomRef.current = false;
      }
      recordScrollMetrics();

      const atBottom = el2.scrollHeight - top - el2.clientHeight <= AT_BOTTOM_PX;
      if (atBottom) stickToBottomRef.current = true;
      setScrolledUpState(!stickToBottomRef.current);
      onAtBottomChange?.(atBottom);
    };

    const onWheel = (e: WheelEvent) => { if (e.deltaY < 0) disarm(); };

    el.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchmove", disarm, { passive: true });
    recordScrollMetrics();
    onAtBottomChange?.(computeAtBottom());
    return () => {
      el.removeEventListener("scroll", onScroll);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchmove", disarm);
    };
  }, [recordScrollMetrics, computeAtBottom, onAtBottomChange, setScrolledUpState]);

  // Track which user message is anchored near the top of the viewport so the
  // conversation outline can highlight the question the reader is currently on.
  useEffect(() => {
    const el = scrollerElRef.current;
    if (!el || !onActiveUserMessageChange) return;
    const userIds = messages.filter((m) => m.role === "user").map((m) => m.id);
    if (userIds.length === 0) {
      onActiveUserMessageChange(null);
      return;
    }
    let raf = 0;
    const compute = () => {
      raf = 0;
      // When scrolled to the bottom, the last question often cannot reach the
      // top anchor line (there isn't enough content below it), so the top-anchor
      // scan would lock onto the previous question. Force the last one instead.
      if (el.scrollHeight - el.scrollTop - el.clientHeight <= AT_BOTTOM_PX) {
        onActiveUserMessageChange(userIds[userIds.length - 1]);
        return;
      }
      const threshold = el.getBoundingClientRect().top + 100;
      let active: string | null = userIds[0];
      for (const id of userIds) {
        const node = itemRefs.current.get(id);
        if (!node) continue;
        if (node.getBoundingClientRect().top <= threshold) active = id;
        else break;
      }
      onActiveUserMessageChange(active);
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(compute); };
    el.addEventListener("scroll", onScroll, { passive: true });
    compute();
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [messages, onActiveUserMessageChange]);

  useLayoutEffect(() => {
    // Follow the bottom while armed (or for a one-shot forced snap). When not
    // armed, leave the viewport exactly where the user parked it.
    if (forceSnapRef.current || stickToBottomRef.current) {
      pinToBottom();
      forceSnapRef.current = false;
    }
  }, [messages, pinToBottom]);

  useEffect(() => {
    const el = scrollerElRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      // Content/viewport grew. Keep pinned only while armed; otherwise leave the
      // user where they are (the jump button covers getting back).
      if (forceSnapRef.current || stickToBottomRef.current) {
        pinToBottom();
      }
    });

    observer.observe(el);
    const firstChild = el.firstElementChild;
    if (firstChild instanceof HTMLElement) observer.observe(firstChild);
    return () => observer.disconnect();
  }, [messages.length, pinToBottom]);

  const handleJumpToLatest = useCallback(() => {
    stickToBottomRef.current = true;
    forceSnapRef.current = true;
    setScrolledUpState(false);
    requestAnimationFrame(() => pinToBottom());
  }, [pinToBottom, setScrolledUpState]);

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
          conversationId={conversationId}
          httpApiBase={httpApiBase}
          onPlanStepAction={onPlanStepAction}
        />
      </div>
    );
  }, [
    messages.length, displayMode, apiBaseUrl, showChain, mdModules,
    onAskAnswer, onRetry, onEdit, onRegenerate, onRewind, onSkipStep, onImagePreview,
    conversationId, httpApiBase, onPlanStepAction,
  ]);

  const Footer = useCallback(() => <div style={{ height: 32 }} />, []);

  // Show the jump-to-bottom affordance whenever the user is parked above the
  // fold, regardless of whether a reply is streaming.
  const showJumpToLatest = scrolledUp;

  return (
    <div ref={containerRef} style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", position: "relative" }}>
      {showJumpToLatest && (
        <button
          type="button"
          onClick={handleJumpToLatest}
          aria-label={t("chat.jumpToLatest", "回到底部")}
          style={{
            position: "absolute",
            bottom: 16,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 5,
            display: "flex",
            alignItems: "center",
            gap: 6,
            border: "1px solid var(--border)",
            background: "var(--brand)",
            color: "#fff",
            borderRadius: 999,
            padding: "6px 14px",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            boxShadow: "0 4px 14px rgba(0,0,0,0.18)",
          }}
        >
          <IconChevronDown size={14} />
          {t("chat.jumpToLatest", "回到底部")}
        </button>
      )}
      <div
        ref={scrollerElRef}
        style={{ flex: 1, minHeight: 0, overflowY: "auto", overscrollBehavior: "contain" }}
      >
        <div>
          {hasMoreBefore && (
            <div style={{ display: "flex", justifyContent: "center", padding: "12px 0 8px" }}>
              <button
                type="button"
                onClick={onLoadOlder}
                disabled={loadingOlder}
                style={{
                  border: "1px solid var(--border)",
                  background: "var(--surface)",
                  color: "var(--text-muted)",
                  borderRadius: 999,
                  padding: "6px 12px",
                  fontSize: 12,
                  cursor: loadingOlder ? "default" : "pointer",
                  opacity: loadingOlder ? 0.7 : 1,
                }}
              >
                {loadingOlder ? "正在加载更早消息..." : "加载更早消息"}
              </button>
            </div>
          )}
          {renderWindow.hiddenCount > 0 && (
            <div style={{ display: "flex", justifyContent: "center", padding: "8px 0" }}>
              <div style={{
                border: "1px solid var(--border)",
                background: "var(--surface)",
                color: "var(--text-muted)",
                borderRadius: 999,
                padding: "5px 12px",
                fontSize: 12,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}>
                <span>已隐藏更早的 {renderWindow.hiddenCount} 条消息以保持流式显示流畅</span>
                <button
                  type="button"
                  onClick={() => setRenderAllMessages(true)}
                  style={{
                    border: "none",
                    background: "transparent",
                    color: "var(--brand)",
                    cursor: "pointer",
                    padding: 0,
                    fontSize: 12,
                  }}
                >
                  显示已隐藏消息
                </button>
              </div>
            </div>
          )}
          {renderWindow.visibleMessages.map((msg, visibleIndex) => {
            const originalIndex = renderWindow.startIndex + visibleIndex;
            return (
            <div
              key={computeItemKey(originalIndex, msg)}
              ref={(el) => {
                if (el) itemRefs.current.set(msg.id, el);
                else itemRefs.current.delete(msg.id);
              }}
            >
              {itemContent(originalIndex, msg)}
            </div>
            );
          })}
          <Footer />
        </div>
      </div>
    </div>
  );
});
