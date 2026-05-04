"""Phase 1 — characters + series CRUD via the registered FastAPI router."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _StubAPI:
    """See test_plugin_skeleton.py — same surface area."""

    def __init__(self, data_dir: Path) -> None:
        self._data = data_dir
        self._cfg: dict[str, Any] = {}
        self.routers: list[Any] = []
        self.spawned: list[asyncio.Task] = []

    def get_data_dir(self) -> Path:
        return self._data

    def get_config(self) -> dict[str, Any]:
        return dict(self._cfg)

    def set_config(self, updates: dict[str, Any]) -> None:
        self._cfg.update(updates)

    def log(self, msg: str, level: str = "info") -> None:
        pass

    def register_tools(self, definitions: list[dict[str, Any]], handler: Any) -> None:
        pass

    def register_api_routes(self, router: Any) -> None:
        self.routers.append(router)

    def spawn_task(self, coro: Any, name: str | None = None) -> asyncio.Task:
        loop = asyncio.get_event_loop()
        return loop.create_task(coro, name=name or "anon")


@pytest.fixture
async def client(tmp_path: Path):
    """Boot the plugin against a tmp dir and return a synchronous TestClient.

    aiosqlite's worker thread schedules futures back onto the loop that
    opened the connection. The synchronous ``TestClient`` runs request
    handlers in a separate thread loop while pytest-asyncio owns the main
    loop — so we explicitly ``close()`` the task manager (joining the
    worker thread) BEFORE the test exits to avoid the
    ``RuntimeError: Event loop is closed`` warning that aiosqlite would
    otherwise raise during interpreter shutdown.
    """
    import importlib

    import plugin as plugin_module

    importlib.reload(plugin_module)
    api = _StubAPI(tmp_path)
    p = plugin_module.Plugin()
    p.on_load(api)
    await p._tm.init()  # type: ignore[attr-defined]
    app = FastAPI()
    app.include_router(api.routers[0])
    try:
        with TestClient(app) as tc:
            yield tc, p
    finally:
        await p.on_unload()


# ─── Characters ──────────────────────────────────────────────────────────


async def test_post_character_returns_full_row(client) -> None:
    tc, _ = client
    r = tc.post(
        "/characters",
        json={
            "name": "Aoi",
            "role_type": "main",
            "description": "forensic linguist",
            "appearance": {"hair": "silver short"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["character_id"].startswith("char_")
    assert body["character"]["name"] == "Aoi"
    assert body["character"]["appearance"] == {"hair": "silver short"}


async def test_post_character_rejects_invalid_role(client) -> None:
    tc, _ = client
    r = tc.post("/characters", json={"name": "x", "role_type": "bogus"})
    assert r.status_code == 422  # pydantic regex


async def test_post_character_rejects_extra_keys(client) -> None:
    tc, _ = client
    r = tc.post(
        "/characters",
        json={"name": "x", "totally_unknown": "x"},
    )
    assert r.status_code == 422


async def test_post_character_rejects_empty_name(client) -> None:
    tc, _ = client
    r = tc.post("/characters", json={"name": ""})
    assert r.status_code == 422


async def test_get_character_404_for_unknown(client) -> None:
    tc, _ = client
    r = tc.get("/characters/char_nope")
    assert r.status_code == 404


async def test_list_characters_filter_by_role(client) -> None:
    tc, _ = client
    tc.post("/characters", json={"name": "A", "role_type": "main"})
    tc.post("/characters", json={"name": "B", "role_type": "support"})
    tc.post("/characters", json={"name": "N", "role_type": "narrator"})
    all_chars = tc.get("/characters").json()["characters"]
    mains = tc.get("/characters?role_type=main").json()["characters"]
    assert len(all_chars) == 3
    assert len(mains) == 1 and mains[0]["name"] == "A"


async def test_put_character_partial_update(client) -> None:
    tc, _ = client
    cid = tc.post("/characters", json={"name": "A"}).json()["character_id"]
    r = tc.put(
        f"/characters/{cid}",
        json={"description": "updated bio", "ref_images": [{"url": "x.png"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] is True
    assert body["character"]["description"] == "updated bio"
    assert body["character"]["ref_images"] == [{"url": "x.png"}]


async def test_put_character_404_for_unknown(client) -> None:
    tc, _ = client
    r = tc.put("/characters/char_nope", json={"description": "x"})
    assert r.status_code == 404


async def test_put_character_rejects_unknown_keys(client) -> None:
    tc, _ = client
    cid = tc.post("/characters", json={"name": "A"}).json()["character_id"]
    r = tc.put(f"/characters/{cid}", json={"id": "hijack"})
    assert r.status_code == 422


async def test_put_character_rejects_invalid_role(client) -> None:
    tc, _ = client
    cid = tc.post("/characters", json={"name": "A"}).json()["character_id"]
    r = tc.put(f"/characters/{cid}", json={"role_type": "bogus"})
    assert r.status_code == 422


async def test_delete_character_then_404(client) -> None:
    tc, _ = client
    cid = tc.post("/characters", json={"name": "A"}).json()["character_id"]
    assert tc.delete(f"/characters/{cid}").status_code == 200
    assert tc.delete(f"/characters/{cid}").status_code == 404
    assert tc.get(f"/characters/{cid}").status_code == 404


# ─── Series ──────────────────────────────────────────────────────────────


async def test_post_series_with_defaults(client) -> None:
    tc, _ = client
    r = tc.post(
        "/series",
        json={"title": "校园物语", "visual_style": "shoujo", "ratio": "9:16"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["series_id"].startswith("ser_")
    assert body["series"]["title"] == "校园物语"
    assert body["series"]["total_episodes"] == 0


async def test_post_series_rejects_invalid_backend(client) -> None:
    tc, _ = client
    r = tc.post("/series", json={"title": "x", "backend_pref": "bogus"})
    assert r.status_code == 422


async def test_post_series_with_default_characters(client) -> None:
    tc, _ = client
    cid = tc.post("/characters", json={"name": "A"}).json()["character_id"]
    r = tc.post(
        "/series",
        json={"title": "x", "default_characters": [cid]},
    )
    assert r.status_code == 200
    assert r.json()["series"]["default_characters"] == [cid]


async def test_list_series_orders_by_created_desc(client) -> None:
    tc, _ = client
    a = tc.post("/series", json={"title": "A"}).json()["series_id"]
    b = tc.post("/series", json={"title": "B"}).json()["series_id"]
    rows = tc.get("/series").json()["series"]
    assert [r["id"] for r in rows] == [b, a]


async def test_get_series_404(client) -> None:
    tc, _ = client
    assert tc.get("/series/ser_nope").status_code == 404


async def test_put_series_partial_update(client) -> None:
    tc, _ = client
    sid = tc.post("/series", json={"title": "A"}).json()["series_id"]
    cid = tc.post("/characters", json={"name": "C"}).json()["character_id"]
    r = tc.put(
        f"/series/{sid}",
        json={"summary": "new", "default_characters": [cid]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["series"]["summary"] == "new"
    assert body["series"]["default_characters"] == [cid]


async def test_put_series_rejects_invalid_backend(client) -> None:
    tc, _ = client
    sid = tc.post("/series", json={"title": "A"}).json()["series_id"]
    r = tc.put(f"/series/{sid}", json={"backend_pref": "bogus"})
    assert r.status_code == 422


async def test_put_series_404_for_unknown(client) -> None:
    tc, _ = client
    r = tc.put("/series/ser_nope", json={"summary": "x"})
    assert r.status_code == 404


async def test_delete_series_then_404(client) -> None:
    tc, _ = client
    sid = tc.post("/series", json={"title": "A"}).json()["series_id"]
    assert tc.delete(f"/series/{sid}").status_code == 200
    assert tc.delete(f"/series/{sid}").status_code == 404
