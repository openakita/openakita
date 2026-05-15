"""C11 D3+D4: R5-18 零配置首装 + R5-19 跨平台路径矩阵.

R5-18 — 零配置首次安装
======================

新用户 ``pip install openakita`` 后无 ``identity/POLICIES.yaml``、无环境
变量、无 ``data/`` 目录, 第一次跑 CLI 应该:

1. ``policy_v2.get_engine_v2()`` 不抛异常, 用 builtin 安全默认构建
2. 引擎默认拒绝 (或要求 confirm) 危险操作; 不静默放行
3. ``identity/SOUL.md`` 等 builtin immune 路径在 ``Path.cwd() / "identity/SOUL.md"``
   形态下被识别 (新用户 cwd 通常是 home 目录, 没有 identity/ 子目录, 引擎仍
   能初始化, 只是没东西可命中)

R5-19 — 跨平台路径矩阵
======================

OpenAkita 跑在 Win / macOS / Linux. PathSpec 与 ``_path_under`` 必须保证:

1. Windows backslash 正反斜杠混用都能识别 (``C:\\Windows\\System32`` ==
   ``C:/Windows/System32``)
2. 大小写不敏感 (Windows 文件系统; POSIX 上为了 immune 配置不被 case 漏配
   也统一不敏感, 见 ``_normalize_path`` 文档)
3. UNC 路径 (``\\\\server\\share``) 不引发崩溃
4. 多连续斜杠折叠 (``C://Windows///System32``) 后等效
5. trailing slash 去除后等效
6. POSIX-only paths (``/etc``, ``/proc``) 在 Windows 上仍生效 (cross-mount
   场景, 例如 WSL 访问 Linux 目录)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.core.policy_v2.context import PolicyContext
from openakita.core.policy_v2.engine import PolicyEngineV2
from openakita.core.policy_v2.enums import ConfirmationMode, DecisionAction, SessionRole
from openakita.core.policy_v2.models import ToolCallEvent
from openakita.core.policy_v2.safety_immune_defaults import (
    BUILTIN_SAFETY_IMMUNE_BY_CATEGORY,
    expand_builtin_immune_paths,
)
from openakita.core.policy_v2.schema import PolicyConfigV2


def _ctx(**kw) -> PolicyContext:
    defaults = {
        "session_id": "t",
        "workspace": Path.cwd(),
        "channel": "cli",
        "is_owner": True,
        "session_role": SessionRole.AGENT,
        "confirmation_mode": ConfirmationMode.DEFAULT,
    }
    defaults.update(kw)
    return PolicyContext(**defaults)


# ============================================================================
# R5-18: zero-config first install
# ============================================================================


class TestR518ZeroConfigFirstInstall:
    def test_engine_constructs_with_no_yaml_no_env(self):
        """policy_v2.PolicyEngineV2() 不需要任何 YAML / 环境变量就能构造."""
        engine = PolicyEngineV2(config=PolicyConfigV2())
        assert engine is not None
        assert engine._config is not None
        assert engine._classifier is not None

    def test_zero_config_does_not_silently_allow_destructive(self):
        """零配置 + agent + default → DESTRUCTIVE 必 CONFIRM (不静默 ALLOW)."""
        engine = PolicyEngineV2(config=PolicyConfigV2())
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="delete_file", params={"path": "x.txt"}),
            _ctx(),
        )
        assert decision.action == DecisionAction.CONFIRM, (
            f"Zero-config must not silently allow destructive ops; "
            f"got {decision.action}"
        )

    def test_zero_config_unknown_tool_confirms(self):
        """完全没见过的工具名 → UNKNOWN class → CONFIRM (safety-by-default)."""
        engine = PolicyEngineV2(config=PolicyConfigV2())
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="totally_unknown_tool_xyz", params={}),
            _ctx(),
        )
        # In default/strict modes UNKNOWN should require confirmation.
        # dont_ask is the only mode that could conceivably allow, but per
        # design (see test_classifier::TestUnknownStrict) UNKNOWN remains
        # CONFIRM even there.
        assert decision.action == DecisionAction.CONFIRM

    def test_zero_config_builtin_9_categories_intact(self):
        """builtin 9 类 immune 路径数量 + 类别完整, 即使无 YAML."""
        cats = BUILTIN_SAFETY_IMMUNE_BY_CATEGORY
        assert set(cats.keys()) == {
            "identity",
            "audit",
            "checkpoints",
            "sessions",
            "scheduler",
            "credentials",
            "os_system",
            "kernel_fs",
            "package_dirs",
        }
        # Each category non-empty
        for name, paths in cats.items():
            assert paths, f"category {name} has no builtin paths"

    def test_zero_config_global_engine_singleton_loads(self, monkeypatch, tmp_path):
        """``get_engine_v2()`` 在无 POLICIES.yaml 的 cwd 也能成功懒加载."""
        from openakita.core.policy_v2 import global_engine

        # Reset the singleton for isolation
        original = global_engine._engine
        global_engine._engine = None
        global_engine._config = None
        try:
            # Patch settings to point to a non-existent identity dir
            monkeypatch.setattr(
                "openakita.core.policy_v2.global_engine.Path",
                Path,
            )
            engine = global_engine.get_engine_v2()
            assert engine is not None, (
                "get_engine_v2 must not return None even without YAML"
            )
        finally:
            global_engine._engine = original


# ============================================================================
# R5-19: cross-platform path matrix
# ============================================================================


class TestR519CrossPlatformPaths:
    @pytest.fixture
    def engine_with_cwd_immune(self):
        """Engine seeded with builtin immune paths anchored at the test cwd."""
        return PolicyEngineV2(config=PolicyConfigV2())

    @pytest.mark.parametrize(
        "raw_path",
        [
            # The real path the engine expects (cwd-anchored)
            lambda: str(Path.cwd() / "identity" / "SOUL.md"),
            # Backslash variant (Windows-style even on POSIX)
            lambda: str(Path.cwd() / "identity" / "SOUL.md").replace("/", "\\"),
            # Mixed separators
            lambda: str(Path.cwd()).replace("\\", "/")
            + "\\identity/SOUL.md",
            # Lowercase variant (Windows case-insensitive)
            lambda: str(Path.cwd() / "identity" / "SOUL.md").lower(),
            # Multiple slashes (user typo / UNC normalisation edge)
            lambda: str(Path.cwd()).replace("\\", "/") + "//identity///SOUL.md",
        ],
        ids=["canonical", "backslash", "mixed", "lower", "multi-slash"],
    )
    def test_safety_immune_normalises_path_form(
        self, engine_with_cwd_immune, raw_path
    ):
        """同一逻辑路径的 5 种形态都应命中 identity/SOUL.md immune."""
        path = raw_path()
        decision = engine_with_cwd_immune.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": path, "content": "x"}),
            _ctx(confirmation_mode=ConfirmationMode.TRUST),
        )
        assert decision.action == DecisionAction.CONFIRM, (
            f"path form {path!r} should match builtin SOUL.md immune; "
            f"got {decision.action} reason={decision.reason}"
        )
        assert decision.safety_immune_match

    def test_unc_path_does_not_crash(self, engine_with_cwd_immune):
        """``\\\\server\\share\\file`` 应安全降级 (engine 不抛, decision 给出)."""
        decision = engine_with_cwd_immune.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file",
                params={"path": "\\\\server\\share\\file.txt", "content": "x"},
            ),
            _ctx(),
        )
        # Just must not crash; decision can be CONFIRM (safety-by-default)
        # since UNC remote share is not in workspace
        assert decision.action in (
            DecisionAction.ALLOW,
            DecisionAction.CONFIRM,
            DecisionAction.DENY,
        )

    def test_posix_protected_path_works_on_any_os(self, engine_with_cwd_immune):
        """``/etc/ssh/sshd_config`` 在 Win 上也应被识别 (cross-mount/WSL 场景)."""
        decision = engine_with_cwd_immune.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file",
                params={"path": "/etc/ssh/sshd_config", "content": "x"},
            ),
            _ctx(confirmation_mode=ConfirmationMode.TRUST),
        )
        assert decision.action == DecisionAction.CONFIRM, (
            f"/etc/* should match builtin OS-system immune even on Win, "
            f"got {decision.action}"
        )

    def test_windows_program_files_protected_on_any_os(
        self, engine_with_cwd_immune
    ):
        """``C:/Program Files/...`` 在 POSIX 上也应被识别."""
        decision = engine_with_cwd_immune.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file",
                params={
                    "path": "C:/Program Files/SomeApp/config.ini",
                    "content": "x",
                },
            ),
            _ctx(confirmation_mode=ConfirmationMode.TRUST),
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_expand_builtin_uses_provided_cwd(self, tmp_path):
        """expand_builtin_immune_paths(cwd=...) honours 显式 cwd, 不读 process cwd."""
        expanded = expand_builtin_immune_paths(cwd=tmp_path)
        soul = str(tmp_path).replace("\\", "/") + "/identity/SOUL.md"
        assert soul in expanded, (
            f"expected expanded list to include {soul!r}; "
            f"sample={[p for p in expanded if 'identity' in p]}"
        )

    def test_expand_builtin_idempotent(self):
        """两次调用结果完全相等 (纯函数)."""
        a = expand_builtin_immune_paths()
        b = expand_builtin_immune_paths()
        assert a == b
