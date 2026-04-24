#!/usr/bin/env python3
"""网易云音乐 ncm-cli 安装与初始化脚本。"""

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
    print("=== 网易云音乐 ncm-cli 安装 ===\n")

    node = check_cmd("node")
    npm = check_cmd("npm")
    if not node or not npm:
        print("✗ 未检测到 Node.js / npm，请先安装: https://nodejs.org/", file=sys.stderr)
        sys.exit(1)
    node_ver = subprocess.check_output(["node", "--version"]).decode().strip()
    print(f"✓ Node.js {node_ver}")

    ncm = check_cmd("ncm-cli")
    if ncm:
        print(f"✓ ncm-cli 已安装: {ncm}")
    else:
        print("○ ncm-cli 未安装，正在安装...")
        if run(["npm", "install", "-g", "@music163/ncm-cli"]) != 0:
            sys.exit(1)
        print("✓ ncm-cli 安装完成")

    print("\n○ 运行 ncm-cli configure ...")
    run(["ncm-cli", "configure"], check=False)

    print("\n=== 安装完成 ===")
    print("使用 music_quick.py 快速调用网易云音乐 API")


if __name__ == "__main__":
    main()

