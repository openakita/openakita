# tests/integration/test_health_monitor_integration.py
import pytest
import asyncio
from openakita.core.health_monitor import HealthMonitor
from openakita.core.health_config import HealthConfig


@pytest.mark.asyncio
async def test_health_monitor_lifecycle():
    config = HealthConfig(check_interval=1)
    monitor = HealthMonitor(config=config)

    await monitor.start(orchestrator=None)
    assert monitor._running is True

    await asyncio.sleep(0.1)

    await monitor.stop()
    assert monitor._running is False


@pytest.mark.asyncio
async def test_health_monitor_start_is_idempotent():
    """Calling start() multiple times should not create multiple loops."""
    config = HealthConfig(check_interval=1)
    monitor = HealthMonitor(config=config)

    await monitor.start(orchestrator=None)
    first_task = monitor._task

    await monitor.start(orchestrator=None)
    assert monitor._task is first_task  # Same task, not a new one

    await monitor.stop()
    assert monitor._running is False


@pytest.mark.asyncio
async def test_health_monitor_stop_is_idempotent():
    """Calling stop() multiple times should not raise errors."""
    config = HealthConfig(check_interval=1)
    monitor = HealthMonitor(config=config)

    await monitor.start(orchestrator=None)
    await monitor.stop()
    assert monitor._running is False

    # Second stop should be a no-op
    await monitor.stop()
    assert monitor._running is False
