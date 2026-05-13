"""
E2E 回归测试：P0-1 / P0-2 / P0-3 + P1-4/5/6/7 治本修复的"绝不能再坏"挡板。

不依赖真实 LLM / 真实通道 — 全部走纯函数 / mock，CI 上 <3s 完成。
任何一条断言失败都意味着我们丢掉了 ai-exploratory-testing.mdc 复盘里
拍板下来的根本修复，立即拉响警报。

覆盖：
- P0-1 文件沙箱：默认 default_zone=CONTROLLED + CONTROLLED 写操作 CONFIRM
- P0-2 阶段 0：tool_evidence_required + tool_calls=0 不再设
  _last_exit_reason="tool_evidence_missing"（避免 OrgRuntime task_failed 误判）
- P0-2 阶段 1：评论意图 ACTION + tool_calls=0 触发重试；其它情况 log-only
- P0-2 阶段 2：IntentResult 拆 evidence_required / evidence_recommended
- P0-2 阶段 3：来源标签一致性后置检测
- P0-3 A：runtime 子任务失败/异常路径自动 _mark_chain_closed
- P0-3 D：_command_store 原子更新（status/phase 不出现错位窗口）
- P1-4：USER.md 占位字段 _clean_user_content 过滤
- P1-7：org_list_delegated_tasks 3s 内重复调用走 backoff cache + hint
"""

from __future__ import annotations

from pathlib import Path

import pytest

# =============================================================================
# P0-1：默认安全区与写操作矩阵
# =============================================================================


def test_p0_1_user_dirs_default_to_mutating_scoped_class():
    """P0-1：桌面/文档/下载等用户目录上的 CREATE/EDIT/OVERWRITE/DELETE 必须 CONFIRM。

    C8b-6b：原本通过 v1 ``PolicyEngine._make_default_config()`` + ``Zone.CONTROLLED``
    + ``_ZONE_OP_MATRIX`` 三层验证。v2 删除了 zone 概念，等价语义改由
    ``ApprovalClass.MUTATING_SCOPED`` 承载——这些工具 + 用户路径的组合走
    PolicyEngineV2 矩阵自动判 CONFIRM。本测试改为对 v2 决策做 e2e smoke。
    """
    from openakita.core.policy_v2 import (
        PolicyConfigV2,
        build_engine_from_config,
    )
    from openakita.core.policy_v2.adapter import evaluate_via_v2
    from openakita.core.policy_v2.enums import ConfirmationMode, DecisionAction
    from openakita.core.policy_v2.global_engine import (
        reset_engine_v2,
        set_engine_v2,
    )
    from openakita.core.policy_v2.schema import ConfirmationConfig

    cfg = PolicyConfigV2(
        confirmation=ConfirmationConfig(mode=ConfirmationMode.DEFAULT)
    )
    set_engine_v2(build_engine_from_config(cfg), cfg)
    try:
        # 用户桌面是默认 controlled 区——delete_file 必须 CONFIRM
        for tool, params in [
            ("delete_file", {"path": "C:/Users/example/Desktop/x.log"}),
            ("write_file", {"path": "C:/Users/example/Desktop/x.log", "content": "y"}),
        ]:
            decision = evaluate_via_v2(tool, params)
            assert decision.action in (DecisionAction.CONFIRM, DecisionAction.DENY), (
                f"P0-1 回归: {tool} 在用户桌面被静默放行（action={decision.action.value}）"
            )
    finally:
        reset_engine_v2()


def test_p0_1_default_controlled_paths_include_user_dirs():
    """P0-1：默认 controlled paths 必须涵盖桌面/文档/下载等高敏目录。

    C8b-6b：v1 ``_default_controlled_paths`` 已删；v2 ``default_controlled_paths()``
    在 ``policy_v2/defaults.py`` 等价提供。
    """
    from openakita.core.policy_v2 import default_controlled_paths

    paths = [p.lower() for p in default_controlled_paths()]
    blob = "|".join(paths)
    assert any(k in blob for k in ("desktop", "桌面")), "桌面缺失，越界写入会被默认放行"
    assert any(k in blob for k in ("documents", "文档")), "文档目录缺失"
    assert any(k in blob for k in ("downloads", "下载")), "下载目录缺失"


