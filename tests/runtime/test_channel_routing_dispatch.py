"""Tests for the async ``dispatch_inbound_message_to_v2`` dispatch path.

P-RC-1 commit 2. Covers the six scenarios the continuation plan
section 2.1 nominates as the canary acceptance gate: happy /
unbound / unknown org / empty org / cancel mid-run / supervisor
exception. The supervisor itself is the real
:class:`Supervisor` -- only the brain is mocked (per the user
contract "the test must NOT touch real LLMs").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openakita.orgs import reset_default_store, set_default_org_manager
from openakita.orgs.manager import OrgManager
from openakita.orgs.org_models import OrgNode
from openakita.runtime.cancel_token import CancellationToken, CancelledByToken
from openakita.runtime.channel_routing import (
    RoutingPlan,
    dispatch_inbound_message_to_v2,
)
from openakita.runtime.checkpoint import MemoryCheckpointer
from openakita.runtime.supervisor import FinalOutcome, SupervisorBrain


def _ledger_json(satisfied: bool = True) -> str:
    return json.dumps({
        "is_request_satisfied":    {"answer": satisfied, "reason": "r"},
        "is_progress_being_made":  {"answer": True,      "reason": "r"},
        "is_in_loop":              {"answer": False,     "reason": "r"},
        "instruction_or_question": {"answer": "ok",      "reason": "r"},
        "next_speaker":            {"answer": "supervisor", "reason": "r"},
    })


class _OneShotBrain(SupervisorBrain):
    def __init__(self) -> None:
        self.facts_calls = self.plan_calls = self.progress_calls = 0

    async def extract_facts(self, *, task: str, **_kwargs: Any) -> str:
        self.facts_calls += 1
        return "facts"

    async def draft_plan(self, *, task: str, facts: str, **_kwargs: Any) -> str:
        self.plan_calls += 1
        return "plan"

    async def emit_progress_ledger(self, **kwargs: Any) -> str:
        self.progress_calls += 1
        return _ledger_json(satisfied=True)


class _CancellingBrain(SupervisorBrain):
    async def extract_facts(self, *, task: str, **_kwargs: Any) -> str:
        raise CancelledByToken("user_cancel_via_im")

    async def draft_plan(self, *, task: str, facts: str, **_kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError

    async def emit_progress_ledger(self, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError


class _ExplodingBrain(SupervisorBrain):
    async def extract_facts(self, *, task: str, **_kwargs: Any) -> str:
        raise RuntimeError("brain misbehaved")

    async def draft_plan(self, *, task: str, facts: str, **_kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError

    async def emit_progress_ledger(self, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError


def _seed_org_in_manager(manager: OrgManager, *, with_nodes: bool = True) -> str:
    """Mint ``org_canary`` in the manager-backed SSoT.

    Sprint 13 H2 (RC-1): the dispatch path reads through the
    JsonOrgStore shim which now delegates to OrgManager, so we
    seed straight into ``data/orgs/<id>/`` rather than
    ``data/orgs_v2.json``. Returns the minted org id (always
    ``"org_canary"`` -- pinned for reason-string assertions).
    """
    nodes: list[dict] = []
    if with_nodes:
        nodes.append(
            OrgNode(
                id="node_root",
                role_title="producer",
                agent_profile_id="default",
            ).to_dict()
        )
    manager.create({"id": "org_canary", "name": "Canary Org", "nodes": nodes})
    return "org_canary"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path) -> OrgManager:
    """Tmp-rooted manager + shim for every dispatch test.

    The yielded :class:`OrgManager` is the SSoT for this test;
    individual tests can call ``_seed_org_in_manager(manager,
    with_nodes=...)`` to add a canary org. Both the shim and the
    process-wide default manager are reset between cases so no
    state leaks across tests.
    """
    manager = OrgManager(tmp_path)
    reset_default_store(path=tmp_path / "orgs_v2.json", manager=manager)
    set_default_org_manager(manager)
    yield manager
    set_default_org_manager(None)
    reset_default_store()


async def test_dispatch_routes_when_org_is_canary_shaped(_isolated_store: OrgManager) -> None:
    _seed_org_in_manager(_isolated_store, with_nodes=True)
    brain = _OneShotBrain()
    checkpointer = MemoryCheckpointer()

    plan = await dispatch_inbound_message_to_v2(
        session_key="telegram:chat:user", org_id="org_canary",
        message="hello world", brain=brain, checkpointer=checkpointer,
    )

    assert isinstance(plan, RoutingPlan)
    assert plan.routed is True
    assert plan.status == "routed"
    assert plan.next_node_id == "node_root" and plan.next_node_role == "producer"
    assert brain.facts_calls == brain.plan_calls == brain.progress_calls == 1
    outcome = plan.result
    assert outcome is not None and outcome.outcome is FinalOutcome.DONE
    assert checkpointer.total() >= 1


async def test_dispatch_skips_when_session_has_no_org_binding() -> None:
    plan = await dispatch_inbound_message_to_v2(
        session_key="telegram:chat:user", org_id=None, message="hi",
    )
    assert plan.status == "skipped" and plan.result is None
    assert "not bound" in plan.reason


async def test_dispatch_skips_when_org_is_unknown() -> None:
    plan = await dispatch_inbound_message_to_v2(
        session_key="feishu:chat:user", org_id="org_does_not_exist", message="hi",
    )
    assert plan.status == "skipped" and "not in v2 store" in plan.reason


async def test_dispatch_skips_when_org_has_no_nodes(_isolated_store: OrgManager) -> None:
    _seed_org_in_manager(_isolated_store, with_nodes=False)
    plan = await dispatch_inbound_message_to_v2(
        session_key="wecom:chat:user", org_id="org_canary", message="hi",
    )
    assert plan.status == "skipped" and "no nodes" in plan.reason


async def test_dispatch_returns_cancelled_when_brain_raises_cancel_token(
    _isolated_store: OrgManager,
) -> None:
    _seed_org_in_manager(_isolated_store, with_nodes=True)
    token = CancellationToken()
    token.cancel("user_cancel_via_im")
    checkpointer = MemoryCheckpointer()

    plan = await dispatch_inbound_message_to_v2(
        session_key="telegram:chat:user", org_id="org_canary",
        message="please stop", brain=_CancellingBrain(),
        cancel_token=token, checkpointer=checkpointer,
    )

    assert plan.status == "cancelled" and plan.cancelled is True
    outcome = plan.result
    assert outcome is not None and outcome.outcome is FinalOutcome.CANCELLED
    assert outcome.final_checkpoint_id is not None
    assert checkpointer.total() >= 1


async def test_dispatch_swallows_supervisor_exception_and_skips(
    _isolated_store: OrgManager,
) -> None:
    _seed_org_in_manager(_isolated_store, with_nodes=True)
    plan = await dispatch_inbound_message_to_v2(
        session_key="qq:chat:user", org_id="org_canary",
        message="trigger boom", brain=_ExplodingBrain(),
    )
    assert plan.status == "skipped" and plan.result is None
    assert "supervisor.run raised" in plan.reason or "dispatch failed" in plan.reason
