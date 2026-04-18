"""
Skill registry.

Follows the Agent Skills specification (agentskills.io/specification).
Stores and manages skill metadata, supporting progressive disclosure.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from ..core.capabilities import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityOrigin,
    CapabilityVisibility,
    build_capability_id,
    build_namespace,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .parser import ParsedSkill

logger = logging.getLogger(__name__)

_MARKETPLACE_HOSTS = {"github.com/openakita", "openakita.com", "skill.openakita.com"}

_RESTRICTED_TOOLS_FOR_UNTRUSTED = frozenset(
    {
        "run_shell",
        "run_command",
        "execute_command",
        "write_file",
        "delete_file",
        "run_skill_script",
        "execute_skill",
    }
)


def _infer_trust_level(skill: "ParsedSkill", source_url: str | None) -> str:
    """Infer trust level from skill metadata and origin."""
    if getattr(skill.metadata, "system", False):
        return "builtin"
    if not source_url:
        # Check if the skill is from the builtin directory
        if skill.path:
            from pathlib import Path

            path_str = str(Path(skill.path)).replace("\\", "/").lower()
            if "/builtin/" in path_str or "/site-packages/" in path_str:
                return "builtin"
        return "local"
    url_lower = source_url.lower()
    for host in _MARKETPLACE_HOSTS:
        if host in url_lower:
            return "marketplace"
    return "remote"


def _infer_origin(
    skill: "ParsedSkill",
    source_url: str | None,
    plugin_source: str | None,
) -> CapabilityOrigin:
    if plugin_source:
        return CapabilityOrigin.PLUGIN
    if getattr(skill.metadata, "system", False):
        return CapabilityOrigin.SYSTEM
    trust_level = _infer_trust_level(skill, source_url)
    if trust_level == "marketplace":
        return CapabilityOrigin.MARKETPLACE
    if trust_level == "remote":
        return CapabilityOrigin.REMOTE
    return CapabilityOrigin.PROJECT


@dataclass
class SkillEntry:
    """
    Skill registry entry.

    Stores skill metadata and references.
    Supports progressive disclosure:
    - Level 1: metadata (name, description) — always available
    - Level 2: body (full instructions) — loaded on activation
    - Level 3: scripts/references/assets — loaded on demand

    Extra fields for system skills:
    - system: whether this is a system skill
    - handler: handler module name
    - tool_name: original tool name
    - category: tool category
    """

    skill_id: str  # Unique identifier (= directory name); used as registry key, allowlist key, and tool name key
    name: str  # Display name declared in SKILL.md (may repeat); used only for display and search
    description: str
    version: str | None = None
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False

    # System-skill-specific fields
    system: bool = False
    handler: str | None = None
    tool_name: str | None = None
    category: str | None = None

    # metadata.openakita structured fields
    supported_os: list[str] = field(default_factory=list)
    required_bins: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)

    # Skill path (used for lazy loading)
    skill_path: str | None = None

    # Skill source URL (from .openakita-source file; used to disambiguate skills with the same name)
    source_url: str | None = None

    # Plugin source identifier (e.g. "plugin:translate-skill"); None for non-plugin skills
    plugin_source: str | None = None

    # Unified capability metadata
    origin: str = CapabilityOrigin.PROJECT.value
    namespace: str = CapabilityOrigin.PROJECT.value
    visibility: str = CapabilityVisibility.PUBLIC.value
    permission_profile: str = ""
    capability_id: str = ""

    # Internationalization (injected via the agents/openai.yaml i18n field; backward compatible with legacy .openakita-i18n.json)
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)

    # Skill configuration schema (passed through from SKILL.md frontmatter)
    config: list[dict] = field(default_factory=list)

    # Exposure level for profile-aware filtering
    exposure_level: str = "recommended"  # "core" | "recommended" | "hidden"

    # F1: extended frontmatter fields
    when_to_use: str = ""
    keywords: list[str] = field(default_factory=list)
    arguments: list[dict] = field(default_factory=list)
    argument_hint: str = ""
    execution_context: str = "inline"
    agent_profile: str | None = None
    paths: list[str] = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    model: str | None = None
    fallback_for_toolsets: list[str] = field(default_factory=list)

    # F12: trust level ("builtin" | "local" | "marketplace" | "remote")
    # builtin: built-in skills shipped with the install package
    # local: skills created locally by the user
    # marketplace: skills installed from the official marketplace
    # remote: skills installed from third-party URL/Git sources (untrusted)
    trust_level: str = "local"

    # Global enable / disable flag
    # Skills disabled by the user via the UI / skills.json stay in the registry but are marked disabled=True,
    # so SkillCatalog and the list_skills tool filter them out,
    # while sub-agent INCLUSIVE mode can still reference and re-enable them explicitly via profile.skills.
    disabled: bool = False

    # L1 catalog-hidden flag (progressive disclosure control)
    # Skills not checked in INCLUSIVE mode are marked catalog_hidden=True,
    # so they do not appear in the system-prompt catalog (L1) but remain in the registry,
    # and the LLM can still discover and load them on demand (L2+) via list_skills / get_skill_info.
    catalog_hidden: bool = False

    # Full skill object reference (lazy loading)
    _parsed_skill: Optional["ParsedSkill"] = field(default=None, repr=False)

    def get_display_name(self, lang: str = "zh") -> str:
        """Return the display name for the given language, falling back to name."""
        return self.name_i18n.get(lang, self.name)

    def get_display_description(self, lang: str = "zh") -> str:
        """Return the display description for the given language, falling back to description."""
        return self.description_i18n.get(lang, self.description)

    @property
    def skill_dir(self) -> "Path":
        """Return the skill's directory path."""
        from pathlib import Path

        if self.skill_path:
            p = Path(self.skill_path)
            return p.parent if p.name.upper() == "SKILL.MD" else p
        return Path(".")

    @property
    def is_trusted(self) -> bool:
        """Whether this skill comes from a trusted source."""
        return self.trust_level in ("builtin", "local", "marketplace")

    def get_restricted_tools(self) -> frozenset[str]:
        """Return tools that should be blocked for untrusted skills."""
        if self.is_trusted:
            return frozenset()
        return _RESTRICTED_TOOLS_FOR_UNTRUSTED

    @classmethod
    def from_parsed_skill(
        cls,
        skill: "ParsedSkill",
        skill_id: str | None = None,
        *,
        plugin_source: str | None = None,
    ) -> "SkillEntry":
        """Create an entry from a ParsedSkill.

        Args:
            skill: the parsed skill object.
            skill_id: unique identifier (usually the directory name). Falls back to metadata.name when not provided.
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

        # F12: determine trust level
        trust_level = _infer_trust_level(skill, source_url)
        origin = _infer_origin(skill, source_url, plugin_source)
        effective_skill_id = skill_id or meta.name
        namespace = build_namespace(origin, plugin_id=plugin_source or "")
        permission_profile = (
            "trusted" if trust_level in ("builtin", "local", "marketplace") else "restricted"
        )

        # Infer exposure_level from metadata or trust level
        _exposure = getattr(meta, "exposure_level", "") or ""
        if not _exposure:
            if meta.system or trust_level == "builtin":
                _exposure = "core"
            else:
                _exposure = "recommended"

        return cls(
            exposure_level=_exposure,
            skill_id=effective_skill_id,
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
            config=list(meta.config) if meta.config else [],
            when_to_use=meta.when_to_use,
            keywords=list(meta.keywords),
            arguments=list(meta.arguments),
            argument_hint=meta.argument_hint,
            execution_context=meta.execution_context,
            agent_profile=meta.agent_profile,
            paths=list(meta.paths),
            hooks=dict(meta.hooks) if meta.hooks else {},
            model=meta.model,
            fallback_for_toolsets=list(meta.fallback_for_toolsets),
            trust_level=trust_level,
            skill_path=str(skill.path),
            source_url=source_url,
            plugin_source=plugin_source,
            origin=origin.value,
            namespace=namespace,
            visibility=CapabilityVisibility.PUBLIC.value,
            permission_profile=permission_profile,
            capability_id=build_capability_id(
                CapabilityKind.SKILL,
                effective_skill_id,
                origin=origin,
                plugin_id=plugin_source or "",
            ),
            name_i18n=dict(meta.name_i18n),
            description_i18n=dict(meta.description_i18n),
            _parsed_skill=skill,
        )

    def get_body(self) -> str | None:
        """Return the skill body (Level 2)."""
        if self._parsed_skill:
            return self._parsed_skill.body
        return None

    def to_capability_descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            id=self.capability_id
            or build_capability_id(
                CapabilityKind.SKILL,
                self.skill_id,
                origin=self.origin,
                plugin_id=self.plugin_source or "",
            ),
            kind=CapabilityKind.SKILL,
            origin=CapabilityOrigin(self.origin),
            namespace=self.namespace,
            display_name=self.name,
            description=self.description,
            version=self.version or "",
            visibility=CapabilityVisibility(self.visibility),
            permission_profile=self.permission_profile,
            source_ref=self.source_url or self.skill_path or "",
            i18n={
                "name": dict(self.name_i18n),
                "description": dict(self.description_i18n),
            },
            metadata={
                "system": self.system,
                "tool_name": self.tool_name or "",
                "handler": self.handler or "",
                "trust_level": self.trust_level,
                "plugin_source": self.plugin_source or "",
            },
        )

    def to_tool_schema(self) -> dict:
        """
        Convert to an LLM tool-call schema.

        Used to expose a skill as a tool to the LLM.
        System skills use the original tool_name; external skills use the skill_ prefix.
        """
        if self.system and self.tool_name:
            return {
                "name": self.tool_name,
                "description": self.description,
                "input_schema": self._get_input_schema(),
                "x-capability-origin": self.origin,
            }

        safe = re.sub(r"[^a-zA-Z0-9_]", "_", self.skill_id)
        desc = f"[Skill] {self.description}"
        body = self.get_body() or ""
        input_schema = self._parse_parameters_from_body(body)

        if input_schema is None:
            body_preview = body[:200].strip() if body else ""
            if body_preview:
                desc = f"[Skill] {self.description}\n\n{body_preview}"
            input_schema = {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The action to perform",
                    },
                    "params": {
                        "type": "object",
                        "description": "Action parameters",
                    },
                },
                "required": ["action"],
            }

        return {
            "name": f"skill_{safe}",
            "description": desc,
            "input_schema": input_schema,
            "x-capability-origin": self.origin,
        }

    @staticmethod
    def _parse_parameters_from_body(body: str) -> dict | None:
        """Try to extract structured inputSchema from ## Parameters / ## 参数 section."""
        if not body:
            return None
        param_match = re.search(
            r"^##\s+(?:Parameters|参数)\s*\n(.*?)(?=\n##\s|\Z)",
            body,
            re.MULTILINE | re.DOTALL,
        )
        if not param_match:
            return None

        section = param_match.group(1).strip()
        props: dict = {}
        required: list[str] = []
        for line in section.splitlines():
            m = re.match(
                r"^[-*]\s+`(\w+)`\s*(?:\((\w+)\))?\s*(?:\*\*required\*\*|必填)?\s*[:\-—]\s*(.+)",
                line.strip(),
            )
            if not m:
                continue
            name, ptype, desc = m.group(1), m.group(2) or "string", m.group(3).strip()
            props[name] = {"type": ptype, "description": desc}
            if "required" in line.lower() or "必填" in line:
                required.append(name)

        if not props:
            return None
        schema: dict = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    def _get_input_schema(self) -> dict:
        """
        Return the input_schema for a system skill.

        Parses parameter definitions from the SKILL.md body, or falls back to the default schema.
        """
        # By default, return an empty object schema.
        # Actual parameter definitions should live in the SKILL.md body or in separate metadata.
        return {
            "type": "object",
            "properties": {},
        }


