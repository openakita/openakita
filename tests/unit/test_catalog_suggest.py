"""Tests for tool-not-found suggestions in ToolCatalog (A1).

Validates that ``get_tool_info_formatted`` for an unknown tool gives:
- difflib-based close-match suggestions when applicable;
- an org-only hint when the catalog only contains org_* tools;
- a generic "stop probing" hint otherwise.
"""

from openakita.tools.catalog import ToolCatalog


def _make_org_only_catalog() -> ToolCatalog:
    return ToolCatalog([
        {"name": "org_delegate_task", "description": "Delegate task"},
        {"name": "org_send_message", "description": "Send msg"},
        {"name": "org_submit_deliverable", "description": "Submit"},
        {"name": "org_request_tools", "description": "Request"},
    ])


def _make_mixed_catalog() -> ToolCatalog:
    return ToolCatalog([
        {"name": "write_file_v2", "description": "Write v2"},
        {"name": "read_file", "description": "Read"},
        {"name": "org_delegate_task", "description": "Delegate"},
    ])


class TestNotFoundSuggestion:
    def test_close_match_suggested(self):
        catalog = _make_mixed_catalog()
        msg = catalog.get_tool_info_formatted("write_file")
        assert "write_file_v2" in msg
        assert "get_tool_info('write_file_v2')" in msg
        assert "❌" in msg

    def test_org_only_catalog_emits_org_hint(self):
        catalog = _make_org_only_catalog()
        msg = catalog.get_tool_info_formatted("write_file")
        # No close match in this org-only catalog, so we want the org-only hint.
        assert "org_*" in msg or "org_" in msg
        assert "请停止探查" in msg
        assert "org_delegate_task" in msg

    def test_unknown_tool_in_mixed_catalog_no_close_match(self):
        catalog = _make_mixed_catalog()
        msg = catalog.get_tool_info_formatted("totally_unrelated_xyz")
        assert "❌ Tool not found: totally_unrelated_xyz" in msg
        assert "请勿继续探查" in msg or "请停止探查" in msg

    def test_get_tool_info_dict_path_unchanged(self):
        """get_tool_info() (dict-returning Level-2) must keep returning None."""
        catalog = _make_mixed_catalog()
        assert catalog.get_tool_info("totally_unrelated_xyz") is None

    def test_known_tool_lookup_unchanged(self):
        catalog = _make_mixed_catalog()
        msg = catalog.get_tool_info_formatted("read_file")
        assert "Tool: read_file" in msg
        assert "Tool not found" not in msg
