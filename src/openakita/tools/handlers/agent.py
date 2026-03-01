"""
Multi-agent handler — delegate_to_agent, spawn_agent and create_agent.

Only registered when settings.multi_agent_enabled is True.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

DYNAMIC_AGENT_POLICIES = {
    "max_agents_per_session": 5,
    "max_delegation_depth": 5,
    "forbidden_tools": {"create_agent"},
    "max_lifetime_minutes": 60,
}


class AgentToolHandler:
    """Handles delegate_to_agent, delegate_parallel, spawn_agent and create_agent tool calls."""

    TOOLS = ["delegate_to_agent", "delegate_parallel", "spawn_agent", "create_agent"]

    def __init__(self, agent: Agent):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if getattr(self.agent, "_is_sub_agent_call", False):
            logger.warning(
                f"[AgentToolHandler] Blocked {tool_name} — sub-agents cannot delegate"
            )
            return (
                f"❌ 你是子 Agent，不允许使用 {tool_name}。"
                "请直接用你自己的工具完成任务。"
            )
        if tool_name == "delegate_to_agent":
            return await self._delegate(params)
        elif tool_name == "delegate_parallel":
            return await self._delegate_parallel(params)
        elif tool_name == "spawn_agent":
            return await self._spawn(params)
        elif tool_name == "create_agent":
            return await self._create(params)
        return f"❌ Unknown agent tool: {tool_name}"

    # ------------------------------------------------------------------
    # delegate_to_agent
    # ------------------------------------------------------------------

    async def _delegate(self, params: dict[str, Any]) -> str:
        agent_id = (params.get("agent_id") or "").strip()
        message = (params.get("message") or "").strip()
        reason = (params.get("reason") or "").strip()

        if not agent_id:
            return "❌ agent_id is required"
        if not message:
            return "❌ message is required"

        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "❌ Orchestrator not available — multi-agent mode may not be fully initialised"

        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return "❌ No active session — delegation requires a session context"

        current_agent = getattr(
            getattr(session, "context", None), "agent_profile_id", "default"
        ) or "default"

        logger.info(
            f"[AgentToolHandler] Delegation: {current_agent} -> {agent_id} | reason={reason}"
        )

        # Agent Harness: context isolation — build task-focused message
        # instead of relying on the sub-agent seeing full session history
        isolated_message = message
        if reason:
            isolated_message = f"[委派任务] {message}\n[委派原因] {reason}"

        try:
            result = await orchestrator.delegate(
                session=session,
                from_agent=current_agent,
                to_agent=agent_id,
                message=isolated_message,
                reason=reason,
            )
            return str(result)
        except Exception as e:
            logger.error(f"[AgentToolHandler] Delegation failed: {e}", exc_info=True)
            return f"❌ Delegation to {agent_id} failed: {e}"

    # ------------------------------------------------------------------
    # delegate_parallel
    # ------------------------------------------------------------------

    async def _delegate_parallel(self, params: dict[str, Any]) -> str:
        import asyncio
        from collections import Counter

        tasks_param = params.get("tasks")
        if not tasks_param or not isinstance(tasks_param, list):
            return "❌ tasks is required and must be a list"
        if len(tasks_param) < 2:
            return "❌ delegate_parallel requires at least 2 tasks (use delegate_to_agent for single)"
        if len(tasks_param) > 5:
            return "❌ Maximum 5 parallel delegations allowed"

        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "❌ Orchestrator not available"

        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return "❌ No active session"

        current_agent = getattr(
            getattr(session, "context", None), "agent_profile_id", "default"
        ) or "default"

        # Detect duplicate agent_ids — auto-spawn ephemeral clones
        # to avoid two coroutines sharing the same Agent instance.
        agent_ids = [(t.get("agent_id") or "").strip() for t in tasks_param]
        id_counts = Counter(agent_ids)
        duplicated_ids = {aid for aid, cnt in id_counts.items() if cnt > 1}

        ephemeral_ids: list[str] = []  # track for cleanup on error

        resolved_tasks: list[dict] = []
        seen_counter: dict[str, int] = {}
        store = self._get_profile_store() if duplicated_ids else None

        for task in tasks_param:
            agent_id = (task.get("agent_id") or "").strip()
            message = (task.get("message") or "").strip()
            reason = (task.get("reason") or "").strip()

            if agent_id in duplicated_ids:
                seen_counter[agent_id] = seen_counter.get(agent_id, 0) + 1
                if store:
                    # ALL occurrences (including the first) get ephemeral clones
                    # to avoid sharing a pool instance with previous delegations
                    from ...agents.profile import AgentProfile, AgentType, SkillsMode
                    base = store.get(agent_id)
                    if base:
                        ts = int(time.time() * 1000)
                        idx = seen_counter[agent_id]
                        eph_id = f"ephemeral_{agent_id}_{ts}_{idx}"
                        clone = AgentProfile(
                            id=eph_id,
                            name=f"{base.name} (分身{idx})",
                            description=base.description,
                            type=AgentType.DYNAMIC,
                            skills=list(base.skills),
                            skills_mode=base.skills_mode,
                            custom_prompt=base.custom_prompt or "",
                            icon=base.icon or "🤖",
                            color=base.color or "#6b7280",
                            fallback_profile_id=base.fallback_profile_id,
                            created_by="ai_parallel_clone",
                            ephemeral=True,
                            inherit_from=agent_id,
                        )
                        store.save(clone)
                        ephemeral_ids.append(eph_id)
                        logger.info(
                            f"[AgentToolHandler] Auto-spawned clone {eph_id} "
                            f"for parallel task (base={agent_id})"
                        )
                        resolved_tasks.append({
                            "agent_id": eph_id,
                            "display_id": agent_id,
                            "message": message,
                            "reason": reason,
                        })
                        continue

            resolved_tasks.append({
                "agent_id": agent_id,
                "display_id": agent_id,
                "message": message,
                "reason": reason,
            })

        async def _run_one(task: dict) -> tuple[str, str]:
            aid = task["agent_id"]
            display = task["display_id"]
            msg = task["message"]
            rsn = task["reason"]
            if not aid or not msg:
                return display or "?", "❌ agent_id and message are required"
            logger.info(
                f"[AgentToolHandler] Parallel delegation: {current_agent} -> {aid} | reason={rsn}"
            )
            try:
                result = await orchestrator.delegate(
                    session=session,
                    from_agent=current_agent,
                    to_agent=aid,
                    message=msg,
                    reason=rsn,
                )
                return display, str(result)
            except BaseException as e:
                logger.error(f"[AgentToolHandler] Parallel delegation to {aid} failed: {e}")
                return display, f"❌ Failed: {e}"

        coros = [_run_one(t) for t in resolved_tasks]
        try:
            raw_results = await asyncio.gather(*coros, return_exceptions=True)
        except BaseException:
            # On unexpected failure, clean up any ephemeral clones we created
            self._cleanup_ephemeral_ids(ephemeral_ids, store)
            raise

        # Clean up ephemeral clones that the orchestrator didn't already clean
        self._cleanup_ephemeral_ids(ephemeral_ids, store)

        _art_marker = "\n\n__ARTIFACT_RECEIPTS__\n"
        all_receipt_blocks: list[str] = []
        parts = []
        for i, res in enumerate(raw_results):
            if isinstance(res, BaseException):
                display = resolved_tasks[i]["display_id"]
                parts.append(f"## Agent: {display}\n❌ Failed: {res}")
            else:
                display_id, result = res
                # Extract __ARTIFACT_RECEIPTS__ from each sub-agent result
                # so they survive _guard_truncate on the combined string.
                while _art_marker in result:
                    idx = result.index(_art_marker)
                    block_start = idx + len(_art_marker)
                    eol = result.find("\n", block_start)
                    block = result[block_start:] if eol < 0 else result[block_start:eol]
                    all_receipt_blocks.append(block)
                    result = result[:idx] + (result[block_start + len(block):] if eol >= 0 else "")
                parts.append(f"## Agent: {display_id}\n{result}")
        combined = "\n\n---\n\n".join(parts)
        # Re-append all receipt blocks as a single merged JSON array at the end
        if all_receipt_blocks:
            import json as _json
            merged: list = []
            for block in all_receipt_blocks:
                try:
                    parsed = _json.loads(block)
                    if isinstance(parsed, list):
                        merged.extend(parsed)
                except (ValueError, TypeError):
                    pass
            if merged:
                combined += _art_marker.rstrip("\n") + "\n" + _json.dumps(merged, ensure_ascii=False)
        return combined

    @staticmethod
    def _cleanup_ephemeral_ids(ephemeral_ids: list[str], store) -> None:
        """Remove leftover ephemeral profiles created by delegate_parallel."""
        if not store or not ephemeral_ids:
            return
        for eph_id in ephemeral_ids:
            try:
                store.remove_ephemeral(eph_id)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # spawn_agent — 继承已有 Profile 创建临时 Agent 并立即委派
    # ------------------------------------------------------------------

    async def _spawn(self, params: dict[str, Any]) -> str:
        inherit_from = (params.get("inherit_from") or "").strip()
        message = (params.get("message") or "").strip()
        extra_skills: list[str] = params.get("extra_skills") or []
        custom_prompt_overlay = (params.get("custom_prompt_overlay") or "").strip()
        reason = (params.get("reason") or "").strip()

        if not inherit_from:
            return "❌ inherit_from is required — specify the base agent profile_id"
        if not message:
            return "❌ message is required — specify the task for the spawned agent"

        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "❌ Orchestrator not available"

        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return "❌ No active session"

        current_agent = getattr(
            getattr(session, "context", None), "agent_profile_id", "default"
        ) or "default"

        from ...agents.profile import AgentProfile, AgentType, ProfileStore, SkillsMode
        from ...config import settings

        store = self._get_profile_store()
        base_profile = store.get(inherit_from) if store else None
        if base_profile is None:
            return (
                f"❌ Base agent '{inherit_from}' not found. "
                f"Available agents: {', '.join(p.id for p in store.list_all()) if store else 'none'}"
            )

        ts = int(time.time() * 1000)
        ephemeral_id = f"ephemeral_{inherit_from}_{ts}"

        merged_skills = list(base_profile.skills)
        for s in extra_skills:
            if s not in merged_skills:
                merged_skills.append(s)

        merged_prompt = base_profile.custom_prompt or ""
        if custom_prompt_overlay:
            merged_prompt = f"{merged_prompt}\n\n{custom_prompt_overlay}".strip()

        ephemeral_profile = AgentProfile(
            id=ephemeral_id,
            name=f"{base_profile.name} (临时)",
            description=f"Inherited from {inherit_from}: {reason or message[:80]}",
            type=AgentType.DYNAMIC,
            skills=merged_skills,
            skills_mode=base_profile.skills_mode if merged_skills else SkillsMode.ALL,
            custom_prompt=merged_prompt,
            icon=base_profile.icon or "🤖",
            color=base_profile.color or "#6b7280",
            fallback_profile_id=base_profile.fallback_profile_id,
            created_by="ai_spawn",
            ephemeral=True,
            inherit_from=inherit_from,
        )

        store.save(ephemeral_profile)

        logger.info(
            f"[AgentToolHandler] Spawned ephemeral agent: {ephemeral_id} "
            f"(inherited from {inherit_from})"
        )

        try:
            result = await orchestrator.delegate(
                session=session,
                from_agent=current_agent,
                to_agent=ephemeral_id,
                message=message,
                reason=reason or f"Spawned from {inherit_from}",
            )
            return str(result)
        except Exception as e:
            logger.error(f"[AgentToolHandler] Spawn delegation failed: {e}", exc_info=True)
            store.remove_ephemeral(ephemeral_id)
            return f"❌ Spawned agent failed: {e}"

    # ------------------------------------------------------------------
    # create_agent — 最后手段：创建全新 Agent (默认 ephemeral)
    # ------------------------------------------------------------------

    async def _create(self, params: dict[str, Any]) -> str:
        name = (params.get("name") or "").strip()
        description = (params.get("description") or "").strip()
        skills = params.get("skills") or []
        custom_prompt = (params.get("custom_prompt") or "").strip()
        persistent = bool(params.get("persistent", False))

        if not name:
            return "❌ name is required"
        if not description:
            return "❌ description is required"

        session = getattr(self.agent, "_current_session", None)
        if session is None:
            return "❌ No active session — agent creation requires a session context"

        ctx = getattr(session, "context", None)
        history: list[dict] = getattr(ctx, "agent_switch_history", []) if ctx else []
        created_count = sum(1 for h in history if h.get("type") == "dynamic_create")
        max_allowed = DYNAMIC_AGENT_POLICIES["max_agents_per_session"]
        if created_count >= max_allowed:
            return f"❌ Maximum dynamic agents per session reached ({max_allowed})"

        from ...agents.profile import (
            AgentProfile,
            AgentType,
            ProfileStore,
            SkillsMode,
        )
        from ...config import settings

        store = self._get_profile_store()
        force = bool(params.get("force", False))
        suggestion = self._find_similar_profile(store, skills, description) if (store and not force) else None
        if suggestion:
            return (
                f"⚠️ Found a similar existing agent: **{suggestion.name}** (`{suggestion.id}`).\n"
                f"Description: {suggestion.description}\n\n"
                f"Suggestion: use `spawn_agent(inherit_from=\"{suggestion.id}\", ...)` "
                f"to inherit and customize it, or `delegate_to_agent(agent_id=\"{suggestion.id}\", ...)` "
                f"to use it directly.\n\n"
                f"If you still need a completely new agent, call `create_agent(..., force=true)` "
                f"to bypass this check."
            )

        session_key = getattr(session, "session_key", "") or getattr(session, "id", "")
        raw_key = str(session_key)[:12] if session_key else "anon"
        short_key = re.sub(r"[^a-z0-9_]", "", raw_key.lower()) or "anon"
        short_key = short_key[:8]
        raw = name.lower().replace(" ", "_")
        safe_name = re.sub(r"[^a-z0-9_]", "", raw)
        if not safe_name:
            safe_name = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]

        is_ephemeral = not persistent
        if is_ephemeral:
            ts = int(time.time() * 1000)
            profile_id = f"ephemeral_{safe_name}_{ts}"
        else:
            profile_id = f"dynamic_{safe_name}_{short_key}"

        profile = AgentProfile(
            id=profile_id,
            name=name,
            description=description,
            type=AgentType.DYNAMIC,
            skills=skills,
            skills_mode=SkillsMode.INCLUSIVE if skills else SkillsMode.ALL,
            custom_prompt=custom_prompt,
            icon="🤖",
            color="#6b7280",
            created_by="ai",
            ephemeral=is_ephemeral,
        )

        if not store:
            return "❌ ProfileStore not available — cannot create agent"
        store.save(profile)

        if ctx is not None and hasattr(ctx, "agent_switch_history"):
            ctx.agent_switch_history.append({
                "type": "dynamic_create",
                "agent_id": profile_id,
                "name": name,
                "persistent": persistent,
                "ephemeral": is_ephemeral,
                "at": datetime.now(timezone.utc).isoformat(),
            })

        logger.info(
            f"[AgentToolHandler] Created {'persistent' if persistent else 'ephemeral'} "
            f"agent: {profile_id}"
        )
        suffix = " (persistent — will be saved)" if persistent else " (ephemeral — auto-cleanup after task)"
        return f"✅ Agent created: {profile_id} ({name}){suffix}"

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _get_orchestrator(self):
        try:
            from ...main import _orchestrator
            if _orchestrator is None:
                logger.warning("[AgentToolHandler] _orchestrator is None in main module")
            return _orchestrator
        except (ImportError, AttributeError) as e:
            logger.warning(f"[AgentToolHandler] Cannot import _orchestrator: {e}")
            return None

    def _get_profile_store(self):
        """Get the shared ProfileStore (same instance as orchestrator's).

        Critical for ephemeral profiles — they live in memory only, so
        we MUST use the same _ephemeral dict as the orchestrator.
        Never create a separate ProfileStore instance — that would cause
        ephemeral profiles to be invisible across components.
        """
        try:
            orchestrator = self._get_orchestrator()
            if orchestrator is not None:
                orchestrator._ensure_deps()
                if orchestrator._profile_store is not None:
                    return orchestrator._profile_store
        except Exception:
            pass
        logger.warning("[AgentToolHandler] ProfileStore unavailable — orchestrator not initialised")
        return None

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Tokenizer that handles both English words and Chinese characters/bigrams."""
        tokens: set[str] = set()
        for word in text.lower().split():
            if word:
                tokens.add(word)
        cjk = [c for c in text if '\u4e00' <= c <= '\u9fff']
        tokens.update(cjk)
        for i in range(len(cjk) - 1):
            tokens.add(cjk[i] + cjk[i + 1])
        return tokens

    @classmethod
    def _find_similar_profile(
        cls, store, skills: list[str], description: str,
    ):
        """Return the best-matching existing profile if skill overlap > 50%."""
        if not store:
            return None

        from ...agents.profile import AgentType

        all_profiles = store.list_all(include_ephemeral=False)
        if not all_profiles:
            return None

        desc_tokens = cls._tokenize(description)
        best_score = 0.0
        best_profile = None

        for p in all_profiles:
            if p.type == AgentType.DYNAMIC:
                continue

            score = 0.0
            if skills and p.skills:
                overlap = len(set(skills) & set(p.skills))
                total = max(len(skills), len(p.skills))
                score += (overlap / total) * 0.7

            if desc_tokens and p.description:
                p_tokens = cls._tokenize(p.description)
                common = len(desc_tokens & p_tokens)
                total_t = max(len(desc_tokens), len(p_tokens))
                score += (common / total_t) * 0.3 if total_t else 0

            if score > best_score:
                best_score = score
                best_profile = p

        if best_score >= 0.5:
            return best_profile
        return None


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = AgentToolHandler(agent)
    return handler.handle
