"""Tests for AgentFactory EXTERNAL_CLI branch + Pool special-case."""
from __future__ import annotations

import pytest

from openakita.config import Settings


def test_settings_external_cli_max_concurrent_default():
    s = Settings()
    assert s.external_cli_max_concurrent == 3


from openakita.agents.factory import AgentFactory
from openakita.agents.cli_runner import ExternalCliLimiter


def test_factory_builds_external_cli_limiter_from_settings(monkeypatch):
    from openakita import config as cfg
    monkeypatch.setattr(cfg.settings, "external_cli_max_concurrent", 7, raising=False)

    factory = AgentFactory()

    assert isinstance(factory._external_cli_limiter, ExternalCliLimiter)
    assert factory._external_cli_limiter._max_concurrent == 7
