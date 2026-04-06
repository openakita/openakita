# update_question 工具参考

## 概述

更新问卷中的某一道题目。需要先用 `get_survey` 获取问卷详情以确认 `question_id`，再调用此接口更新指定题目的内容。只传入目标题目的纯文本（DSL 格式），系统会自动解析并覆盖该题。

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `survey_id` | number | **是** | 问卷 ID |
| `question_id` | string | **是** | 要更新的题目 ID，格式形如 `q-1-xxxx`（从 `get_survey` 返回的题目列表中获取） |
| `text` | string | **是** | 该题目的新文本内容（DSL 格式），只需包含这一道题 |

> **重要**：`question_id` 必须从 `get_survey` 返回的题目列表中获取，不能自行构造。

## 返回值

### 成功响应

```json
{
  "survey_id": 716128,
  "question_id": "q-1-abcd1234",
  "result": "success"
}
```

### 失败响应

```json
{
  "survey_id": 716128,
  "question_id": "q-1-abcd1234",
  "result": "failed",
  "error": "invalid_text_format: ..."
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `survey_id` | number | 问卷 ID |
| `question_id` | string | 更新的题目 ID |
| `result` | string | `"success"` 表示成功，`"failed"` 表示失败 |
| `error` | string | 仅失败时返回，错误描述信息 |

## text 语法说明

`text` 参数使用与 `create_survey` 相同的 DSL 语法，但**只需写一道题**（不需要写问卷标题）。

### 语法格式

```
题目标题[题型](描述)
选项A
选项B
```

> **关键规则**：`[题型]` 标签**必须紧跟在标题末尾**，后面不能再有文字。`(描述)` 部分为可选。

### 各题型示例

| 题型 | text 示例 |
|------|----------|
| 单选题 | `"您的性别[单选题]\n男\n女"` |
| 多选题 | `"您感兴趣的领域[多选题]\n技术\n设计\n产品\n运营"` |
| 下拉题 | `"请选择部门[下拉题]\n研发部\n市场部\n财务部"` |
| 排序题 | `"请排列优先级[排序题]\n功能\n性能\n安全\n体验"` |
| 单行文本题 | `"您的姓名[单行文本题]"` |
| 多行文本题 | `"您的建议[多行文本题]"` |
| 量表题 | `"请打分[量表题](5分非常满意)\n1~5"` |
| 多项填空题 | `"联系方式[多项填空题]\n手机号：____\n邮箱：____"` |
| 矩阵单选题 | `"满意度评价[矩阵单选题]\n非常满意 满意 一般 不满意\n服务态度\n响应速度"` |
| 段落说明 | `"以下为附加问题[段落说明]"` |

> 完整语法参考：`references/create_survey.md` 中的「text 文本语法详解」章节。

## 调用示例

### 典型工作流

```
# 1. 先获取问卷详情，确认题目 ID
get_survey(survey_id=716128)
# 返回中找到目标题目，例如 id="q-1-abcd1234"，类型为单选题

# 2. 更新该题目
update_question(survey_id=716128, question_id="q-1-abcd1234", text="您的性别[单选题]\n男\n女\n其他")
```

### 更新量表题

```
update_question(survey_id=716128, question_id="q-1-efgh5678", text="请对服务打分[量表题](1分最低，10分最高)\n1~10")
```

### 更新多选题

```
update_question(survey_id=716128, question_id="q-1-ijkl9012", text="您常用的编程语言[多选题]\nGo\nPython\nJava\nTypeScript\nRust")
```

### mcporter 调用

```bash
# 更新单选题
mcporter call tencent-survey.update_question --args '{"survey_id": 716128, "question_id": "q-1-abcd1234", "text": "您的性别[单选题]\n男\n女\n其他"}'

# 更新多行文本题
mcporter call tencent-survey.update_question --args '{"survey_id": 716128, "question_id": "q-1-efgh5678", "text": "请填写您的建议[多行文本题]"}'
```

## 权限要求

调用此接口需要满足以下权限条件（逐级校验）：

| 校验层 | 说明 |
|--------|------|
| `WithSurveyClaims` | 问卷归属校验：问卷必须属于当前 Token 绑定的团队 |
| `WithSurveyEditorClaims` | 编辑权限校验：当前用户需具有该问卷的编辑权限 |
| `WithSurveyEditableClaims` | 可编辑状态校验：问卷必须处于可编辑状态（如草稿状态） |

## 错误码

| error.type | 错误描述 | 解决方案 |
|------------|---------|---------|
| `invalid_text_format` | 文本内容格式错误 | 检查 text 语法是否正确，`[题型]` 需紧跟标题末尾 |
| `no_question_parsed` | 未能解析出任何题目 | 确认 text 包含了完整的题目内容 |
| `unknown_question_type` | 无法识别的题型 | 检查 `[题型]` 标签是否使用了支持的题型名称 |
| `save_question_failed` | 保存题目失败 | 服务端异常，请稍后重试 |
| `claim_error` | 权限校验错误 | 问卷不属于当前 Token 绑定的团队，或无编辑权限 |
| `survey_not_editable` | 问卷不可编辑 | 问卷可能正在回收中，需先暂停回收 |
| `invalid_argument` | 参数校验不通过 | 检查 survey_id、question_id、text 是否正确 |

## 注意事项

1. **先获取再更新**：必须先调用 `get_survey` 获取问卷详情，从返回的题目列表中获取正确的 `question_id`
2. **单题更新**：`text` 只需包含一道题目的内容，不要写问卷标题或多道题
3. **非幂等操作**：每次调用都会覆盖原题目内容
4. **题型标签位置**：`[题型]` 必须紧跟标题末尾，不能放在其他位置
5. **问卷状态要求**：问卷必须处于可编辑状态，正在回收中的问卷需先暂停才能编辑
6. **换行用 `\n`**：在 JSON 参数中，所有换行必须使用 `\n` 代替
7. **选项无需字母前缀**：选项直接写内容即可（如 `满意`），不需要写 `A. 满意`

## Annotations（工具注解）

| 注解 | 值 | 说明 |
|------|---|------|
| `readOnlyHint` | false | 非只读操作，会修改问卷内容 |
| `destructiveHint` | false | 非破坏性操作（更新而非删除） |
| `idempotentHint` | false | **非幂等**，每次调用都覆盖题目内容 |
| `openWorldHint` | false | 内部调用 |
