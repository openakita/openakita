---
name: generate-agents-md
description: "Generate or update AGENTS.md for the current project. Use when user asks to create project guidelines, initialize AGENTS.md, or standardize project conventions."
system: true
handler: system
tool-name: generate_agents_md
category: Development
allowed-tools: ["read_file", "write_file", "run_shell", "list_directory"]
---

# Generate AGENTS.md — Generation

> **AGENTS.md** Yes AI Agent ([agents.md](https://agents.md/)), 
> Cursor, Codex, Copilot, Jules, Windsurf, Aider, opencode 20+ Supports. 
>, have AI. 

## When to Use

- "Generation AGENTS.md", "", "create"
- OpenAkita, have AGENTS.md
- "this", " AI this"

## Workflow

### Step 1:

Use `list_directory` and `read_file`: 

1. ****: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `Gemfile`, `composer.json`, and
2. **README.md**: Readandhave
3. ****: `.eslintrc*`, `ruff.toml`, `pyproject.toml [tool.ruff]`, `.prettierrc`, `tsconfig.json`
4. **CI/CD**: `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`
5. ****: `tests/`, `__tests__/`, `spec/`, `test/`; `jest.config.*`, `vitest.config.*`, `pytest.ini`, `conftest.py`
6. ****:, monorepo (`apps/`, `packages/`, `workspaces`) 
7. **have AGENTS.md**: in, Readinupdate

### Step 2: Generation AGENTS.md

Generation, **and **, not Partial: 

```markdown
# [Project Name]

[]

## Tech Stack

- Language: []
- Framework: []
- Package Manager: [manage]

## Dev Environment Setup

[, Python, Node, install]

## Build & Run

[, Launch, ]

## Testing

[, Run, need]

## Code Style

[lint,, ]

## Project Structure

[, notneedlist]

## Architecture Notes

[,, need]

## PR & Commit Conventions

[, ]

## Known Gotchas

[, ]
```

### Step 3: Write

Use `write_file` Write `AGENTS.md`. 

### Step 4: Monorepo

monorepo (`apps/`, `packages/`, `services/` ), ****YesNoneedalsoGeneration AGENTS.md. AGENTS.md have, not. 

## Important Rules

- ****: AGENTS.md 150, notneedFull
- **have **: notneed, not
- ** AI Agent**: Yes AI, notneed""
- **Execute **:,, lint YesExecute
- **notneed**: notneed API key,, URL
- ****: need (README Yes) 

## Examples

: "Generation AGENTS.md"

→ Execute Step 1-4, Generation. 

: "update AGENTS.md"

→ Readhave AGENTS.md, current statusupdate.