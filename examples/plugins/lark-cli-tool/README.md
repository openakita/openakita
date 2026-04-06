# Lark/Feishu CLI Plugin

[English](#english) | [中文](#中文)

---

## 中文

将飞书官方 CLI 工具 ([larksuite/cli](https://github.com/larksuite/cli)) 接入 OpenAkita 插件系统，让 AI Agent 可以通过自然语言操作飞书开放平台的 11 大业务域。

> **重要**：本插件使用的是 `@larksuite/cli`（飞书开放平台官方 CLI），**不是** `@byted-apaas/cli`（飞书低代码平台 CLI），请勿混淆。

### 一键配置（Quickstart）

普通用户只需要说一句话即可完成全部配置，**不需要手动输入租户 ID** 或其他技术参数：

```
用户：帮我配置飞书 CLI

Akita 调用 lark_setup(action="quickstart")
  → 自动检测环境，安装 @larksuite/cli
  → 返回浏览器链接给用户：
    【第 1 步】请打开此链接完成飞书应用创建：https://...
    【第 2 步】请打开此链接完成 OAuth 授权：https://...
  → 用户在浏览器中点几下即完成

用户：配置好了

Akita 调用 lark_setup(action="status")
  → 确认登录成功 ✓
```

整个过程：
1. 自动安装（如未安装）
2. 自动创建飞书应用（用户只需在浏览器里确认）
3. 自动 OAuth 登录（用户只需在浏览器里授权）
4. 完成——可以直接使用

### 功能覆盖

| 业务域 | 能力 |
|--------|------|
| 📅 日历 | 查看日程、创建事件、邀请参与者、查询忙闲、时间建议 |
| 💬 消息 | 发送/回复消息、群聊管理、消息搜索、上传下载媒体 |
| 📄 文档 | 创建、阅读、更新、搜索云文档 |
| 📁 云盘 | 上传下载文件、搜索文档与知识库、管理评论 |
| 📊 多维表格 | 表格/字段/记录/视图/仪表盘管理、数据聚合分析 |
| 📈 电子表格 | 创建、读写、追加、查找、导出 |
| ✅ 任务 | 创建/查询/更新/完成任务、子任务、提醒 |
| 📚 知识库 | 知识空间、节点、文档管理 |
| 👤 通讯录 | 按姓名/邮箱/手机号搜索用户 |
| 📧 邮件 | 收发、搜索、转发邮件、草稿管理 |
| 🎥 会议 | 搜索会议记录、查看纪要与录制 |

### 与现有飞书通道的关系

本插件与 OpenAkita 内置的飞书 IM 通道（`channels/adapters/feishu.py`）是**互补关系**：

| | 飞书 IM 通道 | 本插件 (lark-cli) |
|--|-------------|-------------------|
| 凭证 | App ID + Secret（机器人） | 用户 OAuth 授权 |
| 能力 | 收发消息 | 全域 API（日历/文档/云盘/任务等） |
| 场景 | 机器人对话 | 用户操作飞书平台资源 |

两者可以同时工作，互不冲突。

### 前置条件

- **Node.js** 已安装（`npm`/`npx` 可用）
- 飞书账号（应用会在配置流程中自动创建，无需预先准备）

### 安装

将 `lark-cli-tool/` 目录复制到 OpenAkita 的 `data/plugins/` 下，或通过设置中心安装。

### 使用示例

```
用户: 看看我今天的日程
→ lark_run(command="calendar +agenda")

用户: 给张三发一条消息说"会议改到下午3点"
→ lark_run(command="im +messages-send --chat-id oc_xxx --text '会议改到下午3点'")

用户: 创建一份周报文档
→ lark_run(command="docs +create --title '本周周报' --markdown '# 本周进展\n- 完成了功能 X'")

用户: 搜一下上周的会议纪要
→ lark_run(command="vc +list")

用户: 帮我建一个任务"Review PR"，下周一截止
→ lark_run(command="task +create --title 'Review PR' --due '2026-04-01'")
```

### 注册的工具

| 工具 | 说明 |
|------|------|
| `lark_setup` | 一键安装配置（`quickstart`），或分步操作（`check/install/configure/login/status/logout`） |
| `lark_run` | 执行任意 lark-cli 命令，返回结构化 JSON 输出 |
| `lark_schema` | 查询 API 方法的参数、请求体、响应结构 |

### 可配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `lark_cli_path` | `lark-cli` | lark-cli 可执行文件路径 |
| `npx_path` | `npx` | npx 路径 |
| `default_timeout` | `30` | 默认命令超时（秒） |
| `default_format` | `json` | 默认输出格式 |
| `default_identity` | `user` | 默认执行身份（user / bot） |

### 安全说明

- 所有命令通过 `asyncio.create_subprocess_exec` 直接调用，不经过 shell
- 输入参数过滤 shell 元字符（`; & | $ ` 等）防止注入
- 自动提取 URL 和 device code，无需用户手动复制
- 命令超时保护（默认 30 秒，最长 300 秒）
- 输出限制最大 30,000 字符
- 支持 `--dry-run` 预览写操作

---

## English

Integrates the official Lark/Feishu CLI tool ([larksuite/cli](https://github.com/larksuite/cli)) into the OpenAkita plugin system, enabling AI Agents to operate 11 core business domains on the Feishu/Lark Open Platform via natural language.

> **Important**: This plugin uses `@larksuite/cli` (the official Lark Open Platform CLI), **NOT** `@byted-apaas/cli` (the Feishu low-code platform CLI). Do not confuse them.

### One-Click Setup (Quickstart)

Users only need to say one sentence to complete the entire setup — **no manual tenant ID or technical parameters needed**:

```
User: Set up the Lark CLI for me

Akita calls lark_setup(action="quickstart")
  → Auto-detects environment, installs @larksuite/cli
  → Returns browser links to the user:
    [Step 1] Open this link to create a Feishu app: https://...
    [Step 2] Open this link to complete OAuth: https://...
  → User clicks through in the browser

User: Done

Akita calls lark_setup(action="status")
  → Confirms login success ✓
```

The full flow:
1. Auto-install (if not already installed)
2. Auto-create Feishu app (user just confirms in browser)
3. Auto OAuth login (user just authorizes in browser)
4. Done — ready to use

### Coverage

| Domain | Capabilities |
|--------|-------------|
| 📅 Calendar | View agenda, create events, invite attendees, check free/busy, time suggestions |
| 💬 Messenger | Send/reply messages, group chat management, message search, media upload/download |
| 📄 Docs | Create, read, update, search documents |
| 📁 Drive | Upload/download files, search docs & wiki, manage comments |
| 📊 Base | Tables, fields, records, views, dashboards, data aggregation |
| 📈 Sheets | Create, read, write, append, find, export spreadsheets |
| ✅ Tasks | Create, query, update, complete tasks; subtasks, reminders |
| 📚 Wiki | Knowledge spaces, nodes, documents |
| 👤 Contact | Search users by name/email/phone |
| 📧 Mail | Browse, search, read, send, reply, forward emails; draft management |
| 🎥 Meetings | Search meeting records, view minutes & recordings |

### Registered Tools

| Tool | Description |
|------|-------------|
| `lark_setup` | One-click setup (`quickstart`), or individual steps (`check/install/configure/login/status/logout`) |
| `lark_run` | Execute any lark-cli command with structured JSON output |
| `lark_schema` | Inspect API method parameters, request/response structure |

### Security

- Commands invoked directly via subprocess, never through a shell
- Input sanitization prevents shell injection
- Auto-extracts URLs and device codes — no manual copying needed
- Timeout protection (default 30s, max 300s)
- Output capped at 30,000 characters
- `--dry-run` support for write operation preview

### License

MIT
