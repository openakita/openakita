"""
浏览器处理器

处理浏览器相关的系统技能：
- browser_task: 【推荐优先使用】智能浏览器任务
- browser_open: 启动浏览器 + 状态查询
- browser_navigate: 导航到 URL
- browser_get_content: 获取页面内容（支持 max_length 截断）
- browser_screenshot: 截取页面截图
- browser_close: 关闭浏览器
- view_image: 查看/分析本地图片
"""

import base64
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_IMAGE_MAX_PIXELS = 1024 * 1024  # 缩放阈值（宽×高），大于此值会缩放
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class BrowserHandler:
    """
    浏览器处理器

    通过 BrowserManager / PlaywrightTools / BrowserUseRunner 路由浏览器工具调用
    """

    TOOLS = [
        "browser_task",
        "browser_open",
        "browser_navigate",
        "browser_get_content",
        "browser_screenshot",
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

        result = await self._dispatch(actual_tool_name, params)

        if result.get("success"):
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

    async def _dispatch(self, tool_name: str, params: dict[str, Any]) -> dict:
        """将工具调用路由到对应的组件。"""
        manager = self.agent.browser_manager
        pw = self.agent.pw_tools
        bu = self.agent.bu_runner

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
            elif tool_name == "browser_task":
                return await bu.run_task(
                    task=params.get("task", ""),
                    max_steps=params.get("max_steps", 15),
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
                    "error": "浏览器连接已断开（可能被用户关闭）。\n"
                    "【重要】状态已重置，请直接调用 browser_open 重新启动浏览器，无需先调用 browser_close。",
                }

            return {"success": False, "error": error_str}

    async def _handle_open(self, manager: Any, params: dict) -> dict:
        """处理 browser_open（合并了状态查询功能）。"""
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
                            "message": f"浏览器已在{'可见' if manager.visible else '后台'}模式运行，"
                            f"共 {len(all_pages)} 个标签页",
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
                "message": f"浏览器已启动 ({'可见模式' if manager.visible else '后台模式'})",
            }

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
                error_msg = (
                    "无法启动浏览器。浏览器组件已内置，请尝试重启应用。"
                    "如仍有问题，请检查杀毒软件是否拦截 Chromium 启动。"
                )
            else:
                error_msg = "无法启动浏览器。请安装: pip install playwright && playwright install chromium"
            if hints:
                error_msg += "\n\n" + "\n".join(hints)

            return {
                "success": False,
                "result": {"is_open": False, "status": "failed"},
                "error": error_msg,
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
                f"也可以用 browser_get_content(selector=\"...\") 缩小查询范围。"
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
        """读取图片文件，缩放后编码为 base64。

        Returns:
            (base64_data, media_type, width, height) 或 None（失败时）
        """
        p = Path(path_str)
        if not p.is_file():
            return None
        if p.suffix.lower() not in _IMAGE_EXTENSIONS:
            return None

        try:
            import io

            from PIL import Image

            img = Image.open(p)
            w, h = img.size

            if w * h > _IMAGE_MAX_PIXELS:
                ratio = (_IMAGE_MAX_PIXELS / (w * h)) ** 0.5
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                w, h = new_w, new_h

            if img.mode == "RGBA":
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return b64, "image/jpeg", w, h
        except ImportError:
            raw = p.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
            ext = p.suffix.lower()
            media_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            }
            return b64, media_map.get(ext, "image/jpeg"), 0, 0
        except Exception as e:
            logger.error(f"[view_image] Failed to load image {path_str}: {e}")
            return None

    async def _handle_view_image(self, params: dict[str, Any]) -> str | list:
        """view_image 工具处理：读取图片并返回多模态 tool result。"""
        path_str = params.get("path", "")
        question = params.get("question", "")

        if not path_str:
            return "❌ view_image 缺少必要参数 'path'。"

        loaded = self._load_image_as_base64(path_str)
        if loaded is None:
            return f"❌ 无法读取图片: {path_str}（文件不存在或格式不支持）"

        b64_data, media_type, w, h = loaded

        if self._model_supports_vision():
            # 模型支持 vision → 直接嵌入图片
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

        # 模型不支持 vision → 用 VL 模型生成文字描述
        description = await self._describe_image_with_vl(b64_data, media_type, question)
        return f"✅ 图片: {path_str} ({w}x{h})\n\n{description}"

    async def _describe_image_with_vl(
        self, b64_data: str, media_type: str, question: str = "",
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
