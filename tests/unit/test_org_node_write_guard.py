"""Isolation guard: org nodes must not write into the OpenAkita source tree.

Regression for the "stray ``src/openakita/orgs/tool_handler.py``" pollution
incident: the node FileTool resolves relative paths under CWD (= repo root in a
source run) and returns absolute paths verbatim, so a node could overwrite the
project's own source. ``_guarded_write_violation`` rejects such writes while
leaving ``data/`` deliverables and user-chosen output paths untouched.
"""

from __future__ import annotations

from openakita.orgs._runtime_node_tools import _guarded_write_violation


def test_blocks_write_into_package_source_tree() -> None:
    assert _guarded_write_violation("write_file", {"path": "src/openakita/orgs/x.py"})
    assert _guarded_write_violation("edit_file", {"path": "src/openakita/main.py"})


def test_blocks_write_into_apps_and_git() -> None:
    assert _guarded_write_violation("write_file", {"path": "apps/setup-center/src/a.tsx"})
    assert _guarded_write_violation("write_file", {"path": ".git/hooks/pre-commit"})


def test_blocks_move_destination_into_source_tree() -> None:
    assert _guarded_write_violation(
        "move_file", {"src": "data/orgs/o1/out.md", "dst": "src/openakita/leak.py"}
    )


def test_allows_data_workspace_and_user_paths() -> None:
    assert _guarded_write_violation("write_file", {"path": "data/orgs/o1/artifacts/out.md"}) is None
    assert _guarded_write_violation("write_file", {"path": "D:/tmp/report.md"}) is None


def test_non_write_tools_are_not_guarded() -> None:
    # read_file targeting source is fine (nodes may inspect code); only writes
    # are guarded.
    assert _guarded_write_violation("read_file", {"path": "src/openakita/main.py"}) is None
