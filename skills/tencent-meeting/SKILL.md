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

# Tencent Meeting MCP 服务

## Overview

本技能为腾讯会议ProvidesFull的 MCP 工具集，涵盖会议Manage、成员Manage、录制与转写查询等核心功能。

Full的工具Call示例，请参考：`references/api_references.md`

## 环境配置

**Run环境**：依赖 `python3`，首次UseExecute `python3 --version` 检查。

**Token 配置**：访问 https://meeting.tencent.com/ai-skill Get Token，配置环境变量 `TENCENT_MEETING_TOKEN`。

## 核心规范

### 时间处理

**Default时区**：Asia/Shanghai (UTC+8)

**相对时间（必须先Call `convert_timestamp`）**：
- 用户Use"今天"、"明天"、"下周一"等描述时，**必须先Call `convert_timestamp`**（不传参数）Get current时间
- Based onReturns的 `time_now_str`、`time_yesterday_str`、`time_week_str` 进行推算
- **禁止依赖模型自身猜测当前时间**

**时间格式**：ISO 8601，如 `2026-03-25T15:00:00+08:00`

### 敏感操作

- 修改或取消会议前，必须向用户展示会议信息并确认

### 追踪信息

所有工具Returns的 `X-Tc-Trace` 或 `rpcUuid` 字段，**必须明确展示**给用户（Used for问题排查）

## 触发场景

| 用户意图 | Use工具 |
|---------|---------|
| 预约、Create、安排会议 | `schedule_meeting` |
| 修改、Update会议 | `update_meeting` |
| 取消、Delete会议 | `cancel_meeting` |
| 查询会议详情（有 meeting_id） | `get_meeting` |
| 查询会议详情（有会议号） | `get_meeting_by_code` |
| View实际参会人员 | `get_meeting_participants` |
| View受邀成员 | `get_meeting_invitees` |
| View等候室成员 | `get_waiting_room` |
| View即将开始/进行中的会议 | `get_user_meetings` |
| List already结束的历史会议 | `get_user_ended_meetings` |
| View录制列表 | `get_records_list` |
| Get录制Download地址 | `get_record_addresses` |
| View转写全文 | `get_transcripts_details` |
| 分页浏览转写段落 | `get_transcripts_paragraphs` |
| Search转写关键词 | `search_transcripts` |
| Get智能纪要、AI 总结 | `get_smart_minutes` |

## Pre-built Scripts

本 skill 内置官方 MCP 客户端脚本（纯 Python stdlib，零依赖）。

### scripts/tencent_meeting_mcp.py（Recommendations）

官方 MCP JSON-RPC 2.0 客户端，SupportsAll 16+ 种工具Call。

```bash
# List all可用工具
python3 scripts/tencent_meeting_mcp.py tools/list

# 查询会议详情（Via会议号）
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_meeting_by_code", "arguments": {"meeting_code": "904854736", "_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# Get current时间戳（Used for相对时间计算）
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "convert_timestamp", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# View即将开始/进行中的会议
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_user_meetings", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# List already结束的会议
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_user_ended_meetings", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'
```

### scripts/tencent_meeting.py（旧版 REST 封装）

保留的 REST API 封装版本，Provides更简单的 CLI 接口。

```bash
python3 scripts/tencent_meeting.py create --subject "周会" --start "2026-04-07 10:00" --end "2026-04-07 11:00"
python3 scripts/tencent_meeting.py list
python3 scripts/tencent_meeting.py get --meeting-id xxx
```
