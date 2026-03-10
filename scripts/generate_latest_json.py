#!/usr/bin/env python3
"""
Generate release manifest for Tauri updater from GitHub Release assets.

This script is called by the release CI after all platform builds succeed.
It fetches the release assets and .sig files, then generates a JSON manifest
compatible with tauri-plugin-updater.

Output files:
    - release.json     → stable release manifest
    - pre-release.json → pre-release (dev channel) manifest

Usage:
    python scripts/generate_latest_json.py --tag v1.22.0 --output release.json
    python scripts/generate_latest_json.py --tag v1.22.0 --output release.json --repo openakita/openakita

    # With Aliyun OSS CDN:
    python scripts/generate_latest_json.py --tag v1.22.0 --output release.json \
        --cdn-base-url https://dl-cn.openakita.ai
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

try:
    import urllib.error
    import urllib.request
except ImportError:
    pass


GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "openakita/openakita"

# Asset name patterns for each platform
PLATFORM_PATTERNS = {
    "windows-x86_64": {
        "extensions": [".exe"],
        "keywords": ["core"],         # prefer core variant
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


def fetch_json(url: str, token: str | None = None) -> dict:
    """Fetch JSON from a URL."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_asset(assets: list[dict], platform_config: dict) -> dict | None:
    """Find the best matching asset for a platform."""
    candidates = []
    for asset in assets:
        name = asset["name"].lower()
        # Check if any extension matches
        ext_match = any(name.endswith(ext.lower()) for ext in platform_config["extensions"])
        if not ext_match:
            continue
        # Skip excluded patterns
        if any(excl in name for excl in platform_config["exclude"]):
            continue
        candidates.append(asset)

    if not candidates:
        return None

    # Prefer assets matching keywords
    if platform_config["keywords"]:
        for kw in platform_config["keywords"]:
            keyword_matches = [a for a in candidates if kw in a["name"].lower()]
            if keyword_matches:
                return keyword_matches[0]

    return candidates[0]


def find_sig_content(assets: list[dict], asset_name: str) -> str | None:
    """Find and download the .sig file content for an asset."""
    sig_name = asset_name + ".sig"
    for asset in assets:
        if asset["name"] == sig_name:
            # Download the signature content
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


def rewrite_url_to_cdn(github_url: str, cdn_base: str, tag: str) -> str:
    """Rewrite a GitHub Release download URL to a CDN URL.

    GitHub format:  https://github.com/owner/repo/releases/download/v1.0.0/file.exe
    CDN format:     https://dl.openakita.ai/v1.0.0/file.exe
    """
    filename = github_url.rsplit("/", 1)[-1]
    return f"{cdn_base.rstrip('/')}/{tag}/{filename}"


