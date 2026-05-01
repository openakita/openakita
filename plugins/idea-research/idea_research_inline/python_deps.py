"""Optional Python/runtime dependency manager for idea-research.

This module backs the existing ``/system/python-deps/*`` routes. It keeps the
fire-and-poll shape already used by the settings page, while making packaged
desktop installs use the plugin bootstrap's real Python/pip environment.
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .dep_bootstrap import (
    ensure_runtime_paths,
    module_site_packages,
    pip_env,
    resolve_python_executable,
    subprocess_kwargs,
)

DepKind = Literal["pip", "playwright_browser"]


@dataclass(frozen=True)
class PythonDepSpec:
    id: str
    display_name: str
    packages: tuple[str, ...]
    import_names: tuple[str, ...]
    description: str
    kind: DepKind = "pip"
    install_args: tuple[str, ...] = ()
    can_uninstall: bool = True


@dataclass
class PythonDepState:
    busy: bool = False
    op_kind: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    return_code: int | None = None
    error: str = ""
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=80))


_PYTHON_DEPS: dict[str, PythonDepSpec] = {
    "secure_cookies": PythonDepSpec(
        id="secure_cookies",
        display_name="Cookies 安全存储",
        packages=("cryptography>=42.0.0", "keyring>=25.0.0"),
        import_names=("cryptography.fernet", "keyring"),
        description="用于把 5 平台 cookies 用 Fernet 加密，并把主密钥托管到系统 keyring。",
    ),
    "browser_crawler": PythonDepSpec(
        id="browser_crawler",
        display_name="浏览器爬虫 Python 包",
        packages=("playwright>=1.40.0",),
        import_names=("playwright.async_api",),
        description="用于引擎 B 的抖音、小红书、快手、B 站登录态、微博浏览器采集。",
    ),
    "browser_runtime": PythonDepSpec(
        id="browser_runtime",
        display_name="Playwright Chromium 内核",
        packages=(),
        import_names=(),
        description="Playwright 需要额外下载 Chromium 浏览器内核；只安装 Python 包还不能启动引擎 B。",
        kind="playwright_browser",
        install_args=("install", "chromium"),
        can_uninstall=False,
    ),
}


class PythonDepsManager:
    """Whitelist-only dependency installer used by the settings page."""

    def __init__(self, *, plugin_dir: Path) -> None:
        self._plugin_dir = Path(plugin_dir)
        self._state = {dep_id: PythonDepState() for dep_id in _PYTHON_DEPS}

    def list_components(self) -> list[dict[str, Any]]:
        return [self.detect(dep_id) for dep_id in _PYTHON_DEPS]

    def detect(self, dep_id: str) -> dict[str, Any]:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        imports = [self._import_status(name) for name in spec.import_names]
        missing = [item["name"] for item in imports if not item["found"]]

        runtime_status: dict[str, Any] = {}
        if spec.kind == "playwright_browser":
            runtime_status = self._playwright_chromium_status()
            if not runtime_status.get("found"):
                missing.append("chromium")

        return {
            "id": spec.id,
            "display_name": spec.display_name,
            "description": spec.description,
            "packages": list(spec.packages),
            "imports": [item["name"] for item in imports],
            "import_status": imports,
            "runtime": runtime_status,
            "found": not missing,
            "missing": missing,
            "busy": st.busy,
            "can_uninstall": spec.can_uninstall,
            "target_dir": str(module_site_packages()),
            "python": resolve_python_executable(),
            "manual_command": " ".join(self._install_argv(spec)),
            "last_op": self.status(dep_id),
        }

    def status(self, dep_id: str) -> dict[str, Any]:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        elapsed = 0.0
        if st.started_at:
            end = st.finished_at or time.time()
            elapsed = max(0.0, end - st.started_at)
        return {
            "ok": True,
            "id": spec.id,
            "busy": st.busy,
            "op_kind": st.op_kind,
            "elapsed_sec": round(elapsed, 1),
            "return_code": st.return_code,
            "error": st.error,
            "log_tail": list(st.log_tail),
            "target_dir": str(module_site_packages()),
            "python": resolve_python_executable(),
            "manual_command": " ".join(self._install_argv(spec)),
        }

    async def start_install(self, dep_id: str) -> dict[str, Any]:
        spec = self._require(dep_id)
        return await self._start(dep_id, "install", self._install_argv(spec))

    async def start_uninstall(self, dep_id: str) -> dict[str, Any]:
        spec = self._require(dep_id)
        if not spec.can_uninstall:
            return {
                "ok": False,
                "busy": False,
                "error": "uninstall_not_supported",
                "status": self.status(dep_id),
            }
        argv = [
            resolve_python_executable(),
            "-m",
            "pip",
            "uninstall",
            "-y",
            *[self._package_name(pkg) for pkg in spec.packages],
        ]
        return await self._start(dep_id, "uninstall", argv)

    async def _start(self, dep_id: str, op_kind: str, argv: list[str]) -> dict[str, Any]:
        self._require(dep_id)
        st = self._state[dep_id]
        if st.busy:
            return {"ok": True, "busy": True, "status": self.status(dep_id)}
        st.busy = True
        st.op_kind = op_kind
        st.started_at = time.time()
        st.finished_at = 0.0
        st.return_code = None
        st.error = ""
        st.log_tail.clear()
        st.log_tail.append("$ " + " ".join(argv))
        asyncio.create_task(self._run(dep_id, argv), name=f"idea-research:pydep:{dep_id}:{op_kind}")
        return {"ok": True, "busy": True, "status": self.status(dep_id)}

    async def _run(self, dep_id: str, argv: list[str]) -> None:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._subprocess_env(spec, st.op_kind),
                **subprocess_kwargs(),
            )
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                st.log_tail.append(line.decode("utf-8", errors="replace").rstrip())
            st.return_code = await proc.wait()
            if st.return_code:
                st.error = f"process exited with code {st.return_code}"
            else:
                ensure_runtime_paths(self._plugin_dir)
        except Exception as exc:  # noqa: BLE001
            st.return_code = -1
            st.error = str(exc)
            st.log_tail.append(str(exc))
        finally:
            st.busy = False
            st.finished_at = time.time()

    def _install_argv(self, spec: PythonDepSpec) -> list[str]:
        py = resolve_python_executable()
        if spec.kind == "playwright_browser":
            return [py, "-m", "playwright", *spec.install_args]
        return [
            py,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--prefer-binary",
            "--target",
            str(module_site_packages()),
            *self._pip_index_args(),
            *spec.packages,
        ]

    @staticmethod
    def _subprocess_env(spec: PythonDepSpec, op_kind: str) -> dict[str, str]:
        env = pip_env()
        # ``--target`` installs live outside the interpreter's normal
        # site-packages. Child commands such as ``python -m playwright`` and
        # ``pip uninstall`` need that target on PYTHONPATH, but startup import
        # order remains controlled because the main process still uses
        # sys.path.append via dep_bootstrap.
        if spec.kind == "playwright_browser" or op_kind == "uninstall":
            env["PYTHONPATH"] = str(module_site_packages())
        return env

    @staticmethod
    def _pip_index_args() -> list[str]:
        try:
            from openakita.runtime_env import resolve_pip_index

            index = resolve_pip_index()
            args = ["-i", index["url"]]
            if index.get("trusted_host"):
                args.extend(["--trusted-host", index["trusted_host"]])
            return args
        except Exception:
            return ["-i", "https://mirrors.aliyun.com/pypi/simple/", "--trusted-host", "mirrors.aliyun.com"]

    def _import_status(self, name: str) -> dict[str, Any]:
        ensure_runtime_paths(self._plugin_dir)
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            return {"name": name, "found": False, "error": str(exc), "path": "", "version": ""}
        root_name = name.partition(".")[0]
        root = sys.modules.get(root_name) or module
        version = getattr(root, "__version__", "") or getattr(module, "__version__", "")
        return {
            "name": name,
            "found": True,
            "error": "",
            "path": str(getattr(module, "__file__", "") or getattr(root, "__file__", "") or ""),
            "version": str(version or ""),
        }

    def _playwright_chromium_status(self) -> dict[str, Any]:
        base = {"name": "chromium", "found": False, "path": "", "error": ""}
        if not self._import_status("playwright.sync_api")["found"]:
            base["error"] = "playwright Python package is not importable"
            return base

        script = (
            "import sys\n"
            "from pathlib import Path\n"
            f"sys.path.append({str(module_site_packages())!r})\n"
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            "    path = Path(p.chromium.executable_path)\n"
            "    print(path)\n"
            "    raise SystemExit(0 if path.is_file() else 2)\n"
        )
        try:
            proc = subprocess.run(
                [resolve_python_executable(), "-c", script],
                env=pip_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                **subprocess_kwargs(),
            )
        except Exception as exc:  # noqa: BLE001
            base["error"] = str(exc)
            return base
        out = (proc.stdout or "").strip().splitlines()
        if out:
            base["path"] = out[-1]
        if proc.returncode == 0:
            base["found"] = True
        else:
            base["error"] = (proc.stderr or proc.stdout or "").strip()[-500:]
        return base

    @staticmethod
    def _package_name(spec: str) -> str:
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
            if sep in spec:
                return spec.split(sep, 1)[0]
        return spec

    @staticmethod
    def _require(dep_id: str) -> PythonDepSpec:
        try:
            return _PYTHON_DEPS[dep_id]
        except KeyError as exc:
            raise ValueError(f"unknown python dependency group: {dep_id}") from exc


__all__ = ["PythonDepsManager", "PythonDepSpec"]
