import asyncio

import pytest

from openakita.logging.session_buffer import SessionLogBuffer


@pytest.mark.asyncio
async def test_current_log_session_is_task_local():
    buffer = SessionLogBuffer(max_entries_per_session=10, max_sessions=10)
    buffer.clear_all()
    buffer.clear_current_session()

    async def write_log(session_id: str, message: str):
        buffer.set_current_session(session_id)
        await asyncio.sleep(0)
        buffer.add_log("INFO", "test", message)
        return buffer.get_current_session()

    current_a, current_b = await asyncio.gather(
        write_log("session-a", "from-a"),
        write_log("session-b", "from-b"),
    )

    assert current_a == "session-a"
    assert current_b == "session-b"
    assert [log["message"] for log in buffer.get_logs("session-a", include_global=False)] == [
        "from-a"
    ]
    assert [log["message"] for log in buffer.get_logs("session-b", include_global=False)] == [
        "from-b"
    ]
