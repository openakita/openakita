from __future__ import annotations

import time

import httpx
import pytest

from openakita.api.routes import skill_stats as skill_stats_routes
from openakita.api.server import create_app
from openakita.skills.usage_events import SkillUsageEventLog


@pytest.fixture
async def client(monkeypatch, tmp_path):
    log = SkillUsageEventLog(tmp_path / "skill_usage_events.jsonl")
    now = int(time.time())
    log.record("alpha", "load")
    log.record("alpha", "edit")
    log.record("beta", "load")
    # backdate one event well outside the 7-day window
    with (tmp_path / "skill_usage_events.jsonl").open("a", encoding="utf-8") as f:
        import json

        f.write(json.dumps({"ts": now - 40 * 86400, "skill": "old", "action": "load"}) + "\n")

    monkeypatch.setattr(skill_stats_routes, "get_skill_usage_log", lambda: log)

    app = create_app()
    app.state.agent = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


async def test_usage_stats_default_window(client):
    resp = await client.get("/api/stats/skills/usage/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 7
    assert data["summary"]["total_skill_loads"] == 2
    assert data["summary"]["total_skill_edits"] == 1
    assert data["summary"]["total_skill_actions"] == 3
    assert data["summary"]["distinct_skills_used"] == 2
    skills = {s["skill"] for s in data["top_skills"]}
    assert skills == {"alpha", "beta"}
    assert data["top_skills"][0]["skill"] == "alpha"


async def test_usage_stats_long_window_includes_old_events(client):
    resp = await client.get("/api/stats/skills/usage/stats?days=90")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 90
    assert data["summary"]["distinct_skills_used"] == 3
    assert any(s["skill"] == "old" for s in data["top_skills"])


async def test_usage_stats_rejects_out_of_range_days(client):
    resp = await client.get("/api/stats/skills/usage/stats?days=0")
    assert resp.status_code == 422
    resp = await client.get("/api/stats/skills/usage/stats?days=9999")
    assert resp.status_code == 422
