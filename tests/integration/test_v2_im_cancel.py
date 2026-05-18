"""P-RC-1 integration tests: IM cancel verb fires the v2 CancellationToken.

Commit 5. Three+ cases:

1. With a v2 token registered for the session, the bare-text cancel
   verb ("中止") fires ``token.cancel()`` and the gateway returns
   from ``_handle_message`` without falling through to the legacy
   cancel path.
2. With no v2 token registered, the v2 cancel fast-path is a no-op
   (returns False) and the legacy fast-paths run as before.
3. The helper :meth:`_cancel_v2_dispatch` swallows token-side raises
   and reports False so a misbehaving token never breaks the
   gateway message loop.

Plus an end-to-end test that runs ``dispatch_inbound_message_to_v2``
on a pre-cancelled token and asserts the supervisor writes a
final checkpoint with ``CANCELLED`` status -- the part the commit-5
spec calls "Supervisor must already save a final checkpoint on
cancel -- verify this in the test".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.channels.gateway import MessageGateway
from openakita.runtime.cancel_token import CancellationToken, CancelledByToken
from openakita.runtime.channel_routing import dispatch_inbound_message_to_v2
from openakita.runtime.checkpoint import CheckpointStatus, MemoryCheckpointer
from openakita.runtime.models import NodeType, NodeV2, OrgV2
from openakita.runtime.orgs import reset_default_store
from openakita.runtime.supervisor import FinalOutcome, SupervisorBrain
from tests.fixtures.factories import create_channel_message


def _make_gateway() -> MessageGateway:
    session_manager = MagicMock()
    session_manager._sessions = {}
    session_manager.build_session_key = MagicMock(return_value="sess:k")
    return MessageGateway(session_manager=session_manager)


@pytest.mark.asyncio
async def test_cancel_v2_dispatch_fires_token_and_sends_feedback() -> None:
    gw = _make_gateway()
    token = CancellationToken()
    gw._v2_cancel_tokens["sess:k"] = token
    msg = create_channel_message(text="中止")
    gw._send_feedback = AsyncMock()  # type: ignore[assignment]

    handled = await gw._cancel_v2_dispatch("sess:k", msg, "中止")

    assert handled is True
    assert token.is_cancelled() is True
    gw._send_feedback.assert_awaited_once()
    sent_text = gw._send_feedback.call_args[0][1]
    assert "v2" in sent_text


@pytest.mark.asyncio
async def test_cancel_v2_dispatch_is_noop_when_no_token() -> None:
    gw = _make_gateway()
    msg = create_channel_message(text="中止")
    gw._send_feedback = AsyncMock()  # type: ignore[assignment]

    handled = await gw._cancel_v2_dispatch("sess:k", msg, "中止")

    assert handled is False
    gw._send_feedback.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_v2_dispatch_swallows_token_raise() -> None:
    gw = _make_gateway()

    class _BadToken:
        def cancel(self, reason: str) -> None:
            raise RuntimeError("token store offline")

    gw._v2_cancel_tokens["sess:k"] = _BadToken()  # type: ignore[assignment]
    msg = create_channel_message(text="取消")
    gw._send_feedback = AsyncMock()  # type: ignore[assignment]

    handled = await gw._cancel_v2_dispatch("sess:k", msg, "取消")

    assert handled is False
    gw._send_feedback.assert_not_awaited()


class _CancellingBrain(SupervisorBrain):
    async def extract_facts(self, *, task: str) -> str:
        raise CancelledByToken("user_cancel_via_im")

    async def draft_plan(self, *, task: str, facts: str) -> str:  # pragma: no cover
        raise AssertionError

    async def emit_progress_ledger(self, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError


@pytest.fixture
def _seeded_store(tmp_path: Path) -> None:
    store = reset_default_store(path=tmp_path / "orgs_v2.json")
    org = OrgV2(id="org_canary", name="Canary")
    org.nodes.append(NodeV2(
        id="node_root", org_id=org.id, type=NodeType.LLM,
        role="producer", label="P",
    ))
    store.create(org)
    yield
    reset_default_store()


@pytest.mark.asyncio
async def test_supervisor_writes_final_cancelled_checkpoint(
    _seeded_store: None,
) -> None:
    token = CancellationToken()
    token.cancel("user_cancel_via_im")
    checkpointer = MemoryCheckpointer()

    plan = await dispatch_inbound_message_to_v2(
        session_key="sess:k",
        org_id="org_canary",
        message="please stop",
        cancel_token=token,
        brain=_CancellingBrain(),
        checkpointer=checkpointer,
    )

    # The dispatch reports cancelled and surfaces the SupervisorOutcome.
    assert plan.status == "cancelled"
    outcome = plan.result
    assert outcome is not None
    assert outcome.outcome is FinalOutcome.CANCELLED

    # The supervisor's _terminate path always writes a final checkpoint
    # with status CANCELLED -- this is the resume contract.
    assert outcome.final_checkpoint_id is not None
    stored = await checkpointer.aget(outcome.final_checkpoint_id)
    assert stored is not None
    assert stored.metadata.status is CheckpointStatus.CANCELLED
    # And the cancel reason is preserved in state.
    assert stored.state.get("final_reason") == "user_cancel_via_im"
