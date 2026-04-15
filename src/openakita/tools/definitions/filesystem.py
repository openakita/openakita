"""
File System 工具定义

包含文件系统操作相关的工具：
- run_shell: 执行 Shell 命令
- write_file: 写入文件
- read_file: 读取文件
- edit_file: 精确字符串替换编辑
- list_directory: 列出目录
- grep: 内容搜索
- glob: 文件名模式搜索
- delete_file: 删除文件
"""

FILESYSTEM_TOOLS = [
    {
        "name": "run_shell",
        "category": "File System",
        "description": "Execute shell commands. Use for: (1) running Python scripts you wrote via write_file, (2) system commands, (3) installing packages, (4) executing code from skill instructions (after reading via get_skill_info). This is the primary way to execute code. For repetitive patterns, consider creating a skill.",
        "detail": """执行 Shell 命令，用于运行系统命令、创建目录、执行脚本等。

**适用场景**:
- 运行系统命令
- 执行脚本文件
- 安装软件包
- 管理进程

**注意事项**:
- Windows 使用 PowerShell/cmd 命令
- Linux/Mac 使用 bash 命令
- 如果命令连续失败，请尝试不同的命令或方法
- 输出超过 200 行时会自动截断，完整输出保存到溢出文件，可用 read_file 分页读取

**超时设置**（上限 300 秒）:
- 简单命令: 30-60 秒
- 安装/下载: 120-300 秒
- 连续超时通常说明网络/环境问题，请勿盲目增大 timeout，应告知用户""",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 Shell 命令"},
                "cwd": {"type": "string", "description": "工作目录（可选）"},
                "timeout": {"type": "integer", "description": "超时时间（秒），默认 60，上限 300。连续超时通常说明网络/环境问题，请勿盲目增大"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "write_file",
        "category": "File System",
        "description": "Write content to file, creating new or overwriting existing. When you need to: (1) Create new files, (2) Update file content, (3) Save generated code or data.",
        "detail": """写入文件内容，可以创建新文件或覆盖已有文件。

**适用场景**:
- 创建新文件
- 更新文件内容
- 保存生成的代码或数据

**注意事项**:
- 会覆盖已存在的文件
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
        "description": "Read file content with optional pagination (offset/limit). Default reads first 300 lines. When you need to: (1) Check file content, (2) Analyze code or data, (3) Get configuration values. For large files, use offset and limit to read specific sections.",
        "detail": """读取文件内容（支持分页）。

**适用场景**:
- 查看文件内容
- 分析代码或数据
- 获取配置值

**分页参数**:
- offset: 起始行号（1-based），默认 1
- limit: 读取行数，默认 300
- 如果文件超过 limit 行，结果末尾会包含 [OUTPUT_TRUNCATED] 提示和下一页参数

**注意事项**:
- 适用于文本文件
- 使用 UTF-8 编码
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
        "name": "read_attachment_summary",
        "category": "Attachment",
        "description": "Read a summary of an uploaded attachment by attachment_id. Use this for user-uploaded files, not workspace paths.",
        "detail": """读取已上传附件的摘要信息。

**适用场景**:
- 用户在聊天里上传了文档/文件
- 已知 `attachment_id`，想先快速了解附件内容
- 需要区分“上传附件”与“工作区文件”

**注意事项**:
- 只接受 `attachment_id`
- 不接受本地路径
- 如果附件是大文本，摘要会告诉你继续用 `read_attachment_chunk` / `search_attachment`""",
        "input_schema": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "string", "description": "附件 ID"},
                "max_chars": {"type": "integer", "description": "摘要最大字符数，默认 1600"},
            },
            "required": ["attachment_id"],
        },
    },
    {
        "name": "read_attachment_chunk",
        "category": "Attachment",
        "description": "Read a paginated text chunk from an uploaded attachment by attachment_id.",
        "detail": """分页读取已上传文本附件的内容片段。

**适用场景**:
- 大文本附件不能一次性全部塞进上下文
- 需要按段继续阅读上传附件

**分页参数**:
- offset: 起始行号（1-based），默认 1
- limit: 读取行数，默认 200

**注意事项**:
- 只接受 `attachment_id`
- 非文本附件会返回不可读取提示""",
        "input_schema": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "string", "description": "附件 ID"},
                "offset": {"type": "integer", "description": "起始行号（1-based）", "default": 1},
                "limit": {"type": "integer", "description": "读取行数，默认 200", "default": 200},
            },
            "required": ["attachment_id"],
        },
    },
    {
        "name": "search_attachment",
        "category": "Attachment",
        "description": "Search keyword matches inside an uploaded text attachment by attachment_id.",
        "detail": """在已上传文本附件中搜索关键词。

**适用场景**:
- 用户上传了长文档，只想定位某个关键词
- 先搜索命中，再按行号读取片段

**注意事项**:
- 只接受 `attachment_id`
- 搜索范围是上传附件文本，不是工作区文件
- 默认忽略大小写""",
        "input_schema": {
            "type": "object",
            "properties": {
                "attachment_id": {"type": "string", "description": "附件 ID"},
                "query": {"type": "string", "description": "要搜索的关键词"},
                "context_lines": {"type": "integer", "description": "前后文行数，默认 1", "default": 1},
                "max_results": {"type": "integer", "description": "最大返回结果数，默认 20", "default": 20},
                "case_insensitive": {"type": "boolean", "description": "是否忽略大小写，默认 true", "default": True},
            },
            "required": ["attachment_id", "query"],
        },
    },
    {
        "name": "edit_file",
        "category": "File System",
        "description": "Edit file by exact string replacement. Finds old_string in the file and replaces it with new_string. The old_string must uniquely match one location unless replace_all=true. ALWAYS prefer this over write_file when modifying existing files — it's safer and more token-efficient.",
        "detail": """精确字符串替换式编辑文件。

**适用场景**:
- 修改现有文件中的代码或文本
- 替换配置值
- 批量重命名变量（使用 replace_all=true）

**使用方法**:
1. 先用 read_file 查看文件内容
2. 提供要替换的原文本 (old_string) 和新文本 (new_string)
3. old_string 必须精确匹配文件中的内容（包括缩进和空格）
4. 如果 old_string 匹配到多处且未设 replace_all=true，会报错并提示提供更多上下文

**注意事项**:
- old_string 和 new_string 不能相同
- 自动兼容 Windows CRLF 和 Unix LF 换行符
- 修改前请确保已 read_file 确认文件当前内容""",
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
        "description": "List directory contents including files and subdirectories. When you need to: (1) Explore directory structure, (2) Find specific files, (3) Check what exists in a folder. Default returns up to 200 items. Supports optional pattern filtering and recursive listing.",
        "detail": """列出目录内容，包括文件和子目录。

**适用场景**:
- 探索目录结构
- 查找特定文件
- 检查文件夹中的内容

**返回信息**:
- 文件名和类型
- 文件大小
- 修改时间

**注意事项**:
- 默认最多返回 200 条目
- 超出限制时会提示，可用 run_shell 获取完整列表
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
        "description": "Search file contents using regex pattern. Cross-platform (no external tools needed). When you need to: (1) Find code patterns, (2) Search for string usage across files, (3) Locate function/class definitions. Returns matching lines with file paths and line numbers.",
        "detail": """跨平台内容搜索工具（纯 Python 实现，无需 ripgrep/grep/findstr）。

**适用场景**:
- 在代码库中搜索特定模式
- 查找函数/类定义
- 定位字符串用法
- 搜索 TODO/FIXME 等标记

**参数说明**:
- pattern: 正则表达式（如 "def test_"、"class.*Error"、"TODO"）
- path: 搜索目录，默认当前目录
- include: 文件名 glob 过滤（如 "*.py" 只搜 Python 文件）
- context_lines: 显示匹配行前后的上下文行数
- max_results: 最大返回匹配数，默认 50
- case_insensitive: 是否忽略大小写

**注意事项**:
- 自动跳过 .git、node_modules、__pycache__、.venv 等目录
- 自动跳过二进制文件
- 返回格式: file:line_number:content""",
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
        "description": "Find files by glob pattern recursively. When you need to: (1) Find files by name pattern (e.g. '*.py'), (2) Locate specific files across directories, (3) Check file existence by pattern. Results sorted by modification time (newest first).",
        "detail": """按文件名模式递归搜索文件。

**适用场景**:
- 按扩展名查找文件（如 "*.py"、"*.ts"）
- 按名称模式查找（如 "test_*.py"、"*config*"）
- 跨目录定位文件

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
        "description": "Delete a file or empty directory. When you need to: (1) Remove generated files, (2) Clean up temporary files, (3) Delete empty directories. Non-empty directories are rejected for safety — use run_shell for recursive deletion.",
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
