"""L1 unit tests for web_fetch provenance and redirect safety."""

from __future__ import annotations

import httpx
import pytest

from openakita.tools.handlers.web_fetch import WebFetchHandler


class _FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self._text = text
        self.content = text.encode("utf-8")
        self.request = httpx.Request("GET", url)

    @property
    def text(self) -> str:
        return self._text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(
                self.status_code,
                request=self.request,
                headers=self.headers,
                content=self.content,
            )
            raise httpx.HTTPStatusError("HTTP error", request=self.request, response=response)


class _FakeAsyncClient:
    routes: dict[str, _FakeResponse] = {}
    calls: list[str] = []

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        response = self.routes[url]
        response.url = url
        return response


@pytest.fixture(autouse=True)
def _safe_url(monkeypatch):
    async def _always_safe(_url: str):
        return True, "ok"

    monkeypatch.setattr("openakita.utils.url_safety.is_safe_url", _always_safe)


@pytest.fixture
def _fake_httpx(monkeypatch):
    _FakeAsyncClient.routes = {}
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


@pytest.mark.asyncio
async def test_web_fetch_includes_requested_final_and_source_marker(_fake_httpx):
    _fake_httpx.routes = {
        "https://example.com/a": _FakeResponse(
            "https://example.com/a",
            text="<html><body><h1>Hello</h1><p>World</p></body></html>",
        )
    }

    result = await WebFetchHandler(agent=None)._web_fetch({"url": "https://example.com/a"})

    assert "[OPENAKITA_SOURCE]" in result
    assert "Requested URL: https://example.com/a" in result
    assert "Final URL: https://example.com/a" in result
    assert "Content-Type: text/html" in result
    assert "Hello" in result or "World" in result


@pytest.mark.asyncio
async def test_web_fetch_follows_same_host_redirect(_fake_httpx):
    _fake_httpx.routes = {
        "https://example.com/a": _FakeResponse(
            "https://example.com/a",
            status_code=302,
            headers={"location": "/b", "content-type": "text/html"},
        ),
        "https://example.com/b": _FakeResponse(
            "https://example.com/b",
            text="<html><body>Redirect target</body></html>",
        ),
    }

    result = await WebFetchHandler(agent=None)._web_fetch({"url": "https://example.com/a"})

    assert "Final URL: https://example.com/b" in result
    assert "Redirects: https://example.com/a -> https://example.com/b" in result
    assert "Redirect target" in result


@pytest.mark.asyncio
async def test_web_fetch_follows_cross_host_redirect_and_records_chain(_fake_httpx):
    """Cross-host redirects are followed like a normal browser and disclosed via redirect_chain."""
    _fake_httpx.routes = {
        "https://example.com/a": _FakeResponse(
            "https://example.com/a",
            status_code=302,
            headers={"location": "https://other.example.org/b", "content-type": "text/html"},
        ),
        "https://other.example.org/b": _FakeResponse(
            "https://other.example.org/b",
            text="<html><body>Cross-host target</body></html>",
        ),
    }

    result = await WebFetchHandler(agent=None)._web_fetch({"url": "https://example.com/a"})

    assert "Requested URL: https://example.com/a" in result
    assert "Final URL: https://other.example.org/b" in result
    assert "Cross-host target" in result
    assert "Redirects: https://example.com/a -> https://other.example.org/b" in result
    assert "cross_host_redirect" not in result


@pytest.mark.asyncio
async def test_web_fetch_reports_binary_content(_fake_httpx):
    _fake_httpx.routes = {
        "https://example.com/file.pdf": _FakeResponse(
            "https://example.com/file.pdf",
            headers={"content-type": "application/pdf"},
            text="%PDF-1.7",
        )
    }

    result = await WebFetchHandler(agent=None)._web_fetch({"url": "https://example.com/file.pdf"})

    assert "binary_content" in result
    assert "application/pdf" in result
    assert "浏览器工具" in result


@pytest.mark.asyncio
async def test_web_fetch_respects_session_domain_block(_fake_httpx):
    """Once a user blocks a host in this conversation, web_fetch must refuse it."""

    from types import SimpleNamespace

    from openakita.core.domain_allowlist import get_domain_allowlist

    al = get_domain_allowlist()
    al.clear()
    try:
        agent = SimpleNamespace(_current_conversation_id="conv-block-1")
        al.block(agent._current_conversation_id, "evil.example.net")

        _fake_httpx.routes = {
            "https://evil.example.net/page": _FakeResponse(
                "https://evil.example.net/page",
                text="<html><body>nope</body></html>",
            )
        }
        result = await WebFetchHandler(agent=agent)._web_fetch(
            {"url": "https://evil.example.net/page"}
        )

        assert "domain_blocked" in result
        assert "evil.example.net" in result
        assert "https://evil.example.net/page" not in _fake_httpx.calls, (
            "blocked host must not have been contacted"
        )
    finally:
        al.clear()
