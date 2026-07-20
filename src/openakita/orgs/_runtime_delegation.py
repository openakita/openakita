"""Structured, command-scoped delegation for organization coordinators."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_MAX_REQUESTS = 16
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class DelegationMediaSpec:
    kind: str
    output_group: str = "default"
    aspect_ratio: str = ""
    resolution: str = ""
    width: int = 0
    height: int = 0
    duration_s: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "output_group": self.output_group,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "width": self.width,
            "height": self.height,
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True)
class DelegationRequest:
    target: str
    instruction: str
    step_id: str = ""
    depends_on: tuple[str, ...] = ()
    segment_id: str = ""
    tool_name: str = ""
    output_slot: str = "default"
    expected_outputs: int = 1
    dispatch_key: str = ""
    reuse_completed: bool = False
    media_spec: DelegationMediaSpec | None = None


class DelegationExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class DelegationExecutionResult:
    """Typed result of one coordinator-to-child execution edge."""

    status: DelegationExecutionStatus
    output: str = ""
    reason_code: str = ""
    reason: str = ""
    delivery_manifest: dict[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is DelegationExecutionStatus.COMPLETED

    @classmethod
    def completed(
        cls,
        output: str,
        *,
        delivery_manifest: dict[str, Any] | None = None,
    ) -> DelegationExecutionResult:
        return cls(
            status=DelegationExecutionStatus.COMPLETED,
            output=output,
            delivery_manifest=delivery_manifest,
        )

    @classmethod
    def failed(
        cls,
        *,
        reason_code: str,
        reason: str = "",
        output: str = "",
        delivery_manifest: dict[str, Any] | None = None,
    ) -> DelegationExecutionResult:
        return cls(
            status=DelegationExecutionStatus.FAILED,
            output=output,
            reason_code=reason_code,
            reason=reason,
            delivery_manifest=delivery_manifest,
        )

    def render_for_parent(self) -> str:
        if self.output.strip():
            return self.output
        detail = self.reason or self.reason_code or self.status.value
        return f"[{self.status.value}: {detail}]"


current_delegation_targets_var: ContextVar[frozenset[str]] = ContextVar(
    "org_current_delegation_targets", default=frozenset()
)
current_delegation_requests_var: ContextVar[list[DelegationRequest] | None] = ContextVar(
    "org_current_delegation_requests", default=None
)
current_delegation_assignment_var: ContextVar[str] = ContextVar(
    "org_current_delegation_assignment", default=""
)
current_delegation_output_slot_var: ContextVar[str] = ContextVar(
    "org_current_delegation_output_slot", default="default"
)
current_delegation_media_spec_var: ContextVar[DelegationMediaSpec | None] = ContextVar(
    "org_current_delegation_media_spec", default=None
)


class DelegationLedger:
    """Bounded idempotency ledger shared by all coordinator activations."""

    def __init__(self, *, max_commands: int = 128) -> None:
        self._max_commands = max_commands
        self._states: OrderedDict[tuple[str, str], dict[str, tuple[int, str]]] = OrderedDict()
        self._lock = threading.Lock()

    def claim(self, org_id: str, command_id: str, key: str) -> bool:
        command_key = (org_id, command_id)
        with self._lock:
            states = self._states.setdefault(command_key, {})
            attempts, status = states.get(key, (0, ""))
            if status in {"running", "success"} or attempts >= _MAX_ATTEMPTS:
                return False
            states[key] = (attempts + 1, "running")
            self._states.move_to_end(command_key)
            while len(self._states) > self._max_commands:
                self._states.popitem(last=False)
            return True

    def finish(self, org_id: str, command_id: str, key: str, *, success: bool) -> None:
        command_key = (org_id, command_id)
        with self._lock:
            states = self._states.setdefault(command_key, {})
            attempts, _ = states.get(key, (1, "running"))
            states[key] = (attempts, "success" if success else "failed")
            self._states.move_to_end(command_key)

    def status(self, org_id: str, command_id: str, key: str) -> str:
        with self._lock:
            return self._states.get((org_id, command_id), {}).get(key, (0, ""))[1]

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


delegation_ledger = DelegationLedger()


def build_delegate_tool(targets: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    target_ids = [node_id for node_id, _label in targets]
    return {
        "name": "org_delegate_task",
        "description": (
            "Delegate one concrete task to a direct report. Use this structured tool instead of "
            "writing XML or describing a delegation in prose. output_slot identifies one intended "
            "result: reuse the same slot for retries, and use distinct slots only when the user "
            "explicitly requested multiple results. To declare a multi-stage plan in one reply, "
            "give every step a unique step_id and list prerequisite step ids in depends_on. "
            "Dependencies are LOCAL to this node activation and must reference an earlier step "
            "declared by this same node. Parent-plan steps are already satisfied inputs and must "
            "not be repeated in depends_on. The "
            "runtime starts a dependent step only after its prerequisites complete and injects "
            "their real outputs. For media work, also provide segment_id, tool_name, and "
            "media_spec. All steps contributing to one final output must share the same "
            "media_spec output_group, aspect ratio, and pixel specification."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["target", "instruction"],
            "properties": {
                "target": {"type": "string", "enum": target_ids},
                "instruction": {"type": "string", "minLength": 1},
                "step_id": {"type": "string", "minLength": 1},
                "depends_on": {
                    "type": "array",
                    "maxItems": _MAX_REQUESTS,
                    "items": {"type": "string", "minLength": 1},
                },
                "segment_id": {"type": "string"},
                "tool_name": {"type": "string"},
                "output_slot": {"type": "string", "minLength": 1},
                "expected_outputs": {"type": "integer", "minimum": 1, "maximum": 16},
                "dispatch_key": {"type": "string"},
                "media_spec": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "output_group"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["image", "video", "audio"]},
                        "output_group": {"type": "string", "minLength": 1},
                        "aspect_ratio": {"type": "string"},
                        "resolution": {"type": "string"},
                        "width": {"type": "integer", "minimum": 1},
                        "height": {"type": "integer", "minimum": 1},
                        "duration_s": {"type": "integer", "minimum": 1},
                    },
                },
            },
        },
    }


def _parse_media_spec(value: Any) -> tuple[DelegationMediaSpec | None, str]:
    if value in (None, ""):
        return None, ""
    if not isinstance(value, dict):
        return None, "media_spec must be an object"
    kind = str(value.get("kind") or "").strip().lower()
    if kind not in {"image", "video", "audio"}:
        return None, "media_spec.kind must be image, video, or audio"
    try:
        width = int(value.get("width") or 0)
        height = int(value.get("height") or 0)
        raw_duration = value.get("duration_s")
        duration_s = int(raw_duration) if raw_duration not in (None, "") else None
    except (TypeError, ValueError):
        return None, "media_spec dimensions and duration_s must be integers"
    if width < 0 or height < 0 or (duration_s is not None and duration_s < 1):
        return None, "media_spec dimensions and duration_s must be positive"
    if bool(width) != bool(height):
        return None, "media_spec width and height must be provided together"
    return (
        DelegationMediaSpec(
            kind=kind,
            output_group=str(value.get("output_group") or "default").strip() or "default",
            aspect_ratio=str(value.get("aspect_ratio") or "").strip(),
            resolution=str(value.get("resolution") or "").strip().upper(),
            width=width,
            height=height,
            duration_s=duration_s,
        ),
        "",
    )


def delegation_key(request: DelegationRequest) -> str:
    assignment = current_delegation_assignment_var.get("").strip()
    if assignment:
        # The assignment is minted by the runtime and remains stable across a
        # bounded rework loop. LLM-generated segment names and prose therefore
        # cannot turn a retry into a second paid generation. Explicit multiple
        # outputs remain possible through distinct output slots.
        identity = (
            f"assignment:{assignment}|slot:{request.output_slot.strip() or 'default'}"
            f"|tool:{request.tool_name.strip()}"
        )
        return f"{request.target}|{identity}"
    explicit = request.dispatch_key.strip()
    if explicit:
        identity = explicit
    elif request.segment_id.strip():
        identity = f"segment:{request.segment_id.strip()}|tool:{request.tool_name.strip()}"
    else:
        normalized = " ".join(request.instruction.split()).casefold()
        identity = "instruction:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"{request.target}|{identity}"


def queue_delegation(
    value: dict[str, Any],
    *,
    org_id: str,
    command_id: str | None,
) -> tuple[DelegationRequest | None, str]:
    if not command_id:
        return None, "delegation requires an active organization command"
    target = str(value.get("target") or "").strip()
    instruction = str(value.get("instruction") or "").strip()
    allowed = current_delegation_targets_var.get()
    sink = current_delegation_requests_var.get()
    if sink is None:
        return None, "delegation is not available in this node activation"
    if target not in allowed:
        return None, f"target {target!r} is not a direct report"
    if not instruction:
        return None, "instruction must not be empty"
    if len(sink) >= _MAX_REQUESTS:
        return None, f"at most {_MAX_REQUESTS} delegations are allowed per activation"
    try:
        expected_outputs = int(value.get("expected_outputs") or 1)
    except (TypeError, ValueError):
        return None, "expected_outputs must be an integer between 1 and 16"
    if not 1 <= expected_outputs <= 16:
        return None, "expected_outputs must be between 1 and 16"
    output_slot = str(value.get("output_slot") or "default").strip() or "default"
    if expected_outputs > 1 and output_slot == "default":
        return None, "multiple outputs require a distinct output_slot for each delegation"
    raw_dependencies = value.get("depends_on") or []
    if not isinstance(raw_dependencies, list) or not all(
        isinstance(item, str) for item in raw_dependencies
    ):
        return None, "depends_on must be a list of step_id strings"
    depends_on = tuple(dict.fromkeys(item.strip() for item in raw_dependencies if item.strip()))
    media_spec, media_error = _parse_media_spec(value.get("media_spec"))
    if media_error:
        return None, media_error
    step_id = str(value.get("step_id") or "").strip()
    if not step_id:
        step_id = f"{target}:{output_slot}:{str(value.get('tool_name') or 'task').strip()}"
    declared_steps = {request.step_id for request in sink}
    unknown_dependencies = set(depends_on) - declared_steps
    if unknown_dependencies:
        unknown = ", ".join(sorted(unknown_dependencies))
        return None, (
            f"step {step_id!r} depends on unknown local steps: {unknown}. "
            "depends_on may only reference earlier steps declared by this node activation; "
            "do not reference parent-plan step ids"
        )
    request = DelegationRequest(
        target=target,
        instruction=instruction,
        step_id=step_id,
        depends_on=depends_on,
        segment_id=str(value.get("segment_id") or "").strip(),
        tool_name=str(value.get("tool_name") or "").strip(),
        output_slot=output_slot,
        expected_outputs=expected_outputs,
        dispatch_key=str(value.get("dispatch_key") or "").strip(),
        media_spec=media_spec,
    )
    key = delegation_key(request)
    same_step = next((item for item in sink if item.step_id == step_id), None)
    if same_step is not None and delegation_key(same_step) != key:
        return None, f"step_id {step_id!r} is already declared in this local plan"
    if not delegation_ledger.claim(org_id, command_id, key):
        if delegation_ledger.status(org_id, command_id, key) == "success":
            reused = DelegationRequest(
                target=request.target,
                instruction=request.instruction,
                step_id=request.step_id,
                depends_on=request.depends_on,
                segment_id=request.segment_id,
                tool_name=request.tool_name,
                output_slot=request.output_slot,
                expected_outputs=request.expected_outputs,
                dispatch_key=request.dispatch_key,
                reuse_completed=True,
                media_spec=request.media_spec,
            )
            sink.append(reused)
            return reused, f"reuse completed delegation: {key}"
        return None, f"duplicate delegation suppressed: {key}"
    sink.append(request)
    return request, key


__all__ = [
    "DelegationExecutionResult",
    "DelegationExecutionStatus",
    "DelegationLedger",
    "DelegationMediaSpec",
    "DelegationRequest",
    "build_delegate_tool",
    "current_delegation_requests_var",
    "current_delegation_assignment_var",
    "current_delegation_output_slot_var",
    "current_delegation_media_spec_var",
    "current_delegation_targets_var",
    "delegation_key",
    "delegation_ledger",
    "queue_delegation",
]
