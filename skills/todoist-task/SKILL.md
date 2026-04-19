---
name: openakita/skills@todoist-task
description: Manage Todoist tasks, projects, sections, labels, and filters via REST API v2. Supports task CRUD, due dates, priorities, recurring tasks, project organization, and advanced filtering. Based on doggy8088/agent-skills/todoist-api, using curl + jq.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# Todoist Task — Todoist manage

## When to Use

- When the user needscreate, View, updateordelete Todoist
- needmanage Todoist and
- needSet,, 
- need
- needand
- need Todoist
- needmanage (//) 

---

## Prerequisites

###

| | Description |
|--------|------|
| `TODOIST_API_TOKEN` | Todoist API Token |

**get Token: **

1. Todoist → Set → →
2. or: https://app.todoist.com/app/settings/integrations/developer
3. API Token

in `.env`: 

```
TODOIST_API_TOKEN=your_todoist_api_token_here
```

### Tool

| | | Description |
|------|------|------|
| `curl` | HTTP API Call | |
| `jq` | JSON | Windows: `choco install jq`; macOS: `brew install jq` |

### Validate

```bash
curl -s "https://api.todoist.com/rest/v2/projects" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.[0].name'
```

---

## Instructions

### Todoist API v2

haveSend `https://api.todoist.com/rest/v2/`, Bearer Token: 

```bash
curl -s "https://api.todoist.com/rest/v2/{endpoint}" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json"
```

### Core

| | Description |
|------|------|
| Task () |, Todoist |
| Project () |, |
| Section () | / |
| Label () | |
| Filter () | |
| Priority () | 1=, 2=, 3=, 4= (API and UI ) |
| Due Date () | Supportsand ISO 8601 |

### Priority

API and Todoist UI DisplayYes****: 

| API | UI Display | |
|--------|---------|------|
| `priority: 1` | Priority 4 () | |
| `priority: 2` | Priority 3 () | |
| `priority: 3` | Priority 2 () | |
| `priority: 4` | Priority 1 () | |

---

## Workflows

### Workflow 1: CRUD

#### create

**create**

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d '{
"content": "",
"description": "IncludesandTimeline",
"due_string": "3",
 "due_lang": "zh",
 "priority": 4,
 "project_id": "PROJECT_ID"
 }' | jq '.'
```

**and create**

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

**create**

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d '{
"content": "",
 "parent_id": "PARENT_TASK_ID",
 "priority": 2
 }' | jq '.'
```

#### View

**gethave**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

****

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?project_id=PROJECT_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

****

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?label=urgent" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

**Use**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?filter=today%20%7C%20overdue" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

**get**

```bash
curl -s "https://api.todoist.com/rest/v2/tasks/TASK_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

#### update

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks/TASK_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d '{
"content": "update ",
"due_string": "",
 "due_lang": "zh",
 "priority": 3
 }' | jq '.'
```

#### Complete

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks/TASK_ID/close" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

#### Open

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/tasks/TASK_ID/reopen" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

#### delete

```bash
curl -s -X DELETE "https://api.todoist.com/rest/v2/tasks/TASK_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

---

### Workflow 2: manage

#### listhave

```bash
curl -s "https://api.todoist.com/rest/v2/projects" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.[] | {id, name, color, is_favorite}'
```

#### create

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/projects" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d '{
"name": "Q2 ",
 "color": "blue",
 "is_favorite": true,
 "view_style": "board"
 }' | jq '.'
```

`view_style`: 
- `list`: (Default) 
- `board`: 

#### update

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/projects/PROJECT_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d '{
"name": "Q2 () ",
 "color": "grey"
 }' | jq '.'
```

#### delete

```bash
curl -s -X DELETE "https://api.todoist.com/rest/v2/projects/PROJECT_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN"
```

---

### Workflow 3: manage

 (Section) Used forin,. 

#### list

```bash
curl -s "https://api.todoist.com/rest/v2/sections?project_id=PROJECT_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.'
```

#### create

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/sections" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d '{
 "project_id": "PROJECT_ID",
"name": ""
 }' | jq '.'
```

####

****

```bash
for section in "handle" "" "" ""; do
 curl -s -X POST "https://api.todoist.com/rest/v2/sections" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d "{\"project_id\": \"PROJECT_ID\", \"name\": \"$section\"}"
done
```

**GTD **

```bash
for section in "" "" "" "/also" ""; do
 curl -s -X POST "https://api.todoist.com/rest/v2/sections" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d "{\"project_id\": \"PROJECT_ID\", \"name\": \"$section\"}"
done
```

---

### Workflow 4: manage

#### listhave

```bash
curl -s "https://api.todoist.com/rest/v2/labels" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq '.[] | {id, name, color, is_favorite}'
```

#### create

```bash
curl -s -X POST "https://api.todoist.com/rest/v2/labels" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d '{
 "name": "high-energy",
 "color": "red"
 }' | jq '.'
```

#### Recommendations

| | | |
|------|------|------|
| | `high-energy`, `low-energy` | |
| | `5min`, `15min`, `30min`, `1hour` | |
| | `at-computer`, `at-phone`, `at-office`, `anywhere` | |
| Type | `meeting`, `deep-work`, `admin`, `learning` | |

---

### Workflow 5: and

#### (Recommendations) 

Todoist Supports: 

```json
{
"due_string": "3",
 "due_lang": "zh"
}
```

