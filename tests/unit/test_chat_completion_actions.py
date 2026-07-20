from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openakita.api.routes.sessions import _history_entry
from openakita.api.schemas import ChatRequest
from openakita.sessions.session import SessionContext


def test_chat_request_accepts_known_completion_action() -> None:
    request = ChatRequest.model_validate(
        {
            "message": "diagnose logs",
            "completion_actions": [
                {"type": "submit_feedback", "style": "prominent"},
            ],
        }
    )

    assert request.completion_actions[0].model_dump() == {
        "type": "submit_feedback",
        "style": "prominent",
    }


@pytest.mark.parametrize(
    "action",
    [
        {"type": "delete_files"},
        {"type": "submit_feedback", "style": "unknown"},
        {"type": "submit_feedback", "url": "https://example.com"},
    ],
)
def test_chat_request_rejects_unknown_completion_action(action: dict) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"message": "diagnose logs", "completion_actions": [action]})


def test_completion_action_survives_session_round_trip_and_history_projection() -> None:
    context = SessionContext()
    context.add_message(
        "assistant",
        "diagnosis",
        timestamp="2026-01-01T00:00:00",
        completion_actions=[{"type": "submit_feedback", "style": "prominent"}],
    )
    restored = SessionContext.from_dict(context.to_dict())
    session = SimpleNamespace(last_active=datetime.fromisoformat("2026-01-01T00:00:00"))

    entry = _history_entry(session, "conv-1", 0, restored.messages[0])

    assert entry["completion_actions"] == [{"type": "submit_feedback", "style": "prominent"}]


def test_history_projection_drops_untrusted_completion_actions() -> None:
    session = SimpleNamespace(last_active=datetime.fromisoformat("2026-01-01T00:00:00"))
    entry = _history_entry(
        session,
        "conv-1",
        0,
        {
            "role": "assistant",
            "content": "diagnosis",
            "timestamp": "2026-01-01T00:00:00",
            "completion_actions": [
                {"type": "open_url", "url": "https://example.com"},
                {"type": "submit_feedback", "style": "invalid"},
            ],
        },
    )

    assert "completion_actions" not in entry
