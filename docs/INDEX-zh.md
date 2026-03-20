# OpenAkita 文档索引

> 本文档是 OpenAkita 中文文档的导航索引，帮助你快速找到需要的信息。

---

## 🚀 快速开始

| 文档 | 说明 | 适合人群 |
|------|------|----------|
| [快速入门](getting-started-zh.md) | 安装和基础使用指南 | 新手用户 |
| [配置指南](configuration-guide.md) | 桌面客户端完整配置流程 | 所有用户 |
| [配置说明](configuration-zh.md) | 所有配置项详细说明 | 高级用户 |
| [常见问题](FAQ-zh.md) | 常见问题解答 | 所有用户 |

---

## 📚 核心文档

### 架构与设计

| 文档 | 说明 |
|------|------|
| [架构设计](architecture.md) | 系统整体架构和组件说明 |
| [多 Agent 架构](multi-agent-architecture.md) | 多 Agent 协作系统设计 |
| [记忆系统架构](memory_architecture.md) | 三层记忆系统详解 |
| [工具系统架构](tool-system-architecture.md) | 工具系统设计与实现 |
| [技能加载架构](skill-loading-architecture.md) | 技能加载机制说明 |
| [Prompt 结构](prompt_structure.md) | Prompt 组装与编译流程 |

### 功能模块

| 文档 | 说明 |
|------|------|
| [技能系统](skills.md) | 技能创建、安装和使用 |
| [工具定义规范](tool-definition-spec.md) | 工具定义的标准格式 |
| [MCP 集成](mcp-integration.md) | MCP 服务集成指南 |
| [人格与活人感](persona-and-liveness.md) | 人格系统和主动消息引擎 |
| [系统命令](system-commands.md) | 系统级命令说明 |

---

## 💬 IM 通道接入

### 教程与指南

| 文档 | 说明 |
|------|------|
| [IM 通道接入教程](im-channel-setup-tutorial.md) | 6 大平台一站式接入指南 |
| [LLM 服务商配置教程](llm-provider-setup-tutorial.md) | 各大 LLM 服务商 API 申请与配置 |
| [IM 通道](im-channels.md) | IM 通道系统概述 |

### 平台特定说明

| 平台 | 文档 |
|------|------|
| **Telegram** | [Telegram 接入说明](TELEGRAM_IM_NOTES.md) |
| **飞书** | [飞书接入说明](FEISHU_IM_NOTES.md) |
| **钉钉** | [钉钉接入说明](DINGTALK_IM_NOTES.md) |
| **企业微信** | [企业微信接入说明](WEWORK_WS_IM_NOTES.md) |
| **OneBot** | [OneBot 接入说明](ONEBOT_IM_NOTES.md) |

---

## 🖥️ 桌面应用

| 文档 | 说明 |
|------|------|
| [桌面应用指南](desktop-app-guide.md) | 桌面客户端使用指南（中文） |
| [Desktop App Guide](desktop-app-guide_en.md) | Desktop application guide (English) |
| [桌面终端改进](desktop-terminal-improvements.md) | 桌面终端功能改进说明 |

---

## 🛠️ 开发与贡献

| 文档 | 说明 |
|------|------|
| [贡献指南](CONTRIBUTING-zh.md) | 如何为 OpenAkita 做贡献 |
| [Contributing Guide](../CONTRIBUTING.md) | Contributing to OpenAkita (English) |
| [测试指南](testing.md) | 测试编写和运行 |
| [测试用例](test-cases.md) | 测试用例集合 |
| [依赖说明](dependencies.md) | 项目依赖详解 |

---

## 📦 部署与运维

| 文档 | 说明 |
|------|------|
| [部署指南](deploy.md) | 生产环境部署指南（中文） |
| [Deploy Guide](deploy_en.md) | Deployment guide (English) |
| [发布计划](release-playbook.md) | 版本发布流程 |
| [反馈管理指南](feedback-admin-guide.md) | 用户反馈管理 |
| [反馈调试指南](feedback-debug-guide.md) | 反馈问题调试 |

---

## 🤖 Agent 组织

| 文档 | 说明 |
|------|------|
| [Agent 组织用户指南](agent-org-user-guide.md) | Agent 组织功能使用说明 |
| [Agent 组织技术设计](agent-org-technical-design.md) | Agent 组织技术实现 |
| [Agent 共享规范](agent-sharing-spec.md) | Agent 共享标准 |

---

## 🔍 调试与故障排查

| 文档 | 说明 |
|------|------|
| [LLM 调试失败模式](llm_debug_failure_modes.md) | LLM 调用失败模式分析 |
| [API 对比](api-comparison-openai-anthropic.md) | OpenAI vs Anthropic API 对比 |
| [LLM API 能力研究](llm-api-capabilities-research.md) | 各大 LLM API 能力调研 |
| [消息队列与中断](message-queue-and-interrupt.md) | 消息队列和中断处理机制 |
| [浏览器认证指南](browser-auth-guide.md) | 浏览器自动化认证 |

---

## 📝 示例代码

| 文档 | 说明 |
|------|------|
| [示例目录](examples/) | 示例代码和脚本 |
| [API 转换器示例](examples/api_converter_example.py) | API 格式转换示例 |

---

## 📋 规划与内部文档

| 文档 | 说明 |
|------|------|
| [开放 Issues 汇总](open-issues.md) | GitHub Issues 问题汇总 |
| [IM 通道任务](im-channel-tasks.md) | IM 通道开发任务 |
| [用户功能介绍](OpenAkita_用户功能介绍.md) | 用户功能详细介绍 |
| [功能特点总览](OpenAkita_功能特点总览.md) | 产品功能特点总结 |
| [公众号规划与配图需求](OpenAkita_公众号规划与配图需求.md) | 公众号运营规划 |
| [自媒体宣传 Brief](OpenAkita_自媒体宣传 Brief.md) | 自媒体宣传方案 |

---

## 🔗 外部资源

| 资源 | 说明 |
|------|------|
| [GitHub 仓库](https://github.com/openakita/openakita) | 源代码和 Issues |
| [GitHub Discussions](https://github.com/openakita/openakita/discussions) | 社区讨论 |
| [PyPI 包](https://pypi.org/project/openakita/) | Python 包下载 |
| [GitHub Releases](https://github.com/openakita/openakita/releases) | 桌面客户端下载 |

---

## 📞 社区支持

| 渠道 | 说明 |
|------|------|
| **微信公众号** | 扫码关注，获取最新动态 |
| **微信群** | 扫码加入交流群（7 天更新） |
| **QQ 群** | 群号：854429727 |
| **Discord** | [加入 Discord](https://discord.gg/vFwxNVNH) |
| **X (Twitter)** | [@openakita](https://x.com/openakita) |
| **Email** | zacon365@gmail.com |

---

## 📖 文档维护

### 文档规范

- 使用 Markdown 格式
- 中文文档使用 `-zh.md` 后缀
- 代码示例要可执行
- 保持与现有文档风格一致

### 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-03-19 | 新增配置说明、贡献指南、快速入门、FAQ 中文版本 |

### 待完善文档

以下文档需要补充或更新：

- [ ] `architecture.md` - 补充最新架构图
- [ ] `skills.md` - 添加更多技能示例
- [ ] `testing.md` - 补充测试覆盖率报告
- [ ] `deploy.md` - 更新部署步骤

---

## 📝 需要帮助？

如果找不到需要的文档：

1. 在 GitHub 搜索 Issues 和 Discussions
2. 在社区群聊中提问
3. 提交 Issue 请求补充文档
4. 直接为文档做贡献！
