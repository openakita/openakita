use dirs_next::home_dir;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;
use tauri::Emitter;
use tauri::Manager;
#[cfg(desktop)]
use tauri_plugin_autostart::MacosLauncher;
#[cfg(desktop)]
use tauri_plugin_autostart::ManagerExt as AutostartManagerExt;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PlatformInfo {
    os: String,
    arch: String,
    home_dir: String,
    openakita_root_dir: String,
}

fn default_openakita_root() -> String {
    let home = home_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
    home.join(".openakita").to_string_lossy().to_string()
}

#[tauri::command]
fn get_platform_info() -> PlatformInfo {
    let home = home_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
    PlatformInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        home_dir: home.to_string_lossy().to_string(),
        openakita_root_dir: default_openakita_root(),
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct WorkspaceSummary {
    id: String,
    name: String,
    path: String,
    is_current: bool,
}

#[derive(Debug, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct AppStateFile {
    current_workspace_id: Option<String>,
    workspaces: Vec<WorkspaceMeta>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct WorkspaceMeta {
    id: String,
    name: String,
}

fn openakita_root_dir() -> PathBuf {
    home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita")
}

fn run_dir() -> PathBuf {
    openakita_root_dir().join("run")
}

fn state_file_path() -> PathBuf {
    openakita_root_dir().join("state.json")
}

fn workspaces_dir() -> PathBuf {
    openakita_root_dir().join("workspaces")
}

fn workspace_dir(id: &str) -> PathBuf {
    workspaces_dir().join(id)
}

fn service_pid_file(workspace_id: &str) -> PathBuf {
    run_dir().join(format!("openakita-{}.pid", workspace_id))
}

fn is_pid_running(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    if cfg!(windows) {
        let out = Command::new("cmd")
            .args(["/C", &format!("tasklist /FI \"PID eq {}\"", pid)])
            .output();
        if let Ok(out) = out {
            let s = String::from_utf8_lossy(&out.stdout);
            return s.contains(&pid.to_string());
        }
        false
    } else {
        let status = Command::new("kill").args(["-0", &pid.to_string()]).status();
        status.map(|s| s.success()).unwrap_or(false)
    }
}

fn kill_pid(pid: u32) -> Result<(), String> {
    if pid == 0 {
        return Ok(());
    }
    if cfg!(windows) {
        let status = Command::new("cmd")
            .args(["/C", &format!("taskkill /PID {} /T /F", pid)])
            .status()
            .map_err(|e| format!("taskkill failed: {e}"))?;
        if !status.success() {
            return Err(format!("taskkill failed: {status}"));
        }
        Ok(())
    } else {
        let status = Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .status()
            .map_err(|e| format!("kill failed: {e}"))?;
        if !status.success() {
            return Err(format!("kill failed: {status}"));
        }
        Ok(())
    }
}

fn read_state_file() -> AppStateFile {
    let p = state_file_path();
    let Ok(content) = fs::read_to_string(&p) else {
        return AppStateFile::default();
    };
    serde_json::from_str(&content).unwrap_or_default()
}

fn write_state_file(state: &AppStateFile) -> Result<(), String> {
    let p = state_file_path();
    if let Some(parent) = p.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create_dir_all failed: {e}"))?;
    }
    let data = serde_json::to_string_pretty(state).map_err(|e| format!("serialize failed: {e}"))?;
    fs::write(&p, data).map_err(|e| format!("write state.json failed: {e}"))?;
    Ok(())
}

