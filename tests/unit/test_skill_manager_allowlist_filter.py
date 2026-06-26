from __future__ import annotations

import json
from pathlib import Path

import pytest


class _FakeLoader:
    def __init__(self) -> None:
        self.load_filter = None
        self.pruned = None

    def compute_effective_allowlist(self, external_allowlist):
        return external_allowlist

    def build_preparse_allowlist_filter(self, external_allowlist, *, agent_referenced_skills=None):
        def _filter(_path: Path) -> bool:
            return True

        self.filter_args = (external_allowlist, agent_referenced_skills)
        return _filter

    def load_all(self, _root, *, load_filter=None):
        self.load_filter = load_filter
        return 3

    def prune_external_by_allowlist(self, external_allowlist, agent_referenced_skills=None):
        self.pruned = (external_allowlist, agent_referenced_skills)
        return 0


class _FakeCatalog:
    skill_count = 3

    def generate_catalog(self):
        return "catalog"


@pytest.mark.asyncio
async def test_skill_manager_passes_allowlist_filter_before_loading(tmp_path, monkeypatch):
    from openakita.config import settings
    from openakita.core.skill_manager import SkillManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "skills.json").write_text(
        json.dumps({"version": 1, "external_allowlist": ["keep-me"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "project_root", tmp_path, raising=False)
    monkeypatch.setattr(
        "openakita.skills.preset_utils.collect_preset_referenced_skills",
        lambda: {"preset-only"},
    )

    loader = _FakeLoader()
    manager = SkillManager(None, loader, _FakeCatalog(), None)

    await manager.load_installed_skills()

    assert loader.filter_args == ({"keep-me"}, {"preset-only"})
    assert loader.load_filter is not None
    assert loader.pruned == ({"keep-me"}, {"preset-only"})
    assert manager.catalog_text == "catalog"
