# Configuration Guide

This document covers all configuration options for OpenAkita.

## Environment Variables

OpenAkita is configured primarily through environment variables, typically stored in a `.env` file.

### Required Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | `sk-ant-...` |

### API Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | API endpoint URL |
| `DEFAULT_MODEL` | `claude-sonnet-4-20250514` | Model to use |
| `MAX_TOKENS` | `8192` | Maximum response tokens |

### Agent Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_NAME` | `OpenAkita` | Display name |
| `MAX_ITERATIONS` | `100` | Max Ralph loop iterations |
| `AUTO_CONFIRM` | `false` | Auto-confirm dangerous operations |

### Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `data/agent.db` | SQLite database location |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### GitHub Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | - | GitHub PAT for skill search |

### Persona System

| Variable | Default | Description |
|----------|---------|-------------|
| `PERSONA_NAME` | `default` | Active persona preset: `default` / `business` / `tech_expert` / `butler` / `girlfriend` / `boyfriend` / `family` / `jarvis` |

8 built-in presets with distinct communication styles. Users can also switch persona via chat commands. Persona preferences are learned from conversations (LLM-driven trait mining) and promoted to identity files during daily consolidation.

### Living Presence (Proactive Engine)

| Variable | Default | Description |
|----------|---------|-------------|
| `PROACTIVE_ENABLED` | `false` | Enable proactive messaging (greetings, follow-ups, memory recall) |
| `PROACTIVE_MAX_DAILY_MESSAGES` | `3` | Max proactive messages per day |
| `PROACTIVE_MIN_INTERVAL_MINUTES` | `120` | Min interval between proactive messages (minutes) |
| `PROACTIVE_QUIET_HOURS_START` | `23` | Quiet hours start (0-23, no proactive messages) |
| `PROACTIVE_QUIET_HOURS_END` | `7` | Quiet hours end (0-23) |
| `PROACTIVE_IDLE_THRESHOLD_HOURS` | `24` | Hours of user inactivity before triggering idle greeting |

When enabled, the agent proactively sends greetings, task follow-ups, and memory-based reminders. Frequency adapts based on user feedback (fast reply = keep frequency, ignored = reduce).

### Sticker Engine

| Variable | Default | Description |
|----------|---------|-------------|
| `STICKER_ENABLED` | `true` | Enable sticker/emoji pack feature in IM channels |
| `STICKER_DATA_DIR` | `data/sticker` | Sticker data directory (relative to project root) |

