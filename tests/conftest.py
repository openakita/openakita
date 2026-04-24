"""
Global pytest fixtures for OpenAkita test suite.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Workaround: On Windows, platform._wmi_query() can hang when the WMI
# service is slow/unresponsive (e.g. after a crash).  Faker triggers this
# during pytest plugin collection via platform.system().  Pre-populate the
# uname cache so the real WMI call is never needed.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _orig_wmi = getattr(platform, "_wmi_query", None)
    if _orig_wmi is not None:
        platform._wmi_query = lambda *a, **k: ("10.0.26200", 1, "Multiprocessor Free", 0, 0)
        platform.system()          # populate cache
        platform._wmi_query = _orig_wmi   # restore

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tests.fixtures.mock_llm import MockBrain, MockLLMClient, MockResponse


@pytest.fixture
def mock_llm_client() -> MockLLMClient:
    """A fresh MockLLMClient with an empty response queue."""
    client = MockLLMClient()
    client.set_default_response("Default mock response")
    return client


@pytest.fixture
def mock_brain(mock_llm_client: MockLLMClient) -> MockBrain:
    """A MockBrain backed by the mock_llm_client fixture."""
    return MockBrain(mock_llm_client)


@pytest.fixture
def test_session():
    """A clean test Session with no messages."""
    from tests.fixtures.factories import create_test_session
    return create_test_session()


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """A temporary workspace directory with standard subdirs."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "memory").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "identity").mkdir()
    return tmp_path


@pytest.fixture
def test_settings(tmp_workspace: Path):
    """Test-specific Settings pointing to temp dirs, no external dependencies."""
    os.environ["OPENAKITA_PROJECT_ROOT"] = str(tmp_workspace)
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-placeholder")

    from openakita.config import Settings
    settings = Settings(
        project_root=tmp_workspace,
        database_path=str(tmp_workspace / "data" / "agent.db"),
        log_dir=str(tmp_workspace / "logs"),
        log_level="WARNING",
        max_iterations=10,
    )
    yield settings

    os.environ.pop("OPENAKITA_PROJECT_ROOT", None)


@pytest.fixture
def mock_response_factory():
    """Factory fixture for creating MockResponse instances."""
    def _create(
        content: str = "",
        tool_calls: list[dict] | None = None,
        reasoning_content: str | None = None,
    ) -> MockResponse:
        return MockResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )
    return _create

