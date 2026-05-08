import pytest

from openakita.tools.handlers.config import ConfigHandler


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers):
        assert headers["Accept-Encoding"] == "gzip, deflate"
        return _FakeResponse(
            500,
            (
                '{"error":{"message":"AppIdNoAuthError:`app``astron-code-latest`'
                'tokens.total;business.total","code":"11200"}}'
            ),
        )


@pytest.mark.asyncio
async def test_add_endpoint_probe_disables_clear_xfyun_auth_or_quota(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    handler = ConfigHandler.__new__(ConfigHandler)

    result = await handler._probe_endpoint_before_enable(
        {
            "api_type": "openai",
            "base_url": "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2",
        },
        "sk-test",
    )

    assert result["disable"] is True