Integrates with [ChineseBQB](https://github.com/zhaoolee/ChineseBQB) (5700+ stickers). Each persona preset has a different sticker strategy (e.g., business=never, girlfriend=frequent).

> **Note**: Persona, proactive, and sticker settings can also be changed at runtime via chat commands. Runtime changes are persisted to `data/runtime_state.json` and survive agent restarts.

## IM Channel Configuration

### Telegram

```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token
```

To get a bot token:
1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Use `/newbot` command
3. Follow the prompts
4. Copy the token

### DingTalk

```bash
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=your-client-id
DINGTALK_CLIENT_SECRET=your-client-secret
```

### Feishu (Lark)

```bash
FEISHU_ENABLED=true
FEISHU_APP_ID=your-app-id
FEISHU_APP_SECRET=your-app-secret
```

### WeCom (WeChat Work)

```bash
WEWORK_ENABLED=true
WEWORK_CORP_ID=your-corp-id
WEWORK_AGENT_ID=your-agent-id
WEWORK_SECRET=your-secret
```

### QQ 官方机器人

```bash
QQBOT_ENABLED=true
QQBOT_APP_ID=your-app-id
QQBOT_APP_SECRET=your-app-secret
QQBOT_SANDBOX=false
```

### OneBot（通用协议）

```bash
ONEBOT_ENABLED=true
ONEBOT_WS_URL=ws://127.0.0.1:8080
ONEBOT_ACCESS_TOKEN=              # 可选
```

## Configuration File

You can also use a YAML configuration file at `config/agent.yaml`:

```yaml
# Agent settings
agent:
  name: OpenAkita
  max_iterations: 100
  auto_confirm: false

# Model settings
model:
  provider: anthropic
  name: claude-sonnet-4-20250514
  max_tokens: 8192

# Tools configuration
tools:
  shell:
    enabled: true
    timeout: 30
    blocked_commands:
      - rm -rf /
      - format
  file:
    enabled: true
    allowed_paths:
      - ./
      - /tmp
  web:
    enabled: true
    timeout: 30

# Logging
logging:
  level: INFO
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file: logs/agent.log
```

## Document Configuration

### SOUL.md

The soul document defines core values. Generally should not be modified:

```markdown
# Soul Overview

OpenAkita is a self-evolving AI assistant...

## Core Values
1. Safety and human oversight
2. Ethical behavior
3. Following guidelines
4. Being genuinely helpful
```

### AGENT.md

Behavioral specifications and workflows:

```markdown
# Agent Behavior Specification

## Working Mode
- Ralph Wiggum Mode (never give up)
- Task execution flow
- Validation requirements
```

### USER.md

User-specific preferences (auto-updated):

```markdown
# User Profile

## Preferences
- Language: English
- Technical level: Advanced
- Preferred tools: Python, Git
```

### MEMORY.md

Working memory (auto-managed):

```markdown
# Working Memory

## Current Task
- Description: ...
- Progress: 50%
- Next steps: ...

## Lessons Learned
- Issue X was solved by Y
```

## Command Line Options

```bash
# Override config file
openakita --config /path/to/config.yaml

# Override log level
openakita --log-level DEBUG

# Override model
openakita --model claude-opus-4-0-20250514

# Disable confirmation prompts
openakita --auto-confirm

# Run in specific mode
openakita --mode chat|task|test
```

## POLICIES.yaml Configuration (Policy v2)

OpenAkita's security policy lives in `identity/POLICIES.yaml`. Most fields
have safe defaults; only override what your deployment needs.

### Hot-reload (C18 Phase A)

Watch `POLICIES.yaml` for edits and rebuild the policy engine in-place
without restarting OpenAkita:

```yaml
# identity/POLICIES.yaml
security:
  hot_reload:
    enabled: true              # default: false (opt-in)
    poll_interval_seconds: 5.0 # 0.5 .. 3600
    debounce_seconds: 0.5      # avoid reading half-written editor saves
```

Behavior when `enabled: true`:

- A daemon thread polls the YAML file every `poll_interval_seconds`.
- `mtime` + content-hash dedup skips no-op writes (`touch`,
  `git checkout` to same SHA, etc.).
- Validation failure → keep the **last-known-good** config; write a
  `reload_failed` row to the audit chain. The previous engine keeps
  running so a typo doesn't lock you out.
- Validation success → atomic swap of the engine singleton; write a
  `reload_ok` audit row.

Recommended for: k8s ConfigMap mounts, CI rolling deploys.
Not recommended for: workstation development (manual `openakita serve`
restart is fine).

### Batch confirm aggregation (C18 Phase B)

When `>= 2` confirms accumulate in the same conversation, show one
"Approve all / Deny all" affordance instead of stacking N modals:

```yaml
security:
  confirmation:
    aggregation_window_seconds: 5.0  # default 0 (disabled)
```

Range: `[0, 600]`. Set to `5.0` for the typical UX. The window anchors
on the most recent emission — only confirms that arrived within the
window are batched.

Endpoint: `POST /api/chat/security-confirm/batch` with body
`{session_id, decision, within_seconds?}`. The server clamps client
`within_seconds` to the configured value so a malicious client cannot
resolve every pending confirm with a huge window.

### Environment variable overrides (C18 Phase C)

Five `OPENAKITA_*` variables can override fields without rewriting
`POLICIES.yaml`. Useful for container deploys where the YAML is mounted
read-only.

| ENV variable                  | Field overridden                  | Values                                                       |
| ----------------------------- | --------------------------------- | ------------------------------------------------------------ |
| `OPENAKITA_POLICY_FILE`       | (path) alternate POLICIES.yaml    | absolute or relative path                                    |
| `OPENAKITA_POLICY_HOT_RELOAD` | `hot_reload.enabled`              | `true` / `false` / `1` / `0` / `yes` / `no` / `on` / `off`   |
| `OPENAKITA_AUTO_CONFIRM`      | `confirmation.mode`               | truthy → `trust`; falsy → `default`                          |
| `OPENAKITA_UNATTENDED_STRATEGY` | `unattended.default_strategy`   | `deny` / `auto_approve` / `defer_to_owner` / `defer_to_inbox` / `ask_owner` |
| `OPENAKITA_AUDIT_LOG_PATH`    | `audit.log_path`                  | (path; same safe-path rules as the YAML field)               |

Notes:

- ENV is re-read on every config load → hot-reload (Phase A) picks up
  ENV changes automatically.
- Invalid values (e.g. `OPENAKITA_POLICY_HOT_RELOAD=yes-please`) log a
  loud `[PolicyV2]` warning, leave the YAML default in place, and write
  an `env_override_invalid` audit row.
- Successful overrides write an `env_override_applied` audit row
  listing the (env, path, value) tuples.
- `OPENAKITA_POLICY_FILE` is resolved early (before the override layer
  itself runs) and takes precedence over `settings.identity_path`.

We intentionally do NOT expose `workspace.paths`, `safety_immune.paths`,
`user_allowlist`, or any approval-class table via ENV — those must live
in VCS.

### `--auto-confirm` CLI flag (C18 Phase D)

```bash
openakita --auto-confirm                 # interactive CLI
openakita --auto-confirm run "..."       # one-shot task
openakita --auto-confirm serve           # API server
```

Equivalent to `OPENAKITA_AUTO_CONFIRM=1`. **Destructive
(`mutating_global`) tools and `safety_immune` paths still require
confirm** — this is enforced in the classifier, not in
`confirmation.mode`. So `--auto-confirm` does NOT mean "yolo mode".

The CLI flag is additive: setting it does not clear a pre-existing
`OPENAKITA_AUTO_CONFIRM` env var that operators set elsewhere.

## Advanced Configuration

### Proxy Settings

For users behind firewalls:

```bash
# HTTP proxy
HTTP_PROXY=http://proxy:8080
HTTPS_PROXY=http://proxy:8080

# Or use custom API endpoint
ANTHROPIC_BASE_URL=https://your-proxy-service.com
```

### Rate Limiting

```bash
# Requests per minute
RATE_LIMIT_RPM=60

# Tokens per minute
RATE_LIMIT_TPM=100000
```

### Resource Limits

```bash
# Memory limit (MB)
MEMORY_LIMIT=2048

# CPU cores
CPU_LIMIT=4

# Disk space (MB)
DISK_LIMIT=10240
```

## Validation

To validate your configuration:

```bash
openakita config validate
```

To show current configuration:

```bash
openakita config show
```

## Best Practices

1. **Never commit `.env`** - Add to `.gitignore`
2. **Use separate configs** for dev/staging/production
3. **Rotate API keys** regularly
4. **Enable logging** in production
5. **Set resource limits** to prevent runaway processes

