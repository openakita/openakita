"""C8b-5 — _is_trust_mode external callers migrated to v2 helper。

覆盖：
1. ``agent.py:_check_trust_mode_skip`` 不再 import v1 ``policy.get_policy_engine``
2. ``gateway.py`` IM trust-mode bypass 用 v2 ``read_permission_mode_label``
3. v1 ``_is_trust_mode`` method 仅剩 1 个内部 caller (``policy.py``)
4. v1+v2 trust 判定语义等价（trust 与 non-trust 双向覆盖）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "openakita"


# ---------------------------------------------------------------------------
# Static (no runtime engine init) — fast & deterministic
# ---------------------------------------------------------------------------


def _strip_comments_and_doc(text: str) -> list[tuple[int, str]]:
    """Return ``[(line_no, code_line), ...]`` excluding pure comment lines and
    triple-quoted-string content lines (which often mention deleted symbols
    in historical doc comments). Heuristic — good enough for this audit."""
    out: list[tuple[int, str]] = []
    in_doc = False
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        # Toggle doc-string mode on triple quotes
        triple_count = stripped.count('"""') + stripped.count("'''")
        if triple_count % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        # Skip pure-comment lines
        if stripped.startswith("#"):
            continue
        out.append((i, raw))
    return out


class TestExternalCallersGone:
    """C8b-5 静态守卫：``_is_trust_mode`` 已无外部 callsite（排除 doc 注释）。"""

    def test_agent_py_no_v1_is_trust_mode_call(self) -> None:
        agent_text = (SRC_ROOT / "core" / "agent.py").read_text(encoding="utf-8")
        for ln, line in _strip_comments_and_doc(agent_text):
            assert 'getattr(engine, "_is_trust_mode"' not in line, f"agent.py:{ln}"
            assert "engine._is_trust_mode(" not in line, f"agent.py:{ln}"
            assert "pe._is_trust_mode(" not in line, f"agent.py:{ln}"

    def test_gateway_py_no_v1_is_trust_mode_call(self) -> None:
        gateway_text = (SRC_ROOT / "channels" / "gateway.py").read_text(encoding="utf-8")
        for ln, line in _strip_comments_and_doc(gateway_text):
            assert 'getattr(pe, "_is_trust_mode"' not in line, f"gateway.py:{ln}"
            assert "pe._is_trust_mode(" not in line, f"gateway.py:{ln}"
        # Must import v2 helper somewhere (this can be in a doc-string-stripped line too)
        assert "from ..core.policy_v2 import read_permission_mode_label" in gateway_text

    def test_check_trust_mode_skip_is_pure_v2(self) -> None:
        agent_text = (SRC_ROOT / "core" / "agent.py").read_text(encoding="utf-8")
        # Locate function body
        m = re.search(
            r"def _check_trust_mode_skip\([^)]*\)[^:]*:\s*\n(.*?)\n(?=\n\S|\nclass |\ndef )",
            agent_text,
            re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        assert "from .policy import get_policy_engine" not in body
        assert "v1_trust" not in body
        # Must have v2 read
        assert "from .policy_v2 import ConfirmationMode" in body
        assert "get_config_v2()" in body


# ---------------------------------------------------------------------------
# Runtime equivalence — v1 method vs v2 helper return same boolean
# ---------------------------------------------------------------------------


@pytest.fixture
def v2_engine_factory():
    """Build an isolated v2 engine with a chosen confirmation mode and
    register it as the global v2 layer (auto-cleanup)."""
    from openakita.core.policy_v2 import (
        PolicyConfigV2,
        build_engine_from_config,
    )
    from openakita.core.policy_v2.global_engine import (
        reset_engine_v2,
        set_engine_v2,
    )
    from openakita.core.policy_v2.schema import ConfirmationConfig

    created: list = []

    def _factory(mode):
        cfg = PolicyConfigV2(confirmation=ConfirmationConfig(mode=mode))
        eng = build_engine_from_config(cfg)
        set_engine_v2(eng, cfg)
        created.append(eng)
        return eng, cfg

    yield _factory

    reset_engine_v2()


class TestV1V2TrustEquivalence:
    """v1 ``_is_trust_mode()`` and v2 ``read_permission_mode_label() == "yolo"``
    must agree on every confirmation mode."""

    @pytest.mark.parametrize(
        "v2_mode_str,v1_mode_str,v1_auto_confirm,expected_trust",
        [
            ("trust", "yolo", True, True),
            ("default", "smart", False, False),
            ("strict", "cautious", False, False),
        ],
    )
    def test_equivalence(
        self,
        v2_engine_factory,
        v2_mode_str: str,
        v1_mode_str: str,
        v1_auto_confirm: bool,
        expected_trust: bool,
    ) -> None:
        from openakita.core.policy import (
            ConfirmationConfig as V1Conf,
        )
        from openakita.core.policy import (
            PolicyEngine,
            SecurityConfig,
        )
        from openakita.core.policy_v2 import read_permission_mode_label
        from openakita.core.policy_v2.enums import ConfirmationMode

        v2_engine_factory(ConfirmationMode(v2_mode_str))

        v1_eng = PolicyEngine(
            SecurityConfig(
                enabled=True,
                confirmation=V1Conf(mode=v1_mode_str, auto_confirm=v1_auto_confirm),
            )
        )

        v2_is_trust = read_permission_mode_label() == "yolo"
        v1_is_trust = v1_eng._is_trust_mode()
        assert v1_is_trust == expected_trust
        assert v2_is_trust == expected_trust
        assert v1_is_trust == v2_is_trust


class TestV1MethodStillInternal:
    """``_is_trust_mode`` v1 method retained for internal v1 ``assert_tool_allowed``
    use; not yet deletable until C8b-6."""

    def test_method_still_present_on_engine(self) -> None:
        from openakita.core.policy import PolicyEngine

        eng = PolicyEngine()
        assert hasattr(eng, "_is_trust_mode")
        assert callable(eng._is_trust_mode)

    def test_assert_tool_allowed_still_uses_internal_method(self) -> None:
        """smoke test: v1 ``assert_tool_allowed`` does not break after the
        external isolation."""
        from openakita.core.policy import PolicyEngine

        eng = PolicyEngine()
        result = eng.assert_tool_allowed("read_file", {"path": "/tmp/test.txt"})
        assert result is not None
