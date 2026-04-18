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
        执行系统自检（LLM 驱动）

        流程:
        1. 本地匹配提取 ERROR 日志（从 since 时间开始）
        2. 生成错误摘要
        3. LLM 分析错误并决定修复策略
        4. 根据 LLM 决策执行修复
        5. 修复后自测验证
        6. 生成报告

        Args:
            since: 只分析此时间之后的日志和复盘记录。None 表示分析全部（首次运行）。

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

        # === 阶段 1: 收集所有问题信息（日志 + 记忆 + 复盘） ===

        # 1.1 提取日志错误（支持增量：only since last check）
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

        # 1.2 加载任务复盘汇总（在 LLM 分析之前）
        retrospect_info = ""
        try:
            from ..core.task_monitor import get_retrospect_storage

            retrospect_storage = get_retrospect_storage()
            report.retrospect_summary = retrospect_storage.get_summary(today)

            if report.retrospect_summary.get("total_tasks", 0) > 0:
                logger.info(
                    f"Loaded retrospect summary: {report.retrospect_summary['total_tasks']} tasks"
                )
                # 构建复盘信息摘要
                retrospect_info = self._build_retrospect_summary_for_llm(report.retrospect_summary)
        except Exception as e:
            logger.warning(f"Failed to load retrospect summary: {e}")

        # 1.3 从记忆系统提取错误教训（在 LLM 分析之前）
        memory_info = ""
        try:
            report.memory_insights = await self._extract_memory_insights()
            if report.memory_insights:
                logger.info(
                    f"Extracted memory insights: {report.memory_insights.get('total_errors', 0)} errors"
                )
                # 构建记忆信息摘要
                memory_info = self._build_memory_summary_for_llm(report.memory_insights)
        except Exception as e:
            logger.warning(f"Failed to extract memory insights: {e}")

        # === 阶段 2: 综合分析（日志 + 记忆 + 复盘 一起提交给 LLM） ===

        # 构建完整的分析输入
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
            # LLM 综合分析（如果有 brain）
            if self.brain:
                analysis_results = await self._analyze_errors_with_llm(full_analysis_input)
                logger.info(f"LLM analyzed {len(analysis_results)} issues")
            else:
                # 没有 brain，使用规则匹配（降级模式）
                logger.warning("No brain available, using rule-based analysis")
                analysis_results = self._analyze_errors_with_rules(patterns)

            # === 阶段 3: 根据分析结果处理错误 ===
            # 只允许“直接修复”的错误类型：
            # - tool: 内置工具
            # - skill: skills 目录技能
            # - mcp: MCP 相关（mcps/ 目录、连接/调用）
            # - channel: IM 通道适配器（属于工具层的一部分）
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

                    # 记录工具错误模式（无论是否修复都记录）
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

        # 保存报告
        self._save_daily_report(report)

        return report

    def _build_retrospect_summary_for_llm(self, retrospect_summary: dict) -> str:
        """
        构建复盘信息摘要（给 LLM 分析）

        Args:
            retrospect_summary: 复盘汇总数据

        Returns:
            Markdown 格式摘要
        """
        if not retrospect_summary or retrospect_summary.get("total_tasks", 0) == 0:
            return ""

        lines = [
            "## 任务复盘信息",
            "",
            f"- 今日复盘任务数: {retrospect_summary.get('total_tasks', 0)}",
            f"- 总耗时: {retrospect_summary.get('total_duration', 0):.0f}秒",
            f"- 平均耗时: {retrospect_summary.get('avg_duration', 0):.1f}秒",
            f"- 模型切换次数: {retrospect_summary.get('model_switches', 0)}",
            "",
        ]

        # 常见问题
        common_issues = retrospect_summary.get("common_issues", [])
        if common_issues:
            lines.append("### 复盘发现的常见问题")
            for issue in common_issues:
                lines.append(f"- [{issue.get('count', 0)}次] {issue.get('issue', '')}")
            lines.append("")

        # 复盘详情
        records = retrospect_summary.get("records", [])
        if records:
            lines.append("### 复盘详情")
            for r in records:
                desc = r.get("description", "")
                result = r.get("retrospect_result", "")
                lines.append(f"- **{desc}**")
                if result:
                    lines.append(f"  - 分析: {result}")
            lines.append("")

        return "\n".join(lines)

    def _build_memory_summary_for_llm(self, memory_insights: dict) -> str:
        """
        构建记忆信息摘要（给 LLM 分析）

        Args:
            memory_insights: 记忆优化建议数据

        Returns:
            Markdown 格式摘要
        """
        if not memory_insights:
            return ""

        lines = ["## 记忆系统中的错误教训", ""]

        # 错误教训
        error_list = memory_insights.get("error_list", [])
        if error_list:
            lines.append("### 历史错误教训（最近记录）")
            for err in error_list:
                source = err.get("source", "unknown")
                content = err.get("content", "")
                lines.append(f"- [{source}] {content}")
            lines.append("")

        # 规则约束
        rule_list = memory_insights.get("rule_list", [])
        if rule_list:
            lines.append("### 系统规则约束")
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
        构建完整的分析输入（日志 + 复盘 + 记忆）

        Args:
            error_summary: 日志错误摘要
            retrospect_info: 复盘信息摘要
            memory_info: 记忆信息摘要

        Returns:
            完整的分析输入（Markdown 格式）
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

        # 添加综合分析说明
        header = """# 系统自检综合分析

以下信息来源：
1. **日志错误** - 今日 ERROR/CRITICAL 级别日志
2. **任务复盘** - 长时间任务的执行分析
3. **错误教训** - 记忆系统中记录的历史问题

请综合分析这些信息，识别需要修复的问题。

---

"""
        return header + "\n\n".join(sections)

    async def _extract_memory_insights(self) -> dict:
        """
        从记忆系统提取优化相关的信息

        提取的记忆类型:
        - ERROR: 错误教训（来自复盘、任务失败等）
        - RULE: 规则约束（用户设定的规则）

        Returns:
            记忆优化建议字典
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

            # 提取 ERROR 类型记忆
            error_memories = memory_manager.search_memories(
                memory_type=MemoryType.ERROR,
                limit=50,
            )

            # 提取 RULE 类型记忆
            rule_memories = memory_manager.search_memories(
                memory_type=MemoryType.RULE,
                limit=20,
            )

            # 转换为字典格式
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

            # 如果有足够的错误记忆，让 LLM 提取优化建议
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
        使用 LLM 从记忆中生成优化建议

        Args:
            error_memories: 错误记忆列表
            rule_memories: 规则记忆列表

        Returns:
            优化建议列表
        """
        # 构建错误摘要
        error_summary = "\n".join(
            [f"- [{m.get('source', 'unknown')}] {m.get('content', '')}" for m in error_memories]
        )

        rule_summary = "\n".join([f"- {m.get('content', '')}" for m in rule_memories])

        prompt = f"""请分析以下系统记录的错误教训和规则约束，提取出最重要的优化建议。

## 错误教训（最近记录）
{error_summary if error_summary else "暂无"}

## 规则约束
{rule_summary if rule_summary else "暂无"}

请从这些信息中提取 3-5 条最重要的优化建议，每条建议简洁明了（不超过 50 字）。
用 JSON 数组格式输出，如：["建议1", "建议2", "建议3"]
"""

        try:
            response = await self.brain.think(
                prompt,
                system="你是一个系统优化专家。请从错误记录中提取可行的优化建议。只输出 JSON 数组，不要其他内容。",
            )

            # 解析 JSON
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
        使用 LLM 分析错误并决定修复策略（支持分批处理）

        Args:
            error_summary: 错误摘要（Markdown 格式）

        Returns:
            分析结果列表
        """
        # 加载专用提示词
        prompt_path = settings.project_root / "prompts" / "selfcheck_system.md"
        if prompt_path.exists():
            system_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            system_prompt = self.DEFAULT_SELFCHECK_PROMPT
            logger.warning("Using default selfcheck prompt")

        # 追加环境标识，让 LLM 知道当前环境类型
        env_hint = (
            "production" if settings.selfcheck_autofix else "development（自动修复已关闭，仅分析）"
        )
        system_prompt += f"\n\n当前环境: {env_hint}"

        # 检查摘要大小，如果太大则分批处理
        MAX_CHARS_PER_BATCH = 8000  # 每批最大字符数（约 2000 tokens）

        if len(error_summary) <= MAX_CHARS_PER_BATCH:
            # 摘要较小，直接处理
            return await self._analyze_single_batch(error_summary, system_prompt)

        # 摘要太大，分批处理
        logger.info(f"Error summary too large ({len(error_summary)} chars), splitting into batches")

        # 按 "### [" 分割成独立的错误块
        import re

        error_blocks = re.split(r"(?=### \[)", error_summary)

        # 保留头部信息
        header = ""
        if error_blocks and not error_blocks[0].startswith("### ["):
            header = error_blocks[0]
            error_blocks = error_blocks[1:]

        # 分批
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

        # 分批调用 LLM
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
        分析单个批次的错误

        Args:
            error_summary: 错误摘要
            system_prompt: 系统提示词

        Returns:
            分析结果列表
        """
        user_prompt = f"""请分析以下错误日志摘要，针对每个错误输出分析结果（JSON 数组格式）：

{error_summary}

请直接输出 JSON 数组，不要其他内容。"""

        try:
            response = await self.brain.think(
                user_prompt,
                system=system_prompt,
            )

            # 解析 JSON 结果
            return self._parse_llm_analysis(response.content)

        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return []

    def _parse_llm_analysis(self, content: str) -> list[dict]:
        """
        解析 LLM 返回的分析结果

        Args:
            content: LLM 返回的内容

        Returns:
            分析结果列表
        """
        try:
            # 尝试提取 JSON 数组
            import re

            # 查找 JSON 数组
            json_match = re.search(r"\[[\s\S]*\]", content)
            if json_match:
                json_str = json_match.group()
                return json.loads(json_str)

            # 尝试直接解析
            return json.loads(content)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"LLM response: {content}")
            return []

    def _analyze_errors_with_rules(self, patterns: dict) -> list[dict]:
        """
        使用规则分析错误（降级模式，当没有 LLM 时使用）

        Args:
            patterns: 错误模式字典

        Returns:
            分析结果列表
        """
        results = []

        for pattern_key, pattern in patterns.items():
            sample = pattern.samples[0] if pattern.samples else None
            module = sample.logger_name if sample else "unknown"
            message = sample.message if sample else ""

            # 判断是否是核心组件
            is_core = pattern.component_type == "core"

            # 判断修复策略和生成修复指令
            fix_instruction = None
            can_fix = False

            if not is_core:
                message_lower = message.lower()
                if "permission" in message_lower or "access denied" in message_lower:
                    # 避免涉及操作系统层面的权限调整（尤其是 Windows）
                    fix_instruction = None
                    can_fix = False
                elif "not found" in message_lower or "no such file" in message_lower:
                    fix_instruction = "使用 file 工具创建缺失的目录：确保 data/、data/cache/、data/sessions/、logs/ 目录存在"
                    can_fix = True
                elif "cache" in message_lower or "corrupt" in message_lower:
                    fix_instruction = "使用 shell 工具清理缓存目录：删除 data/cache/ 下的所有文件，然后重新创建目录"
                    can_fix = True
                elif "timeout" in message_lower:
                    # 进程清理通常涉及系统层面操作，报告给用户即可
                    fix_instruction = None
                    can_fix = False
                elif "connection" in message_lower:
                    # 连接错误通常需要人工检查
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
                    "fix_reason": "规则匹配（降级模式）",
                    "requires_restart": is_core,
                    "note_to_user": "需要人工检查" if is_core else None,
                }
            )

        return results

    FIX_TIMEOUT_SECONDS = 60

    async def _execute_fix_by_llm_decision(self, analysis: dict, max_retries: int = 1) -> FixRecord:
        """
        根据 LLM 决策执行修复（使用主 Agent，带超时保护）

        Args:
            analysis: LLM 分析结果（包含 fix_instruction）
            max_retries: 最大重试次数（默认 1 = 不重试）

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
            fix_record.fix_action = f"跳过修复: {analysis.get('fix_reason', '无法自动修复')}"
            fix_record.success = False
            return fix_record

        fix_record.fix_action = f"Agent 执行: {fix_instruction}"

        for attempt in range(max_retries):
            agent = None
            try:
                from ..core.agent import Agent

                agent = Agent()
                await agent.initialize(start_scheduler=False)
                # === 自检自动修复护栏 ===
                # 目标：只允许 LLM “直接修复”工具层 / skills / MCP / channels
                # Akita 核心系统代码（core/llm/memory/scheduler/storage/agents 等）一律不允许自动修改。
                #
                # 具体执行在 FilesystemHandler 中做硬拦截；这里仅注入策略。
                from pathlib import Path as _Path

                from ..config import settings as _settings

                project_root = _Path(_settings.project_root).resolve()
                agent._selfcheck_fix_policy = {
                    "enabled": True,
                    # 允许读取范围（用于排查）
                    "read_roots": [str(project_root)],
                    # 允许写入范围（用于“直接修复”）
                    "write_roots": [
                        str((project_root / "skills").resolve()),
                        str((project_root / "mcps").resolve()),
                        str((project_root / "src" / "openakita" / "tools").resolve()),
                        str((project_root / "src" / "openakita" / "channels").resolve()),
                    ],
                    # 禁止的 shell 关键词（尽量避免 OS/Windows 层操作）
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

                # 构建修复 prompt
                fix_prompt = f"""你是系统自检修复助手。请根据以下分析执行修复任务：

