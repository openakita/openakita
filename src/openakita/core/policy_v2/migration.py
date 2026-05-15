"""v1 → v2 ``identity/POLICIES.yaml`` migration (pure functions).

设计原则：
- **纯函数**：``migrate_v1_to_v2(dict) -> dict``，不读不写文件
- **detect-then-migrate**：``detect_schema_version`` 看 dict 形态决定 v1/v2/mixed
- **保守迁移**：v1 字段 union 到 v2 同义槽位（``zones.protected`` ∪ ``zones.forbidden``
  ∪ ``self_protection.protected_dirs`` → ``safety_immune.paths``，dedupe）
- **WARN 不静默丢**：废弃字段（``zones.controlled`` / ``zones.default_zone``）
  返回 ``MigrationReport`` 列出，loader 决定是否打日志
- **mixed 优先 v2**：若 dict 同时有 ``security.zones`` 和 ``security.safety_immune``，
  v2 字段胜出，v1 字段被 WARN 列出但不覆盖

mode 别名：
- ``yolo`` → ``trust``
- ``smart`` → ``default``
- ``cautious`` → ``strict``
- ``auto_confirm: true`` → ``mode: trust``（强制覆盖任何已设 mode）
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from .enums import LEGACY_MODE_ALIASES
from .schema import PolicyConfigV2

# 从 PolicyConfigV2 自动派生 v2 字段集合——避免 schema 新增字段时这里漏改。
# ``enabled`` 单独处理（在主流程上方），其余字段全部走 wholesale 拷贝。
_V2_BLOCKS: frozenset[str] = frozenset(PolicyConfigV2.model_fields) - {"enabled"}

# C16 Phase B：所有在 ``security.*`` 顶层合法的键（v2 + v1 兼容）。
# 任何不在这个集合里的键被识别为"未知"——可能是 typo，也可能是攻击者
# 注入的伪字段。loader 会把它们记到 ``MigrationReport.unknown_security_keys``
# 并打 WARN，不静默丢。
_V1_LEGACY_KEYS: frozenset[str] = frozenset(
    {"zones", "command_patterns", "self_protection"}
)
_KNOWN_SECURITY_KEYS: frozenset[str] = (
    frozenset(PolicyConfigV2.model_fields) | _V1_LEGACY_KEYS
)


@dataclass(slots=True)
class MigrationReport:
    """迁移过程中收集的事件，供 loader 打日志/审计。"""

    schema_detected: Literal["v1", "v2", "mixed", "empty"] = "empty"
    fields_migrated: list[str] = field(default_factory=list)
    fields_dropped: list[str] = field(default_factory=list)
    """废弃字段（zones.controlled / zones.default_zone / confirmation.auto_confirm）。"""
    conflicts: list[str] = field(default_factory=list)
    """mixed schema 时同义槽位双声明的冲突列表。"""
    unknown_security_keys: list[str] = field(default_factory=list)
    """C16 Phase B：``security.*`` 下出现的未知顶层键。

    旧实现会被 ``_V2_BLOCKS`` filter 静默丢弃；现在显式记录，loader 必 WARN，
    操作员能发现 typo / 攻击者塞进来的字段。
    """
    env_overrides: Any = None
    """C18 Phase C：``OverrideReport`` 实例（即使没有任何 ENV 设置，也是一
    个空的 ``OverrideReport``）。``None`` 仅出现在 ``apply_env=False`` 的
    离线分析路径——global_engine 写审计时会跳过 ``None``。

    类型故意用 ``Any`` 而非 ``OverrideReport``：避免 migration → env_overrides
    的反向 import 循环（``apply_env_overrides`` 内部不需要 migration）。
    """

    def has_changes(self) -> bool:
        return bool(self.fields_migrated or self.fields_dropped)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_schema_version(
    raw: dict[str, Any] | None,
) -> Literal["v1", "v2", "mixed", "empty"]:
    """识别 dict 是 v1/v2/mixed/empty。

    判定依据：
    - empty: ``raw`` 为 None / 空 / 没有 ``security`` 顶层键
    - v2: 含 ``security.safety_immune`` / ``security.shell_risk`` /
      ``security.death_switch`` / ``security.audit`` / ``security.workspace`` 任一
    - v1: 含 ``security.zones`` / ``security.command_patterns`` /
      ``security.self_protection`` 任一
    - mixed: 同时有 v1 + v2 标识
    """
    if not raw or not isinstance(raw, dict):
        return "empty"
    sec = raw.get("security")
    if not isinstance(sec, dict) or not sec:
        return "empty"

    v1_markers = ("zones", "command_patterns", "self_protection")
    v2_markers = (
        "safety_immune",
        "shell_risk",
        "death_switch",
        "audit",
        "workspace",
        "profile",
        "owner_only",
        "approval_classes",
        "unattended",
        "session_role",
    )
    has_v1 = any(k in sec for k in v1_markers)
    has_v2 = any(k in sec for k in v2_markers)

    if has_v1 and has_v2:
        return "mixed"
    if has_v2:
        return "v2"
    if has_v1:
        return "v1"
    # 只有 confirmation 的话——v1/v2 共有，按 mode 字段推断
    confirm = sec.get("confirmation", {})
    if isinstance(confirm, dict):
        mode = str(confirm.get("mode", "")).lower()
        if mode in LEGACY_MODE_ALIASES:
            return "v1"
        if mode in ("default", "accept_edits", "trust", "strict", "dont_ask"):
            return "v2"
    return "v2"  # 极简配置（仅 enabled）当 v2 处理（默认即可）


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_v1_to_v2(
    raw: dict[str, Any] | None,
) -> tuple[dict[str, Any], MigrationReport]:
    """v1 → v2 in-place 翻译（纯函数，输入不可变）。

    Returns:
        (v2_dict, report)。v2_dict 永远是合法的 v2 schema 形态；report 列出迁移轨迹。
    """
    report = MigrationReport()
    detected = detect_schema_version(raw)
    report.schema_detected = detected

    if detected == "empty":
        return {"security": {}}, report

    src = deepcopy(raw or {})
    src_sec = src.get("security", {}) or {}
    out_sec: dict[str, Any] = {}

    # C16 Phase B：扫一遍 src_sec.keys()，把不在 _KNOWN_SECURITY_KEYS 的全部
    # 记到 unknown_security_keys。loader 会 WARN，schema Pydantic 不会看到
    # 它们（因为我们下面只拷贝已知字段），所以也不会触发 extra='forbid'。
    if isinstance(src_sec, dict):
        for k in src_sec:
            if k not in _KNOWN_SECURITY_KEYS:
                report.unknown_security_keys.append(f"security.{k}")

    # enabled (both schemas) — C16: 不再 bool() 强转。原 `bool("no") = True` 的
    # 静默错误现在交给 schema 的 Strict[bool] 让 ValidationError 自然抛出。
    if "enabled" in src_sec:
        out_sec["enabled"] = src_sec["enabled"]

    # v2 字段直接复制（mixed 模式下 v2 优先）。
    # 注意：``confirmation`` 也在这里整块复制——避免下方 v1 mode-alias 特殊处理
    # 把 v2 confirmation 里的 typo 字段（``typo_field`` 等）silently 滤掉，
    # 让 Pydantic ``extra='forbid'`` 能正常抓出来。
    # 字段集合从 PolicyConfigV2.model_fields 自动派生，避免 schema 新增字段时漏改。
    for block in _V2_BLOCKS:
        if block in src_sec and src_sec[block] is not None:
            out_sec[block] = deepcopy(src_sec[block])

    # ----- workspace from zones.workspace -----
    zones = src_sec.get("zones") or {}
    if zones:
        if "workspace" in zones and "workspace" not in out_sec:
            ws_paths = zones["workspace"]
            if isinstance(ws_paths, str):
                ws_paths = [ws_paths]
            elif not isinstance(ws_paths, list):
                ws_paths = []
            out_sec["workspace"] = {"paths": list(ws_paths) or ["${CWD}"]}
            report.fields_migrated.append("zones.workspace → workspace.paths")
        elif "workspace" in zones and "workspace" in out_sec:
            report.conflicts.append("zones.workspace vs workspace.paths (kept v2)")

        # ----- safety_immune ← zones.protected ∪ forbidden -----
        immune_union: list[str] = []
        for legacy_key in ("protected", "forbidden"):
            v = zones.get(legacy_key) or []
            if isinstance(v, list):
                immune_union.extend(str(p) for p in v if p)
        if immune_union:
            existing = _safe_paths(out_sec.get("safety_immune"))
            merged = _dedupe_preserve_order(existing + immune_union)
            out_sec.setdefault("safety_immune", {})["paths"] = merged
            report.fields_migrated.append("zones.protected ∪ zones.forbidden → safety_immune.paths")

        # ----- 废弃字段 -----
        if "controlled" in zones:
            report.fields_dropped.append("zones.controlled (v2 不再分区)")
        if "default_zone" in zones:
            report.fields_dropped.append("zones.default_zone (v2 不再分区)")

    # ----- self_protection 拆分迁移 -----
    # v1 ``self_protection`` 控制 3 件事：(1) L5 自保护检查（``protected_dirs``）；
    # (2) death_switch 阈值 / 倍数；(3) 审计文件输出。v2 拆成 ``safety_immune``
    # / ``death_switch`` / ``audit`` 三个独立 block，各有自己的 ``enabled``。
    #
    # **关键不变量**：v1 ``self_protection.enabled = false`` 表示用户**主动关闭**
    # 了 L5 + death_switch（参考 ``core/policy.py:1148/1418/1518``）。迁移必须
    # 把这个意图传递到 v2，**不能**因为字段重组就静默重新启用：
    #   - protected_dirs 不再合入 safety_immune（用户原本不要）
    #   - death_switch.enabled 显式置 false（避免 v2 默认 true 覆盖意图）
    #   - audit 独立保留（v1 ``audit_to_file`` 才是真正的开关）
    self_prot = src_sec.get("self_protection") or {}
    if self_prot:
        # v1 self_protection.enabled 默认 True；显式 false 才需要传播停用语义
        sp_enabled = self_prot.get("enabled", True)
        sp_disabled = sp_enabled is False  # 严格 is False（None/缺失视为启用）

        # ----- safety_immune ∪ self_protection.protected_dirs -----
        sp_dirs = self_prot.get("protected_dirs") or []
        if isinstance(sp_dirs, list) and sp_dirs and not sp_disabled:
            existing = _safe_paths(out_sec.get("safety_immune"))
            merged = _dedupe_preserve_order(existing + [str(p) for p in sp_dirs])
            out_sec.setdefault("safety_immune", {})["paths"] = merged
            report.fields_migrated.append("self_protection.protected_dirs → safety_immune.paths")
        elif isinstance(sp_dirs, list) and sp_dirs and sp_disabled:
            # 用户原本关了 L5：不把 protected_dirs 升级为 v2 immune（避免静默
            # 加严）。报到 fields_dropped 让用户知情。
            report.fields_dropped.append(
                "self_protection.protected_dirs (v1 self_protection.enabled=false, 跳过升级)"
            )

        # ----- audit from self_protection.audit_* -----
        # audit 独立于 sp_enabled：v1 是 ``audit_to_file`` 单独控制
        if any(k in self_prot for k in ("audit_to_file", "audit_path")):
            audit_out = out_sec.setdefault("audit", {})
            if "audit_to_file" in self_prot and "enabled" not in audit_out:
                audit_out["enabled"] = bool(self_prot["audit_to_file"])
            if "audit_path" in self_prot and "log_path" not in audit_out:
                audit_out["log_path"] = str(self_prot["audit_path"])
            report.fields_migrated.append("self_protection.audit_* → audit.*")

        # ----- death_switch from self_protection.death_switch_* -----
        if any(k in self_prot for k in ("death_switch_threshold", "death_switch_total_multiplier")):
            ds_out = out_sec.setdefault("death_switch", {})
            if "death_switch_threshold" in self_prot and "threshold" not in ds_out:
                ds_out["threshold"] = int(self_prot["death_switch_threshold"])
            if "death_switch_total_multiplier" in self_prot and "total_multiplier" not in ds_out:
                ds_out["total_multiplier"] = int(self_prot["death_switch_total_multiplier"])
            report.fields_migrated.append("self_protection.death_switch_* → death_switch.*")

        # ----- self_protection.enabled = false → death_switch.enabled = false -----
        if sp_disabled:
            ds_out = out_sec.setdefault("death_switch", {})
            if "enabled" not in ds_out:
                ds_out["enabled"] = False
            report.fields_migrated.append(
                "self_protection.enabled=false → death_switch.enabled=false"
            )
        elif "enabled" in self_prot:
            # enabled=true 是 v1 默认且 v2 也默认 true，无需额外动作；只标记 drop
            # 让用户知道这个字段被重组了
            report.fields_dropped.append(
                "self_protection.enabled (v2 safety_immune/audit/death_switch 各自独立 enabled)"
            )

    # ----- shell_risk ← command_patterns -----
    cmd_pat = src_sec.get("command_patterns") or {}
    if cmd_pat and "shell_risk" not in out_sec:
        sr_out: dict[str, Any] = {}
        for k in (
            "enabled",
            "custom_critical",
            "custom_high",
            "excluded_patterns",
            "blocked_commands",
        ):
            if k in cmd_pat:
                sr_out[k] = deepcopy(cmd_pat[k])
        # v1 没 custom_medium —— 留空 list
        out_sec["shell_risk"] = sr_out
        report.fields_migrated.append("command_patterns.* → shell_risk.*")
    elif cmd_pat:
        report.conflicts.append("command_patterns vs shell_risk (kept v2)")

    # ----- sandbox.network.* 扁平化 -----
    sandbox = src_sec.get("sandbox")
    if isinstance(sandbox, dict) and "network" in sandbox:
        sb_out = out_sec.setdefault("sandbox", deepcopy(sandbox))
        net = sb_out.pop("network", None)
        if isinstance(net, dict):
            if "allow_in_sandbox" in net:
                sb_out["network_allow_in_sandbox"] = bool(net["allow_in_sandbox"])
            if "allowed_domains" in net:
                sb_out["network_allowed_domains"] = list(net["allowed_domains"] or [])
            report.fields_migrated.append("sandbox.network.* → sandbox.network_*（扁平化）")

    # ----- confirmation mode 别名 + auto_confirm 处理 -----
    # 上面 v2_or_shared_blocks 已经把 confirmation 整块复制过来；这里只处理
    # 两件事：(a) 把 v1 mode 别名 yolo/smart/cautious → trust/default/strict
    # 翻译；(b) auto_confirm=true 强制 mode=trust 并把 auto_confirm 字段移除
    # （因为 v2 schema 里没这个字段，留着会被 Pydantic ``extra='forbid'`` 抛错）。
    out_confirm = out_sec.get("confirmation")
    if isinstance(out_confirm, dict):
        old_mode = str(out_confirm.get("mode", "")).lower()
        auto_confirm = bool(out_confirm.get("auto_confirm", False))

        if auto_confirm:
            out_confirm["mode"] = "trust"
            report.fields_migrated.append(
                "confirmation.auto_confirm=true → confirmation.mode=trust"
            )
        elif old_mode in LEGACY_MODE_ALIASES:
            out_confirm["mode"] = LEGACY_MODE_ALIASES[old_mode]
            report.fields_migrated.append(
                f"confirmation.mode={old_mode} → {LEGACY_MODE_ALIASES[old_mode]}"
            )

        if "auto_confirm" in out_confirm:
            del out_confirm["auto_confirm"]
            report.fields_dropped.append(
                "confirmation.auto_confirm (v2 用 confirmation.mode=trust 替代)"
            )

        if "enabled" in out_confirm:
            # v1 confirmation.enabled —— v2 用 security.enabled 控制整体；
            # 如果用户显式设了 enabled=false，发 WARN 并保留 mode=trust 等价
            # 体验。但 v2 schema 没这个字段，必须删掉。
            del out_confirm["enabled"]
            report.fields_dropped.append("confirmation.enabled (v2 用 security.enabled 控制整体)")

    # ── enabled ↔ profile.current=off canonicalization ────────────────
    # 历史 YAML 里两个总开关并存（security.enabled、profile.current="off"）容易
    # 漂移。这里在迁移完成后做一次幂等归一：任何一边表达"全关"就同步到另一边。
    # 运行时 engine.preflight 早已"任一为关都短路"，本步只是让导出/UI 看上去
    # 不再自相矛盾。
    profile_block = out_sec.get("profile") if isinstance(out_sec.get("profile"), dict) else None
    explicit_off = bool(profile_block and profile_block.get("current") == "off")
    sec_disabled = out_sec.get("enabled") is False
    if explicit_off or sec_disabled:
        out_sec["enabled"] = False
        if profile_block is None:
            out_sec["profile"] = {"current": "off", "base": None}
            report.fields_migrated.append(
                "security.enabled=false → profile.current=off （canonicalize）"
            )
        else:
            if profile_block.get("current") != "off":
                profile_block["base"] = profile_block.get("current")
                profile_block["current"] = "off"
                report.fields_migrated.append(
                    "security.enabled=false → profile.current=off （canonicalize）"
                )

    return {"security": out_sec}, report


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _safe_paths(block: Any) -> list[str]:
    """从 safety_immune block 提取 paths list，None / 非 list / 缺失都返回 ``[]``。

    防止用户在 YAML 写 ``safety_immune: {paths: null}`` 时 ``list(None)`` 崩溃。
    """
    if not isinstance(block, dict):
        return []
    raw = block.get("paths")
    if not isinstance(raw, list):
        return []
    return list(raw)


__all__ = [
    "MigrationReport",
    "detect_schema_version",
    "migrate_v1_to_v2",
]
