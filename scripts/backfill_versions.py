#!/usr/bin/env python3
"""
Backfill historical version data from GitHub Releases.

Generates per-version manifests and a versions.json index for all existing
GitHub Releases. This is a one-time migration tool to populate the new
download page data structure from historical releases.

Channel detection (four-tier priority):
  1. Release Asset (release.json / pre-release.json) uploaded by CI
  2. Release title badge "[稳定版]" / "[抢先版]" / "[开发版]"
  3. release-channels.json config (tag minor version → channel mapping)
  4. Heuristic fallback: "latest" → release, -rc/-beta/-alpha → pre-release, else → dev

Usage:
    # Generate all manifests locally for review
    python scripts/backfill_versions.py --repo openakita/openakita --output-dir ./backfill-out

    # With CDN URL rewriting
    python scripts/backfill_versions.py --repo openakita/openakita --output-dir ./backfill-out \\
        --cdn-base-url https://dl-cn.openakita.ai

    # Upload to OSS after review
    ossutil cp -r ./backfill-out/api/ oss://{bucket}/api/ -f
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import urllib.error
    import urllib.request
except ImportError:
    pass

# Reuse the manifest generation logic
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_release_manifest import (
    build_grouped_downloads,
    build_updater_platforms,
    flatten_downloads,
    update_version_index,
)

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "openakita/openakita"
PRE_RELEASE_SUFFIX = re.compile(r"-(?:rc|beta|alpha)\.", re.IGNORECASE)


def fetch_json(url: str, token: str | None = None) -> dict | list:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_releases(repo: str, token: str | None = None) -> list[dict]:
    """Fetch all releases via GitHub API (paginated)."""
    releases = []
    page = 1
    while True:
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page=100&page={page}"
        print(f"Fetching page {page}: {url}")
        data = fetch_json(url, token)
        if not data:
            break
        releases.extend(data)
        if len(data) < 100:
            break
        page += 1
    return releases


CHANNEL_NAMES = {"release", "pre-release", "dev"}

TITLE_CHANNEL_MAP = {
    "稳定版": "release",
    "抢先版": "pre-release",
    "开发版": "dev",
}


def detect_channel(release: dict) -> str:
    """Detect release channel with four-tier priority:
    1. Manifest asset (release.json / pre-release.json / dev.json) uploaded by CI
    2. Title badge like "v1.25.9 [抢先版]" set by CI
    3. release-channels.json config (tag minor version → channel mapping)
    4. Heuristic fallback for old releases without CI metadata
    """
    # Priority 1: check for channel manifest asset uploaded by publish-release.yml
    assets = release.get("assets", [])
    for asset in assets:
        name = asset.get("name", "")
        stem = name.removesuffix(".json")
        if stem in CHANNEL_NAMES:
            return stem

    # Priority 2: check release title for channel badge like "[稳定版]"
    title = release.get("name", "") or ""
    for label, channel in TITLE_CHANNEL_MAP.items():
        if label in title:
            return channel

    # Priority 3: infer from release-channels.json config if available
    tag = release.get("tag_name", "")
    config = release.get("_channel_config")
    if config:
        version = tag.lstrip("v")
        parts = version.split(".")
        if len(parts) >= 2:
            minor = f"{parts[0]}.{parts[1]}"
            if minor == config.get("release", ""):
                return "release"
            if minor == config.get("pre-release", ""):
                return "pre-release"

    # Priority 4: heuristic fallback (old releases without CI metadata or config)
    is_prerelease = release.get("prerelease", False)
    is_latest = release.get("is_the_latest", False)

    if PRE_RELEASE_SUFFIX.search(tag):
        return "pre-release"
    if is_latest or (not is_prerelease and not release.get("draft", False)):
        return "release"
    return "dev"


def load_channel_config(path: str | None) -> dict | None:
    """Load .github/release-channels.json if available."""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    default_path = Path(__file__).resolve().parent.parent / ".github" / "release-channels.json"
    if default_path.exists():
        with open(default_path, encoding="utf-8") as f:
            return json.load(f)
    return None


def main():
    parser = argparse.ArgumentParser(description="Backfill historical version data")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--output-dir", required=True, help="Output directory (e.g. ./backfill-out)")
    parser.add_argument("--cdn-base-url", default=os.environ.get("CDN_BASE_URL", ""))
    parser.add_argument(
        "--channel-config", default="",
        help="Path to release-channels.json (auto-detected from repo root if omitted)"
    )
    parser.add_argument(
        "--channel-override", default="",
        help="Force all releases to a specific channel (for manual correction)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing files")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    cdn_base = args.cdn_base_url.strip()
    out_dir = Path(args.output_dir) / "api"

    channel_config = load_channel_config(args.channel_config or None)
    if channel_config:
        print(f"Channel config: release={channel_config.get('release')}, "
              f"pre-release={channel_config.get('pre-release')}")
    else:
        print("No channel config found, using heuristic detection only")

    releases = fetch_all_releases(args.repo, token)
    print(f"\nFound {len(releases)} releases total\n")

    # Filter out drafts
    releases = [r for r in releases if not r.get("draft", False)]

    # Mark the "latest" release explicitly (GitHub API returns it first for non-prerelease)
    latest_found = False
    for r in releases:
        if not r.get("prerelease", False) and not latest_found:
            r["is_the_latest"] = True
            latest_found = True
        else:
            r["is_the_latest"] = False

    # Inject channel config into each release for detect_channel to use
    if channel_config:
        for r in releases:
            r["_channel_config"] = channel_config

    # Build index
    index: dict = {"generated_at": "", "release": [], "pre_release": [], "dev": []}
    stats = {"release": 0, "pre-release": 0, "dev": 0, "skipped": 0}

    for release in releases:
        tag = release.get("tag_name", "")
        if not tag:
            stats["skipped"] += 1
            continue

        version = tag.lstrip("v")
        channel = args.channel_override or detect_channel(release)
        assets = release.get("assets", [])
        notes = release.get("body", "") or ""
        pub_date = release.get("published_at") or ""

        print(f"  {tag} → {channel} ({len(assets)} assets)")

        if args.dry_run:
            stats[channel] = stats.get(channel, 0) + 1
            continue

        # Build manifest
        updater = build_updater_platforms(assets, cdn_base, tag)
        downloads = build_grouped_downloads(assets, cdn_base, tag)

        manifest = {
            "version": version,
            "channel": channel,
            "pub_date": pub_date,
            "notes": notes,
            "platforms": updater,
            "downloads": downloads,
        }

        # Write per-version manifest
        releases_dir = out_dir / "releases"
        releases_dir.mkdir(parents=True, exist_ok=True)
        version_file = releases_dir / f"v{version}.json"
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        # Update index
        available_platforms = list(downloads.keys())
        index = update_version_index(index, version, channel, pub_date, available_platforms)

        stats[channel] = stats.get(channel, 0) + 1

    if not args.dry_run:
        # Write versions.json
        out_dir.mkdir(parents=True, exist_ok=True)
        index_file = out_dir / "versions.json"
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
        print(f"\nWritten index: {index_file}")

        # Write channel manifests for the latest of each channel
        for channel_key in ["release", "pre_release", "dev"]:
            entries = index.get(channel_key, [])
            if not entries:
                continue
            latest_version = entries[0]["version"]
            latest_manifest_path = out_dir / "releases" / f"v{latest_version}.json"
            if latest_manifest_path.exists():
                channel_filename = channel_key.replace("_", "-") + ".json"
                channel_file = out_dir / channel_filename
                with open(latest_manifest_path, encoding="utf-8") as src:
                    data = json.load(src)
                with open(channel_file, "w", encoding="utf-8") as dst:
                    json.dump(data, dst, indent=2, ensure_ascii=False)
                print(f"Written channel: {channel_file} (v{latest_version})")

                # Tauri updater compat (latest.json) for release channel
                if channel_key == "release":
                    compat = {
                        "version": data["version"],
                        "notes": data["notes"],
                        "pub_date": data["pub_date"],
                        "platforms": data["platforms"],
                        "downloads": flatten_downloads(data["downloads"]),
                    }
                    compat_file = out_dir / "latest.json"
                    with open(compat_file, "w", encoding="utf-8") as f:
                        json.dump(compat, f, indent=2, ensure_ascii=False)
                    print(f"Written Tauri updater compat: {compat_file}")

    print(f"\nSummary: release={stats.get('release',0)}, "
          f"pre-release={stats.get('pre-release',0)}, dev={stats.get('dev',0)}, "
          f"skipped={stats.get('skipped',0)}")
    if args.dry_run:
        print("(dry run — no files written)")


if __name__ == "__main__":
    main()
