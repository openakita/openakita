"""Integration test bootstrap for subtitle-craft.

Inherits the parent ``conftest.py`` which seeds ``sys.path`` with the plugin
root. This file additionally registers the ``integration`` pytest marker so
running ``pytest -m integration`` won't trigger a "PytestUnknownMarkWarning"
on systems where the project-wide pyproject does not pre-declare the marker.
"""

from __future__ import annotations


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers",
        "integration: hits live DashScope endpoints; requires DASHSCOPE_API_KEY",
    )
