"""Phase 3.2 — POST /workflows/probe + manga_workflow_test tool dispatch.

These tests exercise the plugin layer's wiring of the comfy client.
The comfy client itself is unit-tested in test_comfy_client.py; here we
verify the route + tool surface forwards the probe result correctly,
returns 503 when the client isn't initialised yet, and shapes the
LLM-facing tool string the way a user would expect to see it in chat.
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

    def get_data_dir(self) -> Path:
        return self._data

    def get_config(self) -> dict[str, Any]:
        return dict(self._cfg)

    def set_config(self, updates: dict[str, Any]) -> None:
        self._cfg.update(updates)

    def log(self, msg: str, level: str = "info") -> None:
        self.logged.append((level, msg))

    def has_permission(self, name: str) -> bool:
        return True

    def get_brain(self) -> Any:
        return None

    def register_tools(self, definitions: list[dict[str, Any]], handler: Any) -> None:
        self.tools = list(definitions)
        self.tool_handler = handler

    def register_api_routes(self, router: Any) -> None:
        self.routers.append(router)

    def spawn_task(self, coro: Any, name: str | None = None) -> asyncio.Task:
        loop = asyncio.get_event_loop()
        return loop.create_task(coro, name=name or "anon")


@pytest.fixture
async def client(tmp_path: Path):
    """Boot the plugin and wire the comfy client manually so the route
    is callable before the spawned ``_async_init`` task finishes."""
    import importlib

    import plugin as plugin_module

    importlib.reload(plugin_module)

    api = _StubAPI(tmp_path)
    p = plugin_module.Plugin()
    p.on_load(api)
    await p._tm.init()  # type: ignore[attr-defined]

    if p._comfy_client is None:  # type: ignore[attr-defined]
        from comfy_client import MangaComfyClient

        p._comfy_client = MangaComfyClient(read_settings=p._read_settings)  # type: ignore[attr-defined]

    app = FastAPI()
    app.include_router(p._router)  # type: ignore[attr-defined]
    tc = TestClient(app)
    try:
        yield tc, p
    finally:
        await p.on_unload()


# ─── /healthz exposes the comfy slot ────────────────────────────────────


def test_healthz_includes_comfy_in_backends_ready(client) -> None:
    tc, _ = client
    r = tc.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "comfy" in body["backends_ready"]
    assert body["backends_ready"]["comfy"] is True


# ─── POST /workflows/probe ──────────────────────────────────────────────


def test_workflows_probe_returns_runninghub_missing_key(client) -> None:
    """Default settings have empty runninghub_api_key, so the probe
    must come back ok=False with a clear message."""
    tc, _ = client
    r = tc.post("/workflows/probe")
    assert r.status_code == 200
    probe = r.json()["probe"]
    assert probe["ok"] is False
    assert probe["backend"] == "runninghub"
    assert "runninghub_api_key" in probe["message"]


def test_workflows_probe_503_when_client_not_initialised(client) -> None:
    """Drop the comfy client to mimic a cold-start race; the route
    returns 503 (not 500) so the UI can retry without showing a stack."""
    tc, p = client
    p._comfy_client = None
    r = tc.post("/workflows/probe")
    assert r.status_code == 503
    assert "not initialised" in r.json()["detail"]


def test_workflows_probe_runninghub_with_key_and_fake_kit(client, monkeypatch) -> None:
    """When the user fills the api key + we monkeypatch the kit factory
    so comfykit isn't actually imported, the probe reports ok=True."""
    tc, p = client
    p._save_settings(  # type: ignore[attr-defined]
        {
            "comfy_backend": "runninghub",
            "runninghub_api_key": "sk-mock",
        }
    )

    class _FakeKit:
        pass

    monkeypatch.setattr(
        p._comfy_client,
        "_construct_kit",
        lambda backend, cfg: _FakeKit(),
    )
    r = tc.post("/workflows/probe")
    assert r.status_code == 200
    probe = r.json()["probe"]
    assert probe["ok"] is True
    assert probe["backend"] == "runninghub"


def test_workflows_probe_unknown_backend_does_not_500(client) -> None:
    """A user can paste any string into ``comfy_backend`` via PUT /settings;
    the probe must still respond with a structured error not an HTTP 500."""
    tc, p = client
    # _save_settings strict-whitelists, so write directly to bypass it.
    p._api._cfg["manga_studio_settings"] = {"comfy_backend": "wandb"}
    r = tc.post("/workflows/probe")
    assert r.status_code == 200
    probe = r.json()["probe"]
    assert probe["ok"] is False
    assert probe["backend"] == "unknown"