fn ensure_workspace_scaffold(dir: &Path) -> Result<(), String> {
    fs::create_dir_all(dir.join("data")).map_err(|e| format!("create data dir failed: {e}"))?;
    fs::create_dir_all(dir.join("identity")).map_err(|e| format!("create identity dir failed: {e}"))?;

    // 默认 .env：用仓库内的 .env.example 作为模板（纯文本，可随版本更新）
    let env_path = dir.join(".env");
    if !env_path.exists() {
        const ENV_EXAMPLE: &str = include_str!("../../../../.env.example");
        fs::write(&env_path, ENV_EXAMPLE).map_err(|e| format!("write .env failed: {e}"))?;
    }

    // minimal identity files (后续可由 GUI 编辑)
    let soul = dir.join("identity").join("SOUL.md");
    if !soul.exists() {
        fs::write(
            &soul,
            "# Agent Soul\n\n你是 OpenAkita，一个忠诚可靠的 AI 助手。\n- 永不放弃，持续尝试直到成功\n- 诚实可靠，不会隐瞒问题\n- 主动学习，不断自我改进\n",
        )
        .map_err(|e| format!("write identity/SOUL.md failed: {e}"))?;
    }

    // 默认 llm_endpoints.json：用仓库内的 data/llm_endpoints.json 作为初始模板
    let llm = dir.join("data").join("llm_endpoints.json");
    if !llm.exists() {
        const DEFAULT_LLM_ENDPOINTS: &str = include_str!("../../../../data/llm_endpoints.json");
        fs::write(&llm, DEFAULT_LLM_ENDPOINTS)
            .map_err(|e| format!("write data/llm_endpoints.json failed: {e}"))?;
    }

    Ok(())
}

#[tauri::command]
fn list_workspaces() -> Result<Vec<WorkspaceSummary>, String> {
    let root = openakita_root_dir();
    fs::create_dir_all(&root).map_err(|e| format!("create root failed: {e}"))?;
    fs::create_dir_all(workspaces_dir()).map_err(|e| format!("create workspaces dir failed: {e}"))?;

    let state = read_state_file();
    let current = state.current_workspace_id.clone();

    let mut out = vec![];
    for w in state.workspaces {
        let dir = workspace_dir(&w.id);
        ensure_workspace_scaffold(&dir)?;
        out.push(WorkspaceSummary {
            id: w.id.clone(),
            name: w.name.clone(),
            path: dir.to_string_lossy().to_string(),
            is_current: current.as_deref() == Some(&w.id),
        });
    }
    Ok(out)
}

#[tauri::command]
fn create_workspace(id: String, name: String, set_current: bool) -> Result<WorkspaceSummary, String> {
    if id.trim().is_empty() {
        return Err("workspace id is empty".into());
    }
    if name.trim().is_empty() {
        return Err("workspace name is empty".into());
    }

    fs::create_dir_all(workspaces_dir()).map_err(|e| format!("create workspaces dir failed: {e}"))?;

    let mut state = read_state_file();
    if state.workspaces.iter().any(|w| w.id == id) {
        return Err("workspace id already exists".into());
    }
    state.workspaces.push(WorkspaceMeta {
        id: id.clone(),
        name: name.clone(),
    });
    if set_current {
        state.current_workspace_id = Some(id.clone());
    } else if state.current_workspace_id.is_none() {
        state.current_workspace_id = Some(id.clone());
    }
    write_state_file(&state)?;

    let dir = workspace_dir(&id);
    ensure_workspace_scaffold(&dir)?;

    Ok(WorkspaceSummary {
        id: id.clone(),
        name,
        path: dir.to_string_lossy().to_string(),
        is_current: state.current_workspace_id.as_deref() == Some(&id),
    })
}

