"""
Skill categories route: /api/skill-categories

技能分类管理 — 基于 JSON 持久化（data/skills/skill_categories.json）。

设计要点：
- 分类定义和技能绑定关系保存在 CategoryStore (JSON)，不再依赖文件夹结构
- 写入操作末尾统一调用 ``Agent.propagate_skill_change``（与
  ``api/routes/skills.py`` 共享相同的刷新路径），由其完成 loader 重扫 →
  allowlist 应用 → catalog 重建 → WebSocket 广播
- "启停大类" 是 mass action：直接对 ``data/skills.json`` 的
  ``external_allowlist`` 做 add / remove
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 工具：解析 agent / store / 触发刷新 ──────────────────────────────────


def _resolve_agent(request: Request):
    from openakita.core.agent import Agent

    agent = getattr(request.app.state, "agent", None)
    if isinstance(agent, Agent):
        return agent
    return getattr(agent, "_local_agent", None)


def _resolve_store(request: Request):
    """从 agent 获取 CategoryStore 实例。"""
    agent = _resolve_agent(request)
    if agent is None:
        return None
    cat_registry = getattr(agent, "skill_category_registry", None)
    if cat_registry is None:
        return None
    return getattr(cat_registry, "store", None)


async def _propagate(request: Request, action: str, *, rescan: bool = True) -> None:
    agent = _resolve_agent(request)
    if agent is None or not hasattr(agent, "propagate_skill_change"):
        return
    try:
        await asyncio.to_thread(agent.propagate_skill_change, action, rescan=rescan)
    except Exception as e:
        logger.warning("propagate_skill_change(%s) failed: %s", action, e)


async def _resolve_skill_for_move(request: Request, skill_id: str) -> tuple[bool, bool]:
    """解析技能是否存在及是否系统技能。

    优先使用运行时 registry；若未命中则回退到磁盘全量扫描，避免
    disabled 外部技能被 prune 后出现“列表可见但移动时报不存在”。

    Returns:
        (exists, is_system)
    """
    agent = _resolve_agent(request)
    if agent is None:
        return False, False

    skill_registry = getattr(agent, "skill_registry", None)
    if skill_registry is not None:
        entry = skill_registry.get(skill_id)
        if entry is not None:
            return True, bool(getattr(entry, "system", False))

    # fallback: 从磁盘全量扫描确认（不依赖当前运行时 registry 的 prune 状态）
    from openakita.skills.loader import SkillLoader

    try:
        from openakita.config import settings

        base_path = Path(settings.project_root)
    except Exception:
        base_path = Path.cwd()

    loader = SkillLoader()
    await asyncio.to_thread(loader.load_all, base_path)
    loaded = loader.registry.get(skill_id)
    if loaded is None:
        return False, False
    return True, bool(getattr(loaded, "system", False))


# ── GET /api/skill-categories ──────────────────────────────────────────


@router.get("/api/skill-categories")
async def list_categories(request: Request):
    """列出所有技能大类。

    返回每个分类的：name / description / total（成员总数） / enabled（启用数） /
    system_readonly（是否只读）。

    通过临时 SkillLoader 从磁盘全量扫描以获取稳定的 total 计数（避免被
    agent.skill_registry 的 prune 行为影响），再用 effective allowlist
    判断 enabled 状态。
    """
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
        effective = loader.compute_effective_allowlist(external_allowlist)
    except Exception:
        effective = None

    def _is_enabled(skill) -> bool:
        if getattr(skill, "system", False):
            return True
        if effective is None:
            return True
        return skill.skill_id in effective

    by_category: dict[str, list] = {}
    for s in loader.registry.list_all():
        cat = s.category or "Uncategorized"
        by_category.setdefault(cat, []).append(s)

    declared = {e.name: e for e in cat_registry.list_all()}

    items: list[dict] = []
    for cat in sorted(set(declared.keys()) | set(by_category.keys())):
        skills = by_category.get(cat, [])
        total = len(skills)
        enabled = sum(1 for s in skills if _is_enabled(s))
        meta = declared.get(cat)
        items.append(
            {
                "name": cat,
                "description": (meta.description if meta else None),
                "total": total,
                "enabled": enabled,
                "system_readonly": bool(meta.system_readonly) if meta else False,
                # 是否来自 CategoryStore 的显式分类定义。
                # False 表示该分类仅来自技能 metadata/frontmatter 的推断聚合。
                "declared": bool(meta is not None),
            }
        )

    return {"categories": items}


# ── POST /api/skill-categories ─────────────────────────────────────────


@router.post("/api/skill-categories")
async def create_category(request: Request):
    """创建新分类（写入 JSON）。

    Body: { "name": "浏览器", "description": "网页打开/截图/标签管理" }
    """
    from openakita.skills.categories import is_valid_category_name

    body = await request.json()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()

    if not is_valid_category_name(name):
        raise HTTPException(
            status_code=400,
            detail="分类名非法：不可为空，且不可与系统命名空间冲突",
        )

    store = _resolve_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="CategoryStore 未初始化")

    if not store.create_category(name, description):
        raise HTTPException(status_code=409, detail=f"分类已存在: {name}")

    await _propagate(request, "category_create", rescan=True)
    return {"status": "ok", "name": name}


# ── PATCH /api/skill-categories/{name:path} ────────────────────────────


@router.patch("/api/skill-categories/{name:path}")
async def patch_category(name: str, request: Request):
    """修改分类描述或重命名。

    Body: { "description"?: str, "new_name"?: str }
    """
    from openakita.skills.categories import is_valid_category_name

    body = await request.json()
    new_description = body.get("description")
    new_name_raw = body.get("new_name")

    agent = _resolve_agent(request)
    if agent is not None:
        cat_registry = getattr(agent, "skill_category_registry", None)
        if cat_registry is not None:
            entry = cat_registry.get(name)
            if entry is not None and entry.system_readonly:
                raise HTTPException(status_code=409, detail="只读分类不可修改")

    store = _resolve_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="CategoryStore 未初始化")

    if not store.has_category(name):
        raise HTTPException(status_code=404, detail=f"分类不存在: {name}")

    new_name = None
    if new_name_raw and isinstance(new_name_raw, str) and new_name_raw.strip() != name:
        new_name = new_name_raw.strip()
        if not is_valid_category_name(new_name):
            raise HTTPException(status_code=400, detail="新分类名非法")

    ok = store.update_category(
        name,
        new_name=new_name,
        description=new_description if isinstance(new_description, str) else None,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="更新失败（可能目标名称已存在）")

    final_name = new_name if new_name else name
    await _propagate(request, "category_patch", rescan=True)
    return {"status": "ok", "name": final_name}


# ── DELETE /api/skill-categories/{name:path} ───────────────────────────


@router.delete("/api/skill-categories/{name:path}")
async def delete_category(name: str, request: Request):
    """删除分类（同时清除该分类下所有 bindings）。"""
    agent = _resolve_agent(request)
    if agent is not None:
        cat_registry = getattr(agent, "skill_category_registry", None)
        if cat_registry is not None:
            entry = cat_registry.get(name)
            if entry is not None and entry.system_readonly:
                raise HTTPException(status_code=409, detail="只读分类不可删除")

    store = _resolve_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="CategoryStore 未初始化")

    if not store.delete_category(name):
        raise HTTPException(status_code=404, detail=f"分类不存在: {name}")

    await _propagate(request, "category_delete", rescan=True)
    return {"status": "ok", "name": name}


# ── POST /api/skill-categories/{name:path}/enable ──────────────────────


async def _scan_external_ids_in_category(
    category: str,
) -> tuple[set[str], int]:
    """从磁盘全量扫描，收集指定分类下所有 *外部* 技能 ID。

    不依赖 agent.skill_registry（可能被 prune_external_by_allowlist 裁剪过，
    导致已禁用的技能从 registry 消失，后续 enable 找不到它们）。
    每次都通过临时 SkillLoader 从磁盘扫描，确保总能看到全部技能。

    Returns:
        (external_ids, system_count): 外部技能 ID 集合，以及该分类中系统技能的数量。
    """
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

    ids: set[str] = set()
    system_count = 0
    for s in loader.registry.list_all():
        if (s.category or "Uncategorized") != category:
            continue
        if getattr(s, "system", False):
            system_count += 1
            continue
        ids.add(s.skill_id)
    return ids, system_count


def _ensure_skills_cache_invalidated() -> None:
    """显式失效 GET /api/skills 的模块级缓存（安全网）。"""
    try:
        from openakita.api.routes.skills import _invalidate_skills_cache
        _invalidate_skills_cache()
    except Exception:
        pass


@router.post("/api/skill-categories/{name:path}/enable")
async def enable_category(name: str, request: Request):
    """批量启用：把该分类下所有外部技能 ID upsert 进 allowlist。"""
    from openakita.skills.allowlist_io import (
        overwrite_allowlist,
        read_allowlist,
        upsert_skill_ids,
    )

    target_ids, system_count = await _scan_external_ids_in_category(name)
    logger.info(
        "[category/enable] category=%r  external=%d  system=%d  ids=%s",
        name, len(target_ids), system_count, sorted(target_ids)[:5],
    )
    if not target_ids:
        return {
            "status": "ok", "name": name, "added": 0,
            "system_count": system_count,
        }

    _, declared = read_allowlist()
    if declared is None:
        from openakita.skills.loader import SkillLoader
        try:
            from openakita.config import settings
            base_path = Path(settings.project_root)
        except Exception:
            base_path = Path.cwd()
        loader = SkillLoader()
        await asyncio.to_thread(loader.load_all, base_path)
        try:
            effective = loader.compute_effective_allowlist(None) or set()
        except Exception:
            effective = set()
        merged = set(effective) | target_ids
        overwrite_allowlist(merged)
    else:
        upsert_skill_ids(target_ids)

    _ensure_skills_cache_invalidated()
    await _propagate(request, "category_enable", rescan=True)
    _ensure_skills_cache_invalidated()
    return {"status": "ok", "name": name, "added": len(target_ids)}


# ── POST /api/skill-categories/{name:path}/disable ─────────────────────


@router.post("/api/skill-categories/{name:path}/disable")
async def disable_category(name: str, request: Request):
    """批量禁用：把该分类下所有外部技能 ID 从 allowlist 中剔除。"""
    from openakita.skills.allowlist_io import (
        overwrite_allowlist,
        read_allowlist,
        remove_skill_ids,
    )

    target_ids, system_count = await _scan_external_ids_in_category(name)
    logger.info(
        "[category/disable] category=%r  external=%d  system=%d  ids=%s",
        name, len(target_ids), system_count, sorted(target_ids)[:5],
    )
    if not target_ids:
        return {
            "status": "ok", "name": name, "removed": 0,
            "system_count": system_count,
        }

    _, declared = read_allowlist()
    if declared is None:
        from openakita.skills.loader import SkillLoader
        try:
            from openakita.config import settings
            base_path = Path(settings.project_root)
        except Exception:
            base_path = Path.cwd()
        loader = SkillLoader()
        await asyncio.to_thread(loader.load_all, base_path)
        try:
            effective = loader.compute_effective_allowlist(None) or set()
        except Exception:
            effective = set()
        remaining = set(effective) - target_ids
        overwrite_allowlist(remaining)
    else:
        remove_skill_ids(target_ids)

    _ensure_skills_cache_invalidated()
    await _propagate(request, "category_disable", rescan=True)
    _ensure_skills_cache_invalidated()
    return {"status": "ok", "name": name, "removed": len(target_ids)}


# ── POST /api/skill-categories/move ────────────────────────────────────


@router.post("/api/skill-categories/move")
async def move_skill(request: Request):
    """把技能绑定到指定分类（逻辑绑定，不移动文件）。

    Body: { "skill_id": "browser-open", "target_category": "浏览器" | null }

    target_category 为 null 时解除绑定（技能变为 Uncategorized）。
    """
    body = await request.json()
    skill_id = (body.get("skill_id") or "").strip()
    target_category = body.get("target_category")
    if isinstance(target_category, str):
        target_category = target_category.strip() or None

    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id 必填")

    exists, is_system_skill = await _resolve_skill_for_move(request, skill_id)
    if not exists:
        raise HTTPException(status_code=404, detail=f"技能不存在: {skill_id}")
    if is_system_skill:
        raise HTTPException(status_code=409, detail="系统技能不可移动")

    agent = _resolve_agent(request)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未就绪")

    store = _resolve_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="CategoryStore 未初始化")

    if target_category:
        if not store.has_category(target_category):
            raise HTTPException(status_code=404, detail=f"分类不存在: {target_category}")
        loader = getattr(agent, "skill_loader", None)
        if loader is not None:
            try:
                for s in loader.registry.list_all():
                    if (s.category or "Uncategorized") == target_category and getattr(s, "system", False):
                        raise HTTPException(status_code=409, detail="外部技能不可移动到系统分类")
            except HTTPException:
                raise
            except Exception:
                # loader 状态异常时回退到下方 registry 标记检查
                pass
        cat_registry = getattr(agent, "skill_category_registry", None)
        if cat_registry is not None:
            target_entry = cat_registry.get(target_category)
            if target_entry is not None and target_entry.system_readonly:
                raise HTTPException(status_code=409, detail="外部技能不可移动到系统分类")
        store.bind_skill(skill_id, target_category)
    else:
        store.unbind_skill(skill_id)

    await _propagate(request, "category_move", rescan=True)
    return {
        "status": "ok",
        "skill_id": skill_id,
        "target_category": target_category,
    }
