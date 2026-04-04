"""
LSP 工具处理器

通过 Language Server Protocol 提供代码智能功能。
启动一个 LSP 子进程，通过 stdin/stdout JSON-RPC 通信。

安全设计：
- 每个语言服务器独立 asyncio.Lock 防并发竞态
- 响应读取循环匹配 request id，跳过中间 notification/log
- 10MB 文件大小限制
"""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
RESPONSE_TIMEOUT = 30  # seconds

_LSP_SERVERS: dict[str, list[str]] = {
    ".py": ["pyright-langserver", "--stdio"],
    ".ts": ["typescript-language-server", "--stdio"],
    ".tsx": ["typescript-language-server", "--stdio"],
    ".js": ["typescript-language-server", "--stdio"],
    ".jsx": ["typescript-language-server", "--stdio"],
    ".go": ["gopls", "serve"],
    ".rs": ["rust-analyzer"],
    ".java": ["jdtls"],
    ".c": ["clangd"],
    ".cpp": ["clangd"],
    ".h": ["clangd"],
}

_request_id = 0


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


def _detect_server(file_path: str) -> list[str] | None:
    ext = Path(file_path).suffix.lower()
    server_cmd = _LSP_SERVERS.get(ext)
    if not server_cmd:
        return None
    if not shutil.which(server_cmd[0]):
        return None
    return server_cmd


