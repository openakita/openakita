# Third-Party Notices

This file lists third-party components included in or used by this project,
along with their respective licenses.

---

## Anthropic Agent Skills

**Source:** https://github.com/anthropics/skills  
**License:** Apache License 2.0  
**Copyright:** © Anthropic, PBC

The following skills under `skills/` are imported from Anthropic's official
Agent Skills collection:

- `algorithmic-art` — Algorithmic art creation with p5.js
- `brand-guidelines` — Anthropic brand color & typography guidelines
- `canvas-design` — Visual design for PDF/PNG documents
- `doc-coauthoring` — Structured documentation co-authoring workflow
- `docx` — Word document creation, editing & analysis
- `frontend-design` — Production-grade frontend interface design
- `internal-comms` — Internal communications templates
- `mcp-builder` — MCP server building guide
- `pdf` — PDF manipulation toolkit
- `pptx` — PowerPoint presentation operations
- `skill-creator` — Skill authoring guide
- `slack-gif-creator` — Animated GIF creation for Slack
- `theme-factory` — Theme styling toolkit
- `web-artifacts-builder` — Multi-component web artifact builder
- `webapp-testing` — Web application testing with Playwright
- `xlsx` — Spreadsheet creation, editing & analysis

Each skill directory contains its own `LICENSE.txt` with the full Apache 2.0
license text. These skills are used in accordance with the Apache License 2.0
terms. No endorsement by Anthropic is implied.

---

## Browser Automation

The following packages are bundled for browser automation:

### Playwright

**Source:** https://github.com/Microsoft/playwright-python  
**License:** Apache License 2.0  
**Copyright:** © Microsoft Corporation

Playwright enables reliable browser automation via Chromium, Firefox, and WebKit.
The bundled package includes the Playwright Python bindings and Chromium browser binary.

### browser-use

**Source:** https://github.com/browser-use/browser-use  
**License:** MIT License  
**Copyright:** © browser-use contributors

browser-use provides AI-driven browser automation, enabling LLM agents to
interact with web pages autonomously.

### LangChain OpenAI / LangChain Core

**Source:** https://github.com/langchain-ai/langchain  
**License:** MIT License  
**Copyright:** © LangChain, Inc.

LangChain Core and LangChain OpenAI adapter are used for LLM integration
within the browser automation module.

---

## Community Skills

The following skills are inspired by or adapted from the
[Agent Skills](https://agentskills.io) community ecosystem:

- `changelog-generator` — Changelog generation from git history
- `code-review` — Code review for local changes and pull requests
- `content-research-writer` — Research-driven content writing assistant
- `video-downloader` — YouTube video downloader

---

## OpenAkita Original Skills

All skills under `skills/system/` and the following are original to this
project, licensed under the same [AGPL-3.0-only](LICENSE) terms as the main project:

- `datetime-tool` — Date/time utilities
- `file-manager` — File system operations
- `github-automation` — GitHub automation via Composio MCP
- `gmail-automation` — Gmail automation via Composio MCP
- `google-calendar-automation` — Google Calendar automation via Composio MCP

---

## HTTP Framework

### FastAPI / Uvicorn

**Source:** https://github.com/tiangolo/fastapi / https://github.com/encode/uvicorn
**License:** MIT License
**Copyright:** © Sebastián Ramírez (FastAPI), © Encode OSS (Uvicorn)

FastAPI is used as the HTTP API framework, served by Uvicorn as the ASGI server.

---

## Desktop Runtime Toolchain

The desktop application may include or download runtime/toolchain components
for creating isolated OpenAkita-managed environments. Exact paths, versions,
hashes, ABI tags, and source metadata are recorded in
`resources/bootstrap/manifest.json` in packaged builds and in the exported
`runtime-env-summary.json` diagnostic bundle.

### uv

**Source:** https://github.com/astral-sh/uv
**License:** Apache License 2.0 OR MIT License
**Copyright:** © Astral Software Inc. and contributors

uv is used to create and install dependencies into managed Python virtual
environments.

### CPython / Python

**Source:** https://www.python.org/
**License:** Python Software Foundation License Version 2
**Copyright:** © Python Software Foundation

Packaged desktop backends include the Python runtime artifacts produced by
PyInstaller. Future managed Python seed runtimes must be recorded in the
bootstrap manifest with version, ABI, source, license, and SHA-256 hash.

### Node.js

**Source:** https://nodejs.org/
**License:** MIT License
**Copyright:** © OpenJS Foundation and Node.js contributors

Future managed Node.js seed runtimes used by OpenAkita tools must be recorded
in the bootstrap manifest with source, license, version, and SHA-256 hash.
