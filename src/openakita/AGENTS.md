# OpenAkita Core Backend

Python backend for the OpenAkita multi-agent AI assistant.

## Module Overview

| Module | Purpose |
|--------|---------|
| `core/agent.py` | Main Agent class — orchestrates brain, tools, identity, memory |
| `core/brain.py` | LLM interaction layer (streaming, tool calls, retries) |
| `core/ralph.py` | Ralph Loop — never-give-up execution with failure analysis |
| `core/reasoning_engine.py` | ReAct reasoning engine (stream-based) |
| `core/identity.py` | Loads SOUL.md, AGENT.md, USER.md, MEMORY.md |
| `prompt/builder.py` | Assembles system prompt in layers (identity → runtime → catalogs → memory) |
| `prompt/compiler.py` | Compiles identity files to `identity/runtime/` optimized fragments |
| `agents/orchestrator.py` | Multi-agent message routing, delegation, timeout, health monitoring |
| `agents/factory.py` | Creates Agent instances from AgentProfile, manages instance pool |
| `agents/profile.py` | AgentProfile dataclass, ProfileStore persistence |
| `tools/handlers/` | Tool implementations (one file per tool or tool group) |
| `tools/definitions/` | Tool JSON schemas for LLM function calling |
| `channels/gateway.py` | MessageGateway — unified IM message routing |
| `channels/adapters/` | IM platform adapters (telegram, feishu, dingtalk, etc.) |
| `memory/unified_store.py` | Three-layer memory: core, semantic, conversation traces |
| `skills/loader.py` | Discovers and loads SKILL.md files from multiple directories |
| `api/server.py` | FastAPI app setup |
| `api/routes/` | API endpoints (chat, agents, config, mcp, etc.) |

## Adding a New Tool

1. Create handler in `tools/handlers/your_tool.py` — implement the async function
2. Create definition in `tools/definitions/your_tool.json` — JSON schema for LLM
3. Register in `tools/catalog.py` if not auto-discovered
4. Create SKILL.md in `skills/system/your-tool/SKILL.md` with description and examples

## Adding a New API Route

1. Create route file in `api/routes/your_route.py`
2. Use `APIRouter` with appropriate prefix and tags
3. Register in `api/server.py` via `app.include_router()`

## Adding a New IM Channel

1. Create adapter in `channels/adapters/your_channel.py`
2. Implement the `ChannelAdapter` interface
3. Register in `channels/gateway.py`

## Multi-Agent System

- **Preset agents** are defined in `agents/presets.py` (default, office-doc, code-assistant, browser-agent, data-analyst)
- Sub-agents receive full session history + delegated task as new user message
- Sub-agents get a "Sub-Agent Work Mode" prompt section that disables delegation tools
- Agent profiles are persisted as JSON in `data/agents/profiles/`
- Instance pool key: `{session_id}::{profile_id}`, idle timeout 30 min

## Async Conventions

- All I/O operations must be async (use `aiosqlite`, `httpx`, `aiofiles`)
- Use `asyncio.Queue` for inter-agent communication
- Never call blocking I/O in the event loop — use `asyncio.to_thread()` if unavoidable
