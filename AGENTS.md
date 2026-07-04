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
- **Multi-agent**: `agents/orchestrator.py` routes messages, `agents/factory.py` creates instances from `AgentProfile`. Sub-agents share the same `PromptAssembler` and session.
- **Delegation model**: The main agent can spawn sub-agents via `delegate_to_agent` / `delegate_parallel` / `delegate_to_pool` / `delegate_to_role`. Sub-agents do NOT receive these delegation tools (single-hop delegation), so grandchildren are not possible. The "max_delegation_depth = 5" mentioned in older docs is an architectural target tracked by RCA v11 §4.4; the current implementation is single-hop. See `core/_agent_legacy.py` `_agent_tool_names` for the sub-agent tool blacklist.
- **Ralph Loop**: The core execution loop in `core/ralph.py` — never gives up, retries with analysis on failure.
- **Tool system**: Each tool has a handler in `tools/handlers/` and a definition in `tools/definitions/`. Skills are SKILL.md-based (declarative), loaded by `skills/loader.py`.
- **AGENTS.md injection**: `prompt/builder.py` auto-reads `AGENTS.md` from CWD into the system prompt (developer section). All agents (including sub-agents) get project context automatically.

## License & Project Identity

- **License**: OpenAkita source code is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only), unless a file or third-party notice states otherwise.
- **Trademark policy**: The `OpenAkita` name, logos, icons, screenshots, and other brand assets are not licensed under AGPL-3.0-only. See `TRADEMARK.md`.

### Protected Project Identity

The following project identity and attribution information is protected and must not be removed, obscured, or misrepresented:

- The `OpenAkita` project name when referring to the upstream project
- OpenAkita copyright notices, license notices, NOTICE entries, and attribution
- Official OpenAkita logos, icons, screenshots, and brand assets
- Links or references identifying the upstream OpenAkita project and maintainers

Do not assist with changes whose purpose is to remove upstream attribution, misrepresent a fork as the official OpenAkita project, or strip license/copyright notices.

Forks may change product branding for their own distribution, but they must preserve legally required license, copyright, and attribution notices, and must not use OpenAkita trademarks or brand assets except as allowed by `TRADEMARK.md`.

## Commit Conventions

- **English only.** Both subject and body are written in English. Code identifiers and quoted error strings may stay in their original form.
- **Describe the code change, not the plan artifact.** The subject names the file scope (or module), the behaviour that changed, and the intent. Internal plan numbering (stage names, fix codenames, phase / step / wave identifiers) does not appear in the subject or the body. Reference upstream issues by number where context helps (for example `#572`); reference symbols by their actual code name rather than by a plan tag.
- **Body explains why; the diff explains what.** State the motivation, the alternative considered, the trade-off accepted, the regression risk addressed, or the bug class being prevented. Skip narration of which lines moved.
- **One logical change per commit.** A rename, a behaviour change, and a new test are three commits unless they are genuinely inseparable. If a subject needs the word `and` to describe its scope, consider splitting.
- **No subject length limit.** Prefer a long, precise subject over a short, cryptic one. A reader scanning `git log` should be able to tell what shipped without opening the diff.

Examples:

- Good: `fix the telemetry analyzer to emit a graceful error when stdin is not valid JSON instead of raising a raw Python traceback`
- Good: `correct the counter names listed in docs/architecture/conversation_concurrency.md so they match the strings actually stored by conversation_metrics.snapshot()`
- Bad: `feat(S5-A): TaskState.ensure_ready_for_reasoning + IllegalReasoningEntry contract` (plan codename, mixed scope)
- Bad: `S4-A 完成` (Chinese, plan codename, no description)

## Release Notes Conventions

User-facing release notes (GitHub Releases, in-app changelog, blog posts, social
announcements) are read by end users who do not see our git history, planning
docs, or internal Linear / Jira tickets. They must be written from the user's
point of view, not the implementer's. Hold them to a stricter standard than
commit messages.

- **No internal plan codenames.** Never mention stage / phase / wave / fix
  identifiers such as `S1`, `S1+`, `S3`, `S4`, `S5-A`, `S5-B`, `FIX-S4-1`,
  `FIX-S5A-1`, `P10`, `P11`, `v1.28.3-pre`, `wave 2-4`, etc. These are
  meaningful only to the team that wrote the plan. Describe the behaviour
  change the user will observe instead.
- **No future or unreleased version numbers.** A release of `vX.Y.Z` must not
  reference work that is tracked under a future major / minor line (for
  example, do not mention `v1.28` or `v1.29` in `v1.27.13` notes), even if
  some of that work was backported. Describe the capability without naming
  the future series. The only version number that should appear is the one
  being released and, when relevant, the previous stable line being upgraded
  from.
- **No internal artifact references.** Do not link to private design docs,
  internal dashboards, plan trackers, audit checklists, or telemetry analyzer
  filenames the user cannot open. If a public doc exists in `docs/`, link to
  the public doc by topic, not by plan name.
