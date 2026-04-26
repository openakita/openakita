"""Whitelist-only optional dependency manager for ppt-maker."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPTIONAL_DEP_GROUPS: dict[str, dict[str, Any]] = {
    "doc_parsing": {
        "packages": ["python-docx", "pypdf", "beautifulsoup4"],
        "imports": ["docx", "pypdf", "bs4"],
        "description": "PDF/DOCX/web source parsing.",
    },
    "table_processing": {
        "packages": ["openpyxl"],
        "imports": ["openpyxl"],
        "description": "XLSX table parsing and profiling.",
    },
    "chart_rendering": {
        "packages": ["matplotlib"],
        "imports": ["matplotlib"],
        "description": "Optional chart image rendering.",
    },
    "advanced_export": {
        "packages": ["python-pptx"],
        "imports": ["pptx"],
        "description": "Editable PPTX export support.",
    },
    "marp_bridge": {
        "packages": [],
        "imports": [],
        "description": "Detect-only bridge for future Marp/.NET integration.",
        "detect_only": True,
    },
}


@dataclass
class DepJob:
    dep_id: str
    status: str = "idle"
    started_at: float | None = None
    completed_at: float | None = None
    exit_code: int | None = None
    log_tail: list[str] = field(default_factory=list)


class PythonDepsManager:
    """Manage only predefined dependency groups, never arbitrary package names."""

    def __init__(self, data_root: str | Path, *, python_executable: str | None = None) -> None:
        self._data_root = Path(data_root)
        self._python = python_executable or sys.executable
        self._jobs: dict[str, DepJob] = {}

    def list_groups(self) -> list[dict[str, Any]]:
        return [self.status(dep_id) for dep_id in OPTIONAL_DEP_GROUPS]

    def status(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        installed = self.detect(dep_id)
        job = self._jobs.get(dep_id, DepJob(dep_id=dep_id))
        return {
            "id": dep_id,
            "description": group["description"],
            "packages": list(group["packages"]),
            "imports": list(group["imports"]),
            "detect_only": bool(group.get("detect_only")),
            "installed": installed,
            "status": job.status,
            "busy": job.status == "running",
            "exit_code": job.exit_code,
            "log_tail": job.log_tail[-40:],
        }

    def detect(self, dep_id: str) -> bool:
        group = self._group(dep_id)
        if group.get("detect_only"):
            return False
        return all(importlib.util.find_spec(name) is not None for name in group["imports"])

    async def start_install(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        if group.get("detect_only"):
            raise ValueError(f"{dep_id} is detect-only and cannot be installed automatically")
        job = self._jobs.get(dep_id)
        if job and job.status == "running":
            return self.status(dep_id)
        job = DepJob(dep_id=dep_id, status="running", started_at=time.time())
        self._jobs[dep_id] = job
        asyncio.create_task(self._run_install(dep_id, list(group["packages"]), job))
        return self.status(dep_id)

    async def _run_install(self, dep_id: str, packages: list[str], job: DepJob) -> None:
        log_path = self._log_path(dep_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [self._python, "-m", "pip", "install", *packages]
        job.log_tail.append("$ " + " ".join(command))
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            lines: list[str] = []
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                lines.append(line)
                job.log_tail.append(line)
                job.log_tail = job.log_tail[-80:]
            job.exit_code = await proc.wait()
            job.status = "succeeded" if job.exit_code == 0 else "failed"
            log_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.exit_code = -1
            job.log_tail.append(str(exc))
        finally:
            job.completed_at = time.time()

    def _group(self, dep_id: str) -> dict[str, Any]:
        if dep_id not in OPTIONAL_DEP_GROUPS:
            raise ValueError(f"Unknown dependency group: {dep_id}")
        return OPTIONAL_DEP_GROUPS[dep_id]

    def _log_path(self, dep_id: str) -> Path:
        return self._data_root / "logs" / "deps" / f"{dep_id}.log"


def list_optional_groups() -> dict[str, list[str]]:
    return {key: list(value["packages"]) for key, value in OPTIONAL_DEP_GROUPS.items()}

