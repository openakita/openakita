"""Tests for ``openakita.agent.errors``.

The class itself is one constructor and three attributes; the value of
this file is mostly *contract* — proving the move from
``openakita.core.errors`` was non-breaking. Both the new path and the
legacy shim path must yield the same class object so ``except`` blocks
and ``isinstance`` checks across both halves of the codebase keep
working during the Phase 2 → Phase 8 transition.
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


def test_legacy_path_re_exports_same_class() -> None:
    """``openakita.core.errors`` must alias the new class, not duplicate it."""
    from openakita.core.errors import UserCancelledError as Legacy

    assert Legacy is UserCancelledError


def test_lazy_attribute_on_core_package_still_works() -> None:
    """``from openakita.core import UserCancelledError`` keeps working."""
    from openakita import core

    assert core.UserCancelledError is UserCancelledError
