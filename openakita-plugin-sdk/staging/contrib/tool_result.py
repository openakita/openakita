"""ToolResult — uniform envelope returned by every plugin tool call.

C0.2 from ``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md``:

    早期假设："字段叫 ``runtime_sec``"
    真实情况：refs/CutClaw 与 refs/OpenMontage 都用 ``duration_seconds``
    影响：OpenAkita 必须用 ``duration_seconds`` 否则 reviewer / log 会断流

Why this lives in SDK contrib:

Every plugin's tool handler eventually returns *something* the agent loop
has to inspect — success / failure, stdout, structured payload, how long
it took, and any non-fatal warnings.  CutClaw and OpenMontage both grew
ad-hoc dicts that drifted between agents (one stage used ``runtime_sec``,
the next used ``duration``, the third used ``elapsed`` — reviewer code
had to ``getattr`` three times to find the timing).

This dataclass pins the canonical shape so every tool handler returns
the same fields and every reviewer / log emitter reads the same keys:

* ``ok`` — boolean verdict (mirrors ``ParallelResult.ok`` for symmetry).
* ``output`` — primary payload, free-form (``str`` for shell tools,
  ``dict`` for structured tools, ``None`` when the tool is fire-and-forget).
* ``error`` — when ``ok=False``, the human-readable reason; pair with
  :class:`openakita_plugin_sdk.contrib.RenderedError` for full UX.
* ``duration_seconds`` — wall-clock time the tool took.  Float so
  sub-millisecond calls do not all collapse to 0.  **Never** name this
  ``runtime_sec`` (C0.2).
* ``warnings`` — non-fatal advisories the reviewer can surface without
  failing the stage ("API quota at 87% — consider reducing parallelism").
  Keep entries short, one per line.
* ``metadata`` — plugin-specific extras the canonical schema does not
  cover.  Stays a plain dict so we never need a schema migration when a
  new plugin invents a field.

Example::

    from openakita_plugin_sdk.contrib import ToolResult

    async def handle_render(args):
        t0 = time.monotonic()
        try:
            payload = await do_render(args)
        except RenderTimeout as e:
            return ToolResult.failure(
                error=str(e),
                duration_seconds=time.monotonic() - t0,
            )
        return ToolResult.success(
            output=payload,
            duration_seconds=time.monotonic() - t0,
            warnings=["GPU at 92% utilization"] if gpu_busy else [],
        )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    """Canonical envelope returned by every plugin tool handler.

    Frozen so the agent loop cannot mutate a result mid-pipeline (a real
    OpenMontage bug we're avoiding by construction — a reviewer once
    rewrote ``ok=True`` after the fact, hiding a downstream regression).
    """

    ok: bool
    output: Any = None
    error: str = ""
    duration_seconds: float = 0.0  # NOT runtime_sec — see C0.2 in module doc
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError(
                f"duration_seconds must be >= 0, got {self.duration_seconds}"
            )
        if self.ok and self.error:
            raise ValueError(
                "ToolResult.error must be empty when ok=True — pass the"
                " problem text via warnings or metadata instead."
            )
        if not self.ok and not self.error:
            # Reviewer logs would say "stage failed: " with no reason —
            # fail loudly at construction so the bug surfaces in the
            # plugin's own tests, not in production.
            raise ValueError(
                "ToolResult.error must be non-empty when ok=False"
            )

    @property
    def failed(self) -> bool:
        """Sugar — ``not ok``.  Mirrors ``ParallelResult.failed``."""
        return not self.ok

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view used by reviewer logs and the host UI."""
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolResult:
        return cls(
            ok=bool(data.get("ok", False)),
            output=data.get("output"),
            error=str(data.get("error", "")),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            warnings=[str(w) for w in (data.get("warnings") or [])],
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def success(
        cls,
        *,
        output: Any = None,
        duration_seconds: float = 0.0,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Convenience builder for ``ok=True`` results."""
        return cls(
            ok=True,
            output=output,
            duration_seconds=duration_seconds,
            warnings=list(warnings or []),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(
        cls,
        *,
        error: str,
        output: Any = None,
        duration_seconds: float = 0.0,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Convenience builder for ``ok=False`` results.  ``error`` is
        required — :func:`__post_init__` rejects an empty reason."""
        if not error:
            raise ValueError("ToolResult.failure requires a non-empty error")
        return cls(
            ok=False,
            output=output,
            error=error,
            duration_seconds=duration_seconds,
            warnings=list(warnings or []),
            metadata=dict(metadata or {}),
        )


__all__ = ["ToolResult"]