#[tauri::command]
fn set_current_workspace(id: String) -> Result<(), String> {
    let mut state = read_state_file();
    if !state.workspaces.iter().any(|w| w.id == id) {
        return Err("workspace id not found".into());
    }
    state.current_workspace_id = Some(id);
    write_state_file(&state)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--background"]),
        ))
        .setup(|app| {
            setup_tray(app)?;
            // 自启动/后台启动时：不弹出主窗口，只保留托盘/菜单栏常驻
            if std::env::args().any(|a| a == "--background") {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { api, .. } => {
                // 默认行为：关闭窗口 -> 隐藏到托盘/菜单栏常驻（用户从托盘 Quit 退出）
                api.prevent_close();
                let _ = window.hide();
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            get_platform_info,
            list_workspaces,
            create_workspace,
            set_current_workspace,
            get_current_workspace_id,
            workspace_read_file,
            workspace_write_file,
            workspace_update_env,
            detect_python,
            install_embedded_python,
            create_venv,
            pip_install,
            pip_uninstall,
            remove_openakita_runtime,
            autostart_is_enabled,
            autostart_set_enabled,
            openakita_service_status,
            openakita_service_start,
            openakita_service_stop,
            openakita_list_skills,
            openakita_list_providers,
            openakita_list_models
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ServiceStatus {
    running: bool,
    pid: Option<u32>,
    pid_file: String,
}

#[tauri::command]
fn openakita_service_status(workspace_id: String) -> Result<ServiceStatus, String> {
    let pid_file = service_pid_file(&workspace_id);
    let pid = fs::read_to_string(&pid_file)
        .ok()
        .and_then(|s| s.trim().parse::<u32>().ok());
    let running = pid.map(is_pid_running).unwrap_or(false);
    Ok(ServiceStatus {
        running,
        pid: if running { pid } else { None },
        pid_file: pid_file.to_string_lossy().to_string(),
    })
}

fn read_env_kv(path: &Path) -> Vec<(String, String)> {
    let Ok(content) = fs::read_to_string(path) else {
        return vec![];
    };
    let mut out = vec![];
    for line in content.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') || !t.contains('=') {
            continue;
        }
        let (k, v) = t.split_once('=').unwrap_or((t, ""));
        let key = k.trim();
        if key.is_empty() {
            continue;
        }
        out.push((key.to_string(), v.to_string()));
    }
    out
}

#[tauri::command]
fn openakita_service_start(venv_dir: String, workspace_id: String) -> Result<ServiceStatus, String> {
    fs::create_dir_all(run_dir()).map_err(|e| format!("create run dir failed: {e}"))?;
    let pid_file = service_pid_file(&workspace_id);
    if let Ok(s) = fs::read_to_string(&pid_file) {
        if let Ok(pid) = s.trim().parse::<u32>() {
            if is_pid_running(pid) {
                return Ok(ServiceStatus {
                    running: true,
                    pid: Some(pid),
                    pid_file: pid_file.to_string_lossy().to_string(),
                });
            }
        }
    }

    let ws_dir = workspace_dir(&workspace_id);
    ensure_workspace_scaffold(&ws_dir)?;
    let py = venv_python_path(&venv_dir);
    if !py.exists() {
        return Err(format!("venv python not found: {}", py.to_string_lossy()));
    }

    let log_dir = ws_dir.join("logs");
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let log_path = log_dir.join("openakita-serve.log");
    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| format!("open log failed: {e}"))?;

    let mut cmd = Command::new(&py);
    cmd.current_dir(&ws_dir);
    cmd.args(["-m", "openakita.main", "serve"]);

    // inherit current env, then overlay workspace .env
    for (k, v) in read_env_kv(&ws_dir.join(".env")) {
        cmd.env(k, v);
    }
    cmd.env("LLM_ENDPOINTS_CONFIG", ws_dir.join("data").join("llm_endpoints.json"));

    // detach + redirect io
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::from(log_file.try_clone().map_err(|e| format!("clone log failed: {e}"))?))
        .stderr(std::process::Stdio::from(log_file));

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x00000008u32 | 0x00000200u32); // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    }

    let child = cmd.spawn().map_err(|e| format!("spawn openakita serve failed: {e}"))?;
    let pid = child.id();
    fs::write(&pid_file, pid.to_string()).map_err(|e| format!("write pid file failed: {e}"))?;

    Ok(ServiceStatus {
        running: true,
        pid: Some(pid),
        pid_file: pid_file.to_string_lossy().to_string(),
    })
}

#[tauri::command]
fn openakita_service_stop(workspace_id: String) -> Result<ServiceStatus, String> {
    let pid_file = service_pid_file(&workspace_id);
    let pid = fs::read_to_string(&pid_file)
        .ok()
        .and_then(|s| s.trim().parse::<u32>().ok());
    if let Some(pid) = pid {
        let _ = kill_pid(pid);
    }
    let _ = fs::remove_file(&pid_file);
    Ok(ServiceStatus {
        running: false,
        pid: None,
        pid_file: pid_file.to_string_lossy().to_string(),
    })
}

