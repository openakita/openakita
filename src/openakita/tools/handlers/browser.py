"""
浏览器处理器

处理浏览器相关的系统技能：
- browser_task: 【推荐优先使用】智能浏览器任务
- browser_open: 启动浏览器 + 状态查询
- browser_navigate: 导航到 URL
- browser_get_content: 获取页面内容（支持 max_length 截断）
- browser_screenshot: 截取页面截图
- browser_close: 关闭浏览器
"""

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


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

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
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
                from ..browser.chrome_finder import detect_chrome_devtools_mcp, check_mcp_chrome_extension
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


def create_handler(agent: "Agent"):
    """创建浏览器处理器"""
    handler = BrowserHandler(agent)
    return handler.handle
