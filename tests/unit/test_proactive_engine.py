"""L1 Unit Tests: ProactiveEngine feedback tracker and configuration."""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

from openakita.core.proactive import (
    ProactiveConfig,
    ProactiveEngine,
    ProactiveFeedbackTracker,
)


class TestProactiveConfig:
    def test_default_config(self):
        cfg = ProactiveConfig()
        assert cfg.enabled is False
        assert cfg.max_daily_messages == 3
        assert cfg.min_interval_minutes == 120
        assert cfg.quiet_hours_start == 23
        assert cfg.quiet_hours_end == 7

    def test_custom_config(self):
        cfg = ProactiveConfig(enabled=True, max_daily_messages=5)
        assert cfg.enabled is True
        assert cfg.max_daily_messages == 5


class TestProactiveFeedbackTracker:
    @pytest.fixture
    def tracker(self, tmp_path):
        return ProactiveFeedbackTracker(data_file=tmp_path / "feedback.json")

    def test_initial_send_count(self, tracker):
        assert tracker.get_today_send_count() == 0

    def test_record_send(self, tracker):
        tracker.record_send("greeting")
        assert tracker.get_today_send_count() == 1

    def test_get_last_send_time(self, tracker):
        assert tracker.get_last_send_time() is None
        tracker.record_send("weather")
        last = tracker.get_last_send_time()
        assert last is not None

    def test_record_reaction(self, tracker):
        tracker.record_send("tip")
        tracker.record_reaction("positive", response_delay_minutes=2.5)

    def test_adjusted_config(self, tracker):
        base = ProactiveConfig(enabled=True)
        adjusted = tracker.get_adjusted_config(base)
        assert isinstance(adjusted, ProactiveConfig)


class TestProactiveEngine:
    @pytest.fixture
    def engine(self, tmp_path):
        cfg = ProactiveConfig(enabled=True, min_interval_minutes=1)
        return ProactiveEngine(config=cfg, feedback_file=tmp_path / "feedback.json")

    def test_toggle(self, engine):
        engine.toggle(False)
        engine.toggle(True)

    def test_update_user_interaction(self, engine):
        engine.update_user_interaction()

    def test_process_user_response(self, engine):
        engine.process_user_response("谢谢提醒", delay_minutes=1.0)
