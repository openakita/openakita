from openakita.agents.profile import AgentProfile, AgentType


def test_agent_profile_to_dict_exposes_definition_metadata():
    profile = AgentProfile(
        id="planner",
        name="Planner",
        type=AgentType.SYSTEM,
        role="coordinator",
    )

    payload = profile.to_dict()
    assert payload["origin"] == "system"
    assert payload["namespace"] == "system"
    assert payload["definition_id"].endswith("agent_definition:planner")
