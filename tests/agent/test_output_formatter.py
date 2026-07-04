"""Tests for ``openakita.agent.output_formatter``.

The legacy module had no dedicated tests beyond a smoke run; this
file makes the contract explicit so the v2 port survives any future
refactor of the Agent → CLI surface.

Behavioural anchors:

* :class:`TextFormatter` produces emoji-prefixed strings; the role
  fallback for unknown roles uses ``📝``.
* :class:`JSONFormatter` suppresses intermediate emissions and
  produces a JSON-decodable artifact at the end.
* :class:`StreamJSONFormatter` produces one JSON event per call,
  ``json.loads``-decodable, with the documented ``type`` field.
* :func:`create_formatter` falls back to text on unknown types so a
  typo does not crash a run.
* The legacy ``core.output_formatter`` path re-exports the same
  classes (move-compat).
"""

from __future__ import annotations

import json

from openakita.agent.output_formatter import (
    JSONFormatter,
    StreamJSONFormatter,
    TextFormatter,
    create_formatter,
)

# ---------------------------------------------------------------------------
# TextFormatter
# ---------------------------------------------------------------------------


def test_text_message_uses_role_emoji() -> None:
    fmt = TextFormatter()
    assert fmt.format_message("assistant", "hello").startswith("🤖")
    assert fmt.format_message("user", "hi").startswith("👤")


def test_text_message_unknown_role_falls_back_to_note_emoji() -> None:
    fmt = TextFormatter()
    assert fmt.format_message("ghost", "boo").startswith("📝")


def test_text_tool_use_renders_pretty_json() -> None:
    fmt = TextFormatter()
    out = fmt.format_tool_use("read_file", {"path": "x.txt"})
    assert "🔧" in out
    assert "read_file" in out
    assert '"path"' in out


def test_text_tool_result_truncates_long_output() -> None:
    fmt = TextFormatter()
    long = "a" * 1000
    out = fmt.format_tool_result("noisy", long, is_error=False)
    assert out.startswith("✅")
    assert len(out) < 1000


def test_text_tool_result_uses_error_icon_on_failure() -> None:
    fmt = TextFormatter()
    out = fmt.format_tool_result("broken", "boom", is_error=True)
    assert out.startswith("❌")


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------


def test_json_formatter_suppresses_intermediate() -> None:
    fmt = JSONFormatter()
    assert fmt.format_message("user", "hi") == ""
    assert fmt.format_tool_use("x", {}) == ""
    assert fmt.format_tool_result("x", "ok") == ""


def test_json_formatter_final_is_decodable() -> None:
    fmt = JSONFormatter()
    convo = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]
    blob = fmt.format_final(convo)
    decoded = json.loads(blob)
    assert decoded == convo


# ---------------------------------------------------------------------------
# StreamJSONFormatter
# ---------------------------------------------------------------------------


def test_stream_message_event_shape() -> None:
    fmt = StreamJSONFormatter()
    line = fmt.format_message("user", "hi", request_id="abc")
    payload = json.loads(line)
    assert payload["type"] == "message"
    assert payload["role"] == "user"
    assert payload["content"] == "hi"
    assert payload["request_id"] == "abc"


def test_stream_tool_use_event_shape() -> None:
    fmt = StreamJSONFormatter()
    payload = json.loads(fmt.format_tool_use("read_file", {"path": "x"}))
    assert payload == {
        "type": "tool_use",
        "name": "read_file",
        "input": {"path": "x"},
    }


def test_stream_tool_result_event_truncates_long_content() -> None:
    fmt = StreamJSONFormatter()
    payload = json.loads(fmt.format_tool_result("dump", "x" * 5000))
    assert payload["type"] == "tool_result"
    assert len(payload["content"]) <= 2000


def test_stream_done_event_emitted_on_final() -> None:
    fmt = StreamJSONFormatter()
    payload = json.loads(fmt.format_final([]))
    assert payload == {"type": "done"}


# ---------------------------------------------------------------------------
# create_formatter
# ---------------------------------------------------------------------------


def test_create_formatter_dispatches_known_types() -> None:
    assert isinstance(create_formatter("text"), TextFormatter)
    assert isinstance(create_formatter("json"), JSONFormatter)
    assert isinstance(create_formatter("stream-json"), StreamJSONFormatter)


def test_create_formatter_unknown_falls_back_to_text() -> None:
    assert isinstance(create_formatter("nonsense"), TextFormatter)


# ---------------------------------------------------------------------------
# Move-compat
# ---------------------------------------------------------------------------


def test_legacy_path_re_exports_same_classes() -> None:
    from openakita.core.output_formatter import (
        JSONFormatter as LegacyJSON,
    )
    from openakita.core.output_formatter import (
        StreamJSONFormatter as LegacyStream,
    )
    from openakita.core.output_formatter import (
        TextFormatter as LegacyText,
    )
    from openakita.core.output_formatter import (
        create_formatter as legacy_create,
    )

    assert LegacyText is TextFormatter
    assert LegacyJSON is JSONFormatter
    assert LegacyStream is StreamJSONFormatter
    assert legacy_create is create_formatter
