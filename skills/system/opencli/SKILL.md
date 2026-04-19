---
name: opencli
description: Operate websites and Electron apps through CLI commands, reusing Chrome login sessions. Prefer over browser_task for supported sites (GitHub, Bilibili, Twitter/X, YouTube, etc.) — deterministic commands with structured JSON output.
system: true
handler: opencli
tool-name: opencli_run
category: Web
priority: high
---

# OpenCLI - CLI

Via [OpenCLI](https://github.com/jackwener/opencli) and Electron CLI. 
Chrome,, Returns JSON. 

## Core

- ** Chrome ** — /token, Chrome
- **** — browser_task " LLM "
- ** JSON ** — LLM

## When to Use OpenCLI ( browser_task) 

| | Recommendations | |
|------|---------|------|
| have adapter (GitHub, Bilibili ) | `opencli_run` |, JSON |
| need | `opencli_run` | Chrome |
| adapter | `browser_task` / Manual browser_* | OpenCLI notSupports |
| Read | `web_fetch` | |

## Tool

### opencli_list —

```python
opencli_list()
```

### opencli_run — Execute

: `<site> <subcommand>`, opencli. 

```python
opencli_run(command="zhihu hot list")
opencli_run(command="hackernews top")
opencli_run(command="bilibili video info", args=["BV1xx411c7XW"])
```

### opencli_doctor —

```python
opencli_doctor()
```

## Prerequisites

1. opencli: `npm install -g @jackwener/opencli`
2. Chrome inRun
3. Browser Bridge (Run `opencli setup` ) 

##

```
need? 
├─ have opencli adapter → opencli_run () 
├─ need adapter → browser_task → Manual browser_click/type
├─ Read → web_fetch
└─ Search → web_search
```

## Related

- `browser_task` — adapter
- `browser_navigate` —
- `web_fetch` — URL Get
- `web_search` — Search