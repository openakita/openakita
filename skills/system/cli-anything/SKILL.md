---
name: cli-anything
description: Control desktop software (GIMP, Blender, LibreOffice, etc.) through CLI-Anything generated command-line interfaces. Calls real application backends — much more reliable than GUI automation via desktop_* tools.
system: true
handler: cli_anything
tool-name: cli_anything_run
category: Desktop
priority: high
---

# CLI-Anything - 桌面软件 CLI 控制

通过 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 生成的 CLI 接口控制桌面软件。
直接调用软件后端 API，比 pyautogui/UIA 的 GUI 自动化可靠得多。

## 核心优势

- **调用真实后端** — GIMP 真实处理图片，LibreOffice 真实生成 PDF
- **结构化 JSON 输出** — `--json` 和 `--help` 标准支持
- **比 GUI 自动化可靠 100x** — 不依赖像素位置、窗口状态

## When to Use CLI-Anything（优先于 desktop_* 工具）

| 场景 | 推荐工具 | 原因 |
|------|---------|------|
| 操作有 CLI 的桌面软件 | `cli_anything_run` | 确定性，JSON 输出 |
| 无 CLI 的桌面软件 | `desktop_*` 工具 | 降级到 GUI 自动化 |
| 查看可用工具 | `cli_anything_discover` | 扫描 PATH |
| 了解命令参数 | `cli_anything_help` | 获取 --help 文档 |

## 工具

### cli_anything_discover — 发现已安装工具

```python
cli_anything_discover()
```

### cli_anything_run — 执行命令

```python
cli_anything_run(app="gimp", subcommand="image resize", args=["--width", "800", "input.png"])
cli_anything_run(app="libreoffice", subcommand="document export-pdf", args=["report.docx"])
```

### cli_anything_help — 查看帮助文档

```python
cli_anything_help(app="gimp")
cli_anything_help(app="gimp", subcommand="image resize")
```

## Installation CLI-Anything 工具

```bash
# 从 CLI-Hub 安装已有的 CLI
pip install cli-anything-gimp
pip install cli-anything-blender
pip install cli-anything-libreoffice

# 为新软件生成 CLI（需要 Claude Code）
/cli-anything ./your-software
```

## 支持的软件

CLI-Anything 社区已支持 9+ 软件：

- **创意工具**: GIMP, Blender, Inkscape, Audacity, OBS Studio
- **办公软件**: LibreOffice
- **AI 平台**: Stable Diffusion, ComfyUI
- **开发工具**: Jenkins, Gitea, pgAdmin

## 决策路径

```
需要控制桌面软件？
├─ 有 cli-anything CLI → cli_anything_run（首选）
├─ 无 CLI + Windows → desktop_* 工具（GUI 自动化）
└─ 无 CLI + 非 Windows → run_shell 尝试命令行工具
```

## 相关技能

- `desktop_click` / `desktop_type` — 无 CLI 时的降级 GUI 方案
- `run_shell` — 直接执行命令行
