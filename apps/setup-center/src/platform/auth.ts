// ─── Auth Token Management ───
// Handles JWT access/refresh token lifecycle.
// Tauri local: no-ops (backend exempts 127.0.0.1). Tauri remote: same as Capacitor.

import { IS_TAURI, IS_CAPACITOR, IS_LOCAL_WEB } from "./detect";

const ACCESS_TOKEN_KEY = "openakita_access_token";

let _tauriRemoteMode = false;
let _localAuthMode = IS_LOCAL_WEB;
let _passwordUserSet = true;

/** Enable/disable auth for Tauri desktop connecting to a remote backend. */
export function setTauriRemoteMode(enabled: boolean): void {
  _tauriRemoteMode = enabled;
  // Reset for Tauri remote; IS_LOCAL_WEB always keeps local mode on.
  _localAuthMode = IS_LOCAL_WEB;
}
export function isTauriRemoteMode(): boolean { return _tauriRemoteMode; }

function needsAuth(): boolean {
  if (IS_LOCAL_WEB) return false;
  return !IS_TAURI || _tauriRemoteMode;
}

/** Cross-origin mode: Capacitor or Tauri remote — no httpOnly cookie refresh. */
function isCrossOriginMode(): boolean { return IS_CAPACITOR || _tauriRemoteMode; }

/** Returns true if the backend granted access via local IP exemption (no token needed). */
export function isLocalAuthMode(): boolean { return _localAuthMode; }

export function setLocalAuthMode(v: boolean): void { _localAuthMode = v; }

/** Returns true if the user has explicitly set a custom password (vs auto-generated). */
export function isPasswordUserSet(): boolean { return _passwordUserSet; }

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

export function getAccessToken(): string | null {
  if (!needsAuth()) return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setAccessToken(token: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

export function clearAccessToken(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
}

// ---------------------------------------------------------------------------
// JWT payload parsing (no verification — that's the server's job)
// ---------------------------------------------------------------------------

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = parts[1];
    const padded = payload + "=".repeat((4 - (payload.length % 4)) % 4);
    return JSON.parse(atob(padded.replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return null;
  }
}

export function isTokenExpiringSoon(token: string, thresholdSeconds = 3600): boolean {
  const payload = parseJwtPayload(token);
  if (!payload || typeof payload.exp !== "number") return true;
  return payload.exp - Date.now() / 1000 < thresholdSeconds;
}

// ---------------------------------------------------------------------------
// Refresh flow
// ---------------------------------------------------------------------------

let _refreshPromise: Promise<string | null> | null = null;

/** Dispatched when refresh fails — App listens and redirects to login. */
export const AUTH_EXPIRED_EVENT = "openakita-auth-expired";

export async function refreshAccessToken(apiBase = ""): Promise<string | null> {
  if (_localAuthMode) return null;
  // Cross-origin (Capacitor / Tauri remote): httpOnly cookie refresh is unreliable
  if (isCrossOriginMode()) return null;
  if (_refreshPromise) return _refreshPromise;

  _refreshPromise = (async () => {
    try {
      const res = await fetch(`${apiBase}/api/auth/refresh`, {
        method: "POST",
        credentials: "include",
        signal: AbortSignal.timeout(10_000),
      });
      if (!res.ok) {
        clearAccessToken();
        window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
        return null;
      }
      const data = await res.json();
      if (data.access_token) {
        setAccessToken(data.access_token);
        return data.access_token as string;
      }
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      return null;
    } catch {
      return null;
    } finally {
      _refreshPromise = null;
    }
  })();

  return _refreshPromise;
}

// ---------------------------------------------------------------------------
// Auth-aware fetch wrapper
// ---------------------------------------------------------------------------

export async function authFetch(
  url: string,
  init?: RequestInit,
  apiBase = "",
): Promise<Response> {
  if (!needsAuth()) return fetch(url, init);

  // Local auth mode: backend grants access by IP, no token needed
  if (_localAuthMode) return fetch(url, init);

  let token = getAccessToken();

  if (isCrossOriginMode()) {
    // Cross-origin (Capacitor / Tauri remote): can't refresh via httpOnly cookie.
    // Use existing token as-is; 401 below will trigger re-login.
  } else if (!token || isTokenExpiringSoon(token)) {
    token = await refreshAccessToken(apiBase);
  }

  const headers = new Headers(init?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  // Snapshot body for potential retry (ReadableStream can only be consumed once)
  let retryInit = init;
  if (init?.body instanceof ReadableStream) {
    try {
      const [s1, s2] = init.body.tee();
      init = { ...init, body: s1 };
      retryInit = { ...init, body: s2 };
    } catch {
      // Stream already locked/consumed — retry will reuse original init (may fail, but won't break first request)
    }
  }

  const credOpts: RequestInit = isCrossOriginMode() ? {} : { credentials: "include" };
  const res = await fetch(url, { ...init, ...credOpts, headers });

  // If 401 and we had a token, try one refresh then retry
  if (res.status === 401 && token) {
    if (isCrossOriginMode()) {
      clearAccessToken();
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      return res;
    }
    const newToken = await refreshAccessToken(apiBase);
    if (newToken) {
      const retryHeaders = new Headers(retryInit?.headers);
      retryHeaders.set("Authorization", `Bearer ${newToken}`);
      return fetch(url, { ...retryInit, ...credOpts, headers: retryHeaders });
    }
  }

  return res;
}

// ---------------------------------------------------------------------------
// Login / Logout
// ---------------------------------------------------------------------------

export async function login(
  password: string,
  apiBase = "",
): Promise<{ success: boolean; error?: string }> {
  try {
    const fetchOpts: RequestInit = {
      method: "POST",
      signal: AbortSignal.timeout(IS_CAPACITOR ? 5_000 : 10_000),
    };
    if (isCrossOriginMode()) {
      fetchOpts.headers = { "Content-Type": "application/x-www-form-urlencoded" };
      fetchOpts.body = new URLSearchParams({ password }).toString();
    } else {
      fetchOpts.headers = { "Content-Type": "application/json" };
      fetchOpts.body = JSON.stringify({ password });
      fetchOpts.credentials = "include";
    }
    const res = await fetch(`${apiBase}/api/auth/login`, fetchOpts);
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: "Login failed" }));
      return { success: false, error: data.detail || `HTTP ${res.status}` };
    }
    const data = await res.json();
    if (data.access_token) {
      setAccessToken(data.access_token);
    }
    return { success: true };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}

