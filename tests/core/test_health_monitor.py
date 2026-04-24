# tests/core/test_health_monitor.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from openakita.core.health_monitor import HealthMonitor, HealthReport
from openakita.core.health_config import HealthConfig


def test_health_report_creation():
    report = HealthReport(
        stale_tasks=["task_1", "task_2"],
        stale_delegations=["dlg_1"],
        orphaned_processes=[],
    )
    assert len(report.stale_tasks) == 2
    assert len(report.stale_delegations) == 1
    assert report.has_issues is True


def test_health_report_no_issues():
    report = HealthReport(
        stale_tasks=[],
        stale_delegations=[],
        orphaned_processes=[],
    )
    assert report.has_issues is False


@pytest.mark.asyncio
async def test_health_monitor_single_check():
    config = HealthConfig(stale_task_age=60, check_interval=10)
    monitor = HealthMonitor(config=config)

    mock_orchestrator = MagicMock()
    mock_orchestrator.cleanup_expired_delegations = AsyncMock(return_value=[])

    report = await monitor.check_health(orchestrator=mock_orchestrator)

    assert isinstance(report, HealthReport)
    mock_orchestrator.cleanup_expired_delegations.assert_called_once()


@pytest.mark.asyncio
async def test_health_monitor_finds_stale_tasks():
    import time
    from unittest.mock import patch

    config = HealthConfig(stale_task_age=1)  # 1 second for test
    monitor = HealthMonitor(config=config)

    # Use public API with mocked time to register a task that appears stale
    fake_registration_time = time.time() - 10  # 10 seconds in the past
    with patch("openakita.core.health_monitor.time.time", return_value=fake_registration_time):
        monitor.register_task("sess_1", "t1")

    mock_orchestrator = MagicMock()
    mock_orchestrator.cleanup_expired_delegations = AsyncMock(return_value=[])

    report = await monitor.check_health(orchestrator=mock_orchestrator)

    assert "sess_1" in report.stale_tasks
