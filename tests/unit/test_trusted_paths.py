"""Fix-11 回归测试：信任路径白名单 + 会话级授权 + risk gate 集成。"""

from __future__ import annotations

import time

import pytest

from openakita.core.trusted_paths import (
    SESSION_KEY,
    consume_session_trust,
    grant_session_trust,
    is_trusted_workspace_path,
)

# ---------------------------------------------------------------------------
# Stub session — minimal API used by trusted_paths helpers.
# ---------------------------------------------------------------------------


class _StubSession:
    def __init__(self, initial: dict | None = None):
        self._meta: dict = dict(initial or {})

    def get_metadata(self, key: str):
        return self._meta.get(key)

    def set_metadata(self, key: str, value):
        if value is None:
            self._meta.pop(key, None)
        else:
            self._meta[key] = value


def _risk(
    *_args,
    **_kwargs,
):
    raise AssertionError("pre-ReAct risk intent skip helpers have been removed")


# ---------------------------------------------------------------------------
# Built-in trusted-path detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "请把 qa_test_2026_05_02/plan.md 删掉",
        "删除 workspaces/ws-1/scratch/foo.txt",
        "把 /tmp/abc.log 移除",
        "删掉 workspaces/main/playground/x",
        "请删除 workspace/temp/foo.md",
    ],
)
def test_is_trusted_workspace_path_true(msg: str):
    assert is_trusted_workspace_path(msg)


@pytest.mark.parametrize(
    "msg",
    [
        "请把 identity/SOUL.md 删掉",
        "删除 data/security/users.yaml",
        "rm -rf /etc/passwd",
        "",
        "随便聊聊天气",
    ],
)
def test_is_trusted_workspace_path_false(msg: str):
    assert not is_trusted_workspace_path(msg)


def test_protected_path_overrides_trusted_match():
    """即使消息里同时出现可信子串，命中 protected 也必须返回 False。"""
    msg = "把 workspaces/main/scratch/foo 删掉，然后 rm -rf /etc/passwd"
    assert not is_trusted_workspace_path(msg)


# ---------------------------------------------------------------------------
# Session-level grant + consume
# ---------------------------------------------------------------------------


def test_grant_and_consume_round_trip():
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    assert consume_session_trust(s, message="删除任意东西", operation="delete") is True


def test_consume_without_grant_returns_false():
    assert consume_session_trust(_StubSession(), message="x", operation="delete") is False


def test_grant_scoped_to_operation():
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    assert consume_session_trust(s, message="x", operation="write") is False
    assert consume_session_trust(s, message="x", operation="delete") is True


def test_grant_with_path_pattern():
    s = _StubSession()
    grant_session_trust(s, operation=None, path_pattern=r"qa_test_\d+")
    assert consume_session_trust(s, message="改 qa_test_001/plan.md", operation="write") is True
    assert consume_session_trust(s, message="改 identity/SOUL.md", operation="write") is False


def test_grant_with_expiry_in_past_is_ignored():
    s = _StubSession()
    grant_session_trust(s, operation="delete", expires_at=time.time() - 60)
    assert consume_session_trust(s, message="x", operation="delete") is False


def test_grant_with_expiry_in_future_is_active():
    s = _StubSession()
    grant_session_trust(s, operation="delete", expires_at=time.time() + 600)
    assert consume_session_trust(s, message="x", operation="delete") is True


def test_grant_persists_in_metadata():
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    assert SESSION_KEY in s._meta
    assert s._meta[SESSION_KEY]["rules"][0]["operation"] == "delete"


def test_consume_does_not_mutate_grant():
    """sticky grant — 同一规则可被消费多次。"""
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    consume_session_trust(s, message="x", operation="delete")
    consume_session_trust(s, message="y", operation="delete")
    assert len(s._meta[SESSION_KEY]["rules"]) == 1
