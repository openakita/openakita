"""Re-export shim — Docker backend moved to ``agent.docker_backend``.

Canonical home: :mod:`openakita.agent.docker_backend`. Shim
preserved at the legacy path until Phase 8, per ADR-0003 and
``docs/revamp/core_audit.md``.

Do not add new code here.
"""

from __future__ import annotations

from openakita.agent.docker_backend import (
    DockerBackend,
    DockerConfig,
    DockerResult,
    configure_docker,
    get_docker_backend,
)

__all__ = [
    "DockerBackend",
    "DockerConfig",
    "DockerResult",
    "configure_docker",
    "get_docker_backend",
]
