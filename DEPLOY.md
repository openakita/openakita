# OpenAkita 部署文档

> 完整的从零开始部署指南

## 📋 目录

- [系统要求](#系统要求)
- [依赖清单](#依赖清单)
- [快速部署](#快速部署)
- [手动部署步骤](#手动部署步骤)
- [配置说明](#配置说明)
- [启动服务](#启动服务)
- [常见问题](#常见问题)

---

## 系统要求

### 硬件要求

| 项目 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 2 核 | 4 核+ |
| 内存 | 2 GB | 4 GB+ |
| 磁盘 | 5 GB | 20 GB+ |
| 网络 | 能访问 API 端点 | 稳定网络 |

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

## 依赖清单

### Python 第三方依赖

```
# 核心 LLM
anthropic>=0.40.0          # Claude API
openai>=1.0.0              # OpenAI 兼容端点

# MCP 协议
mcp>=1.0.0

# CLI/UI
rich>=13.7.0
prompt-toolkit>=3.0.43
typer>=0.12.0

# 异步 HTTP
httpx>=0.27.0
aiofiles>=24.1.0

# 数据库
aiosqlite>=0.20.0

# 数据验证
pydantic>=2.5.0
pydantic-settings>=2.1.0

# Git 操作
gitpython>=3.1.40

# 浏览器自动化
playwright>=1.40.0

# 配置
pyyaml>=6.0.1
python-dotenv>=1.0.0

# 工具
tenacity>=8.2.3

# 记忆系统 - 向量搜索
sentence-transformers>=2.2.0  # 本地 embedding 模型
chromadb>=0.4.0               # 向量数据库

# IM 通道 (可选)
python-telegram-bot>=21.0  # Telegram
```

### 向量搜索配置

记忆系统使用向量搜索实现语义匹配，需要额外配置：

#### 首次启动

首次启动时会自动下载 embedding 模型（约 100MB），需要网络连接。

模型缓存位置：
- Windows: `C:\Users\<用户>\.cache\huggingface\`
- Linux/Mac: `~/.cache/huggingface/`

#### 预下载模型（可选）

如果需要在离线环境部署，可以提前下载模型：

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('shibing624/text2vec-base-chinese')"
```

#### GPU 加速（可选）

如果有 NVIDIA GPU，可以安装 CUDA 版本的 PyTorch 以加速向量计算：

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu118
```

在 `.env` 中设置：
```
EMBEDDING_DEVICE=cuda
```

#### 数据目录

向量索引存储在 `data/memory/chromadb/`，请确保该目录有写入权限。

### Python 标准库依赖 (内置)

这些是 Python 自带的，无需单独安装：

```
asyncio          # 异步编程
logging          # 日志系统
json             # JSON 处理
uuid             # UUID 生成
os               # 操作系统接口
sys              # 系统参数
subprocess       # 子进程管理
shutil           # 文件操作
re               # 正则表达式
pathlib          # 路径处理
datetime         # 日期时间
dataclasses      # 数据类
typing           # 类型提示
enum             # 枚举类型
abc              # 抽象基类
mimetypes        # MIME 类型
hashlib          # 哈希算法
hmac             # 消息认证码
base64           # Base64 编解码
time             # 时间函数
xml.etree        # XML 解析
argparse         # 命令行解析
```

### 系统工具依赖

| 工具 | 用途 | 安装方式 |
|------|------|---------|
| Git | 代码管理、GitPython | 系统包管理器 |
| 浏览器内核 | Playwright | `playwright install` |

---

## 快速部署

### 一键部署 (推荐)

**Windows (PowerShell):**
```powershell
# 下载并运行部署脚本
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/your-repo/openakita/main/deploy.ps1" -OutFile "deploy.ps1"
.\deploy.ps1
```

或者使用本地脚本：
```powershell
.\deploy.ps1
```

**Linux/macOS (Bash):**
```bash
# 下载并运行部署脚本
curl -O https://raw.githubusercontent.com/your-repo/openakita/main/deploy.sh
chmod +x deploy.sh
./deploy.sh
```

或者使用本地脚本：
```bash
chmod +x deploy.sh
./deploy.sh
```

---

## 手动部署步骤

### 1. 安装 Python 3.11+

#### Windows

**方法 A: 官网下载**
```powershell
# 1. 访问 https://www.python.org/downloads/
# 2. 下载 Python 3.11 或更高版本
# 3. 安装时勾选 "Add Python to PATH"
# 4. 验证安装
python --version  # 应显示 Python 3.11.x 或更高
```

**方法 B: winget 安装**
```powershell
winget install Python.Python.3.11
# 重启终端后验证
python --version
```

**方法 C: Scoop 安装**
```powershell
scoop install python
python --version
```

#### Linux (Ubuntu/Debian)

```bash
# 更新包列表
sudo apt update

# 安装 Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# 设置默认 Python (可选)
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# 验证
python3.11 --version
```

#### Linux (CentOS/RHEL)

```bash
# 启用 EPEL 和 CRB 仓库
sudo dnf install -y epel-release
sudo dnf config-manager --set-enabled crb

# 安装 Python 3.11
sudo dnf install -y python3.11 python3.11-pip python3.11-devel

# 验证
python3.11 --version
```

#### macOS

```bash
# 使用 Homebrew
brew install python@3.11

# 验证
python3.11 --version
```

### 2. 安装 Git

#### Windows
```powershell
winget install Git.Git
# 或访问 https://git-scm.com/download/win
```

#### Linux
```bash
sudo apt install -y git  # Ubuntu/Debian
sudo dnf install -y git  # CentOS/RHEL
```

#### macOS
```bash
brew install git
```

### 3. 克隆项目

```bash
git clone https://github.com/your-username/openakita.git
cd openakita
```

### 4. 创建虚拟环境

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux/macOS
python3.11 -m venv venv
source venv/bin/activate
```

### 5. 安装依赖

```bash
# 升级 pip
pip install --upgrade pip

# 安装项目依赖
pip install -e .

# 或使用 requirements.txt
pip install -r requirements.txt
```

### 6. 安装 Playwright 浏览器

```bash
# 安装浏览器内核
playwright install

# 或只安装 Chromium (更小)
playwright install chromium

# 安装系统依赖 (Linux)
playwright install-deps
```

### 7. 配置环境变量

```bash
# 复制示例配置
cp .env.example .env

# 编辑配置文件
# Windows: notepad .env
# Linux/macOS: nano .env 或 vim .env
```

必须配置的项目：
```ini
# 必需 - Anthropic API Key
ANTHROPIC_API_KEY=sk-your-api-key-here

# 可选 - 使用第三方代理
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 可选 - Telegram 机器人
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token
```

### 8. 初始化数据目录

```bash
# 创建数据目录
mkdir -p data
mkdir -p data/sessions
mkdir -p data/media
```

### 9. 验证安装

```bash
# 运行 Agent
openakita

# 或直接运行模块
python -m openakita
```

---

## 配置说明

### 环境变量完整列表

| 变量名 | 必需 | 默认值 | 说明 |
|--------|------|--------|------|
| `ANTHROPIC_API_KEY` | ✅ | - | Claude API 密钥 |
| `ANTHROPIC_BASE_URL` | ❌ | `https://api.anthropic.com` | API 端点 |
| `DEFAULT_MODEL` | ❌ | `claude-opus-4-5-20251101-thinking` | 模型名称 |
| `MAX_TOKENS` | ❌ | `8192` | 最大输出 token |
| `AGENT_NAME` | ❌ | `OpenAkita` | Agent 名称 |
| `MAX_ITERATIONS` | ❌ | `100` | Ralph 循环最大迭代 |
| `AUTO_CONFIRM` | ❌ | `false` | 自动确认危险操作 |
| `DATABASE_PATH` | ❌ | `data/agent.db` | 数据库路径 |
| `LOG_LEVEL` | ❌ | `INFO` | 日志级别 |
| `GITHUB_TOKEN` | ❌ | - | GitHub Token |

### IM 通道配置

| 变量名 | 说明 |
|--------|------|
| `TELEGRAM_ENABLED` | 启用 Telegram (true/false) |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `FEISHU_ENABLED` | 启用飞书 |
| `FEISHU_APP_ID` | 飞书 App ID |
| `FEISHU_APP_SECRET` | 飞书 App Secret |
| `WEWORK_ENABLED` | 启用企业微信 |
| `WEWORK_CORP_ID` | 企业 ID |
| `WEWORK_AGENT_ID` | Agent ID |
| `WEWORK_SECRET` | Secret |
| `DINGTALK_ENABLED` | 启用钉钉 |
| `DINGTALK_APP_KEY` | App Key |
| `DINGTALK_APP_SECRET` | App Secret |
| `QQ_ENABLED` | 启用 QQ |
| `QQ_ONEBOT_URL` | OneBot WebSocket URL |

---

## 启动服务

### 交互模式

```bash
# 启动交互式 CLI
openakita

# 或
python -m openakita
```

### Telegram Bot 服务

```bash
# 使用专用脚本
python run_telegram_bot.py

# 或后台运行
nohup python run_telegram_bot.py > telegram.log 2>&1 &
```

### 使用 systemd (Linux 推荐)

创建服务文件 `/etc/systemd/system/openakita.service`:

```ini
[Unit]
Description=OpenAkita Telegram Bot
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/openakita
Environment="PATH=/path/to/openakita/venv/bin"
ExecStart=/path/to/openakita/venv/bin/python run_telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable openakita
sudo systemctl start openakita
sudo systemctl status openakita
```

### 使用 Docker (可选)

```bash
# 构建镜像
docker build -t openakita .

# 运行容器
docker run -d \
  --name openakita \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  openakita
```

---

## 常见问题

### Q: Python 版本不对？

```bash
# 检查版本
python --version

# Windows: 指定版本运行
py -3.11 -m venv venv

# Linux: 使用 pyenv
pyenv install 3.11.8
pyenv local 3.11.8
```

### Q: pip 安装失败？

```bash
# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 或配置永久镜像
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: Playwright 安装失败？

```bash
# Linux 安装依赖
sudo apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 libxdamage1 libgbm1 libpango-1.0-0 libcairo2

# 或使用 playwright 自动安装
playwright install-deps
```

### Q: API 连接超时？

检查网络环境，如果在国内，建议使用代理服务：
```ini
ANTHROPIC_BASE_URL=https://yunwu.zeabur.app
```

### Q: Telegram Bot 无法启动？

1. 检查 Token 是否正确
2. 确认网络能访问 `api.telegram.org`
3. 如果在国内，需要配置代理

### Q: 内存不足？

```bash
# 限制 Python 内存使用
ulimit -v 2000000  # 约 2GB

# 或在 systemd 中配置
MemoryLimit=2G
```

---

## 更新升级

```bash
# 进入项目目录
cd openakita

# 拉取最新代码
git pull

# 重新安装依赖
pip install -e .

# 重启服务
sudo systemctl restart openakita
```

---

## 卸载

```bash
# 停止服务
sudo systemctl stop openakita
sudo systemctl disable openakita

# 删除服务文件
sudo rm /etc/systemd/system/openakita.service

# 删除虚拟环境
rm -rf venv

# 删除项目目录
cd .. && rm -rf openakita
```

---

## 技术支持

- 📖 文档: 查看项目 README.md
- 🐛 问题: 提交 GitHub Issue
- 💬 讨论: 加入 Telegram 群组

---

*最后更新: 2026-01-31*
