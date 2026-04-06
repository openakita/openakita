# Command Reference

Complete reference for all agent-browser functions. For quick start, see [SKILL.md](../SKILL.md).

## Base Command

All commands follow this pattern:

```bash
infsh app run agent-browser --function <function> --session <session_id|new> --input '<json>'
```

- `--function`: Function to call (open, snapshot, interact, screenshot, execute, close)
- `--session`: Session ID from previous call, or `new` to start fresh
- `--input`: JSON input for the function

## Functions

### open

Navigate to URL and configure browser. This is the entry point for all sessions.

```bash
infsh app run agent-browser --function open --session new --input '{
  "url": "https://example.com",
  "width": 1280,
  "height": 720,
  "user_agent": "Mozilla/5.0...",
  "record_video": false,
  "show_cursor": false,
  "proxy_url": null,
  "proxy_username": null,
  "proxy_password": null
}'
```

**Input Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | URL to navigate to |
| `width` | int | 1280 | Viewport width in pixels |
| `height` | int | 720 | Viewport height in pixels |
| `user_agent` | string | null | Custom user agent string |
| `record_video` | bool | false | Record video (returned on close) |
| `show_cursor` | bool | false | Show cursor indicator in screenshots/video |
| `proxy_url` | string | null | Proxy server URL |
| `proxy_username` | string | null | Proxy auth username |
| `proxy_password` | string | null | Proxy auth password |

**Output:**

```json
{
  "session_id": "abc123",
  "url": "https://example.com",
  "title": "Example Domain",
  "elements": [...],
  "elements_text": "@e1 [a] \"More information...\" href=\"...\"\n...",
  "screenshot": "<File>"
}
```

### snapshot

Re-fetch page state with `@e` refs. Call after navigation or DOM changes.

```bash
infsh app run agent-browser --function snapshot --session $SESSION_ID --input '{}'
```

**Output:** Same as `open` (url, title, elements, elements_text, screenshot)

### interact

Perform actions on the page using `@e` refs.

```bash
infsh app run agent-browser --function interact --session $SESSION_ID --input '{
  "action": "click",
  "ref": "@e1"
}'
```

**Input Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `action` | string | Action to perform (see Actions table) |
| `ref` | string | Element ref (e.g., `@e1`) |
| `text` | string | Text for fill/type/press/select |
| `direction` | string | Scroll direction: up, down, left, right |
| `scroll_amount` | int | Scroll pixels (default 400) |
| `wait_ms` | int | Wait duration in milliseconds |
| `url` | string | URL for goto action |
| `target_ref` | string | Target ref for drag action |
| `file_paths` | array | File paths for upload action |

**Actions:**

| Action | Required Fields | Description |
|--------|-----------------|-------------|
| `click` | `ref` | Single click |
| `dblclick` | `ref` | Double click |
| `fill` | `ref`, `text` | Clear input and type text |
| `type` | `text` | Type text without clearing |
| `press` | `text` | Press key (Enter, Tab, Escape, etc.) |
| `select` | `ref`, `text` | Select dropdown option by label |
| `hover` | `ref` | Hover over element |
| `check` | `ref` | Check checkbox |
| `uncheck` | `ref` | Uncheck checkbox |
| `drag` | `ref`, `target_ref` | Drag from ref to target_ref |
| `upload` | `ref`, `file_paths` | Upload files to file input |
| `scroll` | `direction` | Scroll page (optional: `scroll_amount`) |
| `back` | - | Go back in browser history |
| `wait` | `wait_ms` | Wait for specified milliseconds |
| `goto` | `url` | Navigate to different URL |

**Output:**

```json
{
  "success": true,
  "action": "click",
  "message": null,
  "screenshot": "<File>",
  "snapshot": {
    "url": "...",
    "title": "...",
    "elements": [...],
    "elements_text": "..."
  }
}
```

### screenshot

Take a screenshot of the current page.

```bash
infsh app run agent-browser --function screenshot --session $SESSION_ID --input '{
  "full_page": true
}'
```

**Input Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `full_page` | bool | false | Capture full scrollable page |

**Output:**

```json
{
  "screenshot": "<File>",
  "width": 1280,
  "height": 720
}
```

### execute

Run JavaScript code on the page.

```bash
infsh app run agent-browser --function execute --session $SESSION_ID --input '{
  "code": "document.title"
}'
```

**Input Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | JavaScript code to execute |

**Output:**

```json
{
  "result": "Example Domain",
  "error": null,
  "screenshot": "<File>"
}
```

**Examples:**

```bash
# Get page title
'{"code": "document.title"}'

# Count elements
'{"code": "document.querySelectorAll(\"a\").length"}'

# Extract text
'{"code": "document.querySelector(\"h1\").textContent"}'

# Get all links
'{"code": "Array.from(document.querySelectorAll(\"a\")).map(a => a.href)"}'

# Scroll to bottom
'{"code": "window.scrollTo(0, document.body.scrollHeight)"}'

# Get computed style
'{"code": "getComputedStyle(document.body).backgroundColor"}'
```

### close

Close the browser session. Returns video if recording was enabled.

```bash
infsh app run agent-browser --function close --session $SESSION_ID --input '{}'
```

**Output:**

```json
{
  "success": true,
  "video": "<File or null>"
}
```

## Key Combinations

For the `press` action, use these key names:

| Key | Name |
|-----|------|
| Enter | `Enter` |
| Tab | `Tab` |
| Escape | `Escape` |
| Backspace | `Backspace` |
| Delete | `Delete` |
| Arrow keys | `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight` |
| Modifiers | `Control`, `Shift`, `Alt`, `Meta` |

**Key combinations:**

```bash
# Ctrl+A (select all)
'{"action": "press", "text": "Control+a"}'

# Ctrl+C (copy)
'{"action": "press", "text": "Control+c"}'

# Shift+Tab (focus previous)
'{"action": "press", "text": "Shift+Tab"}'
```

## Error Handling

When an action fails, `success` is `false` and `message` contains the error:

```json
{
  "success": false,
  "action": "click",
  "message": "Unknown ref: @e99. Run 'snapshot' to get current elements.",
  "screenshot": "<File>",
  "snapshot": {...}
}
```

Common errors:
- `Unknown ref: @eN` - Ref doesn't exist, re-snapshot needed
- `'text' required for fill action` - Missing required field
- `'target_ref' required for drag action` - Missing drag target
- `Timeout 5000ms exceeded` - Element not found or not clickable
