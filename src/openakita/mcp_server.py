"""
MCP Server Mode

Exposes OpenAkita's core capabilities as an MCP server,
allowing other AI Agents (e.g., Claude Desktop, Cursor) to call them via the MCP protocol.

Exposed tools:
- openakita_chat: Chat with OpenAkita
- openakita_memory_search: Search memory
- openakita_schedule_task: Create scheduled tasks
- openakita_list_skills: List available skills
- openakita_execute_skill: Execute a skill

Usage:
    python -m openakita.mcp_server [--port 8765]
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = "openakita"
MCP_SERVER_VERSION = "1.0.0"

EXPOSED_TOOLS = [
    {
        "name": "openakita_chat",
        "description": "Send a message to OpenAkita and get a response",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to send"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "openakita_memory_search",
        "description": "Search OpenAkita's memory for relevant information",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "openakita_list_skills",
        "description": "List available OpenAkita skills",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


class MCPServer:
    """Lightweight MCP server that exposes OpenAkita capabilities via stdio."""

    def __init__(self):
        self._agent = None
        self._initialized = False

    async def _ensure_agent(self):
        if self._agent is not None:
            return
        from .core.agent import Agent

        self._agent = Agent()
        await self._agent.initialize(start_scheduler=False)
        self._initialized = True

    async def handle_request(self, request: dict) -> dict:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": MCP_SERVER_NAME,
                        "version": MCP_SERVER_VERSION,
                    },
                },
            }

        if method == "notifications/initialized":
            return None

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": EXPOSED_TOOLS},
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            try:
                result = await self._execute_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result}],
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    async def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        await self._ensure_agent()

        if tool_name == "openakita_chat":
            message = arguments.get("message", "")
            if not message:
                return "Error: message is required"
            response = await self._agent.chat(message)
            return response

        elif tool_name == "openakita_memory_search":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 5)
            mm = getattr(self._agent, "memory_manager", None)
            if not mm:
                return "Memory system not available"
            results = await mm.search(query, limit=limit)
            return json.dumps(results, ensure_ascii=False, indent=2)

        elif tool_name == "openakita_list_skills":
            registry = getattr(self._agent, "skill_registry", None)
            if not registry:
                return "Skill system not available"
            skills = registry.list_all()
            return "\n".join(
                f"- {s.name}: {s.description}" for s in skills
            )

        return f"Unknown tool: {tool_name}"

    async def run_stdio(self):
        """Run MCP server over stdio (for Claude Desktop / Cursor integration)."""
        logger.info("OpenAkita MCP Server starting on stdio...")

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(
            lambda: protocol, sys.stdin.buffer
        )

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout.buffer
        )
        writer = asyncio.StreamWriter(
            writer_transport, writer_protocol, reader, asyncio.get_event_loop()
        )

        while True:
            try:
                header = await reader.readline()
                if not header:
                    break

                header_str = header.decode().strip()
                if header_str.startswith("Content-Length:"):
                    content_length = int(header_str.split(":")[1].strip())
                    await reader.readline()  # empty line
                    body = await reader.readexactly(content_length)
                    request = json.loads(body)
                else:
                    continue

                response = await self.handle_request(request)

                if response is not None:
                    response_bytes = json.dumps(response).encode()
                    header_bytes = f"Content-Length: {len(response_bytes)}\r\n\r\n".encode()
                    writer.write(header_bytes + response_bytes)
                    await writer.drain()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"MCP Server error: {e}", exc_info=True)


async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    server = MCPServer()
    await server.run_stdio()


if __name__ == "__main__":
    asyncio.run(main())