| | | |
|---------|---------|---------|
| | today | |
| | tomorrow | |
| | in 2 days | |
| | next Monday | |
| | every day | |
| | every Monday | |
| 1 | every 1st | 1 |
| 315 | March 15 | |

#### ISO

```json
{
 "due_date": "2025-03-15"
}
```

or: 

```json
{
 "due_datetime": "2025-03-15T15:00:00Z"
}
```

####

```json
{
 "due_string": "every weekday at 9am",
 "due_lang": "en"
}
```

: 

| | `due_string` | Description |
|------|-------------|------|
| | `every day` | |
| | `every weekday` | |
| | `every week` | 7 |
| | `every month` | |
| | `every 3 months` | 3 |
| | `every year` | |
| 3 | `every! 3 days` | 3 |
| | `every other Monday` | |

---

### Workflow 6:

#### Filter

| | Description |
|--------|------|
| `today` | |
| `overdue` | |
| `today \| overdue` | or |
| `7 days` | 7 |
| `no date` | have |
| `p1` | 1 () |
| `p1 & today` | |
| `#` | |
| `@` | |
| `assigned to: me` | |
| `created before: -7 days` | 7 create |

#### Query

****

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?filter=today%20%7C%20overdue" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
 jq '.[] | {content, due:.due.string, priority}'
```

****

```bash
curl -s "https://api.todoist.com/rest/v2/tasks?filter=7%20days%20%26%20(p1%20%7C%20p2)" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
 jq '.[] | {content, due:.due.string, priority}'
```

---

### Workflow 7:

#### Generation

When the userin, create: 

```bash
tasks='[
{"content": "willPPT", "due_string": "", "priority": 3},
{"content": "", "due_string": "", "priority": 4},
{"content": " PR #55", "due_string": "", "priority": 2}
]'

echo "$tasks" | jq -c '.[]' | while read task; do
 curl -s -X POST "https://api.todoist.com/rest/v2/tasks" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" \
 -H "Content-Type: application/json" \
 -d "$task" | jq '{id, content, url}'
 sleep 0.3
done
```

####

```bash
task_ids=("TASK_ID_1" "TASK_ID_2" "TASK_ID_3")

for id in "${task_ids[@]}"; do
 curl -s -X POST "https://api.todoist.com/rest/v2/tasks/$id/close" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN"
 sleep 0.3
done
```

#### Tasks

```bash
curl -s "https://api.todoist.com/rest/v2/tasks" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
jq -r '["","ID","","",""], (.[] | [.content,.project_id,.priority, (.due.date // ""), (.labels | join(","))]) | @csv' \
 > tasks_export.csv
```

---

## Output Format

### Tasks

```
📋 (5 )

🔴 [P1]
📅 14:00 | 🏷️ work, urgent
📁

🟠 [P2] Review PR #42
📅 | 🏷️ code-review
📁

⚪ [P4] willneed
📅 | 🏷️ admin
📁

✅: 3 | ⏰: 1
```

### create

```
✅ create
-:
-: Q2
-: 15:00
-: P1 ()
-: deep-work
-: https://todoist.com/app/task/12345
```

---

## Common Pitfalls

### 1. API Token

****: haveReturns 401
****: `TODOIST_API_TOKEN` Set, Todoist Setget

### 2.

API `priority: 4` UI P1 (), thisYes. orinUse: 

```bash
P1_URGENT=4
P2_HIGH=3
P3_MEDIUM=2
P4_NORMAL=1
```

### 3.

Use `due_lang: "zh"`, Nowillor. 

### 4. URL

Via curl need URL: 
- `|` → `%7C`
- `&` → `%26`
- `#` → `%23`
- → `%20`

### 5. / ID Yes

Todoist REST API v2 Returns ID Yes, notYes. Use jq handle. 

### 6.

Todoist API Yes 450. sleep: 

```bash
sleep 0.3 # 300ms
```

### 7. close vs delete

- `close` ():, willAutomaticcreate
- `delete`: delete, notwillcreate

Use `close` and `delete`. 

### 8.

Use UTC. in (CST),: 

```json
{
 "due_datetime": "2025-03-15T07:00:00Z"
}
```

15:00. 

---

## Advanced Usage

###

```bash
echo "=== 📋 $(date +%Y-%m-%d) ==="
echo ""
echo "--- 🔴 ---"
curl -s "https://api.todoist.com/rest/v2/tasks?filter=overdue" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
jq -r '.[] | " ❗ \(.content) (: \(.due.date))"'
echo ""
echo "--- 📅 ---"
curl -s "https://api.todoist.com/rest/v2/tasks?filter=today" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
 jq -r '.[] | " • [\(if.priority == 4 then "P1" elif.priority == 3 then "P2" elif.priority == 2 then "P3" else "P4" end)] \(.content)"'
echo ""
echo "--- 📆 ---"
curl -s "https://api.todoist.com/rest/v2/tasks?filter=tomorrow" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | \
 jq -r '.[] | " ○ \(.content)"'
```

###

```bash
PROJECT_ID="your_project_id"
total=$(curl -s "https://api.todoist.com/rest/v2/tasks?project_id=$PROJECT_ID" \
 -H "Authorization: Bearer $TODOIST_API_TOKEN" | jq 'length')
echo "📊: $total "
```

---

## EXTEND.md

increate `EXTEND.md`: 
- Default ID and
-
- Task () 
- and
- ID