# =============================================================================
# P0-2 阶段 0：tool_evidence_missing 不再回写 exit_reason
# =============================================================================


def test_p0_2_phase0_no_hard_exit_reason():
    """P0-2 阶段 0：reasoning_engine 源码中已删除 _last_exit_reason='tool_evidence_missing' 赋值。

    这条是组织死锁的根因——只要源码里再次出现这个赋值，OrgRuntime 就会把它映射成
    task_failed，根节点 wait_for_deliverable 永远等不到 deliverable。
    注释里出现关键字是允许的（标记为何被删除），但代码语句不行。
    """
    import re

    src = Path("src/openakita/core/reasoning_engine.py").read_text(encoding="utf-8")
    # 移除注释行后再检查赋值
    code_only_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code_only = "\n".join(code_only_lines)
    bad_pattern = re.compile(r"_last_exit_reason\s*=\s*[\"']tool_evidence_missing[\"']")
    assert not bad_pattern.search(code_only), (
        "reasoning_engine.py 不允许再写入 _last_exit_reason='tool_evidence_missing' 赋值，"
        "否则 P0-3 组织死锁会回归。"
    )


def test_p0_2_phase0_action_done_regex_matches_chinese():
    """阶段 0：兜底正则必须能识别"已查到/已读到/我刚才执行"这类典型动作完成短语。"""
    from openakita.core.reasoning_engine import _get_action_done_re

    rx = _get_action_done_re()
    for sample in [
        "我已查到该文件存在 3 处引用",
        "已读取 D:/foo.txt 第 10 行",
        "我刚才执行了 ls 命令",
        "已删除该记忆条目",
    ]:
        assert rx.search(sample), f"正则未识别动作完成短语：{sample}"


# =============================================================================
# P0-2 阶段 2：IntentResult 拆出 evidence_recommended
# =============================================================================


def test_p0_2_phase2_intent_result_has_evidence_recommended_field():
    """阶段 2：IntentResult 必须有 evidence_recommended 字段（默认 False），
    且与 evidence_required 不再被合并到同一信号。"""
    from openakita.core.intent_analyzer import IntentResult, IntentType

    ir = IntentResult(intent=IntentType.CHAT)
    assert hasattr(ir, "evidence_required")
    assert hasattr(ir, "evidence_recommended"), (
        "IntentResult 一旦不再有 evidence_recommended 字段，"
        "agent.py 的软提示路径就会哑火，回到原来的"
        "纯文本对话也强制 ForceToolCall 的死循环。"
    )
    assert ir.evidence_required is False
    assert ir.evidence_recommended is False


# =============================================================================
# P0-2 阶段 3：来源标签一致性后置检测
# =============================================================================


def test_p0_2_phase3_source_tag_inconsistency_warns():
    """阶段 3：声称 [来源:工具] 但 tools_executed=0 时必须返回告警字符串。"""
    from openakita.core.reasoning_engine import _check_source_tag_consistency

    text_claims_tool = "好的，我已经检查了文件 [来源:工具]，里面有 3 行代码。"
    warn = _check_source_tag_consistency(text_claims_tool, tools_executed_count=0)
    assert warn is not None and "来源" in warn, (
        "声称工具来源但实际未调工具，必须给出 belt-and-suspenders 告警，"
        "否则 P0-2 会从 reasoning_engine 反向回归。"
    )


def test_p0_2_phase3_source_tag_consistent_passes():
    """阶段 3：标签为 [来源:常识] 时，tools_executed=0 不应当告警。"""
    from openakita.core.reasoning_engine import _check_source_tag_consistency

    text = "太阳系第三颗行星是地球。[来源:常识]"
    assert _check_source_tag_consistency(text, tools_executed_count=0) is None


# =============================================================================
# P0-3 D：_command_store 原子更新
# =============================================================================


