# 安装部署

本页列出 OpenAkita 的所有安装方式、可选依赖和首次运行流程。选择最适合你的方式开始。

---

## 安装方式

### 方式一：PyPI 安装（推荐）

最简单的方式，适合大多数用户：

```bash
# 基础安装
pip install openakita

# 安装全部功能（推荐）
pip install "openakita[all]"
```

::: tip 建议使用虚拟环境
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install "openakita[all]"
```
:::

### 方式二：源码安装（开发者）

适合需要修改代码或参与贡献的开发者：

```bash
git clone https://github.com/openakita/openakita.git
cd openakita
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"
```

安装完成后可直接使用 `openakita` 命令，且代码修改即时生效。

### 方式三：一键脚本

**Linux / macOS：**

```bash
curl -fsSL https://get.openakita.com | bash
```

**Windows（PowerShell，以管理员身份运行）：**

```powershell
irm https://get.openakita.com/install.ps1 | iex
```

脚本自动完成：检查 Python 版本 → 创建虚拟环境 → 安装 OpenAkita → 运行初始化向导。

### 方式四：桌面应用（Tauri）

前往 [GitHub Releases](https://github.com/openakita/openakita/releases) 下载对应平台的安装包：

| 平台 | 文件格式 |
|------|----------|
| Windows | `.msi` / `.exe` |
| macOS | `.dmg` |
| Linux | `.AppImage` / `.deb` |

下载后双击安装，启动即可使用，无需配置 Python 环境。

---

## 可选依赖

通过 extras 安装特定 IM 通道或功能模块：

| 安装标记 | 说明 |
|----------|------|
| `openakita[feishu]` | 飞书 / Lark 通道 |
| `openakita[dingtalk]` | 钉钉通道 |
| `openakita[wework]` | 企业微信通道 |
| `openakita[onebot]` | OneBot 协议（可接各类 QQ 框架） |
| `openakita[qqbot]` | QQ 官方机器人 |
| `openakita[windows]` | Windows 桌面自动化 |
| `openakita[all]` | 以上全部 |

可组合使用：

```bash
pip install "openakita[feishu,dingtalk]"
```

---

## 验证安装

安装完成后运行自检命令：

```bash
openakita selfcheck
```

自检将验证：
- Python 版本是否满足要求
- 核心依赖是否完整
- 可选依赖的安装状态
- 配置文件是否存在

全部通过后你会看到 `✓ All checks passed`。

---

## 首次运行

### 初始化

```bash
openakita init
```

向导将引导你完成基础配置：

1. **LLM 提供商** — 选择 Anthropic / OpenAI / DashScope 等，填入 API Key
2. **Agent 名称** — 给你的 AI 助手起个名字
3. **语言偏好** — 选择默认交互语言

也可以跳过向导，之后在图形界面中配置 👉 [打开配置向导](/web/#/config/llm)

### 启动

```bash
openakita
```

---

## 启动模式

| 模式 | 命令 | 说明 |
|------|------|------|
| 交互式 CLI | `openakita` | 在终端中持续对话，适合日常使用 |
| 单次任务 | `openakita run "任务描述"` | 执行一个任务后自动退出 |
| API 服务 | `openakita serve` | 启动 FastAPI 服务，提供 Web 界面和 REST API |
| 桌面应用 | 启动 Tauri 应用 | 原生桌面体验，内置 Web 界面 |

👉 [打开聊天](/web/#/chat) 在 Web 界面中开始对话

---

## 配置入口

安装并启动后，可通过图形界面进行进一步配置：

- [LLM 端点配置](/web/#/config/llm) — 添加、切换或测试大模型 API
- [消息通道](/web/#/im) — 接入飞书、钉钉、Telegram 等 IM
- [技能管理](/web/#/skills) — 浏览和启用技能
- [高级设置](/web/#/config/advanced) — 代理、端口、日志等高级选项

---

## 常见问题

**Q：需要 GPU 吗？**
不需要。OpenAkita 调用云端 LLM API，本地只需普通 CPU 即可运行。

**Q：支持哪些操作系统？**
Windows 10+、macOS 12+、主流 Linux 发行版（Ubuntu 20.04+、Debian 11+ 等）。

**Q：`pip install` 失败怎么办？**
确认 Python ≥ 3.11，尝试 `pip install --upgrade pip` 后重试。国内用户可使用镜像源：
```bash
pip install openakita -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 相关页面

- [快速开始](/guide/quickstart) — 3 分钟上手教程
- [产品介绍](/guide/intro) — 功能概览与核心概念
- [CLI 命令参考](/advanced/cli) — 全部命令行选项
- [配置向导详解](/advanced/wizard) — 配置项完整说明
- [生产部署](/network/production) — 服务器部署与反向代理配置
