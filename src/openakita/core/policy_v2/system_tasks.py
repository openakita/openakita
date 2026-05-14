"""C15 §17.2 — ``SYSTEM_TASKS.yaml`` whitelist + lock + PolicyEngine bypass.

Motivation (R4-10 / R4-11)
==========================

Some operational tasks are mechanical and self-protective by design:
rotate ``policy_decisions.jsonl`` when it crosses a size threshold,
compress old checkpoints, rebuild the prompt compile cache, etc.
Running them through ``PolicyEngineV2.evaluate_tool_call`` is wrong
in two ways:

1. **False positives** — many of these targets sit inside the
   ``safety_immune`` set (e.g. ``data/audit/**``), so the engine emits
   ``DESTRUCTIVE`` even when the task is the carefully-bounded
   ``rotate_audit_log`` routine.
2. **User experience** — a maintenance job that pops a CONFIRM at 3am
   every Sunday means either ignored confirmations or, worse, the job
   silently never runs.

Solution: an operator-authored, hash-locked whitelist that lets a
known task ID bypass PolicyEngine **only for the exact tool + path
glob it declares**, and only after a workspace checkpoint snapshot.

Design constraints
==================

- **Fail-closed on tamper.** When ``SYSTEM_TASKS.yaml`` mutates the
  hash in ``.openakita/system_tasks.lock`` no longer matches → registry
  load returns ``None``. The operator must re-confirm by regenerating
  the lock (intentionally a CLI / setup-center action — NOT something
  the agent itself can do).
- **Path scope is per-task.** A task's ``path_globs`` define exactly
  what the bypass covers; any param path outside that scope falls
  back to the normal PolicyEngine path. The match is conservative
  (literal glob comparison on candidate path fields; one mismatch
  invalidates the whole call).
- **Workspace backup before mutation.** Default ``requires_backup=True``
  routes affected paths through :class:`CheckpointManager.create_checkpoint`
  before any file mutation can land, so a misbehaving task can be
  rolled back from the audit trail.
- **Append-only audit.** Every bypass attempt — match, mismatch,
  start, finish, failure — appends a JSONL record to
  ``data/audit/system_tasks.jsonl``. Tamper detection happens at
  audit log level (C16) — this module just writes durable trail.

API contract
============

Callers (currently none — see "Wiring" below) opt into bypass with::

    decision = request_bypass(
        task_id="rotate_audit_log",
        tool_name="move_file",
        params={"source": "data/audit/policy_decisions.jsonl", ...},
        registry=registry,
        workspace=workspace,
        checkpoint_mgr=checkpoint_mgr,
        audit_path=audit_path,
    )
    if decision is None:
        # task not in registry / scope mismatch / lock failure →
        # let PolicyEngineV2.evaluate_tool_call handle this normally
        ...
    else:
        try:
            # caller actually performs the tool call now
            result = do_the_thing(params)
            finalize_bypass(decision, audit_path=audit_path,
                            success=True, error=None)
        except Exception as exc:
            finalize_bypass(decision, audit_path=audit_path,
                            success=False, error=str(exc))
            raise

The two-step pattern (``request_bypass`` then ``finalize_bypass``) is
intentional: it forces the audit trail to capture both the "we
exempted this" decision and the operational outcome, even when the
underlying file op throws.

Wiring
======

Phase B intentionally **does not** auto-wire any production callers.
The infrastructure (registry, lock check, checkpoint hook, audit
write) is fully tested and ready. Subsequent commits can plug
specific maintenance jobs in — scheduler-driven audit rotation, etc.
— without changing the policy decision path. This keeps the C15
commit's risk surface bounded: an empty / missing
``SYSTEM_TASKS.yaml`` means zero bypasses ever happen (default-deny
behavior).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemTask:
    """A single whitelist entry — operator-authored.

    Fields:
        id: Stable task identifier referenced by callers. Operators
            must keep IDs unique within ``SYSTEM_TASKS.yaml``.
        description: Free-form human-readable description (logged to
            audit for forensics).
        tools: Tuple of tool names this task is allowed to invoke.
            A bypass request whose ``tool_name`` is not in this list
            is rejected.
        path_globs: Tuple of glob patterns that all path-bearing params
            must match. Patterns use :func:`fnmatch.fnmatch` semantics
            (``*`` does NOT cross directories); for recursive matches
            use ``**`` (we resolve it before delegating to fnmatch).
        requires_backup: When True, every affected path is snapshotted
            via :class:`CheckpointManager.create_checkpoint` before the
            bypass succeeds. Set False only for tasks whose targets are
            themselves backups (e.g. checkpoint cleanup) — irreversible
            ops on irreplaceable data should keep True.
    """

    id: str
    description: str
    tools: tuple[str, ...]
    path_globs: tuple[str, ...]
    requires_backup: bool = True


@dataclass
class BypassDecision:
    """Returned by :func:`request_bypass` on a successful match.

    The caller MUST hand this object back to :func:`finalize_bypass`
    so the audit trail is complete. The two-step protocol exists
    precisely so missed-finalize bugs are visible as
    ``start without end`` records in the JSONL.
    """

    task: SystemTask
    checkpoint_id: str | None
    audit_event_id: str
    tool_name: str
    params_summary: dict[str, Any]
    started_at: float = field(default_factory=time.time)


class SystemTasksLockMismatch(Exception):
    """Raised by :func:`load_registry` when the YAML hash differs from
    the recorded lock. Caller should treat this as registry == empty
    (fail-closed) and surface a setup-center warning."""


# ---------------------------------------------------------------------------
# Hash / lock helpers
# ---------------------------------------------------------------------------


_LOCK_PREFIX = "sha256:"


def compute_yaml_hash(content: bytes) -> str:
    """SHA-256 hex digest of the raw YAML bytes."""
    return hashlib.sha256(content).hexdigest()


def write_lock(lock_path: Path, yaml_hash: str) -> None:
    """Write ``<lock_path>`` with the canonical ``sha256:<hex>`` line.

    Operators / setup-center call this when intentionally accepting a
    YAML change. The agent itself never invokes ``write_lock`` —
    self-rotating the lock would defeat the tamper-detection purpose.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(_LOCK_PREFIX + yaml_hash + "\n", encoding="utf-8")


