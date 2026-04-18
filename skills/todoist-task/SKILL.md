---
name: openakita/skills@todoist-task
description: Manage Todoist tasks, projects, sections, labels, and filters via REST API v2. Supports task CRUD, due dates, priorities, recurring tasks, project organization, and advanced filtering. Based on doggy8088/agent-skills/todoist-api, using curl + jq.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Todoist Task — Todoist 任务manage

## When to Use

- When the user needscreate、View、update或delete Todoist 任务
- 需要manage Todoist 项目和分区
- 需要Set任务优先级、截止日期、重复规则
- 需要用标签分类任务
- 需要查询和过滤任务
- 需要将对话中讨论的待办事项同步到 Todoist
- 需要批量manage任务（导入/导出/整理）

---

## Prerequisites

### 必需配置

| 配置项 | Description |
|--------|------|
| `TODOIST_API_TOKEN` | Todoist API Token |

**get Token：**

1. 登录 Todoist → Set → 集成 → 开发者
2. 或访问：https://app.todoist.com/app/settings/integrations/developer
3. 复制 API Token

在 `.env` 中配置：

```
TODOIST_API_TOKEN=your_todoist_api_token_here
```

### 工具依赖

| 工具 | 用途 | Description |
|------|------|------|
| `curl` | HTTP API Call | 系统通常自带 |
| `jq` | JSON 解析 | Windows: `choco install jq`; macOS: `brew install jq` |

### 验证配置

```bash
curl -s "https://api.todoist.com/rest/v2/projects" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.[0].name'
```

---

## Instructions

### Todoist API v2 基础

所有请求Send到 `https://api.todoist.com/rest/v2/`，携带 Bearer Token：

```bash
curl -s "https://api.todoist.com/rest/v2/{endpoint}" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json"
```

### 核心概念

| 概念 | Description |
|------|------|
| Task（任务） | 待办事项，Todoist 的核心单元 |
| Project（项目） | 任务的容器，类似文件夹 |
| Section（分区） | 项目内的分组/栏目 |
| Label（标签） | 跨项目的任务分类标签 |
| Filter（过滤器） | 自定义查询条件 |
| Priority（优先级） | 1=普通, 2=低, 3=中, 4=紧急（API 与 UI 相反） |
| Due Date（截止日期） | Supports自然语言和 ISO 8601 |

### 优先级映射

注意 API 的优先级数字与 Todoist UI DisplayYes**相反**的：

| API 值 | UI Display | 颜色 |
|--------|---------|------|
| `priority: 1` | Priority 4（普通） | 无色 |
| `priority: 2` | Priority 3（低） | 蓝色 |
| `priority: 3` | Priority 2（中） | 橙色 |
| `priority: 4` | Priority 1（紧急） | 红色 |

---

## Workflows

### Workflow 1: 任务 CRUD

#### create任务

**基本create**

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "完成项目方案",
    "description": "Includes技术选型和Timeline",
    "due_string": "明天下午3点",
    "due_lang": "zh",
    "priority": 4,
    "project_id": "PROJECT_ID"
  }' | jq '.'
```

**带标签和分区的create**

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Review PR #42",
    "description": "Check error handling and test coverage",
    "due_date": "2025-03-05",
    "priority": 3,
    "project_id": "PROJECT_ID",
    "section_id": "SECTION_ID",
    "labels": ["code-review", "urgent"]
  }' | jq '.'
```

**create子任务**

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "编写单元测试",
    "parent_id": "PARENT_TASK_ID",
    "priority": 2
  }' | jq '.'
```

#### View任务

**get所有活跃任务**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

**按项目筛选**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?project_id=PROJECT_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

**按标签筛选**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?label=urgent" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

**Use过滤器查询**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?filter=today%20%7C%20overdue" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

**get单个任务**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks/TASK_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

#### update任务

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks/TASK_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "update后的任务标题",
    "due_string": "下周一",
    "due_lang": "zh",
    "priority": 3
  }' | jq '.'
```

#### 完成任务

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks/TASK_ID/close" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

#### 重新Open任务

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks/TASK_ID/reopen" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

#### delete任务

```bash
curl -s -X DELETE "https://api.todoist.com/rest/v2/tasks/TASK_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

---

### Workflow 2: 项目manage

#### list所有项目

```bash
curl -s "https://api.todoist.com/rest/v2/projects" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.[] | {id, name, color, is_favorite}'
```

#### create项目

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/projects" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Q2 产品开发",
    "color": "blue",
    "is_favorite": true,
    "view_style": "board"
  }' | jq '.'
```

`view_style` 可选：
- `list`：列表视图（Default）
- `board`：看板视图

#### update项目

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/projects/PROJECT_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Q2 产品开发（已完成）",
    "color": "grey"
  }' | jq '.'
