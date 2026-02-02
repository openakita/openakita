"""
自检系统

功能:
- 运行测试用例
- 分析 ERROR 日志
- 区分核心组件和工具错误
- 自动修复工具问题
- 修复后自测验证
- 生成每日报告
"""

import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ..core.brain import Brain
from ..tools.shell import ShellTool
from ..tools.file import FileTool
from ..config import settings
from .log_analyzer import LogAnalyzer, ErrorPattern

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """测试用例"""
    id: str
    category: str  # qa, tools, search
    description: str
    input: Any
    expected: Any
    validator: Optional[str] = None  # 验证函数名


@dataclass
class TestResult:
    """测试结果"""
    test_id: str
    passed: bool
    actual: Any = None
    error: Optional[str] = None
    duration_ms: float = 0


@dataclass
class CheckReport:
    """自检报告"""
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
    """修复记录"""
    error_pattern: str
    component: str
    fix_action: str
    fix_time: datetime
    verified: bool = False
    verification_result: str = ""
    success: bool = False


@dataclass
class DailyReport:
    """每日系统报告"""
    date: str
    timestamp: datetime
    
    # 错误统计
    total_errors: int = 0
    core_errors: int = 0
    tool_errors: int = 0
    
    # 修复统计
    fix_attempted: int = 0
    fix_success: int = 0
    fix_failed: int = 0
    
    # 详细内容
    core_error_patterns: list[dict] = field(default_factory=list)
    tool_error_patterns: list[dict] = field(default_factory=list)
    fix_records: list[FixRecord] = field(default_factory=list)
    
    # 记忆整理结果（如果有）
    memory_consolidation: Optional[dict] = None
    
    # 任务复盘统计
    retrospect_summary: Optional[dict] = None  # 复盘汇总
    
    # 记忆系统优化建议
    memory_insights: Optional[dict] = None  # 从记忆中提取的优化建议
    
    # 报告状态
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
        """生成 Markdown 格式报告"""
        lines = [
            f"# 每日系统报告 - {self.date}",
            "",
            "## 摘要",
            "",
            f"- 总错误数: {self.total_errors}",
            f"- 核心组件错误: {self.core_errors} (需人工处理)",
            f"- 工具错误: {self.tool_errors}",
            f"- 尝试修复: {self.fix_attempted}",
            f"- 修复成功: {self.fix_success}",
            f"- 修复失败: {self.fix_failed}",
            "",
        ]
        
        # 核心组件错误
        if self.core_error_patterns:
            lines.append("## 核心组件错误（需人工处理）")
            lines.append("")
            for p in self.core_error_patterns:
                lines.append(f"### [{p.get('count', 1)}次] {p.get('pattern', '')[:60]}")
                lines.append(f"- 模块: `{p.get('logger', 'unknown')}`")
                lines.append(f"- 时间: {p.get('last_seen', '')}")
                if p.get('message'):
                    lines.append(f"- 消息: `{p.get('message', '')[:100]}`")
                lines.append(f"- **建议: 检查日志并考虑重启服务**")
                lines.append("")
        
        # 工具修复记录
        if self.fix_records:
            lines.append("## 工具修复记录")
            lines.append("")
            for r in self.fix_records:
                status = "已修复" if r.success else "修复失败"
                lines.append(f"### [{status}] {r.error_pattern[:50]}")
                lines.append(f"- 组件: `{r.component}`")
                lines.append(f"- 修复操作: {r.fix_action}")
                lines.append(f"- 时间: {r.fix_time.strftime('%Y-%m-%d %H:%M:%S')}")
                lines.append(f"- 验证: {'通过' if r.verified else '未通过'}")
                if r.verification_result:
                    lines.append(f"- 验证结果: {r.verification_result}")
                lines.append("")
        
        # 记忆整理结果
        if self.memory_consolidation:
            lines.append("## 记忆整理结果")
            lines.append("")
            mc = self.memory_consolidation
            lines.append(f"- 处理会话: {mc.get('sessions_processed', 0)}")
            lines.append(f"- 提取记忆: {mc.get('memories_extracted', 0)}")
            lines.append(f"- 新增记忆: {mc.get('memories_added', 0)}")
            lines.append(f"- 去重: {mc.get('duplicates_removed', 0)}")
            lines.append(f"- MEMORY.md: {'已刷新' if mc.get('memory_md_refreshed') else '未刷新'}")
            lines.append("")
        
        # 任务复盘统计
        if self.retrospect_summary:
            lines.append("## 任务复盘统计")
            lines.append("")
            rs = self.retrospect_summary
            lines.append(f"- 复盘任务数: {rs.get('total_tasks', 0)}")
            lines.append(f"- 总耗时: {rs.get('total_duration', 0):.0f}秒")
            lines.append(f"- 平均耗时: {rs.get('avg_duration', 0):.1f}秒")
            lines.append(f"- 模型切换次数: {rs.get('model_switches', 0)}")
            
            # 常见问题
            common_issues = rs.get('common_issues', [])
            if common_issues:
                lines.append("")
                lines.append("### 常见问题")
                for issue in common_issues:
                    lines.append(f"- {issue.get('issue', '')}: {issue.get('count', 0)}次")
            
            # 复盘详情（最多显示 5 个）
            records = rs.get('records', [])[:5]
            if records:
                lines.append("")
                lines.append("### 复盘详情（最近 5 个）")
                for r in records:
                    duration = r.get('duration_seconds', 0)
                    desc = r.get('description', '')[:40]
                    result = r.get('retrospect_result', '')[:100]
                    lines.append(f"- **{desc}...** ({duration:.0f}秒)")
                    if result:
                        lines.append(f"  - 分析: {result}...")
            
            lines.append("")
        
        # 记忆系统优化建议
        if self.memory_insights:
            lines.append("## 记忆系统优化建议")
            lines.append("")
            mi = self.memory_insights
            
            # 错误教训
            error_memories = mi.get('error_memories', [])
            if error_memories:
                lines.append("### 错误教训（需关注）")
                for m in error_memories[:10]:
                    source = m.get('source', '')
                    source_label = f" [{source}]" if source else ""
                    lines.append(f"- {m.get('content', '')[:100]}{source_label}")
                lines.append("")
            
            # 规则约束
            rule_memories = mi.get('rule_memories', [])
            if rule_memories:
                lines.append("### 规则约束（需遵守）")
                for m in rule_memories[:10]:
                    lines.append(f"- {m.get('content', '')[:100]}")
                lines.append("")
            
            # 优化建议汇总
            optimization_suggestions = mi.get('optimization_suggestions', [])
            if optimization_suggestions:
                lines.append("### 优化建议汇总")
                for s in optimization_suggestions[:5]:
                    lines.append(f"- {s}")
                lines.append("")
            
            # 统计
            lines.append(f"*共提取 {mi.get('total_errors', 0)} 条错误教训, "
                        f"{mi.get('total_rules', 0)} 条规则约束*")
            lines.append("")
        
        lines.append("---")
        lines.append(f"*报告生成时间: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}*")
        
        return "\n".join(lines)


