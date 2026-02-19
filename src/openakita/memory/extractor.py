"""
记忆提取器 (v2)

功能:
1. AI 判断提取 (v2: 工具感知, 实体-属性结构, 更新检测)
2. 情节生成: 从对话轮次生成 Episode
3. 草稿本更新: 基于最新 Episode 更新 Scratchpad
4. 快速规则提取: 上下文压缩前低延迟提取
5. 任务完成提取 (保留)
6. 批量整理提取 (保留)
7. 去重合并 (保留)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime

from .types import (
    ActionNode,
    ConversationTurn,
    Episode,
    Memory,
    MemoryPriority,
    MemoryType,
    Scratchpad,
    SemanticMemory,
)

logger = logging.getLogger(__name__)


class MemoryExtractor:
    """AI 驱动的记忆提取器 (v2)"""

    EXTRACTION_PROMPT_V2 = """分析这轮对话，提取值得记住的信息。

对话内容:
[{role}]: {content}
{tool_context}
{extra_context}

对于每条值得记录的信息，用 JSON 输出:
[
  {{
    "type": "FACT|PREFERENCE|RULE|SKILL|ERROR",
    "subject": "实体主语 (谁/什么)",
    "predicate": "属性/关系 (偏好/版本/位于/...)",
    "content": "完整描述",
    "importance": 0.5-1.0,
    "is_update": false,
    "update_hint": ""
  }}
]

如果没有值得记录的信息, 只输出: NONE

注意:
- subject 是"关于谁/什么"的, 如 "用户", "项目X", "Python"
- predicate 是属性关系, 如 "偏好", "版本", "使用工具"
- content 要精简, 不要照抄原文
- is_update: 如果是对已知事实的更新(如版本升级), 设为 true
- 最多输出 3 条记忆"""

    EPISODE_PROMPT = """基于以下对话轮次，生成一个情节摘要。

对话:
{conversation}

请用 JSON 格式输出:
{{
  "summary": "一段话描述发生了什么 (100-200字)",
  "goal": "用户的目标/意图",
  "outcome": "success|partial|failed|ongoing",
  "entities": ["涉及的实体: 文件路径、项目名、概念等"],
  "tools_used": ["使用的工具名列表"]
}}"""

    SCRATCHPAD_PROMPT = """你是 AI agent 的工作记忆管理器。基于最新的交互情节，更新工作记忆草稿本。

当前草稿本内容:
{current_scratchpad}

最新情节:
{episode_summary}

请输出更新后的完整草稿本 (Markdown 格式, 不超过 2000 字符):

## 当前项目
- ...

## 近期进展
- ...

## 未解决的问题
- ...