def main():
    parser = argparse.ArgumentParser(description="Generate release manifest for Tauri updater")
    parser.add_argument("--tag", required=True, help="Release tag (e.g. v1.22.0)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository (owner/repo)")
    parser.add_argument(
        "--cdn-base-url",
        default=os.environ.get("CDN_BASE_URL", ""),
        help="Primary CDN base URL for download acceleration (e.g. https://dl-cn.openakita.ai). "
        "Falls back to env var CDN_BASE_URL. If empty, uses GitHub Release URLs.",
    )
    args = parser.parse_args()
    cdn_base = args.cdn_base_url.strip()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    tag = args.tag

    # Fetch release data
    url = f"{GITHUB_API}/repos/{args.repo}/releases/tags/{tag}"
    print(f"Fetching release: {url}")
    try:
        release = fetch_json(url, token)
    except urllib.error.HTTPError as e:
        print(f"Error fetching release: {e}", file=sys.stderr)
        sys.exit(1)

    version = tag.lstrip("v")
    assets = release.get("assets", [])
    notes = release.get("body", "")
    pub_date = release.get("published_at") or datetime.now(UTC).isoformat()

    print(f"Release {tag}: {len(assets)} assets found")

    # Build platforms dict
    platforms = {}
    for platform_key, config in PLATFORM_PATTERNS.items():
        asset = find_asset(assets, config)
        if not asset:
            print(f"  {platform_key}: no matching asset found, skipping")
            continue

        sig = find_sig_content(assets, asset["name"])
        if not sig:
            # Try to read from local file (CI artifact)
            local_sig = asset["name"] + ".sig"
            if os.path.exists(local_sig):
                with open(local_sig, encoding="utf-8") as f:
                    sig = f.read().strip()

        if not sig:
            print(f"  {platform_key}: asset={asset['name']} but no .sig found, skipping")
            continue

        github_url = asset["browser_download_url"]
        download_url = rewrite_url_to_cdn(github_url, cdn_base, tag) if cdn_base else github_url
        entry: dict = {
            "signature": sig,
            "url": download_url,
        }
        if cdn_base:
            entry["github_url"] = github_url
        platforms[platform_key] = entry
        print(f"  {platform_key}: {asset['name']} → {download_url} ✓")

    if not platforms:
        print("Warning: no platforms with valid signatures found", file=sys.stderr)

    # ── downloads: 网站下载页使用（无需 .sig，直接链接安装包） ──
    DOWNLOAD_PATTERNS = {
        "windows-core": {
            "extensions": [".exe"],
            "keywords": ["core"],
            "exclude": ["full", "uninstall"],
        },
        "windows-full": {
            "extensions": [".exe"],
            "keywords": ["full"],
            "exclude": ["core", "uninstall"],
        },
        "macos-arm64": {
            "extensions": [".dmg"],
            "keywords": ["macos-arm64", "aarch64", "arm64"],
            "exclude": [],
        },
        "macos-x64": {
            "extensions": [".dmg"],
            "keywords": ["macos-x64", "x86_64", "intel"],
            "exclude": [],
        },
        "linux-deb-ubuntu22-amd64": {
            "extensions": [".deb"],
            "keywords": ["ubuntu22-amd64"],
            "exclude": [],
        },
        "linux-deb-ubuntu22-arm64": {
            "extensions": [".deb"],
            "keywords": ["ubuntu22-arm64"],
            "exclude": [],
        },
        "linux-deb-ubuntu24-amd64": {
            "extensions": [".deb"],
            "keywords": ["ubuntu24-amd64"],
            "exclude": [],
        },
        "linux-deb-ubuntu24-arm64": {
            "extensions": [".deb"],
            "keywords": ["ubuntu24-arm64"],
            "exclude": [],
        },
        "linux-appimage-x64": {
            "extensions": [".AppImage", ".appimage"],
            "keywords": ["x86_64", "amd64"],
            "exclude": ["arm64", "aarch64"],
        },
    }
    DOWNLOAD_NICKNAMES = {
        "windows-core": "Windows 10/11",
        "windows-full": "Windows 10/11 完整版",
        "macos-arm64": "macOS Apple Silicon (.dmg)",
        "macos-x64": "macOS Intel (.dmg)",
        "linux-appimage-x64": "Linux AppImage x64",
        "linux-deb-ubuntu22-amd64": "Ubuntu 22 x64 (.deb)",
        "linux-deb-ubuntu22-arm64": "Ubuntu 22 ARM64 (.deb)",
        "linux-deb-ubuntu24-amd64": "Ubuntu 24 x64 (.deb)",
        "linux-deb-ubuntu24-arm64": "Ubuntu 24 ARM64 (.deb)",
    }
    downloads = {}
    for dl_key, dl_config in DOWNLOAD_PATTERNS.items():
        asset = find_asset(assets, dl_config)
        if asset:
            github_url = asset["browser_download_url"]
            download_url = rewrite_url_to_cdn(github_url, cdn_base, tag) if cdn_base else github_url
            dl_entry: dict = {
                "nickname": DOWNLOAD_NICKNAMES.get(dl_key, dl_key),
                "name": asset["name"],
                "url": download_url,
                "size": asset.get("size", 0),
            }
            if cdn_base:
                dl_entry["github_url"] = github_url
            downloads[dl_key] = dl_entry
            print(f"  download.{dl_key}: {asset['name']} → {download_url} ✓")

    manifest = {
        "version": version,
        "notes": notes,
        "pub_date": pub_date,
        "platforms": platforms,
        "downloads": downloads,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Written: {args.output}")
    print(f"Platforms: {list(platforms.keys())}")


if __name__ == "__main__":
    main()