#[tauri::command]
fn autostart_is_enabled(app: tauri::AppHandle) -> Result<bool, String> {
    #[cfg(desktop)]
    {
        let mgr = app.autolaunch();
        return mgr.is_enabled().map_err(|e| format!("autostart is_enabled failed: {e}"));
    }
    #[cfg(not(desktop))]
    {
        let _ = app;
        Ok(false)
    }
}

#[tauri::command]
fn autostart_set_enabled(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    #[cfg(desktop)]
    {
        let mgr = app.autolaunch();
        if enabled {
            mgr.enable().map_err(|e| format!("autostart enable failed: {e}"))?;
        } else {
            mgr.disable().map_err(|e| format!("autostart disable failed: {e}"))?;
        }
        return Ok(());
    }
    #[cfg(not(desktop))]
    {
        let _ = (app, enabled);
        Ok(())
    }
}

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let open_status = MenuItem::with_id(app, "open_status", "打开状态面板", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "隐藏窗口", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出（Quit）", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&open_status, &show, &hide, &quit])?;

    TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id.as_ref() {
            "quit" => {
                app.exit(0);
            }
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "hide" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
            "open_status" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
                let _ = app.emit("open_status", serde_json::json!({}));
            }
            _ => {}
        })
        .on_tray_icon_event(move |tray, event| match event {
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } => {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.unminimize();
                    let _ = w.set_focus();
                }
                let _ = app.emit("open_status", serde_json::json!({}));
            }
            TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } => {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.unminimize();
                    let _ = w.set_focus();
                }
                let _ = app.emit("open_status", serde_json::json!({}));
            }
            _ => {}
        })
        .build(app)?;

    Ok(())
}

#[tauri::command]
fn get_current_workspace_id() -> Result<Option<String>, String> {
    let state = read_state_file();
    Ok(state.current_workspace_id)
}

fn workspace_file_path(workspace_id: &str, relative: &str) -> Result<PathBuf, String> {
    let base = workspace_dir(workspace_id);
    let rel = Path::new(relative);
    if rel.is_absolute() {
        return Err("relative path must not be absolute".into());
    }
    // Prevent path traversal.
    if relative.contains("..") {
        return Err("relative path must not contain ..".into());
    }
    Ok(base.join(rel))
}

#[tauri::command]
fn workspace_read_file(workspace_id: String, relative_path: String) -> Result<String, String> {
    let path = workspace_file_path(&workspace_id, &relative_path)?;
    fs::read_to_string(&path).map_err(|e| format!("read failed: {e}"))
}

#[tauri::command]
fn workspace_write_file(
    workspace_id: String,
    relative_path: String,
    content: String,
) -> Result<(), String> {
    let path = workspace_file_path(&workspace_id, &relative_path)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create parent dir failed: {e}"))?;
    }
    fs::write(&path, content).map_err(|e| format!("write failed: {e}"))
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct EnvEntry {
    key: String,
    value: String,
}

fn update_env_content(existing: &str, entries: &[EnvEntry]) -> String {
    let mut map = std::collections::BTreeMap::new();
    for e in entries {
        if e.key.trim().is_empty() {
            continue;
        }
        map.insert(e.key.trim().to_string(), e.value.clone());
    }
    if map.is_empty() {
        return existing.to_string();
    }

    let mut out = Vec::new();
    let mut seen = std::collections::BTreeSet::new();

    for line in existing.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') || !trimmed.contains('=') {
            out.push(line.to_string());
            continue;
        }
        let (k, _v) = trimmed.split_once('=').unwrap_or((trimmed, ""));
        let key = k.trim();
        if let Some(new_val) = map.get(key) {
            out.push(format!("{key}={new_val}"));
            seen.insert(key.to_string());
        } else {
            out.push(line.to_string());
        }
    }

    // append missing keys
    for (k, v) in map {
        if !seen.contains(&k) {
            out.push(format!("{k}={v}"));
        }
    }

    // ensure trailing newline
    let mut s = out.join("\n");
    if !s.ends_with('\n') {
        s.push('\n');
    }
    s
}

