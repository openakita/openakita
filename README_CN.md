<p align="center">
  <img src="docs/assets/logo.png" alt="OpenAkita Logo" width="200" />
</p>

<h1 align="center">OpenAkita</h1>

<p align="center">
  <strong>忠诚可靠的 AI 伙伴</strong>
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
  <a href="https://github.com/openakita/openakita/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/openakita/openakita/ci.yml?branch=main" alt="Build Status" />
  </a>
</p>

<p align="center">
  <a href="#特性">特性</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#文档">文档</a> •
  <a href="#架构">架构</a> •
  <a href="#贡献">贡献</a>
</p>

<p align="center">
  <a href="./README.md">📖 English Documentation</a>
</p>

---

## 什么是 OpenAkita？

OpenAkita 是一个**自进化 AI 助手** — 你在数字世界中忠诚可靠的伙伴。

就像它名字来源的秋田犬一样，OpenAkita 具备这些品质：
- 🤝 **忠诚伙伴** — 始终陪伴在你身边，随时准备帮助你
- 🧠 **与你共同成长** — 记住你的偏好，变得越来越懂你
- 💪 **可靠搭档** — 承诺完成任务，不轻言放弃
- 🛡️ **值得信赖** — 保护你的数据安全，尊重你的隐私

OpenAkita 不只是一个工具 — 它是一个记住你、理解你、与你并肩面对每个挑战的伙伴。

### 为什么选择 OpenAkita？

- **🔄 自我进化**：自动从 GitHub 搜索技能或生成代码获取新能力
- **💪 永不放弃**：持续执行直到任务完成
- **🛠️ 工具执行**：原生支持 Shell 命令、文件操作和 Web 请求
- **🔌 MCP 集成**：通过 Model Context Protocol 连接浏览器、数据库等外部服务
- **💬 多平台部署**：支持 CLI、Telegram、钉钉、飞书、企业微信

## 特性

| 特性 | 描述 |
|------|------|
| **Ralph Wiggum 模式** | 持续执行循环 - 任务未验证完成不会终止 |
| **自我进化** | 搜索 GitHub 获取技能，安装依赖，或即时生成代码 |
| **工具调用** | 执行 Shell 命令、文件操作、HTTP 请求，内置安全机制 |
| **MCP 支持** | 集成 Model Context Protocol 服务器，支持浏览器自动化、数据库 |
| **多轮对话** | 上下文感知对话，支持持久化记忆 |
| **自动测试** | 300+ 测试用例，自动验证和自我修复 |
| **多平台** | 支持 CLI、Telegram、钉钉、飞书、企业微信、QQ |

## 快速开始

### 前置要求

