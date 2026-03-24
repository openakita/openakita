"""
技能注册中心

遵循 Agent Skills 规范 (agentskills.io/specification)
存储和管理技能元数据，支持渐进式披露
"""

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .parser import ParsedSkill

logger = logging.getLogger(__name__)


@dataclass
class SkillEntry:
    """
    技能注册条目

    存储技能的元数据和引用
    支持渐进式披露:
    - Level 1: 元数据 (name, description) - 总是可用
    - Level 2: body (完整指令) - 激活时加载
    - Level 3: scripts/references/assets - 按需加载

    系统技能额外字段:
    - system: 是否为系统技能
    - handler: 处理器模块名
    - tool_name: 原工具名称
    - category: 工具分类
    """

    skill_id: str  # 唯一标识（= 目录名），用作注册 key、allowlist key、tool name key
    name: str  # SKILL.md 声明的显示名（可重复），仅用于展示和搜索
    description: str
    version: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False

    # 系统技能专用字段
    system: bool = False
    handler: str | None = None
    tool_name: str | None = None
    category: str | None = None

    # metadata.openakita structured fields
    supported_os: list[str] = field(default_factory=list)
    required_bins: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)

    # 技能路径 (用于延迟加载)
    skill_path: str | None = None

    # 技能来源 URL（来自 .openakita-source 文件，用于区分同名技能）
    source_url: str | None = None

    # 国际化（由 agents/openai.yaml i18n 字段注入，兼容旧的 .openakita-i18n.json）
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)

    # 全局启用 / 禁用标记
    # 用户通过 UI / skills.json 禁用的技能在注册表中保留但标记 disabled=True，
    # 这样 SkillCatalog 和 list_skills 工具会过滤它们，
    # 而子 Agent INCLUSIVE 模式仍可通过 profile.skills 显式引用并重新启用。
    disabled: bool = False

    # 完整技能对象引用 (延迟加载)
    _parsed_skill: Optional["ParsedSkill"] = field(default=None, repr=False)

    def get_display_name(self, lang: str = "zh") -> str:
        """按语言返回显示名称，找不到则回退到 name"""
        return self.name_i18n.get(lang, self.name)

    def get_display_description(self, lang: str = "zh") -> str:
        """按语言返回显示描述，找不到则回退到 description"""
        return self.description_i18n.get(lang, self.description)

    @classmethod
    def from_parsed_skill(
        cls, skill: "ParsedSkill", skill_id: str | None = None,
    ) -> "SkillEntry":
        """从 ParsedSkill 创建条目

        Args:
            skill: 解析后的技能对象
            skill_id: 唯一标识（通常为目录名）。未提供时回退到 metadata.name。
        """
        meta = skill.metadata

        source_url: str | None = None
        if skill.path:
            from pathlib import Path

            source_file = Path(skill.path).parent / ".openakita-source"
            try:
                source_url = source_file.read_text(encoding="utf-8").strip() or None
            except Exception:
                pass

        return cls(
            skill_id=skill_id or meta.name,
            name=meta.name,
            description=meta.description,
            version=meta.version,
            license=meta.license,
            compatibility=meta.compatibility,
            metadata=meta.metadata,
            allowed_tools=meta.allowed_tools,
            disable_model_invocation=meta.disable_model_invocation,
            system=meta.system,
            handler=meta.handler,
            tool_name=meta.tool_name,
            category=meta.category,
            supported_os=list(meta.supported_os),
            required_bins=list(meta.required_bins),
            required_env=list(meta.required_env),
            skill_path=str(skill.path),
            source_url=source_url,
            name_i18n=dict(meta.name_i18n),
            description_i18n=dict(meta.description_i18n),
            _parsed_skill=skill,
        )

    def get_body(self) -> str | None:
        """获取技能 body (Level 2)"""
        if self._parsed_skill:
            return self._parsed_skill.body
        return None

    def to_tool_schema(self) -> dict:
        """
        转换为 LLM 工具调用 schema

        用于将技能作为工具提供给 LLM
        系统技能使用原 tool_name，外部技能使用 skill_ 前缀
        """
        if self.system and self.tool_name:
            # 系统技能：使用原工具名
            return {
                "name": self.tool_name,
                "description": self.description,
                "input_schema": self._get_input_schema(),
            }
        else:
            # 外部技能：使用 skill_ 前缀，基于 skill_id 生成唯一工具名
            safe = re.sub(r"[^a-zA-Z0-9_]", "_", self.skill_id)
            return {
                "name": f"skill_{safe}",
                "description": f"[Skill] {self.description}",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "要执行的操作",
                        },
                        "params": {
                            "type": "object",
                            "description": "操作参数",
                        },
                    },
                    "required": ["action"],
                },
            }

    def _get_input_schema(self) -> dict:
        """
        获取系统技能的 input_schema

        从 SKILL.md 的 body 中解析参数定义，或使用默认 schema
        """
        # 默认返回空 object schema
        # 实际参数定义应该在 SKILL.md 的 body 中或单独的元数据中
        return {
            "type": "object",
            "properties": {},
        }


