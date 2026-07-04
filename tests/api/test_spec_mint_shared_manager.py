"""Contract tests for the spec/mint shared :class:`OrgManager` invariant.

v29 CRUD-1 / CRUD-2 background
------------------------------

Commit ``a2b18fc4`` (Sprint 13 H2 RC-1) unified the spec and mint
write paths through :class:`OrgManager` and made
:class:`openakita.orgs.store.JsonOrgStore` a manager-backed read
shim. The shim docstring + ``orgs_v2.py::_resolve_manager_for_writes``
both assume that ``api/server.py`` calls
:func:`openakita.orgs.store.set_default_org_manager` right after
constructing ``app.state.org_manager`` so the two routers resolve
to the *same* :class:`OrgManager` instance (and the same
``_cache: dict[str, Organization]``).

The original commit forgot that one line of wiring. Production
therefore ran two parallel :class:`OrgManager` instances rooted
at the same disk directory but with independent in-memory caches:

* mint routes (``/api/v2/orgs``) called ``app.state.org_manager``
  directly -- instance A;
* spec routes (``/api/v2/orgs-spec``) called
  ``get_default_store()._get_manager()`` which lazily built a
  fresh settings-derived manager -- instance B.

A spec-side PATCH or DELETE wrote to disk via B and updated
B._cache, but a follow-up mint-side GET hit A._cache and returned
the stale snapshot. v29 CRUD-1 saw the rename split; CRUD-2 saw
the ghost-org-after-delete split. The follow-up commit added the
``set_default_org_manager`` call; these three tests pin the
cross-router invariant so any future composition-root refactor
that drops the wiring is caught immediately.

Refs: ``_v29_biz/v29_regression_report.md`` CRUD-1 / CRUD-2 / §5.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openakita.api.server import create_app
from openakita.config import settings
from openakita.orgs.manager import OrgManager
from openakita.orgs.store import (
    JsonOrgStore,
    get_default_store,
    reset_default_store,
    set_default_org_manager,
)


@pytest.fixture
def wired_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[TestClient, OrgManager]:
    """Compose the real FastAPI app at ``tmp_path`` and yield ``(client, manager)``.

    Using ``create_app()`` (mirroring ``openakita serve`` minus the IM
    plumbing) exercises the exact wiring path the server uses at boot,
    so a regression that drops ``set_default_org_manager(org_manager)``
    breaks the identity assertion in
    :func:`test_module_level_default_manager_is_app_state_manager`.

    ``settings.project_root`` is monkeypatched so OrgManager writes land
    under ``tmp_path/data/orgs/`` instead of the real ``D:/OpenAkita/data``
    tree (303 live orgs there at the time of writing). The TestClient
    presents ``client.host == "testclient"`` and so fails the
    ``_is_local_request`` bypass in :mod:`openakita.api.auth`; we mint
    a real access token to authenticate, mirroring the desktop GUI.

    Because ``project_root`` points at a *fresh* ``tmp_path``, the app's
    ``WebAccessConfig`` (rooted at ``tmp_path/data``) has no password set,
    so the setup gate middleware — which runs ahead of auth/business
    logic — would 428 the non-loopback ``"testclient"`` host before the
    warmup GET ever reached the org routers. We therefore complete the
    setup step (``change_password``) on the app's own config, exactly as a
    properly provisioned deployment would, instead of skipping the test or
    relaxing the production gate.
    """
    monkeypatch.setattr(settings, "project_root", tmp_path, raising=False)
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    set_default_org_manager(None)
    reset_default_store()

    app = create_app()
    # Satisfy the setup gate: provision a web password on the same config
    # instance the gate middleware closed over (``app.state.web_access_config``).
    app.state.web_access_config.change_password("contract-test-setup-pass")
    token = app.state.web_access_config.create_access_token()
    client = TestClient(app, raise_server_exceptions=True)
    client.headers.update({"Authorization": f"Bearer {token}"})
    try:
        yield client, app.state.org_manager
    finally:
        client.close()
        set_default_org_manager(None)
        reset_default_store()


def _mint(manager: OrgManager, *, org_id: str, name: str) -> None:
    """Mint a minimal org via ``OrgManager.create`` (deterministic id+name)."""
    organization = manager.create(
        {"id": org_id, "name": name, "description": "v29 CRUD wiring contract"}
    )
    assert organization.id == org_id


def test_spec_patch_visible_via_mint_get(
    wired_app: tuple[TestClient, OrgManager],
) -> None:
    """v29 CRUD-1 contract: spec PATCH name -> mint GET must see the new name.

    Pre-fix: a pre-PATCH mint GET warmed the mint manager's cache with
    the old name; the spec PATCH then landed on the *other* manager's
    cache + disk, leaving the mint manager pinned to the stale entry.
    The second mint GET therefore returned the pre-PATCH name even
    though disk and spec API both reflected the new name.
    Post-fix: both routers resolve to the same manager so the PATCH
    updates the only cache there is.
    """
    client, manager = wired_app
    org_id = "org_v29_crud1_wire_test"
    _mint(manager, org_id=org_id, name="v29_crud1_original")

    # Pre-warm the mint manager's cache so a stale-read regression
    # would observably surface (an empty cache would mask the bug
    # behind an unconditional disk re-read).
    warmup = client.get(f"/api/v2/orgs/{org_id}")
    assert warmup.status_code == 200, warmup.text
    assert warmup.json()["name"] == "v29_crud1_original"

    patch = client.patch(
        f"/api/v2/orgs-spec/{org_id}", json={"name": "v29_crud1_renamed"}
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["name"] == "v29_crud1_renamed"

    after = client.get(f"/api/v2/orgs/{org_id}")
    assert after.status_code == 200, after.text
    assert after.json()["name"] == "v29_crud1_renamed", (
        "mint GET returned the pre-PATCH name -- spec and mint routes "
        "are on different OrgManager instances (v29 CRUD-1 regression: "
        "api/server.py missed set_default_org_manager wiring)"
    )


def test_spec_delete_invisible_via_mint_get(
    wired_app: tuple[TestClient, OrgManager],
) -> None:
    """v29 CRUD-2 contract: spec DELETE -> mint GET must 404.

    Pre-fix: spec DELETE removed the on-disk org dir and popped its
    entry from the spec manager's cache, but the mint manager's cache
    still held the pre-delete Organization object. Mint GET hit the
    cache, never re-checked disk, and returned 200 with stale body --
    a 'ghost org' in the UI even though persistence no longer had it.
    Post-fix: the single shared manager invalidates the cache on
    delete, so the next mint GET takes the disk path and 404s.
    """
    client, manager = wired_app
    org_id = "org_v29_crud2_wire_test"
    _mint(manager, org_id=org_id, name="v29_crud2_doomed")

    warmup = client.get(f"/api/v2/orgs/{org_id}")
    assert warmup.status_code == 200, warmup.text

    deleted = client.delete(f"/api/v2/orgs-spec/{org_id}")
    assert deleted.status_code in (200, 204), deleted.text

    after = client.get(f"/api/v2/orgs/{org_id}")
    assert after.status_code == 404, (
        f"mint GET returned {after.status_code} with body {after.text!r} "
        "-- spec and mint routes are on different OrgManager instances "
        "(v29 CRUD-2 regression: api/server.py missed "
        "set_default_org_manager wiring)"
    )


def test_module_level_default_manager_is_app_state_manager(
    wired_app: tuple[TestClient, OrgManager],
) -> None:
    """Wiring contract: ``get_default_store()._get_manager() is app.state.org_manager``.

    This is the structural assertion behind CRUD-1 / CRUD-2: no matter
    which side of the API the request lands on, both must resolve to
    the *same* :class:`OrgManager` Python object (and therefore the
    same in-memory cache). If the ``set_default_org_manager`` call in
    ``api/server.py`` is ever removed or moved before
    ``app.state.org_manager`` is bound, this assertion fails
    deterministically -- no need to reproduce the timing-dependent
    stale-cache symptom.
    """
    _, app_state_manager = wired_app
    store = get_default_store()
    assert isinstance(store, JsonOrgStore), (
        f"default store is {type(store).__name__}; this contract assumes "
        "the default JSON backend (sqlite-backed deployments have a "
        "separate wiring path)"
    )
    shim_manager = store._get_manager()
    assert shim_manager is app_state_manager, (
        "JsonOrgStore.get_default_store() resolved to a *different* "
        "OrgManager instance than app.state.org_manager "
        f"(id(shim)={id(shim_manager)} vs "
        f"id(app_state)={id(app_state_manager)}). The spec router will "
        "write through a manager whose _cache is not seen by the mint "
        "router -- exactly the v29 CRUD-1/2 split."
    )
