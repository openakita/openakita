---
name: openakita/skills@knowledge-capture
description: Transform conversations and unstructured information into structured Notion documentation. Extract key insights, decisions, and action items. Create cross-linked knowledge bases with templates for meeting notes, how-to guides, decision records, and project documentation. Integrates with Notion API for seamless content creation.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
  upstream: tommy-ca/notion-skills/knowledge-capture
---

# Notion 知识捕获技能

将对话、会议、研究和非结构化信息转化为结构化的 Notion 文档体系，构建可检索、可链接、可追溯的团队知识库。

---

## Core Capabilities

1. **信息Extract** — 从对话和文本中识别关键洞察、决策和行动项
2. **结构化转换** — 将非结构化内容转为 Notion 页面，遵循模板规范
3. **交叉链接** — 建立页面之间的双向关联，形成知识网络
4. **元数据保留** — 记录参与者、日期、上下文等溯源信息
5. **Notion API 集成** — Via API 直接Create和Update Notion 内容

---

## 信息Extract框架

### 从对话中Extract的Core elements

处理任何对话或文本时，系统性地Extract以下要素：

| 要素 | Description | Example |
|------|------|------|
| **关键洞察** | 有价值的认知、发现或结论 | "用户更倾向异步沟通" |
| **决策** | 已确定的选择及其理由 | "选择 PostgreSQL 而非 MongoDB，因为需要事务Supports" |
| **行动项** | 需要后续跟进的任务 | "李四负责在周五前完成 API 文档" |
| **开放问题** | 尚未解决的疑问 | "YesNo需要Supports离线模式？" |
| **风险与依赖** | 潜在风险和外部依赖 | "依赖第三方支付 API 的稳定性" |
| **参考资料** | 提及的文档、链接、工具 | "参考 RFC 7231 HTTP 语义规范" |
| **上下文** | 讨论的背景和前提条件 | "Based on Q1 用户反馈数据" |

### Extract质量标准

- **Full性** — 不遗漏关键信息
- **准确性** — 忠实反映原始表达，不添加推断
- **可操作性** — 行动项Includes负责人、截止日期、验收标准
- **可追溯性** — 每条信息可追溯到来源

---

## Notion 文档模板

### 模板一：会议笔记（Meeting Notes）

```markdown
# 📋 [会议主题]

## 元数据
| 属性 | Value |
|------|-----|
| **日期** | YYYY-MM-DD |
| **时间** | HH:MM - HH:MM |
| **参与者** | @张三, @李四, @王五 |
| **会议类型** | 周会 / 评审 / 规划 / 头脑风暴 / 决策 |
| **状态** | 进行中 / 已完成 / 需跟进 |
| **关联项目** | [[项目名称]] |

## 背景与目标
> 简要说明本次会议的目的和期望产出。

## 议程
1. [ ] 议题一
2. [ ] 议题二
3. [ ] 议题三

## 讨论要点

### 议题一：[标题]
**讨论内容：**
- 要点 1
- 要点 2

**结论：**
> 最终达成的共识或决定。

### 议题二：[标题]
**讨论内容：**
- 要点 1

**结论：**
> 

## 决策记录
| # | 决策内容 | 理由 | 影响范围 |
|---|---------|------|---------|
| 1 |  |  |  |

## 行动项
| # | 任务 | 负责人 | 截止日期 | 状态 |
|---|------|--------|---------|------|
| 1 |  | @姓名 | YYYY-MM-DD | 🔴 未开始 |

## 开放问题
- [ ] 问题描述 → 负责人: @姓名

## 下次会议
- **日期**: 
- **议题预告**: 
```

### 模板二：操作指南（How-To Guide）

```markdown
# 🔧 如何 [完成某个任务]

## 元数据
| 属性 | Value |
|------|-----|
| **Create日期** | YYYY-MM-DD |
| **最后Update** | YYYY-MM-DD |
| **作者** | @姓名 |
| **难度** | 初级 / 中级 / 高级 |
| **预计时间** | X 分钟 |
| **适用版本** | v1.0+ |
| **标签** | #类别1, #类别2 |

## Overview
> 一句话说明这篇指南解决什么问题。

## Prerequisites
- [ ] 条件一：描述
- [ ] 条件二：描述
- [ ] 所需工具/权限：描述

## 步骤

### 第一步：[步骤标题]
**操作：**
详细描述具体操作。

**示例：**
```
代码或命令示例
```

**预期结果：**
> 完成后应该看到什么。

> ⚠️ **注意**: 常见陷阱或注意事项。

### 第二步：[步骤标题]
**操作：**
详细描述。

**示例：**
```
代码或命令示例
```

### 第三步：[步骤标题]
**操作：**
详细描述。

## 验证
如何确认操作成功：
1. 检查项一
2. 检查项二

## 故障排查
| 问题 | 可能原因 | 解决方法 |
|------|---------|---------|
| 问题描述 | 原因Analyze | 具体步骤 |

## 相关文档
- [[相关指南1]]
- [[相关指南2]]
- [外部链接](https://...)
```

