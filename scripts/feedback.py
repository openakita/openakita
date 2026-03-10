#!/usr/bin/env python3
"""
CLI tool for managing feedback reports stored in Alibaba Cloud OSS.

Usage:
    python scripts/feedback.py list [--days N] [--type bug|feature]
    python scripts/feedback.py download <report_id> [--output DIR]
    python scripts/feedback.py stats [--days N]

Credentials are read from environment variables or ~/.openakita/feedback.env:
    OSS_ENDPOINT, OSS_BUCKET, OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import oss2
except ImportError:
    print("Error: oss2 package is required.  Install with:  pip install oss2", file=sys.stderr)
    sys.exit(1)


def _load_env() -> None:
    """Load credentials from ~/.openakita/feedback.env if it exists."""
    env_file = Path.home() / ".openakita" / "feedback.env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _get_bucket() -> oss2.Bucket:
    _load_env()
    required = ["OSS_ENDPOINT", "OSS_BUCKET", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"Error: missing environment variables: {', '.join(missing)}", file=sys.stderr)
        print(f"Set them or create ~/.openakita/feedback.env", file=sys.stderr)
        sys.exit(1)

    auth = oss2.Auth(os.environ["OSS_ACCESS_KEY_ID"], os.environ["OSS_ACCESS_KEY_SECRET"])
    return oss2.Bucket(auth, os.environ["OSS_ENDPOINT"], os.environ["OSS_BUCKET"])


def _list_dates(bucket: oss2.Bucket, days: int) -> list[str]:
    """List feedback date prefixes within the last N days."""
    today = datetime.now(timezone.utc).date()
    dates = []
    for delta in range(days):
        d = (today - timedelta(days=delta)).isoformat()
        dates.append(d)
    return dates


def _read_json(bucket: oss2.Bucket, key: str) -> dict | None:
    try:
        result = bucket.get_object(key)
        return json.loads(result.read().decode("utf-8"))
    except oss2.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"  Warning: failed to read {key}: {e}", file=sys.stderr)
        return None


def cmd_list(args: argparse.Namespace) -> None:
    """List feedback reports."""
    bucket = _get_bucket()
    dates = _list_dates(bucket, args.days)
    count = 0

    for date in dates:
        prefix = f"feedback/{date}/"
        for obj in oss2.ObjectIterator(bucket, prefix=prefix, delimiter="/"):
            if not obj.is_prefix():
                continue
            report_prefix = obj.key
            report_id = report_prefix.rstrip("/").split("/")[-1]
            meta = _read_json(bucket, f"{report_prefix}metadata.json")
            if meta is None:
                continue
            if args.type and meta.get("type") != args.type:
                continue

            status = meta.get("status", "?")
            rtype = meta.get("type", "?")
            title = meta.get("title", "(no title)")
            created = meta.get("created_at", "?")
            size = meta.get("size_bytes", 0)
            issue = meta.get("github_issue_url", "")

            size_str = f"{size / 1024:.0f}KB" if size else "?"
            issue_str = f"  → {issue}" if issue else ""
            print(f"  [{status:>8}] {rtype:>7}  {report_id}  {size_str:>8}  {created}  {title}{issue_str}")
            count += 1

    if count == 0:
        print(f"No feedback reports found in the last {args.days} day(s).")
    else:
        print(f"\nTotal: {count} report(s)")


def cmd_download(args: argparse.Namespace) -> None:
    """Download a feedback report ZIP."""
    bucket = _get_bucket()
    report_id = args.report_id

    dates = _list_dates(bucket, 365)
    zip_key = None
    for date in dates:
        candidate = f"feedback/{date}/{report_id}/report.zip"
        if bucket.object_exists(candidate):
            zip_key = candidate
            break

    if not zip_key:
        print(f"Error: report '{report_id}' not found in OSS.", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output) if args.output else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{report_id}.zip"

    print(f"Downloading {zip_key} → {out_path}")
    bucket.get_object_to_file(zip_key, str(out_path))
    print(f"Done. Saved to {out_path}")

    meta_key = zip_key.replace("report.zip", "metadata.json")
    meta = _read_json(bucket, meta_key)
    if meta:
        meta_path = output_dir / f"{report_id}_metadata.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Metadata saved to {meta_path}")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show feedback statistics."""
    bucket = _get_bucket()
    dates = _list_dates(bucket, args.days)

    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_date: dict[str, int] = {}
    total = 0

    for date in dates:
        prefix = f"feedback/{date}/"
        day_count = 0
        for obj in oss2.ObjectIterator(bucket, prefix=prefix, delimiter="/"):
            if not obj.is_prefix():
                continue
            report_prefix = obj.key
            meta = _read_json(bucket, f"{report_prefix}metadata.json")
            if meta is None:
                continue

            rtype = meta.get("type", "unknown")
            status = meta.get("status", "unknown")
            by_type[rtype] = by_type.get(rtype, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            day_count += 1
            total += 1

        if day_count > 0:
            by_date[date] = day_count

    if total == 0:
        print(f"No feedback reports found in the last {args.days} day(s).")
        return

    print(f"=== Feedback Statistics (last {args.days} days) ===\n")
    print(f"Total reports: {total}\n")

    print("By type:")
    for k, v in sorted(by_type.items()):
        print(f"  {k:>10}: {v}")

    print("\nBy status:")
    for k, v in sorted(by_status.items()):
        print(f"  {k:>10}: {v}")

    print("\nBy date:")
    for k, v in sorted(by_date.items(), reverse=True):
        print(f"  {k}: {v}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage OpenAkita feedback reports stored in Alibaba Cloud OSS",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List feedback reports")
    p_list.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    p_list.add_argument("--type", choices=["bug", "feature"], help="Filter by type")

    p_dl = sub.add_parser("download", help="Download a feedback report")
    p_dl.add_argument("report_id", help="The report ID to download")
    p_dl.add_argument("--output", "-o", help="Output directory (default: current dir)")

    p_stats = sub.add_parser("stats", help="Show feedback statistics")
    p_stats.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "download":
        cmd_download(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
