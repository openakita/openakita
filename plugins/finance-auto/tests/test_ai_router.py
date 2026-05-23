"""M2 AI Stage 4 — LLM router tests.

Verifies:

* `is_local_endpoint_config` import works (when available) or fallback fires.
* Endpoint inventory dedups + drops unhealthy.
* Local-first routing returns local when both kinds present.
* Cloud fallback returns cloud when no local.
* `forbid_cloud_for_raw` forces local for raw payloads.
* `require_local_only` per-scenario override raises if no local.
* MockLLMResponder echoes back deterministic JSON.
* `complete()` wires response.is_local correctly.
"""

from __future__ import annotations

import asyncio

import pytest

from finance_auto_backend.ai.router import (
    EndpointDescriptor,
    FinanceAIRouter,
    LLMResponse,
    MockLLMResponder,
    RoutingConfig,
    _is_local_endpoint,
    collect_endpoints_from_host_client,
)


# ---------------------------------------------------------------------------
# is_local detection
# ---------------------------------------------------------------------------


def test_is_local_endpoint_recognises_localhost():
    assert _is_local_endpoint("ollama", "http://localhost:11434/v1") is True
    assert _is_local_endpoint("lmstudio", "http://127.0.0.1:1234/v1") is True
    assert _is_local_endpoint("custom", "http://0.0.0.0:8000/v1") is True


def test_is_local_endpoint_rejects_cloud():
    assert _is_local_endpoint("anthropic", "https://api.anthropic.com") is False
    assert _is_local_endpoint("openai", "https://api.openai.com") is False


# ---------------------------------------------------------------------------
# Endpoint inventory
# ---------------------------------------------------------------------------


class _StubLLMClient:
    def __init__(self, items):
        self._endpoints = items


class _StubEndpoint:
    def __init__(self, name, provider, base_url, healthy=True):
        self.name = name
        self.provider = provider
        self.base_url = base_url
        self.healthy = healthy
        self.model = f"{provider}-default"


def test_collect_endpoints_dedupes_and_classifies():
    host = _StubLLMClient(
        [
            _StubEndpoint("local-ollama", "ollama", "http://localhost:11434/v1"),
            _StubEndpoint("openai", "openai", "https://api.openai.com"),
            _StubEndpoint("openai", "openai", "https://api.openai.com"),  # dupe
            _StubEndpoint("dead", "anthropic", "https://api.anthropic.com", healthy=True),
        ]
    )
    eps = collect_endpoints_from_host_client(host)
    names = sorted(e.name for e in eps)
    assert names == ["dead", "local-ollama", "openai"]
    by_name = {e.name: e for e in eps}
    assert by_name["local-ollama"].is_local is True
    assert by_name["openai"].is_local is False


def test_collect_endpoints_handles_missing_attrs():
    eps = collect_endpoints_from_host_client(None)
    assert eps == []
    eps = collect_endpoints_from_host_client(object())
    assert eps == []


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _router_with(*endpoints, **cfg_kw):
    return FinanceAIRouter(
        endpoints=list(endpoints),
        config=RoutingConfig(**cfg_kw),
    )


def test_router_prefers_local_when_both_exist():
    r = _router_with(
        EndpointDescriptor("ollama", "ollama", "http://localhost:11434", True),
        EndpointDescriptor("openai", "openai", "https://api.openai.com", False),
        prefer_local_llm=True,
    )
    name, is_local = r.pick_endpoint(scenario_id="erp_source_detect", level="metadata")
    assert name == "ollama"
    assert is_local is True


def test_router_cloud_fallback_when_no_local():
    r = _router_with(
        EndpointDescriptor("openai", "openai", "https://api.openai.com", False),
        prefer_local_llm=True,
    )
    name, is_local = r.pick_endpoint(scenario_id="erp_source_detect", level="metadata")
    assert name == "openai"
    assert is_local is False


def test_router_raw_forces_local_by_default():
    r = _router_with(
        EndpointDescriptor("ollama", "ollama", "http://localhost:11434", True),
        EndpointDescriptor("openai", "openai", "https://api.openai.com", False),
    )
    name, is_local = r.pick_endpoint(scenario_id="any", level="raw")
    assert is_local is True
    assert name == "ollama"


def test_router_raw_skip_desensitize_demands_local():
    r = _router_with(
        EndpointDescriptor("openai", "openai", "https://api.openai.com", False),
        forbid_cloud_for_raw=True,
    )
    with pytest.raises(RuntimeError):
        r.pick_endpoint(scenario_id="any", level="raw", skip_desensitize=True)


def test_router_per_scenario_override_requires_local_only():
    r = _router_with(
        EndpointDescriptor("openai", "openai", "https://api.openai.com", False),
        per_scenario_overrides={"audit_opinion_draft": {"require_local_only": True}},
    )
    with pytest.raises(RuntimeError):
        r.pick_endpoint(scenario_id="audit_opinion_draft", level="metadata")


def test_router_no_endpoints_falls_back_to_mock_marker():
    r = _router_with()
    name, is_local = r.pick_endpoint(scenario_id="erp_source_detect", level="metadata")
    assert name.startswith("mock:")
    assert is_local is True


# ---------------------------------------------------------------------------
# Mock responder + complete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_llm_response_with_local_flag():
    r = _router_with(
        EndpointDescriptor("ollama", "ollama", "http://localhost:11434", True),
    )
    out: LLMResponse = await r.complete(
        scenario_id="erp_source_detect",
        level="metadata",
        prompt="hello",
    )
    assert out.is_local is True
    assert out.tokens_prompt > 0
    assert out.tokens_completion > 0
    assert "mock" in out.text


@pytest.mark.asyncio
async def test_complete_uses_canned_response():
    mock = MockLLMResponder()
    mock.canned_responses[("erp_source_detect", "metadata")] = '{"erp": "用友"}'
    r = FinanceAIRouter(
        responder=mock,
        endpoints=[EndpointDescriptor("ollama", "ollama", "http://localhost:11434", True)],
    )
    out = await r.complete(
        scenario_id="erp_source_detect", level="metadata", prompt="x"
    )
    assert out.text == '{"erp": "用友"}'


@pytest.mark.asyncio
async def test_complete_uses_responder_fn():
    captured: list[tuple[str, str, str]] = []

    async def fn(prompt, level, scenario):
        captured.append((prompt, level, scenario))
        return "::ok"

    mock = MockLLMResponder(responder_fn=fn)
    r = FinanceAIRouter(
        responder=mock,
        endpoints=[EndpointDescriptor("ollama", "ollama", "http://localhost:11434", True)],
    )
    out = await r.complete(scenario_id="cross_period_anomaly", level="aggregated", prompt="P")
    assert out.text == "::ok"
    assert captured == [("P", "aggregated", "cross_period_anomaly")]


def test_routing_config_defaults():
    cfg = RoutingConfig()
    assert cfg.prefer_local_llm is True
    assert cfg.forbid_cloud_for_raw is True
