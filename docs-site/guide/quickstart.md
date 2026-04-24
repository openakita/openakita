# 快速开始

本页帮你在 3 分钟内跑起 OpenAkita，完成第一次对话并接入 IM 通道。

---

## 前置条件

| 条件 | 说明 |
|------|------|
| **Python** | 3.11 或更高版本（[下载](https://www.python.org/downloads/)） |
| **LLM API Key** | 至少一个：Anthropic Claude、OpenAI、通义千问（DashScope）、DeepSeek 等均可 |
| **网络** | 需要访问对应 LLM API 端点 |

::: tip 不确定 Python 版本？
运行 `python --version` 或 `python3 --version` 查看。
:::

---

## 三步启动

### 第 1 步：安装

```bash
pip install openakita
```

如果需要全部功能（飞书、钉钉、企业微信等 IM 通道 + 桌面自动化）：

```bash
pip install "openakita[all]"
```

详细安装选项见 [安装部署](/guide/installation)。

### 第 2 步：初始化

```bash
openakita init
```

交互式向导将引导你完成：
1. 选择 LLM 提供商并填入 API Key
2. 设置 Agent 名称
3. 选择语言偏好

也可以在图形界面中完成配置 👉 [打开配置向导](/web/#/config/llm)

### 第 3 步：启动

```bash
openakita
```

看到欢迎消息后，直接输入你的第一句话：

```
你 > 你好，介绍一下你自己
OpenAkita > 你好！我是 OpenAkita，一个多 Agent AI 助手。我可以帮你处理各种任务……
```

🎉 恭喜！OpenAkita 已经在运行了。

---

## 第一次对话示例

试试这些指令感受 OpenAkita 的能力：

```
你 > 帮我总结这篇文章：https://example.com/article
你 > 用 Python 写一个快速排序并保存到 sort.py
你 > 明天下午 3 点提醒我开会
```

👉 [打开聊天](/web/#/chat) 在 Web 界面中对话

---

## 接入第一个 IM 通道

推荐从 **飞书** 开始——扫码即可接入，无需申请开发者权限。

1. 在 OpenAkita 中打开 [消息通道配置](/web/#/im)
2. 选择"飞书"，按提示扫码授权
3. 在飞书中找到 OpenAkita 机器人，发送消息测试

其他通道（钉钉、Telegram、企业微信、QQ）的配置步骤见 [消息通道（IM）](/features/im-channels)。

---

## 图形界面快速配置

除了 CLI，你也可以通过桌面应用或 Web 浏览器进行配置：

- [LLM 端点配置](/web/#/config/llm) — 添加或切换大模型 API
- [消息通道](/web/#/im) — 接入 IM 通道
- [技能管理](/web/#/skills) — 启用或安装技能
- [Agent 管理](/web/#/agents) — 创建和管理多个 Agent

启动 Web 服务：

```bash
openakita serve
# 浏览器打开 http://localhost:8000
```

---

## 一键安装脚本

如果你更喜欢一键部署：

**Linux / macOS：**

```bash
curl -fsSL https://get.openakita.com | bash
```

**Windows（PowerShell）：**

```powershell
irm https://get.openakita.com/install.ps1 | iex
```

脚本将自动检查 Python 环境、安装 OpenAkita 并运行 `openakita init`。

---

## 其他启动方式

| 方式 | 命令 | 说明 |
|------|------|------|
| 交互式 CLI | `openakita` | 默认模式，在终端对话 |
| 单次任务 | `openakita run "任务描述"` | 执行一个任务后退出 |
| API 服务 | `openakita serve` | 启动 Web 服务 + API |
| 桌面应用 | 双击启动 | Tauri 原生桌面应用 |

---

## 接下来

- [安装部署](/guide/installation) — 更多安装方式与可选依赖
- [聊天对话](/features/chat) — 深入了解对话功能
- [消息通道（IM）](/features/im-channels) — 接入更多聊天软件
- [技能管理](/features/skills) — 扩展 Agent 能力
- [多 Agent 入门](/multi-agent/overview) — 让多个 Agent 协同工作
- [身份配置](/features/identity) — 自定义你的 Agent 性格

