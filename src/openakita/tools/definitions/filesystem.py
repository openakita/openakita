"""
File System tool definitions

Contains tools related to file system operations:
- run_shell: Execute shell commands (persistent sessions + background processes)
- write_file: Write files
- read_file: Read files
- edit_file: Exact string replacement editing
- list_directory: List directory
- grep: Content search
- glob: Filename pattern search
- delete_file: Delete files

Description quality aligned with Cursor Agent Mode — all behavioral constraints are pushed into description.
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
            '- If hung, kill the process using run_shell(command="kill {pid}").\n\n'
            "Multiple commands:\n"
            "- Independent commands → make separate run_shell calls in parallel\n"
            "- Dependent commands → chain with && (e.g., mkdir foo && cd foo && git init)\n"
            "- Don't use newlines to separate commands\n\n"
            "Output handling:\n"
            "- Output >200 lines is truncated; full output saved to overflow file, "
            "readable with read_file"
        ),
        "detail": """Execute shell commands to run system commands, create directories, execute scripts, etc.

**Persistent session**:
- Commands with the same session_id share working directory and environment variables
- Use working_directory parameter to switch directories (instead of cd &&)
- Default session_id=1

**Background processes**:
- block_timeout_ms controls blocking wait time, default 30000ms (30 seconds)
- On timeout, the command is moved to background and output streams to data/terminals/{session_id}.txt
- Set to 0 to background immediately (for dev servers and other long-running processes)

**Windows-specific handling**:
- PowerShell cmdlets are automatically encoded (EncodedCommand)
- UTF-8 code page is automatically set (chcp 65001)
- Multi-line python -c is automatically fixed""",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_directory": {
                    "type": "string",
                    "description": "Working directory (optional, persists for this session)",
                },
                "description": {
                    "type": "string",
                    "description": "5-10 word brief description of the command",
                },
                "block_timeout_ms": {
                    "type": "integer",
                    "description": (
                        "Blocking wait in milliseconds. Default 30000 (30 seconds). "
                        "Set to 0 to background immediately (for dev servers and other long-running processes)."
                    ),
                    "default": 30000,
                },
                "session_id": {
                    "type": "integer",
                    "description": "Terminal session ID. Commands in the same session share working directory and environment variables. Default 1.",
                    "default": 1,
                },
                "timeout": {
                    "type": "integer",
                    "description": "(Legacy parameter) Timeout in seconds; prefer block_timeout_ms",
                },
                "cwd": {
                    "type": "string",
                    "description": "(Legacy parameter) Working directory; prefer working_directory",
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
        "detail": """Write file content; can create a new file or overwrite an existing one.

**Use cases**:
- Create new files
- Fully replace file content (e.g., regenerate)

**Notes**:
- Overwrites existing files — make sure this is intentional
- Auto-creates parent directories (if they don't exist)
- Uses UTF-8 encoding""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path. **The parameter name must be `path`**, not "
                        "`filename` / `filepath` / `file_path` — although the implementation "
                        "has alias fallbacks, the schema only accepts `path`; aliases are a last resort, don't rely on them."
                    ),
                },
                "content": {"type": "string", "description": "File content"},
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
        "detail": """Read file content (with pagination support).

**Pagination parameters**:
- offset: Start line number (1-based), default 1
- limit: Number of lines to read, default 300
- If the file exceeds limit lines, the result ends with an [OUTPUT_TRUNCATED] hint and next-page parameters

**Notes**:
- Large files are paginated automatically; use offset/limit to page through based on hints
- Binary files require special handling""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "offset": {
                    "type": "integer",
                    "description": "Start line number (1-based), defaults to line 1",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read, default 300",
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
        "detail": """Edit a file via exact string replacement.

**Usage**:
1. First use read_file to inspect file content
2. Provide the original text to replace (old_string) and the new text (new_string)
3. old_string must exactly match content in the file (including indentation and whitespace)
4. If old_string matches multiple places and replace_all=true is not set, an error is returned asking for more context

**Notes**:
- Automatically handles Windows CRLF and Unix LF line endings""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {
                    "type": "string",
                    "description": "Original text to replace (must exactly match content in the file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "New text to replace with",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Whether to replace all matches, default false (replaces only the first match, requires unique match)",
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
        "detail": """List directory contents, including files and subdirectories.

**Returned info**:
- File name and type
- File size
- Modification time

**Notes**:
- Returns at most 200 entries by default
- Use pattern to filter by file type (e.g., "*.py")
- Use recursive=true to list subdirectories recursively""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path"},
                "pattern": {
                    "type": "string",
                    "description": "Filename filter pattern (e.g., '*.py', '*.ts'), default '*'",
                    "default": "*",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to recursively list subdirectory contents, default false",
                    "default": False,
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum number of entries to return, default 200",
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
        "detail": """Cross-platform content search tool (pure Python, no ripgrep/grep/findstr needed).

**Parameters**:
- pattern: Regular expression (e.g., "def test_", "class.*Error", "TODO")
- path: Search directory, default current directory
- include: Filename glob filter (e.g., "*.py" to search only Python files)
- context_lines: Number of context lines before and after each match
- max_results: Maximum number of matches returned, default 50
- case_insensitive: Whether to ignore case""",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression search pattern",
                },
                "path": {
                    "type": "string",
                    "description": "Search directory, default is current working directory",
                    "default": ".",
                },
                "include": {
                    "type": "string",
                    "description": "Filename glob filter (e.g., '*.py', '*.ts'); if omitted, searches all text files",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Number of context lines before and after each match, default 0",
                    "default": 0,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matches to return, default 50",
                    "default": 50,
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Whether to ignore case, default false",
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
        "detail": """Recursively search for files by filename pattern.

**Pattern notes**:
- "*.py" → automatically becomes "**/*.py" (recursive search)
- "**/*.test.ts" → recursively search all .test.ts files
- "*config*" → automatically becomes "**/*config*"

**Notes**:
- Automatically skips .git, node_modules, __pycache__, and similar directories
- Results sorted by modification time descending (newest first)
- Returns a list of relative paths""",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g., '*.py', '**/test_*.ts', '*config*')",
                },
                "path": {
                    "type": "string",
                    "description": "Search root directory, default current working directory",
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
        "detail": """Delete a file or empty directory.

**Use cases**:
- Delete generated files
- Clean up temporary files
- Delete empty directories

**Notes**:
- Only deletes files or empty directories
- Non-empty directories are rejected; use run_shell with rm -rf or similar
- Paths are protected by security policy""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file or empty directory to delete",
                },
            },
            "required": ["path"],
        },
    },
]
