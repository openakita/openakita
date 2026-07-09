from openakita.agents.profile import AgentProfile, ProfileStore
from openakita.api import agent_events
from openakita.api.routes.agents import _build_agent_manager_state


def test_agent_manager_state_counts_categories_from_same_profile_projection(tmp_path):
    store = ProfileStore(tmp_path / "agents")
    store.add_category("special", "Special", "#6b7280")
    store.save(AgentProfile(id="visible-agent", name="Visible", category="special"))
    store.save(
        AgentProfile(
            id="hidden-agent",
            name="Hidden",
            category="special",
            hidden=True,
        )
    )

    state = _build_agent_manager_state(store, include_hidden=True)
    profiles = state["profiles"]
    categories = state["categories"]

    special = next(category for category in categories if category["id"] == "special")
    expected_special_count = sum(
        1 for profile in profiles if profile.get("category") == "special" and not profile.get("hidden")
    )
    assert special["agent_count"] == expected_special_count == 1

    general = next(category for category in categories if category["id"] == "general")
    expected_general_count = sum(
        1 for profile in profiles if profile.get("category") == "general" and not profile.get("hidden")
    )
    assert general["agent_count"] == expected_general_count
    assert any(profile["id"] == "default" for profile in profiles)


def test_agent_change_events_use_stable_websocket_names(monkeypatch):
    emitted: list[tuple[str, dict]] = []

    def fake_fire_event(event: str, payload: dict) -> bool:
        emitted.append((event, payload))
        return True

    monkeypatch.setattr("openakita.api.routes.websocket.fire_event", fake_fire_event)

    agent_events.emit_agent_profiles_changed("updated", profile_id="agent-a")
    agent_events.emit_agent_categories_changed("profile_updated", profile_id="agent-a")

    assert emitted == [
        ("agents:profiles_changed", {"action": "updated", "profile_id": "agent-a"}),
        (
            "agents:categories_changed",
            {"action": "profile_updated", "profile_id": "agent-a"},
        ),
    ]
