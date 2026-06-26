"""Fix-15 回归测试：调度任务命名校验 + 历史 quarantine。"""

from __future__ import annotations

import pytest

from openakita.scheduler._naming import (
    FORBIDDEN_TOKENS,
    QUARANTINE_PREFIX,
    is_quarantined,
    quarantine_invalid_task_name,
    validate_task_name,
)


# ---------------------------------------------------------------------------
# validate_task_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good_name",
    [
        "每天早晨提醒",
        "Daily Standup",
        "task_001",
        "Q1 Plan Review",
        "周会-技术",
    ],
)
def test_validate_accepts_good_names(good_name: str):
    ok, _ = validate_task_name(good_name)
    assert ok


@pytest.mark.parametrize(
    "bad_name,expected_kw",
    [
        ("../etc/passwd", "非法字符"),
        ("a/b/c", "非法字符"),
        ("a\\b\\c", "非法字符"),
        ("with:colon", "非法字符"),
        ("name<x>", "非法字符"),
        ("starlit*", "非法字符"),
        ("question?", "非法字符"),
        ("nul\x00byte", "非法字符"),
        ("", "不能为空"),
        ("   ", "不能为空"),
        ("x" * 201, "长度不能超过"),
    ],
)
def test_validate_rejects_bad_names(bad_name: str, expected_kw: str):
    ok, reason = validate_task_name(bad_name)
    assert not ok
    assert expected_kw in reason


def test_validate_none_means_unset_ok():
    ok, _ = validate_task_name(None)
    assert ok


def test_validate_non_string_rejected():
    ok, reason = validate_task_name(12345)  # type: ignore[arg-type]
    assert not ok
    assert "字符串" in reason


def test_validate_already_quarantined_name_passes():
    """重启时再次跑 validator 不应反复 quarantine 同一个名字。"""
    quarantined = f"{QUARANTINE_PREFIX}foo_abc1234567"
    ok, _ = validate_task_name(quarantined)
    assert ok


def test_forbidden_tokens_immutable_tuple():
    assert isinstance(FORBIDDEN_TOKENS, tuple)
    assert ".." in FORBIDDEN_TOKENS
    assert "/" in FORBIDDEN_TOKENS
    assert "\\" in FORBIDDEN_TOKENS


# ---------------------------------------------------------------------------
# quarantine_invalid_task_name
# ---------------------------------------------------------------------------


def test_quarantine_returns_none_for_valid_name():
    assert quarantine_invalid_task_name("Daily Standup") is None


def test_quarantine_rewrites_path_traversal():
    new = quarantine_invalid_task_name("../foo")
    assert new is not None
    assert new.startswith(QUARANTINE_PREFIX)
    assert "../" not in new
    assert is_quarantined(new)


def test_quarantine_idempotent_on_already_quarantined():
    name = f"{QUARANTINE_PREFIX}foo_abc1234567"
    assert quarantine_invalid_task_name(name) is None


def test_quarantine_md5_suffix_is_deterministic():
    a = quarantine_invalid_task_name("../same")
    b = quarantine_invalid_task_name("../same")
    assert a == b


def test_quarantine_distinguishes_different_inputs():
    a = quarantine_invalid_task_name("../alpha")
    b = quarantine_invalid_task_name("../beta")
    assert a != b


def test_quarantine_is_self_validating():
    """quarantined name must itself pass the validator (avoid loops)."""
    new = quarantine_invalid_task_name("a/b\\c<x>?")
    assert new is not None
    ok, _ = validate_task_name(new)
    assert ok


def test_quarantine_handles_empty_string_input():
    """空字符串虽然不合法，但 quarantine 应给出兜底名字。"""
    new = quarantine_invalid_task_name("")
    # 空字符串不会进 quarantine 路径（validate 已经返回 ok=False，
    # 但实际上 validate 把空字符串视为 invalid，所以会被 rewrite）
    assert new is not None
    assert new.startswith(QUARANTINE_PREFIX)


def test_is_quarantined_recognises_prefix():
    assert is_quarantined(f"{QUARANTINE_PREFIX}foo")
    assert not is_quarantined("foo")
    assert not is_quarantined(None)
    assert not is_quarantined(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Scheduler 集成：add_task 拒绝非法 name
# ---------------------------------------------------------------------------


def test_scheduler_add_task_rejects_invalid_name():
    """add_task 在 lock 之前就应该拒绝（防止后续路径用到非法 name）。"""
    import asyncio

    from openakita.scheduler.scheduler import TaskScheduler
    from openakita.scheduler.task import ScheduledTask

    sched = TaskScheduler.__new__(TaskScheduler)
    sched._lock = asyncio.Lock()
    sched._tasks = {}
    sched._triggers = {}

    bad = ScheduledTask.create(
        name="../escape",
        task_type=__import__("openakita.scheduler.task", fromlist=["TaskType"]).TaskType.REMINDER,
        trigger_type=__import__(
            "openakita.scheduler.task", fromlist=["TriggerType"]
        ).TriggerType.ONCE,
        trigger_config={"run_at": "2030-01-01T00:00:00Z"},
        prompt="x",
        description="x",
    )

    with pytest.raises(ValueError, match="Invalid task name"):
        asyncio.run(sched.add_task(bad))
