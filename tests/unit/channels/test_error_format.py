"""PR-E1: IM 对用户可见错误的脱敏格式。"""

from __future__ import annotations


def test_format_user_error_strips_slice_repr():
    from openakita.channels.gateway import _format_user_error

    class SliceArgs(Exception):
        pass

    e = SliceArgs(slice(None, 200, None))
    out = _format_user_error(e)
    assert "slice(" not in out
    assert len(out) > 0


def test_format_user_error_plain_string_passes_through_friendly_branch():
    from openakita.channels.gateway import _format_user_error

    out = _format_user_error("connection reset")
    assert "slice(" not in out
    assert "⚠️" in out or "出错" in out or "连接" in out or "网络" in out
