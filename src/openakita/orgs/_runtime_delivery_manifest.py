"""Structured node-delivery contracts for organization execution."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_STATES = frozenset({"complete", "in_progress", "blocked", "failed"})
_ARTIFACT_STATUSES = frozenset({"ready", "pending", "failed"})
_ARTIFACT_KINDS = frozenset(
    {"text", "document", "data", "code", "image", "video", "audio", "storyboard", "other"}
)
_MAX_ARTIFACTS = 128
_MAX_VALUES = 256


class DeliveryManifestError(ValueError):
    """Raised when a node submits an invalid delivery manifest."""


def _string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DeliveryManifestError(f"{field_name} must be a string list")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))[:_MAX_VALUES]


@dataclass(frozen=True)
class DeliveryArtifact:
    kind: str
    status: str = "ready"
    name: str = ""
    asset_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    segment_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DeliveryArtifact:
        kind = str(value.get("kind") or "").strip().lower()
        if kind not in _ARTIFACT_KINDS:
            raise DeliveryManifestError(f"artifact kind must be one of {sorted(_ARTIFACT_KINDS)}")
        status = str(value.get("status") or "ready").strip().lower()
        if status not in _ARTIFACT_STATUSES:
            raise DeliveryManifestError(
                f"artifact status must be one of {sorted(_ARTIFACT_STATUSES)}"
            )
        segment_id = str(value.get("segment_id") or "").strip() or None
        return cls(
            kind=kind,
            status=status,
            name=str(value.get("name") or "").strip()[:200],
            asset_ids=_string_list(value.get("asset_ids"), field_name="asset_ids"),
            task_ids=_string_list(value.get("task_ids"), field_name="task_ids"),
            paths=_string_list(value.get("paths"), field_name="paths"),
            segment_id=segment_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": self.status,
            "name": self.name,
            "asset_ids": list(self.asset_ids),
            "task_ids": list(self.task_ids),
            "paths": list(self.paths),
            "segment_id": self.segment_id,
        }


@dataclass(frozen=True)
class DeliveryManifest:
    org_id: str
    command_id: str
    node_id: str
    state: str
    final: bool
    assignment_id: str = ""
    output_slot: str = "default"
    summary: str = ""
    artifacts: tuple[DeliveryArtifact, ...] = ()
    artifact_role: str = "intermediate"
    recorded_at: float = field(default_factory=time.time)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        org_id: str,
        command_id: str,
        node_id: str,
        assignment_id: str = "",
        output_slot: str = "default",
    ) -> DeliveryManifest:
        state = str(value.get("state") or "").strip().lower()
        if state not in _STATES:
            raise DeliveryManifestError(f"state must be one of {sorted(_STATES)}")
        final = value.get("final")
        if not isinstance(final, bool):
            raise DeliveryManifestError("final must be a boolean")
        raw_artifacts = value.get("artifacts", [])
        if not isinstance(raw_artifacts, list):
            raise DeliveryManifestError("artifacts must be a list")
        if len(raw_artifacts) > _MAX_ARTIFACTS:
            raise DeliveryManifestError(f"artifacts must contain at most {_MAX_ARTIFACTS} items")
        artifacts: list[DeliveryArtifact] = []
        for raw in raw_artifacts:
            if not isinstance(raw, Mapping):
                raise DeliveryManifestError("each artifact must be an object")
            artifacts.append(DeliveryArtifact.from_mapping(raw))
        if state == "complete" and any(item.status != "ready" for item in artifacts):
            raise DeliveryManifestError("a complete manifest may only contain ready artifacts")
        from openakita.runtime.execution_context import (
            ArtifactRole,
            ExecutionPhase,
            artifact_role_for_phase,
            current_execution_phase_var,
        )

        phase = current_execution_phase_var.get()
        if phase is ExecutionPhase.PLANNING and final is True:
            raise DeliveryManifestError("a planning activation cannot submit final=true")
        artifact_role = artifact_role_for_phase(phase)
        if state == "complete" and final is True:
            artifact_role = ArtifactRole.FINAL
        return cls(
            org_id=org_id,
            command_id=command_id,
            node_id=node_id,
            state=state,
            final=final,
            assignment_id=assignment_id.strip(),
            output_slot=output_slot.strip() or "default",
            summary=str(value.get("summary") or "").strip()[:2000],
            artifacts=tuple(artifacts),
            artifact_role=artifact_role.value,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "final": self.final,
            "assignment_id": self.assignment_id,
            "output_slot": self.output_slot,
            "summary": self.summary,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "artifact_role": self.artifact_role,
            "recorded_at": self.recorded_at,
        }


class DeliveryManifestLedger:
    """Bounded process-local ledger keyed by command and node."""

    def __init__(self, *, max_commands: int = 128) -> None:
        self._max_commands = max_commands
        self._records: OrderedDict[
            tuple[str, str], dict[tuple[str, str], DeliveryManifest]
        ] = OrderedDict()
        self._lock = threading.Lock()

    def record(self, manifest: DeliveryManifest) -> None:
        key = (manifest.org_id, manifest.command_id)
        with self._lock:
            record_key = (manifest.node_id, manifest.assignment_id)
            self._records.setdefault(key, {})[record_key] = manifest
            self._records.move_to_end(key)
            while len(self._records) > self._max_commands:
                self._records.popitem(last=False)

    def latest(
        self,
        org_id: str,
        command_id: str,
        node_id: str,
        *,
        since: float = 0.0,
        assignment_id: str | None = None,
    ) -> DeliveryManifest | None:
        key = (org_id, command_id)
        with self._lock:
            records = self._records.get(key, {})
            if assignment_id is not None:
                manifest = records.get((node_id, assignment_id))
            else:
                candidates = [
                    item for (candidate_node, _assignment), item in records.items()
                    if candidate_node == node_id
                ]
                manifest = max(candidates, key=lambda item: item.recorded_at, default=None)
            if manifest is None or manifest.recorded_at < since:
                return None
            self._records.move_to_end(key)
            return manifest

    def list_since(
        self,
        org_id: str,
        command_id: str,
        *,
        since: float = 0.0,
        exclude_node_id: str | None = None,
    ) -> tuple[DeliveryManifest, ...]:
        """Return manifests recorded by nodes participating in the current run."""

        key = (org_id, command_id)
        with self._lock:
            records = self._records.get(key)
            if records is None:
                return ()
            self._records.move_to_end(key)
            manifests = (
                manifest
                for (node_id, _assignment), manifest in records.items()
                if node_id != exclude_node_id and manifest.recorded_at >= since
            )
            return tuple(sorted(manifests, key=lambda item: item.recorded_at))

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


delivery_manifest_ledger = DeliveryManifestLedger()


def aggregate_completed_child_manifests(
    *,
    org_id: str,
    command_id: str,
    node_id: str,
    assignment_id: str,
    children: tuple[tuple[str, str], ...],
    output_slot: str = "default",
) -> DeliveryManifest | None:
    """Promote a coordinator after all declared direct assignments finish.

    This is structural aggregation, not domain-specific completion inference:
    every direct child assignment must have an explicit complete manifest. All
    ready child artifacts are preserved, including multiple output slots. A
    coordinator that omitted its own in-progress manifest receives a synthesized
    aggregate; explicit failed/blocked/complete parent states are never replaced.
    """

    parent = delivery_manifest_ledger.latest(
        org_id,
        command_id,
        node_id,
        assignment_id=assignment_id,
    )
    if not children or (parent is not None and parent.state != "in_progress"):
        return None
    child_manifests: list[DeliveryManifest] = []
    for child_node_id, child_assignment_id in children:
        manifest = delivery_manifest_ledger.latest(
            org_id,
            command_id,
            child_node_id,
            assignment_id=child_assignment_id,
        )
        if manifest is None or manifest.state != "complete":
            return None
        child_manifests.append(manifest)

    artifacts: list[DeliveryArtifact] = (
        [artifact for artifact in parent.artifacts if artifact.status == "ready"]
        if parent is not None
        else []
    )
    for manifest in child_manifests:
        artifacts.extend(artifact for artifact in manifest.artifacts if artifact.status == "ready")
    unique: dict[
        tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], str | None],
        DeliveryArtifact,
    ] = {}
    for artifact in artifacts:
        key = (
            artifact.kind,
            artifact.name,
            artifact.asset_ids,
            artifact.task_ids,
            artifact.paths,
            artifact.segment_id,
        )
        unique.setdefault(key, artifact)

    child_summary = "\n".join(
        f"[{manifest.node_id}/{manifest.output_slot}] {manifest.summary}"
        for manifest in child_manifests
        if manifest.summary
    )
    parent_summary = parent.summary if parent is not None else ""
    summary = "\n".join(part for part in (parent_summary, child_summary) if part)[:2000]
    promoted = DeliveryManifest(
        org_id=org_id,
        command_id=command_id,
        node_id=node_id,
        state="complete",
        final=parent.final if parent is not None else False,
        assignment_id=assignment_id,
        output_slot=parent.output_slot if parent is not None else output_slot,
        summary=summary,
        artifacts=tuple(unique.values()),
        artifact_role=(parent.artifact_role if parent is not None else "intermediate"),
    )
    delivery_manifest_ledger.record(promoted)
    return promoted


def validate_manifest_media_delivery(
    manifest: DeliveryManifest | None,
    *,
    artifact_records: tuple[Any, ...],
) -> list[dict[str, Any]]:
    """Validate explicitly declared ready videos against the artifact ledger."""

    if manifest is None or manifest.final is not True:
        return []
    videos = [item for item in manifest.artifacts if item.kind == "video"]
    if not videos:
        return []
    if manifest.state != "complete" or any(item.status != "ready" for item in videos):
        return [
            {
                "code": "media_delivery_not_ready",
                "message": "结构化交付清单中的视频尚未就绪，不能标记为正式交付。",
                "reworkable": True,
            }
        ]

    registered = [record for record in artifact_records if "video" in record.asset_kinds]
    for claimed in videos:
        matches = []
        for record in registered:
            id_match = bool(set(claimed.asset_ids).intersection(record.asset_ids)) or bool(
                set(claimed.task_ids).intersection(record.task_ids)
            )
            path_match = bool(
                {str(Path(path)) for path in claimed.paths}.intersection(
                    str(Path(path)) for path in record.registered_paths
                )
            )
            if id_match or path_match:
                matches.append(record)
        if not matches:
            return [
                {
                    "code": "media_delivery_unregistered",
                    "message": "结构化交付清单声明了视频，但没有匹配的已登记视频资产。",
                    "reworkable": True,
                }
            ]
        if not any(_record_has_valid_file(record) for record in matches):
            return [
                {
                    "code": "media_delivery_validation_missing",
                    "message": "结构化交付清单中的视频缺少有效文件或通过的媒体校验。",
                    "reworkable": True,
                }
            ]
    return []


def validate_manifest_runtime_evidence(
    manifest: DeliveryManifest | None,
    *,
    artifact_records: tuple[Any, ...],
    workspace_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Validate deterministic manifest evidence without judging content quality.

    Schema validity is guaranteed by :meth:`DeliveryManifest.from_mapping`.
    This layer verifies the remaining machine-checkable claims: declared files
    exist and are non-empty, media claims match the command artifact ledger,
    and videos have a validated, registered file. Subjective quality remains a
    parent-review concern.
    """

    if manifest is None or manifest.state != "complete":
        return []
    failures: list[dict[str, Any]] = []
    command_roots: list[Path] = []
    if (workspace_dir or "").strip():
        command_roots.append(
            Path(str(workspace_dir)).expanduser().resolve()
            / manifest.command_id
            / "artifacts"
        )
    try:
        from ._runtime_node_artifacts import _resolve_org_dir, safe_path_segment

        org_dir = _resolve_org_dir(None, manifest.org_id)
        if org_dir is not None:
            command_roots.append(
                org_dir
                / "commands"
                / safe_path_segment(manifest.command_id, fallback="_cmd")
                / "artifacts"
            )
    except Exception:  # noqa: BLE001 -- validation reports unresolved paths below
        pass

    def _resolve_claimed_path(raw: str) -> Path | None:
        path = Path(raw).expanduser()
        candidates = [path] if path.is_absolute() else [root / path for root in command_roots]
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate.resolve()
            except OSError:
                continue
        return None

    for artifact in manifest.artifacts:
        resolved_claimed_paths: set[str] = set()
        for raw in artifact.paths:
            resolved = _resolve_claimed_path(raw)
            if resolved is None:
                failures.append(
                    {
                        "code": "delivery_file_missing",
                        "message": f"结构化交付清单声明的文件不存在或为空：{raw}",
                        "reworkable": True,
                    }
                )
            else:
                resolved_claimed_paths.add(str(resolved))
        if artifact.kind not in {"image", "video", "audio"}:
            continue
        matches = []
        for record in artifact_records:
            kind_match = artifact.kind in record.asset_kinds
            id_match = bool(set(artifact.asset_ids).intersection(record.asset_ids)) or bool(
                set(artifact.task_ids).intersection(record.task_ids)
            )
            record_paths = {
                str(Path(path).resolve())
                for path in (*record.registered_paths, *record.registered_video_paths)
            }
            if kind_match and (id_match or bool(resolved_claimed_paths.intersection(record_paths))):
                matches.append(record)
        if not matches:
            failures.append(
                {
                    "code": "media_delivery_unregistered",
                    "message": f"{artifact.kind} 交付物没有匹配的命令资产账本记录。",
                    "reworkable": True,
                }
            )
            continue
        if artifact.kind == "video" and not any(_record_has_valid_file(record) for record in matches):
            failures.append(
                {
                    "code": "media_delivery_validation_missing",
                    "message": "视频交付物缺少通过规格校验的真实落盘文件。",
                    "reworkable": True,
                }
            )
        elif artifact.kind != "video" and not any(
            _record_has_any_valid_file(record) for record in matches
        ):
            failures.append(
                {
                    "code": "media_delivery_file_missing",
                    "message": f"{artifact.kind} 交付物缺少真实落盘文件。",
                    "reworkable": True,
                }
            )
    return failures


