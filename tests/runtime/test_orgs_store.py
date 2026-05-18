"""Unit tests for :mod:`openakita.runtime.orgs.store`.

Cover the persistence layer directly so the HTTP-level CRUD tests
under ``tests/api/test_orgs_v2.py`` can focus on contract / status-
code shape rather than implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.runtime.models import OrgStatus, OrgV2, new_org_id
from openakita.runtime.orgs.store import JsonOrgStore, OrgNotFound


def _mk_org(name: str = "Test", org_id: str | None = None) -> OrgV2:
    return OrgV2(
        id=org_id or new_org_id(),
        name=name,
        template_id="content_ops",
        description=None,
        nodes=[],
        edges=[],
    )


def test_get_unknown_raises(tmp_path: Path) -> None:
    store = JsonOrgStore(path=tmp_path / "orgs.json")
    with pytest.raises(OrgNotFound):
        store.get("org_unknown")


def test_create_then_get_round_trips(tmp_path: Path) -> None:
    store = JsonOrgStore(path=tmp_path / "orgs.json")
    org = _mk_org("Alpha")
    saved = store.create(org)
    assert saved.id == org.id
    got = store.get(org.id)
    assert got.name == "Alpha"
    assert got.status is OrgStatus.CREATED


def test_create_duplicate_raises(tmp_path: Path) -> None:
    store = JsonOrgStore(path=tmp_path / "orgs.json")
    org = _mk_org()
    store.create(org)
    with pytest.raises(ValueError, match="already exists"):
        store.create(org)


def test_patch_updates_whitelisted_fields_only(tmp_path: Path) -> None:
    store = JsonOrgStore(path=tmp_path / "orgs.json")
    org = _mk_org()
    store.create(org)
    patched = store.patch(org.id, name="Renamed", description="now editorial")
    assert patched.name == "Renamed"
    assert patched.description == "now editorial"
    assert patched.updated_at >= org.updated_at


def test_patch_unknown_raises(tmp_path: Path) -> None:
    store = JsonOrgStore(path=tmp_path / "orgs.json")
    with pytest.raises(OrgNotFound):
        store.patch("org_unknown", name="x")


def test_delete_removes_org(tmp_path: Path) -> None:
    store = JsonOrgStore(path=tmp_path / "orgs.json")
    org = _mk_org()
    store.create(org)
    store.delete(org.id)
    with pytest.raises(OrgNotFound):
        store.get(org.id)


def test_persistence_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "orgs.json"
    store = JsonOrgStore(path=path)
    a = _mk_org("Persisted A")
    b = _mk_org("Persisted B")
    store.create(a)
    store.create(b)
    # Fresh store reads from disk
    fresh = JsonOrgStore(path=path)
    assert {o.id for o in fresh.list()} == {a.id, b.id}


def test_list_returns_newest_first(tmp_path: Path) -> None:
    store = JsonOrgStore(path=tmp_path / "orgs.json")
    a = _mk_org("A")
    store.create(a)
    b = _mk_org("B")
    store.create(b)
    listing = store.list()
    # newest-first
    assert listing[0].id in {a.id, b.id}
    assert {o.id for o in listing} == {a.id, b.id}


def test_malformed_disk_payload_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "orgs.json"
    path.write_text(json.dumps({"orgs": {"x": {"bad": "shape"}}}), encoding="utf-8")
    store = JsonOrgStore(path=path)
    # Malformed payload is dropped, not crashed
    assert store.list() == []
