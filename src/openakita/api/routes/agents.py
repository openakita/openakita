"""Agent profile API routes."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

# Valid IM bot types
VALID_BOT_TYPES = frozenset({"feishu", "telegram", "dingtalk", "wework", "wework_ws", "onebot", "onebot_reverse", "qqbot"})


def _bot_channel_name(bot: dict) -> str:
    """Derive the channel_name for a bot config dict."""
    bot_type = bot.get("type", "")
    bot_id = bot.get("id", "")
    return f"{bot_type}:{bot_id}" if bot_id else bot_type


async def _hot_register_bot(request: Request, bot: dict) -> None:
    """Create an adapter and register it in the running gateway (if available)."""
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        logger.info("[Agents API] No running gateway, bot will activate on next restart")
        return
    try:
        from openakita.main import _create_bot_adapter

        channel_name = _bot_channel_name(bot)
        bot_id = bot.get("id", "")
        agent_id = bot.get("agent_profile_id", "default")
        adapter = _create_bot_adapter(
            bot.get("type", ""), bot.get("credentials", {}),
            channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_id,
        )
        if adapter:
            from openakita.core.engine_bridge import to_engine

            await to_engine(gateway.register_adapter(adapter))
            logger.info(f"[Agents API] Hot-registered adapter: {channel_name}")
    except Exception as e:
        logger.warning(f"[Agents API] Hot-register failed (will activate on restart): {e}")


async def _hot_unregister_bot(request: Request, bot: dict) -> None:
    """Stop and remove an adapter from the running gateway."""
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        return
    channel_name = _bot_channel_name(bot)
    adapters = getattr(gateway, "_adapters", {})
    adapter = adapters.pop(channel_name, None)
    if adapter:
        try:
            from openakita.core.engine_bridge import to_engine

            await to_engine(adapter.stop())
            logger.info(f"[Agents API] Hot-unregistered adapter: {channel_name}")
        except Exception as e:
            logger.warning(f"[Agents API] Failed to stop adapter {channel_name}: {e}")


async def _hot_update_bot(request: Request, bot: dict) -> None:
    """Replace a running adapter with a new one (stop old → register new)."""
    await _hot_unregister_bot(request, bot)
    if bot.get("enabled", True):
        await _hot_register_bot(request, bot)


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
    custom_prompt: str = Field("", max_length=5000)
    category: str = Field("", max_length=30)


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    icon: str | None = Field(None, max_length=4)
    color: str | None = Field(None, max_length=20)
    skills: list[str] | None = None
    skills_mode: str | None = None
    custom_prompt: str | None = Field(None, max_length=5000)
    category: str | None = Field(None, max_length=30)


class ProfileVisibilityRequest(BaseModel):
    hidden: bool


# ─── Bot CRUD routes ─────────────────────────────────────────────────────


@router.get("/api/agents/bots")
async def list_bots():
    """List all configured bots from settings.im_bots."""
    from openakita.config import settings

    return {"bots": list(settings.im_bots)}


@router.post("/api/agents/bots")
async def create_bot(body: BotCreateRequest, request: Request):
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
async def update_bot(bot_id: str, body: BotUpdateRequest, request: Request):
    """Update an existing bot. Partial update (only provided fields are changed)."""
    from openakita.config import runtime_state, settings

    bots = list(settings.im_bots)
    idx = next((i for i, b in enumerate(bots) if isinstance(b, dict) and b.get("id") == bot_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"bot '{bot_id}' not found")

    bot = dict(bots[idx])
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

    from openakita.main import apply_im_bot, remove_im_bot
    if bot.get("enabled", True):
        await apply_im_bot(bot)
    else:
        await remove_im_bot(bot)

    return {"status": "ok", "bot": bot}


@router.delete("/api/agents/bots/{bot_id}")
async def delete_bot(bot_id: str, request: Request):
    """Remove a bot."""
    from openakita.config import runtime_state, settings

    bots = list(settings.im_bots)
    deleted = [b for b in bots if isinstance(b, dict) and b.get("id") == bot_id]
    new_bots = [b for b in bots if isinstance(b, dict) and b.get("id") != bot_id]
    if len(new_bots) == len(bots):
        raise HTTPException(status_code=404, detail=f"bot '{bot_id}' not found")

    settings.im_bots = new_bots
    runtime_state.save()
    logger.info(f"[Agents API] Deleted bot: {bot_id}")

    if deleted:
        from openakita.main import remove_im_bot
        await remove_im_bot(deleted[0])

    return {"status": "ok"}


@router.post("/api/agents/bots/{bot_id}/toggle")
async def toggle_bot(bot_id: str, body: BotToggleRequest, request: Request):
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


# ─── Env-bot introspection & migration ───────────────────────────────────


def _collect_env_bots() -> list[dict]:
    """Return a list of bots currently configured via .env (not in im_bots)."""
    from openakita.config import settings

    existing_types = {
        b.get("type") for b in settings.im_bots if isinstance(b, dict)
    }

    env_bots: list[dict] = []

    if settings.telegram_enabled and settings.telegram_bot_token:
        env_bots.append({
            "type": "telegram",
            "env_enabled": True,
            "migrated": "telegram" in existing_types,
            "credentials": {
                "bot_token": settings.telegram_bot_token,
                "webhook_url": settings.telegram_webhook_url or "",
                "proxy": settings.telegram_proxy or "",
                "pairing_code": settings.telegram_pairing_code or "",
                "require_pairing": str(settings.telegram_require_pairing).lower(),
            },
        })

    if settings.feishu_enabled and settings.feishu_app_id:
        env_bots.append({
            "type": "feishu",
            "env_enabled": True,
            "migrated": "feishu" in existing_types,
            "credentials": {
                "app_id": settings.feishu_app_id,
                "app_secret": settings.feishu_app_secret,
            },
        })

    if settings.wework_enabled and settings.wework_corp_id:
        env_bots.append({
            "type": "wework",
            "env_enabled": True,
            "migrated": "wework" in existing_types,
            "credentials": {
                "corp_id": settings.wework_corp_id,
                "token": settings.wework_token,
                "encoding_aes_key": settings.wework_encoding_aes_key,
                "callback_port": str(settings.wework_callback_port),
                "callback_host": settings.wework_callback_host,
            },
        })

    if settings.dingtalk_enabled and settings.dingtalk_client_id:
        env_bots.append({
            "type": "dingtalk",
            "env_enabled": True,
            "migrated": "dingtalk" in existing_types,
            "credentials": {
                "client_id": settings.dingtalk_client_id,
                "client_secret": settings.dingtalk_client_secret,
            },
        })

    if settings.onebot_enabled and settings.onebot_ws_url:
        env_bots.append({
            "type": "onebot",
            "env_enabled": True,
            "migrated": "onebot" in existing_types,
            "credentials": {
                "ws_url": settings.onebot_ws_url,
                "access_token": settings.onebot_access_token or "",
            },
        })

    if settings.qqbot_enabled and settings.qqbot_app_id:
        env_bots.append({
            "type": "qqbot",
            "env_enabled": True,
            "migrated": "qqbot" in existing_types,
            "credentials": {
                "app_id": settings.qqbot_app_id,
                "app_secret": settings.qqbot_app_secret,
                "sandbox": str(settings.qqbot_sandbox).lower(),
                "mode": settings.qqbot_mode,
                "webhook_port": str(settings.qqbot_webhook_port),
                "webhook_path": settings.qqbot_webhook_path,
            },
        })

    return env_bots


@router.get("/api/agents/env-bots")
async def list_env_bots():
    """List bots configured via .env that haven't been migrated to im_bots yet."""
    return {"env_bots": _collect_env_bots()}


