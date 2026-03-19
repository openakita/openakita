// ─── Platform Abstraction Layer ───
// Provides unified APIs across Tauri desktop and Web browser environments.
// Tauri-specific modules are loaded via dynamic import() so they are never
// bundled into the web build and never evaluated when running in a browser.

import { IS_TAURI, IS_WEB, IS_CAPACITOR, IS_LOCAL_WEB, IS_MOBILE_BROWSER } from "./detect";
export { IS_TAURI, IS_WEB, IS_CAPACITOR, IS_LOCAL_WEB, IS_MOBILE_BROWSER };

// ---------------------------------------------------------------------------
// Asset protocol: serve local files directly from disk (desktop only)
// ---------------------------------------------------------------------------

/**
 * Convert a local file path to a Tauri asset protocol URL.
 * Desktop: direct disk access via asset:// — no HTTP, no proxy, no CORS.
 * Web: returns null — caller should fall back to HTTP URL.
 */
export function getAssetUrl(filePath: string): string | null {
  if (!IS_TAURI || !filePath) return null;
  try {
    const internals = (window as any).__TAURI_INTERNALS__;
    if (internals?.convertFileSrc) {
      return internals.convertFileSrc(filePath, "asset");
    }
  } catch { /* unavailable — fall back to HTTP */ }
  return null;
}

// ---------------------------------------------------------------------------
// Core: invoke & listen
// ---------------------------------------------------------------------------

/**
 * Drop-in replacement for `@tauri-apps/api/core` `invoke`.
 * In web mode this always throws — callers must guard with `IS_TAURI` or
 * use higher-level helpers that provide web fallbacks.
 */
export async function invoke<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  if (!IS_TAURI)
    throw new Error(`Tauri invoke("${cmd}") is not available in web mode`);
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  return tauriInvoke<T>(cmd, args);
}

/**
 * Drop-in replacement for `@tauri-apps/api/event` `listen`.
 * Returns a no-op unsubscribe function in web mode.
 */
export async function listen<T>(
  event: string,
  handler: (event: { payload: T }) => void,
): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  const { listen: tauriListen } = await import("@tauri-apps/api/event");
  return tauriListen<T>(event, handler);
}

// ---------------------------------------------------------------------------
// App version
// ---------------------------------------------------------------------------

export async function getAppVersion(): Promise<string> {
  if (IS_TAURI) {
    const { getVersion } = await import("@tauri-apps/api/app");
    return getVersion();
  }
  try {
    let base = "";
    if (IS_CAPACITOR) {
      const { getActiveServer } = await import("./servers");
      base = getActiveServer()?.url || "";
    }
    const res = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      const data = await res.json();
      return data.version || "0.0.0";
    }
  } catch { /* ignore */ }
  return "0.0.0";
}

// ---------------------------------------------------------------------------
// External URLs
// ---------------------------------------------------------------------------

export async function openExternalUrl(url: string): Promise<void> {
  if (IS_TAURI) {
    const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
    await tauriInvoke("open_external_url", { url });
  } else {
    window.open(url, "_blank");
  }
}

// ---------------------------------------------------------------------------
// File operations (download / open / show-in-folder)
// ---------------------------------------------------------------------------

/**
 * Download a URL to a file.
 * - Tauri (Win/Mac/Linux): Native HTTP GET → save to user Downloads → returns path.
 * - Web: Programmatic <a download> click; backend must send Content-Disposition: attachment
 *   so the browser triggers download (works same-origin or cross-origin).
 * Returns: saved path (Tauri) or filename (Web).
 */
export async function downloadFile(
  url: string,
  filename: string,
): Promise<string> {
  if (IS_TAURI) {
    const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
    return tauriInvoke<string>("download_file", { url, filename });
  }
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  return filename;
}

/** Show a file in the OS file manager. No-op on web. */
export async function showInFolder(path: string): Promise<void> {
  if (!IS_TAURI) return;
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  await tauriInvoke("show_item_in_folder", { path });
}

/** Open a file with the OS default application. No-op on web. */
export async function openFileWithDefault(path: string): Promise<void> {
  if (!IS_TAURI) return;
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  await tauriInvoke("open_file_with_default", { path });
}

/** Read a local file as a base64 data-URL. Only available in Tauri. */
export async function readFileBase64(path: string): Promise<string> {
  if (!IS_TAURI)
    throw new Error("readFileBase64 is only available in Tauri");
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  return tauriInvoke<string>("read_file_base64", { path });
}

// ---------------------------------------------------------------------------
// HTTP proxy (bypass CORS in Tauri webview; direct fetch on web)
// ---------------------------------------------------------------------------

export async function proxyFetch(
  url: string,
  options?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    timeoutSecs?: number;
  },
): Promise<{ status: number; body: string }> {
  if (IS_TAURI) {
    const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
    const raw = await tauriInvoke<string>("http_proxy_request", {
      url,
      method: options?.method ?? "GET",
      headers: options?.headers ?? null,
      body: options?.body ?? null,
      timeoutSecs: options?.timeoutSecs ?? 30,
    });
    return JSON.parse(raw) as { status: number; body: string };
  }
  const res = await fetch(url, {
    method: options?.method ?? "GET",
    headers: options?.headers,
    body: options?.body,
    signal: AbortSignal.timeout((options?.timeoutSecs ?? 30) * 1000),
  });
  const body = await res.text();
  return { status: res.status, body };
}

// ---------------------------------------------------------------------------
// Drag & drop
// ---------------------------------------------------------------------------

export type DragDropHandlers = {
  onEnter?: () => void;
  onOver?: () => void;
  onLeave?: () => void;
  onDrop?: (paths: string[]) => void;
};

