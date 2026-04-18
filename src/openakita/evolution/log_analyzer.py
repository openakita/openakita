"""
Log analyzer

Features:
- Extract only ERROR/CRITICAL level logs (efficient, does not load full content)
- Keyword search (retrieve context on demand)
- Error classification (distinguish core components from tools)
- Generate concise summaries (for LLM analysis)
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LogEntry:
    """Log entry."""

    timestamp: datetime
    level: str  # ERROR/CRITICAL
    logger_name: str  # Module name
    message: str
    traceback: str | None = None
    component: str = ""  # core / tool / channel / ...

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
    """Error pattern."""

    pattern: str  # Error pattern / type
    count: int
    first_seen: datetime
    last_seen: datetime
    samples: list[LogEntry] = field(default_factory=list)  # Keep at most 3 samples
    component_type: str = ""  # "core" or "tool"
    can_auto_fix: bool = False  # Whether it can be auto-fixed

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
    Log analyzer

    Analyzes only ERROR logs, supports keyword search.
    """

    # Core component module prefixes (not auto-fixed)
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

    # Tool component module prefixes (auto-fixable)
    TOOL_COMPONENTS = [
        "openakita.tools",
        "openakita.channels",
        "openakita.skills",
        "openakita.testing",
    ]

    # Log line regex pattern
    LOG_PATTERN = re.compile(
        r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},?\d*)\s+-\s+(\S+)\s+-\s+(ERROR|CRITICAL)\s+-\s+(.+)$"
    )

    def __init__(self, log_dir: Path):
        """
        Args:
            log_dir: Log directory
        """
        self.log_dir = Path(log_dir)

    def extract_errors_only(
        self,
        date: str | None = None,
        log_file: Path | None = None,
        since: datetime | None = None,
    ) -> list[LogEntry]:
        """
        Extract only ERROR/CRITICAL level logs.

        Efficient implementation: reads line by line, saves only error logs.

        Args:
            date: Specific date (YYYY-MM-DD); None means today.
            log_file: Specific log file; takes precedence over *date*.
            since: Only return logs after this time (for incremental analysis).

        Returns:
            List of error log entries.
        """
        if log_file:
            target_file = Path(log_file)
        else:
            # Prefer error.log (contains only errors)
            target_file = self.log_dir / "error.log"
            if date:
                # Check for a date-suffixed file
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

                    # Try to match a new log line
                    match = self.LOG_PATTERN.match(line)

                    if match:
                        # Save the previous error
                        if current_entry:
                            errors.append(current_entry)

                        # Parse the new error
                        timestamp_str, logger_name, level, message = match.groups()

                        # Parse timestamp
                        try:
                            # Handle both with and without milliseconds
                            if "," in timestamp_str:
                                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
                            else:
                                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            timestamp = datetime.now()

                        # Determine component type
                        component = self._classify_component(logger_name)

                        current_entry = LogEntry(
                            timestamp=timestamp,
                            level=level,
                            logger_name=logger_name,
                            message=message,
                            component=component,
                        )

                    elif current_entry and line.startswith((" ", "\t", "Traceback")):
                        # Traceback continuation line
                        if current_entry.traceback:
                            current_entry.traceback += "\n" + line
                        else:
                            current_entry.traceback = line

                # Save the last error
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
        Search logs by keyword (use when context is needed).

        Args:
            keyword: Search keyword.
            log_file: Log file path; None uses the main log.
            limit: Maximum number of lines to return.
            context_lines: Number of context lines.

        Returns:
            Matched log lines (including context).
        """
        target_file = Path(log_file) if log_file else self.log_dir / "openakita.log"

        if not target_file.exists():
            return []

        results = []
        buffer = []  # Context buffer

        try:
            with open(target_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.rstrip()
                    buffer.append(line)

                    # Keep buffer size bounded
                    if len(buffer) > context_lines * 2 + 1:
                        buffer.pop(0)

                    # Check for match
                    if keyword.lower() in line.lower():
                        # Add context
                        results.append("---")
                        results.extend(buffer)

                        if len(results) >= limit:
                            break

        except Exception as e:
            logger.error(f"Failed to search log file: {e}")

        return results

    def classify_errors(self, errors: list[LogEntry]) -> dict[str, ErrorPattern]:
        """
        Classify errors (distinguish core components from tools).

        Args:
            errors: List of errors.

        Returns:
            Error pattern dict {pattern: ErrorPattern}.
        """
        patterns: dict[str, ErrorPattern] = {}

        for error in errors:
            # Extract error pattern (use the message prefix as pattern)
            pattern_key = self._extract_pattern(error)

            if pattern_key in patterns:
                # Update existing pattern
                p = patterns[pattern_key]
                p.count += 1
                p.last_seen = max(p.last_seen, error.timestamp)
                p.first_seen = min(p.first_seen, error.timestamp)

                # Keep at most 3 samples
                if len(p.samples) < 3:
                    p.samples.append(error)
            else:
                # Create new pattern
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
        Generate a concise error summary (for LLM analysis).

        Args:
            patterns: Error pattern dict.
            max_patterns: Maximum number of error patterns to display.

        Returns:
            Markdown-formatted summary.
        """
        if not patterns:
            return "# Error Log Summary\n\nNo errors found."

        # Sort by occurrence count
        sorted_patterns = sorted(patterns.values(), key=lambda p: p.count, reverse=True)[
            :max_patterns
        ]

        # Statistics
        total_errors = sum(p.count for p in patterns.values())
        core_errors = [p for p in sorted_patterns if p.component_type == "core"]
        tool_errors = [p for p in sorted_patterns if p.component_type == "tool"]

        lines = [
            "# Error Log Summary",
            "",
            f"- Total errors: {total_errors}",
            f"- Core component errors: {len(core_errors)} type(s) (require manual handling)",
            f"- Tool errors: {len(tool_errors)} type(s) (may attempt auto-fix)",
            "",
        ]

        # Core component errors
        if core_errors:
            lines.append("## Core Component Errors (not auto-fixed)")
            lines.append("")
            for p in core_errors:
                sample = p.samples[0] if p.samples else None
                lines.append(f"### [{p.count} times] {p.pattern}")
                lines.append(f"- Module: `{sample.logger_name if sample else 'unknown'}`")
                lines.append(f"- First seen: {p.first_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                lines.append(f"- Last seen: {p.last_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                if sample and sample.traceback:
                    lines.append(f"- Traceback: `{sample.traceback}`")
                lines.append("")

        # Tool errors
        if tool_errors:
            lines.append("## Tool Errors (auto-fixable)")
            lines.append("")
            for p in tool_errors:
                sample = p.samples[0] if p.samples else None
                lines.append(f"### [{p.count} times] {p.pattern}")
                lines.append(f"- Module: `{sample.logger_name if sample else 'unknown'}`")
                lines.append(f"- First seen: {p.first_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                lines.append(f"- Last seen: {p.last_seen.strftime('%Y-%m-%d %H:%M:%S')}")
                if sample and sample.message:
                    lines.append(f"- Message: `{sample.message}`")
                lines.append("")

        return "\n".join(lines)

    def _classify_component(self, logger_name: str) -> str:
        """Classify component based on logger name."""
        for prefix in self.CORE_COMPONENTS:
            if logger_name.startswith(prefix):
                return "core"

        for prefix in self.TOOL_COMPONENTS:
            if logger_name.startswith(prefix):
                return "tool"

        return "other"

    def _get_component_type(self, logger_name: str) -> str:
        """Get component type (core/tool)."""
        component = self._classify_component(logger_name)
        if component == "core":
            return "core"
        elif component == "tool":
            return "tool"
        else:
            # Unknown components default to core (conservative strategy)
            return "core"

    def _extract_pattern(self, error: LogEntry) -> str:
        """Extract error pattern (for grouping)."""
        # Combine module name and message as pattern
        message_prefix = error.message if error.message else ""

        # Remove dynamic content (e.g. IDs, timestamps)
        message_prefix = re.sub(r"\d+", "N", message_prefix)
        message_prefix = re.sub(r"[0-9a-f]{8,}", "ID", message_prefix)

        return f"{error.logger_name}: {message_prefix}"

    def get_errors_for_date_range(
        self,
        days: int = 1,
    ) -> list[LogEntry]:
        """
        Get all errors within the specified number of days.

        Args:
            days: Number of days.

        Returns:
            List of errors.
        """
        all_errors = []

        # Current error.log
        all_errors.extend(self.extract_errors_only())

        # Historical files
        for i in range(1, days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            errors = self.extract_errors_only(date=date)
            all_errors.extend(errors)

        return all_errors
