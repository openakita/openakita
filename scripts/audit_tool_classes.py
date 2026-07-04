"""Audit plugin ``tool_classes`` coverage and suggest missing classifications.

CLI tool for §A.1 (Plugin tool_classes Phase 2 — incremental backfill) of
``docs/follow-ups/skipped-items-roadmap.md``. Scans every plugin manifest
under the standard plugin roots and prints / writes a per-tool
classification suggestion plus a coverage report.

Why this lives in ``scripts/`` and not in ``src/openakita/``:

  * It is a developer / maintainer tool, not used at runtime.
  * ``pyproject.toml`` only packages ``src/openakita`` into the wheel, so
    landing it here keeps the wheel slim.
  * CI may invoke it in audit mode (`--all --format table`) but must
    NEVER invoke it with ``--apply`` automatically.

Heuristics (in priority order):

  1. NAME tokens             — strongest signal (matches RCA v11 §2.5).
  2. INPUT-SCHEMA properties — boost destructive / network_out when the
                              tool accepts ``path`` / ``file`` / ``url`` /
                              ``command`` parameters.
  3. DESCRIPTION keywords    — boost / refine using verbs like
                              ``calls API``, ``writes to``, ``deletes``.

Confidence:

  * ``high``    — multiple corroborating signals or a clear destructive /
                  read-only name token.
  * ``medium``  — one strong signal (name or schema).
  * ``low``     — only a weak hint.
  * ``unknown`` — no signal; class set to ``unknown`` (safety-by-default
                  fallback) — MUST be human-classified.

Only ``high`` suggestions are written when ``--apply`` is passed.
Everything else lands in the markdown patch report so a human can
review.

Cross-refs:
  * ``docs/follow-ups/skipped-items-roadmap.md`` §A.1
  * ``docs/plugin-tool-classes-howto.md``
  * ``_skip_items_rca_v11.md`` §2.2, §2.5

Usage examples (run from repo root)::

    .venv\\Scripts\\python.exe scripts\\audit_tool_classes.py --all --format table
    .venv\\Scripts\\python.exe scripts\\audit_tool_classes.py --plugin fin-pulse --format json
    .venv\\Scripts\\python.exe scripts\\audit_tool_classes.py --plugin fin-pulse --apply
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Plugin manifest roots scanned by ``--all``. ``examples/plugins/`` is
# included so the docs samples stay covered too; ``dist-plugins/`` holds
# zipped artifacts, not raw manifests, so it is intentionally skipped.
_DEFAULT_PLUGIN_ROOTS: tuple[str, ...] = (
    "plugins",
    "plugins-archive",
    "examples/plugins",
)

# Markdown report destination. Created by the script on demand.
_DEFAULT_REPORT_PATH = "reports/plugin_tool_classes_audit.md"

# ApprovalClass canonical (lower-case) values — must match
# ``src/openakita/core/policy_v2/enums.py``. Imported lazily-by-string so
# the script can run without the openakita venv (handy for one-off
# audits on a fresh checkout).
_VALID_CLASSES: frozenset[str] = frozenset(
    {
        "readonly_scoped",
        "readonly_global",
        "readonly_search",
        "mutating_scoped",
        "mutating_global",
        "destructive",
        "exec_low_risk",
        "exec_capable",
        "control_plane",
        "interactive",
        "network_out",
        "unknown",
    }
)


# ---------------------------------------------------------------------------
# Heuristic tables
# ---------------------------------------------------------------------------

# Name token rules. The order matters: earlier rules win when multiple
# tokens hit. Each entry is (predicate, class, confidence_boost).
_NAME_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    # Destructive verbs are always high-confidence — safety first.
    (("delete", "remove", "uninstall", "drop", "purge"), "destructive", "high"),
    # Cancel / abort sits in control_plane (RCA §2.3 example).
    (("cancel", "abort", "halt"), "control_plane", "high"),
    # Interactive prompts — explicit confirm-style verbs.
    (("confirm", "approve", "ask_user"), "interactive", "high"),
    # Network out — when the tool name advertises media generation or
    # remote sync the host always assumes egress.
    (
        ("publish", "post_to", "dispatch_to", "send_to"),
        "network_out",
        "medium",
    ),
    # Read-only verbs.
    (
        ("status", "list", "get", "view", "read", "audit", "profile", "preview"),
        "readonly_scoped",
        "high",
    ),
    (("search", "find", "lookup"), "readonly_search", "high"),
    # Exec-style verbs.
    (("apply", "run", "execute", "dispatch", "trigger"), "exec_low_risk", "medium"),
    # Mutating verbs (catch-all).
    (
        (
            "create",
            "build",
            "generate",
            "render",
            "compose",
            "make",
            "upload",
            "import",
            "ingest",
            "start",
            "save",
            "update",
            "write",
            "edit",
            "patch",
        ),
        "mutating_scoped",
        "medium",
    ),
)

# Override: when a "create"-like verb collides with a media token we
# upgrade the suggestion to network_out, because all media generation
# in this codebase routes through external APIs (Volcengine, DashScope,
# OpenAI image, etc.). RCA v11 §2.3 mis-classified case.
_MEDIA_TOKENS: frozenset[str] = frozenset(
    {
        "image",
        "video",
        "audio",
        "speak",
        "tts",
        "asr",
        "translate",
        "subtitle",
        "voice",
        "dub",
        "reface",
        "relip",
    }
)

# Input-schema property name hints. Triggered when the tool definition
# has these named properties. Pushes the class towards destructive /
# network_out / exec_capable.
_INPUT_SCHEMA_HEURISTICS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("command", "shell", "argv"), "exec_capable", "medium"),
    (("url", "endpoint", "webhook", "callback"), "network_out", "medium"),
    (("path", "file", "filepath", "filename"), "mutating_scoped", "low"),
    (("regex_pattern", "match_pattern"), "readonly_search", "low"),
)

# Description keyword hints. Triggered when a tool description contains
# these phrases. Direction-only — adjusts the inferred class.
_DESCRIPTION_KEYWORDS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("calls api", "http", "fetch ", "request to", "remote"), "network_out", "medium"),
    (("writes to", "writes the", "persist", "save to"), "mutating_scoped", "medium"),
    (("deletes", "remove the", "uninstall"), "destructive", "high"),
    (("read-only", "returns the", "queries"), "readonly_scoped", "low"),
    (("schedule", "cron"), "control_plane", "medium"),
)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class ToolEntry:
    """One tool exposed by a plugin (name + best-effort metadata)."""

    name: str
    description: str = ""
    input_schema_properties: tuple[str, ...] = ()


@dataclass
class Suggestion:
    """A per-tool classification suggestion."""

    plugin_id: str
    tool: str
    current: str | None  # what the manifest already declares (if any)
    suggested: str
    confidence: str  # ``high`` / ``medium`` / ``low`` / ``unknown``
    evidence: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """True when the suggestion needs human review before applying."""
        return self.confidence != "high"


@dataclass
class PluginReport:
    plugin_id: str
    path: Path
    tools_declared: list[str]
    tool_classes: dict[str, str]
    suggestions: list[Suggestion]

    @property
    def coverage(self) -> float:
        if not self.tools_declared:
            return 1.0
        return len(self.tool_classes) / len(self.tools_declared)


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------


def _iter_plugin_dirs(repo_root: Path, roots: Iterable[str]) -> Iterable[Path]:
    for root_name in roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "plugin.json").is_file():
                yield child


def _read_plugin_manifest(plugin_dir: Path) -> dict[str, Any] | None:
    manifest_path = plugin_dir / "plugin.json"
    try:
        raw = manifest_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[warn] cannot read {manifest_path}: {exc}", file=sys.stderr)
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[warn] {manifest_path}: invalid JSON ({exc})", file=sys.stderr)
        return None


def _resolve_tools(manifest: dict[str, Any]) -> list[str]:
    provides = manifest.get("provides") or {}
    if not isinstance(provides, dict):
        return []
    tools = provides.get("tools") or []
    if not isinstance(tools, list):
        return []
    return [str(t) for t in tools if isinstance(t, str)]


def _resolve_tool_classes(manifest: dict[str, Any]) -> dict[str, str]:
    raw = manifest.get("tool_classes") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for name, klass in raw.items():
        if not isinstance(name, str) or not name:
            continue
        if klass is None:
            continue
        out[name] = str(klass).strip().lower()
    return out


# ---------------------------------------------------------------------------
# Optional: extract per-tool metadata (description + schema) from plugin.py
# ---------------------------------------------------------------------------


def _extract_tool_definitions(plugin_dir: Path) -> dict[str, ToolEntry]:
    """Best-effort static extraction of tool descriptions + input schema.

    Many plugins build their tool list inside ``plugin.py`` as a list of
    dict literals shaped like::

        {"type": "function", "function": {"name": ..., "description": ...,
         "parameters": {"properties": {...}}}}

    or, less commonly::

        {"name": ..., "description": ..., "input_schema": {...}}

    We parse ``plugin.py`` with :mod:`ast` (no execution) and pull out
    any dict literal that contains a string ``name`` key — that is the
    best static signal of a tool definition without running plugin
    code. If nothing parseable is found we silently return an empty
    dict and the heuristic falls back to name-only.
    """
    py_path = plugin_dir / "plugin.py"
    if not py_path.is_file():
        return {}
    try:
        source = py_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:
        print(f"[warn] {py_path}: cannot parse ({exc})", file=sys.stderr)
        return {}

    out: dict[str, ToolEntry] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        flat = _flatten_dict_literal(node)
        if not flat:
            continue
        name = flat.get("name") or flat.get("function.name")
        if not isinstance(name, str) or not name:
            continue
        description = (
            flat.get("description")
            or flat.get("function.description")
            or ""
        )
        if not isinstance(description, str):
            description = ""
        properties = (
            flat.get("parameters.properties")
            or flat.get("function.parameters.properties")
            or flat.get("input_schema.properties")
            or {}
        )
        if isinstance(properties, dict):
            prop_names = tuple(str(k) for k in properties if isinstance(k, str))
        else:
            prop_names = ()
        if name not in out:
            out[name] = ToolEntry(
                name=name,
                description=description,
                input_schema_properties=prop_names,
            )
    return out


def _flatten_dict_literal(node: ast.Dict, prefix: str = "") -> dict[str, Any] | None:
    """Flatten an ast.Dict to a ``key.path -> value`` map.

    Only string keys, string values, and nested dict literals are kept.
    For nested dicts we recurse with a ``parent.child`` prefix. Property
    names (the keys of ``properties: {...}``) are returned as a dict so
    callers can inspect their names.
    """
    out: dict[str, Any] = {}
    for k_node, v_node in zip(node.keys, node.values, strict=False):
        if not isinstance(k_node, ast.Constant) or not isinstance(k_node.value, str):
            continue
        key_path = f"{prefix}.{k_node.value}" if prefix else k_node.value
        if isinstance(v_node, ast.Constant):
            out[key_path] = v_node.value
        elif isinstance(v_node, ast.Dict):
            if k_node.value == "properties":
                prop_map: dict[str, Any] = {}
                for pk, _pv in zip(v_node.keys, v_node.values, strict=False):
                    if isinstance(pk, ast.Constant) and isinstance(pk.value, str):
                        prop_map[pk.value] = None
                out[key_path] = prop_map
            else:
                nested = _flatten_dict_literal(v_node, key_path)
                if nested:
                    out.update(nested)
    return out


# ---------------------------------------------------------------------------
# Heuristic engine
# ---------------------------------------------------------------------------


def _classify(tool: ToolEntry) -> Suggestion:
    name = tool.name.lower()
    evidence: list[str] = []
    candidates: list[tuple[str, str]] = []  # (class, confidence)

    # OpenAkita plugin tools follow ``<scope>_<scope>..._<verb>`` shape
    # (e.g. ``video_bg_remove_status``, ``fin_pulse_settings_get``).
    # The trailing 1-2 tokens carry the actual action — match those
    # first so that ``video_bg_remove_status`` is correctly tagged as
    # ``readonly_scoped`` rather than ``destructive`` just because the
    # plugin scope contains ``remove``. Fall back to substring match
    # only when the suffix is unrecognised, so historical names like
    # ``avatar_compose`` still resolve.
    name_tokens = name.split("_")
    tail = "_".join(name_tokens[-2:]) if len(name_tokens) >= 2 else name
    last_token = name_tokens[-1] if name_tokens else name

    suffix_candidates = (last_token, tail)
    for tokens, klass, confidence in _NAME_RULES:
        hit = next((tok for tok in tokens if tok in suffix_candidates), None)
        if hit is None:
            continue
        if klass == "mutating_scoped" and any(t in name for t in _MEDIA_TOKENS):
            evidence.append(f"suffix '{hit}' + media token → network_out")
            candidates.append(("network_out", "high"))
            break
        evidence.append(f"suffix '{hit}' → {klass} ({confidence})")
        candidates.append((klass, confidence))
        break

    if not candidates:
        # Fallback: substring match for legacy naming (e.g. ``avatar_compose``).
        for tokens, klass, confidence in _NAME_RULES:
            hit = next((tok for tok in tokens if tok in name), None)
            if hit is None:
                continue
            downgraded = "low" if confidence == "high" else confidence
            if klass == "mutating_scoped" and any(t in name for t in _MEDIA_TOKENS):
                evidence.append(
                    f"substring '{hit}' + media token → network_out (downgraded)"
                )
                candidates.append(("network_out", "medium"))
                break
            evidence.append(f"substring '{hit}' → {klass} ({downgraded}, downgraded)")
            candidates.append((klass, downgraded))
            break

    if tool.input_schema_properties:
        schema_props = {p.lower() for p in tool.input_schema_properties}
        for tokens, klass, confidence in _INPUT_SCHEMA_HEURISTICS:
            hit = next((tok for tok in tokens if tok in schema_props), None)
            if hit is None:
                continue
            evidence.append(
                f"schema property '{hit}' → reinforces {klass} ({confidence})"
            )
            candidates.append((klass, confidence))
            break

    if tool.description:
        desc_lower = tool.description.lower()
        for tokens, klass, confidence in _DESCRIPTION_KEYWORDS:
            hit = next((tok for tok in tokens if tok in desc_lower), None)
            if hit is None:
                continue
            evidence.append(
                f"description keyword '{hit.strip()}' → {klass} ({confidence})"
            )
            candidates.append((klass, confidence))
            break

    if not candidates:
        return Suggestion(
            plugin_id="",
            tool=tool.name,
            current=None,
            suggested="unknown",
            confidence="unknown",
            evidence=["no name / schema / description signal — human review required"],
        )

    rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    best_class, best_conf = max(candidates, key=lambda c: rank[c[1]])

    corroborating = sum(1 for c in candidates if c[0] == best_class)
    if corroborating >= 2 and best_conf != "high":
        evidence.append(f"corroborated by {corroborating} signals → upgrade to high")
        best_conf = "high"

    return Suggestion(
        plugin_id="",
        tool=tool.name,
        current=None,
        suggested=best_class,
        confidence=best_conf,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Per-plugin auditing
# ---------------------------------------------------------------------------


def _audit_plugin(plugin_dir: Path) -> PluginReport | None:
    manifest = _read_plugin_manifest(plugin_dir)
    if manifest is None:
        return None
    plugin_id = str(manifest.get("id") or plugin_dir.name)
    tools_declared = _resolve_tools(manifest)
    tool_classes = _resolve_tool_classes(manifest)
    tool_metadata = _extract_tool_definitions(plugin_dir)

    suggestions: list[Suggestion] = []
    for name in tools_declared:
        metadata = tool_metadata.get(name) or ToolEntry(name=name)
        sugg = _classify(metadata)
        sugg.plugin_id = plugin_id
        sugg.current = tool_classes.get(name)
        if sugg.current and sugg.current == sugg.suggested:
            sugg.evidence.insert(0, "existing declaration matches suggestion")
        suggestions.append(sugg)
    return PluginReport(
        plugin_id=plugin_id,
        path=plugin_dir,
        tools_declared=tools_declared,
        tool_classes=tool_classes,
        suggestions=suggestions,
    )


def _find_plugin_dir(repo_root: Path, plugin_id: str) -> Path | None:
    for root_name in _DEFAULT_PLUGIN_ROOTS:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            manifest_path = child / "plugin.json"
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(
                    manifest_path.read_text(encoding="utf-8", errors="replace")
                )
            except (OSError, json.JSONDecodeError):
                continue
            if str(data.get("id")) == plugin_id or child.name == plugin_id:
                return child
    return None


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def _render_table(reports: list[PluginReport]) -> str:
    lines: list[str] = []
    lines.append(
        f"{'plugin':<22}  {'tools':>5}  {'declared':>8}  {'missing':>7}  {'cov%':>5}"
    )
    lines.append("-" * 60)
    total_tools = 0
    total_declared = 0
    for r in reports:
        missing = len(r.tools_declared) - len(r.tool_classes)
        cov_pct = int(round(r.coverage * 100))
        lines.append(
            f"{r.plugin_id:<22}  {len(r.tools_declared):>5}  "
            f"{len(r.tool_classes):>8}  {missing:>7}  {cov_pct:>5}"
        )
        total_tools += len(r.tools_declared)
        total_declared += len(r.tool_classes)
    overall_cov = (
        int(round(total_declared / total_tools * 100)) if total_tools else 100
    )
    lines.append("-" * 60)
    lines.append(
        f"TOTAL plugins={len(reports)} tools={total_tools} "
        f"declared={total_declared} missing={total_tools - total_declared} "
        f"coverage={overall_cov}%"
    )

    lines.append("")
    lines.append("Per-tool suggestions (only missing or mismatching):")
    lines.append(
        f"{'plugin':<22}  {'tool':<32}  {'current':<18}  {'suggested':<18}  {'conf':<8}"
    )
    lines.append("-" * 100)
    for r in reports:
        for s in r.suggestions:
            if s.current and s.current == s.suggested:
                continue
            current = s.current or "-"
            lines.append(
                f"{r.plugin_id:<22}  {s.tool:<32}  {current:<18}  "
                f"{s.suggested:<18}  {s.confidence:<8}"
            )
    return "\n".join(lines)


def _render_json(reports: list[PluginReport]) -> str:
    payload = [
        {
            "plugin_id": r.plugin_id,
            "path": str(r.path),
            "tools_declared": r.tools_declared,
            "tool_classes": r.tool_classes,
            "coverage": r.coverage,
            "suggestions": [
                {
                    "tool": s.tool,
                    "current": s.current,
                    "suggested": s.suggested,
                    "confidence": s.confidence,
                    "evidence": s.evidence,
                }
                for s in r.suggestions
            ],
        }
        for r in reports
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _render_patch(reports: list[PluginReport]) -> str:
    lines: list[str] = []
    lines.append("# Suggested tool_classes patches")
    lines.append("")
    lines.append(
        "Only `high`-confidence rows would be auto-applied by `--apply`. Hand-review "
        "everything else."
    )
    for r in reports:
        missing = [s for s in r.suggestions if not (s.current and s.current == s.suggested)]
        if not missing:
            continue
        lines.append("")
        lines.append(f"## {r.plugin_id}  ({r.path})")
        lines.append("")
        lines.append("```jsonc")
        lines.append('"tool_classes": {')
        for s in missing:
            comment_bits = [f"conf={s.confidence}"]
            if s.current:
                comment_bits.append(f"was {s.current!r}")
            comment_bits.extend(s.evidence[:2])
            lines.append(f'  "{s.tool}": "{s.suggested}",  // {"; ".join(comment_bits)}')
        lines.append("}")
        lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Apply (write-back) mode
# ---------------------------------------------------------------------------


def _apply_to_manifest(report: PluginReport, *, dry_run: bool) -> dict[str, Any]:
    """Write high-confidence suggestions back into the manifest.

    Returns a result dict ``{"plugin_id", "applied", "skipped", "path"}``.

    Behaviour:
      * Only ``confidence == 'high'`` suggestions whose ``current`` is
        ``None`` (i.e. truly missing) are written.
      * Existing declarations are never overwritten (even by ``--apply``).
      * The manifest file is rewritten with stable two-space JSON
        indentation, preserving any other top-level keys.
    """
    manifest_path = report.path / "plugin.json"
    raw = manifest_path.read_text(encoding="utf-8", errors="replace")
    data = json.loads(raw)
    existing = data.get("tool_classes") or {}
    if not isinstance(existing, dict):
        existing = {}
    new_block = dict(existing)
    applied: list[str] = []
    skipped: list[str] = []
    for s in report.suggestions:
        if s.confidence != "high":
            skipped.append(f"{s.tool} ({s.confidence})")
            continue
        if s.current is not None:
            continue
        if s.suggested not in _VALID_CLASSES:
            skipped.append(f"{s.tool} (invalid class {s.suggested!r})")
            continue
        new_block[s.tool] = s.suggested
        applied.append(s.tool)
    if applied and not dry_run:
        data["tool_classes"] = new_block
        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {
        "plugin_id": report.plugin_id,
        "applied": applied,
        "skipped": skipped,
        "path": str(manifest_path),
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audit_tool_classes",
        description=(
            "Audit plugin tool_classes coverage and suggest classifications. "
            "See docs/follow-ups/skipped-items-roadmap.md §A.1."
        ),
    )
    group = p.add_mutually_exclusive_group(required=False)
    group.add_argument("--plugin", help="plugin id to audit (single plugin mode)")
    group.add_argument(
        "--all",
        action="store_true",
        help="audit every plugin under plugins/, plugins-archive/, examples/plugins/",
    )
    p.add_argument(
        "--format",
        choices=("json", "table", "patch"),
        default="table",
        help="output format (default: table)",
    )
    p.add_argument(
        "--report",
        default=_DEFAULT_REPORT_PATH,
        help=(
            "write a markdown report to this path in addition to stdout "
            f"(default: {_DEFAULT_REPORT_PATH})"
        ),
    )
    p.add_argument(
        "--no-report",
        action="store_true",
        help="skip writing the markdown report file",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help=(
            "write high-confidence suggestions back into the plugin manifest. "
            "Never used by CI."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="simulate --apply without touching disk (implies --apply)",
    )
    p.add_argument(
        "--repo-root",
        default=".",
        help="path to the OpenAkita repo root (default: CWD)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    if args.dry_run:
        args.apply = True

    if args.plugin:
        plugin_dir = _find_plugin_dir(repo_root, args.plugin)
        if plugin_dir is None:
            print(f"[error] plugin not found: {args.plugin}", file=sys.stderr)
            return 2
        targets = [plugin_dir]
    else:
        targets = list(_iter_plugin_dirs(repo_root, _DEFAULT_PLUGIN_ROOTS))
        if not targets:
            print(
                "[error] no plugin manifests found under "
                f"{', '.join(_DEFAULT_PLUGIN_ROOTS)}",
                file=sys.stderr,
            )
            return 2

    reports: list[PluginReport] = []
    for plugin_dir in targets:
        report = _audit_plugin(plugin_dir)
        if report is not None:
            reports.append(report)

    if args.format == "json":
        rendered = _render_json(reports)
    elif args.format == "patch":
        rendered = _render_patch(reports)
    else:
        rendered = _render_table(reports)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    print(rendered)

    if not args.no_report and args.format != "json":
        report_path = repo_root / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_body = _render_patch(reports)
        report_path.write_text(
            "# Plugin tool_classes audit\n\n"
            "Generated by `scripts/audit_tool_classes.py`. See\n"
            "`docs/follow-ups/skipped-items-roadmap.md` §A.1.\n\n"
            + _render_table(reports)
            + "\n\n"
            + markdown_body
            + "\n",
            encoding="utf-8",
        )

    if args.apply:
        for report in reports:
            result = _apply_to_manifest(report, dry_run=args.dry_run)
            mark = "[dry-run]" if args.dry_run else "[applied]"
            print(
                f"{mark} {result['plugin_id']}: "
                f"applied={len(result['applied'])} skipped={len(result['skipped'])} "
                f"path={result['path']}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
