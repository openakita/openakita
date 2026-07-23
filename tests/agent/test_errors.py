"""Tests for ``openakita.agent.errors``.

The class itself is one constructor and three attributes; these tests pin
its public construction contract and canonical module.
"""

from __future__ import annotations

from openakita.agent.errors import UserCancelledError


def test_basic_construction() -> None:
    err = UserCancelledError()
    assert isinstance(err, Exception)
    assert err.reason == ""
    assert err.source == ""


def test_construction_with_reason_and_source() -> None:
    err = UserCancelledError(reason="用户按了取消", source="cli")
    assert err.reason == "用户按了取消"
    assert err.source == "cli"
    assert "取消" in str(err)
    assert "cli" in str(err)


def test_error_has_canonical_module() -> None:
    assert UserCancelledError.__module__ == "openakita.agent.errors"
