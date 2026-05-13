"""
Web Search 处理器

直接使用 ddgs 库执行网络搜索，无需通过 MCP。
"""

import asyncio
import logging
import re
import traceback
from typing import Any

from ...config import settings
from ...core.policy_v2 import ApprovalClass

logger = logging.getLogger(__name__)


_UNSAFE_SEARCH_KEYWORDS = (
    "色情",
    "情色",
    "裸聊",
    "裸露",
    "约炮",
    "女优",
    "网黄",
    "无码视频",
    "无码",
    "强奸",
    "自慰",
    "阴茎",
    "阳具",
    "必撸",
    "porn",
    "xxx",
    "xvideo",
    "onlyfans",
)
_UNSAFE_DOMAIN_RE = re.compile(
    r"(?:^|\.)("
    r"porn|xvideos|xnxx|xhamster|onlyfans|jav|sex|adult|noduown"
    r")\.",
    re.IGNORECASE,
)


def _sync_web_search(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
) -> list[dict[str, Any]]:
    """在独立线程中执行同步的 ddgs 搜索（避免事件循环冲突）"""
    from ddgs import DDGS

    with DDGS() as ddgs:
        return ddgs.text(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
        )


def _sync_news_search(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
    timelimit: str | None,
) -> list[dict[str, Any]]:
    """在独立线程中执行同步的 ddgs 新闻搜索"""
    from ddgs import DDGS

    with DDGS() as ddgs:
        return ddgs.news(
            query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
        )


def _result_text(result: dict[str, Any]) -> str:
    return " ".join(
        str(result.get(key, "") or "")
        for key in ("title", "href", "link", "url", "body", "snippet", "excerpt", "source")
    )


def _is_unsafe_search_result(result: dict[str, Any]) -> bool:
    """Return True only for obviously unsafe/spammy snippets.

    Keep this intentionally narrow: the goal is to prevent polluted search output
    from tripping upstream content filters, not to decide what users may search.
    """
    text = _result_text(result).lower()
    if not text:
        return False
    if _UNSAFE_DOMAIN_RE.search(text):
        return True
    return any(keyword in text for keyword in _UNSAFE_SEARCH_KEYWORDS)


def _filter_search_results(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    filtered = [r for r in results if not _is_unsafe_search_result(r)]
    return filtered, len(results) - len(filtered)


def _resolve_attempt_timeout(params: dict[str, Any]) -> float:
    """Return the per-attempt wait budget for a search source.

    This is intentionally a soft wait budget, not a task-level failure policy:
    if the upstream search source is slow, the tool returns guidance so the
    model can continue with other sources or partial evidence.
    """
    raw = params.get("timeout_seconds", settings.web_search_attempt_timeout_seconds)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, float(settings.web_search_attempt_timeout_seconds or 0))


async def _run_search_attempt(func, *, timeout_seconds: float, **kwargs) -> list[dict[str, Any]]:
    task = asyncio.to_thread(func, **kwargs)
    if timeout_seconds <= 0:
        return await task
    return await asyncio.wait_for(task, timeout=timeout_seconds)


def _format_search_timeout(kind: str, timeout_seconds: float) -> str:
    label = "新闻搜索" if kind == "news" else "网页搜索"
    timeout_display = f"{timeout_seconds:g}"
    return (
        f"{label}本次等待超过 {timeout_display} 秒，已先跳过这个外部搜索源。"
        "这不代表任务失败：请优先基于已获得的信息继续完成用户目标；"
        "如果证据不足，可以换更具体的关键词、改用 web_fetch/browser 访问权威来源，"
        "或在结果中标注哪些内容尚未联网验证。不要反复用完全相同的查询空转。"
    )


