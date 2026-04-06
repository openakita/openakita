# list_answers 工具参考

## 概述

获取指定问卷的回答列表，支持游标分页。返回回答总数、回答详情列表和最后一条回答 ID（用于翻页）。

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `survey_id` | number | **是** | 问卷 ID |
| `per_page` | number | 否 | 每页返回条数，默认 20，最大 1000 |
| `last_answer_id` | number | 否 | 上一页最后一条回答的 ID，用于翻页。默认 0 表示从头开始 |

## 返回值

### 成功响应

```json
{
  "total": 150,
  "list": [
    {
      "id": 1,
      "survey_id": 292192,
      "answer_id": 1,
      "respondent_nickname": "用户A",
      "started_at": "2026-01-15 10:30:00",
      "ended_at": "2026-01-15 10:35:22",
      "score": 0,
      "country": "中国",
      "province": "广东",
      "city": "深圳",
      "answer": [
        {
          "id": "p1",
          "questions": [
            {
              "id": "q-1-xxxx",
              "identity": "q-1-abcd1234",
              "title": "您对工作环境是否满意？",
              "type": "radio",
              "sub_type": 0,
              "text": "非常满意",
              "options": [
                {"id": "o1", "text": "非常满意", "selected": true},
                {"id": "o2", "text": "满意"},
                {"id": "o3", "text": "一般"},
                {"id": "o4", "text": "不满意"}
              ]
            },
            {
              "id": "q2",
              "identity": "q-1-efgh5678",
              "title": "请填写您的建议",
              "type": "textarea",
              "sub_type": 0,
              "text": "希望改善食堂伙食"
            }
          ]
        }
      ]
    }
  ],
  "last_answer_id": 20
}
```

### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | number | 问卷回答**总数**（全量统计，非当前页条数） |
| `list` | array | 当前页的回答列表（`AnswerPayload[]`） |
| `last_answer_id` | number | 当前页最后一条回答的 ID，用于请求下一页 |

### AnswerPayload 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | number | 回答记录 ID |
| `survey_id` | number | 问卷 ID |
| `answer_id` | number | 回答序号 |
| `respondent_id` | number | 答题者 ID |
| `respondent_nickname` | string | 答题者昵称 |
| `respondent_avatar` | string | 答题者头像 |
| `respondent_type` | string | 答题者类型 |
| `openid` | string | 答题者 OpenID |
| `started_at` | string | 开始答题时间 |
| `ended_at` | string | 提交答题时间 |
| `score` | number | 考试/测评场景下的得分 |
| `country` | string | 答题者所在国家 |
| `province` | string | 答题者所在省份 |
| `city` | string | 答题者所在城市 |
| `ua` | string | 答题者 User-Agent |
| `ip` | string | 答题者 IP 地址 |
| `answer` | array | 回答内容（按页面分组），见下方结构说明 |

### answer 嵌套结构

回答内容按 `页面 → 题目` 的层级组织：

```
answer[]                         ← 页面列表
├── id: "p1"                     ← 页面 ID
└── questions[]                  ← 该页面下的题目回答
    ├── id: "q1"                 ← 题目 ID
    ├── identity: "q-1-xxxx"     ← 题目唯一标识
    ├── title: "题目标题"         ← 题目标题
    ├── type: "radio"            ← 题型
    ├── text: "用户填写的内容"     ← 文本类题目的回答文本
    ├── options[]                ← 选择题的选项（含选中状态）
    ├── groups[]                 ← 矩阵题的分组回答
    └── blanks[]                 ← 填空题的填空内容
```

### Question（回答中的题目）字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 题目 ID |
| `identity` | string | 题目唯一标识（如 `q-1-xxxx`） |
| `title` | string | 题目标题 |
| `description` | string | 题目描述 |
| `type` | string | 题型（`radio`/`checkbox`/`text`/`textarea`/`star` 等） |
| `sub_type` | number | 题目子类型 |
| `text` | any | 回答内容（文本题为填写的文字，选择题为选中选项的文本） |
| `options` | array | 选择题的选项列表（含选中状态） |
| `groups` | array | 矩阵题的分组回答 |
| `blanks` | array | 填空题的各填空内容 |
| `id_list` | array | 选中选项的 ID 列表 |
| `text_list` | array | 选中选项的文本列表 |
| `signature_id` | string | 手写签名 ID |
| `files` | array | 附件题的文件列表 |

