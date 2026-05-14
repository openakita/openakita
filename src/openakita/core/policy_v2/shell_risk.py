"""Shell command risk classification.

Patterns 迁移自 v1 ``core/policy.py``（docs §5.1 标记的私有常量
_CRITICAL_SHELL_PATTERNS / _HIGH_RISK_SHELL_PATTERNS / _MEDIUM_RISK_SHELL_PATTERNS）。
v2 通过 ``ApprovalClassifier._refine_with_params`` 调用本模块对 ``run_shell`` /
``run_powershell`` / ``opencli_run`` / ``cli_anything_run`` 的 command 字符串做
二次分类，把基础 ``EXEC_CAPABLE`` 细化到对应 ApprovalClass。

C8 删除 v1 ``policy.py`` 主体时，v1 patterns 从这里 re-export 给薄壳兼容。

设计要点：
- pattern 用 ``re.search`` 而非 ``re.match`` —— shell 命令前可能有 sudo/nice/env 等前缀
- 大小写不敏感（Windows cmdlet 风格，``Remove-Item`` vs ``remove-item``）
- DEFAULT_BLOCKED_COMMANDS 是"完全独立的 token 名"，单独走 token 检测（不是 regex）
- ``classify_shell_command`` 返回 ShellRiskLevel；UNKNOWN/空命令默认 LOW（避免空字符串被误判）
"""

from __future__ import annotations

import re
import shlex
from enum import StrEnum
from functools import lru_cache

# ---------------------------------------------------------------------------
# Critical: 不可恢复 + 全系统破坏（rm -rf / 等）
# ---------------------------------------------------------------------------
CRITICAL_SHELL_PATTERNS: list[str] = [
    # Universal
    r"dd\s+if=",
    r"mkfs\.",
    r":\(\)\{\s*:\|:&\s*\};:",  # fork bomb
    # Windows
    r"format\s+[a-zA-Z]:",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"cipher\s+/w:",
    # Linux / macOS
    r"rm\s+-rf\s+/\s",
    r"rm\s+-rf\s+/\*",
    r"rm\s+-rf\s+/$",
    r"mv\s+/\s",
    r"chmod\s+-R\s+000\s+/",
    r"chown\s+-R\s+.*\s+/\s",
    r">\s*/dev/sda",
]

# ---------------------------------------------------------------------------
# High: 大范围删/改/卸载
# ---------------------------------------------------------------------------
HIGH_RISK_SHELL_PATTERNS: list[str] = [
    # Windows cmd + PowerShell
    r"Remove-Item\s+.*-Recurse",
    r"Remove-Item\s+.*-Force",
    r"del\s+/[sS]",
    r"rd\s+/[sS]",
    r"rmdir\s+/[sS]\s*/[qQ]",
    r"(?:copy|move|del|rd|rmdir|echo|Set-Content|Add-Content|New-Item).*"
    r"(?:System32|Windows|Program Files)",
    r"Get-ChildItem.*\|\s*Remove-Item",
    r"Clear-RecycleBin",
    r"wmic\s+product.*uninstall",
    r"msiexec\s+/[xX]",
    r"winget\s+uninstall",
    r"choco\s+uninstall",
    # Linux / macOS
    r"rm\s+-rf\s+",
    r"rm\s+-r\s+",
    r"find\s+.*-delete",
    r"find\s+.*-exec\s+rm",
    r"xargs\s+rm",
    r"chmod\s+-R\s+",
    r"chown\s+-R\s+",
    r"apt\s+(remove|purge)",
    r"yum\s+(remove|erase)",
    r"brew\s+uninstall",
    r"dpkg\s+--purge",
    r"launchctl\s+unload",
    r"systemctl\s+(stop|disable|mask)",
    r"crontab\s+-r",
    # Cross-platform
    r"shutil\.rmtree",
    r"os\.remove\(|os\.unlink\(",
    r"pip\s+uninstall",
    r"npm\s+uninstall\s+-g",
    r"curl\s+.*\|\s*(bash|sh)",
    r"wget\s+.*\|\s*(bash|sh)",
]

# ---------------------------------------------------------------------------
# Medium: 需要确认但不必沙箱
# ---------------------------------------------------------------------------
MEDIUM_RISK_SHELL_PATTERNS: list[str] = [
    r"Remove-Item\b",
    r"Clear-Content\b",
    r"Clear-Item\b",
    r"setx?\s+",
    r"export\s+\w+=",
    r"npm\s+install\s+-g",
    r"pip\s+install\s+",
    r"choco\s+install",
    r"winget\s+install",
    r"apt\s+install",
    r"brew\s+install",
    r"ssh\s+",
    r"scp\s+",
    r"rsync\s+",
    r"git\s+push",
    r"git\s+clone",
    r"docker\s+(run|exec|build)",
    r"kill\s+",
    r"pkill\s+",
    r"nohup\s+",
]