class SkillRegistry:
    """
    技能注册中心

    管理所有已注册的技能，提供:
    - 注册/注销
    - 搜索/查找
    - 渐进式加载

    内部以 skill_id（目录名）做唯一 key，对外查找方法同时接受
    skill_id 和 name（向后兼容：先匹配 skill_id，未命中则回退到 name）。
    """

    def __init__(self):
        self._skills: dict[str, SkillEntry] = {}  # key = skill_id

    def _resolve(self, key: str) -> SkillEntry | None:
        """按 skill_id 查找，未命中时回退到 name 匹配（向后兼容）。"""
        entry = self._skills.get(key)
        if entry is not None:
            return entry
        for e in self._skills.values():
            if e.name == key:
                return e
        return None

    def _resolve_id(self, key: str) -> str | None:
        """将 key 解析为实际的 skill_id。"""
        if key in self._skills:
            return key
        for sid, e in self._skills.items():
            if e.name == key:
                return sid
        return None

    def register(self, skill: "ParsedSkill", skill_id: str | None = None) -> None:
        """
        注册技能

        Args:
            skill: 解析后的技能对象
            skill_id: 唯一标识（通常为目录名）。未提供时回退到 metadata.name。
        """
        entry = SkillEntry.from_parsed_skill(skill, skill_id=skill_id)

        if entry.skill_id in self._skills:
            logger.warning(f"Skill '{entry.skill_id}' already registered, overwriting")

        self._skills[entry.skill_id] = entry
        logger.info(f"Registered skill: {entry.skill_id} (name={entry.name})")

    def unregister(self, key: str) -> bool:
        """
        注销技能

        Args:
            key: skill_id 或 name（向后兼容）

        Returns:
            是否成功
        """
        sid = self._resolve_id(key)
        if sid is not None:
            del self._skills[sid]
            logger.info(f"Unregistered skill: {sid}")
            return True
        return False

    def get(self, key: str) -> SkillEntry | None:
        """
        获取技能

        Args:
            key: skill_id 或 name（向后兼容）

        Returns:
            SkillEntry 或 None
        """
        return self._resolve(key)

    def has(self, key: str) -> bool:
        """检查技能是否存在（接受 skill_id 或 name）"""
        return self._resolve(key) is not None

    def set_disabled(self, key: str, disabled: bool = True) -> bool:
        """设置技能的 disabled 标记。接受 skill_id 或 name。"""
        skill = self._resolve(key)
        if skill is not None:
            skill.disabled = disabled
            return True
        return False

    def list_all(self, include_disabled: bool = True) -> list[SkillEntry]:
        """列出所有技能。

        Args:
            include_disabled: 是否包含被用户禁用的技能，默认 True 保持向后兼容。
        """
        if include_disabled:
            return list(self._skills.values())
        return [s for s in self._skills.values() if not s.disabled]

    def list_enabled(self) -> list[SkillEntry]:
        """列出所有已启用的技能（排除 disabled=True）。"""
        return [s for s in self._skills.values() if not s.disabled]

    def list_metadata(self) -> list[dict]:
        """
        列出已启用技能元数据 (Level 1)

        用于启动时向 LLM 展示可用技能
        """
        return [
            {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "description": skill.description,
                "auto_invoke": not skill.disable_model_invocation,
            }
            for skill in self._skills.values()
            if not skill.disabled
        ]

    def search(
        self,
        query: str,
        include_disabled: bool = False,
    ) -> list[SkillEntry]:
        """
        搜索技能

        Args:
            query: 搜索词 (匹配 skill_id、名称或描述)
            include_disabled: 是否包含禁用自动调用的技能

        Returns:
            匹配的技能列表
        """
        results = []
        query_lower = query.lower()

        for skill in self._skills.values():
            if not include_disabled and (skill.disabled or skill.disable_model_invocation):
                continue

            if (
                query_lower in skill.skill_id.lower()
                or query_lower in skill.name.lower()
                or query_lower in skill.description.lower()
            ):
                results.append(skill)

        return results

    def find_relevant(self, context: str) -> list[SkillEntry]:
        """
        根据上下文查找相关技能

        用于 Agent 决定是否激活某个技能

        Args:
            context: 上下文文本 (如用户输入)

        Returns:
            可能相关的技能列表
        """
        relevant = []
        context_lower = context.lower()

        for skill in self._skills.values():
            if skill.disabled:
                continue
            if skill.disable_model_invocation:
                continue

            # 检查描述中的关键词
            desc_words = skill.description.lower().split()
            for word in desc_words:
                if len(word) > 3 and word in context_lower:
                    relevant.append(skill)
                    break

        return relevant

    def get_tool_schemas(self) -> list[dict]:
        """
        获取已启用技能的工具 schema

        用于将技能作为工具提供给 LLM（排除 disabled 技能）
        """
        return [skill.to_tool_schema() for skill in self._skills.values() if not skill.disabled]

    def list_system_skills(self) -> list[SkillEntry]:
        """列出所有系统技能"""
        return [s for s in self._skills.values() if s.system]

    def list_external_skills(self) -> list[SkillEntry]:
        """列出所有外部技能（非系统技能）"""
        return [s for s in self._skills.values() if not s.system]

    def get_by_tool_name(self, tool_name: str) -> SkillEntry | None:
        """
        根据原工具名称查找技能

        Args:
            tool_name: 原工具名称（如 'browser_navigate'）

        Returns:
            SkillEntry 或 None
        """
        for skill in self._skills.values():
            if skill.tool_name == tool_name:
                return skill
        return None

    def get_by_handler(self, handler: str) -> list[SkillEntry]:
        """
        根据处理器名称获取所有相关技能

        Args:
            handler: 处理器名称（如 'browser'）

        Returns:
            技能列表
        """
        return [s for s in self._skills.values() if s.handler == handler]

    @property
    def count(self) -> int:
        """技能数量"""
        return len(self._skills)

    @property
    def system_count(self) -> int:
        """系统技能数量"""
        return len(self.list_system_skills())

    @property
    def external_count(self) -> int:
        """外部技能数量"""
        return len(self.list_external_skills())

    def __contains__(self, key: str) -> bool:
        return self.has(key)

    def __len__(self) -> int:
        return self.count

    def __iter__(self):
        return iter(self._skills.values())

    def __bool__(self) -> bool:
        """确保空 registry 不被误判为 falsy"""
        return True


# 全局注册中心
default_registry = SkillRegistry()


def register_skill(skill: "ParsedSkill", skill_id: str | None = None) -> None:
    """注册技能到默认注册中心"""
    default_registry.register(skill, skill_id=skill_id)


def get_skill(key: str) -> SkillEntry | None:
    """从默认注册中心获取技能（接受 skill_id 或 name）"""
    return default_registry.get(key)
