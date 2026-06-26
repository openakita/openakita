import zipfile

from openakita.agents.packager import AgentInstaller, AgentPackager
from openakita.agents.profile import AgentProfile, ProfileStore


def test_agent_package_round_trips_profile_identity_files(tmp_path):
    source_store = ProfileStore(tmp_path / "source-agents")
    skills_dir = tmp_path / "skills"
    output_dir = tmp_path / "packages"
    profile = AgentProfile(
        id="clawra",
        name="Clawra",
        description="Custom identity test agent",
        identity_mode="custom",
        memory_mode="isolated",
    )
    source_store.save(profile)

    identity_dir = source_store.ensure_profile_dir("clawra") / "identity"
    (identity_dir / "SOUL.md").write_text("clawra soul", encoding="utf-8")
    (identity_dir / "AGENT.md").write_text("clawra behavior", encoding="utf-8")

    package_path = AgentPackager(
        profile_store=source_store,
        skills_dir=skills_dir,
        output_dir=output_dir,
    ).package("clawra")

    with zipfile.ZipFile(package_path) as zf:
        assert zf.read("identity/SOUL.md").decode("utf-8") == "clawra soul"
        assert zf.read("identity/AGENT.md").decode("utf-8") == "clawra behavior"

    target_store = ProfileStore(tmp_path / "target-agents")
    installed = AgentInstaller(profile_store=target_store, skills_dir=skills_dir).install(
        package_path
    )
    installed_identity_dir = target_store.get_profile_dir(installed.id) / "identity"

    assert (installed_identity_dir / "SOUL.md").read_text(encoding="utf-8") == "clawra soul"
    assert (installed_identity_dir / "AGENT.md").read_text(encoding="utf-8") == "clawra behavior"
