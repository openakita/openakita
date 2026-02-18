#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

mod migrations;

use base64::Engine as _;
use dirs_next::home_dir;
use once_cell::sync::Lazy;
use serde::{Deserialize, Serialize};
use std::fs;
use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
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

fn openakita_root_dir() -> PathBuf {
    home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita")
}

fn run_dir() -> PathBuf {
    openakita_root_dir().join("run")
}

/// 安装配置日志目录：~/.openakita/logs/
fn setup_logs_dir() -> PathBuf {
    openakita_root_dir().join("logs")
}

/// 开始写入安装配置日志，创建带日期的日志文件。返回完整路径供前端展示。
#[tauri::command]
fn start_onboarding_log(date_label: String) -> Result<String, String> {
    let log_dir = setup_logs_dir();
    fs::create_dir_all(&log_dir).map_err(|e| format!("create logs dir failed: {e}"))?;
    let safe_label = date_label
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect::<String>();
    let name = if safe_label.is_empty() {
        format!("onboarding-{}.log", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs())
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

fn modules_dir() -> PathBuf {
    openakita_root_dir().join("modules")
}

/// 获取内嵌 PyInstaller 打包后端的目录
fn bundled_backend_dir() -> PathBuf {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));

    // macOS: exe 在 .app/Contents/MacOS/，resources 在 .app/Contents/Resources/
    #[cfg(target_os = "macos")]
    {
        let macos_resource = exe_dir
            .parent() // Contents/
            .map(|p| p.join("Resources").join("openakita-server"))
            .unwrap_or_else(|| exe_dir.join("resources").join("openakita-server"));
        if macos_resource.exists() {
            return macos_resource;
        }
    }

    // Windows / Linux: resources 位于 exe 同级目录
    exe_dir.join("resources").join("openakita-server")
}

