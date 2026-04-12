"""
适配器注册表：集中管理 IM 适配器的工厂创建函数

替代 main.py 中的 _create_bot_adapter if/elif 分支，
新增通道只需在此注册工厂函数。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

AdapterFactory = Callable[..., Any]

ADAPTER_REGISTRY: dict[str, AdapterFactory] = {}
_ADAPTER_OWNERS: dict[str, str] = {}


def register_adapter(bot_type: str, factory: AdapterFactory, *, owner: str = "builtin") -> None:
    existing_owner = _ADAPTER_OWNERS.get(bot_type)
    if existing_owner and existing_owner != owner:
        logger.warning(
            "Adapter '%s' already registered by '%s', rejecting registration from '%s'",
            bot_type,
            existing_owner,
            owner,
        )
        return
    ADAPTER_REGISTRY[bot_type] = factory
    _ADAPTER_OWNERS[bot_type] = owner


def unregister_adapter(bot_type: str, *, owner: str = "") -> bool:
    """Remove a registered adapter factory. Only the original owner may unregister."""
    current_owner = _ADAPTER_OWNERS.get(bot_type, "")
    if owner and current_owner and current_owner != owner:
        logger.warning(
            "Cannot unregister adapter '%s': owned by '%s', requested by '%s'",
            bot_type,
            current_owner,
            owner,
        )
        return False
    removed = ADAPTER_REGISTRY.pop(bot_type, None)
    _ADAPTER_OWNERS.pop(bot_type, None)
    if removed is not None:
        logger.info("Unregistered adapter type '%s'", bot_type)
    return removed is not None


def _cred_bool(val: Any) -> bool | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def _safe_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _create_feishu(
    creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str, domain: str = "feishu",
):
    from .adapters import FeishuAdapter
    return FeishuAdapter(
        app_id=creds.get("app_id", ""),
        app_secret=creds.get("app_secret", ""),
        domain=domain,
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
        streaming_enabled=_cred_bool(creds.get("streaming_enabled")),
        group_streaming=_cred_bool(creds.get("group_streaming")),
        streaming_throttle_ms=_safe_int(creds.get("streaming_throttle_ms"), None),
        group_response_mode=creds.get("group_response_mode") or None,
        footer_elapsed=_cred_bool(creds.get("footer_elapsed")),
        footer_status=_cred_bool(creds.get("footer_status")),
    )


def _create_telegram(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import TelegramAdapter
    kwargs: dict[str, Any] = dict(
        bot_token=creds.get("bot_token", ""),
        webhook_url=creds.get("webhook_url") or None,
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
    )
    if creds.get("pairing_code"):
        kwargs["pairing_code"] = creds["pairing_code"]
    if creds.get("proxy"):
        kwargs["proxy"] = creds["proxy"]
    rp = _cred_bool(creds.get("require_pairing"))
    if rp is not None:
        kwargs["require_pairing"] = rp
    fe = _cred_bool(creds.get("footer_elapsed"))
    if fe is not None:
        kwargs["footer_elapsed"] = fe
    fs = _cred_bool(creds.get("footer_status"))
    if fs is not None:
        kwargs["footer_status"] = fs
    return TelegramAdapter(**kwargs)


def _create_dingtalk(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import DingTalkAdapter
    return DingTalkAdapter(
        app_key=creds.get("app_key", creds.get("client_id", "")),
        app_secret=creds.get("app_secret", creds.get("client_secret", "")),
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
        footer_elapsed=_cred_bool(creds.get("footer_elapsed")),
        footer_status=_cred_bool(creds.get("footer_status")),
    )


def _create_wework(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import WeWorkBotAdapter
    return WeWorkBotAdapter(
        corp_id=creds.get("corp_id", ""),
        token=creds.get("token", ""),
        encoding_aes_key=creds.get("encoding_aes_key", ""),
        callback_port=_safe_int(creds.get("callback_port", 9880), 9880),
        callback_host=creds.get("callback_host", "0.0.0.0"),
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
    )


def _create_wework_ws(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import WeWorkWsAdapter
    return WeWorkWsAdapter(
        bot_id=creds.get("bot_id", ""),
        secret=creds.get("secret", ""),
        ws_url=creds.get("ws_url", "wss://openws.work.weixin.qq.com"),
        channel_name=channel_name, bot_id_alias=bot_id, agent_profile_id=agent_profile_id,
        webhook_url=creds.get("webhook_url", ""),
    )


def _create_onebot(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import OneBotAdapter
    return OneBotAdapter(
        ws_url=creds.get("ws_url", "ws://127.0.0.1:8080"),
        access_token=creds.get("access_token") or None,
        mode=creds.get("mode", "forward"),
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
    )


def _create_onebot_reverse(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import OneBotAdapter
    return OneBotAdapter(
        access_token=creds.get("access_token") or None,
        mode="reverse",
        reverse_host=creds.get("reverse_host", "0.0.0.0"),
        reverse_port=_safe_int(creds.get("reverse_port", 6700), 6700),
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
    )


def _create_qqbot(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import QQBotAdapter
    return QQBotAdapter(
        app_id=creds.get("app_id", ""),
        app_secret=creds.get("app_secret", ""),
        sandbox=_cred_bool(creds.get("sandbox")) or False,
        mode=creds.get("mode", "websocket"),
        webhook_port=_safe_int(creds.get("webhook_port", 9890), 9890),
        webhook_path=creds.get("webhook_path", "/qqbot/callback"),
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
        public_api_url=creds.get("public_api_url", ""),
        footer_elapsed=_cred_bool(creds.get("footer_elapsed")),
    )


def _create_wechat(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    from .adapters import WeChatAdapter
    return WeChatAdapter(
        token=creds.get("token", ""),
        base_url=creds.get("base_url", ""),
        cdn_base_url=creds.get("cdn_base_url", ""),
        channel_name=channel_name, bot_id=bot_id, agent_profile_id=agent_profile_id,
        footer_elapsed=_cred_bool(creds.get("footer_elapsed")),
        route_tag=creds.get("route_tag", ""),
    )


def _create_lark(creds: dict, *, channel_name: str, bot_id: str, agent_profile_id: str):
    return _create_feishu(
        creds,
        channel_name=channel_name, bot_id=bot_id,
        agent_profile_id=agent_profile_id, domain="lark",
    )


# 自动注册所有内置适配器
register_adapter("feishu", _create_feishu)
register_adapter("lark", _create_lark)
register_adapter("telegram", _create_telegram)
register_adapter("dingtalk", _create_dingtalk)
register_adapter("wework", _create_wework)
register_adapter("wework_ws", _create_wework_ws)
register_adapter("onebot", _create_onebot)
register_adapter("onebot_reverse", _create_onebot_reverse)
register_adapter("qqbot", _create_qqbot)
register_adapter("wechat", _create_wechat)
