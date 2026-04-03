"""
File System 工具定义

包含文件系统操作相关的工具：
- run_shell: 执行 Shell 命令（持久会话 + 后台进程）
- write_file: 写入文件
- read_file: 读取文件
- edit_file: 精确字符串替换编辑
- list_directory: 列出目录
- grep: 内容搜索
- glob: 文件名模式搜索
- delete_file: 删除文件

Description 质量对齐 Cursor Agent Mode — 所有行为约束前置到 description。
"""

FILESYSTEM_TOOLS = [
    {
        "name": "run_shell",
        "category": "File System",
        "description": (
            "Execute shell commands in a persistent terminal session.\n\n"
            "The shell is stateful — working directory and environment variables persist "
            "across calls within the same session. Use working_directory parameter to run "
            "in a different directory (rather than cd && command).\n\n"
            "IMPORTANT — Use specialized tools instead of shell equivalents when available:\n"
            "- read_file instead of cat/head/tail\n"
            "- write_file/edit_file instead of sed/awk/echo >\n"
            "- grep/glob instead of find/grep/rg\n"
            "- web_fetch instead of curl (for reading webpage content)\n\n"
            "Long-running commands:\n"
            "- Commands that don't complete within block_timeout_ms (default 30s) are moved "
            "to background. Output streams to data/terminals/{session_id}.txt.\n"
            "- Set block_timeout_ms to 0 for dev servers, watchers, or any long-running process.\n"
            "- Monitor background commands by reading the terminal file with read_file.\n"
            "- Terminal file header has pid and running_for_ms (updated every 5s).\n"
            "- When finished, footer with exit_code and elapsed_ms appears.\n"
            "- Poll with exponential backoff: read file → check → wait → read again.\n"
            "- If hung, kill the process using run_shell(command=\"kill {pid}\").\n\n"
            "Multiple commands:\n"
            "- Independent commands → make separate run_shell calls in parallel\n"
            "- Dependent commands → chain with && (e.g., mkdir foo && cd foo && git init)\n"
            "- Don't use newlines to separate commands\n\n"
            "Output handling:\n"
            "- Output >200 lines is truncated; full output saved to overflow file, "
            "readable with read_file"
        ),
        "detail": """执行 Shell 命令，用于运行系统命令、创建目录、执行脚本等。

**持久会话**:
- 同一 session_id 的命令共享工作目录和环境变量
- 用 working_directory 参数切换目录（而非 cd &&）
- 默认 session_id=1

**后台进程**:
- block_timeout_ms 控制阻塞等待时间，默认 30000ms (30 秒)
- 超时后命令自动转后台，输出流式写入 data/terminals/{session_id}.txt
- 设为 0 可立即后台化（用于 dev server 等长驻进程）

**Windows 特殊处理**:
- PowerShell cmdlet 自动编码（EncodedCommand）
- UTF-8 代码页自动设置（chcp 65001）
- 多行 python -c 自动修复""",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 Shell 命令"},
                "working_directory": {
                    "type": "string",
                    "description": "工作目录（可选，持久生效于本会话）",
                },
                "description": {
                    "type": "string",
                    "description": "命令的 5-10 字简要描述",
                },
                "block_timeout_ms": {
                    "type": "integer",
                    "description": (
                        "阻塞等待毫秒数。默认 30000（30秒）。"
                        "设为 0 立即后台化（用于 dev server 等长驻进程）。"
                    ),
                    "default": 30000,
                },
                "session_id": {
                    "type": "integer",
                    "description": "终端会话 ID。同一会话的命令共享工作目录和环境变量。默认 1。",
                    "default": 1,
                },
                "timeout": {
                    "type": "integer",
                    "description": "（兼容旧参数）超时时间（秒），优先使用 block_timeout_ms",
                },
                "cwd": {
                    "type": "string",
                    "description": "（兼容旧参数）工作目录，优先使用 working_directory",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "write_file",
        "category": "File System",
        "description": (
            "Write content to file, creating new or overwriting existing. "
            "Auto-creates parent directories.\n\n"
            "IMPORTANT behavioral rules:\n"
            "- ALWAYS prefer edit_file over write_file when modifying existing files — "
            "it's safer and more token-efficient\n"
            "- NEVER create files unless absolutely necessary for the task. "
            "Prefer editing existing files.\n"
            "- NEVER proactively create documentation files (*.md, README) unless the "
            "user explicitly asks\n"
            "- This tool will OVERWRITE the existing file — make sure this is intentional\n"
            "- Uses UTF-8 encoding\n\n"
            "When to use write_file vs edit_file:\n"
            "- write_file: Creating entirely new files, or replacing entire file content\n"
            "- edit_file: Modifying specific parts of an existing file (preferred)"
        ),
        "detail": """写入文件内容，可以创建新文件或覆盖已有文件。

**适用场景**:
- 创建新文件
- 整体替换文件内容（比如重新生成）

**注意事项**:
- 会覆盖已存在的文件，确保这是有意的
- 自动创建父目录（如果不存在）
- 使用 UTF-8 编码""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "category": "File System",
        "description": (
            "Read file content with optional pagination. Default reads first 300 lines.\n\n"
            "Supports text files, images (jpeg/jpg, png, gif, webp), and PDF files:\n"
            "- Text files: returns numbered lines (LINE_NUMBER|CONTENT format)\n"
            "- Images: returns the image for visual analysis\n"
            "- PDFs: automatically converts to text content\n\n"
            "Pagination:\n"
            "- Use offset (1-based line number) and limit to read specific sections\n"
            "- Results include [OUTPUT_TRUNCATED] hint with next-page parameters when truncated\n"
            "- For large files, read in chunks rather than requesting the entire file\n\n"
            "IMPORTANT:\n"
            "- You can call multiple read_file in parallel — always batch-read related files "
            "together rather than reading them one by one\n"
            "- Read a file at least once BEFORE editing it with edit_file or write_file\n"
            "- If the file is empty, returns 'File is empty.'\n"
            "- Binary files (other than images/PDF) are not supported"
        ),
        "detail": """读取文件内容（支持分页）。

**分页参数**:
- offset: 起始行号（1-based），默认 1
- limit: 读取行数，默认 300
- 如果文件超过 limit 行，结果末尾会包含 [OUTPUT_TRUNCATED] 提示和下一页参数

**注意事项**:
- 大文件自动分页，根据提示用 offset/limit 翻页
- 二进制文件需要特殊处理""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "offset": {
                    "type": "integer",
                    "description": "起始行号（1-based），默认从第 1 行开始",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "读取的最大行数，默认 300 行",
                    "default": 300,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "category": "File System",
        "description": (
            "Edit file by exact string replacement. ALWAYS prefer this over write_file "
            "for modifications.\n\n"
            "The edit will FAIL if old_string is not unique in the file. Either:\n"
            "- Provide more surrounding context to make old_string unique, OR\n"
            "- Use replace_all=true to replace every occurrence\n\n"
            "IMPORTANT:\n"
            "- You MUST read_file at least once before editing — never edit blind\n"
            "- Preserve exact indentation (tabs/spaces) as it appears in the file\n"
            "- old_string and new_string must be different\n"
            "- Auto-handles Windows CRLF / Unix LF line endings\n"
            "- Use replace_all=true for renaming variables or strings across the entire file\n\n"
            "When editing fails with 'multiple matches found':\n"
            "- Read the file again to see the full context\n"
            "- Include more lines before/after the change point to make old_string unique"
        ),
        "detail": """精确字符串替换式编辑文件。

**使用方法**:
1. 先用 read_file 查看文件内容
2. 提供要替换的原文本 (old_string) 和新文本 (new_string)
3. old_string 必须精确匹配文件中的内容（包括缩进和空格）
4. 如果 old_string 匹配到多处且未设 replace_all=true，会报错并提示提供更多上下文

**注意事项**:
- 自动兼容 Windows CRLF 和 Unix LF 换行符""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {
                    "type": "string",
                    "description": "要替换的原文本（须精确匹配文件中的内容）",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的新文本",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换所有匹配项，默认 false（仅替换第一处，要求唯一匹配）",
                    "default": False,
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_directory",
        "category": "File System",
        "description": (
            "List directory contents including files and subdirectories. "
            "When you need to: (1) Explore directory structure, (2) Find specific files, "
            "(3) Check what exists in a folder. Default returns up to 200 items. "
            "Supports optional pattern filtering and recursive listing."
        ),
        "detail": """列出目录内容，包括文件和子目录。

**返回信息**:
- 文件名和类型
- 文件大小
- 修改时间

**注意事项**:
- 默认最多返回 200 条目
- 使用 pattern 过滤特定类型文件（如 "*.py"）
- 使用 recursive=true 递归列出子目录""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
                "pattern": {
                    "type": "string",
                    "description": "文件名过滤模式（如 '*.py'、'*.ts'），默认 '*'",
                    "default": "*",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "是否递归列出子目录内容，默认 false",
                    "default": False,
                },
                "max_items": {
                    "type": "integer",
                    "description": "最大返回条目数，默认 200",
                    "default": 200,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "grep",
        "category": "File System",
        "description": (
            "Search file contents using regex pattern. Cross-platform, pure Python "
            "(no external tools needed).\n\n"
            "Supports:\n"
            "- Full regex syntax (e.g., 'def test_', 'class.*Error', 'TODO|FIXME')\n"
            "- File filtering with include parameter (e.g., '*.py', '*.ts')\n"
            "- Case-insensitive search with case_insensitive=true\n"
            "- Context lines around matches with context_lines parameter\n\n"
            "When to use grep vs semantic_search vs glob:\n"
            "- grep: Find exact text patterns ('class UserService', 'import os')\n"
            "- semantic_search: Find code by meaning ('Where is authentication handled?')\n"
            "- glob: Find files by name pattern ('*.config.ts', 'test_*.py')\n\n"
            "IMPORTANT:\n"
            "- Automatically skips .git, node_modules, __pycache__, .venv directories\n"
            "- Automatically skips binary files\n"
            "- Results capped at max_results (default 50); increase for comprehensive searches\n"
            "- Returns format: file:line_number:content\n"
            "- Prefer grep over run_shell('grep ...') — this tool is optimized and cross-platform"
        ),
        "detail": """跨平台内容搜索工具（纯 Python 实现，无需 ripgrep/grep/findstr）。

**参数说明**:
- pattern: 正则表达式（如 "def test_"、"class.*Error"、"TODO"）
- path: 搜索目录，默认当前目录
- include: 文件名 glob 过滤（如 "*.py" 只搜 Python 文件）
- context_lines: 显示匹配行前后的上下文行数
- max_results: 最大返回匹配数，默认 50
- case_insensitive: 是否忽略大小写""",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "正则表达式搜索模式",
                },
                "path": {
                    "type": "string",
                    "description": "搜索目录，默认当前工作目录",
                    "default": ".",
                },
                "include": {
                    "type": "string",
                    "description": "文件名 glob 过滤（如 '*.py'、'*.ts'），不填则搜索所有文本文件",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "匹配行前后的上下文行数，默认 0",
                    "default": 0,
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回匹配数，默认 50",
                    "default": 50,
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "是否忽略大小写，默认 false",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "glob",
        "category": "File System",
        "description": (
            "Find files by glob pattern recursively. Results sorted by modification time "
            "(newest first).\n\n"
            "Patterns not starting with '**/' are automatically prepended — "
            "'*.py' becomes '**/*.py'.\n\n"
            "Examples:\n"
            "- '*.py' → all Python files recursively\n"
            "- 'test_*.py' → all test files\n"
            "- '*config*' → files with 'config' in name\n"
            "- '**/*.test.ts' → all TypeScript test files\n\n"
            "IMPORTANT:\n"
            "- You can call multiple glob searches in parallel — batch related searches "
            "together for better performance (e.g., search for '*.py' and '*.ts' simultaneously)\n"
            "- Automatically skips .git, node_modules, __pycache__ directories\n"
            "- Returns relative path list"
        ),
        "detail": """按文件名模式递归搜索文件。

**模式说明**:
- "*.py" → 自动变为 "**/*.py"（递归搜索）
- "**/*.test.ts" → 递归搜索所有 .test.ts 文件
- "*config*" → 自动变为 "**/*config*"

**注意事项**:
- 自动跳过 .git、node_modules、__pycache__ 等目录
- 结果按修改时间降序排序（最新的在前）
- 返回相对路径列表""",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob 模式（如 '*.py'、'**/test_*.ts'、'*config*'）",
                },
                "path": {
                    "type": "string",
                    "description": "搜索根目录，默认当前工作目录",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "delete_file",
        "category": "File System",
        "description": (
            "Delete a file or empty directory. The operation will fail gracefully if:\n"
            "- The file doesn't exist\n"
            "- The operation is rejected for security reasons\n"
            "- The directory is not empty (use run_shell for recursive deletion)\n"
            "- The file cannot be deleted"
        ),
        "detail": """删除文件或空目录。

**适用场景**:
- 删除生成的文件
- 清理临时文件
- 删除空目录

**注意事项**:
- 仅删除文件或空目录
- 非空目录会被拒绝，需使用 run_shell 执行 rm -rf 等命令
- 路径受安全策略保护""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要删除的文件或空目录路径",
                },
            },
            "required": ["path"],
        },
    },
]