/// 获取后端可执行文件及参数
/// 优先使用内嵌的 PyInstaller 打包后端，降级到 venv python
fn get_backend_executable(venv_dir: &str) -> (PathBuf, Vec<String>) {
    // 1. 优先: 内嵌的 PyInstaller 打包后端
    let bundled_exe = if cfg!(windows) {
        bundled_backend_dir().join("openakita-server.exe")
    } else {
        bundled_backend_dir().join("openakita-server")
    };
    if bundled_exe.exists() {
        return (bundled_exe, vec!["serve".to_string()]);
    }
    // 2. 降级: venv python（开发模式 / 旧安装）
    let py = venv_pythonw_path(venv_dir);
    (py, vec!["-m".into(), "openakita.main".into(), "serve".into()])
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
    // 2. 打包内 python.exe（PyInstaller _internal 目录中，与 openakita-server.exe 同级）
    //    这是构建时从系统 Python 复制进去的，自带 pip 模块
    let bundled = bundled_backend_dir();
    if bundled.exists() {
        let internal_py = if cfg!(windows) {
            bundled.join("_internal").join("python.exe")
        } else {
            bundled.join("_internal").join("python3")
        };
        if internal_py.exists() {
            // 验证 pip 可用
            let mut c = Command::new(&internal_py);
            c.args(["-m", "pip", "--version"]);
            apply_no_window(&mut c);
            if let Ok(output) = c.output() {
                if output.status.success() {
                    return Some(internal_py);
                }
            }
        }
    }
    // 3. embedded python (python-build-standalone)
    //    解压后可能有多层目录（如 tag/assetname/python.exe 或 tag/assetname/python/python.exe），
    //    用 find_python_executable 递归查找，与 install_embedded_python_sync 行为一致，避免安装完成后仍“找不到”
    let runtime_dir = root.join("runtime").join("python");
    if runtime_dir.exists() {
        if let Ok(entries) = fs::read_dir(&runtime_dir) {
            for entry in entries.flatten() {
                if !entry.path().is_dir() { continue; }
                if let Ok(sub_entries) = fs::read_dir(entry.path()) {
                    for sub in sub_entries.flatten() {
                        if !sub.path().is_dir() { continue; }
                        if let Some(py) = find_python_executable(&sub.path()) {
                            return Some(py);
                        }
                    }
                }
            }
        }
    }
    // 4. PATH python（排除 Windows Store 假 Python 并验证可用性）
    let candidates = if cfg!(windows) {
        vec!["python.exe", "python3.exe"]
    } else {
        vec!["python3", "python"]
    };
    for name in candidates {
        let mut wc = Command::new(if cfg!(windows) { "where" } else { "which" });
        wc.arg(name);
        apply_no_window(&mut wc);
        if let Ok(output) = wc.output() {
            if output.status.success() {
                let path_str = String::from_utf8_lossy(&output.stdout).trim().to_string();
                // where 可能返回多个路径，逐一检查
                for line in path_str.lines() {
                    let line = line.trim();
                    if line.is_empty() { continue; }
                    let p = PathBuf::from(line);
                    if !p.exists() { continue; }

                    // 排除 Windows Store 假 Python（只是一个占位符，实际不能执行）
                    // 路径如: C:\Users\xxx\AppData\Local\Microsoft\WindowsApps\python.exe
                    let path_lower = p.to_string_lossy().to_lowercase();
                    if path_lower.contains("windowsapps") || path_lower.contains("microsoft\\windowsapps") {
                        continue;
                    }

                    // 验证 Python 实际可执行（避免其他假冒/损坏的 Python）
                    let mut vc = Command::new(&p);
                    vc.arg("--version");
                    apply_no_window(&mut vc);
                    if let Ok(ver) = vc.output() {
                        if ver.status.success() {
                            return Some(p);
                        }
                    }
                }
            }
        }
    }
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

// ── 模块管理 ──

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ModuleInfo {
    id: String,
    name: String,
    description: String,
    installed: bool,
    bundled: bool,
    size_mb: u32,
    category: String,
}

fn module_definitions() -> Vec<(&'static str, &'static str, &'static str, &'static [&'static str], u32, &'static str)> {
    // (id, name, description, pip_packages, estimated_size_mb, category)
    //
    // 仅体积大(>50MB)或有特殊二进制依赖的包才需要模块化安装。
    // 其余轻量包(文档处理/图像处理/桌面自动化/IM适配器等)已直接打包进 PyInstaller bundle。
    vec![
        ("vector-memory", "向量记忆增强", "语义搜索与向量记忆 (sentence-transformers + chromadb，含 PyTorch)", &["sentence-transformers", "chromadb", "regex>=2023.6.3"], 2500, "core"),
        ("browser", "浏览器自动化", "Playwright 浏览器 + browser-use AI 代理 (含 Chromium ~150MB)", &["playwright", "browser-use", "langchain-openai"], 350, "core"),
        ("whisper", "语音识别", "OpenAI Whisper 语音转文字 (含 PyTorch)", &["openai-whisper", "static-ffmpeg"], 2500, "core"),
        ("orchestration", "多Agent协同", "ZeroMQ 多 Agent 协同通信", &["pyzmq"], 10, "core"),
    ]
}

fn is_module_installed(module_id: &str) -> bool {
    let sp = modules_dir().join(module_id).join("site-packages");
    if sp.exists() && sp.read_dir().map(|mut d| d.next().is_some()).unwrap_or(false) {
        return true;
    }
    // Also check if bundled (PyInstaller full mode includes them)
    let bundled = bundled_backend_dir();
    if bundled.exists() {
        // For full builds, check marker files
        let marker = modules_dir().join(module_id).join(".installed");
        if marker.exists() {
            return true;
        }
    }
    false
}

fn is_module_bundled(module_id: &str) -> bool {
    let bundled_modules = bundled_backend_dir()
        .parent()
        .map(|p| p.join("modules").join(module_id))
        .unwrap_or_default();
    bundled_modules.exists()
}

#[tauri::command]
fn detect_modules() -> Vec<ModuleInfo> {
    module_definitions()
        .iter()
        .map(|(id, name, desc, _pkgs, size, cat)| ModuleInfo {
            id: id.to_string(),
            name: name.to_string(),
            description: desc.to_string(),
            installed: is_module_installed(id),
            bundled: is_module_bundled(id),
            size_mb: *size,
            category: cat.to_string(),
        })
        .collect()
}

#[tauri::command]
async fn install_module(
    app: tauri::AppHandle,
    module_id: String,
    mirror: Option<String>,
) -> Result<String, String> {
    // 从 module_definitions() 获取包列表（单一数据源，避免重复定义）
    let defs = module_definitions();
    let (_, _, _, packages, _, _) = defs
        .iter()
        .find(|(id, _, _, _, _, _)| *id == module_id.as_str())
        .ok_or_else(|| format!("未知模块: {}", module_id))?;

    let target_dir = modules_dir().join(&module_id).join("site-packages");
    fs::create_dir_all(&target_dir)
        .map_err(|e| format!("创建模块目录失败: {e}"))?;

    // Check for bundled wheels first
    let bundled_wheels = bundled_backend_dir()
        .parent()
        .map(|p| p.join("modules").join(&module_id).join("wheels"))
        .unwrap_or_default();

    let effective_mirror = mirror.clone().unwrap_or_else(|| {
        "https://mirrors.aliyun.com/pypi/simple/".to_string()
    });

    // ── 查找 Python 解释器 ──
    // 优先级：venv > 打包内 _internal/python.exe > embedded python > PATH > 自动下载
    let python_exe = match find_pip_python() {
        Some(p) => p,
        None => {
            let _ = app.emit("module-install-progress", serde_json::json!({
                "moduleId": module_id,
                "status": "installing",
                "message": "未找到 Python 环境，正在自动下载嵌入式 Python...",
            }));
            let result = install_embedded_python_sync(None, None)?;
            let p = PathBuf::from(&result.python_path);
            if !p.exists() {
                return Err(format!("自动安装嵌入式 Python 后仍找不到: {}", p.display()));
            }
            let mut ep = Command::new(&p);
            ep.args(["-m", "ensurepip", "--upgrade"]);
            apply_no_window(&mut ep);
            let _ = ep.output();
            p
        }
    };

    // ── 执行 pip install（离线 vs 多源在线） ──
    let run_pip_result = |output: std::process::Output, label: &str| -> Result<String, String> {
        if output.status.success() {
            // ── Post-install hooks (模块特定的额外安装步骤) ──
            if module_id == "browser" {
                let _ = app.emit("module-install-progress", serde_json::json!({
                    "moduleId": &module_id, "status": "installing",
                    "message": "正在下载 Chromium 浏览器引擎（约 150MB）...",
                }));
                let browsers_dir = modules_dir().join("browser").join("browsers");
                let _ = fs::create_dir_all(&browsers_dir);
                let mut pw = Command::new(&python_exe);
                pw.env("PYTHONPATH", &target_dir);
                pw.env("PLAYWRIGHT_BROWSERS_PATH", &browsers_dir);
                // 国内 CDN 加速 Playwright 浏览器下载
                pw.env("PLAYWRIGHT_DOWNLOAD_HOST", "https://cdn.npmmirror.com/binaries/playwright");
                pw.args(["-m", "playwright", "install", "chromium"]);
                apply_no_window(&mut pw);
                match pw.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).output() {
                    Ok(pw_out) if pw_out.status.success() => {
                        let _ = app.emit("module-install-progress", serde_json::json!({
                            "moduleId": &module_id, "status": "installing",
                            "message": "Chromium 浏览器引擎下载完成",
                        }));
                    }
                    Ok(pw_out) => {
                        let err = String::from_utf8_lossy(&pw_out.stderr);
                        let stdout_pw = String::from_utf8_lossy(&pw_out.stdout);
                        let detail_pw = if err.trim().is_empty() { stdout_pw.to_string() } else { err.to_string() };
                        let _ = app.emit("module-install-progress", serde_json::json!({
                            "moduleId": &module_id, "status": "warning",
                            "message": format!(
                                "Chromium 下载失败，模块已安装但浏览器引擎缺失。可稍后手动执行: playwright install chromium\n{}",
                                &detail_pw[..detail_pw.len().min(300)]
                            ),
                        }));
                    }
                    Err(e) => {
                        let _ = app.emit("module-install-progress", serde_json::json!({
                            "moduleId": &module_id, "status": "warning",
                            "message": format!("playwright install 执行失败: {}", e),
                        }));
                    }
                }
            }

            let marker = modules_dir().join(&module_id).join(".installed");
            let _ = fs::write(&marker, format!("installed_at={}", now_epoch_secs()));
            let _ = app.emit("module-install-progress", serde_json::json!({
                "moduleId": module_id, "status": "done",
                "message": format!("{} 安装完成 ({})", module_id, label),
            }));
            // 提示用户重启服务以加载新安装的模块
            let _ = app.emit("module-install-progress", serde_json::json!({
                "moduleId": module_id, "status": "restart-hint",
                "message": "模块已安装，建议重启 OpenAkita 服务以加载新模块",
            }));
            Ok(format!("{} 安装成功", module_id))
        } else {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let stdout = String::from_utf8_lossy(&output.stdout);
            let combined = if stderr.trim().is_empty() { stdout.to_string() }
                else if stdout.trim().is_empty() { stderr.to_string() }
                else { format!("{}\n{}", stderr, stdout) };
            let detail = &combined[..combined.len().min(800)];
            let exit_code = output.status.code().unwrap_or(-1);
            let err_msg = format!("[{}] pip 退出码 {}: {}", label, exit_code, detail);
            Err(err_msg)
        }
    };

    if bundled_wheels.exists() {
        // ── 离线安装：从预打包的 wheels 安装 ──
        let _ = app.emit("module-install-progress", serde_json::json!({
            "moduleId": module_id, "status": "installing",
            "message": format!("正在安装 {} (离线 wheels) ...", module_id),
        }));
        let mut c = Command::new(&python_exe);
        c.args(["-m", "pip", "install", "--no-index", "--find-links"]);
        c.arg(&bundled_wheels);
        c.arg("--target").arg(&target_dir);
        for pkg in *packages { c.arg(*pkg); }
        apply_no_window(&mut c);
        let output = c.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped())
            .output().map_err(|e| format!("执行 pip 失败: {e}"))?;
        let result = run_pip_result(output, "离线");
        if let Err(ref e) = result {
            let _ = app.emit("module-install-progress", serde_json::json!({
                "moduleId": module_id, "status": "error", "message": &e[..e.len().min(800)],
            }));
        }
        return result;
    }

    // ── 在线安装：多源自动切换 ──
    // 镜像优先级列表：用户指定源 > 阿里云 > 清华 > 官方 PyPI
    let user_host = effective_mirror.split("//").nth(1).unwrap_or("").split('/').next().unwrap_or("").to_string();
    let mirror_list: Vec<(&str, String)> = if mirror.is_some() {
        vec![
            (effective_mirror.as_str(), user_host.clone()),
            ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com".into()),
            ("https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn".into()),
            ("https://pypi.org/simple/", "pypi.org".into()),
        ]
    } else {
        vec![
            ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com".into()),
            ("https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn".into()),
            ("https://pypi.org/simple/", "pypi.org".into()),
        ]
    };

    let mut last_err = String::from("所有镜像源均安装失败");
    for (idx, (mirror_url, ref trusted_host)) in mirror_list.iter().enumerate() {
        let _ = app.emit("module-install-progress", serde_json::json!({
            "moduleId": module_id,
            "status": "installing",
            "message": if idx == 0 {
                format!("正在安装 {} (源: {}) ...", module_id, trusted_host)
            } else {
                format!("切换镜像源: {} (第 {} 次重试) ...", trusted_host, idx)
            },
        }));

        let mut c = Command::new(&python_exe);
        c.args(["-m", "pip", "install", "--target"]);
        c.arg(&target_dir);
        c.args(["-i", mirror_url]);
        c.args(["--trusted-host", trusted_host.as_str()]);
        let timeout = if idx == 0 { "120" } else { "60" };
        c.args(["--timeout", timeout]);
        for pkg in *packages { c.arg(*pkg); }
        apply_no_window(&mut c);

        match c.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).output() {
            Ok(output) => {
                if output.status.success() {
                    return run_pip_result(output, trusted_host);
                }
                // 安装失败 - 判断是否值得切换源
                let stderr = String::from_utf8_lossy(&output.stderr);
                let stdout = String::from_utf8_lossy(&output.stdout);
                let combined = format!("{}\n{}", stderr, stdout);
                let exit_code = output.status.code().unwrap_or(-1);
                last_err = format!("[{}] pip 退出码 {}: {}", trusted_host, exit_code, &combined[..combined.len().min(500)]);

                let combined_lower = combined.to_lowercase();
                if combined_lower.contains("no matching distribution")
                    || combined_lower.contains("could not find a version")
                    || combined_lower.contains("conflicting dependencies")
                {
                    break; // 逻辑错误，不是源的问题
                }
                let _ = app.emit("module-install-progress", serde_json::json!({
                    "moduleId": module_id, "status": "retrying",
                    "message": format!("源 {} 安装失败 (退出码 {})，尝试切换...", trusted_host, exit_code),
                }));
            }
            Err(e) => {
                last_err = format!("执行 pip 失败: {}", e);
                break; // pip 本身执行失败
            }
        }
    }

    let _ = app.emit("module-install-progress", serde_json::json!({
        "moduleId": module_id, "status": "error",
        "message": &last_err[..last_err.len().min(800)],
    }));
    Err(last_err)
}

