"""C8b-2 audit script.

6 维度验证 v2 defaults / config-subsection 迁移：
- D1 完整性: ``policy_v2/defaults.py`` 存在 + 4 个公开符号；__init__ 导出
- D2 架构: 单一 source of truth（DEFAULT_BLOCKED_COMMANDS 不重复定义）；无反向依赖
- D3 No-whack-a-mole: ``_default_*_paths`` / ``_DEFAULT_BLOCKED_COMMANDS`` 在 v1
  ``policy.py`` 中已退化为 thin re-export（不再独立维护）
- D4 隐藏 bug: audit_logger / checkpoint 在 v1 PolicyEngine 单例不存在的纯 v2
  环境下能正确初始化 + 字段读出来与 PolicyConfigV2 一致
- D5 兼容: config.py 不再 import v1 私有符号 / reset_policy_engine；6 个 callsite
  全部迁到 reset_policy_v2_layer
- D6 hot-reload: ``reset_policy_v2_layer()`` 同时清 v2 engine + audit_logger
  singleton（UI Save Settings 后下次读取拿到最新值）

退出码：0 全 PASS / 1 任一 FAIL。

依赖前置审计：c8_audit_d1_completeness、d2_arch_clean、d3_no_whack_a_mole、
d4_no_hidden_bugs、d5_compat、c9_audit、c8b1_audit 都应保持 PASS。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openakita.core.policy_v2 import (  # noqa: E402
    AuditConfig,
    CheckpointConfig,
    PolicyConfigV2,
    default_blocked_commands,
    default_controlled_paths,
    default_forbidden_paths,
    default_protected_paths,
)
from openakita.core.policy_v2.defaults import (  # noqa: E402
    DEFAULT_BLOCKED_COMMANDS,
)
from openakita.core.policy_v2.global_engine import (  # noqa: E402
    is_initialized,
    reset_engine_v2,
    reset_policy_v2_layer,
    set_engine_v2,
)

# ---------------------------------------------------------------------------
# D1 — completeness
# ---------------------------------------------------------------------------
print("=== C8b-2 D1 completeness ===")

defaults_path = ROOT / "src" / "openakita" / "core" / "policy_v2" / "defaults.py"
assert defaults_path.exists(), "missing policy_v2/defaults.py"
print(f"  defaults.py exists: OK ({defaults_path.stat().st_size} bytes)")

# 4 public symbols + 1 constant
for name, obj in [
    ("default_protected_paths", default_protected_paths),
    ("default_forbidden_paths", default_forbidden_paths),
    ("default_controlled_paths", default_controlled_paths),
    ("default_blocked_commands", default_blocked_commands),
]:
    assert callable(obj), f"{name} not callable"
    val = obj()
    assert isinstance(val, list) and len(val) > 0, f"{name}() empty"
print("  4 default helpers callable + non-empty: OK")

assert isinstance(DEFAULT_BLOCKED_COMMANDS, tuple), (
    "DEFAULT_BLOCKED_COMMANDS should be tuple (immutable)"
)
assert len(DEFAULT_BLOCKED_COMMANDS) >= 9, "DEFAULT_BLOCKED_COMMANDS too short"
print(f"  DEFAULT_BLOCKED_COMMANDS tuple len={len(DEFAULT_BLOCKED_COMMANDS)}: OK")

# __init__ exports
init_text = (ROOT / "src" / "openakita" / "core" / "policy_v2" / "__init__.py").read_text(
    encoding="utf-8"
)
for sym in (
    "default_blocked_commands",
    "default_controlled_paths",
    "default_forbidden_paths",
    "default_protected_paths",
    "reset_policy_v2_layer",
):
    assert f'"{sym}"' in init_text, f"__init__.py missing __all__ entry: {sym}"
print("  __init__.py exports defaults + reset_policy_v2_layer: OK")

# ---------------------------------------------------------------------------
# D2 — single source of truth + no reverse dependency
# ---------------------------------------------------------------------------
print()
print("=== C8b-2 D2 single source of truth ===")

# DEFAULT_BLOCKED_COMMANDS 必须从 shell_risk 重导出，不能独立定义两份
defaults_text = defaults_path.read_text(encoding="utf-8")
# defaults.py 不应再有自己的 list literal 定义 ("reg", "regedit", ...)
assert (
    'from .shell_risk import DEFAULT_BLOCKED_COMMANDS' in defaults_text
), "defaults.py must import DEFAULT_BLOCKED_COMMANDS from shell_risk (single SoT)"
# 不应有重复 list literal 包含全部 9 个 token
forbidden_block = '"reg",\n    "regedit",\n    "netsh",'
assert forbidden_block not in defaults_text, (
    "defaults.py defines DEFAULT_BLOCKED_COMMANDS list literal —— duplicates shell_risk!"
)
print("  DEFAULT_BLOCKED_COMMANDS has single source (shell_risk): OK")

# 内容一致性
from openakita.core.policy_v2.shell_risk import (  # noqa: E402
    DEFAULT_BLOCKED_COMMANDS as SHELL_RISK_DEFAULT,
)

assert tuple(SHELL_RISK_DEFAULT) == DEFAULT_BLOCKED_COMMANDS, (
    "DEFAULT_BLOCKED_COMMANDS drift between shell_risk and defaults"
)
print("  shell_risk.DEFAULT_BLOCKED_COMMANDS == defaults.DEFAULT_BLOCKED_COMMANDS: OK")

# 无反向依赖：defaults 不依赖 v1 policy.py
assert "from ..policy " not in defaults_text and "import policy" not in defaults_text, (
    "defaults.py should not depend on v1 policy.py"
)
print("  defaults.py has no reverse dep to v1 policy.py: OK")

# ---------------------------------------------------------------------------
# D3 — v1 functions degraded to thin re-export
# ---------------------------------------------------------------------------
print()
print("=== C8b-2 D3 v1 degraded to re-export ===")

policy_v1_text = (ROOT / "src" / "openakita" / "core" / "policy.py").read_text(
    encoding="utf-8"
)
# v1 内的 _default_protected_paths 函数应只剩一行 return，不再含 platform.system 判断
# 简单 marker：函数体中应该 import _v2_default_protected_paths
assert "_v2_default_protected_paths" in policy_v1_text, (
    "v1 policy.py _default_protected_paths not delegated to v2"
)
assert "_v2_default_forbidden_paths" in policy_v1_text, (
    "v1 policy.py _default_forbidden_paths not delegated to v2"
)
assert "_v2_default_controlled_paths" in policy_v1_text, (
    "v1 policy.py _default_controlled_paths not delegated to v2"
)
# v1 _DEFAULT_BLOCKED_COMMANDS 也应该是 derived from v2
assert "_V2_DEFAULT_BLOCKED_COMMANDS" in policy_v1_text, (
    "v1 _DEFAULT_BLOCKED_COMMANDS not derived from v2"
)
print("  v1 _default_*_paths + _DEFAULT_BLOCKED_COMMANDS are thin re-exports: OK")

# parity check：runtime 行为完全一致
from openakita.core.policy import (  # noqa: E402
    _DEFAULT_BLOCKED_COMMANDS,
    _default_controlled_paths,
    _default_forbidden_paths,
    _default_protected_paths,
)

assert _default_protected_paths() == default_protected_paths(), (
    "v1 _default_protected_paths() != v2 default_protected_paths()"
)
assert _default_forbidden_paths() == default_forbidden_paths(), "forbidden paths drift"
assert _default_controlled_paths() == default_controlled_paths(), "controlled paths drift"
assert default_blocked_commands() == _DEFAULT_BLOCKED_COMMANDS, (
    "blocked commands drift between v1 / v2"
)
print("  runtime parity v1 == v2 for all 4 defaults: OK")

# ---------------------------------------------------------------------------
# D4 — audit_logger / checkpoint read v2 config end-to-end
# ---------------------------------------------------------------------------
print()
print("=== C8b-2 D4 subsystems read v2 ===")

import tempfile  # noqa: E402

from openakita.core.audit_logger import (  # noqa: E402
    get_audit_logger,
    reset_audit_logger,
)
from openakita.core.policy_v2.engine import (  # noqa: E402
    build_engine_from_config,
)

with tempfile.TemporaryDirectory() as tmp:
    custom_audit = str(Path(tmp) / "audit_v2.jsonl")
    custom_snap = str(Path(tmp) / "snap_v2")
    cfg = PolicyConfigV2(
        audit=AuditConfig(enabled=True, log_path=custom_audit),
        checkpoint=CheckpointConfig(
            enabled=True, snapshot_dir=custom_snap, max_snapshots=7
        ),
    )
    engine = build_engine_from_config(cfg)
    set_engine_v2(engine, cfg)
    reset_audit_logger()

    log = get_audit_logger()
    assert str(log._path) == custom_audit, (
        f"audit_logger path drift: got {log._path}, expected {custom_audit}"
    )
    assert log._enabled is True, "audit_logger.enabled didn't read v2"
    print("  audit_logger reads v2 AuditConfig.log_path/enabled: OK")

    import openakita.core.checkpoint as ck_mod  # noqa: E402

    ck_mod._global_checkpoint_mgr = None
    mgr = ck_mod.get_checkpoint_manager()
    assert str(mgr._base_dir) == custom_snap, "checkpoint snapshot_dir drift"
    assert mgr._max_snapshots == 7, "checkpoint max_snapshots drift"
    print("  checkpoint reads v2 CheckpointConfig.snapshot_dir/max_snapshots: OK")

    # cleanup
    ck_mod._global_checkpoint_mgr = None
    reset_audit_logger()
    reset_engine_v2()

# ---------------------------------------------------------------------------
# D5 — config.py decoupled from v1 internals
# ---------------------------------------------------------------------------
print()
print("=== C8b-2 D5 config.py decoupled ===")

config_text = (
    ROOT / "src" / "openakita" / "api" / "routes" / "config.py"
).read_text(encoding="utf-8")

# 不再 import v1 私有符号
banned = [
    "_default_protected_paths",
    "_default_forbidden_paths",
    "_default_controlled_paths",
    "_DEFAULT_BLOCKED_COMMANDS",
    "reset_policy_engine",
]
for tok in banned:
    assert tok not in config_text, f"config.py 仍含 v1 私有/废弃符号: {tok}"
print("  config.py 不再 import v1 私有符号 / reset_policy_engine: OK")

# 6 个 reset_policy_v2_layer 调用（每个 endpoint 一个）
n_v2_reset = config_text.count("reset_policy_v2_layer()")
assert n_v2_reset >= 6, (
    f"config.py reset_policy_v2_layer() 调用次数={n_v2_reset}, 期望≥6"
)
print(f"  config.py 含 {n_v2_reset} 次 reset_policy_v2_layer() 调用 (期望≥6): OK")

# defaults import 改成 v2
assert "from openakita.core.policy_v2.defaults import" in config_text, (
    "config.py 没有从 v2 defaults 导入"
)
print("  config.py 从 policy_v2.defaults 导入: OK")

# ---------------------------------------------------------------------------
# D6 — reset_policy_v2_layer 是 hot-reload 契约
# ---------------------------------------------------------------------------
print()
print("=== C8b-2 D6 reset_policy_v2_layer hot-reload ===")

# warm up
from openakita.core.policy_v2.global_engine import get_engine_v2  # noqa: E402

get_engine_v2()
assert is_initialized() is True

log_a = get_audit_logger()
reset_policy_v2_layer()

# v2 engine 应该被清掉
assert is_initialized() is False, "reset_policy_v2_layer didn't clear v2 engine"

# audit_logger 下次取应该是新对象
log_b = get_audit_logger()
assert log_a is not log_b, "reset_policy_v2_layer didn't clear audit_logger singleton"
print("  reset_policy_v2_layer clears both v2 engine + audit_logger: OK")

reset_engine_v2()
reset_audit_logger()

print()
print("C8b-2 ALL 6 DIMENSIONS PASS")
