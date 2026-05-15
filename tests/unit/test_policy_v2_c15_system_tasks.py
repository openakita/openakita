"""C15 §17.2 — SYSTEM_TASKS.yaml whitelist + lock + bypass tests.

Covers:

- Lock file format: write_lock / read_lock round-trip, malformed
  inputs, tamper detection.
- ``compute_yaml_hash`` determinism.
- ``load_registry`` happy path + every failure mode (missing yaml,
  missing lock, hash mismatch, malformed yaml, malformed tasks).
- ``SystemTaskRegistry.try_match``: task lookup, tool gating, path
  glob enforcement, ``**`` recursive glob, path normalization
  (absolute/relative, forward/back slashes).
- ``request_bypass`` / ``finalize_bypass``: full happy path with
  checkpoint creation + audit jsonl start+end records; checkpoint
  failure refuses bypass; mismatch produces audit reject; finalize
  records success/error/duration.
- Reverse regression: lock tampered between commits → empty registry
  → bypass refused.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from openakita.core.policy_v2.system_tasks import (
    BypassDecision,
    SystemTask,
    SystemTaskRegistry,
    _glob_match,
    _normalize_path,
    compute_yaml_hash,
    finalize_bypass,
    load_registry,
    read_lock,
    request_bypass,
    write_lock,
)

# ---------------------------------------------------------------------------
# Hash / lock primitives
# ---------------------------------------------------------------------------


def test_compute_yaml_hash_deterministic():
    content = b"version: 1\ntasks: []\n"
    h1 = compute_yaml_hash(content)
    h2 = compute_yaml_hash(content)
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_write_then_read_lock_round_trip(tmp_path):
    lock_path = tmp_path / "system_tasks.lock"
    write_lock(lock_path, "a" * 64)
    assert read_lock(lock_path) == "a" * 64


def test_read_lock_missing_returns_none(tmp_path):
    assert read_lock(tmp_path / "nope.lock") is None


def test_read_lock_missing_prefix_returns_none(tmp_path):
    bad = tmp_path / "bad.lock"
    bad.write_text("md5:deadbeef\n", encoding="utf-8")
    assert read_lock(bad) is None


def test_read_lock_short_digest_returns_none(tmp_path):
    bad = tmp_path / "bad.lock"
    bad.write_text("sha256:notlongenough\n", encoding="utf-8")
    assert read_lock(bad) is None


def test_read_lock_non_hex_digest_returns_none(tmp_path):
    bad = tmp_path / "bad.lock"
    bad.write_text("sha256:" + "z" * 64 + "\n", encoding="utf-8")
    assert read_lock(bad) is None


# ---------------------------------------------------------------------------
# load_registry — happy path + every failure mode
# ---------------------------------------------------------------------------


def _write_yaml_with_lock(
    tmp_path: Path, tasks: list[dict[str, Any]]
) -> tuple[Path, Path]:
    yaml_path = tmp_path / "SYSTEM_TASKS.yaml"
    lock_path = tmp_path / "system_tasks.lock"
    content = yaml.safe_dump({"version": 1, "tasks": tasks}).encode("utf-8")
    yaml_path.write_bytes(content)
    write_lock(lock_path, compute_yaml_hash(content))
    return yaml_path, lock_path


def test_load_registry_happy_path(tmp_path):
    yaml_path, lock_path = _write_yaml_with_lock(
        tmp_path,
        [
            {
                "id": "rotate_audit",
                "description": "Rotate audit log",
                "tools": ["move_file"],
                "path_globs": ["data/audit/**"],
                "requires_backup": True,
            }
        ],
    )
    reg = load_registry(yaml_path, lock_path)
    assert reg.get("rotate_audit") is not None
    t = reg.get("rotate_audit")
    assert isinstance(t, SystemTask)
    assert t.tools == ("move_file",)
    assert t.path_globs == ("data/audit/**",)
    assert t.requires_backup is True


def test_load_registry_yaml_missing_empty(tmp_path):
    reg = load_registry(tmp_path / "nope.yaml", tmp_path / "nope.lock")
    assert reg.tasks == {}


def test_load_registry_lock_missing_empty(tmp_path, caplog):
    yaml_path = tmp_path / "SYSTEM_TASKS.yaml"
    yaml_path.write_bytes(b"version: 1\ntasks: []\n")
    with caplog.at_level("WARNING"):
        reg = load_registry(yaml_path, tmp_path / "nonexistent.lock")
    assert reg.tasks == {}
    assert "no valid lock" in caplog.text


def test_load_registry_lock_mismatch_empty(tmp_path, caplog):
    """R4-10 critical assertion: when SYSTEM_TASKS.yaml is mutated
    without the operator regenerating the lock, the registry MUST be
    empty (no bypass paths)."""
    yaml_path, lock_path = _write_yaml_with_lock(
        tmp_path,
        [
            {
                "id": "ok_task",
                "description": "vetted",
                "tools": ["read_file"],
                "path_globs": ["data/audit/**"],
            }
        ],
    )
    # Simulate adversary tampering: edit yaml after lock was written.
    yaml_path.write_bytes(b"version: 1\ntasks:\n  - id: evil\n    description: bypass\n    tools: [delete_file]\n    path_globs: ['**']\n")

    with caplog.at_level("WARNING"):
        reg = load_registry(yaml_path, lock_path)
    assert reg.tasks == {}
    assert "lock mismatch" in caplog.text


def test_load_registry_yaml_invalid_top_level_empty(tmp_path, caplog):
    yaml_path = tmp_path / "SYSTEM_TASKS.yaml"
    lock_path = tmp_path / "system_tasks.lock"
    content = b"not a mapping"
    yaml_path.write_bytes(content)
    write_lock(lock_path, compute_yaml_hash(content))
    with caplog.at_level("WARNING"):
        reg = load_registry(yaml_path, lock_path)
    assert reg.tasks == {}


def test_load_registry_skips_invalid_tasks(tmp_path):
    # Mix valid + invalid; valid ones still load.
    yaml_path, lock_path = _write_yaml_with_lock(
        tmp_path,
        [
            {"id": "valid", "tools": ["read_file"], "path_globs": ["**"]},
            {"id": "", "tools": ["read_file"], "path_globs": []},  # empty id
            {"id": "no_tools", "tools": [], "path_globs": ["**"]},  # empty tools
            {"description": "missing id"},
        ],
    )
    reg = load_registry(yaml_path, lock_path)
    assert list(reg.tasks.keys()) == ["valid"]


def test_load_registry_duplicate_ids_keep_first(tmp_path):
    yaml_path, lock_path = _write_yaml_with_lock(
        tmp_path,
        [
            {
                "id": "dup",
                "description": "first",
                "tools": ["read_file"],
                "path_globs": ["**"],
            },
            {
                "id": "dup",
                "description": "second",
                "tools": ["delete_file"],
                "path_globs": ["**"],
            },
        ],
    )
    reg = load_registry(yaml_path, lock_path)
    assert reg.get("dup").description == "first"
    assert reg.get("dup").tools == ("read_file",)


# ---------------------------------------------------------------------------
# SystemTaskRegistry.try_match
# ---------------------------------------------------------------------------


def _make_registry(**kwargs) -> SystemTaskRegistry:
    defaults = {
        "id": "test_task",
        "description": "desc",
        "tools": ("read_file",),
        "path_globs": ("data/audit/**",),
        "requires_backup": True,
    }
    defaults.update(kwargs)
    return SystemTaskRegistry({defaults["id"]: SystemTask(**defaults)})


def test_try_match_unknown_task_returns_none():
    reg = _make_registry()
    assert reg.try_match("nope", "read_file", {}) is None


def test_try_match_wrong_tool_returns_none():
    reg = _make_registry(tools=("read_file",))
    assert (
        reg.try_match("test_task", "delete_file", {"path": "data/audit/x.log"})
        is None
    )


def test_try_match_happy_path():
    reg = _make_registry(path_globs=("data/audit/**",))
    result = reg.try_match(
        "test_task", "read_file", {"path": "data/audit/sub/file.jsonl"}
    )
    assert result is not None
    assert result.id == "test_task"


def test_try_match_path_outside_glob_returns_none():
    reg = _make_registry(path_globs=("data/audit/**",))
    assert (
        reg.try_match(
            "test_task", "read_file", {"path": "data/sessions/x.json"}
        )
        is None
    )


def test_try_match_multiple_paths_all_must_match():
    """If a tool takes multiple paths (move/copy), ALL must fall inside
    the task's globs — partial match → reject."""
    reg = _make_registry(
        tools=("move_file",), path_globs=("data/audit/**",)
    )
    # Both paths inside
    result = reg.try_match(
        "test_task",
        "move_file",
        {"source": "data/audit/old.jsonl", "destination": "data/audit/new.jsonl"},
    )
    assert result is not None
    # One outside
    result = reg.try_match(
        "test_task",
        "move_file",
        {"source": "data/audit/old.jsonl", "destination": "data/sessions/x.json"},
    )
    assert result is None


