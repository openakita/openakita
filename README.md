<p align="center">
  <img src="docs/assets/logo.png" alt="OpenAkita Logo" width="200" />
</p>

<h1 align="center">OpenAkita</h1>

<p align="center">
  <strong>Self-Evolving AI Agent — Learns Autonomously, Never Gives Up</strong>
</p>

<p align="center">
  <a href="https://github.com/openakita/openakita/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" />
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python Version" />
  </a>
  <a href="https://github.com/openakita/openakita/releases">
    <img src="https://img.shields.io/github/v/release/openakita/openakita?color=green" alt="Version" />
  </a>
  <a href="https://pypi.org/project/openakita/">
    <img src="https://img.shields.io/pypi/v/openakita?color=green" alt="PyPI" />
  </a>
  <a href="https://github.com/openakita/openakita/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/openakita/openakita/ci.yml?branch=main" alt="Build Status" />
  </a>
</p>

<p align="center">
  <a href="#setup-center">Setup Center</a> •
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#documentation">Documentation</a>
</p>

<p align="center">
  <a href="README_CN.md">中文文档</a>
</p>

---

## What is OpenAkita?

OpenAkita is a **self-evolving AI Agent framework**. It autonomously learns new skills, performs daily self-checks and repairs, accumulates experience from task execution, and never gives up when facing difficulties — persisting until the task is done.

Like the Akita dog it's named after: **loyal, reliable, never quits**.

- **Self-Evolving** — Auto-generates skills, installs dependencies, learns from mistakes
- **Never Gives Up** — Ralph Wiggum Mode: persistent execution loop until task completion
- **Growing Memory** — Remembers your preferences and habits, auto-consolidates daily
- **Standards-Based** — MCP and Agent Skills standard compliance for broad ecosystem compatibility
- **Multi-Platform** — Setup Center GUI, CLI, Telegram, Feishu, DingTalk, WeCom, QQ

---

## Setup Center

<p align="center">
  <img src="docs/assets/setupcenter.png" alt="Setup Center" width="800" />
</p>

OpenAkita provides a cross-platform **Setup Center** desktop app (built with Tauri + React) for intuitive installation and configuration:

- **Python Environment** — Auto-detect system Python or install embedded Python
- **One-Click Install** — Create venv + pip install OpenAkita (PyPI / GitHub Release / local source)
- **Version Control** — Choose specific versions; defaults to Setup Center version for compatibility
- **LLM Endpoint Manager** — Multi-provider, multi-endpoint, failover; fetch model lists + search selector
- **Prompt Compiler Config** — Dedicated fast model endpoints for instruction preprocessing
- **IM Channel Setup** — Telegram, Feishu, WeCom, DingTalk, QQ — all in one place
- **Agent & Skills Config** — Behavior parameters, skill toggles, MCP tool management
- **System Tray** — Background residency + auto-start on boot, one-click start/stop
- **Status Monitor** — Live service status dashboard with real-time log viewing

