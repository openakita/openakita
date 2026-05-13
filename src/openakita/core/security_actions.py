"""Controlled service layer for security and skill allowlist actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


def list_security_allowlist() -> dict[str, Any]:
    """C8b-6a: v1 ``pe.get_user_allowlist()`` → v2 ``UserAllowlistManager.snapshot()``。"""
    from openakita.core.policy_v2.global_engine import get_engine_v2

    data = get_engine_v2().user_allowlist.snapshot()
    return {"status": "ok", "kind": "security_user_allowlist", **data}


def remove_security_allowlist_entry(entry_type: str = "command", index: int = -1) -> dict[str, Any]:
    """C8b-6a: v1 ``pe.remove_allowlist_entry()`` → v2 ``UserAllowlistManager.remove_entry()``
    + ``save_to_yaml()``（v1 自动写盘 → v2 显式 save，与 add_security_allowlist_entry 对齐）。
    """
    from openakita.core.policy_v2.global_engine import get_engine_v2

    entry_type = "tool" if entry_type == "tool" else "command"
    if index < 0:
        return {
            "status": "error",
            "kind": "security_user_allowlist",
            "message": "缺少有效索引",
        }
    manager = get_engine_v2().user_allowlist
    ok = manager.remove_entry(entry_type, index)
    if ok:
        manager.save_to_yaml()
    return {
        "status": "ok" if ok else "error",
        "kind": "security_user_allowlist",
        "entry_type": entry_type,
        "index": index,
        "message": "" if ok else "无效索引",
    }


def add_security_allowlist_entry(entry_type: str = "command", entry: dict[str, Any] | None = None) -> dict[str, Any]:
    """C8b-6a: v1 ``pe._config.user_allowlist.append() + pe._save_user_allowlist()``
    → v2 ``UserAllowlistManager.add_raw_entry() + save_to_yaml()``。
    """
    from openakita.core.policy_v2.global_engine import get_engine_v2

    entry_type = "tool" if entry_type == "tool" else "command"
    manager = get_engine_v2().user_allowlist
    manager.add_raw_entry(entry_type, dict(entry or {}))
    manager.save_to_yaml()
    return {"status": "ok", "kind": "security_user_allowlist", "entry_type": entry_type}


def reset_death_switch() -> dict[str, Any]:
    """C8b-6a: v1 ``pe.reset_readonly_mode()`` → v2 ``DeathSwitchTracker.reset()``。"""
    from openakita.core.policy_v2 import get_death_switch_tracker

    get_death_switch_tracker().reset()
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
    return {
        "status": "error",
        "kind": "controlled_action",
        "message": (
            "我没有识别到对应的受控执行入口。如果你想装技能，请直接告诉我"
            "技能的 URL 或本地路径（例如 `https://github.com/owner/repo` 或"
            " `path/to/SKILL.md`），我会自动调用 install_skill 工具完成安装；"
            "如果是想执行命令，请明确说明命令内容（例如 `运行: ls -la`），"
            "我会通过受控的 run_powershell / run_shell 工具执行。"
        ),
    }
