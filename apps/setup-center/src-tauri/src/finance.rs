//! Finance plugin desktop enhancements (M3 Infra Deliverable 3).
//!
//! Native overlays for the finance-auto plugin's `/admin/*` HTTP surface:
//! the React UI invokes these commands when running inside the Tauri
//! shell so the consent prompt, save-file picker, and toast use the
//! host OS's native widgets instead of WebView modals.  When the bundle
//! runs in pure-Web (browser preview) or Capacitor mode the commands
//! are simply unavailable and the front-end falls back to its existing
//! WebSocket / HTML modal path (v0.3 Part Infra §4.4 + §4.5).
//!
//! Commands exposed (wired into `invoke_handler!` from `main.rs`):
//!
//! * `show_finance_consent_dialog` — native `OkCancelCustom("允许一次",
//!   "拒绝")` consent dialog backed by `tauri_plugin_dialog`.
//! * `finance_system_info` — synchronous JSON object with the
//!   Tauri-specific fields (tauri version, os, arch, key store
//!   backend hint) that the Python-side `GET /admin/system-info`
//!   doesn't know.
//! * `finance_show_notification` — fire-and-forget OS toast through
//!   `tauri_plugin_notification`.
//! * `finance_pick_save_path` — native save-file dialog used by the
//!   "export backup" flow; returns the chosen absolute path or `None`
//!   when the user cancels.
//!
//! All four commands are intentionally thin: error strings flow back
//! to JS as `Result<_, String>` so the React layer can surface a
//! single toast instead of a TypeScript discriminated union.

use serde_json::json;
use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_notification::NotificationExt;

/// Constant string returned by [`show_finance_consent_dialog`] when the
/// user clicks the primary "允许一次" button.
pub const CONSENT_ALLOW_ONCE: &str = "allow_once";

/// Constant string returned by [`show_finance_consent_dialog`] when the
/// user clicks the secondary "拒绝" button (or dismisses the dialog).
pub const CONSENT_DENY: &str = "deny";

/// Show a native consent dialog with the M3 Infra-mandated button row.
///
/// Returns `"allow_once"` when the user clicks the primary button and
/// `"deny"` for the secondary button or any dismissal path.  The
/// frontend then posts the result to the Python-side
/// `POST /consent/{event_id}/respond` so the audit trail records both
/// the native and the in-app decision paths uniformly (v0.3 Part Infra
/// §4.4).
#[tauri::command]
pub async fn show_finance_consent_dialog(
    app: AppHandle,
    title: String,
    body: String,
) -> Result<String, String> {
    let confirmed = app
        .dialog()
        .message(body)
        .title(title)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "允许一次".to_string(),
            "拒绝".to_string(),
        ))
        .blocking_show();
    if confirmed {
        Ok(CONSENT_ALLOW_ONCE.to_string())
    } else {
        Ok(CONSENT_DENY.to_string())
    }
}

/// Return a JSON object describing the Tauri-specific runtime that
/// the Python `GET /admin/system-info` endpoint cannot observe.
///
/// The React side merges this with the Python payload so the desktop
/// "system info" panel can render a single combined view.
#[tauri::command]
pub fn finance_system_info() -> serde_json::Value {
    let openakita_version = option_env!("CARGO_PKG_VERSION").unwrap_or("0.0.0");
    json!({
        "tauri_version": tauri::VERSION,
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "openakita_version": openakita_version,
        "key_store_backend": "OS keyring",
    })
}

/// Push a native OS toast via `tauri-plugin-notification`.
///
/// The plugin defaults are sufficient for the "备份完成" / "密钥已轮换"
/// notifications we want to fire from the React layer; if the OS denies
/// the permission grant the error string is bubbled back so the
/// frontend can fall back to a webview toast.
#[tauri::command]
pub async fn finance_show_notification(
    app: AppHandle,
    title: String,
    body: String,
) -> Result<(), String> {
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|err| format!("notification failed: {err}"))
}

/// Show a native save-file dialog seeded with `default_name`.
///
/// Used by the "导出备份" flow in the React `KeyManagementView`: the
/// React layer hands the chosen path to
/// `POST /admin/backups`'s `dest_dir` field so the encrypted archive
/// lands where the operator expects.
///
/// Returns `Ok(Some(path))` when the user picks a file and `Ok(None)`
/// when they cancel.  Filesystem errors collapse into `Err(String)`.
#[tauri::command]
pub async fn finance_pick_save_path(
    app: AppHandle,
    default_name: String,
) -> Result<Option<String>, String> {
    let picked = app
        .dialog()
        .file()
        .set_file_name(default_name)
        .blocking_save_file();
    Ok(picked.map(|p| p.to_string()))
}
