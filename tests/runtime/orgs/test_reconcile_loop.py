"""v22 P1: ``OrgCommandService`` background reconcile loop.

Companion to :mod:`tests.runtime.orgs.test_supervisor_hard_ceiling`:
the hard ceiling stops a wedged ``supervisor.run()`` from pinning the
``_running_by_root`` slot. Reconcile is the second line of defence --
even if the hard ceiling somehow gets bypassed (process restart that
loses the asyncio.wait_for, raw KeyError before the finally release
runs, etc.), :meth:`OrgCommandService._reconcile_tick` periodically
drops slots whose owner is provably gone.

The reconcile contract:

* a slot whose ``command_id`` is missing from ``_commands`` -> drop;
* a slot whose ``_commands[cid].status`` is terminal -> drop;
* a slot whose ``_active_supervisors`` entry is gone *and* the command
  thinks it is still running -> drop (the original ``cmd_..._f092f4``
  shape);
* a slot whose owner is genuinely live (status=running + supervisor
  registered) -> leave alone.

The tick is also synchronous; the loop only handles the cadence. So
the assertions all drive ``_reconcile_tick`` directly and verify
``_running_by_root`` mutations without standing up an event loop.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openakita.orgs.command_service import OrgCommandService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> OrgCommandService:
    """Bare ``OrgCommandService`` with a no-op runtime mock.

    We do not exercise ``submit`` here; the tests populate the
    bookkeeping dicts by hand to isolate the reconcile contract from
    the submit / supervisor pipeline.
    """
    runtime = MagicMock()
    runtime.has_active_delegations = MagicMock(return_value=False)
    return OrgCommandService(runtime)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reconcile_pops_stale_running_by_root(service: OrgCommandService) -> None:
    """Slot with no matching ``_commands`` / ``_active_supervisors`` -> drop."""
    service._running_by_root[("orgX", "producer")] = "fake_cid_gone"

    service._reconcile_tick()

    assert ("orgX", "producer") not in service._running_by_root


def test_reconcile_pops_when_command_done_but_slot_lingers(
    service: OrgCommandService,
) -> None:
    """Slot pointing at a terminal command -> drop (real leak signature)."""
    service._commands["c1"] = {
        "command_id": "c1",
        "org_id": "orgX",
        "root_node_id": "root",
        "status": "done",
        "phase": "done",
    }
    service._running_by_root[("orgX", "root")] = "c1"

    service._reconcile_tick()

    assert ("orgX", "root") not in service._running_by_root


def test_reconcile_pops_when_command_running_but_supervisor_gone(
    service: OrgCommandService,
) -> None:
    """``cmd_..._f092f4`` shape: status=running but no live supervisor."""
    service._commands["c2"] = {
        "command_id": "c2",
        "org_id": "orgX",
        "root_node_id": "root",
        "status": "running",
        "phase": "running",
    }
    # NOTE: NO entry in ``_active_supervisors`` -- this is the leak.
    service._running_by_root[("orgX", "root")] = "c2"

    service._reconcile_tick()

    assert ("orgX", "root") not in service._running_by_root


def test_reconcile_does_not_kill_running(service: OrgCommandService) -> None:
    """Genuine live command (status=running + supervisor present) -> leave alone."""
    service._commands["c3"] = {
        "command_id": "c3",
        "org_id": "orgX",
        "root_node_id": "root",
        "status": "running",
        "phase": "running",
    }
    # A live supervisor proxy -- any non-None value is enough for the
    # ``cid in self._active_supervisors`` membership check.
    service._active_supervisors["c3"] = MagicMock(name="LiveSupervisor")
    service._running_by_root[("orgX", "root")] = "c3"

    service._reconcile_tick()

    assert service._running_by_root.get(("orgX", "root")) == "c3"
    assert "c3" in service._active_supervisors


def test_reconcile_pops_when_command_error(service: OrgCommandService) -> None:
    """Status=error slot must also drop (treated as terminal)."""
    service._commands["c4"] = {
        "command_id": "c4",
        "org_id": "orgX",
        "root_node_id": "root",
        "status": "error",
        "phase": "error",
    }
    service._running_by_root[("orgX", "root")] = "c4"

    service._reconcile_tick()

    assert ("orgX", "root") not in service._running_by_root


def test_reconcile_pops_when_command_cancelled(service: OrgCommandService) -> None:
    """Status=cancelled slot must also drop (treated as terminal)."""
    service._commands["c5"] = {
        "command_id": "c5",
        "org_id": "orgX",
        "root_node_id": "root",
        "status": "cancelled",
        "phase": "cancelled",
    }
    service._running_by_root[("orgX", "root")] = "c5"

    service._reconcile_tick()

    assert ("orgX", "root") not in service._running_by_root


def test_reconcile_handles_mixed_state_in_one_tick(service: OrgCommandService) -> None:
    """One tick must drop stale entries and preserve live entries together."""
    # Live -- keep.
    service._commands["live"] = {
        "command_id": "live",
        "org_id": "orgA",
        "root_node_id": "n",
        "status": "running",
    }
    service._active_supervisors["live"] = MagicMock(name="LiveSupervisor")
    service._running_by_root[("orgA", "n")] = "live"
    # Stale -- drop.
    service._commands["stale"] = {
        "command_id": "stale",
        "org_id": "orgB",
        "root_node_id": "n",
        "status": "done",
    }
    service._running_by_root[("orgB", "n")] = "stale"
    # Orphan -- drop.
    service._running_by_root[("orgC", "n")] = "ghost_cid"

    service._reconcile_tick()

    assert service._running_by_root.get(("orgA", "n")) == "live"
    assert ("orgB", "n") not in service._running_by_root
    assert ("orgC", "n") not in service._running_by_root


@pytest.mark.asyncio
async def test_start_stop_reconcile_loop_is_idempotent(
    service: OrgCommandService, monkeypatch
) -> None:
    """Start + stop are safe to call twice; stop terminates the task."""
    # Short interval so the loop actually wakes up at least once.
    from openakita.config import settings

    monkeypatch.setattr(settings, "orgs_reconcile_interval_s", 1, raising=False)

    await service.start_reconcile_loop()
    task1 = service._reconcile_task
    assert task1 is not None
    assert not task1.done()
    # Second call must NOT spawn a duplicate task.
    await service.start_reconcile_loop()
    assert service._reconcile_task is task1

    await service.stop_reconcile_loop(timeout=2.0)
    assert service._reconcile_task is None
    # Second stop is a no-op.
    await service.stop_reconcile_loop(timeout=0.5)


@pytest.mark.asyncio
async def test_start_reconcile_loop_disabled_when_interval_zero(
    service: OrgCommandService, monkeypatch
) -> None:
    """interval=0 keeps the loop off (zero CPU overhead in test envs)."""
    from openakita.config import settings

    monkeypatch.setattr(settings, "orgs_reconcile_interval_s", 0, raising=False)

    await service.start_reconcile_loop()
    assert service._reconcile_task is None
