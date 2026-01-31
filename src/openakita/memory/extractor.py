"""
记忆提取器 - 使用 AI 从对话中自动提取记忆

功能:
1. AI 判断提取: 让 LLM 判断是否值得记录
2. 任务完成提取: 从任务结果中提取经验
3. 批量整理提取: 归纳对话历史精华
4. 去重合并: 避免重复记忆
"""

import re
import json
import logging
from typing import Optional
from datetime import datetime

from .types import Memory, MemoryType, MemoryPriority, ConversationTurn

logger = logging.getLogger(__name__)


class MemoryExtractor:
    """
    AI 驱动的记忆提取器
    
    不再使用简单的关键词规则，而是让 LLM 判断是否值得记录
    """
    
    # AI 判断提取的 prompt
    EXTRACTION_PROMPT = """分析这轮对话，判断是否包含值得长期记住的信息。

对话内容:
[{role}]: {content}

{context}

只有以下情况才值得记录:
1. 用户明确表达的偏好或习惯（如"我喜欢..."、"我习惯..."）
2. 用户设定的规则或约束（如"不要..."、"必须..."、"永远不要..."）
3. 重要的事实信息（如用户身份、项目信息、账号信息）
4. 成功解决问题的关键方法（如果是 assistant 消息）
5. 需要避免的错误或教训

**大部分日常对话都不需要记录**，只记录真正重要的信息。

如果没有值得记录的信息，只输出: NONE

如果有值得记录的信息，用 JSON 格式输出:
[
  {{"type": "PREFERENCE|RULE|FACT|SKILL|ERROR", "content": "精简的记忆内容", "importance": 0.5-1.0}}
]

注意:
- content 要精简，不要照抄原文
- importance: 0.5=一般, 0.7=重要, 0.9=非常重要
- 最多输出 3 条记忆"""
    
    def __init__(self, brain=None):
        """
        Args:
            brain: LLM 大脑实例 (用于 AI 判断提取)
        """
        self.brain = brain
    
    async def extract_from_turn_with_ai(
        self,
        turn: ConversationTurn,
        context: str = "",
    ) -> list[Memory]:
        """
        使用 AI 判断是否应该从这轮对话中提取记忆
        
        这是主要的提取方法，替代之前的关键词规则
        
        Args:
            turn: 对话轮次
            context: 额外上下文（可选）
        
        Returns:
            提取的记忆列表（可能为空）
        """
        if not self.brain:
            logger.debug("No brain available for AI extraction")
            return []
        
        # 太短的消息不需要提取
        if len(turn.content.strip()) < 10:
            return []
        
        try:
            # 构建 prompt
            context_text = f"上下文: {context}" if context else ""
            prompt = self.EXTRACTION_PROMPT.format(
                role=turn.role,
                content=turn.content[:1000],  # 限制长度
                context=context_text,
            )
            
            # 调用 LLM
            response = await self.brain.think(
                prompt,
                system="你是记忆提取专家。只输出 NONE 或 JSON 数组，不要其他内容。",
                max_tokens=500,
            )
            
            # 解析响应
            response = response.strip()
            
            if "NONE" in response.upper() or not response:
                return []
            
            # 尝试解析 JSON
            memories = self._parse_json_response(response, turn.role)
            
            if memories:
                logger.info(f"AI extracted {len(memories)} memories from {turn.role} message")
            
            return memories
            
        except Exception as e:
            logger.error(f"AI extraction failed: {e}")
            return []
    
    def extract_from_turn(self, turn: ConversationTurn) -> list[Memory]:
        """
        从单个对话轮次提取记忆（同步版本，向后兼容）
        
        注意: 这个方法现在返回空列表，实际提取应该使用 extract_from_turn_with_ai
        
        保留此方法是为了向后兼容，但建议使用异步版本
        """
        # 不再使用关键词规则，返回空列表
        # 实际提取应该调用 extract_from_turn_with_ai
        return []
    
    def extract_from_task_completion(
        self,
        task_description: str,
        success: bool,
        tool_calls: list[dict],
        errors: list[str],
    ) -> list[Memory]:
        """
        从任务完成结果中提取记忆
        
        这是重要的记忆来源，记录任务执行的经验
        
        Args:
            task_description: 任务描述
            success: 是否成功
            tool_calls: 工具调用列表
            errors: 错误列表
        
        Returns:
            提取的记忆列表
        """
        memories = []
        
        # 过滤掉空的或太短的任务描述
        if not task_description or len(task_description.strip()) < 10:
            return memories
        
        if success:
            # 记录成功模式（只记录有意义的任务）
            if len(task_description) > 20:  # 避免记录太简单的任务
                memory = Memory(
                    type=MemoryType.SKILL,
                    priority=MemoryPriority.LONG_TERM,
                    content=f"成功完成: {task_description[:200]}",
                    source="task_completion",
                    importance_score=0.7,
                    tags=["success", "task"],
                )
                memories.append(memory)
            
            # 提取使用的工具组合（如果有多个工具）
            if tool_calls and len(tool_calls) >= 3:
                tools_used = list(set(tc.get("name", "") for tc in tool_calls if tc.get("name")))
                if len(tools_used) >= 2:
                    memory = Memory(
                        type=MemoryType.SKILL,
                        priority=MemoryPriority.SHORT_TERM,
                        content=f"任务 '{task_description[:50]}' 使用工具组合: {', '.join(tools_used[:5])}",
                        source="task_completion",
                        importance_score=0.5,
                        tags=["tools", "pattern"],
                    )
                    memories.append(memory)
        else:
            # 记录失败教训
            memory = Memory(
                type=MemoryType.ERROR,
                priority=MemoryPriority.LONG_TERM,
                content=f"任务失败: {task_description[:200]}",
                source="task_completion",
                importance_score=0.7,
                tags=["failure"],
            )
            memories.append(memory)
            
            # 记录具体错误（只记录有意义的错误）
            for error in errors[:2]:  # 最多 2 个
                if len(error) > 20:  # 过滤太短的错误
                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"错误教训: {error[:150]}",
                        source="task_completion",
                        importance_score=0.8,
                        tags=["error", "lesson"],
                    )
                    memories.append(memory)
        
        return memories
    
    async def extract_with_llm(
        self,
        conversation: list[ConversationTurn],
        context: str = "",
    ) -> list[Memory]:
        """
        使用 LLM 批量提取对话中的记忆
        
        适用于:
        - 每日凌晨批量整理对话历史
        - 提取复杂的隐含信息
        
        Args:
            conversation: 对话历史
            context: 额外上下文
        
        Returns:
            提取的记忆列表
        """
        if not self.brain:
            logger.warning("LLM brain not available for batch extraction")
            return []
        
        if not conversation:
            return []
        
        # 构建对话文本
        conv_text = "\n".join([
            f"[{turn.role}]: {turn.content[:500]}"
            for turn in conversation[-30:]  # 最近 30 轮
        ])
        
        prompt = f"""分析以下对话，提取值得长期记住的信息。

对话内容:
{conv_text}

{f"上下文: {context}" if context else ""}

请提取以下类型的信息:
1. **用户偏好** (PREFERENCE): 用户明确表达喜欢或不喜欢什么
2. **事实信息** (FACT): 关于用户或项目的重要事实
3. **成功模式** (SKILL): 有效的解决方案或方法
4. **错误教训** (ERROR): 需要避免的错误
5. **规则约束** (RULE): 用户设定的必须遵守的规则

用 JSON 格式输出:
[
  {{"type": "类型", "content": "精简的记忆内容", "importance": 0.5-1.0}}
]

如果没有值得记录的信息，输出空数组: []

注意:
- 只提取真正有价值的信息，不要提取显而易见的内容
- content 要精简概括，不要照抄原文
- 最多输出 10 条记忆"""
        
        try:
            response = await self.brain.think(
                prompt,
                system="你是记忆提取专家，擅长从对话中识别关键信息。只输出 JSON 数组。",
                max_tokens=1000,
            )
            
            return self._parse_json_response(response)
            
        except Exception as e:
            logger.error(f"LLM batch extraction failed: {e}")
            return []
    
    def _parse_json_response(self, response: str, source: str = "llm_extraction") -> list[Memory]:
        """
        解析 LLM 返回的 JSON 格式响应
        
        Args:
            response: LLM 响应
            source: 记忆来源
        
        Returns:
            记忆列表
        """
        memories = []
        
        try:
            # 提取 JSON 数组
            json_match = re.search(r'\[[\s\S]*\]', response)
            if not json_match:
                return []
            
            data = json.loads(json_match.group())
            
            if not isinstance(data, list):
                return []
            
            for item in data:
                if not isinstance(item, dict):
                    continue
                
                # 解析类型
                type_str = item.get("type", "FACT").upper()
                type_map = {
                    "PREFERENCE": MemoryType.PREFERENCE,
                    "FACT": MemoryType.FACT,
                    "SKILL": MemoryType.SKILL,
                    "ERROR": MemoryType.ERROR,
                    "RULE": MemoryType.RULE,
                    "CONTEXT": MemoryType.CONTEXT,
                }
                mem_type = type_map.get(type_str, MemoryType.FACT)
                
                # 解析内容
                content = item.get("content", "").strip()
                if not content or len(content) < 5:
                    continue
                
                # 解析重要性
                try:
                    importance = float(item.get("importance", 0.5))
                    importance = max(0.1, min(1.0, importance))  # 限制范围
                except (ValueError, TypeError):
                    importance = 0.5
                
                # 根据重要性和类型确定优先级
                if importance >= 0.85 or mem_type == MemoryType.RULE:
                    priority = MemoryPriority.PERMANENT
                elif importance >= 0.6:
                    priority = MemoryPriority.LONG_TERM
                else:
                    priority = MemoryPriority.SHORT_TERM
                
                memory = Memory(
                    type=mem_type,
                    priority=priority,
                    content=content,
                    source=source,
                    importance_score=importance,
                    tags=item.get("tags", []),
                )
                memories.append(memory)
            
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse JSON response: {e}")
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")
        
        return memories
    
    def _parse_llm_response(self, response: str) -> list[Memory]:
        """
        解析旧格式的 LLM 响应（向后兼容）
        
        格式: - [类型] 内容 | 重要性 | 标签
        """
        memories = []
        
        # 匹配格式: - [类型] 内容 | 重要性 | 标签
        pattern = r'-\s*\[(\w+)\]\s*(.+?)\s*\|\s*([\d.]+)\s*\|\s*(.+)'
        
        for match in re.finditer(pattern, response):
            type_str, content, importance, tags_str = match.groups()
            
            type_map = {
                "PREFERENCE": MemoryType.PREFERENCE,
                "FACT": MemoryType.FACT,
                "SKILL": MemoryType.SKILL,
                "ERROR": MemoryType.ERROR,
                "RULE": MemoryType.RULE,
            }
            
            mem_type = type_map.get(type_str.upper(), MemoryType.FACT)
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]
            
            try:
                importance_score = float(importance)
            except ValueError:
                importance_score = 0.5
            
            if importance_score >= 0.8:
                priority = MemoryPriority.PERMANENT
            elif importance_score >= 0.6:
                priority = MemoryPriority.LONG_TERM
            else:
                priority = MemoryPriority.SHORT_TERM
            
            memory = Memory(
                type=mem_type,
                priority=priority,
                content=content.strip(),
                source="llm_extraction",
                importance_score=importance_score,
                tags=tags,
            )
            memories.append(memory)
        
        return memories
    
    def deduplicate(self, memories: list[Memory], existing: list[Memory]) -> list[Memory]:
        """
        去重合并记忆
        
        避免存储重复或相似的记忆
        
        Args:
            memories: 新记忆列表
            existing: 已有记忆列表
        
        Returns:
            去重后的新记忆列表
        """
        unique = []
        existing_contents = set(m.content.lower()[:50] for m in existing)
        
        for memory in memories:
            content_key = memory.content.lower()[:50]
            if content_key not in existing_contents:
                unique.append(memory)
                existing_contents.add(content_key)
        
        return unique
