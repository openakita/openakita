"""
Skill categories route: /api/skill-categories

技能大类管理（参考 hermes-agent 的 DESCRIPTION.md 范式）。

设计要点：
- 写入操作末尾统一调用 ``Agent.propagate_skill_change``（与
  ``api/routes/skills.py`` 共享相同的刷新路径），由其完成 loader 重扫 →
  allowlist 应用 → catalog 重建 → WebSocket 广播
- "启停大类" 是 mass action：直接对 ``data/skills.json`` 的
  ``external_allowlist`` 做 add / remove。disable 大类时若 allowlist 未声明，
  先 materialize 当前 effective set 再剔除目标 IDs，避免破坏"全部启用"语义
- 仅用户可写根（项目 ``skills/`` 与 ``__user_workspace__/skills/``）支持
  create / rename / move 写入；只读分类（``skills/system/`` 与
  ``__builtin__``）的写操作返回 409
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 工具：解析 agent / 触发刷新 ────────────────────────────────────────


def _resolve_agent(request: Request):
    from openakita.core.agent import Agent

    agent = getattr(request.app.state, "agent", None)
    if isinstance(agent, Agent):
        return agent
    return getattr(agent, "_local_agent", None)


async def _propagate(request: Request, action: str, *, rescan: bool = True) -> None:
    agent = _resolve_agent(request)
    if agent is None or not hasattr(agent, "propagate_skill_change"):
        return
    try:
        await asyncio.to_thread(agent.propagate_skill_change, action, rescan=rescan)
    except Exception as e:
        logger.warning("propagate_skill_change(%s) failed: %s", action, e)


def _resolve_writable_root() -> Path:
    """返回当前工作区可写技能根（用于 create/move 操作）。

    复用 SkillLoader._resolve_user_workspace_skills 的语义：优先用
    ``settings.skills_path``，否则回退到 ``OPENAKITA_ROOT/workspaces/default/skills``。
    """
    try:
        from openakita.config import settings

        return Path(settings.skills_path)
    except Exception:
        from openakita.skills.loader import _resolve_user_workspace_skills

        return _resolve_user_workspace_skills()


def _safe_join(root: Path, rel: str) -> Path:
    """把 rel 安全拼到 root 下，拒绝路径穿越。"""
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"非法路径（疑似路径穿越）: {rel}"
        ) from e
    return candidate


# ── GET /api/skill-categories ──────────────────────────────────────────


@router.get("/api/skill-categories")
async def list_categories(request: Request):
    """列出所有技能大类。

    返回每个分类的：name / description / total（成员总数） / enabled（启用数） /
    system_readonly（是否只读） / source_dir（绝对路径，仅用于诊断）。

    成员总数与启用数从 SkillRegistry 实时计算（而非 CategoryRegistry 的
    skill_ids），避免 system/external 双源场景下的口径不一致。
    """
    agent = _resolve_agent(request)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未就绪")

    cat_registry = getattr(agent, "skill_category_registry", None)
    skill_registry = getattr(agent, "skill_registry", None)
    if cat_registry is None or skill_registry is None:
        raise HTTPException(status_code=503, detail="技能系统未初始化")

    by_category: dict[str, list] = {}
    for s in skill_registry.list_all():
        cat = s.category or "Uncategorized"
        by_category.setdefault(cat, []).append(s)

    declared = {e.name: e for e in cat_registry.list_all()}

    seen: set[str] = set()
    items: list[dict] = []
    for cat in sorted(set(declared.keys()) | set(by_category.keys())):
        seen.add(cat)
        skills = by_category.get(cat, [])
        total = len(skills)
        enabled = sum(1 for s in skills if not getattr(s, "disabled", False))
        meta = declared.get(cat)
        items.append(
            {
                "name": cat,
                "description": (meta.description if meta else None),
                "total": total,
                "enabled": enabled,
                "system_readonly": bool(meta.system_readonly) if meta else False,
                "source_dir": (str(meta.source_dir) if meta and meta.source_dir else None),
            }
        )

    return {"categories": items}


# ── POST /api/skill-categories ─────────────────────────────────────────


@router.post("/api/skill-categories")
async def create_category(request: Request):
    """创建新分类（在用户可写根下建子目录 + 写 DESCRIPTION.md）。

    Body: { "name": "Browser", "description": "网页打开/截图/标签管理" }
    """
    from openakita.skills.categories import is_valid_category_name

    body = await request.json()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()

    if not is_valid_category_name(name):
        raise HTTPException(
            status_code=400,
            detail="分类名非法：仅支持小写字母/数字/连字符，可用 / 表示嵌套；不可与系统命名空间冲突",
        )

    root = _resolve_writable_root()
    cat_dir = _safe_join(root, name)
    if cat_dir.exists():
        raise HTTPException(status_code=409, detail=f"分类目录已存在: {name}")

    try:
        cat_dir.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"创建分类目录失败: {e}") from e

    desc_text = description or f"User-defined category '{name}'."
    desc_file = cat_dir / "DESCRIPTION.md"
    try:
        desc_file.write_text(
            f"---\ndescription: {desc_text}\n---\n\n# {name}\n\n{desc_text}\n",
            encoding="utf-8",
        )
    except OSError as e:
        # 回滚
        try:
            cat_dir.rmdir()
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"写 DESCRIPTION.md 失败: {e}") from e

    await _propagate(request, "category_create", rescan=True)
    return {"status": "ok", "name": name, "path": str(cat_dir)}


# ── PATCH /api/skill-categories/{name:path} ────────────────────────────


@router.patch("/api/skill-categories/{name:path}")
async def patch_category(name: str, request: Request):
    """修改分类描述或重命名分类目录。

    Body: { "description"?: str, "new_name"?: str }
    """
    from openakita.skills.categories import is_valid_category_name

    body = await request.json()
    new_description = body.get("description")
    new_name_raw = body.get("new_name")

    root = _resolve_writable_root()
    src = _safe_join(root, name)
    if not src.exists() or not src.is_dir():
        raise HTTPException(status_code=404, detail=f"分类不存在: {name}")

    # 只读分类（system / __builtin__）拒绝写
    agent = _resolve_agent(request)
    if agent is not None:
        cat_registry = getattr(agent, "skill_category_registry", None)
        if cat_registry is not None:
            entry = cat_registry.get(name)
            if entry is not None and entry.system_readonly:
                raise HTTPException(status_code=409, detail="只读分类不可修改")

    # 1. 重命名（包含描述时一起处理）
    final_name = name
    final_dir = src
    if new_name_raw and isinstance(new_name_raw, str) and new_name_raw.strip() != name:
        new_name = new_name_raw.strip()
        if not is_valid_category_name(new_name):
            raise HTTPException(status_code=400, detail="新分类名非法")
        dst = _safe_join(root, new_name)
        if dst.exists():
            raise HTTPException(status_code=409, detail=f"目标分类已存在: {new_name}")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"重命名失败: {e}") from e
        final_name = new_name
        final_dir = dst

    # 2. 写入 / 更新 DESCRIPTION.md
    if isinstance(new_description, str):
        desc_text = new_description.strip() or f"Category '{final_name}'."
        desc_file = final_dir / "DESCRIPTION.md"
        try:
            desc_file.write_text(
                f"---\ndescription: {desc_text}\n---\n\n# {final_name}\n\n{desc_text}\n",
                encoding="utf-8",
            )
        except OSError as e:
            raise HTTPException(
                status_code=500, detail=f"写 DESCRIPTION.md 失败: {e}"
            ) from e

    await _propagate(request, "category_patch", rescan=True)
    return {"status": "ok", "name": final_name, "path": str(final_dir)}


# ── POST /api/skill-categories/{name:path}/enable ──────────────────────


def _collect_external_skill_ids_in_category(
    skill_registry, category: str
) -> set[str]:
    """收集指定分类下所有 *外部* 技能 ID（system 不参与 allowlist）。"""
    ids: set[str] = set()
    for s in skill_registry.list_all():
        if getattr(s, "system", False):
            continue
        if (s.category or "Uncategorized") == category:
            ids.add(s.skill_id)
    return ids


@router.post("/api/skill-categories/{name:path}/enable")
async def enable_category(name: str, request: Request):
    """批量启用：把该分类下所有外部技能 ID upsert 进 allowlist。

    若 allowlist 未声明（即 data/skills.json 不存在或无字段），先 materialize
    当前 effective set 再合并；这样 enable 操作变成幂等且语义清晰。
    """
    from openakita.skills.allowlist_io import (
        overwrite_allowlist,
        read_allowlist,
        upsert_skill_ids,
    )

    agent = _resolve_agent(request)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未就绪")
    skill_registry = getattr(agent, "skill_registry", None)
    if skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry 未初始化")

    target_ids = _collect_external_skill_ids_in_category(skill_registry, name)
    if not target_ids:
        return {"status": "ok", "name": name, "added": 0}

    _, declared = read_allowlist()
    if declared is None:
        # 未声明：先 materialize（compute_effective_allowlist 会扣除 default disabled）
        loader = getattr(agent, "skill_loader", None)
        if loader is None:
            raise HTTPException(status_code=503, detail="SkillLoader 未初始化")
        try:
            effective = loader.compute_effective_allowlist(None) or set()
        except Exception:
            effective = set()
        merged = set(effective) | target_ids
        overwrite_allowlist(merged)
    else:
        upsert_skill_ids(target_ids)

    await _propagate(request, "category_enable", rescan=False)
    return {"status": "ok", "name": name, "added": len(target_ids)}


# ── POST /api/skill-categories/{name:path}/disable ─────────────────────


@router.post("/api/skill-categories/{name:path}/disable")
async def disable_category(name: str, request: Request):
    """批量禁用：把该分类下所有外部技能 ID 从 allowlist 中剔除。

    若 allowlist 未声明，先 materialize 当前 effective set（=全部启用 -
    DEFAULT_DISABLED_SKILLS）作为基线，再剔除目标 IDs；保证语义在两种状态下一致。
    """
    from openakita.skills.allowlist_io import (
        overwrite_allowlist,
        read_allowlist,
        remove_skill_ids,
    )

    agent = _resolve_agent(request)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未就绪")
    skill_registry = getattr(agent, "skill_registry", None)
    if skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry 未初始化")

    target_ids = _collect_external_skill_ids_in_category(skill_registry, name)
    if not target_ids:
        return {"status": "ok", "name": name, "removed": 0}

    _, declared = read_allowlist()
    if declared is None:
        loader = getattr(agent, "skill_loader", None)
        if loader is None:
            raise HTTPException(status_code=503, detail="SkillLoader 未初始化")
        try:
            effective = loader.compute_effective_allowlist(None) or set()
        except Exception:
            effective = set()
        remaining = set(effective) - target_ids
        overwrite_allowlist(remaining)
    else:
        remove_skill_ids(target_ids)

    await _propagate(request, "category_disable", rescan=False)
    return {"status": "ok", "name": name, "removed": len(target_ids)}


# ── POST /api/skill-categories/move ────────────────────────────────────


@router.post("/api/skill-categories/move")
async def move_skill(request: Request):
    """把单个技能目录移动到指定分类下。

    Body: { "skill_id": "browser-open", "target_category": "Browser" | null }

    target_category 为 null 时移回顶层（``skills/<skill_id>/``）。
    源 / 目标必须均位于用户可写根下；只读源（system / __builtin__）拒绝。
    """
    from openakita.skills.categories import is_valid_category_name

    body = await request.json()
    skill_id = (body.get("skill_id") or "").strip()
    target_category = body.get("target_category")
    if isinstance(target_category, str):
        target_category = target_category.strip() or None

    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id 必填")
    if target_category and not is_valid_category_name(target_category):
        raise HTTPException(status_code=400, detail="目标分类名非法")

    agent = _resolve_agent(request)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未就绪")
    skill_registry = getattr(agent, "skill_registry", None)
    if skill_registry is None:
        raise HTTPException(status_code=503, detail="SkillRegistry 未初始化")

    entry = skill_registry.get(skill_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"技能不存在: {skill_id}")
    if getattr(entry, "system", False):
        raise HTTPException(status_code=409, detail="系统技能不可移动")

    skill_path = getattr(entry, "skill_path", None)
    if not skill_path:
        raise HTTPException(status_code=404, detail="技能没有源目录路径，无法移动")
    # SkillEntry.skill_path 指向 SKILL.md 文件本身，移动的是其所在目录
    src_dir = Path(skill_path).parent.resolve()

    root = _resolve_writable_root().resolve()
    try:
        src_dir.relative_to(root)
    except ValueError as e:
        raise HTTPException(
            status_code=409, detail="只读源（非用户可写根下）不可移动"
        ) from e

    if target_category:
        target_root = _safe_join(root, target_category)
        target_root.mkdir(parents=True, exist_ok=True)
        dst_dir = target_root / src_dir.name
    else:
        dst_dir = root / src_dir.name

    if dst_dir.resolve() == src_dir:
        return {"status": "ok", "skill_id": skill_id, "moved": False}
    if dst_dir.exists():
        raise HTTPException(status_code=409, detail=f"目标已存在: {dst_dir}")

    try:
        shutil.move(str(src_dir), str(dst_dir))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"移动失败: {e}") from e

    await _propagate(request, "category_move", rescan=True)
    return {
        "status": "ok",
        "skill_id": skill_id,
        "moved": True,
        "target": str(dst_dir),
    }

