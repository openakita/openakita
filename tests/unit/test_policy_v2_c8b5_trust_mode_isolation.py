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


class TestV2TrustModeMapping:
    """v2 ``read_permission_mode_label() == "yolo"`` correctly tracks
    ``ConfirmationMode.TRUST`` across all 5 supported modes.

    C8b-6b：原 ``TestV1V2TrustEquivalence`` 验证 v1 ``_is_trust_mode()`` 与 v2
    helper 等价；v1 ``policy.py`` 整文件已删，只验证 v2 一侧的正确性。
    """

    @pytest.mark.parametrize(
        "v2_mode_str,expected_trust",
        [
            ("trust", True),
            ("default", False),
            ("strict", False),
            ("accept_edits", False),
            ("dont_ask", True),
        ],
    )
    def test_v2_trust_label_matches_mode(
        self,
        v2_engine_factory,
        v2_mode_str: str,
        expected_trust: bool,
    ) -> None:
        from openakita.core.policy_v2 import read_permission_mode_label
        from openakita.core.policy_v2.enums import ConfirmationMode

        v2_engine_factory(ConfirmationMode(v2_mode_str))
        assert (read_permission_mode_label() == "yolo") == expected_trust


class TestV1ModuleFullyDeleted:
    """C8b-6b：v1 ``core/policy.py`` 整文件已删；任何残余 import 都应抛
    ``ModuleNotFoundError``。"""

    def test_v1_policy_module_not_importable(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            __import__("openakita.core.policy")
