#!/usr/bin/env python3
"""网易云音乐 ncm-cli 快速命令封装。"""

import argparse
import shutil
import subprocess
import sys


def ensure_cli():
    if not shutil.which("ncm-cli"):
        print("错误: ncm-cli 未安装，请先运行 setup.py", file=sys.stderr)
        sys.exit(1)


def run_cli(args: list[str]):
    cmd = ["ncm-cli"] + args
    print(f"→ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_search(args):
    ensure_cli()
    run_cli(["search", "--keyword", args.keyword])


def cmd_playlist(args):
    ensure_cli()
    run_cli(["playlist", "--id", args.id])


def cmd_recommend(args):
    ensure_cli()
    run_cli(["recommend", "daily"])


def cmd_play(args):
    ensure_cli()
    run_cli(["play", "--id", args.id])


def main():
    parser = argparse.ArgumentParser(description="网易云音乐 ncm-cli 快速命令")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="搜索音乐")
    p_search.add_argument("--keyword", required=True, help="搜索关键词")

    p_pl = sub.add_parser("playlist", help="获取歌单")
    p_pl.add_argument("--id", required=True, help="歌单 ID")

    sub.add_parser("recommend", help="每日推荐")

    p_play = sub.add_parser("play", help="播放歌曲")
    p_play.add_argument("--id", required=True, help="歌曲 ID")

    args = parser.parse_args()
    {"search": cmd_search, "playlist": cmd_playlist,
     "recommend": cmd_recommend, "play": cmd_play}[args.command](args)


if __name__ == "__main__":
    main()

