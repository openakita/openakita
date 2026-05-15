"""Tests for the web_search provider refactor (Phase 1 of the panel plan).

Covers:
  - ConfigHint / ToolConfigError contract
  - WebSearchHandler error→hint mapping
  - registry/runtime auto-detect fallback semantics
  - ToolExecutor catches ToolConfigError → returns (text, hint)
  - reasoning_engine event helper emits config_hint
  - orgs/runtime monkey-patch preserves the tuple contract
  - LLM converter drops the ``_hint`` side-channel field
  - /api/tools/web-search endpoints

Frontend bits (panels, hooks, components) are validated by the existing
component tests under tests/component (TBD when the JS test harness is ready).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.tools.tool_hints import ConfigHint, ToolConfigError
from openakita.tools.web_search import (
    AuthFailedError,
    ContentFilterError,
    MissingCredentialError,
    NetworkUnreachableError,
    NoProviderAvailable,
    RateLimitedError,
    SearchResult,
    run_news_search,
    run_web_search,
)
from openakita.tools.web_search.registry import register

# ---------------------------------------------------------------------------
# 1. ConfigHint / ToolConfigError contract
# ---------------------------------------------------------------------------


class TestConfigHint:
    def test_to_llm_text_has_no_markers(self) -> None:
        e = ToolConfigError(
            scope="web_search",
            error_code="missing_credential",
            title="搜索源未配置",
            message="请前往设置配置 Key。",
            actions=[{"id": "open_settings", "label": "去配置"}],
        )
        text = e.to_llm_text()
        assert text == "[搜索源未配置] 请前往设置配置 Key。"
        # No HTML/JSON-style markers that LLMs could learn to mimic.
        for marker in ("<", ">", "{", "}", "config_hint", "<openakita-"):
            assert marker not in text, f"marker {marker!r} leaked into LLM text"

    def test_hint_actions_preserved(self) -> None:
        e = ToolConfigError(
            scope="web_search",
            error_code="auth_failed",
            title="t",
            message="m",
            actions=[
                {"id": "open_settings", "label": "L1", "view": "config"},
                {"id": "ext", "label": "L2", "url": "https://x"},
            ],
        )
        assert e.hint.scope == "web_search"
        assert e.hint.error_code == "auth_failed"
        assert len(e.hint.actions) == 2
        assert e.hint.actions[0]["view"] == "config"

    def test_hint_dataclass_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        h = ConfigHint(
            scope="x", error_code="unknown", title="t", message="", actions=[]
        )
        with pytest.raises(FrozenInstanceError):
            h.scope = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. WebSearchHandler — error → ToolConfigError mapping
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Test double that raises whatever the test asks it to."""

    def __init__(
        self,
        provider_id: str = "fake",
        *,
        available: bool = True,
        order: int = 5,
        raise_on_search: Exception | None = None,
        web_results: list[SearchResult] | None = None,
        news_results: list[SearchResult] | None | type = None,
    ) -> None:
        self.id = provider_id
        self.label = f"Fake({provider_id})"
        self.requires_credential = True
        self.auto_detect_order = order
        self.signup_url = ""
        self.docs_url = ""
        self._available = available
        self._raise = raise_on_search
        self._web_results = web_results or []
        self._news_results = news_results

    def is_available(self) -> bool:
        return self._available

    async def search(self, query, **kw):
        if self._raise is not None:
            raise self._raise
        return self._web_results

    async def news_search(self, query, **kw):
        if self._news_results is None:
            return None
        return self._news_results


