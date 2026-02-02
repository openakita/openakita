"""
SKILL.md 解析器

遵循 Agent Skills 规范 (agentskills.io/specification)
解析 SKILL.md 文件的 YAML frontmatter 和 Markdown body
"""

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """
    技能元数据 (来自 YAML frontmatter)
    
    必需字段:
    - name: 技能名称 (1-64字符, 小写字母/数字/连字符)
    - description: 技能描述 (1-1024字符)
    
    可选字段:
    - license: 许可证
    - compatibility: 环境要求
    - metadata: 额外元数据
    - allowed_tools: 预授权工具列表
    - disable_model_invocation: 是否禁用自动调用
    
    系统技能字段 (system: true):
    - system: 是否为系统技能（内置，不可卸载）
    - handler: 处理器模块名（如 'browser', 'filesystem'）
    - tool_name: 原工具名称（用于兼容，如 'browser_navigate'）
    - category: 工具分类（如 'Browser', 'File System'）
    """
    name: str
    description: str
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False
    
    # 系统技能专用字段
    system: bool = False  # 是否为系统技能
    handler: Optional[str] = None  # 处理器模块名
    tool_name: Optional[str] = None  # 原工具名称（用于兼容）
    category: Optional[str] = None  # 工具分类
    
    def __post_init__(self):
        """验证字段"""
        self._validate_name()
        self._validate_description()
    
    def _validate_name(self):
        """验证 name 字段"""
        if not self.name:
            raise ValueError("name field is required")
        
        if len(self.name) > 64:
            raise ValueError(f"name must be <= 64 characters, got {len(self.name)}")
        
        # 只允许小写字母、数字、连字符
        pattern = r'^[a-z0-9]+(-[a-z0-9]+)*$'
        if not re.match(pattern, self.name):
            raise ValueError(
                f"name must contain only lowercase letters, numbers, and hyphens. "
                f"Cannot start/end with hyphen or have consecutive hyphens. Got: {self.name}"
            )
    
    def _validate_description(self):
        """验证 description 字段"""
        if not self.description:
            raise ValueError("description field is required")
        
        if len(self.description) > 1024:
            raise ValueError(f"description must be <= 1024 characters, got {len(self.description)}")


@dataclass
class ParsedSkill:
    """
    解析后的技能
    
    包含元数据和完整的 SKILL.md 内容
    """
    metadata: SkillMetadata
    body: str  # Markdown body
    path: Path  # SKILL.md 文件路径
    
    # 可选目录
    scripts_dir: Optional[Path] = None
    references_dir: Optional[Path] = None
    assets_dir: Optional[Path] = None
    
    @property
    def skill_dir(self) -> Path:
        """技能根目录"""
        return self.path.parent
    
    def get_scripts(self) -> list[Path]:
        """获取 scripts/ 目录下的所有脚本"""
        if self.scripts_dir and self.scripts_dir.exists():
            return list(self.scripts_dir.iterdir())
        return []
    
    def get_references(self) -> list[Path]:
        """获取 references/ 目录下的所有文档"""
        if self.references_dir and self.references_dir.exists():
            return [f for f in self.references_dir.iterdir() if f.suffix == '.md']
        return []
    
    def get_assets(self) -> list[Path]:
        """获取 assets/ 目录下的所有资源"""
        if self.assets_dir and self.assets_dir.exists():
            return list(self.assets_dir.iterdir())
        return []