BOT_TYPE_LABELS = {
    "telegram": "Telegram",
    "feishu": "飞书",
    "dingtalk": "钉钉",
    "wework": "企业微信",
    "onebot": "OneBot",
    "qqbot": "QQ Bot",
}


@router.post("/api/agents/bots/migrate-from-env")
async def migrate_env_bots(request: Request):
    """Migrate .env-configured bots into im_bots for unified management."""
    from openakita.config import runtime_state, settings

    env_bots = _collect_env_bots()
    migrated = []
    skipped = []

    for eb in env_bots:
        bot_type = eb["type"]
        if eb["migrated"]:
            skipped.append(bot_type)
            continue

        bot_id = f"{bot_type}-env"
        existing_ids = {b.get("id") for b in settings.im_bots if isinstance(b, dict)}
        suffix = 0
        final_id = bot_id
        while final_id in existing_ids:
            suffix += 1
            final_id = f"{bot_id}-{suffix}"

        bot = {
            "id": final_id,
            "type": bot_type,
            "name": BOT_TYPE_LABELS.get(bot_type, bot_type),
            "agent_profile_id": "default",
            "enabled": True,
            "credentials": eb["credentials"],
        }
        settings.im_bots = list(settings.im_bots) + [bot]
        migrated.append(bot)

        # Unregister old .env adapter (channel_name = bot_type) before
        # registering the new im_bots adapter to avoid duplicate adapters
        gateway = getattr(request.app.state, "gateway", None)
        if gateway:
            adapters = getattr(gateway, "_adapters", {})
            old_adapter = adapters.pop(bot_type, None)
            if old_adapter:
                try:
                    await old_adapter.stop()
                    logger.info(f"[Migration] Stopped old .env adapter: {bot_type}")
                except Exception as e:
                    logger.warning(f"[Migration] Failed to stop old adapter {bot_type}: {e}")

        if bot["enabled"]:
            await _hot_register_bot(request, bot)

    if migrated:
        runtime_state.save()
        logger.info(f"[Migration] Migrated {len(migrated)} env bots: "
                     f"{[b['id'] for b in migrated]}")

    return {
        "status": "ok",
        "migrated": migrated,
        "skipped": skipped,
    }


