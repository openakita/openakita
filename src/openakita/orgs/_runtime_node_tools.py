"""``_runtime_node_tools.py`` -- v2 orgs node-level tool resolution + execution.

Sprint-5 P0-1 (audit ``_orgs_business_capability_audit_v5.md`` §5.2 / §7.1)
lands the **D4 minimum-viable cut**: per-node ``external_tools`` declared on
the v1 ``OrgNode`` (which is what every aigc-video-studio v16-* test org
materialises into) are resolved into a real Anthropic-shaped tool list and
passed to :meth:`Brain.messages_create_async`. When the LLM emits a
``tool_use`` block the node agent runs the handler via the same global
:data:`openakita.tools.handlers.default_handler_registry` the v1 chat path
uses, splices the ``tool_result`` block back into the conversation, and
calls the brain a **second** time so the LLM can finalise its reply.

Sprint-5 shipped a one-round bound; the test7 quality RCA (2026-06)
lifted it to a **bounded, budget-guarded multi-round ReAct loop** (see
:data:`MAX_TOOL_ROUNDS` / :data:`MAX_TOOL_CALLS` and the docstring of
:func:`run_with_tools`) so a node can iterate
(search -> search again -> write) instead of delivering its raw
mid-reasoning. MCP servers and skill SKILL.md auto-loading remain
deferred (audit §7.1 ``Not in P0-1 scope``).

### Why mirror v1 and not re-implement

The v1 chat path already wires every system tool handler (filesystem,
research, memory, planning, web_fetch, web_search, ...) into the global
:class:`SystemHandlerRegistry`. The orgs_v2 node path was sending
``tools=[]`` to the brain and therefore the v16 LLM debug dumps reported
``tools_count = 0`` for every workbench dispatch (audit v5 §5.2.2). By
reusing the same registry we:

* avoid duplicating handler implementations,
* benefit from any future tool the main agent gains for free,
* keep the per-node *whitelist* mechanic (``external_tools``) as the
  single source of truth for what a node may call,
* and stay zero-dependency on the workbench / MCP plumbing that the
  workbench nodes will eventually need (those routes through plugin
  manifests, which v2 does not consume yet).

### What is intentionally out of scope

* **Workbench (``hh_*``) tools** -- those live in the
  ``happyhorse-video`` plugin manifest. The plugin handler registry is
  separate from :data:`default_handler_registry`; binding it requires
  the workbench wiring tracked under the D4-ext follow-up. We **filter
  unknown tool names** so the LLM still gets the standard subset
  (research / planning / filesystem / memory etc) and the node can do
  *something* useful even on a workbench node.
* **MCP servers** declared on ``node.mcp_servers`` -- ignored for now
  (audit §7.1 explicit ``Not in P0-1 scope``).
* **Skill SKILL.md auto-load** (D4-ext) -- deferred.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._runtime_agent_host import NodeToolHost

# ── Retrieval-result sanitizer (exploratory v23 reliability fix) ───────────
# Root cause of the偶发 ``data-analyst 任务失败`` (read from real events): a
# ``web_search`` for "《凡人修仙传》B站播放量" returned duckduckgo entries whose
# snippets were explicit AI-porn / 同人 H漫 text (口交/肉棒/性爱…). Those snippets
# were spliced into the NEXT LLM prompt verbatim, and the cloud model
# (dashscope deepseek-r1) rejected the whole request with HTTP 400
# ``data_inspection_failed`` (内容安全审核未通过) -> all endpoints failed -> the
# node raised -> task failed. Stripping the explicit ENTRIES from retrieval
# results before they reach the LLM removes the moderation trigger AND
# improves relevance (the off-topic NSFW hits are exactly the "无关内容" the
# P1.2 browser-relevance item flagged). Conservative term list so we drop the
# offending result line, not legitimate on-topic content.
_RETRIEVAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "web_search",
        "search",
        "web_fetch",
        "fetch",
        "browse",
        "browser",
        "browser_navigate",
        "browser_search",
        "read_url",
        "open_url",
    }
)

# Explicit adult/porn markers. Deliberately narrow (overt sexual terms only) so
# we never strip ordinary 玄幻/修仙 vocabulary. Matching is case-insensitive.
_NSFW_TERMS: tuple[str, ...] = (
    "口交", "深喉", "肉棒", "性爱", "做爱", "乳交", "巨乳", "内射", "射精",
    "淫荡", "淫趴", "骑上去", "插入", "高潮", "情色", "色情", "裸体", "脱光",
    "H漫", "h漫", "成人动漫", "黄漫", "无码", "포르노",
    "porn", "xxx", "nsfw", "hentai", "blowjob", "cum", "nude", "sex video",
)
_NSFW_RE = re.compile("|".join(re.escape(t) for t in _NSFW_TERMS), re.IGNORECASE)


def _sanitize_retrieval_result(tool_name: str, text: str) -> tuple[str, int]:
    """Drop explicit-adult lines from a retrieval tool result.

    Returns ``(clean_text, dropped_lines)``. No-op (returns the text and 0)
    for non-retrieval tools or text without any flagged line, so the common
    path stays byte-for-byte unchanged.
    """

    if tool_name not in _RETRIEVAL_TOOL_NAMES or not text:
        return text, 0
    if not _NSFW_RE.search(text):
        return text, 0
    kept: list[str] = []
    dropped = 0
    for line in text.split("\n"):
        if _NSFW_RE.search(line):
            dropped += 1
            continue
        kept.append(line)
    if dropped == 0:
        return text, 0
    clean = "\n".join(kept).strip()
    clean += (
        f"\n\n[已自动过滤 {dropped} 条与任务无关的成人/不相关检索结果。"
        "如需该领域数据请改用更精确的检索词。]"
    )
    return clean, dropped


# ── Node-written file tracking (test11 P1/P2 fix) ─────────────────────────
# Root cause (read from org_34856abd2e8c real events): a node that does its
# real work by WRITING A FILE (writer-a wrote a 12 KB rezero_fan_meeting_plan.md
# via write_file) but ends its turn with EMPTY final text was judged "空产出"
# by the parent review and bounced / escalated -- the on-disk deliverable was
# ignored. Separately, write_file / deliver_artifacts in the node path NEVER
# emitted ``file_output_registered``, so the command-center delivery cards had
# nothing to show even though files existed. Both are fixed by recording the
# files a node writes (keyed by org+command+node) and emitting a
# ``file_output_registered`` event the moment a write/deliver tool succeeds.
import time as _time  # noqa: E402 -- local to keep import graph light at top

# (org_id, command_id, node_id) -> list[(abs_path, ts, tool_name)]
_NODE_FILE_OUTPUTS: dict[tuple[str, str, str], list[tuple[str, float, str]]] = {}

# Tools whose successful call means "a deliverable file now exists on disk".
_FILE_PRODUCING_TOOLS: frozenset[str] = frozenset(
    {"write_file", "append_file", "edit_file", "create_file"}
)


def _extract_written_paths(tool_name: str, tool_input: Mapping[str, Any]) -> list[str]:
    """Best-effort list of absolute file paths a successful tool just wrote.

    Covers the write-class tools (path/file_path keys, already redirected into
    the per-command workspace) and ``deliver_artifacts`` (its ``artifacts`` list
    of ``{type:'file', path:...}`` entries).
    """

    if not isinstance(tool_input, Mapping):
        return []
    out: list[str] = []
    if tool_name in _FILE_PRODUCING_TOOLS:
        for key in _WRITE_DEST_KEYS.get(tool_name, ("path", "file_path")):
            v = tool_input.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v)
                break
    elif tool_name == "deliver_artifacts":
        arts = tool_input.get("artifacts")
        if isinstance(arts, (list, tuple)):
            for a in arts:
                if isinstance(a, Mapping):
                    pth = a.get("path") or a.get("file_path")
                    if isinstance(pth, str) and pth.strip():
                        out.append(pth)
    return out


def record_node_file_output(
    org_id: str, command_id: str | None, node_id: str, path: str, tool_name: str
) -> int | None:
    """Record one node-written file; returns its size in bytes (or ``None``).

    No-op (returns ``None``) when the file does not exist on disk yet, so a
    failed / clamped write never registers a phantom deliverable.
    """

    if not command_id:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        size = p.stat().st_size
    except OSError:
        return None
    key = (org_id, command_id, node_id)
    _NODE_FILE_OUTPUTS.setdefault(key, []).append((str(p), _time.time(), tool_name))
    return size


def pop_node_file_outputs(
    org_id: str, command_id: str | None, node_id: str, *, since_ts: float = 0.0
) -> list[dict[str, Any]]:
    """Return (and clear) files a node wrote in this command at/after ``since_ts``.

    The executor calls this right after a node run to recover a deliverable
    when the node's final TEXT was empty but it wrote a real file.
    """

    if not command_id:
        return []
    key = (org_id, command_id, node_id)
    entries = _NODE_FILE_OUTPUTS.get(key)
    if not entries:
        return []
    kept: list[tuple[str, float, str]] = []
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, ts, tool in entries:
        if ts >= since_ts:
            if path not in seen:
                seen.add(path)
                size = None
                try:
                    size = Path(path).stat().st_size
                except OSError:
                    pass
                matched.append({"path": path, "ts": ts, "tool_name": tool, "size_bytes": size})
        else:
            kept.append((path, ts, tool))
    if kept:
        _NODE_FILE_OUTPUTS[key] = kept
    else:
        _NODE_FILE_OUTPUTS.pop(key, None)
    return matched

# ── Org-node write sandbox (isolation guard) ──────────────────────────────
# Org node agents share the desktop Agent's FileTool, whose ``_resolve_path``
# returns absolute paths verbatim and resolves relative paths under CWD (= the
# repo root in a source run). A node that wrote a relative path such as
# ``src/openakita/orgs/tool_handler.py`` therefore landed INSIDE the source
# tree — exactly the "stray tool_handler.py" pollution incident. Deliverables
# are already auto-persisted to ``data/orgs/<id>/artifacts/`` by the executor,
# so org nodes never have a legitimate reason to write into the project's own
# source/config tree. This guard rejects write-class tool calls whose target
# resolves into the OpenAkita source tree (or the VCS dir), anchored on the
# real package location so it holds regardless of CWD. It deliberately does
# NOT restrict writes to ``data/`` or to user-chosen output paths.

# tool_name -> list of arg keys that carry a writable destination path.
_WRITE_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "file_path"),
    "edit_file": ("path", "file_path"),
    "append_file": ("path", "file_path"),
    "create_file": ("path", "file_path"),
    "delete_file": ("path", "file_path"),
    "move_file": ("src", "dst", "source", "destination", "dest"),
    "copy_file": ("src", "dst", "source", "destination", "dest"),
    "rename_file": ("src", "dst", "source", "destination", "dest"),
    "create_directory": ("path", "dir_path"),
}


@lru_cache(maxsize=1)
def _guarded_source_roots() -> tuple[Path, ...]:
    """Absolute dirs an org node must never write into.

    Anchored on ``openakita.__file__`` so the source tree is protected even
    when the process CWD differs from the repo root.
    """
    try:
        import openakita

        pkg_dir = Path(openakita.__file__).resolve().parent  # .../src/openakita
        src_dir = pkg_dir.parent  # .../src
        repo_root = src_dir.parent  # repo root
        roots = [pkg_dir, repo_root / ".git", repo_root / "apps", repo_root / "tests"]
        return tuple(r for r in roots if r)
    except Exception:  # noqa: BLE001 -- never let the guard import break a run
        return ()


# tool_name -> destination arg keys whose RELATIVE values get redirected into
# the org's artifacts dir. Deliberately excludes ``src``/``source`` (read
# sources for move/copy must not be relocated) and ``delete_file`` (relocating a
# delete target would silently change semantics; the source-tree guard still
# protects it). Absolute paths are never redirected here — they fall through to
# :func:`_guarded_write_violation`.
_WRITE_DEST_KEYS: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "file_path"),
    "edit_file": ("path", "file_path"),
    "append_file": ("path", "file_path"),
    "create_file": ("path", "file_path"),
    "move_file": ("dst", "destination", "dest"),
    "copy_file": ("dst", "destination", "dest"),
    "rename_file": ("dst", "destination", "dest"),
    "create_directory": ("path", "dir_path"),
}


def _org_artifacts_dir(org_id: str) -> Path | None:
    """Resolve ``data/orgs/<org_id>/artifacts`` (the org-scoped output dir).

    Reuses the artifacts module's resolver so this matches exactly where the
    executor auto-persists node deliverables (download paths stay consistent).
    """
    try:
        from ._runtime_node_artifacts import _resolve_org_dir, safe_path_segment

        org_dir = _resolve_org_dir(None, org_id)
        if org_dir is None:
            return None
        # Anchor under the same per-org tree; ``safe_path_segment`` already
        # ran inside _resolve_org_dir for the fallback path.
        _ = safe_path_segment  # imported for parity / future use
        return org_dir / "artifacts"
    except Exception:  # noqa: BLE001 -- never let redirect break a run
        return None


def _command_workspace_dir(org_id: str, command_id: str | None) -> Path | None:
    """Resolve the per-COMMAND artifacts sandbox for node file tools.

    Exploratory v22 (theme-drift root cause): the contamination vector was a
    node's ``list_directory`` / ``read_file`` discovering a PRIOR command's
    on-disk deliverables (e.g. an old 《剑来》报告 left in the org workspace)
    and anchoring the new 《凡人修仙传》task on it. We give every command its
    OWN artifacts dir at ``data/orgs/<id>/commands/<command_id>/artifacts`` and
    sandbox BOTH reads and writes there, so a fresh command opens an empty
    workspace and cannot see another command's files. Same-command upstream
    outputs still reflow downstream because (a) they are inlined into the child
    prompt by the agent builder and (b) any tool-written file lands in this same
    per-command dir, readable by later same-command nodes.

    Falls back to the org-level ``artifacts`` dir when ``command_id`` is missing
    (legacy / unit-test contexts) so non-command tool calls keep their old
    behaviour byte-for-byte.
    """
    try:
        from ._runtime_node_artifacts import _resolve_org_dir, safe_path_segment

        org_dir = _resolve_org_dir(None, org_id)
        if org_dir is None:
            return None
        cmd = (command_id or "").strip()
        if cmd:
            safe_cmd = safe_path_segment(cmd, fallback="_cmd")
            return org_dir / "commands" / safe_cmd / "artifacts"
        return org_dir / "artifacts"
    except Exception:  # noqa: BLE001 -- never let sandbox resolution break a run
        return None


def _clamp_into(root: Path, raw: str) -> Path:
    """Resolve ``raw`` (relative) under ``root``, clamping ``..`` escapes.

    Mirrors the write-redirect clamp: a relative path that would escape ``root``
    via ``..`` is collapsed to ``<root>/<basename>`` so a node can never
    traverse out of its sandbox.
    """
    root_res = root.resolve()
    candidate = (root_res / Path(raw)).resolve()
    if candidate != root_res and not candidate.is_relative_to(root_res):
        candidate = (root_res / Path(raw).name).resolve()
    return candidate


def _redirect_relative_writes(
    tool_name: str,
    tool_input: dict[str, Any],
    org_id: str,
    command_id: str | None = None,
) -> list[tuple[str, str]]:
    """Rewrite RELATIVE write destinations to live under the per-command dir.

    Mutates ``tool_input`` in place. Returns a list of ``(original, rewritten)``
    pairs for logging/transparency. Absolute paths are left untouched (the
    source-tree guard handles them). Any ``..`` that would escape the artifacts
    dir is clamped to the artifacts root using just the basename, so a node can
    never traverse out of its sandbox via a relative path.

    The destination is the per-COMMAND workspace (see
    :func:`_command_workspace_dir`); when ``command_id`` is ``None`` it falls
    back to the org-level artifacts dir for backward compatibility.
    """
    keys = _WRITE_DEST_KEYS.get(tool_name)
    if not keys or not isinstance(tool_input, dict):
        return []
    artifacts = _command_workspace_dir(org_id, command_id)
    if artifacts is None:
        return []
    rewrites: list[tuple[str, str]] = []
    for key in keys:
        raw = tool_input.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            p = Path(raw)
            if p.is_absolute():
                continue  # absolute paths handled by the source-tree guard
            artifacts_root = artifacts.resolve()
            candidate = _clamp_into(artifacts_root, raw)
            artifacts_root.mkdir(parents=True, exist_ok=True)
            tool_input[key] = str(candidate)
            rewrites.append((raw, str(candidate)))
        except Exception:  # noqa: BLE001 -- a malformed path can't be redirected
            continue
    return rewrites


# Read-class tools whose RELATIVE path arg gets sandboxed into the per-command
# workspace. Sandboxing READS (not just writes) is what actually fixes the
# cross-command theme-drift: a node's ``list_directory(".")`` / ``read_file``
# can no longer reach a prior command's stale deliverables.
_READ_SRC_KEYS: dict[str, tuple[str, ...]] = {
    "read_file": ("path", "file_path"),
    "read_text_file": ("path", "file_path"),
    "read_multiple_files": ("path", "file_path"),
    "list_directory": ("path", "dir_path"),
    "list_dir": ("path", "dir_path"),
    "directory_tree": ("path", "dir_path"),
    "search_files": ("path", "dir_path", "root"),
    "glob": ("path", "dir_path", "root"),
    "grep": ("path", "dir_path", "root"),
}

# Path aliases that mean "the workspace root listing" rather than a named file.
_READ_ROOT_ALIASES: frozenset[str] = frozenset({"", ".", "./", "/"})


def _redirect_relative_reads(
    tool_name: str,
    tool_input: dict[str, Any],
    org_id: str,
    command_id: str | None = None,
) -> list[tuple[str, str]]:
    """Sandbox RELATIVE read paths into the per-command workspace.

    Mutates ``tool_input`` in place; returns ``(original, rewritten)`` pairs.
    A bare ``.`` (workspace-root listing) resolves to the per-command dir
    itself, so ``list_directory(".")`` shows ONLY the current command's files.
    Absolute paths are left untouched (a node cannot auto-discover another
    command's absolute path because directory listing is itself sandboxed).
    No-op when ``command_id`` is missing (legacy/test parity).
    """
    keys = _READ_SRC_KEYS.get(tool_name)
    if not keys or not isinstance(tool_input, dict):
        return []
    # Only sandbox when we actually have a per-command dir; with no command_id
    # the workspace resolves to the org artifacts dir and we keep the old
    # (un-sandboxed) read behaviour to avoid disturbing legacy/tests.
    if not (command_id or "").strip():
        return []
    sandbox = _command_workspace_dir(org_id, command_id)
    if sandbox is None:
        return []
    rewrites: list[tuple[str, str]] = []
    sandbox_root = sandbox.resolve()
    try:
        sandbox_root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 -- mkdir best-effort
        pass
    for key in keys:
        raw = tool_input.get(key)
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if s in _READ_ROOT_ALIASES:
            tool_input[key] = str(sandbox_root)
            rewrites.append((raw or ".", str(sandbox_root)))
            continue
        try:
            if Path(s).is_absolute():
                continue  # absolute reads cannot be auto-discovered (list sandboxed)
            candidate = _clamp_into(sandbox_root, s)
            tool_input[key] = str(candidate)
            rewrites.append((raw, str(candidate)))
        except Exception:  # noqa: BLE001 -- a malformed path can't be redirected
            continue
    return rewrites


def _guarded_write_violation(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """Return a rejection message if this write escapes into the source tree."""
    keys = _WRITE_PATH_KEYS.get(tool_name)
    if not keys or not isinstance(tool_input, Mapping):
        return None
    roots = _guarded_source_roots()
    if not roots:
        return None
    base = Path.cwd()
    for key in keys:
        raw = tool_input.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            p = Path(raw)
            resolved = (p if p.is_absolute() else base / p).resolve()
        except Exception:  # noqa: BLE001 -- a malformed path can't be validated
            continue
        for root in roots:
            try:
                if resolved == root or resolved.is_relative_to(root):
                    return (
                        f"[拒绝写入：{tool_name} 的目标路径 '{raw}' 落在 OpenAkita 工程源码"
                        f"目录内（{root.name}）。节点产出请写入 data/ 工作区或交付目录，"
                        f"不要写入工程源码树。]"
                    )
            except Exception:  # noqa: BLE001 -- is_relative_to edge cases
                continue
    return None

__all__ = [
    "MAX_SEARCH_CALLS",
    "MAX_TOOL_CALLS",
    "MAX_TOOL_ROUNDS",
    "NodeToolEmit",
    "NodeToolHostProvider",
    "execute_node_tool",
    "extract_tool_use_blocks",
    "resolve_node_tools",
    "run_with_tools",
]


_LOGGER = logging.getLogger(__name__)


# Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): per-node-agent callable
# that returns the currently-bound :class:`NodeToolHost` (or ``None``
# when the desktop Agent is not yet wired). We use a provider closure
# rather than a direct reference because :class:`DefaultAgentBuilder`
# is constructed inside the FastAPI lifespan *before* the host can
# exist (``app.state.agent`` is populated later by ``main.py``), so
# the closure picks the host up on first node activation -- mirrors
# the Sprint-2 ``brain_provider`` rationale.
NodeToolHostProvider = Callable[[], "NodeToolHost | None"]


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    """Read a bounded int from the environment (clamped to ``[lo, hi]``)."""
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


MAX_TOOL_ROUNDS = _env_int("OPENAKITA_ORG_MAX_TOOL_ROUNDS", 6, lo=1, hi=12)
"""Cap on tool-call ROUNDS per node activation (one round == one
``tool_use`` -> ``tool_result`` -> next LLM call cycle).

Quality root-fix (test7 RCA, 2026-06): the Sprint-5 ``= 1`` bound made
:func:`run_with_tools` return the LLM's *second* response verbatim even
when that response was still mid-reasoning ("搜索结果不理想，让我再搜一次")
or another ``tool_use`` request. The node therefore "delivered" its raw
chain-of-thought (76-413B ``thinking…`` artifacts) instead of doing the
work. A bounded ReAct loop lets a node iterate (search -> search again ->
write) until it produces a real answer.

The roadmap (``docs/follow-ups/skipped-items-roadmap.md``) gated this
behind "validate per-org behaviour under the node's tool budget": we
ship the loop together with :data:`MAX_TOOL_CALLS` (a hard per-activation
tool-execution budget) and validate token/round counts on test7. The
bound stays env-overridable so an operator can dial it back to ``1``
(byte-for-byte Sprint-5 behaviour) without a code change."""


MAX_TOOL_CALLS = _env_int("OPENAKITA_ORG_MAX_TOOL_CALLS", 16, lo=1, hi=40)
"""Hard budget on TOTAL tool executions per node activation, independent
of rounds (a single round may emit several ``tool_use`` blocks). Once the
budget is spent the loop forces ONE final tool-less LLM call so the node
still returns a clean text answer instead of leaking another tool_use /
thinking turn. This is the cost guard the roadmap required before
lifting :data:`MAX_TOOL_ROUNDS` above 1."""


MAX_LLM_CALL_S = _env_int("OPENAKITA_ORG_NODE_LLM_CALL_TIMEOUT_S", 240, lo=0, hi=1800)
"""Per-brain-call wall-clock cap inside the node ReAct loop (seconds).

test13 RCA fix (a).2: the leaf node activation cap
(:data:`_runtime_agent_pipeline_executor._NODE_TIMEOUT_DEFAULT` = 420s) is
calibrated to the WORST-CASE HEALTHY leaf (up to :data:`MAX_TOOL_ROUNDS` rounds,
each an LLM call + a possibly-slow ``web_fetch``), so lowering it globally would
kill legitimate slow-but-progressing research leaves. writer-b did NOT exhaust
that healthy budget -- a SINGLE ``messages_create_async`` hung after the
truncated ``write_file`` and ate the whole remaining node budget. This cap is the
precise "no-progress" backstop: it bounds ONE brain call so a hung provider call
fails the node fast (≈4min) and returns budget to the root finalization, without
touching the healthy multi-round path. Generous by design (a real single node
call never legitimately runs this long); ``0`` disables it and env overrides it."""


MAX_SEARCH_CALLS = _env_int("OPENAKITA_ORG_MAX_SEARCH_CALLS", 6, lo=1, hi=40)
"""Per-activation cap on *retrieval* tool calls (``web_search`` /
``news_search``).

Efficiency root-fix (adversarial re-test, 2026-06): a research/analysis
node (e.g. ``data_analyst``) would burn its WHOLE :data:`MAX_TOOL_CALLS`
budget firing back-to-back searches and then return an empty deliverable
— and because it never wrote anything up, the parent gate reworked it,
which re-activated it for ANOTHER ~16 searches. Across a few rework
rounds that compounded into ~50 searches with no written output.

Capping retrieval calls specifically (instead of just lowering the
global tool budget, which would also starve legitimate ``write_file`` /
``read_file`` work) forces the node off the search treadmill: once it
has made :data:`MAX_SEARCH_CALLS` searches in one activation, further
search calls short-circuit with a "够用即止 — stop searching and write up
now" instruction so the node spends its remaining budget producing the
deliverable. Env-overridable so an operator can widen it for genuinely
search-heavy orgs without a code change."""


# Retrieval tools the per-activation search cap applies to. File / memory /
# planning tools are intentionally NOT here — the cap is about wasteful
# *external retrieval* spinning, not about writing the deliverable.
_SEARCH_TOOL_NAMES: frozenset[str] = frozenset({"web_search", "news_search"})


_SEARCH_BUDGET_NOTE = (
    "[检索次数已达本轮上限（{cap} 次）。请立即停止继续检索，"
    "直接基于【已经检索到的信息】完成当前任务并成文交付："
    "把完整结论写清楚（需要落盘的用 write_file/append_file 写成文件），"
    "不要再请求 web_search/news_search。若个别数据仍缺失，可在产出中"
    "明确标注‘该项暂未联网核实’，但仍要给出完整、成文的成果。]"
)


_FINALIZE_DIRECTIVE = (
    "[你已用完本轮工具预算。现在必须基于上面已经获得的工具结果，"
    "直接输出完整、成文的最终成果（不要再请求任何工具、不要只回复一句"
    "状态或‘正在整理’）。如有未核实项可简短标注，但主体内容必须完整成文。]"
)


# Best-effort emitter signature: ``(event_name, payload_dict) -> Awaitable[None]``.
# We tolerate sync callables too (in case a test fixture passes a plain
# ``MagicMock`` or a wrapper that captures events into a list); the
# ``_safe_emit`` helper below handles both shapes.
NodeToolEmit = Callable[[str, dict[str, Any]], Any]


def _flatten_external_tools(entries: Iterable[str] | None) -> set[str]:
    """Expand category names (``research`` etc) to concrete tool names.

    Mirrors :func:`openakita.orgs._runtime_tool_categories.expand_tool_categories`
    so the orgs_v2 node path consumes the exact same whitelist semantics
    the main agent's ``Agent._effective_tools`` (v1) reaches via
    ``expand_tool_categories`` inside ``agents/factory.py``. Importing
    lazily keeps the bootstrap cycle (orgs <-> orgs._runtime_tool_categories
    <-> orgs._default_agent_builder) tight.
    """

    if not entries:
        return set()
    from ._runtime_tool_categories import expand_tool_categories

    return expand_tool_categories(list(entries))


def resolve_node_tools(
    *,
    external_tools: Iterable[str] | None,
    enable_file_tools: bool = True,
    tool_host: NodeToolHost | None = None,
) -> list[dict[str, Any]]:
    """Translate a v1-style ``external_tools`` whitelist into LLM tool dicts.

    ``enable_file_tools`` mirrors the :class:`OrgNode` flag: when ``True``
    (the v1 default), the four "basic file tools" (``write_file``,
    ``read_file``, ``edit_file``, ``list_directory``) are auto-merged in
    so non-filesystem-explicit roles can still drop deliverables. The
    aigc-video-studio template **disables** this for workbench nodes
    (``wb-hh-*``) so they only have the explicit ``hh_*`` whitelist.

    Sprint-6 P0-3 (RCA ``_v17_p1_rca.md`` §4 P0-3): when ``tool_host``
    is supplied the resolver also looks up plugin-provided
    definitions (``hh_image_create`` etc) via the host's tool catalog
    -- the plugin API extends ``agent._tools`` with their
    Anthropic-shape definitions, so the workbench ``wb-hh-*`` nodes
    finally see their declared external tools instead of having them
    silently dropped (Sprint-5 §3 P0-3 "out of scope" note).

    Tools unknown to **both** the host and :func:`get_tool_definition`
    are dropped with a debug log -- preserves Sprint-5 behaviour for
    bare-builder fixtures that never set up a host.
    """

    flat = _flatten_external_tools(external_tools)
    if enable_file_tools:
        flat.update({"write_file", "read_file", "edit_file", "append_file", "list_directory"})

    # Lazy import: tools/definitions/ imports a large module graph
    # (browser / mcp / web_fetch) we do not want at orgs_v2 import time.
    from openakita.tools.definitions import get_tool_definition

    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped: list[str] = []
    for name in sorted(flat):
        if name in seen:
            continue
        seen.add(name)
        defn: dict[str, Any] | None = None
        # Sprint-6 P0-3: prefer the host's lookup so plugin tools
        # (``hh_*``) are included. The host inspects the live
        # ``agent._tools`` list -- which is what ``plugins/api.py``
        # extends after each plugin registers -- so any tool the LLM
        # might legitimately call is reachable here.
        if tool_host is not None:
            try:
                defn = tool_host.lookup_tool_definition(name)
            except Exception:  # noqa: BLE001 -- best-effort
                defn = None
        if defn is None:
            static_defn = get_tool_definition(name)
            if static_defn is not None:
                defn = {
                    "name": static_defn.get("name", name),
                    "description": static_defn.get("description", ""),
                    "input_schema": static_defn.get("input_schema", {"type": "object"}),
                }
        if defn is None:
            dropped.append(name)
            continue
        # Brain.messages_create_async accepts the canonical Anthropic
        # shape ``{name, description, input_schema}``; copy only those
        # three keys so unrelated fields (``category``, ``examples``,
        # ``detail``) do not balloon the prompt budget.
        resolved.append(
            {
                "name": defn.get("name", name),
                "description": defn.get("description", ""),
                "input_schema": defn.get("input_schema", {"type": "object"}),
            }
        )
    if dropped:
        _LOGGER.debug(
            "[orgs_v2 node tools] dropped unknown tool names (likely "
            "plugin / workbench tools not yet wired): %s",
            sorted(dropped),
        )
    return resolved


def extract_tool_use_blocks(response: Any) -> list[dict[str, Any]]:
    """Pull ``tool_use`` blocks out of a Brain ``Message``-shaped response.

    Returns a list of ``{"id", "name", "input"}`` dicts in LLM-emit
    order. Mirrors the v1 ``_parse_decision`` walk in
    ``core/_reasoning_engine_legacy.py`` but stripped to just what the
    second-round prompt needs. Robust to both Anthropic SDK objects
    (attribute access) and provider-shim dicts (``isinstance(content,
    list)`` -> nested ``.type`` / ``.name`` lookups).
    """

    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for raw in content:
        if isinstance(raw, dict):
            btype = raw.get("type")
            if btype != "tool_use":
                continue
            blocks.append(
                {
                    "id": str(raw.get("id") or ""),
                    "name": str(raw.get("name") or ""),
                    "input": raw.get("input") or {},
                }
            )
            continue
        btype = getattr(raw, "type", None)
        if btype != "tool_use":
            continue
        blocks.append(
            {
                "id": str(getattr(raw, "id", "") or ""),
                "name": str(getattr(raw, "name", "") or ""),
                "input": getattr(raw, "input", {}) or {},
            }
        )
    return blocks


def _content_blocks_for_assistant(response: Any) -> list[dict[str, Any]]:
    """Re-serialise a Brain response into the assistant-turn ``content``
    list expected by :func:`Brain.messages_create_async` when we feed
    the conversation back for a second round.

    Anthropic requires that when you reply with a ``tool_result`` user
    message, the *prior* assistant turn must contain the original
    ``tool_use`` block(s). We rebuild that turn here from the response
    object verbatim (text blocks + tool_use blocks); any unknown block
    type is skipped so a provider returning extra metadata does not
    poison the second call.
    """

    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for raw in content:
        if isinstance(raw, dict):
            btype = raw.get("type")
            if btype == "text":
                txt = raw.get("text", "")
                if txt:
                    blocks.append({"type": "text", "text": str(txt)})
            elif btype == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(raw.get("id") or ""),
                        "name": str(raw.get("name") or ""),
                        "input": raw.get("input") or {},
                    }
                )
            continue
        btype = getattr(raw, "type", None)
        if btype == "text":
            txt = getattr(raw, "text", "")
            if txt:
                blocks.append({"type": "text", "text": str(txt)})
        elif btype == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(getattr(raw, "id", "") or ""),
                    "name": str(getattr(raw, "name", "") or ""),
                    "input": getattr(raw, "input", {}) or {},
                }
            )
    return blocks


async def _safe_emit(emit: NodeToolEmit | None, event: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget event emission with all exceptions swallowed.

    The orgs_v2 event bus emits return awaitables; some test fixtures
    pass a plain ``MagicMock`` whose call result is not awaitable. We
    accept either shape so the executor wiring stays liberal.
    """

    if emit is None:
        return
    try:
        result = emit(event, payload)
        if asyncio.iscoroutine(result):
            await result
    except asyncio.CancelledError:
        # Cancellation must propagate -- the surrounding node-agent
        # ``run`` is what owns the cancel pipeline. Drop our event
        # silently and let the parent ``raise`` happen.
        raise
    except Exception:  # noqa: BLE001 -- event emission must never block tool execution
        _LOGGER.debug("node tool event emission raised", exc_info=True)


async def execute_node_tool(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    org_id: str,
    node_id: str,
    command_id: str | None,
    emit: NodeToolEmit | None = None,
    tool_host: NodeToolHost | None = None,
) -> tuple[str, bool]:
    """Run one tool via :class:`NodeToolHost` (Sprint-6 P0-1) with safety net.

    Returns ``(text, is_error)``:

    * ``is_error=False`` -- the handler returned a string (or coerced
      result). We use it as the ``content`` of the ``tool_result``
      block sent back to the LLM.
    * ``is_error=True`` -- the handler raised or no handler was mapped.
      The error text is still surfaced (inline in ``tool_result.content``)
      so the LLM can decide how to proceed; this matches the v1
      :class:`ToolExecutor` policy (an unknown / failing tool returns a
      structured error string rather than blowing up the whole turn).

    Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): the host's
    ``handler_registry`` is the *populated* one from the desktop
    Agent (filesystem / memory / web_search / 20 system handlers +
    every plugin-registered tool). When ``tool_host`` is ``None`` we
    fall back to the Sprint-5 global registry path so headless test
    fixtures and the FastAPI lifespan-race window (host not yet
    wired) keep working -- the fallback will still emit
    ``node_tool_failed`` for unknown tools, byte-for-byte v17
    observable.

    Cancellation is propagated unchanged -- if the surrounding task is
    cancelled we re-raise :class:`asyncio.CancelledError` so the cancel
    pipeline (Sprint-3 P0-2) keeps working through tool execution.
    """

    # Command-scope sandbox: redirect RELATIVE write destinations into the
    # PER-COMMAND workspace BEFORE preview/exec so a bare filename like
    # ``jianlai_points.md`` lands in data/orgs/<id>/commands/<cmd>/artifacts/
    # instead of the process CWD (repo root). Absolute paths still hit the
    # source-tree guard.
    redirects = _redirect_relative_writes(tool_name, tool_input, org_id, command_id)
    if redirects:
        _LOGGER.info(
            "[node-tool] redirected %s relative write(s) into command workspace "
            "(org=%s node=%s cmd=%s): %s",
            tool_name,
            org_id,
            node_id,
            command_id,
            "; ".join(f"{o} -> {n}" for o, n in redirects),
        )

    # Command-scope sandbox for READS (exploratory v22 theme-drift fix): a
    # node's ``list_directory`` / ``read_file`` is confined to THIS command's
    # workspace so it can never discover and anchor on a PRIOR command's stale
    # deliverables (the 《剑来》→《凡人修仙传》contamination). Same-command
    # reflow is preserved (upstream output is inlined into the child prompt and
    # any tool-written file lives in this same per-command dir).
    read_redirects = _redirect_relative_reads(tool_name, tool_input, org_id, command_id)
    if read_redirects:
        _LOGGER.info(
            "[node-tool] sandboxed %s relative read(s) to command workspace "
            "(org=%s node=%s cmd=%s): %s",
            tool_name,
            org_id,
            node_id,
            command_id,
            "; ".join(f"{o} -> {n}" for o, n in read_redirects),
        )

    args_preview = ""
    if isinstance(tool_input, Mapping):
        try:
            import json as _json

            args_preview = _json.dumps(tool_input, ensure_ascii=False)[:200]
        except Exception:  # noqa: BLE001 -- preview is best-effort
            args_preview = repr(tool_input)[:200]
    await _safe_emit(
        emit,
        "node_tool_called",
        {
            "org_id": org_id,
            "node_id": node_id,
            "command_id": command_id,
            "tool_name": tool_name,
            "args_preview": args_preview,
        },
    )

    # test13 fix (a): the LLM's tool-call argument JSON was truncated by the
    # provider, so ``convert_tool_calls_from_openai`` injected ``__parse_error__``
    # into the input instead of the real ``path`` / ``content``. Running the
    # handler with that broken input silently mis-executes (writer-b's病根: the
    # write never happened, the node then ground on to the 420s hard timeout).
    # Short-circuit HERE with a crisp, actionable directive so the model
    # self-corrects WITHIN its ReAct budget -- switch to chunked append_file /
    # smaller single-write bodies -- instead of hanging. Surfaced as an error
    # tool_result (is_error=True) so the model treats it as a failed call to fix.
    from openakita.llm.converters.tools import PARSE_ERROR_KEY

    if isinstance(tool_input, Mapping) and PARSE_ERROR_KEY in tool_input:
        original = str(tool_input.get(PARSE_ERROR_KEY) or "").strip()
        directive = (
            f"[工具 `{tool_name}` 调用失败：参数 JSON 被模型输出/接口截断，未能解析出有效参数"
            "（如 path/content 丢失或不完整），本次调用未执行、文件未写入。\n"
            "请立即改用【分段小写入】策略，不要重复整段大参数调用：\n"
            "1) 先用 write_file 写入开头部分（单次 content 控制在 6000 字符以内）；\n"
            "2) 再用 append_file 多次续写后续内容（每次 content 同样 < 6000 字符），"
            "直到全文写完；\n"
            "3) 切勿把超长正文一次性塞进单个工具参数，否则会再次被截断。]"
        )
        if original:
            directive = f"{directive}\n[底层解析器提示]{original}"
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "arg_parse_truncated",
            },
        )
        _LOGGER.warning(
            "[node-tool] fast-failed %s: truncated arg JSON (__parse_error__) "
            "(org=%s node=%s cmd=%s) -- returning append_file directive",
            tool_name,
            org_id,
            node_id,
            command_id,
        )
        return (directive, True)

    # Isolation guard: block write-class tools whose target escapes into the
    # OpenAkita source tree (the "stray tool_handler.py" pollution incident).
    violation = _guarded_write_violation(tool_name, tool_input)
    if violation is not None:
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "write_path_blocked",
            },
        )
        _LOGGER.warning(
            "[node-tool] blocked source-tree write org=%s node=%s tool=%s",
            org_id,
            node_id,
            tool_name,
        )
        return (violation, True)

    # Lazy import: the host module pulls a small graph but the
    # exception class is hashable so a late import keeps the orgs_v2
    # package import-time light.
    from openakita.tools.tool_hints import ToolConfigError

    from ._runtime_agent_host import ToolNotAvailable

    try:
        if tool_host is not None:
            result = await tool_host.execute_tool(
                tool_name,
                dict(tool_input),
                node_id=node_id,
                command_id=command_id,
            )
        else:
            # Sprint-5 fallback path (RCA §1.5.4 rollback): the global
            # registry stays empty in production, so the lookup will
            # raise ``ValueError`` and we surface it as the same
            # ``node_tool_failed`` payload v17 observed. This branch
            # only fires in test fixtures that monkeypatch
            # ``default_handler_registry.execute_by_tool``.
            from openakita.tools.handlers import default_handler_registry

            result = await default_handler_registry.execute_by_tool(
                tool_name, dict(tool_input)
            )
    except asyncio.CancelledError:
        # User cancel must propagate to the outer node-agent run so the
        # outcome cache resolves to ``cancelled`` instead of failing
        # this tool as an error.
        raise
    except ToolNotAvailable as exc:
        # Sprint-6 P0-3: classify "plugin tool not loaded" distinctly
        # from a generic handler crash so events.jsonl readers can
        # tell whether ``hh_*`` failed because the plugin manifest
        # is missing vs the API is down. The Sprint-5 path turned
        # both into ``error="No handler mapped for tool: <name>"``.
        _LOGGER.warning(
            "[orgs_v2 node tool] %s.%s unavailable: %s",
            node_id,
            tool_name,
            exc.reason,
        )
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "plugin_not_loaded",
                "error": exc.reason,
            },
        )
        return (
            f"[tool {tool_name} unavailable: {exc.reason}]",
            True,
        )
    except ToolConfigError as exc:
        # Reliability fix (2026-06): a tool that needs USER-side configuration
        # (e.g. web_search with no working provider / an expired jina key) can
        # NOT be fixed by the node mid-run. The old path let this fall into the
        # generic ``handler_raised`` branch, which (a)炸节点 as a tool failure and
        # (b) made the node spin re-calling the same broken tool until it ran out
        # of budget with near-empty output. Instead we degrade GRACEFULLY: emit a
        # distinct ``config_unavailable`` failed-event for the blackboard/anomaly
        # log, but return a NON-error result that tells the node to proceed with
        # what it has and NOT retry. This mirrors the network-degraded path.
        hint = getattr(exc, "hint", None)
        reason_msg = exc.to_llm_text() if hasattr(exc, "to_llm_text") else str(exc)
        _LOGGER.warning(
            "[orgs_v2 node tool] %s.%s config-unavailable, degrading: %s",
            node_id,
            tool_name,
            reason_msg,
        )
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "config_unavailable",
                "error_code": getattr(hint, "error_code", "unknown"),
                "error": reason_msg,
            },
        )
        degraded = (
            f"[{tool_name} 暂不可用：{reason_msg} "
            "请基于已掌握的信息继续完成当前任务，不要反复重试该工具；"
            "如果证据不足，可在产出中明确标注哪些内容尚未联网核实，"
            "但仍要给出完整、成文的成果，不要只回复一句状态说明。]"
        )
        return (degraded, False)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "[orgs_v2 node tool] %s.%s raised: %s",
            node_id,
            tool_name,
            exc,
        )
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "handler_raised",
                "error": str(exc),
            },
        )
        return (f"[tool {tool_name} failed: {exc}]", True)

    text = result if isinstance(result, str) else str(result)
    # Reliability (v23): strip explicit-adult entries from retrieval results
    # BEFORE they re-enter the LLM prompt, so a noisy duckduckgo hit can't trip
    # the cloud model's content-moderation gate (data_inspection_failed) and
    # fail the whole node. Also improves relevance for the org content team.
    text, _nsfw_dropped = _sanitize_retrieval_result(tool_name, text)
    if _nsfw_dropped:
        _LOGGER.info(
            "[node-tool] sanitized %d adult/irrelevant line(s) from %s result "
            "(org=%s node=%s cmd=%s)",
            _nsfw_dropped,
            tool_name,
            org_id,
            node_id,
            command_id,
        )
    # test11 P1/P2: a write/deliver tool just succeeded -> record the on-disk
    # deliverable and emit ``file_output_registered`` so (a) the command-center
    # delivery cards populate live for EVERY write path (not just the final PDF)
    # and (b) the executor can recover a node whose final TEXT was empty but
    # which actually produced a file.
    for _wpath in _extract_written_paths(tool_name, tool_input):
        _size = record_node_file_output(org_id, command_id, node_id, _wpath, tool_name)
        if _size is None:
            continue
        await _safe_emit(
            emit,
            "file_output_registered",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "path": _wpath,
                "size_bytes": _size,
            },
        )

    # UI 留痕: carry a bounded preview of the tool RESULT (not just the char
    # count) so the command center can let the user expand "返回 N 字" into an
    # actual content summary. Collapse whitespace + cap so the event stays
    # small on the SSE/WS channel.
    result_preview = " ".join(text.split())[:500]
    await _safe_emit(
        emit,
        "node_tool_completed",
        {
            "org_id": org_id,
            "node_id": node_id,
            "command_id": command_id,
            "tool_name": tool_name,
            "result_len": len(text),
            "result_preview": result_preview,
        },
    )
    return (text, False)


