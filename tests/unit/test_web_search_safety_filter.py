import time

import pytest

from openakita.core.reasoning_engine import ReasoningEngine
from openakita.tools.handlers import web_search as web_search_module
from openakita.tools.handlers.web_search import WebSearchHandler


def test_web_search_hides_obviously_unsafe_results_but_keeps_safe_results():
    results = [
        {
            "title": "04月02日：未来三天全国天气预报",
            "href": "https://www.weather.com.cn/",
            "body": "中央气象台发布未来三天全国天气预报。",
        },
        {
            "title": "成人垃圾站",
            "href": "https://attach.noduown.com/category/mrds",
            "body": "网黄 裸聊 高潮 色情内容",
        },
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
        {
            "title": "adult spam",
            "href": "https://porn.example.com/x",
            "body": "xxx onlyfans",
        }
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
    def slow_search(**kwargs):
        time.sleep(0.05)
        return [{"title": "late", "href": "https://example.com", "body": "late"}]

    monkeypatch.setattr(web_search_module, "_sync_web_search", slow_search)

    result = await WebSearchHandler()._web_search(
        {"query": "slow source", "timeout_seconds": 0.01}
    )

    assert "不代表任务失败" in result
    assert "基于已获得的信息继续" in result
    assert "不要反复用完全相同的查询空转" in result
