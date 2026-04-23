# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-04-23

### Added — External-CLI agents, presets, and session APIs

- Added `AgentType.EXTERNAL_CLI` profiles with provider-specific adapters for
  Claude Code, Codex, OpenCode, Gemini, GitHub Copilot, Droid, Cursor, Qwen,
  and Goose. External-CLI agents now run through a shared concurrency limiter
  (`external_cli_max_concurrent`) and preserve turn/session bookkeeping.
- Setup Center now detects installed CLIs, offers a CLI-backed agent creation
  wizard, ships starter presets (`claude-code-pair`, `codex-writer`,
  `local-goose`, `multi-cli-planner`), and surfaces CLI providers in
  Extensions plus the agent graph UI.
- Added read-only session browsing APIs under `/api/sessions/external-cli/*`,
  including installed-provider detection, per-provider historical session
  listing, and byte-offset paginated message streaming for Claude Code / Codex
  transcripts.

### Added — Scheduler playbook autorun tasks

- Scheduler now supports `system:autorun_playbook` tasks with `PlaybookRun`
  orchestration, worktree acquisition, document-loop execution, checkbox reset
  helpers, stall guarding, and `autorun:state` WebSocket broadcasts.
- `SchedulerView` can create/edit playbook tasks, choose an agent profile,
  reorder docs, submit autorun jobs, and render live progress cards from
  `autorun:state` events.

### Added — Live progress streaming for agent runs

- Sub-agent runs can now stream live progress over WebSocket into the chat UI
  instead of only reporting a final summary after completion.
- Added progress-aware timeout utilities so long-running agent / orchestration
  flows time out on inactivity rather than a fixed wall-clock deadline.

### Added — Per-agent external CLI environment variables

- `AgentProfile.cli_env: dict[str, str]` lets each external-CLI agent own a
  private set of environment variables passed to its subprocess. Values support
  `${VAR}` references that resolve from the OpenAkita server process env at
  spawn time. Editable in the Setup Center's external-CLI edit drawer under a
  new **Environment** section.

### Changed — External-CLI subprocess env is now allow-listed

- Previously every `os.environ` variable was inherited, including OpenAkita's
  own LLM provider secrets (`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`,
  `OPENAI_API_KEY`, …) which leaked into every external CLI and broke CLIs
  that use their own credentials.
- External-CLI subprocesses now receive only POSIX essentials (`HOME`, `PATH`,
  `LANG`, `LC_*`, `TERM`, `TMPDIR`, `XDG_*`, `SSH_AUTH_SOCK`, `GPG_AGENT_INFO`,
  …) plus whatever the agent's `cli_env` explicitly sets.
- **Migration:** if a Claude Code / Codex / Goose agent was relying on an
  inherited `ANTHROPIC_API_KEY` or similar, add it to that agent's Environment
  section in Setup Center. OpenAkita's own LLM access is unaffected.

### Changed — English is now the default user-facing language

- Setup Center, Tauri desktop error messages, agent/org/runtime views,
  toasts/modals, shared constants, MCP/identity/token-stats screens, and
  user-facing backend strings were localized to English.
- English is now forced as the default UI language; auto-translation is
  disabled; legacy `*_zh` parallel fields were dropped from backend responses;
  `ReAct` transition enums now use `StrEnum`.

### Changed — Stream failover and runtime environment handling

- Empty or semantic-noop provider streams now trigger failover instead of
  silently ending without a usable answer; thinking-capability handling was
  tightened at the same time.
- Cross-platform `PATH` resolution now explicitly covers Linux, and
  external-CLI env assembly merges allow-listed base variables with per-agent
  overrides.

### Changed — IM and desktop packaging compatibility

- WhatsApp support was upgraded from Baileys 6.x to 7.x for LID / ESM / Node 20
  compatibility.
- IM dependencies were refreshed, QQ voice uploads accept more direct-upload
  formats, and the desktop installer now migrates legacy installs under the new
  `OpenAkitaDesktop` product name.

### Fixed — External-CLI execution stability

