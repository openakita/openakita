# CLI 命令参考

## 概述

OpenAkita 提供 `openakita` 命令行工具，是与系统交互的另一种方式。适合无桌面环境的服务器部署、脚本自动化，或者你就是喜欢终端。

## 子命令一览

| 命令 | 说明 | 示例 |
|------|------|------|
| `openakita` | 启动交互式 CLI + IM 通道 | `openakita` |
| `openakita init [dir]` | 初始化配置向导 | `openakita init ~/my-workspace` |
| `openakita run "task"` | 执行单个任务后退出 | `openakita run "总结今天的邮件"` |
| `openakita selfcheck` | 运行系统自检 | `openakita selfcheck` |
| `openakita status` | 查看 Agent 运行状态 | `openakita status` |
| `openakita compile` | 编译 Identity 文件 | `openakita compile` |
| `openakita prompt-debug` | 输出当前组装的完整 Prompt | `openakita prompt-debug` |
| `openakita serve` | 仅启动 API 服务（IM + HTTP） | `openakita serve` |

### openakita（交互模式）

最常用的启动方式。进入交互式对话的同时，自动启动已配置的 IM 通道：

```bash
openakita
```

启动后在终端中直接输入消息与 Agent 对话，同时 Telegram/飞书等通道也在监听。

### openakita init

初始化工作区并运行配置向导：

```bash
openakita init              # 在当前目录初始化
openakita init ~/workspace  # 在指定目录初始化
```

向导会依次引导你完成 LLM 端点、IM 通道等配置，等同于 Web 界面的 [配置向导](/web/#/config/llm)。

### openakita run

执行单个任务，完成后自动退出，适合脚本调用：

```bash
openakita run "帮我把 report.md 翻译成英文"
openakita run "分析 data.csv 中的销售趋势"
```

### openakita selfcheck

运行自检，诊断常见问题：

```bash
openakita selfcheck
```

检查项包括：LLM 连接、IM 通道状态、依赖完整性、Identity 文件有效性。

### openakita status

查看当前运行状态的快照：

```bash
openakita status
```

输出包括：活跃的 Agent 数量、已连接的 IM 通道、内存用量、任务队列状态。

### openakita compile

手动编译 Identity 文件（`identity/` 目录下的 SOUL.md、AGENT.md 等）到 `identity/runtime/`：

```bash
openakita compile
```

::: tip 提示
通常不需要手动执行。`prompt/builder.py` 会自动检测 Identity 文件是否过期并触发编译。修改 Identity 后若未自动生效，可手动执行此命令。
:::

### openakita prompt-debug

输出当前完整的系统 Prompt（编译后），用于调试：

```bash
openakita prompt-debug
```

输出内容为 PromptBuilder 组装的最终 Prompt，包含所有层级：Identity → Persona → Runtime → Session Rules → Catalogs → Memory。

### openakita serve

启动 HTTP API 服务和 IM 通道，不进入交互式 CLI：

```bash
openakita serve
```

适用于后台服务部署。Web 界面通过 `http://localhost:18900` 访问。

## 交互式命令

在交互模式（`openakita`）中，以 `/` 开头输入命令：

### 通用命令

| 命令 | 说明 |
|------|------|
| `/help` | 查看所有可用命令和说明 |
| `/status` | 查看系统运行状态 |
| `/selfcheck` | 运行自检诊断 |
| `/memory` | 查看和管理 Agent 记忆 |
| `/skills` | 列出已启用的技能 |
| `/channels` | 查看 IM 通道连接状态 |
| `/agents` | 列出当前 Agent 及子 Agent |
| `/clear` | 清空当前会话历史 |
| `/exit` | 退出交互模式 |

### 模型切换命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/model` | 查看当前使用的模型与端点 | `/model` |
| `/switch <model>` | 临时切换到指定模型 | `/switch claude-sonnet-4-20250514` |
| `/priority` | 查看或调整端点优先级 | `/priority` |
| `/restore` | 恢复默认模型配置 | `/restore` |

::: tip 作用范围
`/switch` 仅对当前会话生效。永久修改请前往 [LLM 端点配置](/web/#/config/llm)。
:::

## 相关页面

- [聊天对话](/features/chat) — Web 界面的对话功能与命令
- [配置向导详解](/advanced/wizard) — 完整配置字段说明
- [高级设置](/advanced/advanced) — 网络与系统配置
- [生产部署](/network/production) — 服务器部署指南
