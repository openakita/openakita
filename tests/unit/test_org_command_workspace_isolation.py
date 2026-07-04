"""Command-level workspace isolation for org node file tools (exploratory v22).

Theme-drift root cause: a node's ``list_directory`` / ``read_file`` discovered a
PRIOR command's on-disk deliverables (an old 《剑来》报告) and anchored a new
《凡人修仙传》task on it. The fix sandboxes BOTH reads and writes into a
per-command workspace ``data/orgs/<id>/commands/<command_id>/artifacts`` so a
fresh command opens an empty workspace and cannot see another command's files.

These tests pin:
* relative WRITES land in the per-command dir when a command_id is present,
* relative READS are sandboxed to the SAME per-command dir,
* ``.`` (root listing) resolves to the per-command dir itself,
* ``..`` traversal is clamped inside the sandbox,
* absolute reads are left untouched,
* with NO command_id the legacy org-artifacts behaviour is preserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import openakita.orgs._runtime_node_artifacts as _artifacts
from openakita.orgs._runtime_node_tools import (
    _command_workspace_dir,
    _redirect_relative_reads,
    _redirect_relative_writes,
)


@pytest.fixture
def org_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    org_dir = tmp_path / "orgs" / "o1"

    def _fake_resolve(_get, _org_id):  # noqa: ANN001
        return org_dir

    monkeypatch.setattr(_artifacts, "_resolve_org_dir", _fake_resolve)
    return org_dir


CMD = "cmd_abc123"


def test_command_workspace_dir_is_per_command(org_root: Path) -> None:
    ws = _command_workspace_dir("o1", CMD)
    assert ws == org_root / "commands" / CMD / "artifacts"
    # No command -> falls back to the org-level artifacts dir (legacy parity).
    assert _command_workspace_dir("o1", None) == org_root / "artifacts"
    assert _command_workspace_dir("o1", "") == org_root / "artifacts"


def test_write_redirected_into_command_workspace(org_root: Path) -> None:
    ti = {"path": "report.md", "content": "x"}
    rewrites = _redirect_relative_writes("write_file", ti, "o1", CMD)
    assert rewrites
    expected = (org_root / "commands" / CMD / "artifacts" / "report.md").resolve()
    assert Path(ti["path"]) == expected


def test_write_without_command_id_uses_org_artifacts(org_root: Path) -> None:
    ti = {"path": "report.md", "content": "x"}
    _redirect_relative_writes("write_file", ti, "o1")
    assert Path(ti["path"]) == (org_root / "artifacts" / "report.md").resolve()


def test_relative_read_sandboxed_to_command_workspace(org_root: Path) -> None:
    ti = {"path": "reports/jianlai_animation_final_report.md"}
    rewrites = _redirect_relative_reads("read_file", ti, "o1", CMD)
    assert rewrites, "a relative read must be sandboxed to the command workspace"
    ws = (org_root / "commands" / CMD / "artifacts").resolve()
    assert Path(ti["path"]) == (ws / "reports" / "jianlai_animation_final_report.md").resolve()
    assert Path(ti["path"]).is_relative_to(ws)


def test_list_directory_root_alias_points_at_command_workspace(org_root: Path) -> None:
    ws = (org_root / "commands" / CMD / "artifacts").resolve()
    for alias in (".", "", "./", "/"):
        ti = {"path": alias}
        _redirect_relative_reads("list_directory", ti, "o1", CMD)
        assert Path(ti["path"]) == ws


def test_read_traversal_is_clamped(org_root: Path) -> None:
    ti = {"path": "../../../../../etc/passwd"}
    _redirect_relative_reads("read_file", ti, "o1", CMD)
    ws = (org_root / "commands" / CMD / "artifacts").resolve()
    # Escaping ``..`` collapses to <ws>/<basename>; never outside the sandbox.
    assert Path(ti["path"]) == (ws / "passwd").resolve()
    assert Path(ti["path"]).is_relative_to(ws)


def test_absolute_read_not_sandboxed(org_root: Path) -> None:
    abs_path = str((org_root / "commands" / "OTHER" / "artifacts" / "x.md").resolve())
    ti = {"path": abs_path}
    rewrites = _redirect_relative_reads("read_file", ti, "o1", CMD)
    assert rewrites == []
    assert ti["path"] == abs_path


def test_read_without_command_id_is_not_sandboxed(org_root: Path) -> None:
    # Legacy/parity: no command_id -> reads keep their old (un-sandboxed) shape.
    ti = {"path": "reports/old.md"}
    rewrites = _redirect_relative_reads("read_file", ti, "o1", None)
    assert rewrites == []
    assert ti["path"] == "reports/old.md"


def test_two_commands_get_disjoint_workspaces(org_root: Path) -> None:
    a = {"path": "."}
    b = {"path": "."}
    _redirect_relative_reads("list_directory", a, "o1", "cmd_jianlai")
    _redirect_relative_reads("list_directory", b, "o1", "cmd_fanren")
    assert a["path"] != b["path"], "different commands must see different workspaces"
    assert "cmd_jianlai" in a["path"]
    assert "cmd_fanren" in b["path"]
