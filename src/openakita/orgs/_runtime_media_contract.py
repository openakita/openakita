"""Command-scoped media contracts applied before external submissions."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ._runtime_delegation import DelegationMediaSpec


class MediaContractError(ValueError):
    """A planned or requested media parameter violates the output contract."""


@dataclass(frozen=True)
class MediaOutputContract:
    kind: str
    output_group: str
    aspect_ratio: str
    resolution: str
    width: int = 0
    height: int = 0


class MediaContractLedger:
    def __init__(self, *, max_commands: int = 128) -> None:
        self._max_commands = max_commands
        self._records: OrderedDict[tuple[str, str], dict[tuple[str, str], MediaOutputContract]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def bind(
        self,
        org_id: str,
        command_id: str,
        proposed: MediaOutputContract,
        *,
        explicit: bool,
    ) -> MediaOutputContract:
        command_key = (org_id, command_id)
        contract_key = (proposed.kind, proposed.output_group)
        with self._lock:
            contracts = self._records.setdefault(command_key, {})
            existing = contracts.get(contract_key)
            if existing is None:
                contracts[contract_key] = proposed
                existing = proposed
            elif explicit and _pixel_identity(existing) != _pixel_identity(proposed):
                raise MediaContractError(
                    f"media contract {proposed.output_group!r} conflicts with its established "
                    f"specification: {_pixel_identity(existing)!r} != "
                    f"{_pixel_identity(proposed)!r}"
                )
            self._records.move_to_end(command_key)
            while len(self._records) > self._max_commands:
                self._records.popitem(last=False)
            return existing

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


def _pixel_identity(contract: MediaOutputContract) -> tuple[str, str, int, int]:
    return (
        contract.aspect_ratio,
        contract.resolution,
        contract.width,
        contract.height,
    )


media_contract_ledger = MediaContractLedger()


def bind_media_contract(
    *,
    org_id: str,
    command_id: str | None,
    tool_input: Mapping[str, Any],
    tool_definition: Mapping[str, Any] | None,
    planned_spec: DelegationMediaSpec | None,
    ledger: MediaContractLedger = media_contract_ledger,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind pixels and normalize model duration before a paid tool call."""

    bound = dict(tool_input)
    metadata = tool_definition.get("x-openakita-media-contract") if tool_definition else None
    if not command_id or not isinstance(metadata, Mapping):
        return bound, []
    kind = str(metadata.get("kind") or "").strip().lower()
    if kind not in {"video", "image", "audio"}:
        return bound, []
    if planned_spec is not None and planned_spec.kind != kind:
        raise MediaContractError(
            f"planned media kind {planned_spec.kind!r} does not match tool kind {kind!r}"
        )

    model_param = str(metadata.get("model_param") or "model_id")
    models = metadata.get("models")
    models = models if isinstance(models, Mapping) else {}
    model_id = str(bound.get(model_param) or metadata.get("default_model") or "").strip()
    model_contract = models.get(model_id)
    if not isinstance(model_contract, Mapping):
        raise MediaContractError(f"model {model_id!r} has no declared media contract")
    bound[model_param] = model_id

    aspect_param = str(metadata.get("aspect_ratio_param") or "aspect_ratio")
    resolution_param = str(metadata.get("resolution_param") or "resolution")
    duration_param = str(metadata.get("duration_param") or "duration")
    allowed_resolutions = tuple(
        str(value).upper() for value in model_contract.get("resolutions", [])
    )
    allowed_aspects = tuple(str(value) for value in model_contract.get("aspects", []))
    planned_resolution = planned_spec.resolution if planned_spec else ""
    planned_aspect = planned_spec.aspect_ratio if planned_spec else ""
    resolution = str(planned_resolution or bound.get(resolution_param) or "").strip().upper()
    aspect_ratio = str(planned_aspect or bound.get(aspect_param) or "16:9").strip()
    if not resolution and allowed_resolutions:
        resolution = allowed_resolutions[0]
    if allowed_resolutions and resolution not in allowed_resolutions:
        raise MediaContractError(
            f"resolution {resolution!r} is not supported by model {model_id!r}; "
            f"allowed: {', '.join(allowed_resolutions)}"
        )
    if allowed_aspects and aspect_ratio not in allowed_aspects:
        raise MediaContractError(
            f"aspect ratio {aspect_ratio!r} is not supported by model {model_id!r}; "
            f"allowed: {', '.join(allowed_aspects)}"
        )

    proposed = MediaOutputContract(
        kind=kind,
        output_group=(planned_spec.output_group if planned_spec else "default"),
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        width=(planned_spec.width if planned_spec else 0),
        height=(planned_spec.height if planned_spec else 0),
    )
    canonical = ledger.bind(
        org_id,
        command_id,
        proposed,
        explicit=planned_spec is not None,
    )
    if allowed_resolutions and canonical.resolution not in allowed_resolutions:
        raise MediaContractError(
            f"established resolution {canonical.resolution!r} is not supported by "
            f"model {model_id!r}"
        )

    changes: list[dict[str, Any]] = []
    for parameter, value in (
        (aspect_param, canonical.aspect_ratio),
        (resolution_param, canonical.resolution),
    ):
        previous = bound.get(parameter)
        if value and previous != value:
            bound[parameter] = value
            changes.append({"parameter": parameter, "from": previous, "to": value})

    duration_range = model_contract.get("duration_range")
    if isinstance(duration_range, (list, tuple)) and len(duration_range) == 2:
        minimum, maximum = int(duration_range[0]), int(duration_range[1])
        planned_duration = planned_spec.duration_s if planned_spec else None
        raw_duration = (
            planned_duration if planned_duration is not None else bound.get(duration_param)
        )
        if raw_duration not in (None, ""):
            try:
                duration = int(raw_duration)
            except (TypeError, ValueError) as exc:
                raise MediaContractError(f"{duration_param} must be an integer") from exc
            if duration > maximum:
                raise MediaContractError(
                    f"duration {duration}s exceeds model {model_id!r} maximum {maximum}s"
                )
            normalized = max(minimum, duration)
            if bound.get(duration_param) != normalized:
                changes.append(
                    {
                        "parameter": duration_param,
                        "from": bound.get(duration_param),
                        "to": normalized,
                        "reason": "model_duration_minimum" if duration < minimum else "planned",
                    }
                )
                bound[duration_param] = normalized
    return bound, changes


__all__ = [
    "MediaContractError",
    "MediaContractLedger",
    "MediaOutputContract",
    "bind_media_contract",
    "media_contract_ledger",
]
