from openakita.api.routes.config import (
    _apply_permission_mode_defaults,
    _mode_from_security,
    _normalize_permission_mode,
)


def test_permission_mode_accepts_trust_alias():
    assert _normalize_permission_mode("trust") == "yolo"
    assert _normalize_permission_mode("yolo") == "yolo"


def test_yolo_mode_syncs_low_interrupt_defaults():
    sec: dict = {"zones": {"default_zone": "protected"}}

    _apply_permission_mode_defaults(sec, "trust")

    assert sec["confirmation"]["mode"] == "yolo"
    assert sec["zones"]["default_zone"] == "workspace"
    assert sec["sandbox"]["enabled"] is False
    assert sec["self_protection"]["enabled"] is False
    assert sec["command_patterns"]["enabled"] is False
    assert _mode_from_security(sec) == "yolo"


def test_smart_mode_syncs_protection_defaults():
    sec: dict = {}

    _apply_permission_mode_defaults(sec, "smart")

    assert sec["confirmation"]["mode"] == "smart"
    assert sec["zones"]["default_zone"] == "controlled"
    assert sec["sandbox"]["enabled"] is True
    assert sec["self_protection"]["enabled"] is True
    assert sec["command_patterns"]["enabled"] is True


def test_cautious_mode_syncs_strict_defaults():
    sec: dict = {"zones": {"default_zone": "workspace"}}

    _apply_permission_mode_defaults(sec, "cautious")

    assert sec["confirmation"]["mode"] == "cautious"
    assert sec["zones"]["default_zone"] == "protected"
    assert sec["sandbox"]["enabled"] is True
    assert sec["self_protection"]["enabled"] is True
    assert sec["command_patterns"]["enabled"] is True

