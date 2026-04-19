"""
IM channel adapters

Platform-specific implementations:
- Telegram
- Feishu (Lark)
- WeCom (smart bot — HTTP callback)
- WeCom (smart bot — WebSocket long connection)
- DingTalk
- OneBot (universal protocol)
- QQ Official Bot
- WeChat personal account (iLink Bot API)
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
