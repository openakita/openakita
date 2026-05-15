"""Unit tests for ``core.policy_v2.path_placeholders``.

Covers the centralised path-placeholder resolver. Most importantly,
:func:`test_dollar_cwd_with_suffix_is_expanded_regression_bug_p0`
captures the **P0 security bug** that lived in
``schema.PolicyConfigV2.expand_placeholders`` before Stage 0: strict-
equality ``if p == "${CWD}":`` meant any path containing ``${CWD}/...``
suffix was returned **unchanged**, silently entering the engine as a
literal string and never matching real absolute paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.core.policy_v2.path_placeholders import (
    SUPPORTED_PLACEHOLDERS,
    resolve_path_list,
    resolve_path_template,
)


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    """Pinned workspace root for deterministic ``${CWD}`` expansion."""
    return tmp_path


# ---------------------------------------------------------------------------
# Regression: P0 bug — ``${CWD}/...`` was not expanded under strict equality
# ---------------------------------------------------------------------------


def test_dollar_cwd_exact_match_expanded(cwd: Path) -> None:
    """Sanity: ``${CWD}`` alone still expands (old code path)."""
    got = resolve_path_template("${CWD}", cwd=cwd)
    assert got == str(cwd).replace("\\", "/")


def test_dollar_cwd_with_suffix_is_expanded_regression_bug_p0(cwd: Path) -> None:
    """**P0 regression**: ``${CWD}/secrets/**`` must expand to ``<cwd>/secrets/**``.

    Before Stage 0, ``schema.expand_placeholders`` used ``if p == "${CWD}":``
    strict equality and returned the literal string unchanged for any path
    with a suffix. This caused user-defined ``safety_immune.paths`` entries
    like ``"${CWD}/secrets/**"`` to be silently inert — engine prefix-match
    against real absolute paths could never hit.
    """
    got = resolve_path_template("${CWD}/secrets/**", cwd=cwd)
    expected = f"{str(cwd).replace(chr(92), '/')}/secrets/**"
    assert got == expected, (
        f"Bug regression: ${{CWD}}/secrets/** should expand to {expected!r}, "
        f"got {got!r}. If this fails, the silent-no-op bug is back."
    )


def test_dollar_cwd_with_multiple_segments(cwd: Path) -> None:
    got = resolve_path_template("${CWD}/data/audit/log.jsonl", cwd=cwd)
    expected = f"{str(cwd).replace(chr(92), '/')}/data/audit/log.jsonl"
    assert got == expected


# ---------------------------------------------------------------------------
# ``~`` and ``${HOME}`` expansion (aliases)
# ---------------------------------------------------------------------------


def test_tilde_slash_expanded(cwd: Path) -> None:
    got = resolve_path_template("~/.ssh/id_rsa", cwd=cwd)
    expected = str(Path.home() / ".ssh/id_rsa").replace("\\", "/")
    assert got == expected


def test_tilde_alone_expanded(cwd: Path) -> None:
    got = resolve_path_template("~", cwd=cwd)
    expected = str(Path.home()).replace("\\", "/")
    assert got == expected


def test_dollar_home_alias(cwd: Path) -> None:
    """``${HOME}`` should resolve identically to ``~`` for the same home."""
    got_dollar = resolve_path_template("${HOME}/.ssh/id_rsa", cwd=cwd)
    got_tilde = resolve_path_template("~/.ssh/id_rsa", cwd=cwd)
    assert got_dollar == got_tilde


def test_dollar_home_explicit_override(cwd: Path, tmp_path: Path) -> None:
    """Caller-provided ``home`` overrides ``Path.home()``."""
    fake_home = tmp_path / "fake_home"
    got = resolve_path_template("${HOME}/cfg.yaml", cwd=cwd, home=fake_home)
    expected = f"{str(fake_home).replace(chr(92), '/')}/cfg.yaml"
    assert got == expected


# ---------------------------------------------------------------------------
# ``${WORKSPACE}`` placeholder (defaults to cwd for now)
# ---------------------------------------------------------------------------


def test_dollar_workspace_falls_back_to_cwd(cwd: Path) -> None:
    """Without explicit ``workspace`` arg, ``${WORKSPACE}`` == ``${CWD}``."""
    got_ws = resolve_path_template("${WORKSPACE}/foo", cwd=cwd)
    got_cwd = resolve_path_template("${CWD}/foo", cwd=cwd)
    assert got_ws == got_cwd


def test_dollar_workspace_explicit_override(cwd: Path, tmp_path: Path) -> None:
    ws_root = tmp_path / "setup_center_ws"
    got = resolve_path_template("${WORKSPACE}/proj/file", cwd=cwd, workspace=ws_root)
    expected = f"{str(ws_root).replace(chr(92), '/')}/proj/file"
    assert got == expected


# ---------------------------------------------------------------------------
# Literal paths must pass through unchanged (modulo backslash normalisation)
# ---------------------------------------------------------------------------


def test_literal_posix_path_unchanged(cwd: Path) -> None:
    assert resolve_path_template("/etc/passwd", cwd=cwd) == "/etc/passwd"
    assert resolve_path_template("/etc/**", cwd=cwd) == "/etc/**"


def test_literal_windows_path_normalised(cwd: Path) -> None:
    """Backslashes → forward slashes for consistency with engine match."""
    got = resolve_path_template("C:\\Windows\\System32", cwd=cwd)
    assert got == "C:/Windows/System32"


def test_literal_windows_glob_pattern(cwd: Path) -> None:
    got = resolve_path_template("C:\\Program Files\\**", cwd=cwd)
    assert got == "C:/Program Files/**"


# ---------------------------------------------------------------------------
# Mixed / combined patterns
# ---------------------------------------------------------------------------


def test_cwd_inside_glob_pattern(cwd: Path) -> None:
    """Glob anchor on ``${CWD}/...`` still expands."""
    got = resolve_path_template("${CWD}/identity/runtime/**", cwd=cwd)
    expected = f"{str(cwd).replace(chr(92), '/')}/identity/runtime/**"
    assert got == expected


def test_cwd_in_middle_of_path(cwd: Path) -> None:
    """``${CWD}`` can appear anywhere, not just at the start."""
    got = resolve_path_template("prefix/${CWD}/suffix", cwd=cwd)
    expected = f"prefix/{str(cwd).replace(chr(92), '/')}/suffix"
    assert got == expected


# ---------------------------------------------------------------------------
# resolve_path_list batch helper
# ---------------------------------------------------------------------------


def test_resolve_path_list_preserves_order(cwd: Path) -> None:
    raw = ["${CWD}/a", "/etc/passwd", "~/.ssh", "${HOME}/.aws/credentials"]
    got = resolve_path_list(raw, cwd=cwd)
    assert len(got) == 4
    cwd_str = str(cwd).replace("\\", "/")
    home_str = str(Path.home()).replace("\\", "/")
    assert got[0] == f"{cwd_str}/a"
    assert got[1] == "/etc/passwd"
    assert got[2] == home_str + "/.ssh"
    assert got[3] == f"{home_str}/.aws/credentials"


def test_resolve_path_list_empty() -> None:
    assert resolve_path_list([], cwd=Path("/tmp")) == []


# ---------------------------------------------------------------------------
# Determinism + idempotency
# ---------------------------------------------------------------------------


def test_resolve_idempotent_on_already_resolved(cwd: Path) -> None:
    """Resolving an already-resolved path is a no-op (forward-slash normalised)."""
    first = resolve_path_template("${CWD}/foo", cwd=cwd)
    second = resolve_path_template(first, cwd=cwd)
    assert first == second


def test_supported_placeholders_contract() -> None:
    """``SUPPORTED_PLACEHOLDERS`` documents the canonical set."""
    assert "${CWD}" in SUPPORTED_PLACEHOLDERS
    assert "${HOME}" in SUPPORTED_PLACEHOLDERS
    assert "${WORKSPACE}" in SUPPORTED_PLACEHOLDERS


# ---------------------------------------------------------------------------
# Edge cases (4th-round audit补完)
# ---------------------------------------------------------------------------


def test_multiple_distinct_placeholders_in_one_string(cwd: Path, tmp_path: Path) -> None:
    """``${CWD}`` 与 ``${HOME}`` 同字符串内并存，两者应各自正确替换。"""
    fake_home = tmp_path / "home"
    got = resolve_path_template("${CWD}/data/${HOME}/file", cwd=cwd, home=fake_home)
    cwd_s = str(cwd).replace("\\", "/")
    home_s = str(fake_home).replace("\\", "/")
    assert got == f"{cwd_s}/data/{home_s}/file"


def test_same_placeholder_repeated(cwd: Path) -> None:
    """同一占位符多次出现，``.replace()`` 应替换全部（非 first-only）。"""
    got = resolve_path_template("${CWD}/a/${CWD}/b", cwd=cwd)
    cwd_s = str(cwd).replace("\\", "/")
    assert got == f"{cwd_s}/a/{cwd_s}/b"


def test_placeholder_case_sensitive(cwd: Path) -> None:
    """``${cwd}`` (小写) 不应被替换——防止有人误以为 case-insensitive。"""
    got = resolve_path_template("${cwd}/foo", cwd=cwd)
    assert got == "${cwd}/foo"


def test_empty_string_returns_empty(cwd: Path) -> None:
    """空字符串走完全部分支不应崩。"""
    assert resolve_path_template("", cwd=cwd) == ""


def test_resolve_path_list_with_mixed_empty_and_real(cwd: Path) -> None:
    """混合空字符串、占位符和字面量。"""
    raw = ["", "${CWD}/foo", "/etc/passwd"]
    got = resolve_path_list(raw, cwd=cwd)
    cwd_s = str(cwd).replace("\\", "/")
    assert got == ["", f"{cwd_s}/foo", "/etc/passwd"]


# ---------------------------------------------------------------------------
# Contract with downstream ``engine._normalize_path``
#
# Our resolver normalises backslashes only; the engine's ``_path_under`` does
# additional canonicalisation (multi-slash, casefold, trailing slash). The
# two are complementary by design — but if anyone later adds a competing
# normalisation step here, audit-log path strings would drift. This test
# locks the contract: our output, fed back into engine._normalize_path, is
# a fixed-point.
# ---------------------------------------------------------------------------


def test_resolver_output_is_fixed_point_under_engine_normalize(cwd: Path) -> None:
    """``_normalize_path(resolve_path_template(x)) == _normalize_path(resolve_path_template(x))``.

    即：我们的输出再经 engine 的内部归一化，**不会再变化**（除大小写/多斜杠/
    trailing slash 这些 engine 该做的事）。这条契约守护"我归一化的不重复也不漏"。
    """
    from openakita.core.policy_v2.engine import _normalize_path

    samples = [
        "${CWD}/identity/SOUL.md",
        "${CWD}/data/audit/**",
        "~/.ssh/id_rsa",
        "C:\\Program Files\\App\\bin",
        "/etc/passwd",
    ]
    for s in samples:
        once = _normalize_path(resolve_path_template(s, cwd=cwd))
        twice = _normalize_path(resolve_path_template(once, cwd=cwd))
        assert once == twice, (
            f"Drift detected for {s!r}: {once!r} → {twice!r}. "
            "Either path_placeholders.resolve_path_template grew a new "
            "normalisation step or engine._normalize_path did. Both layers "
            "must agree on the canonical form (forward slash, lowercase, "
            "single slashes, no trailing slash)."
        )
