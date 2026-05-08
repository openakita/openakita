"""
浏览器处理器

处理浏览器相关的系统技能（全部基于 Playwright）：
- browser_open: 启动浏览器 + 状态查询
- browser_navigate: 导航到 URL
- browser_click: 点击页面元素
- browser_type: 输入文本
- browser_scroll: 滚动页面
- browser_wait: 等待元素出现
- browser_execute_js: 执行 JavaScript
- browser_get_content: 获取页面内容（支持 max_length 截断）
- browser_screenshot: 截取页面截图
- browser_list_tabs / browser_switch_tab / browser_new_tab: 标签页管理
- browser_close: 关闭浏览器
- view_image: 查看/分析本地图片
"""

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

# Operations that depend on the shared current page.
# get_content/screenshot are read-only, but they must not overlap with
# navigation; otherwise one agent can read another agent's current tab.
_LOCKED_BROWSER_OPS = frozenset(
    {
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_execute_js",
        "browser_get_content",
        "browser_screenshot",
        "browser_new_tab",
        "browser_switch_tab",
        "browser_close",
    }
)


class BrowserHandler:
    """
    浏览器处理器

    通过 BrowserManager / PlaywrightTools 路由浏览器工具调用
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

    # browser_get_content 默认最大字符数
    CONTENT_DEFAULT_MAX_LENGTH = 32000

    def __init__(self, agent: "Agent"):
        self.agent = agent

    def _check_ready(self) -> str | None:
        """检查浏览器组件是否已初始化，返回错误消息或 None。"""
        has_manager = hasattr(self.agent, "browser_manager") and self.agent.browser_manager
        if not has_manager:
            from openakita.runtime_env import IS_FROZEN

            if IS_FROZEN:
                return "❌ 浏览器服务未启动。请尝试重启应用，如仍有问题请查看日志排查原因。"
            else:
                return "❌ 浏览器模块未启动。请安装: pip install playwright && playwright install chromium"
        return None

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str | list:
        """处理工具调用，返回 str 或多模态 list（view_image/browser_screenshot）。"""

        # view_image 不依赖浏览器，直接处理
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

        if actual_tool_name == "browser_get_content" and result.get("success"):
            output = self._format_get_content_result(result, params)
        elif result.get("success"):
            output = f"✅ {result.get('result', 'OK')}"
        else:
            output = f"❌ {result.get('error', '未知错误')}"

        if actual_tool_name == "browser_get_content":
            output = self._maybe_truncate(output, params)

        # browser_screenshot: 自动附带图片内容（如果模型支持 vision）
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
        except TimeoutError:
            current_holder = await _browser_lock_manager.get_holder("tool:browser")
            logger.warning(
                f"[Browser] Lock timeout for {tool_name} (holder={current_holder}, waiter={holder})"
            )
            return {
                "success": False,
                "error": (
                    f"浏览器被其他 Agent 占用（{current_holder or '未知'}），"
                    f"等待 {int(_BROWSER_LOCK_TIMEOUT)}秒后超时。请稍后重试。"
                ),
            }

    async def _dispatch(self, tool_name: str, params: dict[str, Any]) -> dict:
        """将工具调用路由到对应的组件。"""
        manager = self.agent.browser_manager
        pw = self.agent.pw_tools

        try:
            if tool_name == "browser_open":
                return await self._handle_open(manager, params)
            elif tool_name == "browser_close":
                await manager.stop()
                return {"success": True, "result": "Browser closed"}
            elif tool_name == "browser_navigate":
                result = await pw.navigate(params.get("url", ""))
                if result.get("success"):
                    self.agent._last_browser_navigate_url = params.get("url", "")
                return result
            elif tool_name == "browser_screenshot":
                result = await pw.screenshot(
                    full_page=params.get("full_page", False),
                    path=params.get("path"),
                )
                if result.get("success"):
                    result["source"] = await self._capture_page_source(manager)
                return result
            elif tool_name == "browser_get_content":
                result = await pw.get_content(
                    selector=params.get("selector"),
                    format=params.get("format", "text"),
                )
                if result.get("success"):
                    source = await self._capture_page_source(manager)
                    expected_url = params.get("expected_url") or getattr(
                        self.agent, "_last_browser_navigate_url", ""
                    )
                    if expected_url and source.get("current_url") != expected_url:
                        source["warning"] = (
                            "当前浏览器页面与预期 URL 不一致，可能是页面跳转或其他任务改变了当前页。"
                        )
                        source["expected_url"] = expected_url
                    result["source"] = source
                    result["selector"] = params.get("selector")
                    result["format"] = params.get("format", "text")
                return result
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
                self.agent._browser_user_closed = True
                return {
                    "success": False,
                    "error": "浏览器连接已断开（可能被用户关闭）。\n"
                    "【重要】不要自动重新打开前台浏览器。请先向用户说明浏览器已关闭，"
                    "只有在用户明确确认继续后，才能调用 browser_open 并传入 "
                    '{"user_confirmed": true} 重新启动。',
                }

            return {"success": False, "error": error_str}

    @staticmethod
    async def _capture_page_source(manager: Any) -> dict[str, Any]:
        """Return the actual page URL/title for provenance display."""
        source: dict[str, Any] = {"current_url": "", "title": ""}
        page = getattr(manager, "page", None)
        if not page:
            return source
        try:
            source["current_url"] = getattr(page, "url", "") or ""
        except Exception:
            source["current_url"] = ""
        try:
            source["title"] = await page.title()
        except Exception:
            source["title"] = ""
        return source

    @staticmethod
    def _format_get_content_result(result: dict[str, Any], params: dict[str, Any]) -> str:
        source = result.get("source") if isinstance(result.get("source"), dict) else {}
        current_url = source.get("current_url", "")
        title = source.get("title", "")
        expected_url = source.get("expected_url", "")
        warning = source.get("warning", "")
        selector = result.get("selector", params.get("selector"))
        fmt = result.get("format", params.get("format", "text"))
        content = result.get("result", "OK")
        source_payload = {
            "tool_name": "browser_get_content",
            "requested_url": expected_url or current_url,
            "final_url": current_url,
            "hostname": "",
            "redirected": bool(expected_url and current_url and expected_url != current_url),
            "from_cache": False,
            "status": "ok",
            "hint": warning,
        }
        try:
            from urllib.parse import urlparse

            source_payload["hostname"] = urlparse(current_url or expected_url).hostname or ""
        except Exception:
            pass

        import json

        lines = [
            f"[OPENAKITA_SOURCE] {json.dumps(source_payload, ensure_ascii=False, sort_keys=True)}",
            "✅ Browser content read",
            f"Current URL: {current_url or 'unknown'}",
            f"Title: {title or 'unknown'}",
            f"Selector: {selector or 'document'}",
            f"Format: {fmt}",
        ]
        if expected_url:
            lines.append(f"Expected URL: {expected_url}")
        if warning:
            lines.append(f"Warning: {warning}")
        lines.append("")
        lines.append(str(content))
        return "\n".join(lines)

    async def _handle_open(self, manager: Any, params: dict) -> dict:
        """处理 browser_open（合并了状态查询功能）。"""
        visible = params.get("visible", True)
        if (
            getattr(self.agent, "_browser_user_closed", False)
            and visible
            and not params.get("user_confirmed")
        ):
            return {
                "success": False,
                "error": (
                    "浏览器之前已被用户关闭。为避免在用户未确认时重新打开前台浏览器，"
                    "本次启动已被拦截。请先询问用户是否继续；用户确认后再调用 "
                    'browser_open({"visible": true, "user_confirmed": true})。'
                ),
            }

        if manager.is_ready and manager.context and manager.page:
            try:
                current_url = manager.page.url
                current_title = await manager.page.title()
                all_pages = manager.context.pages

                if visible != manager.visible:
                    logger.info(f"Browser mode change requested: visible={visible}, restarting...")
                    await manager.stop()
                else:
                    result_data = self._build_open_status_result(
                        status="already_running",
                        manager=manager,
                        tab_count=len(all_pages),
                        current_url=current_url,
                        current_title=current_title,
                    )
                    return {
                        "success": True,
                        "result": result_data,
                    }
            except Exception as e:
                logger.warning(f"[Browser] Browser connection lost: {e}, resetting state")
                await manager.reset_state()
                self.agent._browser_user_closed = True
                if visible and not params.get("user_confirmed"):
                    return {
                        "success": False,
                        "error": (
                            "浏览器连接已断开（可能被用户关闭）。为避免在用户未确认时"
                            "重新打开前台浏览器，本次启动已被拦截。请先询问用户是否继续；"
                            "用户确认后再调用 "
                            'browser_open({"visible": true, "user_confirmed": true})。'
                        ),
                    }
        elif manager.is_ready:
            logger.warning("[Browser] Incomplete browser state, resetting")
            await manager.reset_state()

        success = await manager.start(visible=visible)

        if success:
            if params.get("user_confirmed") or not visible:
                self.agent._browser_user_closed = False
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

            result_data = self._build_open_status_result(
                status="started",
                manager=manager,
                tab_count=tab_count,
                current_url=current_url,
                current_title=current_title,
            )

            try:
                from ..browser.chrome_finder import detect_chrome_devtools_mcp

                devtools_info = detect_chrome_devtools_mcp()
                if devtools_info["available"] and not manager.using_user_chrome:
                    result_data["hint"] = (
                        "提示：检测到 Chrome DevTools MCP 可用。如需保留登录状态，"
                        "可使用 call_mcp_tool('chrome-devtools', ...) 调用。"
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
                        "备选方案：Chrome DevTools MCP 可用，可通过 "
                        "call_mcp_tool('chrome-devtools', 'navigate_page', {url: '...'}) 操作浏览器。"
                    )
                mcp_chrome_available = await check_mcp_chrome_extension()
                if mcp_chrome_available:
                    hints.append(
                        "备选方案：mcp-chrome 扩展已运行，可通过 "
                        "call_mcp_tool('chrome-browser', ...) 操作浏览器。"
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
                            "检测到 Chrome 浏览器正在运行，这可能导致配置文件冲突。"
                            "请尝试关闭 Chrome 后重试，或直接使用内置浏览器。"
                        )
                except Exception:
                    pass
                error_msg = "❌ 无法启动浏览器。" + (
                    chrome_running_hint
                    or "浏览器组件已内置，请尝试重启应用。"
                    "如仍有问题，请检查杀毒软件是否拦截 Chromium 启动。"
                )
            else:
                error_msg = (
                    "无法启动浏览器。请安装: pip install playwright && playwright install chromium"
                )
            if hints:
                error_msg += "\n\n" + "\n".join(hints)

            return {
                "success": False,
                "result": {"is_open": False, "status": "failed"},
                "error": error_msg,
            }

    @staticmethod
    def _build_open_status_result(
        *,
        status: str,
        manager: Any,
        tab_count: int,
        current_url: str | None,
        current_title: str | None,
    ) -> dict[str, Any]:
        """Build a precise browser-open status without claiming desktop foreground.

        ``manager.visible`` means the Playwright session is headed (not headless).
        It does not prove that an OS desktop window is visible or focused to the
        user, which was the root cause of #470.
        """
        headed = bool(getattr(manager, "visible", False))
        mode = "有界面自动化模式" if headed else "后台自动化模式"
        action = "已连接" if status == "already_running" else "已启动"
        visibility_note = (
            "`visible/headed` 仅表示浏览器自动化不是 headless；"
            "尚未验证系统桌面窗口是否可见或处于前台。"
        )

        return {
            "is_open": True,
            "automation_ready": True,
            "status": status,
            # Keep existing key for compatibility; new callers should prefer `headed`.
            "visible": headed,
            "headed": headed,
            "desktop_window_visible": None,
            "foreground_verified": None,
            "tab_count": tab_count,
            "current_tab": {"url": current_url, "title": current_title},
            "using_user_chrome": getattr(manager, "using_user_chrome", False),
            "visibility_note": visibility_note,
            "message": (
                f"浏览器自动化会话{action}（{mode}），共 {tab_count} 个标签页。"
                "如用户看不到窗口，请使用桌面窗口/截图工具验证并切换到前台。"
            ),
        }

    def _maybe_truncate(self, output: str, params: dict) -> str:
        """browser_get_content 的智能截断。"""
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
                f"\n\n[OUTPUT_TRUNCATED] 页面内容共 {total_chars} 字符，"
                f"已显示前 {max_length} 字符。\n"
                f"完整内容已保存到: {overflow_path}\n"
                f'使用 read_file(path="{overflow_path}", offset=1, limit=300) '
                f"查看完整内容。\n"
                f'也可以用 browser_get_content(selector="...") 缩小查询范围。'
            )

        return output

    # ── view_image / screenshot 多模态支持 ────────────

    def _model_supports_vision(self) -> bool:
        """检查当前 LLM 是否支持 vision（图片输入）。"""
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
        """读取图片文件，压缩到安全大小后编码为 base64。

        委托给 channels.media.image_prep 的共享预处理函数，
        确保 base64 产出不超过 API payload 限制。

        Returns:
            (base64_data, media_type, width, height) 或 None（失败时）
        """
        p = Path(path_str)
        if not p.is_file():
            return None
        if p.suffix.lower() not in _IMAGE_EXTENSIONS:
            return None

        from ...channels.media.image_prep import prepare_image_file_for_context

        return prepare_image_file_for_context(p)

    async def _handle_view_image(self, params: dict[str, Any]) -> str | list:
        """view_image 工具处理：读取图片并返回多模态 tool result。支持本地路径和 HTTP(S) URL。"""
        path_str = params.get("path", "")
        question = params.get("question", "")

        if not path_str:
            return "❌ view_image 缺少必要参数 'path'。"

        # HTTP(S) URL → 下载到临时文件后按本地文件处理
        if path_str.startswith(("http://", "https://")):
            loaded = await self._download_and_load_image(path_str)
            if loaded is None:
                return f"❌ 无法读取图片: {path_str}（文件不存在或格式不支持）"
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
            return f"❌ 无法读取图片: {path_str}（文件不存在）"
        if p.suffix.lower() not in _IMAGE_EXTENSIONS:
            return (
                f"❌ 不支持的图片格式: {p.suffix}\n"
                f"支持的格式: {', '.join(sorted(_IMAGE_EXTENSIONS))}"
            )
        loaded = self._load_image_as_base64(path_str)
        if loaded is None:
            return (
                f"❌ 图片过大无法嵌入上下文: {path_str}\n"
                f"文件大小: {p.stat().st_size / 1024:.0f}KB。"
                f"请安装 Pillow (pip install Pillow) 以启用自动压缩，"
                f"或使用更小的图片。"
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
        """根据模型 vision 能力构建 view_image 结果。"""
        if self._model_supports_vision():
            content: list[dict] = [
                {"type": "text", "text": f"✅ 已加载图片: {path_str} ({w}x{h})"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
                },
            ]
            if question:
                content.append({"type": "text", "text": f"请回答: {question}"})
            return content

        description = await self._describe_image_with_vl(b64_data, media_type, question)
        return f"✅ 图片: {path_str} ({w}x{h})\n\n{description}"

    @staticmethod
    async def _download_and_load_image(url: str) -> tuple[str, str, int, int] | None:
        """下载 HTTP(S) 图片到临时文件并加载为 base64。"""
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
        """使用 VL 模型对图片进行文字描述（当主模型不支持 vision 时的降级方案）。"""
        try:
            from ...llm.client import get_default_client
            from ...llm.types import ImageBlock, ImageContent, Message, TextBlock

            prompt = question or "请描述这张图片的内容，包括关键元素、文字、布局等。"
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
                        return f"[图片分析结果]\n{block.text}"

            return "[图片分析] 无法获取描述"
        except Exception as e:
            logger.warning(f"[view_image] VL fallback failed: {e}")
            return f"[图片分析失败: {e}]\n提示: 当前模型不支持图片输入，建议切换到支持 vision 的模型（如 qwen-vl-plus）。"

    def _try_embed_screenshot(self, result: dict) -> list | None:
        """尝试将 browser_screenshot 的结果嵌入图片内容。

        仅在模型支持 vision 时生效，否则返回 None（走普通文本路径）。
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
                    f"✅ 截图已保存: {saved_to} ({w}x{h})\n"
                    f"页面: {page_title}\nURL: {page_url}\n"
                    f"提示: 如需将截图交付给用户，请使用 deliver_artifacts 工具"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
            },
        ]


def create_handler(agent: "Agent"):
    """创建浏览器处理器"""
    handler = BrowserHandler(agent)
    return handler.handle