```

#### delete项目

```bash
curl -s -X DELETE "https://api.todoist.com/rest/v2/projects/PROJECT_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

---

### Workflow 3: 分区manage

分区（Section）Used for在项目内组织任务，类似看板的列。

#### list分区

```bash
curl -s "https://api.todoist.com/rest/v2/sections?project_id=PROJECT_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

#### create分区

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/sections" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "PROJECT_ID",
    "name": "进行中"
  }' | jq '.'
```

#### 常见分区模板

**看板式**

```bash
for section in "待handle" "进行中" "待审核" "已完成"; do
  curl -s -X POST "https://api.todoist.com/rest/v2/sections" \
    -H "Authorization: Bearer $TODOIST_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"project_id\": \"PROJECT_ID\", \"name\": \"$section\"}"
done
```

**GTD 式**

```bash
for section in "收集箱" "下一步行动" "等待中" "将来/也许" "参考资料"; do
  curl -s -X POST "https://api.todoist.com/rest/v2/sections" \
    -H "Authorization: Bearer $TODOIST_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"project_id\": \"PROJECT_ID\", \"name\": \"$section\"}"
done
```

---

### Workflow 4: 标签manage

#### list所有标签

```bash
curl -s "https://api.todoist.com/rest/v2/labels" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.[] | {id, name, color, is_favorite}'
```

#### create标签

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/labels" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "high-energy",
    "color": "red"
  }' | jq '.'
```

#### Recommendations标签体系

| 类别 | 标签 | 用途 |
|------|------|------|
| 精力 | `high-energy`, `low-energy` | 按精力状态选择任务 |
| 时长 | `5min`, `15min`, `30min`, `1hour` | 按可用时间选择任务 |
| 场景 | `at-computer`, `at-phone`, `at-office`, `anywhere` | 按场景选择任务 |
| Type | `meeting`, `deep-work`, `admin`, `learning` | 按任务性质分类 |

---

### Workflow 5: 截止日期与重复任务

#### 自然语言日期（Recommendations）

Todoist Supports多语言自然语言日期：

```json
{
  "due_string": "明天下午3点",
  "due_lang": "zh"
}
```

| 中文表达 | 英文等效 | 解析结果 |
|---------|---------|---------|
| 今天 | today | 当天 |
| 明天 | tomorrow | 次日 |
| 后天 | in 2 days | 两天后 |
| 下周一 | next Monday | 下个周一 |
| 每天 | every day | 每日重复 |
| 每周一 | every Monday | 每周一重复 |
| 每月1号 | every 1st | 每月1日重复 |
| 3月15日 | March 15 | 指定日期 |

#### ISO 日期格式

```json
{
  "due_date": "2025-03-15"
}
```

或带时间：

```json
{
  "due_datetime": "2025-03-15T15:00:00Z"
}
```

#### 重复任务

```json
{
  "due_string": "every weekday at 9am",
  "due_lang": "en"
}
```

常见重复规则：

| 规则 | `due_string` | Description |
|------|-------------|------|
| 每日 | `every day` | 每天 |
| 工作日 | `every weekday` | 周一至周五 |
| 每周 | `every week` | 每 7 天 |
| 每月 | `every month` | 每月同日 |
| 每季度 | `every 3 months` | 每 3 个月 |
| 每年 | `every year` | 每年同日 |
| 完成后 3 天 | `every! 3 days` | 完成后 3 天再次出现 |
| 隔周一 | `every other Monday` | 每两周的周一 |

---

### Workflow 6: 高级过滤查询

#### 过滤器语法

| 过滤器 | Description |
|--------|------|
| `today` | 今天到期的任务 |
| `overdue` | 已过期任务 |
| `today \| overdue` | 今天到期或已过期 |
| `7 days` | 未来 7 天到期 |
| `no date` | 没有截止日期的任务 |
| `p1` | 优先级 1（紧急） |
| `p1 & today` | 紧急且今天到期 |
| `#项目名` | 指定项目的任务 |
| `@标签名` | 指定标签的任务 |
| `assigned to: me` | 分配给自己的任务 |
| `created before: -7 days` | 7 天前create的任务 |

#### 查询示例

**今日待办**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?filter=today%20%7C%20overdue" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
  jq '.[] | {content, due: .due.string, priority}'
```

**本周紧急任务**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?filter=7%20days%20%26%20(p1%20%7C%20p2)" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
  jq '.[] | {content, due: .due.string, priority}'
```

---

### Workflow 7: 批量操作

#### 从对话Generation任务

When the user在对话中提到多个待办事项时，批量create：

```bash
tasks='[
  {"content": "准备周会PPT", "due_string": "明天", "priority": 3},
  {"content": "回复客户邮件", "due_string": "今天", "priority": 4},
  {"content": "代码审查 PR #55", "due_string": "后天", "priority": 2}
]'

echo "$tasks" | jq -c '.[]' | while read task; do
  curl -s -X POST "https://api.todoist.com/rest/v2/tasks" \
    -H "Authorization: Bearer $TODOIST_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$task" | jq '{id, content, url}'
  sleep 0.3
done
```

