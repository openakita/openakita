"""
任务监控器

功能:
- 跟踪任务执行时间
- 记录迭代次数和工具调用
- 超时自动切换模型
- 任务完成后复盘分析
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


# 默认配置
DEFAULT_TIMEOUT_SECONDS = 600  # 无进展超时阈值（秒）
DEFAULT_RETROSPECT_THRESHOLD = 60  # 复盘阈值（秒）
DEFAULT_RETRY_BEFORE_SWITCH = 3  # 切换模型前重试次数
DEFAULT_RETRY_INTERVAL = 5  # 重试间隔（秒）


class TaskPhase(Enum):
    """任务阶段"""

    STARTED = "started"
    TOOL_CALLING = "tool_calling"
    WAITING_LLM = "waiting_llm"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass
class ToolCallRecord:
    """工具调用记录"""

    name: str
    input_summary: str
    output_summary: str
    duration_ms: int
    success: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class IterationRecord:
    """迭代记录"""

    iteration: int
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    llm_response_preview: str = ""
    duration_ms: int = 0
    model_used: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TaskMetrics:
    """任务指标"""

    task_id: str
    description: str
    session_id: str | None = None

    # 时间
    start_time: float = 0
    end_time: float = 0
    total_duration_seconds: float = 0

    # 迭代
    total_iterations: int = 0
    iterations: list[IterationRecord] = field(default_factory=list)

    # 模型
    initial_model: str = ""
    final_model: str = ""
    model_switched: bool = False
    switch_reason: str = ""

    # 重试和切换
    retry_count: int = 0  # 切换前重试次数
    context_reset_on_switch: bool = False  # 切换时是否重置上下文

    # 结果
    success: bool = False
    error: str | None = None
    final_response: str = ""

    # 复盘
    retrospect_needed: bool = False
    retrospect_result: str | None = None

    def to_summary(self) -> str:
        """生成摘要"""
        lines = [
            f"任务: {self.description}",
            f"耗时: {self.total_duration_seconds:.1f}秒",
            f"迭代: {self.total_iterations}次",
            f"结果: {'成功' if self.success else '失败'}",
        ]
        if self.model_switched:
            lines.append(f"模型切换: {self.initial_model} → {self.final_model}")
            lines.append(f"切换前重试: {self.retry_count}次")
            if self.context_reset_on_switch:
                lines.append("上下文已重置")
        if self.error:
            lines.append(f"错误: {self.error}")
        return "\n".join(lines)


class TaskMonitor:
    """
    任务监控器

    用于跟踪任务执行状态、时间、迭代等信息，
    并在超时时触发模型切换。
    """

    def __init__(
        self,
        task_id: str,
        description: str,
        session_id: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        hard_timeout_seconds: int = 0,
        retrospect_threshold: int = DEFAULT_RETROSPECT_THRESHOLD,
        fallback_model: str = "",
        on_timeout: Callable[["TaskMonitor"], None] | None = None,
        retry_before_switch: int = DEFAULT_RETRY_BEFORE_SWITCH,
    ):
        """
        初始化任务监控器

        Args:
            task_id: 任务 ID
            description: 任务描述
            session_id: 会话 ID
            timeout_seconds: 超时阈值（秒）
            retrospect_threshold: 复盘阈值（秒）
            fallback_model: 超时后切换的备用模型
            on_timeout: 超时回调
            retry_before_switch: 切换模型前的重试次数
        """
        self.metrics = TaskMetrics(
            task_id=task_id,
            description=description,
            session_id=session_id,
        )

        self.timeout_seconds = timeout_seconds
        self.hard_timeout_seconds = hard_timeout_seconds
        self.retrospect_threshold = retrospect_threshold
        self.fallback_model = fallback_model
        self.on_timeout = on_timeout
        self.retry_before_switch = retry_before_switch

        self._phase = TaskPhase.STARTED
        self._current_iteration: IterationRecord | None = None
        self._current_tool_start: float = 0
        self._timeout_triggered = False

        # 两个独立的重试计数器：
        # 1. LLM 错误重试计数（LLM 调用失败时增加，成功时重置）
        self._retry_count = 0
        self._last_error: str | None = None

        # 2. 超时重试计数（超时检测时增加，不受 LLM 成功影响）
        self._timeout_retry_count = 0
        self._last_progress_time: float = 0.0

    def start(self, model: str) -> None:
        """开始任务"""
        self.metrics.start_time = time.time()
        self._last_progress_time = self.metrics.start_time
        self.metrics.initial_model = model
        self.metrics.final_model = model
        self._phase = TaskPhase.STARTED
        logger.info(f"[TaskMonitor] Task started: {self.metrics.task_id}")

    def begin_iteration(self, iteration: int, model: str) -> None:
        """开始新迭代"""
        self._current_iteration = IterationRecord(
            iteration=iteration,
            model_used=model,
        )
        self._phase = TaskPhase.WAITING_LLM

        # 每次进入新迭代视为有进展（至少系统仍在推进循环）
        self._touch_progress()

        # 检查是否“无进展超时”（避免长任务被硬切）
        if self.progress_idle_seconds > self.timeout_seconds and not self._timeout_triggered:
            self._handle_timeout()

        # 可选硬超时兜底（默认关闭）
        if (
            self.hard_timeout_seconds
            and self.hard_timeout_seconds > 0
            and self.elapsed_seconds > self.hard_timeout_seconds
            and not self._timeout_triggered
        ):
            self._handle_timeout()

    def end_iteration(self, llm_response_preview: str = "") -> None:
        """结束迭代"""
        if self._current_iteration:
            self._touch_progress()
            self._current_iteration.llm_response_preview = llm_response_preview
            self._current_iteration.duration_ms = int(
                (time.time() - self.metrics.start_time) * 1000
            )
            self.metrics.iterations.append(self._current_iteration)
            self.metrics.total_iterations += 1
            self._current_iteration = None

    def begin_tool_call(self, tool_name: str, tool_input: dict) -> None:
        """开始工具调用"""
        self._phase = TaskPhase.TOOL_CALLING
        self._current_tool_start = time.time()
        self._current_tool_name = tool_name
        self._current_tool_input = str(tool_input)
        self._touch_progress()

    def end_tool_call(self, result: str, success: bool = True) -> None:
        """结束工具调用"""
        if self._current_iteration and hasattr(self, "_current_tool_name"):
            duration_ms = int((time.time() - self._current_tool_start) * 1000)
            record = ToolCallRecord(
                name=self._current_tool_name,
                input_summary=self._current_tool_input,
                output_summary=result if result else "",
                duration_ms=duration_ms,
                success=success,
            )
            self._current_iteration.tool_calls.append(record)
        self._phase = TaskPhase.WAITING_LLM
        self._touch_progress()

    def record_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        result: str,
        *,
        success: bool,
        duration_ms: int,
    ) -> None:
        """记录一次工具调用（并行安全）

        说明：
        - begin_tool_call/end_tool_call 依赖“同一时间仅一个工具调用”的隐含前提
        - 当一个模型在同一轮返回多个 tool_use（并行工具调用）时，Agent 可能并发执行工具
        - 此方法允许调用方自行测量耗时并直接写入当前 iteration，避免共享状态竞争
        """
        if not self._current_iteration:
            return
        self._touch_progress()
        record = ToolCallRecord(
            name=tool_name,
            input_summary=str(tool_input),
            output_summary=result if result else "",
            duration_ms=int(duration_ms),
            success=success,
        )
        self._current_iteration.tool_calls.append(record)

    def complete(self, success: bool, response: str = "", error: str = "") -> TaskMetrics:
        """完成任务"""
        self.metrics.end_time = time.time()
        self.metrics.total_duration_seconds = self.metrics.end_time - self.metrics.start_time
        self.metrics.success = success
        self.metrics.final_response = response
        self.metrics.error = error if not success else None
        self._phase = TaskPhase.COMPLETED if success else TaskPhase.FAILED

        # 判断是否需要复盘
        self.metrics.retrospect_needed = (
            self.metrics.total_duration_seconds > self.retrospect_threshold
        )

        logger.info(
            f"[TaskMonitor] Task completed: {self.metrics.task_id}, "
            f"duration={self.metrics.total_duration_seconds:.1f}s, "
            f"iterations={self.metrics.total_iterations}, "
            f"success={success}"
        )

        return self.metrics

    def switch_model(self, new_model: str, reason: str, reset_context: bool = True) -> None:
        """
        切换模型

        Args:
            new_model: 新模型名称
            reason: 切换原因
            reset_context: 是否需要重置上下文（默认 True）
        """
        old_model = self.metrics.final_model
        self.metrics.final_model = new_model
        self.metrics.model_switched = True
        self.metrics.switch_reason = reason
        self.metrics.context_reset_on_switch = reset_context
        self.metrics.retry_count = self._retry_count
        logger.warning(
            f"[TaskMonitor] Model switched: {old_model} → {new_model}, "
            f"reason: {reason}, context_reset: {reset_context}, retries: {self._retry_count}"
        )

    def record_error(self, error: str) -> bool:
        """
        记录错误并判断是否应该重试

        Args:
            error: 错误信息

        Returns:
            True 如果应该重试，False 如果应该切换模型
        """
        self._last_error = error
        self._retry_count += 1

        logger.info(
            f"[TaskMonitor] Error recorded (retry {self._retry_count}/{self.retry_before_switch}): {error}"
        )

        if self._retry_count < self.retry_before_switch:
            return True  # 继续重试
        else:
            return False  # 应该切换模型

    def reset_retry_count(self) -> None:
        """重置 LLM 错误重试计数（在 LLM 调用成功后调用）

        注意：这只重置 LLM 错误重试计数，不影响超时重试计数。
        超时重试是独立的，不会因为 LLM 调用成功而重置。
        """
        self._retry_count = 0
        self._last_error = None

    @property
    def retry_count(self) -> int:
        """当前 LLM 错误重试次数"""
        return self._retry_count

    @property
    def timeout_retry_count(self) -> int:
        """当前超时重试次数（独立计数器）"""
        return self._timeout_retry_count

    @property
    def should_retry(self) -> bool:
        """是否应该重试 LLM 错误（而不是切换模型）"""
        return self._retry_count < self.retry_before_switch

    @property
    def should_retry_timeout(self) -> bool:
        """是否应该重试超时（而不是切换模型）"""
        return self._timeout_retry_count < self.retry_before_switch

    @property
    def last_error(self) -> str | None:
        """最近的错误信息"""
        return self._last_error

    def _handle_timeout(self) -> None:
        """
        处理超时

        使用独立的超时重试计数器（不受 LLM 成功调用影响）。
        只有在超时重试次数用尽后才会真正切换模型。
        """
        self._phase = TaskPhase.TIMEOUT

        # 增加超时重试计数（独立于 LLM 错误重试）
        self._timeout_retry_count += 1

        logger.warning(
            f"[TaskMonitor] Task timeout: {self.metrics.task_id}, "
            f"idle={self.progress_idle_seconds:.1f}s > {self.timeout_seconds}s, "
            f"timeout_retry={self._timeout_retry_count}/{self.retry_before_switch}"
        )

        if self._timeout_retry_count < self.retry_before_switch:
            # 还有重试机会，记录日志但不切换
            logger.info(
                f"[TaskMonitor] Timeout retry {self._timeout_retry_count}/{self.retry_before_switch}, "
                f"continuing with current model"
            )
        else:
            # 超时重试次数用尽，切换到备用模型并重置上下文
            self._timeout_triggered = True
            self.metrics.retry_count = self._timeout_retry_count
            if not self.fallback_model:
                logger.error(
                    "[TaskMonitor] Timeout retries exhausted but no fallback_model configured; "
                    "cannot switch model."
                )
            else:
                self.switch_model(
                    self.fallback_model,
                    f"任务执行超过 {self.timeout_seconds} 秒，已重试 {self.retry_before_switch} 次",
                    reset_context=True,  # 重要：切换时重置上下文
                )

            # 触发回调
            if self.on_timeout:
                try:
                    self.on_timeout(self)
                except Exception as e:
                    logger.error(f"[TaskMonitor] Timeout callback error: {e}")

    @property
    def elapsed_seconds(self) -> float:
        """已经过的时间（秒）"""
        if self.metrics.start_time == 0:
            return 0
        return time.time() - self.metrics.start_time

    def _touch_progress(self) -> None:
        """记录一次进展时间点（用于无进展超时判断）"""
        self._last_progress_time = time.time()

    @property
    def progress_idle_seconds(self) -> float:
        """距上次进展的空闲时间（秒）"""
        if not self._last_progress_time:
            return 0.0
        return time.time() - self._last_progress_time

    @property
    def is_timeout(self) -> bool:
        """是否已超时"""
        return self._timeout_triggered

    @property
    def should_switch_model(self) -> bool:
        """
        是否应该切换模型

        只有在以下条件都满足时才切换：
        1. 已超时
        2. 超时重试次数已用尽
        3. 尚未触发切换
        """
        if self._timeout_triggered:
            return False
        if self.progress_idle_seconds <= self.timeout_seconds:
            return False
        # 超时了，检查超时重试次数是否用尽
        return self._timeout_retry_count >= self.retry_before_switch

    @property
    def needs_context_reset(self) -> bool:
        """切换模型时是否需要重置上下文"""
        return self.metrics.context_reset_on_switch

    @property
    def current_model(self) -> str:
        """当前使用的模型"""
        return self.metrics.final_model

    def get_retrospect_context(self) -> str:
        """
        获取复盘上下文

        返回任务执行的详细信息，供 LLM 分析
        """
        lines = [
            "# 任务执行复盘上下文",
            "",
            "## 基本信息",
            f"- 任务描述: {self.metrics.description}",
            f"- 总耗时: {self.metrics.total_duration_seconds:.1f}秒",
            f"- 迭代次数: {self.metrics.total_iterations}",
            f"- 最终结果: {'成功' if self.metrics.success else '失败'}",
        ]

        if self.metrics.model_switched:
            lines.extend(
                [
                    "",
                    "## 模型切换",
                    f"- 原模型: {self.metrics.initial_model}",
                    f"- 切换到: {self.metrics.final_model}",
                    f"- 切换原因: {self.metrics.switch_reason}",
                ]
            )

        if self.metrics.iterations:
            lines.extend(
                [
                    "",
                    "## 迭代详情",
                ]
            )
            for it in self.metrics.iterations[-10:]:  # 最多显示最后 10 次迭代
                lines.append(f"\n### 第 {it.iteration} 次迭代")
                lines.append(f"- 模型: {it.model_used}")
                lines.append(f"- 工具调用数: {len(it.tool_calls)}")
                for tc in it.tool_calls:
                    status = "✅" if tc.success else "❌"
                    lines.append(f"  - {status} {tc.name} ({tc.duration_ms}ms)")
                    if tc.output_summary:
                        lines.append(f"    输出: {tc.output_summary}")
                if it.llm_response_preview:
                    lines.append(f"- LLM 响应预览: {it.llm_response_preview}")

        if self.metrics.error:
            lines.extend(
                [
                    "",
                    "## 错误信息",
                    f"{self.metrics.error}",
                ]
            )

        return "\n".join(lines)


# 复盘 Prompt 模板
RETROSPECT_PROMPT = """请分析以下任务执行情况，找出耗时过长的原因：

