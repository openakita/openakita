"""Whitelist-only optional dependency manager for excel-maker."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPTIONAL_GROUPS: dict[str, dict[str, Any]] = {
    "table_core": {
        "packages": ["openpyxl", "pandas"],
        "imports": ["openpyxl", "pandas"],
        "description": "Read/write XLSX workbooks and process tabular data.",
    },
    "legacy_excel": {
        "packages": ["xlrd", "pyxlsb"],
        "imports": ["xlrd", "pyxlsb"],
        "description": "Optional support for old .xls and binary .xlsb workbooks.",
    },
    "charting": {
        "packages": ["matplotlib"],
        "imports": ["matplotlib"],
        "description": "Optional chart image rendering for advanced reports.",
    },
    "template_tools": {
        "packages": [],
        "imports": [],
        "description": "Reserved for future template enhancement tools.",
        "detect_only": True,
    },
}


@dataclass
class InstallJob:
    dep_id: str
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    exit_code: int | None = None
    log: list[str] = field(default_factory=list)


def list_optional_groups() -> dict[str, list[str]]:
    return {key: list(value["packages"]) for key, value in OPTIONAL_GROUPS.items()}


class PythonDepsManager:
    def __init__(self, data_root: str | Path) -> None:
        self._data_root = Path(data_root)
        self._jobs: dict[str, InstallJob] = {}

    def _group(self, dep_id: str) -> dict[str, Any]:
        if dep_id not in OPTIONAL_GROUPS:
            raise ValueError(f"Unknown dependency group: {dep_id}")
        return OPTIONAL_GROUPS[dep_id]

    def list_groups(self) -> list[dict[str, Any]]:
        return [self.status(dep_id) for dep_id in OPTIONAL_GROUPS]

    def status(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        imports = group.get("imports", [])
        missing = [name for name in imports if importlib.util.find_spec(name) is None]
        job = self._jobs.get(dep_id)
        status = "installed" if not missing else "missing"
        if job and job.status == "running":
            status = "installing"
        elif job and job.status == "failed":
            status = "failed"
        elif job and job.status == "succeeded" and missing:
            status = "missing"
        return {
            "id": dep_id,
            "packages": list(group.get("packages", [])),
            "imports": list(imports),
            "description": group.get("description", ""),
            "detect_only": bool(group.get("detect_only")),
            "missing": missing,
            "installed": not missing,
            "status": status,
            "busy": bool(job and job.status == "running"),
            "job": None
            if job is None
            else {
                "status": job.status,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "exit_code": job.exit_code,
                "log_tail": job.log[-40:],
            },
        }

    async def start_install(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        if group.get("detect_only"):
            raise ValueError(f"Dependency group is detect-only: {dep_id}")
        current = self._jobs.get(dep_id)
        if current and current.status == "running":
            return self.status(dep_id)
        job = InstallJob(dep_id=dep_id)
        self._jobs[dep_id] = job
        asyncio.create_task(self._run_install(dep_id, list(group.get("packages", [])), job))
        return self.status(dep_id)

    async def _run_install(self, dep_id: str, packages: list[str], job: InstallJob) -> None:
        if not packages:
            job.status = "succeeded"
            job.exit_code = 0
            job.completed_at = time.time()
            return
        cmd = [sys.executable, "-m", "pip", "install", *packages]
        job.log.append(" ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        async for line in process.stdout:
            job.log.append(line.decode(errors="replace").rstrip())
        job.exit_code = await process.wait()
        job.status = "succeeded" if job.exit_code == 0 else "failed"
        job.completed_at = time.time()

