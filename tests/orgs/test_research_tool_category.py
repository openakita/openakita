from __future__ import annotations

from openakita.orgs.tool_categories import expand_tool_categories


def test_research_category_includes_url_fetch_tool() -> None:
    tools = expand_tool_categories(["research"])

    assert {"web_search", "news_search", "web_fetch"} <= tools
