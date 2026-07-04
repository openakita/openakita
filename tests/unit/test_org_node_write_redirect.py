"""Org-scope write sandbox: relative write paths land in the org artifacts dir.

Companion to ``test_org_node_write_guard.py``. The guard rejects ABSOLUTE
writes into the source tree; this redirect makes RELATIVE writes (a bare
``jianlai_points.md`` or ``sub/dir/a.md``) resolve under
``data/orgs/<id>/artifacts/`` instead of the process CWD (repo root), so a node
can never pollute the working directory and can never traverse out of its
sandbox via ``..``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import openakita.orgs._runtime_node_artifacts as _artifacts
from openakita.orgs._runtime_node_tools import (
    _guarded_write_violation,
    _redirect_relative_writes,
)


@pytest.fixture
def org_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the artifacts resolver at a tmp org dir."""
    org_dir = tmp_path / "orgs" / "o1"

    def _fake_resolve(_get, _org_id):  # noqa: ANN001
        return org_dir

    monkeypatch.setattr(_artifacts, "_resolve_org_dir", _fake_resolve)
    return org_dir


def test_relative_write_redirected_into_artifacts(org_root: Path) -> None:
    ti = {"path": "jianlai_points.md", "content": "x"}
    rewrites = _redirect_relative_writes("write_file", ti, "o1")
    assert rewrites, "a relative write should be redirected"
    expected = (org_root / "artifacts" / "jianlai_points.md").resolve()
    assert Path(ti["path"]) == expected
    assert expected.parent == (org_root / "artifacts").resolve()


def test_relative_subdir_preserved(org_root: Path) -> None:
    ti = {"path": "sub/dir/a.md"}
    _redirect_relative_writes("write_file", ti, "o1")
    art = (org_root / "artifacts").resolve()
    assert Path(ti["path"]) == (art / "sub" / "dir" / "a.md").resolve()
    assert Path(ti["path"]).is_relative_to(art)


def test_relative_traversal_is_clamped(org_root: Path) -> None:
    ti = {"path": "../../../../escape.md"}
    _redirect_relative_writes("write_file", ti, "o1")
    art = (org_root / "artifacts").resolve()
    # Escaping ``..`` is clamped to <artifacts>/<basename>; never outside.
    assert Path(ti["path"]) == (art / "escape.md").resolve()
    assert Path(ti["path"]).is_relative_to(art)


def test_absolute_path_not_redirected(org_root: Path) -> None:
    abs = str((org_root.parent / "user_out.md").resolve())
    ti = {"path": abs}
    rewrites = _redirect_relative_writes("write_file", ti, "o1")
    assert rewrites == []
    assert ti["path"] == abs  # untouched -> guard owns absolute paths


def test_move_redirects_dest_not_source(org_root: Path) -> None:
    ti = {"src": "data/orgs/o1/in.md", "dst": "final.md"}
    _redirect_relative_writes("move_file", ti, "o1")
    art = (org_root / "artifacts").resolve()
    assert ti["src"] == "data/orgs/o1/in.md", "read source must not be relocated"
    assert Path(ti["dst"]) == (art / "final.md").resolve()


def test_read_tools_not_redirected(org_root: Path) -> None:
    ti = {"path": "src/openakita/main.py"}
    rewrites = _redirect_relative_writes("read_file", ti, "o1")
    assert rewrites == []
    assert ti["path"] == "src/openakita/main.py"


def test_absolute_source_tree_write_still_blocked() -> None:
    # The guard still rejects ABSOLUTE writes into the package tree even though
    # relative writes are now redirected away from it.
    import openakita

    pkg = Path(openakita.__file__).resolve().parent
    target = str(pkg / "orgs" / "leak.py")
    assert _guarded_write_violation("write_file", {"path": target}) is not None
