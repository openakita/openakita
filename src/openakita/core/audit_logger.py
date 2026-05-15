"""
L5: 持久化审计日志

将安全策略判定记录追加写入 JSONL 文件，
确保即使进程崩溃也不丢失审计记录。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DEFAULT_AUDIT_PATH = "data/audit/openakita_audit.jsonl"


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "password",
        "secret",
        "token",
        "credential",
        "auth",
        "access_key",
        "secret_key",
        "private_key",
        "apikey",
        "passwd",
    }
)


def _mask_sensitive(text: str, max_len: int = 200) -> str:
    """对 params_preview 中可能包含的敏感信息进行脱敏。"""
    if not text:
        return text
    masked = text[:max_len]
    for key in _SENSITIVE_KEYS:
        if key in masked.lower():
            import re

            masked = re.sub(
                rf"({key}['\"]?\s*[:=]\s*['\"]?)([^'\"\\s,}}]+)",
                r"\1***MASKED***",
                masked,
                flags=re.IGNORECASE,
            )
    return masked


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if key_text.lower() in _SENSITIVE_KEYS:
            safe[key_text] = "***MASKED***"
        elif "path" in key_text.lower() and isinstance(value, str):
            safe[key_text] = value[-120:]
        else:
            safe[key_text] = value
    return safe


class AuditLogger:
    """Append-only JSONL audit logger for policy decisions.

    C16 Phase C: when ``include_chain=True`` (the default), each row gains
    ``prev_hash`` + ``row_hash`` via :class:`policy_v2.audit_chain.ChainedJsonlWriter`,
    making post-hoc edits detectable. ``safety_immune`` is also promoted
    to a top-level boolean (read from ``metadata.safety_immune_match``)
    while keeping the original nested ``meta.safety_immune_match`` copy
    so existing readers do not break.

    Operators who explicitly disable chain (``audit.include_chain=false``)
    keep the pre-C16 raw-append behaviour.
    """

    def __init__(
        self,
        path: str = DEFAULT_AUDIT_PATH,
        enabled: bool = True,
        include_chain: bool = True,
    ) -> None:
        self._enabled = enabled
        self._path = Path(path or DEFAULT_AUDIT_PATH)
        self._include_chain = include_chain
        if self._enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        tool_name: str,
        decision: str,
        reason: str,
        policy: str = "",
        params_preview: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.log_event(
            "policy_decision",
            {
                "tool": tool_name,
                "decision": decision,
                "reason": reason,
                "policy": policy,
                "params": _mask_sensitive(params_preview),
                "meta": _safe_metadata(metadata or {}),
            },
        )

    def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        if not self._enabled:
            return
        entry = {
            "ts": time.time(),
            "event": event_type,
        }
        entry.update(data)
        metadata = entry.get("meta")
        if isinstance(metadata, dict):
            # C16: promote safety_immune to top-level so SecurityView /
            # verifier can filter without parsing meta. Nested copy stays
            # for backward compat (any external consumer still sees it).
            si = metadata.get("safety_immune_match")
            if si is not None:
                entry["safety_immune"] = bool(si)

        if self._include_chain:
            # C22 P3-2: prefer async batch writer if started for this
            # path. Falls through to sync :class:`ChainedJsonlWriter`
            # when no writer is running (CLI / pre-init / tests) — same
            # correctness contract, just per-call filelock overhead.
            try:
                from .policy_v2.audit_writer import get_async_audit_writer

                async_w = get_async_audit_writer(str(self._path))
                if async_w is not None and async_w.is_running():
                    async_w.enqueue(entry)
                    return
            except Exception as e:
                logger.debug(
                    "[Audit] async writer unavailable, using sync chain: %s", e
                )
            try:
                from .policy_v2.audit_chain import get_writer

                get_writer(self._path).append(entry)
                return
            except Exception as e:
                logger.warning(
                    "[Audit] Chain write failed, falling back to raw append: %s", e
                )
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[Audit] Failed to write audit log: {e}")

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        """Read the last *n* entries."""
        if not self._enabled:
            return []
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").strip().splitlines()
            return [json.loads(line) for line in lines[-n:]]
        except Exception:
            return []


_global_audit: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Return the lazily-constructed global AuditLogger.

    C8b-2: 改读 ``policy_v2.AuditConfig``。v2 把 v1 ``self_protection.audit_*``
    拆出独立 ``audit`` section，字段名从 ``audit_path``/``audit_to_file`` 变
    为 ``log_path``/``enabled``。``loader.migrate_v1_to_v2`` 已自动转换旧
    YAML，所以本函数读 v2 配置即可拿到最新值。

    fail-safe：v2 加载异常 → 退化到 ``AuditLogger()`` 默认（与 v1 同行为）。
    """
    global _global_audit
    if _global_audit is None:
        try:
            from .policy_v2.global_engine import get_config_v2

            cfg = get_config_v2().audit
            _global_audit = AuditLogger(
                path=cfg.log_path or DEFAULT_AUDIT_PATH,
                enabled=cfg.enabled,
                include_chain=getattr(cfg, "include_chain", True),
            )
        except Exception:
            _global_audit = AuditLogger()
    return _global_audit


def reset_audit_logger() -> None:
    """Force the next audit write/read to use the latest policy config."""
    global _global_audit
    _global_audit = None
