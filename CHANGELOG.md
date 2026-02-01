# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
| 0.5.9 | 2026-01-31 | Multi-platform IM support |
| 0.5.0 | 2026-01-15 | Ralph Mode, Self-evolution |
| 0.4.0 | 2026-01-01 | Testing framework |
| 0.3.0 | 2025-12-15 | Tool system |
| 0.2.0 | 2025-12-01 | Multi-turn chat |
| 0.1.0 | 2025-11-15 | Initial release |

[Unreleased]: https://github.com/openakita/openakita/compare/v0.5.9...HEAD
[0.5.9]: https://github.com/openakita/openakita/compare/v0.5.0...v0.5.9
[0.5.0]: https://github.com/openakita/openakita/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/openakita/openakita/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/openakita/openakita/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/openakita/openakita/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/openakita/openakita/releases/tag/v0.1.0
