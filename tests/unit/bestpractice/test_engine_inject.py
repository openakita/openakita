"""Tests for BPEngine orchestrator injection and scheduler factory."""
from unittest.mock import MagicMock
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.scheduler import LinearScheduler


class TestOrchestratorInjection:
    def test_initial_state_is_none(self):
        sm = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        assert engine._orchestrator is None

    def test_set_orchestrator(self):
        sm = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        mock_orch = MagicMock()
        engine.set_orchestrator(mock_orch)
        assert engine._orchestrator is mock_orch

    def test_get_orchestrator_returns_injected(self):
        sm = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        mock_orch = MagicMock()
        engine.set_orchestrator(mock_orch)
        assert engine._get_orchestrator() is mock_orch

    def test_get_orchestrator_fallback_when_none(self):
        sm = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        # Should not crash, returns None or global fallback
        result = engine._get_orchestrator()
        # Just verify no exception


class TestGetScheduler:
    def test_returns_linear_scheduler(self):
        sm = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        mock_config = MagicMock()
        mock_config.subtasks = [MagicMock(id="s1")]
        mock_snap = MagicMock()
        sched = engine._get_scheduler(mock_config, mock_snap)
        assert isinstance(sched, LinearScheduler)


class TestGetConfig:
    def test_uses_snap_bp_config_if_available(self):
        sm = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        mock_config = MagicMock()
        mock_snap = MagicMock()
        mock_snap.bp_config = mock_config
        assert engine._get_config(mock_snap) is mock_config

    def test_falls_back_to_registry(self):
        sm = MagicMock()
        sc = MagicMock()
        engine = BPEngine(sm, sc)
        mock_snap = MagicMock()
        mock_snap.bp_config = None
        mock_snap.bp_id = "test_bp"
        # This test just verifies no crash when bp_config is None
        # The actual fallback depends on facade.get_bp_config_loader()
        engine._get_config(mock_snap)


def test_facade_set_orchestrator():
    """facade.set_bp_orchestrator() delegates to engine.set_orchestrator()."""
    from unittest.mock import patch
    mock_engine = MagicMock()
    with patch("seeagent.bestpractice.facade._bp_engine", mock_engine):
        from seeagent.bestpractice.facade import set_bp_orchestrator
        mock_orch = MagicMock()
        set_bp_orchestrator(mock_orch)
        mock_engine.set_orchestrator.assert_called_once_with(mock_orch)
