"""obsidian-kb v2.0: Obsidian Vault RAG knowledge source plugin.

Features:
  - obsidian_search: full-text search across Vault Markdown notes
  - obsidian_vault_info: Vault overview (note count, tags, recently modified)
  - obsidian_open: open a note in the Obsidian app via URI protocol
  - obsidian_create: create a new note (write to disk + optional Obsidian open)
  - obsidian_daily: create / open today's daily note
  - on_retrieve hook: auto-inject relevant notes into conversation context
  - retrieval source: standard RAG retrieval interface
  - Incremental indexing with mtime tracking
  - Proper YAML frontmatter parsing via yaml.safe_load
  - fnmatch-based glob exclusion
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from openakita.plugins.api import PluginAPI, PluginBase

logger = logging.getLogger(__name__)

_YAML_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TAG_INLINE_RE = re.compile(
    r"(?:^|\s)#([a-zA-Z\u4e00-\u9fff][\w\u4e00-\u9fff/\-]*)", re.MULTILINE
)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

DEFAULT_EXCLUDES = [".trash", ".obsidian", "templates", ".git", "node_modules"]


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter using yaml.safe_load when available."""
    m = _YAML_FRONT_RE.match(text)
    if not m:
        return {}
    raw = m.group(1)

    if HAS_YAML:
        try:
            result = yaml.safe_load(raw)
            return result if isinstance(result, dict) else {}
        except Exception:
            pass

    fm: dict[str, Any] = {}
    for line in raw.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                if val.startswith("[") and val.endswith("]"):
                    fm[key] = [
                        v.strip().strip('"').strip("'")
                        for v in val[1:-1].split(",") if v.strip()
                    ]
                else:
                    fm[key] = val
    return fm


def _extract_tags(text: str, frontmatter: dict[str, Any]) -> list[str]:
    tags = set()
    fm_tags = frontmatter.get("tags", [])
    if isinstance(fm_tags, str):
        tags.update(t.strip() for t in fm_tags.split(",") if t.strip())
    elif isinstance(fm_tags, list):
        tags.update(str(t).strip() for t in fm_tags if str(t).strip())
    for m in _TAG_INLINE_RE.finditer(text):
        tags.add(m.group(1))
    return sorted(tags)


def _extract_wikilinks(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _WIKILINK_RE.finditer(text)))


def _strip_frontmatter(text: str) -> str:
    m = _YAML_FRONT_RE.match(text)
    if m:
        return text[m.end():]
    return text


def _should_skip(path: Path, vault: Path, patterns: list[str]) -> bool:
    """Check if *path* matches any exclude pattern using fnmatch glob."""
    try:
        rel = str(path.relative_to(vault))
    except ValueError:
        return True
    rel_posix = rel.replace("\\", "/")
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        parts = rel_posix.split("/")
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _obsidian_uri(vault_name: str, action: str = "open", **params: str) -> str:
    """Build an obsidian:// URI."""
    qs = urllib.parse.urlencode({"vault": vault_name, **params})
    return f"obsidian://{action}?{qs}"


