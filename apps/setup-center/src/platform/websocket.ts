// ─── WebSocket Event Client ───
// Works in both Web and Tauri modes.
// Auto-reconnects on disconnect with exponential backoff.

import { IS_TAURI, IS_CAPACITOR } from "./detect";
import { getAccessToken, isTokenExpiringSoon, refreshAccessToken, isTauriRemoteMode } from "./auth";
import { getActiveServer } from "./servers";
import { logger } from "./logger";

const DEFAULT_TAURI_LOCAL_API_BASE = "http://127.0.0.1:18900";

/**
 * Whether WS should be skipped entirely.
 * Org/IM/chat real-time updates also rely on the local backend WebSocket in
 * Tauri desktop mode, so local desktop must not be skipped here.
 */
function _skipWs(): boolean {
  return false;
}

export type WsEventHandler = (event: string, data: unknown) => void;

let _ws: WebSocket | null = null;
let _handlers: WsEventHandler[] = [];
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let _reconnectDelay = 1000;
let _reconnectAttempts = 0;
// PR-F1: 后端崩溃自愈窗口可能很久（Tauri 心跳 + 重启需要 30s+）；
// 上限 120 次（≈ 1h）会让用户看到"放弃重连"的死状态。改成无限重连，
// 但用指数退避到 60s + jitter，防止后端起来时被一波重连风暴冲击。
const MAX_RECONNECT_ATTEMPTS = Number.POSITIVE_INFINITY;
const MAX_RECONNECT_DELAY = 60000;
let _connected = false;
let _intentionallyClosed = false;
let _apiBaseUrlOverride = "";

// 事件级去重：在 200ms 滑窗内，相同 (event + payload) 只投递一次。
// 主要兜底两种情况：
//  1) React.StrictMode dev 双 mount 把同一 useEffect 注册两遍
//  2) Vite HMR 模块热替换后，旧 closure 残留在 _handlers 内
// 后端 manager.broadcast 写入的 ts 字段不参与 key，确保真正不同的事件
// （即便 payload 重复）只要时间间隔 > DEDUPE_WINDOW_MS 就不会被吃掉。
const DEDUPE_WINDOW_MS = 200;
const _dedupeMap = new Map<string, number>();
const _DEDUPE_MAX_KEYS = 1024;

function _dedupeKey(event: string, data: unknown): string {
  // 仅对结构化 data 做去重；非对象 payload 直接拼字符串
  let payload: string;
  try {
    payload = typeof data === "object" && data !== null
      ? JSON.stringify(data)
      : String(data);
  } catch {
    return `${event}::__unserializable__`;
  }
  // 控制单 key 长度，避免极端大 payload 撑爆内存
  if (payload.length > 512) payload = payload.slice(0, 512);
  return `${event}::${payload}`;
}

function _shouldDedupe(event: string, data: unknown, now: number): boolean {
  const key = _dedupeKey(event, data);
  const last = _dedupeMap.get(key);
  if (last !== undefined && now - last < DEDUPE_WINDOW_MS) {
    _dedupeMap.set(key, now);
    return true;
  }
  _dedupeMap.set(key, now);
  // 简单 LRU：超过上限时清掉最旧的若干项
  if (_dedupeMap.size > _DEDUPE_MAX_KEYS) {
    const cutoff = now - DEDUPE_WINDOW_MS * 4;
    for (const [k, ts] of _dedupeMap) {
      if (ts < cutoff) _dedupeMap.delete(k);
      if (_dedupeMap.size <= _DEDUPE_MAX_KEYS) break;
    }
  }
  return false;
}

function _normalizeApiBaseUrl(apiBaseUrl: string | null | undefined): string {
  const raw = (apiBaseUrl || "").trim();
  if (!raw) return "";
  try {
    return new URL(raw).toString().replace(/\/$/, "");
  } catch {
    logger.warn("WS", "Ignoring invalid API base URL override", { apiBaseUrl: raw });
    return "";
  }
}

export function setWsApiBaseUrl(apiBaseUrl: string | null | undefined): void {
  _apiBaseUrlOverride = _normalizeApiBaseUrl(apiBaseUrl);
}

function getWsUrl(): string {
  let host: string;
  let proto: string;

  if (IS_CAPACITOR) {
    const serverUrl = _apiBaseUrlOverride || getActiveServer()?.url || "";
    if (!serverUrl) return "";
    const url = new URL(serverUrl);
    host = url.host;
    proto = url.protocol === "https:" ? "wss:" : "ws:";
  } else if (IS_TAURI) {
    const baseUrl = _apiBaseUrlOverride || (isTauriRemoteMode() ? "" : DEFAULT_TAURI_LOCAL_API_BASE);
    if (!baseUrl) return "";
    const url = new URL(baseUrl);
    host = url.host;
    proto = url.protocol === "https:" ? "wss:" : "ws:";
  } else {
    const loc = window.location;
    host = loc.host;
    proto = loc.protocol === "https:" ? "wss:" : "ws:";
  }

  const token = getAccessToken();
  const params = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}//${host}/ws/events${params}`;
}

