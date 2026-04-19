"""
Code fixer
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from .runner import TestResult

logger = logging.getLogger(__name__)


@dataclass
class FixResult:
    """Fix result"""

    success: bool
    test_id: str
    changes: list[str]
    error: str | None = None


class CodeFixer:
    """
    Code fixer

    Automatically fixes code based on failed tests.
    """

    def __init__(self, brain=None, project_root: Path | None = None):
        self.brain = brain
        self.project_root = project_root or Path.cwd()

    async def fix(self, result: TestResult) -> FixResult:
        """
        Fix a failed test

        Args:
            result: The failed test result

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

        # Analyze the error
        analysis = await self._analyze_failure(result)

        if not analysis:
            return FixResult(
                success=False,
                test_id=result.test_id,
                changes=[],
                error="Failed to analyze error",
            )

        # Attempt the fix
        fix_result = await self._apply_fix(result, analysis)

        return fix_result

    async def _analyze_failure(self, result: TestResult) -> dict | None:
        """Analyze the cause of failure"""
        prompt = f"""Analyze the following test failure:

Test ID: {result.test_id}
Error: {result.error}
Actual result: {result.actual}
Expected result: {result.expected}

Please analyze:
1. Root cause of the failure
2. Code files that may be involved
3. Suggested fix approach

Return in JSON format:
{{
    "cause": "Root cause",
    "files": ["possible_file1.py", "file2.py"],
    "fix_strategy": "Fix strategy",
    "code_changes": [
        {{"file": "File path", "description": "Description of the change"}}
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
        """Apply the fix"""
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

            # Read current code
            try:
                current_code = full_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to read {full_path}: {e}")
                continue

            # Generate the fixed code
            fixed_code = await self._generate_fix(
                current_code,
                description,
                result.error or "",
            )

            if fixed_code and fixed_code != current_code:
                # Back up and write
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
        """Generate the fixed code"""
        prompt = f"""Please fix the code according to the following description:

Fix description: {fix_description}
Error message: {error}

Current code:
```python
{current_code}
```

Output the complete fixed code. Output only code, no explanations."""

        response = await self.brain.think(prompt)

        code = response.content
        if "```python" in code:
            start = code.find("```python") + 9
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()

        return code if code else None

    async def batch_fix(self, results: list[TestResult]) -> list[FixResult]:
        """Fix multiple failures in batch"""
        fix_results = []

        for result in results:
            if not result.passed:
                fix_result = await self.fix(result)
                fix_results.append(fix_result)

        return fix_results