# ─── Agent category routes ───────────────────────────────────────────────


class CategoryCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=30, pattern=r"^[a-z0-9_-]+$")
    label: str = Field(..., min_length=1, max_length=30)
    color: str = Field("#6b7280", max_length=20)


@router.get("/api/agents/categories")
async def list_categories():
    """Return all agent categories (builtin + custom) with agent counts."""
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    store = ProfileStore(settings.data_dir / "agents")
    return {"categories": store.list_categories()}


@router.post("/api/agents/categories")
async def create_category(body: CategoryCreateRequest):
    """Create a custom agent category."""
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    store = ProfileStore(settings.data_dir / "agents")
    try:
        cat = store.add_category(body.id, body.label, body.color)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(f"[Agents API] Created category: {body.id}")
    return {"status": "ok", "category": cat}


@router.delete("/api/agents/categories/{category_id}")
async def delete_category(category_id: str):
    """Delete a custom agent category. Rejects if builtin or has agents."""
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    store = ProfileStore(settings.data_dir / "agents")
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
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        return {"profiles": [], "multi_agent_enabled": False}

    store = ProfileStore(settings.data_dir / "agents")
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
    from openakita.agents.profile import AgentProfile, AgentType, ProfileStore, SkillsMode
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    valid_modes = {"all", "inclusive", "exclusive"}
    if body.skills_mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"skills_mode must be one of: {', '.join(valid_modes)}")

    store = ProfileStore(settings.data_dir / "agents")

    if store.exists(body.id):
        raise HTTPException(status_code=400, detail=f"Profile '{body.id}' already exists")

    profile = AgentProfile(
        id=body.id,
        name=body.name,
        description=body.description,
        type=AgentType.CUSTOM,
        skills=body.skills,
        skills_mode=SkillsMode(body.skills_mode),
        custom_prompt=body.custom_prompt,
        icon=body.icon,
        color=body.color,
        category=body.category,
        created_by="user",
    )

    store.save(profile)
    logger.info(f"[Agents API] Created profile: {body.id}")
    return {"status": "ok", "profile": profile.to_dict()}


