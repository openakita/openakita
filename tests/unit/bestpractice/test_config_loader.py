"""BPConfigLoader tests."""

import json
import pytest
from pathlib import Path

from seeagent.bestpractice.config_loader import BPConfigLoader


@pytest.fixture
def bp_base_path():
    """返回项目的 best_practice/ 目录路径。"""
    return Path(__file__).parents[3] / "best_practice"


class TestBPConfigLoader:
    def test_load_real_configs(self, bp_base_path):
        """从真实 best_practice/ 目录加载所有配置。"""
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        loader = BPConfigLoader(search_paths=[bp_base_path])
        configs = loader.load_all()

        assert len(configs) >= 4
        assert "content-pipeline" in configs
        assert "competitor-analysis" in configs
        assert "market-research-report" in configs
        assert "technical-review" in configs

    def test_config_structure(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        loader = BPConfigLoader(search_paths=[bp_base_path])
        configs = loader.load_all()

        for bp_id, config in configs.items():
            assert config.id == bp_id
            assert config.name
            assert len(config.subtasks) >= 2
            for st in config.subtasks:
                assert st.id
                assert st.agent_profile

    def test_has_configs(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        loader = BPConfigLoader(search_paths=[bp_base_path])
        assert loader.has_configs()

    def test_empty_path(self, tmp_path):
        loader = BPConfigLoader(search_paths=[tmp_path])
        configs = loader.load_all()
        assert configs == {}
        assert not loader.has_configs()

    def test_skips_shared_dir(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        loader = BPConfigLoader(search_paths=[bp_base_path])
        configs = loader.load_all()
        # _shared 不应作为 BP config
        assert "_shared" not in configs

    def test_unwraps_best_practice_key(self, tmp_path):
        """兼容旧格式: config.yaml 有 best_practice: 包装。"""
        bp_dir = tmp_path / "my-bp"
        bp_dir.mkdir()
        (bp_dir / "config.yaml").write_text("""
best_practice:
  id: "wrapped"
  name: "包装"
  subtasks:
    - id: "s1"
      name: "S1"
      agent_profile: "a"
""")
        loader = BPConfigLoader(search_paths=[tmp_path])
        configs = loader.load_all()
        assert "wrapped" in configs


class TestPromptTemplateLoader:
    def test_render_static(self):
        from seeagent.bestpractice.prompt_loader import PromptTemplateLoader
        loader = PromptTemplateLoader()
        result = loader.render("system_static", bp_list="- 市场调研\n- 竞品分析")
        assert "市场调研" in result
        assert "竞品分析" in result

    def test_render_dynamic(self):
        from seeagent.bestpractice.prompt_loader import PromptTemplateLoader
        loader = PromptTemplateLoader()
        result = loader.render(
            "system_dynamic",
            status_table="| bp-001 | 测试 | active |",
            active_context="当前无活跃任务",
            intent_routing="",
        )
        assert "bp-001" in result
