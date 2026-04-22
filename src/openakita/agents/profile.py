"""
AgentProfile data model + ProfileStore

AgentProfile is the "blueprint" for an Agent, defining name, role, skill list,
custom prompt, etc.  ProfileStore handles persistence and retrieval of profiles,
with SYSTEM preset protection.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..core.capabilities import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityOrigin,
    CapabilityVisibility,
    build_capability_id,
    build_namespace,
)
from ..utils.atomic_io import atomic_json_write
from .cli_detector import CliProviderId

logger = logging.getLogger(__name__)


# --- Built-in categories ----------------------------------------------------------------
BUILTIN_CATEGORIES: list[dict[str, Any]] = [
    {"id": "general", "label": "General", "color": "#4A90D9", "builtin": True},
    {"id": "content", "label": "Content Creation", "color": "#FF6B6B", "builtin": True},
    {"id": "enterprise", "label": "Enterprise Office", "color": "#27AE60", "builtin": True},
    {"id": "education", "label": "Education", "color": "#8E44AD", "builtin": True},
    {"id": "productivity", "label": "Productivity", "color": "#E74C3C", "builtin": True},
    {"id": "devops", "label": "DevOps", "color": "#95A5A6", "builtin": True},
]
_BUILTIN_IDS = frozenset(c["id"] for c in BUILTIN_CATEGORIES)


class AgentType(StrEnum):
    SYSTEM = "system"
    CUSTOM = "custom"
    DYNAMIC = "dynamic"
    EXTERNAL_CLI = "external_cli"


class SkillsMode(StrEnum):
    INCLUSIVE = "inclusive"  # Only skills in the skills list
    EXCLUSIVE = "exclusive"  # Exclude skills in the skills list
    ALL = "all"  # All skills


_SKILLS_MODE_ALIASES: dict[str, str] = {
    "only": "inclusive",
}


def safe_agent_type(value: Any) -> AgentType:
    """Safely convert an arbitrary value to AgentType, falling back to CUSTOM if unrecognized."""
    if isinstance(value, AgentType):
        return value
    try:
        return AgentType(value)
    except (ValueError, KeyError, TypeError):
        return AgentType.CUSTOM


def safe_skills_mode(value: Any) -> SkillsMode:
    """Safely convert an arbitrary value to SkillsMode, supporting alias mapping, falling back to ALL if unrecognized."""
    if isinstance(value, SkillsMode):
        return value
    try:
        raw = _SKILLS_MODE_ALIASES.get(value, value)
        return SkillsMode(raw)
    except (ValueError, KeyError, TypeError):
        return SkillsMode.ALL


class FilterMode(StrEnum):
    ALL = "all"
    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"


class CliPermissionMode(StrEnum):
    PLAN = "plan"
    WRITE = "write"


def safe_filter_mode(value: Any) -> FilterMode:
    if isinstance(value, FilterMode):
        return value
    try:
        return FilterMode(value)
    except (ValueError, KeyError, TypeError):
        return FilterMode.ALL


def safe_cli_permission_mode(value: Any) -> CliPermissionMode:
    if isinstance(value, CliPermissionMode):
        return value
    try:
        return CliPermissionMode(value)
    except (ValueError, KeyError, TypeError):
        return CliPermissionMode.WRITE


# Identity fields in SYSTEM profiles that cannot be modified by users (all others are customizable)
_SYSTEM_IMMUTABLE_FIELDS = frozenset(
    {
        "id",
        "type",
        "created_by",
    }
)


@dataclass
class AgentProfile:
    id: str
    name: str
    description: str = ""
    type: AgentType = AgentType.CUSTOM
    role: str = "worker"  # "worker" | "coordinator"

    # Skills configuration
    skills: list[str] = field(default_factory=list)
    skills_mode: SkillsMode = SkillsMode.ALL

    # Tool control (category names or specific tool names; reuses TOOL_CATEGORIES from orgs/tool_categories.py)
    tools: list[str] = field(default_factory=list)
    tools_mode: FilterMode = FilterMode.ALL

    # MCP server control
    mcp_servers: list[str] = field(default_factory=list)
    mcp_mode: FilterMode = FilterMode.ALL

    # Plugin control
    plugins: list[str] = field(default_factory=list)
    plugins_mode: FilterMode = FilterMode.ALL

    # Custom prompt (appended to the system prompt)
    custom_prompt: str = ""

    # Display
    icon: str = "🤖"
    color: str = "#4A90D9"

    # Capability boundary
    fallback_profile_id: str | None = None

    # Preferred LLM endpoint (uses global priority when None or empty string; auto-fallback on unavailability)
    preferred_endpoint: str | None = None

    # Permission rule set (OpenCode style; empty list = allow all)
    # Format: [{"permission": "edit", "pattern": "*", "action": "deny"}, ...]
    permission_rules: list[dict[str, str]] = field(default_factory=list)

    # External CLI agent fields — populated only when type == EXTERNAL_CLI.
    cli_provider_id: CliProviderId | None = None
    cli_permission_mode: CliPermissionMode = CliPermissionMode.WRITE

    # Metadata
    created_by: str = "system"
    created_at: str = ""

    # Internationalization: {"zh": "小秋", "en": "Akita"}
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)

    # Category and visibility
    category: str = ""
    hidden: bool = False

    # Pixel avatar (used by frontend pixel-office / chat avatar rendering)
    pixel_appearance: dict | None = None

    # User customization flag: set True when a system preset is edited by the user; prevents overwrite on upgrade
    user_customized: bool = False

    # Hub source (records provenance when installed from Agent Store)
    hub_source: dict[str, Any] | None = None

    # Ephemeral agent support
    ephemeral: bool = False
    inherit_from: str | None = None

    # Isolation configuration
    identity_mode: str = "shared"  # "shared" | "custom"
    memory_mode: str = "shared"  # "shared" | "isolated"
    memory_inherit_global: bool = True
    user_profile_content: str = ""

    # Execution constraints (inspired by Claude Code's BaseAgentDefinition)
    max_turns: int | None = None  # Max reasoning iterations per delegation
    background: bool = False  # Force background execution
    omit_system_context: bool = False  # Skip full system prompt for sub-agents (saves tokens)
    timeout_seconds: int | None = None  # Per-profile timeout override

    def __post_init__(self):
        self.type = safe_agent_type(self.type)
        self.skills_mode = safe_skills_mode(self.skills_mode)
        self.tools_mode = safe_filter_mode(self.tools_mode)
        self.mcp_mode = safe_filter_mode(self.mcp_mode)
        self.plugins_mode = safe_filter_mode(self.plugins_mode)
        self.cli_permission_mode = safe_cli_permission_mode(self.cli_permission_mode)
        if self.cli_provider_id is not None and not isinstance(self.cli_provider_id, CliProviderId):
            try:
                self.cli_provider_id = CliProviderId(self.cli_provider_id)
            except (ValueError, KeyError, TypeError):
                self.cli_provider_id = None
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    @property
    def is_system(self) -> bool:
        return self.type == AgentType.SYSTEM

    def get_display_name(self, lang: str = "zh") -> str:
        """Return display name for the given language, falling back to *name* if not found."""
        return self.name_i18n.get(lang, self.name)

    @property
    def origin(self) -> CapabilityOrigin:
        if self.is_system:
            return CapabilityOrigin.SYSTEM
        if self.ephemeral:
            return CapabilityOrigin.RUNTIME
        return CapabilityOrigin.USER

    @property
    def namespace(self) -> str:
        return build_namespace(self.origin)

    @property
    def definition_id(self) -> str:
        return build_capability_id(
            CapabilityKind.AGENT_DEFINITION,
            self.id,
            origin=self.origin,
        )

    def to_capability_descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            id=self.definition_id,
            kind=CapabilityKind.AGENT_DEFINITION,
            origin=self.origin,
            namespace=self.namespace,
            display_name=self.name,
            description=self.description,
            version="1",
            visibility=CapabilityVisibility.HIDDEN if self.hidden else CapabilityVisibility.PUBLIC,
            permission_profile=self.role,
            i18n={
                "name": dict(self.name_i18n),
                "description": dict(self.description_i18n),
            },
            metadata={
                "profile_id": self.id,
                "role": self.role,
                "ephemeral": self.ephemeral,
                "skills_mode": self.skills_mode.value,
                "tools_mode": self.tools_mode.value,
                "plugins_mode": self.plugins_mode.value,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["skills_mode"] = self.skills_mode.value
        d["tools_mode"] = self.tools_mode.value
        d["mcp_mode"] = self.mcp_mode.value
        d["plugins_mode"] = self.plugins_mode.value
        d["cli_permission_mode"] = self.cli_permission_mode.value
        d["cli_provider_id"] = self.cli_provider_id.value if self.cli_provider_id else None
        d["origin"] = self.origin.value
        d["namespace"] = self.namespace
        d["definition_id"] = self.definition_id
        return d

    @classmethod
    def derive_ephemeral_from(
        cls,
        base: AgentProfile,
        *,
        id: str,
        **overrides: Any,
    ) -> AgentProfile:
        """Clone *base* into a fresh ephemeral profile with deep-copied mutable fields.

        Used by `tools/handlers/agent.py::_spawn` so it stops hand-assembling
        partial `AgentProfile(...)` objects. All profile-owned fields — including
        the CLI fields added in this plan — are carried over through one helper,
        with *overrides* replacing specific fields after the copy.

        Enforces `ephemeral=True` and `inherit_from=base.id` regardless of overrides.
        """
        from copy import deepcopy

        data = base.to_dict()
        data.pop("origin", None)
        data.pop("namespace", None)
        data.pop("definition_id", None)
        data.update({
            "id": id,
            "ephemeral": True,
            "inherit_from": base.id,
        })
        for k, v in overrides.items():
            data[k] = v
        # Deep-copy list/dict fields so the ephemeral can mutate without touching base
        for k, v in list(data.items()):
            if isinstance(v, list | dict):
                data[k] = deepcopy(v)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentProfile:
        data = dict(data)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


_global_store: ProfileStore | None = None
_global_store_lock = threading.Lock()


def get_profile_store(base_dir: str | Path | None = None) -> ProfileStore:
    """Return a shared ProfileStore singleton.

    On first call the store is created (reading all profiles from disk);
    subsequent calls return the cached instance.  Pass *base_dir* only on the
    first call (e.g. from startup code); omit it to let the function resolve
    ``settings.data_dir / "agents"`` automatically.
    """
    global _global_store
    if _global_store is not None:
        return _global_store
    with _global_store_lock:
        if _global_store is not None:
            return _global_store
        if base_dir is None:
            from openakita.config import settings

            base_dir = settings.data_dir / "agents"
        _global_store = ProfileStore(base_dir)
        return _global_store


class ProfileStore:
    """
    AgentProfile persistent store + ephemeral in-memory store.

    Persistence path: {base_dir}/profiles/{profile_id}.json
    Ephemeral profiles: memory-only (_ephemeral dict), not written to disk,
        automatically cleaned up when the task ends.
    Thread safety: uses RLock to protect all caches.
    SYSTEM profile protection: deletion is prohibited; id/type/created_by are
        immutable; everything else is editable.
    """

    def __init__(self, base_dir: str | Path):
        self._base_dir = Path(base_dir)
        self._profiles_dir = self._base_dir / "profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._categories_file = self._base_dir / "categories.json"
        self._cache: dict[str, AgentProfile] = {}
        self._ephemeral: dict[str, AgentProfile] = {}
        self._custom_categories: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._load_all()
        self._load_categories()

    def _load_all(self) -> None:
        """Load all profiles from disk into cache."""
        loaded = 0
        for fp in self._profiles_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                profile = AgentProfile.from_dict(data)
                self._cache[profile.id] = profile
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load profile {fp.name}: {e}")
        if loaded:
            logger.info(f"ProfileStore loaded {loaded} profile(s) from {self._profiles_dir}")

    def get(self, profile_id: str) -> AgentProfile | None:
        with self._lock:
            return self._ephemeral.get(profile_id) or self._cache.get(profile_id)

    def list_all(
        self,
        include_ephemeral: bool = False,
        include_hidden: bool = True,
    ) -> list[AgentProfile]:
        with self._lock:
            result = list(self._cache.values())
            if include_ephemeral:
                result.extend(self._ephemeral.values())
            if not include_hidden:
                result = [p for p in result if not p.hidden]
            return result

    def save(self, profile: AgentProfile) -> None:
        """Save a profile. Ephemeral profiles (ephemeral=True) are memory-only; others are written to disk."""
        with self._lock:
            if profile.ephemeral:
                self._ephemeral[profile.id] = profile
                logger.info(
                    f"ProfileStore saved ephemeral: {profile.id} "
                    f"(inherit_from={profile.inherit_from})"
                )
                return

            existing = self._cache.get(profile.id)
            if existing and existing.is_system:
                self._validate_system_update(existing, profile)
            self._cache[profile.id] = profile
            self._persist(profile)
        logger.info(f"ProfileStore saved: {profile.id} ({profile.type.value})")

    # Fields used to determine whether the user made substantive changes to a system agent (hidden/visibility excluded)
    _CUSTOMIZATION_FIELDS = frozenset(
        {
            "name",
            "description",
            "icon",
            "color",
            "skills",
            "skills_mode",
            "tools",
            "tools_mode",
            "mcp_servers",
            "mcp_mode",
            "plugins",
            "plugins_mode",
            "custom_prompt",
            "category",
            "fallback_profile_id",
            "preferred_endpoint",
            "identity_mode",
            "memory_mode",
            "memory_inherit_global",
        }
    )

    def update(self, profile_id: str, updates: dict[str, Any]) -> AgentProfile:
        """
        Partially update profile fields.

        For SYSTEM profiles, identity fields (id/type/created_by) are filtered out.
        Substantive edits (non-hidden) automatically set user_customized=True.
        """
        with self._lock:
            existing = self._cache.get(profile_id)
            if existing is None:
                raise KeyError(f"Profile not found: {profile_id}")

            if existing.is_system:
                blocked = set(updates.keys()) & _SYSTEM_IMMUTABLE_FIELDS
                if blocked:
                    logger.warning(
                        f"SYSTEM profile {profile_id}: ignoring immutable fields: {blocked}"
                    )
                    updates = {
                        k: v for k, v in updates.items() if k not in _SYSTEM_IMMUTABLE_FIELDS
                    }
                # Auto-flag substantive edits
                if set(updates.keys()) & self._CUSTOMIZATION_FIELDS:
                    updates["user_customized"] = True

            data = existing.to_dict()
            data.update(updates)
            profile = AgentProfile.from_dict(data)
            self._cache[profile_id] = profile
            self._persist(profile)

        logger.info(f"ProfileStore updated: {profile_id}")
        return profile

    _RESERVED_DIR_NAMES = frozenset({"profiles"})

    def get_profile_dir(self, profile_id: str) -> Path:
        """Return the profile-specific data directory data/agents/{profile_id}/

        Raises ValueError if profile_id collides with reserved directory names.
        """
        if profile_id in self._RESERVED_DIR_NAMES:
            raise ValueError(f"Profile ID '{profile_id}' conflicts with a reserved directory name")
        return self._base_dir / profile_id

    def ensure_profile_dir(self, profile_id: str) -> Path:
        """Ensure the profile-specific directory exists and initialize required subdirectories."""
        d = self.get_profile_dir(profile_id)
        (d / "identity").mkdir(parents=True, exist_ok=True)
        (d / "memory").mkdir(parents=True, exist_ok=True)
        return d

    def delete(self, profile_id: str) -> bool:
        """Delete a profile. SYSTEM profiles cannot be deleted. Also cleans up the profile-specific directory."""
        with self._lock:
            existing = self._cache.get(profile_id)
            if existing is None:
                return False
            if existing.is_system:
                raise PermissionError(f"Cannot delete SYSTEM profile: {profile_id}")
            del self._cache[profile_id]
            fp = self._profiles_dir / f"{profile_id}.json"
            if fp.exists():
                fp.unlink()

        import shutil

        profile_dir = self.get_profile_dir(profile_id)
        if profile_dir.is_dir():
            shutil.rmtree(profile_dir, ignore_errors=True)
            logger.info(f"ProfileStore cleaned profile dir: {profile_dir}")

        logger.info(f"ProfileStore deleted: {profile_id}")
        return True

    def exists(self, profile_id: str) -> bool:
        with self._lock:
            return profile_id in self._cache or profile_id in self._ephemeral

    def count(self, include_ephemeral: bool = False) -> int:
        with self._lock:
            n = len(self._cache)
            if include_ephemeral:
                n += len(self._ephemeral)
            return n

    def remove_ephemeral(self, profile_id: str) -> bool:
        """Remove a single ephemeral profile."""
        with self._lock:
            removed = self._ephemeral.pop(profile_id, None)
        if removed:
            logger.info(f"ProfileStore removed ephemeral: {profile_id}")
            return True
        return False

    def cleanup_ephemeral(self, session_prefix: str = "") -> int:
        """Batch-remove ephemeral profiles by ID prefix. Removes all if no prefix is given."""
        with self._lock:
            if not session_prefix:
                count = len(self._ephemeral)
                self._ephemeral.clear()
            else:
                to_remove = [
                    pid for pid in self._ephemeral if pid.startswith(f"ephemeral_{session_prefix}")
                ]
                count = len(to_remove)
                for pid in to_remove:
                    del self._ephemeral[pid]
        if count:
            logger.info(
                f"ProfileStore cleaned up {count} ephemeral profile(s)"
                + (f" (prefix={session_prefix!r})" if session_prefix else "")
            )
        return count

    def _persist(self, profile: AgentProfile) -> None:
        fp = self._profiles_dir / f"{profile.id}.json"
        atomic_json_write(fp, profile.to_dict())

    @staticmethod
    def _validate_system_update(
        existing: AgentProfile,
        new: AgentProfile,
    ) -> None:
        """Validate that modifications to a SYSTEM profile are allowed."""
        for f in _SYSTEM_IMMUTABLE_FIELDS:
            old_val = getattr(existing, f)
            new_val = getattr(new, f)
            if old_val != new_val:
                raise PermissionError(
                    f"Cannot modify immutable field '{f}' on SYSTEM profile "
                    f"'{existing.id}': {old_val!r} -> {new_val!r}"
                )

    # --- Category management ---------------------------------------------------------------

    def _load_categories(self) -> None:
        if not self._categories_file.exists():
            return
        try:
            data = json.loads(self._categories_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._custom_categories = data
                logger.info(f"Loaded {len(data)} custom category(ies)")
        except Exception as e:
            logger.warning(f"Failed to load categories: {e}")

    def _persist_categories(self) -> None:
        atomic_json_write(self._categories_file, self._custom_categories)

    def list_categories(self) -> list[dict[str, Any]]:
        """Return all categories (built-in + custom), each with an agent_count."""
        with self._lock:
            all_profiles = list(self._cache.values())

        cat_counts: dict[str, int] = {}
        for p in all_profiles:
            if p.category and not p.hidden:
                cat_counts[p.category] = cat_counts.get(p.category, 0) + 1

        result: list[dict[str, Any]] = []
        for bc in BUILTIN_CATEGORIES:
            result.append({**bc, "agent_count": cat_counts.get(bc["id"], 0)})
        with self._lock:
            for cc in self._custom_categories:
                result.append(
                    {
                        **cc,
                        "builtin": False,
                        "agent_count": cat_counts.get(cc["id"], 0),
                    }
                )
        return result

    def add_category(self, cat_id: str, label: str, color: str) -> dict[str, Any]:
        """Add a custom category. The id must not duplicate an existing category."""
        with self._lock:
            existing_ids = _BUILTIN_IDS | {c["id"] for c in self._custom_categories}
            if cat_id in existing_ids:
                raise ValueError(f"Category ID already exists: {cat_id}")
            entry: dict[str, Any] = {"id": cat_id, "label": label, "color": color}
            self._custom_categories.append(entry)
            self._persist_categories()
        logger.info(f"Added custom category: {cat_id} ({label})")
        return {**entry, "builtin": False, "agent_count": 0}

    def remove_category(self, cat_id: str) -> bool:
        """Delete a custom category. Built-in categories or those with agents are refused."""
        if cat_id in _BUILTIN_IDS:
            raise PermissionError(f"Cannot delete built-in category: {cat_id}")
        with self._lock:
            agent_count = sum(
                1 for p in self._cache.values() if p.category == cat_id and not p.hidden
            )
            if agent_count > 0:
                raise ValueError(
                    f"Category '{cat_id}' still has {agent_count} agent(s); please remove or reassign them first"
                )
            before = len(self._custom_categories)
            self._custom_categories = [c for c in self._custom_categories if c["id"] != cat_id]
            if len(self._custom_categories) == before:
                return False
            self._persist_categories()
        logger.info(f"Removed custom category: {cat_id}")
        return True
