from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from openakita.skills.usage_events import SkillUsageEventLog


def _write_events(path, events):
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_record_appends_valid_events(tmp_path):
    log = SkillUsageEventLog(tmp_path / "skill_usage_events.jsonl")
    log.record("alpha", "load")
    log.record("alpha", "edit")
    # invalid action / empty skill are silently ignored
    log.record("alpha", "bogus")
    log.record("", "load")

    lines = (tmp_path / "skill_usage_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["skill"] == "alpha"
    assert parsed[0]["action"] == "load"
    assert parsed[1]["action"] == "edit"
    assert all(isinstance(p["ts"], int) for p in parsed)


def test_aggregate_empty_log_returns_zeroed_summary(tmp_path):
    log = SkillUsageEventLog(tmp_path / "missing.jsonl")
    result = log.aggregate(7)
    assert result["period_days"] == 7
    assert result["summary"] == {
        "total_skill_loads": 0,
        "total_skill_edits": 0,
        "total_skill_actions": 0,
        "distinct_skills_used": 0,
    }
    assert result["top_skills"] == []
    # by_day is filled for every day in the window (inclusive endpoints)
    assert len(result["by_day"]) == 8
    assert all(d["total_count"] == 0 for d in result["by_day"])


def test_aggregate_counts_and_top_skills(tmp_path):
    path = tmp_path / "events.jsonl"
    now = int(time.time())
    events = [
        {"ts": now - 100, "skill": "alpha", "action": "load"},
        {"ts": now - 90, "skill": "alpha", "action": "load"},
        {"ts": now - 80, "skill": "alpha", "action": "edit"},
        {"ts": now - 70, "skill": "beta", "action": "load"},
    ]
    _write_events(path, events)

    log = SkillUsageEventLog(path)
    result = log.aggregate(7)

    assert result["summary"] == {
        "total_skill_loads": 3,
        "total_skill_edits": 1,
        "total_skill_actions": 4,
        "distinct_skills_used": 2,
    }

    top = result["top_skills"]
    assert top[0]["skill"] == "alpha"
    assert top[0]["load_count"] == 2
    assert top[0]["edit_count"] == 1
    assert top[0]["total_count"] == 3
    assert top[0]["percentage"] == 75.0
    assert top[0]["last_used_at"] == now - 80

    beta = next(s for s in top if s["skill"] == "beta")
    assert beta["total_count"] == 1
    assert beta["percentage"] == 25.0


def test_aggregate_filters_out_of_window_events(tmp_path):
    path = tmp_path / "events.jsonl"
    now = int(time.time())
    events = [
        {"ts": now - 2 * 86400, "skill": "recent", "action": "load"},
        {"ts": now - 40 * 86400, "skill": "old", "action": "load"},
    ]
    _write_events(path, events)

    log = SkillUsageEventLog(path)
    result = log.aggregate(7)

    assert result["summary"]["total_skill_loads"] == 1
    assert result["summary"]["distinct_skills_used"] == 1
    assert [s["skill"] for s in result["top_skills"]] == ["recent"]


def test_aggregate_tolerates_corrupt_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    now = int(time.time())
    with path.open("w", encoding="utf-8") as f:
        f.write("not json\n")
        f.write("\n")
        f.write(json.dumps({"ts": now - 10, "skill": "alpha", "action": "load"}) + "\n")
        f.write(json.dumps({"ts": "bad", "skill": "x", "action": "load"}) + "\n")

    log = SkillUsageEventLog(path)
    result = log.aggregate(7)
    assert result["summary"]["total_skill_actions"] == 1


def test_aggregate_by_day_bucketing(tmp_path):
    path = tmp_path / "events.jsonl"
    now = int(time.time())
    events = [
        {"ts": now - 10, "skill": "alpha", "action": "load"},
        {"ts": now - 10, "skill": "alpha", "action": "edit"},
    ]
    _write_events(path, events)

    log = SkillUsageEventLog(path)
    result = log.aggregate(7)

    today = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
    today_bucket = next(d for d in result["by_day"] if d["date"] == today)
    assert today_bucket["load_count"] == 1
    assert today_bucket["edit_count"] == 1
    assert today_bucket["total_count"] == 2
    assert today_bucket["skills"][0]["skill"] == "alpha"
    assert today_bucket["skills"][0]["total_count"] == 2