class SkillParser:
    """
    SKILL.md 解析器
    
    解析符合 Agent Skills 规范的 SKILL.md 文件
    """
    
    # YAML frontmatter 正则
    FRONTMATTER_PATTERN = re.compile(
        r'^---\s*\n(.*?)\n---\s*\n(.*)$',
        re.DOTALL
    )
    
    def parse_file(self, path: Path) -> ParsedSkill:
        """
        解析 SKILL.md 文件
        
        Args:
            path: SKILL.md 文件路径
        
        Returns:
            ParsedSkill 对象
        
        Raises:
            ValueError: 解析失败
            FileNotFoundError: 文件不存在
        """
        if not path.exists():
            raise FileNotFoundError(f"SKILL.md not found: {path}")
        
        content = path.read_text(encoding='utf-8')
        return self.parse_content(content, path)
    
    def parse_content(self, content: str, path: Path) -> ParsedSkill:
        """
        解析 SKILL.md 内容
        
        Args:
            content: 文件内容
            path: 文件路径 (用于定位相关目录)
        
        Returns:
            ParsedSkill 对象
        """
        # 解析 frontmatter
        match = self.FRONTMATTER_PATTERN.match(content)
        if not match:
            raise ValueError(f"Invalid SKILL.md format: missing YAML frontmatter in {path}")
        
        yaml_content = match.group(1)
        body = match.group(2).strip()
        
        # 解析 YAML
        try:
            data = yaml.safe_load(yaml_content) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML frontmatter in {path}: {e}")
        
        # 构建元数据
        metadata = self._build_metadata(data, path)
        
        # 验证目录名匹配
        skill_dir = path.parent
        if skill_dir.name != metadata.name:
            logger.warning(
                f"Skill directory name '{skill_dir.name}' does not match "
                f"skill name '{metadata.name}' in {path}"
            )
        
        # 查找可选目录
        scripts_dir = skill_dir / 'scripts'
        references_dir = skill_dir / 'references'
        assets_dir = skill_dir / 'assets'
        
        return ParsedSkill(
            metadata=metadata,
            body=body,
            path=path,
            scripts_dir=scripts_dir if scripts_dir.exists() else None,
            references_dir=references_dir if references_dir.exists() else None,
            assets_dir=assets_dir if assets_dir.exists() else None,
        )
    
    def _build_metadata(self, data: dict, path: Path) -> SkillMetadata:
        """从 YAML 数据构建元数据"""
        # 必需字段
        name = data.get('name')
        description = data.get('description')
        
        if not name:
            raise ValueError(f"Missing required 'name' field in {path}")
        if not description:
            raise ValueError(f"Missing required 'description' field in {path}")
        
        # 处理 allowed-tools (连字符转下划线)
        allowed_tools = data.get('allowed-tools', '')
        if isinstance(allowed_tools, str):
            allowed_tools = allowed_tools.split() if allowed_tools else []
        
        # 系统技能字段
        system = data.get('system', False)
        handler = data.get('handler')
        tool_name = data.get('tool-name') or data.get('tool_name')  # 支持两种格式
        category = data.get('category')
        
        # 如果是系统技能但没有指定 tool_name，从 name 生成
        if system and not tool_name:
            tool_name = name.replace('-', '_')
        
        return SkillMetadata(
            name=name,
            description=description.strip(),
            license=data.get('license'),
            compatibility=data.get('compatibility'),
            metadata=data.get('metadata', {}),
            allowed_tools=allowed_tools,
            disable_model_invocation=data.get('disable-model-invocation', False),
            system=system,
            handler=handler,
            tool_name=tool_name,
            category=category,
        )
    
    def parse_directory(self, skill_dir: Path) -> ParsedSkill:
        """
        解析技能目录
        
        Args:
            skill_dir: 技能目录路径
        
        Returns:
            ParsedSkill 对象
        """
        skill_md = skill_dir / 'SKILL.md'
        return self.parse_file(skill_md)
    
    def validate(self, skill: ParsedSkill) -> list[str]:
        """
        验证技能
        
        Returns:
            错误消息列表 (空列表表示验证通过)
        """
        errors = []
        
        # 检查目录名匹配
        if skill.skill_dir.name != skill.metadata.name:
            errors.append(
                f"Directory name '{skill.skill_dir.name}' must match "
                f"skill name '{skill.metadata.name}'"
            )
        
        # 检查 body 长度 (建议 < 5000 tokens, 约 500 行)
        body_lines = skill.body.count('\n') + 1
        if body_lines > 500:
            errors.append(
                f"SKILL.md body has {body_lines} lines. "
                f"Recommended: keep under 500 lines for efficient context usage."
            )
        
        return errors


# 全局解析器实例
skill_parser = SkillParser()


def parse_skill(path: Path) -> ParsedSkill:
    """便捷函数：解析技能"""
    return skill_parser.parse_file(path)


def parse_skill_directory(skill_dir: Path) -> ParsedSkill:
    """便捷函数：解析技能目录"""
    return skill_parser.parse_directory(skill_dir)
