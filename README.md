# MyAgent - 全能自进化AI Agent

一个基于 **Ralph Wiggum 模式** 的全能AI助手，具备自我进化能力，永不放弃，直到任务完成。

## 核心特性

| 特性 | 描述 |
|------|------|
| **永不放弃** | Ralph Wiggum模式 - 任务未完成绝不终止，遇到困难自己解决 |
| **自我进化** | 自动搜索GitHub安装新技能，没有就自己生成代码 |
| **工具调用** | 自动执行Shell命令、文件操作、Web请求 |
| **多轮对话** | 记住上下文，支持连续交互 |
| **MCP集成** | 支持调用浏览器、数据库等MCP服务器 |
| **自动测试** | 300+测试用例，自动验证功能，失败自动修复 |

## 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                      MyAgent                            │
├─────────────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │
│  │ SOUL.md │  │AGENT.md │  │ USER.md │  │MEMORY.md│    │
│  │ (哲学)   │  │ (行为)   │  │ (用户)   │  │ (记忆)   │    │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘    │
│       └────────────┴────────────┴────────────┘         │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │                   Agent Core                     │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────────────┐  │   │
│  │  │  Brain  │  │Identity │  │   Ralph Loop    │  │   │
│  │  │ (Claude)│  │ (身份)   │  │ (永不放弃循环)   │  │   │
│  │  └─────────┘  └─────────┘  └─────────────────┘  │   │
│  └─────────────────────────────────────────────────┘   │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │                    Tools                         │   │
│  │  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐    │   │
│  │  │ Shell │  │ File  │  │  Web  │  │  MCP  │    │   │
│  │  └───────┘  └───────┘  └───────┘  └───────┘    │   │
│  └─────────────────────────────────────────────────┘   │
│                         ↓                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Evolution Engine                    │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │   │
│  │  │ Analyzer │  │Installer │  │SkillGenerator│   │   │
│  │  │(需求分析) │  │(自动安装) │  │ (技能生成)    │   │   │
│  │  └──────────┘  └──────────┘  └──────────────┘   │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## 核心文档

| 文档 | 作用 | 来源 |
|------|------|------|
| `AGENT.md` | Agent行为规范、工作流程、操作指令 | [AGENTS.md Standard](https://agentsmd.io/) |
| `SOUL.md` | Agent灵魂、核心哲学、价值观 | [Claude Soul Document](https://gist.github.com/Richard-Weiss/efe157692991535403bd7e7fb20b6695) |
| `USER.md` | 用户档案、偏好、技术栈 | GitHub Copilot Memory |
| `MEMORY.md` | 工作记忆、任务进度、经验教训 | [Ralph Playbook](https://claytonfarr.github.io/ralph-playbook/) |

## 快速开始

### 安装

```bash
# 克隆仓库
git clone git@github.com:jevisuen/jevisuenbot.git
cd jevisuenbot

# 安装依赖
pip install -e .

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

### 环境变量

```bash
# 必需
ANTHROPIC_API_KEY=your-api-key

# 可选 (使用转发服务)
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 模型配置
DEFAULT_MODEL=claude-sonnet-4-20250514
```

### 启动

```bash
# 交互模式
myagent

# 查看帮助
myagent --help

# 查看状态
myagent status
```

## 使用示例

### 多轮对话

```
> 我叫张三，今年25岁
Agent: 你好，张三！很高兴认识你。

> 我叫什么名字？
Agent: 你叫张三，今年25岁。
```

### 复杂任务执行

```
> 在 /tmp/calc 目录创建一个Python计算器项目，包含加减乘除函数和测试

Agent: 正在执行任务...
  [工具调用] 创建目录...
  [工具调用] 写入 calculator.py...
  [工具调用] 写入 test_calc.py...
  [工具调用] 运行测试...

✅ 任务完成！16个测试全部通过。
```

### 自我进化

```
> 帮我分析一个Excel文件

Agent: 检测到需要Excel处理能力...
Agent: 搜索GitHub找到 openpyxl...
Agent: 正在安装...
Agent: 安装完成，开始分析...
```

## 项目结构

```
myagent/
├── AGENT.md                # Agent行为规范
├── SOUL.md                 # Agent灵魂文件
├── USER.md                 # 用户档案
├── MEMORY.md               # 关键记忆
├── PROMPT_plan.md          # Ralph计划模式提示词
├── PROMPT_build.md         # Ralph构建模式提示词
├── src/myagent/
│   ├── main.py             # CLI入口
│   ├── config.py           # 配置管理
│   ├── core/               # 核心模块
│   │   ├── agent.py        # Agent主类 (工具调用)
│   │   ├── brain.py        # LLM交互 (Claude API)
│   │   ├── ralph.py        # Ralph循环引擎
│   │   ├── identity.py     # 身份系统
│   │   └── memory.py       # 记忆管理
│   ├── skills/             # 技能系统
│   │   ├── base.py         # 技能基类
│   │   ├── registry.py     # 技能注册表
│   │   ├── loader.py       # 动态加载器
│   │   └── market.py       # GitHub技能市场
│   ├── tools/              # 工具层
│   │   ├── shell.py        # Shell命令执行
│   │   ├── file.py         # 文件操作
│   │   ├── web.py          # HTTP请求
│   │   └── mcp.py          # MCP桥接
│   ├── storage/            # 持久化
│   │   ├── database.py     # SQLite存储
│   │   └── models.py       # 数据模型
│   ├── evolution/          # 自我进化
│   │   ├── analyzer.py     # 需求分析
│   │   ├── installer.py    # 自动安装
│   │   ├── generator.py    # 技能生成
│   │   └── self_check.py   # 自我检查
│   └── testing/            # 测试系统
│       ├── runner.py       # 测试运行器
│       ├── judge.py        # 结果评判
│       ├── fixer.py        # 自动修复
│       └── cases/          # 300+测试用例
├── skills/                 # 本地技能目录
├── plugins/                # 插件目录
└── data/                   # 数据存储
```

## 测试覆盖

| 类别 | 数量 | 说明 |
|------|------|------|
| QA/基础问答 | 30 | 数学、编程知识、常识 |
| QA/推理 | 35 | 逻辑推理、代码理解 |
| QA/多轮对话 | 35 | 上下文记忆、指令跟随 |
| 工具/Shell | 40 | 命令执行、文件操作 |
| 工具/文件 | 30 | 读写、搜索、目录操作 |
| 工具/API | 30 | HTTP请求、状态码 |
| 搜索/Web | 40 | HTTP、GitHub搜索 |
| 搜索/代码 | 30 | 本地代码搜索 |
| 搜索/文档 | 30 | 项目文档搜索 |
| **总计** | **300** | |

## 参考项目

- [Claude Soul Document](https://gist.github.com/Richard-Weiss/efe157692991535403bd7e7fb20b6695) - Claude 灵魂文档
- [Ralph Playbook](https://claytonfarr.github.io/ralph-playbook/) - Ralph Wiggum 模式指南
- [AGENTS.md Standard](https://agentsmd.io/) - Agent行为规范标准
- [Anthropic Claude Code](https://github.com/anthropics/claude-code) - Claude Code 参考实现

## License

MIT