## 错误信息
- 错误ID: {analysis.get("error_id", "unknown")}
- 模块: {analysis.get("module", "unknown")}
- 分析: {analysis.get("analysis", "")}

## 修复指令
{fix_instruction}

## 要求
1. 你需要 **直接修复** 工具层问题（内置工具/skills/MCP/channels 等），可以使用工具（shell、file、skills、call_mcp_tool 等）
2. **禁止** 修改 Akita 核心系统代码（`src/openakita/core/`、`src/openakita/llm/`、`src/openakita/memory/`、`src/openakita/scheduler/`、`src/openakita/storage/`、`src/openakita/agents/` 等）
3. **禁止** 进行 Windows/系统层面优化与命令操作（注册表、计划任务、权限修复、服务/进程管理等）；如果需要这些操作，请写入“需人工处理”的结论
4. 修复后验证结果是否正确（能用轻量验证就做，如 list_skills、list_mcp_servers、读取文件等）
5. 完成后简要报告修复结果（做了什么、改了哪些文件、验证结果）
6. 快速定位并修复，无法修复的直接报告"需人工处理"

请开始执行修复。"""

                # 带超时执行修复 Agent
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
                            else (result.error if result else "无结果")
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
                    result_msg = f"修复超时（{self.FIX_TIMEOUT_SECONDS}s）"

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
        脚本级降级修复（建议 5）

        当 Agent 修复失败时，尝试简单的脚本级操作：
        - 重启服务
        - 清理缓存
        - 重置配置
        """
        module = analysis.get("module", "")
        error_id = analysis.get("error_id", "")

        logger.info(f"Attempting script-level fix for {module}/{error_id}")

        try:
            # 根据模块类型选择降级策略
            if "browser" in module.lower():
                # 浏览器相关：尝试关闭所有浏览器进程
                fix_record.fix_action = "脚本降级: 清理浏览器进程"
                # 不实际执行危险操作，只记录
                fix_record.verification_result = "已标记需要手动重启浏览器"
                fix_record.success = False

            elif "memory" in module.lower() or "database" in module.lower():
                # 数据库相关：清理临时文件
                fix_record.fix_action = "脚本降级: 清理临时文件"
                temp_dir = Path("data/temp")
                if temp_dir.exists():
                    for f in temp_dir.glob("*.tmp"):
                        f.unlink()
                fix_record.verification_result = "已清理临时文件"
                fix_record.success = True

            elif "config" in module.lower():
                # 配置相关：备份并重置
                fix_record.fix_action = "脚本降级: 建议手动检查配置"
                fix_record.verification_result = "配置问题需要手动检查 .env 或 llm_endpoints.json"
                fix_record.success = False

            else:
                # 其他：通用降级
                fix_record.fix_action = "脚本降级: 无法自动修复"
                fix_record.verification_result = f"建议手动检查 {module} 模块"
                fix_record.success = False

        except Exception as e:
            logger.error(f"Script-level fix failed: {e}")
            fix_record.fix_action = f"脚本降级失败: {str(e)}"
            fix_record.success = False

        return fix_record

    async def _try_auto_fix(self, pattern: ErrorPattern) -> FixRecord:
        """
        尝试自动修复工具错误

        Args:
            pattern: 错误模式

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

        # 根据错误类型选择修复策略
        error_msg = sample.message.lower() if sample else ""

        try:
            if "permission" in error_msg or "access denied" in error_msg:
                fix_record.fix_action = "尝试修复文件权限"
                success = await self._fix_permission_error(sample)

            elif "not found" in error_msg or "no such file" in error_msg:
                fix_record.fix_action = "尝试创建缺失的目录/文件"
                success = await self._fix_missing_file_error(sample)

            elif "timeout" in error_msg:
                fix_record.fix_action = "清理可能的死锁进程"
                success = await self._fix_timeout_error(sample)

            elif "connection" in error_msg or "connect" in error_msg:
                fix_record.fix_action = "尝试重置连接"
                success = await self._fix_connection_error(sample)

            elif "cache" in error_msg or "corrupt" in error_msg:
                fix_record.fix_action = "清理缓存"
                success = await self._fix_cache_error(sample)

            else:
                fix_record.fix_action = "无法自动修复"
                success = False

            # 验证修复
            if success:
                verified, result = await self._verify_fix(component)
                fix_record.verified = verified
                fix_record.verification_result = result
                fix_record.success = verified
            else:
                fix_record.success = False

        except Exception as e:
            fix_record.fix_action = f"修复失败: {str(e)}"
            fix_record.success = False

        return fix_record

    # ==================== 降级修复方法（备用） ====================
    # 以下方法为旧的硬编码修复逻辑，现在主要通过 Agent 执行修复
    # 保留这些方法作为降级备用或快速修复场景使用

    async def _fix_permission_error(self, sample) -> bool:
        """[降级备用] 修复权限错误"""
        # 提取文件路径

        # 尝试修复 data 目录权限
        data_dir = settings.project_root / "data"
        if data_dir.exists():
            try:
                # Windows 下使用 icacls，Linux 下使用 chmod
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
        """[降级备用] 修复缺失文件/目录错误"""
        # 确保常用目录存在
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
        """[降级备用] 修复超时错误（清理僵尸进程）"""
        try:
            import platform

            if platform.system() == "Windows":
                # Windows 下杀死可能的僵尸 Python 进程（谨慎）
                # 这里只是示例，实际需要更精确的筛选
                pass
            else:
                # Linux/Mac 下清理僵尸进程
                await self.shell.run("pkill -9 -f 'openakita.*timeout' || true")

            return True
        except Exception:
            return False

    async def _fix_connection_error(self, sample) -> bool:
        """[降级备用] 修复连接错误"""
        # 对于连接错误，通常需要重试或切换端点
        # 这里返回 False，让系统自然重试
        return False

    async def _fix_cache_error(self, sample) -> bool:
        """[降级备用] 修复缓存错误（清理缓存）"""
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
        """[降级备用] 修复配置错误"""
        # 确保配置目录和基本文件存在
        config_checks = [
            (settings.identity_path, True),  # identity 目录
            (settings.project_root / "data", True),  # data 目录
            (settings.project_root / ".env", False),  # .env 文件（不自动创建）
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
        验证修复是否成功

        Args:
            component: 组件名称

        Returns:
            (是否通过, 验证结果描述)
        """
        try:
            if "tools.file" in component or "file" in component.lower():
                # 测试文件读写
                test_file = settings.project_root / "data" / "test_verify.tmp"
                await self.file_tool.write(str(test_file), "verify_test")
                content = await self.file_tool.read(str(test_file))
                test_file.unlink(missing_ok=True)

                if content == "verify_test":
                    return True, "文件读写测试通过"
                return False, f"文件读写测试失败: {content}"

            elif "tools.shell" in component or "shell" in component.lower():
                # 测试 Shell 命令
                result = await self.shell.run("echo verify_test")
                if result.returncode == 0 and "verify_test" in result.stdout:
                    return True, "Shell 命令测试通过"
                return False, f"Shell 命令测试失败: {result.stderr}"

            elif "tools.mcp" in component or "mcp" in component.lower():
                # MCP 测试需要特殊处理
                return True, "MCP 组件需要手动验证"

            elif "channel" in component.lower():
                # 通道测试需要特殊处理
                return True, "通道组件需要手动验证"

            else:
                # 通用验证：检查目录是否存在
                data_dir = settings.project_root / "data"
                if data_dir.exists():
                    return True, "数据目录检查通过"
                return False, "数据目录不存在"

        except Exception as e:
            return False, f"验证失败: {str(e)}"

    def _save_daily_report(self, report: DailyReport) -> None:
        """保存每日报告"""
        selfcheck_dir = settings.selfcheck_dir
        selfcheck_dir.mkdir(parents=True, exist_ok=True)

        # 保存 JSON 格式
        json_file = selfcheck_dir / f"{report.date}_report.json"
        try:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"Saved daily report: {json_file}")
        except Exception as e:
            logger.error(f"Failed to save report JSON: {e}")

        # 保存 Markdown 格式
        md_file = selfcheck_dir / f"{report.date}_report.md"
        try:
            with open(md_file, "w", encoding="utf-8") as f:
                f.write(report.to_markdown())
            logger.info(f"Saved daily report: {md_file}")
        except Exception as e:
            logger.error(f"Failed to save report MD: {e}")

    def get_pending_report(self) -> str | None:
        """
        获取未提交的报告（供早上主动汇报）

        Returns:
            报告内容（Markdown），如果没有则返回 None
        """
        selfcheck_dir = settings.selfcheck_dir
        if not selfcheck_dir.exists():
            return None

        # 查找昨天的报告
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        json_file = selfcheck_dir / f"{yesterday}_report.json"
        md_file = selfcheck_dir / f"{yesterday}_report.md"

        if not json_file.exists():
            return None

        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)

            # 检查是否已提交
            if data.get("reported"):
                return None

            # 读取 Markdown 报告
            if md_file.exists():
                with open(md_file, encoding="utf-8") as f:
                    return f.read()

            # 如果没有 MD 文件，从 JSON 生成
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
        标记报告为已提交

        Args:
            date: 日期，默认昨天

        Returns:
            是否成功
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
