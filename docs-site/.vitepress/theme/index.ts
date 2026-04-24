import DefaultTheme from "vitepress/theme";
import type { Theme } from "vitepress";
import { h } from "vue";

const DocsBanner = {
  setup() {
    return () =>
      h(
        "div",
        {
          style:
            "background: linear-gradient(135deg, #fef3c7, #fde68a); color: #92400e; text-align: center; padding: 8px 16px; font-size: 13px; line-height: 1.5; border-bottom: 1px solid #f59e0b40;",
        },
        "⚠️ 本文档由 AI 生成，尚未完全人工审核校对，内容仅供参考。请结合实际界面操作，如有出入以软件实际功能为准。",
      );
  },
};

export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      "layout-top": () => h(DocsBanner),
    });
  },
  enhanceApp({ router }) {
    if (typeof window === "undefined") return;

    router.onBeforeRouteChange = (to: string) => {
      if (to.includes("/web/") || to.includes("/web#")) {
        const hashIdx = to.indexOf("#");
        const hash = hashIdx >= 0 ? to.slice(hashIdx) : "";
        if (hash) {
          if (window.parent && window.parent !== window) {
            window.parent.postMessage(
              { type: "openakita-navigate", hash },
              "*",
            );
          } else {
            window.location.hash = hash;
          }
        }
        return false;
      }
    };
  },
} satisfies Theme;

