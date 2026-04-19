---
name: openakita/skills@wecom-cli
description: "WeCom (Enterprise WeChat) CLI — official open-source CLI tool from WeCom. Covers 7 business categories: Contacts, Todos, Meetings, Messages, Schedules, Documents, Smartsheets. Built in Rust for macOS/Linux/Windows. Use when the user wants to operate WeCom resources."
license: MIT
metadata:
  author: WecomTeam
  version: "0.1.5"
---

# WeCom CLI (Enterprise WeChat Command Line Interface)

Official command-line tool from WeCom Open Platform — enables both humans and AI Agents to operate WeCom resources from the terminal.

> Official GitHub: https://github.com/WecomTeam/wecom-cli
> Official Help: https://open.work.weixin.qq.com/help2/pc/21676

## Installation

```bash
# Install CLI
npm install -g @wecom/cli

# Install CLI Skill (required)
npx skills add WeComTeam/wecom-cli -y -g

# Configure credentials (interactive, one-time only)
wecom-cli init
```

### Prerequisites

- Supported platforms: macOS (x64/arm64), Linux (x64/arm64), and Windows (x64)
- Node.js >= 18
- WeCom account (**currently only available for enterprises with ≤ 10 members**)
- (Optional) Bot ID and Secret for intelligent chatbot

## Feature Coverage

Covers core WeCom business categories:

| Category | Capabilities |
|------|------|
| 👤 Contacts | Get visible member list, search by name/alias, etc. |
| ✅ Todos | Create/Read/Update/Delete todos, track user processing status, etc. |
| 🎥 Meetings | Create scheduled meetings, cancel meetings, update invitees, query lists and details, etc. |
| 💬 Messages | Query conversation list, fetch message records (text/image/file/voice/video), download multimedia, send text, etc. |
| 📅 Schedules | Schedule CRUD, participant management, query member availability, etc. |
| 📄 Documents | Document create/read/edit, etc. |
| 📊 Smartsheets | Smartsheet create, sub-sheet and field management, record CRUD, etc. |

## Agent Skills

After installing the CLI Skill, AI Agent tools (Cursor, Claude Code, etc.) can operate WeCom via natural language.

### Skill List

| Skill ID | Function |
|----------|------|
| wecomcli-lookup-contact | Contact search (by name/alias) |
| wecomcli-get-todo-list | Get todo list |
| wecomcli-get-todo-detail | Get todo details |
| wecomcli-edit-todo | Create/Update/Delete todos |
| wecomcli-create-meeting | Schedule a meeting |
| wecomcli-edit-meeting | Update/cancel meetings |
| wecomcli-get-meeting | Query meeting list and details |
| wecomcli-get-msg | Conversation list, message fetching, media download, send text |
| wecomcli-manage-schedule | Schedule CRUD, participant management, availability lookup |
| wecomcli-manage-doc | Document create/read/edit |
| wecomcli-manage-smartsheet-schema | Smartsheet create, field management |
| wecomcli-manage-smartsheet-data | Smartsheet record CRUD |

## Usage Examples

```bash
# List visible contacts
wecom-cli contact get_userlist '{}'

# Create a todo
wecom-cli todo create '{"title": "Weekly report", "due_date": "2026-04-10"}'

# View meeting list
wecom-cli meeting list '{}'
```

## Limitations

Currently only available for **enterprises with ≤ 10 members**.

## Security Rules

- Confirm user intent before write/delete operations
- Never output API keys to terminal in plain text
- Credentials are configured via interactive initialization with secure storage

## Pre-built Scripts

### scripts/setup.py
WeCom CLI installation and configuration script.

```bash
python3 scripts/setup.py
```

### scripts/wecom_quick.py
Quick shortcuts for common WeCom operations.

```bash
python3 scripts/wecom_quick.py send-msg --to xxx --content "Hello"
python3 scripts/wecom_quick.py contacts
python3 scripts/wecom_quick.py create-doc --title "New Document"
python3 scripts/wecom_quick.py schedule
```
