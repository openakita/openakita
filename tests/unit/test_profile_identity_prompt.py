from pathlib import Path

from openakita.core.agent import Agent
from openakita.core.identity import Identity


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
