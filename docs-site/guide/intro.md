# 产品介绍

本页说明 OpenAkita 是什么、能做什么、以及如何沿着文档一步步上手。

---

## OpenAkita 是什么？

OpenAkita 是一款开源多 Agent AI 助手，部署在你自己的环境中。它不只是一个聊天机器人——而是一个帮你把事情做完的 **AI 团队**。你可以通过 CLI、桌面应用、Web 浏览器或日常使用的 IM 软件与它对话；多个 Agent 各司其职，自动协作完成从信息查询到复杂工作流的各类任务。项目基于 Python 3.11+、FastAPI 后端与 React + Tauri 桌面端构建，当前版本 **v1.26.5**。

---

## 六大核心能力

### 1. 多通道 IM 接入

接入 Telegram、飞书、钉钉、企业微信、QQ、OneBot 等主流聊天软件，在你最常用的 App 里直接与 AI 对话，无需切换窗口。一个 OpenAkita 实例可同时接入多个通道。

👉 [配置消息通道](/web/#/im) · 详见 [消息通道（IM）](/features/im-channels)

### 2. 智能记忆

三层记忆系统让 AI 真正"记住你"：

| 层级 | 说明 |
|------|------|
| **工作记忆** | 当前对话的短期上下文 |
| **语义记忆** | 跨对话持久化的事实与偏好 |
| **情节记忆** | 过往交互的经验片段，按相关性召回 |

详见 [记忆管理](/features/memory)

### 3. 技能系统

基于声明式 `SKILL.md` 的技能体系，三类来源：

- **内置技能** — 文件处理、浏览器、Shell、桌面自动化等
- **自定义技能** — 在工作区编写 `SKILL.md` 即可注册
- **技能商店** — 从社区发现并一键安装

👉 [管理技能](/web/#/skills) · 详见 [技能管理](/features/skills)

### 4. 多 Agent 协作

通过编排器（Orchestrator）自动路由消息，工厂（Factory）按需创建专属 Agent，支持最多 5 层委托。可在可视化组织编辑器中拖拽编排团队结构。

👉 [打开组织编排](/web/#/org-editor) · 详见 [多 Agent 入门](/multi-agent/overview)

### 5. MCP 协议支持

实现 Model Context Protocol，让 Agent 连接外部工具与服务——数据库、API、浏览器、文件系统等，能力无限扩展。

👉 [配置 MCP 服务器](/web/#/mcp) · 详见 [MCP 服务器](/features/mcp)

### 6. 多端访问

| 访问方式 | 说明 |
|----------|------|
| 桌面应用 | Tauri 原生应用，轻量快速 |
| Web 浏览器 | 打开浏览器即可使用 |
| 手机 | 通过 IM 机器人或移动端浏览器 |
| IM 机器人 | 飞书、钉钉、Telegram 等原生体验 |

详见 [多端访问指南](/network/multi-access)

---

## 使用方式

```bash
# 交互式 CLI
openakita

# 执行单个任务
openakita run "帮我整理本周会议纪要"

# 启动 API 服务
openakita serve

# 桌面应用（Tauri）
# 下载安装后双击启动

# Web 浏览器
# 服务启动后访问 http://localhost:8000
```

👉 [打开聊天](/web/#/chat) · 详见 [快速开始](/guide/quickstart)

---

## 核心概念

在文档中你会反复遇到以下术语：

- **Agent** — 一个独立的 AI 智能体，拥有自己的身份、记忆和技能配置。
- **Skill（技能）** — Agent 的能力单元，通过 `SKILL.md` 声明式定义。
- **Channel（通道）** — 你和 Agent 对话的"场所"，如飞书群、Telegram Chat 等。
- **Identity（身份）** — Agent 的性格与行为规范，由四个文件组成：
  - `SOUL.md` — 核心价值观
  - `AGENT.md` — 行为规范
  - `USER.md` — 用户画像
  - `MEMORY.md` — 持久化记忆
- **Memory（记忆）** — 三层记忆系统，见上文。
- **MCP** — Model Context Protocol，连接外部工具的标准协议。
- **Ralph Loop** — OpenAkita 的核心执行循环，遇到失败会分析原因并重试，永不放弃。

---

## 建议的阅读顺序

1. **[快速开始](/guide/quickstart)** — 3 分钟跑起来
2. **[安装部署](/guide/installation)** — 完整安装选项与部署方式
3. **[聊天对话](/features/chat)** — 基本对话功能
4. **[消息通道（IM）](/features/im-channels)** — 接入你的聊天软件
5. **[LLM 端点配置](/features/llm-config)** — 配置大模型 API
6. **[技能管理](/features/skills)** — 了解与扩展能力
7. **[MCP 服务器](/features/mcp)** — 连接外部工具
8. **[多 Agent 入门](/multi-agent/overview)** — 多 Agent 协作
9. **[身份配置](/features/identity)** — 自定义 Agent 人格

按需配置，无需全部阅读。遇到问题可随时在 [GitHub Issues](https://github.com/openakita/openakita/issues) 反馈。