@pytest.mark.asyncio
class TestWebSearchHandlerErrorMapping:
    """Each provider error class maps to the expected ConfigHint code."""

    @pytest.fixture(autouse=True)
    def _isolate_registry(self, monkeypatch: pytest.MonkeyPatch):
        # Replace ALL real providers with controllable fakes.
        from openakita.tools.web_search import registry as reg

        monkeypatch.setattr(reg, "_PROVIDERS", {}, raising=False)
        monkeypatch.setattr(reg, "_LOADED", True, raising=False)
        yield

    async def _run_handler(self) -> tuple[str | None, ConfigHint | None]:
        from openakita.tools.handlers.web_search import WebSearchHandler

        h = WebSearchHandler()
        try:
            text = await h._web_search({"query": "test", "timeout_seconds": 1})
            return text, None
        except ToolConfigError as e:
            return e.to_llm_text(), e.hint

    async def test_missing_credential_when_no_providers_available(self) -> None:
        text, hint = await self._run_handler()
        assert hint is not None
        assert hint.error_code == "missing_credential"
        assert "搜索源未配置" in hint.title
        # actions include "前往配置" + signup links
        labels = [a.get("label", "") for a in hint.actions]
        assert any("前往配置" in lb for lb in labels)
        assert any("博查" in lb for lb in labels)  # signup link present

    async def test_auth_failed_maps_to_auth_failed(self) -> None:
        register(_FakeProvider("p1", raise_on_search=AuthFailedError("bad key")))
        text, hint = await self._run_handler()
        assert hint is not None
        assert hint.error_code == "auth_failed"

    async def test_network_unreachable_propagates_network_code(self) -> None:
        # All providers fail with NetworkUnreachableError; runtime falls back
        # through them (per the revised _FALLBACK_ERRORS list) and ends with
        # NoProviderAvailable(error_code="network_unreachable").
        register(_FakeProvider("p1", order=1, raise_on_search=NetworkUnreachableError("dns")))
        register(_FakeProvider("p2", order=2, raise_on_search=NetworkUnreachableError("tls")))
        text, hint = await self._run_handler()
        assert hint is not None
        assert hint.error_code == "network_unreachable"

    async def test_rate_limited_propagates(self) -> None:
        register(_FakeProvider("p1", raise_on_search=RateLimitedError("429")))
        text, hint = await self._run_handler()
        assert hint is not None
        assert hint.error_code == "rate_limited"

    async def test_content_filter_does_not_fallback(self) -> None:
        # ContentFilter is NOT in _FALLBACK_ERRORS — it should propagate to
        # the handler immediately, which maps to ToolConfigError(content_filter).
        register(_FakeProvider("p1", order=1, raise_on_search=ContentFilterError("bad query")))
        register(_FakeProvider("p2", order=2, web_results=[
            SearchResult(title="t", url="u", snippet="s")
        ]))
        text, hint = await self._run_handler()
        assert hint is not None
        assert hint.error_code == "content_filter"

    async def test_success_returns_text_only(self) -> None:
        register(_FakeProvider("p1", web_results=[
            SearchResult(title="Hello", url="https://x", snippet="World")
        ]))
        text, hint = await self._run_handler()
        assert hint is None
        assert "Hello" in text
        assert "https://x" in text


# ---------------------------------------------------------------------------
# 3. Registry / runtime auto-detect semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRuntimeAutoDetect:
    @pytest.fixture(autouse=True)
    def _isolate_registry(self, monkeypatch: pytest.MonkeyPatch):
        from openakita.tools.web_search import registry as reg

        monkeypatch.setattr(reg, "_PROVIDERS", {}, raising=False)
        monkeypatch.setattr(reg, "_LOADED", True, raising=False)
        yield

    async def test_falls_back_through_credential_errors(self) -> None:
        register(_FakeProvider("p1", order=1, raise_on_search=MissingCredentialError("nope")))
        register(_FakeProvider("p2", order=2, web_results=[
            SearchResult(title="ok", url="u", snippet="s")
        ]))
        bundle = await run_web_search("q", timeout_seconds=1)
        assert bundle.provider_id == "p2"
        assert len(bundle.results) == 1

    async def test_falls_back_through_network_errors(self) -> None:
        # New behavior (post-revision): NetworkUnreachable also triggers fallback.
        register(_FakeProvider("p1", order=1, raise_on_search=NetworkUnreachableError("x")))
        register(_FakeProvider("p2", order=2, web_results=[
            SearchResult(title="ok", url="u", snippet="s")
        ]))
        bundle = await run_web_search("q", timeout_seconds=1)
        assert bundle.provider_id == "p2"

    async def test_no_provider_available_when_registry_empty(self) -> None:
        with pytest.raises(NoProviderAvailable) as ei:
            await run_web_search("q", timeout_seconds=1)
        assert ei.value.error_code == "missing_credential"

    async def test_explicit_provider_does_not_fallback(self) -> None:
        register(_FakeProvider("p1", raise_on_search=NetworkUnreachableError("oops")))
        with pytest.raises(NetworkUnreachableError):
            await run_web_search("q", provider_id="p1", timeout_seconds=1)

    async def test_news_skips_providers_returning_none(self) -> None:
        # p1 returns None (no news), p2 returns results
        register(_FakeProvider("p1", order=1, news_results=None))
        register(_FakeProvider("p2", order=2, news_results=[
            SearchResult(title="news", url="u", snippet="s")
        ]))
        bundle = await run_news_search("q", timeout_seconds=1)
        assert bundle.provider_id == "p2"


