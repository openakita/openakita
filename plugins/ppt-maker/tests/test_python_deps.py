from __future__ import annotations

import asyncio
import sys

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

    async def fake_run(dep_id, command, job):
        await asyncio.sleep(0.05)
        job.status = "succeeded"
        job.exit_code = 0

    monkeypatch.setattr(manager, "_run_command", fake_run)
    first = await manager.start_install("table_processing")
    second = await manager.start_install("table_processing")

    assert first["busy"] is True
    assert second["busy"] is True
    await asyncio.sleep(0.08)
    assert manager.status("table_processing")["status"] == "succeeded"


@pytest.mark.asyncio
async def test_start_install_uses_private_target_dir(monkeypatch, tmp_path) -> None:
    target_dir = tmp_path / "deps-target"
    manager = PythonDepsManager(tmp_path, python_executable="python-bin", target_dir=target_dir)
    captured: list[str] = []

    async def fake_run(dep_id, command, job):
        captured.extend(command)
        job.status = "succeeded"
        job.exit_code = 0

    monkeypatch.setattr(manager, "_run_command", fake_run)
    started = await manager.start_install("advanced_export")

    assert started["busy"] is True
    await asyncio.sleep(0)
    assert captured[:5] == ["python-bin", "-m", "pip", "install", "--upgrade"]
    assert "--prefer-binary" in captured
    assert captured[captured.index("--target") + 1] == str(target_dir)
    assert captured[-1] == "python-pptx"


@pytest.mark.asyncio
async def test_start_uninstall_removes_private_target_files(tmp_path) -> None:
    target_dir = tmp_path / "deps-target"
    (target_dir / "pptx").mkdir(parents=True)
    (target_dir / "python_pptx-1.0.0.dist-info").mkdir()
    manager = PythonDepsManager(tmp_path, python_executable=sys.executable, target_dir=target_dir)
    started = await manager.start_uninstall("advanced_export")

    assert started["busy"] is True
    await asyncio.sleep(0.01)
    assert not (target_dir / "pptx").exists()
    assert not (target_dir / "python_pptx-1.0.0.dist-info").exists()
    assert manager.status("advanced_export")["op_kind"] == "uninstall"


def test_status_reports_diagnostics(tmp_path) -> None:
    target_dir = tmp_path / "deps-target"
    manager = PythonDepsManager(tmp_path, python_executable="python-bin", target_dir=target_dir)

    status = manager.status("advanced_export")

    assert status["python_executable"] == "python-bin"
    assert status["target_dir"] == str(target_dir)
    assert str(target_dir) in status["import_paths"]
    assert status["checks"][0]["import"] == "pptx"


def test_manager_prefers_runtime_python(monkeypatch, tmp_path) -> None:
    from openakita import runtime_env

    monkeypatch.setattr(runtime_env, "get_python_executable", lambda: "packaged-python")

    manager = PythonDepsManager(tmp_path)

    assert manager.status("advanced_export")["python_executable"] == "packaged-python"

