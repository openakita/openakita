"""
PlaywrightTools - 所有直接 Playwright 页面操作

依赖 BrowserManager 提供活跃的 ``page``。
每个公共方法在开始时自动调用 ``manager.ensure_ready()``。
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
    """在 BrowserManager 提供的 page 上执行 Playwright 操作。"""

    def __init__(self, manager: BrowserManager):
        self._manager = manager

    # ── 辅助 ──────────────────────────────────────────

    async def _ensure(self) -> bool:
        return await self._manager.ensure_ready()

    @property
    def _page(self) -> Any:
        return self._manager.page

    @property
    def _context(self) -> Any:
        return self._manager.context

    # ── 公共工具方法（暴露给 LLM） ──────────────────────

    async def navigate(self, url: str) -> dict:
        """导航到 URL"""
        if not url:
            return {"success": False, "error": "URL is required"}

        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

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
                    "message": f"已打开页面: {title}",
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
                    "error": "浏览器已关闭（可能被用户关闭或崩溃）。\n"
                    "【重要】请先调用 browser_close 清理状态，然后重新调用 browser_open 启动浏览器。",
                }
            return {
                "success": False,
                "error": f"页面加载失败: {error_str}\n建议: 1) 检查 URL 是否正确 2) 该网站可能无法访问",
            }

    async def screenshot(self, full_page: bool = False, path: str | None = None) -> dict:
        """截取当前页面截图"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

        current_url = self._page.url
        if current_url == "about:blank":
            return {
                "success": False,
                "error": "当前页面是空白页 (about:blank)，请先使用 browser_navigate 打开一个网页",
            }

        try:
            await self._page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        await asyncio.sleep(0.5)

        screenshot_bytes = await self._page.screenshot(full_page=full_page)
        page_title = await self._page.title()

        if not path:
            from datetime import datetime
            screenshots_dir = Path("data/screenshots")
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = str(screenshots_dir / f"screenshot_{timestamp}.png")

        Path(path).write_bytes(screenshot_bytes)
        return {
            "success": True,
            "result": {
                "saved_to": path,
                "page_url": current_url,
                "page_title": page_title,
                "message": f"截图已保存到: {path}",
                "hint": "如需将截图交付给用户，请使用 deliver_artifacts 工具，把此路径作为 artifacts[].path 传入",
            },
        }

    async def get_content(self, selector: str | None = None, format: str = "text") -> dict:
        """获取页面内容"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

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

    # ── 内部工具方法（不暴露给 LLM，供内部调用） ──────────

    async def click(self, selector: str | None = None, text: str | None = None) -> dict:
        """点击元素"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

        if text and not selector:
            selector = f"text={text}"
        if not selector:
            return {"success": False, "error": "selector or text is required"}

        await self._page.click(selector)
        return {"success": True, "result": f"Clicked: {selector}"}

    async def type_text(self, selector: str, text: str, clear: bool = True) -> dict:
        """输入文本（带智能重试和遮挡处理）"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

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
            "error": f"输入失败（重试 {max_retries} 次）: {last_error}\n"
            f"建议: 1) 先用 browser_screenshot 截图查看当前页面状态 "
            f"2) 使用 browser_click 点击页面空白处关闭可能的弹窗 "
            f"3) 使用 browser_get_content 获取页面内容确认元素选择器",
        }

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict:
        """滚动页面"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

        if direction == "up":
            amount = -amount
        await self._page.evaluate(f"window.scrollBy(0, {amount})")
        return {"success": True, "result": f"Scrolled {direction} by {abs(amount)}px"}

    async def wait(self, selector: str | None = None, timeout: int = 30000) -> dict:
        """等待"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

        if selector:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return {"success": True, "result": f"Element appeared: {selector}"}
        else:
            await asyncio.sleep(timeout / 1000)
            return {"success": True, "result": f"Waited {timeout}ms"}

    async def execute_js(self, script: str) -> dict:
        """执行 JavaScript"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

        if not script:
            return {"success": False, "error": "script is required"}
        result = await self._page.evaluate(script)
        return {"success": True, "result": result}

    async def list_tabs(self) -> dict:
        """列出所有标签页"""
        if not self._manager.is_ready or not self._context:
            return {"success": False, "error": "浏览器未启动"}

        try:
            all_pages = self._context.pages
            tabs = []
            for i, page in enumerate(all_pages):
                try:
                    title = await page.title()
                    tabs.append({
                        "index": i, "url": page.url,
                        "title": title, "is_current": page == self._page,
                    })
                except Exception:
                    tabs.append({
                        "index": i, "url": page.url,
                        "title": "(无法获取)", "is_current": page == self._page,
                    })
            return {
                "success": True,
                "result": {"tabs": tabs, "count": len(tabs), "message": f"共 {len(tabs)} 个标签页"},
            }
        except Exception as e:
            logger.error(f"Failed to list tabs: {e}")
            return {"success": False, "error": f"获取标签页列表失败: {str(e)}"}

    async def switch_tab(self, index: int) -> dict:
        """切换到指定标签页"""
        if not self._manager.is_ready or not self._context:
            return {"success": False, "error": "浏览器未启动"}

        try:
            all_pages = self._context.pages
            if index < 0 or index >= len(all_pages):
                return {
                    "success": False,
                    "error": f"标签页索引 {index} 无效。有效范围: 0-{len(all_pages) - 1}",
                }
            self._manager._page = all_pages[index]
            await self._manager._page.bring_to_front()
            title = await self._manager._page.title()
            return {
                "success": True,
                "result": {
                    "switched_to": {"index": index, "url": self._manager._page.url, "title": title},
                    "message": f"已切换到标签页 {index}: {title}",
                },
            }
        except Exception as e:
            logger.error(f"Failed to switch tab: {e}")
            return {"success": False, "error": f"切换标签页失败: {str(e)}"}

    async def new_tab(self, url: str) -> dict:
        """在新标签页打开 URL"""
        if not await self._ensure():
            return {"success": False, "error": "浏览器启动失败"}

        try:
            if not self._context:
                return {"success": False, "error": "浏览器 context 不可用"}

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
                    "message": f"已在{'空白' if reused_blank else '新'}标签页打开: {title}",
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
                    "error": "浏览器已关闭。请先调用 browser_close 然后重新调用 browser_open 启动浏览器。",
                }
            return {"success": False, "error": f"打开新标签页失败: {error_str}"}

    # ── 内部辅助 ─────────────────────────────────────

    async def _handle_page_overlays(self) -> None:
        """处理常见的页面遮挡元素（弹窗、广告、登录提示等）。"""
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
            ".close-btn", ".close-button", ".btn-close",
            '[class*="close"]', '[class*="dismiss"]',
            ".c-tips-container .close", ".login-guide-close", "#s-top-loginbtn",
            ".modal-close", ".popup-close",
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
