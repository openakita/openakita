"""Phase 2 tests: JsonOrgStore as a manager-backed shim (Sprint 13 H2 / RC-1).

What this suite pins:

* Reads route through :class:`OrgManager` via
  :meth:`OrgManager.as_orgv2`. Mint orgs are visible to
  ``JsonOrgStore.get`` / ``.list`` -- the v25 H2 invariant
  ("mint org reachable through the spec read path") regression-
  guarded right at the shim boundary.
* Legacy ``data/orgs_v2.json`` rows are unioned into ``.list``
  during the deprecation soak so v25 leftover orgs don't
  vanish mid-migration; manager entries always win on id
  conflict.
* ``.create`` / ``.patch`` / ``.delete`` raise
  :class:`RuntimeError` with a deprecation message so callers
  cannot accidentally re-introduce the RC-1 double-write split.

Sister suites:

* :mod:`tests.orgs.test_org_manager_orgv2_projection` exercises
  the read-side projection helper directly.
* :mod:`tests.api.test_orgs_v2_spec_routes_use_manager` exercises
  the HTTP-level outcome via the migrated spec routes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.store import JsonOrgStore, OrgNotFound, set_default_org_manager
from openakita.runtime.models import OrgV2, new_org_id


@pytest.fixture
def manager(tmp_path: Path) -> OrgManager:
    """Manager rooted at ``tmp_path``.

    The shim is configured with this manager via the constructor
    so the tests don't depend on the process-wide singleton.
    """
    return OrgManager(tmp_path)


@pytest.fixture
def shim(tmp_path: Path, manager: OrgManager) -> JsonOrgStore:
    """Manager-backed shim with the legacy fallback file under ``tmp_path``."""
    return JsonOrgStore(path=tmp_path / "orgs_v2.json", manager=manager)


@pytest.fixture(autouse=True)
def _reset_default_manager() -> None:
    """Make sure the process-wide default manager doesn't leak across tests."""
    set_default_org_manager(None)
    yield
    set_default_org_manager(None)


def _seed_org(manager: OrgManager, *, name: str = "Acme") -> str:
    org = manager.create({"name": name, "description": "test"})
    return org.id


