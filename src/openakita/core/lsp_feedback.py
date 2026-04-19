"""
LSP integration: language-server diagnostics as passive feedback.

Inspired by Claude Code's diagnostic feedback design:
- Automatically collects lint/type-check info for relevant files after tool execution
- Injects diagnostics into tool results so the LLM can see and fix issues
- Supports multiple language-server backends
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Diagnostic:
    """A single diagnostic item."""

    file: str
    line: int
    column: int = 0
    severity: str = "error"  # 'error' | 'warning' | 'info' | 'hint'
    message: str = ""
    source: str = ""  # 'ruff', 'pyright', 'eslint', 'tsc', etc.
    code: str = ""


@dataclass
class DiagnosticReport:
    """Diagnostic report for a set of files."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == "warning")

    def to_feedback_string(self, max_items: int = 20) -> str:
        """Convert to feedback text suitable for injection into tool results."""
        if not self.diagnostics:
            return ""

        lines = [
            f"[Diagnostics: {self.error_count} errors, "
            f"{self.warning_count} warnings in {len(self.files_checked)} files]",
        ]

        for d in self.diagnostics[:max_items]:
            severity_icon = {"error": "E", "warning": "W"}.get(d.severity, "I")
            lines.append(
                f"  {severity_icon} {d.file}:{d.line}:{d.column} [{d.source}/{d.code}] {d.message}"
            )

        if len(self.diagnostics) > max_items:
            lines.append(f"  ... and {len(self.diagnostics) - max_items} more")

        return "\n".join(lines)


class LSPFeedbackCollector:
    """LSP diagnostic collector.

    Collects diagnostics for modified files after tool execution
    and injects them into tool results as passive feedback.
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
        """Collect diagnostics for the specified files."""
        report = DiagnosticReport(files_checked=files)

        for name, backend in self._backends.items():
            try:
                diagnostics = await asyncio.wait_for(
                    backend.check(files),
                    timeout=timeout,
                )
                report.diagnostics.extend(diagnostics)
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("LSP backend '%s' timed out", name)
            except Exception as e:
                logger.debug("LSP backend '%s' error: %s", name, e)

        report.diagnostics.sort(key=lambda d: (0 if d.severity == "error" else 1, d.file, d.line))
        return report


class DiagnosticBackend:
    """Base class for diagnostic backends."""

    async def check(self, files: list[str]) -> list[Diagnostic]:
        raise NotImplementedError


class RuffBackend(DiagnosticBackend):
    """Ruff (Python) lint backend."""

    async def check(self, files: list[str]) -> list[Diagnostic]:
        py_files = [f for f in files if f.endswith(".py")]
        if not py_files:
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff",
                "check",
                "--output-format=json",
                *py_files,
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
    """TypeScript tsc diagnostic backend."""

    async def check(self, files: list[str]) -> list[Diagnostic]:
        ts_files = [f for f in files if f.endswith((".ts", ".tsx"))]
        if not ts_files:
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx",
                "tsc",
                "--noEmit",
                "--pretty",
                "false",
                *ts_files,
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
                        diagnostics.append(
                            Diagnostic(
                                file=file_path,
                                line=int(loc_parts[0]) if loc_parts else 0,
                                column=int(loc_parts[1]) if len(loc_parts) > 1 else 0,
                                severity="error",
                                message=msg,
                                source="tsc",
                            )
                        )
            return diagnostics
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.debug("TSC check failed: %s", e)
            return []
