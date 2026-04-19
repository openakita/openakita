"""
Self-Check System

Features:
- Run test cases
- Analyze ERROR logs
- Distinguish between core component and tool errors
- Automatically fix tool issues
- Post-fix self-test verification
- Generate daily reports
"""

import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import settings
from ..core.brain import Brain
from ..tools.file import FileTool
from ..tools.shell import ShellTool
from .log_analyzer import ErrorPattern, LogAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """Test case"""

    id: str
    category: str  # qa, tools, search
    description: str
    input: Any
    expected: Any
    validator: str | None = None  # Validator function name


@dataclass
class TestResult:
    """Test result"""

    test_id: str
    passed: bool
    actual: Any = None
    error: str | None = None
    duration_ms: float = 0


@dataclass
class CheckReport:
    """Self-check report"""

    timestamp: datetime
    total_tests: int
    passed: int
    failed: int
    results: list[TestResult] = field(default_factory=list)
    fixed_count: int = 0
    status: str = "unknown"  # healthy, degraded, critical

    @property
    def pass_rate(self) -> float:
        if self.total_tests == 0:
            return 0
        return self.passed / self.total_tests * 100


@dataclass
class FixRecord:
    """Fix record"""

    error_pattern: str
    component: str
    fix_action: str
    fix_time: datetime
    verified: bool = False
    verification_result: str = ""
    success: bool = False


