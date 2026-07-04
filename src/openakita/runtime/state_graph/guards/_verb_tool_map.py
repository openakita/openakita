"""Verb / tool name -> backing-tool fragment maps.

Extracted from ``core/reasoning_engine.py`` (P-RC-5 P5.5) as a
shared data table used by the unbacked-action-claim guard.

* :data:`CLAIMED_TOOL_TO_FRAGMENTS` -- direct tool-name claim: when the
  LLM text mentions ``write_file``, the guard expects a backing
  tool call whose name contains ``write_file``.
* :data:`VERB_TO_TOOL_FRAGMENTS` -- Chinese mutating-verb claim: when
  the LLM text contains a high-risk Chinese verb (删除 / 保存 /
  发送 ...), the guard expects at least one backing tool whose
  name contains any of the listed fragments.

Both dicts are byte-identical to the legacy versions; the
``unbacked-action-claim`` guard extracted in P5.6 will import them
from here, and ``core/reasoning_engine.py`` re-imports them under
the legacy private names for backward compatibility.
"""

from __future__ import annotations

__all__ = ["CLAIMED_TOOL_TO_FRAGMENTS", "VERB_TO_TOOL_FRAGMENTS"]


CLAIMED_TOOL_TO_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "write_file": ("write_file",),
    "edit_file": ("edit_file",),
    "read_file": ("read_file",),
    "run_shell": ("run_shell",),
    "run_powershell": ("run_powershell",),
    "deliver_artifacts": ("deliver_artifacts",),
    "schedule_task": ("schedule_task",),
    "add_memory": ("add_memory",),
    "move_file": ("move_file",),
    "delete_file": ("delete_file",),
}


# 动词 → 候选工具名片段（小写子串匹配）。
# 当 LLM 文本里说"已删除/已发送..."时，必须有匹配片段的工具在本轮"成功"
# 执行过；否则按幻觉降级处理。映射只覆盖最常被滥用的高风险动词，避免
# 误拦低风险描述（如"已分析/已生成"等）。
VERB_TO_TOOL_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "删除": (
        "delete_file",
        "delete_memory",
        "remove",
        "cancel_scheduled_task",
        "run_shell",
        "run_powershell",
    ),
    "删掉": ("delete_file", "delete_memory", "remove", "run_shell", "run_powershell"),
    "清空": ("delete_file", "run_shell", "run_powershell"),
    "编辑": ("edit_file",),
    "修改": ("edit_file", "update_user_profile", "update_scheduled_task"),
    "覆盖": ("write_file", "edit_file"),
    "写入": ("write_file", "edit_file"),
    "保存": (
        "write_file",
        "edit_file",
        "add_memory",
        "update_user_profile",
        "create_plan_file",
        "create_todo",
        "schedule_task",
    ),
    "保存到记忆": ("add_memory", "update_user_profile"),
    # "更新" 覆盖 F1 场景常见的"我更新一下记录/已更新记录"措辞——若无实际记忆/
    # 文件写入工具凭证，则按虚假声称降级告警（不覆盖原文，仅追加一致性提示）。
    "更新": (
        "update_user_profile",
        "add_memory",
        "edit_file",
        "write_file",
        "update_scheduled_task",
    ),
    "记住": ("add_memory", "update_user_profile"),
    "记录": ("add_memory", "create_todo", "schedule_task", "create_plan_file"),
    "存入": ("add_memory", "update_user_profile", "write_file"),
    "创建": ("write_file", "create_todo", "schedule_task", "create_agent", "create_plan_file"),
    "添加": ("add_memory", "create_todo", "schedule_task", "edit_file"),
    "安排": ("schedule_task", "create_todo"),
    "移动": ("move_file", "run_shell", "run_powershell", "write_file", "delete_file"),
    "移至": ("move_file", "run_shell", "run_powershell", "write_file", "delete_file"),
    "重命名": ("move_file", "run_shell", "run_powershell"),
    "复制": ("write_file", "run_shell", "run_powershell"),
    "发送": ("deliver_artifacts", "send_to_chat", "smtp_email_sender", "send_message"),
    "调度": ("schedule_task",),
    "提醒": ("schedule_task",),
    "安装": ("install_skill",),
    "卸载": ("uninstall_skill",),
    "读取": ("read_file", "run_shell", "run_powershell"),
}
