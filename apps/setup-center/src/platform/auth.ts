// ─── Web Auth Token Management ───
// Handles JWT access/refresh token lifecycle for web mode.
// In Tauri mode, all functions are no-ops (local requests are exempt).

import { IS_WEB, IS_CAPACITOR } from "./detect";

const ACCESS_TOKEN_KEY = "openakita_access_token";

let _localAuthMode = false;
let _passwordUserSet = true;

/** Returns true if the backend granted access via local IP exemption (no token needed). */
export function isLocalAuthMode(): boolean { return _localAuthMode; }

export function setLocalAuthMode(v: boolean): void { _localAuthMode = v; }

/** Returns true if the user has explicitly set a custom password (vs auto-generated). */
export function isPasswordUserSet(): boolean { return _passwordUserSet; }

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

export function getAccessToken(): string | null {
  if (!IS_WEB) return null;
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
  // Local auth mode: no refresh needed — backend grants access by IP
  if (_localAuthMode) return null;
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
  if (!IS_WEB) return fetch(url, init);

  // Local auth mode: backend grants access by IP, no token needed
  if (_localAuthMode) return fetch(url, init);

  let token = getAccessToken();

  // Attempt silent refresh if token is missing or expiring
  if (!token || isTokenExpiringSoon(token)) {
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

  const res = await fetch(url, { ...init, headers, credentials: "include" });

  // If 401 and we had a token, try one refresh then retry
  if (res.status === 401 && token) {
    const newToken = await refreshAccessToken(apiBase);
    if (newToken) {
      const retryHeaders = new Headers(retryInit?.headers);
      retryHeaders.set("Authorization", `Bearer ${newToken}`);
      return fetch(url, { ...retryInit, headers: retryHeaders, credentials: "include" });
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
    const res = await fetch(`${apiBase}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
      credentials: "include",
      signal: AbortSignal.timeout(10_000),
    });
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
    await fetch(`${apiBase}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
      signal: AbortSignal.timeout(5_000),
    });
  } catch { /* ignore */ }
  clearAccessToken();
}

// ---------------------------------------------------------------------------
// Global fetch interceptor — auto-adds auth token to same-origin API calls
// ---------------------------------------------------------------------------

let _interceptorInstalled = false;

export function installFetchInterceptor(): void {
  if ((!IS_WEB && !IS_CAPACITOR) || _interceptorInstalled) return;
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

export async function checkAuth(apiBase = ""): Promise<boolean> {
  const maxAttempts = 3;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const token = getAccessToken();
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`${apiBase}/api/auth/check`, {
        headers,
        credentials: "include",
        signal: AbortSignal.timeout(5_000),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated === true) {
          if (data.method === "local") _localAuthMode = true;
          if (data.password_user_set === false) _passwordUserSet = false;
          return true;
        }
      }
      // Access token missing or expired — try silent refresh via httpOnly cookie
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
