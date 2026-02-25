"""
子进程代理 — 通过系统 Python 调用需要可选依赖的操作

当 PyInstaller 打包环境中无法直接 import 某些重量级包（如 playwright、browser_use）时，
通过系统 Python 子进程执行，返回 JSON 结果。

典型流程：
  1. 直接 import → 成功则走进程内路径
  2. ImportError → SubprocessBridge.check_package("playwright")
  3. 如果系统有 → 启动子进程执行
  4. 如果没有 → 返回友好提示
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
    """通过系统 Python 子进程执行需要可选依赖的操作。"""

    def __init__(self) -> None:
        self._python: str | None = None

    def _get_python(self) -> str | None:
        """获取可用的系统 Python 路径（缓存结果）。"""
        if self._python is None:
            py = get_python_executable()
            # 打包环境下 sys.executable 是 openakita-server.exe，不可用
            if py and (not IS_FROZEN or py != sys.executable):
                self._python = py
            else:
                self._python = ""  # 标记为不可用
        return self._python or None

    async def check_package(self, package: str) -> bool:
        """检查系统 Python 中是否安装了指定包。"""
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
        """执行一段 Python 脚本，脚本应将结果以 JSON 格式输出到 stdout。

        Args:
            script: Python 代码片段（会被 dedent 处理）
            timeout: 超时秒数
            env_extra: 额外环境变量

        Returns:
            {"success": True, "data": <parsed JSON>} 或
            {"success": False, "error": <error message>}
        """
        py = self._get_python()
        if not py:
            return {
                "success": False,
                "error": "未找到可用的系统 Python 解释器，无法执行子进程任务",
            }

        import os

        env = os.environ.copy()
        # _ensure_utf8 已在父进程设置了这些环境变量，os.environ.copy() 会继承。
        # 这里保留 setdefault 作为防御，以防本模块在 _ensure_utf8 之前被使用。
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
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                return {"success": False, "error": f"子进程退出码 {proc.returncode}: {err_msg}"}

            # 尝试解析 JSON 输出
            out_text = stdout.decode("utf-8", errors="replace").strip()
            if out_text:
                try:
                    data = json.loads(out_text)
                    return {"success": True, "data": data}
                except json.JSONDecodeError:
                    # 非 JSON 输出也视为成功
                    return {"success": True, "data": out_text}
            return {"success": True, "data": None}

        except TimeoutError:
            return {"success": False, "error": f"子进程执行超时 ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": f"子进程执行异常: {e}"}

    async def run_module_func(
        self,
        module: str,
        func: str,
        *,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """调用系统 Python 中指定模块的函数。

        生成一段 Python 脚本:
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
        """通过系统 Python 启动 Playwright CDP 服务。

        启动一个暴露 CDP 端口的 Chromium 实例，主进程可以通过
        playwright 的 connect_over_cdp 连接，或通过 HTTP 协议直接交互。
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
        # 输出连接信息
        info = {{
            "cdp_url": f"http://127.0.0.1:{port}",
            "ws_endpoint": browser.contexts[0].pages[0].url if browser.contexts else None,
            "pid": browser.process.pid if hasattr(browser, 'process') and browser.process else None,
        }}
        print(json.dumps(info))
        # 保持浏览器运行直到 stdin 关闭
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
                "error": "未找到系统 Python，无法启动 Playwright CDP 服务",
            }

        # 先检查 playwright 是否可用
        has_pw = await self.check_package("playwright")
        if not has_pw:
            return {
                "success": False,
                "error": "系统 Python 中未安装 playwright，请在设置中心安装「浏览器自动化」模块",
            }

        # 以短超时执行启动脚本，等它输出连接信息
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
            # 等待第一行输出（连接信息）
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            info = json.loads(line.decode("utf-8").strip())
            if "error" in info:
                return {"success": False, "error": info["error"]}
            info["process"] = proc
            return {"success": True, "data": info}
        except TimeoutError:
            proc.kill()
            return {"success": False, "error": "Playwright CDP 服务启动超时"}
        except Exception as e:
            proc.kill()
            return {"success": False, "error": f"启动 Playwright CDP 失败: {e}"}


# 全局单例
_bridge: SubprocessBridge | None = None


def get_subprocess_bridge() -> SubprocessBridge:
    """获取全局 SubprocessBridge 单例。"""
    global _bridge
    if _bridge is None:
        _bridge = SubprocessBridge()
    return _bridge
