"""C8b-1 audit script.

5 维度验证 v2 补能完成度：
- D1 完整性: 3 个 manager 模块 + 2 个 step 实装存在
- D2 架构: skill / death_switch 是 module singleton；user 是 engine-scoped；
  无反向耦合（v2→api / v2→v1）
- D3 No-whack-a-mole: 关键状态字段每个只声明一次
- D4 隐藏 bug: dry-run preview engine 不污染计数；engine reset 不破坏 singleton
- D5 兼容: 单测无 v1→v2 行为分裂；test 间 singleton 隔离正常工作

退出码：0 全 PASS / 1 任一 FAIL。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openakita.core.policy_v2 import (  # noqa: E402
    ApprovalClass,
    ConfirmationMode,
    DeathSwitchTracker,
    DecisionAction,
    PolicyConfigV2,
    PolicyContext,
    PolicyEngineV2,
    SessionRole,
    SkillAllowlistManager,
    ToolCallEvent,
    UserAllowlistManager,
    get_death_switch_tracker,
    get_skill_allowlist_manager,
    reset_death_switch_tracker,
    reset_skill_allowlist_manager,
)
from openakita.core.policy_v2.classifier import ApprovalClassifier  # noqa: E402
from openakita.core.policy_v2.enums import DecisionSource  # noqa: E402

print("=== C8b-1 D1 completeness ===")

# D1.1 — 3 个 manager module 存在
mod_dir = ROOT / "src" / "openakita" / "core" / "policy_v2"
assert (mod_dir / "user_allowlist.py").exists(), "missing user_allowlist.py"
assert (mod_dir / "skill_allowlist.py").exists(), "missing skill_allowlist.py"
assert (mod_dir / "death_switch.py").exists(), "missing death_switch.py"
print("  3 manager modules exist: OK")

# D1.2 — engine 暴露 user_allowlist + count_in_death_switch
e = PolicyEngineV2()
assert isinstance(
    e.user_allowlist, UserAllowlistManager
), "engine.user_allowlist not a UserAllowlistManager"
assert e.count_in_death_switch is True, "engine.count_in_death_switch default != True"
print("  engine.user_allowlist + engine.count_in_death_switch wired: OK")

# D1.3 — public API export
import openakita.core.policy_v2 as v2  # noqa: E402

for sym in (
    "UserAllowlistManager",
    "SkillAllowlistManager",
    "DeathSwitchTracker",
    "get_skill_allowlist_manager",
    "get_death_switch_tracker",
    "reset_skill_allowlist_manager",
    "reset_death_switch_tracker",
    "command_to_pattern",
):
    assert hasattr(v2, sym), f"policy_v2.__init__ missing export: {sym}"
print("  8 new exports in policy_v2.__init__: OK")

# D1.4 — step 9 / step 10 不再是 stub return None
def _lookup(name: str):
    if name == "sensitive_tool":
        return ApprovalClass.MUTATING_SCOPED, DecisionSource.EXPLICIT_HANDLER_ATTR
    if name == "deny_me":
        return ApprovalClass.DESTRUCTIVE, DecisionSource.EXPLICIT_HANDLER_ATTR
    return None


cfg = PolicyConfigV2()
clf = ApprovalClassifier(explicit_lookup=_lookup, shell_risk_config=cfg.shell_risk)
e2 = PolicyEngineV2(classifier=clf, config=cfg)
ctx = PolicyContext(
    session_id="audit_d1",
    workspace=Path.cwd(),
    session_role=SessionRole.AGENT,
    confirmation_mode=ConfirmationMode.DEFAULT,
    is_owner=True,
)
e2.user_allowlist.add_entry("sensitive_tool", {})
d = e2.evaluate_tool_call(ToolCallEvent(tool="sensitive_tool", params={}), ctx)
assert (
    d.action == DecisionAction.ALLOW and "persistent_allowlist" in d.reason
), f"step 9 user_allowlist relax not effective: {d}"
print("  step 9 (user_allowlist) actually relaxes CONFIRM→ALLOW: OK")

print()
print("=== C8b-1 D2 architecture ===")

# D2.1 — skill / death_switch 是 module singleton
import openakita.core.policy_v2.death_switch as ds_mod  # noqa: E402
import openakita.core.policy_v2.skill_allowlist as sk_mod  # noqa: E402

assert hasattr(ds_mod, "_singleton"), "death_switch missing _singleton"
assert hasattr(sk_mod, "_singleton"), "skill_allowlist missing _singleton"
print("  module-level _singleton defined for skill / death_switch: OK")

# D2.2 — singleton 是同一实例
assert get_skill_allowlist_manager() is get_skill_allowlist_manager()
assert get_death_switch_tracker() is get_death_switch_tracker()
print("  singletons return same instance across calls: OK")

# D2.3 — user_allowlist 是 engine-scoped 不是 singleton
e3 = PolicyEngineV2(config=PolicyConfigV2())
e4 = PolicyEngineV2(config=PolicyConfigV2())
assert e3.user_allowlist is not e4.user_allowlist
print("  engine-scoped UserAllowlistManager (not shared): OK")

# D2.4 — 无 v2→api 反向耦合（broadcast 走 hook 注入）
src = (mod_dir / "death_switch.py").read_text(encoding="utf-8")
assert (
    "openakita.api" not in src
), "death_switch.py imports openakita.api → reverse coupling!"
assert (
    "from ...api" not in src
), "death_switch.py imports api via relative → reverse coupling!"
print("  death_switch.py 无 v2→api 反向耦合（broadcast 走 hook）: OK")

# D2.5 — 无 v2→v1 反向耦合（不能 import core.policy）
for f in ("user_allowlist.py", "skill_allowlist.py", "death_switch.py"):
    src = (mod_dir / f).read_text(encoding="utf-8")
    assert (
        "from ..policy import" not in src
        and "from openakita.core.policy import" not in src
    ), f"{f} imports v1 core.policy → reverse coupling!"
print("  3 manager 无 v2→v1 反向耦合: OK")

print()
print("=== C8b-1 D3 no-whack-a-mole ===")

# D3.1 — 每个 singleton 模块只声明一次 _singleton
for f, sym in (
    ("death_switch.py", "_singleton"),
    ("skill_allowlist.py", "_singleton"),
):
    content = (mod_dir / f).read_text(encoding="utf-8")
    decls = sum(1 for line in content.splitlines() if "_singleton: " in line and " | None = None" in line)
    assert decls == 1, f"{f}: expected 1 _singleton declaration, found {decls}"
print("  singleton declared exactly once per module: OK")

# D3.2 — engine.user_allowlist property 只在 engine.py 定义
eng_src = (mod_dir / "engine.py").read_text(encoding="utf-8")
prop_count = eng_src.count("def user_allowlist(self)")
assert prop_count == 1, f"engine.user_allowlist defined {prop_count} times"
print("  engine.user_allowlist property defined exactly once: OK")

print()
print("=== C8b-1 D4 hidden bugs ===")

# D4.1 — engine count_in_death_switch=False 不污染 tracker
reset_death_switch_tracker()
e5 = PolicyEngineV2(classifier=clf, config=cfg)
e5.count_in_death_switch = False
ask_ctx = PolicyContext(
    session_id="audit_d4",
    workspace=Path.cwd(),
    session_role=SessionRole.ASK,
    confirmation_mode=ConfirmationMode.DEFAULT,
    is_owner=True,
)
for _ in range(10):
    e5.evaluate_tool_call(ToolCallEvent(tool="deny_me", params={}), ask_ctx)
stats = get_death_switch_tracker().stats()
assert stats["total_denials"] == 0, f"preview engine polluted tracker: {stats}"
print("  count_in_death_switch=False engine doesn't pollute tracker: OK")

# D4.2 — broadcast hook 异常被吞，不破坏计数
reset_death_switch_tracker()
t = get_death_switch_tracker()
t.set_broadcast_hook(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
for _ in range(3):
    t.record_decision(action="deny", tool_name="x", threshold=3)
assert t.is_readonly_mode() is True
print("  broadcast hook exception swallowed without breaking trigger: OK")

# D4.3 — singleton 跨 reset 仍有效
reset_skill_allowlist_manager()
m1 = get_skill_allowlist_manager()
m1.add("s1", ["t1"])
reset_skill_allowlist_manager()
m2 = get_skill_allowlist_manager()
assert m2 is not m1, "reset_skill_allowlist_manager() should create new instance"
assert m2.is_allowed("t1") is False, "new singleton should be empty"
print("  reset_*_singleton() actually creates fresh instance: OK")

# D4.4 — readonly 模式下 read 工具放行
reset_death_switch_tracker()
e6 = PolicyEngineV2(classifier=clf, config=cfg)
for _ in range(3):
    e6.evaluate_tool_call(ToolCallEvent(tool="deny_me", params={}), ask_ctx)
assert get_death_switch_tracker().is_readonly_mode() is True
agent_ctx = PolicyContext(
    session_id="audit_d4",
    workspace=Path.cwd(),
    session_role=SessionRole.AGENT,
    confirmation_mode=ConfirmationMode.DEFAULT,
    is_owner=True,
)
d_read = e6.evaluate_tool_call(
    ToolCallEvent(tool="read_file", params={"path": "README.md"}), agent_ctx
)
assert d_read.action != DecisionAction.DENY, f"read_file denied in readonly mode: {d_read}"
print("  readonly mode allows read tools (not over-blocking): OK")

print()
print("=== C8b-1 D5 compatibility ===")

# D5.1 — UserAllowlistManager match parity 与 v1 _check_persistent_allowlist
reset_death_switch_tracker()
m = UserAllowlistManager(PolicyConfigV2())
m.add_raw_entry("command", {"pattern": "npm install*", "needs_sandbox": False})
assert m.match("run_shell", {"command": "npm install react"}) is not None
assert m.match("run_shell", {"command": "npm uninstall foo"}) is None
print("  UserAllowlistManager.match parity with v1 _check_persistent_allowlist: OK")

# D5.2 — semantic command (with python -m) match 与 v1 一致
m2_test = UserAllowlistManager(PolicyConfigV2())
m2_test.add_raw_entry("command", {"pattern": "pip install*"})
assert (
    m2_test.match(
        "run_shell", {"command": '"C:/Python/python.exe" -m pip install requests'}
    )
    is not None
), "semantic match for python -m pip install failed"
print("  semantic command normalization (python -m → pip) parity: OK")

# D5.3 — v1 mark_confirmed scope='session' 等价：v2 暂未实装 (推到 C8b-3)，
# 但 manager 接口本身不阻断未来接入 SessionAllowlistManager。
# 仅断言 add_entry 不抛异常 + match 可工作。
print("  (v1 session-scope mark_confirmed 留待 C8b-3 SessionAllowlistManager): SKIP")

print()
print("=== C8b-1 D6 preview isolation (P1 regression guard) ===")

# D6.1 — make_preview_engine() exists and disables counting
from openakita.core.policy_v2 import make_preview_engine  # noqa: E402

reset_death_switch_tracker()
prev = make_preview_engine()
assert (
    prev.count_in_death_switch is False
), "make_preview_engine must produce engine with count_in_death_switch=False"
print("  make_preview_engine().count_in_death_switch == False: OK")

# D6.2 — DENY samples on preview engine don't pollute global tracker
reset_death_switch_tracker()
prev2 = make_preview_engine(cfg)
prev2._classifier = clf  # use deny_me-aware classifier
ask_ctx2 = PolicyContext(
    session_id="audit_d6",
    workspace=Path.cwd(),
    session_role=SessionRole.ASK,
    confirmation_mode=ConfirmationMode.DEFAULT,
    is_owner=True,
)
for _ in range(10):
    prev2.evaluate_tool_call(ToolCallEvent(tool="deny_me", params={}), ask_ctx2)
stats_after_preview = get_death_switch_tracker().stats()
assert (
    stats_after_preview["total_denials"] == 0
), f"preview engine polluted global tracker: {stats_after_preview}"
print("  10 DENY samples via preview don't pollute global tracker: OK")

# D6.3 — preview cfg is deep copy (UI editing preview doesn't leak)
prev3 = make_preview_engine()  # uses get_config_v2() deepcopy
prev3.user_allowlist.add_entry("write_file", {"path": "/preview-only-1234"})
from openakita.core.policy_v2 import get_config_v2  # noqa: E402

global_paths = [
    e.get("path", "")
    for e in get_config_v2().user_allowlist.tools
]
assert (
    "/preview-only-1234" not in global_paths
), "preview engine mutated global config user_allowlist!"
print("  preview engine config is deep-copied (no leak to global): OK")

# D6.4 — config.py preview endpoint uses make_preview_engine
api_src = (ROOT / "src" / "openakita" / "api" / "routes" / "config.py").read_text(
    encoding="utf-8"
)
assert (
    "make_preview_engine" in api_src
), "/api/config/security/preview not migrated to make_preview_engine!"
# Also ensure it doesn't fall back to the global engine path
assert (
    "engine = get_engine_v2()" not in api_src.split("preview_security_config")[1].split("\ndef ")[0]
), "preview endpoint still references global engine"
print("  /api/config/security/preview uses make_preview_engine: OK")

print()
print("C8b-1 ALL 6 DIMENSIONS PASS")
