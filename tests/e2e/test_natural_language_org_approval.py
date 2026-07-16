"""End-to-end coverage for unattended natural-language organization approval."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openakita.api.routes.pending_approvals import _resume_task
from openakita.config import settings
from openakita.core.agent import Agent
from openakita.core.agent_state import AgentState
from openakita.core.pending_approvals import (
    get_pending_approvals_store,
    reset_pending_approvals_store,
)
from openakita.core.policy_v2 import (
    ConfirmationMode,
    PolicyConfigV2,
    PolicyContext,
    ReplayAuthorization,
    build_engine_from_config,
    get_config_v2,
    get_engine_v2,
    reset_current_context,
    set_current_context,
    set_engine_v2,
)
from openakita.core.policy_v2.exceptions import DeferredApprovalRequired
from openakita.core.reasoning_engine import ReasoningEngine
from openakita.core.tool_executor import ToolExecutor
from openakita.orgs.manager import OrgManager
from openakita.orgs.store import get_default_org_manager, set_default_org_manager
from openakita.scheduler.task import ScheduledTask, TaskStatus, TriggerType
from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
from openakita.tools.handlers import SystemHandlerRegistry
from openakita.tools.handlers.org_setup import OrgSetupHandler
from tests.fixtures.mock_llm import MockResponse

NATURAL_REQUEST = "请创建一个名为星火研发组的组织，由技术负责人带领开发工程师。"
TASK_ID = "task-natural-language-org"
SESSION_ID = "session-natural-language-org"
ORG_PARAMS = {
    "action": "create",
    "name": "星火研发组",
    "description": "通过自然语言请求创建的测试组织",
    "core_business": "软件研发",
    "nodes": [
        {
            "role_title": "技术负责人",
            "level": 0,
            "agent_profile_id": "architect",
        },
        {
            "role_title": "开发工程师",
            "level": 1,
            "agent_profile_id": "code-assistant",
            "parent_role_title": "技术负责人",
        },
    ],
}


def _make_agent(registry: SystemHandlerRegistry, responses: list[MockResponse]) -> Agent:
    """Build a real Agent loop while scripting only its LLM boundary."""
    agent = Agent.__new__(Agent)
    agent._initialized = True
    agent._current_session_id = None
    agent._context = SimpleNamespace(system="")
    agent._tools = ORG_SETUP_TOOLS
    agent._is_sub_agent_call = False
    agent._agent_tool_names = set()
    agent._suppress_desktop_task_notification = True
    agent.brain = SimpleNamespace(
        model="test-model",
        max_tokens=1000,
        get_fallback_model=lambda _session_id=None: None,
        restore_default_model=lambda **_kwargs: None,
    )
    agent.agent_state = AgentState()
    agent.agent_state.begin_task(session_id=SESSION_ID, task_id=TASK_ID)
    agent.reasoning_engine = SimpleNamespace(
        _drain_steer_before_finish=ReasoningEngine._drain_steer_before_finish
    )
    agent.tool_executor = ToolExecutor(registry)
    agent.tool_executor._agent_ref = agent

    async def _passthrough_compress(messages, system_prompt=""):
        return messages

    agent._compress_context = _passthrough_compress
    queued = iter(responses)

    async def _scripted_llm(cancel_event, **kwargs):
        response = next(queued)
        if response.tool_calls:
            messages = kwargs["messages"]
            tools = kwargs["tools"]
            assert NATURAL_REQUEST in str(messages[0]["content"])
            assert any(tool["name"] == "setup_organization" for tool in tools)
        return response.to_llm_response()

    agent._cancellable_llm_call = _scripted_llm
    return agent


class _Scheduler:
    def __init__(self, task: ScheduledTask) -> None:
        self._tasks = {task.id: task}
        self._lock = asyncio.Lock()

    def _save_tasks(self) -> None:
        pass


@pytest.mark.asyncio
async def test_natural_language_org_creation_defers_then_replays_after_owner_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise request -> tool orchestration -> approval -> replay -> persistence."""
    monkeypatch.setattr(settings, "project_root", tmp_path)
    data_dir = settings.data_dir
    previous_engine = get_engine_v2()
    previous_config = get_config_v2()
    previous_org_manager = get_default_org_manager()
    reset_pending_approvals_store()
    set_default_org_manager(None)

    registry = SystemHandlerRegistry()
    handler_owner = SimpleNamespace()
    org_handler = OrgSetupHandler(handler_owner)
    registry.register("organization", org_handler.handle)
    config = PolicyConfigV2()
    engine = build_engine_from_config(config, explicit_lookup=registry.get_tool_class)
    set_engine_v2(engine, config)

    unattended_context = PolicyContext(
        session_id=SESSION_ID,
        workspace=data_dir,
        channel="scheduler",
        is_owner=True,
        is_unattended=True,
        unattended_strategy="defer_to_owner",
        confirmation_mode=ConfirmationMode.DEFAULT,
        user_message=NATURAL_REQUEST,
    )
    token = set_current_context(unattended_context)

    try:
        first_agent = _make_agent(
            registry,
            [
                MockResponse(
                    tool_calls=[
                        {
                            "id": "toolu_org_create_first",
                            "name": "setup_organization",
                            "input": ORG_PARAMS,
                        }
                    ]
                )
            ],
        )
        with pytest.raises(DeferredApprovalRequired):
            await first_agent.execute_task_from_message(NATURAL_REQUEST)

        store = get_pending_approvals_store()
        pending = store.list_active()
        assert len(pending) == 1
        approval = pending[0]
        assert approval.task_id == TASK_ID
        assert approval.tool_name == "setup_organization"
        assert approval.params == ORG_PARAMS
        assert approval.user_message == NATURAL_REQUEST
        assert OrgManager(data_dir).list_orgs(include_archived=True) == []

        task = ScheduledTask(
            id=TASK_ID,
            name="自然语言创建组织",
            description=NATURAL_REQUEST,
            trigger_type=TriggerType.ONCE,
            trigger_config={"run_at": datetime.now().isoformat()},
            prompt=NATURAL_REQUEST,
            status=TaskStatus.AWAITING_APPROVAL,
        )
        scheduler = _Scheduler(task)
        resolved = store.resolve(approval.id, decision="allow", resolved_by="owner")
        assert resolved is not None
        resume_result = await _resume_task(scheduler, resolved)
        assert resume_result["task_resumed"] is True
        assert task.status is TaskStatus.SCHEDULED

        replay_payload = task.metadata["replay_authorizations"][0]
        replay = ReplayAuthorization(**replay_payload)
        reset_current_context(token)
        token = set_current_context(
            PolicyContext(
                session_id=SESSION_ID,
                workspace=data_dir,
                channel="scheduler",
                is_owner=True,
                is_unattended=True,
                unattended_strategy="defer_to_owner",
                confirmation_mode=ConfirmationMode.DEFAULT,
                user_message=NATURAL_REQUEST,
                replay_authorizations=[replay],
            )
        )

        replay_agent = _make_agent(
            registry,
            [
                MockResponse(
                    tool_calls=[
                        {
                            "id": "toolu_org_create_replay",
                            "name": "setup_organization",
                            "input": ORG_PARAMS,
                        }
                    ]
                ),
                MockResponse(content="## 创建结果\n\n- 星火研发组已创建完成\n- 组织包含两个角色。"),
            ],
        )
        result = await replay_agent.execute_task_from_message(NATURAL_REQUEST)

        assert result.success is True
        organizations = OrgManager(data_dir).list_orgs(include_archived=True)
        assert len(organizations) == 1
        created = OrgManager(data_dir).get(organizations[0]["id"])
        assert created is not None
        assert created.name == "星火研发组"
        assert {node.role_title for node in created.nodes} == {"技术负责人", "开发工程师"}
        assert store.list_active() == []
    finally:
        reset_current_context(token)
        reset_pending_approvals_store()
        set_default_org_manager(previous_org_manager)
        set_engine_v2(previous_engine, previous_config)
