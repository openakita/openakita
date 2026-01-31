<p align="center">
  <img src="docs/assets/logo.png" alt="OpenAkita Logo" width="200" />
</p>

<h1 align="center">OpenAkita</h1>

<p align="center">
  <strong>A Self-Evolving AI Agent that Never Gives Up</strong>
</p>

<p align="center">
  <a href="https://github.com/jevisuen/openakita/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" />
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python Version" />
  </a>
  <a href="https://github.com/jevisuen/openakita/releases">
    <img src="https://img.shields.io/github/v/release/jevisuen/openakita" alt="Release" />
  </a>
  <a href="https://github.com/jevisuen/openakita/actions">
    <img src="https://img.shields.io/github/actions/workflow/status/jevisuen/openakita/ci.yml?branch=main" alt="Build Status" />
  </a>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#documentation">Documentation</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <a href="./README_CN.md">📖 中文文档</a>
</p>

---

## What is OpenAkita?

OpenAkita is a **self-evolving AI agent** built on [Anthropic Claude](https://www.anthropic.com/claude) that embodies the **Ralph Wiggum Mode** philosophy: **never give up until the task is done**. When faced with obstacles, it doesn't just fail gracefully—it actively searches for solutions, installs new capabilities from GitHub, or generates its own code to solve problems.

### Why OpenAkita?

- **🔄 Self-Evolving**: Automatically acquires new skills by searching GitHub or generating code
- **💪 Never Gives Up**: Implements Ralph Wiggum Mode for persistent task completion
- **🛠️ Tool Execution**: Native support for shell commands, file operations, and web requests
- **🔌 MCP Integration**: Connect to browsers, databases, and external services via Model Context Protocol
- **💬 Multi-Platform**: Deploy as CLI, Telegram bot, or integrate with DingTalk, Feishu, WeCom
- **🧪 Self-Testing**: 300+ test cases with automatic failure detection and self-repair

## Features

| Feature | Description |
|---------|-------------|
| **Ralph Wiggum Mode** | Persistent execution loop - tasks are not complete until verified |
| **Self-Evolution** | Searches GitHub for skills, installs packages, or generates code on-the-fly |
| **Tool Calling** | Execute shell commands, file operations, HTTP requests with built-in safety |
| **MCP Support** | Integrate with Model Context Protocol servers for browser automation, databases |
| **Multi-Turn Chat** | Context-aware conversations with persistent memory |
| **Auto Testing** | 300+ test cases with automatic verification and self-repair |
| **Multi-Platform** | CLI, Telegram, DingTalk, Feishu, WeCom, QQ support |

## Quick Start

### Prerequisites

- Python 3.11 or higher
- An [Anthropic API key](https://console.anthropic.com/)

### Installation

```bash
# Clone the repository
git clone https://github.com/jevisuen/openakita.git
cd openakita

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install the package
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Configuration

Create a `.env` file with at minimum:

```bash
# Required
ANTHROPIC_API_KEY=your-api-key-here

# Optional: Custom API endpoint
ANTHROPIC_BASE_URL=https://api.anthropic.com

# Optional: Model selection
DEFAULT_MODEL=claude-sonnet-4-20250514
```

### Run

```bash
# Interactive CLI mode
openakita

# Run a single task
openakita run "Create a Python calculator with tests"

# Check agent status
openakita status

# Run self-check
openakita selfcheck
```

## Documentation

| Document | Description |
|----------|-------------|
| [📖 Quick Start](docs/getting-started.md) | Installation and first steps |
| [🏗️ Architecture](docs/architecture.md) | System design and components |
| [🔧 Configuration](docs/configuration.md) | All configuration options |
| [🚀 Deployment](docs/deploy.md) | Production deployment guide |
| [🔌 MCP Integration](docs/mcp-integration.md) | Connect external services |
| [📱 IM Channels](docs/im-channels.md) | Telegram, DingTalk, Feishu setup |
| [🎯 Skills System](docs/skills.md) | Creating and using skills |
| [🧪 Testing](docs/testing.md) | Test framework and coverage |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         OpenAkita                              │
├─────────────────────────────────────────────────────────────┤
│   ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│   │ SOUL.md │  │AGENT.md │  │ USER.md │  │MEMORY.md│       │
│   │(Values) │  │(Behavior)│ │ (User)  │  │(Memory) │       │
│   └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘       │
│        └────────────┴────────────┴────────────┘            │
│                           ↓                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                    Agent Core                        │  │
│   │  ┌─────────┐  ┌──────────┐  ┌─────────────────────┐ │  │
│   │  │  Brain  │  │ Identity │  │   Ralph Loop        │ │  │
│   │  │(Claude) │  │ (Self)   │  │ (Never Give Up)     │ │  │
│   │  └─────────┘  └──────────┘  └─────────────────────┘ │  │
│   └─────────────────────────────────────────────────────┘  │
│                           ↓                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                      Tools                           │  │
│   │  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐        │  │
│   │  │ Shell │  │ File  │  │  Web  │  │  MCP  │        │  │
│   │  └───────┘  └───────┘  └───────┘  └───────┘        │  │
│   └─────────────────────────────────────────────────────┘  │
│                           ↓                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │               Evolution Engine                       │  │
│   │  ┌──────────┐  ┌───────────┐  ┌────────────────┐   │  │
│   │  │ Analyzer │  │ Installer │  │ SkillGenerator │   │  │
│   │  │(Analyze) │  │ (Install) │  │  (Generate)    │   │  │
│   │  └──────────┘  └───────────┘  └────────────────┘   │  │
│   └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Core Documents

OpenAkita uses a unique document-based identity and memory system:

| Document | Purpose |
|----------|---------|
| `identity/SOUL.md` | Core philosophy and values - the agent's "soul" |
| `identity/AGENT.md` | Behavioral specifications and workflows |
| `identity/USER.md` | User profile, preferences, and context |
| `identity/MEMORY.md` | Working memory, task progress, lessons learned |

### Ralph Wiggum Mode

The agent operates in a persistent loop:

```
Task Received → Analyze → Execute → Verify → Repeat until Complete
                   ↓
            On Failure:
            1. Analyze error
            2. Search GitHub for solutions
            3. Install or generate fix
            4. Retry task
```

## Usage Examples

### Multi-Turn Conversation

```
You: My name is John, I'm 25 years old
Agent: Nice to meet you, John!

You: What's my name?
Agent: Your name is John, and you're 25 years old.
```

### Complex Task Execution

```
You: Create a Python calculator project with add, subtract, multiply, divide functions and tests

Agent: Working on your task...
  [Tool] Creating directory structure...
  [Tool] Writing calculator.py...
  [Tool] Writing test_calculator.py...
  [Tool] Running tests...

✅ Task complete! All 16 tests passed.
```

### Self-Evolution

```
You: Analyze this Excel file

Agent: Detecting Excel processing capability needed...
Agent: Searching GitHub for openpyxl...
Agent: Installing openpyxl...
Agent: Installation complete, analyzing file...
```

## Project Structure

```
openakita/
├── identity/               # Agent identity documents
│   ├── SOUL.md             # Agent's core philosophy
│   ├── AGENT.md            # Behavioral specifications
│   ├── USER.md             # User profile
│   └── MEMORY.md           # Working memory
├── src/openakita/
│   ├── core/               # Core modules
│   │   ├── agent.py        # Main agent class
│   │   ├── brain.py        # Claude API integration
│   │   ├── ralph.py        # Ralph loop engine
│   │   ├── identity.py     # Identity system
│   │   └── memory.py       # Memory management
│   ├── tools/              # Tool implementations
│   │   ├── shell.py        # Shell execution
│   │   ├── file.py         # File operations
│   │   ├── web.py          # HTTP requests
│   │   └── mcp.py          # MCP bridge
│   ├── evolution/          # Self-evolution
│   │   ├── analyzer.py     # Requirement analysis
│   │   ├── installer.py    # Auto-installation
│   │   └── generator.py    # Skill generation
│   ├── channels/           # IM integrations
│   │   └── adapters/       # Platform adapters
│   ├── skills/             # Skill system
│   ├── storage/            # Persistence layer
│   └── testing/            # Test framework
├── skills/                 # Local skills directory
├── plugins/                # Plugin directory
├── data/                   # Data storage
└── docs/                   # Documentation
```

## Test Coverage

| Category | Count | Description |
|----------|-------|-------------|
| QA/Basic | 30 | Math, programming knowledge |
| QA/Reasoning | 35 | Logic, code comprehension |
| QA/Multi-turn | 35 | Context memory, instruction following |
| Tools/Shell | 40 | Command execution, file operations |
| Tools/File | 30 | Read, write, search operations |
| Tools/API | 30 | HTTP requests, status codes |
| Search/Web | 40 | HTTP, GitHub search |
| Search/Code | 30 | Local code search |
| Search/Docs | 30 | Documentation search |
| **Total** | **300** | |

## Deployment Options

### CLI Mode (Default)

```bash
openakita
```

### Telegram Bot

```bash
# Enable in .env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-token

# Run
python scripts/run_telegram_bot.py
```

### Docker

```bash
docker build -t openakita .
docker run -d --name openakita -v $(pwd)/.env:/app/.env openakita
```

### Systemd Service

```bash
sudo cp openakita.service /etc/systemd/system/
sudo systemctl enable openakita
sudo systemctl start openakita
```

See [docs/deploy.md](docs/deploy.md) for detailed deployment instructions.

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Quick Contribution Guide

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Setup

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Type checking
mypy src/

# Linting
ruff check src/
```

## Community

- 📖 [Documentation](docs/)
- 🐛 [Issue Tracker](https://github.com/jevisuen/openakita/issues)
- 💬 [Discussions](https://github.com/jevisuen/openakita/discussions)
- 📧 [Email](mailto:contact@example.com)

## Acknowledgments

OpenAkita is built on the shoulders of giants:

- [Anthropic Claude](https://www.anthropic.com/claude) - Core LLM engine
- [Claude Soul Document](https://gist.github.com/Richard-Weiss/efe157692991535403bd7e7fb20b6695) - Soul document inspiration
- [Ralph Playbook](https://claytonfarr.github.io/ralph-playbook/) - Ralph Wiggum Mode philosophy
- [AGENTS.md Standard](https://agentsmd.io/) - Agent behavior specification

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

<p align="center">
  Made with ❤️ by the OpenAkita Team
</p>

<p align="center">
  <a href="#openakita">Back to Top ↑</a>
</p>
