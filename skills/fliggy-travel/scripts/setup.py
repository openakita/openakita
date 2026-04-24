#!/usr/bin/env python3
"""飞猪 flyai-cli 安装与初始化脚本。"""

import os
import shutil
import subprocess
import sys


def check_cmd(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], check: bool = True) -> int:
    print(f"  → {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    if check and result.returncode != 0:
        print(f"  ✗ 命令失败 (exit {result.returncode})", file=sys.stderr)
    return result.returncode


def main():
    print("=== 飞猪 flyai-cli 安装 ===\n")

    node = check_cmd("node")
    npm = check_cmd("npm")
    if not node or not npm:
        print("✗ 未检测到 Node.js / npm，请先安装: https://nodejs.org/", file=sys.stderr)
        sys.exit(1)
    node_ver = subprocess.check_output(["node", "--version"]).decode().strip()
    print(f"✓ Node.js {node_ver}")

    flyai = check_cmd("flyai")
    if flyai:
        print(f"✓ flyai-cli 已安装: {flyai}")
    else:
        print("○ flyai-cli 未安装，正在安装...")
        if run(["npm", "install", "-g", "@fly-ai/flyai-cli"]) != 0:
            sys.exit(1)
        print("✓ flyai-cli 安装完成")

    api_key = os.environ.get("FLYAI_API_KEY", "")
    if api_key:
        print(f"\n○ 检测到 FLYAI_API_KEY，配置中...")
        run(["flyai", "config", "--api-key", api_key], check=False)
    else:
        print("\n○ 运行 flyai config ...")
        run(["flyai", "config"], check=False)

    print("\n=== 安装完成 ===")
    print("使用 flyai_quick.py 快速调用飞猪 API")


if __name__ == "__main__":
    main()

