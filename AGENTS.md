# OpenAkita

Open-source multi-agent AI assistant — not just chat, an AI team that gets things done.

## Tech Stack

- **Backend**: Python 3.11+ (FastAPI, asyncio, aiosqlite)
- **Frontend**: React 18 + TypeScript + Vite 6 (in `apps/setup-center/`)
- **Desktop**: Tauri 2.x (Rust shell)
- **LLM**: Anthropic Claude, OpenAI-compatible APIs (30+ providers)
- **IM Channels**: Telegram, Feishu, DingTalk, WeCom, QQ, OneBot

## Dev Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Frontend (only if touching `apps/setup-center/`):
```bash
cd apps/setup-center && npm install
```

## Build & Run

```bash
# CLI interactive mode
openakita

# Run a single task
openakita run "your task here"

# API server mode
openakita serve

# Desktop app (Tauri)
cd apps/setup-center && npm run tauri dev
```

## Testing

```bash
pytest                      # all tests (asyncio_mode=auto)
pytest tests/unit/          # unit tests only
pytest -k "test_brain"      # specific test
pytest --cov=src/openakita  # with coverage
```

Test paths: `tests/` (configured in `pyproject.toml`).

## Code Style

- **Linter**: Ruff (line-length=100, target py311)
- **Rules**: E, F, I, N, W, UP, B, C4, SIM (see `pyproject.toml [tool.ruff.lint]` for ignores)
- **Type checking**: mypy (lenient mode — `ignore_errors = true` for now)
- **Formatting**: Ruff formatter

```bash
ruff check src/             # lint
ruff format src/            # format
mypy src/openakita/         # type check (best-effort)
```

## Project Structure

```
src/openakita/          # Core Python backend
  core/                 #   Agent, Brain, Ralph Loop, ReasoningEngine, Identity
  agents/               #   Multi-agent: Orchestrator, Factory, Profiles, TaskQueue
  prompt/               #   Prompt compilation & assembly (builder, compiler, budget)
  api/routes/           #   FastAPI endpoints
  tools/                #   Tool system (handlers/ + definitions/)
  channels/             #   IM adapters (Telegram, Feishu, DingTalk, etc.)
  memory/               #   Three-layer memory (unified_store, vector, retrieval)
  llm/                  #   LLM client & provider registry
  skills/               #   Skill loader, parser, registry
  evolution/            #   Self-evolution engine
  scheduler/            #   Cron-like task scheduler
apps/setup-center/      # Desktop GUI (Tauri + React)
identity/               # Agent identity (SOUL.md, AGENT.md, POLICIES.yaml)
skills/                 # Skill definitions (system/ + external/)
docs/                   # Documentation
tests/                  # Test suite
```

## Architecture Notes

- **Identity system**: `identity/SOUL.md` (values), `AGENT.md` (behavior), `USER.md` (preferences), `MEMORY.md` (persistent memory). Compiled to `identity/runtime/` for prompt injection.
- **Prompt pipeline**: `prompt/compiler.py` compiles identity files → `prompt/builder.py` assembles system prompt in layers: Identity → Persona → Runtime → Session Rules → AGENTS.md → Catalogs → Memory → User.
- **Multi-agent**: `agents/orchestrator.py` routes messages, `agents/factory.py` creates instances from `AgentProfile`. Sub-agents share the same `PromptAssembler` and session. Max delegation depth = 5.
- **Ralph Loop**: The core execution loop in `core/ralph.py` — never gives up, retries with analysis on failure.
- **Tool system**: Each tool has a handler in `tools/handlers/` and a definition in `tools/definitions/`. Skills are SKILL.md-based (declarative), loaded by `skills/loader.py`.
- **AGENTS.md injection**: `prompt/builder.py` auto-reads `AGENTS.md` from CWD into the system prompt (developer section). All agents (including sub-agents) get project context automatically.

## Commit Conventions

- Commit messages in Chinese or English, describe the "why" not the "what"
- Keep changes focused — one logical change per commit

## Known Gotchas

- Windows shell: use `write_file` + `run_shell python script.py` for complex text processing; avoid PowerShell escaping issues.
- `identity/AGENT.md` is OpenAkita's own behavior spec, NOT the industry-standard `AGENTS.md` file — don't confuse them.
- The `prompt/compiler.py` must be re-run when identity files change; `builder.py` auto-detects staleness via `check_compiled_outdated()`.
- Skill loading order: `__builtin__` → workspace → `.cursor/skills` → `.claude/skills` → `skills/` → global home dirs.
- `multi_agent_enabled` defaults to `True` and is always on; the toggle has been removed.
