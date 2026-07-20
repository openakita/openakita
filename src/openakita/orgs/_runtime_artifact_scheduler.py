"""Declarative artifact-edge activation for organization commands."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping
from typing import Any

from openakita.runtime.supervisor import (
    DelegationResult,
    ReadyDelegationAction,
)

from ._runtime_artifact_flow import (
    ArtifactRecord,
    CommandArtifactLedger,
    artifact_ledger,
)

_INSTRUCTION_DATA_CAP = 16_000


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _is_artifact_edge(edge: Any) -> bool:
    edge_type = _value(edge, "edge_type", _value(edge, "kind", ""))
    return (getattr(edge_type, "value", None) or str(edge_type)) == "artifact"


class ArtifactActivationLedger:
    """Process-local claim ledger preventing duplicate automatic executions."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str], tuple[int, str]] = {}
        self._lock = threading.Lock()

    def claim(
        self,
        org_id: str,
        command_id: str,
        action_id: str,
        *,
        max_attempts: int,
    ) -> bool:
        key = (org_id, command_id, action_id)
        with self._lock:
            attempts, status = self._states.get(key, (0, ""))
            if status in {"running", "success"} or attempts >= max_attempts:
                return False
            self._states[key] = (attempts + 1, "running")
            return True

    def finish(
        self,
        org_id: str,
        command_id: str,
        action_id: str,
        *,
        success: bool,
    ) -> None:
        key = (org_id, command_id, action_id)
        with self._lock:
            attempts, _ = self._states.get(key, (1, "running"))
            self._states[key] = (attempts, "success" if success else "failed")

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


artifact_activation_ledger = ArtifactActivationLedger()