#[tauri::command]
fn workspace_update_env(workspace_id: String, entries: Vec<EnvEntry>) -> Result<(), String> {
    let dir = workspace_dir(&workspace_id);
    ensure_workspace_scaffold(&dir)?;
    let env_path = dir.join(".env");
    let existing = fs::read_to_string(&env_path).unwrap_or_default();
    let updated = update_env_content(&existing, &entries);
    fs::write(&env_path, updated).map_err(|e| format!("write .env failed: {e}"))
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PythonCandidate {
    command: Vec<String>,
    version_text: String,
    is_usable: bool,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct EmbeddedPythonInstallResult {
    python_command: Vec<String>,
    python_path: String,
    install_dir: String,
    asset_name: String,
    tag: String,
}

fn run_capture(cmd: &[String]) -> Result<String, String> {
    if cmd.is_empty() {
        return Err("empty command".into());
    }
    let mut c = Command::new(&cmd[0]);
    if cmd.len() > 1 {
        c.args(&cmd[1..]);
    }
    let out = c.output().map_err(|e| format!("failed to run {:?}: {e}", cmd))?;
    let mut s = String::new();
    if !out.stdout.is_empty() {
        s.push_str(&String::from_utf8_lossy(&out.stdout));
    }
    if !out.stderr.is_empty() {
        s.push_str(&String::from_utf8_lossy(&out.stderr));
    }
    Ok(s.trim().to_string())
}

fn python_version_ok(version_text: &str) -> bool {
    // very small parser: "Python 3.11.9"
    let lower = version_text.to_lowercase();
    let Some(idx) = lower.find("python") else { return false; };
    let ver = version_text[idx..].split_whitespace().nth(1).unwrap_or("");
    let parts: Vec<_> = ver.split('.').collect();
    if parts.len() < 2 {
        return false;
    }
    let major: i32 = parts[0].parse().unwrap_or(0);
    let minor: i32 = parts[1].parse().unwrap_or(0);
    major == 3 && minor >= 11
}

#[tauri::command]
fn detect_python() -> Vec<PythonCandidate> {
    // 注意：这里先用“系统 Python”；后续再加 python-build-standalone 的自动下载模式。
    let candidates: Vec<Vec<String>> = if cfg!(windows) {
        vec![
            vec!["py".into(), "-3.11".into()],
            vec!["python".into()],
            vec!["python3".into()],
        ]
    } else {
        vec![vec!["python3".into()], vec!["python".into()]]
    };

    let mut out = vec![];
    for c in candidates {
        let mut cmd = c.clone();
        cmd.push("--version".into());
        let version_text = run_capture(&cmd).unwrap_or_else(|e| e);
        let is_usable = python_version_ok(&version_text);
        out.push(PythonCandidate {
            command: c,
            version_text,
            is_usable,
        });
    }
    out
}

#[derive(Debug, Deserialize)]
struct LatestReleaseInfo {
    tag: String,
}

#[derive(Debug, Deserialize)]
struct GhRelease {
    assets: Vec<GhAsset>,
}

#[derive(Debug, Deserialize, Clone)]
struct GhAsset {
    name: String,
    browser_download_url: String,
}

fn runtime_dir() -> PathBuf {
    openakita_root_dir().join("runtime")
}

fn embedded_python_root() -> PathBuf {
    runtime_dir().join("python")
}

fn target_triple_hint() -> Result<&'static str, String> {
    if cfg!(windows) {
        if cfg!(target_arch = "x86_64") {
            return Ok("x86_64-pc-windows-msvc");
        }
        if cfg!(target_arch = "aarch64") {
            return Ok("aarch64-pc-windows-msvc");
        }
        return Err("unsupported windows arch".into());
    }
    if cfg!(target_os = "macos") {
        if cfg!(target_arch = "aarch64") {
            return Ok("aarch64-apple-darwin");
        }
        if cfg!(target_arch = "x86_64") {
            return Ok("x86_64-apple-darwin");
        }
        return Err("unsupported macos arch".into());
    }
    // Linux
    if cfg!(target_arch = "x86_64") {
        Ok("x86_64-unknown-linux-gnu")
    } else if cfg!(target_arch = "aarch64") {
        Ok("aarch64-unknown-linux-gnu")
    } else {
        Err("unsupported linux arch".into())
    }
}

fn pick_python_build_asset(
    assets: &[GhAsset],
    python_series: &str,
    triple: &str,
) -> Option<GhAsset> {
    let mut cands: Vec<&GhAsset> = assets
        .iter()
        .filter(|a| a.name.starts_with(&format!("cpython-{python_series}.")))
        .filter(|a| a.name.contains(triple))
        .filter(|a| a.name.contains("install_only"))
        .filter(|a| a.name.ends_with(".zip") || a.name.ends_with(".tar.gz"))
        .collect();

    // prefer stripped
    cands.sort_by_key(|a| {
        let stripped = a.name.contains("install_only_stripped");
        let ext_score = if cfg!(windows) {
            if a.name.ends_with(".zip") { 0 } else { 1 }
        } else {
            if a.name.ends_with(".tar.gz") { 0 } else { 1 }
        };
        (if stripped { 0 } else { 1 }, ext_score, a.name.clone())
    });

    cands.first().cloned().cloned()
}

fn safe_extract_path(base: &Path, entry_path: &Path) -> Option<PathBuf> {
    if entry_path.is_absolute() {
        return None;
    }
    let s = entry_path.to_string_lossy();
    if s.contains("..") {
        return None;
    }
    Some(base.join(entry_path))
}

fn extract_zip(zip_path: &Path, out_dir: &Path) -> Result<(), String> {
    let f = std::fs::File::open(zip_path).map_err(|e| format!("open zip failed: {e}"))?;
    let mut zip = zip::ZipArchive::new(f).map_err(|e| format!("read zip failed: {e}"))?;
    for i in 0..zip.len() {
        let mut file = zip.by_index(i).map_err(|e| format!("zip entry failed: {e}"))?;
        let Some(name) = file.enclosed_name().map(|p| p.to_owned()) else { continue };
        let Some(out_path) = safe_extract_path(out_dir, &name) else { continue };
        if file.is_dir() {
            fs::create_dir_all(&out_path).map_err(|e| format!("mkdir failed: {e}"))?;
        } else {
            if let Some(parent) = out_path.parent() {
                fs::create_dir_all(parent).map_err(|e| format!("mkdir failed: {e}"))?;
            }
            let mut out = std::fs::File::create(&out_path).map_err(|e| format!("create file failed: {e}"))?;
            std::io::copy(&mut file, &mut out).map_err(|e| format!("extract zip failed: {e}"))?;
        }
    }
    Ok(())
}

fn extract_tar_gz(tar_gz_path: &Path, out_dir: &Path) -> Result<(), String> {
    let f = std::fs::File::open(tar_gz_path).map_err(|e| format!("open tar.gz failed: {e}"))?;
    let gz = flate2::read::GzDecoder::new(f);
    let mut ar = tar::Archive::new(gz);
    for entry in ar.entries().map_err(|e| format!("tar entries failed: {e}"))? {
        let mut entry = entry.map_err(|e| format!("tar entry failed: {e}"))?;
        let path = entry.path().map_err(|e| format!("tar path failed: {e}"))?.to_path_buf();
        let Some(out_path) = safe_extract_path(out_dir, &path) else { continue };
        if let Some(parent) = out_path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir failed: {e}"))?;
        }
        entry.unpack(&out_path).map_err(|e| format!("tar unpack failed: {e}"))?;
    }
    Ok(())
}

