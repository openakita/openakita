"""Web Search 处理器（Provider Registry 版）.

调用链：
    Agent ─ web_search → WebSearchHandler.handle
                            ↓
                          web_search/runtime.run_web_search
                            ↓
                          web_search/providers/{bocha,tavily,searxng,jina,duckduckgo}

错误约定：
    - 配置/认证类失败 → raise :class:`ToolConfigError` (被 ToolExecutor 转成 SSE config_hint)
    - 临时性失败（超时、单次网络错） → 返回普通错误字符串让模型决定是否换关键词
    - 内容质量问题 → 复用既有 _filter_search_results 黑名单过滤

# ApprovalClass checklist (新增 / 修改工具时必读)
# 1. 在本文件 Handler 类的 TOOLS 列表加新工具名
# 2. 在同 Handler 类的 TOOL_CLASSES 字典加 ApprovalClass 显式声明
#    （或在 agent.py:_init_handlers 的 register() 调用里加 tool_classes={...}）
# 3. 行为依赖参数 → 在 policy_v2/classifier.py:_refine_with_params 加分支
# 4. 跑 pytest tests/unit/test_classifier_completeness.py 验证
# 详见 docs/policy_v2_research.md §4.21
"""

from __future__ import annotations

import logging
import re
import traceback
from typing import Any

from ...config import settings
from ...core.policy_v2 import ApprovalClass
from ..tool_hints import ConfigHintErrorCode, ToolConfigError
from ..web_search import (
    AuthFailedError,
    ContentFilterError,
    NetworkUnreachableError,
    NoProviderAvailable,
    RateLimitedError,
    SearchResult,
    run_news_search,
    run_web_search,
)

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


def _result_blob(r: SearchResult) -> str:
    return " ".join(
        [r.title or "", r.url or "", r.snippet or "", r.source or ""]
    )


def _is_unsafe_search_result(r: SearchResult) -> bool:
    """Return True only for obviously unsafe/spammy snippets.

    Keep this intentionally narrow: the goal is to prevent polluted search output
    from tripping upstream content filters, not to decide what users may search.
    """
    text = _result_blob(r).lower()
    if not text:
        return False
    if _UNSAFE_DOMAIN_RE.search(text):
        return True
    return any(keyword in text for keyword in _UNSAFE_SEARCH_KEYWORDS)


def _filter_search_results(results: list[SearchResult]) -> tuple[list[SearchResult], int]:
    filtered = [r for r in results if not _is_unsafe_search_result(r)]
    return filtered, len(results) - len(filtered)


def _resolve_attempt_timeout(params: dict[str, Any]) -> float:
    """Per-attempt wait budget for a search source.

    Soft wait budget, not a task-level failure policy: if the upstream search
    source is slow, the tool returns guidance so the model can continue with
    other sources or partial evidence.
    """
    raw = params.get("timeout_seconds", settings.web_search_attempt_timeout_seconds)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, float(settings.web_search_attempt_timeout_seconds or 0))


def _format_search_timeout(kind: str, timeout_seconds: float) -> str:
    label = "新闻搜索" if kind == "news" else "网页搜索"
    timeout_display = f"{timeout_seconds:g}"
    return (
        f"{label}本次等待超过 {timeout_display} 秒，已先跳过这个外部搜索源。"
        "这不代表任务失败：请优先基于已获得的信息继续完成用户目标；"
        "如果证据不足，可以换更具体的关键词、改用 web_fetch/browser 访问权威来源，"
        "或在结果中标注哪些内容尚未联网验证。不要反复用完全相同的查询空转。"
    )


# ---- Provider 错误 → 用户文案映射 ---------------------------------------------------

_TITLE_BY_CODE: dict[ConfigHintErrorCode, str] = {
    "missing_credential": "搜索源未配置",
    "auth_failed": "搜索源 API Key 无效",
    "rate_limited": "搜索源已限流",
    "network_unreachable": "搜索源无法访问",
    "content_filter": "搜索关键词被拒绝",
    "unknown": "搜索失败",
}

_MESSAGE_BY_CODE: dict[ConfigHintErrorCode, str] = {
    "missing_credential": (
        "当前没有可用的搜索源（DuckDuckGo 在国内常无法访问）。"
        "请在 OpenAkita 桌面端配置博查 / Tavily / SearXNG 等替代源。"
    ),
    "auth_failed": "当前激活的搜索源拒绝了 API Key（401/403）。请在设置中更新 Key。",
    "rate_limited": "当前搜索源已触发限流。请稍后重试，或切换到其他搜索源。",
    "network_unreachable": (
        "当前搜索源无法访问（网络/代理问题）。请检查网络连接，或切换到其他搜索源。"
    ),
    "content_filter": "搜索关键词被搜索源拒绝。请换更具体或更中性的关键词重试。",
    "unknown": "搜索失败。请稍后重试，或在设置中检查搜索源配置。",
}


