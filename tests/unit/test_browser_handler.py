"""L1 unit tests for browser provenance and shared-page locking policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openakita.tools.handlers.browser import BrowserHandler, _LOCKED_BROWSER_OPS


class _FakePage:
    url = "https://example.com/current"

    async def title(self) -> str:
        return "Example Current"


class _FakeBrowserManager:
    def __init__(self) -> None:
        self.page = _FakePage()


class _FakePlaywrightTools:
    async def get_content(self, selector=None, format="text"):
        return {
            "success": True,
            "result": f"content selector={selector or 'document'} format={format}",
        }

    async def navigate(self, url: str):
        return {"success": True, "result": f"navigated {url}"}


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        name="tester",
        browser_manager=_FakeBrowserManager(),
        pw_tools=_FakePlaywrightTools(),
    )


def test_current_page_readers_are_locked_with_navigation():
    assert "browser_navigate" in _LOCKED_BROWSER_OPS
    assert "browser_get_content" in _LOCKED_BROWSER_OPS
    assert "browser_screenshot" in _LOCKED_BROWSER_OPS


@pytest.mark.asyncio
async def test_browser_get_content_reports_actual_page_source():
    handler = BrowserHandler(_agent())

    result = await handler.handle(
        "browser_get_content",
        {"selector": "main", "format": "text", "expected_url": "https://example.com/current"},
    )

    assert "[OPENAKITA_SOURCE]" in result
    assert "Current URL: https://example.com/current" in result
    assert "Title: Example Current" in result
    assert "Selector: main" in result
    assert "content selector=main format=text" in result


@pytest.mark.asyncio
async def test_browser_get_content_warns_when_expected_url_differs():
    handler = BrowserHandler(_agent())

    result = await handler.handle(
        "browser_get_content",
        {"expected_url": "https://example.com/old"},
    )

    assert "Expected URL: https://example.com/old" in result
    assert "Warning:" in result
    assert "不一致" in result
