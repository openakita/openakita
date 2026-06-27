from openakita.api.routes.chat import _should_emit_resume_task_idle


def test_resume_task_idle_requires_terminal_event() -> None:
    assert (
        _should_emit_resume_task_idle(
            busy=False,
            terminal_seen=False,
            seconds_since_event=30.0,
        )
        is False
    )


def test_resume_task_idle_emits_after_terminal_and_idle() -> None:
    assert (
        _should_emit_resume_task_idle(
            busy=False,
            terminal_seen=True,
            seconds_since_event=1.1,
        )
        is True
    )


def test_resume_task_idle_does_not_emit_while_busy() -> None:
    assert (
        _should_emit_resume_task_idle(
            busy=True,
            terminal_seen=True,
            seconds_since_event=30.0,
        )
        is False
    )
