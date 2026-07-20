"""Command-scoped deterministic artifact flow for organization node tools."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

current_artifact_edges_var: ContextVar[tuple[Any, ...]] = ContextVar(
    "org_current_artifact_edges", default=()
)
current_artifact_delivery_dir_var: ContextVar[Path | None] = ContextVar(
    "org_current_artifact_delivery_dir", default=None
)

_MAX_VALUES_PER_RECORD = 256


class ArtifactBindingError(ValueError):
    """A required artifact input could not be resolved deterministically."""

    def __init__(self, reason: str, message: str, *, edge_id: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.edge_id = edge_id


@dataclass(frozen=True)
class ArtifactRecord:
    org_id: str
    command_id: str
    source_node_id: str
    tool_name: str
    asset_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    segments: tuple[Any, ...] = ()
    asset_kinds: tuple[str, ...] = ()
    local_paths: tuple[str, ...] = ()
    registered_paths: tuple[str, ...] = ()
    registered_video_paths: tuple[str, ...] = ()
    media_validation_passed: bool | None = None
    segment_id: str | None = None
    recorded_at: float = field(default_factory=time.time)

    def values(self, field_name: str) -> list[Any]:
        return list(getattr(self, field_name, ()))


class CommandArtifactLedger:
    """Small process-local LRU ledger isolated by organization and command."""

    def __init__(self, *, max_commands: int = 128, max_records: int = 256) -> None:
        self._max_commands = max_commands
        self._max_records = max_records
        self._records: OrderedDict[tuple[str, str], list[ArtifactRecord]] = OrderedDict()
        self._lock = threading.Lock()

    def append(self, record: ArtifactRecord) -> None:
        key = (record.org_id, record.command_id)
        with self._lock:
            records = self._records.setdefault(key, [])
            records.append(record)
            if len(records) > self._max_records:
                del records[: len(records) - self._max_records]
            self._records.move_to_end(key)
            while len(self._records) > self._max_commands:
                self._records.popitem(last=False)

    def get(self, org_id: str, command_id: str) -> tuple[ArtifactRecord, ...]:
        key = (org_id, command_id)
        with self._lock:
            records = self._records.get(key)
            if records is None:
                return ()
            self._records.move_to_end(key)
            return tuple(records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


artifact_ledger = CommandArtifactLedger()


def structured_upstream_records(
    *,
    org_id: str,
    command_id: str,
    source_node_ids: Iterable[str],
    ledger: CommandArtifactLedger = artifact_ledger,
) -> dict[str, Any]:
    """Return bounded, JSON-ready upstream data without filesystem discovery."""

    sources = {str(value).strip() for value in source_node_ids if str(value).strip()}
    records: list[dict[str, Any]] = []
    for record in ledger.get(org_id, command_id):
        if record.source_node_id not in sources:
            continue
        records.append(
            {
                "source_node_id": record.source_node_id,
                "tool_name": record.tool_name,
                "segment_id": record.segment_id,
                "segments": list(record.segments),
                "asset_kinds": list(record.asset_kinds),
                "asset_ids": list(record.asset_ids),
                "task_ids": list(record.task_ids),
                "registered_paths": list(record.registered_paths),
                "media_validation_passed": record.media_validation_passed,
            }
        )
    return {
        "version": 1,
        "org_id": org_id,
        "command_id": command_id,
        "source_node_ids": sorted(sources),
        "records": records,
    }


def _as_mapping(result: Any) -> Mapping[str, Any] | None:
    if isinstance(result, Mapping):
        return result
    if not isinstance(result, str):
        return None
    try:
        decoded = json.loads(result)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _dedupe(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _media_validation_passed(payload: Mapping[str, Any]) -> bool | None:
    validation = payload.get("media_validation") or payload.get("validation")
    if not isinstance(validation, Mapping):
        return None
    passed = validation.get("passed")
    return passed if isinstance(passed, bool) else None


def _local_paths(payload: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("video_path", "output_path", "last_frame_path"):
        values.extend(_strings(payload.get(key)))
    values.extend(_strings(payload.get("local_paths")))
    return _dedupe(values)[:_MAX_VALUES_PER_RECORD]


def _video_paths(payload: Mapping[str, Any]) -> list[str]:
    """Read only paths explicitly typed as video by the tool protocol."""

    values = _strings(payload.get("video_path"))
    values.extend(_strings(payload.get("video_paths")))
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                continue
            if str(artifact.get("kind") or "").strip().lower() != "video":
                continue
            values.extend(_strings(artifact.get("path")))
            values.extend(_strings(artifact.get("paths")))
    return _dedupe(values)[:_MAX_VALUES_PER_RECORD]


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE).strip("._")
    return cleaned[:96] or fallback


def _materialize_paths(
    paths: Iterable[str],
    *,
    delivery_dir: Path | None,
    record_key: str,
) -> tuple[str, ...]:
    """Mirror plugin files into the command delivery tree.

    A hard link keeps large media cheap on the common same-volume layout;
    copying is the portable fallback. Only existing regular files are accepted.
    """

    if delivery_dir is None:
        return ()
    target_dir = delivery_dir / _safe_segment(record_key, "asset")
    registered: list[str] = []
    for raw in paths:
        source = Path(raw)
        try:
            if not source.is_file():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / _safe_segment(source.name, "asset.bin")
            if not target.exists():
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copy2(source, target)
            if target.is_file() and target.stat().st_size == source.stat().st_size:
                registered.append(str(target))
        except OSError:
            continue
    return tuple(_dedupe(registered))


def _infer_asset_kinds(tool_name: str, payload: Mapping[str, Any]) -> tuple[str, ...]:
    declared = _strings(payload.get("asset_kinds") or payload.get("asset_kind"))
    if declared:
        return tuple(dict.fromkeys(kind.lower() for kind in declared))
    lowered = tool_name.lower()
    if any(token in lowered for token in ("txt2img", "image", "img_", "keyframe")):
        return ("image",)
    if any(
        token in lowered
        for token in (
            "video",
            "t2v",
            "concat",
            "photo_speak",
            "relip",
            "reface",
            "pose_drive",
            "avatar_compose",
        )
    ):
        return ("video",)
    return ()


def record_tool_result(
    *,
    org_id: str,
    command_id: str | None,
    source_node_id: str,
    tool_name: str,
    tool_input: Mapping[str, Any],
    result: Any,
    ledger: CommandArtifactLedger = artifact_ledger,
    delivery_dir: Path | None = None,
) -> ArtifactRecord | None:
    """Extract lineage-bearing fields from a successful structured tool result."""
    if not command_id:
        return None
    payload = _as_mapping(result)
    if not payload or payload.get("success") is False or payload.get("ok") is False:
        return None
    if str(payload.get("status") or "").lower() in {"error", "failed", "failure"}:
        return None

    segments_raw = payload.get("segments")
    segments = list(segments_raw[:_MAX_VALUES_PER_RECORD]) if isinstance(segments_raw, list) else []
    asset_ids = _strings(payload.get("asset_ids") or payload.get("asset_id"))
    task_ids = _strings(payload.get("task_ids") or payload.get("task_id"))
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        asset_ids.extend(_strings(segment.get("asset_ids") or segment.get("asset_id")))
        task_ids.extend(_strings(segment.get("task_ids") or segment.get("task_id")))
    if not asset_ids and not task_ids and not segments:
        return None

    segment_id = (
        str(tool_input.get("segment_id") or payload.get("segment_id") or "").strip() or None
    )
    local_paths = _local_paths(payload)
    video_paths = _video_paths(payload)
    record_key = (task_ids or asset_ids or [source_node_id])[0]
    registered_paths = _materialize_paths(
        local_paths,
        delivery_dir=(
            delivery_dir if delivery_dir is not None else current_artifact_delivery_dir_var.get()
        ),
        record_key=str(record_key),
    )
    registered_video_paths = _materialize_paths(
        video_paths,
        delivery_dir=(
            delivery_dir if delivery_dir is not None else current_artifact_delivery_dir_var.get()
        ),
        record_key=str(record_key),
    )
    record = ArtifactRecord(
        org_id=org_id,
        command_id=str(command_id),
        source_node_id=source_node_id,
        tool_name=tool_name,
        asset_ids=tuple(_dedupe(asset_ids)[:_MAX_VALUES_PER_RECORD]),
        task_ids=tuple(_dedupe(task_ids)[:_MAX_VALUES_PER_RECORD]),
        segments=tuple(segments),
        asset_kinds=_infer_asset_kinds(tool_name, payload),
        local_paths=tuple(local_paths),
        registered_paths=registered_paths,
        registered_video_paths=registered_video_paths,
        media_validation_passed=_media_validation_passed(payload),
        segment_id=segment_id,
    )
    ledger.append(record)
    return record


def _edge_value(edge: Any, name: str, default: Any = None) -> Any:
    if isinstance(edge, Mapping):
        return edge.get(name, default)
    return getattr(edge, name, default)


def _is_artifact_edge(edge: Any) -> bool:
    edge_type = _edge_value(edge, "edge_type", _edge_value(edge, "kind", ""))
    return (getattr(edge_type, "value", None) or str(edge_type)) == "artifact"


def bind_tool_input(
    *,
    org_id: str,
    command_id: str | None,
    target_node_id: str,
    tool_name: str,
    tool_input: Mapping[str, Any],
    edges: Iterable[Any] | None = None,
    ledger: CommandArtifactLedger = artifact_ledger,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply incoming artifact-edge bindings to a target tool invocation."""
    bound = dict(tool_input)
    applied: list[dict[str, Any]] = []
    records = ledger.get(org_id, str(command_id or "")) if command_id else ()
    active_edges = tuple(edges) if edges is not None else current_artifact_edges_var.get()

    for edge in active_edges:
        if (
            not _is_artifact_edge(edge)
            or _edge_value(edge, "target", _edge_value(edge, "dst")) != target_node_id
        ):
            continue
        binding = dict(_edge_value(edge, "binding", {}) or {})
        target_tools = binding.get("target_tools", [])
        if tool_name not in target_tools and "*" not in target_tools:
            continue
        edge_id = str(_edge_value(edge, "id", "") or "")
        source = str(_edge_value(edge, "source", _edge_value(edge, "src", "")) or "")
        value_field = str(binding.get("value_field") or "")
        target_param = str(binding.get("target_param") or "")
        required = bool(binding.get("required", False))
        cardinality = str(binding.get("cardinality") or "many")
        join_key = str(binding.get("join_key") or "")
        join_value = str(bound.get(join_key) or "").strip() if join_key else ""
        accepts = {str(kind).lower() for kind in binding.get("accepts", [])}

        candidates = [record for record in records if record.source_node_id == source]
        if accepts:
            candidates = [
                record
                for record in candidates
                if not record.asset_kinds or accepts.intersection(record.asset_kinds)
            ]
        if join_value:
            candidates = [record for record in candidates if record.segment_id == join_value]
        candidates = [record for record in candidates if record.values(value_field)]

        explicit = bound.get(target_param)
        if cardinality == "one":
            if explicit not in (None, "", []):
                continue
            if len(candidates) > 1:
                if not required:
                    continue
                raise ArtifactBindingError(
                    "artifact_binding_ambiguous",
                    f"工具 {tool_name} 的资产输入 {target_param} 匹配到多个上游产物；"
                    f"请提供 {join_key or 'segment_id'} 以唯一定位。",
                    edge_id=edge_id,
                )
            if not candidates:
                if required:
                    suffix = f"（{join_key}={join_value}）" if join_value else ""
                    raise ArtifactBindingError(
                        "artifact_binding_missing",
                        f"工具 {tool_name} 缺少来自节点 {source} 的必需资产{suffix}；"
                        "请先由上游节点生成资产，或显式提供输入参数。",
                        edge_id=edge_id,
                    )
                continue
            values = candidates[0].values(value_field)
        else:
            values = _dedupe(
                value for candidate in candidates for value in candidate.values(value_field)
            )
            if explicit not in (None, "", []):
                existing = explicit if isinstance(explicit, list) else [explicit]
                values = _dedupe([*existing, *values])
            if not values:
                if required:
                    raise ArtifactBindingError(
                        "artifact_binding_missing",
                        f"工具 {tool_name} 缺少来自节点 {source} 的必需资产；"
                        "请先完成上游生成任务。",
                        edge_id=edge_id,
                    )
                continue

        bound[target_param] = values
        applied.append(
            {
                "edge_id": edge_id,
                "source_node_id": source,
                "target_param": target_param,
                "value_field": value_field,
                "value_count": len(values),
            }
        )
    return bound, applied


__all__ = [
    "ArtifactBindingError",
    "ArtifactRecord",
    "CommandArtifactLedger",
    "artifact_ledger",
    "bind_tool_input",
    "current_artifact_delivery_dir_var",
    "current_artifact_edges_var",
    "record_tool_result",
    "structured_upstream_records",
]
