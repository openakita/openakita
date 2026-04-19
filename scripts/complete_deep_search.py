#!/usr/bin/env python3
"""
One-shot: wire deep_search into OpenAkita and run benchmarks.
1. Add DEEP_SEARCH_TOOLS to BASE_TOOLS
2. Register deep_search handler 
3. Run benchmark suite
"""

import os
import re
import sys
import subprocess

BASE_DIR = "/home/devops/agents/openakita"

# ── Step 1: Add DEEP_SEARCH_TOOLS to BASE_TOOLS ──
init_path = os.path.join(BASE_DIR, "src/openakita/tools/definitions/__init__.py")
with open(init_path, "r") as f:
    content = f.read()

if "+ DEEP_SEARCH_TOOLS" not in content:
    # Add it after WORKTREE_TOOLS in the BASE_TOOLS tuple
    content = content.replace(
        "    + WORKTREE_TOOLS\n)",
        "    + WORKTREE_TOOLS\n    + DEEP_SEARCH_TOOLS\n)"
    )
    with open(init_path, "w") as f:
        f.write(content)
    print("✅ Added DEEP_SEARCH_TOOLS to BASE_TOOLS")
else:
    print("ℹ️  DEEP_SEARCH_TOOLS already in BASE_TOOLS")

# ── Step 2: Find and add handler registration ──
# Check how other handlers are registered (e.g., web_search)
handler_init = os.path.join(BASE_DIR, "src/openakita/tools/handlers/__init__.py")
# Just check the registration pattern - handlers are registered elsewhere
# Look at executor or agent setup
print("\n🔍 Checking handler registration pattern...")
for root, dirs, files in os.walk(os.path.join(BASE_DIR, "src/openakita")):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in files:
        if f.endswith(".py"):
            fpath = os.path.join(root, f)
            with open(fpath, "r") as fp:
                txt = fp.read()
            if "register_handler" in txt and "web_search" in txt:
                print(f"  Found handler registration in: {fpath}")
                # Show the relevant lines
                for i, line in enumerate(txt.split("\n")):
                    if "web_search" in line and "register" in line.lower():
                        print(f"    L{i+1}: {line.strip()}")
            if "register_handler" in txt and "deep_search" in txt:
                print(f"  ✅ deep_search already registered in: {fpath}")

# ── Step 3: Run benchmarks ──
print("\n🚀 Running deep search benchmarks...\n")
env = os.environ.copy()
env["TAVILY_API_KEY"] = "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"
env["EXA_API_KEY"] = "9f2de521-3378-495c-bb09-4fcebe93f206"

result = subprocess.run(
    [sys.executable, os.path.join(BASE_DIR, "tests/benchmark_deep_search.py")],
    env=env,
    capture_output=True,
    text=True,
    timeout=300,
    cwd=BASE_DIR,
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[-2000:])
print(f"\nExit code: {result.returncode}")
