# 快速入门

本指南将帮助你快速上手 OpenAkita。

## 前置要求

开始之前，请确保你有：

- **Python 3.11+** 已安装
- **Anthropic API 密钥**（[在此获取](https://console.anthropic.com/)）
- **Git** 用于克隆仓库

## 安装

### 方式一：从 PyPI 安装（推荐）

```bash
# 创建并激活虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 .\venv\Scripts\activate  # Windows

# 安装 OpenAkita（核心版）
pip install openakita

# 可选功能
pip install "openakita[all]"      # 安装所有可选功能
# pip install "openakita[windows]"  # Windows 桌面自动化
# pip install "openakita[feishu]"   # 飞书 IM 接入

# 运行配置向导
openakita init
```

### 方式二：一键安装脚本（PyPI）

Linux/macOS：

```bash
curl -fsSL https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.sh | bash
```

Windows (PowerShell)：

```powershell
irm https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.ps1 | iex
```

要安装额外功能或使用镜像，请下载后带参数运行（推荐）：

```bash
curl -fsSL -o quickstart.sh https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.sh
bash quickstart.sh --extras all --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

```powershell
irm https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.ps1 -OutFile quickstart.ps1
.\quickstart.ps1 -Extras all -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
```

### 方式三：从源码安装（开发）

```bash
git clone https://github.com/openakita/openakita.git
cd openakita
python -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate
pip install -e ".[all,dev]"
openakita init
```

## 配置

### 1. 创建环境文件

```bash
cp examples/.env.example .env
```

### 2. 添加你的 API 密钥

编辑 `.env` 并设置你的 Anthropic API 密钥：

```bash
ANTHROPIC_API_KEY=sk-your-api-key-here
```

### 3. 可选设置

```bash
# 自定义 API 端点（适用于代理）
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 模型选择
DEFAULT_MODEL=claude-sonnet-4-20250514

# Agent 行为
MAX_ITERATIONS=100
AUTO_CONFIRM=false
```

## 第一次运行

### 启动 CLI

```bash
openakita
```

你应该看到：

```
╭─────────────────────────────────────────╮
│           OpenAkita v0.5.9              │
│   A Self-Evolving AI Agent              │
╰─────────────────────────────────────────╯

你> 
```

### 尝试简单任务

```
你> 你好，你能做什么？
```

OpenAkita 会介绍自己并解释其功能。

### 尝试复杂任务

```
你> 创建一个计算 100 以内质数的 Python 脚本
```

观察 OpenAkita 如何：
1. 分析任务
2. 编写代码
3. 测试代码
4. 报告结果

## 常用命令

| 命令 | 说明 |
|------|------|
| `openakita` | 启动交互模式 |
| `openakita run "任务描述"` | 执行单个任务 |
| `openakita status` | 显示 Agent 状态 |
| `openakita selfcheck` | 运行自诊断 |
| `openakita --help` | 显示所有命令 |

## 下一步

- [架构概览](architecture.md) - 了解 OpenAkita 的工作原理
- [配置指南](configuration.md) - 所有配置选项
- [技能系统](skills.md) - 创建自定义技能
- [IM 通道](im-channels.md) - 设置 Telegram 等接入

## 常见问题

### "找不到 API 密钥"

确保你的 `.env` 文件存在并包含 `ANTHROPIC_API_KEY`。

### "连接超时"

检查你的网络连接。如果在中国，考虑使用代理：

```bash
ANTHROPIC_BASE_URL=https://your-proxy-url
```

### "Python 版本错误"

OpenAkita 需要 Python 3.11+。检查你的版本：

```bash
python --version
```

### 需要更多帮助？

- 查看 [GitHub Issues](https://github.com/openakita/openakita/issues)
- 加入 [GitHub Discussions](https://github.com/openakita/openakita/discussions)
