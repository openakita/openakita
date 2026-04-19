"""
SKILL.md parser

Follows Agent Skills specification (agentskills.io/specification)
Parses YAML frontmatter and Markdown body of SKILL.md files
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
    Skill metadata (from YAML frontmatter)

    Required fields:
    - name: Skill name (1-64 characters, lowercase letters/numbers/hyphens)
    - description: Skill description (1-1024 characters)

    Optional fields:
    - license: License
    - compatibility: Environment requirements
    - metadata: Additional metadata
    - allowed_tools: Pre-authorized tools list
    - disable_model_invocation: Whether to disable auto invocation

    System skill fields (system: true):
    - system: Whether it is a system skill (built-in, not unloadable)
    - handler: Handler module name (e.g. 'browser', 'filesystem')
    - tool_name: Original tool name (for compatibility, e.g. 'browser_navigate')
    - category: Tool category (e.g. 'Browser', 'File System')
    """

    name: str
    description: str
    version: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False

    # System skill-only fields
    system: bool = False  # Whether it is a system skill
    handler: str | None = None  # Handler module name
    tool_name: str | None = None  # Original tool name (for compatibility)
    category: str | None = None  # Tool category

    # metadata.openakita structured fields
    supported_os: list[str] = field(default_factory=list)
    required_bins: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)

    # Config schema (for Setup Center to auto-generate config forms)
    # Each element: {"key": str, "label": str, "type": "text"|"secret"|"number"|"select"|"bool",
    #                "required": bool, "help": str, "default": Any, "options": list, "min": num, "max": num}
    config: list[dict] = field(default_factory=list)

    # --- F1: 9 new frontmatter fields added ---
    when_to_use: str = ""
    keywords: list[str] = field(default_factory=list)
    arguments: list[dict] = field(default_factory=list)
    argument_hint: str = ""
    execution_context: str = "inline"  # "inline" | "fork"
    agent_profile: str | None = None
    paths: list[str] = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    model: str | None = None
    fallback_for_toolsets: list[str] = field(default_factory=list)

    # Internationalization (injected from agents/openai.yaml i18n field, compatible with legacy .openakita-i18n.json)
    # key is language code (e.g. "zh"), value is display name/description in that language
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)

    def get_display_name(self, lang: str = "zh") -> str:
        """Return display name by language, fall back to name if not found"""
        return self.name_i18n.get(lang, self.name)

    def get_display_description(self, lang: str = "zh") -> str:
        """Return display description by language, fall back to description if not found"""
        return self.description_i18n.get(lang, self.description)

    def __post_init__(self):
        """Validate fields"""
        self._validate_name()
        self._validate_description()

    def _validate_name(self):
        """Validate name field.

        Supports two formats:
        - Simple name: ``my-skill``
        - Namespaced: ``owner/repo@skill-name``
        """
        if not self.name:
            raise ValueError("name field is required")

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
        """Validate description field"""
        if not self.description:
            raise ValueError("description field is required")

        if len(self.description) > 1024:
            raise ValueError(f"description must be <= 1024 characters, got {len(self.description)}")


@dataclass
class ParsedSkill:
    """
    Parsed skill

    Contains metadata and complete SKILL.md content
    """

    metadata: SkillMetadata
    body: str  # Markdown body
    path: Path  # SKILL.md file path

    # Optional directories
    scripts_dir: Path | None = None
    references_dir: Path | None = None
    assets_dir: Path | None = None

    @property
    def skill_dir(self) -> Path:
        """Skill root directory"""
        return self.path.parent

    _SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".js"}

    def get_scripts(self) -> list[Path]:
        """Get all available scripts (scripts/ directory takes priority, compatible with external skills with scripts in root)"""
        if self.scripts_dir and self.scripts_dir.exists():
            return list(self.scripts_dir.iterdir())
        return [
            f for f in self.skill_dir.iterdir() if f.is_file() and f.suffix in self._SCRIPT_SUFFIXES
        ]

    def get_references(self) -> list[Path]:
        """Get all documents under references/ directory"""
        if self.references_dir and self.references_dir.exists():
            return [f for f in self.references_dir.iterdir() if f.suffix == ".md"]
        return []

    def get_assets(self) -> list[Path]:
        """Get all resources under assets/ directory"""
        if self.assets_dir and self.assets_dir.exists():
            return list(self.assets_dir.iterdir())
        return []