{context}

请从以下几个方面分析：

1. **任务复杂度分析**
   - 任务本身是否复杂？需要多少步骤？
   - 是否有合理的执行方案？

2. **执行效率分析**
   - 工具调用是否高效？是否有重复或无效的调用？
   - 是否走了弯路？哪些步骤可以优化？

3. **错误和重试分析**
   - 是否有错误发生？错误处理是否得当？
   - 是否有不必要的重试？

4. **改进建议**
   - 下次遇到类似任务，如何提高效率？
   - 是否需要新增技能或工具？

请用简洁的语言总结，控制在 200 字以内。"""


# ==================== 复盘结果存储 ====================


@dataclass
class RetrospectRecord:
    """复盘记录"""

    task_id: str
    session_id: str | None
    description: str
    duration_seconds: float
    iterations: int
    model_switched: bool
    initial_model: str
    final_model: str
    retrospect_result: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "description": self.description,
            "duration_seconds": self.duration_seconds,
            "iterations": self.iterations,
            "model_switched": self.model_switched,
            "initial_model": self.initial_model,
            "final_model": self.final_model,
            "retrospect_result": self.retrospect_result,
            "timestamp": self.timestamp,
        }


class RetrospectStorage:
    """
    复盘结果存储

    将复盘结果保存到文件，供每日自检系统读取和汇总。
    """

    def __init__(self, storage_dir: Path | None = None):
        """
        初始化存储

        Args:
            storage_dir: 存储目录，默认为 data/retrospects/
        """
        if storage_dir is None:
            from ..config import settings

            storage_dir = settings.project_root / "data" / "retrospects"

        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, record: RetrospectRecord) -> bool:
        """
        保存复盘记录

        按日期存储，每天一个文件（追加模式）

        Args:
            record: 复盘记录

        Returns:
            是否保存成功
        """
        import json

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            file_path = self.storage_dir / f"{today}_retrospects.jsonl"

            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

            logger.info(f"[RetrospectStorage] Saved retrospect: {record.task_id}")
            return True

        except Exception as e:
            logger.error(f"[RetrospectStorage] Failed to save: {e}")
            return False

    def load_today(self) -> list[RetrospectRecord]:
        """加载今天的复盘记录"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.load_by_date(today)

    def load_by_date(self, date: str) -> list[RetrospectRecord]:
        """
        加载指定日期的复盘记录

        Args:
            date: 日期字符串 (YYYY-MM-DD)

        Returns:
            复盘记录列表
        """
        import json

        file_path = self.storage_dir / f"{date}_retrospects.jsonl"

        if not file_path.exists():
            return []

        records = []
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        records.append(
                            RetrospectRecord(
                                task_id=data.get("task_id", ""),
                                session_id=data.get("session_id"),
                                description=data.get("description", ""),
                                duration_seconds=data.get("duration_seconds", 0),
                                iterations=data.get("iterations", 0),
                                model_switched=data.get("model_switched", False),
                                initial_model=data.get("initial_model", ""),
                                final_model=data.get("final_model", ""),
                                retrospect_result=data.get("retrospect_result", ""),
                                timestamp=data.get("timestamp", ""),
                            )
                        )
        except Exception as e:
            logger.error(f"[RetrospectStorage] Failed to load {date}: {e}")

        return records

    def get_summary(self, date: str | None = None) -> dict:
        """
        获取复盘汇总

        Args:
            date: 日期，默认今天

        Returns:
            汇总信息
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        records = self.load_by_date(date)

        if not records:
            return {
                "date": date,
                "total_tasks": 0,
                "total_duration": 0,
                "avg_duration": 0,
                "model_switches": 0,
                "common_issues": [],
            }

        total_duration = sum(r.duration_seconds for r in records)
        model_switches = sum(1 for r in records if r.model_switched)

        # 提取常见问题（简单的关键词统计）
        issue_keywords = ["重复", "无效", "弯路", "错误", "超时", "失败"]
        issue_counts = dict.fromkeys(issue_keywords, 0)

        for record in records:
            for kw in issue_keywords:
                if kw in record.retrospect_result:
                    issue_counts[kw] += 1

        common_issues = [
            {"issue": kw, "count": count} for kw, count in issue_counts.items() if count > 0
        ]
        common_issues.sort(key=lambda x: x["count"], reverse=True)

        return {
            "date": date,
            "total_tasks": len(records),
            "total_duration": total_duration,
            "avg_duration": total_duration / len(records),
            "model_switches": model_switches,
            "common_issues": common_issues[:5],  # 最多 5 个
            "records": [r.to_dict() for r in records],
        }


# 全局存储实例
_retrospect_storage: RetrospectStorage | None = None


def get_retrospect_storage() -> RetrospectStorage:
    """获取复盘存储单例"""
    global _retrospect_storage
    if _retrospect_storage is None:
        _retrospect_storage = RetrospectStorage()
    return _retrospect_storage