class NoteIndex:
    """In-memory index of Vault notes with incremental mtime-based rebuilding."""

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []
        self._vault_path: str = ""
        self._built = False
        self._mtime_cache: dict[str, float] = {}

    def build(
        self,
        vault_path: str,
        excludes: list[str] | None = None,
        max_size_kb: int = 500,
    ) -> None:
        vault = Path(vault_path)
        if not vault.is_dir():
            self.notes = []
            self._built = False
            return

        excludes = excludes or DEFAULT_EXCLUDES
        max_bytes = max_size_kb * 1024

        if self._built and self._vault_path == vault_path:
            changed = self._incremental_update(vault, excludes, max_bytes)
            if changed == 0:
                return
            logger.debug("obsidian-kb: incremental update — %d notes changed", changed)
            return

        self._full_build(vault, excludes, max_bytes)

    def _full_build(
        self, vault: Path, excludes: list[str], max_bytes: int
    ) -> None:
        notes: list[dict[str, Any]] = []
        mtime_cache: dict[str, float] = {}

        for md in sorted(vault.rglob("*.md")):
            if _should_skip(md, vault, excludes):
                continue
            note = self._index_file(md, vault, max_bytes)
            if note:
                notes.append(note)
                mtime_cache[note["path"]] = md.stat().st_mtime

        self.notes = notes
        self._mtime_cache = mtime_cache
        self._vault_path = str(vault)
        self._built = True
        logger.info("obsidian-kb: indexed %d notes from %s", len(notes), vault)

    def _incremental_update(
        self, vault: Path, excludes: list[str], max_bytes: int
    ) -> int:
        current_files: dict[str, Path] = {}
        for md in vault.rglob("*.md"):
            if _should_skip(md, vault, excludes):
                continue
            try:
                rel = str(md.relative_to(vault))
            except ValueError:
                continue
            current_files[rel] = md

        changed = 0
        notes_by_path = {n["path"]: n for n in self.notes}

        removed = set(notes_by_path.keys()) - set(current_files.keys())
        for r in removed:
            del notes_by_path[r]
            self._mtime_cache.pop(r, None)
            changed += 1

        for rel, md in current_files.items():
            try:
                mtime = md.stat().st_mtime
            except OSError:
                continue
            cached_mtime = self._mtime_cache.get(rel)
            if cached_mtime is not None and mtime == cached_mtime:
                continue
            note = self._index_file(md, vault, max_bytes)
            if note:
                notes_by_path[rel] = note
                self._mtime_cache[rel] = mtime
                changed += 1
            else:
                notes_by_path.pop(rel, None)
                self._mtime_cache.pop(rel, None)

        if changed > 0:
            self.notes = list(notes_by_path.values())

        return changed

    @staticmethod
    def _index_file(
        md: Path, vault: Path, max_bytes: int
    ) -> dict[str, Any] | None:
        try:
            stat = md.stat()
            if stat.st_size > max_bytes:
                return None
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        fm = _parse_frontmatter(text)
        body = _strip_frontmatter(text)
        tags = _extract_tags(text, fm)
        links = _extract_wikilinks(body)
        title = fm.get("title") or md.stem
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        return {
            "path": str(md.relative_to(vault)),
            "title": title,
            "tags": tags,
            "links": links,
            "body": body,
            "body_lower": body.lower(),
            "mtime": mtime,
            "size": stat.st_size,
        }

    def invalidate(self) -> None:
        self._built = False
        self._mtime_cache.clear()

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: str = "",
        excerpt_len: int = 600,
    ) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q and not tag:
            return []

        tokens = [t for t in re.split(r"[\s\W]+", q) if len(t) > 1] if q else []

        results: list[dict[str, Any]] = []
        for note in self.notes:
            if tag and tag not in note["tags"]:
                continue

            hay = note["body_lower"]
            score = 0.0

            if q and q in hay:
                score += 0.6
            if q and q in note["title"].lower():
                score += 0.3

            for t in tokens[:10]:
                if t in hay:
                    score += 0.08
                if t in note["title"].lower():
                    score += 0.15

            if tag and tag in note["tags"]:
                score += 0.2

            if score <= 0:
                continue

            body_clean = note["body"].strip().replace("\n", " ")
            if q:
                idx = hay.find(q)
                if idx >= 0:
                    start = max(0, idx - 80)
                    excerpt = body_clean[start : start + excerpt_len]
                else:
                    excerpt = body_clean[:excerpt_len]
            else:
                excerpt = body_clean[:excerpt_len]

            results.append({
                "id": note["path"],
                "title": note["title"],
                "content": f"## {note['title']}\n{excerpt}",
                "tags": note["tags"],
                "links": note["links"][:10],
                "mtime": note["mtime"],
                "relevance": round(min(score, 1.0), 3),
            })

        ranked = sorted(results, key=lambda x: -x["relevance"])
        return ranked[:limit]

    def vault_info(self) -> dict[str, Any]:
        if not self.notes:
            return {"total_notes": 0, "tags": [], "recent": []}

        all_tags: dict[str, int] = {}
        for n in self.notes:
            for t in n["tags"]:
                all_tags[t] = all_tags.get(t, 0) + 1

        top_tags = sorted(all_tags.items(), key=lambda x: -x[1])[:30]
        recent = sorted(self.notes, key=lambda x: x["mtime"], reverse=True)[:10]

        total_size = sum(n["size"] for n in self.notes)
        return {
            "total_notes": len(self.notes),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
            "recent_notes": [
                {"path": n["path"], "title": n["title"], "mtime": n["mtime"]}
                for n in recent
            ],
        }


