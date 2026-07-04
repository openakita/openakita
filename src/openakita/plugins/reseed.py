"""Plugin re-seed helpers: detect drift between ``plugins/`` (git-tracked seed)
and ``data/plugins/`` (gitignored runtime copy).

The two trees can drift out of sync when a developer edits a plugin under
``plugins/`` but forgets to re-copy the change into ``data/plugins/`` (which is
the path actually loaded by :class:`openakita.plugins.manager.PluginManager`).
This module provides:

* :func:`compute_drift` -- pure-function, per-file diff used by both the
  startup warning and the ``openakita plugins reseed`` CLI.
* :func:`warn_on_drift` -- emits aggregated WARN/INFO log lines at startup so
  operators notice that the runtime is running stale plugin code.

The companion :func:`apply_reseed` (action half) lives in this same module so
the CLI subcommand only has to import from one place.

Only ``.py`` files are compared.  ``__pycache__``, ``.git``, ``.venv``,
``node_modules`` and the per-plugin ``deps/`` vendored-package directory are
skipped entirely (the latter is runtime-only by design and would otherwise
swamp the diff with hundreds of RUNTIME-ONLY entries).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "STATUS_SOURCE_NEWER",
    "STATUS_RUNTIME_NEWER",
    "STATUS_IDENTICAL",
    "STATUS_SOURCE_ONLY",
    "STATUS_RUNTIME_ONLY",
    "EXCLUDED_DIR_NAMES",
    "MTIME_TOLERANCE_SEC",
    "DiffEntry",
    "DriftReport",
    "ApplyResult",
    "compute_drift",
    "warn_on_drift",
    "apply_reseed",
    "format_human_delta",
]

# --- Status constants ---------------------------------------------------------

STATUS_SOURCE_NEWER = "SOURCE-NEWER"
STATUS_RUNTIME_NEWER = "RUNTIME-NEWER"
STATUS_IDENTICAL = "IDENTICAL"
STATUS_SOURCE_ONLY = "SOURCE-ONLY"
STATUS_RUNTIME_ONLY = "RUNTIME-ONLY"

_ALL_STATUSES = (
    STATUS_SOURCE_NEWER,
    STATUS_RUNTIME_NEWER,
    STATUS_IDENTICAL,
    STATUS_SOURCE_ONLY,
    STATUS_RUNTIME_ONLY,
)

# Directories we never descend into when walking either tree.  ``deps/`` is the
# convention used by plugins that vendor pip-installed packages at runtime
# (``pip install -t deps ...`` in their bootstrap) -- they are intentionally
# runtime-only and not part of the git-tracked seed.
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "deps",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
    }
)

# mtime comparison tolerance.  FAT/exFAT volumes only have 2-second mtime
# resolution; we treat anything within +/- 1 second as IDENTICAL to avoid
# false-positive drift alarms after a fresh clone-and-copy.
MTIME_TOLERANCE_SEC: float = 1.0


# --- Data shapes --------------------------------------------------------------


@dataclass(frozen=True)
class DiffEntry:
    """A single per-file row in the drift report."""

    plugin_id: str
    rel_path: str  # path *within* the plugin dir, posix-style (e.g. "tests/x.py")
    status: str
    source_mtime: float | None = None
    runtime_mtime: float | None = None

    @property
    def delta_seconds(self) -> float:
        """Source mtime minus runtime mtime (positive => source is newer)."""
        if self.source_mtime is None or self.runtime_mtime is None:
            return 0.0
        return self.source_mtime - self.runtime_mtime


@dataclass
class DriftReport:
    """Bucketed result of :func:`compute_drift`."""

    by_status: dict[str, list[DiffEntry]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for status in _ALL_STATUSES:
            self.by_status.setdefault(status, [])

    def all_entries(self) -> Iterator[DiffEntry]:
        for status in _ALL_STATUSES:
            yield from self.by_status[status]

    def count(self, status: str) -> int:
        return len(self.by_status.get(status, ()))

    def total(self) -> int:
        return sum(len(v) for v in self.by_status.values())

    @property
    def has_drift(self) -> bool:
        """True iff there is something the CLI would actually copy."""
        return self.count(STATUS_SOURCE_NEWER) > 0 or self.count(STATUS_SOURCE_ONLY) > 0


# --- Pure helpers -------------------------------------------------------------


def _iter_py_files(root: Path, excluded: frozenset[str]) -> Iterator[Path]:
    """Yield every ``*.py`` file under ``root``, pruning ``excluded`` dir names."""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in-place so os.walk does not descend.
        dirnames[:] = [d for d in dirnames if d not in excluded]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _classify(source_mtime: float | None, runtime_mtime: float | None) -> str:
    if source_mtime is None and runtime_mtime is None:
        # Cannot happen for files we actually walked, kept for safety.
        return STATUS_IDENTICAL
    if source_mtime is None:
        return STATUS_RUNTIME_ONLY
    if runtime_mtime is None:
        return STATUS_SOURCE_ONLY
    delta = source_mtime - runtime_mtime
    if delta > MTIME_TOLERANCE_SEC:
        return STATUS_SOURCE_NEWER
    if delta < -MTIME_TOLERANCE_SEC:
        return STATUS_RUNTIME_NEWER
    return STATUS_IDENTICAL


def _plugin_ids(root: Path, plugin_filter: str | None) -> list[str]:
    if not root.is_dir():
        return []
    ids: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in EXCLUDED_DIR_NAMES:
            continue
        if plugin_filter is not None and child.name != plugin_filter:
            continue
        ids.append(child.name)
    return ids


def compute_drift(
    source_root: Path,
    runtime_root: Path,
    *,
    plugin_id: str | None = None,
    excluded_dir_names: Iterable[str] | None = None,
) -> DriftReport:
    """Compare ``source_root`` and ``runtime_root`` per-plugin, per-file.

    Parameters
    ----------
    source_root:
        Path to the git-tracked seed tree (typically ``<project>/plugins``).
    runtime_root:
        Path to the runtime copy that PluginManager actually loads from
        (typically ``<project>/data/plugins``).
    plugin_id:
        Optional plugin-id filter.  When set, only that single sub-directory
        is examined.
    excluded_dir_names:
        Override the default exclusion set.  Mostly useful for unit tests.

    Returns
    -------
    DriftReport
        A bucketed report.  Empty buckets are still present so callers can
        unconditionally index into ``report.by_status``.
    """
    excluded = (
        frozenset(excluded_dir_names) if excluded_dir_names is not None else EXCLUDED_DIR_NAMES
    )
    report = DriftReport()

    source_root = Path(source_root)
    runtime_root = Path(runtime_root)

    # Union of plugin ids that exist on either side.
    ids = set(_plugin_ids(source_root, plugin_id))
    ids.update(_plugin_ids(runtime_root, plugin_id))
    for pid in sorted(ids):
        src_dir = source_root / pid
        rt_dir = runtime_root / pid

        # Build {rel_path: mtime} for each side.
        src_files: dict[str, float] = {}
        for p in _iter_py_files(src_dir, excluded):
            rel = p.relative_to(src_dir).as_posix()
            src_files[rel] = p.stat().st_mtime
        rt_files: dict[str, float] = {}
        for p in _iter_py_files(rt_dir, excluded):
            rel = p.relative_to(rt_dir).as_posix()
            rt_files[rel] = p.stat().st_mtime

        for rel in sorted(set(src_files) | set(rt_files)):
            s_mt = src_files.get(rel)
            r_mt = rt_files.get(rel)
            status = _classify(s_mt, r_mt)
            entry = DiffEntry(
                plugin_id=pid,
                rel_path=rel,
                status=status,
                source_mtime=s_mt,
                runtime_mtime=r_mt,
            )
            report.by_status[status].append(entry)

    return report


def format_human_delta(seconds: float) -> str:
    """Render a seconds delta as a compact human string (``"5m"``, ``"2.1h"``)."""
    seconds = abs(float(seconds))
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


# --- Startup warning ----------------------------------------------------------

#: Maximum per-file WARN lines emitted before we collapse the rest into a
#: single summary line.  Avoids drowning the log when hundreds of files drift
#: (typical when ``data/plugins/`` has not been re-seeded for days).
_MAX_WARN_DETAILS = 10


def warn_on_drift(
    source_root: Path,
    runtime_root: Path,
    logger: logging.Logger,
    *,
    plugin_id: str | None = None,
) -> int:
    """Log WARN/INFO for any drift between source and runtime.

    Returns the number of ``SOURCE-NEWER`` entries reported (handy for tests).
    Emits nothing when the two trees are in sync, so this is safe to call on
    every PluginManager startup -- a healthy deployment stays silent.
    """
    source_root = Path(source_root)
    runtime_root = Path(runtime_root)
    if not source_root.is_dir():
        # No seed tree (e.g. pip-installed prod where only data/plugins/ ships)
        # -- nothing to compare against.
        return 0

    report = compute_drift(source_root, runtime_root, plugin_id=plugin_id)
    source_newer = report.by_status[STATUS_SOURCE_NEWER]
    source_only = report.by_status[STATUS_SOURCE_ONLY]

    for idx, entry in enumerate(source_newer):
        if idx >= _MAX_WARN_DETAILS:
            break
        delta = format_human_delta(entry.delta_seconds)
        runtime_path = f"{runtime_root.name}/{entry.plugin_id}/{entry.rel_path}"
        source_path = f"{source_root.name}/{entry.plugin_id}/{entry.rel_path}"
        logger.warning(
            "[plugin-reseed] %s is OLDER than %s by %s; runtime is running "
            "stale code. Run `openakita plugins reseed --apply` to sync.",
            runtime_path,
            source_path,
            delta,
        )
    if len(source_newer) > _MAX_WARN_DETAILS:
        logger.warning(
            "[plugin-reseed] ... and %d more file(s) where source is newer; "
            "run `openakita plugins reseed` for the full diff.",
            len(source_newer) - _MAX_WARN_DETAILS,
        )

    # Aggregate SOURCE-ONLY by plugin id so a brand-new plugin produces one
    # informational line instead of N (one per file).
    if source_only:
        per_plugin: dict[str, int] = {}
        for entry in source_only:
            per_plugin[entry.plugin_id] = per_plugin.get(entry.plugin_id, 0) + 1
        for pid, n in sorted(per_plugin.items()):
            logger.info(
                "[plugin-reseed] plugin '%s' has %d file(s) in %s/ not yet "
                "seeded to %s/. Run `openakita plugins reseed --apply` to seed.",
                pid,
                n,
                source_root.name,
                runtime_root.name,
            )

    return len(source_newer)


# --- Action -------------------------------------------------------------------


@dataclass
class ApplyResult:
    """Summary of what :func:`apply_reseed` did (or *would* do in dry-run)."""

    copied: list[DiffEntry] = field(default_factory=list)
    skipped_runtime_newer: list[DiffEntry] = field(default_factory=list)
    forced: list[DiffEntry] = field(default_factory=list)
    errors: list[tuple[DiffEntry, str]] = field(default_factory=list)

    @property
    def total_copied(self) -> int:
        return len(self.copied) + len(self.forced)


def apply_reseed(
    source_root: Path,
    runtime_root: Path,
    report: DriftReport,
    *,
    force: bool = False,
    dry_run: bool = True,
) -> ApplyResult:
    """Copy SOURCE-NEWER and SOURCE-ONLY files from source to runtime.

    Parameters
    ----------
    source_root, runtime_root:
        Same meaning as for :func:`compute_drift`.
    report:
        A :class:`DriftReport` produced by :func:`compute_drift`; ``apply_reseed``
        does not re-walk the tree -- callers can compute the diff once and
        re-use it for both the dry-run preview and the apply.
    force:
        When True, also overwrite RUNTIME-NEWER files (the caller has decided
        that the local edit was stale and should be replaced by the seed
        version).  Without ``force`` such files are returned in
        ``skipped_runtime_newer`` so the CLI can print a clear hint.
    dry_run:
        When True (default), no filesystem writes happen; the returned
        :class:`ApplyResult` reports what *would* be copied.

    The copy preserves mtime via :func:`shutil.copy2` so a subsequent
    :func:`compute_drift` reports IDENTICAL.  Parent directories are created
    as needed.
    """
    import shutil

    source_root = Path(source_root)
    runtime_root = Path(runtime_root)
    result = ApplyResult()

    to_copy: list[DiffEntry] = list(report.by_status[STATUS_SOURCE_NEWER])
    to_copy.extend(report.by_status[STATUS_SOURCE_ONLY])

    if force:
        # RUNTIME-NEWER entries are *also* eligible when --force is set.
        to_copy.extend(report.by_status[STATUS_RUNTIME_NEWER])
    else:
        result.skipped_runtime_newer = list(report.by_status[STATUS_RUNTIME_NEWER])

    for entry in to_copy:
        src = source_root / entry.plugin_id / entry.rel_path
        dst = runtime_root / entry.plugin_id / entry.rel_path
        bucket = (
            result.forced
            if entry.status == STATUS_RUNTIME_NEWER
            else result.copied
        )
        if dry_run:
            bucket.append(entry)
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError as exc:
            result.errors.append((entry, str(exc)))
            continue
        bucket.append(entry)

    return result

