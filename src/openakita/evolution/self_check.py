"""
自检系统

持续学习、错误分析、自动修复。
"""

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core.brain import Brain
from ..tools.shell import ShellTool
from ..tools.file import FileTool
from ..config import settings

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
