"""
Windows 桌面自动化 - 配置管理

支持从环境变量和配置文件加载配置
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CaptureConfig:
    """截图配置"""
    default_monitor: int = 0
    compression_quality: int = 85
    max_width: int = 1920
    max_height: int = 1080
    cache_ttl: float = 1.0  # 截图缓存时间（秒）
    
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
    """UIAutomation 配置"""
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
    """视觉识别配置"""
    enabled: bool = True
    model: str = "qwen3-vl-plus"
    fallback_model: str = "qwen-vl-plus"
    ocr_model: str = "qwen-vl-ocr"
    max_retries: int = 2
    timeout: float = 30.0
    
    @classmethod
    def from_env(cls) -> "VisionConfig":
        return cls(
            enabled=os.getenv("DESKTOP_VISION_ENABLED", "true").lower() == "true",
            model=os.getenv("DESKTOP_VISION_MODEL", "qwen3-vl-plus"),
            fallback_model=os.getenv("DESKTOP_VISION_FALLBACK_MODEL", "qwen-vl-plus"),
            ocr_model=os.getenv("DESKTOP_VISION_OCR_MODEL", "qwen-vl-ocr"),
            max_retries=int(os.getenv("DESKTOP_VISION_MAX_RETRIES", "2")),
            timeout=float(os.getenv("DESKTOP_VISION_TIMEOUT", "30.0")),
        )


@dataclass
class ActionConfig:
    """操作配置"""
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
    """桌面自动化总配置"""
    enabled: bool = True
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    uia: UIAConfig = field(default_factory=UIAConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    actions: ActionConfig = field(default_factory=ActionConfig)
    
    # 日志配置
    log_actions: bool = True
    log_screenshots: bool = False  # 是否保存截图到日志
    log_dir: Optional[str] = None
    
    @classmethod
    def from_env(cls) -> "DesktopConfig":
        """从环境变量加载配置"""
        return cls(
            enabled=os.getenv("DESKTOP_ENABLED", "true").lower() == "true",
            capture=CaptureConfig.from_env(),
            uia=UIAConfig.from_env(),
            vision=VisionConfig.from_env(),
            actions=ActionConfig.from_env(),
            log_actions=os.getenv("DESKTOP_LOG_ACTIONS", "true").lower() == "true",
            log_screenshots=os.getenv("DESKTOP_LOG_SCREENSHOTS", "false").lower() == "true",
            log_dir=os.getenv("DESKTOP_LOG_DIR"),
        )
    
    @classmethod
    def default(cls) -> "DesktopConfig":
        """返回默认配置"""
        return cls()


# 全局配置实例
_config: Optional[DesktopConfig] = None


def get_config() -> DesktopConfig:
    """获取全局配置"""
    global _config
    if _config is None:
        _config = DesktopConfig.from_env()
    return _config


def set_config(config: DesktopConfig) -> None:
    """设置全局配置"""
    global _config
    _config = config


def reset_config() -> None:
    """重置配置为默认值"""
    global _config
    _config = None
