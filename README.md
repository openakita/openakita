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
  <a href="#desktop-terminal">Desktop Terminal</a> •
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#documentation">Documentation</a>
</p>

<p align="center">
  <strong>English</strong> | <a href="README_CN.md">中文</a>
</p>

---

## What is OpenAkita?

OpenAkita is a **self-evolving AI Agent framework**. It autonomously learns new skills, performs daily self-checks and repairs, accumulates experience from task execution, and never gives up when facing difficulties — persisting until the task is done.

Like the Akita dog it's named after: **loyal, reliable, never quits**.

- **Self-Evolving** — Auto-generates skills, installs dependencies, learns from mistakes
- **Never Gives Up** — Ralph Wiggum Mode: persistent execution loop until task completion
- **Growing Memory** — Remembers your preferences and habits, auto-consolidates daily
- **Standards-Based** — MCP and Agent Skills standard compliance for broad ecosystem compatibility
- **Multi-Platform** — Desktop Terminal GUI, CLI, Telegram, Feishu, DingTalk, WeCom, QQ

---

## Desktop Terminal

<p align="center">
  <img src="docs/assets/desktop_terminal_en.png" alt="OpenAkita Desktop Terminal" width="800" />
</p>

OpenAkita provides a cross-platform **Desktop Terminal** (built with Tauri + React) — an all-in-one AI assistant with chat, configuration, monitoring, and skill management:

- **AI Chat Assistant** — Streaming output, Markdown rendering, multimodal input, Thinking display, Plan mode
- **Bilingual (CN/EN)** — Auto-detects system language, one-click switch, fully internationalized
- **Guided Setup Flow** — 9-step wizard, streamlined and focused, dialog-based LLM endpoint management
- **Localization & i18n** — First-class support for Chinese and international ecosystems, PyPI mirrors, IM channels
- **LLM Endpoint Manager** — Multi-provider, multi-endpoint, auto-failover, online model list fetching
- **IM Channel Setup** — Telegram, Feishu, WeCom, DingTalk, QQ — all in one place
- **Persona & Living Presence** — 8 role presets, proactive greetings, memory recall, learns your preferences
- **Skill Marketplace** — Browse, download, configure skills in one place
- **Status Monitor** — Compact dashboard: service/LLM/IM health at a glance
- **System Tray** — Background residency + auto-start on boot, one-click start/stop

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
| **Multi-Platform IM** | CLI / Telegram / Feishu / DingTalk / WeCom (full support); QQ (implemented) |
| **Desktop Automation** | Windows UIAutomation + vision fallback, 9 tools: screenshot, click, type, hotkeys, window management |
| **Multi-Agent** | Master-Worker architecture, ZMQ message bus, smart routing, dynamic scaling, fault recovery |
| **Scheduled Tasks** | Cron / interval / one-time triggers, reminder + task types, persistent storage |
| **Identity & Memory** | Four-file identity (SOUL / AGENT / USER / MEMORY), vector search, daily auto-consolidation |
| **Persona System** | 8 role presets (default / business / tech / butler / girlfriend / boyfriend / family / Jarvis), layered persona architecture (preset + user preferences + context-adaptive), LLM-driven trait mining |
| **Living Presence** | Proactive engine: greetings, task follow-ups, memory recall; frequency control, quiet hours, feedback loop; feels like a real assistant |
| **Sticker Engine** | ChineseBQB integration (5700+ stickers), keyword search, mood mapping, per-persona sticker strategy |
| **Tool System** | 11 categories, 50+ tools, 3-level progressive disclosure (catalog → detail → execute) to reduce token usage |
| **Desktop App** | Tauri cross-platform desktop app, AI chat, guided wizard, tray residency, status monitoring |

---

## Persona & Living Presence

One of OpenAkita's most distinctive features — **not just a tool, but a lifelike assistant with personality, memory, and warmth**:

