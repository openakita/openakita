---
name: openakita/skills@obsidian-skills
description: Manage Obsidian vaults with full support for Obsidian Flavored Markdown — wikilinks, embeds, callouts, YAML properties, Dataview queries, Canvas, and Bases. Organize notes using MOCs (Maps of Content), atomic note principles, and consistent folder/tag taxonomies. Always ask the user where to save before creating notes.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
  upstream: kepano/obsidian-skills
---

# Obsidian 笔记管理技能

帮助用户在 Obsidian 知识库中创建、组织和管理笔记，遵循 Obsidian Flavored Markdown（OFM）规范和知识管理最佳实践。

---

## 核心原则

1. **创建笔记前必须询问保存位置** — 永远不要自行假设路径，先确认用户的 Vault 结构和目标文件夹。
2. **遵循 Obsidian Flavored Markdown** — 使用 wikilinks、嵌入、callouts 等 OFM 专有语法。
3. **原子笔记** — 每条笔记聚焦一个概念，通过链接建立知识网络。
4. **元数据驱动** — 使用 YAML frontmatter properties 保证笔记可被检索和查询。

---

## Obsidian Flavored Markdown 语法参考

### Wikilinks（内部链接）

Obsidian 使用 `[[]]` 语法进行内部链接，这是与标准 Markdown 最重要的区别。

```markdown
[[笔记名称]]                    # 链接到同名笔记
[[笔记名称|显示文本]]            # 自定义显示文本
[[笔记名称#标题]]               # 链接到特定标题
[[笔记名称#^block-id]]          # 链接到特定块
[[笔记名称#标题|显示文本]]       # 标题链接 + 自定义文本
```

