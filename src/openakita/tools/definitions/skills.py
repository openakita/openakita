"""
Skills 工具定义

包含技能管理相关的工具（遵循 Agent Skills 规范）：
- list_skills: 列出已安装的技能
- get_skill_info: 获取技能详细信息
- run_skill_script: 运行技能脚本
- get_skill_reference: 获取技能参考文档
- install_skill: 安装新技能
- generate_skill: 自动生成技能
- improve_skill: 改进已有技能
"""

SKILLS_TOOLS = [
    {
        "name": "list_skills",
        "description": "List all installed skills following Agent Skills specification. When you need to: (1) Check available skills, (2) Find skill for a task, (3) Verify skill installation.",
        "detail": """列出已安装的技能（遵循 Agent Skills 规范）。

**返回信息**：
- 技能名称
- 技能描述
- 是否可自动调用

**适用场景**：
- 查看可用技能
- 为任务查找合适的技能
- 验证技能安装状态""",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_skill_info",
        "description": "Get skill detailed instructions and usage guide (Level 2 disclosure). When you need to: (1) Understand how to use a skill, (2) Check skill capabilities, (3) Learn skill parameters.",
        "detail": """获取技能的详细信息和指令（Level 2 披露）。

**返回信息**：
- 完整的 SKILL.md 内容
- 使用说明
- 可用脚本列表
- 参考文档列表

**适用场景**：
- 了解技能的使用方法
- 查看技能的完整能力
- 学习技能参数""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "技能名称"}
            },
            "required": ["skill_name"]
        }
    },
    {
        "name": "run_skill_script",
        "description": "Execute a skill's script file with arguments. When you need to: (1) Run skill functionality, (2) Execute specific operations, (3) Process data with skill.",
        "detail": """运行技能的脚本。

**适用场景**：
- 执行技能功能
- 运行特定操作
- 用技能处理数据

**使用方法**：
1. 先用 get_skill_info 了解可用脚本
2. 指定脚本名称和参数执行""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "技能名称"},
                "script_name": {"type": "string", "description": "脚本文件名（如 get_time.py）"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "命令行参数"}
            },
            "required": ["skill_name", "script_name"]
        }
    },
    {
        "name": "get_skill_reference",
        "description": "Get skill reference documentation for additional guidance. When you need to: (1) Get detailed technical docs, (2) Find examples, (3) Understand advanced usage.",
        "detail": """获取技能的参考文档。

**适用场景**：
- 获取详细技术文档
- 查找使用示例
- 了解高级用法

**默认文档**：REFERENCE.md""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "技能名称"},
                "ref_name": {"type": "string", "description": "参考文档名称（默认 REFERENCE.md）", "default": "REFERENCE.md"}
            },
            "required": ["skill_name"]
        }
    },
    {
        "name": "install_skill",
        "description": "Install skill from URL or Git repository to local skills/ directory. When you need to: (1) Add new skill from GitHub, (2) Install SKILL.md from URL. Supports Git repos and single SKILL.md files.",
        "detail": """从 URL 或 Git 仓库安装技能到本地 skills/ 目录。

**支持的安装源**：
1. Git 仓库 URL（如 https://github.com/user/repo）
   - 自动克隆仓库并查找 SKILL.md
   - 支持指定子目录路径
2. 单个 SKILL.md 文件 URL
   - 创建规范目录结构（scripts/, references/, assets/）

**安装后**：
技能会自动加载到 skills/<skill-name>/ 目录""",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Git 仓库 URL 或 SKILL.md 文件 URL"},
                "name": {"type": "string", "description": "技能名称（可选，自动从 SKILL.md 提取）"},
                "subdir": {"type": "string", "description": "Git 仓库中技能所在的子目录路径（可选）"},
                "extra_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "额外需要下载的文件 URL 列表"
                }
            },
            "required": ["source"]
        }
    },
    {
        "name": "generate_skill",
        "description": "Auto-generate new skill following SKILL.md specification when existing skills don't meet requirements. When you need to: (1) Create custom skill, (2) Automate new capability, (3) Extend agent abilities.",
        "detail": """自动生成新技能（遵循 SKILL.md 规范）。

**适用场景**：
- 现有技能无法满足需求时
- 需要创建自定义技能
- 扩展 Agent 能力

**生成内容**：
- SKILL.md 文件
- 必要的脚本文件
- 目录结构""",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "技能功能的详细描述"},
                "name": {"type": "string", "description": "技能名称（可选，使用小写字母和连字符）"}
            },
            "required": ["description"]
        }
    },
    {
        "name": "improve_skill",
        "description": "Improve existing skill based on feedback or issues. When you need to: (1) Fix skill bugs, (2) Add new features, (3) Optimize performance.",
        "detail": """根据反馈改进已有技能。

**适用场景**：
- 修复技能 bug
- 添加新功能
- 优化性能

**改进方式**：
- 分析反馈内容
- 修改 SKILL.md 或脚本
- 更新技能版本""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "要改进的技能名称"},
                "feedback": {"type": "string", "description": "改进建议或问题描述"}
            },
            "required": ["skill_name", "feedback"]
        }
    },
]
