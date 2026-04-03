"""Agent profile API routes."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

_deleting_bot_ids: set[str] = set()
_deleting_lock = asyncio.Lock()

# Valid IM bot types
VALID_BOT_TYPES = frozenset({"feishu", "telegram", "dingtalk", "wework", "wework_ws", "onebot", "onebot_reverse", "qqbot", "wechat"})


def _invalidate_bot_agent_sessions(bot_cfg: dict) -> None:
    """Clear ``_bot_default_agent`` metadata on sessions belonging to *bot_cfg*.

    This forces ``_apply_bot_agent_profile`` to re-apply the (new)
    ``agent_profile_id`` on the next message for each affected session.
    """
    try:
        from openakita.main import get_message_gateway, _bot_channel_name
        gw = get_message_gateway()
        if gw is None or gw.session_manager is None:
            return
        channel_name = _bot_channel_name(bot_cfg)
        sessions = gw.session_manager.list_sessions(channel=channel_name)
        cleared = 0
        for s in sessions:
            if s.get_metadata("_bot_default_agent") is not None:
                s.metadata.pop("_bot_default_agent", None)
                cleared += 1
        if cleared:
            gw.session_manager.mark_dirty()
            logger.info(f"[Agents API] Cleared _bot_default_agent on {cleared} sessions for {channel_name}")
    except Exception as e:
        logger.warning(f"[Agents API] Failed to invalidate bot agent sessions: {e}")


def _invalidate_profile_agents(request: Request, profile_id: str) -> None:
    """Drop pooled Agent instances for *profile_id* so edits apply next turn."""
    for pool_attr in ("agent_pool", "orchestrator"):
        obj = getattr(request.app.state, pool_attr, None)
        if obj is None:
            continue
        pool = getattr(obj, "_pool", obj)
        if hasattr(pool, "invalidate_profile"):
            try:
                pool.invalidate_profile(profile_id)
            except Exception as e:
                logger.warning(
                    f"[Agents API] Failed to invalidate profile pool "
                    f"({pool_attr}, profile={profile_id}): {e}"
                )


# ─── Pydantic models ─────────────────────────────────────────────────────


class BotCreateRequest(BaseModel):
    id: str = Field(..., min_length=1)
    type: str = Field(...)
    name: str = Field("")
    agent_profile_id: str = Field("default")
    enabled: bool = Field(True)
    credentials: dict = Field(default_factory=dict)


class BotUpdateRequest(BaseModel):
    type: str | None = None
    name: str | None = None
    agent_profile_id: str | None = None
    enabled: bool | None = None
    credentials: dict | None = None


class BotToggleRequest(BaseModel):
    enabled: bool


class ProfileCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    icon: str = Field("🤖", max_length=4)
    color: str = Field("#6b7280", max_length=20)
    skills: list[str] = Field(default_factory=list)
    skills_mode: str = Field("all")
    tools: list[str] = Field(default_factory=list)
    tools_mode: str = Field("all")
    mcp_servers: list[str] = Field(default_factory=list)
    mcp_mode: str = Field("all")
    plugins: list[str] = Field(default_factory=list)
    plugins_mode: str = Field("all")
    custom_prompt: str = Field("", max_length=5000)
    category: str = Field("", max_length=30)
    preferred_endpoint: str | None = Field(None, max_length=200)
    identity_mode: Literal["shared", "custom"] = "shared"
    memory_mode: Literal["shared", "isolated"] = "shared"
    memory_inherit_global: bool = True


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    icon: str | None = Field(None, max_length=4)
    color: str | None = Field(None, max_length=20)
    skills: list[str] | None = None
    skills_mode: str | None = None
    tools: list[str] | None = None
    tools_mode: str | None = None
    mcp_servers: list[str] | None = None
    mcp_mode: str | None = None
    plugins: list[str] | None = None
    plugins_mode: str | None = None
    custom_prompt: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=30)
    preferred_endpoint: str | None = Field(None, max_length=200)
    identity_mode: Literal["shared", "custom"] | None = None
    memory_mode: Literal["shared", "isolated"] | None = None
    memory_inherit_global: bool | None = None


class ProfileVisibilityRequest(BaseModel):
    hidden: bool


# ─── Bot CRUD routes ─────────────────────────────────────────────────────


@router.get("/api/agents/bots")
async def list_bots():
    """List all configured bots from settings.im_bots."""
    from openakita.config import settings

    return {"bots": list(settings.im_bots)}


@router.post("/api/agents/bots")
async def create_bot(body: BotCreateRequest):
    """Add a new bot. Validates id uniqueness and type."""
    from openakita.config import runtime_state, settings

    if body.id.strip() == "":
        raise HTTPException(status_code=400, detail="id must be non-empty")
    if body.type not in VALID_BOT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of: {', '.join(sorted(VALID_BOT_TYPES))}",
        )
    if not isinstance(body.credentials, dict):
        raise HTTPException(status_code=400, detail="credentials must be a dict")

    existing_ids = {b.get("id") for b in settings.im_bots if isinstance(b, dict)}
    if body.id in existing_ids:
        raise HTTPException(status_code=400, detail=f"bot id '{body.id}' already exists")

    bot = {
        "id": body.id,
        "type": body.type,
        "name": body.name,
        "agent_profile_id": body.agent_profile_id,
        "enabled": body.enabled,
        "credentials": body.credentials,
    }
    settings.im_bots = list(settings.im_bots) + [bot]
    runtime_state.save()
    logger.info(f"[Agents API] Created bot: {body.id}")

    if bot.get("enabled", True):
        from openakita.main import apply_im_bot
        await apply_im_bot(bot)

    return {"status": "ok", "bot": bot}


@router.put("/api/agents/bots/{bot_id}")
async def update_bot(bot_id: str, body: BotUpdateRequest):
    """Update an existing bot. Partial update (only provided fields are changed)."""
    from openakita.config import runtime_state, settings

    bots = list(settings.im_bots)
    idx = next((i for i, b in enumerate(bots) if isinstance(b, dict) and b.get("id") == bot_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"bot '{bot_id}' not found")

    bot = dict(bots[idx])
    old_agent_profile = bot.get("agent_profile_id", "default")

    if body.type is not None:
        if body.type not in VALID_BOT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"type must be one of: {', '.join(sorted(VALID_BOT_TYPES))}",
            )
        bot["type"] = body.type
    if body.name is not None:
        bot["name"] = body.name
    if body.agent_profile_id is not None:
        bot["agent_profile_id"] = body.agent_profile_id
    if body.enabled is not None:
        bot["enabled"] = body.enabled
    if body.credentials is not None:
        bot["credentials"] = body.credentials

    bots[idx] = bot
    settings.im_bots = bots
    runtime_state.save()
    logger.info(f"[Agents API] Updated bot: {bot_id}")

    new_agent_profile = bot.get("agent_profile_id", "default")
    if new_agent_profile != old_agent_profile:
        _invalidate_bot_agent_sessions(bot)

    from openakita.main import apply_im_bot, remove_im_bot
    if bot.get("enabled", True):
        await apply_im_bot(bot)
    else:
        await remove_im_bot(bot)

    return {"status": "ok", "bot": bot}


@router.delete("/api/agents/bots/{bot_id}")
async def delete_bot(bot_id: str):
    """Remove a bot (idempotent & mutex-protected)."""
    async with _deleting_lock:
        if bot_id in _deleting_bot_ids:
            return {"status": "ok", "detail": "already deleting"}
        _deleting_bot_ids.add(bot_id)

    try:
        from openakita.config import runtime_state, settings

        bots = list(settings.im_bots)
        deleted = [b for b in bots if isinstance(b, dict) and b.get("id") == bot_id]
        new_bots = [b for b in bots if isinstance(b, dict) and b.get("id") != bot_id]
        if len(new_bots) == len(bots):
            return {"status": "ok", "detail": "bot already removed"}

        settings.im_bots = new_bots
        runtime_state.save()
        logger.info(f"[Agents API] Deleted bot: {bot_id}")

        if deleted:
            from openakita.main import remove_im_bot, _bot_channel_name, get_message_gateway
            await remove_im_bot(deleted[0])

            channel_name = _bot_channel_name(deleted[0])
            gw = get_message_gateway()
            if gw and gw.session_manager:
                purged = gw.session_manager.purge_channel(channel_name)
                if purged:
                    logger.info(
                        f"[Agents API] Purged {purged} sessions for deleted bot: "
                        f"{channel_name}"
                    )

        return {"status": "ok"}
    finally:
        async with _deleting_lock:
            _deleting_bot_ids.discard(bot_id)


@router.post("/api/agents/bots/{bot_id}/toggle")
async def toggle_bot(bot_id: str, body: BotToggleRequest):
    """Enable or disable a bot."""
    from openakita.config import runtime_state, settings

    bots = list(settings.im_bots)
    idx = next((i for i, b in enumerate(bots) if isinstance(b, dict) and b.get("id") == bot_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"bot '{bot_id}' not found")

    bot = dict(bots[idx])
    bot["enabled"] = body.enabled
    bots[idx] = bot
    settings.im_bots = bots
    runtime_state.save()
    logger.info(f"[Agents API] Toggled bot {bot_id}: enabled={body.enabled}")

    from openakita.main import apply_im_bot, remove_im_bot
    if body.enabled:
        await apply_im_bot(bot)
    else:
        await remove_im_bot(bot)

    return {"status": "ok", "bot": bot}


# ─── Agent category routes ───────────────────────────────────────────────


class CategoryCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=30, pattern=r"^[a-z0-9_-]+$")
    label: str = Field(..., min_length=1, max_length=30)
    color: str = Field("#6b7280", max_length=20)


@router.get("/api/agents/categories")
async def list_categories():
    """Return all agent categories (builtin + custom) with agent counts."""
    from openakita.agents.profile import get_profile_store

    store = get_profile_store()
    return {"categories": store.list_categories()}


@router.post("/api/agents/categories")
async def create_category(body: CategoryCreateRequest):
    """Create a custom agent category."""
    from openakita.agents.profile import get_profile_store

    store = get_profile_store()
    try:
        cat = store.add_category(body.id, body.label, body.color)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"[Agents API] Created category: {body.id}")
    return {"status": "ok", "category": cat}


@router.delete("/api/agents/categories/{category_id}")
async def delete_category(category_id: str):
    """Delete a custom agent category. Rejects if builtin or has agents."""
    from openakita.agents.profile import get_profile_store

    store = get_profile_store()
    try:
        removed = store.remove_category(category_id)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if not removed:
        raise HTTPException(status_code=404, detail=f"分类 '{category_id}' 不存在")

    logger.info(f"[Agents API] Deleted category: {category_id}")
    return {"status": "ok"}


# ─── Agent profile routes ───────────────────────────────────────────────


@router.get("/api/agents/profiles")
async def list_agent_profiles(include_hidden: bool = False):
    """Return available agent profiles (system presets + user-created).

    Query params:
        include_hidden: if True, also return hidden profiles (default False).
    """
    from openakita.agents.presets import SYSTEM_PRESETS
    from openakita.agents.profile import get_profile_store
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        return {"profiles": [], "multi_agent_enabled": False}

    store = get_profile_store()
    stored_map = {p.id: p for p in store.list_all(include_hidden=True)}

    preset_order = [p.id for p in SYSTEM_PRESETS]
    seen_ids: set[str] = set()
    profiles = []

    for pid in preset_order:
        seen_ids.add(pid)
        p = stored_map.get(pid)
        if p is None:
            preset = next((x for x in SYSTEM_PRESETS if x.id == pid), None)
            if preset is None:
                continue
            p = preset
        if not include_hidden and p.hidden:
            continue
        profiles.append(p.to_dict())

    for p in store.list_all(include_hidden=True):
        if p.id not in seen_ids:
            if not include_hidden and p.hidden:
                continue
            profiles.append(p.to_dict())

    return {"profiles": profiles, "multi_agent_enabled": True}


@router.post("/api/agents/profiles")
async def create_agent_profile(body: ProfileCreateRequest):
    """Create a new custom agent profile."""
    from openakita.agents.profile import AgentProfile, AgentType, SkillsMode, get_profile_store
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    valid_modes = {"all", "inclusive", "exclusive"}
    if body.skills_mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"skills_mode must be one of: {', '.join(valid_modes)}")

    store = get_profile_store()

    if store.exists(body.id):
        raise HTTPException(status_code=400, detail=f"Profile '{body.id}' already exists")

    valid_mode_values = {"all", "inclusive", "exclusive"}
    for field_name in ("tools_mode", "mcp_mode", "plugins_mode"):
        val = getattr(body, field_name)
        if val not in valid_mode_values:
            raise HTTPException(
                status_code=400,
                detail=f"{field_name} must be one of: {', '.join(valid_mode_values)}",
            )

    profile = AgentProfile(
        id=body.id,
        name=body.name,
        description=body.description,
        type=AgentType.CUSTOM,
        skills=body.skills,
        skills_mode=SkillsMode(body.skills_mode),
        tools=body.tools,
        tools_mode=body.tools_mode,
        mcp_servers=body.mcp_servers,
        mcp_mode=body.mcp_mode,
        plugins=body.plugins,
        plugins_mode=body.plugins_mode,
        custom_prompt=body.custom_prompt,
        icon=body.icon,
        color=body.color,
        category=body.category,
        preferred_endpoint=body.preferred_endpoint,
        identity_mode=body.identity_mode,
        memory_mode=body.memory_mode,
        memory_inherit_global=body.memory_inherit_global,
        created_by="user",
    )

    store.save(profile)
    logger.info(f"[Agents API] Created profile: {body.id}")
    return {"status": "ok", "profile": profile.to_dict()}


@router.put("/api/agents/profiles/{profile_id}")
async def update_agent_profile(profile_id: str, body: ProfileUpdateRequest, request: Request):
    """Update a custom agent profile (system profiles have restricted updates)."""
    from openakita.agents.profile import get_profile_store
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    if body.skills_mode is not None:
        valid_modes = {"all", "inclusive", "exclusive"}
        if body.skills_mode not in valid_modes:
            raise HTTPException(status_code=400, detail=f"skills_mode must be one of: {', '.join(valid_modes)}")

    store = get_profile_store()
    update_data = body.model_dump(exclude_unset=True)

    try:
        updated = store.update(profile_id, update_data)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    _invalidate_profile_agents(request, profile_id)
    logger.info(f"[Agents API] Updated profile: {profile_id}")
    return {"status": "ok", "profile": updated.to_dict()}


@router.delete("/api/agents/profiles/{profile_id}")
async def delete_agent_profile(profile_id: str):
    """Delete a custom agent profile."""
    from openakita.agents.profile import get_profile_store
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    store = get_profile_store()

    try:
        deleted = store.delete(profile_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    logger.info(f"[Agents API] Deleted profile: {profile_id}")
    return {"status": "ok"}


@router.post("/api/agents/profiles/{profile_id}/reset")
async def reset_agent_profile(profile_id: str, request: Request):
    """Reset a system agent profile to its factory defaults."""
    from openakita.agents.presets import get_preset_by_id
    from openakita.agents.profile import get_profile_store
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    preset = get_preset_by_id(profile_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"No system preset found for '{profile_id}'")

    store = get_profile_store()
    existing = store.get(profile_id)
    if existing is None:
        store.save(preset)
    else:
        reset_data = preset.to_dict()
        keep_fields = {"hidden"}
        for field in keep_fields:
            reset_data[field] = getattr(existing, field, getattr(preset, field))
        reset_data["user_customized"] = False
        from openakita.agents.profile import AgentProfile
        profile = AgentProfile.from_dict(reset_data)
        store._cache[profile_id] = profile
        store._persist(profile)

    _invalidate_profile_agents(request, profile_id)
    logger.info(f"[Agents API] Reset profile to defaults: {profile_id}")
    result = store.get(profile_id)
    return {"status": "ok", "profile": result.to_dict() if result else {}}


@router.patch("/api/agents/profiles/{profile_id}/visibility")
async def update_profile_visibility(profile_id: str, body: ProfileVisibilityRequest):
    """Show or hide an agent profile (works for both SYSTEM and CUSTOM)."""
    from openakita.agents.profile import get_profile_store
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    store = get_profile_store()
    try:
        updated = store.update(profile_id, {"hidden": body.hidden})
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    logger.info(f"[Agents API] Visibility updated: {profile_id} hidden={body.hidden}")
    return {"status": "ok", "profile": updated.to_dict()}


# ─── Profile identity & memory isolation routes ─────────────────────────


class IdentityFileRequest(BaseModel):
    content: str = ""


_ALLOWED_IDENTITY_FILES = frozenset({"SOUL.md", "AGENT.md", "USER.md", "MEMORY.md"})


@router.post("/api/agents/profiles/{profile_id}/identity/init")
async def init_profile_identity(profile_id: str):
    """Initialize the profile-specific identity directory."""
    from openakita.agents.profile import get_profile_store

    store = get_profile_store()
    profile = store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    profile_dir = store.ensure_profile_dir(profile_id)
    identity_dir = profile_dir / "identity"

    from openakita.agents.identity_resolver import ProfileIdentityResolver
    from openakita.config import settings

    resolver = ProfileIdentityResolver(identity_dir, settings.identity_path)
    resolver.ensure_independent_files()

    return {"status": "ok", "identity_dir": str(identity_dir)}


@router.get("/api/agents/profiles/{profile_id}/identity/{filename}")
async def read_profile_identity_file(profile_id: str, filename: str):
    """Read a profile-specific identity file. Returns global content if profile has none."""
    if filename not in _ALLOWED_IDENTITY_FILES:
        raise HTTPException(status_code=400, detail=f"Invalid identity file: {filename}")

    from openakita.agents.profile import get_profile_store
    from openakita.config import settings

    store = get_profile_store()
    profile = store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    profile_dir = store.get_profile_dir(profile_id)
    profile_path = profile_dir / "identity" / filename
    global_path = settings.identity_path / filename

    content = ""
    source = "global"
    if profile_path.exists():
        content = profile_path.read_text(encoding="utf-8")
        source = "profile"
    elif global_path.exists():
        content = global_path.read_text(encoding="utf-8")
        source = "global"

    return {"content": content, "source": source, "filename": filename}


@router.put("/api/agents/profiles/{profile_id}/identity/{filename}")
async def write_profile_identity_file(profile_id: str, filename: str, body: IdentityFileRequest):
    """Write a profile-specific identity file."""
    if filename not in _ALLOWED_IDENTITY_FILES:
        raise HTTPException(status_code=400, detail=f"Invalid identity file: {filename}")

    from openakita.agents.profile import get_profile_store

    store = get_profile_store()
    profile = store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    profile_dir = store.ensure_profile_dir(profile_id)
    identity_dir = profile_dir / "identity"
    identity_dir.mkdir(parents=True, exist_ok=True)

    fp = identity_dir / filename
    fp.write_text(body.content, encoding="utf-8")

    logger.info(f"[Agents API] Wrote identity file {filename} for profile {profile_id}")
    return {"status": "ok", "filename": filename}


@router.get("/api/agents/profiles/{profile_id}/memory/stats")
async def get_profile_memory_stats(profile_id: str):
    """Get memory statistics for an isolated profile."""
    from openakita.agents.profile import get_profile_store

    store = get_profile_store()
    profile = store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    profile_dir = store.get_profile_dir(profile_id)
    memory_dir = profile_dir / "memory"
    db_path = memory_dir / "openakita.db"

    if not db_path.exists():
        return {"exists": False, "semantic_count": 0, "db_size_bytes": 0}

    import aiosqlite
    semantic_count = 0
    try:
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM semantic_memories"
            )
            row = await cursor.fetchone()
            semantic_count = row[0] if row else 0
    except Exception:
        pass

    return {
        "exists": True,
        "semantic_count": semantic_count,
        "db_size_bytes": db_path.stat().st_size,
    }


@router.delete("/api/agents/profiles/{profile_id}/data")
async def delete_profile_data(profile_id: str, request: Request):
    """Delete the profile-specific data directory (identity + memory)."""
    import shutil

    from openakita.agents.profile import get_profile_store

    store = get_profile_store()
    profile = store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    profile_dir = store.get_profile_dir(profile_id)
    if profile_dir.is_dir():
        shutil.rmtree(profile_dir, ignore_errors=True)

    store.update(
        profile_id,
        {
            "identity_mode": "shared",
            "memory_mode": "shared",
            "memory_inherit_global": True,
        },
    )

    _invalidate_profile_agents(request, profile_id)
    logger.info(f"[Agents API] Deleted profile data dir for {profile_id}")
    return {"status": "ok"}


@router.get("/api/agents/health")
async def get_agent_health():
    """Get health metrics from the orchestrator."""
    try:
        from openakita.main import _orchestrator
        if _orchestrator:
            return {"health": _orchestrator.get_health_stats()}
    except Exception:
        pass
    return {"health": {}}


@router.get("/api/agents/topology")
async def get_topology(request: Request):
    """Aggregated topology: pool entries + sub-agent states + delegation edges + stats.

    Single endpoint for the neural-network dashboard to poll.
    """
    from openakita.agents.presets import SYSTEM_PRESETS
    from openakita.agents.profile import get_profile_store
    from openakita.config import settings

    pool = getattr(request.app.state, "agent_pool", None)
    session_manager = getattr(request.app.state, "session_manager", None)

    # Always prefer the module-level _orchestrator — AgentToolHandler writes
    # sub-agent states to this instance, so we must read from the same one.
    orchestrator = None
    try:
        from openakita.main import _orchestrator
        orchestrator = _orchestrator
    except (ImportError, AttributeError):
        pass
    if orchestrator is None:
        orchestrator = getattr(request.app.state, "orchestrator", None)

    profile_map: dict[str, dict] = {}
    hidden_profile_ids: set[str] = set()
    stored_profiles: dict[str, object] = {}
    try:
        store = get_profile_store()
        stored_profiles = {p.id: p for p in store.list_all(include_hidden=True)}
    except Exception:
        pass
    for p in SYSTEM_PRESETS:
        sp = stored_profiles.get(p.id)
        if sp and getattr(sp, "hidden", False):
            hidden_profile_ids.add(p.id)
        if sp:
            profile_map[p.id] = {
                "name": getattr(sp, "name", None) or p.name,
                "icon": getattr(sp, "icon", None) or p.icon or "🤖",
                "color": getattr(sp, "color", None) or p.color or "#6b7280",
            }
        else:
            profile_map[p.id] = {"name": p.name, "icon": p.icon or "🤖", "color": p.color or "#6b7280"}
    for pid, p in stored_profiles.items():
        if getattr(p, "hidden", False):
            hidden_profile_ids.add(pid)
        if pid not in profile_map:
            profile_map[pid] = {"name": p.name, "icon": p.icon or "🤖", "color": getattr(p, "color", None) or "#6b7280"}

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    if pool is not None:
        stats = pool.get_stats()
        for entry in stats.get("sessions", []):
            sid = entry["session_id"]
            agents_in_session = entry.get("agents", [{"profile_id": entry.get("profile_id", "default")}])

            for agent_info in agents_in_session:
                pid = agent_info["profile_id"]
                node_id = f"{sid}::{pid}" if len(agents_in_session) > 1 else sid
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)

                pinfo = profile_map.get(pid, {"name": pid, "icon": "🤖", "color": "#6b7280"})

                status = "idle"
                iteration = 0
                tools_executed: list[str] = []
                tools_total = 0
                elapsed_s = 0
                agent_inst = pool.get_existing(sid, profile_id=pid)
                if agent_inst is not None:
                    astate = getattr(agent_inst, "agent_state", None)
                    if astate:
                        task = astate.get_task_for_session(sid)
                        if task is None:
                            task = astate.current_task
                        if task and task.is_active:
                            status = "running"
                            iteration = task.iteration
                            tools_executed = list(task.tools_executed[-5:]) if task.tools_executed else []
                            tools_total = len(task.tools_executed)
                            if hasattr(task, "started_at") and task.started_at:
                                import time
                                elapsed_s = int(time.time() - task.started_at)

                conv_title = ""
                if session_manager:
                    try:
                        sess = session_manager.get_session("desktop", sid, "desktop_user", create_if_missing=False)
                        if sess and hasattr(sess, "context"):
                            msgs = sess.context.messages if hasattr(sess.context, "messages") else []
                            for m in msgs:
                                if m.get("role") == "user":
                                    conv_title = (m.get("content") or "")[:60]
                    except Exception:
                        pass

                nodes.append({
                    "id": node_id,
                    "profile_id": pid,
                    "name": pinfo["name"],
                    "icon": pinfo["icon"],
                    "color": pinfo["color"],
                    "status": status,
                    "is_sub_agent": False,
                    "parent_id": None,
                    "iteration": iteration,
                    "tools_executed": tools_executed,
                    "tools_total": tools_total,
                    "elapsed_s": elapsed_s,
                    "conversation_title": conv_title,
                })

    # Sub-agent states from orchestrator
    if orchestrator and pool:
        for entry in pool.get_stats().get("sessions", []):
            sid = entry["session_id"]
            try:
                sub_states = orchestrator.get_sub_agent_states(sid)
                for sub in sub_states:
                    sub_id = f"{sid}::{sub.get('profile_id', 'unknown')}"
                    if sub_id not in seen_ids:
                        seen_ids.add(sub_id)
                        sub_pid = sub.get("profile_id", "")
                        pinfo = profile_map.get(sub_pid, {"name": sub.get("name", sub_pid), "icon": sub.get("icon", "🤖"), "color": "#6b7280"})
                        sub_status = sub.get("status", "running")
                        if sub_status == "starting":
                            sub_status = "running"

                        _VALID_SUB_STATUSES = {
                            "running", "completed", "error", "idle",
                            "cancelled", "timeout", "interrupted",
                        }

                        from_agent = sub.get("from_agent", "")
                        parent_node_id = sid
                        if from_agent and f"{sid}::{from_agent}" in seen_ids:
                            parent_node_id = f"{sid}::{from_agent}"

                        nodes.append({
                            "id": sub_id,
                            "profile_id": sub_pid,
                            "name": sub.get("name", pinfo["name"]),
                            "icon": sub.get("icon", pinfo["icon"]),
                            "color": pinfo["color"],
                            "status": sub_status if sub_status in _VALID_SUB_STATUSES else "running",
                            "is_sub_agent": True,
                            "parent_id": parent_node_id,
                            "iteration": sub.get("iteration", 0),
                            "tools_executed": sub.get("tools_executed", [])[-5:],
                            "tools_total": sub.get("tools_total", 0),
                            "elapsed_s": sub.get("elapsed_s", 0),
                            "conversation_title": "",
                        })
                        edges.append({"from": parent_node_id, "to": sub_id, "type": "delegate"})
            except Exception as exc:
                logger.warning(f"[Topology] sub-agent states error for {sid}: {exc}")

    # Include sessions from session_manager that aren't in the pool
    # (e.g. conversations whose agent instances were reaped due to idle timeout).
    # Use chat_id as node ID to stay consistent with pool-based nodes (which use
    # conversation_id), ensuring the frontend sees stable node IDs.
    pool_session_ids = {n["id"].split("::")[0] for n in nodes if not n["id"].startswith("dormant::")}
    _MAX_IDLE_NODES = 3
    _IDLE_CUTOFF = datetime.now() - timedelta(minutes=30)
    _idle_added = 0
    if session_manager:
        try:
            desktop_sessions = session_manager.list_sessions(channel="desktop")
            desktop_sessions.sort(key=lambda s: s.last_active, reverse=True)
            for sess in desktop_sessions:
                if _idle_added >= _MAX_IDLE_NODES:
                    break
                if hasattr(sess, "last_active") and sess.last_active < _IDLE_CUTOFF:
                    break
                try:
                    sid = sess.chat_id if hasattr(sess, "chat_id") else sess.id
                    if sid in pool_session_ids or sid in seen_ids:
                        continue
                    pid = getattr(sess.context, "agent_profile_id", "default") or "default"
                    pinfo = profile_map.get(pid, {"name": pid, "icon": "🤖", "color": "#6b7280"})

                    conv_title = ""
                    msgs = getattr(sess.context, "messages", None) or []
                    for m in msgs:
                        if isinstance(m, dict) and m.get("role") == "user":
                            conv_title = (m.get("content") or "")[:60]

                    seen_ids.add(sid)
                    nodes.append({
                        "id": sid,
                        "profile_id": pid,
                        "name": pinfo["name"],
                        "icon": pinfo["icon"],
                        "color": pinfo["color"],
                        "status": "idle",
                        "is_sub_agent": False,
                        "parent_id": None,
                        "iteration": 0,
                        "tools_executed": [],
                        "tools_total": 0,
                        "elapsed_s": 0,
                        "conversation_title": conv_title,
                    })
                    _idle_added += 1
                except Exception as exc:
                    logger.debug(f"[Topology] skip session {getattr(sess, 'chat_id', '?')}: {exc}")
        except Exception as exc:
            logger.warning(f"[Topology] session_manager fallback error: {exc}")

    # Always include system presets as dormant neurons when not active (skip hidden)
    active_profile_ids = {n["profile_id"] for n in nodes}
    for pid, pinfo in profile_map.items():
        if pid in hidden_profile_ids:
            continue
        if pid not in active_profile_ids:
            dormant_id = f"dormant::{pid}"
            if dormant_id not in seen_ids:
                seen_ids.add(dormant_id)
                nodes.append({
                    "id": dormant_id,
                    "profile_id": pid,
                    "name": pinfo["name"],
                    "icon": pinfo["icon"],
                    "color": pinfo["color"],
                    "status": "dormant",
                    "is_sub_agent": False,
                    "parent_id": None,
                    "iteration": 0,
                    "tools_executed": [],
                    "tools_total": 0,
                    "elapsed_s": 0,
                    "conversation_title": "",
                })

    # Aggregate stats
    total_req = 0
    successful = 0
    failed = 0
    avg_latency = 0.0
    if orchestrator:
        try:
            health = orchestrator.get_health_stats()
            for h in health.values():
                total_req += h.get("total_requests", 0)
                successful += h.get("successful", 0)
                failed += h.get("failed", 0)
                avg_latency += h.get("avg_latency_ms", 0)
            if health:
                avg_latency /= len(health)
        except Exception:
            pass

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_requests": total_req,
            "successful": successful,
            "failed": failed,
            "avg_latency_ms": round(avg_latency, 1),
        },
    }


@router.get("/api/agents/collaboration/{session_id}")
async def get_collaboration_info(session_id: str, request: Request):
    """Get collaboration info for a session (active_agents, delegation_chain)."""
    session_manager = getattr(request.app.state, "session_manager", None)
    if not session_manager:
        raise HTTPException(status_code=503, detail="Session manager not available")

    session = session_manager.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    ctx = session.context
    active_agents = getattr(ctx, "active_agents", [])
    delegation_chain = getattr(ctx, "delegation_chain", [])

    return {
        "session_id": session_id,
        "active_agents": active_agents,
        "delegation_chain": delegation_chain,
    }