@router.put("/api/agents/profiles/{profile_id}")
async def update_agent_profile(profile_id: str, body: ProfileUpdateRequest):
    """Update a custom agent profile (system profiles have restricted updates)."""
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    if body.skills_mode is not None:
        valid_modes = {"all", "inclusive", "exclusive"}
        if body.skills_mode not in valid_modes:
            raise HTTPException(status_code=400, detail=f"skills_mode must be one of: {', '.join(valid_modes)}")

    store = ProfileStore(settings.data_dir / "agents")
    update_data = body.model_dump(exclude_none=True)

    try:
        updated = store.update(profile_id, update_data)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    logger.info(f"[Agents API] Updated profile: {profile_id}")
    return {"status": "ok", "profile": updated.to_dict()}


@router.delete("/api/agents/profiles/{profile_id}")
async def delete_agent_profile(profile_id: str):
    """Delete a custom agent profile."""
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    store = ProfileStore(settings.data_dir / "agents")

    try:
        deleted = store.delete(profile_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    logger.info(f"[Agents API] Deleted profile: {profile_id}")
    return {"status": "ok"}


@router.post("/api/agents/profiles/{profile_id}/reset")
async def reset_agent_profile(profile_id: str):
    """Reset a system agent profile to its factory defaults."""
    from openakita.agents.presets import get_preset_by_id
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    preset = get_preset_by_id(profile_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"No system preset found for '{profile_id}'")

    store = ProfileStore(settings.data_dir / "agents")
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

    logger.info(f"[Agents API] Reset profile to defaults: {profile_id}")
    result = store.get(profile_id)
    return {"status": "ok", "profile": result.to_dict() if result else {}}


@router.patch("/api/agents/profiles/{profile_id}/visibility")
async def update_profile_visibility(profile_id: str, body: ProfileVisibilityRequest):
    """Show or hide an agent profile (works for both SYSTEM and CUSTOM)."""
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    if not settings.multi_agent_enabled:
        raise HTTPException(status_code=400, detail="Multi-agent mode is not enabled")

    store = ProfileStore(settings.data_dir / "agents")
    try:
        updated = store.update(profile_id, {"hidden": body.hidden})
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    logger.info(f"[Agents API] Visibility updated: {profile_id} hidden={body.hidden}")
    return {"status": "ok", "profile": updated.to_dict()}


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
    from openakita.agents.profile import ProfileStore
    from openakita.config import settings

    pool = getattr(request.app.state, "agent_pool", None)
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        try:
            from openakita.main import _orchestrator
            orchestrator = _orchestrator
        except (ImportError, AttributeError):
            pass
    session_manager = getattr(request.app.state, "session_manager", None)

    profile_map: dict[str, dict] = {}
    for p in SYSTEM_PRESETS:
        profile_map[p.id] = {"name": p.name, "icon": p.icon or "🤖", "color": p.color or "#6b7280"}
    try:
        store = ProfileStore(settings.data_dir / "agents")
        for p in store.list_all():
            if p.id not in profile_map:
                profile_map[p.id] = {"name": p.name, "icon": p.icon or "🤖", "color": p.color or "#6b7280"}
    except Exception:
        pass

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
                        task = astate.get_task_for_session(sid) or astate.current_task
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
                            "status": sub_status if sub_status in ("running", "completed", "error", "idle") else "running",
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
                logger.debug(f"[Topology] sub-agent states error for {sid}: {exc}")

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

    # Always include system presets as dormant neurons when not active
    active_profile_ids = {n["profile_id"] for n in nodes}
    for pid, pinfo in profile_map.items():
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
