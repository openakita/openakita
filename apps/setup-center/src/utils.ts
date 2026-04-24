// ─── Shared utility functions for Setup Center ───

import type { EnvMap } from "./types";

/**
 * Cross-platform confirm dialog.
 * Uses Tauri's native dialog plugin in desktop runtime (proper blocking on
 * both Windows WebView2 and macOS WKWebView), falls back to `window.confirm()`
 * in browser dev mode.
 */
export async function askConfirm(message: string): Promise<boolean> {
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    const { confirm } = await import("@tauri-apps/plugin-dialog");
    return confirm(message, { title: "OpenAkita", kind: "info" });
  }
  return window.confirm(message);
}

export function slugify(input: string) {
  return input
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9-_]/g, "")
    .slice(0, 32);
}

export function joinPath(a: string, b: string) {
  if (!a) return b;
  const sep = a.includes("\\") ? "\\" : "/";
  return a.replace(/[\\/]+$/, "") + sep + b.replace(/^[\\/]+/, "");
}

export function toFileUrl(p: string) {
  const t = p.trim();
  if (!t) return "";
  if (/^[a-zA-Z]:[\\/]/.test(t)) {
    const s = t.replace(/\\/g, "/");
    return `file:///${s}`;
  }
  if (t.startsWith("/")) {
    return `file://${t}`;
  }
  return `file://${t}`;
}

export function envKeyFromSlug(slug: string) {
  const up = slug.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
  return `${up}_API_KEY`;
}

export function nextEnvKeyName(base: string, used: Set<string>) {
  const b = base.trim();
  if (!b) return base;
  if (!used.has(b)) return b;
  for (let i = 2; i < 100; i++) {
    const k = `${b}_${i}`;
    if (!used.has(k)) return k;
  }
  return `${b}_${Date.now()}`;
}

export function suggestEndpointName(providerSlug: string, modelId: string) {
  const p = (providerSlug || "provider").trim() || "provider";
  const m = (modelId || "").trim();
  if (!m) return `${p}-primary`.slice(0, 64);
  const clean = m.replace(/[\\/]+/g, "-");
  return `${p}-${clean}`.slice(0, 64);
}

export function parseEnv(content: string): EnvMap {
  const out: EnvMap = {};
  for (const raw of content.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    const k = line.slice(0, idx).trim();
    const v = line.slice(idx + 1);
    out[k] = v;
  }
  return out;
}

export function envGet(env: EnvMap, key: string, fallback = "") {
  return env[key] ?? fallback;
}

export function envSet(env: EnvMap, key: string, value: string): EnvMap {
  return { ...env, [key]: value };
}

/** Generate a unique message/conversation ID */
export function genId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/** Format a timestamp for display */
export function formatTime(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Format a date for conversation list */
export function formatDate(ts: number): string {
  const d = new Date(ts);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return "今天";
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return "昨天";
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** Relative time like "2m", "19m", "3h", "1d" */
export function timeAgo(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "刚刚";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.floor(hr / 24);
  return `${day}d`;
}

