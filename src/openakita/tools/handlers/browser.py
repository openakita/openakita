"""
Browser handler

Handles browser-related system skills (all based on Playwright):
- browser_open: Start browser + status query
- browser_navigate: Navigate to URL
- browser_click: Click page element
- browser_type: Type text
- browser_scroll: Scroll page
- browser_wait: Wait for element to appear
- browser_execute_js: Execute JavaScript
- browser_get_content: Get page content (supports max_length truncation)
- browser_screenshot: Capture page screenshot
- browser_list_tabs / browser_switch_tab / browser_new_tab: Tab management
- browser_close: Close browser
- view_image: View/analyze local image
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...agents.lock_manager import LockManager

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Cross-agent browser lock — shared by all BrowserHandler instances in this
# process. Serialises page-mutating operations so agents do not overwrite
# each other's page navigation.
_browser_lock_manager = LockManager()
_BROWSER_LOCK_TIMEOUT = 300.0  # seconds

# Operations that mutate page state or are long-running.
# Read-only helpers (get_content, screenshot, status, list_tabs, wait) are
# intentionally excluded to avoid blocking during page-mutating operations.
_LOCKED_BROWSER_OPS = frozenset(
    {
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_execute_js",
        "browser_new_tab",
        "browser_switch_tab",
        "browser_close",
    }
)


class BrowserHandler:
    """
    Browser handler

    Routes browser tool calls through BrowserManager / PlaywrightTools
    """

    TOOLS = [
        "browser_open",
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_wait",
        "browser_execute_js",
        "browser_get_content",
        "browser_screenshot",
        "browser_list_tabs",
        "browser_switch_tab",
        "browser_new_tab",
        "browser_close",
        "view_image",
    ]

    # Default maximum character count for browser_get_content
    CONTENT_DEFAULT_MAX_LENGTH = 32000

    def __init__(self, agent: "Agent"):
        self.agent = agent

    def _check_ready(self) -> str | None:
        """Check if browser components are initialized. Returns error message or None."""
        has_manager = hasattr(self.agent, "browser_manager") and self.agent.browser_manager
        if not has_manager:
            from openakita.runtime_env import IS_FROZEN

            if IS_FROZEN:
                return "❌ Browser service not started. Please try restarting the app; if the issue persists, check the logs."
            else:
                return "❌ Browser module not started. Please install: pip install playwright && playwright install chromium"
        return None

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str | list:
        """Handle tool calls; returns str or multimodal list (view_image/browser_screenshot)."""

        # view_image does not depend on the browser; handle it directly
        if tool_name == "view_image":
            return await self._handle_view_image(params)

        err = self._check_ready()
        if err:
            return err

        actual_tool_name = tool_name
        if "browser_" in tool_name and not tool_name.startswith("browser_"):
            match = re.search(r"(browser_\w+)", tool_name)
            if match:
                actual_tool_name = match.group(1)

        result = await self._dispatch_with_lock(actual_tool_name, params)

        if result.get("success"):
            output = f"✅ {result.get('result', 'OK')}"
        else:
            output = f"❌ {result.get('error', 'Unknown error')}"

        if actual_tool_name == "browser_get_content":
            output = self._maybe_truncate(output, params)

        # browser_screenshot: automatically attach image content (if the model supports vision)
        if actual_tool_name == "browser_screenshot" and result.get("success"):
            multimodal = self._try_embed_screenshot(result)
            if multimodal is not None:
                return multimodal

        return output

    async def _dispatch_with_lock(self, tool_name: str, params: dict[str, Any]) -> dict:
        """Acquire the cross-agent browser lock for page-mutating operations."""
        if tool_name not in _LOCKED_BROWSER_OPS:
            return await self._dispatch(tool_name, params)

        holder = getattr(self.agent, "name", "") or "agent"
        try:
            async with _browser_lock_manager.lock(
                "tool:browser",
                holder=holder,
                timeout=_BROWSER_LOCK_TIMEOUT,
            ):
                return await self._dispatch(tool_name, params)
        except (asyncio.TimeoutError, TimeoutError):
            current_holder = await _browser_lock_manager.get_holder("tool:browser")
            logger.warning(
                f"[Browser] Lock timeout for {tool_name} (holder={current_holder}, waiter={holder})"
            )
            return {
                "success": False,
                "error": (
                    f"Browser is being used by another Agent ({current_holder or 'unknown'}); "
                    f"waited {int(_BROWSER_LOCK_TIMEOUT)}s and timed out. Please try again later."
                ),
            }

    async def _dispatch(self, tool_name: str, params: dict[str, Any]) -> dict:
        """Route the tool call to the corresponding component."""
        manager = self.agent.browser_manager
        pw = self.agent.pw_tools

        try:
            if tool_name == "browser_open":
                return await self._handle_open(manager, params)
            elif tool_name == "browser_close":
                await manager.stop()
                return {"success": True, "result": "Browser closed"}
            elif tool_name == "browser_navigate":
                return await pw.navigate(params.get("url", ""))
            elif tool_name == "browser_screenshot":
                return await pw.screenshot(
                    full_page=params.get("full_page", False),
                    path=params.get("path"),
                )
            elif tool_name == "browser_get_content":
                return await pw.get_content(
                    selector=params.get("selector"),
                    format=params.get("format", "text"),
                )
            elif tool_name == "browser_click":
                return await pw.click(
                    selector=params.get("selector"),
                    text=params.get("text"),
                )
            elif tool_name == "browser_type":
                return await pw.type_text(
                    selector=params.get("selector", ""),
                    text=params.get("text", ""),
                    clear=params.get("clear", True),
                )
            elif tool_name == "browser_scroll":
                return await pw.scroll(
                    direction=params.get("direction", "down"),
                    amount=params.get("amount", 500),
                )
            elif tool_name == "browser_wait":
                return await pw.wait(
                    selector=params.get("selector"),
                    timeout=params.get("timeout", 30000),
                )
            elif tool_name == "browser_execute_js":
                return await pw.execute_js(params.get("script", ""))
            elif tool_name == "browser_status":
                status = await manager.get_status()
                return {"success": True, "result": status}
            elif tool_name == "browser_list_tabs":
                return await pw.list_tabs()
            elif tool_name == "browser_switch_tab":
                return await pw.switch_tab(params.get("index", 0))
            elif tool_name == "browser_new_tab":
                return await pw.new_tab(params.get("url", ""))
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            error_str = str(e)
            logger.error(f"Browser tool error: {e}")

            if "closed" in error_str.lower() or "target" in error_str.lower():
                logger.warning("[Browser] Browser/page closed detected, resetting state")
                await manager.reset_state()
                return {
                    "success": False,
                    "error": "Browser connection lost (may have been closed by the user).\n"
                    "[IMPORTANT] State has been reset; call browser_open directly to restart the browser — no need to call browser_close first.",
                }

            return {"success": False, "error": error_str}

    async def _handle_open(self, manager: Any, params: dict) -> dict:
        """Handle browser_open (combined with status-query functionality)."""
        visible = params.get("visible", True)

        if manager.is_ready and manager.context and manager.page:
            try:
                current_url = manager.page.url
                current_title = await manager.page.title()
                all_pages = manager.context.pages

                if visible != manager.visible:
                    logger.info(f"Browser mode change requested: visible={visible}, restarting...")
                    await manager.stop()
                else:
                    return {
                        "success": True,
                        "result": {
                            "is_open": True,
                            "status": "already_running",
                            "visible": manager.visible,
                            "tab_count": len(all_pages),
                            "current_tab": {"url": current_url, "title": current_title},
                            "using_user_chrome": manager.using_user_chrome,
                            "message": f"Browser already running in {'visible' if manager.visible else 'background'} mode, "
                            f"with {len(all_pages)} tabs",
                        },
                    }
            except Exception as e:
                logger.warning(f"[Browser] Browser connection lost: {e}, resetting state")
                await manager.reset_state()
        elif manager.is_ready:
            logger.warning("[Browser] Incomplete browser state, resetting")
            await manager.reset_state()

        success = await manager.start(visible=visible)

        if success:
            current_url = manager.page.url if manager.page else None
            current_title = None
            tab_count = 0
            try:
                if manager.page:
                    current_title = await manager.page.title()
                if manager.context:
                    tab_count = len(manager.context.pages)
            except Exception:
                pass

            result_data: dict[str, Any] = {
                "is_open": True,
                "status": "started",
                "visible": manager.visible,
                "tab_count": tab_count,
                "current_tab": {"url": current_url, "title": current_title},
                "using_user_chrome": manager.using_user_chrome,
                "message": f"Browser started ({'visible mode' if manager.visible else 'background mode'})",
            }

            try:
                from ..browser.chrome_finder import detect_chrome_devtools_mcp

                devtools_info = detect_chrome_devtools_mcp()
                if devtools_info["available"] and not manager.using_user_chrome:
                    result_data["hint"] = (
                        "Hint: Chrome DevTools MCP detected and available. To preserve login state, "
                        "you can use call_mcp_tool('chrome-devtools', ...)."
                    )
            except Exception:
                pass

            return {"success": True, "result": result_data}
        else:
            hints: list[str] = []
            try:
                from ..browser.chrome_finder import (
                    check_mcp_chrome_extension,
                    detect_chrome_devtools_mcp,
                )

                devtools_info = detect_chrome_devtools_mcp()
                if devtools_info["available"]:
                    hints.append(
                        "Alternative: Chrome DevTools MCP is available; you can control the browser via "
                        "call_mcp_tool('chrome-devtools', 'navigate_page', {url: '...'})."
                    )
                mcp_chrome_available = await check_mcp_chrome_extension()
                if mcp_chrome_available:
                    hints.append(
                        "Alternative: mcp-chrome extension is running; you can control the browser via "
                        "call_mcp_tool('chrome-browser', ...)."
                    )
            except Exception:
                pass

            from openakita.runtime_env import IS_FROZEN

            if IS_FROZEN:
                chrome_running_hint = ""
                try:
                    from ..browser.manager import BrowserManager

                    if BrowserManager._is_chrome_process_running():
                        chrome_running_hint = (
                            "Chrome browser is currently running, which may cause profile conflicts. "
                            "Please try closing Chrome and retrying, or use the built-in browser directly."
                        )
                except Exception:
                    pass
                error_msg = "❌ Failed to start the browser. " + (
                    chrome_running_hint
                    or "Browser components are bundled; please try restarting the app. "
                    "If the issue persists, check whether antivirus software is blocking Chromium from starting."
                )
            else:
                error_msg = (
                    "Failed to start the browser. Please install: pip install playwright && playwright install chromium"
                )
            if hints:
                error_msg += "\n\n" + "\n".join(hints)

            return {
                "success": False,
                "result": {"is_open": False, "status": "failed"},
                "error": error_msg,
            }

    def _maybe_truncate(self, output: str, params: dict) -> str:
        """Smart truncation for browser_get_content."""
        max_length = params.get("max_length", self.CONTENT_DEFAULT_MAX_LENGTH)
        try:
            max_length = max(1000, int(max_length))
        except (TypeError, ValueError):
            max_length = self.CONTENT_DEFAULT_MAX_LENGTH

        if len(output) > max_length:
            total_chars = len(output)
            from ...core.tool_executor import save_overflow

            overflow_path = save_overflow("browser_get_content", output)
            output = output[:max_length]
            output += (
                f"\n\n[OUTPUT_TRUNCATED] Page content is {total_chars} characters total; "
                f"showing first {max_length} characters.\n"
                f"Full content saved to: {overflow_path}\n"
                f'Use read_file(path="{overflow_path}", offset=1, limit=300) '
                f"to view the full content.\n"
                f'You can also use browser_get_content(selector="...") to narrow the query scope.'
            )

        return output

    # ── view_image / screenshot multimodal support ────────────

    def _model_supports_vision(self) -> bool:
        """Check whether the current LLM supports vision (image input)."""
        try:
            from ...llm.capabilities import get_provider_slug_from_base_url, infer_capabilities

            brain = getattr(self.agent, "brain", None)
            if not brain:
                return False
            model = getattr(brain, "model_name", "") or ""
            base_url = ""
            llm_client = getattr(brain, "_llm_client", None)
            if llm_client:
                base_url = getattr(llm_client, "base_url", "") or ""
            provider = get_provider_slug_from_base_url(base_url) if base_url else None
            caps = infer_capabilities(model, provider)
            return caps.get("vision", False)
        except Exception:
            return False

    @staticmethod
    def _load_image_as_base64(path_str: str) -> tuple[str, str, int, int] | None:
        """Read an image file, compress it to a safe size, and encode as base64.

        Delegates to the shared preprocessing function in channels.media.image_prep
        to ensure the base64 output does not exceed the API payload limit.

        Returns:
            (base64_data, media_type, width, height), or None on failure.
        """
        p = Path(path_str)
        if not p.is_file():
            return None
        if p.suffix.lower() not in _IMAGE_EXTENSIONS:
            return None

        from ...channels.media.image_prep import prepare_image_file_for_context

        return prepare_image_file_for_context(p)

    async def _handle_view_image(self, params: dict[str, Any]) -> str | list:
        """view_image tool handler: load an image and return a multimodal tool result. Supports local paths and HTTP(S) URLs."""
        path_str = params.get("path", "")
        question = params.get("question", "")

        if not path_str:
            return "❌ view_image is missing required parameter 'path'."

        # HTTP(S) URL → download to a temp file and handle as a local file
        if path_str.startswith(("http://", "https://")):
            loaded = await self._download_and_load_image(path_str)
            if loaded is None:
                return f"❌ Unable to load image: {path_str} (file does not exist or format is not supported)"
            b64_data, media_type, w, h = loaded
            return await self._build_view_image_result(
                path_str,
                b64_data,
                media_type,
                w,
                h,
                question,
            )

        p = Path(path_str)
        if not p.is_file():
            return f"❌ Unable to read image: {path_str} (file does not exist)"
        if p.suffix.lower() not in _IMAGE_EXTENSIONS:
            return (
                f"❌ Unsupported image format: {p.suffix}\n"
                f"Supported formats: {', '.join(sorted(_IMAGE_EXTENSIONS))}"
            )
        loaded = self._load_image_as_base64(path_str)
        if loaded is None:
            return (
                f"❌ Image too large to embed in context: {path_str}\n"
                f"File size: {p.stat().st_size / 1024:.0f}KB. "
                f"Please install Pillow (pip install Pillow) to enable automatic compression, "
                f"or use a smaller image."
            )

        b64_data, media_type, w, h = loaded
        return await self._build_view_image_result(
            path_str,
            b64_data,
            media_type,
            w,
            h,
            question,
        )

    async def _build_view_image_result(
        self,
        path_str: str,
        b64_data: str,
        media_type: str,
        w: int,
        h: int,
        question: str,
    ) -> str | list:
        """Build the view_image result based on the model's vision capability."""
        if self._model_supports_vision():
            content: list[dict] = [
                {"type": "text", "text": f"✅ Image loaded: {path_str} ({w}x{h})"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
                },
            ]
            if question:
                content.append({"type": "text", "text": f"Please answer: {question}"})
            return content

        description = await self._describe_image_with_vl(b64_data, media_type, question)
        return f"✅ Image: {path_str} ({w}x{h})\n\n{description}"

    @staticmethod
    async def _download_and_load_image(url: str) -> tuple[str, str, int, int] | None:
        """Download an HTTP(S) image to a temp file and load it as base64."""
        import tempfile

        try:
            import httpx
        except ImportError:
            try:
                import urllib.request

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    urllib.request.urlretrieve(url, tmp.name)
                    tmp_path = tmp.name
            except Exception:
                return None
        else:
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        return None
                    content_type = resp.headers.get("content-type", "")
                    if not content_type.startswith("image/"):
                        return None
                    ext = {
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/gif": ".gif",
                        "image/webp": ".webp",
                    }.get(content_type.split(";")[0].strip(), ".png")
                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                        tmp.write(resp.content)
                        tmp_path = tmp.name
            except Exception:
                return None

        try:
            from ...channels.media.image_prep import prepare_image_file_for_context

            result = prepare_image_file_for_context(Path(tmp_path))
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
        return result

    async def _describe_image_with_vl(
        self,
        b64_data: str,
        media_type: str,
        question: str = "",
    ) -> str:
        """Use a VL model to generate a textual description of the image (fallback when the primary model lacks vision)."""
        try:
            from ...llm.client import get_default_client
            from ...llm.types import ImageBlock, ImageContent, Message, TextBlock

            prompt = question or "Please describe the contents of this image, including key elements, text, layout, etc."
            messages = [
                Message(
                    role="user",
                    content=[
                        ImageBlock(image=ImageContent(media_type=media_type, data=b64_data)),
                        TextBlock(text=prompt),
                    ],
                )
            ]

            client = get_default_client()
            response = await client.chat(messages=messages, max_tokens=1024)
            if response.content:
                for block in response.content:
                    if hasattr(block, "text"):
                        return f"[Image analysis result]\n{block.text}"

            return "[Image analysis] Unable to obtain description"
        except Exception as e:
            logger.warning(f"[view_image] VL fallback failed: {e}")
            return f"[Image analysis failed: {e}]\nHint: The current model does not support image input; consider switching to a vision-capable model (e.g., qwen-vl-plus)."

    def _try_embed_screenshot(self, result: dict) -> list | None:
        """Try to embed the image content of a browser_screenshot result.

        Only takes effect when the model supports vision; otherwise returns None (falls back to the plain-text path).
        """
        if not self._model_supports_vision():
            return None

        inner = result.get("result", {})
        if not isinstance(inner, dict):
            return None

        saved_to = inner.get("saved_to", "")
        if not saved_to:
            return None

        loaded = self._load_image_as_base64(saved_to)
        if loaded is None:
            return None

        b64_data, media_type, w, h = loaded
        page_url = inner.get("page_url", "")
        page_title = inner.get("page_title", "")

        return [
            {
                "type": "text",
                "text": (
                    f"✅ Screenshot saved: {saved_to} ({w}x{h})\n"
                    f"Page: {page_title}\nURL: {page_url}\n"
                    f"Hint: to deliver the screenshot to the user, use the deliver_artifacts tool"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
            },
        ]


def create_handler(agent: "Agent"):
    """Create the browser handler"""
    handler = BrowserHandler(agent)
    return handler.handle
