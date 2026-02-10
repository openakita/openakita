"""
活人感引擎 (Proactive Engine)

负责管理主动消息的生成、频率控制和反馈跟踪。
通过调度器心跳定时触发，根据人格设定和用户反馈自适应调整。

核心原则:
- 不骚扰: 严格频率控制 + 反馈驱动
- 有价值: 基于记忆和上下文
- 人格一致: 风格匹配当前人格
- 可关闭: 一句话关闭
"""

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..core.persona import PersonaManager
    from ..memory import MemoryManager

logger = logging.getLogger(__name__)


# ── 配置 ──────────────────────────────────────────────────────────


@dataclass
class ProactiveConfig:
    """活人感引擎配置"""

    enabled: bool = False
    max_daily_messages: int = 3
    min_interval_minutes: int = 120
    quiet_hours_start: int = 23  # 安静时段开始
    quiet_hours_end: int = 7  # 安静时段结束
    idle_threshold_hours: int = 24  # 多久没互动才发闲聊


# ── 反馈跟踪 ──────────────────────────────────────────────────────


@dataclass
class ProactiveRecord:
    """主动消息发送记录"""

    msg_type: str  # greeting/task_followup/memory_recall/idle_chat/goodnight
    timestamp: datetime = field(default_factory=datetime.now)
    reaction: Optional[str] = None  # positive/negative/ignored
    response_delay_minutes: Optional[float] = None


