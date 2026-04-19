---
name: openakita/skills@tencent-meeting
description: "Tencent Meeting MCP assistant for meeting lifecycle management. Create, modify, cancel meetings, track attendance, export recordings, query transcripts, and generate smart minutes. Use when user mentions online meetings, video conferencing, or Tencent Meeting operations."
license: MIT
metadata:
 author: openakita
 version: "1.0.6"
 openclaw:
 requires:
 bins: ["python3"]
 env: ["TENCENT_MEETING_TOKEN"]
 primaryEnv: TENCENT_MEETING_TOKEN
 category: tencent
 tencentTokenMode: custom
 tokenUrl: "https://mcp.meeting.tencent.com/mcp/wemeet-open/v1"
requires:
 env: [TENCENT_MEETING_TOKEN]
---

# Tencent Meeting MCP

## Overview

willProvidesFull MCP, willManage, Manage, and. 

Full Call,: `references/api_references.md`

##

**Run**: `python3`, UseExecute `python3 --version`. 

**Token **: https://meeting.tencent.com/ai-skill Get Token, `TENCENT_MEETING_TOKEN`. 

## Core

### Time

**Default**: Asia/Shanghai (UTC+8)

** (Call `convert_timestamp`) **: 
- Use"", "", "", **Call `convert_timestamp`** (not) Get current
- Based onReturns `time_now_str`, `time_yesterday_str`, `time_week_str`
- ****

****: ISO 8601, `2026-03-25T15:00:00+08:00`

###

- orwill, will

###

haveReturns `X-Tc-Trace` or `rpcUuid`, **** (Used for) 

##

| | Use |
|---------|---------|
|, Create, will | `schedule_meeting` |
|, Updatewill | `update_meeting` |
|, Deletewill | `cancel_meeting` |
| will (have meeting_id) | `get_meeting` |
| will (havewill) | `get_meeting_by_code` |
| Viewwill | `get_meeting_participants` |
| View | `get_meeting_invitees` |
| View | `get_waiting_room` |
| View/ will | `get_user_meetings` |
| List already will | `get_user_ended_meetings` |
| View | `get_records_list` |
| GetDownload | `get_record_addresses` |
| View | `get_transcripts_details` |
| | `get_transcripts_paragraphs` |
| Search | `search_transcripts` |
| Getneed, AI | `get_smart_minutes` |

## Pre-built Scripts

skill MCP ( Python stdlib, ). 

### scripts/tencent_meeting_mcp.py (Recommendations) 

MCP JSON-RPC 2.0, SupportsAll 16+ Call. 

```bash
# List all
python3 scripts/tencent_meeting_mcp.py tools/list

# will (Viawill) 
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_meeting_by_code", "arguments": {"meeting_code": "904854736", "_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# Get current (Used for) 
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "convert_timestamp", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# View/ will
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_user_meetings", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# List already will
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_user_ended_meetings", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'
```

### scripts/tencent_meeting.py ( REST ) 

REST API, Provides CLI. 

```bash
python3 scripts/tencent_meeting.py create --subject "will" --start "2026-04-07 10:00" --end "2026-04-07 11:00"
python3 scripts/tencent_meeting.py list
python3 scripts/tencent_meeting.py get --meeting-id xxx
```