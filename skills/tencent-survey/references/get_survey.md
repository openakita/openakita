# get_survey 工具参考

## 概述

获取指定问卷的详细信息，包括标题、设置、页面、题目和选项。同时返回纯文本格式的问卷内容（`text` 字段，DSL 格式）。

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `survey_id` | number | **是** | 问卷 ID |

## 返回值

```json
{
  "id": 292192,
  "hash": "abc123def",
  "scene": "1",
  "title": "员工满意度调查",
  "prefix": "欢迎参与本次调查",
  "suffix": "感谢您的参与",
  "state": 2,
  "page_count": 1,
  "topic_count": 3,
  "started_at": "2026-01-15 00:00:00",
  "end_at": "2026-07-01 00:00:00",
  "createTime": 1736899800,
  "updateTime": 1736899900,
  "creator_user_id": 60000000001,
  "project": {
    "id": 1234,
    "name": "2026年度调查"
  },
  "text": "员工满意度调查\n\n欢迎参与本次调查\n\n1. 您对工作环境是否满意？[单选题]\n非常满意\n满意\n一般\n不满意\n\n2. 请对整体满意度打分[量表题]\n1~5\n\n3. 请填写您的建议[多行文本题]",
  "pages": [
    {
      "id": "p1",
      "index": "0",
      "questions": [
        {
          "id": "q-1-xxxx",
          "index": 1,
          "type": "radio",
          "sub_type": 0,
          "title": "您对工作环境是否满意？",
          "description": "",
          "required": true,
          "options": [
            {"id": "o1", "text": "非常满意"},
            {"id": "o2", "text": "满意"},
            {"id": "o3", "text": "一般"},
            {"id": "o4", "text": "不满意"}
          ]
        },
        {
          "id": "q2",
          "index": 2,
          "type": "star",
          "title": "请对整体满意度打分",
          "required": true,
          "starBeginNum": 1,
          "starNum": 5
        },
        {
          "id": "q3",
          "index": 3,
          "type": "textarea",
          "title": "请填写您的建议",
          "required": false
        }
      ]
    }
  ]
}
```

### Survey 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | number | 问卷 ID |
| `hash` | string | 问卷 hash，用于拼接投放链接 `https://wj.qq.com/s2/{id}/{hash}` |
| `scene` | string | 问卷场景：`"1"`=调查, `"3"`=考试, `"6"`=测评, `"8"`=投票 |
| `title` | string | 问卷标题（**可能包含 HTML 标签**） |
| `prefix` | string | 欢迎语 |
| `suffix` | string | 结束语 |
| `state` | number | 状态：0=草稿, 2=回收中, 3=暂停回收 |
| `page_count` | number | 页数 |
| `topic_count` | number | 问题数 |
| `started_at` | string | 回收开始时间 |
| `end_at` | string | 回收结束时间 |
| `createTime` | number | 创建时间（时间戳） |
| `updateTime` | number | 更新时间（时间戳） |
| `creator_user_id` | number | 问卷创建者的用户 ID |
| `project` | object | 问卷所属项目 |
| `project.id` | number | 项目 ID |
| `project.name` | string | 项目名称 |
| `text` | string | 纯文本格式的问卷内容（DSL 格式），包含标题、引导语和所有题目。可直接用于 `update_question` 等工具的参考 |
| `pages` | array | 页面列表 |

### Page 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 单页标识 |
| `index` | string | 序号 |
| `questions` | array | 该页面下的题目列表 |

