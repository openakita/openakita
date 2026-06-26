"""
IM 通道适配器

各平台的具体实现:
- Telegram
- 飞书
- 企业微信（智能机器人 — HTTP 回调）
- 企业微信（智能机器人 — WebSocket 长连接）
- 钉钉
- OneBot (通用协议)
- QQ 官方机器人
- 微信个人号（iLink Bot API）
"""

from .dingtalk import DingTalkAdapter
from .feishu import FeishuAdapter
from .onebot import OneBotAdapter
from .qq_official import QQBotAdapter
from .telegram import TelegramAdapter
from .wechat import WeChatAdapter
from .wework_bot import WeWorkBotAdapter
from .wework_ws import WeWorkWsAdapter

__all__ = [
    "TelegramAdapter",
    "FeishuAdapter",
    "WeWorkBotAdapter",
    "WeWorkWsAdapter",
    "DingTalkAdapter",
    "OneBotAdapter",
    "QQBotAdapter",
    "WeChatAdapter",
]
