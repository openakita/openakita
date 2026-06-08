"""
测试运行器
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .judge import Judge, JudgeResult

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """测试用例"""

    id: str
    category: str
    subcategory: str
    description: str
    input: Any
    expected: Any
    validator: Callable | None = None
    timeout: int = 30
    tags: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    """测试结果"""

    test_id: str
    passed: bool
    actual: Any = None
    expected: Any = None
    error: str | None = None
    duration_ms: float = 0
    judge_result: JudgeResult | None = None


@dataclass
class TestReport:
    """测试报告"""

    timestamp: datetime
    category: str | None
    total: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    results: list[TestResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0
        return self.passed / self.total * 100


class TestRunner:
    """
    测试运行器

    运行测试用例并生成报告。
    """

    def __init__(
        self,
        judge: Judge | None = None,
        test_dir: Path | None = None,
    ):
        self.judge = judge or Judge()
        self.test_dir = test_dir
        self._test_cases: list[TestCase] = []
        self._executors: dict[str, Callable] = {}

    def register_executor(self, category: str, executor: Callable) -> None:
        """注册测试执行器"""
        self._executors[category] = executor

    def add_test_case(self, test: TestCase) -> None:
        """添加测试用例"""
        self._test_cases.append(test)

    def add_test_cases(self, tests: list[TestCase]) -> None:
        """批量添加测试用例"""
        self._test_cases.extend(tests)

    def load_test_cases(self) -> int:
        """从目录加载测试用例"""
        if not self.test_dir or not self.test_dir.exists():
            return 0

        count = 0
        for category_dir in self.test_dir.iterdir():
            if category_dir.is_dir():
                for _test_file in category_dir.rglob("*.py"):
                    # TODO: 实现从文件加载
                    pass

        return count

    async def run_all(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        parallel: bool = False,
    ) -> TestReport:
        """
        运行所有测试

        Args:
            category: 筛选类别
            tags: 筛选标签
            parallel: 是否并行执行

        Returns:
            TestReport
        """
        start_time = time.time()

        # 筛选测试用例
        tests = self._test_cases
        if category:
            tests = [t for t in tests if t.category == category]
        if tags:
            tests = [t for t in tests if any(tag in t.tags for tag in tags)]

        logger.info(f"Running {len(tests)} tests...")

        results = []
        passed = 0
        failed = 0
        skipped = 0

        if parallel:
            # 并行执行
            tasks = [self._run_test(t) for t in tests]
            results = await asyncio.gather(*tasks)
        else:
            # 串行执行
            for test in tests:
                result = await self._run_test(test)
                results.append(result)

        # 统计结果
        for result in results:
            if result.passed:
                passed += 1
            elif result.error == "skipped":
                skipped += 1
            else:
                failed += 1

        duration = time.time() - start_time

        report = TestReport(
            timestamp=datetime.now(),
            category=category,
            total=len(tests),
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=duration,
            results=results,
        )

        logger.info(
            f"Test complete: {passed}/{len(tests)} passed "
            f"({report.pass_rate:.1f}%) in {duration:.2f}s"
        )

        return report

    async def _run_test(self, test: TestCase) -> TestResult:
        """运行单个测试"""
        start = time.time()

        try:
            # 获取执行器
            executor = self._executors.get(test.category)

            if not executor:
                return TestResult(
                    test_id=test.id,
                    passed=False,
                    error="skipped",
                )

            # 执行测试
            try:
                actual = await asyncio.wait_for(
                    executor(test.input),
                    timeout=test.timeout,
                )
            except TimeoutError:
                return TestResult(
                    test_id=test.id,
                    passed=False,
                    error=f"Timeout after {test.timeout}s",
                    duration_ms=(time.time() - start) * 1000,
                )

            # 判定结果
            if test.validator:
                judge_result = test.validator(actual, test.expected)
                passed = judge_result if isinstance(judge_result, bool) else judge_result.passed
            else:
                judge_result = await self.judge.evaluate(
                    actual,
                    test.expected,
                    test.description,
                )
                passed = judge_result.passed

            return TestResult(
                test_id=test.id,
                passed=passed,
                actual=actual,
                expected=test.expected,
                judge_result=judge_result if isinstance(judge_result, JudgeResult) else None,
                duration_ms=(time.time() - start) * 1000,
            )

        except Exception as e:
            return TestResult(
                test_id=test.id,
                passed=False,
                error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    async def run_single(self, test_id: str) -> TestResult | None:
        """运行单个测试"""
        test = next((t for t in self._test_cases if t.id == test_id), None)
        if test:
            return await self._run_test(test)
        return None

    def get_failed_tests(self, report: TestReport) -> list[TestResult]:
        """获取失败的测试"""
        return [r for r in report.results if not r.passed and r.error != "skipped"]
