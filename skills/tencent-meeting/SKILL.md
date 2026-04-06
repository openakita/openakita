---
name: openakita/skills@tencent-meeting
description: "Tencent Meeting skill for meeting lifecycle management. Create, modify, cancel meetings, track attendance, export recordings, generate meeting summaries. Use when user mentions online meetings, video conferencing, or Tencent Meeting operations."
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
requires:
  env: [TENCENT_MEETING_TOKEN]
---

# 腾讯会议

腾讯会议智能管理技能，支持会议全生命周期管理。

## 配置

需要 mcporter CLI 和腾讯会议 Token：
npm install -g mcporter
export TENCENT_MEETING_TOKEN="your_token"

## 核心能力

### 会前
- 快捷创建会议
- 修改/取消会议
- 查询个人日程

### 会中
- 智能参会统计
- 实时获取参会成员明细

### 会后
- 云录制提取
- 智能纪要生成
- 会议转写检索与导出

## 会议录制导出

支持从公开分享链接导出：
- AI 全文摘要
- 智能章节（按话题自动分段）
- 关键节点（屏幕共享、成员进出）
- 完整转写（带说话人和时间戳）

## REST API

腾讯会议开放平台 REST API：https://cloud.tencent.com/document/product/1095/113415

## 预置脚本

### scripts/tencent_meeting.py
腾讯会议 REST API 封装（HMAC-SHA256 签名），需设置 TENCENT_MEETING_APPID、TENCENT_MEETING_SECRET_KEY、TENCENT_MEETING_SDK_ID。

```bash
python3 scripts/tencent_meeting.py create --subject "周会" --start "2026-04-07 10:00" --end "2026-04-07 11:00"
python3 scripts/tencent_meeting.py get --meeting-id xxx
python3 scripts/tencent_meeting.py cancel --meeting-id xxx
```
