import React from "react";
import ReactDOM from "react-dom/client";

import "./i18n";
import "./styles.css";
import { App } from "./App";

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
        { label: "粘贴", action: () => document.execCommand("paste") },
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
    <App />
  </React.StrictMode>,
);

// In case App mounts but doesn't emit.
requestAnimationFrame(() => hideBoot(true));

