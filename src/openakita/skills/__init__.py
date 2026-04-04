"""
技能系统

遵循 Agent Skills 规范 (agentskills.io/specification)
支持渐进式披露:
- Level 1: 技能清单 (name + description) - 系统提示
- Level 2: 完整指令 (SKILL.md body) - 激活时
- Level 3: 资源文件 - 按需加载
"""

from .catalog import (
    SkillCatalog,
    generate_skill_catalog,
)
from .events import (
    notify_skills_changed,
    register_on_change,
)
from .loader import (
    SKILL_DIRECTORIES,
    SkillLoader,
)
from .parser import (
    ParsedSkill,
    SkillMetadata,
    SkillParser,
    parse_skill,
    parse_skill_directory,
)
from .registry import (
    SkillEntry,
    SkillRegistry,
    default_registry,
    get_skill,
    register_skill,
)
from .activation import SkillActivationManager
from .skill_hooks import SkillHookRunner, create_hook_runner, validate_hooks
from .usage import SkillUsageTracker
from .watcher import SkillWatcher, clear_all_skill_caches

__all__ = [
    # Parser
    "SkillParser",
    "SkillMetadata",
    "ParsedSkill",
    "parse_skill",
    "parse_skill_directory",
    # Registry
    "SkillRegistry",
    "SkillEntry",
    "default_registry",
    "register_skill",
    "get_skill",
    # Loader
    "SkillLoader",
    "SKILL_DIRECTORIES",
    # Catalog
    "SkillCatalog",
    "generate_skill_catalog",
    # Events
    "register_on_change",
    "notify_skills_changed",
    # Usage
    "SkillUsageTracker",
    # Activation
    "SkillActivationManager",
    # Hooks
    "SkillHookRunner",
    "create_hook_runner",
    "validate_hooks",
    # Watcher
    "SkillWatcher",
    "clear_all_skill_caches",
]
