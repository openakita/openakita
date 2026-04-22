"""QualityGates — pure-function checks shared by SKILL.md protocol *and* pytest CI.

Two-track quality gate (per user decision 2026-04-18):

1. **Markdown protocol** — each plugin's ``SKILL.md`` documents G1–G3 in a
   table so the host agent can self-check before / after each invocation.
2. **pytest CI** — these same functions are imported in
   ``tests/test_plugin_<name>.py`` and assertions enforce the gate.

The functions live here so both tracks share *one* implementation — no
multi-path drift.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class GateStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    @classmethod
    def is_blocking(cls, value: str) -> bool:
        return value == cls.FAIL.value


@dataclass(frozen=True)
class GateResult:
    """Outcome of one gate check.

    ``hint`` should be a 1-sentence remediation pointer for the plugin author
    or the agent (e.g. "Add `prompt` to required fields in plugin.json").
    """

    gate_id: str            # "G1.input_integrity" / "G2.output_schema" / ...
    status: str             # GateStatus value
    message: str            # short human-readable summary
    hint: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == GateStatus.PASS.value

    @property
    def blocking(self) -> bool:
        return GateStatus.is_blocking(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "message": self.message,
            "hint": self.hint,
            "details": dict(self.details),
        }


class QualityGates:
    """Bundle of stateless gate checks.

    All methods are ``@staticmethod`` so they can be used either as instance
    methods (for cleaner imports) or directly:

        from openakita_plugin_sdk.contrib import QualityGates
        result = QualityGates.check_input_integrity({"prompt": "..."}, required=["prompt"])
    """

    # ── G1: input integrity ─────────────────────────────────────────────

    @staticmethod
    def check_input_integrity(
        payload: dict[str, Any],
        *,
        required: Iterable[str] = (),
        non_empty_strings: Iterable[str] = (),
    ) -> GateResult:
        """G1 — required fields present & string fields not whitespace-only."""
        missing = [k for k in required if k not in payload]
        empty = [
            k for k in non_empty_strings
            if isinstance(payload.get(k), str) and not payload.get(k, "").strip()
        ]
        if missing or empty:
            return GateResult(
                gate_id="G1.input_integrity",
                status=GateStatus.FAIL.value,
                message=f"missing={missing}, empty={empty}",
                hint="Add the listed fields before calling the plugin tool.",
                details={"missing": missing, "empty": empty},
            )
        return GateResult(
            gate_id="G1.input_integrity",
            status=GateStatus.PASS.value,
            message="All required input fields present.",
        )

    # ── G2: output schema ───────────────────────────────────────────────

    @staticmethod
    def check_output_schema(
        result: Any,
        *,
        schema: type | None = None,
        required_keys: Iterable[str] = (),
    ) -> GateResult:
        """G2 — output validates against a Pydantic schema or required keys.

        ``schema`` may be a Pydantic ``BaseModel`` subclass (preferred) or a
        plain validator callable.  ``required_keys`` is a fallback when no
        schema is available.
        """
        if schema is not None:
            try:
                if hasattr(schema, "model_validate"):
                    schema.model_validate(result)
                elif hasattr(schema, "parse_obj"):
                    schema.parse_obj(result)
                elif callable(schema):
                    schema(result)
                else:
                    raise TypeError(f"Unsupported schema type: {type(schema)!r}")
            except Exception as e:  # noqa: BLE001
                return GateResult(
                    gate_id="G2.output_schema",
                    status=GateStatus.FAIL.value,
                    message=f"Schema validation failed: {e}",
                    hint="Make sure the plugin returns the documented shape.",
                    details={"error_type": type(e).__name__},
                )
            return GateResult(
                gate_id="G2.output_schema",
                status=GateStatus.PASS.value,
                message="Output matches schema.",
            )

        if not isinstance(result, dict):
            return GateResult(
                gate_id="G2.output_schema",
                status=GateStatus.FAIL.value,
                message=f"Expected dict, got {type(result).__name__}",
                hint="Return a JSON-serializable dict from the tool handler.",
            )
        missing = [k for k in required_keys if k not in result]
        if missing:
            return GateResult(
                gate_id="G2.output_schema",
                status=GateStatus.FAIL.value,
                message=f"missing keys: {missing}",
                hint="Augment the response so all required keys are present.",
                details={"missing": missing},
            )
        return GateResult(
            gate_id="G2.output_schema",
            status=GateStatus.PASS.value,
            message="Required keys present.",
        )

    # ── G3: error readability ───────────────────────────────────────────

    @staticmethod
    def check_error_readability(
        rendered: Any,
        *,
        max_problem_chars: int = 140,
        require_actionable: bool = True,
    ) -> GateResult:
        """G3 — rendered error has cause + problem + actionable next_step.

        ``rendered`` should be a :class:`RenderedError` (or a dict with the
        same keys).  This catches the common foot-gun where a plugin
        surfaces ``str(exc)`` directly to the user.
        """
        d = rendered.to_dict() if hasattr(rendered, "to_dict") else dict(rendered or {})

        problems: list[str] = []
        if not d.get("cause_category"):
            problems.append("missing cause_category")
        if not d.get("problem"):
            problems.append("missing problem")
        if require_actionable and not d.get("next_step"):
            problems.append("missing next_step")
        if (d.get("problem") or "").count("\n") > 4:
            problems.append("problem too long (>4 lines)")
        if len(d.get("problem", "")) > max_problem_chars:
            problems.append(f"problem > {max_problem_chars} chars")
        if d.get("pattern_id") == "_fallback":
            return GateResult(
                gate_id="G3.error_readability",
                status=GateStatus.WARN.value,
                message="Hit fallback pattern — consider adding a more specific ErrorPattern.",
                hint="Register an ErrorPattern via coach.register(...) for this case.",
            )
        if problems:
            return GateResult(
                gate_id="G3.error_readability",
                status=GateStatus.FAIL.value,
                message="; ".join(problems),
                hint="Use ErrorCoach.render() instead of str(exc) when surfacing errors.",
                details={"problems": problems},
            )
        return GateResult(
            gate_id="G3.error_readability",
            status=GateStatus.PASS.value,
            message="Error message is structured and actionable.",
        )

    # ── bundle helpers ──────────────────────────────────────────────────

    @staticmethod
    def aggregate(results: Iterable[GateResult]) -> GateResult:
        """Roll several results into a single FAIL/WARN/PASS verdict."""
        results = list(results)
        if not results:
            return GateResult(
                gate_id="G_aggregate",
                status=GateStatus.PASS.value,
                message="No checks ran.",
            )
        if any(r.blocking for r in results):
            failing = [r.gate_id for r in results if r.blocking]
            return GateResult(
                gate_id="G_aggregate",
                status=GateStatus.FAIL.value,
                message=f"{len(failing)} blocking gate(s) failed: {failing}",
                hint="Fix the failing gates before shipping.",
                details={"failing": failing},
            )
        if any(r.status == GateStatus.WARN.value for r in results):
            return GateResult(
                gate_id="G_aggregate",
                status=GateStatus.WARN.value,
                message=f"{sum(1 for r in results if r.status == GateStatus.WARN.value)} warning(s).",
            )
        return GateResult(
            gate_id="G_aggregate",
            status=GateStatus.PASS.value,
            message=f"All {len(results)} gate(s) passed.",
        )
