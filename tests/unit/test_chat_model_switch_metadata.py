from types import SimpleNamespace

from openakita.api.schemas import ChatRequest
from openakita.core.agent import Agent
from openakita.sessions.session import Session


class _FakeBrain:
    def __init__(self):
        self.calls: list[str | None] = []
        self.model = "minimax-old"
        self._llm_client = SimpleNamespace(switch_model=self._switch_model)
        self._overrides: dict[str | None, str] = {}
        self._policies: dict[str | None, str] = {}

    def _switch_model(self, endpoint_name, hours, reason, conversation_id=None, policy="prefer"):
        self._overrides[conversation_id] = endpoint_name
        self._policies[conversation_id] = policy
        return True, "ok"

    def get_current_model_info(self, conversation_id=None):
        self.calls.append(conversation_id)
        endpoint = self._overrides.get(conversation_id)
        if endpoint == "glm-endpoint":
            return {
                "name": "glm-endpoint",
                "model": "glm-5",
                "provider": "zhipu",
                "is_healthy": True,
                "is_override": True,
                "capabilities": ["text", "tools"],
                "note": "",
            }
        return {
            "name": "minimax-endpoint",
            "model": "MiniMax-M2.7",
            "provider": "minimax",
            "is_healthy": True,
            "is_override": False,
            "capabilities": ["text", "tools"],
            "note": "",
        }


def _agent_with_fake_brain() -> tuple[Agent, _FakeBrain]:
    agent = Agent.__new__(Agent)
    brain = _FakeBrain()
    agent.brain = brain
    return agent, brain


def test_model_lookup_prefers_frontend_conversation_id_over_session_storage_id():
    agent, brain = _agent_with_fake_brain()
    session = Session.create(channel="desktop", chat_id="frontend-conv", user_id="desktop_user")

    info = agent._current_model_info_for_turn(session=session)

    assert brain.calls[-1] == "frontend-conv"
    assert info["model"] == "MiniMax-M2.7"
    assert session.id != session.chat_id


def test_endpoint_override_is_applied_before_prompt_and_recorded_in_metadata():
    agent, brain = _agent_with_fake_brain()
    session = Session.create(channel="desktop", chat_id="frontend-conv", user_id="desktop_user")

    info = agent._apply_endpoint_override_for_turn(
        endpoint_override="glm-endpoint",
        session=session,
        conversation_id="frontend-conv",
        session_id=session.id,
        reason="test",
    )

    assert brain._overrides["frontend-conv"] == "glm-endpoint"
    assert brain.calls[-1] == "frontend-conv"
    assert info["model"] == "glm-5"

    effective = session.get_metadata("effective_model")
    assert effective == {
        "selected_endpoint": "glm-endpoint",
        "effective_endpoint": "glm-endpoint",
        "effective_model": "glm-5",
        "effective_provider": "zhipu",
        "endpoint_policy": "prefer",
        "is_override": True,
        "is_fallback": False,
    }


def test_endpoint_policy_is_applied_before_prompt_and_recorded_in_metadata():
    agent, brain = _agent_with_fake_brain()
    session = Session.create(channel="desktop", chat_id="frontend-conv", user_id="desktop_user")

    agent._apply_endpoint_override_for_turn(
        endpoint_override="glm-endpoint",
        endpoint_policy="require",
        session=session,
        conversation_id="frontend-conv",
        session_id=session.id,
        reason="test",
    )

    assert brain._policies["frontend-conv"] == "require"
    assert session.get_metadata("endpoint_policy") == "require"
    assert session.get_metadata("effective_model")["endpoint_policy"] == "require"


def test_chat_request_accepts_endpoint_policy():
    request = ChatRequest(message="hello", endpoint="glm-endpoint", endpoint_policy="require")

    assert request.endpoint == "glm-endpoint"
    assert request.endpoint_policy == "require"