- Fixed profile persistence / clone / factory gaps so external-CLI fields
  survive CRUD, cloning, and self-improvement bridge handoff.
- Session routes are now mounted under the sessions API, orchestrator state is
  exposed correctly for external-CLI agents, and autorun prompts include the
  active playbook context.
- Streaming progress is now emitted consistently across Claude Code, Codex,
  Goose, OpenCode, Gemini, Copilot, Droid, Cursor, and Qwen; stderr is drained
  concurrently; cancellation now reaps subprocesses correctly and fallback
  triggers on provider-side failures.

### Fixed — Reliability and safety follow-ups

- Prevented fire-and-forget conversation lifecycle tasks from being garbage
  collected before completion.
- Fixed a supervisor false-positive that could terminate sessions after five
  tool calls even when the agent was still making progress.
- File creation is no longer hard-denied by security confirmation, and
  repeated shell confirmation prompts were reduced.

### Fixed — 插件加载系统三件套

- **多插件 `task_manager.py` / `providers.py` 同名子模块在 `sys.modules`
  互相覆盖**：21 个插件中有 19 个使用裸名 `from task_manager import X`
  导入自己目录下的 `task_manager.py`；先加载的插件抢占
  `sys.modules["task_manager"]`,后续插件命中缓存导致
  `ImportError: cannot import name 'XxxTaskManager'`。`_load_python_plugin`
  在 `exec_module` 前增加 shadowed 机制：扫描本插件目录的顶层 `.py` /
  包名,把 `sys.modules` 里属于其他插件的同名条目先弹出,让本插件的
  bare import 能沿 `sys.path` 找到自己的文件。已加载兄弟插件持有的
  Python 对象引用照常工作。
- **`PluginManager._failed` 在卸载/移除后从不清理**：UI 长期残留"插件
  加载失败"幽灵条目。`unload_plugin` 入口立即 `pop _failed[plugin_id]`;
  纯 failed-state 的卸载现在返回 `True`(原先返回 `False`,语义更准确,
  现有测试不受影响)。`uninstall_plugin` 路由的 removed 分支额外调用
  新增的 `pm.forget_failure(plugin_id)` 兜底。
- **`seedance-video` 缺失 `prompt_optimizer.py` 导致
  `ModuleNotFoundError`**：Sprint 18 收尾依据
  [docs/sprint18-cleanup-assessment.md](docs/sprint18-cleanup-assessment.md)
  §B8 的错误 grep 结论删除了该文件,但 `plugin.py` 第 39–46 行 import
  并在 4 个 REST 端点(`/prompt-guide`、`/prompt-templates`、
  `/prompt-formulas`、`/prompt-optimize`)实际使用 6 个符号。已从 commit
  `f04787f9^` 还原 291 行原版本。SDK 的
  `openakita_plugin_sdk.contrib.prompt_optimizer.PromptOptimizer` 是另一
  套泛化 API（无 Seedance 静态字典、签名不同）,不能替换;后续若想接 SDK
  须按 §B8 推荐方案做拆分。

### Documentation

- `docs/sprint18-cleanup-assessment.md` §B8.A — 标注 grep 结论错误 +
  撤销动作 + 复核命令(`rg --pcre2 -nP "from\s+prompt_optimizer"`)
- `docs/plugin-2.0-handover.md` — 移除 `prompt_optimizer.py` 删除线,
  改写为"已还原"
- 自检 21 个插件的同名子模块碰撞清单(归档于本次 PR plan)

## [1.27.9] - 2026-04-20

> Plugin Sprint 7-18 整合发布。完成 SDK `contrib/` 6 件套补齐 + 8 个新 AI-媒体插件
> 上线 + 老插件全套加固。所有改动覆盖单元测试，主仓 + SDK + 20 个插件总计 **1180+
> 测试 / 1 skipped / 零回归**。详见 [docs/sprint18-cleanup-assessment.md](docs/sprint18-cleanup-assessment.md)。

