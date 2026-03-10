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
use std::sync::Mutex;
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

fn default_root_dir() -> PathBuf {
    home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".openakita")
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
            eprintln!("warning: failed to parse {}: {e}, using defaults", p.display());
            RootConfig::default()
        }
    }
}

fn write_root_config(config: &RootConfig) -> Result<(), String> {
    let default_dir = default_root_dir();
    fs::create_dir_all(&default_dir).map_err(|e| format!("create default root dir failed: {e}"))?;

    let p = root_config_path();
    let data = serde_json::to_string_pretty(config).map_err(|e| format!("serialize root config failed: {e}"))?;
    fs::write(&p, data).map_err(|e| format!("write root_config.json failed: {e}"))?;

    // 同步写入纯文本文件，供 NSIS 安装脚本简单读取（无需解析 JSON）
    let txt_path = default_dir.join("custom_root.txt");
    match &config.custom_root {
        Some(path) if !path.is_empty() => {
            fs::write(&txt_path, path.trim()).map_err(|e| format!("write custom_root.txt failed: {e}"))?;
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
    let offset = tail.iter().position(|&b| b == b'\n').map(|i| i + 1).unwrap_or(0);
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
    fs::write(&path, content.as_bytes())
        .map_err(|e| format!("save log export failed: {e}"))?;
    Ok(path.to_string_lossy().to_string())
}

fn modules_dir() -> PathBuf {
    openakita_root_dir().join("modules")
}

/// 获取内嵌 PyInstaller 打包后端的目录
fn bundled_backend_dir() -> PathBuf {
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
                .join("openakita-server");
            if primary.exists() {
                return primary;
            }
            // 兼容可能的简化布局（无额外 resources/ 前缀）
            let fallback = contents_dir.join("Resources").join("openakita-server");
            if fallback.exists() {
                return fallback;
            }
        }
    }

    // Windows / Linux: 主路径 — resources 位于 exe 同级目录
    let primary = exe_dir.join("resources").join("openakita-server");
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
            "openakita-setup-center", // Cargo.toml package name (Tauri 2.x default)
            "openakita-desktop",      // legacy / mainBinaryName override
            "open-akita-desktop",
        ];

        // deb 常见布局: /usr/lib/<app-name>/resources/openakita-server/
        if let Some(ref name) = exe_name {
            candidates.push(PathBuf::from(format!(
                "/usr/lib/{}/resources/openakita-server", name
            )));
        }
        for app_name in static_names {
            candidates.push(PathBuf::from(format!(
                "/usr/lib/{}/resources/openakita-server", app_name
            )));
        }

        // 若 exe 在 /usr/bin/，尝试同级 /usr/lib/<app>/
        if let Some(usr_dir) = exe_dir.parent() {
            if let Some(ref name) = exe_name {
                candidates.push(
                    usr_dir.join("lib").join(name)
                        .join("resources").join("openakita-server"),
                );
            }
            for app_name in static_names {
                candidates.push(
                    usr_dir
                        .join("lib")
                        .join(app_name)
                        .join("resources")
                        .join("openakita-server"),
                );
            }
        }

        // AppImage: 解压后 exe 在 <mount>/usr/bin/，resources 可能在 <mount>/usr/lib/<app>/
        // 也可能在 <mount>/resources/ (Tauri AppImage 平坦布局)
        if let Some(mount_root) = exe_dir.parent().and_then(|p| p.parent()) {
            if let Some(ref name) = exe_name {
                candidates.push(
                    mount_root.join("lib").join(name)
                        .join("resources").join("openakita-server"),
                );
            }
            for app_name in static_names {
                candidates.push(
                    mount_root
                        .join("lib")
                        .join(app_name)
                        .join("resources")
                        .join("openakita-server"),
                );
            }
            candidates.push(mount_root.join("resources").join("openakita-server"));
        }

        for c in &candidates {
            if c.exists() {
                eprintln!("[bundled_backend_dir] found at Linux fallback: {}", c.display());
                return c.clone();
            }
        }

        eprintln!(
            "[bundled_backend_dir] not found. exe_dir={}, exe_name={:?}, checked {} Linux fallback paths",
            exe_dir.display(),
            exe_name,
            candidates.len()
        );
    }

    primary
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
/// 优先使用内嵌的 PyInstaller 打包后端，降级到 venv python
fn get_backend_executable(venv_dir: &str) -> (PathBuf, Vec<String>) {
    // 1. 优先: 内嵌的 PyInstaller 打包后端
    let bundled_dir = bundled_backend_dir();
    let bundled_exe = if cfg!(windows) {
        bundled_dir.join("openakita-server.exe")
    } else {
        bundled_dir.join("openakita-server")
    };
    if bundled_exe.exists() {
        return (bundled_exe, vec!["serve".to_string()]);
    }
    // 2. 降级: venv python（开发模式 / 旧安装）
    eprintln!(
        "[backend] bundled openakita-server not found at: {}\n\
         [backend] current_exe: {:?}\n\
         [backend] falling back to venv python in: {}",
        bundled_exe.display(),
        std::env::current_exe().ok().map(|p| p.display().to_string()),
        venv_dir,
    );
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

// ── 模块定义（供 build_modules_pythonpath 使用） ──

fn module_definitions() -> Vec<(&'static str, &'static str, &'static str, &'static [&'static str], u32, &'static str)> {
    // (id, name, description, pip_packages, estimated_size_mb, category)
    //
    // 仅体积大(>50MB)或有特殊二进制依赖的包才需要模块化安装。
    // 其余轻量包(文档处理/图像处理/桌面自动化/IM适配器等)已直接打包进 PyInstaller bundle。
    // browser (playwright + browser-use + langchain-openai) 已内置到 core 包，不再作为外置模块
    vec![
        ("vector-memory", "向量记忆增强", "让 Akita 拥有长期记忆，能根据语义搜索历史对话。体积较大（约 2.5GB，含 PyTorch），安装耗时较长", &["sentence-transformers", "chromadb", "regex>=2023.6.3"], 2500, "core"),
        ("whisper", "语音识别", "支持语音消息自动转文字，无需联网即可识别。体积较大（约 2.5GB，含 PyTorch），安装耗时较长", &["openai-whisper", "static-ffmpeg"], 2500, "core"),
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
    let _lock = ROOT_CONFIG_LOCK.lock().map_err(|e| format!("lock failed: {e}"))?;
    let clean_path = path.as_deref().map(|s| s.trim()).filter(|s| !s.is_empty()).map(String::from);

    if let Some(ref p) = clean_path {
        let target = PathBuf::from(p);
        if !target.is_absolute() {
            return Err("请使用绝对路径（如 D:\\MyData\\.openakita 或 /data/openakita）".into());
        }
        if target.exists() && !target.is_dir() {
            return Err("指定的路径已存在但不是目录".into());
        }
        fs::create_dir_all(&target).map_err(|e| format!("无法创建目标目录: {e}"))?;
        // 验证目录可写
        let test_file = target.join(".openakita_write_test");
        fs::write(&test_file, "test").map_err(|e| format!("目标目录无写入权限: {e}"))?;
        let _ = fs::remove_file(&test_file);
    }

    if migrate {
        if let Some(ref new_root) = clean_path {
            let old_root = openakita_root_dir();
            let new_root_path = PathBuf::from(new_root);
            if old_root != new_root_path && old_root.exists() {
                for entry_name in &["workspaces", "venv", "runtime", "run", "logs", "modules", "bin"] {
                    let src = old_root.join(entry_name);
                    let dst = new_root_path.join(entry_name);
                    if src.exists() && src.is_dir() && !dst.exists() {
                        if let Err(e) = copy_dir_recursive(&src, &dst) {
                            eprintln!("migrate dir {}: {}", entry_name, e);
                        }
                    }
                }
                for file_name in &["state.json", "cli.json"] {
                    let src = old_root.join(file_name);
                    let dst = new_root_path.join(file_name);
                    if src.exists() && src.is_file() && !dst.exists() {
                        if let Err(e) = fs::copy(&src, &dst) {
                            eprintln!("migrate file {}: {}", file_name, e);
                        }
                    }
                }
            }
            if !new_root_path.exists() || !new_root_path.is_dir() {
                return Err("迁移完成后目标目录不可访问，未更改配置。请检查磁盘连接后重试。".into());
            }
        }
    }

    let config = RootConfig { custom_root: clean_path };
    write_root_config(&config)?;

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
                eprintln!("copy file {} -> {}: {e}", src_path.display(), dst_path.display());
            }
        }
    }
    Ok(())
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

    let disk_usage_mb = dir_size_bytes(&root) / (1024 * 1024);

    // venv 是打包后应用运行时的关键组件：
    // - venv: 用于 pip install 模块（vector-memory/whisper 等）和工具执行
    // Python 基座改为安装包内置 _internal，不再依赖 runtime 下载链路。
    let _bundled_exists = bundled_backend_dir().exists();

    let mut conflicts = Vec::new();
    if !running.is_empty() {
        conflicts.push(format!("检测到 {} 个正在运行的 OpenAkita 进程", running.len()));
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
        bundled, bundled_exe.display(), venv_ready, venv_py.display()
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
        let status = rd_cmd.status()
            .map_err(|e| format!("执行 rd 命令失败: {e}"))?;
        if status.success() || !path.exists() {
            return Ok(());
        }
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("chmod").args(["-R", "u+w"]).arg(path).status();
        let status = Command::new("rm").args(["-rf"]).arg(path).status()
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
                && modules_base.read_dir()
                    .map(|mut d| d.any(|e| e.map(|e| e.path().is_dir()).unwrap_or(false)))
                    .unwrap_or(false);
            if has_installed_modules {
                warnings.push("注意: 清理 venv 后已安装的外置模块（vector-memory 等）可能需要重新安装".to_string());
            }
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
        let mut msg = format!("已清理: {}", cleaned.join(", "));
        if !warnings.is_empty() {
            msg.push_str(&format!(" ({})", warnings.join("; ")));
        }
        Ok(msg)
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
        let pid_str = pid.to_string();

        // SIGTERM: 允许进程优雅退出
        let _ = Command::new("kill")
            .args(["-TERM", &pid_str])
            .status();

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
        let mut pids_to_kill: Vec<u32> = Vec::new();
        for pattern in &patterns {
            if let Ok(out) = Command::new("sh")
                .args(["-c", pattern])
                .output()
            {
                let stdout = String::from_utf8_lossy(&out.stdout);
                for line in stdout.lines() {
                    if let Ok(pid) = line.trim().parse::<u32>() {
                        if is_pid_running(pid) && !killed.contains(&pid) && !pids_to_kill.contains(&pid) {
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
        const PERSONA_USER_CUSTOM: &str = include_str!("../../../../identity/personas/user_custom.md.example");

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

fn validate_workspace_id(id: &str) -> Result<(), String> {
    let id = id.trim();
    if id.is_empty() {
        return Err("workspace id is empty".into());
    }
    if id.len() > 64 {
        return Err("workspace id too long (max 64 chars)".into());
    }
    if !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        return Err("workspace id can only contain a-z, A-Z, 0-9, _ and -".into());
    }
    if !id.chars().any(|c| c.is_ascii_alphanumeric()) {
        return Err("workspace id must contain at least one letter or digit".into());
    }
    const RESERVED: &[&str] = &[
        "con", "prn", "aux", "nul",
        "com1","com2","com3","com4","com5","com6","com7","com8","com9",
        "lpt1","lpt2","lpt3","lpt4","lpt5","lpt6","lpt7","lpt8","lpt9",
    ];
    if RESERVED.contains(&id.to_ascii_lowercase().as_str()) {
        return Err("workspace id conflicts with a reserved system name".into());
    }
    Ok(())
}

#[tauri::command]
fn create_workspace(id: String, name: String, set_current: bool) -> Result<WorkspaceSummary, String> {
    validate_workspace_id(&id)?;
    if name.trim().is_empty() {
        return Err("workspace name is empty".into());
    }

    fs::create_dir_all(workspaces_dir()).map_err(|e| format!("create workspaces dir failed: {e}"))?;

    let _lock = STATE_FILE_LOCK.lock().map_err(|e| format!("state lock failed: {e}"))?;
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
    let _lock = STATE_FILE_LOCK.lock().map_err(|e| format!("state lock failed: {e}"))?;
    let mut state = read_state_file();
    if !state.workspaces.iter().any(|w| w.id == id) {
        return Err("workspace id not found".into());
    }
    let dir = workspace_dir(&id);
    if !dir.exists() {
        eprintln!("workspace dir missing, recreating scaffold: {}", dir.display());
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

/// DMG 覆盖安装后版本对账：检查运行中后端的版本，必要时替换。
///
/// macOS 上通过 DMG 拖拽覆盖安装后，旧的 openakita-server 进程可能仍在端口上
/// 服务。新版 app 启动时必须检测版本不匹配并主动替换，否则会一直使用旧后端。
///
/// 此函数合并了「是否有后端在运行」和「版本是否匹配」两个检查，
/// 只发一次 HTTP 请求，避免 setup 阶段重复探测。
fn startup_version_check(app_version: &str, port: u16) -> VersionCheckResult {
    let client = match reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .no_proxy()
        .build()
    {
        Ok(c) => c,
        Err(_) => return VersionCheckResult::NotRunning,
    };

    let resp = match client
        .get(format!("http://127.0.0.1:{}/api/health", port))
        .send()
    {
        Ok(r) if r.status().is_success() => r,
        _ => return VersionCheckResult::NotRunning,
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

    // 版本一致、dev 版本、或无法判断 → 保持现有后端
    if backend_version.is_empty()
        || backend_version == "0.0.0-dev"
        || backend_version == desktop_version
    {
        return VersionCheckResult::RunningOk;
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
        if bundled_v.is_empty() { "?" } else { &bundled_v },
        desktop_version
    );

    // graceful_stop_pid 内部已包含：POST /api/shutdown → 等待 5s → force kill → 等待 2s
    // 无需手动再发 shutdown 或 sleep。
    let pid = match json.get("pid").and_then(|v| v.as_u64()).map(|p| p as u32) {
        Some(p) => p,
        None => {
            eprintln!("Cannot determine backend PID from health response; keeping current backend.");
            return VersionCheckResult::RunningOk;
        }
    };

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

    eprintln!("Old backend (pid={}) stopped. New backend will be started automatically.", pid);
    VersionCheckResult::Upgraded
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
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
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

            // ── 自动拉起后端（仅 release 模式生效） ──
            // 如果有已配置的工作区且后端未在运行，则自动启动后端。
            // dev 模式（cargo tauri dev）跳过，避免与手动启动的开发后端冲突。
            // 前端通过 is_backend_auto_starting 查询此状态，
            // 在启动期间显示提示并禁用启动/重启按钮。
            //
            // startup_version_check 合并了「健康检查」和「版本对账」两步：
            //   - NotRunning  → 端口无响应，需要启动
            //   - RunningOk   → 后端在运行且版本可接受
            //   - Upgraded    → 旧版后端已被终止，需要启动新版
            let app_version = app.package_info().version.to_string();
                let state = read_state_file();
                if let Some(ref ws_id) = state.current_workspace_id {
                    let port = read_workspace_api_port(ws_id).unwrap_or(18900);
                let need_start = !matches!(
                    startup_version_check(&app_version, port),
                    VersionCheckResult::RunningOk
                );
                if need_start {
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
            get_root_dir_info,
            set_custom_root_dir,
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
            show_item_in_folder,
            open_file_with_default,
            export_env_backup,
            export_diagnostic_bundle,
            open_external_url,
            openakita_list_processes,
            openakita_stop_all_processes,
            is_first_run,
            check_environment,
            check_backend_availability,
            cleanup_old_environment,
            start_onboarding_log,
            append_onboarding_log,
            append_onboarding_log_lines,
            append_frontend_log,
            save_log_export,
            register_cli,
            unregister_cli,
            get_cli_status
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                // Safety-net: clean up backend processes on ANY exit path
                // (SIGTERM, system shutdown, unexpected termination, etc.)
                // Idempotent — harmless if tray-quit already stopped everything.
                //
                // 直接 kill 进程而非走 HTTP /api/shutdown：
                //   1. 退出时要尽快完成清理，避免 Finder/macOS 等待超时后强杀本进程
                //      导致后端沦为孤儿进程。
                //   2. Python 后端已注册 SIGTERM handler，收到信号即可优雅关闭。
                //   3. HTTP API 可能因代理、端口状态等原因不可达，增加不确定性。
                let entries = list_service_pids();
                for ent in &entries {
                    if ent.started_by == "external" {
                        continue;
                    }
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
        let bundled_dir = bundled_backend_dir();
        let bundled_name = if cfg!(windows) { "openakita-server.exe" } else { "openakita-server" };
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

    // ── 清除可能干扰 PyInstaller 打包环境的外部 Python 变量 ──
    // 用户电脑的 Anaconda、系统 PYTHONPATH 等会污染模块搜索路径，
    // 导致内置包（如 pydantic_core）被外部版本覆盖后崩溃。
    strip_harmful_python_env(&mut cmd);

    // Force UTF-8 output on Windows and make logs clean & realtime.
    // Without this, Rich may try to write unicode symbols (e.g. ✓) using GBK and crash.
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUNBUFFERED", "1");
    // Disable colored / styled output to avoid ANSI escape codes in log files.
    cmd.env("NO_COLOR", "1");

    // inherit current env, then overlay workspace .env
    // 注意：忽略会污染 Python 运行时的键，避免用户导入旧 .env 后把
    // _internal/venv 的模块搜索路径破坏（典型表现：No module named encodings）。
    for (k, v) in read_env_kv(&ws_dir.join(".env")) {
        if is_harmful_python_env_key(&k) {
            continue;
        }
        cmd.env(k, v);
    }
    cmd.env("LLM_ENDPOINTS_CONFIG", ws_dir.join("data").join("llm_endpoints.json"));
    cmd.env("OPENAKITA_ROOT", openakita_root_dir().to_string_lossy().to_string());

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
    let url = format!(
        "http://127.0.0.1:{}/api/workspace/export",
        api_port
    );
    let body = serde_json::json!({
        "output_dir": output_dir,
        "include_userdata": include_userdata,
        "include_media": include_media,
    });
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(300))
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
            export_workspace_backup_native(&workspace_id, &output_dir, include_userdata, include_media)
        }
    }
}

fn export_workspace_backup_native(
    workspace_id: &str,
    output_dir: &str,
    include_userdata: bool,
    include_media: bool,
) -> Result<serde_json::Value, String> {
    use std::io::{Read as _, Write as _};

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

    let always_dirs = ["identity", "data/agents", "data/sessions", "data/scheduler",
                       "data/mcp", "data/telegram", "skills", "mcps"];
    let always_files = [".env", "data/llm_endpoints.json", "data/skills.json",
                        "data/disabled_views.json", "data/runtime_state.json",
                        "data/proactive_feedback.json", "data/sub_agent_states.json"];
    let userdata_dirs = ["data/memory", "data/retrospects", "data/plans",
                         "data/docs", "data/reports", "data/research"];
    let userdata_files = ["data/agent.db"];
    let media_dirs = ["data/generated_images", "data/sticker", "data/media",
                      "data/output", "data/screenshots"];
    let exclude_dirs = ["logs", "data/llm_debug", "data/delegation_logs",
                        "data/traces", "data/react_traces", "data/temp",
                        "data/tool_overflow", "data/selfcheck", "data/openakita_docs",
                        "identity/runtime", "node_modules", "Lib", "__pycache__"];

    let mut file_count: u64 = 0;

    for entry in walkdir(&ws) {
        let full = entry.path();
        if !full.is_file() { continue; }
        let rel = match full.strip_prefix(&ws) {
            Ok(r) => r.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };

        // Exclude
        if exclude_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/"))) {
            continue;
        }
        if rel == "data/backend.heartbeat" || rel == "package.json" || rel == "package-lock.json" {
            continue;
        }

        let included =
            always_files.contains(&rel.as_str()) ||
            always_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/"))) ||
            (include_userdata && (
                userdata_files.contains(&rel.as_str()) ||
                userdata_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/")))
            )) ||
            (include_media &&
                media_dirs.iter().any(|d| rel == *d || rel.starts_with(&format!("{d}/"))));

        if !included { continue; }

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
    let _ = zw.write_all(serde_json::to_string_pretty(&manifest).unwrap_or_default().as_bytes());
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
    use std::io::{Read as _, Write as _};

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
        if name == "manifest.json" { continue; }

        // Safety: reject path traversal
        let norm = PathBuf::from(&name);
        if norm.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
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
    pub struct Entry { pub path: PathBuf }
    impl Entry {
        pub fn path(&self) -> &Path { &self.path }
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
        out.push(PythonCandidate { command: c, version_text, is_usable });
    }

    if let Some(bundled_py) = bundled_internal_python_path() {
        let c = vec![bundled_py.to_string_lossy().to_string()];
        let mut cmd = c.clone();
        cmd.push("--version".into());
        let version_text = run_capture(&cmd).unwrap_or_else(|e| e);
        let is_usable = python_version_ok(&version_text);
        out.push(PythonCandidate { command: c, version_text, is_usable });
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
    let port = ws_id.as_deref()
        .and_then(read_workspace_api_port)
        .unwrap_or(18900);

    // --- Strategy 0: check heartbeat to understand backend lifecycle ---
    let heartbeat = ws_id.as_deref().and_then(read_heartbeat_file);
    let backend_phase = heartbeat.as_ref().map(|hb| hb.phase.as_str()).unwrap_or("");
    let http_ready = heartbeat.as_ref().map(|hb| hb.http_ready).unwrap_or(false);
    let hb_fresh = heartbeat.as_ref().map(|hb| {
        let age = now_epoch_secs() as f64 - hb.timestamp;
        age <= 30.0
    }).unwrap_or(false);

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

    let failing: Vec<&PythonContractResult> = contracts
        .iter()
        .filter(|c| c.status == "fail")
        .collect();
    let summary = if failing.is_empty() { "healthy" } else { "broken" }.to_string();

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
            evidence: vec![format!("heartbeat ok, port {} API unreachable — retrying may help", port)],
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
        if TcpStream::connect_timeout(
            &addr.parse().ok()?,
            std::time::Duration::from_secs(2),
        ).is_err() {
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
            Ok(resp) if resp.status().is_success() => {
                match resp.json::<serde_json::Value>() {
                    Ok(json) => return parse_diagnostics_json(&json),
                    Err(e) => { last_err = format!("json parse: {e}"); continue; }
                }
            }
            Ok(resp) => { last_err = format!("HTTP {}", resp.status()); continue; }
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

    let summary = json.get("summary")
        .and_then(|v| v.as_str())
        .unwrap_or("healthy")
        .to_string();

    let mut contracts: Vec<PythonContractResult> = vec![];
    if let Some(checks) = json.get("checks").and_then(|v| v.as_array()) {
        for c in checks {
            contracts.push(PythonContractResult {
                id: c.get("id").and_then(|v| v.as_str()).unwrap_or("").into(),
                title: c.get("title").and_then(|v| v.as_str()).unwrap_or("").into(),
                status: c.get("status").and_then(|v| v.as_str()).unwrap_or("pass").into(),
                code: c.get("code").and_then(|v| v.as_str()).unwrap_or("").into(),
                evidence: c.get("evidence")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter().filter_map(|x| x.as_str().map(String::from)).collect())
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
        "未找到可用 Python 解释器（venv/bundled）。请重新安装 OpenAkita 以恢复内置 Python。".to_string()
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

    // Download (timeout 30s to avoid hanging if backend is unreachable, e.g. on macOS)
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
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
    std::fs::write(&dest, &bytes)
        .map_err(|e| format!("Failed to write file: {e}"))?;

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
        c.spawn().map_err(|e| format!("Failed to open explorer: {e}"))?;
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
        fs::create_dir_all(parent)
            .map_err(|e| format!("Cannot create directory: {e}"))?;
    }

    fs::copy(&env_path, &dest)
        .map_err(|e| format!("Failed to copy .env: {e}"))?;

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
        fs::create_dir_all(parent)
            .map_err(|e| format!("Cannot create directory: {e}"))?;
    }

    let file = fs::File::create(&dest)
        .map_err(|e| format!("Failed to create zip file: {e}"))?;
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

    add_dir_to_zip(&mut zip_writer, &logs_dir, "logs", options)?;
    add_dir_to_zip(&mut zip_writer, &llm_debug_dir, "llm_debug", options)?;

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
        if py3.exists() { py3 } else { venv_base.join("bin").join("python") }
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
        (HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
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
                .filter(|p| {
                    !p.trim_end_matches('\\')
                        .eq_ignore_ascii_case(bin_norm)
                })
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
        (HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
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
