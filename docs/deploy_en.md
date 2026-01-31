# OpenAkita Deployment Guide (English)

[中文版](./deploy.md)

> Complete deployment guide from scratch

## 📋 Table of Contents

- [System Requirements](#system-requirements)
- [Dependencies](#dependencies)
- [Quick Deploy](#quick-deploy)
- [Manual Deployment](#manual-deployment)
- [Configuration](#configuration)
- [Starting Services](#starting-services)
- [FAQ](#faq)

---

## System Requirements

### Hardware Requirements

| Item | Minimum | Recommended |
|------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| Memory | 2 GB | 4+ GB |
| Disk | 5 GB | 20+ GB |
| Network | Access to API endpoints | Stable connection |

### Software Requirements

| Software | Version | Purpose |
|----------|---------|---------|
| **Python** | >= 3.11 | Runtime |
| **pip** | >= 23.0 | Package manager |
| **Git** | >= 2.30 | Version control & GitPython |
| **Node.js** | >= 18 (optional) | MCP servers |

### Supported Operating Systems

- ✅ Windows 10/11
- ✅ Ubuntu 20.04/22.04/24.04
- ✅ Debian 11/12
- ✅ CentOS 8/9 Stream
- ✅ macOS 12+

---

## Dependencies

### Python Third-Party Dependencies

```
# Core LLM
anthropic>=0.40.0          # Claude API
openai>=1.0.0              # OpenAI compatible endpoint

# MCP Protocol
mcp>=1.0.0

# CLI/UI
rich>=13.7.0
prompt-toolkit>=3.0.43
typer>=0.12.0

# Async HTTP
httpx>=0.27.0
aiofiles>=24.1.0

# Database
aiosqlite>=0.20.0

# Data Validation
pydantic>=2.5.0
pydantic-settings>=2.1.0

# Git Operations
gitpython>=3.1.40

# Browser Automation
playwright>=1.40.0

# Configuration
pyyaml>=6.0.1
python-dotenv>=1.0.0

# Utilities
tenacity>=8.2.3

# Memory System - Vector Search
sentence-transformers>=2.2.0  # Local embedding model
chromadb>=0.4.0               # Vector database

# Multi-Agent Orchestration
pyzmq>=25.0.0                 # ZeroMQ inter-process communication

# IM Channels (optional)
python-telegram-bot>=21.0  # Telegram
```

### Vector Search Configuration

The memory system uses vector search for semantic matching, requiring additional setup:

#### First Launch

The embedding model (~100MB) will be downloaded automatically on first launch.

Model cache location:
- Windows: `C:\Users\<user>\.cache\huggingface\`
- Linux/Mac: `~/.cache/huggingface/`

#### Pre-download Model (Optional)

For offline deployment, pre-download the model:

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('shibing624/text2vec-base-chinese')"
```

#### GPU Acceleration (Optional)

With NVIDIA GPU, install CUDA version of PyTorch:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu118
```

Set in `.env`:
```
EMBEDDING_DEVICE=cuda
```

#### Data Directory

Vector index is stored in `data/memory/chromadb/`. Ensure write permissions.

### System Tools

| Tool | Purpose | Installation |
|------|---------|--------------|
| Git | Code management, GitPython | System package manager |
| Browser kernel | Playwright | `playwright install` |

---

## Quick Deploy

### One-Click Deploy (Recommended)

**Windows (PowerShell):**
```powershell
# Download and run deployment script
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/openakita/openakita/main/scripts/deploy.ps1" -OutFile "scripts/deploy.ps1"
.\scripts\deploy.ps1
```

Or use local script:
```powershell
.\scripts\deploy.ps1
```

**Linux/macOS (Bash):**
```bash
# Download and run deployment script
curl -O https://raw.githubusercontent.com/openakita/openakita/main/scripts/deploy.sh
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

Or use local script:
```bash
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

---

## Manual Deployment

### 1. Install Python 3.11+

#### Windows

**Method A: Official Download**
```powershell
# 1. Visit https://www.python.org/downloads/
# 2. Download Python 3.11 or higher
# 3. Check "Add Python to PATH" during installation
# 4. Verify installation
python --version  # Should show Python 3.11.x or higher
```

**Method B: winget**
```powershell
winget install Python.Python.3.11
# Restart terminal and verify
python --version
```

**Method C: Scoop**
```powershell
scoop install python
python --version
```

#### Linux (Ubuntu/Debian)

```bash
# Update package list
sudo apt update

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Set default Python (optional)
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Verify
python3.11 --version
```

#### Linux (CentOS/RHEL)

```bash
# Enable EPEL and CRB repositories
sudo dnf install -y epel-release
sudo dnf config-manager --set-enabled crb

# Install Python 3.11
sudo dnf install -y python3.11 python3.11-pip python3.11-devel

# Verify
python3.11 --version
```

#### macOS

```bash
# Using Homebrew
brew install python@3.11

# Verify
python3.11 --version
```

### 2. Install Git

#### Windows
```powershell
winget install Git.Git
# Or visit https://git-scm.com/download/win
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

### 3. Clone Repository

```bash
git clone https://github.com/openakita/openakita.git
cd openakita
```

### 4. Create Virtual Environment

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux/macOS
python3.11 -m venv venv
source venv/bin/activate
```

### 5. Install Dependencies

```bash
# Upgrade pip
pip install --upgrade pip

# Install project dependencies
pip install -e .

# Or use requirements.txt
pip install -r requirements.txt
```

### 6. Install Playwright Browsers

```bash
# Install browser kernels
playwright install

# Or install only Chromium (smaller)
playwright install chromium

# Install system dependencies (Linux)
playwright install-deps
```

### 7. Configure Environment Variables

```bash
# Copy example config
cp .env.example .env

# Edit configuration
# Windows: notepad .env
# Linux/macOS: nano .env or vim .env
```

Required configuration:
```ini
# Required - Anthropic API Key
ANTHROPIC_API_KEY=sk-your-api-key-here

# Optional - Custom API endpoint
ANTHROPIC_BASE_URL=https://api.anthropic.com

# Optional - Telegram bot
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token
```

### 8. Initialize Data Directory

**Linux/macOS:**
```bash
mkdir -p data data/sessions data/media
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Force -Path data, data\sessions, data\media
```

**Windows (CMD):**
```cmd
mkdir data data\sessions data\media
```

### 9. Verify Installation

```bash
# Run Agent
openakita

# Or run module directly
python -m openakita
```

---

## Configuration

### Complete Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | - | Claude API key |
| `ANTHROPIC_BASE_URL` | ❌ | `https://api.anthropic.com` | API endpoint |
| `DEFAULT_MODEL` | ❌ | `claude-opus-4-5-20251101-thinking` | Model name |
| `MAX_TOKENS` | ❌ | `8192` | Max output tokens |
| `AGENT_NAME` | ❌ | `OpenAkita` | Agent name |
| `MAX_ITERATIONS` | ❌ | `100` | Ralph loop max iterations |
| `AUTO_CONFIRM` | ❌ | `false` | Auto-confirm dangerous operations |
| `DATABASE_PATH` | ❌ | `data/agent.db` | Database path |
| `LOG_LEVEL` | ❌ | `INFO` | Log level |
| `GITHUB_TOKEN` | ❌ | - | GitHub Token |

### IM Channel Configuration

| Variable | Description |
|----------|-------------|
| `TELEGRAM_ENABLED` | Enable Telegram (true/false) |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `FEISHU_ENABLED` | Enable Feishu |
| `FEISHU_APP_ID` | Feishu App ID |
| `FEISHU_APP_SECRET` | Feishu App Secret |
| `WEWORK_ENABLED` | Enable WeCom |
| `WEWORK_CORP_ID` | Corp ID |
| `WEWORK_AGENT_ID` | Agent ID |
| `WEWORK_SECRET` | Secret |
| `DINGTALK_ENABLED` | Enable DingTalk |
| `DINGTALK_APP_KEY` | App Key |
| `DINGTALK_APP_SECRET` | App Secret |
| `QQ_ENABLED` | Enable QQ |
| `QQ_ONEBOT_URL` | OneBot WebSocket URL |

### Memory System Configuration

| Variable | Description |
|----------|-------------|
| `EMBEDDING_MODEL` | Embedding model name (default: shibing624/text2vec-base-chinese) |
| `EMBEDDING_DEVICE` | Compute device (cpu or cuda) |
| `MEMORY_HISTORY_DAYS` | Days to retain conversation history |
| `MEMORY_MAX_HISTORY_FILES` | Max history files |
| `MEMORY_MAX_HISTORY_SIZE_MB` | Max history storage size (MB) |

### Multi-Agent Orchestration Configuration

| Variable | Description |
|----------|-------------|
| `ORCHESTRATION_ENABLED` | Enable multi-agent orchestration (true/false) |
| `ORCHESTRATION_BUS_ADDRESS` | ZMQ bus address |
| `ORCHESTRATION_PUB_ADDRESS` | ZMQ pub address |
| `ORCHESTRATION_MIN_WORKERS` | Minimum worker count |
| `ORCHESTRATION_MAX_WORKERS` | Maximum worker count |
| `ORCHESTRATION_HEARTBEAT_INTERVAL` | Heartbeat interval (seconds) |

---

## Starting Services

### Interactive Mode

```bash
# Start interactive CLI
openakita

# Or
python -m openakita
```

### Telegram Bot Service

```bash
# Using dedicated script
python scripts/run_telegram_bot.py

# Or run in background
nohup python scripts/run_telegram_bot.py > telegram.log 2>&1 &
```

### Using systemd (Linux Recommended)

Create service file `/etc/systemd/system/openakita.service`:

```ini
[Unit]
Description=OpenAkita Telegram Bot
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/openakita
Environment="PATH=/path/to/openakita/venv/bin"
ExecStart=/path/to/openakita/venv/bin/python scripts/run_telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable openakita
sudo systemctl start openakita
sudo systemctl status openakita
```

### Using Docker (Optional)

```bash
# Build image
docker build -t openakita .

# Run container
docker run -d \
  --name openakita \
  -v $(pwd)/.env:/app/.env \
  -v $(pwd)/data:/app/data \
  openakita
```

---

## FAQ

### Q: Wrong Python version?

```bash
# Check version
python --version

# Windows: specify version
py -3.11 -m venv venv

# Linux: use pyenv
pyenv install 3.11.8
pyenv local 3.11.8
```

### Q: pip install failed?

```bash
# Use mirror for faster download
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Or configure permanent mirror
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: Playwright installation failed?

```bash
# Linux install dependencies
sudo apt install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 libxdamage1 libgbm1 libpango-1.0-0 libcairo2

# Or use playwright auto-install
playwright install-deps
```

### Q: API connection timeout?

Check network environment and configure custom API endpoint:
```ini
ANTHROPIC_BASE_URL=https://your-api-endpoint.com
```

### Q: Telegram Bot won't start?

1. Check if Token is correct
2. Verify network can access `api.telegram.org`
3. Check firewall settings

### Q: Out of memory?

```bash
# Limit Python memory usage
ulimit -v 2000000  # ~2GB

# Or configure in systemd
MemoryLimit=2G
```

---

## Upgrading

```bash
# Enter project directory
cd openakita

# Pull latest code
git pull

# Reinstall dependencies
pip install -e .

# Restart service
sudo systemctl restart openakita
```

---

## Uninstalling

```bash
# Stop service
sudo systemctl stop openakita
sudo systemctl disable openakita

# Remove service file
sudo rm /etc/systemd/system/openakita.service

# Remove virtual environment
rm -rf venv

# Remove project directory
cd .. && rm -rf openakita
```

---

## Support

- 📖 Documentation: See project README.md
- 🐛 Issues: Submit GitHub Issue
- 💬 Discussion: Join Telegram group

---

*Last updated: 2026-01-31*
