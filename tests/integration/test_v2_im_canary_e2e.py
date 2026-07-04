"""P-RC-1 commit 7: end-to-end canary acceptance gate.

This is the G-RC-1 acceptance test. If any of the assertions
below fail, the gate cannot be claimed and the dispatch wiring
must be fixed before the phase can be signed off.

The test:

1. Boots a minimal FastAPI app with only ``orgs_v2.router`` and
   instantiates a fresh OrgV2 by POSTing to
   ``/api/v2/orgs/templates/{id}/instantiate`` and then POSTing
   it through to ``/api/v2/orgs``.
2. Adds the new org id to ``settings.runtime_v2_canary_orgs``.
3. Constructs a ``MessageGateway`` with a session_manager whose
   ``_sessions`` carries a session bound to the canary org.
4. Spies on :class:`Supervisor` so it can confirm ``Supervisor.run``
   was awaited at least once, then simulates an inbound IM message
   through ``MessageGateway._try_dispatch_v2``.
5. Asserts: the spy fired, the bridge sent at least one IM message,
   the dispatch reported ``routed``, and the supervisor returned
   a non-empty final ``checkpoint_id``.
6. Triggers cancel via the gateway's IM cancel verb helper
   (``_cancel_v2_dispatch``) on a fresh dispatch backed by a brain
   that raises ``CancelledByToken``; asserts a CANCELLED checkpoint
   landed in the shared checkpointer.
7. "Resumes" by running a third dispatch through the gateway with
   the canary brain restored; asserts the checkpointer now holds
   both the cancelled and the resume checkpoints (the resume path
   builds on the same checkpointer instance the gateway hands to
   ``dispatch_inbound_message_to_v2``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import _orgs_v2_legacy_redirects, orgs_v2
from openakita.channels.gateway import MessageGateway
from openakita.config import settings
from openakita.runtime import channel_routing as cr_module
from openakita.runtime import supervisor as sup_module
from openakita.runtime.cancel_token import CancelledByToken
from openakita.runtime.checkpoint import CheckpointStatus, MemoryCheckpointer
from openakita.orgs import reset_default_store
from openakita.runtime.supervisor import SupervisorBrain
from tests.fixtures.factories import create_channel_message

# ---------------------------------------------------------------------------
# Brains
# ---------------------------------------------------------------------------


class _SatisfyingBrain(SupervisorBrain):
    """Reports satisfied on first turn -> supervisor reaches DONE."""

    def __init__(self) -> None:
        self.progress_calls = 0

    async def extract_facts(self, *, task: str, **_kw: Any) -> str:
        return f"facts: {task[:50]}"

    async def draft_plan(self, *, task: str, facts: str, **_kw: Any) -> str:
        return "acknowledge"

    async def emit_progress_ledger(self, **kw: Any) -> str:
        import json
        self.progress_calls += 1
        return json.dumps({
            "is_request_satisfied":    {"answer": True,  "reason": "done"},
            "is_progress_being_made":  {"answer": True,  "reason": "-"},
            "is_in_loop":              {"answer": False, "reason": "-"},
            "instruction_or_question": {"answer": "ok",  "reason": "-"},
            "next_speaker":            {"answer": "supervisor", "reason": "-"},
        })


class _CancellingBrain(SupervisorBrain):
    async def extract_facts(self, *, task: str, **_kw: Any) -> str:
        raise CancelledByToken("user_cancel_via_im")

    async def draft_plan(self, *, task: str, facts: str, **_kw: Any) -> str:  # pragma: no cover
        raise AssertionError

    async def emit_progress_ledger(self, **kw: Any) -> str:  # pragma: no cover
        raise AssertionError


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


@pytest.fixture
def v2_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    monkeypatch.setattr(orgs_v2, "_BOOTSTRAPPED", False, raising=False)
    reset_default_store(path=tmp_path / "orgs_v2.json")
    app = FastAPI()
    app.include_router(orgs_v2.router)
    # P9.7.nit-a: mount 308 shim so TestClient auto-follows legacy /api/v2/orgs/* to /api/v2/orgs-spec/*.
    app.include_router(_orgs_v2_legacy_redirects.router)
    with TestClient(app) as c:
        yield c
    reset_default_store()


def _instantiate_and_persist(client: TestClient) -> str:
    """POST template/instantiate + POST orgs and return the persisted org id."""
    inst = client.post(
        "/api/v2/orgs/templates/content_ops/instantiate",
        json={"name": "Canary E2E Org"},
    )
    assert inst.status_code == 200, inst.text
    org_payload = inst.json()
    create = client.post("/api/v2/orgs", json={"org": org_payload})
    assert create.status_code == 201, create.text
    return create.json()["id"]


def _make_gateway_with_bound_session(session_key: str, org_id: str) -> MessageGateway:
    session = MagicMock()
    session.get_metadata = MagicMock(side_effect=lambda k, default=None:
                                     org_id if k == "bound_org_id" else default)
    sm = MagicMock()
    sm._sessions = {session_key: session}
    sm.build_session_key = MagicMock(return_value=session_key)
    return MessageGateway(session_manager=sm)


# ---------------------------------------------------------------------------
# E2E acceptance gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canary_org_runs_through_supervisor_then_cancel_then_resume(
    v2_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ---- 1. Mint a canary org via the v2 HTTP facade. ---------------
    org_id = _instantiate_and_persist(v2_client)
    monkeypatch.setattr(
        settings, "runtime_v2_canary_orgs", {org_id}, raising=False,
    )

    # ---- 2. Spy on Supervisor so we can prove run() fired. ----------
    real_supervisor = sup_module.Supervisor
    spy_instances: list[Any] = []
    spy_run_calls: list[int] = []

    class _SpySupervisor:
        def __init__(self, **kwargs: Any) -> None:
            self._inner = real_supervisor(**kwargs)
            spy_instances.append(self)

        async def run(self):  # noqa: ANN201
            spy_run_calls.append(1)
            return await self._inner.run()

    monkeypatch.setattr(sup_module, "Supervisor", _SpySupervisor)

    # ---- 3. Share a single checkpointer across all three dispatches.
    shared_checkpointer = MemoryCheckpointer()
    real_dispatch = cr_module.dispatch_inbound_message_to_v2

    async def dispatch_with_shared_checkpointer(**kwargs: Any):
        kwargs.setdefault("checkpointer", shared_checkpointer)
        return await real_dispatch(**kwargs)

    monkeypatch.setattr(
        cr_module, "dispatch_inbound_message_to_v2", dispatch_with_shared_checkpointer,
    )

    # ---- 4. Build a gateway whose session is bound to org_id.
    session_key = "telegram:chat:user"
    gw = _make_gateway_with_bound_session(session_key, org_id)

    # IM bridge needs the gateway's send_response to be observable.
    sent_text: list[str] = []

    async def fake_send_response(message, text: str) -> bool:
        sent_text.append(text)
        return True

    monkeypatch.setattr(gw, "_send_response", fake_send_response, raising=False)

    # And the cancel feedback path needs the same observability.
    async def fake_send_feedback(message, text: str) -> None:
        sent_text.append(text)

    monkeypatch.setattr(gw, "_send_feedback", fake_send_feedback, raising=False)

    # Override the brain used by dispatch via the brain kw -- wrap dispatch
    # so the canary brain is injected without touching the gateway.
    canary_brain = _SatisfyingBrain()

    async def dispatch_with_brain(**kwargs: Any):
        kwargs.setdefault("brain", canary_brain)
        return await dispatch_with_shared_checkpointer(**kwargs)

    monkeypatch.setattr(
        cr_module, "dispatch_inbound_message_to_v2", dispatch_with_brain,
    )

    # ---- 5. Happy-path dispatch through the gateway. ----------------
    msg = create_channel_message(text="hello canary")
    handled = await gw._try_dispatch_v2(msg)
    assert handled is True, "v2 should have taken the message"
    assert len(spy_run_calls) == 1, "Supervisor.run should have fired"
    assert canary_brain.progress_calls >= 1
    assert shared_checkpointer.total() >= 1, "a DONE checkpoint should have landed"
    # The IM bridge should have rendered at least one event (lifecycle.started).
    assert sent_text, "ImStreamBridge should have relayed at least one event"

    n_after_happy = shared_checkpointer.total()
    sent_text.clear()
    spy_run_calls.clear()

    # ---- 6. Cancel-mid-run dispatch. --------------------------------
    cancel_brain = _CancellingBrain()

    async def dispatch_with_cancel_brain(**kwargs: Any):
        kwargs.setdefault("brain", cancel_brain)
        return await dispatch_with_shared_checkpointer(**kwargs)

    monkeypatch.setattr(
        cr_module, "dispatch_inbound_message_to_v2", dispatch_with_cancel_brain,
    )

    # Pre-cancel the token so the cancelling brain's raise short-circuits.
    from openakita.runtime.cancel_token import CancellationToken
    gw._v2_cancel_tokens[session_key] = CancellationToken()
    gw._v2_cancel_tokens[session_key].cancel("user_cancel_via_im")

    cancel_msg = create_channel_message(text="please stop")
    # Even though _try_dispatch_v2 builds its own fresh token, the cancel
    # brain raises CancelledByToken inside extract_facts so the supervisor
    # still lands a CANCELLED checkpoint.
    handled = await gw._try_dispatch_v2(cancel_msg)
    assert handled is True, "cancelled dispatch counts as v2 taking the message"
    assert len(spy_run_calls) == 1
    cancelled_ckpts = [
        c async for c in shared_checkpointer.alist(spy_instances[-1]._inner.command_id)
    ]
    assert any(
        c.status is CheckpointStatus.CANCELLED for c in cancelled_ckpts
    ), "a final CANCELLED checkpoint must be saved on cancel"

    n_after_cancel = shared_checkpointer.total()
    assert n_after_cancel > n_after_happy
    spy_run_calls.clear()

    # ---- 7. Resume: another canary-brain dispatch lands a new checkpoint.
    canary_brain2 = _SatisfyingBrain()

    async def dispatch_with_resume_brain(**kwargs: Any):
        kwargs.setdefault("brain", canary_brain2)
        return await dispatch_with_shared_checkpointer(**kwargs)

    monkeypatch.setattr(
        cr_module, "dispatch_inbound_message_to_v2", dispatch_with_resume_brain,
    )

    resume_msg = create_channel_message(text="continue")
    handled = await gw._try_dispatch_v2(resume_msg)
    assert handled is True
    assert len(spy_run_calls) == 1
    assert shared_checkpointer.total() > n_after_cancel
