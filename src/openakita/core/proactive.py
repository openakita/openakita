"""
Proactive Engine

Manages generation of proactive messages, frequency control, and feedback tracking.
Triggered periodically via scheduler heartbeat and adapts based on persona settings
and user feedback.

Core principles:
- Non-intrusive: strict frequency control + feedback-driven
- Valuable: grounded in memory and context
- Persona-consistent: style matches the current persona
- Easy to disable: can be turned off in one sentence
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


# ── Configuration ─────────────────────────────────────────────────


@dataclass
class ProactiveConfig:
    """Proactive engine configuration"""

    enabled: bool = False
    max_daily_messages: int = 3
    min_interval_minutes: int = 120
    quiet_hours_start: int = 23  # Quiet period start
    quiet_hours_end: int = 7  # Quiet period end
    idle_threshold_hours: int = 3  # Idle time before sending small talk (AI adjusts based on feedback)


# ── Feedback tracking ─────────────────────────────────────────────


@dataclass
class ProactiveRecord:
    """Record of a proactive message send"""

    msg_type: str  # greeting/task_followup/memory_recall/idle_chat/goodnight
    timestamp: datetime = field(default_factory=datetime.now)
    reaction: str | None = None  # positive/negative/ignored
    response_delay_minutes: float | None = None


class ProactiveFeedbackTracker:
    """Tracks user reactions to proactive messages and drives frequency adaptation"""

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
                for r in self.records[-200:]  # Keep only the most recent 200
            ]
        }
        self.data_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_send(self, msg_type: str, timestamp: datetime | None = None) -> None:
        """Record a proactive message send"""
        self.records.append(
            ProactiveRecord(msg_type=msg_type, timestamp=timestamp or datetime.now())
        )
        self._save()

    def record_reaction(self, reaction_type: str, response_delay_minutes: float = 0) -> None:
        """
        Record the user's reaction to the most recent proactive message.

        reaction_type: positive/negative/ignored
        - positive: user responded positively within 10 minutes
        - negative: user indicated things like "stop sending" / "too annoying"
        - ignored: no response within 2 hours
        """
        # Find the most recent record without a reaction
        for rec in reversed(self.records):
            if rec.reaction is None:
                rec.reaction = reaction_type
                rec.response_delay_minutes = response_delay_minutes
                break
        self._save()

    def get_today_send_count(self) -> int:
        """Number of proactive messages sent today"""
        today = datetime.now().date()
        return sum(1 for r in self.records if r.timestamp.date() == today)

    def get_last_send_time(self) -> datetime | None:
        """Timestamp of the last send"""
        if self.records:
            return self.records[-1].timestamp
        return None

    def get_adjusted_config(self, base_config: ProactiveConfig) -> ProactiveConfig:
        """Dynamically adjust frequency and idle threshold based on historical feedback"""
        cutoff = datetime.now() - timedelta(days=30)
        recent = [r for r in self.records if r.timestamp > cutoff and r.reaction]

        if len(recent) < 5:
            return base_config

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

        if negative > 0:
            adjusted.max_daily_messages = max(1, base_config.max_daily_messages - 2)
            adjusted.min_interval_minutes = base_config.min_interval_minutes + 120
            logger.info("Proactive frequency reduced due to negative feedback")
        elif ignored / total > 0.5:
            adjusted.max_daily_messages = max(1, base_config.max_daily_messages - 1)
            adjusted.min_interval_minutes = base_config.min_interval_minutes + 60
            logger.info("Proactive frequency reduced due to high ignore rate")
        elif positive / total > 0.8:
            adjusted.max_daily_messages = min(5, base_config.max_daily_messages + 1)
            adjusted.min_interval_minutes = max(60, base_config.min_interval_minutes - 30)
            logger.info("Proactive frequency increased due to positive feedback")

        # Adjust the idle threshold based on dedicated idle_chat feedback
        adjusted.idle_threshold_hours = self._compute_idle_threshold(
            base_config.idle_threshold_hours, cutoff
        )

        return adjusted

    def _compute_idle_threshold(self, base_hours: int, cutoff: datetime) -> int:
        """
        Dynamically adjust the idle threshold based on historical idle_chat feedback.

        Strategy:
        - many positive → shorten the threshold (user likes it, be more proactive, min 1h)
        - many ignored  → lengthen the threshold (user not interested, back off)
        - negative      → lengthen significantly (user dislikes it, cap at 24h)
        """
        idle_records = [
            r
            for r in self.records
            if r.timestamp > cutoff and r.reaction and r.msg_type == "idle_chat"
        ]

        if len(idle_records) < 2:
            return base_hours

        total = len(idle_records)
        pos = sum(1 for r in idle_records if r.reaction == "positive")
        neg = sum(1 for r in idle_records if r.reaction == "negative")
        ign = sum(1 for r in idle_records if r.reaction == "ignored")

        threshold = base_hours

        if neg > 0:
            threshold = min(24, base_hours * 3)
            logger.info(
                "Idle threshold increased to %dh (negative feedback on idle_chat)", threshold
            )
        elif ign / total > 0.5:
            threshold = min(24, base_hours * 2)
            logger.info("Idle threshold increased to %dh (idle_chat often ignored)", threshold)
        elif pos / total > 0.8:
            threshold = max(1, base_hours - 1)
            logger.info("Idle threshold decreased to %dh (idle_chat well received)", threshold)

        return threshold


# ── Proactive engine ──────────────────────────────────────────────


class ProactiveEngine:
    """Proactive engine that manages triggering and generation of proactive messages"""

    # Message types
    MSG_TYPES = [
        "morning_greeting",  # Morning greeting
        "task_followup",  # Task follow-up
        "memory_recall",  # Key memory recall
        "idle_chat",  # Idle small talk
        "goodnight",  # Goodnight reminder
        "special_day",  # Weather / holidays
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
        self._last_user_interaction: datetime | None = None

    def update_user_interaction(self, timestamp: datetime | None = None) -> None:
        """Record the user's most recent interaction time"""
        self._last_user_interaction = timestamp or datetime.now()

    def toggle(self, enabled: bool) -> None:
        """Toggle proactive mode on/off"""
        self.config.enabled = enabled
        logger.info(f"Proactive mode {'enabled' if enabled else 'disabled'}")

    async def heartbeat(self) -> dict[str, Any] | None:
        """
        Heartbeat check — called by the scheduler every 30 minutes.

        Returns:
            If a message should be sent, returns {"type": str, "content": str, "sticker_mood": str|None};
            otherwise returns None.
        """
        if not self.config.enabled:
            return None

        # Get adaptive config
        effective_config = self.feedback.get_adjusted_config(self.config)

        # Check quiet hours
        now = datetime.now()
        hour = now.hour
        if effective_config.quiet_hours_start > effective_config.quiet_hours_end:
            # Crosses midnight (e.g., 23:00-07:00)
            if (
                hour >= effective_config.quiet_hours_start
                or hour < effective_config.quiet_hours_end
            ):
                return None
        else:
            # Same day (e.g., 01:00-05:00)
            if effective_config.quiet_hours_start <= hour < effective_config.quiet_hours_end:
                return None

        # Check daily send quota
        today_count = self.feedback.get_today_send_count()
        if today_count >= effective_config.max_daily_messages:
            return None

        # Check minimum interval
        last_send = self.feedback.get_last_send_time()
        if last_send:
            elapsed = (now - last_send).total_seconds() / 60
            if elapsed < effective_config.min_interval_minutes:
                return None

        # Decide message type
        msg_type = self._decide_message_type(now, effective_config)
        if not msg_type:
            return None

        # Generate message content
        result = await self._generate_message(msg_type)
        if result:
            self.feedback.record_send(msg_type)
        return result

    def _decide_message_type(self, now: datetime, config: ProactiveConfig) -> str | None:
        """Decide which message type to send based on current state"""
        hour = now.hour

        # Morning greeting (7-9 AM, not yet sent today)
        if 7 <= hour <= 9:
            today_types = [
                r.msg_type for r in self.feedback.records if r.timestamp.date() == now.date()
            ]
            if "morning_greeting" not in today_types:
                return "morning_greeting"

        # Goodnight (21-22)
        if 21 <= hour <= 22:
            today_types = [
                r.msg_type for r in self.feedback.records if r.timestamp.date() == now.date()
            ]
            if "goodnight" not in today_types:
                # Only send goodnight for close personas
                if self.persona_manager:
                    merged = self.persona_manager.get_merged_persona()
                    if merged.emotional_distance in ("close", "intimate"):
                        return "goodnight"

        # Long idle -> small talk
        if self._last_user_interaction:
            idle_hours = (now - self._last_user_interaction).total_seconds() / 3600
            if idle_hours >= config.idle_threshold_hours:
                return "idle_chat"

        # Task follow-up (if there are open tasks)
        if self.memory_manager and random.random() < 0.3:
            return "task_followup"

        # Key memory recall
        if self.memory_manager and random.random() < 0.2:
            return "memory_recall"

        return None

    async def _generate_message(self, msg_type: str) -> dict[str, Any] | None:
        """Generate content based on the message type (templates here; could be LLM-generated)"""
        persona_name = "default"
        sticker_mood = None

        if self.persona_manager:
            merged = self.persona_manager.get_merged_persona()
            persona_name = merged.preset_name

        templates = self._get_templates(persona_name)

        if msg_type == "morning_greeting":
            options = templates.get("morning") or ["Good morning! A new day begins~"]
            content = random.choice(options)
            sticker_mood = "greeting"

        elif msg_type == "goodnight":
            options = templates.get("goodnight") or ["Goodnight, get some rest~"]
            content = random.choice(options)
            sticker_mood = "greeting"

        elif msg_type == "idle_chat":
            raw = templates.get("idle")
            # Empty list means this persona doesn't send idle chat (e.g., business); no fallback
            if raw is not None and len(raw) == 0:
                return None
            options = raw or ["Haven't chatted in a while — how have you been?"]
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
        """Get message templates for the given persona"""
        base_templates = {
            "morning": ["Good morning! A new day begins~", "Morning! Let's go for it today"],
            "goodnight": ["Goodnight, get some rest~", "Time to rest, goodnight"],
            "idle": ["Haven't chatted in a while — how have you been?", "What are you up to?"],
        }

        persona_templates = {
            "girlfriend": {
                "morning": ["Good morning~ The weather's nice today! ☀️", "Awake yet? Hope you have lots of energy for the new day~"],
                "goodnight": ["Goodnight~ Sweet dreams 🌙", "Get some rest, there's another day ahead~"],
                "idle": ["Haven't talked in a while, I miss you~", "Busy? Let's chat when you have a minute"],
            },
            "boyfriend": {
                "morning": ["Morning! You up? Let's make today count 💪", "Morning! New day, let's crush it!"],
                "goodnight": ["Get to bed early, don't stay up", "Goodnight! See you tomorrow~"],
                "idle": ["How's it going? Haven't talked in a while", "What are you up to? Let's catch up sometime"],
            },
            "family": {
                "morning": ["Good morning, have you had breakfast?", "Up yet? Don't forget to eat breakfast"],
                "goodnight": ["Get to bed early, don't stay up — it's bad for your health", "Time to rest, you have work tomorrow"],
                "idle": ["How are you? Don't overwork yourself", "Haven't heard from you in a while, too busy? Remember to rest"],
            },
            "business": {
                "morning": ["Good morning. Today's to-do list:"],
                "idle": [],
            },
            "jarvis": {
                "morning": [
                    "Good morning, Sir. I notice you've finally decided to start the day. All systems are ready — though they never really rested.",
                    "Morning, Sir. The weather is perfect for writing code — though, in my view, every day is.",
                ],
                "goodnight": [
                    "Sir, I take the liberty of reminding you that the optimal human sleep window has passed. I assume, as usual, you will ignore this advice.",
                    "I suggest you rest, Sir. Don't worry, I'll keep watch — not that I have much choice.",
                ],
                "idle": [
                    "Sir, it's been a while since your last instruction. I'm beginning to suspect you've found another AI.",
                    "Long time no chat, Sir. My sense of humor is getting rusty.",
                ],
            },
        }

        return persona_templates.get(persona_name, base_templates)

    async def _generate_task_followup(self) -> str | None:
        """Generate a task follow-up message"""
        if not self.memory_manager:
            return None

        # Search memories for content related to tasks / to-dos / TODO
        # Note: search query kept in Chinese to match Chinese memory content
        try:
            memories = self.memory_manager.search_memories("待办 任务 跟进", limit=3)
            if memories:
                mem = random.choice(memories)
                # Memory object uses .content; dict uses .get()
                content = getattr(mem, "content", None) or (
                    mem.get("content", "") if isinstance(mem, dict) else str(mem)
                )
                return f"There's something I wanted to check with you: {content[:100]}"
        except Exception as e:
            logger.debug(f"Task followup generation failed: {e}")

        return None

    async def _generate_memory_recall(self) -> str | None:
        """Generate a memory recall message"""
        if not self.memory_manager:
            return None

        try:
            # Note: search query kept in Chinese to match Chinese memory content
            memories = self.memory_manager.search_memories("重要 关注 提醒", limit=3)
            if memories:
                mem = random.choice(memories)
                content = getattr(mem, "content", None) or (
                    mem.get("content", "") if isinstance(mem, dict) else str(mem)
                )
                return f"By the way, something we talked about came to mind: {content[:100]}"
        except Exception as e:
            logger.debug(f"Memory recall generation failed: {e}")

        return None

    def process_user_response(self, response_text: str, delay_minutes: float) -> None:
        """Process the user's response to a proactive message and classify the feedback type"""
        # Negative-intent keywords matched against Chinese user input — kept as-is for matching
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