#### 批量完成任务

```bash
task_ids=("TASK_ID_1" "TASK_ID_2" "TASK_ID_3")

for id in "${task_ids[@]}"; do
  curl -s -X POST "https://api.todoist.com/rest/v2/tasks/$id/close" \
    -H "Authorization: Bearer $TODOIST_API_TOKEN"
  sleep 0.3
done
```

#### 任务导出

```bash
curl -s "https://api.todoist.com/rest/v2/tasks" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
  jq -r '["内容","项目ID","优先级","截止日期","标签"], (.[] | [.content, .project_id, .priority, (.due.date // "无"), (.labels | join(","))]) | @csv' \
  > tasks_export.csv
```

---

## Output Format

### 任务列表输出

```
📋 今日待办 (5 项)

🔴 [P1] 回复客户紧急邮件
   📅 今天 14:00 | 🏷️ work, urgent
   📁 客户项目

🟠 [P2] Review PR #42
   📅 今天 | 🏷️ code-review
   📁 开发任务

⚪ [P4] 整理会议纪要
   📅 今天 | 🏷️ admin
   📁 日常事务

✅ 已完成: 3 项 | ⏰ 逾期: 1 项
```

### create确认

```
✅ 任务已create
- 内容: 完成项目方案
- 项目: Q2 产品开发
- 截止: 明天 15:00
- 优先级: P1 (紧急)
- 标签: deep-work
- 链接: https://todoist.com/app/task/12345
```

---

## Common Pitfalls

### 1. API Token 无效

**症状**：所有请求Returns 401
**解决**：确认 `TODOIST_API_TOKEN` 正确Set，到 Todoist Set页面重新get

### 2. 优先级数字混淆

API 的 `priority: 4` 对应 UI 的 P1（紧急），这Yes反直觉的。牢记映射关系或在代码中Use常量：

```bash
P1_URGENT=4
P2_HIGH=3
P3_MEDIUM=2
P4_NORMAL=1
```

### 3. 中文日期解析失败

Use中文自然语言日期时必须指定 `due_lang: "zh"`，No则会解析失败或解析为错误日期。

### 4. 过滤器 URL 编码

Via curl 传递过滤器时需要 URL 编码：
- `|` → `%7C`
- `&` → `%26`
- `#` → `%23`
- 空格 → `%20`

### 5. 项目/任务 ID Yes字符串

Todoist REST API v2 Returns的 ID Yes字符串类型，不Yes数字。Use jq handle时注意类型。

### 6. 批量操作超频

Todoist API 的限制Yes每分钟约 450 次请求。批量操作时添加 sleep：

```bash
sleep 0.3  # 每次请求间隔 300ms
```

### 7. 重复任务的 close vs delete

- `close`（完成）：对重复任务，会Automaticcreate下一次任务
- `delete`：永久delete，不会create下次任务

确保对重复任务Use `close` 而非 `delete`。

### 8. 时区问题

带时间的截止日期Use UTC 格式。如果用户在东八区（CST），需注意时差：

```json
{
  "due_datetime": "2025-03-15T07:00:00Z"
}
```

上述表示北京时间 15:00。

---

## 进阶用法

### 每日回顾脚本

```bash
echo "=== 📋 每日回顾 $(date +%Y-%m-%d) ==="
echo ""
echo "--- 🔴 逾期任务 ---"
curl -s "https://api.todoist.com/rest/v2/tasks?filter=overdue" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
  jq -r '.[] | "  ❗ \(.content) (原定: \(.due.date))"'
echo ""
echo "--- 📅 今日任务 ---"
curl -s "https://api.todoist.com/rest/v2/tasks?filter=today" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
  jq -r '.[] | "  • [\(if .priority == 4 then "P1" elif .priority == 3 then "P2" elif .priority == 2 then "P3" else "P4" end)] \(.content)"'
echo ""
echo "--- 📆 明日预览 ---"
curl -s "https://api.todoist.com/rest/v2/tasks?filter=tomorrow" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
  jq -r '.[] | "  ○ \(.content)"'
```

### 项目进度统计

```bash
PROJECT_ID="your_project_id"
total=$(curl -s "https://api.todoist.com/rest/v2/tasks?project_id=$PROJECT_ID" \
  -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq 'length')
echo "📊 项目进度: 剩余 $total 个活跃任务"
```

---

## EXTEND.md 扩展

用户可在技能同目录下create `EXTEND.md` 添加：
- Default项目 ID 和名称映射
- 自定义标签体系
- Task模板（如每周例行任务）
- 与其他工具的集成配置
- 团队成员 ID 映射
