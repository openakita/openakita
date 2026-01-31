<p align="center">
  <img src="docs/assets/logo.png" alt="OpenAkita Logo" width="200" />
</p>

<h1 align="center">OpenAkita</h1>

<p align="center">
  <strong>Your Loyal and Reliable AI Companion</strong>
</p>

<p align="center">
  <a href="https://github.com/openakita/openakita/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" />
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python Version" />
  </a>
  <a href="https://github.com/openakita/openakita/releases">
    <img src="https://img.shields.io/badge/version-1.0.0-green.svg" alt="Version" />
  </a>
</p>

<p align="center">
  <a href="#philosophy">Philosophy</a> •
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#multi-agent">Multi-Agent</a>
</p>

<p align="center">
  <a href="README_CN.md">中文文档</a>
</p>

---

## What is OpenAkita?

OpenAkita is a **self-evolving AI assistant** — your loyal and reliable companion in the digital world.

Like the Akita dog it's named after, OpenAkita embodies:
- 🤝 **Loyal Companion** — Always by your side, ready to help whenever you need
- 🧠 **Grows With You** — Remembers your preferences and becomes more helpful over time
- 💪 **Reliable Partner** — Commits to completing tasks, never gives up easily
- 🛡️ **Trustworthy** — Keeps your data safe and respects your privacy

OpenAkita is more than a tool — it's a partner that remembers you, understands you, and stands by you through every challenge.

## Philosophy

### 1. Human-Centered

OpenAkita's core is **serving people**, not showcasing technology. We focus on:

- **Understanding Intent**: Not just executing commands, but understanding what you really want
- **Proactive Communication**: Asks when encountering problems, rather than guessing or failing
- **Privacy Respect**: Your data belongs to you, never misused

### 2. Continuous Evolution

OpenAkita can **learn and evolve**:

- **Memory System**: Remembers your preferences, habits, common operations
- **Skill Extension**: Automatically searches or generates new capabilities for new needs
- **Experience Accumulation**: Learns from each task, becomes more efficient

### 3. Reliable Execution

Once a task is assigned to OpenAkita:

- **Persistent Completion**: Won't give up due to minor errors
- **Smart Retry**: Analyzes failure reasons, tries different approaches
- **Progress Saving**: Long tasks support checkpoint recovery

### 4. Multi-Platform Collaboration

Through **Multi-Agent architecture** for efficient parallelism:

- **Master-Worker Architecture**: Master coordinates, Workers execute
- **Smart Scheduling**: Allocates resources based on task complexity
- **Fault Recovery**: Automatic detection and restart of failed nodes

## Features

### Basic Capabilities

| Feature | Description |
|---------|-------------|
| **Smart Dialogue** | Multi-turn contextual conversation |
| **Task Execution** | Shell commands, file operations, network requests |
| **Code Abilities** | Write, debug, explain code |
| **Knowledge Retrieval** | Search web, GitHub, local documents |

### Advanced Capabilities

| Feature | Description |
|---------|-------------|
| **Skill System** | Extensible skill library, supports customization |
| **MCP Integration** | Connect browsers, databases, external services |
| **Scheduled Tasks** | Set reminders, periodic tasks |
| **User Profile** | Learn your preferences, personalized service |

### Multi-Platform Support

| Platform | Status |
|----------|--------|
| **CLI** | ✅ Full Support |
| **Telegram** | ✅ Full Support |
| **Feishu** | ⚠️ Implemented, Not Tested |
| **WeCom** | ⚠️ Implemented, Not Tested |
| **DingTalk** | ⚠️ Implemented, Not Tested |
| **QQ** | ⚠️ Implemented, Not Tested |

## Quick Start

