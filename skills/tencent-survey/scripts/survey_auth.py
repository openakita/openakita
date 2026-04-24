#!/usr/bin/env python3
"""腾讯问卷认证辅助脚本 — 纯 stdlib 实现"""

import argparse
import json
import os
import shutil
import subprocess
import sys


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _mcporter_installed():
    return shutil.which("mcporter") is not None


def cmd_check(_args):
    installed = _mcporter_installed()
    info = {"mcporter_installed": installed}
    if installed:
        try:
            r = _run(["mcporter", "--version"])
            info["mcporter_version"] = r.stdout.strip() or r.stderr.strip()
        except FileNotFoundError:
            info["mcporter_version"] = "unknown"
    else:
        info["install_hint"] = "npm install -g mcporter"
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_configure(_args):
    if not _mcporter_installed():
        print(json.dumps(
            {"error": "mcporter 未安装，请先执行: npm install -g mcporter"},
            ensure_ascii=False, indent=2,
        ))
        sys.exit(1)

    token = os.environ.get("TENCENT_SURVEY_TOKEN", "")
    if token:
        if not token.startswith("wjpt_"):
            print(json.dumps(
                {"error": "TENCENT_SURVEY_TOKEN 必须以 wjpt_ 前缀开头"},
                ensure_ascii=False, indent=2,
            ))
            sys.exit(1)
    else:
        print("未检测到 TENCENT_SURVEY_TOKEN 环境变量。")
        print("请访问腾讯问卷 OAuth 页面获取 token：")
        print("  1. 打开 https://wj.qq.com 并登录")
        print("  2. 在开发者设置中获取 API token（wjpt_ 前缀）")
        print("  3. export TENCENT_SURVEY_TOKEN=wjpt_your_token")
        sys.exit(1)

    cmd = [
        "mcporter", "config", "add", "tencent-survey",
        "https://wj.qq.com/api/v2/mcp",
        "--header", f"Authorization=Bearer {token}",
        "--transport", "http",
        "--scope", "home",
    ]
    r = _run(cmd)
    result = {
        "status": "ok" if r.returncode == 0 else "failed",
        "stdout": r.stdout.strip(),
        "stderr": r.stderr.strip(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_status(_args):
    installed = _mcporter_installed()
    token_set = bool(os.environ.get("TENCENT_SURVEY_TOKEN", ""))
    info = {
        "mcporter_installed": installed,
        "token_env_set": token_set,
    }
    if installed:
        r = _run(["mcporter", "config", "list"])
        configured = "tencent-survey" in r.stdout
        info["tencent_survey_configured"] = configured
        if configured:
            info["config_output"] = r.stdout.strip()
    print(json.dumps(info, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="腾讯问卷认证辅助（mcporter 配置）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="检查 mcporter 是否安装")
    sub.add_parser("configure", help="配置腾讯问卷（需设置 TENCENT_SURVEY_TOKEN）")
    sub.add_parser("status", help="查看配置状态")

    args = parser.parse_args()
    dispatch = {
        "check": cmd_check,
        "configure": cmd_configure,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

