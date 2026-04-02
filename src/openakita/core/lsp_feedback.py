"""
LSP 集成: 语言服务器诊断作为被动反馈

参考 Claude Code 的诊断反馈设计:
- 工具执行后自动收集相关文件的 lint/type-check 信息
- 将诊断注入工具结果，让 LLM 看到并修正
- 支持多种语言服务器后端
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Diagnostic:
    """一条诊断信息"""

    file: str
    line: int
    column: int = 0
    severity: str = "error"  # 'error' | 'warning' | 'info' | 'hint'
    message: str = ""
    source: str = ""  # 'ruff', 'pyright', 'eslint', 'tsc', etc.
    code: str = ""


@dataclass
class DiagnosticReport:
    """一组文件的诊断报告"""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == "warning")

    def to_feedback_string(self, max_items: int = 20) -> str:
        """转为可注入到工具结果的反馈文本。"""
        if not self.diagnostics:
            return ""

        lines = [
            f"[Diagnostics: {self.error_count} errors, "
            f"{self.warning_count} warnings in {len(self.files_checked)} files]",
        ]

        for d in self.diagnostics[:max_items]:
            severity_icon = {"error": "E", "warning": "W"}.get(d.severity, "I")
            lines.append(
                f"  {severity_icon} {d.file}:{d.line}:{d.column} "
                f"[{d.source}/{d.code}] {d.message}"
            )

        if len(self.diagnostics) > max_items:
            lines.append(f"  ... and {len(self.diagnostics) - max_items} more")

        return "\n".join(lines)


class LSPFeedbackCollector:
    """LSP 诊断收集器。

    在工具执行后收集被修改文件的诊断信息，
    注入到工具结果中作为被动反馈。
    """

    def __init__(self) -> None:
        self._backends: dict[str, DiagnosticBackend] = {}

    def register_backend(self, name: str, backend: DiagnosticBackend) -> None:
        self._backends[name] = backend

    async def collect_diagnostics(
        self,
        files: list[str],
        *,
        timeout: float = 10.0,
    ) -> DiagnosticReport:
        """收集指定文件的诊断信息。"""
        report = DiagnosticReport(files_checked=files)

        for name, backend in self._backends.items():
            try:
                diagnostics = await asyncio.wait_for(
                    backend.check(files),
                    timeout=timeout,
                )
                report.diagnostics.extend(diagnostics)
            except TimeoutError:
                logger.warning("LSP backend '%s' timed out", name)
            except Exception as e:
                logger.debug("LSP backend '%s' error: %s", name, e)

        report.diagnostics.sort(
            key=lambda d: (0 if d.severity == "error" else 1, d.file, d.line)
        )
        return report


class DiagnosticBackend:
    """诊断后端基类"""

    async def check(self, files: list[str]) -> list[Diagnostic]:
        raise NotImplementedError


class RuffBackend(DiagnosticBackend):
    """Ruff (Python) lint 后端"""

    async def check(self, files: list[str]) -> list[Diagnostic]:
        py_files = [f for f in files if f.endswith(".py")]
        if not py_files:
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff", "check", "--output-format=json", *py_files,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if not stdout:
                return []

            items = json.loads(stdout.decode())
            return [
                Diagnostic(
                    file=item.get("filename", ""),
                    line=item.get("location", {}).get("row", 0),
                    column=item.get("location", {}).get("column", 0),
                    severity="warning",
                    message=item.get("message", ""),
                    source="ruff",
                    code=item.get("code", ""),
                )
                for item in items
            ]
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.debug("Ruff check failed: %s", e)
            return []


class TypeScriptBackend(DiagnosticBackend):
    """TypeScript tsc 诊断后端"""

    async def check(self, files: list[str]) -> list[Diagnostic]:
        ts_files = [f for f in files if f.endswith((".ts", ".tsx"))]
        if not ts_files:
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "tsc", "--noEmit", "--pretty", "false", *ts_files,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if not stdout:
                return []

            diagnostics = []
            for line in stdout.decode().splitlines():
                if "): error TS" in line:
                    parts = line.split("(")
                    if len(parts) >= 2:
                        file_path = parts[0]
                        loc_and_msg = parts[1].split("): ")
                        loc_parts = loc_and_msg[0].split(",") if loc_and_msg else ["0", "0"]
                        msg = loc_and_msg[1] if len(loc_and_msg) > 1 else ""
                        diagnostics.append(Diagnostic(
                            file=file_path,
                            line=int(loc_parts[0]) if loc_parts else 0,
                            column=int(loc_parts[1]) if len(loc_parts) > 1 else 0,
                            severity="error",
                            message=msg,
                            source="tsc",
                        ))
            return diagnostics
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.debug("TSC check failed: %s", e)
            return []