### 模板三：决策记录（Decision Record）

```markdown
# 🔖 DR-[编号]: [决策标题]

## 元数据
| 属性 | Value |
|------|-----|
| **日期** | YYYY-MM-DD |
| **状态** | 提议 / 已批准 / 已废弃 / 已取代 |
| **决策者** | @姓名1, @姓名2 |
| **关联项目** | [[项目名称]] |
| **取代** | [[DR-XXX]] (如适用) |
| **被取代** | [[DR-YYY]] (如适用) |

## 背景
> 什么情况促使我们需要做这个决策？包括技术背景、业务需求、约束条件。

## 问题陈述
> 用一句话清楚描述需要解决的问题。

## 选项Analyze

### 选项 A：[方案名称]
**描述：** 简要说明方案内容。

| 维度 | 评估 |
|------|------|
| **优点** | 列举优势 |
| **缺点** | 列举劣势 |
| **成本** | 时间/资源估算 |
| **风险** | 潜在风险 |
| **可逆性** | 高 / 中 / 低 |

### 选项 B：[方案名称]
**描述：** 简要说明方案内容。

| 维度 | 评估 |
|------|------|
| **优点** |  |
| **缺点** |  |
| **成本** |  |
| **风险** |  |
| **可逆性** |  |

### 选项 C：[方案名称] （如有更多选项）

## 决策
> **我们选择了选项 [X]。**
>
> 核心理由：...

## 影响
- **短期影响**: 
- **长期影响**: 
- **需要变更的组件**: 
- **迁移计划**: (如适用)

## 后续行动
| # | 行动 | 负责人 | 截止日期 |
|---|------|--------|---------|
| 1 |  | @姓名 |  |
```

### 模板四：知识条目（Knowledge Entry）

```markdown
# 💡 [知识主题]

## 元数据
| 属性 | Value |
|------|-----|
| **来源** | 对话 / 文档 / 研究 / 实践 |
| **捕获日期** | YYYY-MM-DD |
| **捕获者** | @姓名 |
| **置信度** | 高 / 中 / 低 |
| **标签** | #领域, #主题 |
| **关联** | [[相关页面]] |

## 核心内容
> 简明扼要地表述这条知识。

## 详细说明
展开解释，包括背景、原理、适用条件。

## 证据与来源
- 来源 1: 描述
- 来源 2: 描述

## 应用场景
- 场景一: 如何Use这条知识
- 场景二: 

## 局限性
- 不适Used for...
- 需要注意...

## 相关知识
- [[关联概念1]]
- [[关联概念2]]
```

---

## Notion API 集成

### 认证与配置

Via Notion Integration Get API Token，所有 API 操作需要以下前提：

