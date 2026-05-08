from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from openakita.core.reasoning_engine import ReasoningEngine
from openakita.llm.client import EndpointOverride, LLMClient
from openakita.llm.types import AllEndpointsFailedError, EndpointConfig


class FakeLLMClient:
    def __init__(self, ok: bool):
        self.ok = ok
        self.calls: list[dict] = []
        self._providers = {"good-endpoint": SimpleNamespace(model="good-model")}

    def switch_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.ok:
            return True, "switched"
        return False, "端点 'missing-endpoint' 不存在。可用端点: good-endpoint"


def _engine_with(client: FakeLLMClient) -> ReasoningEngine:
    engine = object.__new__(ReasoningEngine)
    engine._brain = SimpleNamespace(_llm_client=client)
    return engine


def test_endpoint_override_failure_falls_back_to_auto():
    client = FakeLLMClient(ok=False)
    engine = _engine_with(client)

    switched = engine._apply_endpoint_override(
        "missing-endpoint",
        conversation_id="conv-1",
        reason="test stale endpoint",
    )

    assert switched is False
    assert client.calls[0]["endpoint_name"] == "missing-endpoint"
    assert client.calls[0]["conversation_id"] == "conv-1"
    assert client.calls[0]["policy"] == "prefer"


def test_endpoint_override_success_is_preserved():
    client = FakeLLMClient(ok=True)
    engine = _engine_with(client)

    switched = engine._apply_endpoint_override(
        "good-endpoint",
        conversation_id="conv-1",
        reason="test valid endpoint",
    )

    assert switched is True
    assert client.calls[0]["endpoint_name"] == "good-endpoint"


def test_endpoint_override_policy_is_forwarded():
    client = FakeLLMClient(ok=True)
    engine = _engine_with(client)

    switched = engine._apply_endpoint_override(
        "good-endpoint",
        conversation_id="conv-1",
        reason="test required endpoint",
        endpoint_policy="require",
    )

    assert switched is True
    assert client.calls[0]["policy"] == "require"


def _provider(
    name: str,
    *,
    healthy: bool = True,
    capabilities: list[str] | None = None,
) -> SimpleNamespace:
    config = EndpointConfig(
        name=name,
        provider="openai",
        api_type="openai",
        base_url="https://example.invalid/v1",
        model=f"{name}-model",
        capabilities=capabilities or ["text", "tools"],
    )
    return SimpleNamespace(
        name=name,
        config=config,
        model=config.model,
        is_healthy=healthy,
        cooldown_remaining=30 if not healthy else 0,
        error_category="transient",
        reset_cooldown=lambda: None,
    )


def _llm_client_with_required_override(
    selected: SimpleNamespace,
    fallback: SimpleNamespace,
    *,
    policy: str = "require",
) -> LLMClient:
    client = object.__new__(LLMClient)
    client._providers = {selected.name: selected, fallback.name: fallback}
    client._endpoint_override = None
    client._conversation_overrides = {
        "conv-1": EndpointOverride(
            endpoint_name=selected.name,
            expires_at=datetime.now() + timedelta(minutes=5),
            policy=policy,
        )
    }
    return client


def test_required_endpoint_does_not_fall_back_when_unhealthy():
    selected = _provider("local", healthy=False)
    fallback = _provider("glm", healthy=True)
    client = _llm_client_with_required_override(selected, fallback)

    eligible = client._filter_eligible_endpoints(
        require_tools=True,
        conversation_id="conv-1",
    )

    assert [provider.name for provider in eligible] == ["local"]


def test_preferred_endpoint_can_fall_back_when_unhealthy():
    selected = _provider("local", healthy=False)
    fallback = _provider("glm", healthy=True)
    client = _llm_client_with_required_override(selected, fallback, policy="prefer")

    eligible = client._filter_eligible_endpoints(
        require_tools=True,
        conversation_id="conv-1",
    )

    assert [provider.name for provider in eligible] == ["glm"]


@pytest.mark.asyncio
async def test_required_endpoint_missing_hard_capability_blocks_fallback():
    selected = _provider("local", healthy=True, capabilities=["text"])
    fallback = _provider("glm", healthy=True, capabilities=["text", "tools"])
    client = _llm_client_with_required_override(selected, fallback)
    request = SimpleNamespace(enable_thinking=False)

    with pytest.raises(AllEndpointsFailedError) as exc_info:
        await client._resolve_providers_with_fallback(
            request=request,
            require_tools=True,
            conversation_id="conv-1",
        )

    assert "Required endpoint 'local' does not support" in str(exc_info.value)
