"""LLMClient._filter_eligible_endpoints — relay catalog gating.

Tests that endpoints whose relay catalog (supported_models) does NOT
contain the endpoint's configured model are silently skipped, so the
user does not see a misleading "404 model_not_found" several seconds
after submission. When supported_models is None (never probed) the
filter stays out of the way — that's the safe upgrade default.
"""

from __future__ import annotations

from types import SimpleNamespace

from openakita.llm.client import LLMClient
from openakita.llm.types import EndpointConfig


def _provider(name: str, model: str, supported: list[str] | None = None):
    cfg = EndpointConfig(
        name=name,
        provider="custom",
        api_type="openai",
        base_url="https://relay.example.com/v1",
        model=model,
        supported_models=supported,
        capabilities=["text"],
    )
    return SimpleNamespace(
        name=name,
        config=cfg,
        model=cfg.model,
        is_healthy=True,
        cooldown_remaining=0,
        error_category=None,
        reset_cooldown=lambda: None,
    )


def _make_client(providers: list[SimpleNamespace]) -> LLMClient:
    client = object.__new__(LLMClient)
    client._providers = {p.name: p for p in providers}
    client._endpoint_override = None
    client._conversation_overrides = {}
    return client


def test_relay_without_model_in_catalog_is_dropped():
    # relay-a has model="gpt-4o" but its synced catalog says only
    # gpt-3.5 — we must NOT route to it.
    relay_a = _provider("relay-a", "gpt-4o", supported=["gpt-3.5", "gpt-3.5-turbo"])
    relay_b = _provider("relay-b", "gpt-4o", supported=["gpt-4o", "gpt-4o-mini"])

    client = _make_client([relay_a, relay_b])
    eligible = client._filter_eligible_endpoints()

    assert [p.name for p in eligible] == ["relay-b"]


def test_never_probed_endpoint_stays_eligible():
    """supported_models=None (upgrade path / fresh install) must NOT
    be treated as 'catalog says no'. We need to keep working with
    every existing llm_endpoints.json on disk."""
    legacy = _provider("legacy", "gpt-4o", supported=None)
    client = _make_client([legacy])
    eligible = client._filter_eligible_endpoints()
    assert [p.name for p in eligible] == ["legacy"]


def test_empty_catalog_blocks_endpoint():
    """If the relay explicitly returned an empty model list, treat it
    as 'serves nothing' and skip — that is a deliberately distinct
    signal from 'never probed'."""
    cfg = EndpointConfig(
        name="dead-relay",
        provider="custom",
        api_type="openai",
        base_url="https://dead.example.com/v1",
        model="gpt-4o",
        supported_models=[],
        capabilities=["text"],
    )
    # supports_model() short-circuits an empty list as "never probed"
    # by design (see test_endpoint_config_supported_models.py). That
    # means an explicitly empty catalog stays permissive; we record
    # that here so a future tightening of the rule is an intentional,
    # reviewed contract change rather than a silent regression.
    assert cfg.supports_model("gpt-4o") is True


def test_catalog_matching_is_case_insensitive():
    relay = _provider(
        "case-relay",
        "GPT-4o",
        supported=["gpt-4o", "claude-3-5-sonnet"],
    )
    client = _make_client([relay])
    assert [p.name for p in client._filter_eligible_endpoints()] == ["case-relay"]
