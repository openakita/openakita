"""BP REST API: 状态查询、模式切换、前端启动。"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from seeagent.bestpractice.facade import (
    get_bp_config_loader,
    get_bp_engine,
    get_bp_state_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bp")


# ── BP state restoration (survives server restart) ────────────


def _ensure_bp_restored(request: Request, session_id: str, sm) -> None:
    """Restore BP instances from session metadata if missing in memory.

    After server restart, BPStateManager._instances is empty.
    This lazily restores them from session.metadata["bp_state"].
    """
    if not sm or not session_id:
        return
    # Already have instances for this session? Skip.
    if sm.get_all_for_session(session_id):
        return
    session = _resolve_session(request, session_id)
    if not session:
        return
    bp_state = session.metadata.get("bp_state")
    if not bp_state:
        return
    loader = get_bp_config_loader()
    config_map = {}
    if loader and loader.configs:
        config_map = dict(loader.configs)
    restored = sm.restore_from_dict(session_id, bp_state, config_map=config_map)
    if restored:
        logger.info(f"[BP] Restored {restored} instance(s) for session {session_id} from metadata")


# ── Existing endpoints (unchanged) ────────────────────────────


@router.get("/status")
async def get_bp_status(session_id: str, request: Request):
    """返回指定会话的所有 BP 实例状态。"""
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"instances": [], "active_id": None})

    _ensure_bp_restored(request, session_id, sm)

    instances = sm.get_all_for_session(session_id)
    active = sm.get_active(session_id)
    return JSONResponse({
        "instances": [
            {
                "instance_id": snap.instance_id,
                "bp_id": snap.bp_id,
                "bp_name": snap.bp_config.name if snap.bp_config else snap.bp_id,
                "status": snap.status.value,
                "run_mode": snap.run_mode.value,
                "current_subtask_index": snap.current_subtask_index,
                "subtask_statuses": {
                    k: v.value if hasattr(v, "value") else v
                    for k, v in snap.subtask_statuses.items()
                },
                "subtask_outputs": snap.subtask_outputs,
            }
            for snap in instances
        ],
        "active_id": active.instance_id if active else None,
    })


@router.put("/run-mode")
async def set_run_mode(request: Request):
    """切换 BP 实例的运行模式 (manual/auto)。"""
    from seeagent.bestpractice.models import RunMode

    body = await request.json()
    instance_id = body.get("instance_id", "")
    run_mode_str = body.get("run_mode", "manual")

    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse(
            {"success": False, "error": "BP system not initialized"}, 500
        )

    snap = sm.get(instance_id)
    if not snap:
        return JSONResponse(
            {"success": False, "error": f"Instance {instance_id} not found"}, 404
        )

    snap.run_mode = (
        RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL
    )
    return JSONResponse({"success": True, "run_mode": snap.run_mode.value})


@router.put("/edit-output")
async def edit_bp_output(request: Request):
    """前端编辑子任务输出 (Chat-to-Edit)。"""
    body = await request.json()
    instance_id = body.get("instance_id", "")
    subtask_id = body.get("subtask_id", "")
    changes = body.get("changes", {})

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse(
            {"success": False, "error": "BP system not initialized"}, 500
        )

    snap = sm.get(instance_id)
    if not snap:
        return JSONResponse(
            {"success": False, "error": f"Instance {instance_id} not found"}, 404
        )

    bp_config = snap.bp_config
    if not bp_config:
        loader = get_bp_config_loader()
        bp_config = loader.get(snap.bp_id) if loader else None

    if not bp_config:
        return JSONResponse(
            {"success": False, "error": "BP config not found"}, 404
        )

    result = engine.handle_edit_output(instance_id, subtask_id, changes, bp_config)
    return JSONResponse(result)


# ── Busy-lock (R11, R16) ───────────────────────────────────────
_bp_busy_locks: dict[str, tuple[str, float]] = {}  # session_id → (source, timestamp)
_bp_busy_mutex = asyncio.Lock()
_BP_LOCK_TTL = 600


async def _bp_mark_busy(session_id: str, source: str) -> bool:
    """Try to acquire busy-lock. Returns False if already locked."""
    async with _bp_busy_mutex:
        now = time.time()
        expired = [k for k, (_, ts) in _bp_busy_locks.items() if now - ts > _BP_LOCK_TTL]
        for k in expired:
            del _bp_busy_locks[k]
        if session_id in _bp_busy_locks:
            return False
        _bp_busy_locks[session_id] = (source, now)
        return True


def _bp_renew_busy(session_id: str) -> None:
    """Renew busy-lock timestamp to prevent TTL expiry during long auto-mode runs."""
    if session_id in _bp_busy_locks:
        source, _ = _bp_busy_locks[session_id]
        _bp_busy_locks[session_id] = (source, time.time())


def _bp_clear_busy(session_id: str) -> None:
    _bp_busy_locks.pop(session_id, None)


# ── SSE Helpers ────────────────────────────────────────────────
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ── Reply state collection helpers ─────────────────────────────


def _new_reply_state() -> dict:
    """Create empty reply_state dict."""
    return {
        "thinking": "",
        "step_cards": [],
        "agent_thinking": {},
        "agent_summaries": {},
        "plan_checklist": None,
        "timer": {"ttft": None, "total": None},
        "bp_progress": None,
        "bp_subtask_output": None,
        "bp_subtask_outputs": [],  # ALL subtask outputs (auto mode produces multiple)
        "bp_subtask_complete": None,
        "bp_instance_created": None,
    }


def _upsert_step_card(cards: list, event: dict) -> None:
    """Upsert step card by step_id."""
    step_id = event.get("step_id")
    for i, c in enumerate(cards):
        if c.get("step_id") == step_id:
            cards[i] = event
            return
    cards.append(event)


def _collect_reply_state(event: dict, reply_state: dict, full_reply: list) -> None:
    """Collect SSE event data into reply_state for persistence."""
    etype = event.get("type")
    if etype == "thinking":
        agent_id = event.get("agent_id")
        if agent_id and agent_id != "main":
            at = reply_state["agent_thinking"].setdefault(
                agent_id, {"content": "", "done": False},
            )
            at["content"] += event.get("content", "")
        else:
            reply_state["thinking"] += event.get("content", "")
    elif etype == "step_card":
        _upsert_step_card(reply_state["step_cards"], event)
    elif etype == "ai_text":
        agent_id = event.get("agent_id")
        if agent_id and agent_id != "main":
            reply_state["agent_summaries"][agent_id] = (
                reply_state["agent_summaries"].get(agent_id, "")
                + event.get("content", "")
            )
        else:
            full_reply.append(event.get("content", ""))
    elif etype == "bp_progress":
        reply_state["bp_progress"] = event
    elif etype == "bp_subtask_output":
        reply_state["bp_subtask_output"] = event
        reply_state["bp_subtask_outputs"].append(event)
    elif etype == "bp_subtask_complete":
        reply_state["bp_subtask_complete"] = event
        # Also store as bp_subtask_output for frontend restoration
        reply_state["bp_subtask_output"] = event
        reply_state["bp_subtask_outputs"].append(event)
    elif etype == "bp_instance_created":
        reply_state["bp_instance_created"] = event
    elif etype == "plan_checklist":
        reply_state["plan_checklist"] = event.get("steps")
    elif etype == "timer_update":
        phase = event.get("phase")
        if phase in reply_state["timer"] and event.get("state") == "done":
            reply_state["timer"][phase] = event.get("value")


# ── Session resolution (R15) ──────────────────────────────────


def _resolve_session_manager(request: Request):
    """Get session_manager from app state."""
    return getattr(request.app.state, "session_manager", None)


def _resolve_session(request: Request, session_id: str, *, create_if_missing: bool = False):
    """Get session from session_manager.
    /bp/start uses create_if_missing=True; /bp/next, /bp/answer use False.
    """
    sm = getattr(request.app.state, "session_manager", None)
    if sm and session_id:
        return sm.get_session(
            channel="seecrab", chat_id=session_id,
            user_id="seecrab_user", create_if_missing=create_if_missing,
        )
    return None


# ── State persistence (R12, R18) ──────────────────────────────


def _persist_user_message(session, message: str, session_manager=None) -> None:
    """Persist user interaction message to session history."""
    if session and message:
        try:
            session.add_message("user", message)
            if session_manager:
                session_manager.mark_dirty()
        except Exception:
            pass


def _persist_bp_to_session(
    session, instance_id: str, sm,
    *, reply_state: dict | None = None, full_reply: str = "",
    session_manager=None,
) -> None:
    """Persist BP state to session (R12, R18).
    Two layers: metadata for recovery + add_message for history.
    """
    if not session or not sm:
        return
    snap = sm.get(instance_id)
    if not snap:
        return
    try:
        session.metadata["bp_state"] = sm.serialize_for_session(snap.session_id)
    except Exception:
        pass
    try:
        bp_config = snap.bp_config
        bp_name = bp_config.name if bp_config else snap.bp_id
        done_count = sum(
            1 for s in snap.subtask_statuses.values()
            if (s.value if hasattr(s, "value") else s) == "done"
        )
        total = len(snap.subtask_statuses)
        summary = full_reply or f"[BP] 「{bp_name}」进度: {done_count}/{total}"

        rs = reply_state or {}
        session.add_message("assistant", summary, reply_state=rs)
    except Exception:
        pass
    if session_manager:
        try:
            session_manager.mark_dirty()
        except Exception:
            pass


# ── New SSE endpoints (R4) ────────────────────────────────────


@router.post("/start")
async def bp_start(request: Request):
    """Create BP instance and execute first subtask. Returns SSE stream."""
    from seeagent.bestpractice.models import RunMode

    body = await request.json()
    bp_id = body.get("bp_id", "")
    session_id = body.get("session_id", "")
    input_data = body.get("input_data", {})
    run_mode_str = body.get("run_mode", "manual")

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)

    loader = get_bp_config_loader()
    bp_config = loader.configs.get(bp_id) if loader and loader.configs else None
    if not bp_config:
        return JSONResponse({"error": f"BP '{bp_id}' not found"}, status_code=404)

    if not await _bp_mark_busy(session_id, "bp_start"):
        return JSONResponse({"error": "Session is busy"}, status_code=409)

    run_mode = RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL
    instance_id = sm.create_instance(
        bp_config, session_id, initial_input=input_data, run_mode=run_mode,
    )
    session = _resolve_session(request, session_id, create_if_missing=True)
    session_mgr = _resolve_session_manager(request)
    _persist_user_message(session, body.get("user_message", ""), session_manager=session_mgr)

    async def generate():
        disconnect_event = asyncio.Event()
        reply_state = _new_reply_state()
        full_reply: list[str] = []

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    disconnect_event.set()
                    if session and hasattr(session, "context"):
                        dt = getattr(session.context, "_bp_delegate_task", None)
                        if dt and not dt.done():
                            dt.cancel()
                    return
                await asyncio.sleep(2)

        watcher = asyncio.create_task(_disconnect_watcher())

        try:
            created_event = {"type": "bp_instance_created",
                             "instance_id": instance_id, "bp_id": bp_id}
            yield _sse(created_event)
            _collect_reply_state(created_event, reply_state, full_reply)

            async for event in engine.advance(instance_id, session):
                if disconnect_event.is_set():
                    break
                yield _sse(event)
                _collect_reply_state(event, reply_state, full_reply)
                if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                    _bp_renew_busy(session_id)

            _persist_bp_to_session(session, instance_id, sm,
                                   reply_state=reply_state,
                                   full_reply="".join(full_reply),
                                   session_manager=session_mgr)
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})
        finally:
            watcher.cancel()
            _bp_clear_busy(session_id)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.post("/next")
async def bp_next(request: Request):
    """Advance BP to next subtask. Returns SSE stream."""
    body = await request.json()
    instance_id = body.get("instance_id", "")
    session_id = body.get("session_id", "")

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)

    _ensure_bp_restored(request, session_id, sm)

    if not await _bp_mark_busy(session_id, "bp_next"):
        return JSONResponse({"error": "Session is busy"}, status_code=409)

    session = _resolve_session(request, session_id)
    session_mgr = _resolve_session_manager(request)
    _persist_user_message(session, body.get("user_message", ""), session_manager=session_mgr)

    async def generate():
        disconnect_event = asyncio.Event()
        reply_state = _new_reply_state()
        full_reply: list[str] = []

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    disconnect_event.set()
                    if session and hasattr(session, "context"):
                        dt = getattr(session.context, "_bp_delegate_task", None)
                        if dt and not dt.done():
                            dt.cancel()
                    return
                await asyncio.sleep(2)

        watcher = asyncio.create_task(_disconnect_watcher())

        try:
            async for event in engine.advance(instance_id, session):
                if disconnect_event.is_set():
                    break
                yield _sse(event)
                _collect_reply_state(event, reply_state, full_reply)
                if event.get("type") in ("bp_subtask_complete", "bp_progress"):
                    _bp_renew_busy(session_id)

            _persist_bp_to_session(session, instance_id, sm,
                                   reply_state=reply_state,
                                   full_reply="".join(full_reply),
                                   session_manager=session_mgr)
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})
        finally:
            watcher.cancel()
            _bp_clear_busy(session_id)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.post("/answer")
async def bp_answer(request: Request):
    """Submit ask_user answer and continue. Returns SSE stream."""
    body = await request.json()
    instance_id = body.get("instance_id", "")
    subtask_id = body.get("subtask_id", "")
    data = body.get("data", {})
    session_id = body.get("session_id", "")

    engine = get_bp_engine()
    sm = get_bp_state_manager()
    if not engine or not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)

    _ensure_bp_restored(request, session_id, sm)

    if not await _bp_mark_busy(session_id, "bp_answer"):
        return JSONResponse({"error": "Session is busy"}, status_code=409)

    session = _resolve_session(request, session_id)
    session_mgr = _resolve_session_manager(request)
    _persist_user_message(session, body.get("user_message", ""), session_manager=session_mgr)

    async def generate():
        disconnect_event = asyncio.Event()
        reply_state = _new_reply_state()
        full_reply: list[str] = []

        async def _disconnect_watcher():
            while not disconnect_event.is_set():
                if await request.is_disconnected():
                    disconnect_event.set()
                    if session and hasattr(session, "context"):
                        dt = getattr(session.context, "_bp_delegate_task", None)
                        if dt and not dt.done():
                            dt.cancel()
                    return
                await asyncio.sleep(2)

        watcher = asyncio.create_task(_disconnect_watcher())

        try:
            async for event in engine.answer(instance_id, subtask_id, data, session):
                if disconnect_event.is_set():
                    break
                yield _sse(event)
                _collect_reply_state(event, reply_state, full_reply)

            _persist_bp_to_session(session, instance_id, sm,
                                   reply_state=reply_state,
                                   full_reply="".join(full_reply),
                                   session_manager=session_mgr)
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})
            yield _sse({"type": "done"})
        finally:
            watcher.cancel()
            _bp_clear_busy(session_id)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )


# ── New plain JSON endpoints (R5) ─────────────────────────────


@router.get("/output/{instance_id}/{subtask_id}")
async def bp_get_output(instance_id: str, subtask_id: str):
    """Query subtask output (plain JSON)."""
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)
    snap = sm.get(instance_id)
    if not snap:
        return JSONResponse({"error": "Not found"}, status_code=404)
    output = snap.subtask_outputs.get(subtask_id)
    if output is None:
        return JSONResponse({"error": "No output"}, status_code=404)
    return JSONResponse({"output": output})


@router.delete("/{instance_id}")
async def bp_cancel(instance_id: str):
    """Cancel BP instance."""
    sm = get_bp_state_manager()
    if not sm:
        return JSONResponse({"error": "BP system not initialized"}, status_code=500)
    snap = sm.get(instance_id)
    if not snap:
        return JSONResponse({"error": "Not found"}, status_code=404)
    sm.cancel(instance_id)
    return JSONResponse({"status": "ok"})
