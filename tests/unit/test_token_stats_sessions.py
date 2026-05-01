import pytest

from openakita.storage.database import Database


@pytest.mark.asyncio
async def test_token_usage_sessions_include_request_ids(tmp_path):
    db = Database(tmp_path / "akita.db")
    await db.connect()
    try:
        await db._connection.execute(
            """
            INSERT INTO token_usage (
                session_id, request_id, turn_id, endpoint_name, model,
                operation_type, operation_detail, input_tokens, output_tokens,
                channel, user_id, agent_profile_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "conv-1",
                "req-1",
                "turn-1",
                "test-endpoint",
                "test-model",
                "chat_react_iteration",
                "iteration_1",
                10,
                5,
                "web",
                "user",
                "default",
            ),
        )
        await db._connection.commit()

        rows = await db.get_token_usage_sessions(
            start_time="2000-01-01 00:00:00",
            end_time="2999-01-01 00:00:00",
        )

        assert rows
        assert rows[0]["session_id"] == "conv-1"
        assert rows[0]["request_ids"] == "req-1"
        assert rows[0]["total_tokens"] == 15
    finally:
        await db.close()
