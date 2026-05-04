"""Phase 4.4 — story templates: schema validity + GET /templates route.

We verify two things:
1. Every entry in ``TEMPLATES`` is well-formed against the schema we
   advertise to the UI (id uniqueness, both-language fields filled,
   visual_style references a real VisualStyleSpec, ratio is a known
   ratio, panel-count and per-panel-seconds inside the same bounds
   ``_EpisodeCreate`` accepts).
2. The ``GET /templates`` route returns the catalogue verbatim and
   inside the standard ``{"ok": True, ...}`` envelope.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manga_models import RATIOS, VISUAL_STYLES_BY_ID
from manga_templates import TEMPLATES, find_template, list_templates

# ─── Schema validation ───────────────────────────────────────────────────


def test_templates_have_unique_ids() -> None:
    ids = [t["id"] for t in TEMPLATES]
    assert len(ids) == len(set(ids)), "duplicate template id"


def test_templates_required_fields() -> None:
    for tpl in TEMPLATES:
        for k in (
            "id",
            "title_zh",
            "title_en",
            "blurb_zh",
            "blurb_en",
            "tag",
            "visual_style",
            "ratio",
            "n_panels",
            "seconds_per_panel",
            "story_zh",
            "story_en",
        ):
            assert k in tpl, f"{tpl.get('id', '?')}: missing {k}"
            assert tpl[k] not in (None, ""), f"{tpl['id']}: empty {k}"


def test_templates_visual_style_resolves() -> None:
    for tpl in TEMPLATES:
        assert tpl["visual_style"] in VISUAL_STYLES_BY_ID, (
            f"{tpl['id']}: unknown visual_style={tpl['visual_style']}"
        )


def test_templates_ratio_resolves() -> None:
    for tpl in TEMPLATES:
        assert tpl["ratio"] in RATIOS, f"{tpl['id']}: unknown ratio={tpl['ratio']}"


def test_templates_panel_layout_within_episode_create_bounds() -> None:
    # _EpisodeCreate enforces: n_panels in [1, 30], seconds_per_panel in [2, 15]
    for tpl in TEMPLATES:
        assert 1 <= int(tpl["n_panels"]) <= 30, f"{tpl['id']}: bad n_panels"
        assert 2 <= int(tpl["seconds_per_panel"]) <= 15, f"{tpl['id']}: bad seconds_per_panel"


def test_templates_story_within_episode_create_bounds() -> None:
    # _EpisodeCreate.story max_length = 8000
    for tpl in TEMPLATES:
        assert len(tpl["story_zh"]) <= 8000
        assert len(tpl["story_en"]) <= 8000


def test_list_templates_is_a_defensive_copy() -> None:
    a = list_templates()
    a[0]["title_zh"] = "MUTATED"
    b = list_templates()
    assert b[0]["title_zh"] != "MUTATED"


def test_find_template_known_and_unknown() -> None:
    sample = TEMPLATES[0]
    hit = find_template(sample["id"])
    assert hit is not None and hit["id"] == sample["id"]
    assert find_template("does-not-exist") is None


# ─── Route smoke test ─────────────────────────────────────────────────────
#
# Inlines the same minimal _StubAPI used by other route suites — we
# can't ``from test_routes_phase2 import _StubAPI`` because pytest
# doesn't put the tests/ directory on ``sys.path`` (only the plugin
# dir, via conftest). Keeping the stub local also avoids drift if the
# Phase-2 tests evolve.


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

    app = FastAPI()
    app.include_router(p._router)  # type: ignore[attr-defined]
    tc = TestClient(app)
    try:
        yield tc, p
    finally:
        await p.on_unload()


async def test_get_templates_route_returns_full_catalogue(client) -> None:
    tc, _p = client
    r = tc.get("/templates")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["templates"], list)
    assert len(body["templates"]) == len(TEMPLATES)
    head = body["templates"][0]
    assert head["id"] == TEMPLATES[0]["id"]
    assert head["visual_style"] == TEMPLATES[0]["visual_style"]
