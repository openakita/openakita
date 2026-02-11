#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use dirs_next::home_dir;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{Read, Seek, SeekFrom};
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

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ServicePidEntry {
    workspace_id: String,
    pid: u32,
    pid_file: String,
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
        let pid = fs::read_to_string(&p)
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok())
            .unwrap_or(0);
        if pid == 0 {
            continue;
        }
        out.push(ServicePidEntry {
            workspace_id: ws,
            pid,
            pid_file: p.to_string_lossy().to_string(),
        });
    }
    out
}

fn stop_service_pid_entry(ent: &ServicePidEntry) -> Result<(), String> {
    if is_pid_running(ent.pid) {
        kill_pid(ent.pid)?;
        // 等待最多 2s 确认退出
        for _ in 0..10 {
            if !is_pid_running(ent.pid) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
        if is_pid_running(ent.pid) {
            return Err(format!(
                "pid {} still running (workspace={})",
                ent.pid, ent.workspace_id
            ));
        }
    }
    let _ = fs::remove_file(PathBuf::from(&ent.pid_file));
    Ok(())
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
        pub fn Process32FirstW(
            hSnapshot: *mut std::ffi::c_void,
            lppe: *mut PROCESSENTRY32W,
        ) -> i32;
        pub fn Process32NextW(
            hSnapshot: *mut std::ffi::c_void,
            lppe: *mut PROCESSENTRY32W,
        ) -> i32;
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
        let handle =
            unsafe { win::OpenProcess(win::PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
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
        let status = Command::new("kill")
            .args(["-0", &pid.to_string()])
            .status();
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
            return Err(format!("TerminateProcess \u{5931}\u{8d25}\u{ff08}pid={}\u{ff09}", pid));
        }
        return Ok(());
    }
    #[cfg(not(windows))]
    {
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

        if unsafe { win::Process32FirstW(snap, &mut pe) } != 0 {
            loop {
                let name = String::from_utf16_lossy(
                    &pe.sz_exe_file[..pe
                        .sz_exe_file
                        .iter()
                        .position(|&c| c == 0)
                        .unwrap_or(260)],
                );
                let name_lower = name.to_ascii_lowercase();
                if name_lower.contains("python") {
                    python_pids.push(pe.th32_process_id);
                }
                if unsafe { win::Process32NextW(snap, &mut pe) } == 0 {
                    break;
                }
            }
        }
        unsafe {
            win::CloseHandle(snap);
        }

        // Step 2: 对每个 python 进程用 wmic 查命令行（只返回 ASCII 字段，无编码问题）
        for ppid in python_pids {
            let mut c = Command::new("cmd");
            c.args([
                "/C",
                &format!(
                    "wmic process where \"ProcessId={}\" get CommandLine /value",
                    ppid
                ),
            ]);
            apply_no_window(&mut c);
            if let Ok(out) = c.output() {
                let s = String::from_utf8_lossy(&out.stdout).to_lowercase();
                if s.contains("openakita") && s.contains("serve") {
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
        let _ = Command::new("pkill")
            .args(["-f", "openakita.*serve"])
            .status();
    }
    killed
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

    // 默认 .env：Setup Center 会按“你实际填写的字段”生成/维护。
    // 不再把完整模板复制进工作区，避免产生大量空值键（会导致 pydantic 解析失败/污染配置）。
    let env_path = dir.join(".env");
    if !env_path.exists() {
        let content = [
            "# OpenAkita 工作区环境变量（由 Setup Center 生成）",
            "#",
            "# 规则：",
            "# - 只会写入你在 Setup Center 里“填写/修改过”的键",
            "# - 你把某个值清空后保存，会从此文件删除该键",
            "# - 手动部署/完整模板请参考仓库 examples/.env.example",
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
        fs::write(&soul, DEFAULT_SOUL).map_err(|e| format!("write identity/SOUL.md failed: {e}"))?;
    }
    let agent_md = dir.join("identity").join("AGENT.md");
    if !agent_md.exists() {
        fs::write(&agent_md, DEFAULT_AGENT).map_err(|e| format!("write identity/AGENT.md failed: {e}"))?;
    }
    let user_md = dir.join("identity").join("USER.md");
    if !user_md.exists() {
        fs::write(&user_md, DEFAULT_USER).map_err(|e| format!("write identity/USER.md failed: {e}"))?;
    }
    let memory_md = dir.join("identity").join("MEMORY.md");
    if !memory_md.exists() {
        fs::write(&memory_md, DEFAULT_MEMORY).map_err(|e| format!("write identity/MEMORY.md failed: {e}"))?;
    }

    // 默认 llm_endpoints.json：用仓库内的 data/llm_endpoints.json.example 作为初始模板
    let llm = dir.join("data").join("llm_endpoints.json");
    if !llm.exists() {
        const DEFAULT_LLM_ENDPOINTS: &str = include_str!("../../../../data/llm_endpoints.json.example");
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
            openakita_service_log,
            openakita_list_skills,
            openakita_list_providers,
            openakita_list_models,
            openakita_version,
            openakita_health_check_endpoint,
            openakita_health_check_im,
            openakita_install_skill,
            openakita_uninstall_skill,
            openakita_list_marketplace,
            openakita_get_skill_config,
            fetch_pypi_versions
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

#[cfg(windows)]
fn apply_no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    // CREATE_NO_WINDOW: avoid flashing a black console window for spawned commands.
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn apply_no_window(_cmd: &mut Command) {}

async fn spawn_blocking_result<R: Send + 'static>(
    f: impl FnOnce() -> Result<R, String> + Send + 'static,
) -> Result<R, String> {
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|e| format!("后台任务失败（join error）: {e}"))?
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
    // Prefer pythonw.exe on Windows to avoid showing any console window.
    let py = venv_pythonw_path(&venv_dir);
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

    // Force UTF-8 output on Windows and make logs clean & realtime.
    // Without this, Rich may try to write unicode symbols (e.g. ✓) using GBK and crash.
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");
    // Disable colored / styled output to avoid ANSI escape codes in log files.
    cmd.env("NO_COLOR", "1");

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
        cmd.creation_flags(0x00000008u32 | 0x00000200u32 | 0x0800_0000u32); // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    }

    let child = cmd.spawn().map_err(|e| format!("spawn openakita serve failed: {e}"))?;
    let pid = child.id();
    fs::write(&pid_file, pid.to_string()).map_err(|e| format!("write pid file failed: {e}"))?;

    // Confirm the process is still alive shortly after spawning.
    std::thread::sleep(std::time::Duration::from_millis(500));
    if !is_pid_running(pid) {
        let _ = fs::remove_file(&pid_file);
        // Best-effort log tail for debugging.
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
        // 强制杀干净：如果杀不掉，要显式报错（避免 UI 显示“已停止”但后台仍残留）。
        if is_pid_running(pid) {
            kill_pid(pid)?;
            // 等待最多 2s 确认退出
            for _ in 0..10 {
                if !is_pid_running(pid) {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
            if is_pid_running(pid) {
                return Err(format!("failed to stop service: pid {pid} still running"));
            }
        }
    }
    let _ = fs::remove_file(&pid_file);
    Ok(ServiceStatus {
        running: false,
        pid: None,
        pid_file: pid_file.to_string_lossy().to_string(),
    })
}

#[tauri::command]
fn openakita_service_log(workspace_id: String, tail_bytes: Option<u64>) -> Result<ServiceLogChunk, String> {
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
    let len = f.metadata().map_err(|e| format!("stat log failed: {e}"))?.len();
    let start = len.saturating_sub(tail);
    let truncated = start > 0;
    f.seek(SeekFrom::Start(start))
        .map_err(|e| format!("seek log failed: {e}"))?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).map_err(|e| format!("read log failed: {e}"))?;
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
                // 退出前先确保后台 OpenAkita serve 已停止（三层保障：PID文件杀→孤儿扫描→最终确认）

                // 第 1 层：按 PID 文件逐一停止
                let entries = list_service_pids();
                for ent in &entries {
                    let _ = stop_service_pid_entry(ent);
                }

                // 第 2 层：兜底扫描所有命令行含 openakita serve 的 python 进程并杀掉
                kill_openakita_orphans();

                // 等一小段让进程退出
                std::thread::sleep(std::time::Duration::from_millis(600));

                // 第 3 层：最终确认——PID 文件里还有活的吗？再扫一次孤儿进程
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
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.show();
                        let _ = w.unminimize();
                        let _ = w.set_focus();
                    }
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
    apply_no_window(&mut c);
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
async fn install_embedded_python(python_series: Option<String>) -> Result<EmbeddedPythonInstallResult, String> {
    spawn_blocking_result(move || {
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
            let mut out =
                std::fs::File::create(&archive_path).map_err(|e| format!("create archive failed: {e}"))?;
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

        let py =
            find_python_executable(&install_dir).ok_or_else(|| "python executable not found after extract".to_string())?;
        Ok(EmbeddedPythonInstallResult {
            python_command: vec![py.to_string_lossy().to_string()],
            python_path: py.to_string_lossy().to_string(),
            install_dir: install_dir.to_string_lossy().to_string(),
            asset_name: asset.name,
            tag: latest.tag,
        })
    })
    .await
}

#[tauri::command]
async fn create_venv(python_command: Vec<String>, venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
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
        apply_no_window(&mut c);
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
        let py = venv_python_path(&venv_dir);
        if !py.exists() {
            return Err(format!("venv python not found: {}", py.to_string_lossy()));
        }

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

        // upgrade pip first (best-effort)
        emit_stage("升级 pip（best-effort）", 40);
        let mut up = Command::new(&py);
        apply_no_window(&mut up);
        up.env("PYTHONUTF8", "1");
        up.env("PYTHONIOENCODING", "utf-8");
        up.args(["-m", "pip", "install", "-U", "pip", "setuptools", "wheel"]);
        if let Some(url) = &index_url {
            up.args(["-i", url]);
        }
        let _ = run_streaming(up, "pip upgrade (best-effort)", &mut log, &emit_line);

        emit_stage("安装 openakita（pip）", 70);
        let mut c = Command::new(&py);
        apply_no_window(&mut c);
        c.env("PYTHONUTF8", "1");
        c.env("PYTHONIOENCODING", "utf-8");
        c.args(["-m", "pip", "install", "-U", &package_spec]);
        if let Some(url) = &index_url {
            c.args(["-i", url]);
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
        verify.env("PYTHONUTF8", "1");
        verify.env("PYTHONIOENCODING", "utf-8");
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
        let py = venv_python_path(&venv_dir);
        if !py.exists() {
            return Err(format!("venv python not found: {}", py.to_string_lossy()));
        }
        if package_name.trim().is_empty() {
            return Err("package_name is empty".into());
        }

        let mut c = Command::new(&py);
        apply_no_window(&mut c);
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
    apply_no_window(&mut c);
    // Force UTF-8 output on Windows (avoid garbled Chinese when Rust decodes stdout/stderr as UTF-8).
    c.env("PYTHONUTF8", "1");
    c.env("PYTHONIOENCODING", "utf-8");
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
async fn openakita_list_providers(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &["list-providers"], &[])
    })
    .await
}

#[tauri::command]
async fn openakita_list_skills(venv_dir: String, workspace_id: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        run_python_module_json(
            &venv_dir,
            "openakita.setup_center.bridge",
            &["list-skills", "--workspace-dir", wd.to_string_lossy().as_ref()],
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
    })
    .await
}

#[tauri::command]
async fn openakita_version(venv_dir: String) -> Result<String, String> {
    spawn_blocking_result(move || {
        let py = venv_python_path(&venv_dir);
        if !py.exists() {
            return Err(format!("venv python not found: {}", py.to_string_lossy()));
        }
        let mut c = Command::new(&py);
        apply_no_window(&mut c);
        c.env("PYTHONUTF8", "1");
        c.env("PYTHONIOENCODING", "utf-8");
        c.args([
            "-c",
            "import openakita; print(getattr(openakita,'__version__',''))",
        ]);
        let out = c.output().map_err(|e| format!("get openakita version failed: {e}"))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr).to_string();
            let stdout = String::from_utf8_lossy(&out.stdout).to_string();
            return Err(format!("python failed: {}\nstdout:\n{}\nstderr:\n{}", out.status, stdout, stderr));
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
        let mut args = vec![
            "health-check-endpoint",
            "--workspace-dir",
            wd.to_string_lossy().as_ref(),
        ];
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
        let mut args = vec![
            "health-check-im",
            "--workspace-dir",
            wd.to_string_lossy().as_ref(),
        ];
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

/// Install a skill from URL/path.
#[tauri::command]
async fn openakita_install_skill(
    venv_dir: String,
    workspace_id: String,
    url: String,
) -> Result<String, String> {
    spawn_blocking_result(move || {
        let wd = workspace_dir(&workspace_id);
        let args = vec![
            "install-skill",
            "--workspace-dir",
            wd.to_string_lossy().as_ref(),
            "--url",
            &url,
        ];
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
        let args = vec![
            "uninstall-skill",
            "--workspace-dir",
            wd.to_string_lossy().as_ref(),
            "--skill-name",
            &skill_name,
        ];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// List marketplace skills.
#[tauri::command]
async fn openakita_list_marketplace(
    venv_dir: String,
) -> Result<String, String> {
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
        let args = vec![
            "get-skill-config",
            "--workspace-dir",
            wd.to_string_lossy().as_ref(),
            "--skill-name",
            &skill_name,
        ];
        run_python_module_json(&venv_dir, "openakita.setup_center.bridge", &args, &[])
    })
    .await
}

/// Fetch available versions of a package from PyPI JSON API.
/// Returns JSON array of version strings, newest first.
#[tauri::command]
async fn fetch_pypi_versions(package: String, index_url: Option<String>) -> Result<String, String> {
    spawn_blocking_result(move || {
        let url = if let Some(ref idx) = index_url {
            // For custom mirrors, try the /pypi/<pkg>/json endpoint at the mirror root.
            // e.g. https://pypi.tuna.tsinghua.edu.cn/pypi/openakita/json
            // Strip trailing /simple or /simple/ from index-url to get mirror root.
            let root = idx
                .trim_end_matches('/')
                .trim_end_matches("/simple")
                .trim_end_matches("/simple/");
            format!("{}/pypi/{}/json", root, package)
        } else {
            format!("https://pypi.org/pypi/{}/json", package)
        };

        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(15))
            .user_agent("openakita-setup-center")
            .build()
            .map_err(|e| format!("HTTP client error: {e}"))?;

        let resp = client
            .get(&url)
            .send()
            .map_err(|e| format!("fetch PyPI versions failed ({}): {}", url, e))?
            .error_for_status()
            .map_err(|e| format!("fetch PyPI versions failed ({}): {}", url, e))?;

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
                        let numeric: String = p.chars().take_while(|c| c.is_ascii_digit()).collect();
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
