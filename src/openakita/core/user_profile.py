"""
用户档案管理模块

负责:
- 跟踪用户信息收集状态
- 首次使用引导
- 日常渐进式信息收集
- 更新 USER.md 文件
"""

import json
import logging
import random
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class UserProfileItem:
    """用户档案项"""
    key: str                    # 键名
    name: str                   # 显示名称
    description: str            # 描述
    question: str               # 询问用户时的问题
    priority: int = 1           # 优先级 (1-5，1最高)
    category: str = "basic"     # 分类
    value: Optional[str] = None # 当前值
    collected_at: Optional[str] = None  # 收集时间
    
    @property
    def is_collected(self) -> bool:
        """是否已收集"""
        return self.value is not None and self.value not in ["", "[待学习]", None]


# 定义要收集的用户信息项
USER_PROFILE_ITEMS = [
    # === 基础信息 (优先级 1，首次使用时询问) ===
    UserProfileItem(
        key="name",
        name="称呼",
        description="用户希望被如何称呼",
        question="我该怎么称呼你呢？（你可以告诉我你的名字、昵称，或者直接跳过）",
        priority=1,
        category="basic",
    ),
    UserProfileItem(
        key="agent_role",
        name="Agent角色",
        description="Agent 扮演的角色",
        question="你希望我扮演什么角色呢？比如：工作助手、学习伙伴、私人管家、技术顾问等（可以跳过）",
        priority=1,
        category="basic",
    ),
    UserProfileItem(
        key="work_field",
        name="工作领域",
        description="用户的工作或学习领域",
        question="你平时主要从事什么领域的工作或学习呢？（可以跳过）",
        priority=2,
        category="basic",
    ),
    
    # === 技术偏好 (优先级 2) ===
    UserProfileItem(
        key="preferred_language",
        name="编程语言",
        description="常用的编程语言",
        question="你平时主要使用什么编程语言呢？",
        priority=2,
        category="tech",
    ),
    UserProfileItem(
        key="os",
        name="操作系统",
        description="使用的操作系统",
        question="你使用的是什么操作系统？（Windows/Mac/Linux）",
        priority=3,
        category="tech",
    ),
    UserProfileItem(
        key="ide",
        name="开发工具",
        description="常用的 IDE 或编辑器",
        question="你平时用什么 IDE 或编辑器写代码？",
        priority=3,
        category="tech",
    ),
    
    # === 交流偏好 (优先级 3) ===
    UserProfileItem(
        key="detail_level",
        name="详细程度",
        description="回复的详细程度偏好",
        question="你喜欢我的回复详细一些，还是简洁一些？",
        priority=3,
        category="communication",
    ),
    UserProfileItem(
        key="code_comment_lang",
        name="代码注释语言",
        description="代码注释使用的语言",
        question="你希望我写的代码注释用中文还是英文？",
        priority=4,
        category="communication",
    ),
    
    # === 工作习惯 (优先级 4) ===
    UserProfileItem(
        key="work_hours",
        name="工作时间",
        description="通常的工作时间段",
        question="你通常在什么时间段工作或学习？",
        priority=4,
        category="habits",
    ),
    UserProfileItem(
        key="timezone",
        name="时区",
        description="用户所在时区",
        question="你在哪个时区？（比如：北京时间、东京时间等）",
        priority=4,
        category="habits",
    ),
    UserProfileItem(
        key="confirm_preference",
        name="确认偏好",
        description="执行操作前是否需要确认",
        question="执行重要操作前，你希望我先确认还是直接执行？",
        priority=4,
        category="habits",
    ),
]


