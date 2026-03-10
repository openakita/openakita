#!/usr/bin/env python3
"""
Generate release manifests for the download page and Tauri updater.

Produces four types of output:
  1. Channel manifest  (release.json / pre-release.json / dev.json)
     — latest version info for one channel, consumed by the website.
  2. Per-version manifest  (releases/v{x}.json)
     — archived manifest for one specific version, consumed by the "history" UI.
  3. Version index  (versions.json)
     — lightweight index of all versions across all channels.
  4. Tauri updater compat  (latest.json)
     — flat format consumed by Tauri's built-in updater (only for "release" channel).

Usage:
    # Basic: generate manifest for a single tag
    python scripts/generate_release_manifest.py \\
        --tag v1.25.9 --channel release --output-dir ./out

    # With CDN rewriting + index update
    python scripts/generate_release_manifest.py \\
        --tag v1.25.9 --channel release --output-dir ./out \\
        --cdn-base-url https://dl-cn.openakita.ai \\
        --existing-index ./existing-versions.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    import urllib.error
    import urllib.request
except ImportError:
    pass

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "openakita/openakita"

# ---------------------------------------------------------------------------
# Tauri updater platform patterns (need .sig signature files)
# ---------------------------------------------------------------------------
UPDATER_PATTERNS: dict[str, dict] = {
    "windows-x86_64": {
        "extensions": [".exe"],
        "keywords": ["core"],
        "exclude": ["full", "uninstall"],
    },
    "darwin-aarch64": {
        "extensions": [".app.tar.gz", ".dmg"],
        "keywords": ["macos-arm64", "aarch64", "arm64"],
        "exclude": [],
    },
    "darwin-x86_64": {
        "extensions": [".app.tar.gz", ".dmg"],
        "keywords": ["macos-x64", "x86_64", "intel"],
        "exclude": [],
    },
    "linux-x86_64": {
        "extensions": [".AppImage", ".appimage"],
        "keywords": [],
        "exclude": [],
    },
}

# ---------------------------------------------------------------------------
# Download patterns grouped by user-facing platform
# ---------------------------------------------------------------------------
PLATFORM_DOWNLOADS: dict[str, list[dict]] = {
    "windows": [
        {
            "key": "windows-x64",
            "extensions": [".exe"],
            "keywords": ["core"],
            "exclude": ["full", "uninstall"],
            "nickname": "Windows 10/11",
        },
    ],
    "macos": [
        {
            "key": "macos-arm64",
            "extensions": [".dmg"],
            "keywords": ["macos-arm64", "aarch64", "arm64"],
            "exclude": [],
            "nickname": "macOS Apple Silicon (.dmg)",
        },
        {
            "key": "macos-x64",
            "extensions": [".dmg"],
            "keywords": ["macos-x64", "x86_64", "intel"],
            "exclude": [],
            "nickname": "macOS Intel (.dmg)",
        },
    ],
    "linux": [
        {
            "key": "linux-deb-ubuntu24-amd64",
            "extensions": [".deb"],
            "keywords": ["ubuntu24-amd64"],
            "exclude": [],
            "nickname": "Ubuntu 24 x64 (.deb)",
        },
        {
            "key": "linux-deb-ubuntu24-arm64",
            "extensions": [".deb"],
            "keywords": ["ubuntu24-arm64"],
            "exclude": [],
            "nickname": "Ubuntu 24 ARM64 (.deb)",
        },
        {
            "key": "linux-deb-ubuntu22-amd64",
            "extensions": [".deb"],
            "keywords": ["ubuntu22-amd64"],
            "exclude": [],
            "nickname": "Ubuntu 22 x64 (.deb)",
        },
        {
            "key": "linux-deb-ubuntu22-arm64",
            "extensions": [".deb"],
            "keywords": ["ubuntu22-arm64"],
            "exclude": [],
            "nickname": "Ubuntu 22 ARM64 (.deb)",
        },
        {
            "key": "linux-appimage-x64",
            "extensions": [".AppImage", ".appimage"],
            "keywords": ["x86_64", "amd64"],
            "exclude": ["arm64", "aarch64"],
            "nickname": "Linux AppImage x64",
        },
    ],
    "android": [
        {
            "key": "android-apk",
            "extensions": [".apk"],
            "keywords": ["android"],
            "exclude": [],
            "nickname": "Android APK",
        },
    ],
    "ios": [
        {
            "key": "ios-ipa",
            "extensions": [".ipa"],
            "keywords": ["ios"],
            "exclude": [],
            "nickname": "iOS IPA",
        },
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_asset(assets: list[dict], config: dict) -> dict | None:
    candidates = []
    for asset in assets:
        name = asset["name"].lower()
        if not any(name.endswith(ext.lower()) for ext in config["extensions"]):
            continue
        if any(excl in name for excl in config.get("exclude", [])):
            continue
        candidates.append(asset)
    if not candidates:
        return None
    for kw in config.get("keywords", []):
        matches = [a for a in candidates if kw in a["name"].lower()]
        if matches:
            return matches[0]
    return candidates[0]


def find_sig_content(assets: list[dict], asset_name: str) -> str | None:
    sig_name = asset_name + ".sig"
    for asset in assets:
        if asset["name"] == sig_name:
            try:
                token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
                headers = {"Accept": "application/octet-stream"}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                req = urllib.request.Request(asset["url"], headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read().decode("utf-8").strip()
            except Exception as e:
                print(f"Warning: could not download sig for {asset_name}: {e}", file=sys.stderr)
                return None
    return None


def rewrite_url(github_url: str, cdn_base: str, tag: str) -> str:
    filename = github_url.rsplit("/", 1)[-1]
    return f"{cdn_base.rstrip('/')}/{tag}/{filename}"


def make_download_url(asset: dict, cdn_base: str, tag: str) -> dict:
    github_url = asset["browser_download_url"]
    url = rewrite_url(github_url, cdn_base, tag) if cdn_base else github_url
    entry: dict = {"url": url}
    if cdn_base:
        entry["github_url"] = github_url
    return entry


def parse_semver(v: str) -> tuple:
    """Parse version string for sorting. Pre-release tags sort lower."""
    v = v.lstrip("v")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", v)
    if not m:
        return (0, 0, 0, 0, v)
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4)
    # No pre-release tag → sorts higher than any pre-release
    if pre is None:
        return (major, minor, patch, 1, "")
    return (major, minor, patch, 0, pre)


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def build_updater_platforms(
    assets: list[dict], cdn_base: str, tag: str
) -> dict:
    platforms = {}
    for platform_key, config in UPDATER_PATTERNS.items():
        asset = find_asset(assets, config)
        if not asset:
            continue
        sig = find_sig_content(assets, asset["name"])
        if not sig:
            local_sig = asset["name"] + ".sig"
            if os.path.exists(local_sig):
                with open(local_sig, encoding="utf-8") as f:
                    sig = f.read().strip()
        if not sig:
            print(f"  updater.{platform_key}: {asset['name']} — no .sig, skipping")
            continue
        urls = make_download_url(asset, cdn_base, tag)
        platforms[platform_key] = {"signature": sig, **urls}
        print(f"  updater.{platform_key}: {asset['name']} ✓")
    return platforms


def build_grouped_downloads(
    assets: list[dict], cdn_base: str, tag: str
) -> dict[str, list[dict]]:
    downloads: dict[str, list[dict]] = {}
    for platform, patterns in PLATFORM_DOWNLOADS.items():
        items = []
        for pat in patterns:
            asset = find_asset(assets, pat)
            if not asset:
                continue
            urls = make_download_url(asset, cdn_base, tag)
            items.append({
                "key": pat["key"],
                "nickname": pat["nickname"],
                "name": asset["name"],
                "size": asset.get("size", 0),
                **urls,
            })
            print(f"  download.{pat['key']}: {asset['name']} ✓")
        if items:
            downloads[platform] = items
    return downloads


def generate_manifest(
    release: dict,
    tag: str,
    channel: str,
    cdn_base: str,
) -> dict:
    version = tag.lstrip("v")
    assets = release.get("assets", [])
    notes = release.get("body", "") or ""
    pub_date = release.get("published_at") or datetime.now(UTC).isoformat()

    print(f"Release {tag}: {len(assets)} assets")

    updater = build_updater_platforms(assets, cdn_base, tag)
    downloads = build_grouped_downloads(assets, cdn_base, tag)

    return {
        "version": version,
        "channel": channel,
        "pub_date": pub_date,
        "notes": notes,
        "platforms": updater,
        "downloads": downloads,
    }


# ---------------------------------------------------------------------------
# Version index management
# ---------------------------------------------------------------------------

def update_version_index(
    existing: dict | None, version: str, channel: str, pub_date: str,
    available_platforms: list[str],
) -> dict:
    if existing is None:
        existing = {"generated_at": "", "release": [], "pre_release": [], "dev": []}

    channel_key = channel.replace("-", "_")  # "pre-release" → "pre_release"
    if channel_key not in existing:
        existing[channel_key] = []

    entries: list[dict] = existing[channel_key]
    entry = {
        "version": version,
        "pub_date": pub_date,
        "platforms": available_platforms,
    }

    replaced = False
    for i, e in enumerate(entries):
        if e["version"] == version:
            entries[i] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)

    entries.sort(key=lambda e: parse_semver(e["version"]), reverse=True)

    existing["generated_at"] = datetime.now(UTC).isoformat()
    return existing


# ---------------------------------------------------------------------------
# Backward-compatible flat downloads (for old website code during transition)
# ---------------------------------------------------------------------------

def flatten_downloads(grouped: dict[str, list[dict]]) -> dict[str, dict]:
    flat: dict[str, dict] = {}
    for items in grouped.values():
        for item in items:
            flat[item["key"]] = {k: v for k, v in item.items() if k != "key"}
    return flat


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate release manifests for download page + Tauri updater"
    )
    parser.add_argument("--tag", required=True, help="Release tag (e.g. v1.25.9)")
    parser.add_argument(
        "--channel", required=True, choices=["release", "pre-release", "dev"],
        help="Release channel"
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Directory for output files (channel manifest, per-version, index)"
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--cdn-base-url", default=os.environ.get("CDN_BASE_URL", ""))
    parser.add_argument(
        "--existing-index", default="",
        help="Path to existing versions.json to merge into (downloaded from OSS)"
    )
    parser.add_argument(
        "--compat-release-json", action="store_true",
        help="Also output latest.json in flat format (Tauri updater compat)"
    )
    args = parser.parse_args()

    cdn_base = args.cdn_base_url.strip()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    tag = args.tag
    channel = args.channel
    out_dir = Path(args.output_dir)

    # Fetch release from GitHub
    url = f"{GITHUB_API}/repos/{args.repo}/releases/tags/{tag}"
    print(f"Fetching release: {url}")
    try:
        release = fetch_json(url, token)
    except urllib.error.HTTPError as e:
        print(f"Error fetching release: {e}", file=sys.stderr)
        sys.exit(1)

    manifest = generate_manifest(release, tag, channel, cdn_base)
    version = manifest["version"]
    available_platforms = list(manifest["downloads"].keys())

    # 1) Channel manifest  (e.g. release.json)
    out_dir.mkdir(parents=True, exist_ok=True)
    channel_file = out_dir / f"{channel}.json"
    with open(channel_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Written channel manifest: {channel_file}")

    # 2) Per-version manifest  (releases/v{version}.json)
    releases_dir = out_dir / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    version_file = releases_dir / f"v{version}.json"
    with open(version_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Written per-version manifest: {version_file}")

    # 3) Tauri updater compat (latest.json — matches tauri.conf.json endpoint)
    if args.compat_release_json or channel == "release":
        compat = {
            "version": version,
            "notes": manifest["notes"],
            "pub_date": manifest["pub_date"],
            "platforms": manifest["platforms"],
            "downloads": flatten_downloads(manifest["downloads"]),
        }
        compat_file = out_dir / "latest.json"
        with open(compat_file, "w", encoding="utf-8") as f:
            json.dump(compat, f, indent=2, ensure_ascii=False)
        print(f"Written Tauri updater compat: {compat_file}")

    # 4) Update version index
    existing_index = None
    if args.existing_index and os.path.exists(args.existing_index):
        with open(args.existing_index, encoding="utf-8") as f:
            existing_index = json.load(f)
        print(f"Loaded existing index: {args.existing_index}")

    index = update_version_index(
        existing_index, version, channel, manifest["pub_date"], available_platforms
    )
    index_file = out_dir / "versions.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(f"Written version index: {index_file}")

    print(f"\nDone — channel={channel}, version={version}, platforms={available_platforms}")


if __name__ == "__main__":
    main()
