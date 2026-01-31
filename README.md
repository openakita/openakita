<p align="center">
  <img src="docs/assets/logo.png" alt="OpenAkita Logo" width="200" />
</p>

<h1 align="center">OpenAkita</h1>

<p align="center">
  <strong>像秋田犬一样忠诚的 AI 助手</strong>
</p>

<p align="center">
  <a href="https://github.com/jevisuen/openakita/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" />
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python Version" />
  </a>
  <a href="https://github.com/jevisuen/openakita/releases">
    <img src="https://img.shields.io/badge/version-0.9.0-green.svg" alt="Version" />
  </a>
</p>

<p align="center">
  <a href="#设计理念">设计理念</a> •
  <a href="#核心功能">核心功能</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#系统架构">系统架构</a> •
  <a href="#多-agent-协同">多 Agent 协同</a>
</p>

---

## 什么是 OpenAkita？

OpenAkita 是一个**自进化 AI 助手**，名字来源于日本的秋田犬——以忠诚、聪明、可靠著称。

就像秋田犬会：
- 🐕 **忠诚守护** — 始终陪伴在你身边，不离不弃
- 🧠 **聪明学习** — 记住你的喜好，越来越懂你
- 💪 **坚持不懈** — 接到任务就会努力完成，不轻言放弃
- 🏠 **守护家园** — 保护你的数据安全，不做危险操作

OpenAkita 也是如此——它会记住你、理解你、帮助你，并且在遇到困难时不会轻易放弃。

## 设计理念

### 1. 以人为本

OpenAkita 的核心是**服务于人**，而不是展示技术。我们专注于：

- **理解意图**：不只是执行命令，而是理解你真正想要什么
- **主动沟通**：遇到问题会主动询问，而不是猜测或失败
- **尊重隐私**：你的数据只属于你，不会被滥用

### 2. 持续进化

OpenAkita 能够**自我学习和进化**：

- **记忆系统**：记住你的偏好、习惯、常用操作
- **技能扩展**：遇到新需求时，自动搜索或生成新能力
- **经验积累**：从每次任务中学习，变得越来越高效

### 3. 可靠执行

任务交给 OpenAkita 后：

- **坚持完成**：不会因为小错误就放弃
- **智能重试**：分析失败原因，尝试不同方案
- **进度保存**：长任务可以断点续传

### 4. 多端协同

通过**多 Agent 协同架构**实现高效并行：

- **Master-Worker 架构**：主节点协调，工作节点执行
- **智能调度**：根据任务复杂度分配资源
- **故障恢复**：自动检测和重启失败的节点

## 核心功能

### 基础能力

| 功能 | 说明 |
|------|------|
| **智能对话** | 多轮上下文对话，记住你说过的话 |
| **任务执行** | Shell 命令、文件操作、网络请求 |
| **代码能力** | 编写、调试、解释代码 |
| **知识检索** | 搜索网络、GitHub、本地文档 |

### 进阶能力

| 功能 | 说明 |
|------|------|
| **技能系统** | 可扩展的技能库，支持自定义 |
| **MCP 集成** | 连接浏览器、数据库、外部服务 |
| **定时任务** | 设置提醒、周期性任务 |
| **用户画像** | 学习你的偏好，个性化服务 |

### 多平台支持

| 平台 | 状态 |
|------|------|
| **CLI** | ✅ 完整支持 |
| **Telegram** | ✅ 完整支持 |
| **飞书** | ✅ 支持 |
| **企业微信** | ✅ 支持 |
| **钉钉** | ✅ 支持 |
| **QQ** | 🚧 开发中 |

## 快速开始

### 环境要求

