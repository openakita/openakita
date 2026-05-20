"""Parity fixtures for OrgCommandService v2-baseline (P-RC-9 P9.9δ-2a; was P9.4c v1 oracle).

Each :class:`ParityCase` builds an :class:`OrgCommandRequest` (and
related v2 dataclasses) from the v2
``openakita.runtime.orgs.command_models`` shard and asserts the
resulting :class:`ParityResult` equals the captured golden dict
in ``_golden_command_service.json``.

Per P-RC-9-P9.9 δ-2a (audit §6 Option B): this file shipped 10
v1-vs-v2 oracle cases in P9.4c. The v1 import / class loader was
removed in δ-2a; the golden dicts were captured from the v2
output at HEAD ``a3a5fde6`` (close of δ-1).

Ignore set per P-RC-9-PLAN §5.2 (``command_id`` plus volatile
timestamps) is still applied via :func:`_strip_volatile`; the
golden dict already has those keys stripped.

Sentinel discipline (P-RC-9 §7.1): sentinel #4 stays ACTIVE
through G-RC-9.9; semantics shift from oracle-equality to
v2-baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.parity.harness import ParityCase, ParityResult

_GOLDEN: dict[str, dict] = json.loads(
    (Path(__file__).parent / "_golden_command_service.json").read_text(encoding="utf-8")
)


def _v2_classes() -> dict[str, Any]:
    from openakita.runtime.orgs.command_models import (
        ForwardTarget,
        OrgCommandRequest,
        OrgCommandSource,
        OrgCommandSurface,
        OrgOutputScope,
        default_scope_for_surface,
    )

    return {
        "Surface": OrgCommandSurface,
        "Scope": OrgOutputScope,
        "Source": OrgCommandSource,
        "Forward": ForwardTarget,
        "Request": OrgCommandRequest,
        "default_scope": default_scope_for_surface,
    }


_VOLATILE = frozenset({"command_id", "created_at", "updated_at", "finished_at", "delivered_to"})


def _strip_volatile(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k not in _VOLATILE}


def _source_dict(src: Any) -> dict[str, Any]:
    return {
        "channel": src.channel,
        "chat_id": src.chat_id,
        "user_id": src.user_id,
        "thread_id": src.thread_id,
        "client_id": src.client_id,
        "display_name": src.display_name,
    }


def _forward_dict(ft: Any) -> dict[str, Any]:
    return {
        "channel": ft.channel,
        "chat_id": ft.chat_id,
        "thread_id": ft.thread_id,
        "bot_instance_id": ft.bot_instance_id,
        "label": ft.label,
    }


def _request_dict(req: Any) -> dict[str, Any]:
    return {
        "org_id": req.org_id,
        "content": req.content,
        "target_node_id": req.target_node_id,
        "source": _source_dict(req.source),
        "origin_surface": req.origin_surface.value,
        "output_scope": req.output_scope.value,
        "replace_existing": req.replace_existing,
        "continue_previous": req.continue_previous,
        "forward_to": [_forward_dict(ft) for ft in (req.forward_to or [])],
    }


def _submit_record(req: Any, *, root_node_id: str) -> dict[str, Any]:
    return {
        "command_id": "placeholder",  # stripped via _VOLATILE
        "org_id": req.org_id,
        "root_node_id": root_node_id,
        "target_node_id": req.target_node_id,
        "status": "running",
        "phase": "running",
        "result": None,
        "error": None,
        "created_at": 0,
        "updated_at": 0,
        "finished_at": None,
        "origin_surface": req.origin_surface.value,
        "output_scope": req.output_scope.value,
        "source": _source_dict(req.source),
        "delivered_to": [],
        "continue_previous": req.continue_previous,
        "forward_to": [_forward_dict(ft) for ft in (req.forward_to or [])],
    }


def _build_request(case: ParityCase, mod: dict[str, Any]) -> Any:
    inputs = case.inputs
    src = mod["Source"](**inputs.get("source", {}))
    forward = [mod["Forward"](**raw) for raw in inputs.get("forward_to", [])]
    return mod["Request"](
        org_id=inputs["org_id"],
        content=inputs["content"],
        target_node_id=inputs.get("target_node_id"),
        source=src,
        origin_surface=mod["Surface"](inputs["origin_surface"]),
        output_scope=mod["Scope"](inputs["output_scope"]),
        replace_existing=inputs.get("replace_existing", False),
        continue_previous=inputs.get("continue_previous", False),
        forward_to=forward,
    )


def _run_case(case: ParityCase, mod: dict[str, Any]) -> ParityResult:
    op = case.inputs["op"]
    if op == "request_to_dict":
        return ParityResult(
            final_message="to_dict",
            success=True,
            extras={"dict": _request_dict(_build_request(case, mod))},
        )
    if op == "default_scope":
        scope = mod["default_scope"](
            mod["Surface"](case.inputs["surface"]),
            chat_type=case.inputs.get("chat_type"),
        )
        return ParityResult(
            final_message="default_scope",
            success=True,
            extras={"scope": scope.value},
        )
    if op == "forward_target_from_dict":
        ft = mod["Forward"].from_dict(case.inputs["raw"])
        return ParityResult(
            final_message="forward_from_dict",
            success=True,
            extras={"dict": _forward_dict(ft) if ft else None},
        )
    if op == "submit_record":
        req = _build_request(case, mod)
        return ParityResult(
            final_message="submit_record",
            success=True,
            extras={
                "record": _strip_volatile(
                    _submit_record(req, root_node_id=case.inputs["root_node_id"])
                )
            },
        )
    raise KeyError(f"unknown op: {op}")


_BASE_REQUEST = {
    "op": "request_to_dict",
    "org_id": "org_parity_sample",
    "content": "hello",
    "origin_surface": "org_console",
    "output_scope": "console_full",
}


CASES: list[ParityCase] = [
    ParityCase(
        id="command_request_to_dict_minimal",
        kind="command_service",
        inputs={**_BASE_REQUEST, "content": "task A"},
    ),
    ParityCase(
        id="command_request_to_dict_full",
        kind="command_service",
        inputs={
            "op": "request_to_dict",
            "org_id": "org_parity_sample",
            "content": "do the thing",
            "target_node_id": "node_ceo",
            "origin_surface": "im",
            "output_scope": "im_summary",
            "replace_existing": True,
            "continue_previous": True,
            "source": {
                "channel": "feishu",
                "chat_id": "oc_123",
                "user_id": "u_42",
                "thread_id": "th_7",
                "client_id": "client_alpha",
                "display_name": "张三",
            },
            "forward_to": [
                {
                    "channel": "telegram",
                    "chat_id": "-100123",
                    "thread_id": None,
                    "bot_instance_id": "bot_main",
                    "label": "ops",
                },
            ],
        },
    ),
    ParityCase(
        id="command_request_to_dict_desktop_chat",
        kind="command_service",
        inputs={
            "op": "request_to_dict",
            "org_id": "org_parity_sample",
            "content": "人事报表",
            "origin_surface": "desktop_chat",
            "output_scope": "chat_summary",
            "source": {
                "channel": "desktop",
                "chat_id": "",
                "user_id": "desktop_user",
                "thread_id": None,
                "client_id": "",
                "display_name": "",
            },
        },
    ),
    ParityCase(
        id="command_default_scope_console",
        kind="command_service",
        inputs={"op": "default_scope", "surface": "org_console"},
    ),
    ParityCase(
        id="command_default_scope_desktop",
        kind="command_service",
        inputs={"op": "default_scope", "surface": "desktop_chat"},
    ),
    ParityCase(
        id="command_default_scope_im_private",
        kind="command_service",
        inputs={"op": "default_scope", "surface": "im", "chat_type": "private"},
    ),
    ParityCase(
        id="command_default_scope_im_group",
        kind="command_service",
        inputs={"op": "default_scope", "surface": "im", "chat_type": "group"},
    ),
    ParityCase(
        id="command_forward_target_roundtrip",
        kind="command_service",
        inputs={
            "op": "forward_target_from_dict",
            "raw": {
                "channel": "wecom",
                "chat_id": "wxc_id_42",
                "thread_id": "th_1",
                "bot_instance_id": "bot_b",
                "label": "finance",
            },
        },
    ),
    ParityCase(
        id="command_forward_target_rejects_empty",
        kind="command_service",
        inputs={
            "op": "forward_target_from_dict",
            "raw": {"channel": "", "chat_id": ""},
        },
    ),
    ParityCase(
        id="command_submit_record_shape",
        kind="command_service",
        inputs={
            "op": "submit_record",
            "org_id": "org_parity_sample",
            "content": "continue work",
            "target_node_id": None,
            "root_node_id": "node_root",
            "origin_surface": "im",
            "output_scope": "final_only",
            "continue_previous": False,
            "source": {
                "channel": "feishu",
                "chat_id": "oc_test",
                "user_id": "u_1",
                "thread_id": None,
                "client_id": "",
                "display_name": "alice",
            },
            "forward_to": [],
        },
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_command_service_parity(case: ParityCase) -> None:
    """v2-baseline ``OrgCommandService`` dataclass contract (P-RC-9 P9.9δ-2a, 10 cases)."""
    v2 = _run_case(case, _v2_classes())
    expected = _GOLDEN[case.id]
    actual = dict(v2.to_compare())
    actual["tool_sequence"] = [list(t) for t in actual.get("tool_sequence", [])]
    assert actual == expected, f"v2-baseline drift on {case.id}: {actual} != {expected}"
