"""Sprint-4 P0-2 helpers -- persist orgs_v2 node outputs to disk.

The v15 audit (``_orgs_business_capability_audit_v4.md`` §5.4 / §6.2)
found that despite Sprint-3 wiring ``agent_run_finished`` events with
real node ids, the per-org ``data/orgs/<id>/artifacts/`` and
``data/orgs/<id>/memory/`` directories were still empty for every
v15 test org. Node outputs lived only inside the events.jsonl payload
text, which means a future node has no way to look up "what did
producer just produce?" without re-reading every event.

This module provides the two write helpers the executor invokes on
every successful agent run:

* :func:`persist_node_artifact` -- writes the full LLM output to a
  per-(command, node) text file under ``artifacts/``. The filename
  format encodes the parent-child chain when the node was reached via
  Sprint-4 P0-1 child dispatch, so the on-disk layout reflects the
  delegation tree.
* :func:`persist_node_memory` -- writes a Markdown summary (yaml
  front-matter + truncated body) under ``memory/`` so a future node's
  prompt-builder has a small file to consult without paying the cost
  of re-reading the full artefact. The summary is a static head+tail
  slice; no second LLM call is made (keeps the per-command token
  budget bounded).

Both functions are **fail-silent**: a read-only filesystem, a Windows
file lock, or a missing ``data/`` root must not be allowed to crash
the executor's success path. The audit's policy was already established
by ``events.jsonl`` and ``delegation_logs/`` writes (best-effort,
warn-and-continue); we keep the same posture here.

The whole module is gated by the ``OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS``
environment variable. Setting it to ``"0"`` / ``"false"`` (case
insensitive) disables the writes entirely without code change; useful
for storage-constrained smokes and for the rare bug-triage situation
where artefact creation itself is the suspect.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "MEMORY_SUMMARY_HEAD_CHARS",
    "MEMORY_SUMMARY_TAIL_CHARS",
    "MEMORY_SUMMARY_THRESHOLD",
    "artifact_persistence_enabled",
    "classify_node_output",
    "persist_node_artifact",
    "persist_node_memory",
    "safe_path_segment",
    "strip_deliverable_thinking",
]

_LOGGER = logging.getLogger(__name__)

# Slice constants for the memory summary. The Sprint-4 plan asked for
# "first 200 + last 100 chars" when the raw output exceeds 1 000 chars,
# which preserves both the opening intent ("我会先...") and any final
# conclusion / TODO lines (often the most useful follow-up signal).
# Outputs at or under the threshold are written verbatim so the
# summary is lossless in the common short-reply case.
MEMORY_SUMMARY_THRESHOLD = 1000
MEMORY_SUMMARY_HEAD_CHARS = 200
MEMORY_SUMMARY_TAIL_CHARS = 100


# Single env-var toggle. Resolved lazily (per call) so a runtime
# ``os.environ`` change in a test fixture takes effect without a
# module reload. Empty string and any of the common falsy spellings
# disable persistence; everything else (including "1", "true", "yes"
# and the no-var case) leaves persistence on.
_DISABLE_VALUES = {"0", "false", "no", "off"}
_ENV_VAR = "OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS"


def artifact_persistence_enabled() -> bool:
    """Return ``True`` unless the env var explicitly disables persistence."""

    raw = os.environ.get(_ENV_VAR)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLE_VALUES


# Characters Windows / POSIX both reject inside filenames. We do not
# normalise unicode (node ids in templates are ASCII slugs by the
# template builder's convention), only forbid the structural ones.
_UNSAFE_CHARS = set('<>:"/\\|?*')


def safe_path_segment(raw: str, *, fallback: str = "_") -> str:
    """Sanitise a string for use as a filename segment.

    Drops path separators and Windows-reserved characters, collapses
    whitespace runs to a single underscore, and truncates to 80 chars
    so we cannot accidentally generate a filename longer than the
    Windows MAX_PATH ceiling once joined with the org tree prefix.
    Falls back to a single underscore when the cleaned string is empty
    (mirrors how :mod:`pathlib` handles ``Path("")`` to avoid surfacing
    silent ``FileNotFoundError`` later).
    """

    cleaned_chars: list[str] = []
    last_was_space = False
    for ch in (raw or "").strip():
        if ch in _UNSAFE_CHARS or ord(ch) < 32:
            continue
        if ch.isspace():
            if last_was_space:
                continue
            cleaned_chars.append("_")
            last_was_space = True
            continue
        cleaned_chars.append(ch)
        last_was_space = False
    cleaned = "".join(cleaned_chars).strip("._")
    if not cleaned:
        return fallback
    return cleaned[:80]


def _resolve_org_dir(
    get_org_dir: Callable[[str], Path] | None,
    org_id: str,
) -> Path | None:
    """Best-effort lookup of ``data/orgs/<org_id>/`` on disk.

    The executor passes the lookup as a callable so this module stays
    decoupled from :class:`OrgManager`. When the lookup is missing or
    raises, we fall back to ``Path("data") / "orgs" / org_id`` -- the
    same relative path the rest of the orgs subsystem uses as its
    default. Callers that genuinely need a per-test override can
    monkeypatch this function.
    """

    if get_org_dir is not None:
        try:
            resolved = get_org_dir(org_id)
        except Exception:  # noqa: BLE001 -- lookup must not crash dispatch
            resolved = None
        else:
            if resolved is not None:
                return Path(resolved)
    safe_id = safe_path_segment(org_id, fallback="_unknown")
    return Path("data") / "orgs" / safe_id


# Phrases that announce a NEXT step ("let me search again / get more accurate
# info") — when these are the WHOLE output the node stopped mid-iteration
# without producing a deliverable (test7 RCA: writer-a/writer-b/data-analyst
# returned 76-189B of raw "搜索结果不理想，让我再搜…" reasoning that was then
# registered as a formal deliverable).
_MID_REASONING_MARKERS: tuple[str, ...] = (
    "让我再",
    "让我搜",
    "让我查",
    "让我继续",
    "让我先",
    "我需要搜",
    "我需要获取",
    "我需要找",
    "重新搜索",
    "搜索更",
    "再搜索",
    "继续搜索",
    "不太理想",
    "不合适",
    "需要更准确",
    "需要更权威",
)


# Phrases that PROMISE the deliverable for later instead of containing it. A
# short, heading-less output built around one of these is a status/placeholder
# stub, not a deliverable. Real bug (org_34856abd2e8c, data-analyst 27字):
# "现在我已收集到足够的数据，将整理完整的市场调研报告。" was waved through a
# lenient parent review and absorbed as a near-empty deliverable. These markers
# are deliberately specific to "I will produce / am producing the output" so a
# genuine short factual answer ("已完成关键词优化，核心词为：剑来…") — which
# contains the result, not a promise — is never rejected.
_DEFERRED_DELIVERY_MARKERS: tuple[str, ...] = (
    "将整理",
    "将撰写",
    "将编写",
    "将生成",
    "将输出",
    "将提供完整",
    "将给出完整",
    "将完成",
    "稍后整理",
    "稍后提供",
    "稍后补充",
    "稍后给出",
    "正在整理",
    "正在编写",
    "正在撰写",
    "正在准备",
    "整理中",
    "编写中",
    "撰写中",
    "准备中",
    "马上整理",
    "接下来整理",
    "接下来撰写",
    "接下来我会",
    "接下来将",
)


# Exploratory v21 (2026-06): a node's deliverable text frequently OPENS with
# a leaked chain-of-thought block — Anthropic-style ``<thinking>…</thinking>``
# or a ``<think>…</think>`` variant — followed by the real document. The
# completeness gate (:func:`classify_node_output`) deliberately ACCEPTS such
# output when a real markdown heading is present (reasoning + a document is a
# valid deliverable), so the leak survived all the way into the persisted
# ``.md`` artifact, the rendered PDF, the parent review sample, and the root
# node's final ``command_done`` report. This stripper is the single chokepoint
# (applied in the executor BEFORE persist / classify / return) that removes the
# reasoning block from the *deliverable* — the dedicated live ``node_thinking``
# channel (``_clean_thinking``) is untouched, so the reasoning is still visible
# in the timeline, just never inside the formal deliverable.
_THINKING_BLOCK_RE = re.compile(
    r"<\s*think(?:ing)?\s*>.*?<\s*/\s*think(?:ing)?\s*>",
    re.DOTALL | re.IGNORECASE,
)
_LEADING_OPEN_THINK_RE = re.compile(r"^\s*<\s*think(?:ing)?\s*>", re.IGNORECASE)
_STRAY_THINK_TAG_RE = re.compile(r"<\s*/?\s*think(?:ing)?\s*>", re.IGNORECASE)


def strip_deliverable_thinking(text: str | None) -> str:
    """Remove leaked chain-of-thought from a node *deliverable*.

    Returns the deliverable text with every well-formed
    ``<thinking>…</thinking>`` / ``<think>…</think>`` block removed. Handles
    three shapes seen in the wild:

    1. One or more closed reasoning blocks (most common) -> all removed.
    2. An UNCLOSED leading reasoning tag followed by the real document at the
       first markdown heading -> everything up to that heading is dropped.
    3. An unclosed leading tag with no heading after it (the whole output was
       reasoning) -> returns ``""`` so the caller's completeness gate rejects
       it as ``empty_output`` instead of persisting a thinking-only artifact.

    Conservative by construction: a deliverable that never opens a reasoning
    tag is returned essentially unchanged (only stray bare tags are dropped).
    Never raises; a non-string input yields ``""``.
    """

    if not isinstance(text, str) or not text:
        return ""
    cleaned = _THINKING_BLOCK_RE.sub("", text)
    # An unclosed leading reasoning tag: drop everything up to the first
    # markdown heading (the real document), else treat the whole thing as
    # reasoning and return empty for the completeness gate to reject.
    if _LEADING_OPEN_THINK_RE.search(cleaned):
        lines = cleaned.splitlines()
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("#"):
                cleaned = "\n".join(lines[i:])
                break
        else:
            return ""
    cleaned = _STRAY_THINK_TAG_RE.sub("", cleaned)
    return cleaned.strip()


def classify_node_output(
    output: str | None,
    *,
    delivery_state: str | None = None,
) -> tuple[str, str]:
    """Classify completion from the structured manifest, never prose wording.

    Empty output remains objectively incomplete. A submitted manifest is
    authoritative: only ``complete`` is accepted. Missing manifests are
    incomplete; prose is never promoted to a delivery state.
    """

    if not isinstance(output, str) or not output.strip():
        return ("incomplete", "empty_output")
    if delivery_state is None:
        return ("incomplete", "delivery_manifest_missing")
    if delivery_state == "complete":
        return ("ok", "")
    if delivery_state in {"in_progress", "blocked", "failed"}:
        return ("incomplete", f"delivery_state_{delivery_state}")
    return ("incomplete", "delivery_state_invalid")


def _derive_semantic_title(output: str, *, limit: int = 48) -> str:
    """Derive a human-readable, content-describing title from the output.

    UI feedback (图3/图7): node artefacts were named ``cmd<digits>.md`` —
    opaque. We lead the filename with a SEMANTIC title so a download reads
    like "牧神记线下交流会-策划方案.md" instead of an id blob. The title is
    extracted for FREE from the deliverable itself (no extra LLM token):

    1. the first Markdown ATX heading (``# 标题`` / ``## 标题`` ...), else
    2. the first ``**bold**`` lead line, else
    3. the first non-empty, non-fence prose line.

    The chosen line is stripped of Markdown decoration, collapsed, clipped
    to ``limit`` chars (CJK titles are short) and returned RAW — the caller
    runs :func:`safe_path_segment` to strip filename-unsafe characters. An
    empty return means "no good title found" so the caller keeps the
    id-based fallback name.
    """

    if not isinstance(output, str) or not output.strip():
        return ""
    heading = ""
    bold = ""
    prose = ""
    in_fence = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not heading and line.startswith("#"):
            heading = line.lstrip("#").strip().strip("*_` ")
            if heading:
                break
        if not bold and line.startswith("**") and line.count("**") >= 2:
            bold = line.strip("*").split("**")[0].strip()
        if not prose:
            # Skip obvious non-title lines (front-matter, list bullets,
            # blockquotes, tables) so we land on a real heading-ish line.
            if line[0] in "-*>|" or line.startswith("---"):
                continue
            # Skip a leaked chain-of-thought preamble ("thinking…" / "思考…")
            # so the filename describes the deliverable, not the reasoning.
            low = line.lower()
            if low.startswith("thinking") or line.startswith(
                ("思考", "我需要", "我先", "我会先", "让我")
            ):
                continue
            prose = line.strip("*_`# ")
    title = heading or bold or prose
    # Drop a leading "标题：" / "Title:" style label if present.
    for sep in ("：", ":"):
        if sep in title and len(title.split(sep, 1)[0]) <= 6:
            title = title.split(sep, 1)[1].strip()
            break
    title = title.strip()
    if len(title) > limit:
        title = title[:limit].rstrip()
    return title


def _timestamp_for_filename() -> str:
    """Generate a sortable filename-friendly timestamp.

    Uses ``time.time()`` (UTC monotonic-ish) rendered as
    ``YYYYMMDDTHHMMSSmmm``. ``datetime.utcnow`` would be one line shorter
    but the constant-format requirement is the same and ``time.time``
    keeps us free of timezone surprises across the test matrix.
    """

    now = time.time()
    millis = int((now - int(now)) * 1000)
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(now)) + f"{millis:03d}"


def persist_node_artifact(
    *,
    org_id: str,
    command_id: str,
    node_id: str,
    output: str,
    parent_node_id: str | None = None,
    get_org_dir: Callable[[str], Path] | None = None,
) -> str | None:
    """Write ``output`` to ``data/orgs/<id>/artifacts/`` and return the path.

    Returns ``None`` when persistence is disabled by env var, when the
    org dir cannot be resolved, or when the actual file write fails
    (any I/O exception is logged at DEBUG and swallowed). The returned
    string is the relative-or-absolute path the executor stamps into
    the ``agent_run_finished`` event payload (so SSE consumers can
    deep-link the artefact without a second round-trip).

    Filename layout:

    * Entry-level run (``parent_node_id is None``) ->
      ``<cid>_<node>_<ts>.txt``.
    * Child dispatch (Sprint-4 P0-1) -> ``<cid>_<parent>_<node>_<ts>.txt``
      so a single ``ls`` reveals the delegation chain.

    The ``output`` text is written verbatim (UTF-8, ``newline=""`` so a
    Windows checkout reading on Linux sees the same bytes). Empty
    outputs skip the write -- there is nothing useful in a 0-byte
    artefact and it makes the per-org file count noisier without
    payoff.
    """

    if not artifact_persistence_enabled():
        return None
    if not isinstance(output, str) or not output.strip():
        return None
    org_dir = _resolve_org_dir(get_org_dir, org_id)
    if org_dir is None:
        return None
    # Per-command physical isolation (adversarial re-test, 2026-06): the
    # auto-persisted node deliverable used to land in the SHARED
    # ``<org>/artifacts/`` directory, so every command's outputs piled into
    # one folder — cross-topic clutter that made it easy to mis-pick a stale
    # artefact and bloated the listing. The file *tools* already sandbox
    # writes under ``<org>/commands/<cid>/artifacts/output/``; we now mirror
    # that for auto-persist so a command's deliverables stay together under
    # ``<org>/commands/<cid>/artifacts/``. The returned absolute path is
    # still under the workspace root, so the ``/api/files`` download +
    # ``file_output_registered`` registration chains keep working unchanged.
    cmd_seg = safe_path_segment(command_id, fallback="cmd")
    target_dir = org_dir / "commands" / cmd_seg / "artifacts"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 -- best-effort
        _LOGGER.debug("artifact dir mkdir failed (org=%s)", org_id, exc_info=True)
        return None

    node_seg = safe_path_segment(node_id, fallback="node")
    ts = _timestamp_for_filename()
    # UI feedback (图5/图6): node deliverables are markdown-shaped prose, so we
    # persist them as ``.md`` (was ``.txt``) — the command center / file card
    # then renders them with proper markdown layout and offers a clean download.
    #
    # UI feedback (图3/图7): LEAD the filename with a SEMANTIC title derived
    # for free from the deliverable's own heading so a download reads like
    # "牧神记线下交流会-策划方案_planner_<ts>.md" instead of "cmd<digits>.md".
    # ``node`` + ``ts`` are kept as a short uniqueness suffix so two nodes (or
    # two runs) that happen to share a title never collide and the delegation
    # owner is still legible. When no usable title is found we fall back to the
    # legacy id-led name so the path is always valid.
    title_seg = safe_path_segment(_derive_semantic_title(output), fallback="")
    if title_seg:
        filename = f"{title_seg}_{node_seg}_{ts}.md"
    else:
        cid_seg = safe_path_segment(command_id, fallback="cmd")
        if parent_node_id:
            parent_seg = safe_path_segment(parent_node_id, fallback="parent")
            filename = f"{cid_seg}_{parent_seg}_{node_seg}_{ts}.md"
        else:
            filename = f"{cid_seg}_{node_seg}_{ts}.md"
    path = target_dir / filename
    try:
        path.write_text(output, encoding="utf-8", newline="")
    except Exception:  # noqa: BLE001 -- best-effort, must not poison run
        _LOGGER.debug(
            "artifact write failed (org=%s node=%s path=%s)",
            org_id,
            node_id,
            path,
            exc_info=True,
        )
        return None
    return str(path)


def _build_memory_summary(output: str) -> str:
    """Return the body slice persisted into the memory Markdown file.

    Short outputs (<= :data:`MEMORY_SUMMARY_THRESHOLD` chars) are
    returned verbatim. Long outputs get a head + ellipsis + tail slice
    sized by :data:`MEMORY_SUMMARY_HEAD_CHARS` /
    :data:`MEMORY_SUMMARY_TAIL_CHARS`. We deliberately do NOT call the
    LLM again to summarise: the audit explicitly flagged token-cost as
    a recurring concern (v15 §5.3 token delta) and a static slice is
    enough for prompt-time retrieval of "what did this node just
    say?".
    """

    if len(output) <= MEMORY_SUMMARY_THRESHOLD:
        return output
    head = output[:MEMORY_SUMMARY_HEAD_CHARS].rstrip()
    tail = output[-MEMORY_SUMMARY_TAIL_CHARS:].lstrip()
    return f"{head}\n\n[... truncated ...]\n\n{tail}"


def persist_node_memory(
    *,
    org_id: str,
    command_id: str,
    node_id: str,
    output: str,
    role: str | None = None,
    parent_node_id: str | None = None,
    get_org_dir: Callable[[str], Path] | None = None,
) -> str | None:
    """Write a Markdown memory record to ``data/orgs/<id>/memory/``.

    Returns the written path or ``None`` (same semantics as
    :func:`persist_node_artifact`). The file carries a small YAML
    front-matter block with the run metadata so downstream
    prompt-builders can filter by node / role / command without parsing
    the body. Body is the
    :func:`_build_memory_summary` output -- bounded so the prompt
    budget at retrieval time stays predictable.

    Sprint-4 scope: we do NOT yet wire a retrieval step that feeds
    these files back into the next node's system prompt; that is
    "Inter-node memory retrieval at prompt time" in the
    out-of-scope list. The files exist so the next sprint can read
    them without first re-implementing persistence.
    """

    if not artifact_persistence_enabled():
        return None
    if not isinstance(output, str) or not output.strip():
        return None
    org_dir = _resolve_org_dir(get_org_dir, org_id)
    if org_dir is None:
        return None
    target_dir = org_dir / "memory"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 -- best-effort
        _LOGGER.debug("memory dir mkdir failed (org=%s)", org_id, exc_info=True)
        return None
    cid_seg = safe_path_segment(command_id, fallback="cmd")
    node_seg = safe_path_segment(node_id, fallback="node")
    filename = f"{cid_seg}_{node_seg}.md"
    path = target_dir / filename

    summary = _build_memory_summary(output)
    # Front-matter values are JSON-safe scalars only; raw output is
    # never inlined here so a stray ``---`` inside the body cannot
    # confuse a future YAML parser. ``role`` / ``parent_node_id`` are
    # quoted when present so a tag like ``role: "wb-hh-image"`` (with
    # a dash) does not collide with YAML reserved characters.
    fm_lines: list[str] = [
        "---",
        f'command_id: "{cid_seg}"',
        f'node_id: "{node_seg}"',
        f'org_id: "{safe_path_segment(org_id, fallback="_unknown")}"',
        f'timestamp: "{_timestamp_for_filename()}"',
    ]
    if role:
        fm_lines.append(f'role: "{safe_path_segment(role, fallback="worker")}"')
    if parent_node_id:
        fm_lines.append(f'parent_node_id: "{safe_path_segment(parent_node_id, fallback="parent")}"')
    fm_lines.append(f"chars: {len(output)}")
    fm_lines.append("---")
    contents = "\n".join(fm_lines) + "\n\n" + summary + "\n"
    try:
        path.write_text(contents, encoding="utf-8", newline="")
    except Exception:  # noqa: BLE001 -- best-effort
        _LOGGER.debug(
            "memory write failed (org=%s node=%s path=%s)",
            org_id,
            node_id,
            path,
            exc_info=True,
        )
        return None
    return str(path)