@dataclass
class UserProfileState:
    """用户档案状态"""
    is_first_use: bool = True
    onboarding_completed: bool = False
    last_question_date: Optional[str] = None
    questions_asked_today: list = field(default_factory=list)
    collected_items: dict = field(default_factory=dict)  # key -> value
    skipped_items: list = field(default_factory=list)    # 用户跳过的项
    
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
    用户档案管理器
    
    负责:
    - 跟踪用户信息收集状态
    - 生成首次使用引导提示
    - 生成日常询问提示
    - 更新 USER.md 文件
    """
    
    MAX_QUESTIONS_PER_DAY = 2  # 每天最多询问的问题数
    
    def __init__(self, data_dir: Optional[Path] = None, user_md_path: Optional[Path] = None):
        self.data_dir = data_dir or (settings.project_root / "data" / "user")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.user_md_path = user_md_path or settings.user_path
        self.state_file = self.data_dir / "profile_state.json"
        
        # 加载状态
        self.state = self._load_state()
        
        # 初始化档案项
        self.items = {item.key: item for item in USER_PROFILE_ITEMS}
        
        # 将已收集的值填充到档案项
        for key, value in self.state.collected_items.items():
            if key in self.items:
                self.items[key].value = value
        
        logger.info(f"UserProfileManager initialized, collected: {len(self.state.collected_items)} items")
    
    def _load_state(self) -> UserProfileState:
        """加载状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return UserProfileState.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load profile state: {e}")
        return UserProfileState()
    
    def _save_state(self) -> None:
        """保存状态"""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save profile state: {e}")
    
    def is_first_use(self) -> bool:
        """是否是首次使用"""
        return self.state.is_first_use
    
    def get_onboarding_prompt(self) -> str:
        """
        获取首次使用引导提示
        
        Returns:
            引导提示文本（添加到系统提示中）
        """
        if not self.state.is_first_use:
            return ""
        
        # 获取优先级为 1 的未收集项
        priority_items = [
            item for item in self.items.values()
            if item.priority == 1 and not item.is_collected and item.key not in self.state.skipped_items
        ]
        
        if not priority_items:
            # 首次引导已完成
            self.state.is_first_use = False
            self.state.onboarding_completed = True
            self._save_state()
            return ""
        
        questions = [f"- {item.question}" for item in priority_items]
        
        return f"""
## 首次使用引导

这是用户第一次使用，请友好地欢迎用户，并自然地了解以下信息（不要强硬要求，用户可以跳过）:

{chr(10).join(questions)}

**重要**: 
- 保持对话自然，不要像问卷一样逐个询问
- 如果用户不想回答，尊重用户的选择，继续帮助用户完成当前任务
- 收集到信息后，使用 update_user_profile 工具保存
"""
    
    def get_daily_question_prompt(self) -> str:
        """
        获取日常询问提示
        
        每天选择性地询问 1-2 个未收集的信息
        
        Returns:
            询问提示文本（添加到系统提示中）
        """
        # 检查今天是否已经问过足够的问题
        today = date.today().isoformat()
        
        if self.state.last_question_date != today:
            # 新的一天，重置计数
            self.state.last_question_date = today
            self.state.questions_asked_today = []
            self._save_state()
        
        if len(self.state.questions_asked_today) >= self.MAX_QUESTIONS_PER_DAY:
            return ""
        
        # 获取未收集且未跳过的项
        uncollected = [
            item for item in self.items.values()
            if not item.is_collected 
            and item.key not in self.state.skipped_items
            and item.key not in self.state.questions_asked_today
        ]
        
        if not uncollected:
            return ""
        
        # 按优先级排序，选择一个
        uncollected.sort(key=lambda x: x.priority)
        
        # 随机选择一个（在同优先级中）
        top_priority = uncollected[0].priority
        same_priority = [item for item in uncollected if item.priority == top_priority]
        selected = random.choice(same_priority)
        
        return f"""
## 日常信息收集（可选）

如果对话氛围合适，可以自然地了解:
- {selected.question} (key: {selected.key})

**重要**: 
- 只在对话自然过渡时才询问，不要刻意打断用户
- 如果用户不想回答，完全没问题，继续当前话题
- 收集到信息后，使用 update_user_profile 工具保存
"""
    
    def update_profile(self, key: str, value: str) -> bool:
        """
        更新用户档案
        
        Args:
            key: 档案项键名
            value: 值
        
        Returns:
            是否成功
        """
        if key not in self.items:
            logger.warning(f"Unknown profile key: {key}")
            return False
        
        # 更新状态
        self.state.collected_items[key] = value
        self.items[key].value = value
        self.items[key].collected_at = datetime.now().isoformat()
        
        # 记录今天询问过
        today = date.today().isoformat()
        if self.state.last_question_date == today and key not in self.state.questions_asked_today:
            self.state.questions_asked_today.append(key)
        
        # 检查是否完成首次引导
        priority_1_items = [item for item in self.items.values() if item.priority == 1]
        all_collected = all(item.is_collected or item.key in self.state.skipped_items for item in priority_1_items)
        if all_collected and self.state.is_first_use:
            self.state.is_first_use = False
            self.state.onboarding_completed = True
        
        self._save_state()
        
        # 更新 USER.md
        self._update_user_md()
        
        logger.info(f"Updated user profile: {key} = {value}")
        return True
    
    def skip_question(self, key: str) -> None:
        """
        跳过某个问题
        
        Args:
            key: 档案项键名
        """
        if key not in self.state.skipped_items:
            self.state.skipped_items.append(key)
        
        # 记录今天询问过
        today = date.today().isoformat()
        if self.state.last_question_date == today and key not in self.state.questions_asked_today:
            self.state.questions_asked_today.append(key)
        
        self._save_state()
        logger.info(f"User skipped question: {key}")
    
    def mark_onboarding_complete(self) -> None:
        """标记首次引导完成"""
        self.state.is_first_use = False
        self.state.onboarding_completed = True
        self._save_state()
        logger.info("Onboarding marked as complete")
    
    def _update_user_md(self) -> None:
        """更新 USER.md 文件"""
        try:
            # 生成新的 USER.md 内容
            content = self._generate_user_md()
            
            with open(self.user_md_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            logger.info("Updated USER.md")
            
        except Exception as e:
            logger.error(f"Failed to update USER.md: {e}")
    
    def _generate_user_md(self) -> str:
        """生成 USER.md 内容"""
        def get_value(key: str) -> str:
            item = self.items.get(key)
            if item and item.is_collected:
                return item.value
            return "[待学习]"
        
        return f"""# User Profile
<!--
参考来源:
- GitHub Copilot Memory: https://docs.github.com/en/copilot/concepts/agents/copilot-memory
- ai-agent-memory-system: https://github.com/trose/ai-agent-memory-system

此文件由 OpenAkita 自动学习和更新，记录用户的偏好和习惯。
-->

## Basic Information

- **称呼**: {get_value("name")}
- **工作领域**: {get_value("work_field")}
- **Agent角色**: {get_value("agent_role")}
- **主要语言**: 中文
- **时区**: {get_value("timezone")}

## Technical Stack

### Preferred Languages

{get_value("preferred_language")}

### Frameworks & Tools

[待学习]

### Development Environment

- **OS**: {get_value("os")}
- **IDE**: {get_value("ide")}
- **Shell**: [待学习]

## Preferences

### Communication Style

- **详细程度**: {get_value("detail_level")}
- **代码注释**: {get_value("code_comment_lang")}
- **解释方式**: [待学习]

### Code Style

- **命名约定**: [待学习]
- **格式化工具**: [待学习]
- **测试框架**: [待学习]

### Work Habits

- **工作时间**: {get_value("work_hours")}
- **响应速度偏好**: [待学习]
- **确认需求**: {get_value("confirm_preference")}

## Interaction Patterns

### Common Task Types

| 任务类型 | 次数 | 最后执行 |
|----------|------|----------|
| [待统计] | - | - |

### Frequently Used Commands

[待学习]

### Common Questions

[待学习]

## Project Context

### Active Projects

[待学习]

### Code Conventions

[待学习 - Agent 会从用户的代码中学习]

## Learning History

### Successful Interactions

[Agent 会记录成功的交互模式]

### Corrections Received

[Agent 会记录用户的纠正，避免重复错误]

## Notes

[其他需要记住的用户相关信息]

---

*此文件由 OpenAkita 自动维护。用户也可以手动编辑以提供更准确的信息。*
*最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""
    
    def get_profile_summary(self) -> str:
        """获取档案摘要"""
        collected = len([item for item in self.items.values() if item.is_collected])
        total = len(self.items)
        
        summary = f"已收集 {collected}/{total} 项用户信息\n\n"
        
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
        """获取所有可用的键名"""
        return list(self.items.keys())


# 全局实例
_profile_manager: Optional[UserProfileManager] = None


def get_profile_manager() -> UserProfileManager:
    """获取全局 UserProfileManager 实例"""
    global _profile_manager
    if _profile_manager is None:
        _profile_manager = UserProfileManager()
    return _profile_manager
