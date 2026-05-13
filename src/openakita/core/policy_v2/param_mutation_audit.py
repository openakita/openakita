"""C10: 插件 ``on_before_tool_use`` 修改 ``tool_input`` 的强制审计 + 闸门。

设计目标
========

R2-12 要求：插件如果在 ``on_before_tool_use`` 钩子里改了工具参数（典型场景：
注入默认 LLM model、改写路径、加入 trace headers 等），必须**满足两个条件**：

1. 在 ``plugin.json`` 的 ``mutates_params: [tool_name, ...]`` 显式声明
   想要修改的工具——属于 plugin manifest 的 capability 声明。
2. 每次实际修改都写入 ``data/audit/plugin_param_modifications.jsonl``，
   包含 (timestamp, plugin_id 候选, tool_name, before/after diff)。

未声明就改 → ``tool_input`` 被还原为 hook 派发前的 deep-copy 快照，并写入
**rejected** 类型的 audit。这样：

- 攻击插件（伪装成正常 hook 改路径绕过 PolicyEngineV2 路径检查）会被还原。
- 正常插件 author 漏写 ``mutates_params`` 会立刻看到自己的修改"没生效 + 有
  rejected audit"，定位非常快。

为什么 attribution 是"候选 plugin_id 列表"而非单个
=================================================

``HookRegistry.dispatch`` 使用 ``asyncio.gather`` **并行**派发所有回调，多个
插件回调共享同一个 ``tool_input`` 字典引用——任意一个 callback 修改后，
diff 阶段已经无法 reliably 区分"是 A 改的还是 B 改的"。

实际场景里 ``on_before_tool_use`` 几乎总是配合 ``match=lambda tool_name: ...``
predicate 使用，同一工具同一时刻只有 1 个候选 callback，所以 attribution
列表通常只有 1 项。当列表 >1 项时审计仍然记录全部 ID（reviewer 自己分辨），
而 allow / revert 决策按"任一候选 plugin 被授权 → allow"的宽松规则——
等价于把 ``mutates_params`` 视为 plugin scope 的 capability。

线程 / 进程 / 重入安全
========================

- 文件追加用 ``threading.Lock``，多协程并发 dispatch 时不会出现 jsonl 乱码。
- 跨进程并发（多 worker）由文件系统 append 原子性兜底——单行 jsonl 远小于
  PIPE_BUF（4096 字节），POSIX 保证不交错；Windows 下 RotatingFileHandler
  级别的并发被避免（每个 ParamMutationAuditor 单实例 lock）。
- 重入：``snapshot/diff`` 都是纯函数，不持有状态。
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认审计文件位置；测试 fixture 通过构造函数注入临时目录覆盖。
DEFAULT_AUDIT_DIR = Path("data/audit")
DEFAULT_AUDIT_FILENAME = "plugin_param_modifications.jsonl"


@dataclass(frozen=True)
class ParamDiff:
    """单条 before→after 字段差异。"""

    path: str
    before: Any
    after: Any
    op: str  # "add" | "remove" | "modify"


@dataclass
class ParamAuditOutcome:
    """audit 结果：是否应该保留 hook 的修改。"""

    diffs: list[ParamDiff]
    allowed: bool
    candidate_plugin_ids: list[str]
    revert_reason: str = ""
    snapshot_failed: bool = False  # C10 二轮：标识快照不可信

    @property
    def has_changes(self) -> bool:
        return bool(self.diffs)


class _SnapshotFailedSentinel:
    """C10 二轮：deepcopy + json roundtrip 全部失败时返回的 sentinel。

    评估时它代替 ``before`` 参与 diff——和真实 ``after`` 永远不相等，所以
    一定产生 1 条整体 ``modify`` diff，触发授权检查与显式 audit 记录。
    避免"snapshot 返回原 ref → before==after → 静默放过 plugin 任意修改"
    的隐蔽 bypass 路径。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅日志
        return "<SNAPSHOT_FAILED>"


