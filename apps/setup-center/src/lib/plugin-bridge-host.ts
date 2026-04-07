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
  "upload",
  "download",
  "clipboard",
  "navigate",
  "api-proxy",
  "websocket-events",
  "config",
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

      case "bridge:clipboard":
        if (data.payload?.text) {
          navigator.clipboard.writeText(data.payload.text as string).catch(() => {});
          this.post({
            type: "bridge:clipboard-ack",
            requestId: data.requestId,
            payload: { ok: true },
          });
        }
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

    const url = path.startsWith("http") ? path : `${this.apiBase}${path}`;
    try {
      const fetchOpts: RequestInit = {
        method,
        headers: { "Content-Type": "application/json" },
      };
      if (body && method !== "GET" && method !== "HEAD") {
        fetchOpts.body = JSON.stringify(body);
      }
      const resp = await fetch(url, fetchOpts);
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
}
