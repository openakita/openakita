"""Phase 1 — episodes (read-only) + upload preview + storage stats routes."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _StubAPI:
    def __init__(self, data_dir: Path) -> None:
        self._data = data_dir
        self._cfg: dict[str, Any] = {}
        self.routers: list[Any] = []

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


# ─── Episodes (read-only) ───────────────────────────────────────────────


async def test_list_episodes_empty(client) -> None:
    tc, _ = client
    r = tc.get("/episodes")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "episodes": []}


async def test_list_episodes_filter_by_series(client) -> None:
    tc, p = client
    sid = await p._tm.create_series(title="S")  # type: ignore[attr-defined]
    e1 = await p._tm.create_episode(series_id=sid, episode_no=1)
    e2 = await p._tm.create_episode(series_id=sid, episode_no=2)
    other = await p._tm.create_episode(story="standalone")
    in_series = tc.get(f"/episodes?series_id={sid}").json()["episodes"]
    assert [r["id"] for r in in_series] == [e1, e2]  # episode_no ASC
    everything = tc.get("/episodes").json()["episodes"]
    assert {r["id"] for r in everything} == {e1, e2, other}


async def test_get_episode_404(client) -> None:
    tc, _ = client
    assert tc.get("/episodes/ep_nope").status_code == 404


async def test_get_episode_returns_full_row(client) -> None:
    tc, p = client
    ep = await p._tm.create_episode(story="x", title="Pilot")  # type: ignore[attr-defined]
    body = tc.get(f"/episodes/{ep}").json()
    assert body["episode"]["title"] == "Pilot"
    assert body["episode"]["story"] == "x"


# ─── Upload + preview ──────────────────────────────────────────────────


async def test_post_upload_saves_file_and_returns_preview_url(client, tmp_path: Path) -> None:
    tc, p = client
    fake_png = (
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    )  # not a real PNG but enough for a write+read roundtrip
    r = tc.post(
        "/upload",
        files={"file": ("ref.png", io.BytesIO(fake_png), "image/png")},
        params={"kind": "character_ref"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["kind"] == "character_ref"
    assert body["filename"].endswith(".png")
    assert body["rel_path"].startswith("character_ref/")
    assert body["preview_url"].startswith("/api/plugins/manga-studio/uploads/character_ref/")
    assert body["oss_url"] is None  # Phase 2 fills this in
    # File actually exists on disk in the right location.
    saved = p._data_dir / "uploads" / body["rel_path"]  # type: ignore[attr-defined]
    assert saved.exists()
    assert saved.read_bytes() == fake_png


async def test_post_upload_rejects_unknown_kind(client) -> None:
    tc, _ = client
    r = tc.post(
        "/upload",
        files={"file": ("ref.png", io.BytesIO(b"x"), "image/png")},
        params={"kind": "bogus"},
    )
    assert r.status_code == 400
    assert "unknown kind" in r.json()["detail"]


async def test_post_upload_rejects_empty_file(client) -> None:
    tc, _ = client
    r = tc.post(
        "/upload",
        files={"file": ("ref.png", io.BytesIO(b""), "image/png")},
        params={"kind": "character_ref"},
    )
    assert r.status_code == 400


async def test_get_uploaded_file_via_preview_route(client) -> None:
    tc, _ = client
    payload = b"\x89PNG\r\n\x1a\nHELLO"
    r = tc.post(
        "/upload",
        files={"file": ("a.png", io.BytesIO(payload), "image/png")},
        params={"kind": "character_ref"},
    )
    rel = r.json()["rel_path"]
    g = tc.get(f"/uploads/{rel}")
    assert g.status_code == 200
    assert g.content == payload


async def test_uploads_route_blocks_path_traversal(client) -> None:
    tc, _ = client
    # Anything outside base_dir should be 403/404 (helper returns 403).
    r = tc.get("/uploads/../../../etc/passwd")
    assert r.status_code in {403, 404}


async def test_uploads_route_404_for_unknown_extension(client, tmp_path: Path) -> None:
    """add_upload_preview_route's allowed-extensions filter returns 404
    (not 403) so we don't leak existence info for arbitrary files."""
    tc, p = client
    base = p._data_dir / "uploads" / "character_ref"  # type: ignore[attr-defined]
    base.mkdir(parents=True, exist_ok=True)
    bad = base / "evil.exe"
    bad.write_bytes(b"x")
    r = tc.get("/uploads/character_ref/evil.exe")
    assert r.status_code == 404


# ─── Storage stats ─────────────────────────────────────────────────────


async def test_storage_stats_empty_data_dir(client) -> None:
    tc, p = client
    body = tc.get("/storage").json()
    assert body["ok"] is True
    assert body["data_dir"] == str(p._data_dir)  # type: ignore[attr-defined]
    s = body["stats"]
    # Some files may have been created by SQLite (manga.db, manga.db-wal, ...)
    # so we just check the shape, not exact zeros.
    assert "total_files" in s
    assert "total_bytes" in s
    assert "by_extension" in s


async def test_storage_stats_after_upload_counts_file(client) -> None:
    tc, _ = client
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 1024
    tc.post(
        "/upload",
        files={"file": ("a.png", io.BytesIO(payload), "image/png")},
        params={"kind": "character_ref"},
    )
    body = tc.get("/storage").json()["stats"]
    assert body["total_files"] >= 1
    assert body["total_bytes"] >= len(payload)
    assert "png" in body["by_extension"]