- Python 3.11+
- [Anthropic API Key](https://console.anthropic.com/)

### 安装

```bash
# 克隆项目
git clone https://github.com/jevisuen/openakita.git
cd openakita

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装
pip install -e .

# 配置
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY
```

### 运行

```bash
# 交互式 CLI
openakita

# 执行单个任务
openakita run "帮我写一个 Python 计算器"

# 服务模式（只运行 IM 通道）
openakita serve

# 查看状态
openakita status
```

### 基本配置

```bash
# .env 文件

# 必需
ANTHROPIC_API_KEY=your-api-key

# 可选：自定义 API 端点
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 可选：启用 Telegram
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token

# 可选：启用多 Agent 协同
ORCHESTRATION_ENABLED=true
```

## 系统架构

### 整体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              OpenAkita                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│    ┌──────────────────────── 身份层 ────────────────────────┐           │
│    │                                                         │           │
│    │   SOUL.md      AGENT.md      USER.md      MEMORY.md    │           │
│    │   (价值观)      (行为规范)    (用户画像)    (记忆)       │           │
│    │                                                         │           │
│    └─────────────────────────────────────────────────────────┘           │
│                               │                                          │
│                               ▼                                          │
│    ┌──────────────────────── 核心层 ────────────────────────┐           │
│    │                                                         │           │
│    │   ┌─────────┐    ┌──────────┐    ┌───────────────┐     │           │
│    │   │  Brain  │    │ Identity │    │    Memory     │     │           │
│    │   │ (LLM)   │    │  (自我)  │    │   (记忆系统)  │     │           │
│    │   └─────────┘    └──────────┘    └───────────────┘     │           │
│    │                                                         │           │
│    └─────────────────────────────────────────────────────────┘           │
│                               │                                          │
│                               ▼                                          │
│    ┌──────────────────────── 工具层 ────────────────────────┐           │
│    │                                                         │           │
│    │   ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐          │           │
│    │   │ Shell │  │ File  │  │  Web  │  │  MCP  │          │           │
│    │   └───────┘  └───────┘  └───────┘  └───────┘          │           │
│    │                                                         │           │
│    │   ┌───────────┐  ┌────────────┐  ┌─────────────┐       │           │
│    │   │  Skills   │  │  Scheduler │  │  Evolution  │       │           │
│    │   │ (技能库)  │  │ (定时任务) │  │  (自进化)   │       │           │
│    │   └───────────┘  └────────────┘  └─────────────┘       │           │
│    │                                                         │           │
│    └─────────────────────────────────────────────────────────┘           │
│                               │                                          │
│                               ▼                                          │
│    ┌──────────────────────── 通道层 ────────────────────────┐           │
│    │                                                         │           │
│    │   ┌─────┐  ┌──────────┐  ┌──────┐  ┌──────┐  ┌────┐   │           │
│    │   │ CLI │  │ Telegram │  │ 飞书 │  │ 钉钉 │  │ QQ │   │           │
│    │   └─────┘  └──────────┘  └──────┘  └──────┘  └────┘   │           │
│    │                                                         │           │
│    └─────────────────────────────────────────────────────────┘           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 说明 |
|------|------|
| **Brain** | LLM 交互层，支持多端点故障切换 |
| **Identity** | 身份系统，加载 SOUL/AGENT/USER/MEMORY |
| **Memory** | 向量记忆系统，支持语义检索 |
| **Skills** | 技能系统，支持动态加载和扩展 |
| **Scheduler** | 定时任务调度器 |
| **Channels** | 多平台消息通道 |

## 多 Agent 协同

当启用 `ORCHESTRATION_ENABLED=true` 时，OpenAkita 进入多 Agent 协同模式：

### 协同架构

```
┌────────────────────────────────────────────────────────────────┐
│                         主进程                                  │
│                                                                 │
│   ┌─────────┐    ┌──────────┐    ┌───────────┐                 │
│   │   CLI   │    │ Gateway  │    │ Scheduler │                 │
│   │  (命令) │    │ (IM通道) │    │ (定时任务)│                 │
│   └────┬────┘    └────┬─────┘    └─────┬─────┘                 │
│        │              │                │                        │
│        └──────────────┼────────────────┘                        │
│                       ▼                                         │
│              ┌────────────────┐                                 │
│              │  MasterAgent   │                                 │
│              │   (主协调器)   │                                 │
│              │                │                                 │
│              │ • 任务路由     │                                 │
│              │ • Worker 管理  │                                 │
│              │ • 健康监控     │                                 │
│              │ • 故障恢复     │                                 │
│              └───────┬────────┘                                 │
│                      │                                          │
│              ┌───────┴────────┐                                 │
│              │   AgentBus     │                                 │
│              │   (ZMQ 通信)   │                                 │
│              └───────┬────────┘                                 │
│                      │                                          │
│              ┌───────┴────────┐                                 │
│              │ AgentRegistry  │                                 │
│              │  (注册中心)    │                                 │
│              └────────────────┘                                 │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌────────────┐ ┌────────────┐ ┌────────────┐
   │  Worker 1  │ │  Worker 2  │ │  Worker N  │
   │   (进程)   │ │   (进程)   │ │   (进程)   │
   │            │ │            │ │            │
   │ • 任务执行 │ │ • 任务执行 │ │ • 任务执行 │
   │ • 心跳上报 │ │ • 心跳上报 │ │ • 心跳上报 │
   │ • 结果返回 │ │ • 结果返回 │ │ • 结果返回 │
   └────────────┘ └────────────┘ └────────────┘
```

### 协同模式特点

| 特性 | 说明 |
|------|------|
| **智能路由** | 简单任务本地处理，复杂任务分发给 Worker |
| **无状态 Worker** | 会话历史通过消息传递，Worker 可任意调度 |
| **共享记忆** | 所有 Worker 使用相同的记忆存储 |
| **故障恢复** | 心跳检测 + Worker 自动重启 |
| **动态扩缩** | 根据负载自动增减 Worker 数量 |

### 配置项

```bash
# 启用多 Agent 协同
ORCHESTRATION_ENABLED=true

# Worker 数量
ORCHESTRATION_MIN_WORKERS=1
ORCHESTRATION_MAX_WORKERS=5

# 心跳间隔（秒）
ORCHESTRATION_HEARTBEAT_INTERVAL=5

# ZMQ 地址
ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555
ORCHESTRATION_PUB_ADDRESS=tcp://127.0.0.1:5556
```

### CLI 命令

```bash
# 查看 Agent 状态
/agents

# 查看协同统计
/status
```

## 项目结构

```
openakita/
├── identity/                 # 身份配置
│   ├── SOUL.md               # 价值观
│   ├── AGENT.md              # 行为规范
│   ├── USER.md               # 用户画像
│   └── MEMORY.md             # 工作记忆
├── src/openakita/
│   ├── core/                 # 核心模块
│   │   ├── agent.py          # Agent 主类
│   │   ├── brain.py          # LLM 交互
│   │   ├── identity.py       # 身份系统
│   │   └── ralph.py          # 任务循环
│   ├── orchestration/        # 多 Agent 协同
│   │   ├── master.py         # MasterAgent
│   │   ├── worker.py         # WorkerAgent
│   │   ├── registry.py       # 注册中心
│   │   ├── bus.py            # ZMQ 通信
│   │   └── monitor.py        # 监控告警
│   ├── tools/                # 工具层
│   ├── skills/               # 技能系统
│   ├── channels/             # 消息通道
│   ├── memory/               # 记忆系统
│   └── scheduler/            # 定时任务
├── skills/                   # 技能目录
├── data/                     # 数据存储
└── docs/                     # 文档
```

## 文档

| 文档 | 说明 |
|------|------|
| [快速开始](docs/getting-started.md) | 安装和基本使用 |
| [配置指南](docs/configuration.md) | 所有配置项说明 |
| [技能系统](docs/skills.md) | 创建和使用技能 |
| [MCP 集成](docs/mcp-integration.md) | 连接外部服务 |
| [IM 通道](docs/im-channels.md) | Telegram/飞书/钉钉配置 |
| [部署指南](docs/deploy.md) | 生产环境部署 |

## 贡献

欢迎贡献！请查看 [贡献指南](CONTRIBUTING.md)。

```bash
# 开发环境
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 代码检查
ruff check src/
mypy src/
```

## 致谢

- [Anthropic Claude](https://www.anthropic.com/claude) — LLM 引擎
- [AGENTS.md Standard](https://agentsmd.io/) — Agent 行为规范
- [ZeroMQ](https://zeromq.org/) — 进程间通信

## License

MIT License - 详见 [LICENSE](LICENSE)

---

<p align="center">
  <strong>OpenAkita — 像秋田犬一样，永远忠诚地陪伴你</strong>
</p>
