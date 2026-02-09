<p align="center">
  <img src="docs/assets/logo.png" alt="OpenAkita Logo" width="200" />
</p>

<h1 align="center">OpenAkita</h1>

<p align="center">
  <strong>自进化 AI Agent — 自主学习，永不放弃</strong>
</p>

<p align="center">
  <a href="https://github.com/openakita/openakita/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" />
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python Version" />
  </a>
  <a href="https://github.com/openakita/openakita/releases">
    <img src="https://img.shields.io/github/v/release/openakita/openakita" alt="Release" />
  </a>
  <a href="https://pypi.org/project/openakita/">
    <img src="https://img.shields.io/pypi/v/openakita?color=green" alt="PyPI" />
  </a>
  <a href="https://github.com/openakita/openakita/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/openakita/openakita/ci.yml?branch=main" alt="Build Status" />
  </a>
</p>

<p align="center">
  <a href="#setup-center可视化安装与配置">Setup Center</a> •
  <a href="#核心特性">核心特性</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#架构">架构</a> •
  <a href="#文档">文档</a>
</p>

<p align="center">
  <a href="./README.md">English Documentation</a>
</p>

---

## 什么是 OpenAkita？

OpenAkita 是一个**自进化 AI Agent 框架**。它能自主学习新技能、每日自检修复、从任务执行中积累经验，并在遇到困难时永不放弃——持续尝试直到任务完成。

就像它名字来源的秋田犬一样：**忠诚、可靠、永不言弃**。

- **自主进化** — 自动生成技能、安装依赖、从错误中学习
- **永不放弃** — Ralph Wiggum 模式：持续执行循环，任务未完成不会终止
- **记忆成长** — 记住你的偏好和习惯，每日自动整理记忆
- **标准化集成** — 遵循 MCP 和 Agent Skills 标准，生态兼容性强
- **全中文支持** — 从 Setup Center 到 IM 通道、LLM 服务商，完整的中文生态

---

## Setup Center：可视化安装与配置

<p align="center">
  <img src="docs/assets/setupcenter.png" alt="Setup Center" width="800" />
</p>

OpenAkita 提供跨平台的 **Setup Center** 桌面应用（基于 Tauri + React），让安装部署变得简单直观：

- **Python 环境管理** — 自动检测系统 Python，支持一键安装嵌入式 Python
- **一键安装** — 创建 venv + pip 安装 OpenAkita，支持 PyPI / GitHub Release / 本地源码
- **版本管理** — 支持指定安装版本，默认与 Setup Center 同版本保证兼容性
- **国内镜像** — 内置清华 TUNA、阿里云镜像源，加速下载
- **LLM 端点管理** — 多服务商、多端点、主备切换；在线拉取模型列表 + 搜索选择
- **提示词编译模型** — 独立配置快速模型端点，预处理用户指令
- **IM 通道配置** — Telegram、飞书、企业微信、钉钉、QQ 一站式配置
- **Agent 与技能配置** — 行为参数、技能开关、MCP 工具管理
- **后台常驻** — 系统托盘 + 开机自启动，一键启动/停止服务
- **状态监控** — 运行状态面板，实时查看服务日志