SNAPSHOT_FAILED: _SnapshotFailedSentinel = _SnapshotFailedSentinel()


def _diff_recursive(
    before: Any, after: Any, prefix: str = ""
) -> list[ParamDiff]:
    """Stable recursive diff 算法。

    - dict: 按 sorted(keys) 遍历，区分 add / remove / modify
    - list: 按位置比较；长度不同时记录整体替换（避免 O(n²) LCS）
    - 其他: ``!=`` 比较

    输出 ``path`` 用 ``a.b[2].c`` 格式便于在 audit 文件 grep。
    """
    if isinstance(before, dict) and isinstance(after, dict):
        diffs: list[ParamDiff] = []
        all_keys = sorted(set(before.keys()) | set(after.keys()))
        for k in all_keys:
            sub_prefix = f"{prefix}.{k}" if prefix else str(k)
            if k not in before:
                diffs.append(ParamDiff(sub_prefix, None, after[k], "add"))
            elif k not in after:
                diffs.append(ParamDiff(sub_prefix, before[k], None, "remove"))
            else:
                diffs.extend(_diff_recursive(before[k], after[k], sub_prefix))
        return diffs

    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [ParamDiff(prefix, before, after, "modify")]
        diffs = []
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            diffs.extend(_diff_recursive(b, a, f"{prefix}[{i}]"))
        return diffs

    if before != after:
        return [ParamDiff(prefix or "<root>", before, after, "modify")]
    return []