> **Download**: [GitHub Releases](https://github.com/openakita/openakita/releases)
>
> Available for Windows (.exe) / macOS (.dmg) / Linux (.deb / .AppImage)

---

## Features

| Feature | Description |
|---------|-------------|
| **Self-Learning & Evolution** | Daily self-check (04:00), memory consolidation (03:00), task retrospection, auto skill generation, auto dependency install |
| **Ralph Wiggum Mode** | Never-give-up execution loop: Plan → Act → Verify → repeat until done; checkpoint recovery |
| **Prompt Compiler** | Two-stage prompt architecture: fast model preprocesses instructions, compiles identity files, detects compound tasks |
| **MCP Integration** | Model Context Protocol standard, stdio transport, auto server discovery, built-in web search |
| **Skill System** | Agent Skills standard (SKILL.md), 8 discovery directories, GitHub install, LLM auto-generation |
| **Plan Mode** | Auto-detect multi-step tasks, create execution plans, real-time progress tracking, persisted as Markdown |
| **Multi-LLM Endpoints** | 9 providers, capability-based routing, priority failover, thinking mode, multimodal (text/image/video/voice) |
| **Multi-Platform IM** | CLI / Telegram / Feishu / DingTalk (full support); WeCom / QQ (implemented) |
| **Desktop Automation** | Windows UIAutomation + vision fallback, 9 tools: screenshot, click, type, hotkeys, window management |
| **Multi-Agent** | Master-Worker architecture, ZMQ message bus, smart routing, dynamic scaling, fault recovery |
| **Scheduled Tasks** | Cron / interval / one-time triggers, reminder + task types, persistent storage |
| **Identity & Memory** | Four-file identity (SOUL / AGENT / USER / MEMORY), vector search, daily auto-consolidation |
| **Tool System** | 11 categories, 50+ tools, 3-level progressive disclosure (catalog → detail → execute) to reduce token usage |
| **Setup Center** | Tauri cross-platform desktop app, guided wizard, tray residency, status monitoring |

---

## Self-Learning & Self-Evolution

The core differentiator: **OpenAkita doesn't just execute — it learns and grows autonomously**.

| Mechanism | Trigger | Behavior |
|-----------|---------|----------|
| **Daily Self-Check** | Every day at 04:00 | Analyze ERROR logs → LLM diagnosis → auto-fix tool errors → generate report |
| **Memory Consolidation** | Every day at 03:00 | Consolidate conversations → semantic dedup → extract insights → refresh MEMORY.md |
| **Task Retrospection** | After long tasks (>60s) | Analyze efficiency → extract lessons → store in long-term memory |
| **Skill Auto-Generation** | Missing capability detected | LLM generates SKILL.md + script → auto-test → register and load |
| **Auto Dependency Install** | pip/npm package missing | Search GitHub → install dependency → fallback to skill generation |
| **Real-Time Memory** | Every conversation turn | Extract preferences/rules/facts → vector storage → auto-update MEMORY.md |
| **User Profile Learning** | During conversations | Identify preferences and habits → update USER.md → personalized experience |

---

## Quick Start

### Option 1: Setup Center (Recommended)

The easiest way — graphical guided setup, no command-line experience needed:

1. Download the installer from [GitHub Releases](https://github.com/openakita/openakita/releases)
2. Install and launch Setup Center
3. Follow the wizard: Python → Install OpenAkita → Configure LLM → Configure IM → Finish & Start

### Option 2: PyPI Install

```bash
# Install
pip install openakita

# Install with all optional features
pip install openakita[all]

# Run setup wizard
openakita init
```

Optional extras: `feishu`, `whisper`, `browser`, `windows`

### Option 3: Source Install

```bash
git clone https://github.com/openakita/openakita.git
cd openakita
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[all]"
openakita init
```

### Run

```bash
# Interactive CLI
openakita

# Execute a single task
openakita run "Create a Python calculator with tests"

# Service mode (IM channels)
openakita serve

# Background daemon
openakita daemon start

# Check status
openakita status
```

### Recommended Models

| Model | Provider | Notes |
|-------|----------|-------|
| `claude-sonnet-4-5-*` | Anthropic | Default, balanced |
| `claude-opus-4-5-*` | Anthropic | Most capable |
| `qwen3-max` | Alibaba | Strong Chinese support |
| `deepseek-v3` | DeepSeek | Cost-effective |
| `kimi-k2.5` | Moonshot | Long-context |
| `minimax-m2.1` | MiniMax | Good for dialogue |

> For complex tasks, enable Thinking mode by using a `*-thinking` model variant (e.g., `claude-opus-4-5-20251101-thinking`).

### Basic Configuration

```bash
# .env (minimum configuration)

# LLM API (required — configure at least one)
ANTHROPIC_API_KEY=your-api-key

# Telegram (optional)
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          OpenAkita                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────── Setup Center ────────────────────────┐   │
│  │  Tauri + React Desktop App · Install · Config · Monitor   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Identity Layer ──────────────────────┐   │
│  │  SOUL.md · AGENT.md · USER.md · MEMORY.md                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Core Layer ──────────────────────────┐   │
│  │  Brain (LLM) · Identity · Memory · Ralph Loop             │   │
│  │  Prompt Compiler · Task Monitor                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Tool Layer ──────────────────────────┐   │
│  │  Shell · File · Web · MCP · Skills · Scheduler            │   │
│  │  Browser · Desktop · Plan · Profile · IM Channel          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Evolution Engine ────────────────────┐   │
│  │  SelfCheck · Generator · Installer · LogAnalyzer          │   │
│  │  DailyConsolidator · TaskRetrospection                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Channel Layer ───────────────────────┐   │
│  │  CLI · Telegram · Feishu · WeCom · DingTalk · QQ          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | Description |
|-----------|-------------|
| **Brain** | Unified LLM client, multi-endpoint failover, capability routing |
| **Identity** | Four-file identity system, compiled to token-efficient summaries |
| **Memory** | Vector memory (ChromaDB), semantic search, daily auto-consolidation |
| **Ralph Loop** | Never-give-up execution loop, StopHook interception, checkpoint recovery |
| **Prompt Compiler** | Two-stage prompt architecture, fast model preprocessing |
| **Task Monitor** | Execution monitoring, timeout model switching, task retrospection |
| **Evolution Engine** | Self-check, skill generation, dependency install, log analysis |
| **Skills** | Agent Skills standard, dynamic loading, GitHub install, auto-generation |
| **MCP** | Model Context Protocol, server discovery, tool proxying |
| **Scheduler** | Task scheduling, cron / interval / one-time triggers |
| **Channels** | Unified message format, multi-platform IM adapters |

---

## Documentation

| Document | Description |
|----------|-------------|
| [Quick Start](docs/getting-started.md) | Installation and basic usage |
| [Architecture](docs/architecture.md) | System design and components |
| [Configuration](docs/configuration.md) | All configuration options |
| [Deployment](docs/deploy.md) | Production deployment (systemd / Docker / nohup) |
| [MCP Integration](docs/mcp-integration.md) | Connecting external services |
| [IM Channels](docs/im-channels.md) | Telegram / Feishu / DingTalk setup |
| [Skill System](docs/skills.md) | Creating and using skills |
| [Testing](docs/testing.md) | Testing framework and coverage |

---

## Community

Join our community for help, discussions, and updates:

<table>
  <tr>
    <td align="center">
      <img src="docs/assets/wechat_group.jpg" width="200" alt="WeChat Group QR Code" /><br/>
      <b>WeChat Group</b><br/>
      <sub>Scan to join (Chinese)</sub>
    </td>
    <td>
      <b>WeChat</b> — Chinese community chat<br/><br/>
      <b>Discord</b> — <a href="https://discord.gg/Mkpd3rsm">Join Discord</a><br/><br/>
      <b>X (Twitter)</b> — <a href="https://x.com/openakita">@openakita</a><br/><br/>
      <b>Email</b> — <a href="mailto:zacon365@gmail.com">zacon365@gmail.com</a>
    </td>
  </tr>
</table>

- [Documentation](docs/) — Complete guides
- [Issues](https://github.com/openakita/openakita/issues) — Bug reports & feature requests
- [Discussions](https://github.com/openakita/openakita/discussions) — Q&A and ideas
- [Star us](https://github.com/openakita/openakita) — Show your support

---

## Acknowledgments

- [Anthropic Claude](https://www.anthropic.com/claude) — LLM Engine
- [Tauri](https://tauri.app/) — Cross-platform desktop framework for Setup Center
- [browser-use](https://github.com/browser-use/browser-use) — AI browser automation
- [AGENTS.md Standard](https://agentsmd.io/) — Agent behavior specification
- [Agent Skills](https://agentskills.io/) — Skill standardization specification
- [ZeroMQ](https://zeromq.org/) — Multi-agent inter-process communication

## License

MIT License — See [LICENSE](LICENSE)

---

<p align="center">
  <strong>OpenAkita — Self-Evolving AI Agent, Learns Autonomously, Never Gives Up</strong>
</p>
