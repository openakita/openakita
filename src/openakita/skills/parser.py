"""
SKILL.md 解析器

遵循 Agent Skills 规范 (agentskills.io/specification)
解析 SKILL.md 文件的 YAML frontmatter 和 Markdown body
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

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
    version: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False

    # 系统技能专用字段
    system: bool = False  # 是否为系统技能
    handler: str | None = None  # 处理器模块名
    tool_name: str | None = None  # 原工具名称（用于兼容）
    category: str | None = None  # 工具分类

    # metadata.openakita structured fields
    supported_os: list[str] = field(default_factory=list)
    required_bins: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)

    # 配置 schema（供 Setup Center 自动生成配置表单）
    # 每个元素: {"key": str, "label": str, "type": "text"|"secret"|"number"|"select"|"bool",
    #            "required": bool, "help": str, "default": Any, "options": list, "min": num, "max": num}
    config: list[dict] = field(default_factory=list)

    # 国际化（由 .openakita-i18n.json sidecar 文件注入，不存储在 frontmatter 中）
    # key 为语言代码 (如 "zh")，value 为该语言的显示名/描述
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)

    def get_display_name(self, lang: str = "zh") -> str:
        """按语言返回显示名称，找不到则回退到 name"""
        return self.name_i18n.get(lang, self.name)

    def get_display_description(self, lang: str = "zh") -> str:
        """按语言返回显示描述，找不到则回退到 description"""
        return self.description_i18n.get(lang, self.description)

    def __post_init__(self):
        """验证字段"""
        self._validate_name()
        self._validate_description()

    def _validate_name(self):
        """验证 name 字段。

        支持两种格式:
        - 简单名:  ``my-skill``
        - 命名空间: ``owner/repo@skill-name``

        自动修正: 下划线转连字符、大写转小写。
        """
        if not self.name:
            raise ValueError("name field is required")

        # auto-fix: underscores → hyphens, lowercase
        normalized = self.name.replace("_", "-").lower()
        if normalized != self.name:
            logger.debug(f"Skill name auto-normalized: '{self.name}' → '{normalized}'")
            self.name = normalized

        if len(self.name) > 128:
            raise ValueError(f"name must be <= 128 characters, got {len(self.name)}")

        _SIMPLE = r"[a-z0-9]+(-[a-z0-9]+)*"
        _NAMESPACE = rf"{_SIMPLE}/{_SIMPLE}@{_SIMPLE}"
        pattern = rf"^({_NAMESPACE}|{_SIMPLE})$"
        if not re.match(pattern, self.name):
            raise ValueError(
                f"name must be lowercase alphanumeric with hyphens, "
                f"optionally namespaced as 'owner/repo@skill-name'. Got: {self.name}"
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
    scripts_dir: Path | None = None
    references_dir: Path | None = None
    assets_dir: Path | None = None

    @property
    def skill_dir(self) -> Path:
        """技能根目录"""
        return self.path.parent

    _SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".js"}

    def get_scripts(self) -> list[Path]:
        """获取所有可用脚本（scripts/ 目录优先，兼容根目录放置脚本的外部技能）"""
        if self.scripts_dir and self.scripts_dir.exists():
            return list(self.scripts_dir.iterdir())
        return [
            f for f in self.skill_dir.iterdir()
            if f.is_file() and f.suffix in self._SCRIPT_SUFFIXES
        ]

    def get_references(self) -> list[Path]:
        """获取 references/ 目录下的所有文档"""
        if self.references_dir and self.references_dir.exists():
            return [f for f in self.references_dir.iterdir() if f.suffix == ".md"]
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
    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

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

        content = path.read_text(encoding="utf-8")
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
        if match:
            yaml_content = match.group(1)
            body = match.group(2).strip()

            try:
                data = yaml.safe_load(yaml_content) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML frontmatter in {path}: {e}")

            metadata = self._build_metadata(data, path)
        else:
            # Fallback: no frontmatter — infer metadata from directory name + content
            metadata, body = self._infer_metadata(content, path)
            logger.warning(
                f"SKILL.md missing YAML frontmatter, loaded with inferred metadata: "
                f"{path} (name={metadata.name})"
            )

        # 验证目录名匹配（命名空间格式取 @ 后部分比较）
        skill_dir = path.parent
        expected_dir = metadata.name.split("@", 1)[-1] if "@" in metadata.name else metadata.name
        if skill_dir.name != expected_dir:
            logger.warning(
                f"Skill directory name '{skill_dir.name}' does not match "
                f"expected '{expected_dir}' (from skill name '{metadata.name}') in {path}"
            )

        # 查找可选目录
        scripts_dir = skill_dir / "scripts"
        references_dir = skill_dir / "references"
        assets_dir = skill_dir / "assets"

        return ParsedSkill(
            metadata=metadata,
            body=body,
            path=path,
            scripts_dir=scripts_dir if scripts_dir.exists() else None,
            references_dir=references_dir if references_dir.exists() else None,
            assets_dir=assets_dir if assets_dir.exists() else None,
        )

    @staticmethod
    def _infer_metadata(content: str, path: Path) -> tuple[SkillMetadata, str]:
        """从目录名和文件内容推导元数据（frontmatter 缺失时的 fallback）。

        - name: 目录名（下划线转连字符，小写）
        - description: 首个 Markdown 标题或首行非空文本
        - body: 完整文件内容
        """
        dir_name = path.parent.name
        name = dir_name.replace("_", "-").lower()

        description = ""
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                description = stripped.lstrip("#").strip()
            else:
                description = stripped
            break
        if not description:
            description = name

        # 截断过长的 description
        if len(description) > 1024:
            description = description[:1021] + "..."

        metadata = SkillMetadata(name=name, description=description)
        return metadata, content.strip()

    def _build_metadata(self, data: dict, path: Path) -> SkillMetadata:
        """从 YAML 数据构建元数据"""
        # 必需字段
        name = data.get("name")
        description = data.get("description")

        if not name:
            raise ValueError(f"Missing required 'name' field in {path}")
        if not description:
            raise ValueError(f"Missing required 'description' field in {path}")

        # 处理 allowed-tools (连字符转下划线)
        allowed_tools = data.get("allowed-tools", "")
        if isinstance(allowed_tools, str):
            allowed_tools = allowed_tools.split() if allowed_tools else []

        # 系统技能字段
        system = data.get("system", False)
        handler = data.get("handler")
        tool_name = data.get("tool-name") or data.get("tool_name")  # 支持两种格式
        category = data.get("category")

        # 如果是系统技能但没有指定 tool_name，从 name 生成
        if system and not tool_name:
            tool_name = name.replace("-", "_")

        # 配置 schema
        config_raw = data.get("config", [])
        config: list[dict] = []
        if isinstance(config_raw, list):
            for item in config_raw:
                if isinstance(item, dict) and "key" in item:
                    config.append({
                        "key": str(item["key"]),
                        "label": str(item.get("label", item["key"])),
                        "type": str(item.get("type", "text")),
                        "required": bool(item.get("required", False)),
                        "help": str(item.get("help", "")),
                        "default": item.get("default"),
                        "options": item.get("options"),
                        "min": item.get("min"),
                        "max": item.get("max"),
                    })

        # Extract metadata.openakita structured fields
        raw_metadata = data.get("metadata", {})
        akita_meta = raw_metadata.get("openakita", {}) if isinstance(raw_metadata, dict) else {}
        if not isinstance(akita_meta, dict):
            akita_meta = {}

        supported_os: list[str] = []
        required_bins: list[str] = []
        required_env: list[str] = []

        if akita_meta:
            os_val = akita_meta.get("os", [])
            if isinstance(os_val, list):
                supported_os = [str(o) for o in os_val]
            elif isinstance(os_val, str):
                supported_os = [o.strip() for o in os_val.split(",") if o.strip()]

            requires = akita_meta.get("requires", {})
            if isinstance(requires, dict):
                bins_val = requires.get("bins", [])
                if isinstance(bins_val, list):
                    required_bins = [str(b) for b in bins_val]
                env_val = requires.get("env", [])
                if isinstance(env_val, list):
                    required_env = [str(e) for e in env_val]

        return SkillMetadata(
            name=name,
            description=description.strip(),
            version=data.get("version"),
            license=data.get("license"),
            compatibility=data.get("compatibility"),
            metadata=raw_metadata if isinstance(raw_metadata, dict) else {},
            allowed_tools=allowed_tools,
            disable_model_invocation=data.get("disable-model-invocation", False),
            system=system,
            handler=handler,
            tool_name=tool_name,
            category=category,
            supported_os=supported_os,
            required_bins=required_bins,
            required_env=required_env,
            config=config,
        )

    def parse_directory(self, skill_dir: Path) -> ParsedSkill:
        """
        解析技能目录

        Args:
            skill_dir: 技能目录路径

        Returns:
            ParsedSkill 对象
        """
        skill_md = skill_dir / "SKILL.md"
        return self.parse_file(skill_md)

    def validate(self, skill: ParsedSkill) -> list[str]:
        """
        验证技能

        Returns:
            错误消息列表 (空列表表示验证通过)
        """
        errors = []

        # 检查目录名匹配（命名空间格式取 @ 后部分）
        expected_dir = (
            skill.metadata.name.split("@", 1)[-1]
            if "@" in skill.metadata.name
            else skill.metadata.name
        )
        if skill.skill_dir.name != expected_dir:
            errors.append(
                f"Directory name '{skill.skill_dir.name}' should match "
                f"expected '{expected_dir}' (from skill name '{skill.metadata.name}')"
            )

        # 检查 body 长度 (建议 < 5000 tokens, 约 500 行)
        body_lines = skill.body.count("\n") + 1
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
