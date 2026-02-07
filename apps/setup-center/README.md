## OpenAkita Setup Center

这是 OpenAkita 的可视化安装与配置中心（跨平台：Windows / macOS / Linux）。

### 目标（分阶段）

- **v0（已打通主链路）**：多工作区、创建/选择工作区、生成工作区文件（`.env`/`data/llm_endpoints.json`/`identity/SOUL.md`）、检测系统 Python、创建 venv、pip 安装 openakita、按 OpenAI/Anthropic 协议拉取模型列表、写入端点配置、写入 IM/代理等 env。
- **v1**：嵌入式 Python 更完善（进度、断点续传、版本固定/回滚、多架构覆盖），可选组件安装（Playwright/Whisper/浏览器等）。
- **v2**：一键打包发布（Windows `.exe`、macOS `.app`，可选签名/公证）

### 目录结构

- `src/`：前端（React + Vite）
- `src-tauri/`：Tauri 后端（Rust），负责文件/进程/网络等本地能力

### 运行（开发）

在 `apps/setup-center/` 下：

```bash
npm install
npm run tauri dev
```

### 说明

- **Windows 图标**：为避免开发环境因缺少 `src-tauri/icons/icon.ico` 导致构建失败，`src-tauri/build.rs` 会在缺失时自动生成一个透明占位 `icon.ico`。正式发布请用 `tauri icon` 生成完整图标集。

