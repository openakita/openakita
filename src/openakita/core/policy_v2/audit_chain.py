"""C16 Phase C — Tamper-evident JSONL audit chain.

Replaces plain ``open(path, "a")`` audit writers across the policy_v2
surface with ``ChainedJsonlWriter``: each record carries ``prev_hash`` and
``row_hash``, computed as SHA-256 over the canonical JSON of the record
(``row_hash`` excluded from its own input). Tampering with any past row
breaks the chain at that exact line, detectable via ``verify_chain``.

Design constraints
------------------

* Single-writer-per-file (per process). A process-level
  ``threading.Lock`` plus a singleton-per-path map (``_WRITERS``) keeps
  intra-process appends consistent.
* **C17 Phase E.1**: ``filelock.FileLock`` now provides cross-process
  serialization. Each ``append()`` acquires the filelock, re-reads the
  last row_hash from disk (since a sibling process may have appended in
  the gap between our last write and now), enriches the new row, writes,
  and releases. The lock file lives next to the JSONL with ``.lock``
  suffix. When ``filelock`` is unavailable, a small stdlib OS file-lock
  fallback preserves the same cross-process serialization contract.
* Crash recovery: if the previous run died mid-write, the file may end on
  a partial JSON line. ``ChainedJsonlWriter`` detects this on open,
  truncates the partial bytes (only when the file does *not* end in
  newline), warns, and resumes from the last full line.
* Legacy prefix: existing audit files written before C16 lack
  ``row_hash``. The writer bootstraps from ``GENESIS_HASH`` at first
  append; ``verify_chain`` reports the legacy prefix length separately
  from tamper events.
* Deterministic serialization: ``json.dumps(sort_keys=True,
  separators=(",", ":"), ensure_ascii=False)``. ``ts`` is a float —
  CPython's ``repr(float)`` is deterministic across 3.1+, so re-hashing
  on read matches the stored ``row_hash``.
* Fail-closed on tamper from the *reader*'s perspective: ``verify_chain``
  returns ``ok=False`` and the offending line index; the writer keeps
  appending so the audit never stops collecting evidence, but the
  verifier surfaces "this file is no longer trustworthy".

C17 二轮 audit tail-window 修复
-------------------------------

The original C17 code used a fixed 64 KB tail window for both
``_bootstrap`` and ``_reload_last_hash_from_disk``. If a single audit row
exceeds that (realistic for ``ParamMutationAuditor`` writes carrying
large ``before``/``after`` payloads, even after ``_sanitize_for_chain``
truncates strings), the tail read would land in the middle of a row and
:func:`_reload_last_hash_from_disk` would silently fail to update
``_last_hash`` — meaning the *next* append would chain off a stale hash
and produce a verify_chain mismatch. We now scan backwards in doubling
chunks via :func:`_read_last_complete_line` up to a 16 MiB hard cap, so
any reasonable single record is recoverable while pathological lines
(>16 MiB) degrade explicitly rather than corrupting the chain.

C20 — JSONL rotation
--------------------

The writer optionally rolls the active file aside on each ``append()``
based on :class:`AuditConfig.rotation_mode`:

* ``"none"`` (default): never rotate; behaviour identical to C16/C17/C18.
* ``"daily"``: when the active file's mtime falls on a UTC date that
  differs from "today", rename the active file to
  ``<stem>.YYYY-MM-DD.jsonl`` (date = mtime's UTC date) before writing
  the new record.
* ``"size"``: when ``stat().st_size + len(serialized_line) >
  rotation_size_mb * 1024 * 1024``, rename the active file to
  ``<stem>.YYYYMMDDTHHMMSS.jsonl`` (UTC timestamp at rotation moment)
  before writing.

**Chain head carry-over** (the contract the original C16 docstring
deferred to "when rotation lands"): because rotation happens *inside*
the same ``with self._lock`` + filelock scope as the append, and because
we always ``_reload_last_hash_from_disk()`` BEFORE deciding to rotate,
the writer's in-memory ``_last_hash`` already points at the renamed
file's tail row_hash when we open the (now-empty) new file for write.
The first record written to the new file therefore has
``prev_hash = <last row_hash of the rotated file>`` — a verifier walking
``<stem>.YYYY-MM-DD.jsonl`` → ``<stem>.jsonl`` in mtime order sees one
continuous chain.

Rotation never widens the trust boundary: it's all under the same
process+filelock window as appends, and ``self.path`` (the *active*
file) is unchanged by rotation — only its on-disk content is moved
aside, so callers don't need to re-resolve the writer.

Pruning: after rotation, if ``rotation_keep_count > 0``, archives
matching ``<stem>.*.jsonl`` are sorted by mtime ascending and the
oldest beyond the keep window are unlinked. ``keep_count = 0`` =
unlimited (operator runs an external archiver).

Out of scope (explicit follow-ups, not bugs)
---------------------------------------------

* ``ParamMutationAuditor`` (C17 Phase E.2) uses the same
  ``ChainedJsonlWriter`` infrastructure — so it inherits rotation
  automatically once its ``AuditConfig`` is wired the same way.
* Cross-process clock skew for daily mode: we use ``time.time()`` of
  *this* process for "today", and ``Path.stat().st_mtime`` of the file
  for "last write". If two processes on different timezones / clocks
  share the file, the date comparison may flip-flop near midnight UTC.
  Operators running multi-host audit consolidation should keep clocks
  in sync via NTP (which is the assumption everywhere else in OpenAkita
  too).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GENESIS_HASH: str = "0" * 64

_FSYNC_ENV: str = "OPENAKITA_AUDIT_FSYNC"

# C17 Phase E.1: cross-process append serialization. ``filelock`` is in
# pyproject deps, but tests and embedded deployments can run from source
# without installing extras. If import fails, use a stdlib OS file lock
# instead of silently degrading to process-only locking.
class _StdlibFileLockTimeout(TimeoutError):
    pass


class _StdlibFileLock:
    """Small cross-process exclusive lock fallback for audit integrity."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._fh: Any | None = None

    def acquire(self, timeout: float | None = None) -> _StdlibFileLock:
        deadline = None if timeout is None else time.monotonic() + timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 - held open until release()
        try:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"\0")
                fh.flush()

            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fh = fh
                    return self
                except OSError as exc:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise _StdlibFileLockTimeout(str(exc)) from exc
                    time.sleep(0.05)
        except Exception:
            fh.close()
            raise

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def __enter__(self) -> _StdlibFileLock:
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


