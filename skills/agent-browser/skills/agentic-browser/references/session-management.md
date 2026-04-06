# Session Management

Browser sessions for state persistence and parallel browsing.

**Related**: [authentication.md](authentication.md) for login patterns, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [How Sessions Work](#how-sessions-work)
- [Starting a Session](#starting-a-session)
- [Using Session IDs](#using-session-ids)
- [Session State](#session-state)
- [Parallel Sessions](#parallel-sessions)
- [Session Cleanup](#session-cleanup)
- [Best Practices](#best-practices)

## How Sessions Work

Each session maintains an isolated browser context with:
- Cookies
- LocalStorage / SessionStorage
- Browser history
- Page state
- Video recording (if enabled)

Sessions persist across function calls, allowing multi-step workflows.

## Starting a Session

Use `--session new` to create a fresh session:

```bash
RESULT=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://example.com"
}')
SESSION_ID=$(echo $RESULT | jq -r '.session_id')
echo "Session: $SESSION_ID"
```

## Using Session IDs

All subsequent calls use the session ID:

```bash
# Navigate
infsh app run agent-browser --function open --session $SESSION_ID --input '{
  "url": "https://example.com/page2"
}'

# Interact
infsh app run agent-browser --function interact --session $SESSION_ID --input '{
  "action": "click", "ref": "@e1"
}'

# Screenshot
infsh app run agent-browser --function screenshot --session $SESSION_ID --input '{}'

# Close
infsh app run agent-browser --function close --session $SESSION_ID --input '{}'
```

## Session State

### What Persists

Within a session, these persist across calls:
- Cookies (login state, preferences)
- LocalStorage and SessionStorage
- IndexedDB data
- Browser history (for back/forward)
- Current page and DOM state
- Video recording buffer

### What Doesn't Persist

- Sessions don't persist across server restarts
- No automatic session recovery
- Video is only available until close is called

## Parallel Sessions

Run multiple independent sessions simultaneously:

```bash
#!/bin/bash
# Scrape multiple sites in parallel

# Start sessions
RESULT1=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://site1.com"
}')
SESSION1=$(echo $RESULT1 | jq -r '.session_id')

RESULT2=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://site2.com"
}')
SESSION2=$(echo $RESULT2 | jq -r '.session_id')

# Work with each session independently
infsh app run agent-browser --function screenshot --session $SESSION1 --input '{}' &
infsh app run agent-browser --function screenshot --session $SESSION2 --input '{}' &
wait

# Clean up both
infsh app run agent-browser --function close --session $SESSION1 --input '{}'
infsh app run agent-browser --function close --session $SESSION2 --input '{}'
```

### Use Cases for Parallel Sessions

1. **A/B Testing** - Compare different pages or user experiences
2. **Multi-site scraping** - Gather data from multiple sources
3. **Load testing** - Simulate multiple users
4. **Cross-region testing** - Use different proxies per session

## Session Cleanup

Always close sessions when done:

```bash
infsh app run agent-browser --function close --session $SESSION_ID --input '{}'
```

**Why close matters:**
- Releases server resources
- Returns video recording (if enabled)
- Prevents resource leaks

### Error Handling

```bash
#!/bin/bash
set -e

cleanup() {
  infsh app run agent-browser --function close --session $SESSION_ID --input '{}' 2>/dev/null || true
}
trap cleanup EXIT

SESSION_ID=$(infsh app run agent-browser --function open --session new --input '{
  "url": "https://example.com"
}' | jq -r '.session_id')

# ... your automation ...
# cleanup runs automatically on exit
```

## Best Practices

### 1. Store Session IDs

```bash
# Good: Store for reuse
SESSION_ID=$(... | jq -r '.session_id')
infsh ... --session $SESSION_ID ...

# Bad: Parse every time
infsh ... --session $(... | jq -r '.session_id') ...
```

### 2. Close Sessions Promptly

Don't leave sessions open longer than needed. Server resources are limited.

### 3. Use Meaningful Variable Names

```bash
# Good: Clear purpose
LOGIN_SESSION=$(...)
SCRAPE_SESSION=$(...)

# Bad: Generic names
S1=$(...)
S2=$(...)
```

### 4. Handle Session Expiry

Sessions may expire after extended inactivity:

```bash
# Check if session is still valid
RESULT=$(infsh app run agent-browser --function snapshot --session $SESSION_ID --input '{}' 2>&1)
if echo "$RESULT" | grep -q "session not found"; then
  echo "Session expired, starting new one"
  SESSION_ID=$(infsh app run agent-browser --function open --session new --input '{
    "url": "https://example.com"
  }' | jq -r '.session_id')
fi
```

### 5. One Task Per Session

For clarity, use one session per logical task:

```bash
# Good: Separate sessions for separate tasks
LOGIN_SESSION=$(...)  # Handle login
SCRAPE_SESSION=$(...)  # Handle scraping

# Okay for related tasks: One session for a workflow
SESSION=$(...)
# login -> navigate -> extract -> close
```
