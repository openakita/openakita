"""
Browser Use MCP - 基于 Playwright 的浏览器自动化

功能:
- 打开网页
- 点击元素
- 输入文本
- 截图
- 提取内容

随 OpenAkita 系统一起启动
"""

import asyncio
import logging
import base64
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Playwright 延迟导入 (可能未安装)
playwright = None
Browser = None
Page = None


@dataclass
class BrowserTool:
    """浏览器工具定义"""
    name: str
    description: str
    arguments: dict = field(default_factory=dict)


class BrowserMCP:
    """
    Browser Use MCP Server
    
    基于 Playwright 的浏览器自动化服务
    """
    
    # 工具定义
    TOOLS = [
        BrowserTool(
            name="browser_open",
            description="启动浏览器。如果需要用户观看操作过程或调试，设置 visible=True；如果只是后台自动化任务，设置 visible=False",
            arguments={
                "type": "object",
                "properties": {
                    "visible": {
                        "type": "boolean",
                        "description": "是否显示浏览器窗口。True=用户可见(调试/演示), False=后台运行(自动化)",
                        "default": False
                    },
                    "ask_user": {
                        "type": "boolean",
                        "description": "是否先询问用户偏好。如果为 True，会返回提示让你询问用户",
                        "default": False
                    }
                }
            }
        ),
        BrowserTool(
            name="browser_navigate",
            description="导航到指定 URL (如果浏览器未启动，会自动以后台模式启动)",
            arguments={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要访问的 URL"}
                },
                "required": ["url"]
            }
        ),
        BrowserTool(
            name="browser_click",
            description="点击页面上的元素 (通过选择器或文本)",
            arguments={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器或 XPath"},
                    "text": {"type": "string", "description": "元素文本 (可选，用于模糊匹配)"}
                }
            }
        ),
        BrowserTool(
            name="browser_type",
            description="在输入框中输入文本",
            arguments={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "输入框选择器"},
                    "text": {"type": "string", "description": "要输入的文本"},
                    "clear": {"type": "boolean", "description": "是否先清空", "default": True}
                },
                "required": ["selector", "text"]
            }
        ),
        BrowserTool(
            name="browser_screenshot",
            description="截取当前页面截图",
            arguments={
                "type": "object",
                "properties": {
                    "full_page": {"type": "boolean", "description": "是否截取整个页面", "default": False},
                    "path": {"type": "string", "description": "保存路径 (可选)"}
                }
            }
        ),
        BrowserTool(
            name="browser_get_content",
            description="获取页面内容 (文本或 HTML)",
            arguments={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "元素选择器 (可选，默认整个页面)"},
                    "format": {"type": "string", "enum": ["text", "html"], "default": "text"}
                }
            }
        ),
        BrowserTool(
            name="browser_wait",
            description="等待元素出现或指定时间",
            arguments={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "等待的元素选择器"},
                    "timeout": {"type": "number", "description": "超时时间(毫秒)", "default": 30000}
                }
            }
        ),
        BrowserTool(
            name="browser_scroll",
            description="滚动页面",
            arguments={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"], "default": "down"},
                    "amount": {"type": "number", "description": "滚动像素", "default": 500}
                }
            }
        ),
        BrowserTool(
            name="browser_execute_js",
            description="执行 JavaScript 代码",
            arguments={
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JavaScript 代码"}
                },
                "required": ["script"]
            }
        ),
        BrowserTool(
            name="browser_close",
            description="关闭浏览器",
            arguments={
                "type": "object",
                "properties": {}
            }
        ),
    ]
    
    def __init__(self, headless: bool = False):
        """
        Args:
            headless: 是否无头模式 (默认 False，即可见模式)
        """
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page: Optional[Any] = None
        self._started = False
        self._visible = True  # 当前是否可见模式（默认可见）
    
    async def start(self, visible: bool = None) -> bool:
        """
        启动浏览器
        
        Args:
            visible: 是否可见模式。None 使用默认设置，True 显示窗口，False 后台运行
        """
        if self._started:
            # 如果已启动但模式不同，需要重启
            if visible is not None and visible != self._visible:
                logger.info(f"Browser mode change requested: visible={visible}, restarting...")
                await self.stop()
            else:
                return True
        
        # 确定是否 headless
        if visible is not None:
            headless = not visible
        else:
            headless = self.headless
        
        try:
            # 延迟导入 playwright
            from playwright.async_api import async_playwright
            
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                ]
            )
            self._page = await self._browser.new_page()
            
            # 设置默认超时
            self._page.set_default_timeout(30000)
            
            self._started = True
            self._visible = not headless
            logger.info(f"Browser MCP started (visible={self._visible})")
            return True
            
        except ImportError:
            logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return False
        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            return False
    
    async def stop(self) -> None:
        """停止浏览器"""
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        
        self._started = False
        logger.info("Browser MCP stopped")
    
    def get_tools(self) -> list[dict]:
        """获取工具定义列表"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.arguments,
            }
            for tool in self.TOOLS
        ]
    
    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        调用工具
        
        Args:
            tool_name: 工具名称
            arguments: 参数
        
        Returns:
            {"success": bool, "result": Any, "error": str}
        """
        # browser_open 是特殊的，需要先处理
        if tool_name == "browser_open":
            return await self._open_browser(
                arguments.get("visible", False),
                arguments.get("ask_user", False)
            )
        
        if not self._started:
            success = await self.start()
            if not success:
                return {
                    "success": False,
                    "error": "Browser not started. Please install playwright: pip install playwright && playwright install chromium"
                }
        
        try:
            if tool_name == "browser_navigate":
                return await self._navigate(arguments.get("url"))
            
            elif tool_name == "browser_click":
                return await self._click(
                    arguments.get("selector"),
                    arguments.get("text")
                )
            
            elif tool_name == "browser_type":
                return await self._type(
                    arguments.get("selector"),
                    arguments.get("text"),
                    arguments.get("clear", True)
                )
            
            elif tool_name == "browser_screenshot":
                return await self._screenshot(
                    arguments.get("full_page", False),
                    arguments.get("path")
                )
            
            elif tool_name == "browser_get_content":
                return await self._get_content(
                    arguments.get("selector"),
                    arguments.get("format", "text")
                )
            
            elif tool_name == "browser_wait":
                return await self._wait(
                    arguments.get("selector"),
                    arguments.get("timeout", 30000)
                )
            
            elif tool_name == "browser_scroll":
                return await self._scroll(
                    arguments.get("direction", "down"),
                    arguments.get("amount", 500)
                )
            
            elif tool_name == "browser_execute_js":
                return await self._execute_js(arguments.get("script"))
            
            elif tool_name == "browser_close":
                await self.stop()
                return {"success": True, "result": "Browser closed"}
            
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}
                
        except Exception as e:
            logger.error(f"Browser tool error: {e}")
            return {"success": False, "error": str(e)}
    
    # ==================== 工具实现 ====================
    
    async def _open_browser(self, visible: bool, ask_user: bool) -> dict:
        """
        启动浏览器
        
        Args:
            visible: 是否显示浏览器窗口
            ask_user: 是否询问用户
        """
        if ask_user:
            # 返回提示，让 agent 询问用户
            return {
                "success": True,
                "result": {
                    "action": "ask_user",
                    "message": "请询问用户是否希望看到浏览器操作过程：\n"
                               "- 选择「是」：打开可见的浏览器窗口，可以看到自动化操作过程\n"
                               "- 选择「否」：后台静默运行，速度更快但看不到过程",
                    "options": ["visible", "headless"]
                }
            }
        
        # 如果已启动且模式相同，直接返回
        if self._started and self._visible == visible:
            return {
                "success": True,
                "result": {
                    "status": "already_running",
                    "visible": self._visible,
                    "message": f"浏览器已在{'可见' if self._visible else '后台'}模式运行"
                }
            }
        
        # 启动浏览器
        success = await self.start(visible=visible)
        
        if success:
            return {
                "success": True,
                "result": {
                    "status": "started",
                    "visible": self._visible,
                    "message": f"浏览器已启动 ({'可见模式 - 用户可以看到操作' if self._visible else '后台模式 - 静默运行'})"
                }
            }
        else:
            return {
                "success": False,
                "error": "无法启动浏览器。请确保已安装 playwright: pip install playwright && playwright install chromium"
            }
    
    async def _navigate(self, url: str) -> dict:
        """导航到 URL"""
        if not url:
            return {"success": False, "error": "URL is required"}
        
        # 自动添加协议
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        response = await self._page.goto(url, wait_until="domcontentloaded")
        
        # 等待页面网络空闲（最多 5 秒）
        try:
            await self._page.wait_for_load_state("networkidle", timeout=5000)
        except:
            pass  # 超时也继续
        
        return {
            "success": True,
            "result": {
                "url": self._page.url,
                "title": await self._page.title(),
                "status": response.status if response else None,
            }
        }
    
    async def _click(self, selector: Optional[str], text: Optional[str]) -> dict:
        """点击元素"""
        if text and not selector:
            # 通过文本查找
            selector = f"text={text}"
        
        if not selector:
            return {"success": False, "error": "selector or text is required"}
        
        await self._page.click(selector)
        
        return {
            "success": True,
            "result": f"Clicked: {selector}"
        }
    
    async def _type(self, selector: str, text: str, clear: bool) -> dict:
        """输入文本"""
        if not selector or not text:
            return {"success": False, "error": "selector and text are required"}
        
        if clear:
            await self._page.fill(selector, text)
        else:
            await self._page.type(selector, text)
        
        return {
            "success": True,
            "result": f"Typed into {selector}: {text[:50]}..."
        }
    
    async def _screenshot(self, full_page: bool, path: Optional[str]) -> dict:
        """截图"""
        # 检查是否在空白页
        current_url = self._page.url
        if current_url == "about:blank":
            return {
                "success": False,
                "error": "当前页面是空白页 (about:blank)，请先使用 browser_navigate 打开一个网页"
            }
        
        # 等待页面稳定（等待网络空闲或最多 3 秒）
        try:
            await self._page.wait_for_load_state("networkidle", timeout=3000)
        except:
            pass  # 超时也继续截图
        
        # 额外等待一点时间让页面渲染完成
        await asyncio.sleep(0.5)
        
        screenshot_bytes = await self._page.screenshot(full_page=full_page)
        
        if path:
            Path(path).write_bytes(screenshot_bytes)
            return {
                "success": True,
                "result": f"Screenshot saved to: {path}"
            }
        else:
            # 返回 base64
            b64 = base64.b64encode(screenshot_bytes).decode()
            return {
                "success": True,
                "result": {
                    "base64": b64[:100] + "...",  # 截断显示
                    "length": len(b64),
                }
            }
    
    async def _get_content(self, selector: Optional[str], format: str) -> dict:
        """获取内容"""
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
        
        return {
            "success": True,
            "result": content[:5000] + "..." if len(content) > 5000 else content
        }
    
    async def _wait(self, selector: Optional[str], timeout: int) -> dict:
        """等待"""
        if selector:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return {"success": True, "result": f"Element appeared: {selector}"}
        else:
            await asyncio.sleep(timeout / 1000)
            return {"success": True, "result": f"Waited {timeout}ms"}
    
    async def _scroll(self, direction: str, amount: int) -> dict:
        """滚动"""
        if direction == "up":
            amount = -amount
        
        await self._page.evaluate(f"window.scrollBy(0, {amount})")
        
        return {
            "success": True,
            "result": f"Scrolled {direction} by {abs(amount)}px"
        }
    
    async def _execute_js(self, script: str) -> dict:
        """执行 JavaScript"""
        if not script:
            return {"success": False, "error": "script is required"}
        
        result = await self._page.evaluate(script)
        
        return {
            "success": True,
            "result": result
        }
    
    @property
    def is_started(self) -> bool:
        return self._started
    
    @property
    def current_url(self) -> Optional[str]:
        return self._page.url if self._page else None


# 单例
_browser_mcp: Optional[BrowserMCP] = None


def get_browser_mcp(headless: bool = False) -> BrowserMCP:
    """获取 BrowserMCP 单例"""
    global _browser_mcp
    if _browser_mcp is None:
        _browser_mcp = BrowserMCP(headless=headless)
    return _browser_mcp
