"""Phase 2.8 — tests for catalog, cost-preview, POST /episodes, /tasks routes,
plus tool dispatch.

Note on the test shape: every test is ``async def`` (pytest-asyncio in
``mode=auto``) so the spawned background task that ``POST /episodes``
kicks off can actually run between calls. Sync ``time.sleep`` would
deadlock the loop because ``TestClient`` runs the FastAPI handlers on a
worker thread but the spawned tasks are scheduled on the main loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _StubAPI:
    def __init__(self, data_dir: Path) -> None:
        self._data = data_dir
        self._cfg: dict[str, Any] = {}
        self.logged: list[tuple[str, str]] = []
        self.tools: list[dict[str, Any]] = []
        self.tool_handler: Any = None
        self.routers: list[Any] = []
        self.spawned: list[asyncio.Task[Any]] = []
        self._brain = None

    def get_data_dir(self) -> Path:
        return self._data

    def get_config(self) -> dict[str, Any]:
        return dict(self._cfg)

    def set_config(self, updates: dict[str, Any]) -> None:
        self._cfg.update(updates)

    def log(self, msg: str, level: str = "info") -> None:
        self.logged.append((level, msg))

    def has_permission(self, name: str) -> bool:
        return name in {"data.own", "config.read", "config.write", "brain.access"}

    def get_brain(self) -> Any:
        return self._brain

    def register_tools(self, definitions: list[dict[str, Any]], handler: Any) -> None:
        self.tools = list(definitions)
        self.tool_handler = handler

    def register_api_routes(self, router: Any) -> None:
        self.routers.append(router)

    def spawn_task(self, coro: Any, name: str | None = None) -> asyncio.Task:
        loop = asyncio.get_event_loop()
        task = loop.create_task(coro, name=name or "anon")
        self.spawned.append(task)
        return task


@pytest.fixture
async def client(tmp_path: Path):
    import importlib

    import plugin as plugin_module

    importlib.reload(plugin_module)

    api = _StubAPI(tmp_path)
    p = plugin_module.Plugin()
    p.on_load(api)
    await p._tm.init()  # type: ignore[attr-defined]

    if p._pipeline is None:  # type: ignore[attr-defined]
        from direct_ark_client import MangaArkClient
        from direct_wanxiang_client import MangaWanxiangClient
        from ffmpeg_service import FFmpegService
        from manga_pipeline import MangaPipeline
        from script_writer import MangaScriptWriter
        from tts_client import MangaTTSClient

        p._direct_ark = MangaArkClient(read_settings=p._read_settings)  # type: ignore[attr-defined]
        p._direct_wan = MangaWanxiangClient(read_settings=p._read_settings)  # type: ignore[attr-defined]
        p._tts = MangaTTSClient(read_settings=p._read_settings)  # type: ignore[attr-defined]
        p._ffmpeg = FFmpegService()  # type: ignore[attr-defined]
        p._writer = MangaScriptWriter(api)  # type: ignore[attr-defined]
        p._pipeline = MangaPipeline(  # type: ignore[attr-defined]
            wanxiang_client=p._direct_wan,
            ark_client=p._direct_ark,
            tts_client=p._tts,
            ffmpeg=p._ffmpeg,
            script_writer=p._writer,
            task_manager=p._tm,
            working_dir=tmp_path / "episodes",
        )

    app = FastAPI()
    app.include_router(p._router)  # type: ignore[attr-defined]
    tc = TestClient(app)
    try:
        yield tc, p
    finally:
        for _tid, bg in list(p._poll_tasks.items()):  # type: ignore[attr-defined]
            if not bg.done():
                bg.cancel()
                try:
                    await bg
                except (asyncio.CancelledError, Exception):
                    pass
        await p.on_unload()


# ─── GET /catalog ─────────────────────────────────────────────────────────


async def test_get_catalog_returns_visual_styles_and_voices(client) -> None:
    tc, _ = client
    r = tc.get("/catalog")
    assert r.status_code == 200
    body = r.json()
    cat = body["catalog"]
    assert isinstance(cat["visual_styles"], list) and len(cat["visual_styles"]) >= 5
    assert all("id" in s and "label_zh" in s for s in cat["visual_styles"])
    assert "9:16" in cat["ratios"]
    assert isinstance(cat["voices"], list) and len(cat["voices"]) >= 4
    assert cat["cost_threshold"] >= 0


# ─── POST /cost-preview ───────────────────────────────────────────────────


async def test_post_cost_preview_returns_breakdown(client) -> None:
    tc, _ = client
    r = tc.post(
        "/cost-preview",
        json={
            "n_panels": 4,
            "total_duration_sec": 20,
            "story_chars": 200,
        },
    )
    assert r.status_code == 200
    cost = r.json()["cost_preview"]
    assert cost["currency"] == "CNY"
    assert isinstance(cost["items"], list)
    assert cost["formatted_total"].startswith("¥")
    assert "exceeds_threshold" in cost


async def test_post_cost_preview_rejects_invalid_video_model(client) -> None:
    tc, _ = client
    r = tc.post(
        "/cost-preview",
        json={
            "n_panels": 4,
            "total_duration_sec": 20,
            "story_chars": 200,
            "video_model": "not-a-real-model",
        },
    )
    assert r.status_code == 400


async def test_post_cost_preview_rejects_negative_n_panels(client) -> None:
    tc, _ = client
    r = tc.post("/cost-preview", json={"n_panels": 0, "total_duration_sec": 5})
    assert r.status_code == 422


# ─── POST /episodes ───────────────────────────────────────────────────────


async def test_post_episode_blocks_when_cost_exceeds_threshold(client) -> None:
    """With default ¥5 threshold, a long-duration episode trips the
    cost gate and gets back 402 with the cost preview."""
    tc, _ = client
    r = tc.post(
        "/episodes",
        json={
            "story": "x" * 200,
            "n_panels": 12,
            "seconds_per_panel": 10,
        },
    )
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["reason"] == "cost_over_threshold"
    assert "cost_preview" in detail
    assert "exceeds the" in detail["hint_en"]


async def test_post_episode_creates_row_and_task_when_under_threshold(client, monkeypatch) -> None:
    """Below the threshold the route returns 200 with episode_id + task_id.
    We monkeypatch ``_run_pipeline`` to a no-op so the test isn't hostage
    to vendor APIs."""
    tc, p = client

    async def fake_run(*, episode_id: str, task_id: str, config: Any) -> None:
        await p._tm.update_task_safe(task_id, status="succeeded", progress=100)

    monkeypatch.setattr(p, "_run_pipeline", fake_run)

    r = tc.post(
        "/episodes",
        json={
            "story": "李雷的小故事",
            "n_panels": 2,
            "seconds_per_panel": 3,
            "burn_subtitles": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["episode_id"].startswith("ep_")
    assert body["task_id"].startswith("task_")
    assert body["cost_preview"]["formatted_total"].startswith("¥")


async def test_post_episode_rejects_unknown_visual_style(client) -> None:
    tc, _ = client
    r = tc.post(
        "/episodes",
        json={"story": "x", "n_panels": 1, "visual_style": "not-real"},
    )
    assert r.status_code == 400
    assert "unknown visual_style" in r.json()["detail"]


async def test_post_episode_rejects_invalid_ratio(client) -> None:
    tc, _ = client
    r = tc.post(
        "/episodes",
        json={"story": "x", "n_panels": 1, "ratio": "21:9"},
    )
    assert r.status_code == 422


async def test_post_episode_with_confirm_proceeds_above_threshold(client, monkeypatch) -> None:
    """Setting ``confirm_over_threshold=true`` bypasses the 402 gate."""
    tc, p = client

    async def fake_run(*, episode_id: str, task_id: str, config: Any) -> None:
        await p._tm.update_task_safe(task_id, status="succeeded", progress=100)

    monkeypatch.setattr(p, "_run_pipeline", fake_run)

    r = tc.post(
        "/episodes",
        json={
            "story": "x" * 100,
            "n_panels": 12,
            "seconds_per_panel": 10,
            "confirm_over_threshold": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["cost_preview"]["exceeds_threshold"] is True


# ─── GET /tasks ───────────────────────────────────────────────────────────


async def test_get_task_404_when_missing(client) -> None:
    tc, _ = client
    r = tc.get("/tasks/task_does_not_exist")
    assert r.status_code == 404


async def test_get_tasks_empty_list_initially(client) -> None:
    tc, _ = client
    r = tc.get("/tasks")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "tasks": []}


async def test_post_episode_then_get_task(client, monkeypatch) -> None:
    tc, p = client

    async def fake_run(*, episode_id: str, task_id: str, config: Any) -> None:
        await p._tm.update_task_safe(task_id, status="succeeded", progress=100)

    monkeypatch.setattr(p, "_run_pipeline", fake_run)

    r = tc.post(
        "/episodes",
        json={"story": "x", "n_panels": 1, "seconds_per_panel": 3},
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    # ``_spawn_pipeline_task`` writes the task row BEFORE spawning the
    # coroutine, so this GET is race-free regardless of whether the
    # background task has run yet.
    r2 = tc.get(f"/tasks/{task_id}")
    assert r2.status_code == 200
    assert r2.json()["task"]["id"] == task_id


async def test_post_cancel_task_404(client) -> None:
    tc, _ = client
    r = tc.post("/tasks/task_x/cancel")
    assert r.status_code == 404


# ─── DELETE /episodes/{id} ────────────────────────────────────────────────


async def test_delete_episode_removes_row(client, monkeypatch) -> None:
    tc, p = client

    async def fake_run(*, episode_id: str, task_id: str, config: Any) -> None:
        await p._tm.update_task_safe(task_id, status="succeeded", progress=100)

    monkeypatch.setattr(p, "_run_pipeline", fake_run)

    r = tc.post(
        "/episodes",
        json={"story": "x", "n_panels": 1, "seconds_per_panel": 3},
    )
    ep_id = r.json()["episode_id"]

    r2 = tc.delete(f"/episodes/{ep_id}")
    assert r2.status_code == 200
    r3 = tc.get(f"/episodes/{ep_id}")
    assert r3.status_code == 404


async def test_delete_episode_also_removes_disk_artefacts(client, monkeypatch) -> None:
    """P1-4 regression: deleting an episode used to leave its
    ``data/episodes/<ep_id>/`` directory orphaned forever — which
    blocked the storage panel from ever shrinking and made delete
    feel half-broken. The route now reclaims the directory and
    reports the freed bytes."""
    tc, p = client

    async def fake_run(*, episode_id: str, task_id: str, config: Any) -> None:
        await p._tm.update_task_safe(task_id, status="succeeded", progress=100)

    monkeypatch.setattr(p, "_run_pipeline", fake_run)

    r = tc.post(
        "/episodes",
        json={"story": "x", "n_panels": 1, "seconds_per_panel": 3},
    )
    ep_id = r.json()["episode_id"]

    ep_dir = p._data_dir / "episodes" / ep_id
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "panels").mkdir(exist_ok=True)
    (ep_dir / "final.mp4").write_bytes(b"x" * 1024)
    (ep_dir / "panels" / "panel_000.png").write_bytes(b"y" * 256)
    assert ep_dir.exists()

    r2 = tc.delete(f"/episodes/{ep_id}")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["ok"] is True
    assert body["removed_bytes"] == 1024 + 256
    assert "cleanup_warning" not in body
    assert not ep_dir.exists()


# ─── Tool dispatch ────────────────────────────────────────────────────────


async def test_tool_create_character_actually_persists(client) -> None:
    _, p = client
    msg = await p._api.tool_handler(
        "manga_create_character",
        {"name": "李雷", "role_type": "main", "description": "勇敢的剑道部主角"},
    )
    assert "created character char_" in msg
    chars = await p._tm.list_characters()
    assert any(c["name"] == "李雷" for c in chars)


async def test_tool_create_series_persists(client) -> None:
    _, p = client
    msg = await p._api.tool_handler(
        "manga_create_series",
        {"title": "Test Series", "summary": "x"},
    )
    assert "created series ser_" in msg
    rows = await p._tm.list_series()
    assert any(r["title"] == "Test Series" for r in rows)


async def test_tool_list_characters_lists_recent(client) -> None:
    _, p = client
    await p._tm.create_character(name="A")
    await p._tm.create_character(name="B")
    msg = await p._api.tool_handler("manga_list_characters", {})
    assert "A" in msg
    assert "B" in msg


async def test_tool_cost_preview_returns_estimated_total(client) -> None:
    _, p = client
    msg = await p._api.tool_handler(
        "manga_cost_preview",
        {"n_panels": 3, "total_duration": 9, "story_chars": 100},
    )
    assert msg.startswith("estimated cost: ¥")


async def test_tool_split_script_runs_with_brain_unavailable(client) -> None:
    """No brain configured → script writer falls back to the
    deterministic split. The tool surfaces this in the result string."""
    _, p = client
    msg = await p._api.tool_handler(
        "manga_split_script",
        {"story": "李雷上学。师傅在等他。剑道开始。", "n_panels": 3},
    )
    assert "deterministic" in msg or "brain" in msg


async def test_tool_quick_drama_kicks_off_pipeline(client, monkeypatch) -> None:
    _, p = client

    async def fake_run(*, episode_id: str, task_id: str, config: Any) -> None:
        await p._tm.update_task_safe(task_id, status="succeeded", progress=100)

    monkeypatch.setattr(p, "_run_pipeline", fake_run)

    msg = await p._api.tool_handler(
        "manga_quick_drama",
        {"story": "x", "n_panels": 1, "total_duration": 3},
    )
    assert "queued" in msg
    assert "task task_" in msg


async def test_tool_handler_catches_unexpected_exception(client, monkeypatch) -> None:
    """If a handler raises, the dispatcher returns ``error: ...`` instead
    of crashing into the LLM's ReAct loop."""
    _, p = client

    async def boom(self: Any, args: dict[str, Any]) -> str:
        raise RuntimeError("simulated")

    p._TOOL_HANDLERS = dict(p._TOOL_HANDLERS)
    p._TOOL_HANDLERS["manga_cost_preview"] = boom
    msg = await p._api.tool_handler("manga_cost_preview", {})
    assert msg.startswith("error: tool manga_cost_preview raised")
    assert "simulated" in msg
