"""
PlaywrightTools - All direct Playwright page operations

Depends on BrowserManager to provide the active ``page``.
Each public method automatically calls ``manager.ensure_ready()`` at the start.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import BrowserManager

logger = logging.getLogger(__name__)


class PlaywrightTools:
    """Execute Playwright operations on the page provided by BrowserManager."""

    def __init__(self, manager: BrowserManager):
        self._manager = manager

    # ── Helpers ─────────────────────────────────────────

    async def _ensure(self) -> bool:
        return await self._manager.ensure_ready()

    @property
    def _page(self) -> Any:
        return self._manager.page

    @property
    def _context(self) -> Any:
        return self._manager.context

    # ── Public tool methods (exposed to LLM) ──────────────────────

    async def navigate(self, url: str) -> dict:
        """Navigate to URL"""
        if not url:
            return {"success": False, "error": "URL is required"}

        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1)
            title = await self._page.title()
            return {
                "success": True,
                "result": {
                    "url": self._page.url,
                    "title": title,
                    "status": response.status if response else None,
                    "message": f"Page opened: {title}",
                },
            }
        except Exception as e:
            error_str = str(e)
            logger.error(f"Navigation failed: {e}")
            if "closed" in error_str.lower() or "target" in error_str.lower():
                logger.warning("[Browser] Browser/page closed, resetting state")
                await self._manager.reset_state()
                return {
                    "success": False,
                    "error": "Browser has been closed (possibly by user or due to a crash).\n"
                    "[IMPORTANT] Please call browser_close to clean up state first, then call browser_open to restart the browser.",
                }
            return {
                "success": False,
                "error": f"Page load failed: {error_str}\nSuggestions: 1) Check that the URL is correct 2) The website may be unreachable",
            }

    async def screenshot(self, full_page: bool = False, path: str | None = None) -> dict:
        """Take a screenshot of the current page"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        current_url = self._page.url
        if current_url == "about:blank":
            return {
                "success": False,
                "error": "Current page is blank (about:blank). Please use browser_navigate to open a webpage first",
            }

        try:
            await self._page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        await asyncio.sleep(0.5)

        screenshot_bytes = await self._page.screenshot(full_page=full_page)
        page_title = await self._page.title()

        # Extract brief page text (helps LLM judge page state, even without vision)
        page_text_brief = ""
        try:
            raw_text = await self._page.inner_text("body")
            # Remove excess whitespace, take first 500 characters
            import re as _re

            cleaned = _re.sub(r"\s+", " ", raw_text).strip()
            if cleaned:
                page_text_brief = cleaned[:500]
        except Exception:
            pass

        if not path:
            from datetime import datetime

            screenshots_dir = Path("data/screenshots")
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(screenshots_dir / f"screenshot_{timestamp}.png")

        Path(path).write_bytes(screenshot_bytes)
        result_data: dict = {
            "saved_to": path,
            "page_url": current_url,
            "page_title": page_title,
            "message": f"Screenshot saved to: {path}",
            "hint": (
                "To deliver the screenshot to the user, use the deliver_artifacts tool. "
                "To verify the screenshot content, use the view_image tool to view it."
            ),
        }
        if page_text_brief:
            result_data["page_text_brief"] = page_text_brief
        return {"success": True, "result": result_data}

    async def get_content(self, selector: str | None = None, format: str = "text") -> dict:
        """Get page content"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        if selector:
            element = await self._page.query_selector(selector)
            if not element:
                return {"success": False, "error": f"Element not found: {selector}"}
            if format == "html":
                content = await element.inner_html()
            else:
                content = await element.inner_text()
        else:
            if format == "html":
                content = await self._page.content()
            else:
                content = await self._page.inner_text("body")

        return {"success": True, "result": content}

    # ── Internal tool methods (not exposed to LLM, for internal use) ──────────

    async def click(self, selector: str | None = None, text: str | None = None) -> dict:
        """Click an element"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        if text and not selector:
            selector = f"text={text}"
        if not selector:
            return {"success": False, "error": "selector or text is required"}

        await self._page.click(selector)
        return {"success": True, "result": f"Clicked: {selector}"}

    async def type_text(self, selector: str, text: str, clear: bool = True) -> dict:
        """Type text (with smart retry and overlay handling)"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        if not selector or not text:
            return {"success": False, "error": "selector and text are required"}

        SEARCH_BOX_ALTERNATIVES = {
            "#kw": ['input[name="wd"]', "input.s_ipt", "#kw"],
            'input[name="wd"]': ["#kw", "input.s_ipt"],
            'input[name="q"]': ['textarea[name="q"]', "input.gLFyf", 'input[type="text"]'],
            "#q": ['input[name="q"]', 'textarea[name="q"]'],
            "#sb_form_q": ['input[name="q"]', "input.sb_form_q"],
        }

        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            try:
                try:
                    await self._page.wait_for_selector(selector, state="visible", timeout=5000)
                except Exception:
                    logger.info(
                        f"[BrowserType] Element {selector} not visible, trying to handle overlay..."
                    )
                    await self._handle_page_overlays()
                    await self._page.wait_for_selector(selector, state="visible", timeout=5000)

                if clear:
                    await self._page.fill(selector, text)
                else:
                    await self._page.type(selector, text)

                return {"success": True, "result": f"Typed into {selector}: {text}"}

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Type attempt {attempt + 1} failed: {e}")

                if attempt < max_retries - 1:
                    alt_selectors = SEARCH_BOX_ALTERNATIVES.get(selector, [])
                    for alt in alt_selectors:
                        if alt == selector:
                            continue
                        try:
                            logger.info(f"[BrowserType] Trying alternative selector: {alt}")
                            await self._handle_page_overlays()
                            await self._page.wait_for_selector(alt, state="visible", timeout=3000)
                            if clear:
                                await self._page.fill(alt, text)
                            else:
                                await self._page.type(alt, text)
                            return {
                                "success": True,
                                "result": f"Typed into {alt} (alt selector): {text}",
                            }
                        except Exception:
                            continue

                    if attempt == max_retries - 2:
                        try:
                            logger.info("[BrowserType] Trying force click then type...")
                            element = self._page.locator(selector).first
                            await element.scroll_into_view_if_needed()
                            await element.click(force=True, timeout=3000)
                            await element.fill(text) if clear else await element.type(text)
                            return {
                                "success": True,
                                "result": f"Typed into {selector} (force mode): {text}",
                            }
                        except Exception as force_error:
                            logger.warning(f"Force type also failed: {force_error}")

                    if attempt == max_retries - 1:
                        try:
                            logger.info("[BrowserType] Trying JavaScript injection...")
                            js_result = await self._page.evaluate(
                                """(selector, text, clear) => {
                                const el = document.querySelector(selector);
                                if (!el) return { success: false, error: 'Element not found' };
                                if (clear) el.value = '';
                                el.value = text;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                return { success: true };
                            }""",
                                selector,
                                text,
                                clear,
                            )
                            if js_result.get("success"):
                                return {
                                    "success": True,
                                    "result": f"Typed into {selector} (JavaScript mode): {text}",
                                }
                        except Exception as js_error:
                            logger.warning(f"JavaScript type also failed: {js_error}")

                    await asyncio.sleep(1)

        return {
            "success": False,
            "error": f"Input failed (retried {max_retries} times): {last_error}\n"
            f"Suggestions: 1) Use browser_screenshot to check the current page state "
            f"2) Use browser_click to click on blank area to dismiss potential popups "
            f"3) Use browser_get_content to get page content and confirm the element selector",
        }

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict:
        """Scroll the page"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        if direction == "up":
            amount = -amount
        await self._page.evaluate(f"window.scrollBy(0, {amount})")
        return {"success": True, "result": f"Scrolled {direction} by {abs(amount)}px"}

    async def wait(self, selector: str | None = None, timeout: int = 30000) -> dict:
        """Wait"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        if selector:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return {"success": True, "result": f"Element appeared: {selector}"}
        else:
            await asyncio.sleep(timeout / 1000)
            return {"success": True, "result": f"Waited {timeout}ms"}

    async def execute_js(self, script: str) -> dict:
        """Execute JavaScript"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        if not script:
            return {"success": False, "error": "script is required"}
        result = await self._page.evaluate(script)
        return {"success": True, "result": result}

    async def list_tabs(self) -> dict:
        """List all tabs"""
        if not self._manager.is_ready or not self._context:
            return {"success": False, "error": "Browser is not started"}

        try:
            all_pages = self._context.pages
            tabs = []
            for i, page in enumerate(all_pages):
                try:
                    title = await page.title()
                    tabs.append(
                        {
                            "index": i,
                            "url": page.url,
                            "title": title,
                            "is_current": page == self._page,
                        }
                    )
                except Exception:
                    tabs.append(
                        {
                            "index": i,
                            "url": page.url,
                            "title": "(unable to retrieve)",
                            "is_current": page == self._page,
                        }
                    )
            return {
                "success": True,
                "result": {"tabs": tabs, "count": len(tabs), "message": f"{len(tabs)} tab(s) in total"},
            }
        except Exception as e:
            logger.error(f"Failed to list tabs: {e}")
            return {"success": False, "error": f"Failed to list tabs: {str(e)}"}

    async def switch_tab(self, index: int) -> dict:
        """Switch to the specified tab"""
        if not self._manager.is_ready or not self._context:
            return {"success": False, "error": "Browser is not started"}

        try:
            all_pages = self._context.pages
            if index < 0 or index >= len(all_pages):
                return {
                    "success": False,
                    "error": f"Tab index {index} is invalid. Valid range: 0-{len(all_pages) - 1}",
                }
            self._manager._page = all_pages[index]
            await self._manager._page.bring_to_front()
            title = await self._manager._page.title()
            return {
                "success": True,
                "result": {
                    "switched_to": {"index": index, "url": self._manager._page.url, "title": title},
                    "message": f"Switched to tab {index}: {title}",
                },
            }
        except Exception as e:
            logger.error(f"Failed to switch tab: {e}")
            return {"success": False, "error": f"Failed to switch tab: {str(e)}"}

    async def new_tab(self, url: str) -> dict:
        """Open URL in a new tab"""
        if not await self._ensure():
            return {"success": False, "error": "Failed to start browser"}

        try:
            if not self._context:
                return {"success": False, "error": "Browser context is not available"}

            reused_blank = False
            if self._page and self._page.url in ("about:blank", ""):
                new_page = self._page
                reused_blank = True
            else:
                new_page = await self._context.new_page()

            await new_page.goto(url, wait_until="domcontentloaded")
            self._manager._page = new_page
            title = await new_page.title()
            all_pages = self._context.pages

            return {
                "success": True,
                "result": {
                    "url": url,
                    "title": title,
                    "tab_index": len(all_pages) - 1,
                    "total_tabs": len(all_pages),
                    "reused_blank": reused_blank,
                    "message": f"Opened in {'blank' if reused_blank else 'new'} tab: {title}",
                },
            }
        except Exception as e:
            error_str = str(e)
            logger.error(f"Failed to open new tab: {e}")
            if "closed" in error_str.lower() or "target" in error_str.lower():
                logger.warning("[Browser] Browser/page closed, resetting state")
                await self._manager.reset_state()
                return {
                    "success": False,
                    "error": "Browser has been closed. Please call browser_close first, then call browser_open to restart the browser.",
                }
            return {"success": False, "error": f"Failed to open new tab: {error_str}"}

    # ── Internal helpers ─────────────────────────────────────

    async def _handle_page_overlays(self) -> None:
        """Handle common page overlay elements (popups, ads, login prompts, etc.)."""
        current_url = self._page.url

        if "baidu.com" in current_url:
            try:
                await self._page.evaluate("""() => {
                    const overlaySelectors = [
                        '.s-skin-container',
                        '.s-isindex-wrap .c-tips-container',
                        '.s-top-login-btn',
                        '#s-top-loginbtn',
                        '.soutu-env-nom498-index',
                        '#s_tab',
                        '.s-p-top',
                    ];
                    overlaySelectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => {
                            el.style.display = 'none';
                        });
                    });

                    const searchInput = document.querySelector('#kw') || document.querySelector('input[name="wd"]');
                    if (searchInput) {
                        searchInput.style.visibility = 'visible';
                        searchInput.style.display = 'block';
                        searchInput.style.opacity = '1';
                        let parent = searchInput.parentElement;
                        while (parent && parent !== document.body) {
                            parent.style.visibility = 'visible';
                            parent.style.display = parent.style.display === 'none' ? 'block' : parent.style.display;
                            parent.style.opacity = '1';
                            parent = parent.parentElement;
                        }
                    }
                }""")
                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug(f"[BrowserOverlay] Baidu overlay removal failed: {e}")

        close_selectors = [
            'button[aria-label="Close"]',
            'button[aria-label="关闭"]',
            ".close-btn",
            ".close-button",
            ".btn-close",
            '[class*="close"]',
            '[class*="dismiss"]',
            ".c-tips-container .close",
            ".login-guide-close",
            "#s-top-loginbtn",
            ".modal-close",
            ".popup-close",
            'button:has-text("我知道了")',
            'button:has-text("关闭")',
            'button:has-text("跳过")',
            'button:has-text("Skip")',
        ]

        for sel in close_selectors:
            try:
                element = self._page.locator(sel).first
                if await element.is_visible():
                    await element.click(timeout=1000)
                    logger.info(f"[BrowserType] Closed overlay: {sel}")
                    await asyncio.sleep(0.3)
            except Exception:
                continue

        try:
            await self._page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
        except Exception:
            pass

        try:
            await self._page.mouse.click(10, 10)
            await asyncio.sleep(0.2)
        except Exception:
            pass
