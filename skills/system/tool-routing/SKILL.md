---
name: tool-routing
description: Decision guide for choosing the right tool when operating websites, browsers, and desktop software. Consult when the task involves web interaction, website automation, or desktop app control.
system: true
category: System
priority: high
---

# Tool selection

, or, Use. 

## & Browser operations

```
need? 
│
├─ Read (,, API) 
│ └─ web_fetch (, ) 
│
├─ search
│ └─ web_search (DuckDuckGo search) 
│
├─ need (Click,, ) 
│ │
│ ├─ have opencli adapter? 
│ │ └─ YES → opencli_run (, Chrome ) 
│ │
│ ├─ need? 
│ │ └─ browser_task (Automatic) 
│ │ └─? → Manual browser_navigate + browser_click + browser_type
│ │
│ └─ need? 
│ └─ browser_navigate / browser_click / browser_type
│
└─ need? 
 └─ browser_screenshot → view_image
```

##

```
need? 
│
├─ have cli-anything CLI? (cli_anything_discover ) 
│ └─ YES → cli_anything_run (, Call) 
│
├─ Windows? 
│ └─ desktop_* (UIA/pyautogui GUI Automatic) 
│
└─ have? 
└─ run_shell (Execute) 
```

## Reliable

### () 
1. **opencli_run** — + JSON +
2. **web_fetch** — HTTP get (Read) 
3. **browser_navigate + browser_click/type** — Manual
4. **browser_task** — AI (not) 
5. **call_mcp_tool("chrome-devtools")** — need

### () 
1. **cli_anything_run** — CLI Call
2. **run_shell** —
3. **desktop_* ** — GUI Automatic ( Windows, ) 

##

- **browser_task notneed** — 1 SwitchManual browser_click/type
- **searchnotneed browser_task** — browser_navigate URL
- **have opencli adapter YesUse** — LLM
- **have cli-anything CLI Use** — GUI Automatic 100