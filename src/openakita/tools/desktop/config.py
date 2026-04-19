"""
Windows Desktop Automation - Configuration Management

Supports loading configuration from environment variables and config files
"""

import os
from dataclasses import dataclass, field


@dataclass
class CaptureConfig:
    """Screenshot capture configuration."""

    default_monitor: int = 0
    compression_quality: int = 85
    max_width: int = 1920
    max_height: int = 1080
    cache_ttl: float = 1.0  # Screenshot cache TTL (seconds)

    @classmethod
    def from_env(cls) -> "CaptureConfig":
        return cls(
            default_monitor=int(os.getenv("DESKTOP_DEFAULT_MONITOR", "0")),
            compression_quality=int(os.getenv("DESKTOP_COMPRESSION_QUALITY", "85")),
            max_width=int(os.getenv("DESKTOP_MAX_WIDTH", "1920")),
            max_height=int(os.getenv("DESKTOP_MAX_HEIGHT", "1080")),
            cache_ttl=float(os.getenv("DESKTOP_CACHE_TTL", "1.0")),
        )


@dataclass
class UIAConfig:
    """UIAutomation configuration."""

    timeout: float = 5.0
    retry_interval: float = 0.5
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> "UIAConfig":
        return cls(
            timeout=float(os.getenv("DESKTOP_UIA_TIMEOUT", "5.0")),
            retry_interval=float(os.getenv("DESKTOP_UIA_RETRY_INTERVAL", "0.5")),
            max_retries=int(os.getenv("DESKTOP_UIA_MAX_RETRIES", "3")),
        )


@dataclass
class VisionConfig:
    """Vision recognition configuration."""

    enabled: bool = True
    max_retries: int = 2
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "VisionConfig":
        return cls(
            enabled=os.getenv("DESKTOP_VISION_ENABLED", "true").lower() == "true",
            max_retries=int(os.getenv("DESKTOP_VISION_MAX_RETRIES", "2")),
            timeout=float(os.getenv("DESKTOP_VISION_TIMEOUT", "30.0")),
        )


@dataclass
class ActionConfig:
    """Action configuration."""

    click_delay: float = 0.1
    type_interval: float = 0.03
    move_duration: float = 0.15
    failsafe: bool = True
    pause_between_actions: float = 0.1

    @classmethod
    def from_env(cls) -> "ActionConfig":
        return cls(
            click_delay=float(os.getenv("DESKTOP_CLICK_DELAY", "0.1")),
            type_interval=float(os.getenv("DESKTOP_TYPE_INTERVAL", "0.03")),
            move_duration=float(os.getenv("DESKTOP_MOVE_DURATION", "0.15")),
            failsafe=os.getenv("DESKTOP_FAILSAFE", "true").lower() == "true",
            pause_between_actions=float(os.getenv("DESKTOP_PAUSE", "0.1")),
        )


@dataclass
class DesktopConfig:
    """Desktop automation top-level configuration."""

    enabled: bool = True
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    uia: UIAConfig = field(default_factory=UIAConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    actions: ActionConfig = field(default_factory=ActionConfig)

    @classmethod
    def from_env(cls) -> "DesktopConfig":
        """Load configuration from environment variables."""
        return cls(
            enabled=os.getenv("DESKTOP_ENABLED", "true").lower() == "true",
            capture=CaptureConfig.from_env(),
            uia=UIAConfig.from_env(),
            vision=VisionConfig.from_env(),
            actions=ActionConfig.from_env(),
        )

    @classmethod
    def default(cls) -> "DesktopConfig":
        """Return the default configuration."""
        return cls()


# Global configuration instance
_config: DesktopConfig | None = None


def get_config() -> DesktopConfig:
    """Get the global configuration."""
    global _config
    if _config is None:
        _config = DesktopConfig.from_env()
    return _config


def set_config(config: DesktopConfig) -> None:
    """Set the global configuration."""
    global _config
    _config = config


def reset_config() -> None:
    """Reset configuration to defaults."""
    global _config
    _config = None
