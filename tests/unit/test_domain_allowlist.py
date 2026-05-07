"""Unit tests for the per-conversation domain allow/block list."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import health
from openakita.core.domain_allowlist import DomainAllowlist, get_domain_allowlist


@pytest.fixture(autouse=True)
def _reset_singleton():
    get_domain_allowlist().clear()
    yield
    get_domain_allowlist().clear()


def test_default_decision_is_allow():
    al = DomainAllowlist()
    assert al.decide("conv1", "example.com") == "allow"


def test_block_then_decide_returns_deny():
    al = DomainAllowlist()
    assert al.block("conv1", "Example.com") is True
    assert al.decide("conv1", "example.com") == "deny"
    # Re-blocking is idempotent (changed=False).
    assert al.block("conv1", "example.com") is False


def test_block_strips_www_prefix_and_normalises_case():
    al = DomainAllowlist()
    al.block("conv1", "www.Example.com")
    assert al.decide("conv1", "example.com") == "deny"
    assert al.decide("conv1", "WWW.example.com") == "deny"


def test_unblock_returns_changed_only_when_present():
    al = DomainAllowlist()
    al.block("conv1", "example.com")
    assert al.unblock("conv1", "example.com") is True
    assert al.decide("conv1", "example.com") == "allow"
    assert al.unblock("conv1", "example.com") is False


def test_rules_are_isolated_per_conversation():
    al = DomainAllowlist()
    al.block("conv1", "example.com")
    assert al.decide("conv1", "example.com") == "deny"
    assert al.decide("conv2", "example.com") == "allow"


def test_list_for_returns_sorted_unique_hosts():
    al = DomainAllowlist()
    al.block("conv1", "b.example.com")
    al.block("conv1", "a.example.com")
    al.approve("conv1", "trusted.example.com")
    listing = al.list_for("conv1")
    assert listing == {
        "blocked": ["a.example.com", "b.example.com"],
        "allowed": ["trusted.example.com"],
    }


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health.router)
    return app


def test_block_endpoint_makes_decision_deny():
    client = TestClient(_build_app())
    resp = client.post(
        "/api/diagnostics/domain-block",
        params={"conversation_id": "conv1", "host": "evil.example.net"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "evil.example.net" in body["blocked"]
    assert get_domain_allowlist().decide("conv1", "evil.example.net") == "deny"


def test_unblock_endpoint_restores_allow():
    al = get_domain_allowlist()
    al.block("conv1", "evil.example.net")
    client = TestClient(_build_app())
    resp = client.post(
        "/api/diagnostics/domain-unblock",
        params={"conversation_id": "conv1", "host": "evil.example.net"},
    )
    assert resp.status_code == 200
    assert resp.json()["changed"] is True
    assert al.decide("conv1", "evil.example.net") == "allow"


def test_rules_endpoint_lists_state():
    al = get_domain_allowlist()
    al.block("conv1", "blocked.example.com")
    al.approve("conv1", "trusted.example.com")
    client = TestClient(_build_app())
    resp = client.get("/api/diagnostics/domain-rules", params={"conversation_id": "conv1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] == ["blocked.example.com"]
    assert body["allowed"] == ["trusted.example.com"]


def test_block_and_unblock_require_args():
    client = TestClient(_build_app())
    r1 = client.post(
        "/api/diagnostics/domain-block", params={"conversation_id": "", "host": "x.com"}
    )
    r2 = client.post(
        "/api/diagnostics/domain-unblock", params={"conversation_id": "c", "host": ""}
    )
    assert r1.json()["ok"] is False
    assert r2.json()["ok"] is False