1. 在 [Notion Developers](https://developers.notion.com/) Create Integration
2. Get Internal Integration Token
3. 在目标页面/数据库中授予 Integration 访问权限（Share → Invite）

### 核心 API 操作

#### Create页面

```json
{
  "parent": {
    "type": "database_id",
    "database_id": "DATABASE_ID"
  },
  "properties": {
    "Name": {
      "title": [
        {
          "text": { "content": "页面标题" }
        }
      ]
    },
    "Status": {
      "select": { "name": "进行中" }
    },
    "Date": {
      "date": { "start": "2026-03-01" }
    },
    "Tags": {
      "multi_select": [
        { "name": "会议" },
        { "name": "产品" }
      ]
    }
  },
  "children": [
    {
      "object": "block",
      "type": "heading_2",
      "heading_2": {
        "rich_text": [{ "text": { "content": "背景" } }]
      }
    },
    {
      "object": "block",
      "type": "paragraph",
      "paragraph": {
        "rich_text": [{ "text": { "content": "页面正文内容..." } }]
      }
    }
  ]
}
```

#### 追加内容到现有页面

```json
{
  "block_id": "PAGE_OR_BLOCK_ID",
  "children": [
    {
      "object": "block",
      "type": "to_do",
      "to_do": {
        "rich_text": [{ "text": { "content": "新的行动项" } }],
        "checked": false
      }
    }
  ]
}
```

#### 查询数据库

```json
{
  "database_id": "DATABASE_ID",
  "filter": {
    "and": [
      {
        "property": "Status",
        "select": { "equals": "进行中" }
      },
      {
        "property": "Date",
        "date": { "on_or_after": "2026-01-01" }
      }
    ]
  },
  "sorts": [
    {
      "property": "Date",
      "direction": "descending"
    }
  ]
}
```

### Notion Block 类型速查

| Block 类型 | 用途 | API type 值 |
|-----------|------|------------|
| 段落 | 正文 | `paragraph` |
| 标题 1-3 | 层级标题 | `heading_1` / `heading_2` / `heading_3` |
| 待办事项 | 可勾选任务 | `to_do` |
| 项目列表 | 无序列表 | `bulleted_list_item` |
| 编号列表 | 有序列表 | `numbered_list_item` |
| 引用 | 引用块 | `quote` |
| 标注 | 高亮提示框 | `callout` |
| 代码 | 代码块 | `code` |
| 分割线 | 水平分隔线 | `divider` |
| 表格 | 数据表格 | `table` |
| Switch列表 | 折叠内容 | `toggle` |

---

## 交叉链接策略

### 链接类型

| 链接方式 | Notion 实现 | 适用场景 |
|---------|------------|---------|
| **页面提及** | `@Page Name` | 行内引用相关页面 |
| **关系属性** | Database Relation | 数据库之间的结构化关联 |
| **反向链接** | Backlinks（AutomaticGeneration） | View哪些页面引用了当前页面 |
| **同步块** | Synced Block | 多页面共享同一内容 |

### 链接最佳实践

1. **每条知识至少链接 2 个相关页面** — 避免孤立知识
2. **Use Relation 属性而非行内链接** — 数据库间的关联用 Relation 更结构化
3. **在"相关文档"章节集中List链接** — 方便读者发现关联内容
4. **定期审查孤立页面** — 识别没有入链的页面并补充关联

---

## Workflow

### 会议知识捕获流程

```
会议 → 实时记录要点 → Extract决策和行动项 → Create会议笔记页面
                                          ↓
                                  Update项目页面 ← 链接行动项
                                          ↓
                                  Create/Update决策记录 ← 链接决策
```

1. **会议期间**: 记录讨论要点（可以Yes粗糙的笔记）
2. **会议后 30 分钟内**: 整理为结构化会议笔记
3. **Extract行动项**: Create为 To-Do 并关联到项目
4. **Extract决策**: 如有重要决策，Create独立的决策记录
5. **建立链接**: 将会议笔记链接到项目、参与者主页等

### 对话知识捕获流程

1. **识别有价值的信息** — 不Yes所有对话都需要记录
2. **确定文档类型** — 选择最合适的模板
3. **ExtractCore elements** — Use信息Extract框架
4. **Create Notion 页面** — 按模板结构化内容
5. **建立链接** — 关联到相关知识和项目
6. **Mark来源** — 注明信息来源和捕获日期

### 知识库维护流程

- **每周**: 审查新增页面的链接Full性
- **每月**: 识别和处理孤立页面
- **每季度**: 评审知识条目的准确性和时效性，归档过期内容

---

## 元数据规范

### 必需元数据

所有 Notion 页面必须Includes以下元数据：

| 属性 | Type | Description |
|------|------|------|
| **Title** | Title | 页面标题 |
| **Created** | Date | Create日期 |
| **Type** | Select | 文档类型（会议笔记/指南/决策记录/知识条目） |
| **Status** | Select | 状态（草稿/已审核/已发布/已归档） |
| **Tags** | Multi-select | 分类标签 |

### 可选元数据

| 属性 | Type | Description |
|------|------|------|
| **Author** | Person | Create者 |
| **Participants** | Person | 参与者 |
| **Project** | Relation | 关联项目 |
| **Source** | URL/Text | 信息来源 |
| **Confidence** | Select | 信息置信度 |
| **Review Date** | Date | 下次审核日期 |

---

## Notes

- **及时捕获** — 信息的价值随时间递减，尽早记录
- **保持结构化** — 始终Use模板，避免自由格式堆砌
- **标注来源** — 每条知识标明出处，保持可追溯性
- **避免重复** — Create前先SearchYesNo已有相关页面
- **权限Manage** — 注意 Notion Integration 的权限范围
- **API 限速** — Notion API 有请求频率限制（约 3 req/s），批量操作注意节流
- **内容长度** — 单个 API 请求的 children 数组上限为 100 个 block
- **定期备份** — Notion 数据建议定期导出备份