**最佳实践：**
- 笔记名称应具有描述性且唯一，避免使用特殊字符 `[ ] # ^ | \`
- 优先使用笔记全名链接而非路径链接，Obsidian 会自动解析
- 使用别名（alias）而不是 `|显示文本` 来处理频繁使用的缩写

### 嵌入（Embeds）

使用 `![[]]` 语法将其他笔记或资源直接嵌入当前笔记。

```markdown
![[笔记名称]]                   # 嵌入整篇笔记
![[笔记名称#标题]]              # 嵌入特定章节
![[笔记名称#^block-id]]         # 嵌入特定块
![[图片.png]]                   # 嵌入图片
![[图片.png|300]]               # 指定宽度
![[音频.mp3]]                   # 嵌入音频
![[视频.mp4]]                   # 嵌入视频
![[文档.pdf]]                   # 嵌入 PDF
![[文档.pdf#page=3]]            # 嵌入 PDF 特定页
```

### Callouts（提示框）

基于 Markdown blockquote 语法扩展，用于高亮重要信息。

```markdown
> [!note] 标题
> 笔记内容

> [!tip] 提示
> 有用的建议

> [!warning] 警告
> 需要注意的事项

> [!important] 重要
> 关键信息

> [!info] 信息
> 补充说明

> [!question] 问题
> 需要进一步探讨的问题

> [!example] 示例
> 具体的例子

> [!abstract] 摘要
> 概述或总结

> [!todo] 待办
> 需要完成的任务

> [!quote] 引用
> 引用来源文字
```

**折叠 Callout：**
```markdown
> [!tip]- 点击展开       # 默认折叠
> 隐藏内容

> [!tip]+ 点击折叠       # 默认展开
> 可折叠内容
```

**嵌套 Callout：**
```markdown
> [!note] 外层
> 外层内容
>> [!tip] 内层
>> 内层内容
```

### 标签（Tags）

```markdown
#标签名                         # 行内标签
#父标签/子标签                   # 嵌套标签
```

在 frontmatter 中声明标签：
```yaml
tags:
  - 项目管理
  - 知识管理/PKM
```

### 注释

```markdown
%%这是 Obsidian 注释，不会在预览模式中显示%%

%%
多行注释
也是可以的
%%
```

### 数学公式

```markdown
行内公式：$E = mc^2$

块级公式：
$$
\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}
$$
```

### Mermaid 图表

````markdown
```mermaid
graph TD
    A[开始] --> B{判断}
    B -->|是| C[执行]
    B -->|否| D[结束]
```
````

---

## YAML Frontmatter Properties

每条笔记应以 YAML frontmatter 开头，定义结构化元数据。

### 标准属性

```yaml
---
title: 笔记标题
aliases:
  - 别名1
  - 别名2
tags:
  - 标签1
  - 父标签/子标签
date: 2026-03-01
created: 2026-03-01T10:30:00
modified: 2026-03-01T14:20:00
author: 作者名
status: draft | in-progress | reviewed | published
type: note | moc | reference | project | daily
cssclasses:
  - custom-class
publish: true
---
```

### 自定义属性（面向 Dataview）

根据笔记类型添加自定义字段，供 Dataview 查询使用：

```yaml
---
# 读书笔记
book_title: "深度工作"
book_author: "Cal Newport"
rating: 4
started: 2026-01-15
finished: 2026-02-20
category: 生产力
---
```

```yaml
---
# 项目笔记
project: OpenAkita
priority: high
deadline: 2026-06-30
stakeholders:
  - Alice
  - Bob
---
```

```yaml
---
# 会议笔记
meeting_type: standup | review | planning
participants:
  - 张三
  - 李四
decisions:
  - 决策内容
action_items:
  - "[ ] 待办事项"
---
```

### 属性类型

Obsidian 支持以下 property 类型：
- **Text**: 普通文本
- **List**: 数组值
- **Number**: 数值
- **Checkbox**: `true`/`false`
- **Date**: `YYYY-MM-DD`
- **Date & time**: `YYYY-MM-DDTHH:mm:ss`
- **Aliases**: 别名列表（内置）
- **Tags**: 标签列表（内置）

---

## 文件夹结构与命名规范

### 推荐的 Vault 结构

```
MyVault/
├── 00 - Inbox/              # 快速捕获，待整理
├── 01 - Projects/           # 活跃项目
│   ├── ProjectA/
│   └── ProjectB/
├── 02 - Areas/              # 持续关注的领域
│   ├── 健康/
│   ├── 财务/
│   └── 职业发展/
├── 03 - Resources/          # 参考资料
│   ├── 读书笔记/
│   ├── 文章剪藏/
│   └── 课程笔记/
├── 04 - Archive/            # 已完成/不活跃
├── 05 - Templates/          # 模板
├── 06 - Daily Notes/        # 日记
├── 07 - MOCs/               # Maps of Content
├── Attachments/             # 附件（图片、PDF 等）
└── Canvas/                  # Canvas 文件
```

### 文件命名规范

| 类型 | 命名格式 | 示例 |
|------|---------|------|
| 普通笔记 | 描述性名称 | `原子笔记方法论.md` |
| 日记 | `YYYY-MM-DD` | `2026-03-01.md` |
| 会议笔记 | `YYYY-MM-DD 会议主题` | `2026-03-01 产品评审会.md` |
| MOC | `MOC - 主题` | `MOC - 知识管理.md` |
| 模板 | `Template - 类型` | `Template - 读书笔记.md` |
| 项目主页 | `项目名 - Home` | `OpenAkita - Home.md` |

**命名原则：**
- 避免特殊字符：`/ \ : * ? " < > |`
- 使用空格而非下划线（Obsidian 对空格友好）
- 名称应具有自描述性，无需借助文件夹路径理解含义

---

## MOC（Maps of Content）

MOC 是连接相关笔记的导航枢纽，起到"目录"和"思维地图"的作用。

### MOC 模板

```markdown
---
title: MOC - 知识管理
type: moc
tags:
  - MOC
  - 知识管理
date: 2026-03-01
---

# 知识管理

> [!abstract] 概述
> 关于个人知识管理（PKM）的核心概念、方法论和工具。

## 核心概念

- [[卡片盒笔记法]]
- [[原子笔记]]
- [[渐进式总结]]
- [[常青笔记]]

## 方法论

- [[PARA 方法]]
- [[Zettelkasten 方法]]
- [[Building a Second Brain]]
- [[CODE 方法]]

## 工具与实践

- [[Obsidian 使用技巧]]
- [[Dataview 查询食谱]]
- [[模板系统设计]]

## 相关 MOC

- [[MOC - 生产力]]
- [[MOC - 学习方法]]
- [[MOC - 写作]]
```

### MOC 最佳实践

1. **层级结构** — MOC 可以嵌套，顶层 MOC 链接子主题 MOC
2. **动态更新** — 新建笔记后检查是否需要加入相关 MOC
3. **分类灵活** — 同一笔记可出现在多个 MOC 中
4. **简要注释** — 每个链接可附加一行说明，解释与当前主题的关系

---

## Dataview 兼容性

确保笔记的 frontmatter 属性与 Dataview 查询兼容。

### 常用 Dataview 查询模式

**表格查询：**
````markdown
```dataview
TABLE rating, book_author, finished
FROM "03 - Resources/读书笔记"
WHERE rating >= 4
SORT finished DESC
```
````

**列表查询：**
````markdown
```dataview
LIST
FROM #项目管理 AND -"04 - Archive"
WHERE status = "in-progress"
SORT priority ASC
```
````

**任务查询：**
````markdown
```dataview
TASK
FROM "01 - Projects"
WHERE !completed
GROUP BY file.link
```
````

**行内查询：**
```markdown
今天是 `= date(today)`，本库共有 `= length(file.lists)` 条列表项。
```

### Dataview 友好的属性设计

- 使用英文属性名（中文属性名在某些查询中可能出问题）
- 日期使用 `YYYY-MM-DD` 格式
- 布尔值使用 `true`/`false`
- 列表属性使用 YAML 数组语法

---

## Canvas 支持

Obsidian Canvas 是可视化组织笔记关系的画布工具。

### Canvas 文件格式

Canvas 文件为 `.canvas` 后缀的 JSON 文件，包含节点（nodes）和边（edges）。

```json
{
  "nodes": [
    {
      "id": "node1",
      "type": "text",
      "text": "核心概念",
      "x": 0, "y": 0,
      "width": 250, "height": 60
    },
    {
      "id": "node2",
      "type": "file",
      "file": "03 - Resources/原子笔记.md",
      "x": 300, "y": 0,
      "width": 250, "height": 60
    }
  ],
  "edges": [
    {
      "id": "edge1",
      "fromNode": "node1",
      "toNode": "node2",
      "label": "包含"
    }
  ]
}
```

### Canvas 节点类型

| 类型 | 说明 | 关键属性 |
|------|------|---------|
| `text` | 纯文本卡片 | `text` |
| `file` | 链接到 Vault 中的文件 | `file` |
| `link` | 嵌入网页 | `url` |
| `group` | 分组容器 | `label` |

### 创建 Canvas 时的注意事项

- 节点坐标以画布中心为原点
- 建议节点宽度 200–400px
- 使用 `group` 节点对相关卡片分组
- 边可以包含 `label` 描述关系

---

## Bases 支持

Obsidian Bases 是 Obsidian 1.8+ 引入的内置数据库视图功能。

### Bases 概述

- Bases 文件以 `.base` 为后缀，存储在 Vault 中
- 自动从笔记的 frontmatter properties 提取结构化数据
- 支持表格视图、筛选、排序、分组
- 无需 Dataview 插件即可实现结构化查询

### 创建 Bases 时的建议

1. **统一属性** — 同一类型的笔记使用相同的 frontmatter 属性集
2. **属性类型** — 在 Obsidian 设置中预定义属性类型，确保数据一致性
3. **筛选条件** — 使用文件夹或标签限定数据来源
4. **视图配置** — 选择合适的列、排序和分组方式

---

## 笔记模板

### 日记模板

```markdown
---
title: "{{date:YYYY-MM-DD}}"
type: daily
date: {{date:YYYY-MM-DD}}
tags:
  - daily
---

# {{date:YYYY-MM-DD dddd}}

## 今日计划
- [ ] 

## 笔记与想法


## 今日回顾

### 完成了什么


### 明天的重点

```

### 读书笔记模板

```markdown
---
title: "{{title}}"
type: reference
book_title: ""
book_author: ""
category: ""
rating: 
started: {{date:YYYY-MM-DD}}
finished: 
status: in-progress
tags:
  - 读书笔记
---

# {{title}}

> [!info] 书籍信息
> - **作者**: 
> - **出版年份**: 
> - **ISBN**: 

## 核心观点


## 章节笔记

### 第一章


## 关键引用

> 

## 我的思考


## 行动项
- [ ] 

## 相关笔记
- [[]]
```

### 项目笔记模板

```markdown
---
title: "{{title}}"
type: project
project: ""
status: active
priority: medium
deadline: 
stakeholders: []
tags:
  - 项目
---

# {{title}}

> [!abstract] 项目概述
> 

## 目标


## 里程碑

| 里程碑 | 截止日期 | 状态 |
|--------|---------|------|
|  |  | 🔴 |

## 任务
- [ ] 

## 会议记录
- [[]]

## 参考资料
- [[]]

## 复盘笔记

```

---

## 工作流程

### 创建笔记的标准流程

1. **询问保存位置** — 始终先确认用户期望的文件夹路径
2. **选择或创建模板** — 根据笔记类型使用对应模板
3. **填写 frontmatter** — 确保所有必需属性完整
4. **编写内容** — 使用 OFM 语法
5. **建立链接** — 添加 wikilinks 连接相关笔记
6. **更新 MOC** — 如有对应 MOC，将新笔记加入其中

### 整理 Inbox 的流程

1. 浏览 `00 - Inbox/` 中的笔记
2. 为每条笔记添加或完善 frontmatter
3. 移动到合适的文件夹
4. 建立与现有笔记的链接
5. 更新相关 MOC

### 重构知识库

1. 识别孤立笔记（无入链和出链）
2. 合并重复概念
3. 拆分过长的笔记为原子笔记
4. 更新失效链接
5. 归档不活跃内容到 `04 - Archive/`

---

## 注意事项

- **不要自行决定保存路径** — 永远先问用户
- **保持笔记原子性** — 一条笔记一个核心概念
- **优先使用 wikilinks** — 而非标准 Markdown 链接
- **YAML frontmatter 必须是文件第一行** — 前面不能有空行
- **属性名使用英文** — 确保 Dataview 和 Bases 兼容
- **图片等附件放在专用文件夹** — 如 `Attachments/`
- **定期维护链接** — 重命名笔记后检查链接是否自动更新
- **备份 Vault** — 建议使用 Git 或 Obsidian Sync