# ---------------------------------------------------------------------------
# 4. ToolExecutor catches ToolConfigError, returns (text, hint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestToolExecutorPropagation:
    async def test_execute_tool_returns_tuple(self) -> None:
        from openakita.core.permission import PermissionDecision
        from openakita.core.tool_executor import ToolExecutor

        registry = MagicMock()
        registry.has_tool.return_value = True
        registry.execute_by_tool = AsyncMock(return_value="hello")
        registry.get_handler_name_for_tool.return_value = "test"
        registry.get_permission_check.return_value = None

        executor = ToolExecutor(handler_registry=registry, max_parallel=1)
        # Bypass v2 policy CONFIRM for unknown tools.
        executor.check_permission = MagicMock(return_value=PermissionDecision("allow"))
        text, hint = await executor.execute_tool("any_tool", {})
        assert text == "hello"
        assert hint is None

    async def test_executor_catches_tool_config_error(self) -> None:
        from openakita.core.permission import PermissionDecision
        from openakita.core.tool_executor import ToolExecutor

        async def _raise(_name, _input):
            raise ToolConfigError(
                scope="web_search",
                error_code="missing_credential",
                title="未配置",
                message="去设置吧",
                actions=[{"id": "open_settings", "label": "去"}],
            )

        registry = MagicMock()
        registry.has_tool.return_value = True
        registry.execute_by_tool = _raise
        registry.get_handler_name_for_tool.return_value = "web_search"
        registry.get_permission_check.return_value = None

        executor = ToolExecutor(handler_registry=registry, max_parallel=1)
        executor.check_permission = MagicMock(return_value=PermissionDecision("allow"))
        text, hint = await executor.execute_tool("web_search", {"query": "x"})
        assert hint is not None
        assert hint.scope == "web_search"
        assert hint.error_code == "missing_credential"
        # LLM-facing text is the natural-language summary, no markers
        assert text == "[未配置] 去设置吧"
        assert "<" not in text and "{" not in text

    async def test_execute_batch_attaches_hint_field(self) -> None:
        from openakita.core.tool_executor import ToolExecutor

        async def _raise(_name, _input):
            raise ToolConfigError(
                scope="web_search",
                error_code="auth_failed",
                title="bad key",
                message="check it",
            )

        registry = MagicMock()
        registry.has_tool.return_value = True
        registry.execute_by_tool = _raise
        registry.get_handler_name_for_tool.return_value = "web_search"
        registry.get_permission_check.return_value = None

        executor = ToolExecutor(handler_registry=registry, max_parallel=1)
        # bypass policy
        from openakita.core.permission import PermissionDecision
        executor.check_permission = MagicMock(return_value=PermissionDecision("allow"))

        results, _, _ = await executor.execute_batch([
            {"id": "tool-1", "name": "web_search", "input": {"query": "x"}}
        ])
        assert len(results) == 1
        tr = results[0]
        # _hint MUST be present in the dict for ReasoningEngine to pop & emit
        assert "_hint" in tr
        assert isinstance(tr["_hint"], ConfigHint)
        assert tr["_hint"].error_code == "auth_failed"


# ---------------------------------------------------------------------------
# 5. reasoning_engine helper builds the right SSE event sequence
# ---------------------------------------------------------------------------


class TestReasoningEngineHelper:
    def test_builds_two_events_when_hint_present(self) -> None:
        from openakita.core.reasoning_engine import _build_tool_end_events

        h = ConfigHint(
            scope="web_search",
            error_code="missing_credential",
            title="t",
            message="m",
            actions=[{"id": "a", "label": "L"}],
        )
        evts = _build_tool_end_events(
            tool_name="web_search",
            tool_id="tool_1",
            result_text="[t] m",
            hint=h,
            is_error=True,
        )
        assert len(evts) == 2
        assert evts[0]["type"] == "tool_call_end"
        assert evts[0]["id"] == "tool_1"
        assert evts[0]["is_error"] is True
        assert evts[1]["type"] == "config_hint"
        assert evts[1]["tool_use_id"] == "tool_1"
        assert evts[1]["error_code"] == "missing_credential"
        assert evts[1]["actions"] == [{"id": "a", "label": "L"}]

    def test_builds_one_event_when_hint_none(self) -> None:
        from openakita.core.reasoning_engine import _build_tool_end_events

        evts = _build_tool_end_events(
            tool_name="read_file",
            tool_id="t",
            result_text="ok",
            hint=None,
            is_error=False,
        )
        assert len(evts) == 1
        assert evts[0]["type"] == "tool_call_end"

    def test_extra_kwarg_merges_into_tool_call_end(self) -> None:
        from openakita.core.reasoning_engine import _build_tool_end_events

        evts = _build_tool_end_events(
            tool_name="x",
            tool_id="t",
            result_text="r",
            hint=None,
            is_error=False,
            extra={"skipped": True},
        )
        assert evts[0]["skipped"] is True


