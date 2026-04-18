"""
Messaging channel module

Provides multi-platform IM integration:
- Unified message types
- Channel adapters
- Message gateway
- Media processing
"""

from .base import ChannelAdapter
from .gateway import MessageGateway
from .types import (
    MediaFile,
    MessageContent,
    MessageType,
    OutgoingMessage,
    UnifiedMessage,
)

__all__ = [
    # Types
    "MessageType",
    "UnifiedMessage",
    "MessageContent",
    "MediaFile",
    "OutgoingMessage",
    # Adapters
    "ChannelAdapter",
    # Gateway
    "MessageGateway",
]