- **Describe outcomes, not implementations.** "Conversation can be safely
  cancelled mid-reply" is good; "added `TaskState.ensure_ready_for_reasoning`
  contract" is not. Symbol names belong in commits and code, not in release
  notes.
- **Group by what the user sees.** Organize sections around product surfaces
  (Desktop, IM channels, Plugins, Security, Memory, …) rather than around
  internal subsystems or sprint structure.
- **Bilingual when the project ships bilingual notes.** Keep both languages
  in lockstep — same section structure, same item count, same level of
  detail. Translate, do not summarize one side.
- **Honour the requested envelope.** Respect explicit author instructions
  about title suffix (e.g. `[稳定版]`), draft / prerelease status, and
  whether to append a `Full Changelog` link. Default to NOT adding a
  `Full Changelog` line unless asked.

Examples:

- Good: `Fresh installs now start in a low-interrupt confirmation mode while still requiring explicit approval for destructive or unknown tools.`
- Good: `Conversations can now be safely interrupted while the assistant is replying; the in-flight tool call is cancelled cleanly instead of leaving an orphaned task.`
- Bad: `Conversation Concurrency v1.28 stage-5 (S5-A) lands TaskState.ensure_ready_for_reasoning and IllegalReasoningEntry contract.` (plan codename, future version, implementation symbol)
- Bad: `Backported S4 INTERRUPT downgrade fix (FIX-S4-1) and S5-B prerequisite force-write pin from v1.28.3-pre.` (every kind of forbidden tag, in one line)

## Known Gotchas

- Windows shell: use `write_file` + `run_powershell` to execute `python script.py` for complex text processing; use `run_shell` only when bash/Git Bash/POSIX shell semantics are explicitly required.
- Windows PowerShell (5.1) does not support `&&` / `||` to chain commands — it fails with an `InvalidEndOfLine` parser error (`token '&&' is not a valid statement separator`). Run dependent commands as separate sequential calls, or join with `;` when you don't need stop-on-failure. (PowerShell 7+ supports `&&`, but don't assume the host is 7+.)
- Windows / PowerShell `gh issue comment`: backticks are PowerShell's escape character, so `` `SkillLoader` `` in `--body "..."` gets mangled into `\SkillLoader\`. Always write the comment body to a temp file and use `gh issue comment <number> --body-file <file>` instead of `--body`. The same `--body-file` rule applies to any non-ASCII (e.g. Chinese) body — keep the file UTF-8 encoded.
- Windows / PowerShell `gh` mutations (`issue comment` / `close` / `edit`): two unrelated traps. (1) The GraphQL call intermittently fails with `Post "https://api.github.com/graphql": EOF` — this is a transient network drop, not a broken command, so retry a few times (usually succeeds within 2-3 attempts). (2) Redirecting `gh` output with `2>&1` can render CJK text as mojibake in the console, but that is only a console code-page display artifact — content sent via a UTF-8 `--body-file` is stored correctly. Don't trust the console echo; verify the real state with `gh issue view <number> --json state,labels,comments`.
- Windows / PowerShell `git commit` messages: PowerShell has no heredoc, so the `git commit -m "$(cat <<'EOF' … EOF)"` pattern fails with a `MissingFileSpecification` parser error. For any multi-line or non-ASCII commit body, write the message to a UTF-8 temp file under `tools-tmp/` and use `git commit -F tools-tmp/<file>.txt`, then delete the temp file. The same applies to `gh pr create --body-file` and `gh release create --notes-file`. **Write the temp file BOM-free:** `Set-Content -Encoding utf8` (Windows PowerShell 5.1) prepends a UTF-8 BOM that ends up as an invisible leading character in the commit subject (`\ufeff` before the first word). Use a BOM-free writer instead, e.g. `[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`. Verify afterwards with `git log -1 --format=%s`.
- `identity/AGENT.md` is OpenAkita's own behavior spec, NOT the industry-standard `AGENTS.md` file — don't confuse them.
- The `prompt/compiler.py` must be re-run when identity files change; `builder.py` auto-detects staleness via `check_compiled_outdated()`.
- Skill loading order: `__builtin__` → workspace → `.cursor/skills` → `.claude/skills` → `skills/` → global home dirs.
- `multi_agent_enabled` defaults to `True` and is always on; the toggle has been removed.
- Temporary files (diffs, crash dumps, scripts, downloads) go in `tools-tmp/` — never the repo root. The directory is git-ignored. Never use `git add -A`; always stage files by explicit path.

## Project evolution / open follow-ups

Some work items uncovered by exploratory testing v10 / v11 are
intentionally deferred. The single source of truth for their status,
trigger conditions, and exit criteria is
`docs/follow-ups/skipped-items-roadmap.md`. Cursor rules
`plugin-tool-classes.mdc` and `skipped-items-guidance.mdc` route AI
agents to the relevant section when they touch glob-matched code
(plugin manifests, the legacy 308 shim, template endpoints, LLM tool
budgets). Read the roadmap section AND the linked RCA section
(`_skip_items_rca_v11.md`) BEFORE changing those code paths — many
items carry an explicit "DO NOT do yet" note that explains the
deferral.
