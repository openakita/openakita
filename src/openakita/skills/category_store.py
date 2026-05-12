"""
技能分类 JSON 持久化层 (Skill Category Store)

使用 ``data/skills/skill_categories.json`` 存储分类定义与技能归属关系，
替代原有"文件夹 = 分类"的方式。

JSON 格式::

    {
      "categories": [
        {
          "name": "browser",
          "description": "网页浏览相关技能",
          "skills": ["browser-open", "agentic-browser"]
        },
        {
          "name": "code",
          "description": "编程与代码生成",
          "skills": ["python-executor"]
        }
      ]
    }

- **categories**: 用户自定义分类列表（name + description + skills）
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DATA: dict = {"categories": []}


def _new_default_data() -> dict:
    return {"categories": []}


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
        self._data: dict = _new_default_data()
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    # ── 文件 I/O ─────────────────────────────────────────────────────────

    def _build_bindings_from_categories(self, categories: list[dict]) -> dict[str, str]:
        """从 ``categories[].skills`` 构建 ``skill_id -> category_name`` 映射。"""
        bindings: dict[str, str] = {}
        for cat in categories:
            name = str(cat.get("name") or "").strip()
            if not name:
                continue
            skills = cat.get("skills") or []
            if not isinstance(skills, list):
                continue
            for sid_raw in skills:
                sid = str(sid_raw).strip()
                if sid and sid not in bindings:
                    bindings[sid] = name
        return bindings

    def _normalize_categories(self, categories_raw: object) -> list[dict]:
        """规范化分类结构，保证每项都包含 ``name/description/skills``。"""
        normalized: list[dict] = []
        seen_names: set[str] = set()
        seen_skills: set[str] = set()
        if not isinstance(categories_raw, list):
            return normalized

        for item in categories_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            description = str(item.get("description") or "").strip()

            skills_raw = item.get("skills") or []
            skills: list[str] = []
            if isinstance(skills_raw, list):
                for sid_raw in skills_raw:
                    sid = str(sid_raw).strip()
                    if not sid or sid in seen_skills:
                        continue
                    seen_skills.add(sid)
                    skills.append(sid)

            if name in seen_names:
                # 重名分类按首次出现为准；后续重名条目仅合并未出现过的技能。
                for existing in normalized:
                    if existing["name"] != name:
                        continue
                    for sid in skills:
                        if sid not in existing["skills"]:
                            existing["skills"].append(sid)
                    break
                continue

            normalized.append(
                {
                    "name": name,
                    "description": description,
                    "skills": skills,
                }
            )
            seen_names.add(name)
        return normalized

    def _normalize_loaded_data(self, data: object) -> dict:
        """规范化从磁盘读取的数据，兼容旧 ``bindings`` 结构。"""
        if not isinstance(data, dict):
            return _new_default_data()

        categories = self._normalize_categories(data.get("categories"))

        # 兼容旧结构：若存在顶层 bindings，则合并进 categories[].skills。
        bindings_raw = data.get("bindings")
        if isinstance(bindings_raw, dict):
            by_name: dict[str, dict] = {c["name"]: c for c in categories}
            assigned = set(self._build_bindings_from_categories(categories).keys())

            for sid_raw, cat_raw in bindings_raw.items():
                sid = str(sid_raw).strip()
                cat_name = str(cat_raw).strip()
                if not sid or not cat_name or sid in assigned:
                    continue
                bucket = by_name.get(cat_name)
                if bucket is None:
                    bucket = {"name": cat_name, "description": "", "skills": []}
                    categories.append(bucket)
                    by_name[cat_name] = bucket
                bucket["skills"].append(sid)
                assigned.add(sid)

        return {"categories": categories}

    def _find_category(self, name: str) -> dict | None:
        for c in self._data["categories"]:
            if c["name"] == name:
                return c
        return None

    def _load(self) -> None:
        if not self._path.exists():
            self._data = _new_default_data()
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            self._data = self._normalize_loaded_data(data)
        except Exception as e:
            logger.warning("Failed to load %s: %s", self._path, e)
            self._data = _new_default_data()

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
            return [
                {
                    "name": c["name"],
                    "description": c.get("description", ""),
                }
                for c in self._data["categories"]
            ]

    def get_category(self, name: str) -> dict | None:
        with self._lock:
            c = self._find_category(name)
            if c is not None:
                return {
                    "name": c["name"],
                    "description": c.get("description", ""),
                }
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
                "skills": [],
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
            target = self._find_category(name)
            if target is None:
                return False

            if new_name is not None and new_name != name:
                for c in self._data["categories"]:
                    if c["name"] == new_name:
                        return False
                target["name"] = new_name

            if description is not None:
                target["description"] = description

            self._save()
            return True

    def delete_category(self, name: str) -> bool:
        """删除分类并清除其下所有 skills 归属。"""
        with self._lock:
            before = len(self._data["categories"])
            self._data["categories"] = [
                c for c in self._data["categories"] if c["name"] != name
            ]
            if len(self._data["categories"]) == before:
                return False
            self._save()
            return True

    def has_category(self, name: str) -> bool:
        with self._lock:
            return any(c["name"] == name for c in self._data["categories"])

    def set_category_order(self, order: list[str]) -> bool:
        """按给定名称顺序重排分类；未出现的分类保持原相对顺序追加在末尾。"""
        with self._lock:
            if not isinstance(order, list) or not self._data["categories"]:
                return False
            wanted = [str(x).strip() for x in order if str(x).strip()]
            if not wanted:
                return False

            by_name = {c["name"]: c for c in self._data["categories"]}
            seen: set[str] = set()
            reordered: list[dict] = []

            for name in wanted:
                if name in by_name and name not in seen:
                    reordered.append(by_name[name])
                    seen.add(name)

            for c in self._data["categories"]:
                name = c["name"]
                if name not in seen:
                    reordered.append(c)
                    seen.add(name)

            if [c["name"] for c in reordered] == [c["name"] for c in self._data["categories"]]:
                return False

            self._data["categories"] = reordered
            self._save()
            return True

    # ── 绑定管理 ─────────────────────────────────────────────────────────

    def get_bindings(self) -> dict[str, str]:
        """返回所有绑定 ``{skill_id: category_name}``。"""
        with self._lock:
            return self._build_bindings_from_categories(self._data["categories"])

    def get_binding(self, skill_id: str) -> str | None:
        with self._lock:
            for c in self._data["categories"]:
                skills = c.get("skills") or []
                if skill_id in skills:
                    return c["name"]
            return None

    def bind_skill(self, skill_id: str, category: str) -> None:
        """绑定技能到分类（覆盖已有绑定）。"""
        with self._lock:
            sid = str(skill_id or "").strip()
            cat_name = str(category or "").strip()
            if not sid or not cat_name:
                return

            for c in self._data["categories"]:
                c["skills"] = [s for s in c.get("skills", []) if s != sid]

            target = self._find_category(cat_name)
            if target is None:
                target = {"name": cat_name, "description": "", "skills": []}
                self._data["categories"].append(target)

            if sid not in target["skills"]:
                target["skills"].append(sid)
            self._save()

    def unbind_skill(self, skill_id: str) -> bool:
        """解绑技能。返回是否确实存在绑定。"""
        with self._lock:
            sid = str(skill_id or "").strip()
            if not sid:
                return False
            removed = False
            for c in self._data["categories"]:
                skills = c.get("skills") or []
                new_skills = [s for s in skills if s != sid]
                if len(new_skills) != len(skills):
                    c["skills"] = new_skills
                    removed = True
            if not removed:
                return False
            self._save()
            return True

    def skills_in_category(self, category: str) -> list[str]:
        """返回绑定到指定分类的所有 skill_id。"""
        with self._lock:
            c = self._find_category(category)
            if c is None:
                return []
            return [str(s) for s in c.get("skills") or [] if str(s).strip()]

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
                    self._data["categories"].append(
                        {
                            "name": cat["name"],
                            "description": str(cat.get("description") or "").strip(),
                            "skills": [],
                        }
                    )
                    existing_names.add(cat["name"])

            for sid, cat in bindings.items():
                sid_norm = str(sid or "").strip()
                cat_norm = str(cat or "").strip()
                if not sid_norm or not cat_norm:
                    continue
                if self.get_binding(sid_norm) is not None:
                    continue
                target = self._find_category(cat_norm)
                if target is None:
                    target = {"name": cat_norm, "description": "", "skills": []}
                    self._data["categories"].append(target)
                target["skills"].append(sid_norm)

            self._save()


__all__ = ["CategoryStore", "_default_store_path"]
