"""
Subprocess bridge — invoke operations requiring optional dependencies via system Python

When certain heavyweight packages (e.g., playwright) cannot be directly imported in a
PyInstaller packaged environment, they are executed via a system Python subprocess,
returning JSON results.

Typical flow:
  1. Direct import → succeeds → in-process path
  2. ImportError → SubprocessBridge.check_package("playwright")
  3. If available on system → launch subprocess to execute
  4. If not available → return a friendly message
"""

import asyncio
import json
import logging
import subprocess
import sys
import textwrap
from typing import Any

from openakita.runtime_env import IS_FROZEN, get_python_executable

logger = logging.getLogger(__name__)

_NO_WINDOW_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


class SubprocessBridge:
    """Execute operations requiring optional dependencies via system Python subprocess."""

    def __init__(self) -> None:
        self._python: str | None = None

    def _get_python(self) -> str | None:
        """Get the available system Python path (cached result)."""
        if self._python is None:
            py = get_python_executable()
            # In packaged environment, sys.executable is openakita-server.exe, not usable
            if py and (not IS_FROZEN or py != sys.executable):
                self._python = py
            else:
                self._python = ""  # Mark as unavailable
        return self._python or None

    async def check_package(self, package: str) -> bool:
        """Check whether the specified package is installed in the system Python."""
        py = self._get_python()
        if not py:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                py,
                "-c",
                f"import {package}; print('ok')",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_NO_WINDOW_FLAGS,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return proc.returncode == 0 and b"ok" in stdout
        except Exception as e:
            logger.debug(f"check_package({package}) failed: {e}")
            return False

    async def run_python_script(
        self,
        script: str,
        *,
        timeout: float = 60,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a Python script; the script should output results as JSON to stdout.

        Args:
            script: Python code snippet (will be dedented)
            timeout: Timeout in seconds
            env_extra: Additional environment variables

        Returns:
            {"success": True, "data": <parsed JSON>} or
            {"success": False, "error": <error message>}
        """
        py = self._get_python()
        if not py:
            return {
                "success": False,
                "error": "No available system Python interpreter found; cannot execute subprocess task",
            }

        import os

        env = os.environ.copy()
        # _ensure_utf8 has already set these env vars in the parent process; os.environ.copy() inherits them.
        # Keep setdefault here as a defensive measure in case this module is used before _ensure_utf8.
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if env_extra:
            env.update(env_extra)

        clean_script = textwrap.dedent(script).strip()

        try:
            proc = await asyncio.create_subprocess_exec(
                py,
                "-c",
                clean_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                **_NO_WINDOW_FLAGS,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                return {"success": False, "error": f"Subprocess exit code {proc.returncode}: {err_msg}"}

            # Try to parse JSON output
            out_text = stdout.decode("utf-8", errors="replace").strip()
            if out_text:
                try:
                    data = json.loads(out_text)
                    return {"success": True, "data": data}
                except json.JSONDecodeError:
                    # Non-JSON output is also treated as success
                    return {"success": True, "data": out_text}
            return {"success": True, "data": None}

        except (asyncio.TimeoutError, TimeoutError):
            return {"success": False, "error": f"Subprocess execution timed out ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": f"Subprocess execution error: {e}"}

    async def run_module_func(
        self,
        module: str,
        func: str,
        *,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """Call a function in the specified module via system Python.

        Generates a Python script:
            import module
            result = module.func(*args, **kwargs)
            print(json.dumps(result))
        """
        args_repr = json.dumps(args or [], ensure_ascii=False)
        kwargs_repr = json.dumps(kwargs or {}, ensure_ascii=False)

        script = f"""
import json
import {module}
_args = json.loads('{args_repr}')
_kwargs = json.loads('{kwargs_repr}')
result = {module}.{func}(*_args, **_kwargs)
print(json.dumps(result, ensure_ascii=False, default=str))
"""
        return await self.run_python_script(script, timeout=timeout)

    async def start_playwright_cdp_server(
        self,
        port: int = 9222,
    ) -> dict[str, Any]:
        """Launch a Playwright CDP server via system Python.

        Starts a Chromium instance exposing a CDP port; the main process can
        connect via playwright's connect_over_cdp or interact directly via HTTP protocol.
        """
        script = f"""
import json, sys, asyncio

async def main():
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--remote-debugging-port={port}"]
        )
        # Output connection info
        info = {{
            "cdp_url": f"http://127.0.0.1:{port}",
            "ws_endpoint": browser.contexts[0].pages[0].url if browser.contexts else None,
            "pid": browser.process.pid if hasattr(browser, 'process') and browser.process else None,
        }}
        print(json.dumps(info))
        # Keep the browser running until stdin is closed
        sys.stdin.read()
    except ImportError:
        print(json.dumps({{"error": "playwright not installed"}}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))
        sys.exit(1)

asyncio.run(main())
"""
        py = self._get_python()
        if not py:
            return {
                "success": False,
                "error": "System Python not found; cannot launch Playwright CDP server",
            }

        # Check if playwright is available first
        has_pw = await self.check_package("playwright")
        if not has_pw:
            return {
                "success": False,
                "error": "playwright is not installed in system Python; please install the 'Browser Automation' module in the setup center",
            }

        # Execute the launch script with a short timeout, waiting for connection info output
        import os

        proc = await asyncio.create_subprocess_exec(
            py,
            "-c",
            textwrap.dedent(script).strip(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
            **_NO_WINDOW_FLAGS,
        )

        try:
            # Wait for the first line of output (connection info)
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            info = json.loads(line.decode("utf-8").strip())
            if "error" in info:
                return {"success": False, "error": info["error"]}
            info["process"] = proc
            return {"success": True, "data": info}
        except (asyncio.TimeoutError, TimeoutError):
            proc.kill()
            return {"success": False, "error": "Playwright CDP server startup timed out"}
        except Exception as e:
            proc.kill()
            return {"success": False, "error": f"Failed to start Playwright CDP: {e}"}


# Global singleton
_bridge: SubprocessBridge | None = None


def get_subprocess_bridge() -> SubprocessBridge:
    """Get the global SubprocessBridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = SubprocessBridge()
    return _bridge
