"""
技能分类注册表 (Skill Category Registry)

分类信息持久化到 ``data/skills/skill_categories.json``（通过 CategoryStore），
内存中的 CategoryRegistry 在每次 SkillLoader.load_all 时从 JSON 重建，
同时合并系统技能（system）的 frontmatter 分类。

分类注册表只记录"声明式"的元数据（名称 / 描述 / 是否只读 /
该类下已加载的 skill_id 集合），不直接影响技能启停 ——
"启停大类"的语义是 mass action：把该类下所有外部 skill_id 一次性
加入或剔除 ``data/skills.json`` 的 external_allowlist，由 SkillLoader 与
PromptBuilder 走原有路径处理。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .category_store import CategoryStore

logger = logging.getLogger(__name__)


# ``skills/`` 下的"命名空间"目录名，不视为用户面向的"大类"，
# 仅用于把系统/外部/插件等技能在物理上隔离。这些目录被递归扫描，
# 但不会作为 inferred_category 透传到 SkillEntry。
RESERVED_NAMESPACE_DIRS: frozenset[str] = frozenset(
    {"system", "external", "custom", "community", "builtin"}
)


@dataclass
class CategoryEntry:
    """单个技能大类的注册条目。"""

    name: str
    description: str | None = None
    source_dir: Path | None = None
    system_readonly: bool = False
    skill_ids: set[str] = field(default_factory=set)


class CategoryRegistry:
    """线程安全的技能分类注册表。

    初始化时从 ``CategoryStore`` 读取 JSON 持久化的分类定义；
    loader 扫描期间增量补充系统技能的分类；
    由 ``/api/skill-categories`` 端点读取。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._categories: dict[str, CategoryEntry] = {}
        self._store: CategoryStore | None = None

    def set_store(self, store: CategoryStore) -> None:
        """关联 CategoryStore，后续 load_from_store 可从 JSON 初始化。"""
        self._store = store

    @property
    def store(self) -> CategoryStore | None:
        return self._store

    # ── 从 JSON store 加载 ───────────────────────────────────────────────

    def load_from_store(self) -> None:
        """从 CategoryStore 读取分类定义，填充内存注册表。

        在 SkillLoader.load_all 开始时调用（替代旧的目录推断）。
        每次调用都会强制重新读取 JSON 文件，确保使用最新的分类与绑定。
        """
        if self._store is None:
            return
        try:
            self._store.reload()
        except Exception as e:
            logger.debug("CategoryStore.reload failed: %s", e)
        with self._lock:
            for cat in self._store.list_categories():
                name = cat["name"]
                if name not in self._categories:
                    self._categories[name] = CategoryEntry(
                        name=name,
                        description=cat.get("description"),
                        system_readonly=False,
                    )
                else:
                    desc = cat.get("description")
                    if desc:
                        self._categories[name].description = desc

    def resolve_category(self, skill_id: str) -> str | None:
        """从 JSON bindings 查找技能的分类。"""
        if self._store is None:
            return None
        return self._store.get_binding(skill_id)

    # ── 写入 (loader 调用) ──────────────────────────────────────────────

    def clear(self) -> None:
        with self._lock:
            self._categories.clear()

    def upsert(
        self,
        name: str,
        *,
        description: str | None = None,
        source_dir: Path | None = None,
        system_readonly: bool = False,
    ) -> None:
        """登记或更新一个分类。

        重复调用会保留首次写入的 ``source_dir`` / ``system_readonly``，
        仅当 ``description`` 非空时覆盖描述。
        """
        if not name:
            return
        with self._lock:
            entry = self._categories.get(name)
            if entry is None:
                entry = CategoryEntry(
                    name=name,
                    description=description,
                    source_dir=source_dir,
                    system_readonly=system_readonly,
                )
                self._categories[name] = entry
                return
            if description:
                entry.description = description
            if source_dir is not None and entry.source_dir is None:
                entry.source_dir = source_dir
            if system_readonly:
                entry.system_readonly = True

    def add_skill(self, category: str, skill_id: str) -> None:
        if not category or not skill_id:
            return
        with self._lock:
            entry = self._categories.get(category)
            if entry is None:
                entry = CategoryEntry(name=category)
                self._categories[category] = entry
            entry.skill_ids.add(skill_id)

    # ── 读取 (API / UI / catalog 调用) ──────────────────────────────────

    def get(self, name: str) -> CategoryEntry | None:
        with self._lock:
            return self._categories.get(name)

    def list_all(self) -> list[CategoryEntry]:
        with self._lock:
            return [
                CategoryEntry(
                    name=e.name,
                    description=e.description,
                    source_dir=e.source_dir,
                    system_readonly=e.system_readonly,
                    skill_ids=set(e.skill_ids),
                )
                for e in self._categories.values()
            ]

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._categories.keys())


# ── 工具函数 (loader / API 共享) ────────────────────────────────────────


def is_valid_category_name(name: str) -> bool:
    """校验分类名是否合法。

    放宽限制：允许 Unicode 字符（中文等），仅禁止空名称、纯空白、
    以及与 RESERVED_NAMESPACE_DIRS 冲突的名称。
    """
    if not name or not isinstance(name, str):
        return False
    name = name.strip()
    if not name:
        return False
    if name.lower() in RESERVED_NAMESPACE_DIRS:
        return False
    return True


def read_description_md(desc_file: Path) -> str | None:
    """读取分类目录下 DESCRIPTION.md 的描述字符串。

    优先级：
    1. YAML frontmatter 的 ``description`` 字段（与 hermes 一致）
    2. 去掉 frontmatter 后的 markdown 首段（连续非空行）

    任何异常都返回 None，调用方按"无描述"处理。
    """
    try:
        raw = desc_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.debug("Could not read %s: %s", desc_file, e)
        return None

    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            fm_block = raw[3:end].strip()
            body = raw[end + 4 :].lstrip("\n")
            try:
                import yaml

                meta = yaml.safe_load(fm_block) or {}
                if isinstance(meta, dict):
                    desc = meta.get("description")
                    if desc:
                        return str(desc).strip().strip("'\"")
            except Exception as e:
                logger.debug("Failed to parse %s frontmatter: %s", desc_file, e)

    body = body.strip()
    if not body:
        return None
    para_lines: list[str] = []
    for line in body.splitlines():
        if line.strip():
            para_lines.append(line.strip())
        elif para_lines:
            break
    if not para_lines:
        return None
    text = " ".join(para_lines)
    return text[:512] if len(text) > 512 else text


__all__ = [
    "CategoryEntry",
    "CategoryRegistry",
    "RESERVED_NAMESPACE_DIRS",
    "is_valid_category_name",
    "read_description_md",
]
