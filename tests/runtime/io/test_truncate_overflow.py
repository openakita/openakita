"""Tests for :mod:`openakita.runtime.io.truncate` + ``overflow``.

Both helpers are pure-function / I/O-bounded; tests use ``tmp_path``
to isolate filesystem effects and a fake settings shim to make
the cap configurable per-test.
"""

from __future__ import annotations

from pathlib import Path

from openakita.runtime.io import (
    DEFAULT_TOOL_RESULT_MAX_CHARS,
    cleanup_overflow_files,
    save_overflow,
    smart_truncate,
)


def test_smart_truncate_under_limit_returns_original_and_false() -> None:
    text = "short text"
    out, was = smart_truncate(text, limit=1000, save_full=False)
    assert (out, was) == ("short text", False)


def test_smart_truncate_over_limit_returns_head_marker_tail() -> None:
    src = "a" * 200 + "b" * 200 + "c" * 200  # 600 chars
    out, was = smart_truncate(src, limit=500, head_ratio=0.5, save_full=False)
    assert was is True
    # Head should be `a`s, tail should be `c`s (some of them).
    assert out.startswith("a")
    assert "已截断" in out
    assert out.endswith("c")


def test_smart_truncate_empty_string_returns_unchanged() -> None:
    assert smart_truncate("", limit=100, save_full=False) == ("", False)


def test_smart_truncate_invokes_save_overflow_callback() -> None:
    calls: list[tuple[str, int]] = []

    def fake_save(label: str, content: str) -> str:
        calls.append((label, len(content)))
        return f"/tmp/fake_{label}.txt"

    src = "x" * 1000
    out, was = smart_truncate(
        src, limit=100, label="my_tool", save_overflow_fn=fake_save
    )
    assert was is True
    assert calls == [("my_tool", 1000)]
    assert "/tmp/fake_my_tool.txt" in out


def test_save_overflow_writes_file_and_returns_path(tmp_path: Path) -> None:
    path_str = save_overflow(
        "my_tool", "hello there", directory=tmp_path, max_files=10
    )
    assert path_str
    p = Path(path_str)
    assert p.exists()
    assert p.read_text(encoding="utf-8") == "hello there"


def test_save_overflow_evicts_oldest_when_over_cap(tmp_path: Path) -> None:
    # Pre-populate 3 files; cap at 2; the third save should evict one.
    for i in range(3):
        (tmp_path / f"old_{i}.txt").write_text(str(i), encoding="utf-8")
    save_overflow("new_tool", "fresh", directory=tmp_path, max_files=2)
    files = sorted(tmp_path.glob("*.txt"))
    assert len(files) == 2


def test_cleanup_overflow_files_is_noop_when_under_cap(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    cleanup_overflow_files(tmp_path, max_files=10)
    assert len(list(tmp_path.glob("*.txt"))) == 1


def test_constants_match_legacy_defaults() -> None:
    assert DEFAULT_TOOL_RESULT_MAX_CHARS == 32000
