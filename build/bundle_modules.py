#!/usr/bin/env python3
"""
OpenAkita optional modules pre-bundling script (for full package)

Downloads wheels and model files for optional modules to build/modules/ directory,
for the full package installer to bundle directly.

Usage:
  python build/bundle_modules.py                    # Download all modules
  python build/bundle_modules.py --module vector-memory  # Download only vector memory module
  python build/bundle_modules.py --mirror https://pypi.tuna.tsinghua.edu.cn/simple  # Use mirror
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = PROJECT_ROOT / "build" / "modules"

# Module definitions: module_id -> {packages, model_commands}
MODULE_DEFS = {
    "vector-memory": {
        "description": "Vector memory enhancement (semantic search)",
        "packages": [
            "sentence-transformers>=2.2.0",
            "chromadb>=0.4.0",
        ],
        "model_script": """
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("shibing624/text2vec-base-chinese")
print(f"Model downloaded to: {model._model_card_text if hasattr(model, '_model_card_text') else 'cache'}")
""",
    },
    "browser": {
        "description": "Browser automation (playwright + browser-use)",
        "packages": [
            "playwright>=1.40.0",
            "browser-use>=0.1.0",
            "langchain-openai>=0.1.0",
        ],
        "post_install": [sys.executable, "-m", "playwright", "install", "chromium"],
    },
    "whisper": {
        "description": "Speech recognition (OpenAI Whisper)",
        "packages": [
            "openai-whisper>=20231117",
            "static-ffmpeg>=2.7",
        ],
    },
    "orchestration": {
        "description": "Multi-Agent orchestration (ZeroMQ)",
        "packages": [
            "pyzmq>=25.0.0",
        ],
    },
}


def run_cmd(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Execute command"""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"  [WARN] Command returned non-zero exit code: {result.returncode}")
    return result


def download_wheels(module_id: str, module_def: dict, mirror: str | None = None):
    """Download module wheel files"""
    wheels_dir = MODULES_DIR / module_id / "wheels"
    wheels_dir.mkdir(parents=True, exist_ok=True)

    packages = module_def["packages"]
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--dest", str(wheels_dir),
        "--only-binary=:all:",
        *packages,
    ]
    if mirror:
        cmd.extend(["-i", mirror])

    print(f"\n  [Download] Downloading {module_id} wheel packages...")
    result = run_cmd(cmd)
    if result.returncode != 0:
        # Try again without --only-binary (some packages don't have prebuilt wheels)
        print("  [WARN] Binary-only download failed, trying with source packages...")
        cmd2 = [
            sys.executable, "-m", "pip", "download",
            "--dest", str(wheels_dir),
            *packages,
        ]
        if mirror:
            cmd2.extend(["-i", mirror])
        run_cmd(cmd2)

    # Statistics
    wheel_files = list(wheels_dir.glob("*.whl")) + list(wheels_dir.glob("*.tar.gz"))
    total_size = sum(f.stat().st_size for f in wheel_files)
    print(f"  [OK] {module_id}: {len(wheel_files)} packages, {total_size / 1024 / 1024:.1f} MB")


def download_model(module_id: str, module_def: dict):
    """Download model files needed by module"""
    model_script = module_def.get("model_script")
    if not model_script:
        return

    models_dir = MODULES_DIR / module_id / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  [Model] Downloading {module_id} model files...")
    # Set model cache directory
    env = {
        **os.environ,
        "TRANSFORMERS_CACHE": str(models_dir),
        "HF_HOME": str(models_dir),
        "HF_ENDPOINT": os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"),
    }
    result = subprocess.run(
        [sys.executable, "-c", model_script],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        total_size = sum(
            f.stat().st_size for f in models_dir.rglob("*") if f.is_file()
        )
        print(f"  [OK] Model download completed: {total_size / 1024 / 1024:.1f} MB")
    else:
        print(f"  [WARN] Model download failed: {result.stderr[:500]}")


def bundle_module(module_id: str, mirror: str | None = None):
    """Bundle single module"""
    module_def = MODULE_DEFS.get(module_id)
    if not module_def:
        print(f"  [ERROR] Unknown module: {module_id}")
        return False

    print(f"\n{'-'*50}")
    print(f"  [Bundle] Module: {module_id} - {module_def['description']}")
    print(f"{'-'*50}")

    download_wheels(module_id, module_def, mirror)
    download_model(module_id, module_def)
    return True


def main():
    parser = argparse.ArgumentParser(description="OpenAkita optional modules pre-bundling script")
    parser.add_argument(
        "--module",
        choices=list(MODULE_DEFS.keys()),
        help="Bundle only specified module (bundles all if not specified)",
    )
    parser.add_argument(
        "--mirror",
        default="https://mirrors.aliyun.com/pypi/simple/",
        help="PyPI mirror URL (default: Aliyun China mirror)",
    )
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="Use official PyPI (disable default domestic mirror)",
    )
    args = parser.parse_args()

    # --no-mirror 显式关闭国内镜像
    if args.no_mirror:
        args.mirror = None

    print(f"\n{'='*60}")
    print("  OpenAkita Optional Modules Pre-bundling")
    print(f"{'='*60}")
    print(f"  Output directory: {MODULES_DIR}")
    print(f"  Mirror: {args.mirror or '(official PyPI)'}")

    modules_to_bundle = [args.module] if args.module else list(MODULE_DEFS.keys())

    for module_id in modules_to_bundle:
        bundle_module(module_id, args.mirror)

    # Summary
    print(f"\n{'='*60}")
    print("  Bundle Summary")
    print(f"{'='*60}")
    total = 0
    for module_id in modules_to_bundle:
        module_dir = MODULES_DIR / module_id
        if module_dir.exists():
            size = sum(f.stat().st_size for f in module_dir.rglob("*") if f.is_file())
            total += size
            print(f"  {module_id}: {size / 1024 / 1024:.1f} MB")
    print(f"  --------------------")
    print(f"  Total: {total / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
