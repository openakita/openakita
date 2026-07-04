"""Shared fixtures for the v2 orgs contract test suite (P-RC-9 P9.7gamma-1).

Mirrors the duck-typed mock pattern used by
``tests/api/test_p97_beta_smoke.py``. Each cluster test file
imports ``mint_app`` / ``mint_client`` from this conftest plus
the small helper builders for fake org / project / task envelopes.
Keeping the fixtures here (rather than a local helper module per
cluster) lets the cluster files stay under the ADR-0014 ~350 LOC
soft cap while still asserting real contract invariants.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import orgs_v2_runtime


@pytest.fixture
def mint_app() -> FastAPI:
    """Bare app with the v2 runtime router + 6 mock subsystems on app.state."""
    app = FastAPI()
    app.state.org_manager = MagicMock(name="OrgManager")
    app.state.org_runtime = MagicMock(name="OrgRuntime")
    app.state.org_command_service = MagicMock(name="OrgCommandService")
    app.state.org_blackboard = MagicMock(name="OrgBlackboard")
    app.state.project_store = MagicMock(name="ProjectStore")
    app.state.node_scheduler = MagicMock(name="NodeScheduler")
    # ``get_org_snapshot`` falls back to manager.get when absent (B10).
    app.state.org_runtime.get_org_snapshot = None
    app.include_router(orgs_v2_runtime.router)
    return app


@pytest.fixture
def mint_client(mint_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(mint_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helper builders -- duck-typed envelopes the mocks return.
# ---------------------------------------------------------------------------


def _async_return(value: Any) -> Any:
    """Return a ``MagicMock`` whose ``side_effect`` resolves to ``value``.

    Hoisted to conftest at P-RC-10 P10.5b (closes nit P9.7-B fixture
    extract). Originally duplicated across dispatch / nodes / ops /
    projects cluster files; now a single shared helper.
    """

    async def _ok(*args: Any, **kwargs: Any) -> Any:
        return value

    return MagicMock(side_effect=_ok)


def _async_raise(exc: Exception) -> Any:
    """Return a ``MagicMock`` whose awaited call raises ``exc``.

    Hoisted alongside ``_async_return`` at P-RC-10 P10.5b. Originally
    duplicated in dispatch + ops cluster files.
    """

    async def _bad(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return MagicMock(side_effect=_bad)


def fake_org(org_id: str = "org_test", name: str = "Test Org", **extra: Any) -> Any:
    """Return an object with ``to_dict()`` returning a v1-shape envelope."""
    obj = MagicMock(spec=["to_dict"])
    payload: dict[str, Any] = {
        "id": org_id,
        "name": name,
        "status": "dormant",
        "description": "",
        "nodes": [],
        "edges": [],
    }
    payload.update(extra)
    obj.to_dict.return_value = payload
    return obj


def fake_project(project_id: str = "p1", name: str = "P1", **extra: Any) -> Any:
    obj = MagicMock(spec=["to_dict"])
    payload: dict[str, Any] = {
        "id": project_id,
        "org_id": "org_x",
        "name": name,
        "description": "",
        "status": "planning",
        "project_type": "temporary",
        "tasks": [],
    }
    payload.update(extra)
    obj.to_dict.return_value = payload
    return obj


def fake_task(task_id: str = "t1", project_id: str = "p1", **extra: Any) -> Any:
    obj = MagicMock(spec=["to_dict"])
    payload: dict[str, Any] = {
        "id": task_id,
        "project_id": project_id,
        "title": "T",
        "status": "todo",
        "assignee_node_id": None,
    }
    payload.update(extra)
    obj.to_dict.return_value = payload
    return obj


def org_with_node(node_id: str | None = "n1") -> Any:
    """Build an org mock whose ``get_node`` returns an object iff ``node_id``.

    Useful for the B22-B33 endpoints that delegate to
    ``mgr.get(org_id).get_node(node_id)`` to gate 404 vs happy.
    """
    org = MagicMock()
    if node_id is None:
        org.get_node.return_value = None
    else:
        org.get_node.return_value = MagicMock(id=node_id)
    return org