try:
    from filelock import FileLock
    from filelock import Timeout as _FileLockTimeout
    _HAS_FILELOCK = True
except Exception:  # pragma: no cover
    _HAS_FILELOCK = False
    FileLock = _StdlibFileLock  # type: ignore[assignment, misc]
    _FileLockTimeout = _StdlibFileLockTimeout  # type: ignore[assignment, misc]

# Bound how long we'll wait for the cross-process lock per append.
# Audit appends are O(ms); a 5s wait is generous. On timeout we log and
# raise — the caller chooses fallback. We deliberately do NOT silently
# write without the lock; for an audit chain a torn write is worse than
# a missing event.
_FILELOCK_TIMEOUT_SECONDS: float = 5.0

_WRITERS: dict[Path, ChainedJsonlWriter] = {}
_WRITERS_LOCK = threading.Lock()

# C17 二轮: how far we'll grow the tail-read window when searching for the
# last newline-terminated line. 16 MiB is enough room for very large
# ParamMutationAuditor ``before/after`` payloads (each capped to ~4 MiB by
# ``_sanitize_for_chain``) plus chain overhead, while still bounding RAM.
_MAX_TAIL_BYTES: int = 16 * 1024 * 1024
_INITIAL_TAIL_WINDOW: int = 65536


def _read_last_complete_line(path: Path) -> bytes | None:
    """Return the bytes of the last newline-terminated line in ``path``.

    Scans backwards in doubling chunks (starting at 64 KiB, capping at
    :data:`_MAX_TAIL_BYTES`) until we have seen at least one complete
    final line. Returns ``None`` if:

    * The file is missing, empty, or unreadable.
    * The trailing line exceeds :data:`_MAX_TAIL_BYTES` (logged as warning).
    * The entire file is a single partial line (no newlines).

    A "complete final line" is detected when either:

    * The whole file fits inside ``window`` (so the first byte is at
      offset 0), or
    * The buffer contains ≥ 2 newlines (the last newline terminates the
      target line; the prior newline guarantees that line started inside
      the window, not before it).
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None

    window = min(size, _INITIAL_TAIL_WINDOW)
    while True:
        try:
            with open(path, "rb") as fh:
                fh.seek(size - window)
                buf = fh.read(window)
        except OSError:
            return None

        # Trim partial trailing bytes (e.g. mid-write crash on another
        # process). We do NOT mutate the file here; ``_bootstrap`` owns
        # that decision for our own writer.
        if not buf.endswith(b"\n"):
            last_nl = buf.rfind(b"\n")
            if last_nl < 0:
                if window >= size:
                    return None  # whole file is one partial line
                # fall through to expand window
            else:
                buf = buf[: last_nl + 1]

        # If we've fully covered the file, the bottom line is complete.
        # Otherwise we need ≥ 2 newlines to guarantee the trailing line
        # started inside our window.
        if window >= size or buf.count(b"\n") >= 2:
            stripped = buf.rstrip(b"\n").rsplit(b"\n", 1)
            return stripped[-1] if stripped and stripped[-1] else None

        if window >= _MAX_TAIL_BYTES:
            logger.warning(
                "[audit_chain] %s last line exceeds %d bytes; "
                "cannot recover row_hash safely",
                path,
                _MAX_TAIL_BYTES,
            )
            return None
        window = min(window * 2, size)


def _canonical_dumps(record: dict[str, Any]) -> str:
    """Stable, byte-exact JSON for hashing.

    Callers must pre-serialise non-primitive values; we deliberately do not
    pass a ``default=`` callback to ``json.dumps``, so a non-JSON-native
    value raises immediately rather than silently changing the hash.
    """
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _compute_row_hash(record_without_row_hash: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of ``record`` *without* ``row_hash``.

    Excluding ``row_hash`` from its own input is what makes the hash
    well-defined; including it would yield a self-referential equation.
    """
    if "row_hash" in record_without_row_hash:
        raise ValueError("row_hash must be excluded from hash input")
    blob = _canonical_dumps(record_without_row_hash).encode("utf-8")
    return sha256(blob).hexdigest()


