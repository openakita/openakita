"""Tests for :class:`openakita.runtime.llm.failover.EndpointFailoverView`.

Run against a hand-rolled fake (``_FakeClient``) so the suite stays
hermetic. Coverage hits happy-path, failover-to-second, and all-fail
states plus a smoke for every mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openakita.runtime.llm import EndpointFailoverView


@dataclass
class _FakeEndpoint:
    name: str
    model: str = "fake-model"


@dataclass
class _FakeProvider:
    model: str
    is_healthy: bool = True


@dataclass
class _FakeModelInfo:
    name: str = "primary"
    model: str = "fake-model"
    provider: str = "fake"
    priority: int = 100
    is_healthy: bool = True
    is_current: bool = True
    is_override: bool = False
    capabilities: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class _FakeClient:
    endpoints: list[_FakeEndpoint] = field(default_factory=list)
    providers: dict[str, _FakeProvider] = field(default_factory=dict)
    health: dict[str, bool] = field(default_factory=dict)
    next_endpoint: str | None = None
    current_model: _FakeModelInfo | None = None
    models: list[_FakeModelInfo] = field(default_factory=list)
    override: dict[str, Any] | None = None
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    async def health_check(self) -> dict[str, bool]:
        self.calls.append(("health_check", (), {}))
        return dict(self.health)

    def switch_model(self, endpoint_name, hours, reason, *, conversation_id=None, policy="prefer"):
        self.calls.append((
            "switch_model", (endpoint_name, hours, reason),
            {"conversation_id": conversation_id, "policy": policy},
        ))
        return True, f"ok:{endpoint_name}"

    def restore_default(self, conversation_id=None):
        self.calls.append(("restore_default", (), {"conversation_id": conversation_id}))
        return True, "restored"

    def get_current_model(self, conversation_id=None):
        self.calls.append(("get_current_model", (), {"conversation_id": conversation_id}))
        return self.current_model

    def get_next_endpoint(self, conversation_id=None):
        self.calls.append(("get_next_endpoint", (), {"conversation_id": conversation_id}))
        return self.next_endpoint

    def list_available_models(self):
        self.calls.append(("list_available_models", (), {}))
        return list(self.models)

    def get_override_status(self):
        self.calls.append(("get_override_status", (), {}))
        return self.override

    def update_priority(self, priority_order):
        self.calls.append(("update_priority", (tuple(priority_order),), {}))
        return True, "priority updated"


def test_current_endpoint_info_happy_picks_first_healthy() -> None:
    client = _FakeClient(
        endpoints=[_FakeEndpoint(name="primary"), _FakeEndpoint(name="secondary")],
        providers={
            "primary": _FakeProvider(model="m1", is_healthy=True),
            "secondary": _FakeProvider(model="m2", is_healthy=True),
        },
    )
    assert EndpointFailoverView(client).current_endpoint_info() == {
        "name": "primary", "model": "m1", "healthy": True,
    }


def test_current_endpoint_info_failover_to_first_endpoint_when_no_healthy() -> None:
    client = _FakeClient(
        endpoints=[_FakeEndpoint(name="primary", model="m1"),
                   _FakeEndpoint(name="secondary", model="m2")],
        providers={
            "primary": _FakeProvider(model="m1", is_healthy=False),
            "secondary": _FakeProvider(model="m2", is_healthy=False),
        },
    )
    assert EndpointFailoverView(client).current_endpoint_info() == {
        "name": "primary", "model": "m1", "healthy": False,
    }


def test_current_endpoint_info_all_fail_sentinel() -> None:
    assert EndpointFailoverView(_FakeClient()).current_endpoint_info() == {
        "name": "none", "model": "none", "healthy": False,
    }


def test_next_fallback_model_returns_empty_when_none_and_forwards_conv_id() -> None:
    client = _FakeClient(next_endpoint=None)
    view = EndpointFailoverView(client)
    assert view.next_fallback_model() == ""
    client.next_endpoint = "secondary"
    assert view.next_fallback_model(conversation_id="conv-1") == "secondary"
    assert client.calls[-1] == ("get_next_endpoint", (), {"conversation_id": "conv-1"})


def test_current_model_info_renders_dict_or_error() -> None:
    client = _FakeClient(current_model=None)
    view = EndpointFailoverView(client)
    assert view.current_model_info() == {"error": "no available model"}
    client.current_model = _FakeModelInfo(
        name="haiku", model="claude-3-haiku", provider="anthropic",
        is_override=True, capabilities=["tools", "vision"], note="cost-optimised",
    )
    assert view.current_model_info(conversation_id="conv-2") == {
        "name": "haiku", "model": "claude-3-haiku", "provider": "anthropic",
        "is_healthy": True, "is_override": True,
        "capabilities": ["tools", "vision"], "note": "cost-optimised",
    }


def test_mutations_proxy_through_to_client() -> None:
    client = _FakeClient()
    view = EndpointFailoverView(client)
    assert view.switch_model("haiku", 6.0, reason="cost", conversation_id="c1") == (True, "ok:haiku")
    assert view.restore_default(conversation_id="c1") == (True, "restored")
    assert view.update_priority(["haiku", "sonnet"]) == (True, "priority updated")
    assert [c[0] for c in client.calls] == ["switch_model", "restore_default", "update_priority"]


@pytest.mark.asyncio
async def test_health_check_awaits_through_to_client() -> None:
    client = _FakeClient(health={"primary": True, "secondary": False})
    assert await EndpointFailoverView(client).health_check() == {"primary": True, "secondary": False}
