"""Phase 4.6 — end-to-end pipeline smoke test (HTTP layer + real pipeline).

This is the broadest test we ship: it exercises the actual route → DB
→ pipeline → DB writeback chain, with only the *external* boundaries
(Wanxiang, Ark, TTS, FFmpeg, ScriptWriter, HTTP downloads) replaced by
fakes. We do NOT monkeypatch ``_run_pipeline`` like the unit-level
route tests do — the real orchestrator runs and we assert that:

1. ``POST /characters`` persists a character row.
2. ``POST /series`` persists a series row carrying that character.
3. ``POST /episodes`` (referencing the series + bound chars) returns
   200 with episode_id + task_id under cost threshold.
4. The spawned background task transitions through the 8 pipeline
   steps and reaches ``status=succeeded`` with progress=100.
5. The episode row gets ``final_video_path`` populated and
   ``GET /episodes/{ep_id}`` returns it.
6. The on-disk artefact tree under ``data_dir/episodes/<ep_id>/``
   contains the muxed final video.

We share the ``_StubAPI`` and the fake-client classes from the unit
suite — they're plain Python objects with no fixtures, so importing
them is safe.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI


class _StubAPI:
    """Minimal PluginAPI surface enough for ``Plugin.on_load`` to run.

    Same shape as the one used by ``test_routes_phase2.py``; copied
    here rather than imported because the tests/ directory isn't on
    ``sys.path`` (only the plugin dir).
    """

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


# ─── Fakes ─────────────────────────────────────────────────────────────────
#
# These mirror the real client APIs *exactly enough* for the pipeline
# to walk through every step. Each one lets the test inspect the calls
# afterwards (counters / arg lists). Same shape as the fakes inside
# ``test_manga_pipeline.py`` — we don't import them because pytest
# doesn't share modules across the tests/ dir, and this file pinning
# its own copies keeps the contract obvious in one place.


class _FakeWanxiang:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, Any]] = []
        self.poll_calls: list[str] = []

    async def submit_image(self, **kwargs: Any) -> str:
        i = len(self.submit_calls)
        self.submit_calls.append(kwargs)
        return f"img_task_{i}"

    async def poll_until_done(self, task_id: str, **_: Any) -> dict[str, Any]:
        self.poll_calls.append(task_id)
        return {
            "task_id": task_id,
            "status": "SUCCEEDED",
            "is_done": True,
            "is_ok": True,
            "output_url": f"https://oss/{task_id}.png",
            "output_kind": "image",
        }


class _FakeArk:
    def __init__(self) -> None:
        self.i2v_calls: list[dict[str, Any]] = []
        self.t2v_calls: list[dict[str, Any]] = []
        self.poll_calls: list[str] = []

    async def submit_seedance_i2v(self, **kwargs: Any) -> dict[str, Any]:
        i = len(self.i2v_calls)
        self.i2v_calls.append(kwargs)
        return {"id": f"i2v_{i}"}

    async def submit_seedance_t2v(self, **kwargs: Any) -> dict[str, Any]:
        i = len(self.t2v_calls)
        self.t2v_calls.append(kwargs)
        return {"id": f"t2v_{i}"}

    async def poll_until_done(self, task_id: str, **_: Any) -> dict[str, Any]:
        self.poll_calls.append(task_id)
        return {
            "id": task_id,
            "status": "succeeded",
            "content": {"video_url": f"https://oss/{task_id}.mp4"},
        }

    @staticmethod
    def extract_video_url(response: dict[str, Any]) -> str | None:
        return response.get("content", {}).get("video_url")


class _FakeTTS:
    def __init__(self) -> None:
        self.synth_calls: list[dict[str, Any]] = []

    async def synth(self, **kwargs: Any) -> dict[str, Any]:
        self.synth_calls.append(kwargs)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKEAUDIO")
        return {
            "bytes": b"FAKEAUDIO",
            "duration_sec": 3.0,
            "engine": "edge",
            "voice_id": kwargs["voice_id"],
            "path": str(out),
        }


class _FakeFFmpeg:
    def __init__(self) -> None:
        self.attach_calls: list[Path] = []
        self.concat_calls: list[Path] = []
        self.burn_calls: list[Path] = []

    async def attach_audio(
        self, video_path: Any, audio_path: Any, output_path: Any, **_: Any
    ) -> Path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"MUXED")
        self.attach_calls.append(out)
        return out

    async def concat(self, video_paths: list[Any], output_path: Any, **_: Any) -> Path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"CONCAT")
        self.concat_calls.append(out)
        return out

    async def burn_subtitles(
        self, video_path: Any, subtitles: list[Any], output_path: Any, **_: Any
    ) -> Path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"BURNED")
        self.burn_calls.append(out)
        return out

    async def mix_bgm(self, video_path: Any, bgm_path: Any, output_path: Any, **_: Any) -> Path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"BGM")
        return out


class _FakeWriter:
    """Always returns a deterministic 2-panel storyboard.

    The real ``MangaScriptWriter`` calls into Brain (LLM) and falls
    back to a hand-rolled storyboard when Brain is unavailable. We
    skip that whole branch to keep the E2E run deterministic.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def write_storyboard(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        from script_writer import BrainResult  # noqa: PLC0415

        return BrainResult(
            ok=True,
            data={
                "episode_title": "E2E Test Episode",
                "summary": "Automated E2E walk-through",
                # NB: ``image_url`` is pre-populated even though the
                # pipeline regenerates the actual image bytes via
                # ``_gen_panel_image``. The current pipeline design
                # uses storyboard-level URLs as the I2V source — see
                # the comment in ``manga_pipeline._gen_panel_video_i2v``
                # ("upload to OSS before I2V"). We mirror what the
                # unit-level happy-path test does so this E2E walks
                # through the same code path production would.
                "panels": [
                    {
                        "idx": 0,
                        "narration": "panel 0 narration",
                        "dialogue": [{"character": "李雷", "line": "测试一下"}],
                        "characters_in_scene": ["李雷"],
                        "camera": "中景",
                        "action": "走入",
                        "mood": "calm",
                        "background": "教室",
                        "image_url": "https://oss/p0.png",
                    },
                    {
                        "idx": 1,
                        "narration": "panel 1 narration",
                        "dialogue": [],
                        "characters_in_scene": ["李雷"],
                        "camera": "特写",
                        "action": "举手",
                        "mood": "focus",
                        "background": "黑板",
                        "image_url": "https://oss/p1.png",
                    },
                ],
            },
            used_brain=False,
        )


# ─── Fixture: full plugin behind a TestClient with fakes wired ────────────


@pytest.fixture
async def app_client(tmp_path: Path, monkeypatch):
    """Return ``(TestClient, plugin, fakes)`` with the real pipeline
    constructed and all external boundaries replaced by fakes.

    The plugin's ``on_load`` is allowed to run as written, then we
    surgically swap its private collaborators *after* ``_async_init``
    finished. This way the routes wire to the same code path
    production runs use.
    """

    import importlib

    import plugin as plugin_module

    importlib.reload(plugin_module)

    from manga_pipeline import MangaPipeline  # noqa: PLC0415

    api = _StubAPI(tmp_path)
    p = plugin_module.Plugin()
    p.on_load(api)
    # ``on_load`` fires off ``_async_init`` as a spawn_task — that task
    # would race against our manual setup and overwrite the fakes
    # below with real Ark / DashScope / TTS clients (which would then
    # try to call the real APIs and the test fails with auth errors
    # or a CancelledError when the fixture tears down). We cancel the
    # background init upfront and do the same setup work synchronously
    # below.
    for spawned in list(api.spawned):
        if not spawned.done():
            spawned.cancel()
            try:
                await spawned
            except (asyncio.CancelledError, Exception):
                pass
    await p._tm.init()  # type: ignore[attr-defined]

    # Replace external boundaries before the route fires.
    fakes = {
        "wanxiang": _FakeWanxiang(),
        "ark": _FakeArk(),
        "tts": _FakeTTS(),
        "ffmpeg": _FakeFFmpeg(),
        "writer": _FakeWriter(),
    }
    p._direct_wan = fakes["wanxiang"]  # type: ignore[attr-defined]
    p._direct_ark = fakes["ark"]  # type: ignore[attr-defined]
    p._tts = fakes["tts"]  # type: ignore[attr-defined]
    p._ffmpeg = fakes["ffmpeg"]  # type: ignore[attr-defined]
    p._writer = fakes["writer"]  # type: ignore[attr-defined]

    # The pipeline downloads vendor URLs (image / video) into the
    # working dir. Real production fetches over HTTP; we shortcut the
    # bytes so the test doesn't need a real network.
    async def fake_download_to(self: Any, url: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"FAKEBIN_" + url.encode("utf-8")[-32:])

    monkeypatch.setattr(MangaPipeline, "_download_to", fake_download_to)

    # Rebuild the pipeline so it picks up the swapped clients.
    p._pipeline = MangaPipeline(  # type: ignore[attr-defined]
        wanxiang_client=fakes["wanxiang"],
        ark_client=fakes["ark"],
        tts_client=fakes["tts"],
        ffmpeg=fakes["ffmpeg"],
        script_writer=fakes["writer"],
        task_manager=p._tm,  # type: ignore[attr-defined]
        working_dir=tmp_path / "episodes",
    )

    # Use httpx.AsyncClient against an ASGI transport so request
    # handling and spawn_task background coroutines share the same
    # event loop as the test body. (Starlette's TestClient drives the
    # ASGI app from a separate portal thread — spawn_task tasks
    # scheduled inside route handlers were getting cancelled the
    # moment the portal returned, which is why we don't use it here.)
    app = FastAPI()
    app.include_router(p._router)  # type: ignore[attr-defined]
    transport = httpx.ASGITransport(app=app)
    tc = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        yield tc, p, fakes
    finally:
        await tc.aclose()
        for _tid, bg in list(p._poll_tasks.items()):  # type: ignore[attr-defined]
            if not bg.done():
                bg.cancel()
                try:
                    await bg
                except (asyncio.CancelledError, Exception):
                    pass
        await p.on_unload()


# ─── Migrate test bodies from sync TestClient → async httpx ───────────────
#
# All ``tc.post / get / delete`` calls below need ``await`` since we
# switched to ``httpx.AsyncClient``. Done inline in the tests.


async def _wait_task(p: Any, task_id: str, timeout_sec: float = 10.0) -> dict[str, Any]:
    """Spin until the in-process pipeline task either reports a
    terminal status (succeeded / failed / cancelled) or we time out.

    We poll the task manager rather than the registered ``asyncio``
    task because ``MangaPipeline._run`` finalises the row *before*
    the coroutine object resolves, and polling the row is what the UI
    does in production anyway.
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while True:
        row = await p._tm.get_task(task_id)
        if row and row.get("status") in {"succeeded", "failed", "cancelled"}:
            return row
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"task {task_id} did not finish in {timeout_sec}s; row={row}")
        await asyncio.sleep(0.05)


# ─── The actual E2E walk-through ───────────────────────────────────────────


async def test_e2e_walkthrough_character_series_episode_pipeline(app_client) -> None:
    tc, p, fakes = app_client

    # 1. Create a character.
    cr = await tc.post(
        "/characters",
        json={
            "name": "李雷",
            "role_type": "main",
            "gender": "male",
            "appearance": {"description": "黑色短发，校服"},
            "default_voice_id": "zh-CN-YunjianNeural",
            "ref_images": [{"url": "https://example.com/lilei.png"}],
        },
    )
    assert cr.status_code == 200, cr.text
    char_id = cr.json()["character_id"]
    assert char_id.startswith("char_")

    # 2. Create a series binding that character.
    sr = await tc.post(
        "/series",
        json={
            "title": "测试漫剧",
            "summary": "E2E test series",
            "visual_style": "shonen",
            "ratio": "9:16",
            "backend_pref": "direct",
            "default_characters": [char_id],
        },
    )
    assert sr.status_code == 200, sr.text
    series_id = sr.json()["series_id"]
    assert series_id.startswith("ser_")

    # 3. Fire off an episode under that series — small layout to
    #    stay well below the cost threshold.
    er = await tc.post(
        "/episodes",
        json={
            "story": "李雷在剑道部觉醒了上古剑灵的力量，第一次面对挑战",
            "series_id": series_id,
            "n_panels": 2,
            "seconds_per_panel": 3,
            "visual_style": "shonen",
            "ratio": "9:16",
            "burn_subtitles": False,  # keep ffmpeg call tree shallower
            "backend": "direct",
            "bound_character_ids": [char_id],
        },
    )
    assert er.status_code == 200, er.text
    body = er.json()
    assert body["ok"] is True
    episode_id = body["episode_id"]
    task_id = body["task_id"]
    assert episode_id.startswith("ep_")
    assert task_id.startswith("task_")
    assert body["cost_preview"]["formatted_total"].startswith("¥")

    # 4. Wait for the in-process pipeline task to finish.
    row = await _wait_task(p, task_id, timeout_sec=10.0)
    assert row["status"] == "succeeded", f"task did not succeed: {row}"
    assert row["progress"] == 100
    assert row.get("error_kind") in (None, "")

    # 5. Episode row should now carry the muxed final-video path.
    ep_row = await p._tm.get_episode(episode_id)
    assert ep_row is not None
    assert ep_row.get("final_video_path"), f"final_video_path empty: {ep_row}"
    assert Path(ep_row["final_video_path"]).exists()

    # 6. GET /episodes/{ep_id} surfaces the same row through the API.
    gr = await tc.get(f"/episodes/{episode_id}")
    assert gr.status_code == 200
    payload = gr.json()
    assert payload["ok"] is True
    assert payload["episode"]["id"] == episode_id
    assert payload["episode"]["final_video_path"] == ep_row["final_video_path"]

    # 7. Each fake collaborator was called the expected number of
    #    times — proves the pipeline actually walked through every
    #    step rather than short-circuiting with a placeholder.
    assert len(fakes["writer"].calls) == 1, "script writer should be called once"
    assert len(fakes["wanxiang"].submit_calls) == 2, "image gen — one per panel"
    assert len(fakes["ark"].i2v_calls) == 2, "i2v — one per panel"
    assert len(fakes["tts"].synth_calls) == 2, "tts — one per panel"
    assert len(fakes["ffmpeg"].attach_calls) == 2, "attach_audio — one per panel"
    assert len(fakes["ffmpeg"].concat_calls) == 1, "concat — one for the episode"

    # 8. GET /tasks/{id} reflects the same terminal state.
    tr = await tc.get(f"/tasks/{task_id}")
    assert tr.status_code == 200
    tjson = tr.json()
    assert tjson["task"]["status"] == "succeeded"
    assert tjson["task"]["progress"] == 100


async def test_e2e_episode_listed_under_series(app_client) -> None:
    """Smaller follow-up: after the pipeline finishes, the episode
    should be reachable via ``GET /episodes?series_id=…`` so the UI's
    Series detail tab can list it. This guards against a regression
    where the route used to filter on a non-existent column."""
    tc, p, fakes = app_client

    sr = await tc.post(
        "/series",
        json={
            "title": "Listing test",
            "visual_style": "shonen",
            "ratio": "9:16",
            "backend_pref": "direct",
        },
    )
    series_id = sr.json()["series_id"]

    er = await tc.post(
        "/episodes",
        json={
            "story": "短故事",
            "series_id": series_id,
            "n_panels": 2,
            "seconds_per_panel": 3,
            "burn_subtitles": False,
            "backend": "direct",
        },
    )
    assert er.status_code == 200
    task_id = er.json()["task_id"]
    episode_id = er.json()["episode_id"]
    await _wait_task(p, task_id, timeout_sec=10.0)

    lr = await tc.get(f"/episodes?series_id={series_id}")
    assert lr.status_code == 200
    eps = lr.json().get("episodes", [])
    assert any(e["id"] == episode_id for e in eps), (
        f"episode {episode_id} not in listing for series {series_id}: {eps}"
    )
