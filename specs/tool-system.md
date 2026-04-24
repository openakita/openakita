# Tool System Specification

## Overview

工具系统提供 OpenAkita 与外部世界交互的能力。

## Jobs to Be Done

1. 执行 Shell 命令
2. 读写文件
3. 发送网络请求
4. 控制浏览器
5. 调用 MCP 服务器

## Components

### ShellTool (`tools/shell.py`)

执行系统命令。

```python
class ShellTool:
    async def run(self, command: str, cwd: str = None) -> CommandResult
    async def run_interactive(self, command: str) -> AsyncIterator[str]
```

### FileTool (`tools/file.py`)

文件操作。

```python
class FileTool:
    async def read(self, path: str) -> str
    async def write(self, path: str, content: str)
    async def append(self, path: str, content: str)
    async def delete(self, path: str)
    async def list_dir(self, path: str) -> list[str]
    async def search(self, pattern: str, path: str) -> list[str]
```

### WebTool (`tools/web.py`)

网络请求。

```python
class WebTool:
    async def get(self, url: str) -> Response
    async def post(self, url: str, data: dict) -> Response
    async def download(self, url: str, path: str)
    async def search(self, query: str) -> list[SearchResult]
```

### BrowserTool (`tools/browser.py`)

浏览器自动化（使用 Playwright）。

```python
class BrowserTool:
    async def navigate(self, url: str)
    async def click(self, selector: str)
    async def type(self, selector: str, text: str)
    async def screenshot(self, path: str)
    async def get_content(self) -> str
```

### MCPBridge (`tools/mcp.py`)

MCP 服务器桥接。

```python
class MCPBridge:
    servers: dict[str, MCPServer]
    
    async def call_tool(self, server: str, tool: str, args: dict) -> Any
    async def list_tools(self, server: str) -> list[ToolInfo]
    def load_server(self, config: dict)
```

## Acceptance Criteria

- [ ] Shell 可以执行命令并返回结果
- [ ] File 可以读写文件
- [ ] Web 可以发送 HTTP 请求
- [ ] Browser 可以自动化网页操作
- [ ] MCP 可以调用已配置的 MCP 服务器

