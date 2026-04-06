#!/usr/bin/env python3
"""飞猪 flyai-cli 快速命令封装。"""

import argparse
import shutil
import subprocess
import sys


def ensure_cli():
    if not shutil.which("flyai"):
        print("错误: flyai-cli 未安装，请先运行 setup.py", file=sys.stderr)
        sys.exit(1)


def run_cli(args: list[str]):
    cmd = ["flyai"] + args
    print(f"→ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_search(args):
    ensure_cli()
    run_cli(["keyword-search", "--keyword", args.keyword])


def cmd_ai_search(args):
    ensure_cli()
    run_cli(["ai-search", "--query", args.query])


def cmd_flight(args):
    ensure_cli()
    cli_args = ["flight-search", "--from", args.from_city, "--to", args.to_city]
    if args.date:
        cli_args += ["--date", args.date]
    run_cli(cli_args)


def cmd_hotel(args):
    ensure_cli()
    cli_args = ["hotel-search", "--city", args.city]
    if args.checkin:
        cli_args += ["--checkin", args.checkin]
    run_cli(cli_args)


def main():
    parser = argparse.ArgumentParser(description="飞猪 flyai-cli 快速命令")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="关键词搜索")
    p_search.add_argument("--keyword", required=True, help="搜索关键词")

    p_ai = sub.add_parser("ai-search", help="AI 智能搜索")
    p_ai.add_argument("--query", required=True, help="搜索问题")

    p_flight = sub.add_parser("flight", help="机票搜索")
    p_flight.add_argument("--from", dest="from_city", required=True, help="出发城市")
    p_flight.add_argument("--to", dest="to_city", required=True, help="到达城市")
    p_flight.add_argument("--date", help="出发日期 (YYYY-MM-DD)")

    p_hotel = sub.add_parser("hotel", help="酒店搜索")
    p_hotel.add_argument("--city", required=True, help="城市")
    p_hotel.add_argument("--checkin", help="入住日期 (YYYY-MM-DD)")

    args = parser.parse_args()
    {"search": cmd_search, "ai-search": cmd_ai_search,
     "flight": cmd_flight, "hotel": cmd_hotel}[args.command](args)


if __name__ == "__main__":
    main()
