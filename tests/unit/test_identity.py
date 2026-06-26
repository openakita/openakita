"""L1 Unit Tests: Identity document loading and prompt generation."""

from pathlib import Path

import pytest

import openakita.core.identity as identity_mod
from openakita.core.identity import (
    Identity,
    _file_hash,
    _save_hashes,
)

# The hash helpers (_file_hash / _save_hashes) DO exist in the canonical
# ``openakita.agent.identity`` and are re-exported by the core shim, so identity
# loading + hash round-trip tests run. What is NOT yet ported after the ADR-0003
# split is the custom-agent ``agent_voice`` parameter on ``get_system_prompt``
# and the ``sync_templates`` bundled-fallback rewrite (commits 1a3c3911 /
# 150ee738); the tests exercising those are skipped individually below. See
# docs/follow-ups/skipped-items-roadmap.md (Batch C — core/agent.py follow-ups).
_AGENT_VOICE_NOT_WIRED = (
    "Identity.get_system_prompt(agent_voice=...) not ported after ADR-0003 split; "
    "see docs/follow-ups/skipped-items-roadmap.md (Batch C)"
)


@pytest.fixture
def identity_dir(tmp_path):
    d = tmp_path / "identity"
    d.mkdir()
    (d / "SOUL.md").write_text(
        "# Soul\n\n你是 OpenAkita，一只忠诚的秋田犬AI助手。", encoding="utf-8"
    )
    (d / "AGENT.md").write_text(
        "# Agent\n\n## Core\n永不放弃。\n\n## Tooling\n善用工具。", encoding="utf-8"
    )
    (d / "USER.md").write_text("# User\n\n用户是一名开发者。", encoding="utf-8")
    (d / "MEMORY.md").write_text("# Memory\n\n用户喜欢 Python。", encoding="utf-8")
    return d


