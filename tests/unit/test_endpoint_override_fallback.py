from types import SimpleNamespace

from openakita.core.reasoning_engine import ReasoningEngine


class FakeLLMClient:
    def __init__(self, ok: bool):
        self.ok = ok
        self.calls: list[dict] = []
        self._providers = {"good-endpoint": SimpleNamespace(model="good-model")}

    def switch_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.ok:
            return True, "switched"
        return False, "Endpoint 'missing-endpoint' does not exist. Available endpoints: good-endpoint"


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
