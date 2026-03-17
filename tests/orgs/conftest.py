"""Shared fixtures for AgentOrg test suite."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.orgs.models import (
    EdgeType,
    InboxMessage,
    InboxPriority,
    MemoryScope,
    MemoryType,
    MsgType,
    NodeSchedule,
    NodeStatus,
    Organization,
    OrgEdge,
    OrgMemoryEntry,
    OrgMessage,
    OrgNode,
    OrgStatus,
    ScheduleType,
)
from openakita.orgs.manager import OrgManager


# ---------------------------------------------------------------------------
# Directory fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory with standard sub-folders."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture()
def org_manager(tmp_data_dir: Path) -> OrgManager:
    return OrgManager(tmp_data_dir)


# ---------------------------------------------------------------------------
# Org / Node / Edge factories
# ---------------------------------------------------------------------------


def make_node(
    id: str = "node_ceo",
    role_title: str = "CEO",
    level: int = 0,
    department: str = "管理层",
    **kwargs: Any,
) -> OrgNode:
    defaults = dict(
        id=id,
        role_title=role_title,
        role_goal=f"{role_title}的目标",
        role_backstory=f"{role_title}的背景",
        level=level,
        department=department,
    )
    defaults.update(kwargs)
    return OrgNode(**defaults)


def make_edge(
    source: str = "node_ceo",
    target: str = "node_cto",
    edge_type: EdgeType = EdgeType.HIERARCHY,
    **kwargs: Any,
) -> OrgEdge:
    return OrgEdge(source=source, target=target, edge_type=edge_type, **kwargs)


def make_org(
    id: str = "org_test",
    name: str = "测试组织",
    nodes: list[OrgNode] | None = None,
    edges: list[OrgEdge] | None = None,
    **kwargs: Any,
) -> Organization:
    if nodes is None:
        nodes = [
            make_node("node_ceo", "CEO", 0, "管理层"),
            make_node("node_cto", "CTO", 1, "技术部"),
            make_node("node_dev", "开发", 2, "技术部"),
        ]
    if edges is None:
        edges = [
            make_edge("node_ceo", "node_cto"),
            make_edge("node_cto", "node_dev"),
        ]
    return Organization(id=id, name=name, nodes=nodes, edges=edges, **kwargs)


@pytest.fixture()
def sample_org() -> Organization:
    return make_org()


@pytest.fixture()
def persisted_org(org_manager: OrgManager) -> Organization:
    """An organization that has been saved to disk."""
    return org_manager.create(make_org().to_dict())


@pytest.fixture()
def org_dir(persisted_org: Organization, org_manager: OrgManager) -> Path:
    """Path to the persisted org's data directory."""
    return org_manager._org_dir(persisted_org.id)


# ---------------------------------------------------------------------------
# Mock runtime for modules that need it
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_runtime(persisted_org: Organization, org_manager: OrgManager, org_dir: Path):
    """A mock OrgRuntime with enough functionality for unit tests."""
    rt = MagicMock()
    rt._manager = org_manager
    rt.get_org = MagicMock(return_value=persisted_org)
    rt._active_orgs = {persisted_org.id: persisted_org}
    rt._chain_delegation_depth = {}

    from openakita.orgs.event_store import OrgEventStore
    from openakita.orgs.blackboard import OrgBlackboard
    from openakita.orgs.messenger import OrgMessenger

    es = OrgEventStore(org_dir, persisted_org.id)
    bb = OrgBlackboard(org_dir, persisted_org.id)
    messenger = OrgMessenger(persisted_org, org_dir)

    rt.get_event_store = MagicMock(return_value=es)
    rt.get_blackboard = MagicMock(return_value=bb)
    rt.get_messenger = MagicMock(return_value=messenger)
    rt._broadcast_ws = AsyncMock()
    rt._save_org = AsyncMock()

    scaler_mock = MagicMock()
    scaler_mock.try_reclaim_idle_clones = AsyncMock(return_value=[])
    rt.get_scaler = MagicMock(return_value=scaler_mock)

    return rt
