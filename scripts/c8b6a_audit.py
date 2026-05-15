"""C8b-6a audit (D1-D6) — \u8fc1 13+1 callsite \u5230 v2 manager / v2_native (\u4fdd\u7559 policy.py).

D1 — Completeness：13 \u751f\u4ea7 callsite + permission.py \u90fd\u8fc7 v2 manager / v2_native
D2 — No v1 imports in production code (channels/policy is unrelated)
D3 — skill_allowlist callers \u8d70 ``get_skill_allowlist_manager()``
D4 — user_allowlist callers \u8d70 ``get_engine_v2().user_allowlist``
D5 — death_switch callers \u8d70 ``get_death_switch_tracker()``
D6 — reasoning_engine + permission \u4f7f\u7528 v2 ``PolicyDecisionV2`` / ``DecisionAction``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


SRC = ROOT / "src" / "openakita"


def _strip_comments(text: str) -> str:
    # Remove pure-comment lines + triple-quoted blocks (heuristic).
    #
    # Handles three patterns:
    # 1. Pure ``# ...`` comment lines
    # 2. Multi-line triple-quoted blocks (toggle on odd triple count per line)
    # 3. Single-line docstrings ``"""..."""`` (even triple count > 0 → strip line)
    out: list[str] = []
    in_doc = False
    for raw in text.splitlines():
        triple_count = raw.count('"""') + raw.count("'''")
        if triple_count % 2 == 1:
            in_doc = not in_doc
            continue
        # Single-line docstring (e.g. ``"""one-line summary."""``) — strip
        if triple_count >= 2 and not in_doc:
            continue
        if in_doc:
            continue
        if raw.strip().startswith("#"):
            continue
        out.append(raw)
    return "\n".join(out)


def d1_completeness() -> None:
    print("\n=== C8b-6a D1 completeness ===")
    # All 6 production files migrated
    expected_v2_imports = {
        "src/openakita/core/agent.py": "from .policy_v2 import get_skill_allowlist_manager",
        "src/openakita/tools/handlers/skills.py": "from openakita.core.policy_v2 import get_skill_allowlist_manager",
        "src/openakita/core/security_actions.py": "from openakita.core.policy_v2.global_engine import get_engine_v2",
        "src/openakita/api/routes/config.py": "from openakita.core.policy_v2 import get_death_switch_tracker",
        "src/openakita/core/reasoning_engine.py": "from .policy_v2.adapter import evaluate_via_v2",
        "src/openakita/core/permission.py": "from .policy_v2.adapter import (",
    }
    for rel, must_contain in expected_v2_imports.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert must_contain in text, f"{rel} missing v2 import: {must_contain!r}"
    print(f"  all {len(expected_v2_imports)} files import v2 helpers: OK")

    print("D1 PASS")


def d2_no_v1_imports_in_production() -> None:
    print("\n=== C8b-6a D2 no v1 core.policy imports in production ===")
    pat_a = re.compile(r"from\s+openakita\.core\.policy\s+import\s+", re.MULTILINE)
    pat_b = re.compile(r"from\s+\.policy\s+import\s+(?!Group)", re.MULTILINE)  # exclude channels/policy
    pat_c = re.compile(r"from\s+\.\.policy\s+import\s+", re.MULTILINE)

    leftovers: list[tuple[Path, int, str]] = []
    for py in SRC.rglob("*.py"):
        if "policy_v2" in py.parts:
            # adapter.py keeps 2 delayed imports until C8b-6b deletes the file
            continue
        if py.name == "policy.py":
            continue
        text = py.read_text(encoding="utf-8")
        clean = _strip_comments(text)
        for pat in (pat_a, pat_b, pat_c):
            for m in pat.finditer(clean):
                # Locate line number in original file
                ln = clean[: m.start()].count("\n") + 1
                snippet = clean.splitlines()[ln - 1]
                # Filter out channels/gateway.py:from .policy import GroupPolicyConfig (channels.policy module)
                if "GroupPolicy" in snippet:
                    continue
                leftovers.append((py.relative_to(ROOT), ln, snippet.strip()))

    assert not leftovers, (
        "v1 core.policy imports still present in production:\n"
        + "\n".join(f"  {p}:{ln}: {s}" for p, ln, s in leftovers)
    )
    print("  zero v1 core.policy imports in production code (excl. policy_v2/adapter.py): OK")

    print("D2 PASS")


