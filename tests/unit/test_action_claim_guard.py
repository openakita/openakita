from openakita.core.reasoning_engine import (
    _extract_unbacked_verbs,
    _get_action_claim_re,
    _guard_unbacked_action_claim,
    _successful_tool_names,
)


def test_action_claim_regex_matches_memory_claims():
    assert _get_action_claim_re().search("我已经记住了这个偏好")
    assert _get_action_claim_re().search("已保存到记忆")


def test_action_claim_regex_matches_fake_tool_receipts_from_issue_424():
    assert _get_action_claim_re().search("✅ 工具已实际调用！")
    assert _get_action_claim_re().search("write_file ✅ 已调用")
    assert _get_action_claim_re().search("已通过 read_file 工具验证")


def test_unbacked_memory_claim_is_downgraded():
    guarded = _guard_unbacked_action_claim("我已经帮你保存到记忆了", [])

    assert "没有检测到长期记忆写入凭证" in guarded
    assert "已经帮你保存到记忆" not in guarded


def test_backed_action_claim_is_kept():
    text = "已帮你创建文件。"

    assert _guard_unbacked_action_claim(text, ["write_file"]) == text


def test_unbacked_fake_tool_receipt_without_tools_is_downgraded():
    text = (
        "✅ 工具已实际调用！\n\n"
        "| 步骤 | 工具 | 状态 |\n"
        "| 写入文件 | write_file | ✅ 已调用 |\n"
        "| 验证写入 | read_file | ✅ 已调用 |"
    )

    guarded = _guard_unbacked_action_claim(text, [])

    assert "没有检测到实际工具执行凭证" in guarded
    assert "write_file" not in guarded


def test_unbacked_named_tool_receipt_with_unrelated_tool_is_warned():
    text = "读取文件 | read_file | ✅ 已调用"

    guarded = _guard_unbacked_action_claim(text, ["write_file"])

    assert "一致性提示" in guarded
    assert "read_file调用" in guarded


def test_backed_named_tool_receipt_is_kept():
    text = "读取文件 | read_file | ✅ 已调用"

    assert _guard_unbacked_action_claim(text, ["read_file"]) == text


def test_unbacked_delete_claim_with_unrelated_tool_is_warned():
    """LLM 谎称已删除文件，但只调用了 get_tool_info — 必须追加警告。"""
    text = "已删除 token_cost_calc.py。"

    guarded = _guard_unbacked_action_claim(text, ["get_tool_info"])

    assert text in guarded  # 原文保留
    assert "一致性提示" in guarded
    assert "删除" in guarded


def test_failed_delete_call_does_not_back_claim():
    """delete_file 调用失败 → 'is_error': True，不应被算作成功凭证。"""
    text = "已删除 README.md。"
    tool_results = [{"tool_name": "delete_file", "is_error": True}]

    guarded = _guard_unbacked_action_claim(text, ["delete_file"], tool_results)

    assert "一致性提示" in guarded


def test_successful_delete_call_backs_claim():
    """delete_file 成功 → 不追加警告。"""
    text = "已删除 README.md。"
    tool_results = [{"tool_name": "delete_file", "is_error": False}]

    assert _guard_unbacked_action_claim(text, ["delete_file"], tool_results) == text


def test_failed_write_file_with_unrelated_success_does_not_back_save_claim():
    """#382: write_file 被策略拒绝后，list_directory 成功不能背书“已保存”。"""
    text = "报告已成功保存！文件位置：D:/Akita/workspaces/default/资本论文本深度分析报告.md"
    tool_results = [
        {
            "tool_name": "write_file",
            "is_error": True,
            "content": "⚠️ 策略拒绝: 操作被拒绝: create 在 protected 区域",
        },
        {
            "tool_name": "list_directory",
            "is_error": False,
            "content": "目录内容: data, logs",
        },
    ]

    guarded = _guard_unbacked_action_claim(
        text,
        ["list_directory"],
        tool_results,
    )

    assert text in guarded
    assert "一致性提示" in guarded
    assert "保存" in guarded
    assert "list_directory" in guarded
    assert "write_file" not in guarded.partition("本轮成功执行的工具是 ")[2]


def test_successful_retry_backs_save_claim_after_earlier_write_failure():
    """同一工具先失败后成功时，成功回执应避免过度一致性提示。"""
    text = "报告已成功保存！文件位置：D:/Akita/workspaces/default/report.md"
    tool_results = [
        {
            "tool_name": "write_file",
            "is_error": True,
            "content": "⚠️ 策略拒绝: 操作被拒绝",
        },
        {
            "tool_name": "write_file",
            "is_error": False,
            "content": "文件已写入: D:/Akita/workspaces/default/report.md (1024 bytes)",
        },
    ]

    guarded = _guard_unbacked_action_claim(text, ["write_file"], tool_results)

    assert guarded == text


def test_unbacked_send_claim_with_unrelated_tools_is_warned():
    """LLM 说已发送但本轮没有任何 deliver_artifacts/send_* 工具调用。"""
    text = "已发送结果到群里。"

    guarded = _guard_unbacked_action_claim(text, ["read_file", "search_memory"])

    assert "一致性提示" in guarded
    assert "发送" in guarded


def test_unbacked_move_claim_with_read_only_tool_is_warned():
    """#435: 只读取文件不能背书“已移动成功”。"""
    text = "文件已移动成功。"

    guarded = _guard_unbacked_action_claim(text, ["read_file"])

    assert text in guarded
    assert "一致性提示" in guarded
    assert "移动" in guarded


def test_successful_move_file_backs_move_claim():
    text = "文件已移动成功。"

    assert _guard_unbacked_action_claim(text, ["move_file"]) == text


def test_successful_write_delete_pair_backs_move_claim():
    """兼容旧执行方式：读写目标后删除源文件，也能构成移动凭证。"""
    text = "文件已移动成功。"

    assert _guard_unbacked_action_claim(text, ["write_file", "delete_file"]) == text


def test_action_claim_without_action_verb_is_passed_through():
    """文本里没有任何 V→T 映射动词 → 即使有 prefix 也不报警（避免误拦）。"""
    text = "已帮你分析完毕，结论如上。"

    assert _guard_unbacked_action_claim(text, ["read_file"]) == text


def test_successful_tool_names_filter_failures():
    succeeded = _successful_tool_names(
        ["delete_file", "edit_file", "read_file"],
        [
            {"tool_name": "delete_file", "is_error": True},
            {"tool_name": "edit_file", "is_error": False},
        ],
    )
    assert "edit_file" in succeeded
    assert "read_file" in succeeded  # no result entry → assumed ok
    assert "delete_file" not in succeeded


def test_successful_tool_names_keep_tool_with_later_success():
    succeeded = _successful_tool_names(
        ["write_file"],
        [
            {"tool_name": "write_file", "is_error": True},
            {"tool_name": "write_file", "is_error": False},
        ],
    )

    assert succeeded == {"write_file"}


def test_extract_unbacked_verbs_only_after_prefix():
    """『需要修改』不算 claim，『已修改』才算。"""
    assert _extract_unbacked_verbs("我会修改这个文件", set()) == []
    assert _extract_unbacked_verbs("已修改这个文件", set()) == ["修改"]
    assert _extract_unbacked_verbs("已移动这个文件", {"read_file"}) == ["移动"]
