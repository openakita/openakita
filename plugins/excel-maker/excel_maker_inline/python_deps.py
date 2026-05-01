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
    status: str = "idle"
    op_kind: str = ""
    started_at: float | None = None
    completed_at: float | None = None
    exit_code: int | None = None
    error: str = ""
    log_tail: list[str] = field(default_factory=list)


def list_optional_groups() -> dict[str, list[str]]:
    return {key: list(value["packages"]) for key, value in OPTIONAL_GROUPS.items()}


class PythonDepsManager:
    def __init__(self, data_root: str | Path, *, python_executable: str | None = None) -> None:
        self._data_root = Path(data_root)
        self._python = python_executable or sys.executable
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
        job = self._jobs.get(dep_id, InstallJob(dep_id=dep_id))
        status = job.status
        if status == "idle":
            status = "installed" if not missing else "missing"
        return {
            "id": dep_id,
            "packages": list(group.get("packages", [])),
            "imports": list(imports),
            "description": group.get("description", ""),
            "detect_only": bool(group.get("detect_only")),
            "missing": missing,
            "installed": not missing,
            "status": status,
            "op_kind": job.op_kind,
            "busy": job.status == "running",
            "exit_code": job.exit_code,
            "error": job.error,
            "elapsed_sec": self._elapsed(job),
            "log_tail": job.log_tail[-40:],
        }

    async def start_install(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        if group.get("detect_only"):
            raise ValueError(f"Dependency group is detect-only: {dep_id}")
        return await self._start(
            dep_id,
            "install",
            [self._python, "-m", "pip", "install", *group.get("packages", [])],
        )

    async def start_uninstall(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        if group.get("detect_only"):
            raise ValueError(f"Dependency group is detect-only: {dep_id}")
        return await self._start(
            dep_id,
            "uninstall",
            [self._python, "-m", "pip", "uninstall", "-y", *group.get("packages", [])],
        )

    async def _start(self, dep_id: str, op_kind: str, command: list[str]) -> dict[str, Any]:
        current = self._jobs.get(dep_id)
        if current and current.status == "running":
            return self.status(dep_id)
        job = InstallJob(dep_id=dep_id, status="running", op_kind=op_kind, started_at=time.time())
        self._jobs[dep_id] = job
        asyncio.create_task(
            self._run_command(dep_id, command, job),
            name=f"excel-maker:pydep:{dep_id}:{op_kind}",
        )
        return self.status(dep_id)

    async def _run_install(self, dep_id: str, packages: list[str], job: InstallJob) -> None:
        await self._run_command(dep_id, [self._python, "-m", "pip", "install", *packages], job)

    async def _run_command(self, dep_id: str, command: list[str], job: InstallJob) -> None:
        log_path = self._data_root / "logs" / "deps" / f"{dep_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        job.log_tail.append("$ " + " ".join(command))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None
            lines: list[str] = []
            async for line in process.stdout:
                text = line.decode(errors="replace").rstrip()
                lines.append(text)
                job.log_tail.append(text)
                job.log_tail = job.log_tail[-80:]
            job.exit_code = await process.wait()
            job.status = "succeeded" if job.exit_code == 0 else "failed"
            if job.exit_code:
                job.error = f"pip exited with code {job.exit_code}"
            log_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.exit_code = -1
            job.error = str(exc)
            job.log_tail.append(job.error)
        finally:
            job.completed_at = time.time()

    @staticmethod
    def _elapsed(job: InstallJob) -> float:
        if job.started_at is None:
            return 0.0
        end = job.completed_at or time.time()
        return round(max(0.0, end - job.started_at), 1)

