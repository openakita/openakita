#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

mod migrations;

use base64::Engine as _;
use dirs_next::home_dir;
use once_cell::sync::Lazy;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Instant;
use tauri::Emitter;
use tauri::Manager;
#[cfg(desktop)]
use tauri_plugin_autostart::MacosLauncher;
#[cfg(desktop)]
use tauri_plugin_autostart::ManagerExt as AutostartManagerExt;

// ── 全局管理的子进程 handle（仅追踪由 Tauri 自身 spawn 的进程） ──
struct ManagedProcess {
    child: std::process::Child,
    workspace_id: String,
    pid: u32,
    started_at: u64,
}

static MANAGED_CHILD: Lazy<Mutex<Option<ManagedProcess>>> = Lazy::new(|| Mutex::new(None));

/// Rust 自动启动后端时置 true，启动完成（成功/失败）后置 false。
/// 前端可查询该标记以显示"正在自动启动服务"并禁用启动/重启按钮。
static AUTO_START_IN_PROGRESS: AtomicBool = AtomicBool::new(false);

/// AUTO_START_IN_PROGRESS 置 true 时记录的 wall-clock 毫秒。
/// 用于 ``is_backend_auto_starting`` 的超时兜底：超过 ``AUTO_START_TIMEOUT_MS``
/// 视为后台 spawn 线程已经死掉/卡死，强制返回 false 防止前端 toast 永久卡住。
static AUTO_START_STARTED_AT_MS: AtomicU64 = AtomicU64::new(0);
static DESKTOP_SESSION_TOKEN: Lazy<Mutex<Option<String>>> = Lazy::new(|| Mutex::new(None));
const AUTO_START_TIMEOUT_MS: u64 = 180_000;

/// 后端启动宽限期（秒）。Backend cold-start 在 dual-venv hack 下：
///   * Python 解释器 import 整个生态 ≈ 30s
///   * 加载 122 个 skills + 30 个 handler + 数百兆 Memory ≈ 60s
///   * IM channel 初始化 + uvicorn bind ≈ 10s
/// 实测从 spawn 到 HTTP /api/health 可访问需要 90~120 秒。
///
/// 启动宽限期内：
///   - Rust 心跳即使 fetch /api/health 失败也不视为"backend down"，不发
///     `backend:lost`、不触发 auto-spawn（避免在 startup 期间反复刷
///     "[heartbeat] backend down" 日志、误以为后端崩溃）。
///   - `is_backend_auto_starting` 仍然返回 true，让前端 UI 显示
///     "正在启动" 而非 "未启动"。
const BACKEND_BOOT_GRACE_SEC: u64 = 150;

/// 即便 PID 已不在跑，也允许在 spawn 后这段窗口内继续认为"在启动宽限"。
/// 用于覆盖 spawn → Python 闪退 → Rust 心跳自愈重 spawn 的过渡窗口，
/// 避免前端 UI 在这个 30 秒小窗口里闪一下"已停止"。
const BACKEND_BOOT_GRACE_PID_DEAD_SEC: u64 = 30;

/// `openakita_service_start` 的进程级互斥窗口（毫秒）。
/// 在 3 秒内对同一 workspace 的第二次调用将被直接拒绝，避免前端重试/竞态
/// 在短时间内连续 spawn 出多个后端进程（autostart.log 里 27s 内 5 次 spawn
/// 就是这个 bug 的现场表现）。
const SERVICE_START_DEDUPE_MS: u64 = 3_000;
static SERVICE_START_LAST_AT: Lazy<Mutex<HashMap<String, u64>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));
const OPENAKITA_ROOT_MARKER: &str = ".openakita-root";

fn now_ms() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// 进程级自愈相关：crash 重启 marker 文件路径。
/// 由 panic hook 在命中 tao#1180 特征时写入，setup 阶段读出并向前端 emit
/// `app-restarted-from-crash` 事件，前端据此恢复上次工作区/视图。
/// 同一窗口去重写：只保留最近一次现场，避免 marker 累积。
fn restart_marker_path() -> PathBuf {
    let base = home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita");
    let _ = fs::create_dir_all(&base);
    base.join("restart.marker")
}

fn frontend_session_marker_path() -> PathBuf {
    let base = home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita");
    let _ = fs::create_dir_all(&base);
    base.join("frontend-session.marker")
}

static STARTUP_RECOVERY_NOTICE: Lazy<Mutex<Option<serde_json::Value>>> =
    Lazy::new(|| Mutex::new(None));

fn set_startup_recovery_notice(payload: serde_json::Value) {
    if let Ok(mut guard) = STARTUP_RECOVERY_NOTICE.lock() {
        *guard = Some(payload);
    }
}

#[tauri::command]
fn take_startup_recovery_notice() -> Option<serde_json::Value> {
    STARTUP_RECOVERY_NOTICE.lock().ok().and_then(|mut guard| guard.take())
}

fn record_frontend_session_marker(app_version: &str) {
    let marker = serde_json::json!({
        "ts": now_epoch_secs(),
        "pid": std::process::id(),
        "app_version": app_version,
    });
    let _ = fs::write(
        frontend_session_marker_path(),
        serde_json::to_string_pretty(&marker).unwrap_or_else(|_| "{}".into()),
    );
}

fn detect_previous_frontend_crash() -> Option<serde_json::Value> {
    let marker_path = frontend_session_marker_path();
    let content = fs::read_to_string(&marker_path).ok()?;
    let previous: serde_json::Value = serde_json::from_str(&content).ok()?;
    let prev_pid = previous.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    if prev_pid == 0 || prev_pid == std::process::id() || is_pid_running(prev_pid) {
        return None;
    }
    Some(serde_json::json!({
        "reason": "native_frontend_crash",
        "previous": previous,
    }))
}

fn clear_frontend_session_marker() {
    let marker_path = frontend_session_marker_path();
    let should_remove = fs::read_to_string(&marker_path)
        .ok()
        .and_then(|content| serde_json::from_str::<serde_json::Value>(&content).ok())
        .and_then(|json| json.get("pid").and_then(|v| v.as_u64()).map(|pid| pid as u32))
        .map(|pid| pid == std::process::id())
        .unwrap_or(true);
    if should_remove {
        let _ = fs::remove_file(marker_path);
    }
}

/// 防止自愈进入无限重启循环：如果短时间内（30s）已经因 panic 自愈过一次，
/// 再次崩溃则不再 spawn，让用户感知到崩溃并人工介入。
const SELF_HEAL_COOLDOWN_MS: u64 = 30_000;

fn try_self_heal_relaunch(panic_msg: &str) {
    use std::time::{SystemTime, UNIX_EPOCH};

    // 写 marker（携带 ts/panic_brief/上次 workspace 等供前端恢复使用）
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let last_ws = read_state_file().current_workspace_id.unwrap_or_default();
    // 命令行恢复时间：若上一份 marker 距今 < 冷却窗，不再二次自愈，
    // 避免无限崩溃-重启循环把 CPU 烧穿。
    if let Ok(prev) = fs::read_to_string(restart_marker_path()) {
        if let Ok(prev_json) = serde_json::from_str::<serde_json::Value>(&prev) {
            if let Some(prev_ts) = prev_json.get("ts").and_then(|v| v.as_u64()) {
                if ts.saturating_sub(prev_ts) < SELF_HEAL_COOLDOWN_MS / 1000 {
                    log_to_file(&format!(
                        "[self-heal] skip relaunch: last self-heal {}s ago < cooldown",
                        ts.saturating_sub(prev_ts)
                    ));
                    return;
                }
            }
        }
    }
    let marker = serde_json::json!({
        "ts": ts,
        "panic_brief": panic_msg.chars().take(200).collect::<String>(),
        "last_workspace_id": last_ws,
        "reason": "tao_destroyed_panic",
    });
    let _ = fs::write(
        restart_marker_path(),
        serde_json::to_string_pretty(&marker).unwrap_or_else(|_| "{}".into()),
    );

    // spawn 自身进程；--auto-restarted 让新实例知晓自己是恢复实例。
    // single-instance 插件会保证只有一个活实例（旧进程即将崩溃）。
    if let Ok(exe) = std::env::current_exe() {
        let mut cmd = Command::new(&exe);
        cmd.arg("--auto-restarted");
        // 避免继承当前控制台句柄，参考 spawn_detached 模式
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt as _;
            const DETACHED_PROCESS: u32 = 0x00000008;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
            cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
        }
        match cmd.spawn() {
            Ok(_) => log_to_file(&format!(
                "[self-heal] relaunched {} after tao panic",
                exe.display()
            )),
            Err(e) => log_to_file(&format!("[self-heal] relaunch FAILED: {e}")),
        }
    }
}

static ROOT_CONFIG_LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));
static STATE_FILE_LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PlatformInfo {
    os: String,
    arch: String,
    home_dir: String,
    openakita_root_dir: String,
}

/// 计算"未配置 custom_root 时的"默认 OpenAkita 数据目录字符串。
///
/// 注意：日常显示请用 [`openakita_root_dir`] 取真实 root，否则会和后端
/// 实际写入位置不一致；此函数仅作为兜底/迁移场景的"默认值"语义保留。
#[allow(dead_code)]
fn default_openakita_root() -> String {
    let home = home_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
    home.join(".openakita").to_string_lossy().to_string()
}

#[tauri::command]
fn get_platform_info() -> PlatformInfo {
    let home = home_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
    // 用 openakita_root_dir() 而不是 default_openakita_root()，确保前端
    // 显示的 root（以及拼出的 runtime / venv / logs hint）与后端 Rust /
    // Python 真正使用的位置完全一致。否则在用户配置了 custom_root 或
    // 设置了 OPENAKITA_ROOT 环境变量时，面板会指向 ~/.openakita 而真实
    // runtime 落在另一个磁盘，让人误以为"runtime 没建出来"。
    PlatformInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        home_dir: home.to_string_lossy().to_string(),
        openakita_root_dir: openakita_root_dir().to_string_lossy().to_string(),
    }
}

#[tauri::command]
fn toggle_pet_window(app_handle: tauri::AppHandle, show: bool) -> Result<(), String> {
    if let Some(window) = app_handle.get_webview_window("pet_window") {
        if show {
            window.show().map_err(|e| e.to_string())?;
        } else {
            window.hide().map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
fn start_dragging(window: tauri::Window) -> Result<(), String> {
    window.start_dragging().map_err(|e| e.to_string())
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
    #[serde(default = "default_config_version")]
    config_version: u32,
    #[serde(default)]
    current_workspace_id: Option<String>,
    #[serde(default)]
    workspaces: Vec<WorkspaceMeta>,
    #[serde(default)]
    auto_start_backend: Option<bool>,
    #[serde(default)]
    last_installed_version: Option<String>,
    #[serde(default)]
    install_mode: Option<String>,
    #[serde(default)]
    auto_update: Option<bool>,
}

fn default_config_version() -> u32 {
    migrations::CURRENT_CONFIG_VERSION
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct WorkspaceMeta {
    id: String,
    name: String,
}

fn default_root_dir() -> PathBuf {
    home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita")
}

fn comparable_path(path: &Path) -> String {
    let mut text = path.to_string_lossy().replace('/', "\\");
    while text.len() > 1 && text.ends_with('\\') {
        text.pop();
    }
    if cfg!(windows) {
        text.to_ascii_lowercase()
    } else {
        text
    }
}

fn is_path_root(path: &Path) -> bool {
    path.parent().is_none() || path.file_name().is_none()
}

fn is_safe_openakita_data_root(path: &Path) -> bool {
    if !path.is_absolute() || is_path_root(path) {
        return false;
    }

    let target = comparable_path(path);
    if let Some(home) = home_dir() {
        if target == comparable_path(&home) {
            return false;
        }
    }

    for protected in [
        dirs_next::desktop_dir(),
        dirs_next::download_dir(),
        dirs_next::document_dir(),
        dirs_next::data_dir(),
        dirs_next::data_local_dir(),
    ]
    .into_iter()
    .flatten()
    {
        if target == comparable_path(&protected) {
            return false;
        }
    }

    true
}

fn ensure_safe_openakita_data_root(path: &Path) -> Result<(), String> {
    if is_safe_openakita_data_root(path) {
        Ok(())
    } else {
        Err("数据目录不能设置为磁盘根目录、用户主目录或系统常用目录。请使用专用目录，例如 D:\\OpenAkitaData\\.openakita".into())
    }
}

fn write_root_marker(root: &Path) -> Result<(), String> {
    fs::create_dir_all(root).map_err(|e| format!("无法创建数据目录: {e}"))?;
    fs::write(
        root.join(OPENAKITA_ROOT_MARKER),
        b"OpenAkita data root\nDo not delete this file unless you no longer use this directory for OpenAkita.\n",
    )
    .map_err(|e| format!("write root marker failed: {e}"))
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct RootConfig {
    #[serde(default)]
    custom_root: Option<String>,
}

fn root_config_path() -> PathBuf {
    default_root_dir().join("root_config.json")
}

fn read_root_config() -> RootConfig {
    let p = root_config_path();
    let Ok(content) = fs::read_to_string(&p) else {
        return RootConfig::default();
    };
    match serde_json::from_str(&content) {
        Ok(cfg) => cfg,
        Err(e) => {
            eprintln!(
                "warning: failed to parse {}: {e}, using defaults",
                p.display()
            );
            RootConfig::default()
        }
    }
}

fn write_root_config(config: &RootConfig) -> Result<(), String> {
    let default_dir = default_root_dir();
    fs::create_dir_all(&default_dir).map_err(|e| format!("create default root dir failed: {e}"))?;
    write_root_marker(&default_dir)?;

    let p = root_config_path();
    let data = serde_json::to_string_pretty(config)
        .map_err(|e| format!("serialize root config failed: {e}"))?;
    atomic_write_with_backup(&p, data.as_bytes())?;

    // 同步写入纯文本文件，供 NSIS 安装脚本简单读取（无需解析 JSON）
    // NSIS Unicode 模式的 FileRead 在无 BOM 时按 ANSI(系统代码页) 解读，
    // 含非 ASCII 字符（如中文路径）会乱码。写成 UTF-16LE + BOM 保证 NSIS 正确读取。
    let txt_path = default_dir.join("custom_root.txt");
    match &config.custom_root {
        Some(path) if !path.is_empty() => {
            let trimmed = path.trim();
            let mut bytes: Vec<u8> = Vec::with_capacity(2 + trimmed.len() * 2);
            bytes.extend_from_slice(&[0xFF, 0xFE]);
            for code_unit in trimmed.encode_utf16() {
                bytes.extend_from_slice(&code_unit.to_le_bytes());
            }
            fs::write(&txt_path, bytes)
                .map_err(|e| format!("write custom_root.txt failed: {e}"))?;
        }
        _ => {
            let _ = fs::remove_file(&txt_path);
        }
    }
    Ok(())
}

fn openakita_root_dir() -> PathBuf {
    if let Ok(val) = std::env::var("OPENAKITA_ROOT") {
        if !val.is_empty() {
            return PathBuf::from(val);
        }
    }
    let config = read_root_config();
    if let Some(ref custom) = config.custom_root {
        if !custom.is_empty() {
            let p = PathBuf::from(custom);
            if !is_safe_openakita_data_root(&p) {
                eprintln!(
                    "WARNING: custom root dir '{}' is unsafe, falling back to default",
                    custom
                );
                return default_root_dir();
            }
            // 如果自定义路径所在的父目录都不可访问（如磁盘断开），回退到默认路径
            if p.exists() || p.parent().map(|parent| parent.exists()).unwrap_or(false) {
                return p;
            }
            eprintln!(
                "WARNING: custom root dir '{}' is not accessible, falling back to default",
                custom
            );
        }
    }
    default_root_dir()
}

fn run_dir() -> PathBuf {
    openakita_root_dir().join("run")
}

/// 安装配置日志目录：~/.openakita/logs/
fn setup_logs_dir() -> PathBuf {
    openakita_root_dir().join("logs")
}

/// Append a diagnostic line to `~/.openakita/logs/autostart.log`.
fn log_to_file(msg: &str) {
    let log_dir = setup_logs_dir();
    let _ = fs::create_dir_all(&log_dir);
    let path = log_dir.join("autostart.log");
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let line = format!("[{}] {}\n", secs, msg);
    let _ = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .and_then(|mut f| std::io::Write::write_all(&mut f, line.as_bytes()));
}

fn desktop_session_token() -> String {
    let mut guard = DESKTOP_SESSION_TOKEN.lock().unwrap();
    if let Some(token) = guard.as_ref() {
        return token.clone();
    }
    let mut seed = [0u8; 32];
    if getrandom::fill(&mut seed).is_err() {
        let fallback = format!(
            "{}:{}:{:?}",
            now_epoch_secs(),
            std::process::id(),
            std::thread::current().id()
        );
        let token = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(fallback.as_bytes());
        *guard = Some(token.clone());
        return token;
    }
    let token = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(seed);
    *guard = Some(token.clone());
    token
}

#[tauri::command]
fn openakita_desktop_session_token() -> String {
    desktop_session_token()
}

fn tail_serve_log_to_autostart(log_path: &Path, max_bytes: usize) {
    let Ok(mut file) = fs::File::open(log_path) else {
        return;
    };
    let Ok(meta) = file.metadata() else {
        return;
    };
    let len = meta.len();
    let start = len.saturating_sub(max_bytes as u64);
    if file.seek(SeekFrom::Start(start)).is_err() {
        return;
    }
    let mut buf = Vec::new();
    if file.read_to_end(&mut buf).is_err() {
        return;
    }
    let text = String::from_utf8_lossy(&buf);
    log_to_file(&format!(
        "[serve_log_tail] path={} bytes={}\n{}",
        log_path.display(),
        buf.len(),
        text
    ));
}

/// 开始写入安装配置日志，创建带日期的日志文件。返回完整路径供前端展示。
#[tauri::command]
fn start_onboarding_log(date_label: String) -> Result<String, String> {
    let log_dir = setup_logs_dir();
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let safe_label = date_label
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect::<String>();
    let name = if safe_label.is_empty() {
        format!(
            "onboarding-{}.log",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs()
        )
    } else {
        format!("onboarding-{}.log", safe_label)
    };
    let path = log_dir.join(&name);
    let mut f = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&path)
        .map_err(|e| format!("open onboarding log failed: {e}"))?;
    let header = format!("OpenAkita 安装配置日志 开始于 {}\n", date_label);
    f.write_all(header.as_bytes())
        .map_err(|e| format!("write onboarding log header failed: {e}"))?;
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(path.to_string_lossy().to_string())
}

/// 追加一行到安装配置日志（每行建议带时间戳，由前端拼接）。
#[tauri::command]
fn append_onboarding_log(log_path: String, line: String) -> Result<(), String> {
    let path = PathBuf::from(&log_path);
    if !path.exists() {
        return Ok(());
    }
    let mut f = OpenOptions::new()
        .append(true)
        .open(&path)
        .map_err(|e| format!("append onboarding log failed: {e}"))?;
    writeln!(f, "{}", line).map_err(|e| format!("write line failed: {e}"))?;
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(())
}

/// 批量追加多行到安装配置日志（用于写入配置快照等）。
#[tauri::command]
fn append_onboarding_log_lines(log_path: String, lines: Vec<String>) -> Result<(), String> {
    let path = PathBuf::from(&log_path);
    if !path.exists() || lines.is_empty() {
        return Ok(());
    }
    let mut f = OpenOptions::new()
        .append(true)
        .open(&path)
        .map_err(|e| format!("append onboarding log failed: {e}"))?;
    for line in lines {
        writeln!(f, "{}", line).map_err(|e| format!("write line failed: {e}"))?;
    }
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(())
}

// ── 前端日志持久化 ──

const FRONTEND_LOG_MAX_BYTES: u64 = 5 * 1024 * 1024; // 5 MB
const FRONTEND_LOG_TRUNCATE_TO: u64 = 2 * 1024 * 1024; // 截断后保留最后 2 MB

fn frontend_log_path() -> PathBuf {
    setup_logs_dir().join("frontend.log")
}

/// 自动轮转：当文件超过 FRONTEND_LOG_MAX_BYTES 时，只保留尾部 FRONTEND_LOG_TRUNCATE_TO 字节。
fn maybe_rotate_frontend_log(path: &Path) {
    let meta = match fs::metadata(path) {
        Ok(m) => m,
        Err(_) => return,
    };
    if meta.len() <= FRONTEND_LOG_MAX_BYTES {
        return;
    }
    // Read tail
    let mut f = match fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return,
    };
    let start = meta.len().saturating_sub(FRONTEND_LOG_TRUNCATE_TO);
    if f.seek(SeekFrom::Start(start)).is_err() {
        return;
    }
    let mut tail = Vec::new();
    if f.read_to_end(&mut tail).is_err() {
        return;
    }
    drop(f);
    // Skip to next newline to avoid partial line
    let offset = tail
        .iter()
        .position(|&b| b == b'\n')
        .map(|i| i + 1)
        .unwrap_or(0);
    let _ = fs::write(path, &tail[offset..]);
}

/// 前端 JS 日志批量追加到 ~/.openakita/logs/frontend.log。
#[tauri::command]
fn append_frontend_log(lines: Vec<String>) -> Result<(), String> {
    if lines.is_empty() {
        return Ok(());
    }
    let log_dir = setup_logs_dir();
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let path = frontend_log_path();
    maybe_rotate_frontend_log(&path);
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("open frontend log failed: {e}"))?;
    for line in &lines {
        writeln!(f, "{}", line).map_err(|e| format!("write line failed: {e}"))?;
    }
    f.flush().map_err(|e| format!("flush failed: {e}"))?;
    Ok(())
}

/// 导出日志到用户下载目录，返回保存路径。
#[tauri::command]
fn save_log_export(filename: String, content: String) -> Result<String, String> {
    let downloads = dirs_next::download_dir()
        .or_else(dirs_next::desktop_dir)
        .unwrap_or_else(|| openakita_root_dir().join("logs"));
    fs::create_dir_all(&downloads).ok();
    let path = downloads.join(&filename);
    fs::write(&path, content.as_bytes()).map_err(|e| format!("save log export failed: {e}"))?;
    Ok(path.to_string_lossy().to_string())
}

fn modules_dir() -> PathBuf {
    openakita_root_dir().join("modules")
}

/// 获取内嵌 PyInstaller 打包后端的目录
fn bundled_backend_dir() -> PathBuf {
    bundled_resource_dir("openakita-server")
}

fn bootstrap_resource_dir() -> PathBuf {
    bundled_resource_dir("bootstrap")
}

fn bundled_resource_dir(resource_name: &str) -> PathBuf {
    let exe_path = std::env::current_exe().ok();
    let exe_dir = exe_path
        .as_ref()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    // macOS: exe 在 .app/Contents/MacOS/，Tauri 将 resources 放在
    // .app/Contents/Resources/ 下并保留原始目录结构。
    // tauri.conf.json 配置 "resources": ["resources/openakita-server/"]，
    // 因此实际路径是 .app/Contents/Resources/resources/openakita-server/
    #[cfg(target_os = "macos")]
    {
        if let Some(contents_dir) = exe_dir.parent() {
            let primary = contents_dir
                .join("Resources")
                .join("resources")
                .join(resource_name);
            if primary.exists() {
                return primary;
            }
            // 兼容可能的简化布局（无额外 resources/ 前缀）
            let fallback = contents_dir.join("Resources").join(resource_name);
            if fallback.exists() {
                return fallback;
            }
        }
    }

    // Windows / Linux: 主路径 — resources 位于 exe 同级目录
    let primary = exe_dir.join("resources").join(resource_name);
    if primary.exists() {
        return primary;
    }

    // Linux deb/AppImage: exe 可能在 /usr/bin/ (symlink) 而 resources 在 /usr/lib/<app>/
    // current_exe() 有时返回 symlink 自身而非目标，导致 exe_dir = /usr/bin/
    #[cfg(target_os = "linux")]
    {
        let mut candidates: Vec<PathBuf> = vec![];

        // Tauri 2.x deb 的二进制名称默认来自 Cargo.toml package.name（非 productName），
        // lib 目录与二进制名称一致: /usr/lib/<binary-name>/resources/...
        // 从 current_exe() 动态推导，避免硬编码过时名称。
        let exe_name = exe_path
            .as_ref()
            .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()));

        let static_names: &[&str] = &[
            "OpenAkitaDesktop",       // tauri.conf.json productName used by deb resource dir
            "OpenAkita Desktop",      // legacy productName with a space
            "openakita-setup-center", // Cargo.toml package name (Tauri 2.x default)
            "openakita-desktop",      // legacy / mainBinaryName override
            "open-akita-desktop",
        ];

        // deb 常见布局: /usr/lib/<app-name>/resources/openakita-server/
        if let Some(ref name) = exe_name {
            candidates.push(
                Path::new("/usr/lib")
                    .join(name)
                    .join("resources")
                    .join(resource_name),
            );
        }
        for app_name in static_names {
            candidates.push(
                Path::new("/usr/lib")
                    .join(app_name)
                    .join("resources")
                    .join(resource_name),
            );
        }

        // 若 exe 在 /usr/bin/，尝试同级 /usr/lib/<app>/
        if let Some(usr_dir) = exe_dir.parent() {
            if let Some(ref name) = exe_name {
                candidates.push(
                    usr_dir
                        .join("lib")
                        .join(name)
                        .join("resources")
                        .join(resource_name),
                );
            }
            for app_name in static_names {
                candidates.push(
                    usr_dir
                        .join("lib")
                        .join(app_name)
                        .join("resources")
                        .join(resource_name),
                );
            }
        }

        // AppImage: 解压后 exe 在 <mount>/usr/bin/，resources 可能在 <mount>/usr/lib/<app>/
        // 也可能在 <mount>/resources/ (Tauri AppImage 平坦布局)
        if let Some(mount_root) = exe_dir.parent().and_then(|p| p.parent()) {
            if let Some(ref name) = exe_name {
                candidates.push(
                    mount_root
                        .join("lib")
                        .join(name)
                        .join("resources")
                        .join(resource_name),
                );
            }
            for app_name in static_names {
                candidates.push(
                    mount_root
                        .join("lib")
                        .join(app_name)
                        .join("resources")
                        .join(resource_name),
                );
            }
            candidates.push(mount_root.join("resources").join(resource_name));
        }

        for c in &candidates {
            if c.exists() {
                eprintln!(
                    "[bundled_resource_dir] found {} at Linux fallback: {}",
                    resource_name,
                    c.display()
                );
                return c.clone();
            }
        }

        eprintln!(
            "[bundled_resource_dir] {} not found. exe_dir={}, exe_name={:?}, checked {} Linux fallback paths",
            resource_name,
            exe_dir.display(),
            exe_name,
            candidates.len()
        );
    }

    primary
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct RuntimePipIndex {
    id: String,
    url: String,
    #[serde(default)]
    trusted_host: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct RuntimeEnvState {
    path: String,
    status: String,
    #[serde(default)]
    created_at: String,
    #[serde(default)]
    last_verified_at: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct RuntimeManifest {
    schema_version: u32,
    app_version: String,
    wheel_hash: String,
    python_version: String,
    app_venv: RuntimeEnvState,
    agent_venv: RuntimeEnvState,
    pip_index: RuntimePipIndex,
    legacy_mode: bool,
    last_error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct BootstrapWheel {
    name: String,
    #[serde(default)]
    sha256: String,
}

#[derive(Clone, Debug, Deserialize)]
struct BootstrapManifest {
    #[serde(default = "default_python_version")]
    python_version: String,
    wheel: BootstrapWheel,
    #[serde(default)]
    default_pip_index: Option<RuntimePipIndex>,
    #[serde(default)]
    python_seed: Option<serde_json::Value>,
    #[serde(default)]
    node_seed: Option<serde_json::Value>,
}

#[derive(Clone, Debug)]
struct RuntimeEnvInfo {
    app_python: PathBuf,
    agent_python: PathBuf,
    app_venv: PathBuf,
    agent_venv: PathBuf,
    pip_index: RuntimePipIndex,
}

fn default_python_version() -> String {
    "3.12".to_string()
}

fn runtime_root_dir() -> PathBuf {
    openakita_root_dir().join("runtime")
}

fn runtime_manifest_path() -> PathBuf {
    runtime_root_dir().join("manifest.json")
}

fn app_venv_dir() -> PathBuf {
    runtime_root_dir().join("app-venv")
}

fn agent_venv_dir() -> PathBuf {
    runtime_root_dir().join("agent-venv")
}

fn runtime_logs_dir() -> PathBuf {
    runtime_root_dir().join("logs")
}

fn runtime_cache_dir() -> PathBuf {
    runtime_root_dir().join("cache")
}

fn runtime_venv_python_path(venv_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        venv_dir.join("Scripts").join("python.exe")
    } else {
        venv_dir.join("bin").join("python")
    }
}

fn runtime_venv_home_python_path(venv_dir: &Path) -> Option<PathBuf> {
    if !cfg!(windows) {
        return None;
    }
    let cfg_path = venv_dir.join("pyvenv.cfg");
    let content = fs::read_to_string(cfg_path).ok()?;
    for line in content.lines() {
        let Some(home) = line.strip_prefix("home = ") else {
            continue;
        };
        let py = PathBuf::from(home.trim()).join("python.exe");
        if py.exists() {
            return Some(py);
        }
    }
    None
}

fn runtime_venv_site_packages_dir(venv_dir: &Path) -> Option<PathBuf> {
    if cfg!(windows) {
        let sp = venv_dir.join("Lib").join("site-packages");
        return sp.exists().then_some(sp);
    }
    let lib_dir = venv_dir.join("lib");
    if let Ok(entries) = fs::read_dir(&lib_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with("python") {
                let sp = entry.path().join("site-packages");
                if sp.exists() {
                    return Some(sp);
                }
            }
        }
    }
    None
}

fn python_string_literal(value: &Path) -> String {
    format!("{:?}", value.to_string_lossy().to_string())
}

/// Render a `&[&str]` into a Python tuple literal, e.g. `("a", "b", "c")`.
/// Used by `app_runtime_health_code` to share Rust-side `BAD_*_MARKERS`
/// constants directly with the embedded Python health-check script, so the
/// two sides never drift apart.
fn python_tuple_literal(values: &[&str]) -> String {
    let body: Vec<String> = values.iter().map(|s| format!("{:?}", s)).collect();
    if body.len() == 1 {
        format!("({},)", body[0])
    } else {
        format!("({})", body.join(", "))
    }
}

fn runtime_venv_backend_args(venv_dir: &Path) -> Vec<String> {
    if cfg!(windows) && runtime_venv_home_python_path(venv_dir).is_some() {
        if let Some(site_packages) = runtime_venv_site_packages_dir(venv_dir) {
            let venv_python = runtime_venv_python_path(venv_dir);
            let code = format!(
                "import runpy, site, sys; sys.prefix = sys.exec_prefix = {}; sys.executable = {}; site.addsitedir({}); runpy.run_module('openakita.main', run_name='__main__')",
                python_string_literal(venv_dir),
                python_string_literal(&venv_python),
                python_string_literal(&site_packages)
            );
            return vec!["-u".into(), "-c".into(), code, "serve".into()];
        }
    }
    vec![
        "-u".into(),
        "-m".into(),
        "openakita.main".into(),
        "serve".into(),
    ]
}

fn runtime_venv_backend_python_path(venv_dir: &Path) -> PathBuf {
    // Do not use the python.exe/pythonw.exe launcher files created by uv on
    // Windows. They delegate to the managed CPython executable as a grandchild,
    // which escapes our CREATE_NO_WINDOW flag and leaves a visible console.
    if let Some(py) = runtime_venv_home_python_path(venv_dir) {
        return py;
    }
    runtime_venv_python_path(venv_dir)
}

fn runtime_venv_bin_dir(venv_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        venv_dir.join("Scripts")
    } else {
        venv_dir.join("bin")
    }
}

fn ensure_runtime_layout() -> Result<(), String> {
    let root = runtime_root_dir();
    for dir in [
        root.clone(),
        app_venv_dir(),
        agent_venv_dir(),
        runtime_logs_dir(),
        runtime_cache_dir().join("wheels"),
        runtime_cache_dir().join("uv"),
        runtime_cache_dir().join("python"),
    ] {
        if let Err(e) = fs::create_dir_all(&dir) {
            // 企业 AD 域 / Windows S 模式 / 杀软"勒索软件防护"会把
            // `%LOCALAPPDATA%\OpenAkitaDesktop\` 设为受保护目录，此时
            // `create_dir_all` 返回 PermissionDenied。Phase 2 加了 30MB seed
            // 后 IO 失败概率上升，必须给出可操作的指引而不是干瘪的报错。
            //
            // 结构化错误码 `RUNTIME_PERMISSION_DENIED|...`：前端识别这个前缀
            // 后渲染中英文指引 + "打开运行时目录"按钮。前后端契约见
            // `apps/setup-center/src/views/StatusView.tsx`。
            if e.kind() == std::io::ErrorKind::PermissionDenied {
                let detail = format!(
                    "RUNTIME_PERMISSION_DENIED|{} 创建被拒。可能是杀软/域策略限制。\
                     请将 {} 加入白名单后重试，或联系管理员。\
                     Permission denied creating {}; please allowlist {} or contact your admin.",
                    dir.display(),
                    runtime_root_dir().display(),
                    dir.display(),
                    runtime_root_dir().display()
                );
                write_runtime_failure_manifest(&detail);
                return Err(detail);
            }
            return Err(format!("create runtime dir {} failed: {e}", dir.display()));
        }
    }
    Ok(())
}

fn default_pip_index() -> RuntimePipIndex {
    RuntimePipIndex {
        id: "aliyun".into(),
        url: "https://mirrors.aliyun.com/pypi/simple/".into(),
        trusted_host: "mirrors.aliyun.com".into(),
    }
}

fn trusted_host_for_url(url: &str) -> String {
    url.split_once("://")
        .map(|(_, rest)| rest.split('/').next().unwrap_or("").to_string())
        .unwrap_or_default()
}

fn read_runtime_manifest() -> Option<RuntimeManifest> {
    let content = fs::read_to_string(runtime_manifest_path()).ok()?;
    serde_json::from_str::<RuntimeManifest>(&content).ok()
}

fn resolve_runtime_pip_index() -> RuntimePipIndex {
    if let Some(manifest) = read_runtime_manifest() {
        if !manifest.pip_index.url.trim().is_empty() {
            return manifest.pip_index;
        }
    }
    if let Ok(url) = std::env::var("OPENAKITA_PIP_INDEX_URL") {
        if !url.trim().is_empty() {
            let trusted_host = std::env::var("OPENAKITA_PIP_TRUSTED_HOST")
                .unwrap_or_else(|_| trusted_host_for_url(&url));
            return RuntimePipIndex {
                id: "env-openakita".into(),
                url,
                trusted_host,
            };
        }
    }
    if let Ok(url) = std::env::var("PIP_INDEX_URL") {
        if !url.trim().is_empty() {
            let trusted_host =
                std::env::var("PIP_TRUSTED_HOST").unwrap_or_else(|_| trusted_host_for_url(&url));
            return RuntimePipIndex {
                id: "env-pip".into(),
                url,
                trusted_host,
            };
        }
    }
    default_pip_index()
}

fn read_bootstrap_manifest() -> Result<BootstrapManifest, String> {
    let path = bootstrap_resource_dir().join("manifest.json");
    let content = fs::read_to_string(&path)
        .map_err(|e| format!("read bootstrap manifest {} failed: {e}", path.display()))?;
    serde_json::from_str(&content)
        .map_err(|e| format!("parse bootstrap manifest {} failed: {e}", path.display()))
}

fn bootstrap_uv_path() -> PathBuf {
    let bootstrap = bootstrap_resource_dir();
    let local = if cfg!(windows) {
        bootstrap.join("bin").join("uv.exe")
    } else {
        bootstrap.join("bin").join("uv")
    };
    if local.exists() {
        local
    } else {
        PathBuf::from("uv")
    }
}

fn managed_python_seed_path() -> Option<PathBuf> {
    let bootstrap = bootstrap_resource_dir();
    let base = bootstrap.join("python");
    if !base.exists() {
        return None;
    }
    let candidates = if cfg!(windows) {
        vec![base.join("python.exe"), base.join("bin").join("python.exe")]
    } else {
        vec![
            base.join("bin").join("python3"),
            base.join("bin").join("python"),
            base.join("python3"),
            base.join("python"),
        ]
    };
    candidates.into_iter().find(|p| p.exists())
}

fn managed_node_seed_path() -> Option<PathBuf> {
    let bootstrap = bootstrap_resource_dir();
    let base = bootstrap.join("node");
    if !base.exists() {
        return None;
    }
    let candidates = if cfg!(windows) {
        vec![base.join("node.exe"), base.join("bin").join("node.exe")]
    } else {
        vec![base.join("bin").join("node"), base.join("node")]
    };
    candidates.into_iter().find(|p| p.exists())
}

#[derive(Clone, Copy, Debug)]
enum RuntimeEnvPurpose {
    Bootstrap,
    Core,
    Agent,
    ExtensionInstall,
}

impl RuntimeEnvPurpose {
    fn as_str(self) -> &'static str {
        match self {
            Self::Bootstrap => "bootstrap",
            Self::Core => "core",
            Self::Agent => "agent",
            Self::ExtensionInstall => "extension-install",
        }
    }
}

/// Centralized runtime environment builder for OpenAkita-managed subprocesses.
///
/// Core/bootstrap subprocesses must not inherit user Python, Conda, pip, or SSL
/// state. Agent/tool subprocesses can keep ordinary user context, but still get
/// explicit OpenAkita runtime paths and Python isolation markers.
fn apply_runtime_env_builder(
    cmd: &mut Command,
    purpose: RuntimeEnvPurpose,
    pip_index: Option<&RuntimePipIndex>,
) {
    strip_harmful_python_env(cmd);
    strip_harmful_toolchain_env(cmd);

    // 过滤 PATH 里的 anaconda/pyenv/homebrew/mise/asdf 等已知 Python 污染源
    // 段。即便上面 strip_harmful_python_env 已经清掉 PYTHONHOME/PYTHONPATH，
    // uv 在 `--python <version>` 模式下仍会按 PATH 顺序 discover 一个匹配的
    // python.exe；若 PATH 头部是 `C:\Users\<u>\anaconda3\` 就 100% 命中。
    // 用 PATH 过滤作为兜底，与下面 apply_runtime_bootstrap_env 的
    // `UV_PYTHON_PREFERENCE=only-managed` 形成"配置 + 兜底"双保险。
    filter_path_for_runtime(cmd);

    cmd.env("OPENAKITA_RUNTIME_ROOT", runtime_root_dir());
    cmd.env("OPENAKITA_ENV_PURPOSE", purpose.as_str());
    cmd.env("OPENAKITA_ENV_TRUST_SOURCE", "host-runtime");
    cmd.env("PYTHONNOUSERSITE", "1");

    let effective_pip_index;
    let pip_index = match pip_index {
        Some(index) => index,
        None => {
            effective_pip_index = resolve_runtime_pip_index();
            &effective_pip_index
        }
    };
    cmd.env("PIP_INDEX_URL", &pip_index.url);
    cmd.env("UV_INDEX_URL", &pip_index.url);
    if !pip_index.trusted_host.trim().is_empty() {
        cmd.env("PIP_TRUSTED_HOST", &pip_index.trusted_host);
    }

    cmd.env(
        "OPENAKITA_APP_PYTHON",
        runtime_venv_python_path(&app_venv_dir()),
    );
    cmd.env(
        "OPENAKITA_AGENT_PYTHON",
        runtime_venv_python_path(&agent_venv_dir()),
    );
    cmd.env(
        "OPENAKITA_AGENT_BIN",
        runtime_venv_bin_dir(&agent_venv_dir()),
    );

    match purpose {
        RuntimeEnvPurpose::Bootstrap | RuntimeEnvPurpose::Core => {
            cmd.env("OPENAKITA_SUBPROCESS_SECRET_SCRUB", "1");
        }
        RuntimeEnvPurpose::Agent | RuntimeEnvPurpose::ExtensionInstall => {
            cmd.env("OPENAKITA_SUBPROCESS_SECRET_SCRUB", "1");
            prepend_path(cmd, &runtime_venv_bin_dir(&agent_venv_dir()));
        }
    }
}

fn apply_runtime_bootstrap_env(cmd: &mut Command, pip_index: Option<&RuntimePipIndex>) {
    apply_runtime_env_builder(cmd, RuntimeEnvPurpose::Bootstrap, pip_index);

    // 仅在 bootstrap 路径上钉死 uv 的 Python 发现策略。Core / Agent 子进程
    // 已经直接通过 venv 内 python 调用，不走 uv 解释器解析。
    //
    //   * UV_PYTHON_PREFERENCE=only-managed：禁止 uv 用宿主 anaconda / pyenv
    //     / brew python。即便 PATH 过滤兜底失效，uv 也不会去 PATH 里找。
    //   * UV_PYTHON_DOWNLOADS=automatic：seed 缺失时允许自动下载
    //     python-build-standalone（联网环境无感升级；断网会落到 fallback）。
    //   * UV_PYTHON_INSTALL_DIR：把下载的 managed Python 落在
    //     OpenAkita 自管目录而不是 `%LOCALAPPDATA%\uv\python`，便于卸载、
    //     便于"修复运行环境"按钮一刀清理。
    //   * UV_PYTHON_BIN_DIR：与 INSTALL_DIR 同根，避免 uv 把 shim 写到
    //     `~/.local/bin` 这种用户全局位置。
    cmd.env("UV_PYTHON_PREFERENCE", "only-managed");
    cmd.env("UV_PYTHON_DOWNLOADS", "automatic");
    let py_install = runtime_cache_dir().join("python");
    cmd.env("UV_PYTHON_INSTALL_DIR", &py_install);
    cmd.env("UV_PYTHON_BIN_DIR", &py_install);
    // 给 uv 的下载缓存也定向到 runtime/cache/uv/，与现有 cache layout 一致。
    cmd.env("UV_CACHE_DIR", runtime_cache_dir().join("uv"));
}

fn apply_runtime_core_env(cmd: &mut Command) {
    apply_runtime_env_builder(cmd, RuntimeEnvPurpose::Core, None);
    prepend_path(cmd, &runtime_venv_bin_dir(&agent_venv_dir()));

    // uv-managed Python can miss an OS trust store on Windows. Prefer the
    // certifi bundle installed into app-venv, and never inherit Conda's SSL vars.
    if let Some(sp) = runtime_venv_site_packages_dir(&app_venv_dir()) {
        let cacert = sp.join("certifi").join("cacert.pem");
        if cacert.exists() {
            cmd.env("SSL_CERT_FILE", &cacert);
            cmd.env("REQUESTS_CA_BUNDLE", &cacert);
            cmd.env("CURL_CA_BUNDLE", &cacert);
            if let Some(parent) = cacert.parent() {
                cmd.env("SSL_CERT_DIR", parent);
            }
        }
    }
}

fn run_and_log(mut cmd: Command, log_path: &Path) -> Result<(), String> {
    let output = cmd
        .output()
        .map_err(|e| format!("run command failed: {e}"))?;
    let mut log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
        .map_err(|e| format!("open runtime log {} failed: {e}", log_path.display()))?;
    let _ = writeln!(log, "\n$ {:?}", cmd);
    let _ = log.write_all(&output.stdout);
    let _ = log.write_all(&output.stderr);
    if output.status.success() {
        Ok(())
    } else {
        Err(format!("command failed with status {}", output.status))
    }
}

fn health_check_python(py: &Path, code: &str, log_path: &Path) -> bool {
    if !py.exists() {
        return false;
    }
    let mut cmd = Command::new(py);
    cmd.args(["-c", code]);
    apply_runtime_bootstrap_env(&mut cmd, None);
    apply_no_window(&mut cmd);
    match cmd.output() {
        Ok(output) if output.status.success() => true,
        Ok(output) => {
            let mut log = OpenOptions::new()
                .create(true)
                .append(true)
                .open(log_path)
                .ok();
            if let Some(ref mut log) = log {
                let _ = writeln!(log, "health check failed for {}", py.display());
                let _ = log.write_all(&output.stdout);
                let _ = log.write_all(&output.stderr);
            }
            false
        }
        Err(_) => false,
    }
}

/// Disallowed base-Python markers shared by:
///   * `venv_is_real_isolated` —— Rust 端读 `pyvenv.cfg::home`；
///   * `app_runtime_health_code` —— Python 端二次校验同一 marker。
///
/// 两侧共享同一份 marker 是关键：如果只在 Python 端拦，Rust 端
/// `venv_is_real_isolated` 会认为旧的 anaconda-base venv 还能用，跳过重建，
/// 每次启动都白白浪费一次 wheel install。
///
/// 拒绝列表覆盖：Anaconda/Miniconda/Mambaforge/Miniforge、WindowsApps stub、
/// pyenv、Homebrew Cellar、asdf、mise、rye。这些 base Python 之上创建的 venv
/// 一旦命中坏 pydantic / 错版本 OpenSSL，启动后会以 SystemExit(23) 失败。
const BAD_BASE_PYTHON_MARKERS: &[&str] = &[
    "anaconda",
    "miniconda",
    "conda",
    "mambaforge",
    "miniforge",
    "windowsapps",
    "appinstallerpythonredirector",
    ".pyenv",
    "homebrew",
    "/cellar/",
    "\\cellar\\",
    ".asdf",
    ".mise",
    ".rye/py",
    ".rye\\py",
];

/// Disallowed PATH segments. 用于 §2 的 PATH 过滤，跟 base python marker 概念
/// 区分开（PATH 段是字符串匹配整段路径，base python 是 `pyvenv.cfg::home`
/// 单一目录）。两个列表故意分开维护，避免误伤合法路径段（例如某些项目用
/// `homebrew-bottles` 但目的不是激活 Homebrew Python）。
const BAD_PATH_MARKERS: &[&str] = &[
    "anaconda",
    "miniconda",
    "conda",
    "mambaforge",
    "miniforge",
    ".pyenv",
    "homebrew",
    "/cellar/",
    "\\cellar\\",
    ".asdf",
    ".mise",
    ".rye/py",
    ".rye\\py",
    "windowsapps",
];

/// 把绝对路径规整为统一可比较形式（lowercase + canonicalize，失败回退原值）。
/// 用于 marker 子串匹配 / 白名单 starts_with 检查时，跨平台保持一致。
fn normalize_path_for_compare(path: &Path) -> String {
    let resolved = path
        .canonicalize()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| path.to_string_lossy().to_string());
    resolved.to_lowercase()
}

/// 判断 `home` 是否落在我们自己管理的 Python 池里（PBS seed 或 uv 下载的
/// managed Python）。命中即视为可信，无视 marker 子串。
///
/// 解决的边角：用户把 OpenAkita 安装到 `C:\anaconda3\OpenAkita\` 这种路径，
/// 我们的 seed `pyvenv.cfg::home = C:\anaconda3\OpenAkita\resources\bootstrap\python`
/// 子串命中 "anaconda" 会被 `BAD_BASE_PYTHON_MARKERS` 误拒，进而陷入
/// "venv 自清 → 重建 → 仍被拒"的无限循环，永远走不到 dual-venv。
fn home_is_under_managed_python_root(home: &str) -> bool {
    let home_norm = normalize_path_for_compare(Path::new(home));
    let candidates = [
        bootstrap_resource_dir().join("python"), // PBS seed
        runtime_cache_dir().join("python"),      // uv downloaded managed
    ];
    for root in &candidates {
        let root_norm = normalize_path_for_compare(root);
        // 空字符串保护：路径不存在时 canonicalize 返回原值；空 root 不能用 starts_with。
        if !root_norm.is_empty() && home_norm.starts_with(&root_norm) {
            return true;
        }
    }
    false
}

/// 读取 `pyvenv.cfg` 的 `home=` 行，命中 `BAD_BASE_PYTHON_MARKERS` 返回 true。
/// 解析失败（例如文件残缺）当成 "未命中"，把判断交给后续的 import 测试。
///
/// 白名单：home 落在我们自己管理的 Python 池里时，永远不拒绝（避免
/// "用户装在 C:\anaconda3\OpenAkita\ → seed 路径含 anaconda → 自拒死循环"）。
fn pyvenv_cfg_home_is_disallowed(venv_dir: &Path) -> Option<String> {
    let cfg = venv_dir.join("pyvenv.cfg");
    let text = fs::read_to_string(&cfg).ok()?;
    let mut home: Option<String> = None;
    for line in text.lines() {
        let lower = line.to_lowercase();
        if lower.starts_with("home") || lower.starts_with("base-executable") {
            if let Some((_, rhs)) = line.split_once('=') {
                home = Some(rhs.trim().to_string());
                break;
            }
        }
    }
    let home = home?;
    if home_is_under_managed_python_root(&home) {
        return None;
    }
    let lower = home.to_lowercase();
    if BAD_BASE_PYTHON_MARKERS.iter().any(|m| lower.contains(m)) {
        Some(home)
    } else {
        None
    }
}

/// 严格判断目录是否是一个完整的 venv。
///
/// uv 在 Windows 上创建 venv 时会先写 `Scripts/python.exe`（一个 launcher
/// 桩），随后再写 `pyvenv.cfg`、`Lib/site-packages/`、seed pip。如果中间任何
/// 一步失败（被杀软拦截、断网下载 pip 失败、权限问题、用户强行关窗口等），
/// 残骸 launcher 会留在磁盘上。它跑起来时因为读不到 `pyvenv.cfg`，
/// `sys.prefix` 会回退到 base interpreter（即 uv 管理的全局 Python），
/// `import pip` 也能成功——但 `uv pip install --python <这个 launcher>`
/// 会判定为 "externally managed" 而拒绝安装。所以光看 `import pip`
/// 不足以证明这是一个真正的、隔离的 venv。
///
/// 额外地：直接拒绝 `pyvenv.cfg::home` 指向 Anaconda/pyenv/Homebrew 等
/// 受污染发行版的 venv（v1.27.10 启动失败的根因）。共享 `BAD_BASE_PYTHON_MARKERS`
/// 让 Rust 与 Python 两侧的判定逻辑严格对齐。
fn venv_is_real_isolated(venv_dir: &Path, py: &Path, log_path: &Path) -> bool {
    if !py.exists() {
        return false;
    }
    if !venv_dir.join("pyvenv.cfg").exists() {
        return false;
    }
    if let Some(home) = pyvenv_cfg_home_is_disallowed(venv_dir) {
        if let Ok(mut log) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_path)
        {
            let _ = writeln!(
                log,
                "venv {} rejected: pyvenv.cfg home={} matches BAD_BASE_PYTHON_MARKERS",
                venv_dir.display(),
                home
            );
        }
        return false;
    }
    health_check_python(
        py,
        "import sys, pip; assert sys.prefix != sys.base_prefix, 'venv launcher fell back to base interpreter'",
        log_path,
    )
}

fn app_runtime_health_code(venv_dir: &Path) -> String {
    let venv = python_string_literal(venv_dir);
    let home_markers = python_tuple_literal(BAD_BASE_PYTHON_MARKERS);
    let path_markers = python_tuple_literal(BAD_PATH_MARKERS);
    // 把"我们自己管理的 Python 池"也注入到 Python 侧，与 Rust
    // `home_is_under_managed_python_root` 严格对齐。让 marker 子串误命中我们
    // 自己 seed 路径的场景（用户装到 C:\anaconda3\OpenAkita\）也能放行。
    let managed_seed = python_string_literal(&bootstrap_resource_dir().join("python"));
    let managed_uv = python_string_literal(&runtime_cache_dir().join("python"));
    format!(
        r#"
import importlib, json, pathlib, site, sys

venv = pathlib.Path({venv}).resolve()
managed_roots = []
for raw in ({managed_seed}, {managed_uv}):
    try:
        managed_roots.append(str(pathlib.Path(raw).resolve()).lower())
    except Exception:
        managed_roots.append(str(raw).lower())

report = {{
    "sys_executable": sys.executable,
    "sys_prefix": sys.prefix,
    "sys_base_prefix": sys.base_prefix,
    "sys_prefix_resolved": str(pathlib.Path(sys.prefix).resolve()),
    "venv": str(venv),
    "sys_path": sys.path,
    "site_packages": [],
    "packages": {{}},
    "native_extensions": [],
    "managed_roots": managed_roots,
}}

def fail(reason):
    report["health_status"] = "failed"
    report["health_reason"] = reason
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(23)

def is_under(path, root):
    try:
        pathlib.Path(path).resolve().relative_to(root)
        return True
    except Exception:
        return False

def is_managed_home(home):
    try:
        h = str(pathlib.Path(home).resolve()).lower()
    except Exception:
        h = str(home).lower()
    return any(r and h.startswith(r) for r in managed_roots)

cfg = venv / "pyvenv.cfg"
if not cfg.exists():
    fail("pyvenv.cfg missing")
if pathlib.Path(sys.prefix).resolve() != venv:
    fail("sys.prefix does not match expected venv")
if sys.prefix == sys.base_prefix:
    fail("venv launcher fell back to base interpreter")

cfg_text = cfg.read_text(encoding="utf-8", errors="replace")
home = ""
for line in cfg_text.splitlines():
    if line.lower().startswith("home"):
        home = line.split("=", 1)[1].strip()
        break
report["pyvenv_home"] = home
report["pyvenv_home_managed"] = is_managed_home(home) if home else False
bad_home_markers = {home_markers}
if (
    not is_managed_home(home)
    and any(marker in home.lower() for marker in bad_home_markers)
):
    fail("pyvenv.cfg home points to disallowed Python: " + home)

try:
    report["site_packages"] = [str(pathlib.Path(p).resolve()) for p in site.getsitepackages()]
except Exception as exc:
    report["site_packages_error"] = repr(exc)
report["user_site"] = getattr(site, "ENABLE_USER_SITE", None)
if getattr(site, "ENABLE_USER_SITE", False):
    fail("user site-packages is enabled")

bad_path_markers = {path_markers}
for p in sys.path:
    low = str(p).lower()
    if "site-packages" in low and any(marker in low for marker in bad_path_markers):
        fail("sys.path contains disallowed site-packages: " + str(p))

for mod_name in ("openakita", "pydantic", "pydantic_core", "certifi"):
    mod = importlib.import_module(mod_name)
    mod_file = pathlib.Path(getattr(mod, "__file__", "") or "").resolve()
    report["packages"][mod_name] = str(mod_file)
    if not mod_file or not is_under(mod_file, venv):
        fail(f"{{mod_name}} imported from outside app runtime: {{mod_file}}")
    root = mod_file.parent
    for ext in list(root.rglob("*.pyd")) + list(root.rglob("*.so")) + list(root.rglob("*.dylib")):
        report["native_extensions"].append(str(ext.resolve()))
        if not is_under(ext, venv):
            fail("native extension imported from outside app runtime: " + str(ext))

report["health_status"] = "ok"
print(json.dumps(report, ensure_ascii=False, indent=2))
"#
    )
}

fn ensure_venv(venv_dir: &Path, python_version: &str, log_path: &Path) -> Result<PathBuf, String> {
    let py = runtime_venv_python_path(venv_dir);
    if venv_is_real_isolated(venv_dir, &py, log_path) {
        return Ok(py);
    }

    // 在重建前彻底清空残骸目录。`uv venv --clear` 自身在某些边界条件下
    // 会留下半残文件（典型场景：上次 uv 在 seed pip 阶段被中断，留下
    // launcher 但缺 pyvenv.cfg），下次再调 `uv venv --clear` 不一定能恢复。
    // 自己 remove_dir_all 一刀更稳。
    if venv_dir.exists() {
        if let Err(e) = fs::remove_dir_all(venv_dir) {
            if let Ok(mut log) = OpenOptions::new()
                .create(true)
                .append(true)
                .open(log_path)
            {
                let _ = writeln!(
                    log,
                    "warning: pre-clean of {} failed: {} (will fall back to `uv venv --clear`)",
                    venv_dir.display(),
                    e
                );
            }
        }
    }

    let uv = bootstrap_uv_path();
    let mut cmd = Command::new(&uv);
    // uv does not guarantee pip is present unless the venv is seeded. The
    // runtime manager immediately uses `uv pip install` and the health checks
    // require `import pip`, so seed the venv at creation time.
    cmd.arg("venv");
    if let Some(seed) = managed_python_seed_path() {
        // POSIX defensive 0o755 on the seed binary right before invocation.
        // 解决两个边角：
        //   1. installer 解压时 mode bit 在某些杀软策略下被重置；
        //   2. Sync 工具（Dropbox / iCloud / OneDrive 同步用户目录）回写后
        //      丢失 exec bit。
        // 与 prepare 阶段的 chmod 和 CI 校验形成三道防线；忽略错误，
        // 失败时让后续 uv 自己报 EACCES 给用户看，保持现有错误链。
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(meta) = fs::metadata(&seed) {
                let mut perms = meta.permissions();
                let current = perms.mode() & 0o777;
                if current & 0o111 == 0 {
                    perms.set_mode(0o755);
                    let _ = fs::set_permissions(&seed, perms);
                    log_to_file(&format!(
                        "[runtime] restored exec bit on seed Python: {} (was {:o})",
                        seed.display(),
                        current
                    ));
                }
            }
        }
        cmd.arg("--python").arg(&seed);
    } else {
        cmd.args(["--python", python_version]);
    }
    cmd.args(["--seed", "--clear"]);
    cmd.arg(venv_dir);
    apply_runtime_bootstrap_env(&mut cmd, None);
    apply_no_window(&mut cmd);
    run_and_log(cmd, log_path)?;
    if venv_is_real_isolated(venv_dir, &py, log_path) {
        Ok(py)
    } else {
        let has_cfg = venv_dir.join("pyvenv.cfg").exists();
        Err(format!(
            "venv health check failed after creation: {} (pyvenv.cfg present={}, see {} for details)",
            py.display(),
            has_cfg,
            log_path.display()
        ))
    }
}

fn ensure_app_venv(
    bootstrap: &BootstrapManifest,
    pip_index: &RuntimePipIndex,
) -> Result<PathBuf, String> {
    let started = Instant::now();
    let log_path = runtime_logs_dir().join("app-venv.log");
    let app_py = runtime_venv_python_path(&app_venv_dir());
    let expected_version = env!("CARGO_PKG_VERSION");
    let manifest_ok = read_runtime_manifest()
        .map(|m| {
            m.app_version == expected_version
                && m.wheel_hash == bootstrap.wheel.sha256
                && !m.legacy_mode
        })
        .unwrap_or(false);
    if manifest_ok && health_check_python(&app_py, &app_runtime_health_code(&app_venv_dir()), &log_path) {
        log_to_file(&format!(
            "[runtime] ensure_app_venv reused existing env in {}ms",
            started.elapsed().as_millis()
        ));
        return Ok(app_py);
    }

    log_to_file("[runtime] ensure_app_venv rebuilding app runtime");
    let app_py = ensure_venv(&app_venv_dir(), &bootstrap.python_version, &log_path)?;
    let wheel_path = bootstrap_resource_dir().join(&bootstrap.wheel.name);
    if !wheel_path.exists() {
        return Err(format!(
            "bootstrap wheel not found: {}",
            wheel_path.display()
        ));
    }
    let wheel_arg = format!("{}[desktop]", wheel_path.display());
    let mut cmd = Command::new(bootstrap_uv_path());
    cmd.args(["pip", "install", "--python"]);
    cmd.arg(&app_py);
    cmd.arg(wheel_arg);
    // 显式把 certifi 加进同一次安装：虽然 httpx/requests/aiohttp 会传递依赖
    // certifi，但 [desktop] extras 不一定每次都触发它；显式钉死避免万一某个
    // resolver 走捷径跳过 certifi 导致 ssl.create_default_context() 找不到
    // cacert.pem（用户日志里 dashscope/QQBot 的 SSL [Errno 2] 根因）。
    cmd.arg("certifi");
    cmd.args(["--reinstall-package", "openakita"]);
    // `uv pip install` does not support pip's `--prefer-binary` flag.
    // Keep binary preference on Python-side `pip install` calls only.
    cmd.args(["--index-url", &pip_index.url]);
    if !pip_index.trusted_host.trim().is_empty() {
        cmd.args(["--trusted-host", &pip_index.trusted_host]);
    }
    apply_runtime_bootstrap_env(&mut cmd, Some(pip_index));
    apply_no_window(&mut cmd);
    let install_started = Instant::now();
    run_and_log(cmd, &log_path)?;
    log_to_file(&format!(
        "[runtime] app wheel install finished in {}ms",
        install_started.elapsed().as_millis()
    ));
    if health_check_python(&app_py, &app_runtime_health_code(&app_venv_dir()), &log_path) {
        log_to_file(&format!(
            "[runtime] ensure_app_venv ready in {}ms",
            started.elapsed().as_millis()
        ));
        Ok(app_py)
    } else {
        // health check 失败：必须把整个 app-venv 目录干掉，避免下一次启动
        // `venv_is_real_isolated` 仍把它当成"完整 venv"，跳过重建，又白白
        // 跑一次 30–60s 的 wheel install + 同样的 reject。`remove_dir_all`
        // 失败不致命（下次 `uv venv --clear` 还会兜底），只记录到日志。
        if let Err(e) = fs::remove_dir_all(app_venv_dir()) {
            log_to_file(&format!(
                "[runtime] post-fail cleanup of {} failed: {}",
                app_venv_dir().display(),
                e
            ));
        } else {
            log_to_file(&format!(
                "[runtime] post-fail cleanup of {} succeeded",
                app_venv_dir().display()
            ));
        }
        Err("app venv health check failed after OpenAkita install".into())
    }
}

fn ensure_agent_venv(
    bootstrap: &BootstrapManifest,
    _pip_index: &RuntimePipIndex,
) -> Result<PathBuf, String> {
    let started = Instant::now();
    let log_path = runtime_logs_dir().join("agent-venv.log");
    let result = ensure_venv(&agent_venv_dir(), &bootstrap.python_version, &log_path);
    log_to_file(&format!(
        "[runtime] ensure_agent_venv finished in {}ms status={}",
        started.elapsed().as_millis(),
        if result.is_ok() { "ok" } else { "error" }
    ));
    result
}

fn write_runtime_manifest(info: &RuntimeEnvInfo, bootstrap: &BootstrapManifest) {
    let now = now_epoch_secs().to_string();
    let manifest = RuntimeManifest {
        schema_version: 1,
        app_version: env!("CARGO_PKG_VERSION").into(),
        wheel_hash: bootstrap.wheel.sha256.clone(),
        python_version: bootstrap.python_version.clone(),
        app_venv: RuntimeEnvState {
            path: info.app_venv.to_string_lossy().to_string(),
            status: "ready".into(),
            created_at: now.clone(),
            last_verified_at: now.clone(),
        },
        agent_venv: RuntimeEnvState {
            path: info.agent_venv.to_string_lossy().to_string(),
            status: "ready".into(),
            created_at: now.clone(),
            last_verified_at: now,
        },
        pip_index: info.pip_index.clone(),
        legacy_mode: false,
        last_error: None,
    };
    if let Ok(content) = serde_json::to_string_pretty(&manifest) {
        let _ = fs::write(runtime_manifest_path(), content);
    }
}

fn mark_legacy_runtime_mode(error: &str) {
    let pip_index = resolve_runtime_pip_index();
    let now = now_epoch_secs().to_string();
    // 即便 dual-venv 创建失败回退到 PyInstaller bundled 后端，也把 bootstrap
    // manifest 中的 wheel sha256 写进 runtime manifest。否则 wheel_hash 永远是
    // 空串，`runtime_wheel_hash_matches_bootstrap()` 永远返回 false，下一次
    // `startup_version_check` 会判定"wheel 变了"并主动 stop_backend_for_restart，
    // 把刚 fallback 拉起来的 bundled 后端反复杀掉，造成"启动一下又无响应"循环。
    let (wheel_hash, python_version) = match read_bootstrap_manifest() {
        Ok(b) => (b.wheel.sha256, b.python_version),
        Err(_) => (String::new(), "3.12".to_string()),
    };
    let manifest = RuntimeManifest {
        schema_version: 1,
        app_version: env!("CARGO_PKG_VERSION").into(),
        wheel_hash,
        python_version,
        app_venv: RuntimeEnvState {
            path: app_venv_dir().to_string_lossy().to_string(),
            status: "failed".into(),
            created_at: now.clone(),
            last_verified_at: now.clone(),
        },
        agent_venv: RuntimeEnvState {
            path: agent_venv_dir().to_string_lossy().to_string(),
            status: "unknown".into(),
            created_at: now.clone(),
            last_verified_at: now,
        },
        pip_index,
        legacy_mode: true,
        last_error: Some(error.to_string()),
    };
    if let Ok(content) = serde_json::to_string_pretty(&manifest) {
        let _ = fs::write(runtime_manifest_path(), content);
    }
}

fn write_runtime_failure_manifest(error: &str) {
    let pip_index = resolve_runtime_pip_index();
    let now = now_epoch_secs().to_string();
    let (wheel_hash, python_version) = match read_bootstrap_manifest() {
        Ok(b) => (b.wheel.sha256, b.python_version),
        Err(_) => (String::new(), default_python_version()),
    };
    let manifest = RuntimeManifest {
        schema_version: 1,
        app_version: env!("CARGO_PKG_VERSION").into(),
        wheel_hash,
        python_version,
        app_venv: RuntimeEnvState {
            path: app_venv_dir().to_string_lossy().to_string(),
            status: "failed".into(),
            created_at: now.clone(),
            last_verified_at: now.clone(),
        },
        agent_venv: RuntimeEnvState {
            path: agent_venv_dir().to_string_lossy().to_string(),
            status: "failed".into(),
            created_at: now.clone(),
            last_verified_at: now,
        },
        pip_index,
        legacy_mode: false,
        last_error: Some(error.to_string()),
    };
    if let Ok(content) = serde_json::to_string_pretty(&manifest) {
        let _ = fs::write(runtime_manifest_path(), content);
    }
}

fn ensure_dual_runtime_env() -> Result<RuntimeEnvInfo, String> {
    let started = Instant::now();
    ensure_runtime_layout()?;
    let bootstrap = read_bootstrap_manifest()?;
    let pip_index = resolve_runtime_pip_index();
    let app_python = ensure_app_venv(&bootstrap, &pip_index)?;
    let agent_python = ensure_agent_venv(&bootstrap, &pip_index)?;
    let info = RuntimeEnvInfo {
        app_python,
        agent_python,
        app_venv: app_venv_dir(),
        agent_venv: agent_venv_dir(),
        pip_index,
    };
    write_runtime_manifest(&info, &bootstrap);
    log_to_file(&format!(
        "[runtime] ensure_dual_runtime_env finished in {}ms",
        started.elapsed().as_millis()
    ));
    Ok(info)
}

/// 读取 cmd 上已设置的 PATH（如有），找不到则回退到父进程 PATH。
/// Windows 环境变量名大小写不敏感，所以采用 eq_ignore_ascii_case。
fn cmd_get_env_path(cmd: &Command) -> Option<std::ffi::OsString> {
    cmd.get_envs().find_map(|(k, v)| {
        let key = k.to_string_lossy();
        if key.eq_ignore_ascii_case("path") {
            v.map(|s| s.to_os_string())
        } else {
            None
        }
    })
}

fn prepend_path(cmd: &mut Command, dir: &Path) {
    // 关键：优先读 cmd 上已设置的 PATH —— 上游 `filter_path_for_runtime` 可能
    // 已经把 anaconda/pyenv/homebrew 等污染段剔除并写回 cmd；如果这里仍然
    // 读父进程 PATH，会把过滤掉的段又带回来，让 §2 的 PATH 过滤白做。
    // 找不到再回退到父进程 PATH，与原行为兼容。
    let current = cmd_get_env_path(cmd).unwrap_or_else(|| std::env::var_os("PATH").unwrap_or_default());
    let mut paths = vec![dir.to_path_buf()];
    paths.extend(std::env::split_paths(&current));
    if let Ok(joined) = std::env::join_paths(paths) {
        cmd.env("PATH", joined);
    }
}

/// 从子进程 PATH 中剔除已知会污染 Python 发现的目录段（anaconda / pyenv /
/// homebrew / mise / asdf / WindowsApps stub）。
///
/// 与 `BAD_BASE_PYTHON_MARKERS`（pyvenv.cfg::home 检查）共用同一组关键字概念
/// 但**故意不共享列表**：PATH 段是整段路径子串匹配，过宽会误伤合法路径，例如
/// 用户把项目放在 `D:\anaconda-projects\` 下并不应当被剔除（那个目录里没有
/// `python.exe`）。所以 PATH 过滤的 marker 边界与 BAD_PATH_MARKERS 对齐，
/// 但保持独立维护，给后续微调留余地。
///
/// 实现策略：
///   * 用 `std::env::split_paths` 解析当前进程 PATH（按 `;`/`:` 自动适配
///     平台分隔符）；
///   * 对每段路径做 lowercase 后子串比对（Windows 不区分大小写、*nix 也兼容
///     `/opt/Anaconda` 这种异常大小写）；
///   * 命中关键字的段**只**在该段末尾或其下确实存在 `python` / `python.exe`
///     时才剔除。这样不会误伤"anaconda-projects/data"这种巧合命名的工作目录。
///   * 把剩余段重新 `join_paths` 写回子进程 env。
fn filter_path_for_runtime(cmd: &mut Command) {
    let current = match std::env::var_os("PATH") {
        Some(v) => v,
        None => return,
    };
    let mut kept: Vec<PathBuf> = Vec::new();
    let mut removed: Vec<String> = Vec::new();
    for seg in std::env::split_paths(&current) {
        let lowered = seg.to_string_lossy().to_lowercase();
        let matched = BAD_PATH_MARKERS.iter().any(|m| lowered.contains(m));
        if matched && segment_contains_python_binary(&seg) {
            removed.push(seg.to_string_lossy().to_string());
            continue;
        }
        kept.push(seg);
    }
    if !removed.is_empty() {
        log_to_file(&format!(
            "[runtime] PATH filtered: stripped {} segments matching BAD_PATH_MARKERS; sample={:?}",
            removed.len(),
            removed.iter().take(3).collect::<Vec<_>>()
        ));
    }
    if let Ok(joined) = std::env::join_paths(kept) {
        cmd.env("PATH", joined);
    }
}

/// 判断给定路径段下是否真的能找到一个 Python 可执行文件。
/// 用于 `filter_path_for_runtime` 仅在该段确实承载 Python 时才剔除，
/// 减少误伤。
fn segment_contains_python_binary(seg: &Path) -> bool {
    if !seg.is_dir() {
        return false;
    }
    let candidates: &[&str] = if cfg!(windows) {
        &["python.exe", "python3.exe", "pythonw.exe"]
    } else {
        &["python", "python3", "python3.11", "python3.12"]
    };
    candidates.iter().any(|name| seg.join(name).exists())
}

fn apply_dual_runtime_env(cmd: &mut Command) {
    apply_runtime_core_env(cmd);
}

/// 获取安装包内置的 Python 解释器路径（openakita-server/_internal）
fn bundled_internal_python_path() -> Option<PathBuf> {
    let bundled = bundled_backend_dir();
    if !bundled.exists() {
        return None;
    }
    let candidates: Vec<PathBuf> = if cfg!(windows) {
        vec![bundled.join("_internal").join("python.exe")]
    } else {
        vec![
            bundled.join("_internal").join("python3"),
            bundled.join("_internal").join("python"),
        ]
    };
    let internal_dir = bundled.join("_internal");
    for internal_py in candidates {
        if !internal_py.exists() {
            continue;
        }
        let mut c = Command::new(&internal_py);
        c.args(["-c", "import pip; print(pip.__version__)"]);
        apply_bundled_python_env(&mut c, &internal_dir);
        apply_no_window(&mut c);
        if let Ok(output) = c.output() {
            if output.status.success() {
                return Some(internal_py);
            }
        }
    }
    None
}

/// 获取后端可执行文件及参数
/// 优先使用 dual app-venv，失败后保留 PyInstaller legacy fallback。
fn get_backend_executable(venv_dir: &str) -> (PathBuf, Vec<String>) {
    // 1. 优先: dual runtime app venv
    match ensure_dual_runtime_env() {
        Ok(runtime) => {
            let backend_python = runtime_venv_backend_python_path(&runtime.app_venv);
            log_to_file(&format!(
                "[runtime] dual venv ready: app_python={}, backend_python={}, agent_python={}",
                runtime.app_python.display(),
                backend_python.display(),
                runtime.agent_python.display()
            ));
            return (backend_python, runtime_venv_backend_args(&runtime.app_venv));
        }
        Err(e) => {
            log_to_file(&format!(
                "[runtime] dual venv unavailable, fallback to legacy: {e}"
            ));
            mark_legacy_runtime_mode(&e);
        }
    }

    // 2. fallback: 内嵌的 PyInstaller 打包后端
    let bundled_dir = bundled_backend_dir();
    let bundled_exe = if cfg!(windows) {
        bundled_dir.join("openakita-server.exe")
    } else {
        bundled_dir.join("openakita-server")
    };
    if bundled_exe.exists() {
        return (bundled_exe, vec!["serve".to_string()]);
    }
    // 3. 最后降级: 旧 ~/.openakita/venv python（开发模式 / 旧安装）
    eprintln!(
        "[backend] dual runtime and bundled openakita-server unavailable at: {}\n\
         [backend] current_exe: {:?}\n\
         [backend] falling back to venv python in: {}",
        bundled_exe.display(),
        std::env::current_exe()
            .ok()
            .map(|p| p.display().to_string()),
        venv_dir,
    );
    let py = venv_pythonw_path(venv_dir);
    (
        py,
        vec!["-m".into(), "openakita.main".into(), "serve".into()],
    )
}

/// 构建可选模块路径字符串（自动从 module_definitions 获取模块列表）
/// 返回 path-separated 的 site-packages 目录列表，用于 OPENAKITA_MODULE_PATHS 环境变量
fn build_modules_pythonpath() -> Option<String> {
    let base = modules_dir();
    if !base.exists() {
        return None;
    }
    let mut paths = Vec::new();
    for (module_id, _, _, _, _, _) in module_definitions() {
        let sp = base.join(module_id).join("site-packages");
        if sp.exists() {
            paths.push(sp.to_string_lossy().to_string());
        }
    }
    if paths.is_empty() {
        return None;
    }
    let sep = if cfg!(windows) { ";" } else { ":" };
    Some(paths.join(sep))
}

/// 查找可用于 pip install 的 Python 可执行文件路径
fn find_pip_python() -> Option<PathBuf> {
    let root = openakita_root_dir();
    // 1. venv python
    let venv_py = if cfg!(windows) {
        root.join("venv").join("Scripts").join("python.exe")
    } else {
        root.join("venv").join("bin").join("python")
    };
    if venv_py.exists() {
        return Some(venv_py);
    }
    // 2. 安装包内置 python（PyInstaller _internal 目录）
    if let Some(py) = bundled_internal_python_path() {
        return Some(py);
    }
    // 不再搜索用户系统 PATH 中的 Python，也不再运行时下载 Python。
    // 统一要求：使用安装包内置 Python 创建/修复 venv。
    None
}

/// 检查是否有可用于 pip install 的 Python 解释器
#[tauri::command]
fn check_python_for_pip() -> Result<String, String> {
    match find_pip_python() {
        Some(p) => Ok(format!("Python 可用: {}", p.display())),
        None => Err("未找到可用的 Python 解释器".into()),
    }
}

/// 暴露 runtime manifest 的 `last_error` 与 `legacy_mode` 给前端。
///
/// 用途：前端 StatusView 在"后端已停止 / 启动失败"时调本命令，识别
/// `RUNTIME_PERMISSION_DENIED|...` 前缀并渲染中英双语指引 + "打开运行时
/// 目录"按钮。其它结构化前缀（如 `RUNTIME_WHEEL_HASH_MISMATCH|`）也走
/// 同一通道，前端按前缀分发。
///
/// 返回 None 表示尚无 runtime manifest（首次启动尚未跑到 ensure_runtime_layout）。
#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct RuntimeLastError {
    last_error: Option<String>,
    legacy_mode: bool,
    runtime_root: String,
    manifest_path: String,
}

#[tauri::command]
fn openakita_runtime_last_error() -> RuntimeLastError {
    let manifest = read_runtime_manifest();
    RuntimeLastError {
        last_error: manifest.as_ref().and_then(|m| m.last_error.clone()),
        legacy_mode: manifest.as_ref().map(|m| m.legacy_mode).unwrap_or(false),
        runtime_root: runtime_root_dir().to_string_lossy().to_string(),
        manifest_path: runtime_manifest_path().to_string_lossy().to_string(),
    }
}

/// "打开 runtime 目录"命令——专门为 PermissionDenied 等 banner 设计。
///
/// 与通用 `show_item_in_folder` 不同的点：当 runtime root 还没被创建（典型场景
/// 就是 PermissionDenied 之前的失败），通用命令会直接抛 `Path does not exist`，
/// 用户什么也看不到。本命令向上溯源，找到最近一级**确实存在**的祖先目录
/// 并打开，让用户能在自己的文件管理器里看到现场（例如 `%LOCALAPPDATA%\
/// OpenAkitaDesktop\` 还在，但 `runtime\` 子目录因为 AD 策略建不出来）。
///
/// 返回的 `fellBack=true` 标记给前端用，用来弹一条"我们退回到上一级"的提示。
#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct OpenedRuntimePath {
    opened: String,
    /// true: 目标路径不存在，已退回到最近一级存在的祖先。
    fell_back: bool,
}

fn first_existing_ancestor(start: &Path) -> Option<PathBuf> {
    let mut cur: Option<&Path> = Some(start);
    while let Some(p) = cur {
        if p.exists() {
            return Some(p.to_path_buf());
        }
        cur = p.parent();
    }
    None
}

fn reveal_in_file_manager(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        let mut c = std::process::Command::new("explorer");
        // 目录直接打开，文件用 /select, 高亮。
        if path.is_dir() {
            c.arg(path);
        } else {
            c.args(["/select,", &path.to_string_lossy()]);
        }
        apply_no_window(&mut c);
        c.spawn()
            .map_err(|e| format!("Failed to open explorer: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        // Finder 对目录/文件都接受 -R（reveal）；目录则直接 open 也可。
        let arg = if path.is_dir() { "" } else { "-R" };
        let mut c = std::process::Command::new("open");
        if !arg.is_empty() {
            c.arg(arg);
        }
        c.arg(path);
        c.spawn()
            .map_err(|e| format!("Failed to reveal in Finder: {e}"))?;
    }
    #[cfg(target_os = "linux")]
    {
        let target: PathBuf = if path.is_dir() {
            path.to_path_buf()
        } else {
            path.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| path.to_path_buf())
        };
        std::process::Command::new("xdg-open")
            .arg(&target)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {e}"))?;
    }
    Ok(())
}

#[tauri::command]
fn openakita_open_runtime_root() -> Result<OpenedRuntimePath, String> {
    let target = runtime_root_dir();
    let (resolved, fell_back) = if target.exists() {
        (target.clone(), false)
    } else {
        let ancestor = first_existing_ancestor(&target).ok_or_else(|| {
            format!(
                "No existing ancestor for runtime root: {}",
                target.display()
            )
        })?;
        (ancestor, true)
    };
    reveal_in_file_manager(&resolved)?;
    Ok(OpenedRuntimePath {
        opened: resolved.to_string_lossy().to_string(),
        fell_back,
    })
}

// ── 模块定义（供 build_modules_pythonpath 使用） ──

fn module_definitions() -> Vec<(
    &'static str,
    &'static str,
    &'static str,
    &'static [&'static str],
    u32,
    &'static str,
)> {
    // (id, name, description, pip_packages, estimated_size_mb, category)
    //
    // 仅体积大(>50MB)或有特殊二进制依赖的包才需要模块化安装。
    // 其余轻量包(文档处理/图像处理/桌面自动化/IM适配器等)已直接打包进 PyInstaller bundle。
    // browser (playwright + browser-use + langchain-openai) 已内置到 core 包，不再作为外置模块
    vec![
        ("vector-memory", "向量记忆增强", "让 Akita 拥有长期记忆，能根据语义搜索历史对话。体积较大（约 2.5GB，含 PyTorch），安装耗时较长", &["sentence-transformers", "chromadb", "regex>=2023.6.3"], 2500, "core"),
    ]
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct RootDirInfo {
    default_root: String,
    current_root: String,
    custom_root: Option<String>,
}

#[tauri::command]
fn get_root_dir_info() -> RootDirInfo {
    RootDirInfo {
        default_root: default_root_dir().to_string_lossy().to_string(),
        current_root: openakita_root_dir().to_string_lossy().to_string(),
        custom_root: read_root_config().custom_root,
    }
}

#[tauri::command]
fn set_custom_root_dir(path: Option<String>, migrate: bool) -> Result<RootDirInfo, String> {
    let _lock = ROOT_CONFIG_LOCK
        .lock()
        .map_err(|e| format!("lock failed: {e}"))?;
    let clean_path = path
        .as_deref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(String::from);

    if let Some(ref p) = clean_path {
        let target = PathBuf::from(p);
        if !target.is_absolute() {
            return Err("请使用绝对路径（如 D:\\MyData\\.openakita 或 /data/openakita）".into());
        }
        ensure_safe_openakita_data_root(&target)?;
        if target.exists() && !target.is_dir() {
            return Err("指定的路径已存在但不是目录".into());
        }
        fs::create_dir_all(&target).map_err(|e| format!("无法创建目标目录: {e}"))?;
        write_root_marker(&target)?;
        // 验证目录可写
        let test_file = target.join(".openakita_write_test");
        fs::write(&test_file, "test").map_err(|e| format!("目标目录无写入权限: {e}"))?;
        let _ = fs::remove_file(&test_file);
    }

    let migrate_old_root: Option<PathBuf> = if migrate {
        let old_root = openakita_root_dir();
        let new_root_path = match &clean_path {
            Some(p) => PathBuf::from(p),
            None => default_root_dir(),
        };

        if old_root != new_root_path && old_root.exists() {
            if !new_root_path.exists() {
                fs::create_dir_all(&new_root_path).map_err(|e| format!("无法创建目标目录: {e}"))?;
            }

            let critical_dirs = ["workspaces"];
            let optional_dirs = ["venv", "runtime", "run", "logs", "modules", "bin"];
            let mut errors: Vec<String> = Vec::new();

            for entry_name in critical_dirs.iter().chain(optional_dirs.iter()) {
                let src = old_root.join(entry_name);
                let dst = new_root_path.join(entry_name);
                if src.exists() && src.is_dir() && !dst.exists() {
                    if let Err(e) = copy_dir_recursive(&src, &dst) {
                        let msg = format!("{}: {}", entry_name, e);
                        eprintln!("migrate dir {}", msg);
                        if critical_dirs.contains(entry_name) {
                            let _ = fs::remove_dir_all(&dst);
                            return Err(format!(
                                "关键目录 {} 复制失败，已中止迁移，配置未更改。错误: {}",
                                entry_name, e
                            ));
                        }
                        errors.push(msg);
                    }
                }
            }
            for file_name in &["state.json", "cli.json"] {
                let src = old_root.join(file_name);
                let dst = new_root_path.join(file_name);
                if src.exists() && src.is_file() && !dst.exists() {
                    if let Err(e) = fs::copy(&src, &dst) {
                        errors.push(format!("{}: {}", file_name, e));
                        eprintln!("migrate file {}: {}", file_name, e);
                    }
                }
            }
            if !errors.is_empty() {
                eprintln!(
                    "migration completed with {} non-critical errors",
                    errors.len()
                );
            }

            if !new_root_path.exists() || !new_root_path.is_dir() {
                return Err(
                    "迁移完成后目标目录不可访问，未更改配置。请检查磁盘连接后重试。".into(),
                );
            }
            Some(old_root)
        } else {
            None
        }
    } else {
        None
    };

    let config = RootConfig {
        custom_root: clean_path,
    };
    write_root_config(&config)?;

    // Config updated successfully — clean up migrated entries from old root
    if let Some(ref old_root) = migrate_old_root {
        if is_safe_openakita_data_root(old_root) {
            let dir_names = [
                "workspaces",
                "venv",
                "runtime",
                "run",
                "logs",
                "modules",
                "bin",
            ];
            let file_names = ["state.json", "cli.json"];
            for name in &dir_names {
                let p = old_root.join(name);
                if p.exists() && p.is_dir() {
                    if let Err(e) = fs::remove_dir_all(&p) {
                        eprintln!("cleanup old {}: {e}", p.display());
                    }
                }
            }
            for name in &file_names {
                let p = old_root.join(name);
                if p.exists() && p.is_file() {
                    let _ = fs::remove_file(&p);
                }
            }
        } else {
            eprintln!("skip cleanup for unsafe old root {}", old_root.display());
        }
    }

    Ok(RootDirInfo {
        default_root: default_root_dir().to_string_lossy().to_string(),
        current_root: openakita_root_dir().to_string_lossy().to_string(),
        custom_root: config.custom_root,
    })
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<(), String> {
    fs::create_dir_all(dst).map_err(|e| format!("create dir {}: {e}", dst.display()))?;
    let entries = fs::read_dir(src).map_err(|e| format!("read dir {}: {e}", src.display()))?;
    for entry in entries.flatten() {
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        // file_type() 不跟随符号链接（区别于 metadata()），能正确识别 symlink
        let ft = match entry.file_type() {
            Ok(ft) => ft,
            Err(_) => continue,
        };
        if ft.is_symlink() {
            continue;
        }
        if ft.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else if ft.is_file() {
            if let Err(e) = fs::copy(&src_path, &dst_path) {
                eprintln!(
                    "copy file {} -> {}: {e}",
                    src_path.display(),
                    dst_path.display()
                );
            }
        }
    }
    Ok(())
}

// ── Workspace migration preflight ──

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct MigrateEntry {
    name: String,
    size_mb: f64,
    exists_at_target: bool,
    is_dir: bool,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct MigratePreflightInfo {
    source_path: String,
    source_size_mb: f64,
    target_path: String,
    target_free_mb: f64,
    entries: Vec<MigrateEntry>,
    can_migrate: bool,
    reason: String,
}

fn available_space_mb(path: &Path) -> f64 {
    #[cfg(target_os = "windows")]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        let fallback = path
            .ancestors()
            .last()
            .map(|r| r.to_string_lossy().to_string())
            .unwrap_or_else(|| "C:\\".to_string());
        let wide: Vec<u16> = OsStr::new(path.to_str().unwrap_or(&fallback))
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let mut free_bytes: u64 = 0;
        unsafe {
            #[link(name = "kernel32")]
            extern "system" {
                fn GetDiskFreeSpaceExW(
                    lpDirectoryName: *const u16,
                    lpFreeBytesAvailableToCaller: *mut u64,
                    lpTotalNumberOfBytes: *mut u64,
                    lpTotalNumberOfFreeBytes: *mut u64,
                ) -> i32;
            }
            GetDiskFreeSpaceExW(
                wide.as_ptr(),
                &mut free_bytes,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            );
        }
        free_bytes as f64 / 1024.0 / 1024.0
    }
    #[cfg(not(target_os = "windows"))]
    {
        use std::mem::MaybeUninit;
        let c_path = std::ffi::CString::new(path.to_str().unwrap_or("/")).unwrap_or_default();
        let mut stat = MaybeUninit::<libc::statvfs>::uninit();
        let ok = unsafe { libc::statvfs(c_path.as_ptr(), stat.as_mut_ptr()) };
        if ok == 0 {
            let stat = unsafe { stat.assume_init() };
            (stat.f_bavail as f64) * (stat.f_frsize as f64) / 1024.0 / 1024.0
        } else {
            0.0
        }
    }
}

#[tauri::command]
fn preflight_migrate_root(target_path: String) -> Result<MigratePreflightInfo, String> {
    let target = PathBuf::from(target_path.trim());
    if !target.is_absolute() {
        return Err("请使用绝对路径".into());
    }
    ensure_safe_openakita_data_root(&target)?;

    let source = openakita_root_dir();
    if source == target {
        return Ok(MigratePreflightInfo {
            source_path: source.to_string_lossy().to_string(),
            source_size_mb: 0.0,
            target_path: target.to_string_lossy().to_string(),
            target_free_mb: 0.0,
            entries: vec![],
            can_migrate: false,
            reason: "目标路径与当前路径相同".into(),
        });
    }

    let dir_names: &[&str] = &[
        "workspaces",
        "venv",
        "runtime",
        "run",
        "logs",
        "modules",
        "bin",
    ];
    let file_names: &[&str] = &["state.json", "cli.json"];

    let mut entries = Vec::new();
    let mut total_size: u64 = 0;

    for name in dir_names {
        let src = source.join(name);
        if src.exists() && src.is_dir() {
            let size = dir_size_bytes(&src);
            total_size += size;
            entries.push(MigrateEntry {
                name: name.to_string(),
                size_mb: size as f64 / 1024.0 / 1024.0,
                exists_at_target: target.join(name).exists(),
                is_dir: true,
            });
        }
    }
    for name in file_names {
        let src = source.join(name);
        if src.exists() && src.is_file() {
            let size = src.metadata().map(|m| m.len()).unwrap_or(0);
            total_size += size;
            entries.push(MigrateEntry {
                name: name.to_string(),
                size_mb: size as f64 / 1024.0 / 1024.0,
                exists_at_target: target.join(name).exists(),
                is_dir: false,
            });
        }
    }

    let free_space_path = if target.exists() {
        target.clone()
    } else {
        target
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| target.clone())
    };
    let target_free_mb = available_space_mb(&free_space_path);
    let source_size_mb = total_size as f64 / 1024.0 / 1024.0;

    let has_conflicts = entries.iter().any(|e| e.exists_at_target);
    let enough_space = target_free_mb > source_size_mb * 1.1 + 100.0;

    let (can_migrate, reason) = if entries.is_empty() {
        (false, "当前数据目录为空，无需迁移".into())
    } else if !enough_space {
        (
            false,
            format!(
                "目标磁盘空间不足（需要 {:.0} MB，可用 {:.0} MB）",
                source_size_mb * 1.1,
                target_free_mb
            ),
        )
    } else if has_conflicts {
        (true, "目标路径已存在部分数据，已有数据将被跳过".into())
    } else {
        (true, "可以迁移".into())
    };

    Ok(MigratePreflightInfo {
        source_path: source.to_string_lossy().to_string(),
        source_size_mb,
        target_path: target.to_string_lossy().to_string(),
        target_free_mb,
        entries,
        can_migrate,
        reason,
    })
}

#[tauri::command]
fn is_first_run() -> bool {
    let state = read_state_file();
    state.workspaces.is_empty()
}

// ── 环境检测 ──

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct EnvironmentCheck {
    /// 实际检查的根目录路径，便于用户核对是否与已删除的目录一致（如以管理员运行可能为另一用户目录）
    openakita_root: String,
    has_old_venv: bool,
    has_old_runtime: bool,
    has_old_workspaces: bool,
    old_version: Option<String>,
    current_version: String,
    running_processes: Vec<String>,
    disk_usage_mb: u64,
    conflicts: Vec<String>,
}

fn dir_size_bytes(path: &Path) -> u64 {
    if !path.exists() {
        return 0;
    }
    let mut total: u64 = 0;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                total += p.metadata().map(|m| m.len()).unwrap_or(0);
            } else if p.is_dir() {
                total += dir_size_bytes(&p);
            }
        }
    }
    total
}

#[tauri::command]
fn check_environment() -> EnvironmentCheck {
    let root = openakita_root_dir();
    // 只有目录存在且非空才算有旧残留
    let has_old_venv = root.join("venv").exists()
        && root
            .join("venv")
            .read_dir()
            .map(|mut d| d.next().is_some())
            .unwrap_or(false);
    let has_old_runtime = root.join("runtime").exists()
        && root
            .join("runtime")
            .read_dir()
            .map(|mut d| d.next().is_some())
            .unwrap_or(false);
    let has_old_workspaces = root.join("workspaces").exists()
        && root
            .join("workspaces")
            .read_dir()
            .map(|mut d| d.next().is_some())
            .unwrap_or(false);

    // Read version from state.json
    let state = read_state_file();
    let old_version = state.last_installed_version.clone();
    let current_version = env!("CARGO_PKG_VERSION").to_string();

    // Check running processes (extract workspace_id from filename: openakita-{ws_id}.pid)
    let mut running = Vec::new();
    if let Ok(entries) = fs::read_dir(run_dir()) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("pid") {
                let ws_id = path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .and_then(|s| s.strip_prefix("openakita-"))
                    .unwrap_or("unknown");
                if let Ok(content) = fs::read_to_string(&path) {
                    if let Ok(data) = serde_json::from_str::<PidFileData>(&content) {
                        if is_pid_running(data.pid) {
                            running.push(format!("PID {} (workspace: {})", data.pid, ws_id));
                        }
                    }
                }
            }
        }
    }

    let disk_usage_mb = dir_size_bytes(&root) / (1024 * 1024);

    // venv 是打包后应用运行时的关键组件：
    // - venv: 用于 pip install 模块（vector-memory 等）和工具执行
    // Python 基座改为安装包内置 _internal，不再依赖 runtime 下载链路。
    let _bundled_exists = bundled_backend_dir().exists();

    let mut conflicts = Vec::new();
    if !running.is_empty() {
        conflicts.push(format!(
            "检测到 {} 个正在运行的 OpenAkita 进程",
            running.len()
        ));
    }

    EnvironmentCheck {
        openakita_root: root.to_string_lossy().to_string(),
        has_old_venv,
        has_old_runtime,
        has_old_workspaces,
        old_version,
        current_version,
        running_processes: running,
        disk_usage_mb,
        conflicts,
    }
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct BackendAvailability {
    bundled: bool,
    venv_ready: bool,
    exe_path: String,
    bundled_checked: String,
    venv_checked: String,
}

#[tauri::command]
fn check_backend_availability(venv_dir: String) -> BackendAvailability {
    let bundled_dir = bundled_backend_dir();
    let bundled_exe = if cfg!(windows) {
        bundled_dir.join("openakita-server.exe")
    } else {
        bundled_dir.join("openakita-server")
    };
    let venv_py = venv_pythonw_path(&venv_dir);
    let bundled = bundled_exe.exists();
    let venv_ready = venv_py.exists();
    let exe_path = if bundled {
        bundled_exe.to_string_lossy().to_string()
    } else if venv_ready {
        venv_py.to_string_lossy().to_string()
    } else {
        String::new()
    };
    eprintln!(
        "[backend-check] bundled={} ({}) venv={} ({})",
        bundled,
        bundled_exe.display(),
        venv_ready,
        venv_py.display()
    );
    BackendAvailability {
        bundled,
        venv_ready,
        exe_path,
        bundled_checked: bundled_exe.to_string_lossy().to_string(),
        venv_checked: venv_py.to_string_lossy().to_string(),
    }
}

/// 强制删除目录：先尝试 Rust remove_dir_all，失败时在 Windows 上回退到 cmd /c rd /s /q
fn force_remove_dir(path: &std::path::Path) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }
    // 第一次尝试：Rust 标准库
    if fs::remove_dir_all(path).is_ok() {
        return Ok(());
    }
    // 第二次尝试 (Windows)：先去掉只读属性再 rd /s /q，避免“清不掉”
    #[cfg(target_os = "windows")]
    {
        let mut attrib = std::process::Command::new("cmd");
        attrib.args(["/c", "attrib", "-R", "/S", "/D"]).arg(path);
        apply_no_window(&mut attrib);
        let _ = attrib.status();
        let mut rd_cmd = std::process::Command::new("cmd");
        rd_cmd.args(["/c", "rd", "/s", "/q"]).arg(path);
        apply_no_window(&mut rd_cmd);
        let status = rd_cmd
            .status()
            .map_err(|e| format!("执行 rd 命令失败: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("chmod").args(["-R", "u+w"]).arg(path).status();
        let status = Command::new("rm")
            .args(["-rf"])
            .arg(path)
            .status()
            .map_err(|e| format!("rm -rf failed: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    if path.exists() {
        Err(format!("无法删除目录: {}", path.display()))
    } else {
        Ok(())
    }
}

#[tauri::command]
fn cleanup_old_environment(clean_venv: bool, clean_runtime: bool) -> Result<String, String> {
    let root = openakita_root_dir();
    let mut cleaned = Vec::new();
    let mut warnings = Vec::new();

    if clean_venv {
        let venv_path = root.join("venv");
        if venv_path.exists() {
            // 检查是否有已安装的外置模块依赖此 venv
            let modules_base = root.join("modules");
            let has_installed_modules = modules_base.exists()
                && modules_base
                    .read_dir()
                    .map(|mut d| d.any(|e| e.map(|e| e.path().is_dir()).unwrap_or(false)))
                    .unwrap_or(false);
            if has_installed_modules {
                warnings.push(
                    "注意: 清理 venv 后已安装的外置模块（vector-memory 等）可能需要重新安装"
                        .to_string(),
                );
            }
            force_remove_dir(&venv_path).map_err(|e| format!("清理 venv 失败: {e}"))?;
            cleaned.push("venv");
        }
    }
    if clean_runtime {
        let runtime_path = root.join("runtime");
        if runtime_path.exists() {
            force_remove_dir(&runtime_path).map_err(|e| format!("清理 runtime 失败: {e}"))?;
            cleaned.push("runtime");
        }
    }

    if cleaned.is_empty() {
        Ok("无需清理".to_string())
    } else {
        let mut msg = format!("已清理: {}", cleaned.join(", "));
        if !warnings.is_empty() {
            msg.push_str(&format!(" ({})", warnings.join("; ")));
        }
        Ok(msg)
    }
}

/// Reset the entire OpenAkita installation to factory state.
/// Stops all processes, then removes workspaces, runtime, venv, logs, etc.
/// Preserves only `root_config.json` (custom root dir setting).
#[tauri::command]
fn factory_reset() -> Result<String, String> {
    // 1. Stop all running backend processes
    let stopped = openakita_stop_all_processes();

    // 2. Determine root and build list of paths to remove
    let root = openakita_root_dir();
    let dirs_to_remove = [
        "workspaces",
        "venv",
        "runtime",
        "run",
        "logs",
        "modules",
        "bin",
        "data",
    ];
    let files_to_remove = ["state.json", "cli.json"];

    let mut removed = Vec::new();
    let mut errors = Vec::new();

    for name in &dirs_to_remove {
        let p = root.join(name);
        if p.exists() {
            match force_remove_dir(&p) {
                Ok(()) => removed.push(name.to_string()),
                Err(e) => errors.push(format!("{name}: {e}")),
            }
        }
    }

    for name in &files_to_remove {
        let p = root.join(name);
        if p.exists() {
            match fs::remove_file(&p) {
                Ok(()) => removed.push(name.to_string()),
                Err(e) => errors.push(format!("{name}: {e}")),
            }
        }
    }

    if !errors.is_empty() {
        return Err(format!(
            "部分重置失败: {}{}",
            errors.join("; "),
            if !removed.is_empty() {
                format!(" (已清理: {})", removed.join(", "))
            } else {
                String::new()
            }
        ));
    }

    let mut msg = if removed.is_empty() {
        "无需清理（已是初始状态）".to_string()
    } else {
        format!("已清理: {}", removed.join(", "))
    };

    if !stopped.is_empty() {
        msg.push_str(&format!(" (已停止 {} 个进程)", stopped.len()));
    }

    Ok(msg)
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

fn last_clean_shutdown_marker(workspace_id: &str) -> PathBuf {
    workspace_dir(workspace_id)
        .join("data")
        .join("memory")
        .join(".last_clean_shutdown")
}

fn write_last_clean_shutdown_marker(workspace_id: &str, pid: u32, spawn_started_at: u64) {
    let marker = last_clean_shutdown_marker(workspace_id);
    if let Some(parent) = marker.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let payload = serde_json::json!({
        "ts": now_epoch_secs().saturating_mul(1000),
        "pid": pid,
        "version": env!("CARGO_PKG_VERSION"),
        "spawn_started_at": spawn_started_at,
    });
    let _ = fs::write(
        &marker,
        serde_json::to_string_pretty(&payload).unwrap_or_default(),
    );
}

// ── PID 文件 JSON 格式 ──
#[derive(Debug, Serialize, Deserialize, Clone)]
struct PidFileData {
    pid: u32,
    #[serde(default = "default_started_by")]
    started_by: String, // "tauri" | "external"
    #[serde(default)]
    started_at: u64, // unix epoch seconds
}

fn default_started_by() -> String {
    "tauri".to_string()
}

fn now_epoch_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn write_pid_file(workspace_id: &str, pid: u32, started_by: &str) -> Result<(), String> {
    let data = PidFileData {
        pid,
        started_by: started_by.to_string(),
        started_at: now_epoch_secs(),
    };
    let json = serde_json::to_string_pretty(&data).map_err(|e| format!("serialize pid: {e}"))?;
    let path = service_pid_file(workspace_id);
    fs::write(&path, json).map_err(|e| format!("write pid file: {e}"))?;
    Ok(())
}

/// 判断当前 workspace 的后端是否仍在"启动宽限期"内。
///
/// 宽限规则：
///   1. PID 文件存在，且 `started_at > 0`（旧格式/外部进程不进入宽限）
///   2. age < BACKEND_BOOT_GRACE_SEC
///   3. **PID 还在跑**：仍在宽限
///      **或** PID 已死但 age < BACKEND_BOOT_GRACE_PID_DEAD_SEC：依然算宽限
///         —— 这是为了对付 dual-venv hack 启动初期"Python 子进程
///         一闪而过又被自愈重 spawn"的窗口，避免心跳立刻误判 down
///         然后前端跟着闪一下"已停止"红条。
///
/// 用于压制 startup 期间的"backend down"误报和无意义的 auto-spawn，
/// 同时让前端 UI 在这段时间内持续显示"正在启动"而非"未启动"。
fn backend_in_boot_grace(workspace_id: &str) -> bool {
    let Some(data) = read_pid_file(workspace_id) else {
        return false;
    };
    if data.started_at == 0 {
        return false;
    }
    let age = now_epoch_secs().saturating_sub(data.started_at);
    if age >= BACKEND_BOOT_GRACE_SEC {
        return false;
    }
    if is_pid_running(data.pid) {
        return true;
    }
    // PID 已死，但还在 spawn-死亡-重 spawn 自愈窗口内 → 仍视作宽限，
    // 避免心跳跳过 boot-grace 直接报 lost。
    age < BACKEND_BOOT_GRACE_PID_DEAD_SEC
}

/// 暴露给前端的命令版本，便于 App.tsx 心跳直接判定"是否还在启动宽限"，
/// 而不必走 `is_backend_auto_starting`（后者复用同一逻辑但语义偏向"自启动"）。
#[tauri::command]
fn backend_in_boot_grace_cmd(workspace_id: String) -> bool {
    backend_in_boot_grace(&workspace_id)
}

/// 读取 PID 文件，兼容旧版纯数字格式
fn read_pid_file(workspace_id: &str) -> Option<PidFileData> {
    let path = service_pid_file(workspace_id);
    let content = fs::read_to_string(&path).ok()?;
    let trimmed = content.trim();
    // 尝试 JSON 格式
    if let Ok(data) = serde_json::from_str::<PidFileData>(trimmed) {
        if data.pid > 0 {
            return Some(data);
        }
    }
    // 向后兼容：纯数字格式
    if let Ok(pid) = trimmed.parse::<u32>() {
        if pid > 0 {
            return Some(PidFileData {
                pid,
                started_by: "tauri".to_string(),
                started_at: 0,
            });
        }
    }
    None
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ServicePidEntry {
    workspace_id: String,
    pid: u32,
    pid_file: String,
    #[serde(default)]
    started_by: String,
}

fn can_auto_stop_backend(workspace_id: &str, pid: u32) -> bool {
    if let Some(data) = read_pid_file(workspace_id) {
        if data.pid == pid {
            return data.started_by != "external";
        }
        // A different tracked process belongs to this workspace; do not kill a
        // random healthy backend discovered from the port.
        return false;
    }

    // Release builds still need to replace orphaned old packaged backends after
    // an app upgrade. In dev, an untracked backend is usually a manually started
    // `python -m openakita serve`, so keep it alive.
    !cfg!(debug_assertions)
}

fn list_service_pids() -> Vec<ServicePidEntry> {
    let mut out = Vec::new();
    let dir = run_dir();
    let Ok(rd) = fs::read_dir(&dir) else {
        return out;
    };
    for e in rd.flatten() {
        let p = e.path();
        let Some(name) = p.file_name().and_then(|s| s.to_str()) else {
            continue;
        };
        if !name.starts_with("openakita-") || !name.ends_with(".pid") {
            continue;
        }
        let ws = name
            .trim_start_matches("openakita-")
            .trim_end_matches(".pid")
            .to_string();
        if let Some(data) = read_pid_file(&ws) {
            out.push(ServicePidEntry {
                workspace_id: ws,
                pid: data.pid,
                pid_file: p.to_string_lossy().to_string(),
                started_by: data.started_by,
            });
        }
    }
    out
}

// ── 心跳文件管理 ──
// Python 后端每 10 秒写入心跳文件 {workspace}/data/backend.heartbeat
// Tauri 读取此文件判断后端真实健康状态。

#[derive(Debug, Serialize, Deserialize, Clone)]
struct HeartbeatData {
    pid: u32,
    timestamp: f64, // unix epoch seconds (float for sub-second precision)
    #[serde(default)]
    phase: String, // "starting" | "initializing" | "http_ready" | "starting_im" | "running" | "restarting" | "stopping"
    #[serde(default)]
    http_ready: bool, // HTTP API 是否就绪
    #[serde(default)]
    im_ready: bool, // IM / late-bound gateway 是否完成启动路径
    #[serde(default)]
    ready: bool, // 后端业务启动流程是否整体收敛
}

/// 心跳文件路径：{workspace_dir}/data/backend.heartbeat
fn service_heartbeat_file(workspace_id: &str) -> PathBuf {
    workspace_dir(workspace_id)
        .join("data")
        .join("backend.heartbeat")
}

/// 读取心跳文件
fn read_heartbeat_file(workspace_id: &str) -> Option<HeartbeatData> {
    let path = service_heartbeat_file(workspace_id);
    let content = fs::read_to_string(&path).ok()?;
    serde_json::from_str::<HeartbeatData>(content.trim()).ok()
}

/// 心跳是否过期。max_age_secs 为最大容忍的无心跳时间（秒）。
/// 返回 None 表示没有心跳文件（旧版后端或尚未启动），
/// 返回 Some(true) 表示心跳过期，Some(false) 表示心跳新鲜。
fn is_heartbeat_stale(workspace_id: &str, max_age_secs: u64) -> Option<bool> {
    let hb = read_heartbeat_file(workspace_id)?;
    let now = now_epoch_secs() as f64;
    let age = now - hb.timestamp;
    Some(age > max_age_secs as f64)
}

/// 删除心跳文件（进程清理时调用）
fn remove_heartbeat_file(workspace_id: &str) {
    let _ = fs::remove_file(service_heartbeat_file(workspace_id));
}

/// 检测指定端口是否可用（未被占用）。
/// 尝试绑定端口，成功则可用，失败则被占用。
fn check_port_available(port: u16) -> bool {
    std::net::TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// 等待端口释放，最多等 timeout_ms 毫秒。
/// 返回 true 表示端口已释放。
fn wait_for_port_free(port: u16, timeout_ms: u64) -> bool {
    let start = std::time::Instant::now();
    let timeout = std::time::Duration::from_millis(timeout_ms);
    while start.elapsed() < timeout {
        if check_port_available(port) {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    false
}

fn is_backend_http_healthy(port: Option<u16>) -> bool {
    let effective_port = port.unwrap_or(18900);
    reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .no_proxy()
        .build()
        .ok()
        .and_then(|client| {
            client
                .get(format!("http://127.0.0.1:{}/api/health", effective_port))
                .send()
                .ok()
        })
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

fn should_cleanup_stale_heartbeat(heartbeat_stale: Option<bool>, http_healthy: bool) -> bool {
    matches!(heartbeat_stale, Some(true)) && !http_healthy
}

/// 尝试通过 HTTP API 优雅关闭 Python 服务（POST /api/shutdown），
/// 然后等待进程退出。如果 API 调用失败或超时则回退到 kill。
/// `port`: 可选端口号，默认 18900
fn graceful_stop_pid(pid: u32, port: Option<u16>) -> Result<bool, String> {
    if !is_pid_running(pid) {
        return Ok(true);
    }

    let effective_port = port.unwrap_or(18900);
    // 第一步：尝试通过 HTTP API 触发优雅关闭
    let api_ok = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .no_proxy()
        .build()
        .ok()
        .and_then(|client| {
            client
                .post(format!("http://127.0.0.1:{}/api/shutdown", effective_port))
                .send()
                .ok()
        })
        .map(|r| r.status().is_success())
        .unwrap_or(false);

    if api_ok {
        // API 调用成功，给 Python 最多 10 秒优雅退出时间
        for _ in 0..50 {
            if !is_pid_running(pid) {
                return Ok(true);
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    }

    // 第二步：进程仍然存活，强制 kill
    if is_pid_running(pid) {
        kill_pid(pid)?;
        // 等待最多 3s 确认退出
        for _ in 0..15 {
            if !is_pid_running(pid) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    }

    if is_pid_running(pid) {
        Err(format!(
            "pid {} still running after graceful + forced stop",
            pid
        ))
    } else {
        Ok(false)
    }
}

fn stop_service_pid_entry(ent: &ServicePidEntry, port: Option<u16>) -> Result<(), String> {
    if is_pid_running(ent.pid) {
        graceful_stop_pid(ent.pid, port)?;
    }
    let _ = fs::remove_file(PathBuf::from(&ent.pid_file));
    remove_heartbeat_file(&ent.workspace_id);
    Ok(())
}

/// 启动锁文件路径
fn service_lock_file(workspace_id: &str) -> PathBuf {
    run_dir().join(format!("openakita-{}.lock", workspace_id))
}

/// 尝试获取启动锁（原子创建文件），成功返回 true
fn try_acquire_start_lock(workspace_id: &str) -> bool {
    let lock_path = service_lock_file(workspace_id);
    let _ = fs::create_dir_all(lock_path.parent().unwrap_or(Path::new(".")));
    // OpenOptions::create_new ensures atomicity
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock_path)
        .is_ok()
}

fn release_start_lock(workspace_id: &str) {
    let _ = fs::remove_file(service_lock_file(workspace_id));
}

/// 获取进程创建时间（Unix epoch 秒）
#[cfg(windows)]
fn get_process_create_time(pid: u32) -> Option<u64> {
    #[repr(C)]
    #[derive(Copy, Clone)]
    struct FILETIME {
        dw_low_date_time: u32,
        dw_high_date_time: u32,
    }
    extern "system" {
        fn GetProcessTimes(
            hProcess: *mut std::ffi::c_void,
            lpCreationTime: *mut FILETIME,
            lpExitTime: *mut FILETIME,
            lpKernelTime: *mut FILETIME,
            lpUserTime: *mut FILETIME,
        ) -> i32;
    }
    unsafe {
        let handle = win::OpenProcess(win::PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let mut creation: FILETIME = std::mem::zeroed();
        let mut exit: FILETIME = std::mem::zeroed();
        let mut kernel: FILETIME = std::mem::zeroed();
        let mut user: FILETIME = std::mem::zeroed();
        let ok = GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user);
        win::CloseHandle(handle);
        if ok == 0 {
            return None;
        }
        // Convert FILETIME (100-ns intervals since 1601-01-01) to Unix epoch seconds
        let ft = ((creation.dw_high_date_time as u64) << 32) | (creation.dw_low_date_time as u64);
        // 116444736000000000 = 100-ns intervals between 1601-01-01 and 1970-01-01
        let unix_100ns = ft.checked_sub(116444736000000000)?;
        Some(unix_100ns / 10_000_000)
    }
}

#[cfg(target_os = "linux")]
fn get_process_create_time(pid: u32) -> Option<u64> {
    let stat = fs::read_to_string(format!("/proc/{}/stat", pid)).ok()?;
    let after_comm = stat.rfind(')')? + 2;
    if after_comm >= stat.len() {
        return None;
    }
    let fields: Vec<&str> = stat[after_comm..].split_whitespace().collect();
    let starttime = fields.get(19)?.parse::<u64>().ok()?;
    let clk_tck: u64 = 100;
    let uptime_str = fs::read_to_string("/proc/uptime").ok()?;
    let uptime_secs: f64 = uptime_str.split_whitespace().next()?.parse().ok()?;
    let now = now_epoch_secs();
    let boot_time = now.saturating_sub(uptime_secs as u64);
    Some(boot_time + starttime / clk_tck)
}

#[cfg(target_os = "macos")]
fn get_process_create_time(pid: u32) -> Option<u64> {
    let output = Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "lstart="])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let lstart = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if lstart.is_empty() {
        return None;
    }
    // lstart format: "Wed Jan  1 08:00:00 2025"
    // Parse with chrono-less manual approach: use `date -jf` on macOS
    let date_out = Command::new("date")
        .args(["-jf", "%a %b %d %T %Y", &lstart, "+%s"])
        .output()
        .ok()?;
    let epoch_str = String::from_utf8_lossy(&date_out.stdout).trim().to_string();
    epoch_str.parse::<u64>().ok()
}

/// 验证 PID 文件中的 started_at 是否与实际进程创建时间匹配（允许 5 秒误差）
fn is_pid_file_valid(data: &PidFileData) -> bool {
    if !is_pid_running(data.pid) {
        return false;
    }
    // 旧格式没有 started_at：不能仅靠 PID 存活来判断——
    // Windows 上 PID 会被复用，必须验证进程身份。
    if data.started_at == 0 {
        return is_openakita_process(data.pid);
    }
    if let Some(actual_create) = get_process_create_time(data.pid) {
        let diff = if data.started_at > actual_create {
            data.started_at - actual_create
        } else {
            actual_create - data.started_at
        };
        if diff > 5 {
            // 时间不匹配——PID 被复用了，再验证一下进程身份
            return is_openakita_process(data.pid);
        }
        true // 时间匹配
    } else {
        // 无法获取进程创建时间，退回到进程身份验证
        is_openakita_process(data.pid)
    }
}

/// 从 workspace .env 文件读取 API_PORT
fn read_workspace_api_port(workspace_id: &str) -> Option<u16> {
    let env_path = workspace_dir(workspace_id).join(".env");
    let content = read_text_lossy(&env_path);
    for line in content.lines() {
        let t = line.trim();
        if let Some(val) = t.strip_prefix("API_PORT=") {
            return val.trim().parse::<u16>().ok();
        }
    }
    None
}

// --- Windows 原生 API FFI（进程检测/杀死/枚举，不依赖 cmd/tasklist/taskkill，中文 Windows 零编码问题）---
#[cfg(windows)]
#[allow(non_snake_case, dead_code)]
mod win {
    extern "system" {
        pub fn OpenProcess(
            dwDesiredAccess: u32,
            bInheritHandle: i32,
            dwProcessId: u32,
        ) -> *mut std::ffi::c_void;
        pub fn TerminateProcess(hProcess: *mut std::ffi::c_void, uExitCode: u32) -> i32;
        pub fn CloseHandle(hObject: *mut std::ffi::c_void) -> i32;
        pub fn CreateToolhelp32Snapshot(dwFlags: u32, th32ProcessID: u32) -> *mut std::ffi::c_void;
        pub fn Process32FirstW(hSnapshot: *mut std::ffi::c_void, lppe: *mut PROCESSENTRY32W)
            -> i32;
        pub fn Process32NextW(hSnapshot: *mut std::ffi::c_void, lppe: *mut PROCESSENTRY32W) -> i32;
    }
    pub const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    pub const PROCESS_TERMINATE: u32 = 0x0001;
    pub const TH32CS_SNAPPROCESS: u32 = 0x00000002;
    pub const INVALID_HANDLE_VALUE: *mut std::ffi::c_void = -1_isize as *mut std::ffi::c_void;

    #[repr(C)]
    pub struct PROCESSENTRY32W {
        pub dw_size: u32,
        pub cnt_usage: u32,
        pub th32_process_id: u32,
        pub th32_default_heap_id: usize,
        pub th32_module_id: u32,
        pub cnt_threads: u32,
        pub th32_parent_process_id: u32,
        pub pc_pri_class_base: i32,
        pub dw_flags: u32,
        pub sz_exe_file: [u16; 260],
    }
}

fn is_pid_running(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(windows)]
    {
        // 直接用 Windows API 检查——最可靠，无 GBK 编码问题。
        let handle = unsafe { win::OpenProcess(win::PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if handle.is_null() {
            return false;
        }
        unsafe {
            win::CloseHandle(handle);
        }
        return true;
    }
    #[cfg(not(windows))]
    {
        let status = Command::new("kill").args(["-0", &pid.to_string()]).status();
        status.map(|s| s.success()).unwrap_or(false)
    }
}

fn kill_pid(pid: u32) -> Result<(), String> {
    if pid == 0 {
        return Ok(());
    }
    #[cfg(windows)]
    {
        // 直接用 TerminateProcess API 杀进程，不走 cmd/taskkill。
        let handle = unsafe { win::OpenProcess(win::PROCESS_TERMINATE, 0, pid) };
        if handle.is_null() {
            if !is_pid_running(pid) {
                return Ok(());
            }
            return Err(format!(
                "\u{65e0}\u{6cd5}\u{6253}\u{5f00}\u{8fdb}\u{7a0b}\u{ff08}pid={}\u{ff09}\u{ff0c}\u{6743}\u{9650}\u{4e0d}\u{8db3}\u{6216}\u{8fdb}\u{7a0b}\u{4e0d}\u{5b58}\u{5728}",
                pid
            ));
        }
        let ok = unsafe { win::TerminateProcess(handle, 1) };
        unsafe {
            win::CloseHandle(handle);
        }
        if ok == 0 {
            if !is_pid_running(pid) {
                return Ok(());
            }
            return Err(format!(
                "TerminateProcess \u{5931}\u{8d25}\u{ff08}pid={}\u{ff09}",
                pid
            ));
        }
        return Ok(());
    }
    #[cfg(not(windows))]
    {
        let pid_str = pid.to_string();

        // SIGTERM: 允许进程优雅退出
        let _ = Command::new("kill").args(["-TERM", &pid_str]).status();

        // 等待最多 2 秒确认退出
        for _ in 0..10 {
            if !is_pid_running(pid) {
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }

        // SIGKILL: 进程未响应 SIGTERM（可能事件循环卡死），强制终止
        let status = Command::new("kill")
            .args(["-KILL", &pid_str])
            .status()
            .map_err(|e| format!("kill -KILL failed: {e}"))?;
        if !status.success() && is_pid_running(pid) {
            return Err(format!("kill -KILL failed: {status}"));
        }
        Ok(())
    }
}

/// 检查指定 PID 是否属于 OpenAkita 后端进程（python/openakita-server）。
/// 用于判断 PID 文件是否有效——避免 Windows PID 复用导致的误判。
fn is_openakita_process(pid: u32) -> bool {
    if pid == 0 || !is_pid_running(pid) {
        return false;
    }
    #[cfg(windows)]
    {
        // Step 1: 用 Toolhelp32 快速检查进程名
        let snap = unsafe { win::CreateToolhelp32Snapshot(win::TH32CS_SNAPPROCESS, 0) };
        if snap == win::INVALID_HANDLE_VALUE || snap.is_null() {
            return false;
        }
        let mut pe: win::PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        pe.dw_size = std::mem::size_of::<win::PROCESSENTRY32W>() as u32;

        let mut exe_name = String::new();
        if unsafe { win::Process32FirstW(snap, &mut pe) } != 0 {
            loop {
                if pe.th32_process_id == pid {
                    exe_name = String::from_utf16_lossy(
                        &pe.sz_exe_file
                            [..pe.sz_exe_file.iter().position(|&c| c == 0).unwrap_or(260)],
                    )
                    .to_ascii_lowercase();
                    break;
                }
                if unsafe { win::Process32NextW(snap, &mut pe) } == 0 {
                    break;
                }
            }
        }
        unsafe {
            win::CloseHandle(snap);
        }

        // 进程名包含 python 或 openakita-server → 可能是后端
        if exe_name.contains("openakita-server") {
            return true;
        }
        if !exe_name.contains("python") {
            return false; // 既不是 python 也不是 openakita-server，肯定不是后端
        }

        // Step 2: python 进程需进一步检查命令行是否包含 openakita
        let mut c = Command::new("powershell");
        c.args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            &format!(
                "(Get-CimInstance Win32_Process -Filter 'ProcessId={}').CommandLine",
                pid
            ),
        ]);
        apply_no_window(&mut c);
        if let Ok(out) = c.output() {
            let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
            return s.contains("openakita");
        }
        false
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(cmdline) = fs::read_to_string(format!("/proc/{}/cmdline", pid)) {
            return cmdline.to_lowercase().contains("openakita");
        }
        let output = Command::new("ps")
            .args(["-p", &pid.to_string(), "-o", "args="])
            .output();
        if let Ok(out) = output {
            let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
            return s.contains("openakita");
        }
        false
    }
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("ps")
            .args(["-p", &pid.to_string(), "-o", "args="])
            .output();
        if let Ok(out) = output {
            let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
            return s.contains("openakita");
        }
        false
    }
}

/// 扫描并杀死所有进程名为 python/pythonw 且命令行包含 "openakita" 和 "serve" 的进程。
/// 用于托盘退出时兜底清理孤儿进程（PID 文件可能已被删除但进程仍存活）。
/// 返回被杀掉的 PID 列表。
fn kill_openakita_orphans() -> Vec<u32> {
    let mut killed = Vec::new();
    #[cfg(windows)]
    {
        // Step 1: 用 Toolhelp32 枚举所有进程，找到进程名含 python 的
        let snap = unsafe { win::CreateToolhelp32Snapshot(win::TH32CS_SNAPPROCESS, 0) };
        if snap == win::INVALID_HANDLE_VALUE || snap.is_null() {
            return killed;
        }
        let mut pe: win::PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        pe.dw_size = std::mem::size_of::<win::PROCESSENTRY32W>() as u32;

        let mut python_pids: Vec<u32> = Vec::new();
        let mut bundled_pids: Vec<u32> = Vec::new();

        if unsafe { win::Process32FirstW(snap, &mut pe) } != 0 {
            loop {
                let name = String::from_utf16_lossy(
                    &pe.sz_exe_file[..pe.sz_exe_file.iter().position(|&c| c == 0).unwrap_or(260)],
                );
                let name_lower = name.to_ascii_lowercase();
                if name_lower.contains("python") {
                    python_pids.push(pe.th32_process_id);
                }
                // PyInstaller 打包后端进程名为 openakita-server.exe
                if name_lower.contains("openakita-server") {
                    bundled_pids.push(pe.th32_process_id);
                }
                if unsafe { win::Process32NextW(snap, &mut pe) } == 0 {
                    break;
                }
            }
        }
        unsafe {
            win::CloseHandle(snap);
        }

        // Step 1.5: 直接 kill 孤立的 openakita-server.exe (PyInstaller bundled backend)
        for ppid in bundled_pids {
            if is_pid_running(ppid) {
                let _ = kill_pid(ppid);
                killed.push(ppid);
            }
        }

        // Step 2: 对每个 python 进程查命令行，判断是否是 openakita serve 进程
        // 使用 PowerShell Get-CimInstance 替代已废弃的 wmic（Windows 11 已移除 wmic）
        for ppid in python_pids {
            let mut c = Command::new("powershell");
            c.args([
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                &format!(
                    "(Get-CimInstance Win32_Process -Filter 'ProcessId={}').CommandLine",
                    ppid
                ),
            ]);
            apply_no_window(&mut c);
            if let Ok(out) = c.output() {
                let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
                // 精确匹配模块调用签名
                if s.contains("openakita.main") && (s.contains(" serve") || s.ends_with("serve")) {
                    if is_pid_running(ppid) {
                        let _ = kill_pid(ppid);
                        killed.push(ppid);
                    }
                }
            }
        }
    }
    #[cfg(not(windows))]
    {
        // 搜索 openakita.main serve (venv 模式) 和 openakita-server (PyInstaller 模式)
        let patterns = [
            "ps aux | grep '[o]penakita\\.main.*serve' | awk '{print $2}'",
            "ps aux | grep '[o]penakita-server' | awk '{print $2}'",
        ];
        let mut pids_to_kill: Vec<u32> = Vec::new();
        for pattern in &patterns {
            if let Ok(out) = Command::new("sh").args(["-c", pattern]).output() {
                let stdout = String::from_utf8_lossy(&out.stdout);
                for line in stdout.lines() {
                    if let Ok(pid) = line.trim().parse::<u32>() {
                        if is_pid_running(pid)
                            && !killed.contains(&pid)
                            && !pids_to_kill.contains(&pid)
                        {
                            pids_to_kill.push(pid);
                        }
                    }
                }
            }
        }

        // SIGTERM
        for &pid in &pids_to_kill {
            let _ = Command::new("kill")
                .args(["-TERM", &pid.to_string()])
                .status();
        }

        if !pids_to_kill.is_empty() {
            std::thread::sleep(std::time::Duration::from_millis(1500));
        }

        // SIGKILL 升级：对 SIGTERM 后仍存活的进程强制终止
        for pid in pids_to_kill {
            if is_pid_running(pid) {
                let _ = Command::new("kill")
                    .args(["-KILL", &pid.to_string()])
                    .status();
            }
            killed.push(pid);
        }
    }
    killed
}

/// 扫描所有进程名含 python 且命令行包含 "openakita" 和 "serve" 的进程。
/// 返回 OpenAkitaProcess 列表，供前端多进程检测使用。
#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct OpenAkitaProcess {
    pid: u32,
    cmd: String,
}

#[tauri::command]
fn openakita_list_processes() -> Vec<OpenAkitaProcess> {
    let mut out = Vec::new();
    #[cfg(windows)]
    {
        // Step 1: 枚举所有进程，找到进程名含 python 的 PID
        let snap = unsafe { win::CreateToolhelp32Snapshot(win::TH32CS_SNAPPROCESS, 0) };
        if snap == win::INVALID_HANDLE_VALUE || snap.is_null() {
            return out;
        }
        let mut pe: win::PROCESSENTRY32W = unsafe { std::mem::zeroed() };
        pe.dw_size = std::mem::size_of::<win::PROCESSENTRY32W>() as u32;

        let mut python_pids: Vec<(u32, u32)> = Vec::new();

        if unsafe { win::Process32FirstW(snap, &mut pe) } != 0 {
            loop {
                let name = String::from_utf16_lossy(
                    &pe.sz_exe_file[..pe.sz_exe_file.iter().position(|&c| c == 0).unwrap_or(260)],
                );
                let name_lower = name.to_ascii_lowercase();
                if name_lower.contains("python") {
                    python_pids.push((pe.th32_process_id, pe.th32_parent_process_id));
                }
                if unsafe { win::Process32NextW(snap, &mut pe) } == 0 {
                    break;
                }
            }
        }
        unsafe {
            win::CloseHandle(snap);
        }

        let mut matched: Vec<(u32, u32, String)> = Vec::new();

        // Step 2: 对每个 python 进程查命令行
        for (ppid, parent_pid) in python_pids {
            let mut c = Command::new("powershell");
            c.args([
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                &format!(
                    "(Get-CimInstance Win32_Process -Filter 'ProcessId={}').CommandLine",
                    ppid
                ),
            ]);
            apply_no_window(&mut c);
            if let Ok(cmd_out) = c.output() {
                let s = String::from_utf8_lossy(&cmd_out.stdout).to_string();
                let s_lower = s.to_lowercase();
                // 精确匹配模块调用签名，避免 venv 路径中 .openakita 误报
                if s_lower.contains("openakita.main")
                    && (s_lower.contains(" serve") || s_lower.ends_with("serve"))
                {
                    if is_pid_running(ppid) {
                        matched.push((ppid, parent_pid, s.trim().to_string()));
                    }
                }
            }
        }

        // uv-created venv python.exe can be a launcher parent that delegates to
        // the managed CPython executable. Count only the leaf backend process.
        for (pid, _parent, cmd) in &matched {
            let has_matched_child = matched.iter().any(|(_, parent, _)| parent == pid);
            if !has_matched_child {
                out.push(OpenAkitaProcess {
                    pid: *pid,
                    cmd: cmd.clone(),
                });
            }
        }
    }
    #[cfg(not(windows))]
    {
        // ps aux | grep openakita.main.*serve  —— 精确匹配模块调用
        if let Ok(ps_out) = Command::new("sh")
            .args(["-c", "ps aux | grep '[o]penakita\\.main.*serve'"])
            .output()
        {
            let stdout = String::from_utf8_lossy(&ps_out.stdout);
            for line in stdout.lines() {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 {
                    if let Ok(pid) = parts[1].parse::<u32>() {
                        if is_pid_running(pid) {
                            out.push(OpenAkitaProcess {
                                pid,
                                cmd: parts[10..].join(" "),
                            });
                        }
                    }
                }
            }
        }
    }
    out
}

/// 停止所有检测到的 OpenAkita serve 进程。
/// 返回被停止的 PID 列表。
#[tauri::command]
fn openakita_stop_all_processes() -> Vec<u32> {
    let mut stopped = Vec::new();

    // 第 1 层：按 PID 文件逐一停止
    let entries = list_service_pids();
    for ent in &entries {
        if is_pid_running(ent.pid) {
            let port = read_workspace_api_port(&ent.workspace_id);
            let _ = stop_service_pid_entry(ent, port);
            stopped.push(ent.pid);
        }
    }

    // 第 2 层：兜底扫描所有命令行含 openakita serve 的 python 进程并杀掉
    let orphans = kill_openakita_orphans();
    for pid in orphans {
        if !stopped.contains(&pid) {
            stopped.push(pid);
        }
    }

    stopped
}

fn read_state_file() -> AppStateFile {
    let p = state_file_path();
    if let Ok(content) = fs::read_to_string(&p) {
        if let Ok(state) = serde_json::from_str::<AppStateFile>(&content) {
            if !state.workspaces.is_empty() {
                return state;
            }
            // workspaces is empty — could be a truncated/corrupted write.
            // Fall through to disk recovery, but preserve other fields.
            let recovered = rebuild_state_from_disk(Some(state));
            if !recovered.workspaces.is_empty() {
                eprintln!(
                    "state.json had empty workspaces but {} workspace dir(s) found on disk — recovered",
                    recovered.workspaces.len()
                );
                let _ = write_state_file(&recovered);
            }
            return recovered;
        }
        // JSON parse failed (truncated / corrupted file)
        eprintln!("warning: state.json is corrupted, attempting disk recovery");
    }
    // File missing or unreadable — try to recover from workspaces/ directory
    let recovered = rebuild_state_from_disk(None);
    if !recovered.workspaces.is_empty() {
        eprintln!(
            "state.json missing but {} workspace dir(s) found on disk — recovered",
            recovered.workspaces.len()
        );
        let _ = write_state_file(&recovered);
    }
    recovered
}

/// Scan workspaces/ directory to rebuild state when state.json is missing or corrupted.
/// A subdirectory is considered a valid workspace only if it contains a `data/` child.
fn rebuild_state_from_disk(partial: Option<AppStateFile>) -> AppStateFile {
    let mut state = partial.unwrap_or_default();
    let ws_dir = workspaces_dir();
    let Ok(entries) = fs::read_dir(&ws_dir) else {
        return state;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        if !path.join("data").exists() {
            continue;
        }
        let id = entry.file_name().to_string_lossy().to_string();
        if state.workspaces.iter().any(|w| w.id == id) {
            continue;
        }
        state.workspaces.push(WorkspaceMeta {
            id: id.clone(),
            name: id.clone(),
        });
    }
    if state.current_workspace_id.is_none() && !state.workspaces.is_empty() {
        // Prefer "default" if it exists, otherwise pick the first one
        let preferred = state
            .workspaces
            .iter()
            .find(|w| w.id == "default")
            .unwrap_or(&state.workspaces[0]);
        state.current_workspace_id = Some(preferred.id.clone());
    }
    state
}

fn write_state_file(state: &AppStateFile) -> Result<(), String> {
    let p = state_file_path();
    if let Some(parent) = p.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create_dir_all failed: {e}"))?;
    }
    let data = serde_json::to_string_pretty(state).map_err(|e| format!("serialize failed: {e}"))?;
    atomic_write_with_backup(&p, data.as_bytes())
}

/// Crash-safe file write: backup existing file, write to .tmp, then atomic rename.
/// On Windows rename failure (file locked), retries up to 3 times before falling back
/// to direct write.
fn atomic_write_with_backup(path: &Path, content: &[u8]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create parent dir failed: {e}"))?;
    }
    if path.exists() {
        let bak = path.with_extension("json.bak");
        let _ = fs::copy(path, &bak);
    }
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, content).map_err(|e| format!("write tmp failed: {e}"))?;
    for attempt in 0..3u64 {
        match fs::rename(&tmp, path) {
            Ok(()) => return Ok(()),
            Err(e) => {
                if attempt < 2 {
                    std::thread::sleep(std::time::Duration::from_millis(100 * (attempt + 1)));
                } else {
                    eprintln!(
                        "atomic rename failed after 3 retries ({e}), falling back to direct write"
                    );
                    if let Err(e2) = fs::write(path, content) {
                        let _ = fs::remove_file(&tmp);
                        return Err(format!("write failed: {e2}"));
                    }
                    let _ = fs::remove_file(&tmp);
                    return Ok(());
                }
            }
        }
    }
    Ok(())
}

fn ensure_workspace_scaffold(dir: &Path) -> Result<(), String> {
    fs::create_dir_all(dir.join("data")).map_err(|e| format!("create data dir failed: {e}"))?;
    fs::create_dir_all(dir.join("identity"))
        .map_err(|e| format!("create identity dir failed: {e}"))?;

    // Only ASCII comments in .env to avoid encoding issues on non-UTF-8 Windows systems.
    let env_path = dir.join(".env");
    if !env_path.exists() {
        let content = [
            "# OpenAkita workspace environment (managed by Setup Center)",
            "#",
            "# - Only keys you explicitly set in Setup Center are written here.",
            "# - Clearing a value removes the key from this file.",
            "# - For the full template, see examples/.env.example",
            "",
        ]
        .join("\n");
        fs::write(&env_path, content).map_err(|e| format!("write .env failed: {e}"))?;
    }

    // identity 文件：从仓库模板复制生成，保证字段完整性与一致性（而不是随意占位）
    const DEFAULT_SOUL: &str = include_str!("../../../../identity/SOUL.md.example");
    const DEFAULT_AGENT: &str = include_str!("../../../../identity/AGENT.md.example");
    const DEFAULT_USER: &str = include_str!("../../../../identity/USER.md.example");
    const DEFAULT_MEMORY: &str = include_str!("../../../../identity/MEMORY.md.example");

    let soul = dir.join("identity").join("SOUL.md");
    if !soul.exists() {
        fs::write(&soul, DEFAULT_SOUL)
            .map_err(|e| format!("write identity/SOUL.md failed: {e}"))?;
    }
    let agent_md = dir.join("identity").join("AGENT.md");
    if !agent_md.exists() {
        fs::write(&agent_md, DEFAULT_AGENT)
            .map_err(|e| format!("write identity/AGENT.md failed: {e}"))?;
    }
    let user_md = dir.join("identity").join("USER.md");
    if !user_md.exists() {
        fs::write(&user_md, DEFAULT_USER)
            .map_err(|e| format!("write identity/USER.md failed: {e}"))?;
    }
    let memory_md = dir.join("identity").join("MEMORY.md");
    if !memory_md.exists() {
        fs::write(&memory_md, DEFAULT_MEMORY)
            .map_err(|e| format!("write identity/MEMORY.md failed: {e}"))?;
    }

    // 人格预设文件：8 个标配预设 + user_custom 模板
    // 从仓库 identity/personas/ 目录嵌入，确保新工作区开箱即用
    {
        const PERSONA_DEFAULT: &str = include_str!("../../../../identity/personas/default.md");
        const PERSONA_BUSINESS: &str = include_str!("../../../../identity/personas/business.md");
        const PERSONA_TECH_EXPERT: &str =
            include_str!("../../../../identity/personas/tech_expert.md");
        const PERSONA_BUTLER: &str = include_str!("../../../../identity/personas/butler.md");
        const PERSONA_GIRLFRIEND: &str =
            include_str!("../../../../identity/personas/girlfriend.md");
        const PERSONA_BOYFRIEND: &str = include_str!("../../../../identity/personas/boyfriend.md");
        const PERSONA_FAMILY: &str = include_str!("../../../../identity/personas/family.md");
        const PERSONA_JARVIS: &str = include_str!("../../../../identity/personas/jarvis.md");
        const PERSONA_USER_CUSTOM: &str =
            include_str!("../../../../identity/personas/user_custom.md.example");

        let personas_dir = dir.join("identity").join("personas");
        fs::create_dir_all(&personas_dir)
            .map_err(|e| format!("create identity/personas dir failed: {e}"))?;

        let presets: &[(&str, &str)] = &[
            ("default.md", PERSONA_DEFAULT),
            ("business.md", PERSONA_BUSINESS),
            ("tech_expert.md", PERSONA_TECH_EXPERT),
            ("butler.md", PERSONA_BUTLER),
            ("girlfriend.md", PERSONA_GIRLFRIEND),
            ("boyfriend.md", PERSONA_BOYFRIEND),
            ("family.md", PERSONA_FAMILY),
            ("jarvis.md", PERSONA_JARVIS),
            ("user_custom.md", PERSONA_USER_CUSTOM),
        ];

        for (filename, content) in presets {
            let path = personas_dir.join(filename);
            if !path.exists() {
                fs::write(&path, content)
                    .map_err(|e| format!("write identity/personas/{filename} failed: {e}"))?;
            }
        }
    }

    // policies 文件：运行时策略规则，builder.py 会读取
    {
        let prompts_dir = dir.join("identity").join("prompts");
        fs::create_dir_all(&prompts_dir)
            .map_err(|e| format!("create identity/prompts dir failed: {e}"))?;
        let policies = prompts_dir.join("policies.md");
        if !policies.exists() {
            const DEFAULT_POLICIES: &str = include_str!("../../../../identity/prompts/policies.md");
            fs::write(&policies, DEFAULT_POLICIES)
                .map_err(|e| format!("write identity/prompts/policies.md failed: {e}"))?;
        }
    }

    // runtime 黄金文件：手写的行为规范精简版，避免首次启动时等 LLM 编译
    // SOUL.md 已改为全文注入，不再需要 soul.summary.md
    {
        let runtime_dir = dir.join("identity").join("runtime");
        fs::create_dir_all(&runtime_dir)
            .map_err(|e| format!("create identity/runtime dir failed: {e}"))?;

        const AGENT_CORE: &str = include_str!("../../../../identity/runtime/agent.core.md");
        const AGENT_TOOLING: &str = include_str!("../../../../identity/runtime/agent.tooling.md");

        let golden_files: &[(&str, &str)] = &[
            ("agent.core.md", AGENT_CORE),
            ("agent.tooling.md", AGENT_TOOLING),
        ];
        for (filename, content) in golden_files {
            let path = runtime_dir.join(filename);
            if !path.exists() {
                fs::write(&path, content)
                    .map_err(|e| format!("write identity/runtime/{filename} failed: {e}"))?;
            }
        }
    }

    // 默认 llm_endpoints.json：用仓库内的 data/llm_endpoints.json.example 作为初始模板
    let llm = dir.join("data").join("llm_endpoints.json");
    if !llm.exists() {
        const DEFAULT_LLM_ENDPOINTS: &str =
            include_str!("../../../../data/llm_endpoints.json.example");
        fs::write(&llm, DEFAULT_LLM_ENDPOINTS)
            .map_err(|e| format!("write data/llm_endpoints.json failed: {e}"))?;
    }

    Ok(())
}

#[tauri::command]
fn list_workspaces() -> Result<Vec<WorkspaceSummary>, String> {
    let root = openakita_root_dir();
    fs::create_dir_all(&root).map_err(|e| format!("create root failed: {e}"))?;
    fs::create_dir_all(workspaces_dir())
        .map_err(|e| format!("create workspaces dir failed: {e}"))?;

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

fn validate_workspace_id(id: &str) -> Result<(), String> {
    let id = id.trim();
    if id.is_empty() {
        return Err("workspace id is empty".into());
    }
    if id.len() > 64 {
        return Err("workspace id too long (max 64 chars)".into());
    }
    if !id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err("workspace id can only contain a-z, A-Z, 0-9, _ and -".into());
    }
    if !id.chars().any(|c| c.is_ascii_alphanumeric()) {
        return Err("workspace id must contain at least one letter or digit".into());
    }
    const RESERVED: &[&str] = &[
        "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8",
        "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    ];
    if RESERVED.contains(&id.to_ascii_lowercase().as_str()) {
        return Err("workspace id conflicts with a reserved system name".into());
    }
    Ok(())
}

#[tauri::command]
fn create_workspace(
    id: String,
    name: String,
    set_current: bool,
) -> Result<WorkspaceSummary, String> {
    validate_workspace_id(&id)?;
    if name.trim().is_empty() {
        return Err("workspace name is empty".into());
    }

    fs::create_dir_all(workspaces_dir())
        .map_err(|e| format!("create workspaces dir failed: {e}"))?;

    let _lock = STATE_FILE_LOCK
        .lock()
        .map_err(|e| format!("state lock failed: {e}"))?;
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
    let _lock = STATE_FILE_LOCK
        .lock()
        .map_err(|e| format!("state lock failed: {e}"))?;
    let mut state = read_state_file();
    if !state.workspaces.iter().any(|w| w.id == id) {
        return Err("workspace id not found".into());
    }
    let dir = workspace_dir(&id);
    if !dir.exists() {
        eprintln!(
            "workspace dir missing, recreating scaffold: {}",
            dir.display()
        );
        ensure_workspace_scaffold(&dir)?;
    }
    state.current_workspace_id = Some(id);
    write_state_file(&state)?;
    Ok(())
}

/// 读取安装包内 bundled 后端版本号（不启动 Python，直接读文件）。
fn bundled_backend_version() -> Option<String> {
    let version_file = bundled_backend_dir()
        .join("_internal")
        .join("openakita")
        .join("_bundled_version.txt");
    fs::read_to_string(&version_file)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

/// 启动时后端版本对账的结果。
///
/// 三种状态覆盖所有情况，调用方据此决定是否启动新后端，
/// 且只需一次 HTTP 健康检查，避免重复请求。
enum VersionCheckResult {
    /// 端口上没有后端在运行。
    NotRunning,
    /// 后端正在运行且版本可接受（匹配、dev 版本、或重启无法改善）。
    RunningOk,
    /// 旧版后端已被终止，需要启动新后端。
    Upgraded,
}

fn runtime_wheel_hash_matches_bootstrap() -> bool {
    let bootstrap_hash = match read_bootstrap_manifest() {
        Ok(b) => b.wheel.sha256,
        Err(e) => {
            log_to_file(&format!(
                "[version_check] bootstrap manifest unavailable: {e}"
            ));
            return false;
        }
    };
    if bootstrap_hash.trim().is_empty() {
        return true;
    }
    read_runtime_manifest()
        .map(|m| {
            // legacy 模式下 dual-venv 没创建成功，wheel hash 字段写不写都
            // 不代表"app-venv 包含 bootstrap wheel 的代码"。但如果重启后
            // 端只会再走一遍 dual-venv 创建（大概率仍然失败）然后再 fallback
            // 到同一个 PyInstaller bundled 后端，重启没有任何意义，反而把
            // 唯一能用的后端杀掉。所以 legacy 模式直接视为 hash 匹配。
            if m.legacy_mode {
                return true;
            }
            m.wheel_hash == bootstrap_hash
        })
        .unwrap_or(false)
}

fn stop_backend_for_restart(workspace_id: &str, pid: u32, port: u16) -> VersionCheckResult {
    if !can_auto_stop_backend(workspace_id, pid) {
        log_to_file(&format!(
            "[version_check] keeping externally owned backend pid={} for ws={}",
            pid, workspace_id
        ));
        return VersionCheckResult::RunningOk;
    }

    if let Err(e) = graceful_stop_pid(pid, Some(port)) {
        eprintln!(
            "Failed to stop old backend (pid={}): {}. Keeping current backend.",
            pid, e
        );
        return VersionCheckResult::RunningOk;
    }

    // 清理被终止进程对应的 PID 文件
    for ent in list_service_pids() {
        if let Some(data) = read_pid_file(&ent.workspace_id) {
            if data.pid == pid || !is_pid_running(data.pid) {
                let _ = fs::remove_file(service_pid_file(&ent.workspace_id));
                remove_heartbeat_file(&ent.workspace_id);
            }
        }
    }

    eprintln!(
        "Old backend (pid={}) stopped. New backend will be started automatically.",
        pid
    );
    VersionCheckResult::Upgraded
}

fn healthy_backend_pid(port: u16) -> Option<u32> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .no_proxy()
        .build()
        .ok()?;
    let resp = client
        .get(format!("http://127.0.0.1:{}/api/health", port))
        .send()
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let json: serde_json::Value = resp.json().ok()?;
    if json.get("service").and_then(|v| v.as_str()) != Some("openakita") {
        return None;
    }
    json.get("pid")
        .and_then(|v| v.as_u64())
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| is_pid_running(*pid))
}

/// DMG 覆盖安装后版本对账：检查运行中后端的版本，必要时替换。
///
/// macOS 上通过 DMG 拖拽覆盖安装后，旧的 openakita-server 进程可能仍在端口上
/// 服务。新版 app 启动时必须检测版本不匹配并主动替换，否则会一直使用旧后端。
///
/// 此函数合并了「是否有后端在运行」和「版本是否匹配」两个检查，
/// 只发一次 HTTP 请求，避免 setup 阶段重复探测。
fn startup_version_check(workspace_id: &str, app_version: &str, port: u16) -> VersionCheckResult {
    let client = match reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .no_proxy()
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            log_to_file(&format!("[version_check] client build failed: {e}"));
            return VersionCheckResult::NotRunning;
        }
    };

    let resp = match client
        .get(format!("http://127.0.0.1:{}/api/health", port))
        .send()
    {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => {
            log_to_file(&format!(
                "[version_check] health check non-success: {}",
                r.status()
            ));
            return VersionCheckResult::NotRunning;
        }
        Err(e) => {
            log_to_file(&format!("[version_check] health check failed: {e}"));
            return VersionCheckResult::NotRunning;
        }
    };

    let json: serde_json::Value = match resp.json() {
        Ok(v) => v,
        Err(_) => return VersionCheckResult::RunningOk, // 响应成功但 JSON 解析失败，保守处理
    };

    let backend_version = json
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim_start_matches('v');
    let desktop_version = app_version.trim_start_matches('v');

    // 版本无法判断或 dev 后端 → 保守保持现有后端。
    if backend_version.is_empty() || backend_version == "0.0.0-dev" {
        return VersionCheckResult::RunningOk;
    }

    if backend_version == desktop_version {
        if runtime_wheel_hash_matches_bootstrap() {
            return VersionCheckResult::RunningOk;
        }
        let pid = match json.get("pid").and_then(|v| v.as_u64()).map(|p| p as u32) {
            Some(p) => p,
            None => {
                eprintln!("Runtime wheel changed but backend PID is unavailable; keeping current backend.");
                return VersionCheckResult::RunningOk;
            }
        };
        eprintln!(
            "Runtime wheel changed for version {}. Stopping backend to refresh app-venv...",
            desktop_version
        );
        return stop_backend_for_restart(workspace_id, pid, port);
    }

    // 核心防护：检查安装包内 bundled 后端版本。
    // 如果 bundled 版本和运行中版本相同，重启只会拉起同样版本的后端，
    // 杀死毫无意义且可能影响用户正在使用的服务。
    let bundled_v = bundled_backend_version()
        .unwrap_or_default()
        .trim_start_matches('v')
        .to_string();
    if !bundled_v.is_empty() && bundled_v == backend_version {
        eprintln!(
            "Version mismatch: backend={} desktop={}, but bundled backend is also {}. \
             Restart would not help — keeping current backend.",
            backend_version, desktop_version, bundled_v
        );
        return VersionCheckResult::RunningOk;
    }

    eprintln!(
        "Version mismatch: running={} bundled={} desktop={}. Stopping old backend for upgrade...",
        backend_version,
        if bundled_v.is_empty() {
            "?"
        } else {
            &bundled_v
        },
        desktop_version
    );

    // graceful_stop_pid 内部已包含：POST /api/shutdown → 等待 5s → force kill → 等待 2s
    // 无需手动再发 shutdown 或 sleep。
    let pid = match json.get("pid").and_then(|v| v.as_u64()).map(|p| p as u32) {
        Some(p) => p,
        None => {
            eprintln!(
                "Cannot determine backend PID from health response; keeping current backend."
            );
            return VersionCheckResult::RunningOk;
        }
    };

    stop_backend_for_restart(workspace_id, pid, port)
}

/// 启动对账：清理残留锁文件和已死的 PID 文件
fn startup_reconcile() {
    let dir = run_dir();
    if !dir.exists() {
        return;
    }

    // 1. 清理残留 .lock 文件（上次崩溃可能遗留）
    if let Ok(rd) = fs::read_dir(&dir) {
        for e in rd.flatten() {
            let p = e.path();
            if let Some(ext) = p.extension() {
                if ext == "lock" {
                    let _ = fs::remove_file(&p);
                }
            }
        }
    }

    // 2. 扫描 PID 文件，清理已死进程的 stale 条目
    let entries = list_service_pids();
    for ent in &entries {
        if let Some(data) = read_pid_file(&ent.workspace_id) {
            if !is_pid_file_valid(&data) {
                // 进程已死或 PID 被复用，清理 PID 文件和心跳文件
                let _ = fs::remove_file(service_pid_file(&ent.workspace_id));
                remove_heartbeat_file(&ent.workspace_id);
            } else if let Some(true) = is_heartbeat_stale(&ent.workspace_id, 60) {
                // PID 文件有效但心跳超时。先用 HTTP health 复核，避免因心跳文件
                // 写入异常误杀仍可响应的后端进程。
                let port = read_workspace_api_port(&ent.workspace_id);
                if should_cleanup_stale_heartbeat(Some(true), is_backend_http_healthy(port)) {
                    let _ = graceful_stop_pid(data.pid, port);
                    let _ = fs::remove_file(service_pid_file(&ent.workspace_id));
                    remove_heartbeat_file(&ent.workspace_id);
                }
            }
        }
    }
}

/// Append a crash entry to `~/.openakita/logs/crash.log`.
///
/// When `show_dialog` is true, a native `MessageBoxW` (Windows) is displayed
/// so the user gets feedback instead of a silent flash-exit.
///
/// Returns the path to the crash log (best-effort; may not exist if writing
/// failed, e.g. due to permissions).
fn write_crash_log(message: &str, show_dialog: bool) -> PathBuf {
    let log_dir = setup_logs_dir();
    let _ = fs::create_dir_all(&log_dir);
    let crash_path = log_dir.join("crash.log");

    let timestamp = {
        let dur = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default();
        dur.as_secs()
    };
    let exe = std::env::current_exe()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| "<unknown>".to_string());
    let cwd = std::env::current_dir()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| "<unknown>".to_string());
    let home = home_dir()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|| "<None>".to_string());
    let entry = format!("[{timestamp}] exe={exe} cwd={cwd} home={home}\n{message}\n---\n");

    let _ = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&crash_path)
        .and_then(|mut f| f.write_all(entry.as_bytes()));

    if show_dialog {
        #[cfg(windows)]
        {
            use std::ffi::OsStr;
            use std::iter::once;
            use std::os::windows::ffi::OsStrExt;

            extern "system" {
                fn MessageBoxW(
                    hwnd: *mut std::ffi::c_void,
                    text: *const u16,
                    caption: *const u16,
                    typ: u32,
                ) -> i32;
            }

            fn to_wide(s: &str) -> Vec<u16> {
                OsStr::new(s).encode_wide().chain(once(0)).collect()
            }

            let body = format!(
                "OpenAkita Desktop 启动失败 (startup failed)\n\n\
                 {message}\n\n\
                 崩溃日志已写入 (crash log): {}\n\
                 请将此日志发送给开发者以帮助诊断问题。",
                crash_path.display()
            );
            let caption = "OpenAkita – Crash";
            let wb = to_wide(&body);
            let wc = to_wide(caption);
            unsafe {
                MessageBoxW(std::ptr::null_mut(), wb.as_ptr(), wc.as_ptr(), 0x10);
            }
        }
    }

    crash_path
}

fn show_main_window(app: &tauri::AppHandle, reason: &str, open_status: bool) {
    let app_handle = app.clone();
    let reason = reason.to_string();

    // Windows WebView2/tao can crash if show/focus runs re-entrantly from a
    // single-instance or tray callback while the hidden window state is changing.
    #[cfg(target_os = "windows")]
    {
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(120));
            show_main_window_now(&app_handle, &reason, open_status);
        });
    }

    #[cfg(not(target_os = "windows"))]
    {
        show_main_window_now(&app_handle, &reason, open_status);
    }
}

fn show_main_window_now(app: &tauri::AppHandle, reason: &str, open_status: bool) {
    if let Some(w) = app.get_webview_window("main") {
        if let Err(e) = w.show() {
            log_to_file(&format!("[window] show failed ({reason}): {e}"));
        }
        let _ = w.unminimize();
        if let Err(e) = w.set_focus() {
            log_to_file(&format!("[window] focus failed ({reason}): {e}"));
        }
    } else {
        log_to_file(&format!("[window] main window not found ({reason})"));
    }
    if open_status {
        let _ = app.emit("open_status", serde_json::json!({}));
    }
}

fn main() {
    // 自愈接力进程的启动时序兜底：
    // panic hook 在 spawn 新实例时旧进程还没真正退出，
    // tauri-plugin-single-instance 会让新实例的 callback 在旧进程里触发
    // 然后新实例直接退出。这里在新实例最早期 sleep 让旧进程的崩溃流程
    // 完整执行（写 crash.log + 释放 single-instance 锁），然后再继续启动。
    if std::env::args().any(|a| a == "--auto-restarted") {
        std::thread::sleep(std::time::Duration::from_millis(1500));
    }

    // Global panic hook: capture panics to crash.log + show dialog.
    // 进程级自愈：检测 tao Windows 事件循环的已知 panic
    // ("cannot move state from Destroyed"，对应上游 tao#1180)，落
    // restart.marker 并立刻 spawn 一份自身进程接力。新进程靠
    // tauri-plugin-single-instance 保证只有一个活实例，旧进程随后崩溃。
    // 上游修复后此自愈可拆除（见 Cargo.toml TODO 标记）。
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let msg = format!("PANIC: {info}");
        eprintln!("{msg}");
        write_crash_log(&msg, true);
        let panic_str = info.to_string();
        if panic_str.contains("cannot move state from Destroyed")
            || panic_str.contains("tao") && panic_str.contains("Destroyed")
        {
            try_self_heal_relaunch(&panic_str);
        }
        default_hook(info);
    }));

    // Ensure localhost is always excluded from proxy resolution.
    //
    // macOS: Clash/V2Ray set system proxy via Network Preferences. hyper-util
    //   links `system-configuration` and reads these settings, so ALL reqwest
    //   clients (including Tauri HTTP plugin's) would route 127.0.0.1 through
    //   the proxy — which fails because the backend only listens locally.
    // Windows: similar issue with system proxy via Internet Options.
    //
    // We APPEND to any existing NO_PROXY/no_proxy rather than overwrite, so
    // user-defined exclusions (e.g. *.corp.com) are preserved.
    // Both cases are set because different libraries check different variants.
    {
        const LOCALS: &str = "localhost,127.0.0.1";
        for key in ["NO_PROXY", "no_proxy"] {
            let cur = std::env::var(key).unwrap_or_default();
            if !cur.contains("127.0.0.1") {
                let val = if cur.is_empty() {
                    LOCALS.to_string()
                } else {
                    format!("{cur},{LOCALS}")
                };
                std::env::set_var(key, &val);
            }
        }
    }

    // Workaround: NVIDIA drivers on Linux can cause a blank WebKitGTK window
    // due to DMA-BUF renderer incompatibility. Disable it preemptively.
    #[cfg(target_os = "linux")]
    {
        if std::env::var("WEBKIT_DISABLE_DMABUF_RENDERER").is_err() {
            std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
    }

    let app = match tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 第二个实例启动时，聚焦已有窗口并退出自身
            show_main_window(app, "single-instance", false);
        }))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--background"]),
        ))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let result: Result<(), Box<dyn std::error::Error>> = (|| {
            // ── NSIS 安装后以当前用户执行清理（解决“以管理员运行安装程序”时清错目录的问题） ──
            let args: Vec<String> = std::env::args().collect();
            if let Some(pos) = args.iter().position(|a| a == "--clean-env") {
                let mut clean_venv = false;
                let mut clean_runtime = false;
                for a in args.iter().skip(pos + 1) {
                    if a == "venv" {
                        clean_venv = true;
                    }
                    if a == "runtime" {
                        clean_runtime = true;
                    }
                    if a.starts_with("--") {
                        break;
                    }
                }
                if clean_venv || clean_runtime {
                    match cleanup_old_environment(clean_venv, clean_runtime) {
                        Ok(msg) => eprintln!("Clean env: {}", msg),
                        Err(e) => eprintln!("Clean env failed: {}", e),
                    }
                    std::process::exit(0);
                }
            }

            // ── 启动对账：清理残留 .lock 和 stale PID 文件 ──
            startup_reconcile();

            // ── 配置文件版本迁移 ──
            let root = openakita_root_dir();
            let state_path = state_file_path();
            if let Err(e) = migrations::run_migrations(&state_path, &root) {
                eprintln!("Config migration error: {e}");
            }

            setup_tray(app)?;

            // ── 自启自修复：防止注册表条目意外丢失（上游 Issue #771） ──
            // 如果用户之前开启了自启（记录在 state file），但注册表条目被意外移除，
            // 则自动重新注册，确保下次开机仍能自启。
            #[cfg(desktop)]
            {
                let repair_state = read_state_file();
                if repair_state.auto_start_backend.unwrap_or(false) {
                    let mgr = app.autolaunch();
                    match mgr.is_enabled() {
                        Ok(false) => {
                            eprintln!("Auto-start self-repair: registry entry missing, re-enabling...");
                            if let Err(e) = mgr.enable() {
                                eprintln!("Auto-start self-repair failed: {e}");
                            }
                        }
                        Err(e) => eprintln!("Auto-start check failed: {e}"),
                        _ => {} // 已启用，无需修复
                    }
                }
            }

            // ── 首次运行检测 (NSIS 安装后自动启动时传入 --first-run) ──
            let is_first_run_arg = std::env::args().any(|a| a == "--first-run");
            let launch_mode = if is_first_run_arg { "first-run" } else { "normal" };
            app.emit("app-launch-mode", launch_mode).ok();
            let app_version = app.package_info().version.to_string();

            if let Some(payload) = detect_previous_frontend_crash() {
                log_to_file("[self-heal] stale frontend session marker recovered");
                set_startup_recovery_notice(payload);
            }
            record_frontend_session_marker(&app_version);

            // ── 自愈恢复：检查上次崩溃留下的 restart.marker ──
            // 由 panic hook 在命中 tao#1180 特征时写入；这里读出后立刻删除
            // 避免重复触发，并向前端 emit 事件，前端可据此恢复上次工作区/视图
            // 或弹温和提示告诉用户"刚刚已自动恢复"。
            let marker_path = restart_marker_path();
            if marker_path.exists() {
                if let Ok(content) = fs::read_to_string(&marker_path) {
                    log_to_file(&format!(
                        "[self-heal] restart.marker recovered: {}",
                        content.lines().next().unwrap_or("")
                    ));
                    let payload: serde_json::Value =
                        serde_json::from_str(&content).unwrap_or(serde_json::json!({}));
                    set_startup_recovery_notice(payload.clone());
                    app.emit("app-restarted-from-crash", payload).ok();
                }
                let _ = fs::remove_file(&marker_path);
            }

            // 后台启动时：不弹出主窗口，只保留托盘/菜单栏常驻
            let is_background = std::env::args().any(|a| a == "--background");
            if is_background {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }

            // ── 自动拉起后端 ──
            // 如果有已配置的工作区且后端未在运行，则自动启动后端。
            // 前端通过 is_backend_auto_starting 查询此状态，
            // 在启动期间显示提示并禁用启动/重启按钮。
            //
            // startup_version_check 合并了「健康检查」和「版本对账」两步：
            //   - NotRunning  → 端口无响应，需要启动
            //   - RunningOk   → 后端在运行且版本可接受
            //   - Upgraded    → 旧版后端已被终止，需要启动新版
            let state = read_state_file();
            if let Some(ref ws_id) = state.current_workspace_id {
                let port = read_workspace_api_port(ws_id).unwrap_or(18900);
                if cfg!(debug_assertions) {
                    if let Some(pid) = healthy_backend_pid(port) {
                        let should_adopt = read_pid_file(ws_id)
                            .map(|data| !is_pid_file_valid(&data))
                            .unwrap_or(true);
                        if should_adopt {
                            match write_pid_file(ws_id, pid, "external") {
                                Ok(()) => log_to_file(&format!(
                                    "[auto-start] adopted dev backend pid={} for ws={}",
                                    pid, ws_id
                                )),
                                Err(e) => log_to_file(&format!(
                                    "[auto-start] failed to adopt dev backend pid={}: {}",
                                    pid, e
                                )),
                            }
                        }
                    }
                }

                let check_result = startup_version_check(ws_id, &app_version, port);
                let need_start = !matches!(check_result, VersionCheckResult::RunningOk);
                log_to_file(&format!(
                    "[auto-start] app_version={}, ws_id={}, port={}, need_start={}",
                    app_version, ws_id, port, need_start
                ));
                if need_start {
                    AUTO_START_IN_PROGRESS.store(true, Ordering::SeqCst);
                    AUTO_START_STARTED_AT_MS.store(now_ms(), Ordering::SeqCst);
                    let venv_dir = openakita_root_dir().join("venv").to_string_lossy().to_string();
                    let ws_clone = ws_id.clone();
                    std::thread::spawn(move || {
                        match openakita_service_start_impl(venv_dir.clone(), ws_clone.clone()) {
                            Ok(status) => {
                                log_to_file(&format!(
                                    "[auto-start] success: running={}, pid={:?}",
                                    status.running, status.pid
                                ));
                            }
                            Err(e) => {
                                log_to_file(&format!("[auto-start] FAILED: {}", e));
                            }
                        }
                        AUTO_START_IN_PROGRESS.store(false, Ordering::SeqCst);
                        AUTO_START_STARTED_AT_MS.store(0, Ordering::SeqCst);
                    });
                }
            } else {
                log_to_file("[auto-start] skipped: no current_workspace_id in state");
            }

            // PR-F1: 启动常驻 5s 心跳。后端崩溃时连续 3 次失败（≈ 15s）就尝试
            // 自动重启 + 向前端 emit `backend:lost` / `backend:back`。
            // 旧实现仅依赖 startup_version_check 一次性探测，进程死后用户要等
            // 60+ 分钟才能在 autostart.log 里看到下一次探测。
            {
                let app_handle = app.handle().clone();
                let app_version_for_hb = app_version.clone();
                std::thread::spawn(move || {
                    let mut consecutive_failures: u32 = 0;
                    let mut last_status_was_healthy: Option<bool> = None;
                    let mut last_starting_log_at: u64 = 0;
                    loop {
                        std::thread::sleep(std::time::Duration::from_secs(5));
                        let state_snap = read_state_file();
                        let ws_id = match state_snap.current_workspace_id {
                            Some(s) => s,
                            None => continue,
                        };
                        let port = read_workspace_api_port(&ws_id).unwrap_or(18900);
                        let healthy = is_backend_http_healthy(Some(port));
                        if healthy {
                            consecutive_failures = 0;
                            if last_status_was_healthy != Some(true) {
                                let _ = app_handle.emit(
                                    "backend:status",
                                    serde_json::json!({"healthy": true, "port": port}),
                                );
                                if last_status_was_healthy == Some(false) {
                                    let _ = app_handle.emit(
                                        "backend:back",
                                        serde_json::json!({"port": port}),
                                    );
                                }
                                last_status_was_healthy = Some(true);
                            }
                            continue;
                        }

                        // ── 启动宽限期：PID 还在 spawn 后的 BACKEND_BOOT_GRACE_SEC 秒内 ──
                        // 后端 dual-venv hack cold start 实测需要 90~120 秒（Python
                        // import + 122 个 skills + Memory + IM channels + uvicorn bind）。
                        // 心跳 5s × 3 次失败 = 15s 就报 down 完全不合理：那时后端
                        // 才刚开始加载 skills，HTTP 还没绑定端口。
                        // 在宽限期内：
                        //   - emit `backend:status starting=true` 让 UI 显示"正在启动"
                        //   - 不发 backend:lost，不触发 auto-spawn
                        //   - 不累加 consecutive_failures
                        if backend_in_boot_grace(&ws_id) {
                            let now = now_epoch_secs();
                            // 最多每 30 秒打一条 log + emit，避免刷屏
                            if now.saturating_sub(last_starting_log_at) >= 30 {
                                log_to_file(&format!(
                                    "[heartbeat] backend in boot-grace (port={}) — skipping down/spawn",
                                    port
                                ));
                                let _ = app_handle.emit(
                                    "backend:status",
                                    serde_json::json!({"healthy": false, "starting": true, "port": port}),
                                );
                                last_starting_log_at = now;
                            }
                            consecutive_failures = 0;
                            continue;
                        }

                        consecutive_failures = consecutive_failures.saturating_add(1);
                        if consecutive_failures < 3 {
                            continue;
                        }
                        if last_status_was_healthy != Some(false) {
                            let _ = app_handle.emit(
                                "backend:lost",
                                serde_json::json!({
                                    "port": port,
                                    "consecutive_failures": consecutive_failures,
                                }),
                            );
                            log_to_file(&format!(
                                "[heartbeat] backend down for {}s, attempting auto spawn (port={})",
                                consecutive_failures * 5,
                                port,
                            ));
                            last_status_was_healthy = Some(false);
                        }
                        if AUTO_START_IN_PROGRESS.load(Ordering::SeqCst) {
                            continue;
                        }
                        let check_result = startup_version_check(&ws_id, &app_version_for_hb, port);
                        let need_start = !matches!(check_result, VersionCheckResult::RunningOk);
                        if !need_start {
                            // 端口又被别人占了或 health 临时抖动 — 重置计数
                            consecutive_failures = 0;
                            continue;
                        }
                        AUTO_START_IN_PROGRESS.store(true, Ordering::SeqCst);
                        AUTO_START_STARTED_AT_MS.store(now_ms(), Ordering::SeqCst);
                        let venv_dir = openakita_root_dir()
                            .join("venv")
                            .to_string_lossy()
                            .to_string();
                        let ws_clone = ws_id.clone();
                        match openakita_service_start_impl(venv_dir, ws_clone) {
                            Ok(status) => log_to_file(&format!(
                                "[heartbeat] auto-spawn returned: running={}, pid={:?} (note: pid may be existing process if dedupe-skip)",
                                status.running, status.pid
                            )),
                            Err(e) => log_to_file(&format!("[heartbeat] auto-spawn FAILED: {}", e)),
                        }
                        AUTO_START_IN_PROGRESS.store(false, Ordering::SeqCst);
                        AUTO_START_STARTED_AT_MS.store(0, Ordering::SeqCst);
                        consecutive_failures = 0;
                    }
                });
            }

            Ok(())
            })();

            if let Err(ref e) = result {
                write_crash_log(&format!("Setup failed: {e}"), false);
            }
            result
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
            toggle_pet_window,
            get_root_dir_info,
            set_custom_root_dir,
            preflight_migrate_root,
            list_workspaces,
            create_workspace,
            set_current_workspace,
            get_current_workspace_id,
            workspace_read_file,
            workspace_write_file,
            workspace_update_env,
            export_workspace_backup,
            import_workspace_backup,
            detect_python,
            diagnose_python_env,
            export_python_diagnostic_report,
            check_python_for_pip,
            openakita_runtime_last_error,
            openakita_desktop_session_token,
            openakita_open_runtime_root,
            install_bundled_python,
            create_venv,
            pip_install,
            pip_uninstall,
            autostart_is_enabled,
            autostart_set_enabled,
            openakita_service_status,
            openakita_service_start,
            openakita_service_stop,
            openakita_service_log,
            openakita_check_pid_alive,
            set_tray_backend_status,
            is_backend_auto_starting,
            backend_in_boot_grace_cmd,
            repair_runtime_env,
            get_auto_start_backend,
            set_auto_start_backend,
            get_auto_update,
            set_auto_update,
            openakita_list_skills,
            openakita_list_providers,
            openakita_list_models,
            openakita_version,
            openakita_health_check_endpoint,
            openakita_health_check_im,
            openakita_ensure_channel_deps,
            openakita_install_skill,
            openakita_uninstall_skill,
            openakita_list_marketplace,
            openakita_get_skill_config,
            openakita_wecom_onboard_start,
            openakita_wecom_onboard_poll,
            openakita_feishu_onboard_start,
            openakita_feishu_onboard_poll,
            openakita_feishu_validate,
            openakita_qqbot_onboard_start,
            openakita_qqbot_onboard_poll,
            openakita_qqbot_onboard_create,
            openakita_qqbot_onboard_poll_and_create,
            openakita_qqbot_validate,
            openakita_wechat_onboard_start,
            openakita_wechat_onboard_poll,
            fetch_pypi_versions,
            http_get_json,
            http_proxy_request,
            backend_fetch,
            read_file_base64,
            download_file,
            copy_file_to_downloads,
            show_item_in_folder,
            open_file_with_default,
            export_env_backup,
            export_diagnostic_bundle,
            build_feedback_zip,
            upload_feedback_to_cloud,
            save_pending_feedback,
            get_feedback_config_offline,
            open_external_url,
            openakita_list_processes,
            openakita_stop_all_processes,
            is_first_run,
            check_environment,
            check_backend_availability,
            cleanup_old_environment,
            factory_reset,
            start_onboarding_log,
            append_onboarding_log,
            append_onboarding_log_lines,
            append_frontend_log,
            take_startup_recovery_notice,
            save_log_export,
            register_cli,
            unregister_cli,
            get_cli_status,
            start_dragging
        ])
        .build(tauri::generate_context!())
    {
        Ok(a) => a,
        Err(e) => {
            let msg = format!("Tauri build failed: {e}");
            eprintln!("{msg}");
            write_crash_log(&msg, true);
            std::process::exit(1);
        }
    };

    app.run(|_app_handle, event| {
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::Reopen {
            has_visible_windows,
            ..
        } = &event
        {
            if !has_visible_windows {
                if let Some(win) = _app_handle.get_webview_window("main") {
                    let _ = win.show();
                    let _ = win.set_focus();
                }
            }
        }
        if let tauri::RunEvent::Exit = event {
            clear_frontend_session_marker();
            // Safety-net: clean up backend processes on ANY exit path
            // (SIGTERM, system shutdown, unexpected termination, etc.)
            // Idempotent — harmless if tray-quit already stopped everything.
            //
            // 直接 kill 进程而非走 HTTP /api/shutdown：
            //   1. 退出时要尽快完成清理，避免 Finder/macOS 等待超时后强杀本进程
            //      导致后端沦为孤儿进程。
            //   2. Python 后端已注册 SIGTERM handler，收到信号即可优雅关闭。
            //   3. HTTP API 可能因代理、端口状态等原因不可达，增加不确定性。
            // 应用退出兜底：杀掉所有 OpenAkita 后端进程（含 external 归属）。
            // 启动阶段不动手动后端的保护由 startup_version_check 实现；
            // 应用退出意味着用户已经主动结束会话，留任何后端都没用。
            let entries = list_service_pids();
            for ent in &entries {
                if is_pid_running(ent.pid) {
                    let _ = kill_pid(ent.pid);
                }
                let _ = fs::remove_file(std::path::PathBuf::from(&ent.pid_file));
                remove_heartbeat_file(&ent.workspace_id);
            }
            kill_openakita_orphans();
        }
    });
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ServiceStatus {
    running: bool,
    pid: Option<u32>,
    pid_file: String,
    /// 后端心跳阶段："starting" | "initializing" | "http_ready" | "starting_im" | "running" | "restarting" | "stopping" | ""
    #[serde(default)]
    heartbeat_phase: String,
    /// HTTP API 是否就绪
    #[serde(default)]
    heartbeat_http_ready: bool,
    /// IM / late-bound gateway 启动路径是否已收敛
    #[serde(default)]
    heartbeat_im_ready: bool,
    /// 后端业务启动流程是否整体收敛
    #[serde(default)]
    heartbeat_ready: bool,
    /// 心跳是否过期（超过 30 秒没更新）。None = 没有心跳文件（旧版后端）
    #[serde(default)]
    heartbeat_stale: Option<bool>,
    /// 距上次心跳的秒数。None = 没有心跳文件
    #[serde(default)]
    heartbeat_age_secs: Option<f64>,
}

/// 构造 ServiceStatus，自动填充心跳信息
fn build_service_status(
    workspace_id: &str,
    running: bool,
    pid: Option<u32>,
    pid_file_str: String,
) -> ServiceStatus {
    let (heartbeat_phase, heartbeat_http_ready, heartbeat_im_ready, heartbeat_ready, heartbeat_stale, heartbeat_age_secs) =
        if let Some(hb) = read_heartbeat_file(workspace_id) {
            let now = now_epoch_secs() as f64;
            let age = now - hb.timestamp;
            let stale = age > 30.0; // 超过 30 秒无心跳视为过期
            (hb.phase, hb.http_ready, hb.im_ready, hb.ready, Some(stale), Some(age))
        } else {
            (String::new(), false, false, false, None, None)
        };
    ServiceStatus {
        running,
        pid,
        pid_file: pid_file_str,
        heartbeat_phase,
        heartbeat_http_ready,
        heartbeat_im_ready,
        heartbeat_ready,
        heartbeat_stale,
        heartbeat_age_secs,
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ServiceLogChunk {
    path: String,
    content: String,
    truncated: bool,
}

#[tauri::command]
fn openakita_service_status(workspace_id: String) -> Result<ServiceStatus, String> {
    let pid_file = service_pid_file(&workspace_id);
    let pf = pid_file.to_string_lossy().to_string();

    // ── 1. 优先用 MANAGED_CHILD（精确 try_wait）──
    {
        let mut guard = MANAGED_CHILD.lock().unwrap();
        if let Some(ref mut mp) = *guard {
            if mp.workspace_id == workspace_id {
                match mp.child.try_wait() {
                    Ok(None) => {
                        return Ok(build_service_status(&workspace_id, true, Some(mp.pid), pf));
                    }
                    _ => {
                        // 进程已退出，清理 handle、PID 文件和心跳文件
                        *guard = None;
                        let _ = fs::remove_file(&pid_file);
                        remove_heartbeat_file(&workspace_id);
                        return Ok(build_service_status(&workspace_id, false, None, pf));
                    }
                }
            }
        }
    }

    // ── 2. 回退到 PID 文件 ──
    if let Some(data) = read_pid_file(&workspace_id) {
        if is_pid_file_valid(&data) {
            // PID 文件有效，但如果心跳超过 60 秒没更新，进程可能卡死
            // 此时仍报告 running（让前端根据心跳状态决定是否提示用户）
            return Ok(build_service_status(
                &workspace_id,
                true,
                Some(data.pid),
                pf,
            ));
        } else {
            // Stale PID，清理 PID 文件和心跳文件
            let _ = fs::remove_file(&pid_file);
            remove_heartbeat_file(&workspace_id);
        }
    }
    Ok(build_service_status(&workspace_id, false, None, pf))
}

/// 检查进程是否仍在运行（供前端心跳二次确认用）。
/// 除了检查 PID 存活，还验证进程身份和心跳文件。
/// 如果心跳超过 60 秒没更新且 HTTP 不可达，自动清理进程和 PID 文件。
#[tauri::command]
fn openakita_check_pid_alive(workspace_id: String) -> Result<bool, String> {
    // 优先 MANAGED_CHILD（由 Tauri 直接管理的子进程，不需要额外校验身份）
    {
        let mut guard = MANAGED_CHILD.lock().unwrap();
        if let Some(ref mut mp) = *guard {
            if mp.workspace_id == workspace_id {
                let alive = mp.child.try_wait().ok().flatten().is_none();
                if !alive {
                    // 进程已退出，清理
                    *guard = None;
                    let _ = fs::remove_file(service_pid_file(&workspace_id));
                    remove_heartbeat_file(&workspace_id);
                }
                return Ok(alive);
            }
        }
    }
    // 回退到 PID 文件：检查 PID 存活 + 验证进程身份
    if let Some(data) = read_pid_file(&workspace_id) {
        if !is_pid_running(data.pid) {
            // 进程已死，清理 stale PID 文件和心跳文件
            let _ = fs::remove_file(service_pid_file(&workspace_id));
            remove_heartbeat_file(&workspace_id);
            return Ok(false);
        }
        // PID 存活，但需验证是否真的是 OpenAkita 进程
        if !is_openakita_process(data.pid) {
            // PID 被其他进程复用了，清理 stale PID 文件和心跳文件
            let _ = fs::remove_file(service_pid_file(&workspace_id));
            remove_heartbeat_file(&workspace_id);
            return Ok(false);
        }
        // 进程身份已确认，但检查心跳是否严重过期（> 60 秒）
        // 心跳过期意味着进程虽然存活但可能已经卡死
        if let Some(true) = is_heartbeat_stale(&workspace_id, 60) {
            // 心跳严重过期时先复核 HTTP health；只在 API 也不可达时才清理，
            // 防止心跳文件写入异常造成“后端仍可用却被误杀”。
            let port = read_workspace_api_port(&workspace_id);
            if should_cleanup_stale_heartbeat(Some(true), is_backend_http_healthy(port)) {
                let _ = graceful_stop_pid(data.pid, port);
                let _ = fs::remove_file(service_pid_file(&workspace_id));
                remove_heartbeat_file(&workspace_id);
                return Ok(false);
            }
        }
        return Ok(true);
    }
    Ok(false)
}

#[cfg(windows)]
fn apply_no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    // CREATE_NO_WINDOW: avoid flashing a black console window for spawned commands.
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn apply_no_window(_cmd: &mut Command) {}

/// 清除可能干扰 Python 运行环境的外部环境变量。
///
/// 常见场景：用户安装了 Anaconda/Miniconda、系统设置了 PYTHONPATH 等，
/// 这些变量会在 Python 启动时被注入到 sys.path 最前面，覆盖 PyInstaller
/// 内置的包（如 pydantic_core），导致 C 扩展不兼容而崩溃。
///
/// 同时清除 pip 行为干扰变量（PIP_TARGET/PIP_PREFIX 等），
/// 避免 pip install --target 时被用户配置覆盖。
fn strip_harmful_python_env(cmd: &mut Command) {
    // Python 运行时变量
    cmd.env_remove("PYTHONPATH");
    cmd.env_remove("PYTHONHOME");
    cmd.env_remove("PYTHONSTARTUP");
    // 虚拟环境 / Conda 变量
    cmd.env_remove("VIRTUAL_ENV");
    cmd.env_remove("CONDA_PREFIX");
    cmd.env_remove("CONDA_DEFAULT_ENV");
    cmd.env_remove("CONDA_SHLVL");
    cmd.env_remove("CONDA_PYTHON_EXE");
    // pip 行为干扰变量
    cmd.env_remove("PIP_TARGET");
    cmd.env_remove("PIP_PREFIX");
    cmd.env_remove("PIP_USER");
    cmd.env_remove("PIP_INDEX_URL");
    cmd.env_remove("PIP_REQUIRE_VIRTUALENV");
}

fn strip_harmful_toolchain_env(cmd: &mut Command) {
    // SSL and proxy-like CA overrides from Conda/Homebrew often point at files
    // outside the managed runtime. Core/bootstrap will inject its own CA bundle.
    cmd.env_remove("SSL_CERT_FILE");
    cmd.env_remove("SSL_CERT_DIR");
    cmd.env_remove("REQUESTS_CA_BUNDLE");
    cmd.env_remove("CURL_CA_BUNDLE");
    cmd.env_remove("NODE_EXTRA_CA_CERTS");
    cmd.env_remove("NODE_TLS_REJECT_UNAUTHORIZED");
    cmd.env_remove("DYLD_LIBRARY_PATH");
    cmd.env_remove("DYLD_INSERT_LIBRARIES");
    cmd.env_remove("DYLD_FRAMEWORK_PATH");
    cmd.env_remove("DYLD_FALLBACK_LIBRARY_PATH");

    // Node/npm/corepack writes must not fall into a user global prefix/cache
    // when OpenAkita is creating or repairing its own toolchain.
    cmd.env_remove("NODE_PATH");
    cmd.env_remove("NPM_CONFIG_PREFIX");
    cmd.env_remove("NPM_CONFIG_CACHE");
    cmd.env_remove("npm_config_prefix");
    cmd.env_remove("npm_config_cache");
    cmd.env_remove("COREPACK_HOME");

    if cfg!(target_os = "linux") {
        cmd.env_remove("LD_LIBRARY_PATH");
        cmd.env_remove("LD_PRELOAD");
        cmd.env_remove("LIBRARY_PATH");
        cmd.env_remove("PKG_CONFIG_PATH");
    }
}

/// Configure environment for invoking `_internal/python{3}` directly.
///
/// PyInstaller packs `encodings`, `codecs` and other bootstrap modules into
/// `base_library.zip`.  When calling the raw Python binary we must make sure
/// it can find them.
///
/// Platform-specific behaviour:
/// - **Windows**: `._pth` files (created by `ensure_bundled_pth_file`) are the
///   primary mechanism; `PYTHONHOME` + `PYTHONPATH` serve as fallback.
/// - **macOS / Linux**: `._pth` files are Windows-only and ignored.
///   Setting `PYTHONHOME` to `_internal/` fails because Python expects
///   `PYTHONHOME/lib/pythonX.Y/` which does not exist in a PyInstaller layout.
///   We rely on `PYTHONPATH` alone and suppress user site-packages.
fn apply_bundled_python_env(cmd: &mut Command, internal_dir: &std::path::Path) {
    ensure_bundled_pth_file(internal_dir);
    strip_harmful_python_env(cmd);

    // PYTHONHOME: Windows only.  On macOS/Linux it breaks stdlib resolution
    // because _internal/ lacks the expected lib/pythonX.Y/ subdirectory.
    #[cfg(target_os = "windows")]
    cmd.env("PYTHONHOME", internal_dir);

    #[cfg(not(target_os = "windows"))]
    {
        cmd.env_remove("PYTHONHOME");
        cmd.env("PYTHONNOUSERSITE", "1");
    }

    let mut parts: Vec<PathBuf> = vec![];
    let base_lib = internal_dir.join("base_library.zip");
    if base_lib.exists() {
        parts.push(base_lib);
    }
    parts.push(internal_dir.to_path_buf());
    let lib = internal_dir.join("Lib");
    if lib.is_dir() {
        parts.push(lib);
    }
    let dlls = internal_dir.join("DLLs");
    if dlls.is_dir() {
        parts.push(dlls);
    }
    if let Ok(joined) = std::env::join_paths(&parts) {
        cmd.env("PYTHONPATH", joined);
    }
}

/// 确保 `_internal/` 目录中存在 `python3XX._pth` 文件。
///
/// `._pth` 文件是 CPython 最底层的路径配置机制，在 `PYTHONPATH`/`PYTHONHOME`
/// 之前生效，确保 `base_library.zip` 在 Python 启动最早阶段就能被搜索到。
/// 对于已有新版构建（build_backend.py 已创建 ._pth）的安装，此函数直接返回；
/// 对于旧版安装（无 ._pth），此函数动态创建。
fn ensure_bundled_pth_file(internal_dir: &std::path::Path) {
    // Detect Python version from DLL (Windows) or shared lib (Unix).
    let detected_ver: Option<u32> = (8..=15).find(|minor| {
        let dll = internal_dir.join(format!("python3{}.dll", minor));
        if dll.exists() {
            return true;
        }
        if let Ok(entries) = std::fs::read_dir(internal_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if name.starts_with(&format!("libpython3.{}", minor)) && name.contains(".so") {
                    return true;
                }
            }
        }
        false
    });
    let Some(minor) = detected_ver else { return };

    let pth_name = format!("python3{}._pth", minor);
    let pth_path = internal_dir.join(&pth_name);

    if pth_path.exists() {
        if let Ok(content) = std::fs::read_to_string(&pth_path) {
            if content.contains("base_library.zip") {
                return;
            }
        }
    }

    let mut lines = vec![];
    if internal_dir.join("base_library.zip").exists() {
        lines.push("base_library.zip".to_string());
    }
    let zip_name = format!("python3{}.zip", minor);
    if internal_dir.join(&zip_name).exists() {
        lines.push(zip_name);
    }
    lines.push(".".to_string());
    if internal_dir.join("Lib").is_dir() {
        lines.push("Lib".to_string());
    }
    if internal_dir.join("DLLs").is_dir() {
        lines.push("DLLs".to_string());
    }
    lines.push("import site".to_string());
    let content = lines.join("\n") + "\n";
    let _ = std::fs::write(&pth_path, content);
}

/// 根据 Python 路径自动选择正确的环境配置。
/// bundled（_internal）Python 需要 apply_bundled_python_env，
/// venv Python 只需 strip_harmful_python_env。
fn apply_python_env_for(cmd: &mut Command, py: &std::path::Path) {
    let internal_dir = bundled_backend_dir().join("_internal");
    if py.starts_with(&internal_dir) {
        apply_bundled_python_env(cmd, &internal_dir);
    } else {
        strip_harmful_python_env(cmd);
    }
}

/// 判断 .env 中的键是否会污染 Python 运行时（应在启动后端时忽略）。
fn is_harmful_python_env_key(key: &str) -> bool {
    key.eq_ignore_ascii_case("PYTHONPATH")
        || key.eq_ignore_ascii_case("PYTHONHOME")
        || key.eq_ignore_ascii_case("PYTHON_VENV_PATH")
        || key.eq_ignore_ascii_case("PYTHON_EXECUTABLE")
        || key.eq_ignore_ascii_case("PYTHONSTARTUP")
        || key.eq_ignore_ascii_case("VIRTUAL_ENV")
        || key.eq_ignore_ascii_case("CONDA_PREFIX")
        || key.eq_ignore_ascii_case("CONDA_DEFAULT_ENV")
        || key.eq_ignore_ascii_case("CONDA_SHLVL")
        || key.eq_ignore_ascii_case("CONDA_PYTHON_EXE")
}

async fn spawn_blocking_result<R: Send + 'static>(
    f: impl FnOnce() -> Result<R, String> + Send + 'static,
) -> Result<R, String> {
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|e| format!("后台任务失败（join error）: {e}"))?
}

/// Strip surrounding quotes and inline comments from a raw .env value.
///
/// - Quoted values (`"..."` or `'...'`): return content between quotes literally.
/// - Unquoted values: strip inline comment (`#` preceded by whitespace).
#[allow(dead_code)]
fn clean_env_value(raw: &str) -> String {
    let v = raw.trim();
    if v.len() >= 2 {
        let bytes = v.as_bytes();
        if (bytes[0] == b'"' && bytes[v.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[v.len() - 1] == b'\'')
        {
            return v[1..v.len() - 1].to_string();
        }
    }
    // Unquoted: strip inline comment (# preceded by space or tab)
    for pat in [" #", "\t#"] {
        if let Some(pos) = v.find(pat) {
            return v[..pos].trim_end().to_string();
        }
    }
    v.to_string()
}

#[allow(dead_code)]
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
        out.push((key.to_string(), clean_env_value(v)));
    }
    out
}

#[tauri::command]
async fn openakita_service_start(
    venv_dir: String,
    workspace_id: String,
) -> Result<ServiceStatus, String> {
    let task_started = Instant::now();
    let log_workspace_id = workspace_id.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        openakita_service_start_impl(venv_dir, workspace_id)
    })
    .await
    .map_err(|e| format!("backend start task failed: {e}"))?;
    log_to_file(&format!(
        "[service_start] async command finished: ws={}, elapsed_ms={}, status={}",
        log_workspace_id,
        task_started.elapsed().as_millis(),
        if result.is_ok() { "ok" } else { "error" }
    ));
    result
}

fn openakita_service_start_impl(
    venv_dir: String,
    workspace_id: String,
) -> Result<ServiceStatus, String> {
    let service_start_started = Instant::now();
    log_to_file(&format!(
        "[service_start] called: ws={}, venv={}",
        workspace_id, venv_dir
    ));
    // ── 进程级互斥：同一 workspace 在 SERVICE_START_DEDUPE_MS 窗口内拒绝重复 spawn。
    // 解决 autostart.log 里 27s 内 5 次 spawn pid 的现场表现：前端在 health
    // check 还没响应时反复 invoke，下游 try_acquire_start_lock 的文件锁有
    // 短暂失效窗，需要在更外层加一层时间窗去重。命中时直接返回当前已知
    // ServiceStatus（让前端继续轮询 health 即可），不抛错避免触发 toast。
    {
        let mut last_map = SERVICE_START_LAST_AT.lock().unwrap();
        let now = now_ms();
        if let Some(&last) = last_map.get(&workspace_id) {
            let elapsed = now.saturating_sub(last);
            if elapsed < SERVICE_START_DEDUPE_MS {
                log_to_file(&format!(
                    "[service_start] dedupe-skip ws={} elapsed_ms={}",
                    workspace_id, elapsed
                ));
                let pid_file = service_pid_file(&workspace_id);
                let pf = pid_file.to_string_lossy().to_string();
                let pid_opt = read_pid_file(&workspace_id).map(|d| d.pid);
                let running = read_pid_file(&workspace_id)
                    .map(|d| is_pid_file_valid(&d))
                    .unwrap_or(false);
                return Ok(build_service_status(&workspace_id, running, pid_opt, pf));
            }
        }
        last_map.insert(workspace_id.clone(), now);
    }

    fs::create_dir_all(run_dir()).map_err(|e| {
        let msg = format!("create run dir failed: {e}");
        log_to_file(&format!("[service_start] FAIL: {}", msg));
        msg
    })?;
    let pid_file = service_pid_file(&workspace_id);
    let pf = pid_file.to_string_lossy().to_string();

    // ── 0. 启动前清理旧的心跳文件（避免新进程读到旧心跳） ──
    remove_heartbeat_file(&workspace_id);

    // ── 1. 检查是否已在运行（通过 MANAGED_CHILD 或 PID 文件）──
    {
        let mut guard = MANAGED_CHILD.lock().unwrap();
        if let Some(ref mut mp) = *guard {
            if mp.workspace_id == workspace_id {
                match mp.child.try_wait() {
                    Ok(None) => {
                        return Ok(build_service_status(&workspace_id, true, Some(mp.pid), pf));
                    }
                    _ => {
                        *guard = None;
                    }
                }
            }
        }
    }
    if let Some(data) = read_pid_file(&workspace_id) {
        if is_pid_file_valid(&data) {
            // 进程已在运行，但检查心跳是否严重过期（可能卡死）
            if let Some(true) = is_heartbeat_stale(&workspace_id, 60) {
                // 心跳严重过期时先复核 HTTP health；如果 API 仍正常，
                // 继续复用现有进程，不启动第二个后端。
                let port = read_workspace_api_port(&workspace_id);
                if should_cleanup_stale_heartbeat(Some(true), is_backend_http_healthy(port)) {
                    let _ = graceful_stop_pid(data.pid, port);
                    let _ = fs::remove_file(&pid_file);
                    remove_heartbeat_file(&workspace_id);
                } else {
                    return Ok(build_service_status(
                        &workspace_id,
                        true,
                        Some(data.pid),
                        pf,
                    ));
                }
            } else {
                return Ok(build_service_status(
                    &workspace_id,
                    true,
                    Some(data.pid),
                    pf,
                ));
            }
        } else {
            let _ = fs::remove_file(&pid_file);
            remove_heartbeat_file(&workspace_id);
        }
    }

    // ── 2. 获取启动锁（防止竞态双启动）──
    if !try_acquire_start_lock(&workspace_id) {
        return Err("另一个启动操作正在进行中，请稍候".to_string());
    }
    struct LockGuard(String);
    impl Drop for LockGuard {
        fn drop(&mut self) {
            release_start_lock(&self.0);
        }
    }
    let _lock_guard = LockGuard(workspace_id.clone());

    let ws_dir = workspace_dir(&workspace_id);
    ensure_workspace_scaffold(&ws_dir)?;

    // ── 2.5 端口可用性预检 ──
    // 在 spawn 之前检查端口是否被占用（旧进程残留、TIME_WAIT、其他程序等）。
    // Python 端也有重试，但尽早发现可以给用户更明确的提示。
    let effective_port = read_workspace_api_port(&workspace_id).unwrap_or(18900);
    if !check_port_available(effective_port) {
        // 端口被占用，等待最多 10 秒（处理 TIME_WAIT 等场景）
        if !wait_for_port_free(effective_port, 10_000) {
            return Err(format!(
                "端口 {} 已被占用，无法启动后端服务。\n\
                 可能原因：上次关闭后端口尚未释放、或有其他程序占用该端口。\n\
                 请稍后重试，或检查是否有其他程序占用端口 {}。",
                effective_port, effective_port
            ));
        }
    }

    // 优先使用内嵌 PyInstaller 后端，降级到 venv python
    let backend_resolve_started = Instant::now();
    let (backend_exe, backend_args) = get_backend_executable(&venv_dir);
    log_to_file(&format!(
        "[service_start] backend executable resolved in {}ms",
        backend_resolve_started.elapsed().as_millis()
    ));
    log_to_file(&format!(
        "[service_start] exe={}, exists={}",
        backend_exe.display(),
        backend_exe.exists()
    ));
    if !backend_exe.exists() {
        let bundled_dir = bundled_backend_dir();
        let bundled_name = if cfg!(windows) {
            "openakita-server.exe"
        } else {
            "openakita-server"
        };
        return Err(format!(
            "后端可执行文件不存在: {}\n\
             已检查路径:\n  - bundled: {}/{}\n  - venv: {}\n\
             请尝试: 1) 重新安装桌面端  2) 运行 quickstart.sh 创建 venv",
            backend_exe.to_string_lossy(),
            bundled_dir.display(),
            bundled_name,
            backend_exe.to_string_lossy(),
        ));
    }

    let log_dir = ws_dir.join("logs");
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let log_path = log_dir.join("openakita-serve.log");
    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| format!("open log failed: {e}"))?;

    let mut cmd = Command::new(&backend_exe);
    cmd.current_dir(&ws_dir);
    cmd.args(&backend_args);

    // ── 注入 dual runtime 环境 ──
    // 清除 Anaconda/PYTHONPATH 等污染源，同时把 agent-venv 的 Scripts/bin
    // 前置到 PATH，让后端工具执行 python/pip 时自然落到 agent tools venv。
    apply_dual_runtime_env(&mut cmd);

    // Force UTF-8 output on Windows and make logs clean & realtime.
    // Without this, Rich may try to write unicode symbols (e.g. ✓) using GBK and crash.
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");
    // Disable colored / styled output to avoid ANSI escape codes in log files.
    cmd.env("NO_COLOR", "1");
    let spawn_started_at_ms = now_epoch_secs().saturating_mul(1000);
    cmd.env("OPENAKITA_DESKTOP_SESSION_TOKEN", desktop_session_token());
    cmd.env(
        "OPENAKITA_SPAWN_STARTED_AT_MS",
        spawn_started_at_ms.to_string(),
    );

    // .env 由 Python 端的 load_dotenv(override=True) 自行加载，
    // 不再由 Rust 注入，避免编码/BOM 问题导致 Key 丢失或损坏值抢占。
    // Rust 只注入 Python 自己无法确定的路径类环境变量。
    cmd.env(
        "LLM_ENDPOINTS_CONFIG",
        ws_dir.join("data").join("llm_endpoints.json"),
    );
    cmd.env(
        "OPENAKITA_ROOT",
        openakita_root_dir().to_string_lossy().to_string(),
    );

    // 设置可选模块路径（已安装的可选模块 site-packages）
    // 重要：不能使用 PYTHONPATH！Python 启动时 PYTHONPATH 会被插入到 sys.path
    // 最前面，覆盖 PyInstaller 内置的包（如 pydantic），导致外部 pydantic 的
    // C 扩展 pydantic_core._pydantic_core 加载失败，进程在 import 阶段崩溃。
    // 改用自定义环境变量 OPENAKITA_MODULE_PATHS，由 Python 端的
    // inject_module_paths() 读取并 append 到 sys.path 末尾。
    if let Some(extra_path) = build_modules_pythonpath() {
        cmd.env("OPENAKITA_MODULE_PATHS", extra_path);
    }

    // Playwright 浏览器二进制路径
    // 优先级: 打包内置 > 旧版外置模块安装路径
    // 注: browser 模块已内置到 core 包，Python 端会自动检测 _MEIPASS/playwright-browsers/
    // 这里作为兜底，兼容旧版外置安装
    let browsers_dir = modules_dir().join("browser").join("browsers");
    if browsers_dir.exists() {
        cmd.env("PLAYWRIGHT_BROWSERS_PATH", &browsers_dir);
    }

    // detach + redirect io
    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::from(
            log_file
                .try_clone()
                .map_err(|e| format!("clone log failed: {e}"))?,
        ))
        .stderr(std::process::Stdio::from(log_file));

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x00000008u32 | 0x00000200u32 | 0x0800_0000u32); // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    }

    let spawn_started = Instant::now();
    let child = cmd.spawn().map_err(|e| {
        let msg = format!("spawn openakita serve failed: {e}");
        log_to_file(&format!("[service_start] {}", msg));
        msg
    })?;
    let pid = child.id();
    log_to_file(&format!(
        "[service_start] spawned pid={} in {}ms",
        pid,
        spawn_started.elapsed().as_millis()
    ));
    let started_at = now_epoch_secs();

    // ── 3. 写 JSON PID 文件 ──
    write_pid_file(&workspace_id, pid, "tauri")?;

    // ── 4. 存入 MANAGED_CHILD ──
    {
        let mut guard = MANAGED_CHILD.lock().unwrap();
        *guard = Some(ManagedProcess {
            child,
            workspace_id: workspace_id.clone(),
            pid,
            started_at,
        });
    }

    // Confirm the process is still alive after spawning.
    // 实测在 dual-venv hack 下，Python 解释器 import 失败/路径错误等
    // "立即退出"故障通常发生在 spawn 后 1-3 秒内。原来 sleep 500ms 仅能
    // 抓到极少数现场，导致 service_start 误返回 Ok，前端跟着进入 starting
    // 死循环。改成 6 次 × 500ms 轮询，命中即停，最多多等 2.5s 即可换来
    // 准确的失败判定。
    let mut alive = true;
    for _ in 0..6 {
        std::thread::sleep(std::time::Duration::from_millis(500));
        if !is_pid_running(pid) {
            alive = false;
            break;
        }
    }
    if !alive {
        {
            let mut guard = MANAGED_CHILD.lock().unwrap();
            if let Some(ref mp) = *guard {
                if mp.pid == pid {
                    *guard = None;
                }
            }
        }
        let _ = fs::remove_file(&pid_file);
        tail_serve_log_to_autostart(&log_path, 8 * 1024);
        let tail = fs::read_to_string(&log_path)
            .ok()
            .and_then(|s| {
                if s.len() > 6000 {
                    Some(s[s.len() - 6000..].to_string())
                } else {
                    Some(s)
                }
            })
            .unwrap_or_default();
        return Err(format!(
            "openakita serve 似乎启动后立即退出（PID={pid}）。\n请查看服务日志：{}\n\n--- log tail ---\n{}",
            log_path.to_string_lossy(),
            tail
        ));
    }

    log_to_file(&format!(
        "[service_start] completed in {}ms",
        service_start_started.elapsed().as_millis()
    ));
    Ok(build_service_status(&workspace_id, true, Some(pid), pf))
}

#[tauri::command]
fn openakita_service_stop(workspace_id: String) -> Result<ServiceStatus, String> {
    let pid_file = service_pid_file(&workspace_id);
    let port = read_workspace_api_port(&workspace_id);
    let effective_port = port.unwrap_or(18900);

    // ── 1. MANAGED_CHILD handle ──
    {
        let mut guard = MANAGED_CHILD.lock().unwrap();
        if let Some(mut mp) = guard.take() {
            if mp.workspace_id == workspace_id {
                let old_pid = mp.pid;
                let spawn_started_at = mp.started_at.saturating_mul(1000);
                let clean_shutdown = graceful_stop_pid(mp.pid, port).unwrap_or(false);
                if clean_shutdown && !is_pid_running(old_pid) {
                    write_last_clean_shutdown_marker(&workspace_id, old_pid, spawn_started_at);
                }
                if is_pid_running(mp.pid) {
                    let _ = mp.child.kill();
                    let _ = mp.child.wait();
                }
                let _ = fs::remove_file(&pid_file);
                // 等待端口释放（最多 10 秒），确保后续重启不会遇到端口冲突
                let _ = wait_for_port_free(effective_port, 10_000);
                remove_heartbeat_file(&workspace_id);
                return Ok(build_service_status(
                    &workspace_id,
                    false,
                    None,
                    pid_file.to_string_lossy().to_string(),
                ));
            } else {
                *guard = Some(mp);
            }
        }
    }

    // ── 2. PID 文件回退 ──
    let pid = read_pid_file(&workspace_id).map(|d| d.pid);
    if let Some(pid) = pid {
        // 强制杀干净：如果杀不掉，要显式报错（避免 UI 显示“已停止”但后台仍残留）。
        let clean_shutdown =
            graceful_stop_pid(pid, port).map_err(|e| format!("failed to stop service: {e}"))?;
        if clean_shutdown && !is_pid_running(pid) {
            write_last_clean_shutdown_marker(&workspace_id, pid, 0);
        }
    }
    let _ = fs::remove_file(&pid_file);
    remove_heartbeat_file(&workspace_id);
    // 等待端口释放（最多 10 秒），确保后续重启不会遇到端口冲突
    let _ = wait_for_port_free(effective_port, 10_000);
    Ok(build_service_status(
        &workspace_id,
        false,
        None,
        pid_file.to_string_lossy().to_string(),
    ))
}

#[tauri::command]
fn openakita_service_log(
    workspace_id: String,
    tail_bytes: Option<u64>,
) -> Result<ServiceLogChunk, String> {
    let ws_dir = workspace_dir(&workspace_id);
    let log_path = ws_dir.join("logs").join("openakita-serve.log");
    let path_str = log_path.to_string_lossy().to_string();
    let tail = tail_bytes.unwrap_or(40_000).min(400_000);

    if !log_path.exists() {
        return Ok(ServiceLogChunk {
            path: path_str,
            content: "".into(),
            truncated: false,
        });
    }

    let mut f = std::fs::File::open(&log_path).map_err(|e| format!("open log failed: {e}"))?;
    let len = f
        .metadata()
        .map_err(|e| format!("stat log failed: {e}"))?
        .len();
    let start = len.saturating_sub(tail);
    let truncated = start > 0;
    f.seek(SeekFrom::Start(start))
        .map_err(|e| format!("seek log failed: {e}"))?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf)
        .map_err(|e| format!("read log failed: {e}"))?;
    let content = String::from_utf8_lossy(&buf).to_string();

    Ok(ServiceLogChunk {
        path: path_str,
        content,
        truncated,
    })
}

#[tauri::command]
fn autostart_is_enabled(app: tauri::AppHandle) -> Result<bool, String> {
    #[cfg(desktop)]
    {
        let mgr = app.autolaunch();
        return mgr
            .is_enabled()
            .map_err(|e| format!("autostart is_enabled failed: {e}"));
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
            mgr.enable()
                .map_err(|e| format!("autostart enable failed: {e}"))?;
        } else {
            mgr.disable()
                .map_err(|e| format!("autostart disable failed: {e}"))?;
        }
        // 同步持久化到 state file，用于下次启动时的自修复检查
        let mut state = read_state_file();
        state.auto_start_backend = Some(enabled);
        let _ = write_state_file(&state);
        return Ok(());
    }
    #[cfg(not(desktop))]
    {
        let _ = (app, enabled);
        Ok(())
    }
}

/// 前端调用：查询后端是否正在自动启动中。
/// 返回 true 时前端应禁用启动/重启按钮并显示"正在自动启动服务"提示。
///
/// 判定优先级：
/// 1. `AUTO_START_IN_PROGRESS` 为 true 且未超时 — 自动启动 spawn 线程仍在跑
/// 2. 后端 PID 文件存在但仍处于 BOOT_GRACE 期 + HTTP 不可达 — 进程已 spawn
///    但还在 cold-start（dual-venv hack 实测要 90~120 秒）
///
/// 第 2 条是关键：spawn 调用本身是同步立即返回的，AUTO_START_IN_PROGRESS
/// 在 spawn 返回后立即被清掉，但此时后端可能还要 90 秒才能 HTTP ready。
/// 老逻辑会让前端在 spawn 返回后立刻把 UI 从"启动中"切回"未启动"，
/// 等 90 秒后端真起来再切回"运行中"——这就是用户感知到的诡异闪烁。
#[tauri::command]
fn is_backend_auto_starting() -> bool {
    // 优先级 1：显式的 AUTO_START_IN_PROGRESS flag
    if AUTO_START_IN_PROGRESS.load(Ordering::SeqCst) {
        let started_at = AUTO_START_STARTED_AT_MS.load(Ordering::SeqCst);
        if started_at > 0 {
            let elapsed = now_ms().saturating_sub(started_at);
            if elapsed >= AUTO_START_TIMEOUT_MS {
                log_to_file(&format!(
                    "[auto-start] is_backend_auto_starting timeout after {}ms, clearing flag",
                    elapsed
                ));
                AUTO_START_IN_PROGRESS.store(false, Ordering::SeqCst);
                AUTO_START_STARTED_AT_MS.store(0, Ordering::SeqCst);
            } else {
                return true;
            }
        } else {
            return true;
        }
    }
    // 优先级 2：BOOT_GRACE — 进程已 spawn、PID 还活着、HTTP 还没起来
    let state = read_state_file();
    if let Some(ws_id) = state.current_workspace_id {
        if backend_in_boot_grace(&ws_id) {
            let port = read_workspace_api_port(&ws_id).unwrap_or(18900);
            if !is_backend_http_healthy(Some(port)) {
                return true;
            }
        }
    }
    false
}

/// 前端"重试启动/修复"按钮调用：先把残骸 venv 和 manifest 删干净，
/// 然后重新 ensure dual runtime venv。Bug-rescue 路径，正常启动不会走这里。
///
/// 老的"重试启动/修复"只是再次调 `openakita_service_start`，但 `ensure_venv`
/// 的早期健康检查会被残骸 launcher 蒙混通过、直接 return Ok 而不重建 venv，
/// 用户怎么点都修不好——必须先把 app-venv 目录砍了再重建。
#[tauri::command]
fn repair_runtime_env() -> Result<String, String> {
    let mut report = String::new();
    report.push_str("runtime repair started\n");

    let state = read_state_file();
    if let Some(ws_id) = state.current_workspace_id.clone() {
        match openakita_service_stop(ws_id.clone()) {
            Ok(_) => report.push_str(&format!("stopped backend for workspace {}\n", ws_id)),
            Err(e) => report.push_str(&format!("warn: stop backend for {} failed: {}\n", ws_id, e)),
        }
    }

    let evidence_dir = runtime_root_dir()
        .join("reports")
        .join(format!("pre-repair-{}", now_epoch_secs()));
    if let Err(e) = fs::create_dir_all(&evidence_dir) {
        report.push_str(&format!("warn: create evidence dir failed: {}\n", e));
    } else {
        for path in [
            runtime_manifest_path(),
            runtime_logs_dir().join("app-venv.log"),
            runtime_logs_dir().join("agent-venv.log"),
        ] {
            if path.exists() {
                if let Some(name) = path.file_name() {
                    let dest = evidence_dir.join(name);
                    match fs::copy(&path, &dest) {
                        Ok(_) => report.push_str(&format!("saved evidence {}\n", dest.display())),
                        Err(e) => report.push_str(&format!(
                            "warn: save evidence {} failed: {}\n",
                            path.display(),
                            e
                        )),
                    }
                }
            }
        }
    }

    for dir in [app_venv_dir(), agent_venv_dir()] {
        if dir.exists() {
            match fs::remove_dir_all(&dir) {
                Ok(()) => report.push_str(&format!("removed {}\n", dir.display())),
                Err(e) => {
                    report.push_str(&format!("warn: remove {} failed: {}\n", dir.display(), e));
                }
            }
        }
    }
    let manifest = runtime_manifest_path();
    if manifest.exists() {
        match fs::remove_file(&manifest) {
            Ok(()) => report.push_str(&format!("removed {}\n", manifest.display())),
            Err(e) => {
                report.push_str(&format!(
                    "warn: remove {} failed: {}\n",
                    manifest.display(),
                    e
                ));
            }
        }
    }
    let app_venv_log = runtime_logs_dir().join("app-venv.log");
    if app_venv_log.exists() {
        let _ = fs::remove_file(&app_venv_log);
    }
    let agent_venv_log = runtime_logs_dir().join("agent-venv.log");
    if agent_venv_log.exists() {
        let _ = fs::remove_file(&agent_venv_log);
    }
    match ensure_dual_runtime_env() {
        Ok(info) => {
            report.push_str(&format!(
                "ok: app_python={} agent_python={}\n",
                info.app_python.display(),
                info.agent_python.display()
            ));
            Ok(report)
        }
        Err(e) => {
            write_runtime_failure_manifest(&e);
            report.push_str(&format!("ensure_dual_runtime_env failed: {}\n", e));
            Err(report)
        }
    }
}

#[tauri::command]
fn get_auto_start_backend() -> Result<bool, String> {
    let state = read_state_file();
    Ok(state.auto_start_backend.unwrap_or(false))
}

#[tauri::command]
fn set_auto_start_backend(enabled: bool) -> Result<(), String> {
    let mut state = read_state_file();
    state.auto_start_backend = Some(enabled);
    write_state_file(&state)
}

#[tauri::command]
fn get_auto_update() -> Result<bool, String> {
    let state = read_state_file();
    Ok(state.auto_update.unwrap_or(true))
}

#[tauri::command]
fn set_auto_update(enabled: bool) -> Result<(), String> {
    let mut state = read_state_file();
    state.auto_update = Some(enabled);
    write_state_file(&state)
}

/// 前端心跳检测到后端状态变化时调用，更新托盘 tooltip
/// status: "alive" | "degraded" | "dead"
/// im_summary: 可选的 IM 通道状态摘要（如 "TG:✓ FS:✓ WX:✗"）
#[tauri::command]
fn set_tray_backend_status(
    app: tauri::AppHandle,
    status: String,
    im_summary: Option<String>,
) -> Result<(), String> {
    let base = match status.as_str() {
        "alive" => "OpenAkita - Running",
        "degraded" => "OpenAkita - Backend Unresponsive",
        "dead" => "OpenAkita - Backend Stopped",
        _ => "OpenAkita",
    };
    let tooltip = if let Some(ref im) = im_summary {
        if !im.is_empty() {
            format!("{}\nIM: {}", base, im)
        } else {
            base.to_string()
        }
    } else {
        base.to_string()
    };
    // 更新所有 tray icon 的 tooltip
    if let Some(tray) = app.tray_by_id("main_tray") {
        let _ = tray.set_tooltip(Some(tooltip));
    }

    // 后端死亡时发送系统通知
    if status == "dead" {
        #[cfg(windows)]
        {
            // 使用 Windows toast notification via PowerShell
            // 关键：AUMID 必须与 NSIS 安装器在开始菜单快捷方式上设置的一致（即 tauri.conf.json 的 identifier），
            // 否则 Windows 无法关联到已注册的应用，导致通知内容为空。
            // 同时在注册表注册 AUMID 以确保通知正常显示。
            let mut cmd = Command::new("powershell");
            cmd.args([
                "-NoProfile", "-NonInteractive", "-Command",
                "try { \
                    $aumid = 'com.openakita.setupcenter'; \
                    $rp = \"HKCU:\\SOFTWARE\\Classes\\AppUserModelId\\$aumid\"; \
                    if (!(Test-Path $rp)) { New-Item $rp -Force | Out-Null; Set-ItemProperty $rp -Name DisplayName -Value 'OpenAkita Desktop' }; \
                    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; \
                    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); \
                    $t = $xml.GetElementsByTagName('text'); \
                    $t[0].AppendChild($xml.CreateTextNode('OpenAkita')) | Out-Null; \
                    $t[1].AppendChild($xml.CreateTextNode('Backend service has stopped')) | Out-Null; \
                    $n = [Windows.UI.Notifications.ToastNotification]::new($xml); \
                    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid).Show($n) \
                } catch {}"
            ]);
            apply_no_window(&mut cmd);
            let _ = cmd.spawn();
        }
        #[cfg(not(windows))]
        {
            // macOS: use osascript
            let _ = Command::new("osascript")
                .args([
                    "-e",
                    "display notification \"Backend service has stopped\" with title \"OpenAkita\"",
                ])
                .spawn();
        }
    }
    Ok(())
}

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let open_status = MenuItem::with_id(app, "open_status", "打开状态面板", true, None::<&str>)?;
    let open_web = MenuItem::with_id(app, "open_web", "打开网页版", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "隐藏窗口", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出（Quit）", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&open_status, &open_web, &show, &hide, &quit])?;

    TrayIconBuilder::with_id("main_tray")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("OpenAkita")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app: &tauri::AppHandle, event| match event.id.as_ref() {
            "quit" => {
                // ── 退出前根据所有权标记决定是否停止后端 ──

                // 1. 先停 MANAGED_CHILD（Tauri 自己启动的进程）
                {
                    let mut guard = MANAGED_CHILD.lock().unwrap();
                    if let Some(mut mp) = guard.take() {
                        let port = read_workspace_api_port(&mp.workspace_id);
                        let _ = graceful_stop_pid(mp.pid, port);
                        if is_pid_running(mp.pid) {
                            let _ = mp.child.kill();
                            let _ = mp.child.wait();
                        }
                        let _ = fs::remove_file(service_pid_file(&mp.workspace_id));
                    }
                }

                // 2. 按 PID 文件逐一处理：tauri / external 都停掉
                //    托盘 Quit 是用户明确意图，留任何后端进程都没有意义。
                //    启动阶段的「不杀手动后端」由 startup_version_check 单独保证。
                let entries = list_service_pids();
                for ent in &entries {
                    let port = read_workspace_api_port(&ent.workspace_id);
                    let _ = stop_service_pid_entry(ent, port);
                }

                // 3. 兜底扫描孤儿进程（dev/release 都执行）
                kill_openakita_orphans();

                std::thread::sleep(std::time::Duration::from_millis(600));

                // 4. 最终确认
                let still_pid = list_service_pids()
                    .into_iter()
                    .filter(|x| is_pid_running(x.pid))
                    .collect::<Vec<_>>();
                let still_orphans = kill_openakita_orphans();

                if still_pid.is_empty() && still_orphans.is_empty() {
                    // 全部清理干净，安全退出
                    app.exit(0);
                } else {
                    // 仍有残留：阻止退出，提示用户
                    show_main_window(app, "quit-failed", false);
                    let mut detail = Vec::new();
                    for x in &still_pid {
                        detail.push(format!("{} (PID={})", x.workspace_id, x.pid));
                    }
                    for p in &still_orphans {
                        detail.push(format!("orphan PID={}", p));
                    }
                    let msg = format!(
                        "\u{9000}\u{51fa}\u{5931}\u{8d25}\u{ff1a}\u{540e}\u{53f0}\u{670d}\u{52a1}\u{4ecd}\u{5728}\u{8fd0}\u{884c}\u{3002}\n\n\u{8bf7}\u{5148}\u{5728}\u{201c}\u{72b6}\u{6001}\u{9762}\u{677f}\u{201d}\u{70b9}\u{51fb}\u{201c}\u{505c}\u{6b62}\u{670d}\u{52a1}\u{201d}\u{ff0c}\u{786e}\u{8ba4}\u{72b6}\u{6001}\u{53d8}\u{4e3a}\u{201c}\u{672a}\u{8fd0}\u{884c}\u{201d}\u{540e}\u{518d}\u{9000}\u{51fa}\u{3002}\n\n\u{4ecd}\u{5728}\u{8fd0}\u{884c}\u{7684}\u{8fdb}\u{7a0b}\u{ff1a}{}",
                        detail.join("; ")
                    );
                    let _ = app.emit("open_status", serde_json::json!({}));
                    let _ = app.emit("quit_failed", serde_json::json!({ "message": msg }));
                }
            }
            "show" => {
                show_main_window(app, "tray-show", false);
            }
            "hide" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
            "open_web" => {
                let state = read_state_file();
                let ws_id = state.current_workspace_id.unwrap_or_else(|| "default".into());
                let port = read_workspace_api_port(&ws_id).unwrap_or(18900);
                let url = format!("http://127.0.0.1:{}/web", port);
                #[cfg(target_os = "windows")]
                { let _ = std::process::Command::new("cmd").args(["/c", "start", &url]).spawn(); }
                #[cfg(target_os = "macos")]
                { let _ = std::process::Command::new("open").arg(&url).spawn(); }
                #[cfg(target_os = "linux")]
                { let _ = std::process::Command::new("xdg-open").arg(&url).spawn(); }
            }
            "open_status" => {
                show_main_window(app, "tray-open-status", true);
            }
            _ => {}
        })
        .on_tray_icon_event(move |tray: &tauri::tray::TrayIcon, event| match event {
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } => {
                let app = tray.app_handle();
                show_main_window(app, "tray-left-click", true);
            }
            TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } => {
                let app = tray.app_handle();
                show_main_window(app, "tray-double-click", true);
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
    // Prevent path traversal: use Path::components to reliably detect ".." segments
    // (more robust than string matching, handles edge cases like "foo/..bar" correctly).
    use std::path::Component;
    if rel.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err("relative path must not contain parent directory references (..)".into());
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
    let mut updates = std::collections::BTreeMap::new();
    let mut deletes = std::collections::BTreeSet::new();
    for e in entries {
        if e.key.trim().is_empty() {
            continue;
        }
        let k = e.key.trim().to_string();
        if e.value.trim().is_empty() {
            // 约定：空值表示删除该键（可选字段不填就不落盘）
            deletes.insert(k);
        } else {
            updates.insert(k, e.value.clone());
        }
    }
    if updates.is_empty() && deletes.is_empty() {
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
        if deletes.contains(key) {
            // 删除该键：跳过该行
            seen.insert(key.to_string());
            continue;
        }
        if let Some(new_val) = updates.get(key) {
            out.push(format!("{key}={new_val}"));
            seen.insert(key.to_string());
        } else {
            out.push(line.to_string());
        }
    }

    // append missing keys
    for (k, v) in updates {
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
    let existing = read_text_lossy(&env_path);
    let updated = update_env_content(&existing, &entries);
    fs::write(&env_path, updated).map_err(|e| format!("write .env failed: {e}"))
}

/// Read a text file as UTF-8; fall back to lossy conversion for non-UTF-8 files
/// (e.g. .env with GBK-encoded Chinese comments on Windows).
fn read_text_lossy(path: &Path) -> String {
    match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(_) => {
            // Non-UTF-8 bytes — decode lossily so existing content is preserved.
            fs::read(path)
                .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
                .unwrap_or_default()
        }
    }
}

// ── Workspace backup commands ────────────────────────────────────────

#[tauri::command]
fn export_workspace_backup(
    workspace_id: String,
    output_dir: String,
    include_userdata: bool,
    include_media: bool,
    api_port: u16,
) -> Result<serde_json::Value, String> {
    // Try the Python backend API first (preferred: consistent logic)
    let url = format!("http://127.0.0.1:{}/api/workspace/export", api_port);
    let body = serde_json::json!({
        "output_dir": output_dir,
        "include_userdata": include_userdata,
        "include_media": include_media,
    });
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(300))
        .no_proxy()
        .build()
        .map_err(|e| format!("http client error: {e}"))?;
    let resp = client.post(&url).json(&body).send();
    match resp {
        Ok(r) if r.status().is_success() => {
            let val: serde_json::Value = r.json().map_err(|e| format!("parse response: {e}"))?;
            Ok(val)
        }
        Ok(r) => {
            let status = r.status();
            let text = r.text().unwrap_or_default();
            Err(format!("Backend returned {status}: {text}"))
        }
        Err(_) => {
            // Fallback: create a basic zip using Rust zip crate
            export_workspace_backup_native(
                &workspace_id,
                &output_dir,
                include_userdata,
                include_media,
            )
        }
    }
}

fn export_workspace_backup_native(
    workspace_id: &str,
    output_dir: &str,
    include_userdata: bool,
    include_media: bool,
) -> Result<serde_json::Value, String> {
    use std::io::Read as _;

    let ws = workspace_dir(workspace_id);
    if !ws.exists() {
        return Err("Workspace directory not found".into());
    }
    let out = PathBuf::from(output_dir);
    fs::create_dir_all(&out).map_err(|e| format!("create output dir: {e}"))?;

    let ts = chrono_like_timestamp();
    let zip_name = format!("openakita-backup-{workspace_id}-{ts}.zip");
    let zip_path = out.join(&zip_name);

    let file = fs::File::create(&zip_path).map_err(|e| format!("create zip: {e}"))?;
    let mut zw = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);

    let always_dirs = [
        "identity",
        "data/agents",
        "data/sessions",
        "data/scheduler",
        "data/mcp",
        "data/telegram",
        "skills",
        "mcps",
    ];
    let always_files = [
        ".env",
        "data/llm_endpoints.json",
        "data/skills.json",
        "data/disabled_views.json",
        "data/runtime_state.json",
        "data/proactive_feedback.json",
        "data/sub_agent_states.json",
    ];
    let userdata_dirs = [
        "data/memory",
        "data/retrospects",
        "data/plans",
        "data/docs",
        "data/reports",
        "data/research",
    ];
    let userdata_files = ["data/agent.db"];
    let media_dirs = [
        "data/generated_images",
        "data/sticker",
        "data/media",
        "data/output",
        "data/screenshots",
    ];
    let exclude_dirs = [
        "logs",
        "data/llm_debug",
        "data/delegation_logs",
        "data/traces",
        "data/react_traces",
        "data/temp",
        "data/tool_overflow",
        "data/selfcheck",
        "data/openakita_docs",
        "identity/runtime",
        "node_modules",
        "Lib",
        "__pycache__",
    ];

    let mut file_count: u64 = 0;

    for entry in walkdir(&ws) {
        let full = entry.path();
        if !full.is_file() {
            continue;
        }
        let rel = match full.strip_prefix(&ws) {
            Ok(r) => r.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };

        // Exclude
        if exclude_dirs
            .iter()
            .any(|d| rel == *d || rel.starts_with(&format!("{d}/")))
        {
            continue;
        }
        if rel == "data/backend.heartbeat" || rel == "package.json" || rel == "package-lock.json" {
            continue;
        }

        let included = always_files.contains(&rel.as_str())
            || always_dirs
                .iter()
                .any(|d| rel == *d || rel.starts_with(&format!("{d}/")))
            || (include_userdata
                && (userdata_files.contains(&rel.as_str())
                    || userdata_dirs
                        .iter()
                        .any(|d| rel == *d || rel.starts_with(&format!("{d}/")))))
            || (include_media
                && media_dirs
                    .iter()
                    .any(|d| rel == *d || rel.starts_with(&format!("{d}/"))));

        if !included {
            continue;
        }

        if let Ok(mut f) = fs::File::open(full) {
            let _ = zw.start_file(&rel, options);
            let mut buf = Vec::new();
            if f.read_to_end(&mut buf).is_ok() {
                let _ = zw.write_all(&buf);
                file_count += 1;
            }
        }
    }

    // Write manifest
    let manifest = serde_json::json!({
        "format_version": 1,
        "created_at": chrono_like_timestamp(),
        "workspace_id": workspace_id,
        "include_userdata": include_userdata,
        "include_media": include_media,
        "file_count": file_count,
    });
    let _ = zw.start_file("manifest.json", options);
    let _ = zw.write_all(
        serde_json::to_string_pretty(&manifest)
            .unwrap_or_default()
            .as_bytes(),
    );
    zw.finish().map_err(|e| format!("finalize zip: {e}"))?;

    let size = fs::metadata(&zip_path).map(|m| m.len()).unwrap_or(0);
    Ok(serde_json::json!({
        "status": "ok",
        "path": zip_path.to_string_lossy(),
        "filename": zip_name,
        "size_bytes": size,
    }))
}

#[tauri::command]
fn import_workspace_backup(
    workspace_id: String,
    zip_path: String,
    api_port: u16,
) -> Result<serde_json::Value, String> {
    let url = format!("http://127.0.0.1:{}/api/workspace/import", api_port);
    let body = serde_json::json!({ "zip_path": zip_path });
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(300))
        .no_proxy()
        .build()
        .map_err(|e| format!("http client error: {e}"))?;
    let resp = client.post(&url).json(&body).send();
    match resp {
        Ok(r) if r.status().is_success() => {
            let val: serde_json::Value = r.json().map_err(|e| format!("parse: {e}"))?;
            Ok(val)
        }
        Ok(r) => {
            let status = r.status();
            let text = r.text().unwrap_or_default();
            Err(format!("Backend returned {status}: {text}"))
        }
        Err(_) => {
            // Fallback: native extraction
            import_workspace_backup_native(&workspace_id, &zip_path)
        }
    }
}

fn import_workspace_backup_native(
    workspace_id: &str,
    zip_path: &str,
) -> Result<serde_json::Value, String> {
    use std::io::Read as _;

    let zp = PathBuf::from(zip_path);
    if !zp.exists() {
        return Err("Backup file not found".into());
    }
    let ws = workspace_dir(workspace_id);
    fs::create_dir_all(&ws).map_err(|e| format!("create workspace dir: {e}"))?;

    let file = fs::File::open(&zp).map_err(|e| format!("open zip: {e}"))?;
    let mut archive = zip::ZipArchive::new(file).map_err(|e| format!("read zip: {e}"))?;

    let mut restored = 0u64;
    for i in 0..archive.len() {
        let mut entry = archive.by_index(i).map_err(|e| format!("zip entry: {e}"))?;
        let name = entry.name().to_string();
        if name == "manifest.json" {
            continue;
        }

        // Safety: reject path traversal
        let norm = PathBuf::from(&name);
        if norm
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
        {
            continue;
        }

        let target = ws.join(&name);
        if entry.is_dir() {
            let _ = fs::create_dir_all(&target);
            continue;
        }
        if let Some(parent) = target.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let mut buf = Vec::new();
        if entry.read_to_end(&mut buf).is_ok() {
            if fs::write(&target, &buf).is_ok() {
                restored += 1;
            }
        }
    }

    Ok(serde_json::json!({
        "status": "ok",
        "restored_count": restored,
    }))
}

/// Simple recursive file walker (no external crate dependency needed)
fn walkdir(dir: &Path) -> Vec<walkdir_entry::Entry> {
    let mut result = Vec::new();
    walkdir_recurse(dir, &mut result);
    result
}

fn walkdir_recurse(dir: &Path, out: &mut Vec<walkdir_entry::Entry>) {
    let Ok(rd) = fs::read_dir(dir) else { return };
    for entry in rd.flatten() {
        let path = entry.path();
        out.push(walkdir_entry::Entry { path: path.clone() });
        if path.is_dir() {
            walkdir_recurse(&path, out);
        }
    }
}

mod walkdir_entry {
    use std::path::{Path, PathBuf};
    pub struct Entry {
        pub path: PathBuf,
    }
    impl Entry {
        pub fn path(&self) -> &Path {
            &self.path
        }
    }
}

fn chrono_like_timestamp() -> String {
    use std::time::SystemTime;
    let now = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default();
    // Convert to a simple YYYYMMDD_HHMMSS using rough calculation
    let secs = now.as_secs();
    // Use a simple approach: format via the system's time
    let dt = time_from_epoch(secs);
    format!(
        "{:04}{:02}{:02}_{:02}{:02}{:02}",
        dt.0, dt.1, dt.2, dt.3, dt.4, dt.5
    )
}

fn time_from_epoch(epoch_secs: u64) -> (u32, u32, u32, u32, u32, u32) {
    // Simple epoch-to-datetime conversion (UTC-based, good enough for filenames)
    const SECS_PER_DAY: u64 = 86400;
    const DAYS_PER_YEAR: u64 = 365;

    let total_days = epoch_secs / SECS_PER_DAY;
    let time_of_day = epoch_secs % SECS_PER_DAY;
    let hour = (time_of_day / 3600) as u32;
    let minute = ((time_of_day % 3600) / 60) as u32;
    let second = (time_of_day % 60) as u32;

    // Calculate year/month/day from total_days since 1970-01-01
    let mut year = 1970u32;
    let mut remaining = total_days;
    loop {
        let days_in_year = if is_leap(year) { 366 } else { 365 };
        if remaining < days_in_year {
            break;
        }
        remaining -= days_in_year;
        year += 1;
    }
    let days_in_months: [u64; 12] = if is_leap(year) {
        [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    } else {
        [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    };
    let mut month = 1u32;
    for &dm in &days_in_months {
        if remaining < dm {
            break;
        }
        remaining -= dm;
        month += 1;
    }
    let day = remaining as u32 + 1;

    (year, month, day, hour, minute, second)
}

fn is_leap(y: u32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
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
struct BundledPythonInstallResult {
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
    apply_no_window(&mut c);
    let out = c
        .output()
        .map_err(|e| format!("failed to run {:?}: {e}", cmd))?;
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
    let Some(idx) = lower.find("python") else {
        return false;
    };
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
    let mut out = vec![];

    let root = openakita_root_dir();
    let venv_py = if cfg!(windows) {
        root.join("venv").join("Scripts").join("python.exe")
    } else {
        root.join("venv").join("bin").join("python")
    };
    if venv_py.exists() {
        let c = vec![venv_py.to_string_lossy().to_string()];
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

    if let Some(bundled_py) = bundled_internal_python_path() {
        let c = vec![bundled_py.to_string_lossy().to_string()];
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

    if out.is_empty() {
        out.push(PythonCandidate {
            command: vec![],
            version_text: "未检测到可用的项目内置 Python".to_string(),
            is_usable: false,
        });
    }
    out
}

/// Diagnostic report for the Python environment.
#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PythonDiagnostic {
    /// healthy | broken
    summary: String,
    contracts: Vec<PythonContractResult>,
    environment: PythonEnvironmentSnapshot,
    trace_id: String,
    generated_at: String,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PythonContractResult {
    id: String,
    title: String,
    status: String, // pass | warn | fail
    code: String,
    evidence: Vec<String>,
    auto_fix: bool,
    fix_hint: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PythonEnvironmentSnapshot {
    platform: String,
    bundled_python_path: Option<String>,
    openakita_version: Option<String>,
}

fn python_diag_trace_id() -> String {
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    format!("pydiag-{now_ms}")
}

fn python_diag_generated_at() -> String {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        .to_string()
}

/// Run a full diagnostic.
///
/// Strategy:
///   0. Check heartbeat to distinguish "not started" / "starting" / "running".
///   1. If the backend is running → call GET /api/diagnostics (the backend
///      self-reports, no fragile _internal/python3 invocation needed).
///   2. If the backend is NOT running → basic file-existence check on the
///      bundled openakita-server binary.
#[tauri::command]
fn diagnose_python_env(venv_dir: String) -> PythonDiagnostic {
    let _ = venv_dir;
    let trace_id = python_diag_trace_id();

    let state = read_state_file();
    let ws_id = state.current_workspace_id.clone();

    // Determine the API port of the current workspace's backend.
    let port = ws_id
        .as_deref()
        .and_then(read_workspace_api_port)
        .unwrap_or(18900);

    // --- Strategy 0: check heartbeat to understand backend lifecycle ---
    let heartbeat = ws_id.as_deref().and_then(read_heartbeat_file);
    let backend_phase = heartbeat.as_ref().map(|hb| hb.phase.as_str()).unwrap_or("");
    let http_ready = heartbeat.as_ref().map(|hb| hb.http_ready).unwrap_or(false);
    let hb_fresh = heartbeat
        .as_ref()
        .map(|hb| {
            let age = now_epoch_secs() as f64 - hb.timestamp;
            age <= 30.0
        })
        .unwrap_or(false);

    // Backend process is alive with fresh heartbeat but HTTP not yet ready
    // → it's still initializing; skip the API call (would just time out).
    if hb_fresh && !http_ready && matches!(backend_phase, "starting" | "initializing") {
        return make_backend_starting_diagnostic(trace_id, port, backend_phase);
    }

    // --- Strategy 1: ask the running backend ---
    if let Some(diag) = diagnose_via_backend_api(port) {
        return PythonDiagnostic {
            summary: diag.summary,
            contracts: diag.contracts,
            environment: diag.environment,
            trace_id,
            generated_at: python_diag_generated_at(),
        };
    }

    // API call failed — but if heartbeat says backend is alive, give a
    // more specific message than a generic "unreachable".
    if hb_fresh && http_ready {
        return make_backend_api_unreachable_diagnostic(trace_id, port);
    }

    // --- Strategy 2: backend not reachable — static file check ---
    let bundled_dir = bundled_backend_dir();
    let bundled_exe = if cfg!(windows) {
        bundled_dir.join("openakita-server.exe")
    } else {
        bundled_dir.join("openakita-server")
    };
    let internal_dir = bundled_dir.join("_internal");

    let mut contracts: Vec<PythonContractResult> = vec![];

    if bundled_exe.exists() && internal_dir.exists() {
        contracts.push(PythonContractResult {
            id: "C1_BUNDLED_RUNTIME".into(),
            title: "内置运行时".into(),
            status: "pass".into(),
            code: "RUNTIME_OK".into(),
            evidence: vec![format!("binary: {}", bundled_exe.display())],
            auto_fix: false,
            fix_hint: None,
        });
    } else {
        let mut missing = vec![];
        if !bundled_exe.exists() {
            missing.push(format!("missing: {}", bundled_exe.display()));
        }
        if !internal_dir.exists() {
            missing.push(format!("missing: {}", internal_dir.display()));
        }
        contracts.push(PythonContractResult {
            id: "C1_BUNDLED_RUNTIME".into(),
            title: "内置运行时".into(),
            status: "fail".into(),
            code: "RUNTIME_MISSING".into(),
            evidence: missing,
            auto_fix: false,
            fix_hint: Some("请重装 OpenAkita 以恢复内置运行时".into()),
        });
    }

    contracts.push(PythonContractResult {
        id: "C0_BACKEND_OFFLINE".into(),
        title: "后端服务".into(),
        status: "warn".into(),
        code: "BACKEND_NOT_RUNNING".into(),
        evidence: vec![format!("port {} unreachable", port)],
        auto_fix: false,
        fix_hint: Some("启动后端服务后可获得完整诊断信息".into()),
    });

    let failing: Vec<&PythonContractResult> =
        contracts.iter().filter(|c| c.status == "fail").collect();
    let summary = if failing.is_empty() {
        "healthy"
    } else {
        "broken"
    }
    .to_string();

    PythonDiagnostic {
        summary,
        contracts,
        environment: PythonEnvironmentSnapshot {
            platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
            bundled_python_path: None,
            openakita_version: None,
        },
        trace_id,
        generated_at: python_diag_generated_at(),
    }
}

/// Diagnostic result when backend is still initializing (heartbeat alive, HTTP not ready).
fn make_backend_starting_diagnostic(trace_id: String, port: u16, phase: &str) -> PythonDiagnostic {
    PythonDiagnostic {
        summary: "healthy".into(),
        contracts: vec![PythonContractResult {
            id: "C0_BACKEND_STARTING".into(),
            title: "后端服务".into(),
            status: "warn".into(),
            code: "BACKEND_STARTING".into(),
            evidence: vec![format!("phase: {}, port {}", phase, port)],
            auto_fix: false,
            fix_hint: Some("后端正在启动，请稍后再试".into()),
        }],
        environment: PythonEnvironmentSnapshot {
            platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
            bundled_python_path: None,
            openakita_version: None,
        },
        trace_id,
        generated_at: python_diag_generated_at(),
    }
}

/// Diagnostic result when heartbeat says http_ready=true but API call still fails.
fn make_backend_api_unreachable_diagnostic(trace_id: String, port: u16) -> PythonDiagnostic {
    PythonDiagnostic {
        summary: "healthy".into(),
        contracts: vec![PythonContractResult {
            id: "C0_BACKEND_OFFLINE".into(),
            title: "后端服务".into(),
            status: "warn".into(),
            code: "BACKEND_API_UNREACHABLE".into(),
            evidence: vec![format!(
                "heartbeat ok, port {} API unreachable — retrying may help",
                port
            )],
            auto_fix: false,
            fix_hint: Some("后端进程正在运行但 API 暂时不可达，请稍后重试".into()),
        }],
        environment: PythonEnvironmentSnapshot {
            platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
            bundled_python_path: None,
            openakita_version: None,
        },
        trace_id,
        generated_at: python_diag_generated_at(),
    }
}

/// Call GET /api/diagnostics on the running backend and map the response
/// to our diagnostic structures.
///
/// Uses a quick TCP probe first; if nothing is listening, returns None
/// immediately without wasting time on HTTP. On transient failures
/// (timeout, reset) retries once after a short delay.
fn diagnose_via_backend_api(port: u16) -> Option<PythonDiagnostic> {
    // Quick TCP probe: if nothing is listening, bail out immediately.
    {
        use std::net::TcpStream;
        let addr = format!("127.0.0.1:{}", port);
        if TcpStream::connect_timeout(&addr.parse().ok()?, std::time::Duration::from_secs(2))
            .is_err()
        {
            return None;
        }
    }

    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(6))
        .no_proxy()
        .build()
        .ok()?;

    let url = format!("http://127.0.0.1:{}/api/diagnostics", port);
    let max_attempts: u8 = 2;
    let mut last_err = String::new();

    for attempt in 0..max_attempts {
        if attempt > 0 {
            std::thread::sleep(std::time::Duration::from_millis(1500));
        }
        match client.get(&url).send() {
            Ok(resp) if resp.status().is_success() => match resp.json::<serde_json::Value>() {
                Ok(json) => return parse_diagnostics_json(&json),
                Err(e) => {
                    last_err = format!("json parse: {e}");
                    continue;
                }
            },
            Ok(resp) => {
                last_err = format!("HTTP {}", resp.status());
                continue;
            }
            Err(e) => {
                let msg = format!("{e}");
                // Connection refused → nothing is listening, don't retry.
                if msg.contains("onnection refused") || msg.contains("No connection") {
                    eprintln!("[diagnose] connection refused on port {port}");
                    return None;
                }
                last_err = msg;
                continue;
            }
        }
    }

    eprintln!("[diagnose] backend API unreachable after {max_attempts} attempts (port={port}): {last_err}");
    None
}

fn parse_diagnostics_json(json: &serde_json::Value) -> Option<PythonDiagnostic> {
    let summary = json
        .get("summary")
        .and_then(|v| v.as_str())
        .unwrap_or("healthy")
        .to_string();

    let mut contracts: Vec<PythonContractResult> = vec![];
    if let Some(checks) = json.get("checks").and_then(|v| v.as_array()) {
        for c in checks {
            contracts.push(PythonContractResult {
                id: c.get("id").and_then(|v| v.as_str()).unwrap_or("").into(),
                title: c.get("title").and_then(|v| v.as_str()).unwrap_or("").into(),
                status: c
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("pass")
                    .into(),
                code: c.get("code").and_then(|v| v.as_str()).unwrap_or("").into(),
                evidence: c
                    .get("evidence")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|x| x.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default(),
                auto_fix: c.get("autoFix").and_then(|v| v.as_bool()).unwrap_or(false),
                fix_hint: c.get("fixHint").and_then(|v| v.as_str()).map(String::from),
            });
        }
    }

    let env_obj = json.get("environment");
    let environment = PythonEnvironmentSnapshot {
        platform: env_obj
            .and_then(|e| e.get("platform"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        bundled_python_path: None,
        openakita_version: env_obj
            .and_then(|e| e.get("openakitaVersion"))
            .and_then(|v| v.as_str())
            .map(String::from),
    };

    Some(PythonDiagnostic {
        summary,
        contracts,
        environment,
        trace_id: String::new(),
        generated_at: String::new(),
    })
}

#[tauri::command]
fn export_python_diagnostic_report(venv_dir: String) -> Result<String, String> {
    let diag = diagnose_python_env(venv_dir);
    let report_dir = openakita_root_dir().join("runtime").join("reports");
    fs::create_dir_all(&report_dir).map_err(|e| format!("创建报告目录失败: {e}"))?;
    let report_path = report_dir.join(format!("python-diagnostic-{}.json", diag.trace_id));
    let text = serde_json::to_string_pretty(&diag).map_err(|e| format!("序列化报告失败: {e}"))?;
    fs::write(&report_path, text).map_err(|e| format!("写入报告失败: {e}"))?;
    Ok(report_path.to_string_lossy().to_string())
}

/// 校验并返回安装包内置 Python（不再运行时下载 Python）。
fn install_bundled_python_sync(
    _python_series: Option<String>,
    _log_path: Option<PathBuf>,
) -> Result<BundledPythonInstallResult, String> {
    let py = bundled_internal_python_path().ok_or_else(|| {
        "安装包内置 Python 不可用。请重新安装 OpenAkita 以恢复 resources/openakita-server/_internal".to_string()
    })?;
    let bundled_dir = bundled_backend_dir();
    Ok(BundledPythonInstallResult {
        python_command: vec![py.to_string_lossy().to_string()],
        python_path: py.to_string_lossy().to_string(),
        install_dir: bundled_dir.to_string_lossy().to_string(),
        asset_name: "bundled-internal".to_string(),
        tag: "bundled".to_string(),
    })
}

#[tauri::command]
async fn install_bundled_python(
    python_series: Option<String>,
    log_path: Option<String>,
) -> Result<BundledPythonInstallResult, String> {
    let path_buf = log_path.map(PathBuf::from);
    spawn_blocking_result(move || install_bundled_python_sync(python_series, path_buf)).await
}

#[tauri::command]
async fn create_venv(python_command: Vec<String>, venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let venv = PathBuf::from(venv_dir);
        if venv.exists() {
            return Ok(venv.to_string_lossy().to_string());
        }
        let _ = python_command; // API 兼容保留，实际统一使用安装包内置 Python
        let bundled_py = bundled_internal_python_path()
            .ok_or_else(|| "安装包内置 Python 不可用，请重新安装 OpenAkita".to_string())?;
        let mut c = Command::new(&bundled_py);
        apply_no_window(&mut c);
        apply_bundled_python_env(&mut c, &bundled_backend_dir().join("_internal"));
        c.args(["-m", "venv"])
            .arg(&venv)
            .status()
            .map_err(|e| format!("failed to create venv: {e}"))?
            .success()
            .then_some(())
            .ok_or_else(|| "venv creation failed".to_string())?;
        Ok(venv.to_string_lossy().to_string())
    })
    .await
}

fn venv_python_path(venv_dir: &str) -> PathBuf {
    let v = PathBuf::from(venv_dir);
    if cfg!(windows) {
        v.join("Scripts").join("python.exe")
    } else {
        v.join("bin").join("python")
    }
}

/// 解析可用的 Python 解释器路径，并可选返回需要设置的 PYTHONPATH（bundled 模式）。
/// 只使用安装包内置 Python 创建的环境：venv → bundled _internal/python.exe
fn resolve_python(venv_dir: &str) -> Result<(PathBuf, Option<String>), String> {
    let venv_py = venv_python_path(venv_dir);
    if venv_py.exists() {
        return Ok((venv_py, None));
    }
    let py = find_pip_python().ok_or_else(|| {
        "未找到可用 Python 解释器（venv/bundled）。请重新安装 OpenAkita 以恢复内置 Python。"
            .to_string()
    })?;
    let bundled = bundled_backend_dir();
    let internal_dir = bundled.join("_internal");
    let pythonpath = if py.starts_with(&internal_dir) {
        let mut parts: Vec<PathBuf> = vec![];
        let base_lib = internal_dir.join("base_library.zip");
        if base_lib.exists() {
            parts.push(base_lib);
        }
        parts.push(internal_dir.clone());
        let lib = internal_dir.join("Lib");
        if lib.is_dir() {
            parts.push(lib);
        }
        let dlls = internal_dir.join("DLLs");
        if dlls.is_dir() {
            parts.push(dlls);
        }
        let joined = std::env::join_paths(parts)
            .map_err(|e| format!("构建 bundled PYTHONPATH 失败: {e}"))?;
        Some(joined.to_string_lossy().to_string())
    } else {
        None
    };
    Ok((py, pythonpath))
}

fn venv_pythonw_path(venv_dir: &str) -> PathBuf {
    let v = PathBuf::from(venv_dir);
    if cfg!(windows) {
        let p = v.join("Scripts").join("pythonw.exe");
        if p.exists() {
            return p;
        }
        v.join("Scripts").join("python.exe")
    } else {
        v.join("bin").join("python")
    }
}

#[tauri::command]
async fn pip_install(
    app: tauri::AppHandle,
    venv_dir: String,
    package_spec: String,
    index_url: Option<String>,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let (py, pythonpath) = resolve_python(&venv_dir)?;

        let mut log = String::new();

        #[derive(Serialize, Clone)]
        #[serde(rename_all = "camelCase")]
        struct PipInstallEvent {
            kind: String, // "stage" | "line"
            stage: Option<String>,
            percent: Option<u8>,
            text: Option<String>,
        }

        let emit_stage = |stage: &str, percent: u8| {
            let _ = app.emit(
                "pip_install_event",
                PipInstallEvent {
                    kind: "stage".into(),
                    stage: Some(stage.into()),
                    percent: Some(percent),
                    text: None,
                },
            );
        };
        let emit_line = |text: &str| {
            let _ = app.emit(
                "pip_install_event",
                PipInstallEvent {
                    kind: "line".into(),
                    stage: None,
                    percent: None,
                    text: Some(text.into()),
                },
            );
        };

        fn run_streaming(
            mut cmd: Command,
            header: &str,
            log: &mut String,
            emit_line: &dyn Fn(&str),
        ) -> Result<std::process::ExitStatus, String> {
            use std::io::Read as _;
            use std::process::Stdio;
            use std::sync::mpsc;
            use std::thread;

            emit_line(&format!("\n=== {header} ===\n"));
            log.push_str(&format!("=== {header} ===\n"));

            cmd.stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());

            let mut child = cmd.spawn().map_err(|e| format!("{header} failed to start: {e}"))?;
            let mut stdout = child
                .stdout
                .take()
                .ok_or_else(|| format!("{header} stdout pipe missing"))?;
            let mut stderr = child
                .stderr
                .take()
                .ok_or_else(|| format!("{header} stderr pipe missing"))?;

            let (tx, rx) = mpsc::channel::<(bool, String)>();
            let tx1 = tx.clone();
            let h1 = thread::spawn(move || {
                let mut buf = [0u8; 4096];
                loop {
                    match stdout.read(&mut buf) {
                        Ok(0) => break,
                        Ok(n) => {
                            let s = String::from_utf8_lossy(&buf[..n]).to_string();
                            let _ = tx1.send((false, s));
                        }
                        Err(_) => break,
                    }
                }
            });
            let tx2 = tx.clone();
            let h2 = thread::spawn(move || {
                let mut buf = [0u8; 4096];
                loop {
                    match stderr.read(&mut buf) {
                        Ok(0) => break,
                        Ok(n) => {
                            let s = String::from_utf8_lossy(&buf[..n]).to_string();
                            let _ = tx2.send((true, s));
                        }
                        Err(_) => break,
                    }
                }
            });
            drop(tx);

            // Drain output while process runs
            loop {
                match rx.recv_timeout(std::time::Duration::from_millis(120)) {
                    Ok((_is_err, chunk)) => {
                        emit_line(&chunk);
                        log.push_str(&chunk);
                    }
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        if let Ok(Some(_)) = child.try_wait() {
                            break;
                        }
                    }
                    Err(mpsc::RecvTimeoutError::Disconnected) => break,
                }
            }

            let status = child
                .wait()
                .map_err(|e| format!("{header} wait failed: {e}"))?;
            let _ = h1.join();
            let _ = h2.join();

            // Drain remaining buffered chunks
            while let Ok((_is_err, chunk)) = rx.try_recv() {
                emit_line(&chunk);
                log.push_str(&chunk);
            }
            log.push_str("\n\n");
            Ok(status)
        }

        // 国内镜像兜底：前端未传 index_url 时默认使用阿里云
        let effective_index = index_url.as_deref()
            .unwrap_or("https://mirrors.aliyun.com/pypi/simple/");
        let effective_host = effective_index
            .split("//").nth(1).unwrap_or("")
            .split('/').next().unwrap_or("");

        // upgrade pip first (best-effort)
        emit_stage("升级 pip（best-effort）", 40);
        let mut up = Command::new(&py);
        apply_no_window(&mut up);
        strip_harmful_python_env(&mut up);
        up.env("PYTHONUTF8", "1");
        up.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath {
            up.env("PYTHONPATH", pp);
        }
        up.args(["-m", "pip", "install", "-U", "pip", "setuptools", "wheel"]);
        up.args(["-i", effective_index]);
        if !effective_host.is_empty() {
            up.args(["--trusted-host", effective_host]);
        }
        let _ = run_streaming(up, "pip upgrade (best-effort)", &mut log, &emit_line);

        emit_stage("安装 openakita（pip）", 70);
        let mut c = Command::new(&py);
        apply_no_window(&mut c);
        strip_harmful_python_env(&mut c);
        c.env("PYTHONUTF8", "1");
        c.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath {
            c.env("PYTHONPATH", pp);
        }
        c.args(["-m", "pip", "install", "-U", &package_spec]);
        c.args(["-i", effective_index]);
        if !effective_host.is_empty() {
            c.args(["--trusted-host", effective_host]);
        }
        let status = run_streaming(c, "pip install", &mut log, &emit_line)?;
        if !status.success() {
            let tail = if log.len() > 6000 {
                &log[log.len() - 6000..]
            } else {
                &log
            };
            return Err(format!("pip install failed: {status}\n\n--- output tail ---\n{tail}"));
        }

        // Post-check: ensure Setup Center bridge exists in the installed package.
        emit_stage("验证安装", 95);
        emit_line("\n=== verify ===\n");
        let mut verify = Command::new(&py);
        apply_no_window(&mut verify);
        strip_harmful_python_env(&mut verify);
        verify.env("PYTHONUTF8", "1");
        verify.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath {
            verify.env("PYTHONPATH", pp);
        }
        verify.args([
            "-c",
            "import openakita; import openakita.setup_center.bridge; print(getattr(openakita,'__version__',''))",
        ]);
        let v = verify.output().map_err(|e| format!("verify openakita failed: {e}"))?;
        if !v.status.success() {
            let stdout = String::from_utf8_lossy(&v.stdout).to_string();
            let stderr = String::from_utf8_lossy(&v.stderr).to_string();
            return Err(format!(
                "openakita 已安装，但缺少 Setup Center 所需模块（openakita.setup_center.bridge）。\n这通常意味着你安装的 openakita 版本过旧或来源不包含该模块。\nstdout:\n{}\nstderr:\n{}",
                stdout, stderr
            ));
        }

        let ver = String::from_utf8_lossy(&v.stdout).trim().to_string();
        log.push_str("=== verify ===\n");
        log.push_str("import openakita.setup_center.bridge: OK\n");
        emit_line("import openakita.setup_center.bridge: OK\n");
        if !ver.is_empty() {
            log.push_str(&format!("openakita version: {ver}\n"));
            emit_line(&format!("openakita version: {ver}\n"));
        }
        emit_stage("完成", 100);

        Ok(log)
    })
    .await
}

#[tauri::command]
async fn pip_uninstall(venv_dir: String, package_name: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let (py, pythonpath) = resolve_python(&venv_dir)?;
        if package_name.trim().is_empty() {
            return Err("package_name is empty".into());
        }

        let mut c = Command::new(&py);
        apply_no_window(&mut c);
        strip_harmful_python_env(&mut c);
        if let Some(ref pp) = pythonpath {
            c.env("PYTHONPATH", pp);
        }
        c.args(["-m", "pip", "uninstall", "-y", package_name.trim()]);
        let status = c
            .status()
            .map_err(|e| format!("pip uninstall failed to start: {e}"))?;
        if !status.success() {
            return Err(format!("pip uninstall failed: {status}"));
        }
        Ok("ok".into())
    })
    .await
}

fn run_python_module_json(
    venv_dir: &str,
    module: &str,
    args: &[&str],
    extra_env: &[(&str, &str)],
) -> Result<String, String> {
    let (py, pythonpath) = resolve_python(venv_dir)?;

    let mut c = Command::new(&py);
    apply_no_window(&mut c);
    strip_harmful_python_env(&mut c);
    c.env("PYTHONUTF8", "1");
    c.env("PYTHONIOENCODING", "utf-8");
    if let Some(ref pp) = pythonpath {
        c.env("PYTHONPATH", pp);
    }
    c.arg("-m").arg(module);
    c.args(args);
    for (k, v) in extra_env {
        c.env(k, v);
    }
    let out = c
        .output()
        .map_err(|e| format!("failed to run python: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        let stdout = String::from_utf8_lossy(&out.stdout).to_string();
        return Err(format!(
            "python failed: {}\nstdout:\n{}\nstderr:\n{}",
            out.status, stdout, stderr
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

#[tauri::command]
async fn openakita_list_providers(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        run_python_module_json(
            &venv_dir,
            "openakita.setup_center.bridge",
            &["list-providers"],
            &[],
        )
    })
    .await
}

#[tauri::command]
async fn openakita_list_skills(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        run_python_module_json(
            &venv_dir,
            "openakita.setup_center.bridge",
            &["list-skills", "--workspace-dir", &wd_str],
            &[],
        )
    })
    .await
}

#[tauri::command]
async fn openakita_list_models(
    venv_dir: String,
    api_type: String,
    base_url: String,
    provider_slug: Option<String>,
    api_key: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let mut args = vec![
            "list-models",
            "--api-type",
            api_type.as_str(),
            "--base-url",
            base_url.as_str(),
        ];
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
    })
    .await
}

#[tauri::command]
async fn openakita_version(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        // 1. 尝试从打包后端读取 _bundled_version.txt（最快且无需 Python）
        let bundled = bundled_backend_dir();
        let version_file = bundled
            .join("_internal")
            .join("openakita")
            .join("_bundled_version.txt");
        if version_file.exists() {
            if let Ok(v) = fs::read_to_string(&version_file) {
                let v = v.trim().to_string();
                if !v.is_empty() {
                    return Ok(v);
                }
            }
        }

        // 2. 使用 resolve_python 查找可用 Python 并获取版本
        let (py, pythonpath) = resolve_python(&venv_dir)?;
        let mut c = Command::new(&py);
        apply_no_window(&mut c);
        strip_harmful_python_env(&mut c);
        c.env("PYTHONUTF8", "1");
        c.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath {
            c.env("PYTHONPATH", pp);
        }
        c.args([
            "-c",
            "import openakita; print(getattr(openakita,'__version__',''))",
        ]);
        let out = c
            .output()
            .map_err(|e| format!("get openakita version failed: {e}"))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr).to_string();
            let stdout = String::from_utf8_lossy(&out.stdout).to_string();
            return Err(format!(
                "python failed: {}\nstdout:\n{}\nstderr:\n{}",
                out.status, stdout, stderr
            ));
        }
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    })
    .await
}

/// Health check LLM endpoints via Python bridge.
/// Returns JSON array of health results.
#[tauri::command]
async fn openakita_health_check_endpoint(
    venv_dir: String,
    workspace_id: String,
    endpoint_name: Option<String>,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        let mut args = vec!["health-check-endpoint", "--workspace-dir", &wd_str];
        let ep_name_str;
        if let Some(ref name) = endpoint_name {
            ep_name_str = name.clone();
            args.push("--endpoint-name");
            args.push(&ep_name_str);
        }
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Health check IM channels via Python bridge.
/// Returns JSON array of health results.
#[tauri::command]
async fn openakita_health_check_im(
    venv_dir: String,
    workspace_id: String,
    channel: Option<String>,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        let mut args = vec!["health-check-im", "--workspace-dir", &wd_str];
        let ch_str;
        if let Some(ref ch) = channel {
            ch_str = ch.clone();
            args.push("--channel");
            args.push(&ch_str);
        }
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Ensure IM channel dependencies are installed via Python bridge.
/// Returns JSON with status/installed/message.
#[tauri::command]
async fn openakita_ensure_channel_deps(
    venv_dir: String,
    workspace_id: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        let args = vec!["ensure-channel-deps", "--workspace-dir", &wd_str];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Install a skill from URL/path.
#[tauri::command]
async fn openakita_install_skill(
    venv_dir: String,
    workspace_id: String,
    url: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        let args = vec!["install-skill", "--workspace-dir", &wd_str, "--url", &url];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Uninstall a skill by name.
#[tauri::command]
async fn openakita_uninstall_skill(
    venv_dir: String,
    workspace_id: String,
    skill_name: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        let args = vec![
            "uninstall-skill",
            "--workspace-dir",
            &wd_str,
            "--skill-name",
            &skill_name,
        ];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// List marketplace skills.
#[tauri::command]
async fn openakita_list_marketplace(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["list-marketplace"];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Get skill config schema.
#[tauri::command]
async fn openakita_get_skill_config(
    venv_dir: String,
    workspace_id: String,
    skill_name: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let wd_str = wd.to_string_lossy().to_string();
        let args = vec![
            "get-skill-config",
            "--workspace-dir",
            &wd_str,
            "--skill-name",
            &skill_name,
        ];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Start WeCom QR code onboarding (generate QR).
/// Returns JSON with qr_url + qr_id.
#[tauri::command]
async fn openakita_wecom_onboard_start(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["wecom-onboard-start"];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Poll WeCom QR code scan result.
/// Returns JSON with bot_id + secret on success.
#[tauri::command]
async fn openakita_wecom_onboard_poll(venv_dir: String, scode: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["wecom-onboard-poll", "--scode", &scode];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Start Feishu Device Flow onboarding (QR scan).
/// Returns JSON with device_code + verification_uri.
#[tauri::command]
async fn openakita_feishu_onboard_start(
    venv_dir: String,
    domain: Option<String>,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let d = domain.unwrap_or_else(|| "feishu".to_string());
        let args = vec!["feishu-onboard-start", "--domain", &d];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Poll Feishu Device Flow authorization status.
/// Returns JSON with status / app_id / app_secret on success.
#[tauri::command]
async fn openakita_feishu_onboard_poll(
    venv_dir: String,
    domain: Option<String>,
    device_code: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let d = domain.unwrap_or_else(|| "feishu".to_string());
        let args = vec![
            "feishu-onboard-poll",
            "--domain",
            &d,
            "--device-code",
            &device_code,
        ];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Validate Feishu App ID / App Secret credentials.
/// Returns JSON with {valid: bool, error?: string}.
#[tauri::command]
async fn openakita_feishu_validate(
    venv_dir: String,
    app_id: String,
    app_secret: String,
    domain: Option<String>,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let d = domain.unwrap_or_else(|| "feishu".to_string());
        let args = vec![
            "feishu-validate",
            "--app-id",
            &app_id,
            "--app-secret",
            &app_secret,
            "--domain",
            &d,
        ];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Start QQ Bot OpenClaw onboarding (QR scan).
/// Returns JSON with session_id + qr_url.
#[tauri::command]
async fn openakita_qqbot_onboard_start(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["qqbot-onboard-start"];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Poll QQ Bot OpenClaw login status.
/// Returns JSON with status / developer_id.
#[tauri::command]
async fn openakita_qqbot_onboard_poll(
    venv_dir: String,
    session_id: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["qqbot-onboard-poll", "--session-id", &session_id];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Create a QQ bot via OpenClaw.
/// Returns JSON with app_id / app_secret / bot_name.
#[tauri::command]
async fn openakita_qqbot_onboard_create(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["qqbot-onboard-create"];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Atomic poll + create in one process so cookies carry over.
/// Returns JSON with status / app_id / app_secret.
#[tauri::command]
async fn openakita_qqbot_onboard_poll_and_create(
    venv_dir: String,
    session_id: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["qqbot-onboard-poll-and-create", "--session-id", &session_id];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Validate QQ Bot App ID / App Secret credentials.
/// Returns JSON with {valid: bool, error?: string}.
#[tauri::command]
async fn openakita_qqbot_validate(
    venv_dir: String,
    app_id: String,
    app_secret: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec![
            "qqbot-validate",
            "--app-id",
            &app_id,
            "--app-secret",
            &app_secret,
        ];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Start WeChat iLink Bot QR code login.
/// Returns JSON with qrcode + qrcode_url.
#[tauri::command]
async fn openakita_wechat_onboard_start(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["wechat-onboard-start"];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Poll WeChat QR code login status (long-poll).
/// Returns JSON with status (wait/scaned/confirmed/expired) + token.
#[tauri::command]
async fn openakita_wechat_onboard_poll(venv_dir: String, qrcode: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let args = vec!["wechat-onboard-poll", "--qrcode", &qrcode];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Fetch available versions of a package from PyPI JSON API.
/// Returns JSON array of version strings, newest first.
#[tauri::command]
async fn fetch_pypi_versions(package: String, index_url: Option<String>) -> Result<String, String> {
    spawn_blocking_result(move || {
        // 构建候选 URL 列表，多源回退
        // 注意：并非所有 PyPI 镜像都支持 /pypi/<pkg>/json API（阿里云不支持）
        // 因此即使用户指定了 index_url，也要带上已验证可用的回退源
        let mut urls: Vec<String> = Vec::new();
        if let Some(ref idx) = index_url {
            let root = idx
                .trim_end_matches('/')
                .trim_end_matches("/simple")
                .trim_end_matches("/simple/");
            urls.push(format!("{}/pypi/{}/json", root, package));
        }
        // 清华（已验证支持 JSON API）和官方 PyPI 作为回退
        let tuna_url = format!("https://pypi.tuna.tsinghua.edu.cn/pypi/{}/json", package);
        let pypi_url = format!("https://pypi.org/pypi/{}/json", package);
        if !urls.iter().any(|u| u.contains("tuna.tsinghua")) {
            urls.push(tuna_url);
        }
        if !urls.iter().any(|u| u.contains("pypi.org")) {
            urls.push(pypi_url);
        }

        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .user_agent("openakita-setup-center")
            .build()
            .map_err(|e| format!("HTTP client error: {e}"))?;

        // 多源自动回退
        let mut last_err = String::new();
        let mut resp_ok = None;
        for url in &urls {
            match client.get(url).send() {
                Ok(r) => match r.error_for_status() {
                    Ok(r) => {
                        resp_ok = Some(r);
                        break;
                    }
                    Err(e) => {
                        last_err = format!("fetch PyPI versions failed ({}): {}", url, e);
                    }
                },
                Err(e) => {
                    last_err = format!("fetch PyPI versions failed ({}): {}", url, e);
                }
            }
        }
        let resp = resp_ok.ok_or(last_err)?;

        let body: serde_json::Value = resp
            .json()
            .map_err(|e| format!("parse PyPI JSON failed: {e}"))?;

        // PyPI JSON API: { "releases": { "1.0.0": [...], "1.2.3": [...], ... } }
        let releases = body
            .get("releases")
            .and_then(|v| v.as_object())
            .ok_or_else(|| "unexpected PyPI JSON format: missing 'releases'".to_string())?;

        let mut versions: Vec<String> = releases
            .keys()
            .filter(|v| {
                // Skip pre-release / dev versions with letters like "a", "b", "rc", "dev"
                // unless the version contains only dots and digits
                let v_lower = v.to_lowercase();
                !v_lower.contains("dev") && !v_lower.contains("alpha")
            })
            .cloned()
            .collect();

        // Sort by semver-ish descending (newest first).
        // Use a simple tuple-based comparison: split on '.', parse each part.
        versions.sort_by(|a, b| {
            let parse = |s: &str| -> Vec<i64> {
                s.split('.')
                    .map(|p| {
                        // strip pre-release suffixes for sorting: "1a0" -> 1
                        let numeric: String =
                            p.chars().take_while(|c| c.is_ascii_digit()).collect();
                        numeric.parse::<i64>().unwrap_or(0)
                    })
                    .collect()
            };
            parse(b).cmp(&parse(a))
        });

        Ok(serde_json::to_string(&versions).unwrap_or_else(|_| "[]".into()))
    })
    .await
}

/// Generic HTTP GET JSON proxy – bypasses CORS for the webview.
/// Returns the response body as a JSON string.
#[tauri::command]
async fn http_get_json(url: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(15))
            .user_agent("openakita-desktop/1.0")
            .build()
            .map_err(|e| format!("HTTP client error: {e}"))?;

        let resp = client
            .get(&url)
            .send()
            .map_err(|e| format!("HTTP GET failed ({}): {}", url, e))?
            .error_for_status()
            .map_err(|e| format!("HTTP GET failed ({}): {}", url, e))?;

        let text = resp
            .text()
            .map_err(|e| format!("read response body failed: {e}"))?;

        Ok(text)
    })
    .await
}

/// Generic HTTP proxy – supports GET/POST with custom headers, bypasses CORS for the webview.
/// `method`: "GET" | "POST"
/// `headers`: JSON object of header key-value pairs, e.g. {"Authorization": "Bearer sk-xxx"}
/// `body`: optional request body string (for POST)
/// Returns `{ status, body }` as JSON string.
#[tauri::command]
async fn http_proxy_request(
    url: String,
    method: Option<String>,
    headers: Option<std::collections::HashMap<String, String>>,
    body: Option<String>,
    timeout_secs: Option<u64>,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let timeout = timeout_secs.unwrap_or(30);
        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(timeout))
            .user_agent("openakita-desktop/1.0")
            .build()
            .map_err(|e| format!("HTTP client error: {e}"))?;

        let m = method.as_deref().unwrap_or("GET").to_uppercase();
        let mut req_builder = match m.as_str() {
            "POST" => client.post(&url),
            "PUT" => client.put(&url),
            "DELETE" => client.delete(&url),
            _ => client.get(&url),
        };

        if let Some(h) = headers {
            for (k, v) in h {
                req_builder = req_builder.header(&k, &v);
            }
        }
        if let Some(b) = body {
            req_builder = req_builder.body(b);
        }

        let resp = req_builder
            .send()
            .map_err(|e| format!("HTTP {} failed ({}): {}", m, url, e))?;

        let status = resp.status().as_u16();
        let resp_body = resp
            .text()
            .map_err(|e| format!("read response body failed: {e}"))?;

        Ok(format!(
            "{{\"status\":{},\"body\":{}}}",
            status,
            serde_json::to_string(&resp_body).unwrap_or_else(|_| "\"\"".to_string())
        ))
    })
    .await
}

// ── Local backend fetch (proxy-safe) ─────────────────────────────────
//
// On macOS, Clash / V2Ray set a *system-level* proxy via Network Preferences.
// WKWebView's native fetch() and @tauri-apps/plugin-http's reqwest client
// both honour that proxy, causing requests to 127.0.0.1 to be routed through
// the external proxy server — which cannot reach the user's localhost.
//
// `.no_proxy()` on the reqwest Client builder **completely disables** all proxy
// detection (env vars, system-configuration, everything) so the request always
// goes directly to the local backend.
//
// The response body is streamed back to JS via a Tauri Channel, preserving
// SSE / chunked-transfer behaviour for the chat view.

#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data", rename_all = "camelCase")]
enum BackendFetchEvent {
    Chunk { text: String },
    Done,
    Error { message: String },
}

#[tauri::command]
async fn backend_fetch(
    on_event: tauri::ipc::Channel<BackendFetchEvent>,
    url: String,
    method: Option<String>,
    headers: Option<std::collections::HashMap<String, String>>,
    body: Option<String>,
    timeout_secs: Option<u64>,
) -> Result<serde_json::Value, String> {
    if !url.starts_with("http://127.0.0.1") && !url.starts_with("http://localhost") {
        return Err("backend_fetch only allows localhost URLs".into());
    }

    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .connect_timeout(std::time::Duration::from_secs(10));
    if let Some(t) = timeout_secs {
        builder = builder.timeout(std::time::Duration::from_secs(t));
    }
    let client = builder
        .build()
        .map_err(|e| format!("HTTP client error: {e}"))?;

    let m = method.as_deref().unwrap_or("GET").to_uppercase();
    let mut req = match m.as_str() {
        "POST" => client.post(&url),
        "PUT" => client.put(&url),
        "DELETE" => client.delete(&url),
        "PATCH" => client.patch(&url),
        _ => client.get(&url),
    };
    if let Some(h) = headers {
        for (k, v) in h {
            req = req.header(&k, &v);
        }
    }
    if let Some(b) = body {
        req = req.body(b);
    }

    let resp = req
        .send()
        .await
        .map_err(|e| format!("HTTP {} failed ({}): {}", m, url, e))?;

    let status = resp.status().as_u16();
    let resp_headers: std::collections::HashMap<String, String> = resp
        .headers()
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_str().unwrap_or("").to_string()))
        .collect();

    tauri::async_runtime::spawn(async move {
        let mut response = resp;
        loop {
            match response.chunk().await {
                Ok(Some(chunk)) => {
                    let text = String::from_utf8_lossy(&chunk).to_string();
                    if on_event.send(BackendFetchEvent::Chunk { text }).is_err() {
                        break;
                    }
                }
                Ok(None) => {
                    let _ = on_event.send(BackendFetchEvent::Done);
                    break;
                }
                Err(e) => {
                    let _ = on_event.send(BackendFetchEvent::Error {
                        message: e.to_string(),
                    });
                    break;
                }
            }
        }
    });

    Ok(serde_json::json!({
        "status": status,
        "headers": resp_headers,
    }))
}

/// Read a file from disk and return its contents as a base64 data-URL.
/// Used by the frontend to handle Tauri file-drop events (which provide paths, not File objects).
#[tauri::command]
async fn read_file_base64(path: String) -> Result<String, String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("File not found: {}", path));
    }
    let data = std::fs::read(p).map_err(|e| format!("Failed to read {}: {}", path, e))?;
    let mime = match p
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "svg" => "image/svg+xml",
        "pdf" => "application/pdf",
        "txt" | "md" => "text/plain",
        "json" => "application/json",
        "csv" => "text/csv",
        _ => "application/octet-stream",
    };
    let b64 = base64::engine::general_purpose::STANDARD.encode(&data);
    Ok(format!("data:{};base64,{}", mime, b64))
}

fn sanitize_download_filename(candidate: &str) -> String {
    let leaf = std::path::Path::new(candidate)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or(candidate);
    let sanitized: String = leaf
        .chars()
        .map(|ch| match ch {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            ch if ch.is_control() => '_',
            ch => ch,
        })
        .collect();
    let trimmed = sanitized.trim_matches(|ch| ch == ' ' || ch == '.');
    let name = if trimmed.is_empty() {
        "download"
    } else {
        trimmed
    };
    let stem = std::path::Path::new(name)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(name);
    let reserved = matches!(
        stem.to_ascii_uppercase().as_str(),
        "CON"
            | "PRN"
            | "AUX"
            | "NUL"
            | "COM1"
            | "COM2"
            | "COM3"
            | "COM4"
            | "COM5"
            | "COM6"
            | "COM7"
            | "COM8"
            | "COM9"
            | "LPT1"
            | "LPT2"
            | "LPT3"
            | "LPT4"
            | "LPT5"
            | "LPT6"
            | "LPT7"
            | "LPT8"
            | "LPT9"
    );
    if reserved {
        format!("_{name}")
    } else {
        name.to_string()
    }
}

fn unique_download_path(filename: &str) -> Result<std::path::PathBuf, String> {
    let downloads_dir = dirs_next::download_dir()
        .or_else(|| dirs_next::home_dir().map(|h| h.join("Downloads")))
        .ok_or_else(|| "Cannot determine Downloads directory".to_string())?;
    std::fs::create_dir_all(&downloads_dir)
        .map_err(|e| format!("Cannot create Downloads dir: {e}"))?;

    let safe_filename = sanitize_download_filename(filename);
    let stem = std::path::Path::new(&safe_filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("download")
        .to_string();
    let ext = std::path::Path::new(&safe_filename)
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| format!(".{s}"))
        .unwrap_or_default();
    let mut dest = downloads_dir.join(&safe_filename);
    let mut counter = 1u32;
    while dest.exists() {
        dest = downloads_dir.join(format!("{stem} ({counter}){ext}"));
        counter += 1;
    }
    Ok(dest)
}

/// Download a file from a URL and save it to the user's Downloads folder.
/// Returns the saved file path on success.
#[tauri::command]
async fn download_file(url: String, filename: String) -> Result<String, String> {
    let dest = unique_download_path(&filename)?;

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .no_proxy()
        .build()
        .map_err(|e| format!("Failed to create HTTP client: {e}"))?;
    let resp = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Download request failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("Download failed with status {}", resp.status()));
    }
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("Failed to read response body: {e}"))?;
    std::fs::write(&dest, &bytes).map_err(|e| format!("Failed to write file: {e}"))?;

    Ok(dest.to_string_lossy().to_string())
}

/// Copy an existing local file to the user's Downloads folder.
/// Returns the saved file path on success.
#[tauri::command]
fn copy_file_to_downloads(path: String, filename: Option<String>) -> Result<String, String> {
    let source = std::path::Path::new(&path);
    if !source.is_file() {
        return Err(format!("Source file does not exist: {path}"));
    }

    let source_name = source
        .file_name()
        .and_then(|s| s.to_str())
        .filter(|s| !s.trim().is_empty())
        .unwrap_or("download");
    let requested_name = filename
        .as_deref()
        .and_then(|name| std::path::Path::new(name).file_name())
        .and_then(|s| s.to_str())
        .filter(|s| !s.trim().is_empty())
        .unwrap_or(source_name);
    let dest = unique_download_path(requested_name)?;

    std::fs::copy(source, &dest).map_err(|e| format!("Failed to copy file: {e}"))?;

    Ok(dest.to_string_lossy().to_string())
}

/// Open the OS file manager and highlight the given file.
#[tauri::command]
fn show_item_in_folder(path: String) -> Result<(), String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("Path does not exist: {path}"));
    }
    #[cfg(target_os = "windows")]
    {
        let mut c = std::process::Command::new("explorer");
        c.args(["/select,", &path]);
        apply_no_window(&mut c);
        c.spawn()
            .map_err(|e| format!("Failed to open explorer: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .args(["-R", &path])
            .spawn()
            .map_err(|e| format!("Failed to reveal in Finder: {e}"))?;
    }
    #[cfg(target_os = "linux")]
    {
        if let Some(parent) = p.parent() {
            std::process::Command::new("xdg-open")
                .arg(parent)
                .spawn()
                .map_err(|e| format!("Failed to open file manager: {e}"))?;
        }
    }
    Ok(())
}

/// Open a local file with the system default application.
#[tauri::command]
fn open_file_with_default(path: String) -> Result<(), String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("File does not exist: {path}"));
    }
    #[cfg(target_os = "windows")]
    {
        let mut c = std::process::Command::new("cmd");
        c.args(["/C", "start", "", &path]);
        apply_no_window(&mut c);
        c.spawn().map_err(|e| format!("Failed to open file: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file: {e}"))?;
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open file: {e}"))?;
    }
    Ok(())
}

/// Export the workspace .env file. If `dest_path` is given (from a save dialog),
/// write there; otherwise fall back to Downloads with a timestamped name.
#[tauri::command]
fn export_env_backup(workspace_id: String, dest_path: Option<String>) -> Result<String, String> {
    let env_path = workspace_dir(&workspace_id).join(".env");
    if !env_path.exists() {
        return Err("No .env file found in workspace".to_string());
    }

    let dest = if let Some(p) = dest_path {
        PathBuf::from(p)
    } else {
        let downloads_dir = dirs_next::download_dir()
            .or_else(|| dirs_next::home_dir().map(|h| h.join("Downloads")))
            .ok_or_else(|| "Cannot determine Downloads directory".to_string())?;
        fs::create_dir_all(&downloads_dir)
            .map_err(|e| format!("Cannot create Downloads dir: {e}"))?;
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        downloads_dir.join(format!("openakita-env-backup-{ts}.env"))
    };

    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("Cannot create directory: {e}"))?;
    }

    fs::copy(&env_path, &dest).map_err(|e| format!("Failed to copy .env: {e}"))?;

    Ok(dest.to_string_lossy().to_string())
}

/// Export diagnostic bundle (logs, llm_debug, system info) as a zip.
/// If `dest_path` is given (from a save dialog), write there; otherwise fall back to Downloads.
#[tauri::command]
fn export_diagnostic_bundle(
    workspace_id: String,
    system_info_json: Option<String>,
    dest_path: Option<String>,
) -> Result<String, String> {
    let ws_dir = workspace_dir(&workspace_id);
    let logs_dir = ws_dir.join("logs");
    let llm_debug_dir = ws_dir.join("data").join("llm_debug");

    let dest = if let Some(p) = dest_path {
        PathBuf::from(p)
    } else {
        let downloads_dir = dirs_next::download_dir()
            .or_else(|| dirs_next::home_dir().map(|h| h.join("Downloads")))
            .ok_or_else(|| "Cannot determine Downloads directory".to_string())?;
        fs::create_dir_all(&downloads_dir)
            .map_err(|e| format!("Cannot create Downloads dir: {e}"))?;
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        downloads_dir.join(format!("openakita-diagnostic-{ts}.zip"))
    };

    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("Cannot create directory: {e}"))?;
    }

    let file = fs::File::create(&dest).map_err(|e| format!("Failed to create zip file: {e}"))?;
    let mut zip_writer = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);

    fn collect_files(dir: &Path) -> Vec<PathBuf> {
        let mut result = Vec::new();
        if let Ok(entries) = fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    result.extend(collect_files(&path));
                } else {
                    result.push(path);
                }
            }
        }
        result
    }

    fn add_dir_to_zip(
        zip_writer: &mut zip::ZipWriter<fs::File>,
        dir: &Path,
        prefix: &str,
        options: zip::write::SimpleFileOptions,
    ) -> Result<(), String> {
        if !dir.exists() {
            return Ok(());
        }
        for file_path in collect_files(dir) {
            if let Ok(rel) = file_path.strip_prefix(dir) {
                let name = format!("{}/{}", prefix, rel.to_string_lossy().replace('\\', "/"));
                zip_writer
                    .start_file(&name, options)
                    .map_err(|e| format!("zip start error: {e}"))?;
                let data = fs::read(&file_path).unwrap_or_default();
                zip_writer
                    .write_all(&data)
                    .map_err(|e| format!("zip write error: {e}"))?;
            }
        }
        Ok(())
    }

    fn add_dir_to_zip_capped(
        zip_writer: &mut zip::ZipWriter<fs::File>,
        dir: &Path,
        prefix: &str,
        options: zip::write::SimpleFileOptions,
        max_bytes: u64,
    ) -> Result<(), String> {
        if !dir.exists() {
            return Ok(());
        }
        let mut files = collect_files(dir);
        files.sort_by(|a, b| {
            let ma = fs::metadata(a).and_then(|m| m.modified()).ok();
            let mb = fs::metadata(b).and_then(|m| m.modified()).ok();
            mb.cmp(&ma)
        });
        let mut total: u64 = 0;
        for file_path in files {
            let sz = fs::metadata(&file_path).map(|m| m.len()).unwrap_or(0);
            if total + sz > max_bytes {
                continue;
            }
            if let Ok(rel) = file_path.strip_prefix(dir) {
                let name = format!("{}/{}", prefix, rel.to_string_lossy().replace('\\', "/"));
                zip_writer
                    .start_file(&name, options)
                    .map_err(|e| format!("zip start error: {e}"))?;
                let data = fs::read(&file_path).unwrap_or_default();
                zip_writer
                    .write_all(&data)
                    .map_err(|e| format!("zip write error: {e}"))?;
                total += sz;
            }
        }
        Ok(())
    }

    fn add_file_to_zip(
        zip_writer: &mut zip::ZipWriter<fs::File>,
        path: &Path,
        zip_name: &str,
        options: zip::write::SimpleFileOptions,
    ) -> Result<(), String> {
        if !path.exists() || !path.is_file() {
            return Ok(());
        }
        zip_writer
            .start_file(zip_name, options)
            .map_err(|e| format!("zip start error: {e}"))?;
        let data = fs::read(path).unwrap_or_default();
        zip_writer
            .write_all(&data)
            .map_err(|e| format!("zip write error: {e}"))?;
        Ok(())
    }

    // -- Logs (workspace) --
    add_dir_to_zip(&mut zip_writer, &logs_dir, "logs", options)?;

    // -- LLM debug data --
    add_dir_to_zip_capped(
        &mut zip_writer,
        &llm_debug_dir,
        "llm_debug",
        options,
        10 * 1024 * 1024,
    )?;

    // -- Debug data directories (capped per-dir) --
    let data_dir = ws_dir.join("data");
    add_dir_to_zip_capped(
        &mut zip_writer,
        &data_dir.join("delegation_logs"),
        "delegation_logs",
        options,
        2 * 1024 * 1024,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &data_dir.join("react_traces"),
        "react_traces",
        options,
        5 * 1024 * 1024,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &data_dir.join("traces"),
        "traces",
        options,
        2 * 1024 * 1024,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &data_dir.join("orgs"),
        "orgs",
        options,
        2 * 1024 * 1024,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &data_dir.join("tool_overflow"),
        "tool_overflow",
        options,
        2 * 1024 * 1024,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &data_dir.join("failure_analysis"),
        "failure_analysis",
        options,
        1 * 1024 * 1024,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &data_dir.join("retrospects"),
        "retrospects",
        options,
        1 * 1024 * 1024,
    )?;

    // -- Small state files --
    add_file_to_zip(
        &mut zip_writer,
        &data_dir.join("runtime_state.json"),
        "state/runtime_state.json",
        options,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &data_dir.join("sub_agent_states.json"),
        "state/sub_agent_states.json",
        options,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &data_dir.join("backend.heartbeat"),
        "state/backend.heartbeat",
        options,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &data_dir.join("sessions").join("sessions.json"),
        "state/sessions.json",
        options,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &data_dir.join("sessions").join("channel_registry.json"),
        "state/channel_registry.json",
        options,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &data_dir.join("scheduler").join("tasks.json"),
        "state/scheduler_tasks.json",
        options,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &data_dir.join("scheduler").join("executions.json"),
        "state/scheduler_executions.json",
        options,
    )?;

    // -- Global logs (frontend.log, crash.log, onboarding) --
    let global_logs = setup_logs_dir();
    add_file_to_zip(
        &mut zip_writer,
        &global_logs.join("frontend.log"),
        "global_logs/frontend.log",
        options,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &global_logs.join("crash.log"),
        "global_logs/crash.log",
        options,
    )?;
    for entry in fs::read_dir(&global_logs).into_iter().flatten().flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if name_str.starts_with("onboarding-") && name_str.ends_with(".log") {
            add_file_to_zip(
                &mut zip_writer,
                &entry.path(),
                &format!("global_logs/{}", name_str),
                options,
            )?;
        }
    }

    // -- Runtime diagnostics (available even when backend is down) --
    add_file_to_zip(
        &mut zip_writer,
        &runtime_manifest_path(),
        "runtime/manifest.json",
        options,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &runtime_logs_dir(),
        "runtime/logs",
        options,
        5 * 1024 * 1024,
    )?;
    add_dir_to_zip_capped(
        &mut zip_writer,
        &runtime_root_dir().join("reports"),
        "runtime/reports",
        options,
        5 * 1024 * 1024,
    )?;
    add_file_to_zip(
        &mut zip_writer,
        &bootstrap_resource_dir().join("manifest.json"),
        "bootstrap-manifest.json",
        options,
    )?;

    let port = read_workspace_api_port(&workspace_id).unwrap_or(18900);
    let pid_data = read_pid_file(&workspace_id);
    let runtime_summary = serde_json::json!({
        "desktop_version": env!("CARGO_PKG_VERSION"),
        "runtime_mode": read_runtime_manifest()
            .map(|m| if m.legacy_mode { "legacy-fallback" } else { "dual-venv" })
            .unwrap_or("unknown"),
        "platform": std::env::consts::OS,
        "machine": std::env::consts::ARCH,
        "runtime_root": runtime_root_dir().to_string_lossy(),
        "app_python": runtime_venv_python_path(&app_venv_dir()).to_string_lossy(),
        "agent_python": runtime_venv_python_path(&agent_venv_dir()).to_string_lossy(),
        "toolchain_python": managed_python_seed_path().map(|p| p.to_string_lossy().to_string()),
        "node_toolchain": managed_node_seed_path().map(|p| p.to_string_lossy().to_string()),
        "legacy_mode": read_runtime_manifest().map(|m| m.legacy_mode).unwrap_or(false),
        "last_error": read_runtime_manifest().and_then(|m| m.last_error),
        "env_trust_source": "host-runtime",
        "subprocess_secret_scrub": true,
        "scrubbed_env_keys": ["PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", "VIRTUAL_ENV", "PIP_TARGET"],
        "api_port": port,
        "pid": pid_data.as_ref().map(|p| p.pid),
        "pid_running": pid_data.as_ref().map(|p| is_pid_file_valid(p)).unwrap_or(false),
        "health_status": if is_backend_http_healthy(Some(port)) { "ok" } else { "unknown" },
    });
    zip_writer
        .start_file("runtime-env-summary.json", options)
        .map_err(|e| format!("zip error: {e}"))?;
    zip_writer
        .write_all(
            serde_json::to_string_pretty(&runtime_summary)
                .unwrap_or_else(|_| "{}".into())
                .as_bytes(),
        )
        .map_err(|e| format!("zip write error: {e}"))?;

    zip_writer
        .start_file("port-18900.txt", options)
        .map_err(|e| format!("zip error: {e}"))?;
    zip_writer
        .write_all(
            format!(
                "workspace_id={}\napi_port={}\nhttp_healthy={}\npid={:?}\npid_running={}\n",
                workspace_id,
                port,
                is_backend_http_healthy(Some(port)),
                pid_data.as_ref().map(|p| p.pid),
                pid_data.as_ref().map(|p| is_pid_file_valid(p)).unwrap_or(false)
            )
            .as_bytes(),
        )
        .map_err(|e| format!("zip write error: {e}"))?;

    zip_writer
        .start_file("processes.txt", options)
        .map_err(|e| format!("zip error: {e}"))?;
    let proc_text = pid_data
        .as_ref()
        .map(|p| {
            format!(
                "managed_pid={}\nworkspace_id={}\nrunning={}\n",
                p.pid,
                workspace_id,
                is_pid_file_valid(p)
            )
        })
        .unwrap_or_else(|| "no pid file\n".into());
    zip_writer
        .write_all(proc_text.as_bytes())
        .map_err(|e| format!("zip write error: {e}"))?;

    // -- System info --
    if let Some(info) = system_info_json {
        zip_writer
            .start_file("system-info.json", options)
            .map_err(|e| format!("zip error: {e}"))?;
        zip_writer
            .write_all(info.as_bytes())
            .map_err(|e| format!("zip write error: {e}"))?;
    }

    zip_writer
        .finish()
        .map_err(|e| format!("zip finish error: {e}"))?;

    Ok(dest.to_string_lossy().to_string())
}

// ═══════════════════════════════════════════════════════════════════════
// Offline Feedback (when Python backend is down)
// ═══════════════════════════════════════════════════════════════════════

const DEFAULT_FEEDBACK_ENDPOINT: &str = "https://feedback-openakita.fzstack.com";
const DEFAULT_CAPTCHA_SCENE_ID: &str = "jkyrkj0w";
const DEFAULT_CAPTCHA_PREFIX: &str = "yiqg72";

fn pending_feedback_path() -> PathBuf {
    openakita_root_dir().join("pending_feedback.json")
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct PendingFeedbackRecord {
    report_id: String,
    feedback_token: Option<String>,
    title: String,
    report_type: String,
    contact_email: String,
    submitted_at: String,
    issue_url: Option<String>,
}

/// Read feedback endpoint from workspace config.yaml, falling back to default.
fn read_feedback_endpoint(workspace_id: &str) -> String {
    let cfg_path = workspace_dir(workspace_id).join("config.yaml");
    if let Ok(content) = fs::read_to_string(&cfg_path) {
        for line in content.lines() {
            let trimmed = line.trim();
            if trimmed.starts_with("bug_report_endpoint:") {
                let val = trimmed
                    .trim_start_matches("bug_report_endpoint:")
                    .trim()
                    .trim_matches('"')
                    .trim_matches('\'');
                if !val.is_empty() {
                    return val.to_string();
                }
            }
        }
    }
    DEFAULT_FEEDBACK_ENDPOINT.to_string()
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FeedbackImage {
    filename: String,
    data_base64: String,
}

/// Build a feedback ZIP with diagnostic data, metadata, and optional images.
/// Returns the path to the generated ZIP file.
#[tauri::command]
fn build_feedback_zip(
    workspace_id: String,
    report_id: String,
    title: String,
    description: String,
    report_type: String,
    steps: Option<String>,
    contact_email: Option<String>,
    images: Option<Vec<FeedbackImage>>,
) -> Result<String, String> {
    let ws_dir = workspace_dir(&workspace_id);
    let temp_dir = openakita_root_dir().join("temp-feedback");
    fs::create_dir_all(&temp_dir).map_err(|e| format!("mkdir error: {e}"))?;
    let dest = temp_dir.join(format!("{report_id}.zip"));

    let file = fs::File::create(&dest).map_err(|e| format!("create zip: {e}"))?;
    let mut zw = zip::ZipWriter::new(file);
    let opts = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);

    // --- metadata.json ---
    let now = {
        let d = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let secs = d % 60;
        let mins = (d / 60) % 60;
        let hrs = (d / 3600) % 24;
        let days = d / 86400;
        let (y, m, day) = civil_from_days(days as i64);
        format!("{y:04}-{m:02}-{day:02}T{hrs:02}:{mins:02}:{secs:02}Z")
    };
    let metadata = serde_json::json!({
        "report_id": report_id,
        "type": report_type,
        "title": title,
        "description": description,
        "steps": steps.unwrap_or_default(),
        "created_at": now,
        "submitted_via": "tauri_offline",
        "contact": { "email": contact_email.clone().unwrap_or_default() },
        "system_info": {
            "os": std::env::consts::OS,
            "arch": std::env::consts::ARCH,
        }
    });
    zw.start_file("metadata.json", opts)
        .map_err(|e| format!("zip: {e}"))?;
    zw.write_all(
        serde_json::to_string_pretty(&metadata)
            .unwrap_or_default()
            .as_bytes(),
    )
    .map_err(|e| format!("zip write: {e}"))?;

    // --- images ---
    if let Some(imgs) = images {
        for (i, img) in imgs.iter().enumerate() {
            if let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(&img.data_base64) {
                let name = if img.filename.is_empty() {
                    format!("images/image_{i}.png")
                } else {
                    format!("images/{}", img.filename)
                };
                zw.start_file(&name, opts)
                    .map_err(|e| format!("zip: {e}"))?;
                let _ = zw.write_all(&bytes);
            }
        }
    }

    // --- Reuse diagnostic collection logic (same as export_diagnostic_bundle) ---
    fn collect_files_recursive(dir: &Path) -> Vec<PathBuf> {
        let mut result = Vec::new();
        if let Ok(entries) = fs::read_dir(dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_dir() {
                    result.extend(collect_files_recursive(&p));
                } else {
                    result.push(p);
                }
            }
        }
        result
    }
    fn zip_add_dir(
        zw: &mut zip::ZipWriter<fs::File>,
        dir: &Path,
        prefix: &str,
        opts: zip::write::SimpleFileOptions,
    ) {
        if !dir.exists() {
            return;
        }
        for fp in collect_files_recursive(dir) {
            if let Ok(rel) = fp.strip_prefix(dir) {
                let name = format!("{}/{}", prefix, rel.to_string_lossy().replace('\\', "/"));
                if zw.start_file(&name, opts).is_ok() {
                    let _ = zw.write_all(&fs::read(&fp).unwrap_or_default());
                }
            }
        }
    }
    fn zip_add_dir_capped(
        zw: &mut zip::ZipWriter<fs::File>,
        dir: &Path,
        prefix: &str,
        opts: zip::write::SimpleFileOptions,
        max_bytes: u64,
    ) {
        if !dir.exists() {
            return;
        }
        let mut files = collect_files_recursive(dir);
        files.sort_by(|a, b| {
            let ma = fs::metadata(a).and_then(|m| m.modified()).ok();
            let mb = fs::metadata(b).and_then(|m| m.modified()).ok();
            mb.cmp(&ma)
        });
        let mut total: u64 = 0;
        for fp in files {
            let sz = fs::metadata(&fp).map(|m| m.len()).unwrap_or(0);
            if total + sz > max_bytes {
                continue;
            }
            if let Ok(rel) = fp.strip_prefix(dir) {
                let name = format!("{}/{}", prefix, rel.to_string_lossy().replace('\\', "/"));
                if zw.start_file(&name, opts).is_ok() {
                    let _ = zw.write_all(&fs::read(&fp).unwrap_or_default());
                    total += sz;
                }
            }
        }
    }
    fn zip_add_file(
        zw: &mut zip::ZipWriter<fs::File>,
        path: &Path,
        zip_name: &str,
        opts: zip::write::SimpleFileOptions,
    ) {
        if !path.exists() || !path.is_file() {
            return;
        }
        if zw.start_file(zip_name, opts).is_ok() {
            let _ = zw.write_all(&fs::read(path).unwrap_or_default());
        }
    }

    let logs_dir = ws_dir.join("logs");
    let data_dir = ws_dir.join("data");
    let llm_debug_dir = data_dir.join("llm_debug");

    zip_add_dir(&mut zw, &logs_dir, "logs", opts);
    zip_add_dir_capped(&mut zw, &llm_debug_dir, "llm_debug", opts, 10 * 1024 * 1024);
    zip_add_dir_capped(
        &mut zw,
        &data_dir.join("delegation_logs"),
        "delegation_logs",
        opts,
        2 * 1024 * 1024,
    );
    zip_add_dir_capped(
        &mut zw,
        &data_dir.join("react_traces"),
        "react_traces",
        opts,
        5 * 1024 * 1024,
    );
    zip_add_dir_capped(
        &mut zw,
        &data_dir.join("traces"),
        "traces",
        opts,
        2 * 1024 * 1024,
    );
    zip_add_dir_capped(
        &mut zw,
        &data_dir.join("orgs"),
        "orgs",
        opts,
        2 * 1024 * 1024,
    );
    zip_add_dir_capped(
        &mut zw,
        &data_dir.join("tool_overflow"),
        "tool_overflow",
        opts,
        2 * 1024 * 1024,
    );
    zip_add_dir_capped(
        &mut zw,
        &data_dir.join("failure_analysis"),
        "failure_analysis",
        opts,
        1 * 1024 * 1024,
    );
    zip_add_dir_capped(
        &mut zw,
        &data_dir.join("retrospects"),
        "retrospects",
        opts,
        1 * 1024 * 1024,
    );

    zip_add_file(
        &mut zw,
        &data_dir.join("runtime_state.json"),
        "state/runtime_state.json",
        opts,
    );
    zip_add_file(
        &mut zw,
        &data_dir.join("sub_agent_states.json"),
        "state/sub_agent_states.json",
        opts,
    );
    zip_add_file(
        &mut zw,
        &data_dir.join("backend.heartbeat"),
        "state/backend.heartbeat",
        opts,
    );
    zip_add_file(
        &mut zw,
        &data_dir.join("sessions").join("sessions.json"),
        "state/sessions.json",
        opts,
    );
    zip_add_file(
        &mut zw,
        &data_dir.join("sessions").join("channel_registry.json"),
        "state/channel_registry.json",
        opts,
    );
    zip_add_file(
        &mut zw,
        &data_dir.join("scheduler").join("tasks.json"),
        "state/scheduler_tasks.json",
        opts,
    );
    zip_add_file(
        &mut zw,
        &data_dir.join("scheduler").join("executions.json"),
        "state/scheduler_executions.json",
        opts,
    );

    let global_logs = setup_logs_dir();
    zip_add_file(
        &mut zw,
        &global_logs.join("frontend.log"),
        "global_logs/frontend.log",
        opts,
    );
    zip_add_file(
        &mut zw,
        &global_logs.join("crash.log"),
        "global_logs/crash.log",
        opts,
    );
    for entry in fs::read_dir(&global_logs).into_iter().flatten().flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        if name_str.starts_with("onboarding-") && name_str.ends_with(".log") {
            zip_add_file(
                &mut zw,
                &entry.path(),
                &format!("global_logs/{name_str}"),
                opts,
            );
        }
    }

    zw.finish().map_err(|e| format!("zip finish: {e}"))?;
    Ok(dest.to_string_lossy().to_string())
}

/// Simple days-since-epoch to civil date (year, month, day).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// Upload a feedback ZIP to the cloud FC endpoint (3-phase: prepare → OSS PUT → complete).
/// Returns { reportId, feedbackToken, issueUrl } on success.
#[tauri::command]
fn upload_feedback_to_cloud(
    workspace_id: String,
    zip_path: String,
    report_id: String,
    report_type: String,
    title: String,
    summary: String,
    captcha_verify_param: String,
    contact_email: String,
) -> Result<serde_json::Value, String> {
    let endpoint = read_feedback_endpoint(&workspace_id);
    if endpoint.is_empty() {
        return Err("Feedback endpoint not configured".into());
    }
    let zip_bytes = fs::read(&zip_path).map_err(|e| format!("read zip: {e}"))?;
    let _ = fs::remove_file(&zip_path);
    if zip_bytes.len() > 30 * 1024 * 1024 {
        return Err(format!(
            "ZIP too large: {:.1} MB (max 30 MB)",
            zip_bytes.len() as f64 / 1048576.0
        ));
    }

    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(180))
        .build()
        .map_err(|e| format!("http client: {e}"))?;

    let base = endpoint.trim_end_matches('/');

    fn truncate_chars(s: &str, max_chars: usize) -> &str {
        match s.char_indices().nth(max_chars) {
            Some((idx, _)) => &s[..idx],
            None => s,
        }
    }

    // Phase 1: prepare
    let prepare_resp = client
        .post(format!("{base}/prepare"))
        .json(&serde_json::json!({
            "report_id": report_id,
            "title": truncate_chars(&title, 200),
            "type": report_type,
            "summary": truncate_chars(&summary, 2000),
            "system_info": format!("OS: {} {}", std::env::consts::OS, std::env::consts::ARCH),
            "captcha_verify_param": captcha_verify_param,
            "contact_email": contact_email,
        }))
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .map_err(|e| format!("prepare failed: {e}"))?;

    if prepare_resp.status().as_u16() == 429 {
        return Err("Rate limit, please try later".into());
    }
    if prepare_resp.status().as_u16() == 403 {
        return Err("CAPTCHA verification failed".into());
    }
    if prepare_resp.status().is_client_error() || prepare_resp.status().is_server_error() {
        let text = prepare_resp.text().unwrap_or_default();
        return Err(format!("Cloud error: {}", &text[..text.len().min(200)]));
    }

    let prepare_data: serde_json::Value = prepare_resp
        .json()
        .map_err(|e| format!("parse prepare: {e}"))?;
    let upload_url = prepare_data["upload_url"]
        .as_str()
        .ok_or("missing upload_url")?;
    let report_date = prepare_data["report_date"].as_str().unwrap_or("");

    // Phase 2: OSS upload
    let oss_resp = client
        .put(upload_url)
        .header("Content-Length", zip_bytes.len().to_string())
        .body(zip_bytes)
        .send()
        .map_err(|e| format!("OSS upload failed: {e}"))?;

    if oss_resp.status().is_client_error() || oss_resp.status().is_server_error() {
        return Err(format!("OSS upload error: {}", oss_resp.status()));
    }

    // Phase 3: complete
    let complete_resp = client
        .post(format!("{base}/complete/{report_id}"))
        .json(&serde_json::json!({ "report_date": report_date }))
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .map_err(|e| format!("complete failed: {e}"))?;

    let mut feedback_token: Option<String> = None;
    let mut issue_url: Option<String> = None;
    if complete_resp.status().is_success() {
        if let Ok(data) = complete_resp.json::<serde_json::Value>() {
            feedback_token = data["feedback_token"].as_str().map(|s| s.to_string());
            issue_url = data["issue_url"].as_str().map(|s| s.to_string());
        }
    }

    Ok(serde_json::json!({
        "reportId": report_id,
        "feedbackToken": feedback_token,
        "issueUrl": issue_url,
    }))
}

/// Save a pending feedback record to JSON file for later import by Python backend.
#[tauri::command]
fn save_pending_feedback(record: PendingFeedbackRecord) -> Result<(), String> {
    let path = pending_feedback_path();
    let mut records: Vec<PendingFeedbackRecord> = if path.exists() {
        let data = fs::read_to_string(&path).unwrap_or_else(|_| "[]".to_string());
        serde_json::from_str(&data).unwrap_or_default()
    } else {
        Vec::new()
    };
    records.push(record);

    let tmp = path.with_extension("json.tmp");
    fs::write(
        &tmp,
        serde_json::to_string_pretty(&records).unwrap_or_else(|_| "[]".into()),
    )
    .map_err(|e| format!("write pending: {e}"))?;
    fs::rename(&tmp, &path).map_err(|e| format!("rename pending: {e}"))?;
    Ok(())
}

/// Get feedback config (captcha ids) when backend is offline.
#[tauri::command]
fn get_feedback_config_offline(workspace_id: String) -> serde_json::Value {
    let cfg_path = workspace_dir(&workspace_id).join("config.yaml");
    let mut scene_id = DEFAULT_CAPTCHA_SCENE_ID.to_string();
    let mut prefix = DEFAULT_CAPTCHA_PREFIX.to_string();
    if let Ok(content) = fs::read_to_string(&cfg_path) {
        for line in content.lines() {
            let t = line.trim();
            if t.starts_with("captcha_scene_id:") {
                let v = t
                    .trim_start_matches("captcha_scene_id:")
                    .trim()
                    .trim_matches('"')
                    .trim_matches('\'');
                if !v.is_empty() {
                    scene_id = v.to_string();
                }
            }
            if t.starts_with("captcha_prefix:") {
                let v = t
                    .trim_start_matches("captcha_prefix:")
                    .trim()
                    .trim_matches('"')
                    .trim_matches('\'');
                if !v.is_empty() {
                    prefix = v.to_string();
                }
            }
        }
    }
    serde_json::json!({
        "captcha_scene_id": scene_id,
        "captcha_prefix": prefix,
    })
}

/// Open an external URL in the OS default browser.
#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let url = url.trim();
    if url.is_empty() {
        return Err("URL is empty".to_string());
    }

    #[cfg(target_os = "windows")]
    {
        // Avoid `cmd /C start`: URLs from WeChat articles often contain `&`,
        // which cmd.exe treats as a command separator and truncates the link.
        let mut c = std::process::Command::new("rundll32");
        c.args(["url.dll,FileProtocolHandler", url]);
        apply_no_window(&mut c);
        c.spawn().map_err(|e| format!("Failed to open URL: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&url)
            .spawn()
            .map_err(|e| format!("Failed to open URL: {e}"))?;
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&url)
            .spawn()
            .map_err(|e| format!("Failed to open URL: {e}"))?;
    }
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════
// CLI 命令注册（跨平台）
// ═══════════════════════════════════════════════════════════════════════

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct CliConfig {
    commands: Vec<String>,
    add_to_path: bool,
    bin_dir: String,
    installed_at: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct CliStatus {
    registered_commands: Vec<String>,
    in_path: bool,
    bin_dir: String,
}

/// 获取 CLI bin 目录路径
fn cli_bin_dir() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        // Windows: 使用安装目录下的 bin/ 子目录
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));
        exe_dir.join("bin")
    }
    #[cfg(not(target_os = "windows"))]
    {
        // macOS / Linux: 使用 ~/.openakita/bin/
        openakita_root_dir().join("bin")
    }
}

/// 获取后端可执行文件的绝对路径
fn cli_backend_exe_path() -> Result<PathBuf, String> {
    let bundled_dir = bundled_backend_dir();
    let exe = if cfg!(windows) {
        bundled_dir.join("openakita-server.exe")
    } else {
        bundled_dir.join("openakita-server")
    };
    if exe.exists() {
        return Ok(exe);
    }
    // 降级：尝试 venv 模式（开发环境），先 python3 再 python
    let venv_base = openakita_root_dir().join("venv");
    let venv_py = if cfg!(windows) {
        venv_base.join("Scripts").join("python.exe")
    } else {
        let py3 = venv_base.join("bin").join("python3");
        if py3.exists() {
            py3
        } else {
            venv_base.join("bin").join("python")
        }
    };
    if venv_py.exists() {
        return Ok(venv_py);
    }
    eprintln!(
        "[cli_backend_exe_path] not found. checked:\n  bundled: {}\n  venv: {}",
        exe.display(),
        venv_py.display(),
    );
    Err(format!(
        "未找到后端可执行文件（openakita-server 或 venv python）\n\
         已检查: {} | {}",
        exe.display(),
        venv_py.display(),
    ))
}

/// 读取 CLI 配置文件
fn read_cli_config() -> Option<CliConfig> {
    let path = openakita_root_dir().join("cli.json");
    if !path.exists() {
        return None;
    }
    let content = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str(&content).ok()
}

/// 写入 CLI 配置文件
fn write_cli_config(config: &CliConfig) -> Result<(), String> {
    let path = openakita_root_dir().join("cli.json");
    let content =
        serde_json::to_string_pretty(config).map_err(|e| format!("序列化 CLI 配置失败: {e}"))?;
    std::fs::write(&path, content).map_err(|e| format!("写入 cli.json 失败: {e}"))?;
    Ok(())
}

/// 生成 wrapper 脚本内容
fn generate_wrapper_content(backend_exe: &Path) -> String {
    #[cfg(target_os = "windows")]
    {
        let _ = backend_exe; // Windows 使用相对路径，不需要绝对路径
        format!(
            "@echo off\r\n\"%~dp0..\\resources\\openakita-server\\openakita-server.exe\" %*\r\n"
        )
    }
    #[cfg(not(target_os = "windows"))]
    {
        let exe_path = backend_exe.to_string_lossy();
        format!(
            "#!/bin/sh\n# OpenAkita CLI wrapper - managed by OpenAkita Desktop\nexec \"{}\" \"$@\"\n",
            exe_path
        )
    }
}

/// 创建 wrapper 脚本文件
fn create_wrapper_script(bin_dir: &Path, cmd_name: &str, backend_exe: &Path) -> Result<(), String> {
    let content = generate_wrapper_content(backend_exe);

    #[cfg(target_os = "windows")]
    let file_path = bin_dir.join(format!("{}.cmd", cmd_name));
    #[cfg(not(target_os = "windows"))]
    let file_path = bin_dir.join(cmd_name);

    std::fs::write(&file_path, &content)
        .map_err(|e| format!("写入 {} 失败: {e}", file_path.display()))?;

    // macOS / Linux: 设置可执行权限
    #[cfg(not(target_os = "windows"))]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o755);
        std::fs::set_permissions(&file_path, perms)
            .map_err(|e| format!("chmod {} 失败: {e}", file_path.display()))?;
    }

    Ok(())
}

/// 删除 wrapper 脚本文件
fn remove_wrapper_script(bin_dir: &Path, cmd_name: &str) {
    #[cfg(target_os = "windows")]
    let file_path = bin_dir.join(format!("{}.cmd", cmd_name));
    #[cfg(not(target_os = "windows"))]
    let file_path = bin_dir.join(cmd_name);

    let _ = std::fs::remove_file(&file_path);
}

// ── PATH 操作：Windows ──

#[cfg(target_os = "windows")]
fn windows_add_to_path(bin_dir: &Path) -> Result<(), String> {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();
    let bin_norm = bin_str.trim_end_matches('\\');

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let key = hkcu
        .open_subkey_with_flags("Environment", KEY_READ | KEY_WRITE)
        .map_err(|e| format!("无法打开用户环境变量注册表: {e}"))?;

    let current_path = read_path_value(&key)?;

    if current_path
        .split(';')
        .any(|p| p.trim_end_matches('\\').eq_ignore_ascii_case(bin_norm))
    {
        return Ok(());
    }

    let new_path = if current_path.is_empty() {
        bin_str
    } else {
        format!("{};{}", current_path, bin_str)
    };
    if new_path.len() > 2047 {
        return Err("PATH 环境变量已接近长度限制 (2048)，无法追加".into());
    }

    write_path_value(&key, &new_path)?;
    windows_broadcast_env_change();

    Ok(())
}

#[cfg(target_os = "windows")]
fn windows_remove_from_path(bin_dir: &Path) -> Result<(), String> {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();
    let bin_norm = bin_str.trim_end_matches('\\');
    let mut modified = false;

    for (hive_predef, subkey_path) in [
        (
            HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (HKEY_CURRENT_USER, "Environment"),
    ] {
        let hive = RegKey::predef(hive_predef);
        if let Ok(key) = hive.open_subkey_with_flags(subkey_path, KEY_READ | KEY_WRITE) {
            let current_path = read_path_value(&key).unwrap_or_default();
            if current_path.is_empty() {
                continue;
            }
            let new_paths: Vec<&str> = current_path
                .split(';')
                .filter(|p| !p.trim_end_matches('\\').eq_ignore_ascii_case(bin_norm))
                .collect();
            let new_path = new_paths.join(";");
            if new_path != current_path {
                let _ = write_path_value(&key, &new_path);
                modified = true;
            }
        }
    }

    if modified {
        windows_broadcast_env_change();
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn windows_is_in_path(bin_dir: &Path) -> bool {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();
    let bin_norm = bin_str.trim_end_matches('\\');

    for (hive_predef, subkey_path) in [
        (
            HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (HKEY_CURRENT_USER, "Environment"),
    ] {
        let hive = RegKey::predef(hive_predef);
        if let Ok(key) = hive.open_subkey_with_flags(subkey_path, KEY_READ) {
            if let Ok(current_path) = read_path_value(&key) {
                if current_path
                    .split(';')
                    .any(|p| p.trim_end_matches('\\').eq_ignore_ascii_case(bin_norm))
                {
                    return true;
                }
            }
        }
    }
    false
}

#[cfg(target_os = "windows")]
fn windows_broadcast_env_change() {
    use std::ffi::CString;
    // SendMessageTimeout(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", ...)
    #[link(name = "user32")]
    extern "system" {
        fn SendMessageTimeoutA(
            hwnd: isize,
            msg: u32,
            w_param: usize,
            l_param: *const u8,
            fu_flags: u32,
            u_timeout: u32,
            lpdw_result: *mut usize,
        ) -> isize;
    }
    let env_str = CString::new("Environment").unwrap();
    unsafe {
        let mut result: usize = 0;
        // HWND_BROADCAST = 0xFFFF, WM_SETTINGCHANGE = 0x001A, SMTO_ABORTIFHUNG = 0x0002
        SendMessageTimeoutA(
            0xFFFF_isize,
            0x001A,
            0,
            env_str.as_ptr() as *const u8,
            0x0002,
            5000,
            &mut result,
        );
    }
}

/// 从注册表中读取 PATH 值的原始内容（不展开 %...% 环境变量引用）
#[cfg(target_os = "windows")]
fn read_path_value(key: &winreg::RegKey) -> Result<String, String> {
    use winreg::enums::RegType;
    match key.get_raw_value("Path") {
        Ok(raw) => {
            if raw.vtype != RegType::REG_SZ && raw.vtype != RegType::REG_EXPAND_SZ {
                return Err(format!("PATH 注册表值类型异常: {:?}", raw.vtype));
            }
            let wide: Vec<u16> = raw
                .bytes
                .chunks_exact(2)
                .map(|c| u16::from_le_bytes([c[0], c[1]]))
                .collect();
            Ok(String::from_utf16_lossy(&wide)
                .trim_end_matches('\0')
                .to_string())
        }
        Err(_) => Ok(String::new()),
    }
}

/// 将 PATH 值以 REG_EXPAND_SZ 类型写入注册表（保留 %...% 环境变量引用能力）
#[cfg(target_os = "windows")]
fn write_path_value(key: &winreg::RegKey, value: &str) -> Result<(), String> {
    use winreg::enums::RegType;
    use winreg::RegValue;
    let wide: Vec<u16> = value.encode_utf16().chain(std::iter::once(0)).collect();
    let bytes: Vec<u8> = wide.iter().flat_map(|&w| w.to_le_bytes()).collect();
    key.set_raw_value(
        "Path",
        &RegValue {
            bytes,
            vtype: RegType::REG_EXPAND_SZ,
        },
    )
    .map_err(|e| format!("写入 PATH 注册表失败: {e}"))
}

// ── PATH 操作：macOS / Linux ──

#[cfg(not(target_os = "windows"))]
fn unix_add_to_path(bin_dir: &Path) -> Result<(), String> {
    let bin_str = bin_dir.to_string_lossy().to_string();
    let marker_start = "# >>> openakita cli >>>";
    let marker_end = "# <<< openakita cli <<<";
    let block = format!(
        "{}\nexport PATH=\"{}:$PATH\"\n{}\n",
        marker_start, bin_str, marker_end
    );

    // 确定要写入的 shell profile 文件
    let home = home_dir().ok_or("无法获取 HOME 目录")?;
    let profiles = get_shell_profiles(&home);

    for profile in &profiles {
        // 读取现有内容，检查是否已存在标记
        let existing = std::fs::read_to_string(profile).unwrap_or_default();
        if existing.contains(marker_start) {
            // 已有标记，替换旧的 block
            let lines: Vec<&str> = existing.lines().collect();
            let mut new_lines: Vec<&str> = Vec::new();
            let mut in_block = false;
            for line in &lines {
                if line.contains(marker_start) {
                    in_block = true;
                    continue;
                }
                if line.contains(marker_end) {
                    in_block = false;
                    continue;
                }
                if !in_block {
                    new_lines.push(line);
                }
            }
            let mut content = new_lines.join("\n");
            if !content.ends_with('\n') {
                content.push('\n');
            }
            content.push_str(&block);
            std::fs::write(profile, content)
                .map_err(|e| format!("写入 {} 失败: {e}", profile.display()))?;
        } else {
            // 追加到文件末尾
            let mut content = existing;
            if !content.is_empty() && !content.ends_with('\n') {
                content.push('\n');
            }
            content.push_str(&block);
            std::fs::write(profile, content)
                .map_err(|e| format!("写入 {} 失败: {e}", profile.display()))?;
        }
    }

    // Linux: 额外尝试在 ~/.local/bin/ 创建 symlink
    #[cfg(target_os = "linux")]
    {
        let local_bin = home.join(".local").join("bin");
        if local_bin.exists() || std::fs::create_dir_all(&local_bin).is_ok() {
            // 读取 CLI 配置，为每个注册的命令创建 symlink
            if let Some(config) = read_cli_config() {
                for cmd in &config.commands {
                    let src = bin_dir.join(cmd);
                    let dst = local_bin.join(cmd);
                    let _ = std::fs::remove_file(&dst); // 先删除旧的
                    let _ = std::os::unix::fs::symlink(&src, &dst);
                }
            }
        }
    }

    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn unix_remove_from_path(_bin_dir: &Path) -> Result<(), String> {
    let marker_start = "# >>> openakita cli >>>";
    let marker_end = "# <<< openakita cli <<<";

    let home = home_dir().ok_or("无法获取 HOME 目录")?;
    let profiles = get_shell_profiles(&home);

    for profile in &profiles {
        if !profile.exists() {
            continue;
        }
        let existing = std::fs::read_to_string(profile).unwrap_or_default();
        if !existing.contains(marker_start) {
            continue;
        }
        let lines: Vec<&str> = existing.lines().collect();
        let mut new_lines: Vec<&str> = Vec::new();
        let mut in_block = false;
        for line in &lines {
            if line.contains(marker_start) {
                in_block = true;
                continue;
            }
            if line.contains(marker_end) {
                in_block = false;
                continue;
            }
            if !in_block {
                new_lines.push(line);
            }
        }
        let content = new_lines.join("\n");
        let _ = std::fs::write(profile, content);
    }

    // Linux: 清理 ~/.local/bin/ 中的 symlink
    #[cfg(target_os = "linux")]
    {
        let local_bin = home.join(".local").join("bin");
        if let Some(config) = read_cli_config() {
            for cmd in &config.commands {
                let dst = local_bin.join(cmd);
                let _ = std::fs::remove_file(&dst);
            }
        }
    }

    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn unix_is_in_path(bin_dir: &Path) -> bool {
    let marker_start = "# >>> openakita cli >>>";
    let home = match home_dir() {
        Some(h) => h,
        None => return false,
    };
    let profiles = get_shell_profiles(&home);
    for profile in &profiles {
        if let Ok(content) = std::fs::read_to_string(profile) {
            if content.contains(marker_start) {
                return true;
            }
        }
    }
    // 也检查当前运行时的 PATH
    if let Ok(path) = std::env::var("PATH") {
        let bin_str = bin_dir.to_string_lossy();
        if path.split(':').any(|p| p == bin_str.as_ref()) {
            return true;
        }
    }
    false
}

#[cfg(not(target_os = "windows"))]
fn get_shell_profiles(home: &Path) -> Vec<PathBuf> {
    let mut profiles = Vec::new();
    // zsh (macOS default, also common on Linux)
    let zshrc = home.join(".zshrc");
    profiles.push(zshrc);
    // bash
    #[cfg(target_os = "macos")]
    {
        profiles.push(home.join(".bash_profile"));
    }
    #[cfg(target_os = "linux")]
    {
        profiles.push(home.join(".bashrc"));
    }
    profiles
}

// ── Tauri 命令 ──

#[tauri::command]
fn register_cli(commands: Vec<String>, add_to_path: bool) -> Result<String, String> {
    if commands.is_empty() {
        return Err("至少需要选择一个命令名称".into());
    }

    // 验证命令名仅包含合法字符
    for cmd in &commands {
        if !cmd
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Err(format!("命令名 '{}' 包含非法字符", cmd));
        }
    }

    let bin_dir = cli_bin_dir();
    std::fs::create_dir_all(&bin_dir).map_err(|e| format!("创建 bin 目录失败: {e}"))?;

    // 获取后端可执行文件路径
    let backend_exe = cli_backend_exe_path()?;

    // 生成 wrapper 脚本
    for cmd_name in &commands {
        create_wrapper_script(&bin_dir, cmd_name, &backend_exe)?;
    }

    // PATH 注入
    if add_to_path {
        #[cfg(target_os = "windows")]
        windows_add_to_path(&bin_dir)?;

        #[cfg(not(target_os = "windows"))]
        unix_add_to_path(&bin_dir)?;
    }

    // 保存配置
    let config = CliConfig {
        commands: commands.clone(),
        add_to_path,
        bin_dir: bin_dir.to_string_lossy().to_string(),
        installed_at: {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            format!("{}", now)
        },
    };
    write_cli_config(&config)?;

    Ok(format!(
        "CLI 命令已注册: {}{}",
        commands.join(", "),
        if add_to_path {
            " (已添加到 PATH)"
        } else {
            ""
        }
    ))
}

#[tauri::command]
fn unregister_cli() -> Result<String, String> {
    let config = read_cli_config().ok_or("未找到 CLI 配置")?;
    let bin_dir = PathBuf::from(&config.bin_dir);

    // 删除 wrapper 脚本
    for cmd_name in &config.commands {
        remove_wrapper_script(&bin_dir, cmd_name);
    }

    // 从 PATH 移除
    if config.add_to_path {
        #[cfg(target_os = "windows")]
        windows_remove_from_path(&bin_dir)?;

        #[cfg(not(target_os = "windows"))]
        unix_remove_from_path(&bin_dir)?;
    }

    // 清理 bin 目录（如果为空）
    let _ = std::fs::remove_dir(&bin_dir);

    // 删除配置文件
    let config_path = openakita_root_dir().join("cli.json");
    let _ = std::fs::remove_file(&config_path);

    Ok("CLI 命令已注销".into())
}

#[tauri::command]
fn get_cli_status() -> Result<CliStatus, String> {
    let bin_dir = cli_bin_dir();

    if let Some(config) = read_cli_config() {
        // 验证 wrapper 脚本是否实际存在
        let existing_commands: Vec<String> = config
            .commands
            .iter()
            .filter(|cmd| {
                #[cfg(target_os = "windows")]
                let path = PathBuf::from(&config.bin_dir).join(format!("{}.cmd", cmd));
                #[cfg(not(target_os = "windows"))]
                let path = PathBuf::from(&config.bin_dir).join(cmd.as_str());
                path.exists()
            })
            .cloned()
            .collect();

        let in_path = {
            #[cfg(target_os = "windows")]
            {
                windows_is_in_path(&PathBuf::from(&config.bin_dir))
            }
            #[cfg(not(target_os = "windows"))]
            {
                unix_is_in_path(&PathBuf::from(&config.bin_dir))
            }
        };

        Ok(CliStatus {
            registered_commands: existing_commands,
            in_path,
            bin_dir: config.bin_dir,
        })
    } else {
        Ok(CliStatus {
            registered_commands: vec![],
            in_path: false,
            bin_dir: bin_dir.to_string_lossy().to_string(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bundled_backend_dir_returns_non_empty_path() {
        let dir = bundled_backend_dir();
        assert!(!dir.to_string_lossy().is_empty());
        assert!(
            dir.to_string_lossy().contains("openakita-server"),
            "bundled_backend_dir should contain 'openakita-server': {:?}",
            dir
        );
    }

    #[test]
    fn test_get_backend_executable_falls_back_to_venv() {
        let fake_venv = if cfg!(windows) {
            r"C:\nonexistent-test-venv-12345"
        } else {
            "/tmp/nonexistent-test-venv-12345"
        };
        let (exe, args) = get_backend_executable(fake_venv);
        // When bundled binary is missing, should return venv python path
        let exe_str = exe.to_string_lossy();
        assert!(
            exe_str.contains("python"),
            "fallback exe should contain 'python': {}",
            exe_str
        );
        assert!(args.contains(&"-m".to_string()));
        assert!(args.contains(&"openakita.main".to_string()));
        assert!(args.contains(&"serve".to_string()));
    }

    #[test]
    fn test_venv_python_path_platform_layout() {
        let dir = if cfg!(windows) {
            r"C:\Users\test\.openakita\venv"
        } else {
            "/home/test/.openakita/venv"
        };
        let py = venv_python_path(dir);
        if cfg!(windows) {
            assert!(py.to_string_lossy().contains("Scripts"));
            assert!(py.to_string_lossy().ends_with("python.exe"));
        } else {
            assert!(py.to_string_lossy().contains("bin"));
            assert!(py.to_string_lossy().ends_with("python"));
        }
    }

    #[test]
    fn test_venv_pythonw_path_consistent_with_python_path() {
        let dir = if cfg!(windows) {
            r"C:\Users\test\.openakita\venv"
        } else {
            "/home/test/.openakita/venv"
        };
        let py = venv_python_path(dir);
        let pyw = venv_pythonw_path(dir);
        // On Linux both should resolve to bin/python
        if cfg!(not(windows)) {
            assert_eq!(py, pyw);
        }
        // On Windows pythonw prefers pythonw.exe but falls back to python.exe
        // For non-existent dir it returns python.exe since pythonw.exe doesn't exist
        if cfg!(windows) {
            assert!(pyw.to_string_lossy().contains("python"));
        }
    }

    #[test]
    fn test_check_backend_availability_with_nonexistent_venv() {
        let fake = if cfg!(windows) {
            r"C:\nonexistent-venv-test-99999"
        } else {
            "/tmp/nonexistent-venv-test-99999"
        };
        let result = check_backend_availability(fake.to_string());
        assert!(!result.venv_ready);
        assert!(!result.venv_checked.is_empty());
        assert!(!result.bundled_checked.is_empty());
    }

    #[test]
    fn test_stale_heartbeat_cleanup_requires_http_failure() {
        assert!(!should_cleanup_stale_heartbeat(Some(true), true));
        assert!(should_cleanup_stale_heartbeat(Some(true), false));
        assert!(!should_cleanup_stale_heartbeat(Some(false), false));
        assert!(!should_cleanup_stale_heartbeat(None, false));
    }

    #[test]
    fn test_cli_backend_exe_path_does_not_panic() {
        let result = cli_backend_exe_path();
        // In dev environment, may or may not find a backend
        assert!(result.is_ok() || result.is_err());
    }

    #[test]
    fn test_openakita_root_dir_is_valid() {
        let root = openakita_root_dir();
        assert!(!root.to_string_lossy().is_empty());
        // Should contain .openakita unless overridden by OPENAKITA_ROOT
        let root_str = root.to_string_lossy();
        assert!(
            root_str.contains(".openakita") || std::env::var("OPENAKITA_ROOT").is_ok(),
            "root dir should contain '.openakita' or OPENAKITA_ROOT should be set: {}",
            root_str
        );
    }

    #[test]
    fn test_data_root_rejects_drive_or_filesystem_root() {
        let root = if cfg!(windows) {
            PathBuf::from(r"D:\")
        } else {
            PathBuf::from("/")
        };
        assert!(!is_safe_openakita_data_root(&root));
        assert!(ensure_safe_openakita_data_root(&root).is_err());
    }

    #[test]
    fn test_data_root_rejects_home_directory() {
        if let Some(home) = home_dir() {
            assert!(!is_safe_openakita_data_root(&home));
            assert!(ensure_safe_openakita_data_root(&home).is_err());
        }
    }

    #[test]
    fn test_data_root_allows_dedicated_directory() {
        let dedicated = if cfg!(windows) {
            PathBuf::from(r"D:\OpenAkitaData\.openakita")
        } else {
            PathBuf::from("/tmp/openakita-data/.openakita")
        };
        assert!(is_safe_openakita_data_root(&dedicated));
        assert!(ensure_safe_openakita_data_root(&dedicated).is_ok());
    }

    #[test]
    fn test_cli_bin_dir_is_valid() {
        let dir = cli_bin_dir();
        assert!(!dir.to_string_lossy().is_empty());
        if cfg!(windows) {
            assert!(dir.to_string_lossy().contains("bin"));
        } else {
            assert!(dir.to_string_lossy().contains("bin"));
        }
    }
}
