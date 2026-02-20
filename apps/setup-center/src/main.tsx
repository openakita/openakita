import React from "react";
import ReactDOM from "react-dom/client";

import "./i18n";
import "./styles.css";
import { App } from "./App";
import { initTheme } from "./theme";

// Initialize theme before rendering to catch OS changes
initTheme();

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
    console.error("[ErrorBoundary] Uncaught error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          height: "100vh", width: "100vw", background: "linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%)",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          color: "#334155", padding: 32, boxSizing: "border-box",
        }}>
          <div style={{
            background: "#fff", borderRadius: 16, boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
            padding: "40px 48px", maxWidth: 480, width: "100%", textAlign: "center",
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
                marginBottom: 20, textAlign: "left", background: "#f1f5f9",
                borderRadius: 8, padding: "8px 12px", fontSize: 12, color: "#475569",
                maxHeight: 120, overflow: "auto",
              }}>
                <summary style={{ cursor: "pointer", fontWeight: 500 }}>Error Details</summary>
                <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {this.state.error.message}
                  {"\n"}
                  {this.state.error.stack?.slice(0, 500)}
                </pre>
              </details>
            )}
            <button
              onClick={() => location.reload()}
              style={{
                background: "linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)",
                color: "#fff", border: "none", borderRadius: 10, padding: "10px 32px",
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
setTimeout(() => hideBoot(true), 20000);

// ── Desktop app hardening ──

// Custom right-click context menu (replaces browser default)
{
  let ctxMenu: HTMLDivElement | null = null;
  const removeMenu = () => { ctxMenu?.remove(); ctxMenu = null; };

  document.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    removeMenu();

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
        { label: "复制", action: () => document.execCommand("copy"), disabled: !hasSelection },
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
        { label: "复制", action: () => document.execCommand("copy"), disabled: !hasSelection },
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
      background: "#fff",
      border: "1px solid rgba(0,0,0,0.12)",
      borderRadius: "8px",
      boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
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
  // Use Tauri's invoke to open URL externally (if the command is available)
  import("@tauri-apps/api/core").then(({ invoke }) => {
    invoke("open_external_url", { url: href }).catch(() => {
      // Fallback: use window.open which Tauri may handle
      window.open(href, "_blank");
    });
  }).catch(() => {
    window.open(href, "_blank");
  });
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <GlobalErrorBoundary>
      <App />
    </GlobalErrorBoundary>
  </React.StrictMode>,
);

// In case App mounts but doesn't emit.
requestAnimationFrame(() => hideBoot(true));

