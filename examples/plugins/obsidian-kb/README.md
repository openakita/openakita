# Obsidian 知识库插件 / Obsidian Knowledge Base Plugin

将你的 Obsidian Vault 接入 OpenAkita，作为 RAG 知识源和笔记管理工具。

## 功能

| 工具 | 说明 |
|------|------|
| `obsidian_search` | 全文搜索 Vault 中的 Markdown 笔记，支持标签过滤 |
| `obsidian_vault_info` | 获取 Vault 概览：笔记总数、常用标签、最近修改 |
| `obsidian_open` | 通过 `obsidian://` URI 协议在桌面端打开笔记 |
| `obsidian_create` | 创建新笔记并写入磁盘，可选在 Obsidian 中打开 |
| `obsidian_daily` | 创建/打开今日日记（YYYY-MM-DD.md） |

此外还提供：

- **on_retrieve hook**：自动将相关笔记注入对话上下文（RAG 检索增强）
- **retrieval source**：标准 RAG 检索接口
- **内置 SKILL.md**：引导 LLM 遵循 Obsidian Flavored Markdown 规范

## 配置

在插件管理界面配置以下参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `vault_path` | Obsidian Vault 目录路径 | 必填 |
| `exclude_patterns` | 排除的目录/文件 glob 模式 | `[".trash", ".obsidian", "templates"]` |
| `max_file_size_kb` | 超过此大小的文件将被跳过 | `500` |
| `excerpt_length` | 搜索结果摘要的字符数 | `600` |

## 技术特性

- **增量索引**：基于文件 mtime 追踪，仅重建变更的文件
- **YAML 解析**：使用 `yaml.safe_load` 准确解析 frontmatter（回落到手写解析器）
- **glob 排除**：使用 `fnmatch` 实现真正的 glob 模式匹配
- **URI 协议**：通过 `obsidian://` 协议与桌面端交互

## 权限

- `tools.register` — 注册搜索和管理工具
- `hooks.retrieve` — 在 RAG 检索时注入知识
- `retrieval.register` — 注册为 RAG 检索源
- `config.read/write` — 读写插件配置
- `skill` — 注册 OFM 技能文档

---

# Obsidian Knowledge Base Plugin

Connect your Obsidian Vault to OpenAkita as a RAG knowledge source and note management tool.

## Features

| Tool | Description |
|------|-------------|
| `obsidian_search` | Full-text search across Vault Markdown notes with tag filtering |
| `obsidian_vault_info` | Vault overview: note count, top tags, recently modified |
| `obsidian_open` | Open a note in the Obsidian desktop app via URI protocol |
| `obsidian_create` | Create a new note on disk, optionally open in Obsidian |
| `obsidian_daily` | Create / open today's daily note (YYYY-MM-DD.md) |

Also provides:

- **on_retrieve hook**: auto-inject relevant notes into conversation context
- **retrieval source**: standard RAG retrieval interface
- **built-in SKILL.md**: guides LLM to follow Obsidian Flavored Markdown conventions

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `vault_path` | Path to Obsidian vault directory | Required |
| `exclude_patterns` | Glob patterns for excluded dirs/files | `[".trash", ".obsidian", "templates"]` |
| `max_file_size_kb` | Skip files larger than this | `500` |
| `excerpt_length` | Character count for search result excerpts | `600` |

## Technical Features

- **Incremental indexing**: tracks file mtime, only rebuilds changed files
- **YAML parsing**: uses `yaml.safe_load` for accurate frontmatter parsing
- **Glob exclusion**: `fnmatch`-based pattern matching
- **URI protocol**: interacts with Obsidian desktop via `obsidian://`

