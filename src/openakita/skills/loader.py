"""
技能加载器

遵循 Agent Skills 规范 (agentskills.io/specification)
从标准目录结构加载 SKILL.md 定义的技能
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .parser import ParsedSkill, SkillParser, SkillMetadata
from .registry import SkillRegistry

logger = logging.getLogger(__name__)


# 标准技能目录 (按优先级排序)
SKILL_DIRECTORIES = [
    # 项目级别
    ".cursor/skills",
    ".claude/skills",
    ".codex/skills",
    "skills",
    # 用户级别 (全局)
    "~/.cursor/skills",
    "~/.claude/skills",
    "~/.codex/skills",
]

# 系统技能目录（优先加载）
SYSTEM_SKILL_DIRECTORIES = [
    "skills",  # 系统技能也放在 skills/ 目录下，通过 system: true 标记区分
]


class SkillLoader:
    """
    技能加载器
    
    支持:
    - 从标准目录自动发现技能
    - 解析 SKILL.md 文件
    - 加载技能脚本
    - 渐进式披露
    """
    
    def __init__(
        self,
        registry: Optional[SkillRegistry] = None,
        parser: Optional[SkillParser] = None,
    ):
        self.registry = registry if registry is not None else SkillRegistry()
        self.parser = parser or SkillParser()
        self._loaded_skills: dict[str, ParsedSkill] = {}
    
    def discover_skill_directories(self, base_path: Optional[Path] = None) -> list[Path]:
        """
        发现所有技能目录
        
        Args:
            base_path: 基础路径 (项目根目录)
        
        Returns:
            存在的技能目录列表
        """
        base_path = base_path or Path.cwd()
        directories = []
        
        for skill_dir in SKILL_DIRECTORIES:
            if skill_dir.startswith("~"):
                path = Path(skill_dir).expanduser()
            else:
                path = base_path / skill_dir
            
            if path.exists() and path.is_dir():
                directories.append(path)
                logger.debug(f"Found skill directory: {path}")
        
        return directories
    
    def load_all(self, base_path: Optional[Path] = None) -> int:
        """
        从所有标准目录加载技能
        
        Args:
            base_path: 基础路径
        
        Returns:
            加载的技能数量
        """
        directories = self.discover_skill_directories(base_path)
        loaded = 0
        
        for skill_dir in directories:
            loaded += self.load_from_directory(skill_dir)
        
        return loaded
    
    def load_from_directory(self, directory: Path) -> int:
        """
        从目录加载所有技能
        
        每个子目录如果包含 SKILL.md 则被视为一个技能
        
        Args:
            directory: 技能目录
        
        Returns:
            加载的技能数量
        """
        if not directory.exists():
            logger.warning(f"Skill directory not found: {directory}")
            return 0
        
        loaded = 0
        
        for item in directory.iterdir():
            if not item.is_dir():
                continue
            
            skill_md = item / "SKILL.md"
            if not skill_md.exists():
                continue
            
            try:
                skill = self.load_skill(item)
                if skill:
                    loaded += 1
            except Exception as e:
                logger.error(f"Failed to load skill from {item}: {e}")
        
        logger.info(f"Loaded {loaded} skills from {directory}")
        return loaded
    
    def load_skill(self, skill_dir: Path) -> Optional[ParsedSkill]:
        """
        加载单个技能
        
        Args:
            skill_dir: 技能目录
        
        Returns:
            ParsedSkill 或 None
        """
        try:
            skill = self.parser.parse_directory(skill_dir)
            
            # 验证
            errors = self.parser.validate(skill)
            if errors:
                for error in errors:
                    logger.warning(f"Skill validation warning: {error}")
            
            # 注册到 registry
            self.registry.register(skill)
            self._loaded_skills[skill.metadata.name] = skill
            
            logger.info(f"Loaded skill: {skill.metadata.name}")
            return skill
            
        except Exception as e:
            logger.error(f"Failed to load skill from {skill_dir}: {e}")
            return None
    
    def get_skill(self, name: str) -> Optional[ParsedSkill]:
        """获取已加载的技能"""
        return self._loaded_skills.get(name)
    
    def get_skill_body(self, name: str) -> Optional[str]:
        """
        获取技能的完整指令 (body)
        
        这是渐进式披露的第二级:
        - 第一级: 元数据 (name, description) - 启动时加载
        - 第二级: 完整指令 (body) - 激活时加载
        - 第三级: 资源文件 - 按需加载
        """
        skill = self._loaded_skills.get(name)
        if skill:
            return skill.body
        return None
    
    def get_script_content(self, name: str, script_name: str) -> Optional[str]:
        """
        获取技能脚本内容
        
        Args:
            name: 技能名称
            script_name: 脚本文件名
        
        Returns:
            脚本内容或 None
        """
        skill = self._loaded_skills.get(name)
        if not skill or not skill.scripts_dir:
            return None
        
        script_path = skill.scripts_dir / script_name
        if script_path.exists():
            return script_path.read_text(encoding='utf-8')
        
        return None
    
    def run_script(
        self,
        name: str,
        script_name: str,
        args: Optional[list[str]] = None,
        cwd: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """
        运行技能脚本
        
        Args:
            name: 技能名称
            script_name: 脚本文件名
            args: 命令行参数
            cwd: 工作目录
        
        Returns:
            (成功, 输出) 元组
        """
        skill = self._loaded_skills.get(name)
        if not skill or not skill.scripts_dir:
            return False, f"Skill or scripts not found: {name}"
        
        script_path = skill.scripts_dir / script_name
        if not script_path.exists():
            return False, f"Script not found: {script_name}"
        
        # 确定如何运行脚本
        args = args or []
        
        if script_path.suffix == '.py':
            cmd = [sys.executable, str(script_path)] + args
        elif script_path.suffix in ('.sh', '.bash'):
            cmd = ['bash', str(script_path)] + args
        elif script_path.suffix == '.js':
            cmd = ['node', str(script_path)] + args
        else:
            # 尝试直接运行
            cmd = [str(script_path)] + args
        
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd or skill.skill_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            
            return result.returncode == 0, output
            
        except subprocess.TimeoutExpired:
            return False, "Script execution timed out"
        except Exception as e:
            return False, f"Script execution failed: {e}"
    
    def get_reference(self, name: str, ref_name: str) -> Optional[str]:
        """
        获取技能参考文档
        
        Args:
            name: 技能名称
            ref_name: 参考文档名称 (如 REFERENCE.md)
        
        Returns:
            文档内容或 None
        """
        skill = self._loaded_skills.get(name)
        if not skill or not skill.references_dir:
            return None
        
        ref_path = skill.references_dir / ref_name
        if ref_path.exists():
            return ref_path.read_text(encoding='utf-8')
        
        return None
    
    def unload_skill(self, name: str) -> bool:
        """卸载技能"""
        if name in self._loaded_skills:
            del self._loaded_skills[name]
            self.registry.unregister(name)
            logger.info(f"Unloaded skill: {name}")
            return True
        return False
    
    def reload_skill(self, name: str) -> Optional[ParsedSkill]:
        """重新加载技能"""
        skill = self._loaded_skills.get(name)
        if not skill:
            return None
        
        skill_dir = skill.skill_dir
        self.unload_skill(name)
        return self.load_skill(skill_dir)
    
    @property
    def loaded_count(self) -> int:
        """已加载技能数量"""
        return len(self._loaded_skills)
    
    @property
    def loaded_skills(self) -> list[ParsedSkill]:
        """所有已加载的技能"""
        return list(self._loaded_skills.values())
    
    @property
    def system_skills(self) -> list[ParsedSkill]:
        """所有系统技能"""
        return [s for s in self._loaded_skills.values() if s.metadata.system]
    
    @property
    def external_skills(self) -> list[ParsedSkill]:
        """所有外部技能"""
        return [s for s in self._loaded_skills.values() if not s.metadata.system]
    
    def get_skill_by_tool_name(self, tool_name: str) -> Optional[ParsedSkill]:
        """
        根据工具名获取技能
        
        Args:
            tool_name: 原工具名称（如 'browser_navigate'）
        
        Returns:
            ParsedSkill 或 None
        """
        for skill in self._loaded_skills.values():
            if skill.metadata.tool_name == tool_name:
                return skill
        return None
    
    def get_skills_by_handler(self, handler: str) -> list[ParsedSkill]:
        """
        根据处理器名获取所有相关技能
        
        Args:
            handler: 处理器名称（如 'browser'）
        
        Returns:
            技能列表
        """
        return [
            s for s in self._loaded_skills.values()
            if s.metadata.handler == handler
        ]
    
    def get_tool_definitions(self) -> list[dict]:
        """
        获取所有系统技能的工具定义
        
        用于传递给 LLM API 的 tools 参数
        
        Returns:
            工具定义列表
        """
        from ..tools.definitions import BASE_TOOLS
        
        definitions = []
        
        # 从系统技能生成工具定义
        for skill in self.system_skills:
            # 查找对应的原始工具定义
            original_def = None
            for tool in BASE_TOOLS:
                if tool.get("name") == skill.metadata.tool_name:
                    original_def = tool
                    break
            
            if original_def:
                # 使用原始定义但更新描述（如果 SKILL.md 中有更详细的）
                tool_def = original_def.copy()
                # 可以在这里用 SKILL.md 中的描述覆盖
                definitions.append(tool_def)
            else:
                # 没有原始定义，从 SKILL.md 生成
                definitions.append({
                    "name": skill.metadata.tool_name,
                    "description": skill.metadata.description,
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                    },
                })
        
        return definitions
    
    def is_system_skill(self, name: str) -> bool:
        """检查是否为系统技能"""
        skill = self._loaded_skills.get(name)
        return skill.metadata.system if skill else False
    
    def get_handler_name(self, name: str) -> Optional[str]:
        """获取技能的处理器名称"""
        skill = self._loaded_skills.get(name)
        return skill.metadata.handler if skill else None