class TestIdentityLoading:
    def test_load_all_documents(self, identity_dir):
        identity = Identity(
            soul_path=identity_dir / "SOUL.md",
            agent_path=identity_dir / "AGENT.md",
            user_path=identity_dir / "USER.md",
            memory_path=identity_dir / "MEMORY.md",
        )
        identity.load()
        assert "OpenAkita" in identity.soul or "秋田犬" in identity.soul
        assert len(identity.agent) > 0
        assert "开发者" in identity.user

    def test_load_missing_file(self, tmp_path):
        identity = Identity(soul_path=tmp_path / "nonexistent.md")
        identity.load()
        # Should not crash, just have empty content
        assert isinstance(identity.soul, str)

    def test_get_system_prompt(self, identity_dir):
        identity = Identity(
            soul_path=identity_dir / "SOUL.md",
            agent_path=identity_dir / "AGENT.md",
            user_path=identity_dir / "USER.md",
            memory_path=identity_dir / "MEMORY.md",
        )
        identity.load()
        prompt = identity.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    @pytest.mark.skip(reason=_AGENT_VOICE_NOT_WIRED)
    def test_get_system_prompt_does_not_inject_openakita_self_identity(self, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text("# Soul\n\n你是 CloseBeta。", encoding="utf-8")
        (identity_dir / "AGENT.md").write_text("# Agent\n\n保持诚实。", encoding="utf-8")
        (identity_dir / "USER.md").write_text("# User\n\n用户偏好中文。", encoding="utf-8")
        (identity_dir / "MEMORY.md").write_text("# Memory\n\n无。", encoding="utf-8")

        identity = Identity(
            soul_path=identity_dir / "SOUL.md",
            agent_path=identity_dir / "AGENT.md",
            user_path=identity_dir / "USER.md",
            memory_path=identity_dir / "MEMORY.md",
        )
        identity.load()

        prompt = identity.get_system_prompt(include_active_task=False)
        assert "你是 CloseBeta" in prompt
        assert "你是 OpenAkita，一个全能自进化AI助手。" not in prompt

    @pytest.mark.skip(reason=_AGENT_VOICE_NOT_WIRED)
    def test_get_system_prompt_replaces_agent_name_placeholder(self, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        (identity_dir / "SOUL.md").write_text(
            "# Soul\n\n我是 {{agent_name}}，由 CloseBeta 项目驱动。",
            encoding="utf-8",
        )
        (identity_dir / "AGENT.md").write_text(
            "# Agent\n\n{{agent_name}} 保持诚实。",
            encoding="utf-8",
        )
        (identity_dir / "USER.md").write_text(
            "# User\n\n用户正在和 {{agent_name}} 聊天。",
            encoding="utf-8",
        )
        (identity_dir / "MEMORY.md").write_text(
            "# Memory\n\n{{agent_name}} 的独立记忆。",
            encoding="utf-8",
        )

        identity = Identity(
            soul_path=identity_dir / "SOUL.md",
            agent_path=identity_dir / "AGENT.md",
            user_path=identity_dir / "USER.md",
            memory_path=identity_dir / "MEMORY.md",
        )
        identity.load()

        prompt = identity.get_system_prompt(include_active_task=False, agent_voice="叮叮")
        assert "我是 叮叮" in prompt
        assert "叮叮 保持诚实" in prompt
        assert "{{agent_name}}" not in prompt

    def test_get_soul_summary(self, identity_dir):
        identity = Identity(soul_path=identity_dir / "SOUL.md")
        identity.load()
        summary = identity.get_soul_summary()
        assert isinstance(summary, str)


class TestIdentityUpdate:
    def test_update_memory(self, identity_dir):
        identity = Identity(memory_path=identity_dir / "MEMORY.md")
        identity.load()
        # update_memory returns bool
        result = identity.update_memory("preferences", "用户喜欢咖啡")
        assert isinstance(result, bool)


@pytest.mark.skip(
    reason=(
        "bundled-template fallback (_resolve_bundled_identity_template / "
        "sync_templates) not ported after ADR-0003 split; "
        "see docs/follow-ups/skipped-items-roadmap.md (Batch C)"
    )
)
class TestSyncIdentityFileBundledFallback:
    """Regression coverage for the upgrade-install path.

    The wizard only seeds ``identity/SOUL.md`` from ``SOUL.md.example`` once,
    so on a wheel / Tauri upgrade the user's workspace typically has *no*
    sibling ``.example``. Before the fallback was introduced,
    ``_sync_identity_file`` returned the stale on-disk content untouched and
    the decision matrix (silent-upgrade vs preserve-user-edits) never ran.

    These tests pin down the four upgrade scenarios so we don't regress to
    that behaviour and don't accidentally trample user edits when the bundled
    template is the only source available.
    """

    def _make_user_identity(self, tmp_path: Path, soul_content: str) -> Path:
        d = tmp_path / "user_identity"
        d.mkdir()
        (d / "SOUL.md").write_text(soul_content, encoding="utf-8")
        return d

    def _make_bundled(self, tmp_path: Path, content: str) -> Path:
        bundled_dir = tmp_path / "bundled"
        bundled_dir.mkdir()
        target = bundled_dir / "SOUL.md.example"
        target.write_text(content, encoding="utf-8")
        return target

    def _install_bundled_resolver(self, monkeypatch, bundled_example: Path) -> None:
        def fake_resolver(rel_name: str) -> Path | None:
            return bundled_example if rel_name == "SOUL.md.example" else None

        monkeypatch.setattr(identity_mod, "_resolve_bundled_identity_template", fake_resolver)

    def test_silent_upgrade_when_user_unmodified_and_only_bundled_exists(
        self, tmp_path, monkeypatch
    ):
        old_stub = "# Soul\n\n你是 OpenAkita。\n"
        new_template = "# Soul\n\n你是 {{agent_name}}，一只秋田犬。\n"

        user_id = self._make_user_identity(tmp_path, old_stub)
        soul = user_id / "SOUL.md"

        _save_hashes(user_id, {"SOUL.md": _file_hash(soul)})

        bundled_example = self._make_bundled(tmp_path, new_template)
        self._install_bundled_resolver(monkeypatch, bundled_example)

        identity = Identity(soul_path=soul)
        identity.load()

        assert "{{agent_name}}" in soul.read_text(encoding="utf-8")
        assert "{{agent_name}}" in identity.soul

    def test_preserves_user_edits_when_hash_mismatches_via_bundled(self, tmp_path, monkeypatch):
        user_edited = "# Soul\n\n你是 我的自定义角色。\n"
        new_template = "# Soul\n\n你是 {{agent_name}}，一只秋田犬。\n"

        user_id = self._make_user_identity(tmp_path, user_edited)
        soul = user_id / "SOUL.md"

        # Record a stale hash that does *not* match current SOUL.md → simulates
        # "system wrote this once, user has since edited it without us knowing".
        _save_hashes(user_id, {"SOUL.md": "stalehash00000000"})

        bundled_example = self._make_bundled(tmp_path, new_template)
        self._install_bundled_resolver(monkeypatch, bundled_example)

        identity = Identity(soul_path=soul)
        identity.load()

        assert soul.read_text(encoding="utf-8") == user_edited
        assert identity.soul == user_edited

    def test_no_change_when_neither_local_nor_bundled_example_exists(self, tmp_path, monkeypatch):
        content = "# Soul\n\n你是 OpenAkita。\n"
        user_id = self._make_user_identity(tmp_path, content)
        soul = user_id / "SOUL.md"

        monkeypatch.setattr(identity_mod, "_resolve_bundled_identity_template", lambda name: None)

        identity = Identity(soul_path=soul)
        identity.load()

        assert soul.read_text(encoding="utf-8") == content
        assert identity.soul == content

    def test_local_example_wins_over_bundled_when_both_exist(self, tmp_path, monkeypatch):
        old_stub = "# Soul\n\n你是 OpenAkita。\n"
        local_template = "# Soul\n\n你是 LOCAL_OVERRIDE。\n"
        bundled_template = "# Soul\n\n你是 BUNDLED_FALLBACK。\n"

        user_id = self._make_user_identity(tmp_path, old_stub)
        soul = user_id / "SOUL.md"
        (user_id / "SOUL.md.example").write_text(local_template, encoding="utf-8")
        _save_hashes(user_id, {"SOUL.md": _file_hash(soul)})

        bundled_example = self._make_bundled(tmp_path, bundled_template)
        self._install_bundled_resolver(monkeypatch, bundled_example)

        identity = Identity(soul_path=soul)
        identity.load()

        assert "LOCAL_OVERRIDE" in soul.read_text(encoding="utf-8")
        assert "BUNDLED_FALLBACK" not in soul.read_text(encoding="utf-8")

    def test_pending_upgrade_queued_when_no_hash_record(self, tmp_path, monkeypatch):
        """Old-old user with no .file_hashes.json: SOUL.md content differs from
        bundled but no recorded hash → scenario 4, queued for prompt, file
        untouched."""
        user_content = "# Soul\n\n你是 OpenAkita。\n"
        bundled_content = "# Soul\n\n你是 {{agent_name}}，一只秋田犬。\n"

        user_id = self._make_user_identity(tmp_path, user_content)
        soul = user_id / "SOUL.md"

        bundled_example = self._make_bundled(tmp_path, bundled_content)
        self._install_bundled_resolver(monkeypatch, bundled_example)

        identity = Identity(soul_path=soul)
        identity.load()

        assert soul.read_text(encoding="utf-8") == user_content
        pending_names = [item["name"] for item in identity.get_pending_upgrades()]
        assert "SOUL.md" in pending_names
