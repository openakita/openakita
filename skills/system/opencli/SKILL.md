---
name: opencli
description: Operate websites and Electron apps through CLI commands, reusing Chrome login sessions. Prefer over browser_task for supported sites (GitHub, Bilibili, Twitter/X, YouTube, etc.) — deterministic commands with structured JSON output.
system: true
handler: opencli
tool-name: opencli_run
category: Web
priority: high
---

# OpenCLI - 网站 CLI 操作

通过 [OpenCLI](https://github.com/jackwener/opencli) 将网站和 Electron 应用转化为 CLI 命令。
复用用户 Chrome 登录态，无需输入密码，返回结构化 JSON。

## 核心优势

- **复用 Chrome 登录态** — 无需密码/token，直接用已登录的 Chrome
- **确定性命令** — 比 browser_task 的 "让 LLM 猜" 可靠得多
- **结构化 JSON 输出** — LLM 解析零歧义

## When to Use OpenCLI（优先于 browser_task）

| 场景 | 推荐工具 | 原因 |
|------|---------|------|
| 操作有 adapter 的网站（GitHub、Bilibili 等）| `opencli_run` | 确定性命令，JSON 输出 |
| 需要登录态的操作 | `opencli_run` | 复用 Chrome 登录 |
| 无 adapter 的网站 | `browser_task` / 手动 browser_* | OpenCLI 不支持的站点 |
| 简单读取网页内容 | `web_fetch` | 无需浏览器 |

## 工具

### opencli_list — 发现可用命令

```python
opencli_list()
```

### opencli_run — 执行命令

命令格式: `<site> <subcommand>`，直接传给 opencli。

```python
opencli_run(command="zhihu hot list")
opencli_run(command="hackernews top")
opencli_run(command="bilibili video info", args=["BV1xx411c7XW"])
```

### opencli_doctor — 诊断环境

```python
opencli_doctor()
```

## Prerequisites

1. 安装 opencli: `npm install -g @jackwener/opencli`
2. Chrome 浏览器正在运行并已登录目标网站
3. Browser Bridge 扩展已安装（首次运行 `opencli setup` 配置）

## 决策路径

```
需要操作网站？
├─ 目标网站有 opencli adapter → opencli_run（首选）
├─ 需要登录但无 adapter → browser_task → 手动 browser_click/type 组合
├─ 只需读取内容 → web_fetch
└─ 只需搜索 → web_search
```

## 相关技能

- `browser_task` — 无 adapter 时的降级方案
- `browser_navigate` — 简单导航
- `web_fetch` — 轻量 URL 内容获取
- `web_search` — 搜索引擎查询
