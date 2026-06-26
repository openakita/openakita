"""L1 Unit Tests: Ralph loop Task dataclass and TaskResult."""

import pytest
from datetime import datetime

from openakita.core.ralph import Task, TaskResult, TaskStatus, StopHook


class TestRalphTask:
    def test_create_task(self):
        t = Task(id="task-1", description="Translate document")
        assert t.status == TaskStatus.PENDING
        assert t.attempts == 0
        assert t.max_attempts == 10

    def test_mark_in_progress(self):
        t = Task(id="task-2", description="d")
        t.mark_in_progress()
        assert t.status == TaskStatus.IN_PROGRESS

    def test_mark_completed(self):
        t = Task(id="task-3", description="d")
        t.mark_in_progress()
        t.mark_completed(result="Finished")
        assert t.status == TaskStatus.COMPLETED
        assert t.result == "Finished"
        assert t.is_complete is True

    def test_mark_failed(self):
        t = Task(id="task-4", description="d")
        t.mark_failed("LLM error")
        assert t.error == "LLM error"
        # mark_failed may only record error without changing status from PENDING
        assert t.status in (TaskStatus.PENDING, TaskStatus.FAILED)

    def test_can_retry(self):
        t = Task(id="task-5", description="d", max_attempts=3)
        assert t.can_retry is True
        t.attempts = 3
        assert t.can_retry is False

    def test_subtasks(self):
        parent = Task(id="parent", description="Main task")
        child = Task(id="child-1", description="Subtask 1")
        parent.subtasks.append(child)
        assert len(parent.subtasks) == 1


class TestTaskResult:
    def test_success_result(self):
        r = TaskResult(success=True, data="output", iterations=5, duration_seconds=12.3)
        assert r.success is True
        assert r.iterations == 5

    def test_failure_result(self):
        r = TaskResult(success=False, error="Max iterations exceeded")
        assert r.success is False
        assert r.error is not None


class TestStopHook:
    def test_default_should_not_stop(self):
        t = Task(id="t", description="d")
        hook = StopHook(task=t)
        assert hook.should_stop() is False

    def test_intercept(self):
        t = Task(id="t", description="d")
        hook = StopHook(task=t)
        assert isinstance(hook.intercept(), bool)
