"""L1 Unit Tests: Tool catalog generation and progressive disclosure."""

import pytest

from openakita.tools.catalog import ToolCatalog, create_tool_catalog


def _sample_tools() -> list[dict]:
    return [
        {
            "name": "read_file",
            "category": "filesystem",
            "description": "Read a file",
            "detail": "Read the contents of a file at the given path.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "category": "filesystem",
            "description": "Write a file",
            "detail": "Write content to a file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "web_search",
            "category": "search",
            "description": "Search the web",
            "detail": "Perform a web search.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]


class TestToolCatalogCreation:
    def test_create_with_tools(self):
        catalog = ToolCatalog(_sample_tools())
        assert catalog.list_tools() == ["read_file", "write_file", "web_search"]

    def test_create_tool_catalog_factory(self):
        catalog = create_tool_catalog(_sample_tools())
        assert isinstance(catalog, ToolCatalog)

    def test_empty_tools(self):
        catalog = ToolCatalog([])
        assert catalog.list_tools() == []


class TestToolLookup:
    def test_has_tool(self):
        catalog = ToolCatalog(_sample_tools())
        assert catalog.has_tool("read_file") is True
        assert catalog.has_tool("nonexistent") is False

    def test_get_tool_info(self):
        catalog = ToolCatalog(_sample_tools())
        info = catalog.get_tool_info("read_file")
        assert info is not None
        assert info["name"] == "read_file"

    def test_get_tool_info_missing(self):
        catalog = ToolCatalog(_sample_tools())
        info = catalog.get_tool_info("nonexistent")
        assert info is None

    def test_get_tool_info_formatted(self):
        catalog = ToolCatalog(_sample_tools())
        formatted = catalog.get_tool_info_formatted("read_file")
        assert isinstance(formatted, str)
        assert "read_file" in formatted


class TestToolMutation:
    def test_add_tool(self):
        catalog = ToolCatalog(_sample_tools())
        catalog.add_tool({
            "name": "new_tool",
            "category": "custom",
            "description": "New tool",
            "detail": "Detail",
            "input_schema": {"type": "object", "properties": {}},
        })
        assert catalog.has_tool("new_tool") is True

    def test_remove_tool(self):
        catalog = ToolCatalog(_sample_tools())
        result = catalog.remove_tool("read_file")
        assert result is True
        assert catalog.has_tool("read_file") is False

    def test_remove_nonexistent(self):
        catalog = ToolCatalog(_sample_tools())
        result = catalog.remove_tool("nonexistent")
        assert result is False

    def test_update_tools(self):
        catalog = ToolCatalog([])
        catalog.update_tools(_sample_tools())
        assert len(catalog.list_tools()) == 3


class TestCatalogGeneration:
    def test_generate_catalog_returns_string(self):
        catalog = ToolCatalog(_sample_tools())
        text = catalog.generate_catalog()
        assert isinstance(text, str)
        assert len(text) > 0

    def test_catalog_contains_some_tool_info(self):
        catalog = ToolCatalog(_sample_tools())
        text = catalog.generate_catalog()
        assert len(text) > 0
        assert "web_search" in text or "search" in text

    def test_get_direct_tool_schemas(self):
        catalog = ToolCatalog(_sample_tools())
        schemas = catalog.get_direct_tool_schemas()
        assert isinstance(schemas, list)

