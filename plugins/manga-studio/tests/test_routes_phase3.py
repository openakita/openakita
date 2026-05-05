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
    PUT /settings round-trips them. The Settings tab UI relies on this
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
    # 2026-05 refactor — secrets are echoed back as-is so the Settings
    # tab can repopulate <input value=...> behind a 「显示」 toggle.
    # The boolean flag the UI keys off is ``has_runninghub_key``; the
    # raw value comes through unmodified.
    assert s["runninghub_api_key"] == "rhk-mock"
    assert s["has_runninghub_key"] is True
    assert s["runninghub_configured"] is True
    assert s["runninghub_workflow_image"] == "1234"
    assert s["runninghub_workflow_animate"] == "5678"
    assert s["runninghub_workflow_t2v"] == "9012"


def test_put_settings_round_trips_secret_unchanged(client) -> None:
    """Secrets round-trip exactly — the 2026-05 refactor dropped the
    server-side bullet redaction. The Settings tab now relies on this
    so a "save → refresh" flow keeps the input populated."""
    tc, _ = client
    tc.put("/settings", json={"runninghub_api_key": "real-key-1234567890"})
    r1 = tc.get("/settings")
    s1 = r1.json()["settings"]
    assert s1["runninghub_api_key"] == "real-key-1234567890"
    assert "•" not in s1["runninghub_api_key"]
    # Sending the same value back is a no-op (idempotent save).
    tc.put("/settings", json={"runninghub_api_key": s1["runninghub_api_key"]})
    r2 = tc.get("/settings")
    assert r2.json()["settings"]["runninghub_api_key"] == "real-key-1234567890"


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


# ─── Python dependency installer routes (mirrors avatar-studio) ──────


def test_list_python_deps_returns_all_known_packages(client) -> None:
    """GET /system/python-deps returns the static PYTHON_DEPS catalog
    enriched with live ``ok`` / ``version`` flags from the bootstrap
    probe. Each entry must carry the four spec fields the UI renders
    plus the ``ok`` boolean."""
    tc, _ = client
    r = tc.get("/system/python-deps")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    components = body["components"]
    by_id = {c["id"]: c for c in components}
    assert {"oss2", "mutagen", "edge_tts", "dashscope", "comfykit"} <= set(by_id)
    sample = by_id["oss2"]
    for k in ("display_name", "description", "import_name", "pip_spec", "ok"):
        assert k in sample, f"missing {k} in {sample}"


def test_python_dep_status_unknown_returns_404(client) -> None:
    tc, _ = client
    r = tc.get("/system/python-deps/no-such-package/status")
    assert r.status_code == 404


def test_python_dep_install_starts_install_thread(client, monkeypatch) -> None:
    """POST /install kicks off a background install via dep_bootstrap.
    We monkeypatch ``start_install`` so the test never spawns a real
    ``pip install`` subprocess (which would hit the network)."""
    tc, _ = client
    captured: dict[str, Any] = {}

    def fake_start_install(
        dep_id: str,
        import_name: str,
        pip_spec: str,
        *,
        plugin_dir: Any,
        friendly_name: str,
    ) -> dict[str, Any]:
        captured.update(
            dep_id=dep_id,
            import_name=import_name,
            pip_spec=pip_spec,
            friendly_name=friendly_name,
        )
        return {
            "id": dep_id,
            "import_name": import_name,
            "ok": False,
            "busy": True,
            "version": "",
            "error": "",
            "target": "/tmp/fake",
            "candidate_dirs": [],
            "log_tail": [],
            "last_error": "",
            "last_started_at": 0.0,
            "last_finished_at": 0.0,
            "last_success": False,
        }

    import manga_inline.dep_bootstrap as boot

    monkeypatch.setattr(boot, "start_install", fake_start_install)

    r = tc.post("/system/python-deps/oss2/install", json={"force": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["component"]["id"] == "oss2"
    assert body["component"]["busy"] is True
    assert captured["dep_id"] == "oss2"
    assert captured["pip_spec"].startswith("oss2")
    assert captured["import_name"] == "oss2"


def test_default_generation_backend_round_trips(client) -> None:
    """The new ``default_generation_backend`` settings key flows through
    PUT /settings → GET /settings unmodified, mirroring the avatar
    pattern. UI Settings → 「默认生成后端」 binds straight to this key."""
    tc, _ = client
    r = tc.put("/settings", json={"default_generation_backend": "runninghub"})
    assert r.status_code == 200
    cfg = r.json()["settings"]
    assert cfg["default_generation_backend"] == "runninghub"

    r2 = tc.get("/settings")
    assert r2.json()["settings"]["default_generation_backend"] == "runninghub"
