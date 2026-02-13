# OpenAkita Desktop Terminal - User Guide & Feature Overview

> OpenAkita Desktop Terminal — an all-in-one desktop client integrating AI chat, IM channel monitoring, skill management, system status, and step-by-step configuration.

---

## Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [1. Chat](#1-chat)
- [2. IM Channel Monitoring](#2-im-channel-monitoring)
- [3. Skill Management](#3-skill-management)
- [4. System Status](#4-system-status)
- [5. Configuration Wizard](#5-configuration-wizard)
- [Appendix: Shortcuts & Tips](#appendix-shortcuts--tips)

---

## Overview

OpenAkita Desktop Terminal is built with **Tauri 2 + React 18**, connecting to the `openakita serve` backend via HTTP SSE. It supports two operating modes:

- **Local mode**: The desktop app starts the local backend service automatically (requires completing the configuration wizard)
- **Remote mode**: Connect to a running `openakita serve` instance (ideal for development, debugging, or server deployment)

### Interface Layout

```
+------------------------------------------+
| Title Bar                                |
+--------+---------------------------------+
| Sidebar | Topbar: Workspace | Status | Endpoints | Actions |
|         +---------------------------------+
| Chat    |                                 |
| IM      |        Main Content Area        |
| Skills  |                                 |
| Status  |                                 |
| Config >|                                 |
+---------+---------------------------------+
```

**Topbar**: Current workspace name, service status (green/gray indicator), available endpoint count, remote mode badge, connect/disconnect/refresh/language switch buttons.

---

## Quick Start

1. **First Launch**: Expand "Config" in the sidebar and follow the 9-step wizard
2. **Start Service**: Click "Start Service" in the topbar, or run `openakita serve` in terminal
3. **Connect Service**: If you already have a backend running, click "Connect" and enter the address (default `127.0.0.1:18900`)
4. **Start Using**: Click "Chat" in the sidebar to begin

---

## 1. Chat

The Chat page is the core feature of the desktop terminal, providing a full AI conversation experience using the same Agent pipeline (Persona, memory, tools, skills, context compression) as IM/CLI channels.

### 1.1 Basic Conversation

- **Streaming output**: AI responses stream in real-time with Markdown rendering (tables, code blocks, lists)
- **Syntax highlighting**: Code blocks are automatically highlighted (highlight.js)
- **State persistence**: Chat history is preserved when switching to other pages

### 1.2 LLM Endpoint Selection

The model selector is located in the input area:

- **Auto Select** (default): System automatically picks available endpoints, with failover on errors
- **Specific Endpoint**: Choose a specific endpoint (e.g., `gpt-5.2`, `claude-opus`). No auto-fallback; errors are reported directly

### 1.3 Multimodal Input

| Input Type | Action | Supported Formats |
|-----------|--------|-------------------|
| Text | Type directly | - |
| Images | Click attachment or paste | PNG, JPG, JPEG, WebP, GIF |
| Files | Click attachment | PDF, TXT, MD, PY, JS, TS, JSON, CSV, etc. |
| Voice | Click microphone to record | Auto-recorded as WebM and uploaded |
| Paste Image | Ctrl+V from clipboard | Auto-detected and added as attachment |

Attachments show as preview thumbnails with remove buttons.

### 1.4 Slash Commands

Type `/` in the input box to open the command palette, with fuzzy search and keyboard navigation:

| Command | Function | Example |
|---------|----------|---------|
| `/model <endpoint>` | Switch LLM endpoint | `/model gpt-5.2` |
| `/plan` | Toggle Plan mode | `/plan` |
| `/clear` | Clear conversation | `/clear` |
| `/skill <name>` | View skill usage | `/skill docx` |
| `/persona <role>` | Switch Agent persona | `/persona girlfriend` |
| `/agent <name>` | Switch Agent (multi-agent) | `/agent researcher` |
| `/agents` | List available Agents | `/agents` |
| `/help` | Show help | `/help` |

**Available persona presets**: `default`, `business`, `tech_expert`, `butler`, `girlfriend`, `boyfriend`, `family`, `jarvis`

### 1.5 Plan Mode

Click the Plan button in the input area. AI creates a plan before executing complex tasks:

- **Plan creation**: AI breaks down tasks into steps
- **Step tracking**: Each step shows real-time status (pending / in progress / completed / skipped / failed)
- **Visualization**: Plans are displayed as cards in the chat flow

Recommended for tasks involving 3+ tool calls. Disable for simple tasks.

### 1.6 Thinking Process

AI's reasoning process is shown in collapsible panels:

- Collapsed by default, click to expand
- Contains internal reasoning and decision logic
- Supports deep thinking mode

### 1.7 Ask User (Interactive Questions)

When AI needs clarification or confirmation, a question card appears:

- Supports free-text input reply
- AI continues after receiving the answer
- Timeout mechanism (~2 minutes), AI decides on its own after timeout

### 1.8 File & Image Delivery

Files sent by AI via `deliver_artifacts` tool are displayed inline:

| Type | Display |
|------|---------|
| Image | Inline thumbnail, click to open in new window |
| Audio | Inline audio player |
| File | Filename and size, click to download |

### 1.9 Tool Call Display

When AI invokes tools, the call details are shown in the conversation:

- Tool name and parameters (expandable)
- Execution result
- Useful for understanding AI actions and debugging

---

## 2. IM Channel Monitoring

The IM page provides viewing and management of all configured instant messaging channels.

### 2.1 Supported IM Channels

| Channel | Type | Protocol |
|---------|------|----------|
| Telegram | Bot | Telegram Bot API |
| Feishu (Lark) | Enterprise App | WebSocket long connection |
| WeCom (WeChat Work) | Smart Robot | Webhook callback |
| DingTalk | Smart Robot | Stream mode |
| QQ Official Bot | Bot | QQ Open Platform API |
| OneBot | Bot | OneBot v11 protocol |

### 2.2 Channel Status

Each channel displays:

- **Connection status**: Online (green) / Offline (gray) / Config missing (yellow)
- **Session count**: Number of active sessions
- **App type**: e.g., "Smart Robot", "Enterprise App"
- **App icon**: Platform logo

### 2.3 Session Viewer

- Left panel: Channel list + session list for selected channel
- Right panel: Message history for selected session
- Supports text, images, voice, and file messages
- Auto-refreshes every 30 seconds

---

## 3. Skill Management

Skills are extensible capability modules for OpenAkita, following the Agent Skills standard.

### 3.1 Installed Skills

- **Skill list**: Shows all installed skills with name, description, type (system/external)
- **Status indicators**:
  - Enabled / Disabled
  - Config complete / Config incomplete (needs API Key, etc.)
- **Skill configuration**: Expand to show config form:
  - Text input (string)
  - Secret input (with show/hide toggle)
  - Dropdown select
  - Toggle (bool)
  - Number
- **Save config**: Stored in workspace `.env` file

### 3.2 Skill Marketplace

Powered by [skills.sh](https://skills.sh):

- **Real-time search**: Search the global skill library by keywords
- **Install count sorting**: Results sorted by popularity
- **One-click install**: Automatically downloaded and registered locally
- **Default recommendations**: Popular skills shown on entry

### 3.3 Built-in Skills

The system includes 60+ system skills and 29+ external skills:

| Category | Examples |
|----------|---------|
| File Operations | run-shell, read-file, write-file, list-directory |
| Browser Automation | browser-task, browser-navigate, browser-click, browser-screenshot |
| Desktop Automation | desktop-click, desktop-type, desktop-screenshot, desktop-hotkey |
| Document Processing | docx, pdf, pptx, xlsx |
| Development Tools | code-reviewer, changelog-generator, mcp-builder |
| Web Search | web-search, news-search |
| Task Scheduling | schedule-task, list-scheduled-tasks |
| Memory Management | add-memory, search-memory |
| Multimedia | generate-image, video-downloader, image-understanding |

---

## 4. System Status

The Status panel provides a comprehensive view of system health.

### 4.1 Service Status

- **Running state**: Whether the backend is running, with process PID
- **Action buttons**:
  - Start service
  - Stop service (graceful shutdown)
  - Restart service

### 4.2 LLM Endpoint Health Check

- **Endpoint list**: All configured LLM endpoints
- **Status indicators**:
  - Healthy (green): Operating normally
  - Degraded (yellow): Some failures but still available
  - Unhealthy (red): Consecutive failures, in cooldown
- **Latency**: Response time in milliseconds
- **Key status**: Whether API Key is configured
- **Check actions**: Individual or bulk health check
- **Read-only check**: Health checks do not affect cooldown state or interfere with active AI conversations

### 4.3 IM Channel Health Check

- **Channel list**: Telegram, Feishu, WeCom, DingTalk, QQ Official Bot, OneBot
- **Status**: Online / Offline / Not configured / Key missing
- **Independent check**: IM and LLM health checks are fully decoupled

### 4.4 Auto-Start on Boot

- Toggle switch: Enable/disable auto-start on system boot
- Windows: Registry-based
- macOS: LaunchAgent-based
- Starts in background tray mode (no main window popup)

---

## 5. Configuration Wizard

The wizard has 9 steps to guide users from zero to running. Completed steps are marked with green checkmarks and can be freely navigated.

### Step 1: Welcome

- Platform information (OS, architecture)
- Configuration flow overview
- Get started button

### Step 2: Workspace

- Create or select a workspace
- Workspace stores `.env` config, `llm_endpoints.json`, `SOUL.md` identity file
- Multi-workspace support

### Step 3: Python Environment

- **Built-in Python**: One-click embedded Python installation (recommended)
- **System Python**: Detect installed Python paths
- Long paths show full path on hover

### Step 4: Installation

- **Create virtual environment**: One-click venv creation in workspace
- **Install openakita**: From PyPI with:
  - Version selection
  - Mirror source configuration (Tsinghua, Alibaba, etc.)
  - Install options (all / windows / browser extras)
- **Update/Uninstall**

### Step 5: LLM Endpoints

- **Endpoint list**: All configured endpoints
- **Add/Edit**: Modal form with:
  - Endpoint name
  - API Base URL
  - API Key (password mode with eye icon toggle)
  - Model name
  - Capability tags (text / vision / tools / thinking)
  - Timeout settings
- **Delete**: SVG icon button
- At least one endpoint required to proceed

### Step 6: IM Channels

- 6 channels: Telegram, Feishu, WeCom, DingTalk, QQ Official Bot, OneBot
- Each channel:
  - Enable/disable toggle
  - Credential configuration (Bot Token, App ID/Secret, Webhook, etc.)
  - Documentation links (opens in external browser)
- Configuration saved to `.env`

### Step 7: Tools & Skills

- **MCP services**: Browser-Use and other MCP service configuration
- **Desktop automation**: Enable/disable
- **Whisper speech recognition**: Model selection
- **Network proxy**: HTTP/HTTPS/SOCKS proxy configuration
- **GitHub Token**: For skill installation, etc.
- **Installed skill list**: Collapsed display
- Each config item has an info tooltip (hover for description)

### Step 8: Agent & System

- **Agent name**: Custom AI assistant name
- **Persona preset**: Dropdown with 8 preset roles
- **Scheduler**: Scheduled task toggle
- **Session management**: Timeout, concurrency settings
- **Log level**: DEBUG / INFO / WARNING / ERROR
- **Memory system**: Toggle and configuration

### Step 9: Finish

- **Config verification**: One-click check for configuration completeness
- **Start service**: Launch immediately after configuration
- **Uninstall & cleanup**: Remove runtime data

---

## Appendix: Shortcuts & Tips

### Chat Shortcuts

| Shortcut | Function |
|----------|----------|
| `Enter` | Send message |
| `Shift+Enter` | New line |
| `Ctrl+V` | Paste image |
| `/` | Open slash command palette |
| `Up/Down` | Navigate command palette |
| `Esc` | Close command palette |

### Tips

1. **Remote debugging**: Run `openakita serve` in terminal, then click "Connect" in the desktop app. Python code changes take effect immediately (no recompilation needed)
2. **Endpoint cooldown**: If an endpoint fails 3 times consecutively, it enters a 1-hour cooldown. Use the Status panel to manually check and clear
3. **Complex commands**: On Windows, multi-line Python commands are automatically written to temp files, avoiding cmd.exe line truncation issues
4. **Skill search**: The marketplace supports English keywords like `react`, `deploy`, `testing`, `changelog`
5. **Language switch**: Globe icon button in the topbar, instant Chinese/English toggle
6. **Auto-start**: When enabled in Status page, the app runs as a tray service on system boot

---

## Technical Architecture

```
Desktop Terminal (Tauri 2 + React 18)
+-- Frontend (TypeScript/React)
|   +-- App.tsx --- Main framework, routing, state management
|   +-- ChatView.tsx --- AI chat
|   +-- SkillManager.tsx --- Skill management
|   +-- i18n/ --- Internationalization (zh/en)
+-- Rust Backend (Tauri Commands)
|   +-- Workspace management
|   +-- Python environment detection
|   +-- Service start/stop control
|   +-- HTTP proxy (CORS bypass)
+-- Python Backend (FastAPI)
    +-- /api/chat --- SSE streaming chat
    +-- /api/health --- Health check
    +-- /api/skills --- Skill management
    +-- /api/im --- IM channels
    +-- /api/config --- Config read/write
    +-- /api/files --- File service
    +-- /api/upload --- File upload
```

---

*This document is based on OpenAkita v1.10.2, last updated: 2026-02-12*
