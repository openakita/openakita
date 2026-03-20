"""
Agent package import/export routes + Hub/Store proxy routes.

Local routes for the Setup Center frontend to call:
- POST /api/agents/package/export     — export agent to .akita-agent
- POST /api/agents/package/import     — import from .akita-agent
- POST /api/agents/package/inspect    — preview package contents
- GET  /api/agents/package/exportable — list exportable agents
- GET  /api/hub/agents                — proxy search Agent Store
- GET  /api/hub/agents/{id}           — proxy get Agent detail
- POST /api/hub/agents/{id}/install   — download + install from Hub
- GET  /api/hub/skills                — proxy search Skill Store
- GET  /api/hub/skills/{id}           — proxy get Skill detail
- POST /api/hub/skills/{id}/install   — install Skill from Store
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _project_root() -> Path:
    try:
        from openakita.config import settings
        return Path(settings.project_root)
    except Exception:
        return Path.cwd()


def _get_stores():
    from openakita.config import settings
    root = Path(settings.project_root)

    from openakita.agents.profile import get_profile_store
    profile_store = get_profile_store()

    skills_dir = Path(settings.skills_path)
    return profile_store, skills_dir, root


def _reload_skills(request) -> None:
    """Trigger skill reload on the running agent after installing from platform.

    Uses the same mechanism as POST /api/skills/reload — access the live
    agent's skill_loader to re-scan all skill directories.
    Best-effort: failures are logged but never break the install flow.
    """
    try:
        from openakita.core.agent import Agent

        agent = getattr(request.app.state, "agent", None)
        actual_agent = agent
        if not isinstance(agent, Agent):
            actual_agent = getattr(agent, "_local_agent", None)
        if actual_agent is None:
            logger.debug("Skill reload skipped: agent not initialized")
            return

        loader = getattr(actual_agent, "skill_loader", None)
        if not loader:
            logger.debug("Skill reload skipped: no skill_loader on agent")
            return

        count = loader.load_all(Path(_project_root()))
        logger.info(f"Skills reloaded after platform install: {count} loaded")
    except Exception as e:
        logger.warning(f"Skill reload after platform install failed (non-blocking): {e}")


class ExportRequest(BaseModel):
    profile_id: str
    author_name: str = ""
    author_url: str = ""
    version: str = "1.0.0"
    include_skills: list[str] | None = None


class BatchExportRequest(BaseModel):
    profile_ids: list[str]
    author_name: str = ""
    version: str = "1.0.0"


@router.post("/api/agents/package/export")
async def export_agent(req: ExportRequest):
    """Export an agent profile as a .akita-agent package."""
    from openakita.agents.packager import AgentPackager, PackageError

    profile_store, skills_dir, root = _get_stores()
    output_dir = root / "data" / "agent_packages"

    packager = AgentPackager(
        profile_store=profile_store,
        skills_dir=skills_dir,
        output_dir=output_dir,
    )

    try:
        output_path = packager.package(
            profile_id=req.profile_id,
            author_name=req.author_name,
            author_url=req.author_url,
            version=req.version,
            include_skills=req.include_skills,
        )
    except PackageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return FileResponse(
        path=str(output_path),
        media_type="application/x-akita-agent",
        filename=output_path.name,
    )


@router.post("/api/agents/package/batch-export")
async def batch_export_agents(req: BatchExportRequest):
    """Export multiple agents as a single .zip archive."""
    import zipfile
    from openakita.agents.packager import AgentPackager, PackageError

    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="profile_ids is required")
    if len(req.profile_ids) > 20:
        raise HTTPException(status_code=400, detail="最多同时导出 20 个 Agent")

    profile_store, skills_dir, root = _get_stores()
    output_dir = root / "data" / "agent_packages"
    output_dir.mkdir(parents=True, exist_ok=True)

    packager = AgentPackager(
        profile_store=profile_store,
        skills_dir=skills_dir,
        output_dir=output_dir,
    )

    exported: list[Path] = []
    errors: list[str] = []
    for pid in req.profile_ids:
        try:
            out = packager.package(
                profile_id=pid,
                author_name=req.author_name,
                version=req.version,
            )
            exported.append(out)
        except PackageError as e:
            errors.append(f"{pid}: {e}")

    if not exported:
        raise HTTPException(status_code=400, detail=f"没有可导出的 Agent: {'; '.join(errors)}")

    if len(exported) == 1:
        return FileResponse(
            path=str(exported[0]),
            media_type="application/x-akita-agent",
            filename=exported[0].name,
        )

    zip_path = output_dir / "batch_export.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in exported:
            zf.write(p, p.name)

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"agents_batch_{len(exported)}.zip",
    )


class ExportJsonRequest(BaseModel):
    profile_id: str
    version: str = "1.0.0"
    output_path: str = ""


@router.post("/api/agents/package/export-json")
async def export_agent_json(req: ExportJsonRequest):
    """Export agent profile as JSON. If output_path given, write to disk."""
    import json as _json

    profile_store, _, _ = _get_stores()
    profile = profile_store.get(req.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile not found: {req.profile_id}")
    data = profile.to_dict()
    data.pop("ephemeral", None)
    data.pop("inherit_from", None)
    export_data = {
        "format": "akita-agent",
        "version": req.version,
        "profile": data,
    }

    if req.output_path:
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(out)}

    from fastapi.responses import JSONResponse
    return JSONResponse(content=export_data)


class BatchExportJsonRequest(BaseModel):
    profile_ids: list[str]
    output_path: str = ""


@router.post("/api/agents/package/batch-export-json")
async def batch_export_agents_json(req: BatchExportJsonRequest):
    """Export multiple agent profiles as JSON. If output_path given, write to disk."""
    import json as _json

    if not req.profile_ids:
        raise HTTPException(status_code=400, detail="profile_ids is required")
    if len(req.profile_ids) > 50:
        raise HTTPException(status_code=400, detail="最多同时导出 50 个 Agent")

    profile_store, _, _ = _get_stores()
    exported = []
    errors = []
    for pid in req.profile_ids:
        profile = profile_store.get(pid)
        if profile is None:
            errors.append(pid)
            continue
        data = profile.to_dict()
        data.pop("ephemeral", None)
        data.pop("inherit_from", None)
        exported.append(data)

    result = {
        "format": "akita-agent-batch",
        "version": "1.0",
        "agents": exported,
        "errors": errors,
    }

    if req.output_path:
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(out)}

    from fastapi.responses import JSONResponse
    return JSONResponse(content=result)


@router.post("/api/agents/package/import")
async def import_agent(
    request: Request,
    file: UploadFile = File(...),
    force: bool = False,
):
    """Import an agent from .akita-agent (ZIP) or .json file."""
    import json as _json
    from openakita.agents.profile import AgentProfile

    profile_store, skills_dir, _ = _get_stores()
    content = await file.read()
    filename = file.filename or ""

    if filename.endswith(".json"):
        try:
            data = _json.loads(content)
        except (ValueError, UnicodeDecodeError) as e:
            raise HTTPException(400, f"无效的 JSON 文件: {e}")

        if data.get("format") == "akita-agent-batch":
            agents_data = data.get("agents", [])
        elif data.get("format") == "akita-agent":
            agents_data = [data.get("profile", {})]
        elif isinstance(data.get("profile"), dict):
            agents_data = [data["profile"]]
        else:
            raise HTTPException(400, "无法识别的 JSON 格式，缺少 profile 或 agents 字段")

        imported = []
        skipped = []
        for pdata in agents_data:
            if not pdata or not isinstance(pdata, dict):
                continue
            pid = pdata.get("id", "")
            pdata["type"] = "custom"
            for k in ("ephemeral", "inherit_from", "user_customized", "hidden"):
                pdata.pop(k, None)

            if profile_store.exists(pid) and not force:
                suffix = 1
                while profile_store.exists(f"{pid}-{suffix}"):
                    suffix += 1
                old_id = pid
                pid = f"{pid}-{suffix}"
                pdata["id"] = pid
                skipped.append(f"{old_id} → {pid}")

            profile = AgentProfile.from_dict(pdata)
            profile_store.save(profile)
            imported.append(profile.to_dict())

        _reload_skills(request)
        msg = f"导入成功: {len(imported)} 个 Agent"
        if skipped:
            msg += f"（{len(skipped)} 个 ID 冲突已重命名: {', '.join(skipped)}）"
        return {"message": msg, "profile": imported[0] if len(imported) == 1 else None, "imported": imported}

    from openakita.agents.packager import AgentInstaller, PackageError

    with tempfile.NamedTemporaryFile(
        suffix=".akita-agent", delete=False
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )
        profile = installer.install(tmp_path, force=force)
    except PackageError as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    _reload_skills(request)

    return {
        "message": "Agent imported successfully",
        "profile": profile.to_dict(),
    }


@router.post("/api/agents/package/inspect")
async def inspect_package(file: UploadFile = File(...)):
    """Preview the contents of an uploaded .akita-agent package."""
    from openakita.agents.packager import AgentInstaller, PackageError

    profile_store, skills_dir, _ = _get_stores()

    with tempfile.NamedTemporaryFile(
        suffix=".akita-agent", delete=False
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        installer = AgentInstaller(
            profile_store=profile_store,
            skills_dir=skills_dir,
        )
        info = installer.inspect(tmp_path)
    except PackageError as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return info


@router.get("/api/agents/package/exportable")
async def list_exportable():
    """List all agent profiles that can be exported."""
    profile_store, _, _ = _get_stores()
    profiles = profile_store.list_all(include_hidden=False)

    return {
        "agents": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "type": p.type.value,
                "icon": p.icon,
                "color": p.color,
                "category": p.category,
                "skills_count": len(p.skills) if p.skills else 0,
                "hub_source": p.hub_source,
            }
            for p in profiles
        ]
    }


# ---------------------------------------------------------------------------
# Hub proxy routes — forward requests to the OpenAkita Platform
# ---------------------------------------------------------------------------

def _get_hub_client():
    from openakita.hub import AgentHubClient
    return AgentHubClient()


def _get_skill_client():
    from openakita.hub import SkillStoreClient
    return SkillStoreClient()


@router.get("/api/hub/agents")
async def hub_search_agents(
    q: str = "",
    category: str = "",
    sort: str = "downloads",
    page: int = 1,
    limit: int = 20,
):
    """Proxy search to platform Agent Store."""
    client = _get_hub_client()
    try:
        result = await client.search(query=q, category=category, sort=sort, page=page, limit=limit)
        return result
    except Exception as e:
        logger.warning(f"Hub search agents unavailable (remote platform may be offline): {e}")
        raise HTTPException(
            status_code=502,
            detail="远程 Agent Store 暂不可用。本地 Agent 导入导出功能不受影响。",
        )
    finally:
        await client.close()


@router.get("/api/hub/agents/{agent_id}")
async def hub_agent_detail(agent_id: str):
    """Proxy Agent detail from platform."""
    client = _get_hub_client()
    try:
        return await client.get_detail(agent_id)
    except Exception as e:
        logger.warning(f"Hub agent detail unavailable: {e}")
        raise HTTPException(status_code=502, detail="远程 Agent Store 暂不可用")
    finally:
        await client.close()


@router.post("/api/hub/agents/{agent_id}/install")
async def hub_install_agent(request: Request, agent_id: str, force: bool = False):
    """Download agent from hub and install locally."""
    client = _get_hub_client()
    try:
        package_path = await client.download(agent_id)
    except Exception as e:
        logger.warning(f"Hub download unavailable: {e}")
        raise HTTPException(status_code=502, detail="远程 Agent Store 暂不可用，无法下载。可通过 .akita-agent 文件本地导入。")
    finally:
        await client.close()

    from openakita.agents.packager import AgentInstaller, PackageError

    profile_store, skills_dir, _ = _get_stores()
    installer = AgentInstaller(profile_store=profile_store, skills_dir=skills_dir)

    try:
        profile = installer.install(package_path, force=force)
    except PackageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if profile.hub_source is None:
        profile.hub_source = {}
    profile.hub_source.update({
        "platform": "openakita",
        "agent_id": agent_id,
        "installed_at": datetime.now().isoformat(),
    })
    profile_store.save(profile)

    _reload_skills(request)

    return {
        "message": "Agent installed from Hub",
        "profile": profile.to_dict(),
    }


@router.get("/api/hub/skills")
async def hub_search_skills(
    q: str = "",
    category: str = "",
    trust_level: str = "",
    sort: str = "installs",
    page: int = 1,
    limit: int = 20,
):
    """Proxy search to platform Skill Store."""
    client = _get_skill_client()
    try:
        result = await client.search(
            query=q, category=category, trust_level=trust_level,
            sort=sort, page=page, limit=limit,
        )
        return result
    except Exception as e:
        logger.warning(f"Hub search skills unavailable (remote platform may be offline): {e}")
        raise HTTPException(
            status_code=502,
            detail="远程 Skill Store 暂不可用。本地技能管理和 skills.sh 市场不受影响。",
        )
    finally:
        await client.close()


@router.get("/api/hub/skills/{skill_id}")
async def hub_skill_detail(skill_id: str):
    """Proxy Skill detail from platform."""
    client = _get_skill_client()
    try:
        return await client.get_detail(skill_id)
    except Exception as e:
        logger.warning(f"Hub skill detail unavailable: {e}")
        raise HTTPException(status_code=502, detail="远程 Skill Store 暂不可用")
    finally:
        await client.close()


@router.post("/api/hub/skills/{skill_id}/install")
async def hub_install_skill(request: Request, skill_id: str):
    """Get skill info from platform and install locally."""
    client = _get_skill_client()
    try:
        detail = await client.get_detail(skill_id)
    except Exception as e:
        logger.warning(f"Hub skill install - cannot reach platform: {e}")
        raise HTTPException(
            status_code=502,
            detail="远程 Skill Store 暂不可用。可在「技能管理 → 浏览市场」通过 skills.sh 安装，或使用 install_skill 从 GitHub 安装。",
        )

    skill = detail.get("skill", detail)
    install_url = skill.get("installUrl", "")
    if not install_url:
        await client.close()
        raise HTTPException(status_code=400, detail="该 Skill 没有安装地址")

    try:
        skill_dir = await client.install_skill(install_url, skill_id=skill_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"安装失败: {e}")
    finally:
        await client.close()

    _reload_skills(request)

    return {
        "message": "Skill installed from Store",
        "skill_name": skill.get("name", skill_id),
        "skill_dir": str(skill_dir),
        "trust_level": skill.get("trustLevel", "community"),
    }