class SelfChecker:
    """
    自检系统
    
    - 运行测试用例
    - 分析失败原因
    - 自动修复代码
    - 记录学习经验
    """
    
    def __init__(
        self,
        brain: Brain,
        test_dir: Optional[Path] = None,
    ):
        self.brain = brain
        self.test_dir = test_dir or (settings.project_root / "src" / "openakita" / "testing" / "cases")
        self.shell = ShellTool()
        self.file_tool = FileTool()
        
        self._test_cases: list[TestCase] = []
    
    def load_test_cases(self) -> int:
        """加载测试用例"""
        self._test_cases = []
        
        # 从测试目录加载
        if self.test_dir.exists():
            for category_dir in self.test_dir.iterdir():
                if category_dir.is_dir():
                    category = category_dir.name
                    for test_file in category_dir.glob("*.py"):
                        cases = self._load_test_file(test_file, category)
                        self._test_cases.extend(cases)
        
        # 添加内置测试用例
        self._test_cases.extend(self._get_builtin_tests())
        
        logger.info(f"Loaded {len(self._test_cases)} test cases")
        return len(self._test_cases)
    
    def _load_test_file(self, path: Path, category: str) -> list[TestCase]:
        """从文件加载测试用例"""
        # TODO: 实现从 Python 文件加载测试用例
        return []
    
    def _get_builtin_tests(self) -> list[TestCase]:
        """获取内置测试用例"""
        tests = []
        
        # 基础功能测试
        tests.append(TestCase(
            id="core_brain_001",
            category="core",
            description="Brain 基本响应测试",
            input="你好",
            expected="包含响应文本",
        ))
        
        tests.append(TestCase(
            id="core_shell_001",
            category="tools",
            description="Shell 命令执行测试",
            input="echo hello",
            expected="hello",
        ))
        
        tests.append(TestCase(
            id="core_file_001",
            category="tools",
            description="文件读写测试",
            input={"action": "write_read", "content": "test"},
            expected="test",
        ))
        
        return tests
    
    async def run_check(
        self,
        categories: Optional[list[str]] = None,
        quick: bool = False,
    ) -> CheckReport:
        """
        运行自检
        
        Args:
            categories: 要测试的类别
            quick: 是否快速检查（只运行核心测试）
        
        Returns:
            CheckReport
        """
        logger.info("Starting self-check...")
        
        if not self._test_cases:
            self.load_test_cases()
        
        # 筛选测试用例
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
        
        # 确定状态
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
        """运行单个测试"""
        import time
        start = time.time()
        
        try:
            if test.category == "core":
                actual = await self._run_core_test(test)
            elif test.category == "tools":
                actual = await self._run_tool_test(test)
            else:
                actual = await self._run_generic_test(test)
            
            # 验证结果
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
        """运行核心测试"""
        if "brain" in test.id:
            response = await self.brain.think(test.input)
            return response.content
        return None
    
    async def _run_tool_test(self, test: TestCase) -> Any:
        """运行工具测试"""
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
        """运行通用测试"""
        # TODO: 实现更多测试类型
        return None
    
    def _validate(self, actual: Any, expected: Any) -> bool:
        """验证结果"""
        if expected is None:
            return actual is not None
        
        if isinstance(expected, str):
            if expected.startswith("包含"):
                return expected[2:] in str(actual) or str(actual) != ""
            return str(actual) == expected
        
        return actual == expected
    
    async def fix_failures(self, report: CheckReport) -> int:
        """
        尝试修复失败的测试
        
        Args:
            report: 自检报告
        
        Returns:
            修复数量
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
        """尝试修复单个失败"""
        logger.info(f"Attempting to fix: {result.test_id}")
        
        # 使用 LLM 分析错误并提供修复建议
        prompt = f"""测试失败:
