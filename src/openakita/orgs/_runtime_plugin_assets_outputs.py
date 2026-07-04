"""``_runtime_plugin_assets_outputs.py`` -- v2 OrgRuntime file
outputs / react-trace helpers / task-delivery synth.

Companion to :mod:`_runtime_plugin_assets` (split out in
P-RC-10 P10.5a per ADR-0014). Owns :class:`FileOutput` /
:class:`FileOutputRegistry`, the react-trace inspection helpers
(:func:`react_trace_has_tool`, :func:`collect_tool_stats_from_trace`,
:func:`extract_accepted_chain_ids`), :class:`SynthesizedDelivery`
and :class:`TaskDeliverySynthesizer`. :class:`PluginAsset` is
imported lazily under ``TYPE_CHECKING`` (referenced only as a
type hint by :class:`TaskDeliverySynthesizer`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._runtime_plugin_assets import PluginAsset

_LOGGER = logging.getLogger(__name__)


@dataclass
class FileOutput:
    """v1 file-output dict shape (parity)."""

    org_id: str
    node_id: str
    tool_name: str
    path: str
    size_bytes: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    recorded_at: float = field(default_factory=time)


class FileOutputRegistry:
    """v2 file-output registry (replaces v1 ``_register_file_output`` + ``_record_file_output``).

    v1 has two paired methods (register opens a row /
    record finalizes it with size + ctime + digest) at
    156 + 101 = 257 LOC. v2 collapses to one
    register-and-record-in-one-shot ``register`` plus a
    ``record_existing`` for pre-existing files. The
    composition root wires the persistence sink (an
    optional async ``persist`` callable that mirrors v1''s
    sqlite write).
    """

    def __init__(
        self,
        *,
        event_bus: Any,
        persist: Callable[[FileOutput], Awaitable[None]] | None = None,
    ) -> None:
        self._bus = event_bus
        self._persist = persist
        self._by_org: dict[str, list[FileOutput]] = {}

    async def register(
        self,
        *,
        org_id: str,
        node_id: str,
        tool_name: str,
        path: Path,
        metadata: Mapping[str, Any] | None = None,
    ) -> FileOutput | None:
        if not path.exists() or not path.is_file():
            _LOGGER.debug("FileOutputRegistry.register: missing %s", path)
            return None
        out = FileOutput(
            org_id=org_id,
            node_id=node_id,
            tool_name=tool_name,
            path=str(path),
            size_bytes=path.stat().st_size,
            metadata=dict(metadata or {}),
        )
        self._by_org.setdefault(org_id, []).append(out)
        if self._persist is not None:
            try:
                await self._persist(out)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "FileOutputRegistry persist failed (org=%s path=%s)",
                    org_id,
                    path,
                )
        try:
            await self._bus.emit(
                "file_output_registered",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "tool_name": tool_name,
                    "path": out.path,
                    "size_bytes": out.size_bytes,
                },
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("file_output event emit failed")
        return out

    def list_for_org(self, org_id: str) -> list[FileOutput]:
        return list(self._by_org.get(org_id, []))

    def list_for_node(self, org_id: str, node_id: str) -> list[FileOutput]:
        return [f for f in self._by_org.get(org_id, []) if f.node_id == node_id]


# --------------------------------------------------------------------
# React trace helpers (v1 _react_trace_has_tool / _collect_tool_stats_from_trace /
# _extract_accepted_chain_ids)
# --------------------------------------------------------------------


def react_trace_has_tool(trace: Any, tool_name: str) -> bool:
    """v1 ``_react_trace_has_tool`` parity (22 LOC -> ~6 LOC)."""

    if not tool_name or not trace:
        return False
    steps = trace.get("steps") if isinstance(trace, Mapping) else getattr(trace, "steps", None)
    if not steps:
        return False
    return any(
        (s.get("tool") if isinstance(s, Mapping) else getattr(s, "tool", None)) == tool_name
        for s in steps
    )


def collect_tool_stats_from_trace(trace: Any) -> dict[str, int]:
    """v1 ``_collect_tool_stats_from_trace`` parity (31 LOC -> ~10 LOC).

    Returns a ``{tool_name: invocation_count}`` map.
    """

    stats: dict[str, int] = {}
    if not trace:
        return stats
    steps = (
        trace.get("steps") if isinstance(trace, Mapping) else getattr(trace, "steps", None) or ()
    )
    for s in steps or ():
        name = s.get("tool") if isinstance(s, Mapping) else getattr(s, "tool", None)
        if not name:
            continue
        stats[name] = stats.get(name, 0) + 1
    return stats


def extract_accepted_chain_ids(trace: Any) -> list[str]:
    """v1 ``_extract_accepted_chain_ids`` parity (57 LOC -> ~12 LOC).

    Scans trace steps for ``chain_id`` annotations marked
    as accepted (``status == "accepted"`` or
    ``accepted == True``).
    """

    out: list[str] = []
    if not trace:
        return out
    steps = (
        trace.get("steps") if isinstance(trace, Mapping) else getattr(trace, "steps", None) or ()
    )
    for s in steps or ():
        cid = s.get("chain_id") if isinstance(s, Mapping) else getattr(s, "chain_id", None)
        if not cid:
            continue
        status = s.get("status") if isinstance(s, Mapping) else getattr(s, "status", None)
        accepted = s.get("accepted") if isinstance(s, Mapping) else getattr(s, "accepted", None)
        if status == "accepted" or accepted is True:
            if cid not in out:
                out.append(cid)
    return out


# --------------------------------------------------------------------
# TaskDeliverySynthesizer (v1 _synthesize_task_delivered_to_parent)
# --------------------------------------------------------------------


@dataclass
class SynthesizedDelivery:
    """Output of :meth:`TaskDeliverySynthesizer.synthesize`."""

    org_id: str
    parent_node_id: str
    child_node_id: str
    summary: str
    chain_ids: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()


class TaskDeliverySynthesizer:
    """v2 task-delivery synth (replaces v1 ``_synthesize_task_delivered_to_parent``).

    v1 method is 107 LOC of trace-walking + chain-accepting
    + asset-listing + summary-fmt. v2 splits the
    trace-walking out to the helper functions above and
    keeps only the orchestration.
    """

    def __init__(
        self,
        *,
        asset_lister: Callable[[str], list[PluginAsset]] | None = None,
        file_lister: Callable[[str, str], list[FileOutput]] | None = None,
    ) -> None:
        self._asset_lister = asset_lister
        self._file_lister = file_lister

    def synthesize(
        self,
        *,
        org_id: str,
        parent_node_id: str,
        child_node_id: str,
        trace: Any,
        summary_text: str | None = None,
    ) -> SynthesizedDelivery:
        chain_ids = tuple(extract_accepted_chain_ids(trace))
        assets: list[str] = []
        if self._asset_lister is not None:
            try:
                assets.extend(a.path for a in self._asset_lister(org_id))
            except Exception:  # noqa: BLE001
                _LOGGER.exception("asset_lister raised (org=%s)", org_id)
        if self._file_lister is not None:
            try:
                assets.extend(f.path for f in self._file_lister(org_id, child_node_id))
            except Exception:  # noqa: BLE001
                _LOGGER.exception("file_lister raised (org=%s node=%s)", org_id, child_node_id)
        if summary_text is None:
            summary_text = (
                f"Child {child_node_id} delivered task to {parent_node_id}: "
                f"{len(chain_ids)} chain(s), {len(assets)} asset(s)."
            )
        return SynthesizedDelivery(
            org_id=org_id,
            parent_node_id=parent_node_id,
            child_node_id=child_node_id,
            summary=summary_text,
            chain_ids=chain_ids,
            assets=tuple(assets),
        )


__all__ = [
    "FileOutput",
    "FileOutputRegistry",
    "SynthesizedDelivery",
    "TaskDeliverySynthesizer",
    "collect_tool_stats_from_trace",
    "extract_accepted_chain_ids",
    "react_trace_has_tool",
]
