from openakita.core.reasoning_engine import (
    _get_action_claim_re,
    _guard_unbacked_action_claim,
)


def test_action_claim_regex_matches_memory_claims():
    assert _get_action_claim_re().search("我已经记住了这个偏好")
    assert _get_action_claim_re().search("已保存到记忆")


def test_unbacked_memory_claim_is_downgraded():
    guarded = _guard_unbacked_action_claim("我已经帮你保存到记忆了", [])

    assert "没有检测到长期记忆写入凭证" in guarded
    assert "已经帮你保存到记忆" not in guarded


def test_backed_action_claim_is_kept():
    text = "已帮你创建文件。"

    assert _guard_unbacked_action_claim(text, ["write_file"]) == text
