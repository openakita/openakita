"""
Skills route: GET /api/skills, POST /api/skills/config, GET /api/skills/marketplace

技能列表与配置管理。

本模块只负责 HTTP 适配 + 自身的列表缓存；所有会影响技能可见性 / 内容的操作
（install / uninstall / reload / content-update / allowlist-change）在完成磁盘
副作用后统一调用 ``Agent.propagate_skill_change``，由其负责：
  - 清空 parser/loader 缓存
  - 重扫技能目录
  - 重新应用 allowlist
  - 重建 SkillCatalog 与 ``_skill_catalog_text``
  - 同步 handler 映射
  - 通知 AgentInstancePool 回收旧实例
  - 广播 ``SkillEvent``（HTTP 缓存失效 + WebSocket 广播通过事件回调完成）

API 层不再自行做半套刷新，避免多路径导致状态不一致。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)


router = APIRouter()

SKILLS_SH_API = "https://skills.sh/api/search"


_skills_cache: dict | None = None
"""Module-level cache for GET /api/skills response.
Populated on first request, invalidated via the cross-layer on-change callback
registered at the bottom of this module."""

_organize_cache: dict | None = None
_organize_cache_hash: str | None = None


def _invalidate_skills_cache() -> None:
    """Clear the cached skill list so the next GET /api/skills re-scans disk."""
    global _skills_cache, _organize_cache, _organize_cache_hash
    _skills_cache = None
    _organize_cache = None
    _organize_cache_hash = None


def _resolve_agent(request: Request):
    """返回真实 Agent 实例（解包可能的 thin wrapper / _local_agent）。"""
    from openakita.core.agent import Agent

    agent = getattr(request.app.state, "agent", None)
    if isinstance(agent, Agent):
        return agent
    return getattr(agent, "_local_agent", None)


def _extract_json_object(text: str) -> dict:
    """Extract a JSON object from plain or fenced LLM output."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty response")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S | re.I)
    if fenced:
        raw = fenced.group(1)
    elif not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found")
        raw = raw[start : end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def _get_external_skills_from_cache() -> list[dict] | None:
    """从 _skills_cache 中提取外部技能列表（不触发磁盘扫描）。"""
    if _skills_cache is None:
        return None
    skills = _skills_cache.get("skills")
    if not skills:
        return None
    return [s for s in skills if not s.get("system", False)]


async def _load_external_skills_for_organize() -> tuple[list, set[str] | None]:
    """Load all external skills from disk and return effective allowlist."""
    from openakita.skills.allowlist_io import read_allowlist
    from openakita.skills.categories import CategoryRegistry
    from openakita.skills.category_store import CategoryStore
    from openakita.skills.loader import SkillLoader

    try:
        from openakita.config import settings

        base_path = Path(settings.project_root)
    except Exception:
        base_path = Path.cwd()

    cat_registry = CategoryRegistry()
    cat_registry.set_store(CategoryStore())
    loader = SkillLoader(category_registry=cat_registry)
    await asyncio.to_thread(loader.load_all, base_path)
    _, external_allowlist = read_allowlist()
    try:
        effective_allowlist = loader.compute_effective_allowlist(external_allowlist)
    except Exception:
        effective_allowlist = external_allowlist
    external_skills = [s for s in loader.registry.list_all() if not getattr(s, "system", False)]
    return external_skills, effective_allowlist


async def _propagate(request: Request, action: str, *, rescan: bool = True) -> None:
    """在工作线程中调用 Agent 的统一刷新入口，避免阻塞事件循环。"""
    agent = _resolve_agent(request)
    if agent is None or not hasattr(agent, "propagate_skill_change"):
        return
    try:
        await asyncio.to_thread(agent.propagate_skill_change, action, rescan=rescan)
    except Exception as e:
        logger.warning("propagate_skill_change(%s) failed: %s", action, e)


def _skill_load_issues(loader, *, limit: int = 20) -> list[dict[str, str]]:
    """Return concise non-fatal skill load diagnostics from the active loader."""
    raw = getattr(loader, "last_load_issues", []) or []
    if not isinstance(raw, list | tuple):
        return []
    issues: list[dict[str, str]] = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip()
        error = str(item.get("error") or "").strip()
        path = str(item.get("path") or "").strip()
        if skill_id and error:
            issues.append({"skill_id": skill_id, "error": error, "path": path})
    return issues


async def _auto_translate_new_skills(request: Request, install_url: str) -> None:
    """安装后为缺少 i18n 翻译的技能自动生成中文翻译（写入 agents/openai.yaml）。

    翻译失败不影响安装结果，仅记录日志。
    """
    try:
        actual_agent = _resolve_agent(request)
        if actual_agent is None:
            return

        brain = getattr(actual_agent, "brain", None)
        registry = getattr(actual_agent, "skill_registry", None)
        if not brain or not registry:
            return

        from openakita.skills.i18n import auto_translate_skill

        for skill in registry.list_all():
            if skill.name_i18n:
                continue
            if not skill.skill_path:
                continue
            skill_dir = Path(skill.skill_path).parent
            if not skill_dir.exists():
                continue
            await auto_translate_skill(
                skill_dir,
                skill.name,
                skill.description,
                brain,
            )
    except Exception as e:
        logger.warning(f"Auto-translate after install failed: {e}")


@router.get("/api/skills")
async def list_skills(request: Request):
    """List all available skills with their config schemas.

    Returns ALL discovered skills (including disabled ones) with correct
    ``enabled`` status derived from ``data/skills.json`` allowlist.

    Uses a module-level cache to avoid re-scanning disk on every request.
    The cache is invalidated by install/uninstall/reload/edit operations via
    the cross-layer on-change callback.
    """
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache

    from openakita.skills.allowlist_io import read_allowlist

    skills_json_path, external_allowlist = read_allowlist()
    # 用于生成 relative_path 的 base 仍需项目根目录
    try:
        from openakita.config import settings

        base_path = Path(settings.project_root)
    except Exception:
        base_path = skills_json_path.parent.parent

    try:
        from openakita.skills.loader import SkillLoader

        loader = SkillLoader()
        await asyncio.to_thread(loader.load_all, base_path)
        all_skills = loader.registry.list_all()
        effective_allowlist = loader.compute_effective_allowlist(external_allowlist)
    except Exception:
        actual_agent = _resolve_agent(request)
        if actual_agent is None:
            return {"skills": []}
        registry = getattr(actual_agent, "skill_registry", None)
        if registry is None:
            return {"skills": []}
        all_skills = registry.list_all()
        effective_allowlist = external_allowlist

    skills = []
    for skill in all_skills:
        config = None
        parsed = getattr(skill, "_parsed_skill", None)
        if parsed and hasattr(parsed, "metadata"):
            config = getattr(parsed.metadata, "config", None) or None

        is_system = bool(skill.system)
        sid = getattr(skill, "skill_id", skill.name)
        is_enabled = is_system or effective_allowlist is None or sid in effective_allowlist

        relative_path = None
        if skill.skill_path:
            try:
                relative_path = str(Path(skill.skill_path).relative_to(base_path))
            except (ValueError, TypeError):
                relative_path = sid

        runtime_state = {}
        try:
            from openakita.skills.runtime_registry import read_skill_runtime_registry

            runtime_state = (
                read_skill_runtime_registry()
                .get("skills", {})
                .get(str(sid), {})
            )
            if not isinstance(runtime_state, dict):
                runtime_state = {}
        except Exception:
            runtime_state = {}

        skills.append(
            {
                "skill_id": sid,
                "capability_id": getattr(skill, "capability_id", ""),
                "namespace": getattr(skill, "namespace", ""),
                "origin": getattr(skill, "origin", "project"),
                "visibility": getattr(skill, "visibility", "public"),
                "permission_profile": getattr(skill, "permission_profile", ""),
                "name": skill.name,
                "description": skill.description,
                "name_i18n": skill.name_i18n or None,
                "description_i18n": skill.description_i18n or None,
                "system": is_system,
                "enabled": is_enabled,
                "category": skill.category,
                "tool_name": skill.tool_name,
                "config": config,
                "path": relative_path,
                "source_url": getattr(skill, "source_url", None),
                "runtime_state": {
                    "installed": bool(runtime_state.get("installed", True)),
                    "enabled": is_enabled,
                    "loaded": bool(runtime_state.get("loaded", True)),
                    "deps_hash": runtime_state.get("deps_hash", ""),
                    "pending_update_revision": runtime_state.get("pending_update_revision"),
                    "reload_required": bool(runtime_state.get("reload_required", False)),
                    "update_policy": runtime_state.get("update_policy", "disk-only"),
                },
            }
        )

    def _sort_key(s: dict) -> tuple:
        enabled = s.get("enabled", False)
        system = s.get("system", False)
        if enabled and not system:
            tier = 0
        elif enabled and system:
            tier = 1
        else:
            tier = 2
        return (tier, s.get("name", ""))

    skills.sort(key=_sort_key)

    result = {"skills": skills}
    _skills_cache = result
    return result


_ORGANIZE_BATCH_SIZE = 50


def _compact(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _compute_skill_list_hash(ids: set[str]) -> str:
    import hashlib
    return hashlib.sha256(",".join(sorted(ids)).encode()).hexdigest()[:16]


@router.post("/api/skills/organize")
async def organize_skills(request: Request):
    """Generate a structured preview for AI skill categorization.

    优化策略：
    1. 增量整理 — 跳过已绑定分类的技能，只让 LLM 处理未分类技能
    2. 两阶段并行 — 阶段1生成分类，阶段2分批并行归类，大幅降低总耗时
    3. 结果缓存 — 相同技能列表直接返回上次结果
    4. 优先从 _skills_cache 读取（避免磁盘扫描），回退到全量加载
    """
    global _organize_cache, _organize_cache_hash

    from openakita.skills.categories import is_valid_category_name
    from openakita.skills.category_store import CategoryStore

    agent = _resolve_agent(request)
    if agent is None or not hasattr(agent, "brain"):
        raise HTTPException(status_code=503, detail="Agent 尚未就绪，无法执行 AI 整理")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    force = body.get("force", False)

    # ── 获取技能列表 ──
    cached_skills = _get_external_skills_from_cache()
    if cached_skills is not None:
        skill_items = [
            {
                "id": s["skill_id"],
                "name": s.get("name", s["skill_id"]),
                "desc": s.get("description") or "",
                "cat": s.get("category") or "",
            }
            for s in cached_skills
        ]
        known_ids = {s["skill_id"] for s in cached_skills}
    else:
        try:
            external_skills, _ = await _load_external_skills_for_organize()
        except Exception as e:
            logger.warning("Failed to load skills for AI organize: %s", e)
            raise HTTPException(
                status_code=500, detail="技能列表读取失败，无法生成整理请求"
            ) from e
        skill_items = [
            {
                "id": s.skill_id,
                "name": s.name,
                "desc": s.description or "",
                "cat": s.category or "",
            }
            for s in external_skills
        ]
        known_ids = {s.skill_id for s in external_skills}

    total_count = len(skill_items)

    # ── P2: 结果缓存 — 技能列表未变时直接返回 ──
    current_hash = _compute_skill_list_hash(known_ids)
    if not force and _organize_cache is not None and _organize_cache_hash == current_hash:
        logger.info("[organize] cache hit (hash=%s), returning cached result", current_hash)
        return _organize_cache

    # ── P1: 增量整理 — 只处理未分类技能 ──
    store = CategoryStore()
    store.reload()
    existing_bindings = store.get_bindings()
    existing_categories = store.list_categories()
    existing_cat_names = {c["name"] for c in existing_categories}

    unclassified = [s for s in skill_items if s["id"] not in existing_bindings]
    pre_bound = {
        sid: cat for sid, cat in existing_bindings.items()
        if sid in known_ids and cat in existing_cat_names
    }

    if not unclassified:
        result = {
            "categories": existing_categories,
            "bindings": pre_bound,
            "summary": {
                "total": total_count,
                "included": total_count,
                "truncated": 0,
                "category_count": len(existing_categories),
                "binding_count": len(pre_bound),
                "unassigned_count": 0,
                "by_category": {
                    c["name"]: sum(1 for v in pre_bound.values() if v == c["name"])
                    for c in existing_categories
                },
            },
        }
        _organize_cache = result
        _organize_cache_hash = current_hash
        return result

    # ── 阶段1: 生成分类（只传 id+name，prompt 更小、输出更少） ──
    names_lines: list[str] = []
    for item in sorted(unclassified, key=lambda s: (s["name"], s["id"])):
        names_lines.append(f"- {item['id']}|{item['name']}")

    existing_hint = ""
    if existing_cat_names:
        existing_hint = (
            f"\n已有分类供参考（可复用）: {', '.join(sorted(existing_cat_names))}\n"
        )

    phase1_prompt = "\n".join([
        f"共{len(unclassified)}个待分类技能:",
        *names_lines,
        existing_hint,
        "生成4-12个中文分类（可复用已有分类），返回严格JSON。",
        '{"categories":[{"name":"X","description":"Y"}]}',
    ])

    try:
        phase1_resp = await agent.brain.think_lightweight(
            phase1_prompt,
            system="技能分类助手。只返回JSON对象，无其他文本。",
            max_tokens=800,
        )
        phase1_data = _extract_json_object(phase1_resp.content)
    except Exception as e:
        logger.warning("AI organize phase1 (category generation) failed: %s", e)
        raise HTTPException(
            status_code=502, detail="AI 整理生成失败（阶段1），请检查模型端点后重试"
        ) from e

    raw_categories = phase1_data.get("categories") or []
    if not isinstance(raw_categories, list):
        raise HTTPException(status_code=502, detail="AI 整理结果格式无效（阶段1）")

    categories: list[dict] = []
    category_names: set[str] = set()
    for item in raw_categories[:20]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not is_valid_category_name(name) or name in category_names:
            continue
        description = _compact(str(item.get("description") or ""), 120)
        categories.append({"name": name, "description": description})
        category_names.add(name)

    for ec in existing_categories:
        if ec["name"] not in category_names:
            categories.append(ec)
            category_names.add(ec["name"])

    if not categories:
        raise HTTPException(status_code=502, detail="AI 整理没有生成有效分类")

    # ── 阶段2: 分批并行归类 ──
    cat_list_str = ", ".join(sorted(category_names))
    batches = [
        unclassified[i : i + _ORGANIZE_BATCH_SIZE]
        for i in range(0, len(unclassified), _ORGANIZE_BATCH_SIZE)
    ]

    async def _classify_batch(batch: list[dict]) -> dict[str, str]:
        lines = "\n".join(
            f"- {s['id']}|{s['name']}|{_compact(s['desc'], 30)}" for s in batch
        )
        prompt = "\n".join([
            f"可选分类: {cat_list_str}",
            f"技能({len(batch)}个):",
            lines,
            "",
            "为每个技能选择最合适的分类，返回严格JSON。",
            "bindings的key必须是上方id，value必须是上方可选分类之一，不要凭空创造。",
            '{"bindings":{"skill_id":"category_name"}}',
        ])
        resp = await agent.brain.think_lightweight(
            prompt,
            system="技能分类助手。只返回JSON对象，无其他文本。",
            max_tokens=1500,
        )
        data = _extract_json_object(resp.content)
        return data.get("bindings") or {}

    try:
        batch_results = await asyncio.gather(
            *[_classify_batch(b) for b in batches],
            return_exceptions=True,
        )
    except Exception as e:
        logger.warning("AI organize phase2 (batch classify) failed: %s", e)
        raise HTTPException(
            status_code=502, detail="AI 整理生成失败（阶段2），请检查模型端点后重试"
        ) from e

    new_bindings: dict[str, str] = {}
    for i, result in enumerate(batch_results):
        if isinstance(result, Exception):
            logger.warning("AI organize batch %d/%d failed: %s", i + 1, len(batches), result)
            continue
        if not isinstance(result, dict):
            continue
        for sid_raw, cat_raw in result.items():
            sid = str(sid_raw).strip()
            cat = str(cat_raw).strip()
            if sid in known_ids and cat in category_names:
                new_bindings[sid] = cat

    all_bindings = {**pre_bound, **new_bindings}

    if not all_bindings:
        raise HTTPException(status_code=502, detail="AI 整理没有生成有效技能归类")

    by_category: dict[str, int] = {cat["name"]: 0 for cat in categories}
    for cat in all_bindings.values():
        by_category[cat] = by_category.get(cat, 0) + 1

    result = {
        "categories": categories,
        "bindings": all_bindings,
        "summary": {
            "total": total_count,
            "included": len(unclassified),
            "truncated": 0,
            "category_count": len(categories),
            "binding_count": len(all_bindings),
            "unassigned_count": max(0, total_count - len(all_bindings)),
            "by_category": by_category,
        },
    }
    _organize_cache = result
    _organize_cache_hash = current_hash
    return result


@router.post("/api/skills/organize/apply")
async def apply_skill_organization(request: Request):
    """Apply a reviewed AI skill categorization preview to CategoryStore."""
    from openakita.skills.categories import is_valid_category_name
    from openakita.skills.category_store import CategoryStore

    body = await request.json()
    categories = body.get("categories") or []
    bindings = body.get("bindings") or {}
    rename_map_raw = body.get("rename_map") or {}
    category_order_raw = body.get("category_order") or []
    if not isinstance(categories, list) or not isinstance(bindings, dict):
        raise HTTPException(status_code=400, detail="categories 和 bindings 格式无效")
    if rename_map_raw is not None and not isinstance(rename_map_raw, dict):
        raise HTTPException(status_code=400, detail="rename_map 格式无效")
    if category_order_raw is not None and not isinstance(category_order_raw, list):
        raise HTTPException(status_code=400, detail="category_order 格式无效")

    try:
        external_skills, _ = await _load_external_skills_for_organize()
    except Exception as e:
        raise HTTPException(status_code=500, detail="技能列表读取失败，无法应用整理结果") from e

    known_ids = {s.skill_id for s in external_skills}
    store = CategoryStore()
    store.reload()

    valid_categories_ordered: list[tuple[str, str]] = []
    seen_category_names: set[str] = set()
    valid_categories: dict[str, str] = {}
    for item in categories:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not is_valid_category_name(name):
            continue
        if name in seen_category_names:
            continue
        desc = str(item.get("description") or "").strip()
        valid_categories_ordered.append((name, desc))
        valid_categories[name] = desc
        seen_category_names.add(name)

    if not valid_categories:
        raise HTTPException(status_code=400, detail="没有有效分类可应用")

    rename_map: dict[str, str] = {}
    for old_raw, new_raw in rename_map_raw.items():
        old_name = str(old_raw).strip()
        new_name = str(new_raw).strip()
        if not old_name or not new_name or old_name == new_name:
            continue
        if not is_valid_category_name(new_name):
            raise HTTPException(status_code=400, detail=f"重命名目标非法: {new_name}")
        rename_map[old_name] = new_name

    target_seen: set[str] = set()
    for target in rename_map.values():
        if target in target_seen:
            raise HTTPException(status_code=400, detail="rename_map 存在重名目标冲突")
        target_seen.add(target)

    for start in rename_map:
        seen: set[str] = set()
        cur = start
        while cur in rename_map:
            if cur in seen:
                raise HTTPException(status_code=400, detail="rename_map 存在循环映射")
            seen.add(cur)
            cur = rename_map[cur]

    rename_order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def _dfs(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            raise HTTPException(status_code=400, detail="rename_map 存在循环映射")
        visiting.add(node)
        nxt = rename_map.get(node)
        if nxt and nxt in rename_map:
            _dfs(nxt)
        visiting.remove(node)
        visited.add(node)
        rename_order.append(node)

    for name in rename_map:
        _dfs(name)

    for old_name in rename_order:
        new_name = rename_map[old_name]
        if not store.has_category(old_name):
            continue
        if store.has_category(new_name):
            raise HTTPException(status_code=400, detail=f"重命名冲突，目标已存在: {new_name}")
        ok = store.update_category(old_name, new_name=new_name)
        if not ok:
            raise HTTPException(status_code=400, detail=f"分类重命名失败: {old_name} -> {new_name}")

    applied_categories = 0
    for name, desc in valid_categories_ordered:
        if store.has_category(name):
            store.update_category(name, description=desc)
        else:
            store.create_category(name, desc)
        applied_categories += 1

    applied_bindings = 0
    for sid_raw, cat_raw in bindings.items():
        sid = str(sid_raw).strip()
        cat = str(cat_raw).strip()
        if sid not in known_ids or cat not in valid_categories:
            continue
        store.bind_skill(sid, cat)
        applied_bindings += 1

    if category_order_raw:
        order = [str(x).strip() for x in category_order_raw if str(x).strip()]
        if order:
            store.set_category_order(order)

    _invalidate_skills_cache()
    await _propagate(request, "skill_organize_apply", rescan=True)
    _invalidate_skills_cache()

    return {
        "status": "ok",
        "categories": applied_categories,
        "bindings": applied_bindings,
    }


@router.post("/api/skills/config")
async def update_skill_config(request: Request):
    """Persist skill configuration to data/skill_configs.json."""
    body = await request.json()
    skill_name = body.get("skill_name", "")
    config_values = body.get("config", {})

    if not skill_name:
        raise HTTPException(status_code=400, detail="skill_name is required")

    try:
        from openakita.config import settings

        config_file = settings.project_root / "data" / "skill_configs.json"
    except Exception:
        config_file = Path.cwd() / "data" / "skill_configs.json"

    existing: dict = {}
    if config_file.exists():
        try:
            raw = config_file.read_text(encoding="utf-8")
            existing = json.loads(raw) if raw.strip() else {}
        except Exception:
            pass

    existing[skill_name] = config_values
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {"status": "ok", "skill": skill_name, "config": config_values}


@router.post("/api/skills/install")
async def install_skill(request: Request):
    """安装技能（远程模式替代 Tauri openakita_install_skill 命令）。

    POST body: { "url": "github:user/repo/skill", "category": "Browser" (可选) }

    完成后会：
      1. 把新安装 skill_id upsert 到 data/skills.json 的 external_allowlist
         （仅当已存在该字段；文件不存在时保留“未声明=全部启用”语义）
      2. 通过 ``propagate_skill_change`` 完整刷新运行时缓存与 Agent Pool。

    Args:
        category: 可选大类名。命中且通过校验时安装到 ``skills/<category>/<id>/``；
            否则安装到 ``skills/<id>/`` 顶层（向后兼容）。
    """
    from openakita.skills.allowlist_io import upsert_skill_ids

    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return {"error": "url is required"}
    category_raw = body.get("category")
    category = (
        str(category_raw).strip() if isinstance(category_raw, str) and category_raw.strip() else None
    )

    try:
        from openakita.config import settings

        workspace_dir = str(settings.project_root)
    except Exception:
        workspace_dir = str(Path.cwd())

    from openakita.setup_center.bridge import SkillInstallError
    from openakita.setup_center.bridge import install_skill as _install_skill

    try:
        await asyncio.to_thread(_install_skill, workspace_dir, url, category=category)
    except SkillInstallError as e:
        logger.error("Skill install failed (%s): %s", e.code, e.message, exc_info=True)
        return {"error": e.message, "error_code": e.code}
    except FileNotFoundError as e:
        missing = getattr(e, "filename", None) or "外部命令"
        logger.error("Skill install missing dependency: %s", e, exc_info=True)
        return {
            "error": (
                f"安装失败：未找到可执行命令 `{missing}`。"
                "请先安装 Git 并确保在 PATH 中，或改用 GitHub 简写/单个 SKILL.md 链接。"
            )
        }
    except Exception as e:
        logger.error("Skill install failed: %s", e, exc_info=True)
        return {"error": str(e)}

    # 识别本次新增的 skill 目录（最近修改的 SKILL.md 所在目录）。
    # 升级为 rglob 扫描以兼容分类目录化后的嵌套布局
    # （skills/<category>/<skill_id>/SKILL.md，最多 4 层即可覆盖
    #  <category>/<sub>/<skill> + SKILL.md 的最深路径）。
    install_warning = None
    new_skill_id: str | None = None
    try:
        from openakita.setup_center.bridge import _resolve_skills_dir

        skills_dir = _resolve_skills_dir(workspace_dir)
        candidate_dirs: list[Path] = []
        try:
            for skill_md in skills_dir.rglob("SKILL.md"):
                parent = skill_md.parent
                # 跳过位于隐藏 / 内部目录里的 SKILL.md（如克隆遗留 .git）
                rel_parts = parent.relative_to(skills_dir).parts
                if any(p.startswith(".") or p.startswith("_") for p in rel_parts):
                    continue
                # 限制深度：分类最多嵌套 3 层 + skill 目录 = 4 段
                if len(rel_parts) > 4:
                    continue
                candidate_dirs.append(parent)
        except Exception:
            # rglob 异常时回退到原顶层扫描，避免完全失败
            candidate_dirs = [
                d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()
            ]
        candidates = sorted(
            candidate_dirs,
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            from openakita.skills.parser import SkillParser

            parser = SkillParser()
            try:
                parser.parse_directory(candidates[0])
                new_skill_id = candidates[0].name
            except Exception as parse_err:
                import shutil

                skill_dir_name = candidates[0].name
                logger.error(
                    "Installed skill %s has invalid SKILL.md, removing: %s",
                    skill_dir_name,
                    parse_err,
                )
                shutil.rmtree(str(candidates[0]), ignore_errors=True)
                return {
                    "error": (
                        f"技能文件已下载，但 SKILL.md 格式无效，无法加载：{parse_err}。"
                        "该技能可能不兼容 OpenAkita 格式，已自动清理。"
                    )
                }
    except Exception as ve:
        install_warning = str(ve)
        logger.warning("Post-install validation skipped: %s", ve)

    # 若 skills.json 已有 external_allowlist，自动把新装 skill upsert 进去，
    # 避免被随后的 prune 立即裁掉。不存在 allowlist 字段时跳过（全部启用语义）。
    if new_skill_id:
        try:
            upsert_skill_ids({new_skill_id})
        except Exception as e:
            logger.warning("Failed to upsert %s into skills.json: %s", new_skill_id, e)

    # 若指定了分类，写入分类归属（由 CategoryStore 落盘到 categories[].skills）
    if new_skill_id and category:
        try:
            agent = _resolve_agent(request)
            if agent is not None:
                cat_registry = getattr(agent, "skill_category_registry", None)
                store = getattr(cat_registry, "store", None) if cat_registry else None
                if store is not None:
                    if store.has_category(category):
                        store.bind_skill(new_skill_id, category)
                    else:
                        logger.warning(
                            "Skip binding newly installed skill %s to unknown category %s",
                            new_skill_id,
                            category,
                        )
        except Exception as e:
            logger.warning("Failed to bind %s to category %s: %s", new_skill_id, category, e)

    # 统一刷新入口 —— 重扫磁盘 + 重新应用 allowlist + 重建 catalog + 通知 Pool
    await _propagate(request, "install")

    # 自动翻译（可选，不阻塞成功返回）—— 后台执行，避免拖慢安装路径
    try:
        async def _bg_translate(req=request, src_url=url):
            try:
                await _auto_translate_new_skills(req, src_url)
            except Exception as bg_err:  # pragma: no cover - 仅日志
                logger.debug("Background auto-translate skipped: %s", bg_err)

        asyncio.create_task(_bg_translate())
    except Exception as e:
        logger.debug("Auto-translate scheduling skipped: %s", e)

    result: dict = {"status": "ok", "url": url}
    if install_warning:
        result["warning"] = install_warning
    if new_skill_id:
        result["skill_id"] = new_skill_id
    return result


@router.post("/api/skills/uninstall")
async def uninstall_skill(request: Request):
    """卸载技能。

    POST body: { "skill_id": "skill-directory-name" }
    """
    from openakita.skills.allowlist_io import remove_skill_ids

    body = await request.json()
    skill_id = (body.get("skill_id") or "").strip()
    if not skill_id:
        return {"error": "skill_id is required"}

    try:
        from openakita.config import settings

        workspace_dir = str(settings.project_root)
    except Exception:
        workspace_dir = str(Path.cwd())

    try:
        from openakita.setup_center.bridge import uninstall_skill as _uninstall_skill

        await asyncio.to_thread(_uninstall_skill, workspace_dir, skill_id)
    except Exception as e:
        logger.error("Skill uninstall failed: %s", e, exc_info=True)
        return {"error": str(e)}

    # 从 allowlist 中移除（文件不存在或无该字段时静默跳过）
    try:
        remove_skill_ids({skill_id})
    except Exception as e:
        logger.warning("Failed to remove %s from skills.json: %s", skill_id, e)

    await _propagate(request, "uninstall")

    return {"status": "ok", "skill_id": skill_id}


@router.get("/api/skills/conflicts")
async def list_skill_conflicts(request: Request):
    """Return skill registration conflicts logged by SkillRegistry.

    Each entry has ``skill_id`` / ``name`` / ``action`` (``rejected`` |
    ``overridden``) plus the winner and shadowed source metadata. The frontend
    Skill panel uses this to warn users that two sources tried to register
    the same skill and which one is being used.
    """
    actual_agent = _resolve_agent(request)
    registry = getattr(actual_agent, "skill_registry", None) if actual_agent else None
    if registry is None or not hasattr(registry, "get_conflicts"):
        return {"conflicts": []}
    return {"conflicts": registry.get_conflicts()}


@router.post("/api/skills/conflicts/clear")
async def clear_skill_conflicts(request: Request):
    """Reset the in-memory conflict log (audit only — does not change registrations)."""
    actual_agent = _resolve_agent(request)
    registry = getattr(actual_agent, "skill_registry", None) if actual_agent else None
    if registry is None or not hasattr(registry, "clear_conflicts"):
        return {"ok": False, "error": "skill_registry unavailable"}
    registry.clear_conflicts()
    return {"ok": True}


@router.post("/api/skills/reload")
async def reload_skills(request: Request):
    """热重载技能（安装新技能后、修改 SKILL.md 后、切换启用/禁用后调用）。

    POST body: { "skill_name": "optional-name" }
    如果 skill_name 为空或未提供，则重新扫描并加载所有技能。
    """
    agent = _resolve_agent(request)
    if agent is None:
        return {"error": "Agent not initialized"}

    loader = getattr(agent, "skill_loader", None)
    registry = getattr(agent, "skill_registry", None)
    if not loader or not registry:
        return {"error": "Skill loader/registry not available"}

    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    skill_name = (body.get("skill_name") or "").strip()

    try:
        if skill_name:
            reloaded = await asyncio.to_thread(loader.reload_skill, skill_name)
            if not reloaded:
                return {"error": f"Skill '{skill_name}' not found or reload failed"}
            await _propagate(request, "reload", rescan=False)
            return {"status": "ok", "reloaded": [skill_name]}

        await _propagate(request, "reload", rescan=True)
        total = len(registry.list_all())
        issues = _skill_load_issues(loader)
        result = {
            "status": "ok",
            "reloaded": "all",
            "total": total,
        }
        if issues:
            result.update(
                {
                    "partial": True,
                    "skipped_count": len(issues),
                    "skipped_skills": issues,
                    "warning": (
                        f"已刷新可用技能，但有 {len(issues)} 个技能未加载。"
                        "其他技能可正常使用。"
                    ),
                }
            )
        return result
    except Exception as e:
        logger.error(f"Skill reload failed: {e}")
        return {"error": str(e)}


@router.get("/api/skills/content/{skill_name:path}")
async def get_skill_content(skill_name: str, request: Request):
    """读取单个技能的 SKILL.md 原始内容。

    返回 { content, path, system } 供前端展示和编辑。
    系统内置技能标记 system=true，前端可据此决定是否允许编辑。
    """
    from openakita.skills.loader import SkillLoader

    try:
        from openakita.config import settings

        base_path = Path(settings.project_root)
    except Exception:
        base_path = Path.cwd()

    actual_agent = _resolve_agent(request)

    skill = None
    if actual_agent:
        loader = getattr(actual_agent, "skill_loader", None)
        if loader:
            skill = loader.get_skill(skill_name)

    if not skill:
        try:
            tmp_loader = SkillLoader()
            tmp_loader.load_all(base_path=base_path)
            skill = tmp_loader.get_skill(skill_name)
        except Exception:
            pass

    if not skill:
        return {"error": f"Skill '{skill_name}' not found"}

    try:
        content = skill.path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"Failed to read SKILL.md: {e}"}

    safe_path = skill_name
    try:
        safe_path = str(Path(skill.path).relative_to(base_path))
    except (ValueError, TypeError):
        pass

    return {
        "content": content,
        "path": safe_path,
        "system": skill.metadata.system,
    }


@router.put("/api/skills/content/{skill_name:path}")
async def update_skill_content(skill_name: str, request: Request):
    """更新技能的 SKILL.md 内容并热重载。

    PUT body: { "content": "完整的 SKILL.md 内容" }
    """
    from openakita.skills.parser import skill_parser

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    new_content = body.get("content", "")
    if not new_content.strip():
        return {"error": "content is required"}

    actual_agent = _resolve_agent(request)

    skill = None
    loader = None
    if actual_agent:
        loader = getattr(actual_agent, "skill_loader", None)
        if loader:
            skill = loader.get_skill(skill_name)

    if not skill:
        return {"error": f"Skill '{skill_name}' not found"}

    if skill.metadata.system:
        return {"error": "Cannot edit system (built-in) skills"}

    try:
        parsed = skill_parser.parse_content(new_content, skill.path)
    except Exception as e:
        return {"error": f"Invalid SKILL.md format: {e}"}

    try:
        skill.path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return {"error": f"Failed to write SKILL.md: {e}"}

    reloaded = False
    if loader:
        try:
            result = await asyncio.to_thread(loader.reload_skill, skill_name)
            if result:
                await _propagate(request, "content_update", rescan=False)
                reloaded = True
        except Exception as e:
            logger.warning(f"Skill reload after edit failed: {e}")

    return {
        "status": "ok",
        "reloaded": reloaded,
        "name": parsed.metadata.name,
        "description": parsed.metadata.description,
    }


@router.get("/api/skills/marketplace")
async def search_marketplace(q: str = "agent"):
    """Proxy to skills.sh search API (bypasses CORS for desktop app)."""
    from openakita.llm.providers.proxy_utils import (
        get_httpx_transport,
        get_proxy_config,
    )

    try:
        client_kwargs: dict = {
            "timeout": 15,
            "follow_redirects": True,
            "trust_env": False,
        }

        proxy = get_proxy_config()
        if proxy:
            client_kwargs["proxy"] = proxy

        transport = get_httpx_transport()
        if transport:
            client_kwargs["transport"] = transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(SKILLS_SH_API, params={"q": q})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("skills.sh API error: %s", e)
        return {"skills": [], "count": 0, "error": str(e)}


# ──────────────────────────────────────────────────────────────────────
# Cross-layer event subscribers
#
# ``Agent.propagate_skill_change`` 是所有刷新路径的起点，
# 其最后一步会调用 ``notify_skills_changed(action)``；此处注册两个副作用：
#   1. 清空 GET /api/skills 的模块缓存，使前端下次 GET 时拿到最新列表
#   2. 通过 WebSocket 广播 ``skills:changed`` 事件，前端可实时刷新 UI
#
# AgentInstancePool 的版本号提升已在 ``propagate_skill_change`` 内部完成，
# 此处**不再**重复通知池，避免版本号被同一次变更递增两次。
# ──────────────────────────────────────────────────────────────────────


def _broadcast_ws_event(action: str) -> None:
    """WebSocket 广播（fire-and-forget）。"""
    try:
        from openakita.api.routes.websocket import broadcast_event

        asyncio.ensure_future(broadcast_event("skills:changed", {"action": action}))
    except Exception:
        pass


def _on_skills_changed_api(action: str) -> None:
    """由 ``skills.events.notify_skills_changed`` 触发的 API 层副作用。"""
    _invalidate_skills_cache()
    _broadcast_ws_event(action)


try:
    from openakita.skills.events import register_on_change

    register_on_change(_on_skills_changed_api)
except Exception:
    pass

