"""Sprint-5 duck-call cleanup: 3 ``OrgRuntime`` node-query stubs.

Audit v5 §5.2 #5: ``GET /api/v2/orgs/{id}/nodes/{nid}/thinking``,
``.../prompt-preview`` and ``.../status`` returned ``503 subsystem
_not_wired`` (then ``AttributeError`` after the duck-call shim was
relaxed) because ``OrgRuntime`` had no implementations. The v10
``_call_runtime_method`` helper now also short-circuits to a safe
default when the method is missing, but the cleaner fix is to ship
real stubs that return structured payloads -- which is what this
sprint does. The real implementations will land alongside the
``NodeStatusController`` subsystem (tracked as P9.7gamma in the
runtime roadmap).

These cases pin the structured-payload contract so the frontend panel
can render an empty / informational view in v17 instead of crashing.
"""

from __future__ import annotations

from typing import Any

from openakita.orgs.runtime import OrgRuntime


class _Org:
    def __init__(self, org_id: str) -> None:
        self.id = org_id
        self.state = "active"
        self.workspace_dir = None
        self.nodes = {
            "n1": type("N", (), {"role": "eng", "persona": "engineer"})()
        }


class _Lookup:
    def __init__(self, *, present: bool = True) -> None:
        self._present = present

    def get_org(self, org_id: str) -> Any:
        return _Org(org_id) if self._present else None


class _CmdService:
    async def submit(self, **_kwargs: Any) -> dict[str, Any]:
        return {"command_id": "cmd_1", "status": "submitted"}

    async def cancel(self, *_args: Any) -> None:
        return None


class _StubEventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Any]] = {}

    def subscribe(self, event: str, handler: Any) -> None:
        self._subs.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Any) -> None:
        if handler in self._subs.get(event, ()):
            self._subs[event].remove(handler)

    async def emit(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _make_runtime() -> OrgRuntime:
    return OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
        command_service=_CmdService(),
        event_bus=_StubEventBus(),
    )


def test_get_node_thinking_returns_structured_payload() -> None:
    """case id: p05.duck.thinking.shape

    Pre-fix the call raised ``AttributeError``. We now return a dict
    with ``org_id``, ``node_id``, ``thinking`` (list, possibly empty)
    + an ``implementation`` marker so the v17 frontend can disable the
    panel for the stub case without 503-ing.
    """

    rt = _make_runtime()
    out = rt.get_node_thinking("o1", "n1")
    assert isinstance(out, dict)
    assert out["org_id"] == "o1"
    assert out["node_id"] == "n1"
    assert isinstance(out.get("thinking"), list)


def test_preview_node_prompt_returns_structured_payload() -> None:
    """case id: p05.duck.prompt_preview.shape

    ``prompt`` may be ``None`` when the spec is missing -- that's
    fine: the panel renders the n/a state. ``prompt`` *must not*
    be absent (the v17 frontend keys off the field's presence).
    """

    rt = _make_runtime()
    out = rt.preview_node_prompt("o1", "n1")
    assert isinstance(out, dict)
    assert out["org_id"] == "o1"
    assert out["node_id"] == "n1"
    assert "prompt" in out  # may be None; key must exist


def test_get_node_status_snapshot_returns_structured_payload() -> None:
    """case id: p05.duck.status_snapshot.shape"""

    rt = _make_runtime()
    out = rt.get_node_status_snapshot("o1", "n1")
    assert isinstance(out, dict)
    assert out["org_id"] == "o1"
    assert out["node_id"] == "n1"
    assert out["status"] in {"active", "idle"}
    assert isinstance(out["is_active"], bool)
    assert isinstance(out["recently_stopped"], bool)


def test_stubs_safe_when_org_missing() -> None:
    """case id: p05.duck.org_missing_no_raise

    A missing org id must not raise -- the routes ahead already
    return a 404 envelope; the stubs are best-effort wrappers and
    must never reintroduce the AttributeError v5 audit flagged.
    """

    rt = OrgRuntime(
        lookup=_Lookup(present=False),
        persistence=object(),
        lifecycle_emitter=object(),
        command_service=_CmdService(),
        event_bus=_StubEventBus(),
    )
    assert isinstance(rt.get_node_thinking("nope", "n1"), dict)
    assert isinstance(rt.preview_node_prompt("nope", "n1"), dict)
    assert isinstance(rt.get_node_status_snapshot("nope", "n1"), dict)


def test_set_on_stop_org_passthrough_does_not_raise() -> None:
    """case id: p05.duck.set_on_stop_org_passthrough

    The lifecycle manager's late-binding callback setter must be
    reachable via the public ``OrgRuntime`` surface so ``api/server.py``
    can wire the F2 stop-org cancel propagation without poking the
    private ``_lifecycle`` attribute.
    """

    rt = _make_runtime()
    calls: list[tuple[str, str]] = []

    async def cb(org_id: str, reason: str) -> None:
        calls.append((org_id, reason))

    rt.set_on_stop_org(cb)  # must not raise.