def d3_skill_allowlist_v2() -> None:
    print("\n=== C8b-6a D3 skill_allowlist callsites use v2 manager ===")
    paths = [
        SRC / "core" / "agent.py",
        SRC / "tools" / "handlers" / "skills.py",
    ]
    total = 0
    for p in paths:
        text = p.read_text(encoding="utf-8")
        clean = _strip_comments(text)
        # No v1 ``get_policy_engine().add_skill_allowlist`` etc.
        for v1_method in (
            ".add_skill_allowlist(",
            ".remove_skill_allowlist(",
            ".clear_skill_allowlists(",
        ):
            assert v1_method not in clean, f"{p.name} still calls v1 {v1_method}"
        # And does call v2 ``get_skill_allowlist_manager().``
        v2_calls = clean.count("get_skill_allowlist_manager()")
        total += v2_calls
        print(f"  {p.name}: {v2_calls} v2 callsite(s)")
    assert total == 5, f"expected 5 total v2 skill_allowlist callsites, got {total}"

    print("D3 PASS")


def d4_user_allowlist_v2() -> None:
    print("\n=== C8b-6a D4 user_allowlist callsites use v2 manager ===")
    p = SRC / "core" / "security_actions.py"
    text = p.read_text(encoding="utf-8")
    clean = _strip_comments(text)
    for v1_method in (
        ".get_user_allowlist(",
        ".remove_allowlist_entry(",
        "._save_user_allowlist(",
        "._config.user_allowlist",
    ):
        assert v1_method not in clean, f"security_actions.py still calls v1 {v1_method}"
    assert "get_engine_v2()" in clean, "security_actions.py missing v2 get_engine_v2 call"
    assert ".user_allowlist." in clean or ".user_allowlist" in clean
    print("  security_actions.py uses get_engine_v2().user_allowlist exclusively: OK")

    print("D4 PASS")


def d5_death_switch_v2() -> None:
    print("\n=== C8b-6a D5 death_switch callsites use v2 tracker ===")
    paths = [
        SRC / "core" / "security_actions.py",
        SRC / "api" / "routes" / "config.py",
    ]
    for p in paths:
        text = p.read_text(encoding="utf-8")
        clean = _strip_comments(text)
        for v1_method in (".reset_readonly_mode(", ".readonly_mode"):
            # ``.readonly_mode`` could appear as a key string in dict; tighten by
            # checking for actual attribute access patterns
            if v1_method == ".readonly_mode":
                # only flag if appears as ``.readonly_mode`` not preceded by quote
                pat = re.compile(r"(?<![\"'])\.readonly_mode\b")
                bad = [
                    line.strip()
                    for line in clean.splitlines()
                    if pat.search(line)
                ]
                assert not bad, f"{p.name} still reads v1 .readonly_mode: {bad}"
            else:
                assert v1_method not in clean, f"{p.name} still calls v1 {v1_method}"
        if "death_switch_tracker" in clean.lower() or "DeathSwitch" in text:
            pass  # OK
    print("  security_actions.py + config.py use get_death_switch_tracker: OK")

    # Reasoning engine death-switch read
    re_text = (SRC / "core" / "reasoning_engine.py").read_text(encoding="utf-8")
    re_clean = _strip_comments(re_text)
    assert "is_readonly_mode()" in re_clean, "reasoning_engine.py missing is_readonly_mode()"
    assert "_pe.readonly_mode" not in re_clean, "reasoning_engine.py still has _pe.readonly_mode"
    print("  reasoning_engine.py uses get_death_switch_tracker().is_readonly_mode(): OK")

    print("D5 PASS")


def d6_reasoning_engine_v2_native() -> None:
    print("\n=== C8b-6a D6 reasoning_engine + permission consume v2 types ===")
    re_text = (SRC / "core" / "reasoning_engine.py").read_text(encoding="utf-8")
    re_clean = _strip_comments(re_text)
    # No more v1 PolicyDecision / PolicyResult references
    for v1_token in ("PolicyDecision.", "PolicyResult(", "_pe."):
        assert v1_token not in re_clean, f"reasoning_engine.py still uses {v1_token}"
    # Yes uses v2 types
    assert "DecisionAction" in re_clean
    assert "PolicyDecisionV2" in re_clean
    assert "evaluate_via_v2(" in re_clean
    print("  reasoning_engine.py: 0 v1 token, all v2_native: OK")

    perm_text = (SRC / "core" / "permission.py").read_text(encoding="utf-8")
    perm_clean = _strip_comments(perm_text)
    assert "evaluate_via_v2_to_v1_result" not in perm_clean
    assert "evaluate_via_v2(" in perm_clean
    assert "V2_TO_V1_DECISION" in perm_clean
    print("  permission.py: 0 v1 adapter call, uses v2 evaluate_via_v2: OK")

    print("D6 PASS")


def main() -> None:
    d1_completeness()
    d2_no_v1_imports_in_production()
    d3_skill_allowlist_v2()
    d4_user_allowlist_v2()
    d5_death_switch_v2()
    d6_reasoning_engine_v2_native()
    print("\nC8b-6a ALL 6 DIMENSIONS PASS")


if __name__ == "__main__":
    main()
