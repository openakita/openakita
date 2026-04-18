---
name: openakita/skills@wecom-cli
description: "WeCom (Enterprise WeChat) CLI - official open-source CLI tool from WeCom. Covers 7 business categories: Contacts, Todos, Meetings, Messages, Schedules, Documents, Smartsheets. Built in Rust for macOS/Linux/Windows. Use when user wants to operate WeCom resources."
license: MIT
metadata:
  author: WecomTeam
  version: "0.1.5"
---

# 企业微信 CLI (wecom-cli)

企业微信开放平台官方命令行工具 — 让人类和 AI Agent 都能在终端中操作企业微信。

> 官方 GitHub: https://github.com/WecomTeam/wecom-cli
> 官方帮助: https://open.work.weixin.qq.com/help2/pc/21676

## Installation

```bash
# 安装 CLI
npm install -g @wecom/cli

# 安装 CLI Skill（必需）
npx skills add WeComTeam/wecom-cli -y -g

# 配置凭证（交互式，仅需一次）
wecom-cli init
```

### Prerequisites

- Supports平台：macOS (x64/arm64)、Linux (x64/arm64) 及 Windows (x64)
- Node.js >= 18
- 企业微信账号（**目前仅对 ≤ 10 人企业开放Use**）
- （可选）智能机器人 Bot ID 和 Secret

## Features范围

覆盖企业微信核心业务品类：

| 品类 | 能力 |
|------|------|
| 👤 通讯录 | Get可见范围成员列表、按姓名/别名Search等 |
| ✅ 待办 | Create/Read/Update/Delete待办，变更用户处理状态等 |
| 🎥 会议 | Create预约会议、取消会议、Update受邀成员、查询列表与详情等 |
| 💬 消息 | 会话列表查询、消息记录拉取（文本/图片/文件/语音/视频）、多媒体Download、Send文本等 |
| 📅 日程 | 日程增删改查、参与人Manage、多成员闲忙查询等 |
| 📄 文档 | 文档Create/Read/Edit等 |
| 📊 智能表格 | 智能表格Create、子表与字段Manage、记录增删改查等 |

## Agent Skills

安装 CLI Skill 后，AI Agent 工具（Cursor、Claude Code 等）即可Via自然语言操作企业微信。

### Skill 列表

| Skill ID | 功能 |
|----------|------|
| wecomcli-lookup-contact | 通讯录成员Search（姓名/别名） |
| wecomcli-get-todo-list | Get待办列表 |
| wecomcli-get-todo-detail | Get待办详情 |
| wecomcli-edit-todo | Create/Update/Delete待办 |
| wecomcli-create-meeting | Create预约会议 |
| wecomcli-edit-meeting | Update/取消会议 |
| wecomcli-get-meeting | 查询会议列表与详情 |
| wecomcli-get-msg | 会话列表、消息拉取、媒体Download、Send文本 |
| wecomcli-manage-schedule | 日程 CRUD、参与人Manage、闲忙查询 |
| wecomcli-manage-doc | 文档Create/Read/Edit |
| wecomcli-manage-smartsheet-schema | 智能表格Create、字段Manage |
| wecomcli-manage-smartsheet-data | 智能表格记录增删改查 |

## Usage Examples

```bash
# Get通讯录可见范围内的成员列表
wecom-cli contact get_userlist '{}'

# Create待办
wecom-cli todo create '{"title": "周报", "due_date": "2026-04-10"}'

# View会议列表
wecom-cli meeting list '{}'
```

## 限制

当前仅对 **≤ 10 人企业** 开放Use。

## 安全规则

- Write/Delete操作前确认用户意图
- 不输出密钥到终端明文
- 配置凭证Via交互式初始化完成，安全存储

## Pre-built Scripts

### scripts/setup.py
企微 wecom-cli 安装配置脚本。

```bash
python3 scripts/setup.py
```

### scripts/wecom_quick.py
企微常用操作快捷脚本。

```bash
python3 scripts/wecom_quick.py send-msg --to xxx --content "Hello"
python3 scripts/wecom_quick.py contacts
python3 scripts/wecom_quick.py create-doc --title "新文档"
python3 scripts/wecom_quick.py schedule
```