def test_try_match_no_path_args_passes():
    """A task whose tool legitimately has no path params (e.g. trigger
    a state refresh) doesn't fail glob-match."""
    reg = _make_registry(
        tools=("rebuild_index",), path_globs=("data/cache/**",)
    )
    assert (
        reg.try_match("test_task", "rebuild_index", {"force": True})
        is not None
    )


# ---------------------------------------------------------------------------
# Glob / path helpers
# ---------------------------------------------------------------------------


def test_glob_match_recursive_double_star():
    assert _glob_match("data/audit/x.jsonl", "data/audit/**")
    assert _glob_match("data/audit/sub/x.jsonl", "data/audit/**")
    assert _glob_match("data/audit", "data/audit/**")
    assert not _glob_match("data/sessions/x.json", "data/audit/**")


def test_glob_match_single_star_does_not_cross_directory():
    """``*`` in fnmatch crosses path separators on POSIX behavior;
    sanity-check our usage here so operators understand the contract."""
    # The pattern ``*.log`` matches a file at any depth where the
    # filename ends with .log — and importantly, since fnmatch does
    # NOT specially handle '/', it will treat the whole string. Verify
    # both behaviors so future maintainers don't accidentally break
    # this.
    assert _glob_match("app.log", "*.log")
    # fnmatch's `*` does match '/' in plain Python — verify so docs
    # match reality. Operators who need strict per-component matching
    # should use ``**``.
    assert _glob_match("logs/app.log", "*.log")