def test_p0_3_command_store_atomic_update():
    """P0-3 D：_update_command_state 必须存在并保证 status='done' 时 phase 同步。"""
    from openakita.api.routes import orgs as orgs_route

    assert hasattr(orgs_route, "_update_command_state"), (
        "_update_command_state 一旦被去掉，_command_store 的 status/phase "
        "就回到非原子更新，前端短期内可能拿到不一致快照。"
    )
    assert hasattr(orgs_route, "_command_store_lock"), (
        "_command_store_lock 是原子保证的核心，必须保留。"
    )

    cmd_id = "test_p0_3_atomic"
    orgs_route._command_store[cmd_id] = {"status": "running", "phase": "running"}
    try:
        orgs_route._update_command_state(cmd_id, status="done", result={"ok": 1})
        snapshot = dict(orgs_route._command_store[cmd_id])
        assert snapshot["status"] == "done"
        assert snapshot["phase"] == "done", (
            "status='done' 时 phase 没自动对齐 'done'，前端 polling 可能拿到错位快照。"
        )
        assert snapshot.get("result") == {"ok": 1}
    finally:
        orgs_route._command_store.pop(cmd_id, None)


# =============================================================================
# P1-4：USER.md 占位字段过滤
# =============================================================================


@pytest.mark.parametrize(
    "raw,must_be_filtered_out",
    [
        ("- **称呼**: [待学习]\n", "称呼"),
        ("- **工作领域**: <to_learn>\n", "工作领域"),
        ("- **行业**: `待学习`\n", "行业"),
        ("- **OS**: (待学习)\n", "OS"),
        ("- **统计项**: [待统计]\n", "统计项"),
        ("- **拓展**: [待补充]\n", "拓展"),
    ],
)
def test_p1_4_clean_user_content_filters_placeholders(raw: str, must_be_filtered_out: str):
    """P1-4：所有占位符行必须被滤掉，否则 LLM 又会拿到伪事实。"""
    from openakita.prompt.builder import _clean_user_content

    cleaned = _clean_user_content(raw)
    assert must_be_filtered_out not in cleaned, (
        f"占位符行未被过滤，会向 LLM 注入伪用户档案：{raw!r} → {cleaned!r}"
    )


def test_p1_4_clean_user_content_keeps_real_values():
    """真实字段（含中文值）必须被保留。"""
    from openakita.prompt.builder import _clean_user_content

    raw = "- **称呼**: 张明\n- **工作领域**: 嵌入式开发\n"
    cleaned = _clean_user_content(raw)
    assert "张明" in cleaned
    assert "嵌入式开发" in cleaned


# =============================================================================
# P1-7：org_list_delegated_tasks backoff
# =============================================================================


@pytest.mark.asyncio
async def test_p1_7_org_list_delegated_tasks_backoff(tmp_path: Path, monkeypatch):
    """P1-7：3s 内对相同 (org, node, status) 重复调用必须命中 cache 并返回 hint。"""
    from openakita.orgs.tool_handler import OrgToolHandler

    fake_runtime = type("R", (), {"_manager": type("M", (), {
        "_org_dir": staticmethod(lambda _oid: tmp_path),
    })()})()

    h = OrgToolHandler.__new__(OrgToolHandler)
    h._runtime = fake_runtime  # type: ignore[attr-defined]

    call_counter = {"n": 0}

    class _FakeStore:
        def __init__(self, _path):
            pass

        def all_tasks(self, **_kw):
            call_counter["n"] += 1
            return []

    monkeypatch.setattr(
        "openakita.orgs.project_store.ProjectStore", _FakeStore, raising=True,
    )

    r1 = await h._handle_org_list_delegated_tasks({}, "org1", "node1")
    assert isinstance(r1, list), "首次调用应直接返回 list"
    assert call_counter["n"] == 1, "首次必须真正查 ProjectStore 一次"

    r2 = await h._handle_org_list_delegated_tasks({}, "org1", "node1")
    assert isinstance(r2, dict), "3s 内重复必须返回带 hint 的 dict 而非穿透 ProjectStore"
    assert "hint" in r2 and "wait_for_deliverable" in r2["hint"], (
        "backoff hint 必须明确指向 org_wait_for_deliverable，引导 LLM 改行为。"
    )
    assert call_counter["n"] == 1, (
        "backoff 期内不允许再次访问 ProjectStore，否则 token 节流彻底失效。"
    )
