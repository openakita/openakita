---
name: browser-click
description: Click page elements by CSS selector or text content. When you need to click buttons, links, or select options. PREREQUISITE - must use browser_navigate to open target page first.
system: true
handler: browser
tool-name: browser_click
category: Browser
---

# Browser Click

Click on page elements.

## Parameters

| Parameter | Type | Required | Description |
|-----|------|-----|------|
| selector | string | No | CSS selector, e.g. `'#btn-submit'`, `'.button-class'` |
| text | string | No | Visible element text, e.g. `'Submit'`, `'Click here'` |

Provide either `selector` or `text` to identify the target element.

## Examples

**Click by CSS selector**:
```json
{"selector": "#submit-btn"}
```

**Click by visible text**:
```json
{"text": "Submit"}
```

## Prerequisites

- `browser_navigate` — must open the target page first

## Related Skills

- `browser-navigate`: Navigate to a URL
- `browser-type`: Type text into input fields

## Recommendations

For multi-step browser workflows, prefer `browser_task`. It automatically plans and executes browser operations, avoiding the need for manual step-by-step calls.

Example:
```python
browser_task(task="Open Google and search for Python tutorials")
```