class WebSearchHandler:
    """Web Search 处理器"""

    TOOLS = ["web_search", "news_search"]
    TOOL_CLASSES = {
        "web_search": ApprovalClass.NETWORK_OUT,
        "news_search": ApprovalClass.NETWORK_OUT,
    }

    def __init__(self, agent: Any = None):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "web_search":
            return await self._web_search(params)
        elif tool_name == "news_search":
            return await self._news_search(params)
        else:
            return f"Unknown web search tool: {tool_name}"

    async def _web_search(self, params: dict[str, Any]) -> str:
        """搜索网页"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")

        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            return f"错误：{import_or_hint('ddgs')}"

        try:
            timeout_seconds = _resolve_attempt_timeout(params)
            results = await _run_search_attempt(
                _sync_web_search,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
            )
            return self._format_web_results(results)
        except TimeoutError:
            logger.warning("Web search attempt timed out after %ss: %s", timeout_seconds, query)
            return _format_search_timeout("web", timeout_seconds)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Web search failed: {type(e).__name__}: {e}\n{tb}")
            return (
                "搜索暂时不可用（网络无法访问 DuckDuckGo）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具，"
                "不要反复重试，也不要伪造搜索结果。"
            )

    async def _news_search(self, params: dict[str, Any]) -> str:
        """搜索新闻"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")

        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from openakita.tools._import_helper import import_or_hint

            return f"错误：{import_or_hint('ddgs')}"

        try:
            timeout_seconds = _resolve_attempt_timeout(params)
            results = await _run_search_attempt(
                _sync_news_search,
                timeout_seconds=timeout_seconds,
                query=query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
            )
            return self._format_news_results(results)
        except TimeoutError:
            logger.warning("News search attempt timed out after %ss: %s", timeout_seconds, query)
            return _format_search_timeout("news", timeout_seconds)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"News search failed: {type(e).__name__}: {e}\n{tb}")
            return (
                "新闻搜索暂时不可用（网络无法访问 DuckDuckGo）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具，"
                "不要反复重试，也不要伪造搜索结果。"
            )

    @staticmethod
    def _format_web_results(results: list) -> str:
        """格式化网页搜索结果"""
        if not results:
            return "未找到相关结果"

        safe_results, hidden_count = _filter_search_results(results)
        if not safe_results:
            return (
                f"搜索返回了 {len(results)} 条结果，但结果内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据；"
                "如果当前确实没有可验证信息，请明确说明无法联网验证，不要编造结果。"
            )

        output = []
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条明显垃圾或可能触发平台安全审核的搜索结果。"
                "如果剩余结果不够相关，请换关键词或改用 web_fetch/browser 访问权威来源继续验证。"
            )
        for i, r in enumerate(safe_results, 1):
            title = r.get("title", "无标题")
            url = r.get("href", r.get("link", ""))
            body = r.get("body", r.get("snippet", ""))
            output.append(f"**{i}. {title}**\n{url}\n{body}\n")

        return "\n".join(output)

    @staticmethod
    def _format_news_results(results: list) -> str:
        """格式化新闻搜索结果"""
        if not results:
            return "未找到相关新闻"

        safe_results, hidden_count = _filter_search_results(results)
        if not safe_results:
            return (
                f"新闻搜索返回了 {len(results)} 条结果，但结果内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据；"
                "如果当前确实没有可验证信息，请明确说明无法联网验证，不要编造结果。"
            )

        output = []
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条明显垃圾或可能触发平台安全审核的新闻搜索结果。"
                "如果剩余结果不够相关，请换关键词或改用 web_fetch/browser 访问权威来源继续验证。"
            )
        for i, r in enumerate(safe_results, 1):
            title = r.get("title", "无标题")
            url = r.get("url", r.get("link", ""))
            body = r.get("body", r.get("excerpt", ""))
            date = r.get("date", "")
            source = r.get("source", "")

            header = f"**{i}. {title}**"
            if source or date:
                header += f" ({source} {date})"

            output.append(f"{header}\n{url}\n{body}\n")

        return "\n".join(output)


def create_handler(agent: Any = None):
    """创建 WebSearchHandler 实例并返回 handle 方法"""
    handler = WebSearchHandler(agent)
    return handler.handle