class ObsidianRetriever:
    source_name = "obsidian"

    def __init__(self, index: NoteIndex, get_config: Any) -> None:
        self._index = index
        self._get_config = get_config

    async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
        cfg = self._get_config()
        vault = (cfg.get("vault_path") or "").strip()
        if not vault:
            return []
        self._index.build(
            vault,
            excludes=cfg.get("exclude_patterns", DEFAULT_EXCLUDES),
            max_size_kb=cfg.get("max_file_size_kb", 500),
        )
        return self._index.search(
            query,
            limit=limit,
            excerpt_len=cfg.get("excerpt_length", 600),
        )


def _get_vault_name(vault_path: str) -> str:
    return Path(vault_path).name


# ---------- Tool definitions ----------

SEARCH_DEF = {
    "type": "function",
    "function": {
        "name": "obsidian_search",
        "description": (
            "Search Markdown notes in the user's Obsidian Vault. "
            "Supports full-text search and optional tag filtering."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query keywords",
                },
                "tag": {
                    "type": "string",
                    "description": "Filter by tag (without #)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                },
            },
            "required": ["query"],
        },
    },
}

VAULT_INFO_DEF = {
    "type": "function",
    "function": {
        "name": "obsidian_vault_info",
        "description": (
            "Get Obsidian vault overview: note count, top tags, "
            "recently modified notes."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

OPEN_DEF = {
    "type": "function",
    "function": {
        "name": "obsidian_open",
        "description": (
            "Open a note in the Obsidian desktop app via obsidian:// URI."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Note path relative to vault root (e.g. 'Projects/idea.md')",
                },
            },
            "required": ["file"],
        },
    },
}

CREATE_DEF = {
    "type": "function",
    "function": {
        "name": "obsidian_create",
        "description": (
            "Create a new Markdown note in the Obsidian vault. "
            "Writes the file to disk and optionally opens it in Obsidian."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Note path relative to vault root (e.g. 'Inbox/new-idea.md')",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content for the note",
                },
                "open": {
                    "type": "boolean",
                    "description": "Open the note in Obsidian after creation (default true)",
                },
            },
            "required": ["file", "content"],
        },
    },
}

DAILY_DEF = {
    "type": "function",
    "function": {
        "name": "obsidian_daily",
        "description": (
            "Create or open today's daily note (YYYY-MM-DD.md) in Obsidian."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Daily notes folder relative to vault (default 'Daily')",
                },
                "template": {
                    "type": "string",
                    "description": "Optional template content for a new daily note",
                },
            },
        },
    },
}

ALL_TOOLS = [SEARCH_DEF, VAULT_INFO_DEF, OPEN_DEF, CREATE_DEF, DAILY_DEF]