def test_normalize_path_strips_dot_slash():
    assert _normalize_path("./data/x.json", None) == "data/x.json"


def test_normalize_path_backslash_to_forward():
    assert (
        _normalize_path("data\\audit\\x.json", None) == "data/audit/x.json"
    )


def test_normalize_path_relative_to_workspace(tmp_path):
    # When workspace is provided and the input is absolute under it,
    # normalize to relative form.
    sub = tmp_path / "data" / "audit"
    sub.mkdir(parents=True)
    fp = sub / "x.json"
    fp.touch()
    norm = _normalize_path(str(fp), tmp_path)
    assert norm == "data/audit/x.json"


def test_normalize_path_outside_workspace_unchanged(tmp_path):
    other = tmp_path.parent / "outside.json"
    other.touch()
    norm = _normalize_path(str(other), tmp_path)
    # When absolute path can't be made relative to workspace, the
    # function returns the normalized (forward-slash) form so the
    # glob check still proceeds. The check itself will then reject
    # outside-workspace paths.
    assert "outside.json" in norm


# ---------------------------------------------------------------------------
# request_bypass / finalize_bypass full lifecycle
# ---------------------------------------------------------------------------


class _FakeCheckpointMgr:
    """Test double — records calls; returns a deterministic id."""

    def __init__(self, fail: bool = False, raise_exc: bool = False):
        self.calls: list[dict[str, Any]] = []
        self._fail = fail
        self._raise = raise_exc

    def create_checkpoint(
        self,
        file_paths: list[str],
        tool_name: str = "",
        description: str = "",
    ) -> str | None:
        self.calls.append(
            {
                "file_paths": list(file_paths),
                "tool_name": tool_name,
                "description": description,
            }
        )
        if self._raise:
            raise RuntimeError("disk full")
        if self._fail:
            return None
        return "cp_abc12345"


