"""Whitelist-only optional dependency manager for ppt-maker."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from importlib import metadata
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
    op_kind: str = ""
    started_at: float | None = None
    completed_at: float | None = None
    exit_code: int | None = None
    error: str = ""
    log_tail: list[str] = field(default_factory=list)


class PythonDepsManager:
    """Manage only predefined dependency groups, never arbitrary package names."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        python_executable: str | None = None,
        target_dir: str | Path | None = None,
    ) -> None:
        self._data_root = Path(data_root)
        self._target_dir = Path(target_dir) if target_dir else self._data_root / "python_deps" / "site-packages"
        self._python = python_executable or self._resolve_python_executable()
        self._jobs: dict[str, DepJob] = {}
        self._ensure_target_on_path()

    def list_groups(self) -> list[dict[str, Any]]:
        return [self.status(dep_id) for dep_id in OPTIONAL_DEP_GROUPS]

    def status(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        checks = self._import_checks(group)
        installed = bool(checks) and all(check["found"] for check in checks)
        job = self._jobs.get(dep_id, DepJob(dep_id=dep_id))
        return {
            "id": dep_id,
            "description": group["description"],
            "packages": list(group["packages"]),
            "imports": list(group["imports"]),
            "detect_only": bool(group.get("detect_only")),
            "installed": installed,
            "checks": checks,
            "python_executable": self._python,
            "target_dir": str(self._target_dir),
            "import_paths": self._import_paths(),
            "status": job.status,
            "op_kind": job.op_kind,
            "busy": job.status == "running",
            "exit_code": job.exit_code,
            "error": job.error,
            "elapsed_sec": self._elapsed(job),
            "log_tail": job.log_tail[-40:],
        }

    def detect(self, dep_id: str) -> bool:
        group = self._group(dep_id)
        if group.get("detect_only"):
            return False
        return all(check["found"] for check in self._import_checks(group))

    async def start_install(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        if group.get("detect_only"):
            raise ValueError(f"{dep_id} is detect-only and cannot be installed automatically")
        packages = list(group["packages"])
        return await self._start(dep_id, "install", self._install_command(packages))

    async def start_uninstall(self, dep_id: str) -> dict[str, Any]:
        group = self._group(dep_id)
        if group.get("detect_only"):
            raise ValueError(f"{dep_id} is detect-only and cannot be uninstalled automatically")
        return await self._start(dep_id, "uninstall", ["<internal>", "remove", dep_id])

    async def _start(self, dep_id: str, op_kind: str, command: list[str]) -> dict[str, Any]:
        job = self._jobs.get(dep_id)
        if job and job.status == "running":
            return self.status(dep_id)
        job = DepJob(dep_id=dep_id, status="running", op_kind=op_kind, started_at=time.time())
        self._jobs[dep_id] = job
        asyncio.create_task(
            self._run_command(dep_id, command, job),
            name=f"ppt-maker:pydep:{dep_id}:{op_kind}",
        )
        return self.status(dep_id)

    async def _run_install(self, dep_id: str, packages: list[str], job: DepJob) -> None:
        await self._run_command(dep_id, self._install_command(packages), job)

    async def _run_command(self, dep_id: str, command: list[str], job: DepJob) -> None:
        log_path = self._log_path(dep_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        job.log_tail.append("$ " + " ".join(command))
        if command[:2] == ["<internal>", "remove"]:
            self._remove_group_files(dep_id, job)
            job.completed_at = time.time()
            log_path.write_text("\n".join(job.log_tail), encoding="utf-8")
            return
        try:
            env = self._subprocess_env()
            kwargs: dict[str, Any] = {"env": env}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **kwargs,
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
    def _elapsed(job: DepJob) -> float:
        if job.started_at is None:
            return 0.0
        end = job.completed_at or time.time()
        return round(max(0.0, end - job.started_at), 1)

    def _group(self, dep_id: str) -> dict[str, Any]:
        if dep_id not in OPTIONAL_DEP_GROUPS:
            raise ValueError(f"Unknown dependency group: {dep_id}")
        return OPTIONAL_DEP_GROUPS[dep_id]

    def _log_path(self, dep_id: str) -> Path:
        return self._data_root / "logs" / "deps" / f"{dep_id}.log"

    def _install_command(self, packages: list[str]) -> list[str]:
        return [
            self._python,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--prefer-binary",
            "--target",
            str(self._target_dir),
            *self._pip_index_args(),
            *packages,
        ]

    def _ensure_target_on_path(self) -> None:
        target = str(self._target_dir)
        if target not in sys.path:
            sys.path.append(target)

    def _import_checks(self, group: dict[str, Any]) -> list[dict[str, Any]]:
        self._ensure_target_on_path()
        checks: list[dict[str, Any]] = []
        packages = list(group["packages"])
        imports = list(group["imports"])
        for index, import_name in enumerate(imports):
            package = packages[index] if index < len(packages) else import_name
            check: dict[str, Any] = {
                "import": import_name,
                "package": package,
                "found": False,
                "path": "",
                "version": "",
                "error": "",
            }
            try:
                spec = importlib.util.find_spec(import_name)
                if spec is None:
                    check["error"] = "import not found"
                else:
                    check["found"] = True
                    check["path"] = self._spec_path(spec)
                    check["version"] = self._package_version(package)
            except Exception as exc:  # noqa: BLE001
                check["error"] = str(exc)
            checks.append(check)
        return checks

    @staticmethod
    def _spec_path(spec: Any) -> str:
        origin = getattr(spec, "origin", "") or ""
        if origin and origin != "namespace":
            return str(origin)
        locations = getattr(spec, "submodule_search_locations", None)
        if locations:
            try:
                return str(next(iter(locations)))
            except StopIteration:
                return ""
        return ""

    @staticmethod
    def _package_version(package: str) -> str:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return ""

    def _remove_group_files(self, dep_id: str, job: DepJob) -> None:
        group = self._group(dep_id)
        removed: list[str] = []
        self._target_dir.mkdir(parents=True, exist_ok=True)
        for import_name in group["imports"]:
            for candidate in self._target_candidates(import_name):
                if candidate.exists():
                    self._remove_path(candidate)
                    removed.append(candidate.name)
        for package in group["packages"]:
            normalized = self._normalize_package_name(package)
            for candidate in self._target_dir.glob("*"):
                name = self._normalize_package_name(candidate.name)
                if name.startswith(f"{normalized}-") and (
                    candidate.name.endswith(".dist-info") or candidate.name.endswith(".egg-info")
                ):
                    self._remove_path(candidate)
                    removed.append(candidate.name)
        job.exit_code = 0
        job.status = "succeeded"
        job.log_tail.append(
            "Removed from plugin target: " + (", ".join(sorted(set(removed))) if removed else "nothing")
        )

    def _target_candidates(self, import_name: str) -> list[Path]:
        root = import_name.split(".", 1)[0]
        return [
            self._target_dir / root,
            self._target_dir / f"{root}.py",
        ]

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _normalize_package_name(name: str) -> str:
        return name.lower().replace("_", "-").replace(".", "-")

    @staticmethod
    def _resolve_python_executable() -> str:
        try:
            from openakita.runtime_env import get_python_executable

            resolved = get_python_executable()
            if resolved:
                return resolved
        except Exception:  # noqa: BLE001
            pass
        return sys.executable

    @staticmethod
    def _pip_index_args() -> list[str]:
        try:
            from openakita.runtime_env import resolve_pip_index

            index = resolve_pip_index()
            args = ["-i", index["url"]]
            trusted_host = index.get("trusted_host")
            if trusted_host:
                args.extend(["--trusted-host", trusted_host])
            return args
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _subprocess_env() -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONNOUSERSITE"] = "1"
        env.pop("PYTHONPATH", None)
        return env

    def _import_paths(self) -> list[str]:
        target = str(self._target_dir)
        return [path for path in sys.path if path == target]


def list_optional_groups() -> dict[str, list[str]]:
    return {key: list(value["packages"]) for key, value in OPTIONAL_DEP_GROUPS.items()}