def _actions_for(code: ConfigHintErrorCode) -> list[dict[str, Any]]:
    """Build the action button list for a given error code.

    Always includes "go to settings"; some codes get extra actions
    (申请 Key / 切换其他源 等).
    """
    actions: list[dict[str, Any]] = [
        {
            "id": "open_settings",
            "label": "前往配置搜索源",
            "view": "config",
            "section": "tools-and-skills",
            "anchor": "web-search",
        }
    ]
    if code == "missing_credential":
        actions.append(
            {
                "id": "signup_bocha",
                "label": "申请博查 Key（国内推荐）",
                "url": "https://api.bochaai.com",
            }
        )
        actions.append(
            {
                "id": "signup_tavily",
                "label": "申请 Tavily Key（海外推荐）",
                "url": "https://app.tavily.com/home",
            }
        )
    elif code == "auth_failed":
        actions.append(
            {
                "id": "update_key",
                "label": "更新 Key",
                "view": "config",
                "section": "tools-and-skills",
                "anchor": "web-search",
            }
        )
    return actions


def _raise_config_error(code: ConfigHintErrorCode, *, scope: str = "web_search") -> None:
    """Raise a ToolConfigError with the standard title/message/actions for ``code``."""
    raise ToolConfigError(
        scope=scope,
        error_code=code,
        title=_TITLE_BY_CODE.get(code, _TITLE_BY_CODE["unknown"]),
        message=_MESSAGE_BY_CODE.get(code, _MESSAGE_BY_CODE["unknown"]),
        actions=_actions_for(code),
    )