export async function logout(apiBase = ""): Promise<void> {
  try {
    const opts: RequestInit = {
      method: "POST",
      signal: AbortSignal.timeout(5_000),
    };
    if (!isCrossOriginMode()) opts.credentials = "include";
    const token = getAccessToken();
    if (token) {
      opts.headers = { Authorization: `Bearer ${token}` };
    }
    await fetch(`${apiBase}/api/auth/logout`, opts);
  } catch { /* ignore */ }
  clearAccessToken();
  _localAuthMode = false;
  try { sessionStorage.removeItem(LOCAL_AUTH_SESSION_KEY); } catch { /* */ }
}

// ---------------------------------------------------------------------------
// Global fetch interceptor — auto-adds auth token to same-origin API calls
// ---------------------------------------------------------------------------

let _interceptorInstalled = false;

export function installFetchInterceptor(): void {
  if (!needsAuth() || _interceptorInstalled) return;
  _interceptorInstalled = true;

  const originalFetch = window.fetch.bind(window);
  window.fetch = async function (input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    if (_localAuthMode) return originalFetch(input, init);

    const url = typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
    const isApi = url.startsWith("/") || url.startsWith(window.location.origin) || url.includes("/api/");

    if (isApi) {
      const token = getAccessToken();
      if (token) {
        const base = init?.headers
          ?? (input instanceof Request ? input.headers : undefined);
        const headers = new Headers(base);
        if (!headers.has("Authorization")) {
          headers.set("Authorization", `Bearer ${token}`);
        }
        init = { ...init, headers };
      }
    }

    return originalFetch(input, init);
  } as typeof fetch;
}

// ---------------------------------------------------------------------------
// Auth check
// ---------------------------------------------------------------------------

const LOCAL_AUTH_SESSION_KEY = "openakita_auth_local";

/** Restore local-auth mode from sessionStorage (survives page refresh). */
export function tryRestoreLocalAuth(): boolean {
  try {
    if (sessionStorage.getItem(LOCAL_AUTH_SESSION_KEY) === "1") {
      _localAuthMode = true;
      return true;
    }
  } catch { /* sessionStorage unavailable */ }
  return false;
}

export async function checkAuth(apiBase = ""): Promise<boolean> {
  const maxAttempts = IS_CAPACITOR ? 1 : 3;
  const timeoutMs = IS_CAPACITOR ? 3_000 : 5_000;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const token = getAccessToken();
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const fetchOpts: RequestInit = {
        headers,
        signal: AbortSignal.timeout(timeoutMs),
      };
      if (!isCrossOriginMode()) fetchOpts.credentials = "include";
      const res = await fetch(`${apiBase}/api/auth/check`, fetchOpts);
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated === true) {
          if (data.method === "local") {
            _localAuthMode = true;
            try { sessionStorage.setItem(LOCAL_AUTH_SESSION_KEY, "1"); } catch { /* */ }
          }
          if (data.password_user_set === false) _passwordUserSet = false;
          return true;
        }
      }
      // Transient HTTP error (500/502/503) — retry before falling through
      if (!res.ok && attempt < maxAttempts) {
        await new Promise((r) => setTimeout(r, attempt * 1000));
        continue;
      }
      // Final attempt: try silent refresh via httpOnly cookie
      const refreshed = await refreshAccessToken(apiBase);
      if (refreshed) return true;
      return false;
    } catch {
      if (attempt < maxAttempts) {
        await new Promise((r) => setTimeout(r, attempt * 1000));
        continue;
      }
      return false;
    }
  }
  return false;
}
