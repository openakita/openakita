"""
Result judge
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class JudgeResult:
    """Judgment result"""

    passed: bool
    reason: str = ""
    score: float = 0  # 0-1
    details: dict | None = None


class Judge:
    """
    Result judge

    Determines whether test execution results match expectations.
    Supports multiple judgment strategies.
    """

    def __init__(self, brain=None):
        self.brain = brain  # Used for LLM-based judgment

    async def evaluate(
        self,
        actual: Any,
        expected: Any,
        description: str = "",
    ) -> JudgeResult:
        """
        Evaluate a result

        Args:
            actual: Actual result
            expected: Expected result
            description: Test description

        Returns:
            JudgeResult
        """
        # Choose judgment strategy based on the type of expected
        if expected is None:
            # Pass as long as there is any result
            return self._judge_not_none(actual)

        if isinstance(expected, str):
            return self._judge_string(actual, expected)

        if isinstance(expected, (int, float)):
            return self._judge_number(actual, expected)

        if isinstance(expected, bool):
            return self._judge_bool(actual, expected)

        if isinstance(expected, dict):
            return self._judge_dict(actual, expected)

        if isinstance(expected, list):
            return self._judge_list(actual, expected)

        if callable(expected):
            return self._judge_callable(actual, expected)

        # Default: exact match
        return self._judge_exact(actual, expected)

    def _judge_not_none(self, actual: Any) -> JudgeResult:
        """Check that the value is non-empty"""
        passed = actual is not None and actual != ""
        return JudgeResult(
            passed=passed,
            reason="Result exists" if passed else "Result is None or empty",
            score=1.0 if passed else 0.0,
        )

    def _judge_string(self, actual: Any, expected: str) -> JudgeResult:
        """String judgment"""
        actual_str = str(actual) if actual is not None else ""

        # Check special judgment rules
        if expected.startswith("contains:"):
            pattern = expected[9:]
            passed = pattern in actual_str
            return JudgeResult(
                passed=passed,
                reason=f"Contains '{pattern}'" if passed else f"Does not contain '{pattern}'",
                score=1.0 if passed else 0.0,
            )

        if expected.startswith("regex:"):
            pattern = expected[6:]
            passed = bool(re.search(pattern, actual_str))
            return JudgeResult(
                passed=passed,
                reason=f"Matches pattern '{pattern}'" if passed else f"Does not match '{pattern}'",
                score=1.0 if passed else 0.0,
            )

        if expected.startswith("startswith:"):
            prefix = expected[11:]
            passed = actual_str.startswith(prefix)
            return JudgeResult(
                passed=passed,
                reason=f"Starts with '{prefix}'" if passed else f"Does not start with '{prefix}'",
                score=1.0 if passed else 0.0,
            )

        if expected.startswith("endswith:"):
            suffix = expected[9:]
            passed = actual_str.endswith(suffix)
            return JudgeResult(
                passed=passed,
                reason=f"Ends with '{suffix}'" if passed else f"Does not end with '{suffix}'",
                score=1.0 if passed else 0.0,
            )

        if expected.startswith("length>="):
            min_len = int(expected[8:])
            passed = len(actual_str) >= min_len
            return JudgeResult(
                passed=passed,
                reason=f"Length {len(actual_str)} >= {min_len}"
                if passed
                else f"Length {len(actual_str)} < {min_len}",
                score=1.0 if passed else 0.0,
            )

        # Default: exact match (ignoring leading/trailing whitespace)
        passed = actual_str.strip() == expected.strip()
        return JudgeResult(
            passed=passed,
            reason="Exact match" if passed else "Not exact match",
            score=1.0 if passed else 0.0,
        )

    def _judge_number(self, actual: Any, expected: float) -> JudgeResult:
        """Numeric judgment"""
        try:
            actual_num = float(actual)
            # Allow small tolerance
            passed = abs(actual_num - expected) < 0.001
            return JudgeResult(
                passed=passed,
                reason=f"Value {actual_num} == {expected}"
                if passed
                else f"Value {actual_num} != {expected}",
                score=1.0 if passed else 0.0,
            )
        except (TypeError, ValueError):
            return JudgeResult(
                passed=False,
                reason=f"Cannot convert '{actual}' to number",
                score=0.0,
            )

    def _judge_bool(self, actual: Any, expected: bool) -> JudgeResult:
        """Boolean judgment"""
        actual_bool = bool(actual)
        passed = actual_bool == expected
        return JudgeResult(
            passed=passed,
            reason=f"Bool {actual_bool} == {expected}"
            if passed
            else f"Bool {actual_bool} != {expected}",
            score=1.0 if passed else 0.0,
        )

    def _judge_dict(self, actual: Any, expected: dict) -> JudgeResult:
        """Dict judgment"""
        if not isinstance(actual, dict):
            return JudgeResult(
                passed=False,
                reason=f"Expected dict, got {type(actual).__name__}",
                score=0.0,
            )

        # Check that all expected keys exist and their values match
        missing_keys = []
        wrong_values = []

        for key, value in expected.items():
            if key not in actual:
                missing_keys.append(key)
            elif actual[key] != value:
                wrong_values.append(key)

        if missing_keys or wrong_values:
            return JudgeResult(
                passed=False,
                reason=f"Missing keys: {missing_keys}, Wrong values: {wrong_values}",
                score=0.0,
                details={"missing_keys": missing_keys, "wrong_values": wrong_values},
            )

        return JudgeResult(
            passed=True,
            reason="All expected keys and values match",
            score=1.0,
        )

    def _judge_list(self, actual: Any, expected: list) -> JudgeResult:
        """List judgment"""
        if not isinstance(actual, (list, tuple)):
            return JudgeResult(
                passed=False,
                reason=f"Expected list, got {type(actual).__name__}",
                score=0.0,
            )

        actual_list = list(actual)

        # Check length
        if len(actual_list) != len(expected):
            return JudgeResult(
                passed=False,
                reason=f"Length mismatch: {len(actual_list)} != {len(expected)}",
                score=0.0,
            )

        # Check elements
        for i, (a, e) in enumerate(zip(actual_list, expected, strict=False)):
            if a != e:
                return JudgeResult(
                    passed=False,
                    reason=f"Element {i} mismatch: {a} != {e}",
                    score=0.0,
                )

        return JudgeResult(
            passed=True,
            reason="All elements match",
            score=1.0,
        )

    def _judge_callable(self, actual: Any, validator: callable) -> JudgeResult:
        """Use a custom validator function"""
        try:
            result = validator(actual)
            if isinstance(result, JudgeResult):
                return result
            passed = bool(result)
            return JudgeResult(
                passed=passed,
                reason="Custom validator passed" if passed else "Custom validator failed",
                score=1.0 if passed else 0.0,
            )
        except Exception as e:
            return JudgeResult(
                passed=False,
                reason=f"Validator error: {e}",
                score=0.0,
            )

    def _judge_exact(self, actual: Any, expected: Any) -> JudgeResult:
        """Exact match"""
        passed = actual == expected
        return JudgeResult(
            passed=passed,
            reason="Exact match" if passed else f"Mismatch: {actual} != {expected}",
            score=1.0 if passed else 0.0,
        )

    async def llm_judge(
        self,
        actual: Any,
        expected: str,
        context: str = "",
    ) -> JudgeResult:
        """Use LLM for judgment"""
        if not self.brain:
            return JudgeResult(
                passed=False,
                reason="LLM judge not available",
                score=0.0,
            )

        prompt = f"""Please determine whether the following result matches the expectation:

Expected: {expected}
Actual result: {actual}
{f"Context: {context}" if context else ""}

Please respond in JSON format:
{{
    "passed": true/false,
    "reason": "Reasoning for the judgment",
    "score": 0-1 confidence level
}}"""

        response = await self.brain.think(prompt)

        import json

        try:
            content = response.content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            data = json.loads(content)

            return JudgeResult(
                passed=data.get("passed", False),
                reason=data.get("reason", ""),
                score=data.get("score", 0),
            )
        except Exception:
            return JudgeResult(
                passed=False,
                reason="Failed to parse LLM response",
                score=0.0,
            )
