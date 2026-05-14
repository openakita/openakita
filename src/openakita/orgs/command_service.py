"""Shared organization command service.

This module is the single backend entry point for commands submitted to any
organization from the org console, desktop chat, or IM channels.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from openakita.core.engine_bridge import get_engine_loop
from openakita.orgs.models import OrgStatus

logger = logging.getLogger(__name__)

_CMD_TTL = 3600
_service_instance: OrgCommandService | None = None


class OrgCommandError(Exception):
    """Base error for organization command submission."""

    status_code = 400


class OrgCommandConflict(OrgCommandError):
    """Raised when a root node already has a running command."""

    status_code = 409

    def __init__(self, message: str, *, command_id: str) -> None:
        super().__init__(message)
        self.command_id = command_id


class OrgOutputScope(StrEnum):
    INTERNAL = "internal"
    CONSOLE_FULL = "console_full"
    CHAT_SUMMARY = "chat_summary"
    IM_SUMMARY = "im_summary"
    FINAL_ONLY = "final_only"


class OrgCommandSurface(StrEnum):
    ORG_CONSOLE = "org_console"
    DESKTOP_CHAT = "desktop_chat"
    IM = "im"


@dataclass(slots=True)
class OrgCommandSource:
    channel: str = "desktop"
    chat_id: str = ""
    user_id: str = "desktop_user"
    thread_id: str | None = None
    client_id: str = ""
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "client_id": self.client_id,
            "display_name": self.display_name,
        }


@dataclass(slots=True)
class OrgCommandRequest:
    org_id: str
    content: str
    target_node_id: str | None = None
    source: OrgCommandSource = field(default_factory=OrgCommandSource)
    origin_surface: OrgCommandSurface = OrgCommandSurface.ORG_CONSOLE
    output_scope: OrgOutputScope = OrgOutputScope.CONSOLE_FULL
    replace_existing: bool = False
    continue_previous: bool = False


def set_command_service(service: OrgCommandService | None) -> None:
    global _service_instance
    _service_instance = service


def get_command_service() -> OrgCommandService | None:
    return _service_instance


def default_scope_for_surface(
    surface: OrgCommandSurface,
    *,
    chat_type: str | None = None,
) -> OrgOutputScope:
    if surface == OrgCommandSurface.ORG_CONSOLE:
        return OrgOutputScope.CONSOLE_FULL
    if surface == OrgCommandSurface.DESKTOP_CHAT:
        return OrgOutputScope.CHAT_SUMMARY
    if surface == OrgCommandSurface.IM:
        return OrgOutputScope.FINAL_ONLY if chat_type == "group" else OrgOutputScope.IM_SUMMARY
    return OrgOutputScope.FINAL_ONLY


class OrgCommandService:
    """Submit, track, cancel, and observe commands for any organization."""

    def __init__(self, runtime: Any, session_manager: Any | None = None) -> None:
        self._runtime = runtime
        self._session_manager = session_manager
        self._commands: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._running_by_root: dict[tuple[str, str], str] = {}
        self._summary_subscribers: dict[
            str,
            list[tuple[asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop, str, str]],
        ] = {}

    @property
    def commands(self) -> dict[str, dict[str, Any]]:
        return self._commands

    def bridge_session_chat_id(self, org_id: str, target_node_id: str | None) -> str:
        return f"org_{org_id}_node_{target_node_id}" if target_node_id else f"org_{org_id}"

    def submit(self, request: OrgCommandRequest) -> dict[str, Any]:
        content = (request.content or "").strip()
        if not content:
            raise OrgCommandError("content is required")

        org = self._require_org_running(request.org_id)
        if request.target_node_id and not org.get_node(request.target_node_id):
            raise OrgCommandError(f"Node not found: {request.target_node_id}")
        root_node_id = self._resolve_command_root_id(org, request.target_node_id)
        if not root_node_id:
            raise OrgCommandError("Organization has no root nodes")

        self._purge_old_commands()
        command_id = uuid.uuid4().hex[:12]
        root_key = (request.org_id, root_node_id)
        now = time.time()
        run_content = content
        if request.continue_previous:
            run_content = self._build_continue_content(
                request.org_id,
                root_node_id,
                content,
            )

        with self._lock:
            existing_id = self._running_by_root.get(root_key)
            existing = self._commands.get(existing_id or "")
            if existing and existing.get("status") == "running":
                if not request.replace_existing:
                    raise OrgCommandConflict(
                        "组织已有命令正在执行，请稍后再试或显式取消/替换。",
                        command_id=existing_id or "",
                    )
                existing["cancel_requested_by_user"] = True
                existing["cancel_requested_at"] = now

            self._commands[command_id] = {
                "command_id": command_id,
                "org_id": request.org_id,
                "root_node_id": root_node_id,
                "target_node_id": request.target_node_id,
                "status": "running",
                "phase": "running",
                "result": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
                "finished_at": None,
                "origin_surface": request.origin_surface.value,
                "output_scope": request.output_scope.value,
                "source": request.source.to_dict(),
                "delivered_to": [],
                "continue_previous": request.continue_previous,
            }
            self._running_by_root[root_key] = command_id

        self._bridge_persist_user_message(request.org_id, request.target_node_id, content)
        run_request = OrgCommandRequest(
            org_id=request.org_id,
            content=run_content,
            target_node_id=request.target_node_id,
            source=request.source,
            origin_surface=request.origin_surface,
            output_scope=request.output_scope,
            replace_existing=request.replace_existing,
            continue_previous=request.continue_previous,
        )
        self._schedule_run(
            run_request,
            command_id,
            root_node_id,
            replace_existing_id=existing_id if request.replace_existing else None,
        )
        return {"command_id": command_id, "status": "running", "root_node_id": root_node_id}

    def get_status(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        cmd = self._commands.get(command_id)
        if not cmd or cmd.get("org_id") != org_id:
            return None

        try:
            live = self._runtime.get_command_tracker_snapshot(org_id, command_id)
        except Exception:
            live = None

        phase = cmd.get("phase") or cmd["status"]
        if cmd["status"] == "running":
            if live:
                phase = live.get("phase") or phase
            try:
                es = self._runtime.get_event_store(org_id)
                for ev in es.query(event_type="command_phase", limit=20) or []:
                    data = ev.get("data") or {}
                    if data.get("command_id") == command_id:
                        phase = data.get("phase") or phase
                        break
            except Exception:
                pass

        result = {
            "command_id": cmd["command_id"],
            "status": cmd["status"],
            "phase": phase,
            "root_node_id": cmd.get("root_node_id", ""),
            "result": cmd["result"],
            "error": cmd["error"],
            "elapsed_s": round(time.time() - cmd["created_at"], 1),
            "cancel_requested_by_user": bool(cmd.get("cancel_requested_by_user")),
            "origin_surface": cmd.get("origin_surface"),
            "output_scope": cmd.get("output_scope"),
        }
        if live:
            result.update({
                "root_node_id": live.get("root_node_id") or result["root_node_id"],
                "tracker_state": live.get("tracker_state"),
                "root_chain_id": live.get("root_chain_id", ""),
                "open_chains": live.get("open_chains", []),
                "open_chain_count": live.get("open_chain_count", 0),
                "open_subtree_chains": live.get("open_subtree_chains", []),
                "blockers": live.get("blockers", []),
                "blocker_summary": live.get("blocker_summary", ""),
                "busy_nodes": live.get("busy_nodes", []),
                "pending_mailbox": live.get("pending_mailbox", []),
                "root_status": live.get("root_status", ""),
                "last_progress_elapsed_s": live.get("last_progress_elapsed_s"),
                "warned_stuck": live.get("warned_stuck", False),
                "stopped_by_watchdog": live.get("auto_stopped", False),
                "cancelled_by_user": live.get("user_cancelled", False),
            })
        elif isinstance(cmd.get("result"), dict):
            command_result = cmd["result"]
            result.update({
                "warning": command_result.get("warning"),
                "stopped_by_watchdog": bool(command_result.get("stopped_by_watchdog")),
                "cancelled_by_user": bool(command_result.get("cancelled_by_user")),
            })
        return result

    async def cancel(self, org_id: str, command_id: str) -> dict[str, Any] | None:
        cmd = self._commands.get(command_id)
        if not cmd or cmd.get("org_id") != org_id:
            return None
        if cmd.get("status") != "running":
            return {"ok": True, "command_id": command_id, "already_done": True}

        result = await self._runtime.cancel_user_command(org_id, command_id)
        self._update_command_state(
            command_id,
            cancel_requested_by_user=True,
            cancel_requested_at=time.time(),
        )
        try:
            from openakita.api.routes.websocket import broadcast_event
            await broadcast_event(
                "org:command_cancelled",
                {
                    "org_id": org_id,
                    "command_id": command_id,
                    "by": "user",
                    "cancelled_roots": result.get("cancelled_roots", []),
                },
            )
        except Exception:
            logger.debug("[OrgCmd] broadcast org:command_cancelled failed", exc_info=True)
        return {
            "ok": True,
            "command_id": command_id,
            "cancelled_roots": result.get("cancelled_roots", []),
        }

    def subscribe_summary(
        self,
        command_id: str,
        *,
        surface: str = "unknown",
        target: str = "",
    ) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._summary_subscribers.setdefault(command_id, []).append(
            (queue, asyncio.get_running_loop(), surface, target)
        )
        return queue

    def unsubscribe_summary(self, command_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._summary_subscribers.get(command_id)
        if not subscribers:
            return
        for item in list(subscribers):
            if item[0] is queue:
                with suppress(ValueError):
                    subscribers.remove(item)
                break
        if not subscribers:
            self._summary_subscribers.pop(command_id, None)

    async def publish_summary(self, command_id: str, event: dict[str, Any]) -> None:
        for queue, loop, surface, target in list(self._summary_subscribers.get(command_id, [])):
            try:
                self.mark_delivered(
                    command_id,
                    surface=surface,
                    target=target,
                    event=str(event.get("type") or event.get("event") or ""),
                )
                if loop is asyncio.get_running_loop():
                    queue.put_nowait(event)
                else:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except asyncio.QueueFull:
                pass

    def find_command_for_event(self, org_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        command_id = str(data.get("command_id") or "")
        if command_id:
            cmd = self._commands.get(command_id)
            if cmd and cmd.get("org_id") == org_id:
                return cmd
        running = [
            cmd for cmd in self._commands.values()
            if cmd.get("org_id") == org_id and cmd.get("status") == "running"
        ]
        if len(running) == 1:
            return running[0]
        return None

    def mark_delivered(self, command_id: str, *, surface: str, target: str, event: str) -> None:
        cmd = self._commands.get(command_id)
        if not cmd:
            return
        delivered = cmd.setdefault("delivered_to", [])
        delivered.append({"surface": surface, "target": target, "event": event, "ts": time.time()})

    def _schedule_run(
        self,
        request: OrgCommandRequest,
        command_id: str,
        root_node_id: str,
        *,
        replace_existing_id: str | None = None,
    ) -> None:
        async def _run() -> None:
            try:
                if replace_existing_id:
                    with suppress(Exception):
                        await self._runtime.cancel_user_command(request.org_id, replace_existing_id)
                result = await self._runtime.send_command(
                    request.org_id,
                    request.target_node_id,
                    request.content,
                    command_id=command_id,
                )
                self._update_command_state(
                    command_id,
                    status="done",
                    phase="done",
                    result=result,
                    finished_at=time.time(),
                )
                self._bridge_persist_result(request.org_id, request.target_node_id, result)
                await self._push_root_task_complete(request, root_node_id, result)
                await self._broadcast_done(request.org_id, command_id, result=result)
                await self.publish_summary(command_id, {
                    "type": "org_command_done",
                    "org_id": request.org_id,
                    "command_id": command_id,
                    "result": result,
                })
            except Exception as exc:
                self._update_command_state(
                    command_id,
                    status="error",
                    phase="error",
                    error=str(exc),
                    finished_at=time.time(),
                )
                self._bridge_persist_result(request.org_id, request.target_node_id, {"error": str(exc)})
                await self._broadcast_done(request.org_id, command_id, error=str(exc))
                await self.publish_summary(command_id, {
                    "type": "org_command_done",
                    "org_id": request.org_id,
                    "command_id": command_id,
                    "error": str(exc),
                })
            finally:
                with self._lock:
                    root_key = (request.org_id, root_node_id)
                    if self._running_by_root.get(root_key) == command_id:
                        self._running_by_root.pop(root_key, None)

        engine_loop = get_engine_loop()
        if engine_loop is not None:
            asyncio.run_coroutine_threadsafe(_run(), engine_loop)
        else:
            asyncio.create_task(_run())

    async def _broadcast_done(
        self,
        org_id: str,
        command_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        try:
            from openakita.api.routes.websocket import broadcast_event
            payload = {"org_id": org_id, "command_id": command_id}
            if error:
                payload["error"] = error
            else:
                payload["result"] = result
            await broadcast_event("org:command_done", payload)
        except Exception:
            logger.warning("[OrgCmd] broadcast org:command_done failed", exc_info=True)

    async def _push_root_task_complete(
        self,
        request: OrgCommandRequest,
        root_node_id: str,
        result: dict[str, Any],
    ) -> None:
        try:
            if not self._runtime._has_active_delegations(request.org_id, root_node_id):
                inbox = self._runtime.get_inbox(request.org_id)
                result_text = (result or {}).get("result", "")
                inbox.push_task_complete(
                    request.org_id,
                    root_node_id,
                    request.content[:60],
                    result_text[:300] if result_text else "命令已完成",
                )
        except Exception:
            pass

    def _update_command_state(
        self,
        command_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        with self._lock:
            cmd = self._commands.get(command_id)
            if cmd is None:
                return None
            if status is not None:
                cmd["status"] = status
                if phase is None and status in ("done", "error"):
                    cmd["phase"] = status
            if phase is not None:
                cmd["phase"] = phase
            for k, v in fields.items():
                cmd[k] = v
            cmd["updated_at"] = time.time()
            return cmd

    def _purge_old_commands(self) -> None:
        now = time.time()
        with self._lock:
            stale = [
                cid
                for cid, cmd in self._commands.items()
                if (cmd["status"] in ("done", "error") and now - cmd["created_at"] > _CMD_TTL)
                or (cmd["status"] == "running" and now - cmd["created_at"] > _CMD_TTL * 2)
            ]
            for cid in stale:
                cmd = self._commands.pop(cid, None)
                if cmd:
                    self._running_by_root.pop((cmd.get("org_id"), cmd.get("root_node_id")), None)

    def _require_org_running(self, org_id: str) -> Any:
        org = self._runtime.get_org(org_id)
        if not org:
            raise OrgCommandError("Organization not found")
        if org.status in (OrgStatus.ACTIVE, OrgStatus.RUNNING):
            return org
        status_value = org.status.value if hasattr(org.status, "value") else str(org.status)
        if org.status == OrgStatus.PAUSED:
            raise OrgCommandConflict("组织当前已暂停，请先恢复组织后再下发指令。", command_id="")
        if org.status == OrgStatus.ARCHIVED:
            raise OrgCommandConflict("组织已归档，无法下发指令。", command_id="")
        raise OrgCommandConflict(
            f"组织尚未启动，无法下发指令或与节点通讯。当前状态: {status_value}",
            command_id="",
        )

    def _resolve_command_root_id(self, org: Any, target_node_id: str | None) -> str:
        if target_node_id:
            return target_node_id
        roots = org.get_root_nodes()
        return roots[0].id if roots else ""

    def _build_continue_content(self, org_id: str, root_node_id: str, content: str) -> str:
        """Augment a new command with recent context after user cancellation.

        This is intentionally a new command, not a resurrection of the old
        command_id. It gives the root enough persisted context to continue the
        story from blackboard/events/project tasks.
        """
        last_cmd = self._find_recent_previous_command(org_id, root_node_id)
        sections: list[str] = []
        if last_cmd:
            result = last_cmd.get("result")
            result_text = ""
            if isinstance(result, dict):
                result_text = str(result.get("result") or result.get("error") or "")[:1200]
            elif result:
                result_text = str(result)[:1200]
            sections.append(
                "\n".join([
                    f"- 上一条命令: {last_cmd.get('command_id')}",
                    f"- 状态: {last_cmd.get('status')} / {last_cmd.get('phase')}",
                    f"- 是否用户终止: {bool(last_cmd.get('cancel_requested_by_user'))}",
                    f"- 阶段性结果: {result_text or '（无）'}",
                ])
            )

        try:
            bb = self._runtime.get_blackboard(org_id)
            summary = bb.get_org_summary(max_entries=8)
            if summary:
                sections.append("最近组织黑板:\n" + summary[:2000])
        except Exception:
            pass

        try:
            store = self._runtime.get_project_store(org_id)
            tasks = store.all_tasks()
            unfinished = [
                t for t in tasks
                if str(t.get("status") or "") in {"todo", "in_progress", "delivered", "rejected", "blocked"}
            ][:12]
            if unfinished:
                lines = []
                for t in unfinished:
                    lines.append(
                        f"- {t.get('title') or t.get('id')} [{t.get('status')}] "
                        f"assignee={t.get('assignee_node_id') or '-'} "
                        f"chain={str(t.get('chain_id') or '')[:12]}"
                    )
                sections.append("未完成/待处理项目任务:\n" + "\n".join(lines))
        except Exception:
            pass

        context = "\n\n".join(s for s in sections if s.strip()) or "（没有可恢复的结构化上下文）"
        return (
            "[继续被中断任务]\n"
            "这是一条新的组织命令，不是恢复旧 command_id。请先阅读下面的历史上下文，"
            "基于黑板、事件和未完成任务继续推进；不要重复已经完成的工作。\n\n"
            f"{context}\n\n"
            "[用户的新指令]\n"
            f"{content}"
        )

    def _find_recent_previous_command(self, org_id: str, root_node_id: str) -> dict[str, Any] | None:
        candidates = [
            cmd for cmd in self._commands.values()
            if cmd.get("org_id") == org_id
            and cmd.get("root_node_id") == root_node_id
            and cmd.get("status") != "running"
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda c: float(c.get("finished_at") or c.get("updated_at") or 0), reverse=True)
        return candidates[0]

    def _bridge_persist_user_message(
        self,
        org_id: str,
        target_node_id: str | None,
        content: str,
    ) -> None:
        sm = self._session_manager
        if not sm:
            return
        chat_id = self.bridge_session_chat_id(org_id, target_node_id)
        try:
            session = sm.get_session(
                channel="desktop",
                chat_id=chat_id,
                user_id="desktop_user",
                create_if_missing=True,
            )
            if session:
                session.add_message("user", content)
                sm.mark_dirty()
        except Exception as exc:
            logger.warning("[OrgCmd] failed to persist user message to session: %s", exc)

    def _bridge_persist_result(
        self,
        org_id: str,
        target_node_id: str | None,
        result: dict[str, Any],
    ) -> None:
        sm = self._session_manager
        if not sm:
            return
        chat_id = self.bridge_session_chat_id(org_id, target_node_id)
        try:
            session = sm.get_session(
                channel="desktop",
                chat_id=chat_id,
                user_id="desktop_user",
                create_if_missing=True,
            )
            if not session:
                return
            if result.get("error"):
                session.add_message("system", f"命令执行失败: {result['error']}")
            elif result.get("result"):
                text = result["result"]
                if isinstance(text, dict):
                    text = text.get("result") or text.get("error") or str(text)
                session.add_message("assistant", str(text))
            sm.mark_dirty()
        except Exception as exc:
            logger.warning("[OrgCmd] failed to persist result to session: %s", exc)

