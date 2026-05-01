"""Controlled service layer for security and skill allowlist actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


def list_security_allowlist() -> dict[str, Any]:
    from openakita.core.policy import get_policy_engine

    data = get_policy_engine().get_user_allowlist()
    return {"status": "ok", "kind": "security_user_allowlist", **data}


def remove_security_allowlist_entry(entry_type: str = "command", index: int = -1) -> dict[str, Any]:
    from openakita.core.policy import get_policy_engine

    entry_type = "tool" if entry_type == "tool" else "command"
    if index < 0:
        return {
            "status": "error",
            "kind": "security_user_allowlist",
            "message": "缺少有效索引",
        }
    ok = get_policy_engine().remove_allowlist_entry(entry_type, index)
    return {
        "status": "ok" if ok else "error",
        "kind": "security_user_allowlist",
        "entry_type": entry_type,
        "index": index,
        "message": "" if ok else "无效索引",
    }


def add_security_allowlist_entry(entry_type: str = "command", entry: dict[str, Any] | None = None) -> dict[str, Any]:
    from openakita.core.policy import get_policy_engine

    entry_type = "tool" if entry_type == "tool" else "command"
    item = dict(entry or {})
    pe = get_policy_engine()
    al = pe._config.user_allowlist
    if entry_type == "command":
        al.commands.append(item)
    else:
        al.tools.append(item)
    pe._save_user_allowlist()
    return {"status": "ok", "kind": "security_user_allowlist", "entry_type": entry_type}


def reset_death_switch() -> dict[str, Any]:
    from openakita.core.policy import get_policy_engine

    get_policy_engine().reset_readonly_mode()
    return {"status": "ok", "kind": "security_death_switch", "readonly_mode": False}


def list_skill_external_allowlist() -> dict[str, Any]:
    from openakita.skills.allowlist_io import read_allowlist

    path, allowlist = read_allowlist()
    return {
        "status": "ok",
        "kind": "skill_external_allowlist",
        "path": str(path),
        "external_allowlist": sorted(allowlist) if allowlist is not None else None,
        "meaning": "None means all external skills are enabled unless hidden by skill metadata.",
    }


def set_skill_external_allowlist(skill_ids: list[str]) -> dict[str, Any]:
    from openakita.skills.allowlist_io import overwrite_allowlist

    cleaned = {str(item).strip() for item in skill_ids if str(item).strip()}
    overwrite_allowlist(cleaned)
    return {
        "status": "ok",
        "kind": "skill_external_allowlist",
        "external_allowlist": sorted(cleaned),
    }


async def maybe_broadcast_death_switch_reset(result: dict[str, Any]) -> None:
    if result.get("kind") != "security_death_switch" or result.get("status") != "ok":
        return
    try:
        from openakita.api.routes.websocket import broadcast_event

        await broadcast_event("security:death_switch", {"active": False})
    except Exception:
        pass


async def maybe_refresh_skills(result: dict[str, Any], get_agent: Callable[[], Any] | None = None) -> None:
    if result.get("kind") != "skill_external_allowlist" or result.get("status") != "ok":
        return
    if get_agent is None:
        return
    try:
        from openakita.core.agent import Agent
        from openakita.skills.events import SkillEvent

        agent = get_agent()
        actual_agent = agent if isinstance(agent, Agent) else getattr(agent, "_local_agent", None)
        if actual_agent is not None and hasattr(actual_agent, "propagate_skill_change"):
            await asyncio.to_thread(actual_agent.propagate_skill_change, SkillEvent.ENABLE, rescan=False)
    except Exception:
        pass


def execute_controlled_action(action: str | None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    params = parameters or {}
    if action == "list_security_allowlist":
        return list_security_allowlist()
    if action == "remove_security_allowlist_entry":
        return remove_security_allowlist_entry(
            entry_type=str(params.get("entry_type", "command")),
            index=int(params.get("index", -1)),
        )
    if action == "reset_death_switch":
        return reset_death_switch()
    if action == "list_skill_external_allowlist":
        return list_skill_external_allowlist()
    if action == "set_skill_external_allowlist":
        values = params.get("external_allowlist", params.get("skill_ids", []))
        return set_skill_external_allowlist(list(values) if isinstance(values, list) else [])
    return {"status": "error", "kind": "controlled_action", "message": "该操作尚无受控执行入口"}
