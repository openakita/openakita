"""
AgentProfile 数据模型 + ProfileStore

AgentProfile 是 Agent 的"蓝图"，定义名称、角色、技能列表、自定义提示词等。
ProfileStore 负责持久化和检索 Profile，支持 SYSTEM 预置保护。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from openakita.utils.atomic_io import atomic_json_write

logger = logging.getLogger(__name__)


# ─── 内置分类 ──────────────────────────────────────────────────────────
BUILTIN_CATEGORIES: list[dict[str, Any]] = [
    {"id": "general",      "label": "通用基础", "color": "#4A90D9", "builtin": True},
    {"id": "content",      "label": "内容创作", "color": "#FF6B6B", "builtin": True},
    {"id": "enterprise",   "label": "企业办公", "color": "#27AE60", "builtin": True},
    {"id": "education",    "label": "教育辅助", "color": "#8E44AD", "builtin": True},
    {"id": "productivity", "label": "生活效率", "color": "#E74C3C", "builtin": True},
    {"id": "devops",       "label": "开发运维", "color": "#95A5A6", "builtin": True},
]
_BUILTIN_IDS = frozenset(c["id"] for c in BUILTIN_CATEGORIES)


class AgentType(str, Enum):
    SYSTEM = "system"
    CUSTOM = "custom"
    DYNAMIC = "dynamic"


class SkillsMode(str, Enum):
    INCLUSIVE = "inclusive"  # 仅含 skills 列表中的技能
    EXCLUSIVE = "exclusive"  # 排除 skills 列表中的技能
    ALL = "all"  # 全部技能


# SYSTEM Profile 中不可被用户修改的身份字段（其余均可自定义）
_SYSTEM_IMMUTABLE_FIELDS = frozenset({
    "id", "type", "created_by",
})


@dataclass
class AgentProfile:
    id: str
    name: str
    description: str = ""
    type: AgentType = AgentType.CUSTOM

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

    # 用户自定义标记：系统预设被用户编辑后置 True，升级时不再覆盖
    user_customized: bool = False

    # Hub 来源（从 Agent Store 安装时记录来源信息）
    hub_source: dict[str, Any] | None = None

    # 临时 Agent 支持
    ephemeral: bool = False
    inherit_from: str | None = None

    def __post_init__(self):
        if isinstance(self.type, str):
            self.type = AgentType(self.type)
        if isinstance(self.skills_mode, str):
            self.skills_mode = SkillsMode(self.skills_mode)
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_system(self) -> bool:
        return self.type == AgentType.SYSTEM

    def get_display_name(self, lang: str = "zh") -> str:
        """按语言返回显示名称，找不到则回退到 name"""
        return self.name_i18n.get(lang, self.name)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["skills_mode"] = self.skills_mode.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentProfile:
        data = dict(data)
        if "type" in data:
            data["type"] = AgentType(data["type"])
        if "skills_mode" in data:
            data["skills_mode"] = SkillsMode(data["skills_mode"])
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
    _CUSTOMIZATION_FIELDS = frozenset({
        "name", "description", "icon", "color", "skills", "skills_mode",
        "tools", "tools_mode", "mcp_servers", "mcp_mode", "plugins", "plugins_mode",
        "custom_prompt", "category", "fallback_profile_id", "preferred_endpoint",
    })

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
                        f"SYSTEM profile {profile_id}: "
                        f"ignoring immutable fields: {blocked}"
                    )
                    updates = {
                        k: v for k, v in updates.items()
                        if k not in _SYSTEM_IMMUTABLE_FIELDS
                    }
                # 实质修改时自动标记
                if set(updates.keys()) & self._CUSTOMIZATION_FIELDS:
                    updates["user_customized"] = True

            data = existing.to_dict()
            data.update(updates)
            profile = AgentProfile.from_dict(data)
            self._cache[profile_id] = profile
            self._persist(profile)

        logger.info(f"ProfileStore updated: {profile_id}")
        return profile

    def delete(self, profile_id: str) -> bool:
        """删除 Profile。SYSTEM 类型禁止删除。"""
        with self._lock:
            existing = self._cache.get(profile_id)
            if existing is None:
                return False
            if existing.is_system:
                raise PermissionError(
                    f"Cannot delete SYSTEM profile: {profile_id}"
                )
            del self._cache[profile_id]
            fp = self._profiles_dir / f"{profile_id}.json"
            if fp.exists():
                fp.unlink()
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
                    pid for pid in self._ephemeral
                    if pid.startswith(f"ephemeral_{session_prefix}")
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
        existing: AgentProfile, new: AgentProfile,
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

    def list_categories(self) -> list[dict[str, Any]]:
        """返回所有分类（内置 + 自定义），每项含 agent_count。"""
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
                result.append({
                    **cc,
                    "builtin": False,
                    "agent_count": cat_counts.get(cc["id"], 0),
                })
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
                1 for p in self._cache.values()
                if p.category == cat_id and not p.hidden
            )
            if agent_count > 0:
                raise ValueError(
                    f"分类 '{cat_id}' 下还有 {agent_count} 个 Agent，请先移除或更换分类"
                )
            before = len(self._custom_categories)
            self._custom_categories = [
                c for c in self._custom_categories if c["id"] != cat_id
            ]
            if len(self._custom_categories) == before:
                return False
            self._persist_categories()
        logger.info(f"Removed custom category: {cat_id}")
        return True
