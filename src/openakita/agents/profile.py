"""
AgentProfile 数据模型 + ProfileStore

AgentProfile 是 Agent 的"蓝图"，定义名称、角色、技能列表、自定义提示词等。
ProfileStore 负责持久化和检索 Profile，支持 SYSTEM 预置保护。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
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

logger = logging.getLogger(__name__)


# ─── 内置分类 ──────────────────────────────────────────────────────────
BUILTIN_CATEGORIES: list[dict[str, Any]] = [
    {"id": "general", "label": "通用基础", "color": "#4A90D9", "builtin": True},
    {"id": "content", "label": "内容创作", "color": "#FF6B6B", "builtin": True},
    {"id": "enterprise", "label": "企业办公", "color": "#27AE60", "builtin": True},
    {"id": "education", "label": "教育辅助", "color": "#8E44AD", "builtin": True},
    {"id": "productivity", "label": "生活效率", "color": "#E74C3C", "builtin": True},
    {"id": "devops", "label": "开发运维", "color": "#95A5A6", "builtin": True},
]
_BUILTIN_IDS = frozenset(c["id"] for c in BUILTIN_CATEGORIES)


class AgentType(StrEnum):
    SYSTEM = "system"
    CUSTOM = "custom"
    DYNAMIC = "dynamic"


class SkillsMode(StrEnum):
    INCLUSIVE = "inclusive"  # 仅含 skills 列表中的技能
    EXCLUSIVE = "exclusive"  # 排除 skills 列表中的技能
    ALL = "all"  # 全部技能


_SKILLS_MODE_ALIASES: dict[str, str] = {
    "only": "inclusive",
}
_IDENTITY_MODES = frozenset({"shared", "custom"})
_MEMORY_MODES = frozenset({"shared", "isolated"})
_LIST_MODES = frozenset({"all", "inclusive", "exclusive"})


def _normalize_choice(value: Any, *, allowed: frozenset[str], default: str, field_name: str) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in allowed:
            return normalized
    logger.warning("Invalid AgentProfile.%s=%r, falling back to %r", field_name, value, default)
    return default


def _coerce_bool(value: Any, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def safe_agent_type(value: Any) -> AgentType:
    """将任意值安全转换为 AgentType，无法识别时回退到 CUSTOM。"""
    if isinstance(value, AgentType):
        return value
    try:
        return AgentType(value)
    except (ValueError, KeyError, TypeError):
        return AgentType.CUSTOM


def safe_skills_mode(value: Any) -> SkillsMode:
    """将任意值安全转换为 SkillsMode，支持别名映射，无法识别时回退到 ALL。"""
    if isinstance(value, SkillsMode):
        return value
    try:
        raw = _SKILLS_MODE_ALIASES.get(value, value)
        return SkillsMode(raw)
    except (ValueError, KeyError, TypeError):
        return SkillsMode.ALL


# SYSTEM Profile 中不可被用户修改的身份字段（其余均可自定义）
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

    # 技能配置
    skills: list[str] = field(default_factory=list)
    skills_mode: SkillsMode = SkillsMode.ALL

    # 工具控制（类目名或具体工具名，复用 orgs/tool_categories.py 的 TOOL_CATEGORIES）
    tools: list[str] = field(default_factory=list)
    tools_mode: str = "all"  # "all" | "inclusive" | "exclusive"

    # MCP 服务器控制
    mcp_servers: list[str] = field(default_factory=list)
    mcp_mode: str = "all"  # "all" | "inclusive" | "exclusive"

    # 插件控制
    plugins: list[str] = field(default_factory=list)
    plugins_mode: str = "all"  # "all" | "inclusive" | "exclusive"

    # 自定义提示词（追加到系统提示词中）
    custom_prompt: str = ""

    # 显示
    icon: str = "🤖"
    color: str = "#4A90D9"

    # 能力边界
    fallback_profile_id: str | None = None

    # 首选 LLM 端点（为 None 或空字符串时使用全局优先级，不可用时自动回退）
    preferred_endpoint: str | None = None
    # prefer: 优先使用该端点，不可用时自动回退；require: 必须使用该端点，不自动切换。
    endpoint_policy: str = "prefer"

    # 权限规则集 (OpenCode 风格，空列表 = 全部允许)
    # 格式: [{"permission": "edit", "pattern": "*", "action": "deny"}, ...]
    permission_rules: list[dict[str, str]] = field(default_factory=list)

    # 元数据
    created_by: str = "system"
    created_at: str = ""

    # 国际化：{"zh": "小秋", "en": "Akita"}
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)

    # 分类与可见性
    category: str = ""
    hidden: bool = False

    # 像素形象（前端像素办公室/聊天头像渲染用）
    pixel_appearance: dict | None = None

    # 用户自定义标记：系统预设被用户编辑后置 True，升级时不再覆盖
    user_customized: bool = False

    # Hub 来源（从 Agent Store 安装时记录来源信息）
    hub_source: dict[str, Any] | None = None

    # 临时 Agent 支持
    ephemeral: bool = False
    inherit_from: str | None = None

    # 隔离配置
    identity_mode: str = "shared"  # "shared" | "custom"
    # `memory_mode` 保留为 canonical 字段名（dataclass / JSON 持久化都用它），
    # `memory_isolation` 是 Phase 2b.2 引入的同名只读+只写属性别名 —— 命名更直观，
    # 推荐新代码使用 `profile.memory_isolation`，旧代码 `profile.memory_mode` 仍然可用。
    # 计划在 v1.30 把 `memory_mode` 标记为 @deprecated，更晚版本下线。
    memory_mode: str = "shared"  # "shared" | "isolated"
    memory_inherit_global: bool = True
    user_profile_content: str = ""

    # Python runtime isolation. "shared" preserves the historical agent-venv
    # behavior; "agent" gives this AgentProfile a managed venv; "custom" is
    # reserved for explicit external interpreters or future remote runtimes.
    runtime_env_mode: str = "shared"  # "shared" | "agent" | "custom"
    runtime_env_dependencies: list[str] = field(default_factory=list)
    runtime_env_python: str | None = None

    # Execution constraints (inspired by Claude Code's BaseAgentDefinition)
    max_turns: int | None = None  # Max reasoning iterations per delegation
    background: bool = False  # Force background execution
    omit_system_context: bool = False  # Skip full system prompt for sub-agents (saves tokens)
    timeout_seconds: int | None = None  # Per-profile timeout override

    def __post_init__(self):
        self.type = safe_agent_type(self.type)
        self.skills_mode = safe_skills_mode(self.skills_mode)
        self.tools_mode = _normalize_choice(
            self.tools_mode,
            allowed=_LIST_MODES,
            default="all",
            field_name="tools_mode",
        )
        self.mcp_mode = _normalize_choice(
            self.mcp_mode,
            allowed=_LIST_MODES,
            default="all",
            field_name="mcp_mode",
        )
        self.plugins_mode = _normalize_choice(
            self.plugins_mode,
            allowed=_LIST_MODES,
            default="all",
            field_name="plugins_mode",
        )
        if self.endpoint_policy not in {"prefer", "require"}:
            self.endpoint_policy = "prefer"
        self.identity_mode = _normalize_choice(
            self.identity_mode,
            allowed=_IDENTITY_MODES,
            default="shared",
            field_name="identity_mode",
        )
        self.memory_mode = _normalize_choice(
            self.memory_mode,
            allowed=_MEMORY_MODES,
            default="shared",
            field_name="memory_mode",
        )
        self.memory_inherit_global = _coerce_bool(self.memory_inherit_global)
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()
        # Invariant: 默认 name_i18n[zh] 与 name 一致；description 同理。
        # caller 显式给了 zh 译名时尊重 caller（例如 name="Alice" + name_i18n.zh="艾莉丝"），
        # 缺省路径（直接 AgentProfile(name=...) / from_dict() 无 i18n 字段）由这里兜底，
        # 防止 get_display_name("zh") 与 name 在系统提示词、Agents 列表、chat header 之间漂移。
        if self.name and not self.name_i18n.get("zh"):
            self.name_i18n = {**self.name_i18n, "zh": self.name}
        if self.description and not self.description_i18n.get("zh"):
            self.description_i18n = {**self.description_i18n, "zh": self.description}

    @property
    def is_system(self) -> bool:
        return self.type == AgentType.SYSTEM

    def get_display_name(self, lang: str = "zh") -> str:
        """按语言返回显示名称，找不到则回退到 name"""
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
                "tools_mode": self.tools_mode,
                "plugins_mode": self.plugins_mode,
            },
        )

    @property
    def memory_isolation(self) -> str:
        """Phase 2b.2：``memory_mode`` 的语义化别名（推荐新代码使用）。

        值与 ``memory_mode`` 保持完全同步，写 ``memory_isolation`` 实际写入
        ``memory_mode`` 字段。计划在 v1.30 把 ``memory_mode`` 标记为 deprecated。
        """
        return self.memory_mode

    @memory_isolation.setter
    def memory_isolation(self, value: str) -> None:
        self.memory_mode = value

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["skills_mode"] = self.skills_mode.value
        d["origin"] = self.origin.value
        d["namespace"] = self.namespace
        d["definition_id"] = self.definition_id
        # Phase 2b.2：同时输出新别名 `memory_isolation`，让前端和外部消费者可以
        # 直接迁移到新字段；老消费者继续读 `memory_mode` 不受影响。
        d["memory_isolation"] = self.memory_mode
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentProfile:
        data = dict(data)
        # Phase 2b.2：接收 `memory_isolation` 作为新 canonical 名；
        # 老 JSON 里如果只有 `memory_mode` 也直接生效。
        # 同时传两个时，以新名为准（与 to_dict 输出的顺序一致）。
        if "memory_isolation" in data and "memory_mode" not in data:
            data["memory_mode"] = data["memory_isolation"]
        elif "memory_isolation" in data and "memory_mode" in data:
            # 显式两个都给：让 `memory_isolation` 覆盖 `memory_mode`
            data["memory_mode"] = data["memory_isolation"]
        # 丢掉前端可能附带的别名字段，避免传给不识别该 kwarg 的 dataclass __init__。
        data.pop("memory_isolation", None)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def derive(
        self,
        *,
        id: str,
        name: str | None = None,
        description: str | None = None,
        type: AgentType = AgentType.DYNAMIC,
        created_by: str,
        ephemeral: bool = True,
        inherit_from: str | None = None,
        **overrides: Any,
    ) -> AgentProfile:
        """Create a derived profile while preserving runtime and isolation settings.

        When ``name`` (resp. ``description``) is explicitly overridden, mirror it
        into ``name_i18n["zh"]`` (resp. ``description_i18n["zh"]``) so the derived
        profile does not inherit a stale Chinese display name from the parent. The
        rule matches ``ProfileStore.update`` and is bypassed if the caller passes
        an explicit ``name_i18n`` / ``description_i18n`` via ``overrides``.
        """
        data = self.to_dict()
        new_name = name if name is not None else self.name
        new_description = description if description is not None else self.description

        if name is not None and "name_i18n" not in overrides:
            merged = dict(self.name_i18n or {})
            merged["zh"] = new_name
            data["name_i18n"] = merged
        if description is not None and "description_i18n" not in overrides:
            merged = dict(self.description_i18n or {})
            merged["zh"] = new_description
            data["description_i18n"] = merged

        data.update(
            {
                "id": id,
                "name": new_name,
                "description": new_description,
                "type": type,
                "created_by": created_by,
                "created_at": "",
                "ephemeral": ephemeral,
                "inherit_from": inherit_from if inherit_from is not None else self.id,
            }
        )
        data.update(overrides)
        return AgentProfile.from_dict(data)


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
    AgentProfile 持久化存储 + 临时 (ephemeral) 内存存储。

    持久化路径: {base_dir}/profiles/{profile_id}.json
    临时 Profile: 仅存内存 (_ephemeral dict)，不写磁盘，任务结束后自动清理。
    线程安全：使用 RLock 保护所有缓存。
    SYSTEM Profile 保护：禁止删除，id/type/created_by 不可变，其余均可编辑。
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
        """从磁盘加载所有 Profile 到缓存"""
        loaded = 0
        for fp in self._profiles_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                profile = AgentProfile.from_dict(data)
                profile = self._heal_loaded_profile(profile, fp)
                self._cache[profile.id] = profile
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load profile {fp.name}: {e}")
        if loaded:
            logger.info(f"ProfileStore loaded {loaded} profile(s) from {self._profiles_dir}")

    def _heal_loaded_profile(
        self,
        profile: AgentProfile,
        source_path: Path,
    ) -> AgentProfile:
        """Repair a SYSTEM profile whose ``name_i18n.zh`` drifted off ``name``.

        Older releases of ``ProfileStore.update`` did not mirror ``name`` into
        ``name_i18n['zh']``. A user who renamed the default Agent through the
        Agents manager (PUT ``/api/agents/profiles/{id}`` with a single
        ``name`` field) ended up with a JSON file where ``name`` was their new
        choice but ``name_i18n['zh']`` still held the original preset value.
        ``Agent._resolve_agent_voice`` reads ``profile.get_display_name('zh')``
        first, so the LLM kept introducing itself with the stale name even
        after the user had renamed the profile.

        The fix in ``ProfileStore.update`` and ``AgentProfile.__post_init__``
        prevents new divergence, but it does not retroactively heal a file
        that already diverged on disk: ``__post_init__`` only fills
        ``name_i18n['zh']`` when it is missing, deliberately keeping
        legitimate explicit translations (``name='Alice'`` +
        ``name_i18n['zh']='艾莉丝'``) intact.

        This method narrows the heal to SYSTEM profiles only. The shipped
        ``SYSTEM_PRESETS`` always declare ``name == name_i18n['zh']`` (the
        ``en`` slot carries the localized variant), and no UI surface allows
        a user to author a different Chinese display name for a SYSTEM
        profile, so divergence on a SYSTEM profile on disk is unambiguously
        the legacy mirroring bug. CUSTOM / DYNAMIC profiles may legitimately
        carry an independent ``name_i18n['zh']`` (e.g. profiles installed
        from the Agent Hub whose publisher provided a Chinese localization
        distinct from the canonical name); for those we log a warning and
        leave the file alone so we never clobber a publisher's intent.
        """
        try:
            current_zh = (profile.name_i18n or {}).get("zh") or ""
            primary = profile.name or ""
            current_desc_zh = (profile.description_i18n or {}).get("zh") or ""
            primary_desc = profile.description or ""

            name_diverged = bool(primary) and bool(current_zh) and primary != current_zh
            desc_diverged = (
                bool(primary_desc) and bool(current_desc_zh) and primary_desc != current_desc_zh
            )
            if not name_diverged and not desc_diverged:
                return profile

            if not profile.is_system:
                if name_diverged:
                    logger.warning(
                        "Profile %s has name=%r but name_i18n['zh']=%r; the "
                        "UI/Agents list will show one and the LLM self-reference "
                        "will use the other. Update name_i18n explicitly via the "
                        "agents API to align them, or rename the profile.",
                        profile.id,
                        primary,
                        current_zh,
                    )
                return profile

            healed_name_i18n = dict(profile.name_i18n or {})
            healed_desc_i18n = dict(profile.description_i18n or {})
            if name_diverged:
                healed_name_i18n["zh"] = primary
            if desc_diverged:
                healed_desc_i18n["zh"] = primary_desc

            data = profile.to_dict()
            data["name_i18n"] = healed_name_i18n
            data["description_i18n"] = healed_desc_i18n
            healed = AgentProfile.from_dict(data)

            atomic_json_write(source_path, healed.to_dict())
            logger.info(
                "ProfileStore self-healed SYSTEM profile %s on load: "
                "name_i18n['zh'] %r -> %r, description_i18n['zh'] %r -> %r",
                profile.id,
                current_zh if name_diverged else healed_name_i18n.get("zh"),
                healed.name_i18n.get("zh"),
                current_desc_zh if desc_diverged else healed_desc_i18n.get("zh"),
                healed.description_i18n.get("zh"),
            )
            return healed
        except Exception as exc:
            # Self-heal is best-effort. A failure (e.g. disk full, permission
            # denied) must not block the rest of profile loading; the
            # in-memory object remains the pre-heal value so behaviour
            # degrades to "old release" rather than "no profile at all".
            logger.warning(
                "ProfileStore failed to self-heal profile %s at %s: %s",
                profile.id,
                source_path,
                exc,
            )
            return profile

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
        """保存 Profile。ephemeral=True 的只存内存，否则写磁盘。"""
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

    # 仅用于判断"用户是否实质修改了系统 Agent"的字段集（hidden/visibility 不算）
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
            "endpoint_policy",
            "identity_mode",
            "memory_mode",
            "memory_inherit_global",
            "runtime_env_mode",
            "runtime_env_dependencies",
            "runtime_env_python",
        }
    )

    def update(self, profile_id: str, updates: dict[str, Any]) -> AgentProfile:
        """
        部分更新 Profile 字段。

        对 SYSTEM Profile，过滤掉身份字段（id/type/created_by）。
        实质修改（非 hidden）时自动标记 user_customized=True。
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
                # 实质修改时自动标记
                if set(updates.keys()) & self._CUSTOMIZATION_FIELDS:
                    updates["user_customized"] = True

            # 当调用方仅改了 name/description 而未显式提供对应的 i18n 字段时，
            # 镜像新值到 `name_i18n['zh']` / `description_i18n['zh']`，避免出现
            # `name="中秋"` 而 `name_i18n.zh="小秋"` 的内部不自洽状态。该状态会
            # 让 system prompt（用 name）和 IM 显示 / 像素办公室 / 日志（用 name_i18n
            # 经 get_display_name 解析）出现"双名"漂移。
            # 调用方若需精确控制多语言版本，可同时显式传 name_i18n/description_i18n
            # 直接覆盖本兜底。仅同步 zh 一键，保留 en 等其它语种的既有值不动。
            if "name" in updates and "name_i18n" not in updates:
                new_name = str(updates.get("name") or "").strip()
                if new_name:
                    merged_name_i18n = dict(existing.name_i18n or {})
                    merged_name_i18n["zh"] = new_name
                    updates["name_i18n"] = merged_name_i18n
            if "description" in updates and "description_i18n" not in updates:
                new_desc = str(updates.get("description") or "").strip()
                if new_desc:
                    merged_desc_i18n = dict(existing.description_i18n or {})
                    merged_desc_i18n["zh"] = new_desc
                    updates["description_i18n"] = merged_desc_i18n

            data = existing.to_dict()
            # Phase 2b.2：data（来自 to_dict）同时含 `memory_mode` 和
            # `memory_isolation`；partial updates 只可能改其中一个。如果只改了
            # 旧名，必须先丢掉旧的 `memory_isolation` alias，否则 from_dict 的
            # "新名优先"规则会反过来覆盖用户的实际修改。反之亦然。
            if "memory_mode" in updates and "memory_isolation" not in updates:
                data.pop("memory_isolation", None)
            elif "memory_isolation" in updates and "memory_mode" not in updates:
                data.pop("memory_mode", None)
            data.update(updates)
            profile = AgentProfile.from_dict(data)
            self._cache[profile_id] = profile
            self._persist(profile)

        logger.info(f"ProfileStore updated: {profile_id}")
        return profile

    _RESERVED_DIR_NAMES = frozenset({"profiles"})

    def get_profile_dir(self, profile_id: str) -> Path:
        """返回 Profile 专属数据目录 data/agents/{profile_id}/

        Raises ValueError if profile_id collides with reserved directory names.
        """
        if profile_id in self._RESERVED_DIR_NAMES:
            raise ValueError(f"Profile ID '{profile_id}' conflicts with a reserved directory name")
        return self._base_dir / profile_id

    def ensure_profile_dir(self, profile_id: str) -> Path:
        """确保 Profile 专属目录存在并初始化必要子目录。"""
        d = self.get_profile_dir(profile_id)
        (d / "identity").mkdir(parents=True, exist_ok=True)
        (d / "memory").mkdir(parents=True, exist_ok=True)
        return d

    def delete(self, profile_id: str) -> bool:
        """删除 Profile。SYSTEM 类型禁止删除。同时清理 Profile 专属目录。"""
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
        """移除单个临时 Profile。"""
        with self._lock:
            removed = self._ephemeral.pop(profile_id, None)
        if removed:
            logger.info(f"ProfileStore removed ephemeral: {profile_id}")
            return True
        return False

    def cleanup_ephemeral(self, session_prefix: str = "") -> int:
        """按 ID 前缀批量清理临时 Profile。无前缀时清理全部。"""
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
        """检查对 SYSTEM Profile 的修改是否合法"""
        for f in _SYSTEM_IMMUTABLE_FIELDS:
            old_val = getattr(existing, f)
            new_val = getattr(new, f)
            if old_val != new_val:
                raise PermissionError(
                    f"Cannot modify immutable field '{f}' on SYSTEM profile "
                    f"'{existing.id}': {old_val!r} -> {new_val!r}"
                )

    # ── 分类管理 ────────────────────────────────────────────────────────

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

    def list_categories(self, profiles: Iterable[AgentProfile] | None = None) -> list[dict[str, Any]]:
        """返回所有分类（内置 + 自定义），每项含 agent_count。"""
        if profiles is None:
            with self._lock:
                all_profiles = list(self._cache.values())
        else:
            all_profiles = list(profiles)

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
        """新增自定义分类。id 不能与已有分类重复。"""
        with self._lock:
            existing_ids = _BUILTIN_IDS | {c["id"] for c in self._custom_categories}
            if cat_id in existing_ids:
                raise ValueError(f"分类 ID 已存在: {cat_id}")
            entry: dict[str, Any] = {"id": cat_id, "label": label, "color": color}
            self._custom_categories.append(entry)
            self._persist_categories()
        logger.info(f"Added custom category: {cat_id} ({label})")
        return {**entry, "builtin": False, "agent_count": 0}

    def remove_category(self, cat_id: str) -> bool:
        """删除自定义分类。内置分类或有 Agent 的分类拒绝删除。"""
        if cat_id in _BUILTIN_IDS:
            raise PermissionError(f"不能删除内置分类: {cat_id}")
        with self._lock:
            agent_count = sum(
                1 for p in self._cache.values() if p.category == cat_id and not p.hidden
            )
            if agent_count > 0:
                raise ValueError(
                    f"分类 '{cat_id}' 下还有 {agent_count} 个 Agent，请先移除或更换分类"
                )
            before = len(self._custom_categories)
            self._custom_categories = [c for c in self._custom_categories if c["id"] != cat_id]
            if len(self._custom_categories) == before:
                return False
            self._persist_categories()
        logger.info(f"Removed custom category: {cat_id}")
        return True
