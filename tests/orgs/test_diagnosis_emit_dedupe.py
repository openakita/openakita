"""OrgRuntime 失败诊断卡片 broadcast 去重测试

覆盖 verify_incomplete / max_iterations / loop_terminated 等异常退出在 30s 窗口
内同 (org, node, root_cause) 只 broadcast 一次，避免前端聊天气泡里出现多张
相同的"任务验证未通过"卡片。

直接测 _should_skip_diagnosis_emit 的判定逻辑，不引入 OrgRuntime 全量初始化
（其依赖 OrgManager 的 lifecycle，不利于单元化测试）。
"""

from __future__ import annotations

import time

import pytest

from openakita.orgs.runtime import OrgRuntime


@pytest.fixture()
def rt() -> OrgRuntime:
    """裸实例：手动初始化必要字段，避开完整 __init__ 的副作用。"""
    obj = OrgRuntime.__new__(OrgRuntime)
    obj._recent_diagnosis_emit = {}
    obj._diagnosis_emit_window_secs = 30.0
    return obj


class TestDiagnosisEmitDedupe:
    def test_first_emit_passes(self, rt: OrgRuntime):
        assert rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete") is False

    def test_duplicate_within_window_skipped(self, rt: OrgRuntime):
        rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete")
        # 紧接的相同 (org, node, root_cause) 应该被抑制
        assert rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete") is True

    def test_different_root_cause_not_deduped(self, rt: OrgRuntime):
        rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete")
        assert rt._should_skip_diagnosis_emit("org_a", "n1", "max_iterations") is False

    def test_different_node_not_deduped(self, rt: OrgRuntime):
        rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete")
        assert rt._should_skip_diagnosis_emit("org_a", "n2", "verify_incomplete") is False

    def test_different_org_not_deduped(self, rt: OrgRuntime):
        rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete")
        assert rt._should_skip_diagnosis_emit("org_b", "n1", "verify_incomplete") is False

    def test_window_expiry_allows_reemit(self, rt: OrgRuntime):
        rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete")
        # 把时间戳人工倒退到窗口外
        key = ("org_a", "n1", "verify_incomplete")
        rt._recent_diagnosis_emit[key] = time.time() - 100.0
        assert rt._should_skip_diagnosis_emit("org_a", "n1", "verify_incomplete") is False

    def test_unknown_root_cause_treated_as_unknown(self, rt: OrgRuntime):
        # 空字符串与 None 都规约成 "unknown"，便于 dict 用作 key
        assert rt._should_skip_diagnosis_emit("org_a", "n1", "") is False
        # 第二次同样 "" 在窗口内应被抑制
        assert rt._should_skip_diagnosis_emit("org_a", "n1", "") is True


class TestToolInflightAcquireRelease:
    """_try_acquire_tool_inflight / _release_tool_inflight 基础语义"""

    @pytest.fixture()
    def rt2(self) -> OrgRuntime:
        obj = OrgRuntime.__new__(OrgRuntime)
        obj._tool_inflight_keys = {}
        obj._tool_inflight_window_secs = 5.0
        return obj

    def test_first_acquire_succeeds(self, rt2: OrgRuntime):
        assert rt2._try_acquire_tool_inflight("k1") is True

    def test_second_acquire_within_window_fails(self, rt2: OrgRuntime):
        assert rt2._try_acquire_tool_inflight("k1") is True
        assert rt2._try_acquire_tool_inflight("k1") is False

    def test_release_allows_immediate_reacquire(self, rt2: OrgRuntime):
        rt2._try_acquire_tool_inflight("k1")
        rt2._release_tool_inflight("k1")
        assert rt2._try_acquire_tool_inflight("k1") is True

    def test_different_keys_independent(self, rt2: OrgRuntime):
        assert rt2._try_acquire_tool_inflight("k1") is True
        assert rt2._try_acquire_tool_inflight("k2") is True
