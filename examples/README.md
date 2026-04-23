# Example Plugins

This directory contains example plugins demonstrating the OpenAkita Plugin 2.0 system.
Copy any plugin folder into `data/plugins/` to install it.

| Plugin | Type | Description |
|--------|------|-------------|
| [echo-channel](plugins/echo-channel/) | Channel | Stub echo channel adapter for testing |
| [echo-llm](plugins/echo-llm/) | LLM Provider | Echo LLM provider that mirrors input back |
| [github-mcp](plugins/github-mcp/) | MCP | GitHub MCP server integration via `npx` |
| [hello-tool](plugins/hello-tool/) | Tool | Minimal tool plugin — good starting template |
| [lark-cli-tool](plugins/lark-cli-tool/) | Tool | Lark/Feishu CLI tool integration |
| [message-logger](plugins/message-logger/) | Hook | Logs all messages to `messages.jsonl` |
| [obsidian-kb](plugins/obsidian-kb/) | Knowledge Base | Obsidian vault RAG integration |
| [ollama-provider](plugins/ollama-provider/) | LLM Provider | Local Ollama model provider |
| [qdrant-memory](plugins/qdrant-memory/) | Memory | Qdrant vector memory backend |
| [sqlite-memory](plugins/sqlite-memory/) | Memory | SQLite-based memory backend |
| [translate-skill](plugins/translate-skill/) | Skill | Translation skill with SKILL.md example |
| [whatsapp-channel](plugins/whatsapp-channel/) | Channel | WhatsApp adapter (Cloud API + Baileys 7.x) |

See [`.env.example`](.env.example) for environment variable reference.