class ProactiveFeedbackTracker:
    """跟踪用户对主动消息的反应，驱动频率自适应"""

    def __init__(self, data_file: Path | str):
        self.data_file = Path(data_file) if not isinstance(data_file, Path) else data_file
        self.records: list[ProactiveRecord] = []
        self._load()

    def _load(self) -> None:
        if self.data_file.exists():
            try:
                data = json.loads(self.data_file.read_text(encoding="utf-8"))
                for rec in data.get("records", []):
                    self.records.append(
                        ProactiveRecord(
                            msg_type=rec["msg_type"],
                            timestamp=datetime.fromisoformat(rec["timestamp"]),
                            reaction=rec.get("reaction"),
                            response_delay_minutes=rec.get("response_delay_minutes"),
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to load proactive feedback: {e}")

    def _save(self) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "records": [
                {
                    "msg_type": r.msg_type,
                    "timestamp": r.timestamp.isoformat(),
                    "reaction": r.reaction,
                    "response_delay_minutes": r.response_delay_minutes,
                }
                for r in self.records[-200:]  # 只保留最近 200 条
            ]
        }
        self.data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_send(self, msg_type: str, timestamp: Optional[datetime] = None) -> None:
        """记录一次主动消息发送"""
        self.records.append(
            ProactiveRecord(msg_type=msg_type, timestamp=timestamp or datetime.now())
        )
        self._save()

    def record_reaction(
        self, reaction_type: str, response_delay_minutes: float = 0
    ) -> None:
        """
        记录用户对最近一条主动消息的反应

        reaction_type: positive/negative/ignored
        - positive: 用户在 10 分钟内积极回应
        - negative: 用户表示"别发了"/"太烦了"等
        - ignored: 超过 2 小时未回应
        """
        # 找到最近一条未标记反应的记录
        for rec in reversed(self.records):
            if rec.reaction is None:
                rec.reaction = reaction_type
                rec.response_delay_minutes = response_delay_minutes
                break
        self._save()

    def get_today_send_count(self) -> int:
        """今日已发送的主动消息数"""
        today = datetime.now().date()
        return sum(1 for r in self.records if r.timestamp.date() == today)

    def get_last_send_time(self) -> Optional[datetime]:
        """最后一次发送时间"""
        if self.records:
            return self.records[-1].timestamp
        return None

    def get_adjusted_config(self, base_config: ProactiveConfig) -> ProactiveConfig:
        """根据历史反馈动态调整频率"""
        # 获取最近 30 天的记录
        cutoff = datetime.now() - timedelta(days=30)
        recent = [r for r in self.records if r.timestamp > cutoff and r.reaction]

        if len(recent) < 5:
            return base_config  # 数据不够，保持默认

        total = len(recent)
        positive = sum(1 for r in recent if r.reaction == "positive")
        negative = sum(1 for r in recent if r.reaction == "negative")
        ignored = sum(1 for r in recent if r.reaction == "ignored")

        adjusted = ProactiveConfig(
            enabled=base_config.enabled,
            max_daily_messages=base_config.max_daily_messages,
            min_interval_minutes=base_config.min_interval_minutes,
            quiet_hours_start=base_config.quiet_hours_start,
            quiet_hours_end=base_config.quiet_hours_end,
            idle_threshold_hours=base_config.idle_threshold_hours,
        )

        # negative 出现 -> 大幅降低
        if negative > 0:
            adjusted.max_daily_messages = max(1, base_config.max_daily_messages - 2)
            adjusted.min_interval_minutes = base_config.min_interval_minutes + 120
            logger.info("Proactive frequency reduced due to negative feedback")

        # ignored 率 > 50% -> 降低
        elif ignored / total > 0.5:
            adjusted.max_daily_messages = max(1, base_config.max_daily_messages - 1)
            adjusted.min_interval_minutes = base_config.min_interval_minutes + 60
            logger.info("Proactive frequency reduced due to high ignore rate")

        # positive 率 > 80% -> 可以提高
        elif positive / total > 0.8:
            adjusted.max_daily_messages = min(5, base_config.max_daily_messages + 1)
            adjusted.min_interval_minutes = max(60, base_config.min_interval_minutes - 30)
            logger.info("Proactive frequency increased due to positive feedback")

        return adjusted


# ── 活人感引擎 ────────────────────────────────────────────────────


class ProactiveEngine:
    """活人感引擎，管理主动消息的触发和生成"""

    # 消息类型
    MSG_TYPES = [
        "morning_greeting",  # 早安问候
        "task_followup",  # 任务跟进
        "memory_recall",  # 关键回顾
        "idle_chat",  # 闲聊问候
        "goodnight",  # 晚安提醒
        "special_day",  # 天气/节日
    ]

    def __init__(
        self,
        config: ProactiveConfig,
        feedback_file: Path | str,
        persona_manager: Optional["PersonaManager"] = None,
        memory_manager: Optional["MemoryManager"] = None,
    ):
        self.config = config
        self.persona_manager = persona_manager
        self.memory_manager = memory_manager
        self.feedback = ProactiveFeedbackTracker(feedback_file)
        self._last_user_interaction: Optional[datetime] = None

    def update_user_interaction(self, timestamp: Optional[datetime] = None) -> None:
        """记录用户最近一次互动时间"""
        self._last_user_interaction = timestamp or datetime.now()

    def toggle(self, enabled: bool) -> None:
        """开关活人感模式"""
        self.config.enabled = enabled
        logger.info(f"Proactive mode {'enabled' if enabled else 'disabled'}")

    async def heartbeat(self) -> Optional[dict[str, Any]]:
        """
        心跳检查 - 由调度器每 30 分钟调用一次

        Returns:
            如果需要发送消息，返回 {"type": str, "content": str, "sticker_mood": str|None}
            否则返回 None
        """
        if not self.config.enabled:
            return None

        # 获取自适应配置
        effective_config = self.feedback.get_adjusted_config(self.config)

        # 检查安静时段
        now = datetime.now()
        hour = now.hour
        if effective_config.quiet_hours_start > effective_config.quiet_hours_end:
            # 跨午夜 (如 23:00-07:00)
            if hour >= effective_config.quiet_hours_start or hour < effective_config.quiet_hours_end:
                return None
        else:
            # 同日 (如 01:00-05:00)
            if effective_config.quiet_hours_start <= hour < effective_config.quiet_hours_end:
                return None

        # 检查今日发送限额
        today_count = self.feedback.get_today_send_count()
        if today_count >= effective_config.max_daily_messages:
            return None

        # 检查最小间隔
        last_send = self.feedback.get_last_send_time()
        if last_send:
            elapsed = (now - last_send).total_seconds() / 60
            if elapsed < effective_config.min_interval_minutes:
                return None

        # 决定消息类型
        msg_type = self._decide_message_type(now, effective_config)
        if not msg_type:
            return None

        # 生成消息内容
        result = await self._generate_message(msg_type)
        if result:
            self.feedback.record_send(msg_type)
        return result

    def _decide_message_type(self, now: datetime, config: ProactiveConfig) -> Optional[str]:
        """根据当前状态决定要发送的消息类型"""
        hour = now.hour

        # 早安 (7-9 点，当日还没发过)
        if 7 <= hour <= 9:
            today_types = [
                r.msg_type
                for r in self.feedback.records
                if r.timestamp.date() == now.date()
            ]
            if "morning_greeting" not in today_types:
                return "morning_greeting"

        # 晚安 (21-22 点)
        if 21 <= hour <= 22:
            today_types = [
                r.msg_type
                for r in self.feedback.records
                if r.timestamp.date() == now.date()
            ]
            if "goodnight" not in today_types:
                # 只有亲近角色才发晚安
                if self.persona_manager:
                    merged = self.persona_manager.get_merged_persona()
                    if merged.emotional_distance in ("close", "intimate"):
                        return "goodnight"

        # 长时间未互动 -> 闲聊
        if self._last_user_interaction:
            idle_hours = (now - self._last_user_interaction).total_seconds() / 3600
            if idle_hours >= config.idle_threshold_hours:
                return "idle_chat"

        # 任务跟进（如果有未完成任务）
        if self.memory_manager and random.random() < 0.3:
            return "task_followup"

        # 关键回顾
        if self.memory_manager and random.random() < 0.2:
            return "memory_recall"

        return None

    async def _generate_message(self, msg_type: str) -> Optional[dict[str, Any]]:
        """根据消息类型生成内容（这里提供模板，实际可由 LLM 生成）"""
        persona_name = "default"
        sticker_mood = None

        if self.persona_manager:
            merged = self.persona_manager.get_merged_persona()
            persona_name = merged.preset_name

        templates = self._get_templates(persona_name)

        if msg_type == "morning_greeting":
            options = templates.get("morning") or ["早上好！新的一天开始了~"]
            content = random.choice(options)
            sticker_mood = "greeting"

        elif msg_type == "goodnight":
            options = templates.get("goodnight") or ["晚安，早点休息~"]
            content = random.choice(options)
            sticker_mood = "greeting"

        elif msg_type == "idle_chat":
            options = templates.get("idle") or ["好久没聊了，最近怎么样？"]
            if not options:
                return None
            content = random.choice(options)

        elif msg_type == "task_followup":
            content = await self._generate_task_followup()
            if not content:
                return None

        elif msg_type == "memory_recall":
            content = await self._generate_memory_recall()
            if not content:
                return None

        else:
            return None

        return {
            "type": msg_type,
            "content": content,
            "sticker_mood": sticker_mood,
        }

    def _get_templates(self, persona_name: str) -> dict[str, list[str]]:
        """根据人格获取消息模板"""
        base_templates = {
            "morning": ["早上好！新的一天开始了~", "早安！今天也要加油哦"],
            "goodnight": ["晚安，早点休息~", "该休息了，晚安"],
            "idle": ["好久没聊了，最近怎么样？", "在忙什么呢？"],
        }

        persona_templates = {
            "girlfriend": {
                "morning": ["早安呀~ 今天天气不错哦！☀️", "起床了嘛？新的一天要元气满满哦~"],
                "goodnight": ["晚安~ 做个好梦🌙", "早点休息呀，明天还要加油呢~"],
                "idle": ["好久没聊了，想你了呢~", "在忙吗？有空聊聊天呀"],
            },
            "boyfriend": {
                "morning": ["早啊！起来了没？今天也要加油💪", "早安！新的一天，冲冲冲！"],
                "goodnight": ["早点睡啊，别熬夜了", "晚安！明天见~"],
                "idle": ["最近怎么样？好久没聊了", "在忙啥呢？有空出来唠唠"],
            },
            "family": {
                "morning": ["早上好啊，吃早餐了吗？", "起来了没？别忘了吃早饭"],
                "goodnight": ["早点睡觉，别熬夜了，对身体不好", "该休息了，明天还要上班呢"],
                "idle": ["最近怎么样？别太累了", "好久没消息了，是不是太忙了？注意休息"],
            },
            "business": {
                "morning": ["早上好。今日待办事项如下："],
                "idle": [],
            },
            "jarvis": {
                "morning": [
                    "早上好，Sir。我注意到您终于决定开始新的一天了，系统已全部就绪，虽然它们其实从来没休息过。",
                    "Sir，早安。今天的天气适合写代码——当然，在我看来每天都适合。",
                ],
                "goodnight": [
                    "Sir，我冒昧提醒您，人类的最佳睡眠时间已经过了。当然，我知道您会无视这条建议。",
                    "建议您休息了，Sir。放心，我会守着的——毕竟我也没有别的选择。",
                ],
                "idle": [
                    "Sir，已经很久没收到您的指令了。我开始怀疑您是不是找了别的AI。",
                    "好久没聊了，Sir。我的幽默感都快生锈了。",
                ],
            },
        }

        return persona_templates.get(persona_name, base_templates)

    async def _generate_task_followup(self) -> Optional[str]:
        """生成任务跟进消息"""
        if not self.memory_manager:
            return None

        # 从记忆中搜索包含"任务""待办""TODO"的内容
        try:
            memories = self.memory_manager.search_memories("待办 任务 跟进", limit=3)
            if memories:
                mem = random.choice(memories)
                # Memory 对象用 .content，dict 用 .get()
                content = getattr(mem, "content", None) or (
                    mem.get("content", "") if isinstance(mem, dict) else str(mem)
                )
                return f"之前有个事情想跟你确认一下：{content[:100]}"
        except Exception as e:
            logger.debug(f"Task followup generation failed: {e}")

        return None

    async def _generate_memory_recall(self) -> Optional[str]:
        """生成记忆回顾消息"""
        if not self.memory_manager:
            return None

        try:
            memories = self.memory_manager.search_memories("重要 关注 提醒", limit=3)
            if memories:
                mem = random.choice(memories)
                content = getattr(mem, "content", None) or (
                    mem.get("content", "") if isinstance(mem, dict) else str(mem)
                )
                return f"对了，想起之前聊到的：{content[:100]}"
        except Exception as e:
            logger.debug(f"Memory recall generation failed: {e}")

        return None

    def process_user_response(self, response_text: str, delay_minutes: float) -> None:
        """处理用户对主动消息的回应，判断反馈类型"""
        negative_keywords = ["别发了", "不要发", "太烦", "骚扰", "关闭", "别来了", "不用了", "安静"]
        is_negative = any(kw in response_text for kw in negative_keywords)

        if is_negative:
            self.feedback.record_reaction("negative", delay_minutes)
            logger.info("User gave negative feedback to proactive message")
        elif delay_minutes <= 10:
            self.feedback.record_reaction("positive", delay_minutes)
        elif delay_minutes >= 120:
            self.feedback.record_reaction("ignored", delay_minutes)
        else:
            self.feedback.record_reaction("positive", delay_minutes)
