"""
日志分析器

功能:
- 只提取 ERROR/CRITICAL 级别日志（高效，不加载全部内容）
- 支持关键词检索（按需获取上下文）
- 错误分类（区分核心组件和工具）
- 生成精简摘要（给 LLM 分析）
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LogEntry:
    """日志条目"""

    timestamp: datetime
    level: str  # ERROR/CRITICAL
    logger_name: str  # 模块名
    message: str
    traceback: str | None = None
    component: str = ""  # core/tool/channel/...

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
            "traceback": self.traceback,
            "component": self.component,
        }


@dataclass
class ErrorPattern:
    """错误模式"""

    pattern: str  # 错误模式/类型
    count: int
    first_seen: datetime
    last_seen: datetime
    samples: list[LogEntry] = field(default_factory=list)  # 最多保留 3 个样本
    component_type: str = ""  # "core" 或 "tool"
    can_auto_fix: bool = False  # 是否可自动修复

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "count": self.count,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "samples": [s.to_dict() for s in self.samples],
            "component_type": self.component_type,
            "can_auto_fix": self.can_auto_fix,
        }


class LogAnalyzer:
    """
    日志分析器

    只分析 ERROR 日志，支持关键词检索
    """

    # 核心组件模块前缀（不自动修复）
    CORE_COMPONENTS = [
        "openakita.core.brain",
        "openakita.core.agent",
        "openakita.core.ralph",
        "openakita.memory",
        "openakita.scheduler",
        "openakita.llm",
        "openakita.agents",
        "openakita.storage",
    ]

    # 工具组件模块前缀（可自动修复）
    TOOL_COMPONENTS = [
        "openakita.tools",
        "openakita.channels",
        "openakita.skills",
        "openakita.testing",
    ]

    # 日志行正则表达式
    LOG_PATTERN = re.compile(
        r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},?\d*)\s+-\s+(\S+)\s+-\s+(ERROR|CRITICAL)\s+-\s+(.+)$"
    )

    def __init__(self, log_dir: Path):
        """
        Args:
            log_dir: 日志目录
        """
        self.log_dir = Path(log_dir)

    def extract_errors_only(
        self,
        date: str | None = None,
        log_file: Path | None = None,
        since: datetime | None = None,
    ) -> list[LogEntry]:
        """
        只提取 ERROR/CRITICAL 级别日志

        高效实现：逐行读取，只保存错误日志

        Args:
            date: 指定日期 (YYYY-MM-DD)，None 表示今天
            log_file: 指定日志文件，优先于 date
            since: 只返回此时间之后的日志（用于增量分析）

        Returns:
            错误日志列表
        """
        if log_file:
            target_file = Path(log_file)
        else:
            # 优先读取 error.log（只包含错误）
            target_file = self.log_dir / "error.log"
            if date:
                # 检查是否有日期后缀的文件
                dated_file = self.log_dir / f"error.log.{date}"
                if dated_file.exists():
                    target_file = dated_file

        if not target_file.exists():
            logger.warning(f"Log file not found: {target_file}")
            return []

        errors = []
        current_entry: LogEntry | None = None

        try:
            with open(target_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.rstrip()

                    # 尝试匹配新的日志行
                    match = self.LOG_PATTERN.match(line)

                    if match:
                        # 保存前一个错误
                        if current_entry:
                            errors.append(current_entry)

                        # 解析新错误
                        timestamp_str, logger_name, level, message = match.groups()

                        # 解析时间戳
                        try:
                            # 处理带毫秒和不带毫秒的格式
                            if "," in timestamp_str:
                                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
                            else:
                                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            timestamp = datetime.now()

                        # 确定组件类型
                        component = self._classify_component(logger_name)

                        current_entry = LogEntry(
                            timestamp=timestamp,
                            level=level,
                            logger_name=logger_name,
                            message=message,
                            component=component,
                        )

                    elif current_entry and line.startswith((" ", "\t", "Traceback")):
                        # Traceback 续行
                        if current_entry.traceback:
                            current_entry.traceback += "\n" + line
                        else:
                            current_entry.traceback = line

                # 保存最后一个错误
                if current_entry:
                    errors.append(current_entry)

        except Exception as e:
            logger.error(f"Failed to parse log file {target_file}: {e}")

        if since and errors:
            errors = [e for e in errors if e.timestamp >= since]

        logger.info(
            f"Extracted {len(errors)} errors from {target_file.name}"
            + (f" (since {since.isoformat()})" if since else "")
        )
        return errors

    def search_by_keyword(
        self,
        keyword: str,
        log_file: Path | None = None,
        limit: int = 50,
        context_lines: int = 3,
    ) -> list[str]:
        """
        按关键词检索日志（当需要上下文时使用）

        Args:
            keyword: 搜索关键词
            log_file: 日志文件路径，None 使用主日志
            limit: 最大返回行数
            context_lines: 上下文行数

        Returns:
            匹配的日志行（包含上下文）
        """
        target_file = Path(log_file) if log_file else self.log_dir / "openakita.log"

        if not target_file.exists():
            return []

        results = []
        buffer = []  # 上下文缓冲

        try:
            with open(target_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.rstrip()
                    buffer.append(line)

                    # 保持缓冲区大小
                    if len(buffer) > context_lines * 2 + 1:
                        buffer.pop(0)

                    # 检查是否匹配
                    if keyword.lower() in line.lower():
                        # 添加上下文
                        results.append("---")
                        results.extend(buffer)

                        if len(results) >= limit:
                            break

        except Exception as e:
            logger.error(f"Failed to search log file: {e}")

        return results

    def classify_errors(self, errors: list[LogEntry]) -> dict[str, ErrorPattern]:
        """
        分类错误（区分核心组件和工具）

        Args:
            errors: 错误列表

        Returns:
            错误模式字典 {pattern: ErrorPattern}
        """
        patterns: dict[str, ErrorPattern] = {}

        for error in errors:
            # 提取错误模式（取消息的前 50 个字符作为模式）
            pattern_key = self._extract_pattern(error)

            if pattern_key in patterns:
                # 更新已有模式
                p = patterns[pattern_key]
                p.count += 1
                p.last_seen = max(p.last_seen, error.timestamp)
                p.first_seen = min(p.first_seen, error.timestamp)

                # 最多保留 3 个样本
                if len(p.samples) < 3:
                    p.samples.append(error)
            else:
                # 创建新模式
                component_type = self._get_component_type(error.logger_name)

                patterns[pattern_key] = ErrorPattern(
                    pattern=pattern_key,
                    count=1,
                    first_seen=error.timestamp,
                    last_seen=error.timestamp,
                    samples=[error],
                    component_type=component_type,
                    can_auto_fix=(component_type == "tool"),
                )

        return patterns

    def generate_error_summary(
        self,
        patterns: dict[str, ErrorPattern],
        max_patterns: int = 20,
    ) -> str:
        """
        生成精简的错误摘要（给 LLM 分析）

        Args:
            patterns: 错误模式字典
            max_patterns: 最大显示的错误模式数

        Returns:
            Markdown 格式摘要
        """
        if not patterns:
            return "# 错误日志摘要\n\n没有发现错误。"

        # 按出现次数排序
        sorted_patterns = sorted(patterns.values(), key=lambda p: p.count, reverse=True)[
            :max_patterns
        ]

        # 统计
        total_errors = sum(p.count for p in patterns.values())
        core_errors = [p for p in sorted_patterns if p.component_type == "core"]
        tool_errors = [p for p in sorted_patterns if p.component_type == "tool"]

        lines = [
            "# 错误日志摘要",
            "",
            f"- 总错误数: {total_errors}",
            f"- 核心组件错误: {len(core_errors)} 种（需人工处理）",
            f"- 工具错误: {len(tool_errors)} 种（可尝试自动修复）",
            "",
        ]

        # 核心组件错误
        if core_errors:
            lines.append("## 核心组件错误（不自动修复）")
            lines.append("")
            for p in core_errors:
                sample = p.samples[0] if p.samples else None
                lines.append(f"### [{p.count}次] {p.pattern}")
                lines.append(f"- 模块: `{sample.logger_name if sample else 'unknown'}`")
                lines.append(f"- 首次: {p.first_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                lines.append(f"- 最后: {p.last_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                if sample and sample.traceback:
                    lines.append(f"- Traceback: `{sample.traceback}`")
                lines.append("")

        # 工具错误
        if tool_errors:
            lines.append("## 工具错误（可自动修复）")
            lines.append("")
            for p in tool_errors:
                sample = p.samples[0] if p.samples else None
                lines.append(f"### [{p.count}次] {p.pattern}")
                lines.append(f"- 模块: `{sample.logger_name if sample else 'unknown'}`")
                lines.append(f"- 首次: {p.first_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                lines.append(f"- 最后: {p.last_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                if sample and sample.message:
                    lines.append(f"- 消息: `{sample.message}`")
                lines.append("")

        return "\n".join(lines)

    def _classify_component(self, logger_name: str) -> str:
        """根据 logger 名称分类组件"""
        for prefix in self.CORE_COMPONENTS:
            if logger_name.startswith(prefix):
                return "core"

        for prefix in self.TOOL_COMPONENTS:
            if logger_name.startswith(prefix):
                return "tool"

        return "other"

    def _get_component_type(self, logger_name: str) -> str:
        """获取组件类型（core/tool）"""
        component = self._classify_component(logger_name)
        if component == "core":
            return "core"
        elif component == "tool":
            return "tool"
        else:
            # 未知组件默认为 core（保守策略）
            return "core"

    def _extract_pattern(self, error: LogEntry) -> str:
        """提取错误模式（用于分组）"""
        # 组合模块名和消息作为模式
        message_prefix = error.message if error.message else ""

        # 移除动态内容（如 ID、时间戳等）
        message_prefix = re.sub(r"\d+", "N", message_prefix)
        message_prefix = re.sub(r"[0-9a-f]{8,}", "ID", message_prefix)

        return f"{error.logger_name}: {message_prefix}"

    def get_errors_for_date_range(
        self,
        days: int = 1,
    ) -> list[LogEntry]:
        """
        获取指定天数内的所有错误

        Args:
            days: 天数

        Returns:
            错误列表
        """
        all_errors = []

        # 当前 error.log
        all_errors.extend(self.extract_errors_only())

        # 历史文件
        for i in range(1, days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            errors = self.extract_errors_only(date=date)
            all_errors.extend(errors)

        return all_errors
