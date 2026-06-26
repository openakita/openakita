"""针对 ``openakita.skills.allowlist_io`` 的单元测试。

覆盖：
- ``read_allowlist`` 的各种文件状态（不存在 / 缺字段 / 空列表 / 损坏 JSON / 含空白项）
- ``overwrite_allowlist`` 正常写入 + 原子性（os.replace 异常时不损坏原文件 + 清理 tmp 文件）
- ``upsert_skill_ids`` / ``remove_skill_ids`` 的文件缺失语义（保持「未声明 = 全部启用」）
- 50 线程并发写入 ``overwrite_allowlist`` 下的互斥性（_WRITE_LOCK）与最终文件完整性
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture：把 settings.project_root 重定向到 tmp_path，隔离测试工作区
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch):
    """让 allowlist_io._skills_json_path() 指向 tmp_path/data/skills.json。"""
    from openakita.config import settings as real_settings

    monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)
    (tmp_path / "data").mkdir(exist_ok=True)
    return tmp_path


def _skills_file(workspace: Path) -> Path:
    return workspace / "data" / "skills.json"


# ---------------------------------------------------------------------------
# read_allowlist
# ---------------------------------------------------------------------------


class TestReadAllowlist:
    def test_file_missing_returns_none(self, isolated_workspace):
        from openakita.skills import allowlist_io

        path, allowlist = allowlist_io.read_allowlist()
        assert path == _skills_file(isolated_workspace)
        assert allowlist is None

    def test_field_missing_returns_none(self, isolated_workspace):
        """文件存在但无 external_allowlist 字段：语义为『全部启用』→ None。"""
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(json.dumps({"version": 1}), encoding="utf-8")

        _, allowlist = allowlist_io.read_allowlist()
        assert allowlist is None

    def test_empty_list_returns_empty_set(self, isolated_workspace):
        """显式空列表：语义为『禁用全部外部技能』→ set()。"""
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(
            json.dumps({"version": 1, "external_allowlist": []}), encoding="utf-8"
        )

        _, allowlist = allowlist_io.read_allowlist()
        assert allowlist == set()

    def test_normal_list_parsed(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(
            json.dumps({"version": 1, "external_allowlist": ["skill-a", "skill-b", "skill-a"]}),
            encoding="utf-8",
        )

        _, allowlist = allowlist_io.read_allowlist()
        assert allowlist == {"skill-a", "skill-b"}

    def test_whitespace_entries_filtered(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(
            json.dumps({"version": 1, "external_allowlist": ["  skill-a  ", "", "   ", "b"]}),
            encoding="utf-8",
        )

        _, allowlist = allowlist_io.read_allowlist()
        assert allowlist == {"skill-a", "b"}

    def test_corrupt_json_returns_none(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text("{not json", encoding="utf-8")

        _, allowlist = allowlist_io.read_allowlist()
        assert allowlist is None

    def test_empty_file_returns_none(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text("", encoding="utf-8")

        _, allowlist = allowlist_io.read_allowlist()
        assert allowlist is None


# ---------------------------------------------------------------------------
# overwrite_allowlist
# ---------------------------------------------------------------------------


class TestOverwriteAllowlist:
    def test_writes_sorted_list(self, isolated_workspace):
        from openakita.skills import allowlist_io

        path = allowlist_io.overwrite_allowlist({"zzz", "aaa", "mmm"})
        assert path == _skills_file(isolated_workspace)

        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["version"] == 1
        assert content["external_allowlist"] == ["aaa", "mmm", "zzz"]
        assert "updated_at" in content

    def test_none_writes_empty_list(self, isolated_workspace):
        from openakita.skills import allowlist_io

        path = allowlist_io.overwrite_allowlist(None)
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["external_allowlist"] == []

    def test_empty_set_writes_empty_list(self, isolated_workspace):
        from openakita.skills import allowlist_io

        path = allowlist_io.overwrite_allowlist(set())
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["external_allowlist"] == []

    def test_atomic_on_os_replace_failure(self, isolated_workspace, monkeypatch):
        """os.replace 抛异常时：原文件保持不变 + tmp 文件被清理。"""
        from openakita.skills import allowlist_io

        # 预置一份有效内容
        allowlist_io.overwrite_allowlist({"initial"})
        path = _skills_file(isolated_workspace)
        original_bytes = path.read_bytes()

        real_replace = os.replace

        def boom(src, dst):
            # 只在针对目标文件时失败，避免误伤
            if str(dst) == str(path):
                raise OSError("simulated crash during replace")
            return real_replace(src, dst)

        monkeypatch.setattr(allowlist_io.os, "replace", boom)

        with pytest.raises(OSError):
            allowlist_io.overwrite_allowlist({"new"})

        # 原文件字节未变
        assert path.read_bytes() == original_bytes

        # tmp 文件已清理（目录下只剩 skills.json）
        leftovers = [
            p
            for p in path.parent.iterdir()
            if p.name.startswith(".skills.") and p.name.endswith(".json.tmp")
        ]
        assert leftovers == [], f"leftover tmp files: {leftovers}"

    def test_creates_parent_dir(self, tmp_path: Path, monkeypatch):
        """data 目录不存在时，overwrite 应自动创建。"""
        from openakita.config import settings as real_settings
        from openakita.skills import allowlist_io

        fresh = tmp_path / "fresh-workspace"
        monkeypatch.setattr(real_settings, "project_root", fresh, raising=False)
        assert not (fresh / "data").exists()

        allowlist_io.overwrite_allowlist({"x"})
        assert (fresh / "data" / "skills.json").exists()


# ---------------------------------------------------------------------------
# upsert_skill_ids
# ---------------------------------------------------------------------------


class TestUpsertSkillIds:
    def test_file_missing_returns_none(self, isolated_workspace):
        """skills.json 不存在时 upsert 不创建文件（保持全部启用语义）。"""
        from openakita.skills import allowlist_io

        result = allowlist_io.upsert_skill_ids({"newbie"})
        assert result is None
        assert not _skills_file(isolated_workspace).exists()

    def test_empty_ids_noop(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(
            json.dumps({"version": 1, "external_allowlist": ["a"]}), encoding="utf-8"
        )
        assert allowlist_io.upsert_skill_ids(set()) is None

    def test_no_allowlist_field_returns_none(self, isolated_workspace):
        """skills.json 存在但没有 external_allowlist：保持未声明语义，不 upsert。"""
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(json.dumps({"version": 1}), encoding="utf-8")
        result = allowlist_io.upsert_skill_ids({"newbie"})
        assert result is None
        # 原文件未被修改为含 allowlist
        cfg = json.loads(_skills_file(isolated_workspace).read_text(encoding="utf-8"))
        assert "external_allowlist" not in cfg

    def test_merges_into_existing(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(
            json.dumps({"version": 1, "external_allowlist": ["a", "b"]}),
            encoding="utf-8",
        )

        path = allowlist_io.upsert_skill_ids({"c", "b"})
        assert path is not None
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["external_allowlist"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# remove_skill_ids
# ---------------------------------------------------------------------------


class TestRemoveSkillIds:
    def test_file_missing_returns_none(self, isolated_workspace):
        from openakita.skills import allowlist_io

        assert allowlist_io.remove_skill_ids({"x"}) is None

    def test_empty_ids_noop(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(
            json.dumps({"version": 1, "external_allowlist": ["a"]}), encoding="utf-8"
        )
        assert allowlist_io.remove_skill_ids(set()) is None

    def test_no_allowlist_field_returns_none(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(json.dumps({"version": 1}), encoding="utf-8")
        assert allowlist_io.remove_skill_ids({"x"}) is None

    def test_removes_existing(self, isolated_workspace):
        from openakita.skills import allowlist_io

        _skills_file(isolated_workspace).write_text(
            json.dumps({"version": 1, "external_allowlist": ["a", "b", "c"]}),
            encoding="utf-8",
        )

        path = allowlist_io.remove_skill_ids({"b", "nonexistent"})
        assert path is not None
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["external_allowlist"] == ["a", "c"]


# ---------------------------------------------------------------------------
# 并发写入
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_50_threads_overwrite_produces_consistent_file(self, isolated_workspace):
        """50 个线程并发 overwrite：
        - 所有写者应全部成功（_WRITE_LOCK 串行化，不会因互相抢占引发异常）。
        - 最终文件内容必须**完全等于**某一次写入的集合（而不是多个写入串在一起）。

        注：不在此处启动并发读者线程——Windows 下 ``os.replace`` 与打开中的文件
        句柄存在锁冲突（非 allowlist_io 的缺陷），该跨平台读写冲突属于 OS 原子性
        语义差异，不纳入本模块契约。
        """
        from openakita.skills import allowlist_io

        N = 50
        written_sets: list[set[str]] = [
            {f"worker-{tid}-item-{i}" for i in range(tid + 1)} for tid in range(N)
        ]

        errors: list[BaseException] = []
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(allowlist_io.overwrite_allowlist, s) for s in written_sets]
            for f in as_completed(futures):
                try:
                    f.result()
                except BaseException as e:  # noqa: BLE001
                    errors.append(e)

        assert errors == [], f"concurrent overwrite raised: {[type(e).__name__ for e in errors]}"

        final = json.loads(_skills_file(isolated_workspace).read_text(encoding="utf-8"))
        final_set = set(final["external_allowlist"])
        assert any(final_set == s for s in written_sets), (
            f"final set {final_set} does not match any written set"
        )

    def test_serial_writes_all_atomic(self, isolated_workspace):
        """串行多次写入后，每次中间状态都应是完整 JSON（原子性回归）。"""
        from openakita.skills import allowlist_io

        for i in range(20):
            allowlist_io.overwrite_allowlist({f"s{j}" for j in range(i + 1)})
            cfg = json.loads(_skills_file(isolated_workspace).read_text(encoding="utf-8"))
            assert isinstance(cfg.get("external_allowlist"), list)
            assert len(cfg["external_allowlist"]) == i + 1
