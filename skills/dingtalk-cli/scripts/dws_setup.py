#!/usr/bin/env python3
"""钉钉 CLI (dws) 安装和认证脚本 — 纯 stdlib 实现"""

import argparse
import json
import platform
import shutil
import subprocess
import sys


def _is_windows():
    return platform.system() == "Windows"


def _dws_installed():
    return shutil.which("dws") is not None


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def cmd_status(_args):
    installed = _dws_installed()
    info = {
        "platform": platform.system(),
        "dws_installed": installed,
    }
    if installed:
        try:
            r = _run(["dws", "--version"])
            info["dws_version"] = r.stdout.strip() or r.stderr.strip()
        except FileNotFoundError:
            info["dws_version"] = "unknown"
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_install(args):
    if _dws_installed() and not args.force:
        print(json.dumps({"status": "already_installed"}, ensure_ascii=False, indent=2))
        return

    method = args.method
    if method == "auto":
        method = "npm" if shutil.which("npm") else "script"

    if method == "npm":
        r = _run(["npm", "install", "-g", "dingtalk-workspace-cli"])
        result = {"method": "npm", "status": "ok" if r.returncode == 0 else "failed", "code": r.returncode}
        if r.stderr.strip():
            result["stderr"] = r.stderr.strip()
    elif _is_windows():
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell:
            cmd = [
                shell, "-NoProfile", "-Command",
                "irm https://dtapp-pub.dingtalk.com/dws/install.ps1 | iex",
            ]
            r = subprocess.run(cmd, text=True)
            result = {"method": "powershell", "status": "ok" if r.returncode == 0 else "failed", "code": r.returncode}
        else:
            result = {"method": "script", "status": "skipped", "reason": "PowerShell not found"}
    else:
        cmd = ["sh", "-c", "curl -fsSL https://dtapp-pub.dingtalk.com/dws/install.sh | sh"]
        r = subprocess.run(cmd, text=True)
        result = {"method": "curl", "status": "ok" if r.returncode == 0 else "failed", "code": r.returncode}

    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_auth(_args):
    if not _dws_installed():
        print(json.dumps({"error": "dws 未安装，请先运行 install"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(">>> 启动 dws auth login（可能会打开浏览器进行 OAuth 认证）...")
    r = subprocess.run(["dws", "auth", "login"], text=True)
    result = {"status": "ok" if r.returncode == 0 else "failed", "code": r.returncode}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="钉钉 CLI (dws) 安装和认证")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install", help="安装 dws CLI")
    p.add_argument("--method", choices=["auto", "npm", "script"], default="auto",
                    help="安装方式 (default: auto)")
    p.add_argument("--force", action="store_true", help="强制重新安装")

    sub.add_parser("auth", help="登录钉钉账号")
    sub.add_parser("status", help="查看安装状态")

    args = parser.parse_args()
    dispatch = {
        "install": cmd_install,
        "auth": cmd_auth,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
