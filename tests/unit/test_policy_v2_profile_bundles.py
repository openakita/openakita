"""``policy_v2/defaults.py`` 单一真源契约测试。

v1.27.13 起，schema 默认 / 路由层 ``_apply_security_profile_defaults`` /
setup-center 占位都从 ``PROFILE_BUNDLES`` + ``FACTORY_DEFAULT_PROFILE`` 取，
避免历史上多处硬编码 "trust" / "protect" 漂移的痛。

本模块只钉 **单一真源** 这件事：
1. PROFILE_BUNDLES 内容自洽（结构、字段、enum 合法性）
2. ``profile_bundle()`` 返回 deep-copy，调用方 mutate 不污染真源
3. schema 默认与 PROFILE_BUNDLES[FACTORY_DEFAULT_PROFILE] 在引擎源字段
   （``confirmation.mode``、``profile.current``）上完全一致
4. ``_apply_security_profile_defaults`` 套用后的 sec dict 与 bundle 在
   bundle 字段集上完全一致（用户已写过的非 bundle 字段保留不动）

刻意不覆盖的范围：
- profile bundle 的"业务含义"（trust 该关 sandbox、protect 该 default、
  strict 该 strict）由 ``test_profile_to_engine_invariants.py`` /
  ``test_security_permission_mode_api.py`` 已经钉过；本模块只测"真源对齐"
- ``sandbox.enabled`` schema 默认 (True) 与 bundle["trust"]["sandbox"]
  ["enabled"] (False) 的有意非对称——见 ``PolicyConfigV2`` docstring，
  本模块只保证 schema 默认引擎模式 = bundle 引擎模式
"""

from __future__ import annotations

import pytest

from openakita.core.policy_v2.defaults import (
    FACTORY_DEFAULT_PROFILE,
    PROFILE_BUNDLES,
    factory_default_confirmation_mode,
    factory_default_profile_current,
    profile_bundle,
)
from openakita.core.policy_v2.enums import ConfirmationMode

# ---------------------------------------------------------------------------
# PROFILE_BUNDLES 内容自洽
# ---------------------------------------------------------------------------


class TestProfileBundlesShape:
    def test_baked_profile_names_complete(self) -> None:
        """4 个 baked profile 必须齐全；``custom`` 不在表里（它没有 baked bundle）。"""
        assert set(PROFILE_BUNDLES.keys()) == {"trust", "protect", "strict", "off"}, (
            "PROFILE_BUNDLES 真源缺/多 baked profile；setup-center 卡片与此表"
            "枚举必须一致，否则 UI 会出现没有对应 bundle 的卡片。"
        )

    def test_factory_default_is_baked_profile(self) -> None:
        assert FACTORY_DEFAULT_PROFILE in PROFILE_BUNDLES, (
            f"FACTORY_DEFAULT_PROFILE={FACTORY_DEFAULT_PROFILE!r} 必须是 baked 名称之一"
        )

    @pytest.mark.parametrize("profile", sorted(PROFILE_BUNDLES.keys()))
    def test_bundle_has_required_keys(self, profile: str) -> None:
        """每个 bundle 都必须显式声明 5 个原子开关 + enabled。

        缺一个就意味着 ``_apply_security_profile_defaults`` 在切换到这个 profile
        时不会写入对应字段——会让 sec dict 留下"上一个 profile 的残留"。
        """
        bundle = PROFILE_BUNDLES[profile]
        required = {
            "enabled",
            "confirmation",
            "sandbox",
            "shell_risk",
            "death_switch",
            "checkpoint",
        }
        assert set(bundle.keys()) == required, (
            f"{profile} bundle 字段集 {set(bundle.keys())} != required {required}"
        )

    @pytest.mark.parametrize("profile", sorted(PROFILE_BUNDLES.keys()))
    def test_confirmation_mode_is_valid_enum(self, profile: str) -> None:
        """每个 bundle 的 confirmation.mode 必须是 ConfirmationMode 合法值——
        否则 schema 校验在用户点这个 profile 后会直接抛 ValidationError。"""
        mode_str = PROFILE_BUNDLES[profile]["confirmation"]["mode"]
        ConfirmationMode(mode_str)  # raises ValueError if invalid

    def test_off_bundle_disables_security_globally(self) -> None:
        """off 是唯一 ``enabled=False`` 的 bundle——这是它的定义。"""
        bundle = PROFILE_BUNDLES["off"]
        assert bundle["enabled"] is False
        for block_name, fields in bundle.items():
            if block_name == "enabled":
                continue
            if "enabled" in fields and block_name != "confirmation":
                assert fields["enabled"] is False, (
                    f"off bundle 里 {block_name}.enabled 应该为 False"
                )


# ---------------------------------------------------------------------------
# profile_bundle() deep-copy 语义
# ---------------------------------------------------------------------------


