"""
Skills 工具定义

包含技能管理相关的工具（遵循 Agent Skills 规范）：
- list_skills: 列出已安装的技能
- get_skill_info: 获取技能详细信息
- run_skill_script: 运行技能脚本
- get_skill_reference: 获取技能参考文档
- install_skill: 安装新技能
- load_skill: 加载新创建的技能
- reload_skill: 重新加载已修改的技能

说明：技能创建/封装等工作流建议使用专门的技能（外部技能）完成。
"""

SKILLS_TOOLS = [
    {
        "name": "list_skills",
        "category": "Skills",
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
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_skill_info",
        "category": "Skills",
        "description": "Get skill detailed instructions and usage guide (Level 2 disclosure). When you need to: (1) Understand how to use a skill, (2) Check skill capabilities, (3) Learn skill parameters. NOTE: This is for SKILL instructions (pdf, docx, code-review, etc.). For system TOOL parameter schemas (run_shell, browser_navigate, etc.), use get_tool_info instead.",
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
            "properties": {"skill_name": {"type": "string", "description": "技能名称"}},
            "required": ["skill_name"],
        },
    },
    {
        "name": "run_skill_script",
        "category": "Skills",
        "description": "Execute a skill's pre-built script file. IMPORTANT: Many skills (xlsx, docx, pptx, pdf, etc.) are instruction-only — they have NO scripts. For those skills, use get_skill_info to read instructions, then write code and execute via run_shell instead.",
        "detail": """运行技能的**预置脚本**。

**⚠️ 重要提醒**：
很多技能（xlsx, docx, pptx, pdf, algorithmic-art 等）是**指令型技能**，它们不提供可执行脚本。
如果 run_skill_script 报告 "Script not found" 或 "no executable scripts"，说明该技能没有预置脚本。
此时**不要重试 run_skill_script**，而应：
1. 用 get_skill_info 读取技能的完整指令
2. 按照指令编写 Python 代码
3. 用 run_shell 执行代码

**适用场景**：
- 执行技能内预置的脚本（如 recalc.py 等）
- 必须先确认技能有可用脚本

**使用方法**：
1. 先用 get_skill_info 了解可用脚本列表
2. 仅当技能有可执行脚本时使用本工具
3. 如果失败提示"no executable scripts"，改用 run_shell

**配置缺失处理**：
如果脚本因缺少配置（API Key/凭据/路径等）而失败，应主动帮用户完成配置（引导获取、写入配置文件），而不是告诉用户"缺少XX无法使用"。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "技能名称"},
                "script_name": {"type": "string", "description": "脚本文件名（如 get_time.py）"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "命令行参数"},
                "cwd": {"type": "string", "description": "脚本执行的工作目录（可选，默认为技能目录。处理用户文件时建议传入文件所在目录）"},
            },
            "required": ["skill_name", "script_name"],
        },
    },
    {
        "name": "get_skill_reference",
        "category": "Skills",
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
                "ref_name": {
                    "type": "string",
                    "description": "参考文档名称（默认 REFERENCE.md）",
                    "default": "REFERENCE.md",
                },
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "install_skill",
        "category": "Skills",
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
                "subdir": {
                    "type": "string",
                    "description": "Git 仓库中技能所在的子目录路径（可选）",
                },
                "extra_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "额外需要下载的文件 URL 列表",
                },
            },
            "required": ["source"],
        },
    },
    {
        "name": "load_skill",
        "category": "Skills",
        "description": "Load a newly created skill from skills/ directory. Use after creating a skill with skill-creator to make it immediately available.",
        "detail": """加载新创建的技能到系统中。

**适用场景**：
- 使用 skill-creator 创建技能后
- 手动在 skills/ 目录创建技能后
- 需要立即使用新技能时

**使用流程**：
1. 使用 skill-creator 创建 SKILL.md
2. 保存到 skills/<skill-name>/SKILL.md
3. 调用 load_skill 加载
4. 技能立即可用

**注意**：技能目录必须包含有效的 SKILL.md 文件""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "技能名称（即 skills/ 下的目录名）"}
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "reload_skill",
        "category": "Skills",
        "description": "Reload an existing skill to apply changes. Use after modifying a skill's SKILL.md or scripts.",
        "detail": """重新加载已存在的技能以应用修改。

**适用场景**：
- 修改了技能的 SKILL.md 后
- 更新了技能的脚本后
- 需要刷新技能配置时

**工作原理**：
1. 卸载原有技能
2. 重新解析 SKILL.md
3. 重新注册到系统

**注意**：只能重新加载已加载过的技能""",
        "input_schema": {
            "type": "object",
            "properties": {"skill_name": {"type": "string", "description": "技能名称"}},
            "required": ["skill_name"],
        },
    },
]
