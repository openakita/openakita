# Architecture

This document describes the architecture and design principles of OpenAkita.

## Overview

OpenAkita is a self-evolving AI agent built on three core principles:

1. **Never Give Up** - Ralph Wiggum Mode ensures task completion
2. **Self-Evolution** - Automatically acquires new capabilities
3. **Tool Integration** - Native support for system interactions

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      User Interfaces                         │
│  ┌─────┐  ┌──────────┐  ┌────────┐  ┌────────┐  ┌─────┐   │
│  │ CLI │  │ Telegram │  │DingTalk│  │ Feishu │  │ ... │   │
│  └──┬──┘  └────┬─────┘  └───┬────┘  └───┬────┘  └──┬──┘   │
│     └──────────┴────────────┴───────────┴──────────┘       │
│                           ↓                                 │
├─────────────────────────────────────────────────────────────┤
│                    Channel Gateway                          │
│              (Message routing & normalization)              │
├─────────────────────────────────────────────────────────────┤
│                       Agent Core                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                    Identity Layer                     │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐ │  │
│  │  │ SOUL.md │  │AGENT.md │  │ USER.md │  │MEMORY.md│ │  │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘ │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                   Processing Layer                    │  │
│  │  ┌────────────────┐  ┌────────────┐                  │  │
│  │  │Prompt Compiler │  │  Session   │                  │  │
│  │  │ (Stage 1)      │  │  Manager   │                  │  │
│  │  └───────┬────────┘  └────────────┘                  │  │
│  │          ↓                                            │  │
│  │  ┌────────────────┐  ┌─────────────────────┐         │  │
│  │  │ Brain (Claude) │  │    Ralph Loop       │         │  │
│  │  │ (Stage 2)      │  │  (Never Give Up)    │         │  │
│  │  └────────────────┘  └─────────────────────┘         │  │
│  └──────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                       Tool Layer                            │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐           │
│  │ Shell  │  │  File  │  │  Web   │  │  MCP   │           │
│  └────────┘  └────────┘  └────────┘  └────────┘           │
├─────────────────────────────────────────────────────────────┤
│                    Evolution Engine                         │
│  ┌──────────┐  ┌───────────┐  ┌────────────────┐          │
│  │ Analyzer │  │ Installer │  │ SkillGenerator │          │
│  └──────────┘  └───────────┘  └────────────────┘          │
├─────────────────────────────────────────────────────────────┤
│                    Storage Layer                            │
│  ┌────────────┐  ┌─────────────┐  ┌───────────────┐       │
│  │   SQLite   │  │   Sessions  │  │    Skills     │       │
│  └────────────┘  └─────────────┘  └───────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Identity System

OpenAkita uses a document-based identity system:

| Document | Purpose | Update Frequency |
|----------|---------|------------------|
| `identity/SOUL.md` | Core values and philosophy | Rarely |
| `identity/AGENT.md` | Behavioral specifications | Occasionally |
| `identity/USER.md` | User preferences and context | Per-user |
| `identity/MEMORY.md` | Working memory and progress | Per-task |

### 2. Two-Stage Prompt Architecture

OpenAkita uses a two-stage prompt architecture for better task understanding:

**Stage 1: Prompt Compiler**
- Translates user request into structured YAML task definition
- Independent context (destroyed after use, not in main context)
- Logged for debugging but not visible in conversation

```yaml
# Prompt Compiler Output Example
task_goal: "Create voice transcription feature"
inputs:
  provided: "voice file path"
  missing: []
constraints:
  - "Use local Whisper model"
  - "Support Chinese language"
output_requirements:
  - "Transcribed text returned to user"
risks:
  - "Large audio files may be slow"
```

**Stage 2: Main Brain Processing**
- Receives structured task definition from Stage 1
- Full tool access and conversation context
- Executes task with clear understanding

### 3. Brain Module (`core/brain.py`)

The Brain handles all LLM interactions:

```python
class Brain:
    async def think(self, messages, tools) -> Response
    async def stream_think(self, messages, tools) -> AsyncIterator
```

