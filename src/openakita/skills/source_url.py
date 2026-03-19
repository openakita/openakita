"""
技能安装源 URL 的解析与校验。

将各种 URL 变体（GitHub blob/tree/repo、playbooks.com 市场页面、
raw.githubusercontent.com 等）归一化为结构化的安装源描述，
供 SkillManager（聊天路径）和 bridge（Setup Center UI 路径）共用。
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
    """归一化后的 GitHub 仓库坐标。"""
    owner: str
    repo: str
    subdir: str | None


def parse_github_source(url: str) -> GitHubSource | None:
    """将任意 GitHub URL 归一化为 (owner, repo, subdir)。

    支持:
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
    """将 playbooks.com 技能市场 URL 转为 GitHub 坐标。"""
    m = _PLAYBOOKS_RE.match(url)
    if m:
        return GitHubSource(m.group("owner"), m.group("repo"), m.group("skill"))
    return None


# ---------------------------------------------------------------------------
# Content validation
# ---------------------------------------------------------------------------

def is_html_content(text: str) -> bool:
    """检测 HTTP 响应是否是 HTML 网页而非 Markdown。"""
    stripped = text.lstrip()
    return stripped[:50].lower().startswith(("<!doctype", "<html"))


def has_yaml_frontmatter(text: str) -> bool:
    """检测内容是否有 YAML frontmatter（合法 SKILL.md 的必要条件）。"""
    return bool(re.match(r"^---\s*\n", text))
