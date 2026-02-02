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
        "description": "Execute shell commands for system operations, directory creation, and script execution. When you need to: (1) Run system commands, (2) Execute scripts, (3) Install packages, (4) Manage processes. Note: If commands fail consecutively, try different approaches.",
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

**超时设置**:
- 简单命令: 30-60 秒
- 安装/下载: 300 秒
- 长时间任务: 根据需要设置更长时间""",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 Shell 命令"},
                "cwd": {"type": "string", "description": "工作目录（可选）"},
                "timeout": {"type": "integer", "description": "超时时间（秒），默认 60 秒"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "write_file",
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
                "content": {"type": "string", "description": "文件内容"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "read_file",
        "description": "Read file content for text files. When you need to: (1) Check file content, (2) Analyze code or data, (3) Get configuration values.",
        "detail": """读取文件内容。

**适用场景**:
- 查看文件内容
- 分析代码或数据
- 获取配置值

**注意事项**:
- 适用于文本文件
- 使用 UTF-8 编码
- 二进制文件需要特殊处理""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "list_directory",
        "description": "List directory contents including files and subdirectories. When you need to: (1) Explore directory structure, (2) Find specific files, (3) Check what exists in a folder.",
        "detail": """列出目录内容，包括文件和子目录。

**适用场景**:
- 探索目录结构
- 查找特定文件
- 检查文件夹中的内容

**返回信息**:
- 文件名和类型
- 文件大小
- 修改时间""",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"}
            },
            "required": ["path"]
        }
    },
]
