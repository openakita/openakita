---
name: openakita/skills@qq-channel
description: "QQ Channel (Tencent Channel) bot management skill. Manage channels, sub-channels, members, messages, announcements, and schedules via QQ Bot API. Use when user wants to operate QQ channels, send messages to channels, or manage channel members."
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# QQ ChannelManage

Via QQ 机器人 API Manage腾讯频道/QQ 频道的消息、成员和内容。

## Prerequisites

- 在 QQ 机器人开放平台 https://bot.q.qq.com 注册并Create机器人
- Get AppID 和 Token
- Set QQBot 鉴权头信息

## Core Capabilities

| 功能 | Description |
|------|------|
| 频道Manage | Get频道列表、频道详情 |
| 子频道Manage | Create/修改/Delete子频道 |
| 消息Send | Send文本/图片/Markdown 消息 |
| 成员Manage | 成员列表、身份组权限 |
| 公告Manage | Create/Delete公告 |
| 日程Manage | Create/查询日程 |

## API 鉴权

Use getAppAccessToken Get Token，请求头携带 Authorization: QQBot {token}。

## Pre-built Scripts

### scripts/qq_bot.py
QQ 频道机器人 API 封装，需Set QQ_BOT_APPID 和 QQ_BOT_TOKEN。

```bash
python3 scripts/qq_bot.py guilds
python3 scripts/qq_bot.py channels --guild-id 123456
python3 scripts/qq_bot.py send --channel-id 789 --content "Hello"
```
