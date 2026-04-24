# 腾讯会议 MCP 工具参考

本文件提供所有工具的调用场景、关键规则与示例。详细参数说明已集成到 MCP 工具的 Schema 中，可直接通过工具描述查看。

---

## 目录

- [会议管理](#会议管理)
  - [创建会议](#1-schedule_meeting--创建会议)
  - [修改会议](#2-update_meeting--修改会议)
  - [取消会议](#3-cancel_meeting--取消会议)
  - [查询会议详情](#4-get_meeting--查询会议详情)
  - [通过会议号查询](#5-get_meeting_by_code--通过会议号查询)
- [成员管理](#成员管理)
  - [获取参会成员明细](#6-get_meeting_participants--获取参会成员明细)
  - [获取受邀成员列表](#7-get_meeting_invitees--获取受邀成员列表)
  - [查询等候室成员](#8-get_waiting_room--查询等候室成员)
  - [查询用户会议列表](#9-get_user_meetings--查询用户会议列表)
  - [查询已结束会议](#10-get_user_ended_meetings--查询已结束会议)
- [录制与转写](#录制与转写)
  - [查询录制列表](#11-get_records_list--查询录制列表)
  - [获取录制下载地址](#12-get_record_addresses--获取录制下载地址)
  - [查询转写详情](#13-get_transcripts_details--查询转写详情)
  - [查询转写段落](#14-get_transcripts_paragraphs--查询转写段落)
  - [搜索转写内容](#15-search_transcripts--搜索转写内容)
  - [获取智能纪要](#16-get_smart_minutes--获取智能纪要)

---

## 会议管理

### 1. `schedule_meeting` — 创建会议

#### 调用场景
用户要求**预约、创建、安排**一场腾讯会议时使用。

#### ⚠️ 关键规则

**非周期性会议创建**
- 必须获取：`subject`（会议主题）、`start_time`（开始时间）、`end_time`（结束时间）
- 若用户未提及结束时间，默认设置为1小时，并提示用户可修改
- 若缺少会议主题，必须提示用户输入

**周期性会议创建**
- 必须获取：`subject`、`start_time`、`end_time`、`recurring_type`（周期类型）、`until_count`（重复次数）
- 若用户未提及重复次数，默认设置为50次，并提示用户可修改
- 若缺少周期类型，必须提示用户输入

**其他限制**
- ❌ 不支持邀请人，即使创建成功也不返回邀请人信息
- ❌ 缺少会议主题时报错

#### 调用示例

```bash
# 普通会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "schedule_meeting",
  "arguments": {
    "subject": "产品周会",
    "start_time": "2026-03-25T15:00:00+08:00",
    "end_time": "2026-03-25T16:00:00+08:00"
  }
}'

# 周期性会议（每周开会，共重复5次）
python3 scripts/tencent_meeting.py tools/call '{
  "name": "schedule_meeting",
  "arguments": {
    "subject": "每周例会",
    "start_time": "2026-03-25T15:00:00+08:00",
    "end_time": "2026-03-25T16:00:00+08:00",
    "meeting_type": 1,
    "recurring_rule": {
      "recurring_type": 2,
      "until_type": 1,
      "until_count": 5
    }
  }
}'
```

---

### 2. `update_meeting` — 修改会议

#### 调用场景
用户要求**修改、更新**已有会议的主题、时间或其他信息时使用。

#### ⚠️ 关键规则
- 🔴 **强制二次确认**：修改前必须向用户展示要修改的会议信息，用户确认后再执行修改
- 若用户提供的是**会议号（meeting_code）**而非 meeting_id，先通过 `get_meeting_by_code` 查询 meeting_id
- 可修改：主题、时间、密码、时区、会议类型、入会限制、等候室、周期性规则等

#### 调用示例

```bash
# 修改非周期性会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "update_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "subject": "新主题",
    "start_time": "2026-03-25T16:00:00+08:00",
    "end_time": "2026-03-25T17:00:00+08:00"
  }
}'

# 修改周期性会议其中一场子会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "update_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "start_time": "2026-03-26T10:00:00+08:00",
    "end_time": "2026-03-26T11:00:00+08:00",
    "meeting_type": 1,
    "recurring_rule": {
      "sub_meeting_id": "yyy"
    }
  }
}'
```

---

### 3. `cancel_meeting` — 取消会议

#### 调用场景
用户要求**取消、删除**已有会议时使用。

#### ⚠️ 关键规则
- 🔴 **强制二次确认**：取消前必须向用户展示要取消的会议信息，用户确认后再执行取消
- 若用户提供的是**会议号（meeting_code）**而非 meeting_id，先通过 `get_meeting_by_code` 查询 meeting_id
- 取消整场周期性会议时，需传入 `meeting_type: 1`
- 取消周期性会议的某个子会议时，需传入 `sub_meeting_id`

#### 调用示例

```bash
# 取消普通会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "cancel_meeting",
  "arguments": {
    "meeting_id": "xxx"
  }
}'

# 取消周期性会议的某个子会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "cancel_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "sub_meeting_id": "yyy"
  }
}'

# 取消整场周期性会议
python3 scripts/tencent_meeting.py tools/call '{
  "name": "cancel_meeting",
  "arguments": {
    "meeting_id": "xxx",
    "meeting_type": 1
  }
}'
```

---

### 4. `get_meeting` — 查询会议详情

#### 调用场景
用户要求通过 meeting_id **查询会议详情**时使用。

#### ⚠️ 关键规则
- 返回主持人和参会者时，如果没有特殊要求，只返回用户昵称（不返回用户ID）

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_meeting",
  "arguments": {
    "meeting_id": "xxx"
  }
}'
```

---

### 5. `get_meeting_by_code` — 通过会议号查询

#### 调用场景
用户提供**会议号（meeting_code）查询会议信息**时使用。

#### ⚠️ 关键规则
- 此工具常作为其他工具的前置步骤，用于将 meeting_code 转换为 meeting_id

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_meeting_by_code",
  "arguments": {
    "meeting_code": "904854736"
  }
}'
```

---

## 成员管理

### 6. `get_meeting_participants` — 获取参会成员明细

#### 调用场景
用户要求查看、询问**实际参会人员、谁参加了会议、参会明细**相关信息时使用。

#### ⚠️ 关键规则
- 周期性会议必须传入 `sub_meeting_id`（可通过 `get_meeting` 获取 `current_sub_meeting_id`）
- 当参会成员较多时，使用 `pos` 和 `size` 进行分页查询
- 根据 `has_remaining` 判断是否需要继续查询

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_meeting_participants",
  "arguments": {
    "meeting_id": "xxx",
    "size": "20",
    "pos": "0"
  }
}'
```

---

### 7. `get_meeting_invitees` — 获取受邀成员列表

#### 调用场景
用户要求查看、询问**受邀成员、邀请了谁**相关信息时使用。

#### ⚠️ 关键规则
- 返回邀请人时，如果没有特殊要求，只返回用户昵称（不返回用户ID）
- 根据 `has_remaining` 判断是否需要继续查询

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_meeting_invitees",
  "arguments": {
    "meeting_id": "xxx"
  }
}'
```

---

### 8. `get_waiting_room` — 查询等候室成员

#### 调用场景
用户要求查看、询问**等候室成员**相关信息时使用。

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_waiting_room",
  "arguments": {
    "meeting_id": "xxx"
  }
}'
```

---

### 9. `get_user_meetings` — 查询用户会议列表

#### 调用场景
用户要求查看、询问**自己的会议列表、近期会议、我的会议**相关信息时使用。

#### ⚠️ 关键规则
- ⚠️ 只能查询**即将开始、正在进行中**的会议
- 若用户需要查询今天的会议，需配合 `get_user_ended_meetings` 使用并做聚合去重
- 根据 `remaining`、`next_pos`、`next_cursory` 进行翻页查询

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_user_meetings",
  "arguments": {
    "pos": 0,
    "cursory": 20,
    "is_show_all_sub_meetings": 0
  }
}'
```

---

### 10. `get_user_ended_meetings` — 查询已结束会议

#### 调用场景
用户要求查看、询问**已结束的会议、历史会议**相关信息时使用。

#### ⚠️ 关键规则
- 必须指定时间范围（`start_time` 和 `end_time`）
- 若用户需要查询今天的会议，需配合 `get_user_meetings` 使用并做聚合去重

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_user_ended_meetings",
  "arguments": {
    "start_time": "2026-03-25T00:00:00+08:00",
    "end_time": "2026-03-25T23:59:59+08:00"
  }
}'
```

---

## 录制与转写

### 11. `get_records_list` — 查询录制列表

#### 调用场景
用户要求查看**会议录制列表、录制文件、录制回放**时使用。

#### ⚠️ 关键规则
- 若用户提供的是**会议号（meeting_code）**而非 meeting_id：
  1. 先通过 `get_meeting_by_code` 查询 meeting_id
  2. 再通过 `get_records_list` 获取 `record_file_id`（后续操作需要）
- 可通过时间范围查询，也可通过 meeting_id 或 meeting_code 查询

#### 调用示例

```bash
# 按时间范围查询
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_records_list",
  "arguments": {
    "start_time": "2026-03-25T00:00:00+08:00",
    "end_time": "2026-03-25T23:59:59+08:00",
    "page_number": 1
  }
}'

# 按会议ID查询
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_records_list",
  "arguments": {
    "start_time": "2026-03-25T00:00:00+08:00",
    "end_time": "2026-03-25T23:59:59+08:00",
    "meeting_id": "xxx"
  }
}'
```

---

### 12. `get_record_addresses` — 获取录制下载地址

#### 调用场景
用户要求获取**录制下载地址、下载录制视频/音频**时使用。

#### ⚠️ 关键规则
- 若用户提供的是**会议号（meeting_code）**：
  1. 先通过 `get_meeting_by_code` 查询 meeting_id
  2. 再通过 `get_records_list` 获取 `meeting_record_id`
  3. 最后通过 `get_record_addresses` 获取下载地址

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_record_addresses",
  "arguments": {
    "meeting_record_id": "xxx"
  }
}'
```

---

### 13. `get_transcripts_details` — 查询转写详情

#### 调用场景
用户要求查看**会议转写全文、转写详情**时使用。

#### ⚠️ 关键规则
- 若用户提供的是**会议号（meeting_code）**：
  1. 先通过 `get_meeting_by_code` 查询 meeting_id
  2. 再通过 `get_records_list` 获取 `record_file_id`
  3. 最后通过 `get_transcripts_details` 获取转写内容
- 可通过 `pid` 和 `limit` 参数控制分页

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_transcripts_details",
  "arguments": {
    "record_file_id": "xxx"
  }
}'
```

---

### 14. `get_transcripts_paragraphs` — 查询转写段落

#### 调用场景
用户要求**分页浏览转写段落**时使用。

#### ⚠️ 关键规则
- 若用户提供的是**会议号（meeting_code）**：
  1. 先通过 `get_meeting_by_code` 查询 meeting_id
  2. 再通过 `get_records_list` 获取 `record_file_id`
- 返回段落 ID 列表，配合 `get_transcripts_details` 通过 pid 获取具体文本内容

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_transcripts_paragraphs",
  "arguments": {
    "record_file_id": "xxx"
  }
}'
```

---

### 15. `search_transcripts` — 搜索转写内容

#### 调用场景
用户要求在转写内容中**搜索关键词**时使用。

#### ⚠️ 关键规则
- 中文关键词需要 urlencode
- 返回匹配的段落 ID、句子 ID 和时间戳信息

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "search_transcripts",
  "arguments": {
    "record_file_id": "xxx",
    "text": "产品需求"
  }
}'
```

---

### 16. `get_smart_minutes` — 获取智能纪要

#### 调用场景
用户要求获取**智能纪要、AI纪要、会议总结**时使用。

#### 💡 推荐流程
当用户**咨询与会议相关的问题**时，按以下优先级获取信息：
1. 先使用 `get_smart_minutes` 获取智能纪要
2. 若未找到相关信息，使用 `get_transcripts_details` 获取转写详情
3. 若仍未找到，使用 `get_record_addresses` 获取录制下载地址，获取完整会议信息

#### ⚠️ 关键规则
- 支持多语言翻译：`default`（原文）、`zh`（简体中文）、`en`（英文）、`ja`（日语）
- 若录制文件有密码，需传入 `pwd` 参数

#### 调用示例

```bash
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_smart_minutes",
  "arguments": {
    "record_file_id": "xxx"
  }
}'

# 获取英文版智能纪要
python3 scripts/tencent_meeting.py tools/call '{
  "name": "get_smart_minutes",
  "arguments": {
    "record_file_id": "xxx",
    "lang": "en"
  }
}'
```

---

## 📌 通用规则

### 时间处理
- **默认时区**：Asia/Shanghai (UTC+8)
- **时间格式**：ISO 8601 标准格式（如 `2026-03-25T15:00:00+08:00`）
- **相对时间**：当用户使用"今天"、"明天"、"下周一"等相对时间时，必须先调用 `convert_timestamp` 获取准确的当前时间

### Meeting Code 转换
多个工具需要 meeting_id，若用户提供的是会议号（meeting_code），必须先通过 `get_meeting_by_code` 查询 meeting_id。

### 敏感操作确认
修改或取消会议前，必须向用户展示会议信息并确认。

### 追踪信息展示
所有工具返回的 `X-Tc-Trace` 或 `rpcUuid` 字段必须明确展示给用户。

---

## 相关文档

- **SKILL.md** - 完整的工具使用规范与触发场景
- **api_references.md**（本文档）- 调用示例与关键规则