class TestProfileBundleAccessor:
    def test_returns_deep_copy(self) -> None:
        b1 = profile_bundle("trust")
        b1["confirmation"]["mode"] = "MUTATED"
        b1["new_field"] = "should_not_leak"
        # 真源不受影响
        assert PROFILE_BUNDLES["trust"]["confirmation"]["mode"] == "trust"
        assert "new_field" not in PROFILE_BUNDLES["trust"]
        # 第二次拿仍是干净的
        b2 = profile_bundle("trust")
        assert b2["confirmation"]["mode"] == "trust"
        assert "new_field" not in b2

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(KeyError) as excinfo:
            profile_bundle("totally_unknown")
        assert "totally_unknown" in str(excinfo.value)

    def test_custom_profile_raises(self) -> None:
        """custom 没有 baked bundle，调用方必须单独处理（不应静默拿到 trust）。"""
        with pytest.raises(KeyError):
            profile_bundle("custom")


# ---------------------------------------------------------------------------
# 跨真源一致性：schema 默认 ↔ bundle
# ---------------------------------------------------------------------------


class TestSingleSourceOfTruth:
    """schema 默认 + ``_apply_security_profile_defaults`` 必须共用同一份真源。"""

    def test_factory_default_confirmation_mode_matches_bundle(self) -> None:
        helper_value = factory_default_confirmation_mode()
        assert isinstance(helper_value, ConfirmationMode)
        bundle_value = PROFILE_BUNDLES[FACTORY_DEFAULT_PROFILE]["confirmation"]["mode"]
        assert helper_value.value == bundle_value

    def test_factory_default_profile_current_matches_constant(self) -> None:
        assert factory_default_profile_current() == FACTORY_DEFAULT_PROFILE

    def test_schema_default_consumes_factory_helper(self) -> None:
        """``PolicyConfigV2()`` 必须通过 factory helper 取默认 mode，不能再硬编码。"""
        from openakita.core.policy_v2.schema import PolicyConfigV2

        cfg = PolicyConfigV2()
        # schema 默认 mode == bundle[FACTORY_DEFAULT_PROFILE].confirmation.mode
        assert (
            cfg.confirmation.mode
            == PROFILE_BUNDLES[FACTORY_DEFAULT_PROFILE]["confirmation"]["mode"]
        )
        # schema 默认 profile.current == FACTORY_DEFAULT_PROFILE
        assert cfg.profile.current == FACTORY_DEFAULT_PROFILE

    @pytest.mark.parametrize("profile", sorted(PROFILE_BUNDLES.keys()))
    def test_apply_profile_defaults_writes_full_bundle(self, profile: str) -> None:
        """``_apply_security_profile_defaults`` 套餐写下的 sec 在 bundle 字段集
        上必须与 PROFILE_BUNDLES 完全一致——单一真源契约的核心。"""
        from openakita.api.routes import config as config_routes

        sec: dict = {}
        config_routes._apply_security_profile_defaults(sec, profile)

        bundle = PROFILE_BUNDLES[profile]
        assert sec["enabled"] == bundle["enabled"]
        assert sec["profile"]["current"] == profile
        for block_name, fields in bundle.items():
            if block_name == "enabled":
                continue
            for field_name, field_value in fields.items():
                assert sec[block_name][field_name] == field_value, (
                    f"{profile} bundle 套用后 {block_name}.{field_name} = "
                    f"{sec[block_name][field_name]!r}, expected {field_value!r}"
                )

    def test_apply_profile_defaults_preserves_user_non_bundle_fields(self) -> None:
        """套用 bundle 时不能抹掉用户已写过的非 bundle 字段（如 timeout_seconds、
        custom_critical）。这条挡的是"切换 profile 把用户调过的 timeout 重置"
        类回归。"""
        from openakita.api.routes import config as config_routes

        sec: dict = {
            "confirmation": {"timeout_seconds": 30, "mode": "default"},
            "shell_risk": {"custom_critical": ["danger"], "enabled": False},
        }
        config_routes._apply_security_profile_defaults(sec, "trust")
        # bundle 字段被覆盖
        assert sec["confirmation"]["mode"] == "trust"
        assert sec["shell_risk"]["enabled"] is True
        # 非 bundle 字段保留
        assert sec["confirmation"]["timeout_seconds"] == 30
        assert sec["shell_risk"]["custom_critical"] == ["danger"]

    def test_apply_profile_defaults_for_custom_leaves_atomic_fields_alone(self) -> None:
        """custom 不在 PROFILE_BUNDLES 里：仅更新 profile.current，原子字段
        全部保留——这保证用户在 setup-center 上点"自定义"不会把已调好的
        sandbox/shell_risk 设置抹掉。"""
        from openakita.api.routes import config as config_routes

        sec: dict = {
            "enabled": True,
            "confirmation": {"mode": "default", "timeout_seconds": 30},
            "sandbox": {"enabled": True},
            "shell_risk": {"enabled": False},
            "profile": {"current": "protect", "base": None},
        }
        config_routes._apply_security_profile_defaults(sec, "custom")
        assert sec["profile"]["current"] == "custom"
        assert sec["profile"]["base"] == "protect"
        # 原子字段不被 bundle 覆盖
        assert sec["confirmation"]["mode"] == "default"
        assert sec["sandbox"]["enabled"] is True
        assert sec["shell_risk"]["enabled"] is False
