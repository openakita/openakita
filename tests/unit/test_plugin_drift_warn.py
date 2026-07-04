"""Unit tests for :func:`openakita.plugins.reseed.warn_on_drift`.

Drift detection is the *detection* half of the plugin re-seed feature
(hygiene #4 / discovered during F-2).  These tests exercise the WARN/INFO
log surface, the config-flag silencer, the "in-sync stays quiet" guarantee,
and the standard exclusion set (``__pycache__``, ``deps/``, ...).

The :class:`PluginManager` wiring is exercised through a small synthetic
fixture rather than the real ``data/plugins/`` tree so the tests do not
flake on real-world drift.
"""

from __future__ import annotations  # noqa: I001

import logging
import os
from pathlib import Path

import pytest

# isort: off  -- workaround for pre-existing import cycle in openakita.plugins
#                (``core.capabilities`` <-> ``agent`` <-> ``skills.registry``).
# Importing ``openakita.agent`` first forces ``core.capabilities`` to finish
# its module body before ``plugins.api`` re-enters it.  Without this, running
# this test file in isolation fails at collection time; the full-suite
# ``pytest`` invocation happens to load things in the right order already.
import openakita.agent  # noqa: F401  pylint: disable=unused-import

from openakita.plugins.reseed import (
    EXCLUDED_DIR_NAMES,
    MTIME_TOLERANCE_SEC,
    STATUS_SOURCE_NEWER,
    compute_drift,
    warn_on_drift,
)
# isort: on


# --- helpers -----------------------------------------------------------------


def _make_plugin(root: Path, plugin_id: str, files: dict[str, str], *, mtime: float) -> None:
    """Create ``root/<plugin_id>/<rel_path> = body`` for every entry in *files*.

    Every file is stamped with the same ``mtime`` (in epoch-seconds) so we can
    deterministically simulate "source is N seconds newer than runtime".
    """
    pdir = root / plugin_id
    pdir.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        target = pdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
        os.utime(target, (mtime, mtime))