> ⚠️ **Early Stage Project**: This project is in early development. For faster deployment, we recommend using AI coding assistants like [Cursor](https://cursor.sh/), [Claude](https://claude.ai/), or [GitHub Copilot](https://github.com/features/copilot) to help with setup and troubleshooting.

### Requirements

- Python 3.11+
- [Anthropic API Key](https://console.anthropic.com/)

### Recommended Models

**Anthropic Claude (Default)**
| Model | Best For | Notes |
|-------|----------|-------|
| `claude-sonnet-4-20250514` | Daily use | Balanced performance and cost |
| `claude-opus-4-20250514` | Complex tasks | Most capable, higher cost |
| `claude-opus-4-5-20251101-thinking` | Deep reasoning | Extended thinking for complex problems |

**Other Compatible Models** (via OpenAI-compatible API)
| Model | Provider | Notes |
|-------|----------|-------|
| `qwen3-max` | Alibaba | Strong Chinese language support |
| `minimax-2.1` | MiniMax | Good for dialogue and creation |
| `kimi-2.5` | Moonshot | Excellent long-context capability |

### Installation

```bash
# Clone repository
git clone https://github.com/openakita/openakita.git
cd openakita

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install
pip install -e .

# Configure
cp .env.example .env
# Edit .env, fill in ANTHROPIC_API_KEY
```

### Run

```bash
# Interactive CLI
openakita

# Execute single task
openakita run "Write a Python calculator"

# Service mode (IM channels only)
openakita serve

# Check status
openakita status
```

### Basic Configuration

```bash
# .env file

# Required
ANTHROPIC_API_KEY=your-api-key

# Optional: Custom API endpoint
ANTHROPIC_BASE_URL=https://api.anthropic.com

# Optional: Enable Telegram
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token

# Optional: Enable Multi-Agent
ORCHESTRATION_ENABLED=true
```

## Architecture

### Overall Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              OpenAkita                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│    ┌──────────────────────── Identity Layer ──────────────────────┐     │
│    │                                                               │     │
│    │   SOUL.md      AGENT.md      USER.md      MEMORY.md          │     │
│    │   (Values)     (Behavior)    (Profile)    (Memory)           │     │
│    │                                                               │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                               │                                          │
│                               ▼                                          │
│    ┌──────────────────────── Core Layer ──────────────────────────┐     │
│    │                                                               │     │
│    │   ┌─────────┐    ┌──────────┐    ┌───────────────┐           │     │
│    │   │  Brain  │    │ Identity │    │    Memory     │           │     │
│    │   │  (LLM)  │    │  (Self)  │    │   (System)    │           │     │
│    │   └─────────┘    └──────────┘    └───────────────┘           │     │
│    │                                                               │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                               │                                          │
│                               ▼                                          │
│    ┌──────────────────────── Tool Layer ──────────────────────────┐     │
│    │                                                               │     │
│    │   ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐                │     │
│    │   │ Shell │  │ File  │  │  Web  │  │  MCP  │                │     │
│    │   └───────┘  └───────┘  └───────┘  └───────┘                │     │
│    │                                                               │     │
│    │   ┌───────────┐  ┌────────────┐  ┌─────────────┐             │     │
│    │   │  Skills   │  │  Scheduler │  │  Evolution  │             │     │
│    │   └───────────┘  └────────────┘  └─────────────┘             │     │
│    │                                                               │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                               │                                          │
│                               ▼                                          │
│    ┌──────────────────────── Channel Layer ───────────────────────┐     │
│    │                                                               │     │
│    │   ┌─────┐  ┌──────────┐  ┌────────┐  ┌──────────┐  ┌────┐   │     │
│    │   │ CLI │  │ Telegram │  │ Feishu │  │ DingTalk │  │ QQ │   │     │
│    │   └─────┘  └──────────┘  └────────┘  └──────────┘  └────┘   │     │
│    │                                                               │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | Description |
|-----------|-------------|
| **Brain** | LLM interaction layer, supports multi-endpoint failover |
| **Identity** | Identity system, loads SOUL/AGENT/USER/MEMORY |
| **Memory** | Vector memory system, supports semantic retrieval |
| **Skills** | Skill system, supports dynamic loading and extension |
| **Scheduler** | Scheduled task scheduler |
| **Channels** | Multi-platform message channels |

## Multi-Agent

When `ORCHESTRATION_ENABLED=true` is set, OpenAkita enters Multi-Agent collaboration mode:

### Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       Main Process                              │
│                                                                 │
│   ┌─────────┐    ┌──────────┐    ┌───────────┐                 │
│   │   CLI   │    │ Gateway  │    │ Scheduler │                 │
│   │(Command)│    │(IM Chan.)│    │  (Tasks)  │                 │
│   └────┬────┘    └────┬─────┘    └─────┬─────┘                 │
│        │              │                │                        │
│        └──────────────┼────────────────┘                        │
│                       ▼                                         │
│              ┌────────────────┐                                 │
│              │  MasterAgent   │                                 │
│              │ (Coordinator)  │                                 │
│              │                │                                 │
│              │ • Task Routing │                                 │
│              │ • Worker Mgmt  │                                 │
│              │ • Health Check │                                 │
│              │ • Fault Recov. │                                 │
│              └───────┬────────┘                                 │
│                      │                                          │
│              ┌───────┴────────┐                                 │
│              │   AgentBus     │                                 │
│              │  (ZMQ Comm.)   │                                 │
│              └───────┬────────┘                                 │
│                      │                                          │
│              ┌───────┴────────┐                                 │
│              │ AgentRegistry  │                                 │
│              │  (Registry)    │                                 │
│              └────────────────┘                                 │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌────────────┐ ┌────────────┐ ┌────────────┐
   │  Worker 1  │ │  Worker 2  │ │  Worker N  │
   │  (Process) │ │  (Process) │ │  (Process) │
   │            │ │            │ │            │
   │ • Execute  │ │ • Execute  │ │ • Execute  │
   │ • Heartbeat│ │ • Heartbeat│ │ • Heartbeat│
   │ • Return   │ │ • Return   │ │ • Return   │
   └────────────┘ └────────────┘ └────────────┘
```

### Features

| Feature | Description |
|---------|-------------|
| **Smart Routing** | Simple tasks local, complex tasks to Workers |
| **Stateless Workers** | Session history via messages, flexible scheduling |
| **Shared Memory** | All Workers use same memory storage |
| **Fault Recovery** | Heartbeat + automatic Worker restart |
| **Dynamic Scaling** | Auto-adjust Worker count based on load |

### Configuration

```bash
# Enable Multi-Agent
ORCHESTRATION_ENABLED=true

# Worker count
ORCHESTRATION_MIN_WORKERS=1
ORCHESTRATION_MAX_WORKERS=5

# Heartbeat interval (seconds)
ORCHESTRATION_HEARTBEAT_INTERVAL=5

# ZMQ addresses
ORCHESTRATION_BUS_ADDRESS=tcp://127.0.0.1:5555
ORCHESTRATION_PUB_ADDRESS=tcp://127.0.0.1:5556
```

### CLI Commands

```bash
# View Agent status
/agents

# View collaboration stats
/status
```

## Project Structure

```
openakita/
├── identity/                 # Identity configs
│   ├── SOUL.md               # Values
│   ├── AGENT.md              # Behavior rules
│   ├── USER.md               # User profile
│   └── MEMORY.md             # Working memory
├── src/openakita/
│   ├── core/                 # Core modules
│   │   ├── agent.py          # Agent main class
│   │   ├── brain.py          # LLM interaction
│   │   ├── identity.py       # Identity system
│   │   └── ralph.py          # Task loop
│   ├── orchestration/        # Multi-Agent
│   │   ├── master.py         # MasterAgent
│   │   ├── worker.py         # WorkerAgent
│   │   ├── registry.py       # Registry
│   │   ├── bus.py            # ZMQ communication
│   │   └── monitor.py        # Monitoring
│   ├── tools/                # Tool layer
│   ├── skills/               # Skill system
│   ├── channels/             # Message channels
│   ├── memory/               # Memory system
│   └── scheduler/            # Scheduled tasks
├── skills/                   # Skills directory
├── data/                     # Data storage
└── docs/                     # Documentation
```

## Documentation

| Document | Description |
|----------|-------------|
| [Quick Start](docs/getting-started.md) | Installation and basic usage |
| [Configuration](docs/configuration.md) | All configuration options |
| [Skill System](docs/skills.md) | Creating and using skills |
| [MCP Integration](docs/mcp-integration.md) | Connecting external services |
| [IM Channels](docs/im-channels.md) | Telegram/Feishu/DingTalk setup |
| [Deployment](docs/deploy_en.md) | Production deployment |

## Contributing

Contributions welcome! See [Contributing Guide](CONTRIBUTING.md).

```bash
# Development environment
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Code check
ruff check src/
mypy src/
```

## Acknowledgments

- [Anthropic Claude](https://www.anthropic.com/claude) — LLM Engine
- [AGENTS.md Standard](https://agentsmd.io/) — Agent behavior specification
- [ZeroMQ](https://zeromq.org/) — Inter-process communication

## License

MIT License - See [LICENSE](LICENSE)

---

<p align="center">
  <strong>OpenAkita — Your Loyal and Reliable AI Companion</strong>
</p>
