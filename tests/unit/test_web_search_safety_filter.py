import pytest

from openakita.core.reasoning_engine import ReasoningEngine
from openakita.tools.handlers.web_search import WebSearchHandler
from openakita.tools.tool_hints import ToolConfigError
from openakita.tools.web_search.base import (
    NetworkUnreachableError,
    SearchBundle,
    SearchResult,
)
from openakita.tools.web_search import runtime as web_search_runtime


def test_web_search_hides_obviously_unsafe_results_but_keeps_safe_results():
    # Phase 1 contract: ``_format_web_results`` consumes ``SearchResult``
    # dataclasses, not raw provider dicts. Each provider does its own
    # dict→SearchResult mapping (see e.g. duckduckgo._to_result).
    results = [
        SearchResult(
            title="04月02日：未来三天全国天气预报",
            url="https://www.weather.com.cn/",
            snippet="中央气象台发布未来三天全国天气预报。",
        ),
        SearchResult(
            title="成人垃圾站",
            url="https://attach.noduown.com/category/mrds",
            snippet="网黄 裸聊 高潮 色情内容",
        ),
    ]

    formatted = WebSearchHandler._format_web_results(results)

    assert "中央气象台" in formatted
    assert "weather.com.cn" in formatted
    assert "noduown" not in formatted
    assert "网黄" not in formatted
    assert "已隐藏 1 条" in formatted
    assert "权威来源继续验证" in formatted


def test_web_search_all_unsafe_results_returns_actionable_fallback():
    results = [
        SearchResult(
            title="adult spam",
            url="https://porn.example.com/x",
            snippet="xxx onlyfans",
        )
    ]

    formatted = WebSearchHandler._format_web_results(results)

    assert "porn.example.com" not in formatted
    assert "已隐藏" in formatted
    assert "web_fetch" in formatted
    assert "不要编造结果" in formatted


def test_content_safety_placeholder_keeps_agent_on_evidence_path():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call-1", "name": "web_search", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "unsafe search output",
                }
            ],
        },
    ]

    cleaned, did_clean = ReasoningEngine._strip_tool_results_for_content_safety(messages)
    placeholder = cleaned[-1]["content"][0]["content"]

    assert did_clean is True
    assert "不要基于被移除的内容下结论" in placeholder
    assert "web_fetch" in placeholder
    assert "浏览器" in placeholder
    assert "不要编造结果" in placeholder
    assert "直接基于已有信息回答" not in placeholder


@pytest.mark.asyncio
async def test_web_search_attempt_timeout_is_soft_guidance(monkeypatch):
    """A single provider raising NetworkUnreachableError on an explicit pick
    should yield soft, non-fatal guidance — not a hard task failure.

    Phase 1 changed the architecture: providers now own their own timeout
    handling and wrap timeouts as ``NetworkUnreachableError``. The handler
    catches that and returns guidance text encouraging the model to
    continue with partial evidence rather than spin retries.
    """
    async def fake_run_web_search(**kwargs):
        raise NetworkUnreachableError(
            "stub provider unreachable (simulated timeout)",
            provider_id="stub",
        )

    monkeypatch.setattr(web_search_runtime, "run_web_search", fake_run_web_search)
    # The handler imports run_web_search by name at module load, so patch the
    # already-bound symbol there too.
    monkeypatch.setattr(
        "openakita.tools.handlers.web_search.run_web_search", fake_run_web_search
    )

    result = await WebSearchHandler()._web_search(
        {"query": "slow source", "provider": "stub", "timeout_seconds": 0.01}
    )

    # The post-refactor guidance lives in the NetworkUnreachableError handler
    # branch of ``_web_search`` — it tells the LLM to keep going on partial
    # evidence and avoid spinning identical queries.
    assert "网页搜索失败" in result
    assert "请基于已有信息继续" in result
    assert "不要反复用相同查询空转" in result


@pytest.mark.asyncio
async def test_web_search_no_provider_available_raises_config_hint(monkeypatch):
    """When auto-detect finds no usable provider, the handler must raise
    ``ToolConfigError`` (not return plain text) so the chat UI surfaces the
    structured ConfigHintCard instead of the LLM having to interpret prose."""
    from openakita.tools.web_search.base import NoProviderAvailable

    async def fake_run_web_search(**kwargs):
        raise NoProviderAvailable(
            "no provider available",
            error_code="missing_credential",
            attempted=[],
        )

    monkeypatch.setattr(
        "openakita.tools.handlers.web_search.run_web_search", fake_run_web_search
    )

    with pytest.raises(ToolConfigError) as excinfo:
        await WebSearchHandler()._web_search({"query": "anything"})

    assert excinfo.value.hint.error_code == "missing_credential"
    assert excinfo.value.hint.scope == "web_search"
    # Confirm the card carries an actionable "go to settings" entry so the
    # frontend ConfigHintCard navigation button has something to render.
    action_kinds = {a.get("view") for a in excinfo.value.hint.actions}
    assert "config" in action_kinds


def _ignore_unused_imports() -> None:
    # SearchBundle is referenced by import to keep the public surface explicit
    # for future tests; pyflakes would otherwise complain.
    _ = SearchBundle