## 下一步
- ..."""

    # 保留 v1 prompt 用于向后兼容
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
        self.brain = brain

    # ==================================================================
    # v2: Entity-Attribute Extraction with Tool Awareness
    # ==================================================================

    async def extract_from_turn_v2(
        self,
        turn: ConversationTurn,
        context: str = "",
    ) -> list[dict]:
        """
        v2 提取: 感知工具调用, 输出实体-属性结构

        Returns:
            List of dicts with keys: type, subject, predicate, content,
            importance, is_update, update_hint
        """
        if not self.brain:
            return []

        content = turn.content or ""
        if len(content.strip()) < 10 and not turn.tool_calls:
            return []

        tool_context = self._build_tool_context(turn.tool_calls, turn.tool_results)
        extra = f"上下文: {context}" if context else ""

        prompt = self.EXTRACTION_PROMPT_V2.format(
            role=turn.role,
            content=content,
            tool_context=tool_context,
            extra_context=extra,
        )

        try:
            response = await self._call_brain(
                prompt, system="你是记忆提取专家。只输出 NONE 或 JSON 数组。",
            )

            text = (getattr(response, "content", None) or str(response)).strip()
            if "NONE" in text.upper() or not text:
                return []

            json_match = re.search(r"\[[\s\S]*\]", text)
            if not json_match:
                return []

            data = json.loads(json_match.group())
            if not isinstance(data, list):
                return []

            results = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                c = (item.get("content") or "").strip()
                if len(c) < 5:
                    continue
                results.append({
                    "type": (item.get("type") or "FACT").upper(),
                    "subject": (item.get("subject") or "").strip(),
                    "predicate": (item.get("predicate") or "").strip(),
                    "content": c,
                    "importance": max(0.1, min(1.0, float(item.get("importance", 0.5)))),
                    "is_update": bool(item.get("is_update", False)),
                    "update_hint": (item.get("update_hint") or "").strip(),
                })

            if results:
                logger.info(f"[Extractor v2] Extracted {len(results)} items from {turn.role}")
            return results

        except Exception as e:
            logger.error(f"[Extractor v2] Extraction failed: {e}")
            return []

    def _build_tool_context(
        self,
        tool_calls: list[dict] | None,
        tool_results: list[dict] | None,
    ) -> str:
        if not tool_calls:
            return ""

        lines = ["\n工具调用:"]
        for tc in (tool_calls or [])[:5]:
            name = tc.get("name", "unknown")
            inp = tc.get("input", {})
            key_params = {k: v for k, v in inp.items() if k in (
                "command", "path", "query", "url", "content", "filename"
            )} if isinstance(inp, dict) else {}
            lines.append(f"  - {name}({json.dumps(key_params, ensure_ascii=False)[:200]})")

        if tool_results:
            for tr in tool_results[:3]:
                content = tr.get("content", "")
                is_err = tr.get("is_error", False)
                summary = content[:150] if isinstance(content, str) else str(content)[:150]
                prefix = "错误" if is_err else "结果"
                lines.append(f"  {prefix}: {summary}")

        return "\n".join(lines)

    # ==================================================================
    # v2: Episode Generation
    # ==================================================================

    async def generate_episode(
        self,
        turns: list[ConversationTurn],
        session_id: str,
        source: str = "session_end",
    ) -> Episode | None:
        """从对话轮次生成情节记忆"""
        if not turns:
            return None

        action_nodes = self._extract_action_nodes(turns)

        conv_text = "\n".join(
            f"[{t.role}]: {(t.content or '')[:300]}"
            + (f" [调用了 {len(t.tool_calls)} 个工具]" if t.tool_calls else "")
            for t in turns[-20:]
        )

        episode = Episode(
            session_id=session_id,
            started_at=turns[0].timestamp,
            ended_at=turns[-1].timestamp,
            action_nodes=action_nodes,
            tools_used=list({n.tool_name for n in action_nodes}),
            source=source,
        )

        if self.brain:
            try:
                prompt = self.EPISODE_PROMPT.format(conversation=conv_text)
                resp = await self._call_brain(
                    prompt, system="你是交互情节分析专家。只输出 JSON。"
                )
                text = (getattr(resp, "content", None) or str(resp)).strip()
                json_match = re.search(r"\{[\s\S]*\}", text)
                if json_match:
                    data = json.loads(json_match.group())
                    episode.summary = data.get("summary", "")
                    episode.goal = data.get("goal", "")
                    episode.outcome = data.get("outcome", "completed")
                    episode.entities = data.get("entities", [])
                    if data.get("tools_used"):
                        episode.tools_used = list(set(episode.tools_used + data["tools_used"]))
            except Exception as e:
                logger.warning(f"[Extractor] Episode LLM generation failed: {e}")

        if not episode.summary:
            episode.summary = self._generate_fallback_summary(turns)
            episode.goal = turns[0].content[:100] if turns[0].content else ""
            episode.entities = self._extract_entities(turns)

        return episode

    def _extract_action_nodes(self, turns: list[ConversationTurn]) -> list[ActionNode]:
        nodes = []
        for turn in turns:
            if not turn.tool_calls:
                continue
            for tc in turn.tool_calls:
                name = tc.get("name", "")
                inp = tc.get("input", {})
                key_params = {}
                if isinstance(inp, dict):
                    for k in ("command", "path", "query", "url", "filename"):
                        if k in inp:
                            key_params[k] = str(inp[k])[:200]

                result_summary = ""
                success = True
                error_msg = None
                tc_id = tc.get("id", "")
                for tr in turn.tool_results:
                    if tr.get("tool_use_id") == tc_id or not tc_id:
                        content = tr.get("content", "")
                        result_summary = (content if isinstance(content, str) else str(content))[:200]
                        if tr.get("is_error"):
                            success = False
                            error_msg = result_summary
                        break

                nodes.append(ActionNode(
                    tool_name=name,
                    key_params=key_params,
                    result_summary=result_summary,
                    success=success,
                    error_message=error_msg,
                    timestamp=turn.timestamp,
                ))
        return nodes

    def _generate_fallback_summary(self, turns: list[ConversationTurn]) -> str:
        user_msgs = [t.content[:100] for t in turns if t.role == "user" and t.content]
        if user_msgs:
            return f"对话涉及: {'; '.join(user_msgs[:3])}"
        return f"共 {len(turns)} 轮对话"

    def _extract_entities(self, turns: list[ConversationTurn]) -> list[str]:
        entities = set()
        for turn in turns:
            text = turn.content or ""
            for m in re.finditer(r'[A-Za-z]:[\\\/][^\s"\']+', text):
                entities.add(m.group(0))
            for m in re.finditer(r'[\w-]+\.(?:py|js|ts|md|json|yaml|toml|sh)\b', text):
                entities.add(m.group(0))
        return list(entities)[:20]

    # ==================================================================
    # v2: Scratchpad Update
    # ==================================================================

    async def update_scratchpad(
        self,
        current: Scratchpad | None,
        episode: Episode,
    ) -> Scratchpad:
        """基于最新情节更新草稿本"""
        current_content = current.content if current else "(空白)"
        user_id = current.user_id if current else "default"

        if self.brain:
            try:
                prompt = self.SCRATCHPAD_PROMPT.format(
                    current_scratchpad=current_content,
                    episode_summary=episode.summary or episode.to_markdown(),
                )
                resp = await self._call_brain(prompt)
                text = (getattr(resp, "content", None) or str(resp)).strip()

                return Scratchpad(
                    user_id=user_id,
                    content=text[:2000],
                    active_projects=self._parse_list_section(text, "当前项目"),
                    current_focus=self._parse_first_item(text, "当前项目"),
                    open_questions=self._parse_list_section(text, "未解决的问题"),
                    next_steps=self._parse_list_section(text, "下一步"),
                    updated_at=datetime.now(),
                )
            except Exception as e:
                logger.warning(f"[Extractor] Scratchpad LLM update failed: {e}")

        pad = current or Scratchpad(user_id=user_id)
        if episode.summary:
            date_str = episode.ended_at.strftime("%m/%d")
            progress = f"- {date_str}: {episode.summary[:100]}"
            pad.content = self._append_to_section(
                pad.content, "近期进展", progress
            )
        pad.updated_at = datetime.now()
        return pad

    @staticmethod
    def _parse_list_section(text: str, section: str) -> list[str]:
        pattern = rf"##\s*{re.escape(section)}\s*\n((?:- .+\n?)*)"
        m = re.search(pattern, text)
        if not m:
            return []
        items = []
        for line in m.group(1).strip().split("\n"):
            line = line.strip()
            if line.startswith("- "):
                items.append(line[2:].strip())
        return items[:10]

    @staticmethod
    def _parse_first_item(text: str, section: str) -> str:
        pattern = rf"##\s*{re.escape(section)}\s*\n- (.+)"
        m = re.search(pattern, text)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _append_to_section(content: str, section: str, item: str) -> str:
        pattern = rf"(##\s*{re.escape(section)}\s*\n)"
        m = re.search(pattern, content)
        if m:
            insert_pos = m.end()
            return content[:insert_pos] + item + "\n" + content[insert_pos:]
        return content + f"\n\n## {section}\n{item}\n"

    # ==================================================================
    # v2: Quick Facts (rule-based, for context compression)
    # ==================================================================

    def extract_quick_facts(self, messages: list[dict]) -> list[SemanticMemory]:
        """
        快速规则提取 — 用于上下文压缩前, 不调用 LLM
        只提取强信号: 偏好、规则、路径
        """
        facts: list[SemanticMemory] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role != "user" or not content or len(content) < 10:
                continue

            if any(k in content for k in ("我喜欢", "我更喜欢", "我习惯", "我偏好")):
                facts.append(SemanticMemory(
                    type=MemoryType.PREFERENCE,
                    priority=MemoryPriority.LONG_TERM,
                    subject="用户",
                    predicate="偏好",
                    content=content[:200],
                    source="quick_extract",
                    importance_score=0.7,
                    tags=["preference"],
                ))

            if any(k in content for k in ("不要", "必须", "禁止", "永远不要")):
                facts.append(SemanticMemory(
                    type=MemoryType.RULE,
                    priority=MemoryPriority.LONG_TERM,
                    subject="用户",
                    predicate="规则",
                    content=content[:200],
                    source="quick_extract",
                    importance_score=0.8,
                    tags=["rule"],
                ))

            m = re.search(r"[A-Za-z]:\\[^\s\"']{3,}", content)
            if m:
                facts.append(SemanticMemory(
                    type=MemoryType.FACT,
                    priority=MemoryPriority.LONG_TERM,
                    subject="用户",
                    predicate="路径",
                    content=f"用户提到路径: {m.group(0)}",
                    source="quick_extract",
                    importance_score=0.6,
                    tags=["path"],
                ))

        return facts[:5]

    # ==================================================================
    # v1 Backward Compatible Methods
    # ==================================================================

    async def extract_from_turn_with_ai(
        self,
        turn: ConversationTurn,
        context: str = "",
    ) -> list[Memory]:
        """v1 兼容: 使用 AI 判断是否应该提取记忆"""
        if not self.brain:
            return []

        if len((turn.content or "").strip()) < 10:
            return []

        try:
            context_text = f"上下文: {context}" if context else ""
            prompt = self.EXTRACTION_PROMPT.format(
                role=turn.role,
                content=turn.content,
                context=context_text,
            )

            response = await self._call_brain(
                prompt,
                system="你是记忆提取专家。只输出 NONE 或 JSON 数组，不要其他内容。",
            )

            response_text = (getattr(response, "content", "") or str(response)).strip()
            if "NONE" in response_text.upper() or not response_text:
                return []

            memories = self._parse_json_response(response_text, turn.role)
            if memories:
                logger.info(f"AI extracted {len(memories)} memories from {turn.role} message")
            return memories

        except Exception as e:
            logger.error(f"AI extraction failed: {e}")
            return []

    async def _call_brain(self, prompt: str, system: str = "", max_tokens: int | None = None):
        """Call brain with think_lightweight fallback to think."""
        kwargs: dict = {"system": system} if system else {}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        think_lw = getattr(self.brain, "think_lightweight", None)
        if think_lw and callable(think_lw):
            try:
                return await think_lw(prompt, **kwargs)
            except Exception:
                pass
        return await self.brain.think(prompt, **kwargs)

    def extract_from_turn(self, turn: ConversationTurn) -> list[Memory]:
        """同步规则提取 (向后兼容)"""
        if turn.role != "user":
            return []

        text = (turn.content or "").strip()
        if len(text) < 10:
            return []

        memories: list[Memory] = []

        if any(k in text for k in ("我喜欢", "我更喜欢", "我习惯", "我偏好", "请以后", "以后请")):
            memories.append(Memory(
                type=MemoryType.PREFERENCE,
                priority=MemoryPriority.LONG_TERM,
                content=text[:200],
                source="turn_sync",
                importance_score=0.7,
                tags=["preference"],
            ))

        if any(k in text for k in ("不要", "必须", "禁止", "永远不要", "务必")):
            memories.append(Memory(
                type=MemoryType.RULE,
                priority=MemoryPriority.LONG_TERM,
                content=text[:200],
                source="turn_sync",
                importance_score=0.8 if "永远不要" in text else 0.7,
                tags=["rule"],
            ))

        m = re.search(r"[A-Za-z]:\\\\[^\s\"']{3,}", text)
        if m:
            memories.append(Memory(
                type=MemoryType.FACT,
                priority=MemoryPriority.LONG_TERM,
                content=f"用户提到路径: {m.group(0)}",
                source="turn_sync",
                importance_score=0.6,
                tags=["path", "fact"],
            ))

        return memories[:2]

    def extract_from_task_completion(
        self, task_description: str, success: bool,
        tool_calls: list[dict], errors: list[str],
    ) -> list[Memory]:
        """从任务完成结果中提取记忆 (保留)"""
        memories = []
        if not task_description or len(task_description.strip()) < 10:
            return memories

        if success:
            if len(task_description) > 20:
                memories.append(Memory(
                    type=MemoryType.SKILL,
                    priority=MemoryPriority.LONG_TERM,
                    content=f"成功完成: {task_description}",
                    source="task_completion",
                    importance_score=0.7,
                    tags=["success", "task"],
                ))
            if tool_calls and len(tool_calls) >= 3:
                tools_used = list({tc.get("name", "") for tc in tool_calls if tc.get("name")})
                if len(tools_used) >= 2:
                    memories.append(Memory(
                        type=MemoryType.SKILL,
                        priority=MemoryPriority.SHORT_TERM,
                        content=f"任务 '{task_description}' 使用工具组合: {', '.join(tools_used)}",
                        source="task_completion",
                        importance_score=0.5,
                        tags=["tools", "pattern"],
                    ))
        else:
            memories.append(Memory(
                type=MemoryType.ERROR,
                priority=MemoryPriority.LONG_TERM,
                content=f"任务失败: {task_description}",
                source="task_completion",
                importance_score=0.7,
                tags=["failure"],
            ))
            for error in errors:
                if len(error) > 20:
                    memories.append(Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"错误教训: {error}",
                        source="task_completion",
                        importance_score=0.8,
                        tags=["error", "lesson"],
                    ))

        return memories

    async def extract_with_llm(
        self, conversation: list[ConversationTurn], context: str = "",
    ) -> list[Memory]:
        """使用 LLM 批量提取 (保留)"""
        if not self.brain or not conversation:
            return []

        conv_text = "\n".join(
            f"[{t.role}]: {t.content}" for t in conversation[-30:]
        )

        prompt = f"""分析以下对话，提取值得长期记住的信息。

