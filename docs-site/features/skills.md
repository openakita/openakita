# 技能管理

## 什么是技能

技能（Skill）是 Agent 能力的**声明式扩展**——通过一个 `SKILL.md` 文件描述"这个技能做什么、需要什么、怎么用"，Agent 就能自动学会新本领。

你可以把技能理解为 Agent 的"插件系统"：不改代码，只写一份说明书，Agent 就能解锁新能力。

## 内置技能

OpenAkita 预装了一系列核心技能，开箱即用：

| 技能 | 说明 |
|------|------|
| **filesystem** | 文件读写、目录操作、搜索替换 |
| **web_search** | 互联网搜索（Google / Bing / DuckDuckGo） |
| **browser** | 网页浏览、截图、表单操作 |
| **desktop** | 桌面自动化（鼠标、键盘、截屏） |
| **memory** | 记忆存取——Agent 的长期记忆 |
| **scheduler** | 定时任务与计划执行 |
| **im_channel** | 通过 IM 通道发送消息 |
| **persona** | 切换 Agent 人格 / 身份 |
| **sticker** | 生成表情包和图片 |
| **mcp** | 连接 MCP 服务器扩展工具 |
| **plan** | 任务规划与拆解 |
| **config** | 运行时配置管理 |
| **profile** | Agent 配置文件管理 |

## 启用与禁用

[打开技能管理](/web/#/skills)

在技能管理页面中，每个技能卡片上有开关按钮。关闭不需要的技能可以：
- 减少 system prompt 长度，节省 token
- 避免 Agent 使用不必要的工具
- 加快推理速度

## 技能配置

部分技能提供配置表单，由 `SkillConfigField` 自动生成。例如：

- **web_search**：设置搜索引擎、每次搜索返回条数
- **browser**：设置无头模式、超时时间
- **scheduler**：设置时区

在技能卡片上点击「配置」即可展开表单：[工具与技能配置](/web/#/config/tools)

## 编写自定义技能

创建 `SKILL.md` 文件即可定义一个技能。格式如下：

```markdown
---
name: my-custom-skill
display_name: 我的自定义技能
description: 一句话描述技能用途
version: 1.0.0
tags: [productivity, custom]
config:
  - name: api_key
    type: string
    required: true
    description: API 密钥
---

# 我的自定义技能

## 使用场景
描述什么时候应该使用这个技能...

## 使用方法
详细的使用说明和示例...
```

**YAML frontmatter** 声明元信息与配置项，**Markdown body** 是 Agent 阅读的使用说明。

## 技能加载顺序

OpenAkita 按以下顺序加载技能，后加载的同名技能会覆盖先加载的：

```
__builtin__（内置）
    → workspace（项目根目录 skills/）
        → .cursor/skills
            → .claude/skills
                → skills/ 目录
                    → 全局 home 目录
```

::: tip 实用场景
在项目根目录放一个 `skills/SKILL.md`，可以给 Agent 注入项目专属知识。例如代码规范、部署流程、API 文档等。
:::

## Skill Store

[打开 Skill Store](/web/#/skill-store)

Skill Store 是技能市场，你可以：

- **浏览** 社区贡献的技能
- **一键安装** 到本地
- **查看评分与使用量**，选择高质量技能

安装后的技能会出现在[技能管理](/web/#/skills)页面中。

## 相关页面

- [MCP 服务器](/features/mcp) — 通过 MCP 协议连接更多外部工具
- [聊天对话](/features/chat) — 在对话中使用技能
- [LLM 端点配置](/features/llm-config) — 部分技能对模型能力有要求
- [多 Agent 入门](/multi-agent/overview) — 不同 Agent 可启用不同技能
- [Agent Store / Skill Store](/multi-agent/store) — 更多技能与 Agent 模板