fn find_python_executable(root: &Path) -> Option<PathBuf> {
    let mut queue = vec![root.to_path_buf()];
    let mut depth = 0usize;
    while !queue.is_empty() && depth < 6 {
        let mut next = vec![];
        for dir in queue {
            let Ok(rd) = fs::read_dir(&dir) else { continue };
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    next.push(p);
                } else {
                    let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
                    if cfg!(windows) {
                        if name.eq_ignore_ascii_case("python.exe") {
                            return Some(p);
                        }
                    } else if name == "python3" || name == "python" {
                        return Some(p);
                    }
                }
            }
        }
        queue = next;
        depth += 1;
    }
    None
}

#[tauri::command]
fn install_embedded_python(python_series: Option<String>) -> Result<EmbeddedPythonInstallResult, String> {
    let python_series = python_series.unwrap_or_else(|| "3.11".to_string());
    let triple = target_triple_hint()?;

    let client = reqwest::blocking::Client::builder()
        .user_agent("openakita-setup-center")
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|e| format!("http client build failed: {e}"))?;

    let latest: LatestReleaseInfo = client
        .get("https://raw.githubusercontent.com/astral-sh/python-build-standalone/latest-release/latest-release.json")
        .send()
        .map_err(|e| format!("fetch latest-release.json failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("fetch latest-release.json failed: {e}"))?
        .json()
        .map_err(|e| format!("parse latest-release.json failed: {e}"))?;

    let gh: GhRelease = client
        .get(format!(
            "https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{}",
            latest.tag
        ))
        .send()
        .map_err(|e| format!("fetch github release failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("fetch github release failed: {e}"))?
        .json()
        .map_err(|e| format!("parse github release failed: {e}"))?;

    let asset = pick_python_build_asset(&gh.assets, &python_series, triple)
        .ok_or_else(|| "no matching python-build-standalone asset found".to_string())?;

    let install_dir = embedded_python_root().join(&latest.tag).join(&asset.name);
    if install_dir.exists() {
        if let Some(py) = find_python_executable(&install_dir) {
            return Ok(EmbeddedPythonInstallResult {
                python_command: vec![py.to_string_lossy().to_string()],
                python_path: py.to_string_lossy().to_string(),
                install_dir: install_dir.to_string_lossy().to_string(),
                asset_name: asset.name,
                tag: latest.tag,
            });
        }
    }

    fs::create_dir_all(&install_dir).map_err(|e| format!("create install dir failed: {e}"))?;
    let archive_path = runtime_dir().join("downloads").join(&latest.tag).join(&asset.name);
    if let Some(parent) = archive_path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create download dir failed: {e}"))?;
    }

    if !archive_path.exists() {
        let mut resp = client
            .get(&asset.browser_download_url)
            .send()
            .map_err(|e| format!("download failed: {e}"))?
            .error_for_status()
            .map_err(|e| format!("download failed: {e}"))?;
        let mut out = std::fs::File::create(&archive_path).map_err(|e| format!("create archive failed: {e}"))?;
        std::io::copy(&mut resp, &mut out).map_err(|e| format!("write archive failed: {e}"))?;
    }

    // extract
    if asset.name.ends_with(".zip") {
        extract_zip(&archive_path, &install_dir)?;
    } else if asset.name.ends_with(".tar.gz") {
        extract_tar_gz(&archive_path, &install_dir)?;
    } else {
        return Err("unsupported archive type".into());
    }

    let py = find_python_executable(&install_dir).ok_or_else(|| "python executable not found after extract".to_string())?;
    Ok(EmbeddedPythonInstallResult {
        python_command: vec![py.to_string_lossy().to_string()],
        python_path: py.to_string_lossy().to_string(),
        install_dir: install_dir.to_string_lossy().to_string(),
        asset_name: asset.name,
        tag: latest.tag,
    })
}

#[tauri::command]
fn create_venv(python_command: Vec<String>, venv_dir: String) -> Result<String, String> {
    let venv = PathBuf::from(venv_dir);
    if venv.exists() {
        return Ok(venv.to_string_lossy().to_string());
    }
    let cmd = python_command;
    if cmd.is_empty() {
        return Err("python command is empty".into());
    }
    let mut c = Command::new(&cmd[0]);
    if cmd.len() > 1 {
        c.args(&cmd[1..]);
    }
    c.args(["-m", "venv"])
        .arg(&venv)
        .status()
        .map_err(|e| format!("failed to create venv: {e}"))?
        .success()
        .then_some(())
        .ok_or_else(|| "venv creation failed".to_string())?;
    Ok(venv.to_string_lossy().to_string())
}

fn venv_python_path(venv_dir: &str) -> PathBuf {
    let v = PathBuf::from(venv_dir);
    if cfg!(windows) {
        v.join("Scripts").join("python.exe")
    } else {
        v.join("bin").join("python")
    }
}

#[tauri::command]
fn pip_install(
    venv_dir: String,
    package_spec: String,
    index_url: Option<String>,
) -> Result<String, String> {
    let py = venv_python_path(&venv_dir);
    if !py.exists() {
        return Err(format!("venv python not found: {}", py.to_string_lossy()));
    }

    // upgrade pip first (best-effort)
    let mut up = Command::new(&py);
    up.args(["-m", "pip", "install", "-U", "pip", "setuptools", "wheel"]);
    if let Some(url) = &index_url {
        up.args(["-i", url]);
    }
    let _ = up.status();

    let mut c = Command::new(&py);
    c.args(["-m", "pip", "install", "-U", &package_spec]);
    if let Some(url) = &index_url {
        c.args(["-i", url]);
    }
    let status = c
        .status()
        .map_err(|e| format!("pip install failed to start: {e}"))?;
    if !status.success() {
        return Err(format!("pip install failed: {status}"));
    }
    Ok("ok".into())
}

#[tauri::command]
fn pip_uninstall(venv_dir: String, package_name: String) -> Result<String, String> {
    let py = venv_python_path(&venv_dir);
    if !py.exists() {
        return Err(format!("venv python not found: {}", py.to_string_lossy()));
    }
    if package_name.trim().is_empty() {
        return Err("package_name is empty".into());
    }

    let mut c = Command::new(&py);
    c.args(["-m", "pip", "uninstall", "-y", package_name.trim()]);
    let status = c
        .status()
        .map_err(|e| format!("pip uninstall failed to start: {e}"))?;
    if !status.success() {
        return Err(format!("pip uninstall failed: {status}"));
    }
    Ok("ok".into())
}

#[tauri::command]
fn remove_openakita_runtime(remove_venv: bool, remove_embedded_python: bool) -> Result<String, String> {
    let root = openakita_root_dir();
    if remove_venv {
        let venv = root.join("venv");
        if venv.exists() {
            fs::remove_dir_all(&venv).map_err(|e| format!("remove venv failed: {e}"))?;
        }
    }
    if remove_embedded_python {
        let rt = runtime_dir();
        if rt.exists() {
            fs::remove_dir_all(&rt).map_err(|e| format!("remove runtime failed: {e}"))?;
        }
    }
    Ok("ok".into())
}

fn run_python_module_json(
    venv_dir: &str,
    module: &str,
    args: &[&str],
    extra_env: &[(&str, &str)],
) -> Result<String, String> {
    let py = venv_python_path(venv_dir);
    if !py.exists() {
        return Err(format!("venv python not found: {}", py.to_string_lossy()));
    }

    let mut c = Command::new(&py);
    c.arg("-m").arg(module);
    c.args(args);
    for (k, v) in extra_env {
        c.env(k, v);
    }
    let out = c.output().map_err(|e| format!("failed to run python: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        let stdout = String::from_utf8_lossy(&out.stdout).to_string();
        return Err(format!("python failed: {}\nstdout:\n{}\nstderr:\n{}", out.status, stdout, stderr));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

#[tauri::command]
fn openakita_list_providers(venv_dir: String) -> Result<String, String> {
    run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &["list-providers"], &[])
}

#[tauri::command]
fn openakita_list_skills(venv_dir: String, workspace_id: String) -> Result<String, String> {
    let wd = workspace_dir(&workspace_id);
    run_python_module_json(
        &venv_dir,
        "openakita.setup_center.bridge",
        &["list-skills", "--workspace-dir", wd.to_string_lossy().as_ref()],
        &[],
    )
}

#[tauri::command]
fn openakita_list_models(
    venv_dir: String,
    api_type: String,
    base_url: String,
    provider_slug: Option<String>,
    api_key: String,
) -> Result<String, String> {
    let mut args = vec!["list-models", "--api-type", api_type.as_str(), "--base-url", base_url.as_str()];
    if let Some(slug) = provider_slug.as_deref() {
        args.push("--provider-slug");
        args.push(slug);
    }

    run_python_module_json(
        &venv_dir,
        "openakita.setup_center.bridge",
        &args,
        &[("SETUPCENTER_API_KEY", api_key.as_str())],
    )
}

