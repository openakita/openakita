import asyncio
from types import SimpleNamespace

import pytest

from openakita.api.routes.chat import _schedule_background_save


@pytest.mark.asyncio
async def test_background_save_respects_text_replace():
    agent_done = asyncio.Event()
    agent_queue: asyncio.Queue = asyncio.Queue()
    await agent_queue.put({"type": "text_delta", "content": "[TOOL_CALL] {tool => \"x\"}"})
    await agent_queue.put({"type": "text_replace", "content": ""})
    await agent_queue.put({"type": "text_delta", "content": "最终回复"})
    await agent_queue.put(None)

    saved: list[tuple[str, dict]] = []
    session = SimpleNamespace(add_message=lambda role, text, **meta: saved.append((text, meta)))
    session_manager = SimpleNamespace(persist=lambda: None)

    _schedule_background_save(
        agent_task=asyncio.create_task(asyncio.sleep(0)),
        agent_done=agent_done,
        agent_queue=agent_queue,
        sse_fn=None,
        session=session,
        session_manager=session_manager,
        conversation_id="",
        full_reply_snapshot="",
        collected_artifacts=[],
        save_done=False,
    )

    agent_done.set()
    await asyncio.sleep(0.05)

    assert saved == [("最终回复", {})]