@pytest.fixture
def drift_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Standard fixture: ``tmp_path/plugins/`` + ``tmp_path/data/plugins/``."""
    src = tmp_path / "plugins"
    rt = tmp_path / "data" / "plugins"
    src.mkdir()
    rt.mkdir(parents=True)
    return src, rt


# --- warn_on_drift behaviour --------------------------------------------------


def test_warn_emitted_when_source_newer(drift_tree: tuple[Path, Path], caplog):
    src, rt = drift_tree
    _make_plugin(rt, "demo", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "demo", {"plugin.py": "new"}, mtime=1_000_500.0)

    caplog.set_level(logging.WARNING, logger="t.warn")
    logger = logging.getLogger("t.warn")
    n = warn_on_drift(src, rt, logger)

    assert n == 1, "exactly one SOURCE-NEWER entry expected"
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    msg = warns[0].getMessage()
    assert "[plugin-reseed]" in msg
    assert "plugin.py" in msg
    assert "openakita plugins reseed --apply" in msg


def test_no_warn_when_in_sync(drift_tree: tuple[Path, Path], caplog):
    src, rt = drift_tree
    same = 1_700_000_000.0
    _make_plugin(src, "demo", {"plugin.py": "v1"}, mtime=same)
    _make_plugin(rt, "demo", {"plugin.py": "v1"}, mtime=same)

    caplog.set_level(logging.DEBUG, logger="t.sync")
    logger = logging.getLogger("t.sync")
    n = warn_on_drift(src, rt, logger)

    assert n == 0
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_source_only_logs_info_not_warning(drift_tree: tuple[Path, Path], caplog):
    """A brand-new plugin in ``plugins/`` but absent from ``data/plugins/`` should
    surface as an INFO line ("not yet seeded"), never a WARN."""
    src, rt = drift_tree
    _make_plugin(
        src,
        "freshly-added",
        {"plugin.py": "x", "tests/test_x.py": "y"},
        mtime=1_000_000.0,
    )

    caplog.set_level(logging.INFO, logger="t.so")
    logger = logging.getLogger("t.so")
    n = warn_on_drift(src, rt, logger)

    assert n == 0
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warns) == 0
    # Aggregated: one INFO per plugin, not per file.
    assert len(infos) == 1
    assert "freshly-added" in infos[0].getMessage()
    assert "2 file(s)" in infos[0].getMessage()


def test_missing_source_tree_is_silent(tmp_path: Path, caplog):
    """Production deployments without the seed tree must not emit anything."""
    rt = tmp_path / "data" / "plugins"
    rt.mkdir(parents=True)
    _make_plugin(rt, "demo", {"plugin.py": "runtime-only"}, mtime=1_000_000.0)

    caplog.set_level(logging.DEBUG, logger="t.nosrc")
    logger = logging.getLogger("t.nosrc")
    n = warn_on_drift(tmp_path / "plugins", rt, logger)

    assert n == 0
    assert caplog.records == []


# --- config-flag silencer (mirror of manager._maybe_warn_on_source_drift) ----


def test_silenced_when_flag_disabled(drift_tree: tuple[Path, Path], monkeypatch, caplog):
    """When ``plugins_drift_warn_enabled=False`` the PluginManager hook must
    not call ``warn_on_drift`` at all -- we assert this by spying on the
    symbol the manager imports."""
    src, rt = drift_tree
    _make_plugin(rt, "demo", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "demo", {"plugin.py": "new"}, mtime=1_000_500.0)

    from openakita.config import settings
    from openakita.plugins import reseed as _reseed

    calls: list[tuple[Path, Path]] = []

    def _spy(source_root, runtime_root, logger, **kwargs):  # noqa: ARG001
        calls.append((source_root, runtime_root))
        return 0

    monkeypatch.setattr(_reseed, "warn_on_drift", _spy)
    monkeypatch.setattr(settings, "plugins_drift_warn_enabled", False, raising=False)

    from openakita.plugins.manager import PluginManager

    pm = PluginManager(plugins_dir=rt)
    pm._maybe_warn_on_source_drift()

    assert calls == [], "warn_on_drift must not run when the flag is False"


def test_flag_default_true_invokes_warn(drift_tree: tuple[Path, Path], monkeypatch):
    """Sanity: when the flag is True the helper does invoke warn_on_drift."""
    src, rt = drift_tree
    _make_plugin(rt, "demo", {"plugin.py": "old"}, mtime=1_000_000.0)
    _make_plugin(src, "demo", {"plugin.py": "new"}, mtime=1_000_500.0)

    from openakita.config import settings
    from openakita.plugins import reseed as _reseed
    from openakita.plugins.manager import PluginManager

    calls: list[tuple[Path, Path]] = []

    def _spy(source_root, runtime_root, logger, **kwargs):  # noqa: ARG001
        calls.append((source_root, runtime_root))
        return 1

    monkeypatch.setattr(_reseed, "warn_on_drift", _spy)
    monkeypatch.setattr(settings, "plugins_drift_warn_enabled", True, raising=False)

    pm = PluginManager(plugins_dir=rt)
    pm._maybe_warn_on_source_drift()

    assert len(calls) == 1
    assert calls[0][1].resolve() == rt.resolve()


# --- compute_drift edge cases consumed by the warn surface --------------------


def test_excluded_dirs_are_skipped(drift_tree: tuple[Path, Path]):
    """``deps/`` and ``__pycache__`` must not produce drift entries."""
    src, rt = drift_tree
    _make_plugin(
        rt,
        "demo",
        {
            "plugin.py": "ok",
            "deps/anyio/_x.py": "vendored",
            "__pycache__/plugin.cpython-311.pyc.py": "bytecode-ish",
        },
        mtime=1_000_000.0,
    )
    _make_plugin(src, "demo", {"plugin.py": "ok"}, mtime=1_000_000.0)

    report = compute_drift(src, rt)
    all_paths = [e.rel_path for e in report.all_entries()]
    assert all_paths == ["plugin.py"]
    for d in ("__pycache__", "deps", ".git", ".venv"):
        assert d in EXCLUDED_DIR_NAMES


def test_mtime_tolerance_classifies_as_identical(drift_tree: tuple[Path, Path]):
    """Sub-second skew must not be reported as drift (FAT-fs guard)."""
    src, rt = drift_tree
    base = 1_700_000_000.0
    _make_plugin(src, "demo", {"plugin.py": "v1"}, mtime=base + MTIME_TOLERANCE_SEC / 2.0)
    _make_plugin(rt, "demo", {"plugin.py": "v1"}, mtime=base)

    report = compute_drift(src, rt)
    assert report.count(STATUS_SOURCE_NEWER) == 0
    assert report.count("IDENTICAL") == 1
