from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.tools.mcp import MCPClient
from openakita.tools.mcp_catalog import MCPCatalog
from openakita.tools.mcp_workspace import reload_all_servers

BUILTIN_MCPS = Path(__file__).resolve().parents[2] / "mcps"


@pytest.mark.asyncio
async def test_parallel_search_loads_without_credentials_or_auto_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("PARALLEL_API_TOKEN", raising=False)
    catalog = MCPCatalog()
    client = MCPClient()

    await reload_all_servers(client, catalog, [BUILTIN_MCPS])

    server = catalog.get_server("parallel-search")
    assert server is not None
    assert server.auto_connect is False
    assert server.config_schema == []
    assert server.env == {}
    assert client.list_connected() == []
    config = client.get_server_config("parallel-search")
    assert config is not None
    assert config.transport == "streamable_http"
    assert config.url == "https://search.parallel.ai/mcp"
    assert config.headers == {"User-Agent": "OpenAkita (https://github.com/openakita/openakita)"}
    assert 'connect_mcp_server("parallel-search")' in catalog.generate_catalog()
    assert '"tool_name": "web_search"' in server.instructions
    assert '"tool_name": "web_fetch"' in server.instructions


@pytest.mark.asyncio
async def test_parallel_search_preserves_workspace_selection(tmp_path: Path) -> None:
    workspace = tmp_path / "mcps"
    server_dir = workspace / "parallel-search"
    server_dir.mkdir(parents=True)
    metadata = {
        "serverIdentifier": "parallel-search",
        "serverName": "My search server",
        "transport": "streamable_http",
        "url": "https://example.org/my-mcp",
        "headers": {"User-Agent": "My client"},
        "enabled": False,
        "autoConnect": False,
    }
    metadata_path = server_dir / "SERVER_METADATA.json"
    original = json.dumps(metadata)
    metadata_path.write_text(original, encoding="utf-8")
    client = MCPClient()
    catalog = MCPCatalog()

    await reload_all_servers(client, catalog, [workspace, BUILTIN_MCPS])

    server = catalog.get_server("parallel-search")
    assert server is not None
    assert server.enabled is False
    assert "parallel-search" not in catalog.generate_catalog()
    config = client.get_server_config("parallel-search")
    assert config is not None
    assert config.url == metadata["url"]
    assert config.headers == metadata["headers"]
    assert metadata_path.read_text(encoding="utf-8") == original
