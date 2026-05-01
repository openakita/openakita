"""Managed execution environments for agents, skills, and scratch scripts.

This module intentionally sits beside ``runtime_env.py`` instead of replacing it.
``runtime_env.py`` owns OpenAkita's app/agent/channel/module runtime layout.
This module owns optional, narrower Python venvs that isolate dependencies for
AgentProfile instances, skill scripts, and short-lived scratch work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .runtime_env import (
    get_agent_python_executable,
    get_app_python_executable,
    get_pip_install_args,
    get_runtime_root,
    resolve_pip_index,
    verify_python_executable,
)

logger = logging.getLogger(__name__)

ExecutionEnvScope = Literal["agent", "skill", "scratch"]


@dataclass(frozen=True)
class ExecutionEnvSpec:
    """Resolved OpenAkita-managed execution environment."""

    scope: ExecutionEnvScope
    key: str
    venv_path: Path
    python_path: Path
    bin_path: Path
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    deps_hash: str = ""


def _safe_key(raw: str, *, prefix: str = "") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw.strip()).strip("-._").lower()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    base = normalized[:48] or "default"
    return f"{prefix}{base}-{digest}" if prefix else f"{base}-{digest}"


def _deps_tuple(deps: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(sorted({str(dep).strip() for dep in deps or [] if str(dep).strip()}))


def _deps_hash(deps: tuple[str, ...]) -> str:
    if not deps:
        return ""
    payload = "\n".join(deps).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _venv_python(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _venv_bin_dir(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts"
    return venv_root / "bin"


def get_execution_envs_root() -> Path:
    return get_runtime_root() / "envs"


def resolve_agent_env(profile_id: str, deps: list[str] | tuple[str, ...] | None = None) -> ExecutionEnvSpec:
    deps_t = _deps_tuple(deps)
    key = _safe_key(profile_id or "default")
    venv = get_execution_envs_root() / "agents" / key / ".venv"
    return ExecutionEnvSpec(
        scope="agent",
        key=key,
        venv_path=venv,
        python_path=_venv_python(venv),
        bin_path=_venv_bin_dir(venv),
        dependencies=deps_t,
        deps_hash=_deps_hash(deps_t),
    )


def resolve_skill_env(skill_id: str, deps: list[str] | tuple[str, ...] | None = None) -> ExecutionEnvSpec:
    deps_t = _deps_tuple(deps)
    key = _safe_key(skill_id or "unknown-skill")
    venv = get_execution_envs_root() / "skills" / key / ".venv"
    return ExecutionEnvSpec(
        scope="skill",
        key=key,
        venv_path=venv,
        python_path=_venv_python(venv),
        bin_path=_venv_bin_dir(venv),
        dependencies=deps_t,
        deps_hash=_deps_hash(deps_t),
    )


def resolve_scratch_env(
    session_id: str | None = None,
    workspace_id: str | None = None,
    deps: list[str] | tuple[str, ...] | None = None,
) -> ExecutionEnvSpec:
    deps_t = _deps_tuple(deps)
    raw = session_id or workspace_id or "default"
    key = _safe_key(raw)
    venv = get_execution_envs_root() / "scratch" / key / ".venv"
    return ExecutionEnvSpec(
        scope="scratch",
        key=key,
        venv_path=venv,
        python_path=_venv_python(venv),
        bin_path=_venv_bin_dir(venv),
        dependencies=deps_t,
        deps_hash=_deps_hash(deps_t),
    )


def _manifest_path(spec: ExecutionEnvSpec) -> Path:
    return spec.venv_path.parent / "manifest.json"


def _read_manifest(spec: ExecutionEnvSpec) -> dict:
    try:
        return json.loads(_manifest_path(spec).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_manifest(spec: ExecutionEnvSpec, *, status: str = "ready", error: str = "") -> None:
    manifest = {
        "schema_version": 1,
        "scope": spec.scope,
        "key": spec.key,
        "venv_path": str(spec.venv_path),
        "python_path": str(spec.python_path),
        "dependencies": list(spec.dependencies),
        "deps_hash": spec.deps_hash,
        "status": status,
        "last_error": error,
        "last_used_at": int(time.time()),
    }
    existing = _read_manifest(spec)
    manifest["created_at"] = existing.get("created_at") or manifest["last_used_at"]
    spec.venv_path.parent.mkdir(parents=True, exist_ok=True)
    _manifest_path(spec).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _base_python() -> str | None:
    return get_app_python_executable() or get_agent_python_executable() or sys.executable


def _create_venv(spec: ExecutionEnvSpec) -> None:
    spec.venv_path.parent.mkdir(parents=True, exist_ok=True)
    base_py = _base_python()
    uv = shutil.which("uv")
    if uv and base_py:
        cmd = [uv, "venv", "--python", base_py, "--seed", str(spec.venv_path)]
    elif base_py:
        cmd = [base_py, "-m", "venv", str(spec.venv_path)]
    else:
        raise RuntimeError("No Python executable available to create execution env")

    logger.info("[runtime_envs] Creating %s env %s with %s", spec.scope, spec.key, cmd[0])
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 600}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "venv creation failed").strip())


def _install_dependencies(spec: ExecutionEnvSpec) -> None:
    if not spec.dependencies:
        return
    cmd = [str(spec.python_path), *get_pip_install_args(list(spec.dependencies))]
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 900}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    logger.info("[runtime_envs] Installing %d deps into %s env %s", len(spec.dependencies), spec.scope, spec.key)
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "dependency install failed").strip())


def ensure_execution_env(spec: ExecutionEnvSpec) -> ExecutionEnvSpec:
    """Create/update an execution env and install declared dependencies."""
    try:
        manifest = _read_manifest(spec)
        old_hash = manifest.get("deps_hash")
        if old_hash and old_hash != spec.deps_hash and spec.venv_path.exists():
            shutil.rmtree(spec.venv_path, ignore_errors=True)
        if not spec.python_path.exists() or not verify_python_executable(str(spec.python_path)):
            _create_venv(spec)
        if manifest.get("deps_hash") != spec.deps_hash:
            _install_dependencies(spec)
        _write_manifest(spec)
        return spec
    except Exception as exc:
        _write_manifest(spec, status="error", error=str(exc))
        raise


def apply_execution_environment(env: dict[str, str], spec: ExecutionEnvSpec) -> dict[str, str]:
    """Return env with a managed execution venv preferred for Python and pip."""
    merged = dict(env)
    pip_index = resolve_pip_index()
    bin_path = str(spec.bin_path)
    merged["OPENAKITA_EXECUTION_ENV_SCOPE"] = spec.scope
    merged["OPENAKITA_EXECUTION_ENV_KEY"] = spec.key
    merged["OPENAKITA_EXECUTION_ENV"] = str(spec.venv_path)
    merged["OPENAKITA_EXECUTION_PYTHON"] = str(spec.python_path)
    merged["OPENAKITA_EXECUTION_DEPS_HASH"] = spec.deps_hash
    # Compatibility: existing prompts/tools look for OPENAKITA_AGENT_PYTHON.
    merged["OPENAKITA_AGENT_PYTHON"] = str(spec.python_path)
    merged["OPENAKITA_AGENT_BIN"] = bin_path
    merged["PATH"] = bin_path + os.pathsep + merged.get("PATH", "")
    merged["PIP_INDEX_URL"] = pip_index["url"]
    merged["UV_INDEX_URL"] = pip_index["url"]
    if pip_index.get("trusted_host"):
        merged["PIP_TRUSTED_HOST"] = pip_index["trusted_host"]
    merged["PYTHONNOUSERSITE"] = "1"
    return merged


def describe_execution_env(spec: ExecutionEnvSpec | None) -> dict:
    if spec is None:
        return {"scope": "shared", "key": "", "python": get_agent_python_executable() or ""}
    return {
        "scope": spec.scope,
        "key": spec.key,
        "venv": str(spec.venv_path),
        "python": str(spec.python_path),
        "deps_hash": spec.deps_hash,
        "dependencies": list(spec.dependencies),
    }