@dataclass
class ChainVerifyResult:
    """Outcome of :func:`verify_chain`.

    ``ok=True`` means every line carrying ``row_hash`` chains correctly to
    the previous one, starting from either ``GENESIS_HASH`` (no legacy
    prefix) or the implicit boundary right after the last legacy line.

    ``legacy_prefix_lines`` counts lines that pre-date C16 (no
    ``row_hash`` field). Those lines are *not* flagged as tamper because
    they were never chained in the first place; they're surfaced so the
    UI can show "X legacy lines, Y chained lines, all chained lines OK".

    ``truncated_tail_recovered=True`` means the writer detected and
    discarded a partial trailing line on open. The verifier reports this
    so operators know a crash was recovered, but it does *not* flag
    tamper.
    """

    ok: bool
    total: int
    legacy_prefix_lines: int
    truncated_tail_recovered: bool
    first_bad_line: int | None
    reason: str | None


class ChainedJsonlWriter:
    """Append-only hash-chained JSONL writer.

    Use :func:`get_writer` (or :func:`reset_writers_for_testing` in tests)
    rather than constructing directly — the singleton-per-path map
    guarantees that multiple import sites pointing at the same file share
    a lock and chain head.
    """

    def __init__(self, path: Path, *, lock: threading.Lock | None = None) -> None:
        self.path = Path(path)
        self._lock = lock or threading.Lock()
        self._last_hash: str = GENESIS_HASH
        self._truncated_tail_recovered: bool = False
        # C17 Phase E.1: cross-process filelock sibling of the audit file.
        # ``filelock`` 0.12+ is happy with str paths on Windows + POSIX.
        self._filelock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._filelock = FileLock(str(self._filelock_path))
        self._bootstrap()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _bootstrap(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return

        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size == 0:
            return

        # Crash-recovery preamble: if the file does NOT end in a newline,
        # the last bytes are a partial write from a previous crash. We
        # need to truncate them so subsequent appends produce a clean
        # chain. We do this by reading only the bytes after the last
        # newline (small read, regardless of file size).
        try:
            with open(self.path, "rb") as fh:
                fh.seek(max(0, size - _INITIAL_TAIL_WINDOW))
                tail_probe = fh.read()
        except OSError:
            return
        ends_clean = tail_probe.endswith(b"\n")
        if not ends_clean:
            last_nl = tail_probe.rfind(b"\n")
            if last_nl < 0:
                # Either the whole file is one partial line, or the
                # partial bytes overflow our 64 KiB probe. Either way
                # refuse to truncate silently. Bootstrap from GENESIS.
                logger.warning(
                    "[audit_chain] %s has no newline terminator within tail "
                    "probe; bootstrapping from GENESIS without truncating.",
                    self.path,
                )
                return
            keep_until = size - (len(tail_probe) - last_nl - 1)
            try:
                with open(self.path, "ab") as fh:
                    fh.truncate(keep_until)
                self._truncated_tail_recovered = True
                logger.warning(
                    "[audit_chain] %s had partial trailing bytes (crash "
                    "recovery); truncated to last full line.",
                    self.path,
                )
            except OSError as exc:
                logger.error(
                    "[audit_chain] Failed to truncate partial tail on %s: %s",
                    self.path,
                    exc,
                )
                return

        # Now scan back (with auto-expand) for the last complete line and
        # try to extract row_hash. (C17 二轮: was a fixed 64 KiB read; now
        # ``_read_last_complete_line`` doubles its window up to 16 MiB so
        # huge ParamMutationAuditor rows don't break bootstrap.)
        last_line = _read_last_complete_line(self.path)
        if last_line is None:
            return
        try:
            last_obj = json.loads(last_line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.warning(
                "[audit_chain] %s last line is not valid JSON; "
                "bootstrapping from GENESIS.",
                self.path,
            )
            return
        if isinstance(last_obj, dict) and isinstance(last_obj.get("row_hash"), str):
            self._last_hash = last_obj["row_hash"]
        # else: legacy file (no row_hash) → keep GENESIS; the first chained
        # append starts a new sub-chain after the legacy prefix.

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def _reload_last_hash_from_disk(self) -> None:
        """Re-read the last full line's ``row_hash`` from disk.

        Called under the cross-process filelock right before computing the
        next ``prev_hash``. Without this step, two processes that both
        bootstrapped from the same on-disk tail would each compute
        ``prev_hash = X`` and write a fork — the verifier would flag the
        second one as a prev_hash mismatch. By re-reading inside the
        filelock we always chain off the latest committed tail, whichever
        process wrote it.

        Uses :func:`_read_last_complete_line` (C17 二轮): the tail window
        auto-grows up to :data:`_MAX_TAIL_BYTES` so a single audit record
        larger than 64 KiB (e.g. a ParamMutationAuditor entry with a big
        ``before``/``after`` blob) still yields the correct prior row_hash
        instead of silently leaving ``_last_hash`` stale and producing a
        chain fork on the next append.
        """
        if not self.path.exists():
            self._last_hash = GENESIS_HASH
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size == 0:
            self._last_hash = GENESIS_HASH
            return
        last_line = _read_last_complete_line(self.path)
        if last_line is None:
            return
        try:
            obj = json.loads(last_line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(obj, dict) and isinstance(obj.get("row_hash"), str):
            self._last_hash = obj["row_hash"]

    # ------------------------------------------------------------------
    # Rotation (C20)
    # ------------------------------------------------------------------

    def _get_rotation_config(self) -> tuple[str, int, int]:
        """Read ``audit.rotation_*`` from current policy config.

        Lazy lookup at append time means hot-reload of POLICIES.yaml
        takes effect on the next write — no restart required, matching
        the C18 hot-reload promise.

        C20 自审：we **deliberately bypass** ``get_config_v2()`` here.
        Reason: ``append()`` can be invoked indirectly from inside
        ``rebuild_engine_v2``'s ``threading.Lock`` (e.g. when
        ``_audit_env_overrides`` writes an override audit row via its
        ephemeral ``AuditLogger``). ``get_config_v2()`` itself tries to
        ``with _lock:`` — the same non-reentrant lock — so we'd reproduce
        the exact BUG-C2 deadlock pattern. Reading the module-level
        ``_config`` attribute directly is GIL-safe (single attribute
        read), lock-free, and gives us either the latest installed cfg
        or ``None`` (in which case we fall back to defaults).

        We tolerate every failure path (no config, partial config,
        circular imports during interpreter shutdown) by falling back
        to ``("none", 100, 30)`` so that audit writes never get blocked
        on a config error. The contract: rotation is best-effort UX;
        the chain integrity contract (prev_hash / row_hash) is
        non-negotiable.
        """
        try:
            from . import global_engine

            cfg_obj = global_engine._config
            if cfg_obj is None:
                return "none", 100, 30
            cfg = cfg_obj.audit
            mode = getattr(cfg, "rotation_mode", "none")
            size_mb = int(getattr(cfg, "rotation_size_mb", 100))
            keep = int(getattr(cfg, "rotation_keep_count", 30))
            if mode not in ("none", "daily", "size"):
                mode = "none"
            return mode, size_mb, keep
        except Exception:
            return "none", 100, 30

    def _needs_rotation(
        self, *, mode: str, size_mb: int, pending_line_bytes: int
    ) -> tuple[bool, str | None]:
        """Decide whether to rotate BEFORE the upcoming append.

        Returns ``(needed, archive_suffix)`` — when ``needed`` is True,
        the caller renames ``self.path`` to
        ``<stem>.<archive_suffix>.jsonl``. Empty / missing files never
        rotate (nothing to archive).
        """
        if mode == "none":
            return False, None
        if not self.path.exists():
            return False, None
        try:
            stat = self.path.stat()
        except OSError:
            return False, None
        if stat.st_size == 0:
            return False, None

        if mode == "daily":
            try:
                mtime_date = datetime.fromtimestamp(stat.st_mtime, tz=UTC).date()
            except (OSError, OverflowError, ValueError):
                return False, None
            today = datetime.fromtimestamp(time.time(), tz=UTC).date()
            if mtime_date != today:
                return True, mtime_date.strftime("%Y-%m-%d")
            return False, None

        if mode == "size":
            limit_bytes = size_mb * 1024 * 1024
            if stat.st_size + pending_line_bytes > limit_bytes:
                stamp = datetime.fromtimestamp(time.time(), tz=UTC).strftime(
                    "%Y%m%dT%H%M%S"
                )
                # Disambiguate same-second rotations: if the archive
                # path already exists (extremely rare, but two threads
                # could race here in pathological tests / micro-bench),
                # append a millisecond suffix.
                archive = self.path.with_suffix(f".{stamp}.jsonl")
                if archive.exists():
                    ms = datetime.fromtimestamp(time.time(), tz=UTC).strftime(
                        "%Y%m%dT%H%M%S%f"
                    )
                    stamp = ms
                return True, stamp
            return False, None

        return False, None

    def _do_rotate(self, archive_suffix: str, keep_count: int) -> None:
        """Rename current ``self.path`` to its archive name and prune.

        Must be called inside both ``self._lock`` and the cross-process
        filelock. Idempotent w.r.t. concurrent writers (the second
        writer's ``_needs_rotation`` check finds the file already
        empty / fresh and skips).
        """
        archive = self.path.with_suffix(f".{archive_suffix}.jsonl")
        # If the archive already exists (e.g. two rotations in the same
        # second with the same daily suffix because clocks jumped), keep
        # the existing archive untouched and skip rotation rather than
        # silently overwrite history.
        if archive.exists():
            logger.warning(
                "[audit_chain] rotate target %s already exists; skipping "
                "rotation to preserve historical archive",
                archive,
            )
            return
        try:
            self.path.rename(archive)
        except OSError as exc:
            logger.error(
                "[audit_chain] failed to rotate %s -> %s: %s",
                self.path,
                archive,
                exc,
            )
            return
        logger.info("[audit_chain] rotated %s -> %s", self.path, archive)

        # Prune older archives. Sibling pattern: same stem prefix +
        # ``.<suffix>.jsonl``. We deliberately do NOT prune ``self.path``
        # itself — that's the active file, not an archive.
        if keep_count > 0:
            self._prune_archives(keep_count)

    def _list_archives(self) -> list[Path]:
        """Find rotated siblings of ``self.path``.

        Pattern: same parent dir, basename matches
        ``<stem>.<anything>.jsonl`` AND is NOT ``self.path`` itself.
        Returned list is sorted by ``st_mtime`` ascending (oldest
        first), which is the verification + prune order.
        """
        parent = self.path.parent
        stem = self.path.stem  # e.g. "policy_decisions" for ".jsonl"
        if not parent.exists():
            return []
        results: list[tuple[float, Path]] = []
        active_resolved = self.path.resolve()
        for entry in parent.iterdir():
            if not entry.is_file():
                continue
            if entry.resolve() == active_resolved:
                continue
            name = entry.name
            # ``<stem>.<suffix>.jsonl`` — at least one dot beyond the
            # stem before the trailing .jsonl. ``audit.jsonl`` (active)
            # is filtered above by identity; ``audit.jsonl.lock`` is
            # filtered here because it doesn't end with ``.jsonl``.
            if not name.endswith(".jsonl"):
                continue
            if not name.startswith(stem + "."):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            results.append((mtime, entry))
        results.sort(key=lambda t: t[0])
        return [p for _, p in results]

    def _prune_archives(self, keep_count: int) -> None:
        archives = self._list_archives()
        if len(archives) <= keep_count:
            return
        for stale in archives[: len(archives) - keep_count]:
            try:
                stale.unlink()
                logger.info(
                    "[audit_chain] pruned old archive %s (keep_count=%d)",
                    stale,
                    keep_count,
                )
            except OSError as exc:
                logger.warning(
                    "[audit_chain] failed to prune %s: %s", stale, exc
                )

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append ``record`` with ``prev_hash`` + ``row_hash`` populated.

        Returns the augmented record so callers (e.g. tests) can inspect
        what was actually written.

        Locking order (C17 Phase E.1):

        1. Acquire process-local ``threading.Lock`` (cheap, blocks other
           threads in this interpreter).
        2. Acquire ``filelock.FileLock`` with a bounded timeout (blocks
           other processes).
        3. Re-read the on-disk tail to refresh ``_last_hash`` — a sibling
           process may have appended while we were waiting.
        4. C20: check rotation; if needed, rename active file aside. The
           in-memory ``_last_hash`` (refreshed in step 3) carries the
           chain head across the boundary.
        5. Build enriched record + write + fsync (optional).
        6. Release filelock, then process lock.
        """
        if not isinstance(record, dict):
            raise TypeError(f"record must be a dict, got {type(record).__name__}")
        if "row_hash" in record or "prev_hash" in record:
            raise ValueError(
                "record must not pre-populate prev_hash / row_hash; "
                "ChainedJsonlWriter owns those fields."
            )

        with self._lock:
            # 2 + 3: cross-process serialization + read fresh tail. We do
            # this work *inside* the filelock so two processes can't both
            # observe the same _last_hash and fork the chain.
            acquired_cross = False
            if self._filelock is not None:
                try:
                    self._filelock.acquire(timeout=_FILELOCK_TIMEOUT_SECONDS)
                    acquired_cross = True
                except _FileLockTimeout as exc:
                    logger.error(
                        "[audit_chain] cross-process filelock timed out "
                        "after %.1fs for %s; refusing to append",
                        _FILELOCK_TIMEOUT_SECONDS,
                        self.path,
                    )
                    raise OSError(
                        f"audit_chain filelock timeout on {self.path}"
                    ) from exc

            try:
                # Critical: re-read tail under the filelock, not before.
                self._reload_last_hash_from_disk()

                # Pre-build the enriched line so size-mode rotation can
                # make an accurate decision (we know exactly how many
                # bytes are about to land).
                enriched = {**record, "prev_hash": self._last_hash}
                row_hash = _compute_row_hash(enriched)
                enriched["row_hash"] = row_hash
                line = _canonical_dumps(enriched) + "\n"
                line_bytes = len(line.encode("utf-8"))

                # C20: rotation check + rename happens BEFORE the write.
                # _last_hash has just been refreshed from the current
                # file's tail, so when we rotate the file aside and then
                # write to the empty new path, ``prev_hash`` in the
                # enriched record above is already correct (it equals
                # the rotated file's tail row_hash). Chain stays
                # continuous across the rotation boundary.
                mode, size_mb, keep = self._get_rotation_config()
                needs, suffix = self._needs_rotation(
                    mode=mode, size_mb=size_mb, pending_line_bytes=line_bytes
                )
                if needs and suffix is not None:
                    self._do_rotate(suffix, keep)

                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.path, "a", encoding="utf-8") as fh:
                        fh.write(line)
                        if os.getenv(_FSYNC_ENV) == "1":
                            fh.flush()
                            os.fsync(fh.fileno())
                except OSError as exc:
                    logger.error(
                        "[audit_chain] Failed to append to %s: %s", self.path, exc
                    )
                    raise

                self._last_hash = row_hash
                return enriched
            finally:
                if acquired_cross and self._filelock is not None:
                    try:
                        self._filelock.release()
                    except Exception:  # pragma: no cover
                        pass

    def append_batch(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """C22 P3-2: append N records under a SINGLE filelock acquisition.

        Plan §13.5.2 A specified an async batch writer to coalesce audit
        writes. Per-record :meth:`append` re-acquires the cross-process
        filelock + re-reads the tail every call (~ms each). Under
        burst load (e.g. checkpoint replay) this dominates engine
        latency. ``append_batch`` keeps the same correctness guarantees
        but pays the filelock + tail-read cost once for the whole batch:

        - Acquire process lock + filelock once
        - Re-read tail once to refresh ``_last_hash``
        - For each record: compute prev_hash → row_hash in-memory,
          chaining the next record off the previous in-batch row_hash
          (NOT off the on-disk tail — the next on-disk tail will be
          the *previous* in-batch row after we write)
        - Pre-build all enriched lines, then a single ``fh.write`` of
          the concatenated lines + optional fsync
        - Update ``_last_hash`` to the last in-batch row_hash
        - Release locks

        Chain invariant: external verifier sees exactly the same byte
        sequence as if N :meth:`append` calls had been made
        back-to-back. Empty list → no-op, returns ``[]``.

        Returns the list of enriched records (in input order). On
        partial failure (mid-batch ``write`` raises) the lock is
        released and the exception bubbles — some records may have
        been written to disk; caller should treat the batch as
        failed and re-enqueue unconfirmed ones if it needs at-least-once.
        """
        if not records:
            return []
        for r in records:
            if not isinstance(r, dict):
                raise TypeError(
                    f"all batch records must be dicts, got {type(r).__name__}"
                )
            if "row_hash" in r or "prev_hash" in r:
                raise ValueError(
                    "batch records must not pre-populate prev_hash / row_hash"
                )

        with self._lock:
            acquired_cross = False
            if self._filelock is not None:
                try:
                    self._filelock.acquire(timeout=_FILELOCK_TIMEOUT_SECONDS)
                    acquired_cross = True
                except _FileLockTimeout as exc:
                    logger.error(
                        "[audit_chain] cross-process filelock timed out "
                        "after %.1fs for %s (batch=%d); refusing to append",
                        _FILELOCK_TIMEOUT_SECONDS,
                        self.path,
                        len(records),
                    )
                    raise OSError(
                        f"audit_chain filelock timeout on {self.path}"
                    ) from exc

            try:
                self._reload_last_hash_from_disk()

                # Pre-build all enriched records + concatenated payload
                # so we can apply size-mode rotation accurately (sum of
                # all line bytes) and then issue a single write.
                enriched_list: list[dict[str, Any]] = []
                lines: list[str] = []
                cursor = self._last_hash
                for r in records:
                    enriched = {**r, "prev_hash": cursor}
                    rh = _compute_row_hash(enriched)
                    enriched["row_hash"] = rh
                    line = _canonical_dumps(enriched) + "\n"
                    enriched_list.append(enriched)
                    lines.append(line)
                    cursor = rh

                total_bytes = sum(len(line.encode("utf-8")) for line in lines)

                mode, size_mb, keep = self._get_rotation_config()
                needs, suffix = self._needs_rotation(
                    mode=mode, size_mb=size_mb, pending_line_bytes=total_bytes
                )
                if needs and suffix is not None:
                    self._do_rotate(suffix, keep)

                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.path, "a", encoding="utf-8") as fh:
                        fh.write("".join(lines))
                        if os.getenv(_FSYNC_ENV) == "1":
                            fh.flush()
                            os.fsync(fh.fileno())
                except OSError as exc:
                    logger.error(
                        "[audit_chain] Failed batch-append (%d records) to %s: %s",
                        len(records),
                        self.path,
                        exc,
                    )
                    raise

                self._last_hash = cursor
                return enriched_list
            finally:
                if acquired_cross and self._filelock is not None:
                    try:
                        self._filelock.release()
                    except Exception:  # pragma: no cover
                        pass

    @property
    def last_hash(self) -> str:
        return self._last_hash

    @property
    def truncated_tail_recovered(self) -> bool:
        return self._truncated_tail_recovered


# ---------------------------------------------------------------------------
# Singleton registry
# ---------------------------------------------------------------------------


def get_writer(path: Path | str) -> ChainedJsonlWriter:
    """Return a process-wide singleton writer for ``path``.

    Two import sites that resolve the same path get the same writer
    instance — same lock, same in-memory ``_last_hash``. This is the
    intended entry point for every audit sink in the codebase.
    """
    p = Path(path).resolve()
    with _WRITERS_LOCK:
        writer = _WRITERS.get(p)
        if writer is None:
            writer = ChainedJsonlWriter(p)
            _WRITERS[p] = writer
        return writer


def reset_writers_for_testing() -> None:
    """Clear the singleton map.

    Tests should call this between cases that share an audit path so each
    case bootstraps fresh. Not for production use.
    """
    with _WRITERS_LOCK:
        _WRITERS.clear()


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def _list_rotation_archives(active_path: Path) -> list[Path]:
    """Find rotated siblings of ``active_path`` (the "current" file).

    Mirror of :meth:`ChainedJsonlWriter._list_archives` but available
    as a module-level helper so :func:`verify_chain_with_rotation` can
    use it without instantiating a writer.

    Pattern: same parent dir, basename starts with
    ``<active_stem>.`` + ends with ``.jsonl``, NOT the active file
    itself, NOT a ``.lock`` sidecar. Result sorted by mtime ascending
    (oldest first — natural chain walk order).
    """
    parent = active_path.parent
    stem = active_path.stem
    if not parent.exists():
        return []
    results: list[tuple[float, Path]] = []
    try:
        active_resolved = active_path.resolve()
    except OSError:
        active_resolved = active_path
    for entry in parent.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.resolve() == active_resolved:
                continue
        except OSError:
            continue
        name = entry.name
        if not name.endswith(".jsonl"):
            continue
        if not name.startswith(stem + "."):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        results.append((mtime, entry))
    results.sort(key=lambda t: t[0])
    return [p for _, p in results]


def verify_chain_with_rotation(active_path: Path | str) -> ChainVerifyResult:
    """Verify the hash chain across rotated archives + the active file.

    Walks every ``<stem>.<suffix>.jsonl`` archive in mtime order,
    followed by the active file ``<stem>.jsonl``. Maintains
    ``expected_prev`` across file boundaries — so a chain that was
    rotated mid-life still verifies cleanly end-to-end.

    Returns a single :class:`ChainVerifyResult` over the concatenated
    history. ``total`` counts every line across all files;
    ``legacy_prefix_lines`` is summed across all files where the
    pre-C16 raw-append prefix existed; ``first_bad_line`` is the
    line index within the concatenated stream (1-indexed).

    If the directory has no rotation archives, behaviour is identical
    to :func:`verify_chain` on the active file alone — so existing
    callers can switch to this entry point freely without seeing
    spurious differences on un-rotated deployments.
    """
    p = Path(active_path)
    archives = _list_rotation_archives(p)
    # Walk archives first (oldest → newest), then the active file. If
    # active file doesn't exist yet (fresh deploy) we still verify the
    # archives — operator may be checking history of a since-rotated
    # log.
    files: list[Path] = list(archives)
    if p.exists():
        files.append(p)
    if not files:
        return ChainVerifyResult(
            ok=True,
            total=0,
            legacy_prefix_lines=0,
            truncated_tail_recovered=False,
            first_bad_line=None,
            reason=None,
        )

    total = 0
    legacy_prefix = 0
    truncated_any = False
    expected_prev = GENESIS_HASH
    in_chain = False

    for file_idx, file_path in enumerate(files):
        try:
            with open(file_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            return ChainVerifyResult(
                ok=False,
                total=total,
                legacy_prefix_lines=legacy_prefix,
                truncated_tail_recovered=truncated_any,
                first_bad_line=total,
                reason=f"read error on {file_path.name}: {exc}",
            )

        file_truncated = bool(content) and not content.endswith("\n")
        truncated_any = truncated_any or file_truncated
        lines = content.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]

        is_last_file = file_idx == len(files) - 1

        for line_in_file_idx, raw in enumerate(lines, start=1):
            total += 1
            try:
                obj = json.loads(raw)
            except ValueError as exc:
                # Allow only the very last line of the very last file
                # to be a torn partial write (crash recovery semantics).
                if (
                    is_last_file
                    and line_in_file_idx == len(lines)
                    and file_truncated
                ):
                    total -= 1
                    break
                return ChainVerifyResult(
                    ok=False,
                    total=total,
                    legacy_prefix_lines=legacy_prefix,
                    truncated_tail_recovered=truncated_any,
                    first_bad_line=total,
                    reason=(
                        f"{file_path.name} line {line_in_file_idx} is "
                        f"not valid JSON: {exc}"
                    ),
                )
            if not isinstance(obj, dict):
                return ChainVerifyResult(
                    ok=False,
                    total=total,
                    legacy_prefix_lines=legacy_prefix,
                    truncated_tail_recovered=truncated_any,
                    first_bad_line=total,
                    reason=(
                        f"{file_path.name} line {line_in_file_idx} is "
                        "not a JSON object"
                    ),
                )

            row_hash = obj.get("row_hash")
            prev_hash = obj.get("prev_hash")

            if row_hash is None and prev_hash is None:
                if not in_chain:
                    legacy_prefix += 1
                    continue
                return ChainVerifyResult(
                    ok=False,
                    total=total,
                    legacy_prefix_lines=legacy_prefix,
                    truncated_tail_recovered=truncated_any,
                    first_bad_line=total,
                    reason=(
                        f"{file_path.name} line {line_in_file_idx} is "
                        "missing chain fields after chain started"
                    ),
                )

            in_chain = True

            if prev_hash != expected_prev:
                return ChainVerifyResult(
                    ok=False,
                    total=total,
                    legacy_prefix_lines=legacy_prefix,
                    truncated_tail_recovered=truncated_any,
                    first_bad_line=total,
                    reason=(
                        f"{file_path.name} line {line_in_file_idx} "
                        f"prev_hash mismatch: expected {expected_prev[:12]}…, "
                        f"got {(prev_hash or 'None')[:12]}…"
                    ),
                )

            bare = {k: v for k, v in obj.items() if k != "row_hash"}
            recomputed = _compute_row_hash(bare)
            if recomputed != row_hash:
                return ChainVerifyResult(
                    ok=False,
                    total=total,
                    legacy_prefix_lines=legacy_prefix,
                    truncated_tail_recovered=truncated_any,
                    first_bad_line=total,
                    reason=(
                        f"{file_path.name} line {line_in_file_idx} "
                        f"row_hash mismatch: stored {(row_hash or 'None')[:12]}…, "
                        f"recomputed {recomputed[:12]}…"
                    ),
                )

            expected_prev = row_hash

    return ChainVerifyResult(
        ok=True,
        total=total,
        legacy_prefix_lines=legacy_prefix,
        truncated_tail_recovered=truncated_any,
        first_bad_line=None,
        reason=None,
    )


def verify_chain(path: Path | str) -> ChainVerifyResult:
    """Walk ``path`` line-by-line and verify the hash chain.

    Single-file walker — kept for back-compat and for callers who
    explicitly want to verify just one file (e.g. one archive in
    isolation). For end-to-end verification across rotation use
    :func:`verify_chain_with_rotation`.

    Linear O(N). Use sparingly on very large files; SecurityView
    should call this on operator demand, not on every page render.
    """
    p = Path(path)
    if not p.exists():
        return ChainVerifyResult(
            ok=True,
            total=0,
            legacy_prefix_lines=0,
            truncated_tail_recovered=False,
            first_bad_line=None,
            reason=None,
        )

    legacy_prefix = 0
    total = 0
    truncated = False
    expected_prev = GENESIS_HASH
    in_chain = False

    try:
        with open(p, encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        return ChainVerifyResult(
            ok=False,
            total=0,
            legacy_prefix_lines=0,
            truncated_tail_recovered=False,
            first_bad_line=0,
            reason=f"read error: {exc}",
        )

    if content and not content.endswith("\n"):
        truncated = True

    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]

    for idx, raw in enumerate(lines, start=1):
        total += 1
        try:
            obj = json.loads(raw)
        except ValueError as exc:
            if idx == len(lines) and truncated:
                total -= 1
                break
            return ChainVerifyResult(
                ok=False,
                total=total,
                legacy_prefix_lines=legacy_prefix,
                truncated_tail_recovered=truncated,
                first_bad_line=idx,
                reason=f"line {idx} is not valid JSON: {exc}",
            )

        if not isinstance(obj, dict):
            return ChainVerifyResult(
                ok=False,
                total=total,
                legacy_prefix_lines=legacy_prefix,
                truncated_tail_recovered=truncated,
                first_bad_line=idx,
                reason=f"line {idx} is not a JSON object",
            )

        row_hash = obj.get("row_hash")
        prev_hash = obj.get("prev_hash")

        if row_hash is None and prev_hash is None:
            if not in_chain:
                legacy_prefix += 1
                continue
            return ChainVerifyResult(
                ok=False,
                total=total,
                legacy_prefix_lines=legacy_prefix,
                truncated_tail_recovered=truncated,
                first_bad_line=idx,
                reason=f"line {idx} is missing chain fields after chain started",
            )

        in_chain = True

        if prev_hash != expected_prev:
            return ChainVerifyResult(
                ok=False,
                total=total,
                legacy_prefix_lines=legacy_prefix,
                truncated_tail_recovered=truncated,
                first_bad_line=idx,
                reason=(
                    f"line {idx} prev_hash mismatch: "
                    f"expected {expected_prev[:12]}…, got "
                    f"{(prev_hash or 'None')[:12]}…"
                ),
            )

        bare = {k: v for k, v in obj.items() if k != "row_hash"}
        recomputed = _compute_row_hash(bare)
        if recomputed != row_hash:
            return ChainVerifyResult(
                ok=False,
                total=total,
                legacy_prefix_lines=legacy_prefix,
                truncated_tail_recovered=truncated,
                first_bad_line=idx,
                reason=(
                    f"line {idx} row_hash mismatch: "
                    f"stored {(row_hash or 'None')[:12]}…, "
                    f"recomputed {recomputed[:12]}…"
                ),
            )

        expected_prev = row_hash

    return ChainVerifyResult(
        ok=True,
        total=total,
        legacy_prefix_lines=legacy_prefix,
        truncated_tail_recovered=truncated,
        first_bad_line=None,
        reason=None,
    )


__all__ = [
    "ChainVerifyResult",
    "ChainedJsonlWriter",
    "GENESIS_HASH",
    "_canonical_dumps",
    "_compute_row_hash",
    "_list_rotation_archives",
    "get_writer",
    "reset_writers_for_testing",
    "verify_chain",
    "verify_chain_with_rotation",
]
