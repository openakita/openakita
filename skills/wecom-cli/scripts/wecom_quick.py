#!/usr/bin/env python3
"""企业微信 wecom-cli 快速命令封装。"""

import argparse
import shutil
import subprocess
import sys


def ensure_cli():
    if not shutil.which("wecom-cli"):
        print("错误: wecom-cli 未安装，请先运行 setup.py", file=sys.stderr)
        sys.exit(1)


def run_cli(args: list[str]):
    cmd = ["wecom-cli"] + args
    print(f"→ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_send_msg(args):
    ensure_cli()
    run_cli(["message", "send", "--to", args.to, "--content", args.content])


def cmd_contacts(args):
    ensure_cli()
    run_cli(["contact", "list"])


def cmd_create_doc(args):
    ensure_cli()
    run_cli(["doc", "create", "--title", args.title])


def cmd_schedule(args):
    ensure_cli()
    run_cli(["calendar", "list"])


def main():
    parser = argparse.ArgumentParser(description="企业微信 wecom-cli 快速命令")
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send-msg", help="发送消息")
    p_send.add_argument("--to", required=True, help="接收人 userid")
    p_send.add_argument("--content", required=True, help="消息内容")

    sub.add_parser("contacts", help="通讯录列表")

    p_doc = sub.add_parser("create-doc", help="创建文档")
    p_doc.add_argument("--title", required=True, help="文档标题")

    sub.add_parser("schedule", help="日程列表")

    args = parser.parse_args()
    {"send-msg": cmd_send_msg, "contacts": cmd_contacts,
     "create-doc": cmd_create_doc, "schedule": cmd_schedule}[args.command](args)


if __name__ == "__main__":
    main()

