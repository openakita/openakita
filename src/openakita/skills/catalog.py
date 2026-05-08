"""
技能目录 (Skill Catalog)

遵循 Agent Skills 规范的渐进式披露:
- Level 1: 技能清单 (name + description) - 在系统提示中提供
- Level 2: 完整指令 (SKILL.md body) - 激活时加载
- Level 3: 资源文件 - 按需加载

技能清单在 Agent 启动时生成，并注入到系统提示中，
让大模型在首次对话时就知道有哪些技能可用。

三级降级预算策略:
- Level A (full): name + description + when_to_use
- Level B (compact): name + when_to_use
- Level C (index): names only
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .registry import SkillRegistry

if TYPE_CHECKING:
    from .categories import CategoryRegistry
    from .usage import SkillUsageTracker

logger = logging.getLogger(__name__)


class SkillCatalog:
    """
    技能目录

    管理技能清单的生成和格式化，用于系统提示注入。
    """

    CATALOG_TEMPLATE = """
## Available Skills

Use `get_skill_info(skill_name)` to load full instructions when needed.
Installed skills may come from builtin, user workspace, or project directories.
Do not infer filesystem paths from the workspace map; `get_skill_info` is authoritative.

{skill_list}
"""

    SKILL_ENTRY_TEMPLATE = "- **{name}**: {description}"
    SKILL_ENTRY_WITH_HINT_TEMPLATE = "- **{name}**: {description} _(Use when: {when_to_use})_"

    @staticmethod
    def _safe_format(template: str, **kwargs: str) -> str:
        """str.format that won't crash on {/} in values."""
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError, IndexError) as e:
            logger.warning(
                "[SkillCatalog] str.format failed (template=%r, keys=%s): %s",
                template[:60],
                list(kwargs.keys()),
                e,
            )
            return template + " " + " | ".join(f"{k}={v}" for k, v in kwargs.items())

    def __init__(
        self,
        registry: SkillRegistry,
        usage_tracker: "SkillUsageTracker | None" = None,
        category_registry: "CategoryRegistry | None" = None,
    ):
        self.registry = registry
        # 注：usage_tracker 仍保留供 list_skills 工具与 UI 排序使用，
        # 但 **不再** 影响系统提示中技能清单的展示顺序，
        # 否则新技能（score=0）总被排到末尾，配合截断会被剔除（参考 hermes 范式）
        self._usage_tracker = usage_tracker
        self._category_registry = category_registry
        self._lock = threading.Lock()
        self._cached_catalog: str | None = None
        self._cached_index: str | None = None
        self._cached_compact: str | None = None
        self._cached_grouped: dict[tuple, str] = {}
        self._snapshot_loaded: bool = False  # 单次进程启动只尝试加载一次

    def _list_model_visible(self, exposure_filter: str | None = None) -> list:
        """Return enabled skills that are also visible to the model.

        排序：先按 ``category`` 字典序、再按 ``name`` 字典序（确定性），
        刻意 **不** 按 usage_tracker 倒排，避免新技能因 score=0 永远排末尾
        从而在 prompt 截断时被剔除（参考 hermes-agent 范式）。

        Args:
            exposure_filter: If provided, only return skills with exposure_level
                in the specified set. Use "core" to get only core skills,
                "core+recommended" to get core and recommended skills.
                None returns all non-hidden skills (backward compatible).
        """
        _allowed_levels = None
        if exposure_filter == "core":
            _allowed_levels = {"core"}
        elif exposure_filter == "core+recommended":
            _allowed_levels = {"core", "recommended"}

        skills = []
        for s in self.registry.list_enabled():
            if s.disable_model_invocation or s.catalog_hidden:
                continue
            if _allowed_levels and getattr(s, "exposure_level", "recommended") not in _allowed_levels:
                continue
            skills.append(s)

        skills.sort(
            key=lambda s: (
                (s.category or "Uncategorized").lower(),
                (s.name or "").lower(),
            )
        )
        return skills

    def generate_catalog(self, *, exposure_filter: str | None = None) -> str:
        """
        生成已启用技能目录。

        这是旧接口，仍被启动预热、技能安装/重载和若干 CLI/debug 路径调用。
        默认必须保持目录式输出，避免这些旁路重新生成 name + description +
        when_to_use 的长清单并进入缓存或提示词。需要按任务展开摘要时使用
        get_grouped_compact_catalog()；需要完整说明时使用 get_skill_info。

        Args:
            exposure_filter: "core" | "core+recommended" | None
                控制按 exposure_level 过滤。CONSUMER_CHAT 场景传 "core"，
                IM_ASSISTANT 传 "core+recommended"，LOCAL_AGENT 传 None。
        """
        catalog = self.get_index_catalog(exposure_filter=exposure_filter)
        if exposure_filter is None:
            self._cached_catalog = catalog
        return catalog

    def get_catalog(self, refresh: bool = False) -> str:
        """
        获取技能清单

        Args:
            refresh: 是否强制刷新
        """
        if refresh or self._cached_catalog is None:
            return self.generate_catalog()
        return self._cached_catalog

    def get_compact_catalog(self) -> str:
        """获取紧凑版技能清单 (仅名称列表)，用于 token 受限场景。"""
        with self._lock:
            skills = self._list_model_visible()
            if not skills:
                result = "No skills installed."
            else:
                names = [s.name for s in skills]
                result = f"Available skills: {', '.join(names)}"
            self._cached_compact = result
            return result

    def get_grouped_compact_catalog(
        self,
        *,
        exposure_filter: str | None = None,
        max_tokens: int = 0,
        priority_categories: tuple[str, ...] | None = None,
    ) -> str:
        """生成 **按分类分组的紧凑技能清单**，用于系统提示注入。

        零丢失 + 自适应压缩：
        - 所有技能名字始终保留（保证 LLM 能发现并调用）
        - 按分类字典序、再按 name 字典序输出（确定性，避免抖动）
        - 分类标题展示 ``DESCRIPTION.md`` 的描述（如果有）

        当 ``max_tokens > 0`` 时启用三级自适应压缩：
        - Level B (默认): ``- **name**: when_to_use`` (描述截至 160 字符)
        - Level B-short: 描述缩短至 80 字符（技能数 > 80 时自动触发）
        - Level C (index): 分类 + 逗号分隔名字，无描述

        当 ``priority_categories`` 非空时启用**分类优先级裁剪**（Fix-4）：
        - 列入优先级集合的分类按 Level B/B-short 输出（保留描述）
        - 其余分类一律降级为 Level C（仅名字），由 LLM 通过
          ``get_skill_info`` 按需展开。这种"主线详细 + 长尾索引"
          的混合模式可在保留全量发现能力的同时显著节省 token。

        缓存：按 ``(exposure_filter, max_tokens, priority_categories)`` 维度缓存。
        """
        with self._lock:
            cache_key = (
                exposure_filter,
                max_tokens,
                tuple(sorted(priority_categories or ())),
            )
            cached = self._cached_grouped.get(cache_key)
            if cached is not None:
                return cached

            # L2：进程首次未命中时尝试从磁盘 snapshot prime L1
            if not self._snapshot_loaded:
                self._snapshot_loaded = True
                try:
                    primed = self._load_disk_snapshot_if_valid()
                    if primed:
                        self._cached_grouped.update(primed)
                        if cache_key in primed:
                            return primed[cache_key]
                except Exception as e:
                    logger.debug("Skill catalog snapshot prime failed: %s", e)

            skills = self._list_model_visible(exposure_filter=exposure_filter)
            hidden_count = self.registry.count_catalog_hidden()

            if not skills:
                if hidden_count > 0:
                    result = (
                        "## Available Skills\n\n"
                        "No skills pre-loaded for this profile. "
                        f"{hidden_count} more skill(s) available — "
                        "use `list_skills` to discover, then `get_skill_info` to load."
                    )
                else:
                    result = (
                        "## Available Skills\n\n"
                        "No skills installed. Use the skill creation workflow to add new skills."
                    )
                self._cached_grouped[cache_key] = result
                return result

            grouped: dict[str, list] = {}
            for s in skills:
                cat = s.category or "Uncategorized"
                grouped.setdefault(cat, []).append(s)

            cat_descriptions: dict[str, str | None] = {}
            if self._category_registry is not None:
                try:
                    for entry in self._category_registry.list_all():
                        cat_descriptions[entry.name] = entry.description
                except Exception:
                    pass

            sorted_cats = sorted(grouped.keys(), key=lambda x: x.lower())

            def _render(when_max: int) -> str:
                """渲染带描述的技能清单 (Level B)"""
                lines: list[str] = [
                    "## Available Skills",
                    "",
                    "Use `get_skill_info(skill_name)` to load full instructions when needed.",
                    "",
                ]
                for cat in sorted_cats:
                    desc = cat_descriptions.get(cat)
                    lines.append(f"### {cat} — {desc}" if desc else f"### {cat}")
                    for s in grouped[cat]:
                        when = (getattr(s, "when_to_use", "") or "").strip()
                        if not when:
                            when = (s.description or "").split("\n")[0].strip()
                        when = when[:when_max]
                        lines.append(self._safe_format(
                            "- **{name}**: {when}",
                            name=s.name,
                            when=when,
                        ))
                    lines.append("")
                if hidden_count > 0:
                    lines.append(
                        f"_({hidden_count} more skill(s) hidden by profile — "
                        "use `list_skills` to enumerate)_"
                    )
                return "\n".join(lines).rstrip() + "\n"

            def _render_index() -> str:
                """渲染仅名字的紧凑清单 (Level C)"""
                lines: list[str] = [
                    "## Available Skills",
                    "",
                    "Use `get_skill_info(skill_name)` to load full instructions before using a skill.",
                    "",
                ]
                for cat in sorted_cats:
                    names = [s.name for s in grouped[cat]]
                    desc = cat_descriptions.get(cat)
                    lines.append(f"### {cat} — {desc}" if desc else f"### {cat}")
                    lines.append(", ".join(names))
                    lines.append("")
                if hidden_count > 0:
                    lines.append(
                        f"_({hidden_count} more skill(s) hidden by profile — "
                        "use `list_skills` to enumerate)_"
                    )
                return "\n".join(lines).rstrip() + "\n"

            def _est_tokens(text: str) -> int:
                return max(len(text) // 4, len(text.encode("utf-8")) // 3)

            def _render_mixed(when_max: int) -> str:
                """混合模式：priority 分类用 Level B；其余用 Level C。"""
                priority_set = {c.lower() for c in (priority_categories or ())}
                lines: list[str] = [
                    "## Available Skills",
                    "",
                    "Use `get_skill_info(skill_name)` to load full instructions when needed.",
                    "",
                ]
                for cat in sorted_cats:
                    desc = cat_descriptions.get(cat)
                    is_priority = cat.lower() in priority_set
                    if is_priority:
                        # Level B 详细
                        lines.append(f"### {cat} — {desc}" if desc else f"### {cat}")
                        for s in grouped[cat]:
                            when = (getattr(s, "when_to_use", "") or "").strip()
                            if not when:
                                when = (s.description or "").split("\n")[0].strip()
                            when = when[:when_max]
                            lines.append(self._safe_format(
                                "- **{name}**: {when}",
                                name=s.name,
                                when=when,
                            ))
                        lines.append("")
                    else:
                        # Level C 仅名字 — 用 (index) 后缀提示
                        names = [s.name for s in grouped[cat]]
                        title = f"### {cat} (index) — {desc}" if desc else f"### {cat} (index)"
                        lines.append(title)
                        lines.append(", ".join(names))
                        lines.append("")
                if hidden_count > 0:
                    lines.append(
                        f"_({hidden_count} more skill(s) hidden by profile — "
                        "use `list_skills` to enumerate)_"
                    )
                return "\n".join(lines).rstrip() + "\n"

            if max_tokens <= 0:
                result = _render_mixed(160) if priority_categories else _render(160)
            else:
                if priority_categories:
                    result = _render_mixed(160)
                    if _est_tokens(result) > max_tokens:
                        result = _render_mixed(80)
                else:
                    result = _render(160)
                    if _est_tokens(result) > max_tokens:
                        result = _render(80)
                if _est_tokens(result) > max_tokens:
                    result = _render_index()

            self._cached_grouped[cache_key] = result
            logger.debug(
                "Generated grouped catalog: %d skills across %d categories "
                "(~%d tokens, max_tokens=%d)",
                len(skills),
                len(grouped),
                _est_tokens(result),
                max_tokens,
            )
            try:
                self._write_disk_snapshot()
            except Exception as e:
                logger.debug("Skill catalog snapshot write failed: %s", e)
            return result

    def get_index_catalog(self, *, exposure_filter: str | None = None) -> str:
        """
        获取已启用技能的"全量索引"（仅名称，尽量短，但完整）。

        Args:
            exposure_filter: "core" | "core+recommended" | None
        """
        with self._lock:
            skills = self._list_model_visible(exposure_filter=exposure_filter)
            hidden_count = self.registry.count_catalog_hidden()
            if not skills:
                if hidden_count > 0:
                    result = (
                        "## Skills Index\n\n"
                        "No skills pre-loaded for this profile. "
                        f"{hidden_count} more skill(s) available via `list_skills`."
                    )
                else:
                    result = "## Skills Index (complete)\n\nNo skills installed."
                if exposure_filter is None:
                    self._cached_index = result
                return result

            system_names: list[str] = []
            external_names: list[str] = []
            plugin_entries: list[str] = []

            for s in skills:
                if getattr(s, "system", False):
                    system_names.append(s.name)
                elif getattr(s, "plugin_source", None):
                    plugin_id = s.plugin_source.replace("plugin:", "")
                    plugin_entries.append(f"{s.name} (via {plugin_id})")
                else:
                    external_names.append(s.name)

            system_names.sort()
            external_names.sort()
            plugin_entries.sort()

            lines: list[str] = [
                "## Skills Index (complete)",
                "",
                "Use `get_skill_info(skill_name)` to load full instructions.",
                "Most external skills are **instruction-only** (no pre-built scripts) "
                "\u2014 read instructions via get_skill_info, then write code and execute via run_shell.",
                "Only use `run_skill_script` when a skill explicitly lists executable scripts.",
            ]

            if system_names:
                lines += ["", f"**System skills ({len(system_names)})**: {', '.join(system_names)}"]
            if external_names:
                lines += [
                    "",
                    f"**External skills ({len(external_names)})**: {', '.join(external_names)}",
                ]
            if plugin_entries:
                lines += [
                    "",
                    f"**Plugin skills ({len(plugin_entries)})**: {', '.join(plugin_entries)}",
                ]

            result = "\n".join(lines)
            if exposure_filter is None:
                self._cached_index = result
            return result

    def generate_catalog_budgeted(self, budget_chars: int = 0) -> str:
        """Generate catalog with three-level degradation if budget_chars is set.

        Level A: full (name + description + when_to_use) via generate_catalog()
        Level B: name + short hint for each skill
        Level C: comma-separated names only

        If budget_chars <= 0, returns full catalog without budget constraint.
        """
        if budget_chars <= 0:
            return self.generate_catalog()

        full = self.generate_catalog()
        if len(full) <= budget_chars:
            return full

        # Level B: name + short hint
        with self._lock:
            skills = self._list_model_visible()
            if not skills:
                return "No skills installed."
            b_lines = ["## Skills (compact)"]
            for s in skills:
                hint = getattr(s, "when_to_use", "") or ""
                if hint:
                    b_lines.append(f"- **{s.name}**: {hint[:60]}")
                else:
                    desc_short = (s.description or "")[:40]
                    b_lines.append(f"- **{s.name}**: {desc_short}")
            level_b = "\n".join(b_lines)
            if len(level_b) <= budget_chars:
                return level_b

            # Level C: names only
            names = [s.name for s in skills]
            return f"Skills ({len(skills)}): {', '.join(names)}"

    def get_skill_summary(self, skill_name: str) -> str | None:
        """获取单个技能的摘要"""
        skill = self.registry.get(skill_name)
        if not skill:
            return None
        return f"**{skill.name}**: {skill.description}"

    def generate_recommendation_hint(
        self,
        task_description: str,
        *,
        max_hints: int = 3,
        max_chars: int = 250,
        exposure_filter: str | None = None,
    ) -> str:
        """根据用户输入生成轻量技能推荐 hint。

        基于 when_to_use 和 keywords 做简单关键词匹配，不调用 LLM。
        返回格式如: "💡 可能有用的技能: web-search (搜索网页), ..."

        Args:
            task_description: 用户的任务描述/输入
            max_hints: 最多推荐几个技能
            max_chars: hint 总长度上限
            exposure_filter: "core" / "core+recommended" / None(all except hidden)
        """
        if not task_description:
            return ""

        _allowed: set[str] | None = None
        if exposure_filter == "core":
            _allowed = {"core"}
        elif exposure_filter == "core+recommended":
            _allowed = {"core", "recommended"}
        query_lower = task_description.lower()
        candidates: list[tuple[float, str, str]] = []

        for s in self.registry.list_enabled():
            if s.disable_model_invocation or s.catalog_hidden:
                continue
            _exp = getattr(s, "exposure_level", "recommended")
            if _allowed and _exp not in _allowed:
                continue

            score = 0.0
            when = getattr(s, "when_to_use", "") or ""
            kws = getattr(s, "keywords", []) or []

            for kw in kws:
                if kw.lower() in query_lower:
                    score += 2.0
            if when:
                when_words = when.lower().split()
                for w in when_words:
                    if len(w) > 2 and w in query_lower:
                        score += 0.5

            if score > 0:
                short_desc = (s.description or "")[:40]
                candidates.append((score, s.name, short_desc))

        if not candidates:
            return ""

        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:max_hints]
        parts = [f"{name} ({desc})" for _, name, desc in top]
        hint = "💡 可能有用的技能: " + ", ".join(parts)

        if len(hint) > max_chars:
            hint = hint[:max_chars - 3] + "..."
        return hint

    def invalidate_cache(self) -> None:
        """使所有缓存失效"""
        with self._lock:
            self._cached_catalog = None
            self._cached_index = None
            self._cached_compact = None
            self._cached_grouped.clear()
        self._invalidate_disk_snapshot()

    @property
    def skill_count(self) -> int:
        """技能数量"""
        return self.registry.count

    # ── 二层缓存：磁盘 snapshot（参考 hermes-agent） ──────────────────

    @staticmethod
    def _snapshot_path() -> Path | None:
        """返回 ``data/.skills_prompt_snapshot.json`` 的绝对路径。"""
        try:
            from ..config import settings

            return Path(settings.project_root) / "data" / ".skills_prompt_snapshot.json"
        except Exception:
            try:
                return Path.cwd() / "data" / ".skills_prompt_snapshot.json"
            except Exception:
                return None

    def _build_manifest(self) -> dict[str, list[int]]:
        """收集所有 SKILL.md / DESCRIPTION.md 的 (mtime_ns, size) 作为 manifest。"""
        manifest: dict[str, list[int]] = {}
        seen_dirs: set[str] = set()
        for entry in self.registry.list_all():
            sp = getattr(entry, "skill_path", None)
            if not sp:
                continue
            try:
                p = Path(sp)
                if p.exists():
                    st = p.stat()
                    manifest[str(p)] = [st.st_mtime_ns, st.st_size]
                # 同级或上溯查找 DESCRIPTION.md（最多向上 4 层）
                cur = p.parent
                for _ in range(4):
                    key = str(cur)
                    if key in seen_dirs:
                        break
                    seen_dirs.add(key)
                    desc = cur / "DESCRIPTION.md"
                    if desc.exists():
                        st = desc.stat()
                        manifest[str(desc)] = [st.st_mtime_ns, st.st_size]
                    if cur.parent == cur:
                        break
                    cur = cur.parent
            except OSError:
                continue
        return manifest

    def _load_disk_snapshot_if_valid(self) -> dict[tuple, str] | None:
        """如果 manifest 与 snapshot 一致，返回缓存字典。

        磁盘快照只存 max_tokens=0 的无预算版本（完整描述），
        预算压缩版在内存中按需从完整版推导，不持久化。
        """
        path = self._snapshot_path()
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        snap_manifest = data.get("manifest")
        snap_catalogs = data.get("catalogs")
        if not isinstance(snap_manifest, dict) or not isinstance(snap_catalogs, dict):
            return None
        current = self._build_manifest()
        if current != snap_manifest:
            return None
        result: dict[tuple, str] = {}
        for k, v in snap_catalogs.items():
            if not isinstance(v, str):
                continue
            exposure: str | None = k if k else None
            result[(exposure, 0)] = v
        return result

    def _write_disk_snapshot(self) -> None:
        """原子地把 L1 缓存写入磁盘 snapshot。"""
        path = self._snapshot_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        manifest = self._build_manifest()
        # 只持久化 max_tokens=0 的无预算版本；预算压缩版在内存中按需推导
        catalogs = {}
        for (exposure, mt), v in self._cached_grouped.items():
            if mt == 0:
                catalogs[exposure if exposure is not None else ""] = v
        payload = {"version": 1, "manifest": manifest, "catalogs": catalogs}

        try:
            tmp_fd, tmp_str = tempfile.mkstemp(
                prefix=".skills_snap.", suffix=".json.tmp", dir=str(path.parent)
            )
            tmp_path = Path(tmp_str)
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_path, path)
        except OSError as e:
            logger.debug("Snapshot write failed: %s", e)
            try:
                if "tmp_path" in locals() and tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _invalidate_disk_snapshot(self) -> None:
        """清空磁盘 snapshot 与 ``_snapshot_loaded`` 标记。"""
        self._snapshot_loaded = False
        path = self._snapshot_path()
        if path is None:
            return
        try:
            if path.exists():
                path.unlink()
        except OSError as e:
            logger.debug("Snapshot delete failed: %s", e)


def generate_skill_catalog(registry: SkillRegistry) -> str:
    """便捷函数：生成技能清单"""
    catalog = SkillCatalog(registry)
    return catalog.generate_catalog()

