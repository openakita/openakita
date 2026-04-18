"""
User profile management module

Responsible for:
- Tracking user information collection state
- First-use onboarding
- Progressive day-to-day information collection
- Updating the USER.md file
"""

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class UserProfileItem:
    """A single user profile item"""

    key: str  # Key name
    name: str  # Display name
    description: str  # Description
    question: str  # Question to ask the user
    priority: int = 1  # Priority (1-5, 1 is highest)
    category: str = "basic"  # Category
    value: str | None = None  # Current value
    collected_at: str | None = None  # Collection timestamp

    @property
    def is_collected(self) -> bool:
        """Whether the item has been collected"""
        return self.value is not None and self.value not in ["", "[to be learned]", None]


# Definition of user information items to collect
USER_PROFILE_ITEMS = [
    # === Basic info (priority 1, asked on first use) ===
    UserProfileItem(
        key="name",
        name="Name",
        description="How the user would like to be addressed",
        question="What should I call you? (You can share your name or nickname, or skip this)",
        priority=1,
        category="basic",
    ),
    UserProfileItem(
        key="agent_role",
        name="Agent role",
        description="The role the agent plays",
        question="What role would you like me to play? For example: work assistant, study buddy, personal butler, technical advisor, etc. (optional)",
        priority=1,
        category="basic",
    ),
    UserProfileItem(
        key="work_field",
        name="Work field",
        description="The user's field of work or study",
        question="What field do you primarily work or study in? (optional)",
        priority=2,
        category="basic",
    ),
    # === Technical preferences (priority 2) ===
    UserProfileItem(
        key="preferred_language",
        name="Programming language",
        description="The programming language the user primarily uses",
        question="What programming language do you mainly use?",
        priority=2,
        category="tech",
    ),
    UserProfileItem(
        key="os",
        name="Operating system",
        description="The operating system in use",
        question="Which operating system are you using? (Windows/Mac/Linux)",
        priority=3,
        category="tech",
    ),
    UserProfileItem(
        key="ide",
        name="Development tool",
        description="The IDE or editor the user commonly uses",
        question="Which IDE or editor do you normally write code in?",
        priority=3,
        category="tech",
    ),
    # === Communication preferences (priority 3) ===
    UserProfileItem(
        key="detail_level",
        name="Detail level",
        description="Preferred level of detail in replies",
        question="Do you prefer my replies to be more detailed or more concise?",
        priority=3,
        category="communication",
    ),
    UserProfileItem(
        key="code_comment_lang",
        name="Code comment language",
        description="Language used for code comments",
        question="Would you like the code comments I write to be in Chinese or English?",
        priority=4,
        category="communication",
    ),
    UserProfileItem(
        key="indent_style",
        name="Indentation style",
        description="Preferred code indentation (e.g. 2 spaces / 4 spaces / tab)",
        question="Do you prefer 2-space, 4-space, or tab indentation when writing code?",
        priority=5,
        category="communication",
    ),
    UserProfileItem(
        key="code_style",
        name="Code style",
        description="Code formatting/style preferences (e.g. PEP8, Google Style, Prettier)",
        question="Which code style convention do you usually follow?",
        priority=5,
        category="communication",
    ),
    # === Work habits (priority 4) ===
    UserProfileItem(
        key="work_hours",
        name="Working hours",
        description="Typical working hours",
        question="What hours do you typically work or study?",
        priority=4,
        category="habits",
    ),
    UserProfileItem(
        key="timezone",
        name="Timezone",
        description="The user's timezone",
        question="Which timezone are you in? (e.g. Beijing time, Tokyo time)",
        priority=4,
        category="habits",
    ),
    UserProfileItem(
        key="confirm_preference",
        name="Confirmation preference",
        description="Whether confirmation is needed before taking actions",
        question="Before important actions, would you prefer I confirm first or just proceed?",
        priority=4,
        category="habits",
    ),
    # === Personal info (priority 3-4, collected gradually over time) ===
    UserProfileItem(
        key="hobbies",
        name="Hobbies",
        description="The user's hobbies",
        question="Do you have any hobbies?",
        priority=3,
        category="personal",
    ),
    UserProfileItem(
        key="health_habits",
        name="Health habits",
        description="The user's routine and exercise habits",
        question="Do you have a regular routine? Do you exercise?",
        priority=4,
        category="personal",
    ),
    # === Persona preferences (priority 2-3, integrated with the persona system) ===
    UserProfileItem(
        key="communication_style",
        name="Communication style",
        description="Preferred communication style",
        question="Would you like me to speak formally or casually?",
        priority=2,
        category="persona",
    ),
    UserProfileItem(
        key="humor_preference",
        name="Humor preference",
        description="Whether the user enjoys humor",
        question="Would you like me to crack a joke now and then?",
        priority=2,
        category="persona",
    ),
    UserProfileItem(
        key="proactive_preference",
        name="Proactive message preference",
        description="Whether the user enjoys proactive messages",
        question="Would you like me to send you proactive messages? Things like greetings, reminders, and so on",
        priority=2,
        category="persona",
    ),
    UserProfileItem(
        key="emoji_preference",
        name="Emoji preference",
        description="Whether the user likes emojis and stickers",
        question="Do you like me to use emojis in my replies?",
        priority=3,
        category="persona",
    ),
    UserProfileItem(
        key="care_topics",
        name="Topics of interest",
        description="Topics the user wants extra attention on",
        question="Are there topics you want me to pay special attention to? Things like health reminders or project progress",
        priority=3,
        category="persona",
    ),
]