### Question 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 题目 ID |
| `index` | number | 题目编号 |
| `type` | string | 题目类型（见下方 type 枚举） |
| `sub_type` | number | 题目子类型（有 sub_type 时优先根据 sub_type 判断） |
| `title` | string | 题目标题 |
| `description` | string | 题目备注 |
| `required` | boolean | 是否必答 |
| `options` | array | 选项列表（仅选择题有） |
| `hidden` | boolean | 是否隐藏 |
| `random` | boolean | 选项是否随机 |
| `goto` | object | 答题后跳转题目 |
| `maxlength` | object | 多选题最多可选 / 文本题最大字数 |
| `starBeginNum` | number | 量表题起始值 |
| `starNum` | number | 量表范围（2~10） |
| `starShowCustomStart` | string | 量表题起始文案 |
| `starShowCustomEnd` | string | 量表题末尾文案 |
| `subTitles` | array | 矩阵题子问题列表 |
| `subTitles[].id` | string | 矩阵题子问题 ID |
| `subTitles[].text` | string | 矩阵题子问题文本 |
| `levels` | array | 联动题各级标题 |
| `groups` | array | 联动题选项列表（嵌套） |

### Option 对象

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 选项 ID |
| `text` | string | 选项文案 |
| `exclusive` | boolean | 多选题中是否为互斥选项 |
| `noRandom` | boolean | 选项随机时是否固定当前选项位置 |
| `goto` | object | 选择后跳转题目 |
| `display` | object | 选择后显示题目 |

### type 枚举

| type | 说明 |
|------|------|
| `radio` | 单选 |
| `checkbox` | 多选 |
| `select` | 下拉 |
| `text` | 单行文本 |
| `textarea` | 多行文本 |
| `blanks` | 填空 |
| `star` | 量表/NPS |
| `sort` | 排序 |
| `matrix_radio` | 矩阵单选 |
| `matrix_checkbox` | 矩阵多选 |
| `matrix_star` | 矩阵量表 |
| `matrix_blank` | 矩阵填空 |
| `chained_selects` | 联动 |
| `upload` | 图片/文件 |
| `description` | 文本描述 |
| `datetime` | 日期/时间 |
| `signature` | 手写签名 |
| `address` | 地理位置 |
| `phone` | 手机号 |
| `sheet` | 自增表格 |

### 基本设置字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `prev` | boolean | 是否允许回到上一页 |
| `titleIndex` | boolean | 是否显示题目序号 |
| `login_check` | boolean | 是否开启登录验证 |
| `answer_count` | number | 允许回答次数 |
| `whitelist_enable` | boolean | 是否开启白名单 |
| `redirect_url` | string | 答题后跳转链接 |
| `webhook_url` | string | 答题后推送数据的地址 |
| `is_allow_update_answer` | boolean | 是否允许修改答案 |
| `is_enabled_location` | boolean | 是否获取用户位置信息 |

## 调用示例

### 直接调用

```
get_survey(survey_id=292192)
```

### mcporter 调用

```bash
mcporter call tencent-survey.get_survey --args '{"survey_id": 292192}'
```

## 错误码

| error.type | 错误描述 | 解决方案 |
|------------|---------|---------|
| `invalid_auth_status` | 权限类型错误 | 检查 Token 权限 |
| `claim_error` | 权限校验错误 | 问卷不属于当前 Token 绑定的团队 |
| `get_survey_error` | 获取数据错误 | 确认 survey_id 正确且问卷存在 |

## 注意事项

1. **title 可能包含 HTML 标签**：如 `<p>标题</p>`、`<br>` 等，展示给用户前建议清理
2. **嵌套结构**：数据为 `pages[] → questions[] → options[]`，需要递归解析
3. **type 与 sub_type**：有 `sub_type` 时优先根据 `sub_type` 判断题目类型
4. **投放链接拼接**：`https://wj.qq.com/s2/{id}/{hash}`
5. **text 字段**：返回纯文本 DSL 格式的问卷内容，包含标题和所有题目。该字段可作为 `update_question` 工具的参考，了解当前问卷的文本结构

## Annotations（工具注解）

| 注解 | 值 | 说明 |
|------|---|------|
| `readOnlyHint` | true | 只读操作，不修改任何数据 |
| `destructiveHint` | false | 非破坏性操作 |
| `idempotentHint` | true | 幂等操作，多次调用结果一致 |
| `openWorldHint` | false | 内部调用 |
