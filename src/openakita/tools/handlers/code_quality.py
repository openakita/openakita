"""
Code Quality 处理器

读取 linter 诊断信息：
- read_lints: 调用项目配置的 linter 获取诊断
"""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class CodeQualityHandler:
    TOOLS = ["read_lints"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "read_lints":
            return await self._read_lints(params)
        return f"❌ Unknown code_quality tool: {tool_name}"

    async def _read_lints(self, params: dict) -> str:
        paths = params.get("paths") or []
        cwd = os.getcwd()

        diagnostics: list[str] = []

        has_python = self._detect_python_project(cwd)
        has_js = self._detect_js_project(cwd)

        if has_python:
            py_result = await self._run_ruff(paths, cwd)
            if py_result:
                diagnostics.append(py_result)

        if has_js:
            js_result = await self._run_eslint(paths, cwd)
            if js_result:
                diagnostics.append(js_result)

        if not has_python and not has_js:
            if has_python := bool(shutil.which("ruff")):
                py_result = await self._run_ruff(paths, cwd)
                if py_result:
                    diagnostics.append(py_result)

        if not diagnostics:
            scope = ", ".join(paths) if paths else "workspace"
            return f"No linter errors found in {scope}."

        return "\n\n".join(diagnostics)

    def _detect_python_project(self, cwd: str) -> bool:
        indicators = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", ".flake8"]
        return any((Path(cwd) / f).exists() for f in indicators)

    def _detect_js_project(self, cwd: str) -> bool:
        indicators = ["package.json", ".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml"]
        return any((Path(cwd) / f).exists() for f in indicators)

    async def _run_ruff(self, paths: list[str], cwd: str) -> str | None:
        if not shutil.which("ruff"):
            return None

        target_paths = paths if paths else ["."]
        cmd = ["ruff", "check", "--output-format=json", "--no-fix"] + target_paths

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            if not output:
                return None

            try:
                issues = json.loads(output)
            except json.JSONDecodeError:
                return f"[ruff] {output}" if output else None

            if not issues:
                return None

            lines = [f"[ruff] Found {len(issues)} issue(s):"]
            for issue in issues[:50]:
                loc = issue.get("location", {})
                lines.append(
                    f"  {issue.get('filename', '?')}:{loc.get('row', '?')}:{loc.get('column', '?')}: "
                    f"{issue.get('code', '?')} {issue.get('message', '')}"
                )
            if len(issues) > 50:
                lines.append(f"  ... and {len(issues) - 50} more")
            return "\n".join(lines)

        except TimeoutError:
            return "[ruff] Timed out after 30s"
        except Exception as e:
            logger.warning(f"ruff failed: {e}")
            return None

    async def _run_eslint(self, paths: list[str], cwd: str) -> str | None:
        eslint_cmd = None
        npx = shutil.which("npx")
        if npx:
            eslint_cmd = ["npx", "eslint"]
        elif shutil.which("eslint"):
            eslint_cmd = ["eslint"]
        else:
            return None

        target_paths = paths if paths else ["."]
        cmd = eslint_cmd + ["--format=json", "--no-error-on-unmatched-pattern"] + target_paths

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=60
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            if not output:
                return None

            try:
                results = json.loads(output)
            except json.JSONDecodeError:
                return f"[eslint] {output[:500]}" if output else None

            lines = []
            total = 0
            for file_result in results:
                for msg in file_result.get("messages", []):
                    total += 1
                    if total <= 50:
                        severity = "error" if msg.get("severity", 0) == 2 else "warning"
                        lines.append(
                            f"  {file_result.get('filePath', '?')}:{msg.get('line', '?')}:"
                            f"{msg.get('column', '?')}: [{severity}] "
                            f"{msg.get('ruleId', '?')} {msg.get('message', '')}"
                        )

            if not total:
                return None

            header = f"[eslint] Found {total} issue(s):"
            if total > 50:
                lines.append(f"  ... and {total - 50} more")
            return header + "\n" + "\n".join(lines)

        except TimeoutError:
            return "[eslint] Timed out after 60s"
        except Exception as e:
            logger.warning(f"eslint failed: {e}")
            return None


def create_handler(agent: "Agent"):
    handler = CodeQualityHandler(agent)
    return handler.handle
