"""L1 Unit Tests: Judge evaluation rules."""

import pytest

from openakita.testing.judge import Judge, JudgeResult


@pytest.fixture
def judge():
    return Judge()


class TestJudgeNotNone:
    async def test_non_none_passes(self, judge):
        result = await judge.evaluate("something", None)
        assert result.passed is True

    async def test_none_fails(self, judge):
        result = await judge.evaluate(None, None)
        assert result.passed is False

    async def test_empty_string_fails(self, judge):
        result = await judge.evaluate("", None)
        assert result.passed is False


class TestJudgeString:
    async def test_exact_match(self, judge):
        result = await judge.evaluate("hello", "hello")
        assert result.passed is True

    async def test_exact_match_with_whitespace(self, judge):
        result = await judge.evaluate("  hello  ", "hello")
        assert result.passed is True

    async def test_contains_rule(self, judge):
        result = await judge.evaluate("hello world", "contains:hello")
        assert result.passed is True

    async def test_contains_missing(self, judge):
        result = await judge.evaluate("hello world", "contains:python")
        assert result.passed is False

    async def test_regex_rule(self, judge):
        result = await judge.evaluate("answer is 42", r"regex:\d+")
        assert result.passed is True

    async def test_regex_no_match(self, judge):
        result = await judge.evaluate("no numbers here", r"regex:\d+")
        assert result.passed is False

    async def test_startswith_rule(self, judge):
        result = await judge.evaluate("Hello world", "startswith:Hello")
        assert result.passed is True

    async def test_endswith_rule(self, judge):
        result = await judge.evaluate("Hello world", "endswith:world")
        assert result.passed is True

    async def test_length_rule(self, judge):
        result = await judge.evaluate("long enough text", "length>=5")
        assert result.passed is True

    async def test_length_too_short(self, judge):
        result = await judge.evaluate("hi", "length>=100")
        assert result.passed is False


class TestJudgeNumber:
    async def test_exact_number(self, judge):
        result = await judge.evaluate(42, 42)
        assert result.passed is True

    async def test_float_close(self, judge):
        result = await judge.evaluate(3.14, 3.14)
        assert result.passed is True

    async def test_string_to_number(self, judge):
        result = await judge.evaluate("42", 42)
        assert result.passed is True

    async def test_not_a_number(self, judge):
        result = await judge.evaluate("abc", 42)
        assert result.passed is False


class TestJudgeBool:
    async def test_true_match(self, judge):
        result = await judge.evaluate(True, True)
        assert result.passed is True

    async def test_false_match(self, judge):
        result = await judge.evaluate(False, False)
        assert result.passed is True

    async def test_truthy_value(self, judge):
        """Bool expected with non-bool actual: Judge converts via bool()."""
        result = await judge.evaluate(1, True)
        assert result.passed is True


class TestJudgeDict:
    async def test_matching_dict(self, judge):
        result = await judge.evaluate({"a": 1, "b": 2}, {"a": 1, "b": 2})
        assert result.passed is True

    async def test_subset_check(self, judge):
        result = await judge.evaluate({"a": 1, "b": 2, "c": 3}, {"a": 1})
        assert result.passed is True

    async def test_missing_key(self, judge):
        result = await judge.evaluate({"a": 1}, {"b": 2})
        assert result.passed is False

    async def test_wrong_value(self, judge):
        result = await judge.evaluate({"a": 1}, {"a": 2})
        assert result.passed is False

    async def test_not_a_dict(self, judge):
        result = await judge.evaluate("not a dict", {"a": 1})
        assert result.passed is False


class TestJudgeList:
    async def test_matching_list(self, judge):
        result = await judge.evaluate([1, 2, 3], [1, 2, 3])
        assert result.passed is True

    async def test_length_mismatch(self, judge):
        result = await judge.evaluate([1, 2], [1, 2, 3])
        assert result.passed is False

    async def test_element_mismatch(self, judge):
        result = await judge.evaluate([1, 2, 4], [1, 2, 3])
        assert result.passed is False


class TestJudgeCallable:
    async def test_callable_returns_true(self, judge):
        result = await judge.evaluate(42, lambda x: x > 0)
        assert result.passed is True

    async def test_callable_returns_false(self, judge):
        result = await judge.evaluate(-1, lambda x: x > 0)
        assert result.passed is False

    async def test_callable_returns_judge_result(self, judge):
        def custom(x):
            return JudgeResult(passed=True, reason="Custom", score=0.9)
        result = await judge.evaluate(42, custom)
        assert result.passed is True
        assert result.score == 0.9


class TestJudgeResult:
    def test_default_values(self):
        r = JudgeResult(passed=True)
        assert r.reason == ""
        assert r.score == 0
        assert r.details is None

