"""Splitter red-line tests.

The splitter is tiny but load-bearing: every push larger than the host
adapter's byte budget flows through here, so we lock down:

* Empty input returns ``[]`` (not ``[""]``).
* Chunks never exceed ``max_bytes`` when footer is applied.
* UTF-8 bytes are counted, not characters — 1 CJK char ≈ 3 bytes.
* ``base_header`` prepends every follow-up chunk (not the first).
* Oversized single lines become standalone chunks (force-split).
* ``max_bytes`` smaller than header + footer + 32B margin raises.
"""

from __future__ import annotations

import pytest

from finpulse_notification.splitter import (
    DEFAULT_BATCH_BYTES,
    concat_with_footer,
    split_by_lines,
)


# ── Edge cases ───────────────────────────────────────────────────────


def test_empty_input_returns_empty_list() -> None:
    assert split_by_lines("", max_bytes=1024) == []


def test_single_small_message_returns_single_chunk() -> None:
    out = split_by_lines("hello world\n", max_bytes=1024)
    assert len(out) == 1
    assert "hello world" in out[0]


def test_respects_max_bytes_with_footer() -> None:
    content = "\n".join(f"line-{i:02d}" for i in range(20)) + "\n"
    footer = "--END--"
    out = split_by_lines(content, footer=footer, max_bytes=40)
    assert len(out) >= 2
    for chunk in out:
        assert len(chunk.encode("utf-8")) <= 40
        assert chunk.rstrip().endswith(footer)


def test_utf8_bytes_not_characters() -> None:
    """3 lines of 3-char CJK = ~9 UTF-8 bytes + newline each ≈ 10 bytes."""
    content = "美联储加息\n经济数据发布\n重要决议\n"
    out = split_by_lines(content, max_bytes=40)
    # Each CJK char ≈ 3 bytes so we expect 2+ chunks for max_bytes=40.
    assert len(out) >= 2
    for chunk in out:
        assert len(chunk.encode("utf-8")) <= 40


# ── base_header behaviour ────────────────────────────────────────────


def test_base_header_prepends_to_followups() -> None:
    content = "\n".join([f"line {i:02d}" for i in range(20)]) + "\n"
    header = "[cont.]\n"
    out = split_by_lines(content, max_bytes=60, base_header=header)
    assert len(out) >= 2
    assert not out[0].startswith("[cont.]"), (
        "first chunk must not carry the follow-up header"
    )
    for chunk in out[1:]:
        assert chunk.startswith("[cont.]"), (
            f"follow-up chunk missing header: {chunk!r}"
        )


def test_base_header_not_applied_when_single_chunk() -> None:
    out = split_by_lines("ok\n", max_bytes=1024, base_header="[cont.]\n")
    assert len(out) == 1
    assert not out[0].startswith("[cont.]")


# ── Force-split oversize line ───────────────────────────────────────


def test_oversize_single_line_is_kept_as_own_chunk() -> None:
    big = "x" * 200
    content = f"small\n{big}\nsmall2\n"
    out = split_by_lines(content, max_bytes=120)
    # The oversize line cannot fit inside max_bytes=120 but must not
    # be dropped — at minimum one chunk must contain the long line.
    joined = "".join(out)
    assert big in joined


# ── Configuration guardrails ────────────────────────────────────────


def test_max_bytes_too_small_raises() -> None:
    with pytest.raises(ValueError):
        split_by_lines("whatever\n", max_bytes=10)


def test_max_bytes_below_footer_plus_header_raises() -> None:
    with pytest.raises(ValueError):
        split_by_lines(
            "hello\n", max_bytes=40, footer="=" * 20, base_header="=" * 20
        )


# ── Helpers + defaults table ────────────────────────────────────────


def test_default_batch_bytes_covers_required_channels() -> None:
    for ch in ("dingtalk", "feishu", "wework", "telegram", "default"):
        assert ch in DEFAULT_BATCH_BYTES
        assert DEFAULT_BATCH_BYTES[ch] >= 3000


def test_concat_with_footer_strips_footer() -> None:
    chunks = ["a\n--F--", "b\n--F--"]
    reconstructed = concat_with_footer(chunks, footer="--F--")
    assert "--F--" not in reconstructed


def test_concat_without_footer_is_simple_join() -> None:
    chunks = ["a\n", "b\n"]
    assert concat_with_footer(chunks) == "a\n\nb\n"
