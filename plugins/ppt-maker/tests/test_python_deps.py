from __future__ import annotations

import asyncio

import pytest
from ppt_maker_inline.python_deps import PythonDepsManager, list_optional_groups


def test_optional_groups_are_whitelisted() -> None:
    groups = list_optional_groups()

    assert set(groups) == {
        "doc_parsing",
        "table_processing",
        "chart_rendering",
        "advanced_export",
        "marp_bridge",
    }
    assert "python-pptx" in groups["advanced_export"]


def test_unknown_dependency_group_rejected(tmp_path) -> None:
    manager = PythonDepsManager(tmp_path)

    with pytest.raises(ValueError):
        manager.status("requests")


def test_detect_only_group_cannot_install(tmp_path) -> None:
    manager = PythonDepsManager(tmp_path)

    assert manager.status("marp_bridge")["detect_only"] is True


@pytest.mark.asyncio
async def test_start_install_reports_busy(monkeypatch, tmp_path) -> None:
    manager = PythonDepsManager(tmp_path)

    async def fake_run(dep_id, packages, job):
        await asyncio.sleep(0.05)
        job.status = "succeeded"
        job.exit_code = 0

    monkeypatch.setattr(manager, "_run_install", fake_run)
    first = await manager.start_install("table_processing")
    second = await manager.start_install("table_processing")

    assert first["busy"] is True
    assert second["busy"] is True
    await asyncio.sleep(0.08)
    assert manager.status("table_processing")["status"] == "succeeded"