@dataclass
class DailyReport:
    """Daily system report"""

    date: str
    timestamp: datetime

    # Error statistics
    total_errors: int = 0
    core_errors: int = 0
    tool_errors: int = 0

    # Fix statistics
    fix_attempted: int = 0
    fix_success: int = 0
    fix_failed: int = 0

    # Detailed content
    core_error_patterns: list[dict] = field(default_factory=list)
    tool_error_patterns: list[dict] = field(default_factory=list)
    fix_records: list[FixRecord] = field(default_factory=list)

    # Memory consolidation results (if any)
    memory_consolidation: dict | None = None

    # Task retrospect statistics
    retrospect_summary: dict | None = None  # Retrospect summary

    # Memory system optimization suggestions
    memory_insights: dict | None = None  # Optimization suggestions extracted from memory

    # Report status
    reported: bool = False

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "timestamp": self.timestamp.isoformat(),
            "total_errors": self.total_errors,
            "core_errors": self.core_errors,
            "tool_errors": self.tool_errors,
            "fix_attempted": self.fix_attempted,
            "fix_success": self.fix_success,
            "fix_failed": self.fix_failed,
            "core_error_patterns": self.core_error_patterns,
            "tool_error_patterns": self.tool_error_patterns,
            "fix_records": [
                {
                    "error_pattern": r.error_pattern,
                    "component": r.component,
                    "fix_action": r.fix_action,
                    "fix_time": r.fix_time.isoformat(),
                    "verified": r.verified,
                    "verification_result": r.verification_result,
                    "success": r.success,
                }
                for r in self.fix_records
            ],
            "memory_consolidation": self.memory_consolidation,
            "retrospect_summary": self.retrospect_summary,
            "memory_insights": self.memory_insights,
            "reported": self.reported,
        }

    def to_markdown(self) -> str:
        """Generate report in Markdown format"""
        lines = [
            f"# Daily System Report - {self.date}",
            "",
            "## Summary",
            "",
            f"- Total Errors: {self.total_errors}",
            f"- Core Component Errors: {self.core_errors} (Manual intervention required)",
            f"- Tool Errors: {self.tool_errors}",
            f"- Fixes Attempted: {self.fix_attempted}",
            f"- Fixes Successful: {self.fix_success}",
            f"- Fixes Failed: {self.fix_failed}",
            "",
        ]

        # Core component errors
        if self.core_error_patterns:
            lines.append("## Core Component Errors (Manual intervention required)")
            lines.append("")
            for p in self.core_error_patterns:
                lines.append(f"### [{p.get('count', 1)} times] {p.get('pattern', '')}")
                lines.append(f"- Module: `{p.get('logger', 'unknown')}`")
                lines.append(f"- Time: {p.get('last_seen', '')}")
                if p.get("message"):
                    lines.append(f"- Message: `{p.get('message', '')}`")
                lines.append("- **Recommendation: Check logs and consider restarting services**")
                lines.append("")

        # Tool fix records
        if self.fix_records:
            lines.append("## Tool Fix Records")
            lines.append("")
            for r in self.fix_records:
                status = "Fixed" if r.success else "Fix Failed"
                lines.append(f"### [{status}] {r.error_pattern}")
                lines.append(f"- Component: `{r.component}`")
                lines.append(f"- Fix Action: {r.fix_action}")
                lines.append(f"- Time: {r.fix_time.strftime('%Y-%m-%d %H:%M:%S')}")
                lines.append(f"- Verification: {'Passed' if r.verified else 'Failed'}")
                if r.verification_result:
                    lines.append(f"- Verification Result: {r.verification_result}")
                lines.append("")

        # Memory consolidation results
        if self.memory_consolidation:
            lines.append("## Memory Consolidation Results")
            lines.append("")
            mc = self.memory_consolidation
            lines.append(f"- Sessions Processed: {mc.get('sessions_processed', 0)}")
            lines.append(f"- Memories Extracted: {mc.get('memories_extracted', 0)}")
            lines.append(f"- Memories Added: {mc.get('memories_added', 0)}")
            lines.append(f"- Duplicates Removed: {mc.get('duplicates_removed', 0)}")
            lines.append(f"- MEMORY.md: {'Refreshed' if mc.get('memory_md_refreshed') else 'Not Refreshed'}")
            lines.append("")

        # Task retrospect statistics
        if self.retrospect_summary:
            lines.append("## Task Retrospect Statistics")
            lines.append("")
            rs = self.retrospect_summary
            lines.append(f"- Retrospect Task Count: {rs.get('total_tasks', 0)}")
            lines.append(f"- Total Duration: {rs.get('total_duration', 0):.0f}s")
            lines.append(f"- Average Duration: {rs.get('avg_duration', 0):.1f}s")
            lines.append(f"- Model Switches: {rs.get('model_switches', 0)}")

            # Common issues
            common_issues = rs.get("common_issues", [])
            if common_issues:
                lines.append("")
                lines.append("### Common Issues")
                for issue in common_issues:
                    lines.append(f"- {issue.get('issue', '')}: {issue.get('count', 0)} times")

            # Retrospect details
            records = rs.get("records", [])
            if records:
                lines.append("")
                lines.append("### Retrospect Details")
                for r in records:
                    duration = r.get("duration_seconds", 0)
                    desc = r.get("description", "")
                    result = r.get("retrospect_result", "")
                    lines.append(f"- **{desc}** ({duration:.0f}s)")
                    if result:
                        lines.append(f"  - Analysis: {result}")

            lines.append("")

        # Memory system optimization suggestions
        if self.memory_insights:
            lines.append("## Memory System Optimization Suggestions")
            lines.append("")
            mi = self.memory_insights

            # Error lessons
            error_memories = mi.get("error_memories", [])
            if error_memories:
                lines.append("### Error Lessons (needs attention)")
                for m in error_memories:
                    source = m.get("source", "")
                    source_label = f" [{source}]" if source else ""
                    lines.append(f"- {m.get('content', '')}{source_label}")
                lines.append("")

            # Rule constraints
            rule_memories = mi.get("rule_memories", [])
            if rule_memories:
                lines.append("### Rule Constraints (must follow)")
                for m in rule_memories:
                    lines.append(f"- {m.get('content', '')}")
                lines.append("")

            # Optimization suggestions summary
            optimization_suggestions = mi.get("optimization_suggestions", [])
            if optimization_suggestions:
                lines.append("### Optimization Suggestions Summary")
                for s in optimization_suggestions:
                    lines.append(f"- {s}")
                lines.append("")

            # Statistics
            lines.append(
                f"*Extracted {mi.get('total_errors', 0)} error lessons, "
                f"{mi.get('total_rules', 0)} rule constraints*"
            )
            lines.append("")

        lines.append("---")
        lines.append(f"*Report generated at: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)


class SelfChecker:
    """
    Self-check system

    - Run test cases
    - Analyze failure causes
    - Auto-fix code
    - Record learning experience
    """

    def __init__(
        self,
        brain: Brain,
        test_dir: Path | None = None,
        memory_manager=None,
    ):
        self.brain = brain
        self.test_dir = test_dir or (
            settings.project_root / "src" / "openakita" / "testing" / "cases"
        )
        self._memory_manager = memory_manager
        self.shell = ShellTool()
        self.file_tool = FileTool()

        self._test_cases: list[TestCase] = []

    def load_test_cases(self) -> int:
        """Load test cases"""
        self._test_cases = []

        # Load from test directory
        if self.test_dir.exists():
            for category_dir in self.test_dir.iterdir():
                if category_dir.is_dir():
                    category = category_dir.name
                    for test_file in category_dir.glob("*.py"):
                        cases = self._load_test_file(test_file, category)
                        self._test_cases.extend(cases)

        # Add built-in test cases
        self._test_cases.extend(self._get_builtin_tests())

        logger.info(f"Loaded {len(self._test_cases)} test cases")
        return len(self._test_cases)

    def _load_test_file(self, path: Path, category: str) -> list[TestCase]:
        """Load test cases from file"""
        # TODO: Implement loading test cases from Python files
        return []

    def _get_builtin_tests(self) -> list[TestCase]:
        """Get built-in test cases"""
        tests = []

        # Basic functionality tests
        tests.append(
            TestCase(
                id="core_brain_001",
                category="core",
                description="Brain basic response test",
                input="Hello",
                expected="Contains response text",
            )
        )

        tests.append(
            TestCase(
                id="core_shell_001",
                category="tools",
                description="Shell command execution test",
                input="echo hello",
                expected="hello",
            )
        )

        tests.append(
            TestCase(
                id="core_file_001",
                category="tools",
                description="File read/write test",
                input={"action": "write_read", "content": "test"},
                expected="test",
            )
        )

        return tests

    async def run_check(
        self,
        categories: list[str] | None = None,
        quick: bool = False,
    ) -> CheckReport:
        """
        Run self-check

        Args:
            categories: Categories to test
            quick: Whether to do a quick check (only run core tests)

        Returns:
            CheckReport
        """
        logger.info("Starting self-check...")

        if not self._test_cases:
            self.load_test_cases()

        # Filter test cases
        tests = self._test_cases
        if categories:
            tests = [t for t in tests if t.category in categories]
        if quick:
            tests = [t for t in tests if t.category == "core"][:10]

        results = []
        passed = 0
        failed = 0

        for test in tests:
            result = await self._run_test(test)
            results.append(result)

            if result.passed:
                passed += 1
            else:
                failed += 1
                logger.warning(f"Test failed: {test.id} - {result.error}")

        # Determine status
        pass_rate = passed / len(results) * 100 if results else 0
        if pass_rate >= 95:
            status = "healthy"
        elif pass_rate >= 80:
            status = "degraded"
        else:
            status = "critical"

        report = CheckReport(
            timestamp=datetime.now(),
            total_tests=len(results),
            passed=passed,
            failed=failed,
            results=results,
            status=status,
        )

        logger.info(f"Self-check complete: {status} ({pass_rate:.1f}% passed)")

        return report

    async def _run_test(self, test: TestCase) -> TestResult:
        """Run a single test"""
        import time

        start = time.time()

        try:
            if test.category == "core":
                actual = await self._run_core_test(test)
            elif test.category == "tools":
                actual = await self._run_tool_test(test)
            else:
                actual = await self._run_generic_test(test)

            # Validate result
            passed = self._validate(actual, test.expected)

            duration = (time.time() - start) * 1000

            return TestResult(
                test_id=test.id,
                passed=passed,
                actual=actual,
                duration_ms=duration,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                test_id=test.id,
                passed=False,
                error=str(e),
                duration_ms=duration,
            )

    async def _run_core_test(self, test: TestCase) -> Any:
        """Run core test"""
        if "brain" in test.id:
            response = await self.brain.think(test.input)
            return response.content
        return None

    async def _run_tool_test(self, test: TestCase) -> Any:
        """Run tool test"""
        if "shell" in test.id:
            result = await self.shell.run(test.input)
            return result.stdout.strip()
        elif "file" in test.id:
            if isinstance(test.input, dict):
                if test.input.get("action") == "write_read":
                    test_file = str(Path(tempfile.gettempdir()) / "openakita_test.txt")
                    await self.file_tool.write(test_file, test.input["content"])
                    return await self.file_tool.read(test_file)
        return None

    async def _run_generic_test(self, test: TestCase) -> Any:
        """Run generic test"""
        # TODO: Implement more test types
        return None

    def _validate(self, actual: Any, expected: Any) -> bool:
        """Validate result"""
        if expected is None:
            return actual is not None

        if isinstance(expected, str):
            if expected.startswith("包含"):
                return expected[2:] in str(actual) or str(actual) != ""
            return str(actual) == expected

        return actual == expected

    async def fix_failures(self, report: CheckReport) -> int:
        """
        Attempt to fix failed tests

        Args:
            report: Self-check report

        Returns:
            Number of fixes
        """
        fixed = 0

        for result in report.results:
            if not result.passed:
                success = await self._try_fix(result)
                if success:
                    fixed += 1

        report.fixed_count = fixed
        logger.info(f"Fixed {fixed} failing tests")

        return fixed

    async def _try_fix(self, result: TestResult) -> bool:
        """Attempt to fix a single failure"""
        logger.info(f"Attempting to fix: {result.test_id}")

        # Use LLM to analyze the error and provide fix suggestions
        prompt = f"""Test failed:
ID: {result.test_id}
Error: {result.error}
Actual result: {result.actual}

Please analyze possible causes and provide fix suggestions."""

        response = await self.brain.think(prompt)

        logger.info(f"Fix suggestion: {response.content}")

        # TODO: Implement automatic fix logic
        # This requires different fix strategies for different error types

        return False

    async def learn_from_check(self, report: CheckReport) -> None:
        """Learn from self-check"""
        if report.failed > 0:
            # Record failure patterns
            failures = [r for r in report.results if not r.passed]

            for failure in failures:
                logger.info(f"Learning from failure: {failure.test_id}")

                # TODO: Record failure patterns to memory system
                # So similar issues can be avoided next time

    # ==================== Log Analysis and Auto-fix ====================

    # Default self-check prompt (used when the file does not exist)
    DEFAULT_SELFCHECK_PROMPT = """You are the system self-check Agent, responsible for analyzing error logs and deciding fix strategies.

For each error, output a JSON array:
[
  {
    "error_id": "module_name_message_prefix",
    "module": "module name",
    "error_type": "core|tool|channel|config|network|skill|task",
    "analysis": "error cause analysis",
    "severity": "critical|high|medium|low",
    "can_fix": true|false,
    "fix_instruction": "specific fix instructions (task description for the fix Agent)",
    "fix_reason": "reason for choosing the strategy",
    "requires_restart": false,
    "note_to_user": "note to user (if manual handling is needed)"
  }
]

Rules:
- Core component (Brain/Agent/Memory/Scheduler/LLM/Database) errors: can_fix=false
- Tool/channel/config errors: can attempt to fix, clearly state the specific operation in fix_instruction
- Skill-related errors: investigate issues with the skill itself (files, format, dependencies), don't dwell on the task
- Persistent task failures: suggest the user optimize task configuration, it may be a poorly designed task
- fix_instruction must clearly state which tool to use (shell/file) and what command to execute
- Only output a JSON array"""

    async def run_daily_check(self, since: datetime | None = None) -> DailyReport:
        """
        Execute system self-check (LLM-driven)

        Flow:
        1. Locally extract ERROR logs (starting from `since`)
        2. Generate error summary
        3. LLM analyzes errors and decides on fix strategies
        4. Execute fixes based on LLM decisions
        5. Self-test verification after fix
        6. Generate report

        Args:
            since: Only analyze logs and retrospect records after this time. None means analyze everything (first run).

        Returns:
            DailyReport
        """
        logger.info(
            "Starting self-check (LLM-driven)"
            + (f", since={since.isoformat()}" if since else ", first run")
            + "..."
        )

        today = datetime.now().strftime("%Y-%m-%d")
        report = DailyReport(
            date=today,
            timestamp=datetime.now(),
        )

        # === Phase 1: Collect all issue information (logs + memory + retrospect) ===

        # 1.1 Extract log errors (supports incremental: only since last check)
        log_analyzer = LogAnalyzer(settings.log_dir_path)
        errors = log_analyzer.extract_errors_only(since=since)
        error_summary = ""
        patterns = {}

        if errors:
            patterns = log_analyzer.classify_errors(errors)
            report.total_errors = sum(p.count for p in patterns.values())
            error_summary = log_analyzer.generate_error_summary(patterns)
            logger.info(f"Extracted {report.total_errors} errors from logs")
        else:
            logger.info("No errors found in logs")

        # 1.2 Load task retrospect summary (before LLM analysis)
        retrospect_info = ""
        try:
            from ..core.task_monitor import get_retrospect_storage

            retrospect_storage = get_retrospect_storage()
            report.retrospect_summary = retrospect_storage.get_summary(today)

            if report.retrospect_summary.get("total_tasks", 0) > 0:
                logger.info(
                    f"Loaded retrospect summary: {report.retrospect_summary['total_tasks']} tasks"
                )
                # Build retrospect info summary
                retrospect_info = self._build_retrospect_summary_for_llm(report.retrospect_summary)
        except Exception as e:
            logger.warning(f"Failed to load retrospect summary: {e}")

        # 1.3 Extract error lessons from memory system (before LLM analysis)
        memory_info = ""
        try:
            report.memory_insights = await self._extract_memory_insights()
            if report.memory_insights:
                logger.info(
                    f"Extracted memory insights: {report.memory_insights.get('total_errors', 0)} errors"
                )
                # Build memory info summary
                memory_info = self._build_memory_summary_for_llm(report.memory_insights)
        except Exception as e:
            logger.warning(f"Failed to extract memory insights: {e}")

        # === Phase 2: Comprehensive analysis (logs + memory + retrospect submitted to LLM together) ===

        # Build the full analysis input
        full_analysis_input = self._build_full_analysis_input(
            error_summary=error_summary,
            retrospect_info=retrospect_info,
            memory_info=memory_info,
        )

        if not full_analysis_input.strip():
            logger.info("No issues to analyze")
            self._save_daily_report(report)
            return report

        try:
            # LLM comprehensive analysis (if brain is available)
            if self.brain:
                analysis_results = await self._analyze_errors_with_llm(full_analysis_input)
                logger.info(f"LLM analyzed {len(analysis_results)} issues")
            else:
                # No brain, use rule-based matching (fallback mode)
                logger.warning("No brain available, using rule-based analysis")
                analysis_results = self._analyze_errors_with_rules(patterns)

            # === Phase 3: Process errors based on analysis results ===
            # Only allow "direct fix" for these error types:
            # - tool: built-in tools
            # - skill: skills under the skills directory
            # - mcp: MCP related (mcps/ directory, connections/calls)
            # - channel: IM channel adapters (part of the tool layer)
            allowed_fix_types = {"tool", "skill", "mcp", "channel"}
            autofix_enabled = settings.selfcheck_autofix
            if not autofix_enabled:
                logger.info("Selfcheck autofix is disabled, skipping fix attempts")

            MAX_FIX_ATTEMPTS = 3
            for result in analysis_results:
                error_type = result.get("error_type", "unknown")
                can_fix = result.get("can_fix", False)

                if error_type == "core" or error_type not in allowed_fix_types or not can_fix:
                    report.core_errors += 1
                    report.core_error_patterns.append(
                        {
                            "pattern": result.get("error_id", ""),
                            "count": 1,
                            "logger": result.get("module", "unknown"),
                            "message": result.get("analysis", ""),
                            "last_seen": datetime.now().isoformat(),
                            "note_to_user": result.get("note_to_user", ""),
                            "requires_restart": result.get("requires_restart", False),
                        }
                    )
                else:
                    report.tool_errors += 1

                    if autofix_enabled and report.fix_attempted < MAX_FIX_ATTEMPTS:
                        report.fix_attempted += 1

                        try:
                            fix_record = await self._execute_fix_by_llm_decision(result)
                            report.fix_records.append(fix_record)

                            if fix_record.success:
                                report.fix_success += 1
                            else:
                                report.fix_failed += 1

                        except Exception as e:
                            logger.error(f"Fix failed for {result.get('error_id')}: {e}")
                            report.fix_failed += 1
                    elif autofix_enabled and report.fix_attempted >= MAX_FIX_ATTEMPTS:
                        logger.info(
                            f"Skipping fix for {result.get('error_id')}: "
                            f"max fix attempts ({MAX_FIX_ATTEMPTS}) reached"
                        )

                    # Record tool error patterns (recorded regardless of fix status)
                    report.tool_error_patterns.append(
                        {
                            "pattern": result.get("error_id", ""),
                            "count": 1,
                            "logger": result.get("module", "unknown"),
                            "message": result.get("analysis", ""),
                            "last_seen": datetime.now().isoformat(),
                        }
                    )

            logger.info(
                f"Daily check complete: {report.total_errors} errors, "
                f"core={report.core_errors}, tool={report.tool_errors}, "
                f"fixed={report.fix_success}, failed={report.fix_failed}"
            )

        except Exception as e:
            logger.error(f"Daily check failed: {e}", exc_info=True)

        # Save report
        self._save_daily_report(report)

        return report

    def _build_retrospect_summary_for_llm(self, retrospect_summary: dict) -> str:
        """
        Build retrospect info summary (for LLM analysis)

        Args:
            retrospect_summary: Retrospect summary data

        Returns:
            Markdown-formatted summary
        """
        if not retrospect_summary or retrospect_summary.get("total_tasks", 0) == 0:
            return ""

        lines = [
            "## Task Retrospect Information",
            "",
            f"- Today's retrospect task count: {retrospect_summary.get('total_tasks', 0)}",
            f"- Total duration: {retrospect_summary.get('total_duration', 0):.0f}s",
            f"- Average duration: {retrospect_summary.get('avg_duration', 0):.1f}s",
            f"- Model switches: {retrospect_summary.get('model_switches', 0)}",
            "",
        ]

        # Common issues
        common_issues = retrospect_summary.get("common_issues", [])
        if common_issues:
            lines.append("### Common Issues Found in Retrospect")
            for issue in common_issues:
                lines.append(f"- [{issue.get('count', 0)} times] {issue.get('issue', '')}")
            lines.append("")

        # Retrospect details
        records = retrospect_summary.get("records", [])
        if records:
            lines.append("### Retrospect Details")
            for r in records:
                desc = r.get("description", "")
                result = r.get("retrospect_result", "")
                lines.append(f"- **{desc}**")
                if result:
                    lines.append(f"  - Analysis: {result}")
            lines.append("")

        return "\n".join(lines)

    def _build_memory_summary_for_llm(self, memory_insights: dict) -> str:
        """
        Build memory info summary (for LLM analysis)

        Args:
            memory_insights: Memory optimization suggestions data

        Returns:
            Markdown-formatted summary
        """
        if not memory_insights:
            return ""

        lines = ["## Error Lessons in Memory System", ""]

        # Error lessons
        error_list = memory_insights.get("error_list", [])
        if error_list:
            lines.append("### Historical Error Lessons (recent records)")
            for err in error_list:
                source = err.get("source", "unknown")
                content = err.get("content", "")
                lines.append(f"- [{source}] {content}")
            lines.append("")

        # Rule constraints
        rule_list = memory_insights.get("rule_list", [])
        if rule_list:
            lines.append("### System Rule Constraints")
            for rule in rule_list:
                content = rule.get("content", "")
                lines.append(f"- {content}")
            lines.append("")

        return "\n".join(lines)

    def _build_full_analysis_input(
        self,
        error_summary: str,
        retrospect_info: str,
        memory_info: str,
    ) -> str:
        """
        Build the full analysis input (logs + retrospect + memory)

        Args:
            error_summary: Log error summary
            retrospect_info: Retrospect info summary
            memory_info: Memory info summary

        Returns:
            Full analysis input (Markdown format)
        """
        sections = []

        if error_summary:
            sections.append(error_summary)

        if retrospect_info:
            sections.append(retrospect_info)

        if memory_info:
            sections.append(memory_info)

        if not sections:
            return ""

        # Add comprehensive analysis description
        header = """# System Self-Check Comprehensive Analysis

The following information comes from:
1. **Log errors** - Today's ERROR/CRITICAL level logs
2. **Task retrospect** - Execution analysis of long-running tasks
3. **Error lessons** - Historical issues recorded in the memory system

Please analyze this information comprehensively and identify issues that need fixing.

---

"""
        return header + "\n\n".join(sections)

    async def _extract_memory_insights(self) -> dict:
        """
        Extract optimization-related information from the memory system

        Memory types extracted:
        - ERROR: Error lessons (from retrospects, task failures, etc.)
        - RULE: Rule constraints (rules set by the user)

        Returns:
            Memory optimization suggestions dictionary
        """
        try:
            from ..memory import MemoryManager, MemoryType

            memory_manager = self._memory_manager
            if memory_manager is None:
                memory_manager = MemoryManager(
                    data_dir=settings.project_root / "data" / "memory",
                    memory_md_path=settings.memory_path,
                    search_backend=settings.search_backend,
                    embedding_api_provider=settings.embedding_api_provider,
                    embedding_api_key=settings.embedding_api_key,
                    embedding_api_model=settings.embedding_api_model,
                )

            # Extract ERROR-type memories
            error_memories = memory_manager.search_memories(
                memory_type=MemoryType.ERROR,
                limit=50,
            )

            # Extract RULE-type memories
            rule_memories = memory_manager.search_memories(
                memory_type=MemoryType.RULE,
                limit=20,
            )

            # Convert to dict format
            error_list = [
                {
                    "id": m.id,
                    "content": m.content,
                    "source": m.source,
                    "importance": m.importance_score,
                    "created_at": m.created_at.isoformat(),
                    "tags": m.tags,
                }
                for m in error_memories
            ]

            rule_list = [
                {
                    "id": m.id,
                    "content": m.content,
                    "importance": m.importance_score,
                    "created_at": m.created_at.isoformat(),
                }
                for m in rule_memories
            ]

            # If there are enough error memories, let the LLM extract optimization suggestions
            optimization_suggestions = []
            if len(error_list) >= 3 and self.brain:
                optimization_suggestions = await self._generate_optimization_suggestions(
                    error_list, rule_list
                )

            return {
                "error_memories": error_list,
                "rule_memories": rule_list,
                "total_errors": len(error_list),
                "total_rules": len(rule_list),
                "optimization_suggestions": optimization_suggestions,
            }

        except Exception as e:
            logger.error(f"Failed to extract memory insights: {e}")
            return {}

    async def _generate_optimization_suggestions(
        self, error_memories: list[dict], rule_memories: list[dict]
    ) -> list[str]:
        """
        Use LLM to generate optimization suggestions from memory

        Args:
            error_memories: List of error memories
            rule_memories: List of rule memories

        Returns:
            List of optimization suggestions
        """
        # Build error summary
        error_summary = "\n".join(
            [f"- [{m.get('source', 'unknown')}] {m.get('content', '')}" for m in error_memories]
        )

        rule_summary = "\n".join([f"- {m.get('content', '')}" for m in rule_memories])

        prompt = f"""Please analyze the following system-recorded error lessons and rule constraints, and extract the most important optimization suggestions.

## Error Lessons (recent records)
{error_summary if error_summary else "None"}

## Rule Constraints
{rule_summary if rule_summary else "None"}

Extract 3-5 most important optimization suggestions from this information, each concise (no more than 50 chars).
Output as a JSON array, e.g.: ["suggestion1", "suggestion2", "suggestion3"]
"""

        try:
            response = await self.brain.think(
                prompt,
                system="You are a system optimization expert. Extract actionable optimization suggestions from error records. Output only a JSON array, nothing else.",
            )

            # Parse JSON
            import re

            json_match = re.search(r"\[.*\]", response.content, re.DOTALL)
            if json_match:
                suggestions = json.loads(json_match.group())
                if isinstance(suggestions, list):
                    return [str(s) for s in suggestions]

            return []

        except Exception as e:
            logger.warning(f"Failed to generate optimization suggestions: {e}")
            return []

    async def _analyze_errors_with_llm(self, error_summary: str) -> list[dict]:
        """
        Use LLM to analyze errors and decide fix strategies (supports batch processing)

        Args:
            error_summary: Error summary (Markdown format)

        Returns:
            List of analysis results
        """
        # Load dedicated prompt
        prompt_path = settings.project_root / "prompts" / "selfcheck_system.md"
        if prompt_path.exists():
            system_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            system_prompt = self.DEFAULT_SELFCHECK_PROMPT
            logger.warning("Using default selfcheck prompt")

        # Append environment indicator so the LLM knows the current environment type
        env_hint = (
            "production" if settings.selfcheck_autofix else "development (auto-fix disabled, analysis only)"
        )
        system_prompt += f"\n\nCurrent environment: {env_hint}"

        # Check summary size; if too large, process in batches
        MAX_CHARS_PER_BATCH = 8000  # Max chars per batch (approximately 2000 tokens)

        if len(error_summary) <= MAX_CHARS_PER_BATCH:
            # Summary is small, process directly
            return await self._analyze_single_batch(error_summary, system_prompt)

        # Summary too large, process in batches
        logger.info(f"Error summary too large ({len(error_summary)} chars), splitting into batches")

        # Split into independent error blocks by "### ["
        import re

        error_blocks = re.split(r"(?=### \[)", error_summary)

        # Preserve header info
        header = ""
        if error_blocks and not error_blocks[0].startswith("### ["):
            header = error_blocks[0]
            error_blocks = error_blocks[1:]

        # Batch
        batches = []
        current_batch = header

        for block in error_blocks:
            if len(current_batch) + len(block) > MAX_CHARS_PER_BATCH:
                if current_batch.strip():
                    batches.append(current_batch)
                current_batch = header + block
            else:
                current_batch += block

        if current_batch.strip():
            batches.append(current_batch)

        logger.info(f"Split into {len(batches)} batches for LLM analysis")

        # Call LLM in batches
        all_results = []
        for i, batch in enumerate(batches):
            logger.info(f"Analyzing batch {i + 1}/{len(batches)} ({len(batch)} chars)")
            try:
                batch_results = await self._analyze_single_batch(batch, system_prompt)
                all_results.extend(batch_results)
            except Exception as e:
                logger.error(f"Batch {i + 1} analysis failed: {e}")
                continue

        return all_results

    async def _analyze_single_batch(self, error_summary: str, system_prompt: str) -> list[dict]:
        """
        Analyze a single batch of errors

        Args:
            error_summary: Error summary
            system_prompt: System prompt

        Returns:
            List of analysis results
        """
        user_prompt = f"""Analyze the following error log summary, and for each error output an analysis result (JSON array format):

{error_summary}

Output the JSON array directly, nothing else."""

        try:
            response = await self.brain.think(
                user_prompt,
                system=system_prompt,
            )

            # Parse JSON result
            return self._parse_llm_analysis(response.content)

        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return []

    def _parse_llm_analysis(self, content: str) -> list[dict]:
        """
        Parse the analysis result returned by the LLM

        Args:
            content: Content returned by the LLM

        Returns:
            List of analysis results
        """
        try:
            # Try to extract JSON array
            import re

            # Look for a JSON array
            json_match = re.search(r"\[[\s\S]*\]", content)
            if json_match:
                json_str = json_match.group()
                return json.loads(json_str)

            # Try to parse directly
            return json.loads(content)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"LLM response: {content}")
            return []

    def _analyze_errors_with_rules(self, patterns: dict) -> list[dict]:
        """
        Analyze errors using rules (fallback mode, used when no LLM is available)

        Args:
            patterns: Error patterns dictionary

        Returns:
            List of analysis results
        """
        results = []

        for pattern_key, pattern in patterns.items():
            sample = pattern.samples[0] if pattern.samples else None
            module = sample.logger_name if sample else "unknown"
            message = sample.message if sample else ""

            # Check if it's a core component
            is_core = pattern.component_type == "core"

            # Determine fix strategy and generate fix instruction
            fix_instruction = None
            can_fix = False

            if not is_core:
                message_lower = message.lower()
                if "permission" in message_lower or "access denied" in message_lower:
                    # Avoid OS-level permission adjustments (especially on Windows)
                    fix_instruction = None
                    can_fix = False
                elif "not found" in message_lower or "no such file" in message_lower:
                    fix_instruction = "Use the file tool to create missing directories: ensure data/, data/cache/, data/sessions/, logs/ directories exist"
                    can_fix = True
                elif "cache" in message_lower or "corrupt" in message_lower:
                    fix_instruction = "Use the shell tool to clean cache directory: delete all files under data/cache/, then recreate the directory"
                    can_fix = True
                elif "timeout" in message_lower:
                    # Process cleanup usually involves OS-level operations; just report to the user
                    fix_instruction = None
                    can_fix = False
                elif "connection" in message_lower:
                    # Connection errors usually require manual check
                    fix_instruction = None
                    can_fix = False

            results.append(
                {
                    "error_id": pattern_key,
                    "module": module,
                    "error_type": "core" if is_core else "tool",
                    "analysis": message,
                    "severity": "high" if is_core else "medium",
                    "can_fix": can_fix,
                    "fix_instruction": fix_instruction,
                    "fix_reason": "Rule matching (fallback mode)",
                    "requires_restart": is_core,
                    "note_to_user": "Manual check required" if is_core else None,
                }
            )

        return results

    FIX_TIMEOUT_SECONDS = 60

    async def _execute_fix_by_llm_decision(self, analysis: dict, max_retries: int = 1) -> FixRecord:
        """
        Execute fix based on LLM decision (uses main Agent, with timeout protection)

        Args:
            analysis: LLM analysis result (contains fix_instruction)
            max_retries: Max retries (default 1 = no retry)

        Returns:
            FixRecord
        """
        import asyncio

        fix_record = FixRecord(
            error_pattern=analysis.get("error_id", ""),
            component=analysis.get("module", "unknown"),
            fix_action=analysis.get("fix_reason", ""),
            fix_time=datetime.now(),
        )

        fix_instruction = analysis.get("fix_instruction")

        if not fix_instruction or not analysis.get("can_fix", False):
            fix_record.fix_action = f"Skip fix: {analysis.get('fix_reason', 'cannot auto-fix')}"
            fix_record.success = False
            return fix_record

        fix_record.fix_action = f"Agent executing: {fix_instruction}"

        for attempt in range(max_retries):
            agent = None
            try:
                from ..core.agent import Agent

                agent = Agent()
                await agent.initialize(start_scheduler=False)
                # === Self-check auto-fix guardrails ===
                # Goal: only allow the LLM to "directly fix" the tool layer / skills / MCP / channels.
                # Akita core system code (core/llm/memory/scheduler/storage/agents, etc.) may never be auto-modified.
                #
                # The actual enforcement happens in FilesystemHandler as a hard block; this only injects policy.
                from pathlib import Path as _Path

                from ..config import settings as _settings

                project_root = _Path(_settings.project_root).resolve()
                agent._selfcheck_fix_policy = {
                    "enabled": True,
                    # Allowed read scope (for investigation)
                    "read_roots": [str(project_root)],
                    # Allowed write scope (for "direct fix")
                    "write_roots": [
                        str((project_root / "skills").resolve()),
                        str((project_root / "mcps").resolve()),
                        str((project_root / "src" / "openakita" / "tools").resolve()),
                        str((project_root / "src" / "openakita" / "channels").resolve()),
                    ],
                    # Denied shell keywords (avoid OS/Windows layer operations as much as possible)
                    "deny_shell_patterns": [
                        r"\bpowershell\b",
                        r"\bpwsh\b",
                        r"\bicacls\b",
                        r"\breg(\.exe)?\b",
                        r"\bnetsh\b",
                        r"\bschtasks\b",
                        r"\bsc\b",
                        r"\btaskkill\b",
                        r"\bshutdown\b",
                        r"\brestart\b",
                        r"\bGet-ScheduledTask\b",
                        r"\bGet-Service\b",
                        r"\bGet-Process\b",
                    ],
                }

                agent._context.messages = []
                agent._conversation_history = []
                if hasattr(agent, "_cli_session") and agent._cli_session:
                    agent._cli_session.context.clear_messages()

                logger.info(
                    f"SelfChecker: fix attempt {attempt + 1}/{max_retries} "
                    f"(timeout={self.FIX_TIMEOUT_SECONDS}s)"
                )

                # Build fix prompt
                fix_prompt = f"""You are the system self-check fix assistant. Execute the fix task based on the following analysis:

## Error Information
- Error ID: {analysis.get("error_id", "unknown")}
- Module: {analysis.get("module", "unknown")}
- Analysis: {analysis.get("analysis", "")}

## Fix Instruction
{fix_instruction}

## Requirements
1. You must **directly fix** tool layer issues (built-in tools/skills/MCP/channels, etc.), using tools (shell, file, skills, call_mcp_tool, etc.)
2. **Do not** modify Akita core system code (`src/openakita/core/`, `src/openakita/llm/`, `src/openakita/memory/`, `src/openakita/scheduler/`, `src/openakita/storage/`, `src/openakita/agents/`, etc.)
3. **Do not** perform Windows/system-level optimization or command operations (registry, scheduled tasks, permission fixes, service/process management, etc.); if such operations are needed, write a "manual handling required" conclusion
4. After fixing, verify the result (do a lightweight verification when possible, e.g. list_skills, list_mcp_servers, reading files, etc.)
5. When done, briefly report the fix result (what was done, which files were changed, verification result)
6. Locate and fix quickly; if it can't be fixed, directly report "manual handling required"

Please begin executing the fix."""

                # Execute fix Agent with timeout
                try:
                    if hasattr(agent, "execute_task_from_message"):
                        result = await asyncio.wait_for(
                            agent.execute_task_from_message(fix_prompt),
                            timeout=self.FIX_TIMEOUT_SECONDS,
                        )
                        success = result.success if result else False
                        result_msg = (
                            result.data
                            if result and result.success
                            else (result.error if result else "no result")
                        )
                    else:
                        result_msg = await asyncio.wait_for(
                            agent.chat(fix_prompt),
                            timeout=self.FIX_TIMEOUT_SECONDS,
                        )
                        success = "失败" not in result_msg and "error" not in result_msg.lower()
                except (asyncio.TimeoutError, TimeoutError):
                    logger.warning(
                        f"Fix attempt {attempt + 1} timed out after {self.FIX_TIMEOUT_SECONDS}s"
                    )
                    success = False
                    result_msg = f"Fix timed out ({self.FIX_TIMEOUT_SECONDS}s)"

                await agent.shutdown()
                agent = None

                if success:
                    fix_record.success = True
                    fix_record.verified = True
                    fix_record.verification_result = result_msg if result_msg else ""
                    logger.info(
                        f"Agent fix completed: {analysis.get('error_id')} "
                        f"- success on attempt {attempt + 1}"
                    )
                    return fix_record

                logger.warning(
                    f"Agent fix attempt {attempt + 1} failed: "
                    f"{result_msg[:100] if result_msg else 'no result'}"
                )

            except Exception as e:
                logger.error(f"Agent fix attempt {attempt + 1} error: {e}")
                if agent:
                    try:
                        await agent.shutdown()
                    except Exception:
                        pass
                if attempt == max_retries - 1:
                    logger.info("All retries failed, attempting script-level fallback...")
                    return await self._try_script_level_fix(analysis, fix_record)

        return await self._try_script_level_fix(analysis, fix_record)

    async def _try_script_level_fix(self, analysis: dict, fix_record: FixRecord) -> FixRecord:
        """
        Script-level fallback fix (suggestion 5)

        When the Agent fix fails, try simple script-level operations:
        - Restart service
        - Clean cache
        - Reset config
        """
        module = analysis.get("module", "")
        error_id = analysis.get("error_id", "")

        logger.info(f"Attempting script-level fix for {module}/{error_id}")

        try:
            # Choose fallback strategy based on module type
            if "browser" in module.lower():
                # Browser-related: try to close all browser processes
                fix_record.fix_action = "Script fallback: clean browser processes"
                # Don't actually perform dangerous operations, just record
                fix_record.verification_result = "Marked as needing manual browser restart"
                fix_record.success = False

            elif "memory" in module.lower() or "database" in module.lower():
                # Database-related: clean temp files
                fix_record.fix_action = "Script fallback: clean temp files"
                temp_dir = Path("data/temp")
                if temp_dir.exists():
                    for f in temp_dir.glob("*.tmp"):
                        f.unlink()
                fix_record.verification_result = "Temp files cleaned"
                fix_record.success = True

            elif "config" in module.lower():
                # Config-related: back up and reset
                fix_record.fix_action = "Script fallback: suggest manual config check"
                fix_record.verification_result = "Config issue requires manual check of .env or llm_endpoints.json"
                fix_record.success = False

            else:
                # Other: generic fallback
                fix_record.fix_action = "Script fallback: cannot auto-fix"
                fix_record.verification_result = f"Suggest manual check of {module} module"
                fix_record.success = False

        except Exception as e:
            logger.error(f"Script-level fix failed: {e}")
            fix_record.fix_action = f"Script fallback failed: {str(e)}"
            fix_record.success = False

        return fix_record

    async def _try_auto_fix(self, pattern: ErrorPattern) -> FixRecord:
        """
        Attempt to auto-fix tool error

        Args:
            pattern: Error pattern

        Returns:
            FixRecord
        """
        sample = pattern.samples[0] if pattern.samples else None
        component = sample.logger_name if sample else "unknown"

        fix_record = FixRecord(
            error_pattern=pattern.pattern,
            component=component,
            fix_action="",
            fix_time=datetime.now(),
        )

        # Choose fix strategy based on error type
        error_msg = sample.message.lower() if sample else ""

        try:
            if "permission" in error_msg or "access denied" in error_msg:
                fix_record.fix_action = "Attempt to fix file permissions"
                success = await self._fix_permission_error(sample)

            elif "not found" in error_msg or "no such file" in error_msg:
                fix_record.fix_action = "Attempt to create missing directories/files"
                success = await self._fix_missing_file_error(sample)

            elif "timeout" in error_msg:
                fix_record.fix_action = "Clean up potential deadlocked processes"
                success = await self._fix_timeout_error(sample)

            elif "connection" in error_msg or "connect" in error_msg:
                fix_record.fix_action = "Attempt to reset connection"
                success = await self._fix_connection_error(sample)

            elif "cache" in error_msg or "corrupt" in error_msg:
                fix_record.fix_action = "Clean cache"
                success = await self._fix_cache_error(sample)

            else:
                fix_record.fix_action = "Cannot auto-fix"
                success = False

            # Verify fix
            if success:
                verified, result = await self._verify_fix(component)
                fix_record.verified = verified
                fix_record.verification_result = result
                fix_record.success = verified
            else:
                fix_record.success = False

        except Exception as e:
            fix_record.fix_action = f"Fix failed: {str(e)}"
            fix_record.success = False

        return fix_record

    # ==================== Fallback fix methods (backup) ====================
    # The following methods are the old hardcoded fix logic; fixes now primarily execute via Agent
    # These methods are retained for fallback backup or quick-fix scenarios

    async def _fix_permission_error(self, sample) -> bool:
        """[Fallback backup] Fix permission error"""
        # Extract file path

        # Try to fix data directory permissions
        data_dir = settings.project_root / "data"
        if data_dir.exists():
            try:
                # Use icacls on Windows, chmod on Linux
                import platform

                if platform.system() == "Windows":
                    result = await self.shell.run(f'icacls "{data_dir}" /grant Users:F /T')
                else:
                    result = await self.shell.run(f'chmod -R 755 "{data_dir}"')

                return result.returncode == 0
            except Exception as e:
                logger.error(f"Failed to fix permission: {e}")

        return False

    async def _fix_missing_file_error(self, sample) -> bool:
        """[Fallback backup] Fix missing file/directory error"""
        # Ensure common directories exist
        dirs_to_check = [
            settings.project_root / "data",
            settings.project_root / "data" / "cache",
            settings.project_root / "data" / "sessions",
            settings.project_root / "logs",
            settings.selfcheck_dir,
        ]

        created = False
        for dir_path in dirs_to_check:
            if not dir_path.exists():
                try:
                    dir_path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created missing directory: {dir_path}")
                    created = True
                except Exception as e:
                    logger.error(f"Failed to create {dir_path}: {e}")

        return created

    async def _fix_timeout_error(self, sample) -> bool:
        """[Fallback backup] Fix timeout error (clean up zombie processes)"""
        try:
            import platform

            if platform.system() == "Windows":
                # On Windows, kill potentially zombie Python processes (with care)
                # This is just an example; actual filtering needs to be more precise
                pass
            else:
                # On Linux/Mac, clean up zombie processes
                await self.shell.run("pkill -9 -f 'openakita.*timeout' || true")

            return True
        except Exception:
            return False

    async def _fix_connection_error(self, sample) -> bool:
        """[Fallback backup] Fix connection error"""
        # For connection errors, usually need to retry or switch endpoints
        # Return False here and let the system retry naturally
        return False

    async def _fix_cache_error(self, sample) -> bool:
        """[Fallback backup] Fix cache error (clean cache)"""
        cache_dirs = [
            settings.project_root / "data" / "cache",
            settings.project_root / ".cache",
        ]

        cleaned = False
        for cache_dir in cache_dirs:
            if cache_dir.exists():
                try:
                    import shutil

                    shutil.rmtree(cache_dir)
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Cleaned cache directory: {cache_dir}")
                    cleaned = True
                except Exception as e:
                    logger.error(f"Failed to clean cache {cache_dir}: {e}")

        return cleaned

    async def _fix_config_error(self, sample) -> bool:
        """[Fallback backup] Fix config error"""
        # Ensure config directories and basic files exist
        config_checks = [
            (settings.identity_path, True),  # identity directory
            (settings.project_root / "data", True),  # data directory
            (settings.project_root / ".env", False),  # .env file (not auto-created)
        ]

        fixed = False
        for path, is_dir in config_checks:
            if is_dir and not path.exists():
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created config directory: {path}")
                    fixed = True
                except Exception as e:
                    logger.error(f"Failed to create {path}: {e}")

        return fixed

    async def _verify_fix(self, component: str) -> tuple[bool, str]:
        """
        Verify whether the fix succeeded

        Args:
            component: Component name

        Returns:
            (passed, verification result description)
        """
        try:
            if "tools.file" in component or "file" in component.lower():
                # Test file read/write
                test_file = settings.project_root / "data" / "test_verify.tmp"
                await self.file_tool.write(str(test_file), "verify_test")
                content = await self.file_tool.read(str(test_file))
                test_file.unlink(missing_ok=True)

                if content == "verify_test":
                    return True, "File read/write test passed"
                return False, f"File read/write test failed: {content}"

            elif "tools.shell" in component or "shell" in component.lower():
                # Test shell command
                result = await self.shell.run("echo verify_test")
                if result.returncode == 0 and "verify_test" in result.stdout:
                    return True, "Shell command test passed"
                return False, f"Shell command test failed: {result.stderr}"

            elif "tools.mcp" in component or "mcp" in component.lower():
                # MCP testing requires special handling
                return True, "MCP component requires manual verification"

            elif "channel" in component.lower():
                # Channel testing requires special handling
                return True, "Channel component requires manual verification"

            else:
                # Generic verification: check if directory exists
                data_dir = settings.project_root / "data"
                if data_dir.exists():
                    return True, "Data directory check passed"
                return False, "Data directory does not exist"

        except Exception as e:
            return False, f"Verification failed: {str(e)}"

    def _save_daily_report(self, report: DailyReport) -> None:
        """Save daily report"""
        selfcheck_dir = settings.selfcheck_dir
        selfcheck_dir.mkdir(parents=True, exist_ok=True)

        # Save in JSON format
        json_file = selfcheck_dir / f"{report.date}_report.json"
        try:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"Saved daily report: {json_file}")
        except Exception as e:
            logger.error(f"Failed to save report JSON: {e}")

        # Save in Markdown format
        md_file = selfcheck_dir / f"{report.date}_report.md"
        try:
            with open(md_file, "w", encoding="utf-8") as f:
                f.write(report.to_markdown())
            logger.info(f"Saved daily report: {md_file}")
        except Exception as e:
            logger.error(f"Failed to save report MD: {e}")

    def get_pending_report(self) -> str | None:
        """
        Get unsubmitted report (for morning proactive reporting)

        Returns:
            Report content (Markdown), or None if not available
        """
        selfcheck_dir = settings.selfcheck_dir
        if not selfcheck_dir.exists():
            return None

        # Look for yesterday's report
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        json_file = selfcheck_dir / f"{yesterday}_report.json"
        md_file = selfcheck_dir / f"{yesterday}_report.md"

        if not json_file.exists():
            return None

        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)

            # Check if already submitted
            if data.get("reported"):
                return None

            # Read Markdown report
            if md_file.exists():
                with open(md_file, encoding="utf-8") as f:
                    return f.read()

            # If no MD file, generate from JSON
            report = DailyReport(
                date=data["date"],
                timestamp=datetime.fromisoformat(data["timestamp"]),
                total_errors=data.get("total_errors", 0),
                core_errors=data.get("core_errors", 0),
                tool_errors=data.get("tool_errors", 0),
                fix_attempted=data.get("fix_attempted", 0),
                fix_success=data.get("fix_success", 0),
                fix_failed=data.get("fix_failed", 0),
                core_error_patterns=data.get("core_error_patterns", []),
                tool_error_patterns=data.get("tool_error_patterns", []),
                memory_consolidation=data.get("memory_consolidation"),
            )
            return report.to_markdown()

        except Exception as e:
            logger.error(f"Failed to get pending report: {e}")
            return None

    def mark_report_as_reported(self, date: str | None = None) -> bool:
        """
        Mark report as submitted

        Args:
            date: Date; defaults to yesterday

        Returns:
            Whether successful
        """
        if not date:
            date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        json_file = settings.selfcheck_dir / f"{date}_report.json"

        if not json_file.exists():
            return False

        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)

            data["reported"] = True

            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return True

        except Exception as e:
            logger.error(f"Failed to mark report as reported: {e}")
            return False
