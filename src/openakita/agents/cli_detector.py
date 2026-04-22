# src/openakita/agents/cli_detector.py
"""External-CLI binary detection.

Discovers which AI-coding CLIs are installed on PATH (claude, codex, goose, ...).
Version probes run concurrently with `asyncio.gather`. Results are cached for 60 s
keyed on `()` (the detection is global, not per-session).

Used by:
- `api/routes/sessions.py` to filter the "External CLIs" session browser.
- `api/routes/config.py::list_extensions` to surface installed CLIs as Extensions.
- `apps/setup-center/AgentManagerView.tsx` wizard to populate the provider dropdown.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum

from openakita.utils.path_helper import which_command


class CliProviderId(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    OPENCODE = "opencode"
    GEMINI = "gemini"
    COPILOT = "copilot"
    DROID = "droid"
    CURSOR = "cursor"
    QWEN = "qwen"
    GOOSE = "goose"


# CLI binary names as shipped. Keep this dict in sync with `cli_providers/*.py`
# module names — adding a provider means adding a file there AND a row here.
_BINARY_NAMES: dict[CliProviderId, str] = {
    CliProviderId.CLAUDE_CODE: "claude",
    CliProviderId.CODEX: "codex",
    CliProviderId.OPENCODE: "opencode",
    CliProviderId.GEMINI: "gemini",
    CliProviderId.COPILOT: "copilot",
    CliProviderId.DROID: "droid",
    CliProviderId.CURSOR: "cursor",
    CliProviderId.QWEN: "qwen",
    CliProviderId.GOOSE: "goose",
}


@dataclass(frozen=True)
class DetectedCli:
    provider_id: CliProviderId
    binary_name: str
    binary_path: str | None
    version: str | None
    error: str | None


_CACHE_TTL_S = 60.0
_cache: tuple[float, dict[CliProviderId, DetectedCli]] | None = None


def reset_cache() -> None:
    """Drop the TTL cache. Call from tests; no-op in production."""
    global _cache
    _cache = None


async def discover_all() -> dict[CliProviderId, DetectedCli]:
    global _cache
    now = time.monotonic()
    if _cache is not None and (now - _cache[0]) < _CACHE_TTL_S:
        return _cache[1]
    results = await asyncio.gather(*(
        _probe_one(pid, binary) for pid, binary in _BINARY_NAMES.items()
    ))
    bucket = {r.provider_id: r for r in results}
    _cache = (now, bucket)
    return bucket


async def _probe_one(provider_id: CliProviderId, binary: str) -> DetectedCli:
    path = which_command(binary)
    if path is None:
        return DetectedCli(provider_id, binary, None, None, "not found")
    version, err = await _read_version(path)
    return DetectedCli(provider_id, binary, path, version, err)


async def _read_version(binary_path: str) -> tuple[str | None, str | None]:
    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return None, f"spawn failed: {exc}"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except TimeoutError:
        proc.kill()
        return None, "probe timed out"
    if proc.returncode != 0:
        first = stderr.decode(errors="replace").splitlines()
        return None, (first[0].strip() if first else f"exit {proc.returncode}")
    first = stdout.decode(errors="replace").splitlines()
    return (first[0].strip() if first else ""), None
