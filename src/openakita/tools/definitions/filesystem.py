"""
File System 工具定义

包含文件系统操作相关的工具：
- run_shell: 执行 Shell 命令
- write_file: 写入文件
- read_file: 读取文件
- list_directory: 列出目录
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

**超时设置**:
- 简单命令: 30-60 秒
- 安装/下载: 300 秒
- 长时间任务: 根据需要设置更长时间""",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 Shell 命令"},
                "cwd": {"type": "string", "description": "工作目录（可选）"},
                "timeout": {"type": "integer", "description": "超时时间（秒），默认 60 秒"},
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
        "name": "list_directory",
        "category": "File System",
        "description": "List directory contents including files and subdirectories. When you need to: (1) Explore directory structure, (2) Find specific files, (3) Check what exists in a folder. Default returns up to 200 items.",
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
- 超出限制时会提示，可用 run_shell 获取完整列表""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
                "max_items": {
                    "type": "integer",
                    "description": "最大返回条目数，默认 200",
                    "default": 200,
                },
            },
            "required": ["path"],
        },
    },
]