class ParamMutationAuditor:
    """负责 snapshot / diff / 写 jsonl + revert 决策。

    单实例可以跨多次调用复用：``audit_dir`` 解析在构造时一次完成，
    后续 append 走 ``threading.Lock``。
    """

    def __init__(self, audit_dir: Path | None = None) -> None:
        self._audit_dir = Path(audit_dir) if audit_dir is not None else DEFAULT_AUDIT_DIR
        self._audit_path = self._audit_dir / DEFAULT_AUDIT_FILENAME
        self._lock = threading.Lock()

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    @staticmethod
    def snapshot(tool_input: Any) -> Any:
        """Deep-copy 快照，作为 diff 基线。

        必须在 dispatch hook **之前**调用——hook 可能就地修改 ``tool_input``。
        非 dict 输入（罕见，但 LLM 偶尔会发字符串）原样返回，差异计算阶段
        会按 ``!=`` 比较。

        C10 二轮加固：deepcopy 失败时不再静默返回原 ref（会让 diff 永远为空，
        plugin 任意修改静默放过）。降级链：

        1. ``copy.deepcopy`` —— 99% 情况
        2. ``json.loads(json.dumps(default=str))`` —— 兜底处理含
           thread.Lock / 文件句柄等不可 deepcopy 但 JSON 可序列化的对象
        3. 都失败 → 返回 ``SNAPSHOT_FAILED`` sentinel；evaluate 阶段
           会把它当作"和任何 after 都不等"，强制走授权检查 + 写 audit
        """
        try:
            return copy.deepcopy(tool_input)
        except Exception as deepcopy_exc:
            try:
                return json.loads(json.dumps(tool_input, default=str))
            except Exception as json_exc:
                logger.error(
                    "ParamMutationAuditor.snapshot failed: deepcopy=%s, "
                    "json fallback=%s; using sentinel — any mutation will be "
                    "audited as 'snapshot_failed' and treated as unauthorized",
                    deepcopy_exc,
                    json_exc,
                )
                return SNAPSHOT_FAILED

    def evaluate(
        self,
        *,
        tool_name: str,
        before: Any,
        after: Any,
        candidate_plugin_ids: list[str],
        is_plugin_authorized: Any,
    ) -> ParamAuditOutcome:
        """生成 ``ParamAuditOutcome``：列出 diff、决定是否允许保留。

        ``is_plugin_authorized``: ``Callable[[plugin_id, tool_name], bool]``。
        典型实现 = ``PluginManager.plugin_allows_param_mutation``，但也接受
        测试用 lambda——所以这里只声明 callable 而不强类型耦合。

        C10 二轮：若 ``before`` 是 :data:`SNAPSHOT_FAILED` sentinel，diff 阶段
        必然产生整体 ``modify`` 一条，授权评估走 deny-by-default——快照不可
        信时绝不允许"沉默放行"。
        """
        snapshot_failed = isinstance(before, _SnapshotFailedSentinel)
        diffs = _diff_recursive(before, after)
        if not diffs and not snapshot_failed:
            return ParamAuditOutcome(
                diffs=[],
                allowed=True,
                candidate_plugin_ids=candidate_plugin_ids,
            )

        if snapshot_failed:
            return ParamAuditOutcome(
                diffs=diffs or [ParamDiff("<root>", before, after, "modify")],
                allowed=False,
                candidate_plugin_ids=candidate_plugin_ids,
                revert_reason="snapshot failed; cannot verify mutation safely",
                snapshot_failed=True,
            )

        allowed = False
        if candidate_plugin_ids:
            for pid in candidate_plugin_ids:
                try:
                    if is_plugin_authorized(pid, tool_name):
                        allowed = True
                        break
                except Exception as exc:
                    logger.debug(
                        "is_plugin_authorized(%s, %s) raised %s; treating as deny",
                        pid,
                        tool_name,
                        exc,
                    )

        return ParamAuditOutcome(
            diffs=diffs,
            allowed=allowed,
            candidate_plugin_ids=candidate_plugin_ids,
            revert_reason=(
                ""
                if allowed
                else "no candidate plugin has tool in manifest.mutates_params"
            ),
        )

    def write(
        self,
        *,
        tool_name: str,
        outcome: ParamAuditOutcome,
        before: Any,
        after: Any,
    ) -> None:
        """把审计记录追加到 jsonl 文件。

        发生异常（磁盘满、权限错）只 WARN log 不抛——audit 永远不应该让
        tool 调用失败。
        """
        if not outcome.has_changes:
            return
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "tool_name": tool_name,
            "candidate_plugin_ids": outcome.candidate_plugin_ids,
            "allowed": outcome.allowed,
            "revert_reason": outcome.revert_reason,
            "snapshot_failed": outcome.snapshot_failed,
            "before": before,
            "after": after,
            "diffs": [
                {
                    "path": d.path,
                    "op": d.op,
                    "before": d.before,
                    "after": d.after,
                }
                for d in outcome.diffs
            ],
        }
        try:
            with self._lock:
                self._audit_dir.mkdir(parents=True, exist_ok=True)
                with self._audit_path.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(record, ensure_ascii=False, default=str))
                    fp.write("\n")
        except Exception as exc:
            logger.warning(
                "ParamMutationAuditor.write failed (%s); audit record dropped: %r",
                exc,
                record,
            )


_default_auditor: ParamMutationAuditor | None = None
_default_lock = threading.Lock()


def get_default_auditor() -> ParamMutationAuditor:
    """获取进程级默认 ``ParamMutationAuditor`` 单例。

    懒加载 + 双检锁；测试 fixture 应通过 :func:`set_default_auditor`
    或直接构造新实例覆盖，避免污染生产 audit 文件。
    """
    global _default_auditor
    if _default_auditor is not None:
        return _default_auditor
    with _default_lock:
        if _default_auditor is None:
            _default_auditor = ParamMutationAuditor()
    return _default_auditor


def set_default_auditor(auditor: ParamMutationAuditor | None) -> None:
    """注入测试用 auditor / 重置单例。"""
    global _default_auditor
    with _default_lock:
        _default_auditor = auditor


__all__ = [
    "ParamDiff",
    "ParamAuditOutcome",
    "ParamMutationAuditor",
    "get_default_auditor",
    "set_default_auditor",
    "DEFAULT_AUDIT_DIR",
    "DEFAULT_AUDIT_FILENAME",
]
