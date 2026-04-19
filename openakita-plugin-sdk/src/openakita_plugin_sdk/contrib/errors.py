"""ErrorCoach — translate raw exceptions/error codes into "problem + evidence + next step".

Inspired by:

- CutClaw ``ReviewerAgent`` ("reviewer as a coach")
- AnyGen FAQ tone: every error message has a *cause category* and *actionable suggestion*
- CapCut Web help-center 3-part error layout (Why does it happen / What to do / Tip)

Pattern library is intentionally a plain dict so plugins can extend at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import Any


@dataclass(frozen=True)
class RenderedError:
    """User-facing error rendered through a pattern.

    Attributes:
        pattern_id: Id of the matched ``ErrorPattern`` (or ``"_fallback"``).
        cause_category: One short Chinese label, e.g. ``"网络问题"`` / ``"配额耗尽"``.
        problem: Why does it happen — 1 sentence, user words, no jargon.
        evidence: What we observed — short fact (status code, file name, ...).
        next_step: What to do — concrete clickable / actionable action.
        tip: Optional "📍 Tip" — preventive hint, may be empty.
        severity: ``"info" | "warning" | "error"``.
        retryable: Whether the host UI should show a "重试" button.
    """

    pattern_id: str
    cause_category: str
    problem: str
    evidence: str
    next_step: str
    tip: str = ""
    severity: str = "error"
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "cause_category": self.cause_category,
            "problem": self.problem,
            "evidence": self.evidence,
            "next_step": self.next_step,
            "tip": self.tip,
            "severity": self.severity,
            "retryable": self.retryable,
        }


@dataclass
class ErrorPattern:
    """A single error pattern.

    Matching rules: any of (status_code, exc_type, message_regex) that is set
    must match.  ``priority`` resolves ties (higher wins).

    Templates may use ``{evidence}`` placeholder which receives the matched
    excerpt or status code.
    """

    pattern_id: str
    cause_category: str
    problem_template: str
    next_step_template: str
    tip: str = ""
    severity: str = "error"
    retryable: bool = False
    priority: int = 0

    status_codes: tuple[int, ...] = ()
    exc_types: tuple[str, ...] = ()
    message_regex: Pattern[str] | None = None


def _default_patterns() -> list[ErrorPattern]:
    return [
        ErrorPattern(
            pattern_id="api_key_missing",
            cause_category="API Key 未配置",
            problem_template="还没填供应商的 API Key，所以请求没法发出去。",
            next_step_template="点右上角【设置】→ 把 API Key 粘进去 → 重试一次。",
            tip="API Key 一次性配置，下次直接用。",
            severity="warning",
            retryable=False,
            priority=20,
            message_regex=re.compile(
                r"(api[\s_-]?key|access[\s_-]?key).*(missing|empty|not\s*set|未配置)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="api_key_invalid",
            cause_category="API Key 失效",
            problem_template="供应商说这个 Key 用不了 ({evidence})，可能填错或者过期。",
            next_step_template="去供应商控制台核对 Key → 重新粘贴 → 保存重试。",
            tip="多账号时，注意别把测试 Key 当生产用。",
            severity="error",
            retryable=False,
            priority=20,
            status_codes=(401, 403),
        ),
        ErrorPattern(
            pattern_id="rate_limit",
            cause_category="请求太频繁",
            problem_template="供应商限流了 ({evidence})，需要等一会儿。",
            next_step_template="不用动，10 秒后插件会自动重试；如果常态化，去后台升级套餐。",
            tip="可在【设置】里把 poll_interval 调大，少打几次。",
            severity="warning",
            retryable=True,
            priority=15,
            status_codes=(429,),
        ),
        ErrorPattern(
            pattern_id="server_error",
            cause_category="供应商故障",
            problem_template="供应商服务端报错 ({evidence})，不是你的问题。",
            next_step_template="点【重试】；如果连续失败 3 次，去供应商状态页查公告。",
            tip="可在【设置】里切换备用 provider。",
            severity="warning",
            retryable=True,
            priority=10,
            status_codes=(500, 502, 503, 504),
        ),
        ErrorPattern(
            pattern_id="content_moderation",
            cause_category="内容被风控",
            problem_template="内容审核没过 ({evidence})，重试也不会变。",
            next_step_template="改一下 Prompt 里的敏感词，或者上传不一样的素材。",
            tip="先用【意图验证】预检一遍 Prompt，能省一次失败。",
            severity="error",
            retryable=False,
            priority=18,
            status_codes=(400, 422),
            message_regex=re.compile(
                r"(content[\s_-]?policy|moderation|sensitive|敏感|违规|风控)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="quota_exhausted",
            cause_category="配额耗尽",
            problem_template="本月额度用完了 ({evidence})。",
            next_step_template="去供应商控制台续费 → 回来点【重试】。",
            tip="【设置】打开「成本预警」，到 80% 就提醒。",
            severity="error",
            retryable=False,
            priority=18,
            message_regex=re.compile(
                r"(quota|insufficient|余额|额度.*不足|over.*limit)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="network_timeout",
            cause_category="网络问题",
            problem_template="请求超时了 ({evidence})，可能你这边网不稳。",
            next_step_template="检查一下网络，或者切到代理；点【重试】。",
            tip="国内访问海外 API 建议配代理。",
            severity="warning",
            retryable=True,
            priority=12,
            exc_types=("TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectError"),
            message_regex=re.compile(r"(timeout|timed?\s*out|超时|connection.*reset)", re.IGNORECASE),
        ),
        ErrorPattern(
            pattern_id="ffmpeg_missing",
            cause_category="FFmpeg 未安装",
            problem_template="系统找不到 ffmpeg 命令 ({evidence})，没法做视频处理。",
            next_step_template="去 https://ffmpeg.org/download.html 下一份 → 加进 PATH → 重启应用。",
            tip="Windows 下推荐 winget install Gyan.FFmpeg。",
            severity="error",
            retryable=False,
            priority=20,
            message_regex=re.compile(
                r"(ffmpeg|ffprobe).*(not\s*found|missing|找不到)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="file_not_found",
            cause_category="文件丢失",
            problem_template="找不到这个文件 ({evidence})，可能被删了或路径变了。",
            next_step_template="重新上传一次素材；如果是历史任务，先在【素材库】里恢复。",
            tip="开启「自动备份」可以减少这种情况。",
            severity="error",
            retryable=False,
            priority=18,
            exc_types=("FileNotFoundError",),
        ),
        ErrorPattern(
            pattern_id="task_not_found",
            cause_category="任务不存在",
            problem_template="找不到这个任务 ID ({evidence})，可能已过期或被清理。",
            next_step_template="刷新任务列表，从最新一条重新开始。",
            severity="warning",
            retryable=False,
            priority=12,
            status_codes=(404,),
        ),
    ]


class ErrorCoach:
    """Translate raw errors into actionable user-facing messages.

    Usage::

        coach = ErrorCoach()  # built-in patterns
        rendered = coach.render(exc, status=503, raw_message="Bad Gateway")

        # Plugin-specific patterns: register more
        coach.register(ErrorPattern(
            pattern_id="seedance_image_unsupported",
            cause_category="模型不支持",
            ...
        ))
    """

    def __init__(self, patterns: list[ErrorPattern] | None = None) -> None:
        self._patterns: list[ErrorPattern] = list(patterns) if patterns else _default_patterns()

    def register(self, pattern: ErrorPattern) -> None:
        """Add or override a pattern (matched by ``pattern_id``)."""
        self._patterns = [p for p in self._patterns if p.pattern_id != pattern.pattern_id]
        self._patterns.append(pattern)

    def patterns(self) -> list[ErrorPattern]:
        """Return current pattern list (copy)."""
        return list(self._patterns)

    def render(
        self,
        exc: BaseException | None = None,
        *,
        status: int | None = None,
        raw_message: str | None = None,
        evidence: str | None = None,
    ) -> RenderedError:
        """Match the best pattern and render a user-facing error.

        At least one of ``exc`` / ``status`` / ``raw_message`` must be given.
        """
        message = raw_message or (str(exc) if exc else "")
        exc_name = type(exc).__name__ if exc else ""
        ev = evidence or self._auto_evidence(status, exc_name, message)

        best: ErrorPattern | None = None
        best_score = -1
        for pat in self._patterns:
            score = self._match_score(pat, status, exc_name, message)
            if score < 0:
                continue
            score += pat.priority
            if score > best_score:
                best, best_score = pat, score

        if best is None:
            return RenderedError(
                pattern_id="_fallback",
                cause_category="未知错误",
                problem=f"出了个我们没见过的错 ({ev or 'unknown'})。",
                evidence=ev,
                next_step="点【重试】；如果反复出现，把日志发给我们 (设置 → 反馈)。",
                tip="报错日志在 data/plugins/<id>/logs/ 下。",
                severity="error",
                retryable=True,
            )

        return RenderedError(
            pattern_id=best.pattern_id,
            cause_category=best.cause_category,
            problem=self._fmt(best.problem_template, ev),
            evidence=ev,
            next_step=self._fmt(best.next_step_template, ev),
            tip=best.tip,
            severity=best.severity,
            retryable=best.retryable,
        )

    @staticmethod
    def _fmt(template: str, evidence: str) -> str:
        try:
            return template.format(evidence=evidence or "无详情")
        except (KeyError, IndexError):
            return template

    @staticmethod
    def _auto_evidence(status: int | None, exc_name: str, message: str) -> str:
        bits: list[str] = []
        if status is not None:
            bits.append(f"HTTP {status}")
        if exc_name and exc_name not in {"Exception", "BaseException"}:
            bits.append(exc_name)
        if message:
            short = message.strip().splitlines()[0][:120]
            if short:
                bits.append(short)
        return " · ".join(bits)

    @staticmethod
    def _match_score(
        pat: ErrorPattern,
        status: int | None,
        exc_name: str,
        message: str,
    ) -> int:
        criteria_total = 0
        criteria_matched = 0

        if pat.status_codes:
            criteria_total += 1
            if status is not None and status in pat.status_codes:
                criteria_matched += 1
        if pat.exc_types:
            criteria_total += 1
            if exc_name in pat.exc_types:
                criteria_matched += 1
        if pat.message_regex is not None:
            criteria_total += 1
            if message and pat.message_regex.search(message):
                criteria_matched += 1

        if criteria_total == 0:
            return -1
        if criteria_matched == 0:
            return -1
        return criteria_matched
