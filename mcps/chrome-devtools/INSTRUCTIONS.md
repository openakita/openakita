# Chrome DevTools MCP Usage Guide

## Overview

Chrome DevTools MCP is an official Google browser automation tool that connects to a running Chrome browser, **preserving all login states, cookies, and password manager extensions**.

## Prerequisites

### Option 1: autoConnect (Recommended, Chrome 144+)

1. Open Chrome and visit `chrome://inspect/#remote-debugging`
2. Enable remote debugging
3. Chrome DevTools MCP will automatically connect to the running Chrome instance

### Option 2: Manually enable the debugging port

Launch Chrome from a terminal with the debugging port flag:

**Windows:**
```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**macOS:**
```
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Linux:**
```
google-chrome --remote-debugging-port=9222
```

## Core Tools

- `navigate_page` - Navigate to a specified URL
- `click` - Click a page element
- `fill` / `fill_form` - Fill out forms
- `take_screenshot` - Capture a page screenshot
- `take_snapshot` - Get a structural snapshot of the page
- `evaluate_script` - Execute JavaScript
- `list_pages` - List all open tabs

## Relationship to the Built-in Browser Tool

- Chrome DevTools MCP is suited for scenarios that **require preserved login state**
- The built-in `browser_task` tool uses the browser-use Agent for intelligent automation
- Both can coexist and be chosen based on the task at hand

## Notes

- Requires Node.js v20.19+ and npm
- The `chrome-devtools-mcp` package will be downloaded automatically on first use
- In autoConnect mode, a permission prompt will appear each time a connection is made
