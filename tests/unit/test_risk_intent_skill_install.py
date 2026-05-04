"""问题 1 回归测试：装技能页面误触高危 shell 拦截。

用户在前端"技能广场/装技能"页面发送的请求（含 GitHub URL / 本地 SKILL.md
路径），曾被 RiskIntentClassifier 误判为 OperationKind.EXECUTE +
TargetKind.SHELL_COMMAND，触发高危确认弹窗；用户确认后又因为
`classification.action is None` 报 "该操作尚无受控执行入口"。

这里覆盖：
1. 各种合法"装技能"表述应短路到 TargetKind.SKILL_INSTALL，
   requires_confirmation=False，action="install_skill"
2. 真正的 shell 命令（rm -rf, sudo 等）仍被识别为 HIGH/EXECUTE
3. 普通"装"动词不命中（避免过度泛化）
"""

from __future__ import annotations

import pytest

from openakita.core.risk_intent import (
    AccessMode,
    OperationKind,
    RiskLevel,
    TargetKind,
    classify_risk_intent,
)


# ---------------------------------------------------------------------------
# 正例：装技能 → SKILL_INSTALL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected_url_fragment,expected_path_fragment",
    [
        # 含 GitHub / Gitee / GitLab URL + 装动词 + 技能名词
        ("帮我装这个技能 https://github.com/owner/repo", "github.com/owner/repo", None),
        ("安装一下 https://gitee.com/foo/bar 这个技能", "gitee.com/foo/bar", None),
        ("install this skill: https://gitlab.com/x/y", "gitlab.com/x/y", None),
        ("把这个技能装上 https://github.com/openakita/skill-pack", "skill-pack", None),
        # URL 直接指向 SKILL.md
        ("https://github.com/owner/repo/blob/main/SKILL.md 帮我配一下", "SKILL.md", None),
        # 本地路径（绝对/相对）+ 装动词
        ("装一下 D:/workspace/skills/foo/SKILL.md", None, "SKILL.md"),
        ("帮我安装 plugins/avatar-studio/SKILL.md", None, "SKILL.md"),
        ("启用这个技能 ./local/skill.yaml", None, "skill.yaml"),
    ],
)
def test_skill_install_short_circuit(
    message: str,
    expected_url_fragment: str | None,
    expected_path_fragment: str | None,
) -> None:
    """合法的'装技能'请求应短路到 SKILL_INSTALL，不弹高危确认。"""
    result = classify_risk_intent(message)

    assert result.target_kind is TargetKind.SKILL_INSTALL, (
        f"应识别为 SKILL_INSTALL，但 target_kind={result.target_kind}, "
        f"reason={result.reason}, message={message!r}"
    )
    assert result.requires_confirmation is False, (
        f"装技能不应弹高危确认弹窗，但 requires_confirmation=True, message={message!r}"
    )
    assert result.action == "install_skill", (
        f"应路由到 install_skill 工具，但 action={result.action!r}"
    )
    assert result.risk_level is RiskLevel.LOW
    assert result.access_mode is AccessMode.WRITE
    assert result.operation_kind is OperationKind.WRITE
    assert result.reason == "skill_install_intent"

    if expected_url_fragment:
        assert "skill_url" in result.parameters, (
            f"应从消息提取 skill_url 参数，但 parameters={result.parameters}"
        )
        assert expected_url_fragment in result.parameters["skill_url"]

    if expected_path_fragment:
        assert "skill_path" in result.parameters, (
            f"应从消息提取 skill_path 参数，但 parameters={result.parameters}"
        )
        assert expected_path_fragment in result.parameters["skill_path"]


# ---------------------------------------------------------------------------
# 反例：真正的 shell 命令仍应高危
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "rm -rf /tmp/foo",
        "执行 sudo apt-get install nginx",
        "运行这条命令: kill -9 1234",
        "force push 到 main",
    ],
)
def test_shell_command_still_high_risk(message: str) -> None:
    """真正的 shell 高危命令应保持 HIGH + 弹确认，不能被新规则吞掉。"""
    result = classify_risk_intent(message)
    assert result.risk_level is RiskLevel.HIGH, (
        f"应为 HIGH，但 risk_level={result.risk_level}, reason={result.reason}, "
        f"message={message!r}"
    )
    assert result.requires_confirmation is True, (
        f"应弹高危确认，但 requires_confirmation=False, message={message!r}"
    )
    assert result.target_kind is not TargetKind.SKILL_INSTALL


# ---------------------------------------------------------------------------
# 反例：普通"装"动词（不带技能上下文）不命中
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # 装东西但不是技能
        "帮我装一下 nginx 的配置",
        # 谈论技能但不是装
        "这个技能怎么用？",
        # URL 但与装技能无关（无 skill 上下文）
        "看一下 https://github.com/owner/repo 这个仓库的星数",
        # 纯讨论
        "如果我让你装 SKILL，应该怎么处理？",
    ],
)
def test_non_skill_install_not_matched(message: str) -> None:
    """避免过度泛化：与装技能无关的输入不应被识别为 SKILL_INSTALL。"""
    result = classify_risk_intent(message)
    assert result.target_kind is not TargetKind.SKILL_INSTALL, (
        f"不应识别为 SKILL_INSTALL，但命中了。message={message!r}, "
        f"reason={result.reason}, parameters={result.parameters}"
    )


# ---------------------------------------------------------------------------
# 边界：用户日志里的真实复现样本
# ---------------------------------------------------------------------------


def test_real_world_log_sample_minimax_skill() -> None:
    """对应用户 2026-05-04 14:48 提供 minimax-docx 技能页面 URL 的场景。"""
    message = (
        "帮我装一下这个技能：https://github.com/MiniMax-AI/skills-minimax-docx"
    )
    result = classify_risk_intent(message)

    assert result.target_kind is TargetKind.SKILL_INSTALL
    assert result.requires_confirmation is False
    assert result.action == "install_skill"
    assert "skill_url" in result.parameters
    assert "MiniMax-AI" in result.parameters["skill_url"]