- Python 3.11 或更高版本
- [Anthropic API 密钥](https://console.anthropic.com/)

### 安装

```bash
# 克隆仓库
git clone https://github.com/openakita/openakita.git
cd openakita

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装
pip install -e .

# 配置环境变量
cp .env.example .env
# 编辑 .env 添加你的 ANTHROPIC_API_KEY
```

### 配置

创建 `.env` 文件，至少包含：

```bash
# 必需
ANTHROPIC_API_KEY=你的API密钥

# 可选：自定义 API 端点（国内用户推荐使用代理）
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 可选：模型选择
DEFAULT_MODEL=claude-sonnet-4-20250514
```

### 运行

```bash
# 交互式 CLI 模式
openakita

# 执行单个任务
openakita run "创建一个带测试的 Python 计算器"

# 查看状态
openakita status

# 运行自检
openakita selfcheck
```

## 文档

| 文档 | 描述 |
|------|------|
| [📖 快速开始](docs/getting-started.md) | 安装和入门指南 |
| [🏗️ 架构设计](docs/architecture.md) | 系统设计和组件说明 |
| [🔧 配置说明](docs/configuration.md) | 所有配置选项详解 |
| [🚀 部署指南](docs/deploy.md) | 生产环境部署指南 |
| [🔌 MCP 集成](docs/mcp-integration.md) | 连接外部服务 |
| [📱 IM 通道](docs/im-channels.md) | Telegram、钉钉、飞书配置 |
| [🎯 技能系统](docs/skills.md) | 创建和使用技能 |
| [🧪 测试框架](docs/testing.md) | 测试框架和覆盖率 |

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                         OpenAkita                              │
├─────────────────────────────────────────────────────────────┤
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│   │ SOUL.md │  │AGENT.md │  │ USER.md │  │MEMORY.md│       │
│   │  (灵魂)  │  │  (行为)  │  │  (用户)  │  │  (记忆)  │       │
│   └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘       │
│        └────────────┴────────────┴────────────┘            │
│                           ↓                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                    Agent 核心                        │  │
│   │  ┌─────────┐  ┌──────────┐  ┌─────────────────────┐ │  │
│   │  │  Brain  │  │ Identity │  │   Ralph Loop        │ │  │
│   │  │(Claude) │  │  (身份)   │  │  (永不放弃循环)      │ │  │
│   │  └─────────┘  └──────────┘  └─────────────────────┘ │  │
│   └─────────────────────────────────────────────────────┘  │
│                           ↓                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                     工具层                           │  │
│   │  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐        │  │
│   │  │ Shell │  │ 文件   │  │  Web  │  │  MCP  │        │  │
│   │  └───────┘  └───────┘  └───────┘  └───────┘        │  │
│   └─────────────────────────────────────────────────────┘  │
│                           ↓                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                   进化引擎                           │  │
│   │  ┌──────────┐  ┌───────────┐  ┌────────────────┐   │  │
│   │  │ Analyzer │  │ Installer │  │ SkillGenerator │   │  │
│   │  │ (需求分析) │  │ (自动安装) │  │   (技能生成)    │   │  │
│   │  └──────────┘  └───────────┘  └────────────────┘   │  │
│   └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 核心文档

OpenAkita 使用独特的基于文档的身份和记忆系统：

| 文档 | 用途 |
|------|------|
| `identity/SOUL.md` | 核心哲学和价值观 - Agent 的"灵魂" |
| `identity/AGENT.md` | 行为规范和工作流程 |
| `identity/USER.md` | 用户档案、偏好和上下文 |
| `identity/MEMORY.md` | 工作记忆、任务进度、经验教训 |

### Ralph Wiggum 模式

Agent 在持续循环中运行：

```
接收任务 → 分析 → 执行 → 验证 → 重复直到完成
              ↓
         遇到失败时：
         1. 分析错误原因
         2. 搜索 GitHub 寻找解决方案
         3. 安装或生成修复代码
         4. 重试任务
```

## 使用示例

### 多轮对话

```
用户: 我叫张三，今年25岁
Agent: 你好，张三！很高兴认识你。

用户: 我叫什么名字？
Agent: 你叫张三，今年25岁。
```

### 复杂任务执行

```
用户: 在 /tmp/calc 目录创建一个 Python 计算器项目，包含加减乘除函数和测试

Agent: 正在执行任务...
  [工具调用] 创建目录结构...
  [工具调用] 写入 calculator.py...
  [工具调用] 写入 test_calculator.py...
  [工具调用] 运行测试...

✅ 任务完成！16 个测试全部通过。
```

### 自我进化

```
用户: 帮我分析这个 Excel 文件

Agent: 检测到需要 Excel 处理能力...
Agent: 搜索 GitHub 找到 openpyxl...
Agent: 正在安装 openpyxl...
Agent: 安装完成，开始分析文件...
```

## 项目结构

```
openakita/
├── identity/               # Agent 身份配置
│   ├── SOUL.md             # Agent 核心哲学
│   ├── AGENT.md            # 行为规范
│   ├── USER.md             # 用户档案
│   └── MEMORY.md           # 工作记忆
├── src/openakita/
│   ├── core/               # 核心模块
│   │   ├── agent.py        # Agent 主类
│   │   ├── brain.py        # Claude API 集成
│   │   ├── ralph.py        # Ralph 循环引擎
│   │   ├── identity.py     # 身份系统
│   │   └── memory.py       # 记忆管理
│   ├── tools/              # 工具实现
│   │   ├── shell.py        # Shell 执行
│   │   ├── file.py         # 文件操作
│   │   ├── web.py          # HTTP 请求
│   │   └── mcp.py          # MCP 桥接
│   ├── evolution/          # 自我进化
│   │   ├── analyzer.py     # 需求分析
│   │   ├── installer.py    # 自动安装
│   │   └── generator.py    # 技能生成
│   ├── channels/           # IM 集成
│   │   └── adapters/       # 平台适配器
│   ├── skills/             # 技能系统
│   ├── storage/            # 持久化层
│   └── testing/            # 测试框架
├── skills/                 # 本地技能目录
├── plugins/                # 插件目录
├── data/                   # 数据存储
└── docs/                   # 文档
```

## 测试覆盖

| 类别 | 数量 | 描述 |
|------|------|------|
| QA/基础问答 | 30 | 数学、编程知识、常识 |
| QA/推理 | 35 | 逻辑推理、代码理解 |
| QA/多轮对话 | 35 | 上下文记忆、指令跟随 |
| 工具/Shell | 40 | 命令执行、文件操作 |
| 工具/文件 | 30 | 读写、搜索、目录操作 |
| 工具/API | 30 | HTTP 请求、状态码 |
| 搜索/Web | 40 | HTTP、GitHub 搜索 |
| 搜索/代码 | 30 | 本地代码搜索 |
| 搜索/文档 | 30 | 项目文档搜索 |
| **总计** | **300** | |

## 部署方式

### CLI 模式（默认）

```bash
openakita
```

### Telegram 机器人

```bash
# 在 .env 中启用
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=你的token

# 运行
python scripts/run_telegram_bot.py
```

### Docker

```bash
docker build -t openakita .
docker run -d --name openakita -v $(pwd)/.env:/app/.env openakita
```

### Systemd 服务

```bash
sudo cp openakita.service /etc/systemd/system/
sudo systemctl enable openakita
sudo systemctl start openakita
```

详细部署说明请参阅 [docs/deploy.md](docs/deploy.md)。

## 贡献

我们欢迎各种形式的贡献！请查看 [贡献指南](CONTRIBUTING.md) 了解详情。

### 快速贡献指南

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

### 开发环境设置

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 类型检查
mypy src/

# 代码检查
ruff check src/
```

## 社区

- 📖 [文档](docs/)
- 🐛 [问题追踪](https://github.com/openakita/openakita/issues)
- 💬 [讨论区](https://github.com/openakita/openakita/discussions)
- 📧 [邮箱](mailto:contact@example.com)

## 致谢

OpenAkita 站在巨人的肩膀上：

- [Anthropic Claude](https://www.anthropic.com/claude) - 核心 LLM 引擎
- [Claude Soul Document](https://gist.github.com/Richard-Weiss/efe157692991535403bd7e7fb20b6695) - 灵魂文档灵感来源
- [Ralph Playbook](https://claytonfarr.github.io/ralph-playbook/) - Ralph Wiggum 模式哲学
- [AGENTS.md Standard](https://agentsmd.io/) - Agent 行为规范标准

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。

---

<p align="center">
  <strong>OpenAkita — 忠诚可靠的 AI 伙伴</strong>
</p>

<p align="center">
  <a href="#openakita">返回顶部 ↑</a>
</p>
