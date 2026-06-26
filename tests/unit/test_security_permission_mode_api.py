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


def test_factory_default_security_is_trust():
    """Fresh install / empty POLICIES.yaml must report trust mode end-to-end.

    This is the routing-layer half of the v1.27.13 "default = trust" contract;
    the schema half is in tests/unit/test_policy_v2_loader.py::
    TestSchemaDefaults::test_defaults_construct_clean. Both halves must agree
    or the setup-center will show one mode while the engine runs another.
    """
    # 空 dict → 空 sec → 没有 confirmation 块 → fallback 必须是 yolo (=trust)
    assert _mode_from_security(None) == "yolo"
    assert _mode_from_security({}) == "yolo"
    # _normalize_security_profile 在缺失/未知输入时也必须落到 trust，
    # 否则 setup-center 第一次"刷新全部"会显示 protect 卡片。
    assert config_routes._normalize_security_profile("") == "trust"
    assert config_routes._normalize_security_profile("unknown") == "trust"


def test_schema_default_and_trust_bundle_agree_on_confirmation_mode():
    """schema 默认 (PolicyConfigV2()) 与 ``_apply_security_profile_defaults("trust")``
    在引擎决策真源 ``confirmation.mode`` 上必须一致。

    这是 v1.27.13 默认值变更的核心契约：fresh install 用户（走 schema 默认）
    与主动点"信任方案"按钮的用户（走 profile bundle）必须感受到同一档"打扰
    水平"。两条路径分属两份独立 source-of-truth：
    - schema 默认：``policy_v2/schema.py``
    - profile bundle：``api/routes/config.py::_apply_security_profile_defaults``

    其他字段（sandbox / shell_risk / death_switch）schema 默认有意保留 fail-safe
    与 bundle 不完全一致——见 ``PolicyConfigV2`` docstring。本测试**只**钉住
    引擎真源 mode 一项；扩大覆盖前请先评估是否真要让 schema 默认完全 = trust bundle。
    """
    from openakita.core.policy_v2 import PolicyConfigV2

    sec: dict = {}
    config_routes._apply_security_profile_defaults(sec, "trust")
    bundle_mode = sec["confirmation"]["mode"]

    schema_mode = PolicyConfigV2().confirmation.mode

    assert bundle_mode == schema_mode == "trust", (
        f"schema 默认 confirmation.mode ({schema_mode!r}) 与 trust profile "
        f"bundle ({bundle_mode!r}) 必须一致；引擎真源是 mode 字段，"
        "两者不一致会让 fresh install 与 '点击信任方案' 行为分叉。"
    )


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
