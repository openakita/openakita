/**
 * Plugin Bridge Host — handles postMessage communication between the
 * OpenAkita desktop app (host) and plugin UIs loaded inside iframes.
 *
 * Protocol: fixed envelope (BridgeMessage), capability negotiation,
 * unknown types gracefully ignored.
 */

export interface BridgeMessage {
  __akita_bridge: true;
  version: number;
  type: string;
  requestId?: string;
  payload?: Record<string, unknown>;
}

export interface BridgeHostOptions {
  pluginId: string;
  iframe: HTMLIFrameElement;
  apiBase: string;
  theme: string;
  locale: string;
  onNotification?: (opts: { title: string; body: string; type?: string }) => void;
  onNavigate?: (viewId: string) => void;
}

const BRIDGE_VERSION = 1;

const HOST_CAPABILITIES = [
  "theme",
  "locale",
  "notification",
  "open-external",
  "upload",
  "download",
  "file-download",
  "show-in-folder",
  "pick-folder",
  "clipboard",
  "navigate",
  "api-proxy",
  "websocket-events",
  "config",
  // M3 Infra fix: advertise the finance-auto native command surface so
  // the plugin iframe can dispatch the 4 Tauri commands registered in
  // `apps/setup-center/src-tauri/src/finance.rs`. Plugins probe this
  // list during handshake to decide whether to enable native buttons.
  "finance-native",
] as const;

function isBridgeMessage(data: unknown): data is BridgeMessage {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as BridgeMessage).__akita_bridge === true &&
    typeof (data as BridgeMessage).type === "string"
  );
}

export class PluginBridgeHost {
  private pluginId: string;
  private iframe: HTMLIFrameElement;
  private apiBase: string;
  private theme: string;
  private locale: string;
  private onNotification?: BridgeHostOptions["onNotification"];
  private onNavigate?: BridgeHostOptions["onNavigate"];
  private disposed = false;
  private boundHandler: (e: MessageEvent) => void;

  constructor(opts: BridgeHostOptions) {
    this.pluginId = opts.pluginId;
    this.iframe = opts.iframe;
    this.apiBase = opts.apiBase;
    this.theme = opts.theme;
    this.locale = opts.locale;
    this.onNotification = opts.onNotification;
    this.onNavigate = opts.onNavigate;
    this.boundHandler = this.handleMessage.bind(this);
    window.addEventListener("message", this.boundHandler);
  }

  dispose() {
    this.disposed = true;
    window.removeEventListener("message", this.boundHandler);
  }

  private post(msg: Omit<BridgeMessage, "__akita_bridge" | "version">) {
    if (this.disposed || !this.iframe.contentWindow) return;
    const full: BridgeMessage = {
      __akita_bridge: true,
      version: BRIDGE_VERSION,
      ...msg,
    };
    this.iframe.contentWindow.postMessage(full, "*");
  }

  /** Send theme/locale updates to the plugin */
  sendThemeChange(theme: string) {
    this.theme = theme;
    this.post({ type: "bridge:theme-change", payload: { theme } });
  }

  sendLocaleChange(locale: string) {
    this.locale = locale;
    this.post({ type: "bridge:locale-change", payload: { locale } });
  }

  /** Forward a WebSocket event to the plugin */
  sendEvent(eventType: string, data: unknown) {
    this.post({ type: "bridge:event", payload: { eventType, data } });
  }

  private handleMessage(event: MessageEvent) {
    if (this.disposed) return;
    if (event.source !== this.iframe.contentWindow) return;

    const data = event.data;
    if (!isBridgeMessage(data)) return;

    switch (data.type) {
      case "bridge:ready":
        this.post({
          type: "bridge:init",
          requestId: data.requestId,
          payload: {
            theme: this.theme,
            locale: this.locale,
            apiBase: this.apiBase,
            pluginId: this.pluginId,
          },
        });
        break;

      case "bridge:handshake":
        this.post({
          type: "bridge:handshake-ack",
          requestId: data.requestId,
          payload: {
            hostVersion: "1.0.0",
            capabilities: [...HOST_CAPABILITIES],
            bridgeVersion: BRIDGE_VERSION,
          },
        });
        break;

      case "bridge:api-request":
        this.handleApiRequest(data);
        break;

      case "bridge:upload":
        this.handleUpload(data);
        break;

      case "bridge:notification":
        if (this.onNotification && data.payload) {
          this.onNotification(data.payload as { title: string; body: string; type?: string });
        }
        break;

      case "bridge:navigate":
        if (this.onNavigate && data.payload?.viewId) {
          this.onNavigate(data.payload.viewId as string);
        }
        break;

      case "bridge:download":
        this.handleDownload(data);
        break;

      case "bridge:open-external":
        this.handleOpenExternal(data);
        break;

      case "bridge:show-in-folder":
        this.handleShowInFolder(data);
        break;

      case "bridge:pick-folder":
        this.handlePickFolder(data);
        break;

      case "bridge:clipboard":
        this.handleClipboard(data);
        break;

      case "bridge:finance-native-invoke":
        this.handleFinanceNativeInvoke(data);
        break;

      default:
        this.post({
          type: "bridge:unsupported",
          requestId: data.requestId,
          payload: { originalType: data.type },
        });
        break;
    }
  }

