# 配置说明

本文档涵盖 OpenAkita 的所有配置选项。

## 环境变量

OpenAkita 主要通过环境变量进行配置，通常存储在 `.env` 文件中。

### 必需设置

| 变量 | 说明 | 示例 |
|------|------|------|
| `ANTHROPIC_API_KEY` | 你的 Anthropic API 密钥 | `sk-ant-...` |

### API 设置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | API 端点 URL |
| `DEFAULT_MODEL` | `claude-sonnet-4-20250514` | 使用的模型 |
| `MAX_TOKENS` | `8192` | 最大响应 token 数 |

### Agent 行为

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENT_NAME` | `OpenAkita` | 显示名称 |
| `MAX_ITERATIONS` | `100` | 最大 Ralph 循环迭代次数 |
| `AUTO_CONFIRM` | `false` | 自动确认危险操作 |

### 存储

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_PATH` | `data/agent.db` | SQLite 数据库位置 |
| `LOG_LEVEL` | `INFO` | 日志详细程度 |

### GitHub 集成

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GITHUB_TOKEN` | - | 用于技能搜索的 GitHub 个人访问令牌 |

### 人格系统

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PERSONA_NAME` | `default` | 激活的人格预设：`default` / `business` / `tech_expert` / `butler` / `girlfriend` / `boyfriend` / `family` / `jarvis` |

内置 8 种人格预设，具有不同的沟通风格。用户也可以通过聊天命令切换人格。人格偏好会从对话中学习（LLM 驱动的特征挖掘），并在每日整理时提升到身份文件。

### 活人感引擎（主动消息）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROACTIVE_ENABLED` | `false` | 启用主动消息（问候、跟进、记忆回顾） |
| `PROACTIVE_MAX_DAILY_MESSAGES` | `3` | 每天最多主动消息数 |
| `PROACTIVE_MIN_INTERVAL_MINUTES` | `120` | 主动消息最小间隔（分钟） |
| `PROACTIVE_QUIET_HOURS_START` | `23` | 安静时段开始（0-23 点，不发送主动消息） |
| `PROACTIVE_QUIET_HOURS_END` | `7` | 安静时段结束（0-23 点） |
| `PROACTIVE_IDLE_THRESHOLD_HOURS` | `24` | 用户 inactive 多少小时后触发 idle 问候 |

启用后，Agent 会主动发送问候、任务跟进和基于记忆的提醒。频率会根据用户反馈自适应调整（快速回复 = 保持频率，忽略 = 降低频率）。

### 表情包引擎

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STICKER_ENABLED` | `true` | 在 IM 通道中启用表情包/贴纸功能 |
| `STICKER_DATA_DIR` | `data/sticker` | 表情包数据目录（相对于项目根目录） |

集成 [ChineseBQB](https://github.com/zhaoolee/ChineseBQB)（5700+ 表情包）。每个人格预设都有不同的表情包策略（例如：商务=从不，女友=频繁）。

> **注意**：人格、主动消息和表情包设置也可以通过聊天命令在运行时更改。运行时更改会持久化到 `data/runtime_state.json` 并在 Agent 重启后保留。

## IM 通道配置

### Telegram

```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token
```

获取 Bot Token：
1. 在 Telegram 上联系 [@BotFather](https://t.me/botfather)
2. 使用 `/newbot` 命令
3. 按照提示操作
4. 复制 Token

### 钉钉

```bash
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=your-client-id
DINGTALK_CLIENT_SECRET=your-client-secret
```

### 飞书

```bash
FEISHU_ENABLED=true
FEISHU_APP_ID=your-app-id
FEISHU_APP_SECRET=your-app-secret
```

### 企业微信

```bash
WEWORK_ENABLED=true
WEWORK_CORP_ID=your-corp-id
WEWORK_AGENT_ID=your-agent-id
WEWORK_SECRET=your-secret
```

### QQ 官方机器人

```bash
QQBOT_ENABLED=true
QQBOT_APP_ID=your-app-id
QQBOT_APP_SECRET=your-app-secret
QQBOT_SANDBOX=false
```

### OneBot（通用协议）

```bash
ONEBOT_ENABLED=true
ONEBOT_WS_URL=ws://127.0.0.1:8080
ONEBOT_ACCESS_TOKEN=              # 可选
```

## 配置文件

你也可以使用 YAML 配置文件，位于 `config/agent.yaml`：

```yaml
# Agent 设置
agent:
  name: OpenAkita
  max_iterations: 100
  auto_confirm: false

# 模型设置
model:
  provider: anthropic
  name: claude-sonnet-4-20250514
  max_tokens: 8192

# 工具配置
tools:
  shell:
    enabled: true
    timeout: 30
    blocked_commands:
      - rm -rf /
      - format
  file:
    enabled: true
    allowed_paths:
      - ./
      - /tmp
  web:
    enabled: true
    timeout: 30

# 日志
logging:
  level: INFO
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file: logs/agent.log
```

## 身份文件配置

### SOUL.md

灵魂文件定义核心价值观。一般不应修改：

```markdown
# Soul Overview

OpenAkita is a self-evolving AI assistant...

## Core Values
1. Safety and human oversight
2. Ethical behavior
3. Following guidelines
4. Being genuinely helpful
```

### AGENT.md

行为规范和工作流程：

```markdown
# Agent Behavior Specification

## Working Mode
- Ralph Wiggum Mode (never give up)
- Task execution flow
- Validation requirements
```

### USER.md

用户特定偏好（自动更新）：

```markdown
# User Profile

## Preferences
- Language: English
- Technical level: Advanced
- Preferred tools: Python, Git
```

### MEMORY.md

工作记忆（自动管理）：

```markdown
# Working Memory

## Current Task
- Description: ...
- Progress: 50%
- Next steps: ...

## Lessons Learned
- Issue X was solved by Y
```

## 命令行选项

```bash
# 覆盖配置文件
openakita --config /path/to/config.yaml

# 覆盖日志级别
openakita --log-level DEBUG

# 覆盖模型
openakita --model claude-opus-4-0-20250514

# 禁用确认提示
openakita --auto-confirm

# 运行特定模式
openakita --mode chat|task|test
```

## 高级配置

### 代理设置

适用于防火墙后的用户：

```bash
# HTTP 代理
HTTP_PROXY=http://proxy:8080
HTTPS_PROXY=http://proxy:8080

# 或使用自定义 API 端点
ANTHROPIC_BASE_URL=https://your-proxy-service.com
```

### 速率限制

```bash
# 每分钟请求数
RATE_LIMIT_RPM=60

# 每分钟 token 数
RATE_LIMIT_TPM=100000
```

### 资源限制

```bash
# 内存限制（MB）
MEMORY_LIMIT=2048

# CPU 核心数
CPU_LIMIT=4

# 磁盘空间（MB）
DISK_LIMIT=10240
```

## 验证配置

验证你的配置：

```bash
openakita config validate
```

显示当前配置：

```bash
openakita config show
```

## 最佳实践

1. **永远不要提交 `.env`** - 添加到 `.gitignore`
2. **为不同环境使用独立配置** - 开发/测试/生产环境分离
3. **定期轮换 API 密钥** - 提高安全性
4. **在生产环境启用日志** - 便于问题排查
5. **设置资源限制** - 防止失控进程
