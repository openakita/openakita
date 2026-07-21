"""Durable context-continuity primitives used by context compaction."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_digest(value: Any) -> str:
    """Return a stable digest for persisted context projections."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContextEpoch:
    """Fingerprint of privileged context used to produce a compaction."""

    digest: str
    system_digest: str
    tools_digest: str
    model: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def build(cls, *, system_prompt: str, tools: list | None, model: str = "") -> ContextEpoch:
        system_digest = content_digest(system_prompt or "")
        tools_digest = content_digest(tools or [])
        return cls(
            digest=content_digest(
                {"system": system_digest, "tools": tools_digest, "model": model or ""}
            ),
            system_digest=system_digest,
            tools_digest=tools_digest,
            model=model or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompactionContribution:
    """A bounded, named context block supplied before compaction."""

    name: str
    content: str
    priority: int = 50
    max_tokens: int = 500


@runtime_checkable
class CompactionContributor(Protocol):
    """Extension point for plans, memory, plugins, or domain runtimes."""

    def contribute_to_compaction(
        self,
        *,
        session_id: str,
        messages: list[dict],
        context_epoch: ContextEpoch,
    ) -> CompactionContribution | list[CompactionContribution] | None: ...


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Read-only VCS snapshot associated with a compaction checkpoint."""

    id: str
    session_id: str
    root: str
    capture_status: str = "unknown"
    capture_error: str = ""
    vcs: str = ""
    head: str = ""
    status_digest: str = ""
    changed_files: list[str] = field(default_factory=list)
    patch: str = ""
    patch_truncated: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _GitResult:
    stdout: str = ""
    status: str = "success"
    error: str = ""


def _git(root: Path, *args: str, timeout: float = 4.0) -> _GitResult:
    command = "git " + " ".join(args[:2])
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return _GitResult(
            status="git_unavailable",
            error="git executable was not found",
        )
    except subprocess.TimeoutExpired:
        return _GitResult(
            status="command_failed",
            error=f"{command} timed out after {timeout:g} seconds",
        )
    except OSError as exc:
        return _GitResult(
            status="command_failed",
            error=f"{command} could not start: {exc}"[:1000],
        )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        status = (
            "not_a_repository" if "not a git repository" in stderr.lower() else "command_failed"
        )
        detail = stderr or "no error output"
        return _GitResult(
            status=status,
            error=f"{command} exited with code {completed.returncode}: {detail}"[:1000],
        )
    return _GitResult(stdout=completed.stdout)


def capture_workspace_snapshot(
    root: str | Path | None,
    *,
    session_id: str,
    max_patch_chars: int = 2_000_000,
) -> WorkspaceSnapshot | None:
    """Capture Git identity and a bounded patch without modifying the workspace."""
    if not root:
        return None
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        return None

    top_result = _git(path, "rev-parse", "--show-toplevel")
    top = top_result.stdout.strip()
    if top_result.status != "success" or not top:
        return WorkspaceSnapshot(
            id=uuid.uuid4().hex,
            session_id=session_id,
            root=str(path),
            capture_status=(
                top_result.status if top_result.status != "success" else "command_failed"
            ),
            capture_error=top_result.error or "git rev-parse returned an empty repository root",
        )

    repo = Path(top).resolve()
    head_result = _git(repo, "rev-parse", "HEAD")
    status_result = _git(repo, "status", "--porcelain=v1", "--untracked-files=normal")
    patch_result = _git(repo, "diff", "--binary", "--no-ext-diff", "HEAD", "--", timeout=8.0)
    results = (head_result, status_result, patch_result)
    failed = next((result for result in results if result.status != "success"), None)
    head = head_result.stdout.strip()
    status = status_result.stdout
    changed_files = []
    for line in status.splitlines():
        value = line[3:].strip() if len(line) > 3 else ""
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value and value not in changed_files:
            changed_files.append(value)

    patch = patch_result.stdout
    truncated = len(patch) > max_patch_chars
    if truncated:
        patch = patch[:max_patch_chars]
    return WorkspaceSnapshot(
        id=uuid.uuid4().hex,
        session_id=session_id,
        root=str(repo),
        capture_status=failed.status if failed else "success",
        capture_error=failed.error if failed else "",
        vcs="git",
        head=head,
        status_digest=content_digest(status),
        changed_files=changed_files,
        patch=patch,
        patch_truncated=truncated,
    )


@dataclass(frozen=True)
class CompactionCheckpoint:
    """Durable projection of a compacted conversation prefix."""

    id: str
    session_id: str
    status: str
    source_digest: str
    source_message_count: int
    session_source_digest: str
    session_source_message_count: int
    summary: str
    recent_messages: list[dict]
    projected_messages: list[dict]
    tail_start_index: int
    tokens_before: int
    tokens_after: int
    epoch_digest: str
    workspace_snapshot_id: str = ""
    contributions: list[dict] = field(default_factory=list)
    model: str = ""
    agent_profile_id: str = "default"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
