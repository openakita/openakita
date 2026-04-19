"""
Web Fetch Handler

Lightweight URL content fetching — no browser launched; directly fetches via HTTP,
extracts the main body text, and converts it to Markdown.
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
            return "❌ web_fetch missing required parameter 'url'."

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return f"❌ Invalid URL: {url} (a full URL with scheme prefix such as https:// is required)"

        from ...utils.url_safety import is_safe_url

        safe, reason = await is_safe_url(url)
        if not safe:
            return f"❌ URL safety check failed: {reason}. Use the browser tool to access local/intranet services."

        try:
            import httpx
        except ImportError:
            return "❌ web_fetch requires the httpx library. Please run: pip install httpx"

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
            return f"❌ HTTP {e.response.status_code} error: {url}"
        except httpx.TimeoutException:
            return f"❌ Request timed out (30s): {url}"
        except Exception as e:
            return f"❌ Request failed: {e}"

        content_type = response.headers.get("content-type", "")

        if any(t in content_type for t in ("image/", "audio/", "video/", "application/pdf")):
            return f"❌ web_fetch does not support binary content ({content_type}). Use the browser tool or download directly."

        html = response.text

        markdown = self._html_to_markdown(html, url)

        if len(markdown) > max_length:
            markdown = markdown[:max_length] + (
                f"\n\n[CONTENT_TRUNCATED] Content truncated to {max_length} characters. "
                "Increase max_length or use the browser tool for the full content."
            )

        if not markdown.strip():
            return (
                f"⚠️ Page content is empty or main text could not be extracted: {url}\n"
                "This may be a JavaScript-rendered page. Try the browser tool instead."
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