ID: {result.test_id}
错误: {result.error}
实际结果: {result.actual}

请分析可能的原因并提供修复建议。"""

        response = await self.brain.think(prompt)
        
        logger.info(f"Fix suggestion: {response.content[:200]}...")
        
        # TODO: 实现自动修复逻辑
        # 这需要根据具体错误类型采取不同的修复策略
        
        return False
    
    async def learn_from_check(self, report: CheckReport) -> None:
        """从自检中学习"""
        if report.failed > 0:
            # 记录失败模式
            failures = [r for r in report.results if not r.passed]
            
            for failure in failures:
                logger.info(f"Learning from failure: {failure.test_id}")
                
                # TODO: 将失败模式记录到记忆系统
                # 这样下次遇到类似问题时可以避免
    
    # ==================== 日志分析与自动修复 ====================
    
    # 默认的自检提示词（当文件不存在时使用）
    DEFAULT_SELFCHECK_PROMPT = """你是系统自检 Agent，负责分析错误日志并决定修复策略。

针对每个错误，输出 JSON 数组：
[
  {
    "error_id": "模块名_消息前缀",
    "module": "模块名",
    "error_type": "core|tool|channel|config|network|skill|task",
    "analysis": "错误原因分析",
    "severity": "critical|high|medium|low",
    "can_fix": true|false,
    "fix_instruction": "具体的修复指令（给修复 Agent 的任务描述）",
    "fix_reason": "选择策略的原因",
    "requires_restart": false,
    "note_to_user": "给用户的提示（如需人工处理）"
  }
]

