"""L1 Unit Tests: reasoning_engine._build_task_checkpoint_event helper.

确保 task_checkpoint SSE event 的容错与字段裁剪行为正确：
- 缺 session / 缺 context / 缺 append 方法 时仍能产出有效 SSE event；
- summary / next_step_hint 单行裁剪到 200 字符；
- 写入成功时返回值与 session.context.task_checkpoints 一致。
"""

from openakita.core.reasoning_engine import _build_task_checkpoint_event
from openakita.sessions.session import Session, SessionContext


class _FakeSession:
    """模拟一个携带 SessionContext 的 Session，用于校验写入路径。"""

    def __init__(self, ctx: SessionContext | None = None) -> None:
        self.context = ctx or SessionContext()


def test_emit_writes_to_session_context():
    sess = _FakeSession()
    sess.context.messages = [{"role": "user", "content": "hi"}] * 5

    ev = _build_task_checkpoint_event(
        session=sess,
        conversation_id="conv-A",
        task_id="t-1",
        iteration=3,
        exit_reason="completed",
        summary="任务结束",
        next_step_hint="下一次再见",
    )

    assert ev["type"] == "task_checkpoint"
    assert ev["task_id"] == "t-1"
    assert ev["iteration"] == 3
    assert ev["exit_reason"] == "completed"
    assert ev["messages_offset"] == 5
    assert sess.context.task_checkpoints == [
        {k: v for k, v in ev.items() if k != "type"}
    ]


def test_emit_handles_none_session():
    ev = _build_task_checkpoint_event(
        session=None,
        conversation_id="conv-B",
        task_id="t-2",
        iteration=0,
        exit_reason="user_cancelled",
    )
    assert ev["type"] == "task_checkpoint"
    assert ev["messages_offset"] == 0
    assert ev["exit_reason"] == "user_cancelled"


def test_emit_handles_session_without_append_method():
    """老 Session 类型可能未升级 — 不应抛错。"""

    class _OldCtx:
        messages: list = []

    class _OldSession:
        def __init__(self) -> None:
            self.context = _OldCtx()

    ev = _build_task_checkpoint_event(
        session=_OldSession(),
        conversation_id="c",
        task_id="t",
        iteration=1,
        exit_reason="running",
    )
    assert ev["type"] == "task_checkpoint"
    assert ev["task_id"] == "t"


def test_summary_and_next_step_truncated_to_200_chars():
    long_text = "A" * 500
    ev = _build_task_checkpoint_event(
        session=None,
        conversation_id="c",
        task_id="t",
        iteration=1,
        exit_reason="completed",
        summary=long_text,
        next_step_hint=long_text,
    )
    assert len(ev["summary"]) == 200
    assert len(ev["next_step_hint"]) == 200
    assert ev["summary"].endswith("…")


def test_summary_strips_newlines_to_single_line():
    multiline = "first line\nsecond line\nthird"
    ev = _build_task_checkpoint_event(
        session=None,
        conversation_id="c",
        task_id="t",
        iteration=1,
        exit_reason="completed",
        summary=multiline,
    )
    assert "\n" not in ev["summary"]
    assert ev["summary"] == "first line second line third"


def test_emit_with_real_session_object():
    sess = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
    ev = _build_task_checkpoint_event(
        session=sess,
        conversation_id="conv",
        task_id="t-real",
        iteration=2,
        exit_reason="budget_paused",
        summary="预算暂停",
    )
    assert ev["type"] == "task_checkpoint"
    assert ev["exit_reason"] == "budget_paused"
    assert sess.context.task_checkpoints[-1]["task_id"] == "t-real"
