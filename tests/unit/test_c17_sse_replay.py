"""C17 Phase B — SSE Last-Event-ID 续传 + ringbuffer + UIConfirmBus 广播单测。

覆盖：

- :class:`SSESession` 单调 seq、deque maxlen、replay_from 边界
- :class:`SSESessionRegistry` GC + LRU evict + 全局 cap
- :func:`parse_last_event_id` 标准/异常输入
- :func:`format_sse_frame` 帧格式（``id:`` + ``data:`` 行）
- UIConfirmBus ``confirm_initiated`` / ``confirm_revoked`` broadcast + dedup
- UIConfirmBus ``active_confirms_for_session`` per-session 过滤 / 脱敏
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from openakita.core.sse_replay import (
    MAX_SESSIONS,
    SSEEvent,
    SSESession,
    SSESessionRegistry,
    format_sse_frame,
    get_registry,
    parse_last_event_id,
    reset_registry_for_testing,
)
from openakita.core.ui_confirm_bus import UIConfirmBus, reset_ui_confirm_bus

# ---------------------------------------------------------------------------
# SSESession
# ---------------------------------------------------------------------------


class TestSSESession:
    def test_seq_monotonic(self) -> None:
        s = SSESession(session_id="conv_a")
        a = s.add_event("text_delta", {"content": "hi"})
        b = s.add_event("text_delta", {"content": "there"})
        assert a.seq == 1
        assert b.seq == 2
        assert a.event_type == "text_delta"
        assert b.payload == {"content": "there"}
        assert s.current_seq == 2

    def test_maxlen_evicts_oldest(self) -> None:
        s = SSESession(session_id="conv_b", maxlen=3)
        for i in range(5):
            s.add_event("text_delta", {"i": i})
        # Only last 3 events remain in the buffer.
        assert len(s) == 3
        # But seq monotonically advanced through all 5 adds.
        assert s.current_seq == 5
        events = s.replay_from(0)
        assert [e.payload["i"] for e in events] == [2, 3, 4]
        assert [e.seq for e in events] == [3, 4, 5]

    def test_replay_from_none_returns_empty(self) -> None:
        s = SSESession(session_id="conv_c")
        s.add_event("text_delta", {})
        assert s.replay_from(None) == []
        assert s.replay_from(-5) == []
        # last_seq=0 means "I've seen nothing"; replay every buffered event.
        all_evts = s.replay_from(0)
        assert len(all_evts) == 1
        assert all_evts[0].seq == 1

    def test_replay_from_future_seq_returns_empty(self) -> None:
        s = SSESession(session_id="conv_d")
        s.add_event("text_delta", {})  # seq=1
        s.add_event("text_delta", {})  # seq=2
        assert s.replay_from(2) == []
        assert s.replay_from(100) == []

    def test_replay_from_partial_range(self) -> None:
        s = SSESession(session_id="conv_e", maxlen=10)
        for _ in range(5):
            s.add_event("text_delta", {})
        replayed = s.replay_from(2)
        assert [e.seq for e in replayed] == [3, 4, 5]

    def test_replay_from_older_than_buffer(self) -> None:
        """Client claims to have seen seq=1 but buffer maxlen evicted it."""
        s = SSESession(session_id="conv_f", maxlen=3)
        for _ in range(5):
            s.add_event("text_delta", {})
        replayed = s.replay_from(1)
        # Buffer only has 3 events left; we return them — gap will be visible.
        assert [e.seq for e in replayed] == [3, 4, 5]

    def test_is_idle_after_ttl(self) -> None:
        s = SSESession(session_id="conv_g", ttl_s=0.01)
        s.add_event("text_delta", {})
        assert s.is_idle() is False
        time.sleep(0.03)
        assert s.is_idle() is True

    def test_done_marks_session_terminal(self) -> None:
        s = SSESession(session_id="conv_terminal")
        assert s.is_terminal is False
        evt = s.add_event("done", {})
        assert s.is_terminal is True
        assert s.terminal_seq == evt.seq
        assert s.terminal_event_type == "done"

    def test_begin_turn_clears_terminal_marker(self) -> None:
        s = SSESession(session_id="conv_terminal_clear")
        s.add_event("done", {})
        assert s.is_terminal is True
        s.begin_turn()
        assert s.is_terminal is False
        assert s.terminal_seq == 0
        assert s.terminal_event_type == ""


# ---------------------------------------------------------------------------
# Turn-boundary replay floor — the cross-turn replay guard.
#
# Regression cover for the "finished turn's answer reappears on top of my new
# question" bug. The ringbuffer + seq persist across turns; without a floor a
# stale Last-Event-ID (POST door) or since_seq (resume door) would replay the
# previous, completed turn's tail into the new turn.
# ---------------------------------------------------------------------------


class TestTurnBoundaryReplayFloor:
    def test_begin_turn_advances_floor_to_current_seq(self) -> None:
        s = SSESession(session_id="conv_turn_a")
        s.add_event("text_delta", {"i": 1})
        s.add_event("done", {})  # seq=2 — turn A ends here
        assert s.begin_turn() == 2  # floor sealed at end of turn A
        # Turn B produces new events.
        s.add_event("text_delta", {"i": 3})  # seq=3
        assert s.current_seq == 3

    def test_begin_turn_is_monotonic(self) -> None:
        s = SSESession(session_id="conv_turn_mono")
        s.add_event("text_delta", {})  # seq=1
        assert s.begin_turn() == 1
        # A second begin_turn with no new events keeps the floor put — it never
        # rewinds, even if called spuriously.
        assert s.begin_turn() == 1
        s.add_event("text_delta", {})  # seq=2
        assert s.begin_turn() == 2

    def test_stale_last_event_id_cannot_replay_previous_turn(self) -> None:
        """POST door: a stale Last-Event-ID from turn A must not replay it."""
        s = SSESession(session_id="conv_turn_post")
        # Turn A: client saw seq 1, then dropped; backend buffered the tail 2..4.
        for _ in range(4):
            s.add_event("text_delta", {})
        assert s.current_seq == 4
        # Turn B begins (POST). Floor seals at 4.
        s.begin_turn()
        # A stale Last-Event-ID=1 (client's last seen seq from turn A) must NOT
        # flush turn A's buffered tail (2..4) into turn B.
        assert s.replay_from(1) == []
        assert s.replay_from(0) == []  # "seen nothing" is clamped too

    def test_stale_since_seq_cannot_replay_previous_turn(self) -> None:
        """Resume door: a stale since_seq from turn A must not replay it."""
        s = SSESession(session_id="conv_turn_resume")
        for _ in range(4):  # turn A → seq 1..4
            s.add_event("text_delta", {})
        s.begin_turn()  # turn B starts, floor=4
        s.add_event("text_delta", {})  # turn B seq=5
        s.add_event("text_delta", {})  # turn B seq=6
        # Resume with a stale since_seq=2 (from turn A) only gets turn B's
        # events (5, 6) — never turn A's tail (3, 4).
        replayed = s.replay_from(2)
        assert [e.seq for e in replayed] == [5, 6]

    def test_within_turn_catchup_still_works(self) -> None:
        """The floor must NOT break legitimate mid-turn resume catch-up."""
        s = SSESession(session_id="conv_turn_within")
        for _ in range(4):  # turn A → seq 1..4
            s.add_event("text_delta", {})
        s.begin_turn()  # turn B, floor=4
        for _ in range(3):  # turn B → seq 5..7
            s.add_event("text_delta", {})
        # Client is mid-turn-B at seq 5 and reconnects: it correctly catches up
        # on 6, 7 (since_seq=5 is past the floor, so the floor is a no-op here).
        replayed = s.replay_from(5)
        assert [e.seq for e in replayed] == [6, 7]

    def test_first_turn_floor_is_zero_no_behavior_change(self) -> None:
        """Before any begin_turn the floor is 0 — legacy replay behavior."""
        s = SSESession(session_id="conv_turn_first")
        for _ in range(3):
            s.add_event("text_delta", {})
        # No begin_turn() yet → floor 0 → replay_from(0) returns everything.
        assert [e.seq for e in s.replay_from(0)] == [1, 2, 3]


# ---------------------------------------------------------------------------
# SSESessionRegistry
# ---------------------------------------------------------------------------


class TestSSESessionRegistry:
    def test_get_or_create_idempotent(self) -> None:
        reg = SSESessionRegistry()
        a = reg.get_or_create("conv_x")
        b = reg.get_or_create("conv_x")
        assert a is b

    def test_lru_evicts_oldest_when_over_cap(self) -> None:
        reg = SSESessionRegistry(max_sessions=3)
        reg.get_or_create("a")
        reg.get_or_create("b")
        reg.get_or_create("c")
        # Access "a" so "b" is now oldest.
        reg.get_or_create("a")
        reg.get_or_create("d")
        ids = reg.session_ids()
        assert "b" not in ids
        assert "a" in ids
        assert "c" in ids
        assert "d" in ids

    def test_gc_idle_sessions(self) -> None:
        reg = SSESessionRegistry(default_ttl_s=0.01)
        s = reg.get_or_create("conv_gc")
        s.add_event("text_delta", {})
        time.sleep(0.03)
        removed = reg.gc_idle_sessions()
        assert removed == 1
        assert reg.get("conv_gc") is None

    def test_get_or_create_rejects_empty(self) -> None:
        reg = SSESessionRegistry()
        with pytest.raises(ValueError):
            reg.get_or_create("")


class TestRegistrySingleton:
    def setup_method(self) -> None:
        reset_registry_for_testing()

    def teardown_method(self) -> None:
        reset_registry_for_testing()

    def test_singleton_persists(self) -> None:
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_reset_for_testing(self) -> None:
        r1 = get_registry()
        reset_registry_for_testing()
        r2 = get_registry()
        assert r1 is not r2

    def test_default_max_sessions(self) -> None:
        assert MAX_SESSIONS == 1024


# ---------------------------------------------------------------------------
# parse_last_event_id + format_sse_frame
# ---------------------------------------------------------------------------


class TestParseLastEventID:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, None),
            ("", None),
            ("0", None),
            ("-3", None),
            ("not-a-number", None),
            ("42", 42),
            ("  17  ", 17),
        ],
    )
    def test_inputs(self, raw: str | None, expected: int | None) -> None:
        assert parse_last_event_id(raw) == expected


class TestFormatSSEFrame:
    def test_frame_contains_id_and_data(self) -> None:
        evt = SSEEvent(seq=7, event_type="text_delta", payload={"content": "hi"}, ts=1.0)
        out = format_sse_frame(evt, data_json='{"type":"text_delta","content":"hi"}')
        assert out.startswith("id: 7\n")
        assert "data: " in out
        assert out.endswith("\n\n")


# ---------------------------------------------------------------------------
# UIConfirmBus broadcast hook (Phase B.4)
# ---------------------------------------------------------------------------


class TestUIConfirmBusBroadcast:
    def setup_method(self) -> None:
        reset_ui_confirm_bus()

    def teardown_method(self) -> None:
        reset_ui_confirm_bus()

    def test_store_pending_broadcasts_confirm_initiated(self) -> None:
        bus = UIConfirmBus(ttl_seconds=300)
        events: list[tuple[str, dict[str, Any]]] = []

        def hook(event_type: str, payload: dict[str, Any]) -> None:
            events.append((event_type, payload))

        bus.set_broadcast_hook(hook)
        bus.store_pending(
            "tu_abc",
            "shell",
            {"command": "ls"},
            session_id="conv_a",
            needs_sandbox=True,
        )
        assert len(events) == 1
        ev_type, payload = events[0]
        assert ev_type == "confirm_initiated"
        assert payload["confirm_id"] == "tu_abc"
        assert payload["tool_name"] == "shell"
        assert payload["session_id"] == "conv_a"
        assert payload["needs_sandbox"] is True
        # Privacy: the broadcasted payload deliberately does NOT include
        # the params dict (which may carry secrets / file contents).
        assert "params" not in payload

    def test_resolve_broadcasts_confirm_revoked_once(self) -> None:
        bus = UIConfirmBus(ttl_seconds=300)
        events: list[tuple[str, dict[str, Any]]] = []
        bus.set_broadcast_hook(lambda et, p: events.append((et, p)))

        bus.prepare("tu_xyz")
        bus.store_pending("tu_xyz", "shell", {"command": "rm"}, session_id="conv_z")
        events.clear()  # ignore the initiated event for this case
        first = bus.resolve("tu_xyz", "deny")
        assert first is not None
        # Second resolve is a no-op for pending + decisions
        second = bus.resolve("tu_xyz", "allow_once")
        assert second is None
        revoked = [e for e in events if e[0] == "confirm_revoked"]
        assert len(revoked) == 1
        assert revoked[0][1]["confirm_id"] == "tu_xyz"
        assert revoked[0][1]["decision"] == "deny"

    def test_broadcast_hook_exception_is_isolated(self) -> None:
        bus = UIConfirmBus(ttl_seconds=300)

        def bad_hook(event_type: str, payload: dict[str, Any]) -> None:
            raise RuntimeError("hook boom")

        bus.set_broadcast_hook(bad_hook)
        # Bus must continue working despite a broken hook.
        bus.store_pending("tu_e", "shell", {}, session_id="conv_e")
        assert bus.list_pending()

    def test_active_confirms_for_session(self) -> None:
        bus = UIConfirmBus(ttl_seconds=300)
        bus.store_pending("tu_1", "shell", {"command": "ls"}, session_id="conv_a")
        bus.store_pending("tu_2", "write_file", {"path": "/tmp/x"}, session_id="conv_a")
        bus.store_pending("tu_3", "shell", {}, session_id="conv_b")
        a = bus.active_confirms_for_session("conv_a")
        assert {c["confirm_id"] for c in a} == {"tu_1", "tu_2"}
        # Privacy: params not leaked here either.
        for c in a:
            assert "params" not in c
            assert c["tool_name"] in ("shell", "write_file")

    def test_no_hook_is_silent_no_op(self) -> None:
        bus = UIConfirmBus(ttl_seconds=300)
        bus.store_pending("tu_x", "shell", {}, session_id="conv_x")
        bus.prepare("tu_x")
        bus.resolve("tu_x", "deny")
        # The whole flow must succeed even without a broadcast hook wired.
        assert bus.list_pending() == []
