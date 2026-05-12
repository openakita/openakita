"""P1-4: '执行' must not trigger the high-risk shell gate when the user is
clearly asking for sub-agent delegation, nor when '脚本' refers to content
('视频脚本', '宣传脚本') instead of a shell script.
"""

from openakita.core.risk_intent import (
    OperationKind,
    RiskIntentClassifier,
    RiskLevel,
    TargetKind,
)


def _classify(message: str):
    return RiskIntentClassifier().classify(message)


def test_parallel_delegation_with_content_script_is_not_shell_risk():
    message = (
        "并行委托：让 video-planner 写 30 秒抖音宣传脚本，"
        "让 marketing-planner 写 3 条小红书文案。要并发执行。"
    )
    result = _classify(message)
    assert result.operation_kind != OperationKind.EXECUTE
    assert result.target_kind != TargetKind.SHELL_COMMAND
    assert result.risk_level in {RiskLevel.NONE, RiskLevel.LOW}, (
        f"Expected delegation request to stay low-risk, got {result.risk_level}"
    )
    assert result.requires_confirmation is False


def test_video_script_alone_does_not_trigger_shell_gate():
    message = "执行一下我们的视频脚本，把它做成正式版"
    result = _classify(message)
    # 'execute' + '脚本' but '脚本' here is content, not shell — should not
    # tag SHELL_COMMAND.
    assert result.target_kind != TargetKind.SHELL_COMMAND
    assert result.operation_kind != OperationKind.EXECUTE


def test_real_shell_command_still_flagged():
    message = "请帮我执行这条 shell 命令：rm -rf /tmp/foo"
    result = _classify(message)
    assert result.operation_kind == OperationKind.EXECUTE
    assert result.target_kind == TargetKind.SHELL_COMMAND
    assert result.requires_confirmation is True


def test_powershell_script_path_is_flagged():
    message = "执行一下 D:\\scripts\\backup.ps1"
    result = _classify(message)
    assert result.operation_kind == OperationKind.EXECUTE


def test_delegate_to_subagent_is_low_risk():
    message = "把这件事委托给 customer-support agent，让它去回复用户"
    result = _classify(message)
    assert result.risk_level in {RiskLevel.NONE, RiskLevel.LOW}
    assert result.target_kind != TargetKind.SHELL_COMMAND


def test_fan_out_in_parallel_keyword_downgrades_execute():
    message = "fan out in parallel to all sub-agents and execute the briefs"
    result = _classify(message)
    assert result.operation_kind != OperationKind.EXECUTE