class ArtifactEdgeScheduler:
    """Produce runnable actions from explicitly activated artifact edges.

    The scheduler understands only graph concepts: source records, target
    records, join keys and fan-out/join modes. Tool and domain semantics stay
    in the organization definition and target node prompt.
    """

    def __init__(
        self,
        *,
        org_id: str,
        command_id: str,
        edges: Iterable[Any],
        ledger: CommandArtifactLedger = artifact_ledger,
        activation_ledger: ArtifactActivationLedger = artifact_activation_ledger,
    ) -> None:
        self.org_id = org_id
        self.command_id = command_id
        self.edges = tuple(edges)
        self.ledger = ledger
        self.activation_ledger = activation_ledger

    def next_action(self) -> ReadyDelegationAction | None:
        records = self.ledger.get(self.org_id, self.command_id)
        if not records:
            return None
        edges = sorted(
            self.edges,
            key=lambda edge: (
                -int(_value(edge, "priority", 0) or 0),
                str(_value(edge, "id", "")),
            ),
        )
        for edge in edges:
            action = self._action_for_edge(edge, records)
            if action is not None:
                return action
        return None

    def record_result(
        self,
        action: ReadyDelegationAction,
        result: DelegationResult,
    ) -> None:
        self.activation_ledger.finish(
            self.org_id,
            self.command_id,
            action.action_id,
            success=result.success,
        )

    def _action_for_edge(
        self,
        edge: Any,
        records: tuple[ArtifactRecord, ...],
    ) -> ReadyDelegationAction | None:
        if not _is_artifact_edge(edge):
            return None
        binding = dict(_value(edge, "binding", {}) or {})
        if binding.get("activation", "manual") != "when_ready":
            return None

        edge_id = str(_value(edge, "id", "") or "")
        source = str(_value(edge, "source", _value(edge, "src", "")) or "")
        target = str(_value(edge, "target", _value(edge, "dst", "")) or "")
        value_field = str(binding.get("value_field") or "")
        accepts = {str(value).lower() for value in binding.get("accepts", [])}
        source_records = [
            record
            for record in records
            if record.source_node_id == source
            and record.values(value_field)
            and (not accepts or not record.asset_kinds or accepts.intersection(record.asset_kinds))
            and record.media_validation_passed is not False
        ]
        if not source_records:
            return None

        mode = str(binding.get("dispatch_mode") or "per_join_key")
        if mode == "per_join_key":
            return self._per_join_key_action(
                edge_id=edge_id,
                target=target,
                binding=binding,
                source_records=source_records,
                all_records=records,
            )
        if mode == "join_all":
            return self._join_all_action(
                edge_id=edge_id,
                target=target,
                binding=binding,
                source_records=source_records,
                all_records=records,
            )
        return None

    def _per_join_key_action(
        self,
        *,
        edge_id: str,
        target: str,
        binding: dict[str, Any],
        source_records: list[ArtifactRecord],
        all_records: tuple[ArtifactRecord, ...],
    ) -> ReadyDelegationAction | None:
        for record in source_records:
            join_value = str(record.segment_id or "").strip()
            if not join_value:
                continue
            if any(
                item.source_node_id == target and item.segment_id == join_value
                for item in all_records
            ):
                continue
            action_id = f"{edge_id}:{join_value}"
            action = self._claim_action(
                action_id=action_id,
                target=target,
                binding=binding,
                source_records=[record],
                scope_items=self._scope_items(binding, all_records, join_value=join_value),
                join_keys=[join_value],
            )
            if action is not None:
                return action
        return None

    def _join_all_action(
        self,
        *,
        edge_id: str,
        target: str,
        binding: dict[str, Any],
        source_records: list[ArtifactRecord],
        all_records: tuple[ArtifactRecord, ...],
    ) -> ReadyDelegationAction | None:
        expected = self._expected_join_keys(binding, all_records)
        if isinstance(binding.get("join_scope"), Mapping) and not expected:
            return None
        available = {str(record.segment_id) for record in source_records if record.segment_id}
        if expected and not expected.issubset(available):
            return None
        min_count = max(1, int(binding.get("min_count", 1) or 1))
        if not expected and len(source_records) < min_count:
            return None

        newest_source = max(record.recorded_at for record in source_records)
        if any(
            record.source_node_id == target and record.recorded_at >= newest_source
            for record in all_records
        ):
            return None
        join_keys = sorted(expected or available)
        action_id = f"{edge_id}:join:{','.join(join_keys) or 'all'}"
        return self._claim_action(
            action_id=action_id,
            target=target,
            binding=binding,
            source_records=source_records,
            scope_items=self._scope_items(binding, all_records),
            join_keys=join_keys,
        )

    def _claim_action(
        self,
        *,
        action_id: str,
        target: str,
        binding: dict[str, Any],
        source_records: list[ArtifactRecord],
        scope_items: list[Any],
        join_keys: list[str],
    ) -> ReadyDelegationAction | None:
        max_attempts = min(max(int(binding.get("max_attempts", 1) or 1), 1), 5)
        if not self.activation_ledger.claim(
            self.org_id,
            self.command_id,
            action_id,
            max_attempts=max_attempts,
        ):
            return None
        payload = {
            "action_id": action_id,
            "join_keys": join_keys,
            "allowed_tools": list(binding.get("target_tools", [])),
            "source_artifacts": [self._record_payload(record) for record in source_records],
            "scope_items": scope_items,
        }
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        if len(encoded) > _INSTRUCTION_DATA_CAP:
            encoded = encoded[:_INSTRUCTION_DATA_CAP] + "..."
        instruction = (
            "[组织运行时自动推进]\n"
            "声明的上游产物依赖已经满足。请直接消费下列真实产物完成本节点工作，"
            "优先调用允许的业务工具；不要重新规划、不要重复生成上游产物、不要再向其他节点派单。\n"
            f"{encoded}"
        )
        return ReadyDelegationAction(
            action_id=action_id,
            speaker=target,
            instruction=instruction,
            metadata={
                "edge_id": action_id.split(":", 1)[0],
                "join_keys": join_keys,
                "automatic": True,
            },
        )

    @staticmethod
    def _record_payload(record: ArtifactRecord) -> dict[str, Any]:
        return {
            "source_node_id": record.source_node_id,
            "tool_name": record.tool_name,
            "segment_id": record.segment_id,
            "asset_ids": list(record.asset_ids),
            "task_ids": list(record.task_ids),
            "segments": list(record.segments),
            "asset_kinds": list(record.asset_kinds),
        }

    @staticmethod
    def _scope_items(
        binding: Mapping[str, Any],
        records: tuple[ArtifactRecord, ...],
        *,
        join_value: str = "",
    ) -> list[Any]:
        scope = binding.get("join_scope")
        if not isinstance(scope, Mapping):
            return []
        source = str(scope.get("source") or "")
        field = str(scope.get("value_field") or "segments")
        key_field = str(scope.get("key_field") or "segment_id")
        items: list[Any] = []
        for record in records:
            if record.source_node_id != source:
                continue
            for item in record.values(field):
                if join_value and isinstance(item, Mapping):
                    if str(item.get(key_field) or "") != join_value:
                        continue
                items.append(item)
        return items

    def _expected_join_keys(
        self,
        binding: Mapping[str, Any],
        records: tuple[ArtifactRecord, ...],
    ) -> set[str]:
        scope = binding.get("join_scope")
        if not isinstance(scope, Mapping):
            return set()
        key_field = str(scope.get("key_field") or "segment_id")
        expected: set[str] = set()
        for item in self._scope_items(binding, records):
            if isinstance(item, Mapping):
                value = str(item.get(key_field) or "").strip()
                if value:
                    expected.add(value)
        return expected


__all__ = [
    "ArtifactActivationLedger",
    "ArtifactEdgeScheduler",
    "artifact_activation_ledger",
]
