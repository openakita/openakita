"""Unit tests for the manager-backed :class:`JsonOrgStore` shim.

Sprint 13 H2 治根 (RC-1): the JSON store no longer writes; this
suite pins the new read-only contract -- mint orgs round-trip
through ``OrgManager.as_orgv2``, write methods raise, malformed
legacy payloads are tolerated.

The exhaustive shim contract lives in
:mod:`tests.orgs.test_json_org_store_shim`; this file keeps the
historical ``tests/runtime`` location alive for the
``pytest tests/runtime/orgs/`` glob and pins the deprecation
boundary at the *unit* layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.store import JsonOrgStore, OrgNotFound
from openakita.runtime.models import OrgStatus, OrgV2, new_org_id


def _mk_org(name: str = "Test", org_id: str | None = None) -> OrgV2:
    return OrgV2(
        id=org_id or new_org_id(),
        name=name,
        template_id="content_ops",
        description=None,
        nodes=[],
        edges=[],
    )


def _shim_with_manager(tmp_path: Path) -> tuple[JsonOrgStore, OrgManager]:
    manager = OrgManager(tmp_path)
    store = JsonOrgStore(path=tmp_path / "orgs_v2.json", manager=manager)
    return store, manager


def test_get_unknown_raises(tmp_path: Path) -> None:
    """Miss contract preserved -- HTTP 404 mappers stay green."""
    store, _ = _shim_with_manager(tmp_path)
    with pytest.raises(OrgNotFound):
        store.get("org_unknown")


def test_get_finds_mint_orgs_via_manager(tmp_path: Path) -> None:
    """Mint via OrgManager (the SSoT) -> visible to the shim's read path."""
    store, manager = _shim_with_manager(tmp_path)
    minted = manager.create({"name": "Alpha"})
    got = store.get(minted.id)
    assert got.id == minted.id
    assert got.name == "Alpha"
    # DORMANT (legacy default) -> CREATED (v2)
    assert got.status is OrgStatus.CREATED


def test_list_returns_mint_orgs(tmp_path: Path) -> None:
    """``list()`` enumerates manager orgs (no legacy file = manager only)."""
    store, manager = _shim_with_manager(tmp_path)
    a = manager.create({"name": "A"})
    b = manager.create({"name": "B"})
    listed = store.list()
    assert {o.id for o in listed} == {a.id, b.id}


def test_create_raises_deprecation(tmp_path: Path) -> None:
    """Writes are forbidden -- callers must use OrgManager.create."""
    store, _ = _shim_with_manager(tmp_path)
    with pytest.raises(RuntimeError, match="JsonOrgStore.create is deprecated"):
        store.create(_mk_org())


def test_patch_raises_deprecation(tmp_path: Path) -> None:
    store, _ = _shim_with_manager(tmp_path)
    with pytest.raises(RuntimeError, match="JsonOrgStore.patch is deprecated"):
        store.patch("org_unknown", name="x")


def test_delete_raises_deprecation(tmp_path: Path) -> None:
    store, _ = _shim_with_manager(tmp_path)
    with pytest.raises(RuntimeError, match="JsonOrgStore.delete is deprecated"):
        store.delete("org_unknown")


def test_persistence_via_manager_round_trips(tmp_path: Path) -> None:
    """OrgManager persistence survives a fresh shim instance over the same dir.

    The new SSoT contract: ``data/orgs/<id>/org.json`` is the
    durable record; the shim is just a wire-format projection.
    Reopening the manager + shim picks every persisted org back up.
    """
    manager_a = OrgManager(tmp_path)
    a = manager_a.create({"name": "Persisted A"})
    b = manager_a.create({"name": "Persisted B"})
    manager_b = OrgManager(tmp_path)
    fresh = JsonOrgStore(path=tmp_path / "orgs_v2.json", manager=manager_b)
    assert {o.id for o in fresh.list()} == {a.id, b.id}


def test_malformed_legacy_payload_is_tolerated(tmp_path: Path) -> None:
    """A corrupted ``data/orgs_v2.json`` (legacy soak file) cannot break the shim.

    No mint orgs + bad legacy file -> empty list, no raise.
    """
    bad_path = tmp_path / "orgs_v2.json"
    bad_path.write_text(
        json.dumps({"orgs": {"x": {"bad": "shape"}}}),
        encoding="utf-8",
    )
    manager = OrgManager(tmp_path)
    store = JsonOrgStore(path=bad_path, manager=manager)
    assert store.list() == []
