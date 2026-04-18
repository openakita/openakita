"""
Parsing and validation of skill installation source URLs.

Normalizes various URL variants (GitHub blob/tree/repo, playbooks.com marketplace
pages, raw.githubusercontent.com, etc.) into a structured installation source
description, shared by SkillManager (chat path) and bridge (Setup Center UI path).
"""

import re
from typing import NamedTuple

# ---------------------------------------------------------------------------
# GitHub URL patterns
# ---------------------------------------------------------------------------

_GITHUB_BLOB_TREE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)"
    r"/(?:blob|tree)/[^/]+/(?P<path>.+?)/?$"
)

_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)

RAW_GITHUB_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/[^/]+/(?P<path>.+)$"
)

_PLAYBOOKS_RE = re.compile(
    r"^https?://(?:www\.)?playbooks\.com/skills/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/(?P<skill>[^/?#]+))?"
)


class GitHubSource(NamedTuple):
    """Normalized GitHub repository coordinates."""

    owner: str
    repo: str
    subdir: str | None


def parse_github_source(url: str) -> GitHubSource | None:
    """Normalize any GitHub URL to (owner, repo, subdir).

    Supported forms:
      - https://github.com/owner/repo
      - https://github.com/owner/repo.git
      - https://github.com/owner/repo/blob/main/path/to/SKILL.md
      - https://github.com/owner/repo/tree/main/path/to/dir
    """
    m = _GITHUB_BLOB_TREE_RE.match(url)
    if m:
        raw_path = m.group("path")
        subdir = re.sub(r"/?SKILL\.md$", "", raw_path, flags=re.IGNORECASE).rstrip("/") or None
        return GitHubSource(m.group("owner"), m.group("repo"), subdir)

    m = _GITHUB_REPO_RE.match(url)
    if m:
        return GitHubSource(m.group("owner"), m.group("repo"), None)

    return None


def parse_playbooks_source(url: str) -> GitHubSource | None:
    """Convert a playbooks.com skill marketplace URL to GitHub coordinates."""
    m = _PLAYBOOKS_RE.match(url)
    if m:
        return GitHubSource(m.group("owner"), m.group("repo"), m.group("skill"))
    return None


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------


def is_html_content(text: str) -> bool:
    """Detect whether an HTTP response is an HTML page rather than Markdown."""
    stripped = text.lstrip()
    return stripped[:50].lower().startswith(("<!doctype", "<html"))


def has_yaml_frontmatter(text: str) -> bool:
    """Detect whether content has YAML frontmatter (a requirement for a valid SKILL.md)."""
    return bool(re.match(r"^---\s*\n", text))
