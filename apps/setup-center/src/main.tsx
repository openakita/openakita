// Polyfill: AbortSignal.timeout is unavailable on older WebKit (macOS < 13 / Safari < 16).
// Must run before any module that calls AbortSignal.timeout().
if (typeof AbortSignal.timeout !== "function") {
  AbortSignal.timeout = (ms: number) => {
    const c = new AbortController();
    const id = setTimeout(() => c.abort(new DOMException("TimeoutError", "TimeoutError")), ms);
    c.signal.addEventListener("abort", () => clearTimeout(id), { once: true });
    return c.signal;
  };
}

if (__BUILD_TARGET__ === "tauri") {
  import("./localFetch").then(m => m.installLocalFetchOverride());
}

import React from "react";
import ReactDOM from "react-dom/client";

import "./i18n";
import "./globals.css";
import "./styles.css";
import { App } from "./App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { initTheme } from "./theme";
import { logger } from "./platform/logger";
import { copyToClipboard } from "./utils/clipboard";

// Initialize theme before rendering to catch OS changes
initTheme();

// ── Global error capture ──
// Catches JS errors and unhandled promise rejections that React ErrorBoundary cannot.
window.addEventListener("error", (event) => {
  logger.error("Global", `Uncaught error: ${event.message}`, {
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
    stack: event.error?.stack?.slice(0, 500),
  });
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  const message = reason instanceof Error ? reason.message : String(reason);
  logger.error("Global", `Unhandled promise rejection: ${message}`, {
    stack: reason instanceof Error ? reason.stack?.slice(0, 500) : undefined,
  });
});

logger.info("Boot", "Application starting", {
  platform: __BUILD_TARGET__,
  userAgent: navigator.userAgent.slice(0, 120),
});

