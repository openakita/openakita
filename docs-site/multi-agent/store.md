# Agent Store / Skill Store

## 什么是 Store

Store 是 OpenAkita 的**社区市场**——你可以在这里发现、安装和分享 Agent 与技能。

- **Agent Store** — 预配置好的 Agent 角色，安装即用
- **Skill Store** — 独立的技能包，为 Agent 增加新能力

类似手机上的 App Store：Agent Store 里是"完整的应用"，Skill Store 里是"功能插件"。

## Agent Store

[打开 Agent Store](/web/#/agent-store)

### 浏览与搜索

- **分类筛选** — 按功能类别浏览：办公效率、编程开发、数据分析、创意写作……
- **关键词搜索** — 输入关键词快速查找
- **排序** — 按热度、评分、最新排序

### 安装 Agent

1. 找到目标 Agent，点击 **「安装」**
2. 预览 Agent 配置（名称、描述、技能需求）
3. 确认安装 → Agent 出现在 [Agent 管理](/web/#/agent-manager) 列表中
4. 如需微调，可进入管理页面修改提示词或技能范围

::: tip 提示
安装后的 Agent 是本地副本，后续修改不会影响 Store 中的原版，Store 更新也不会覆盖你的自定义修改。
:::

### 评分与反馈

- 安装并使用后，可回到 Store 页面为 Agent 打分（1-5 星）
- 评分帮助社区发现优质 Agent

## Skill Store

[打开 Skill Store](/web/#/skill-store)

### 浏览与搜索

与 Agent Store 类似，支持分类筛选和关键词搜索。技能按能力类型分类：

| 类别 | 示例技能 |
|------|---------|
| **文件处理** | PDF 解析、Excel 处理、图片编辑 |
| **网络请求** | HTTP 客户端、API 调用、爬虫 |
| **数据处理** | 数据清洗、统计分析、可视化 |
| **系统操作** | Shell 命令、文件管理、进程控制 |
| **外部服务** | 邮件发送、日历管理、消息推送 |

### 安装技能

1. 点击 **「安装」** → 技能自动下载到本地
2. 在 [工具与技能配置](/web/#/config/tools) 中确认已启用
3. 所有 Agent（或指定 Agent）即可使用该技能

### 技能依赖

部分技能依赖外部服务或 Python 包：

- Store 页面会标注依赖信息
- 安装时自动检查依赖并提示缺失项
- 使用 `pip install` 或配置 API Key 后重试

## 发布你的 Agent / 技能

### 发布 Agent

1. 在 [Agent 管理](/web/#/agent-manager) 中选择要发布的 Agent
2. 点击菜单 → **「发布到 Store」**
3. 填写描述、截图、分类等元信息
4. 提交审核 → 审核通过后上架

### 发布技能

1. 编写符合规范的 `SKILL.md` 技能文件
2. 在 [Skill Store](/web/#/skill-store) 点击 **「提交技能」**
3. 上传技能文件并填写元信息
4. 提交审核

::: warning 注意
发布的内容需遵循社区准则，不得包含恶意代码或不当内容。
:::

## 相关页面

- [Agent 管理](/multi-agent/agent-manager) — 管理已安装的 Agent
- [多 Agent 入门](/multi-agent/overview) — 理解多 Agent 协作
- [技能管理](/features/skills) — 管理本地技能列表
- [配置向导 · 工具与技能](/web/#/config/tools) — 技能的全局开关

