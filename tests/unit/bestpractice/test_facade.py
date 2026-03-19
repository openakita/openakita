"""BP facade integration tests — test the full initialization flow."""

import pytest
from pathlib import Path

from seeagent.bestpractice.facade import (
    init_bp_system,
    get_bp_engine,
    get_bp_handler,
    get_bp_state_manager,
    get_static_prompt_section,
    get_dynamic_prompt_section,
)
import seeagent.bestpractice.facade as facade


@pytest.fixture(autouse=True)
def reset_facade():
    """Reset singleton state between tests."""
    facade._initialized = False
    facade._bp_engine = None
    facade._bp_handler = None
    facade._bp_state_manager = None
    facade._bp_config_loader = None
    facade._bp_context_bridge = None
    facade._bp_prompt_loader = None
    yield
    facade._initialized = False
    facade._bp_engine = None
    facade._bp_handler = None
    facade._bp_state_manager = None
    facade._bp_config_loader = None
    facade._bp_context_bridge = None
    facade._bp_prompt_loader = None


@pytest.fixture
def bp_base_path():
    return Path(__file__).parents[3] / "best_practice"


class TestFacadeInit:
    def test_init_with_real_configs(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        result = init_bp_system(search_paths=[bp_base_path])
        assert result is True

        engine = get_bp_engine()
        assert engine is not None

        handler = get_bp_handler()
        assert handler is not None
        assert len(handler.config_registry) >= 4

        state_mgr = get_bp_state_manager()
        assert state_mgr is not None

    def test_init_no_configs(self, tmp_path):
        result = init_bp_system(search_paths=[tmp_path])
        assert result is False
        assert get_bp_handler() is None

    def test_static_prompt_section(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        section = get_static_prompt_section()
        assert "最佳实践" in section
        assert "content-pipeline" in section or "内容创作" in section

    def test_dynamic_prompt_section_empty_session(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        section = get_dynamic_prompt_section("nonexistent-session")
        # No instances → empty
        assert section == ""

    def test_dynamic_prompt_with_instance(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        mgr = get_bp_state_manager()
        handler = get_bp_handler()

        # Create an instance
        config = list(handler.config_registry.values())[0]
        mgr.create_instance(config, "test-session", {"topic": "test"})

        section = get_dynamic_prompt_section("test-session")
        assert "active" in section
        assert "test-session" not in section  # session_id not in output