> **下载安装包**：[GitHub Releases](https://github.com/openakita/openakita/releases)
>
> 支持 Windows (.exe) / macOS (.dmg) / Linux (.deb / .AppImage)

---

## 核心特性

| 特性 | 描述 |
|------|------|
| **自学习与自进化** | 每日自检修复（04:00）、记忆整理（03:00）、任务复盘、技能自动生成、依赖自动安装 |
| **Ralph Wiggum 模式** | 永不放弃的执行循环：Plan → Act → Verify → 重复直到完成，支持断点恢复 |
| **提示词编译** | 双阶段提示架构：快速模型预处理指令，身份文件编译压缩，自动检测复合任务 |
| **MCP 标准化集成** | Model Context Protocol 规范，stdio 传输，服务自动发现，内置 Web 搜索 |
| **Skill 标准化集成** | Agent Skills 规范（SKILL.md），8 个标准目录加载，GitHub 一键安装，LLM 自动生成 |
| **Plan 模式** | 智能检测多步骤任务，自动创建执行计划，实时进度追踪，持久化为 Markdown |
| **多 LLM 端点** | 9 个服务商、能力路由、优先级故障转移、Thinking 模式、多模态（文本/图片/视频/语音） |
| **多平台 IM** | CLI / Telegram / 飞书 / 钉钉（完整支持）；企业微信 / QQ（已实现） |
| **桌面自动化** | Windows UIAutomation + 视觉识别，9 个工具：截屏、点击、输入、快捷键、窗口管理等 |
| **多 Agent 协同** | Master-Worker 架构，ZMQ 消息总线，智能路由，动态扩缩容，故障自恢复 |
| **定时任务** | Cron / 间隔 / 一次性触发，提醒型 + 任务型，持久化存储 |
| **身份与记忆** | 四文件身份架构（SOUL / AGENT / USER / MEMORY），向量检索，每日自动整理 |
| **工具体系** | 11 个类别、50+ 工具，三层渐进式披露（目录 → 详情 → 执行）减少 token 消耗 |
| **Setup Center** | Tauri 跨平台桌面应用，全流程引导式安装配置，托盘常驻，状态监控 |

---

## 自学习与自进化

OpenAkita 的核心差异：**不只是工具，而是会自主学习和成长的 Agent**。

| 机制 | 触发时机 | 具体行为 |
|------|---------|---------|
| **每日自检修复** | 每天 04:00 | 分析 ERROR 日志 → LLM 诊断 → 工具类错误自动修复 → 生成报告 |
| **记忆整理** | 每天 03:00 | 会话记忆整理 → 语义去重 → 提取洞察 → 刷新 MEMORY.md |
| **任务复盘** | 长任务完成后（>60s） | 分析执行效率 → 提取经验教训 → 存入长期记忆 |
| **技能自动生成** | 遇到缺失能力时 | LLM 生成 SKILL.md + 脚本 → 自动测试验证 → 注册加载 |
| **依赖自动安装** | pip/npm 缺失时 | 搜索 GitHub → 安装依赖 → 失败则自动生成技能替代 |
| **实时记忆提取** | 每轮对话 | 提取偏好/规则/事实 → 向量存储 → MEMORY.md 自动更新 |
| **用户画像学习** | 对话过程中 | 识别用户偏好和习惯 → 更新 USER.md → 越用越懂你 |

---

## 中文生态支持

OpenAkita 对中文用户提供完整的本地化支持：

- **全中文 Setup Center** — 从安装到配置，全程中文引导界面
- **国内 LLM 服务商** — 内置阿里云 DashScope（通义千问）、月之暗面 Kimi、MiniMax、DeepSeek、硅基流动
- **国内 PyPI 镜像** — 内置清华 TUNA、阿里云镜像源，一键切换
- **中文 IM 通道** — 飞书、企业微信、钉钉、QQ 原生支持
- **中文桌面自动化** — 支持中文自然语言描述 UI 元素（如"保存按钮"）
- **中文部署文档** — 完整的中文安装部署指南

### 推荐模型

| 模型 | 厂商 | 说明 |
|------|------|------|
| `claude-sonnet-4-5-*` | Anthropic | 默认推荐，性能均衡 |
| `claude-opus-4-5-*` | Anthropic | 能力最强 |
| `qwen3-max` | 阿里通义 | 中文能力强 |
| `deepseek-v3` | DeepSeek | 高性价比 |
| `kimi-k2.5` | 月之暗面 | 超长上下文 |
| `minimax-m2.1` | MiniMax | 对话创作出色 |

> 复杂任务建议开启 Thinking 模式。将模型设为 `*-thinking` 版本（如 `claude-opus-4-5-20251101-thinking`）可获得更好的推理效果。

---

## 快速开始

### 方式一：Setup Center（推荐）

最简单的安装方式，图形化引导，无需命令行经验：

1. 从 [GitHub Releases](https://github.com/openakita/openakita/releases) 下载对应平台安装包
2. 安装并启动 Setup Center
3. 按向导完成：Python 环境 → 安装 OpenAkita → 配置 LLM → 配置 IM → 完成并启动

### 方式二：PyPI 安装

```bash
# 安装
pip install openakita

# 安装全部可选功能
pip install openakita[all]

# 运行配置向导
openakita init
```

可选功能包：`feishu`（飞书）、`whisper`（语音识别）、`browser`（浏览器自动化）、`windows`（桌面自动化）

### 方式三：源码安装

```bash
git clone https://github.com/openakita/openakita.git
cd openakita
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[all]"
openakita init
```

### 运行

```bash
# 交互式 CLI
openakita

# 执行单个任务
openakita run "创建一个带测试的 Python 计算器"

# 服务模式（IM 通道）
openakita serve

# 后台守护进程
openakita daemon start

# 查看状态
openakita status
```

### 基本配置

```bash
# .env 文件（最小配置）

# LLM API（必需，至少配置一个）
ANTHROPIC_API_KEY=your-api-key

# Telegram（可选）
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token

# 飞书（可选）
FEISHU_ENABLED=true
FEISHU_APP_ID=your-app-id
FEISHU_APP_SECRET=your-app-secret
```

---

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                          OpenAkita                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────── Setup Center ────────────────────────┐   │
│  │  Tauri + React 桌面应用 · 安装配置 · 托盘常驻 · 状态监控  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── 身份层 ──────────────────────────────┐   │
│  │  SOUL.md(灵魂) · AGENT.md(行为) · USER.md(用户) · MEMORY.md │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── 核心层 ──────────────────────────────┐   │
│  │  Brain(LLM) · Identity(身份) · Memory(记忆) · Ralph(循环) │   │
│  │  Prompt Compiler(提示词编译) · Task Monitor(任务监控)      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── 工具层 ──────────────────────────────┐   │
│  │  Shell · File · Web · MCP · Skills · Scheduler            │   │
│  │  Browser · Desktop · Plan · Profile · IM Channel          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── 进化引擎 ────────────────────────────┐   │
│  │  SelfCheck(自检) · Generator(技能生成) · Installer(自动安装)│   │
│  │  LogAnalyzer(日志分析) · DailyConsolidator(记忆整理)       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── 通道层 ──────────────────────────────┐   │
│  │  CLI · Telegram · 飞书 · 企业微信 · 钉钉 · QQ            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 描述 |
|------|------|
| **Brain** | 统一 LLM 客户端，多端点故障转移，能力路由 |
| **Identity** | 四文件身份系统，编译压缩为 token 高效摘要 |
| **Memory** | 向量记忆系统（ChromaDB），语义检索，每日自动整理 |
| **Ralph Loop** | 永不放弃执行循环，StopHook 拦截，断点恢复 |
| **Prompt Compiler** | 双阶段提示架构，快速模型预处理 |
| **Task Monitor** | 执行监控，超时切换模型，任务复盘 |
| **Evolution Engine** | 自检修复、技能生成、依赖安装、日志分析 |
| **Skills** | Agent Skills 标准，动态加载，GitHub 安装，自动生成 |
| **MCP** | Model Context Protocol，服务发现，工具代理 |
| **Scheduler** | 定时任务调度，Cron / 间隔 / 一次性 |
| **Channels** | 统一消息格式，多平台 IM 适配 |

---

## 文档

| 文档 | 描述 |
|------|------|
| [快速开始](docs/getting-started.md) | 安装和入门指南 |
| [架构设计](docs/architecture.md) | 系统设计和组件说明 |
| [配置说明](docs/configuration.md) | 所有配置选项详解 |
| [部署指南](docs/deploy.md) | 生产环境部署（systemd / Docker / nohup） |
| [MCP 集成](docs/mcp-integration.md) | 连接外部服务 |
| [IM 通道](docs/im-channels.md) | Telegram / 飞书 / 钉钉配置 |
| [技能系统](docs/skills.md) | 创建和使用技能 |
| [测试框架](docs/testing.md) | 测试框架和用例 |

---

## 社区

加入社区，获取帮助、分享经验、参与讨论：

<table>
  <tr>
    <td align="center">
      <img src="docs/assets/wechat_group.jpg" width="200" alt="微信群二维码" /><br/>
      <b>微信交流群</b><br/>
      <sub>扫码加入中文社区</sub>
    </td>
    <td>
      <b>微信群</b> — 中文用户即时交流<br/><br/>
      <b>Discord</b> — <a href="https://discord.gg/Mkpd3rsm">加入 Discord</a><br/><br/>
      <b>X (Twitter)</b> — <a href="https://x.com/openakita">@openakita</a><br/><br/>
      <b>邮箱</b> — <a href="mailto:zacon365@gmail.com">zacon365@gmail.com</a>
    </td>
  </tr>
</table>

- [文档](docs/) — 完整使用指南
- [Issues](https://github.com/openakita/openakita/issues) — Bug 报告与功能请求
- [Discussions](https://github.com/openakita/openakita/discussions) — 技术讨论与问答
- [Star 项目](https://github.com/openakita/openakita) — 支持我们

---

## 致谢

- [Anthropic Claude](https://www.anthropic.com/claude) — 核心 LLM 引擎
- [Tauri](https://tauri.app/) — Setup Center 跨平台桌面框架
- [browser-use](https://github.com/browser-use/browser-use) — AI 浏览器自动化
- [AGENTS.md Standard](https://agentsmd.io/) — Agent 行为规范标准
- [Agent Skills](https://agentskills.io/) — 技能标准化规范
- [ZeroMQ](https://zeromq.org/) — 多 Agent 进程间通信

## 许可证

MIT License — 详见 [LICENSE](LICENSE)

---

<p align="center">
  <strong>OpenAkita — 自进化 AI Agent，自主学习，永不放弃</strong>
</p>
