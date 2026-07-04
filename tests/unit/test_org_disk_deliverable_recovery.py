"""test13 fix (b): when the forced root finalization is skipped/cut short, the
final deliverable + PDF must fall back to the ROOT node's on-disk integration
report -- never the kickoff/派单稿 and never a downstream product.

These tests cover the deterministic building blocks:

* ``runtime._artifact_looks_like_kickoff`` -- keeps the kickoff .md out of the
  ``_root_final_artifact`` slot (and therefore out of the final PDF).
* ``command_service._looks_like_kickoff_text`` -- text-level kickoff guard.
* ``command_service.OrgCommandService._root_disk_deliverable`` -- reads the
  recorded root integration file so the degrade path can fill
  ``command_done.final_message`` from disk.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openakita.orgs.command_service import (
    OrgCommandService,
    _looks_like_kickoff_text,
)
from openakita.orgs.runtime import _artifact_looks_like_kickoff

# ---------------------------------------------------------------------------
# 1) Artifact-level kickoff detector (runtime layer)
# ---------------------------------------------------------------------------


def test_artifact_kickoff_detector_flags_dispatch_scaffold(tmp_path: Path) -> None:
    kickoff = tmp_path / "项目启动指令_editor-in-chief.md"
    kickoff.write_text(
        "# 项目启动指令\n\n各位团队成员，现在项目正式启动。\n[dispatched to writer-a]\n",
        encoding="utf-8",
    )
    assert _artifact_looks_like_kickoff(str(kickoff)) is True


def test_artifact_kickoff_detector_passes_real_report(tmp_path: Path) -> None:
    report = tmp_path / "线下交流会策划案.md"
    report.write_text(
        "# 自律习惯线下交流会策划案\n\n## 一、活动目标\n提升成员自律能力……\n"
        "## 二、议程\n1. 开场\n2. 分享\n## 三、预算\n合计 5000 元。\n",
        encoding="utf-8",
    )
    assert _artifact_looks_like_kickoff(str(report)) is False


def test_artifact_kickoff_detector_fail_open_on_missing_file(tmp_path: Path) -> None:
    # A read failure must NOT classify the file as a kickoff (fail-open: we would
    # rather keep a genuine report than silently drop it).
    assert _artifact_looks_like_kickoff(str(tmp_path / "nope.md")) is False


# ---------------------------------------------------------------------------
# 2) Text-level kickoff guard (command_service layer)
# ---------------------------------------------------------------------------


def test_kickoff_text_guard() -> None:
    assert _looks_like_kickoff_text("项目启动指令：请各节点认领任务") is True
    assert _looks_like_kickoff_text("[from node `writer-a`]\n正文……") is True
    assert _looks_like_kickoff_text("# 最终整合报告\n\n综合各方成果……") is False
    assert _looks_like_kickoff_text("") is False


# ---------------------------------------------------------------------------
# 3) Root on-disk deliverable recovery (command_service layer)
# ---------------------------------------------------------------------------


def _svc_with_artifact_store(store: dict) -> SimpleNamespace:
    return SimpleNamespace(_runtime=SimpleNamespace(_root_final_artifact=store))


def test_root_disk_deliverable_reads_recorded_report(tmp_path: Path) -> None:
    report = tmp_path / "整合稿.md"
    body = "# 最终整合报告\n\n综合策划、SEO、数据分析成果，形成完整方案。"
    report.write_text(body, encoding="utf-8")
    svc = _svc_with_artifact_store({"cmd1": ("editor-in-chief", str(report))})
    out = OrgCommandService._root_disk_deliverable(svc, "cmd1")
    assert out == body


def test_root_disk_deliverable_rejects_kickoff_file(tmp_path: Path) -> None:
    kickoff = tmp_path / "项目启动指令.md"
    kickoff.write_text("# 项目启动指令\n[dispatched to writer-a]\n", encoding="utf-8")
    svc = _svc_with_artifact_store({"cmd1": ("editor-in-chief", str(kickoff))})
    assert OrgCommandService._root_disk_deliverable(svc, "cmd1") is None


def test_root_disk_deliverable_none_when_unrecorded() -> None:
    svc = _svc_with_artifact_store({})
    assert OrgCommandService._root_disk_deliverable(svc, "cmd1") is None


def test_root_disk_deliverable_truncates_huge_report(tmp_path: Path) -> None:
    report = tmp_path / "长报告.md"
    report.write_text("正" * 20000, encoding="utf-8")
    svc = _svc_with_artifact_store({"cmd1": ("root", str(report))})
    out = OrgCommandService._root_disk_deliverable(svc, "cmd1")
    assert out is not None
    assert len(out) < 20000
    assert "见附件文件" in out
