"""Local mock generation — no external API keys.

Used for UI/flow testing. Tasks are tagged with api_task_id prefix ``mock-``;
use TaskManager.delete_mock_tasks() or DELETE /tasks/purge-mock to remove.

Disable with config ``mock_mode=0`` or env ``OPENAKITA_ECOM_MOCK=0``.
Force-always with ``mock_mode=1`` or ``OPENAKITA_ECOM_MOCK=1``.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from ecom_execution import safe_format, split_params

# Stable placeholder media (no API key). User can delete mock tasks anytime.
MOCK_VIDEO_URL = "https://www.w3.org/2010/05/video/movie_300.webm"


def mock_mode_from_env() -> str | None:
    v = os.environ.get("OPENAKITA_ECOM_MOCK", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return "1"
    if v in ("0", "false", "no", "off"):
        return "0"
    return None


def should_use_mock(
    *,
    feature_provider: str,
    mock_mode_cfg: str | None,
    has_dashscope: bool,
    has_ark: bool,
) -> bool:
    """Tri-state mock_mode_cfg: empty/None = auto, '0' = off, '1' = always."""
    env = mock_mode_from_env()
    if env == "1":
        return True
    if env == "0":
        return False
    mode = (mock_mode_cfg or "").strip().lower()
    if mode == "1" or mode == "always":
        return True
    if mode == "0" or mode == "off" or mode == "false":
        return False
    # auto (default): mock when the required client is missing
    if feature_provider == "dashscope":
        return not has_dashscope
    if feature_provider == "ark":
        return not has_ark
    return False


def build_mock_prompt(feature: Any, params: dict) -> str:
    text_params, _ = split_params(feature, params)
    prompt = safe_format(feature.prompt_template or "", text_params).strip()
    if not prompt:
        prompt = (
            str(params.get("prompt") or params.get("storyboard_script") or "").strip()
        )
    if not prompt:
        prompt = "【演示】未填写描述 — 这是本地模拟生成，配置真实 API Key 后可关闭演示模式。"
    return f"{prompt}\n\n[演示数据 · mock · 可一键清除]"


def mock_image_urls(task_id: str, count: int) -> list[str]:
    n = max(1, min(int(count), 6))
    base = task_id.replace("-", "")[:16] or uuid.uuid4().hex[:12]
    return [f"https://picsum.photos/seed/ecom{base}{i}/800/800" for i in range(n)]


def mock_delay_seconds() -> float:
    try:
        return float(os.environ.get("OPENAKITA_ECOM_MOCK_DELAY", "2.0"))
    except ValueError:
        return 2.0
