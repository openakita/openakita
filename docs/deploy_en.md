# OpenAkita Deployment Guide (English)

[中文版](./deploy.md)

> Complete deployment guide covering PyPI installation, source installation, LLM configuration, and IM channel setup

## Table of Contents

- [System Requirements](#system-requirements)
- [Installation Methods](#installation-methods)
  - [Method 1: PyPI Install (Recommended)](#method-1-pypi-install-recommended)
  - [Method 2: One-Click Deploy Script](#method-2-one-click-deploy-script)
  - [Method 3: Source Install](#method-3-source-install)
- [Configuration](#configuration)
  - [Configuration Files Overview](#configuration-files-overview)
  - [Environment Variables (.env)](#environment-variables-env)
  - [LLM Endpoint Configuration (llm_endpoints.json)](#llm-endpoint-configuration-llm_endpointsjson)
  - [IM Channel Configuration](#im-channel-configuration)
  - [Identity Configuration (identity/)](#identity-configuration-identity)
  - [Memory System Configuration](#memory-system-configuration)
  - [Multi-Agent Orchestration](#multi-agent-orchestration)
- [Starting Services](#starting-services)
- [Publishing to PyPI](#publishing-to-pypi)
- [Production Deployment](#production-deployment)
- [FAQ](#faq)
- [Upgrading & Uninstalling](#upgrading--uninstalling)

---

## System Requirements

### Hardware

| Item | Minimum | Recommended |
|------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| Memory | 2 GB | 4+ GB |
| Disk | 5 GB | 20+ GB |
| Network | Access to API endpoints | Stable, low-latency |

### Software

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

## Installation Methods

### Method 1: PyPI Install (Recommended)

The simplest way to get started:

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or .\venv\Scripts\activate  # Windows

# 2. Install OpenAkita (core)
pip install openakita

# 3. Install optional features
pip install openakita[feishu]     # + Feishu (Lark) support
pip install openakita[whisper]    # + Voice recognition
pip install openakita[browser]    # + Browser AI agent
pip install openakita[windows]    # + Windows desktop automation
pip install openakita[all]        # Install all optional features (Windows-only deps are auto-skipped on non-Windows)

# 4. Run setup wizard
openakita init

# 5. Start
openakita
```

### Method 2: One-Click Deploy Script

There are two one-click paths:

- **One-click install (PyPI)**: fastest way to get a working installation (recommended)
- **One-click deploy (Source)**: for development / modifying the repo

#### Method 2-A: One-click install (PyPI, recommended)

**Linux/macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.ps1 | iex
```

For extras / mirrors, download and run with parameters (recommended):

```bash
curl -fsSL -o quickstart.sh https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.sh
bash quickstart.sh --extras all --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

```powershell
irm https://raw.githubusercontent.com/openakita/openakita/main/scripts/quickstart.ps1 -OutFile quickstart.ps1
.\quickstart.ps1 -Extras all -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
```

> The script installs into `~/.openakita/app` and uses an isolated venv at `~/.openakita/venv` by default
> (Windows: `%USERPROFILE%\.openakita\...`), to avoid polluting system Python.

#### Method 2-B: One-click deploy (Source)

Automatically installs Python, Git, dependencies, and everything else (requires cloning the repo first):

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

The script will automatically:
1. Detect and install Python 3.11+
2. Detect and install Git
3. Create virtual environment
4. Install dependencies (auto-fallback to Chinese mirror if needed)
5. Optionally install Playwright browsers
6. Optionally download Whisper voice model
7. Initialize `.env` and `data/llm_endpoints.json`
8. Create all required data directories
9. Verify installation
10. Optionally create systemd service (Linux)

### Method 3: Source Install

```bash
# 1. Clone repository
git clone https://github.com/openakita/openakita.git
cd openakita

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or .\venv\Scripts\activate  # Windows

# 3. Upgrade pip
pip install --upgrade pip

# 4. Install project (development mode)
pip install -e ".[all,dev]"

# 5. Install Playwright browsers (optional)
playwright install chromium

# 6. Copy configuration files
cp examples/.env.example .env
cp data/llm_endpoints.json.example data/llm_endpoints.json

# 7. Edit configuration
# Edit .env to fill in API Keys and IM channel settings
# Edit data/llm_endpoints.json to configure LLM endpoints

# 8. Run setup wizard (or configure manually)
openakita init

# 9. Start
openakita
```

---

## Configuration

### Configuration Files Overview

```
project-root/
├── .env                          # Environment variables (API Keys, IM Tokens, etc.)
├── data/
│   └── llm_endpoints.json        # LLM multi-endpoint config (models, priority, capability routing)
└── identity/
    ├── SOUL.md                   # Agent core personality
    ├── AGENT.md                  # Agent behavior specification
    ├── USER.md                   # User profile (auto-learned)
    └── MEMORY.md                 # Core memory (auto-updated)
```

**Configuration priority:** Environment variables > `.env` file > Code defaults

### Environment Variables (.env)

Copy the example file and edit:

```bash
cp examples/.env.example .env
```

#### Required

```ini
# At least one LLM API Key is required
ANTHROPIC_API_KEY=sk-your-api-key-here
```

> **Tip:** If you don't use Anthropic, you can configure other API Keys (e.g. `DASHSCOPE_API_KEY`)
> as long as they are properly referenced in `data/llm_endpoints.json`.

#### Full Environment Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| **LLM Configuration** | | | |
| `ANTHROPIC_API_KEY` | ⚡ | - | Anthropic Claude API Key |
| `ANTHROPIC_BASE_URL` | | `https://api.anthropic.com` | API endpoint (supports proxies) |
| `DEFAULT_MODEL` | | `claude-opus-4-5-20251101-thinking` | Default model |
| `MAX_TOKENS` | | `8192` | Max output tokens |
| `KIMI_API_KEY` | | - | Kimi (Moonshot) API Key |
| `DASHSCOPE_API_KEY` | | - | Qwen (DashScope) API Key |
| `MINIMAX_API_KEY` | | - | MiniMax API Key |
| `DEEPSEEK_API_KEY` | | - | DeepSeek API Key |
| `OPENROUTER_API_KEY` | | - | OpenRouter API Key |
| `SILICONFLOW_API_KEY` | | - | SiliconFlow API Key |
| `LLM_ENDPOINTS_CONFIG` | | `data/llm_endpoints.json` | LLM endpoint config file path |
| **Agent Configuration** | | | |
| `AGENT_NAME` | | `OpenAkita` | Agent name |
| `MAX_ITERATIONS` | | `100` | Ralph loop max iterations |
| `AUTO_CONFIRM` | | `false` | Auto-confirm dangerous operations |
| `DATABASE_PATH` | | `data/agent.db` | Database path |
| `LOG_LEVEL` | | `INFO` | Log level |
| **Network Proxy** | | | |
| `HTTP_PROXY` | | - | HTTP proxy |
| `HTTPS_PROXY` | | - | HTTPS proxy |
| `ALL_PROXY` | | - | Global proxy (highest priority) |
| `FORCE_IPV4` | | `false` | Force IPv4 |
| **IM Channels** | | | |
| `TELEGRAM_ENABLED` | | `false` | Enable Telegram |
| `TELEGRAM_BOT_TOKEN` | | - | Telegram Bot Token |
| `TELEGRAM_PROXY` | | - | Telegram-specific proxy |
| `FEISHU_ENABLED` | | `false` | Enable Feishu (Lark) |
| `FEISHU_APP_ID` | | - | Feishu App ID |
| `FEISHU_APP_SECRET` | | - | Feishu App Secret |
| `WEWORK_ENABLED` | | `false` | Enable WeCom |
| `WEWORK_CORP_ID` | | - | Corp ID |
| `WEWORK_AGENT_ID` | | - | Application Agent ID |
| `WEWORK_SECRET` | | - | Application Secret |
| `DINGTALK_ENABLED` | | `false` | Enable DingTalk |
| `DINGTALK_CLIENT_ID` | | - | DingTalk Client ID (formerly App Key) |
| `DINGTALK_CLIENT_SECRET` | | - | DingTalk Client Secret (formerly App Secret) |
| `QQBOT_ENABLED` | | `false` | Enable QQ Official Bot |
| `QQBOT_APP_ID` | | - | QQ Open Platform AppID |
| `QQBOT_APP_SECRET` | | - | QQ Open Platform AppSecret |
| `QQBOT_SANDBOX` | | `false` | Sandbox mode |
| `ONEBOT_ENABLED` | | `false` | Enable OneBot |
| `ONEBOT_WS_URL` | | `ws://127.0.0.1:8080` | OneBot WebSocket URL |
| `ONEBOT_ACCESS_TOKEN` | | - | OneBot Access Token (optional) |
| **Memory System** | | | |
| `EMBEDDING_MODEL` | | `shibing624/text2vec-base-chinese` | Embedding model |
| `EMBEDDING_DEVICE` | | `cpu` | Compute device (cpu/cuda) |
| `MEMORY_HISTORY_DAYS` | | `30` | History retention days |
| **Voice Recognition** | | | |
| `WHISPER_MODEL` | | `base` | Whisper model size |
| **GitHub** | | | |
| `GITHUB_TOKEN` | | - | For searching/downloading skills |

### LLM Endpoint Configuration (llm_endpoints.json)

This is the **core configuration file** of OpenAkita, supporting multi-endpoint, automatic failover, and capability-based routing.

#### Configuration Methods

**Method A: Interactive Wizard (Recommended)**
```bash
python -m openakita.llm.setup.cli
```

The wizard supports:
- Selecting from known provider list
- Automatically fetching available models
- Testing endpoint connectivity
- Setting priorities
- Saving configuration

**Method B: Manual Edit**
```bash
cp data/llm_endpoints.json.example data/llm_endpoints.json
# Then edit the file
```

#### Configuration Structure

```json
{
  "endpoints": [
    {
      "name": "claude-primary",          // Endpoint name (unique identifier)
      "provider": "anthropic",           // Provider identifier
      "api_type": "anthropic",           // API protocol: anthropic or openai
      "base_url": "https://api.anthropic.com",  // API base URL
      "api_key_env": "ANTHROPIC_API_KEY",       // API Key env variable name
      "model": "claude-opus-4-5-20251101-thinking",
      "priority": 1,                     // Priority (1 = highest)
      "max_tokens": 8192,               // Max output tokens
      "timeout": 60,                     // Timeout (seconds)
      "capabilities": ["text", "vision", "tools"],  // Capability declaration
      "extra_params": {},                // Extra API parameters
      "note": "Anthropic Official API"   // Note
    }
  ],
  "settings": {
    "retry_count": 2,                    // Retries per endpoint
    "retry_delay_seconds": 2,            // Retry delay (seconds)
    "health_check_interval": 60,         // Health check interval (seconds)
    "fallback_on_error": true            // Auto-failover to backup endpoint
  }
}
```

#### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Unique endpoint name |
| `provider` | string | ✅ | Provider: `anthropic` / `openai` / `dashscope` / `moonshot` / `minimax` / `deepseek` / `zhipu` / `openrouter` / `siliconflow` |
| `api_type` | string | ✅ | API protocol: `anthropic` (native) or `openai` (OpenAI-compatible) |
| `base_url` | string | ✅ | API base URL |
| `api_key_env` | string | ✅ | Environment variable name for API Key (actual value set in `.env`) |
| `model` | string | ✅ | Model name |
| `priority` | int | ✅ | Priority — lower number = higher priority |
| `max_tokens` | int | | Max output tokens, default 8192 |
| `timeout` | int | | Request timeout in seconds, default 60 |
| `capabilities` | list | | Capability list: `text` / `vision` / `video` / `tools` / `thinking` |
| `extra_params` | dict | | Extra parameters passed to the API |
| `note` | string | | Description note |

#### Capability Routing

| Capability | Description | Typical Models |
|------------|-------------|----------------|
| `text` | Text conversation | All models |
| `vision` | Image understanding | Claude 3.5+, GPT-4V, Qwen-VL |
| `video` | Video understanding | Kimi, Gemini |
| `tools` | Tool/function calling | Claude 3+, GPT-4+, Qwen |
| `thinking` | Deep reasoning | O1, DeepSeek-R1, QwQ, Claude Thinking |

When a user sends an image, the system automatically selects an endpoint with `vision` capability; for video, it selects one with `video` capability.

#### Failover Mechanism

1. Endpoints are tried in `priority` order (lowest first)
2. On failure, automatically switches to the next endpoint
3. Failed endpoints enter a **3-minute cooldown** period
4. Endpoints automatically recover after cooldown

#### Provider Configuration Examples

**Anthropic (Claude)**
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

**Qwen (DashScope / Alibaba Cloud)**
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

**Kimi (Moonshot AI)**
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

**OpenRouter (Multi-model Aggregator)**
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

**MiniMax (Anthropic Protocol)**
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

**Using a Proxy / Relay Service**

If direct access to Anthropic is difficult, use a relay service:
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

### IM Channel Configuration

OpenAkita supports 5 major IM platforms, all enabled via `.env`:

| Platform | Status | Protocol | Extra Dependency |
|----------|--------|----------|-----------------|
| Telegram | ✅ Stable | Bot API | Built-in |
| Feishu (Lark) | ✅ Stable | WebSocket | `pip install openakita[feishu]` |
| WeCom (WeWork) | ✅ Stable | HTTP API | None |
| DingTalk | ✅ Stable | HTTP API | None |
| QQ Official Bot | ✅ Stable | QQ Open Platform API | `pip install openakita[qqbot]` |
| OneBot | ✅ Stable | OneBot WS | Requires OneBot server + `pip install openakita[onebot]` |

#### Telegram

1. Create a Bot at [@BotFather](https://t.me/BotFather) and get the Token
2. Configure `.env`:
```ini
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
# Users in mainland China must configure a proxy
TELEGRAM_PROXY=http://127.0.0.1:7890
```
3. On first use, the Agent generates a pairing code in `data/telegram/pairing/` (visible in console output)

#### Feishu (Lark)

1. Create an app at [Feishu Open Platform](https://open.feishu.cn/)
2. Enable Bot capability and add message-related permissions
3. Configure `.env`:
```ini
FEISHU_ENABLED=true
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
```
4. The Feishu adapter uses WebSocket long connection by default (recommended) — no callback URL needed

#### WeCom (WeWork)

1. Create an internal app at [WeCom Admin Console](https://work.weixin.qq.com/)
2. Get Corp ID, Agent ID, and Secret
3. Configure `.env`:
```ini
WEWORK_ENABLED=true
WEWORK_CORP_ID=ww_xxxxx
WEWORK_AGENT_ID=1000002
WEWORK_SECRET=xxxxx
```

#### DingTalk

1. Create an internal app at [DingTalk Open Platform](https://open.dingtalk.com/)
2. Enable Bot capability
3. Configure `.env`:
```ini
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=dingxxxxx
DINGTALK_CLIENT_SECRET=xxxxx
```

#### QQ Official Bot

Create a bot at [QQ Open Platform](https://q.qq.com) and get credentials:
```ini
QQBOT_ENABLED=true
QQBOT_APP_ID=your-app-id
QQBOT_APP_SECRET=your-app-secret
QQBOT_SANDBOX=false
```

#### OneBot (Universal Protocol)

Requires a running OneBot implementation (e.g. [NapCat](https://github.com/NapNeko/NapCatQQ)):
```ini
ONEBOT_ENABLED=true
ONEBOT_WS_URL=ws://127.0.0.1:8080
ONEBOT_ACCESS_TOKEN=
```

#### Running Modes

IM channels support two running modes:

```bash
# Mode 1: CLI + IM (interactive mode with IM channels running simultaneously)
openakita

# Mode 2: IM-only service (background service, no CLI)
openakita serve
```

### Identity Configuration (identity/)

Identity files define the Agent's personality, behavior, and memory:

```bash
# Create from example files
cp identity/SOUL.md.example identity/SOUL.md
cp identity/AGENT.md.example identity/AGENT.md
cp identity/USER.md.example identity/USER.md
cp identity/MEMORY.md.example identity/MEMORY.md
```

| File | Description | Auto-Updated |
|------|-------------|--------------|
| `SOUL.md` | Core personality and philosophy | No (manual) |
| `AGENT.md` | Behavior specification and workflows | No (manual) |
| `USER.md` | User profile | Yes (Agent auto-learns) |
| `MEMORY.md` | Core memory | Yes (daily consolidation) |

> Running `openakita init` will automatically create these files.

### Memory System Configuration

The memory system uses vector search for semantic matching:

```ini
# Configure in .env
EMBEDDING_MODEL=shibing624/text2vec-base-chinese  # Recommended for Chinese
EMBEDDING_DEVICE=cpu                                # Set to cuda if GPU available
```

**First launch** will automatically download the embedding model (~100MB).

**Offline deployment** — pre-download:
```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('shibing624/text2vec-base-chinese')"
```

**GPU acceleration** (optional):
```bash
pip install torch --index-url https://download.pytorch.org/whl/cu118
# Set EMBEDDING_DEVICE=cuda in .env
```

### Multi-Agent Orchestration

Enable the MasterAgent + Worker architecture for complex tasks:

```ini
# Configure in .env
ORCHESTRATION_ENABLED=true
ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555
ORCHESTRATION_PUB_ADDRESS=tcp://127.0.0.1:5556
ORCHESTRATION_MIN_WORKERS=1
ORCHESTRATION_MAX_WORKERS=5
```

---

## Starting Services

### Interactive Mode (Development/Testing)

```bash
openakita           # Interactive CLI (with IM channels running simultaneously)
python -m openakita # Same
```

### Service Mode (Production)

```bash
openakita serve     # IM-only service, no CLI interaction
```

### Single Task

```bash
openakita run "Analyze the code structure of the current directory"
```

### Other Commands

```bash
openakita init              # Run setup wizard
openakita status            # Show Agent status
openakita selfcheck         # Run self-check
openakita compile           # Compile identity files (reduces token usage)
openakita prompt-debug      # Show prompt debug info
openakita --version         # Show version
```

---

## Publishing to PyPI

The project has a fully configured PyPI publishing pipeline:

### Manual Publishing

```bash
# 1. Install build tools
pip install build twine

# 2. Build package
python -m build

# 3. Check package
twine check dist/*

# 4. Upload to PyPI
twine upload dist/*
# Or upload to TestPyPI
twine upload --repository testpypi dist/*
```

### Automated Publishing (GitHub Actions)

Push a version tag to automatically publish:

```bash
# 1. Update version in pyproject.toml
# 2. Create tag
git tag v1.2.2
git push origin v1.2.2
# 3. GitHub Actions automatically builds and publishes to PyPI
```

> Requires `PYPI_API_TOKEN` configured in GitHub repository Settings > Secrets.

### Verifying Installation

```bash
# Install from PyPI
pip install openakita

# Verify
openakita --version
python -c "import openakita; print(openakita.__version__)"
```

---

## Production Deployment

### Using systemd (Linux Recommended)

Create service file `/etc/systemd/system/openakita.service`:

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

# View logs
journalctl -u openakita -f
```

### Using Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[feishu]"

# Copy project files
COPY . .

# Install Playwright
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

### Using nohup (Simple Background)

```bash
source venv/bin/activate
nohup openakita serve > logs/serve.log 2>&1 &
echo $! > openakita.pid
```

---

## FAQ

### Q: How to choose an LLM?

Recommended strategy (in `data/llm_endpoints.json`):
- **Primary:** Claude Sonnet/Opus (most comprehensive capabilities)
- **Backup 1:** Qwen qwen3-max (fast access in China, supports reasoning)
- **Backup 2:** Kimi k2.5 (supports video understanding)
- **Backup 3:** DeepSeek Chat (best cost-performance ratio)

### Q: Wrong Python version?

```bash
python --version
# Windows: py -3.11 -m venv venv
# Linux: pyenv install 3.11.8 && pyenv local 3.11.8
```

### Q: pip install failed?

```bash
# Use Chinese mirror (for users in China)
pip install openakita -i https://pypi.tuna.tsinghua.edu.cn/simple
# Or set permanent mirror
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: Playwright installation failed?

```bash
# Linux: install system dependencies
playwright install-deps
# Or install only Chromium
playwright install chromium
```

### Q: API connection timeout?

1. Check if your network can reach the API endpoint
2. Configure proxy: set `ALL_PROXY` in `.env`
3. Use an API relay service: modify `base_url` in `llm_endpoints.json`

### Q: Telegram Bot won't start?

1. Verify Token is correct
2. Users in mainland China must configure `TELEGRAM_PROXY`
3. Ensure the proxy can reach `api.telegram.org`

### Q: Out of memory?

```bash
# Use CPU-only PyTorch (saves ~2GB)
pip install torch --index-url https://download.pytorch.org/whl/cpu
# Use a smaller embedding model
# EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

### Q: How to verify LLM endpoint configuration?

```bash
# Use the interactive tool to test
python -m openakita.llm.setup.cli
# Choose "4. Test endpoint" to verify connectivity
```

---

## Upgrading & Uninstalling

### Upgrading

```bash
# PyPI install
pip install --upgrade openakita

# Source install
cd openakita
git pull
pip install -e ".[all]"
```

### Uninstalling

```bash
# Stop service
sudo systemctl stop openakita
sudo systemctl disable openakita
sudo rm /etc/systemd/system/openakita.service

# Uninstall package
pip uninstall openakita

# Remove data (use caution)
rm -rf data/ identity/ logs/
```

---

## Support

- Documentation: See `docs/` directory for detailed docs
- Issues: Submit a [GitHub Issue](https://github.com/openakita/openakita/issues)
- Community: Join the Telegram group

---

*Last updated: 2026-02-06*