function _connect(): void {
  if (_ws) return;
  _intentionallyClosed = false;

  try {
    _ws = new WebSocket(getWsUrl());
  } catch {
    _scheduleReconnect();
    return;
  }

  _ws.onopen = () => {
    _connected = true;
    _reconnectDelay = 1000;
    _reconnectAttempts = 0;
  };

  _ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      const event = msg.event as string;
      const data = msg.data;
      if (event === "ping") {
        _ws?.send("ping");
        return;
      }
      // 事件级去重：抑制 200ms 内同 (event + payload) 的重复投递
      if (_shouldDedupe(event, data, Date.now())) return;
      // handler 集合去重：兜底防御 _handlers 内出现同函数引用多份
      // （Vite HMR 残留 / 异常路径累积），保证每个 handler 只被触发一次
      const seen = new Set<WsEventHandler>();
      for (const handler of _handlers) {
        if (seen.has(handler)) continue;
        seen.add(handler);
        try {
          handler(event, data);
        } catch (e) {
          logger.error("WS", "Event handler error", { error: String(e) });
        }
      }
    } catch { /* ignore non-JSON */ }
  };

  _ws.onclose = () => {
    _ws = null;
    _connected = false;
    if (!_intentionallyClosed) {
      _scheduleReconnect();
    }
  };

  _ws.onerror = () => {
    _ws?.close();
  };
}

function _scheduleReconnect(): void {
  if (_reconnectTimer || _intentionallyClosed) return;
  _reconnectAttempts++;
  if (Number.isFinite(MAX_RECONNECT_ATTEMPTS) && _reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
    logger.warn("WS", `Gave up reconnecting after ${MAX_RECONNECT_ATTEMPTS} attempts`);
    return;
  }
  // PR-F1: 指数退避到 60s + ±25% jitter（避免后端起来时雷鸣群效应）
  const jitter = 1 + (Math.random() * 0.5 - 0.25);
  const delayWithJitter = Math.max(500, Math.round(_reconnectDelay * jitter));
  _reconnectTimer = setTimeout(async () => {
    _reconnectTimer = null;
    _reconnectDelay = Math.min(_reconnectDelay * 2, MAX_RECONNECT_DELAY);
    const token = getAccessToken();
    if (!token || isTokenExpiringSoon(token, 60)) {
      await refreshAccessToken().catch(() => {});
    }
    _connect();
  }, delayWithJitter);
}

/**
 * Subscribe to all WebSocket events. Returns unsubscribe function.
 * Works in both Web and Tauri modes.
 */
export function onWsEvent(handler: WsEventHandler): () => void {
  if (_skipWs()) return () => {};

  // 注册级去重：同一 handler 函数引用重复 onWsEvent 不会进数组多次。
  // 主要防御 React.StrictMode dev 双 mount + Vite HMR 旧闭包残留。
  if (!_handlers.includes(handler)) {
    _handlers.push(handler);
  }
  // Ensure connection is started
  if (!_ws && !_reconnectTimer) {
    _connect();
  }

  return () => {
    _handlers = _handlers.filter((h) => h !== handler);
    // If no more handlers, disconnect
    if (_handlers.length === 0) {
      disconnectWs();
    }
  };
}

export function disconnectWs(): void {
  _intentionallyClosed = true;
  _reconnectAttempts = 0;
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  if (_ws) {
    _ws.close();
    _ws = null;
  }
  _connected = false;
}

/**
 * Immediately reconnect WebSocket (e.g. after app returns from background).
 * Resets backoff and attempts counter. No-op if no handlers are registered.
 */
export function reconnectWsNow(): void {
  if (_skipWs()) return;
  _intentionallyClosed = false;
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  _reconnectDelay = 1000;
  _reconnectAttempts = 0;
  if (_ws) {
    try { _ws.close(); } catch { /* ignore */ }
    _ws = null;
  }
  _connected = false;
  if (_handlers.length > 0) _connect();
}

export function isWsConnected(): boolean {
  return _connected;
}

// Vite HMR：模块热替换前清空 handler 列表并断开旧 WebSocket，
// 防止旧的 closure 残留在 _handlers 内造成事件被多次投递。
// 仅 dev 生效（import.meta.hot 只在 Vite dev 中可用），生产构建为 undefined。
if (typeof import.meta !== "undefined" && (import.meta as ImportMeta & { hot?: { dispose: (cb: () => void) => void } }).hot) {
  (import.meta as ImportMeta & { hot?: { dispose: (cb: () => void) => void } }).hot!.dispose(() => {
    _handlers = [];
    _dedupeMap.clear();
    if (_ws) {
      try { _ws.close(); } catch { /* ignore */ }
      _ws = null;
    }
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer);
      _reconnectTimer = null;
    }
    _connected = false;
    _intentionallyClosed = true;
  });
}

