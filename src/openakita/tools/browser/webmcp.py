"""
WebMCP 接口预留

WebMCP 是 W3C 草案标准 (2026-02-10 Early Preview)，允许网站通过
navigator.modelContext.registerTool() 向 AI Agent 暴露结构化工具。

例如航空网站可以暴露 searchFlights(from, to, date)，
而不需要 Agent 猜测点击哪个按钮。

当前状态：
- W3C Early Preview Program (EPP)，仅限参与者
- 由 Google + Microsoft 联合在 W3C Web Machine Learning CG 下推进
- Chrome DevTools MCP 已支持发现页面上的 navigator.modelContext 注册的工具

此模块预留了 WebMCP 工具发现和调用接口，等标准成熟后填充实现。
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WebMCPTool:
    """
    网站通过 WebMCP 暴露的工具

    对应 W3C 草案中 navigator.modelContext.registerTool() 注册的工具。
    """

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    origin: str = ""  # 注册此工具的网站源（如 "https://www.united.com"）


@dataclass
class WebMCPDiscoveryResult:
    """WebMCP 工具发现结果"""

    url: str  # 当前页面 URL
    tools: list[WebMCPTool] = field(default_factory=list)
    supported: bool = False  # 页面是否支持 WebMCP


async def discover_webmcp_tools(backend: Any) -> WebMCPDiscoveryResult:
    """
    在当前页面发现 WebMCP 工具

    通过在页面中执行 JavaScript 检测 navigator.modelContext API
    并枚举注册的工具。

    Args:
        backend: BrowserBackend 实例，需支持 execute_js

    Returns:
        WebMCPDiscoveryResult
    """
    # 检测 navigator.modelContext 是否可用
    detect_script = """
    (() => {
        if (!navigator.modelContext) {
            return { supported: false, tools: [] };
        }
        try {
            const tools = navigator.modelContext.getRegisteredTools
                ? navigator.modelContext.getRegisteredTools()
                : [];
            return {
                supported: true,
                tools: tools.map(t => ({
                    name: t.name || '',
                    description: t.description || '',
                    inputSchema: t.inputSchema || {},
                }))
            };
        } catch (e) {
            return { supported: false, tools: [], error: e.message };
        }
    })()
    """

    try:
        result = await backend.execute_js(detect_script)
        if not result.get("success"):
            return WebMCPDiscoveryResult(url="", supported=False)

        data = result.get("result", {})
        if isinstance(data, str):
            import json
            data = json.loads(data)

        tools = []
        for tool_data in data.get("tools", []):
            tools.append(
                WebMCPTool(
                    name=tool_data.get("name", ""),
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                )
            )

        return WebMCPDiscoveryResult(
            url="",  # 调用者可以填充
            tools=tools,
            supported=data.get("supported", False),
        )

    except Exception as e:
        logger.debug(f"[WebMCP] Discovery failed: {e}")
        return WebMCPDiscoveryResult(url="", supported=False)


async def call_webmcp_tool(
    backend: Any,
    tool_name: str,
    arguments: dict,
) -> dict:
    """
    调用 WebMCP 工具

    通过在页面中执行 JavaScript 调用 navigator.modelContext.callTool()

    Args:
        backend: BrowserBackend 实例
        tool_name: 工具名称
        arguments: 参数

    Returns:
        {"success": bool, "result": Any, "error": str | None}
    """
    import json

    call_script = f"""
    (async () => {{
        if (!navigator.modelContext || !navigator.modelContext.callTool) {{
            return {{ success: false, error: 'WebMCP not available on this page' }};
        }}
        try {{
            const result = await navigator.modelContext.callTool(
                '{tool_name}',
                {json.dumps(arguments)}
            );
            return {{ success: true, result: result }};
        }} catch (e) {{
            return {{ success: false, error: e.message }};
        }}
    }})()
    """

    try:
        result = await backend.execute_js(call_script)
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "JS execution failed")}

        data = result.get("result", {})
        if isinstance(data, str):
            data = json.loads(data)

        return data

    except Exception as e:
        return {"success": False, "error": str(e)}
