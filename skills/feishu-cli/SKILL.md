---
name: openakita/skills@feishu-cli
description: "Feishu/Lark CLI - official open-source CLI tool from Feishu for AI Agents. Provides 200+ commands across 12 business domains: IM, Docs, Sheets, Base (Bitable), Calendar, Video Meeting, Mail, Tasks, Wiki, Drive, Contacts, Search. Supports both user identity and bot identity authentication. Use when user wants to operate Feishu/Lark resources."
license: MIT
metadata:
  author: larksuite
  version: "1.0.8"
---

# Feishu CLI (lark-cli)

Feishu's official open-source command-line tool, providing AI Agents with a standardized execution gateway to connect with Feishu business systems. Once installed, the Agent can directly read messages, check calendars, edit documents, create bitables, send emails, and effectively complete tasks within Feishu.

> Official GitHub: https://github.com/larksuite/cli (7.3k+ Stars)
> Official introduction: https://www.feishu.cn/content/article/7623291503305083853
> npm: https://www.npmjs.com/package/@larksuite/cli

## Installation

```bash
# Step 1: Install lark-cli
npm install -g @larksuite/cli

# Step 2: Install related Skills
npx skills add https://github.com/larksuite/cli -y -g

# Step 3: Initialize app configuration (creates a new app by default, or select an existing one)
lark-cli config init --new
```

After installation, restart the AI Agent tool to ensure skills are fully loaded.

## Authentication

Feishu CLI supports two working modes:

### App Identity (Bot)
No user authorization required. The AI can execute actions such as sending messages and creating documents, but cannot access user personal data (e.g., calendar, private messages, inbox). Simply enable the corresponding scopes in the Feishu developer console.

### User Identity (User)
The AI can access the user's personal calendar, messages, documents, and perform operations on the user's behalf. Requires a one-time user authorization:

```bash
lark-cli auth login
```

After execution, open the link and confirm in Feishu. Subsequently, the AI will automatically prompt for authorization whenever it needs to access personal data.

### Identity Selection Principles

- Bot mode cannot see user resources (calendar, cloud drive documents, mailbox, etc.)
- Bot mode cannot act on behalf of the user
- Operations involving personal data must use User identity

### Handling Insufficient Permissions

- Bot identity: Provide the console_url to the user to enable the scope in the admin console
- User identity: `lark-cli auth login --scope "missing_scope"`

## Core Business Domains

| Business Domain | Core Capabilities |
|--------|---------|
| Messages & Groups | Search messages and groups, send messages, reply to threads |
| Cloud Documents | Create documents, read content, edit body text, collaborative commenting |
| Cloud Drive | Upload/download files, manage permissions, handle comments |
| Spreadsheets | Create spreadsheets, read/write cells, batch updates |
| Bitable | Manage data tables, fields, records, views, dashboards, automation |
| Calendar | Query events, create meetings, check availability, recommend times |
| Video Meetings | Search meetings, get minutes and transcripts, link calendar documents |
| Mailbox | Search, read, draft, send, reply to, and archive emails |
| Tasks | Create tasks, update status, manage checklists and subtasks |
| Knowledge Base | Query spaces, manage nodes and document hierarchy |
| Contacts | Query users, search colleagues, view departments |
| Search | Search groups, messages, documents, etc. |

## Typical Use Cases

### Automated Meeting Action Items
Read meeting minutes and transcripts, extract action items, automatically create documents for the user, send messages, and schedule meetings.

### Human-AI Collaborative Document Writing
The AI creates a first draft directly in a Feishu document, the user provides feedback via comments, and the AI revises the body text based on comments, iterating continuously. Conversely, the AI can also act as a reviewer and leave comments. Supports bidirectional conversion between Markdown and Feishu documents.

### Cross-Timezone Smart Scheduling
The AI automatically gathers group members, checks each person's calendar availability, considers everyone's time zones, and recommends suitable meeting times.

### Calendar Audit to Bitable Dashboard
Pull calendar data, tag and categorize meetings, write to a bitable to generate a dashboard for visualizing time allocation.

### Smart Unread Email Classification
The AI periodically scans unread emails, classifies them by priority, pushes summaries of important emails to the group chat, and automatically archives low-priority ones.

## Verify Installation

```bash
lark-cli help          # View command overview
lark-cli auth status   # View current login status
```

## Security Rules

- Never output secret keys (appSecret, accessToken) in plain text to the terminal
- Always confirm user intent before write/delete operations
- Use `--dry-run` to preview dangerous requests

## Updates

After executing a lark-cli command, if a new version is detected, the output will include an `_notice.update` field. Update command:

```bash
npm update -g @larksuite/cli && npx skills add larksuite/cli -g -y
```

## Supports International Lark Version

Simply run `lark-cli config init` and configure the international Lark app to use it.

## Pre-built Scripts

### scripts/setup.py
Feishu lark-cli installation and configuration script.

```bash
python3 scripts/setup.py
```

### scripts/feishu_quick.py
Quick script for common Feishu operations.

```bash
python3 scripts/feishu_quick.py send-msg --receive-id xxx --content "Hello"
python3 scripts/feishu_quick.py list-chats
python3 scripts/feishu_quick.py create-doc --folder-token xxx --title "New Document"
python3 scripts/feishu_quick.py list-events --calendar-id xxx
python3 scripts/feishu_quick.py create-task --summary "Todo Item"
```