Features:
- Streaming responses
- Tool calling
- Retry with exponential backoff
- Token management
- **Thinking mode** (enabled by default for complex reasoning)
- **Full interaction logging** (system prompt, messages, tool calls)

### 3. Ralph Loop (`core/ralph.py`)

Implements the "never give up" philosophy:

```
while not task_complete:
    result = execute_step()
    if result.failed:
        analyze_failure()
        if can_fix_locally:
            apply_fix()
        else:
            search_github_for_solution()
            if found:
                install_and_retry()
            else:
                generate_solution()
    verify_progress()
    save_to_memory()
```

### 4. Tool System (`tools/`)

Built-in tools:

| Tool | Module | Description |
|------|--------|-------------|
| Shell | `shell.py` | Execute system commands |
| File | `file.py` | Read/write/search files |
| Web | `web.py` | HTTP requests |
| MCP | `mcp.py` | External service bridge |

### 5. Evolution Engine (`evolution/`)

Self-evolution capabilities:

- **Analyzer**: Determines what capability is needed
- **Installer**: Installs packages from PyPI/GitHub
- **Generator**: Creates new skills dynamically

### 6. Channel System (`channels/`)

Multi-platform support:

```python
class BaseChannel(ABC):
    async def receive_message() -> Message
    async def send_response(response: str)
```

Adapters: Telegram, DingTalk, Feishu, WeCom, QQ

## Data Flow

### Message Processing

```
1. User sends message via channel
2. Channel adapter normalizes message
3. Media preprocessing:
   - Voice: Download → Whisper transcription → Text
   - Image: Download → Base64 encode → Multimodal input
4. Session manager retrieves/creates context
5. Prompt Compiler (Stage 1) structures the request
6. Brain (Stage 2) processes with tools available
7. Ralph loop ensures completion
8. Response recorded to session history
9. Response sent back through channel
```

### Chat History

All messages are recorded to session:

| Role | Source | Description |
|------|--------|-------------|
| `user` | Incoming message | User's text/voice/image |
| `assistant` | Agent response | LLM's reply |
| `system` | Scheduled tasks, notifications | System-generated messages |

The `get_chat_history` tool allows LLM to query these records.

### Tool Execution

```
1. Brain decides tool is needed
2. Tool registry validates request
3. Tool executes with safety checks
4. Result returned to Brain
5. Brain continues reasoning
```

### Self-Evolution

```
1. Task requires unknown capability
2. Analyzer identifies gap
3. Search GitHub for existing solution
4. If found: Installer adds to system
5. If not: Generator creates new skill
6. Retry original task
```

## Design Principles

### 1. Async-First

All I/O operations use `async/await`:

```python
async def process_message(self, message: str) -> str:
    response = await self.brain.think(messages)
    return response.content
```

### 2. Fail-Safe Execution

Tools have multiple safety layers:

```python
@safe_execute
@require_confirmation(dangerous=True)
@timeout(seconds=30)
async def run_shell_command(cmd: str) -> str:
    ...
```

### 3. Stateless with Persistence

- Each request is stateless
- State persisted to SQLite/files
- Fresh context loaded per request

### 4. Modular and Extensible

- Skills can be added dynamically
- Channels follow adapter pattern
- Tools implement common interface

## File Structure

```
src/openakita/
├── core/           # Core agent logic
│   ├── agent.py    # Main Agent class
│   ├── brain.py    # LLM interaction
│   ├── ralph.py    # Ralph loop
│   ├── identity.py # Identity management
│   └── memory.py   # Memory operations
├── tools/          # Tool implementations
├── skills/         # Skill system
├── channels/       # IM integrations
├── evolution/      # Self-evolution
├── storage/        # Persistence
├── scheduler/      # Task scheduling
├── sessions/       # Session management
└── testing/        # Test framework
```

## Performance Considerations

- **Streaming**: Responses stream as they're generated
- **Caching**: Frequently used data is cached
- **Async I/O**: Non-blocking operations throughout
- **Batch Processing**: Multiple operations combined when possible

## Security Model

See [SECURITY.md](../SECURITY.md) for detailed security information.

Key points:
- Command confirmation for dangerous operations
- Path restrictions for file access
- Input validation and sanitization
- Rate limiting on API calls
