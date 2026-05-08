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


def _invalidate_skills_cache() -> None:
    """Clear the cached skill list so the next GET /api/skills re-scans disk."""
    global _skills_cache
    _skills_cache = None


def _resolve_agent(request: Request):
    """返回真实 Agent 实例（解包可能的 thin wrapper / _local_agent）。"""
    from openakita.core.agent import Agent

    agent = getattr(request.app.state, "agent", None)
    if isinstance(agent, Agent):
        return agent
    return getattr(agent, "_local_agent", None)


async def _propagate(request: Request, action: str, *, rescan: bool = True) -> None:
    """在工作线程中调用 Agent 的统一刷新入口，避免阻塞事件循环。"""
    agent = _resolve_agent(request)
    if agent is None or not hasattr(agent, "propagate_skill_change"):
        return
    try:
        await asyncio.to_thread(agent.propagate_skill_change, action, rescan=rescan)
    except Exception as e:
        logger.warning("propagate_skill_change(%s) failed: %s", action, e)


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
        return {
            "status": "ok",
            "reloaded": "all",
            "total": total,
        }
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

