---
name: openakita/skills@qq-channel
description: "QQ Channel (Tencent Channel) bot management skill. Manage channels, sub-channels, members, messages, announcements, and schedules via QQ Bot API. Use when user wants to operate QQ channels, send messages to channels, or manage channel members."
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# QQ Channel Management

Manage Tencent QQ Channel messages, members, and content via the QQ Bot API.

## Prerequisites

- Register and create a bot at the QQ Bot Open Platform: https://bot.q.qq.com
- Obtain AppID and Token
- Configure QQBot authentication headers

## Core Capabilities

| Feature | Description |
|------|------|
| Channel Management | Get channel list, channel details |
| Sub-channel Management | Create/modify/delete sub-channels |
| Message Sending | Send text/image/Markdown messages |
| Member Management | Member list, role group permissions |
| Announcement Management | Create/delete announcements |
| Schedule Management | Create/query schedules |

## API Authentication

Use `getAppAccessToken` to obtain a Token. Include `Authorization: QQBot {token}` in request headers.

## Pre-built Scripts

### scripts/qq_bot.py
QQ Channel Bot API wrapper. Requires `QQ_BOT_APPID` and `QQ_BOT_TOKEN`.

```bash
python3 scripts/qq_bot.py guilds
python3 scripts/qq_bot.py channels --guild-id 123456
python3 scripts/qq_bot.py send --channel-id 789 --content "Hello"
```