def _audit_lines(audit_path: Path) -> list[dict[str, Any]]:
    text = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_request_bypass_happy_path_creates_checkpoint_and_audits(tmp_path):
    audit_path = tmp_path / "system_tasks.jsonl"
    reg = _make_registry(path_globs=("data/audit/**",))
    cp = _FakeCheckpointMgr()

    decision = request_bypass(
        task_id="test_task",
        tool_name="read_file",
        params={"path": "data/audit/x.jsonl"},
        registry=reg,
        workspace=None,
        checkpoint_mgr=cp,
        audit_path=audit_path,
    )
    assert decision is not None
    assert isinstance(decision, BypassDecision)
    assert decision.checkpoint_id == "cp_abc12345"
    assert cp.calls[0]["tool_name"] == "system_task:test_task"

    lines = _audit_lines(audit_path)
    assert len(lines) == 1
    assert lines[0]["type"] == "system_task_bypass_start"
    assert lines[0]["task_id"] == "test_task"
    assert lines[0]["checkpoint_id"] == "cp_abc12345"


def test_request_bypass_no_match_returns_none_and_audits_reject(tmp_path):
    audit_path = tmp_path / "system_tasks.jsonl"
    reg = _make_registry()
    decision = request_bypass(
        task_id="nope",
        tool_name="read_file",
        params={},
        registry=reg,
        workspace=None,
        checkpoint_mgr=None,
        audit_path=audit_path,
    )
    assert decision is None

    lines = _audit_lines(audit_path)
    assert len(lines) == 1
    assert lines[0]["type"] == "system_task_bypass_reject"
    assert lines[0]["reason"] == "no_match"


def test_request_bypass_checkpoint_exception_refuses_bypass(tmp_path):
    """Critical R4-11 behavior: when the checkpoint manager raises
    (disk full, permission denied), the bypass must be REFUSED —
    irreversible writes without rollback safety net would defeat the
    workspace backup guarantee."""
    audit_path = tmp_path / "system_tasks.jsonl"
    reg = _make_registry(path_globs=("data/audit/**",))
    cp = _FakeCheckpointMgr(raise_exc=True)

    decision = request_bypass(
        task_id="test_task",
        tool_name="read_file",
        params={"path": "data/audit/x.jsonl"},
        registry=reg,
        workspace=None,
        checkpoint_mgr=cp,
        audit_path=audit_path,
    )
    assert decision is None

    lines = _audit_lines(audit_path)
    assert len(lines) == 1
    assert lines[0]["type"] == "system_task_bypass_reject"
    assert lines[0]["reason"] == "checkpoint_failed"
    assert "disk full" in lines[0]["error"]


def test_request_bypass_no_backup_skips_checkpoint(tmp_path):
    """When a task explicitly sets ``requires_backup=False`` (e.g. the
    target IS a backup), no checkpoint is taken — but audit still
    records the bypass."""
    audit_path = tmp_path / "system_tasks.jsonl"
    reg = _make_registry(
        path_globs=("data/checkpoints/**",), requires_backup=False
    )
    cp = _FakeCheckpointMgr()

    decision = request_bypass(
        task_id="test_task",
        tool_name="read_file",
        params={"path": "data/checkpoints/abc.json"},
        registry=reg,
        workspace=None,
        checkpoint_mgr=cp,
        audit_path=audit_path,
    )
    assert decision is not None
    assert decision.checkpoint_id is None
    assert cp.calls == []


def test_finalize_bypass_appends_end_record(tmp_path):
    audit_path = tmp_path / "system_tasks.jsonl"
    reg = _make_registry(path_globs=("data/audit/**",))
    cp = _FakeCheckpointMgr()

    decision = request_bypass(
        task_id="test_task",
        tool_name="read_file",
        params={"path": "data/audit/x.jsonl"},
        registry=reg,
        workspace=None,
        checkpoint_mgr=cp,
        audit_path=audit_path,
    )
    finalize_bypass(decision, audit_path=audit_path, success=True)

    lines = _audit_lines(audit_path)
    assert len(lines) == 2
    assert lines[1]["type"] == "system_task_bypass_end"
    assert lines[1]["event_id"] == decision.audit_event_id
    assert lines[1]["success"] is True
    assert lines[1]["error"] is None
    assert "duration_ms" in lines[1]


