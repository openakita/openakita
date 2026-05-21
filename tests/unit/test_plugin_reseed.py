"""Unit tests for the *action* half of plugin re-seed (hygiene #4).

Covers :func:`openakita.plugins.reseed.apply_reseed` (the pure copier) and
the :class:`openakita.cli.plugins_cmd.plugins_app` Typer sub-app.

The CLI is exercised via :class:`typer.testing.CliRunner` so we never have
to spawn a subprocess.
"""

from __future__ import annotations  # noqa: I001

import os
from pathlib import Path

import pytest

# Workaround for pre-existing openakita import cycle: pre-load ``openakita.agent``
# so ``core.capabilities`` finishes loading before ``plugins.api`` re-enters.
# Without this, running this file in isolation fails at collection time.
import openakita.agent  # noqa: F401  pylint: disable=unused-import

from openakita.cli.plugins_cmd import plugins_app
from openakita.plugins.reseed import (
    STATUS_RUNTIME_NEWER,
    STATUS_SOURCE_NEWER,
    STATUS_SOURCE_ONLY,
    apply_reseed,
    compute_drift,
)
from typer.testing import CliRunner


# --- helpers -----------------------------------------------------------------


def _make_plugin(root: Path, plugin_id: str, files: dict[str, str], *, mtime: float) -> None:
    pdir = root / plugin_id
    pdir.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        target = pdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
        os.utime(target, (mtime, mtime))


