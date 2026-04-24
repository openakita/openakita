// ─── Frontend Logger ───
// Unified logging for Tauri desktop, Web browser, and Capacitor mobile.
// Provides leveled logging, in-memory ring buffer, batched persistence,
// and cross-platform log export.

import { IS_TAURI, IS_CAPACITOR } from "./detect";
import { copyToClipboard } from "../utils/clipboard";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type LogLevel = "debug" | "info" | "warn" | "error";

interface LogEntry {
  ts: number;
  level: LogLevel;
  tag: string;
  message: string;
  extra?: Record<string, unknown>;
}

interface ExportResult {
  ok: boolean;
  /** "downloaded" | "shared" | "clipboard" | "saved" */
  method?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

const BUFFER_SIZE = IS_TAURI ? 500 : 1000;
const FLUSH_INTERVAL_MS = 5_000;
const FLUSH_BATCH_SIZE = 10;
const MIN_LEVEL: LogLevel = import.meta.env.DEV ? "debug" : "info";

// ---------------------------------------------------------------------------
// Ring buffer
// ---------------------------------------------------------------------------

const _buffer: LogEntry[] = [];
let _pendingLines: string[] = [];
let _flushTimer: ReturnType<typeof setTimeout> | null = null;
let _flushing = false;

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function formatTimestamp(ts: number): string {
  const d = new Date(ts);
  const hh = pad2(d.getHours());
  const mm = pad2(d.getMinutes());
  const ss = pad2(d.getSeconds());
  const ms = d.getMilliseconds().toString().padStart(3, "0");
  return `${hh}:${mm}:${ss}.${ms}`;
}

function formatLine(entry: LogEntry): string {
  const ts = formatTimestamp(entry.ts);
  const lvl = entry.level.toUpperCase().padEnd(5);
  const extra =
    entry.extra && Object.keys(entry.extra).length > 0
      ? " " + JSON.stringify(entry.extra)
      : "";
  return `[${ts}] [${lvl}] [${entry.tag}] ${entry.message}${extra}`;
}

function formatDateForExport(ts: number): string {
  return new Date(ts).toISOString();
}

// ---------------------------------------------------------------------------
// Persistence backends
// ---------------------------------------------------------------------------

async function flushToTauri(lines: string[]): Promise<void> {
  try {
    const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
    await tauriInvoke("append_frontend_log", { lines });
  } catch {
    // Tauri not available or command failed — silent
  }
}

function _getApiBase(): string {
  if (!IS_CAPACITOR) return "";
  try {
    const raw = localStorage.getItem("openakita_servers");
    const activeId = localStorage.getItem("openakita_active_server");
    if (raw && activeId) {
      const list = JSON.parse(raw) as { id: string; url: string }[];
      const s = list.find((x) => x.id === activeId);
      if (s) return s.url;
    }
  } catch { /* ignore */ }
  return "";
}

async function flushToApi(lines: string[]): Promise<void> {
  try {
    const base = _getApiBase();
    if (IS_CAPACITOR && !base) return;
    const url = `${base}/api/logs/frontend`;

    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    // API unreachable — silent, logs stay in memory buffer
  }
}

async function flushLines(lines: string[]): Promise<void> {
  if (lines.length === 0) return;
  if (IS_TAURI) {
    await flushToTauri(lines);
  } else {
    await flushToApi(lines);
  }
}

function scheduleFlush(): void {
  if (_flushTimer) return;
  _flushTimer = setTimeout(() => {
    _flushTimer = null;
    doFlush();
  }, FLUSH_INTERVAL_MS);
}

async function doFlush(): Promise<void> {
  if (_flushing || _pendingLines.length === 0) return;
  _flushing = true;
  const batch = _pendingLines.splice(0);
  try {
    await flushLines(batch);
  } catch {
    // On failure, don't re-add — they're still in the ring buffer
  } finally {
    _flushing = false;
  }
}

// ---------------------------------------------------------------------------
// Core log function
// ---------------------------------------------------------------------------

function log(level: LogLevel, tag: string, message: string, extra?: Record<string, unknown>): void {
  if (LEVEL_PRIORITY[level] < LEVEL_PRIORITY[MIN_LEVEL]) return;

  const entry: LogEntry = { ts: Date.now(), level, tag, message, extra };

  // Ring buffer
  if (_buffer.length >= BUFFER_SIZE) _buffer.shift();
  _buffer.push(entry);

  // Console output
  const line = formatLine(entry);
  const consoleFn =
    level === "error"
      ? console.error
      : level === "warn"
        ? console.warn
        : level === "debug"
          ? console.debug
          : console.log;
  consoleFn(line);

  // Queue for persistence
  _pendingLines.push(line);

  if (level === "error" || _pendingLines.length >= FLUSH_BATCH_SIZE) {
    doFlush();
  } else {
    scheduleFlush();
  }
}

// ---------------------------------------------------------------------------
// Export logs
// ---------------------------------------------------------------------------

async function fetchCombinedLogs(
  apiBase: string,
): Promise<{ backend: string; frontend: string } | null> {
  try {
    const res = await fetch(`${apiBase}/api/logs/combined`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return {
      backend: data.backend?.content || "",
      frontend: data.frontend?.content || "",
    };
  } catch {
    return null;
  }
}

function buildExportText(
  backendLogs: string | null,
  frontendMemLogs: string,
  platform: string,
  version: string,
  serverUrl: string,
): string {
  const sections: string[] = [];
  sections.push("====== OpenAkita Log Export ======");
  sections.push(`Date: ${formatDateForExport(Date.now())}`);
  sections.push(`Platform: ${platform}`);
  sections.push(`App Version: ${version}`);
  if (serverUrl) sections.push(`Server: ${serverUrl}`);
  sections.push(`User Agent: ${navigator.userAgent}`);
  sections.push("");

  if (backendLogs !== null) {
    sections.push("====== Backend Logs ======");
    sections.push(backendLogs || "(empty)");
    sections.push("");
  } else {
    sections.push("====== Backend Logs ======");
    sections.push("(backend unreachable)");
    sections.push("");
  }

  sections.push(`====== Frontend Logs (${_buffer.length} entries) ======`);
  sections.push(frontendMemLogs);

  return sections.join("\n");
}

function getPlatformLabel(): string {
  if (IS_TAURI) return "tauri (desktop)";
  if (IS_CAPACITOR) {
    const ua = navigator.userAgent;
    if (/android/i.test(ua)) return "capacitor (Android)";
    if (/iphone|ipad|ipod/i.test(ua)) return "capacitor (iOS)";
    return "capacitor";
  }
  return "web";
}

function downloadBlob(text: string, filename: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function dateLabel(): string {
  const d = new Date();
  return `${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}`;
}

async function exportLogs(): Promise<ExportResult> {
  // Flush any pending logs first
  await doFlush();

  const frontendMemLogs = _buffer.map(formatLine).join("\n");
  const platform = getPlatformLabel();

  let version = "unknown";
  try {
    const { getAppVersion } = await import("./index");
    version = await getAppVersion();
  } catch { /* ignore */ }

  let serverUrl = "";
  if (IS_CAPACITOR) {
    try {
      const { getActiveServer } = await import("./servers");
      serverUrl = getActiveServer()?.url || "";
    } catch { /* ignore */ }
  }

  const apiBase = IS_CAPACITOR
    ? serverUrl
    : IS_TAURI
      ? "http://127.0.0.1:18900"
      : window.location.origin;

  const combined = await fetchCombinedLogs(apiBase);
  const text = buildExportText(
    combined ? combined.backend : null,
    frontendMemLogs,
    platform,
    version,
    serverUrl || apiBase,
  );

  const filename = `openakita-logs-${dateLabel()}.log`;

  // --- Tauri: save file + show in folder ---
  if (IS_TAURI) {
    try {
      const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
      const savedPath = await tauriInvoke<string>("save_log_export", {
        filename,
        content: text,
      });
      if (savedPath) {
        try {
          await tauriInvoke("show_item_in_folder", { path: savedPath });
        } catch { /* non-critical */ }
      }
      return { ok: true, method: "saved" };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }

  // --- Capacitor: Web Share API → clipboard fallback ---
  if (IS_CAPACITOR) {
    if (navigator.share) {
      try {
        const file = new File([text], filename, { type: "text/plain" });
        await navigator.share({ files: [file] });
        return { ok: true, method: "shared" };
      } catch (e: any) {
        if (e?.name === "AbortError") return { ok: false, error: "cancelled" };
        // Fall through to clipboard
      }
    }
    const ok = await copyToClipboard(text);
    if (ok) return { ok: true, method: "clipboard" };
    return { ok: false, error: "clipboard failed" };
  }

  // --- Web: blob download ---
  try {
    downloadBlob(text, filename);
    return { ok: true, method: "downloaded" };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---------------------------------------------------------------------------
// Lifecycle: flush on page unload
// ---------------------------------------------------------------------------

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", () => {
    if (_pendingLines.length === 0) return;
    const lines = _pendingLines.splice(0);
    if (IS_TAURI) {
      // Can't do async in beforeunload; best-effort via sendBeacon to local API
      // Tauri logs are already mostly flushed by the periodic timer
      return;
    }
    let base = "";
    if (IS_CAPACITOR) {
      try {
        const raw = localStorage.getItem("openakita_servers");
        const activeId = localStorage.getItem("openakita_active_server");
        if (raw && activeId) {
          const list = JSON.parse(raw) as { id: string; url: string }[];
          const s = list.find((x) => x.id === activeId);
          if (s) base = s.url;
        }
      } catch { /* ignore */ }
    }
    navigator.sendBeacon?.(
      `${base}/api/logs/frontend`,
      new Blob([JSON.stringify({ lines })], { type: "application/json" }),
    );
  });
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const logger = {
  debug: (tag: string, message: string, extra?: Record<string, unknown>) =>
    log("debug", tag, message, extra),
  info: (tag: string, message: string, extra?: Record<string, unknown>) =>
    log("info", tag, message, extra),
  warn: (tag: string, message: string, extra?: Record<string, unknown>) =>
    log("warn", tag, message, extra),
  error: (tag: string, message: string, extra?: Record<string, unknown>) =>
    log("error", tag, message, extra),

  getBufferedLogs: (): readonly LogEntry[] => _buffer,
  getBufferedText: (): string => _buffer.map(formatLine).join("\n"),
  flush: doFlush,
  exportLogs,
};