def _write_legacy_fallback(path: Path, *orgs: OrgV2) -> None:
    """Smuggle a legacy ``data/orgs_v2.json`` payload into ``path``."""
    payload = {"orgs": {o.id: o.to_jsonable() for o in orgs}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_org(name: str = "Legacy") -> OrgV2:
    return OrgV2(id=new_org_id(), name=name, template_id="content_ops")


# ---------------------------------------------------------------------------
# Read contract: get/list route through OrgManager
# ---------------------------------------------------------------------------


def test_get_routes_to_manager(shim: JsonOrgStore, manager: OrgManager) -> None:
    """Mint a fresh org via the manager; the shim must see it
    (this is the RC-1 regression: pre-fix, ``shim.get`` 404'd
    on every mint id)."""
    org_id = _seed_org(manager, name="MintCorp")
    got = shim.get(org_id)
    assert got.id == org_id
    assert got.name == "MintCorp"


def test_get_unknown_id_raises_orgnotfound(shim: JsonOrgStore) -> None:
    """Miss contract: callers that map this to HTTP 404 must keep working."""
    with pytest.raises(OrgNotFound):
        shim.get("org_does_not_exist")


def test_list_returns_manager_orgs(shim: JsonOrgStore, manager: OrgManager) -> None:
    """Manager.list_orgs flows through the shim's ``.list`` projection."""
    a = _seed_org(manager, name="A")
    b = _seed_org(manager, name="B")
    listed = shim.list()
    assert {o.id for o in listed} == {a, b}


def test_list_empty_when_no_manager_orgs_and_no_legacy(shim: JsonOrgStore) -> None:
    """No mint orgs, no legacy file -> empty list (not raise)."""
    assert shim.list() == []


# ---------------------------------------------------------------------------
# Legacy fallback: data/orgs_v2.json soak path
# ---------------------------------------------------------------------------


def test_list_unions_manager_and_legacy_data(
    tmp_path: Path, shim: JsonOrgStore, manager: OrgManager
) -> None:
    """Legacy ``data/orgs_v2.json`` rows show up alongside mint orgs.

    During the 3-month soak after Sprint 13 H2 lands, callers
    must keep seeing v25 leftovers via ``.list`` -- otherwise
    a partially-migrated deployment 404s on legacy ids.
    """
    mint_id = _seed_org(manager, name="MintCorp")
    legacy = _legacy_org(name="LegacySpec")
    _write_legacy_fallback(tmp_path / "orgs_v2.json", legacy)
    listed = shim.list()
    listed_ids = {o.id for o in listed}
    assert mint_id in listed_ids
    assert legacy.id in listed_ids


def test_legacy_fallback_get_works(tmp_path: Path, shim: JsonOrgStore) -> None:
    """Get a legacy id when the manager doesn't know it -- soak path."""
    legacy = _legacy_org(name="LegacyOnly")
    _write_legacy_fallback(tmp_path / "orgs_v2.json", legacy)
    got = shim.get(legacy.id)
    assert got.id == legacy.id
    assert got.name == "LegacyOnly"


def test_manager_wins_on_id_collision(
    tmp_path: Path, manager: OrgManager
) -> None:
    """When the same id exists in both the manager and the legacy
    file, the manager's projection must win (it is the SSoT)."""
    org = manager.create({"id": "org_shared", "name": "ManagerWins"})
    legacy = OrgV2(id="org_shared", name="LegacyShouldLose", template_id=None)
    legacy_path = tmp_path / "orgs_v2.json"
    _write_legacy_fallback(legacy_path, legacy)
    shim = JsonOrgStore(path=legacy_path, manager=manager)
    got = shim.get("org_shared")
    assert got.name == "ManagerWins"
    assert got.id == org.id
    listed = {o.id: o for o in shim.list()}
    assert listed["org_shared"].name == "ManagerWins"


def test_malformed_legacy_payload_is_tolerated(
    tmp_path: Path, manager: OrgManager
) -> None:
    """A corrupted legacy file must not break the shim."""
    bad_path = tmp_path / "orgs_v2.json"
    bad_path.write_text(
        json.dumps({"orgs": {"x": {"completely": "wrong"}}}),
        encoding="utf-8",
    )
    shim = JsonOrgStore(path=bad_path, manager=manager)
    assert shim.list() == []


# ---------------------------------------------------------------------------
# Write contract: every mutation raises RuntimeError pointing at OrgManager
# ---------------------------------------------------------------------------


def test_create_raises_deprecation_error(shim: JsonOrgStore) -> None:
    org = OrgV2(id=new_org_id(), name="N")
    with pytest.raises(RuntimeError, match="JsonOrgStore.create is deprecated"):
        shim.create(org)


def test_patch_raises_deprecation_error(shim: JsonOrgStore) -> None:
    with pytest.raises(RuntimeError, match="JsonOrgStore.patch is deprecated"):
        shim.patch("org_x", name="renamed")


def test_delete_raises_deprecation_error(shim: JsonOrgStore) -> None:
    with pytest.raises(RuntimeError, match="JsonOrgStore.delete is deprecated"):
        shim.delete("org_x")


def test_create_deprecation_message_points_at_org_manager(
    shim: JsonOrgStore,
) -> None:
    """The error message must name OrgManager so downstream
    log scrapers can route the warning to the right team."""
    org = OrgV2(id=new_org_id(), name="N")
    with pytest.raises(RuntimeError) as exc_info:
        shim.create(org)
    assert "OrgManager" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Default manager wiring
# ---------------------------------------------------------------------------


def test_set_default_org_manager_propagates_to_default_store(
    tmp_path: Path, manager: OrgManager
) -> None:
    """``set_default_org_manager`` must update the cached singleton
    so subsequent ``get_default_store().get(...)`` reads see the
    same SSoT instance the FastAPI ``app.state.org_manager`` does."""
    from openakita.orgs.store import reset_default_store

    store = reset_default_store(path=tmp_path / "orgs_v2.json")
    assert isinstance(store, JsonOrgStore)
    org_id = manager.create({"name": "X"}).id
    set_default_org_manager(manager)
    got = store.get(org_id)
    assert got.id == org_id
