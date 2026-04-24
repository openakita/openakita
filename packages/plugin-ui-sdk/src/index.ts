/**
 * @openakita/plugin-ui-sdk — Frontend SDK for Plugin 2.0 UIs.
 *
 * Provides PluginBridge for communicating with the OpenAkita host app
 * via postMessage. Zero dependencies, < 5 KB gzipped.
 */

export interface BridgeMessage {
  __akita_bridge: true;
  version: number;
  type: string;
  requestId?: string;
  payload?: Record<string, unknown>;
}

export interface BridgeContext {
  theme: string;
  locale: string;
  apiBase: string;
  pluginId: string;
}

export interface NotificationOptions {
  title: string;
  body: string;
  type?: "success" | "error" | "warning" | "info";
}

const PROTOCOL_VERSION = 1;

function uid(): string {
  return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

function isBridgeMessage(data: unknown): data is BridgeMessage {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as BridgeMessage).__akita_bridge === true
  );
}

type EventHandler = (...args: unknown[]) => void;

export class PluginBridge {
  private _context: BridgeContext | null = null;
  private _hostCapabilities: string[] = [];
  private _pendingRequests = new Map<string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private _eventHandlers = new Map<string, Set<EventHandler>>();
  private _boundHandler: (e: MessageEvent) => void;
  private _initResolve: ((ctx: BridgeContext) => void) | null = null;

  constructor() {
    this._boundHandler = this._handleMessage.bind(this);
    window.addEventListener("message", this._boundHandler);
  }

  /**
   * Initialize the bridge — waits for the host to respond with
   * theme, locale, apiBase, and pluginId.
   */
  async init(): Promise<BridgeContext> {
    return new Promise<BridgeContext>((resolve) => {
      this._initResolve = resolve;
      this._post({ type: "bridge:ready" });

      const handshakeId = uid();
      this._post({
        type: "bridge:handshake",
        requestId: handshakeId,
        payload: {
          sdkVersion: "1.0.0",
          requiredCapabilities: [],
        },
      });
    });
  }

  /** Check if the host supports a given capability. */
  supports(capability: string): boolean {
    return this._hostCapabilities.includes(capability);
  }

  /** Listen for theme changes from the host. */
  onThemeChange(cb: (theme: string) => void): () => void {
    return this.on("bridge:theme-change", (payload: Record<string, unknown>) => {
      cb(payload.theme as string);
    });
  }

  /** Listen for locale changes from the host. */
  onLocaleChange(cb: (locale: string) => void): () => void {
    return this.on("bridge:locale-change", (payload: Record<string, unknown>) => {
      cb(payload.locale as string);
    });
  }

  /**
   * Make an API request proxied through the host (with auth).
   * Path is relative to the host API base (e.g. "/api/plugins/my-plugin/tasks").
   */
  async apiCall(method: string, path: string, body?: unknown): Promise<{ ok: boolean; status: number; body: unknown }> {
    const id = uid();
    return new Promise((resolve, reject) => {
      this._pendingRequests.set(id, {
        resolve: (v) => resolve(v as { ok: boolean; status: number; body: unknown }),
        reject,
      });
      this._post({
        type: "bridge:api-request",
        requestId: id,
        payload: { method, path, body },
      });
      setTimeout(() => {
        if (this._pendingRequests.has(id)) {
          this._pendingRequests.delete(id);
          reject(new Error("API request timed out"));
        }
      }, 30_000);
    });
  }

  /**
   * Convenience wrapper for plugin-specific API calls.
   * Automatically prepends `/api/plugins/{pluginId}` to the path.
   */
  async pluginApi(method: string, path: string, body?: unknown): Promise<{ ok: boolean; status: number; body: unknown }> {
    if (!this._context) throw new Error("Bridge not initialized");
    const fullPath = `/api/plugins/${this._context.pluginId}${path.startsWith("/") ? path : "/" + path}`;
    return this.apiCall(method, fullPath, body);
  }

  /** Show a toast notification in the host UI. */
  showNotification(opts: NotificationOptions): void {
    this._post({ type: "bridge:notification", payload: opts as unknown as Record<string, unknown> });
  }

  /** Request the host to navigate to a different view. */
  navigateTo(viewId: string): void {
    this._post({ type: "bridge:navigate", payload: { viewId } });
  }

  /** Copy text to clipboard via the host. */
  async copyToClipboard(text: string): Promise<void> {
    const id = uid();
    return new Promise((resolve) => {
      this._pendingRequests.set(id, { resolve: () => resolve(), reject: () => resolve() });
      this._post({ type: "bridge:clipboard", requestId: id, payload: { text } });
      setTimeout(() => {
        this._pendingRequests.delete(id);
        resolve();
      }, 3000);
    });
  }

  /**
   * Safe call — if the host doesn't support the capability, returns null
   * instead of throwing.
   */
  async tryCall(capability: string, method: string, path: string, body?: unknown): Promise<unknown | null> {
    if (!this.supports(capability)) return null;
    try {
      return await this.apiCall(method, path, body);
    } catch {
      return null;
    }
  }

  /** Register an event listener. Returns an unsubscribe function. */
  on(event: string, handler: EventHandler): () => void {
    if (!this._eventHandlers.has(event)) {
      this._eventHandlers.set(event, new Set());
    }
    this._eventHandlers.get(event)!.add(handler);
    return () => this.off(event, handler);
  }

  /** Unregister an event listener. */
  off(event: string, handler: EventHandler): void {
    this._eventHandlers.get(event)?.delete(handler);
  }

  /** Clean up — call when your plugin UI is shutting down. */
  dispose(): void {
    window.removeEventListener("message", this._boundHandler);
    this._pendingRequests.clear();
    this._eventHandlers.clear();
  }

  private _post(msg: Omit<BridgeMessage, "__akita_bridge" | "version">): void {
    const full: BridgeMessage = {
      __akita_bridge: true,
      version: PROTOCOL_VERSION,
      ...msg,
    };
    window.parent.postMessage(full, "*");
  }

  private _handleMessage(event: MessageEvent): void {
    const data = event.data;
    if (!isBridgeMessage(data)) return;

    switch (data.type) {
      case "bridge:init":
        if (data.payload) {
          this._context = {
            theme: data.payload.theme as string,
            locale: data.payload.locale as string,
            apiBase: data.payload.apiBase as string,
            pluginId: data.payload.pluginId as string,
          };
          if (this._initResolve) {
            this._initResolve(this._context);
            this._initResolve = null;
          }
        }
        break;

      case "bridge:handshake-ack":
        if (data.payload) {
          this._hostCapabilities = (data.payload.capabilities as string[]) || [];
        }
        break;

      case "bridge:api-response":
      case "bridge:clipboard-ack":
        if (data.requestId && this._pendingRequests.has(data.requestId)) {
          const { resolve } = this._pendingRequests.get(data.requestId)!;
          this._pendingRequests.delete(data.requestId);
          resolve(data.payload);
        }
        break;

      case "bridge:theme-change":
      case "bridge:locale-change":
      case "bridge:event":
        this._emit(data.type, data.payload || {});
        break;

      case "bridge:unsupported":
        break;

      default:
        this._emit(data.type, data.payload || {});
        break;
    }
  }

  private _emit(event: string, ...args: unknown[]): void {
    const handlers = this._eventHandlers.get(event);
    if (handlers) {
      for (const h of handlers) {
        try { h(...args); } catch { /* plugin handler error — ignore */ }
      }
    }
  }
}