@pytest.fixture
def drift_tree(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "plugins"
    rt = tmp_path / "data" / "plugins"
    src.mkdir()
    rt.mkdir(parents=True)
    return src, rt


# --- apply_reseed (pure function) --------------------------------------------


def test_source_newer_detected_then_dry_run_does_not_copy(drift_tree):
    src, rt = drift_tree
    _make_plugin(rt, "demo", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "demo", {"plugin.py": "new content"}, mtime=1_000_500.0)

    report = compute_drift(src, rt)
    assert report.count(STATUS_SOURCE_NEWER) == 1

    result = apply_reseed(src, rt, report, dry_run=True)
    assert len(result.copied) == 1
    # Dry-run: content on disk is still the old version.
    assert (rt / "demo" / "plugin.py").read_text(encoding="utf-8") == "old"


def test_apply_copies_with_mtime_preserved(drift_tree):
    src, rt = drift_tree
    src_mt = 1_700_000_500.0
    _make_plugin(rt, "demo", {"plugin.py": "old"}, mtime=1_700_000_000.0)
    _make_plugin(src, "demo", {"plugin.py": "new content"}, mtime=src_mt)

    report = compute_drift(src, rt)
    result = apply_reseed(src, rt, report, dry_run=False)

    assert len(result.copied) == 1
    runtime_file = rt / "demo" / "plugin.py"
    assert runtime_file.read_text(encoding="utf-8") == "new content"
    # mtime must be preserved (within 1s tolerance for FAT-fs).
    assert abs(runtime_file.stat().st_mtime - src_mt) <= 1.0

    # And a fresh drift check now reports IDENTICAL.
    follow_up = compute_drift(src, rt)
    assert follow_up.count(STATUS_SOURCE_NEWER) == 0
    assert follow_up.count("IDENTICAL") == 1


def test_source_only_file_is_seeded(drift_tree):
    """An entirely-new plugin gets created in data/plugins/ on --apply."""
    src, rt = drift_tree
    _make_plugin(src, "freshly-added", {"plugin.py": "fresh"}, mtime=1_000_000.0)

    report = compute_drift(src, rt)
    assert report.count(STATUS_SOURCE_ONLY) == 1

    result = apply_reseed(src, rt, report, dry_run=False)
    assert len(result.copied) == 1
    assert (rt / "freshly-added" / "plugin.py").read_text(encoding="utf-8") == "fresh"


def test_runtime_newer_protected_without_force(drift_tree):
    """RUNTIME-NEWER files must NOT be overwritten unless ``--force``."""
    src, rt = drift_tree
    _make_plugin(src, "demo", {"plugin.py": "seed"}, mtime=1_000_000.0)
    _make_plugin(rt, "demo", {"plugin.py": "local edit"}, mtime=1_000_500.0)

    report = compute_drift(src, rt)
    assert report.count(STATUS_RUNTIME_NEWER) == 1

    result = apply_reseed(src, rt, report, force=False, dry_run=False)
    assert len(result.copied) == 0
    assert len(result.forced) == 0
    assert len(result.skipped_runtime_newer) == 1
    # Local edit preserved.
    assert (rt / "demo" / "plugin.py").read_text(encoding="utf-8") == "local edit"


def test_force_overrides_runtime_newer(drift_tree):
    src, rt = drift_tree
    _make_plugin(src, "demo", {"plugin.py": "seed wins"}, mtime=1_000_000.0)
    _make_plugin(rt, "demo", {"plugin.py": "local edit"}, mtime=1_000_500.0)

    report = compute_drift(src, rt)
    result = apply_reseed(src, rt, report, force=True, dry_run=False)
    assert len(result.forced) == 1
    assert (rt / "demo" / "plugin.py").read_text(encoding="utf-8") == "seed wins"


def test_plugin_filter_isolates_one_plugin(drift_tree):
    src, rt = drift_tree
    # Two drifting plugins.
    _make_plugin(rt, "alpha", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "alpha", {"plugin.py": "new"}, mtime=1_000_500.0)
    _make_plugin(rt, "beta", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "beta", {"plugin.py": "new"}, mtime=1_000_500.0)

    report = compute_drift(src, rt, plugin_id="alpha")
    assert report.count(STATUS_SOURCE_NEWER) == 1
    assert all(e.plugin_id == "alpha" for e in report.all_entries())

    apply_reseed(src, rt, report, dry_run=False)
    # alpha re-seeded; beta untouched.
    assert (rt / "alpha" / "plugin.py").read_text(encoding="utf-8") == "new"
    assert (rt / "beta" / "plugin.py").read_text(encoding="utf-8") == "old"


# --- CLI surface --------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_cli_dry_run_lists_diff_and_does_not_copy(drift_tree, cli_runner):
    src, rt = drift_tree
    _make_plugin(rt, "demo", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "demo", {"plugin.py": "new"}, mtime=1_000_500.0)

    res = cli_runner.invoke(
        plugins_app,
        ["--source", str(src), "--runtime", str(rt)],
    )

    assert res.exit_code == 0, res.output
    # Dry-run banner present.
    assert "Dry-run only." in res.output
    # Diff table mentions the drifted file.
    assert "SOURCE-NEWER" in res.output
    assert "plugin.py" in res.output
    # Filesystem unchanged.
    assert (rt / "demo" / "plugin.py").read_text(encoding="utf-8") == "old"


def test_cli_apply_actually_copies(drift_tree, cli_runner):
    src, rt = drift_tree
    _make_plugin(rt, "demo", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "demo", {"plugin.py": "new"}, mtime=1_000_500.0)

    res = cli_runner.invoke(
        plugins_app,
        ["--apply", "--source", str(src), "--runtime", str(rt)],
    )

    assert res.exit_code == 0, res.output
    assert "Done." in res.output
    assert (rt / "demo" / "plugin.py").read_text(encoding="utf-8") == "new"


def test_cli_in_sync_reports_ok(drift_tree, cli_runner):
    src, rt = drift_tree
    _make_plugin(src, "demo", {"plugin.py": "v1"}, mtime=1_000_000.0)
    _make_plugin(rt, "demo", {"plugin.py": "v1"}, mtime=1_000_000.0)

    res = cli_runner.invoke(
        plugins_app,
        ["--source", str(src), "--runtime", str(rt)],
    )

    assert res.exit_code == 0, res.output
    assert "in sync" in res.output


def test_cli_plugin_filter(drift_tree, cli_runner):
    src, rt = drift_tree
    _make_plugin(rt, "alpha", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "alpha", {"plugin.py": "new"}, mtime=1_000_500.0)
    _make_plugin(rt, "beta", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "beta", {"plugin.py": "new"}, mtime=1_000_500.0)

    res = cli_runner.invoke(
        plugins_app,
        ["--plugin", "alpha", "--source", str(src), "--runtime", str(rt)],
    )

    assert res.exit_code == 0, res.output
    assert "alpha" in res.output
    # Only alpha is in scope; beta should not appear in the per-bucket
    # tables (it can still be in the IDENTICAL count if the filter let it
    # through, but the diff buckets must not surface it).
    assert "beta" not in res.output


def test_cli_runtime_newer_protected_message(drift_tree, cli_runner):
    src, rt = drift_tree
    _make_plugin(src, "demo", {"plugin.py": "seed"}, mtime=1_000_000.0)
    _make_plugin(rt, "demo", {"plugin.py": "local edit"}, mtime=1_000_500.0)

    res = cli_runner.invoke(
        plugins_app,
        ["--apply", "--source", str(src), "--runtime", str(rt)],
    )

    assert res.exit_code == 0, res.output
    assert "RUNTIME-NEWER" in res.output
    assert "--force" in res.output
    assert (rt / "demo" / "plugin.py").read_text(encoding="utf-8") == "local edit"


def test_cli_force_overrides_runtime_newer(drift_tree, cli_runner):
    src, rt = drift_tree
    _make_plugin(src, "demo", {"plugin.py": "seed wins"}, mtime=1_000_000.0)
    _make_plugin(rt, "demo", {"plugin.py": "local edit"}, mtime=1_000_500.0)

    res = cli_runner.invoke(
        plugins_app,
        ["--apply", "--force", "--source", str(src), "--runtime", str(rt)],
    )

    assert res.exit_code == 0, res.output
    assert (rt / "demo" / "plugin.py").read_text(encoding="utf-8") == "seed wins"


def test_cli_missing_source_dir_exits_with_2(tmp_path, cli_runner):
    res = cli_runner.invoke(
        plugins_app,
        ["--source", str(tmp_path / "does-not-exist"),
         "--runtime", str(tmp_path / "rt")],
    )
    assert res.exit_code == 2
    assert "source dir not found" in res.output