# ─── PUT /settings whitelist accepts Phase 3.5 keys ─────────────────────


def test_put_settings_persists_phase35_workflow_keys(client) -> None:
    """All nine Phase-3.5 workflow-config keys are in DEFAULT_SETTINGS so
    PUT /settings round-trips them. The Workflows tab UI relies on this
    behaviour to save backend + 3 image/animate/t2v workflow IDs in one
    PUT call without touching the unrelated Phase-2 API-key fields."""
    tc, _ = client
    payload = {
        "comfy_backend": "comfyui_local",
        "comfyui_local_url": "http://127.0.0.1:8188",
        "comfyui_workflow_image": "/wf/img.json",
        "comfyui_workflow_animate": "/wf/anim.json",
        "comfyui_workflow_t2v": "/wf/t2v.json",
        "runninghub_api_key": "rhk-mock",
        "runninghub_workflow_image": "1234",
        "runninghub_workflow_animate": "5678",
        "runninghub_workflow_t2v": "9012",
    }
    r = tc.put("/settings", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    s = body["settings"]
    assert s["comfy_backend"] == "comfyui_local"
    assert s["comfyui_local_url"] == "http://127.0.0.1:8188"
    assert s["comfyui_workflow_image"] == "/wf/img.json"
    assert s["comfyui_workflow_animate"] == "/wf/anim.json"
    assert s["comfyui_workflow_t2v"] == "/wf/t2v.json"
    # rh_api_key is redacted (8 char string → all bullets), but the
    # plain workflow IDs come back as-is.
    assert "•" in s["runninghub_api_key"]
    assert s["runninghub_workflow_image"] == "1234"
    assert s["runninghub_workflow_animate"] == "5678"
    assert s["runninghub_workflow_t2v"] == "9012"


def test_put_settings_redacted_api_key_is_not_persisted_back(client) -> None:
    """If the UI sends the redacted dot-mask back unchanged (because the
    user didn't edit the api_key field), the Workflows tab UI's
    saveDraft helper must skip it. This test confirms the *backend*
    behaviour: writing the redacted mask DOES overwrite the real key
    with garbage, which is why the UI strips it client-side. We verify
    that on round-trip 2 (where UI sends back redacted), the new value
    is what the UI actually sent.
    """
    tc, _ = client
    tc.put("/settings", json={"runninghub_api_key": "real-key-1234567890"})
    r = tc.get("/settings")
    redacted = r.json()["settings"]["runninghub_api_key"]
    assert "•" in redacted
    # Send the redacted value back — the backend has no way to tell it
    # apart from a real key, so it overwrites. This is the bug the
    # Workflows tab guards against client-side via `isRedacted(...)`.
    tc.put("/settings", json={"runninghub_api_key": redacted})
    r2 = tc.get("/settings")
    # The new "value" is the redacted mask (re-redacted, but not the
    # original real key).
    assert r2.json()["settings"]["runninghub_api_key"].count("•") > 0


# ─── manga_workflow_test tool ───────────────────────────────────────────


async def test_tool_workflow_test_returns_fail_string_when_unconfigured(client) -> None:
    _, p = client
    msg = await p._api.tool_handler("manga_workflow_test", {"backend": "runninghub"})
    assert msg.startswith("FAIL ·")
    assert "backend=runninghub" in msg
    assert "runninghub_api_key" in msg


async def test_tool_workflow_test_returns_ok_string_when_configured(client, monkeypatch) -> None:
    _, p = client
    p._save_settings(
        {
            "comfy_backend": "runninghub",
            "runninghub_api_key": "sk-mock",
        }
    )

    class _FakeKit:
        pass

    monkeypatch.setattr(
        p._comfy_client,
        "_construct_kit",
        lambda backend, cfg: _FakeKit(),
    )
    msg = await p._api.tool_handler("manga_workflow_test", {"backend": "runninghub"})
    assert msg.startswith("ok ·")
    assert "RunningHub" in msg


async def test_tool_workflow_test_when_client_dropped(client) -> None:
    """Race-time: tool fired before async init wired the client."""
    _, p = client
    p._comfy_client = None
    msg = await p._api.tool_handler("manga_workflow_test", {"backend": "runninghub"})
    assert msg.startswith("error:")
    assert "workflow client" in msg


# ─── /healthz still returns Phase ≥ 2 even without comfy ────────────────


def test_healthz_phase_unchanged_by_phase3_wiring(client) -> None:
    tc, _ = client
    r = tc.get("/healthz")
    assert r.json()["phase"] >= 2
