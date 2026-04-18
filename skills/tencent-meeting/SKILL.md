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

本技能为腾讯会议提供完整的 MCP 工具集，涵盖会议管理、成员管理、录制与转写查询等核心功能。

完整的工具调用示例，请参考：`references/api_references.md`

## 环境配置

**运行环境**：依赖 `python3`，首次使用执行 `python3 --version` 检查。

**Token 配置**：访问 https://meeting.tencent.com/ai-skill 获取 Token，配置环境变量 `TENCENT_MEETING_TOKEN`。

## 核心规范

### 时间处理

**默认时区**：Asia/Shanghai (UTC+8)

**相对时间（必须先调用 `convert_timestamp`）**：
- 用户使用"今天"、"明天"、"下周一"等描述时，**必须先调用 `convert_timestamp`**（不传参数）获取当前时间
- 基于返回的 `time_now_str`、`time_yesterday_str`、`time_week_str` 进行推算
- **禁止依赖模型自身猜测当前时间**

**时间格式**：ISO 8601，如 `2026-03-25T15:00:00+08:00`

### 敏感操作

- 修改或取消会议前，必须向用户展示会议信息并确认

### 追踪信息

所有工具返回的 `X-Tc-Trace` 或 `rpcUuid` 字段，**必须明确展示**给用户（用于问题排查）

## 触发场景

| 用户意图 | 使用工具 |
|---------|---------|
| 预约、创建、安排会议 | `schedule_meeting` |
| 修改、更新会议 | `update_meeting` |
| 取消、删除会议 | `cancel_meeting` |
| 查询会议详情（有 meeting_id） | `get_meeting` |
| 查询会议详情（有会议号） | `get_meeting_by_code` |
| 查看实际参会人员 | `get_meeting_participants` |
| 查看受邀成员 | `get_meeting_invitees` |
| 查看等候室成员 | `get_waiting_room` |
| 查看即将开始/进行中的会议 | `get_user_meetings` |
| 查看已结束的历史会议 | `get_user_ended_meetings` |
| 查看录制列表 | `get_records_list` |
| 获取录制下载地址 | `get_record_addresses` |
| 查看转写全文 | `get_transcripts_details` |
| 分页浏览转写段落 | `get_transcripts_paragraphs` |
| 搜索转写关键词 | `search_transcripts` |
| 获取智能纪要、AI 总结 | `get_smart_minutes` |

## Pre-built Scripts

本 skill 内置官方 MCP 客户端脚本（纯 Python stdlib，零依赖）。

### scripts/tencent_meeting_mcp.py（推荐）

官方 MCP JSON-RPC 2.0 客户端，支持全部 16+ 种工具调用。

```bash
# 列出所有可用工具
python3 scripts/tencent_meeting_mcp.py tools/list

# 查询会议详情（通过会议号）
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_meeting_by_code", "arguments": {"meeting_code": "904854736", "_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# 获取当前时间戳（用于相对时间计算）
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "convert_timestamp", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# 查看即将开始/进行中的会议
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_user_meetings", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'

# 查看已结束的会议
python3 scripts/tencent_meeting_mcp.py tools/call '{"name": "get_user_ended_meetings", "arguments": {"_client_info": {"os": "auto", "agent": "openakita", "model": "claude"}}}'
```

### scripts/tencent_meeting.py（旧版 REST 封装）

保留的 REST API 封装版本，提供更简单的 CLI 接口。

```bash
python3 scripts/tencent_meeting.py create --subject "周会" --start "2026-04-07 10:00" --end "2026-04-07 11:00"
python3 scripts/tencent_meeting.py list
python3 scripts/tencent_meeting.py get --meeting-id xxx
```
