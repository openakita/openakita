"""
Web Fetch 处理器

轻量 URL 内容获取 — 不启动浏览器，直接 HTTP 抓取并提取正文转 Markdown。
"""

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


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
            return "❌ web_fetch 缺少必要参数 'url'。"

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return f"❌ 无效 URL：{url}（需要完整 URL，包含 https:// 等协议前缀）"

        if parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return "❌ web_fetch 不支持 localhost/本地 IP。请使用浏览器工具访问本地服务。"

        try:
            import httpx
        except ImportError:
            return "❌ web_fetch 需要 httpx 库。请运行: pip install httpx"

        from ...llm.providers.proxy_utils import get_httpx_client_kwargs

        try:
            async with httpx.AsyncClient(
                **get_httpx_client_kwargs(timeout=30),
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; OpenAkita/1.0; "
                        "+https://github.com/openakita/openakita)"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            return f"❌ HTTP {e.response.status_code} 错误：{url}"
        except httpx.TimeoutException:
            return f"❌ 请求超时（30s）：{url}"
        except Exception as e:
            return f"❌ 请求失败：{e}"

        content_type = response.headers.get("content-type", "")

        if any(t in content_type for t in ("image/", "audio/", "video/", "application/pdf")):
            return f"❌ web_fetch 不支持二进制内容（{content_type}）。请使用浏览器工具或下载。"

        html = response.text

        markdown = self._html_to_markdown(html, url)

        if len(markdown) > max_length:
            markdown = markdown[:max_length] + (
                f"\n\n[CONTENT_TRUNCATED] 内容已截断至 {max_length} 字符。"
                "如需完整内容，增大 max_length 参数或使用浏览器工具。"
            )

        if not markdown.strip():
            return (
                f"⚠️ 页面内容为空或无法提取正文：{url}\n"
                "可能是 JavaScript 渲染的页面，请使用浏览器工具查看。"
            )

        return f"URL: {url}\n\n{markdown}"

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
