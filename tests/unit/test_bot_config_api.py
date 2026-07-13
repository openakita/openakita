import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from openakita.api.routes.agents import BotCreateRequest, _validate_bot_credentials, create_bot


def test_wework_ws_requires_all_credentials() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validate_bot_credentials("wework_ws", {"bot_id": "bot-1"})

    assert exc_info.value.status_code == 400
    assert "secret" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_bot_rolls_back_when_runtime_start_fails(monkeypatch) -> None:
    import openakita.config as config

    original_bots = config.settings.im_bots
    saves: list[list[dict]] = []

    async def fail_apply(_bot: dict) -> bool:
        return False

    monkeypatch.setattr(config.settings, "im_bots", [], raising=False)
    monkeypatch.setattr(config.runtime_state, "save", lambda: saves.append(list(config.settings.im_bots)))
    main_stub = ModuleType("openakita.main")
    main_stub.apply_im_bot = fail_apply
    main_stub.get_im_bot_runtime_error = lambda _channel: "authentication rejected"
    monkeypatch.setitem(sys.modules, "openakita.main", main_stub)

    try:
        with pytest.raises(HTTPException) as exc_info:
            await create_bot(
                BotCreateRequest(
                    id="warehouse",
                    type="wework_ws",
                    credentials={"bot_id": "bot-1", "secret": "secret-1"},
                )
            )

        assert exc_info.value.status_code == 502
        assert config.settings.im_bots == []
        assert len(saves) == 2
        assert saves[0][0]["id"] == "warehouse"
        assert saves[1] == []
    finally:
        config.settings.im_bots = original_bots


def test_runtime_status_reports_missing_credentials() -> None:
    from openakita.channels.status import collect_effective_im_status

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
                "id": "warehouse",
                "type": "wework_ws",
                "enabled": True,
                "credentials": {},
            }
        ],
    )

    status = collect_effective_im_status(settings)
    detail = next(item for item in status["details"] if item["source"] == "im_bots")

    assert detail["configured"] is False
    assert detail["missing"] == ["bot_id", "secret"]
    assert detail["runtime_status"] == "unknown"
