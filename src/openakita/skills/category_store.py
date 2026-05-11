"""
技能分类 JSON 持久化层 (Skill Category Store)

使用 ``data/skills/skill_categories.json`` 存储分类定义与技能绑定关系，
替代原有"文件夹 = 分类"的方式。

JSON 格式::

    {
      "categories": [
        {"name": "browser", "description": "网页浏览相关技能"},
        {"name": "code", "description": "编程与代码生成"}
      ],
      "bindings": {
        "browser-open": "browser",
        "agentic-browser": "browser",
        "python-executor": "code"
      }
    }

- **categories**: 用户自定义分类列表（name + description）
- **bindings**: skill_id → category_name 映射
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DATA: dict = {"categories": [], "bindings": {}}


def _default_store_path() -> Path:
    """Return the default path for ``skill_categories.json``."""
    try:
        from openakita.config import settings
        return settings.project_root / "data" / "skills" / "skill_categories.json"
    except Exception:
        return Path.cwd() / "data" / "skills" / "skill_categories.json"


class CategoryStore:
    """线程安全的技能分类 JSON 持久化管理。

    所有写操作自动落盘；读操作从内存缓存返回。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_store_path()
        self._lock = threading.RLock()
        self._data: dict = {"categories": [], "bindings": {}}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    # ── 文件 I/O ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {"categories": [], "bindings": {}}
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            self._data = {
                "categories": data.get("categories") or [],
                "bindings": data.get("bindings") or {},
            }
        except Exception as e:
            logger.warning("Failed to load %s: %s", self._path, e)
            self._data = {"categories": [], "bindings": {}}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as e:
            logger.error("Failed to save %s: %s", self._path, e)

    def reload(self) -> None:
        with self._lock:
            self._load()

    # ── 分类 CRUD ────────────────────────────────────────────────────────

    def list_categories(self) -> list[dict]:
        """返回所有分类 ``[{"name": ..., "description": ...}, ...]``。"""
        with self._lock:
            return [dict(c) for c in self._data["categories"]]

    def get_category(self, name: str) -> dict | None:
        with self._lock:
            for c in self._data["categories"]:
                if c["name"] == name:
                    return dict(c)
            return None

    def create_category(self, name: str, description: str = "") -> bool:
        """创建分类。如果同名已存在返回 False。"""
        with self._lock:
            for c in self._data["categories"]:
                if c["name"] == name:
                    return False
            self._data["categories"].append({
                "name": name,
                "description": description,
            })
            self._save()
            return True

    def update_category(
        self,
        name: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
    ) -> bool:
        """更新分类名称和/或描述。返回是否找到并更新。"""
        with self._lock:
            target = None
            for c in self._data["categories"]:
                if c["name"] == name:
                    target = c
                    break
            if target is None:
                return False

            if new_name is not None and new_name != name:
                for c in self._data["categories"]:
                    if c["name"] == new_name:
                        return False
                # 同步更新 bindings 中引用旧名称的条目
                new_bindings: dict[str, str] = {}
                for sid, cat in self._data["bindings"].items():
                    new_bindings[sid] = new_name if cat == name else cat
                self._data["bindings"] = new_bindings
                target["name"] = new_name

            if description is not None:
                target["description"] = description

            self._save()
            return True

    def delete_category(self, name: str) -> bool:
        """删除分类并清除其下所有 bindings。"""
        with self._lock:
            before = len(self._data["categories"])
            self._data["categories"] = [
                c for c in self._data["categories"] if c["name"] != name
            ]
            if len(self._data["categories"]) == before:
                return False
            self._data["bindings"] = {
                sid: cat
                for sid, cat in self._data["bindings"].items()
                if cat != name
            }
            self._save()
            return True

    def has_category(self, name: str) -> bool:
        with self._lock:
            return any(c["name"] == name for c in self._data["categories"])

    # ── 绑定管理 ─────────────────────────────────────────────────────────

    def get_bindings(self) -> dict[str, str]:
        """返回所有绑定 ``{skill_id: category_name}``。"""
        with self._lock:
            return dict(self._data["bindings"])

    def get_binding(self, skill_id: str) -> str | None:
        with self._lock:
            return self._data["bindings"].get(skill_id)

    def bind_skill(self, skill_id: str, category: str) -> None:
        """绑定技能到分类（覆盖已有绑定）。"""
        with self._lock:
            self._data["bindings"][skill_id] = category
            self._save()

    def unbind_skill(self, skill_id: str) -> bool:
        """解绑技能。返回是否确实存在绑定。"""
        with self._lock:
            if skill_id not in self._data["bindings"]:
                return False
            del self._data["bindings"][skill_id]
            self._save()
            return True

    def skills_in_category(self, category: str) -> list[str]:
        """返回绑定到指定分类的所有 skill_id。"""
        with self._lock:
            return [
                sid for sid, cat in self._data["bindings"].items()
                if cat == category
            ]

    # ── 迁移辅助 ─────────────────────────────────────────────────────────

    def import_from_registry(
        self,
        categories: list[dict],
        bindings: dict[str, str],
    ) -> None:
        """从旧的目录结构一次性导入分类和绑定（用于迁移）。

        只导入不存在的分类和绑定，不覆盖已有数据。
        """
        with self._lock:
            existing_names = {c["name"] for c in self._data["categories"]}
            for cat in categories:
                if cat["name"] not in existing_names:
                    self._data["categories"].append(dict(cat))
                    existing_names.add(cat["name"])

            for sid, cat in bindings.items():
                if sid not in self._data["bindings"]:
                    self._data["bindings"][sid] = cat

            self._save()


__all__ = ["CategoryStore", "_default_store_path"]