#[tauri::command]
fn uninstall_module(module_id: String) -> Result<String, String> {
    let module_path = modules_dir().join(&module_id);
    if module_path.exists() {
        force_remove_dir(&module_path)
            .map_err(|e| format!("删除模块目录失败: {e}"))?;
    }
    Ok(format!("{} 已卸载", module_id))
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
        && root.join("venv").read_dir()
            .map(|mut d| d.next().is_some())
            .unwrap_or(false);
    let has_old_runtime = root.join("runtime").exists()
        && root.join("runtime").read_dir()
            .map(|mut d| d.next().is_some())
            .unwrap_or(false);
    let has_old_workspaces = root.join("workspaces").exists()
        && root.join("workspaces").read_dir()
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
                let ws_id = path.file_stem()
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

    // Calculate disk usage
    let disk_usage_mb = dir_size_bytes(&root) / (1024 * 1024);

    // 如果内嵌后端存在，自动清理旧 venv/runtime（它们已经被打包替代，不再需要）
    let bundled_exists = bundled_backend_dir().exists();
    let mut auto_cleaned = Vec::new();
    if has_old_venv && bundled_exists {
        if force_remove_dir(&root.join("venv")).is_ok() {
            auto_cleaned.push("venv");
        }
    }
    if has_old_runtime && bundled_exists {
        if force_remove_dir(&root.join("runtime")).is_ok() {
            auto_cleaned.push("runtime");
        }
    }

    // 重新检测清理后的状态
    let has_old_venv = has_old_venv && root.join("venv").exists();
    let has_old_runtime = has_old_runtime && root.join("runtime").exists();

    // Generate conflict descriptions
    let mut conflicts = Vec::new();
    if !auto_cleaned.is_empty() {
        conflicts.push(format!("已自动清理旧环境: {}", auto_cleaned.join(", ")));
    }
    if has_old_venv && bundled_exists {
        conflicts.push("旧 Python 虚拟环境 (venv) 清理失败，请手动删除".to_string());
    }
    if has_old_runtime && bundled_exists {
        conflicts.push("旧 Python 运行时 (runtime) 清理失败，请手动删除".to_string());
    }
    if !running.is_empty() {
        conflicts.push(format!("检测到 {} 个正在运行的 OpenAkita 进程", running.len()));
    }

    // Recalculate disk usage after cleanup
    let disk_usage_mb = dir_size_bytes(&root) / (1024 * 1024);

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
        let status = rd_cmd.status()
            .map_err(|e| format!("执行 rd 命令失败: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    // 最终检查
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

    if clean_venv {
        let venv_path = root.join("venv");
        if venv_path.exists() {
            force_remove_dir(&venv_path)
                .map_err(|e| format!("清理 venv 失败: {e}"))?;
            cleaned.push("venv");
        }
    }
    if clean_runtime {
        let runtime_path = root.join("runtime");
        if runtime_path.exists() {
            force_remove_dir(&runtime_path)
                .map_err(|e| format!("清理 runtime 失败: {e}"))?;
            cleaned.push("runtime");
        }
    }

    if cleaned.is_empty() {
        Ok("无需清理".to_string())
    } else {
        Ok(format!("已清理: {}", cleaned.join(", ")))
    }
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

// ── PID 文件 JSON 格式 ──
#[derive(Debug, Serialize, Deserialize, Clone)]
struct PidFileData {
    pid: u32,
    #[serde(default = "default_started_by")]
    started_by: String, // "tauri" | "external"
    #[serde(default)]
    started_at: u64,    // unix epoch seconds
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
    timestamp: f64,  // unix epoch seconds (float for sub-second precision)
    #[serde(default)]
    phase: String,    // "starting" | "initializing" | "running" | "restarting" | "stopping"
    #[serde(default)]
    http_ready: bool, // HTTP API 是否就绪
}

/// 心跳文件路径：{workspace_dir}/data/backend.heartbeat
fn service_heartbeat_file(workspace_id: &str) -> PathBuf {
    workspace_dir(workspace_id).join("data").join("backend.heartbeat")
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

/// 尝试通过 HTTP API 优雅关闭 Python 服务（POST /api/shutdown），
/// 然后等待进程退出。如果 API 调用失败或超时则回退到 kill。
/// `port`: 可选端口号，默认 18900
fn graceful_stop_pid(pid: u32, port: Option<u16>) -> Result<(), String> {
    if !is_pid_running(pid) {
        return Ok(());
    }

    let effective_port = port.unwrap_or(18900);
    // 第一步：尝试通过 HTTP API 触发优雅关闭
    let api_ok = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
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
        // API 调用成功，给 Python 最多 5 秒优雅退出时间
        for _ in 0..25 {
            if !is_pid_running(pid) {
                return Ok(());
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    }

    // 第二步：进程仍然存活，强制 kill
    if is_pid_running(pid) {
        kill_pid(pid)?;
        // 等待最多 2s 确认退出
        for _ in 0..10 {
            if !is_pid_running(pid) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    }

    if is_pid_running(pid) {
        Err(format!("pid {} still running after graceful + forced stop", pid))
    } else {
        Ok(())
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

#[cfg(not(windows))]
fn get_process_create_time(pid: u32) -> Option<u64> {
    // On Unix, read /proc/{pid}/stat field 22 (starttime in clock ticks)
    // comm field (index 1) can contain spaces/parens, so we find the last ')' first
    let stat = fs::read_to_string(format!("/proc/{}/stat", pid)).ok()?;
    let after_comm = stat.rfind(')')? + 2; // skip ") "
    if after_comm >= stat.len() {
        return None;
    }
    // Fields after comm start at index 2; starttime is field 22 (index 20 after comm = 22-2)
    let fields: Vec<&str> = stat[after_comm..].split_whitespace().collect();
    let starttime = fields.get(19)?.parse::<u64>().ok()?; // field 22 → index 19 after comm
    let clk_tck: u64 = 100; // typical default
    // Read uptime to compute boot time
    let uptime_str = fs::read_to_string("/proc/uptime").ok()?;
    let uptime_secs: f64 = uptime_str.split_whitespace().next()?.parse().ok()?;
    let now = now_epoch_secs();
    let boot_time = now.saturating_sub(uptime_secs as u64);
    Some(boot_time + starttime / clk_tck)
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
    let content = fs::read_to_string(&env_path).ok()?;
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
                        &pe.sz_exe_file[..pe
                            .sz_exe_file
                            .iter()
                            .position(|&c| c == 0)
                            .unwrap_or(260)],
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
    #[cfg(not(windows))]
    {
        // Unix: 检查 /proc/{pid}/cmdline 或用 ps
        if let Ok(cmdline) = fs::read_to_string(format!("/proc/{}/cmdline", pid)) {
            return cmdline.to_lowercase().contains("openakita");
        }
        // fallback: ps
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
        for pattern in &patterns {
            if let Ok(out) = Command::new("sh")
                .args(["-c", pattern])
                .output()
            {
                let stdout = String::from_utf8_lossy(&out.stdout);
                for line in stdout.lines() {
                    if let Ok(pid) = line.trim().parse::<u32>() {
                        if is_pid_running(pid) && !killed.contains(&pid) {
                            let _ = Command::new("kill")
                                .args(["-TERM", &pid.to_string()])
                                .status();
                            killed.push(pid);
                        }
                    }
                }
            }
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

        // Step 2: 对每个 python 进程查命令行
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
            if let Ok(cmd_out) = c.output() {
                let s = String::from_utf8_lossy(&cmd_out.stdout).to_string();
                let s_lower = s.to_lowercase();
                // 精确匹配模块调用签名，避免 venv 路径中 .openakita 误报
                if s_lower.contains("openakita.main") && (s_lower.contains(" serve") || s_lower.ends_with("serve")) {
                    if is_pid_running(ppid) {
                        out.push(OpenAkitaProcess {
                            pid: ppid,
                            cmd: s.trim().to_string(),
                        });
                    }
                }
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

    // 人格预设文件：8 个标配预设 + user_custom 模板
    // 从仓库 identity/personas/ 目录嵌入，确保新工作区开箱即用
    {
        const PERSONA_DEFAULT: &str = include_str!("../../../../identity/personas/default.md");
        const PERSONA_BUSINESS: &str = include_str!("../../../../identity/personas/business.md");
        const PERSONA_TECH_EXPERT: &str = include_str!("../../../../identity/personas/tech_expert.md");
        const PERSONA_BUTLER: &str = include_str!("../../../../identity/personas/butler.md");
        const PERSONA_GIRLFRIEND: &str = include_str!("../../../../identity/personas/girlfriend.md");
        const PERSONA_BOYFRIEND: &str = include_str!("../../../../identity/personas/boyfriend.md");
        const PERSONA_FAMILY: &str = include_str!("../../../../identity/personas/family.md");
        const PERSONA_JARVIS: &str = include_str!("../../../../identity/personas/jarvis.md");
        const PERSONA_USER_CUSTOM: &str = include_str!("../../../../identity/personas/user_custom.md");

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
                // PID 文件有效但心跳超时（进程可能卡死），强制清理
                let port = read_workspace_api_port(&ent.workspace_id);
                let _ = graceful_stop_pid(data.pid, port);
                let _ = fs::remove_file(service_pid_file(&ent.workspace_id));
                remove_heartbeat_file(&ent.workspace_id);
            }
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 第二个实例启动时，聚焦已有窗口并退出自身
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--background"]),
        ))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .setup(|app| {
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

            // 后台启动时：不弹出主窗口，只保留托盘/菜单栏常驻
            let is_background = std::env::args().any(|a| a == "--background");
            if is_background {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }

            // ── 自动拉起后端（所有启动模式都生效） ──
            // 如果有已配置的工作区且后端未在运行，则自动启动后端。
            // 前端通过 is_backend_auto_starting 查询此状态，
            // 在启动期间显示提示并禁用启动/重启按钮。
            let state = read_state_file();
            if let Some(ref ws_id) = state.current_workspace_id {
                let port = read_workspace_api_port(ws_id).unwrap_or(18900);
                let already_running = reqwest::blocking::Client::builder()
                    .timeout(std::time::Duration::from_secs(2))
                    .build()
                    .ok()
                    .and_then(|c| c.get(format!("http://127.0.0.1:{}/api/health", port)).send().ok())
                    .map(|r| r.status().is_success())
                    .unwrap_or(false);
                if !already_running {
                    AUTO_START_IN_PROGRESS.store(true, Ordering::SeqCst);
                    let venv_dir = openakita_root_dir().join("venv").to_string_lossy().to_string();
                    let ws_clone = ws_id.clone();
                    std::thread::spawn(move || {
                        let _ = openakita_service_start(venv_dir, ws_clone);
                        AUTO_START_IN_PROGRESS.store(false, Ordering::SeqCst);
                    });
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
            check_python_for_pip,
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
            openakita_check_pid_alive,
            set_tray_backend_status,
            is_backend_auto_starting,
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
            fetch_pypi_versions,
            http_get_json,
            http_proxy_request,
            read_file_base64,
            download_file,
            open_external_url,
            openakita_list_processes,
            openakita_stop_all_processes,
            detect_modules,
            install_module,
            uninstall_module,
            is_first_run,
            check_environment,
            cleanup_old_environment,
            start_onboarding_log,
            append_onboarding_log,
            append_onboarding_log_lines,
            register_cli,
            unregister_cli,
            get_cli_status
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
    /// 后端心跳阶段："starting" | "initializing" | "running" | "restarting" | "stopping" | ""
    #[serde(default)]
    heartbeat_phase: String,
    /// 心跳是否过期（超过 30 秒没更新）。None = 没有心跳文件（旧版后端）
    #[serde(default)]
    heartbeat_stale: Option<bool>,
    /// 距上次心跳的秒数。None = 没有心跳文件
    #[serde(default)]
    heartbeat_age_secs: Option<f64>,
}

/// 构造 ServiceStatus，自动填充心跳信息
fn build_service_status(workspace_id: &str, running: bool, pid: Option<u32>, pid_file_str: String) -> ServiceStatus {
    let (heartbeat_phase, heartbeat_stale, heartbeat_age_secs) = if let Some(hb) = read_heartbeat_file(workspace_id) {
        let now = now_epoch_secs() as f64;
        let age = now - hb.timestamp;
        let stale = age > 30.0; // 超过 30 秒无心跳视为过期
        (hb.phase, Some(stale), Some(age))
    } else {
        (String::new(), None, None)
    };
    ServiceStatus {
        running,
        pid,
        pid_file: pid_file_str,
        heartbeat_phase,
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
            return Ok(build_service_status(&workspace_id, true, Some(data.pid), pf));
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
            // 心跳严重过期，进程很可能已卡死。
            // 主动尝试清理：先 kill 进程，再清理 PID 和心跳文件。
            let port = read_workspace_api_port(&workspace_id);
            let _ = graceful_stop_pid(data.pid, port);
            let _ = fs::remove_file(service_pid_file(&workspace_id));
            remove_heartbeat_file(&workspace_id);
            return Ok(false);
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
                    _ => { *guard = None; }
                }
            }
        }
    }
    if let Some(data) = read_pid_file(&workspace_id) {
        if is_pid_file_valid(&data) {
            // 进程已在运行，但检查心跳是否严重过期（可能卡死）
            if let Some(true) = is_heartbeat_stale(&workspace_id, 60) {
                // 心跳严重过期，进程可能卡死，先尝试清理再启动
                let port = read_workspace_api_port(&workspace_id);
                let _ = graceful_stop_pid(data.pid, port);
                let _ = fs::remove_file(&pid_file);
                remove_heartbeat_file(&workspace_id);
            } else {
                return Ok(build_service_status(&workspace_id, true, Some(data.pid), pf));
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
        fn drop(&mut self) { release_start_lock(&self.0); }
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
    let (backend_exe, backend_args) = get_backend_executable(&venv_dir);
    if !backend_exe.exists() {
        return Err(format!("后端可执行文件不存在: {}", backend_exe.to_string_lossy()));
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

    // 设置可选模块路径（已安装的可选模块 site-packages）
    // 重要：不能使用 PYTHONPATH！Python 启动时 PYTHONPATH 会被插入到 sys.path
    // 最前面，覆盖 PyInstaller 内置的包（如 pydantic），导致外部 pydantic 的
    // C 扩展 pydantic_core._pydantic_core 加载失败，进程在 import 阶段崩溃。
    // 改用自定义环境变量 OPENAKITA_MODULE_PATHS，由 Python 端的
    // inject_module_paths() 读取并 append 到 sys.path 末尾。
    if let Some(extra_path) = build_modules_pythonpath() {
        cmd.env("OPENAKITA_MODULE_PATHS", extra_path);
    }

    // Playwright 浏览器二进制路径（install_module 安装到此目录）
    let browsers_dir = modules_dir().join("browser").join("browsers");
    if browsers_dir.exists() {
        cmd.env("PLAYWRIGHT_BROWSERS_PATH", &browsers_dir);
    }

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

    // Confirm the process is still alive shortly after spawning.
    std::thread::sleep(std::time::Duration::from_millis(500));
    if !is_pid_running(pid) {
        {
            let mut guard = MANAGED_CHILD.lock().unwrap();
            if let Some(ref mp) = *guard {
                if mp.pid == pid { *guard = None; }
            }
        }
        let _ = fs::remove_file(&pid_file);
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
                let _ = graceful_stop_pid(mp.pid, port);
                if is_pid_running(mp.pid) {
                    let _ = mp.child.kill();
                    let _ = mp.child.wait();
                }
                let _ = fs::remove_file(&pid_file);
                // 等待端口释放（最多 10 秒），确保后续重启不会遇到端口冲突
                let _ = wait_for_port_free(effective_port, 10_000);
                remove_heartbeat_file(&workspace_id);
                return Ok(build_service_status(&workspace_id, false, None, pid_file.to_string_lossy().to_string()));
            } else {
                *guard = Some(mp);
            }
        }
    }

    // ── 2. PID 文件回退 ──
    let pid = read_pid_file(&workspace_id).map(|d| d.pid);
    if let Some(pid) = pid {
        // 强制杀干净：如果杀不掉，要显式报错（避免 UI 显示“已停止”但后台仍残留）。
        graceful_stop_pid(pid, port).map_err(|e| format!("failed to stop service: {e}"))?;
    }
    let _ = fs::remove_file(&pid_file);
    remove_heartbeat_file(&workspace_id);
    // 等待端口释放（最多 10 秒），确保后续重启不会遇到端口冲突
    let _ = wait_for_port_free(effective_port, 10_000);
    Ok(build_service_status(&workspace_id, false, None, pid_file.to_string_lossy().to_string()))
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
#[tauri::command]
fn is_backend_auto_starting() -> bool {
    AUTO_START_IN_PROGRESS.load(Ordering::SeqCst)
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
#[tauri::command]
fn set_tray_backend_status(app: tauri::AppHandle, status: String) -> Result<(), String> {
    let tooltip = match status.as_str() {
        "alive" => "OpenAkita - Running",
        "degraded" => "OpenAkita - Backend Unresponsive",
        "dead" => "OpenAkita - Backend Stopped",
        _ => "OpenAkita",
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
                .args(["-e", "display notification \"Backend service has stopped\" with title \"OpenAkita\""])
                .spawn();
        }
    }
    Ok(())
}

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    let open_status = MenuItem::with_id(app, "open_status", "打开状态面板", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "隐藏窗口", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出（Quit）", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&open_status, &show, &hide, &quit])?;

    TrayIconBuilder::with_id("main_tray")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("OpenAkita")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id.as_ref() {
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

                // 2. 按 PID 文件逐一处理：tauri 启动的停掉，external 启动的跳过
                let entries = list_service_pids();
                for ent in &entries {
                    if ent.started_by == "external" {
                        // CLI 启动的后端，不停止
                        continue;
                    }
                    let port = read_workspace_api_port(&ent.workspace_id);
                    let _ = stop_service_pid_entry(ent, port);
                }

                // 3. 兜底扫描孤儿进程（精确匹配）
                kill_openakita_orphans();

                std::thread::sleep(std::time::Duration::from_millis(600));

                // 4. 最终确认
                let still_pid = list_service_pids()
                    .into_iter()
                    .filter(|x| x.started_by != "external" && is_pid_running(x.pid))
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

/// 带重试的 HTTP GET，依次尝试原始 URL 和镜像 URL
fn get_with_mirrors(client: &reqwest::blocking::Client, urls: &[&str]) -> Result<reqwest::blocking::Response, String> {
    let mut last_err = String::new();
    for url in urls {
        match client.get(*url).send() {
            Ok(resp) => match resp.error_for_status() {
                Ok(r) => return Ok(r),
                Err(e) => { last_err = format!("{}", e); }
            },
            Err(e) => { last_err = format!("{}", e); }
        }
    }
    Err(last_err)
}

/// 向 onboarding 日志文件追加一行（仅用于内部进度，忽略错误）
fn append_to_onboarding_log(log_path: Option<&Path>, line: &str) {
    let Some(path) = log_path else { return };
    if !path.exists() {
        return;
    }
    let mut f = match OpenOptions::new().append(true).open(path) {
        Ok(f) => f,
        Err(_) => return,
    };
    let _ = writeln!(f, "{}", line);
    let _ = f.flush();
}

/// 同步下载并安装嵌入式 Python（供 install_module 等内部函数调用）
fn install_embedded_python_sync(
    python_series: Option<String>,
    log_path: Option<PathBuf>,
) -> Result<EmbeddedPythonInstallResult, String> {
    let python_series = python_series.unwrap_or_else(|| "3.11".to_string());
    let triple = target_triple_hint()?;
    let log_path = log_path.as_deref();

    let client = reqwest::blocking::Client::builder()
        .user_agent("openakita-setup-center")
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| format!("http client build failed: {e}"))?;

    // 多镜像：jsDelivr 国内常可访问，ghp.ci 代理，最后直连 GitHub raw
    let latest_urls = [
        "https://cdn.jsdelivr.net/gh/astral-sh/python-build-standalone@latest-release/latest-release.json",
        "https://ghp.ci/https://raw.githubusercontent.com/astral-sh/python-build-standalone/latest-release/latest-release.json",
        "https://raw.githubusercontent.com/astral-sh/python-build-standalone/latest-release/latest-release.json",
    ];
    let latest: LatestReleaseInfo = match get_with_mirrors(&client, &latest_urls) {
        Ok(resp) => resp
            .json()
            .map_err(|e| format!("parse latest-release.json failed: {e}"))?,
        Err(e) => {
            // 所有镜像均失败时使用内置 fallback 标签，避免因网络拉不到 JSON 导致无法安装（需与 python-build-standalone 已发布 release 一致）
            const FALLBACK_TAG: &str = "20260211";
            eprintln!("fetch latest-release.json failed (all mirrors): {e}, using fallback tag {FALLBACK_TAG}");
            LatestReleaseInfo {
                tag: FALLBACK_TAG.to_string(),
            }
        }
    };

    let gh_api_urls_str = [
        format!("https://ghp.ci/https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{}", latest.tag),
        format!("https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{}", latest.tag),
    ];
    let gh_api_urls: Vec<&str> = gh_api_urls_str.iter().map(|s| s.as_str()).collect();
    let gh: GhRelease = get_with_mirrors(&client, &gh_api_urls)
        .map_err(|e| format!("fetch github release failed (all mirrors): {e}"))?
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

    // 安装包为 python-build-standalone 的 install_only 归档，典型 20–50 MB，慢网下可能较久
    if !archive_path.exists() {
        append_to_onboarding_log(log_path, "[嵌入式 Python] 开始下载安装包（约 20–50 MB）...");
        let download_client = reqwest::blocking::Client::builder()
            .user_agent("openakita-setup-center")
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(3600))
            .build()
            .map_err(|e| format!("download client build failed: {e}"))?;
        let dl_mirror_ghp = format!("https://ghp.ci/{}", &asset.browser_download_url);
        let dl_urls = [dl_mirror_ghp.as_str(), asset.browser_download_url.as_str()];
        const MAX_DOWNLOAD_ATTEMPTS: u32 = 3;
        const IDLE_TIMEOUT_SECS: u64 = 120;
        let mut last_err = String::new();
        for attempt in 1..=MAX_DOWNLOAD_ATTEMPTS {
            if attempt > 1 {
                let _ = fs::remove_file(&archive_path);
                append_to_onboarding_log(log_path, &format!("[嵌入式 Python] 重试 {}/{}...", attempt, MAX_DOWNLOAD_ATTEMPTS));
            }
            match get_with_mirrors(&download_client, &dl_urls) {
                Ok(resp) => {
                    let mut out = match std::fs::File::create(&archive_path) {
                        Ok(f) => f,
                        Err(e) => {
                            last_err = format!("create archive failed: {e}");
                            continue;
                        }
                    };
                    let (tx, rx) = mpsc::sync_channel::<Result<Vec<u8>, String>>(4);
                    let reader_handle = thread::spawn(move || {
                        let mut resp = resp;
                        let mut buf = [0u8; 65536];
                        loop {
                            match resp.read(&mut buf) {
                                Ok(0) => {
                                    let _ = tx.send(Ok(vec![]));
                                    break;
                                }
                                Ok(n) => {
                                    if tx.send(Ok(buf[..n].to_vec())).is_err() {
                                        break;
                                    }
                                }
                                Err(e) => {
                                    let _ = tx.send(Err(format!("{e}")));
                                    break;
                                }
                            }
                        }
                    });
                    let idle = Duration::from_secs(IDLE_TIMEOUT_SECS);
                    let mut write_err: Option<String> = None;
                    let mut reader_handle = Some(reader_handle);
                    loop {
                        match rx.recv_timeout(idle) {
                            Ok(Ok(chunk)) => {
                                if chunk.is_empty() {
                                    break;
                                }
                                if let Err(e) = out.write_all(&chunk) {
                                    write_err = Some(format!("{e}"));
                                    break;
                                }
                            }
                            Ok(Ok(_)) => {}
                            Ok(Err(e)) => {
                                write_err = Some(e);
                                break;
                            }
                            Err(mpsc::RecvTimeoutError::Timeout) => {
                                last_err = format!("下载无进度超时（{}s 内无数据），请检查网络", IDLE_TIMEOUT_SECS);
                                drop(rx);
                                if let Some(h) = reader_handle.take() { let _ = h.join(); }
                                break;
                            }
                            Err(mpsc::RecvTimeoutError::Disconnected) => break,
                        }
                    }
                    if let Some(h) = reader_handle.take() { let _ = h.join(); }
                    if let Some(e) = write_err {
                        last_err = e;
                        continue;
                    }
                    if last_err.contains("无进度超时") {
                        let _ = fs::remove_file(&archive_path);
                        continue;
                    }
                    append_to_onboarding_log(log_path, "[嵌入式 Python] 下载完成，正在解压...");
                    break;
                }
                Err(e) => last_err = format!("download failed (all mirrors): {e}"),
            }
            if attempt == MAX_DOWNLOAD_ATTEMPTS {
                let _ = fs::remove_file(&archive_path);
                return Err(format!("{last_err} (已重试 {MAX_DOWNLOAD_ATTEMPTS} 次)"));
            }
        }
    } else {
        append_to_onboarding_log(log_path, "[嵌入式 Python] 使用已缓存安装包，正在解压...");
    }

    // extract
    if asset.name.ends_with(".zip") {
        extract_zip(&archive_path, &install_dir)?;
    } else if asset.name.ends_with(".tar.gz") {
        extract_tar_gz(&archive_path, &install_dir)?;
    } else {
        return Err("unsupported archive type".into());
    }
    append_to_onboarding_log(log_path, "[嵌入式 Python] 解压完成");

    let py =
        find_python_executable(&install_dir).ok_or_else(|| "python executable not found after extract".to_string())?;
    Ok(EmbeddedPythonInstallResult {
        python_command: vec![py.to_string_lossy().to_string()],
        python_path: py.to_string_lossy().to_string(),
        install_dir: install_dir.to_string_lossy().to_string(),
        asset_name: asset.name,
        tag: latest.tag,
    })
}

#[tauri::command]
async fn install_embedded_python(
    python_series: Option<String>,
    log_path: Option<String>,
) -> Result<EmbeddedPythonInstallResult, String> {
    let path_buf = log_path.map(PathBuf::from);
    spawn_blocking_result(move || install_embedded_python_sync(python_series, path_buf)).await
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

/// 解析可用的 Python 解释器路径，并可选返回需要设置的 PYTHONPATH（bundled 模式）。
/// 查找顺序：venv → bundled _internal/python.exe → embedded → PATH Python
fn resolve_python(venv_dir: &str) -> Result<(PathBuf, Option<String>), String> {
    let venv_py = venv_python_path(venv_dir);
    if venv_py.exists() {
        return Ok((venv_py, None));
    }
    let py = find_pip_python().ok_or_else(|| {
        format!(
            "No Python interpreter available. Tried venv: {}, bundled and PATH Python also not found.",
            venv_py.to_string_lossy()
        )
    })?;
    let bundled = bundled_backend_dir();
    let internal_dir = bundled.join("_internal");
    let pythonpath = if py.starts_with(&internal_dir) {
        Some(internal_dir.to_string_lossy().to_string())
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
        let (py, _pythonpath) = resolve_python(&venv_dir)?;

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
        up.env("PYTHONUTF8", "1");
        up.env("PYTHONIOENCODING", "utf-8");
        up.args(["-m", "pip", "install", "-U", "pip", "setuptools", "wheel"]);
        up.args(["-i", effective_index]);
        if !effective_host.is_empty() {
            up.args(["--trusted-host", effective_host]);
        }
        let _ = run_streaming(up, "pip upgrade (best-effort)", &mut log, &emit_line);

        emit_stage("安装 openakita（pip）", 70);
        let mut c = Command::new(&py);
        apply_no_window(&mut c);
        c.env("PYTHONUTF8", "1");
        c.env("PYTHONIOENCODING", "utf-8");
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
        let (py, _pythonpath) = resolve_python(&venv_dir)?;
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
    let (py, pythonpath) = resolve_python(venv_dir)?;

    let mut c = Command::new(&py);
    apply_no_window(&mut c);
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
        // 1. 尝试从打包后端读取 _bundled_version.txt（最快且无需 Python）
        let bundled = bundled_backend_dir();
        let version_file = bundled.join("_internal").join("openakita").join("_bundled_version.txt");
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
        c.env("PYTHONUTF8", "1");
        c.env("PYTHONIOENCODING", "utf-8");
        if let Some(ref pp) = pythonpath {
            c.env("PYTHONPATH", pp);
        }
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
        let wd_str = wd.to_string_lossy().to_string();
        let mut args = vec![
            "health-check-endpoint",
            "--workspace-dir",
            &wd_str,
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
        let wd_str = wd.to_string_lossy().to_string();
        let mut args = vec![
            "health-check-im",
            "--workspace-dir",
            &wd_str,
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
        let args = vec![
            "ensure-channel-deps",
            "--workspace-dir",
            &wd_str,
        ];
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
        let args = vec![
            "install-skill",
            "--workspace-dir",
            &wd_str,
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
                    Ok(r) => { resp_ok = Some(r); break; }
                    Err(e) => { last_err = format!("fetch PyPI versions failed ({}): {}", url, e); }
                },
                Err(e) => { last_err = format!("fetch PyPI versions failed ({}): {}", url, e); }
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

/// Download a file from a URL and save it to the user's Downloads folder.
/// Returns the saved file path on success.
#[tauri::command]
async fn download_file(url: String, filename: String) -> Result<String, String> {
    // Determine downloads directory
    let downloads_dir = dirs_next::download_dir()
        .or_else(|| dirs_next::home_dir().map(|h| h.join("Downloads")))
        .ok_or_else(|| "Cannot determine Downloads directory".to_string())?;
    std::fs::create_dir_all(&downloads_dir)
        .map_err(|e| format!("Cannot create Downloads dir: {e}"))?;

    // Avoid overwriting: if file exists, append (1), (2), etc.
    let stem = std::path::Path::new(&filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("download")
        .to_string();
    let ext = std::path::Path::new(&filename)
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| format!(".{s}"))
        .unwrap_or_default();
    let mut dest = downloads_dir.join(&filename);
    let mut counter = 1u32;
    while dest.exists() {
        dest = downloads_dir.join(format!("{stem} ({counter}){ext}"));
        counter += 1;
    }

    // Download
    let client = reqwest::Client::new();
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
    std::fs::write(&dest, &bytes)
        .map_err(|e| format!("Failed to write file: {e}"))?;

    Ok(dest.to_string_lossy().to_string())
}

/// Open an external URL in the OS default browser.
#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        let mut c = std::process::Command::new("cmd");
        c.args(["/C", "start", "", &url]);
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
    let exe = if cfg!(windows) {
        bundled_backend_dir().join("openakita-server.exe")
    } else {
        bundled_backend_dir().join("openakita-server")
    };
    if exe.exists() {
        return Ok(exe);
    }
    // 降级：尝试 venv 模式（开发环境）
    let venv_py = if cfg!(windows) {
        openakita_root_dir().join("venv").join("Scripts").join("python.exe")
    } else {
        openakita_root_dir().join("venv").join("bin").join("python3")
    };
    if venv_py.exists() {
        return Ok(venv_py);
    }
    Err("未找到后端可执行文件（openakita-server 或 venv python）".into())
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
    let content = serde_json::to_string_pretty(config)
        .map_err(|e| format!("序列化 CLI 配置失败: {e}"))?;
    std::fs::write(&path, content)
        .map_err(|e| format!("写入 cli.json 失败: {e}"))?;
    Ok(())
}

/// 生成 wrapper 脚本内容
fn generate_wrapper_content(backend_exe: &Path) -> String {
    #[cfg(target_os = "windows")]
    {
        let _ = backend_exe; // Windows 使用相对路径，不需要绝对路径
        format!("@echo off\r\n\"%~dp0..\\resources\\openakita-server\\openakita-server.exe\" %*\r\n")
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

    // 尝试 HKLM (perMachine), 如果权限不够降级到 HKCU (currentUser)
    let (hive, subkey) = {
        let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
        let sys_env = hklm.open_subkey_with_flags(
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            KEY_READ | KEY_WRITE,
        );
        if let Ok(key) = sys_env {
            (key, "system")
        } else {
            let hkcu = RegKey::predef(HKEY_CURRENT_USER);
            let user_env = hkcu
                .open_subkey_with_flags("Environment", KEY_READ | KEY_WRITE)
                .map_err(|e| format!("无法打开用户环境变量注册表: {e}"))?;
            (user_env, "user")
        }
    };

    // 读取当前 PATH
    let current_path: String = hive.get_value("Path").unwrap_or_default();

    // 检查是否已存在
    let separator = ";";
    let paths: Vec<&str> = current_path.split(separator).collect();
    if paths.iter().any(|p| p.eq_ignore_ascii_case(&bin_str)) {
        return Ok(()); // 已存在，无需重复添加
    }

    // 检查 PATH 长度限制
    let new_path = if current_path.is_empty() {
        bin_str.clone()
    } else {
        format!("{}{}{}", current_path, separator, bin_str)
    };
    if new_path.len() > 2047 {
        return Err("PATH 环境变量已接近长度限制 (2048)，无法追加".into());
    }

    // 写入注册表 (REG_EXPAND_SZ type to support %...% variables)
    hive.set_value("Path", &new_path)
        .map_err(|e| format!("写入 PATH 注册表失败 ({}): {e}", subkey))?;

    // 广播 WM_SETTINGCHANGE
    windows_broadcast_env_change();

    Ok(())
}

#[cfg(target_os = "windows")]
fn windows_remove_from_path(bin_dir: &Path) -> Result<(), String> {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();
    let separator = ";";

    // 尝试系统和用户两个位置
    for (hive_predef, subkey_path) in [
        (HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (HKEY_CURRENT_USER, "Environment"),
    ] {
        let hive = RegKey::predef(hive_predef);
        if let Ok(key) = hive.open_subkey_with_flags(subkey_path, KEY_READ | KEY_WRITE) {
            let current_path: String = key.get_value("Path").unwrap_or_default();
            let new_paths: Vec<&str> = current_path
                .split(separator)
                .filter(|p| !p.eq_ignore_ascii_case(&bin_str) && !p.is_empty())
                .collect();
            let new_path = new_paths.join(separator);
            let _ = key.set_value("Path", &new_path);
        }
    }

    windows_broadcast_env_change();
    Ok(())
}

#[cfg(target_os = "windows")]
fn windows_is_in_path(bin_dir: &Path) -> bool {
    use winreg::enums::*;
    use winreg::RegKey;

    let bin_str = bin_dir.to_string_lossy().to_string();

    for (hive_predef, subkey_path) in [
        (HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (HKEY_CURRENT_USER, "Environment"),
    ] {
        let hive = RegKey::predef(hive_predef);
        if let Ok(key) = hive.open_subkey_with_flags(subkey_path, KEY_READ) {
            let current_path: String = key.get_value("Path").unwrap_or_default();
            if current_path
                .split(';')
                .any(|p| p.eq_ignore_ascii_case(&bin_str))
            {
                return true;
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
        if !cmd.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
            return Err(format!("命令名 '{}' 包含非法字符", cmd));
        }
    }

    let bin_dir = cli_bin_dir();
    std::fs::create_dir_all(&bin_dir)
        .map_err(|e| format!("创建 bin 目录失败: {e}"))?;

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
        if add_to_path { " (已添加到 PATH)" } else { "" }
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
            { windows_is_in_path(&PathBuf::from(&config.bin_dir)) }
            #[cfg(not(target_os = "windows"))]
            { unix_is_in_path(&PathBuf::from(&config.bin_dir)) }
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
