from openakita.channels.bot_config import BotConfigRule, BotConfigStore


def test_bot_config_rules_are_scoped_by_bot_instance_id(tmp_path):
    store = BotConfigStore(tmp_path / "bot_config.json")

    store.set_rule(
        BotConfigRule(
            channel="feishu:writer",
            chat_id="chat-1",
            user_id="*",
            enabled=False,
            response_mode="disabled",
        )
    )

    assert store.is_enabled("feishu:writer", "chat-1", "user-1") is False
    assert store.is_enabled("feishu:reviewer", "chat-1", "user-1") is True
    assert store.get_response_mode("feishu:writer", "chat-1", "user-1") == "disabled"
    assert store.get_response_mode("feishu:reviewer", "chat-1", "user-1") is None
