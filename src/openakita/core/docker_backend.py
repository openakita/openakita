"""
Docker Execution Backend

Executes commands inside Docker containers, providing stronger isolation than subprocess:
- --cap-drop ALL: Drop all Linux capabilities
- --no-new-privileges: Prevent privilege escalation
- --pids-limit: Limit the number of processes
- --tmpfs: Mount a temporary filesystem
- --network none: Optional network-isolated mode
- Workspace mounted as a writable volume

Configuration is controlled via settings:
- docker_backend_enabled: Whether to enable
- docker_image: Docker image to use
- docker_network: Network mode
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DockerConfig:
    enabled: bool = False
    image: str = "python:3.12-slim"
    network: str = "none"
    memory_limit: str = "512m"
    pids_limit: int = 256
    timeout: int = 120
    workspace_mount: str = "/workspace"
    extra_volumes: list[str] = field(default_factory=list)


@dataclass
class DockerResult:
    stdout: str
    stderr: str
    returncode: int
    backend: str = "docker"
    container_id: str = ""


class DockerBackend:
    """Execute commands inside a hardened Docker container."""

    def __init__(self, config: DockerConfig | None = None):
        self._config = config or DockerConfig()
        self._docker_available: bool | None = None

    async def is_available(self) -> bool:
        if self._docker_available is not None:
            return self._docker_available

        if not shutil.which("docker"):
            self._docker_available = False
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            self._docker_available = proc.returncode == 0
        except Exception:
            self._docker_available = False

        return self._docker_available

    def _build_docker_args(
        self,
        command: str,
        cwd: str | None = None,
    ) -> list[str]:
        cfg = self._config

        args = [
            "docker", "run", "--rm",
            "--cap-drop", "ALL",
            "--no-new-privileges",
            "--pids-limit", str(cfg.pids_limit),
            "--memory", cfg.memory_limit,
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        ]

        if cfg.network:
            args.extend(["--network", cfg.network])

        workspace = cwd or str(Path.cwd())
        mount_target = cfg.workspace_mount
        args.extend(["-v", f"{workspace}:{mount_target}:rw"])
        args.extend(["-w", mount_target])

        for vol in cfg.extra_volumes:
            args.extend(["-v", vol])

        args.extend([cfg.image, "sh", "-c", command])

        return args

    async def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> DockerResult:
        if not await self.is_available():
            return DockerResult(
                stdout="",
                stderr="Docker is not available on this system",
                returncode=-1,
                backend="docker_unavailable",
            )

        effective_timeout = timeout or self._config.timeout
        args = self._build_docker_args(command, cwd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            return DockerResult(
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
                returncode=proc.returncode or 0,
            )

        except (asyncio.TimeoutError, TimeoutError):
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            return DockerResult(
                stdout="",
                stderr=f"Docker execution timed out after {effective_timeout}s",
                returncode=-2,
                backend="docker_timeout",
            )
        except Exception as e:
            return DockerResult(
                stdout="",
                stderr=f"Docker execution error: {e}",
                returncode=-3,
                backend="docker_error",
            )


_docker_backend: DockerBackend | None = None


def get_docker_backend() -> DockerBackend:
    global _docker_backend
    if _docker_backend is None:
        _docker_backend = DockerBackend()
    return _docker_backend


def configure_docker(config: DockerConfig) -> DockerBackend:
    global _docker_backend
    _docker_backend = DockerBackend(config)
    return _docker_backend