### Added — SDK `openakita-plugin-sdk/contrib/` 6 件套（Sprint 8）

- **`quality_gates`** — G1-G5 多轨闸门（含 `slideshow_risk` D2.1）
- **`intent_verifier`** — `verify_delivery` 出参与用户意图比对（D2.2 + P3.5）
- **`provider_score`** — 多 provider 排序与裁判（D2.5）
- **`verification`** — `Verification` + `LowConfidenceField` 协议（D2.10）
- **`error_coach`** — 错误归因 + 可执行建议（D2.11 + D2.14 双段）
- **`prompt_optimizer`** — 通用 LLM 提示词优化器（P3.1-P3.5 共 5 条 prompt）

补充模块：`upload_preview`、`agent_loop_config`、`base_task_manager`、`base_vendor_client`、
`ffmpeg`（`run_ffmpeg` + `auto_color_grade_filter` + `signalstats sampling ±8% clamp`，B7）、
`source_review`（D2.3）、`slideshow_risk`（D2.1）、`cost_tracker` / `checkpoint`
（健康检查通过，标记为 🅿️ waiting-for-consumer）。

### Added — 8 个新 AI-媒体插件

| Sprint | 插件 | 卖点 |
|--------|------|------|
| Sprint 11 | `transcribe-archive` | parallel_executor + checkpoint + cost_tracker (95 测试) |
| Sprint 12 | `bgm-mixer` | madmom beat-aware ducking + ffmpeg 切点对齐 (68 测试) |
| Sprint 13 | `video-color-grade` | SDK auto_color_grade 薄封装 (49 测试) |
| Sprint 13 | `smart-poster-grid` | 4 尺寸编排 + verification (50 测试) |
| Sprint 14 | `video-bg-remove` | RVM ckpt + onnxruntime + dep_gate (72 测试) |
| Sprint 15 | `ppt-to-video` | LibreOffice headless + tts-studio 跨插件调用 (79 测试) |
| Sprint 16 | `local-sd-flux` | ComfyUI HTTP 客户端 + 5 条 workflow + provider_score (99 测试) |
| Sprint 17 | `shorts-batch` | 批量 shorts 编排 + slideshow_risk D2.1 (51 测试) |
| Sprint 17 | `dub-it` | 视频配音翻译 5 阶段流水线 + source_review D2.3 (52 测试) |

每个新插件均自带 `SKILL.md` + `README.md` + `ui/dist/index.html` 占位 + 完整测试。

### Added — 老插件加固（Sprint 7 + Sprint 9 真实复用）

- `seedance-video` — 全套 SQL 白名单 / spawn_task / async unload / SKILL.md / 30+ 测试
- `storyboard` — 接 `gate_g5_slideshow_risk` + `intent_verifier.verify_delivery`
- `tongyi-image` — 接 `error_coach` 双段错误归因
- `bgm-suggester` — 接 `verification` 字段（self-check style match）
- `cost_translation_map.yaml` — 给 tongyi-image / seedance-video / bgm-suggester 各加一条人话翻译

### Changed

- 主 README 「Plugin System」章节新增「Bundled AI-Media Plugins (20)」表，统计 913 个测试
- `docs/plugin-2.0-handover.md` — 标记 seedance `prompt_optimizer.py` 已删除
- `openakita-plugin-sdk/docs/contrib.md` — `cost_tracker` / `checkpoint` 标 🅿️ waiting-for-consumer

### Removed

