from types import SimpleNamespace

import pytest

from openakita.llm.registries.openrouter import OpenRouterRegistry
from openakita.setup_center.bridge import _list_models_openai
from openakita.tools.handlers.config import ConfigHandler


@pytest.mark.asyncio
async def test_openrouter_registry_falls_back_to_router_models(monkeypatch):
    class BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(
        "openakita.llm.registries.openrouter.create_registry_client",
        lambda _target_url: BrokenClient(),
    )

    models = await OpenRouterRegistry().list_models("sk-test")
    ids = {m.id for m in models}

    assert {"openrouter/auto", "openrouter/free"} <= ids


@pytest.mark.asyncio
async def test_openrouter_model_list_includes_router_models(monkeypatch):
    import httpx

    class FakeResponse:
        headers = {"content-type": "application/json"}
        text = ""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {"id": "openrouter/auto"},
                    {"id": "mistralai/mistral-small-3.2-24b-instruct:free"},
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    models = await _list_models_openai(
        "sk-test",
        "https://openrouter.ai/api/v1",
        "openrouter",
    )
    ids = [m["id"] for m in models]

    assert ids.count("openrouter/auto") == 1
    assert "openrouter/free" in ids
    assert "mistralai/mistral-small-3.2-24b-instruct:free" in ids


@pytest.mark.asyncio
async def test_config_handler_rejects_openrouter_without_key_before_saving():
    handler = ConfigHandler(SimpleNamespace())

    result = await handler.handle(
        "system_config",
        {
            "action": "add_endpoint",
            "endpoint": {
                "name": "openrouter-free",
                "provider": "openrouter",
                "model": "openrouter/free",
            },
        },
    )

    assert "OpenRouter" in result
    assert "API Key" in result