| Capability | Description |
|-----------|-------------|
| **8 Role Presets** | Default / Business / Tech Expert / Butler / Girlfriend / Boyfriend / Family / Jarvis |
| **3-Layer Persona** | Preset base → User preference learning → Context-adaptive, gets to know you over time |
| **Living Presence** | Proactive greetings, task follow-ups, memory recall ("Last time you mentioned learning guitar...") |
| **Auto Trait Mining** | LLM analyzes user personality every conversation turn, daily promotion to identity files |
| **Quiet Hours** | Auto-mutes at night, present but never intrusive |
| **Sticker Engine** | ChineseBQB 5700+ stickers, per-persona sticker strategy |

> The Agent proactively greets you during idle time, remembers your birthday, preferences, and work habits — like a real friend.

---

## Localization & i18n

OpenAkita offers **first-class support for both Chinese and international ecosystems**:

- **Chinese LLM Providers** — Alibaba DashScope (Qwen), Moonshot Kimi, MiniMax, DeepSeek, SiliconFlow
- **Global LLM Support** — Anthropic, OpenAI, Google Gemini, and more
- **Chinese IM Channels** — Feishu (Lark), WeCom, DingTalk, QQ native support
- **PyPI Mirrors** — Built-in Tsinghua TUNA and Alibaba mirrors for faster installs in China
- **Full i18n** — `react-i18next` based, auto system language detection, one-click switch

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
| **Persona Trait Mining** | Every conversation turn | LLM analyzes user messages → extract personality preferences → daily promotion to identity |
| **User Profile Learning** | During conversations | Identify preferences and habits → update USER.md → personalized experience |

---

## Quick Start

### Option 1: OpenAkita Desktop (Recommended)

The easiest way — graphical guided setup, no command-line experience needed:

1. Download the installer from [GitHub Releases](https://github.com/openakita/openakita/releases)
2. Install and launch OpenAkita Desktop
3. Follow the wizard: Workspace → Python → Install → LLM Endpoints → IM Channels → Finish & Start

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
│  ┌──────────────────── Desktop App ────────────────────────┐   │
│  │  Tauri + React · AI Chat · Config · Monitor · Skills    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Identity Layer ──────────────────────┐   │
│  │  SOUL.md · AGENT.md · USER.md · MEMORY.md                │   │
│  │  Personas (8 presets + user_custom)                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Core Layer ──────────────────────────┐   │
│  │  Brain (LLM) · Identity · Memory · Ralph Loop             │   │
│  │  Prompt Compiler · Task Monitor                           │   │
│  │  PersonaManager · TraitMiner · ProactiveEngine            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌──────────────────── Tool Layer ──────────────────────────┐   │
│  │  Shell · File · Web · MCP · Skills · Scheduler            │   │
│  │  Browser · Desktop · Plan · Profile · IM Channel          │   │
│  │  Persona · Sticker                                        │   │
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
| **Persona** | 3-layer persona architecture, 8 presets, LLM-driven trait mining, runtime state persistence |
| **Proactive Engine** | Living presence mode: proactive greetings, task follow-ups, memory recall, feedback-driven frequency control |
| **Sticker Engine** | ChineseBQB sticker integration, keyword/mood search, per-persona sticker strategy |
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
- [Tauri](https://tauri.app/) — Cross-platform desktop framework
- [browser-use](https://github.com/browser-use/browser-use) — AI browser automation
- [AGENTS.md Standard](https://agentsmd.io/) — Agent behavior specification
- [Agent Skills](https://agentskills.io/) — Skill standardization specification
- [ZeroMQ](https://zeromq.org/) — Multi-agent inter-process communication
- [ChineseBQB](https://github.com/zhaoolee/ChineseBQB) — Chinese sticker/emoji pack collection

## License

MIT License — See [LICENSE](LICENSE)

This project includes third-party skills licensed under Apache 2.0 and other
open-source licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for
details.

---

<p align="center">
  <strong>OpenAkita — Self-Evolving AI Agent, Learns Autonomously, Never Gives Up</strong>
</p>