def _record_has_valid_file(record: Any) -> bool:
    if record.media_validation_passed is not True:
        return False
    for raw in record.registered_video_paths:
        path = Path(raw)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def _record_has_any_valid_file(record: Any) -> bool:
    for raw in (*record.registered_paths, *record.registered_video_paths):
        path = Path(raw)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


ORG_SUBMIT_DELIVERABLE_TOOL: dict[str, Any] = {
    "name": "org_submit_deliverable",
    "description": (
        "Record this node's structured delivery state. Call exactly once before the final response. "
        "Use state=complete only when the declared work is actually ready. Root nodes set final=true "
        "only for the final user delivery; child nodes normally use final=false."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["state", "final", "summary", "artifacts"],
        "properties": {
            "state": {"type": "string", "enum": sorted(_STATES)},
            "final": {"type": "boolean"},
            "summary": {"type": "string"},
            "artifacts": {
                "type": "array",
                "maxItems": _MAX_ARTIFACTS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "status"],
                    "properties": {
                        "kind": {"type": "string", "enum": sorted(_ARTIFACT_KINDS)},
                        "status": {"type": "string", "enum": sorted(_ARTIFACT_STATUSES)},
                        "name": {"type": "string"},
                        "asset_ids": {"type": "array", "items": {"type": "string"}},
                        "task_ids": {"type": "array", "items": {"type": "string"}},
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "segment_id": {"type": "string"},
                    },
                },
            },
        },
    },
}


__all__ = [
    "DeliveryArtifact",
    "DeliveryManifest",
    "DeliveryManifestError",
    "DeliveryManifestLedger",
    "ORG_SUBMIT_DELIVERABLE_TOOL",
    "aggregate_completed_child_manifests",
    "delivery_manifest_ledger",
    "validate_manifest_media_delivery",
    "validate_manifest_runtime_evidence",
]
