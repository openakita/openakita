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
  suffix. When ``filelock`` is unavailable (very rare) we fall back to
  process-only locking with a warning.
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

Out of scope (explicit follow-ups, not bugs)
---------------------------------------------

* Cross-file rotation (e.g. ``audit-2026-05-13.jsonl`` → next day) doesn't
  exist yet; when added, the chain head will need to embed the tail hash
  of the previous file. ``ParamMutationAuditor`` (C17 Phase E.2) now uses
  the same ``ChainedJsonlWriter`` infrastructure as the policy audit.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GENESIS_HASH: str = "0" * 64

_FSYNC_ENV: str = "OPENAKITA_AUDIT_FSYNC"

# C17 Phase E.1: cross-process append serialization. ``filelock`` is in
# pyproject deps so this should always import successfully — if it doesn't
# we degrade to in-process locking only.
try:
    from filelock import FileLock
    from filelock import Timeout as _FileLockTimeout
    _HAS_FILELOCK = True
except Exception:  # pragma: no cover
    _HAS_FILELOCK = False
    _FileLockTimeout = Exception  # type: ignore[assignment, misc]

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
        self._filelock = (
            FileLock(str(self._filelock_path)) if _HAS_FILELOCK else None
        )
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
        4. Build enriched record + write + fsync (optional).
        5. Release filelock, then process lock.
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

                enriched = {**record, "prev_hash": self._last_hash}
                row_hash = _compute_row_hash(enriched)
                enriched["row_hash"] = row_hash

                line = _canonical_dumps(enriched) + "\n"
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


def verify_chain(path: Path | str) -> ChainVerifyResult:
    """Walk ``path`` line-by-line and verify the hash chain.

    Linear O(N) — acceptable until rotation lands in C17. Use sparingly
    on very large files; SecurityView should call this on operator demand,
    not on every page render.
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
    "get_writer",
    "reset_writers_for_testing",
    "verify_chain",
]
