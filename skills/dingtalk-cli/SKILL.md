---
name: openakita/skills@dingtalk-cli
description: "DingTalk Workspace CLI (dws) - officially open-sourced cross-platform CLI tool from DingTalk. Provides 86 commands across 12 products: Contact, Chat, Bot, Calendar, Todo, Approval, Attendance, Ding, Report, AITable, Workbench, DevDoc. Built in Go with zero-trust security architecture. Use when user wants to operate DingTalk resources."
license: Apache-2.0
metadata:
  author: DingTalk-Real-AI
  version: "1.0.10"
---

# DingTalk CLI Workspace CLI (dws)

CLI, Go. and AI Agent,. 

> GitHub: https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli
> 1500+ Stars | Apache-2.0

## Installation

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.ps1 | iex
```

###

```bash
# npm ( Node.js) 
npm install -g dingtalk-workspace-cli

#: GitHub Releases Download
# https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases
```

##

```bash
dws auth login # AutomaticOpen
dws auth login --device # (Docker, SSH, CI) 
```

. Use **PBKDF2 + AES-256-GCM**. 

### Customization (CI/CD, ISV ) 

```bash
dws auth login --client-id <your-app-key> --client-secret <your-app-secret>
```

## Product (86, 12 ) 

| | | | Description |
|------|------|--------|------|
| contact | 6 | user dept | /Search,,, current user |
| chat | 10 | message group search | Manage, Manage,, webhook |
| bot | 6 | bot group message search | Create/Search, /, webhook, |
| calendar | 13 | event room participant busy | CRUD, will,, willManage |
| todo | 6 | task | Create,, Update,,, Delete |
| approval | 9 | approval | //,,, |
| attendance | 4 | record shift summary rules |,,, |
| ding | 2 | message | Send/ DING |
| report | 7 | create list detail template stats sent | Create,,, |
| AI aitable | 20 | base table record field attachment template | Full |
| workbench | 2 | app | |
| devdoc | 1 | article | Searchand |

## AI Agent

### Smart

Automatic AI: 

| Agent | dws Automatic |
|------------|---------------|
| --userId | --user-id |
| --limit100 | --limit 100 |
| --tabel-id | --table-id |
| --USER-ID | --user-id |

### Schema

Agent,: 

```bash
# have
dws schema --jq '.products[] | {id, tool_count: (.tools | length)}'

# View Schema
dws schema aitable.query_records --jq '.tool.parameters'
```

### jq and

```bash
#, token
dws aitable record query --base-id BASE_ID --table-id TABLE_ID --jq '.invocation.params'
```

### and

```bash
# Read
dws chat message send-by-bot --robot-code BOT_CODE --group GROUP_ID \
--title "" --text @report.md

# stdin Read
cat report.md | dws chat message send-by-bot --robot-code BOT_CODE --group GROUP_ID \
--title ""
```

## Agent Skills

Full Agent Skill (skills/ ): 

```bash
# skills
curl -fsSL https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install-skills.sh | sh
```

### Includes

| | | Description |
|------|------|------|
| Master Skill | SKILL.md |,,, |
| | references/products/*.md | |
| | references/intent-guide.md | |
| | references/global-reference.md |,, flags |
| | references/error-codes.md | + |
| Resume | references/recovery-guide.md | RECOVERY_EVENT_ID |
| | scripts/*.py | 13 Python |

### Python

| | |
|------|------|
| calendar_schedule_meeting.py | Create + will + Findwill |
| calendar_free_slot_finder.py | Find, Recommendationswill |
| calendar_today_agenda.py | View// |
| import_records.py | CSV/JSON AI |
| bulk_add_fields.py | AI |
| todo_batch_create.py | JSON Create (, ) |
| todo_daily_summary.py | / |
| todo_overdue_check.py | |
| contact_dept_members.py | SearchListAll |
| attendance_my_record.py | View |
| attendance_team_shift.py | and |
| report_inbox_today.py | View |

## Secure

: not, Token not, not, not. 

| | |
|------|------|
| | PBKDF2 + AES-256-GCM, Based on MAC |
| |, CRLF, Unicode |
| | DWS_TRUSTED_DOMAINS Default *.dingtalk.com, bearer token not |
| HTTPS | haveneed TLS |
| Dry-run | --dry-run CallnotExecute, |
| | Client ID / Secret inUse |

## Upgrade

```bash
dws upgrade #
dws upgrade --check #
dws upgrade --rollback #
```

## Quick Start

```bash
dws contact user search --keyword "engineering" # Search
dws calendar event list # List
dws todo task create --title "" --executors "<userId>" # Create
dws todo task list --dry-run # (notExecute) 
```

## Pre-built Scripts

### scripts/dws_setup.py
CLI and. 

```bash
python3 scripts/dws_setup.py install
python3 scripts/dws_setup.py auth
python3 scripts/dws_setup.py status
```

### scripts/dws_quick.py
Shortcut script for common DingTalk operations (thin wrapper over `dws`; command paths use `dws schema` output as the single source of truth).

```bash
# Send a message to a group via a bot (--text supports @file references, matching the dws CLI)
python3 scripts/dws_quick.py send --robot-code <BOT> --group <GID> --text "Hello" --title "Notification"

# Search contacts
python3 scripts/dws_quick.py contacts --keyword "engineering"

# List calendar events
python3 scripts/dws_quick.py calendar

# Create a todo (multiple executors separated by commas)
python3 scripts/dws_quick.py todo --title "Quarterly report" --executors "userId1,userId2"

# Pass through any dws subcommand (quick discovery of namespaces like attendance/approval)
python3 scripts/dws_quick.py raw -- attendance --help
python3 scripts/dws_quick.py raw -- approval instance list --help
```
