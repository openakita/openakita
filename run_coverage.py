#!/usr/bin/env python
"""运行测试覆盖率统计脚本"""
import subprocess
import sys
import os

# 切换到项目根目录
os.chdir(r"D:\coder\myagent")

# 运行 pytest 覆盖率测试
cmd = [
    sys.executable, "-m", "pytest",
    "tests/unit/",
    "-v",
    "--cov=src/openakita",
    "--cov-report=term-missing",
    "--cov-report=html:coverage_html",
    "--cov-fail-under=0",
    "-x"
]

print(f"执行命令：{' '.join(cmd)}")
print("=" * 80)

result = subprocess.run(cmd, capture_output=False, text=True)

print("=" * 80)
print(f"退出码：{result.returncode}")
