from types import SimpleNamespace

from openakita.channels.status import collect_effective_im_status


class _Adapter:
    channel_name = "feishu:bot1"
    channel_type = "feishu"
    bot_id = "bot1"
    is_running = True


def test_effective_status_treats_im_bots_as_configured():
    settings = SimpleNamespace(
        telegram_enabled=False,
        feishu_enabled=False,
        wework_enabled=False,
        wework_ws_enabled=False,
        dingtalk_enabled=False,
        onebot_enabled=False,
        qqbot_enabled=False,
        wechat_enabled=False,
        im_bots=[
            {
                "id": "bot1",
                "type": "feishu",
                "name": "飞书",
                "enabled": True,
                "credentials": {
                    "app_id": "cli_xxx",
                    "app_secret": "secret_xxx",
                },
            }
        ],
    )

    status = collect_effective_im_status(settings)

    assert status["channels"] == ["feishu"]
    feishu_bot = next(
        detail for detail in status["details"] if detail["source"] == "im_bots"
    )
    assert feishu_bot["configured"] is True
    assert feishu_bot["missing"] == []


def test_effective_status_marks_runtime_adapter_as_seen():
    settings = SimpleNamespace(
        telegram_enabled=False,
        feishu_enabled=False,
        wework_enabled=False,
        wework_ws_enabled=False,
        dingtalk_enabled=False,
        onebot_enabled=False,
        qqbot_enabled=False,
        wechat_enabled=False,
        im_bots=[],
    )
    gateway = SimpleNamespace(_adapters={"feishu:bot1": _Adapter()})

    status = collect_effective_im_status(settings, gateway)

    assert status["channels"] == ["feishu"]
    feishu_env = next(
        detail
        for detail in status["details"]
        if detail["source"] == "env" and detail["type"] == "feishu"
    )
    assert feishu_env["configured"] is False
    assert feishu_env["runtime_seen"] is True
    assert feishu_env["runtime_status"] == "online"