class Plugin(PluginBase):
    def __init__(self) -> None:
        self._api: PluginAPI | None = None
        self._index = NoteIndex()

    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        retriever = ObsidianRetriever(self._index, api.get_config)

        api.register_retrieval_source(retriever)

        async def inject_knowledge(**kwargs: Any) -> str:
            query = str(
                kwargs.get("query")
                or kwargs.get("enhanced_query")
                or kwargs.get("user_query")
                or ""
            )
            chunks = await retriever.retrieve(query, limit=3)
            if not chunks:
                return ""
            lines = []
            for c in chunks:
                body = (c.get("content") or "")[:500]
                if body:
                    lines.append(body)
            if not lines:
                return ""
            return "\n<!-- obsidian-kb -->\n" + "\n\n".join(lines) + "\n"

        api.register_hook("on_retrieve", inject_knowledge)

        async def tool_handler(tool_name: str, params: dict) -> str:
            cfg = api.get_config()
            vault = (cfg.get("vault_path") or "").strip()
            if not vault:
                return "Please configure vault_path first in plugin settings."

            self._index.build(
                vault,
                excludes=cfg.get("exclude_patterns", DEFAULT_EXCLUDES),
                max_size_kb=cfg.get("max_file_size_kb", 500),
            )
            vault_name = _get_vault_name(vault)

            if tool_name == "obsidian_search":
                return self._handle_search(params, cfg)
            if tool_name == "obsidian_vault_info":
                return self._handle_vault_info()
            if tool_name == "obsidian_open":
                return self._handle_open(params, vault, vault_name)
            if tool_name == "obsidian_create":
                return self._handle_create(params, vault, vault_name)
            if tool_name == "obsidian_daily":
                return self._handle_daily(params, vault, vault_name)
            return f"Unknown tool: {tool_name}"

        api.register_tools(ALL_TOOLS, tool_handler)
        api.log("obsidian-kb v2.0.0 loaded — 5 tools + retrieval source", "info")

    # ---------- Tool handlers ----------

    def _handle_search(self, params: dict, cfg: dict) -> str:
        query = str(params.get("query", ""))
        tag = str(params.get("tag", ""))
        lim = int(params.get("limit", 5))
        results = self._index.search(
            query, limit=lim, tag=tag,
            excerpt_len=cfg.get("excerpt_length", 600),
        )
        if not results:
            return f"No matching notes found for '{query}'."
        parts = []
        for r in results:
            tag_str = " ".join(f"#{t}" for t in r.get("tags", [])[:5])
            parts.append(
                f"**{r['title']}** (relevance: {r['relevance']:.2f})\n"
                f"   Path: {r['id']} | Tags: {tag_str or 'none'}\n"
                f"   {r.get('content', '')[:400]}"
            )
        return "\n\n".join(parts)

    def _handle_vault_info(self) -> str:
        info = self._index.vault_info()
        if info["total_notes"] == 0:
            return "Vault is empty or path is invalid."
        tag_lines = ", ".join(
            f"#{t['tag']}({t['count']})" for t in info["top_tags"][:15]
        )
        recent_lines = "\n".join(
            f"  - {n['title']} ({n['mtime'][:10]})" for n in info["recent_notes"]
        )
        return (
            f"Vault overview\n"
            f"Total notes: {info['total_notes']}\n"
            f"Total size: {info['total_size_mb']} MB\n"
            f"Top tags: {tag_lines}\n"
            f"Recently modified:\n{recent_lines}"
        )

    @staticmethod
    def _handle_open(params: dict, vault: str, vault_name: str) -> str:
        file = params.get("file", "").strip()
        if not file:
            return "Please provide a file path."
        note_path = Path(vault) / file
        if not note_path.exists():
            return f"Note not found: {file}"
        uri = _obsidian_uri(vault_name, "open", file=file)
        try:
            webbrowser.open(uri)
            return f"Opened {file} in Obsidian."
        except Exception as e:
            return f"Failed to open URI: {e}"

    @staticmethod
    def _handle_create(params: dict, vault: str, vault_name: str) -> str:
        file = params.get("file", "").strip()
        content = params.get("content", "")
        should_open = params.get("open", True)
        if not file:
            return "Please provide a file path."

        note_path = Path(vault) / file
        if note_path.exists():
            return f"Note already exists: {file}. Use obsidian_open instead."

        try:
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"Failed to write note: {e}"

        if should_open:
            uri = _obsidian_uri(vault_name, "open", file=file)
            try:
                webbrowser.open(uri)
            except Exception:
                pass

        return f"Created {file} ({len(content)} chars)."

    @staticmethod
    def _handle_daily(params: dict, vault: str, vault_name: str) -> str:
        folder = params.get("folder", "Daily").strip()
        template = params.get("template", "")
        today = datetime.now().strftime("%Y-%m-%d")
        file = f"{folder}/{today}.md"
        note_path = Path(vault) / file

        if not note_path.exists():
            note_path.parent.mkdir(parents=True, exist_ok=True)
            if not template:
                template = f"---\ndate: {today}\ntags: [daily]\n---\n\n# {today}\n\n"
            try:
                note_path.write_text(template, encoding="utf-8")
            except OSError as e:
                return f"Failed to create daily note: {e}"

        uri = _obsidian_uri(vault_name, "open", file=file)
        try:
            webbrowser.open(uri)
        except Exception:
            pass
        return f"Daily note: {file}"

    def on_unload(self) -> None:
        self._index.invalidate()
        self._api = None

