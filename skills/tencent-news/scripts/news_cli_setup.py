#!/usr/bin/env python3
"""腾讯新闻 CLI 安装和配置脚本 — 纯 stdlib 实现"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys


def _is_windows():
    return platform.system() == "Windows"


def _cli_installed():
    return shutil.which("tencent-news-cli") is not None


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def cmd_status(_args):
    installed = _cli_installed()
    api_key = os.environ.get("TENCENT_NEWS_API_KEY", "")
    info = {
        "platform": platform.system(),
        "cli_installed": installed,
        "api_key_set": bool(api_key),
    }
    if installed:
        try:
            r = _run(["tencent-news-cli", "--version"])
            info["cli_version"] = r.stdout.strip() or r.stderr.strip()
        except FileNotFoundError:
            info["cli_version"] = "unknown"
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_install(_args):
    if _cli_installed():
        print(json.dumps({"status": "already_installed"}, ensure_ascii=False, indent=2))
        return

    if _is_windows():
        print(">>> Windows 安装：请在 PowerShell 中执行：")
        print("irm https://mat1.gtimg.com/qqcdn/qqnews/cli/hub/tencent-news/setup.ps1 | iex")
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell:
            cmd = [
                shell, "-NoProfile", "-Command",
                "irm https://mat1.gtimg.com/qqcdn/qqnews/cli/hub/tencent-news/setup.ps1 | iex",
            ]
            r = subprocess.run(cmd, text=True)
            result = {"status": "ok" if r.returncode == 0 else "failed", "code": r.returncode}
        else:
            result = {"status": "skipped", "reason": "PowerShell not found, please install manually"}
    else:
        cmd = ["sh", "-c", "curl -fsSL https://mat1.gtimg.com/qqcdn/qqnews/cli/hub/tencent-news/setup.sh | sh"]
        r = subprocess.run(cmd, text=True)
        result = {"status": "ok" if r.returncode == 0 else "failed", "code": r.returncode}

    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_configure(_args):
    api_key = os.environ.get("TENCENT_NEWS_API_KEY", "")
    if not api_key:
        print(json.dumps(
            {"error": "未设置 TENCENT_NEWS_API_KEY 环境变量，请先 export TENCENT_NEWS_API_KEY=your_key"},
            ensure_ascii=False, indent=2,
        ))
        sys.exit(1)

    if not _cli_installed():
        print(json.dumps({"error": "tencent-news-cli 未安装，请先运行 install"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    r = _run(["tencent-news-cli", "config", "set", "apikey", api_key])
    result = {
        "status": "ok" if r.returncode == 0 else "failed",
        "stdout": r.stdout.strip(),
        "stderr": r.stderr.strip(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="腾讯新闻 CLI 安装和配置")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("install", help="安装 tencent-news-cli")
    sub.add_parser("configure", help="配置 API Key（需设置 TENCENT_NEWS_API_KEY）")
    sub.add_parser("status", help="查看安装和配置状态")

    args = parser.parse_args()
    dispatch = {
        "install": cmd_install,
        "configure": cmd_configure,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