# ---------------------------------------------------------------------------
# Default blocked tokens (硬阻断；按命令名独立 token 匹配，非 regex)
# ---------------------------------------------------------------------------
DEFAULT_BLOCKED_COMMANDS: list[str] = [
    "reg",
    "regedit",
    "netsh",
    "schtasks",
    "sc",
    "wmic",
    "bcdedit",
    "shutdown",
    "taskkill",
]


class ShellRiskLevel(StrEnum):
    """Shell command 风险分级。

    - BLOCKED: token in DEFAULT_BLOCKED_COMMANDS → 无条件 DENY
    - CRITICAL: 命中 CRITICAL_SHELL_PATTERNS → 不可恢复破坏，DENY
    - HIGH: 命中 HIGH_RISK_SHELL_PATTERNS → CONFIRM + 强烈推荐 sandbox
    - MEDIUM: 命中 MEDIUM_RISK_SHELL_PATTERNS → CONFIRM
    - LOW: 普通命令（ls/cat/echo 等）→ 跟随矩阵决策
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKED = "blocked"


# Pre-compile patterns for perf (engine hot path)
_CRITICAL_RE = [re.compile(p, re.IGNORECASE) for p in CRITICAL_SHELL_PATTERNS]
_HIGH_RE = [re.compile(p, re.IGNORECASE) for p in HIGH_RISK_SHELL_PATTERNS]
_MEDIUM_RE = [re.compile(p, re.IGNORECASE) for p in MEDIUM_RISK_SHELL_PATTERNS]

# C22 P3-1: LRU cache size for shell classification.
# Plan §13.5.2 B mandated an LRU cache; benchmarks (test_c22_shell_risk_lru
# bench section) show first-call cost ~150-300µs, cached call ~0.5-1.5µs
# (>100× speedup) on a 50-pattern set. 512 entries cover typical agent
# session command diversity (CI / dev loops repeat the same commands 100s
# of times). Tune via OPENAKITA_SHELL_LRU_SIZE env if needed.
_SHELL_LRU_DEFAULT_SIZE = 512


def _shell_lru_size() -> int:
    """Resolve cache size at module load. Test fixtures may override via
    ``classify_shell_command.cache_clear`` + ``configure_cache_size``."""
    import os as _os

    raw = _os.environ.get("OPENAKITA_SHELL_LRU_SIZE", "").strip()
    if raw:
        try:
            v = int(raw)
            if 0 < v < 100_000:
                return v
        except ValueError:
            pass
    return _SHELL_LRU_DEFAULT_SIZE


def _coerce_tuple(value: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
    """Convert list/None into a hashable key fragment.

    ``functools.lru_cache`` requires hashable arguments — patterns come in as
    lists from ``ShellRiskConfig``. Returns:

    - ``None`` → ``None`` (means "use default behaviour" for this slot)
    - ``[]`` → ``()`` (empty tuple; the **opt-out** state: caller passed
      empty list to explicitly disable. Notably, ``blocked_tokens=[]``
      means "skip the blocked-token check entirely" — must NOT be folded
      into ``None``, which would re-enable the defaults.)
    - non-empty list → tuple copy

    The None-vs-empty distinction is load-bearing for ``blocked_tokens``;
    keep it consistent across all 5 slots so callers don't need to
    remember which slot has which semantics.
    """
    if value is None:
        return None
    if isinstance(value, tuple):
        return value
    return tuple(value)


@lru_cache(maxsize=_shell_lru_size())
def _classify_cached(
    command: str,
    extra_critical: tuple[str, ...] | None,
    extra_high: tuple[str, ...] | None,
    extra_medium: tuple[str, ...] | None,
    blocked_tokens: tuple[str, ...] | None,
    excluded_patterns: tuple[str, ...] | None,
) -> ShellRiskLevel:
    """Pure inner classifier — all args hashable so ``lru_cache`` can key on
    them. Public ``classify_shell_command`` is a thin wrapper that converts
    list args to tuples.

    Cache invalidation strategy: when ``ShellRiskConfig`` changes (POLICIES.yaml
    hot-reload), the tuples change → different cache keys → old entries
    naturally age out under LRU pressure. Hot-reload code does NOT need to
    explicitly clear the cache, though tests can call
    ``classify_shell_command.cache_clear`` for determinism.
    """
    text = command

    if excluded_patterns:
        for raw in excluded_patterns:
            try:
                if re.search(raw, text, flags=re.IGNORECASE):
                    return ShellRiskLevel.LOW
            except re.error:
                continue

    tokens = (
        list(blocked_tokens) if blocked_tokens is not None else DEFAULT_BLOCKED_COMMANDS
    )
    if tokens and _has_blocked_token(text, tokens):
        return ShellRiskLevel.BLOCKED

    if _matches_any(text, _CRITICAL_RE, extra_critical):
        return ShellRiskLevel.CRITICAL

    if _matches_any(text, _HIGH_RE, extra_high):
        return ShellRiskLevel.HIGH

    if _matches_any(text, _MEDIUM_RE, extra_medium):
        return ShellRiskLevel.MEDIUM

    return ShellRiskLevel.LOW


def classify_shell_command(
    command: str,
    *,
    extra_critical: list[str] | None = None,
    extra_high: list[str] | None = None,
    extra_medium: list[str] | None = None,
    blocked_tokens: list[str] | None = None,
    excluded_patterns: list[str] | None = None,
) -> ShellRiskLevel:
    """Classify a shell command string.

    C22 P3-1: LRU-cached on (command, extra_*, blocked_tokens,
    excluded_patterns). First call for a given combination scans patterns;
    subsequent calls return in O(1). Plan §13.5.2 B mandated this — engine
    hot path runs this per ``run_shell`` tool call, and a typical dev loop
    repeats the same 10–20 commands hundreds of times.

    Args:
        command: The full command line (may include sudo/nice/env prefixes).
        extra_critical / extra_high / extra_medium: User-provided custom patterns
            from POLICIES.yaml ``command_patterns.custom_critical/custom_high``
            (currently no custom_medium in v1, but accepted for symmetry).
        blocked_tokens: Override DEFAULT_BLOCKED_COMMANDS. None → use defaults.
        excluded_patterns: Patterns that, if matched, bypass classification
            (forced LOW). For user opt-out via POLICIES.yaml.

    Returns:
        ShellRiskLevel. Empty/whitespace-only command → LOW (avoid false alarm).

    Cache introspection (mainly tests):
        ``classify_shell_command.cache_info()`` returns
        ``functools._CacheInfo`` (hits, misses, maxsize, currsize).
        ``classify_shell_command.cache_clear()`` resets the cache.
    """
    if command is None or not command.strip():
        # Skip cache for empty input: hot path is non-empty commands and
        # caching "" → LOW just wastes one slot for a trivial check.
        return ShellRiskLevel.LOW
    # NOTE: 不 strip 工作字符串 —— 部分 CRITICAL pattern 依赖 trailing whitespace
    # （例如 ``chown\s+-R\s+.*\s+/\s`` 要求 / 后有空白才不误中 'rm -rf /tmp'）。
    # v1 policy.py 行为一致。
    return _classify_cached(
        command,
        _coerce_tuple(extra_critical),
        _coerce_tuple(extra_high),
        _coerce_tuple(extra_medium),
        _coerce_tuple(blocked_tokens),
        _coerce_tuple(excluded_patterns),
    )


# Expose ``_classify_cached``'s cache_info/cache_clear at the public entry
# so callers (tests, perf benches, ops scripts) can introspect without
# importing the underscore-prefixed helper.
classify_shell_command.cache_info = _classify_cached.cache_info  # type: ignore[attr-defined]
classify_shell_command.cache_clear = _classify_cached.cache_clear  # type: ignore[attr-defined]


def _matches_any(
    text: str,
    compiled: list[re.Pattern[str]],
    extra: tuple[str, ...] | list[str] | None,
) -> bool:
    for rx in compiled:
        if rx.search(text):
            return True
    if extra:
        for raw in extra:
            try:
                if re.search(raw, text, flags=re.IGNORECASE):
                    return True
            except re.error:
                # Bad user-provided pattern — skip silently (logger best-effort
                # at engine level; classify_shell_command is a pure function).
                continue
    return False


def _has_blocked_token(text: str, tokens: list[str]) -> bool:
    """Token-level match: split by shell quoting, compare lowercase exact.

    Why not regex: tokens like "sc" would catch "scp", "scope_x" etc. as substrings.
    Tokenized exact match avoids that. shlex handles quoting / escaping correctly.
    """
    try:
        # posix=False => Windows-friendly (don't strip backslashes)
        parts = shlex.split(text, posix=False)
    except ValueError:
        # Mismatched quotes; fallback to whitespace split
        parts = text.split()

    lowered = {p.strip("\"'").lower() for p in parts if p}
    return any(tok.lower() in lowered for tok in tokens)