# ---------------------------------------------------------------------------
# 6. orgs/runtime monkey-patch preserves the tuple contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orgs_runtime_patch_returns_tuple_for_org_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The org_* shortcut path must return (text, None), and the original
    call path must forward the (text, hint) tuple unchanged."""
    from types import SimpleNamespace

    from openakita.orgs.runtime import OrgRuntime

    # Build a stub agent + executor sufficient for _register_org_tool_handler
    captured: dict[str, Any] = {}

    async def _original_with_policy(tool_name, tool_input, policy_result, *, session_id=None):
        captured["called"] = (tool_name, tool_input, session_id)
        # Simulate a handler that raised ToolConfigError → executor returns tuple
        h = ConfigHint(scope="web_search", error_code="auth_failed", title="t", message="m")
        return ("[t] m", h)

    fake_executor = SimpleNamespace(execute_tool_with_policy=_original_with_policy)
    fake_engine = SimpleNamespace(_tool_executor=fake_executor)
    fake_agent = SimpleNamespace(reasoning_engine=fake_engine, _org_context={})

    runtime = OrgRuntime.__new__(OrgRuntime)
    runtime._node_last_activity = {}
    runtime._tool_handler = SimpleNamespace(
        handle=AsyncMock(return_value="org_tool_text_result"),
        _bridge_plan_to_task=lambda *a, **k: None,
    )
    runtime._is_plugin_tool = lambda agent, name: False
    runtime._touch_trackers_for_org = lambda org_id: None
    runtime._broadcast_ws = AsyncMock(return_value=None)
    runtime.get_event_store = lambda org_id: SimpleNamespace(emit=lambda *a, **k: None)
    runtime._record_file_output = lambda *a, **k: None

    runtime._register_org_tool_handler(fake_agent, "org-1", "node-1")
    patched = fake_executor.execute_tool_with_policy

    # 1) org_* path → (text, None)
    text, hint = await patched("org_get_status", {}, None)
    assert text == "org_tool_text_result"
    assert hint is None

    # 2) regular tool path → forward original tuple incl. hint
    text2, hint2 = await patched("web_search", {"query": "x"}, None)
    assert text2 == "[t] m"
    assert isinstance(hint2, ConfigHint)
    assert hint2.error_code == "auth_failed"
    assert captured["called"][0] == "web_search"


# ---------------------------------------------------------------------------
# 7. LLM converter drops the _hint field
# ---------------------------------------------------------------------------


def test_llm_converter_drops_hint_field() -> None:
    """``convert_tool_result_from_openai`` only reads tool_use_id+content;
    inversely, when we hand-build a tool_result dict for the OpenAI
    converter, the ``_hint`` key MUST not surface in the produced ``tool``
    message (whose only fields are role/tool_call_id/content)."""
    from openakita.llm.converters.tools import convert_tool_result_to_openai

    # Simulate the dict reasoning_engine produces (with _hint after pop should not happen)
    msg = convert_tool_result_to_openai("call_1", "content text")
    assert set(msg.keys()) == {"role", "tool_call_id", "content"}
    assert msg["role"] == "tool"
    # The function takes content as a string param, so hint payloads simply
    # have no path into the OpenAI message — extra dict keys at the call site
    # cannot leak. This is the structural reason the side-channel is safe.


# ---------------------------------------------------------------------------
# 8. /api/tools/web-search endpoints
# ---------------------------------------------------------------------------


def test_providers_endpoint_lists_all() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openakita.api.routes.web_search import router

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.get("/api/tools/web-search/providers")
    assert r.status_code == 200
    data = r.json()
    ids = {p["id"] for p in data["providers"]}
    assert {"bocha", "tavily", "searxng", "jina", "duckduckgo"}.issubset(ids)
    # Each entry has the contract fields the panel uses
    for p in data["providers"]:
        for k in ("id", "label", "requires_credential", "is_available", "auto_detect_order"):
            assert k in p


def test_test_endpoint_unknown_provider_returns_structured_error() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openakita.api.routes.web_search import router

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.post(
        "/api/tools/web-search/test",
        json={"provider_id": "no_such_provider", "query": "x"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["error_code"] == "missing_credential"
    assert "no_such_provider" in data["message"]