class _LSPConnection:
    """Wraps a single LSP subprocess with a mutex for safe request/response."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self._lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.process.returncode is None

    async def request(self, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request and read the matching response.

        Uses a lock to prevent interleaved reads from concurrent callers.
        Skips notifications (messages without 'id') until the matching
        response for our request_id is found.
        """
        async with self._lock:
            return await self._request_locked(method, params)

    async def _request_locked(self, method: str, params: dict) -> dict | None:
        msg_id = _next_id()
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
        })
        header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
        payload = (header + body).encode("utf-8")

        stdin = self.process.stdin
        stdout = self.process.stdout
        if not stdin or not stdout:
            return None

        stdin.write(payload)
        await stdin.drain()

        # Read responses in a loop, skipping notifications until we find our id
        deadline = asyncio.get_event_loop().time() + RESPONSE_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(f"[LSP] Timeout waiting for response to {method} (id={msg_id})")
                return None

            msg = await self._read_one_message(stdout, remaining)
            if msg is None:
                return None

            # Notification (no id) — skip
            if "id" not in msg:
                continue

            # Response for a different request — skip (stale)
            if msg.get("id") != msg_id:
                logger.debug(f"[LSP] Skipping response id={msg.get('id')}, want {msg_id}")
                continue

            if "error" in msg:
                err = msg["error"]
                logger.debug(f"[LSP] Error response: {err}")
                return None

            return msg.get("result")

    @staticmethod
    async def _read_one_message(
        stdout: asyncio.StreamReader, timeout: float,
    ) -> dict | None:
        """Read one complete JSON-RPC message from the stream."""
        try:
            # Read headers until empty line
            content_length = 0
            while True:
                line = await asyncio.wait_for(stdout.readline(), timeout=timeout)
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    break  # empty line = end of headers
                if line_str.lower().startswith("content-length:"):
                    content_length = int(line_str.split(":")[1].strip())

            if content_length <= 0:
                return None

            body_bytes = await asyncio.wait_for(
                stdout.readexactly(content_length), timeout=timeout,
            )
            return json.loads(body_bytes.decode("utf-8"))
        except Exception as e:
            logger.debug(f"[LSP] Read error: {e}")
            return None

    async def notify(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
        if self.process.stdin:
            self.process.stdin.write((header + body).encode("utf-8"))
            await self.process.stdin.drain()


# Per-language-server connection cache
_connections: dict[str, _LSPConnection] = {}


class LSPHandler:
    """LSP 工具处理器"""

    TOOLS = ["lsp"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "lsp":
            return await self._lsp(params)
        return f"Unknown LSP tool: {tool_name}"

    async def _lsp(self, params: dict[str, Any]) -> str:
        operation = params.get("operation", "")
        file_path = params.get("filePath", "")
        line = params.get("line", 1)
        character = params.get("character", 1)
        query = params.get("query", "")

        if not operation:
            return "lsp requires 'operation' parameter."

        # workspaceSymbol doesn't need a file
        if operation != "workspaceSymbol":
            if not file_path:
                return "lsp requires 'filePath' parameter for this operation."
            fp = Path(file_path)
            if not fp.exists():
                return f"File not found: {file_path}"
            if fp.stat().st_size > MAX_FILE_SIZE:
                return f"File too large (>{MAX_FILE_SIZE // (1024*1024)}MB): {file_path}"
        else:
            fp = Path(file_path) if file_path else None

        detect_path = file_path or "dummy.py"
        server_cmd = _detect_server(detect_path)
        if not server_cmd:
            ext = Path(detect_path).suffix
            return (
                f"No LSP server available for {ext} files. "
                f"Install the appropriate language server."
            )

        try:
            conn = await self._get_or_start(server_cmd, fp)
        except Exception as e:
            return f"Failed to start LSP server: {e}"

        uri = fp.as_uri() if fp else ""
        pos = {"line": max(0, line - 1), "character": max(0, character - 1)}

        try:
            result = await self._dispatch(conn, operation, uri, pos, query)
            if result is None:
                return f"No results for {operation} at {file_path}:{line}:{character}"
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)[:8000]
        except Exception as e:
            logger.error(f"[LSP] Operation {operation} failed: {e}")
            return f"LSP operation failed: {e}"

    async def _dispatch(
        self,
        conn: _LSPConnection,
        operation: str,
        uri: str,
        pos: dict,
        query: str,
    ) -> Any:
        td = {"uri": uri} if uri else {}
        td_pos = {"textDocument": td, "position": pos} if uri else {}

        if operation == "goToDefinition":
            return await conn.request("textDocument/definition", td_pos)
        elif operation == "findReferences":
            return await conn.request("textDocument/references", {
                **td_pos, "context": {"includeDeclaration": True},
            })
        elif operation == "hover":
            return await conn.request("textDocument/hover", td_pos)
        elif operation == "documentSymbol":
            return await conn.request("textDocument/documentSymbol", {"textDocument": td})
        elif operation == "workspaceSymbol":
            return await conn.request("workspace/symbol", {"query": query or ""})
        elif operation == "goToImplementation":
            return await conn.request("textDocument/implementation", td_pos)
        elif operation == "prepareCallHierarchy":
            return await conn.request("textDocument/prepareCallHierarchy", td_pos)
        elif operation in ("incomingCalls", "outgoingCalls"):
            prep = await conn.request("textDocument/prepareCallHierarchy", td_pos)
            if not prep or not isinstance(prep, list) or not prep:
                return None
            method = f"callHierarchy/{operation}"
            return await conn.request(method, {"item": prep[0]})
        else:
            return None

    async def _get_or_start(
        self, server_cmd: list[str], file_path: Path | None,
    ) -> _LSPConnection:
        cache_key = server_cmd[0]

        conn = _connections.get(cache_key)
        if conn and conn.alive:
            if file_path:
                await self._open_file(conn, file_path)
            return conn

        cwd = getattr(self.agent, "default_cwd", None) or os.getcwd()
        process = await asyncio.create_subprocess_exec(
            *server_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        conn = _LSPConnection(process)
        root_uri = Path(cwd).as_uri()
        await conn.request("initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {},
        })
        await conn.notify("initialized", {})

        if file_path:
            await self._open_file(conn, file_path)

        _connections[cache_key] = conn
        logger.info(f"[LSP] Started server: {' '.join(server_cmd)}")
        return conn

    @staticmethod
    async def _open_file(conn: _LSPConnection, file_path: Path) -> None:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_SIZE]
        except Exception:
            return
        await conn.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_path.as_uri(),
                "languageId": file_path.suffix.lstrip("."),
                "version": 1,
                "text": text,
            },
        })


def create_handler(agent: "Agent"):
    handler = LSPHandler(agent)
    return handler.handle