@dataclass
class UserProfileState:
    """User profile state"""

    is_first_use: bool = True
    onboarding_completed: bool = False
    last_question_date: str | None = None
    questions_asked_today: list = field(default_factory=list)
    collected_items: dict = field(default_factory=dict)  # key -> value
    skipped_items: list = field(default_factory=list)  # Items the user skipped

    def to_dict(self) -> dict:
        return {
            "is_first_use": self.is_first_use,
            "onboarding_completed": self.onboarding_completed,
            "last_question_date": self.last_question_date,
            "questions_asked_today": self.questions_asked_today,
            "collected_items": self.collected_items,
            "skipped_items": self.skipped_items,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfileState":
        return cls(
            is_first_use=data.get("is_first_use", True),
            onboarding_completed=data.get("onboarding_completed", False),
            last_question_date=data.get("last_question_date"),
            questions_asked_today=data.get("questions_asked_today", []),
            collected_items=data.get("collected_items", {}),
            skipped_items=data.get("skipped_items", []),
        )


class UserProfileManager:
    """
    User profile manager

    Responsible for:
    - Tracking user information collection state
    - Generating first-use onboarding prompts
    - Generating daily question prompts
    - Updating the USER.md file
    """

    MAX_QUESTIONS_PER_DAY = 2  # Maximum questions asked per day

    def __init__(self, data_dir: Path | None = None, user_md_path: Path | None = None):
        self.data_dir = data_dir or (settings.project_root / "data" / "user")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.user_md_path = user_md_path or settings.user_path
        self.state_file = self.data_dir / "profile_state.json"

        # Load state
        self.state = self._load_state()

        # Initialize profile items
        self.items = {item.key: item for item in USER_PROFILE_ITEMS}

        # Populate collected values into profile items
        for key, value in self.state.collected_items.items():
            if key in self.items:
                self.items[key].value = value

        logger.info(
            f"UserProfileManager initialized, collected: {len(self.state.collected_items)} items"
        )

    def _load_state(self) -> UserProfileState:
        """Load state"""
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    data = json.load(f)
                return UserProfileState.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load profile state: {e}")
        return UserProfileState()

    def _save_state(self) -> None:
        """Save state"""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save profile state: {e}")

    def is_first_use(self) -> bool:
        """Whether this is the first use"""
        return self.state.is_first_use

    def get_onboarding_prompt(self) -> str:
        """
        Get the first-use onboarding prompt

        Returns:
            Onboarding prompt text (to be appended to the system prompt)
        """
        if not self.state.is_first_use:
            return ""

        # Get uncollected items with priority 1
        priority_items = [
            item
            for item in self.items.values()
            if item.priority == 1
            and not item.is_collected
            and item.key not in self.state.skipped_items
        ]

        if not priority_items:
            # First-use onboarding is already complete
            self.state.is_first_use = False
            self.state.onboarding_completed = True
            self._save_state()
            return ""

        questions = [f"- {item.question}" for item in priority_items]

        return f"""
## First-use onboarding

This is the user's first time using the assistant. Please welcome them warmly and naturally gather the following information (do not be pushy; the user can skip any of them):

{chr(10).join(questions)}

**Important**:
- Keep the conversation natural; do not ask like a questionnaire one by one
- If the user doesn't want to answer, respect their choice and continue helping them with the current task
- After collecting information, use the update_user_profile tool to save it
"""

    def get_daily_question_prompt(self) -> str:
        """
        Get the daily question prompt

        Optionally asks 1-2 uncollected pieces of information per day.

        Returns:
            Question prompt text (to be appended to the system prompt)
        """
        # Check whether enough questions have already been asked today
        today = date.today().isoformat()

        if self.state.last_question_date != today:
            # New day, reset the counter
            self.state.last_question_date = today
            self.state.questions_asked_today = []
            self._save_state()

        if len(self.state.questions_asked_today) >= self.MAX_QUESTIONS_PER_DAY:
            return ""

        # Get uncollected and non-skipped items
        uncollected = [
            item
            for item in self.items.values()
            if not item.is_collected
            and item.key not in self.state.skipped_items
            and item.key not in self.state.questions_asked_today
        ]

        if not uncollected:
            return ""

        # Sort by priority and pick one
        uncollected.sort(key=lambda x: x.priority)

        # Randomly pick one from the highest-priority group
        top_priority = uncollected[0].priority
        same_priority = [item for item in uncollected if item.priority == top_priority]
        selected = random.choice(same_priority)

        return f"""
## Daily information gathering (optional)

If the conversation flows naturally, you may ask:
- {selected.question} (key: {selected.key})

**Important**:
- Only ask when the conversation transitions naturally; do not interrupt the user abruptly
- If the user doesn't want to answer, that is perfectly fine; continue the current topic
- After collecting information, use the update_user_profile tool to save it
"""

    def update_profile(self, key: str, value: str) -> bool:
        """
        Update the user profile

        Args:
            key: Profile item key name
            value: Value

        Returns:
            Whether the update succeeded
        """
        if key not in self.items:
            logger.warning(f"Unknown profile key: {key}")
            return False

        # Update state
        self.state.collected_items[key] = value
        self.items[key].value = value
        self.items[key].collected_at = datetime.now().isoformat()

        # Record that we asked today
        today = date.today().isoformat()
        if self.state.last_question_date == today and key not in self.state.questions_asked_today:
            self.state.questions_asked_today.append(key)

        # Check whether first-use onboarding is complete
        priority_1_items = [item for item in self.items.values() if item.priority == 1]
        all_collected = all(
            item.is_collected or item.key in self.state.skipped_items for item in priority_1_items
        )
        if all_collected and self.state.is_first_use:
            self.state.is_first_use = False
            self.state.onboarding_completed = True

        self._save_state()

        # Update USER.md
        self._update_user_md()

        logger.info(f"Updated user profile: {key} = {value}")
        return True

    def skip_question(self, key: str) -> None:
        """
        Skip a given question

        Args:
            key: Profile item key name
        """
        if key not in self.state.skipped_items:
            self.state.skipped_items.append(key)

        # Record that we asked today
        today = date.today().isoformat()
        if self.state.last_question_date == today and key not in self.state.questions_asked_today:
            self.state.questions_asked_today.append(key)

        self._save_state()
        logger.info(f"User skipped question: {key}")

    def mark_onboarding_complete(self) -> None:
        """Mark first-use onboarding as complete"""
        self.state.is_first_use = False
        self.state.onboarding_completed = True
        self._save_state()
        logger.info("Onboarding marked as complete")

    def _update_user_md(self) -> None:
        """Update the USER.md file"""
        try:
            # Generate new USER.md content
            content = self._generate_user_md()

            with open(self.user_md_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info("Updated USER.md")

        except Exception as e:
            logger.error(f"Failed to update USER.md: {e}")

    def _generate_user_md(self) -> str:
        """Generate USER.md content"""

        def get_value(key: str) -> str:
            item = self.items.get(key)
            if item and item.is_collected:
                return item.value
            return "[to be learned]"

        return f"""# User Profile
<!--
References:
- GitHub Copilot Memory: https://docs.github.com/en/copilot/concepts/agents/copilot-memory
- ai-agent-memory-system: https://github.com/trose/ai-agent-memory-system

This file is automatically learned and updated by OpenAkita to record the user's preferences and habits.
-->

## Basic Information

- **Name**: {get_value("name")}
- **Work field**: {get_value("work_field")}
- **Agent role**: {get_value("agent_role")}
- **Primary language**: English
- **Timezone**: {get_value("timezone")}

## Technical Stack

### Preferred Languages

{get_value("preferred_language")}

### Frameworks & Tools

[to be learned]

### Development Environment

- **OS**: {get_value("os")}
- **IDE**: {get_value("ide")}
- **Shell**: [to be learned]

## Preferences

### Communication Style

- **Detail level**: {get_value("detail_level")}
- **Code comments**: {get_value("code_comment_lang")}
- **Explanation style**: [to be learned]

### Code Style

- **Naming conventions**: [to be learned]
- **Formatter**: [to be learned]
- **Test framework**: [to be learned]

### Work Habits

- **Working hours**: {get_value("work_hours")}
- **Response speed preference**: [to be learned]
- **Confirmation requirements**: {get_value("confirm_preference")}

## Interaction Patterns

### Common Task Types

| Task type | Count | Last executed |
|-----------|-------|---------------|
| [to be tracked] | - | - |

### Frequently Used Commands

[to be learned]

### Common Questions

[to be learned]

## Project Context

### Active Projects

[to be learned]

### Code Conventions

[to be learned — the agent will learn from the user's code]

## Learning History

### Successful Interactions

[The agent will record successful interaction patterns]

### Corrections Received

[The agent will record the user's corrections to avoid repeating mistakes]

## Notes

[Any other user-related information worth remembering]

---

*This file is maintained automatically by OpenAkita. Users may also edit it manually to provide more accurate information.*
*Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}*
"""

    def get_profile_summary(self) -> str:
        """Get a summary of the profile"""
        collected = len([item for item in self.items.values() if item.is_collected])
        total = len(self.items)

        summary = f"Collected {collected}/{total} user information items\n\n"

        for category in ["basic", "tech", "communication", "habits"]:
            category_items = [item for item in self.items.values() if item.category == category]
            summary += f"**{category.title()}**:\n"
            for item in category_items:
                status = "✅" if item.is_collected else "⬜"
                value = item.value if item.is_collected else "-"
                summary += f"  {status} {item.name}: {value}\n"
            summary += "\n"

        return summary

    def get_available_keys(self) -> list[str]:
        """Get all available key names"""
        return list(self.items.keys())


# Global instance
_profile_manager: UserProfileManager | None = None


def get_profile_manager() -> UserProfileManager:
    """Get the global UserProfileManager instance"""
    global _profile_manager
    if _profile_manager is None:
        _profile_manager = UserProfileManager()
    return _profile_manager
