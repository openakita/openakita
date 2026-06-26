"""
代码修复器
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from .runner import TestResult

logger = logging.getLogger(__name__)


@dataclass
class FixResult:
    """修复结果"""

    success: bool
    test_id: str
    changes: list[str]
    error: str | None = None


class CodeFixer:
    """
    代码修复器

    根据失败的测试自动修复代码。
    """

    def __init__(self, brain=None, project_root: Path | None = None):
        self.brain = brain
        self.project_root = project_root or Path.cwd()

    async def fix(self, result: TestResult) -> FixResult:
        """
        修复失败的测试

        Args:
            result: 失败的测试结果

        Returns:
            FixResult
        """
        if result.passed:
            return FixResult(
                success=True,
                test_id=result.test_id,
                changes=[],
            )

        logger.info(f"Attempting to fix: {result.test_id}")

        if not self.brain:
            return FixResult(
                success=False,
                test_id=result.test_id,
                changes=[],
                error="Brain not available",
            )

        # 分析错误
        analysis = await self._analyze_failure(result)

        if not analysis:
            return FixResult(
                success=False,
                test_id=result.test_id,
                changes=[],
                error="Failed to analyze error",
            )

        # 尝试修复
        fix_result = await self._apply_fix(result, analysis)

        return fix_result

    async def _analyze_failure(self, result: TestResult) -> dict | None:
        """分析失败原因"""
        prompt = f"""分析以下测试失败:

测试 ID: {result.test_id}
错误: {result.error}
实际结果: {result.actual}
预期结果: {result.expected}

请分析:
1. 失败的根本原因
2. 可能涉及的代码文件
3. 建议的修复方法

以 JSON 格式返回:
{{
    "cause": "根本原因",
    "files": ["可能的文件1.py", "文件2.py"],
    "fix_strategy": "修复策略",
    "code_changes": [
        {{"file": "文件路径", "description": "修改描述"}}
    ]
}}"""

        response = await self.brain.think(prompt)

        import json

        try:
            content = response.content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            return json.loads(content)
        except Exception:
            return None

    async def _apply_fix(self, result: TestResult, analysis: dict) -> FixResult:
        """应用修复"""
        changes = []

        for change in analysis.get("code_changes", []):
            file_path = change.get("file")
            description = change.get("description")

            if not file_path:
                continue

            full_path = self.project_root / file_path

            if not full_path.exists():
                logger.warning(f"File not found: {full_path}")
                continue

            # 读取当前代码
            try:
                current_code = full_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to read {full_path}: {e}")
                continue

            # 生成修复后的代码
            fixed_code = await self._generate_fix(
                current_code,
                description,
                result.error or "",
            )

            if fixed_code and fixed_code != current_code:
                # 备份并写入
                backup_path = full_path.with_suffix(".bak")
                backup_path.write_text(current_code, encoding="utf-8")

                full_path.write_text(fixed_code, encoding="utf-8")

                changes.append(f"Modified {file_path}: {description}")
                logger.info(f"Fixed {file_path}")

        return FixResult(
            success=len(changes) > 0,
            test_id=result.test_id,
            changes=changes,
        )

    async def _generate_fix(
        self,
        current_code: str,
        fix_description: str,
        error: str,
    ) -> str | None:
        """生成修复后的代码"""
        prompt = f"""请根据以下描述修复代码:

修复描述: {fix_description}
错误信息: {error}

当前代码:
```python
{current_code}
```

请输出修复后的完整代码。只输出代码，不要解释。"""

        response = await self.brain.think(prompt)

        code = response.content
        if "```python" in code:
            start = code.find("```python") + 9
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()

        return code if code else None

    async def batch_fix(self, results: list[TestResult]) -> list[FixResult]:
        """批量修复"""
        fix_results = []

        for result in results:
            if not result.passed:
                fix_result = await self.fix(result)
                fix_results.append(fix_result)

        return fix_results