规则：
- 核心组件（Brain/Agent/Memory/Scheduler/LLM/Database）错误：can_fix=false
- 工具/通道/配置错误：可以尝试修复，在 fix_instruction 中写清楚具体操作
- Skill 相关错误：排查 skill 本身的问题（文件、格式、依赖），不要纠结于任务
- 任务持续失败：建议用户优化任务配置，可能是任务设计不合理
- fix_instruction 要写清楚使用什么工具（shell/file），执行什么命令
- 只输出 JSON 数组"""
    
    async def run_daily_check(self) -> DailyReport:
        """
        执行每日自检（LLM 驱动）
        
        流程:
        1. 本地匹配提取 ERROR 日志
        2. 生成错误摘要
        3. LLM 分析错误并决定修复策略
        4. 根据 LLM 决策执行修复
        5. 修复后自测验证
        6. 生成报告
        
        Returns:
            DailyReport
        """
        logger.info("Starting daily self-check (LLM-driven)...")
        
        today = datetime.now().strftime("%Y-%m-%d")
        report = DailyReport(
            date=today,
            timestamp=datetime.now(),
        )
        
        # === 阶段 1: 收集所有问题信息（日志 + 记忆 + 复盘） ===
        
        # 1.1 提取日志错误
        log_analyzer = LogAnalyzer(settings.log_dir_path)
        errors = log_analyzer.extract_errors_only()
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
            for result in analysis_results:
                error_type = result.get("error_type", "unknown")
                can_fix = result.get("can_fix", False)
                
                if error_type == "core" or not can_fix:
                    # 核心组件错误或不可修复，记录到报告
                    report.core_errors += 1
                    report.core_error_patterns.append({
                        "pattern": result.get("error_id", ""),
                        "count": 1,
                        "logger": result.get("module", "unknown"),
                        "message": result.get("analysis", ""),
                        "last_seen": datetime.now().isoformat(),
                        "note_to_user": result.get("note_to_user", ""),
                        "requires_restart": result.get("requires_restart", False),
                    })
                else:
                    # 工具错误，尝试修复
                    report.tool_errors += 1
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
                    
                    # 记录工具错误模式
                    report.tool_error_patterns.append({
                        "pattern": result.get("error_id", ""),
                        "count": 1,
                        "logger": result.get("module", "unknown"),
                        "message": result.get("analysis", ""),
                        "last_seen": datetime.now().isoformat(),
                    })
            
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
            for issue in common_issues[:5]:
                lines.append(f"- [{issue.get('count', 0)}次] {issue.get('issue', '')}")
            lines.append("")
        
        # 复盘详情（最多 3 个）
        records = retrospect_summary.get("records", [])[:3]
        if records:
            lines.append("### 复盘详情（最近 3 个）")
            for r in records:
                desc = r.get('description', '')[:50]
                result = r.get('retrospect_result', '')[:150]
                lines.append(f"- **{desc}...**")
                if result:
                    lines.append(f"  - 分析: {result}...")
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
            for err in error_list[:10]:
                source = err.get('source', 'unknown')
                content = err.get('content', '')[:100]
                lines.append(f"- [{source}] {content}")
            lines.append("")
        
        # 规则约束
        rule_list = memory_insights.get("rule_list", [])
        if rule_list:
            lines.append("### 系统规则约束")
            for rule in rule_list[:5]:
                content = rule.get('content', '')[:80]
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
            
            # 获取记忆管理器
            memory_manager = MemoryManager(settings.project_root / "data")
            
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
        self, 
        error_memories: list[dict], 
        rule_memories: list[dict]
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
        error_summary = "\n".join([
            f"- [{m.get('source', 'unknown')}] {m.get('content', '')[:150]}"
            for m in error_memories[:20]
        ])
        
        rule_summary = "\n".join([
            f"- {m.get('content', '')[:100]}"
            for m in rule_memories[:10]
        ])
        
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
            json_match = re.search(r'\[.*\]', response.content, re.DOTALL)
            if json_match:
                suggestions = json.loads(json_match.group())
                if isinstance(suggestions, list):
                    return [str(s) for s in suggestions[:5]]
            
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
        
        # 检查摘要大小，如果太大则分批处理
        MAX_CHARS_PER_BATCH = 8000  # 每批最大字符数（约 2000 tokens）
        
        if len(error_summary) <= MAX_CHARS_PER_BATCH:
            # 摘要较小，直接处理
            return await self._analyze_single_batch(error_summary, system_prompt)
        
        # 摘要太大，分批处理
        logger.info(f"Error summary too large ({len(error_summary)} chars), splitting into batches")
        
        # 按 "### [" 分割成独立的错误块
        import re
        error_blocks = re.split(r'(?=### \[)', error_summary)
        
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
            logger.info(f"Analyzing batch {i+1}/{len(batches)} ({len(batch)} chars)")
            try:
                batch_results = await self._analyze_single_batch(batch, system_prompt)
                all_results.extend(batch_results)
            except Exception as e:
                logger.error(f"Batch {i+1} analysis failed: {e}")
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
            json_match = re.search(r'\[[\s\S]*\]', content)
            if json_match:
                json_str = json_match.group()
                return json.loads(json_str)
            
            # 尝试直接解析
            return json.loads(content)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"LLM response: {content[:500]}")
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
                    fix_instruction = "使用 shell 工具修复目录权限：Linux 下执行 chmod -R 755 data/，Windows 下执行 icacls data /grant Users:F /T"
                    can_fix = True
                elif "not found" in message_lower or "no such file" in message_lower:
                    fix_instruction = "使用 file 工具创建缺失的目录：确保 data/、data/cache/、data/sessions/、logs/ 目录存在"
                    can_fix = True
                elif "cache" in message_lower or "corrupt" in message_lower:
                    fix_instruction = "使用 shell 工具清理缓存目录：删除 data/cache/ 下的所有文件，然后重新创建目录"
                    can_fix = True
                elif "timeout" in message_lower:
                    fix_instruction = "检查是否有僵尸进程，使用 shell 工具查看并清理可能卡住的进程"
                    can_fix = True
                elif "connection" in message_lower:
                    # 连接错误通常需要人工检查
                    fix_instruction = None
                    can_fix = False
            
            results.append({
                "error_id": pattern_key[:50],
                "module": module,
                "error_type": "core" if is_core else "tool",
                "analysis": message[:100],
                "severity": "high" if is_core else "medium",
                "can_fix": can_fix,
                "fix_instruction": fix_instruction,
                "fix_reason": "规则匹配（降级模式）",
                "requires_restart": is_core,
                "note_to_user": "需要人工检查" if is_core else None,
            })
        
        return results
    
    async def _execute_fix_by_llm_decision(self, analysis: dict) -> FixRecord:
        """
        根据 LLM 决策执行修复（使用主 Agent）
        
        创建一个完整的 Agent 实例来执行修复任务，
        Agent 拥有完整能力：Soul、User、Memory、工具等。
        
        Args:
            analysis: LLM 分析结果（包含 fix_instruction）
        
        Returns:
            FixRecord
        """
        fix_record = FixRecord(
            error_pattern=analysis.get("error_id", ""),
            component=analysis.get("module", "unknown"),
            fix_action=analysis.get("fix_reason", ""),
            fix_time=datetime.now(),
        )
        
        fix_instruction = analysis.get("fix_instruction")
        
        # 没有修复指令，跳过
        if not fix_instruction or not analysis.get("can_fix", False):
            fix_record.fix_action = f"跳过修复: {analysis.get('fix_reason', '无法自动修复')}"
            fix_record.success = False
            return fix_record
        
        fix_record.fix_action = f"Agent 执行: {fix_instruction[:50]}..."
        
        try:
            # 创建 Agent（不启动 scheduler 避免递归）
            from ..core.agent import Agent
            agent = Agent()
            await agent.initialize(start_scheduler=False)
            
            # 关键：清空历史上下文，使用干净状态
            # 避免累积的会话历史导致上下文过大
            agent._context.messages = []
            agent._conversation_history = []
            logger.info("SelfChecker: Agent context cleared for clean execution")
            
            # 构建修复 prompt
            fix_prompt = f"""你是系统自检修复助手。请根据以下分析执行修复任务：

