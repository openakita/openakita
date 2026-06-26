"""Cancel-resume wiring tests (Issue #608).

v1.28 shipped the ``cancel_cleanup`` persist/load/clear helpers but left
them unwired — a cancelled turn dropped its in-memory ``working_messages``
and the next turn re-planned from the flattened text history, re-running
already-completed tool work ("取消后续聊从头重做").

These tests cover the wiring added on top of the (already-green) helper
unit tests in ``test_cancel_cleanup.py``:

* ``ReasoningEngine._resume_eligible`` gating (sub-agent / ephemeral id).
* ``_maybe_persist_cancelled_working_messages`` only persists a cancelled
  turn that actually ran tools, keyed off ``state.session_id``.
* ``_maybe_load_resume_working_messages`` restores the structured tool
  blocks and merges the new human-user tail + a continue nudge — and the
  result stays Anthropic-well-formed after orphan synthesis ("不重做").
* ``_maybe_clear_resume_state`` clears on normal exit but preserves the
  just-persisted file on a cancel exit.
* TTL expiry / missing file fall back gracefully (return None).
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from openakita.core import reasoning_engine as re_mod
from openakita.core.agent_state import TaskState
from openakita.core.cancel_cleanup import (
    find_orphan_tool_uses,
    has_tool_blocks,
    persist_working_messages,
    synthesize_tool_results_for_orphans,
)
from openakita.core.reasoning_engine import ReasoningEngine

# The cancel-resume wiring (``ReasoningEngine._resume_eligible`` /
# ``_maybe_persist_cancelled_working_messages`` /
# ``_maybe_load_resume_working_messages`` / ``_maybe_clear_resume_state``) is
# upstream v1.28 work (#608) that was NOT ported after the ADR-0003 split of
# ``core/agent.py``. The underlying cancel_cleanup helpers
# (tests/integration/test_cancel_cleanup.py) ARE present; only the
# reasoning-engine wiring is deferred. The compat shim keeps the import path
# alive. See docs/follow-ups/skipped-items-roadmap.md (Batch C — core/agent.py
# merge follow-ups).
pytestmark = pytest.mark.skip(
    reason=(
        "cancel-resume reasoning-engine wiring (#608) not ported after "
        "ADR-0003 split; see docs/follow-ups/skipped-items-roadmap.md (Batch C)"
    )
)


@pytest.fixture()
def engine():
    """An uninitialized engine: the resume helpers only touch static
    methods + ``settings.data_dir`` + ``cancel_cleanup``, so we can skip
    the heavy ``__init__`` (brain / tools / context manager)."""
    return ReasoningEngine.__new__(ReasoningEngine)


@pytest.fixture()
def data_dir(monkeypatch):
    # ``settings.data_dir`` is a read-only computed property (== project_root /
    # "data"); redirect it by pointing project_root at a temp dir.
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(re_mod.settings, "project_root", Path(d))
        yield re_mod.settings.data_dir


def _tool_turn() -> list[dict]:
    """A working_messages snapshot of a turn that wrote a file."""
    return [
        {"role": "user", "content": "把内容写到 a.txt"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "好的，我来写文件"},
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": "write_file",
                    "input": {"path": "a.txt", "content": "hello"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu-1", "content": "written"},
            ],
        },
    ]


# ── has_tool_blocks ───────────────────────────────────────────────────


class TestHasToolBlocks:
    def test_detects_tool_use_and_tool_result(self) -> None:
        assert has_tool_blocks(_tool_turn()) is True

    def test_plain_text_history_has_none(self) -> None:
        assert has_tool_blocks([{"role": "user", "content": "hi"}]) is False
        assert has_tool_blocks([]) is False
        assert has_tool_blocks(None) is False


# ── _resume_eligible gating ───────────────────────────────────────────


class TestResumeEligible:
    def test_normal_conversation_is_eligible(self) -> None:
        assert ReasoningEngine._resume_eligible("conv-123", False) is True

    def test_sub_agent_is_skipped(self) -> None:
        assert ReasoningEngine._resume_eligible("conv-123", True) is False

    def test_ephemeral_run_id_is_skipped(self) -> None:
        assert ReasoningEngine._resume_eligible("_run_abc", False) is False

    def test_empty_id_is_skipped(self) -> None:
        assert ReasoningEngine._resume_eligible("", False) is False
        assert ReasoningEngine._resume_eligible(None, False) is False


# ── persist guard ─────────────────────────────────────────────────────


class TestPersistGuard:
    def _state(self, **kw) -> TaskState:
        st = TaskState(task_id="t", session_id=kw.get("session_id", "conv-1"))
        st.is_sub_agent = kw.get("is_sub_agent", False)
        st.cancelled = kw.get("cancelled", True)
        st.cancel_reason = kw.get("cancel_reason", "用户从界面取消任务")
        return st

    def _file(self, data_dir: Path, conv: str) -> Path:
        return data_dir / "working_messages" / f"{conv}.json"

    def test_persists_cancelled_turn_with_tools(self, engine, data_dir) -> None:
        engine._maybe_persist_cancelled_working_messages(
            _tool_turn(), self._state(session_id="conv-1"), "claude"
        )
        assert self._file(data_dir, "conv-1").exists()

    def test_skips_turn_without_tool_blocks(self, engine, data_dir) -> None:
        engine._maybe_persist_cancelled_working_messages(
            [{"role": "user", "content": "hi"}], self._state(session_id="conv-2"), "claude"
        )
        assert not self._file(data_dir, "conv-2").exists()

    def test_skips_sub_agent(self, engine, data_dir) -> None:
        engine._maybe_persist_cancelled_working_messages(
            _tool_turn(), self._state(session_id="conv-3", is_sub_agent=True), "claude"
        )
        assert not self._file(data_dir, "conv-3").exists()

    def test_skips_ephemeral_run_id(self, engine, data_dir) -> None:
        engine._maybe_persist_cancelled_working_messages(
            _tool_turn(), self._state(session_id="_run_xyz"), "claude"
        )
        assert not self._file(data_dir, "_run_xyz").exists()


# ── load + merge ("不重做") ─────────────────────────────────────────────


class TestLoadAndMerge:
    def test_resume_restores_tools_and_appends_new_user_and_hint(self, engine, data_dir) -> None:
        persist_working_messages("conv-9", _tool_turn(), base_dir=data_dir)

        # Next turn's rebuilt text history + the new "继续" message.
        messages = [
            {"role": "user", "content": "把内容写到 a.txt"},
            {"role": "assistant", "content": "[任务已取消]"},
            {"role": "user", "content": "继续"},
        ]
        merged = engine._maybe_load_resume_working_messages(messages, "conv-9", False)
        assert merged is not None

        # The completed write_file tool_use + tool_result are carried forward
        # (the whole point: the model sees it's done and won't re-run it).
        assert has_tool_blocks(merged) is True
        tool_use_ids = [
            b.get("id")
            for m in merged
            if isinstance(m.get("content"), list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        assert "tu-1" in tool_use_ids

        # New human-user tail is appended after the restored state.
        assert any(m.get("role") == "user" and m.get("content") == "继续" for m in merged)
        # A continue nudge is appended last (framed as reusable context, not
        # an imperative to continue — avoids biasing a genuine topic change).
        assert "不要重复执行" in merged[-1]["content"]

    def test_merged_sequence_is_well_formed_after_synthesis(self, engine, data_dir) -> None:
        """Cancelled mid-tool: persisted snapshot ends on an orphan tool_use.
        After merge + synthesize the sequence must be Anthropic-well-formed."""
        orphan_turn = [
            {"role": "user", "content": "跑个命令"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "orphan-x", "name": "run_shell", "input": {}},
                ],
            },
        ]
        persist_working_messages("conv-orphan", orphan_turn, base_dir=data_dir)
        messages = [
            {"role": "user", "content": "跑个命令"},
            {"role": "user", "content": "继续"},
        ]
        merged = engine._maybe_load_resume_working_messages(messages, "conv-orphan", False)
        assert merged is not None
        # Mirror the seed: synthesize runs right after the load in the engine.
        synthesize_tool_results_for_orphans(merged)
        assert find_orphan_tool_uses(merged) == []

    def test_funnel_persist_key_matches_seed_load_key(self, engine, data_dir) -> None:
        """Contract lock: the funnel persists keyed off ``state.session_id``
        while the seed loads keyed off the ``conversation_id`` param.  In the
        engine these are the same string (``state.session_id`` is seeded from
        ``conversation_id``); if a future refactor breaks that equivalence,
        resume silently stops working.  This test pins the round-trip across
        the two real helper entry points."""
        conv = "conv-keycheck"
        st = TaskState(task_id="t", session_id=conv)
        st.is_sub_agent = False
        st.cancelled = True
        st.cancel_reason = "用户从界面取消任务"

        # Funnel side (uses state.session_id as the key).
        engine._maybe_persist_cancelled_working_messages(_tool_turn(), st, "claude")

        # Seed side (uses conversation_id param as the key).
        merged = engine._maybe_load_resume_working_messages(
            [{"role": "user", "content": "继续"}], conv, False
        )
        assert merged is not None
        assert has_tool_blocks(merged) is True

    def test_returns_none_when_no_persisted_state(self, engine, data_dir) -> None:
        assert (
            engine._maybe_load_resume_working_messages(
                [{"role": "user", "content": "hi"}], "conv-absent", False
            )
            is None
        )

    def test_returns_none_for_sub_agent(self, engine, data_dir) -> None:
        persist_working_messages("conv-sa", _tool_turn(), base_dir=data_dir)
        assert (
            engine._maybe_load_resume_working_messages(
                [{"role": "user", "content": "继续"}], "conv-sa", True
            )
            is None
        )

    def test_load_consumes_file(self, engine, data_dir) -> None:
        persist_working_messages("conv-consume", _tool_turn(), base_dir=data_dir)
        f = data_dir / "working_messages" / "conv-consume.json"
        assert f.exists()
        engine._maybe_load_resume_working_messages(
            [{"role": "user", "content": "继续"}], "conv-consume", False
        )
        assert not f.exists()

    def test_expired_file_falls_back_gracefully(self, engine, data_dir, monkeypatch) -> None:
        # Load uses the 24h hygiene window; anything older than that is treated
        # as a crash leftover and not resumed (return None → text-history fallback).
        persist_working_messages("conv-old", _tool_turn(), base_dir=data_dir)
        f = data_dir / "working_messages" / "conv-old.json"
        long_ago = time.time() - (re_mod.DEFAULT_TTL_SECONDS + 600)
        import os

        os.utime(f, (long_ago, long_ago))
        assert (
            engine._maybe_load_resume_working_messages(
                [{"role": "user", "content": "继续"}], "conv-old", False
            )
            is None
        )

    def test_stale_within_load_window_resumes_tools_without_hint(self, engine, data_dir) -> None:
        """Past the hint-freshness window but inside the 24h load window: the
        completed tool work is STILL restored (no redo) but the continue nudge
        is suppressed, since a long-stale resume is more likely a topic change.
        This is the key decoupling from Issue #608 — load by hygiene, hint by
        freshness, matching the reference projects (none discard tools by age)."""
        persist_working_messages("conv-stale", _tool_turn(), base_dir=data_dir)
        f = data_dir / "working_messages" / "conv-stale.json"
        stale = time.time() - (re_mod.RESUME_HINT_FRESHNESS_SECONDS + 600)
        import os

        os.utime(f, (stale, stale))
        merged = engine._maybe_load_resume_working_messages(
            [{"role": "user", "content": "换个话题"}], "conv-stale", False
        )
        assert merged is not None
        # Tools carried forward (the redo-prevention guarantee holds regardless
        # of age, as long as the snapshot survived the 24h janitor).
        assert has_tool_blocks(merged) is True
        # ...but no continue nudge was appended.
        assert not any("不要重复执行" in str(m.get("content", "")) for m in merged)

    def test_fresh_file_injects_hint(self, engine, data_dir) -> None:
        """Within the freshness window the continue nudge IS appended."""
        persist_working_messages("conv-fresh", _tool_turn(), base_dir=data_dir)
        merged = engine._maybe_load_resume_working_messages(
            [{"role": "user", "content": "继续"}], "conv-fresh", False
        )
        assert merged is not None
        assert "不要重复执行" in str(merged[-1].get("content", ""))


# ── clear semantics ───────────────────────────────────────────────────


class TestClearResumeState:
    def _persist(self, data_dir: Path, conv: str) -> Path:
        persist_working_messages(conv, _tool_turn(), base_dir=data_dir)
        return data_dir / "working_messages" / f"{conv}.json"

    def test_normal_exit_clears(self, engine, data_dir) -> None:
        f = self._persist(data_dir, "conv-done")
        st = TaskState(task_id="t", session_id="conv-done")
        st.cancelled = False
        engine._maybe_clear_resume_state("conv-done", False, st)
        assert not f.exists()

    def test_cancel_exit_preserves_just_persisted_file(self, engine, data_dir) -> None:
        f = self._persist(data_dir, "conv-cxl")
        st = TaskState(task_id="t", session_id="conv-cxl")
        st.cancelled = True
        engine._maybe_clear_resume_state("conv-cxl", False, st)
        assert f.exists()

    def test_just_persisted_flag_preserves_file_even_if_not_cancelled(
        self, engine, data_dir
    ) -> None:
        """The funnel sets ``state._resume_persisted`` after writing a snapshot;
        the finally-stage clear must honour it even if ``cancelled`` reads
        False (defends against any cancel path that doesn't leave cancelled
        True at finally time)."""
        st = TaskState(task_id="t", session_id="conv-flag")
        st.is_sub_agent = False
        st.cancelled = True
        engine._maybe_persist_cancelled_working_messages(_tool_turn(), st, "claude")
        f = data_dir / "working_messages" / "conv-flag.json"
        assert f.exists()
        assert getattr(st, "_resume_persisted", False) is True

        # Simulate a finally where cancelled has (hypothetically) been cleared:
        st.cancelled = False
        engine._maybe_clear_resume_state("conv-flag", False, st)
        assert f.exists(), "just-persisted snapshot must survive the clear pass"

    def test_sub_agent_exit_does_not_clear_parent(self, engine, data_dir) -> None:
        f = self._persist(data_dir, "conv-parent")
        st = TaskState(task_id="t", session_id="conv-parent")
        st.cancelled = False
        # sub-agent exit must not clear the parent's resume state
        engine._maybe_clear_resume_state("conv-parent", True, st)
        assert f.exists()
