from openakita.core.log_health import LogHealthRegistry


def test_log_health_registry_aggregates_repeated_events():
    registry = LogHealthRegistry(rate_limit_seconds=60)

    first = registry.record("memory", "profile_extraction", "ReadError")
    second = registry.record("memory", "profile_extraction", "ReadError again")
    summary = registry.summary()

    assert first is True
    assert second is False
    assert summary["event_count"] == 1
    assert summary["events"][0]["count"] == 2
    assert summary["events"][0]["suppressed"] == 1