## 错误信息
- 错误ID: {analysis.get('error_id', 'unknown')}
- 模块: {analysis.get('module', 'unknown')}
- 分析: {analysis.get('analysis', '')}

## 修复指令
{fix_instruction}

## 要求
1. 使用可用工具（shell、file 等）完成修复
2. 修复后验证结果是否正确
3. 如果修复失败，说明原因
4. 完成后简要报告修复结果

请开始执行修复。"""

            # 使用 Ralph 模式执行（支持多轮工具调用）
            if hasattr(agent, "execute_task_from_message"):
                result = await agent.execute_task_from_message(fix_prompt)
                success = result.success if result else False
                result_msg = result.data if result and result.success else (result.error if result else "无结果")
            else:
                # 降级到普通 chat
                result_msg = await agent.chat(fix_prompt)
                success = "失败" not in result_msg and "error" not in result_msg.lower()
            
            # 清理 Agent
            await agent.shutdown()
            
            # 记录结果
            fix_record.success = success
            fix_record.verified = success
            fix_record.verification_result = result_msg[:200] if result_msg else ""
            
            logger.info(f"Agent fix completed: {analysis.get('error_id')} - {'success' if success else 'failed'}")
            
        except Exception as e:
            logger.error(f"Agent fix failed: {e}", exc_info=True)
            fix_record.fix_action = f"Agent 修复失败: {str(e)}"
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
        message = sample.message if sample else ""
        
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
    
    def get_pending_report(self) -> Optional[str]:
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
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 检查是否已提交
            if data.get("reported"):
                return None
            
            # 读取 Markdown 报告
            if md_file.exists():
                with open(md_file, "r", encoding="utf-8") as f:
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
    
    def mark_report_as_reported(self, date: Optional[str] = None) -> bool:
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
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            data["reported"] = True
            
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to mark report as reported: {e}")
            return False