/**
 * Register Tauri webview-level drag-drop listeners.
 * Returns an unsubscribe function. On web, returns no-op — the browser's
 * native drag-drop should be handled separately with HTML5 APIs.
 */
export async function onDragDrop(
  handlers: DragDropHandlers,
): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  try {
    const { getCurrentWebview } = await import("@tauri-apps/api/webview");
    const webview = getCurrentWebview();
    return await webview.onDragDropEvent((event) => {
      const payload = event.payload as any;
      if (payload.type === "enter") handlers.onEnter?.();
      else if (payload.type === "over") handlers.onOver?.();
      else if (payload.type === "leave" || payload.type === "cancel")
        handlers.onLeave?.();
      else if (payload.type === "drop")
        handlers.onDrop?.(payload.paths || []);
    });
  } catch {
    // Fallback for older Tauri versions
    try {
      const { getCurrentWebview } = await import("@tauri-apps/api/webview");
      const webview = getCurrentWebview();
      const unlisteners: Array<() => void> = [];
      unlisteners.push(
        await webview.listen<any>("tauri://drag-enter", () => handlers.onEnter?.()),
      );
      unlisteners.push(
        await webview.listen<any>("tauri://drag-over", () => handlers.onOver?.()),
      );
      unlisteners.push(
        await webview.listen<any>("tauri://drag-leave", () => handlers.onLeave?.()),
      );
      unlisteners.push(
        await webview.listen<any>("tauri://drag-drop", (ev) =>
          handlers.onDrop?.((ev as any).payload?.paths || []),
        ),
      );
      return () => unlisteners.forEach((u) => u());
    } catch {
      return () => {};
    }
  }
}

// ---------------------------------------------------------------------------
// Tauri updater & process (desktop-only, graceful no-ops on web)
// ---------------------------------------------------------------------------

export type UpdateInfo = {
  version: string;
  downloadAndInstall: (
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onProgress?: (progress: { event: string; data?: any }) => void,
  ) => Promise<void>;
};

export async function checkForUpdate(): Promise<UpdateInfo | null> {
  if (!IS_TAURI) return null;
  try {
    const { check } = await import("@tauri-apps/plugin-updater");
    const update = await check();
    if (!update) return null;
    return {
      version: update.version,
      downloadAndInstall: (onProgress) =>
        update.downloadAndInstall(onProgress),
    };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// File picker dialog (Tauri only; no-op on web)
// ---------------------------------------------------------------------------

export async function openFileDialog(options?: {
  directory?: boolean;
  multiple?: boolean;
  title?: string;
  filters?: { name: string; extensions: string[] }[];
}): Promise<string | null> {
  if (!IS_TAURI) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    directory: options?.directory,
    multiple: options?.multiple ?? false,
    title: options?.title,
    filters: options?.filters,
  });
  if (!selected) return null;
  return typeof selected === "string" ? selected : (selected as any)?.path ?? null;
}

/**
 * Show a native "Save File" dialog (Tauri only).
 * Returns the chosen path or null if cancelled.
 * On web, returns null — callers should fall back to browser download.
 */
export async function saveFileDialog(options?: {
  title?: string;
  defaultPath?: string;
  filters?: { name: string; extensions: string[] }[];
}): Promise<string | null> {
  if (!IS_TAURI) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  const selected = await save({
    title: options?.title,
    defaultPath: options?.defaultPath,
    filters: options?.filters,
  });
  return selected ?? null;
}

// ---------------------------------------------------------------------------
// Tauri updater & process
// ---------------------------------------------------------------------------

export async function relaunchApp(): Promise<void> {
  if (!IS_TAURI) {
    window.location.reload();
    return;
  }
  const { relaunch } = await import("@tauri-apps/plugin-process");
  await relaunch();
}

// ---------------------------------------------------------------------------
// Popup / detached windows
// ---------------------------------------------------------------------------

/**
 * Open a view in a detached popup window.
 * In Tauri, creates a new WebviewWindow; in Web, uses window.open.
 */
export async function openPopupWindow(
  path: string,
  label: string,
  opts?: { width?: number; height?: number; title?: string },
): Promise<void> {
  const width = opts?.width ?? 1200;
  const height = opts?.height ?? 800;
  const title = opts?.title ?? label;

  if (IS_CAPACITOR) {
    // Mobile: popup windows not supported, navigate in-place or no-op
    return;
  }

  if (IS_TAURI) {
    try {
      const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
      new WebviewWindow(label, {
        url: path,
        title,
        width,
        height,
        center: true,
        decorations: true,
        resizable: true,
      });
    } catch (e) {
      window.open(path, label, `width=${width},height=${height}`);
    }
  } else {
    const left = (screen.width - width) / 2;
    const top = (screen.height - height) / 2;
    window.open(
      path,
      label,
      `width=${width},height=${height},left=${left},top=${top},resizable=yes`,
    );
  }
}

/** Whether popup windows are available on the current platform. */
export function canOpenPopupWindow(): boolean {
  return !IS_CAPACITOR;
}

// ---------------------------------------------------------------------------
// Re-exports from sub-modules
// ---------------------------------------------------------------------------

export { authFetch, login, logout, checkAuth } from "./auth";
export { onWsEvent, disconnectWs, isWsConnected, reconnectWsNow } from "./websocket";
export type { WsEventHandler } from "./websocket";
export {
  getServers, getActiveServer, getActiveServerId,
  addServer, updateServer, removeServer, setActiveServer, testConnection,
} from "./servers";
export type { ServerEntry } from "./servers";
export { logger } from "./logger";
