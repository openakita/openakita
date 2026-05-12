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
from datetime import datetime

from .json_utils import (
    as_clean_str,
    coerce_text,
    coerce_tool_names,
    extract_json_array,
    extract_json_object,
    loads_llm_json,
)
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


def _loads_llm_json(text: str):
    """Load JSON emitted by an LLM with a small repair pass.

    This is intentionally conservative: it removes markdown fences and trailing
    commas, but does not try to invent missing fields.
    """
    return loads_llm_json(text)


class MemoryExtractor:
    """AI 驱动的记忆提取器 (v2)"""

    EXTRACTION_PROMPT_V2 = """分析这轮对话，判断是否包含值得长期记住的信息。

对话内容:
[{role}]: {content}
{tool_context}
{extra_context}

### 核心原则：区分「用户是谁」和「用户要做什么」

**记忆只存「用户是谁」**（身份、性格、长期偏好），**不存「用户要做什么」**（任务、指令、请求）。

判断方法：问自己「这条信息在一个月后的新对话中还有用吗？」
- "用户喜欢简洁风格" → 有用 → 记录
- "用户需要苹果照片" → 没用（那是当时的任务） → 不记录
- "用户偏好通过Telegram接收通知" → 有用 → 记录
- "用户希望创建Word报告" → 没用（那是当时的任务） → 不记录

### 值得记录的（真正的长期信息）
- 用户身份：名字、称呼、职业、时区
- 用户性格偏好：沟通风格、语言习惯、审美取向
- 行为规则：用户对 AI 行为的持久要求（提炼为结构化规则）
- 技术环境：常用技术栈、开发工具、OS 信息
- 可复用经验：解决特定类型问题的通用方法
- 失败教训：需要长期避免的操作模式

### 绝对不要记录的
- **一次性任务请求**：「下载X」「搜索Y」「帮我找Z」「整理XX」「生成XX文档」
- **任务产物细节**：文件大小、分辨率、下载链接、具体报告内容
- **临时性需求**：「需要XX照片」「希望获取XX」「想要XX」（这些是当前任务，不是长期偏好）
- **任务执行参数**：在哪个文件夹、几点提醒、发送到哪个渠道（除非用户明确表示这是长期规则）
- 打招呼、寒暄、确认、感谢
- 系统状态、错误堆栈、调试信息
- AI 的回复内容、任务完成报告

### 常见误判示例（不要犯这些错误）
× "用户需要苹果和香蕉照片" → 这是任务请求，不是偏好！
× "用户希望在D盘创建报告" → 这是任务指令，不是规则！
× "图片800x600，150KB" → 这是任务产物细节！
× "整理10条AI新闻" → 这是一次性任务！
× "生成Word文档并保存" → 这是任务指令！
✓ "用户偏好 Jarvis 人格风格" → 这是长期性格偏好
✓ "用户操作系统为Windows" → 这是持久环境事实
✓ "禁止虚报执行结果" → 这是行为规则

### 规则提炼指导
如果用户表达了对 AI 行为的持久要求（如"不要骗我"、"必须认真做"），
应提炼为结构化 RULE。注意：只有「你以后每次都要这样做」的才是规则，
「这次帮我生成Word」不是规则。

对于每条值得记录的信息，用 JSON 输出:
[
  {{
    "type": "FACT|PREFERENCE|RULE|SKILL|ERROR",
    "subject": "实体主语 (谁/什么)",
    "predicate": "属性/关系 (偏好/版本/位于/...)",
    "content": "完整描述（精炼表达，不照抄原文）",
    "importance": 0.5-1.0,
    "duration": "permanent|7d|24h|session",
    "is_update": false,
    "update_hint": ""
  }}
]

duration 参考:
- permanent: 用户身份、长期偏好、行为规则
- 7d: 错误教训、技能经验
- 24h: 任务特定的临时上下文（极少使用）
- session: 仅当前会话有效（极少使用）

如果没有值得记录的信息, 只输出: NONE

注意:
- subject 是"关于谁/什么"的, 如 "用户", "项目X", "Python"
- predicate 是属性关系, 如 "偏好", "版本", "使用工具"
- content 要精简, 不要照抄原文
- is_update: 如果是对已知事实的更新(如版本升级), 设为 true
- 最多输出 2 条记忆（宁少勿多）
- 绝大部分对话不需要记录任何信息，输出 NONE 是最常见的正确答案"""

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

        content = coerce_text(turn.content)
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
            response = await self._call_brain_main(
                prompt,
                system="你是记忆提取专家。只输出 NONE 或 JSON 数组。",
            )

            text = coerce_text(getattr(response, "content", response)).strip()
            if "NONE" in text.upper() or not text:
                return []

            json_text = extract_json_array(text)
            if not json_text:
                return []

            data = _loads_llm_json(json_text)
            if not isinstance(data, list):
                return []

            results = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                c = as_clean_str(item.get("content"))
                if len(c) < 5:
                    continue
                mem_type = as_clean_str(item.get("type"), "FACT").upper()
                duration = as_clean_str(item.get("duration"))
                if duration not in ("permanent", "7d", "24h", "session"):
                    duration = {
                        "RULE": "permanent",
                        "PREFERENCE": "permanent",
                        "SKILL": "permanent",
                        "ERROR": "7d",
                        "FACT": "permanent",
                    }.get(mem_type, "permanent")
                results.append(
                    {
                        "type": mem_type,
                        "subject": as_clean_str(item.get("subject")),
                        "predicate": as_clean_str(item.get("predicate")),
                        "content": c,
                        "importance": max(0.1, min(1.0, float(item.get("importance", 0.5)))),
                        "duration": duration,
                        "is_update": bool(item.get("is_update", False)),
                        "update_hint": as_clean_str(item.get("update_hint")),
                    }
                )

            if results:
                logger.info(f"[Extractor v2] Extracted {len(results)} items from {turn.role}")
            return results

        except Exception as e:
            logger.error(f"[Extractor v2] Extraction failed: {e}")
            return []

    CONVERSATION_EXTRACTION_PROMPT = """回顾整段对话，提取所有值得长期记住的信息。

## 完整对话
{conversation}

### 核心原则：记录稳定信息，普通任务留在会话里

你的职责是发现对未来确实有帮助的信息，但不要把当前任务流水账写成长期记忆。
长期记忆宁可少而准；不确定是否长期有用时，不要输出该条，让会话/情节记录承载它。

### 必须记录的（遇到就记）
- 用户身份：名字、称呼、职业、公司、时区
- 用户偏好：沟通风格、语言习惯、审美取向、技术偏好
- 行为规则：用户对 AI 行为的要求（「每次先做X」「不要Y」）
- 技术环境：常用技术栈、开发工具、OS、运行环境
- **账号和配置**：邮箱地址、API endpoint、端口号、认证方式、服务商（不含密码/密钥原文）
- **验证有效的技术方案**：经过测试确认可用的配置、参数组合、代码模式
- **创建的可复用 Skill/工具**：skill 名称、用途、关键参数（一次性文件产物不记录）
- **重要的事实发现**：调试过程中发现的环境特性、兼容性、限制条件

### 不要记录的
- 打招呼、寒暄、确认、感谢
- 密码、API Key、Token 等敏感凭证原文

对于每条值得记录的信息，用 JSON 输出:
[
  {{
    "type": "FACT|PREFERENCE|RULE|SKILL|ERROR",
    "subject": "实体主语 (谁/什么)",
    "predicate": "属性/关系 (偏好/版本/位于/配置为/...)",
    "content": "完整描述（包含具体的值、路径、参数，可直接复用）",
    "importance": 0.5-1.0,
    "duration": "permanent|7d|24h|session"
  }}
]

如果确实没有任何有价值的信息，输出: NONE

注意:
- 最多输出 5 条记忆
- 对话中多次提到同一信息只提取一次
- content 必须包含具体值（端口号、路径、参数等），不要用模糊描述"""

    EXPERIENCE_EXTRACTION_PROMPT = """回顾整段对话，提取所有**任务经验、操作结果和教训**。

## 完整对话
{conversation}

### 核心原则：只记录可复用经验，不记录普通流水账

只记录下次遇到同类任务时能直接复用的成功方法、关键配置或踩坑教训。
普通执行过程、一次性产物和“已完成”类汇报不要写成长期经验。

### 必须记录的
- **成功的操作和方法**：什么操作最终成功了？用了什么配置/参数/步骤？（必须记录具体值）
- **失败的尝试和原因**：哪些方法失败了？报了什么错？原因是什么？
- **错误→修复的完整路径**：从错误到成功的关键转折（改了什么、为什么管用）
- **环境和配置发现**：调试过程中发现的系统特性、版本兼容性、端口、路径等
- **工具/Skill 使用经验**：用了哪个工具、怎么调用的、效果如何
- **Skill 封装经验**：创建了什么 skill、放在哪里、核心逻辑是什么、注意事项
- **可复用产物模式**：只有当产物模板/脚本/Skill 可长期复用时才记录，普通文件交付不记录

### 不要记录的
- 打招呼、寒暄、感谢
- 用户身份信息（那属于用户画像记忆）

对于每条记录，用 JSON 输出:
[
  {{
    "type": "EXPERIENCE|SKILL|ERROR",
    "subject": "主题 (什么任务/什么操作)",
    "predicate": "属性 (成功方法/失败原因/踩坑教训/Skill封装/最终配置/...)",
    "content": "详细描述（包含具体的参数、路径、配置值、错误信息，确保下次可直接复用）",
    "importance": 0.5-1.0,
    "duration": "permanent|7d"
  }}
]

如果对话中确实没有任何操作或经验，输出: NONE

注意:
- 最多输出 5 条
- 宁可少记普通过程，也不要把一次性任务污染成长期记忆
- content 必须足够具体，让下次看到这条记忆就能直接操作"""

    CITATION_SCORING_SECTION = """

## 本次对话中检索过的记忆（请评分）

以下是本次对话中被检索到的历史记忆，请逐条评判它对本次任务是否有实际帮助：
{cited_memories}

在你的 JSON 输出中，增加一个 "citation_scores" 字段：
"citation_scores": [
  {{"memory_id": "xxx", "useful": true/false}}
]
如果该记忆确实帮助了本次任务的执行（提供了有用的信息、避免了错误等），标记 useful=true。
如果该记忆与本次任务无关或没有实际帮助，标记 useful=false。"""

    async def extract_from_conversation(
        self,
        turns: list[ConversationTurn],
        cited_memories: list[dict] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Extract memories from conversation + score cited memories.

        Returns:
            (extracted_items, citation_scores)
            - extracted_items: list of memory dicts to save
            - citation_scores: list of {memory_id, useful} dicts
        """
        if not self.brain or not turns:
            return [], []

        user_turns = [
            t for t in turns if t.role == "user" and len(coerce_text(t.content).strip()) >= 10
        ]
        if not user_turns:
            return [], []

        from openakita.core.tool_executor import smart_truncate as _st

        conv_lines = []
        for t in turns[-30:]:
            role_label = "用户" if t.role == "user" else "助手"
            content, _ = _st(coerce_text(t.content), 1500, save_full=False, label="mem_conv")
            if content.strip():
                conv_lines.append(f"[{role_label}]: {content}")
            tool_ctx = self._build_tool_context(t.tool_calls, t.tool_results)
            if tool_ctx:
                conv_lines.append(tool_ctx)

        if not conv_lines:
            return [], []

        conversation = "\n".join(conv_lines)
        prompt = self.CONVERSATION_EXTRACTION_PROMPT.format(conversation=conversation)

        has_citations = cited_memories and len(cited_memories) > 0
        if has_citations:
            cited_text = "\n".join(
                f"- ID={m['id']} | {coerce_text(m.get('content', ''))[:150]}"
                for m in cited_memories
            )
            prompt += self.CITATION_SCORING_SECTION.format(cited_memories=cited_text)
            prompt += '\n\n最终输出格式: {"memories": [...], "citation_scores": [...]}\n如果没有要提取的记忆，memories 为空数组。只输出 JSON。'
            system_msg = (
                "你是记忆提取+评分专家。输出 JSON 对象，包含 memories 和 citation_scores 两个字段。"
            )
        else:
            system_msg = "你是记忆提取专家。只输出 NONE 或 JSON 数组。"

        try:
            response = await self._call_brain_main(prompt, system=system_msg)
            text = coerce_text(getattr(response, "content", response)).strip()

            if not has_citations:
                if "NONE" in text.upper() or not text:
                    return [], []
                return self._parse_memory_list(text), []

            json_text = extract_json_object(text)
            if not json_text:
                return self._parse_memory_list(text), []

            data = _loads_llm_json(json_text)
            if not isinstance(data, dict):
                return self._parse_memory_list(text), []

            items = self._parse_memory_items(data.get("memories", []))
            scores = [
                s
                for s in data.get("citation_scores", [])
                if isinstance(s, dict) and "memory_id" in s
            ]

            if items:
                logger.info(
                    f"[Extractor] Conversation extraction: {len(items)} items from {len(turns)} turns"
                )
            if scores:
                useful_count = sum(1 for s in scores if s.get("useful"))
                logger.info(
                    f"[Extractor] Citation scoring: {useful_count}/{len(scores)} marked useful"
                )
            return items, scores

        except Exception as e:
            logger.error(f"[Extractor] Conversation extraction failed: {e}")
            return [], []

    async def extract_experience_from_conversation(
        self,
        turns: list[ConversationTurn],
    ) -> list[dict]:
        """Extract task experience/lessons from conversation (separate from user profile)."""
        if not self.brain or not turns:
            return []

        assistant_turns = [t for t in turns if t.role == "assistant" and coerce_text(t.content)]
        if len(assistant_turns) < 2:
            return []

        from openakita.core.tool_executor import smart_truncate as _st

        conv_lines = []
        for t in turns[-30:]:
            role_label = "用户" if t.role == "user" else "助手"
            content, _ = _st(coerce_text(t.content), 1500, save_full=False, label="mem_conv")
            if content.strip():
                conv_lines.append(f"[{role_label}]: {content}")
            tool_ctx = self._build_tool_context(t.tool_calls, t.tool_results)
            if tool_ctx:
                conv_lines.append(tool_ctx)

        if not conv_lines:
            return []

        conversation = "\n".join(conv_lines)
        prompt = self.EXPERIENCE_EXTRACTION_PROMPT.format(conversation=conversation)

        try:
            response = await self._call_brain_main(
                prompt,
                system="你是任务经验总结专家。只输出 NONE 或 JSON 数组。",
            )
            text = coerce_text(getattr(response, "content", response)).strip()
            if "NONE" in text.upper() or not text:
                return []
            return self._parse_memory_list(text)
        except Exception as e:
            logger.error(f"[Extractor] Experience extraction failed: {e}")
            return []

    def _parse_memory_list(self, text: str) -> list[dict]:
        """Parse a JSON array of memory items from LLM output."""
        json_text = extract_json_array(text)
        if not json_text:
            return []
        try:
            data = _loads_llm_json(json_text)
            if not isinstance(data, list):
                return []
            return self._parse_memory_items(data)
        except (json.JSONDecodeError, ValueError):
            return []

    def _parse_memory_items(self, items: list) -> list[dict]:
        """Normalize a list of raw memory dicts."""
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            c = as_clean_str(item.get("content"))
            if len(c) < 5:
                continue
            mem_type = as_clean_str(item.get("type"), "FACT").upper()
            duration = as_clean_str(item.get("duration"))
            if duration not in ("permanent", "7d", "24h", "session"):
                duration = {
                    "RULE": "permanent",
                    "PREFERENCE": "permanent",
                    "SKILL": "permanent",
                    "ERROR": "7d",
                    "FACT": "permanent",
                    "EXPERIENCE": "permanent",
                }.get(mem_type, "permanent")
            results.append(
                {
                    "type": mem_type,
                    "subject": as_clean_str(item.get("subject")),
                    "predicate": as_clean_str(item.get("predicate")),
                    "content": c,
                    "importance": min(1.0, max(0.3, float(item.get("importance", 0.5)))),
                    "duration": duration,
                    "is_update": bool(item.get("is_update", False)),
                    "update_hint": as_clean_str(item.get("update_hint")),
                }
            )
        return results

    def _build_tool_context(
        self,
        tool_calls: list[dict] | None,
        tool_results: list[dict] | None,
    ) -> str:
        if not tool_calls:
            return ""

        lines = ["\n工具调用:"]
        from openakita.core.tool_executor import smart_truncate as _st

        for tc in (tool_calls or [])[:5]:
            name = tc.get("name", "unknown")
            inp = tc.get("input", tc.get("arguments", {}))
            key_params = (
                {
                    k: v
                    for k, v in inp.items()
                    if k in ("command", "path", "query", "url", "content", "filename")
                }
                if isinstance(inp, dict)
                else {}
            )
            params_str = json.dumps(key_params, ensure_ascii=False)
            params_trunc, _ = _st(params_str, 400, save_full=False, label="mem_tool_param")
            lines.append(f"  - {name}({params_trunc})")

        if tool_results:
            for tr in tool_results[:3]:
                content = tr.get("content", "")
                is_err = tr.get("is_error", False)
                raw = content if isinstance(content, str) else str(content)
                summary, _ = _st(raw, 300, save_full=False, label="mem_tool_result")
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

        from openakita.core.tool_executor import smart_truncate as _st

        def _episode_line(t):
            c, _ = _st(coerce_text(t.content), 600, save_full=False, label="mem_episode")
            suffix = f" [调用了 {len(t.tool_calls)} 个工具]" if t.tool_calls else ""
            return f"[{t.role}]: {c}{suffix}"

        conv_text = "\n".join(_episode_line(t) for t in turns[-20:])

        episode = Episode(
            session_id=session_id,
            started_at=turns[0].timestamp,
            ended_at=turns[-1].timestamp,
            action_nodes=action_nodes,
            tools_used=coerce_tool_names({n.tool_name for n in action_nodes}),
            source=source,
        )

        if self.brain:
            try:
                prompt = self.EPISODE_PROMPT.format(conversation=conv_text)
                resp = await self._call_brain(prompt, system="你是交互情节分析专家。只输出 JSON。")
                text = coerce_text(getattr(resp, "content", resp)).strip()
                json_text = extract_json_object(text)
                if json_text:
                    data = _loads_llm_json(json_text)
                    if not isinstance(data, dict):
                        data = {}
                    episode.summary = as_clean_str(data.get("summary"))
                    episode.goal = as_clean_str(data.get("goal"))
                    episode.outcome = as_clean_str(data.get("outcome"), "completed")
                    raw_entities = data.get("entities", [])
                    if not isinstance(raw_entities, list):
                        raw_entities = [raw_entities]
                    episode.entities = [
                        entity_text
                        for entity in raw_entities
                        if (entity_text := as_clean_str(entity))
                    ]
                    if data.get("tools_used"):
                        tools_used = coerce_tool_names(data["tools_used"])
                        episode.tools_used = list(set(episode.tools_used + tools_used))
            except Exception as e:
                logger.warning(f"[Extractor] Episode LLM generation failed: {e}")

        if not episode.summary:
            episode.summary = self._generate_fallback_summary(turns)
            episode.goal = coerce_text(turns[0].content)[:100] if turns[0].content else ""
            episode.entities = self._extract_entities(turns)

        return episode

    def _extract_action_nodes(self, turns: list[ConversationTurn]) -> list[ActionNode]:
        nodes = []
        for turn in turns:
            if not turn.tool_calls:
                continue
            for tc in turn.tool_calls:
                name = tc.get("name", "")
                inp = tc.get("input", tc.get("arguments", {}))
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
                        result_summary = (content if isinstance(content, str) else str(content))[
                            :200
                        ]
                        if tr.get("is_error"):
                            success = False
                            error_msg = result_summary
                        break

                nodes.append(
                    ActionNode(
                        tool_name=name,
                        key_params=key_params,
                        result_summary=result_summary,
                        success=success,
                        error_message=error_msg,
                        timestamp=turn.timestamp,
                    )
                )
        return nodes

    def _generate_fallback_summary(self, turns: list[ConversationTurn]) -> str:
        user_msgs = [
            coerce_text(t.content)[:100] for t in turns if t.role == "user" and coerce_text(t.content)
        ]
        if user_msgs:
            return f"对话涉及: {'; '.join(user_msgs[:3])}"
        return f"共 {len(turns)} 轮对话"

    def _extract_entities(self, turns: list[ConversationTurn]) -> list[str]:
        entities = set()
        for turn in turns:
            text = coerce_text(turn.content)
            for m in re.finditer(r'[A-Za-z]:[\\\/][^\s"\']+', text):
                entities.add(m.group(0))
            for m in re.finditer(r"[\w-]+\.(?:py|js|ts|md|json|yaml|toml|sh)\b", text):
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
                text = coerce_text(getattr(resp, "content", resp)).strip()

                from openakita.core.tool_executor import smart_truncate as _st

                sp_content, _ = _st(text, 2000, save_full=False, label="mem_scratchpad")
                return Scratchpad(
                    user_id=user_id,
                    content=sp_content,
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
            pad.content = self._append_to_section(pad.content, "近期进展", progress)
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

    _RULE_SIGNAL_PATTERNS = [
        re.compile(r"(?:每次|总是|always)\s*.{4,80}"),
        re.compile(r"(?:不要|不可以|禁止|never)\s*.{4,80}"),
        re.compile(r"(?:必须|务必|一定要|must)\s*.{4,80}"),
        re.compile(r"(?:永远|永远不要)\s*.{4,80}"),
        re.compile(r"(?:规则|rule)[：:]\s*.{4,120}"),
    ]

    def extract_quick_facts(self, messages: list[dict]) -> list[SemanticMemory]:
        """轻量级规则扫描 — 上下文压缩前调用，不使用 LLM。

        仅提取用户消息中含有强规则信号的语句，
        生成 RULE 类型 PERMANENT 优先级的 SemanticMemory。
        """
        from datetime import datetime as _dt

        seen: set[str] = set()
        results: list[SemanticMemory] = []

        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = coerce_text(msg.get("content", ""))
            if len(content) < 5:
                continue

            for pattern in self._RULE_SIGNAL_PATTERNS:
                for match in pattern.finditer(content):
                    snippet = match.group(0).strip()
                    if len(snippet) < 6 or snippet in seen:
                        continue
                    seen.add(snippet)
                    results.append(
                        SemanticMemory(
                            type=MemoryType.RULE,
                            priority=MemoryPriority.PERMANENT,
                            content=snippet,
                            source="quick_rule_scan",
                            subject="user",
                            predicate="rule",
                            importance_score=0.9,
                            confidence=0.7,
                            created_at=_dt.now(),
                            updated_at=_dt.now(),
                        )
                    )
                    if len(results) >= 10:
                        return results
        return results

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

        content = coerce_text(turn.content)
        if len(content.strip()) < 10:
            return []

        try:
            context_text = f"上下文: {context}" if context else ""
            prompt = self.EXTRACTION_PROMPT.format(
                role=turn.role,
                content=content,
                context=context_text,
            )

            response = await self._call_brain_main(
                prompt,
                system="你是记忆提取专家。只输出 NONE 或 JSON 数组，不要其他内容。",
            )

            response_text = coerce_text(getattr(response, "content", response)).strip()
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
        """Call brain with think_lightweight fallback to think.

        Memory extraction is an auxiliary background task that NEVER needs
        chain-of-thought. Both paths must force enable_thinking=False to avoid
        the 4k-7k thinking-token waste seen in dashscope/qwen3 dual-mode setups.
        """
        kwargs: dict = {"system": system} if system else {}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        think_lw = getattr(self.brain, "think_lightweight", None)
        if think_lw and callable(think_lw):
            try:
                # think_lightweight already enforces enable_thinking=False internally
                return await think_lw(prompt, **kwargs)
            except Exception:
                pass
        return await self.brain.think(prompt, enable_thinking=False, **kwargs)

    async def _call_brain_main(self, prompt: str, system: str = "", max_tokens: int | None = None):
        """Always use main model — for critical tasks like memory extraction.

        Even when forced onto the main endpoint we keep thinking off:
        extraction outputs are short JSON, thinking would waste budget.
        """
        kwargs: dict = {"system": system} if system else {}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        return await self.brain.think(prompt, enable_thinking=False, **kwargs)

    def extract_from_turn(self, turn: ConversationTurn) -> list[Memory]:
        """同步规则提取 (向后兼容)"""
        if turn.role != "user":
            return []

        text = coerce_text(turn.content).strip()
        if len(text) < 10:
            return []

        memories: list[Memory] = []

        from openakita.core.tool_executor import smart_truncate as _st

        if any(k in text for k in ("我喜欢", "我更喜欢", "我习惯", "我偏好", "请以后", "以后请")):
            pref_content, _ = _st(text, 400, save_full=False, label="mem_pref")
            memories.append(
                Memory(
                    type=MemoryType.PREFERENCE,
                    priority=MemoryPriority.LONG_TERM,
                    content=pref_content,
                    source="turn_sync",
                    importance_score=0.7,
                    tags=["preference"],
                )
            )

        if any(k in text for k in ("不要", "必须", "禁止", "永远不要", "务必")):
            rule_content, _ = _st(text, 400, save_full=False, label="mem_rule")
            memories.append(
                Memory(
                    type=MemoryType.RULE,
                    priority=MemoryPriority.LONG_TERM,
                    content=rule_content,
                    source="turn_sync",
                    importance_score=0.8 if "永远不要" in text else 0.7,
                    tags=["rule"],
                )
            )

        m = re.search(r"[A-Za-z]:\\\\[^\s\"']{3,}", text)
        if m:
            memories.append(
                Memory(
                    type=MemoryType.FACT,
                    priority=MemoryPriority.LONG_TERM,
                    content=f"用户提到路径: {m.group(0)}",
                    source="turn_sync",
                    importance_score=0.6,
                    tags=["path", "fact"],
                )
            )

        return memories[:2]

    def extract_from_task_completion(
        self,
        task_description: str,
        success: bool,
        tool_calls: list[dict],
        errors: list[str],
    ) -> list[Memory]:
        """Deprecated: Episode 已接管会话总结，不再自动创建低质量 skill 记忆。"""
        return []

    async def extract_with_llm(
        self,
        conversation: list[ConversationTurn],
        context: str = "",
    ) -> list[Memory]:
        """使用 LLM 批量提取 (保留)"""
        if not self.brain or not conversation:
            return []

        conv_text = "\n".join(f"[{t.role}]: {coerce_text(t.content)}" for t in conversation[-30:])

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
            return self._parse_json_response(coerce_text(getattr(response, "content", response)))
        except Exception as e:
            logger.error(f"LLM batch extraction failed: {e}")
            return []

    def _parse_json_response(self, response: str, source: str = "llm_extraction") -> list[Memory]:
        memories = []
        try:
            json_text = extract_json_array(response)
            if not json_text:
                return []
            data = _loads_llm_json(json_text)
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
                content = as_clean_str(item.get("content"))
                if len(content) < 5:
                    continue

                type_str = as_clean_str(item.get("type"), "FACT").upper()
                mem_type = type_map.get(type_str, MemoryType.FACT)

                try:
                    importance = max(0.1, min(1.0, float(item.get("importance", 0.5))))
                except (ValueError, TypeError):
                    importance = 0.5

                # 仅 PERSONA_TRAIT / PREFERENCE / RULE 三类身份层信息
                # 在高置信度且不像任务流水账时才允许进入 PERMANENT。
                # 其它类型（FACT / SKILL / ERROR / CONTEXT / EXPERIENCE）
                # 即便 importance=1.0 也只升到 LONG_TERM，避免任务记录污染
                # USER.md / MEMORY.md（参见 memory.manager P1-7 修复）。
                _persona_types = {
                    MemoryType.PERSONA_TRAIT,
                    MemoryType.PREFERENCE,
                    MemoryType.RULE,
                }
                if importance >= 0.9 and mem_type in _persona_types:
                    priority = MemoryPriority.PERMANENT
                elif importance >= 0.6:
                    priority = MemoryPriority.LONG_TERM
                else:
                    priority = MemoryPriority.SHORT_TERM

                memories.append(
                    Memory(
                        type=mem_type,
                        priority=priority,
                        content=content,
                        source=source,
                        importance_score=importance,
                        subject=as_clean_str(item.get("subject")),
                        predicate=as_clean_str(item.get("predicate")),
                        tags=[as_clean_str(tag) for tag in item.get("tags", []) if as_clean_str(tag)],
                    )
                )

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
