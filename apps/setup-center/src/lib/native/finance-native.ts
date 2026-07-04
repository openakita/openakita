/**
 * finance-auto native command wrappers (M3 Infra Deliverable 3).
 *
 * Background — why this module exists
 * -----------------------------------
 * `apps/setup-center/src-tauri/src/finance.rs` registers four
 * `#[tauri::command]`s that the React UI is supposed to invoke when
 * running inside the Tauri shell so the consent prompt, save-file
 * picker and OS toast use native widgets instead of WebView modals
 * (v0.3 Part Infra §4.4 + §4.5).
 *
 * The M3 Infra completion report claimed the Rust side was wired into
 * `invoke_handler!`, but the audit report (§3.5 / §6 P1-A) confirmed
 * that no frontend code path actually called `invoke()` for any of
 * the four commands. This wrapper closes that gap by:
 *
 *   1. Providing a single, typed entry point per command so callers
 *      don't sprinkle raw `invoke("finance_*", …)` calls.
 *   2. Centralising the web-fallback contract — every helper returns a
 *      discriminated result (`{ kind: "ok" | "unsupported" | "error" }`)
 *      so the caller can keep a clean fallback path without try/catch
 *      ladders.
 *   3. Re-exporting a single `isFinanceNativeSupported()` probe used by
 *      the plugin-bridge host to decide whether to advertise the
 *      "finance-native" capability to the iframe.
 *
 * Browser preview (web) mode is fully supported: every call short-
 * circuits with `{ kind: "unsupported" }` so the plugin UI can keep
 * working with `<a download>` / HTML modal fallbacks — exactly what
 * the existing flow did before this module landed.
 */

import { IS_TAURI, invoke } from "../../platform";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Outcome envelope used by every wrapper. */
export type NativeResult<T> =
  | { kind: "ok"; value: T }
  | { kind: "unsupported"; reason: string }
  | { kind: "error"; error: string };

/** Decision string returned by the native consent dialog. */
export type ConsentDecision = "allow_once" | "deny";

export interface FinanceSystemInfo {
  tauri_version?: string;
  os?: string;
  arch?: string;
  openakita_version?: string;
  key_store_backend?: string;
  [key: string]: unknown;
}

const UNSUPPORTED: NativeResult<never> = {
  kind: "unsupported",
  reason: "tauri-not-available",
};

// ---------------------------------------------------------------------------
// Capability probe
// ---------------------------------------------------------------------------

/**
 * Whether the host can actually invoke the four finance native commands.
 *
 * Returns `false` in web preview (`npm run dev:web`) and in the bundled
 * `dist-web/` flow served from Python; returns `true` only inside the
 * Tauri shell where `main.rs` registered the commands.
 */
export function isFinanceNativeSupported(): boolean {
  return IS_TAURI;
}

// ---------------------------------------------------------------------------
// Wrappers
// ---------------------------------------------------------------------------

/**
 * Show the M3 Infra consent dialog with the mandated 允许一次 / 拒绝 buttons.
 *
 * Returns `"allow_once"` when the user accepts and `"deny"` otherwise
 * (including dismissal). The plugin then POSTs the decision to
 * `/api/plugins/finance-auto/ai/consent/respond` exactly as the
 * existing WebView flow does — so the audit trail records the native
 * and in-app paths uniformly.
 */
export async function showFinanceConsentDialog(opts: {
  title: string;
  body: string;
}): Promise<NativeResult<ConsentDecision>> {
  if (!isFinanceNativeSupported()) return UNSUPPORTED;
  try {
    const value = await invoke<ConsentDecision>("show_finance_consent_dialog", {
      title: opts.title,
      body: opts.body,
    });
    return { kind: "ok", value };
  } catch (err) {
    return { kind: "error", error: String(err) };
  }
}

/**
 * Fetch the Tauri-only runtime descriptor (version / os / arch /
 * key-store hint) that the Python-side `GET /admin/system-info`
 * endpoint cannot observe. Callers usually merge this onto the
 * Python payload before rendering a unified "System info" card.
 */
export async function getFinanceSystemInfo(): Promise<NativeResult<FinanceSystemInfo>> {
  if (!isFinanceNativeSupported()) return UNSUPPORTED;
  try {
    const value = await invoke<FinanceSystemInfo>("finance_system_info");
    return { kind: "ok", value };
  } catch (err) {
    return { kind: "error", error: String(err) };
  }
}

/**
 * Fire-and-forget OS toast via `tauri-plugin-notification`.
 *
 * Used after long-running admin actions (backup created, key
 * rotated) so the operator sees feedback even if the OpenAkita
 * window isn't focused.
 */
export async function showFinanceNotification(opts: {
  title: string;
  body: string;
}): Promise<NativeResult<void>> {
  if (!isFinanceNativeSupported()) return UNSUPPORTED;
  try {
    await invoke<void>("finance_show_notification", {
      title: opts.title,
      body: opts.body,
    });
    return { kind: "ok", value: undefined };
  } catch (err) {
    return { kind: "error", error: String(err) };
  }
}

/**
 * Show the native save-file dialog (`tauri-plugin-dialog`) seeded
 * with `defaultName`. Resolves to the absolute path the user picked
 * or `null` when they cancelled.
 *
 * The plugin hands the returned path to `POST /admin/backups`'s
 * `dest_dir` field so the encrypted archive lands where the operator
 * expects.
 */
export async function pickFinanceSavePath(opts: {
  defaultName: string;
}): Promise<NativeResult<string | null>> {
  if (!isFinanceNativeSupported()) return UNSUPPORTED;
  try {
    const value = await invoke<string | null>("finance_pick_save_path", {
      defaultName: opts.defaultName,
    });
    return { kind: "ok", value: value ?? null };
  } catch (err) {
    return { kind: "error", error: String(err) };
  }
}

// ---------------------------------------------------------------------------
// Bridge dispatch (used by plugin-bridge-host)
// ---------------------------------------------------------------------------

/** Allow-list of commands plugin iframes are permitted to invoke. */
export const FINANCE_NATIVE_COMMANDS = [
  "show_finance_consent_dialog",
  "finance_system_info",
  "finance_show_notification",
  "finance_pick_save_path",
] as const;

export type FinanceNativeCommand = (typeof FINANCE_NATIVE_COMMANDS)[number];

/**
 * Dispatcher used by the plugin-bridge to translate a request from an
 * iframe (`bridge:finance-native-invoke`) into the matching wrapper
 * above. The bridge passes through the raw command name + args so a
 * single message handler can serve all four commands, while the
 * allow-list above prevents arbitrary command exposure.
 */
export async function dispatchFinanceNative(
  command: string,
  args: Record<string, unknown> | undefined,
): Promise<NativeResult<unknown>> {
  if (!isFinanceNativeSupported()) return UNSUPPORTED;
  if (!FINANCE_NATIVE_COMMANDS.includes(command as FinanceNativeCommand)) {
    return { kind: "error", error: `command not in allow-list: ${command}` };
  }
  const a = args ?? {};
  switch (command as FinanceNativeCommand) {
    case "show_finance_consent_dialog":
      return showFinanceConsentDialog({
        title: String(a.title ?? ""),
        body: String(a.body ?? ""),
      });
    case "finance_system_info":
      return getFinanceSystemInfo();
    case "finance_show_notification":
      return showFinanceNotification({
        title: String(a.title ?? ""),
        body: String(a.body ?? ""),
      });
    case "finance_pick_save_path":
      return pickFinanceSavePath({
        defaultName: String(a.default_name ?? a.defaultName ?? "backup.bin"),
      });
  }
}
