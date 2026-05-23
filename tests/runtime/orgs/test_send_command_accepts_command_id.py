"""H2 regression: ``send_command`` accepts ``command_id`` kwarg end-to-end.

Pin the contract drift the audit at
``_orgs_business_capability_audit_v1.md`` §3.2 P0 uncovered:
``CommandRuntimeProtocol.send_command`` requires
``*, command_id: str`` but the v2 ``OrgRuntime.send_command`` /
``CommandDispatchManager.send_command`` implementations did not
accept the kwarg. Every call from ``OrgCommandService._run_minimal``
therefore crashed with ``TypeError`` and the user-visible command
silently flipped to ``status=error``.

The fix exposes ``command_id: str | None = None`` on both layers
and threads the supplied id through the tracker instead of
re-minting. ``None`` preserves the legacy submit-or-mint fallback
for callsites (NodeScheduler dispatch, contract tests) that do
not pre-mint an id.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from openakita.orgs._runtime_dispatch import CommandDispatchManager
from openakita.orgs.command_service import CommandRuntimeProtocol
from openakita.orgs.runtime import OrgRuntime, _InMemoryEventBus


class _Org:
    def __init__(self, org_id: str) -> None:
        self.id = org_id
        self.state = "active"
        self.nodes = {"n1": type("N", (), {"role": "eng", "persona": "engineer"})()}


class _Lookup:
    def get_org(self, org_id: str) -> Any:
        return _Org(org_id)


def _make_runtime(**kwargs: Any) -> OrgRuntime:
    return OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
        **kwargs,
    )


def test_org_runtime_send_command_signature_has_command_id_kwarg() -> None:
    """case id: h2.org_runtime.signature_has_command_id"""

    sig = inspect.signature(OrgRuntime.send_command)
    assert "command_id" in sig.parameters
    param = sig.parameters["command_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


def test_dispatch_manager_send_command_signature_has_command_id_kwarg() -> None:
    """case id: h2.dispatch_manager.signature_has_command_id"""

    sig = inspect.signature(CommandDispatchManager.send_command)
    assert "command_id" in sig.parameters
    param = sig.parameters["command_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_org_runtime_send_command_accepts_command_id_no_typeerror() -> None:
    """case id: h2.org_runtime.accepts_kwarg

    Pre-fix: ``TypeError: OrgRuntime.send_command() got an unexpected
    keyword argument 'command_id'``. This is the exact call shape
    ``OrgCommandService._run_minimal`` uses (see
    ``command_service.py:714``).
    """

    rt = _make_runtime()
    out = asyncio.run(rt.send_command("o1", "n1", "do it", command_id="cmd_user_xyz"))
    assert out["status"] == "submitted"
    assert out["command_id"] == "cmd_user_xyz"


def test_org_runtime_send_command_without_kwarg_still_mints_id() -> None:
    """case id: h2.org_runtime.legacy_callers_unchanged

    NodeScheduler / contract suites still call without the kwarg;
    they must continue to receive an auto-minted id.
    """

    rt = _make_runtime()
    out = asyncio.run(rt.send_command("o1", "n1", "do it"))
    assert out["status"] == "submitted"
    assert out["command_id"]
    assert isinstance(out["command_id"], str)


def test_dispatch_manager_send_command_accepts_command_id_kwarg() -> None:
    """case id: h2.dispatch_manager.accepts_kwarg"""

    bus = _InMemoryEventBus()
    d = CommandDispatchManager(
        command_service=None,
        lookup=_Lookup(),
        event_bus=bus,
    )
    out = asyncio.run(d.send_command("o1", "n1", "x", command_id="cmd_user_abc"))
    assert out["command_id"] == "cmd_user_abc"


def test_tracker_records_supplied_command_id() -> None:
    """case id: h2.tracker.uses_supplied_id

    The whole reason the kwarg matters: ``OrgCommandService.submit``
    returns ``command_id`` X to the HTTP caller, who later polls
    ``GET /commands/{X}``. The tracker live-snapshot must therefore
    key on X, not on a fresh dispatch-minted id.
    """

    rt = _make_runtime()
    asyncio.run(rt.send_command("o1", "n1", "x", command_id="cmd_user_aaa"))
    snap = rt.get_command_tracker_snapshot("o1", "cmd_user_aaa")
    assert snap is not None
    assert snap["command_id"] == "cmd_user_aaa"


def test_command_runtime_protocol_runtime_check_isinstance() -> None:
    """case id: h2.protocol.runtime_isinstance

    P9.4 contract: ``OrgRuntime`` satisfies ``CommandRuntimeProtocol``
    at runtime (the Protocol's command_id kwarg already required this).
    Pre-fix the isinstance check was passing only because
    Python's ``@runtime_checkable`` does not enforce kwarg names; the
    drift survived in production.
    """

    rt = _make_runtime()
    assert isinstance(rt, CommandRuntimeProtocol)
