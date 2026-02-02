"""
Windows 桌面自动化 - 视觉识别模块

基于 DashScope Qwen-VL 实现 UI 视觉识别
"""

from .analyzer import VisionAnalyzer, get_vision_analyzer
from .prompts import PromptTemplates

__all__ = [
    "VisionAnalyzer",
    "PromptTemplates",
    "get_vision_analyzer",
]