class SkillRegistry:
    """
    Skill registry.

    Manages all registered skills, providing:
    - registration / unregistration
    - search / lookup
    - progressive loading

    Internally keyed by skill_id (directory name). Lookup methods accept
    both skill_id and name (backward compatible: match skill_id first, fall back to name).
    """

    def __init__(self):
        self._skills: dict[str, SkillEntry] = {}  # key = skill_id

    def _resolve(self, key: str) -> SkillEntry | None:
        """Look up by skill_id, falling back to name matching (backward compatible)."""
        entry = self._skills.get(key)
        if entry is not None:
            return entry
        matches = [e for e in self._skills.values() if e.name == key]
        if len(matches) > 1:
            logger.warning(
                "Ambiguous skill name '%s' matches %d entries: %s — refusing fuzzy resolution",
                key,
                len(matches),
                [m.skill_id for m in matches],
            )
            return None
        return matches[0] if matches else None

    def _resolve_id(self, key: str) -> str | None:
        """Resolve a key to its actual skill_id."""
        if key in self._skills:
            return key
        matches = [sid for sid, e in self._skills.items() if e.name == key]
        if len(matches) > 1:
            logger.warning(
                "Ambiguous skill name '%s' matches %d entries: %s — refusing fuzzy resolution",
                key,
                len(matches),
                matches,
            )
            return None
        if matches:
            return matches[0]
        return None

    def register(
        self,
        skill: "ParsedSkill",
        skill_id: str | None = None,
        *,
        plugin_source: str | None = None,
        force: bool = False,
    ) -> bool:
        """
        Register a skill.

        Args:
            skill: the parsed skill object.
            skill_id: unique identifier (usually the directory name). Falls back to metadata.name when not provided.
            plugin_source: plugin source identifier.
            force: allow overwriting an existing entry (reload scenarios only).

        Returns:
            True if registered, False if rejected due to conflict.
        """
        entry = SkillEntry.from_parsed_skill(
            skill,
            skill_id=skill_id,
            plugin_source=plugin_source,
        )

        existing = self._skills.get(entry.skill_id)
        if existing is not None and not force:
            logger.warning(
                "Skill '%s' already registered (origin=%s, plugin=%s). "
                "Rejecting new registration from plugin=%s. "
                "Use force=True or unregister first.",
                entry.skill_id,
                existing.origin,
                existing.plugin_source or "none",
                plugin_source or "none",
            )
            return False

        self._skills[entry.skill_id] = entry
        logger.info(f"Registered skill: {entry.skill_id} (name={entry.name})")
        return True

    def unregister(self, key: str) -> bool:
        """
        Unregister a skill.

        Args:
            key: skill_id or name (backward compatible).

        Returns:
            Whether the operation succeeded.
        """
        sid = self._resolve_id(key)
        if sid is not None:
            del self._skills[sid]
            logger.info(f"Unregistered skill: {sid}")
            return True
        return False

    def get(self, key: str) -> SkillEntry | None:
        """
        Get a skill.

        Args:
            key: skill_id or name (backward compatible).

        Returns:
            SkillEntry or None.
        """
        return self._resolve(key)

    def has(self, key: str) -> bool:
        """Check whether a skill exists (accepts skill_id or name)."""
        return self._resolve(key) is not None

    def set_disabled(self, key: str, disabled: bool = True) -> bool:
        """Set a skill's disabled flag. Accepts skill_id or name."""
        skill = self._resolve(key)
        if skill is not None:
            skill.disabled = disabled
            return True
        return False

    def set_catalog_hidden(self, key: str, hidden: bool = True) -> bool:
        """Set a skill's catalog_hidden flag (L1 progressive disclosure control).

        catalog_hidden skills do not appear in the system-prompt catalog
        but can still be discovered and loaded on demand via list_skills / get_skill_info.
        """
        skill = self._resolve(key)
        if skill is not None:
            skill.catalog_hidden = hidden
            return True
        return False

    def count_catalog_hidden(self) -> int:
        """Count skills that are catalog_hidden but still enabled."""
        return sum(1 for s in self._skills.values() if not s.disabled and s.catalog_hidden)

    def list_all(self, include_disabled: bool = True) -> list[SkillEntry]:
        """List all skills.

        Args:
            include_disabled: whether to include skills disabled by the user. Defaults to True for backward compatibility.
        """
        if include_disabled:
            return list(self._skills.values())
        return [s for s in self._skills.values() if not s.disabled]

    def list_enabled(self) -> list[SkillEntry]:
        """List all enabled skills (excludes disabled=True)."""
        return [s for s in self._skills.values() if not s.disabled]

    def list_metadata(self) -> list[dict]:
        """
        List metadata for enabled skills (Level 1).

        Used at startup to present the available skills to the LLM.
        """
        return [
            {
                "skill_id": skill.skill_id,
                "capability_id": skill.capability_id,
                "namespace": skill.namespace,
                "origin": skill.origin,
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
        Search skills.

        Args:
            query: search term (matches skill_id, name, or description).
            include_disabled: whether to include skills that disable auto-invocation.

        Returns:
            List of matching skills.
        """
        query_lower = query.strip().lower()
        if not query_lower:
            return []

        results = []
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

    _STOP_WORDS = frozenset(
        {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "can",
            "need",
            "dare",
            "ought",
            "used",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "out",
            "off",
            "over",
            "under",
            "again",
            "further",
            "then",
            "once",
            "here",
            "there",
            "when",
            "where",
            "why",
            "how",
            "all",
            "each",
            "every",
            "both",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "nor",
            "not",
            "only",
            "own",
            "same",
            "so",
            "than",
            "too",
            "very",
            "just",
            "about",
            "also",
            "and",
            "but",
            "or",
            "if",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "file",
            "files",
            "tool",
            "tools",
            "use",
            "using",
            "data",
            "work",
            "make",
            "like",
            "new",
            "way",
            "help",
            "get",
            "set",
            "的",
            "了",
            "和",
            "是",
            "在",
            "有",
            "不",
            "与",
            "或",
            "及",
            "对",
            "将",
            "从",
            "到",
            "等",
            "用",
            "为",
            "把",
            "被",
            "让",
            "可以",
            "使用",
            "通过",
            "支持",
            "提供",
            "进行",
            "功能",
            "操作",
        }
    )

    def find_relevant(self, context: str) -> list[SkillEntry]:
        """
        Find skills relevant to the given context.

        Used by the Agent to decide whether to activate a skill.

        Args:
            context: context text (e.g. user input).

        Returns:
            Skills that may be relevant, sorted by relevance (descending).
        """
        if not context or not context.strip():
            return []

        context_lower = context.lower()
        scored: list[tuple[SkillEntry, int]] = []

        for skill in self._skills.values():
            if skill.disabled or skill.disable_model_invocation:
                continue

            score = 0
            sid = skill.skill_id.lower()
            sname = skill.name.lower()

            if sid in context_lower or sname in context_lower:
                score += 10

            for kw in skill.keywords:
                if kw.lower() in context_lower:
                    score += 5

            if skill.when_to_use and any(
                w in context_lower
                for w in skill.when_to_use.lower().split()
                if len(w) > 3 and w not in self._STOP_WORDS
            ):
                score += 3

            desc_words = set(skill.description.lower().split()) - self._STOP_WORDS
            for word in desc_words:
                if len(word) > 3 and word in context_lower:
                    score += 1

            if score > 0:
                scored.append((skill, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored]

    def get_tool_schemas(self) -> list[dict]:
        """
        Return tool schemas for enabled skills.

        Used to expose skills as tools to the LLM (excluding disabled ones).
        """
        return [skill.to_tool_schema() for skill in self._skills.values() if not skill.disabled]

    def list_system_skills(self) -> list[SkillEntry]:
        """List all system skills."""
        return [s for s in self._skills.values() if s.system]

    def list_external_skills(self) -> list[SkillEntry]:
        """List all external (non-system) skills."""
        return [s for s in self._skills.values() if not s.system]

    def get_by_tool_name(self, tool_name: str) -> SkillEntry | None:
        """
        Find a skill by its original tool name.

        Args:
            tool_name: original tool name (e.g. 'browser_navigate').

        Returns:
            SkillEntry or None.
        """
        for skill in self._skills.values():
            if skill.tool_name == tool_name:
                return skill
        return None

    def get_by_handler(self, handler: str) -> list[SkillEntry]:
        """
        Get all skills associated with the given handler.

        Args:
            handler: handler name (e.g. 'browser').

        Returns:
            List of skills.
        """
        return [s for s in self._skills.values() if s.handler == handler]

    @property
    def count(self) -> int:
        """Total skill count."""
        return len(self._skills)

    @property
    def system_count(self) -> int:
        """System skill count."""
        return len(self.list_system_skills())

    @property
    def external_count(self) -> int:
        """External skill count."""
        return len(self.list_external_skills())

    def __contains__(self, key: str) -> bool:
        return self.has(key)

    def __len__(self) -> int:
        return self.count

    def __iter__(self):
        return iter(self._skills.values())

    def __bool__(self) -> bool:
        """Ensure an empty registry is not mistakenly treated as falsy."""
        return True

    def items(self):
        return self._skills.items()

    def pop(self, key: str, default=None):
        return self._skills.pop(key, default)


# Global registry
default_registry = SkillRegistry()


def register_skill(skill: "ParsedSkill", skill_id: str | None = None) -> None:
    """Register a skill with the default registry."""
    default_registry.register(skill, skill_id=skill_id)


def get_skill(key: str) -> SkillEntry | None:
    """Get a skill from the default registry (accepts skill_id or name)."""
    return default_registry.get(key)
