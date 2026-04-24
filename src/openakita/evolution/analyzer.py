"""
需求分析器

分析任务需求，识别缺失的能力。
"""

import logging
from dataclasses import dataclass

from ..core.brain import Brain
from ..skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


@dataclass
class CapabilityGap:
    """能力缺口"""

    name: str
    description: str
    category: str  # skill, tool, knowledge
    priority: int  # 1-10
    suggested_solutions: list[str]


@dataclass
class TaskAnalysis:
    """任务分析结果"""

    task: str
    required_capabilities: list[str]
    available_capabilities: list[str]
    missing_capabilities: list[CapabilityGap]
    can_execute: bool
    complexity: int  # 1-10
    estimated_steps: int


class NeedAnalyzer:
    """
    需求分析器

    分析任务需要的能力，识别缺失的部分，
    并建议如何获取这些能力。
    """

    def __init__(
        self,
        brain: Brain,
        skill_registry: SkillRegistry | None = None,
    ):
        self.brain = brain
        self.skill_registry = skill_registry if skill_registry is not None else SkillRegistry()

    async def analyze_task(self, task: str) -> TaskAnalysis:
        """
        分析任务需求

        Args:
            task: 任务描述

        Returns:
            TaskAnalysis
        """
        logger.info(f"Analyzing task: {task}")

        # 使用 LLM 分析任务
        analysis_prompt = f"""分析以下任务，识别完成它所需的能力：

任务: {task}

请以 JSON 格式返回分析结果:
{{
    "required_capabilities": ["能力1", "能力2", ...],
    "complexity": 1-10 的数字,
    "estimated_steps": 预估步骤数,
    "suggested_approach": "建议的方法"
}}

只返回 JSON，不要解释。"""

        response = await self.brain.think(analysis_prompt)

        # 解析响应
        import json

        try:
            content = response.content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()

            data = json.loads(content)
        except json.JSONDecodeError:
            data = {
                "required_capabilities": [],
                "complexity": 5,
                "estimated_steps": 3,
            }

        required = data.get("required_capabilities", [])
        complexity = data.get("complexity", 5)
        estimated_steps = data.get("estimated_steps", 3)

        # 检查哪些能力已有
        available = []
        missing = []

        for cap in required:
            if self._has_capability(cap):
                available.append(cap)
            else:
                gap = await self._analyze_gap(cap)
                missing.append(gap)

        return TaskAnalysis(
            task=task,
            required_capabilities=required,
            available_capabilities=available,
            missing_capabilities=missing,
            can_execute=len(missing) == 0,
            complexity=complexity,
            estimated_steps=estimated_steps,
        )

    def _has_capability(self, capability: str) -> bool:
        """检查是否有某能力"""
        # 检查技能注册表
        cap_lower = capability.lower()

        for skill in self.skill_registry:
            if cap_lower in skill.name.lower() or cap_lower in skill.description.lower():
                return True

        # 检查内置工具
        builtin_tools = [
            "shell",
            "file",
            "web",
            "http",
            "browser",
            "python",
            "code",
            "execute",
            "search",
        ]

        return any(tool in cap_lower for tool in builtin_tools)

    async def _analyze_gap(self, capability: str) -> CapabilityGap:
        """分析能力缺口"""
        # 使用 LLM 分析如何获取这个能力
        prompt = f"""我需要"{capability}"这个能力，但目前没有。

请分析:
1. 这是什么类型的能力？(skill/tool/knowledge)
2. 优先级有多高？(1-10)
3. 有哪些方式可以获取这个能力？

以 JSON 格式返回:
{{
    "category": "skill/tool/knowledge",
    "priority": 1-10,
    "solutions": ["方案1", "方案2", ...]
}}"""

        response = await self.brain.think(prompt)

        import json

        try:
            content = response.content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            data = json.loads(content)
        except Exception:
            data = {
                "category": "skill",
                "priority": 5,
                "solutions": [f"搜索 GitHub 找 {capability} 相关项目", "自己编写实现"],
            }

        return CapabilityGap(
            name=capability,
            description=f"缺少 {capability} 能力",
            category=data.get("category", "skill"),
            priority=data.get("priority", 5),
            suggested_solutions=data.get("solutions", []),
        )

    async def suggest_evolution(self, gaps: list[CapabilityGap]) -> list[dict]:
        """
        根据能力缺口建议进化方案

        Args:
            gaps: 能力缺口列表

        Returns:
            进化建议列表
        """
        suggestions = []

        for gap in sorted(gaps, key=lambda g: -g.priority):
            suggestion = {
                "gap": gap.name,
                "priority": gap.priority,
                "actions": [],
            }

            for solution in gap.suggested_solutions:
                if "github" in solution.lower() or "搜索" in solution:
                    suggestion["actions"].append(
                        {
                            "type": "search_install",
                            "description": f"搜索并安装 {gap.name} 相关技能",
                        }
                    )
                elif "编写" in solution or "实现" in solution:
                    suggestion["actions"].append(
                        {
                            "type": "generate",
                            "description": f"自动生成 {gap.name} 技能",
                        }
                    )
                else:
                    suggestion["actions"].append(
                        {
                            "type": "manual",
                            "description": solution,
                        }
                    )

            suggestions.append(suggestion)

        return suggestions

