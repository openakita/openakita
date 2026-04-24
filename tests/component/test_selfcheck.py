"""L2 Component Tests: SelfChecker test case loading and report generation."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from openakita.evolution.self_check import SelfChecker


@pytest.fixture
def mock_brain():
    brain = MagicMock()
    brain.max_tokens = 4096
    return brain


class TestSelfCheckerInit:
    def test_create_with_brain(self, mock_brain):
        checker = SelfChecker(brain=mock_brain)
        assert checker is not None

    def test_create_with_test_dir(self, mock_brain, tmp_path):
        test_dir = tmp_path / "test_cases"
        test_dir.mkdir()
        checker = SelfChecker(brain=mock_brain, test_dir=test_dir)
        assert checker is not None


class TestLoadTestCases:
    def test_load_builtin_cases(self, mock_brain):
        checker = SelfChecker(brain=mock_brain)
        count = checker.load_test_cases()
        assert isinstance(count, int)
        assert count >= 0

    def test_load_from_custom_dir(self, mock_brain, tmp_path):
        test_dir = tmp_path / "custom_tests"
        test_dir.mkdir()
        checker = SelfChecker(brain=mock_brain, test_dir=test_dir)
        count = checker.load_test_cases()
        assert isinstance(count, int)


class TestReportManagement:
    def test_get_pending_report_when_none(self, mock_brain):
        checker = SelfChecker(brain=mock_brain)
        report = checker.get_pending_report()
        # May be None or a string
        assert report is None or isinstance(report, str)