# ---- Handler ------------------------------------------------------------------------

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
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, int(params.get("max_results", 5))), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        provider_id = (params.get("provider") or settings.web_search_provider or "").strip() or None
        timeout_seconds = _resolve_attempt_timeout(params)

        try:
            bundle = await run_web_search(
                query=query,
                provider_id=provider_id,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timeout_seconds=timeout_seconds,
            )
        except NoProviderAvailable as exc:
            _raise_config_error(exc.error_code)
        except AuthFailedError:
            _raise_config_error("auth_failed")
        except RateLimitedError:
            # 单源限流：直接抛 ConfigHint 让用户/模型切换源
            _raise_config_error("rate_limited")
        except ContentFilterError:
            _raise_config_error("content_filter")
        except KeyError:
            # provider_id 显式给的但不存在
            return (
                f"错误：未知的搜索源 ID {provider_id!r}。"
                "请用 bocha / tavily / searxng / jina / duckduckgo，"
                "或在设置中查看已注册源。"
            )
        except NetworkUnreachableError as exc:
            # 单源网络问题：返回普通错误文本（让模型决定是否换关键词/换源）；
            # 但不再静默"没数据"的兜底——明确告诉用户/模型问题在哪里
            logger.warning("[web_search] %s", exc)
            return (
                f"网页搜索失败（{exc}）。当前搜索源无法访问，"
                "请告知用户检查网络/代理，或在设置中切换其他搜索源。"
                "请基于已有信息继续，不要反复用相同查询空转。"
            )
        except TimeoutError:
            logger.warning("[web_search] attempt timed out after %ss: %s", timeout_seconds, query)
            return _format_search_timeout("web", timeout_seconds)
        except Exception as exc:  # pragma: no cover - defensive
            tb = traceback.format_exc()
            logger.error("[web_search] unexpected error: %s\n%s", exc, tb)
            return (
                f"搜索时遇到未知错误：{type(exc).__name__}: {exc}。"
                "请告知用户当前无法联网搜索，建议稍后重试或改用其他工具，"
                "不要反复重试，也不要伪造搜索结果。"
            )

        return self._format_web_results(bundle.results, provider_id=bundle.provider_id)

    async def _news_search(self, params: dict[str, Any]) -> str:
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, int(params.get("max_results", 5))), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")
        provider_id = (params.get("provider") or settings.web_search_provider or "").strip() or None
        timeout_seconds = _resolve_attempt_timeout(params)

        try:
            bundle = await run_news_search(
                query=query,
                provider_id=provider_id,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
                timeout_seconds=timeout_seconds,
            )
        except NoProviderAvailable as exc:
            # news_search 比 web_search 更挑剔：当前内置 provider 中只有 ddg
            # 暴露 news 端点，因此 missing_credential 在国内场景实际上是
            # "ddg 不可达且没有别的能跑 news 的源"。给一个更明确的提示。
            if exc.error_code == "missing_credential":
                raise ToolConfigError(
                    scope="web_search",
                    error_code="missing_credential",
                    title="新闻搜索源不可用",
                    message=(
                        "当前内置搜索源中没有可用于新闻检索的源（DuckDuckGo 国内常不可达，"
                        "其他源暂不暴露独立 news 端点）。"
                        "请改用 web_search + 关键词限定时间，或配置 DuckDuckGo 可达的网络环境。"
                    ),
                    actions=_actions_for("missing_credential"),
                )
            _raise_config_error(exc.error_code)
        except AuthFailedError:
            _raise_config_error("auth_failed")
        except RateLimitedError:
            _raise_config_error("rate_limited")
        except ContentFilterError:
            _raise_config_error("content_filter")
        except KeyError:
            return (
                f"错误：未知的搜索源 ID {provider_id!r}。"
                "请用 bocha / tavily / searxng / jina / duckduckgo。"
            )
        except NetworkUnreachableError as exc:
            logger.warning("[news_search] %s", exc)
            return (
                f"新闻搜索失败（{exc}）。请告知用户检查网络/代理，"
                "或改用 web_search 配合关键词限定时间。"
            )
        except TimeoutError:
            logger.warning("[news_search] attempt timed out after %ss: %s", timeout_seconds, query)
            return _format_search_timeout("news", timeout_seconds)
        except Exception as exc:  # pragma: no cover - defensive
            tb = traceback.format_exc()
            logger.error("[news_search] unexpected error: %s\n%s", exc, tb)
            return (
                f"新闻搜索时遇到未知错误：{type(exc).__name__}: {exc}。"
                "请告知用户当前无法联网搜索新闻，不要反复重试。"
            )

        return self._format_news_results(bundle.results, provider_id=bundle.provider_id)

    @staticmethod
    def _format_web_results(results: list[SearchResult], *, provider_id: str = "") -> str:
        if not results:
            label = f" ({provider_id})" if provider_id else ""
            return f"未找到相关结果{label}"

        safe_results, hidden_count = _filter_search_results(results)
        if not safe_results:
            return (
                f"搜索返回了 {len(results)} 条结果，但结果内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据；"
                "如果当前确实没有可验证信息，请明确说明无法联网验证，不要编造结果。"
            )

        output = []
        if provider_id:
            output.append(f"[搜索源: {provider_id}]")
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条明显垃圾或可能触发平台安全审核的搜索结果。"
                "如果剩余结果不够相关，请换关键词或改用 web_fetch/browser 访问权威来源继续验证。"
            )
        for i, r in enumerate(safe_results, 1):
            output.append(f"**{i}. {r.title}**\n{r.url}\n{r.snippet}\n")

        return "\n".join(output)

    @staticmethod
    def _format_news_results(results: list[SearchResult], *, provider_id: str = "") -> str:
        if not results:
            label = f" ({provider_id})" if provider_id else ""
            return f"未找到相关新闻{label}"

        safe_results, hidden_count = _filter_search_results(results)
        if not safe_results:
            return (
                f"新闻搜索返回了 {len(results)} 条结果，但结果内容质量不可靠或可能触发平台安全审核，"
                "已隐藏。请换用更具体关键词、web_fetch、浏览器或权威来源继续获取证据；"
                "如果当前确实没有可验证信息，请明确说明无法联网验证，不要编造结果。"
            )

        output = []
        if provider_id:
            output.append(f"[搜索源: {provider_id}]")
        if hidden_count:
            output.append(
                f"[系统提示] 已隐藏 {hidden_count} 条明显垃圾或可能触发平台安全审核的新闻搜索结果。"
                "如果剩余结果不够相关，请换关键词或改用 web_fetch/browser 访问权威来源继续验证。"
            )
        for i, r in enumerate(safe_results, 1):
            header = f"**{i}. {r.title}**"
            if r.source or r.date:
                header += f" ({r.source} {r.date})".rstrip()
            output.append(f"{header}\n{r.url}\n{r.snippet}\n")

        return "\n".join(output)


def create_handler(agent: Any = None):
    """创建 WebSearchHandler 实例并返回 handle 方法"""
    handler = WebSearchHandler(agent)
    return handler.handle
