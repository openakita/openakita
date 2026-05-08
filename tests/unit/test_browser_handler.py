"""L1 unit tests for browser provenance and shared-page locking policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openakita.tools.handlers.browser import _LOCKED_BROWSER_OPS, BrowserHandler


class _FakePage:
    url = "https://example.com/current"

    async def title(self) -> str:
        return "Example Current"


class _ClosedPage:
    url = "https://example.com/current"

    async def title(self) -> str:
        raise RuntimeError("Target page has been closed")


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


class _ClosedPlaywrightTools:
    async def navigate(self, url: str):
        raise RuntimeError("Target page, context or browser has been closed")


class _StartableBrowserManager:
    def __init__(self) -> None:
        self.is_ready = False
        self.context = None
        self.page = None
        self.visible = False
        self.using_user_chrome = True
        self.started = False
        self.reset_count = 0

    async def reset_state(self) -> None:
        self.is_ready = False
        self.context = None
        self.page = None
        self.reset_count += 1

    async def start(self, visible=True):
        self.started = True
        self.visible = visible
        self.is_ready = True
        self.page = _FakePage()
        self.context = SimpleNamespace(pages=[self.page])
        return True


class _ReadyBrowserManager(_StartableBrowserManager):
    def __init__(self) -> None:
        super().__init__()
        self.is_ready = True
        self.visible = True
        self.context = SimpleNamespace(pages=[_FakePage(), _FakePage()])
        self.page = self.context.pages[0]


class _ReadyClosedBrowserManager(_StartableBrowserManager):
    def __init__(self) -> None:
        super().__init__()
        self.is_ready = True
        self.context = SimpleNamespace(pages=[])
        self.page = _ClosedPage()


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


@pytest.mark.asyncio
async def test_closed_browser_sets_user_confirmation_gate():
    manager = _StartableBrowserManager()
    agent = SimpleNamespace(
        name="tester",
        browser_manager=manager,
        pw_tools=_ClosedPlaywrightTools(),
    )
    handler = BrowserHandler(agent)

    closed_result = await handler.handle("browser_navigate", {"url": "https://example.com"})
    assert "不要自动重新打开前台浏览器" in closed_result
    assert agent._browser_user_closed is True

    blocked_open = await handler.handle("browser_open", {"visible": True})
    assert "本次启动已被拦截" in blocked_open
    assert manager.started is False


@pytest.mark.asyncio
async def test_user_confirmed_browser_open_clears_closed_gate():
    manager = _StartableBrowserManager()
    agent = SimpleNamespace(
        name="tester",
        browser_manager=manager,
        pw_tools=_FakePlaywrightTools(),
        _browser_user_closed=True,
    )
    handler = BrowserHandler(agent)

    result = await handler.handle(
        "browser_open",
        {"visible": True, "user_confirmed": True},
    )

    assert "status" in result
    assert manager.started is True
    assert agent._browser_user_closed is False


@pytest.mark.asyncio
async def test_browser_open_does_not_claim_desktop_foreground_for_headed_session():
    manager = _ReadyBrowserManager()
    agent = SimpleNamespace(
        name="tester",
        browser_manager=manager,
        pw_tools=_FakePlaywrightTools(),
    )
    handler = BrowserHandler(agent)

    result = await handler.handle("browser_open", {"visible": True})

    assert "automation_ready" in result
    assert "'headed': True" in result
    assert "'desktop_window_visible': None" in result
    assert "'foreground_verified': None" in result
    assert "尚未验证系统桌面窗口是否可见或处于前台" in result
    assert "已在可见模式运行" not in result
    assert "现在已经在前台" not in result


@pytest.mark.asyncio
async def test_browser_open_does_not_restart_after_existing_page_was_closed():
    manager = _ReadyClosedBrowserManager()
    agent = SimpleNamespace(
        name="tester",
        browser_manager=manager,
        pw_tools=_FakePlaywrightTools(),
    )
    handler = BrowserHandler(agent)

    result = await handler.handle("browser_open", {"visible": True})

    assert "本次启动已被拦截" in result
    assert manager.started is False
    assert agent._browser_user_closed is True
