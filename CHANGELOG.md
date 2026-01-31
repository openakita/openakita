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

### Changed
- README restructured for open source

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

[Unreleased]: https://github.com/jevisuen/openakita/compare/v0.5.9...HEAD
[0.5.9]: https://github.com/jevisuen/openakita/compare/v0.5.0...v0.5.9
[0.5.0]: https://github.com/jevisuen/openakita/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/jevisuen/openakita/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jevisuen/openakita/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jevisuen/openakita/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jevisuen/openakita/releases/tag/v0.1.0