// ── Global Error Boundary ──
// Catches unhandled React rendering errors to prevent white-screen crashes.
// Displays a friendly recovery UI instead of a blank page.
class GlobalErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    logger.error("ErrorBoundary", `React render error: ${error.message}`, {
      stack: error.stack?.slice(0, 500),
      componentStack: errorInfo.componentStack?.slice(0, 500),
    });
  }

  private getErrorText(): string {
    const e = this.state.error;
    if (!e) return "";
    const name = e.name || "Error";
    const msg = e.message || "(no message)";
    const stack = e.stack || "";
    const ua = navigator.userAgent;
    return `${name}: ${msg}\n\nStack:\n${stack}\n\nUserAgent: ${ua}`;
  }

  render() {
    if (this.state.hasError) {
      const errorText = this.getErrorText();
      return (
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          height: "100vh", width: "100vw", background: "linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%)",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          color: "#334155", padding: 32, boxSizing: "border-box",
        }}>
          <div style={{
            background: "#fff", borderRadius: 16, boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
            padding: "40px 48px", maxWidth: 540, width: "100%", textAlign: "center",
          }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>:(</div>
            <h2 style={{ margin: "0 0 12px", fontSize: 20, fontWeight: 600, color: "#1e293b" }}>
              Something went wrong
            </h2>
            <p style={{ margin: "0 0 20px", fontSize: 14, color: "#64748b", lineHeight: 1.6 }}>
              The application encountered an unexpected error. Your data is safe. Click the button below to reload.
            </p>
            {this.state.error && (
              <details style={{
                marginBottom: 16, textAlign: "left", background: "#f1f5f9",
                borderRadius: 8, padding: "8px 12px", fontSize: 12, color: "#475569",
                maxHeight: 200, overflow: "auto",
              }}>
                <summary style={{ cursor: "pointer", fontWeight: 500 }}>Error Details</summary>
                <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-all", fontSize: 11 }}>
                  {errorText}
                </pre>
              </details>
            )}
            <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
              <button
                onClick={async () => {
                  const ok = await copyToClipboard(errorText);
                  const btn = document.getElementById("_eb_copy");
                  if (btn) btn.textContent = ok ? "Copied!" : "Copy failed";
                }}
                id="_eb_copy"
                style={{
                  background: "#f1f5f9", color: "#475569", border: "1px solid #cbd5e1",
                  borderRadius: 10, padding: "10px 20px", fontSize: 14, fontWeight: 500,
                  cursor: "pointer", transition: "transform 0.1s",
                }}
              >
                Copy Error
              </button>
              <button
                onClick={() => location.reload()}
                style={{
                  background: "linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)",
                  color: "#fff", border: "none", borderRadius: 10, padding: "10px 24px",
                  fontSize: 15, fontWeight: 600, cursor: "pointer",
                  boxShadow: "0 2px 8px rgba(14,165,233,0.3)", transition: "transform 0.1s",
                }}
                onMouseDown={(e) => { (e.target as HTMLButtonElement).style.transform = "scale(0.97)"; }}
                onMouseUp={(e) => { (e.target as HTMLButtonElement).style.transform = ""; }}
              >
                Reload Application
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function hideBoot(remove = true) {
  const el = document.getElementById("boot");
  if (!el) return;
  if (remove) el.remove();
  else (el as HTMLElement).style.display = "none";
}

function wireBootButtons() {
  document.getElementById("bootClose")?.addEventListener("click", () => hideBoot(true));
  document.getElementById("bootReload")?.addEventListener("click", () => location.reload());
}

wireBootButtons();
window.addEventListener("openakita_app_ready", () => hideBoot(true));
// Failsafe: if something went wrong, don't leave it forever.
setTimeout(() => hideBoot(true), 8000);

// ── Desktop app hardening ──

// Custom right-click context menu (replaces browser default)
{
  let ctxMenu: HTMLDivElement | null = null;
  const removeMenu = () => { ctxMenu?.remove(); ctxMenu = null; };

  document.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    removeMenu();

    // 如果事件已被组件级自定义右键菜单处理，跳过全局菜单
    if ((e as any)._handled) return;

    const sel = window.getSelection();
    const hasSelection = !!(sel && sel.toString().trim());
    // Detect if right-click target is an editable element
    const target = e.target as HTMLElement;
    const isEditable =
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target.isContentEditable;

    const items: { label: string; action: () => void; disabled?: boolean }[] = [];

    if (isEditable) {
      items.push(
        { label: "剪切", action: () => document.execCommand("cut"), disabled: !hasSelection },
        {
          label: "复制",
          action: async () => {
            const text = window.getSelection()?.toString() ?? "";
            if (text) await copyToClipboard(text);
          },
          disabled: !hasSelection,
        },
        {
          label: "粘贴",
          action: () => {
            navigator.clipboard.readText().then((text) => {
              if (!text) return;
              if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
                const el = target;
                const start = el.selectionStart ?? el.value.length;
                const end = el.selectionEnd ?? el.value.length;
                const before = el.value.slice(0, start);
                const after = el.value.slice(end);
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                  target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
                  "value",
                )?.set;
                nativeInputValueSetter?.call(el, before + text + after);
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.setSelectionRange(start + text.length, start + text.length);
              } else if (target.isContentEditable) {
                const selection = window.getSelection();
                if (selection && selection.rangeCount > 0) {
                  const range = selection.getRangeAt(0);
                  range.deleteContents();
                  range.insertNode(document.createTextNode(text));
                  range.collapse(false);
                }
              }
            }).catch(() => {});
          },
        },
        { label: "全选", action: () => document.execCommand("selectAll") },
      );
    } else {
      items.push(
        {
          label: "复制",
          action: async () => {
            const text = window.getSelection()?.toString() ?? "";
            if (text) await copyToClipboard(text);
          },
          disabled: !hasSelection,
        },
        { label: "全选", action: () => document.execCommand("selectAll") },
      );
    }

    const menu = document.createElement("div");
    menu.className = "custom-ctx-menu";
    Object.assign(menu.style, {
      position: "fixed",
      zIndex: "99999",
      left: `${e.clientX}px`,
      top: `${e.clientY}px`,
      background: "var(--panel2)",
      backdropFilter: "var(--glass-blur)",
      border: "1px solid var(--line)",
      borderRadius: "8px",
      boxShadow: "var(--shadow)",
      color: "var(--text)",
      padding: "4px 0",
      minWidth: "120px",
      fontSize: "13px",
      fontFamily: "inherit",
    } as CSSStyleDeclaration);

    for (const item of items) {
      const row = document.createElement("div");
      row.textContent = item.label;
      Object.assign(row.style, {
        padding: "6px 16px",
        cursor: item.disabled ? "default" : "pointer",
        opacity: item.disabled ? "0.4" : "1",
        transition: "background 0.1s",
        userSelect: "none",
      } as CSSStyleDeclaration);
      if (!item.disabled) {
        row.addEventListener("mouseenter", () => { row.style.background = "rgba(14,165,233,0.08)"; });
        row.addEventListener("mouseleave", () => { row.style.background = ""; });
        row.addEventListener("click", () => { item.action(); removeMenu(); });
      }
      menu.appendChild(row);
    }

    document.body.appendChild(menu);
    ctxMenu = menu;

    // Clamp to viewport
    requestAnimationFrame(() => {
      const rect = menu.getBoundingClientRect();
      if (rect.right > window.innerWidth) menu.style.left = `${window.innerWidth - rect.width - 4}px`;
      if (rect.bottom > window.innerHeight) menu.style.top = `${window.innerHeight - rect.height - 4}px`;
    });
  });

  // Dismiss on click / scroll / keydown
  document.addEventListener("click", removeMenu);
  document.addEventListener("scroll", removeMenu, true);
  document.addEventListener("keydown", removeMenu);
}

// Prevent the webview from navigating away from the SPA.
// External <a> links (e.g. "apply for API key") should open in the OS browser.
// Without this guard, clicking a backend URL (e.g. file download) when the
// service is down would show Edge's "page not found" and trap the user.
document.addEventListener("click", (e) => {
  const anchor = (e.target as HTMLElement).closest?.("a[href]") as HTMLAnchorElement | null;
  if (!anchor || !anchor.href) return;
  const href = anchor.href;
  // Allow same-origin navigations (SPA hash/path links)
  if (href.startsWith(location.origin)) return;
  // Allow javascript: and blob: URLs
  if (href.startsWith("javascript:") || href.startsWith("blob:")) return;
  // Prevent webview navigation; open in OS default browser instead
  e.preventDefault();
  e.stopPropagation();
  import("./platform").then(({ openExternalUrl }) => {
    openExternalUrl(href);
  }).catch(() => {
    window.open(href, "_blank");
  });
});

if ("serviceWorker" in navigator && __BUILD_TARGET__ === "web") {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/web/sw.js").catch(() => {});
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <GlobalErrorBoundary>
      <TooltipProvider>
        <App />
      </TooltipProvider>
    </GlobalErrorBoundary>
  </React.StrictMode>,
);

// In case App mounts but doesn't emit.
requestAnimationFrame(() => hideBoot(true));

