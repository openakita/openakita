---
name: browser-task
description: Smart browser task agent - describe what you want done in natural language and it completes automatically. PREFERRED tool for multi-step browser operations like searching, form filling, and data extraction.
system: true
handler: browser
tool-name: browser_task
category: Browser
priority: high
---

# browser_task -

**RecommendationsUse** - thisYesBrowser operations. 

Based on [browser-use](https://github.com/browser-use/browser-use). 

## Usage

```python
browser_task(
task="need ",
 max_steps=15 # Optional,Default 15
)
```

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| task | string | Yes |, |
| max_steps | integer | No | MaximumExecute, Default 15 |

## When to Use () 

- Browser operations
- Search,, Extract
- not
-

## Examples

### Search
```python
browser_task(task="OpenSearch")
```

### Form
```python
browser_task(task="Open example.com, test123")
```

### InfoExtract
```python
browser_task(task="Open GitHub, Get ")
```

###
```python
browser_task(task="OpenSearch, Save")
```

## Browser &

Providesand,: 

| | Recommendations | Description |
|------|---------|------|
| have opencli adapter | `opencli_run` () | + JSON, Chrome |
| need adapter | `browser_task` → Manual | browser_task, click/type Manual |
| Read | `web_fetch` |, |
| Search | `web_search` | DuckDuckGo Search |
| | `browser_task` |,, |
| Browser operations | `browser_navigate`/`browser_click` | |
| Chrome | `call_mcp_tool("chrome-devtools",...)` | Chrome |

: `opencli_run` (have adapter ) → `web_fetch`/`web_search` () → `browser_task` → Manual browser_click/type → chrome-devtools MCP. 

## When to Use

inUse `browser_navigate`, `browser_click`: 

- `browser_task` ExecuteneedManual
- ( `browser_screenshot`) 
- need

## Return Values

```json
{
 "success": true,
 "result": {
"task": "OpenSearch",
 "steps_taken": 5,
"final_result": "Search, Display",
"message": ": OpenSearch"
 }
}
```

## Notes

1. need, 
2. need max_steps
3. UsewillAutomaticLaunch browser () 
4. **Automatic LLM **, API Key

##

- Via CDP (Chrome DevTools Protocol) OpenAkita Launch
- Automatic OpenAkita LLM ( llm_endpoints.json) 
- Based on [browser-use](https://github.com/browser-use/browser-use)

## Advanced: Open Chrome

OpenAkita Open Chrome, needLaunch Chrome: 

**Windows:**
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Linux:**
```bash
google-chrome --remote-debugging-port=9222
```

Launch, OpenAkita willAutomatic, Open. 

## Related

- `browser_screenshot` -
- `browser_navigate` -
- `deliver_artifacts` - Send