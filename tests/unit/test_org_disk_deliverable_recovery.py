"""Tests for bounded reads of runtime-designated root artifacts.

Artifact roles are assigned by the runtime and delivery manifest.  This recovery
helper therefore reads only the path already recorded in ``_root_final_artifact``
and never infers an artifact role from its filename or prose.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openakita.orgs.command_service import OrgCommandService


def _svc_with_artifact_store(store: dict) -> SimpleNamespace:
    return SimpleNamespace(_runtime=SimpleNamespace(_root_final_artifact=store))


def test_root_disk_deliverable_reads_recorded_report(tmp_path: Path) -> None:
    report = tmp_path / "整合稿.md"
    body = "# 最终整合报告\n\n综合策划、SEO、数据分析成果，形成完整方案。"
    report.write_text(body, encoding="utf-8")
    svc = _svc_with_artifact_store({"cmd1": ("editor-in-chief", str(report))})
    out = OrgCommandService._root_disk_deliverable(svc, "cmd1")
    assert out == body


def test_root_disk_deliverable_does_not_infer_role_from_prose(tmp_path: Path) -> None:
    report = tmp_path / "项目启动指令.md"
    body = "# 项目启动指令\n[dispatched to writer-a]\n"
    report.write_text(body, encoding="utf-8")
    svc = _svc_with_artifact_store({"cmd1": ("editor-in-chief", str(report))})
    assert OrgCommandService._root_disk_deliverable(svc, "cmd1") == body.strip()


def test_root_disk_deliverable_none_when_unrecorded() -> None:
    svc = _svc_with_artifact_store({})
    assert OrgCommandService._root_disk_deliverable(svc, "cmd1") is None


def test_root_disk_deliverable_none_when_recorded_file_is_missing(tmp_path: Path) -> None:
    svc = _svc_with_artifact_store({"cmd1": ("root", str(tmp_path / "missing.md"))})
    assert OrgCommandService._root_disk_deliverable(svc, "cmd1") is None


def test_root_disk_deliverable_truncates_huge_report(tmp_path: Path) -> None:
    report = tmp_path / "长报告.md"
    report.write_text("正" * 20000, encoding="utf-8")
    svc = _svc_with_artifact_store({"cmd1": ("root", str(report))})
    out = OrgCommandService._root_disk_deliverable(svc, "cmd1")
    assert out is not None
    assert len(out) == 12000
