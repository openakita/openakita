"""
Windows Desktop Automation — Vision Recognition Module

UI visual recognition powered by DashScope Qwen-VL.
"""

from .analyzer import VisionAnalyzer, get_vision_analyzer
from .prompts import PromptTemplates

__all__ = [
    "VisionAnalyzer",
    "PromptTemplates",
    "get_vision_analyzer",
]
