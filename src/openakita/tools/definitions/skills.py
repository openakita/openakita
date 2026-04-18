"""
Skills tool definitions

Contains tools for skill management (following the Agent Skills specification):
- list_skills: List installed skills
- get_skill_info: Get detailed skill information
- run_skill_script: Run a skill script
- get_skill_reference: Get skill reference documentation
- install_skill: Install a new skill
- load_skill: Load a newly created skill
- reload_skill: Reload a modified skill

Note: Skill creation / packaging workflows are better handled by dedicated skills (external skills).
"""

SKILLS_TOOLS = [
    {
        "name": "list_skills",
        "category": "Skills",
        "description": "List all installed skills following Agent Skills specification. When you need to: (1) Check available skills, (2) Find skill for a task, (3) Verify skill installation.",
        "detail": """List installed skills (following the Agent Skills specification).

**Returned information**:
- Skill name
- Skill description
- Whether it can be auto-invoked

**Use cases**:
- View available skills
- Find a suitable skill for a task
- Verify skill installation status""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_skill_info",
        "category": "Skills",
        "description": "Get skill detailed instructions and usage guide (Level 2 disclosure). When you need to: (1) Understand how to use a skill, (2) Check skill capabilities, (3) Learn skill parameters. NOTE: This is for SKILL instructions (pdf, docx, code-review, etc.). For system TOOL parameter schemas (run_shell, browser_navigate, etc.), use get_tool_info instead.",
        "detail": """Get detailed information and instructions for a skill (Level 2 disclosure).

**Returned information**:
- Full SKILL.md content (after parameter substitution)
- Usage instructions
- List of available scripts
- List of reference documents
- Parameter definitions (if any)

**Use cases**:
- Understand how to use a skill
- View the full capabilities of a skill
- Learn the skill's parameters""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name"},
                "args": {
                    "type": "object",
                    "description": "Arguments passed to the skill (optional, used for placeholder substitution)",
                },
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "run_skill_script",
        "category": "Skills",
        "description": "Execute a skill's pre-built script file. IMPORTANT: Many skills (xlsx, docx, pptx, pdf, etc.) are instruction-only — they have NO scripts. For those skills, use get_skill_info to read instructions, then write code and execute via run_shell instead.",
        "detail": """Run a skill's **pre-built script**.

**Important note**:
Many skills (xlsx, docx, pptx, pdf, algorithmic-art, etc.) are **instruction-based skills** and do not ship executable scripts.
If run_skill_script reports "Script not found" or "no executable scripts", it means the skill has no pre-built scripts.
In that case **do not retry run_skill_script**, instead:
1. Use get_skill_info to read the skill's full instructions
2. Write Python code following the instructions
3. Execute the code with run_shell

**Use cases**:
- Execute a pre-built script inside the skill (e.g. recalc.py)
- Only after confirming the skill has runnable scripts

**Usage**:
1. First use get_skill_info to see the list of available scripts
2. Only use this tool when the skill has executable scripts
3. If it fails with "no executable scripts", switch to run_shell

**Handling missing configuration**:
If a script fails due to missing configuration (API keys, credentials, paths, etc.), proactively help the user complete the configuration (guide them through obtaining it and writing the config file) instead of telling them "XX is missing, cannot use".""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name"},
                "script_name": {"type": "string", "description": "Script filename (e.g. get_time.py)"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Command line arguments"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory for script execution (optional, defaults to the skill directory; when processing user files, prefer passing the file's directory)",
                },
            },
            "required": ["skill_name", "script_name"],
        },
    },
    {
        "name": "get_skill_reference",
        "category": "Skills",
        "description": "Get skill reference documentation for additional guidance. When you need to: (1) Get detailed technical docs, (2) Find examples, (3) Understand advanced usage.",
        "detail": """Get the skill's reference documentation.

**Use cases**:
- Get detailed technical documentation
- Find usage examples
- Learn advanced usage

**Default document**: REFERENCE.md""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name"},
                "ref_name": {
                    "type": "string",
                    "description": "Reference document name (defaults to REFERENCE.md)",
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
        "detail": """Install a skill from a URL or Git repository into the local skills/ directory.

**Supported install sources**:
1. Git repository URL (e.g. https://github.com/user/repo)
   - Automatically clones the repo and locates SKILL.md
   - Supports specifying a subdirectory path
2. Single SKILL.md file URL
   - Creates the standard directory structure (scripts/, references/, assets/)

**After installation**:
The skill is automatically loaded into the skills/<skill-name>/ directory""",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Git repository URL or SKILL.md file URL"},
                "name": {"type": "string", "description": "Skill name (optional, auto-extracted from SKILL.md)"},
                "subdir": {
                    "type": "string",
                    "description": "Subdirectory path within the Git repository where the skill lives (optional)",
                },
                "extra_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of extra file URLs to download",
                },
            },
            "required": ["source"],
        },
    },
    {
        "name": "load_skill",
        "category": "Skills",
        "description": "Load a newly created skill from skills/ directory. Use after creating a skill with skill-creator to make it immediately available.",
        "detail": """Load a newly created skill into the system.

**Use cases**:
- After creating a skill with skill-creator
- After manually creating a skill under the skills/ directory
- When a new skill is needed immediately

**Workflow**:
1. Use skill-creator to create SKILL.md
2. Save it to skills/<skill-name>/SKILL.md
3. Call load_skill to load it
4. The skill is immediately available

**Note**: The skill directory must contain a valid SKILL.md file""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name (i.e. the directory name under skills/)"}
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "reload_skill",
        "category": "Skills",
        "description": "Reload an existing skill to apply changes. Use after modifying a skill's SKILL.md or scripts.",
        "detail": """Reload an existing skill to apply modifications.

**Use cases**:
- After modifying a skill's SKILL.md
- After updating a skill's scripts
- When skill configuration needs to be refreshed

**How it works**:
1. Unload the existing skill
2. Re-parse SKILL.md
3. Re-register it with the system

**Note**: Only skills that have already been loaded can be reloaded""",
        "input_schema": {
            "type": "object",
            "properties": {"skill_name": {"type": "string", "description": "Skill name"}},
            "required": ["skill_name"],
        },
    },
    {
        "name": "manage_skill_enabled",
        "category": "Skills",
        "description": "Enable or disable external skills by updating the allowlist. Use when: (1) User asks to organize/clean up skills, (2) User wants to disable unused skills to reduce noise, (3) AI recommends enabling/disabling skills based on usage patterns.",
        "detail": """Enable or disable external skills.

**Features**:
- Batch-set the enabled/disabled state of multiple skills
- Changes take effect immediately (automatically written to data/skills.json and hot-reloaded)

**Use cases**:
- The user asks to tidy up skills (disable unused ones, enable needed ones)
- Adjusting the skill set to fit the current work context
- Reducing skill noise to improve response quality

**Notes**:
- System skills cannot be disabled; only external skills are supported
- Skills not mentioned in changes keep their current state""",
        "input_schema": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "skill_name": {"type": "string", "description": "Skill name"},
                            "enabled": {"type": "boolean", "description": "true=enable, false=disable"},
                        },
                        "required": ["skill_name", "enabled"],
                    },
                    "description": "List of skills to change",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the change (shown to the user)",
                },
            },
            "required": ["changes"],
        },
    },
    {
        "name": "execute_skill",
        "category": "Skills",
        "description": "Execute a skill in a forked context with isolated turns and timeout. Use for skills that declare execution-context: fork, or when you need to run a complex multi-step skill workflow independently.",
        "detail": """Execute a skill in an isolated fork context.

**Use cases**:
- The skill declares `execution-context: fork`
- A complex multi-step workflow needs to run independently
- Avoid skill execution polluting the main conversation context

**Parameters**:
- skill_name: Name of the skill to execute
- task: Task description assigned to the skill
- max_turns: Maximum execution turns (default 10, max 50)""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name"},
                "task": {"type": "string", "description": "Task description assigned to the skill"},
                "max_turns": {
                    "type": "integer",
                    "description": "Maximum execution turns (default 10)",
                    "default": 10,
                },
            },
            "required": ["skill_name", "task"],
        },
    },
    {
        "name": "uninstall_skill",
        "category": "Skills",
        "description": "Uninstall an external skill by removing its directory. System skills cannot be uninstalled. Use when user explicitly asks to remove a skill.",
        "detail": """Uninstall an external skill (deletes the skill directory and all its files).

**Limitations**:
- System skills cannot be uninstalled
- Only external skills under the skills/ directory can be uninstalled

**Note**: This operation is irreversible; make sure the user has confirmed.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Name of the skill to uninstall"},
            },
            "required": ["skill_name"],
        },
    },
]