class SkillParser:
    """
    SKILL.md parser

    Parses SKILL.md files compliant with Agent Skills specification
    """

    # YAML frontmatter regex
    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

    # F13: mtime-based parse cache — key: (resolved_path, mtime), value: ParsedSkill
    _parse_cache: dict[tuple[str, float], "ParsedSkill"] = {}

    def parse_file(self, path: Path) -> ParsedSkill:
        """
        Parse SKILL.md file

        Args:
            path: SKILL.md file path

        Returns:
            ParsedSkill object

        Raises:
            ValueError: Parsing failed
            FileNotFoundError: File not found
        """
        if not path.exists():
            raise FileNotFoundError(f"SKILL.md not found: {path}")

        # F13: check mtime-based cache
        resolved = str(path.resolve())
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cache_key = (resolved, mtime)
        cached = self._parse_cache.get(cache_key)
        if cached is not None:
            return cached

        content = path.read_text(encoding="utf-8")
        result = self.parse_content(content, path)

        # Store in cache (limit size to prevent unbounded growth)
        if len(self._parse_cache) > 500:
            self._parse_cache.clear()
        self._parse_cache[cache_key] = result
        return result

    def parse_content(self, content: str, path: Path) -> ParsedSkill:
        """
        Parse SKILL.md content

        Args:
            content: File content
            path: File path (used to locate related directories)

        Returns:
            ParsedSkill object
        """
        # Parse frontmatter
        match = self.FRONTMATTER_PATTERN.match(content)
        if not match:
            raise ValueError(f"Invalid SKILL.md format: missing YAML frontmatter in {path}")

        yaml_content = match.group(1)
        body = match.group(2).strip()

        # Parse YAML
        try:
            data = yaml.safe_load(yaml_content) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML frontmatter in {path}: {e}")

        # Build metadata (body used as fallback for auto-extraction of description)
        metadata = self._build_metadata(data, path, body=body)

        # Validate directory name match (for namespaced format, compare part after @)
        skill_dir = path.parent
        expected_dir = metadata.name.split("@", 1)[-1] if "@" in metadata.name else metadata.name
        if skill_dir.name != expected_dir:
            logger.warning(
                f"Skill directory name '{skill_dir.name}' does not match "
                f"expected '{expected_dir}' (from skill name '{metadata.name}') in {path}"
            )

        # Find optional directories
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

    def _build_metadata(self, data: dict, path: Path, body: str = "") -> SkillMetadata:
        """Build metadata from YAML data."""
        # Required fields
        name = data.get("name")
        description = data.get("description", "")

        if not name:
            raise ValueError(f"Missing required 'name' field in {path}")

        if not description and body:
            first_para = body.split("\n\n")[0].replace("\n", " ").strip()
            description = first_para[:100] + ("..." if len(first_para) > 100 else "")

        if not description:
            raise ValueError(f"Missing required 'description' field in {path}")

        # Process allowed-tools (hyphens to underscores)
        allowed_tools = data.get("allowed-tools", "")
        if isinstance(allowed_tools, str):
            allowed_tools = allowed_tools.split() if allowed_tools else []

        # System skill fields
        system = data.get("system", False)
        handler = data.get("handler")
        tool_name = data.get("tool-name") or data.get("tool_name")  # Support both formats
        category = data.get("category")

        # If system skill but no tool_name specified, generate from name
        if system and not tool_name:
            tool_name = name.replace("-", "_")

        # Config schema
        config_raw = data.get("config", [])
        config: list[dict] = []
        if isinstance(config_raw, list):
            for item in config_raw:
                if isinstance(item, dict) and "key" in item:
                    config.append(
                        {
                            "key": str(item["key"]),
                            "label": str(item.get("label", item["key"])),
                            "type": str(item.get("type", "text")),
                            "required": bool(item.get("required", False)),
                            "help": str(item.get("help", "")),
                            "default": item.get("default"),
                            "options": item.get("options"),
                            "min": item.get("min"),
                            "max": item.get("max"),
                        }
                    )

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

        # F1: Parse new fields
        when_to_use = str(data.get("when-to-use", "") or "")
        keywords_raw = data.get("keywords", [])
        if isinstance(keywords_raw, list):
            keywords = [str(k) for k in keywords_raw]
        elif isinstance(keywords_raw, str):
            keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
        else:
            keywords = []
        arguments_raw = data.get("arguments", [])
        arguments = (
            [a for a in arguments_raw if isinstance(a, dict)]
            if isinstance(arguments_raw, list)
            else []
        )
        argument_hint = str(data.get("argument-hint", "") or "")
        execution_context = str(data.get("execution-context", "inline") or "inline")
        if execution_context not in ("inline", "fork"):
            execution_context = "inline"
        agent_profile = data.get("agent-profile") or None
        paths_raw = data.get("paths", [])
        paths = [str(p) for p in paths_raw] if isinstance(paths_raw, list) else []
        hooks_raw = data.get("hooks", {})
        hooks = hooks_raw if isinstance(hooks_raw, dict) else {}
        model = data.get("model") or None
        fbt_raw = data.get("fallback-for-toolsets", [])
        fallback_for_toolsets = (
            [str(t) for t in fbt_raw] if isinstance(fbt_raw, list) else []
        )

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
            when_to_use=when_to_use,
            keywords=keywords,
            arguments=arguments,
            argument_hint=argument_hint,
            execution_context=execution_context,
            agent_profile=agent_profile if isinstance(agent_profile, str) else None,
            paths=paths,
            hooks=hooks,
            model=model if isinstance(model, str) else None,
            fallback_for_toolsets=fallback_for_toolsets,
        )

    def parse_directory(self, skill_dir: Path) -> ParsedSkill:
        """
        Parse a skill directory

        Args:
            skill_dir: Skill directory path

        Returns:
            ParsedSkill object
        """
        skill_md = skill_dir / "SKILL.md"
        return self.parse_file(skill_md)

    def validate(self, skill: ParsedSkill) -> list[str]:
        """
        Validate a skill

        Returns:
            List of error messages (empty list means validation passed)
        """
        import shutil as _shutil

        errors = []
        meta = skill.metadata

        # Name length (soft recommendation; hard limit is 128 in _validate_name)
        if len(meta.name) > 64:
            logger.warning(
                "Skill name '%s...' exceeds recommended 64 characters (%d)",
                meta.name[:30],
                len(meta.name),
            )

        # Directory name vs expected
        expected_dir = meta.name.split("@", 1)[-1] if "@" in meta.name else meta.name
        if skill.skill_dir and skill.skill_dir.name != expected_dir:
            errors.append(
                f"Directory name '{skill.skill_dir.name}' should match "
                f"expected '{expected_dir}' (from skill name '{meta.name}')"
            )

        # Body length
        body_lines = skill.body.count("\n") + 1
        if body_lines > 500:
            errors.append(
                f"SKILL.md body has {body_lines} lines. "
                f"Recommended: keep under 500 lines for efficient context usage."
            )

        # System skill must have handler and tool_name
        if meta.system and not meta.handler:
            errors.append("System skill must declare 'handler' in frontmatter")
        if meta.system and not meta.tool_name:
            errors.append("System skill must declare 'tool-name' in frontmatter")

        # required_bins availability
        for bin_name in meta.required_bins:
            if not _shutil.which(bin_name):
                errors.append(f"Required binary '{bin_name}' not found in PATH")

        # required_env availability
        import os as _os

        for env_name in meta.required_env:
            if not _os.environ.get(env_name):
                errors.append(f"Required environment variable '{env_name}' not set")

        # Config schema basic validation
        for item in meta.config or []:
            if isinstance(item, dict):
                if "key" not in item:
                    errors.append(f"Config item missing 'key': {item}")
                if "type" in item and item["type"] not in ("string", "number", "boolean", "select"):
                    errors.append(
                        f"Config item '{item.get('key', '?')}' has unknown type: {item['type']}"
                    )

        return errors


# Global parser instance
skill_parser = SkillParser()


def parse_skill(path: Path) -> ParsedSkill:
    """Convenience function: parse a skill"""
    return skill_parser.parse_file(path)


def parse_skill_directory(skill_dir: Path) -> ParsedSkill:
    """Convenience function: parse a skill directory"""
    return skill_parser.parse_directory(skill_dir)
