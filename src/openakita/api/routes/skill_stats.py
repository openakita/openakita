"""技能用量统计 API。

GET /api/stats/skills/usage/stats — 按时间窗口聚合的技能加载/编辑统计

数据来自追加式事件日志 ``data/skill_usage_events.jsonl``（见
``openakita.skills.usage_events``），在读取时聚合，供前端监控面板的
「技能用量」页面渲染趋势图、概览卡片和热门技能榜。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from openakita.skills.usage_events import get_skill_usage_log

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats/skills", tags=["统计"])


@router.get("/usage/stats")
async def usage_stats(days: int = Query(7, ge=1, le=365)):
    """返回最近 ``days`` 天的技能用量聚合。

    Args:
        days: 统计窗口（天），默认 7，裁剪到 [1, 365]。
    """
    try:
        return get_skill_usage_log().aggregate(days)
    except Exception as e:  # noqa: BLE001
        logger.error("[SkillStats] usage stats failed: %s", e)
        return {"error": f"Failed to read skill usage stats: {e}"}
