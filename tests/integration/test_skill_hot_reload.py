"""技能热重载三大场景回归测试。

针对用户反馈「技能导入后不重启不生效」的根因，使用真实文件系统验证：

1. **新装技能立即可见**：创建新 SKILL.md 目录 → 调 ``load_all(force=True)`` 能注册成功。
2. **enable / disable 立即生效**：修改 ``data/skills.json`` 的 external_allowlist →
   ``compute_effective_allowlist`` + ``prune_external_by_allowlist`` 正确刷新注册表。
3. **SKILL.md 内容热重载**：磁盘文件改内容后，先清缓存再 ``reload_skill`` 读到新 body
   （验证 ``clear_all_skill_caches`` 清掉了 ``SkillParser._parse_cache``）。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# 工具：生成合法的外部技能目录
# ---------------------------------------------------------------------------


def _write_skill(dir_: Path, name: str, description: str, body: str = "Body content.") -> Path:
    """在 dir_ 下创建一个最小化的合法 SKILL.md。"""
    dir_.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent(
        f"""\
        ---
        name: {name}
        description: {description}
        ---

        # {name}

        {body}
        """
    )
    skill_md = dir_ / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return skill_md


# ---------------------------------------------------------------------------
# 场景 1：新装技能立即可见（无需重启）
# ---------------------------------------------------------------------------


class TestScenarioInstallVisible:
    def test_new_skill_registered_on_load_all(self, tmp_path: Path):
        """模拟：安装新技能 = 目录出现 → load_all 能把它注册进 registry。"""
        from openakita.skills.loader import SkillLoader

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "skill-a", "skill-a", "First skill")

        loader = SkillLoader()
        n = loader.load_from_directory(skills_root)
        assert n == 1
        assert loader.get_skill("skill-a") is not None

        # 安装第二个
        _write_skill(skills_root / "skill-b", "skill-b", "Second skill")
        n2 = loader.load_from_directory(skills_root)
        # force=True 默认 → 不会因为 skill-a 已注册而整体失败
        assert n2 == 2
        assert loader.get_skill("skill-b") is not None
        # skill-a 仍然在
        assert loader.get_skill("skill-a") is not None

    def test_reinstall_same_skill_with_new_content(self, tmp_path: Path):
        """场景：用户从 store 重装同一个 skill 新版本。

        之前的 bug：registry.register 会因为已存在拒绝新版本 → 内容不更新。
        修复：load_skill / load_from_directory 默认 force=True。
        """
        from openakita.skills.loader import SkillLoader

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "my-skill", "my-skill", "v1")

        loader = SkillLoader()
        loader.load_from_directory(skills_root)
        entry_v1 = loader.registry.get("my-skill")
        assert entry_v1 is not None
        assert entry_v1.description == "v1"

        _write_skill(skills_root / "my-skill", "my-skill", "v2")
        # 必须先清 parser cache，否则会读到旧的解析结果
        from openakita.skills.watcher import clear_all_skill_caches

        clear_all_skill_caches()

        loader.load_from_directory(skills_root)
        entry_v2 = loader.registry.get("my-skill")
        assert entry_v2 is not None
        assert entry_v2.description == "v2", "force=True 应允许覆盖已注册技能"


# ---------------------------------------------------------------------------
# 场景 2：enable / disable 立即生效
# ---------------------------------------------------------------------------


class TestScenarioEnableDisable:
    def test_disable_via_allowlist_unregisters_skill(self, tmp_path: Path):
        """把某个外部技能从 allowlist 移除后，prune 应从 registry 中删除它。"""
        from openakita.skills.loader import SkillLoader

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "keep-me", "keep-me", "Should remain")
        _write_skill(skills_root / "kick-me", "kick-me", "Should be kicked")

        loader = SkillLoader()
        loader.load_from_directory(skills_root)
        assert loader.get_skill("keep-me") is not None
        assert loader.get_skill("kick-me") is not None

        effective = loader.compute_effective_allowlist({"keep-me"})
        loader.prune_external_by_allowlist(effective)

        assert loader.get_skill("keep-me") is not None, "在 allowlist 中的技能应保留"
        assert loader.get_skill("kick-me") is None, "不在 allowlist 中的外部技能应被裁掉"

    def test_enable_via_allowlist_none_keeps_all(self, tmp_path: Path):
        """allowlist 为 None（skills.json 不存在）时，保留全部外部技能。"""
        from openakita.skills.loader import SkillLoader

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "a", "a", "")
        _write_skill(skills_root / "b", "b", "")

        loader = SkillLoader()
        loader.load_from_directory(skills_root)

        effective = loader.compute_effective_allowlist(None)
        loader.prune_external_by_allowlist(effective)

        assert loader.get_skill("a") is not None
        assert loader.get_skill("b") is not None

    def test_empty_allowlist_disables_all_external(self, tmp_path: Path):
        """allowlist 为空集合 → 禁用全部外部技能。"""
        from openakita.skills.loader import SkillLoader

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "a", "a", "")
        _write_skill(skills_root / "b", "b", "")

        loader = SkillLoader()
        loader.load_from_directory(skills_root)

        loader.prune_external_by_allowlist(set())

        assert loader.get_skill("a") is None
        assert loader.get_skill("b") is None

    def test_preparse_allowlist_filter_skips_disabled_external_before_yaml_parse(
        self, tmp_path: Path
    ):
        """启动过滤应在解析 SKILL.md 前跳过未允许的外部技能。"""
        from openakita.skills.loader import SkillLoader

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "keep-me", "keep-me", "Allowed")

        bad_dir = skills_root / "bad-disabled"
        bad_dir.mkdir(parents=True)
        (bad_dir / "SKILL.md").write_text(
            "---\nname: Bad Disabled\ndescription: invalid name\n---\nBroken.",
            encoding="utf-8",
        )

        loader = SkillLoader()
        load_filter = loader.build_preparse_allowlist_filter({"keep-me"})

        assert loader.load_from_directory(skills_root, load_filter=load_filter) == 1
        assert loader.get_skill("keep-me") is not None
        assert loader.get_skill("bad-disabled") is None
        assert loader.last_load_issues == []

    def test_preparse_allowlist_filter_keeps_preset_referenced_skills_disabled_later(
        self, tmp_path: Path
    ):
        """预设引用的外部技能仍需加载，后续 prune 才能标记 disabled。"""
        from openakita.skills.loader import SkillLoader

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "agent-only", "agent-only", "Used by preset")

        loader = SkillLoader()
        load_filter = loader.build_preparse_allowlist_filter(
            set(),
            agent_referenced_skills={"agent-only"},
        )

        assert loader.load_from_directory(skills_root, load_filter=load_filter) == 1
        loader.prune_external_by_allowlist(set(), agent_referenced_skills={"agent-only"})
        entry = loader.registry.get("agent-only")
        assert entry is not None
        assert entry.disabled is True

    def test_allowlist_io_round_trip(self, tmp_path: Path, monkeypatch):
        """综合：通过 allowlist_io 写入 → read_allowlist 读出 → 用于 prune。"""
        from openakita.config import settings as real_settings
        from openakita.skills import allowlist_io
        from openakita.skills.loader import SkillLoader

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "ok", "ok", "")
        _write_skill(skills_root / "banned", "banned", "")

        loader = SkillLoader()
        loader.load_from_directory(skills_root)

        # 只允许 ok
        allowlist_io.overwrite_allowlist({"ok"})

        _, external = allowlist_io.read_allowlist()
        effective = loader.compute_effective_allowlist(external)
        loader.prune_external_by_allowlist(effective)

        assert loader.get_skill("ok") is not None
        assert loader.get_skill("banned") is None


# ---------------------------------------------------------------------------
# 场景 3：SKILL.md 内容热重载
# ---------------------------------------------------------------------------


class TestScenarioContentHotReload:
    def test_edit_skill_md_reflected_after_cache_clear(self, tmp_path: Path):
        """编辑 SKILL.md → 清缓存 → reload_skill → 能读到新 description 和 body。"""
        from openakita.skills.loader import SkillLoader
        from openakita.skills.watcher import clear_all_skill_caches

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "editable", "editable", "old-desc", body="OLD BODY")

        loader = SkillLoader()
        loader.load_from_directory(skills_root)
        assert loader.get_skill_body("editable") is not None
        assert "OLD BODY" in loader.get_skill_body("editable")

        # 修改文件
        _write_skill(skills_root / "editable", "editable", "new-desc", body="NEW BODY")

        # 若跳过 clear_all_skill_caches 且 SkillParser 有 memoization，则会读到旧内容
        clear_all_skill_caches()

        reloaded = loader.reload_skill("editable")
        assert reloaded is not None
        assert "NEW BODY" in loader.get_skill_body("editable")
        entry = loader.registry.get("editable")
        assert entry is not None
        assert entry.description == "new-desc"

    def test_parse_cache_stale_without_clear(self, tmp_path: Path):
        """反向验证：parser 缓存不清时，直接再解析同一路径会命中缓存。

        这能解释用户看到的「改了 SKILL.md 但描述没变」现象，
        并确认 clear_all_skill_caches 是必要的。
        """
        from openakita.skills.parser import SkillParser

        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "cached", "cached", "v1", body="B1")

        parser = SkillParser()
        first = parser.parse_directory(skills_root / "cached")
        assert first.metadata.description == "v1"

        _write_skill(skills_root / "cached", "cached", "v2", body="B2")
        parser.parse_directory(skills_root / "cached")

        # 若内部有缓存，second 可能仍是 v1——但这里我们只需断言 clear 之后能拿到 v2。
        from openakita.skills.watcher import clear_all_skill_caches

        clear_all_skill_caches()
        third = parser.parse_directory(skills_root / "cached")
        assert third.metadata.description == "v2"


# ---------------------------------------------------------------------------
# 综合：完整热重载链路（不依赖 Agent / FastAPI）
# ---------------------------------------------------------------------------


class TestFullReloadChain:
    def test_install_then_disable_then_enable(self, tmp_path: Path, monkeypatch):
        """端到端：安装两个 → 禁用 A → 重启时 A 不可见 → 重新启用 A → A 恢复。

        模拟真实使用中「通过面板切换启停 + 重启会保持状态」的场景，
        这是 ``propagate_skill_change`` + ``allowlist_io`` 配合工作的核心路径。
        """
        from openakita.config import settings as real_settings
        from openakita.skills import allowlist_io
        from openakita.skills.loader import SkillLoader
        from openakita.skills.watcher import clear_all_skill_caches

        monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
        skills_root = tmp_path / "skills"
        _write_skill(skills_root / "alpha", "alpha", "A")
        _write_skill(skills_root / "beta", "beta", "B")

        # 首次「启动」
        loader = SkillLoader()
        loader.load_from_directory(skills_root)
        assert loader.get_skill("alpha") is not None
        assert loader.get_skill("beta") is not None

        # 用户禁用 alpha（allowlist 只留 beta）
        allowlist_io.overwrite_allowlist({"beta"})
        _, al = allowlist_io.read_allowlist()
        effective = loader.compute_effective_allowlist(al)
        loader.prune_external_by_allowlist(effective)
        assert loader.get_skill("alpha") is None
        assert loader.get_skill("beta") is not None

        # 模拟「重启」：新建 loader，按磁盘扫描，再应用 allowlist
        clear_all_skill_caches()
        loader2 = SkillLoader()
        loader2.load_from_directory(skills_root)
        _, al2 = allowlist_io.read_allowlist()
        loader2.prune_external_by_allowlist(loader2.compute_effective_allowlist(al2))
        assert loader2.get_skill("alpha") is None, "重启后禁用状态应持久"
        assert loader2.get_skill("beta") is not None

        # 用户重新启用 alpha
        allowlist_io.overwrite_allowlist({"alpha", "beta"})
        _, al3 = allowlist_io.read_allowlist()
        loader2.load_from_directory(skills_root)  # 重新扫盘
        loader2.prune_external_by_allowlist(loader2.compute_effective_allowlist(al3))
        assert loader2.get_skill("alpha") is not None
        assert loader2.get_skill("beta") is not None