对话内容:
{conv_text}

{f"上下文: {context}" if context else ""}

请提取以下类型的信息:
1. **用户偏好** (PREFERENCE)
2. **事实信息** (FACT)
3. **成功模式** (SKILL)
4. **错误教训** (ERROR)
5. **规则约束** (RULE)

用 JSON 格式输出:
[
  {{"type": "类型", "content": "精简的记忆内容", "importance": 0.5-1.0}}
]

如果没有值得记录的信息，输出空数组: []
最多输出 10 条记忆"""

        try:
            response = await self.brain.think(
                prompt,
                system="你是记忆提取专家。只输出 JSON 数组。",
                max_tokens=1000,
            )
            return self._parse_json_response(response.content)
        except Exception as e:
            logger.error(f"LLM batch extraction failed: {e}")
            return []

    def _parse_json_response(self, response: str, source: str = "llm_extraction") -> list[Memory]:
        memories = []
        try:
            json_match = re.search(r"\[[\s\S]*\]", response)
            if not json_match:
                return []
            data = json.loads(json_match.group())
            if not isinstance(data, list):
                return []

            type_map = {
                "PREFERENCE": MemoryType.PREFERENCE,
                "FACT": MemoryType.FACT,
                "SKILL": MemoryType.SKILL,
                "ERROR": MemoryType.ERROR,
                "RULE": MemoryType.RULE,
                "CONTEXT": MemoryType.CONTEXT,
                "PERSONA_TRAIT": MemoryType.PERSONA_TRAIT,
            }

            for item in data:
                if not isinstance(item, dict):
                    continue
                content = (item.get("content") or "").strip()
                if len(content) < 5:
                    continue

                type_str = (item.get("type") or "FACT").upper()
                mem_type = type_map.get(type_str, MemoryType.FACT)

                try:
                    importance = max(0.1, min(1.0, float(item.get("importance", 0.5))))
                except (ValueError, TypeError):
                    importance = 0.5

                if importance >= 0.85 or mem_type == MemoryType.RULE:
                    priority = MemoryPriority.PERMANENT
                elif importance >= 0.6:
                    priority = MemoryPriority.LONG_TERM
                else:
                    priority = MemoryPriority.SHORT_TERM

                memories.append(Memory(
                    type=mem_type,
                    priority=priority,
                    content=content,
                    source=source,
                    importance_score=importance,
                    subject=item.get("subject", ""),
                    predicate=item.get("predicate", ""),
                    tags=item.get("tags", []),
                ))

        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse JSON response: {e}")
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")

        return memories

    def deduplicate(self, memories: list[Memory], existing: list[Memory]) -> list[Memory]:
        """去重合并记忆 (保留)"""
        unique = []
        existing_contents = {m.content.lower() for m in existing}
        for memory in memories:
            content_key = memory.content.lower()
            if content_key not in existing_contents:
                unique.append(memory)
                existing_contents.add(content_key)
        return unique
