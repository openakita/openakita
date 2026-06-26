"""Cancel cleanup —— synthetic tool_result + working_messages 持久化（S3, plan: conversation concurrency v1.28）

解决 Anthropic API 400 "tool_use ids were found without tool_result blocks"：

当用户在 LLM 已发出 tool_use 但工具尚未把 tool_result 写回 working_messages 时
按下取消按钮，``working_messages`` 最末 ``assistant`` 消息会包含孤儿
``tool_use`` blocks，再发给 LLM 会被 Anthropic API 拒绝。同样的链路在
preempt / abandoned 路径也会触发。

S3 的策略：
1. 在 ``working_messages`` 末尾**追加** 一条 ``user`` 消息，里面给每个孤儿
   ``tool_use_id`` 填一个 ``tool_result`` 块，content 是 "[Interrupted ...]"。
   这是 **synthetic 补偿**，不写 ``session.context.messages``——只让下一轮
   LLM 看到配对消息，不污染用户可见历史。
2. 把整份 ``working_messages``（已补偿）持久化到
   ``data/working_messages/<conversation_id>.json``，带 mtime；下一轮
   ``reason_stream`` / ``run`` 入口检查这个文件存在且未过期（24h TTL）时
   就直接用它作为起始状态，而不是从 ``session.context.messages`` 重建。
3. 重建一次性消费：load 成功后立即删除文件，避免后续重复使用
   留下旧 synthetic 补偿。

为什么不直接修复 session.context.messages？
- 用户在 UI 看到的是清晰的"我说了 → AI 答了 → 我中断 → ……"，synthetic
  ``[Interrupted]`` 是给 LLM 的内部信号，不是给人看的；写进 session 后
  下次渲染会出现一串机械占位符。
- v1.27.15 的 ``aborted_partial`` marker 已经在用户可见侧给了 "ai 说到一半"
  的反馈，足够了。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS: int = 86400  # 24h
# Issue #608 — two *separate* time windows, deliberately decoupled (this mirrors
# how claude-code / hermes / openclaw / QwenPaw behave: none of them discard the
# completed tool work by age):
#
#   * LOAD window == DEFAULT_TTL_SECONDS (24h): a persisted snapshot is always
#     restored if it still exists, so the model never re-runs already-completed
#     tools just because the user came back a few hours later.  The 24h bound is
#     pure hygiene, matching the startup janitor — anything older was a crash
#     leftover the janitor should already have swept.
#   * HINT freshness (1h): only gates whether we inject the "上一轮被中断…接着干"
#     nudge.  Past this window we still feed the completed tool results back
#     (no redo) but stop actively telling the model to continue — a long-stale
#     resume is more likely a topic change.  Aligns with hermes' 3600s
#     gateway auto-continue freshness.
RESUME_HINT_FRESHNESS_SECONDS: int = 3600  # 1h
DEFAULT_INTERRUPT_TEXT: str = "[Tool call interrupted by user/system before completion.]"


def persisted_age_seconds(conversation_id: str, *, base_dir: str | os.PathLike) -> float | None:
    """Age (seconds) of the persisted snapshot for ``conversation_id`` based on
    its file mtime, or None if no file exists.  Read-only: does NOT consume the
    file, so callers can peek freshness before a consuming load.  Never raises.
    """
    if not conversation_id:
        return None
    target = _wm_path(conversation_id, base_dir)
    try:
        if not target.exists():
            return None
        return max(0.0, time.time() - target.stat().st_mtime)
    except OSError:
        return None


def has_tool_blocks(working_messages: list[dict] | None) -> bool:
    """True if any message carries a structured ``tool_use`` / ``tool_result``
    block.  Used to decide whether a cancelled turn is worth persisting for
    resume — a turn that never reached a tool call has nothing to carry
    forward and would just bloat ``data/working_messages/``.
    """
    for msg in working_messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                return True
    return False


def find_orphan_tool_uses(working_messages: list[dict]) -> list[dict[str, str]]:
    """扫描 working_messages，找出所有未配对的 tool_use blocks。

    Returns:
        list of {"id": tool_use_id, "name": tool_name} for each orphan,
        in order of appearance.  Empty list if everything is paired.
    """
    if not working_messages:
        return []

    # 1) collect all tool_use blocks
    pending: dict[str, dict[str, str]] = {}
    for msg in working_messages:
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if role == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    use_id = block.get("id")
                    if isinstance(use_id, str) and use_id:
                        pending[use_id] = {
                            "id": use_id,
                            "name": str(block.get("name", "")),
                        }
        elif role == "user":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    use_id = block.get("tool_use_id")
                    if isinstance(use_id, str) and use_id in pending:
                        pending.pop(use_id, None)

    # dict preserves insertion order in 3.7+; this is the canonical order
    # of the orphans by their tool_use appearance.
    return list(pending.values())


def synthesize_tool_results_for_orphans(
    working_messages: list[dict],
    *,
    interrupt_text: str = DEFAULT_INTERRUPT_TEXT,
) -> int:
    """Walk working_messages; for every ``assistant`` message that contains
    one or more ``tool_use`` blocks not paired in the *next* message, insert
    a synthetic ``user(tool_result)`` message **right after** it.

    The "right after" placement is critical: Anthropic API requires the
    sequence ``assistant(tool_use) → user(tool_result)`` and rejects
    ``assistant(tool_use) → user(text) → user(tool_result)`` (out-of-order
    consolidation).  A naive append at the tail would break the common case
    where the user types "继续" after a cancel — that new user message would
    sit between the orphan tool_use and the synthetic tool_result.

    Mutates ``working_messages`` in place. Returns the total number of
    orphan tool_use_id's that were synthesized for (across all positions).
    """
    if not working_messages:
        return 0

    # First pass: collect every tool_use_id that already has a tool_result
    # somewhere later in the conversation (regardless of position).  These
    # are NOT orphans and must not get a synthetic block.
    paired_ids: set[str] = set()
    for msg in working_messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                uid = block.get("tool_use_id")
                if isinstance(uid, str):
                    paired_ids.add(uid)

    # Second pass: for each assistant message with orphan tool_use blocks,
    # build a synthetic insertion. Collect (insert_after_index, blocks) and
    # apply in reverse so insertion indices stay valid.
    insertions: list[tuple[int, list[dict[str, Any]]]] = []
    total_synth = 0
    for idx, msg in enumerate(working_messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        orphan_blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            uid = block.get("id")
            if not isinstance(uid, str) or not uid:
                continue
            if uid in paired_ids:
                continue
            orphan_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": uid,
                    "content": interrupt_text,
                    "is_error": True,
                }
            )
        if orphan_blocks:
            insertions.append((idx, orphan_blocks))
            total_synth += len(orphan_blocks)

    if not insertions:
        return 0

    # Apply in reverse so earlier indices remain valid.
    for idx, blocks in reversed(insertions):
        working_messages.insert(
            idx + 1,
            {
                "role": "user",
                "content": blocks,
                # marker so downstream code (memory extractor, audit log,
                # debugger) can distinguish synthetic compensation from
                # real tool results.
                "_synthetic": True,
            },
        )

    logger.info(
        "[CancelCleanup] synthesized %d tool_result block(s) at %d position(s)",
        total_synth,
        len(insertions),
    )
    return total_synth


# ---------- Persistence: data/working_messages/<conv_id>.json ----------


def _safe_conv_id(conversation_id: str) -> str:
    """Sanitize conversation_id to a filesystem-safe stem.  We keep alnum,
    dash, underscore; anything else is replaced with '_'."""
    safe = []
    for ch in str(conversation_id):
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
        else:
            safe.append("_")
    s = "".join(safe).strip("_") or "default"
    # cap length to avoid edge-case crazy-long ids
    return s[:128]


def _wm_path(conversation_id: str, base_dir: str | os.PathLike) -> Path:
    return Path(base_dir) / "working_messages" / f"{_safe_conv_id(conversation_id)}.json"


def persist_working_messages(
    conversation_id: str,
    working_messages: list[dict],
    *,
    base_dir: str | os.PathLike,
    metadata: dict | None = None,
) -> Path | None:
    """Atomically write working_messages to disk so the next turn can resume.

    Returns the Path written, or None if persistence failed (logged, not raised).
    """
    if not conversation_id or not working_messages:
        return None

    target = _wm_path(conversation_id, base_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "conversation_id": conversation_id,
            "saved_at": time.time(),
            "metadata": metadata or {},
            "messages": working_messages,
        }
        # atomic write via temp + replace
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{_safe_conv_id(conversation_id)}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.info(
            "[CancelCleanup] persisted working_messages: conv=%s, msgs=%d, path=%s",
            conversation_id,
            len(working_messages),
            target,
        )
        return target
    except Exception as exc:
        logger.warning(
            "[CancelCleanup] failed to persist working_messages for conv=%s: %s",
            conversation_id,
            exc,
        )
        return None


def load_persisted_working_messages(
    conversation_id: str,
    *,
    base_dir: str | os.PathLike,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    consume: bool = True,
) -> list[dict] | None:
    """Load the persisted working_messages for ``conversation_id`` if present
    and not expired.  Returns None when the file is missing, malformed, or
    older than ``ttl_seconds``.

    When ``consume=True`` (default), the file is deleted after a successful
    load so subsequent turns don't reuse stale synthetic compensation.
    """
    if not conversation_id:
        return None

    target = _wm_path(conversation_id, base_dir)
    if not target.exists():
        return None

    try:
        st = target.stat()
        age = time.time() - st.st_mtime
        if age > ttl_seconds:
            logger.info(
                "[CancelCleanup] persisted working_messages expired: conv=%s, age=%.0fs, ttl=%ds",
                conversation_id,
                age,
                ttl_seconds,
            )
            try:
                target.unlink()
            except OSError:
                pass
            return None
    except OSError:
        return None

    try:
        with target.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        msgs = payload.get("messages")
        if not isinstance(msgs, list):
            logger.warning(
                "[CancelCleanup] persisted file malformed (messages not list): %s", target
            )
            try:
                target.unlink()
            except OSError:
                pass
            return None
    except Exception as exc:
        logger.warning(
            "[CancelCleanup] failed to read persisted working_messages for conv=%s: %s",
            conversation_id,
            exc,
        )
        try:
            target.unlink()
        except OSError:
            pass
        return None

    if consume:
        try:
            target.unlink()
        except OSError:
            pass

    logger.info(
        "[CancelCleanup] loaded persisted working_messages: conv=%s, msgs=%d",
        conversation_id,
        len(msgs),
    )
    return msgs


def clear_persisted_working_messages(conversation_id: str, *, base_dir: str | os.PathLike) -> bool:
    """Delete the persisted file for ``conversation_id``.  Returns True if a
    file was removed, False otherwise.  Never raises."""
    target = _wm_path(conversation_id, base_dir)
    try:
        if target.exists():
            target.unlink()
            return True
    except OSError as exc:
        logger.debug("[CancelCleanup] could not delete %s: %s", target, exc)
    return False


def cleanup_expired_working_messages(
    *, base_dir: str | os.PathLike, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> int:
    """Scan ``<base_dir>/working_messages/`` and delete any file older than
    ``ttl_seconds``.  Returns count of deleted files.  Never raises.

    Intended to run once at process startup so a crash that left stale files
    doesn't keep injecting yesterday's synthetic compensation."""
    folder = Path(base_dir) / "working_messages"
    if not folder.exists():
        return 0
    now = time.time()
    deleted = 0
    try:
        for p in folder.iterdir():
            if not p.is_file():
                continue
            try:
                age = now - p.stat().st_mtime
            except OSError:
                continue
            if age > ttl_seconds:
                try:
                    p.unlink()
                    deleted += 1
                except OSError:
                    pass
    except OSError as exc:
        logger.debug("[CancelCleanup] cleanup scan failed: %s", exc)
    if deleted:
        logger.info(
            "[CancelCleanup] startup cleanup: removed %d expired working_messages file(s)",
            deleted,
        )
    return deleted