- `plugins/seedance-video/prompt_optimizer.py` — 孤儿文件（无 import / 无测试，逻辑已被 SDK
  `contrib.prompt_optimizer.PromptOptimizer` 泛化覆盖）。详见
  [Sprint 18 评估 §B8](docs/sprint18-cleanup-assessment.md#b8-prompt_optimizer-迁移评估)。

### Documentation

- `docs/sprint18-cleanup-assessment.md` — A1+ tongyi / A1+ seedance / B8 prompt_optimizer
  / SkillManifest loader 4 项迁移评估
- `docs/refs-extraction-report.md` — D1-D9 9 个插件 `findings/d_class_copy_points/` 抽点报告（Sprint 10）
- 各插件 `SKILL.md` + `README.md` 全套补齐

### Tests

- SDK：367 passed / 1 skipped
- 20 个插件：813+ passed
- **总计：1180+ 测试，零回归**

## [1.2.1] - 2026-02-05

### Added
- **Feishu (飞书) Full Support** - Text, voice, image, and file messages fully tested
- **Plan Mode Documentation** - Comprehensive guide for multi-step task management
- **Community Section** - WeChat group, Discord, X (Twitter) contact info

### Changed
- **Version Management** - Unified version source from `pyproject.toml`
  - `__init__.py` now reads version dynamically
  - README badge auto-syncs with GitHub releases
- **README Enhancements** - Added Plan Mode workflow diagrams and examples

### Fixed
- WeChat group QR code image path typo

## [1.2.0] - 2026-02-02

### Added
- **Scheduled Task Management Enhancement**
  - New `update_scheduled_task` tool for modifying task settings without deletion
  - `notify_on_start` / `notify_on_complete` notification switches
  - Clear distinction between "cancel task" vs "disable notification" vs "pause task"
  - Detailed tool descriptions with usage examples
- **ToolCatalog Progressive Disclosure**
  - `get_tool_info` tool for querying detailed tool parameters
  - `list_available_tools` for discovering system capabilities
  - Level-based tool disclosure (basic → advanced)
- **Telegram Proxy Configuration**
  - `TELEGRAM_PROXY` environment variable support
  - HTTP/HTTPS/SOCKS5 proxy support for restricted networks

### Fixed
- **IM Session Tool Usage** - Fixed Telegram sessions missing tool definitions, causing bot to only respond with "I understand" without taking action
- **Task Notification Format** - Removed over-escaping in scheduled task notifications that caused garbled Markdown
- **System Prompt Tool Guidelines** - Strengthened tool usage requirements: "Must use tools immediately upon receiving tasks"

### Changed
- Enhanced shell tool security checks
- Improved scheduled task tool descriptions with clear concept differentiation

## [1.1.0] - 2026-02-02

### Added
- **MiniMax Interleaved Thinking Support**
  - New `ThinkingBlock` type in `llm/types.py` for model reasoning content
  - Anthropic provider parses `thinking` blocks from MiniMax M2.1 responses
  - Brain converts `ThinkingBlock` to tagged `TextBlock` for Pydantic compatibility
  - Agent preserves thinking blocks in message history for MiniMax context requirements
- **Enhanced Browser Automation Tools** (`tools/browser_mcp.py`)
  - `browser_status`: Get browser state (open/closed, current URL, tab count)
  - `browser_list_tabs`: List all open tabs with index, URL, title
  - `browser_switch_tab`: Switch to a specific tab by index
  - `browser_new_tab`: Open URL in new tab (without overwriting current page)
  - Smart blank page reuse: First `browser_new_tab` reuses `about:blank` instead of creating extra tab
- Project open source preparation
- Comprehensive documentation suite
- Contributing guidelines
- Security policy
- **Unified LLM Client Architecture** (`src/openakita/llm/`)
  - `LLMClient`: Central client managing multi-endpoint, capability routing, failover
  - `LLMProvider` base class with Anthropic and OpenAI implementations
  - Unified internal types: `Message`, `Tool`, `LLMRequest`, `LLMResponse`, `ContentBlock`
  - Anthropic-like format as internal standard, automatic conversion for OpenAI-compatible APIs
- **LLM Endpoint Configuration** (`data/llm_endpoints.json`)
  - Centralized endpoint config: name, provider, model, API key, capabilities, priority
  - Supports multiple providers: Anthropic, OpenAI, DashScope, Kimi (Moonshot), MiniMax
  - Capability-based routing: text, vision, video, tools
  - Priority-based failover with automatic endpoint selection
- **LLM Endpoint Cooldown Mechanism**
  - Failed endpoints enter 3-minute cooldown period
  - Automatically skipped during cooldown, uses fallback endpoints
  - Auto-recovery after cooldown expires
  - Applies to auth errors, rate limits, and unexpected errors
- **Text-based Tool Call Parsing**
  - Fallback for models not supporting native `tool_calls`
  - Parses `<function_calls>` XML patterns from text responses
  - Seamless degradation without code changes
- **Multimodal Support**
  - Image processing with automatic format detection and base64 encoding
  - Video support via Kimi (Moonshot) with `video_url` type
  - Capability-based routing: video tasks prioritize Kimi

### Changed
- README restructured for open source
- **Browser MCP uses explicit context** for multi-tab support
  - Changed from `browser.new_page()` to `browser.new_context()` + `context.new_page()`
  - Enables creating multiple tabs in same browser window
- **`browser_open` default `visible=True`** - Browser window visible by default for user observation
- **Brain Refactored as Thin Wrapper**
  - Removed direct Anthropic/OpenAI client instances
  - All LLM calls now go through `LLMClient`
  - `messages_create()` and `think()` delegate to `LLMClient.chat()`
- **Message Converters** (`src/openakita/llm/converters/`)
  - `messages.py`: Bidirectional conversion between internal and OpenAI formats
  - `tools.py`: Tool definition conversion, text tool call parsing
  - `multimodal.py`: Image/video content block conversion
- **httpx AsyncClient Event Loop Fix**
  - Tracks event loop ID when client is created
  - Recreates client if event loop changes (fixes "Event loop is closed" error)
  - Applied to both Anthropic and OpenAI providers
- **Cross-platform Path Handling**
  - System prompt suggests `data/temp/` instead of hardcoded `/tmp`
  - Dynamic OS info injected into system prompt
  - `tempfile.gettempdir()` used in self-check module
- **Context Compression: LLM-based instead of truncation**
  - `_compress_context()` now uses LLM to summarize early messages
  - `_summarize_messages()` passes full content to LLM (no truncation)
  - Recursive compression when context still too large
  - Never directly truncates message content
- **Full Logging Output (no truncation)**
  - User messages logged completely
  - Agent responses logged completely
  - Tool execution results logged completely
  - Task descriptions logged completely
  - Prompt compiler output logged completely
- **Tool Output: Full content display**
  - `list_skills` shows full skill descriptions
  - `add_memory` shows full memory content
  - `get_chat_history` shows full message content
  - `executed_tools.result_preview` shows full result
- **Identity/Memory Module: No truncation**
  - Current task content preserved fully
  - Success patterns preserved fully
- **LLM Failover Optimization**
  - With fallback endpoints: switch immediately after one failure
  - Single endpoint: retry multiple times (default 3)
- **Thinking as Parameter, not Capability**
  - `thinking` removed from endpoint capability filtering
  - Now treated as transmission parameter only
- **Kimi-specific Adaptations**
  - `reasoning_content` field support in Message/LLMResponse types
  - Automatic extraction and injection for Kimi multi-turn tool calls
  - `thinking.type` set to `enabled` per official documentation

### Fixed
- **Session messages not persisting** - Added `session_manager.mark_dirty()` calls in gateway after `session.add_message()` to ensure voice transcriptions and user messages are saved
- **Playwright multi-tab error** - Fixed "Please use browser.new_context()" error when opening multiple tabs

## [0.6.0] - 2026-01-31

### Added
- **Two-stage Prompt Architecture (Prompt Compiler)**
  - Stage 1: Translates user request into structured YAML task definition
  - Stage 2: Main LLM processes the structured task
  - Improves task understanding and execution quality

- **Autonomous Evolution Principle**
  - Agent can install/create tools autonomously
  - Ralph Wiggum mode: never give up, solve problems instead of returning to user
  - Max tool iterations increased to 100 for complex tasks

- **Voice Message Processing**
  - Automatic voice-to-text using local Whisper model
  - No API calls needed, fully offline
  - Default: base model, Chinese language

- **Chat History Tool (`get_chat_history`)**
  - LLM can query recent chat messages
  - Includes user messages, assistant replies, system notifications
  - Configurable limit and system message filtering

- **Telegram Pairing Mechanism**
  - Security pairing code required for new users
  - Paired users saved locally
  - Pairing code saved to file for headless operation

- **Proactive Communication**
  - Agent acknowledges received messages before processing
  - Can send multiple progress updates during task execution
  - Driven by LLM judgment, not keyword matching

- **Full LLM Interaction Logging**
  - Complete system prompt output in logs
  - All messages logged (not truncated)
  - Full tool call parameters logged
  - Token usage tracking

### Changed
- **Thinking Mode**: Now enabled by default for better quality
- **Telegram Markdown**: Switched from MarkdownV2 to Markdown for better compatibility
- **Message Recording**: All sent messages now recorded to session history
- **Scheduled Tasks**: Clear distinction between REMINDER and TASK types

### Fixed
- Telegram MarkdownV2 parsing errors with tables and special characters
- Multiple notification issue with scheduled tasks
- Voice file path not passed to Agent correctly
- Tool call limit too low for complex tasks

## [0.5.9] - 2026-01-31

### Added
- Multi-platform IM channel support
  - Telegram bot integration
  - DingTalk adapter
  - Feishu (Lark) adapter
  - WeCom (WeChat Work) adapter
  - QQ (OneBot) adapter
- Media handling system for IM channels
- Session management across platforms
- Scheduler system for automated tasks

### Changed
- Improved error handling in Brain module
- Enhanced tool execution reliability
- Better memory consolidation

### Fixed
- Telegram message parsing edge cases
- File operation permissions on Windows

## [0.5.0] - 2026-01-15

### Added
- Ralph Wiggum Mode implementation
- Self-evolution engine
  - GitHub skill search
  - Automatic package installation
  - Dynamic skill generation
- MCP (Model Context Protocol) integration
- Browser automation via Playwright

### Changed
- Complete architecture refactor
- Async-first design throughout
- Improved Claude API integration

## [0.4.0] - 2026-01-01

### Added
- Testing framework with 300+ test cases
- Self-check and auto-repair functionality
- Test categories: QA, Tools, Search

### Changed
- Enhanced tool system with priority levels
- Better context management

### Fixed
- Memory leaks in long-running sessions
- Shell command timeout handling

## [0.3.0] - 2025-12-15

### Added
- Tool execution system
  - Shell command execution
  - File operations (read/write/search)
  - Web requests (HTTP client)
- SQLite-based persistence
- User profile management

### Changed
- Restructured project layout
- Improved error messages

## [0.2.0] - 2025-12-01

### Added
- Multi-turn conversation support
- Context memory system
- Basic CLI interface with Rich

### Changed
- Upgraded to Anthropic SDK 0.40+
- Better response streaming

## [0.1.0] - 2025-11-15

### Added
- Initial release
- Basic Claude API integration
- Simple chat functionality
- Configuration via environment variables

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 1.2.0 | 2026-02-02 | Scheduled task management, IM session fix |
| 1.1.0 | 2026-02-02 | MiniMax thinking, Unified LLM client |
| 0.5.9 | 2026-01-31 | Multi-platform IM support |
| 0.5.0 | 2026-01-15 | Ralph Mode, Self-evolution |
| 0.4.0 | 2026-01-01 | Testing framework |
| 0.3.0 | 2025-12-15 | Tool system |
| 0.2.0 | 2025-12-01 | Multi-turn chat |
| 0.1.0 | 2025-11-15 | Initial release |

[Unreleased]: https://github.com/openakita/openakita/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/openakita/openakita/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/openakita/openakita/compare/v1.0.2...v1.1.0
[0.5.9]: https://github.com/openakita/openakita/compare/v0.5.0...v0.5.9
[0.5.0]: https://github.com/openakita/openakita/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/openakita/openakita/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/openakita/openakita/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/openakita/openakita/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/openakita/openakita/releases/tag/v0.1.0
