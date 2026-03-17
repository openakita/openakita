import DefaultTheme from "vitepress/theme";
import type { Theme } from "vitepress";

export default {
  extends: DefaultTheme,
  enhanceApp() {
    if (typeof window === "undefined") return;

    document.addEventListener("click", (e) => {
      const anchor = (e.target as HTMLElement).closest?.("a");
      if (!anchor) return;

      const href = anchor.getAttribute("href");
      if (!href) return;

      // /web/... links → navigate the top-level app window, not the iframe
      if (href.startsWith("/web/") || href.startsWith("/web#") || href === "/web") {
        e.preventDefault();
        if (window.top && window.top !== window) {
          window.top.location.href = href;
        } else {
          window.location.href = href;
        }
      }
    });
  },
} satisfies Theme;
