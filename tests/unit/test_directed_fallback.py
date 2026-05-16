"""LLMClient — directed fallback chain.

EndpointConfig.fallback_endpoint + fallback_enabled let the user
say "if X fails, prefer Y" without re-juggling priorities. These
tests freeze the contract that:

1. Opt-out by default: legacy configs (fallback_enabled=False) keep
   the existing priority-sorted order bit-for-bit.
2. One-hop only: even if Y also configures a fallback to Z, the
   reorder does not chain — multi-hop fallback is a config smell.
3. The fallback target is promoted to the slot RIGHT AFTER its
   source, regardless of where priority would have put it.
4. Reorder is stable: endpoints not involved in any fallback stay
   in their original position.
5. Dangling fallback names (referencing a removed endpoint, or one
   that was filtered out for capability mismatch) are silently
   ignored — they never crash the lookup.

Round-trip serialization is covered in
``test_endpoint_config_supported_models.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from openakita.llm.client import LLMClient
from openakita.llm.types import EndpointConfig


def _provider(
    name: str,
    *,
    priority: int = 10,
    fallback: str | None = None,
    fallback_enabled: bool = False,
):
    cfg = EndpointConfig(
        name=name,
        provider="custom",
        api_type="openai",
        base_url="https://x.example.com/v1",
        model="gpt-4o",
        priority=priority,
        capabilities=["text"],
        fallback_endpoint=fallback,
        fallback_enabled=fallback_enabled,
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


def _client(providers):
    c = object.__new__(LLMClient)
    c._providers = {p.name: p for p in providers}
    c._endpoint_override = None
    c._conversation_overrides = {}
    return c


def test_no_fallback_keeps_priority_order():
    a = _provider("a", priority=1)
    b = _provider("b", priority=5)
    c = _provider("c", priority=10)
    cli = _client([c, b, a])  # insertion order intentionally scrambled

    eligible = cli._filter_eligible_endpoints()
    assert [p.name for p in eligible] == ["a", "b", "c"]


def test_enabled_fallback_promoted_after_source():
    """relay-a has priority=1 and points at official=priority=99.
    Without directed fallback official would be tried LAST; with it
    official is tried second."""
    relay = _provider("relay-a", priority=1, fallback="official", fallback_enabled=True)
    fast = _provider("openrouter-fast", priority=5)
    official = _provider("official", priority=99)

    eligible = _client([fast, official, relay])._filter_eligible_endpoints()
    assert [p.name for p in eligible] == ["relay-a", "official", "openrouter-fast"]


def test_disabled_fallback_is_ignored():
    """fallback_endpoint set but fallback_enabled=False = legacy noise.
    Must NOT reorder; user keeps priority-only behaviour."""
    relay = _provider("relay-a", priority=1, fallback="official", fallback_enabled=False)
    fast = _provider("openrouter-fast", priority=5)
    official = _provider("official", priority=99)

    eligible = _client([fast, official, relay])._filter_eligible_endpoints()
    assert [p.name for p in eligible] == ["relay-a", "openrouter-fast", "official"]


def test_one_hop_only():
    """Even if Y itself fallbacks to Z, the chain does NOT extend."""
    a = _provider("a", priority=1, fallback="b", fallback_enabled=True)
    b = _provider("b", priority=5, fallback="c", fallback_enabled=True)
    c = _provider("c", priority=10)

    eligible = _client([a, b, c])._filter_eligible_endpoints()
    # 'a' promotes 'b', then we visit b in original loop (already
    # seen, skipped), then we visit c which is fresh. No further
    # promotion happens for b.fallback because seen-set already has b.
    assert [p.name for p in eligible] == ["a", "b", "c"]


def test_dangling_fallback_name_is_silently_dropped():
    """Endpoint named in fallback_endpoint was deleted / disabled.
    The remaining endpoints must still serve traffic — never raise."""
    a = _provider("a", priority=1, fallback="ghost", fallback_enabled=True)
    b = _provider("b", priority=5)

    eligible = _client([a, b])._filter_eligible_endpoints()
    assert [p.name for p in eligible] == ["a", "b"]


def test_fallback_target_not_duplicated():
    """If the priority order already puts the fallback target right
    after its source, the reorder is a no-op for that pair (and the
    target must not appear twice)."""
    a = _provider("a", priority=1, fallback="b", fallback_enabled=True)
    b = _provider("b", priority=2)
    c = _provider("c", priority=3)

    names = [p.name for p in _client([a, b, c])._filter_eligible_endpoints()]
    assert names == ["a", "b", "c"]
    assert names.count("b") == 1
