"""
IM 通道适配器

各平台的具体实现:
- Telegram
- 飞书
- 企业微信（智能机器人）
- 钉钉
- OneBot (通用协议)
- QQ 官方机器人
"""

from .dingtalk import DingTalkAdapter
from .feishu import FeishuAdapter
from .onebot import OneBotAdapter
from .qq_official import QQBotAdapter
from .telegram import TelegramAdapter
from .wework_bot import WeWorkBotAdapter

__all__ = [
    "TelegramAdapter",
    "FeishuAdapter",
    "WeWorkBotAdapter",
    "DingTalkAdapter",
    "OneBotAdapter",
    "QQBotAdapter",
]