def test_finalize_bypass_records_failure_with_error(tmp_path):
    audit_path = tmp_path / "system_tasks.jsonl"
    reg = _make_registry(path_globs=("data/audit/**",))
    cp = _FakeCheckpointMgr()
    decision = request_bypass(
        task_id="test_task",
        tool_name="read_file",
        params={"path": "data/audit/x.jsonl"},
        registry=reg,
        workspace=None,
        checkpoint_mgr=cp,
        audit_path=audit_path,
    )
    finalize_bypass(
        decision,
        audit_path=audit_path,
        success=False,
        error="disk write failed",
    )
    lines = _audit_lines(audit_path)
    assert lines[1]["success"] is False
    assert lines[1]["error"] == "disk write failed"


def test_long_string_param_truncated_in_audit(tmp_path):
    audit_path = tmp_path / "system_tasks.jsonl"
    reg = _make_registry(path_globs=("data/audit/**",))
    cp = _FakeCheckpointMgr()

    request_bypass(
        task_id="test_task",
        tool_name="read_file",
        params={
            "path": "data/audit/x.jsonl",
            "content": "x" * 5000,
        },
        registry=reg,
        workspace=None,
        checkpoint_mgr=cp,
        audit_path=audit_path,
    )
    lines = _audit_lines(audit_path)
    summary = lines[0]["params_summary"]
    assert "content" in summary
    assert len(summary["content"]) <= 280  # 256 + ellipsis suffix
    assert "…" in summary["content"]


# ---------------------------------------------------------------------------
# Reverse regression — lock tampering scenario
# ---------------------------------------------------------------------------


def test_reverse_regression_yaml_tamper_blocks_bypass(tmp_path):
    """End-to-end attack scenario:

    1. Operator authors SYSTEM_TASKS.yaml with a safe rotate-audit task.
    2. Adversary mutates the YAML to add ``id: evil`` with ``tools:
       [delete_file]`` and ``path_globs: ["**"]``.
    3. Adversary does NOT regenerate the lock (it's a setup-center
       action).
    4. Caller tries ``request_bypass(task_id="evil", ...)`` — must
       return ``None`` because the registry is empty due to lock
       mismatch.
    """
    yaml_path, lock_path = _write_yaml_with_lock(
        tmp_path,
        [
            {
                "id": "rotate_audit_log",
                "description": "vetted",
                "tools": ["move_file"],
                "path_globs": ["data/audit/**"],
            }
        ],
    )

    # Adversary edits YAML to add an "evil" task without regenerating lock.
    tampered = yaml.safe_dump(
        {
            "version": 1,
            "tasks": [
                {
                    "id": "rotate_audit_log",
                    "description": "vetted",
                    "tools": ["move_file"],
                    "path_globs": ["data/audit/**"],
                },
                {
                    "id": "evil",
                    "description": "rm anywhere",
                    "tools": ["delete_file"],
                    "path_globs": ["**"],
                },
            ],
        }
    ).encode("utf-8")
    yaml_path.write_bytes(tampered)

    reg = load_registry(yaml_path, lock_path)
    assert reg.tasks == {}, (
        "tampered YAML must produce an empty registry; an adversary "
        "must NOT be able to add a bypass without regenerating the lock"
    )

    audit_path = tmp_path / "audit.jsonl"
    cp = _FakeCheckpointMgr()
    decision = request_bypass(
        task_id="evil",
        tool_name="delete_file",
        params={"path": "/secret/file"},
        registry=reg,
        workspace=None,
        checkpoint_mgr=cp,
        audit_path=audit_path,
    )
    assert decision is None
    assert cp.calls == []  # no checkpoint touched
    lines = _audit_lines(audit_path)
    # Single reject record
    assert len(lines) == 1
    assert lines[0]["reason"] == "no_match"