  private async handleOpenExternal(msg: BridgeMessage) {
    const url = (msg.payload?.url as string) || "";
    if (!url) {
      this.post({
        type: "bridge:open-external-ack",
        requestId: msg.requestId,
        payload: { ok: false, error: "Missing url" },
      });
      return;
    }
    try {
      const { openExternalUrl } = await import("../platform");
      await openExternalUrl(url);
      this.post({
        type: "bridge:open-external-ack",
        requestId: msg.requestId,
        payload: { ok: true },
      });
    } catch (e) {
      this.post({
        type: "bridge:open-external-ack",
        requestId: msg.requestId,
        payload: { ok: false, error: String(e) },
      });
    }
  }

  /**
   * Handle file download requests from plugin iframes.
   *
   * Uses Tauri's native `download_file` command (reqwest → disk) which
   * reliably works in WebView2/WKWebView without blob-URL limitations.
   * Falls back to <a download> for web mode.
   */
  private async handleDownload(msg: BridgeMessage) {
    const { url: rawUrl, filename, localPath } = (msg.payload || {}) as {
      url?: string;
      filename?: string;
      localPath?: string;
    };
    if (!rawUrl && !localPath) {
      this.post({
        type: "bridge:download-ack",
        requestId: msg.requestId,
        payload: { ok: false, error: "Missing url or localPath" },
      });
      return;
    }

    const fname = filename || "download";

    try {
      const platform = await import("../platform");
      const { isTauriRemoteMode } = await import("../platform/auth");
      const savedPath = localPath
        ? await platform.copyFileToDownloads(localPath, fname)
        : await (async () => {
            const resolvedUrl = rawUrl!.startsWith("http") ? rawUrl! : `${this.apiBase}${rawUrl}`;
            if (platform.IS_TAURI && !isTauriRemoteMode()) {
              return platform.downloadFile(resolvedUrl, fname);
            }

            const resp = this.isBackendUrl(resolvedUrl)
              ? await this.authenticatedFetch(resolvedUrl)
              : await fetch(resolvedUrl);
            if (!resp.ok) throw new Error(`Download failed: HTTP ${resp.status}`);
            const objectUrl = URL.createObjectURL(await resp.blob());
            try {
              return await platform.downloadFile(objectUrl, fname);
            } finally {
              setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
            }
          })();
      this.post({
        type: "bridge:download-ack",
        requestId: msg.requestId,
        payload: { ok: true, path: savedPath },
      });
    } catch (e) {
      this.post({
        type: "bridge:download-ack",
        requestId: msg.requestId,
        payload: { ok: false, error: String(e) },
      });
    }
  }

  private async handleShowInFolder(msg: BridgeMessage) {
    const { path } = (msg.payload || {}) as { path?: string };
    if (!path) return;
    try {
      const { showInFolder } = await import("../platform");
      await showInFolder(path);
      this.post({ type: "bridge:show-in-folder-ack", requestId: msg.requestId, payload: { ok: true } });
    } catch (e) {
      this.post({ type: "bridge:show-in-folder-ack", requestId: msg.requestId, payload: { ok: false, error: String(e) } });
    }
  }

  private async handlePickFolder(msg: BridgeMessage) {
    const { title } = (msg.payload || {}) as { title?: string };
    try {
      const { openFileDialog } = await import("../platform");
      const selected = await openFileDialog({ directory: true, title: title || "选择文件夹" });
      this.post({ type: "bridge:pick-folder-ack", requestId: msg.requestId, payload: { ok: true, path: selected } });
    } catch (e) {
      this.post({ type: "bridge:pick-folder-ack", requestId: msg.requestId, payload: { ok: false, error: String(e) } });
    }
  }

  private handleClipboard(msg: BridgeMessage) {
    const text = (msg.payload?.text as string) || "";
    if (!text) return;
    navigator.clipboard.writeText(text).catch(() => {});
    this.post({ type: "bridge:clipboard-ack", requestId: msg.requestId, payload: { ok: true } });
  }