async def run_with_tools(
    *,
    brain: Any,
    system_prompt: str,
    user_content: str,
    tools: list[dict[str, Any]],
    org_id: str,
    node_id: str,
    command_id: str | None,
    emit: NodeToolEmit | None = None,
    second_round_caller: Callable[[list[dict[str, Any]]], Awaitable[Any]] | None = None,
    tool_host: NodeToolHost | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[Any, int]:
    """Bounded multi-round ReAct loop on :meth:`Brain.messages_create_async`.

    Returns ``(final_response, tool_rounds)`` where ``tool_rounds`` is
    how many tool-use rounds ran (0 when the first response was already
    a final answer). ``final_response`` is the last
    ``messages_create_async`` result so the caller can extract text +
    attribute it to events / artefacts unchanged.

    Loop semantics (quality root-fix, test7 RCA 2026-06):

    * Each iteration calls the brain, extracts ``tool_use`` blocks and,
      if there are none, returns the response as the FINAL answer.
    * When the LLM emits ``tool_use`` we run each tool sequentially,
      splice the ``tool_result`` blocks back in, and loop -- so the node
      can iterate (search -> search again -> write) instead of being
      forced to "finalise" after a single tool call (which is what made
      it deliver raw ``thinking…`` mid-reasoning).
    * The loop is bounded by :data:`MAX_TOOL_ROUNDS` rounds AND
      :data:`MAX_TOOL_CALLS` total tool executions. When either budget is
      exhausted we make ONE last call with ``tools=[]`` so the LLM is
      forced to emit a clean text answer instead of another ``tool_use``
      turn that we would otherwise return verbatim.

    The ``second_round_caller`` parameter is a test hook: when given it
    is used for every brain call AFTER the first, so existing one-round
    fixtures keep asserting on the spliced ``tool_result`` history.

    Sprint-13 H1 (RC-4 §6 H1): ``cancel_event`` is forwarded to every
    ``brain.messages_create_async`` call so :meth:`LLMClient._race_with_cancel`
    can abort the in-flight ``httpx`` request the moment a user cancel
    fires.
    """

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

    async def _call_brain(turn_messages: list[dict[str, Any]], *, with_tools: bool) -> Any:
        # Subsequent turns prefer the test hook so fixtures can capture
        # the spliced tool_result history. The hook ignores the
        # tools/system kwargs (it stubs the LLM entirely) and is never
        # time-boxed (it is a synchronous stub, not a live provider call).
        if second_round_caller is not None and len(turn_messages) > 1:
            return await second_round_caller(turn_messages)
        call = brain.messages_create_async(
            messages=turn_messages,
            system=system_prompt,
            tools=tools if with_tools else [],
            cancel_event=cancel_event,
        )
        # test13 fix (a).2: bound ONE brain call so a hung provider request
        # cannot silently consume the whole node activation budget. On timeout
        # we raise a plain RuntimeError (NOT TimeoutError, which the executor
        # would mis-attribute to the node-level activation cap) so the executor
        # surfaces an accurate ``agent_run_failed`` and moves on fast.
        if MAX_LLM_CALL_S <= 0:
            return await call
        try:
            return await asyncio.wait_for(call, timeout=MAX_LLM_CALL_S)
        except TimeoutError as exc:
            _LOGGER.warning(
                "[node-tool] brain call exceeded %ss (org=%s node=%s cmd=%s); "
                "failing node fast to free budget",
                MAX_LLM_CALL_S,
                org_id,
                node_id,
                command_id,
            )
            raise RuntimeError(
                f"node LLM call exceeded {MAX_LLM_CALL_S}s (likely a hung provider "
                "request); failing this node so the org can continue"
            ) from exc

    rounds = 0
    total_tool_calls = 0
    search_calls = 0
    # First turn always offers tools (unless the caller passed none).
    response = await _call_brain(messages, with_tools=bool(tools))

    while True:
        tool_blocks = extract_tool_use_blocks(response) if tools else []
        if not tool_blocks:
            return response, rounds

        # Capture the LLM's tool_use turn verbatim (Anthropic requires the
        # prior assistant turn to contain the tool_use blocks the
        # following tool_result references).
        assistant_blocks = _content_blocks_for_assistant(response)
        if not assistant_blocks:
            assistant_blocks = [
                {
                    "type": "tool_use",
                    "id": block["id"],
                    "name": block["name"],
                    "input": block["input"],
                }
                for block in tool_blocks
            ]
        messages.append({"role": "assistant", "content": assistant_blocks})

        # Run each tool sequentially (deterministic ordering + trivial
        # cancellation propagation, matching the Sprint-4 child-dispatch
        # rationale).
        tool_results: list[dict[str, Any]] = []
        for block in tool_blocks:
            tool_name = block["name"]
            tool_input = block["input"] if isinstance(block["input"], dict) else {}
            # Efficiency cap: once a node has spent its per-activation
            # retrieval budget, short-circuit further search calls with a
            # "stop searching, write up now" note instead of executing them.
            # This is what stops a research node from burning its whole
            # budget on back-to-back searches and delivering nothing.
            if tool_name in _SEARCH_TOOL_NAMES and search_calls >= MAX_SEARCH_CALLS:
                _LOGGER.info(
                    "[node-tool] search budget reached (org=%s node=%s searches=%d); "
                    "short-circuiting %s and asking node to write up",
                    org_id,
                    node_id,
                    search_calls,
                    tool_name,
                )
                await _safe_emit(
                    emit,
                    "node_tool_failed",
                    {
                        "org_id": org_id,
                        "node_id": node_id,
                        "command_id": command_id,
                        "tool_name": tool_name,
                        "reason": "search_budget_reached",
                        "search_calls": search_calls,
                    },
                )
                text, is_error = (
                    _SEARCH_BUDGET_NOTE.format(cap=MAX_SEARCH_CALLS),
                    False,
                )
                total_tool_calls += 1
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": text,
                        "is_error": is_error,
                    }
                )
                continue
            text, is_error = await execute_node_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                org_id=org_id,
                node_id=node_id,
                command_id=command_id,
                emit=emit,
                tool_host=tool_host,
            )
            total_tool_calls += 1
            if tool_name in _SEARCH_TOOL_NAMES:
                search_calls += 1
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": text,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})
        rounds += 1

        budget_spent = rounds >= MAX_TOOL_ROUNDS or total_tool_calls >= MAX_TOOL_CALLS
        if budget_spent:
            _LOGGER.info(
                "[node-tool] tool budget reached (org=%s node=%s rounds=%d calls=%d); "
                "forcing final tool-less answer",
                org_id,
                node_id,
                rounds,
                total_tool_calls,
            )
            # Force a clean text answer: no tools on the last call so the
            # LLM cannot emit another tool_use we would return as "output".
            # We also splice an explicit "write it up now" directive into the
            # last user turn so the forced call produces a real deliverable
            # instead of another "let me search again" / thinking stub (the
            # empty-output-after-budget bug from the adversarial re-test).
            last = messages[-1]
            if isinstance(last.get("content"), list):
                last["content"].append({"type": "text", "text": _FINALIZE_DIRECTIVE})
            else:
                messages.append(
                    {"role": "user", "content": [{"type": "text", "text": _FINALIZE_DIRECTIVE}]}
                )
            return await _call_brain(messages, with_tools=False), rounds

        response = await _call_brain(messages, with_tools=True)
