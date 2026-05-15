// useExpandPanel — listen for `openakita:expand-panel` CustomEvents and bring
// the matching <details> element into focus.
//
// Why a custom event + global listener instead of prop drilling?
// The trigger originates inside chat (ConfigHintCard buttons) but the target
// lives in the settings tab (WebSearchProviderPanel). Lifting state up to a
// shared store would couple two otherwise-unrelated views; an event bus keeps
// the chat layer 0% aware of how settings is structured.
//
// Lifecycle:
//   1. App.tsx mounts a ref via this hook on the <details> it wants to expand.
//   2. ConfigHintCard dispatches `openakita:navigate-view` to switch to the
//      settings view, then `openakita:expand-panel` after a short delay so the
//      view has a chance to mount.
//   3. This hook receives the event, opens <details>, scrolls into view, and
//      adds a ``data-expand-flash`` attribute that styles.css fades over 1.4s.

import { useEffect, useRef } from "react";

export interface ExpandPanelDetail {
  /** Stable identifier for the target panel; matches ``targetAnchor`` arg. */
  anchor: string;
  /** Optional sub-target inside the panel (currently unused — reserved). */
  scrollTo?: string;
}

const FLASH_ATTR = "data-expand-flash";
const FLASH_TIMEOUT_MS = 1400;

/**
 * Returns a ref to attach to a ``<details>`` element. When a global
 * ``openakita:expand-panel`` event fires with ``detail.anchor === targetAnchor``,
 * the panel opens, scrolls into view, and briefly highlights itself.
 *
 * @example
 *   const ref = useExpandPanel("web-search");
 *   return <details ref={ref}>...</details>;
 */
export function useExpandPanel(targetAnchor: string) {
  const ref = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    if (!targetAnchor) return;

    const handler = (event: Event) => {
      const ce = event as CustomEvent<ExpandPanelDetail>;
      if (!ce.detail || ce.detail.anchor !== targetAnchor) return;
      const el = ref.current;
      if (!el) return;

      // Open the disclosure; idempotent if already open.
      if (!el.open) el.open = true;

      // Scroll into the viewport. ``block: "center"`` keeps the panel away
      // from edges so the user notices the focus change.
      try {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      } catch {
        // Old Safari without smooth-scroll falls back to instant.
        el.scrollIntoView();
      }

      // Trigger the flash highlight by setting an attribute styled in CSS.
      el.setAttribute(FLASH_ATTR, "true");
      window.setTimeout(() => {
        el.removeAttribute(FLASH_ATTR);
      }, FLASH_TIMEOUT_MS);
    };

    window.addEventListener("openakita:expand-panel", handler as EventListener);
    return () => {
      window.removeEventListener("openakita:expand-panel", handler as EventListener);
    };
  }, [targetAnchor]);

  return ref;
}

/**
 * Helper to dispatch the expand event from anywhere in the app.
 *
 * Use this from chat-side action buttons rather than constructing the
 * CustomEvent inline — keeps the event name + detail shape in one place.
 */
export function dispatchExpandPanel(anchor: string, scrollTo?: string): void {
  const detail: ExpandPanelDetail = { anchor, ...(scrollTo ? { scrollTo } : {}) };
  window.dispatchEvent(
    new CustomEvent<ExpandPanelDetail>("openakita:expand-panel", { detail }),
  );
}
