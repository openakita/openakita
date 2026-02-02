"""
技能注册中心

遵循 Agent Skills 规范 (agentskills.io/specification)
存储和管理技能元数据，支持渐进式披露
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .parser import ParsedSkill, SkillMetadata

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
    name: str
    description: str
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False
    
    # 系统技能专用字段
    system: bool = False
    handler: Optional[str] = None
    tool_name: Optional[str] = None
    category: Optional[str] = None
    
    # 技能路径 (用于延迟加载)
    skill_path: Optional[str] = None
    
    # 完整技能对象引用 (延迟加载)
    _parsed_skill: Optional["ParsedSkill"] = field(default=None, repr=False)
    
    @classmethod
    def from_parsed_skill(cls, skill: "ParsedSkill") -> "SkillEntry":
        """从 ParsedSkill 创建条目"""
        meta = skill.metadata
        return cls(
            name=meta.name,
            description=meta.description,
            license=meta.license,
            compatibility=meta.compatibility,
            metadata=meta.metadata,
            allowed_tools=meta.allowed_tools,
            disable_model_invocation=meta.disable_model_invocation,
            system=meta.system,
            handler=meta.handler,
            tool_name=meta.tool_name,
            category=meta.category,
            skill_path=str(skill.path),
            _parsed_skill=skill,
        )
    
    def get_body(self) -> Optional[str]:
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
            # 外部技能：使用 skill_ 前缀
            return {
                "name": f"skill_{self.name.replace('-', '_')}",
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
    """
    
    def __init__(self):
        self._skills: dict[str, SkillEntry] = {}
    
    def register(self, skill: "ParsedSkill") -> None:
        """
        注册技能
        
        Args:
            skill: 解析后的技能对象
        """
        entry = SkillEntry.from_parsed_skill(skill)
        
        if entry.name in self._skills:
            logger.warning(f"Skill '{entry.name}' already registered, overwriting")
        
        self._skills[entry.name] = entry
        logger.info(f"Registered skill: {entry.name}")
    
    def unregister(self, name: str) -> bool:
        """
        注销技能
        
        Args:
            name: 技能名称
        
        Returns:
            是否成功
        """
        if name in self._skills:
            del self._skills[name]
            logger.info(f"Unregistered skill: {name}")
            return True
        return False
    
    def get(self, name: str) -> Optional[SkillEntry]:
        """
        获取技能
        
        Args:
            name: 技能名称
        
        Returns:
            SkillEntry 或 None
        """
        return self._skills.get(name)
    
    def has(self, name: str) -> bool:
        """检查技能是否存在"""
        return name in self._skills
    
    def list_all(self) -> list[SkillEntry]:
        """列出所有技能"""
        return list(self._skills.values())
    
    def list_metadata(self) -> list[dict]:
        """
        列出所有技能元数据 (Level 1)
        
        用于启动时向 LLM 展示可用技能
        """
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "auto_invoke": not skill.disable_model_invocation,
            }
            for skill in self._skills.values()
        ]
    
    def search(
        self,
        query: str,
        include_disabled: bool = False,
    ) -> list[SkillEntry]:
        """
        搜索技能
        
        Args:
            query: 搜索词 (匹配名称或描述)
            include_disabled: 是否包含禁用自动调用的技能
        
        Returns:
            匹配的技能列表
        """
        results = []
        query_lower = query.lower()
        
        for skill in self._skills.values():
            if not include_disabled and skill.disable_model_invocation:
                continue
            
            if (
                query_lower in skill.name.lower() or
                query_lower in skill.description.lower()
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
            # 跳过禁用自动调用的技能
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
        获取所有技能的工具 schema
        
        用于将技能作为工具提供给 LLM
        """
        return [skill.to_tool_schema() for skill in self._skills.values()]
    
    def list_system_skills(self) -> list[SkillEntry]:
        """列出所有系统技能"""
        return [s for s in self._skills.values() if s.system]
    
    def list_external_skills(self) -> list[SkillEntry]:
        """列出所有外部技能（非系统技能）"""
        return [s for s in self._skills.values() if not s.system]
    
    def get_by_tool_name(self, tool_name: str) -> Optional[SkillEntry]:
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
    
    def __contains__(self, name: str) -> bool:
        return self.has(name)
    
    def __len__(self) -> int:
        return self.count
    
    def __iter__(self):
        return iter(self._skills.values())
    
    def __bool__(self) -> bool:
        """确保空 registry 不被误判为 falsy"""
        return True


# 全局注册中心
default_registry = SkillRegistry()


def register_skill(skill: "ParsedSkill") -> None:
    """注册技能到默认注册中心"""
    default_registry.register(skill)


def get_skill(name: str) -> Optional[SkillEntry]:
    """从默认注册中心获取技能"""
    return default_registry.get(name)
