"""
Web Fetch 处理器

轻量 URL 内容获取 — 不启动浏览器，直接 HTTP 抓取并提取正文转 Markdown。
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 10
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_BINARY_CONTENT_MARKERS = ("image/", "audio/", "video/", "application/pdf")


@dataclass(slots=True)
class WebFetchMeta:
    requested_url: str
    final_url: str
    redirect_chain: list[str]
    content_type: str = ""
    status_code: int | None = None
    bytes: int = 0
    fetched_at: str = ""
    error_code: str | None = None
    hint: str | None = None
    from_cache: bool = False

    @property
    def redirected(self) -> bool:
        return self.final_url != self.requested_url or len(self.redirect_chain) > 1

    @property
    def hostname(self) -> str:
        try:
            return urlparse(self.final_url or self.requested_url).hostname or ""
        except Exception:
            return ""


def clear_web_fetch_cache() -> None:
    """Clear module-level web fetch caches.

    The current implementation does not cache fetched bodies yet, but exposing
    this lifecycle hook lets session reset code clear future URL/domain caches
    without another API change.
    """


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _source_marker(meta: WebFetchMeta, *, tool_name: str = "web_fetch") -> str:
    payload = {
        "tool_name": tool_name,
        "requested_url": meta.requested_url,
        "final_url": meta.final_url,
        "hostname": meta.hostname,
        "redirected": meta.redirected,
        "redirect_chain": meta.redirect_chain,
        "content_type": meta.content_type,
        "status_code": meta.status_code,
        "from_cache": meta.from_cache,
        "status": "error" if meta.error_code else "ok",
        "error_code": meta.error_code,
        "hint": meta.hint,
    }
    import json

    return f"[OPENAKITA_SOURCE] {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"


def _build_fetch_result_text(meta: WebFetchMeta, markdown: str) -> str:
    redirects = " -> ".join(meta.redirect_chain) if meta.redirect_chain else "none"
    return (
        f"{_source_marker(meta)}\n"
        f"Requested URL: {meta.requested_url}\n"
        f"Final URL: {meta.final_url}\n"
        f"Redirects: {redirects}\n"
        f"Content-Type: {meta.content_type or 'unknown'}\n"
        f"Status: {meta.status_code or 'unknown'}\n"
        f"Fetched At: {meta.fetched_at}\n\n"
        f"{markdown}"
    )


def _build_fetch_error_text(meta: WebFetchMeta) -> str:
    redirects = " -> ".join(meta.redirect_chain) if meta.redirect_chain else "none"
    hint = meta.hint or "请稍后重试，或改用浏览器工具查看。"
    return (
        f"{_source_marker(meta)}\n"
        f"❌ web_fetch 失败（{meta.error_code or 'network_error'}）。\n"
        f"Requested URL: {meta.requested_url}\n"
        f"Final URL: {meta.final_url}\n"
        f"Redirects: {redirects}\n"
        f"Content-Type: {meta.content_type or 'unknown'}\n"
        f"Status: {meta.status_code or 'unknown'}\n"
        f"Hint: {hint}"
    )


async def _fetch_with_redirects(
    client: Any,
    requested_url: str,
    *,
    max_redirects: int = _MAX_REDIRECTS,
) -> tuple[Any | None, WebFetchMeta]:
    """Follow redirects like a normal browser, record the chain for disclosure.

    Behaviour mirrors curl / browsers: every 30x is followed up to
    ``max_redirects`` regardless of host. The full chain is recorded in
    ``WebFetchMeta.redirect_chain`` so the UI can show "由 X 跳转" — that's
    the disclosure mechanism. SSRF / private-IP defence remains in
    ``is_safe_url`` (called before this helper).
    """
    current_url = requested_url
    chain = [requested_url]

    for _ in range(max_redirects + 1):
        response = await client.get(current_url)
        content_type = response.headers.get("content-type", "")
        meta = WebFetchMeta(
            requested_url=requested_url,
            final_url=current_url,
            redirect_chain=list(chain),
            content_type=content_type,
            status_code=response.status_code,
            bytes=len(response.content or b""),
            fetched_at=_utc_now_iso(),
        )

        if response.status_code not in _REDIRECT_STATUSES:
            return response, meta

        location = response.headers.get("location")
        if not location:
            meta.error_code = "redirect_missing_location"
            meta.hint = "目标站点返回了跳转状态，但没有提供 Location 头。"
            return None, meta

        current_url = urljoin(current_url, location)
        chain.append(current_url)

    return (
        None,
        WebFetchMeta(
            requested_url=requested_url,
            final_url=current_url,
            redirect_chain=chain,
            fetched_at=_utc_now_iso(),
            error_code="too_many_redirects",
            hint=f"跳转次数超过 {max_redirects} 次，可能是重定向循环。",
        ),
    )


class WebFetchHandler:
    TOOLS = ["web_fetch"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "web_fetch":
            return await self._web_fetch(params)
        return f"❌ Unknown web_fetch tool: {tool_name}"

    async def _web_fetch(self, params: dict) -> str:
        url = params.get("url", "").strip()
        max_length = params.get("max_length", 15000)

        if not url:
            meta = WebFetchMeta(
                requested_url="",
                final_url="",
                redirect_chain=[],
                fetched_at=_utc_now_iso(),
                error_code="invalid_url",
                hint="请提供完整 URL，例如 https://example.com/page。",
            )
            return _build_fetch_error_text(meta)

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            meta = WebFetchMeta(
                requested_url=url,
                final_url=url,
                redirect_chain=[url],
                fetched_at=_utc_now_iso(),
                error_code="invalid_url",
                hint="URL 需要包含协议和域名，例如 https://example.com/page。",
            )
            return _build_fetch_error_text(meta)

        from ...utils.url_safety import is_safe_url

        safe, reason = await is_safe_url(url)
        if not safe:
            meta = WebFetchMeta(
                requested_url=url,
                final_url=url,
                redirect_chain=[url],
                fetched_at=_utc_now_iso(),
                error_code="unsafe_url",
                hint=f"{reason}。请使用浏览器工具访问本地/内网服务。",
            )
            return _build_fetch_error_text(meta)

        conv_id = (
            getattr(self.agent, "_current_conversation_id", "")
            or getattr(self.agent, "_current_session_id", "")
            or ""
        )
        if conv_id:
            from ...core.domain_allowlist import get_domain_allowlist

            host = parsed.hostname or ""
            if get_domain_allowlist().decide(conv_id, host) == "deny":
                meta = WebFetchMeta(
                    requested_url=url,
                    final_url=url,
                    redirect_chain=[url],
                    fetched_at=_utc_now_iso(),
                    error_code="domain_blocked",
                    hint=(
                        f"用户在本会话已屏蔽 {host}。"
                        "如需重新读取，请到「状态 → 链接读取诊断」解除屏蔽。"
                    ),
                )
                return _build_fetch_error_text(meta)

        try:
            import httpx
        except ImportError:
            return "web_fetch 需要 httpx 库。请在设置中心修复 OpenAkita 运行环境，而不是安装到宿主 Python。"

        from ...llm.providers.proxy_utils import get_httpx_client_kwargs

        try:
            async with httpx.AsyncClient(
                **get_httpx_client_kwargs(timeout=30),
                follow_redirects=False,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; OpenAkita/1.0; "
                        "+https://github.com/openakita/openakita)"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            ) as client:
                response, meta = await _fetch_with_redirects(client, url)
                if response is None:
                    return _build_fetch_error_text(meta)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            meta = WebFetchMeta(
                requested_url=url,
                final_url=str(e.request.url) if e.request else url,
                redirect_chain=[url],
                content_type=e.response.headers.get("content-type", ""),
                status_code=e.response.status_code,
                bytes=len(e.response.content or b""),
                fetched_at=_utc_now_iso(),
                error_code="http_error",
                hint=f"目标服务器返回 HTTP {e.response.status_code}，可能需要登录、权限或稍后重试。",
            )
            return _build_fetch_error_text(meta)
        except httpx.TimeoutException:
            meta = WebFetchMeta(
                requested_url=url,
                final_url=url,
                redirect_chain=[url],
                fetched_at=_utc_now_iso(),
                error_code="timeout",
                hint="请求超过 30 秒没有完成。请稍后重试，或改用浏览器工具查看动态页面。",
            )
            return _build_fetch_error_text(meta)
        except Exception as e:
            meta = WebFetchMeta(
                requested_url=url,
                final_url=url,
                redirect_chain=[url],
                fetched_at=_utc_now_iso(),
                error_code="network_error",
                hint=f"网络请求失败：{e}",
            )
            return _build_fetch_error_text(meta)

        content_type = response.headers.get("content-type", "")
        meta.content_type = content_type
        meta.bytes = len(response.content or b"")
        meta.status_code = response.status_code

        if any(t in content_type for t in _BINARY_CONTENT_MARKERS):
            meta.error_code = "binary_content"
            meta.hint = (
                f"这个链接是 {content_type}，不是网页文本，所以无法用 web_fetch 直接读取。"
                "如果是 PDF 或图片，可以下载后让助手分析；或者改用浏览器工具打开。"
            )
            return _build_fetch_error_text(meta)

        html = response.text

        markdown = self._html_to_markdown(html, meta.final_url)

        if len(markdown) > max_length:
            markdown = markdown[:max_length] + (
                f"\n\n[CONTENT_TRUNCATED] 内容已截断至 {max_length} 字符。"
                "如需完整内容，增大 max_length 参数或使用浏览器工具。"
            )

        if not markdown.strip():
            meta.error_code = "empty_content"
            meta.hint = (
                "这个页面没有可直接读取的正文（多半是 JS 动态渲染的页面）。"
                "如果需要看到完整内容，可以让助手用浏览器工具打开它。"
            )
            return _build_fetch_error_text(meta)

        return _build_fetch_result_text(meta, markdown)

    @staticmethod
    def _html_to_markdown(html: str, url: str = "") -> str:
        """Extract main content from HTML and convert to readable markdown."""
        try:
            import trafilatura

            result = trafilatura.extract(
                html,
                include_links=True,
                include_tables=True,
                include_formatting=True,
                output_format="txt",
                url=url,
            )
            if result:
                return result
        except ImportError:
            pass

        try:
            from readability import Document

            doc = Document(html)
            title = doc.title()
            content_html = doc.summary()
            text = re.sub(r"<[^>]+>", " ", content_html)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return f"# {title}\n\n{text}" if title else text
        except ImportError:
            pass

        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&#\d+;", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


def create_handler(agent: "Agent"):
    handler = WebFetchHandler(agent)
    return handler.handle
