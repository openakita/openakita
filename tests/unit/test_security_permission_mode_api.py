import pytest

import openakita.api.routes.config as config_routes
from openakita.api.routes.config import (
    _PermissionModeBody,
    _apply_permission_mode_defaults,
    _mode_from_security,
    _normalize_permission_mode,
    write_permission_mode,
)


def test_permission_mode_accepts_trust_alias():
    assert _normalize_permission_mode("trust") == "yolo"
    assert _normalize_permission_mode("yolo") == "yolo"


def test_yolo_mode_syncs_low_interrupt_defaults():
    """trust profile (=v1 yolo): confirmation=trust, sandbox off, but
    shell_risk / death_switch / checkpoint stay on for fail-safe."""
    sec: dict = {}

    _apply_permission_mode_defaults(sec, "trust")

    assert sec["confirmation"]["mode"] == "trust"
    assert sec["sandbox"]["enabled"] is False
    assert sec["shell_risk"]["enabled"] is True
    assert sec["death_switch"]["enabled"] is True
    assert sec["enabled"] is True
    assert sec["profile"]["current"] == "trust"
    assert _mode_from_security(sec) == "yolo"


def test_smart_mode_syncs_protection_defaults():
    """smart → protect profile: confirmation=default, all defenses on."""
    sec: dict = {}

    _apply_permission_mode_defaults(sec, "smart")

    assert sec["confirmation"]["mode"] == "default"
    assert sec["sandbox"]["enabled"] is True
    assert sec["shell_risk"]["enabled"] is True
    assert sec["death_switch"]["enabled"] is True
    assert sec["enabled"] is True
    assert sec["profile"]["current"] == "protect"


def test_cautious_mode_syncs_strict_defaults():
    """cautious → strict profile: confirmation=strict, defenses on."""
    sec: dict = {}

    _apply_permission_mode_defaults(sec, "cautious")

    assert sec["confirmation"]["mode"] == "strict"
    assert sec["sandbox"]["enabled"] is True
    assert sec["shell_risk"]["enabled"] is True
    assert sec["death_switch"]["enabled"] is True
    assert sec["enabled"] is True
    assert sec["profile"]["current"] == "strict"


@pytest.mark.asyncio
async def test_write_permission_mode_fails_when_yaml_unreadable(monkeypatch):
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: None)

    result = await write_permission_mode(_PermissionModeBody(mode="smart"))

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_write_permission_mode_fails_when_yaml_write_fails(monkeypatch):
    data = {"security": {}}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: data)
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda _data: False)

    result = await write_permission_mode(_PermissionModeBody(mode="smart"))

    assert result["status"] == "error"

