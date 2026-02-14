# OpenAkita 部署文档 (中文版)

[English Version](./deploy_en.md)

> 完整的从零开始部署指南，涵盖 PyPI 安装、源码安装、大模型配置、IM 通道接入

## 目录

- [系统要求](#系统要求)
- [安装方式](#安装方式)
  - [方式一：PyPI 安装（推荐）](#方式一pypi-安装推荐)
  - [方式二：一键部署脚本](#方式二一键部署脚本)
  - [方式三：源码安装](#方式三源码安装)
- [配置说明](#配置说明)
  - [核心配置文件概览](#核心配置文件概览)
  - [环境变量配置 (.env)](#环境变量配置-env)
  - [大模型端点配置 (llm_endpoints.json)](#大模型端点配置-llm_endpointsjson)
  - [IM 通道配置](#im-通道配置)
  - [身份配置 (identity/)](#身份配置-identity)
  - [记忆系统配置](#记忆系统配置)
  - [多 Agent 协同配置](#多-agent-协同配置)
- [启动服务](#启动服务)
- [PyPI 发布](#pypi-发布)
- [生产部署](#生产部署)
- [常见问题](#常见问题)
- [更新与卸载](#更新与卸载)

---

## 系统要求

### 硬件要求

| 项目 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 2 核 | 4 核+ |
| 内存 | 2 GB | 4 GB+ |
| 磁盘 | 5 GB | 20 GB+ |
| 网络 | 能访问 API 端点 | 稳定低延迟网络 |

### 软件要求

| 软件 | 版本要求 | 用途 |
|------|---------|------|
| **Python** | >= 3.11 | 运行环境 |
| **pip** | >= 23.0 | 包管理 |
| **Git** | >= 2.30 | 版本控制 & GitPython |
| **Node.js** | >= 18 (可选) | MCP 服务器 |

### 操作系统支持

- ✅ Windows 10/11
- ✅ Ubuntu 20.04/22.04/24.04
- ✅ Debian 11/12
- ✅ CentOS 8/9 Stream
- ✅ macOS 12+

---

## 安装方式

### 方式一：PyPI 安装（推荐）

最简单的安装方式，适合快速上手：

```bash
# 1. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 .\venv\Scripts\activate  # Windows

# 2. 安装 OpenAkita（核心版）
pip install openakita

# 3. 安装可选功能
pip install openakita[feishu]     # + 飞书支持
pip install openakita[whisper]    # + 语音识别
pip install openakita[browser]    # + 浏览器 AI 代理
pip install openakita[windows]    # + Windows 桌面自动化
pip install openakita[all]        # 安装所有可选功能（跨平台安全，Windows-only 依赖会自动跳过）

# 4. 运行初始化向导
openakita init

# 5. 启动
openakita
```

### 方式二：一键部署脚本

如果你希望“零手动操作”快速跑起来，有两种脚本路径：

- **一键安装（PyPI）**：适合只想装好并运行（推荐）
- **一键部署（源码）**：适合需要从源码开发/修改

#### 方式二-A：一键安装（PyPI，推荐）

**Linux/macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.ps1 | iex
```

如需安装 extras / 使用镜像，建议先下载脚本再带参数运行：

```bash
curl -fsSL -o quickstart.sh https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.sh
bash quickstart.sh --extras all --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

```powershell
irm https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.ps1 -OutFile quickstart.ps1
.\quickstart.ps1 -Extras all -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
```

> 说明：脚本会把工作目录默认放在 `~/.openakita/app`（Windows：`%USERPROFILE%\.openakita\app`），
> 并创建独立虚拟环境 `~/.openakita/venv`，避免污染系统 Python。

#### 方式二-B：一键部署（源码）

自动安装 Python、Git、依赖等全部环境（需要先 `git clone` 仓库）：

**Linux/macOS:**
```bash
git clone https://github.com/openakita/openakita.git
cd openakita
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/openakita/openakita.git
cd openakita
.\scripts\deploy.ps1
```

脚本会自动完成：
1. 检测并安装 Python 3.11+
2. 检测并安装 Git
3. 创建虚拟环境
4. 安装项目依赖（失败自动切换国内镜像）
5. 可选安装 Playwright 浏览器
6. 可选下载 Whisper 语音模型
7. 初始化 `.env` 和 `data/llm_endpoints.json`
8. 创建所有必要数据目录
9. 验证安装
10. 可选创建 systemd 服务（Linux）

### 方式三：源码安装

```bash
# 1. 克隆项目
git clone https://github.com/openakita/openakita.git
cd openakita

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 .\venv\Scripts\activate  # Windows

# 3. 升级 pip
pip install --upgrade pip

# 4. 安装项目（开发模式）
pip install -e ".[all,dev]"

# 5. 安装 Playwright 浏览器（可选）
playwright install chromium

# 6. 复制配置文件
cp examples/.env.example .env
cp data/llm_endpoints.json.example data/llm_endpoints.json

# 7. 编辑配置
# 编辑 .env 填入 API Key 和 IM 通道配置
# 编辑 data/llm_endpoints.json 配置 LLM 端点

# 8. 运行初始化向导（或手动配置）
openakita init

# 9. 启动
openakita
```

---

## 配置说明

### 核心配置文件概览

```
项目根目录/
├── .env                          # 环境变量（API Key、IM Token 等敏感信息）
├── data/
│   └── llm_endpoints.json        # LLM 多端点配置（模型、优先级、能力路由）
└── identity/
    ├── SOUL.md                   # Agent 核心人格
    ├── AGENT.md                  # Agent 行为规范
    ├── USER.md                   # 用户画像（自动学习）
    └── MEMORY.md                 # 核心记忆（自动更新）
```

**配置优先级：** 环境变量 > `.env` 文件 > 代码默认值

### 环境变量配置 (.env)

复制示例文件并编辑：

```bash
cp examples/.env.example .env
```

#### 必需配置

```ini
# 至少需要一个 LLM API Key
ANTHROPIC_API_KEY=sk-your-api-key-here
```

> **提示：** 如果不用 Anthropic，也可以只配置其他 API Key（如 `DASHSCOPE_API_KEY`），
> 只要在 `data/llm_endpoints.json` 中正确引用即可。

#### 完整环境变量列表

| 变量名 | 必需 | 默认值 | 说明 |
|--------|------|--------|------|
| **LLM 配置** | | | |
| `ANTHROPIC_API_KEY` | ⚡ | - | Anthropic Claude API Key |
| `ANTHROPIC_BASE_URL` | | `https://api.anthropic.com` | API 端点（支持代理） |
| `DEFAULT_MODEL` | | `claude-opus-4-5-20251101-thinking` | 默认模型 |
| `MAX_TOKENS` | | `8192` | 最大输出 token |
| `KIMI_API_KEY` | | - | Kimi API Key |
| `DASHSCOPE_API_KEY` | | - | 通义千问 API Key |
| `MINIMAX_API_KEY` | | - | MiniMax API Key |
| `DEEPSEEK_API_KEY` | | - | DeepSeek API Key |
| `OPENROUTER_API_KEY` | | - | OpenRouter API Key |
| `SILICONFLOW_API_KEY` | | - | SiliconFlow API Key |
| `LLM_ENDPOINTS_CONFIG` | | `data/llm_endpoints.json` | LLM 端点配置文件路径 |
| **Agent 配置** | | | |
| `AGENT_NAME` | | `OpenAkita` | Agent 名称 |
| `MAX_ITERATIONS` | | `100` | Ralph 循环最大迭代 |
| `AUTO_CONFIRM` | | `false` | 自动确认危险操作 |
| `DATABASE_PATH` | | `data/agent.db` | 数据库路径 |
| `LOG_LEVEL` | | `INFO` | 日志级别 |
| **网络代理** | | | |
| `HTTP_PROXY` | | - | HTTP 代理 |
| `HTTPS_PROXY` | | - | HTTPS 代理 |
| `ALL_PROXY` | | - | 全局代理（优先级最高） |
| `FORCE_IPV4` | | `false` | 强制 IPv4 |
| **IM 通道** | | | |
| `TELEGRAM_ENABLED` | | `false` | 启用 Telegram |
| `TELEGRAM_BOT_TOKEN` | | - | Telegram Bot Token |
| `TELEGRAM_PROXY` | | - | Telegram 专用代理 |
| `FEISHU_ENABLED` | | `false` | 启用飞书 |
| `FEISHU_APP_ID` | | - | 飞书 App ID |
| `FEISHU_APP_SECRET` | | - | 飞书 App Secret |
| `WEWORK_ENABLED` | | `false` | 启用企业微信 |
| `WEWORK_CORP_ID` | | - | 企业 ID |
| `WEWORK_AGENT_ID` | | - | 应用 Agent ID |
| `WEWORK_SECRET` | | - | 应用 Secret |
| `DINGTALK_ENABLED` | | `false` | 启用钉钉 |
| `DINGTALK_CLIENT_ID` | | - | 钉钉 Client ID（原 App Key） |
| `DINGTALK_CLIENT_SECRET` | | - | 钉钉 Client Secret（原 App Secret） |
| `QQBOT_ENABLED` | | `false` | 启用 QQ 官方机器人 |
| `QQBOT_APP_ID` | | - | QQ 开放平台 AppID |
| `QQBOT_APP_SECRET` | | - | QQ 开放平台 AppSecret |
| `QQBOT_SANDBOX` | | `false` | 沙箱模式 |
| `ONEBOT_ENABLED` | | `false` | 启用 OneBot |
| `ONEBOT_WS_URL` | | `ws://127.0.0.1:8080` | OneBot WebSocket URL |
| `ONEBOT_ACCESS_TOKEN` | | - | OneBot 访问令牌（可选） |
| **记忆系统** | | | |
| `EMBEDDING_MODEL` | | `shibing624/text2vec-base-chinese` | Embedding 模型 |
| `EMBEDDING_DEVICE` | | `cpu` | 计算设备（cpu/cuda） |
| `MEMORY_HISTORY_DAYS` | | `30` | 历史保留天数 |
| **语音识别** | | | |
| `WHISPER_MODEL` | | `base` | Whisper 模型大小 |
| **GitHub** | | | |
| `GITHUB_TOKEN` | | - | 用于搜索/下载技能 |

### 大模型端点配置 (llm_endpoints.json)

这是 OpenAkita 的**核心配置文件**，支持多端点、自动故障切换、能力路由。

#### 配置方式

**方式 A：交互式向导（推荐）**
```bash
python -m openakita.llm.setup.cli
```

向导支持：
- 从已知供应商列表选择
- 自动获取可用模型列表
- 测试端点连通性
- 设置优先级
- 保存配置

**方式 B：手动编辑**
```bash
cp data/llm_endpoints.json.example data/llm_endpoints.json
# 然后编辑此文件
```

#### 配置结构

```json
{
  "endpoints": [
    {
      "name": "claude-primary",          // 端点名称（唯一标识）
      "provider": "anthropic",           // 供应商标识
      "api_type": "anthropic",           // API 协议: anthropic 或 openai
      "base_url": "https://api.anthropic.com",  // API 基地址
      "api_key_env": "ANTHROPIC_API_KEY",       // API Key 环境变量名
      "model": "claude-opus-4-5-20251101-thinking",
      "priority": 1,                     // 优先级（1=最高）
      "max_tokens": 8192,               // 最大输出 token
      "timeout": 60,                     // 超时（秒）
      "capabilities": ["text", "vision", "tools"],  // 能力声明
      "extra_params": {},                // 传给 API 的额外参数
      "note": "Anthropic 官方 API"       // 备注
    }
  ],
  "settings": {
    "retry_count": 2,                    // 单端点重试次数
    "retry_delay_seconds": 2,            // 重试间隔（秒）
    "health_check_interval": 60,         // 健康检查间隔（秒）
    "fallback_on_error": true            // 失败自动切换备用端点
  }
}
```

#### 字段详解

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 端点唯一名称 |
| `provider` | string | ✅ | 供应商：`anthropic` / `openai` / `dashscope` / `moonshot` / `minimax` / `deepseek` / `zhipu` / `openrouter` / `siliconflow` |
| `api_type` | string | ✅ | API 协议：`anthropic`（Anthropic 原生格式）或 `openai`（OpenAI 兼容格式） |
| `base_url` | string | ✅ | API 基地址 |
| `api_key_env` | string | ✅ | API Key 对应的环境变量名（在 `.env` 中设置实际值） |
| `model` | string | ✅ | 模型名称 |
| `priority` | int | ✅ | 优先级，数字越小越优先 |
| `max_tokens` | int | | 最大输出 token，默认 8192 |
| `timeout` | int | | 请求超时秒数，默认 60 |
| `capabilities` | list | | 能力列表：`text` / `vision` / `video` / `tools` / `thinking` |
| `extra_params` | dict | | 传给 API 的额外参数 |
| `note` | string | | 备注说明 |

#### 能力路由说明

| 能力 | 说明 | 典型模型 |
|------|------|---------|
| `text` | 文本对话 | 所有模型 |
| `vision` | 图像理解 | Claude 3.5+, GPT-4V, Qwen-VL |
| `video` | 视频理解 | Kimi, Gemini |
| `tools` | 工具调用/函数调用 | Claude 3+, GPT-4+, Qwen |
| `thinking` | 深度推理 | O1, DeepSeek-R1, QwQ, Claude Thinking |

当用户发送图片时，系统自动选择有 `vision` 能力的端点；发送视频时，选择有 `video` 能力的端点。

#### 故障切换机制

1. 按 `priority` 从小到大尝试端点
2. 单端点失败后自动切换下一个
3. 失败端点进入 **3 分钟冷静期**，期间不再使用
4. 冷静期结束后自动恢复

#### 各供应商配置示例

**Anthropic（Claude 系列）**
```json
{
  "name": "claude",
  "provider": "anthropic",
  "api_type": "anthropic",
  "base_url": "https://api.anthropic.com",
  "api_key_env": "ANTHROPIC_API_KEY",
  "model": "claude-sonnet-4-20250514",
  "priority": 1,
  "capabilities": ["text", "vision", "tools"]
}
```

**通义千问（DashScope）**
```json
{
  "name": "qwen",
  "provider": "dashscope",
  "api_type": "openai",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key_env": "DASHSCOPE_API_KEY",
  "model": "qwen3-max",
  "priority": 2,
  "capabilities": ["text", "tools", "thinking"],
  "extra_params": {"enable_thinking": true}
}
```

**Kimi（月之暗面）**
```json
{
  "name": "kimi",
  "provider": "moonshot",
  "api_type": "openai",
  "base_url": "https://api.moonshot.cn/v1",
  "api_key_env": "KIMI_API_KEY",
  "model": "kimi-k2.5",
  "priority": 3,
  "capabilities": ["text", "vision", "video", "tools"],
  "extra_params": {"thinking": {"type": "enabled"}}
}
```

**DeepSeek**
```json
{
  "name": "deepseek",
  "provider": "deepseek",
  "api_type": "openai",
  "base_url": "https://api.deepseek.com/v1",
  "api_key_env": "DEEPSEEK_API_KEY",
  "model": "deepseek-chat",
  "priority": 4,
  "capabilities": ["text", "tools"]
}
```

**OpenRouter（聚合多家模型）**
```json
{
  "name": "openrouter-gemini",
  "provider": "openrouter",
  "api_type": "openai",
  "base_url": "https://openrouter.ai/api/v1",
  "api_key_env": "OPENROUTER_API_KEY",
  "model": "google/gemini-2.5-pro",
  "priority": 5,
  "capabilities": ["text", "vision", "video", "tools"]
}
```

**MiniMax（Anthropic 协议）**
```json
{
  "name": "minimax",
  "provider": "minimax",
  "api_type": "anthropic",
  "base_url": "https://api.minimaxi.com/anthropic",
  "api_key_env": "MINIMAX_API_KEY",
  "model": "MiniMax-M2.1",
  "priority": 6,
  "capabilities": ["text", "tools"]
}
```

**使用代理/转发服务**

如果直连 Anthropic 有困难，可以使用转发服务：
```json
{
  "name": "claude-proxy",
  "provider": "anthropic",
  "api_type": "anthropic",
  "base_url": "https://your-proxy-domain.com",
  "api_key_env": "ANTHROPIC_API_KEY",
  "model": "claude-sonnet-4-20250514",
  "priority": 1,
  "capabilities": ["text", "vision", "tools"]
}
```

### IM 通道配置

OpenAkita 支持 5 大 IM 平台，统一通过 `.env` 启用：

| 平台 | 状态 | 协议 | 额外依赖 |
|------|------|------|---------|
| Telegram | ✅ 稳定 | Bot API | 已内置 |
| 飞书 | ✅ 稳定 | WebSocket | `pip install openakita[feishu]` |
| 企业微信 | ✅ 稳定 | HTTP API | 无 |
| 钉钉 | ✅ 稳定 | HTTP API | 无 |
| QQ 官方机器人 | ✅ 稳定 | QQ 开放平台 API | `pip install openakita[qqbot]` |
| OneBot | ✅ 稳定 | OneBot WS | 需 OneBot 服务 + `pip install openakita[onebot]` |

#### Telegram

1. 在 [@BotFather](https://t.me/BotFather) 创建 Bot，获取 Token
2. 配置 `.env`：
```ini
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
# 中国大陆用户必须配置代理
TELEGRAM_PROXY=http://127.0.0.1:7890
```
3. 首次使用时，Agent 会在 `data/telegram/pairing/` 生成配对码，控制台输出可见

#### 飞书

1. 在 [飞书开放平台](https://open.feishu.cn/) 创建应用
2. 启用机器人能力，添加消息相关权限
3. 配置 `.env`：
```ini
FEISHU_ENABLED=true
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
```
4. 飞书适配器默认使用 WebSocket 长连接（推荐），无需配置回调 URL

#### 企业微信

1. 在 [企业微信管理后台](https://work.weixin.qq.com/) 创建自建应用
2. 获取 Corp ID、Agent ID、Secret
3. 配置 `.env`：
```ini
WEWORK_ENABLED=true
WEWORK_CORP_ID=ww_xxxxx
WEWORK_AGENT_ID=1000002
WEWORK_SECRET=xxxxx
```

#### 钉钉

1. 在 [钉钉开放平台](https://open.dingtalk.com/) 创建企业内部应用
2. 启用机器人能力
3. 配置 `.env`：
```ini
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=dingxxxxx
DINGTALK_CLIENT_SECRET=xxxxx
```

#### QQ 官方机器人

在 [QQ 开放平台](https://q.qq.com) 创建机器人并获取凭证：
```ini
QQBOT_ENABLED=true
QQBOT_APP_ID=your-app-id
QQBOT_APP_SECRET=your-app-secret
QQBOT_SANDBOX=false
```

#### OneBot（通用协议）

需要先部署 OneBot 实现（如 [NapCat](https://github.com/NapNeko/NapCatQQ)）：
```ini
ONEBOT_ENABLED=true
ONEBOT_WS_URL=ws://127.0.0.1:8080
ONEBOT_ACCESS_TOKEN=
```

#### 启动方式

IM 通道有两种运行模式：

```bash
# 模式 1: CLI + IM（交互模式下同时运行 IM 通道）
openakita

# 模式 2: 纯 IM 服务（后台服务，不启动 CLI）
openakita serve
```

### 身份配置 (identity/)

身份文件定义 Agent 的人格、行为和记忆：

```bash
# 从示例文件创建
cp identity/SOUL.md.example identity/SOUL.md
cp identity/AGENT.md.example identity/AGENT.md
cp identity/USER.md.example identity/USER.md
cp identity/MEMORY.md.example identity/MEMORY.md
```

| 文件 | 说明 | 自动更新 |
|------|------|---------|
| `SOUL.md` | 核心人格和哲学 | ❌ 手动维护 |
| `AGENT.md` | 行为规范和工作流 | ❌ 手动维护 |
| `USER.md` | 用户画像 | ✅ Agent 自动学习 |
| `MEMORY.md` | 核心记忆 | ✅ 每日自动整理 |

> 运行 `openakita init` 会自动创建这些文件。

### 记忆系统配置

记忆系统使用向量搜索实现语义匹配：

```ini
# .env 中配置
EMBEDDING_MODEL=shibing624/text2vec-base-chinese  # 中文推荐
EMBEDDING_DEVICE=cpu                                # 有 GPU 可设为 cuda
```

**首次启动**会自动下载 Embedding 模型（约 100MB）。

**离线部署**可提前下载：
```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('shibing624/text2vec-base-chinese')"
```

**GPU 加速**（可选）：
```bash
pip install torch --index-url https://download.pytorch.org/whl/cu118
# .env 中设置 EMBEDDING_DEVICE=cuda
```

### 多 Agent 协同配置

启用 MasterAgent + Worker 架构处理复杂任务：

```ini
# .env 中配置
ORCHESTRATION_ENABLED=true
ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555
ORCHESTRATION_PUB_ADDRESS=tcp://127.0.0.1:5556
ORCHESTRATION_MIN_WORKERS=1
ORCHESTRATION_MAX_WORKERS=5
```

---

## 启动服务

### 交互模式（开发/测试）

```bash
openakita           # 交互式 CLI（同时运行 IM 通道）
python -m openakita # 同上
```

### 服务模式（生产部署）

```bash
openakita serve     # 纯 IM 服务，无 CLI 交互
```

### 单次任务

```bash
openakita run "帮我分析当前目录的代码结构"
```

### 其他命令

```bash
openakita init              # 运行配置向导
openakita status            # 显示 Agent 状态
openakita selfcheck         # 运行自检
openakita compile           # 编译 identity 文件（降低 token 消耗）
openakita prompt-debug      # 显示 prompt 调试信息
openakita --version         # 显示版本
```

---

## PyPI 发布

项目已配置好 PyPI 发布流程：

### 手动发布

```bash
# 1. 安装构建工具
pip install build twine

# 2. 构建包
python -m build

# 3. 检查包
twine check dist/*

# 4. 上传到 PyPI
twine upload dist/*
# 或上传到 TestPyPI
twine upload --repository testpypi dist/*
```

### 自动发布（GitHub Actions）

推送版本标签即可自动发布：

```bash
# 1. 更新 pyproject.toml 中的 version
# 2. 创建标签
git tag v1.2.2
git push origin v1.2.2
# 3. GitHub Actions 自动构建并发布到 PyPI
```

> 需要在 GitHub 仓库 Settings → Secrets 中配置 `PYPI_API_TOKEN`。

### 包安装验证

```bash
# 从 PyPI 安装
pip install openakita

# 验证
openakita --version
python -c "import openakita; print(openakita.__version__)"
```

---

## 生产部署

### 使用 systemd (Linux 推荐)

创建服务文件 `/etc/systemd/system/openakita.service`：

```ini
[Unit]
Description=OpenAkita AI Agent Service
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/openakita
Environment="PATH=/path/to/openakita/venv/bin"
ExecStart=/path/to/openakita/venv/bin/openakita serve
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable openakita
sudo systemctl start openakita
sudo systemctl status openakita

# 查看日志
journalctl -u openakita -f
```

### 使用 Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[feishu]"

# 复制项目文件
COPY . .

# 安装 Playwright
RUN playwright install chromium && playwright install-deps chromium

CMD ["openakita", "serve"]
```

```bash
docker build -t openakita .
docker run -d \
  --name openakita \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/identity:/app/identity \
  openakita
```

### 使用 nohup（简单后台运行）

```bash
source venv/bin/activate
nohup openakita serve > logs/serve.log 2>&1 &
echo $! > openakita.pid
```

---

## 常见问题

### Q: 如何选择大模型？

推荐配置策略（在 `data/llm_endpoints.json` 中）：
- **主端点**：Claude Sonnet/Opus（能力最全面）
- **备用 1**：通义千问 qwen3-max（国内访问快，支持推理）
- **备用 2**：Kimi k2.5（支持视频理解）
- **备用 3**：DeepSeek Chat（性价比高）

### Q: Python 版本不对？

```bash
python --version
# Windows: py -3.11 -m venv venv
# Linux: pyenv install 3.11.8 && pyenv local 3.11.8
```

### Q: pip 安装失败？

```bash
# 使用国内镜像
pip install openakita -i https://pypi.tuna.tsinghua.edu.cn/simple
# 或配置永久镜像
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: Playwright 安装失败？

```bash
# Linux 安装系统依赖
playwright install-deps
# 或只安装 Chromium
playwright install chromium
```

### Q: API 连接超时？

1. 检查网络是否能访问 API 端点
2. 配置代理：在 `.env` 设置 `ALL_PROXY`
3. 使用 API 转发服务：修改 `llm_endpoints.json` 中的 `base_url`

### Q: Telegram Bot 无法启动？

1. 检查 Token 是否正确
2. 中国大陆必须配置 `TELEGRAM_PROXY`
3. 确认代理能访问 `api.telegram.org`

### Q: 内存不足？

```bash
# 使用 CPU-only PyTorch（节省约 2GB）
pip install torch --index-url https://download.pytorch.org/whl/cpu
# 选择更小的 Embedding 模型
# EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

### Q: 如何验证 LLM 端点配置正确？

```bash
# 使用交互式工具测试
python -m openakita.llm.setup.cli
# 选择 "4. 测试端点" 即可验证连通性
```

### Q: macOS 安装后提示"已损坏，无法打开"？

这是因为桌面端安装包尚未进行 Apple 签名和公证（Notarization），macOS Gatekeeper 会阻止未签名的应用运行。

**解决方法：** 在终端执行以下命令移除隔离属性：

```bash
# 1. 移除 DMG 文件的隔离属性（下载后、打开前执行）
xattr -cr ~/Downloads/OpenAkita.Desktop_*_aarch64.dmg

# 2. 将 app 拖入 Applications 后，对 app 再执行一次
xattr -cr /Applications/OpenAkita\ Desktop.app
```

> **说明：** 两条命令都执行最保险，核心是第 2 条。执行后即可正常打开应用。
> 如果仍无法打开，可前往「系统设置 → 隐私与安全性」，在页面底部点击"仍要打开"。

---

## 更新与卸载

### 更新

```bash
# PyPI 安装
pip install --upgrade openakita

# 源码安装
cd openakita
git pull
pip install -e ".[all]"
```

### 卸载

```bash
# 停止服务
sudo systemctl stop openakita
sudo systemctl disable openakita
sudo rm /etc/systemd/system/openakita.service

# 卸载包
pip uninstall openakita

# 删除数据（慎重）
rm -rf data/ identity/ logs/
```

---

## 技术支持

- 文档：查看 `docs/` 目录下的详细文档
- 问题：提交 [GitHub Issue](https://github.com/openakita/openakita/issues)
- 社区：加入 Telegram 群组

---

*最后更新: 2026-02-06*