## 分页机制（重要）

`list_answers` 使用**游标分页**（Cursor-based Pagination），不是传统的 offset 分页。

### 工作原理

- 每次请求返回一页数据和 `last_answer_id`
- 将上一次返回的 `last_answer_id` 作为下一次请求的参数，获取下一页
- 数据按 answer_id **升序**排列

### 翻页流程

```
# 第一页：不传 last_answer_id（默认从头开始）
list_answers(survey_id=292192)
→ { total: 150, list: [...20条...], last_answer_id: 20 }

# 第二页：传入上一页返回的 last_answer_id
list_answers(survey_id=292192, last_answer_id=20)
→ { total: 150, list: [...20条...], last_answer_id: 45 }

# 第三页
list_answers(survey_id=292192, last_answer_id=45)
→ { total: 150, list: [...20条...], last_answer_id: 78 }

# ...继续翻页...

# 最后一页：返回不足 per_page 条，翻页结束
list_answers(survey_id=292192, last_answer_id=135)
→ { total: 150, list: [...10条...], last_answer_id: 150 }
```

### 判断是否还有下一页

| 条件 | 含义 |
|------|------|
| `list.length == per_page` | 可能还有下一页，继续用 `last_answer_id` 翻页 |
| `list.length < per_page` | **已到最后一页**，无需继续翻页 |
| `list` 为空数组 | **没有更多数据** |

> ⚠️ **注意**：`total` 是问卷回答总数，不是当前页的条数。判断翻页是否结束，应该看 `list.length` 是否小于 `per_page`，而不是看 `total`。

### 获取全部回答的伪代码

```python
all_answers = []
last_id = 0

while True:
    result = list_answers(survey_id=292192, per_page=100, last_answer_id=last_id)
    all_answers.extend(result.list)
    
    if len(result.list) < 100:  # 不足一页，已到末尾
        break
    
    last_id = result.last_answer_id

print(f"共 {result.total} 条回答，已全部获取 {len(all_answers)} 条")
```

## 调用示例

### 获取首页回答

```
list_answers(survey_id=292192)
```

### 指定每页条数

```
list_answers(survey_id=292192, per_page=50)
```

### 翻页获取下一页

```
list_answers(survey_id=292192, per_page=50, last_answer_id=45)
```

### mcporter 调用

```bash
# 获取首页（默认 20 条）
mcporter call tencent-survey.list_answers --args '{"survey_id": 292192}'

# 每页 100 条
mcporter call tencent-survey.list_answers --args '{"survey_id": 292192, "per_page": 100}'

# 翻页
mcporter call tencent-survey.list_answers --args '{"survey_id": 292192, "per_page": 100, "last_answer_id": 45}'
```

## 错误码

| error.type | 错误描述 | 解决方案 |
|------------|---------|---------|
| `invalid_auth_status` | 权限类型错误 | 检查 Token 权限 |
| `claim_error` | 权限校验错误 | 问卷不属于当前 Token 绑定的团队 |
| `invalid_argument` | 参数校验不通过 | 检查 survey_id 是否正确 |
| `get_answers_error` | 获取回答列表错误 | 确认 survey_id 正确且问卷存在 |

## 注意事项

1. **游标分页**：使用 `last_answer_id` 翻页，不支持跳页；必须按顺序逐页获取
2. **需要循环翻页**：如果回答数量多于 `per_page`，必须循环调用直到获取完所有数据
3. **per_page 上限 1000**：即使传入大于 1000 的值，也会被截断为 1000
4. **total 是全量统计**：`total` 字段表示问卷回收的总回答数，不随分页变化
5. **answer 嵌套结构**：每条回答的 `answer` 字段按 `页面 → 题目` 嵌套，需要递归解析
6. **题目回答内容**：不同题型的回答内容存储方式不同（`text` / `options` / `blanks` / `groups`），需根据 `type` 字段判断

## Annotations（工具注解）

| 注解 | 值 | 说明 |
|------|---|------|
| `readOnlyHint` | true | 只读操作，不修改任何数据 |
| `destructiveHint` | false | 非破坏性操作 |
| `idempotentHint` | true | 幂等操作，多次调用结果一致 |
| `openWorldHint` | false | 内部调用 |
