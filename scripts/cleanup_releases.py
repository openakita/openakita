#!/usr/bin/env python3
"""
清理 GitHub Releases 中废弃的 Draft 和失败版本。

这个脚本会列出所有 Draft Release，并提供交互式确认后删除它们。
也可以用 --auto 模式自动删除所有 Draft Release。

使用方法:
    # 交互式模式（推荐首次使用）
    python scripts/cleanup_releases.py

    # 自动模式（CI / 脚本调用）
    python scripts/cleanup_releases.py --auto

    # 指定仓库
    python scripts/cleanup_releases.py --repo openakita/openakita

环境变量:
    GH_TOKEN / GITHUB_TOKEN: GitHub Personal Access Token (需要 repo 权限)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "openakita/openakita"


def api_request(url: str, method: str = "GET", token: str | None = None) -> dict | list | None:
    """Make a GitHub API request."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 204:  # No Content (successful delete)
                return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  Error {e.code}: {e.reason}", file=sys.stderr)
        return None


def list_releases(repo: str, token: str | None) -> list[dict]:
    """List all releases (including drafts)."""
    releases = []
    page = 1
    while True:
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page=100&page={page}"
        data = api_request(url, token=token)
        if not data:
            break
        releases.extend(data)
        if len(data) < 100:
            break
        page += 1
    return releases


def main():
    parser = argparse.ArgumentParser(description="清理 GitHub Releases 中的废弃版本")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub 仓库 (owner/repo)")
    parser.add_argument("--auto", action="store_true", help="自动模式：不需要确认即删除所有 Draft")
    parser.add_argument("--dry-run", action="store_true", help="仅列出要删除的 Release，不执行删除")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: 需要设置 GH_TOKEN 或 GITHUB_TOKEN 环境变量", file=sys.stderr)
        sys.exit(1)

    print(f"正在获取 {args.repo} 的 Releases...")
    releases = list_releases(args.repo, token)
    print(f"共 {len(releases)} 个 Release\n")

    # 找出 Draft Release
    drafts = [r for r in releases if r.get("draft")]
    published = [r for r in releases if not r.get("draft")]

    print(f"已发布: {len(published)} 个")
    print(f"Draft: {len(drafts)} 个\n")

    if not drafts:
        print("没有需要清理的 Draft Release。")
        return

    print("以下 Draft Release 将被清理:")
    print("-" * 60)
    for r in drafts:
        tag = r.get("tag_name", "无 tag")
        name = r.get("name", "无标题")
        created = r.get("created_at", "?")[:10]
        assets = len(r.get("assets", []))
        print(f"  [{tag}] {name} ({created}, {assets} 个资产)")
    print("-" * 60)

    if args.dry_run:
        print("\n(--dry-run 模式，不执行删除)")
        return

    if not args.auto:
        confirm = input(f"\n确认删除以上 {len(drafts)} 个 Draft Release? (y/N): ").strip().lower()
        if confirm != "y":
            print("取消操作。")
            return

    print(f"\n开始删除 {len(drafts)} 个 Draft Release...")
    deleted = 0
    for r in drafts:
        release_id = r["id"]
        tag = r.get("tag_name", "?")
        url = f"{GITHUB_API}/repos/{args.repo}/releases/{release_id}"
        result = api_request(url, method="DELETE", token=token)
        if result is None:  # 204 No Content = success, or api_request returns None on delete success
            print(f"  已删除: [{tag}]")
            deleted += 1
        else:
            print(f"  删除失败: [{tag}]")

    print(f"\n完成：已删除 {deleted}/{len(drafts)} 个 Draft Release")


if __name__ == "__main__":
    main()