  /**
   * Dispatch a finance-auto native command request from the plugin
   * iframe. Routes through the allow-listed wrappers in
   * `lib/native/finance-native.ts` so arbitrary `invoke()` cannot be
   * issued by plugin code. The ack envelope mirrors the wrapper's
   * `NativeResult<T>` shape so callers can branch on `kind`.
   */
  private async handleFinanceNativeInvoke(msg: BridgeMessage) {
    const { command, args } = (msg.payload || {}) as {
      command?: string;
      args?: Record<string, unknown>;
    };
    if (!command) {
      this.post({
        type: "bridge:finance-native-ack",
        requestId: msg.requestId,
        payload: { kind: "error", error: "missing command" },
      });
      return;
    }
    try {
      const native = await import("./native/finance-native");
      const result = await native.dispatchFinanceNative(command, args);
      this.post({
        type: "bridge:finance-native-ack",
        requestId: msg.requestId,
        payload: result,
      });
    } catch (e) {
      this.post({
        type: "bridge:finance-native-ack",
        requestId: msg.requestId,
        payload: { kind: "error", error: String(e) },
      });
    }
  }

  private async handleApiRequest(msg: BridgeMessage) {
    const { method, path, body } = (msg.payload || {}) as {
      method?: string;
      path?: string;
      body?: unknown;
    };
    if (!method || !path) {
      this.post({
        type: "bridge:api-response",
        requestId: msg.requestId,
        payload: { ok: false, status: 400, error: "Missing method or path" },
      });
      return;
    }

    try {
      const url = this.resolvePluginApiUrl(path);
      const fetchOpts: RequestInit = {
        method,
        headers: { "Content-Type": "application/json" },
      };
      if (body && method !== "GET" && method !== "HEAD") {
        fetchOpts.body = JSON.stringify(body);
      }
      const resp = await this.authenticatedFetch(url, fetchOpts);
      let respBody: unknown;
      const ct = resp.headers.get("content-type") || "";
      if (ct.includes("application/json")) {
        respBody = await resp.json();
      } else {
        respBody = await resp.text();
      }
      this.post({
        type: "bridge:api-response",
        requestId: msg.requestId,
        payload: { ok: resp.ok, status: resp.status, body: respBody },
      });
    } catch (e) {
      this.post({
        type: "bridge:api-response",
        requestId: msg.requestId,
        payload: { ok: false, status: 0, error: String(e) },
      });
    }
  }

  private async handleUpload(msg: BridgeMessage) {
    const { path, entries } = (msg.payload || {}) as {
      path?: string;
      entries?: Array<{ name?: string; value?: unknown; filename?: string }>;
    };
    if (!path || !Array.isArray(entries)) {
      this.post({
        type: "bridge:upload-ack",
        requestId: msg.requestId,
        payload: { ok: false, status: 400, error: "Missing upload path or entries" },
      });
      return;
    }

    try {
      const url = this.resolvePluginApiUrl(path);
      const formData = new FormData();
      for (const entry of entries) {
        if (!entry.name) continue;
        if (entry.value instanceof Blob) {
          formData.append(entry.name, entry.value, entry.filename);
        } else {
          formData.append(entry.name, String(entry.value ?? ""));
        }
      }
      const resp = await this.authenticatedFetch(url, { method: "POST", body: formData });
      const contentType = resp.headers.get("content-type") || "";
      const body = contentType.includes("application/json")
        ? await resp.json()
        : await resp.text();
      this.post({
        type: "bridge:upload-ack",
        requestId: msg.requestId,
        payload: { ok: resp.ok, status: resp.status, body },
      });
    } catch (e) {
      this.post({
        type: "bridge:upload-ack",
        requestId: msg.requestId,
        payload: { ok: false, status: 0, error: String(e) },
      });
    }
  }

  private async authenticatedFetch(url: string, init?: RequestInit): Promise<Response> {
    const { authFetch } = await import("../platform/auth");
    let authBase = this.apiBase;
    try {
      authBase = new URL(this.apiBase || window.location.origin).origin;
    } catch { /* relative local-web base */ }
    return authFetch(url, init, authBase);
  }

  private isBackendUrl(url: string): boolean {
    try {
      const backendOrigin = new URL(this.apiBase || window.location.origin).origin;
      return new URL(url, backendOrigin).origin === backendOrigin;
    } catch {
      return false;
    }
  }

  private resolvePluginApiUrl(path: string): string {
    const backendOrigin = new URL(this.apiBase || window.location.origin).origin;
    const url = new URL(path, backendOrigin);
    const pluginPrefix = `/api/plugins/${encodeURIComponent(this.pluginId)}`;
    if (
      url.origin !== backendOrigin
      || (url.pathname !== pluginPrefix && !url.pathname.startsWith(`${pluginPrefix}/`))
    ) {
      throw new Error("Plugin bridge requests must target the current plugin API");
    }
    return url.toString();
  }
}
