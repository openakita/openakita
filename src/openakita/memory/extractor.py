"""
Memory Extractor (v2)

Features:
1. AI-driven extraction (v2: tool-aware, entity-attribute structure, update detection)
2. Episode generation: generate Episode from conversation turns
3. Scratchpad update: update Scratchpad based on the latest Episode
4. Quick rule extraction: low-latency extraction before context compression
5. Task completion extraction (retained)
6. Batch consolidation extraction (retained)
7. Deduplication and merging (retained)
"""

from __future__ import annotations

import json
import logging
import re
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
    """AI-driven memory extractor (v2)"""

    EXTRACTION_PROMPT_V2 = """Analyze this conversation turn and determine whether it contains information worth remembering long-term.

Conversation content:
[{role}]: {content}
{tool_context}
{extra_context}

### Core Principle: Distinguish "who the user is" from "what the user wants to do"

**Memory only stores "who the user is"** (identity, personality, long-term preferences), **not "what the user wants to do"** (tasks, commands, requests).

Judgment method: Ask yourself "Will this information still be useful in a new conversation a month from now?"
- "User likes concise style" -> useful -> record
- "User needs apple photos" -> not useful (that was a one-off task) -> don't record
- "User prefers to receive notifications via Telegram" -> useful -> record
- "User wants to create a Word report" -> not useful (that was a one-off task) -> don't record

### Worth recording (truly long-term information)
- User identity: name, title, profession, time zone
- User personality preferences: communication style, language habits, aesthetic preferences
- Behavioral rules: user's persistent requirements for AI behavior (distilled into structured rules)
- Technical environment: common tech stack, dev tools, OS info
- Reusable experience: general methods for solving specific types of problems
- Failure lessons: operation patterns to avoid long-term

### Absolutely do not record
- **One-off task requests**: "download X", "search Y", "help me find Z", "organize XX", "generate XX document"
- **Task output details**: file size, resolution, download links, specific report content
- **Temporary needs**: "need XX photos", "want to get XX", "want XX" (these are current tasks, not long-term preferences)
- **Task execution parameters**: which folder, reminder time, which channel to send to (unless user explicitly says this is a long-term rule)
- Greetings, small talk, acknowledgments, thanks
- System status, error stack traces, debug info
- AI's reply content, task completion reports

### Common misjudgment examples (don't make these mistakes)
X "User needs apple and banana photos" -> This is a task request, not a preference!
X "User wants to create a report on D drive" -> This is a task command, not a rule!
X "Image 800x600, 150KB" -> This is task output detail!
X "Organize 10 AI news items" -> This is a one-off task!
X "Generate Word document and save" -> This is a task command!
OK "User prefers Jarvis persona style" -> This is a long-term personality preference
OK "User's OS is Windows" -> This is a persistent environment fact
OK "Do not misreport execution results" -> This is a behavioral rule

### Rule distillation guidance
If the user expresses a persistent requirement for AI behavior (e.g. "don't lie to me", "must do it carefully"),
distill into a structured RULE. Note: only "you must do this every time from now on" is a rule,
"help me generate Word this time" is not a rule.

For each piece of information worth recording, output JSON:
[
  {{
    "type": "FACT|PREFERENCE|RULE|SKILL|ERROR",
    "subject": "entity subject (who/what)",
    "predicate": "attribute/relation (preference/version/located at/...)",
    "content": "complete description (refined expression, do not copy original text)",
    "importance": 0.5-1.0,
    "duration": "permanent|7d|24h|session",
    "is_update": false,
    "update_hint": ""
  }}
]

duration reference:
- permanent: user identity, long-term preferences, behavioral rules
- 7d: error lessons, skill experience
- 24h: task-specific temporary context (rarely used)
- session: valid only for current session (rarely used)

If no information is worth recording, only output: NONE

Notes:
- subject is "about whom/what", e.g. "user", "project X", "Python"
- predicate is the attribute relation, e.g. "preference", "version", "uses tool"
- content should be concise, do not copy original text
- is_update: set true if this is an update to a known fact (e.g. version upgrade)
- Output at most 2 memories (better fewer than more)
- Most conversations do not need to record any info; outputting NONE is the most common correct answer"""

    EPISODE_PROMPT = """Based on the following conversation turns, generate an episode summary.

Conversation:
{conversation}

Output in JSON format:
{{
  "summary": "a paragraph describing what happened (100-200 chars)",
  "goal": "user's goal/intent",
  "outcome": "success|partial|failed|ongoing",
  "entities": ["entities involved: file paths, project names, concepts, etc."],
  "tools_used": ["list of tool names used"]
}}"""

    SCRATCHPAD_PROMPT = """You are the working memory manager for an AI agent. Based on the latest interaction episode, update the working memory scratchpad.

Current scratchpad content:
{current_scratchpad}

Latest episode:
{episode_summary}

Output the updated complete scratchpad (Markdown format, no more than 2000 chars):

## Current Projects
- ...

## Recent Progress
- ...

## Open Questions
- ...

## Next Steps
- ..."""

    # Retain v1 prompt for backward compatibility
    EXTRACTION_PROMPT = """Analyze this conversation turn and determine whether it contains information worth remembering long-term.

Conversation content:
[{role}]: {content}

{context}

Only record in the following cases:
1. Preferences or habits the user explicitly expresses (e.g. "I like...", "I usually...")
2. Rules or constraints set by the user (e.g. "don't...", "must...", "never...")
3. Important factual information (e.g. user identity, project info, account info)
4. Key methods that successfully solved a problem (if it's an assistant message)
5. Errors or lessons to be avoided

**Most everyday conversations don't need to be recorded**; only record truly important information.

If there's no information worth recording, only output: NONE

If there's information worth recording, output in JSON format:
[
  {{"type": "PREFERENCE|RULE|FACT|SKILL|ERROR", "content": "concise memory content", "importance": 0.5-1.0}}
]

Notes:
- content should be concise, don't copy original text
- importance: 0.5=normal, 0.7=important, 0.9=very important
- Output at most 3 memories"""

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
        v2 extraction: tool-call aware, outputs entity-attribute structure

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
        extra = f"Context: {context}" if context else ""

        prompt = self.EXTRACTION_PROMPT_V2.format(
            role=turn.role,
            content=content,
            tool_context=tool_context,
            extra_context=extra,
        )

        try:
            response = await self._call_brain_main(
                prompt,
                system="You are a memory extraction expert. Output only NONE or a JSON array.",
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
                mem_type = (item.get("type") or "FACT").upper()
                duration = (item.get("duration") or "").strip()
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
                        "subject": (item.get("subject") or "").strip(),
                        "predicate": (item.get("predicate") or "").strip(),
                        "content": c,
                        "importance": max(0.1, min(1.0, float(item.get("importance", 0.5)))),
                        "duration": duration,
                        "is_update": bool(item.get("is_update", False)),
                        "update_hint": (item.get("update_hint") or "").strip(),
                    }
                )

            if results:
                logger.info(f"[Extractor v2] Extracted {len(results)} items from {turn.role}")
            return results

        except Exception as e:
            logger.error(f"[Extractor v2] Extraction failed: {e}")
            return []

    CONVERSATION_EXTRACTION_PROMPT = """Review the entire conversation and extract all information worth remembering long-term.

## Full Conversation
{conversation}

### Core Principle: Proactively record facts

Your job is to **proactively discover and save** valuable information that appears in the conversation. Better to record too much than to miss something.

### Must record (record whenever seen)
- User identity: name, title, profession, company, time zone
- User preferences: communication style, language habits, aesthetic preferences, technical preferences
- Behavioral rules: user's requirements for AI behavior ("always do X first", "don't Y")
- Technical environment: common tech stack, dev tools, OS, runtime environment
- **Accounts and configuration**: email addresses, API endpoints, port numbers, authentication methods, service providers (not including raw passwords/keys)
- **Verified working technical solutions**: tested and confirmed configurations, parameter combinations, code patterns
- **Created files/Skills/tools**: file paths, skill names, purposes, key parameters
- **Important factual discoveries**: environment characteristics, compatibility, limitations found during debugging

### Do not record
- Greetings, small talk, acknowledgments, thanks
- Raw sensitive credentials such as passwords, API keys, tokens

For each piece of information worth recording, output JSON:
[
  {{
    "type": "FACT|PREFERENCE|RULE|SKILL|ERROR",
    "subject": "entity subject (who/what)",
    "predicate": "attribute/relation (preference/version/located at/configured as/...)",
    "content": "complete description (including specific values, paths, parameters; directly reusable)",
    "importance": 0.5-1.0,
    "duration": "permanent|7d|24h|session"
  }}
]

If there's truly no valuable information, output: NONE

Notes:
- Output at most 8 memories
- Extract the same information only once even if mentioned multiple times
- content must include concrete values (port numbers, paths, parameters, etc.); don't use vague descriptions"""

    EXPERIENCE_EXTRACTION_PROMPT = """Review the entire conversation and extract all **task experience, operation results, and lessons learned**.

## Full Conversation
{conversation}

### Core Principle: Completely record what was done, the results, and how it was accomplished

You must record key events and conclusions that occurred in the conversation. Next time you encounter a similar task, these records will let you directly reuse successful approaches and avoid known errors.

### Must record
- **Successful operations and methods**: which operations ultimately succeeded? What configuration/parameters/steps were used? (must record specific values)
- **Failed attempts and reasons**: which methods failed? What errors were reported? What was the cause?
- **Error -> fix complete paths**: key turning points from error to success (what was changed, why it worked)
- **Environment and configuration discoveries**: system features, version compatibility, ports, paths found during debugging
- **Tool/Skill usage experience**: which tool was used, how it was called, what the effect was
- **Skill packaging experience**: what skills were created, where they are, what the core logic is, caveats
- **Final artifacts**: what files were ultimately generated, where they are deployed, how to use them

### Do not record
- Greetings, small talk, thanks
- User identity information (that belongs to user profile memory)

For each record, output JSON:
[
  {{
    "type": "EXPERIENCE|SKILL|ERROR",
    "subject": "subject (what task/what operation)",
    "predicate": "attribute (successful method/failure reason/pitfall lesson/Skill packaging/final config/...)",
    "content": "detailed description (including specific parameters, paths, config values, error info; ensure it can be directly reused next time)",
    "importance": 0.5-1.0,
    "duration": "permanent|7d"
  }}
]

If there's truly no operation or experience in the conversation, output: NONE

Notes:
- Output at most 8
- **Better to record too much than to miss something** - missing a successful experience means stepping into the same pit next time
- content must be specific enough that seeing this memory next time allows direct action"""

    CITATION_SCORING_SECTION = """

## Memories retrieved during this conversation (please score)

Below are the historical memories retrieved during this conversation. Judge each one on whether it actually helped with this task:
{cited_memories}

In your JSON output, add a "citation_scores" field:
"citation_scores": [
  {{"memory_id": "xxx", "useful": true/false}}
]
If the memory actually helped execute this task (provided useful info, avoided errors, etc.), mark useful=true.
If the memory is unrelated to this task or didn't actually help, mark useful=false."""

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
            t for t in turns if t.role == "user" and t.content and len(t.content.strip()) >= 10
        ]
        if not user_turns:
            return [], []

        from openakita.core.tool_executor import smart_truncate as _st

        conv_lines = []
        for t in turns[-30:]:
            role_label = "user" if t.role == "user" else "assistant"
            content, _ = _st(t.content or "", 1500, save_full=False, label="mem_conv")
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
                f"- ID={m['id']} | {m.get('content', '')[:150]}" for m in cited_memories
            )
            prompt += self.CITATION_SCORING_SECTION.format(cited_memories=cited_text)
            prompt += '\n\nFinal output format: {"memories": [...], "citation_scores": [...]}\nIf no memories need extracting, memories is an empty array. Output only JSON.'
            system_msg = (
                "You are a memory extraction + scoring expert. Output a JSON object containing memories and citation_scores fields."
            )
        else:
            system_msg = "You are a memory extraction expert. Output only NONE or a JSON array."

        try:
            response = await self._call_brain_main(prompt, system=system_msg)
            text = (getattr(response, "content", None) or str(response)).strip()

            if not has_citations:
                if "NONE" in text.upper() or not text:
                    return [], []
                return self._parse_memory_list(text), []

            json_match = re.search(r"\{[\s\S]*\}", text)
            if not json_match:
                return self._parse_memory_list(text), []

            data = json.loads(json_match.group())
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

        assistant_turns = [t for t in turns if t.role == "assistant" and t.content]
        if len(assistant_turns) < 2:
            return []

        from openakita.core.tool_executor import smart_truncate as _st

        conv_lines = []
        for t in turns[-30:]:
            role_label = "user" if t.role == "user" else "assistant"
            content, _ = _st(t.content or "", 1500, save_full=False, label="mem_conv")
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
                system="You are a task experience summarization expert. Output only NONE or a JSON array.",
            )
            text = (getattr(response, "content", None) or str(response)).strip()
            if "NONE" in text.upper() or not text:
                return []
            return self._parse_memory_list(text)
        except Exception as e:
            logger.error(f"[Extractor] Experience extraction failed: {e}")
            return []

    def _parse_memory_list(self, text: str) -> list[dict]:
        """Parse a JSON array of memory items from LLM output."""
        json_match = re.search(r"\[[\s\S]*\]", text)
        if not json_match:
            return []
        try:
            data = json.loads(json_match.group())
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
            c = (item.get("content") or "").strip()
            if len(c) < 5:
                continue
            mem_type = (item.get("type") or "FACT").upper()
            duration = (item.get("duration") or "").strip()
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
                    "subject": (item.get("subject") or "").strip(),
                    "predicate": (item.get("predicate") or "").strip(),
                    "content": c,
                    "importance": min(1.0, max(0.3, float(item.get("importance", 0.5)))),
                    "duration": duration,
                    "is_update": bool(item.get("is_update", False)),
                    "update_hint": "",
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

        lines = ["\nTool calls:"]
        from openakita.core.tool_executor import smart_truncate as _st

        for tc in (tool_calls or [])[:5]:
            name = tc.get("name", "unknown")
            inp = tc.get("input", {})
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
                prefix = "Error" if is_err else "Result"
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
        """Generate episode memory from conversation turns"""
        if not turns:
            return None

        action_nodes = self._extract_action_nodes(turns)

        from openakita.core.tool_executor import smart_truncate as _st

        def _episode_line(t):
            c, _ = _st(t.content or "", 600, save_full=False, label="mem_episode")
            suffix = f" [called {len(t.tool_calls)} tools]" if t.tool_calls else ""
            return f"[{t.role}]: {c}{suffix}"

        conv_text = "\n".join(_episode_line(t) for t in turns[-20:])

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
                resp = await self._call_brain(prompt, system="You are an interaction episode analysis expert. Output only JSON.")
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
        user_msgs = [t.content[:100] for t in turns if t.role == "user" and t.content]
        if user_msgs:
            return f"Conversation covered: {'; '.join(user_msgs[:3])}"
        return f"{len(turns)} total turns"

    def _extract_entities(self, turns: list[ConversationTurn]) -> list[str]:
        entities = set()
        for turn in turns:
            text = turn.content or ""
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
        """Update scratchpad based on latest episode"""
        current_content = current.content if current else "(empty)"
        user_id = current.user_id if current else "default"

        if self.brain:
            try:
                prompt = self.SCRATCHPAD_PROMPT.format(
                    current_scratchpad=current_content,
                    episode_summary=episode.summary or episode.to_markdown(),
                )
                resp = await self._call_brain(prompt)
                text = (getattr(resp, "content", None) or str(resp)).strip()

                from openakita.core.tool_executor import smart_truncate as _st

                sp_content, _ = _st(text, 2000, save_full=False, label="mem_scratchpad")
                return Scratchpad(
                    user_id=user_id,
                    content=sp_content,
                    active_projects=self._parse_list_section(text, "Current Projects"),
                    current_focus=self._parse_first_item(text, "Current Projects"),
                    open_questions=self._parse_list_section(text, "Open Questions"),
                    next_steps=self._parse_list_section(text, "Next Steps"),
                    updated_at=datetime.now(),
                )
            except Exception as e:
                logger.warning(f"[Extractor] Scratchpad LLM update failed: {e}")

        pad = current or Scratchpad(user_id=user_id)
        if episode.summary:
            date_str = episode.ended_at.strftime("%m/%d")
            progress = f"- {date_str}: {episode.summary[:100]}"
            pad.content = self._append_to_section(pad.content, "Recent Progress", progress)
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
        """Lightweight rule scan - called before context compression, no LLM used.

        Only extracts statements in user messages containing strong rule signals,
        generating RULE type SemanticMemory with PERMANENT priority.
        """
        from datetime import datetime as _dt

        seen: set[str] = set()
        results: list[SemanticMemory] = []

        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) < 5:
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
        """v1 compatibility: use AI to determine whether to extract memory"""
        if not self.brain:
            return []

        if len((turn.content or "").strip()) < 10:
            return []

        try:
            context_text = f"Context: {context}" if context else ""
            prompt = self.EXTRACTION_PROMPT.format(
                role=turn.role,
                content=turn.content,
                context=context_text,
            )

            response = await self._call_brain_main(
                prompt,
                system="You are a memory extraction expert. Output only NONE or a JSON array, nothing else.",
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

    async def _call_brain_main(self, prompt: str, system: str = "", max_tokens: int | None = None):
        """Always use main model — for critical tasks like memory extraction."""
        kwargs: dict = {"system": system} if system else {}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        return await self.brain.think(prompt, **kwargs)

    def extract_from_turn(self, turn: ConversationTurn) -> list[Memory]:
        """Synchronous rule-based extraction (backward compatible)"""
        if turn.role != "user":
            return []

        text = (turn.content or "").strip()
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
                    content=f"User mentioned path: {m.group(0)}",
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
        """Deprecated: Episode has taken over session summarization; no longer auto-creates low-quality skill memories."""
        return []

    async def extract_with_llm(
        self,
        conversation: list[ConversationTurn],
        context: str = "",
    ) -> list[Memory]:
        """Batch extraction using LLM (retained)"""
        if not self.brain or not conversation:
            return []

        conv_text = "\n".join(f"[{t.role}]: {t.content}" for t in conversation[-30:])

        prompt = f"""Analyze the following conversation and extract information worth remembering long-term.

Conversation content:
{conv_text}

{f"Context: {context}" if context else ""}

Extract the following types of information:
1. **User preferences** (PREFERENCE)
2. **Factual information** (FACT)
3. **Successful patterns** (SKILL)
4. **Error lessons** (ERROR)
5. **Rule constraints** (RULE)

Output in JSON format:
[
  {{"type": "type", "content": "concise memory content", "importance": 0.5-1.0}}
]

If there's no information worth recording, output an empty array: []
Output at most 10 memories"""

        try:
            response = await self.brain.think(
                prompt,
                system="You are a memory extraction expert. Output only a JSON array.",
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

                memories.append(
                    Memory(
                        type=mem_type,
                        priority=priority,
                        content=content,
                        source=source,
                        importance_score=importance,
                        subject=item.get("subject", ""),
                        predicate=item.get("predicate", ""),
                        tags=item.get("tags", []),
                    )
                )

        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse JSON response: {e}")
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")

        return memories

    def deduplicate(self, memories: list[Memory], existing: list[Memory]) -> list[Memory]:
        """Deduplicate and merge memories (retained)"""
        unique = []
        existing_contents = {m.content.lower() for m in existing}
        for memory in memories:
            content_key = memory.content.lower()
            if content_key not in existing_contents:
                unique.append(memory)
                existing_contents.add(content_key)
        return unique
