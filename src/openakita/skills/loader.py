"""
技能加载器

遵循 Agent Skills 规范 (agentskills.io/specification)
从标准目录结构加载 SKILL.md 定义的技能
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .parser import ParsedSkill, SkillParser
from .registry import SkillRegistry

logger = logging.getLogger(__name__)

def _builtin_skills_root() -> Path | None:
    """
    返回内置技能目录（随 wheel 分发）。

    期望结构：
    openakita/
      builtin_skills/
        system/<tool-name>/SKILL.md
    """
    try:
        root = Path(__file__).resolve().parents[1] / "builtin_skills"
        return root if root.exists() and root.is_dir() else None
    except Exception:
        return None


# 标准技能目录 (按优先级排序)
SKILL_DIRECTORIES = [
    # 内置系统技能（随 pip 包分发，优先级最高）
    "__builtin__",
    # 用户工作区（用户安装/创建的技能，打包版本的主技能目录）
    "~/.openakita/workspaces/default/skills",
    # 项目级别（开发模式下仍可扫描）
    ".cursor/skills",
    ".claude/skills",
    ".codex/skills",
    "skills",
    # 用户级别 (全局，兼容其他产品的技能)
    "~/.cursor/skills",
    "~/.claude/skills",
    "~/.codex/skills",
]

# 系统技能目录（优先加载）
SYSTEM_SKILL_DIRECTORIES = [
    "skills",  # 系统技能也放在 skills/ 目录下，通过 system: true 标记区分
]

# 打包时默认不启用的外部技能（新安装 / 无 data/skills.json 时生效）。
# 用户通过前端面板手动勾选后会创建 skills.json，之后以用户选择为准。
DEFAULT_DISABLED_SKILLS: frozenset[str] = frozenset({
    "algorithmic-art",
    "brand-guidelines",
    "changelog-generator",
    "code-reviewer",
    "frontend-design",
    "github-automation",
    "gmail-automation",
    "google-calendar-automation",
    "image-understander",
    "internal-comms",
    "moltbook",
    "slack-gif-creator",
    "theme-factory",
    "video-downloader",
    "webapp-testing",
})


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
        registry: SkillRegistry | None = None,
        parser: SkillParser | None = None,
    ):
        self.registry = registry if registry is not None else SkillRegistry()
        self.parser = parser or SkillParser()
        self._loaded_skills: dict[str, ParsedSkill] = {}

    def discover_skill_directories(self, base_path: Path | None = None) -> list[Path]:
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
            if skill_dir == "__builtin__":
                builtin = _builtin_skills_root()
                if builtin is not None:
                    directories.append(builtin)
                    logger.debug(f"Found builtin skill directory: {builtin}")
                continue

            if skill_dir.startswith("~"):
                path = Path(skill_dir).expanduser()
            else:
                path = base_path / skill_dir

            if path.exists() and path.is_dir():
                directories.append(path)
                logger.debug(f"Found skill directory: {path}")

        return directories

    def load_all(self, base_path: Path | None = None) -> int:
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

        每个子目录如果包含 SKILL.md 则被视为一个技能。
        特殊处理: 'system' 子目录会被递归扫描，用于存放系统工具 skill。

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
            if skill_md.exists():
                # 有 SKILL.md，作为技能加载
                try:
                    skill = self.load_skill(item)
                    if skill:
                        loaded += 1
                except Exception as e:
                    logger.error(f"Failed to load skill from {item}: {e}")
            elif item.name == "system":
                # 'system' 子目录：递归加载系统工具 skill
                loaded += self.load_from_directory(item)

        logger.info(f"Loaded {loaded} skills from {directory}")
        return loaded

    def load_skill(self, skill_dir: Path) -> ParsedSkill | None:
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

    def get_skill(self, name: str) -> ParsedSkill | None:
        """获取已加载的技能"""
        return self._loaded_skills.get(name)

    def get_skill_body(self, name: str) -> str | None:
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

    def compute_effective_allowlist(
        self, external_allowlist: set[str] | None
    ) -> set[str] | None:
        """根据 skills.json 的 allowlist 和默认禁用列表，计算最终的有效 allowlist。

        - skills.json 存在且有 external_allowlist -> 直接使用（用户显式选择）
        - skills.json 不存在（external_allowlist is None）-> 用全部外部技能 - DEFAULT_DISABLED_SKILLS
        """
        if external_allowlist is not None:
            return external_allowlist

        if not DEFAULT_DISABLED_SKILLS:
            return None

        all_external = {
            name
            for name, skill in self._loaded_skills.items()
            if not getattr(skill.metadata, "system", False)
        }
        return all_external - DEFAULT_DISABLED_SKILLS

    def prune_external_by_allowlist(self, external_allowlist: set[str] | None) -> int:
        """
        根据外部技能 allowlist 裁剪已加载技能。

        约定：
        - system 技能永远保留
        - external_allowlist 为 None 表示“不做限制（外部技能全部启用）”
        - external_allowlist 为 set() 表示“禁用所有外部技能”
        """
        if external_allowlist is None:
            return 0

        removed = 0
        for name, skill in list(self._loaded_skills.items()):
            try:
                if getattr(skill.metadata, "system", False):
                    continue
            except Exception:
                # 保守：解析异常时不裁剪
                continue

            if name not in external_allowlist:
                self._loaded_skills.pop(name, None)
                try:
                    self.registry.unregister(name)
                except Exception:
                    pass
                removed += 1

        if removed:
            logger.info(f"Pruned {removed} external skills by allowlist")
        return removed

    def get_script_content(self, name: str, script_name: str) -> str | None:
        """
        获取技能脚本内容

        Args:
            name: 技能名称
            script_name: 脚本文件名

        Returns:
            脚本内容或 None
        """
        skill = self._loaded_skills.get(name)
        if not skill:
            return None

        script_path = self._resolve_script_path(skill, script_name)
        if script_path:
            return script_path.read_text(encoding="utf-8")

        return None

    _SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".bash", ".js", ".ts", ".mjs"})
    _SCRIPT_IGNORE = frozenset({"__init__.py", "__pycache__"})

    def _list_available_scripts(self, skill: ParsedSkill) -> list[str]:
        """列出技能中所有可执行脚本（scripts/ 递归 + 根目录顶层）。"""
        scripts: list[str] = []

        if skill.scripts_dir and skill.scripts_dir.is_dir():
            for f in sorted(skill.scripts_dir.rglob("*")):
                if (
                    f.is_file()
                    and f.suffix in self._SCRIPT_SUFFIXES
                    and f.name not in self._SCRIPT_IGNORE
                ):
                    rel = f.relative_to(skill.scripts_dir)
                    scripts.append(f"scripts/{rel.as_posix()}")

        for f in sorted(skill.skill_dir.iterdir()):
            if (
                f.is_file()
                and f.suffix in self._SCRIPT_SUFFIXES
                and f.name not in self._SCRIPT_IGNORE
            ):
                scripts.append(f.name)

        return scripts

    def _resolve_script_path(self, skill: ParsedSkill, script_name: str) -> Path | None:
        """在技能的 scripts/ 目录和根目录中查找脚本文件。

        很多外部技能（如 Anthropic 的 xlsx、pdf 等）把脚本直接放在技能根目录
        而非 scripts/ 子目录，因此需要双重查找。
        """
        if skill.scripts_dir:
            candidate = skill.scripts_dir / script_name
            if candidate.exists():
                return candidate
        # Fallback: 技能根目录
        candidate = skill.skill_dir / script_name
        if candidate.exists():
            return candidate
        return None

    def run_script(
        self,
        name: str,
        script_name: str,
        args: list[str] | None = None,
        cwd: Path | None = None,
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
        if not skill:
            return False, f"Skill not found: {name}"

        script_path = self._resolve_script_path(skill, script_name)
        if not script_path:
            available = self._list_available_scripts(skill)
            if available:
                return False, (
                    f"Script not found: {script_name}\n"
                    f"Available scripts: {', '.join(available)}\n"
                    f"Use one of the available scripts, or use get_skill_info(\"{name}\") "
                    f"to check usage instructions."
                )
            else:
                return False, (
                    f"Script not found: {script_name}\n"
                    f"This skill has NO executable scripts — it is an instruction-only skill.\n"
                    f"DO NOT retry run_skill_script for this skill.\n"
                    f"Instead: use get_skill_info(\"{name}\") to read the skill instructions, "
                    f"then write Python code and execute it via run_shell."
                )

        # 确定如何运行脚本
        args = args or []

        if script_path.suffix == ".py":
            # PyInstaller 兼容: 使用 runtime_env 获取正确的 Python 解释器
            from openakita.runtime_env import get_python_executable
            py = get_python_executable()
            if not py:
                return False, "Python 解释器不可用，无法执行脚本"
            cmd = [py, str(script_path)] + args
        elif script_path.suffix in (".sh", ".bash"):
            bash_path = shutil.which("bash")
            if not bash_path:
                # Windows 上尝试 Git Bash 的常见路径
                if sys.platform == "win32":
                    for candidate in [
                        r"C:\Program Files\Git\bin\bash.exe",
                        r"C:\Program Files (x86)\Git\bin\bash.exe",
                    ]:
                        if Path(candidate).exists():
                            bash_path = candidate
                            break
                if not bash_path:
                    return False, (
                        f"Cannot run {script_name}: 'bash' not found on this system. "
                        f"On Windows, install Git for Windows (https://git-scm.com) to get bash."
                    )
            cmd = [bash_path, str(script_path)] + args
        elif script_path.suffix == ".js":
            cmd = ["node", str(script_path)] + args
        else:
            # 尝试直接运行
            cmd = [str(script_path)] + args

        try:
            extra: dict = {}
            if sys.platform == "win32":
                extra["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                cmd,
                cwd=cwd or skill.skill_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                **extra,
            )

            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"

            return result.returncode == 0, output

        except subprocess.TimeoutExpired:
            return False, "Script execution timed out"
        except Exception as e:
            return False, f"Script execution failed: {e}"

    def get_reference(self, name: str, ref_name: str) -> str | None:
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
            return ref_path.read_text(encoding="utf-8")

        return None

    def unload_skill(self, name: str) -> bool:
        """卸载技能"""
        if name in self._loaded_skills:
            del self._loaded_skills[name]
            self.registry.unregister(name)
            logger.info(f"Unloaded skill: {name}")
            return True
        return False

    def reload_skill(self, name: str) -> ParsedSkill | None:
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

    def get_skill_by_tool_name(self, tool_name: str) -> ParsedSkill | None:
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
        return [s for s in self._loaded_skills.values() if s.metadata.handler == handler]

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
                definitions.append(
                    {
                        "name": skill.metadata.tool_name,
                        "description": skill.metadata.description,
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                        },
                    }
                )

        return definitions

    def is_system_skill(self, name: str) -> bool:
        """检查是否为系统技能"""
        skill = self._loaded_skills.get(name)
        return skill.metadata.system if skill else False

    def get_handler_name(self, name: str) -> str | None:
        """获取技能的处理器名称"""
        skill = self._loaded_skills.get(name)
        return skill.metadata.handler if skill else None
