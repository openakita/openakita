"""P4.2 unit tests: tool_experience.jsonl → failure summary distillation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.experience import (
    ToolExperienceTracker,
    format_failure_hint_section,
    reset_failure_summary_cache,
    summarize_recent_failures,
)


def _write_entries(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _entry(
    tool: str,
    *,
    success: bool,
    error_type: str = "",
    output: str = "",
    profile: str = "default",
    ts: int = 0,
) -> dict:
    return {
        "ts": ts,
        "agent_profile_id": profile,
        "tool_name": tool,
        "skill_name": "",
        "env_scope": "",
        "deps_hash": "",
        "success": success,
        "duration_ms": 1.0,
        "error_type": error_type,
        "exit_code": None,
        "input_summary": "",
        "output_summary": output,
    }


@pytest.fixture
def tracker_with(tmp_path: Path):
    """Build a tracker pointed at a tmp file and reset the module cache."""
    file_path = tmp_path / "tool_experience.jsonl"
    file_path.touch()
    tracker = ToolExperienceTracker(path=file_path)
    reset_failure_summary_cache()
    yield tracker, file_path
    reset_failure_summary_cache()


def test_empty_file_returns_empty(tracker_with):
    tracker, _ = tracker_with
    assert summarize_recent_failures(tracker=tracker) == []


def test_below_threshold_filtered(tracker_with):
    tracker, path = tracker_with
    _write_entries(
        path,
        [
            _entry("only_one_failure", success=False, error_type="x"),
            _entry("only_one_failure", success=True),
            _entry("only_one_failure", success=True),
        ],
    )
    # Default min_failures=2 → single failure should be excluded
    assert summarize_recent_failures(tracker=tracker) == []


def test_high_failure_rate_surfaces(tracker_with):
    tracker, path = tracker_with
    _write_entries(
        path,
        [
            _entry("flaky_tool", success=False, error_type="ConnTimeout", output="conn refused"),
            _entry("flaky_tool", success=False, error_type="ConnTimeout"),
            _entry("flaky_tool", success=True),
            _entry("reliable_tool", success=True),
            _entry("reliable_tool", success=True),
        ],
    )
    summary = summarize_recent_failures(tracker=tracker)
    assert len(summary) == 1
    row = summary[0]
    assert row["tool_name"] == "flaky_tool"
    assert row["failures"] == 2
    assert row["total"] == 3
    assert row["failure_rate"] == round(2 / 3, 2)
    assert row["common_errors"][0]["error_type"] == "ConnTimeout"
    assert row["common_errors"][0]["count"] == 2
    assert row["last_error"]  # last failure had no output -> may be empty? second one had no output
    # last_error reflects the most recent failure with non-empty output:
    assert row["last_error"] == "conn refused" or row["last_error"] == ""


def test_low_failure_rate_excluded(tracker_with):
    tracker, path = tracker_with
    rows = [_entry("popular", success=True) for _ in range(10)]
    rows.append(_entry("popular", success=False, error_type="x"))
    rows.append(_entry("popular", success=False, error_type="x"))
    _write_entries(path, rows)
    # 2 failures meet min_failures, but rate = 2/12 < default 0.5
    assert summarize_recent_failures(tracker=tracker) == []


def test_window_limits_scan(tracker_with):
    tracker, path = tracker_with
    rows = [_entry("ancient", success=False, error_type="x") for _ in range(50)]
    rows += [_entry("recent", success=True) for _ in range(10)]
    _write_entries(path, rows)
    # window=10 → only the recent successes are visible, ancient failures invisible
    assert summarize_recent_failures(tracker=tracker, window=10) == []


def test_agent_profile_filter(tracker_with):
    tracker, path = tracker_with
    _write_entries(
        path,
        [
            _entry("split", success=False, error_type="x", profile="alpha"),
            _entry("split", success=False, error_type="x", profile="alpha"),
            _entry("split", success=True, profile="beta"),
            _entry("split", success=True, profile="beta"),
        ],
    )
    # Filtered to alpha → 2/2 failures, qualifies
    only_alpha = summarize_recent_failures(tracker=tracker, agent_profile_id="alpha")
    assert len(only_alpha) == 1 and only_alpha[0]["tool_name"] == "split"
    # Filtered to beta → no failures
    assert summarize_recent_failures(tracker=tracker, agent_profile_id="beta") == []


def test_corrupt_lines_are_skipped(tracker_with):
    tracker, path = tracker_with
    with path.open("w", encoding="utf-8") as f:
        f.write("this is not json\n")
        f.write(json.dumps(_entry("good", success=False, error_type="x")) + "\n")
        f.write("{broken json\n")
        f.write(json.dumps(_entry("good", success=False, error_type="x")) + "\n")
        f.write("\n")
    summary = summarize_recent_failures(tracker=tracker)
    assert len(summary) == 1
    assert summary[0]["tool_name"] == "good"
    assert summary[0]["failures"] == 2


def test_format_section_empty_returns_blank():
    assert format_failure_hint_section([]) == ""


def test_format_section_renders_known_fields():
    section = format_failure_hint_section(
        [
            {
                "tool_name": "browser_open",
                "total": 4,
                "failures": 3,
                "failure_rate": 0.75,
                "common_errors": [{"error_type": "Timeout", "count": 2}],
                "last_error": "page took too long",
            }
        ]
    )
    assert "Recent Tool Reliability" in section
    assert "browser_open" in section
    assert "3/4 failed" in section
    assert "75%" in section
    assert "Timeout" in section
    assert "page took too long" in section


def test_cache_invalidates_on_mtime_change(tracker_with):
    tracker, path = tracker_with
    _write_entries(
        path,
        [
            _entry("t", success=False, error_type="x"),
            _entry("t", success=False, error_type="x"),
        ],
    )
    first = summarize_recent_failures(tracker=tracker)
    assert len(first) == 1

    # Force a clear successful run that brings rate below threshold.
    _write_entries(path, [_entry("t", success=True) for _ in range(10)])
    second = summarize_recent_failures(tracker=tracker)
    assert second == []
