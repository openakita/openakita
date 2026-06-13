from openakita.agents.profile import AgentProfile, ProfileStore
from openakita.api.routes import hub


def test_json_identity_file_helpers_round_trip_profile_identity(tmp_path):
    source_store = ProfileStore(tmp_path / "source-agents")
    source_store.save(AgentProfile(id="clawra", name="Clawra", identity_mode="custom"))
    source_identity = source_store.ensure_profile_dir("clawra") / "identity"
    (source_identity / "SOUL.md").write_text("clawra soul", encoding="utf-8")

    exported = hub._read_profile_identity_files(source_store, "clawra")

    assert exported == {"SOUL.md": "clawra soul"}

    target_store = ProfileStore(tmp_path / "target-agents")
    target_store.save(AgentProfile(id="clawra", name="Clawra", identity_mode="custom"))
    hub._write_profile_identity_files(target_store, "clawra", exported)

    target_identity = target_store.get_profile_dir("clawra") / "identity" / "SOUL.md"
    assert target_identity.read_text(encoding="utf-8") == "clawra soul"