def read_lock(lock_path: Path) -> str | None:
    """Parse the lock file, returning the hex digest or ``None`` when
    the file is missing / malformed.

    Malformed inputs always return ``None`` (and we log a WARNING).
    A missing file is the normal "no whitelist activated yet" state
    and is logged at DEBUG.
    """
    if not lock_path.exists():
        logger.debug(
            "[C15 system_tasks] lock file absent: %s", lock_path
        )
        return None
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning(
            "[C15 system_tasks] failed to read lock %s: %s", lock_path, exc
        )
        return None
    if not text.startswith(_LOCK_PREFIX):
        logger.warning(
            "[C15 system_tasks] lock %s missing %r prefix; treating as tampered",
            lock_path,
            _LOCK_PREFIX,
        )
        return None
    digest = text[len(_LOCK_PREFIX):].strip()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        logger.warning(
            "[C15 system_tasks] lock %s has malformed digest", lock_path
        )
        return None
    return digest


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SystemTaskRegistry:
    """In-memory whitelist parsed from ``SYSTEM_TASKS.yaml``.

    Construct via :func:`load_registry` — the public ``__init__`` is
    intended for tests that want to build a registry without writing
    YAML to disk.
    """

    def __init__(self, tasks: dict[str, SystemTask]) -> None:
        self._tasks = dict(tasks)

    @property
    def tasks(self) -> dict[str, SystemTask]:
        """Read-only view of the registry; tests may inspect this but
        callers should mutate via :func:`load_registry`."""
        return dict(self._tasks)

    def get(self, task_id: str) -> SystemTask | None:
        return self._tasks.get(task_id)

    def try_match(
        self,
        task_id: str,
        tool_name: str,
        params: dict[str, Any],
        *,
        workspace: Path | None = None,
    ) -> SystemTask | None:
        """Return the matching task or ``None`` if anything disqualifies
        the call.

        Disqualifiers (in evaluation order):

        1. ``task_id`` is not in the registry.
        2. ``tool_name`` is not in the task's allowed ``tools`` tuple.
        3. Any path-bearing param falls outside the task's
           ``path_globs``.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if tool_name not in task.tools:
            logger.debug(
                "[C15 system_tasks] task %s rejects tool %s (allowed=%s)",
                task_id,
                tool_name,
                task.tools,
            )
            return None
        if not _params_match_globs(params, task.path_globs, workspace=workspace):
            logger.debug(
                "[C15 system_tasks] task %s rejects params (globs=%s)",
                task_id,
                task.path_globs,
            )
            return None
        return task


def load_registry(
    yaml_path: Path,
    lock_path: Path,
    *,
    yaml_loader=None,
) -> SystemTaskRegistry:
    """Load ``SYSTEM_TASKS.yaml`` after hash-verifying the lock.

    Args:
        yaml_path: Path to ``identity/SYSTEM_TASKS.yaml``.
        lock_path: Path to ``.openakita/system_tasks.lock``.
        yaml_loader: Optional injected loader (tests) — must accept
            ``str`` and return ``dict``. Defaults to ``yaml.safe_load``.

    Returns:
        An empty registry when:
        - YAML file does not exist
        - lock file does not exist or is malformed
        - hashes mismatch (lock was not regenerated after YAML change)
        - YAML structure is invalid

        Each empty-registry path logs a clear WARNING so operators can
        diagnose. The function never raises on bad input — keeps
        PolicyEngine boot resilient.

    Notes:
        We deliberately fail **closed** (empty registry, no bypass)
        rather than raising. An exception here would propagate into
        engine boot and could bring down the whole policy layer for
        what is at most a config typo.
    """
    if not yaml_path.exists():
        logger.debug(
            "[C15 system_tasks] %s absent — no whitelist active", yaml_path
        )
        return SystemTaskRegistry({})

    try:
        raw_bytes = yaml_path.read_bytes()
    except OSError as exc:
        logger.warning(
            "[C15 system_tasks] failed to read %s: %s — empty registry",
            yaml_path,
            exc,
        )
        return SystemTaskRegistry({})

    actual_hash = compute_yaml_hash(raw_bytes)
    expected_hash = read_lock(lock_path)
    if expected_hash is None:
        logger.warning(
            "[C15 system_tasks] no valid lock at %s — empty registry "
            "(operator must run setup-center 'regenerate system_tasks lock' "
            "to activate the whitelist)",
            lock_path,
        )
        return SystemTaskRegistry({})
    if expected_hash != actual_hash:
        logger.warning(
            "[C15 system_tasks] lock mismatch for %s: expected %s got %s — "
            "empty registry (YAML changed without operator consent)",
            yaml_path,
            expected_hash[:12],
            actual_hash[:12],
        )
        return SystemTaskRegistry({})

    if yaml_loader is None:
        try:
            import yaml

            yaml_loader = yaml.safe_load
        except ImportError:  # pragma: no cover - pyyaml is in deps
            logger.error(
                "[C15 system_tasks] PyYAML not available; empty registry"
            )
            return SystemTaskRegistry({})

    try:
        data = yaml_loader(raw_bytes.decode("utf-8"))
    except Exception as exc:
        logger.warning(
            "[C15 system_tasks] YAML parse failed for %s: %s — empty registry",
            yaml_path,
            exc,
        )
        return SystemTaskRegistry({})

    if not isinstance(data, dict):
        logger.warning(
            "[C15 system_tasks] %s top-level not a mapping — empty registry",
            yaml_path,
        )
        return SystemTaskRegistry({})

    tasks_raw = data.get("tasks")
    if not isinstance(tasks_raw, list):
        logger.warning(
            "[C15 system_tasks] %s has no 'tasks' list — empty registry",
            yaml_path,
        )
        return SystemTaskRegistry({})

    out: dict[str, SystemTask] = {}
    for idx, item in enumerate(tasks_raw):
        if not isinstance(item, dict):
            logger.warning(
                "[C15 system_tasks] tasks[%d] not a mapping; skipping", idx
            )
            continue
        try:
            task = _parse_task(item)
        except ValueError as exc:
            logger.warning(
                "[C15 system_tasks] tasks[%d] invalid: %s; skipping",
                idx,
                exc,
            )
            continue
        if task.id in out:
            logger.warning(
                "[C15 system_tasks] duplicate task id %r at index %d; "
                "skipping (first occurrence wins)",
                task.id,
                idx,
            )
            continue
        out[task.id] = task

    logger.info(
        "[C15 system_tasks] loaded %d task(s) from %s", len(out), yaml_path
    )
    return SystemTaskRegistry(out)


def _parse_task(raw: dict[str, Any]) -> SystemTask:
    task_id = raw.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("'id' is required and must be a non-empty string")

    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ValueError("'description' must be a string")

    tools = raw.get("tools")
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError("'tools' must be a list of strings")
    if not tools:
        raise ValueError("'tools' must not be empty")

    path_globs = raw.get("path_globs", [])
    if not isinstance(path_globs, list) or not all(
        isinstance(g, str) for g in path_globs
    ):
        raise ValueError("'path_globs' must be a list of strings")

    requires_backup = raw.get("requires_backup", True)
    if not isinstance(requires_backup, bool):
        raise ValueError("'requires_backup' must be a boolean")

    return SystemTask(
        id=task_id.strip(),
        description=description,
        tools=tuple(tools),
        path_globs=tuple(path_globs),
        requires_backup=requires_backup,
    )


# ---------------------------------------------------------------------------
# Param ↔ glob matching
# ---------------------------------------------------------------------------


_PATH_PARAM_KEYS: tuple[str, ...] = (
    "path",
    "file",
    "filename",
    "source",
    "destination",
    "target",
    "src",
    "dst",
    "to",
    "from",
)


def _extract_paths(params: dict[str, Any]) -> list[str]:
    """Collect every value in ``params`` that *looks like* a filesystem
    path. We use a small hand-curated whitelist of common param keys
    plus any nested string that contains a path separator.

    The list is conservative: when in doubt, include — the strictness
    rule (require ALL matched paths to fall in path_globs) means
    false-positive path extraction errs on the side of *rejecting*
    bypass, which is the safe direction.
    """
    out: list[str] = []
    for key, val in params.items():
        if not isinstance(val, str):
            continue
        if key in _PATH_PARAM_KEYS or "/" in val or "\\" in val:
            out.append(val)
    return out


def _normalize_path(p: str, workspace: Path | None) -> str:
    """Normalize ``p`` for glob matching:

    - Forward slashes only.
    - Strip a leading ``./``.
    - When ``p`` is absolute and falls under ``workspace``, render the
      workspace-relative form (so YAML globs like
      ``data/audit/**`` match without forcing the operator to encode
      the absolute workspace path).
    """
    p_norm = p.replace("\\", "/")
    if p_norm.startswith("./"):
        p_norm = p_norm[2:]
    if workspace is not None:
        try:
            abs_p = Path(p_norm)
            if abs_p.is_absolute():
                rel = abs_p.resolve().relative_to(workspace.resolve())
                p_norm = str(rel).replace("\\", "/")
        except (ValueError, OSError):
            pass
    return p_norm


def _glob_match(path: str, pattern: str) -> bool:
    """``fnmatch.fnmatch`` with ``**`` support for recursive globs.

    fnmatch alone doesn't understand ``**``; we expand by trying both
    "match anywhere" and "match exact directory boundary" semantics:

    - ``data/audit/**`` matches ``data/audit/file.jsonl`` AND
      ``data/audit/sub/file.jsonl``.
    - ``*.log`` matches ``app.log`` but NOT ``logs/app.log``
      (fnmatch's `*` excludes the path separator).
    """
    if "**" not in pattern:
        return fnmatch(path, pattern)
    # Recursive glob: split on ``**`` and verify prefix/suffix anchors.
    # Pattern ``a/**/b`` → path must start with prefix glob ``a/`` and
    # end with suffix glob ``/b`` (or ``b`` if no trailing slash).
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        return path == prefix[:-1] or path.startswith(prefix)
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        return fnmatch(path, suffix) or any(
            fnmatch(path[i:], suffix) for i in range(len(path))
        )
    parts = pattern.split("**")
    if len(parts) == 2:
        prefix, suffix = parts
        return path.startswith(prefix) and path.endswith(suffix)
    # Multiple ``**`` — rare; fall back to literal fnmatch.
    return fnmatch(path, pattern)


def _params_match_globs(
    params: dict[str, Any],
    globs: tuple[str, ...],
    *,
    workspace: Path | None,
) -> bool:
    """Every extracted path must match at least one glob; if no paths
    are extracted at all, the call still passes (purely-non-file
    tools like a hypothetical ``rebuild_index`` with no path args)."""
    paths = _extract_paths(params)
    if not paths:
        return True
    if not globs:
        # Tool has path args but task whitelist had no path_globs →
        # scope undefined → reject (safer to fall through to
        # PolicyEngine than to bypass with no scope).
        return False
    for p in paths:
        norm = _normalize_path(p, workspace)
        if not any(_glob_match(norm, g) for g in globs):
            return False
    return True


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _append_audit(audit_path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record to the audit jsonl with hash chain.

    C16 wired this to :class:`policy_v2.audit_chain.ChainedJsonlWriter` —
    every line carries ``prev_hash`` + ``row_hash`` so any post-hoc edit
    breaks the chain at that exact line. Failures are logged at WARNING
    but never raised — losing one audit line is preferable to crashing
    the bypass path. On chain-write error we fall back to raw append
    (defensive: do not lose evidence if the chain writer itself blows up).
    """
    chain_exc: Exception | None = None
    try:
        from .audit_chain import get_writer

        get_writer(audit_path).append(record)
        return
    except Exception as exc:  # noqa: BLE001 — best-effort audit append
        # C17 二轮: same fix as evolution_window. OSError (filelock
        # timeout, disk full, etc.) used to short-circuit without trying
        # the raw fallback, defeating the "never lose audit lines"
        # guarantee. Now every failure falls through to raw append.
        chain_exc = exc

    logger.warning(
        "[C16 system_tasks] chain append failed for %s: %s; "
        "falling back to raw append.",
        audit_path,
        chain_exc,
    )
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(
            "[C15 system_tasks] fallback raw append failed %s: %s",
            audit_path,
            exc,
        )


# ---------------------------------------------------------------------------
# Public bypass API
# ---------------------------------------------------------------------------


def request_bypass(
    *,
    task_id: str,
    tool_name: str,
    params: dict[str, Any],
    registry: SystemTaskRegistry,
    workspace: Path | None,
    checkpoint_mgr: Any | None,
    audit_path: Path,
) -> BypassDecision | None:
    """Attempt to bypass PolicyEngine for an operator-whitelisted task.

    Returns:
        :class:`BypassDecision` on success (caller MUST call
        :func:`finalize_bypass`), or ``None`` if the task is not
        whitelisted / scope mismatches / lock failed. ``None`` is the
        signal that callers should fall back to the normal PolicyEngine
        path.

    Notes:
        Even a *match* doesn't perform the actual tool call — it only
        records the intent and (when ``requires_backup=True``) creates
        a checkpoint. The caller is responsible for invoking the
        handler.
    """
    task = registry.try_match(
        task_id, tool_name, params, workspace=workspace
    )
    if task is None:
        # Audit the rejection so operators see "why didn't bypass run?"
        _append_audit(
            audit_path,
            {
                "ts": time.time(),
                "type": "system_task_bypass_reject",
                "task_id": task_id,
                "tool_name": tool_name,
                "params_summary": _summarize_params(params),
                "reason": "no_match",
            },
        )
        return None

    checkpoint_id: str | None = None
    if task.requires_backup and checkpoint_mgr is not None:
        try:
            checkpoint_id = checkpoint_mgr.create_checkpoint(
                file_paths=_extract_paths(params),
                tool_name=f"system_task:{task.id}",
                description=task.description,
            )
        except Exception as exc:
            logger.warning(
                "[C15 system_tasks] checkpoint failed for task %s: %s — "
                "refusing bypass (safer to deny than to mutate without "
                "rollback safety net)",
                task.id,
                exc,
            )
            _append_audit(
                audit_path,
                {
                    "ts": time.time(),
                    "type": "system_task_bypass_reject",
                    "task_id": task_id,
                    "tool_name": tool_name,
                    "params_summary": _summarize_params(params),
                    "reason": "checkpoint_failed",
                    "error": str(exc),
                },
            )
            return None

    audit_event_id = uuid.uuid4().hex[:16]
    params_summary = _summarize_params(params)
    _append_audit(
        audit_path,
        {
            "ts": time.time(),
            "type": "system_task_bypass_start",
            "event_id": audit_event_id,
            "task_id": task.id,
            "task_description": task.description,
            "tool_name": tool_name,
            "params_summary": params_summary,
            "checkpoint_id": checkpoint_id,
            "requires_backup": task.requires_backup,
        },
    )
    logger.info(
        "[C15 system_tasks] bypass granted: task=%s tool=%s checkpoint=%s",
        task.id,
        tool_name,
        checkpoint_id,
    )
    return BypassDecision(
        task=task,
        checkpoint_id=checkpoint_id,
        audit_event_id=audit_event_id,
        tool_name=tool_name,
        params_summary=params_summary,
    )


def finalize_bypass(
    decision: BypassDecision,
    *,
    audit_path: Path,
    success: bool,
    error: str | None = None,
) -> None:
    """Close the audit record opened by :func:`request_bypass`.

    Args:
        decision: The :class:`BypassDecision` returned earlier.
        audit_path: Same audit JSONL path used at request time.
        success: Whether the tool call completed without exception.
        error: Failure message (when ``success=False``). Logged
            verbatim — sanitize before passing if it can contain
            secrets.
    """
    _append_audit(
        audit_path,
        {
            "ts": time.time(),
            "type": "system_task_bypass_end",
            "event_id": decision.audit_event_id,
            "task_id": decision.task.id,
            "tool_name": decision.tool_name,
            "checkpoint_id": decision.checkpoint_id,
            "success": success,
            "error": error,
            "duration_ms": int((time.time() - decision.started_at) * 1000),
        },
    )


def _summarize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Truncate / sanitize ``params`` for the audit record so we don't
    accidentally write 100MB of file content to the jsonl.

    Strings longer than 256 chars are truncated; non-JSON-serializable
    values are coerced to ``repr()``.
    """
    out: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str):
            out[k] = v if len(v) <= 256 else v[:256] + f"…(+{len(v) - 256} chars)"
        elif isinstance(v, (int, float, bool, type(None))):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = f"<{type(v).__name__} len={len(v)}>"
        elif isinstance(v, dict):
            out[k] = f"<dict keys={sorted(v.keys())[:5]}>"
        else:
            out[k] = repr(v)[:256]
    return out


# ---------------------------------------------------------------------------
# Default paths (used by callers that don't want to specify explicitly)
# ---------------------------------------------------------------------------


def default_yaml_path(workspace: Path) -> Path:
    return workspace / "identity" / "SYSTEM_TASKS.yaml"


def default_lock_path(workspace: Path) -> Path:
    return workspace / ".openakita" / "system_tasks.lock"


def default_audit_path(workspace: Path) -> Path:
    return workspace / "data" / "audit" / "system_tasks.jsonl"


__all__ = [
    "BypassDecision",
    "SystemTask",
    "SystemTaskRegistry",
    "SystemTasksLockMismatch",
    "compute_yaml_hash",
    "default_audit_path",
    "default_lock_path",
    "default_yaml_path",
    "finalize_bypass",
    "load_registry",
    "read_lock",
    "request_bypass",
    "write_lock",
]
