from __future__ import annotations

import asyncio

import pytest
from excel_maker_inline.python_deps import PythonDepsManager, list_optional_groups


def test_optional_groups_are_whitelisted() -> None:
    groups = list_optional_groups()

    assert set(groups) == {"table_core", "legacy_excel", "charting", "template_tools"}
    assert "openpyxl" in groups["table_core"]


def test_unknown_dependency_group_rejected(tmp_path) -> None:
    manager = PythonDepsManager(tmp_path)

    with pytest.raises(ValueError):
        manager.status("requests")


@pytest.mark.asyncio
async def test_start_install_reports_busy(monkeypatch, tmp_path) -> None:
    manager = PythonDepsManager(tmp_path)

    async def fake_run(dep_id, command, job):
        await asyncio.sleep(0.05)
        job.status = "succeeded"
        job.exit_code = 0

    monkeypatch.setattr(manager, "_run_command", fake_run)
    first = await manager.start_install("table_core")
    second = await manager.start_install("table_core")

    assert first["busy"] is True
    assert second["busy"] is True
    await asyncio.sleep(0.08)
    assert manager.status("table_core")["status"] == "succeeded"


@pytest.mark.asyncio
async def test_start_uninstall_uses_whitelisted_packages(monkeypatch, tmp_path) -> None:
    manager = PythonDepsManager(tmp_path)
    captured: list[str] = []

    async def fake_run(dep_id, command, job):
        captured.extend(command)
        job.status = "succeeded"
        job.exit_code = 0

    monkeypatch.setattr(manager, "_run_command", fake_run)
    started = await manager.start_uninstall("table_core")

    assert started["busy"] is True
    await asyncio.sleep(0)
    assert captured[-2:] == ["openpyxl", "pandas"]
    assert manager.status("table_core")["op_kind"] == "uninstall"
