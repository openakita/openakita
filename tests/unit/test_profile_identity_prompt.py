import pytest

# Profile identity prompt wiring (``Agent._prepare_prompt_identity_dir`` +
# ``Identity`` v1 helpers ``_file_hash`` / ``_save_hashes`` + the
# ``sync_templates=False`` placeholder path) is upstream work NOT ported after
# the ADR-0003 split of ``core/agent.py``. The compat shims keep
# ``openakita.core.agent`` importable, but ``openakita.core.identity`` does not
# yet expose these helpers, so this whole module is deferred. See
# docs/follow-ups/skipped-items-roadmap.md (Batch C — core/agent.py follow-ups).
pytest.skip(
    "profile identity prompt wiring + Identity v1 helpers not ported after "
    "ADR-0003 split; see docs/follow-ups/skipped-items-roadmap.md (Batch C)",
    allow_module_level=True,
)

from pathlib import Path  # noqa: E402

import openakita.core.identity as identity_mod  # noqa: E402
from openakita.agents.identity_resolver import ProfileIdentityResolver  # noqa: E402
from openakita.core.agent import Agent  # noqa: E402
from openakita.core.identity import Identity, _file_hash, _save_hashes  # noqa: E402


def test_agent_materializes_mixed_profile_identity(monkeypatch, tmp_path: Path):
    global_dir = tmp_path / "identity"
    profile_dir = tmp_path / "agents" / "clawra" / "identity"
    global_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)

    global_agent = global_dir / "AGENT.md"
    profile_soul = profile_dir / "SOUL.md"
    profile_user = profile_dir / "USER.md"
    global_agent.write_text("global agent behavior", encoding="utf-8")
    profile_soul.write_text("clawra soul", encoding="utf-8")
    profile_user.write_text("clawra user", encoding="utf-8")

    agent = Agent.__new__(Agent)
    agent.name = "Clawra"
    agent._agent_profile_id = "clawra"
    agent._prompt_identity_runtime_root = tmp_path / "home"
    agent.identity = Identity(
        soul_path=profile_soul,
        agent_path=global_agent,
        user_path=profile_user,
        memory_path=profile_dir / "MEMORY.md",
    )

    resolved_dir = agent._prepare_prompt_identity_dir()

    assert resolved_dir != global_dir
    assert (resolved_dir / "SOUL.md").read_text(encoding="utf-8") == "clawra soul"
    assert (resolved_dir / "AGENT.md").read_text(encoding="utf-8") == "global agent behavior"
    assert (resolved_dir / "USER.md").read_text(encoding="utf-8") == "clawra user"


def test_profile_identity_load_does_not_sync_bundled_templates(monkeypatch, tmp_path: Path):
    global_dir = tmp_path / "identity"
    profile_dir = tmp_path / "agents" / "a-bot" / "identity"
    bundled_dir = tmp_path / "bundled"
    global_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    bundled_dir.mkdir(parents=True)

    profile_soul = profile_dir / "SOUL.md"
    profile_agent = profile_dir / "AGENT.md"
    profile_user = profile_dir / "USER.md"
    profile_soul.write_text("# Soul\n\n你是 CloseBeta。", encoding="utf-8")
    profile_agent.write_text("# Agent\n\n我是 CloseBeta。", encoding="utf-8")
    profile_user.write_text("# User\n\n由 CloseBeta 自动维护。", encoding="utf-8")

    _save_hashes(
        profile_dir,
        {
            "SOUL.md": _file_hash(profile_soul),
            "AGENT.md": _file_hash(profile_agent),
            "USER.md": _file_hash(profile_user),
        },
    )

    bundled_soul = bundled_dir / "SOUL.md.example"
    bundled_agent = bundled_dir / "AGENT.md.example"
    bundled_user = bundled_dir / "USER.md.example"
    bundled_soul.write_text("# Soul\n\n你是 OpenAkita。", encoding="utf-8")
    bundled_agent.write_text("# Agent\n\n我是 OpenAkita。", encoding="utf-8")
    bundled_user.write_text("# User\n\n由 OpenAkita 自动维护。", encoding="utf-8")

    def fake_resolver(rel_name: str) -> Path | None:
        return {
            "SOUL.md.example": bundled_soul,
            "AGENT.md.example": bundled_agent,
            "USER.md.example": bundled_user,
        }.get(rel_name)

    monkeypatch.setattr(identity_mod, "_resolve_bundled_identity_template", fake_resolver)

    identity = ProfileIdentityResolver(profile_dir, global_dir).build_identity()
    identity.load()

    assert profile_soul.read_text(encoding="utf-8") == "# Soul\n\n你是 CloseBeta。"
    assert profile_agent.read_text(encoding="utf-8") == "# Agent\n\n我是 CloseBeta。"
    assert profile_user.read_text(encoding="utf-8") == "# User\n\n由 CloseBeta 自动维护。"
    assert identity.get_pending_upgrades() == []
